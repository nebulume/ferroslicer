"""
Background thread for running the full slicer pipeline.
Keeps the GUI responsive during potentially slow generation.
Emits granular progress signals so the progress bar tracks real stages.
"""

import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class SlicerWorker(QThread):
    """
    Runs the slicer pipeline in a background thread with per-stage progress.

    Signals:
        progress(int, str)  — (percent 0-100, message)
        finished(str)       — path to generated .gcode file
        error(str)          — error message
    """

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    # Rough stage percentages (cumulative)
    _STAGES = [
        (5,  "Loading config…"),
        (15, "Parsing STL…"),
        (30, "Analysing geometry…"),
        (45, "Generating wave patterns…"),
        (60, "Building spiral path…"),
        (80, "Writing GCode…"),
        (95, "Saving file…"),
    ]

    def __init__(
        self,
        stl_path: str,
        config_overrides: dict,
        output_file: str = "",
        custom_gcode: dict = None,
        parent=None,
    ):
        super().__init__(parent)
        self.stl_path        = stl_path
        self.config_overrides = config_overrides
        self.output_file      = output_file or ""
        self.custom_gcode     = custom_gcode or {}

    def run(self):
        try:
            from project.core.config import Config
            from project.core.stl_parser import STLParser
            from project.core.geometry_analyzer import GeometryAnalyzer
            from project.core.wave_generator import WaveGenerator, LayerAlternationController
            from project.core.spiral_generator import SpiralGenerator, _HAS_RUST as _SP_RUST
            from project.core.base_integrity import BaseIntegrityManager
            from project.core.gcode_generator import GCodeGenerator
            from project.core.slicer import MeshVaseSlicer
            from pathlib import Path as _Path

            # ── stage 0: config ───────────────────────────────────────────────
            self.progress.emit(5, "Loading config…")
            config = Config()
            slicer = MeshVaseSlicer(config)

            overrides = dict(self.config_overrides)
            if self.custom_gcode:
                overrides["custom_gcode"] = self.custom_gcode

            merged = slicer._merge_config(overrides)

            # ── stage 1: STL parse ────────────────────────────────────────────
            self.progress.emit(15, "Parsing STL…")
            model = STLParser.parse(self.stl_path)
            slicer._validate_model(model, interactive=False)

            # Apply model scale (set in STL viewer; default 1.0 = no change)
            model_scale = float(self.config_overrides.get("model_scale", 1.0))
            if abs(model_scale - 1.0) > 1e-6:
                for tri in model.triangles:
                    for v in (tri.vertex1, tri.vertex2, tri.vertex3):
                        v.x *= model_scale
                        v.y *= model_scale
                        v.z *= model_scale
                model._bounds = None   # invalidate cached bounding box

            # ── stage 2: geometry analysis ────────────────────────────────────
            self.progress.emit(30, f"Analysing geometry ({len(model.triangles):,} triangles)…")
            analyzer = GeometryAnalyzer(
                layer_height=merged["print_settings"]["layer_height"]
            )
            analyzer.analyze_model(model)

            # ── stage 3: wave setup ───────────────────────────────────────────
            self.progress.emit(45, f"Generating wave patterns ({len(analyzer.layers)} layers)…")
            ms = merged["mesh_settings"]
            ps = merged["print_settings"]

            wave_gen = WaveGenerator(
                amplitude=ms["wave_amplitude"],
                spacing=ms["wave_spacing"],
                smoothness=ms["wave_smoothness"],
                pattern_type=ms["wave_pattern"],
            )
            alternation = LayerAlternationController(
                alternation_period=ms["layer_alternation"],
                phase_offset=ms["phase_offset"],
            )
            base_mgr = BaseIntegrityManager(
                base_height=ms["base_height"],
                mode=ms["base_mode"],
                transition=ms["base_transition"],
            )

            wave_points_by_layer = []
            for idx, layer in enumerate(analyzer.layers):
                amp_f = base_mgr.get_amplitude_factor(layer.z)
                phase = alternation.get_phase_for_layer(idx)
                wp = wave_gen.generate_wave_points(layer.points, amplitude_factor=amp_f, phase_offset=phase)
                wave_points_by_layer.append(wp)

            # ── stage 4: spiral / GCode ───────────────────────────────────────
            gcode_gen = GCodeGenerator(
                nozzle_diameter=merged["printer"]["nozzle_diameter"],
                layer_height=ps["layer_height"],
                nozzle_temp=merged["printer"]["nozzle_temp"],
                bed_temp=merged["printer"]["bed_temp"],
                print_speed=ps["print_speed"],
                travel_speed=ps["travel_speed"],
                fan_speed=ps["fan_speed"],
                filament_diameter=merged["printer"]["filament_diameter"],
                purge_gap=ps.get("purge_gap", 20.0),
                purge_length=ps.get("purge_length", 50.0),
                purge_side=ps.get("purge_side", "left"),
                max_volumetric_speed=ps.get("max_volumetric_speed", 12.0),
                skirt_enabled=ps.get("skirt_enabled", True),
                skirt_distance=ps.get("skirt_distance", 0.0),
                skirt_height=ps.get("skirt_height", 1),
                start_gcode_override=merged.get("custom_gcode", {}).get("start_gcode", ""),
                end_gcode_override=merged.get("custom_gcode", {}).get("end_gcode", ""),
            )

            base_layer_points = analyzer.layers[0].points if analyzer.layers else None

            if ps.get("vase_mode"):
                self.progress.emit(55, "Building spiral path (Rust)…")
                ppd = ps.get("spiral_points_per_degree", 1.2)
                target_samples = ps.get("target_samples_per_wave", 16)
                smoothing_window = ps.get("smoothing_window_size", 3)
                smoothing_threshold = ps.get("smoothing_move_threshold", 0.5)
                auto_resample = ps.get("auto_resample_spiral", True)

                wave_amp = ms.get("wave_amplitude", 2.0)
                wave_count = ms.get("wave_count")
                wave_spacing = ms.get("wave_spacing")
                wave_pattern = ms.get("wave_pattern", "sine")
                layer_alt = ms.get("layer_alternation", 2)
                phase_offset = ms.get("phase_offset", 50)
                wave_asymmetry = ms.get("wave_asymmetry", False)
                wave_asym_int = ms.get("wave_asymmetry_intensity", 100)
                seam_shift = ms.get("seam_shift", 0.0)

                if wave_count:
                    waves_per_rev = float(wave_count)
                elif wave_spacing and wave_spacing > 0 and analyzer.layers:
                    avg_perim = analyzer.layers[0].calculate_perimeter_length()
                    waves_per_rev = avg_perim / wave_spacing if avg_perim > 0 else 0.0
                else:
                    waves_per_rev = 0.0

                spiral_gen = SpiralGenerator(
                    analyzer.layers,
                    layer_height=ps["layer_height"],
                    points_per_degree=ppd,
                    smoothing_window_size=smoothing_window,
                    smoothing_move_threshold=smoothing_threshold,
                    target_samples_per_wave=target_samples,
                    auto_resample_spiral=auto_resample,
                )

                if _SP_RUST:
                    modified_spiral = spiral_gen._generate_spiral_rust(
                        wave_amplitude=wave_amp,
                        waves_per_rev=waves_per_rev,
                        wave_pattern=wave_pattern,
                        layer_alternation=layer_alt,
                        phase_offset=phase_offset,
                        seam_shift=seam_shift,
                        base_integrity_manager=base_mgr,
                        wave_asymmetry=wave_asymmetry,
                        wave_asymmetry_intensity=wave_asym_int,
                    )
                else:
                    spiral_points = spiral_gen.generate_spiral_path()
                    modified_spiral = spiral_gen.apply_wave_to_spiral(
                        spiral_points,
                        wave_amplitude=wave_amp,
                        wave_count=wave_count,
                        wave_spacing=wave_spacing,
                        wave_pattern=wave_pattern,
                        layer_alternation=layer_alt,
                        phase_offset=phase_offset,
                        wave_asymmetry=wave_asymmetry,
                        wave_asymmetry_intensity=wave_asym_int,
                        base_integrity_manager=base_mgr,
                        seam_shift=seam_shift,
                    )

                n_pts = len(modified_spiral)
                self.progress.emit(75, f"Writing GCode ({n_pts:,} spiral points)…")
                gcode_content = gcode_gen.generate_gcode(
                    [], model.name, model.bounds, base_layer_points,
                    spiral_points=modified_spiral
                )
            else:
                self.progress.emit(70, f"Writing GCode ({len(analyzer.layers)} layers)…")
                gcode_content = gcode_gen.generate_gcode(
                    wave_points_by_layer, model.name, model.bounds, base_layer_points
                )

            # ── stage 5: save ─────────────────────────────────────────────────
            self.progress.emit(90, "Saving GCode file…")
            output_path = self.output_file or slicer._generate_output_filename(self.stl_path)
            slicer._save_gcode(output_path, gcode_content)
            slicer._save_log(output_path.replace(".gcode", ".log"), model, analyzer, merged)

            self.progress.emit(100, "Done!")
            self.finished.emit(output_path)

        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"{e}\n\n{tb}")

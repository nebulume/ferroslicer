"""
Background thread for generating toolpath preview data.
Runs geometry analysis and wave generation (but not GCode output),
then returns point arrays the preview widgets can render.
"""

import sys
import traceback
from pathlib import Path
from typing import List

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

class PreviewWorker(QThread):
    """
    Generates preview geometry in background.

    Signals:
        layer_data_ready(list)  — list of (N,2) numpy arrays, one per layer
        path_data_ready(array)  — (N,3) numpy array of spiral/wave path points
        error(str)
    """

    layer_data_ready = pyqtSignal(list)   # List[np.ndarray(M,2)]
    path_data_ready  = pyqtSignal(object) # np.ndarray(N,3)
    error = pyqtSignal(str)

    def __init__(
        self,
        stl_path: str,
        config_overrides: dict,
        mode: str = "2d",  # "2d" | "3d_snap" | "3d_full"
        snap_resolution: float = 0.15,  # fraction of full ppd (Rust anti-alias guard bumps it up)
        target_samples_per_wave: int = 8,  # for preview, 8 is enough to see waves
        parent=None,
    ):
        super().__init__(parent)
        self.stl_path        = stl_path
        self.config_overrides = config_overrides
        self.mode             = mode
        self.snap_resolution  = snap_resolution
        self.target_samples_per_wave = target_samples_per_wave

    def run(self):
        try:
            from project.core.config import Config
            from project.core.stl_parser import STLParser
            from project.core.geometry_analyzer import GeometryAnalyzer
            from project.core.wave_generator import WaveGenerator, LayerAlternationController
            from project.core.base_integrity import BaseIntegrityManager
            from project.core.slicer import MeshVaseSlicer

            cfg = Config()
            overrides = dict(self.config_overrides)

            # Merge config
            merged = {
                "printer":       cfg.get_nested("printer"),
                "print_settings": cfg.get_nested("print_settings"),
                "mesh_settings":  cfg.get_nested("mesh_settings"),
            }
            for section, vals in overrides.items():
                if section in merged and isinstance(merged[section], dict):
                    merged[section].update(vals)
                else:
                    merged[section] = vals

            ps = merged["print_settings"]
            ms = merged["mesh_settings"]
            is_vase = ps.get("vase_mode", False)

            # Parse + analyze (uses Rust/numpy automatically)
            model = STLParser.parse(self.stl_path)

            # Reduce layer resolution for snap/2D preview
            lh = ps.get("layer_height", 0.5)
            if self.mode in ("2d", "3d_snap"):
                lh = lh * max(1, int(1 / self.snap_resolution))

            analyzer = GeometryAnalyzer(layer_height=lh)
            analyzer.analyze_model(model)

            if self.mode == "2d":
                # 2D iso: emit per-layer XY arrays (wave-modified)
                wave_gen = WaveGenerator(
                    amplitude=ms.get("wave_amplitude", 2.0),
                    spacing=ms.get("wave_spacing", 4.0),
                    smoothness=ms.get("wave_smoothness", 10),
                    pattern_type=ms.get("wave_pattern", "sine"),
                )
                alternation = LayerAlternationController(
                    alternation_period=ms.get("layer_alternation", 2),
                    phase_offset=ms.get("phase_offset", 50),
                )
                base_mgr = BaseIntegrityManager(
                    base_height=ms.get("base_height", 28.0),
                    mode=ms.get("base_mode", "fewer_gaps"),
                    transition=ms.get("base_transition", "exponential"),
                )

                layer_arrays: List[np.ndarray] = []
                for idx, layer in enumerate(analyzer.layers):
                    amp_f = base_mgr.get_amplitude_factor(layer.z)
                    phase = alternation.get_phase_for_layer(idx)
                    wp = wave_gen.generate_wave_points(
                        layer.points, amplitude_factor=amp_f, phase_offset=phase
                    )
                    if wp:
                        pts = np.array([[w.modified.x, w.modified.y] for w in wp], dtype=np.float32)
                        layer_arrays.append(pts)

                self.layer_data_ready.emit(layer_arrays)

            else:
                # 3D snap / full: generate spiral or layer-stack path
                if is_vase:
                    from project.core.spiral_generator import SpiralGenerator, _HAS_RUST as _SP_RUST
                    from project.core.base_integrity import BaseIntegrityManager as _BIM
                    ppd = ps.get("spiral_points_per_degree", 1.2)
                    if self.mode == "3d_snap":
                        ppd = ppd * self.snap_resolution

                    base_mgr = _BIM(
                        base_height=ms.get("base_height", 28.0),
                        mode=ms.get("base_mode", "fewer_gaps"),
                        transition=ms.get("base_transition", "exponential"),
                    )

                    # Compute waves_per_rev
                    wave_count = ms.get("wave_count")
                    wave_spacing = ms.get("wave_spacing", 4.0)
                    if wave_count:
                        waves_per_rev = float(wave_count)
                    elif wave_spacing and wave_spacing > 0 and analyzer.layers:
                        avg_perim = analyzer.layers[0].calculate_perimeter_length()
                        waves_per_rev = avg_perim / wave_spacing if avg_perim > 0 else 0.0
                    else:
                        waves_per_rev = 0.0

                    sp_gen = SpiralGenerator(
                        analyzer.layers,
                        layer_height=ps.get("layer_height", 0.5),
                        points_per_degree=ppd,
                        smoothing_window_size=ps.get("smoothing_window_size", 3),
                        smoothing_move_threshold=ps.get("smoothing_move_threshold", 0.5),
                        target_samples_per_wave=self.target_samples_per_wave,
                    )

                    if _SP_RUST:
                        modified = sp_gen._generate_spiral_rust(
                            wave_amplitude=ms.get("wave_amplitude", 2.0),
                            waves_per_rev=waves_per_rev,
                            wave_pattern=ms.get("wave_pattern", "sine"),
                            layer_alternation=ms.get("layer_alternation", 2),
                            phase_offset=ms.get("phase_offset", 50),
                            seam_shift=ms.get("seam_shift", 0.0),
                            base_integrity_manager=base_mgr,
                            wave_asymmetry=ms.get("wave_asymmetry", False),
                            wave_asymmetry_intensity=ms.get("wave_asymmetry_intensity", 100),
                        )
                        pts = np.array(
                            list(zip(modified._xs, modified._ys, modified._zs)),
                            dtype=np.float32,
                        )
                    else:
                        spiral = sp_gen.generate_spiral_path()
                        modified = sp_gen.apply_wave_to_spiral(
                            spiral,
                            wave_amplitude=ms.get("wave_amplitude", 2.0),
                            wave_count=wave_count,
                            wave_spacing=wave_spacing,
                            wave_pattern=ms.get("wave_pattern", "sine"),
                            layer_alternation=ms.get("layer_alternation", 2),
                            phase_offset=ms.get("phase_offset", 50),
                            base_integrity_manager=base_mgr,
                            seam_shift=ms.get("seam_shift", 0.0),
                        )
                        pts = np.array(
                            [[p.position.x, p.position.y, p.position.z] for p in modified],
                            dtype=np.float32,
                        )
                else:
                    # Layer mesh: flatten all wave points into a path
                    wave_gen = WaveGenerator(
                        amplitude=ms.get("wave_amplitude", 2.0),
                        spacing=ms.get("wave_spacing", 4.0),
                        smoothness=ms.get("wave_smoothness", 10),
                        pattern_type=ms.get("wave_pattern", "sine"),
                    )
                    alternation = LayerAlternationController(
                        alternation_period=ms.get("layer_alternation", 2),
                        phase_offset=ms.get("phase_offset", 50),
                    )
                    base_mgr = BaseIntegrityManager(
                        base_height=ms.get("base_height", 28.0),
                        mode=ms.get("base_mode", "fewer_gaps"),
                        transition=ms.get("base_transition", "exponential"),
                    )
                    all_pts = []
                    for idx, layer in enumerate(analyzer.layers):
                        amp_f = base_mgr.get_amplitude_factor(layer.z)
                        phase = alternation.get_phase_for_layer(idx)
                        wp = wave_gen.generate_wave_points(
                            layer.points, amplitude_factor=amp_f, phase_offset=phase
                        )
                        for w in wp:
                            all_pts.append([w.modified.x, w.modified.y, layer.z])
                    pts = np.array(all_pts, dtype=np.float32)

                self.path_data_ready.emit(pts)

        except Exception as e:
            self.error.emit(f"{e}\n{traceback.format_exc()}")

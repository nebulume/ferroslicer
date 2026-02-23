'''
Main MeshVase Slicer orchestrator - coordinates all components.
'''

import os
from pathlib import Path
from datetime import datetime
from typing import Optional
from .logger import setup_logger
from .config import Config
from .stl_parser import STLParser
from .geometry_analyzer import GeometryAnalyzer
from .wave_generator import WaveGenerator, LayerAlternationController
from .spiral_generator import SpiralGenerator
from .base_integrity import BaseIntegrityManager
from .adaptive_behavior import CurvatureAdaptation, DiameterScaling, AdaptiveWaveBehavior
from .gcode_generator import GCodeGenerator
from .preview import PreviewSystem
from .exceptions import ProjectError

logger = setup_logger("slicer")


class MeshVaseSlicer:
    """Main orchestrator for MeshVase slicing process."""

    def __init__(self, config: Config):
        """
        Initialize slicer with configuration.

        Args:
            config: Config instance
        """
        self.config = config
        self.output_dir = config.get("output_dir", "output")

        # Create output directory
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        logger.info(f"MeshVase Slicer v{config.get('version', '1.0.0')} initialized")

    def slice_stl(
        self,
        stl_file: str,
        output_file: Optional[str] = None,
        override_config: Optional[dict] = None
    ) -> str:
        """
        Complete slicing pipeline: STL -> geometry analysis -> wave generation -> GCode.

        Args:
            stl_file: Path to STL file
            output_file: Optional output filename
            override_config: Optional config overrides

        Returns:
            Path to generated GCode file
        """
        logger.info(f"Starting slice: {stl_file}")

        # Merge config overrides
        merged_config = self._merge_config(override_config)

        # Parse STL
        logger.info("Parsing STL...")
        model = STLParser.parse(stl_file)

        # Validate model (interactive=False for direct slicing)
        self._validate_model(model, interactive=False)

        # Analyze geometry
        logger.info("Analyzing geometry...")
        analyzer = GeometryAnalyzer(
            layer_height=merged_config["print_settings"]["layer_height"]
        )
        analyzer.analyze_model(model)

        # Setup wave generator
        logger.info("Setting up wave generator...")
        wave_gen = WaveGenerator(
            amplitude=merged_config["mesh_settings"]["wave_amplitude"],
            spacing=merged_config["mesh_settings"]["wave_spacing"],
            smoothness=merged_config["mesh_settings"]["wave_smoothness"],
            pattern_type=merged_config["mesh_settings"]["wave_pattern"]
        )

        # Setup layer alternation
        alternation = LayerAlternationController(
            alternation_period=merged_config["mesh_settings"]["layer_alternation"],
            phase_offset=merged_config["mesh_settings"]["phase_offset"]
        )

        # Setup base integrity
        base_mgr = BaseIntegrityManager(
            base_height=merged_config["mesh_settings"]["base_height"],
            mode=merged_config["mesh_settings"]["base_mode"],
            transition=merged_config["mesh_settings"]["base_transition"]
        )

        # Setup adaptive behavior
        curvature_adapt = CurvatureAdaptation(
            angle_threshold=merged_config["mesh_settings"]["curvature_threshold_angle"],
            distance_threshold=merged_config["mesh_settings"]["curvature_threshold_distance"],
            amplitude_reduction=merged_config["mesh_settings"]["curvature_amplitude_reduction"],
            frequency_reduction=merged_config["mesh_settings"]["curvature_frequency_reduction"],
            transition_smoothness=merged_config["mesh_settings"]["transition_smoothness"]
        )

        diameter_scale = DiameterScaling(
            scaling_type=merged_config["mesh_settings"]["diameter_scaling"]
        )

        adaptive_behavior = AdaptiveWaveBehavior(curvature_adapt, diameter_scale)

        # Generate wave points for all layers
        logger.info("Generating wave patterns...")
        wave_points_by_layer = []

        for layer_idx, layer in enumerate(analyzer.layers):
            z = layer.z
            amplitude_factor = base_mgr.get_amplitude_factor(z)

            # Get phase offset for this layer
            phase = alternation.get_phase_for_layer(layer_idx)

            # Generate wave points
            wave_points = wave_gen.generate_wave_points(
                layer.points,
                amplitude_factor=amplitude_factor,
                phase_offset=phase
            )

            wave_points_by_layer.append(wave_points)

        # Generate GCode
        logger.info("Generating GCode...")
        gcode_gen = GCodeGenerator(
            nozzle_diameter=merged_config["printer"]["nozzle_diameter"],
            layer_height=merged_config["print_settings"]["layer_height"],
            nozzle_temp=merged_config["printer"]["nozzle_temp"],
            bed_temp=merged_config["printer"]["bed_temp"],
            print_speed=merged_config["print_settings"]["print_speed"],
            travel_speed=merged_config["print_settings"]["travel_speed"],
            fan_speed=merged_config["print_settings"]["fan_speed"],
            filament_diameter=merged_config["printer"]["filament_diameter"],
            purge_gap=merged_config.get("print_settings", {}).get("purge_gap", 20.0),
            purge_length=merged_config.get("print_settings", {}).get("purge_length", 50.0),
            purge_side=merged_config.get("print_settings", {}).get("purge_side", "left"),
            max_volumetric_speed=merged_config.get("print_settings", {}).get("max_volumetric_speed", 12.0),
            skirt_enabled=merged_config.get("print_settings", {}).get("skirt_enabled", True),
            skirt_distance=merged_config.get("print_settings", {}).get("skirt_distance", 0.0),
            skirt_height=merged_config.get("print_settings", {}).get("skirt_height", 1),
            start_gcode_override=merged_config.get("custom_gcode", {}).get("start_gcode", ""),
            end_gcode_override=merged_config.get("custom_gcode", {}).get("end_gcode", ""),
        )

        # If vase mode (spiral) requested, build continuous spiral path
        base_layer_points = analyzer.layers[0].points if analyzer.layers else None
        if merged_config.get("print_settings", {}).get("vase_mode"):
            logger.info("Building spiral path for vase mode...")
            ppd = merged_config.get("print_settings", {}).get("spiral_points_per_degree", 1.2)
            target_samples = merged_config.get("print_settings", {}).get("target_samples_per_wave", 16)
            smoothing_window = merged_config.get("print_settings", {}).get("smoothing_window_size", 3)
            smoothing_threshold = merged_config.get("print_settings", {}).get("smoothing_move_threshold", 0.5)
            auto_resample = merged_config.get("print_settings", {}).get("auto_resample_spiral", True)

            wave_amp = merged_config["mesh_settings"].get("wave_amplitude", 2.0)
            wave_count = merged_config["mesh_settings"].get("wave_count")
            wave_spacing = merged_config["mesh_settings"].get("wave_spacing")
            wave_pattern = merged_config["mesh_settings"].get("wave_pattern", "sine")
            layer_alt = merged_config["mesh_settings"].get("layer_alternation", 2)
            phase_offset = merged_config["mesh_settings"].get("phase_offset", 50)
            wave_asymmetry = merged_config["mesh_settings"].get("wave_asymmetry", False)
            wave_asymmetry_intensity = merged_config["mesh_settings"].get("wave_asymmetry_intensity", 100)
            seam_shift = merged_config["mesh_settings"].get("seam_shift", 0.0)

            # Compute waves_per_rev (same logic as apply_wave_to_spiral)
            if wave_count:
                waves_per_rev = float(wave_count)
            elif wave_spacing and wave_spacing > 0 and analyzer.layers:
                avg_perimeter = analyzer.layers[0].calculate_perimeter_length()
                waves_per_rev = avg_perimeter / wave_spacing if avg_perimeter > 0 else 0.0
            else:
                waves_per_rev = 0.0

            spiral_gen = SpiralGenerator(
                analyzer.layers,
                layer_height=merged_config["print_settings"]["layer_height"],
                points_per_degree=ppd,
                smoothing_window_size=smoothing_window,
                smoothing_move_threshold=smoothing_threshold,
                target_samples_per_wave=target_samples,
                auto_resample_spiral=auto_resample,
            )

            # Use Rust fast-path when available (generates + applies waves in one call)
            from .spiral_generator import _HAS_RUST as _SPIRAL_HAS_RUST
            if _SPIRAL_HAS_RUST:
                logger.info("Using Rust spiral generator (fastest)")
                modified_spiral = spiral_gen._generate_spiral_rust(
                    wave_amplitude=wave_amp,
                    waves_per_rev=waves_per_rev,
                    wave_pattern=wave_pattern,
                    layer_alternation=layer_alt,
                    phase_offset=phase_offset,
                    seam_shift=seam_shift,
                    base_integrity_manager=base_mgr,
                    wave_asymmetry=wave_asymmetry,
                    wave_asymmetry_intensity=wave_asymmetry_intensity,
                )
            else:
                logger.info("Using Python spiral generator (Rust unavailable)")
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
                    wave_asymmetry_intensity=wave_asymmetry_intensity,
                    base_integrity_manager=base_mgr,
                    seam_shift=seam_shift,
                )

            gcode_content = gcode_gen.generate_gcode(
                [],
                model.name,
                model.bounds,
                base_layer_points,
                spiral_points=modified_spiral
            )
        else:
            # Pass base layer (first layer) points so GCodeGenerator can position purge line
            gcode_content = gcode_gen.generate_gcode(
                wave_points_by_layer,
                model.name,
                model.bounds,
                base_layer_points
            )

        # Save GCode
        output_path = output_file or self._generate_output_filename(stl_file)
        self._save_gcode(output_path, gcode_content)

        # Save log
        log_path = output_path.replace(".gcode", ".log")
        self._save_log(log_path, model, analyzer, merged_config)

        logger.info(f"✓ Slicing complete: {output_path}")
        return output_path

    def _validate_model(self, model, interactive: bool = False) -> None:
        """Validate STL model with user prompts for warnings."""
        # Check manifold
        is_manifold, manifold_msg = model.check_manifold()
        if not is_manifold:
            print(f"\n⚠️  Warning: {manifold_msg}")
            if interactive:
                response = input("Continue anyway? [y/n]: ").strip().lower()
                if response != "y":
                    raise ProjectError("Slicing cancelled by user")
            else:
                logger.warning(f"Non-manifold: {manifold_msg}")

        # Check vase suitability
        is_suitable, vase_warnings = model.check_vase_suitability()
        if not is_suitable:
            for warning in vase_warnings:
                print(f"⚠️  {warning}")
            if interactive:
                response = input("Continue anyway? [y/n]: ").strip().lower()
                if response != "y":
                    raise ProjectError("Slicing cancelled by user")
            else:
                logger.warning(f"Vase suitability warnings: {vase_warnings}")

    def _merge_config(self, overrides: Optional[dict]) -> dict:
        """Merge config overrides with current config."""
        merged = {
            "printer": self.config.get_nested("printer"),
            "print_settings": self.config.get_nested("print_settings"),
            "mesh_settings": self.config.get_nested("mesh_settings")
        }

        if overrides:
            for section, values in overrides.items():
                if section in merged and isinstance(merged[section], dict):
                    merged[section].update(values)
                else:
                    merged[section] = values

        return merged

    def _generate_output_filename(self, stl_file: str) -> str:
        """Generate standardized output filename."""
        model_name = Path(stl_file).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wave_amp = self.config.get("mesh_settings.wave_amplitude", 2.0)
        wave_spacing = self.config.get("mesh_settings.wave_spacing", 4.0)
        layer_alt = self.config.get("mesh_settings.layer_alternation", 2)

        filename = (
            f"{model_name}_mesh_{wave_amp}a_{wave_spacing}s_"
            f"{layer_alt}alt_{timestamp}.gcode"
        )

        return os.path.join(self.output_dir, filename)

    def _save_gcode(self, output_path: str, gcode_content: str) -> None:
        """Save GCode to file."""
        try:
            with open(output_path, "w") as f:
                f.write(gcode_content)
            logger.info(f"Saved GCode: {output_path}")
        except IOError as e:
            raise ProjectError(f"Failed to save GCode: {e}")

    def _save_log(self, log_path: str, model, analyzer, config: dict) -> None:
        """Save detailed log file."""
        try:
            with open(log_path, "w") as f:
                f.write("MeshVase Slicer - Generation Log\n")
                f.write("=" * 60 + "\n\n")

                f.write("Model Information:\n")
                f.write(f"  Name: {model.name}\n")
                f.write(f"  Dimensions: {model.dimensions.x:.1f} x {model.dimensions.y:.1f} x {model.dimensions.z:.1f} mm\n")
                f.write(f"  Triangles: {len(model.triangles)}\n")
                f.write(f"  Bounds: {model.bounds[0]} to {model.bounds[1]}\n\n")

                f.write("Layer Statistics:\n")
                stats = analyzer.get_layer_statistics()
                for key, value in stats.items():
                    f.write(f"  {key}: {value}\n")
                f.write("\n")

                f.write("Configuration:\n")
                for section, values in config.items():
                    f.write(f"  [{section}]\n")
                    for key, value in values.items():
                        f.write(f"    {key}: {value}\n")

            logger.info(f"Saved log: {log_path}")
        except IOError as e:
            logger.error(f"Failed to save log: {e}")

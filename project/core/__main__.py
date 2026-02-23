'''
MeshVase Slicer - Main CLI entry point.
Interactive vase mode slicer for 3D printing with mesh patterns.
'''

import os
import sys
import argparse
from pathlib import Path
from .config import Config
from .logger import setup_logger
from .slicer import MeshVaseSlicer
from .preview import PreviewSystem
from .exceptions import ProjectError

logger = setup_logger("cli")


class MeshVaseCliApp:
    """Interactive CLI application for MeshVase Slicer."""

    def __init__(self):
        self.config = Config()
        self.slicer = MeshVaseSlicer(self.config)
        self.preview = PreviewSystem(
            self.config.get("orcaslicer_path", "/Applications/OrcaSlicer.app")
        )

    def run(self, args=None):
        """Run the CLI application."""
        parser = self._create_argument_parser()
        parsed_args = parser.parse_args(args)

        try:
            if parsed_args.input:
                # Direct file mode
                self._slice_file(parsed_args.input, parsed_args)
            else:
                # Interactive mode
                self._interactive_mode()
        except ProjectError as e:
            logger.error(f"Error: {e}")
            print(f"\n❌ Error: {e}")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n\nCancelled by user")
            sys.exit(0)

    def _create_argument_parser(self):
        """Create argument parser."""
        parser = argparse.ArgumentParser(
            description="MeshVase Slicer - Convert STL to Klipper GCode with mesh patterns",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  python -m project.core                    # Interactive mode
  python -m project.core --input lamp1.stl # Slice with defaults
  python -m project.core --input lamp1.stl --wave-amplitude 1.5 --nozzle-temp 250
            """
        )

        parser.add_argument(
            "--input", "-i",
            type=str,
            help="Input STL file"
        )

        parser.add_argument(
            "--output", "-o",
            type=str,
            help="Output GCode filename"
        )

        # Printer settings
        parser.add_argument(
            "--nozzle",
            type=float,
            help="Nozzle diameter (mm)"
        )

        parser.add_argument(
            "--nozzle-temp",
            type=float,
            help="Nozzle temperature (°C)"
        )

        parser.add_argument(
            "--bed-temp",
            type=float,
            help="Bed temperature (°C)"
        )

        # Print settings
        parser.add_argument(
            "--layer-height",
            type=float,
            help="Layer height (mm)"
        )

        parser.add_argument(
            "--print-speed",
            type=float,
            help="Print speed (mm/s)"
        )

        parser.add_argument(
            "--travel-speed",
            type=float,
            help="Travel speed (mm/s)"
        )

        parser.add_argument(
            "--purge-gap",
            type=float,
            help="Purge gap from model base (mm)"
        )

        parser.add_argument(
            "--fan-speed",
            type=float,
            help="Fan speed (0-100 percent)"
        )

        parser.add_argument(
            "--max-volumetric-speed",
            type=float,
            help="Maximum volumetric extrusion speed (mm^3/s)"
        )

        parser.add_argument(
            "--purge-length",
            type=float,
            help="Purge line length (mm)"
        )

        parser.add_argument(
            "--purge-side",
            type=str,
            choices=["left", "right", "front", "back", "auto"],
            help="Which side of the bed to place the purge line"
        )

        parser.add_argument(
            "--skirt",
            action="store_true",
            help="Enable skirt (default: enabled, one loop around base for adhesion)"
        )

        parser.add_argument(
            "--no-skirt",
            action="store_true",
            help="Disable skirt"
        )

        parser.add_argument(
            "--skirt-distance",
            type=float,
            help="Distance from print to skirt (mm, default 0 = touching)"
        )

        parser.add_argument(
            "--skirt-height",
            type=int,
            help="Number of layers for skirt (default 1)"
        )

        parser.add_argument(
            "--first-layer-squish",
            type=float,
            help="First layer squish percentage (0-80%%, default 15). Reduces first layer Z by this amount for better adhesion."
        )

        # Mesh settings
        parser.add_argument(
            "--wave-amplitude",
            type=float,
            help="Wave amplitude (mm)"
        )

        parser.add_argument(
            "--wave-spacing",
            type=float,
            help="Wave spacing (mm)"
        )

        parser.add_argument(
            "--wave-count",
            type=int,
            help="Number of waves per revolution (overrides wave-spacing if set)"
        )

        parser.add_argument(
            "--wave-pattern",
            type=str,
            choices=["sine", "triangular", "sawtooth"],
            help="Wave pattern type"
        )

        parser.add_argument(
            "--wave-smoothness",
            type=int,
            help="Wave smoothness, 1-10"
        )

        parser.add_argument(
            "--wave-asymmetry",
            action="store_true",
            help="Enable asymmetric waves (shallow rise, steep fall) for visual effect"
        )

        parser.add_argument(
            "--wave-asymmetry-intensity",
            type=float,
            help="Asymmetry intensity, 0-100 (0=symmetric sine, 100=full asymmetry)"
        )

        parser.add_argument(
            "--vase-mode",
            action="store_true",
            help="Enable true spiral vase mode (continuous spiral extrusion)"
        )

        parser.add_argument(
            "--spiral-points-per-degree",
            type=float,
            help="Spiral sampling resolution (points per degree). Default 1.2 yields ~432 points/rev"
        )

        parser.add_argument(
            "--target-samples-per-wave",
            type=int,
            help="Target samples per wave (helps avoid aliasing)."
        )

        parser.add_argument(
            "--smoothing-window-size",
            type=int,
            help="Smoothing window size (odd integer, e.g. 3)."
        )

        parser.add_argument(
            "--smoothing-threshold",
            type=float,
            help="Smoothing move threshold in mm."
        )

        parser.add_argument(
            "--no-auto-resample-spiral",
            action="store_true",
            help="Disable automatic spiral resampling for high-frequency waves"
        )

        parser.add_argument(
            "--layer-alternation",
            type=int,
            help="Layers before alternation"
        )

        parser.add_argument(
            "--phase-offset",
            type=float,
            help="Phase offset percentage, 0-100"
        )

        parser.add_argument(
            "--seam-shift",
            type=float,
            help="Extend alternation cycle by this many waves to shift seam (e.g. 0.5)"
        )

        parser.add_argument(
            "--seam-position",
            type=str,
            choices=["auto", "front", "back", "left", "right",
                     "front_right", "front_left", "back_right", "back_left", "sharpest"],
            help="Place seam at a specific corner/direction of the model"
        )

        parser.add_argument(
            "--seam-transition-waves",
            type=float,
            help="Smooth the seam phase transition over N waves (0 = sharp, e.g. 2.0)"
        )

        parser.add_argument(
            "--scale",
            type=float,
            help="Uniform scale factor applied to the STL before slicing (e.g. 0.5 = half-size, 2.0 = double)"
        )

        parser.add_argument(
            "--base-height",
            type=float,
            help="Base integrity height (mm)"
        )

        parser.add_argument(
            "--base-mode",
            type=str,
            choices=["tighter_waves", "fewer_gaps", "solid_then_mesh"],
            help="Base integrity mode"
        )

        parser.add_argument(
            "--base-transition",
            type=str,
            choices=["linear", "exponential", "step"],
            help="Base transition profile"
        )

        return parser

    def _interactive_mode(self):
        """Run interactive mode with file selection and parameter prompts."""
        print("\n" + "=" * 60)
        print("  MeshVase Slicer - Interactive Mode")
        print("=" * 60)

        # List available STL files
        stl_files = list(Path(".").glob("*.stl"))

        if not stl_files:
            raise ProjectError("No STL files found in current directory")

        print("\nAvailable STL files:")
        for i, file in enumerate(stl_files, 1):
            print(f"  {i}. {file.name}")

        # Get user selection
        while True:
            try:
                choice = int(input("\nSelect file (number): ")) - 1
                if 0 <= choice < len(stl_files):
                    stl_file = str(stl_files[choice])
                    break
            except ValueError:
                pass
            print("Invalid selection")

        print(f"\n✓ Selected: {stl_file}")

        # Interactive parameter entry
        config_overrides = self._get_parameter_prompts()

        # Slice
        self._slice_file(stl_file, argparse.Namespace(**config_overrides))

    def _get_parameter_prompts(self) -> dict:
        """Prompt user for slicing parameters."""
        print("\n" + "-" * 60)
        print("Slicing Parameters (press Enter for defaults)")
        print("-" * 60)

        overrides = {}

        # Printer settings
        print("\n[Printer Settings]")
        nozzle = self._prompt_float("Nozzle diameter (mm)", self.config.get("printer.nozzle_diameter", 1.0))
        nozzle_temp = self._prompt_float("Nozzle temperature (°C)", self.config.get("printer.nozzle_temp", 260))
        bed_temp = self._prompt_float("Bed temperature (°C)", self.config.get("printer.bed_temp", 65))

        # Print settings
        print("\n[Print Settings]")
        layer_height = self._prompt_float("Layer height (mm)", self.config.get("print_settings.layer_height", 0.5))
        print_speed = self._prompt_float("Print speed (mm/s)", self.config.get("print_settings.print_speed", 35))
        travel_speed = self._prompt_float("Travel speed (mm/s)", self.config.get("print_settings.travel_speed", 40))
        fan_speed = self._prompt_float("Fan speed (%)", self.config.get("print_settings.fan_speed", 100), 0, 100)
        max_volumetric_speed = self._prompt_float("Max volumetric speed (mm³/s)", self.config.get("print_settings.max_volumetric_speed", 12.0), 0.1)

        # Skirt settings
        print("\n[Skirt Settings]")
        skirt_enabled = self._prompt_choice("Enable skirt", ["yes", "no"], "yes" if self.config.get("print_settings.skirt_enabled", True) else "no") == "yes"
        
        if skirt_enabled:
            skirt_distance = self._prompt_float("Skirt distance from print (mm)", self.config.get("print_settings.skirt_distance", 0.0))
            skirt_height = self._prompt_int("Skirt height (layers)", self.config.get("print_settings.skirt_height", 1), 1, 10)
        else:
            skirt_distance = None
            skirt_height = None

        # Vase mode selection
        print("\n[Printing Mode]")
        default_vase_mode = "spiral_vase" if self.config.get("print_settings.vase_mode", False) else "layer_mesh"
        vase_mode = self._prompt_choice(
            "Printing mode",
            ["spiral_vase", "layer_mesh"],
            default_vase_mode
        )
        vase_mode = vase_mode == "spiral_vase"

        # Mesh settings
        print("\n[Wave Pattern Settings]")
        wave_amplitude = self._prompt_float("Wave amplitude (mm)", self.config.get("mesh_settings.wave_amplitude", 2.0))
        
        # Ask about wave frequency mode
        default_wave_count = self.config.get("mesh_settings.wave_count")
        default_wave_mode = "per_revolution" if default_wave_count is not None else "per_distance"
        
        wave_mode = self._prompt_choice(
            "Wave frequency specification",
            ["per_revolution", "per_distance"],
            default_wave_mode
        )

        if wave_mode == "per_revolution":
            default_count = default_wave_count if default_wave_count is not None else 120
            wave_count = self._prompt_int("Number of waves per revolution", default_count, 1, 1000)
            wave_spacing = None
        else:
            default_spacing = self.config.get("mesh_settings.wave_spacing", 4.0)
            wave_spacing = self._prompt_float("Wave spacing / distance (mm)", default_spacing)
            wave_count = None

        pattern = self._prompt_choice("Wave pattern", ["sine", "triangular", "sawtooth"], self.config.get("mesh_settings.wave_pattern", "sine"))
        wave_smoothness = self._prompt_int("Wave smoothness (1-10)", self.config.get("mesh_settings.wave_smoothness", 10), 1, 10)
        layer_alt = self._prompt_int("Layer alternation (revolutions)", self.config.get("mesh_settings.layer_alternation", 2), 1, 10)
        phase_offset = self._prompt_float("Phase offset (%)", self.config.get("mesh_settings.phase_offset", 50), 0, 100)
        seam_shift = self._prompt_float("Seam shift (waves per alternation)", self.config.get("mesh_settings.seam_shift", 0.0))

        # Spiral-specific settings
        if vase_mode:
            print("\n[Spiral Vase Settings]")
            spiral_points_per_degree = self._prompt_float("Spiral sampling resolution (points/degree)", self.config.get("print_settings.spiral_points_per_degree", 1.2), 0.1)
        else:
            spiral_points_per_degree = None

        # Base settings
        print("\n[Base Integrity Settings]")
        base_height = self._prompt_float("Base height (mm)", self.config.get("mesh_settings.base_height", 28.0))
        base_mode = self._prompt_choice(
            "Base mode",
            ["tighter_waves", "fewer_gaps", "solid_then_mesh"],
            self.config.get("mesh_settings.base_mode", "fewer_gaps")
        )
        base_transition = self._prompt_choice(
            "Base transition",
            ["linear", "exponential", "step"],
            self.config.get("mesh_settings.base_transition", "exponential")
        )

        result = {
            "nozzle": nozzle,
            "nozzle_temp": nozzle_temp,
            "bed_temp": bed_temp,
            "layer_height": layer_height,
            "print_speed": print_speed,
            "travel_speed": travel_speed,
            "fan_speed": fan_speed,
            "max_volumetric_speed": max_volumetric_speed,
            "vase_mode": vase_mode,
            "wave_amplitude": wave_amplitude,
            "wave_smoothness": wave_smoothness,
            "wave_pattern": pattern,
            "layer_alternation": layer_alt,
            "phase_offset": phase_offset,
            "seam_shift": seam_shift,
            "base_height": base_height,
            "base_mode": base_mode,
            "base_transition": base_transition
        }

        if skirt_enabled:
            result["skirt"] = True
            result["skirt_distance"] = skirt_distance
            result["skirt_height"] = skirt_height
        else:
            result["no_skirt"] = True

        if wave_spacing is not None:
            result["wave_spacing"] = wave_spacing
        if wave_count is not None:
            result["wave_count"] = wave_count
        if spiral_points_per_degree is not None:
            result["spiral_points_per_degree"] = spiral_points_per_degree

        return result

    def _prompt_float(self, prompt: str, default: float, min_val=None, max_val=None) -> float:
        """Prompt for float input."""
        while True:
            try:
                value = input(f"  {prompt} [{default}]: ").strip()
                if not value:
                    return default
                val = float(value)
                if min_val is not None and val < min_val:
                    print(f"    Value must be >= {min_val}")
                    continue
                if max_val is not None and val > max_val:
                    print(f"    Value must be <= {max_val}")
                    continue
                return val
            except ValueError:
                print("    Invalid number")

    def _prompt_int(self, prompt: str, default: int, min_val=1, max_val=100) -> int:
        """Prompt for integer input."""
        return int(self._prompt_float(prompt, float(default), min_val, max_val))

    def _prompt_choice(self, prompt: str, choices: list, default: str) -> str:
        """Prompt for choice input."""
        print(f"  {prompt}:")
        for i, choice in enumerate(choices, 1):
            marker = "(*)" if choice == default else "   "
            print(f"    {marker} {i}. {choice}")

        while True:
            try:
                choice = int(input(f"  Select [{choices.index(default) + 1}]: ").strip() or str(choices.index(default) + 1)) - 1
                if 0 <= choice < len(choices):
                    return choices[choice]
            except (ValueError, IndexError):
                pass
            print("  Invalid selection")

    def _slice_file(self, stl_file: str, args) -> None:
        """Slice an STL file with given parameters."""
        if not os.path.exists(stl_file):
            raise ProjectError(f"File not found: {stl_file}")

        # Build config overrides from arguments
        config_overrides = {}

        if hasattr(args, 'nozzle') and args.nozzle:
            config_overrides.setdefault('printer', {})['nozzle_diameter'] = args.nozzle

        if hasattr(args, 'nozzle_temp') and args.nozzle_temp:
            config_overrides.setdefault('printer', {})['nozzle_temp'] = args.nozzle_temp

        if hasattr(args, 'bed_temp') and args.bed_temp:
            config_overrides.setdefault('printer', {})['bed_temp'] = args.bed_temp

        if hasattr(args, 'layer_height') and args.layer_height:
            config_overrides.setdefault('print_settings', {})['layer_height'] = args.layer_height

        if hasattr(args, 'print_speed') and args.print_speed:
            config_overrides.setdefault('print_settings', {})['print_speed'] = args.print_speed

        if hasattr(args, 'travel_speed') and args.travel_speed:
            config_overrides.setdefault('print_settings', {})['travel_speed'] = args.travel_speed

        if hasattr(args, 'purge_gap') and args.purge_gap is not None:
            config_overrides.setdefault('print_settings', {})['purge_gap'] = args.purge_gap

        if hasattr(args, 'purge_length') and args.purge_length is not None:
            config_overrides.setdefault('print_settings', {})['purge_length'] = args.purge_length

        if hasattr(args, 'purge_side') and args.purge_side:
            config_overrides.setdefault('print_settings', {})['purge_side'] = args.purge_side

        # Skirt settings
        if hasattr(args, 'no_skirt') and args.no_skirt:
            config_overrides.setdefault('print_settings', {})['skirt_enabled'] = False
        elif hasattr(args, 'skirt') and args.skirt:
            config_overrides.setdefault('print_settings', {})['skirt_enabled'] = True

        if hasattr(args, 'skirt_distance') and args.skirt_distance is not None:
            config_overrides.setdefault('print_settings', {})['skirt_distance'] = args.skirt_distance

        if hasattr(args, 'skirt_height') and args.skirt_height is not None:
            config_overrides.setdefault('print_settings', {})['skirt_height'] = args.skirt_height

        if hasattr(args, 'first_layer_squish') and args.first_layer_squish is not None:
            config_overrides.setdefault('print_settings', {})['first_layer_squish'] = args.first_layer_squish

        if hasattr(args, 'wave_amplitude') and args.wave_amplitude:
            config_overrides.setdefault('mesh_settings', {})['wave_amplitude'] = args.wave_amplitude

        if hasattr(args, 'wave_spacing') and args.wave_spacing:
            config_overrides.setdefault('mesh_settings', {})['wave_spacing'] = args.wave_spacing

        if hasattr(args, 'wave_smoothness') and args.wave_smoothness:
            config_overrides.setdefault('mesh_settings', {})['wave_smoothness'] = args.wave_smoothness

        if hasattr(args, 'wave_pattern') and args.wave_pattern:
            config_overrides.setdefault('mesh_settings', {})['wave_pattern'] = args.wave_pattern

        if hasattr(args, 'layer_alternation') and args.layer_alternation:
            config_overrides.setdefault('mesh_settings', {})['layer_alternation'] = args.layer_alternation

        if hasattr(args, 'phase_offset') and args.phase_offset:
            config_overrides.setdefault('mesh_settings', {})['phase_offset'] = args.phase_offset

        if hasattr(args, 'seam_shift') and args.seam_shift is not None:
            config_overrides.setdefault('mesh_settings', {})['seam_shift'] = args.seam_shift

        if hasattr(args, 'seam_position') and args.seam_position:
            config_overrides.setdefault('mesh_settings', {})['seam_position'] = args.seam_position

        if hasattr(args, 'seam_transition_waves') and args.seam_transition_waves is not None:
            config_overrides.setdefault('mesh_settings', {})['seam_transition_waves'] = args.seam_transition_waves

        if hasattr(args, 'scale') and args.scale is not None:
            config_overrides['model_scale'] = args.scale

        if hasattr(args, 'base_height') and args.base_height:
            config_overrides.setdefault('mesh_settings', {})['base_height'] = args.base_height

        if hasattr(args, 'base_mode') and args.base_mode:
            config_overrides.setdefault('mesh_settings', {})['base_mode'] = args.base_mode

        if hasattr(args, 'base_transition') and args.base_transition:
            config_overrides.setdefault('mesh_settings', {})['base_transition'] = args.base_transition

        # New CLI flags
        if hasattr(args, 'fan_speed') and args.fan_speed is not None:
            config_overrides.setdefault('print_settings', {})['fan_speed'] = args.fan_speed

        if hasattr(args, 'max_volumetric_speed') and args.max_volumetric_speed is not None:
            config_overrides.setdefault('print_settings', {})['max_volumetric_speed'] = args.max_volumetric_speed

        if hasattr(args, 'wave_count') and args.wave_count is not None:
            config_overrides.setdefault('mesh_settings', {})['wave_count'] = args.wave_count

        if hasattr(args, 'vase_mode') and args.vase_mode:
            config_overrides.setdefault('print_settings', {})['vase_mode'] = True

        if hasattr(args, 'spiral_points_per_degree') and args.spiral_points_per_degree is not None:
            config_overrides.setdefault('print_settings', {})['spiral_points_per_degree'] = args.spiral_points_per_degree

        # Spiral smoothing and sampling overrides
        if hasattr(args, 'target_samples_per_wave') and args.target_samples_per_wave is not None:
            config_overrides.setdefault('print_settings', {})['target_samples_per_wave'] = args.target_samples_per_wave

        if hasattr(args, 'smoothing_window_size') and args.smoothing_window_size is not None:
            config_overrides.setdefault('print_settings', {})['smoothing_window_size'] = args.smoothing_window_size

        if hasattr(args, 'smoothing_threshold') and args.smoothing_threshold is not None:
            config_overrides.setdefault('print_settings', {})['smoothing_move_threshold'] = args.smoothing_threshold

        if hasattr(args, 'no_auto_resample_spiral') and args.no_auto_resample_spiral:
            # CLI flag is --no-auto-resample-spiral, store enabled state as boolean
            config_overrides.setdefault('print_settings', {})['auto_resample_spiral'] = False

        if hasattr(args, 'wave_asymmetry') and args.wave_asymmetry:
            config_overrides.setdefault('mesh_settings', {})['wave_asymmetry'] = args.wave_asymmetry

        if hasattr(args, 'wave_asymmetry_intensity') and args.wave_asymmetry_intensity is not None:
            config_overrides.setdefault('mesh_settings', {})['wave_asymmetry_intensity'] = args.wave_asymmetry_intensity

        # Slice
        print("\n⏳ Slicing...")
        output_gcode = self.slicer.slice_stl(
            stl_file,
            output_file=args.output if hasattr(args, 'output') and args.output else None,
            override_config=config_overrides if config_overrides else None
        )

        print(f"✓ GCode saved: {output_gcode}")

        # Launch preview
        print("\n🚀 Launching OrcaSlicer...")
        self.preview.launch_preview(output_gcode)

        # Print reproduction command
        self._print_reproduction_command(stl_file, config_overrides)

    def _print_reproduction_command(self, stl_file: str, overrides: dict) -> None:
        """Print the command to reproduce this slicing."""
        print("\n" + "-" * 60)
        print("To reproduce this slicing, use:")
        print("-" * 60)

        cmd = f"python -m project.core --input {stl_file}"

        if 'printer' in overrides:
            if 'nozzle_diameter' in overrides['printer']:
                cmd += f" --nozzle {overrides['printer']['nozzle_diameter']}"
            if 'nozzle_temp' in overrides['printer']:
                cmd += f" --nozzle-temp {overrides['printer']['nozzle_temp']}"
            if 'bed_temp' in overrides['printer']:
                cmd += f" --bed-temp {overrides['printer']['bed_temp']}"

        if 'print_settings' in overrides:
            if 'layer_height' in overrides['print_settings']:
                cmd += f" --layer-height {overrides['print_settings']['layer_height']}"
            if 'print_speed' in overrides['print_settings']:
                cmd += f" --print-speed {overrides['print_settings']['print_speed']}"
            if 'travel_speed' in overrides['print_settings']:
                cmd += f" --travel-speed {overrides['print_settings']['travel_speed']}"
            if 'fan_speed' in overrides['print_settings']:
                cmd += f" --fan-speed {overrides['print_settings']['fan_speed']}"
            if 'max_volumetric_speed' in overrides['print_settings']:
                cmd += f" --max-volumetric-speed {overrides['print_settings']['max_volumetric_speed']}"
            if 'vase_mode' in overrides['print_settings'] and overrides['print_settings']['vase_mode']:
                cmd += " --vase-mode"
            if 'spiral_points_per_degree' in overrides['print_settings']:
                cmd += f" --spiral-points-per-degree {overrides['print_settings']['spiral_points_per_degree']}"
            if 'target_samples_per_wave' in overrides['print_settings']:
                cmd += f" --target-samples-per-wave {overrides['print_settings']['target_samples_per_wave']}"
            if 'smoothing_window_size' in overrides['print_settings']:
                cmd += f" --smoothing-window-size {overrides['print_settings']['smoothing_window_size']}"
            if 'smoothing_move_threshold' in overrides['print_settings']:
                cmd += f" --smoothing-threshold {overrides['print_settings']['smoothing_move_threshold']}"
            if 'auto_resample_spiral' in overrides['print_settings'] and overrides['print_settings']['auto_resample_spiral'] is False:
                cmd += " --no-auto-resample-spiral"
            if 'first_layer_squish' in overrides['print_settings']:
                cmd += f" --first-layer-squish {overrides['print_settings']['first_layer_squish']}"

        if 'mesh_settings' in overrides:
            if 'wave_amplitude' in overrides['mesh_settings']:
                cmd += f" --wave-amplitude {overrides['mesh_settings']['wave_amplitude']}"
            if 'wave_spacing' in overrides['mesh_settings']:
                cmd += f" --wave-spacing {overrides['mesh_settings']['wave_spacing']}"
            if 'wave_count' in overrides['mesh_settings']:
                cmd += f" --wave-count {overrides['mesh_settings']['wave_count']}"
            if 'wave_pattern' in overrides['mesh_settings']:
                cmd += f" --wave-pattern {overrides['mesh_settings']['wave_pattern']}"
            if 'wave_smoothness' in overrides['mesh_settings']:
                cmd += f" --wave-smoothness {overrides['mesh_settings']['wave_smoothness']}"
            if 'layer_alternation' in overrides['mesh_settings']:
                cmd += f" --layer-alternation {overrides['mesh_settings']['layer_alternation']}"
            if 'phase_offset' in overrides['mesh_settings']:
                cmd += f" --phase-offset {overrides['mesh_settings']['phase_offset']}"
            if 'seam_shift' in overrides['mesh_settings']:
                cmd += f" --seam-shift {overrides['mesh_settings']['seam_shift']}"
            if 'seam_position' in overrides['mesh_settings']:
                cmd += f" --seam-position {overrides['mesh_settings']['seam_position']}"
            if 'seam_transition_waves' in overrides['mesh_settings']:
                cmd += f" --seam-transition-waves {overrides['mesh_settings']['seam_transition_waves']}"
        if 'model_scale' in overrides and overrides['model_scale'] != 1.0:
            cmd += f" --scale {overrides['model_scale']}"
        if 'mesh_settings' in overrides:
            if 'base_height' in overrides['mesh_settings']:
                cmd += f" --base-height {overrides['mesh_settings']['base_height']}"
            if 'base_mode' in overrides['mesh_settings']:
                cmd += f" --base-mode {overrides['mesh_settings']['base_mode']}"
            if 'base_transition' in overrides['mesh_settings']:
                cmd += f" --base-transition {overrides['mesh_settings']['base_transition']}"

        print(f"\n{cmd}\n")


def main():
    """Main entry point."""
    app = MeshVaseCliApp()
    app.run()


if __name__ == "__main__":
    main()


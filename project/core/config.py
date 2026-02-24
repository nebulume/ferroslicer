'''
Configuration management for MeshVase Slicer.
'''

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from .logger import setup_logger


def _default_config_path() -> str:
    """Locate config.json: bundled _MEIPASS dir when frozen, repo root otherwise."""
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "config.json")
    return str(Path(__file__).parent.parent.parent / "config.json")

logger = setup_logger("config")


class Config:
    """
    Manages MeshVase slicer configuration.
    Loads from config.json with support for nested dictionaries.
    """

    def __init__(self, config_path: str = ""):
        self.config_path = config_path or _default_config_path()
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from file or use defaults.
        """
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    config = json.load(f)
                logger.info(f"Loaded config from {self.config_path}")
                return config
            except Exception as e:
                logger.warning(f"Failed to load config: {e}, using defaults")
                return self._get_default_config()
        else:
            logger.info("Config file not found, using defaults")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """
        Get complete default configuration for MeshVase Slicer.
        """
        return {
            "project_name": "MeshVase Slicer",
            "version": "0.3.0",
            "debug": False,
            "output_dir": "output",
            "printer": {
                "nozzle_diameter": 1.0,
                "filament_diameter": 1.75,
                "nozzle_temp": 260,
                "bed_temp": 65,
                "kinematics": "cartesian",
                "bed_x": 220.0,
                "bed_y": 220.0,
                "max_z": 280.0,
                "origin": "front_left"
            },
            "print_settings": {
                "layer_height": 0.5,
                "print_speed": 35,
                "first_layer_speed_pct": 50,
                "travel_speed": 40,
                "fan_speed": 25,
                "print_accel": 500,
                "travel_accel": 1500,
                "z_hop": 0.0,
                "skirt_enabled": True,
                "skirt_distance": 0.0,
                "skirt_height": 1,
                "skirt_loops": 1,
                "seam_ramp_enabled": False,
                "seam_ramp_pcts": [25, 50, 75, 100],
                "seam_ramp_layers": []
            },
            "mesh_settings": {
                "wave_amplitude": 2.0,
                "wave_spacing": 4.0,
                "wave_smoothness": 10,
                "wave_pattern": "sine",  # Options: sine, triangular, sawtooth
                "layer_alternation": 2,
                "phase_offset": 50,
                "seam_shift": 0.0,
                "wave_skew_enabled": False,
                "wave_skew": 0.0,
                "start_phase": "random",  # Options: random, aligned
                "base_height": 28.0,
                "base_mode": "fewer_gaps",  # Options: tighter_waves, fewer_gaps, solid_then_mesh
                "base_transition": "exponential",  # Options: linear, exponential, step
                "diameter_scaling": "dynamic",  # Options: constant_wavelength, dynamic
                "curvature_threshold_angle": 30,
                "curvature_threshold_distance": 10,
                "curvature_amplitude_reduction": 60,
                "curvature_frequency_reduction": 40,
                "transition_smoothness": "medium"  # Options: instant, fast, medium, slow
            },
            "orcaslicer_path": "/Applications/OrcaSlicer.app"
        }

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value (supports nested keys with dot notation).
        Example: config.get("printer.nozzle_diameter")
        """
        if "." in key:
            keys = key.split(".")
            value = self._config
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k, default)
                else:
                    return default
            return value
        return self._config.get(key, default)

    def get_nested(self, section: str) -> Dict[str, Any]:
        """
        Get all values from a configuration section.
        Example: config.get_nested("mesh_settings")
        """
        return self._config.get(section, {})

    def set(self, key: str, value: Any) -> None:
        """
        Set a configuration value (supports nested keys with dot notation).
        """
        if "." in key:
            keys = key.split(".")
            config = self._config
            for k in keys[:-1]:
                if k not in config:
                    config[k] = {}
                config = config[k]
            config[keys[-1]] = value
        else:
            self._config[key] = value
        self._save_config()

    def _save_config(self) -> None:
        """
        Save configuration to file.
        """
        try:
            with open(self.config_path, "w") as f:
                json.dump(self._config, f, indent=4)
            logger.info(f"Saved config to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")


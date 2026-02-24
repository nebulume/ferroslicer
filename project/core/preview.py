'''
Preview system and OrcaSlicer integration.
'''

import subprocess
import os
from pathlib import Path
from typing import Tuple, List
from .logger import setup_logger

logger = setup_logger("preview")


class PreviewSystem:
    """Handles preview launching and OrcaSlicer integration."""

    def __init__(self, orcaslicer_path: str = "/Applications/OrcaSlicer.app"):
        """
        Initialize preview system.

        Args:
            orcaslicer_path: Path to OrcaSlicer application
        """
        self.orcaslicer_path = orcaslicer_path
        self._check_orcaslicer()

    def _check_orcaslicer(self) -> bool:
        """Check if OrcaSlicer is available."""
        if os.path.exists(self.orcaslicer_path):
            logger.info(f"OrcaSlicer found at {self.orcaslicer_path}")
            return True
        else:
            logger.warning(f"OrcaSlicer not found at {self.orcaslicer_path}")
            return False

    def launch_preview(self, gcode_file: str) -> bool:
        """
        Launch OrcaSlicer with generated GCode.

        Args:
            gcode_file: Path to GCode file

        Returns:
            True if successful, False otherwise
        """
        if not os.path.exists(gcode_file):
            logger.error(f"GCode file not found: {gcode_file}")
            return False

        if not self._check_orcaslicer():
            logger.warning(f"Cannot launch OrcaSlicer. File location: {gcode_file}")
            print(f"\n⚠️  OrcaSlicer not found. GCode saved to: {gcode_file}")
            return False

        try:
            # Use 'open' command on macOS
            subprocess.Popen(["open", "-a", self.orcaslicer_path, gcode_file])
            logger.info(f"Launched OrcaSlicer with {gcode_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to launch OrcaSlicer: {e}")
            print(f"\n⚠️  Failed to launch OrcaSlicer: {e}")
            print(f"GCode file: {gcode_file}")
            return False

    @staticmethod
    def get_gcode_file_size(gcode_file: str) -> float:
        """
        Get size of GCode file in KB.

        Args:
            gcode_file: Path to GCode file

        Returns:
            File size in KB
        """
        if os.path.exists(gcode_file):
            return os.path.getsize(gcode_file) / 1024.0
        return 0.0

    @staticmethod
    def validate_gcode_content(gcode_content: str) -> Tuple[bool, List[str]]:
        """
        Validate GCode content for correctness.

        Args:
            gcode_content: GCode string

        Returns:
            (is_valid, list_of_warnings) tuple
        """
        warnings = []

        # Check file size
        if len(gcode_content) < 500:
            warnings.append("GCode file seems very small - may not contain actual content")

        # Check for critical commands
        has_start = "START_PRINT" in gcode_content
        has_g1 = "G1" in gcode_content
        has_end = "END_PRINT" in gcode_content

        if not has_start:
            warnings.append("Missing START_PRINT command")
        if not has_g1:
            warnings.append("No movement commands (G1) found")
        if not has_end:
            warnings.append("Missing END_PRINT command")

        # Check for NaN/Inf
        if "nan" in gcode_content.lower() or "inf" in gcode_content.lower():
            warnings.append("GCode contains NaN or Inf values")
            return False, warnings

        is_valid = not any("Missing" in w or "NaN" in w for w in warnings)

        return is_valid, warnings

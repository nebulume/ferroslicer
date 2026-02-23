"""
Background thread for running the full slicer pipeline.
Keeps the GUI responsive during potentially slow generation.
"""

import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class SlicerWorker(QThread):
    """
    Runs MeshVaseSlicer.slice_stl() in a background thread.

    Signals:
        progress(int, str)  — (percent 0-100, message)
        finished(str)       — path to generated .gcode file
        error(str)          — error message
    """

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

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
            from project.core.slicer import MeshVaseSlicer

            self.progress.emit(5, "Loading config…")
            config = Config()
            slicer = MeshVaseSlicer(config)

            overrides = dict(self.config_overrides)
            if self.custom_gcode:
                overrides["custom_gcode"] = self.custom_gcode

            self.progress.emit(10, "Parsing STL…")
            # We piggy-back on the slicer — it will emit no sub-progress,
            # but we bracket with coarse markers.
            output_path = slicer.slice_stl(
                self.stl_path,
                output_file=self.output_file or None,
                override_config=overrides,
            )
            self.progress.emit(100, "Done!")
            self.finished.emit(output_path)

        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"{e}\n\n{tb}")

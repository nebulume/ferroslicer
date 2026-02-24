"""
GCode Library dialog — browse all generated GCode files with settings detail
and a live 3D toolpath preview.
"""

import json
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QTextEdit, QSplitter, QMessageBox, QWidget,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import db.print_db as pdb
from gui.widgets.toolpath_viewer import ToolpathViewer


STATUS_COLORS = {
    "generated": "#4a90d9",
    "sent":      "#f0a500",
    "printing":  "#27ae60",
    "completed": "#2ecc71",
    "failed":    "#e74c3c",
}

# Map settings JSON keys → (section, CLI flag, is_bool_flag)
# section: "printer" | "print_settings" | "mesh_settings" | None (top-level)
_SETTINGS_MAP = [
    # printer
    ("printer",        "nozzle_diameter",          "--nozzle",                    False),
    ("printer",        "nozzle_temp",               "--nozzle-temp",               False),
    ("printer",        "bed_temp",                  "--bed-temp",                  False),
    # print settings
    ("print_settings", "layer_height",              "--layer-height",              False),
    ("print_settings", "print_speed",               "--print-speed",               False),
    ("print_settings", "travel_speed",              "--travel-speed",              False),
    ("print_settings", "fan_speed",                 "--fan-speed",                 False),
    ("print_settings", "max_volumetric_speed",      "--max-volumetric-speed",      False),
    ("print_settings", "vase_mode",                 "--vase-mode",                 True),
    ("print_settings", "spiral_points_per_degree",  "--spiral-points-per-degree",  False),
    ("print_settings", "target_samples_per_wave",   "--target-samples-per-wave",   False),
    ("print_settings", "smoothing_window_size",     "--smoothing-window-size",     False),
    ("print_settings", "smoothing_move_threshold",  "--smoothing-threshold",       False),
    ("print_settings", "first_layer_squish",        "--first-layer-squish",        False),
    ("print_settings", "purge_gap",                 "--purge-gap",                 False),
    ("print_settings", "purge_length",              "--purge-length",              False),
    ("print_settings", "purge_side",                "--purge-side",                False),
    ("print_settings", "skirt_distance",            "--skirt-distance",            False),
    ("print_settings", "skirt_height",              "--skirt-height",              False),
    # mesh settings
    ("mesh_settings",  "wave_amplitude",            "--wave-amplitude",            False),
    ("mesh_settings",  "wave_spacing",              "--wave-spacing",              False),
    ("mesh_settings",  "wave_count",                "--wave-count",                False),
    ("mesh_settings",  "wave_pattern",              "--wave-pattern",              False),
    ("mesh_settings",  "wave_smoothness",           "--wave-smoothness",           False),
    ("mesh_settings",  "wave_asymmetry",            "--wave-asymmetry",            True),
    ("mesh_settings",  "wave_asymmetry_intensity",  "--wave-asymmetry-intensity",  False),
    ("mesh_settings",  "layer_alternation",         "--layer-alternation",         False),
    ("mesh_settings",  "phase_offset",              "--phase-offset",              False),
    ("mesh_settings",  "seam_shift",                "--seam-shift",                False),
    ("mesh_settings",  "seam_position",             "--seam-position",             False),
    ("mesh_settings",  "seam_transition_waves",     "--seam-transition-waves",     False),
    ("mesh_settings",  "base_height",               "--base-height",               False),
    ("mesh_settings",  "base_mode",                 "--base-mode",                 False),
    ("mesh_settings",  "base_transition",           "--base-transition",           False),
    # top-level
    (None,             "model_scale",               "--scale",                     False),
]


def _settings_to_cli(job: dict) -> str:
    """Convert a job's settings_json to a CLI invocation string."""
    settings_json = job.get("settings_json", "")
    stl = job.get("stl_file", "model.stl")
    stl_name = Path(stl).name if stl else "model.stl"

    lines = [f"python -m project.core \\", f"  --input {stl_name}"]

    # skirt/no-skirt first (special bool)
    try:
        settings = json.loads(settings_json) if settings_json else {}
    except Exception:
        settings = {}

    ps = settings.get("print_settings", {})
    if isinstance(ps, dict):
        skirt = ps.get("skirt_enabled")
        if skirt is False:
            lines.append("  --no-skirt \\")
        elif skirt is True:
            lines.append("  --skirt \\")

    # auto_resample_spiral=False → --no-auto-resample-spiral
    if isinstance(ps, dict) and ps.get("auto_resample_spiral") is False:
        lines.append("  --no-auto-resample-spiral \\")

    for section, key, flag, is_bool in _SETTINGS_MAP:
        if section is None:
            val = settings.get(key)
        else:
            sec_data = settings.get(section, {})
            val = sec_data.get(key) if isinstance(sec_data, dict) else None

        if val is None:
            continue

        if is_bool:
            if val:
                lines.append(f"  {flag} \\")
        else:
            lines.append(f"  {flag} {val} \\")

    # Strip trailing backslash from last line
    if lines:
        lines[-1] = lines[-1].rstrip(" \\")

    return "\n".join(lines)


class GCodeLibraryDialog(QDialog):
    """
    3-pane dialog:
      Left   — list of all generated jobs (name + date + status)
      Middle — CLI command + action buttons
      Right  — live ToolpathViewer showing selected job's GCode
    """

    settings_loaded = pyqtSignal(dict)   # emitted when user clicks "Load Settings"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GCode Library")
        self.resize(1400, 820)
        self._jobs: list[dict] = []
        self._build_ui()
        self._load_jobs()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        # ── Left: job list ─────────────────────────────────────────────────
        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.setContentsMargins(0, 0, 4, 0)
        left_l.setSpacing(4)

        lbl = QLabel("Generated files")
        lbl.setStyleSheet("color: #889; font-size: 11px; font-weight: bold;")
        left_l.addWidget(lbl)

        self.job_list = QListWidget()
        self.job_list.setAlternatingRowColors(True)
        self.job_list.setStyleSheet(
            "QListWidget { font-size: 12px; }"
            "QListWidget::item { padding: 4px 6px; }"
        )
        self.job_list.currentRowChanged.connect(self._on_job_selected)
        left_l.addWidget(self.job_list)

        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self._load_jobs)
        left_l.addWidget(refresh_btn)

        splitter.addWidget(left_w)

        # ── Middle: CLI command + actions ──────────────────────────────────
        mid_w = QWidget()
        mid_l = QVBoxLayout(mid_w)
        mid_l.setContentsMargins(4, 0, 4, 0)
        mid_l.setSpacing(4)

        lbl2 = QLabel("Terminal command")
        lbl2.setStyleSheet("color: #889; font-size: 11px; font-weight: bold;")
        mid_l.addWidget(lbl2)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        mono = QFont("Menlo")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setPointSize(10)
        self.detail.setFont(mono)
        self.detail.setStyleSheet(
            "QTextEdit { background: #0b0d10; color: #7ec8a0; border: 1px solid #2a2d3a;"
            " border-radius: 3px; }"
        )
        mid_l.addWidget(self.detail)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.load_settings_btn = QPushButton("Load Settings")
        self.load_settings_btn.setFixedHeight(30)
        self.load_settings_btn.setEnabled(False)
        self.load_settings_btn.setToolTip("Apply these settings to the slicer panel")
        self.load_settings_btn.setStyleSheet(
            "QPushButton { background: #2a5298; color: white; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3a62a8; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )
        self.load_settings_btn.clicked.connect(self._load_settings)
        btn_row.addWidget(self.load_settings_btn)

        self.reveal_btn = QPushButton("Open in Finder")
        self.reveal_btn.setFixedHeight(30)
        self.reveal_btn.setEnabled(False)
        self.reveal_btn.clicked.connect(self._reveal_gcode)
        btn_row.addWidget(self.reveal_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setFixedHeight(30)
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet(
            "QPushButton { color: #e74c3c; }"
            "QPushButton:disabled { color: #666; }"
        )
        self.delete_btn.clicked.connect(self._delete_job)
        btn_row.addWidget(self.delete_btn)

        mid_l.addLayout(btn_row)
        splitter.addWidget(mid_w)

        # ── Right: live toolpath preview ───────────────────────────────────
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(4, 0, 0, 0)
        right_l.setSpacing(4)

        lbl3 = QLabel("GCode preview")
        lbl3.setStyleSheet("color: #889; font-size: 11px; font-weight: bold;")
        right_l.addWidget(lbl3)

        self.toolpath = ToolpathViewer()
        right_l.addWidget(self.toolpath, stretch=1)

        splitter.addWidget(right_w)

        splitter.setSizes([280, 460, 660])

        # ── Close button ──────────────────────────────────────────────────
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(30)
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_jobs(self):
        self._jobs = pdb.get_all_jobs()
        self.job_list.clear()
        for job in self._jobs:
            name    = job.get("job_name", "?")
            created = (job.get("created_at") or "")[:16]
            status  = job.get("status", "")
            color   = STATUS_COLORS.get(status, "#888")

            item = QListWidgetItem(f"{name}\n{created}  [{status}]")
            item.setForeground(QColor(color))
            self.job_list.addItem(item)

    # ── Selection handler ─────────────────────────────────────────────────────

    def _on_job_selected(self, row: int):
        no_job = (row < 0 or row >= len(self._jobs))
        self.load_settings_btn.setEnabled(not no_job)
        self.delete_btn.setEnabled(not no_job)
        self._reset_load_btn()

        if no_job:
            self.detail.clear()
            self.reveal_btn.setEnabled(False)
            return

        job   = self._jobs[row]
        gcode = job.get("gcode_file", "")
        gcode_exists = bool(gcode and Path(gcode).exists())
        self.reveal_btn.setEnabled(gcode_exists)

        # Show settings as CLI command
        self.detail.setPlainText(_settings_to_cli(job))

        # Load toolpath preview if file exists
        if gcode_exists:
            self.toolpath.load_gcode(gcode)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _reset_load_btn(self):
        self.load_settings_btn.setText("Load Settings")
        self.load_settings_btn.setStyleSheet(
            "QPushButton { background: #2a5298; color: white; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3a62a8; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )

    def _load_settings(self):
        """Emit the selected job's settings so main window can apply them."""
        row = self.job_list.currentRow()
        if row < 0 or row >= len(self._jobs):
            return
        settings_json = self._jobs[row].get("settings_json", "")
        if not settings_json:
            return
        try:
            cfg = json.loads(settings_json)
            self.settings_loaded.emit(cfg)
            self.load_settings_btn.setText("✓  Loaded")
            self.load_settings_btn.setStyleSheet(
                "QPushButton { background: #1e6b3a; color: #7effa0; border-radius: 3px; font-weight: bold; }"
                "QPushButton:hover { background: #28854a; }"
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not parse settings:\n{e}")

    def _reveal_gcode(self):
        row = self.job_list.currentRow()
        if row < 0 or row >= len(self._jobs):
            return
        gcode = self._jobs[row].get("gcode_file", "")
        if gcode and Path(gcode).exists():
            subprocess.Popen(["open", "-R", gcode])
        else:
            QMessageBox.warning(self, "Not Found", f"File not found:\n{gcode}")

    def _delete_job(self):
        row = self.job_list.currentRow()
        if row < 0 or row >= len(self._jobs):
            return
        job = self._jobs[row]
        reply = QMessageBox.question(
            self, "Delete Job",
            f"Remove '{job.get('job_name', '')}' from history?\n"
            "(The GCode file on disk is not deleted.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            pdb.delete_job(job["id"])
            self._load_jobs()
            self.detail.clear()
            self.load_settings_btn.setEnabled(False)
            self.reveal_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)

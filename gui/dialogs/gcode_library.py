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
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import db.print_db as pdb
from gui.widgets.toolpath_viewer import ToolpathViewer


STATUS_COLORS = {
    "generated": "#4a90d9",
    "sent":      "#f0a500",
    "printing":  "#27ae60",
    "completed": "#2ecc71",
    "failed":    "#e74c3c",
}


class GCodeLibraryDialog(QDialog):
    """
    3-pane dialog:
      Left   — list of all generated jobs (name + date + status)
      Middle — settings text + action buttons
      Right  — live ToolpathViewer showing selected job's GCode
    """

    settings_loaded = pyqtSignal(dict)   # emitted when user clicks "Load Settings"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GCode Library")
        self.resize(1400, 780)
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

        # ── Middle: settings detail + actions ──────────────────────────────
        mid_w = QWidget()
        mid_l = QVBoxLayout(mid_w)
        mid_l.setContentsMargins(4, 0, 4, 0)
        mid_l.setSpacing(4)

        lbl2 = QLabel("Settings used")
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
            "QTextEdit { background: #0e0f14; color: #8da; border: 1px solid #2a2d3a;"
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

        # ── Right: toolpath preview ────────────────────────────────────────
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(4, 0, 0, 0)
        right_l.setSpacing(4)

        lbl3 = QLabel("Toolpath preview")
        lbl3.setStyleSheet("color: #889; font-size: 11px; font-weight: bold;")
        right_l.addWidget(lbl3)

        self.tp_viewer = ToolpathViewer()
        right_l.addWidget(self.tp_viewer)

        splitter.addWidget(right_w)
        splitter.setSizes([280, 340, 780])

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

        if no_job:
            self.detail.clear()
            self.reveal_btn.setEnabled(False)
            self.tp_viewer.clear()
            return

        job   = self._jobs[row]
        gcode = job.get("gcode_file", "")
        gcode_exists = bool(gcode and Path(gcode).exists())
        self.reveal_btn.setEnabled(gcode_exists)

        # Settings text
        lines = [
            f"Job:     {job.get('job_name', '')}",
            f"STL:     {Path(job['stl_file']).name if job.get('stl_file') else '—'}",
            f"GCode:   {Path(gcode).name if gcode else '—'}",
            f"Status:  {job.get('status', '')}",
            f"Created: {job.get('created_at', '')}",
            f"Sent:    {job.get('sent_at') or '—'}",
            "",
            "── Settings ─────────────────────────────────",
        ]
        settings_json = job.get("settings_json", "")
        if settings_json:
            try:
                settings = json.loads(settings_json)
                for section, vals in settings.items():
                    lines.append(f"[{section}]")
                    if isinstance(vals, dict):
                        for k, v in vals.items():
                            lines.append(f"  {k}: {v}")
                    else:
                        lines.append(f"  {vals}")
            except Exception:
                lines.append(settings_json)
        self.detail.setPlainText("\n".join(lines))

        # Toolpath preview
        if gcode_exists:
            self.tp_viewer.load_gcode(gcode)
        else:
            self.tp_viewer.clear()

    # ── Actions ───────────────────────────────────────────────────────────────

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
            self.tp_viewer.clear()
            self.load_settings_btn.setEnabled(False)
            self.reveal_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)

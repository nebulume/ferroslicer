"""
Print history dialog — table of all slicing jobs with settings and status.
"""

import json
import subprocess
import sys
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QMessageBox, QTextEdit, QSplitter,
    QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

import db.print_db as pdb


STATUS_COLORS = {
    "generated": "#4a90d9",
    "sent":      "#f0a500",
    "printing":  "#27ae60",
    "completed": "#2ecc71",
    "failed":    "#e74c3c",
}


class PrintHistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Print History")
        self.setMinimumSize(900, 500)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        # ── Jobs table ───────────────────────────────────────────────────────
        left = QVBoxLayout()
        left_w = QWidget()
        left_w.setLayout(left)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Job name", "Created", "Status", "Printer"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.selectionModel().selectionChanged.connect(self._on_select)
        left.addWidget(self.table)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load)
        delete_btn = QPushButton("Delete selected")
        delete_btn.clicked.connect(self._delete)
        open_btn = QPushButton("Open GCode file")
        open_btn.clicked.connect(self._open_gcode)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addWidget(open_btn)
        left.addLayout(btn_row)

        splitter.addWidget(left_w)

        # ── Detail panel ─────────────────────────────────────────────────────
        right = QVBoxLayout()
        right_w = QWidget()
        right_w.setLayout(right)

        right.addWidget(QLabel("Settings used:"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(self._mono_font())
        right.addWidget(self.detail)

        splitter.addWidget(right_w)
        splitter.setSizes([550, 350])

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _mono_font(self):
        from PyQt6.QtGui import QFont
        f = QFont("Menlo")
        if not f.exactMatch():
            f = QFont("Courier New")
        f.setPointSize(10)
        return f

    def _load(self):
        self._jobs = pdb.get_all_jobs()
        self.table.setRowCount(len(self._jobs))
        for row, job in enumerate(self._jobs):
            self.table.setItem(row, 0, QTableWidgetItem(str(job["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(job.get("job_name", "")))
            self.table.setItem(row, 2, QTableWidgetItem(job.get("created_at", "")))
            status = job.get("status", "?")
            status_item = QTableWidgetItem(status)
            color = STATUS_COLORS.get(status, "#888888")
            status_item.setForeground(QColor(color))
            self.table.setItem(row, 3, status_item)
            self.table.setItem(row, 4, QTableWidgetItem(job.get("printer_ip", "")))
        self.table.resizeColumnsToContents()

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.detail.clear()
            return
        row = rows[0].row()
        job = self._jobs[row]
        lines = [
            f"Job:       {job.get('job_name', '')}",
            f"STL:       {job.get('stl_file', '')}",
            f"GCode:     {job.get('gcode_file', '')}",
            f"Status:    {job.get('status', '')}",
            f"Created:   {job.get('created_at', '')}",
            f"Sent:      {job.get('sent_at', '') or '—'}",
            f"Completed: {job.get('completed_at', '') or '—'}",
            f"Printer:   {job.get('printer_ip', '') or '—'}",
            "",
            "── Settings ────────────────────────────────",
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

    def _delete(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        job = self._jobs[row]
        reply = QMessageBox.question(
            self, "Delete", f"Delete job '{job.get('job_name', '')}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            pdb.delete_job(job["id"])
            self._load()

    def _open_gcode(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        gcode = self._jobs[row].get("gcode_file", "")
        if gcode and Path(gcode).exists():
            subprocess.Popen(["open", gcode])
        else:
            QMessageBox.warning(self, "Not found", f"GCode file not found:\n{gcode}")

"""
App Settings dialog — printer IP/port, custom start/end GCode, output dir.
"""

import json
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QPushButton, QLabel, QGroupBox,
    QFileDialog, QTabWidget, QWidget, QSpinBox, QDialogButtonBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt

SETTINGS_PATH = Path(__file__).parent.parent.parent / "data" / "app_settings.json"

DEFAULT_START_GCODE = """; Custom start GCode — use {nozzle_temp}, {bed_temp}, {fan_speed} as placeholders
; Example (Klipper):
SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=bed_temp VALUE={bed_temp}
SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=extruder_temp VALUE={nozzle_temp}
M106 S{fan_speed}
START_PRINT"""

DEFAULT_END_GCODE = """; Custom end GCode
G10
G91
G1 Z10 F600
G90
G1 X110 Y200 F4800
END_PRINT"""


def load_app_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {
        "printer_ip": "192.168.1.65",
        "printer_port": 80,
        "start_gcode": "",
        "end_gcode": "",
        "output_dir": str(Path(__file__).parent.parent.parent / "output"),
    }


def save_app_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


class AppSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("App Settings")
        self.setMinimumSize(640, 520)
        self._settings = load_app_settings()
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Printer tab ──────────────────────────────────────────────────────
        printer_tab = QWidget()
        ptab_layout = QVBoxLayout(printer_tab)
        ptab_layout.setSpacing(8)

        conn_group = QGroupBox("Klipper / Moonraker Connection")
        form = QFormLayout(conn_group)
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.65")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(80)
        form.addRow("Printer IP:", self.ip_edit)
        form.addRow("Port:", self.port_spin)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_connection)
        form.addRow("", test_btn)
        ptab_layout.addWidget(conn_group)
        ptab_layout.addStretch()
        tabs.addTab(printer_tab, "Printer")

        # ── GCode tab ────────────────────────────────────────────────────────
        gcode_tab = QWidget()
        gcode_layout = QVBoxLayout(gcode_tab)

        start_group = QGroupBox("Start GCode  (leave blank for default Klipper macros)")
        sg_layout = QVBoxLayout(start_group)
        self.start_gcode_edit = QTextEdit()
        self.start_gcode_edit.setFont(self._mono_font())
        self.start_gcode_edit.setPlaceholderText(DEFAULT_START_GCODE)
        self.start_gcode_edit.setMinimumHeight(150)
        import_start_btn = QPushButton("Import from file…")
        import_start_btn.clicked.connect(lambda: self._import_gcode(self.start_gcode_edit))
        sg_layout.addWidget(self.start_gcode_edit)
        sg_layout.addWidget(import_start_btn)
        gcode_layout.addWidget(start_group)

        end_group = QGroupBox("End GCode  (leave blank for default)")
        eg_layout = QVBoxLayout(end_group)
        self.end_gcode_edit = QTextEdit()
        self.end_gcode_edit.setFont(self._mono_font())
        self.end_gcode_edit.setPlaceholderText(DEFAULT_END_GCODE)
        self.end_gcode_edit.setMinimumHeight(120)
        import_end_btn = QPushButton("Import from file…")
        import_end_btn.clicked.connect(lambda: self._import_gcode(self.end_gcode_edit))
        eg_layout.addWidget(self.end_gcode_edit)
        eg_layout.addWidget(import_end_btn)
        gcode_layout.addWidget(end_group)

        tabs.addTab(gcode_tab, "GCode Templates")

        # ── Output tab ───────────────────────────────────────────────────────
        output_tab = QWidget()
        out_layout = QFormLayout(output_tab)
        self.output_dir_edit = QLineEdit()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output)
        row = QHBoxLayout()
        row.addWidget(self.output_dir_edit)
        row.addWidget(browse_btn)
        out_layout.addRow("Output directory:", row)
        tabs.addTab(output_tab, "Output")

        # ── Buttons ──────────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _mono_font(self):
        from PyQt6.QtGui import QFont
        f = QFont("Menlo")
        if not f.exactMatch():
            f = QFont("Courier New")
        f.setPointSize(11)
        return f

    def _populate(self):
        self.ip_edit.setText(self._settings.get("printer_ip", "192.168.1.65"))
        self.port_spin.setValue(self._settings.get("printer_port", 80))
        self.start_gcode_edit.setPlainText(self._settings.get("start_gcode", ""))
        self.end_gcode_edit.setPlainText(self._settings.get("end_gcode", ""))
        self.output_dir_edit.setText(self._settings.get("output_dir", ""))

    def _test_connection(self):
        ip = self.ip_edit.text().strip() or "192.168.1.65"
        port = self.port_spin.value()
        import sys; sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from klipper.moonraker import MoonrakerClient
        client = MoonrakerClient(ip, port)
        ok = client.check_connection()
        if ok:
            QMessageBox.information(self, "Connection", f"Connected to {ip}:{port}")
        else:
            QMessageBox.warning(self, "Connection Failed", f"Cannot reach {ip}:{port}\nCheck IP and that Moonraker is running.")

    def _import_gcode(self, target: QTextEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import GCode file", "", "GCode (*.gcode *.txt);;All (*)"
        )
        if path:
            text = Path(path).read_text(errors="ignore")
            target.setPlainText(text)

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self.output_dir_edit.setText(d)

    def _save_and_accept(self):
        self._settings["printer_ip"] = self.ip_edit.text().strip() or "192.168.1.65"
        self._settings["printer_port"] = self.port_spin.value()
        self._settings["start_gcode"] = self.start_gcode_edit.toPlainText()
        self._settings["end_gcode"] = self.end_gcode_edit.toPlainText()
        self._settings["output_dir"] = self.output_dir_edit.text().strip()
        save_app_settings(self._settings)
        self.accept()

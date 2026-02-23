"""
App Settings dialog — printer profiles, start/end GCode, output dir.

Each printer profile stores firmware type, bed dimensions, connection
details, and GCode templates.  Nozzle size, temperatures and print speeds
are NOT part of a profile (they stay in the slicer settings panel).
"""

import json
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QPushButton, QLabel, QGroupBox,
    QFileDialog, QTabWidget, QWidget, QSpinBox, QDialogButtonBox,
    QMessageBox, QComboBox, QDoubleSpinBox, QInputDialog,
)
from PyQt6.QtCore import Qt, QLocale
from PyQt6.QtGui import QFont

SETTINGS_PATH = Path(__file__).parent.parent.parent / "data" / "app_settings.json"

# ── Firmware-specific default GCode ──────────────────────────────────────────

_DEFAULT_START = {
    "klipper": (
        "SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=bed_temp VALUE={bed_temp}\n"
        "SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=extruder_temp VALUE={nozzle_temp}\n"
        "M106 S{fan_speed}\n"
        "START_PRINT"
    ),
    "marlin": (
        "M140 S{bed_temp}      ; Set bed temp (no wait)\n"
        "M104 S{nozzle_temp}   ; Set hotend temp (no wait)\n"
        "M190 S{bed_temp}      ; Wait for bed temp\n"
        "M109 S{nozzle_temp}   ; Wait for hotend temp\n"
        "G28                   ; Home all axes\n"
        "G29                   ; Auto bed leveling\n"
        "M106 S{fan_speed}     ; Fan speed\n"
        "G92 E0                ; Reset extruder"
    ),
    "rrf": (
        "M140 S{bed_temp}      ; Set bed temp\n"
        "M109 S{nozzle_temp}   ; Wait for hotend temp\n"
        "M190 S{bed_temp}      ; Wait for bed temp\n"
        "G28                   ; Home all axes\n"
        "M106 S{fan_speed}     ; Fan speed\n"
        "G92 E0                ; Reset extruder"
    ),
}

_DEFAULT_END = {
    "klipper": (
        "G10\n"
        "G1 Z{raise_z} F{travel_f}\n"
        "G1 X{safe_x} Y{safe_y} F{travel_f}\n"
        "END_PRINT"
    ),
    "marlin": (
        "G1 E-2.0 F2400        ; Retract\n"
        "G1 Z{raise_z} F{travel_f}\n"
        "G1 X{safe_x} Y{safe_y} F{travel_f}\n"
        "M104 S0               ; Hotend off\n"
        "M140 S0               ; Bed off\n"
        "M84                   ; Motors off"
    ),
    "rrf": (
        "G1 E-2.0 F2400        ; Retract\n"
        "G1 Z{raise_z} F{travel_f}\n"
        "G1 X{safe_x} Y{safe_y} F{travel_f}\n"
        "M104 S0               ; Hotend off\n"
        "M140 S0               ; Bed off\n"
        "M84                   ; Motors off"
    ),
}

# Default profile used when no profiles exist yet
_BUILTIN_PROFILE = {
    "firmware": "klipper",
    "bed_x": 220,
    "bed_y": 220,
    "max_z": 280,
    "origin": "front_left",
    "kinematics": "cartesian",
    "retract_dist": 0.8,
    "retract_speed": 40.0,
    "printer_ip": "192.168.1.65",
    "printer_port": 80,
    "start_gcode": "",
    "end_gcode": "",
}


# ── Persistence helpers ───────────────────────────────────────────────────────

def load_app_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return _default_settings()


def _default_settings() -> dict:
    return {
        "output_dir": str(Path(__file__).parent.parent.parent / "output"),
        "active_profile": "Default",
        "printer_profiles": {"Default": dict(_BUILTIN_PROFILE)},
    }


def save_app_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def get_active_profile(settings: dict) -> dict:
    """Return the active printer profile dict (always a copy)."""
    profiles = settings.get("printer_profiles", {})
    name = settings.get("active_profile", "")
    return dict(profiles.get(name, _BUILTIN_PROFILE))


# ── Dialog ────────────────────────────────────────────────────────────────────

class AppSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("App Settings")
        self.setMinimumSize(680, 620)
        self._settings = load_app_settings()
        self._ensure_profiles()
        self._build_ui()
        self._load_profile_list()
        self._populate_active_profile()

    def _ensure_profiles(self):
        """Make sure we always have at least one profile."""
        if "printer_profiles" not in self._settings:
            self._settings["printer_profiles"] = {"Default": dict(_BUILTIN_PROFILE)}
        if not self._settings.get("active_profile") or \
                self._settings["active_profile"] not in self._settings["printer_profiles"]:
            self._settings["active_profile"] = next(iter(self._settings["printer_profiles"]))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        tabs.addTab(self._build_profiles_tab(), "Printer Profiles")
        tabs.addTab(self._build_output_tab(),   "Output")

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _build_profiles_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setSpacing(8)

        # ── Profile selector bar ──────────────────────────────────────────
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Profile:"))
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(180)
        self._profile_combo.currentTextChanged.connect(self._on_profile_selected)
        bar.addWidget(self._profile_combo, 1)

        new_btn = QPushButton("New…")
        new_btn.setMaximumHeight(26)
        new_btn.setToolTip("Create a new printer profile")
        new_btn.clicked.connect(self._new_profile)
        bar.addWidget(new_btn)

        ren_btn = QPushButton("Rename…")
        ren_btn.setMaximumHeight(26)
        ren_btn.clicked.connect(self._rename_profile)
        bar.addWidget(ren_btn)

        del_btn = QPushButton("Delete")
        del_btn.setMaximumHeight(26)
        del_btn.setStyleSheet("color: #c66;")
        del_btn.clicked.connect(self._delete_profile)
        bar.addWidget(del_btn)

        v.addLayout(bar)

        # ── Connection ────────────────────────────────────────────────────
        conn_g = QGroupBox("Connection")
        conn_f = QFormLayout(conn_g)
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("192.168.1.65")
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(80)
        conn_f.addRow("Printer IP:", self._ip_edit)
        conn_f.addRow("Port:", self._port_spin)
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_connection)
        conn_f.addRow("", test_btn)
        v.addWidget(conn_g)

        # ── Firmware & Hardware ───────────────────────────────────────────
        hw_g = QGroupBox("Firmware & Hardware")
        hw_f = QFormLayout(hw_g)

        self._firmware_combo = QComboBox()
        self._firmware_combo.addItems(["Klipper", "Marlin", "RRF (RepRapFirmware)"])
        self._firmware_combo.setToolTip(
            "Klipper: uses START_PRINT/END_PRINT macros, G10/G11 firmware retract\n"
            "Marlin: explicit M109/M190 heat, G28 home, G1 E for retract\n"
            "RRF: RepRapFirmware — similar to Marlin"
        )
        self._firmware_combo.currentIndexChanged.connect(self._on_firmware_changed)
        hw_f.addRow("Firmware:", self._firmware_combo)

        self._bed_x_spin = self._make_int_spin(50, 2000, 220)
        self._bed_y_spin = self._make_int_spin(50, 2000, 220)
        self._max_z_spin = self._make_int_spin(50, 2000, 280)
        hw_f.addRow("Bed X (mm):", self._bed_x_spin)
        hw_f.addRow("Bed Y (mm):", self._bed_y_spin)
        hw_f.addRow("Max Z (mm):", self._max_z_spin)

        self._origin_combo = QComboBox()
        self._origin_combo.addItems(["Front-Left Corner", "Center (delta / RRF)"])
        hw_f.addRow("Origin (0,0):", self._origin_combo)

        self._kinematics_combo = QComboBox()
        self._kinematics_combo.addItems(["Cartesian", "CoreXY", "Delta"])
        hw_f.addRow("Kinematics:", self._kinematics_combo)

        self._retract_dist_spin = self._make_dbl_spin(0.1, 10.0, 0.8, 0.1)
        self._retract_speed_spin = self._make_dbl_spin(1.0, 120.0, 40.0, 5.0)
        self._retract_dist_spin.setToolTip(
            "Retract distance in mm.\nKlipper firmware retract is configured in printer.cfg;\n"
            "this value is used for Marlin/RRF explicit retract commands."
        )
        self._retract_speed_spin.setToolTip("Retract/unretract speed in mm/s")
        hw_f.addRow("Retract dist (mm):", self._retract_dist_spin)
        hw_f.addRow("Retract speed (mm/s):", self._retract_speed_spin)
        v.addWidget(hw_g)

        # ── GCode templates ───────────────────────────────────────────────
        gc_g = QGroupBox("GCode Templates  (leave blank to use firmware defaults)")
        gc_v = QVBoxLayout(gc_g)

        gc_v.addWidget(QLabel("Start GCode  — placeholders: {bed_temp}, {nozzle_temp}, {fan_speed}"))
        self._start_edit = QTextEdit()
        self._start_edit.setFont(self._mono_font())
        self._start_edit.setMinimumHeight(110)
        self._start_edit.setMaximumHeight(150)
        gc_v.addWidget(self._start_edit)

        imp_start = QPushButton("Import from file…")
        imp_start.clicked.connect(lambda: self._import_gcode(self._start_edit))
        gc_v.addWidget(imp_start)

        gc_v.addWidget(QLabel("End GCode"))
        self._end_edit = QTextEdit()
        self._end_edit.setFont(self._mono_font())
        self._end_edit.setMinimumHeight(90)
        self._end_edit.setMaximumHeight(130)
        gc_v.addWidget(self._end_edit)

        imp_end = QPushButton("Import from file…")
        imp_end.clicked.connect(lambda: self._import_gcode(self._end_edit))
        gc_v.addWidget(imp_end)
        v.addWidget(gc_g)

        return tab

    def _build_output_tab(self) -> QWidget:
        tab = QWidget()
        f = QFormLayout(tab)
        self._output_dir_edit = QLineEdit()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output)
        row = QHBoxLayout()
        row.addWidget(self._output_dir_edit)
        row.addWidget(browse_btn)
        f.addRow("Output directory:", row)
        return tab

    # ── Widget factories ──────────────────────────────────────────────────────

    @staticmethod
    def _make_int_spin(lo, hi, default) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(default)
        return s

    @staticmethod
    def _make_dbl_spin(lo, hi, default, step) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(2)
        s.setValue(default)
        return s

    def _mono_font(self) -> QFont:
        f = QFont("Menlo")
        if not f.exactMatch():
            f = QFont("Courier New")
        f.setPointSize(10)
        return f

    # ── Profile management ────────────────────────────────────────────────────

    def _load_profile_list(self):
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        for name in self._settings.get("printer_profiles", {}):
            self._profile_combo.addItem(name)
        active = self._settings.get("active_profile", "")
        idx = self._profile_combo.findText(active)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)
        self._profile_combo.blockSignals(False)

    def _populate_active_profile(self):
        name = self._profile_combo.currentText()
        p = self._settings.get("printer_profiles", {}).get(name, _BUILTIN_PROFILE)
        self._populate_fields(p)

    def _populate_fields(self, p: dict):
        self._ip_edit.setText(p.get("printer_ip", "192.168.1.65"))
        self._port_spin.setValue(p.get("printer_port", 80))

        fw = p.get("firmware", "klipper").lower()
        fw_map = {"klipper": 0, "marlin": 1, "rrf": 2}
        self._firmware_combo.blockSignals(True)
        self._firmware_combo.setCurrentIndex(fw_map.get(fw, 0))
        self._firmware_combo.blockSignals(False)

        self._bed_x_spin.setValue(int(p.get("bed_x", 220)))
        self._bed_y_spin.setValue(int(p.get("bed_y", 220)))
        self._max_z_spin.setValue(int(p.get("max_z", 280)))

        orig_map = {"front_left": 0, "center": 1}
        self._origin_combo.setCurrentIndex(orig_map.get(p.get("origin", "front_left"), 0))

        kin_map = {"cartesian": 0, "corexy": 1, "delta": 2}
        self._kinematics_combo.setCurrentIndex(kin_map.get(p.get("kinematics", "cartesian"), 0))

        self._retract_dist_spin.setValue(float(p.get("retract_dist", 0.8)))
        self._retract_speed_spin.setValue(float(p.get("retract_speed", 40.0)))

        self._start_edit.setPlainText(p.get("start_gcode", ""))
        self._end_edit.setPlainText(p.get("end_gcode", ""))

        # Update placeholder text for the active firmware
        self._update_gcode_placeholders(p.get("firmware", "klipper"))

        self._output_dir_edit.setText(self._settings.get("output_dir", ""))

    def _fields_to_profile(self) -> dict:
        fw_idx_map = {0: "klipper", 1: "marlin", 2: "rrf"}
        orig_map = {0: "front_left", 1: "center"}
        kin_map = {0: "cartesian", 1: "corexy", 2: "delta"}
        return {
            "firmware":     fw_idx_map[self._firmware_combo.currentIndex()],
            "bed_x":        self._bed_x_spin.value(),
            "bed_y":        self._bed_y_spin.value(),
            "max_z":        self._max_z_spin.value(),
            "origin":       orig_map[self._origin_combo.currentIndex()],
            "kinematics":   kin_map[self._kinematics_combo.currentIndex()],
            "retract_dist": self._retract_dist_spin.value(),
            "retract_speed":self._retract_speed_spin.value(),
            "printer_ip":   self._ip_edit.text().strip() or "192.168.1.65",
            "printer_port": self._port_spin.value(),
            "start_gcode":  self._start_edit.toPlainText(),
            "end_gcode":    self._end_edit.toPlainText(),
        }

    def _on_profile_selected(self, name: str):
        p = self._settings.get("printer_profiles", {}).get(name, _BUILTIN_PROFILE)
        self._populate_fields(p)

    def _on_firmware_changed(self, _idx: int):
        fw = {0: "klipper", 1: "marlin", 2: "rrf"}[self._firmware_combo.currentIndex()]
        self._update_gcode_placeholders(fw)

    def _update_gcode_placeholders(self, firmware: str):
        key = firmware.lower().split()[0]  # "rrf (reprapfirmware)" → "rrf"
        self._start_edit.setPlaceholderText(
            _DEFAULT_START.get(key, _DEFAULT_START["klipper"])
        )
        self._end_edit.setPlaceholderText(
            _DEFAULT_END.get(key, _DEFAULT_END["klipper"])
        )

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        profiles = self._settings.setdefault("printer_profiles", {})
        if name in profiles:
            QMessageBox.warning(self, "Exists", f"Profile '{name}' already exists.")
            return
        # Clone currently shown fields as the new profile
        profiles[name] = self._fields_to_profile()
        self._load_profile_list()
        idx = self._profile_combo.findText(name)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)

    def _rename_profile(self):
        old = self._profile_combo.currentText()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename Profile", "New name:", text=old)
        if not ok or not new.strip() or new.strip() == old:
            return
        new = new.strip()
        profiles = self._settings.get("printer_profiles", {})
        if new in profiles:
            QMessageBox.warning(self, "Exists", f"Profile '{new}' already exists.")
            return
        profiles[new] = profiles.pop(old)
        if self._settings.get("active_profile") == old:
            self._settings["active_profile"] = new
        self._load_profile_list()
        idx = self._profile_combo.findText(new)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)

    def _delete_profile(self):
        name = self._profile_combo.currentText()
        profiles = self._settings.get("printer_profiles", {})
        if len(profiles) <= 1:
            QMessageBox.information(self, "Cannot Delete", "You must have at least one profile.")
            return
        reply = QMessageBox.question(
            self, "Delete Profile", f"Delete profile '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        profiles.pop(name, None)
        if self._settings.get("active_profile") == name:
            self._settings["active_profile"] = next(iter(profiles))
        self._load_profile_list()

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def _test_connection(self):
        ip = self._ip_edit.text().strip() or "192.168.1.65"
        port = self._port_spin.value()
        import sys; sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        try:
            from klipper.moonraker import MoonrakerClient
            client = MoonrakerClient(ip, port)
            ok = client.check_connection()
        except Exception:
            ok = False
        if ok:
            QMessageBox.information(self, "Connection", f"Connected to {ip}:{port}")
        else:
            QMessageBox.warning(self, "Connection Failed",
                                f"Cannot reach {ip}:{port}\nCheck IP and that Moonraker is running.")

    def _import_gcode(self, target: QTextEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import GCode file", "", "GCode (*.gcode *.txt);;All (*)"
        )
        if path:
            target.setPlainText(Path(path).read_text(errors="ignore"))

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self._output_dir_edit.setText(d)

    # ── Save & accept ─────────────────────────────────────────────────────────

    def _save_and_accept(self):
        # Save currently-shown fields back into the active profile
        name = self._profile_combo.currentText()
        if name:
            self._settings.setdefault("printer_profiles", {})[name] = self._fields_to_profile()
            self._settings["active_profile"] = name

        self._settings["output_dir"] = self._output_dir_edit.text().strip()
        save_app_settings(self._settings)
        self.accept()

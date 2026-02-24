"""
First-run setup wizard — walks through essential configuration in 4 steps:
  1. Welcome
  2. STL files directory
  3. Printer connection (IP / port / test)
  4. Printer hardware (firmware, bed size, temperatures)

Saves directly into the same app_settings.json / active profile that the rest
of the app reads, so no migration is needed.
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QComboBox, QSpinBox,
    QDoubleSpinBox, QFileDialog, QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QLocale
from PyQt6.QtGui import QFont, QPixmap, QPainter, QColor, QLinearGradient, QPen

from gui.dialogs.app_settings import (
    load_app_settings, save_app_settings, get_active_profile,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _label(text: str, bold: bool = False, color: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    style = ""
    if bold:
        style += "font-weight: bold;"
    if color:
        style += f" color: {color};"
    if style:
        lbl.setStyleSheet(style)
    return lbl


def _heading(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    f = lbl.font()
    f.setPointSize(16)
    f.setWeight(QFont.Weight.Bold)
    lbl.setFont(f)
    lbl.setStyleSheet("color: #7ed4f7; margin-bottom: 4px;")
    return lbl


def _mono_spin(lo, hi, default, step=1.0, decimals=2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setValue(default)
    s.setMinimumWidth(100)
    return s


# ── Page 1: Welcome ───────────────────────────────────────────────────────────

class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("")
        v = QVBoxLayout(self)
        v.setSpacing(14)

        v.addSpacing(10)
        v.addWidget(_heading("Welcome to FerroSlicer"))
        v.addWidget(_label(
            "This short setup guide will configure FerroSlicer for your printer in under a minute.\n\n"
            "You'll set up:\n"
            "  •  Where your STL files are stored\n"
            "  •  Your printer's network address (Klipper / Moonraker)\n"
            "  •  Printer hardware — bed size, firmware, temperatures\n\n"
            "All settings can be changed later via  <b>Edit → App Settings</b>.",
            color="#ccd",
        ))
        v.addStretch()
        v.addWidget(_label("Click  Next  to begin.", color="#778"))


# ── Page 2: STL directory ─────────────────────────────────────────────────────

class STLDirPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("STL Files Directory")

        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.addWidget(_label(
            "Choose the folder where you keep your STL models. "
            "The built-in file browser will open here every time you start FerroSlicer.",
            color="#ccd",
        ))

        row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText(str(Path.home() / "Documents" / "STL Models"))
        self._dir_edit.setMinimumWidth(320)
        browse = QPushButton("Browse…")
        browse.setFixedHeight(30)
        browse.clicked.connect(self._browse)
        row.addWidget(self._dir_edit, 1)
        row.addWidget(browse)
        v.addLayout(row)

        v.addWidget(_label(
            "Tip: you can leave this blank to use your home folder, "
            "and change it anytime in App Settings.",
            color="#778",
        ))
        v.addStretch()

        # Register field so the wizard can read it
        self.registerField("stl_dir", self._dir_edit)

    def _browse(self):
        start = self._dir_edit.text().strip() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select STL files directory", start)
        if d:
            self._dir_edit.setText(d)


# ── Page 3: Printer connection ────────────────────────────────────────────────

class ConnectionPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Printer Connection")

        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.addWidget(_label(
            "Enter your Klipper printer's IP address and port.\n"
            "FerroSlicer uses the Moonraker API to upload and start prints.",
            color="#ccd",
        ))

        grp = QGroupBox("Moonraker / Klipper")
        f = QFormLayout(grp)

        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("192.168.1.100")
        self._ip_edit.setMinimumWidth(200)
        f.addRow("Printer IP:", self._ip_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(80)
        self._port_spin.setMinimumWidth(90)
        f.addRow("Port:", self._port_spin)

        f.addRow("", _label(
            "Port 80 is standard for Fluidd/Mainsail behind nginx. "
            "Direct Moonraker is usually 7125.",
            color="#778",
        ))

        self._test_btn = QPushButton("Test Connection")
        self._test_btn.setFixedHeight(30)
        self._test_btn.clicked.connect(self._test)
        f.addRow("", self._test_btn)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        f.addRow("", self._status_lbl)

        v.addWidget(grp)

        v.addWidget(_label(
            "No Klipper printer?  Leave the IP blank and skip this step — "
            "you can still generate and export GCode files.",
            color="#778",
        ))
        v.addStretch()

        self.registerField("printer_ip", self._ip_edit)
        self.registerField("printer_port*", self._port_spin,
                           property="value", changedSignal=self._port_spin.valueChanged)

    def _test(self):
        ip   = self._ip_edit.text().strip()
        port = self._port_spin.value()
        if not ip:
            self._status_lbl.setStyleSheet("color: #f0a500;")
            self._status_lbl.setText("Enter an IP address first.")
            return
        self._status_lbl.setStyleSheet("color: #778;")
        self._status_lbl.setText("Testing…")
        self._test_btn.setEnabled(False)
        try:
            from klipper.moonraker import MoonrakerClient
            ok = MoonrakerClient(ip, port).check_connection()
        except Exception:
            ok = False
        finally:
            self._test_btn.setEnabled(True)
        if ok:
            self._status_lbl.setStyleSheet("color: #2ecc71;")
            self._status_lbl.setText(f"Connected to {ip}:{port}")
        else:
            self._status_lbl.setStyleSheet("color: #e74c3c;")
            self._status_lbl.setText(f"Cannot reach {ip}:{port} — check IP and Moonraker.")

    def isComplete(self) -> bool:
        return True   # IP is optional


# ── Page 4: Printer hardware ──────────────────────────────────────────────────

class HardwarePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Printer Hardware")

        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.addWidget(_label(
            "Configure your printer's physical specs. These are used to position "
            "prints correctly and generate the right start/end GCode.",
            color="#ccd",
        ))

        f = QFormLayout()
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

        # Firmware
        self._firmware = QComboBox()
        self._firmware.addItems(["Klipper", "Marlin", "RRF (RepRapFirmware)"])
        self._firmware.setMinimumWidth(200)
        self._firmware.setToolTip(
            "Klipper: START_PRINT/END_PRINT macros, G10/G11 retract\n"
            "Marlin: M109/M190 heat-and-wait, G28 home, explicit E retract\n"
            "RRF: RepRapFirmware — similar to Marlin"
        )
        f.addRow("Firmware:", self._firmware)

        # Bed
        self._bed_x = QSpinBox(); self._bed_x.setRange(50, 2000); self._bed_x.setValue(220)
        self._bed_y = QSpinBox(); self._bed_y.setRange(50, 2000); self._bed_y.setValue(220)
        self._max_z = QSpinBox(); self._max_z.setRange(50, 2000); self._max_z.setValue(280)
        f.addRow("Bed X (mm):", self._bed_x)
        f.addRow("Bed Y (mm):", self._bed_y)
        f.addRow("Max Z (mm):", self._max_z)

        # Kinematics
        self._kinematics = QComboBox()
        self._kinematics.addItems(["Cartesian", "CoreXY", "Delta"])
        self._kinematics.setMinimumWidth(180)
        f.addRow("Kinematics:", self._kinematics)

        # Nozzle diameter
        self._nozzle = _mono_spin(0.1, 2.0, 1.0, 0.1, 2)
        self._nozzle.setToolTip("Common: 0.4 mm standard, 0.6 mm, 1.0 mm volcano")
        f.addRow("Nozzle diameter (mm):", self._nozzle)

        # Filament
        self._filament = _mono_spin(1.0, 3.5, 1.75, 0.05, 2)
        f.addRow("Filament diameter (mm):", self._filament)

        # Temperatures
        self._nozzle_temp = QSpinBox(); self._nozzle_temp.setRange(150, 320); self._nozzle_temp.setValue(260)
        self._bed_temp    = QSpinBox(); self._bed_temp.setRange(0, 120);     self._bed_temp.setValue(65)
        self._nozzle_temp.setSuffix(" °C")
        self._bed_temp.setSuffix(" °C")
        f.addRow("Nozzle temperature:", self._nozzle_temp)
        f.addRow("Bed temperature:", self._bed_temp)

        v.addLayout(f)
        v.addStretch()

    def get_profile_fields(self) -> dict:
        fw_map = {0: "klipper", 1: "marlin", 2: "rrf"}
        kin_map = {0: "cartesian", 1: "corexy", 2: "delta"}
        return {
            "firmware":          fw_map[self._firmware.currentIndex()],
            "bed_x":             self._bed_x.value(),
            "bed_y":             self._bed_y.value(),
            "max_z":             self._max_z.value(),
            "kinematics":        kin_map[self._kinematics.currentIndex()],
            "nozzle_diameter":   self._nozzle.value(),
            "filament_diameter": self._filament.value(),
            "nozzle_temp":       self._nozzle_temp.value(),
            "bed_temp":          self._bed_temp.value(),
        }


# ── Page 5: Done ──────────────────────────────────────────────────────────────

class DonePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("")
        v = QVBoxLayout(self)
        v.setSpacing(14)
        v.addSpacing(10)
        v.addWidget(_heading("You're all set!"))
        v.addWidget(_label(
            "FerroSlicer is configured and ready to use.\n\n"
            "  •  Drop an STL into the file browser on the left\n"
            "  •  Adjust wave and print settings in the panel on the right\n"
            "  •  Click  Generate GCode  — then  Send to Printer\n\n"
            "All settings are saved and can be changed anytime via  Edit → App Settings.",
            color="#ccd",
        ))
        v.addStretch()
        v.addWidget(_label("Click  Finish  to start.", color="#778"))


# ── Wizard ────────────────────────────────────────────────────────────────────

class SetupWizard(QWizard):
    """
    Multi-page first-run wizard.

    Usage:
        wiz = SetupWizard(parent)
        if wiz.exec():
            ...   # settings already saved inside the wizard
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FerroSlicer — First-Time Setup")
        self.setMinimumSize(560, 440)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.setStyleSheet("""
            QWizard        { background: #12181f; color: #ccd; }
            QWizardPage    { background: #12181f; }
            QLabel         { color: #ccd; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: #1a2535; color: #dde; border: 1px solid #2a3e55;
                border-radius: 3px; padding: 3px 6px; min-height: 24px;
            }
            QGroupBox      { color: #8ab; border: 1px solid #2a3e55; border-radius: 4px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #8ab; }
            QPushButton    { background: #1e3a52; color: #cde; border: 1px solid #2a5070;
                             border-radius: 3px; padding: 4px 12px; }
            QPushButton:hover { background: #284d6e; }
            QPushButton#qt_wizard_nextbutton,
            QPushButton#qt_wizard_finishbutton {
                background: #2a5298; color: white; font-weight: bold;
                border: none; min-width: 80px;
            }
            QPushButton#qt_wizard_nextbutton:hover,
            QPushButton#qt_wizard_finishbutton:hover { background: #3a62a8; }
        """)

        self._page_stl     = STLDirPage()
        self._page_conn    = ConnectionPage()
        self._page_hw      = HardwarePage()

        self.addPage(WelcomePage())
        self.addPage(self._page_stl)
        self.addPage(self._page_conn)
        self.addPage(self._page_hw)
        self.addPage(DonePage())

    def accept(self):
        """Persist everything before closing."""
        settings = load_app_settings()

        # STL dir
        stl_dir = self.field("stl_dir") or ""
        settings["stl_dir"] = stl_dir.strip()

        # Printer connection → active profile
        ip   = self.field("printer_ip") or ""
        port = self.field("printer_port") or 80
        profile_data = self._page_hw.get_profile_fields()
        profile_data["printer_ip"]   = ip.strip()
        profile_data["printer_port"] = int(port)
        # Keep existing gcode templates
        existing = get_active_profile(settings)
        profile_data.setdefault("start_gcode", existing.get("start_gcode", ""))
        profile_data.setdefault("end_gcode",   existing.get("end_gcode", ""))
        profile_data.setdefault("retract_dist",  existing.get("retract_dist", 0.8))
        profile_data.setdefault("retract_speed", existing.get("retract_speed", 40.0))
        profile_data.setdefault("origin",        existing.get("origin", "front_left"))

        active_name = settings.get("active_profile", "Default")
        settings.setdefault("printer_profiles", {})[active_name] = profile_data

        # Mark setup complete so we never show this again
        settings["setup_complete"] = True

        save_app_settings(settings)
        super().accept()


# ── Public helper ─────────────────────────────────────────────────────────────

def run_if_needed(parent=None) -> bool:
    """Show the wizard if this is the first launch. Returns True if it ran."""
    settings = load_app_settings()
    if settings.get("setup_complete"):
        return False
    wiz = SetupWizard(parent)
    wiz.exec()
    return True

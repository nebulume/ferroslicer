"""
Settings panel — all slicer parameters as Qt widgets.
Mirrors every CLI flag from __main__.py.
"""

from PyQt6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QSlider, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal


class SettingsPanel(QScrollArea):
    """
    Scrollable panel containing all slicing settings.
    Emit `settings_changed` when any value changes.
    """

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(260)
        self.setMaximumWidth(340)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        self._widgets = {}   # key → widget
        self._building = True

        self._add_printer_group(layout)
        self._add_print_group(layout)
        self._add_mode_group(layout)
        self._add_wave_group(layout)
        self._add_base_group(layout)
        self._add_skirt_group(layout)

        layout.addStretch()
        self._building = False

    # ── Group builders ───────────────────────────────────────────────────────

    def _add_printer_group(self, parent):
        g = QGroupBox("Printer")
        f = QFormLayout(g)
        self._dbl(f, "nozzle_diameter",   "Nozzle (mm):",    1.0,  0.1, 2.0,  0.1)
        self._int(f, "nozzle_temp",       "Nozzle temp (°C):",260, 150, 350)
        self._int(f, "bed_temp",          "Bed temp (°C):",    65,   0, 130)
        parent.addWidget(g)

    def _add_print_group(self, parent):
        g = QGroupBox("Print Settings")
        f = QFormLayout(g)
        self._dbl(f, "layer_height",      "Layer height (mm):", 0.5, 0.05, 2.0,   0.05)
        self._int(f, "print_speed",       "Print speed (mm/s):", 35,    5, 300)
        self._int(f, "travel_speed",      "Travel speed (mm/s):",40,   10, 500)
        self._int(f, "fan_speed",         "Fan speed (%):",      25,    0, 100)
        self._dbl(f, "max_volumetric_speed","Max vol. (mm³/s):", 12.0, 0.5, 50.0, 0.5)
        parent.addWidget(g)

    def _add_mode_group(self, parent):
        g = QGroupBox("Printing Mode")
        f = QFormLayout(g)

        cb = QComboBox()
        cb.addItems(["Spiral Vase (continuous)", "Layer Mesh"])
        cb.currentIndexChanged.connect(self._on_mode_change)
        self._widgets["vase_mode"] = cb
        cb.currentIndexChanged.connect(self._emit)
        f.addRow("Mode:", cb)

        dbl = self._dbl(f, "spiral_points_per_degree", "Spiral res (pts/°):", 1.2, 0.1, 5.0, 0.1)
        self._spiral_row = (f, dbl)

        parent.addWidget(g)

    def _add_wave_group(self, parent):
        g = QGroupBox("Wave Pattern")
        f = QFormLayout(g)

        self._dbl(f, "wave_amplitude",    "Amplitude (mm):",   2.0, 0.0, 20.0, 0.1)

        # Wave frequency: count OR spacing
        freq_cb = QComboBox()
        freq_cb.addItems(["Per revolution (count)", "Per distance (spacing mm)"])
        freq_cb.currentIndexChanged.connect(self._on_freq_mode_change)
        self._widgets["wave_freq_mode"] = freq_cb
        freq_cb.currentIndexChanged.connect(self._emit)
        f.addRow("Frequency mode:", freq_cb)

        self._wave_count_spin = self._int(f, "wave_count",   "Waves / revolution:", 120, 1, 2000)
        self._wave_spacing_spin = self._dbl(f, "wave_spacing", "Wave spacing (mm):", 4.0, 0.1, 50.0, 0.1)
        # Show count by default, hide spacing
        self._wave_spacing_spin.setVisible(False)
        # Find label for spacing row
        self._spacing_label = f.labelForField(self._wave_spacing_spin)
        if self._spacing_label:
            self._spacing_label.setVisible(False)

        ptn = QComboBox()
        ptn.addItems(["sine", "triangular", "sawtooth"])
        ptn.currentIndexChanged.connect(self._emit)
        self._widgets["wave_pattern"] = ptn
        f.addRow("Pattern:", ptn)

        self._int(f,  "wave_smoothness",   "Smoothness (1-10):",  10,  1, 10)
        self._int(f,  "layer_alternation", "Alternation (rev):",   2,  1, 20)
        self._dbl(f,  "phase_offset",      "Phase offset (%):",   50,  0, 100, 1.0)
        self._dbl(f,  "seam_shift",        "Seam shift (waves):", 0.0, 0.0, 10.0, 0.1)

        parent.addWidget(g)

    def _add_base_group(self, parent):
        g = QGroupBox("Base Integrity")
        f = QFormLayout(g)
        self._dbl(f, "base_height",    "Base height (mm):", 28.0, 0.0, 200.0, 0.5)

        bm = QComboBox()
        bm.addItems(["fewer_gaps", "tighter_waves", "solid_then_mesh"])
        bm.currentIndexChanged.connect(self._emit)
        self._widgets["base_mode"] = bm
        f.addRow("Base mode:", bm)

        bt = QComboBox()
        bt.addItems(["exponential", "linear", "step"])
        bt.currentIndexChanged.connect(self._emit)
        self._widgets["base_transition"] = bt
        f.addRow("Transition:", bt)

        parent.addWidget(g)

    def _add_skirt_group(self, parent):
        g = QGroupBox("Skirt / Adhesion")
        f = QFormLayout(g)

        chk = QCheckBox("Enable skirt")
        chk.setChecked(True)
        chk.stateChanged.connect(self._emit)
        self._widgets["skirt_enabled"] = chk
        f.addRow("", chk)

        self._dbl(f, "skirt_distance", "Skirt gap (mm):",  0.0, 0.0, 20.0, 0.5)
        self._int(f, "skirt_height",   "Skirt layers:",      1,   1,   10)

        parent.addWidget(g)

    # ── Widget factories ─────────────────────────────────────────────────────

    def _dbl(self, form, key, label, default, lo, hi, step) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(len(str(step).split(".")[-1]) if "." in str(step) else 1)
        spin.setValue(default)
        spin.valueChanged.connect(self._emit)
        self._widgets[key] = spin
        form.addRow(label, spin)
        return spin

    def _int(self, form, key, label, default, lo, hi) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.valueChanged.connect(self._emit)
        self._widgets[key] = spin
        form.addRow(label, spin)
        return spin

    # ── Slots ────────────────────────────────────────────────────────────────

    def _emit(self, *_):
        if not self._building:
            self.settings_changed.emit()

    def _on_mode_change(self, idx):
        """Show/hide spiral-specific settings based on mode selection."""
        is_vase = (idx == 0)
        spiral_spin = self._widgets.get("spiral_points_per_degree")
        if spiral_spin:
            spiral_spin.setVisible(is_vase)

    def _on_freq_mode_change(self, idx):
        """Toggle between wave_count and wave_spacing widgets."""
        per_rev = (idx == 0)
        self._wave_count_spin.setVisible(per_rev)
        self._wave_spacing_spin.setVisible(not per_rev)
        if self._spacing_label:
            self._spacing_label.setVisible(not per_rev)

    # ── Public API ───────────────────────────────────────────────────────────

    def get_config_overrides(self) -> dict:
        """
        Build config override dict matching what MeshVaseSlicer.slice_stl() expects.
        """
        w = self._widgets

        is_vase = w["vase_mode"].currentIndex() == 0
        per_rev = w["wave_freq_mode"].currentIndex() == 0

        mesh = {
            "wave_amplitude":    w["wave_amplitude"].value(),
            "wave_pattern":      w["wave_pattern"].currentText(),
            "wave_smoothness":   w["wave_smoothness"].value(),
            "layer_alternation": w["layer_alternation"].value(),
            "phase_offset":      w["phase_offset"].value(),
            "seam_shift":        w["seam_shift"].value(),
            "base_height":       w["base_height"].value(),
            "base_mode":         w["base_mode"].currentText(),
            "base_transition":   w["base_transition"].currentText(),
        }
        if per_rev:
            mesh["wave_count"] = w["wave_count"].value()
        else:
            mesh["wave_spacing"] = w["wave_spacing"].value()

        print_s = {
            "layer_height":          w["layer_height"].value(),
            "print_speed":           w["print_speed"].value(),
            "travel_speed":          w["travel_speed"].value(),
            "fan_speed":             w["fan_speed"].value(),
            "max_volumetric_speed":  w["max_volumetric_speed"].value(),
            "vase_mode":             is_vase,
            "skirt_enabled":         w["skirt_enabled"].isChecked(),
            "skirt_distance":        w["skirt_distance"].value(),
            "skirt_height":          w["skirt_height"].value(),
        }
        if is_vase:
            print_s["spiral_points_per_degree"] = w["spiral_points_per_degree"].value()

        return {
            "printer": {
                "nozzle_diameter": w["nozzle_diameter"].value(),
                "nozzle_temp":     w["nozzle_temp"].value(),
                "bed_temp":        w["bed_temp"].value(),
            },
            "print_settings": print_s,
            "mesh_settings":  mesh,
        }

    def load_config(self, cfg: dict) -> None:
        """Populate widgets from a config override dict."""
        self._building = True
        try:
            w = self._widgets
            printer = cfg.get("printer", {})
            ps = cfg.get("print_settings", {})
            ms = cfg.get("mesh_settings", {})

            def _set_dbl(key, val):
                if key in w and val is not None:
                    w[key].setValue(float(val))

            def _set_int(key, val):
                if key in w and val is not None:
                    w[key].setValue(int(val))

            def _set_combo(key, val):
                if key in w and val is not None:
                    idx = w[key].findText(str(val))
                    if idx >= 0:
                        w[key].setCurrentIndex(idx)

            _set_dbl("nozzle_diameter",          printer.get("nozzle_diameter"))
            _set_int("nozzle_temp",              printer.get("nozzle_temp"))
            _set_int("bed_temp",                 printer.get("bed_temp"))
            _set_dbl("layer_height",             ps.get("layer_height"))
            _set_int("print_speed",              ps.get("print_speed"))
            _set_int("travel_speed",             ps.get("travel_speed"))
            _set_int("fan_speed",                ps.get("fan_speed"))
            _set_dbl("max_volumetric_speed",     ps.get("max_volumetric_speed"))
            _set_dbl("spiral_points_per_degree", ps.get("spiral_points_per_degree"))

            if ps.get("vase_mode"):
                w["vase_mode"].setCurrentIndex(0)
            else:
                w["vase_mode"].setCurrentIndex(1)

            if ps.get("skirt_enabled") is not None:
                w["skirt_enabled"].setChecked(bool(ps["skirt_enabled"]))
            _set_dbl("skirt_distance", ps.get("skirt_distance"))
            _set_int("skirt_height",   ps.get("skirt_height"))

            _set_dbl("wave_amplitude",   ms.get("wave_amplitude"))
            if "wave_count" in ms and ms["wave_count"] is not None:
                w["wave_freq_mode"].setCurrentIndex(0)
                _set_int("wave_count", ms["wave_count"])
            elif "wave_spacing" in ms:
                w["wave_freq_mode"].setCurrentIndex(1)
                _set_dbl("wave_spacing", ms["wave_spacing"])

            _set_combo("wave_pattern",     ms.get("wave_pattern"))
            _set_int("wave_smoothness",    ms.get("wave_smoothness"))
            _set_int("layer_alternation",  ms.get("layer_alternation"))
            _set_dbl("phase_offset",       ms.get("phase_offset"))
            _set_dbl("seam_shift",         ms.get("seam_shift"))
            _set_dbl("base_height",        ms.get("base_height"))
            _set_combo("base_mode",        ms.get("base_mode"))
            _set_combo("base_transition",  ms.get("base_transition"))
        finally:
            self._building = False

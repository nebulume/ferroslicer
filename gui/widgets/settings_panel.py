"""
Settings panel — all slicer parameters as Qt widgets.
Mirrors every CLI flag from __main__.py.
"""

import json
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QSlider, QSizePolicy, QPushButton, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QLocale

def _user_data_dir() -> Path:
    """Writable directory for user settings — redirects to ~/Documents/FerroSlicer/ when frozen."""
    if getattr(sys, "frozen", False):
        p = Path.home() / "Documents" / "FerroSlicer"
    else:
        p = Path(__file__).parent.parent.parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p

SLICER_SETTINGS_PATH = _user_data_dir() / "slicer_settings.json"
PRESETS_PATH         = _user_data_dir() / "presets.json"


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
        self.setMinimumWidth(300)
        self.setMaximumWidth(420)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        self._widgets = {}   # key → widget
        self._building = True

        # Debounce timer — batches rapid changes into one disk write
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_to_disk)

        self._add_presets_bar(layout)
        self._add_printer_group(layout)
        self._add_motion_group(layout)
        self._add_print_group(layout)
        self._add_seam_ramp_group(layout)
        self._add_mode_group(layout)
        self._add_wave_group(layout)
        self._add_base_group(layout)
        self._add_skirt_group(layout)

        layout.addStretch()
        self._building = False

        # Restore last session's values (silently ignore if file missing/corrupt)
        self._load_from_disk()

    # ── Presets bar ──────────────────────────────────────────────────────────

    def _add_presets_bar(self, parent):
        """Compact preset selector strip at the top of the panel."""
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 4)
        h.setSpacing(4)

        lbl = QLabel("Preset:")
        lbl.setStyleSheet("color: #889; font-size: 11px;")
        h.addWidget(lbl)

        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.setMaximumHeight(26)
        h.addWidget(self._preset_combo)

        save_btn = QPushButton("Save…")
        save_btn.setMinimumWidth(52)
        save_btn.setMaximumHeight(26)
        save_btn.setStyleSheet("font-size: 11px; padding: 0 6px;")
        save_btn.setToolTip("Save current settings as a named preset")
        save_btn.clicked.connect(self._save_preset)
        h.addWidget(save_btn)

        del_btn = QPushButton("Del")
        del_btn.setMinimumWidth(36)
        del_btn.setMaximumHeight(26)
        del_btn.setStyleSheet("font-size: 11px; padding: 0 4px; color: #c66;")
        del_btn.setToolTip("Delete selected preset")
        del_btn.clicked.connect(self._delete_preset)
        h.addWidget(del_btn)

        parent.addWidget(bar)

        # Connect after widgets exist so blockSignals works cleanly
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self._load_presets()

    def _load_presets(self):
        """Populate preset combo from presets.json."""
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("(no preset)")
        if PRESETS_PATH.exists():
            try:
                data = json.loads(PRESETS_PATH.read_text())
                for name in data:
                    self._preset_combo.addItem(name)
            except Exception:
                pass
        self._preset_combo.blockSignals(False)

    def _on_preset_selected(self, idx):
        """Load the selected preset into all widgets."""
        if self._building or idx <= 0:
            return
        name = self._preset_combo.currentText()
        if not PRESETS_PATH.exists():
            return
        try:
            data = json.loads(PRESETS_PATH.read_text())
            cfg = data.get(name)
            if cfg:
                self.load_config(cfg)
        except Exception:
            pass

    def _save_preset(self):
        """Prompt for a name and save current settings as a preset."""
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        data = {}
        if PRESETS_PATH.exists():
            try:
                data = json.loads(PRESETS_PATH.read_text())
            except Exception:
                pass
        data[name] = self.get_config_overrides()
        PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRESETS_PATH.write_text(json.dumps(data, indent=2))
        self._load_presets()
        idx = self._preset_combo.findText(name)
        if idx >= 0:
            self._preset_combo.blockSignals(True)
            self._preset_combo.setCurrentIndex(idx)
            self._preset_combo.blockSignals(False)

    def _delete_preset(self):
        """Delete the currently selected preset."""
        idx = self._preset_combo.currentIndex()
        if idx <= 0:
            return
        name = self._preset_combo.currentText()
        reply = QMessageBox.question(
            self, "Delete Preset",
            f"Delete preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not PRESETS_PATH.exists():
            return
        try:
            data = json.loads(PRESETS_PATH.read_text())
            data.pop(name, None)
            PRESETS_PATH.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
        self._load_presets()

    # ── Group builders ───────────────────────────────────────────────────────

    def _add_printer_group(self, parent):
        g = QGroupBox("Printer")
        f = QFormLayout(g)
        self._dbl(f, "nozzle_diameter", "Nozzle (mm):",      1.0,  0.1,  2.0,  0.1,
                  tip="Diameter of the nozzle tip. Common sizes: 0.4, 0.6, 1.0mm")
        self._int(f, "nozzle_temp",     "Nozzle temp (°C):", 260,  150,  400,
                  tip="Hotend temperature. Typical range: 200–260°C for PLA/PETG")
        self._int(f, "bed_temp",        "Bed temp (°C):",      65,    0,  130,
                  tip="Heated bed temperature. 0 = bed off. Typical: 60–70°C for PLA")
        parent.addWidget(g)

    def _add_motion_group(self, parent):
        g = QGroupBox("Motion")
        f = QFormLayout(g)
        self._int(f, "print_accel",  "Print accel (mm/s²):",  500,  100, 20000,
                  tip="Acceleration during printing moves. Lower = smoother but slower")
        self._int(f, "travel_accel", "Travel accel (mm/s²):", 1500, 100, 30000,
                  tip="Acceleration during travel (non-printing) moves")
        self._dbl(f, "z_hop",        "Z-hop (mm):",            0.0,  0.0,   5.0, 0.1,
                  tip="Lift the nozzle by this amount when travelling to avoid clipping the print.\n0 = disabled")
        parent.addWidget(g)

    def _add_print_group(self, parent):
        g = QGroupBox("Print Settings")
        f = QFormLayout(g)
        self._dbl(f, "layer_height",         "Layer height (mm):",    0.5,  0.05, 2.0,  0.05,
                  tip="Thickness of each printed layer. Smaller = more detail, slower print")
        self._int(f, "print_speed",          "Print speed (mm/s):",    35,     5, 500,
                  tip="Speed for extrusion moves. Higher speeds need higher temperatures and volumetric flow")
        self._int(f, "first_layer_speed_pct","First layer speed (%):", 50,    10, 100,
                  tip="Print the first layer at this % of print speed. Slower = better bed adhesion")
        self._dbl(f, "first_layer_squish",   "1st layer squish (%):", 15.0,  0.0, 80.0, 5.0,
                  tip="Reduce the first layer Z height by this % to press the nozzle closer to the bed.\n"
                      "Helps adhesion and controls elephant's foot. 0 = no squish, 50 = half height")
        self._int(f, "travel_speed",         "Travel speed (mm/s):",   40,    10, 800,
                  tip="Speed for non-printing (travel) moves")
        self._int(f, "fan_speed",            "Fan speed (%):",           25,    0, 100,
                  tip="Part cooling fan speed. 0 = off. Higher = better bridging and overhangs, worse layer bonding")
        self._dbl(f, "max_volumetric_speed", "Max vol. (mm³/s):",      12.0,  0.5, 50.0, 0.5,
                  tip="Maximum plastic flow rate in mm³/s. Limits print speed to prevent under-extrusion.\n"
                      "Your hotend's capability — typically 8–15mm³/s for standard, up to 40+ for high-flow")
        parent.addWidget(g)

    def _add_seam_ramp_group(self, parent):
        g = QGroupBox("Seam Speed Ramp")
        f = QFormLayout(g)

        chk = QCheckBox("Enable speed ramp after alternation")
        chk.setChecked(False)
        chk.setToolTip(
            "After each layer-alternation cycle boundary, ramp speed back up\n"
            "over the specified number of layers.\n"
            "Example with alternation=2, ramp=[25,50,100]:\n"
            "  Cycle start → 25% → 50% → 100% → repeat"
        )
        chk.stateChanged.connect(self._emit)
        self._widgets["seam_ramp_enabled"] = chk
        f.addRow("", chk)

        # Individual speed % fields for up to 4 ramp layers
        defaults = [25, 50, 75, 100]
        for n in range(1, 5):
            key = f"seam_ramp_pct_{n}"
            spin = QSpinBox()
            spin.setRange(1, 200)
            spin.setValue(defaults[n - 1])
            spin.setSuffix(" %")
            spin.setToolTip(
                f"Print speed for layer {n} after each alternation boundary\n"
                f"(% of the main print speed).\n"
                f"Set to 100% to not slow this layer."
            )
            spin.valueChanged.connect(self._emit)
            self._widgets[key] = spin
            f.addRow(f"  Layer {n} speed:", spin)

        note = QLabel(
            "Tip: set ramp layers ≤ layer alternation period for the ramp\n"
            "to complete before the next cycle starts."
        )
        note.setStyleSheet("color: #667; font-size: 10px;")
        note.setWordWrap(True)
        f.addRow("", note)

        parent.addWidget(g)

    def _add_mode_group(self, parent):
        g = QGroupBox("Printing Mode")
        f = QFormLayout(g)

        cb = QComboBox()
        cb.addItems(["Spiral Vase (continuous)", "Layer Mesh"])
        cb.setToolTip("Spiral Vase: single-wall continuous spiral, no seam, ideal for vases.\n"
                      "Layer Mesh: traditional layer-by-layer with wave pattern on each layer")
        cb.currentIndexChanged.connect(self._on_mode_change)
        self._widgets["vase_mode"] = cb
        cb.currentIndexChanged.connect(self._emit)
        f.addRow("Mode:", cb)

        dbl = self._dbl(f, "spiral_points_per_degree", "Spiral res (pts/°):", 1.2, 0.1, 5.0, 0.1,
                        tip="Sampling resolution of the spiral path. 1.2 pts/° = ~432 points/revolution.\n"
                            "Higher = smoother curves but larger GCode file")
        self._spiral_row = (f, dbl)

        parent.addWidget(g)

    def _add_wave_group(self, parent):
        g = QGroupBox("Wave Pattern")
        f = QFormLayout(g)

        self._dbl(f, "wave_amplitude",    "Amplitude (mm):",   2.0, 0.0, 20.0, 0.1,
                  tip="Peak-to-trough height of the surface waves in mm. 0 = smooth cylinder")

        # Wave frequency: count OR spacing
        freq_cb = QComboBox()
        freq_cb.addItems(["Per revolution (count)", "Per distance (spacing mm)"])
        freq_cb.setToolTip("How to set wave frequency:\n"
                           "Per revolution: fixed number of waves around the circumference\n"
                           "Per distance: spacing between wave peaks in mm (adapts to model size)")
        freq_cb.currentIndexChanged.connect(self._on_freq_mode_change)
        self._widgets["wave_freq_mode"] = freq_cb
        freq_cb.currentIndexChanged.connect(self._emit)
        f.addRow("Frequency mode:", freq_cb)

        self._wave_count_spin = self._int(f, "wave_count",   "Waves / revolution:", 120, 1, 2000,
                                          tip="Number of complete wave cycles around the model per revolution")
        self._wave_spacing_spin = self._dbl(f, "wave_spacing", "Wave spacing (mm):", 4.0, 0.1, 50.0, 0.1,
                                            tip="Distance between wave peaks in mm.\nAdapts to model circumference so waves stay evenly spaced")
        # Show count by default, hide spacing
        self._wave_spacing_spin.setVisible(False)
        # Find label for spacing row
        self._spacing_label = f.labelForField(self._wave_spacing_spin)
        if self._spacing_label:
            self._spacing_label.setVisible(False)

        ptn = QComboBox()
        ptn.addItems(["sine", "triangular", "sawtooth"])
        ptn.setToolTip("Wave shape:\nSine = smooth rounded waves\nTriangular = sharp V-peaks\nSawtooth = asymmetric ramp up, sharp drop")
        ptn.currentIndexChanged.connect(self._emit)
        self._widgets["wave_pattern"] = ptn
        f.addRow("Pattern:", ptn)

        self._int(f,  "wave_smoothness",   "Smoothness (1-10):",  10,  1, 10,
                  tip="How smooth the wave shape is. 1 = sharp pointy peaks, 10 = very round and gradual")
        self._int(f,  "layer_alternation", "Alternation (rev):",   2,  1, 20,
                  tip="Number of revolutions before the wave phase shifts. Creates the interlocking mesh pattern.\n"
                      "2 = phase shifts every 2 revolutions (good default)")
        self._dbl(f,  "phase_offset",      "Phase offset (%):",   50,  0, 100, 1.0,
                  tip="How far the wave shifts at each alternation point.\n"
                      "50% = half a wave offset — creates the classic diamond mesh look")
        self._dbl(f,  "seam_shift",        "Seam shift (waves):", 0.0, 0.0, 10.0, 0.1,
                  tip="Extend the alternation cycle by this many waves to move the seam to a different position.\n"
                      "0 = no shift")

        sp = QComboBox()
        sp.addItems(["auto", "front", "back", "left", "right",
                     "front_right", "front_left", "back_right", "back_left", "sharpest"])
        sp.setToolTip("Place the phase-alternation seam at a specific corner or\n"
                      "direction of the model (front=+Y, right=+X).\n"
                      "'sharpest' finds the most acute geometric corner.")
        sp.currentIndexChanged.connect(self._emit)
        self._widgets["seam_position"] = sp
        f.addRow("Seam position:", sp)

        self._dbl(f, "seam_transition_waves", "Seam blend (waves):", 0.0, 0.0, 10.0, 0.5,
                  tip="Blend the seam phase transition over this many waves.\n"
                      "0 = hard step. 2.0 = gradual two-wave crossfade (less visible seam)")

        parent.addWidget(g)

    def _add_base_group(self, parent):
        g = QGroupBox("Base Integrity")
        f = QFormLayout(g)
        self._dbl(f, "base_height",    "Base height (mm):", 28.0, 0.0, 200.0, 0.5,
                  tip="Height of the reinforced base zone before mesh waves start. "
                      "The base uses reduced or no amplitude for structural integrity")

        bm = QComboBox()
        bm.addItems(["fewer_gaps", "tighter_waves", "solid_then_mesh"])
        bm.setToolTip("Base reinforcement mode:\n"
                      "Fewer gaps: reduces wave amplitude to minimize gaps\n"
                      "Tighter waves: compresses wave spacing in the base\n"
                      "Solid then mesh: prints solid layers first, then transitions to mesh")
        bm.currentIndexChanged.connect(self._emit)
        self._widgets["base_mode"] = bm
        f.addRow("Base mode:", bm)

        bt = QComboBox()
        bt.addItems(["exponential", "linear", "step"])
        bt.setToolTip("How amplitude ramps up from base to full mesh:\n"
                      "Exponential: slow start, fast finish (smooth)\n"
                      "Linear: constant rate\n"
                      "Step: instant jump to full amplitude")
        bt.currentIndexChanged.connect(self._emit)
        self._widgets["base_transition"] = bt
        f.addRow("Transition:", bt)

        parent.addWidget(g)

    def _add_skirt_group(self, parent):
        g = QGroupBox("Skirt / Adhesion")
        f = QFormLayout(g)

        chk = QCheckBox("Enable skirt")
        chk.setChecked(True)
        chk.setToolTip("Print a single loop around the model base to prime the nozzle and help bed adhesion")
        chk.stateChanged.connect(self._emit)
        self._widgets["skirt_enabled"] = chk
        f.addRow("", chk)

        self._dbl(f, "skirt_distance", "Skirt gap (mm):",  0.0, 0.0, 20.0, 0.5,
                  tip="Gap between skirt loop and the model. 0 = touching (skirt sits against the model)")
        self._int(f, "skirt_height",   "Skirt layers:",      1,   1,   10,
                  tip="Number of skirt loops stacked vertically")

        parent.addWidget(g)

    # ── Widget factories ─────────────────────────────────────────────────────

    def _dbl(self, form, key, label, default, lo, hi, step, tip: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(len(str(step).split(".")[-1]) if "." in str(step) else 1)
        spin.setValue(default)
        if tip:
            spin.setToolTip(tip)
        spin.valueChanged.connect(self._emit)
        self._widgets[key] = spin
        lbl = form.addRow(label, spin)
        return spin

    def _int(self, form, key, label, default, lo, hi, tip: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        if tip:
            spin.setToolTip(tip)
        spin.valueChanged.connect(self._emit)
        self._widgets[key] = spin
        form.addRow(label, spin)
        return spin

    # ── Slots ────────────────────────────────────────────────────────────────

    def _emit(self, *_):
        if not self._building:
            self.settings_changed.emit()
            self._save_timer.start()   # (re)start 500 ms debounce

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save_to_disk(self):
        try:
            SLICER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SLICER_SETTINGS_PATH.write_text(
                json.dumps(self.get_config_overrides(), indent=2)
            )
        except Exception:
            pass   # non-fatal — settings just won't persist this session

    def _load_from_disk(self):
        if not SLICER_SETTINGS_PATH.exists():
            return
        try:
            saved = json.loads(SLICER_SETTINGS_PATH.read_text())
            self.load_config(saved)
        except Exception:
            pass   # corrupt file — ignore and keep defaults

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
            "layer_alternation":     w["layer_alternation"].value(),
            "phase_offset":          w["phase_offset"].value(),
            "seam_shift":            w["seam_shift"].value(),
            "seam_position":         w["seam_position"].currentText(),
            "seam_transition_waves": w["seam_transition_waves"].value(),
            "base_height":           w["base_height"].value(),
            "base_mode":         w["base_mode"].currentText(),
            "base_transition":   w["base_transition"].currentText(),
        }
        if per_rev:
            mesh["wave_count"] = w["wave_count"].value()
        else:
            mesh["wave_spacing"] = w["wave_spacing"].value()

        print_s = {
            "layer_height":           w["layer_height"].value(),
            "print_speed":            w["print_speed"].value(),
            "first_layer_speed_pct":  w["first_layer_speed_pct"].value(),
            "first_layer_squish":     w["first_layer_squish"].value(),
            "travel_speed":           w["travel_speed"].value(),
            "fan_speed":              w["fan_speed"].value(),
            "max_volumetric_speed":   w["max_volumetric_speed"].value(),
            "print_accel":            w["print_accel"].value(),
            "travel_accel":           w["travel_accel"].value(),
            "z_hop":                  w["z_hop"].value(),
            "vase_mode":              is_vase,
            "skirt_enabled":          w["skirt_enabled"].isChecked(),
            "skirt_distance":         w["skirt_distance"].value(),
            "skirt_height":           w["skirt_height"].value(),
            "seam_ramp_enabled":      w["seam_ramp_enabled"].isChecked(),
            "seam_ramp_pcts":         [w[f"seam_ramp_pct_{n}"].value() for n in range(1, 5)],
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

            _set_dbl("nozzle_diameter", printer.get("nozzle_diameter"))
            _set_int("nozzle_temp",     printer.get("nozzle_temp"))
            _set_int("bed_temp",        printer.get("bed_temp"))

            _set_dbl("layer_height",             ps.get("layer_height"))
            _set_int("print_speed",              ps.get("print_speed"))
            _set_int("first_layer_speed_pct",    ps.get("first_layer_speed_pct"))
            _set_dbl("first_layer_squish",       ps.get("first_layer_squish"))
            _set_int("travel_speed",             ps.get("travel_speed"))
            _set_int("fan_speed",                ps.get("fan_speed"))
            _set_dbl("max_volumetric_speed",     ps.get("max_volumetric_speed"))
            _set_int("print_accel",              ps.get("print_accel"))
            _set_int("travel_accel",             ps.get("travel_accel"))
            _set_dbl("z_hop",                    ps.get("z_hop"))
            _set_dbl("spiral_points_per_degree", ps.get("spiral_points_per_degree"))

            if ps.get("vase_mode"):
                w["vase_mode"].setCurrentIndex(0)
            else:
                w["vase_mode"].setCurrentIndex(1)

            if ps.get("skirt_enabled") is not None:
                w["skirt_enabled"].setChecked(bool(ps["skirt_enabled"]))
            _set_dbl("skirt_distance", ps.get("skirt_distance"))
            _set_int("skirt_height",   ps.get("skirt_height"))

            if ps.get("seam_ramp_enabled") is not None:
                w["seam_ramp_enabled"].setChecked(bool(ps["seam_ramp_enabled"]))
            ramp_pcts = ps.get("seam_ramp_pcts", [])
            for n in range(1, 5):
                if n - 1 < len(ramp_pcts):
                    w[f"seam_ramp_pct_{n}"].setValue(int(ramp_pcts[n - 1]))

            _set_dbl("wave_amplitude",   ms.get("wave_amplitude"))
            if "wave_count" in ms and ms["wave_count"] is not None:
                w["wave_freq_mode"].setCurrentIndex(0)
                _set_int("wave_count", ms["wave_count"])
            elif "wave_spacing" in ms:
                w["wave_freq_mode"].setCurrentIndex(1)
                _set_dbl("wave_spacing", ms["wave_spacing"])

            _set_combo("wave_pattern",     ms.get("wave_pattern"))
            _set_int("wave_smoothness",    ms.get("wave_smoothness"))
            _set_int("layer_alternation",          ms.get("layer_alternation"))
            _set_dbl("phase_offset",               ms.get("phase_offset"))
            _set_dbl("seam_shift",                 ms.get("seam_shift"))
            _set_combo("seam_position",            ms.get("seam_position"))
            _set_dbl("seam_transition_waves",      ms.get("seam_transition_waves"))
            _set_dbl("base_height",                ms.get("base_height"))
            _set_combo("base_mode",        ms.get("base_mode"))
            _set_combo("base_transition",  ms.get("base_transition"))
        finally:
            self._building = False

    def load_printer_profile(self, profile: dict) -> None:
        """Called when the active printer profile changes.
        Applies hardware overrides from the profile into the slicer panel,
        then emits settings_changed so the main window can refresh viewers."""
        w = self._widgets
        def _set(key, val):
            if val is None or key not in w:
                return
            widget = w[key]
            try:
                if hasattr(widget, "setValue"):
                    widget.setValue(float(val) if hasattr(widget, "setDecimals") else int(val))
                elif hasattr(widget, "setCurrentText"):
                    widget.setCurrentText(str(val))
            except Exception:
                pass

        _set("nozzle_diameter", profile.get("nozzle_diameter"))
        _set("nozzle_temp",     profile.get("nozzle_temp"))
        _set("bed_temp",        profile.get("bed_temp"))
        self.settings_changed.emit()


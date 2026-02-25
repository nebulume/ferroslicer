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
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QLocale, QByteArray, QObject, QEvent
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

def _res_path() -> Path:
    """Path to gui/resources — works in both dev and frozen .app."""
    if getattr(sys, "frozen", False):
        import sys as _sys
        return Path(_sys._MEIPASS) / "gui" / "resources"
    # settings_panel.py lives in gui/widgets/ → up two levels → gui/resources
    return Path(__file__).parent.parent / "resources"


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


# ── Scroll-wheel guard ───────────────────────────────────────────────────────

class _WheelIgnoreFilter(QObject):
    """Event filter that swallows Wheel events so spinboxes/combos don't change on scroll."""
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Wheel:
            ev.ignore()
            return True
        return super().eventFilter(obj, ev)

_wheel_guard = None   # singleton, created once per process

def _no_scroll(widget) -> None:
    """Prevent mouse-wheel from changing a spinbox/combo value."""
    global _wheel_guard
    if _wheel_guard is None:
        from PyQt6.QtWidgets import QApplication
        _wheel_guard = _WheelIgnoreFilter(QApplication.instance())
    widget.installEventFilter(_wheel_guard)


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
        self._seam_ramp_row_frames: list = []
        self._seam_ramp_follow_lbls: dict = {}   # n → QLabel shown on inactive rows
        self._seam_ramp_active: int = 99   # all rows active by default

        # Debounce timer — batches rapid changes into one disk write
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_to_disk)

        self._add_logo_header(layout)
        self._add_presets_bar(layout)
        self._add_printer_group(layout)
        self._add_motion_group(layout)
        self._add_print_group(layout)
        self._add_mode_group(layout)
        self._add_wave_group(layout)
        self._add_seam_ramp_group(layout)   # after wave_group: uses layer_alternation
        self._add_base_group(layout)
        self._add_skirt_group(layout)

        layout.addStretch()
        self._building = False

        # Install wheel-ignore filter on every spin/combo in this panel
        self._apply_wheel_guard()

        # Restore last session's values (silently ignore if file missing/corrupt)
        self._load_from_disk()

    # ── Logo header ──────────────────────────────────────────────────────────

    def _add_logo_header(self, parent):
        """Compact branded header: SVG logo + app name + tagline."""
        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 #1a3a5c, stop:1 #122840);"
            "border-bottom: 1px solid #3a6a9a;"
        )
        header.setAutoFillBackground(True)
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        # Render the SVG logo into a 44×44 QPixmap
        logo_lbl = QLabel()
        logo_lbl.setFixedSize(44, 44)
        svg_path = _res_path() / "ferroslicer_logo.svg"
        if svg_path.exists():
            renderer = QSvgRenderer(str(svg_path))
            pix = QPixmap(44, 44)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            renderer.render(painter)
            painter.end()
            logo_lbl.setPixmap(pix)
        h.addWidget(logo_lbl)

        # App name + tagline
        text_col = QWidget()
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)

        name_lbl = QLabel("FerroSlicer")
        name_lbl.setStyleSheet(
            "color: #a8e8ff; font-size: 15px; font-weight: 700;"
            "letter-spacing: 1px; background: transparent;"
        )
        text_layout.addWidget(name_lbl)

        sub_lbl = QLabel("mesh vase slicer")
        sub_lbl.setStyleSheet(
            "color: #6aaccc; font-size: 10px; background: transparent;"
        )
        text_layout.addWidget(sub_lbl)

        h.addWidget(text_col, stretch=1)
        parent.addWidget(header)

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
        from PyQt6.QtWidgets import QFrame
        g = QGroupBox("Layer Ramp")
        outer = QVBoxLayout(g)
        outer.setSpacing(4)
        outer.setContentsMargins(6, 4, 6, 6)

        chk = QCheckBox("Enable layer speed / extrusion ramp")
        chk.setChecked(False)
        chk.setToolTip(
            "At each layer-alternation cycle, vary print speed and optionally\n"
            "extrusion rate per layer.  Rows automatically match Alternation count."
        )
        chk.stateChanged.connect(self._emit)
        self._widgets["seam_ramp_enabled"] = chk
        outer.addWidget(chk)

        # Container rebuilt whenever layer_alternation changes
        self._seam_ramp_rows_widget = QWidget()
        self._seam_ramp_rows_layout = QVBoxLayout(self._seam_ramp_rows_widget)
        self._seam_ramp_rows_layout.setSpacing(3)
        self._seam_ramp_rows_layout.setContentsMargins(0, 0, 0, 0)

        # Vertical "active layers" slider + count label (bottom = all layers active)
        self._seam_ramp_slider = QSlider(Qt.Orientation.Vertical)
        self._seam_ramp_slider.setInvertedAppearance(True)
        self._seam_ramp_slider.setRange(1, 2)   # updated in _rebuild_seam_ramp_rows
        self._seam_ramp_slider.setValue(2)
        self._seam_ramp_slider.setFixedWidth(22)
        self._seam_ramp_slider.setToolTip(
            "Active layers slider — drag up to reduce the number of\n"
            "independently-configured layers.\n\n"
            "Bottom = all layers are active (each has its own settings).\n"
            "Any layer above the cutpoint copies the last active layer's settings.\n\n"
            "NOTE: The slider marks are evenly spaced regardless of whether\n"
            "the 'Var. extrusion' section is open — use the 'k/N' counter\n"
            "above the slider to see exactly how many layers are active."
        )
        self._seam_ramp_slider.valueChanged.connect(self._on_seam_ramp_active_changed)

        # Small counter label above the slider: shows "k/N" or "all"
        self._seam_ramp_active_lbl = QLabel("all")
        self._seam_ramp_active_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._seam_ramp_active_lbl.setStyleSheet(
            "color: #9988bb; font-size: 9px; font-weight: bold;"
        )
        self._seam_ramp_active_lbl.setToolTip(
            "Number of independently-configured layers / total layers.\n"
            "'all' = every layer has its own settings."
        )

        slider_col = QWidget()
        scl = QVBoxLayout(slider_col)
        scl.setContentsMargins(0, 0, 0, 0)
        scl.setSpacing(2)
        scl.addWidget(self._seam_ramp_active_lbl)
        scl.addWidget(self._seam_ramp_slider, stretch=1)

        rows_slider_box = QWidget()
        rsl = QHBoxLayout(rows_slider_box)
        rsl.setContentsMargins(0, 0, 0, 0)
        rsl.setSpacing(4)
        rsl.addWidget(self._seam_ramp_rows_widget, stretch=1)
        rsl.addWidget(slider_col)
        outer.addWidget(rows_slider_box)

        # Connect to layer_alternation (created in _add_wave_group just above)
        self._widgets["layer_alternation"].valueChanged.connect(
            self._rebuild_seam_ramp_rows
        )
        self._rebuild_seam_ramp_rows(self._widgets["layer_alternation"].value())

        parent.addWidget(g)

    # ── Seam ramp dynamic rows ────────────────────────────────────────────────

    def _collect_seam_ramp_layer_data(self) -> list:
        """Return current per-layer settings as a list of dicts.
        Inactive rows (past _seam_ramp_active) copy the last active layer's data."""
        w = self._widgets
        active = getattr(self, "_seam_ramp_active", 99)
        result = []
        last_active_data: dict = {}
        n = 1
        while f"seam_ramp_speed_{n}" in w:
            if n <= active:
                p2v_ramp_w = w.get(f"seam_ramp_p2v_ramp_{n}")
                v2p_ramp_w = w.get(f"seam_ramp_v2p_ramp_{n}")
                row_data = {
                    "speed_pct":     w[f"seam_ramp_speed_{n}"].value(),
                    "var_extrusion": w[f"seam_ramp_varx_{n}"].isChecked(),
                    "peak_pct":      w[f"seam_ramp_peak_pct_{n}"].value(),
                    "peak_ramp":     w[f"seam_ramp_peak_ramp_{n}"].currentText(),
                    "p2v_rate":      w[f"seam_ramp_p2v_{n}"].value(),
                    "p2v_ramp":      p2v_ramp_w.currentText() if p2v_ramp_w else "gradual",
                    "valley_pct":    w[f"seam_ramp_valley_pct_{n}"].value(),
                    "valley_ramp":   w[f"seam_ramp_valley_ramp_{n}"].currentText(),
                    "v2p_rate":      w[f"seam_ramp_v2p_{n}"].value(),
                    "v2p_ramp":      v2p_ramp_w.currentText() if v2p_ramp_w else "gradual",
                }
                last_active_data = row_data
            else:
                row_data = dict(last_active_data)
            result.append(row_data)
            n += 1
        return result

    def _restore_seam_ramp_row(self, n: int, vals: dict) -> None:
        w = self._widgets
        def _sv(key, val):
            if key in w and val is not None:
                try:
                    if hasattr(w[key], "setValue"):
                        w[key].setValue(val)
                    elif hasattr(w[key], "setChecked"):
                        w[key].setChecked(bool(val))
                    elif hasattr(w[key], "setCurrentText"):
                        w[key].setCurrentText(str(val))
                except Exception:
                    pass
        _sv(f"seam_ramp_speed_{n}",       vals.get("speed_pct"))
        _sv(f"seam_ramp_varx_{n}",        vals.get("var_extrusion", False))
        _sv(f"seam_ramp_peak_pct_{n}",    vals.get("peak_pct"))
        _sv(f"seam_ramp_peak_ramp_{n}",   vals.get("peak_ramp"))
        _sv(f"seam_ramp_p2v_{n}",         vals.get("p2v_rate"))
        _sv(f"seam_ramp_p2v_ramp_{n}",    vals.get("p2v_ramp"))
        _sv(f"seam_ramp_valley_pct_{n}",  vals.get("valley_pct"))
        _sv(f"seam_ramp_valley_ramp_{n}", vals.get("valley_ramp"))
        _sv(f"seam_ramp_v2p_{n}",         vals.get("v2p_rate"))
        _sv(f"seam_ramp_v2p_ramp_{n}",    vals.get("v2p_ramp"))

    def _rebuild_seam_ramp_rows(self, count: int) -> None:
        """Rebuild the per-layer rows to match `count` (= layer_alternation)."""
        old_vals = self._collect_seam_ramp_layer_data()

        # Remove stale widget refs
        for k in [k for k in self._widgets
                  if k.startswith("seam_ramp_") and k != "seam_ramp_enabled"]:
            del self._widgets[k]

        # Clear old row widgets
        layout = self._seam_ramp_rows_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Build new rows, track frames and follow-labels for enable/disable
        self._seam_ramp_row_frames = []
        self._seam_ramp_follow_lbls = {}
        defaults = [25, 50, 75, 100]
        for n in range(1, count + 1):
            row = self._make_seam_ramp_row(n, defaults[(n - 1) % 4])
            layout.addWidget(row)
            self._seam_ramp_row_frames.append(row)

        # Sync slider range — all rows active by default after a rebuild
        self._seam_ramp_active = count
        if hasattr(self, "_seam_ramp_slider"):
            self._seam_ramp_slider.blockSignals(True)
            self._seam_ramp_slider.setRange(1, count)
            self._seam_ramp_slider.setValue(count)
            self._seam_ramp_slider.blockSignals(False)
        if hasattr(self, "_seam_ramp_active_lbl"):
            self._seam_ramp_active_lbl.setText("all")

        # Restore saved values where available
        for n, vals in enumerate(old_vals[:count], 1):
            self._restore_seam_ramp_row(n, vals)

        # Apply wheel-ignore filter to freshly created widgets
        for w in self._seam_ramp_rows_widget.findChildren(
                (QSpinBox, QDoubleSpinBox, QComboBox)):
            _no_scroll(w)

        if not self._building:
            self._emit()

    def _on_seam_ramp_active_changed(self, k: int) -> None:
        """Slider moved: update which rows are independently active."""
        self._seam_ramp_active = k
        total = len(self._seam_ramp_row_frames)

        # Update counter label
        if hasattr(self, "_seam_ramp_active_lbl"):
            self._seam_ramp_active_lbl.setText(
                "all" if k >= total else f"{k}/{total}"
            )

        active_style = (
            "QFrame { border: 1px solid #2a3248; border-radius: 3px; background: #0f1018; }"
        )
        inactive_style = (
            "QFrame { border: 2px dashed #6a3a9a; border-radius: 3px; background: #07050f; }"
        )

        for i, frame in enumerate(self._seam_ramp_row_frames):
            is_active = (i < k)
            frame.setEnabled(is_active)
            frame.setStyleSheet(active_style if is_active else inactive_style)

        # Update "→ follows layer k" indicator labels
        for n, lbl in self._seam_ramp_follow_lbls.items():
            if n > k:
                lbl.setText(f"→ L{k}")
                lbl.setVisible(True)
            else:
                lbl.setVisible(False)

        if not self._building:
            self._emit()

    def _apply_wheel_guard(self) -> None:
        """Install wheel-ignore filter on every spin-box and combo-box in this panel."""
        container = self.widget()
        for w in container.findChildren((QSpinBox, QDoubleSpinBox, QComboBox)):
            _no_scroll(w)

    def _make_seam_ramp_row(self, n: int, default_speed: int = 100) -> "QWidget":
        """Create a single collapsible layer ramp row."""
        from PyQt6.QtWidgets import QFrame
        _ss = "border: none; background: transparent;"

        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { border: 1px solid #252a38; border-radius: 3px;"
            " background: #0f1018; }"
        )
        vlay = QVBoxLayout(frame)
        vlay.setContentsMargins(6, 4, 6, 4)
        vlay.setSpacing(2)

        # ── header row ──────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(_ss)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(4)

        lbl = QLabel(f"Layer {n}")
        lbl.setStyleSheet(f"color: #7a8aaa; font-size: 11px; font-weight: bold; {_ss}")
        hlay.addWidget(lbl)

        spd_lbl = QLabel("Speed:")
        spd_lbl.setStyleSheet(f"color: #667; font-size: 11px; {_ss}")
        hlay.addWidget(spd_lbl)

        speed_spin = QSpinBox()
        speed_spin.setRange(1, 200)
        speed_spin.setValue(default_speed)
        speed_spin.setSuffix(" %")
        speed_spin.setFixedWidth(64)
        speed_spin.setToolTip(f"Print speed for layer {n} as % of base print speed")
        speed_spin.valueChanged.connect(self._emit)
        self._widgets[f"seam_ramp_speed_{n}"] = speed_spin
        hlay.addWidget(speed_spin)

        hlay.addStretch()

        varx_chk = QCheckBox("Var. extrusion")
        varx_chk.setChecked(False)
        varx_chk.setStyleSheet(f"font-size: 10px; color: #88a; {_ss}")
        varx_chk.setToolTip("Enable wave-phase variable extrusion for this layer")
        varx_chk.stateChanged.connect(self._emit)
        self._widgets[f"seam_ramp_varx_{n}"] = varx_chk
        hlay.addWidget(varx_chk)

        # "→ L k" label — hidden while row is active, shown when row is inactive
        follow_lbl = QLabel("")
        follow_lbl.setStyleSheet(
            f"color: #9055cc; font-size: 9px; font-style: italic; {_ss}"
        )
        follow_lbl.setToolTip("This layer is inactive — its settings are copied from the last active layer.")
        follow_lbl.setVisible(False)
        self._seam_ramp_follow_lbls[n] = follow_lbl
        hlay.addWidget(follow_lbl)

        vlay.addWidget(header)

        # ── expandable extrusion section ────────────────────────────────────
        extr = QWidget()
        extr.setVisible(False)
        extr.setStyleSheet(_ss)
        elay = QFormLayout(extr)
        elay.setContentsMargins(12, 2, 0, 2)
        elay.setSpacing(3)
        elay.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def _spin(key, lo, hi, default, tip, suffix=" %", width=62):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(default)
            s.setSuffix(suffix)
            s.setFixedWidth(width)
            s.setToolTip(tip)
            s.valueChanged.connect(self._emit)
            self._widgets[key] = s
            return s

        def _combo(key, items, tip):
            c = QComboBox()
            c.addItems(items)
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            c.setMinimumWidth(108)
            c.setToolTip(tip)
            c.currentIndexChanged.connect(self._emit)
            self._widgets[key] = c
            return c

        _ramp_items = ["gradual", "parabolic", "straight"]

        # Peak row
        peak_box = QWidget(); peak_box.setStyleSheet(_ss)
        pl = QHBoxLayout(peak_box); pl.setContentsMargins(0,0,0,0); pl.setSpacing(4)
        pl.addWidget(_spin(f"seam_ramp_peak_pct_{n}", -100, 500, 0,
                           "+% extrusion at wave peak (+25 = 1.25× E)"))
        pl.addWidget(_combo(f"seam_ramp_peak_ramp_{n}", _ramp_items,
                            "Curve shape for the extrusion step at the wave peak.\n"
                            "gradual = smooth S-curve (3t²–2t³)\n"
                            "parabolic = sharp near the extreme (t²)\n"
                            "straight = linear blend"))
        pl.addStretch()
        pk_lbl = QLabel("Peak:"); pk_lbl.setStyleSheet(f"color:#779;font-size:10px;{_ss}")
        elay.addRow(pk_lbl, peak_box)

        p2v_box = QWidget(); p2v_box.setStyleSheet(_ss)
        p2v_bl = QHBoxLayout(p2v_box); p2v_bl.setContentsMargins(0,0,0,0); p2v_bl.setSpacing(4)
        p2v_bl.addWidget(_spin(f"seam_ramp_p2v_{n}", 0, 500, 100,
                               "E multiplier (%) while descending from peak → valley.\n"
                               "100 = normal extrusion on the falling flank."))
        p2v_bl.addWidget(_combo(f"seam_ramp_p2v_ramp_{n}", _ramp_items,
                                "Curve shape for the descending flank (peak → valley).\n"
                                "gradual = smooth S-curve\n"
                                "parabolic = accelerating drop\n"
                                "straight = linear"))
        p2v_bl.addStretch()
        p2v_lbl = QLabel("Peak→Valley:"); p2v_lbl.setStyleSheet(f"color:#779;font-size:10px;{_ss}")
        elay.addRow(p2v_lbl, p2v_box)

        # Valley row
        val_box = QWidget(); val_box.setStyleSheet(_ss)
        vl = QHBoxLayout(val_box); vl.setContentsMargins(0,0,0,0); vl.setSpacing(4)
        vl.addWidget(_spin(f"seam_ramp_valley_pct_{n}", -100, 500, 0,
                           "+% extrusion at wave valley"))
        vl.addWidget(_combo(f"seam_ramp_valley_ramp_{n}", _ramp_items,
                            "Curve shape for the extrusion step at the wave valley.\n"
                            "gradual = smooth S-curve (3t²–2t³)\n"
                            "parabolic = sharp near the extreme (t²)\n"
                            "straight = linear blend"))
        vl.addStretch()
        va_lbl = QLabel("Valley:"); va_lbl.setStyleSheet(f"color:#779;font-size:10px;{_ss}")
        elay.addRow(va_lbl, val_box)

        v2p_box = QWidget(); v2p_box.setStyleSheet(_ss)
        v2p_bl = QHBoxLayout(v2p_box); v2p_bl.setContentsMargins(0,0,0,0); v2p_bl.setSpacing(4)
        v2p_bl.addWidget(_spin(f"seam_ramp_v2p_{n}", 0, 500, 100,
                               "E multiplier (%) while rising from valley → peak.\n"
                               "100 = normal extrusion on the rising flank."))
        v2p_bl.addWidget(_combo(f"seam_ramp_v2p_ramp_{n}", _ramp_items,
                                "Curve shape for the ascending flank (valley → peak).\n"
                                "gradual = smooth S-curve\n"
                                "parabolic = accelerating rise\n"
                                "straight = linear"))
        v2p_bl.addStretch()
        v2p_lbl = QLabel("Valley→Peak:"); v2p_lbl.setStyleSheet(f"color:#779;font-size:10px;{_ss}")
        elay.addRow(v2p_lbl, v2p_box)

        vlay.addWidget(extr)
        varx_chk.toggled.connect(extr.setVisible)

        return frame

    def _add_mode_group(self, parent):
        g = QGroupBox("Printing Mode")
        f = QFormLayout(g)

        _mode_tip = ("Spiral Vase: single-wall continuous spiral, no seam, ideal for vases.\n"
                     "Layer Mesh: traditional layer-by-layer with wave pattern on each layer")
        cb = QComboBox()
        cb.addItems(["Spiral Vase (continuous)", "Layer Mesh"])
        cb.setToolTip(_mode_tip)
        cb.currentIndexChanged.connect(self._on_mode_change)
        self._widgets["vase_mode"] = cb
        cb.currentIndexChanged.connect(self._emit)
        _mode_lbl = QLabel("Mode:")
        _mode_lbl.setToolTip(_mode_tip)
        f.addRow(_mode_lbl, cb)

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
        _freq_tip = ("How to set wave frequency:\n"
                     "Per revolution: fixed number of waves around the circumference\n"
                     "Per distance: spacing between wave peaks in mm (adapts to model size)")
        freq_cb = QComboBox()
        freq_cb.addItems(["Per revolution (count)", "Per distance (spacing mm)"])
        freq_cb.setToolTip(_freq_tip)
        freq_cb.currentIndexChanged.connect(self._on_freq_mode_change)
        self._widgets["wave_freq_mode"] = freq_cb
        freq_cb.currentIndexChanged.connect(self._emit)
        _freq_lbl = QLabel("Frequency mode:")
        _freq_lbl.setToolTip(_freq_tip)
        f.addRow(_freq_lbl, freq_cb)

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

        _ptn_tip = "Wave shape:\nSine = smooth rounded waves\nTriangular = sharp V-peaks\nSawtooth = asymmetric ramp up, sharp drop"
        ptn = QComboBox()
        ptn.addItems(["sine", "triangular", "sawtooth"])
        ptn.setToolTip(_ptn_tip)
        ptn.currentIndexChanged.connect(self._emit)
        self._widgets["wave_pattern"] = ptn
        _ptn_lbl = QLabel("Pattern:")
        _ptn_lbl.setToolTip(_ptn_tip)
        f.addRow(_ptn_lbl, ptn)

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

        _sp_tip = ("Place the phase-alternation seam at a specific corner or\n"
                   "direction of the model (front=+Y, right=+X).\n"
                   "'sharpest' finds the most acute geometric corner.")
        sp = QComboBox()
        sp.addItems(["auto", "front", "back", "left", "right",
                     "front_right", "front_left", "back_right", "back_left", "sharpest"])
        sp.setToolTip(_sp_tip)
        sp.currentIndexChanged.connect(self._emit)
        self._widgets["seam_position"] = sp
        _sp_lbl = QLabel("Seam position:")
        _sp_lbl.setToolTip(_sp_tip)
        f.addRow(_sp_lbl, sp)

        self._dbl(f, "seam_transition_waves", "Seam blend (waves):", 0.0, 0.0, 10.0, 0.5,
                  tip="Blend the seam phase transition over this many waves.\n"
                      "0 = hard step. 2.0 = gradual two-wave crossfade (less visible seam)")

        # ── Wave skew (shape warp to combat running-wave artifact) ────────────
        skew_tip = (
            "Warps the wave shape so the peak occurs earlier or later in each cycle,\n"
            "compensating for the 'running wave' distortion some printers produce.\n\n"
            "When printing curved paths, acceleration/deceleration can stretch\n"
            "one side of each wave, making the rise slower than the fall (or vice versa).\n\n"
            "Positive values move the peak later in the cycle (slow rise / fast fall).\n"
            "Negative values move the peak earlier (fast rise / slow fall).\n\n"
            "Start with small values (±10–30) and compare printed output to the\n"
            "on-screen preview until the peaks look symmetric."
        )
        skew_chk = QCheckBox("Enable wave skew")
        skew_chk.setChecked(False)
        skew_chk.setToolTip(skew_tip)
        skew_chk.stateChanged.connect(self._emit)
        self._widgets["wave_skew_enabled"] = skew_chk
        f.addRow("", skew_chk)

        skew_spin = QDoubleSpinBox()
        skew_spin.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        skew_spin.setRange(-100.0, 100.0)
        skew_spin.setSingleStep(5.0)
        skew_spin.setDecimals(1)
        skew_spin.setValue(0.0)
        skew_spin.setFixedWidth(self._SPIN_W)
        skew_spin.setToolTip(skew_tip)
        skew_spin.valueChanged.connect(self._emit)
        self._widgets["wave_skew"] = skew_spin
        skew_lbl = QLabel("Wave skew strength:")
        skew_lbl.setToolTip(skew_tip)
        f.addRow(skew_lbl, skew_spin)

        parent.addWidget(g)

    def _add_base_group(self, parent):
        g = QGroupBox("Base Integrity")
        f = QFormLayout(g)
        self._dbl(f, "base_height",    "Base height (mm):", 28.0, 0.0, 200.0, 0.5,
                  tip="Height of the reinforced base zone before mesh waves start. "
                      "The base uses reduced or no amplitude for structural integrity")

        _bm_tip = ("Base reinforcement mode:\n"
                   "Fewer gaps: reduces wave amplitude to minimize gaps\n"
                   "Tighter waves: compresses wave spacing in the base\n"
                   "Solid then mesh: prints solid layers first, then transitions to mesh")
        bm = QComboBox()
        bm.addItems(["fewer_gaps", "tighter_waves", "solid_then_mesh"])
        bm.setToolTip(_bm_tip)
        bm.currentIndexChanged.connect(self._emit)
        self._widgets["base_mode"] = bm
        _bm_lbl = QLabel("Base mode:")
        _bm_lbl.setToolTip(_bm_tip)
        f.addRow(_bm_lbl, bm)

        _bt_tip = ("How amplitude ramps up from base to full mesh:\n"
                   "Exponential: slow start, fast finish (smooth)\n"
                   "Linear: constant rate\n"
                   "Step: instant jump to full amplitude")
        bt = QComboBox()
        bt.addItems(["exponential", "linear", "step"])
        bt.setToolTip(_bt_tip)
        bt.currentIndexChanged.connect(self._emit)
        self._widgets["base_transition"] = bt
        _bt_lbl = QLabel("Transition:")
        _bt_lbl.setToolTip(_bt_tip)
        f.addRow(_bt_lbl, bt)

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
        self._int(f, "skirt_loops",    "Skirt loops:",        1,   1,    8,
                  tip="Number of concentric skirt loops printed side-by-side.\n"
                      "More loops = better nozzle priming and bed adhesion.\n"
                      "Each loop is spaced one nozzle-width from the previous.")

        parent.addWidget(g)

    # ── Widget factories ─────────────────────────────────────────────────────

    _SPIN_W = 90  # uniform width for all numeric input boxes

    def _dbl(self, form, key, label, default, lo, hi, step, tip: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(len(str(step).split(".")[-1]) if "." in str(step) else 1)
        spin.setValue(default)
        spin.setFixedWidth(self._SPIN_W)
        if tip:
            spin.setToolTip(tip)
        spin.valueChanged.connect(self._emit)
        self._widgets[key] = spin
        lbl = QLabel(label)
        if tip:
            lbl.setToolTip(tip)
        form.addRow(lbl, spin)
        return spin

    def _int(self, form, key, label, default, lo, hi, tip: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.setFixedWidth(self._SPIN_W)
        if tip:
            spin.setToolTip(tip)
        spin.valueChanged.connect(self._emit)
        self._widgets[key] = spin
        lbl = QLabel(label)
        if tip:
            lbl.setToolTip(tip)
        form.addRow(lbl, spin)
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
            "seam_shift":               w["seam_shift"].value(),
            "seam_position":            w["seam_position"].currentText(),
            "seam_transition_waves":    w["seam_transition_waves"].value(),
            "wave_skew_enabled":        w["wave_skew_enabled"].isChecked(),
            "wave_skew":                w["wave_skew"].value(),
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
            "seam_ramp_layers":       self._collect_seam_ramp_layer_data(),
            "skirt_loops":            w["skirt_loops"].value(),
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
            _set_int("skirt_loops",    ps.get("skirt_loops"))

            _set_dbl("wave_amplitude",   ms.get("wave_amplitude"))
            if "wave_count" in ms and ms["wave_count"] is not None:
                w["wave_freq_mode"].setCurrentIndex(0)
                _set_int("wave_count", ms["wave_count"])
            elif "wave_spacing" in ms:
                w["wave_freq_mode"].setCurrentIndex(1)
                _set_dbl("wave_spacing", ms["wave_spacing"])

            _set_combo("wave_pattern",     ms.get("wave_pattern"))
            _set_int("wave_smoothness",    ms.get("wave_smoothness"))
            # layer_alternation must be set before restoring seam ramp rows
            _set_int("layer_alternation",          ms.get("layer_alternation"))

            # Restore seam ramp (rows already rebuilt by layer_alternation.setValue above)
            if ps.get("seam_ramp_enabled") is not None:
                w["seam_ramp_enabled"].setChecked(bool(ps["seam_ramp_enabled"]))
            layers_data = ps.get("seam_ramp_layers", [])
            if not layers_data:
                # backward compat: old seam_ramp_pcts format
                old_pcts = ps.get("seam_ramp_pcts", [])
                if old_pcts:
                    layers_data = [{"speed_pct": p} for p in old_pcts]
            for n, vals in enumerate(layers_data, 1):
                if f"seam_ramp_speed_{n}" in w:
                    self._restore_seam_ramp_row(n, vals)
            _set_dbl("phase_offset",               ms.get("phase_offset"))
            _set_dbl("seam_shift",                 ms.get("seam_shift"))
            _set_combo("seam_position",            ms.get("seam_position"))
            _set_dbl("seam_transition_waves",        ms.get("seam_transition_waves"))
            if ms.get("wave_skew_enabled") is not None:
                w["wave_skew_enabled"].setChecked(bool(ms["wave_skew_enabled"]))
            _set_dbl("wave_skew",                    ms.get("wave_skew"))
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


"""
Test First Layer dialog.

Generates a 30×30mm single-layer filled square GCode for first-layer
adhesion calibration. Uses its own settings so adjustments don't affect
the main slicer configuration.
"""

import math
import traceback
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QLabel, QProgressBar,
    QMessageBox, QCheckBox,
)


# ── GCode generator ───────────────────────────────────────────────────────────

def _generate_test_square(cfg: dict, custom_gcode: dict, infill_only: bool = False) -> str:
    """Return a 30×30mm filled single-layer test square as a GCode string."""
    ps  = cfg.get("print_settings", {})
    pr  = cfg.get("printer", {})
    pp  = cfg.get("printer_profile", {})

    layer_height  = float(ps.get("layer_height", 0.4))
    squish_pct    = float(ps.get("first_layer_squish", 15.0))
    speed_pct     = int(ps.get("first_layer_speed_pct", 50))
    print_speed   = float(ps.get("print_speed", 35))
    travel_speed  = float(ps.get("travel_speed", 40))
    fan_pct       = float(ps.get("fan_speed", 25))
    e_mult        = float(ps.get("extrusion_multiplier", 1.0))
    nozzle_temp   = float(pr.get("nozzle_temp", 260))
    bed_temp      = float(pr.get("bed_temp", 65))
    nozzle_dia    = float(pr.get("nozzle_diameter", 0.4))
    fil_dia       = float(pp.get("filament_diameter", 1.75))
    firmware      = pp.get("firmware", "klipper").lower()
    retract_dist  = float(pp.get("retract_dist", 0.8))
    retract_speed = float(pp.get("retract_speed", 40.0))
    start_gc      = custom_gcode.get("start_gcode", "")
    end_gc        = custom_gcode.get("end_gcode", "")

    # Derived
    z              = layer_height * (1.0 - squish_pct / 100.0)
    speed          = print_speed * speed_pct / 100.0
    speed_f        = int(speed * 60)
    travel_f       = int(travel_speed * 60)
    extrusion_w    = nozzle_dia * 1.2
    fil_area       = math.pi * (fil_dia / 2.0) ** 2
    e_factor       = (z * extrusion_w) / fil_area * e_mult
    fan_pwm        = max(0, min(255, int(fan_pct / 100.0 * 255)))

    # Square: 30×30mm centred at (110, 110) on a 220×220 bed
    x0, y0 = 95.0, 95.0
    x1, y1 = 125.0, 125.0

    def _retract():
        if firmware == "klipper":
            return "G10"
        return f"G1 E-{retract_dist:.2f} F{int(retract_speed * 60)}"

    def _unretract():
        if firmware == "klipper":
            return "G11"
        return f"G1 E{retract_dist:.2f} F{int(retract_speed * 60)}"

    def _e(dx, dy=0.0):
        return abs(math.sqrt(dx * dx + dy * dy) * e_factor)

    L = []
    add = L.append

    # ── Header ────────────────────────────────────────────────────────────────
    mode_str = "infill only" if infill_only else "walls + infill"
    add("; MeshyGen — Test First Layer Square (30×30 mm)")
    add(f"; Mode: {mode_str}")
    add(f"; Layer z={z:.3f} mm  speed={speed:.0f} mm/s  E×{e_mult:.3f}")
    add(f"; Layer height={layer_height:.2f} mm  squish={squish_pct:.1f}%")
    add(f"; Nozzle={nozzle_temp:.0f}°C  Bed={bed_temp:.0f}°C")
    add(f"; Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    add("")

    # ── Start GCode ───────────────────────────────────────────────────────────
    add("; --- START GCODE ---")
    if start_gc.strip():
        rendered = (
            start_gc
            .replace("{bed_temp}",    str(int(bed_temp)))
            .replace("{nozzle_temp}", str(int(nozzle_temp)))
            .replace("{fan_speed}",   str(fan_pwm))
        )
        L.extend(rendered.splitlines())
    elif firmware == "marlin":
        add(f"M140 S{int(bed_temp)}")
        add(f"M104 S{int(nozzle_temp)}")
        add(f"M190 S{int(bed_temp)}")
        add(f"M109 S{int(nozzle_temp)}")
        add("G28")
        add(f"M106 S{fan_pwm}")
    else:  # klipper
        add(f"SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=bed_temp VALUE={int(bed_temp)}")
        add(f"SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=extruder_temp VALUE={int(nozzle_temp)}")
        add(f"M106 S{fan_pwm}")
        add("START_PRINT")
    add("")
    add("G90  ; absolute XYZ")
    add("M83  ; relative E")
    add("")

    # ── Travel to start corner (bottom-left) ──────────────────────────────────
    add(f"G1 Z2.0 F{travel_f}")
    add(f"G1 X{x0:.3f} Y{y0:.3f} F{travel_f}")
    add(f"G1 Z{z:.3f} F{travel_f}")
    add(_unretract())
    add("")

    if infill_only:
        # ── Infill only: full square coverage, no perimeter walls ─────────────
        add("; Infill only — continuous zig-zag, full square coverage")
        y     = y0
        right = True
        while True:
            tx = x1 if right else x0
            add(f"G1 X{tx:.3f} Y{y:.3f} E{_e(x1 - x0):.5f} F{speed_f}")
            next_y = y + extrusion_w
            if next_y > y1 + 0.001:
                break
            add(f"G1 X{tx:.3f} Y{next_y:.3f} E{_e(0, extrusion_w):.5f} F{speed_f}")
            y     = next_y
            right = not right
        add("")

    else:
        # ── Perimeter 1 (outer) — head already at (x0, y0) from initial travel ──
        add("; Perimeter 1/2 — outer")
        add(f"G1 X{x1:.3f} Y{y0:.3f} E{_e(x1 - x0):.5f} F{speed_f}")       # bottom →
        add(f"G1 X{x1:.3f} Y{y1:.3f} E{_e(0, y1 - y0):.5f} F{speed_f}")    # right  ↑
        add(f"G1 X{x0:.3f} Y{y1:.3f} E{_e(x0 - x1):.5f} F{speed_f}")       # top    ←
        add(f"G1 X{x0:.3f} Y{y0:.3f} E{_e(0, y0 - y1):.5f} F{speed_f}")    # left   ↓
        add("")

        # ── Travel to perimeter 2 start (inset by one extrusion width) ─────────
        px0_2 = x0 + extrusion_w
        py0_2 = y0 + extrusion_w
        px1_2 = x1 - extrusion_w
        py1_2 = y1 - extrusion_w
        add(_retract())
        add(f"G1 X{px0_2:.3f} Y{py0_2:.3f} F{travel_f}")
        add(_unretract())
        add("")

        # ── Perimeter 2 (inner) ────────────────────────────────────────────────
        add("; Perimeter 2/2 — inner")
        add(f"G1 X{px1_2:.3f} Y{py0_2:.3f} E{_e(px1_2 - px0_2):.5f} F{speed_f}")  # bottom →
        add(f"G1 X{px1_2:.3f} Y{py1_2:.3f} E{_e(0, py1_2 - py0_2):.5f} F{speed_f}")  # right ↑
        add(f"G1 X{px0_2:.3f} Y{py1_2:.3f} E{_e(px0_2 - px1_2):.5f} F{speed_f}")  # top ←
        add(f"G1 X{px0_2:.3f} Y{py0_2:.3f} E{_e(0, py0_2 - py1_2):.5f} F{speed_f}")  # left ↓
        add("")

        # ── Infill: continuous zig-zag inside inner perimeter ─────────────────
        add("; Infill — continuous zig-zag, 100% density")
        margin = extrusion_w * 2.0
        ix0 = x0 + margin
        ix1 = x1 - margin
        iy0 = y0 + margin
        iy1 = y1 - margin
        add(_retract())
        add(f"G1 X{ix0:.3f} Y{iy0:.3f} F{travel_f}")
        add(_unretract())
        add("")

        y     = iy0
        right = True
        while True:
            tx = ix1 if right else ix0
            add(f"G1 X{tx:.3f} Y{y:.3f} E{_e(ix1 - ix0):.5f} F{speed_f}")
            next_y = y + extrusion_w
            if next_y > iy1 + 0.001:
                break
            add(f"G1 X{tx:.3f} Y{next_y:.3f} E{_e(0, extrusion_w):.5f} F{speed_f}")
            y     = next_y
            right = not right
        add("")

    # ── End GCode ─────────────────────────────────────────────────────────────
    add(_retract())
    add(f"G1 Z5.0 F{travel_f}")
    add(f"G1 X{(x0 + x1) / 2:.3f} Y{(y0 + y1) / 2:.3f} F{travel_f}")
    add("")
    add("; --- END GCODE ---")
    if end_gc.strip():
        L.extend(end_gc.splitlines())
    elif firmware in ("marlin", "rrf"):
        add("M104 S0")
        add("M140 S0")
        add("M84")
    else:
        add("END_PRINT")

    return "\n".join(L)


# ── Background worker ─────────────────────────────────────────────────────────

class _Worker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, cfg, output_file, custom_gcode, infill_only=False, parent=None):
        super().__init__(parent)
        self.cfg          = cfg
        self.output_file  = output_file
        self.custom_gcode = custom_gcode
        self.infill_only  = infill_only

    def run(self):
        try:
            self.progress.emit(20, "Generating test square…")
            gcode = _generate_test_square(self.cfg, self.custom_gcode, self.infill_only)
            self.progress.emit(80, "Saving…")
            with open(self.output_file, "w") as f:
                f.write(gcode)
            self.progress.emit(100, "Done!")
            self.finished.emit(self.output_file)
        except Exception as e:
            self.error.emit(f"{e}\n\n{traceback.format_exc()}")


# ── Dialog ────────────────────────────────────────────────────────────────────

class TestLayerDialog(QDialog):
    """
    Compact dialog for generating a first-layer calibration square.
    Settings here are independent of the main slicer — adjust freely.
    """

    # Emitted when a GCode file has been generated (main window can load it)
    gcode_ready = pyqtSignal(str)

    def __init__(self, main_cfg: dict, custom_gcode: dict, app_settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Test First Layer — 30×30 mm Square")
        self.setMinimumWidth(360)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._main_cfg     = main_cfg
        self._custom_gcode = custom_gcode
        self._app_settings = app_settings
        self._worker       = None
        self._output_file  = ""
        self._send_after   = False
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        info = QLabel(
            "Prints a 30×30 mm filled square — one layer thick — to calibrate\n"
            "first-layer adhesion, squish, and extrusion rate.\n"
            "Settings here do not affect the main slicer."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #99a8bb; font-size: 11px;")
        lay.addWidget(info)

        # ── Settings form ─────────────────────────────────────────────────────
        g = QGroupBox("Settings")
        f = QFormLayout(g)
        f.setSpacing(6)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        ps = self._main_cfg.get("print_settings", {})
        pr = self._main_cfg.get("printer", {})

        def _dbl(key, label, default, lo, hi, step, tip="", source=None):
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setDecimals(len(str(step).split(".")[-1]) if "." in str(step) else 0)
            src = source or ps
            sb.setValue(float(src.get(key, default)))
            if tip:
                sb.setToolTip(tip)
            f.addRow(label, sb)
            return sb

        def _int(key, label, default, lo, hi, tip="", source=None):
            sb = QSpinBox()
            sb.setRange(lo, hi)
            src = source or ps
            sb.setValue(int(src.get(key, default)))
            if tip:
                sb.setToolTip(tip)
            f.addRow(label, sb)
            return sb

        self._layer_height = _dbl("layer_height",         "Layer height (mm):",    0.4,  0.05, 1.5,  0.05)
        self._squish       = _dbl("first_layer_squish",   "1st layer squish (%):", 15.0, 0.0,  80.0, 0.5)
        self._speed_pct    = _int("first_layer_speed_pct","First layer speed (%):", 50,   10,   200)
        self._print_speed  = _int("print_speed",           "Print speed (mm/s):",   35,    5,   300)
        self._e_mult       = _dbl("extrusion_multiplier", "Extrusion rate:",        1.0,  0.5,  2.0,  0.01,
                                  tip="1.0 = 100% (normal). Increase if under-extruding, decrease if over.")
        self._nozzle_temp  = _int("nozzle_temp",  "Nozzle temp (°C):", 260, 150, 400, source=pr)
        self._bed_temp     = _int("bed_temp",     "Bed temp (°C):",     65,   0, 130, source=pr)

        # Infill-only checkbox (spans both columns via a QWidget row)
        self._infill_only_cb = QCheckBox("Infill only (no perimeter walls)")
        self._infill_only_cb.setToolTip(
            "Skip perimeter loops — fills the full 30×30 mm square with solid infill only.\n"
            "Useful for testing pure adhesion without wall seams."
        )
        f.addRow("", self._infill_only_cb)

        lay.addWidget(g)

        # ── Progress ──────────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar::chunk { background: #2a5298; border-radius: 2px; }"
            "QProgressBar { border-radius: 2px; background: #333; }"
        )
        lay.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #99a8bb; font-size: 10px;")
        lay.addWidget(self._status)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._gen_btn = QPushButton("Generate & Save")
        self._gen_btn.setFixedHeight(32)
        self._gen_btn.setStyleSheet(
            "QPushButton { background: #2a5298; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3a62a8; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )
        self._gen_btn.clicked.connect(lambda: self._start(send=False))

        self._send_btn = QPushButton("Generate & Send")
        self._send_btn.setFixedHeight(32)
        self._send_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #2ecc71; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )
        self._send_btn.clicked.connect(lambda: self._start(send=True))

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(32)
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._gen_btn)
        btn_row.addWidget(self._send_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

    def _collect_cfg(self) -> dict:
        cfg = {
            "print_settings": dict(self._main_cfg.get("print_settings", {})),
            "printer":        dict(self._main_cfg.get("printer", {})),
            "printer_profile": dict(self._main_cfg.get("printer_profile", {})),
        }
        cfg["print_settings"].update({
            "layer_height":          self._layer_height.value(),
            "first_layer_squish":    self._squish.value(),
            "first_layer_speed_pct": self._speed_pct.value(),
            "print_speed":           self._print_speed.value(),
            "extrusion_multiplier":  self._e_mult.value(),
        })
        cfg["printer"].update({
            "nozzle_temp": self._nozzle_temp.value(),
            "bed_temp":    self._bed_temp.value(),
        })
        return cfg

    def _start(self, send: bool):
        if self._worker and self._worker.isRunning():
            return
        self._send_after = send

        ts = datetime.now().strftime("%d%m%y_%H%M")
        out_dir = self._app_settings.get("output_dir", str(Path.home() / "meshy_output"))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        output_file = str(Path(out_dir) / f"test_first_layer_{ts}.gcode")

        self._gen_btn.setEnabled(False)
        self._send_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status.setText("Working…")

        infill_only = self._infill_only_cb.isChecked()
        self._worker = _Worker(
            self._collect_cfg(), output_file, self._custom_gcode,
            infill_only=infill_only, parent=self
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._status.setText(msg)

    def _on_finished(self, path: str):
        self._output_file = path
        self._gen_btn.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._status.setText(f"Saved: {Path(path).name}")
        self.gcode_ready.emit(path)
        if self._send_after:
            self._do_send(path)

    def _on_error(self, msg: str):
        self._gen_btn.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText("Error!")
        QMessageBox.critical(self, "Test Layer Error", msg[:600])

    def _do_send(self, path: str):
        try:
            from klipper.moonraker import MoonrakerClient
            settings  = self._app_settings
            profiles  = settings.get("printer_profiles", [{}])
            idx       = settings.get("active_profile", 0)
            profile   = profiles[idx] if idx < len(profiles) else (profiles[0] if profiles else {})
            ip        = profile.get("printer_ip",   "192.168.1.65")
            port      = int(profile.get("printer_port", 80))
            client    = MoonrakerClient(ip, port)
            remote    = client.upload_file(path)
            if remote:
                reply = QMessageBox.question(
                    self, "Start Print?",
                    "File uploaded successfully.\nStart printing the test square now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    client.start_print(remote)
            else:
                QMessageBox.warning(
                    self, "Upload Failed",
                    "Could not upload to printer.\nCheck printer connection in App Settings.",
                )
        except Exception as e:
            QMessageBox.critical(self, "Send Error", str(e))

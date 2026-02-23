"""
MeshyGen main window.

Layout (horizontal splitter):
  LEFT  : Settings panel (scrollable)
  RIGHT : Vertical splitter
           TOP    : STL 3D viewer
           BOTTOM : Vertical splitter
                     TOP    : Path preview (2D iso / 3D snap / 3D full)
                     BOTTOM : Log / progress panel

Bottom toolbar: Load STL | Preview | Generate | Send to Printer | App Settings
Status bar: file path | Klipper status
"""

import json
import logging
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QFileDialog, QStatusBar,
    QToolBar, QMessageBox, QComboBox, QTabWidget, QFrame,
    QApplication, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QIcon, QFont, QKeySequence, QColor, QTextCursor

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.widgets.stl_viewer    import STLViewer
from gui.widgets.path_preview  import PathPreviewWidget
from gui.widgets.settings_panel import SettingsPanel
from gui.workers.slicer_worker  import SlicerWorker
from gui.workers.preview_worker import PreviewWorker
from gui.dialogs.app_settings   import AppSettingsDialog, load_app_settings
from gui.dialogs.print_history  import PrintHistoryDialog
import db.print_db as pdb


# ── Qt-safe logging bridge ────────────────────────────────────────────────────

class _LogSignaller(QObject):
    """Emits log records as Qt signals (thread-safe bridge to the UI)."""
    record = pyqtSignal(str, str)  # (level, message)


class _QtLogHandler(logging.Handler):
    """Logging handler that routes records to a Qt signal."""

    def __init__(self):
        super().__init__()
        self.signaller = _LogSignaller()

    def emit(self, record: logging.LogRecord):
        level = record.levelname
        msg   = self.format(record)
        try:
            self.signaller.record.emit(level, msg)
        except Exception:
            pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeshyGen — Mesh Vase Slicer")
        self.resize(1280, 860)

        self._stl_path: str = ""
        self._gcode_path: str = ""
        self._app_settings: dict = load_app_settings()
        self._slicer_worker: SlicerWorker = None
        self._preview_worker: PreviewWorker = None
        self._klipper_timer = QTimer(self)
        self._klipper_timer.setInterval(5000)
        self._klipper_timer.timeout.connect(self._poll_klipper)

        # Logging bridge
        self._log_handler = _QtLogHandler()
        self._log_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        self._log_handler.signaller.record.connect(self._append_log)
        logging.getLogger().addHandler(self._log_handler)

        self._build_ui()
        self._build_menu()
        self._update_controls()
        self._klipper_timer.start()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Main splitter ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        # LEFT: settings panel
        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_changed.connect(self._on_settings_changed)
        splitter.addWidget(self.settings_panel)

        # RIGHT: vertical splitter (STL viewer top, preview + log bottom)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(right_splitter)
        splitter.setSizes([290, 990])

        # STL viewer
        self.stl_viewer = STLViewer()
        self.stl_viewer.file_dropped.connect(self._load_stl)
        right_splitter.addWidget(self.stl_viewer)

        # ── Bottom of right panel: preview + log ─────────────────────────────
        bottom_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(bottom_splitter)
        right_splitter.setSizes([480, 320])

        # Path preview panel
        preview_container = QWidget()
        pc_layout = QVBoxLayout(preview_container)
        pc_layout.setContentsMargins(0, 2, 0, 0)
        pc_layout.setSpacing(4)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(6, 0, 6, 0)
        mode_lbl = QLabel("Preview:")
        mode_lbl.setStyleSheet("color: #aaa;")
        self.preview_mode_combo = QComboBox()
        self.preview_mode_combo.addItems(["2D Isometric", "3D Snapshot", "3D Full"])
        self.preview_mode_combo.currentIndexChanged.connect(self._on_preview_mode_change)
        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.setFixedHeight(24)
        refresh_btn.clicked.connect(self._refresh_preview)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self.preview_mode_combo, stretch=1)
        mode_row.addWidget(refresh_btn)
        pc_layout.addLayout(mode_row)

        self.path_preview = PathPreviewWidget()
        pc_layout.addWidget(self.path_preview, stretch=1)
        bottom_splitter.addWidget(preview_container)

        # Log panel
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(6, 2, 6, 4)
        log_layout.setSpacing(2)

        log_header = QHBoxLayout()
        log_title = QLabel("Progress / Log")
        log_title.setStyleSheet("color: #778; font-size: 11px; font-weight: bold;")
        clear_log_btn = QPushButton("Clear")
        clear_log_btn.setFixedHeight(18)
        clear_log_btn.setFixedWidth(48)
        clear_log_btn.setStyleSheet("font-size: 10px; padding: 0 4px;")
        clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(120)
        self.log_text.setStyleSheet(
            "QTextEdit { background: #0e0f14; color: #8da; border: 1px solid #2a2d3a; "
            "border-radius: 3px; font-family: Menlo, Monaco, monospace; font-size: 11px; }"
        )
        log_layout.addWidget(self.log_text)
        bottom_splitter.addWidget(log_container)
        bottom_splitter.setSizes([220, 140])

        # ── Bottom toolbar ───────────────────────────────────────────────────
        toolbar_frame = QFrame()
        toolbar_frame.setFixedHeight(52)
        toolbar_frame.setStyleSheet("background: #1e2028; border-top: 1px solid #333;")
        tb_layout = QHBoxLayout(toolbar_frame)
        tb_layout.setContentsMargins(10, 6, 10, 6)
        tb_layout.setSpacing(8)

        self.load_btn = QPushButton("Load STL…")
        self.load_btn.setFixedHeight(36)
        self.load_btn.clicked.connect(self._pick_stl)

        self.preview_btn = QPushButton("⚡ Preview")
        self.preview_btn.setFixedHeight(36)
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self._refresh_preview)

        self.settings_btn = QPushButton("⚙ App Settings")
        self.settings_btn.setFixedHeight(36)
        self.settings_btn.setToolTip("Edit printer settings, start/end GCode templates, output directory")
        self.settings_btn.clicked.connect(self._open_settings)

        self.generate_btn = QPushButton("▶  Generate GCode")
        self.generate_btn.setFixedHeight(36)
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._generate_gcode)
        self.generate_btn.setStyleSheet(
            "QPushButton { background: #2a5298; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3a62a8; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )

        self.send_btn = QPushButton("◆  Send to Printer")
        self.send_btn.setFixedHeight(36)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_to_printer)
        self.send_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #2ecc71; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar::chunk { background: #2a5298; border-radius: 3px; }"
            "QProgressBar { border-radius: 3px; background: #333; }"
        )

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self.progress_label.setMinimumWidth(200)

        tb_layout.addWidget(self.load_btn)
        tb_layout.addWidget(self.preview_btn)
        tb_layout.addWidget(self.settings_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self.progress_label)
        tb_layout.addWidget(self.generate_btn)
        tb_layout.addWidget(self.send_btn)

        main_layout.addWidget(toolbar_frame)
        main_layout.addWidget(self.progress_bar)

        # ── Status bar ───────────────────────────────────────────────────────
        self.status_bar = self.statusBar()
        self.status_file_lbl = QLabel("No file loaded")
        self.status_klipper_lbl = QLabel("Klipper: checking…")
        self.status_klipper_lbl.setStyleSheet("color: #aaa;")
        self.status_bar.addWidget(self.status_file_lbl, stretch=1)
        self.status_bar.addPermanentWidget(self.status_klipper_lbl)

    def _build_menu(self):
        menubar = self.menuBar()

        # File
        file_menu = menubar.addMenu("&File")
        open_act = QAction("Open STL…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._pick_stl)
        reveal_act = QAction("Show GCode in Finder", self)
        reveal_act.triggered.connect(self._reveal_gcode)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        file_menu.addAction(reveal_act)

        # Settings
        settings_menu = menubar.addMenu("&Settings")
        prefs_act = QAction("App Settings…", self)
        prefs_act.setShortcut(QKeySequence("Ctrl+,"))
        prefs_act.triggered.connect(self._open_settings)
        settings_menu.addAction(prefs_act)

        # History
        history_menu = menubar.addMenu("&History")
        hist_act = QAction("Print History…", self)
        hist_act.triggered.connect(self._open_history)
        history_menu.addAction(hist_act)

    # ── File loading ─────────────────────────────────────────────────────────

    def _pick_stl(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STL file", "", "STL Files (*.stl);;All (*)"
        )
        if path:
            self._load_stl(path)

    def _load_stl(self, path: str):
        self._stl_path = path
        self._gcode_path = ""
        self.path_preview.clear()
        self.stl_viewer.load_stl(path)
        self.status_file_lbl.setText(Path(path).name)
        self._update_controls()
        self._append_log("INFO", f"Loaded {Path(path).name}")

    # ── Log panel ────────────────────────────────────────────────────────────

    @pyqtSlot(str, str)
    def _append_log(self, level: str, msg: str):
        colors = {
            "DEBUG":    "#557",
            "INFO":     "#8da",
            "WARNING":  "#da8",
            "ERROR":    "#e74",
            "CRITICAL": "#f44",
        }
        color = colors.get(level, "#8da")
        self.log_text.append(f'<span style="color:{color};">{msg}</span>')
        # Auto-scroll to bottom
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _clear_log(self):
        self.log_text.clear()

    # ── Preview ──────────────────────────────────────────────────────────────

    def _on_preview_mode_change(self, idx):
        modes = ["2d", "3d_snap", "3d_full"]
        self.path_preview.set_mode(modes[idx])

    def _refresh_preview(self, snap_res=0.15):
        if not self._stl_path:
            return
        if self._preview_worker and self._preview_worker.isRunning():
            return

        modes = ["2d", "3d_snap", "3d_full"]
        mode = modes[self.preview_mode_combo.currentIndex()]
        overrides = self.settings_panel.get_config_overrides()

        # Higher quality snap preview: use more samples to make waves visible
        # snap_res 0.15 means 15% of full ppd, but Rust anti-alias guard will
        # bump it to meet target_samples_per_wave (set to 8 for faster preview)
        if mode == "3d_snap":
            snap_res = 0.15
        elif mode == "3d_full":
            snap_res = 1.0
        else:
            snap_res = 0.3

        self.preview_btn.setEnabled(False)
        self._set_progress(0, "Generating preview…")
        self.progress_bar.setVisible(True)
        self._append_log("INFO", f"Starting {mode} preview…")

        self._preview_worker = PreviewWorker(
            self._stl_path, overrides, mode=mode,
            snap_resolution=snap_res,
            target_samples_per_wave=8,  # 8 samples/wave is enough to see pattern
            parent=self,
        )
        self._preview_worker.layer_data_ready.connect(self._on_layer_preview)
        self._preview_worker.path_data_ready.connect(self._on_path_preview)
        self._preview_worker.error.connect(self._on_preview_error)
        self._preview_worker.finished.connect(self._on_preview_finished)
        self._preview_worker.start()

    @pyqtSlot(list)
    def _on_layer_preview(self, layers):
        self.path_preview.set_layer_data(layers, label=f"{len(layers)} layers")

    @pyqtSlot(object)
    def _on_path_preview(self, pts):
        self.path_preview.set_path_data(pts, label=f"{len(pts):,} path points")

    @pyqtSlot()
    def _on_preview_finished(self):
        self.preview_btn.setEnabled(bool(self._stl_path))
        self.progress_bar.setVisible(False)
        self._set_progress(0, "")
        self._append_log("INFO", "Preview ready.")

    @pyqtSlot(str)
    def _on_preview_error(self, msg):
        self.preview_btn.setEnabled(bool(self._stl_path))
        self.progress_bar.setVisible(False)
        self._set_progress(0, "")
        self._append_log("ERROR", f"Preview error: {msg[:200]}")

    # ── GCode generation ─────────────────────────────────────────────────────

    def _generate_gcode(self):
        if not self._stl_path:
            return
        if self._slicer_worker and self._slicer_worker.isRunning():
            return

        overrides = self.settings_panel.get_config_overrides()
        settings  = self._app_settings

        custom_gcode = {
            "start_gcode": settings.get("start_gcode", ""),
            "end_gcode":   settings.get("end_gcode",   ""),
        }

        stl_stem  = Path(self._stl_path).stem[:20]
        ms        = overrides.get("mesh_settings", {})
        amp       = ms.get("wave_amplitude", 2.0)
        ps        = overrides.get("print_settings", {})
        mode_tag  = "vase" if ps.get("vase_mode") else "mesh"
        from datetime import datetime
        ts = datetime.now().strftime("%d%m%y_%H%M")
        fname = f"{stl_stem}_{amp:.0f}a_{mode_tag}_{ts}.gcode"

        output_dir = settings.get("output_dir", str(Path(self._stl_path).parent / "output"))
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_file = str(Path(output_dir) / fname)

        self.generate_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._clear_log()
        self._append_log("INFO", f"Starting generation: {Path(self._stl_path).name}")

        self._slicer_worker = SlicerWorker(
            self._stl_path, overrides,
            output_file=output_file,
            custom_gcode=custom_gcode,
            parent=self,
        )
        self._slicer_worker.progress.connect(self._on_slicer_progress)
        self._slicer_worker.finished.connect(self._on_slicer_finished)
        self._slicer_worker.error.connect(self._on_slicer_error)
        self._slicer_worker.start()

    @pyqtSlot(int, str)
    def _on_slicer_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self._set_progress(pct, msg)
        self._append_log("INFO", f"[{pct}%] {msg}")

    @pyqtSlot(str)
    def _on_slicer_finished(self, gcode_path: str):
        self._gcode_path = gcode_path
        self.progress_bar.setValue(100)
        QTimer.singleShot(800, lambda: self.progress_bar.setVisible(False))
        self._set_progress(100, "Done!")
        self.generate_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_file_lbl.setText(f"Generated: {Path(gcode_path).name}")
        self._append_log("INFO", f"GCode saved: {Path(gcode_path).name}")

        # Save to print history
        overrides = self.settings_panel.get_config_overrides()
        ip = self._app_settings.get("printer_ip", "")
        pdb.add_job(self._stl_path, gcode_path, overrides, printer_ip=ip)

        # Auto-refresh preview with the generated toolpath
        QTimer.singleShot(300, self._refresh_preview)

        QMessageBox.information(
            self, "Done",
            f"GCode saved:\n{Path(gcode_path).name}\n\nPreview updated. Click 'Send to Printer' to upload."
        )

    @pyqtSlot(str)
    def _on_slicer_error(self, msg):
        self.progress_bar.setVisible(False)
        self._set_progress(0, "")
        self.generate_btn.setEnabled(bool(self._stl_path))
        self._append_log("ERROR", f"Slicer error: {msg[:500]}")
        QMessageBox.critical(self, "Slicer Error", msg[:1000])

    # ── Klipper integration ──────────────────────────────────────────────────

    def _send_to_printer(self):
        if not self._gcode_path:
            QMessageBox.warning(self, "No GCode", "Generate GCode first.")
            return

        ip   = self._app_settings.get("printer_ip", "192.168.1.65")
        port = self._app_settings.get("printer_port", 80)

        from klipper.moonraker import MoonrakerClient
        client = MoonrakerClient(ip, port)

        if not client.check_connection():
            QMessageBox.warning(
                self, "Not Connected",
                f"Cannot reach {ip}:{port}.\nCheck IP in App Settings (⚙ App Settings button)."
            )
            return

        self._set_progress(0, f"Uploading to {ip}…")
        filename = client.upload_file(self._gcode_path)
        if not filename:
            QMessageBox.critical(self, "Upload Failed", "File upload failed. Check Moonraker logs.")
            self._set_progress(0, "")
            return

        jobs = pdb.get_all_jobs()
        if jobs and Path(jobs[0].get("gcode_file", "")).name == Path(self._gcode_path).name:
            pdb.update_status(jobs[0]["id"], "sent")

        reply = QMessageBox.question(
            self, "Uploaded",
            f"File uploaded: {filename}\n\nStart printing now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            ok = client.start_print(filename)
            if ok:
                self._set_progress(0, f"Printing {Path(filename).name} on {ip}")
                if jobs:
                    pdb.update_status(jobs[0]["id"], "printing")
            else:
                QMessageBox.warning(self, "Start Failed", "Could not start print. Check Klipper.")
        else:
            self._set_progress(0, f"Uploaded to {ip} — ready to print")

    @pyqtSlot()
    def _poll_klipper(self):
        ip   = self._app_settings.get("printer_ip", "192.168.1.65")
        port = self._app_settings.get("printer_port", 80)
        from klipper.moonraker import MoonrakerClient
        client = MoonrakerClient(ip, port)
        state = client.get_print_state()
        if state == "unknown":
            self.status_klipper_lbl.setText(f"Klipper: {ip} offline")
            self.status_klipper_lbl.setStyleSheet("color: #e74c3c;")
        elif state in ("printing",):
            pct = int(client.get_progress() * 100)
            self.status_klipper_lbl.setText(f"Klipper: printing {pct}%")
            self.status_klipper_lbl.setStyleSheet("color: #27ae60;")
        elif state == "complete":
            self.status_klipper_lbl.setText(f"Klipper: complete")
            self.status_klipper_lbl.setStyleSheet("color: #2ecc71;")
        else:
            self.status_klipper_lbl.setText(f"Klipper: {ip} — {state or 'idle'}")
            self.status_klipper_lbl.setStyleSheet("color: #aaa;")

    # ── Dialogs ──────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = AppSettingsDialog(self)
        if dlg.exec():
            self._app_settings = load_app_settings()

    def _open_history(self):
        dlg = PrintHistoryDialog(self)
        dlg.exec()

    def _reveal_gcode(self):
        if self._gcode_path and Path(self._gcode_path).exists():
            subprocess.Popen(["open", "-R", self._gcode_path])

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _on_settings_changed(self):
        pass  # Could auto-preview here if desired

    def _update_controls(self):
        has_file = bool(self._stl_path)
        self.preview_btn.setEnabled(has_file)
        self.generate_btn.setEnabled(has_file)
        self.send_btn.setEnabled(bool(self._gcode_path))

    def _set_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.progress_label.setText(msg)

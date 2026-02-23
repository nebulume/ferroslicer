"""
MeshyGen main window.

Layout (horizontal splitter):
  LEFT  : Settings panel (always visible, scrollable)
  RIGHT : Vertical splitter
           TOP    : QTabWidget
                     Tab 0 "Files"     — file browser (default on open)
                     Tab 1 "Model"     — STL 3D viewer
                     Tab 2 "Generated" — toolpath 3D viewer
           BOTTOM : Log / progress panel

Bottom toolbar: Load STL | App Settings | GCode Library | … | Generate | 📁 Reveal | Send
Status bar: file path | Klipper status
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QFileDialog, QStatusBar,
    QMessageBox, QTabWidget, QFrame, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, QMimeData, QUrl, pyqtSlot, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import (
    QAction, QFont, QKeySequence, QColor, QTextCursor,
    QDrag, QPixmap, QPainter,
)

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.widgets.stl_viewer      import STLViewer
from gui.widgets.toolpath_viewer import ToolpathViewer
from gui.widgets.settings_panel  import SettingsPanel
from gui.widgets.file_browser    import FileBrowserWidget
from gui.workers.slicer_worker   import SlicerWorker
from gui.dialogs.app_settings    import AppSettingsDialog, load_app_settings
from gui.dialogs.print_history   import PrintHistoryDialog
from gui.dialogs.gcode_library   import GCodeLibraryDialog
import db.print_db as pdb


# ── Qt-safe logging bridge ────────────────────────────────────────────────────

class _LogSignaller(QObject):
    record = pyqtSignal(str, str)


class _QtLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.signaller = _LogSignaller()

    def emit(self, record: logging.LogRecord):
        try:
            self.signaller.record.emit(record.levelname, self.format(record))
        except Exception:
            pass


# ── Draggable GCode chip ──────────────────────────────────────────────────────

class _GCodeChip(QLabel):
    """
    A small label that displays the last generated GCode filename.
    • Drag it to any Finder window, Desktop, or USB drive to copy the file.
    • Double-click to reveal the file in Finder / file manager.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: str = ""
        self._drag_start: QPoint = QPoint()
        self._set_empty()
        self.setFixedHeight(28)
        self.setMinimumWidth(80)
        self.setMaximumWidth(260)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.setStyleSheet(
            "QLabel { color: #99b; font-size: 11px; padding: 0 6px;"
            " border: 1px solid #2a3040; border-radius: 4px;"
            " background: #13151d; }"
        )

    def set_path(self, path: str):
        self._path = path
        name = Path(path).name
        display = name if len(name) <= 32 else name[:29] + "…"
        self.setText(f"↗ {display}")
        self.setToolTip(f"Drag to copy  •  double-click to reveal\n{path}")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def clear_path(self):
        self._path = ""
        self._set_empty()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _set_empty(self):
        self.setText("no GCode yet")
        self.setToolTip("")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if not self._path:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._drag_start).manhattanLength() < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._path)])
        drag.setMimeData(mime)
        # Small pixmap as drag icon
        pix = QPixmap(160, 22)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setFont(QFont("Helvetica", 10))
        p.setPen(QColor(180, 190, 220))
        p.drawText(4, 16, f"📄 {Path(self._path).name[:24]}")
        p.end()
        drag.setPixmap(pix)
        drag.exec(Qt.DropAction.CopyAction)

    def mouseDoubleClickEvent(self, event):
        if self._path and Path(self._path).exists():
            _reveal_file(self._path)


def _reveal_file(path: str):
    """Open the parent folder with the file selected (cross-platform)."""
    p = Path(path)
    if not p.exists():
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(p.parent)])
    else:
        subprocess.Popen(["explorer", f"/select,{p}"])


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    # Tab indices
    TAB_FILES     = 0
    TAB_MODEL     = 1
    TAB_GENERATED = 2

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeshyGen — Mesh Vase Slicer")
        self.resize(1280, 860)

        self._stl_path:    str = ""
        self._gcode_path:  str = ""
        self._app_settings: dict = load_app_settings()
        self._slicer_worker: SlicerWorker = None
        self._klipper_timer = QTimer(self)
        self._klipper_timer.setInterval(5000)
        self._klipper_timer.timeout.connect(self._poll_klipper)

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

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter, stretch=1)

        # LEFT: settings panel
        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_changed.connect(self._on_settings_changed)
        main_splitter.addWidget(self.settings_panel)

        # RIGHT: vertical splitter
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([290, 990])

        # ── Preview tabs ──────────────────────────────────────────────────────
        self.preview_tabs = QTabWidget()
        self.preview_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.preview_tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                padding: 7px 20px;
                background: #1a1d26;
                color: #888;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #22273a;
                color: #dde;
                border-bottom: 2px solid #2a5298;
            }
            QTabBar::tab:hover:!selected { background: #1f2230; }
        """)
        right_splitter.addWidget(self.preview_tabs)

        # Tab 0: Files — file browser (shown on startup)
        self.file_browser = FileBrowserWidget()
        self.file_browser.file_hovered.connect(self._on_file_hovered)
        self.file_browser.file_opened.connect(self._load_stl)
        self.preview_tabs.addTab(self.file_browser, "  Files  ")

        # Tab 1: Model — STL viewer
        self.stl_viewer = STLViewer()
        self.stl_viewer.file_dropped.connect(self._load_stl)
        self.preview_tabs.addTab(self.stl_viewer, "  Model  ")

        # Tab 2: Generated — toolpath viewer
        self.toolpath_viewer = ToolpathViewer()
        self.preview_tabs.addTab(self.toolpath_viewer, "  Generated  ")

        # Start on the Files tab
        self.preview_tabs.setCurrentIndex(self.TAB_FILES)

        # ── Log panel ─────────────────────────────────────────────────────────
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(6, 4, 6, 4)
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
        self.log_text.setStyleSheet(
            "QTextEdit { background: #0e0f14; color: #8da; border: 1px solid #2a2d3a;"
            " border-radius: 3px; font-family: Menlo, Monaco, monospace; font-size: 11px; }"
        )
        log_layout.addWidget(self.log_text)

        right_splitter.addWidget(log_container)
        right_splitter.setSizes([660, 140])

        # ── Bottom toolbar ────────────────────────────────────────────────────
        toolbar_frame = QFrame()
        toolbar_frame.setFixedHeight(52)
        toolbar_frame.setStyleSheet("background: #1e2028; border-top: 1px solid #333;")
        tb_layout = QHBoxLayout(toolbar_frame)
        tb_layout.setContentsMargins(10, 6, 10, 6)
        tb_layout.setSpacing(8)

        self.load_btn = QPushButton("Load STL…")
        self.load_btn.setFixedHeight(36)
        self.load_btn.clicked.connect(self._pick_stl)

        self.settings_btn = QPushButton("⚙ App Settings")
        self.settings_btn.setFixedHeight(36)
        self.settings_btn.setToolTip("Edit printer settings, start/end GCode templates, output dir")
        self.settings_btn.clicked.connect(self._open_settings)

        self.library_btn = QPushButton("◫  GCode Library")
        self.library_btn.setFixedHeight(36)
        self.library_btn.setToolTip("Browse generated GCode files (Ctrl+L)")
        self.library_btn.clicked.connect(self._open_library)
        self.library_btn.setStyleSheet(
            "QPushButton { background: #2a3a52; color: #ccd; border-radius: 4px; }"
            "QPushButton:hover { background: #354a66; }"
        )

        self.generate_btn = QPushButton("▶  Generate GCode")
        self.generate_btn.setFixedHeight(36)
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._generate_gcode)
        self.generate_btn.setStyleSheet(
            "QPushButton { background: #2a5298; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3a62a8; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )

        # GCode chip — draggable filename, double-click reveals in Finder
        self._gcode_chip = _GCodeChip()

        # Reveal button — opens parent folder with file selected
        self.reveal_btn = QPushButton("📁")
        self.reveal_btn.setFixedSize(36, 36)
        self.reveal_btn.setToolTip("Show GCode in Finder / file manager")
        self.reveal_btn.setEnabled(False)
        self.reveal_btn.clicked.connect(self._reveal_gcode)

        self.send_btn = QPushButton("◆  Send to Printer")
        self.send_btn.setFixedHeight(36)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_to_printer)
        self.send_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #2ecc71; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self.progress_label.setMinimumWidth(160)

        tb_layout.addWidget(self.load_btn)
        tb_layout.addWidget(self.settings_btn)
        tb_layout.addWidget(self.library_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self.progress_label)
        tb_layout.addWidget(self.generate_btn)
        tb_layout.addWidget(self._gcode_chip)
        tb_layout.addWidget(self.reveal_btn)
        tb_layout.addWidget(self.send_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar::chunk { background: #2a5298; border-radius: 2px; }"
            "QProgressBar { border-radius: 2px; background: #333; }"
        )

        main_layout.addWidget(toolbar_frame)
        main_layout.addWidget(self.progress_bar)

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_bar = self.statusBar()
        self.status_file_lbl = QLabel("No file loaded")
        self.status_klipper_lbl = QLabel("Klipper: checking…")
        self.status_klipper_lbl.setStyleSheet("color: #aaa;")
        self.status_bar.addWidget(self.status_file_lbl, stretch=1)
        self.status_bar.addPermanentWidget(self.status_klipper_lbl)

    def _build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        open_act = QAction("Open STL…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._pick_stl)
        reveal_act = QAction("Show GCode in Finder", self)
        reveal_act.triggered.connect(self._reveal_gcode)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        file_menu.addAction(reveal_act)

        settings_menu = menubar.addMenu("&Settings")
        prefs_act = QAction("App Settings…", self)
        prefs_act.setShortcut(QKeySequence("Ctrl+,"))
        prefs_act.triggered.connect(self._open_settings)
        settings_menu.addAction(prefs_act)

        history_menu = menubar.addMenu("&History")
        lib_act = QAction("GCode Library…", self)
        lib_act.setShortcut(QKeySequence("Ctrl+L"))
        lib_act.triggered.connect(self._open_library)
        history_menu.addAction(lib_act)
        hist_act = QAction("Print History…", self)
        hist_act.triggered.connect(self._open_history)
        history_menu.addAction(hist_act)

    # ── File loading ──────────────────────────────────────────────────────────

    def _pick_stl(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STL file", "", "STL Files (*.stl);;All (*)"
        )
        if path:
            self._load_stl(path)

    def _load_stl(self, path: str):
        self._stl_path  = path
        self._gcode_path = ""
        self.toolpath_viewer.clear()
        self.stl_viewer.load_stl(path)
        self.preview_tabs.setCurrentIndex(self.TAB_MODEL)
        self.status_file_lbl.setText(Path(path).name)
        # Navigate file browser to parent dir so the file is visible
        self.file_browser.navigate_to(str(Path(path).parent))
        self._update_controls()
        self._append_log("INFO", f"Loaded {Path(path).name}")

    def _on_file_hovered(self, path: str):
        """Single-click in file browser: preview in Model tab without switching."""
        self.stl_viewer.load_stl(path)
        self._stl_path = path
        self.status_file_lbl.setText(f"Preview: {Path(path).name}")
        self._update_controls()

    # ── Log panel ─────────────────────────────────────────────────────────────

    @pyqtSlot(str, str)
    def _append_log(self, level: str, msg: str):
        colors = {
            "DEBUG": "#557", "INFO": "#8da", "WARNING": "#da8",
            "ERROR": "#e74", "CRITICAL": "#f44",
        }
        color = colors.get(level, "#8da")
        self.log_text.append(f'<span style="color:{color};">{msg}</span>')
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _clear_log(self):
        self.log_text.clear()

    # ── GCode generation ──────────────────────────────────────────────────────

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

        stl_stem = Path(self._stl_path).stem[:20]
        ms       = overrides.get("mesh_settings", {})
        amp      = ms.get("wave_amplitude", 2.0)
        ps       = overrides.get("print_settings", {})
        mode_tag = "vase" if ps.get("vase_mode") else "mesh"
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
        self.reveal_btn.setEnabled(True)
        self._gcode_chip.set_path(gcode_path)
        self.status_file_lbl.setText(f"Generated: {Path(gcode_path).name}")
        self._append_log("INFO", f"GCode saved: {Path(gcode_path).name}")

        overrides = self.settings_panel.get_config_overrides()
        ip = self._app_settings.get("printer_ip", "")
        pdb.add_job(self._stl_path, gcode_path, overrides, printer_ip=ip)

        QTimer.singleShot(300, self._start_toolpath_preview)

    @pyqtSlot(str)
    def _on_slicer_error(self, msg):
        self.progress_bar.setVisible(False)
        self._set_progress(0, "")
        self.generate_btn.setEnabled(bool(self._stl_path))
        self._append_log("ERROR", f"Slicer error: {msg[:500]}")
        QMessageBox.critical(self, "Slicer Error", msg[:1000])

    # ── Toolpath preview ─────────────────────────────────────────────────────

    def _start_toolpath_preview(self):
        if not self._gcode_path:
            return
        self._append_log("INFO", "Loading toolpath from GCode…")
        self.preview_tabs.setCurrentIndex(self.TAB_GENERATED)
        self.toolpath_viewer.load_gcode(self._gcode_path)

    # ── Klipper ───────────────────────────────────────────────────────────────

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
                f"Cannot reach {ip}:{port}.\nCheck IP in App Settings."
            )
            return

        self._set_progress(0, f"Uploading to {ip}…")
        filename = client.upload_file(self._gcode_path)
        if not filename:
            QMessageBox.critical(self, "Upload Failed", "File upload failed.")
            self._set_progress(0, "")
            return

        jobs = pdb.get_all_jobs()
        if jobs and Path(jobs[0].get("gcode_file","")).name == Path(self._gcode_path).name:
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
                QMessageBox.warning(self, "Start Failed", "Could not start print.")
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
        elif state == "printing":
            pct = int(client.get_progress() * 100)
            self.status_klipper_lbl.setText(f"Klipper: printing {pct}%")
            self.status_klipper_lbl.setStyleSheet("color: #27ae60;")
        elif state == "complete":
            self.status_klipper_lbl.setText("Klipper: complete")
            self.status_klipper_lbl.setStyleSheet("color: #2ecc71;")
        else:
            self.status_klipper_lbl.setText(f"Klipper: {ip} — {state or 'idle'}")
            self.status_klipper_lbl.setStyleSheet("color: #aaa;")

    # ── Dialogs ───────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = AppSettingsDialog(self)
        if dlg.exec():
            self._app_settings = load_app_settings()

    def _open_library(self):
        dlg = GCodeLibraryDialog(self)
        dlg.settings_loaded.connect(self.settings_panel.load_config)
        dlg.exec()

    def _open_history(self):
        dlg = PrintHistoryDialog(self)
        dlg.exec()

    def _reveal_gcode(self):
        if self._gcode_path:
            _reveal_file(self._gcode_path)
        elif self._stl_path:
            _reveal_file(self._stl_path)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _on_settings_changed(self):
        pass

    def _update_controls(self):
        has_file  = bool(self._stl_path)
        has_gcode = bool(self._gcode_path)
        self.generate_btn.setEnabled(has_file)
        self.send_btn.setEnabled(has_gcode)
        self.reveal_btn.setEnabled(has_gcode or has_file)

    def _set_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.progress_label.setText(msg)

"""
QApplication entry point for MeshyGen.
Applies dark styling and launches the main window.
"""

import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor, QFont
from PyQt6.QtCore import Qt


DARK_STYLE = """
QMainWindow, QDialog, QWidget {
    background: #1a1b22;
    color: #dde1ec;
}
QMenuBar {
    background: #14151c;
    color: #ccc;
    border-bottom: 1px solid #2a2b35;
}
QMenuBar::item:selected { background: #2a3050; }
QMenu {
    background: #20212e;
    color: #ddd;
    border: 1px solid #333;
}
QMenu::item:selected { background: #2a3050; }

QGroupBox {
    color: #8899bb;
    border: 1px solid #2a2d3a;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}

QScrollArea, QAbstractScrollArea {
    background: #1a1b22;
    border: none;
}
QScrollBar:vertical {
    background: #1a1b22;
    width: 8px;
}
QScrollBar::handle:vertical {
    background: #444;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox, QTextEdit {
    background: #252630;
    color: #dde1ec;
    border: 1px solid #3a3d50;
    border-radius: 3px;
    padding: 2px 4px;
}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus,
QComboBox:focus, QTextEdit:focus {
    border: 1px solid #4a6aaf;
}
QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-position: top right;
    subcontrol-origin: border;
    width: 18px;
    border-left: 1px solid #3a3d50;
    background: #2c3350;
    border-top-right-radius: 3px;
}
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover {
    background: #3a4570;
}
QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-position: bottom right;
    subcontrol-origin: border;
    width: 18px;
    border-left: 1px solid #3a3d50;
    background: #2c3350;
    border-bottom-right-radius: 3px;
}
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {
    background: #3a4570;
}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
    image: url("{arrow_up}");
    width: 8px;
    height: 6px;
}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
    image: url("{arrow_down}");
    width: 8px;
    height: 6px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background: #252630;
    color: #dde1ec;
    selection-background-color: #2a3050;
}

QPushButton {
    background: #252630;
    color: #dde1ec;
    border: 1px solid #3a3d50;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover { background: #2e3040; border: 1px solid #4a6aaf; }
QPushButton:pressed { background: #1e2030; }

QCheckBox { color: #dde1ec; }
QCheckBox::indicator { width: 14px; height: 14px; }

QLabel { color: #bbc0d0; }

QTableWidget {
    background: #1e2028;
    color: #ddd;
    gridline-color: #2a2d3a;
    border: none;
}
QTableWidget::item:selected { background: #2a3050; }
QHeaderView::section {
    background: #14151c;
    color: #aab;
    border: 1px solid #2a2d3a;
    padding: 4px;
}

QTabWidget::pane { border: 1px solid #2a2d3a; }
QTabBar::tab {
    background: #1e2028;
    color: #aaa;
    padding: 5px 14px;
    border: 1px solid #2a2d3a;
    border-bottom: none;
}
QTabBar::tab:selected { background: #252630; color: #dde1ec; }

QStatusBar { background: #14151c; color: #888; border-top: 1px solid #2a2d3a; }
QSplitter::handle { background: #2a2d3a; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical  { height: 2px; }
"""


# In a PyInstaller frozen bundle the real data files live under sys._MEIPASS.
# __file__ still carries the module's virtual path so we fall back to _MEIPASS.
if getattr(sys, "frozen", False):
    _RES = Path(sys._MEIPASS) / "gui" / "resources"
else:
    _RES = Path(__file__).parent / "resources"


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MeshyGen")
    app.setOrganizationName("MeshyGen")

    # Base font
    font = QFont("Helvetica Neue")
    if not font.exactMatch():
        font = QFont("Helvetica")
    font.setPointSize(12)
    app.setFont(font)

    # Inject SVG arrow paths (absolute, forward-slash) into the stylesheet
    arrow_up   = str(_RES / "arrow_up.svg").replace("\\", "/")
    arrow_down = str(_RES / "arrow_down.svg").replace("\\", "/")
    css = DARK_STYLE.replace("{arrow_up}", arrow_up).replace("{arrow_down}", arrow_down)
    app.setStyleSheet(css)

    # Import here (after path setup) to avoid circular issues
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from gui.main_window import MainWindow
    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

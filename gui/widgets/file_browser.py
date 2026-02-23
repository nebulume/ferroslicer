"""
File browser widget — a QTreeView backed by QFileSystemModel filtered to .stl
files.  Used as the first tab in the main window so users can open models
immediately without a file dialog.

Signals:
  file_hovered(path)  — emitted on single-click selection (shows quick preview)
  file_opened(path)   — emitted on double-click or Open button (full load)
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeView, QSizePolicy, QLineEdit, QAbstractItemView,
)
from PyQt6.QtCore import (
    Qt, QDir, QModelIndex, pyqtSignal, QSortFilterProxyModel,
    QItemSelectionModel,
)
from PyQt6.QtGui import QFileSystemModel


class _STLFilterProxy(QSortFilterProxyModel):
    """Show only directories and .stl files."""
    def filterAcceptsRow(self, src_row: int, src_parent: QModelIndex) -> bool:
        model: QFileSystemModel = self.sourceModel()
        idx = model.index(src_row, 0, src_parent)
        if model.isDir(idx):
            return True
        name = model.fileName(idx).lower()
        return name.endswith(".stl")


class FileBrowserWidget(QWidget):
    file_hovered = pyqtSignal(str)   # single-click: preview
    file_opened  = pyqtSignal(str)   # double-click / Open button: full load

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── File system model ─────────────────────────────────────────────────
        self._fs_model = QFileSystemModel(self)
        self._fs_model.setRootPath(QDir.rootPath())
        self._fs_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )
        # Name filters applied via proxy (model-level filter greys out rather than hides)

        self._proxy = _STLFilterProxy(self)
        self._proxy.setSourceModel(self._fs_model)
        self._proxy.setDynamicSortFilter(True)

        # ── Tree view ─────────────────────────────────────────────────────────
        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        self._tree.setRootIndex(
            self._proxy.mapFromSource(
                self._fs_model.index(str(Path.home()))
            )
        )
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        # Hide size / type / date columns — only show name
        for col in (1, 2, 3):
            self._tree.hideColumn(col)
        self._tree.header().hide()

        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setAnimated(True)
        self._tree.setStyleSheet(
            "QTreeView { background: #0e0f14; color: #ccd; border: none; font-size: 12px; }"
            "QTreeView::item { padding: 2px 4px; }"
            "QTreeView::item:selected { background: #2a3a52; color: #eef; }"
            "QTreeView::item:hover { background: #1a2030; }"
            "QTreeView::branch { background: #0e0f14; }"
        )
        self._tree.selectionModel().selectionChanged.connect(self._on_selection)
        self._tree.doubleClicked.connect(self._on_double_click)

        # ── Navigation bar ────────────────────────────────────────────────────
        nav = QHBoxLayout()
        nav.setSpacing(4)
        nav.setContentsMargins(0, 0, 0, 0)

        self._up_btn = QPushButton("↑")
        self._up_btn.setFixedSize(28, 28)
        self._up_btn.setToolTip("Go up one directory")
        self._up_btn.clicked.connect(self._go_up)

        self._home_btn = QPushButton("⌂")
        self._home_btn.setFixedSize(28, 28)
        self._home_btn.setToolTip("Home directory")
        self._home_btn.clicked.connect(self._go_home)

        self._path_lbl = QLabel(str(Path.home()))
        self._path_lbl.setStyleSheet("color: #778; font-size: 11px;")
        self._path_lbl.setWordWrap(False)
        self._path_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        nav.addWidget(self._up_btn)
        nav.addWidget(self._home_btn)
        nav.addWidget(self._path_lbl, stretch=1)

        # ── Open button ───────────────────────────────────────────────────────
        self._open_btn = QPushButton("Open STL")
        self._open_btn.setFixedHeight(32)
        self._open_btn.setEnabled(False)
        self._open_btn.setStyleSheet(
            "QPushButton { background: #2a5298; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3a62a8; }"
            "QPushButton:disabled { background: #222; color: #555; }"
        )
        self._open_btn.clicked.connect(self._open_selected)

        # ── Layout ────────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        root.addLayout(nav)
        root.addWidget(self._tree, stretch=1)
        root.addWidget(self._open_btn)

        self._current_path: str = ""

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go_up(self):
        cur = self._tree.rootIndex()
        src = self._proxy.mapToSource(cur)
        parent = src.parent()
        if parent.isValid():
            self._set_root(self._proxy.mapFromSource(parent))

    def _go_home(self):
        self._set_root(
            self._proxy.mapFromSource(
                self._fs_model.index(str(Path.home()))
            )
        )

    def _set_root(self, proxy_idx: QModelIndex):
        self._tree.setRootIndex(proxy_idx)
        src = self._proxy.mapToSource(proxy_idx)
        path = self._fs_model.filePath(src)
        self._path_lbl.setText(path)
        self._path_lbl.setToolTip(path)

    def navigate_to(self, path: str):
        """Programmatically navigate to a directory."""
        idx = self._fs_model.index(path)
        if idx.isValid():
            self._set_root(self._proxy.mapFromSource(idx))

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection(self):
        idxs = self._tree.selectedIndexes()
        if not idxs:
            self._open_btn.setEnabled(False)
            self._current_path = ""
            return
        src = self._proxy.mapToSource(idxs[0])
        path = self._fs_model.filePath(src)
        is_stl = path.lower().endswith(".stl")
        self._open_btn.setEnabled(is_stl)
        self._current_path = path if is_stl else ""

        if is_stl:
            self.file_hovered.emit(path)
        elif self._fs_model.isDir(src):
            # Navigating into a folder on single-click
            pass

    def _on_double_click(self, proxy_idx: QModelIndex):
        src = self._proxy.mapToSource(proxy_idx)
        path = self._fs_model.filePath(src)
        if self._fs_model.isDir(src):
            # Drill into directory
            self._set_root(proxy_idx)
        elif path.lower().endswith(".stl"):
            self.file_opened.emit(path)

    def _open_selected(self):
        if self._current_path:
            self.file_opened.emit(self._current_path)

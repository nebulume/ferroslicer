"""
3D STL viewer widget — software renderer using numpy + QPainter.

Supports:
  - Left drag  : rotate (arcball-style Euler XY)
  - Scroll     : zoom
  - Right drag : pan
  - Double-click: reset view
  - Drag-and-drop STL files onto the widget
"""

import math
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt, QPointF, pyqtSignal, QThread, pyqtSlot
from PyQt6.QtGui import (
    QPainter, QColor, QPolygonF, QPen, QBrush, QFont, QLinearGradient,
)


class STLLoaderThread(QThread):
    """Load and triangulate STL in background so the UI stays responsive."""
    loaded = pyqtSignal(object, object)   # verts (N,3,3) float32, normals (N,3) float32
    error  = pyqtSignal(str)

    def __init__(self, stl_path: str, parent=None):
        super().__init__(parent)
        self.stl_path = stl_path

    def run(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        try:
            from project.core.stl_parser import STLParser
            model = STLParser.parse(self.stl_path)
            n = len(model.triangles)
            verts   = np.empty((n, 3, 3), dtype=np.float32)
            normals = np.empty((n, 3),    dtype=np.float32)
            for i, tri in enumerate(model.triangles):
                verts[i, 0] = [tri.vertex1.x, tri.vertex1.y, tri.vertex1.z]
                verts[i, 1] = [tri.vertex2.x, tri.vertex2.y, tri.vertex2.z]
                verts[i, 2] = [tri.vertex3.x, tri.vertex3.y, tri.vertex3.z]
                normals[i]  = [tri.normal.x,  tri.normal.y,  tri.normal.z]
            self.loaded.emit(verts, normals)
        except Exception as e:
            self.error.emit(str(e))


class STLViewer(QWidget):
    """
    Rotatable 3D STL preview widget.
    Renders using QPainter (CPU) + numpy matrix math.
    Accepts drag-and-drop STL files.
    """

    file_dropped = pyqtSignal(str)   # emitted when user drops an STL

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        # View state
        self.rot_x: float = 25.0   # degrees — tilt down slightly
        self.rot_y: float = 35.0   # degrees — rotate right
        self.zoom:  float = 1.0
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0

        # Mesh data (set after loading)
        self._verts:   Optional[np.ndarray] = None   # (N, 3, 3)
        self._normals: Optional[np.ndarray] = None   # (N, 3)
        self._model_name: str = ""

        # Interaction
        self._last_mouse: Optional[QPointF] = None
        self._mouse_button: Qt.MouseButton = Qt.MouseButton.NoButton

        # Loading state
        self._loading = False
        self._error: str = ""

        # Light direction (fixed in world space)
        self._light = np.array([0.6, 0.8, 1.0], dtype=np.float32)
        self._light /= np.linalg.norm(self._light)

    # ── Public API ───────────────────────────────────────────────────────────

    def load_stl(self, path: str) -> None:
        self._loading = True
        self._error = ""
        self._verts = None
        self.update()
        self._loader = STLLoaderThread(path, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.error.connect(self._on_error)
        self._loader.start()
        self._model_name = Path(path).stem

    def clear(self):
        self._verts = None
        self._normals = None
        self._model_name = ""
        self._loading = False
        self._error = ""
        self.update()

    # ── Slots ────────────────────────────────────────────────────────────────

    @pyqtSlot(object, object)
    def _on_loaded(self, verts: np.ndarray, normals: np.ndarray):
        self._loading = False
        # Normalize to [-1, 1]³
        flat = verts.reshape(-1, 3)
        center = (flat.max(0) + flat.min(0)) / 2
        scale = np.abs(flat - center).max()
        if scale > 0:
            verts = (verts - center) / scale
        # Flip Y so Z is up (STL is typically Z-up)
        # Swap Y and Z so Z-up maps to Y-up in screen space
        verts = verts[:, :, [0, 2, 1]]
        verts[:, :, 1] *= -1  # flip new Y so model is right-way up
        self._verts   = verts
        self._normals = normals.copy()
        self.reset_view()

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self._loading = False
        self._error = msg
        self.update()

    # ── View helpers ─────────────────────────────────────────────────────────

    def reset_view(self):
        self.rot_x = 25.0
        self.rot_y = 35.0
        self.zoom  = 0.85
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()

    def _rotation_matrix(self) -> np.ndarray:
        rx = math.radians(self.rot_x)
        ry = math.radians(self.rot_y)
        Rx = np.array([
            [1, 0,           0          ],
            [0, math.cos(rx), -math.sin(rx)],
            [0, math.sin(rx),  math.cos(rx)],
        ], dtype=np.float32)
        Ry = np.array([
            [ math.cos(ry), 0, math.sin(ry)],
            [0,             1, 0            ],
            [-math.sin(ry), 0, math.cos(ry)],
        ], dtype=np.float32)
        return Rx @ Ry

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Background gradient
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(28, 28, 35))
        grad.setColorAt(1, QColor(18, 18, 24))
        painter.fillRect(self.rect(), grad)

        if self._loading:
            painter.setPen(QColor(180, 180, 180))
            painter.setFont(QFont("Helvetica", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading…")
            return

        if self._error:
            painter.setPen(QColor(220, 80, 80))
            painter.setFont(QFont("Helvetica", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"Error:\n{self._error}")
            return

        if self._verts is None:
            self._draw_drop_hint(painter)
            return

        self._render_mesh(painter)

        # Model name overlay
        if self._model_name:
            painter.setPen(QColor(160, 160, 160))
            painter.setFont(QFont("Helvetica", 11))
            painter.drawText(8, self.height() - 8, self._model_name)

    def _draw_drop_hint(self, painter: QPainter):
        painter.setPen(QColor(80, 80, 100))
        painter.setFont(QFont("Helvetica", 14))
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignCenter,
            "Drop an STL file here\nor use Load STL below"
        )

    def _render_mesh(self, painter: QPainter):
        verts   = self._verts    # (N, 3, 3)
        normals = self._normals  # (N, 3)
        n = len(verts)
        if n == 0:
            return

        R = self._rotation_matrix()
        w, h = self.width(), self.height()

        # Rotate vertices: (N, 3, 3) → each vertex row @ R.T
        rot_verts = verts @ R.T                           # (N, 3, 3) rotated
        rot_normals = normals @ R.T                       # (N, 3) rotated

        # Back-face culling — drop triangles whose normal faces away from camera
        # In view space, camera is along -Z, so normal.z < 0 means back-facing
        front = rot_normals[:, 2] > -0.1                 # slight bias to keep edges
        rot_verts   = rot_verts[front]
        rot_normals = rot_normals[front]
        if len(rot_verts) == 0:
            return

        # Depth sort (painter's algorithm): sort by mean Z of triangle, back-to-front
        depths = rot_verts[:, :, 2].mean(axis=1)         # (M,)
        order  = np.argsort(depths)                      # back → front
        rot_verts   = rot_verts[order]
        rot_normals = rot_normals[order]

        # Project to screen (orthographic)
        scale = min(w, h) / 2.0 * self.zoom
        cx, cy = w / 2 + self.pan_x, h / 2 + self.pan_y
        proj_x = rot_verts[:, :, 0] * scale + cx        # (M, 3)
        proj_y = rot_verts[:, :, 1] * -scale + cy       # (M, 3)  flip Y

        # Lighting: diffuse from fixed light direction
        light_r = self._light @ R.T                      # light in view space
        diffuse = np.clip(rot_normals @ light_r, 0, 1)  # (M,)

        # Draw triangles
        for i in range(len(rot_verts)):
            d = float(diffuse[i])
            # Blue-grey tinted mesh
            r = int(60  + 160 * d)
            g = int(70  + 150 * d)
            b = int(100 + 130 * d)
            color = QColor(r, g, b)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            poly = QPolygonF([
                QPointF(float(proj_x[i, j]), float(proj_y[i, j]))
                for j in range(3)
            ])
            painter.drawConvexPolygon(poly)

    # ── Interaction ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._last_mouse = event.position()
        self._mouse_button = event.button()

    def mouseMoveEvent(self, event):
        if self._last_mouse is None:
            return
        dx = event.position().x() - self._last_mouse.x()
        dy = event.position().y() - self._last_mouse.y()
        self._last_mouse = event.position()

        if self._mouse_button == Qt.MouseButton.LeftButton:
            self.rot_y += dx * 0.5
            self.rot_x += dy * 0.5
            self.update()
        elif self._mouse_button == Qt.MouseButton.RightButton:
            self.pan_x += dx
            self.pan_y += dy
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_mouse = None
        self._mouse_button = Qt.MouseButton.NoButton

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.zoom = max(0.05, min(20.0, self.zoom * factor))
        self.update()

    # ── Drag-and-drop ────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(u.toLocalFile().lower().endswith(".stl") for u in urls):
                event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if path.lower().endswith(".stl"):
                self.file_dropped.emit(path)
                break

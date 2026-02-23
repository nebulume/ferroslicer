"""
Path preview widget — shows sliced toolpath in 2D isometric and 3D views.

Three modes:
  2D Iso   — all layer perimeters projected from an isometric angle, stacked
  3D Snap  — full 3D line-strip of the actual spiral/wave path (low res for speed)
  3D Full  — same but rendered at full resolution
"""

import math
from typing import Optional, List

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QButtonGroup,
    QLabel, QSizePolicy, QComboBox,
)
from PyQt6.QtCore import Qt, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QLinearGradient, QFont, QPolygonF,
)


class PathPreviewWidget(QWidget):
    """Displays toolpath in 2D isometric or 3D views."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(300, 220)

        # Data
        self._path_pts: Optional[np.ndarray] = None   # (N, 3) float32 x, y, z
        self._layer_pts: Optional[List[np.ndarray]] = None  # per-layer (M, 2) arrays
        self._mode: str = "2d"   # "2d" | "3d_snap" | "3d_full"
        self._label: str = ""

        # View state (shared across modes)
        self.rot_x: float = 35.0
        self.rot_y: float = 45.0
        self.zoom:  float = 1.0
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0
        self._last_mouse = None
        self._mouse_btn = Qt.MouseButton.NoButton

        self.setAcceptDrops(False)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_layer_data(self, layer_pts: List[np.ndarray], label: str = "") -> None:
        """Supply per-layer (x,y) arrays for 2D isometric view."""
        self._layer_pts = layer_pts
        self._label = label
        self.update()

    def set_path_data(self, pts: np.ndarray, label: str = "") -> None:
        """Supply (N,3) path array for 3D views."""
        self._path_pts = pts
        self._label = label
        self.update()

    def set_mode(self, mode: str) -> None:
        """mode: '2d' | '3d_snap' | '3d_full'"""
        self._mode = mode
        self.update()

    def clear(self):
        self._path_pts = None
        self._layer_pts = None
        self._label = ""
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(22, 26, 34))
        grad.setColorAt(1, QColor(14, 18, 24))
        painter.fillRect(self.rect(), grad)

        has_data = (
            (self._mode == "2d" and self._layer_pts is not None) or
            (self._mode != "2d" and self._path_pts is not None)
        )

        if not has_data:
            painter.setPen(QColor(70, 70, 90))
            painter.setFont(QFont("Helvetica", 12))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "No preview — click Refresh Preview"
            )
            return

        if self._mode == "2d":
            self._draw_2d_iso(painter)
        else:
            self._draw_3d_path(painter)

        # Label overlay
        if self._label:
            painter.setPen(QColor(130, 130, 160))
            painter.setFont(QFont("Helvetica", 10))
            painter.drawText(8, self.height() - 8, self._label)

    def _draw_2d_iso(self, painter: QPainter):
        """Render layer perimeters in isometric-ish projection."""
        layers = self._layer_pts
        if not layers:
            return

        w, h = self.width(), self.height()

        # Collect all XY points for normalization
        all_xy = np.vstack([l[:, :2] for l in layers if len(l) > 0])
        if len(all_xy) == 0:
            return

        # Normalise to [-1, 1]
        center = (all_xy.max(0) + all_xy.min(0)) / 2
        scale_xy = np.abs(all_xy - center).max()
        if scale_xy == 0:
            scale_xy = 1

        n_layers = len(layers)
        z_step = 1.0 / max(n_layers, 1)

        scale = min(w, h) / 2.5 * self.zoom
        cx, cy = w / 2 + self.pan_x, h / 2 + self.pan_y

        # Isometric projection: (x, y, z) → (u, v)
        # Rotated by rot_y around Z, then isometric tilt
        rot_rad = math.radians(self.rot_y)
        tilt = math.radians(self.rot_x)
        cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)
        cos_t = math.cos(tilt)

        for li, layer_pts in enumerate(layers):
            if len(layer_pts) < 2:
                continue
            z_norm = li * z_step * 2 - 1  # -1 to +1

            # Color gradient: bottom=blue, top=red
            t = li / max(n_layers - 1, 1)
            r = int(40 + 180 * t)
            g = int(120 - 60 * t)
            b = int(220 - 180 * t)
            color = QColor(r, g, b, 180)

            painter.setPen(QPen(color, 1.0))

            pts_xy = (layer_pts[:, :2] - center) / scale_xy

            # Apply view rotation in XY plane
            x_rot =  pts_xy[:, 0] * cos_r + pts_xy[:, 1] * sin_r
            y_rot = -pts_xy[:, 0] * sin_r + pts_xy[:, 1] * cos_r

            # Isometric: project Y onto screen using tilt
            u = x_rot * scale + cx
            v = (-z_norm * 0.6 - y_rot * cos_t) * scale + cy

            poly = QPolygonF([QPointF(float(u[i]), float(v[i])) for i in range(len(u))])
            poly.append(QPointF(float(u[0]), float(v[0])))   # close
            painter.drawPolyline(poly)

    def _draw_3d_path(self, painter: QPainter):
        """Render 3D toolpath as a coloured line strip."""
        pts = self._path_pts
        if pts is None or len(pts) < 2:
            return

        # Subsample for snap mode (every 5th point)
        if self._mode == "3d_snap" and len(pts) > 5000:
            step = max(1, len(pts) // 3000)
            pts = pts[::step]

        w, h = self.width(), self.height()

        # Normalize
        center = (pts.max(0) + pts.min(0)) / 2
        scale_v = np.abs(pts - center).max()
        if scale_v == 0:
            scale_v = 1
        pts_n = (pts - center) / scale_v

        # Swap Z and Y (STL is Z-up, screen is Y-down)
        pts_n = pts_n[:, [0, 2, 1]]
        pts_n[:, 1] *= -1

        # Rotation matrix
        rx = math.radians(self.rot_x)
        ry = math.radians(self.rot_y)
        Rx = np.array([
            [1, 0, 0],
            [0, math.cos(rx), -math.sin(rx)],
            [0, math.sin(rx),  math.cos(rx)],
        ])
        Ry = np.array([
            [math.cos(ry), 0, math.sin(ry)],
            [0, 1, 0],
            [-math.sin(ry), 0, math.cos(ry)],
        ])
        R = (Rx @ Ry).astype(np.float32)
        rot = pts_n @ R.T

        scale = min(w, h) / 2.0 * self.zoom
        cx, cy = w / 2 + self.pan_x, h / 2 + self.pan_y
        px = rot[:, 0] * scale + cx
        py = rot[:, 1] * -scale + cy

        n = len(pts)
        # Color gradient by index (bottom=blue, top=orange)
        for i in range(n - 1):
            t = i / max(n - 2, 1)
            r = int(30 + 200 * t)
            g = int(100 + 80 * t)
            b = int(220 - 150 * t)
            painter.setPen(QPen(QColor(r, g, b, 200), 1.2))
            painter.drawLine(
                QPointF(float(px[i]),   float(py[i])),
                QPointF(float(px[i+1]), float(py[i+1])),
            )

    # ── Interaction ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._last_mouse = event.position()
        self._mouse_btn = event.button()

    def mouseMoveEvent(self, event):
        if self._last_mouse is None:
            return
        dx = event.position().x() - self._last_mouse.x()
        dy = event.position().y() - self._last_mouse.y()
        self._last_mouse = event.position()
        if self._mouse_btn == Qt.MouseButton.LeftButton:
            self.rot_y += dx * 0.6
            self.rot_x += dy * 0.6
            self.update()
        elif self._mouse_btn == Qt.MouseButton.RightButton:
            self.pan_x += dx
            self.pan_y += dy
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_mouse = None

    def mouseDoubleClickEvent(self, event):
        self.rot_x, self.rot_y = 35.0, 45.0
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.zoom *= 1.15 if delta > 0 else 1 / 1.15
        self.zoom = max(0.05, min(20.0, self.zoom))
        self.update()

"""
3D toolpath viewer — OpenGL accelerated (QOpenGLWidget).

All extrusion moves from the GCode file are uploaded to a GPU VBO once and
rendered as hardware GL_LINE_STRIP segments with MSAA.  The GPU handles 150k+
points in <1 ms, so no subsampling is needed and interaction is silky.

Travel moves (non-extrusion G0/G1 with XY movement) break the LINE_STRIP so
no spurious diagonal lines appear between print segments.

Color gradient: blue at bed level → orange at the top of the print.

Interaction (same as STL viewer):
  Left drag    — rotate
  Right drag   — pan
  Scroll       — zoom
  Double-click — reset view
"""

import ctypes
import math
from pathlib import Path
from typing import Optional

import OpenGL                            # must be set before importing GL
OpenGL.ERROR_CHECKING = False
import OpenGL.GL as gl

import numpy as np

from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram, QOpenGLShader,
    QOpenGLBuffer, QOpenGLVertexArrayObject,
)
from PyQt6.QtGui import QSurfaceFormat, QPainter, QColor, QFont, QLinearGradient
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot


# ── GLSL shaders ─────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aColor;
uniform mat4 uMVP;
out vec3 vColor;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vColor      = aColor;
}
"""

_FRAG = """
#version 330 core
in  vec3 vColor;
out vec4 fragColor;
void main() {
    fragColor = vec4(vColor, 0.92);
}
"""


# ── Background GCode parser ───────────────────────────────────────────────────

class GCodeLoaderThread(QThread):
    """
    Reads a .gcode file in the background and emits:
      - pts      : (N, 3) float32 — all extrusion move positions [x, y, z]
      - segments : list of (start_idx, count) tuples, one per continuous
                   extrusion segment (broken wherever a travel move occurs)

    Only G1 lines where E strictly increases are treated as extrusion.
    G92 E resets are handled correctly.
    """
    loaded = pyqtSignal(object, object)   # (pts ndarray, segments list)
    error  = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            xs, ys, zs = [], [], []
            segments: list[tuple[int, int]] = []
            seg_start      = 0
            had_travel     = True   # treat start as "after travel"
            relative_e     = False  # M83 sets True, M82 sets False
            cur_x = cur_y = cur_z = cur_e = 0.0

            with open(self.path, "r") as fh:
                for raw in fh:
                    if len(raw) < 2:
                        continue
                    c0, c1 = raw[0], raw[1]

                    # M82 / M83 — absolute / relative extruder mode
                    if c0 == "M" and c1 == "8" and len(raw) > 2 and raw[2] in "23":
                        relative_e = (raw[2] == "3"); continue

                    # G92 — extruder position reset (only relevant in absolute mode)
                    if c0 == "G" and c1 == "9" and len(raw) > 2 and raw[2] == "2":
                        for word in raw.split():
                            if word[0] == ";":
                                break
                            if word[0] == "E":
                                try:
                                    cur_e = float(word[1:])
                                except ValueError:
                                    pass
                        continue

                    # Only G0 / G1
                    if c0 != "G" or c1 not in "01":
                        continue
                    if len(raw) > 2 and raw[2] not in " \t\r\n;":
                        continue   # skip G10, G11, etc.

                    nx, ny, nz, ne = cur_x, cur_y, cur_z, cur_e
                    has_xy = has_e = False
                    for word in raw.split():
                        if not word:
                            continue
                        wc = word[0]
                        if wc == ";":
                            break
                        if wc == "X":
                            try:
                                nx = float(word[1:]); has_xy = True
                            except ValueError:
                                pass
                        elif wc == "Y":
                            try:
                                ny = float(word[1:]); has_xy = True
                            except ValueError:
                                pass
                        elif wc == "Z":
                            try:
                                nz = float(word[1:])
                            except ValueError:
                                pass
                        elif wc == "E":
                            try:
                                ne = float(word[1:]); has_e = True
                            except ValueError:
                                pass

                    # Determine if this is an extrusion move.
                    # Relative E (M83): ne is the delta — positive means extruding.
                    # Absolute E (M82): ne must exceed previous accumulated position.
                    if relative_e:
                        is_extruding = has_e and ne > 0 and has_xy
                    else:
                        is_extruding = has_e and ne > cur_e and has_xy

                    if is_extruding:
                        if had_travel:
                            if xs:
                                segments.append((seg_start, len(xs) - seg_start))
                            seg_start  = len(xs)
                            had_travel = False
                        xs.append(nx); ys.append(ny); zs.append(nz)
                    elif has_xy:
                        # XY travel move — break the current segment
                        had_travel = True

                    cur_x, cur_y, cur_z, cur_e = nx, ny, nz, ne

            # Close the final segment
            if xs and len(xs) - seg_start > 0:
                segments.append((seg_start, len(xs) - seg_start))

            if not xs:
                self.error.emit("No extrusion moves found in GCode file.")
                return

            pts = np.column_stack([
                np.array(xs, dtype=np.float32),
                np.array(ys, dtype=np.float32),
                np.array(zs, dtype=np.float32),
            ])
            self.loaded.emit(pts, segments)

        except Exception as exc:
            self.error.emit(str(exc))


# ── Main widget ───────────────────────────────────────────────────────────────

class ToolpathViewer(QOpenGLWidget):
    """
    Renders the GCode toolpath using OpenGL — no subsampling, no artifacts,
    smooth rotation at any point count.
    """

    def __init__(self, parent=None):
        # OpenGL 3.3 Core Profile + 4× MSAA must be requested before show()
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSamples(4)
        QSurfaceFormat.setDefaultFormat(fmt)

        super().__init__(parent)
        self.setFormat(fmt)

        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        # ── View state ────────────────────────────────────────────────────────
        self.rot_x: float = 25.0
        self.rot_y: float = 35.0
        self.zoom:  float = 0.85
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0

        # ── Data state ────────────────────────────────────────────────────────
        self._n_verts:    int  = 0
        self._segments:   list = []   # list of (start_idx, count)
        self._loading:    bool = False
        self._error:      str  = ""
        self._label:      str  = ""

        # Vertex data waiting to be uploaded (set from background slot,
        # consumed on the next paintGL call where context is current)
        self._pending:          Optional[np.ndarray] = None
        self._pending_segments: list = []

        # ── GL objects (valid only after initializeGL) ─────────────────────
        self._gl_ready: bool = False
        self._prog:  Optional[QOpenGLShaderProgram]     = None
        self._vao:   Optional[QOpenGLVertexArrayObject] = None
        self._vbo:   Optional[QOpenGLBuffer]            = None
        self._mvp_loc: int = -1

        # ── Interaction ───────────────────────────────────────────────────────
        self._last_mouse = None
        self._mouse_btn  = Qt.MouseButton.NoButton

        self._loader: Optional[GCodeLoaderThread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load_gcode(self, path: str) -> None:
        """Start loading a .gcode file in the background."""
        self._loading = True
        self._error   = ""
        self._n_verts = 0
        self._segments = []
        self._label   = Path(path).name
        self._pending  = None
        self._pending_segments = []
        self.update()

        self._loader = GCodeLoaderThread(path, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.error.connect(self._on_error)
        self._loader.start()

    def clear(self) -> None:
        self._n_verts    = 0
        self._segments   = []
        self._loading    = False
        self._error      = ""
        self._label      = ""
        self._pending    = None
        self._pending_segments = []
        if self._gl_ready and self._vbo is not None:
            self.makeCurrent()
            self._vbo.bind()
            self._vbo.allocate(b"", 0)
            self._vbo.release()
            self.doneCurrent()
        self.update()

    def reset_view(self) -> None:
        self.rot_x = 25.0
        self.rot_y = 35.0
        self.zoom  = 0.85
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()

    # ── Background thread slots ───────────────────────────────────────────────

    @pyqtSlot(object, object)
    def _on_loaded(self, pts: np.ndarray, segments: list):
        self._loading = False
        n_segs = len(segments)
        self._label = f"{len(pts):,} extrusion moves  •  {n_segs} segment{'s' if n_segs != 1 else ''}"

        # ── Normalize to [-1, 1]³ ────────────────────────────────────────────
        mn = pts.min(0); mx = pts.max(0)
        center  = (mn + mx) * 0.5
        scale_v = float(np.abs(pts - center).max())
        if scale_v < 1e-9:
            scale_v = 1.0
        pts_n = (pts - center) / scale_v            # (N, 3)

        # GCode Z is height; swap Z↔Y so index-1 = old-Z (height axis).
        # Then flip so that high Z (top of print) maps to negative index-1 —
        # matching OpenGL convention where we apply -s_y in the MVP.
        pts_n = pts_n[:, [0, 2, 1]].copy()
        pts_n[:, 1] *= -1.0

        # ── Per-vertex colour: blue (base) → orange (top) ───────────────────
        z_min = pts[:, 2].min(); z_max = pts[:, 2].max()
        t = (pts[:, 2] - z_min) / max(z_max - z_min, 1e-9)   # 0→1
        colours = np.column_stack([
            0.12 + 0.78 * t,   # R
            0.39 + 0.31 * t,   # G
            0.86 - 0.59 * t,   # B
        ]).astype(np.float32)

        # ── Build interleaved buffer: [x y z r g b] per vertex ───────────────
        self._pending = np.column_stack(
            [pts_n.astype(np.float32), colours]
        ).astype(np.float32, copy=False)             # (N, 6)
        self._pending_segments = segments

        self.reset_view()   # also calls update()

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self._loading = False
        self._error   = msg
        self.update()

    # ── OpenGL lifecycle ──────────────────────────────────────────────────────

    def initializeGL(self):
        gl.glClearColor(0.085, 0.1, 0.13, 1.0)
        gl.glEnable(gl.GL_LINE_SMOOTH)
        gl.glHint(gl.GL_LINE_SMOOTH_HINT, gl.GL_NICEST)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        # Shaders
        self._prog = QOpenGLShaderProgram(self)
        self._prog.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex,   _VERT)
        self._prog.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, _FRAG)
        self._prog.link()
        self._mvp_loc = self._prog.uniformLocation("uMVP")

        # VAO + VBO (empty until first data is loaded)
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)

        stride = 6 * 4   # 6 floats × 4 bytes = 24
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, stride,
                                 ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, stride,
                                 ctypes.c_void_p(12))
        gl.glEnableVertexAttribArray(1)

        self._vao.release()
        self._vbo.release()
        self._gl_ready = True

    def resizeGL(self, w: int, h: int):
        gl.glViewport(0, 0, w, h)

    def paintGL(self):
        # ── Upload pending vertex data ─────────────────────────────────────
        if self._pending is not None and self._gl_ready:
            data = self._pending
            self._vbo.bind()
            self._vbo.allocate(data.tobytes(), data.nbytes)
            self._vbo.release()
            self._n_verts  = len(data)
            self._segments = self._pending_segments
            self._pending  = None
            self._pending_segments = []

        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if self._n_verts < 2 or not self._gl_ready or not self._segments:
            return

        self._prog.bind()
        mvp = self._build_mvp(self.width(), self.height())
        gl.glUniformMatrix4fv(self._mvp_loc, 1, gl.GL_TRUE, mvp.flatten())
        self._vao.bind()

        # Draw each continuous extrusion segment separately so travel moves
        # don't create spurious diagonal lines between print segments.
        for start, count in self._segments:
            if count >= 2:
                gl.glDrawArrays(gl.GL_LINE_STRIP, start, count)

        self._vao.release()
        self._prog.release()

    # Override paintEvent so we can draw text overlays on top of the GL scene
    def paintEvent(self, event):
        super().paintEvent(event)           # triggers paintGL

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._loading:
            painter.setPen(QColor(160, 160, 180))
            painter.setFont(QFont("Helvetica", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             f"Loading GCode…\n{self._label}")

        elif self._error:
            painter.setPen(QColor(220, 80, 80))
            painter.setFont(QFont("Helvetica", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             f"Error:\n{self._error}")

        elif self._n_verts == 0:
            # Draw background gradient when there's no GL content
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0, QColor(22, 26, 34))
            grad.setColorAt(1, QColor(14, 18, 24))
            painter.fillRect(self.rect(), grad)
            painter.setPen(QColor(70, 70, 90))
            painter.setFont(QFont("Helvetica", 13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Generate GCode to see toolpath preview")

        else:
            painter.setPen(QColor(120, 120, 150))
            painter.setFont(QFont("Helvetica", 10))
            painter.drawText(8, self.height() - 8, self._label)

        painter.end()

    # ── MVP matrix ────────────────────────────────────────────────────────────

    def _build_mvp(self, w: int, h: int) -> np.ndarray:
        """
        4×4 row-major MVP.  OpenGL NDC has Y pointing UP, but our stored
        vertex Y is already negated (high print = negative Y) — so we apply
        -s_y to flip it back up for the screen.

        NDC.x =  s_x * rot.x + p_x
        NDC.y = -s_y * rot.y + p_y   (double-negation → top of print at top)
        """
        rx = math.radians(self.rot_x)
        ry = math.radians(self.rot_y)
        cos_rx, sin_rx = math.cos(rx), math.sin(rx)
        cos_ry, sin_ry = math.cos(ry), math.sin(ry)

        Rx = np.array([
            [1,      0,       0,  0],
            [0, cos_rx, -sin_rx,  0],
            [0, sin_rx,  cos_rx,  0],
            [0,      0,       0,  1],
        ], dtype=np.float32)
        Ry = np.array([
            [ cos_ry, 0, sin_ry, 0],
            [0,       1,      0, 0],
            [-sin_ry, 0, cos_ry, 0],
            [0,       0,      0, 1],
        ], dtype=np.float32)
        R = Rx @ Ry

        s_x  =  min(w, h) / w * self.zoom
        s_y  =  min(w, h) / h * self.zoom
        p_x  =  2.0 * self.pan_x / w
        p_y  = -2.0 * self.pan_y / h

        S = np.array([
            [s_x,  0,   0, p_x],
            [0,   -s_y, 0, p_y],   # -s_y: flip Y so top of print appears at top
            [0,    0,   1, 0  ],
            [0,    0,   0, 1  ],
        ], dtype=np.float32)

        return (S @ R).astype(np.float32)

    # ── Interaction ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._last_mouse = event.position()
        self._mouse_btn  = event.button()

    def mouseMoveEvent(self, event):
        if self._last_mouse is None:
            return
        dx = event.position().x() - self._last_mouse.x()
        dy = event.position().y() - self._last_mouse.y()
        self._last_mouse = event.position()
        if self._mouse_btn == Qt.MouseButton.LeftButton:
            self.rot_y += dx * 0.5
            self.rot_x -= dy * 0.5
            self.update()
        elif self._mouse_btn == Qt.MouseButton.RightButton:
            self.pan_x += dx
            self.pan_y += dy
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_mouse = None
        self._mouse_btn  = Qt.MouseButton.NoButton

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(0.05, min(20.0, self.zoom * factor))
        self.update()

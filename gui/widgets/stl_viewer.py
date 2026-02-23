"""
3D STL viewer — OpenGL accelerated (QOpenGLWidget).

Replaces the QPainter-based renderer which was bottlenecked by a Python
for-loop over every triangle.  GPU renders 200k+ faces in <1 ms with proper
depth-buffer occlusion and flat-shaded diffuse lighting.

Interaction (same as before):
  Left drag    — rotate
  Right drag   — pan
  Scroll       — zoom
  Double-click — reset view
  Drag-and-drop STL files onto the widget
"""

import ctypes
import math
from pathlib import Path
from typing import Optional

import OpenGL
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
layout(location = 1) in vec3 aNormal;
uniform mat4 uMVP;
uniform vec3 uLight;   // fixed in model space — shading doesn't change on rotate
flat out vec3 vColor;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    float d     = max(dot(normalize(aNormal), uLight), 0.0);
    float lit   = 0.12 + 0.88 * d;
    vColor = vec3(
        0.15 + 0.63 * lit,
        0.17 + 0.59 * lit,
        0.25 + 0.51 * lit
    );
}
"""

_FRAG = """
#version 330 core
flat in  vec3 vColor;
out vec4 fragColor;
void main() {
    fragColor = vec4(vColor, 1.0);
}
"""


# ── Background STL loader ─────────────────────────────────────────────────────

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


# ── Main widget ───────────────────────────────────────────────────────────────

class STLViewer(QOpenGLWidget):
    """
    Rotatable 3D STL preview — OpenGL accelerated.
    Accepts drag-and-drop STL files.
    """

    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSamples(4)
        QSurfaceFormat.setDefaultFormat(fmt)

        super().__init__(parent)
        self.setFormat(fmt)

        self.setAcceptDrops(True)
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
        self._n_tris:   int  = 0
        self._loading:  bool = False
        self._error:    str  = ""
        self._model_name: str = ""
        self._pending:  Optional[np.ndarray] = None   # VBO data waiting for GL context

        # ── GL objects ────────────────────────────────────────────────────────
        self._gl_ready: bool = False
        self._prog:  Optional[QOpenGLShaderProgram]     = None
        self._vao:   Optional[QOpenGLVertexArrayObject] = None
        self._vbo:   Optional[QOpenGLBuffer]            = None
        self._mvp_loc:   int = -1
        self._light_loc: int = -1

        # Fixed light direction in model space (shading constant through rotations)
        _l = np.array([0.6, 0.8, 1.0], dtype=np.float32)
        self._light = (_l / np.linalg.norm(_l)).tolist()

        # ── Interaction ───────────────────────────────────────────────────────
        self._last_mouse = None
        self._mouse_button = Qt.MouseButton.NoButton

        self._loader: Optional[STLLoaderThread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load_stl(self, path: str) -> None:
        self._loading = True
        self._error   = ""
        self._n_tris  = 0
        self._pending = None
        self._model_name = Path(path).stem
        self.update()
        self._loader = STLLoaderThread(path, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.error.connect(self._on_error)
        self._loader.start()

    def clear(self) -> None:
        self._n_tris     = 0
        self._loading    = False
        self._error      = ""
        self._model_name = ""
        self._pending    = None
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
    def _on_loaded(self, verts: np.ndarray, normals: np.ndarray):
        self._loading = False

        # Normalize to [-1, 1]³
        flat   = verts.reshape(-1, 3)
        center = (flat.max(0) + flat.min(0)) / 2
        scale  = float(np.abs(flat - center).max())
        if scale < 1e-9:
            scale = 1.0
        verts = (verts - center) / scale

        # Z-up STL → Y-down screen convention (same as toolpath viewer)
        verts = verts[:, :, [0, 2, 1]].copy()
        verts[:, :, 1] *= -1.0

        # Recompute face normals from vertices (STL stored normals are often zero)
        v0, v1, v2 = verts[:, 0], verts[:, 1], verts[:, 2]
        face_normals = np.cross(v1 - v0, v2 - v0)
        norms = np.linalg.norm(face_normals, axis=1, keepdims=True)
        norms = np.where(norms > 1e-12, norms, 1.0)
        face_normals = (face_normals / norms).astype(np.float32)

        # Apply same swap/flip to normals so they match transformed vertices
        face_normals = face_normals[:, [0, 2, 1]].copy()
        face_normals[:, 1] *= -1.0

        # Build interleaved VBO: [x y z nx ny nz] × 3 vertices per triangle
        N = len(verts)
        vbo_data = np.empty((N * 3, 6), dtype=np.float32)
        vbo_data[0::3, :3] = verts[:, 0, :]
        vbo_data[1::3, :3] = verts[:, 1, :]
        vbo_data[2::3, :3] = verts[:, 2, :]
        vbo_data[0::3, 3:] = face_normals
        vbo_data[1::3, 3:] = face_normals
        vbo_data[2::3, 3:] = face_normals

        self._pending = vbo_data.astype(np.float32, copy=False)
        self.reset_view()   # also calls update()

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self._loading = False
        self._error   = msg
        self.update()

    # ── OpenGL lifecycle ──────────────────────────────────────────────────────

    def initializeGL(self):
        gl.glClearColor(0.11, 0.11, 0.14, 1.0)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glEnable(gl.GL_CULL_FACE)
        gl.glCullFace(gl.GL_BACK)

        self._prog = QOpenGLShaderProgram(self)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,   _VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAG)
        self._prog.link()
        self._mvp_loc   = self._prog.uniformLocation("uMVP")
        self._light_loc = self._prog.uniformLocation("uLight")

        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)

        stride = 6 * 4   # 6 floats × 4 bytes
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, ctypes.c_void_p(12))
        gl.glEnableVertexAttribArray(1)

        self._vao.release()
        self._vbo.release()
        self._gl_ready = True

    def resizeGL(self, w: int, h: int):
        gl.glViewport(0, 0, w, h)

    def paintGL(self):
        if self._pending is not None and self._gl_ready:
            data = self._pending
            self._vbo.bind()
            self._vbo.allocate(data.tobytes(), data.nbytes)
            self._vbo.release()
            self._n_tris  = len(data) // 3
            self._pending = None

        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)

        if self._n_tris == 0 or not self._gl_ready:
            return

        self._prog.bind()
        mvp = self._build_mvp(self.width(), self.height())
        gl.glUniformMatrix4fv(self._mvp_loc, 1, gl.GL_TRUE, mvp.flatten())
        gl.glUniform3f(self._light_loc, *self._light)
        self._vao.bind()
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, self._n_tris * 3)
        self._vao.release()
        self._prog.release()

    def paintEvent(self, event):
        super().paintEvent(event)   # triggers paintGL

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._loading:
            painter.setPen(QColor(180, 180, 180))
            painter.setFont(QFont("Helvetica", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading…")

        elif self._error:
            painter.setPen(QColor(220, 80, 80))
            painter.setFont(QFont("Helvetica", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             f"Error:\n{self._error}")

        elif self._n_tris == 0:
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0, QColor(28, 28, 35))
            grad.setColorAt(1, QColor(18, 18, 24))
            painter.fillRect(self.rect(), grad)
            painter.setPen(QColor(80, 80, 100))
            painter.setFont(QFont("Helvetica", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Drop an STL file here\nor use Load STL below")

        else:
            if self._model_name:
                painter.setPen(QColor(160, 160, 160))
                painter.setFont(QFont("Helvetica", 11))
                painter.drawText(8, self.height() - 8, self._model_name)

        painter.end()

    # ── MVP matrix ────────────────────────────────────────────────────────────

    def _build_mvp(self, w: int, h: int) -> np.ndarray:
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

        s_x =  min(w, h) / w * self.zoom
        s_y =  min(w, h) / h * self.zoom
        p_x =  2.0 * self.pan_x / w
        p_y = -2.0 * self.pan_y / h

        S = np.array([
            [s_x,  0,   0, p_x],
            [0,   -s_y, 0, p_y],
            [0,    0,   1, 0  ],
            [0,    0,   0, 1  ],
        ], dtype=np.float32)

        return (S @ R).astype(np.float32)

    # ── Interaction ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._last_mouse   = event.position()
        self._mouse_button = event.button()

    def mouseMoveEvent(self, event):
        if self._last_mouse is None:
            return
        dx = event.position().x() - self._last_mouse.x()
        dy = event.position().y() - self._last_mouse.y()
        self._last_mouse = event.position()
        if self._mouse_button == Qt.MouseButton.LeftButton:
            self.rot_y += dx * 0.5
            self.rot_x -= dy * 0.5
            self.update()
        elif self._mouse_button == Qt.MouseButton.RightButton:
            self.pan_x += dx
            self.pan_y += dy
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_mouse   = None
        self._mouse_button = Qt.MouseButton.NoButton

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
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
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".stl"):
                self.file_dropped.emit(path)
                break

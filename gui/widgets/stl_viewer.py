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
from PyQt6.QtWidgets import (
    QWidget, QSizePolicy, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QDoubleSpinBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QLocale


# ── GLSL shaders ─────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;
uniform mat4 uMVP;
flat out vec3 vNormal;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vNormal = aNormal;
}
"""

_FRAG = """
#version 330 core
flat in  vec3 vNormal;
uniform float uAlpha;
out vec4 fragColor;
void main() {
    // Use face normal for front faces; flip for back faces.
    // Back faces are interior surfaces or mis-wound triangles — render them
    // dark so the model looks full everywhere with no holes.
    vec3 n = normalize(gl_FrontFacing ? vNormal : -vNormal);

    vec3 key  = normalize(vec3( 0.55,  0.70,  1.0));
    vec3 fill = normalize(vec3(-0.60, -0.25,  0.55));
    vec3 rim  = normalize(vec3( 0.10,  0.40, -0.85));

    float dk = max(dot(n, key),  0.0);
    float df = max(dot(n, fill), 0.0);
    float dr = max(dot(n, rim),  0.0);

    float lit = clamp(0.22 + 0.62*dk + 0.26*df + 0.14*dr, 0.0, 1.0);

    // Strongly darken back-facing fragments (interior walls, wrong-winding)
    // so the outside surface always reads as brighter / dominant.
    if (!gl_FrontFacing) lit *= 0.25;

    fragColor = vec4(
        0.20 + 0.65 * lit,
        0.21 + 0.62 * lit,
        0.24 + 0.52 * lit,
        uAlpha
    );
}
"""


# ── Box wireframe shaders ─────────────────────────────────────────────────────

_BOX_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
uniform mat4 uMVP;
void main() { gl_Position = uMVP * vec4(aPos, 1.0); }
"""

_BOX_FRAG = """
#version 330 core
uniform vec4 uColor;
out vec4 fragColor;
void main() { fragColor = uColor; }
"""


def _grid_verts(x0: float, y: float, z0: float,
                x1: float, z1: float, step: float) -> np.ndarray:
    """Return (N, 3) float32 — grid line vertices for GL_LINES at constant viewer-Y.

    Lines run parallel to X (varying Z) and parallel to Z (varying X),
    covering [x0, x1] × [z0, z1] with the given step spacing.
    """
    n_x = max(2, round((x1 - x0) / step) + 1)
    n_z = max(2, round((z1 - z0) / step) + 1)
    xs = np.linspace(x0, x1, n_x, dtype=np.float32)
    zs = np.linspace(z0, z1, n_z, dtype=np.float32)
    verts = []
    for z in zs:                     # horizontal lines (parallel to X)
        verts.append([x0, y, z])
        verts.append([x1, y, z])
    for x in xs:                     # vertical lines (parallel to Z)
        verts.append([x, y, z0])
        verts.append([x, y, z1])
    return np.array(verts, dtype=np.float32)


def _box_edge_verts(x0: float, y0: float, z0: float,
                    x1: float, y1: float, z1: float) -> np.ndarray:
    """Return (24, 3) float32 — 12 edges × 2 vertices for GL_LINES."""
    c = [
        (x0, y0, z0), (x1, y0, z0), (x0, y1, z0), (x1, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x0, y1, z1), (x1, y1, z1),
    ]
    edges = [
        (0, 1), (2, 3), (4, 5), (6, 7),   # parallel to X
        (0, 2), (1, 3), (4, 6), (5, 7),   # parallel to Y
        (0, 4), (1, 5), (2, 6), (3, 7),   # parallel to Z
    ]
    verts = []
    for a, b in edges:
        verts.append(c[a])
        verts.append(c[b])
    return np.array(verts, dtype=np.float32)


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


# ── Inner OpenGL widget ───────────────────────────────────────────────────────

class _STLViewerGL(QOpenGLWidget):
    """All OpenGL rendering. Wrapped by STLViewer which adds the control bar."""

    file_dropped   = pyqtSignal(str)
    model_extents  = pyqtSignal(float, float, float)   # x_mm, y_mm, z_mm (raw, before scale)

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

        # ── Model scale (relative to print volume, independent of camera zoom) ─
        self._model_scale: float = 1.0

        # ── Raw STL extents in mm (before normalization or user scale) ────────
        self._raw_extents: tuple = (0.0, 0.0, 0.0)   # (x_mm, y_mm, z_mm)

        # ── Transparent mode ──────────────────────────────────────────────────
        self._transparent: bool = True
        self._alpha_loc:   int  = -1

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
        self._mvp_loc: int = -1

        # ── Print volume box ──────────────────────────────────────────────────
        self._bed_dims:       Optional[tuple] = None   # (bed_x, bed_y, max_z) mm
        self._scale_mm:       float           = 0.0    # model half-extent in mm (1 norm unit = this many mm)
        self._model_y_bottom: float           = 0.0    # viewer-Y of model floor (box bed aligns here)
        self._box_prog:    Optional[QOpenGLShaderProgram]     = None
        self._box_vao:     Optional[QOpenGLVertexArrayObject] = None
        self._box_vbo:     Optional[QOpenGLBuffer]            = None
        self._box_mvp_loc:   int                              = -1
        self._box_color_loc: int                              = -1
        self._pending_box: Optional[np.ndarray]               = None
        self._box_n_verts: int                                = 0

        # ── Bed grid (cm squares + mm squares) ───────────────────────────────
        self._grid_cm_vao:     Optional[QOpenGLVertexArrayObject] = None
        self._grid_cm_vbo:     Optional[QOpenGLBuffer]            = None
        self._pending_grid_cm: Optional[np.ndarray]               = None
        self._grid_cm_n_verts: int                                = 0
        self._grid_mm_vao:     Optional[QOpenGLVertexArrayObject] = None
        self._grid_mm_vbo:     Optional[QOpenGLBuffer]            = None
        self._pending_grid_mm: Optional[np.ndarray]               = None
        self._grid_mm_n_verts: int                                = 0

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

    def set_print_volume(self, bed_x: float, bed_y: float, max_z: float) -> None:
        """Show a wireframe box representing the printer build volume."""
        self._bed_dims = (bed_x, bed_y, max_z)
        self._queue_box()
        self.update()

    def _queue_box(self) -> None:
        """Compute box vertices in model-normalized space and queue for GL upload.

        The bed (floor) of the box is aligned with the bottom of the loaded
        model so the model appears sitting on the bed rather than floating.
        All dimensions use _scale_mm so the proportions are correct.
        """
        if self._bed_dims is None or self._scale_mm <= 0:
            return
        bed_x, bed_y, max_z = self._bed_dims
        s = self._scale_mm   # 1 norm unit = s mm

        # Horizontal half-extents (box centred in XZ to match the model)
        hx = bed_x / (2.0 * s)
        hz = bed_y / (2.0 * s)

        # Vertical: viewer_Y is inverted STL-Z (positive viewer_Y = lower/bed).
        # Place the box bed at the model's actual floor so the model touches it.
        y_bed = self._model_y_bottom          # viewer-Y of the bed surface
        y_top = y_bed - max_z / s             # viewer-Y of ceiling (negative = higher)

        self._pending_box = _box_edge_verts(-hx, y_top, -hz, hx, y_bed, hz)
        self._queue_grid(hx, y_bed, hz, s)

    def _queue_grid(self, hx: float, y_bed: float, hz: float, s: float) -> None:
        """Compute and queue cm (10 mm) and mm (1 mm) grid vertices for the bed surface."""
        step_cm = 10.0 / s   # 10 mm in normalized units
        step_mm =  1.0 / s   #  1 mm in normalized units
        self._pending_grid_cm = _grid_verts(-hx, y_bed, -hz, hx, hz, step_cm)
        self._pending_grid_mm = _grid_verts(-hx, y_bed, -hz, hx, hz, step_mm)

    def reset_view(self) -> None:
        self.rot_x = 25.0
        self.rot_y = 35.0
        self.zoom  = 0.85
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()

    def set_view(self, rot_x: float, rot_y: float) -> None:
        self.rot_x = rot_x
        self.rot_y = rot_y
        self.pan_x = self.pan_y = 0.0
        self.update()

    # ── Background thread slots ───────────────────────────────────────────────

    @pyqtSlot(object, object)
    def _on_loaded(self, verts: np.ndarray, normals: np.ndarray):
        self._loading = False

        # Normalize to [-1, 1]³ and record the half-extent for box sizing.
        flat   = verts.reshape(-1, 3)
        center = (flat.max(0) + flat.min(0)) / 2
        scale  = float(np.abs(flat - center).max())
        if scale < 1e-9:
            scale = 1.0
        self._scale_mm = scale   # 1 normalized unit = scale mm

        # Store raw bounding-box extents in mm for the dimensions display.
        x_ext = float(flat[:, 0].max() - flat[:, 0].min())
        y_ext = float(flat[:, 1].max() - flat[:, 1].min())
        z_ext = float(flat[:, 2].max() - flat[:, 2].min())
        self._raw_extents = (x_ext, y_ext, z_ext)
        self.model_extents.emit(x_ext, y_ext, z_ext)

        # After axis-swap+flip the viewer Y of the model's STL floor is:
        #   viewer_y_bottom = (center_z - z_min) / scale
        #                   = (z_max - z_min) / (2 * scale)
        # Store it so _queue_box can align the box bed with the model floor.
        self._model_y_bottom = (flat[:, 2].max() - flat[:, 2].min()) / (2.0 * scale)

        verts = (verts - center) / scale

        # Z-up STL → Y-down screen convention (same as toolpath viewer)
        verts = verts[:, :, [0, 2, 1]].copy()
        verts[:, :, 1] *= -1.0

        # Recompute face normals from the already-transformed vertices.
        # We trust the STL winding — outward-facing normals mean front faces,
        # inward-facing mean back faces. GL_CULL_FACE handles the rest.
        v0, v1, v2 = verts[:, 0], verts[:, 1], verts[:, 2]
        face_normals = np.cross(v1 - v0, v2 - v0)
        norms = np.linalg.norm(face_normals, axis=1, keepdims=True)
        norms = np.where(norms > 1e-12, norms, 1.0)
        face_normals = (face_normals / norms).astype(np.float32)

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

        # Re-queue box now that _scale_mm is known / updated.
        self._queue_box()

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
        # No GL_CULL_FACE: non-convex models need both sides rendered;
        # the fragment shader uses gl_FrontFacing to flip normals for back faces.

        self._prog = QOpenGLShaderProgram(self)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,   _VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAG)
        self._prog.link()
        self._mvp_loc  = self._prog.uniformLocation("uMVP")
        self._alpha_loc = self._prog.uniformLocation("uAlpha")

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

        # ── Box shader ────────────────────────────────────────────────────────
        self._box_prog = QOpenGLShaderProgram(self)
        self._box_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,   _BOX_VERT)
        self._box_prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _BOX_FRAG)
        self._box_prog.link()
        self._box_mvp_loc   = self._box_prog.uniformLocation("uMVP")
        self._box_color_loc = self._box_prog.uniformLocation("uColor")

        self._box_vao = QOpenGLVertexArrayObject(self)
        self._box_vao.create(); self._box_vao.bind()
        self._box_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._box_vbo.create(); self._box_vbo.bind()
        self._box_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        self._box_vao.release(); self._box_vbo.release()

        # ── cm grid VAO (reuses _box_prog — same vertex layout) ───────────────
        self._grid_cm_vao = QOpenGLVertexArrayObject(self)
        self._grid_cm_vao.create(); self._grid_cm_vao.bind()
        self._grid_cm_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._grid_cm_vbo.create(); self._grid_cm_vbo.bind()
        self._grid_cm_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        self._grid_cm_vao.release(); self._grid_cm_vbo.release()

        # ── mm grid VAO ────────────────────────────────────────────────────────
        self._grid_mm_vao = QOpenGLVertexArrayObject(self)
        self._grid_mm_vao.create(); self._grid_mm_vao.bind()
        self._grid_mm_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._grid_mm_vbo.create(); self._grid_mm_vbo.bind()
        self._grid_mm_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        self._grid_mm_vao.release(); self._grid_mm_vbo.release()

        # Re-queue if dims were set before GL was ready
        self._queue_box()

    def resizeGL(self, w: int, h: int):
        gl.glViewport(0, 0, w, h)

    def paintGL(self):
        # Upload pending model data
        if self._pending is not None and self._gl_ready:
            data = self._pending
            self._vbo.bind()
            self._vbo.allocate(data.tobytes(), data.nbytes)
            self._vbo.release()
            self._n_tris  = len(data) // 3
            self._pending = None

        # Upload pending box data
        if self._pending_box is not None and self._gl_ready:
            self._box_vbo.bind()
            self._box_vbo.allocate(self._pending_box.tobytes(), self._pending_box.nbytes)
            self._box_vbo.release()
            self._box_n_verts = len(self._pending_box)
            self._pending_box = None

        # Upload pending grid data
        if self._pending_grid_cm is not None and self._gl_ready:
            data = self._pending_grid_cm
            self._grid_cm_vbo.bind()
            self._grid_cm_vbo.allocate(data.tobytes(), data.nbytes)
            self._grid_cm_vbo.release()
            self._grid_cm_n_verts = len(data)
            self._pending_grid_cm = None
        if self._pending_grid_mm is not None and self._gl_ready:
            data = self._pending_grid_mm
            self._grid_mm_vbo.bind()
            self._grid_mm_vbo.allocate(data.tobytes(), data.nbytes)
            self._grid_mm_vbo.release()
            self._grid_mm_n_verts = len(data)
            self._pending_grid_mm = None

        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)

        if not self._gl_ready:
            return

        # Base MVP (camera only — used for the volume box)
        base_mvp = self._build_mvp(self.width(), self.height())

        # Model MVP: scale the 3D part, then pin the model's bed-floor to the
        # same clip-space position as the box bed so the model stays on the bed
        # when scaled (rather than floating or sinking into it).
        model_mvp = base_mvp.copy()
        model_mvp[:3, :3] *= self._model_scale
        if abs(self._model_scale - 1.0) > 1e-6 and self._model_y_bottom > 0.0:
            anchor = np.array([0.0, self._model_y_bottom, 0.0], dtype=np.float32)
            q = base_mvp[:3, :3] @ anchor
            model_mvp[:3, 3] += (1.0 - self._model_scale) * q

        # ── Model triangles ────────────────────────────────────────────────
        if self._n_tris > 0:
            self._prog.bind()
            gl.glUniform1f(self._alpha_loc, 1.0)
            gl.glUniformMatrix4fv(self._mvp_loc, 1, gl.GL_TRUE, model_mvp.flatten())
            self._vao.bind()

            if self._transparent:
                # Transparent: single pass, both faces, alpha blended.
                gl.glDisable(gl.GL_CULL_FACE)
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                gl.glDepthMask(gl.GL_FALSE)
                gl.glUniform1f(self._alpha_loc, 0.35)
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, self._n_tris * 3)
                gl.glDepthMask(gl.GL_TRUE)
                gl.glDisable(gl.GL_BLEND)
            else:
                # Solid: two-pass render.
                #
                # Pass 1 — back-facing triangles (interior surfaces, wrong-winding faces).
                #   The fragment shader darkens them (gl_FrontFacing=false → lit*=0.25).
                #   Their depths are written so pass 2 can overwrite correctly.
                #
                # Pass 2 — front-facing triangles (exterior surfaces).
                #   These are geometrically closer to the camera than the interior,
                #   so they pass the depth test and overwrite pass 1 everywhere
                #   the exterior is visible.  Bad-winding faces (no matching front
                #   face) remain dark from pass 1 — no holes, no z-fighting.
                gl.glDisable(gl.GL_BLEND)
                gl.glDepthMask(gl.GL_TRUE)
                gl.glEnable(gl.GL_CULL_FACE)

                gl.glCullFace(gl.GL_FRONT)   # draw back-facing tris (dim)
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, self._n_tris * 3)

                gl.glCullFace(gl.GL_BACK)    # draw front-facing tris (bright) on top
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, self._n_tris * 3)

                gl.glDisable(gl.GL_CULL_FACE)

            self._vao.release()
            self._prog.release()

        # ── Bed grid + print volume box (unscaled — always shows actual bed size) ─
        has_box  = self._box_n_verts > 0
        has_grid = self._grid_cm_n_verts > 0 or self._grid_mm_n_verts > 0
        if (has_box or has_grid) and self._box_prog is not None:
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            self._box_prog.bind()
            gl.glUniformMatrix4fv(self._box_mvp_loc, 1, gl.GL_TRUE, base_mvp.flatten())

            # mm grid (1 mm squares) — very faint; draw first so cm grid sits on top
            if self._grid_mm_n_verts > 0:
                gl.glUniform4f(self._box_color_loc, 0.12, 0.18, 0.32, 0.20)
                self._grid_mm_vao.bind()
                gl.glDrawArrays(gl.GL_LINES, 0, self._grid_mm_n_verts)
                self._grid_mm_vao.release()

            # cm grid (10 mm squares) — clearly visible
            if self._grid_cm_n_verts > 0:
                gl.glUniform4f(self._box_color_loc, 0.22, 0.38, 0.65, 0.55)
                self._grid_cm_vao.bind()
                gl.glDrawArrays(gl.GL_LINES, 0, self._grid_cm_n_verts)
                self._grid_cm_vao.release()

            # Box wireframe edges
            if has_box:
                gl.glUniform4f(self._box_color_loc, 0.40, 0.55, 0.80, 0.30)
                self._box_vao.bind()
                gl.glDrawArrays(gl.GL_LINES, 0, self._box_n_verts)
                self._box_vao.release()

            self._box_prog.release()
            gl.glDisable(gl.GL_BLEND)   # restore — must not bleed into next frame

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

        # Dynamic Z scale: compute bounding-sphere radius of the box in
        # model-normalized space so that no corner is ever clipped regardless
        # of how the model was scaled or how large the printer volume is.
        z_scale = 0.2
        if self._scale_mm > 0 and self._bed_dims is not None:
            bed_x, bed_y, max_z_dim = self._bed_dims
            hx = bed_x / (2.0 * self._scale_mm)
            hz = bed_y / (2.0 * self._scale_mm)
            hy = max(abs(self._model_y_bottom),
                     abs(self._model_y_bottom - max_z_dim / self._scale_mm))
            bsphere = math.sqrt(hx * hx + hy * hy + hz * hz)
            if bsphere > 0:
                z_scale = max(0.005, min(0.2, 0.9 / bsphere))

        S = np.array([
            [s_x,  0,        0,   p_x],
            [0,   -s_y,      0,   p_y],
            [0,    0,    z_scale, 0  ],
            [0,    0,        0,   1  ],
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
            self.rot_y -= dx * 0.5
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


# ── Public wrapper widget ─────────────────────────────────────────────────────

class STLViewer(QWidget):
    """
    GL canvas + control bar with model-scale spinbox.

    The scale slider adjusts the model size relative to the print-volume box,
    letting you see whether a model (or a scaled version of it) fits the bed.
    Camera zoom (scroll wheel) is independent and affects the whole scene.

    Public API (unchanged from before):
      load_stl(path)
      set_print_volume(bed_x, bed_y, max_z)
      clear()
      reset_view()
      file_dropped  — signal(str)
    """

    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._gl = _STLViewerGL(self)
        self._gl.file_dropped.connect(self.file_dropped)
        root.addWidget(self._gl, stretch=1)

        # ── Control bar ───────────────────────────────────────────────────────
        bar = QWidget()
        bar.setFixedHeight(30)
        bar.setStyleSheet("background: #161820;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 2, 8, 2)
        bl.setSpacing(6)

        lbl = QLabel("Scale:")
        lbl.setStyleSheet("color: #779; font-size: 11px;")
        bl.addWidget(lbl)

        self._scale_spin = QDoubleSpinBox()
        loc = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
        self._scale_spin.setLocale(loc)
        self._scale_spin.setRange(1.0, 500.0)
        self._scale_spin.setSingleStep(10.0)
        self._scale_spin.setDecimals(0)
        self._scale_spin.setSuffix(" %")
        self._scale_spin.setValue(100.0)
        self._scale_spin.setFixedWidth(72)
        self._scale_spin.setToolTip(
            "Scale the model relative to the print-volume box.\n"
            "100% = STL file dimensions. Camera zoom (scroll) is separate."
        )
        self._scale_spin.setStyleSheet(
            "QDoubleSpinBox { background: #1e2030; color: #ccd; "
            "border: 1px solid #334; font-size: 11px; padding: 1px 3px; }"
        )
        bl.addWidget(self._scale_spin)

        reset_btn = QPushButton("100%")
        reset_btn.setFixedSize(36, 20)
        reset_btn.setStyleSheet(
            "QPushButton { font-size: 10px; padding: 0; color: #99b; "
            "background: transparent; border: none; }"
            "QPushButton:hover { color: #ccf; }"
        )
        reset_btn.setToolTip("Reset scale to 100%")
        reset_btn.clicked.connect(self._reset_scale)
        bl.addWidget(reset_btn)

        sep_v = QLabel("|")
        sep_v.setStyleSheet("color: #334; padding: 0 2px;")
        bl.addWidget(sep_v)

        _btn_style = (
            "QPushButton{font-size:10px;padding:0 3px;color:#99b;"
            "background:transparent;border:none;}"
            "QPushButton:hover{color:#ccf;}"
        )
        for label, tip, rx, ry in [
            ("Iso",   "Isometric view",  25.0, 35.0),
            ("Front", "Front view",       0.0,  0.0),
            ("Top",   "Top view",        90.0,  0.0),
            ("Side",  "Side view",        0.0, 90.0),
        ]:
            vbtn = QPushButton(label)
            vbtn.setFixedHeight(20)
            vbtn.setStyleSheet(_btn_style)
            vbtn.setToolTip(tip)
            vbtn.clicked.connect(lambda checked=False, x=rx, y=ry: self.set_view(x, y))
            bl.addWidget(vbtn)

        bl.addStretch()

        self._transparent_check = QCheckBox("Transparent")
        self._transparent_check.setChecked(True)
        self._transparent_check.setStyleSheet(
            "QCheckBox { color: #779; font-size: 11px; }"
            "QCheckBox::indicator { width: 13px; height: 13px; }"
        )
        self._transparent_check.setToolTip("Show model as semi-transparent (useful for inspecting geometry)")
        bl.addWidget(self._transparent_check)

        root.addWidget(bar)

        # ── Dimensions info bar ───────────────────────────────────────────────
        info_bar = QWidget()
        info_bar.setFixedHeight(22)
        info_bar.setStyleSheet("background: #12131a;")
        il = QHBoxLayout(info_bar)
        il.setContentsMargins(8, 0, 8, 0)
        il.setSpacing(0)

        self._dims_lbl = QLabel("")
        self._dims_lbl.setStyleSheet("color: #667; font-size: 10px;")
        il.addWidget(self._dims_lbl)
        il.addStretch()

        root.addWidget(info_bar)

        self._scale_spin.valueChanged.connect(self._on_scale_changed)
        self._transparent_check.toggled.connect(self._on_transparent_changed)
        self._gl.model_extents.connect(self._on_model_extents)

    # ── Scale control ─────────────────────────────────────────────────────────

    @property
    def model_scale(self) -> float:
        """Current model scale as a fraction (1.0 = 100%).
        Used by the slicer to physically scale the STL before slicing."""
        return self._scale_spin.value() / 100.0

    def _on_model_extents(self, x_mm: float, y_mm: float, z_mm: float) -> None:
        self._update_dims_label(x_mm, y_mm, z_mm)

    def _update_dims_label(self, x_mm: float = None, y_mm: float = None, z_mm: float = None) -> None:
        if x_mm is None:
            x_mm, y_mm, z_mm = self._gl._raw_extents
        if x_mm == 0.0 and y_mm == 0.0 and z_mm == 0.0:
            self._dims_lbl.setText("")
            return
        s = self._scale_spin.value() / 100.0
        sx, sy, sz = x_mm * s, y_mm * s, z_mm * s
        max_d = max(sx, sy)
        self._dims_lbl.setText(
            f"H: {sz:.1f}mm   W: {sx:.1f}mm   D: {sy:.1f}mm   Ø: {max_d:.1f}mm"
        )

    def _on_scale_changed(self, pct: float) -> None:
        self._gl._model_scale = pct / 100.0
        self._gl.update()
        self._update_dims_label()

    def _reset_scale(self) -> None:
        self._scale_spin.setValue(100.0)

    def _on_transparent_changed(self, checked: bool) -> None:
        self._gl._transparent = checked
        self._gl.update()

    # ── Proxy API ─────────────────────────────────────────────────────────────

    def load_stl(self, path: str) -> None:
        self._gl.load_stl(path)

    def clear(self) -> None:
        self._gl.clear()
        self._dims_lbl.setText("")

    def set_print_volume(self, bed_x: float, bed_y: float, max_z: float) -> None:
        self._gl.set_print_volume(bed_x, bed_y, max_z)

    def reset_view(self) -> None:
        self._gl.reset_view()

    def set_view(self, rot_x: float, rot_y: float) -> None:
        self._gl.set_view(rot_x, rot_y)

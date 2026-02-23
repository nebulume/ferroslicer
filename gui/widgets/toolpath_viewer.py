"""
3D toolpath viewer — OpenGL accelerated (QOpenGLWidget).

Features:
  • GL_LINE_STRIP segment rendering, no diagonal travel artefacts
  • Z-range filter bar (0.1 % steps) clips via fragment-shader discard
  • Seam markers: orange GL_POINTS wherever one full revolution completes
    — these form a vertical seam line that maps to the visible phase-seam
    on the printed part.  Toggle with the "Seams" checkbox.

Color gradient: blue (bed) → orange (top).

Interaction:
  Left drag    — rotate
  Right drag   — pan
  Scroll       — zoom
  Double-click — reset view
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
    QWidget, QVBoxLayout, QHBoxLayout, QSlider, QLabel, QPushButton,
    QCheckBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot


# ── GLSL shaders ─────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aColor;
uniform mat4 uMVP;
out vec3  vColor;
out float vZ;       // normalized print-height: -1 = bed, +1 = top
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vColor = aColor;
    vZ = -aPos.y;   // stored y = -(norm_z), so -y recovers norm_z
}
"""

_FRAG = """
#version 330 core
in  vec3  vColor;
in  float vZ;
out vec4  fragColor;
uniform float uZLo;
uniform float uZHi;
void main() {
    if (vZ < uZLo || vZ > uZHi) discard;
    fragColor = vec4(vColor, 0.92);
}
"""


# ── Background GCode parser ───────────────────────────────────────────────────

class GCodeLoaderThread(QThread):
    """
    Parses a .gcode file and emits three arrays:
      pts      — (N, 3) float32  all extrusion positions [x, y, z]
      segments — list of (start_idx, count) per continuous extrusion segment
      seam_pts — (M, 3) float32  positions where one full XY revolution
                 completes; these form the vertical seam line visible on
                 the printed part.
    """
    loaded = pyqtSignal(object, object, object)
    error  = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            xs, ys, zs     = [], [], []
            seam_xs = seam_ys = seam_zs = None
            seam_xs, seam_ys, seam_zs = [], [], []

            segments: list[tuple[int, int]] = []
            seg_start   = 0
            had_travel  = True
            relative_e  = False
            cur_x = cur_y = cur_z = cur_e = 0.0

            # Revolution tracking for seam dots
            prev_ex = prev_ey = None
            prev_angle   = None
            cumul_angle  = 0.0
            last_rev_ang = 0.0

            with open(self.path, "r") as fh:
                for raw in fh:
                    if len(raw) < 2:
                        continue
                    c0, c1 = raw[0], raw[1]

                    if c0 == "M" and c1 == "8" and len(raw) > 2 and raw[2] in "23":
                        relative_e = (raw[2] == "3"); continue

                    if c0 == "G" and c1 == "9" and len(raw) > 2 and raw[2] == "2":
                        for word in raw.split():
                            if word[0] == ";": break
                            if word[0] == "E":
                                try: cur_e = float(word[1:])
                                except ValueError: pass
                        continue

                    if c0 != "G" or c1 not in "01":
                        continue
                    if len(raw) > 2 and raw[2] not in " \t\r\n;":
                        continue

                    nx, ny, nz, ne = cur_x, cur_y, cur_z, cur_e
                    has_xy = has_e = False
                    for word in raw.split():
                        if not word: continue
                        wc = word[0]
                        if wc == ";": break
                        if wc == "X":
                            try: nx = float(word[1:]); has_xy = True
                            except ValueError: pass
                        elif wc == "Y":
                            try: ny = float(word[1:]); has_xy = True
                            except ValueError: pass
                        elif wc == "Z":
                            try: nz = float(word[1:])
                            except ValueError: pass
                        elif wc == "E":
                            try: ne = float(word[1:]); has_e = True
                            except ValueError: pass

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

                        # ── Seam / revolution detection ──────────────────────
                        # Track cumulative XY angle; every 360° = one revolution
                        if prev_ex is not None:
                            dx = nx - prev_ex
                            dy = ny - prev_ey
                            if math.hypot(dx, dy) > 0.001:
                                a = math.atan2(dy, dx)
                                if prev_angle is not None:
                                    da = a - prev_angle
                                    while da >  math.pi: da -= 2 * math.pi
                                    while da < -math.pi: da += 2 * math.pi
                                    cumul_angle += da
                                    # Complete revolution — CW or CCW
                                    while cumul_angle - last_rev_ang >= 2 * math.pi:
                                        seam_xs.append(nx)
                                        seam_ys.append(ny)
                                        seam_zs.append(nz)
                                        last_rev_ang += 2 * math.pi
                                    while cumul_angle - last_rev_ang <= -2 * math.pi:
                                        seam_xs.append(nx)
                                        seam_ys.append(ny)
                                        seam_zs.append(nz)
                                        last_rev_ang -= 2 * math.pi
                                prev_angle = a
                        prev_ex, prev_ey = nx, ny

                    elif has_xy:
                        had_travel = True

                    cur_x, cur_y, cur_z, cur_e = nx, ny, nz, ne

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

            if seam_xs:
                seam_pts = np.column_stack([
                    np.array(seam_xs, dtype=np.float32),
                    np.array(seam_ys, dtype=np.float32),
                    np.array(seam_zs, dtype=np.float32),
                ])
            else:
                seam_pts = np.empty((0, 3), dtype=np.float32)

            self.loaded.emit(pts, segments, seam_pts)

        except Exception as exc:
            self.error.emit(str(exc))


# ── Inner GL widget ───────────────────────────────────────────────────────────

class _ToolpathGL(QOpenGLWidget):
    """All OpenGL rendering. Wrapped by ToolpathViewer which adds the control bar."""

    def __init__(self, parent=None):
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

        self.rot_x: float = 25.0
        self.rot_y: float = 35.0
        self.zoom:  float = 0.85
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0

        self._z_lo: float = -1.0
        self._z_hi: float =  1.0
        self._show_seams: bool = True

        self._n_verts:    int  = 0
        self._segments:   list = []
        self._seam_n_verts: int = 0
        self._loading:    bool = False
        self._error:      str  = ""
        self._label:      str  = ""

        self._pending:          Optional[np.ndarray] = None
        self._pending_segments: list = []
        self._pending_seam:     Optional[np.ndarray] = None

        self._gl_ready: bool = False
        self._prog:     Optional[QOpenGLShaderProgram]     = None
        self._vao:      Optional[QOpenGLVertexArrayObject] = None
        self._vbo:      Optional[QOpenGLBuffer]            = None
        self._seam_vao: Optional[QOpenGLVertexArrayObject] = None
        self._seam_vbo: Optional[QOpenGLBuffer]            = None
        self._mvp_loc:  int = -1
        self._z_lo_loc: int = -1
        self._z_hi_loc: int = -1

        self._last_mouse = None
        self._mouse_btn  = Qt.MouseButton.NoButton
        self._loader: Optional[GCodeLoaderThread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load_gcode(self, path: str) -> None:
        self._loading = True
        self._error   = ""
        self._n_verts = self._seam_n_verts = 0
        self._segments = []
        self._label    = Path(path).name
        self._pending  = self._pending_seam = None
        self._pending_segments = []
        self.update()
        self._loader = GCodeLoaderThread(path, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.error.connect(self._on_error)
        self._loader.start()

    def clear(self) -> None:
        self._n_verts = self._seam_n_verts = 0
        self._segments = []
        self._loading = self._error == "" and False
        self._error = self._label = ""
        self._pending = self._pending_seam = None
        self._pending_segments = []
        if self._gl_ready:
            self.makeCurrent()
            for vbo in (self._vbo, self._seam_vbo):
                if vbo:
                    vbo.bind(); vbo.allocate(b"", 0); vbo.release()
            self.doneCurrent()
        self.update()

    def reset_view(self) -> None:
        self.rot_x, self.rot_y = 25.0, 35.0
        self.zoom = 0.85
        self.pan_x = self.pan_y = 0.0
        self.update()

    # ── Background thread slots ───────────────────────────────────────────────

    @pyqtSlot(object, object, object)
    def _on_loaded(self, pts: np.ndarray, segments: list, seam_pts: np.ndarray):
        self._loading = False
        n_segs = len(segments)
        self._label = (
            f"{len(pts):,} pts  •  {n_segs} seg{'s' if n_segs!=1 else ''}"
            + (f"  •  {len(seam_pts)} seam marks" if len(seam_pts) > 0 else "")
        )

        mn = pts.min(0); mx = pts.max(0)
        center  = (mn + mx) * 0.5
        scale_v = float(np.abs(pts - center).max())
        if scale_v < 1e-9: scale_v = 1.0

        def _transform(p):
            p = (p - center) / scale_v
            p = p[:, [0, 2, 1]].copy()
            p[:, 1] *= -1.0
            return p

        pts_n = _transform(pts)

        z_min = pts[:, 2].min(); z_max = pts[:, 2].max()
        t = (pts[:, 2] - z_min) / max(z_max - z_min, 1e-9)
        colours = np.column_stack([
            0.12 + 0.78 * t,
            0.39 + 0.31 * t,
            0.86 - 0.59 * t,
        ]).astype(np.float32)

        self._pending = np.column_stack(
            [pts_n.astype(np.float32), colours]
        ).astype(np.float32, copy=False)
        self._pending_segments = segments

        # Seam dots — bright orange, same coordinate space
        if len(seam_pts) > 0:
            sp_n = _transform(seam_pts)
            seam_col = np.tile(
                np.array([[1.0, 0.55, 0.10]], dtype=np.float32),
                (len(sp_n), 1)
            )
            self._pending_seam = np.column_stack(
                [sp_n.astype(np.float32), seam_col]
            ).astype(np.float32, copy=False)
        else:
            self._pending_seam = np.empty((0, 6), dtype=np.float32)

        self.reset_view()

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self._loading = False
        self._error   = msg
        self.update()

    # ── OpenGL lifecycle ──────────────────────────────────────────────────────

    def _make_vao_vbo(self):
        """Create a fresh VAO+VBO pair with the standard [xyz rgb] layout."""
        vao = QOpenGLVertexArrayObject(self)
        vao.create(); vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        vbo.create(); vbo.bind()
        vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        stride = 6 * 4
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, ctypes.c_void_p(12))
        gl.glEnableVertexAttribArray(1)
        vao.release(); vbo.release()
        return vao, vbo

    def initializeGL(self):
        gl.glClearColor(0.085, 0.1, 0.13, 1.0)
        gl.glEnable(gl.GL_LINE_SMOOTH)
        gl.glHint(gl.GL_LINE_SMOOTH_HINT, gl.GL_NICEST)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        self._prog = QOpenGLShaderProgram(self)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,   _VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAG)
        self._prog.link()
        self._mvp_loc  = self._prog.uniformLocation("uMVP")
        self._z_lo_loc = self._prog.uniformLocation("uZLo")
        self._z_hi_loc = self._prog.uniformLocation("uZHi")

        self._vao,      self._vbo      = self._make_vao_vbo()
        self._seam_vao, self._seam_vbo = self._make_vao_vbo()
        self._gl_ready = True

    def resizeGL(self, w: int, h: int):
        gl.glViewport(0, 0, w, h)

    def paintGL(self):
        # Upload pending toolpath data
        if self._pending is not None and self._gl_ready:
            data = self._pending
            self._vbo.bind()
            self._vbo.allocate(data.tobytes(), data.nbytes)
            self._vbo.release()
            self._n_verts  = len(data)
            self._segments = self._pending_segments
            self._pending  = None
            self._pending_segments = []

        # Upload pending seam data
        if self._pending_seam is not None and self._gl_ready:
            sd = self._pending_seam
            self._seam_vbo.bind()
            self._seam_vbo.allocate(sd.tobytes(), sd.nbytes)
            self._seam_vbo.release()
            self._seam_n_verts = len(sd)
            self._pending_seam = None

        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if self._n_verts < 2 or not self._gl_ready or not self._segments:
            return

        self._prog.bind()
        mvp = self._build_mvp(self.width(), self.height())
        gl.glUniformMatrix4fv(self._mvp_loc, 1, gl.GL_TRUE, mvp.flatten())
        gl.glUniform1f(self._z_lo_loc, self._z_lo)
        gl.glUniform1f(self._z_hi_loc, self._z_hi)

        # ── Toolpath lines ────────────────────────────────────────────────────
        self._vao.bind()
        for start, count in self._segments:
            if count >= 2:
                gl.glDrawArrays(gl.GL_LINE_STRIP, start, count)
        self._vao.release()

        # ── Seam marker dots ──────────────────────────────────────────────────
        if self._show_seams and self._seam_n_verts > 0:
            gl.glPointSize(7.0)
            self._seam_vao.bind()
            gl.glDrawArrays(gl.GL_POINTS, 0, self._seam_n_verts)
            self._seam_vao.release()
            gl.glPointSize(1.0)

        self._prog.release()

    def paintEvent(self, event):
        super().paintEvent(event)
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
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0, QColor(22, 26, 34))
            grad.setColorAt(1, QColor(14, 18, 24))
            painter.fillRect(self.rect(), grad)
            painter.setPen(QColor(70, 70, 90))
            painter.setFont(QFont("Helvetica", 13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Generate GCode to see toolpath preview")
        else:
            painter.setPen(QColor(100, 100, 130))
            painter.setFont(QFont("Helvetica", 10))
            painter.drawText(8, self.height() - 8, self._label)
        painter.end()

    # ── MVP ───────────────────────────────────────────────────────────────────

    def _build_mvp(self, w: int, h: int) -> np.ndarray:
        rx, ry = math.radians(self.rot_x), math.radians(self.rot_y)
        crx, srx = math.cos(rx), math.sin(rx)
        cry, sry = math.cos(ry), math.sin(ry)
        Rx = np.array([[1,0,0,0],[0,crx,-srx,0],[0,srx,crx,0],[0,0,0,1]], np.float32)
        Ry = np.array([[cry,0,sry,0],[0,1,0,0],[-sry,0,cry,0],[0,0,0,1]], np.float32)
        R  = Rx @ Ry
        sx = min(w,h)/w * self.zoom
        sy = min(w,h)/h * self.zoom
        px =  2.0*self.pan_x/w
        py = -2.0*self.pan_y/h
        S = np.array([[sx,0,0,px],[0,-sy,0,py],[0,0,1,0],[0,0,0,1]], np.float32)
        return (S @ R).astype(np.float32)

    # ── Interaction ───────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        self._last_mouse = e.position(); self._mouse_btn = e.button()

    def mouseMoveEvent(self, e):
        if self._last_mouse is None: return
        dx = e.position().x() - self._last_mouse.x()
        dy = e.position().y() - self._last_mouse.y()
        self._last_mouse = e.position()
        if self._mouse_btn == Qt.MouseButton.LeftButton:
            self.rot_y += dx*0.5; self.rot_x += dy*0.5; self.update()
        elif self._mouse_btn == Qt.MouseButton.RightButton:
            self.pan_x += dx; self.pan_y += dy; self.update()

    def mouseReleaseEvent(self, e):
        self._last_mouse = None; self._mouse_btn = Qt.MouseButton.NoButton

    def mouseDoubleClickEvent(self, e): self.reset_view()

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1/1.15
        self.zoom = max(0.05, min(20.0, self.zoom*f)); self.update()


# ── Public wrapper widget ─────────────────────────────────────────────────────

class ToolpathViewer(QWidget):
    """
    GL canvas + layer-range bar + seam-marker toggle.

    Layer sliders use 0.1 % steps (range 0–1000 internally, displayed as
    0.0–100.0 %) so individual layers can be isolated precisely.

    The orange seam dots mark every full XY revolution; they form a vertical
    line on the toolpath that corresponds to the visible alternation seam on
    the finished print.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._gl = _ToolpathGL(self)
        root.addWidget(self._gl, stretch=1)

        # ── Control bar ───────────────────────────────────────────────────────
        bar = QWidget()
        bar.setFixedHeight(30)
        bar.setStyleSheet("background: #161820;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 2, 8, 2)
        bl.setSpacing(6)

        lbl = QLabel("Layers:")
        lbl.setStyleSheet("color: #779; font-size: 11px;")
        lbl.setFixedWidth(46)
        bl.addWidget(lbl)

        bl.addWidget(self._lbl("Bot"))
        self._lo = QSlider(Qt.Orientation.Horizontal)
        self._lo.setRange(0, 1000)
        self._lo.setValue(0)
        self._lo.setFixedHeight(18)
        self._lo.setToolTip("Bottom of visible height range (0.1 % steps)")
        bl.addWidget(self._lo)

        bl.addWidget(self._lbl("Top"))
        self._hi = QSlider(Qt.Orientation.Horizontal)
        self._hi.setRange(0, 1000)
        self._hi.setValue(1000)
        self._hi.setFixedHeight(18)
        self._hi.setToolTip("Top of visible height range (0.1 % steps)")
        bl.addWidget(self._hi)

        self._range_lbl = QLabel("0.0 – 100.0%")
        self._range_lbl.setStyleSheet("color: #88a; font-size: 10px;")
        self._range_lbl.setFixedWidth(82)
        self._range_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self._range_lbl)

        all_btn = QPushButton("All")
        all_btn.setFixedSize(32, 20)
        all_btn.setStyleSheet("QPushButton{font-size:10px;padding:0;color:#99b;}"
                              "QPushButton:hover{color:#ccf;}")
        all_btn.setToolTip("Show all layers")
        all_btn.clicked.connect(self._reset_range)
        bl.addWidget(all_btn)

        sep = QLabel("|")
        sep.setStyleSheet("color: #334;")
        bl.addWidget(sep)

        self._seam_chk = QCheckBox("Seams")
        self._seam_chk.setChecked(True)
        self._seam_chk.setStyleSheet(
            "QCheckBox { color: #f8851a; font-size: 10px; }"
            "QCheckBox::indicator { width:12px; height:12px; }"
        )
        self._seam_chk.setToolTip(
            "Show orange dots where each revolution completes\n"
            "(marks the seam / alternation line visible on the print)"
        )
        self._seam_chk.stateChanged.connect(self._on_seam_toggle)
        bl.addWidget(self._seam_chk)

        root.addWidget(bar)

        self._lo.valueChanged.connect(self._on_range_changed)
        self._hi.valueChanged.connect(self._on_range_changed)

    def _lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet("color: #667; font-size: 10px;")
        return l

    # ── Range control ─────────────────────────────────────────────────────────

    def _on_range_changed(self):
        lo = self._lo.value()
        hi = self._hi.value()
        if lo > hi:
            if self.sender() is self._lo:
                lo = hi
                self._lo.blockSignals(True); self._lo.setValue(lo); self._lo.blockSignals(False)
            else:
                hi = lo
                self._hi.blockSignals(True); self._hi.setValue(hi); self._hi.blockSignals(False)
        self._range_lbl.setText(f"{lo/10:.1f} – {hi/10:.1f}%")
        self._gl._z_lo = lo / 500.0 - 1.0
        self._gl._z_hi = hi / 500.0 - 1.0
        self._gl.update()

    def _reset_range(self):
        self._lo.blockSignals(True); self._hi.blockSignals(True)
        self._lo.setValue(0); self._hi.setValue(1000)
        self._lo.blockSignals(False); self._hi.blockSignals(False)
        self._range_lbl.setText("0.0 – 100.0%")
        self._gl._z_lo = -1.0; self._gl._z_hi = 1.0
        self._gl.update()

    def _on_seam_toggle(self, state):
        self._gl._show_seams = (state == Qt.CheckState.Checked.value)
        self._gl.update()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_gcode(self, path: str) -> None:
        self._reset_range()
        self._gl.load_gcode(path)

    def clear(self) -> None:
        self._reset_range()
        self._gl.clear()

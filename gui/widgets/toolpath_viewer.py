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




# ── Background GCode parser ───────────────────────────────────────────────────

class GCodeLoaderThread(QThread):
    """
    Parses a .gcode file and emits four arrays:
      pts      — (N, 3) float32  all extrusion positions [x, y, z]
      segments — list of (start_idx, count) per continuous extrusion segment
      seam_pts — (M, 3) float32  positions where one full XY revolution
                 completes; these form the vertical seam line visible on
                 the printed part.
      speeds   — (N,) float32  print speed in mm/s at each extrusion point
    """
    loaded = pyqtSignal(object, object, object, object)
    error  = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            xs, ys, zs, spds = [], [], [], []

            segments: list[tuple[int, int]] = []
            seg_start  = 0
            had_travel = True
            relative_e = False
            cur_x = cur_y = cur_z = cur_e = cur_f = 0.0

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

                    nx, ny, nz, ne, nf = cur_x, cur_y, cur_z, cur_e, cur_f
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
                        elif wc == "F":
                            try: nf = float(word[1:])
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
                        spds.append(nf / 60.0)   # mm/min → mm/s
                    elif has_xy:
                        had_travel = True

                    cur_x, cur_y, cur_z, cur_e, cur_f = nx, ny, nz, ne, nf

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
            speeds = np.array(spds, dtype=np.float32)

            # ── Seam / revolution detection ───────────────────────────────────
            # Track the position angle of each point relative to the XY centroid.
            # The position angle completes exactly 360° per physical revolution.
            #
            # With seam_shift != 0 the phase alternation happens every
            # cycle_len_revs = layer_alternation + seam_shift / waves_per_rev
            # revolutions, which is a non-integer that makes the seam diagonal.
            # We read the matching .log file (same basename, .log suffix) to
            # get the exact cycle length so the dots follow that diagonal.
            #
            # Critical: reset tracking at each segment start (after a travel
            # move) so purge/skirt accumulation doesn't corrupt the spiral seam.
            cycle_len_revs = 1.0   # default: one dot per revolution
            try:
                log_path = Path(self.path).with_suffix(".log")
                if log_path.exists():
                    layer_alt_v   = 1
                    seam_shift_v  = 0.0
                    wave_count_v  = None
                    wave_spacing_v = 0.0
                    avg_perim_v   = 0.0
                    with open(log_path, "r") as lf:
                        for ln in lf:
                            ln = ln.strip()
                            if ln.startswith("layer_alternation:"):
                                layer_alt_v = int(ln.split(":", 1)[1].strip())
                            elif ln.startswith("seam_shift:"):
                                seam_shift_v = float(ln.split(":", 1)[1].strip())
                            elif ln.startswith("wave_count:"):
                                val = ln.split(":", 1)[1].strip()
                                if val not in ("null", "None", ""):
                                    wave_count_v = float(val)
                            elif ln.startswith("wave_spacing:"):
                                wave_spacing_v = float(ln.split(":", 1)[1].strip())
                            elif ln.startswith("avg_perimeter:"):
                                avg_perim_v = float(ln.split(":", 1)[1].strip())
                    if wave_count_v and wave_count_v > 0:
                        wpr = wave_count_v
                    elif wave_spacing_v > 0 and avg_perim_v > 0:
                        wpr = avg_perim_v / wave_spacing_v
                    else:
                        wpr = 0.0
                    cycle_len_revs = float(layer_alt_v)
                    if seam_shift_v != 0 and wpr > 0:
                        cycle_len_revs += seam_shift_v / wpr
            except Exception:
                pass

            seam_interval = cycle_len_revs * 2 * math.pi

            seam_pts = np.empty((0, 3), dtype=np.float32)
            if len(xs) > 3:
                cx = float(pts[:, 0].mean())
                cy = float(pts[:, 1].mean())

                seg_start_set = set(s for s, _ in segments)

                seam_xs: list[float] = []
                seam_ys: list[float] = []
                seam_zs: list[float] = []
                prev_pa    = None
                cumul_pos  = 0.0
                last_cross = 0.0

                for i, (x, y, z) in enumerate(zip(xs, ys, zs)):
                    # Reset ALL tracking at each segment start so that purge
                    # and skirt accumulation doesn't offset the spiral seam.
                    if i in seg_start_set:
                        prev_pa    = None
                        cumul_pos  = 0.0
                        last_cross = 0.0

                    pa = math.atan2(y - cy, x - cx)
                    if prev_pa is not None:
                        da = pa - prev_pa
                        while da >  math.pi: da -= 2 * math.pi
                        while da < -math.pi: da += 2 * math.pi
                        cumul_pos += da
                        while cumul_pos - last_cross >= seam_interval:
                            seam_xs.append(x); seam_ys.append(y); seam_zs.append(z)
                            last_cross += seam_interval
                    prev_pa = pa

                if seam_xs:
                    seam_pts = np.column_stack([
                        np.array(seam_xs, dtype=np.float32),
                        np.array(seam_ys, dtype=np.float32),
                        np.array(seam_zs, dtype=np.float32),
                    ])

            self.loaded.emit(pts, segments, seam_pts, speeds)

        except Exception as exc:
            self.error.emit(str(exc))


# ── Speed colormap (blue → cyan → green → yellow → red) ─────────────────────

def _speed_colormap(t: np.ndarray) -> np.ndarray:
    """Map normalized speed values t ∈ [0,1] to RGB colors using a
    blue→cyan→green→yellow→red gradient. Output shape (N,3) float32."""
    stops_t = np.array([0.0,  0.25, 0.5,  0.75, 1.0],  dtype=np.float32)
    stops_r = np.array([0.08, 0.0,  0.05, 1.0,  1.0],  dtype=np.float32)
    stops_g = np.array([0.18, 0.75, 0.92, 0.90, 0.08], dtype=np.float32)
    stops_b = np.array([0.95, 0.95, 0.08, 0.0,  0.0],  dtype=np.float32)
    r = np.interp(t, stops_t, stops_r).astype(np.float32)
    g = np.interp(t, stops_t, stops_g).astype(np.float32)
    b = np.interp(t, stops_t, stops_b).astype(np.float32)
    return np.column_stack([r, g, b])


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
        self._vz_min: float = -1.0   # actual vZ range of loaded toolpath
        self._vz_max: float =  1.0
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

        # Cached per-point data for color mode switching (no re-parse needed)
        self._pts_n:           Optional[np.ndarray] = None   # (N,3) normalized
        self._height_colours:  Optional[np.ndarray] = None   # (N,3) float32
        self._speed_colours:   Optional[np.ndarray] = None   # (N,3) float32
        self._speed_min:       float = 0.0
        self._speed_max:       float = 0.0
        self._color_mode:      str   = "speed"   # "speed" | "height"

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

        # ── Print volume box ──────────────────────────────────────────────────
        self._bed_dims:    Optional[tuple]                    = None
        self._norm_center: Optional[np.ndarray]               = None
        self._norm_scale_v: float                             = 1.0
        self._box_prog:      Optional[QOpenGLShaderProgram]     = None
        self._box_vao:       Optional[QOpenGLVertexArrayObject] = None
        self._box_vbo:       Optional[QOpenGLBuffer]            = None
        self._box_mvp_loc:   int                                = -1
        self._box_color_loc: int                                = -1
        self._pending_box:   Optional[np.ndarray]               = None
        self._box_n_verts:   int                                = 0

        # ── Bed grid (cm + mm squares) ────────────────────────────────────────
        self._grid_cm_vao:     Optional[QOpenGLVertexArrayObject] = None
        self._grid_cm_vbo:     Optional[QOpenGLBuffer]            = None
        self._pending_grid_cm: Optional[np.ndarray]               = None
        self._grid_cm_n_verts: int                                = 0
        self._grid_mm_vao:     Optional[QOpenGLVertexArrayObject] = None
        self._grid_mm_vbo:     Optional[QOpenGLBuffer]            = None
        self._pending_grid_mm: Optional[np.ndarray]               = None
        self._grid_mm_n_verts: int                                = 0

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
        self._loading = False
        self._error = self._label = ""
        self._pending = self._pending_seam = None
        self._pending_segments = []
        if self._gl_ready:
            self.makeCurrent()
            for vbo in (self._vbo, self._seam_vbo):
                if vbo is not None:
                    vbo.bind(); vbo.allocate(b"", 0); vbo.release()
            self.doneCurrent()
        self.update()

    def reset_view(self) -> None:
        self.rot_x, self.rot_y = 25.0, 35.0
        self.zoom = 0.85
        self.pan_x = self.pan_y = 0.0
        self.update()

    def set_print_volume(self, bed_x: float, bed_y: float, max_z: float) -> None:
        self._bed_dims = (bed_x, bed_y, max_z)
        self._queue_box()
        self.update()

    def _queue_box(self) -> None:
        """Compute box in normalized toolpath space and queue for GL upload."""
        if self._bed_dims is None or self._norm_center is None:
            return
        bed_x, bed_y, max_z = self._bed_dims
        # 8 corners of printer volume in printer coords (front-left origin)
        raw = np.array([
            [0,     0,     0    ], [bed_x, 0,     0    ],
            [0,     bed_y, 0    ], [bed_x, bed_y, 0    ],
            [0,     0,     max_z], [bed_x, 0,     max_z],
            [0,     bed_y, max_z], [bed_x, bed_y, max_z],
        ], dtype=np.float32)
        # Apply same normalization as toolpath points
        n = (raw - self._norm_center) / self._norm_scale_v
        # Axis swap: [x, z, y] then flip y  (same as _transform in _on_loaded)
        n = n[:, [0, 2, 1]].copy()
        n[:, 1] *= -1.0
        # Build 24-vertex edge list
        c = list(n)
        edges = [
            (0,1),(2,3),(4,5),(6,7),
            (0,2),(1,3),(4,6),(5,7),
            (0,4),(1,5),(2,6),(3,7),
        ]
        verts = []
        for a, b in edges:
            verts.append(c[a]); verts.append(c[b])
        self._pending_box = np.array(verts, dtype=np.float32)
        self._queue_grid()

    def _queue_grid(self) -> None:
        """Compute and queue cm (10 mm) and mm (1 mm) bed surface grid."""
        if self._bed_dims is None or self._norm_center is None:
            return
        bed_x, bed_y, _ = self._bed_dims

        def _make_lines(step: float) -> np.ndarray:
            xs = np.arange(0, bed_x + 1e-9, step, dtype=np.float32)
            ys = np.arange(0, bed_y + 1e-9, step, dtype=np.float32)
            verts = []
            for y in ys:                          # horizontal lines (parallel to X)
                verts.append([0,     y, 0])
                verts.append([bed_x, y, 0])
            for x in xs:                          # vertical lines (parallel to Y)
                verts.append([x, 0,     0])
                verts.append([x, bed_y, 0])
            raw = np.array(verts, dtype=np.float32)
            # Same normalisation + axis swap as toolpath points and box corners
            n = (raw - self._norm_center) / self._norm_scale_v
            n = n[:, [0, 2, 1]].copy()
            n[:, 1] *= -1.0
            return n

        self._pending_grid_cm = _make_lines(10.0)
        self._pending_grid_mm = _make_lines(1.0)

    # ── Background thread slots ───────────────────────────────────────────────

    @pyqtSlot(object, object, object, object)
    def _on_loaded(self, pts: np.ndarray, segments: list,
                   seam_pts: np.ndarray, speeds: np.ndarray):
        self._loading = False
        n_segs = len(segments)
        spd_min = float(speeds.min()) if len(speeds) else 0.0
        spd_max = float(speeds.max()) if len(speeds) else 0.0
        self._speed_min = spd_min
        self._speed_max = spd_max
        self._label = (
            f"{len(pts):,} pts  •  {n_segs} seg{'s' if n_segs!=1 else ''}"
            + (f"  •  {len(seam_pts)} seam marks" if len(seam_pts) > 0 else "")
            + (f"  •  {spd_min:.0f}–{spd_max:.0f} mm/s" if spd_max > 0 else "")
        )

        mn = pts.min(0); mx = pts.max(0)
        center  = (mn + mx) * 0.5
        scale_v = float(np.abs(pts - center).max())
        if scale_v < 1e-9: scale_v = 1.0

        self._norm_center  = center
        self._norm_scale_v = scale_v

        def _transform(p):
            p = (p - center) / scale_v
            p = p[:, [0, 2, 1]].copy()
            p[:, 1] *= -1.0
            return p

        pts_n = _transform(pts)
        self._pts_n = pts_n.astype(np.float32)

        # Store actual vZ range
        self._vz_min = float(-pts_n[:, 1].max())
        self._vz_max = float(-pts_n[:, 1].min())
        self._z_lo   = self._vz_min
        self._z_hi   = self._vz_max

        # ── Height colour (blue low → orange high) ───────────────────────────
        z_min = pts[:, 2].min(); z_max = pts[:, 2].max()
        t_h = (pts[:, 2] - z_min) / max(z_max - z_min, 1e-9)
        self._height_colours = np.column_stack([
            0.12 + 0.78 * t_h,
            0.39 + 0.31 * t_h,
            0.86 - 0.59 * t_h,
        ]).astype(np.float32)

        # ── Speed colour (blue slow → red fast) ──────────────────────────────
        t_s = (speeds - spd_min) / max(spd_max - spd_min, 1e-9)
        self._speed_colours = _speed_colormap(t_s)

        # Choose colour array based on current mode
        colours = self._speed_colours if self._color_mode == "speed" else self._height_colours

        self._pending = np.column_stack(
            [self._pts_n, colours]
        ).astype(np.float32, copy=False)
        self._pending_segments = segments

        # Seam dots — bright orange
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

        self._queue_box()
        self.reset_view()

    def set_color_mode(self, mode: str) -> None:
        """Switch between 'speed' and 'height' coloring without re-parsing."""
        self._color_mode = mode
        if self._pts_n is None:
            return
        colours = self._speed_colours if mode == "speed" else self._height_colours
        if colours is None:
            return
        self._pending = np.column_stack(
            [self._pts_n, colours]
        ).astype(np.float32, copy=False)
        self._pending_segments = self._segments[:]
        self.update()

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

        # ── Box shader ──────────────────────────────────────────────────────
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

        # ── cm grid VAO ───────────────────────────────────────────────────────
        self._grid_cm_vao = QOpenGLVertexArrayObject(self)
        self._grid_cm_vao.create(); self._grid_cm_vao.bind()
        self._grid_cm_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._grid_cm_vbo.create(); self._grid_cm_vbo.bind()
        self._grid_cm_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        self._grid_cm_vao.release(); self._grid_cm_vbo.release()

        # ── mm grid VAO ───────────────────────────────────────────────────────
        self._grid_mm_vao = QOpenGLVertexArrayObject(self)
        self._grid_mm_vao.create(); self._grid_mm_vao.bind()
        self._grid_mm_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._grid_mm_vbo.create(); self._grid_mm_vbo.bind()
        self._grid_mm_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        self._grid_mm_vao.release(); self._grid_mm_vbo.release()

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

        if not self._gl_ready:
            return

        mvp = self._build_mvp(self.width(), self.height())

        # ── Toolpath lines + seam markers ────────────────────────────────────
        if self._n_verts >= 2 and self._segments:
            self._prog.bind()
            gl.glUniformMatrix4fv(self._mvp_loc, 1, gl.GL_TRUE, mvp.flatten())
            gl.glUniform1f(self._z_lo_loc, self._z_lo)
            gl.glUniform1f(self._z_hi_loc, self._z_hi)

            self._vao.bind()
            for start, count in self._segments:
                if count >= 2:
                    gl.glDrawArrays(gl.GL_LINE_STRIP, start, count)
            self._vao.release()

            # ── Seam marker dots ──────────────────────────────────────────────
            if self._show_seams and self._seam_n_verts > 0:
                gl.glPointSize(7.0)
                self._seam_vao.bind()
                gl.glDrawArrays(gl.GL_POINTS, 0, self._seam_n_verts)
                self._seam_vao.release()
                gl.glPointSize(1.0)

            self._prog.release()

        # ── Bed grid + print volume box ───────────────────────────────────────
        if self._pending_box is not None:
            self._box_vbo.bind()
            self._box_vbo.allocate(self._pending_box.tobytes(), self._pending_box.nbytes)
            self._box_vbo.release()
            self._box_n_verts = len(self._pending_box)
            self._pending_box = None
        if self._pending_grid_cm is not None:
            data = self._pending_grid_cm
            self._grid_cm_vbo.bind()
            self._grid_cm_vbo.allocate(data.tobytes(), data.nbytes)
            self._grid_cm_vbo.release()
            self._grid_cm_n_verts = len(data)
            self._pending_grid_cm = None
        if self._pending_grid_mm is not None:
            data = self._pending_grid_mm
            self._grid_mm_vbo.bind()
            self._grid_mm_vbo.allocate(data.tobytes(), data.nbytes)
            self._grid_mm_vbo.release()
            self._grid_mm_n_verts = len(data)
            self._pending_grid_mm = None

        has_box  = self._box_n_verts > 0
        has_grid = self._grid_cm_n_verts > 0 or self._grid_mm_n_verts > 0
        if (has_box or has_grid) and self._box_prog is not None:
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            self._box_prog.bind()
            gl.glUniformMatrix4fv(self._box_mvp_loc, 1, gl.GL_TRUE, mvp.flatten())

            # mm grid — very faint
            if self._grid_mm_n_verts > 0:
                gl.glUniform4f(self._box_color_loc, 0.12, 0.18, 0.32, 0.20)
                self._grid_mm_vao.bind()
                gl.glDrawArrays(gl.GL_LINES, 0, self._grid_mm_n_verts)
                self._grid_mm_vao.release()

            # cm grid — clearly visible
            if self._grid_cm_n_verts > 0:
                gl.glUniform4f(self._box_color_loc, 0.22, 0.38, 0.65, 0.55)
                self._grid_cm_vao.bind()
                gl.glDrawArrays(gl.GL_LINES, 0, self._grid_cm_n_verts)
                self._grid_cm_vao.release()

            # Box wireframe
            if has_box:
                gl.glUniform4f(self._box_color_loc, 0.40, 0.55, 0.80, 0.30)
                self._box_vao.bind()
                gl.glDrawArrays(gl.GL_LINES, 0, self._box_n_verts)
                self._box_vao.release()

            self._box_prog.release()
            gl.glDisable(gl.GL_BLEND)

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

            # Speed legend (top-right corner, only when colour mode is speed)
            if self._color_mode == "speed" and self._speed_max > 0:
                self._draw_speed_legend(painter)
        painter.end()

    def _draw_speed_legend(self, painter: QPainter) -> None:
        """Draw a vertical colour bar with min/max speed labels."""
        bar_w, bar_h = 12, 80
        margin = 10
        x = self.width() - bar_w - margin - 42   # leave room for text
        y = margin + 16

        # Gradient bar matching the colormap
        grad = QLinearGradient(x, y + bar_h, x, y)   # bottom=slow, top=fast
        # stops: blue(0)→cyan(0.25)→green(0.5)→yellow(0.75)→red(1)
        grad.setColorAt(0.00, QColor(20,  46,  242))
        grad.setColorAt(0.25, QColor(0,   191, 242))
        grad.setColorAt(0.50, QColor(12,  234, 20))
        grad.setColorAt(0.75, QColor(255, 230, 0))
        grad.setColorAt(1.00, QColor(255, 20,  0))
        painter.fillRect(x, y, bar_w, bar_h, grad)
        painter.setPen(QColor(60, 70, 90))
        painter.drawRect(x, y, bar_w, bar_h)

        # Labels
        painter.setPen(QColor(190, 200, 220))
        painter.setFont(QFont("Helvetica", 9))
        painter.drawText(x + bar_w + 4, y + 10,       f"{self._speed_max:.0f} mm/s")
        painter.drawText(x + bar_w + 4, y + bar_h,    f"{self._speed_min:.0f} mm/s")
        painter.drawText(x + bar_w + 4, y + bar_h // 2 + 4, "Speed")

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
        S = np.array([[sx,0,0,px],[0,-sy,0,py],[0,0,0.05,0],[0,0,0,1]], np.float32)
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
        bar.setFixedHeight(39)
        bar.setStyleSheet("background: #161820;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 3, 14, 3)   # 14px right padding so last widget breathes
        bl.setSpacing(6)

        lbl = QLabel("Layers:")
        lbl.setStyleSheet("color: #779; font-size: 11px;")
        lbl.setFixedWidth(46)
        bl.addWidget(lbl)

        bl.addWidget(self._lbl("bot"))
        self._lo = QSlider(Qt.Orientation.Horizontal)
        self._lo.setRange(0, 1000)
        self._lo.setValue(0)
        self._lo.setFixedHeight(20)
        self._lo.setToolTip("Bottom of visible height range (0.1 % steps)")
        bl.addWidget(self._lo)

        bl.addWidget(self._lbl("top"))
        self._hi = QSlider(Qt.Orientation.Horizontal)
        self._hi.setRange(0, 1000)
        self._hi.setValue(1000)
        self._hi.setFixedHeight(20)
        self._hi.setToolTip("Top of visible height range (0.1 % steps)")
        bl.addWidget(self._hi)

        self._range_lbl = QLabel("0.0 – 100.0%")
        self._range_lbl.setStyleSheet("color: #88a; font-size: 10px; padding-right: 4px;")
        self._range_lbl.setFixedWidth(90)
        self._range_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self._range_lbl)

        all_btn = QPushButton("All")
        all_btn.setFixedSize(32, 22)
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
            "QCheckBox::indicator { width:13px; height:13px; }"
        )
        self._seam_chk.setToolTip(
            "Show orange dots where each revolution completes\n"
            "(marks the seam / alternation line visible on the print)"
        )
        self._seam_chk.stateChanged.connect(self._on_seam_toggle)
        bl.addWidget(self._seam_chk)

        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #334;")
        bl.addWidget(sep2)

        colour_lbl = QLabel("Colour:")
        colour_lbl.setStyleSheet("color: #779; font-size: 11px;")
        bl.addWidget(colour_lbl)

        from PyQt6.QtWidgets import QComboBox as _QCB
        self._colour_combo = _QCB()
        self._colour_combo.addItems(["Speed", "Height"])
        self._colour_combo.setFixedHeight(22)
        self._colour_combo.setFixedWidth(72)
        self._colour_combo.setStyleSheet(
            "QComboBox { background:#1a1f2a; color:#aac; font-size:10px; border:1px solid #334; }"
            "QComboBox::drop-down { border:none; }"
        )
        self._colour_combo.setToolTip(
            "Speed: blue (slow) → cyan → green → yellow → red (fast)\n"
            "Height: blue (bed) → orange (top)"
        )
        self._colour_combo.currentTextChanged.connect(self._on_colour_mode)
        bl.addWidget(self._colour_combo)

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
        vz_min = self._gl._vz_min
        vz_max = self._gl._vz_max
        self._gl._z_lo = vz_min + (lo / 1000.0) * (vz_max - vz_min)
        self._gl._z_hi = vz_min + (hi / 1000.0) * (vz_max - vz_min)
        self._gl.update()

    def _reset_range(self):
        self._lo.blockSignals(True); self._hi.blockSignals(True)
        self._lo.setValue(0); self._hi.setValue(1000)
        self._lo.blockSignals(False); self._hi.blockSignals(False)
        self._range_lbl.setText("0.0 – 100.0%")
        self._gl._z_lo = self._gl._vz_min
        self._gl._z_hi = self._gl._vz_max
        self._gl.update()

    def _on_seam_toggle(self, state):
        self._gl._show_seams = (state == Qt.CheckState.Checked.value)
        self._gl.update()

    def _on_colour_mode(self, text: str):
        self._gl.set_color_mode("speed" if text == "Speed" else "height")

    # ── Public API ────────────────────────────────────────────────────────────

    def load_gcode(self, path: str) -> None:
        self._reset_range()
        self._gl.load_gcode(path)

    def clear(self) -> None:
        self._reset_range()
        self._gl.clear()

    def set_print_volume(self, bed_x: float, bed_y: float, max_z: float) -> None:
        self._gl.set_print_volume(bed_x, bed_y, max_z)

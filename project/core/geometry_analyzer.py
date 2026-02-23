'''
Geometry analysis for perimeter extraction and curvature calculation.
NumPy-accelerated layer slicing for ~50x speedup over pure Python.
'''

import math
from typing import List, Tuple, Optional
from dataclasses import dataclass
from .stl_parser import Vector3, STLModel
from .logger import setup_logger

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import slicer_core as _rust
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False

logger = setup_logger("geometry_analyzer")


@dataclass
class Layer:
    """Represents a single layer at specific Z height."""
    z: float
    points: List[Vector3]
    perimeter_length: float = 0.0

    def calculate_perimeter_length(self) -> float:
        """Calculate total perimeter length."""
        if len(self.points) < 2:
            return 0.0

        total = 0.0
        for i in range(len(self.points)):
            p1 = self.points[i]
            p2 = self.points[(i + 1) % len(self.points)]
            dx = p2.x - p1.x
            dy = p2.y - p1.y
            total += math.sqrt(dx**2 + dy**2)

        self.perimeter_length = total
        return total

    @property
    def diameter(self) -> float:
        """Estimate layer diameter from points."""
        if not self.points:
            return 0.0

        # Find bounding box
        min_x = min(p.x for p in self.points)
        max_x = max(p.x for p in self.points)
        min_y = min(p.y for p in self.points)
        max_y = max(p.y for p in self.points)

        return math.sqrt((max_x - min_x)**2 + (max_y - min_y)**2)

    @property
    def center(self) -> Vector3:
        """Calculate layer center."""
        if not self.points:
            return Vector3(0, 0, 0)

        avg_x = sum(p.x for p in self.points) / len(self.points)
        avg_y = sum(p.y for p in self.points) / len(self.points)

        return Vector3(avg_x, avg_y, self.z)


class CurvatureAnalyzer:
    """Analyzes curvature and geometry features."""

    @staticmethod
    def calculate_curvature(
        prev_point: Vector3,
        curr_point: Vector3,
        next_point: Vector3
    ) -> float:
        """
        Calculate curvature at current point using angle change.
        Returns angle change in degrees between consecutive segments.
        """
        # Vector from prev to curr
        v1 = curr_point - prev_point
        # Vector from curr to next
        v2 = next_point - curr_point

        mag1 = v1.magnitude()
        mag2 = v2.magnitude()

        if mag1 == 0 or mag2 == 0:
            return 0.0

        # Normalize
        v1_norm = v1.normalize()
        v2_norm = v2.normalize()

        # Angle between vectors using dot product
        cos_angle = v1_norm.dot(v2_norm)
        cos_angle = max(-1, min(1, cos_angle))  # Clamp to [-1, 1]

        angle_rad = math.acos(cos_angle)
        angle_deg = math.degrees(angle_rad)

        return angle_deg

    @staticmethod
    def analyze_perimeter_curvature(
        points: List[Vector3],
        window_size: int = 5
    ) -> List[float]:
        """
        Analyze curvature along perimeter using sliding window.
        Returns curvature at each point (smoothed).
        """
        if len(points) < 3:
            return [0.0] * len(points)

        curvatures = [0.0] * len(points)

        for i, curr_point in enumerate(points):
            prev_point = points[(i - 1) % len(points)]
            next_point = points[(i + 1) % len(points)]

            curvatures[i] = CurvatureAnalyzer.calculate_curvature(
                prev_point, curr_point, next_point
            )

        # Smooth with window
        if window_size > 1:
            smoothed = []
            for i in range(len(curvatures)):
                start = i - window_size // 2
                end = i + window_size // 2 + 1
                window = [curvatures[j % len(curvatures)] for j in range(start, end)]
                smoothed.append(sum(window) / len(window))
            return smoothed

        return curvatures

    @staticmethod
    def identify_high_curvature_regions(
        curvatures: List[float],
        threshold: float = 30.0
    ) -> List[Tuple[int, int]]:
        """
        Identify regions where curvature exceeds threshold.
        Returns list of (start_idx, end_idx) tuples.
        """
        regions = []
        in_region = False
        start_idx = 0

        for i, curvature in enumerate(curvatures):
            if curvature > threshold:
                if not in_region:
                    start_idx = i
                    in_region = True
            else:
                if in_region:
                    regions.append((start_idx, i - 1))
                    in_region = False

        if in_region:
            regions.append((start_idx, len(curvatures) - 1))

        return regions


class GeometryAnalyzer:
    """Analyzes model geometry to extract layers and perimeters."""

    def __init__(self, layer_height: float = 0.5):
        self.layer_height = layer_height
        self.layers: List[Layer] = []
        # NumPy arrays for fast slicing — built once per model
        self._v0 = None
        self._v1 = None
        self._v2 = None

    def _build_numpy_arrays(self, model: STLModel) -> None:
        """Pre-build vertex arrays for vectorized layer slicing."""
        if not _HAS_NUMPY:
            return
        n = len(model.triangles)
        v0 = np.empty((n, 3), dtype=np.float64)
        v1 = np.empty((n, 3), dtype=np.float64)
        v2 = np.empty((n, 3), dtype=np.float64)
        for i, tri in enumerate(model.triangles):
            v0[i, 0] = tri.vertex1.x; v0[i, 1] = tri.vertex1.y; v0[i, 2] = tri.vertex1.z
            v1[i, 0] = tri.vertex2.x; v1[i, 1] = tri.vertex2.y; v1[i, 2] = tri.vertex2.z
            v2[i, 0] = tri.vertex3.x; v2[i, 1] = tri.vertex3.y; v2[i, 2] = tri.vertex3.z
        self._v0, self._v1, self._v2 = v0, v1, v2

    def analyze_model(self, model: STLModel) -> None:
        """
        Analyze STL model and extract layers.
        Uses NumPy vectorization when available for ~50x speedup.
        """
        min_pt, max_pt = model.bounds
        min_z = min_pt.z
        max_z = max_pt.z

        num_layers = int((max_z - min_z) / self.layer_height) + 1
        logger.info(f"Analyzing model into {num_layers} layers at {self.layer_height}mm height")

        z_levels = [min_z + i * self.layer_height for i in range(num_layers)]

        if _HAS_RUST:
            # Fast path: build flat arrays once, batch all layers in one Rust/Rayon call
            logger.info("Using Rust parallel slicer (fastest)")
            self._build_numpy_arrays(model)
            n = len(model.triangles)
            v0x = self._v0[:, 0].tolist(); v0y = self._v0[:, 1].tolist(); v0z = self._v0[:, 2].tolist()
            v1x = self._v1[:, 0].tolist(); v1y = self._v1[:, 1].tolist(); v1z = self._v1[:, 2].tolist()
            v2x = self._v2[:, 0].tolist(); v2y = self._v2[:, 1].tolist(); v2z = self._v2[:, 2].tolist()
            results = _rust.slice_all_layers(
                v0x, v0y, v0z, v1x, v1y, v1z, v2x, v2y, v2z, z_levels
            )
            for z, (xs, ys) in zip(z_levels, results):
                if xs:
                    pts = [Vector3(float(x), float(y), z) for x, y in zip(xs, ys)]
                    layer = Layer(z=z, points=pts)
                    layer.calculate_perimeter_length()
                    self.layers.append(layer)
        elif _HAS_NUMPY:
            self._build_numpy_arrays(model)
            for z in z_levels:
                points = self._slice_numpy(z)
                if points:
                    layer = Layer(z=z, points=points)
                    layer.calculate_perimeter_length()
                    self.layers.append(layer)
        else:
            for z in z_levels:
                points = self._slice_model_at_z(model, z)
                if points:
                    layer = Layer(z=z, points=points)
                    layer.calculate_perimeter_length()
                    self.layers.append(layer)

        logger.info(f"Extracted {len(self.layers)} layers with geometry")

    def _slice_numpy(self, z: float) -> List[Vector3]:
        """
        NumPy-vectorized layer slicing — ~50x faster than the Python version.
        Finds all triangle-plane intersections at height z.
        """
        v0, v1, v2 = self._v0, self._v1, self._v2
        z0, z1, z2 = v0[:, 2], v1[:, 2], v2[:, 2]

        all_ix: List[np.ndarray] = []
        all_iy: List[np.ndarray] = []

        # Process edges: (v0→v1), (v1→v2), (v2→v0)
        for va, vb, za, zb in ((v0, v1, z0, z1), (v1, v2, z1, z2), (v2, v0, z2, z0)):
            # Edge spans z if one endpoint <= z and the other >= z
            spans = ((za <= z) & (zb >= z)) | ((za >= z) & (zb <= z))
            dz = zb[spans] - za[spans]
            nonzero = np.abs(dz) > 1e-10
            if not np.any(nonzero):
                continue

            va_s = va[spans][nonzero]
            vb_s = vb[spans][nonzero]
            dz_s = dz[nonzero]
            t = (z - za[spans][nonzero]) / dz_s

            valid = (t >= -1e-6) & (t <= 1 + 1e-6)
            if not np.any(valid):
                continue

            t = t[valid]
            va_s = va_s[valid]
            vb_s = vb_s[valid]
            all_ix.append(va_s[:, 0] + t * (vb_s[:, 0] - va_s[:, 0]))
            all_iy.append(va_s[:, 1] + t * (vb_s[:, 1] - va_s[:, 1]))

        if not all_ix:
            return []

        ix = np.concatenate(all_ix)
        iy = np.concatenate(all_iy)

        if len(ix) < 2:
            return []

        # Remove near-duplicate points (within 0.01mm)
        pts = np.column_stack([ix, iy])
        # Round to 0.01mm and deduplicate
        pts_r = np.round(pts, 2)
        _, unique_idx = np.unique(pts_r, axis=0, return_index=True)
        pts = pts[unique_idx]

        if len(pts) < 2:
            return []

        # Sort by angle from centroid to form a proper perimeter
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
        order = np.argsort(angles)
        pts = pts[order]

        return [Vector3(float(p[0]), float(p[1]), z) for p in pts]

    def _slice_model_at_z(self, model: STLModel, z: float) -> List[Vector3]:
        """
        Find intersection points between triangles and horizontal plane at Z.
        Returns sorted list of points forming the perimeter.
        """
        intersection_points = []

        for triangle in model.triangles:
            points = self._intersect_triangle_with_plane(triangle, z)
            intersection_points.extend(points)

        if not intersection_points:
            return []

        # Sort points in circular order around center
        if len(intersection_points) > 1:
            center_x = sum(p.x for p in intersection_points) / len(intersection_points)
            center_y = sum(p.y for p in intersection_points) / len(intersection_points)

            def angle_from_center(point):
                return math.atan2(point.y - center_y, point.x - center_x)

            intersection_points.sort(key=angle_from_center)

        return intersection_points

    @staticmethod
    def _intersect_triangle_with_plane(triangle, z: float) -> List[Vector3]:
        """
        Find where triangle intersects horizontal plane at Z.
        Returns list of 0-2 intersection points.
        """
        vertices = triangle.vertices
        points_above = []
        points_below = []

        for v in vertices:
            if v.z > z:
                points_above.append(v)
            elif v.z < z:
                points_below.append(v)
            else:
                # Point is on plane
                return []  # Degenerate case

        # Intersection occurs if some points are above and some below
        if len(points_above) == 0 or len(points_below) == 0:
            return []

        intersections = []

        # Find edge intersections
        for v1 in vertices:
            for v2 in vertices:
                if v1 == v2:
                    continue

                if (v1.z <= z <= v2.z) or (v2.z <= z <= v1.z):
                    if v1.z != v2.z:
                        t = (z - v1.z) / (v2.z - v1.z)
                        if 0 <= t <= 1:
                            intersection = Vector3(
                                v1.x + t * (v2.x - v1.x),
                                v1.y + t * (v2.y - v1.y),
                                z
                            )
                            # Avoid duplicates
                            is_duplicate = any(
                                abs(intersection.x - p.x) < 0.0001 and
                                abs(intersection.y - p.y) < 0.0001
                                for p in intersections
                            )
                            if not is_duplicate:
                                intersections.append(intersection)

        return intersections[:2]  # Return at most 2 points per triangle per plane

    def get_layer_with_most_points(self) -> Optional[Layer]:
        """Get the layer with the most perimeter points (typically widest)."""
        if not self.layers:
            return None
        return max(self.layers, key=lambda l: len(l.points))

    def get_layer_statistics(self) -> dict:
        """Get statistics about extracted layers."""
        if not self.layers:
            return {}

        return {
            "total_layers": len(self.layers),
            "min_z": min(l.z for l in self.layers),
            "max_z": max(l.z for l in self.layers),
            "avg_diameter": sum(l.diameter for l in self.layers) / len(self.layers),
            "min_diameter": min(l.diameter for l in self.layers),
            "max_diameter": max(l.diameter for l in self.layers),
            "avg_perimeter": sum(l.perimeter_length for l in self.layers) / len(self.layers),
        }

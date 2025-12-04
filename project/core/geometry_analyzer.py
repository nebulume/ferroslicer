'''
Geometry analysis for perimeter extraction and curvature calculation.
'''

import math
from typing import List, Tuple, Optional
from dataclasses import dataclass
from .stl_parser import Vector3, STLModel
from .logger import setup_logger

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

    def analyze_model(self, model: STLModel) -> None:
        """
        Analyze STL model and extract layers.
        Slices model horizontally into layers.
        """
        min_pt, max_pt = model.bounds
        min_z = min_pt.z
        max_z = max_pt.z

        num_layers = int((max_z - min_z) / self.layer_height) + 1

        logger.info(f"Analyzing model into {num_layers} layers at {self.layer_height}mm height")

        for layer_idx in range(num_layers):
            z = min_z + layer_idx * self.layer_height
            points = self._slice_model_at_z(model, z)

            if points:
                layer = Layer(z=z, points=points)
                layer.calculate_perimeter_length()
                self.layers.append(layer)

        logger.info(f"Extracted {len(self.layers)} layers with geometry")

    def _slice_model_at_z(self, model: STLModel, z: float) -> List[Vector3]:
        """
        Find intersection points between triangles and horizontal plane at Z.
        Returns sorted list of points forming the perimeter.
        Optimized: batch process intersections before sorting.
        """
        intersection_points = []
        
        # Batch collect all intersection points (faster than checking each triangle individually)
        for triangle in model.triangles:
            points = self._intersect_triangle_with_plane(triangle, z)
            if points:  # Only extend if there are points
                intersection_points.extend(points)

        if not intersection_points:
            return []

        # Sort points in circular order around center (do this once, not per-triangle)
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
        seen_points = set()  # Use set for O(1) duplicate detection instead of O(n) list search

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
                            # Avoid duplicates using set lookup (O(1) instead of O(n))
                            point_key = (round(intersection.x, 4), round(intersection.y, 4))
                            if point_key not in seen_points:
                                seen_points.add(point_key)
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

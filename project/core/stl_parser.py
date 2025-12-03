'''
STL file parser with validation and manifold checking.
'''

import struct
from typing import List, Tuple, Optional
from dataclasses import dataclass
from .exceptions import ProjectError
from .logger import setup_logger

logger = setup_logger("stl_parser")


@dataclass
class Vector3:
    """3D vector representation."""
    x: float
    y: float
    z: float

    def __add__(self, other: 'Vector3') -> 'Vector3':
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: 'Vector3') -> 'Vector3':
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> 'Vector3':
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)

    def magnitude(self) -> float:
        """Calculate vector magnitude."""
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5

    def normalize(self) -> 'Vector3':
        """Return normalized vector."""
        mag = self.magnitude()
        if mag == 0:
            return Vector3(0, 0, 0)
        return Vector3(self.x / mag, self.y / mag, self.z / mag)

    def dot(self, other: 'Vector3') -> float:
        """Calculate dot product."""
        return self.x * other.x + self.y * other.y + self.z * other.z


@dataclass
class Triangle:
    """Triangle representation with vertices and normal."""
    normal: Vector3
    vertex1: Vector3
    vertex2: Vector3
    vertex3: Vector3

    @property
    def vertices(self) -> List[Vector3]:
        return [self.vertex1, self.vertex2, self.vertex3]

    def get_bounds(self) -> Tuple[Vector3, Vector3]:
        """Get min and max bounds of triangle."""
        vertices = self.vertices
        min_x = min(v.x for v in vertices)
        max_x = max(v.x for v in vertices)
        min_y = min(v.y for v in vertices)
        max_y = max(v.y for v in vertices)
        min_z = min(v.z for v in vertices)
        max_z = max(v.z for v in vertices)
        return Vector3(min_x, min_y, min_z), Vector3(max_x, max_y, max_z)


class STLModel:
    """Represents a parsed STL model."""

    def __init__(self, name: str, triangles: List[Triangle]):
        self.name = name
        self.triangles = triangles
        self._bounds = None
        self._validate()

    def _validate(self) -> None:
        """Validate model data."""
        if not self.triangles:
            raise ProjectError("STL model contains no triangles")
        logger.info(f"Loaded STL: {self.name} with {len(self.triangles)} triangles")

    @property
    def bounds(self) -> Tuple[Vector3, Vector3]:
        """Get model bounding box."""
        if self._bounds is None:
            all_vertices = []
            for tri in self.triangles:
                all_vertices.extend(tri.vertices)

            min_x = min(v.x for v in all_vertices)
            max_x = max(v.x for v in all_vertices)
            min_y = min(v.y for v in all_vertices)
            max_y = max(v.y for v in all_vertices)
            min_z = min(v.z for v in all_vertices)
            max_z = max(v.z for v in all_vertices)

            self._bounds = (
                Vector3(min_x, min_y, min_z),
                Vector3(max_x, max_y, max_z)
            )
        return self._bounds

    @property
    def dimensions(self) -> Vector3:
        """Get model dimensions (width, depth, height)."""
        min_pt, max_pt = self.bounds
        return Vector3(
            max_pt.x - min_pt.x,
            max_pt.y - min_pt.y,
            max_pt.z - min_pt.z
        )

    @property
    def center(self) -> Vector3:
        """Get model center point."""
        min_pt, max_pt = self.bounds
        return Vector3(
            (min_pt.x + max_pt.x) / 2,
            (min_pt.y + max_pt.y) / 2,
            (min_pt.z + max_pt.z) / 2
        )

    def check_manifold(self) -> Tuple[bool, Optional[str]]:
        """
        Check if model is manifold (watertight).
        Returns (is_manifold, error_message).
        """
        # Count edge occurrences - each edge should appear exactly twice in watertight mesh
        edge_count = {}

        for triangle in self.triangles:
            vertices = triangle.vertices
            for i in range(3):
                v1 = vertices[i]
                v2 = vertices[(i + 1) % 3]

                # Create edge key (sorted to handle both directions)
                key = tuple(sorted([
                    (round(v1.x, 6), round(v1.y, 6), round(v1.z, 6)),
                    (round(v2.x, 6), round(v2.y, 6), round(v2.z, 6))
                ]))

                edge_count[key] = edge_count.get(key, 0) + 1

        # Check if any edge appears exactly twice
        is_manifold = all(count == 2 for count in edge_count.values())

        if not is_manifold:
            bad_edges = sum(1 for count in edge_count.values() if count != 2)
            msg = f"Non-manifold geometry: {bad_edges} edges don't appear exactly twice"
            return False, msg

        return True, None

    def check_vase_suitability(self) -> Tuple[bool, List[str]]:
        """
        Check if model is suitable for vase mode.
        Returns (is_suitable, list_of_warnings).
        """
        warnings = []
        dims = self.dimensions

        # Check for internal geometry (very simplified check)
        # In a vase, height should be significantly larger than width/depth
        if dims.z < max(dims.x, dims.y):
            warnings.append("Model width/depth exceeds height - may not be suitable for vase mode")

        # Check for extreme aspect ratios
        if dims.z / max(dims.x, dims.y) > 5:
            warnings.append("Model is very tall and thin - may be unstable in vase mode")

        return len(warnings) == 0, warnings

    def squash_z_axis(self, target_height: float) -> None:
        """Scale Z-axis only to fit target height."""
        min_pt, max_pt = self.bounds
        current_height = max_pt.z - min_pt.z

        if current_height <= 0:
            raise ProjectError("Cannot squash model with zero height")

        scale_factor = target_height / current_height

        for triangle in self.triangles:
            min_z = min_pt.z
            for vertex in triangle.vertices:
                vertex.z = min_z + (vertex.z - min_z) * scale_factor

        self._bounds = None  # Invalidate bounds cache
        logger.info(f"Squashed Z-axis by factor {scale_factor:.3f}")


class STLParser:
    """Parser for ASCII STL files."""

    @staticmethod
    def parse(file_path: str) -> STLModel:
        """
        Parse ASCII STL file.

        Args:
            file_path: Path to STL file

        Returns:
            STLModel instance

        Raises:
            ProjectError: If file cannot be read or is invalid
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except IOError as e:
            raise ProjectError(f"Cannot read STL file: {e}")

        return STLParser._parse_ascii(file_path, content)

    @staticmethod
    def _parse_ascii(file_path: str, content: str) -> STLModel:
        """Parse ASCII STL format."""
        lines = content.strip().split('\n')

        if not lines or 'solid' not in lines[0].lower():
            raise ProjectError("Invalid ASCII STL format - missing 'solid' header")

        # Extract model name from solid line
        model_name = lines[0].lower().replace('solid', '').strip() or "model"

        triangles = []
        current_normal = None
        current_vertices = []

        for line in lines[1:]:
            line = line.strip().lower()

            if line.startswith('facet normal'):
                # Parse normal vector
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        current_normal = Vector3(
                            float(parts[2]),
                            float(parts[3]),
                            float(parts[4])
                        )
                    except ValueError:
                        continue

            elif line.startswith('vertex'):
                # Parse vertex
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        vertex = Vector3(
                            float(parts[1]),
                            float(parts[2]),
                            float(parts[3])
                        )
                        current_vertices.append(vertex)
                    except ValueError:
                        continue

            elif line.startswith('endfacet'):
                # Complete triangle
                if current_normal and len(current_vertices) == 3:
                    triangle = Triangle(
                        normal=current_normal,
                        vertex1=current_vertices[0],
                        vertex2=current_vertices[1],
                        vertex3=current_vertices[2]
                    )
                    triangles.append(triangle)

                current_normal = None
                current_vertices = []

        if not triangles:
            raise ProjectError("No valid triangles found in STL file")

        return STLModel(model_name, triangles)

'''
Geometric utilities for the project.
'''

import math
from typing import Tuple, List


class Point:
    """
    Represents a point in 2D space.
    """

    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

    def distance_to(self, other: 'Point') -> float:
        """
        Calculate the Euclidean distance to another point.
        """
        return math.hypot(self.x - other.x, self.y - other.y)

    def __repr__(self):
        return f"Point(x={self.x}, y={self.y})"


class Line:
    """
    Represents a line segment defined by two points.
    """

    def __init__(self, start: Point, end: Point):
        self.start = start
        self.end = end

    def length(self) -> float:
        """
        Calculate the length of the line segment.
        """
        return self.start.distance_to(self.end)

    def midpoint(self) -> Point:
        """
        Calculate the midpoint of the line segment.
        """
        return Point(
            (self.start.x + self.end.x) / 2,
            (self.start.y + self.end.y) / 2
        )

    def __repr__(self):
        return f"Line(start={self.start}, end={self.end})"


def distance_between_points(p1: Point, p2: Point) -> float:
    """
    Calculate the Euclidean distance between two points.
    """
    return p1.distance_to(p2)


def get_bounding_box(points: List[Point]) -> Tuple[Point, Point]:
    """
    Calculate the bounding box (min and max points) for a list of points.
    """
    if not points:
        raise ValueError("Points list cannot be empty")

    min_x = min(p.x for p in points)
    max_x = max(p.x for p in points)
    min_y = min(p.y for p in points)
    max_y = max(p.y for p in points)

    return (Point(min_x, min_y), Point(max_x, max_y))

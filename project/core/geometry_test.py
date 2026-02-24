'''
Tests for geometric utilities.
'''

import unittest
from .geometry import Point, Line, distance_between_points, get_bounding_box


class TestPoint(unittest.TestCase):

    def test_distance_to(self):
        p1 = Point(0, 0)
        p2 = Point(3, 4)
        self.assertEqual(p1.distance_to(p2), 5.0)


class TestLine(unittest.TestCase):

    def test_length(self):
        start = Point(0, 0)
        end = Point(3, 4)
        line = Line(start, end)
        self.assertEqual(line.length(), 5.0)

    def test_midpoint(self):
        start = Point(0, 0)
        end = Point(4, 6)
        line = Line(start, end)
        midpoint = line.midpoint()
        self.assertEqual(midpoint.x, 2.0)
        self.assertEqual(midpoint.y, 3.0)


class TestGeometryFunctions(unittest.TestCase):

    def test_distance_between_points(self):
        p1 = Point(1, 1)
        p2 = Point(4, 5)
        self.assertEqual(distance_between_points(p1, p2), 5.0)

    def test_get_bounding_box(self):
        points = [Point(1, 2), Point(3, 4), Point(0, 1)]
        min_point, max_point = get_bounding_box(points)
        self.assertEqual(min_point.x, 0)
        self.assertEqual(min_point.y, 1)
        self.assertEqual(max_point.x, 3)
        self.assertEqual(max_point.y, 4)


if __name__ == '__main__':
    unittest.main()

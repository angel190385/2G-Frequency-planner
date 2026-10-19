"""
Unit tests for polygon zones, containment exclusion rules, and cluster optimizer.
"""
import unittest

from src.models.cell import Cell, Station
from src.models.zone import (
    OptimizationZone, point_in_polygon, polygon_area, assign_cells_to_zones
)
from src.engine.cluster_optimizer import find_frontier_cells, ClusterOptimizer


class TestZoneClusters(unittest.TestCase):
    def setUp(self):
        self.st_center = Station("S_CENTRO", "Centro", 40.4168, -3.7038)
        self.st_inner = Station("S_INTERIOR", "Interior", 40.4170, -3.7040)
        self.st_outer = Station("S_EXTERIOR", "Exterior", 40.4250, -3.7150)
        self.st_far = Station("S_LEJOS", "Lejos", 40.6000, -3.9000)

        self.c1 = Cell("C_CENTRO", self.st_center, "1", 0.0, 100, [102])
        self.c2 = Cell("C_INTERIOR", self.st_inner, "2", 120.0, 104, [106])
        self.c3 = Cell("C_EXTERIOR", self.st_outer, "1", 0.0, 108, [110])
        self.c4 = Cell("C_LEJOS", self.st_far, "1", 0.0, 112, [114])

        self.cells = {
            "C_CENTRO": self.c1,
            "C_INTERIOR": self.c2,
            "C_EXTERIOR": self.c3,
            "C_LEJOS": self.c4
        }

        # Small inner polygon covering C_CENTRO and C_INTERIOR
        self.poly_inner = [
            (40.4150, -3.7060),
            (40.4190, -3.7060),
            (40.4190, -3.7020),
            (40.4150, -3.7020)
        ]

        # Large outer polygon covering all three (C_CENTRO, C_INTERIOR, C_EXTERIOR)
        self.poly_outer = [
            (40.4100, -3.7200),
            (40.4300, -3.7200),
            (40.4300, -3.7000),
            (40.4100, -3.7000)
        ]

    def test_containment_and_exclusion(self):
        """
        Verify that if Polygon Outer contains Polygon Inner,
        Polygon Outer ONLY receives the cells that Polygon Inner does NOT contain.
        """
        z_outer = OptimizationZone("z_outer", "Zona Exterior", self.poly_outer)
        z_inner = OptimizationZone("z_inner", "Zona Interior", self.poly_inner)

        assign_cells_to_zones(self.cells, [z_outer, z_inner])

        # Inner zone should have claimed C_CENTRO and C_INTERIOR
        self.assertEqual(z_inner.cell_ids, {"C_CENTRO", "C_INTERIOR"})

        # Outer zone must ONLY have C_EXTERIOR, excluding the ones claimed by inner
        self.assertEqual(z_outer.cell_ids, {"C_EXTERIOR"})

        # C_LEJOS is not in any polygon
        self.assertNotIn("C_LEJOS", z_inner.cell_ids)
        self.assertNotIn("C_LEJOS", z_outer.cell_ids)

    def test_frontier_detection(self):
        """
        Verify that border cells outside active zone are identified as protective frontier.
        """
        active_ids = {"C_CENTRO", "C_INTERIOR"}
        frontier = find_frontier_cells(active_ids, self.cells, threshold_km=5.0)

        # C_EXTERIOR is ~1.3 km away -> MUST be in frontier
        self.assertIn("C_EXTERIOR", frontier)

        # C_LEJOS is ~25 km away -> MUST NOT be in frontier
        self.assertNotIn("C_LEJOS", frontier)


if __name__ == "__main__":
    unittest.main()

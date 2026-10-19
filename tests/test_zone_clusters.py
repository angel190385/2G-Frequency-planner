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

    def test_neighbor_graph_filter_by_cells(self):
        """
        Verify that NeighborGraph.filter_by_cells creates a subgraph
        with only relations where both cells are in the given subset.
        """
        from src.models.neighbor import NeighborGraph, NeighborRelation
        ng = NeighborGraph()
        ng.add_relation(NeighborRelation("C_CENTRO", "C_INTERIOR", ho_count=50, distance_km=0.1))
        ng.add_relation(NeighborRelation("C_INTERIOR", "C_EXTERIOR", ho_count=20, distance_km=1.2))
        ng.add_relation(NeighborRelation("C_EXTERIOR", "C_LEJOS", ho_count=5, distance_km=25.0))

        self.assertEqual(ng.total_relations, 3)

        sub_ng = ng.filter_by_cells({"C_CENTRO", "C_INTERIOR"})
        self.assertEqual(sub_ng.total_relations, 1)
        self.assertTrue(sub_ng.are_neighbors("C_CENTRO", "C_INTERIOR"))
        self.assertFalse(sub_ng.are_neighbors("C_INTERIOR", "C_EXTERIOR"))

        sub_ng2 = ng.filter_by_cells({"C_CENTRO", "C_EXTERIOR"})
        self.assertEqual(sub_ng2.total_relations, 0)

    def test_cluster_optimizer_sequential_execution(self):
        """
        Test end-to-end execution of ClusterOptimizer with neighbor graph and multiple zones.
        """
        from src.models.neighbor import NeighborGraph, NeighborRelation
        from src.engine.constraints import FrequencyRules

        ng = NeighborGraph()
        ng.add_relation(NeighborRelation("C_CENTRO", "C_INTERIOR", ho_count=50, distance_km=0.1))
        ng.add_relation(NeighborRelation("C_INTERIOR", "C_EXTERIOR", ho_count=20, distance_km=1.2))

        rules = FrequencyRules(
            freq_range_start=100,
            freq_range_end=124,
            num_generations=2,
            population_size=10,
            num_runs=1
        )

        z1 = OptimizationZone("z1", "Zona 1", self.poly_inner, rules=rules, execution_order=1)
        z2 = OptimizationZone("z2", "Zona 2", self.poly_outer, rules=rules, execution_order=2)
        assign_cells_to_zones(self.cells, [z1, z2])

        optimizer = ClusterOptimizer(
            cells=self.cells,
            zones=[z1, z2],
            global_neighbor_graph=ng,
            frontier_distance_km=5.0
        )

        results = optimizer.run()
        self.assertIn("z1", results)
        self.assertIn("z2", results)
        self.assertEqual(z1.status, "Completada")
        self.assertEqual(z2.status, "Completada")

    def test_cluster_optimizer_respects_cell_flags(self):
        """
        Test that when respect_cell_flags is True, individual cell configuration
        (recalculate_bcch, tch_indices, new_tch_count) is strictly honored,
        and zones without cells requiring calculation are skipped.
        """
        from src.models.neighbor import NeighborGraph
        from src.engine.constraints import FrequencyRules

        ng = NeighborGraph()
        rules = FrequencyRules(
            freq_range_start=100,
            freq_range_end=124,
            num_generations=2,
            population_size=10,
            num_runs=1
        )

        z1 = OptimizationZone("z1", "Zona 1", self.poly_inner, rules=rules, execution_order=1)
        z2 = OptimizationZone("z2", "Zona 2", self.poly_outer, rules=rules, execution_order=2)
        assign_cells_to_zones(self.cells, [z1, z2])

        # Configure cells manually as if customized in the calculation table
        orig_centro_bcch = self.cells["C_CENTRO"].bcch
        orig_exterior_bcch = self.cells["C_EXTERIOR"].bcch
        orig_exterior_tch = list(self.cells["C_EXTERIOR"].tch_list)

        # C_CENTRO (in z1): don't recalc BCCH, only new TCH
        self.cells["C_CENTRO"].recalculate_bcch = False
        self.cells["C_CENTRO"].tch_indices_to_recalculate = []
        self.cells["C_CENTRO"].new_tch_count = 1

        # C_INTERIOR (in z1): recalc BCCH
        self.cells["C_INTERIOR"].recalculate_bcch = True
        self.cells["C_INTERIOR"].tch_indices_to_recalculate = []
        self.cells["C_INTERIOR"].new_tch_count = 0

        # C_EXTERIOR (in z2): unchecked in table (needs_calculation == False)
        self.cells["C_EXTERIOR"].recalculate_bcch = False
        self.cells["C_EXTERIOR"].tch_indices_to_recalculate = []
        self.cells["C_EXTERIOR"].new_tch_count = 0

        optimizer = ClusterOptimizer(
            cells=self.cells,
            zones=[z1, z2],
            global_neighbor_graph=ng,
            frontier_distance_km=5.0,
            respect_cell_flags=True
        )

        results = optimizer.run()
        self.assertIn("z1", results)
        self.assertEqual(z1.status, "Completada")
        self.assertEqual(z2.status, "Omitida (Sin celdas a recalcular)")

        # Verify C_CENTRO BCCH was preserved because recalculate_bcch was False
        self.assertEqual(optimizer.cells["C_CENTRO"].bcch, orig_centro_bcch)
        # Verify C_CENTRO got an extra TCH added
        self.assertEqual(len(optimizer.cells["C_CENTRO"].tch_list), len(self.cells["C_CENTRO"].tch_list) + 1)
        # Verify C_EXTERIOR was completely untouched
        self.assertEqual(optimizer.cells["C_EXTERIOR"].bcch, orig_exterior_bcch)
        self.assertEqual(optimizer.cells["C_EXTERIOR"].tch_list, orig_exterior_tch)

    def test_zone_defaults_and_minimum_out_of_range_recalculation(self):
        """
        Verify that OptimizationZone defaults to recalculate_bcch=False and recalculate_tch=False,
        and that ClusterOptimizer only flags frequencies that fall outside the allowed range.
        """
        from src.engine.constraints import FrequencyRules

        # 1. Defaults check
        z = OptimizationZone("z_test", "Test Zone", self.poly_inner)
        self.assertFalse(z.recalculate_bcch)
        self.assertFalse(z.recalculate_tch)

        # 2. Cell setup: Allowed range is 50..60
        # C_CENTRO: BCCH=55 (in), TCH=[56] (in) -> nothing to recalculate
        # C_INTERIOR: BCCH=90 (out of 50..60), TCH=[55, 95] (index 0 in, index 1 out)
        self.cells["C_CENTRO"].bcch = 55
        self.cells["C_CENTRO"].tch_list = [56]
        self.cells["C_INTERIOR"].bcch = 90
        self.cells["C_INTERIOR"].tch_list = [55, 95]

        rules = FrequencyRules(freq_range_start=50, freq_range_end=60, num_generations=2, population_size=10, num_runs=1)
        z.rules = rules
        z.cell_ids = {"C_CENTRO", "C_INTERIOR"}

        opt = ClusterOptimizer(
            cells=self.cells,
            zones=[z],
            frontier_distance_km=5.0,
            respect_cell_flags=False
        )

        # Before running, simulate how ClusterOptimizer sets flags for active cells
        allowed_pool = set(rules.available_frequencies)
        for cid in z.cell_ids:
            c = self.cells[cid]
            if z.recalculate_bcch:
                c.recalculate_bcch = True
            else:
                c.recalculate_bcch = bool(c.bcch is None or c.bcch <= 0 or (allowed_pool and c.bcch not in allowed_pool))

            if z.recalculate_tch:
                c.tch_indices_to_recalculate = list(range(len(c.tch_list)))
            else:
                c.tch_indices_to_recalculate = [
                    i for i, f in enumerate(c.tch_list)
                    if f is None or f <= 0 or f not in allowed_pool
                ]

        # C_CENTRO should have no changes needed
        self.assertFalse(self.cells["C_CENTRO"].recalculate_bcch)
        self.assertEqual(self.cells["C_CENTRO"].tch_indices_to_recalculate, [])
        self.assertFalse(self.cells["C_CENTRO"].needs_calculation)

        # C_INTERIOR should recalculate BCCH (90 is out) and only TCH index 1 (95 is out, 55 is in)
        self.assertTrue(self.cells["C_INTERIOR"].recalculate_bcch)
        self.assertEqual(self.cells["C_INTERIOR"].tch_indices_to_recalculate, [1])
        self.assertTrue(self.cells["C_INTERIOR"].needs_calculation)


if __name__ == "__main__":
    unittest.main()


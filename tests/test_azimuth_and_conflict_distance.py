"""
Unit tests for:
1. Azimuth-aware weighting in frequency planning (facing cells vs same-orientation / divergent cells).
2. Distance column in the conflict review / violations tree (Resultados tab).
"""
import math
import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PyQt6.QtWidgets import QApplication

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph, NeighborRelation, calculate_bearing, angle_diff
from src.models.frequency_plan import FrequencyAssignment, PlanResult, Violation, format_number
from src.engine.constraints import FrequencyRules
from src.engine.cost_function import evaluate_plan, _azimuth_factor, precompute_close_cell_pairs
from src.engine.fast_evaluator import FastPlanEvaluator
from src.ui.widgets.result_comparison import SingleRunResultWidget


class TestAzimuthAndConflictDistance(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        # Site 1 (South) and Site 2 (North), spaced ~1.1 km apart along North-South axis
        self.st1 = Station("ST1", "Site South", latitude=40.0000, longitude=-3.0000)
        self.st2 = Station("ST2", "Site North", latitude=40.0100, longitude=-3.0000)

        # Facing cells ("enfrentadas"):
        # CELL_S_FACING points North (azimuth 0°) towards Site 2
        # CELL_N_FACING points South (azimuth 180°) towards Site 1
        self.c_s_facing = Cell("C_S_FACING", self.st1, sector="A", azimuth=0.0, bcch=100, tch_list=[102])
        self.c_n_facing = Cell("C_N_FACING", self.st2, sector="B", azimuth=180.0, bcch=100, tch_list=[104])

        # Same orientation pointing North ("misma orientación apuntan a zonas distintas"):
        # CELL_N_DIVERGENT points North (azimuth 0°) AWAY from Site 1 into the northern zone
        self.c_n_divergent = Cell("C_N_DIVERGENT", self.st2, sector="A", azimuth=0.0, bcch=100, tch_list=[104])

        # Same orientation pointing East ("ambas hacia el Este"):
        self.c_s_east = Cell("C_S_EAST", self.st1, sector="B", azimuth=90.0, bcch=100, tch_list=[102])
        self.c_n_east = Cell("C_N_EAST", self.st2, sector="C", azimuth=90.0, bcch=100, tch_list=[104])

        # Back to back ("espalda con espalda"):
        self.c_s_away = Cell("C_S_AWAY", self.st1, sector="C", azimuth=180.0, bcch=100, tch_list=[102])

        self.rules = FrequencyRules()

    def test_bearing_and_angle_diff_calculation(self):
        """Test bearing calculation and circular angle difference."""
        # Due North
        b_north = calculate_bearing(40.0, -3.0, 40.1, -3.0)
        self.assertAlmostEqual(b_north, 0.0, places=1)

        # Due South
        b_south = calculate_bearing(40.1, -3.0, 40.0, -3.0)
        self.assertAlmostEqual(b_south, 180.0, places=1)

        # Due East (equatorial baseline is exactly 90.0)
        b_east = calculate_bearing(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(b_east, 90.0, places=1)

        # Due West
        b_west = calculate_bearing(0.0, 0.0, 0.0, -1.0)
        self.assertAlmostEqual(b_west, 270.0, places=1)

        # angle_diff tests
        self.assertEqual(angle_diff(0, 0), 0)
        self.assertEqual(angle_diff(0, 180), 180)
        self.assertEqual(angle_diff(10, 350), 20)
        self.assertEqual(angle_diff(350, 10), 20)
        self.assertEqual(angle_diff(90, 270), 180)

    def test_azimuth_factor_facing_vs_divergent(self):
        """
        Facing cells (enfrentadas) must have significantly higher weight factor
        than cells with the same orientation pointing to different zones.
        """
        dist_km = 1.1

        # Facing: S points at N (0°), N points at S (180°)
        f_facing = _azimuth_factor(self.c_s_facing, self.c_n_facing, dist_km, self.rules)

        # Same orientation pointing North (S points at N, N points away North)
        f_divergent = _azimuth_factor(self.c_s_facing, self.c_n_divergent, dist_km, self.rules)

        # Both pointing East (parallel coverage into different zones)
        f_east = _azimuth_factor(self.c_s_east, self.c_n_east, dist_km, self.rules)

        # Back to back
        f_b2b = _azimuth_factor(self.c_s_away, self.c_n_divergent, dist_km, self.rules)

        # Facing cells must have maximum factor (near 1.8)
        self.assertAlmostEqual(f_facing, 1.8, delta=0.05)

        # Same orientation divergent and parallel must have much lower weights
        self.assertLess(f_divergent, f_facing)
        self.assertLess(f_east, f_facing)
        self.assertLess(f_b2b, f_divergent)

        # Facing cells should have at least double the penalty of divergent cells
        self.assertGreater(f_facing, f_divergent * 2.0)
        self.assertGreater(f_facing, f_b2b * 4.0)

    def test_azimuth_factor_same_station_neutral(self):
        """Cells on the same station or zero distance must return neutral 1.0."""
        c_same_st = Cell("C_SAME", self.st1, sector="B", azimuth=120.0)
        self.assertEqual(_azimuth_factor(self.c_s_facing, c_same_st, distance_km=0.0), 1.0)

    def test_azimuth_weight_toggle(self):
        """When enable_azimuth_weight is False, factor should be bypassed (1.0)."""
        rules_disabled = FrequencyRules(enable_azimuth_weight=False)

        cells = {"C1": self.c_s_facing, "C2": self.c_n_facing}
        graph = NeighborGraph()
        graph.add_relation(NeighborRelation("C1", "C2", ho_count=10, distance_km=1.1))

        assign = {
            "C1": FrequencyAssignment("C1", bcch=100, tch_list=[102]),
            "C2": FrequencyAssignment("C2", bcch=100, tch_list=[104]),
        }

        res_disabled = evaluate_plan(assign, cells, graph, rules_disabled, collect_violations=True)
        res_enabled = evaluate_plan(assign, cells, graph, self.rules, collect_violations=True)

        # With azimuth weight enabled on facing cells, cost should be ~1.8x higher
        self.assertGreater(res_enabled.score, res_disabled.score)
        self.assertAlmostEqual(res_enabled.score / res_disabled.score, 1.8, delta=0.1)

    def test_fast_evaluator_consistency_with_azimuth(self):
        """FastPlanEvaluator score must exactly match evaluate_plan with azimuth weights."""
        c_s = Cell("C_S", self.st1, "A", azimuth=0.0, bcch=100, tch_list=[102])
        c_n = Cell("C_N", self.st2, "B", azimuth=180.0, bcch=100, tch_list=[104])
        c_e = Cell("C_E", self.st1, "B", azimuth=90.0, bcch=101, tch_list=[105])

        cells = {"C_S": c_s, "C_N": c_n, "C_E": c_e}
        graph = NeighborGraph()
        graph.add_relation(NeighborRelation("C_S", "C_N", ho_count=25, distance_km=1.1))
        graph.add_relation(NeighborRelation("C_S", "C_E", ho_count=50, distance_km=0.0))

        assign = {
            "C_S": FrequencyAssignment("C_S", bcch=100, tch_list=[102]),
            "C_N": FrequencyAssignment("C_N", bcch=100, tch_list=[104]),
            "C_E": FrequencyAssignment("C_E", bcch=101, tch_list=[105]),
        }

        fe = FastPlanEvaluator(cells, graph, self.rules)
        eval_res = evaluate_plan(assign, cells, graph, self.rules, collect_violations=False)

        mat = fe.base_freq_matrix.copy()
        fast_score = fe.quick_score_matrix(mat)
        self.assertAlmostEqual(eval_res.score, fast_score, places=2)

    def test_conflict_distance_column_in_results_ui(self):
        """
        Verify that the Results widget conflict review tree contains the 'Distancia'
        column at the end and populates the distance correctly.
        """
        widget = SingleRunResultWidget()

        # Check column headers
        header_labels = [
            widget.viol_tree.headerItem().text(i)
            for i in range(widget.viol_tree.columnCount())
        ]
        self.assertIn("Distancia", header_labels)
        self.assertEqual(header_labels[-1], "Distancia", "Distancia column must be the last column")

        # Create sample PlanResult with violations at different distances
        plan = PlanResult(plan_id=1, run_id=1, score=150.0)
        v1 = Violation(
            cell_a_id="C_S_FACING", cell_b_id="C_N_FACING",
            freq_a=100, freq_b=100,
            violation_type="cochannel", channel_type_a="BCCH", channel_type_b="BCCH",
            ho_count=20, distance_km=2.45, weighted_cost=180.0
        )
        v2 = Violation(
            cell_a_id="C1", cell_b_id="C2",
            freq_a=105, freq_b=105,
            violation_type="cochannel", channel_type_a="TCH", channel_type_b="TCH",
            ho_count=None, distance_km=0.0, weighted_cost=200.0
        )
        plan.violations = [v1, v2]
        plan.assignments = [
            FrequencyAssignment("C_S_FACING", 100, [102]),
            FrequencyAssignment("C_N_FACING", 100, [104]),
        ]

        widget.load_results([plan], cells={"C_S_FACING": self.c_s_facing, "C_N_FACING": self.c_n_facing})

        # Check that top-level group has children and child has distance displayed
        self.assertGreater(widget.viol_tree.topLevelItemCount(), 0)
        group_item = widget.viol_tree.topLevelItem(0)
        self.assertGreater(group_item.childCount(), 0)

        # First child corresponds to v1
        child_item = group_item.child(0)
        dist_column_index = 8
        dist_text = child_item.text(dist_column_index)

        self.assertTrue(dist_text.endswith("km"), f"Expected distance with 'km', got '{dist_text}'")
        self.assertIn("2.45", dist_text)

    def test_cancellation_preserves_best_results(self):
        """
        When optimization is cancelled mid-calculation, it must NOT lose results.
        It must return the best plans found up to that instant.
        """
        from src.engine.optimizer import FrequencyOptimizer

        # Create cells that need calculation
        c1 = Cell("C1", self.st1, "A", azimuth=0.0, bcch=100, tch_list=[102], recalculate_bcch=True)
        c2 = Cell("C2", self.st2, "A", azimuth=180.0, bcch=104, tch_list=[106], recalculate_bcch=True)
        cells = {"C1": c1, "C2": c2}

        graph = NeighborGraph()
        graph.add_relation(NeighborRelation("C1", "C2", ho_count=20, distance_km=1.1))

        rules = FrequencyRules(
            freq_range_start=100,
            freq_range_end=120,
            population_size=40,
            num_generations=200,
            num_runs=2,
            top_k_results=3
        )

        optimizer = FrequencyOptimizer(cells, graph, rules, max_workers=2)

        # Trigger cancel after generation 4
        def cancel_on_progress(*args):
            gen = args[2] if len(args) == 5 else args[0]
            if gen >= 4:
                optimizer.cancel()

        optimizer.progress_callback = cancel_on_progress
        results = optimizer.optimize()

        # Must have returned valid plans up to that moment
        self.assertGreater(len(results), 0, "Cancelled optimization must preserve partial results")
        best = results[0]
        self.assertIsNotNone(best.score)
        self.assertLess(best.score, float("inf"))
        self.assertEqual(len(best.assignments), 2)
        for a in best.assignments:
            self.assertIn(a.bcch, range(100, 121))


if __name__ == "__main__":
    unittest.main()

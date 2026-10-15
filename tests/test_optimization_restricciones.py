"""
Tests for optimization constraints, number formatting, hardware profiling,
and multi-core parallel optimizer execution.
"""
import unittest
import numpy as np

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph, NeighborRelation
from src.models.frequency_plan import FrequencyAssignment, format_number
from src.engine.constraints import FrequencyRules
from src.engine.cost_function import evaluate_plan
from src.engine.fast_evaluator import FastPlanEvaluator, HARD_PENALTY
from src.engine.hardware import get_hardware_profile
from src.engine.parallel import _repair_chromosome, run_parallel_optimizer
from src.engine.optimizer import FrequencyOptimizer, _Variable


class TestOptimizationRestricciones(unittest.TestCase):
    def setUp(self):
        self.rules = FrequencyRules(
            freq_range_start=100,
            freq_range_end=120,
            population_size=20,
            num_generations=15,
            num_runs=2,
            top_k_results=3
        )
        self.st1 = Station("ST01", "Site 01", 40.4168, -3.7038)
        self.st2 = Station("ST02", "Site 02", 40.4200, -3.7000)

        self.c1 = Cell("C01", self.st1, "A", 0.0, 100, [105, 106], recalculate_bcch=True)
        self.c2 = Cell("C02", self.st1, "B", 120.0, 102, [107], recalculate_bcch=True)
        self.c3 = Cell("C03", self.st2, "A", 0.0, 104, [108], recalculate_bcch=False)

        self.cells = {"C01": self.c1, "C02": self.c2, "C03": self.c3}
        self.graph = NeighborGraph()
        self.graph.add_relation(NeighborRelation("C01", "C02", 100, 0.0))
        self.graph.add_relation(NeighborRelation("C01", "C03", 50, 1.5))

    def test_number_formatting_no_decimals(self):
        """Whole numbers must never show trailing .0."""
        self.assertEqual(format_number(100.0), "100")
        self.assertEqual(format_number(0.0), "0")
        self.assertEqual(format_number(42), "42")
        self.assertEqual(format_number(1500000.0), "1500000")
        self.assertEqual(format_number(10.5), "10.5")
        self.assertEqual(format_number(10.256, precision=2), "10.26")
        self.assertEqual(format_number(None), "")

    def test_hard_constraint_same_cell_cochannel(self):
        """Under no circumstances can frequencies repeat within the same cell."""
        bad_assign = {
            "C01": FrequencyAssignment("C01", bcch=100, tch_list=[100, 106]),
            "C02": FrequencyAssignment("C02", bcch=102, tch_list=[107]),
            "C03": FrequencyAssignment("C03", bcch=104, tch_list=[108]),
        }
        res = evaluate_plan(bad_assign, self.cells, self.graph, self.rules, collect_violations=True)
        self.assertGreaterEqual(res.score, HARD_PENALTY)
        same_cell_viols = [v for v in res.violations if v.cell_a_id == v.cell_b_id]
        self.assertGreaterEqual(len(same_cell_viols), 1)

    def test_hard_constraint_same_station_zero_distance(self):
        """Under no circumstances can frequencies repeat between cells of same station (dist=0)."""
        bad_assign = {
            "C01": FrequencyAssignment("C01", bcch=100, tch_list=[105, 106]),
            "C02": FrequencyAssignment("C02", bcch=100, tch_list=[107]),
            "C03": FrequencyAssignment("C03", bcch=104, tch_list=[108]),
        }
        res = evaluate_plan(bad_assign, self.cells, self.graph, self.rules, collect_violations=True)
        self.assertGreaterEqual(res.score, HARD_PENALTY)

    def test_repair_chromosome_removes_cochannel(self):
        """_repair_chromosome must eliminate same-cell and same-station duplicates."""
        vars_list = [
            _Variable("C01", "BCCH", -1),
            _Variable("C02", "BCCH", -1),
        ]
        pool = np.array([100, 101, 102, 103, 104, 105], dtype=np.int32)
        chrom = np.array([0, 0], dtype=np.int32)
        repaired = _repair_chromosome(chrom, vars_list, self.cells, pool)

        f1 = pool[repaired[0] % len(pool)]
        f2 = pool[repaired[1] % len(pool)]
        self.assertNotEqual(f1, f2, "Repaired chromosome must not have cochannel on same station")

    def test_hardware_profiling(self):
        """Hardware profiling must detect CPU cores and recommend workers."""
        hw = get_hardware_profile()
        self.assertGreaterEqual(hw.cpu_count, 1)
        self.assertGreaterEqual(hw.recommended_workers, 1)
        self.assertTrue(len(hw.summary_short) > 0)
        self.assertTrue("100% CPU" in hw.summary_detailed)

    def test_fast_evaluator_consistency(self):
        """FastPlanEvaluator must match evaluate_plan exactly."""
        fe = FastPlanEvaluator(self.cells, self.graph, self.rules)
        assign = {
            "C01": FrequencyAssignment("C01", bcch=100, tch_list=[105, 106]),
            "C02": FrequencyAssignment("C02", bcch=102, tch_list=[107]),
            "C03": FrequencyAssignment("C03", bcch=104, tch_list=[108]),
        }
        score_eval = evaluate_plan(assign, self.cells, self.graph, self.rules, collect_violations=False).score

        mat = fe.base_freq_matrix.copy()
        score_fast = fe.quick_score_matrix(mat)
        self.assertAlmostEqual(score_eval, score_fast, places=1)

    def test_optimizer_parallel_execution(self):
        """Optimizer runs multi-core without error and returns clean results."""
        optimizer = FrequencyOptimizer(
            self.cells, self.graph, self.rules, max_workers=2
        )
        results = optimizer.optimize()
        self.assertGreater(len(results), 0)
        self.assertIsNotNone(results.per_run)

        best_plan = results[0]
        for a in best_plan.assignments:
            all_f = [a.bcch] + a.tch_list
            self.assertEqual(len(all_f), len(set(all_f)), f"Cell {a.cell_id} has duplicate frequencies")


if __name__ == "__main__":
    unittest.main()

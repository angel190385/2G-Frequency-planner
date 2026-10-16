"""
Unit tests for the new features in branch feature/mejoras-mapa-calculo:
1. Map widget dynamic viewport filtering, cell selection and modification highlighting.
2. Distance-based cost decay, topological neighbor weighting, and missing HO resilience.
3. Spectrum reconfiguration: flag to include all additional TCHs of affected cells for recalculation.
4. HTML dashboard cost decomposition (inherited pre-existing vs newly generated costs).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph, NeighborRelation
from src.models.frequency_plan import PlanResult, FrequencyAssignment, Violation
from src.engine.constraints import FrequencyRules
from src.engine.cost_function import evaluate_plan, _distance_factor, _ho_weight
from src.engine.fast_evaluator import FastPlanEvaluator
from src.engine.spectrum_manager import apply_spectrum_shift_to_cells
from src.io.html_exporter import export_plan_html


class TestMejorasMapaCalculo(unittest.TestCase):

    def setUp(self):
        # Create test stations
        self.st1 = Station(station_id="ST1", name="Station 1", latitude=40.0000, longitude=-3.0000)
        self.st2 = Station(station_id="ST2", name="Station 2", latitude=40.0100, longitude=-3.0000)  # ~1.1 km
        self.st3 = Station(station_id="ST3", name="Station 3", latitude=40.1000, longitude=-3.0000)  # ~11.1 km

        # Create test cells
        self.c1 = Cell(cell_id="CELL1", station=self.st1, sector="1", azimuth=0, bcch=50, tch_list=[60, 62, 64])
        self.c2 = Cell(cell_id="CELL2", station=self.st2, sector="1", azimuth=180, bcch=50, tch_list=[70, 72])
        self.c3 = Cell(cell_id="CELL3", station=self.st3, sector="1", azimuth=180, bcch=51, tch_list=[60, 80])

        self.cells = {
            "CELL1": self.c1,
            "CELL2": self.c2,
            "CELL3": self.c3
        }

        # Setup neighbor graph
        self.neighbor_graph = NeighborGraph()
        # CELL1 and CELL2 are neighbors (with 0 HO stats)
        self.neighbor_graph.add_relation(NeighborRelation(cell_a_id="CELL1", cell_b_id="CELL2", ho_count=0))

        self.rules = FrequencyRules()

    def test_distance_factor_and_neighbor_weighting(self):
        """Test that physical distance decays penalties and neighbor status applies factor."""
        # Distance factor
        d0 = _distance_factor(0.0, decay=0.3)
        d1 = _distance_factor(1.0, decay=0.3)
        d5 = _distance_factor(5.0, decay=0.3)
        d10 = _distance_factor(10.0, decay=0.3)

        self.assertEqual(d0, 2.0)
        self.assertLess(d1, d0)
        self.assertLess(d5, d1)
        self.assertLess(d10, d5)
        self.assertGreater(d10, 0.0)

        # Baseline HO weight when HO is 0 or None must be neutral 1.0
        self.assertEqual(_ho_weight(0), 1.0)
        self.assertEqual(_ho_weight(None), 1.0)

    def test_cost_evaluation_parity_with_fast_evaluator(self):
        """Verify that evaluate_plan and FastPlanEvaluator match exactly with new formulas."""
        import numpy as np

        fast_eval = FastPlanEvaluator(self.cells, self.neighbor_graph, self.rules)

        # Create assignments with cochannel (50) and adjacent (50 vs 51)
        assignments = {
            "CELL1": FrequencyAssignment("CELL1", bcch=50, tch_list=[60, 62, 64]),
            "CELL2": FrequencyAssignment("CELL2", bcch=50, tch_list=[70, 72]),
            "CELL3": FrequencyAssignment("CELL3", bcch=51, tch_list=[60, 80]),
        }

        res = evaluate_plan(
            assignments,
            self.cells,
            self.neighbor_graph,
            self.rules
        )

        matrix = np.array([
            [50, 60, 62, 64],  # CELL1
            [50, 70, 72, 0],   # CELL2
            [51, 60, 80, 0]    # CELL3
        ], dtype=np.int32)
        fast_score = fast_eval.quick_score_matrix(matrix)

        self.assertAlmostEqual(res.score, fast_score, places=2)
        self.assertGreater(res.score, 0)

    def test_spectrum_shift_include_all_cell_tchs(self):
        """Test include_all_cell_tchs in apply_spectrum_shift_to_cells."""
        from src.engine.spectrum_manager import analyze_spectrum_shift

        # Initial range is 50-75. Target range is 50-70.
        # CELL1 has TCHs [60, 62, 75] where 75 disappears in target range.
        cell_test_1 = Cell(cell_id="TEST_C1", station=self.st1, sector="1", azimuth=0, bcch=50, tch_list=[60, 62, 75])
        test_cells_1 = {"TEST_C1": cell_test_1}
        analysis_1 = analyze_spectrum_shift(test_cells_1, initial_start=50, initial_end=75, target_start=50, target_end=70)

        # Case A: include_all_cell_tchs=False -> Only index 2 (freq 75) is marked
        apply_spectrum_shift_to_cells(test_cells_1, self.neighbor_graph, self.rules, analysis_1, mode="disappeared_only", include_all_cell_tchs=False)
        self.assertEqual(test_cells_1["TEST_C1"].tch_indices_to_recalculate, [2])

        # Case B: include_all_cell_tchs=True with TCH affected -> ALL TCH indices [0, 1, 2] should be marked
        cell_test_2 = Cell(cell_id="TEST_C2", station=self.st1, sector="1", azimuth=0, bcch=50, tch_list=[60, 62, 75])
        test_cells_2 = {"TEST_C2": cell_test_2}
        analysis_2 = analyze_spectrum_shift(test_cells_2, initial_start=50, initial_end=75, target_start=50, target_end=70)
        apply_spectrum_shift_to_cells(test_cells_2, self.neighbor_graph, self.rules, analysis_2, mode="disappeared_only", include_all_cell_tchs=True)
        self.assertEqual(test_cells_2["TEST_C2"].tch_indices_to_recalculate, [0, 1, 2])

        # Case C: include_all_cell_tchs=True with ONLY BCCH affected (BCCH 75, TCHs valid [60, 62]) -> ALL TCHs must be reviewed!
        cell_test_3 = Cell(cell_id="TEST_C3", station=self.st1, sector="1", azimuth=0, bcch=75, tch_list=[60, 62])
        test_cells_3 = {"TEST_C3": cell_test_3}
        analysis_3 = analyze_spectrum_shift(test_cells_3, initial_start=50, initial_end=75, target_start=50, target_end=70)
        apply_spectrum_shift_to_cells(test_cells_3, self.neighbor_graph, self.rules, analysis_3, mode="disappeared_only", include_all_cell_tchs=True)
        self.assertTrue(test_cells_3["TEST_C3"].recalculate_bcch)
        self.assertEqual(test_cells_3["TEST_C3"].tch_indices_to_recalculate, [0, 1])

    def test_same_station_strict_constraints_and_repair(self):
        """Verify hard constraints and genetic repair strictly forbid cochannel and adjacent on same site."""
        import numpy as np
        from src.engine.parallel import _repair_chromosome
        from src.engine.optimizer import _Variable

        # 1. Check constraints logic
        self.assertTrue(self.rules.is_hard_constraint_violated(
            same_station=True, is_neighbor=True, is_cochannel=True, channel_type_a="BCCH", channel_type_b="TCH"
        ))
        self.assertTrue(self.rules.is_hard_constraint_violated(
            same_station=True, is_neighbor=False, is_cochannel=False, channel_type_a="TCH", channel_type_b="TCH"
        ))

        # 2. Check repair chromosome with 2 cells on the exact same site coordinates (distance = 0)
        same_site_st = Station("SITE_X", "Site X", latitude=41.123456, longitude=-3.654321)
        cell_a = Cell("SITE_A", same_site_st, sector="1", azimuth=0, bcch=100, tch_list=[104])
        cell_b = Cell("SITE_B", same_site_st, sector="2", azimuth=120, bcch=110, tch_list=[114])
        cell_a.recalculate_bcch = True
        cell_b.recalculate_bcch = True

        test_cells = {"SITE_A": cell_a, "SITE_B": cell_b}
        variables = [
            _Variable("SITE_A", "BCCH", -1),
            _Variable("SITE_B", "BCCH", -1)
        ]
        pool = np.array([100, 101, 105, 110], dtype=np.int32)

        # Force conflicting chromosome: gene 0 -> 100 (idx 0), gene 1 -> 101 (idx 1, adjacent!)
        raw_chrom = np.array([0, 1], dtype=np.int32)
        repaired = _repair_chromosome(raw_chrom, variables, test_cells, pool)

        freq_a = pool[repaired[0]]
        freq_b = pool[repaired[1]]
        # Separation on the same site must be >= 2 (no cochannel and no adjacent)
        self.assertGreaterEqual(abs(freq_a - freq_b), 2)

    def test_cost_decomposition_inherited_vs_new(self):
        """Test decomposition of plan cost into inherited (pre-existing) vs newly generated."""
        # Add relation between CELL2 and CELL3 so new conflicts can occur
        self.neighbor_graph.add_relation(NeighborRelation(cell_a_id="CELL2", cell_b_id="CELL3", ho_count=10))

        # Original: CELL1 and CELL2 have cochannel BCCH 50 (pre-existing).
        # In the new plan, CELL1 and CELL2 remain 50 (inherited).
        # We modify CELL3 TCH from 60 to 70, creating a NEW cochannel with CELL2 (TCH 70).
        plan_assignments = {
            "CELL1": FrequencyAssignment("CELL1", bcch=50, tch_list=[60, 62, 64]),
            "CELL2": FrequencyAssignment("CELL2", bcch=50, tch_list=[70, 72]),
            "CELL3": FrequencyAssignment("CELL3", bcch=51, tch_list=[70, 80]),  # Changed 60 to 70
        }

        res = evaluate_plan(
            plan_assignments,
            self.cells,
            self.neighbor_graph,
            self.rules
        )

        # Check that score decomposes cleanly
        self.assertAlmostEqual(res.score, res.inherited_cost + res.new_assignment_cost, places=2)
        self.assertGreater(res.inherited_cost, 0, "Pre-existing cochannel 50-50 should be inherited")
        self.assertGreater(res.new_assignment_cost, 0, "New cochannel from CELL3 should be new assignment cost")

        # Verify violations have is_inherited flag properly set
        has_inherited = False
        has_new = False
        for v in res.violations:
            if v.is_inherited:
                has_inherited = True
            else:
                has_new = True

        self.assertTrue(has_inherited)
        self.assertTrue(has_new)

    def test_html_export_includes_cost_decomposition(self):
        """Verify that HTML export contains decomposition stats, cards and badges."""
        self.neighbor_graph.add_relation(NeighborRelation(cell_a_id="CELL2", cell_b_id="CELL3", ho_count=10))
        plan_assignments = [
            FrequencyAssignment("CELL1", bcch=50, tch_list=[60, 62, 64]),
            FrequencyAssignment("CELL2", bcch=50, tch_list=[70, 72]),
            FrequencyAssignment("CELL3", bcch=51, tch_list=[70, 80]),
        ]
        res = evaluate_plan(
            {a.cell_id: a for a in plan_assignments},
            self.cells,
            self.neighbor_graph,
            self.rules
        )
        res.plan_id = 1
        res.run_id = 1

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            temp_path = f.name

        try:
            export_plan_html(temp_path, self.cells, self.neighbor_graph, self.rules, [res])
            with open(temp_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            self.assertIn("Coste Heredado (Preexistente)", html_content)
            self.assertIn("Coste Nuevas Asignaciones", html_content)
            self.assertIn("Preexistente", html_content)
            self.assertIn("Nueva Asignación", html_content)
            self.assertIn("filter-viol-origin", html_content)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_fast_evaluator_cost_decomposition(self):
        """Verify FastPlanEvaluator.evaluate_matrix decomposes inherited vs new costs properly."""
        import numpy as np

        # CELL1 needs recalculation on TCH index 0 (slot col 1)
        self.c1.tch_indices_to_recalculate = [0]
        fe = FastPlanEvaluator(self.cells, self.neighbor_graph, self.rules)

        # Base matrix has CELL1 BCCH 50, CELL2 BCCH 50 (inherited cochannel)
        matrix = fe.base_freq_matrix.copy()
        # Change CELL1 TCH from 60 to 70 (new assignment that conflicts with CELL2 TCH 70)
        c1_idx = fe.cell_to_idx["CELL1"]
        matrix[c1_idx, 1] = 70

        score, plan = fe.evaluate_matrix(matrix, collect_violations=True, plan_id=1, run_id=1)

        self.assertGreater(plan.inherited_cost, 0)
        self.assertGreater(plan.new_assignment_cost, 0)
        self.assertAlmostEqual(plan.score, plan.inherited_cost + plan.new_assignment_cost, places=1)

        # Check violation objects have is_inherited set
        self.assertTrue(any(v.is_inherited for v in plan.violations))
        self.assertTrue(any(not v.is_inherited for v in plan.violations))

    def test_html_export_scenarios_per_execution(self):
        """Verify HTML exporter groups plans and displays the best candidate of each execution."""
        from src.models.frequency_plan import OptimizationResultList
        import tempfile

        # Plan 1 from Run 1 (score 200), Plan 2 from Run 1 (score 190) -> Best of Run 1 is 190
        p1 = PlanResult(plan_id=1, run_id=1, score=200.0)
        p2 = PlanResult(plan_id=2, run_id=1, score=190.0)
        # Plan 3 from Run 2 (score 170), Plan 4 from Run 2 (score 180) -> Best of Run 2 is 170
        p3 = PlanResult(plan_id=3, run_id=2, score=170.0)
        p4 = PlanResult(plan_id=4, run_id=2, score=180.0)

        for p in (p1, p2, p3, p4):
            p.assignments = [FrequencyAssignment(cid, c.bcch or 50, list(c.tch_list)) for cid, c in self.cells.items()]

        res_list = OptimizationResultList([p3, p4, p2, p1])
        res_list.per_run = {
            1: [p1, p2],
            2: [p3, p4]
        }

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            temp_path = f.name

        try:
            export_plan_html(temp_path, self.cells, self.neighbor_graph, self.rules, res_list)
            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # The scenarios extracted must be the best of Run 1 (Plan 2) and best of Run 2 (Plan 3)
            self.assertIn("Ejecución 1", content)
            self.assertIn("Ejecución 2", content)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_convergence_dialog_single_comparison_plot(self):
        """Verify that ConvergenceDialog uses a single large plot with all execution curves."""
        from PyQt6.QtWidgets import QApplication
        from src.ui.dialogs.convergence_dialog import ConvergenceDialog

        app = QApplication.instance() or QApplication(sys.argv)
        dialog = ConvergenceDialog(total_runs=3, total_gens=50)

        # 1. Must have a single main axes
        self.assertIsNotNone(dialog.ax)
        self.assertEqual(len(dialog.axes), 1)

        # 2. Must have 3 line series in lines dict
        self.assertEqual(len(dialog.lines), 3)
        self.assertIn(1, dialog.lines)
        self.assertIn(2, dialog.lines)
        self.assertIn(3, dialog.lines)

        # 3. Simulate progress updates
        dialog.update_progress(run_idx=1, total_runs=3, gen=10, total_gens=50, best_score=150.0)
        dialog.update_progress(run_idx=2, total_runs=3, gen=10, total_gens=50, best_score=130.0)
        dialog.update_progress(run_idx=3, total_runs=3, gen=10, total_gens=50, best_score=180.0)

        # 4. Best score label must be colored with Run 2's color (as it has 130.0, the best)
        self.assertEqual(dialog.global_best_score, 130.0)
        run2_color = dialog.get_run_color(2)
        self.assertIn(run2_color, dialog.lbl_best.styleSheet())
        self.assertFalse(hasattr(dialog, "lbl_cpu_info"))

        # 5. Finish and verify winning run
        dialog.set_finished(best_score=130.0)
        self.assertTrue(dialog.is_finished)
        self.assertIn(run2_color, dialog.lbl_best.styleSheet())
        dialog.close()


if __name__ == '__main__':
    unittest.main()

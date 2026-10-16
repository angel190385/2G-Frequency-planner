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

        # Case B: include_all_cell_tchs=True -> ALL TCH indices [0, 1, 2] should be marked
        cell_test_2 = Cell(cell_id="TEST_C2", station=self.st1, sector="1", azimuth=0, bcch=50, tch_list=[60, 62, 75])
        test_cells_2 = {"TEST_C2": cell_test_2}
        analysis_2 = analyze_spectrum_shift(test_cells_2, initial_start=50, initial_end=75, target_start=50, target_end=70)
        apply_spectrum_shift_to_cells(test_cells_2, self.neighbor_graph, self.rules, analysis_2, mode="disappeared_only", include_all_cell_tchs=True)
        self.assertEqual(test_cells_2["TEST_C2"].tch_indices_to_recalculate, [0, 1, 2])

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

        self.assertEqual(dialog.global_best_score, 130.0)
        self.assertEqual(len(dialog.histories[1][0]), 1)
        self.assertEqual(dialog.histories[2][1], [130.0])

        # 4. Finish and verify winning run
        dialog.set_finished(best_score=130.0)
        self.assertTrue(dialog.is_finished)
        dialog.close()


if __name__ == '__main__':
    unittest.main()

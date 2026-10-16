"""
Unit and integration tests for autofreq branch features:
1. Spectrum reconfiguration (Initial -> Final Range, disappearing frequencies substitution, new spectrum improvement).
2. Handover statistical calculation models for weights.
3. PDF report generation comparing baseline with 3 scenarios.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PyQt6.QtWidgets import QApplication

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph, NeighborRelation
from src.engine.constraints import FrequencyRules, STATISTICAL_MODELS
from src.engine.cost_function import _ho_weight, evaluate_plan
from src.engine.spectrum_manager import (
    analyze_spectrum_shift,
    apply_spectrum_shift_to_cells,
    greedy_substitute_disappeared_frequencies,
    detect_current_spectrum_range
)
from src.engine.optimizer import FrequencyOptimizer
from src.io.data_loader import load_cells, load_neighbors, load_handovers
from src.io.html_exporter import export_plan_html, generate_baseline_plan


class TestAutofreqFeatures(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), '..', 'test_data')
        cells_path = os.path.join(cls.test_data_dir, 'cells.csv')
        neighbors_path = os.path.join(cls.test_data_dir, 'neighbors.csv')
        ho_path = os.path.join(cls.test_data_dir, 'handovers.csv')

        cls.cells, cls.stations, _ = load_cells(cells_path)
        cls.neighbor_graph, _ = load_neighbors(neighbors_path, cls.cells)
        load_handovers(ho_path, cls.neighbor_graph)

    def test_statistical_models_defined(self):
        """Verify all 5 statistical models are properly defined with metadata."""
        expected_models = ["logarithmic", "linear", "sigmoid", "exponential", "uniform"]
        for m in expected_models:
            self.assertIn(m, STATISTICAL_MODELS)
            info = STATISTICAL_MODELS[m]
            self.assertIn("name", info)
            self.assertIn("formula", info)
            self.assertIn("description", info)
            self.assertIn("usage", info)

    def test_statistical_models_calculation(self):
        """Test calculation of HO weights across all statistical models."""
        stats = {"mean": 50.0, "median": 30.0, "max": 200.0, "min": 0.0, "count": 10.0}

        # None or 0 HO should be neutral 1.0 for all models
        for m in STATISTICAL_MODELS:
            self.assertEqual(_ho_weight(None, model=m, stats=stats), 1.0)
            self.assertEqual(_ho_weight(0, model=m, stats=stats), 1.0)

        # Uniform should always return 1.0
        self.assertEqual(_ho_weight(100, model="uniform", stats=stats), 1.0)

        # Logarithmic: 1.0 + 0.5 * log2(1 + 50)
        w_log = _ho_weight(50, model="logarithmic", stats=stats)
        self.assertAlmostEqual(w_log, 3.8362, places=3)

        # Linear
        w_lin = _ho_weight(50, model="linear", stats=stats)
        self.assertGreater(w_lin, 1.0)

        # Sigmoid
        w_sig = _ho_weight(50, model="sigmoid", stats=stats)
        self.assertGreater(w_sig, 0.2)

        # Exponential
        w_exp = _ho_weight(50, model="exponential", stats=stats)
        self.assertGreater(w_exp, 1.0)

    def test_neighbor_graph_statistics(self):
        """Test HO statistics calculation on NeighborGraph."""
        stats = self.neighbor_graph.get_ho_statistics()
        self.assertIn("mean", stats)
        self.assertIn("median", stats)
        self.assertIn("max", stats)
        self.assertGreater(stats["count"], 0)
        self.assertGreater(stats["max"], 0)

    def test_spectrum_detection_includes_bcch_and_tch(self):
        """Verify that detect_current_spectrum_range scans both BCCH and TCH channels."""
        station = Station(station_id="ST1", name="TestSite", latitude=40.0, longitude=-3.0)
        dummy_cells = {
            "C1": Cell(cell_id="C1", station=station, sector="A", bcch=50, tch_list=[52, 54]),
            "C2": Cell(cell_id="C2", station=station, sector="B", bcch=60, tch_list=[80, 95]),
        }
        det_min, det_max, details = detect_current_spectrum_range(dummy_cells)
        # BCCH max is 60, but TCH max is 95 -> overall max MUST be 95
        self.assertEqual(det_min, 50)
        self.assertEqual(det_max, 95)
        self.assertEqual(details["bcch_min"], 50)
        self.assertEqual(details["bcch_max"], 60)
        self.assertEqual(details["tch_min"], 52)
        self.assertEqual(details["tch_max"], 95)
        self.assertEqual(details["total_bcch_channels"], 2)
        self.assertEqual(details["total_tch_channels"], 4)

    def test_spectrum_detection_and_shift_analysis(self):
        """Test detection of used spectrum and differential analysis."""
        det_min, det_max, _ = detect_current_spectrum_range(self.cells)
        self.assertEqual(det_min, 100)
        self.assertEqual(det_max, 124)

        # Shift: Initial 100-124 -> Final 105-130
        # Disappearing: 100, 101, 102, 103, 104
        # New: 125, 126, 127, 128, 129, 130
        analysis = analyze_spectrum_shift(
            cells=self.cells,
            initial_start=100,
            initial_end=124,
            initial_excluded=[],
            target_start=105,
            target_end=130,
            target_excluded=[]
        )

        self.assertEqual(analysis.disappearing_freqs, [100, 101, 102, 103, 104])
        self.assertEqual(analysis.new_freqs, [125, 126, 127, 128, 129, 130])
        self.assertTrue(analysis.has_disappearing)
        self.assertTrue(analysis.has_new_spectrum)
        self.assertGreater(analysis.total_affected_cells_count, 0)
        self.assertGreater(analysis.total_affected_bcch_count, 0)
        self.assertGreater(analysis.total_affected_tch_count, 0)

    def test_apply_spectrum_shift_and_greedy_substitution(self):
        """Test greedy substitution of disappearing frequencies."""
        # Work on a copy of cells
        cells_copy, _, _ = load_cells(os.path.join(self.test_data_dir, 'cells.csv'))
        rules = FrequencyRules()

        analysis = analyze_spectrum_shift(
            cells=cells_copy,
            initial_start=100,
            initial_end=124,
            target_start=105,
            target_end=130
        )

        # Apply greedy substitution
        subs = greedy_substitute_disappeared_frequencies(
            cells=cells_copy,
            neighbor_graph=self.neighbor_graph,
            rules=rules,
            analysis=analysis
        )

        self.assertGreater(len(subs), 0)

        # Verify that no cell in cells_copy now uses frequencies 100-104
        disappearing_set = set(analysis.disappearing_freqs)
        for cid, cell in cells_copy.items():
            if cell.bcch is not None:
                self.assertNotIn(cell.bcch, disappearing_set, f"Cell {cid} still has disappearing BCCH {cell.bcch}")
            for f in cell.tch_list:
                self.assertNotIn(f, disappearing_set, f"Cell {cid} still has disappearing TCH {f}")

    def test_html_export_3_scenarios(self):
        """Test dynamic HTML dashboard generation comparing baseline with 3 calculated scenarios."""
        rules = FrequencyRules()
        rules.population_size = 20
        rules.num_generations = 10
        rules.top_k_results = 3
        rules.num_runs = 2

        # Mark 3 cells for optimization
        cells_calc, _, _ = load_cells(os.path.join(self.test_data_dir, 'cells.csv'))
        list(cells_calc.values())[0].recalculate_bcch = True
        list(cells_calc.values())[1].tch_indices_to_recalculate = [0]
        list(cells_calc.values())[2].recalculate_bcch = True

        optimizer = FrequencyOptimizer(cells_calc, self.neighbor_graph, rules)
        results = optimizer.optimize()

        self.assertGreaterEqual(len(results), 1)

        html_path = os.path.join(self.test_data_dir, 'test_output_report.html')
        if os.path.exists(html_path):
            os.remove(html_path)

        export_plan_html(
            file_path=html_path,
            cells=cells_calc,
            neighbor_graph=self.neighbor_graph,
            rules=rules,
            results=results
        )

        self.assertTrue(os.path.exists(html_path))
        self.assertGreater(os.path.getsize(html_path), 5000)

        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("const data =", content)
            self.assertIn("selectScenario", content)
            self.assertIn("renderMatrixTable", content)

        # Clean up
        os.remove(html_path)


if __name__ == '__main__':
    unittest.main()

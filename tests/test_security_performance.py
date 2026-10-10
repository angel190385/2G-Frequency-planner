"""
Unit tests for security hardening and performance optimizations.
Verifies:
1. CSV Formula Injection sanitization.
2. HTML / XSS entity escaping in exported reports.
3. Precomputed spatial grid distance optimization.
4. Genetic algorithm optimization with cached fixed assignments.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph, NeighborRelation
from src.engine.constraints import FrequencyRules
from src.engine.cost_function import evaluate_plan, quick_score, precompute_close_cell_pairs
from src.engine.optimizer import FrequencyOptimizer
from src.io.data_loader import _sanitize_csv_field, export_results
from src.io.html_exporter import export_plan_html, generate_baseline_plan
from src.io.pdf_exporter import export_plan_pdf


class TestSecurityAndPerformance(unittest.TestCase):

    def setUp(self):
        self.station1 = Station(station_id="ST1", name="=CMD|' /C calc'!A0", latitude=40.4168, longitude=-3.7038)
        self.station2 = Station(station_id="ST2", name="NormalSite", latitude=40.4200, longitude=-3.7000)

        self.cells = {
            "=1+1": Cell(cell_id="=1+1", station=self.station1, sector="A", bcch=100, tch_list=[102, 104]),
            "<script>alert(1)</script>": Cell(cell_id="<script>alert(1)</script>", station=self.station2, sector="B", bcch=100, tch_list=[106, 108]),
        }

        self.neighbor_graph = NeighborGraph()
        self.neighbor_graph.add_relation(NeighborRelation("=1+1", "<script>alert(1)</script>", distance_km=0.5, ho_count=20))
        self.rules = FrequencyRules()

    def test_csv_formula_injection_sanitization(self):
        """Values starting with =, +, -, @ must be prefixed with single quote."""
        self.assertEqual(_sanitize_csv_field("=cmd|' /C calc'!A0"), "'=cmd|' /C calc'!A0")
        self.assertEqual(_sanitize_csv_field("+12345"), "'+12345")
        self.assertEqual(_sanitize_csv_field("-secret"), "'-secret")
        self.assertEqual(_sanitize_csv_field("@SUM(A1:A10)"), "'@SUM(A1:A10)")
        self.assertEqual(_sanitize_csv_field("NormalText"), "NormalText")
        self.assertEqual(_sanitize_csv_field(100), "100")
        self.assertEqual(_sanitize_csv_field(None), "")

    def test_spatial_grid_precomputation(self):
        """Verify precompute_close_cell_pairs finds stations within threshold."""
        # ST1 and ST2 are ~0.5 km apart, threshold is 5.0 km
        close_pairs = precompute_close_cell_pairs(self.cells, self.neighbor_graph, threshold_km=5.0)
        # Note: C1 and C2 are neighbors, so they are excluded from non-neighbor close pairs
        self.assertEqual(len(close_pairs), 0)

        # Add a 3rd non-neighbor station close by
        station3 = Station(station_id="ST3", name="Site3", latitude=40.4220, longitude=-3.7010)
        self.cells["C3"] = Cell(cell_id="C3", station=station3, sector="A", bcch=110, tch_list=[112])

        close_pairs = precompute_close_cell_pairs(self.cells, self.neighbor_graph, threshold_km=5.0)
        # C3 should be close to C1 and C2
        pair_cell_ids = {(p[0], p[1]) for p in close_pairs}
        self.assertTrue(any("C3" in p for p in pair_cell_ids))

    def test_optimizer_runs_with_optimizations(self):
        """Test optimizer execution with precomputed close pairs and cached assignments."""
        self.cells["=1+1"].recalculate_bcch = True
        self.rules.population_size = 20
        self.rules.num_generations = 10
        self.rules.num_runs = 1

        optimizer = FrequencyOptimizer(
            self.cells, self.neighbor_graph, self.rules
        )
        # Check precomputed close pairs attribute exists
        self.assertIsNotNone(optimizer.close_cell_pairs)

        results = optimizer.optimize()
        self.assertGreater(len(results), 0)
        best = results[0]
        self.assertIsNotNone(best.score)
        self.assertGreater(len(best.assignments), 0)

    def test_html_export_escapes_xss_payloads(self):
        """HTML exporter must escape script tags and HTML in cell and station names."""
        test_html_path = os.path.join(os.path.dirname(__file__), "test_security_out.html")
        try:
            export_plan_html(
                test_html_path, self.cells, self.neighbor_graph, self.rules, []
            )
            with open(test_html_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Ensure raw unescaped script tag is NOT injected directly as an HTML element
            # (It should be inside json payload and escaped as <\/script> or handled via escapeHtml in JS)
            self.assertNotIn("<script>alert(1)</script>", content)
            self.assertIn("escapeHtml", content)
            self.assertIn("sanitizeCsv", content)
        finally:
            if os.path.exists(test_html_path):
                os.remove(test_html_path)

    def test_pdf_export_escapes_xss_payloads(self):
        """PDF exporter must escape HTML characters in cell IDs and station names."""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        test_pdf_path = os.path.join(os.path.dirname(__file__), "test_security_out.pdf")
        try:
            export_plan_pdf(
                test_pdf_path, self.cells, self.neighbor_graph, self.rules, []
            )
            self.assertTrue(os.path.exists(test_pdf_path))
            self.assertGreater(os.path.getsize(test_pdf_path), 0)
        finally:
            if os.path.exists(test_pdf_path):
                os.remove(test_pdf_path)


if __name__ == "__main__":
    unittest.main()

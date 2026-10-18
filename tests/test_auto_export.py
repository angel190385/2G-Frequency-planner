"""
Unit tests for automatic export of results (CSV, HTML, and debug text file)
upon completion of optimization calculations.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import PyQt6.QtWebEngineWidgets  # Must be imported before QApplication is created
except ImportError:
    pass

from PyQt6.QtWidgets import QApplication

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph, NeighborRelation
from src.models.frequency_plan import FrequencyAssignment, PlanResult, Violation
from src.engine.constraints import FrequencyRules
from src.io.auto_export import (
    get_application_root_dir,
    generate_debug_info_text,
    export_execution_bundle
)


class TestAutoExport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        # Create test stations and cells
        self.st1 = Station("ST1", "Site South", latitude=40.0000, longitude=-3.0000)
        self.st2 = Station("ST2", "Site North", latitude=40.0100, longitude=-3.0000)

        self.c1 = Cell(
            cell_id="CELL_1",
            station=self.st1,
            sector="A",
            azimuth=0.0,
            bcch=100,
            tch_list=[102, 104],
            recalculate_bcch=True
        )
        self.c2 = Cell(
            cell_id="CELL_2",
            station=self.st2,
            sector="B",
            azimuth=180.0,
            bcch=101,
            tch_list=[103, 105],
            recalculate_bcch=False
        )
        self.cells = {"CELL_1": self.c1, "CELL_2": self.c2}

        # Create neighbor graph with relation and handover
        self.neighbor_graph = NeighborGraph()
        rel = NeighborRelation(
            cell_a_id="CELL_1",
            cell_b_id="CELL_2",
            distance_km=1.11,
            ho_count=45
        )
        self.neighbor_graph.add_relation(rel)

        # Rules
        self.rules = FrequencyRules(
            freq_range_start=100,
            freq_range_end=110,
            enable_azimuth_weight=True,
            azimuth_weight_min=0.3,
            azimuth_weight_max=1.8,
            num_runs=2,
            num_generations=10
        )

        # Plan results
        v1 = Violation(
            cell_a_id="CELL_1",
            cell_b_id="CELL_2",
            freq_a=100,
            freq_b=101,
            violation_type="adjacent",
            channel_type_a="BCCH",
            channel_type_b="BCCH",
            ho_count=45,
            distance_km=1.11,
            weighted_cost=88.5
        )
        p1 = PlanResult(
            plan_id=1,
            run_id=1,
            score=88.5,
            assignments=[
                FrequencyAssignment("CELL_1", bcch=100, tch_list=[102, 104]),
                FrequencyAssignment("CELL_2", bcch=101, tch_list=[103, 105])
            ],
            adjacent_bcch_bcch=1,
            violations=[v1]
        )
        p2 = PlanResult(
            plan_id=2,
            run_id=2,
            score=120.0,
            assignments=[
                FrequencyAssignment("CELL_1", bcch=100, tch_list=[102, 104]),
                FrequencyAssignment("CELL_2", bcch=100, tch_list=[103, 105])
            ],
            cochannel_bcch_bcch=1
        )
        self.results = [p1, p2]

    def test_get_application_root_dir(self):
        root = get_application_root_dir()
        self.assertTrue(os.path.isdir(root))
        self.assertTrue(os.path.exists(os.path.join(root, "src")))

    def test_generate_debug_info_text_content(self):
        start = datetime(2026, 10, 17, 10, 0, 0)
        end = datetime(2026, 10, 17, 10, 0, 15)

        text = generate_debug_info_text(
            cells=self.cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            results=self.results,
            start_time=start,
            end_time=end,
            was_cancelled=False,
            workers_used=4
        )

        # Verify key sections exist
        self.assertIn("FREQ PLANNER 2G — REPORTE DE CONFIGURACIÓN Y DEPURACIÓN", text)
        self.assertIn("1. METADATOS Y ESTADO DE LA EJECUCIÓN", text)
        self.assertIn("COMPLETADO CON ÉXITO", text)
        self.assertIn("15.00 s", text)
        self.assertIn("2. ENTORNO DEL SISTEMA Y RECURSOS", text)
        self.assertIn("Workers Configurados:   4", text)
        self.assertIn("3. CONFIGURACIÓN DE REGLAS Y PARÁMETROS", text)
        self.assertIn("Rango de Frecuencias:   100 a 110", text)
        self.assertIn("Ponderación Azimuth Activa:    Sí", text)
        self.assertIn("4. TOPOLOGÍA DE RED Y DATOS DE ENTRADA", text)
        self.assertIn("Total Celdas Cargadas:         2", text)
        self.assertIn("5. RESUMEN GLOBAL DE RESULTADOS", text)
        self.assertIn("Total Planes Devueltos:        2", text)
        self.assertIn("Mejor Score Obtenido:          88.5", text)
        self.assertIn("6. DETALLE EXHAUSTIVO DEL MEJOR PLAN", text)
        self.assertIn("Top 30 Conflictos Más Severos", text)
        self.assertIn("CELL_1", text)
        self.assertIn("CELL_2", text)
        self.assertIn("1.11", text)
        self.assertIn("Distribución de Frecuencias Asignadas (Histograma ARFCN)", text)
        self.assertIn("7. TABLA COMPARATIVA DE TODOS LOS PLANES", text)

    def test_generate_debug_info_text_cancelled(self):
        text = generate_debug_info_text(
            cells=self.cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            results=self.results,
            was_cancelled=True
        )
        self.assertIn("DETENIDO / CANCELADO POR EL USUARIO", text)

    def test_export_execution_bundle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            start = datetime(2026, 10, 17, 12, 0, 0)
            end = datetime(2026, 10, 17, 12, 1, 30)

            bundle = export_execution_bundle(
                cells=self.cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                results=self.results,
                start_time=start,
                end_time=end,
                was_cancelled=False,
                app_dir=tmp_dir,
                folder_prefix="ejecucion_test"
            )

            folder = bundle["folder"]
            csv_file = bundle["csv"]
            html_file = bundle["html"]
            debug_file = bundle["debug"]

            # Directory must exist and follow naming
            self.assertTrue(os.path.isdir(folder))
            self.assertTrue(os.path.basename(folder).startswith("ejecucion_test_20261017_120130"))

            # CSV file must exist and have content
            self.assertTrue(os.path.isfile(csv_file))
            self.assertGreater(os.path.getsize(csv_file), 100)
            with open(csv_file, "r", encoding="utf-8") as f:
                csv_content = f.read()
                self.assertIn("CELL_1", csv_content)
                self.assertIn("CELL_2", csv_content)

            # HTML file must exist and contain HTML tags
            self.assertTrue(os.path.isfile(html_file))
            self.assertGreater(os.path.getsize(html_file), 500)
            with open(html_file, "r", encoding="utf-8") as f:
                html_content = f.read()
                self.assertIn("<!DOCTYPE html>", html_content)

            # Debug text file must exist and have detailed info
            self.assertTrue(os.path.isfile(debug_file))
            self.assertGreater(os.path.getsize(debug_file), 500)
            with open(debug_file, "r", encoding="utf-8") as f:
                debug_content = f.read()
                self.assertIn("REPORTE DE CONFIGURACIÓN Y DEPURACIÓN", debug_content)
                self.assertIn("Rango de Frecuencias:   100 a 110", debug_content)
                self.assertIn("1.11", debug_content)

    def test_export_empty_results_raises_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(ValueError):
                export_execution_bundle(
                    cells=self.cells,
                    neighbor_graph=self.neighbor_graph,
                    rules=self.rules,
                    results=[],
                    app_dir=tmp_dir
                )

    def test_main_window_auto_export_integration(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication
        from src.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv)
        win = MainWindow()
        win.cells = self.cells
        win.neighbor_graph = self.neighbor_graph
        win.rules = self.rules

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.io.auto_export.get_application_root_dir", return_value=tmp_dir):
                win._on_optimization_finished(self.results)

                created_folders = [f for f in os.listdir(tmp_dir) if f.startswith("ejecucion_")]
                self.assertEqual(len(created_folders), 1)
                exp_dir = os.path.join(tmp_dir, created_folders[0])
                self.assertTrue(os.path.isfile(os.path.join(exp_dir, "resultados_frecuencias.csv")))
                self.assertTrue(os.path.isfile(os.path.join(exp_dir, "informe_optimizacion.html")))
                self.assertTrue(os.path.isfile(os.path.join(exp_dir, "configuracion_y_depuracion.txt")))

        win.close()

    def test_recalculate_all_tch_and_recalculate_all_buttons(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        from src.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv)
        win = MainWindow()
        win.cells = self.cells
        win.neighbor_graph = self.neighbor_graph
        win.rules = self.rules
        win._refresh_calc_table()

        # 1. Test 'Recalcular todos los TCH'
        win.btn_recalc_all_tch.click()

        for row in range(win.calc_table.rowCount()):
            self.assertEqual(win.calc_table.item(row, 0).checkState(), Qt.CheckState.Checked)
            self.assertEqual(win.calc_table.item(row, 5).checkState(), Qt.CheckState.Unchecked)
            self.assertEqual(win.calc_table.item(row, 7).text(), "1,2")

        self.assertFalse(self.c1.recalculate_bcch)
        self.assertEqual(self.c1.tch_indices_to_recalculate, [0, 1])
        self.assertTrue(self.c1.needs_calculation)

        self.assertFalse(self.c2.recalculate_bcch)
        self.assertEqual(self.c2.tch_indices_to_recalculate, [0, 1])
        self.assertTrue(self.c2.needs_calculation)

        self.assertEqual(win.lbl_calc_cells.text(), "2 celdas seleccionadas")
        self.assertEqual(win.lbl_calc_vars.text(), "4 variables a optimizar")

        # 2. Test 'Recalcular todo'
        win.btn_recalc_all.click()

        for row in range(win.calc_table.rowCount()):
            self.assertEqual(win.calc_table.item(row, 0).checkState(), Qt.CheckState.Checked)
            self.assertEqual(win.calc_table.item(row, 5).checkState(), Qt.CheckState.Checked)
            self.assertEqual(win.calc_table.item(row, 7).text(), "1,2")

        self.assertTrue(self.c1.recalculate_bcch)
        self.assertEqual(self.c1.tch_indices_to_recalculate, [0, 1])
        self.assertTrue(self.c1.needs_calculation)

        self.assertTrue(self.c2.recalculate_bcch)
        self.assertEqual(self.c2.tch_indices_to_recalculate, [0, 1])
        self.assertTrue(self.c2.needs_calculation)

        self.assertEqual(win.lbl_calc_cells.text(), "2 celdas seleccionadas")
        self.assertEqual(win.lbl_calc_vars.text(), "6 variables a optimizar")

        # 3. Test 'Deseleccionar todo' (clears Recalc, Recalc BCCH, and all TCHs to recalculate)
        win.btn_deselect_all.click()

        for row in range(win.calc_table.rowCount()):
            self.assertEqual(win.calc_table.item(row, 0).checkState(), Qt.CheckState.Unchecked)
            self.assertEqual(win.calc_table.item(row, 5).checkState(), Qt.CheckState.Unchecked)
            self.assertEqual(win.calc_table.item(row, 7).text(), "")

        self.assertFalse(self.c1.recalculate_bcch)
        self.assertEqual(self.c1.tch_indices_to_recalculate, [])
        self.assertFalse(self.c1.needs_calculation)

        self.assertFalse(self.c2.recalculate_bcch)
        self.assertEqual(self.c2.tch_indices_to_recalculate, [])
        self.assertFalse(self.c2.needs_calculation)

        self.assertEqual(win.lbl_calc_cells.text(), "0 celdas seleccionadas")
        self.assertEqual(win.lbl_calc_vars.text(), "0 variables a optimizar")

        win.close()


if __name__ == "__main__":
    unittest.main()



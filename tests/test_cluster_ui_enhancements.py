"""
Test cluster UI enhancements:
1. ClusterProgressDialog with miniature convergence plot
2. MapWidget optimization parameters (runs & CPUs)
3. Calculation table with Zona column and synchronization
"""
import sys
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph
from src.models.zone import OptimizationZone
from src.engine.constraints import FrequencyRules
from src.engine.cluster_optimizer import ZoneOptimizationProgress
from src.ui.dialogs.cluster_progress_dialog import ClusterProgressDialog
from src.ui.widgets.map_widget import MapWidget
from src.ui.main_window import MainWindow


class TestClusterUIEnhancements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_cluster_progress_dialog_convergence_plot(self):
        """Test miniature convergence plot inside ClusterProgressDialog."""
        dlg = ClusterProgressDialog(total_zones=2)
        self.assertIsNotNone(dlg.canvas)
        self.assertIsNotNone(dlg.figure)
        self.assertIsNotNone(dlg.ax)

        # Simulate Zone 1 Run 1 Gen 1..5
        p1 = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=1,
            total_runs=2,
            current_gen=1,
            total_gens=10,
            best_score=150.0
        )
        dlg.update_progress(p1)
        self.assertIn(1, dlg._lines)
        self.assertEqual(dlg._zone_histories[1][0], [1])
        self.assertEqual(dlg._zone_histories[1][1], [150.0])

        # Next gen
        p2 = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=1,
            total_runs=2,
            current_gen=10,
            total_gens=10,
            best_score=120.0
        )
        dlg.update_progress(p2)
        self.assertEqual(dlg._zone_histories[1][0], [1, 10])
        self.assertEqual(dlg._zone_histories[1][1], [150.0, 120.0])
        self.assertEqual(dlg.bar_current.value(), 50)
        self.assertIn("10 / 20 Iteraciones", dlg.bar_current.text())
        self.assertEqual(dlg.lbl_run_gen.text(), "Iteración: 10 / 20")
        self.assertNotIn("Run actual:", dlg.lbl_run_gen.text())

        # Run 2 of Zone 1: Run 1 reached 10, Run 2 starts at 1 -> sum = 10 + 1 = 11 of 20 (55%)
        p3 = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=2,
            total_runs=2,
            current_gen=1,
            total_gens=10,
            best_score=140.0
        )
        dlg.update_progress(p3)
        self.assertIn(2, dlg._lines)
        self.assertEqual(dlg._zone_histories[2][0], [1])
        self.assertEqual(dlg.bar_current.value(), 55)
        self.assertIn("11 / 20 Iteraciones", dlg.bar_current.text())
        self.assertEqual(dlg.lbl_run_gen.text(), "Iteración: 11 / 20")
        self.assertNotIn("Run actual:", dlg.lbl_run_gen.text())
        # Run 1 is still better (120 < 140) -> color remains RUN_COLORS[0] (#00d4aa)
        self.assertIn("#00d4aa", dlg.bar_current.styleSheet())

        # When Run 2 improves to 100.0 on Gen 2 -> Run 2 becomes best, bar turns into RUN_COLORS[1] (#38bdf8)
        p3_better = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=2,
            total_runs=2,
            current_gen=2,
            total_gens=10,
            best_score=100.0
        )
        dlg.update_progress(p3_better)
        self.assertEqual(dlg.bar_current.value(), 60)
        self.assertIn("12 / 20 Iteraciones", dlg.bar_current.text())
        self.assertEqual(dlg.lbl_run_gen.text(), "Iteración: 12 / 20")
        self.assertNotIn("Run actual:", dlg.lbl_run_gen.text())
        self.assertIn("#38bdf8", dlg.bar_current.styleSheet())
        self.assertIn("(Run 2)", dlg.lbl_best_score.text())

        # Switch to Zone 2: check that previous lines and history are cleared for the new zone
        p4 = ZoneOptimizationProgress(
            zone_index=2,
            total_zones=2,
            zone_name="Zona 2",
            current_run=1,
            total_runs=1,
            current_gen=1,
            total_gens=5,
            best_score=90.0
        )
        dlg.update_progress(p4)
        self.assertEqual(dlg._current_zone_index, 2)
        self.assertEqual(len(dlg._zone_histories), 1)
        self.assertEqual(dlg._zone_histories[1][0], [1])
        self.assertEqual(dlg._zone_histories[1][1], [90.0])

        # Test cumulative scenario across runs: Zone 1 with Run 1 at 500, Run 2 at 500, Run 3 at 44
        p_r1 = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=1,
            total_runs=3,
            current_gen=500,
            total_gens=500,
            best_score=250000000.0,
        )
        dlg.update_progress(p_r1)

        p_r2 = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=2,
            total_runs=3,
            current_gen=500,
            total_gens=500,
            best_score=240000000.0,
        )
        dlg.update_progress(p_r2)

        p_user = ZoneOptimizationProgress(
            zone_index=1,
            total_zones=2,
            zone_name="Zona 1",
            current_run=3,
            total_runs=3,
            current_gen=44,
            total_gens=500,
            best_score=224994993.81,
            global_run=3,
            total_global_runs=6
        )
        dlg.update_progress(p_user)
        # Total gens = 3 * 500 = 1500. Sum = 500 + 500 + 44 = 1044 of 1500 (69%)
        self.assertIn("1044 / 1500 Iteraciones", dlg.bar_current.text())
        self.assertEqual(dlg.bar_current.value(), int(1044 / 1500 * 100))
        self.assertEqual(dlg.lbl_run_gen.text(), "Iteración: 1044 / 1500")
        self.assertNotIn("Run actual:", dlg.lbl_run_gen.text())

        # bar_overall should strictly show percentage and count of analyzed zones (ratio)
        self.assertEqual(dlg.bar_overall.value(), 0)
        self.assertIn("0% (0 de 2 Zonas analizadas)", dlg.bar_overall.text())

        # When Zone 1 completes, ratio becomes 1 / 2 = 50%
        dlg.log_zone_completed("Zona 1", 100.0, 10)
        self.assertEqual(dlg.bar_overall.value(), 50)
        self.assertIn("50% (1 de 2 Zonas analizadas)", dlg.bar_overall.text())

        # When Zone 2 completes, ratio becomes 2 / 2 = 100%
        dlg.log_zone_completed("Zona 2", 90.0, 10)
        self.assertEqual(dlg.bar_overall.value(), 100)
        self.assertIn("100% (2 de 2 Zonas analizadas)", dlg.bar_overall.text())
        dlg.close()

    def test_map_widget_optimization_parameters(self):
        """Test spin_frontier, spin_runs, and spin_workers in MapWidget."""
        map_w = MapWidget(mode="zones")
        self.assertTrue(hasattr(map_w, "spin_frontier"))
        self.assertTrue(hasattr(map_w, "spin_runs"))
        self.assertTrue(hasattr(map_w, "spin_workers"))

        self.assertGreaterEqual(map_w.spin_workers.value(), 1)
        self.assertEqual(map_w.spin_runs.value(), 3)

        # Add a dummy zone
        z = OptimizationZone(
            zone_id="z1",
            name="Zona Alpha",
            polygon_coords=[(40.0, -3.0), (40.1, -3.0), (40.1, -3.1)],
            rules=FrequencyRules()
        )
        map_w.zones.append(z)

        # Change runs
        map_w.spin_runs.setValue(5)
        self.assertEqual(z.rules.num_runs, 5)

        map_w.close()

    def test_calc_table_zona_column_and_sync(self):
        """Test calculation table with Zona column and polygon synchronization."""
        win = MainWindow()
        st = Station("ST1", "Emplazamiento 1", 40.4, -3.7)
        c1 = Cell("C1", st, 1, bcch=10, tch_list=[12, 14])
        c2 = Cell("C2", st, 2, bcch=20, tch_list=[22, 24])
        win.cells = {"C1": c1, "C2": c2}

        win._refresh_calc_table()
        self.assertEqual(win.calc_table.columnCount(), 10)
        self.assertEqual(win.calc_table.horizontalHeaderItem(win.COL_ZONE).text(), "Zona")

        # Initially unassigned
        self.assertEqual(win.calc_table.item(0, win.COL_ZONE).text(), "—")
        self.assertEqual(win.calc_table.item(1, win.COL_ZONE).text(), "—")

        # Define a zone covering C1
        zone = OptimizationZone(
            zone_id="z_test",
            name="Polígono Centro",
            polygon_coords=[(40.0, -3.0), (40.1, -3.0), (40.1, -3.1)],
            color="#38bdf8",
            cell_ids={"C1"}
        )
        win.zone_map_widget.zones = [zone]
        win.zone_map_widget.zones_changed.emit(win.zone_map_widget.zones)

        # Calculation table should have refreshed automatically via signal
        # Row 0 (C1) should now show "● Polígono Centro"
        c1_row = 0 if win.calc_table.item(0, win.COL_CELL_ID).text() == "C1" else 1
        c2_row = 1 - c1_row

        self.assertEqual(win.calc_table.item(c1_row, win.COL_ZONE).text(), "● Polígono Centro")
        self.assertEqual(win.calc_table.item(c2_row, win.COL_ZONE).text(), "—")

        win.close()

    def test_apply_to_calc_switches_tab_and_configures_mode(self):
        """Test that clicking Apply to Calc switches to Calculation tab and handles zones vs no-zones."""
        win = MainWindow()
        st = Station("ST1", "Emplazamiento 1", 40.4, -3.7)
        c1 = Cell("C1", st, 1, bcch=10, tch_list=[12, 14])
        win.cells = {"C1": c1}
        win.zone_map_widget.cells = win.cells

        # Case 1: No zones painted -> applies, opens calculation tab, single optimization mode
        win.tabs.setCurrentWidget(win.zone_map_widget)
        win.zone_map_widget._apply_zones_to_calc_table()
        self.assertEqual(win.tabs.currentWidget(), win.calc_tab_widget)
        self.assertFalse(win.chk_sequential_zones.isChecked())
        self.assertEqual(win.btn_calculate.text(), "🚀 CALCULAR FRECUENCIAS")

        # Case 2: Zones painted -> applies, opens calculation tab, sequential mode activated
        z = OptimizationZone(
            zone_id="z_test",
            name="Zona Test",
            polygon_coords=[(40.0, -3.0), (40.1, -3.0), (40.1, -3.1)],
            cell_ids={"C1"}
        )
        win.zone_map_widget.zones = [z]
        win.tabs.setCurrentWidget(win.zone_map_widget)
        win.zone_map_widget._apply_zones_to_calc_table()
        self.assertEqual(win.tabs.currentWidget(), win.calc_tab_widget)
        self.assertTrue(win.chk_sequential_zones.isChecked())
        self.assertEqual(win.btn_calculate.text(), "🚀 CALCULAR FRECUENCIAS (Secuencial)")

        win.close()

    def test_apply_zones_minimum_out_of_range_selection(self):
        """Test that applying zones flags only frequencies outside the allowed range by default."""
        win = MainWindow()
        st = Station("ST1", "Emplazamiento 1", 40.4, -3.7)

        # C1 has all frequencies within 1..50
        c1 = Cell("C1", st, 1, bcch=10, tch_list=[12, 14])
        # C2 has BCCH=80 (out) and TCH2=85 (out), while TCH1=12 is in
        c2 = Cell("C2", st, 2, bcch=80, tch_list=[12, 85])

        win.cells = {"C1": c1, "C2": c2}
        win.zone_map_widget.cells = win.cells

        rules = FrequencyRules(freq_range_start=1, freq_range_end=50)
        win.rules = rules

        z = OptimizationZone(
            zone_id="z_test",
            name="Zona Test",
            polygon_coords=[(40.0, -3.0), (40.1, -3.0), (40.1, -3.1)],
            cell_ids={"C1", "C2"},
            rules=rules
        )
        self.assertFalse(z.recalculate_bcch)
        self.assertFalse(z.recalculate_tch)

        win.zone_map_widget.zones = [z]
        win.zone_map_widget._apply_zones_to_calc_table()

        # C1 should be completely untouched because all frequencies are within 1..50
        self.assertFalse(c1.recalculate_bcch)
        self.assertEqual(c1.tch_indices_to_recalculate, [])
        self.assertFalse(c1.needs_calculation)

        # C2 should only recalculate BCCH and TCH index 1 (85), not TCH index 0 (12)
        self.assertTrue(c2.recalculate_bcch)
        self.assertEqual(c2.tch_indices_to_recalculate, [1])
        self.assertTrue(c2.needs_calculation)

        # Now test forced recalculation when zone checkboxes are checked
        z.recalculate_bcch = True
        z.recalculate_tch = True
        win.zone_map_widget._apply_zones_to_calc_table()

        self.assertTrue(c1.recalculate_bcch)
        self.assertEqual(c1.tch_indices_to_recalculate, [0, 1])
        self.assertTrue(c2.recalculate_bcch)
        self.assertEqual(c2.tch_indices_to_recalculate, [0, 1])

        win.close()

    def test_select_only_out_of_range_action(self):
        """Test the 'Solo fuera de rango' quick action in MainWindow."""
        win = MainWindow()
        st = Station("ST1", "Emplazamiento 1", 40.4, -3.7)
        c1 = Cell("C1", st, 1, bcch=10, tch_list=[12, 14])
        c2 = Cell("C2", st, 2, bcch=80, tch_list=[12, 85])
        win.cells = {"C1": c1, "C2": c2}
        win.rules = FrequencyRules(freq_range_start=1, freq_range_end=50)

        self.assertTrue(hasattr(win, "btn_select_out_of_range"))

        # Trigger quick action
        win._select_only_out_of_range()

        self.assertFalse(c1.recalculate_bcch)
        self.assertEqual(c1.tch_indices_to_recalculate, [])
        self.assertTrue(c2.recalculate_bcch)
        self.assertEqual(c2.tch_indices_to_recalculate, [1])

        win.close()

    def test_sequential_results_multi_run_tabs(self):
        """Test that ResultComparisonWidget creates per-polygon tabs and multi-run tabs."""
        from src.ui.widgets.result_comparison import ResultComparisonWidget, MultiRunViewWidget
        from src.models.frequency_plan import PlanResult, FrequencyAssignment, OptimizationResultList
        from src.models.zone import OptimizationZone

        widget = ResultComparisonWidget()
        st = Station("ST1", "Emplazamiento 1", 40.4, -3.7)
        c1 = Cell("C1", st, 1, bcch=10, tch_list=[12])
        c2 = Cell("C2", st, 2, bcch=14, tch_list=[16])
        cells = {"C1": c1, "C2": c2}

        # Plans for Zone 1: 2 runs
        z1_p_r1 = PlanResult(plan_id=1, run_id=1, assignments=[FrequencyAssignment("C1", 20, [22])], score=100.0)
        z1_p_r2 = PlanResult(plan_id=2, run_id=2, assignments=[FrequencyAssignment("C1", 24, [26])], score=90.0)
        z1_res = OptimizationResultList(
            [z1_p_r2, z1_p_r1],
            per_run={1: [z1_p_r1], 2: [z1_p_r2]},
            per_run_history={1: [(1, 120.0), (2, 100.0)], 2: [(1, 110.0), (2, 90.0)]}
        )

        # Plans for Zone 2: 2 runs
        z2_p_r1 = PlanResult(plan_id=3, run_id=1, assignments=[FrequencyAssignment("C2", 30, [32])], score=80.0)
        z2_p_r2 = PlanResult(plan_id=4, run_id=2, assignments=[FrequencyAssignment("C2", 34, [36])], score=70.0)
        z2_res = OptimizationResultList(
            [z2_p_r2, z2_p_r1],
            per_run={1: [z2_p_r1], 2: [z2_p_r2]},
            per_run_history={1: [(1, 95.0), (2, 80.0)], 2: [(1, 85.0), (2, 70.0)]}
        )

        zone1 = OptimizationZone("z1", "Polígono Centro", polygon_coords=[], cell_ids={"C1"})
        zone2 = OptimizationZone("z2", "Polígono Norte", polygon_coords=[], cell_ids={"C2"})

        # Consolidated global results across the 2 runs
        glob_r1 = PlanResult(plan_id=1, run_id=1, assignments=[FrequencyAssignment("C1", 20, [22]), FrequencyAssignment("C2", 30, [32])], score=180.0)
        glob_r2 = PlanResult(plan_id=2, run_id=2, assignments=[FrequencyAssignment("C1", 24, [26]), FrequencyAssignment("C2", 34, [36])], score=160.0)
        glob_res = OptimizationResultList(
            [glob_r2, glob_r1],
            per_run={1: [glob_r1], 2: [glob_r2]},
            per_run_history={1: [(1, 120.0), (2, 100.0)], 2: [(1, 110.0), (2, 90.0)]}
        )

        base_plan = PlanResult(plan_id=0, run_id=0, assignments=[FrequencyAssignment("C1", 10, [12]), FrequencyAssignment("C2", 14, [16])], score=500.0)

        widget.load_results(
            results=glob_res,
            cells=cells,
            baseline=base_plan,
            zone_results={"z1": z1_res, "z2": z2_res},
            zones=[zone1, zone2]
        )

        # Main tabs should contain: Polígono Centro + Polígono Norte + Red Global Consolidada
        self.assertEqual(widget.results_tabs.count(), 3)
        self.assertIn("Polígono Centro", widget.results_tabs.tabText(0))
        self.assertIn("Polígono Norte", widget.results_tabs.tabText(1))
        self.assertIn("Red Global Consolidada", widget.results_tabs.tabText(2))

        # Default selected tab must be the first polygon (Tab 0)
        self.assertEqual(widget.results_tabs.currentIndex(), 0)

        # Check sub-tabs inside Polígono Centro (Tab 0)
        z1_tab = widget.results_tabs.widget(0)
        self.assertIsInstance(z1_tab, MultiRunViewWidget)
        self.assertEqual(z1_tab.count(), 4)  # Mejores + Ejec 1 + Ejec 2 + Convergencia
        self.assertIn("Mejores de Cada Ejecución", z1_tab.tabText(0))
        self.assertIn("Ejecución 1", z1_tab.tabText(1))
        self.assertIn("Ejecución 2", z1_tab.tabText(2))
        self.assertIn("Curvas de Convergencia", z1_tab.tabText(3))

        # Scoped cells check: Polígono Centro assignment table should only list C1
        z1_run2 = z1_tab.widget(2)
        z1_run2.summary_table.selectRow(1)
        self.assertEqual(z1_run2.assign_table.rowCount(), 1)
        self.assertEqual(z1_run2.assign_table.item(0, 0).text(), "C1")

        # Check sub-tabs inside Polígono Norte (Tab 1)
        z2_tab = widget.results_tabs.widget(1)
        self.assertIsInstance(z2_tab, MultiRunViewWidget)
        self.assertEqual(z2_tab.count(), 4)
        self.assertIn("Ejecución 1", z2_tab.tabText(1))
        self.assertIn("Ejecución 2", z2_tab.tabText(2))

        # Scoped cells check: Polígono Norte assignment table should only list C2
        z2_run2 = z2_tab.widget(2)
        z2_run2.summary_table.selectRow(1)
        self.assertEqual(z2_run2.assign_table.rowCount(), 1)
        self.assertEqual(z2_run2.assign_table.item(0, 0).text(), "C2")

        # Check sub-tabs inside Red Global Consolidada (Tab 2)
        glob_tab = widget.results_tabs.widget(2)
        self.assertIsInstance(glob_tab, MultiRunViewWidget)
        self.assertEqual(glob_tab.count(), 4)
        self.assertIn("Mejores de Cada Ejecución", glob_tab.tabText(0))
        self.assertIn("Ejecución 1", glob_tab.tabText(1))
        self.assertIn("Ejecución 2", glob_tab.tabText(2))
        self.assertIn("Curvas de Convergencia", glob_tab.tabText(3))

        # Check assignment changes detection: C1 changed from 10 -> 24
        run2_widget = glob_tab.widget(2)
        # Row 0 is baseline plan ("— (Línea Base)"). Row 1 is the calculated plan:
        run2_widget.summary_table.selectRow(1)
        table = run2_widget.assign_table
        # Column 5 is "Cambio"
        status_item = table.item(0, 5)
        self.assertIsNotNone(status_item)
        self.assertEqual(status_item.text(), "✓ Cambiado")

        widget.close()


if __name__ == "__main__":
    unittest.main()


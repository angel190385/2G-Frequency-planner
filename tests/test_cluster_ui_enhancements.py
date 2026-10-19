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
            current_gen=2,
            total_gens=10,
            best_score=120.0
        )
        dlg.update_progress(p2)
        self.assertEqual(dlg._zone_histories[1][0], [1, 2])
        self.assertEqual(dlg._zone_histories[1][1], [150.0, 120.0])

        # Run 2 of Zone 1
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

        # Test exact scenario from user request: Zone 1 of 2, Run 3 of 3, Gen 44 of 500
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
        # bar_current should strictly show generation 44 of 500 (8.8% -> 8% or 9%)
        self.assertIn("44 / 500 Generaciones", dlg.bar_current.text())
        self.assertEqual(dlg.bar_current.value(), int(44 / 500 * 100))

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


if __name__ == "__main__":
    unittest.main()

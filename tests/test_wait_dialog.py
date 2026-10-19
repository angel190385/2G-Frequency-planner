"""
Unit tests for BusyWaitDialog and import progress callbacks.
"""
import unittest
import tempfile
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from src.ui.dialogs.wait_dialog import BusyWaitDialog
from src.io.data_loader import load_cells, load_neighbors, load_handovers
from src.io.planner_importer import load_planner_global_cells
from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph


class TestBusyWaitDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure QApplication is initialized for GUI tests
        cls.app = QApplication.instance() or QApplication([])

    def test_wait_dialog_cursor_and_lifecycle(self):
        """Test that BusyWaitDialog sets WaitCursor and restores normal cursor on exit."""
        self.assertIsNone(QApplication.overrideCursor())

        with BusyWaitDialog("Operación", "Procesando datos...") as wait:
            self.assertIsNotNone(QApplication.overrideCursor())
            self.assertEqual(QApplication.overrideCursor().shape(), Qt.CursorShape.WaitCursor)

            # Test updating message
            wait.set_text("Paso 2...")
            self.assertEqual(wait.dialog.labelText(), "Paso 2...")

        # Must be restored after exiting context manager
        self.assertIsNone(QApplication.overrideCursor())

    def test_wait_dialog_restores_cursor_on_exception(self):
        """Test that BusyWaitDialog restores cursor even when an exception is raised."""
        try:
            with BusyWaitDialog("Operación Fallida", "Iniciando..."):
                self.assertIsNotNone(QApplication.overrideCursor())
                raise ValueError("Simulated failure")
        except ValueError:
            pass

        self.assertIsNone(QApplication.overrideCursor())

    def test_load_cells_progress_callback(self):
        """Test that load_cells calls progress_callback."""
        csv_content = (
            "station_id;station_name;lat;lon;CellName;sector;azimuth;bcch;tch1\n" +
            "\n".join([f"S{i};Site{i};40.0;-3.0;C{i};A;0;100;102" for i in range(1, 1005)])
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write(csv_content)
            temp_path = f.name

        progress_calls = []
        try:
            cells, stations, warnings = load_cells(
                temp_path,
                progress_callback=lambda r, c: progress_calls.append((r, c))
            )
            self.assertGreater(len(progress_calls), 0)
            self.assertEqual(len(cells), 1004)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_planner_global_cells_progress_callback(self):
        """Test that load_planner_global_cells calls progress_callback."""
        header = "Cellid;MNC;Tech;Band;Status;SiteId;SiteName;WGS84_LAT_DEC;WGS84_LON_DEC;CellName;BCCH;TCHs\n"
        rows = [f"{i};1;2G;900;In Service;S{i};Site{i};40.0;-3.0;CELL{i}1;100;102" for i in range(1, 2005)]
        csv_content = header + "\n".join(rows)

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write(csv_content)
            temp_path = f.name

        progress_calls = []
        try:
            cells, stations, stats = load_planner_global_cells(
                temp_path,
                progress_callback=lambda tot, acc: progress_calls.append((tot, acc))
            )
            self.assertGreater(len(progress_calls), 0)
            self.assertEqual(len(cells), 2004)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()

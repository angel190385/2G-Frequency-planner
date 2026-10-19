"""
Unit tests for t_Planner export loading and filtering.
"""
import unittest
import tempfile
import os

from src.io.planner_importer import load_planner_global_cells, load_planner_sectors


class TestPlannerImporter(unittest.TestCase):
    def setUp(self):
        self.cells_csv = (
            "MNC;Tech;Band;Status;SiteId;SiteName;WGS84_LAT_DEC;WGS84_LON_DEC;CellName;BCCH;TCHs\n"
            "1;2G;900;In Service;SITE001;Estacion Centro;40,4168;-3,7038;SITE0011;102;104,106,108\n"
            "1;2G;900;In Service;SITE001;Estacion Centro;40.4168;-3.7038;SITE0012;103;105,107,109\n"
            "1;2G;1800;In Service;SITE001;Estacion Centro;40.4168;-3.7038;SITE0013;600;602,604\n"
            "2;2G;900;In Service;SITE002;Otro Operador;40.4200;-3.7100;SITE0021;110;112,114\n"
            "1;4G;900;In Service;SITE003;Estacion 4G;40.4300;-3.7200;SITE0031;;116\n"
            "1;2G;900;Planned;SITE004;Planificada;40.4400;-3.7300;SITE0041;120;122\n"
        )
        self.sectors_csv = (
            "SiteId;Sector;CellName;SectorAzimuth\n"
            "SITE001;1;SITE0011;55.0\n"
            "SITE001;2;SITE0012;175.0\n"
        )

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write(self.cells_csv)
            self.c_file = f.name

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write(self.sectors_csv)
            self.s_file = f.name

    def tearDown(self):
        if os.path.exists(self.c_file):
            os.remove(self.c_file)
        if os.path.exists(self.s_file):
            os.remove(self.s_file)

    def test_filter_and_import(self):
        cells, stations, stats = load_planner_global_cells(self.c_file, self.s_file)

        # Only 2 rows meet all filters: MNC=1, Tech=2G, Band=900, Status=In Service
        self.assertEqual(len(cells), 2)
        self.assertEqual(stats["filtered_band"], 1)   # 1800 band
        self.assertEqual(stats["filtered_mnc"], 1)    # MNC=2
        self.assertEqual(stats["filtered_tech"], 1)   # 4G
        self.assertEqual(stats["filtered_status"], 1) # Planned

        c1 = cells["SITE0011"]
        self.assertEqual(c1.station.station_id, "SITE001")
        self.assertEqual(c1.sector, "1")
        self.assertEqual(c1.azimuth, 55.0)
        self.assertEqual(c1.bcch, 102)
        self.assertEqual(c1.tch_list, [104, 106, 108])
        self.assertAlmostEqual(c1.station.latitude, 40.4168)
        self.assertAlmostEqual(c1.station.longitude, -3.7038)

        c2 = cells["SITE0012"]
        self.assertEqual(c2.sector, "2")
        self.assertEqual(c2.azimuth, 175.0)
        self.assertEqual(c2.bcch, 103)
        self.assertEqual(c2.tch_list, [105, 107, 109])

    def test_planner_import_prioritizes_cellname_over_cellid(self):
        """
        Verify that when planner export contains BOTH 'Cellid' and 'CellName',
        the importer strictly picks 'CellName' and does NOT pick 'Cellid'.
        """
        csv_content = (
            "Cellid;MNC;Tech;Band;Status;SiteId;SiteName;WGS84_LAT_DEC;WGS84_LON_DEC;CellName;BCCH;TCHs\n"
            "999888;1;2G;900;In Service;SITE009;Estacion Test;40.4168;-3.7038;MAD0091;102;104,106\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            cells, stations, stats = load_planner_global_cells(temp_path)
            self.assertEqual(len(cells), 1)
            # Must be keyed by CellName ('MAD0091'), NOT by numeric Cellid ('999888')
            self.assertIn("MAD0091", cells)
            self.assertNotIn("999888", cells)
            self.assertEqual(cells["MAD0091"].cell_id, "MAD0091")
            self.assertEqual(cells["MAD0091"].sector, "1")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_data_loader_supports_cellname_header(self):
        """
        Verify that load_cells correctly recognizes the 'CellName' header
        and does not treat 'CellName' as a TCH column.
        """
        from src.io.data_loader import load_cells

        csv_content = (
            "station_id;station_name;lat;lon;CellName;sector;azimuth;bcch;tch1;tch2\n"
            "EST01;Estacion;40.4168;-3.7038;EST01A;A;0;100;102;104\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            cells, stations, warnings = load_cells(temp_path)
            self.assertIn("EST01A", cells)
            c = cells["EST01A"]
            self.assertEqual(c.cell_id, "EST01A")
            self.assertEqual(c.bcch, 100)
            self.assertEqual(c.tch_list, [102, 104])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()

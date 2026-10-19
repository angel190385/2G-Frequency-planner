"""
Dialog for importing t_Planner_Global_Cells.csv and t_Planner_Global_Sectors.csv.
"""
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QGroupBox, QTextEdit, QMessageBox
)
from PyQt6.QtCore import Qt

from src.io.planner_importer import load_planner_global_cells
from src.models.cell import Cell, Station
from src.ui.styles import Colors


class PlannerImportDialog(QDialog):
    """Dialog for importing global planner network files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells_file = ""
        self.sectors_file = ""
        self.imported_cells: dict[str, Cell] = {}
        self.imported_stations: dict[str, Station] = {}
        self.import_stats: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Importar Red Global t_Planner (Cells + Sectors)")
        self.setMinimumSize(600, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Filters description
        info_box = QGroupBox("Criterios de Filtrado y Mapeo Automático")
        info_layout = QVBoxLayout(info_box)

        info_text = (
            "Esta importación procesa la red completa de t_Planner aplicando los siguientes filtros:\n\n"
            "  • MNC = 1 (o 01)\n"
            "  • Tech = 2G (GSM)\n"
            "  • Band = 900 MHz\n"
            "  • Status = In Service\n\n"
            "Mapeo de atributos:\n"
            "  • SiteId → station_id, SiteName → station_name\n"
            "  • WGS84_LAT_DEC, WGS84_LON_DEC → Coordenadas\n"
            "  • CellName → Nombre de la celda (se prioriza siempre CellName y se ignora Cellid)\n"
            "  • Último dígito numérico de CellName → sector\n"
            "  • BCCH → bcch, TCHs (separados por coma) → tch1, tch2...\n"
            "  • SectorAzimuth (primer match de sectors) → azimut de antena"
        )
        lbl_info = QLabel(info_text)
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet("color: #b0b0c0; font-size: 11px;")
        info_layout.addWidget(lbl_info)
        layout.addWidget(info_box)

        # File selection 1: Cells
        box_cells = QGroupBox("1. Archivo de Celdas (t_Planner_Global_Cells.csv)")
        l_cells = QHBoxLayout(box_cells)
        self.lbl_cells_path = QLabel("Ningún archivo seleccionado")
        self.lbl_cells_path.setStyleSheet("color: #8888a0; font-style: italic;")
        l_cells.addWidget(self.lbl_cells_path, 1)

        btn_browse_cells = QPushButton("📁 Seleccionar Celdas...")
        btn_browse_cells.clicked.connect(self._browse_cells)
        l_cells.addWidget(btn_browse_cells)
        layout.addWidget(box_cells)

        # File selection 2: Sectors
        box_sectors = QGroupBox("2. Archivo de Sectores / Azimuts (t_Planner_Global_Sectors.csv)")
        l_sectors = QHBoxLayout(box_sectors)
        self.lbl_sectors_path = QLabel("Ningún archivo seleccionado (Opcional)")
        self.lbl_sectors_path.setStyleSheet("color: #8888a0; font-style: italic;")
        l_sectors.addWidget(self.lbl_sectors_path, 1)

        btn_browse_sectors = QPushButton("📁 Seleccionar Sectores...")
        btn_browse_sectors.clicked.connect(self._browse_sectors)
        l_sectors.addWidget(btn_browse_sectors)
        layout.addWidget(box_sectors)

        # Summary box
        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setPlaceholderText("El resumen de importación aparecerá aquí tras seleccionar y procesar los archivos...")
        self.txt_summary.setMaximumHeight(130)
        layout.addWidget(self.txt_summary)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        self.btn_import = QPushButton("Importar y Filtrar")
        self.btn_import.setObjectName("btn_primary")
        self.btn_import.setEnabled(False)
        self.btn_import.clicked.connect(self._do_import)
        btn_layout.addWidget(self.btn_import)

        layout.addLayout(btn_layout)

    def _browse_cells(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar t_Planner_Global_Cells.csv",
            "", "Archivos CSV (*.csv *.txt);;Todos (*.*)"
        )
        if file_path:
            self.cells_file = file_path
            self.lbl_cells_path.setText(os.path.basename(file_path))
            self.lbl_cells_path.setStyleSheet("color: #00d4aa; font-weight: bold;")
            self.btn_import.setEnabled(True)

    def _browse_sectors(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar t_Planner_Global_Sectors.csv",
            "", "Archivos CSV (*.csv *.txt);;Todos (*.*)"
        )
        if file_path:
            self.sectors_file = file_path
            self.lbl_sectors_path.setText(os.path.basename(file_path))
            self.lbl_sectors_path.setStyleSheet("color: #00d4aa; font-weight: bold;")

    def _do_import(self):
        if not self.cells_file:
            return

        try:
            from src.ui.dialogs.wait_dialog import BusyWaitDialog
            with BusyWaitDialog("Importando t_Planner", "Leyendo archivos y azimuts de sectores...", self) as wait:
                def on_progress(total, accepted):
                    wait.set_text(f"Procesando celdas ({total} filas analizadas, {accepted} aceptadas)...")

                cells, stations, stats = load_planner_global_cells(
                    self.cells_file,
                    self.sectors_file if self.sectors_file else None,
                    progress_callback=on_progress
                )

            self.imported_cells = cells
            self.imported_stations = stations
            self.import_stats = stats

            summary_msg = (
                f"✅ Importación completada con éxito:\n\n"
                f"  • Filas procesadas en Cells: {stats['total_rows']}\n"
                f"  • Celdas descartadas por MNC != 1: {stats['filtered_mnc']}\n"
                f"  • Celdas descartadas por Tech != 2G: {stats['filtered_tech']}\n"
                f"  • Celdas descartadas por Band != 900: {stats['filtered_band']}\n"
                f"  • Celdas descartadas por Status != In Service: {stats['filtered_status']}\n\n"
                f"  🎯 Total Celdas 2G 900 Aceptadas: {stats['accepted']}\n"
                f"  📍 Total Emplazamientos Únicos: {stats['stations_count']}"
            )
            self.txt_summary.setPlainText(summary_msg)

            if stats['accepted'] == 0:
                QMessageBox.warning(
                    self, "Sin celdas importadas",
                    "No se encontraron celdas que cumplan los filtros (MNC=1, Tech=2G, Band=900, Status=In Service)."
                )
                return

            QMessageBox.information(
                self, "Importación Finalizada",
                f"Se han cargado {stats['accepted']} celdas en {stats['stations_count']} estaciones."
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self, "Error de Importación",
                f"Ocurrió un error al procesar los archivos:\n{str(e)}"
            )

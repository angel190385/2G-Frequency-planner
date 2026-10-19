"""
Import dialog for loading data files.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QGroupBox, QTextEdit
)
from PyQt6.QtCore import Qt


class ImportDialog(QDialog):
    """Dialog for importing cells, neighbors, and handover data."""

    def __init__(self, parent=None, import_type="cells"):
        super().__init__(parent)
        self.import_type = import_type
        self.selected_file = None
        self._setup_ui()

    def _setup_ui(self):
        titles = {
            "cells": "Importar Celdas (cells.csv / cells_extended.csv)",
            "neighbors": "Importar Vecindades",
            "handovers": "Importar Handovers",
        }
        self.setWindowTitle(titles.get(self.import_type, "Importar"))
        self.setMinimumSize(500, 360)

        layout = QVBoxLayout(self)

        # Format info
        info_box = QGroupBox("Formato esperado")
        info_layout = QVBoxLayout(info_box)

        formats = {
            "cells": (
                "Archivo CSV o Excel con las siguientes columnas:\n\n"
                "  station_id   - Identificador de la estación\n"
                "  station_name - Nombre de la estación\n"
                "  lat          - Latitud\n"
                "  lon          - Longitud\n"
                "  cell_id      - Identificador de la celda\n"
                "  sector       - Sector (A, B, C...)\n"
                "  azimuth      - Azimut en grados (0..360)\n"
                "  bcch         - Frecuencia BCCH\n"
                "  tch1, tch2...- Frecuencias TCH\n\n"
                "Compatible con cells.csv y cells_extended.csv.\n"
                "Separadores: punto y coma (;) o coma (,)."
            ),
            "neighbors": (
                "Archivo CSV o Excel con las siguientes columnas:\n\n"
                "  cell_a  - Cell ID de la primera celda\n"
                "  cell_b  - Cell ID de la segunda celda\n\n"
                "Las distancias se calculan automáticamente\n"
                "a partir de las coordenadas de las estaciones."
            ),
            "handovers": (
                "Archivo CSV o Excel con las siguientes columnas:\n\n"
                "  cell_a    - Cell ID de la primera celda\n"
                "  cell_b    - Cell ID de la segunda celda\n"
                "  ho_count  - Número de handovers\n\n"
                "Solo se actualizan vecindades ya definidas.\n"
                "⚠ Requiere cargar vecindades previamente."
            ),
        }

        info_text = QTextEdit()
        info_text.setPlainText(formats.get(self.import_type, ""))
        info_text.setReadOnly(True)
        info_text.setMaximumHeight(180)
        info_layout.addWidget(info_text)
        layout.addWidget(info_box)

        # File selection
        file_layout = QHBoxLayout()
        self.lbl_file = QLabel("Ningún archivo seleccionado")
        self.lbl_file.setStyleSheet("padding: 8px;")
        file_layout.addWidget(self.lbl_file, 1)

        btn_browse = QPushButton("📁 Seleccionar archivo")
        btn_browse.clicked.connect(self._browse_file)
        file_layout.addWidget(btn_browse)
        layout.addLayout(file_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        self.btn_import = QPushButton("Importar")
        self.btn_import.setObjectName("btn_primary")
        self.btn_import.setEnabled(False)
        self.btn_import.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_import)

        layout.addLayout(btn_layout)

    def _browse_file(self):
        """Open file browser."""
        file_filter = "Archivos de datos (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls);;Todos (*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar archivo", "", file_filter
        )

        if file_path:
            self.selected_file = file_path
            self.lbl_file.setText(file_path)
            self.btn_import.setEnabled(True)

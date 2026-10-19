"""
Dialog for configuring a specific OptimizationZone.
Allows editing zone name, frequency recalculation options,
zone-specific neighbors/handovers, and algorithm rules.
"""
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QGroupBox, QCheckBox, QSpinBox, QLineEdit,
    QMessageBox, QGridLayout
)
from PyQt6.QtCore import Qt

from src.models.zone import OptimizationZone
from src.ui.dialogs.rules_dialog import RulesDialog
from src.io.data_loader import load_neighbors, load_handovers
from src.models.neighbor import NeighborGraph
from src.ui.styles import Colors


class ZoneConfigDialog(QDialog):
    """Dialog for editing an OptimizationZone's settings."""

    def __init__(self, zone: OptimizationZone, parent=None):
        super().__init__(parent)
        self.zone = zone
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(f"Configuración de {self.zone.name}")
        self.setMinimumSize(480, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Zone info
        info_group = QGroupBox("Identificación de la Zona")
        ig_layout = QGridLayout(info_group)

        ig_layout.addWidget(QLabel("Nombre:"), 0, 0)
        self.txt_name = QLineEdit(self.zone.name)
        ig_layout.addWidget(self.txt_name, 0, 1)

        ig_layout.addWidget(QLabel("Celdas Asignadas:"), 1, 0)
        lbl_count = QLabel(f"<b>{self.zone.cell_count} celdas</b>")
        lbl_count.setStyleSheet("color: #00d4aa; font-size: 13px;")
        ig_layout.addWidget(lbl_count, 1, 1)

        layout.addWidget(info_group)

        # Frequencies to recalculate
        freq_group = QGroupBox("Frecuencias a Recalcular en esta Zona")
        fg_layout = QVBoxLayout(freq_group)

        self.chk_bcch = QCheckBox("Recalcular canales BCCH")
        self.chk_bcch.setChecked(self.zone.recalculate_bcch)
        fg_layout.addWidget(self.chk_bcch)

        self.chk_tch = QCheckBox("Recalcular canales TCH existentes")
        self.chk_tch.setChecked(self.zone.recalculate_tch)
        fg_layout.addWidget(self.chk_tch)

        new_tch_layout = QHBoxLayout()
        new_tch_layout.addWidget(QLabel("Añadir nuevos canales TCH por celda:"))
        self.spin_new_tch = QSpinBox()
        self.spin_new_tch.setRange(0, 8)
        self.spin_new_tch.setValue(self.zone.new_tch_count)
        new_tch_layout.addWidget(self.spin_new_tch)
        new_tch_layout.addStretch()
        fg_layout.addLayout(new_tch_layout)

        layout.addWidget(freq_group)

        # Zone-specific Neighbors & Handovers
        nh_group = QGroupBox("Vecindades y Traspasos de esta Zona (Opcional)")
        nh_layout = QVBoxLayout(nh_group)

        lbl_nh_info = QLabel(
            "Si dispones de la matriz de adyacencias o contadores HO para esta zona, cárgalos aquí.\n"
            "Si no los cargas, la zona utilizará la penalización espacial de distancia para protegerse."
        )
        lbl_nh_info.setStyleSheet("color: #8888a0; font-size: 11px;")
        lbl_nh_info.setWordWrap(True)
        nh_layout.addWidget(lbl_nh_info)

        btn_nh_layout = QHBoxLayout()
        self.lbl_nh_status = QLabel("Sin vecindades específicas")
        if self.zone.neighbor_graph:
            self.lbl_nh_status.setText(f"✅ {len(self.zone.neighbor_graph.all_relations())} relaciones cargadas")
            self.lbl_nh_status.setStyleSheet("color: #00d4aa; font-weight: bold;")
        else:
            self.lbl_nh_status.setStyleSheet("color: #8888a0; font-style: italic;")
        btn_nh_layout.addWidget(self.lbl_nh_status, 1)

        btn_load_nh = QPushButton("📁 Cargar Vecinas...")
        btn_load_nh.clicked.connect(self._load_zone_neighbors)
        btn_nh_layout.addWidget(btn_load_nh)

        btn_load_ho = QPushButton("📡 Cargar HO...")
        btn_load_ho.clicked.connect(self._load_zone_handovers)
        btn_nh_layout.addWidget(btn_load_ho)

        nh_layout.addLayout(btn_nh_layout)
        layout.addWidget(nh_group)

        # Algorithm rules for this zone
        rules_group = QGroupBox("Parámetros del Algoritmo Genético")
        rg_layout = QHBoxLayout(rules_group)

        lbl_rules_info = QLabel(
            f"Población: {self.zone.rules.population_size} | "
            f"Generaciones: {self.zone.rules.num_generations} | "
            f"Banda: {self.zone.rules.freq_range_start}..{self.zone.rules.freq_range_end}"
        )
        rg_layout.addWidget(lbl_rules_info, 1)

        btn_edit_rules = QPushButton("⚙️ Configurar Reglas...")
        btn_edit_rules.clicked.connect(self._edit_zone_rules)
        rg_layout.addWidget(btn_edit_rules)

        layout.addWidget(rules_group)

        # Dialog buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = QPushButton("Guardar Cambios")
        btn_save.setObjectName("btn_primary")
        btn_save.clicked.connect(self._save_changes)
        btn_box.addWidget(btn_save)

        layout.addLayout(btn_box)

    def _edit_zone_rules(self):
        """Open RulesDialog with this zone's rules."""
        dlg = RulesDialog(self.zone.rules, self)
        if dlg.exec():
            self.zone.rules = dlg.get_rules()

    def _load_zone_neighbors(self):
        """Load neighbor file specifically for this zone."""
        path, _ = QFileDialog.getOpenFileName(
            self, f"Cargar Vecinas para {self.zone.name}",
            "", "Archivos CSV (*.csv *.txt);;Todos (*.*)"
        )
        if path:
            try:
                relations, _ = load_neighbors(path)
                ng = NeighborGraph()
                for r in relations:
                    ng.add_relation(r)
                self.zone.neighbor_graph = ng
                self.zone.neighbors_file_path = path
                self.lbl_nh_status.setText(f"✅ {len(relations)} relaciones cargadas")
                self.lbl_nh_status.setStyleSheet("color: #00d4aa; font-weight: bold;")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error al cargar vecinas:\n{str(e)}")

    def _load_zone_handovers(self):
        """Load handovers file specifically for this zone."""
        if not self.zone.neighbor_graph:
            QMessageBox.warning(self, "Atención", "Debes cargar primero las vecindades de la zona antes de los handovers.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, f"Cargar Handovers para {self.zone.name}",
            "", "Archivos CSV (*.csv *.txt);;Todos (*.*)"
        )
        if path:
            try:
                ho_data, _ = load_handovers(path)
                matched = self.zone.neighbor_graph.update_from_handover_data(ho_data)
                self.zone.handovers_file_path = path
                QMessageBox.information(self, "Handovers", f"Se han actualizado {matched} relaciones con datos de traspaso.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error al cargar handovers:\n{str(e)}")

    def _save_changes(self):
        name = self.txt_name.text().strip()
        if name:
            self.zone.name = name
        self.zone.recalculate_bcch = self.chk_bcch.isChecked()
        self.zone.recalculate_tch = self.chk_tch.isChecked()
        self.zone.new_tch_count = self.spin_new_tch.value()
        self.accept()

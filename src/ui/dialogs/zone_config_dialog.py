"""
Dialog for configuring a specific OptimizationZone.
Allows editing zone name, frequency recalculation options,
zone-specific neighbors/handovers, and algorithm rules.
"""
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QSpinBox, QLineEdit,
    QMessageBox, QGridLayout
)
from PyQt6.QtCore import Qt

from src.models.zone import OptimizationZone
from src.ui.dialogs.rules_dialog import RulesDialog
from src.ui.styles import Colors


class ZoneConfigDialog(QDialog):
    """Dialog for editing an OptimizationZone's settings."""

    def __init__(self, zone: OptimizationZone, parent=None):
        super().__init__(parent)
        self.zone = zone
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(f"Configuración de {self.zone.name}")
        self.setMinimumSize(460, 290)

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

        self.chk_bcch = QCheckBox("Forzar recálculo de todos los canales BCCH")
        self.chk_bcch.setToolTip("Si está desmarcado, solo se recalculan los canales BCCH fuera del rango permitido.")
        self.chk_bcch.setChecked(self.zone.recalculate_bcch)
        fg_layout.addWidget(self.chk_bcch)

        self.chk_tch = QCheckBox("Forzar recálculo de todos los canales TCH existentes")
        self.chk_tch.setToolTip("Si está desmarcado, solo se recalculan los canales TCH fuera del rango permitido.")
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

    def _save_changes(self):
        name = self.txt_name.text().strip()
        if name:
            self.zone.name = name
        self.zone.recalculate_bcch = self.chk_bcch.isChecked()
        self.zone.recalculate_tch = self.chk_tch.isChecked()
        self.zone.new_tch_count = self.spin_new_tch.value()
        self.accept()

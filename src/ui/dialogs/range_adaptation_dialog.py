"""
Dialog for spectrum range transition and adaptation (Initial Range -> Final Range).

Allows selecting an initial and final frequency range, reviews disappearing frequencies,
identifies affected cells across the loaded plan, and provides actions to substitute
disappearing frequencies and improve the plan with new spectrum.
"""
from __future__ import annotations
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QSpinBox, QGridLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QRadioButton, QButtonGroup,
    QMessageBox, QSplitter, QWidget, QFrame, QScrollArea, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.engine.constraints import FrequencyRules
from src.engine.spectrum_manager import (
    detect_current_spectrum_range,
    analyze_spectrum_shift,
    apply_spectrum_shift_to_cells,
    greedy_substitute_disappeared_frequencies,
    SpectrumShiftAnalysis
)
from src.ui.styles import Colors


class RangeAdaptationDialog(QDialog):
    """
    Dialog for configuring initial and final frequency ranges,
    analyzing disappearing spectrum, and updating the loaded plan.
    """
    # Signal emitted when user requests direct optimization: (mode)
    optimize_requested = pyqtSignal(str)

    def __init__(
        self,
        cells: dict[str, Cell],
        neighbor_graph: NeighborGraph,
        rules: FrequencyRules,
        results: Optional[list] = None,
        parent=None
    ):
        super().__init__(parent)
        self.cells = cells
        self.neighbor_graph = neighbor_graph
        self.rules = rules
        self.results = results
        self.analysis: Optional[SpectrumShiftAnalysis] = None
        self.applied_mode: Optional[str] = None

        self._setup_ui()
        self._init_defaults()
        self._update_analysis()

    def _setup_ui(self):
        self.setWindowTitle("📻 Reconfiguración y Adaptación de Espectro (Rango Inicial → Final)")
        self.setMinimumSize(700, 450)
        self.resize(880, 640)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # Scroll area for smooth vertical scrolling across all resolutions
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(10)

        # Header description
        lbl_info = QLabel(
            "<b>Transición de Espectro:</b> Selecciona el rango inicial y el rango final (objetivo). "
            "El sistema identificará las frecuencias que desaparecen y las sustituirá en todo el plan "
            "cargado. Si se amplía el espectro, podrás aprovechar las nuevas frecuencias para mejorar "
            "la calidad global del plan."
        )
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet("color: #b8c4d0; padding: 4px; font-size: 13px;")
        layout.addWidget(lbl_info)

        # ── Group 1: Ranges Configuration ─────────────────────────
        ranges_group = QGroupBox("1. Definición de Rangos de Frecuencia (ARFCN)")
        rg_layout = QGridLayout(ranges_group)

        # Initial range
        lbl_init = QLabel("<b>Rango Inicial (Espectro actual):</b>")
        lbl_init.setStyleSheet("color: #ffa502;")
        rg_layout.addWidget(lbl_init, 0, 0, 1, 4)

        rg_layout.addWidget(QLabel("ARFCN Mín:"), 1, 0)
        self.spin_init_start = QSpinBox()
        self.spin_init_start.setRange(0, 1023)
        rg_layout.addWidget(self.spin_init_start, 1, 1)

        rg_layout.addWidget(QLabel("ARFCN Máx:"), 1, 2)
        self.spin_init_end = QSpinBox()
        self.spin_init_end.setRange(0, 1023)
        rg_layout.addWidget(self.spin_init_end, 1, 3)

        rg_layout.addWidget(QLabel("Excluidas:"), 1, 4)
        self.txt_init_excluded = QLineEdit()
        self.txt_init_excluded.setPlaceholderText("Ej: 105, 110")
        rg_layout.addWidget(self.txt_init_excluded, 1, 5)

        btn_detect = QPushButton("🔍 Detectar del Plan (BCCH + TCH)")
        btn_detect.setToolTip("Detectar rango mínimo y máximo usado en todos los canales (BCCH y TCH)")
        btn_detect.setStyleSheet("""
            QPushButton {
                background-color: #2c3e50;
                color: #00d4aa;
                font-weight: bold;
                border: 1px solid #00d4aa;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover { background-color: #00d4aa; color: #1e272e; }
        """)
        btn_detect.clicked.connect(self._detect_from_plan)
        rg_layout.addWidget(btn_detect, 1, 6)

        # Status feedback for detection
        self.lbl_detect_status = QLabel("")
        self.lbl_detect_status.setStyleSheet("color: #00d4aa; font-size: 11px; padding: 2px;")
        rg_layout.addWidget(self.lbl_detect_status, 2, 0, 1, 7)

        # Target range
        lbl_target = QLabel("<b>Rango Final (Nuevo espectro objetivo):</b>")
        lbl_target.setStyleSheet("color: #00d4aa;")
        rg_layout.addWidget(lbl_target, 3, 0, 1, 4)

        rg_layout.addWidget(QLabel("ARFCN Mín:"), 4, 0)
        self.spin_tgt_start = QSpinBox()
        self.spin_tgt_start.setRange(0, 1023)
        rg_layout.addWidget(self.spin_tgt_start, 4, 1)

        rg_layout.addWidget(QLabel("ARFCN Máx:"), 4, 2)
        self.spin_tgt_end = QSpinBox()
        self.spin_tgt_end.setRange(0, 1023)
        rg_layout.addWidget(self.spin_tgt_end, 4, 3)

        rg_layout.addWidget(QLabel("Excluidas:"), 4, 4)
        self.txt_tgt_excluded = QLineEdit()
        self.txt_tgt_excluded.setPlaceholderText("Ej: 105, 110")
        rg_layout.addWidget(self.txt_tgt_excluded, 4, 5)

        layout.addWidget(ranges_group)

        # Connect range changes to live analysis
        for spin in (self.spin_init_start, self.spin_init_end, self.spin_tgt_start, self.spin_tgt_end):
            spin.valueChanged.connect(self._update_analysis)
        self.txt_init_excluded.textChanged.connect(self._update_analysis)
        self.txt_tgt_excluded.textChanged.connect(self._update_analysis)

        # ── Group 2: Differential Analysis Summary ────────────────
        analysis_group = QGroupBox("2. Análisis Diferencial de Espectro")
        ag_layout = QVBoxLayout(analysis_group)

        self.summary_cards_layout = QHBoxLayout()

        self.card_disappearing = self._create_metric_card("🔴 Desaparecen", "0 freqs", "#ff4757")
        self.card_new = self._create_metric_card("🟢 Nuevo Espectro", "0 freqs", "#2ed573")
        self.card_retained = self._create_metric_card("⚪ Conservadas", "0 freqs", "#70a1ff")
        self.card_affected = self._create_metric_card("⚠️ Celdas Afectadas", "0 celdas", "#ffa502")

        self.summary_cards_layout.addWidget(self.card_disappearing)
        self.summary_cards_layout.addWidget(self.card_new)
        self.summary_cards_layout.addWidget(self.card_retained)
        self.summary_cards_layout.addWidget(self.card_affected)
        ag_layout.addLayout(self.summary_cards_layout)

        self.lbl_analysis_details = QLabel()
        self.lbl_analysis_details.setWordWrap(True)
        self.lbl_analysis_details.setStyleSheet("color: #dcdde1; padding: 4px; font-size: 12px;")
        ag_layout.addWidget(self.lbl_analysis_details)

        layout.addWidget(analysis_group)

        # ── Group 3: Affected Cells Table ──────────────────────────
        table_group = QGroupBox("3. Detalle de Canales que Desaparecen en Celdas Cargadas")
        tg_layout = QVBoxLayout(table_group)

        self.affected_table = QTableWidget()
        self.affected_table.setAlternatingRowColors(True)
        self.affected_table.horizontalHeader().setStretchLastSection(True)
        self.affected_table.verticalHeader().setVisible(False)
        columns = ["Cell ID", "Estación", "Sector", "Tipo de Canal", "Frecuencia Actual (Eliminada)", "Acción Propuesta"]
        self.affected_table.setColumnCount(len(columns))
        self.affected_table.setHorizontalHeaderLabels(columns)
        self.affected_table.setMinimumHeight(160)
        self.affected_table.verticalHeader().setDefaultSectionSize(26)
        tg_layout.addWidget(self.affected_table)

        layout.addWidget(table_group)

        # ── Group 4: Strategy Selection ───────────────────────────
        strat_group = QGroupBox("4. Estrategia de Aplicación")
        strat_layout = QVBoxLayout(strat_group)

        self.rad_disappeared_only = QRadioButton(
            "🔹 Sustituir solo frecuencias desaparecidas (reasignar únicamente canales retirados)"
        )
        self.rad_improve_with_new = QRadioButton(
            "✨ Sustituir y mejorar plan globalmente con nuevo espectro (Recomendado: aprovecha nuevo espectro para desatascar vecindades)"
        )
        self.rad_improve_with_new.setChecked(True)

        strat_layout.addWidget(self.rad_improve_with_new)
        strat_layout.addWidget(self.rad_disappeared_only)

        # Selector to review/recalculate all TCHs if any channel (BCCH or TCH) changes
        self.chk_include_all_tchs = QCheckBox(
            "🔄 Si se modifica cualquier canal (BCCH o TCH) en una celda, revisar todos sus canales TCH"
        )
        self.chk_include_all_tchs.setChecked(True)
        self.chk_include_all_tchs.setStyleSheet(
            "QCheckBox { color: #00d4aa; font-weight: bold; font-size: 12px; margin-top: 6px; }"
        )
        self.chk_include_all_tchs.setToolTip(
            "Al marcar esta opción, si una celda tiene cualquier afectación (ya sea de BCCH o de TCH), "
            "se revisarán y optimizarán todos los canales TCH de dicha celda para conseguir una reasignación óptima."
        )
        self.chk_include_all_tchs.stateChanged.connect(self._update_analysis)

        lbl_tch_desc = QLabel(
            "💡 <i>Si se modifica cualquier canal (BCCH o TCH) en una celda, "
            "revisar también todos los TCHs de esa celda no añade coste operativo y desatasca la vecindad.</i>"
        )
        lbl_tch_desc.setWordWrap(True)
        lbl_tch_desc.setStyleSheet("color: #a0a0b0; font-size: 11px; margin-left: 20px; margin-bottom: 4px;")

        strat_layout.addWidget(self.chk_include_all_tchs)
        strat_layout.addWidget(lbl_tch_desc)
        layout.addWidget(strat_group)

        # ── Buttons ───────────────────────────────────────────────
        btn_layout = QHBoxLayout()

        self.btn_greedy = QPushButton("⚡ Sustitución Rápida Directa")
        self.btn_greedy.setToolTip(
            "Asigna de inmediato frecuencias del nuevo espectro a las celdas afectadas con menor interferencia local"
        )
        self.btn_greedy.clicked.connect(self._on_greedy_substitute)
        btn_layout.addWidget(self.btn_greedy)

        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        self.btn_apply = QPushButton("⚙️ Aplicar al Cálculo")
        self.btn_apply.setToolTip("Configura el nuevo rango y marca las celdas para recálculo en la pestaña de Cálculo")
        self.btn_apply.clicked.connect(self._on_apply_to_calc)
        btn_layout.addWidget(self.btn_apply)

        self.btn_optimize_now = QPushButton("🚀 Aplicar y Optimizar Ahora")
        self.btn_optimize_now.setObjectName("btn_primary")
        self.btn_optimize_now.setStyleSheet("""
            QPushButton {
                background-color: #00b894;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #00d4aa; }
        """)
        self.btn_optimize_now.clicked.connect(self._on_apply_and_optimize)
        btn_layout.addWidget(self.btn_optimize_now)

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, 1)
        main_layout.addLayout(btn_layout)

    def _create_metric_card(self, title: str, default_val: str, color_hex: str) -> QFrame:
        """Create a styled metric card."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #16162a;
                border: 1px solid {color_hex};
                border-radius: 6px;
                padding: 6px;
            }}
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: #a0a0b0; font-size: 11px; font-weight: bold;")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_val = QLabel(default_val)
        lbl_val.setStyleSheet(f"color: {color_hex}; font-size: 15px; font-weight: bold;")
        lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)

        c_layout.addWidget(lbl_title)
        c_layout.addWidget(lbl_val)
        card.lbl_val = lbl_val  # attach reference
        return card

    def _init_defaults(self):
        """Initialize spinboxes with current rules and plan."""
        det_min, det_max, _ = detect_current_spectrum_range(self.cells, self.results)
        self.spin_init_start.blockSignals(True)
        self.spin_init_end.blockSignals(True)

        self.spin_init_start.setValue(det_min if det_min > 0 else self.rules.freq_range_start)
        self.spin_init_end.setValue(det_max if det_max > 0 else self.rules.freq_range_end)

        self.spin_init_start.blockSignals(False)
        self.spin_init_end.blockSignals(False)

        # Target range defaults to rules
        self.spin_tgt_start.setValue(self.rules.freq_range_start)
        self.spin_tgt_end.setValue(self.rules.freq_range_end)

        if self.rules.excluded_frequencies:
            excl_str = ", ".join(str(f) for f in self.rules.excluded_frequencies)
            self.txt_init_excluded.setText(excl_str)
            self.txt_tgt_excluded.setText(excl_str)

    def _detect_from_plan(self):
        """Auto-detect initial range across ALL channels (BCCH and all TCH frequencies)."""
        det_min, det_max, details = detect_current_spectrum_range(self.cells, self.results)

        self.spin_init_start.blockSignals(True)
        self.spin_init_end.blockSignals(True)

        self.spin_init_start.setValue(det_min)
        self.spin_init_end.setValue(det_max)

        self.spin_init_start.blockSignals(False)
        self.spin_init_end.blockSignals(False)

        self._update_analysis()

        # Update detailed feedback label
        b_min = details.get("bcch_min")
        b_max = details.get("bcch_max")
        t_min = details.get("tch_min")
        t_max = details.get("tch_max")
        n_bcch = details.get("total_bcch_channels", 0)
        n_tch = details.get("total_tch_channels", 0)
        n_all = details.get("total_unique_freqs", 0)

        bcch_str = f"{b_min}..{b_max}" if b_min is not None else "—"
        tch_str = f"{t_min}..{t_max}" if t_min is not None else "—"

        self.lbl_detect_status.setText(
            f"✅ Rango detectado (BCCH + TCH): ARFCN {det_min} a {det_max} | "
            f"BCCH ({n_bcch} celdas): {bcch_str} | TCH ({n_tch} canales): {tch_str} | "
            f"Total frecuencias únicas: {n_all}"
        )

    def _parse_excluded(self, text: str) -> list[int]:
        res = []
        for p in text.split(','):
            p = p.strip()
            if p:
                try:
                    res.append(int(p))
                except ValueError:
                    pass
        return res

    def _update_analysis(self):
        """Re-analyze difference between initial and target ranges."""
        init_start = self.spin_init_start.value()
        init_end = self.spin_init_end.value()
        init_excl = self._parse_excluded(self.txt_init_excluded.text())

        tgt_start = self.spin_tgt_start.value()
        tgt_end = self.spin_tgt_end.value()
        tgt_excl = self._parse_excluded(self.txt_tgt_excluded.text())

        if init_start > init_end:
            init_end = init_start
        if tgt_start > tgt_end:
            tgt_end = tgt_start

        self.analysis = analyze_spectrum_shift(
            cells=self.cells,
            initial_start=init_start,
            initial_end=init_end,
            initial_excluded=init_excl,
            target_start=tgt_start,
            target_end=tgt_end,
            target_excluded=tgt_excl
        )

        an = self.analysis

        # Update cards
        self.card_disappearing.lbl_val.setText(f"{len(an.disappearing_freqs)} freqs")
        self.card_new.lbl_val.setText(f"{len(an.new_freqs)} freqs")
        self.card_retained.lbl_val.setText(f"{len(an.retained_freqs)} freqs")
        self.card_affected.lbl_val.setText(f"{an.total_affected_cells_count} celdas")

        # Build descriptive details text
        details = []
        if an.disappearing_freqs:
            disp_str = ", ".join(str(f) for f in an.disappearing_freqs[:15])
            if len(an.disappearing_freqs) > 15:
                disp_str += f" ... (+{len(an.disappearing_freqs)-15} más)"
            details.append(f"<b>🔴 Frecuencias retiradas ({len(an.disappearing_freqs)}):</b> {disp_str}")
        else:
            details.append("<b>✅ Ninguna frecuencia desaparece del rango inicial.</b>")

        if an.new_freqs:
            new_str = ", ".join(str(f) for f in an.new_freqs[:15])
            if len(an.new_freqs) > 15:
                new_str += f" ... (+{len(an.new_freqs)-15} más)"
            details.append(f"<b>🟢 Nuevo espectro disponible ({len(an.new_freqs)}):</b> {new_str}")
        else:
            details.append("<b>ℹ️ Sin frecuencias adicionales en el nuevo rango.</b>")

        details.append(
            f"<b>Impacto en el plan:</b> {an.total_affected_bcch_count} canales BCCH y "
            f"{an.total_affected_tch_count} canales TCH deben sustituirse."
        )

        inc_all = getattr(self, "chk_include_all_tchs", None) and self.chk_include_all_tchs.isChecked()
        if inc_all:
            extra_tchs = 0
            for cid, info in an.affected_cells.items():
                if info["bcch_affected"] or info["tch_affected_indices"]:
                    cell = self.cells.get(cid)
                    if cell:
                        extra_tchs += max(0, len(cell.tch_list) - len(info["tch_affected_indices"]))
            if extra_tchs > 0:
                details.append(
                    f"<b>🔄 Revisión integral de TCH:</b> Se incluirán {extra_tchs} canales TCH en celdas afectadas "
                    f"(BCCH o TCH) para reoptimizar completamente sus portadoras de tráfico."
                )

        self.lbl_analysis_details.setText("<br>".join(details))

        # Refresh affected table
        self._refresh_affected_table()

    def _refresh_affected_table(self):
        """Populate table of channels that disappear."""
        if not self.analysis:
            self.affected_table.setRowCount(0)
            return

        an = self.analysis
        rows_data = []

        for cid, info in sorted(an.affected_cells.items()):
            cell = self.cells.get(cid)
            st_name = cell.station.name if cell else info["station_id"]
            sec = info["sector"]

            if info["bcch_affected"]:
                rows_data.append((
                    cid, st_name, sec, "BCCH",
                    str(info["bcch_val"]),
                    "Sustituir por frecuencia válida del nuevo espectro"
                ))

            for idx, f in zip(info["tch_affected_indices"], info["tch_affected_vals"]):
                rows_data.append((
                    cid, st_name, sec, f"TCH{idx+1}",
                    str(f),
                    "Sustituir por frecuencia válida del nuevo espectro"
                ))

        self.affected_table.setRowCount(len(rows_data))
        for row, data in enumerate(rows_data):
            for col, text in enumerate(data):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 3:  # channel type
                    item.setFont(QFont("Segoe UI", weight=QFont.Weight.Bold))
                    if "BCCH" in text:
                        item.setForeground(QBrush(QColor(Colors.DANGER)))
                    else:
                        item.setForeground(QBrush(QColor(Colors.WARNING)))
                elif col == 4:  # frequency
                    item.setForeground(QBrush(QColor(Colors.DANGER)))
                    item.setFont(QFont("Segoe UI", weight=QFont.Weight.Bold))
                elif col == 5:
                    item.setForeground(QBrush(QColor(Colors.SUCCESS)))

                self.affected_table.setItem(row, col, item)

        self.affected_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

    def _get_selected_mode(self) -> str:
        return "improve_with_new" if self.rad_improve_with_new.isChecked() else "disappeared_only"

    def _on_greedy_substitute(self):
        """Perform immediate heuristic greedy substitution on the loaded cells."""
        if not self.analysis:
            return

        an = self.analysis
        if not an.affected_cells:
            QMessageBox.information(
                self, "Sin frecuencias desaparecidas",
                "No hay frecuencias en el plan cargado que desaparezcan en el nuevo rango."
            )
            return

        confirm = QMessageBox.question(
            self, "Confirmar Sustitución Rápida",
            f"Se sustituirán inmediatamente {an.total_affected_bcch_count} canales BCCH y "
            f"{an.total_affected_tch_count} canales TCH en {an.total_affected_cells_count} celdas "
            f"usando el nuevo espectro.\n\n¿Deseas continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        subs = greedy_substitute_disappeared_frequencies(
            cells=self.cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            analysis=an
        )

        total_swapped = sum(len(items) for items in subs.values())
        QMessageBox.information(
            self, "Sustitución completada",
            f"✅ Se han sustituido {total_swapped} frecuencias en {len(subs)} celdas "
            f"dentro del rango {an.target_start}-{an.target_end}."
        )

        # Update initial range to target range and re-analyze
        self.spin_init_start.setValue(an.target_start)
        self.spin_init_end.setValue(an.target_end)
        self.txt_init_excluded.setText(self.txt_tgt_excluded.text())
        self._update_analysis()

    def _on_apply_to_calc(self):
        """Apply target range and cell flags to calculation setup and close."""
        if not self.analysis:
            return

        mode = self._get_selected_mode()
        include_all = getattr(self, "chk_include_all_tchs", None) and self.chk_include_all_tchs.isChecked()
        total_vars = apply_spectrum_shift_to_cells(
            cells=self.cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            analysis=self.analysis,
            mode=mode,
            include_all_cell_tchs=include_all
        )
        self.applied_mode = mode

        QMessageBox.information(
            self, "Configuración Aplicada",
            f"Rango de frecuencias actualizado a {self.analysis.target_start}-{self.analysis.target_end}.\n"
            f"Se han configurado {total_vars} variables para optimización en la pestaña Cálculo."
        )
        self.accept()

    def _on_apply_and_optimize(self):
        """Apply configuration and request immediate optimization."""
        if not self.analysis:
            return

        mode = self._get_selected_mode()
        include_all = getattr(self, "chk_include_all_tchs", None) and self.chk_include_all_tchs.isChecked()
        apply_spectrum_shift_to_cells(
            cells=self.cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            analysis=self.analysis,
            mode=mode,
            include_all_cell_tchs=include_all
        )
        self.applied_mode = mode
        self.optimize_requested.emit(mode)
        self.accept()

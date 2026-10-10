"""
Result comparison widget for displaying and comparing optimization results across runs.
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QAbstractItemView, QSplitter,
    QTreeWidget, QTreeWidgetItem, QGroupBox, QFrame, QTabWidget
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont

from src.models.cell import Cell
from src.models.frequency_plan import PlanResult, Violation
from src.ui.styles import Colors


class SingleRunResultWidget(QWidget):
    """View component for a single set of candidate plan results."""

    plan_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[PlanResult] = []
        self._cells: dict[str, Cell] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top: Summary table
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.summary_table = QTableWidget()
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.summary_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.currentCellChanged.connect(self._on_plan_selected)
        top_layout.addWidget(self.summary_table)

        splitter.addWidget(top_widget)

        # Bottom: Detailed view
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        detail_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Assignments table
        assign_widget = QWidget()
        assign_layout = QVBoxLayout(assign_widget)
        assign_layout.setContentsMargins(0, 0, 0, 0)

        assign_label = QLabel("Asignaciones: Antes → Después")
        assign_label.setObjectName("label_subtitle")
        assign_layout.addWidget(assign_label)

        self.assign_table = QTableWidget()
        self.assign_table.setAlternatingRowColors(True)
        self.assign_table.horizontalHeader().setStretchLastSection(True)
        self.assign_table.verticalHeader().setVisible(False)
        assign_layout.addWidget(self.assign_table)
        detail_splitter.addWidget(assign_widget)

        # Right: Violations tree
        viol_widget = QWidget()
        viol_layout = QVBoxLayout(viol_widget)
        viol_layout.setContentsMargins(0, 0, 0, 0)

        viol_label = QLabel("Detalle de Violaciones")
        viol_label.setObjectName("label_subtitle")
        viol_layout.addWidget(viol_label)

        self.viol_tree = QTreeWidget()
        self.viol_tree.setAlternatingRowColors(True)
        self.viol_tree.setHeaderLabels([
            "Tipo", "Celda A", "Celda B", "Freq A", "Freq B",
            "HO", "Coste"
        ])
        self.viol_tree.header().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        viol_layout.addWidget(self.viol_tree)
        detail_splitter.addWidget(viol_widget)

        detail_splitter.setSizes([500, 500])
        bottom_layout.addWidget(detail_splitter)

        splitter.addWidget(bottom_widget)
        splitter.setSizes([250, 350])

        layout.addWidget(splitter)

    def load_results(self, results: list[PlanResult], cells: dict[str, Cell]):
        self._results = results
        self._cells = cells

        if not results:
            self.summary_table.clear()
            self.summary_table.setRowCount(0)
            self.assign_table.clear()
            self.assign_table.setRowCount(0)
            self.viol_tree.clear()
            return

        columns = [
            "Plan", "Ejecución", "Score", "⚠ Críticas", "Total Viol.",
            "Cochan\nBCCH-BCCH", "Cochan\nBCCH-TCH", "Cochan\nTCH-TCH",
            "Adj\nBCCH-BCCH", "Adj\nBCCH-TCH", "Adj\nTCH-TCH",
            "Cochan\nHO-Pond", "Adj\nHO-Pond",
            "Misma Est.\nCochan", "Misma Est.\nAdj"
        ]

        self.summary_table.setColumnCount(len(columns))
        self.summary_table.setHorizontalHeaderLabels(columns)
        self.summary_table.setRowCount(len(results))

        best_score = min(r.score for r in results)

        for row, result in enumerate(results):
            values = [
                (str(result.plan_id), None),
                (f"Ejec. {result.run_id}", None),
                (f"{result.score:.1f}", Colors.SUCCESS if result.score == best_score else None),
                (str(result.critical_violations),
                 Colors.DANGER if result.critical_violations > 0 else Colors.SUCCESS),
                (str(result.total_violations),
                 Colors.WARNING if result.total_violations > 0 else Colors.SUCCESS),
                (str(result.cochannel_bcch_bcch),
                 Colors.DANGER if result.cochannel_bcch_bcch > 0 else None),
                (str(result.cochannel_bcch_tch),
                 Colors.HIGH if result.cochannel_bcch_tch > 0 else None),
                (str(result.cochannel_tch_tch),
                 Colors.LOW if result.cochannel_tch_tch > 0 else None),
                (str(result.adjacent_bcch_bcch),
                 Colors.DANGER if result.adjacent_bcch_bcch > 0 else None),
                (str(result.adjacent_bcch_tch),
                 Colors.HIGH if result.adjacent_bcch_tch > 0 else None),
                (str(result.adjacent_tch_tch),
                 Colors.LOW if result.adjacent_tch_tch > 0 else None),
                (f"{result.cochannel_ho_weighted:.1f}", None),
                (f"{result.adjacent_ho_weighted:.1f}", None),
                (str(result.same_station_cochannel),
                 Colors.DANGER if result.same_station_cochannel > 0 else None),
                (str(result.same_station_adjacent),
                 Colors.WARNING if result.same_station_adjacent > 0 else None),
            ]

            for col, (text, color) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if color:
                    item.setForeground(QBrush(QColor(color)))
                if row == 0:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.summary_table.setItem(row, col, item)

            if result.score == best_score:
                for col in range(len(values)):
                    item = self.summary_table.item(row, col)
                    if item:
                        item.setBackground(QBrush(QColor("#0a2240")))

        self.summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        self.summary_table.selectRow(0)
        self._show_assignments(results[0])
        self._show_violations(results[0])

    def _on_plan_selected(self, current_row, current_col, prev_row, prev_col):
        if current_row < 0 or current_row >= len(self._results):
            return
        result = self._results[current_row]
        self._show_assignments(result)
        self._show_violations(result)
        self.plan_selected.emit(result.plan_id)

    def _show_assignments(self, result: PlanResult):
        self.assign_table.clear()

        columns = ["Cell ID", "Estación", "Sector",
                    "BCCH Antes", "BCCH Después", "Cambio",
                    "TCH Antes", "TCH Después"]
        self.assign_table.setColumnCount(len(columns))
        self.assign_table.setHorizontalHeaderLabels(columns)
        self.assign_table.setRowCount(len(result.assignments))

        for row, assign in enumerate(sorted(result.assignments,
                                             key=lambda a: a.cell_id)):
            cell = self._cells.get(assign.cell_id)
            if not cell:
                continue

            bcch_changed = cell.bcch != assign.bcch
            tch_before = ",".join(str(f) for f in cell.tch_list)
            tch_after = ",".join(str(f) for f in assign.tch_list)
            tch_changed = tch_before != tch_after

            items = [
                (assign.cell_id, None),
                (cell.station.name if cell else "", None),
                (cell.sector if cell else "", None),
                (str(cell.bcch) if cell and cell.bcch else "—", None),
                (str(assign.bcch), Colors.WARNING if bcch_changed else None),
                ("✓ Cambiado" if bcch_changed or tch_changed else "—",
                 Colors.ACCENT if (bcch_changed or tch_changed) else Colors.TEXT_DIM),
                (tch_before if tch_before else "—", None),
                (tch_after if tch_after else "—",
                 Colors.ACCENT_BLUE if tch_changed else None),
            ]

            for col, (text, color) in enumerate(items):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if color:
                    item.setForeground(QBrush(QColor(color)))
                if bcch_changed or tch_changed:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.assign_table.setItem(row, col, item)

        self.assign_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

    def _show_violations(self, result: PlanResult):
        self.viol_tree.clear()

        if not result.violations:
            no_viol = QTreeWidgetItem(["✅ Sin violaciones"])
            no_viol.setForeground(0, QBrush(QColor(Colors.SUCCESS)))
            self.viol_tree.addTopLevelItem(no_viol)
            return

        groups = {
            "🔴 Cocanalidad BCCH-BCCH": [],
            "🟠 Cocanalidad BCCH-TCH": [],
            "🟡 Cocanalidad TCH-TCH": [],
            "🔴 Adyacencia BCCH-BCCH": [],
            "🟠 Adyacencia BCCH-TCH": [],
            "🟡 Adyacencia TCH-TCH": [],
        }

        for v in result.violations:
            both = {v.channel_type_a, v.channel_type_b}
            if v.violation_type == "cochannel":
                if both == {"BCCH"}:
                    key = "🔴 Cocanalidad BCCH-BCCH"
                elif "BCCH" in both:
                    key = "🟠 Cocanalidad BCCH-TCH"
                else:
                    key = "🟡 Cocanalidad TCH-TCH"
            else:
                if both == {"BCCH"}:
                    key = "🔴 Adyacencia BCCH-BCCH"
                elif "BCCH" in both:
                    key = "🟠 Adyacencia BCCH-TCH"
                else:
                    key = "🟡 Adyacencia TCH-TCH"
            groups[key].append(v)

        for group_name, violations in groups.items():
            if not violations:
                continue

            group_item = QTreeWidgetItem([f"{group_name} ({len(violations)})"])
            group_item.setExpanded(True)

            if "🔴" in group_name:
                group_item.setForeground(0, QBrush(QColor(Colors.DANGER)))
            elif "🟠" in group_name:
                group_item.setForeground(0, QBrush(QColor(Colors.WARNING)))
            else:
                group_item.setForeground(0, QBrush(QColor(Colors.MEDIUM)))

            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)

            for v in sorted(violations, key=lambda x: x.weighted_cost, reverse=True):
                child = QTreeWidgetItem([
                    f"{v.channel_type_a}-{v.channel_type_b}",
                    v.cell_a_id,
                    v.cell_b_id,
                    str(v.freq_a),
                    str(v.freq_b),
                    str(v.ho_count) if v.ho_count is not None else "—",
                    f"{v.weighted_cost:.1f}",
                ])
                group_item.addChild(child)

            self.viol_tree.addTopLevelItem(group_item)


class ResultComparisonWidget(QWidget):
    """Main results comparison widget supporting multi-execution sub-tabs."""

    plan_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[PlanResult] = []
        self._cells: dict[str, Cell] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("📈 Resultados de Optimización")
        title.setObjectName("label_title")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.lbl_status = QLabel("Sin resultados")
        self.lbl_status.setObjectName("label_subtitle")
        header_layout.addWidget(self.lbl_status)

        self.btn_export = QPushButton("📥 Exportar CSV")
        self.btn_export.setObjectName("btn_secondary")
        self.btn_export.setEnabled(False)
        header_layout.addWidget(self.btn_export)

        self.btn_export_pdf = QPushButton("📄 Exportar Informe PDF (3 Escenarios)")
        self.btn_export_pdf.setObjectName("btn_primary")
        self.btn_export_pdf.setStyleSheet("""
            QPushButton {
                background-color: #0984e3;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #74b9ff; }
        """)
        self.btn_export_pdf.setEnabled(False)
        header_layout.addWidget(self.btn_export_pdf)

        layout.addLayout(header_layout)

        # Sub-tabs for Multi-run results
        self.results_tabs = QTabWidget()
        layout.addWidget(self.results_tabs, 1)

    def load_results(self, results: list[PlanResult], cells: dict[str, Cell]):
        """Load results and generate sub-tabs for multi-run results."""
        self._results = results
        self._cells = cells

        self.results_tabs.clear()

        if not results:
            self.lbl_status.setText("Sin resultados")
            self.btn_export.setEnabled(False)
            self.btn_export_pdf.setEnabled(False)
            return

        self.btn_export.setEnabled(True)
        self.btn_export_pdf.setEnabled(True)

        per_run = getattr(results, "per_run", {})
        num_runs = len(per_run)

        if num_runs > 1:
            self.lbl_status.setText(
                f"{len(results)} mejores globales ({num_runs} ejecuciones realizadas)"
            )

            # Master Tab: Global Best across all runs
            global_widget = SingleRunResultWidget()
            global_widget.load_results(results, cells)
            global_widget.plan_selected.connect(self.plan_selected.emit)
            self.results_tabs.addTab(global_widget, "🏆 Mejor Global (Todas las Pasadas)")

            # Individual Run Tabs
            for run_idx in sorted(per_run.keys()):
                run_res = per_run[run_idx]
                best = min(r.score for r in run_res) if run_res else 0.0
                widget = SingleRunResultWidget()
                widget.load_results(run_res, cells)
                widget.plan_selected.connect(self.plan_selected.emit)
                self.results_tabs.addTab(
                    widget, f"Ejecución {run_idx} (Score: {best:.1f})"
                )
        else:
            best = min(r.score for r in results) if results else 0.0
            self.lbl_status.setText(f"{len(results)} planes calculados — Mejor score: {best:.1f}")

            widget = SingleRunResultWidget()
            widget.load_results(results, cells)
            widget.plan_selected.connect(self.plan_selected.emit)
            self.results_tabs.addTab(widget, "Resultados de Optimización")

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
from src.models.frequency_plan import PlanResult, Violation, format_number
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
        self.assign_table.verticalHeader().setDefaultSectionSize(26)
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
            "Tipo", "Origen / Estado", "Celda A", "Celda B", "Freq A", "Freq B",
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

    def load_results(
        self,
        results: list[PlanResult],
        cells: dict[str, Cell],
        baseline: Optional[PlanResult] = None
    ):
        self._baseline = baseline
        self._cells = cells

        # Include baseline plan as row 0 if provided
        all_plans = []
        if baseline is not None:
            all_plans.append(baseline)
        all_plans.extend(results)
        self._results = all_plans

        if not all_plans:
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
        self.summary_table.setRowCount(len(all_plans))

        best_calc_score = min((r.score for r in results), default=None)

        for row, result in enumerate(all_plans):
            is_base = (result is baseline)
            plan_label = "Actual (Línea Base)" if is_base else str(result.plan_id)
            run_label = "Línea Base" if is_base else f"Ejec. {result.run_id}"
            score_color = None
            if not is_base and best_calc_score is not None and result.score == best_calc_score:
                score_color = Colors.SUCCESS

            values = [
                (plan_label, None),
                (run_label, None),
                (format_number(result.score), score_color),
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
                (format_number(result.cochannel_ho_weighted), None),
                (format_number(result.adjacent_ho_weighted), None),
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

                if is_base:
                    item.setBackground(QBrush(QColor("#1e293b")))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif row == (1 if baseline else 0):
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

                self.summary_table.setItem(row, col, item)

            if not is_base and best_calc_score is not None and result.score == best_calc_score:
                for col in range(len(values)):
                    item = self.summary_table.item(row, col)
                    if item:
                        item.setBackground(QBrush(QColor("#0a2240")))

        self.summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        self.summary_table.selectRow(0)
        self._show_assignments(all_plans[0])
        self._show_violations(all_plans[0])

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

        is_base = (result is self._baseline or result.plan_id == 0)

        for row, assign in enumerate(sorted(result.assignments,
                                             key=lambda a: a.cell_id)):
            cell = self._cells.get(assign.cell_id)
            if not cell:
                continue

            bcch_changed = (cell.bcch != assign.bcch) and not is_base
            tch_before = ", ".join(format_number(f) for f in cell.tch_list)
            tch_after = ", ".join(format_number(f) for f in assign.tch_list)
            tch_changed = (tch_before != tch_after) and not is_base

            if is_base:
                status_text = "— (Línea Base)"
                status_color = Colors.TEXT_DIM
            elif bcch_changed or tch_changed:
                status_text = "✓ Cambiado"
                status_color = Colors.ACCENT
            else:
                status_text = "Sin cambio"
                status_color = Colors.TEXT_DIM

            items = [
                (assign.cell_id, None),
                (cell.station.name if cell else "", None),
                (cell.sector if cell else "", None),
                (format_number(cell.bcch) if cell and cell.bcch else "—", None),
                (format_number(assign.bcch), Colors.WARNING if bcch_changed else None),
                (status_text, status_color),
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

        header = self.assign_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i in range(len(columns)):
            self.assign_table.setColumnWidth(i, 90)
        self.assign_table.setColumnWidth(1, 130)  # Estación
        self.assign_table.setColumnWidth(6, 120)  # TCH Antes
        self.assign_table.setColumnWidth(7, 120)  # TCH Después

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

        is_base = (result is self._baseline or result.plan_id == 0)

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
                if is_base:
                    is_inh = True
                else:
                    c_a = self._cells.get(v.cell_a_id)
                    c_b = self._cells.get(v.cell_b_id)
                    a_pre = (c_a.bcch == v.freq_a if v.channel_type_a == "BCCH" else v.freq_a in c_a.tch_list) if c_a else False
                    b_pre = (c_b.bcch == v.freq_b if v.channel_type_b == "BCCH" else v.freq_b in c_b.tch_list) if c_b else False
                    is_inh = (a_pre and b_pre)
                    if getattr(v, "is_inherited", False):
                        is_inh = True

                origin_text = "📌 Preexistente" if is_inh else "✨ Modificada"

                child = QTreeWidgetItem([
                    f"{v.channel_type_a}-{v.channel_type_b}",
                    origin_text,
                    v.cell_a_id,
                    v.cell_b_id,
                    format_number(v.freq_a),
                    format_number(v.freq_b),
                    format_number(v.ho_count) if v.ho_count is not None else "—",
                    format_number(v.weighted_cost),
                ])
                if is_inh:
                    child.setForeground(1, QBrush(QColor("#38bdf8")))
                else:
                    child.setForeground(1, QBrush(QColor("#f472b6")))
                    f_mod = child.font(1)
                    f_mod.setBold(True)
                    child.setFont(1, f_mod)

                group_item.addChild(child)

            self.viol_tree.addTopLevelItem(group_item)


import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.ticker import FuncFormatter


class ConvergenceTabWidget(QWidget):
    """Widget displaying the final convergence history curves of all runs in a single unified plot."""
    def __init__(self, per_run_history: dict[int, list[tuple[int, float]]], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        fig = Figure(facecolor="#141a24")
        canvas = FigureCanvasQTAgg(fig)
        layout.addWidget(canvas)

        ax = fig.add_subplot(1, 1, 1)
        ax.set_facecolor("#0c1017")
        num_runs = len(per_run_history)
        title_text = "Comparación de Convergencia por Ejecución (Iteración vs Coste)" if num_runs > 1 else "Convergencia de la Optimización (Iteración vs Coste)"
        ax.set_title(title_text, color="#f0f4f8", fontsize=12, fontweight="bold", pad=10)
        ax.set_xlabel("Iteración / Generación", color="#8b9bb4", fontsize=10, labelpad=6)
        ax.set_ylabel("Coste (Score)", color="#8b9bb4", fontsize=10, labelpad=6)
        ax.grid(True, linestyle="--", alpha=0.3, color="#2b384e")
        ax.tick_params(colors="#8b9bb4", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#2b384e")

        formatter = FuncFormatter(lambda val, _: format_number(val))
        ax.xaxis.set_major_formatter(formatter)
        ax.yaxis.set_major_formatter(formatter)

        colors = ["#00d4aa", "#38bdf8", "#fb923c", "#a855f7", "#f43f5e", "#10b981", "#eab308", "#ec4899", "#6366f1"]

        for run_idx, hist in sorted(per_run_history.items()):
            c = colors[(run_idx - 1) % len(colors)]
            xs = [h[0] for h in hist]
            ys = [h[1] for h in hist]
            min_y = min(ys) if ys else 0
            ax.plot(
                xs, ys, color=c, linewidth=2.2, marker="o", markersize=3.5,
                label=f"Ejecución {run_idx} (Mín: {format_number(min_y)})"
            )

        ax.legend(facecolor="#16213e", edgecolor="#2b384e", labelcolor="#f0f4f8", fontsize=9, loc="upper right")
        fig.tight_layout(pad=2.0)
        canvas.draw_idle()


class ResultComparisonWidget(QWidget):
    """Main results comparison widget supporting multi-execution sub-tabs."""

    plan_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[PlanResult] = []
        self._cells: dict[str, Cell] = {}
        self._baseline: Optional[PlanResult] = None
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

        self.btn_export_html = QPushButton("🌐 Exportar Informe HTML Dinámico (Escenarios)")
        self.btn_export_html.setObjectName("btn_primary")
        self.btn_export_html.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00b894, stop:1 #0984e3);
                color: white;
                font-weight: bold;
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #55efc4, stop:1 #74b9ff);
                color: #2d3436;
            }
        """)
        self.btn_export_html.setEnabled(False)
        self.btn_export_pdf = self.btn_export_html  # Alias for backward compatibility
        header_layout.addWidget(self.btn_export_html)

        layout.addLayout(header_layout)

        # Sub-tabs for Multi-run results
        self.results_tabs = QTabWidget()
        layout.addWidget(self.results_tabs, 1)

    def load_results(
        self,
        results: list[PlanResult],
        cells: dict[str, Cell],
        neighbor_graph: Optional[object] = None,
        rules: Optional[object] = None,
        baseline: Optional[PlanResult] = None
    ):
        """Load results and generate sub-tabs for multi-run results."""
        self._results = results
        self._cells = cells

        if baseline is None and cells and neighbor_graph and rules:
            from src.io.html_exporter import generate_baseline_plan
            baseline = generate_baseline_plan(cells, neighbor_graph, rules)
        self._baseline = baseline

        self.results_tabs.clear()

        if not results:
            self.lbl_status.setText("Sin resultados")
            self.btn_export.setEnabled(False)
            self.btn_export_html.setEnabled(False)
            return

        self.btn_export.setEnabled(True)
        self.btn_export_html.setEnabled(True)

        per_run = getattr(results, "per_run", {})
        num_runs = len(per_run)

        if num_runs > 1:
            self.lbl_status.setText(
                f"{len(results)} mejores opciones ({num_runs} ejecuciones realizadas)"
            )

            # Master Tab: Best plan of each distinct execution
            best_of_each_run = []
            for run_idx in sorted(per_run.keys()):
                run_res = per_run[run_idx]
                if run_res:
                    best_run_plan = min(run_res, key=lambda r: r.score)
                    best_of_each_run.append(best_run_plan)

            best_widget = SingleRunResultWidget()
            best_widget.load_results(best_of_each_run, cells, baseline=baseline)
            best_widget.plan_selected.connect(self.plan_selected.emit)
            self.results_tabs.addTab(best_widget, "🏆 Mejores de Cada Ejecución")

            # Individual Run Tabs: top 5 plans of each run
            for run_idx in sorted(per_run.keys()):
                run_res = per_run[run_idx]
                sorted_run_res = sorted(run_res, key=lambda r: r.score)[:5]
                best = sorted_run_res[0].score if sorted_run_res else 0.0
                widget = SingleRunResultWidget()
                widget.load_results(sorted_run_res, cells, baseline=baseline)
                widget.plan_selected.connect(self.plan_selected.emit)
                self.results_tabs.addTab(
                    widget, f"Ejecución {run_idx} (Top 5 - Score: {format_number(best)})"
                )
        else:
            sorted_res = sorted(results, key=lambda r: r.score)[:5]
            best = sorted_res[0].score if sorted_res else 0.0
            self.lbl_status.setText(f"Top 5 planes calculados — Mejor score: {format_number(best)}")

            widget = SingleRunResultWidget()
            widget.load_results(sorted_res, cells, baseline=baseline)
            widget.plan_selected.connect(self.plan_selected.emit)
            self.results_tabs.addTab(widget, "Resultados (Top 5 Planes)")

        # Convergence history tab
        per_run_history = getattr(results, "per_run_history", {})
        if per_run_history:
            self.results_tabs.addTab(
                ConvergenceTabWidget(per_run_history), "📈 Curvas de Convergencia"
            )

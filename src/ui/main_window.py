"""
Main application window with tabbed interface.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMenuBar, QMenu, QStatusBar, QMessageBox,
    QFileDialog, QProgressBar, QTableWidget, QTableWidgetItem,
    QCheckBox, QSpinBox, QGroupBox, QGridLayout, QHeaderView,
    QAbstractItemView, QSplitter, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QBrush

import os
from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import PlanResult
from src.engine.constraints import FrequencyRules, STATISTICAL_MODELS
from src.engine.optimizer import FrequencyOptimizer
from src.io.data_loader import (
    load_cells, load_neighbors, load_handovers, export_results
)
from src.io.pdf_exporter import export_plan_pdf
from src.ui.styles import Colors
from src.ui.widgets.cell_table import CellTableWidget
from src.ui.widgets.neighbor_table import NeighborTableWidget
from src.ui.widgets.ho_table import HOTableWidget
from src.ui.widgets.result_comparison import ResultComparisonWidget
from src.ui.widgets.map_widget import MapWidget
from src.ui.dialogs.import_dialog import ImportDialog
from src.ui.dialogs.rules_dialog import RulesDialog
from src.ui.dialogs.range_adaptation_dialog import RangeAdaptationDialog


class OptimizerThread(QThread):
    """Background thread for running the optimization."""
    progress = pyqtSignal(int, int, int, int, float)  # run_idx, total_runs, gen, total_gen, best_score
    finished = pyqtSignal(list)  # list[PlanResult]
    error = pyqtSignal(str)

    def __init__(self, cells, neighbor_graph, rules):
        super().__init__()
        self.cells = cells
        self.neighbor_graph = neighbor_graph
        self.rules = rules
        self.optimizer = None

    def run(self):
        try:
            self.optimizer = FrequencyOptimizer(
                cells=self.cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                progress_callback=self._on_progress
            )
            results = self.optimizer.optimize()
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, *args):
        if len(args) == 5:
            run_idx, total_runs, gen, total, best_score = args
            self.progress.emit(run_idx, total_runs, gen, total, best_score)
        elif len(args) == 3:
            gen, total, best_score = args
            self.progress.emit(1, 1, gen, total, best_score)

    def cancel(self):
        if self.optimizer:
            self.optimizer.cancel()


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("📡 Freq Planner 2G — Planificación de Frecuencias GSM")
        self.setMinimumSize(1200, 800)
        self.showMaximized()

        # State
        self.cells: dict[str, Cell] = {}
        self.stations: dict[str, Station] = {}
        self.neighbor_graph = NeighborGraph()
        self.rules = FrequencyRules()
        self.results: list[PlanResult] = []
        self._optimizer_thread: OptimizerThread | None = None

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._update_status()

    def _setup_menu(self):
        """Setup the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&Archivo")

        act_import_cells = QAction("📊 Importar Celdas...", self)
        act_import_cells.setShortcut("Ctrl+I")
        act_import_cells.triggered.connect(lambda: self._import_data("cells"))
        file_menu.addAction(act_import_cells)

        act_import_neighbors = QAction("🔗 Importar Vecindades...", self)
        act_import_neighbors.setShortcut("Ctrl+N")
        act_import_neighbors.triggered.connect(lambda: self._import_data("neighbors"))
        file_menu.addAction(act_import_neighbors)

        act_import_ho = QAction("📡 Importar Handovers...", self)
        act_import_ho.setShortcut("Ctrl+H")
        act_import_ho.triggered.connect(lambda: self._import_data("handovers"))
        file_menu.addAction(act_import_ho)

        file_menu.addSeparator()

        act_export = QAction("📥 Exportar CSV...", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self._export_results)
        file_menu.addAction(act_export)

        act_export_pdf = QAction("📄 Exportar Informe PDF (3 Escenarios)...", self)
        act_export_pdf.setShortcut("Ctrl+P")
        act_export_pdf.triggered.connect(self._export_pdf)
        file_menu.addAction(act_export_pdf)

        file_menu.addSeparator()

        act_exit = QAction("Salir", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # Rules menu
        rules_menu = menubar.addMenu("&Reglas y Espectro")

        act_spectrum = QAction("📻 Reconfigurar Espectro (Rango Inicial → Final)...", self)
        act_spectrum.triggered.connect(self._open_spectrum_adaptation_dialog)
        rules_menu.addAction(act_spectrum)

        act_rules = QAction("⚙️ Configurar Reglas y Pesos Estadísticos...", self)
        act_rules.setShortcut("Ctrl+R")
        act_rules.triggered.connect(self._open_rules_dialog)
        rules_menu.addAction(act_rules)

        # Help menu
        help_menu = menubar.addMenu("A&yuda")

        act_about = QAction("Acerca de...", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _setup_ui(self):
        """Setup the main tabbed interface."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Tab widget
        self.tabs = QTabWidget()

        # ── Tab 1: Cells ───────────────────────────────────────────
        self.cell_table = CellTableWidget()
        self.tabs.addTab(self.cell_table, "📊 Datos de Entrada")

        # ── Tab 2: Neighbors & HO ─────────────────────────────────
        neighbor_ho_widget = QWidget()
        nh_layout = QVBoxLayout(neighbor_ho_widget)
        nh_layout.setContentsMargins(0, 0, 0, 0)

        nh_splitter = QSplitter(Qt.Orientation.Vertical)
        self.neighbor_table = NeighborTableWidget()
        self.ho_table = HOTableWidget()
        nh_splitter.addWidget(self.neighbor_table)
        nh_splitter.addWidget(self.ho_table)
        nh_splitter.setSizes([400, 300])
        nh_layout.addWidget(nh_splitter)

        self.tabs.addTab(neighbor_ho_widget, "🔗 Vecindades & HO")

        # ── Tab 3: Calculation ─────────────────────────────────────
        calc_widget = self._build_calc_tab()
        self.tabs.addTab(calc_widget, "🚀 Cálculo")

        # ── Tab 4: Results ─────────────────────────────────────────
        self.result_widget = ResultComparisonWidget()
        self.result_widget.btn_export.clicked.connect(self._export_results)
        self.result_widget.btn_export_pdf.clicked.connect(self._export_pdf)
        self.tabs.addTab(self.result_widget, "📈 Resultados")

        # ── Tab 5: Interactive Map ────────────────────────────────
        self.map_widget = MapWidget()
        self.tabs.addTab(self.map_widget, "🗺️ Mapa de Frecuencias")

        main_layout.addWidget(self.tabs)

    def _build_calc_tab(self) -> QWidget:
        """Build the calculation configuration tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header = QHBoxLayout()
        title = QLabel("🚀 Configuración del Cálculo")
        title.setObjectName("label_title")
        header.addWidget(title)
        header.addStretch()

        btn_spectrum = QPushButton("📻 Reconfigurar Espectro (Inicial → Final)")
        btn_spectrum.setToolTip("Definir rango inicial y final, sustituir frecuencias que desaparecen y mejorar con nuevo espectro")
        btn_spectrum.setStyleSheet("""
            QPushButton {
                background-color: #1e2b3e;
                color: #00d4aa;
                font-weight: bold;
                border: 1px solid #00d4aa;
                border-radius: 4px;
                padding: 5px 12px;
            }
            QPushButton:hover { background-color: #00d4aa; color: #1e272e; }
        """)
        btn_spectrum.clicked.connect(self._open_spectrum_adaptation_dialog)
        header.addWidget(btn_spectrum)

        btn_rules = QPushButton("⚙️ Reglas & Pesos")
        btn_rules.clicked.connect(self._open_rules_dialog)
        header.addWidget(btn_rules)
        layout.addLayout(header)

        # Splitter: cell selection + config
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Cell selection for recalculation
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_label = QLabel("Seleccionar celdas a recalcular:")
        left_label.setObjectName("label_subtitle")
        left_layout.addWidget(left_label)

        self.calc_table = QTableWidget()
        self.calc_table.setAlternatingRowColors(True)
        self.calc_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.calc_table.verticalHeader().setVisible(False)
        self.calc_table.horizontalHeader().setStretchLastSection(True)

        columns = ["Recalc", "Cell ID", "Estación", "Sector",
                    "BCCH", "Recalc BCCH", "TCH actuales",
                    "TCH a recalcular", "Nuevos TCH"]
        self.calc_table.setColumnCount(len(columns))
        self.calc_table.setHorizontalHeaderLabels(columns)
        left_layout.addWidget(self.calc_table)

        # Quick actions
        quick_layout = QHBoxLayout()
        btn_select_all = QPushButton("Seleccionar todo")
        btn_select_all.clicked.connect(self._select_all_cells)
        quick_layout.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("Deseleccionar todo")
        btn_deselect_all.clicked.connect(self._deselect_all_cells)
        quick_layout.addWidget(btn_deselect_all)

        quick_layout.addStretch()
        left_layout.addLayout(quick_layout)

        splitter.addWidget(left_widget)

        # Right: Summary and run
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Rules summary
        summary_group = QGroupBox("Resumen de configuración")
        sg_layout = QVBoxLayout(summary_group)
        self.lbl_rules_summary = QLabel("Reglas por defecto cargadas")
        self.lbl_rules_summary.setWordWrap(True)
        sg_layout.addWidget(self.lbl_rules_summary)
        right_layout.addWidget(summary_group)

        # Data status
        status_group = QGroupBox("Estado de datos")
        stg_layout = QGridLayout(status_group)
        self.lbl_data_cells = QLabel("❌ Sin celdas")
        self.lbl_data_neighbors = QLabel("❌ Sin vecindades")
        self.lbl_data_ho = QLabel("⚠ Sin HO (opcional)")
        self.lbl_data_cells.setObjectName("label_error")
        self.lbl_data_neighbors.setObjectName("label_error")
        self.lbl_data_ho.setObjectName("label_warning")
        stg_layout.addWidget(self.lbl_data_cells, 0, 0)
        stg_layout.addWidget(self.lbl_data_neighbors, 1, 0)
        stg_layout.addWidget(self.lbl_data_ho, 2, 0)
        right_layout.addWidget(status_group)

        # Calculation stats
        calc_group = QGroupBox("Parámetros del Cálculo")
        cg_layout = QGridLayout(calc_group)
        self.lbl_calc_cells = QLabel("0 celdas seleccionadas")
        self.lbl_calc_vars = QLabel("0 variables a optimizar")
        cg_layout.addWidget(self.lbl_calc_cells, 0, 0, 1, 2)
        cg_layout.addWidget(self.lbl_calc_vars, 1, 0, 1, 2)

        cg_layout.addWidget(QLabel("Número de ejecuciones:"), 2, 0)
        self.spin_calc_num_runs = QSpinBox()
        self.spin_calc_num_runs.setRange(1, 20)
        self.spin_calc_num_runs.setValue(self.rules.num_runs)
        self.spin_calc_num_runs.valueChanged.connect(self._on_calc_num_runs_changed)
        cg_layout.addWidget(self.spin_calc_num_runs, 2, 1)

        right_layout.addWidget(calc_group)

        right_layout.addStretch()

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        right_layout.addWidget(self.progress_bar)

        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("label_subtitle")
        self.lbl_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.lbl_progress)

        # Run button
        self.btn_calculate = QPushButton("🚀 CALCULAR FRECUENCIAS")
        self.btn_calculate.setObjectName("btn_success")
        self.btn_calculate.setMinimumHeight(50)
        self.btn_calculate.setStyleSheet("""
            QPushButton {
                background-color: #00b894;
                color: white;
                font-size: 16px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
                padding: 12px;
            }
            QPushButton:hover {
                background-color: #00d4aa;
            }
            QPushButton:pressed {
                background-color: #009874;
            }
            QPushButton:disabled {
                background-color: #2a2a3e;
                color: #555570;
            }
        """)
        self.btn_calculate.clicked.connect(self._run_calculation)
        self.btn_calculate.setEnabled(False)
        right_layout.addWidget(self.btn_calculate)

        self.btn_cancel = QPushButton("⛔ Cancelar cálculo")
        self.btn_cancel.setObjectName("btn_danger")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_calculation)
        right_layout.addWidget(self.btn_cancel)

        splitter.addWidget(right_widget)
        splitter.setSizes([700, 300])

        layout.addWidget(splitter)
        return widget

    def _setup_statusbar(self):
        """Setup the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Listo — Importa datos para comenzar")
        self.status_bar.addWidget(self.status_label)

    def _on_calc_num_runs_changed(self, value: int):
        self.rules.num_runs = value
        self._update_status()

    def _update_status(self):
        """Update status indicators across the UI."""
        has_cells = len(self.cells) > 0
        has_neighbors = self.neighbor_graph.total_relations > 0
        has_ho = self.neighbor_graph.has_ho_data

        # Data status labels
        if has_cells:
            self.lbl_data_cells.setText(f"✅ {len(self.cells)} celdas cargadas")
            self.lbl_data_cells.setStyleSheet(f"color: {Colors.SUCCESS}; font-weight: bold;")
        else:
            self.lbl_data_cells.setText("❌ Sin celdas — importa datos")
            self.lbl_data_cells.setStyleSheet(f"color: {Colors.DANGER}; font-weight: bold;")

        if has_neighbors:
            n = self.neighbor_graph.total_relations
            self.lbl_data_neighbors.setText(f"✅ {n} vecindades cargadas")
            self.lbl_data_neighbors.setStyleSheet(f"color: {Colors.SUCCESS}; font-weight: bold;")
        else:
            self.lbl_data_neighbors.setText("❌ Sin vecindades — importa datos")
            self.lbl_data_neighbors.setStyleSheet(f"color: {Colors.DANGER}; font-weight: bold;")

        if has_ho:
            n = self.neighbor_graph.relations_with_ho
            self.lbl_data_ho.setText(f"✅ {n} HO cargados")
            self.lbl_data_ho.setStyleSheet(f"color: {Colors.SUCCESS}; font-weight: bold;")
        else:
            self.lbl_data_ho.setText("⚠ Sin HO — cálculo sin ponderación")
            self.lbl_data_ho.setStyleSheet(f"color: {Colors.WARNING}; font-weight: bold;")

        # Calculate button requires cells + neighbors
        can_calculate = has_cells and has_neighbors
        self.btn_calculate.setEnabled(can_calculate)

        # Update rules summary
        r = self.rules
        pool = len(r.available_frequencies)
        stat_key = getattr(r, "ho_statistical_model", "logarithmic")
        stat_name = STATISTICAL_MODELS.get(stat_key, {}).get("short_name", stat_key.capitalize())
        summary = (
            f"Rango ARFCN: {r.freq_range_start}-{r.freq_range_end} ({pool} disponibles)\n"
            f"Modelo Estadístico: {stat_name}\n"
            f"Ejecuciones: {r.num_runs} pasadas | Población: {r.population_size}\n"
            f"Generaciones: {r.num_generations} por pasada | Top-K: {r.top_k_results}"
        )
        self.lbl_rules_summary.setText(summary)

        # Refresh calc table if cells loaded
        if has_cells:
            self._refresh_calc_table()

    def _refresh_calc_table(self):
        """Refresh the calculation cell selection table."""
        self.calc_table.setRowCount(len(self.cells))

        for row, cell in enumerate(sorted(self.cells.values(),
                                           key=lambda c: (c.station.station_id, c.sector))):
            # Recalc checkbox
            chk = QCheckBox()
            chk.setChecked(cell.needs_calculation)
            chk.stateChanged.connect(self._on_calc_selection_changed)
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self.calc_table.setCellWidget(row, 0, chk_widget)

            # Cell info
            items = [
                cell.cell_id,
                cell.station.station_id,
                cell.sector,
                str(cell.bcch) if cell.bcch is not None else "—",
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.calc_table.setItem(row, col + 1, item)

            # Recalc BCCH checkbox
            chk_bcch = QCheckBox()
            chk_bcch.setChecked(cell.recalculate_bcch)
            chk_bcch_widget = QWidget()
            chk_bcch_layout = QHBoxLayout(chk_bcch_widget)
            chk_bcch_layout.addWidget(chk_bcch)
            chk_bcch_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_bcch_layout.setContentsMargins(0, 0, 0, 0)
            self.calc_table.setCellWidget(row, 5, chk_bcch_widget)

            # TCH info
            tch_str = ",".join(str(f) for f in cell.tch_list) if cell.tch_list else "—"
            tch_item = QTableWidgetItem(tch_str)
            tch_item.setFlags(tch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.calc_table.setItem(row, 6, tch_item)

            # TCH indices to recalculate (editable text)
            tch_recalc_str = ",".join(str(i+1) for i in cell.tch_indices_to_recalculate)
            tch_recalc_item = QTableWidgetItem(tch_recalc_str)
            tch_recalc_item.setToolTip(
                "Indices de TCH a recalcular (1-based, separados por coma).\n"
                "Ej: '1,3' recalcula TCH1 y TCH3.\n"
                "Dejar vacío para no recalcular TCH existentes."
            )
            self.calc_table.setItem(row, 7, tch_recalc_item)

            # New TCH count
            spin_new = QSpinBox()
            spin_new.setRange(0, 20)
            spin_new.setValue(cell.new_tch_count)
            self.calc_table.setCellWidget(row, 8, spin_new)

        self.calc_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._on_calc_selection_changed()

    def _on_calc_selection_changed(self):
        """Update stats when cell selection changes."""
        selected = 0
        total_vars = 0

        for row in range(self.calc_table.rowCount()):
            chk_widget = self.calc_table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk and chk.isChecked():
                    selected += 1

                    # Count BCCH
                    bcch_widget = self.calc_table.cellWidget(row, 5)
                    if bcch_widget:
                        bcch_chk = bcch_widget.findChild(QCheckBox)
                        if bcch_chk and bcch_chk.isChecked():
                            total_vars += 1

                    # Count TCH recalcs
                    tch_item = self.calc_table.item(row, 7)
                    if tch_item and tch_item.text().strip():
                        indices = [p.strip() for p in tch_item.text().split(',')
                                   if p.strip()]
                        total_vars += len(indices)

                    # Count new TCH
                    spin = self.calc_table.cellWidget(row, 8)
                    if spin:
                        total_vars += spin.value()

        self.lbl_calc_cells.setText(f"{selected} celdas seleccionadas")
        self.lbl_calc_vars.setText(f"{total_vars} variables a optimizar")

    def _select_all_cells(self):
        for row in range(self.calc_table.rowCount()):
            chk_widget = self.calc_table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(True)

    def _deselect_all_cells(self):
        for row in range(self.calc_table.rowCount()):
            chk_widget = self.calc_table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)

    def _import_data(self, import_type: str):
        """Handle data import."""
        if import_type == "handovers" and self.neighbor_graph.total_relations == 0:
            QMessageBox.warning(
                self, "Aviso",
                "Debes importar las vecindades antes de los handovers."
            )
            return

        if import_type == "neighbors" and len(self.cells) == 0:
            QMessageBox.warning(
                self, "Aviso",
                "Debes importar las celdas antes de las vecindades."
            )
            return

        dialog = ImportDialog(self, import_type)
        if dialog.exec() != ImportDialog.DialogCode.Accepted:
            return

        file_path = dialog.selected_file
        if not file_path:
            return

        try:
            if import_type == "cells":
                cells, stations, warnings = load_cells(file_path)
                self.cells = cells
                self.stations = stations
                self.cell_table.load_cells(cells)
                self.map_widget.load_data(self.cells, self.results)
                self.status_label.setText(
                    f"Celdas importadas: {len(cells)} celdas de "
                    f"{len(stations)} estaciones")

            elif import_type == "neighbors":
                graph, warnings = load_neighbors(file_path, self.cells)
                self.neighbor_graph = graph
                self.neighbor_table.load_data(graph, self.cells)
                self.ho_table.load_data(graph, self.cells)
                self.status_label.setText(
                    f"Vecindades importadas: {graph.total_relations} relaciones")

            elif import_type == "handovers":
                warnings = load_handovers(file_path, self.neighbor_graph)
                self.neighbor_table.load_data(self.neighbor_graph, self.cells)
                self.ho_table.load_data(self.neighbor_graph, self.cells)
                self.status_label.setText("Handovers importados")

            if warnings:
                msg = "\n".join(warnings[:20])
                if len(warnings) > 20:
                    msg += f"\n... y {len(warnings) - 20} más"
                QMessageBox.information(self, "Importación completada", msg)

            self._update_status()

        except Exception as e:
            QMessageBox.critical(
                self, "Error de importación",
                f"Error procesando el archivo:\n\n{str(e)}"
            )

    def _open_rules_dialog(self):
        """Open the rules configuration dialog."""
        dialog = RulesDialog(self.rules, cells=self.cells, results=self.results, parent=self)
        if dialog.exec() == RulesDialog.DialogCode.Accepted:
            self.rules = dialog.get_rules()
            self._update_status()
            self.status_label.setText("Reglas actualizadas")

    def _apply_calc_selections(self):
        """
        Read the calc table UI and apply selections back to cell objects.
        """
        sorted_cells = sorted(self.cells.values(),
                              key=lambda c: (c.station.station_id, c.sector))

        for row, cell in enumerate(sorted_cells):
            # Check if row is selected for recalculation
            chk_widget = self.calc_table.cellWidget(row, 0)
            if not chk_widget:
                continue
            chk = chk_widget.findChild(QCheckBox)
            if not chk or not chk.isChecked():
                cell.recalculate_bcch = False
                cell.tch_indices_to_recalculate = []
                cell.new_tch_count = 0
                continue

            # BCCH recalculation
            bcch_widget = self.calc_table.cellWidget(row, 5)
            if bcch_widget:
                bcch_chk = bcch_widget.findChild(QCheckBox)
                cell.recalculate_bcch = bcch_chk.isChecked() if bcch_chk else False

            # TCH indices to recalculate
            tch_item = self.calc_table.item(row, 7)
            if tch_item and tch_item.text().strip():
                indices = []
                for part in tch_item.text().split(','):
                    part = part.strip()
                    if part:
                        try:
                            idx = int(part) - 1  # Convert to 0-based
                            if 0 <= idx < len(cell.tch_list):
                                indices.append(idx)
                        except ValueError:
                            pass
                cell.tch_indices_to_recalculate = indices
            else:
                cell.tch_indices_to_recalculate = []

            # New TCH count
            spin = self.calc_table.cellWidget(row, 8)
            cell.new_tch_count = spin.value() if spin else 0

    def _run_calculation(self):
        """Start the optimization process."""
        # Apply selections from the UI
        self._apply_calc_selections()

        # Check that we have something to calculate
        cells_to_calc = {cid: c for cid, c in self.cells.items()
                         if c.needs_calculation}
        if not cells_to_calc:
            QMessageBox.warning(
                self, "Sin celdas seleccionadas",
                "Selecciona al menos una celda y marca qué frecuencias recalcular\n"
                "(BCCH, TCH existentes, o nuevos TCH)."
            )
            return

        # Prepare: we pass ALL cells (for context) but only some have variables
        self.btn_calculate.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setVisible(True)
        total_steps = self.rules.num_generations * max(1, self.rules.num_runs)
        self.progress_bar.setRange(0, total_steps)
        self.progress_bar.setValue(0)
        self.lbl_progress.setText(f"Iniciando {self.rules.num_runs} ejecuciones...")

        self._optimizer_thread = OptimizerThread(
            self.cells, self.neighbor_graph, self.rules
        )
        self._optimizer_thread.progress.connect(self._on_optimization_progress)
        self._optimizer_thread.finished.connect(self._on_optimization_finished)
        self._optimizer_thread.error.connect(self._on_optimization_error)
        self._optimizer_thread.start()

    def _cancel_calculation(self):
        """Cancel the running optimization."""
        if self._optimizer_thread:
            self._optimizer_thread.cancel()
            self.lbl_progress.setText("Cancelando...")

    def _on_optimization_progress(self, run_idx, total_runs, gen, total_gens, best_score):
        """Update progress bar and label for multi-run execution."""
        current_step = (run_idx - 1) * total_gens + gen
        self.progress_bar.setValue(current_step)
        self.lbl_progress.setText(
            f"Ejecución {run_idx}/{total_runs} — Generación {gen}/{total_gens} — "
            f"Mejor score: {best_score:.1f}"
        )

    def _on_optimization_finished(self, results):
        """Handle optimization completion."""
        self.results = results
        self.btn_calculate.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setVisible(False)

        n = len(results)
        best = min(r.score for r in results) if results else 0
        self.lbl_progress.setText(
            f"✅ Completado — {n} planes generados — "
            f"Mejor score: {best:.1f}"
        )
        self.lbl_progress.setStyleSheet(f"color: {Colors.SUCCESS};")

        # Show results
        self.result_widget.load_results(results, self.cells)
        self.map_widget.load_data(self.cells, self.results)
        self.tabs.setCurrentIndex(3)  # Switch to Results tab

        self.status_label.setText(
            f"Optimización completada: {n} planes, mejor score: {best:.1f}"
        )

    def _on_optimization_error(self, error_msg):
        """Handle optimization error."""
        self.btn_calculate.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setVisible(False)
        self.lbl_progress.setText(f"❌ Error: {error_msg}")
        self.lbl_progress.setStyleSheet(f"color: {Colors.DANGER};")

        QMessageBox.critical(
            self, "Error de optimización",
            f"Se produjo un error durante la optimización:\n\n{error_msg}"
        )

    def _export_results(self):
        """Export results to a CSV file."""
        if not self.results:
            QMessageBox.warning(self, "Sin resultados",
                                "No hay resultados para exportar.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Exportar resultados",
            "resultados_frecuencias.csv",
            "CSV (*.csv);;Todos (*)"
        )

        if file_path:
            try:
                export_results(file_path, self.results, self.cells)
                QMessageBox.information(
                    self, "Exportado",
                    f"Resultados exportados a:\n{file_path}"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error",
                    f"Error exportando:\n{str(e)}"
                )

    def _open_spectrum_adaptation_dialog(self):
        """Open the spectrum range transition and adaptation dialog."""
        if not self.cells:
            QMessageBox.warning(
                self, "Sin celdas cargadas",
                "Debes importar los datos de celdas antes de reconfigurar el espectro."
            )
            return

        dialog = RangeAdaptationDialog(
            cells=self.cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            results=self.results,
            parent=self
        )

        should_optimize = False
        def on_optimize_requested(mode):
            nonlocal should_optimize
            should_optimize = True

        dialog.optimize_requested.connect(on_optimize_requested)

        if dialog.exec() == RangeAdaptationDialog.DialogCode.Accepted:
            self._update_status()
            self._refresh_calc_table()
            self.tabs.setCurrentIndex(2)  # Switch to Calculation tab
            self.status_label.setText(
                f"Espectro reconfigurado a {self.rules.freq_range_start}-{self.rules.freq_range_end}"
            )
            if should_optimize:
                self._run_calculation()

    def _export_pdf(self):
        """Export comprehensive statistical comparison report to PDF (comparing with 3 scenarios)."""
        if not self.results:
            QMessageBox.warning(
                self, "Sin resultados",
                "Debes ejecutar el cálculo de frecuencias antes de generar el informe PDF."
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Informe PDF de Planificación",
            "informe_plan_frecuencias_3_escenarios.pdf",
            "Documentos PDF (*.pdf);;Todos (*)"
        )

        if not file_path:
            return

        try:
            export_plan_pdf(
                file_path=file_path,
                cells=self.cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                results=self.results
            )

            res = QMessageBox.information(
                self, "Informe PDF Generado",
                f"✅ Informe PDF generado correctamente con la comparativa de 3 escenarios:\n\n{file_path}\n\n"
                f"¿Deseas abrir el archivo ahora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if res == QMessageBox.StandardButton.Yes:
                try:
                    os.startfile(file_path)
                except Exception as open_err:
                    QMessageBox.warning(self, "Aviso", f"No se pudo abrir automáticamente el archivo: {open_err}")

        except Exception as e:
            QMessageBox.critical(
                self, "Error al generar PDF",
                f"Se produjo un error al generar el informe PDF:\n\n{str(e)}"
            )

    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self, "Acerca de Freq Planner 2G",
            "<h2>📡 Freq Planner 2G</h2>"
            "<p>Herramienta de planificación y optimización de "
            "frecuencias GSM 2G.</p>"
            "<p>Utiliza un algoritmo genético con evaluación global "
            "para encontrar asignaciones de frecuencias óptimas "
            "minimizando cocanalidades y adyacencias.</p>"
            "<hr>"
            "<p><b>Características:</b></p>"
            "<ul>"
            "<li>Optimización global (no iterativa)</li>"
            "<li>Ponderación BCCH vs TCH (hopping)</li>"
            "<li>Soporte de handovers</li>"
            "<li>Múltiples planes para comparar</li>"
            "</ul>"
        )

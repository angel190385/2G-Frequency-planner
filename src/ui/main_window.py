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
import copy
from datetime import datetime
from src.models.cell import Cell, Station
from src.models.neighbor import NeighborGraph
from src.models.zone import OptimizationZone
from src.models.frequency_plan import PlanResult, format_number
from src.engine.constraints import FrequencyRules, STATISTICAL_MODELS
from src.engine.optimizer import FrequencyOptimizer
from src.engine.cluster_optimizer import ClusterOptimizerThread
from src.engine.hardware import get_hardware_profile
from src.io.data_loader import (
    load_cells, load_neighbors, load_handovers, export_results
)
from src.io.html_exporter import export_plan_html
from src.io.auto_export import export_execution_bundle
from src.ui.styles import Colors
from src.ui.widgets.cell_table import CellTableWidget
from src.ui.widgets.neighbor_table import NeighborTableWidget
from src.ui.widgets.ho_table import HOTableWidget
from src.ui.widgets.result_comparison import ResultComparisonWidget
from src.ui.widgets.map_widget import MapWidget
from src.ui.dialogs.import_dialog import ImportDialog
from src.ui.dialogs.planner_import_dialog import PlannerImportDialog
from src.ui.dialogs.rules_dialog import RulesDialog
from src.ui.dialogs.range_adaptation_dialog import RangeAdaptationDialog
from src.ui.dialogs.convergence_dialog import ConvergenceDialog
from src.ui.dialogs.cluster_progress_dialog import ClusterProgressDialog


class OptimizerThread(QThread):
    """Background thread for running the optimization."""
    progress = pyqtSignal(int, int, int, int, float)  # run_idx, total_runs, gen, total_gen, best_score
    finished = pyqtSignal(object)  # OptimizationResultList preserved with per_run and per_run_history
    error = pyqtSignal(str)

    def __init__(self, cells, neighbor_graph, rules, max_workers: int | None = None):
        super().__init__()
        self.cells = cells
        self.neighbor_graph = neighbor_graph
        self.rules = rules
        self.max_workers = max_workers
        self.optimizer = None

    def run(self):
        try:
            self.optimizer = FrequencyOptimizer(
                cells=self.cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                progress_callback=self._on_progress,
                max_workers=self.max_workers
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

    # Calc table column indices
    COL_RECALC = 0
    COL_ZONE = 1
    COL_CELL_ID = 2
    COL_CELLNAME = 2
    COL_STATION = 3
    COL_SECTOR = 4
    COL_BCCH = 5
    COL_RECALC_BCCH = 6
    COL_TCH = 7
    COL_RECALC_TCH = 8
    COL_NEW_TCH = 9

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
        self._cluster_thread: ClusterOptimizerThread | None = None
        self._zones_applied_to_calc: bool = False

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._update_status()

    def _setup_menu(self):
        """Setup the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&Archivo")

        act_import_planner = QAction("🌐 Importar Red Global t_Planner (Cells + Sectors)...", self)
        act_import_planner.setShortcut("Ctrl+G")
        act_import_planner.triggered.connect(self._open_planner_import_dialog)
        file_menu.addAction(act_import_planner)

        act_import_cells = QAction("📊 Importar Celdas (Formato Estándar)...", self)
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

        act_export_html = QAction("🌐 Exportar Informe HTML Dinámico (Escenarios)...", self)
        act_export_html.setShortcut("Ctrl+H")
        act_export_html.triggered.connect(self._export_html)
        file_menu.addAction(act_export_html)

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

        # ── Tab 3: Map 1 - Zones & Clusters (Pre-Calculation) ─────
        self.zone_map_widget = MapWidget(mode="zones")
        self.zone_map_widget.cluster_optimization_completed.connect(self._on_cluster_optimization_completed)
        self.zone_map_widget.zone_selection_applied.connect(self._on_zone_selection_applied)
        self.tabs.addTab(self.zone_map_widget, "🗺️ Zonas y Clústeres")

        # ── Tab 4: Calculation ─────────────────────────────────────
        self.calc_tab_widget = self._build_calc_tab()
        self.tabs.addTab(self.calc_tab_widget, "🚀 Cálculo")

        # ── Tab 5: Results ─────────────────────────────────────────
        self.result_widget = ResultComparisonWidget()
        self.result_widget.btn_export.clicked.connect(self._export_results)
        self.result_widget.btn_export_html.clicked.connect(self._export_html)
        self.tabs.addTab(self.result_widget, "📈 Resultados")

        # ── Tab 6: Map 2 - Frequency / Results Map (Post-Calculation)
        self.result_map_widget = MapWidget(mode="results")
        self.tabs.addTab(self.result_map_widget, "🗺️ Mapa de Resultados")

        # Backward compatibility alias
        self.map_widget = self.zone_map_widget

        # Synchronize zone outlines to results map and calculation table
        self.zone_map_widget.zones_changed.connect(self.result_map_widget.set_zones)
        self.zone_map_widget.zones_changed.connect(lambda _: self._refresh_calc_table())

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

        columns = ["Recalc", "Zona", "CellName", "Estación", "Sector",
                   "BCCH", "Recalc BCCH", "TCH actuales",
                   "TCH a recalcular", "Nuevos TCH"]
        self.calc_table.setColumnCount(len(columns))
        self.calc_table.setHorizontalHeaderLabels(columns)
        self.calc_table.itemChanged.connect(self._on_calc_table_item_changed)
        left_layout.addWidget(self.calc_table)

        # Quick actions
        quick_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Seleccionar todo")
        self.btn_select_all.clicked.connect(self._select_all_cells)
        quick_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("Deseleccionar todo")
        self.btn_deselect_all.clicked.connect(self._deselect_all_cells)
        quick_layout.addWidget(self.btn_deselect_all)

        self.btn_select_out_of_range = QPushButton("🎯 Solo fuera de rango")
        self.btn_select_out_of_range.setToolTip(
            "Selecciona automáticamente solo las frecuencias (BCCH y TCH) que estén\n"
            "fuera del rango permitido actual, cambiando lo mínimo necesario."
        )
        self.btn_select_out_of_range.setStyleSheet("""
            QPushButton {
                background-color: #1e2b3e;
                color: #00d4aa;
                border: 1px solid #00d4aa;
                font-weight: bold;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover { background-color: #00d4aa; color: #1e272e; }
        """)
        self.btn_select_out_of_range.clicked.connect(self._select_only_out_of_range)
        quick_layout.addWidget(self.btn_select_out_of_range)

        self.btn_recalc_all_tch = QPushButton("Recalcular todos los TCH")
        self.btn_recalc_all_tch.setToolTip("Marca 'Recalc' de todas las celdas y selecciona todos los TCH existentes para recalcular.")
        self.btn_recalc_all_tch.clicked.connect(self._recalculate_all_tch)
        quick_layout.addWidget(self.btn_recalc_all_tch)

        self.btn_recalc_all = QPushButton("Recalcular todo")
        self.btn_recalc_all.setToolTip("Marca 'Recalc', 'Recalc BCCH' y todos los TCH para todas las celdas.")
        self.btn_recalc_all.clicked.connect(self._recalculate_all)
        quick_layout.addWidget(self.btn_recalc_all)

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

        self.chk_sequential_zones = QCheckBox("Optimización secuencial por zonas")
        self.chk_sequential_zones.setChecked(False)
        self.chk_sequential_zones.setToolTip(
            "Si está activado y hay zonas definidas, ejecuta la optimización secuencialmente zona por zona\n"
            "protegiendo las fronteras de interferencia de la red.\n"
            "Se activa automáticamente al pulsar 'Aplicar Selección a Tabla de Cálculo' en el mapa."
        )
        self.chk_sequential_zones.toggled.connect(self._on_sequential_mode_toggled)
        cg_layout.addWidget(self.chk_sequential_zones, 2, 0, 1, 2)

        cg_layout.addWidget(QLabel("Número de ejecuciones:"), 3, 0)
        self.spin_calc_num_runs = QSpinBox()
        self.spin_calc_num_runs.setRange(1, 20)
        self.spin_calc_num_runs.setValue(self.rules.num_runs)
        self.spin_calc_num_runs.valueChanged.connect(self._on_calc_num_runs_changed)
        cg_layout.addWidget(self.spin_calc_num_runs, 3, 1)

        # Multi-core hardware profiling
        hw = get_hardware_profile()
        cg_layout.addWidget(QLabel("Procesos CPU paralelos:"), 4, 0)
        self.spin_calc_workers = QSpinBox()
        self.spin_calc_workers.setRange(1, max(32, hw.cpu_count))
        self.spin_calc_workers.setValue(hw.recommended_workers)
        self.spin_calc_workers.setToolTip("Aprovecha el 100% de la CPU distribuyendo el cálculo en todos los núcleos.")
        cg_layout.addWidget(self.spin_calc_workers, 4, 1)

        self.lbl_hw_info = QLabel(hw.summary_detailed)
        self.lbl_hw_info.setStyleSheet(f"color: {Colors.ACCENT}; font-size: 11px; padding-top: 4px;")
        cg_layout.addWidget(self.lbl_hw_info, 5, 0, 1, 2)

        right_layout.addWidget(calc_group)

        right_layout.addStretch()

        # Progress status label
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

    def _on_calc_table_item_changed(self, item):
        if self.calc_table.signalsBlocked():
            return
        if item.column() in (self.COL_RECALC, self.COL_RECALC_BCCH, self.COL_RECALC_TCH, self.COL_NEW_TCH):
            self._on_calc_selection_changed()

    def _refresh_calc_table(self):
        """Refresh the calculation cell selection table with cell info and assigned zones."""
        self.calc_table.blockSignals(True)
        self.calc_table.setRowCount(len(self.cells))
        self.calc_table.verticalHeader().setDefaultSectionSize(26)

        # Build lookup for quick zone mapping
        cell_to_zone = {}
        if hasattr(self, "zone_map_widget") and self.zone_map_widget and self.zone_map_widget.zones:
            for z in self.zone_map_widget.zones:
                for cid in z.cell_ids:
                    cell_to_zone[cid] = z

        for row, cell in enumerate(sorted(self.cells.values(),
                                           key=lambda c: (c.station.station_id, c.sector))):
            # 0. Recalc checkbox (native QTableWidgetItem checkstate)
            item_recalc = QTableWidgetItem()
            item_recalc.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item_recalc.setCheckState(Qt.CheckState.Checked if cell.needs_calculation else Qt.CheckState.Unchecked)
            self.calc_table.setItem(row, self.COL_RECALC, item_recalc)

            # 1. Zona column (badge with zone color or unassigned mark)
            z = cell_to_zone.get(cell.cell_id)
            if z:
                item_zone = QTableWidgetItem(f"● {z.name}")
                item_zone.setToolTip(f"Perteneciente al clúster / zona: {z.name}")
                c_hex = getattr(z, "color", "#00d4aa")
                item_zone.setForeground(QBrush(QColor(c_hex)))
            else:
                item_zone = QTableWidgetItem("—")
                item_zone.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_zone.setForeground(QBrush(QColor("#64748b")))
                item_zone.setToolTip("Celda fuera de polígonos / zonas")
            item_zone.setFlags(item_zone.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.calc_table.setItem(row, self.COL_ZONE, item_zone)

            # 2..5 Cell info: Cell ID, Estación, Sector, BCCH
            items = [
                cell.cell_id,
                cell.station.station_id,
                cell.sector,
                str(cell.bcch) if cell.bcch is not None else "—",
            ]
            for col_offset, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.calc_table.setItem(row, self.COL_CELL_ID + col_offset, item)

            # 6. Recalc BCCH checkbox
            item_bcch = QTableWidgetItem()
            item_bcch.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item_bcch.setCheckState(Qt.CheckState.Checked if cell.recalculate_bcch else Qt.CheckState.Unchecked)
            self.calc_table.setItem(row, self.COL_RECALC_BCCH, item_bcch)

            # 7. TCH info
            tch_str = ",".join(str(f) for f in cell.tch_list) if cell.tch_list else "—"
            tch_item = QTableWidgetItem(tch_str)
            tch_item.setFlags(tch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.calc_table.setItem(row, self.COL_TCH, tch_item)

            # 8. TCH indices to recalculate (editable text)
            tch_recalc_str = ",".join(str(i+1) for i in cell.tch_indices_to_recalculate)
            tch_recalc_item = QTableWidgetItem(tch_recalc_str)
            tch_recalc_item.setToolTip(
                "Indices de TCH a recalcular (1-based, separados por coma).\n"
                "Ej: '1,3' recalcula TCH1 y TCH3.\n"
                "Dejar vacío para no recalcular TCH existentes."
            )
            self.calc_table.setItem(row, self.COL_RECALC_TCH, tch_recalc_item)

            # 9. New TCH count (editable integer item)
            item_new = QTableWidgetItem(str(cell.new_tch_count))
            item_new.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
            item_new.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.calc_table.setItem(row, self.COL_NEW_TCH, item_new)

        header = self.calc_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.calc_table.setColumnWidth(self.COL_RECALC, 60)
        self.calc_table.setColumnWidth(self.COL_ZONE, 95)
        self.calc_table.setColumnWidth(self.COL_CELL_ID, 100)
        self.calc_table.setColumnWidth(self.COL_STATION, 90)
        self.calc_table.setColumnWidth(self.COL_SECTOR, 60)
        self.calc_table.setColumnWidth(self.COL_BCCH, 70)
        self.calc_table.setColumnWidth(self.COL_RECALC_BCCH, 90)
        self.calc_table.setColumnWidth(self.COL_TCH, 110)
        self.calc_table.setColumnWidth(self.COL_RECALC_TCH, 120)
        self.calc_table.setColumnWidth(self.COL_NEW_TCH, 80)

        self.calc_table.blockSignals(False)
        has_zones = hasattr(self, "zone_map_widget") and bool(self.zone_map_widget.zones)
        if hasattr(self, "chk_sequential_zones"):
            self.chk_sequential_zones.setEnabled(has_zones)
            if not has_zones:
                self.chk_sequential_zones.setChecked(False)
        self._update_calc_button_text()
        self._on_calc_selection_changed()

    _update_calc_table = _refresh_calc_table

    def _on_sequential_mode_toggled(self, checked: bool):
        self._update_calc_button_text()
        self._on_calc_selection_changed()

    def _update_calc_button_text(self):
        has_zones = hasattr(self, "zone_map_widget") and bool(self.zone_map_widget.zones)
        is_seq = getattr(self, "chk_sequential_zones", None) and self.chk_sequential_zones.isChecked()
        if is_seq and has_zones:
            self.btn_calculate.setText("🚀 CALCULAR FRECUENCIAS (Secuencial)")
            self.btn_calculate.setToolTip("Ejecuta la optimización secuencial respetando las zonas y protegiendo las fronteras de interferencia.")
        else:
            self.btn_calculate.setText("🚀 CALCULAR FRECUENCIAS")
            self.btn_calculate.setToolTip("Ejecuta la optimización global sobre todas las celdas seleccionadas.")

    def _on_calc_selection_changed(self):
        """Update stats when cell selection changes."""
        selected = 0
        total_vars = 0
        selected_cell_ids = set()

        for row in range(self.calc_table.rowCount()):
            recalc_item = self.calc_table.item(row, self.COL_RECALC)
            if recalc_item and recalc_item.checkState() == Qt.CheckState.Checked:
                selected += 1
                cell_id_item = self.calc_table.item(row, self.COL_CELL_ID)
                if cell_id_item:
                    selected_cell_ids.add(cell_id_item.text().strip())

                # Count BCCH
                bcch_item = self.calc_table.item(row, self.COL_RECALC_BCCH)
                if bcch_item and bcch_item.checkState() == Qt.CheckState.Checked:
                    total_vars += 1

                # Count TCH recalcs
                tch_item = self.calc_table.item(row, self.COL_RECALC_TCH)
                if tch_item and tch_item.text().strip():
                    indices = [p.strip() for p in tch_item.text().split(',')
                               if p.strip()]
                    total_vars += len(indices)

                # Count new TCH
                new_item = self.calc_table.item(row, self.COL_NEW_TCH)
                if new_item and new_item.text().strip():
                    try:
                        total_vars += max(0, min(20, int(new_item.text().strip())))
                    except ValueError:
                        pass

        # Count active zones among selected cells
        active_zone_names = set()
        if hasattr(self, "zone_map_widget") and self.zone_map_widget and self.zone_map_widget.zones:
            for z in self.zone_map_widget.zones:
                if any(cid in selected_cell_ids for cid in z.cell_ids):
                    active_zone_names.add(z.name)

        if active_zone_names and getattr(self, "chk_sequential_zones", None) and self.chk_sequential_zones.isChecked():
            self.lbl_calc_cells.setText(f"{selected} celdas seleccionadas ({len(active_zone_names)} zonas)")
        else:
            self.lbl_calc_cells.setText(f"{selected} celdas seleccionadas")
        self.lbl_calc_vars.setText(f"{total_vars} variables a optimizar")

    def _select_all_cells(self):
        self.calc_table.blockSignals(True)
        for row in range(self.calc_table.rowCount()):
            item = self.calc_table.item(row, self.COL_RECALC)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
        self.calc_table.blockSignals(False)
        self._apply_calc_selections()
        self._on_calc_selection_changed()

    def _deselect_all_cells(self):
        self.calc_table.blockSignals(True)
        for row in range(self.calc_table.rowCount()):
            # 1. Uncheck Recalc
            item = self.calc_table.item(row, self.COL_RECALC)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

            # 2. Uncheck Recalc BCCH
            item_bcch = self.calc_table.item(row, self.COL_RECALC_BCCH)
            if item_bcch:
                item_bcch.setCheckState(Qt.CheckState.Unchecked)

            # 3. Clear TCH a recalcular
            item_tch = self.calc_table.item(row, self.COL_RECALC_TCH)
            if item_tch:
                item_tch.setText("")

        self.calc_table.blockSignals(False)
        self._apply_calc_selections()
        self._on_calc_selection_changed()

    def _recalculate_all_tch(self):
        """Mark 'Recalc' for all cells and set all existing TCHs to be recalculated (without BCCH)."""
        self.calc_table.blockSignals(True)
        for row in range(self.calc_table.rowCount()):
            # 1. Recalc checked
            item_recalc = self.calc_table.item(row, self.COL_RECALC)
            if item_recalc:
                item_recalc.setCheckState(Qt.CheckState.Checked)

            # 2. Recalc BCCH unchecked (only TCHs)
            item_bcch = self.calc_table.item(row, self.COL_RECALC_BCCH)
            if item_bcch:
                item_bcch.setCheckState(Qt.CheckState.Unchecked)

            # 3. Mark all TCHs (1-based indices)
            cell_id_item = self.calc_table.item(row, self.COL_CELL_ID)
            cid = cell_id_item.text().strip() if cell_id_item else ""
            cell = self.cells.get(cid)
            if cell and cell.tch_list:
                all_tch_str = ",".join(str(i + 1) for i in range(len(cell.tch_list)))
            else:
                tch_item_col = self.calc_table.item(row, self.COL_TCH)
                if tch_item_col and tch_item_col.text().strip() and tch_item_col.text().strip() != "—":
                    count = len([x for x in tch_item_col.text().split(",") if x.strip()])
                    all_tch_str = ",".join(str(i + 1) for i in range(count))
                else:
                    all_tch_str = ""

            item_tch = self.calc_table.item(row, self.COL_RECALC_TCH)
            if item_tch:
                item_tch.setText(all_tch_str)

        self.calc_table.blockSignals(False)
        self._apply_calc_selections()
        self._on_calc_selection_changed()

    def _recalculate_all(self):
        """Mark 'Recalc', 'Recalc BCCH', and all existing TCHs for all cells."""
        self.calc_table.blockSignals(True)
        for row in range(self.calc_table.rowCount()):
            # 1. Recalc checked
            item_recalc = self.calc_table.item(row, self.COL_RECALC)
            if item_recalc:
                item_recalc.setCheckState(Qt.CheckState.Checked)

            # 2. Recalc BCCH checked
            item_bcch = self.calc_table.item(row, self.COL_RECALC_BCCH)
            if item_bcch:
                item_bcch.setCheckState(Qt.CheckState.Checked)

            # 3. Mark all TCHs (1-based indices)
            cell_id_item = self.calc_table.item(row, self.COL_CELL_ID)
            cid = cell_id_item.text().strip() if cell_id_item else ""
            cell = self.cells.get(cid)
            if cell and cell.tch_list:
                all_tch_str = ",".join(str(i + 1) for i in range(len(cell.tch_list)))
            else:
                tch_item_col = self.calc_table.item(row, self.COL_TCH)
                if tch_item_col and tch_item_col.text().strip() and tch_item_col.text().strip() != "—":
                    count = len([x for x in tch_item_col.text().split(",") if x.strip()])
                    all_tch_str = ",".join(str(i + 1) for i in range(count))
                else:
                    all_tch_str = ""

            item_tch = self.calc_table.item(row, self.COL_RECALC_TCH)
            if item_tch:
                item_tch.setText(all_tch_str)

        self.calc_table.blockSignals(False)
        self._apply_calc_selections()
        self._on_calc_selection_changed()

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
            from src.ui.dialogs.wait_dialog import BusyWaitDialog

            if import_type == "cells":
                with BusyWaitDialog("Importando Celdas", "Leyendo archivo de celdas...", self) as wait:
                    def on_cells_progress(read_count, accepted_count):
                        wait.set_text(f"Leyendo archivo ({read_count} filas procesadas, {accepted_count} celdas)...")

                    cells, stations, warnings = load_cells(file_path, progress_callback=on_cells_progress)
                    self.cells = cells
                    self.stations = stations

                    wait.set_text("Actualizando tabla de datos de entrada...")
                    self.cell_table.load_cells(cells)

                    wait.set_text("Actualizando tabla de cálculo...")
                    self._refresh_calc_table()

                    wait.set_text("Actualizando mapa y visualización...")
                    self.zone_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)
                    self.result_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)

                fname = os.path.basename(file_path)
                self.status_label.setText(
                    f"Celdas importadas ({fname}): {len(cells)} celdas de {len(stations)} estaciones"
                )
                # Automatically direct user to the zones map tab to trace clusters
                self.tabs.setCurrentWidget(self.zone_map_widget)

            elif import_type == "neighbors":
                with BusyWaitDialog("Importando Vecindades", "Calculando distancias y grafo de vecindades...", self) as wait:
                    def on_ng_progress(read_count, rel_count):
                        wait.set_text(f"Calculando vecindades ({read_count} filas, {rel_count} relaciones)...")

                    graph, warnings = load_neighbors(file_path, self.cells, progress_callback=on_ng_progress)
                    self.neighbor_graph = graph

                    wait.set_text("Actualizando tabla de vecindades...")
                    self.neighbor_table.load_data(graph, self.cells)
                    self.ho_table.load_data(graph, self.cells)

                    wait.set_text("Actualizando mapa con relaciones...")
                    self.zone_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)
                    self.result_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)

                self.status_label.setText(
                    f"Vecindades importadas: {graph.total_relations} relaciones"
                )

            elif import_type == "handovers":
                with BusyWaitDialog("Importando Handovers", "Asociando estadísticas de traspaso a vecindades...", self) as wait:
                    def on_ho_progress(read_count, matched_count):
                        wait.set_text(f"Asociando handovers ({read_count} filas, {matched_count} coincidencias)...")

                    warnings = load_handovers(file_path, self.neighbor_graph, progress_callback=on_ho_progress)

                    wait.set_text("Actualizando tablas de vecindades y HO...")
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

    def _open_planner_import_dialog(self):
        """Open dialog to import global network exports from t_Planner."""
        dlg = PlannerImportDialog(self)
        if dlg.exec() == PlannerImportDialog.DialogCode.Accepted:
            from src.ui.dialogs.wait_dialog import BusyWaitDialog
            with BusyWaitDialog("Cargando en Interfaz", "Poblando tabla de celdas...", self) as wait:
                self.cells = dlg.imported_cells
                self.stations = dlg.imported_stations
                self.cell_table.load_cells(self.cells)

                wait.set_text("Actualizando tabla de frecuencias y cálculo...")
                self._refresh_calc_table()

                wait.set_text("Actualizando mapa y visualización...")
                self.zone_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)
                self.result_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)

            self.status_label.setText(
                f"Red t_Planner importada: {len(self.cells)} celdas en {len(self.stations)} estaciones"
            )
            self._update_status()
            # Switch to zones map tab automatically so the user can see the network and trace zones
            self.tabs.setCurrentWidget(self.zone_map_widget)

    def _on_cluster_optimization_completed(self, zone_results: dict, updated_cells: dict):
        """Handle completion of sequential cluster/zone optimization."""
        import copy
        from src.engine.cost_function import evaluate_plan
        from src.models.frequency_plan import FrequencyAssignment, OptimizationResultList
        from src.io.html_exporter import generate_baseline_plan
        from src.io.auto_export import export_execution_bundle

        # 1. Preserve original cells as baseline before updating self.cells
        baseline_cells = getattr(self, "_baseline_cells", None)
        if not baseline_cells:
            baseline_cells = copy.deepcopy(self.cells)

        baseline_plan = generate_baseline_plan(baseline_cells, self.neighbor_graph, self.rules)

        # 2. Update network cells with newly calculated frequencies
        self.cells = updated_cells
        self.cell_table.load_cells(self.cells)
        self._refresh_calc_table()

        # 3. Determine active zones and all run IDs across all zones
        active_zones = getattr(self, "_last_active_zones", None)
        if not active_zones and hasattr(self, "zone_map_widget"):
            active_zones = [z for z in self.zone_map_widget.zones if z.zone_id in zone_results]
        if not active_zones:
            active_zones = list(self.zone_map_widget.zones) if hasattr(self, "zone_map_widget") else []

        all_run_ids = set()
        for z_id, z_res in zone_results.items():
            if hasattr(z_res, "per_run") and z_res.per_run:
                all_run_ids.update(z_res.per_run.keys())

        if not all_run_ids:
            all_run_ids = {1}

        sorted_run_ids = sorted(all_run_ids)
        per_run = {}
        per_run_history = {}

        # Evaluate the absolute best combination across all zones (from updated_cells)
        best_assignments = {}
        for cid, cell in self.cells.items():
            best_assignments[cid] = FrequencyAssignment(
                cell_id=cid,
                bcch=cell.bcch or 0,
                tch_list=list(cell.tch_list)
            )

        best_consolidated_plan = evaluate_plan(
            assignments=best_assignments,
            cells=baseline_cells,
            neighbor_graph=self.neighbor_graph,
            rules=self.rules,
            plan_id=1,
            run_id=1,
            collect_violations=True
        )

        for r in sorted_run_ids:
            run_assignments = {}
            for cid, cell in baseline_cells.items():
                run_assignments[cid] = FrequencyAssignment(
                    cell_id=cid,
                    bcch=cell.bcch or 0,
                    tch_list=list(cell.tch_list)
                )

            for z in active_zones:
                z_res = zone_results.get(z.zone_id)
                if not z_res:
                    continue
                plans_in_r = getattr(z_res, "per_run", {}).get(r, []) if hasattr(z_res, "per_run") else []
                if plans_in_r:
                    best_p = min(plans_in_r, key=lambda p: p.score)
                else:
                    best_p = getattr(z_res, "best_plan", z_res[0] if isinstance(z_res, list) and z_res else None)

                if best_p:
                    for assign in best_p.assignments:
                        if assign.cell_id in z.cell_ids:
                            run_assignments[assign.cell_id] = copy.deepcopy(assign)

            plan_r = evaluate_plan(
                assignments=run_assignments,
                cells=baseline_cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                plan_id=r,
                run_id=r,
                collect_violations=True
            )
            per_run[r] = [plan_r]

            # Aggregate per_run_history across zones for run r
            hist_r = []
            gen_offset = 0
            for z in active_zones:
                z_res = zone_results.get(z.zone_id)
                if z_res and hasattr(z_res, "per_run_history"):
                    z_hist = z_res.per_run_history.get(r, [])
                    if z_hist:
                        for gen_num, score in z_hist:
                            hist_r.append((gen_offset + gen_num, score))
                        gen_offset += max(h[0] for h in z_hist)
            if hist_r:
                per_run_history[r] = hist_r

        seen_scores = set()
        all_plans = []
        for p in [best_consolidated_plan] + [per_run[r][0] for r in sorted_run_ids]:
            sc_key = round(p.score, 4)
            if sc_key not in seen_scores:
                seen_scores.add(sc_key)
                all_plans.append(p)
        all_plans.sort(key=lambda p: p.score)

        res_list = OptimizationResultList(all_plans, per_run=per_run, per_run_history=per_run_history)
        self.results = res_list

        self.result_widget.load_results(
            res_list,
            baseline_cells,
            self.neighbor_graph,
            self.rules,
            baseline=baseline_plan,
            zone_results=zone_results,
            zones=active_zones
        )
        self.zone_map_widget.load_data(baseline_cells, self.results, self.neighbor_graph, self.rules)
        self.result_map_widget.load_data(baseline_cells, self.results, self.neighbor_graph, self.rules)
        self.tabs.setCurrentWidget(self.result_widget)

        try:
            export_execution_bundle(
                cells=baseline_cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                results=res_list
            )
        except Exception as e:
            print("Error exporting cluster bundle:", e)

    def _on_zone_selection_applied(self):
        """Called when polygon zones selection is applied to the Calculation table."""
        has_zones = hasattr(self, "zone_map_widget") and bool(self.zone_map_widget.zones)
        if has_zones:
            self._zones_applied_to_calc = True
            if hasattr(self, "chk_sequential_zones"):
                self.chk_sequential_zones.setChecked(True)
            self.status_label.setText("Celdas seleccionadas desde los polígonos del mapa para recálculo secuencial")
        else:
            self._zones_applied_to_calc = False
            if hasattr(self, "chk_sequential_zones"):
                self.chk_sequential_zones.setChecked(False)
            self.status_label.setText("Sin zonas dibujadas — Se ejecutará en una sola optimización global")

        self._update_calc_button_text()
        self._refresh_calc_table()
        # Switch to calculation tab immediately
        self.tabs.setCurrentWidget(self.calc_tab_widget)

    def _open_rules_dialog(self):
        """Open the rules configuration dialog."""
        old_start = self.rules.freq_range_start
        old_end = self.rules.freq_range_end
        old_excl = set(self.rules.excluded_frequencies)

        dialog = RulesDialog(self.rules, cells=self.cells, results=self.results, parent=self)
        if dialog.exec() == RulesDialog.DialogCode.Accepted:
            self.rules = dialog.get_rules()

            # Synchronize zone rules if any exist
            if hasattr(self, "zone_map_widget") and self.zone_map_widget and self.zone_map_widget.zones:
                for z in self.zone_map_widget.zones:
                    if hasattr(z, "rules") and z.rules:
                        z.rules.freq_range_start = self.rules.freq_range_start
                        z.rules.freq_range_end = self.rules.freq_range_end
                        z.rules.excluded_frequencies = list(self.rules.excluded_frequencies)

            new_excl = set(self.rules.excluded_frequencies)
            range_changed = (old_start != self.rules.freq_range_start or
                             old_end != self.rules.freq_range_end or
                             old_excl != new_excl)

            if range_changed and self.cells:
                self._select_only_out_of_range()
                self.status_label.setText(
                    f"Reglas actualizadas (Rango {self.rules.freq_range_start}-{self.rules.freq_range_end}). "
                    "Marcadas por defecto solo frecuencias fuera de rango."
                )
            else:
                self._update_status()
                self.status_label.setText("Reglas actualizadas")

    def _select_only_out_of_range(self):
        """
        Quick action in Calculation tab:
        Select only cells and frequencies that are outside the allowed frequency pool.
        Frequencies within the allowed range remain untouched (minimum change principle).
        """
        allowed_pool = set(self.rules.available_frequencies) if self.rules else set()
        if not allowed_pool:
            QMessageBox.information(
                self, "Pool vacío",
                "No hay frecuencias disponibles configuradas en las reglas actuales."
            )
            return

        recalc_cells = 0
        recalc_vars = 0
        for cell in self.cells.values():
            # BCCH: only recalculate if None, <= 0, or outside allowed_pool
            if cell.bcch is None or cell.bcch <= 0 or cell.bcch not in allowed_pool:
                cell.recalculate_bcch = True
            else:
                cell.recalculate_bcch = False

            # TCH: only recalculate indices with frequencies outside allowed_pool
            cell.tch_indices_to_recalculate = [
                i for i, f in enumerate(cell.tch_list)
                if f is None or f <= 0 or f not in allowed_pool
            ]
            cell.new_tch_count = 0

            if cell.needs_calculation:
                recalc_cells += 1
                recalc_vars += cell.count_variables()

        self._refresh_calc_table()
        self._update_status()
        self.status_label.setText(
            f"Selección mínima aplicada: {recalc_cells} celdas con {recalc_vars} frecuencias fuera del rango ({self.rules.freq_range_start}-{self.rules.freq_range_end})."
        )

    def _apply_calc_selections(self):
        """
        Read the calc table UI and apply selections back to cell objects.
        """
        sorted_cells = sorted(self.cells.values(),
                              key=lambda c: (c.station.station_id, c.sector))

        for row, cell in enumerate(sorted_cells):
            recalc_item = self.calc_table.item(row, self.COL_RECALC)
            if not recalc_item or recalc_item.checkState() != Qt.CheckState.Checked:
                cell.recalculate_bcch = False
                cell.tch_indices_to_recalculate = []
                cell.new_tch_count = 0
                continue

            bcch_item = self.calc_table.item(row, self.COL_RECALC_BCCH)
            cell.recalculate_bcch = (bcch_item is not None and bcch_item.checkState() == Qt.CheckState.Checked)

            tch_item = self.calc_table.item(row, self.COL_RECALC_TCH)
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

            new_item = self.calc_table.item(row, self.COL_NEW_TCH)
            try:
                cell.new_tch_count = max(0, min(20, int(new_item.text().strip()))) if new_item else 0
            except (ValueError, TypeError):
                cell.new_tch_count = 0

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

        is_sequential = (
            getattr(self, "chk_sequential_zones", None)
            and self.chk_sequential_zones.isChecked()
            and hasattr(self, "zone_map_widget")
            and bool(self.zone_map_widget.zones)
        )

        if is_sequential:
            active_zones = []
            assigned_calc_cell_ids = set()

            for z in self.zone_map_widget.zones:
                matching = [cid for cid in z.cell_ids if cid in cells_to_calc]
                if matching:
                    active_zones.append(z)
                    assigned_calc_cell_ids.update(matching)

            unassigned_cids = set(cells_to_calc.keys()) - assigned_calc_cell_ids
            if unassigned_cids:
                manual_zone = OptimizationZone(
                    zone_id="__manual_cells__",
                    name="Celdas Fuera de Zona",
                    cell_ids=unassigned_cids,
                    execution_order=len(active_zones) + 1,
                    rules=copy.deepcopy(self.rules)
                )
                active_zones.append(manual_zone)

            if not active_zones:
                QMessageBox.warning(
                    self, "Sin celdas en zonas",
                    "No se encontraron celdas a recalcular dentro de las zonas definidas."
                )
                return

            runs = self.spin_calc_num_runs.value()
            for z in active_zones:
                if not hasattr(z, "rules") or not z.rules:
                    z.rules = copy.deepcopy(self.rules)
                z.rules.num_runs = runs

            workers = getattr(self, "spin_calc_workers", None)
            max_workers = workers.value() if workers else None
            frontier_dist = self.zone_map_widget.spin_frontier.value() if hasattr(self.zone_map_widget, "spin_frontier") else 0.0

            self._baseline_cells = copy.deepcopy(self.cells)
            self._last_active_zones = active_zones

            self.btn_calculate.setEnabled(False)
            self._calc_start_time = datetime.now()
            self.lbl_progress.setText(f"⏳ Optimizando secuencialmente ({len(active_zones)} zonas, {runs} ejecuciones)...")
            self.lbl_progress.setStyleSheet(f"color: {Colors.ACCENT}; font-weight: bold;")

            self.cluster_dialog = ClusterProgressDialog(total_zones=len(active_zones), parent=self)
            self.cluster_dialog.show()

            self._cluster_thread = ClusterOptimizerThread(
                cells=self.cells,
                zones=active_zones,
                global_neighbor_graph=self.neighbor_graph,
                frontier_distance_km=frontier_dist,
                max_workers=max_workers,
                respect_cell_flags=True
            )
            self._cluster_thread.progress.connect(self.cluster_dialog.update_progress)
            self._cluster_thread.zone_completed.connect(self.cluster_dialog.log_zone_completed)
            self._cluster_thread.finished.connect(self._on_calc_sequential_finished)
            self._cluster_thread.error.connect(self._on_calc_sequential_error)
            self.cluster_dialog.cancelled.connect(self._cluster_thread.cancel)
            self._cluster_thread.start()
            return

        # Monolithic global optimization
        self._baseline_cells = copy.deepcopy(self.cells)
        self._last_active_zones = []

        self.btn_calculate.setEnabled(False)
        self._calc_start_time = datetime.now()
        self.lbl_progress.setText(f"⏳ Optimizando ({self.rules.num_runs} ejecuciones en paralelo)...")
        self.lbl_progress.setStyleSheet(f"color: {Colors.ACCENT}; font-weight: bold;")

        # Open real-time convergence dialog
        self.convergence_dialog = ConvergenceDialog(
            total_runs=self.rules.num_runs,
            total_gens=self.rules.num_generations,
            parent=self
        )
        self.convergence_dialog.cancelled.connect(self._cancel_calculation)
        self.convergence_dialog.show()

        workers = getattr(self, "spin_calc_workers", None)
        max_workers = workers.value() if workers else None

        self._optimizer_thread = OptimizerThread(
            self.cells, self.neighbor_graph, self.rules, max_workers=max_workers
        )
        self._optimizer_thread.progress.connect(self._on_optimization_progress)
        self._optimizer_thread.finished.connect(self._on_optimization_finished)
        self._optimizer_thread.error.connect(self._on_optimization_error)
        self._optimizer_thread.start()

    def _on_calc_sequential_finished(self, zone_results: dict, updated_cells: dict):
        self.btn_calculate.setEnabled(True)
        if hasattr(self, "cluster_dialog") and self.cluster_dialog:
            self.cluster_dialog.accept()
        self._on_cluster_optimization_completed(zone_results, updated_cells)
        self.lbl_progress.setText("✅ Optimización secuencial por zonas completada")
        self.lbl_progress.setStyleSheet(f"color: {Colors.ACCENT}; font-weight: bold;")

    def _on_calc_sequential_error(self, err_msg: str):
        self.btn_calculate.setEnabled(True)
        if hasattr(self, "cluster_dialog") and self.cluster_dialog:
            self.cluster_dialog.reject()
        self.lbl_progress.setText("❌ Error en optimización secuencial")
        self.lbl_progress.setStyleSheet(f"color: {Colors.ERROR}; font-weight: bold;")
        QMessageBox.critical(self, "Error de Optimización Secuencial", f"Ocurrió un error:\n{err_msg}")

    def _cancel_calculation(self):
        """Cancel the running optimization."""
        if hasattr(self, "_cluster_thread") and self._cluster_thread and self._cluster_thread.isRunning():
            self._was_cancelled = True
            self._cluster_thread.cancel()
            self.lbl_progress.setText("⏹️ Cancelando cálculo secuencial...")
            self.lbl_progress.setStyleSheet(f"color: {Colors.WARNING}; font-weight: bold;")
        elif self._optimizer_thread and self._optimizer_thread.isRunning():
            self._was_cancelled = True
            self._optimizer_thread.cancel()
            self.lbl_progress.setText("⏹️ Cancelando cálculo y recuperando resultados...")
            self.lbl_progress.setStyleSheet(f"color: {Colors.WARNING}; font-weight: bold;")

    def _on_optimization_progress(self, run_idx, total_runs, gen, total_gens, best_score):
        """Forward progress directly to the real-time convergence dialog without jittering main window."""
        if hasattr(self, "convergence_dialog") and self.convergence_dialog:
            self.convergence_dialog.update_progress(run_idx, total_runs, gen, total_gens, best_score)

    def _on_optimization_finished(self, results):
        """Handle optimization completion."""
        self.results = results
        self.btn_calculate.setEnabled(True)

        was_cancelled = getattr(self, "_was_cancelled", False)
        self._was_cancelled = False

        end_time = datetime.now()
        start_time = getattr(self, "_calc_start_time", None)

        workers = getattr(self, "spin_calc_workers", None)
        max_workers = workers.value() if workers else None

        export_folder_name = None
        if results:
            try:
                bundle_info = export_execution_bundle(
                    cells=self.cells,
                    neighbor_graph=self.neighbor_graph,
                    rules=self.rules,
                    results=results,
                    start_time=start_time,
                    end_time=end_time,
                    was_cancelled=was_cancelled,
                    workers_used=max_workers,
                )
                export_folder_name = os.path.basename(bundle_info["folder"])
            except Exception as e:
                import logging
                logging.exception("Error al auto-exportar resultados de optimización: %s", e)

        n = len(results)
        best = min((r.score for r in results), default=0)
        clean_best = format_number(best)

        save_suffix = f" | Exportado en: {export_folder_name}" if export_folder_name else ""

        if was_cancelled:
            self.lbl_progress.setText(
                f"⏹️ Cálculo detenido — Mostrando {n} mejores opciones calculadas hasta el momento "
                f"(Mejor score: {clean_best}){save_suffix}"
            )
            self.lbl_progress.setStyleSheet(f"color: {Colors.WARNING}; font-weight: bold;")
            self.status_label.setText(
                f"Cálculo cancelado: {n} planes recuperados, mejor score: {clean_best}{save_suffix}"
            )
        else:
            self.lbl_progress.setText(
                f"✅ Completado — {n} planes generados — "
                f"Mejor score: {clean_best}{save_suffix}"
            )
            self.lbl_progress.setStyleSheet(f"color: {Colors.SUCCESS};")
            self.status_label.setText(
                f"Optimización completada: {n} planes, mejor score: {clean_best}{save_suffix}"
            )

        if hasattr(self, "convergence_dialog") and self.convergence_dialog:
            self.convergence_dialog.set_finished(best, cancelled=was_cancelled, export_folder=export_folder_name)

        # Show results
        self.result_widget.load_results(results, self.cells, self.neighbor_graph, self.rules)
        self.result_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)
        self.zone_map_widget.load_data(self.cells, self.results, self.neighbor_graph, self.rules)
        self.tabs.setCurrentWidget(self.result_widget)  # Switch to Results tab

    def _on_optimization_error(self, error_msg):
        """Handle optimization error."""
        self.btn_calculate.setEnabled(True)
        self.lbl_progress.setText(f"❌ Error: {error_msg}")
        self.lbl_progress.setStyleSheet(f"color: {Colors.DANGER};")

        if hasattr(self, "convergence_dialog") and self.convergence_dialog:
            self.convergence_dialog.close()

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
            self.tabs.setCurrentWidget(self.calc_tab_widget)  # Switch to Calculation tab
            self.status_label.setText(
                f"Espectro reconfigurado a {self.rules.freq_range_start}-{self.rules.freq_range_end}"
            )
            if should_optimize:
                self._run_calculation()

    def _export_html(self):
        """Export comprehensive statistical comparison report to dynamic HTML dashboard."""
        if not self.results:
            QMessageBox.warning(
                self, "Sin resultados",
                "Debes ejecutar el cálculo de frecuencias antes de generar el informe HTML."
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Informe HTML Dinámico de Planificación",
            "informe_plan_frecuencias_dashboard.html",
            "Documentos HTML (*.html);;Todos (*)"
        )

        if not file_path:
            return

        try:
            export_plan_html(
                file_path=file_path,
                cells=self.cells,
                neighbor_graph=self.neighbor_graph,
                rules=self.rules,
                results=self.results
            )

            res = QMessageBox.information(
                self, "Informe HTML Dinámico Generado",
                f"✅ Informe HTML dinámico interactivo generado con éxito con la comparativa de escenarios por ejecución:\n\n{file_path}\n\n"
                f"¿Deseas abrir el informe en tu navegador web ahora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if res == QMessageBox.StandardButton.Yes:
                import webbrowser
                webbrowser.open("file:///" + os.path.abspath(file_path).replace("\\", "/"))

        except Exception as e:
            QMessageBox.critical(
                self, "Error al generar HTML",
                f"Se produjo un error al generar el informe HTML dinámico:\n\n{str(e)}"
            )

    def _export_pdf(self):
        """Backward compatibility alias directing to _export_html."""
        self._export_html()

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

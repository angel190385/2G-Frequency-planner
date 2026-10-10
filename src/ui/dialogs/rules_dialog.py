"""
Rules configuration dialog for co-channel and adjacent channel settings.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox, QTabWidget,
    QWidget, QGridLayout, QFrame, QLineEdit, QComboBox
)
from PyQt6.QtCore import Qt

from src.engine.constraints import FrequencyRules, STATISTICAL_MODELS
from src.ui.styles import Colors


class RulesDialog(QDialog):
    """Dialog for configuring co-channel, adjacent channel, and algorithm rules."""

    def __init__(self, rules: FrequencyRules, cells=None, results=None, parent=None):
        super().__init__(parent)
        self.rules = FrequencyRules.from_dict(rules.to_dict())  # work on a copy
        self.cells = cells or {}
        self.results = results
        self._setup_ui()
        self._load_from_rules()

    def _setup_ui(self):
        self.setWindowTitle("⚙️ Configuración de Reglas")
        self.setMinimumSize(700, 650)

        layout = QVBoxLayout(self)

        # Tabs
        tabs = QTabWidget()

        # ── Tab 1: Co-channel & Adjacent rules ─────────────────────
        rules_tab = QWidget()
        rules_layout = QVBoxLayout(rules_tab)

        # Co-channel group
        cochannel_group = QGroupBox("Cocanalidades (misma frecuencia)")
        cochannel_layout = QGridLayout(cochannel_group)

        self.chk_cochan_same_station = QCheckBox(
            "Permitir cocanalidad en misma estación")
        self.chk_cochan_bcch_bcch = QCheckBox(
            "Permitir cocanalidad BCCH-BCCH entre vecinas")
        self.chk_cochan_bcch_tch = QCheckBox(
            "Permitir cocanalidad BCCH-TCH entre vecinas")
        self.chk_cochan_tch_tch = QCheckBox(
            "Permitir cocanalidad TCH-TCH entre vecinas (hopping)")

        cochannel_layout.addWidget(self.chk_cochan_same_station, 0, 0, 1, 2)
        cochannel_layout.addWidget(self.chk_cochan_bcch_bcch, 1, 0, 1, 2)
        cochannel_layout.addWidget(self.chk_cochan_bcch_tch, 2, 0, 1, 2)
        cochannel_layout.addWidget(self.chk_cochan_tch_tch, 3, 0, 1, 2)

        lbl_note1 = QLabel(
            "⚠ Las opciones desmarcadas son restricciones DURAS "
            "(nunca se violarán)")
        lbl_note1.setObjectName("label_warning")
        lbl_note1.setWordWrap(True)
        cochannel_layout.addWidget(lbl_note1, 4, 0, 1, 2)

        rules_layout.addWidget(cochannel_group)

        # Adjacent group
        adjacent_group = QGroupBox("Adyacencias (frecuencia ±1)")
        adjacent_layout = QGridLayout(adjacent_group)

        self.chk_adj_same_station = QCheckBox(
            "Permitir adyacencia en misma estación")
        self.chk_adj_bcch_bcch = QCheckBox(
            "Permitir adyacencia BCCH-BCCH entre vecinas")
        self.chk_adj_bcch_tch = QCheckBox(
            "Permitir adyacencia BCCH-TCH entre vecinas")
        self.chk_adj_tch_tch = QCheckBox(
            "Permitir adyacencia TCH-TCH entre vecinas (hopping)")

        adjacent_layout.addWidget(self.chk_adj_same_station, 0, 0, 1, 2)
        adjacent_layout.addWidget(self.chk_adj_bcch_bcch, 1, 0, 1, 2)
        adjacent_layout.addWidget(self.chk_adj_bcch_tch, 2, 0, 1, 2)
        adjacent_layout.addWidget(self.chk_adj_tch_tch, 3, 0, 1, 2)

        rules_layout.addWidget(adjacent_group)

        # Separation group
        sep_group = QGroupBox("Separación mínima (canales)")
        sep_layout = QGridLayout(sep_group)

        sep_layout.addWidget(QLabel("Entre frecuencias de la misma celda:"), 0, 0)
        self.spin_sep_cell = QSpinBox()
        self.spin_sep_cell.setRange(0, 10)
        sep_layout.addWidget(self.spin_sep_cell, 0, 1)

        sep_layout.addWidget(QLabel("Entre frecuencias de misma estación:"), 1, 0)
        self.spin_sep_station = QSpinBox()
        self.spin_sep_station.setRange(0, 10)
        sep_layout.addWidget(self.spin_sep_station, 1, 1)

        sep_layout.addWidget(QLabel("Entre BCCHs de celdas vecinas:"), 2, 0)
        self.spin_sep_bcch = QSpinBox()
        self.spin_sep_bcch.setRange(0, 10)
        sep_layout.addWidget(self.spin_sep_bcch, 2, 1)

        rules_layout.addWidget(sep_group)
        rules_layout.addStretch()

        tabs.addTab(rules_tab, "🔒 Reglas")

        # ── Tab 2: Weights & Statistical Models ────────────────────
        weights_tab = QWidget()
        weights_layout = QVBoxLayout(weights_tab)

        # Statistical Model Group
        stat_group = QGroupBox("📊 Modelo de Cálculo Estadístico de Handovers (Tráfico)")
        stat_layout = QVBoxLayout(stat_group)

        stat_header = QHBoxLayout()
        stat_header.addWidget(QLabel("<b>Modelo de ponderación estadística:</b>"))
        self.combo_stat_model = QComboBox()
        for key, m_info in STATISTICAL_MODELS.items():
            self.combo_stat_model.addItem(m_info["name"], key)
        self.combo_stat_model.currentIndexChanged.connect(self._on_stat_model_changed)
        stat_header.addWidget(self.combo_stat_model, 1)
        stat_layout.addLayout(stat_header)

        # Informative card explaining formula and rationale
        self.card_stat_info = QFrame()
        self.card_stat_info.setStyleSheet("""
            QFrame {
                background-color: #1a1a2e;
                border: 1px solid #2a2a4e;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        card_layout = QVBoxLayout(self.card_stat_info)
        self.lbl_stat_formula = QLabel()
        self.lbl_stat_formula.setStyleSheet("color: #00d4aa; font-weight: bold; font-size: 13px;")
        self.lbl_stat_desc = QLabel()
        self.lbl_stat_desc.setWordWrap(True)
        self.lbl_stat_desc.setStyleSheet("color: #e0e0e0; font-size: 12px;")
        self.lbl_stat_usage = QLabel()
        self.lbl_stat_usage.setWordWrap(True)
        self.lbl_stat_usage.setStyleSheet("color: #ffa502; font-size: 11px;")

        card_layout.addWidget(self.lbl_stat_formula)
        card_layout.addWidget(self.lbl_stat_desc)
        card_layout.addWidget(self.lbl_stat_usage)
        stat_layout.addWidget(self.card_stat_info)

        weights_layout.addWidget(stat_group)

        weights_group = QGroupBox("Pesos base de la función de coste")
        wg_layout = QGridLayout(weights_group)

        weight_labels = [
            ("Cocanalidad BCCH-BCCH:", "weight_cochannel_bcch_bcch"),
            ("Cocanalidad BCCH-TCH:", "weight_cochannel_bcch_tch"),
            ("Cocanalidad TCH-TCH:", "weight_cochannel_tch_tch"),
            ("Adyacencia BCCH-BCCH:", "weight_adjacent_bcch_bcch"),
            ("Adyacencia BCCH-TCH:", "weight_adjacent_bcch_tch"),
            ("Adyacencia TCH-TCH:", "weight_adjacent_tch_tch"),
            ("Cocanalidad misma estación:", "weight_same_station_cochannel"),
            ("Adyacencia misma estación:", "weight_same_station_adjacent"),
        ]

        self.weight_spins = {}
        for row, (label, attr) in enumerate(weight_labels):
            wg_layout.addWidget(QLabel(label), row, 0)
            spin = QDoubleSpinBox()
            spin.setRange(0, 1000)
            spin.setDecimals(1)
            spin.setSingleStep(5)
            self.weight_spins[attr] = spin
            wg_layout.addWidget(spin, row, 1)

        weights_layout.addWidget(weights_group)

        # Distance penalty
        dist_group = QGroupBox("Penalización por distancia")
        dist_layout = QGridLayout(dist_group)

        self.chk_distance = QCheckBox("Activar penalización por distancia")
        dist_layout.addWidget(self.chk_distance, 0, 0, 1, 2)

        dist_layout.addWidget(QLabel("Umbral de distancia (km):"), 1, 0)
        self.spin_dist_threshold = QDoubleSpinBox()
        self.spin_dist_threshold.setRange(0.1, 50.0)
        self.spin_dist_threshold.setDecimals(1)
        self.spin_dist_threshold.setSingleStep(0.5)
        dist_layout.addWidget(self.spin_dist_threshold, 1, 1)

        dist_layout.addWidget(QLabel("Peso de distancia:"), 2, 0)
        self.spin_dist_weight = QDoubleSpinBox()
        self.spin_dist_weight.setRange(0, 500)
        self.spin_dist_weight.setDecimals(1)
        dist_layout.addWidget(self.spin_dist_weight, 2, 1)

        weights_layout.addWidget(dist_group)
        weights_layout.addStretch()

        tabs.addTab(weights_tab, "⚖️ Pesos & Estadísticas")

        # ── Tab 3: Frequency Range ─────────────────────────────────
        freq_tab = QWidget()
        freq_layout = QVBoxLayout(freq_tab)

        range_group = QGroupBox("Rango de frecuencias (ARFCN)")
        range_layout = QGridLayout(range_group)

        range_layout.addWidget(QLabel("ARFCN mínimo:"), 0, 0)
        self.spin_freq_start = QSpinBox()
        self.spin_freq_start.setRange(0, 1023)
        range_layout.addWidget(self.spin_freq_start, 0, 1)

        range_layout.addWidget(QLabel("ARFCN máximo:"), 1, 0)
        self.spin_freq_end = QSpinBox()
        self.spin_freq_end.setRange(0, 1023)
        range_layout.addWidget(self.spin_freq_end, 1, 1)

        btn_detect = QPushButton("🔍 Detectar Rango del Plan (BCCH + TCH)")
        btn_detect.setToolTip("Detectar rango mínimo y máximo usado en todas las celdas (BCCH y TCH)")
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
        btn_detect.clicked.connect(self._detect_range_from_plan)
        range_layout.addWidget(btn_detect, 2, 0, 1, 2)

        self.lbl_detect_range_status = QLabel("")
        self.lbl_detect_range_status.setStyleSheet("color: #00d4aa; font-size: 11px;")
        range_layout.addWidget(self.lbl_detect_range_status, 3, 0, 1, 2)

        range_layout.addWidget(QLabel("Frecuencias excluidas (separadas por coma):"), 4, 0, 1, 2)
        self.txt_excluded = QLineEdit()
        self.txt_excluded.setPlaceholderText("Ej: 50, 75, 100")
        range_layout.addWidget(self.txt_excluded, 5, 0, 1, 2)

        freq_layout.addWidget(range_group)

        # Available pool preview
        pool_group = QGroupBox("Pool de frecuencias disponible")
        pool_layout = QVBoxLayout(pool_group)
        self.lbl_pool_count = QLabel("")
        pool_layout.addWidget(self.lbl_pool_count)
        freq_layout.addWidget(pool_group)

        self.spin_freq_start.valueChanged.connect(self._update_pool_preview)
        self.spin_freq_end.valueChanged.connect(self._update_pool_preview)
        self.txt_excluded.textChanged.connect(self._update_pool_preview)

        freq_layout.addStretch()
        tabs.addTab(freq_tab, "📻 Frecuencias")

        # ── Tab 4: Algorithm parameters ────────────────────────────
        algo_tab = QWidget()
        algo_layout = QVBoxLayout(algo_tab)

        algo_group = QGroupBox("Parámetros del Algoritmo Genético")
        ag_layout = QGridLayout(algo_group)

        ag_layout.addWidget(QLabel("Tamaño de población:"), 0, 0)
        self.spin_pop = QSpinBox()
        self.spin_pop.setRange(20, 2000)
        self.spin_pop.setSingleStep(50)
        ag_layout.addWidget(self.spin_pop, 0, 1)

        ag_layout.addWidget(QLabel("Número de generaciones:"), 1, 0)
        self.spin_gen = QSpinBox()
        self.spin_gen.setRange(50, 5000)
        self.spin_gen.setSingleStep(100)
        ag_layout.addWidget(self.spin_gen, 1, 1)

        ag_layout.addWidget(QLabel("Tasa de crossover:"), 2, 0)
        self.spin_crossover = QDoubleSpinBox()
        self.spin_crossover.setRange(0.0, 1.0)
        self.spin_crossover.setDecimals(2)
        self.spin_crossover.setSingleStep(0.05)
        ag_layout.addWidget(self.spin_crossover, 2, 1)

        ag_layout.addWidget(QLabel("Tasa de mutación:"), 3, 0)
        self.spin_mutation = QDoubleSpinBox()
        self.spin_mutation.setRange(0.0, 1.0)
        self.spin_mutation.setDecimals(2)
        self.spin_mutation.setSingleStep(0.01)
        ag_layout.addWidget(self.spin_mutation, 3, 1)

        ag_layout.addWidget(QLabel("Élite (individuos preservados):"), 4, 0)
        self.spin_elite = QSpinBox()
        self.spin_elite.setRange(1, 100)
        ag_layout.addWidget(self.spin_elite, 4, 1)

        ag_layout.addWidget(QLabel("Top-K resultados a mostrar:"), 5, 0)
        self.spin_topk = QSpinBox()
        self.spin_topk.setRange(1, 20)
        ag_layout.addWidget(self.spin_topk, 5, 1)

        ag_layout.addWidget(QLabel("Número de ejecuciones (pasadas):"), 6, 0)
        self.spin_num_runs = QSpinBox()
        self.spin_num_runs.setRange(1, 20)
        ag_layout.addWidget(self.spin_num_runs, 6, 1)

        algo_layout.addWidget(algo_group)

        # Performance note
        lbl_perf = QLabel(
            "💡 Más población y generaciones = mejor resultado pero mayor tiempo.\n"
            "Para ~20 celdas: 200 pop × 500 gen ≈ 10-30 seg.\n"
            "Para ~100 celdas: 500 pop × 1000 gen ≈ 2-5 min."
        )
        lbl_perf.setObjectName("label_subtitle")
        lbl_perf.setWordWrap(True)
        algo_layout.addWidget(lbl_perf)

        algo_layout.addStretch()
        tabs.addTab(algo_tab, "🧬 Algoritmo")

        layout.addWidget(tabs)

        # Buttons
        btn_layout = QHBoxLayout()

        btn_defaults = QPushButton("Restaurar valores por defecto")
        btn_defaults.clicked.connect(self._restore_defaults)
        btn_layout.addWidget(btn_defaults)

        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_apply = QPushButton("Aplicar")
        btn_apply.setObjectName("btn_primary")
        btn_apply.clicked.connect(self._apply_and_accept)
        btn_layout.addWidget(btn_apply)

        layout.addLayout(btn_layout)

    def _load_from_rules(self):
        """Load current rules into the UI."""
        r = self.rules

        # Co-channel
        self.chk_cochan_same_station.setChecked(r.allow_cochannel_same_station)
        self.chk_cochan_bcch_bcch.setChecked(r.allow_cochannel_bcch_bcch_neighbors)
        self.chk_cochan_bcch_tch.setChecked(r.allow_cochannel_bcch_tch_neighbors)
        self.chk_cochan_tch_tch.setChecked(r.allow_cochannel_tch_tch_neighbors)

        # Adjacent
        self.chk_adj_same_station.setChecked(r.allow_adjacent_same_station)
        self.chk_adj_bcch_bcch.setChecked(r.allow_adjacent_bcch_bcch_neighbors)
        self.chk_adj_bcch_tch.setChecked(r.allow_adjacent_bcch_tch_neighbors)
        self.chk_adj_tch_tch.setChecked(r.allow_adjacent_tch_tch_neighbors)

        # Separation
        self.spin_sep_cell.setValue(r.min_separation_same_cell)
        self.spin_sep_station.setValue(r.min_separation_same_station)
        self.spin_sep_bcch.setValue(r.min_separation_bcch_neighbors)

        # Statistical model
        curr_model = getattr(r, "ho_statistical_model", "logarithmic")
        idx = self.combo_stat_model.findData(curr_model)
        if idx >= 0:
            self.combo_stat_model.setCurrentIndex(idx)
        self._on_stat_model_changed()

        # Weights
        for attr, spin in self.weight_spins.items():
            spin.setValue(getattr(r, attr))

        # Distance
        self.chk_distance.setChecked(r.enable_distance_penalty)
        self.spin_dist_threshold.setValue(r.distance_threshold_km)
        self.spin_dist_weight.setValue(r.weight_distance)

        # Frequency range
        self.spin_freq_start.setValue(r.freq_range_start)
        self.spin_freq_end.setValue(r.freq_range_end)
        if r.excluded_frequencies:
            self.txt_excluded.setText(
                ", ".join(str(f) for f in r.excluded_frequencies))

        # Algorithm
        self.spin_pop.setValue(r.population_size)
        self.spin_gen.setValue(r.num_generations)
        self.spin_crossover.setValue(r.crossover_rate)
        self.spin_mutation.setValue(r.mutation_rate)
        self.spin_elite.setValue(r.elite_count)
        self.spin_topk.setValue(r.top_k_results)
        self.spin_num_runs.setValue(r.num_runs)

        self._update_pool_preview()

    def _on_stat_model_changed(self):
        """Update informative labels when the statistical model changes."""
        key = self.combo_stat_model.currentData() or "logarithmic"
        info = STATISTICAL_MODELS.get(key, STATISTICAL_MODELS["logarithmic"])
        self.lbl_stat_formula.setText(f"📐 Fórmula: {info['formula']}")
        self.lbl_stat_desc.setText(f"ℹ️ {info['description']}")
        self.lbl_stat_usage.setText(f"💡 {info['usage']}")

    def _detect_range_from_plan(self):
        """Auto-detect minimum and maximum ARFCN across all BCCH and TCH channels."""
        if not self.cells:
            self.lbl_detect_range_status.setText("⚠ No hay celdas cargadas para detectar el rango.")
            self.lbl_detect_range_status.setStyleSheet("color: #ffa502; font-size: 11px;")
            return

        from src.engine.spectrum_manager import detect_current_spectrum_range
        det_min, det_max, details = detect_current_spectrum_range(self.cells, self.results)

        self.spin_freq_start.blockSignals(True)
        self.spin_freq_end.blockSignals(True)

        self.spin_freq_start.setValue(det_min)
        self.spin_freq_end.setValue(det_max)

        self.spin_freq_start.blockSignals(False)
        self.spin_freq_end.blockSignals(False)

        self._update_pool_preview()

        b_min = details.get("bcch_min")
        b_max = details.get("bcch_max")
        t_min = details.get("tch_min")
        t_max = details.get("tch_max")
        n_bcch = details.get("total_bcch_channels", 0)
        n_tch = details.get("total_tch_channels", 0)

        bcch_str = f"{b_min}..{b_max}" if b_min is not None else "—"
        tch_str = f"{t_min}..{t_max}" if t_min is not None else "—"

        self.lbl_detect_range_status.setText(
            f"✅ Rango detectado: ARFCN {det_min} a {det_max} "
            f"(BCCH [{n_bcch}]: {bcch_str} | TCH [{n_tch}]: {tch_str})"
        )
        self.lbl_detect_range_status.setStyleSheet("color: #00d4aa; font-weight: bold; font-size: 11px;")

    def _update_pool_preview(self):
        """Update the frequency pool count preview."""
        start = self.spin_freq_start.value()
        end = self.spin_freq_end.value()
        excluded = self._parse_excluded()
        count = sum(1 for f in range(start, end + 1) if f not in excluded)
        self.lbl_pool_count.setText(
            f"Frecuencias disponibles: {count} "
            f"(de {start} a {end}, "
            f"{len(excluded)} excluidas)")

    def _parse_excluded(self) -> set[int]:
        """Parse excluded frequencies from text field."""
        text = self.txt_excluded.text().strip()
        if not text:
            return set()
        excluded = set()
        for part in text.split(','):
            part = part.strip()
            if part:
                try:
                    excluded.add(int(part))
                except ValueError:
                    pass
        return excluded

    def _apply_and_accept(self):
        """Save UI values back to rules and accept."""
        r = self.rules

        # Co-channel
        r.allow_cochannel_same_station = self.chk_cochan_same_station.isChecked()
        r.allow_cochannel_bcch_bcch_neighbors = self.chk_cochan_bcch_bcch.isChecked()
        r.allow_cochannel_bcch_tch_neighbors = self.chk_cochan_bcch_tch.isChecked()
        r.allow_cochannel_tch_tch_neighbors = self.chk_cochan_tch_tch.isChecked()

        # Adjacent
        r.allow_adjacent_same_station = self.chk_adj_same_station.isChecked()
        r.allow_adjacent_bcch_bcch_neighbors = self.chk_adj_bcch_bcch.isChecked()
        r.allow_adjacent_bcch_tch_neighbors = self.chk_adj_bcch_tch.isChecked()
        r.allow_adjacent_tch_tch_neighbors = self.chk_adj_tch_tch.isChecked()

        # Separation
        r.min_separation_same_cell = self.spin_sep_cell.value()
        r.min_separation_same_station = self.spin_sep_station.value()
        r.min_separation_bcch_neighbors = self.spin_sep_bcch.value()

        # Statistical model
        r.ho_statistical_model = self.combo_stat_model.currentData() or "logarithmic"

        # Weights
        for attr, spin in self.weight_spins.items():
            setattr(r, attr, spin.value())

        # Distance
        r.enable_distance_penalty = self.chk_distance.isChecked()
        r.distance_threshold_km = self.spin_dist_threshold.value()
        r.weight_distance = self.spin_dist_weight.value()

        # Frequency range
        r.freq_range_start = self.spin_freq_start.value()
        r.freq_range_end = self.spin_freq_end.value()
        r.excluded_frequencies = list(self._parse_excluded())

        # Algorithm
        r.population_size = self.spin_pop.value()
        r.num_generations = self.spin_gen.value()
        r.crossover_rate = self.spin_crossover.value()
        r.mutation_rate = self.spin_mutation.value()
        r.elite_count = self.spin_elite.value()
        r.top_k_results = self.spin_topk.value()
        r.num_runs = self.spin_num_runs.value()

        self.accept()

    def _restore_defaults(self):
        """Restore all rules to default values."""
        self.rules = FrequencyRules()
        self._load_from_rules()

    def get_rules(self) -> FrequencyRules:
        """Return the configured rules."""
        return self.rules

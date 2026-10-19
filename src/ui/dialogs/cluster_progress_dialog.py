"""
Progress dialog for sequential multi-zone cluster optimization.
Displays zone-by-zone progress, current generation, best score, and status log.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QProgressBar, QTextEdit, QGroupBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from src.engine.cluster_optimizer import ZoneOptimizationProgress
from src.models.frequency_plan import format_number
from src.ui.styles import Colors

RUN_COLORS = [
    "#00d4aa",  # Cyan/Teal
    "#38bdf8",  # Sky Blue
    "#fb923c",  # Orange
    "#a855f7",  # Purple
    "#f43f5e",  # Rose
    "#10b981",  # Emerald
    "#eab308",  # Yellow
    "#ec4899",  # Pink
    "#6366f1",  # Indigo
]


class ClusterProgressDialog(QDialog):
    """Dialog showing live sequential progress across multiple optimization zones."""
    cancelled = pyqtSignal()

    def __init__(self, total_zones: int, parent=None):
        super().__init__(parent)
        self.total_zones = max(1, total_zones)
        self._completed_zones_count = 0
        self._current_zone_index: int = -1
        self._zone_histories: dict[int, tuple[list[int], list[float]]] = {}
        self._lines: dict[int, object] = {}
        self.setWindowTitle("Optimización Secuencial por Zonas")
        self.setMinimumSize(640, 560)
        self.resize(700, 620)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header card
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #16213e, stop:1 #0f3460);
                border: 1px solid #1f4068;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        hf_layout = QVBoxLayout(header_frame)

        self.lbl_zone_title = QLabel("⏳ Inicializando optimización por zonas...")
        self.lbl_zone_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #00d4aa;")
        hf_layout.addWidget(self.lbl_zone_title)

        self.lbl_zone_sub = QLabel("Preparando celdas activas y fronteras de protección...")
        self.lbl_zone_sub.setStyleSheet("font-size: 12px; color: #b0b0c0;")
        hf_layout.addWidget(self.lbl_zone_sub)

        layout.addWidget(header_frame)

        # Overall progress
        zona_lbl = "Zona" if self.total_zones == 1 else "Zonas"
        self.grp_overall = QGroupBox(f"Progreso Global ({self.total_zones} {zona_lbl})")
        og_layout = QVBoxLayout(self.grp_overall)
        self.bar_overall = QProgressBar()
        self.bar_overall.setRange(0, 100)
        self.bar_overall.setValue(0)
        self.bar_overall.setFormat(f"0% (0 de {self.total_zones} {zona_lbl} realizadas)")
        self.bar_overall.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #334155;
                border-radius: 4px;
                text-align: center;
                background: #0f172a;
                color: #ffffff;
                font-weight: bold;
                height: 18px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00d4aa, stop:1 #38bdf8);
                border-radius: 3px;
            }}
        """)
        og_layout.addWidget(self.bar_overall)
        layout.addWidget(self.grp_overall)

        # Current zone progress
        self.grp_current = QGroupBox("Zona Activa")
        cg_layout = QVBoxLayout(self.grp_current)

        row_info = QHBoxLayout()
        self.lbl_run_gen = QLabel("Generación: 0 / 0 | Run: 1 / 1")
        self.lbl_run_gen.setStyleSheet("color: #e0e0e8; font-weight: 500;")
        row_info.addWidget(self.lbl_run_gen)

        row_info.addStretch()
        self.lbl_best_score = QLabel("Mejor Coste: —")
        self.lbl_best_score.setStyleSheet("color: #ff007f; font-weight: bold; font-size: 13px;")
        row_info.addWidget(self.lbl_best_score)
        cg_layout.addLayout(row_info)

        self.bar_current = QProgressBar()
        self.bar_current.setRange(0, 100)
        self.bar_current.setValue(0)
        self.bar_current.setFormat("0%")
        self.bar_current.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #334155;
                border-radius: 4px;
                text-align: center;
                background: #0f172a;
                color: #ffffff;
                height: 16px;
            }}
            QProgressBar::chunk {{
                background-color: #ff007f;
                border-radius: 3px;
            }}
        """)
        cg_layout.addWidget(self.bar_current)

        # Compact Convergence Graph for the active zone
        plot_frame = QFrame()
        plot_frame.setStyleSheet("""
            QFrame {
                background-color: #0c1017;
                border: 1px solid #1e293b;
                border-radius: 6px;
            }
        """)
        pf_layout = QVBoxLayout(plot_frame)
        pf_layout.setContentsMargins(4, 4, 4, 4)

        self.figure = Figure(facecolor="#0c1017", layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(150)
        self.canvas.setStyleSheet("background: transparent;")
        pf_layout.addWidget(self.canvas)
        cg_layout.addWidget(plot_frame)

        self._init_plot()
        layout.addWidget(self.grp_current)

        # Log
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #0b1120; font-family: 'Consolas', monospace; font-size: 11px;")
        self.txt_log.setMaximumHeight(85)
        layout.addWidget(self.txt_log)

        # Footer button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("⏹️ Cancelar Optimización")
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 16px;
            }
            QPushButton:hover { background-color: #dc2626; }
        """)
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _init_plot(self):
        """Initialize the compact convergence subplot."""
        self.figure.clear()
        self.ax = self.figure.add_subplot(1, 1, 1)
        self.ax.set_facecolor("#070d19")
        self.ax.set_title("Convergencia: Esperando inicio...", color="#00d4aa", fontsize=9, fontweight="bold", pad=4)
        self.ax.set_xlabel("Generación", color="#94a3b8", fontsize=8, labelpad=2)
        self.ax.set_ylabel("Coste", color="#94a3b8", fontsize=8, labelpad=2)
        self.ax.grid(True, linestyle="--", alpha=0.25, color="#1e293b")
        self.ax.tick_params(axis="both", which="major", colors="#8b9bb4", labelsize=8, pad=2)
        for spine in self.ax.spines.values():
            spine.set_color("#1e293b")
        self.canvas.draw_idle()

    def update_progress(self, p: ZoneOptimizationProgress):
        """Update live status and convergence plot for the active zone."""
        self.lbl_zone_title.setText(f"🚀 Optimizando Zona {p.zone_index} de {p.total_zones}: {p.zone_name}")
        self.lbl_zone_sub.setText(f"Run {p.current_run} de {p.total_runs} — Generación {p.current_gen} de {p.total_gens}")
        self.lbl_run_gen.setText(f"Generación: {p.current_gen} / {p.total_gens} | Run: {p.current_run} / {p.total_runs}")

        sc_str = format_number(p.best_score)
        self.lbl_best_score.setText(f"Mejor Coste: {sc_str}")

        # 1. Barra de generación (debajo de Zona Activa):
        # Muestra la generación actual sobre el total de generaciones (ej: 44 de 500)
        gen_pct = min(100, max(0, int((p.current_gen / max(1, p.total_gens)) * 100)))
        self.bar_current.setValue(gen_pct)
        self.bar_current.setFormat(f"{gen_pct}% ({p.current_gen} / {p.total_gens} Generaciones)")

        # 2. Barra de progreso global:
        # Muestra el avance y las zonas realizadas sobre el total de zonas
        zone_fraction = (p.current_run - 1 + (p.current_gen / max(1, p.total_gens))) / max(1, p.total_runs)
        zones_progress = (p.zone_index - 1) + zone_fraction
        overall_pct = min(100, max(0, int((zones_progress / max(1, p.total_zones)) * 100)))
        self.bar_overall.setValue(overall_pct)

        zona_lbl = "Zona" if p.total_zones == 1 else "Zonas"
        if hasattr(self, "grp_overall"):
            self.grp_overall.setTitle(f"Progreso Global ({p.total_zones} {zona_lbl})")

        if hasattr(self, "grp_current"):
            self.grp_current.setTitle(f"Zona Activa: {p.zone_name}")

        zona_str = "Zona realizada" if p.total_zones == 1 else "Zonas realizadas"
        self.bar_overall.setFormat(
            f"{overall_pct}% ({self._completed_zones_count} de {p.total_zones} {zona_str})"
        )

        # ── Live Miniature Convergence Plot ──
        if p.zone_index != self._current_zone_index:
            self._current_zone_index = p.zone_index
            self._zone_histories.clear()
            self._lines.clear()
            self.ax.cla()
            self.ax.set_facecolor("#070d19")
            self.ax.set_title(f"Convergencia: {p.zone_name}", color="#00d4aa", fontsize=9, fontweight="bold", pad=4)
            self.ax.set_xlabel("Generación", color="#94a3b8", fontsize=8, labelpad=2)
            self.ax.set_ylabel("Coste", color="#94a3b8", fontsize=8, labelpad=2)
            self.ax.grid(True, linestyle="--", alpha=0.25, color="#1e293b")
            self.ax.tick_params(axis="both", which="major", colors="#8b9bb4", labelsize=8, pad=2)
            for spine in self.ax.spines.values():
                spine.set_color("#1e293b")

        x_vals, y_vals = self._zone_histories.setdefault(p.current_run, ([], []))
        x_vals.append(p.current_gen)
        y_vals.append(p.best_score)

        if p.current_run not in self._lines:
            c = RUN_COLORS[(p.current_run - 1) % len(RUN_COLORS)]
            line, = self.ax.plot(
                [], [],
                color=c,
                linewidth=1.8,
                label=f"Run {p.current_run}" if p.total_runs > 1 else "Convergencia",
                alpha=0.9
            )
            self._lines[p.current_run] = line
            if p.total_runs > 1:
                self.ax.legend(
                    facecolor="#0b1120",
                    edgecolor="#1e293b",
                    labelcolor="#f0f4f8",
                    fontsize=7,
                    loc="upper right"
                )

        line = self._lines[p.current_run]
        line.set_data(x_vals, y_vals)
        max_x = max(p.total_gens, max(x_vals) if x_vals else p.total_gens, 1)
        self.ax.set_xlim(0, max_x)
        self.ax.relim()
        self.ax.autoscale_view(scalex=False, scaley=True)
        self.canvas.draw_idle()

    def log_zone_completed(self, zone_name: str, score: float, cell_count: int):
        """Append log message when a zone finishes."""
        if cell_count > 0:
            sc_str = format_number(score)
            self.txt_log.append(f"✅ [{zone_name}] completada: {cell_count} celdas optimizadas | Score: {sc_str}")
        else:
            self.txt_log.append(f"⏭️ [{zone_name}] omitida (sin celdas a recalcular)")
        self._completed_zones_count += 1
        pct = min(100, int((self._completed_zones_count / max(1, self.total_zones)) * 100))
        self.bar_overall.setValue(pct)
        zona_str = "Zona realizada" if self.total_zones == 1 else "Zonas realizadas"
        if self._completed_zones_count >= self.total_zones:
            self.bar_overall.setFormat(f"100% ({self.total_zones} de {self.total_zones} {zona_str})")
            self.bar_current.setValue(100)
            self.bar_current.setFormat("100% (Finalizado)")
        else:
            self.bar_overall.setFormat(f"{pct}% ({self._completed_zones_count} de {self.total_zones} {zona_str})")

    def log_message(self, msg: str):
        self.txt_log.append(msg)

    def _on_cancel(self):
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelando...")
        self.cancelled.emit()

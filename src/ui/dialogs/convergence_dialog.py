"""
Real-time convergence dialog displaying iteration vs cost curves during optimization.
Supports single and multi-run visualizations with automatic subplots and dark theme.
"""
from __future__ import annotations
import time
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QProgressBar, QWidget, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.ticker import FuncFormatter

from src.models.frequency_plan import format_number
from src.ui.styles import Colors


def _axis_formatter(val, pos):
    """Format plot axis values cleanly without trailing .0."""
    try:
        f = float(val)
        if f.is_integer():
            return str(int(f))
        s = f"{f:.1f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    except Exception:
        return str(val)


class ConvergenceDialog(QDialog):
    """
    Dialog displaying live iteration vs cost plots during optimization.
    """
    cancelled = pyqtSignal()

    def __init__(self, total_runs: int, total_gens: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.total_runs = max(1, total_runs)
        self.total_gens = max(1, total_gens)

        # History tracking per run: run_idx -> (list of x, list of y)
        self.histories: dict[int, tuple[list[int], list[float]]] = {
            r: ([], []) for r in range(1, self.total_runs + 1)
        }
        self.start_time = time.time()
        self.is_finished = False

        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        self.setWindowTitle("📈 Progreso de la Optimización — Iteración vs Coste")
        self.setMinimumSize(850, 580)
        self.resize(1000, 680)

        # Dark theme dialog styling
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {Colors.BG_PRIMARY};
                color: {Colors.TEXT};
            }}
            QLabel {{
                color: {Colors.TEXT};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 10px;
            }}
        """)
        hf_layout = QVBoxLayout(header_frame)
        hf_layout.setContentsMargins(10, 8, 10, 8)
        hf_layout.setSpacing(6)

        top_row = QHBoxLayout()
        self.lbl_title = QLabel("🚀 Optimizando Frecuencias...")
        self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Colors.ACCENT};")
        top_row.addWidget(self.lbl_title)
        top_row.addStretch()

        self.lbl_timer = QLabel("⏱️ 00:00")
        self.lbl_timer.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_DIM};")
        top_row.addWidget(self.lbl_timer)
        hf_layout.addLayout(top_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self.total_runs * self.total_gens)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colors.BG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                height: 18px;
                text-align: center;
                color: {Colors.TEXT};
                font-size: 11px;
                font-weight: bold;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ACCENT};
                border-radius: 3px;
            }}
        """)
        hf_layout.addWidget(self.progress_bar)

        bottom_row = QHBoxLayout()
        self.lbl_status = QLabel("Iniciando cálculo...")
        self.lbl_status.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_DIM};")
        bottom_row.addWidget(self.lbl_status)
        bottom_row.addStretch()

        self.lbl_best = QLabel("Mejor coste: —")
        self.lbl_best.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.SUCCESS};")
        bottom_row.addWidget(self.lbl_best)
        hf_layout.addLayout(bottom_row)

        layout.addWidget(header_frame)

        # ── Matplotlib Plots ─────────────────────────────────────
        self.figure = Figure(facecolor="#141a24")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setStyleSheet("background: transparent;")
        layout.addWidget(self.canvas, 1)

        self._init_plots()

        # ── Footer ───────────────────────────────────────────────
        footer_layout = QHBoxLayout()
        self.lbl_cpu_info = QLabel("⚡ Multinúcleo activo — 100% CPU")
        self.lbl_cpu_info.setStyleSheet(f"color: {Colors.ACCENT_BLUE}; font-size: 11px;")
        footer_layout.addWidget(self.lbl_cpu_info)

        footer_layout.addStretch()

        self.btn_action = QPushButton("⛔ Cancelar cálculo")
        self.btn_action.setObjectName("btn_danger")
        self.btn_action.setMinimumWidth(160)
        self.btn_action.setStyleSheet("""
            QPushButton {
                background-color: #d63031;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #e17055; }
        """)
        self.btn_action.clicked.connect(self._on_action_clicked)
        footer_layout.addWidget(self.btn_action)

        layout.addLayout(footer_layout)

    def _init_plots(self):
        """Initialize Matplotlib subplots based on total_runs."""
        self.figure.clear()

        # Grid calculation
        if self.total_runs == 1:
            rows, cols = 1, 1
        elif self.total_runs == 2:
            rows, cols = 1, 2
        elif self.total_runs == 3:
            rows, cols = 1, 3
        elif self.total_runs <= 4:
            rows, cols = 2, 2
        elif self.total_runs <= 6:
            rows, cols = 2, 3
        else:
            cols = 3
            rows = (self.total_runs + cols - 1) // cols

        self.axes = []
        self.lines = {}

        colors = ["#00d4aa", "#38bdf8", "#fb923c", "#a855f7", "#f43f5e", "#10b981", "#eab308"]

        formatter = FuncFormatter(_axis_formatter)

        for r in range(1, self.total_runs + 1):
            ax = self.figure.add_subplot(rows, cols, r)
            ax.set_facecolor("#0c1017")
            ax.set_title(f"Ejecución {r}" if self.total_runs > 1 else "Convergencia de la Optimización",
                         color="#f0f4f8", fontsize=11, fontweight="bold", pad=8)
            ax.set_xlabel("Iteración", color="#8b9bb4", fontsize=10)
            ax.set_ylabel("Coste (Score)", color="#8b9bb4", fontsize=10)
            ax.grid(True, linestyle="--", alpha=0.3, color="#2b384e")
            ax.tick_params(colors="#8b9bb4", labelsize=9)

            for spine in ax.spines.values():
                spine.set_color("#2b384e")

            ax.xaxis.set_major_formatter(formatter)
            ax.yaxis.set_major_formatter(formatter)

            c = colors[(r - 1) % len(colors)]
            line, = ax.plot([], [], color=c, linewidth=2.0, marker="o", markersize=3,
                            markerfacecolor=c, markeredgecolor=c, label=f"Ejec. {r}")
            self.lines[r] = line
            self.axes.append(ax)

        self.figure.tight_layout(pad=2.0)
        self.canvas.draw_idle()

    def _setup_timer(self):
        """Update elapsed time every second."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_timer)
        self.timer.start(1000)

    def _update_timer(self):
        if not self.is_finished:
            elapsed = int(time.time() - self.start_time)
            m, s = divmod(elapsed, 60)
            self.lbl_timer.setText(f"⏱️ {m:02d}:{s:02d}")

    def update_progress(self, run_idx: int, total_runs: int, gen: int, total_gens: int, best_score: float):
        """Slot called on optimization progress."""
        if self.is_finished:
            return

        # Record history
        x_vals, y_vals = self.histories.setdefault(run_idx, ([], []))
        x_vals.append(gen)
        y_vals.append(best_score)

        # Update line
        if run_idx in self.lines:
            line = self.lines[run_idx]
            line.set_data(x_vals, y_vals)

            ax = self.axes[run_idx - 1]
            ax.relim()
            ax.autoscale_view()

        # Update progress bar
        completed_steps = sum(len(x) for x, _ in self.histories.values())
        self.progress_bar.setValue(min(self.progress_bar.maximum(), completed_steps))

        # Update labels with clean numbers (no .0)
        clean_score = format_number(best_score)
        self.lbl_status.setText(f"Ejecución {run_idx}/{total_runs} — Iteración {gen}/{total_gens}")
        self.lbl_best.setText(f"Mejor coste: {clean_score}")

        self.canvas.draw_idle()

    def set_finished(self, best_score: Optional[float] = None):
        """Called when optimization completes."""
        self.is_finished = True
        self.timer.stop()
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.lbl_title.setText("✅ Optimización Completada con Éxito")
        self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Colors.SUCCESS};")

        clean_score = format_number(best_score) if best_score is not None else "—"
        self.lbl_best.setText(f"🏆 Score Óptimo: {clean_score}")
        self.lbl_status.setText("Cálculo finalizado. Revisa las curvas de evolución.")

        self.btn_action.setText("✓ Ver Resultados y Cerrar")
        self.btn_action.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: #0c1017;
                font-weight: bold;
                padding: 8px 16px;
                border: none;
                border-radius: 5px;
            }}
            QPushButton:hover {{ background-color: #00b894; }}
        """)

        # Redraw final plots with proper bounds
        for ax in self.axes:
            ax.relim()
            ax.autoscale_view()
        self.canvas.draw_idle()

    def _on_action_clicked(self):
        if not self.is_finished:
            self.cancelled.emit()
            self.lbl_status.setText("Cancelando cálculo...")
            self.btn_action.setEnabled(False)
        else:
            self.accept()

    def closeEvent(self, event):
        if not self.is_finished:
            self.cancelled.emit()
        super().closeEvent(event)

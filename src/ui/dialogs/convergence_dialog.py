"""
Real-time convergence dialog displaying iteration vs cost curves during optimization.
Supports single and multi-run visualizations with automatic subplots and dark theme.
"""
from __future__ import annotations
import time
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QProgressBar, QWidget, QFrame, QSizePolicy
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

RUN_COLORS = [
    "#00d4aa",  # Cyan/Teal (Ejecución 1)
    "#38bdf8",  # Sky Blue (Ejecución 2)
    "#fb923c",  # Orange (Ejecución 3)
    "#a855f7",  # Purple (Ejecución 4)
    "#f43f5e",  # Rose (Ejecución 5)
    "#10b981",  # Emerald (Ejecución 6)
    "#eab308",  # Yellow (Ejecución 7)
    "#ec4899",  # Pink (Ejecución 8)
    "#6366f1",  # Indigo (Ejecución 9)
]


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
    RUN_COLORS = RUN_COLORS

    @classmethod
    def get_run_color(cls, run_idx: int) -> str:
        return cls.RUN_COLORS[(run_idx - 1) % len(cls.RUN_COLORS)]

    def __init__(self, total_runs: int, total_gens: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.total_runs = max(1, total_runs)
        self.total_gens = max(1, total_gens)

        self.global_best_score: float = float("inf")
        self.current_gens: dict[int, int] = {r: 0 for r in range(1, self.total_runs + 1)}
        self.run_best_scores: dict[int, float] = {r: float("inf") for r in range(1, self.total_runs + 1)}
        self.run_row_labels: dict[int, tuple[QLabel, QLabel]] = {}

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
        self.setMinimumSize(900, 620)
        self.resize(1050, 720)

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
        hf_layout.setSpacing(8)

        top_row = QHBoxLayout()
        self.lbl_title = QLabel("🚀 Optimizando Frecuencias...")
        self.lbl_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Colors.ACCENT};")
        top_row.addWidget(self.lbl_title)
        top_row.addStretch()

        self.lbl_best = QLabel("🏆 Mejor coste global: —")
        self.lbl_best.setStyleSheet(f"""
            font-size: 13px;
            font-weight: bold;
            color: {Colors.TEXT_MUTED};
            padding: 3px 8px;
            border: 1px solid {Colors.BORDER};
            border-radius: 4px;
            background-color: #0c1017;
        """)
        top_row.addWidget(self.lbl_best)

        top_row.addSpacing(16)
        self.lbl_timer = QLabel("⏱️ 00:00")
        self.lbl_timer.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_DIM};")
        top_row.addWidget(self.lbl_timer)
        hf_layout.addLayout(top_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self.total_runs * self.total_gens)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0% (0 / %m iteraciones)")
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colors.BG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                height: 20px;
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

        # Dedicated line for each execution
        runs_frame = QFrame()
        runs_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.BG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
        """)
        if self.total_runs <= 3:
            rf_layout = QVBoxLayout(runs_frame)
            rf_layout.setContentsMargins(8, 4, 8, 4)
            rf_layout.setSpacing(4)
        else:
            rf_layout = QGridLayout(runs_frame)
            rf_layout.setContentsMargins(8, 4, 8, 4)
            rf_layout.setHorizontalSpacing(24)
            rf_layout.setVerticalSpacing(4)

        for r in range(1, self.total_runs + 1):
            c = self.get_run_color(r)
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(12)

            lbl_name = QLabel(f"● Ejecución {r}:")
            lbl_name.setStyleSheet(f"color: {c}; font-weight: bold; font-size: 12px; min-width: 95px;")

            lbl_iter = QLabel(f"Iteración 0/{self.total_gens} (0%)")
            lbl_iter.setStyleSheet(f"color: {Colors.TEXT}; font-size: 11px; min-width: 140px;")

            lbl_cost = QLabel("Mejor coste: —")
            lbl_cost.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 11px;")

            row_l.addWidget(lbl_name)
            row_l.addWidget(lbl_iter)
            row_l.addWidget(lbl_cost)
            row_l.addStretch()

            self.run_row_labels[r] = (lbl_iter, lbl_cost)

            if self.total_runs <= 3:
                rf_layout.addWidget(row_w)
            else:
                col = (r - 1) % 2
                row_idx = (r - 1) // 2
                rf_layout.addWidget(row_w, row_idx, col)

        hf_layout.addWidget(runs_frame)
        layout.addWidget(header_frame)

        # ── Matplotlib Plots Frame ──────────────────────────────
        plot_frame = QFrame()
        plot_frame.setStyleSheet(f"""
            QFrame {{
                background-color: #141a24;
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        pf_layout = QVBoxLayout(plot_frame)
        pf_layout.setContentsMargins(6, 6, 6, 6)

        self.figure = Figure(facecolor="#141a24", layout="constrained")
        if hasattr(self.figure, "get_layout_engine") and self.figure.get_layout_engine():
            self.figure.get_layout_engine().set(w_pad=0.08, h_pad=0.08, wspace=0.06, hspace=0.06)
        else:
            self.figure.set_constrained_layout_pads(w_pad=0.08, h_pad=0.08, wspace=0.06, hspace=0.06)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(320)
        self.canvas.setStyleSheet("background: transparent;")
        pf_layout.addWidget(self.canvas)

        layout.addWidget(plot_frame, 1)

        self._init_plots()

        # ── Footer ───────────────────────────────────────────────
        footer_layout = QHBoxLayout()
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
        """Initialize a single large comparison Matplotlib plot where each execution is a distinct line."""
        self.figure.clear()

        self.ax = self.figure.add_subplot(1, 1, 1)
        self.ax.set_facecolor("#0c1017")
        self.axes = [self.ax]

        title_text = "Comparación de Convergencia por Ejecución (Iteración vs Coste)" if self.total_runs > 1 else "Convergencia de la Optimización (Iteración vs Coste)"
        self.ax.set_title(title_text, color="#f0f4f8", fontsize=12, fontweight="bold", pad=10)
        self.ax.set_xlabel("Iteración / Generación", color="#94a3b8", fontsize=10, fontweight="bold", labelpad=8)
        self.ax.set_ylabel("Coste (Score)", color="#94a3b8", fontsize=10, fontweight="bold", labelpad=8)
        self.ax.grid(True, linestyle="--", alpha=0.3, color="#2b384e")
        self.ax.tick_params(axis="both", which="major", colors="#8b9bb4", labelsize=9, pad=5)

        for spine in self.ax.spines.values():
            spine.set_color("#2b384e")

        formatter = FuncFormatter(_axis_formatter)
        self.ax.xaxis.set_major_formatter(formatter)
        self.ax.yaxis.set_major_formatter(formatter)

        self.ax.set_xlim(0, max(self.total_gens, 1))

        self.lines = {}
        for r in range(1, self.total_runs + 1):
            c = self.get_run_color(r)
            label = f"Ejecución {r}"
            line, = self.ax.plot(
                [], [],
                color=c,
                linewidth=2.2,
                marker="o",
                markersize=3.5,
                markerfacecolor=c,
                markeredgecolor=c,
                label=label,
                alpha=0.9
            )
            self.lines[r] = line

        self.legend = self.ax.legend(
            facecolor="#141a24",
            edgecolor="#2b384e",
            labelcolor="#f0f4f8",
            fontsize=9,
            loc="upper right",
            framealpha=0.85
        )

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

        # 1. Record history & update plot curve
        x_vals, y_vals = self.histories.setdefault(run_idx, ([], []))
        x_vals.append(gen)
        y_vals.append(best_score)

        if run_idx in self.lines:
            line = self.lines[run_idx]
            line.set_data(x_vals, y_vals)
            max_x = max(self.total_gens, max(x_vals) if x_vals else self.total_gens, 1)
            self.ax.set_xlim(0, max_x)
            self.ax.relim()
            self.ax.autoscale_view(scalex=False, scaley=True)

        # 2. Track current gen and best score per run
        self.current_gens[run_idx] = max(self.current_gens.get(run_idx, 0), gen)
        self.run_best_scores[run_idx] = min(self.run_best_scores.get(run_idx, float("inf")), best_score)

        # 3. Overall best score & dynamic color based on the leading execution
        best_run = None
        min_score = float("inf")
        for r in range(1, self.total_runs + 1):
            sc = self.run_best_scores.get(r, float("inf"))
            if sc < min_score:
                min_score = sc
                best_run = r

        if best_run is not None and min_score < float("inf"):
            self.global_best_score = min_score
            c = self.get_run_color(best_run)
            run_text = f" (Ejecución {best_run})" if self.total_runs > 1 else ""
            self.lbl_best.setText(f"🏆 Mejor coste global: {format_number(min_score)}{run_text}")
            self.lbl_best.setStyleSheet(f"""
                font-size: 13px;
                font-weight: bold;
                color: {c};
                padding: 3px 8px;
                border: 1px solid {c};
                border-radius: 4px;
                background-color: #0c1017;
            """)

        # 4. Progress bar fills smoothly as a whole across all runs
        total_work = self.total_runs * self.total_gens
        completed_gens = sum(self.current_gens.values())
        self.progress_bar.setValue(min(total_work, completed_gens))
        pct = int((completed_gens / total_work) * 100) if total_work > 0 else 0
        self.progress_bar.setFormat(f"{pct}% ({completed_gens}/{total_work} iteraciones)")

        # 5. Update only this execution's fixed line (never dances!)
        if run_idx in self.run_row_labels:
            lbl_iter, lbl_cost = self.run_row_labels[run_idx]
            run_pct = int((gen / total_gens) * 100) if total_gens > 0 else 0
            lbl_iter.setText(f"Iteración {gen}/{total_gens} ({run_pct}%)")
            lbl_cost.setText(f"Mejor coste: {format_number(self.run_best_scores[run_idx])}")

        self.canvas.draw_idle()

    def set_finished(self, best_score: Optional[float] = None, cancelled: bool = False, export_folder: Optional[str] = None):
        """Called when optimization completes or is stopped."""
        self.is_finished = True
        self.timer.stop()
        was_canc = cancelled or getattr(self, "_was_cancelled", False)
        total_work = self.total_runs * self.total_gens

        save_note = f" (Exportado en: {export_folder})" if export_folder else ""
        if was_canc:
            self.lbl_title.setText(f"⏹️ Cálculo Detenido — Resultados Rescatados{save_note}")
            self.lbl_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Colors.WARNING};")
            curr_val = self.progress_bar.value()
            self.progress_bar.setFormat(f"Detenido en {curr_val}/{total_work} iteraciones")
        else:
            self.progress_bar.setValue(total_work)
            self.progress_bar.setFormat(f"100% ({total_work}/{total_work} iteraciones)")
            self.lbl_title.setText(f"✅ Optimización Completada con Éxito{save_note}")
            self.lbl_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Colors.SUCCESS};")

        final_best = best_score if best_score is not None else self.global_best_score
        clean_score = format_number(final_best) if final_best != float("inf") else "—"

        winning_run = None
        for r in range(1, self.total_runs + 1):
            r_best = self.run_best_scores.get(r, float("inf"))
            if r_best == final_best and winning_run is None and r_best != float("inf"):
                winning_run = r

            if r in self.run_row_labels:
                lbl_iter, lbl_cost = self.run_row_labels[r]
                if was_canc:
                    lbl_iter.setText(f"{lbl_iter.text()} — Detenida")
                else:
                    lbl_iter.setText(f"Iteración {self.total_gens}/{self.total_gens} (100%) — Finalizada")

                if r == winning_run:
                    lbl_cost.setText(f"Coste final: {format_number(r_best)} ★ (Mejor)")
                    lbl_cost.setStyleSheet(f"color: {Colors.SUCCESS}; font-size: 11px; font-weight: bold;")
                else:
                    lbl_cost.setText(f"Coste final: {format_number(r_best if r_best != float('inf') else '—')}")

        if winning_run is not None:
            c = self.get_run_color(winning_run)
            run_text = f" (Ejecución {winning_run})" if self.total_runs > 1 else ""
            self.lbl_best.setText(f"🏆 Score Óptimo: {clean_score}{run_text}")
            self.lbl_best.setStyleSheet(f"""
                font-size: 13px;
                font-weight: bold;
                color: {c};
                padding: 3px 8px;
                border: 1px solid {c};
                border-radius: 4px;
                background-color: #0c1017;
            """)
        else:
            self.lbl_best.setText(f"🏆 Score Óptimo: {clean_score}")

        # Update legend labels to show final scores and highlight winner
        if hasattr(self, "ax"):
            for r, line in self.lines.items():
                r_best = self.run_best_scores.get(r, float("inf"))
                score_str = format_number(r_best) if r_best != float("inf") else "—"
                if r == winning_run:
                    line.set_label(f"Ejecución {r}: {score_str} ★ (Mejor)")
                    line.set_linewidth(3.2)
                    line.set_zorder(10)
                else:
                    line.set_label(f"Ejecución {r}: {score_str}")
                    line.set_linewidth(1.8)
                    line.set_alpha(0.7)

            self.ax.legend(
                facecolor="#141a24",
                edgecolor="#2b384e",
                labelcolor="#f0f4f8",
                fontsize=9,
                loc="upper right",
                framealpha=0.9
            )
            self.ax.relim()
            self.ax.autoscale_view()

        self.btn_action.setEnabled(True)
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

        self.canvas.draw_idle()

    def _on_action_clicked(self):
        if not self.is_finished:
            self._was_cancelled = True
            self.cancelled.emit()
            self.lbl_title.setText("⛔ Cancelando cálculo y recuperando resultados...")
            self.lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #fdcb6e;")
            self.btn_action.setEnabled(False)
        else:
            self.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def closeEvent(self, event):
        if not self.is_finished:
            self.cancelled.emit()
        super().closeEvent(event)

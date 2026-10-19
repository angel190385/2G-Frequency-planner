"""
Progress dialog for sequential multi-zone cluster optimization.
Displays zone-by-zone progress, current generation, best score, and status log.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QProgressBar, QTextEdit, QGroupBox, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal

from src.engine.cluster_optimizer import ZoneOptimizationProgress
from src.models.frequency_plan import format_number
from src.ui.styles import Colors


class ClusterProgressDialog(QDialog):
    """Dialog showing live sequential progress across multiple optimization zones."""
    cancelled = pyqtSignal()

    def __init__(self, total_zones: int, parent=None):
        super().__init__(parent)
        self.total_zones = max(1, total_zones)
        self._completed_zones_count = 0
        self.setWindowTitle("Optimización Secuencial por Zonas")
        self.setMinimumSize(560, 440)
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
        grp_overall = QGroupBox(f"Progreso Global ({self.total_zones} Zonas)")
        og_layout = QVBoxLayout(grp_overall)
        self.bar_overall = QProgressBar()
        self.bar_overall.setRange(0, 100)
        self.bar_overall.setValue(0)
        self.bar_overall.setFormat(f"0% (0/{self.total_zones} Zonas)")
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
        layout.addWidget(grp_overall)

        # Current zone progress
        grp_current = QGroupBox("Zona Activa")
        cg_layout = QVBoxLayout(grp_current)

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
        layout.addWidget(grp_current)

        # Log
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #0b1120; font-family: 'Consolas', monospace; font-size: 11px;")
        self.txt_log.setMaximumHeight(140)
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

    def update_progress(self, p: ZoneOptimizationProgress):
        """Update live status for the active zone."""
        self.lbl_zone_title.setText(f"🚀 Optimizando Zona {p.zone_index} de {p.total_zones}: {p.zone_name}")
        self.lbl_zone_sub.setText(f"Run {p.current_run} de {p.total_runs} — Generación {p.current_gen} de {p.total_gens}")
        self.lbl_run_gen.setText(f"Generación: {p.current_gen} / {p.total_gens} | Run: {p.current_run} / {p.total_runs}")

        sc_str = format_number(p.best_score)
        self.lbl_best_score.setText(f"Mejor Coste: {sc_str}")

        # Active zone progress across its runs
        zone_total = max(1, p.total_runs * p.total_gens)
        zone_done = (p.current_run - 1) * p.total_gens + p.current_gen
        zone_pct = min(100, max(0, int((zone_done / zone_total) * 100)))
        self.bar_current.setValue(zone_pct)
        self.bar_current.setFormat(f"{zone_pct}% (Gen {p.current_gen}/{p.total_gens} - Run {p.current_run}/{p.total_runs})")

        # Smooth overall progress across all zones
        completed_zones = p.zone_index - 1
        zone_fraction = zone_done / zone_total
        overall_pct = min(100, max(0, int(((completed_zones + zone_fraction) / max(1, p.total_zones)) * 100)))
        self.bar_overall.setValue(overall_pct)
        self.bar_overall.setFormat(f"{overall_pct}% (Zona {p.zone_index}/{p.total_zones})")

    def log_zone_completed(self, zone_name: str, score: float, cell_count: int):
        """Append log message when a zone finishes."""
        sc_str = format_number(score)
        self.txt_log.append(f"✅ [{zone_name}] completada: {cell_count} celdas optimizadas | Score: {sc_str}")
        self._completed_zones_count += 1
        pct = min(100, int((self._completed_zones_count / max(1, self.total_zones)) * 100))
        self.bar_overall.setValue(pct)
        self.bar_overall.setFormat(f"{pct}% ({self._completed_zones_count}/{self.total_zones} Zonas)")

    def log_message(self, msg: str):
        self.txt_log.append(msg)

    def _on_cancel(self):
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelando...")
        self.cancelled.emit()

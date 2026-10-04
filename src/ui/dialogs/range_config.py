"""
Range configuration dialog — used inline in calculation tab.
This module is kept for potential standalone use.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QSpinBox, QGridLayout, QLineEdit
)
from PyQt6.QtCore import Qt


class RangeConfigDialog(QDialog):
    """Quick dialog for configuring the frequency range."""

    def __init__(self, freq_start=1, freq_end=124,
                 excluded=None, parent=None):
        super().__init__(parent)
        self.freq_start = freq_start
        self.freq_end = freq_end
        self.excluded = excluded or []
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("📻 Rango de Frecuencias")
        self.setMinimumSize(400, 250)

        layout = QVBoxLayout(self)

        group = QGroupBox("Configurar rango ARFCN")
        g_layout = QGridLayout(group)

        g_layout.addWidget(QLabel("ARFCN mínimo:"), 0, 0)
        self.spin_start = QSpinBox()
        self.spin_start.setRange(0, 1023)
        self.spin_start.setValue(self.freq_start)
        g_layout.addWidget(self.spin_start, 0, 1)

        g_layout.addWidget(QLabel("ARFCN máximo:"), 1, 0)
        self.spin_end = QSpinBox()
        self.spin_end.setRange(0, 1023)
        self.spin_end.setValue(self.freq_end)
        g_layout.addWidget(self.spin_end, 1, 1)

        g_layout.addWidget(QLabel("Excluidas (coma):"), 2, 0)
        self.txt_excluded = QLineEdit()
        if self.excluded:
            self.txt_excluded.setText(", ".join(str(f) for f in self.excluded))
        g_layout.addWidget(self.txt_excluded, 2, 1)

        layout.addWidget(group)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("Aceptar")
        btn_ok.setObjectName("btn_primary")
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

    def get_values(self) -> tuple[int, int, list[int]]:
        """Return the configured range values."""
        excluded = []
        for part in self.txt_excluded.text().split(','):
            part = part.strip()
            if part:
                try:
                    excluded.append(int(part))
                except ValueError:
                    pass
        return self.spin_start.value(), self.spin_end.value(), excluded

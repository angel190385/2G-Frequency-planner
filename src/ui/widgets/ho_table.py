"""
Handover table widget (combined into neighbor view).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from src.models.neighbor import NeighborGraph
from src.models.cell import Cell
from src.ui.styles import Colors


class HOTableWidget(QWidget):
    """Widget displaying handover data sorted by HO count."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("📡 Handovers")
        title.setObjectName("label_title")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.lbl_status = QLabel("Sin datos de HO")
        self.lbl_status.setObjectName("label_warning")
        header_layout.addWidget(self.lbl_status)
        layout.addLayout(header_layout)

        # Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)

        columns = ["Celda A", "Est. A", "Celda B", "Est. B",
                    "HO Count", "Dist. (km)", "Misma Est."]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        layout.addWidget(self.table)

        # Summary
        summary_layout = QHBoxLayout()
        self.lbl_total_ho = QLabel("Total HO: 0")
        self.lbl_max_ho = QLabel("Max HO: 0")
        self.lbl_coverage = QLabel("Cobertura: 0%")

        for lbl in [self.lbl_total_ho, self.lbl_max_ho, self.lbl_coverage]:
            lbl.setObjectName("label_subtitle")
            summary_layout.addWidget(lbl)

        summary_layout.addStretch()
        layout.addLayout(summary_layout)

    def load_data(self, neighbor_graph: NeighborGraph, cells: dict[str, Cell]):
        """Populate the HO table, showing only relations with HO data."""
        self.table.setSortingEnabled(False)

        # Filter relations with HO data
        all_relations = neighbor_graph.all_relations()
        ho_relations = [r for r in all_relations if r.ho_count is not None]
        ho_relations.sort(key=lambda r: r.ho_count or 0, reverse=True)

        self.table.setRowCount(len(ho_relations))

        total_ho = 0
        max_ho = 0

        for row, rel in enumerate(ho_relations):
            cell_a = cells.get(rel.cell_a_id)
            cell_b = cells.get(rel.cell_b_id)
            same_station = (cell_a and cell_b and
                            cell_a.station.station_id == cell_b.station.station_id)

            ho = rel.ho_count or 0
            total_ho += ho
            max_ho = max(max_ho, ho)

            items_data = [
                rel.cell_a_id,
                cell_a.station.station_id if cell_a else "?",
                rel.cell_b_id,
                cell_b.station.station_id if cell_b else "?",
                str(ho),
                f"{rel.distance_km:.2f}",
                "Sí" if same_station else "No",
            ]

            for col, text in enumerate(items_data):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                if col == 4:  # HO count
                    item.setData(Qt.ItemDataRole.UserRole, ho)
                    if ho > 1000:
                        item.setForeground(QBrush(QColor(Colors.DANGER)))
                    elif ho > 100:
                        item.setForeground(QBrush(QColor(Colors.WARNING)))
                    else:
                        item.setForeground(QBrush(QColor(Colors.SUCCESS)))

                if col == 6 and same_station:
                    item.setForeground(QBrush(QColor(Colors.WARNING)))

                self.table.setItem(row, col, item)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i in range(len(columns)):
            self.table.setColumnWidth(i, 100)

        # Update stats
        if ho_relations:
            self.lbl_status.setText("Datos de HO cargados")
            self.lbl_status.setObjectName("label_success")
            self.lbl_status.setStyleSheet(f"color: {Colors.SUCCESS}; font-weight: bold;")
        else:
            self.lbl_status.setText("Sin datos de HO — se calcularán sin ponderación")
            self.lbl_status.setObjectName("label_warning")
            self.lbl_status.setStyleSheet(f"color: {Colors.WARNING}; font-weight: bold;")

        coverage = (len(ho_relations) / len(all_relations) * 100) if all_relations else 0

        self.lbl_total_ho.setText(f"Total HO: {total_ho:,}")
        self.lbl_max_ho.setText(f"Max HO: {max_ho:,}")
        self.lbl_coverage.setText(f"Cobertura: {coverage:.0f}%")

        self.table.setSortingEnabled(True)

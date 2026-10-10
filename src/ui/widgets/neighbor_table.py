"""
Neighbor table widget for displaying neighbor relations.
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


class NeighborTableWidget(QWidget):
    """Widget displaying neighbor relations between cells."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("🔗 Vecindades")
        title.setObjectName("label_title")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.lbl_count = QLabel("0 vecindades cargadas")
        self.lbl_count.setObjectName("label_subtitle")
        header_layout.addWidget(self.lbl_count)
        layout.addLayout(header_layout)

        # Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)

        columns = ["Celda A", "Est. A", "Celda B", "Est. B",
                    "Distancia (km)", "HO Count"]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        layout.addWidget(self.table)

        # Summary
        summary_layout = QHBoxLayout()
        self.lbl_with_ho = QLabel("Con HO: 0")
        self.lbl_without_ho = QLabel("Sin HO: 0")
        self.lbl_avg_dist = QLabel("Dist. media: 0 km")

        for lbl in [self.lbl_with_ho, self.lbl_without_ho, self.lbl_avg_dist]:
            lbl.setObjectName("label_subtitle")
            summary_layout.addWidget(lbl)

        summary_layout.addStretch()
        layout.addLayout(summary_layout)

    def load_data(self, neighbor_graph: NeighborGraph, cells: dict[str, Cell]):
        """Populate the table with neighbor data."""
        self.table.setSortingEnabled(False)

        relations = neighbor_graph.all_relations()
        self.table.setRowCount(len(relations))

        total_dist = 0.0
        with_ho = 0
        without_ho = 0

        for row, rel in enumerate(sorted(relations, key=lambda r: r.key)):
            cell_a = cells.get(rel.cell_a_id)
            cell_b = cells.get(rel.cell_b_id)

            items_data = [
                rel.cell_a_id,
                cell_a.station.station_id if cell_a else "?",
                rel.cell_b_id,
                cell_b.station.station_id if cell_b else "?",
                f"{rel.distance_km:.2f}",
                str(rel.ho_count) if rel.ho_count is not None else "—",
            ]

            for col, text in enumerate(items_data):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                # Color HO column
                if col == 5:
                    if rel.ho_count is not None:
                        with_ho += 1
                        if rel.ho_count > 1000:
                            item.setForeground(QBrush(QColor(Colors.DANGER)))
                        elif rel.ho_count > 100:
                            item.setForeground(QBrush(QColor(Colors.WARNING)))
                        else:
                            item.setForeground(QBrush(QColor(Colors.SUCCESS)))
                        # For proper numeric sorting
                        item.setData(Qt.ItemDataRole.UserRole, rel.ho_count)
                    else:
                        without_ho += 1
                        item.setForeground(QBrush(QColor(Colors.TEXT_DIM)))

                if col == 4:
                    item.setData(Qt.ItemDataRole.UserRole, rel.distance_km)

                self.table.setItem(row, col, item)

            total_dist += rel.distance_km

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i in range(self.table.columnCount()):
            self.table.setColumnWidth(i, 110)

        # Update stats
        total = len(relations)
        self.lbl_count.setText(f"{total} vecindades cargadas")
        self.lbl_with_ho.setText(f"Con HO: {with_ho}")
        self.lbl_without_ho.setText(f"Sin HO: {without_ho}")
        avg_dist = total_dist / total if total > 0 else 0
        self.lbl_avg_dist.setText(f"Dist. media: {avg_dist:.2f} km")

        self.table.setSortingEnabled(True)

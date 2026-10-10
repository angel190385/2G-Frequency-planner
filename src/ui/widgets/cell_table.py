"""
Cell table widget for displaying and managing cell frequency data.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QCheckBox, QSpinBox, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush

from src.models.cell import Cell
from src.ui.styles import Colors


class CellTableWidget(QWidget):
    """Widget displaying all cells with their current frequencies."""

    cells_updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells: dict[str, Cell] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("📊 Celdas y Frecuencias")
        title.setObjectName("label_title")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.lbl_count = QLabel("0 celdas cargadas")
        self.lbl_count.setObjectName("label_subtitle")
        header_layout.addWidget(self.lbl_count)
        layout.addLayout(header_layout)

        # Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)

        # Summary bar
        summary_layout = QHBoxLayout()
        self.lbl_stations = QLabel("Estaciones: 0")
        self.lbl_bcch_count = QLabel("BCCHs: 0")
        self.lbl_tch_count = QLabel("TCHs: 0")
        self.lbl_unique_freqs = QLabel("Frecuencias únicas: 0")

        for lbl in [self.lbl_stations, self.lbl_bcch_count,
                     self.lbl_tch_count, self.lbl_unique_freqs]:
            lbl.setObjectName("label_subtitle")
            summary_layout.addWidget(lbl)

        summary_layout.addStretch()
        layout.addLayout(summary_layout)

    def load_cells(self, cells: dict[str, Cell]):
        """Populate the table with cell data."""
        self._cells = cells
        self.table.setSortingEnabled(False)

        # Determine max TCH count for columns
        max_tch = max((len(c.tch_list) for c in cells.values()), default=0)

        # Setup columns
        base_cols = ["Estación", "Nombre Est.", "Sector", "Cell ID",
                      "Lat", "Lon", "BCCH"]
        tch_cols = [f"TCH{i+1}" for i in range(max_tch)]
        all_cols = base_cols + tch_cols

        self.table.setColumnCount(len(all_cols))
        self.table.setHorizontalHeaderLabels(all_cols)
        self.table.setRowCount(len(cells))

        all_freqs = set()
        stations = set()

        for row, cell in enumerate(sorted(cells.values(),
                                           key=lambda c: (c.station.station_id, c.sector))):
            stations.add(cell.station.station_id)

            items = [
                cell.station.station_id,
                cell.station.name,
                cell.sector,
                cell.cell_id,
                f"{cell.station.latitude:.6f}",
                f"{cell.station.longitude:.6f}",
                str(cell.bcch) if cell.bcch is not None else "",
            ]

            for i, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if i == 6 and cell.bcch is not None:  # BCCH column
                    item.setForeground(QBrush(QColor(Colors.ACCENT)))
                    item.setData(Qt.ItemDataRole.UserRole, cell.bcch)
                    all_freqs.add(cell.bcch)
                self.table.setItem(row, i, item)

            # TCH columns
            for j, tch in enumerate(cell.tch_list):
                item = QTableWidgetItem(str(tch))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setForeground(QBrush(QColor(Colors.ACCENT_BLUE)))
                item.setData(Qt.ItemDataRole.UserRole, tch)
                self.table.setItem(row, len(base_cols) + j, item)
                all_freqs.add(tch)

        # Resize columns efficiently without freezing on large datasets
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for c_idx in range(len(all_cols)):
            self.table.setColumnWidth(c_idx, 80)
        self.table.setColumnWidth(0, 110)  # Estación
        self.table.setColumnWidth(1, 140)  # Nombre Est.
        self.table.setColumnWidth(3, 110)  # Cell ID

        # Update stats
        self.lbl_count.setText(f"{len(cells)} celdas cargadas")
        self.lbl_stations.setText(f"Estaciones: {len(stations)}")
        bcch_count = sum(1 for c in cells.values() if c.bcch is not None)
        tch_count = sum(len(c.tch_list) for c in cells.values())
        self.lbl_bcch_count.setText(f"BCCHs: {bcch_count}")
        self.lbl_tch_count.setText(f"TCHs: {tch_count}")
        self.lbl_unique_freqs.setText(f"Frecuencias únicas: {len(all_freqs)}")

        self.table.setSortingEnabled(True)
        self.cells_updated.emit()

    def get_selected_cell_ids(self) -> list[str]:
        """Return the cell_ids of selected rows."""
        selected = set()
        for item in self.table.selectedItems():
            row = item.row()
            cell_id_item = self.table.item(row, 3)  # Cell ID column
            if cell_id_item:
                selected.add(cell_id_item.text())
        return list(selected)

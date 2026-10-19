"""
Optimization Zone data model and spatial polygon algorithms.

Implements:
- Point-in-polygon ray casting
- Polygon planar area calculation
- Hierarchical containment and cell assignment:
  If a polygon contains another, the larger polygon only takes the cells
  that the smaller (inner) polygon does NOT contain.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any
import copy

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.engine.constraints import FrequencyRules

# Palette of distinct vibrant colors for auto-naming zones
ZONE_COLORS = [
    "#00d4aa",  # Cyan/Teal
    "#ff007f",  # Magenta
    "#3498db",  # Blue
    "#f39c12",  # Orange
    "#9b59b6",  # Purple
    "#2ecc71",  # Green
    "#e74c3c",  # Red
    "#1abc9c",  # Turquoise
    "#e67e22",  # Carrot
    "#8e44ad",  # Violet
]


def point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    """
    Ray casting algorithm to determine if a point (lat, lon) is inside a polygon.
    Polygon is a list of (lat, lon) tuples.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    p1_lat, p1_lon = polygon[0]

    for i in range(1, n + 1):
        p2_lat, p2_lon = polygon[i % n]
        if lon > min(p1_lon, p2_lon):
            if lon <= max(p1_lon, p2_lon):
                if lat <= max(p1_lat, p2_lat):
                    if p1_lon != p2_lon:
                        lat_inters = (lon - p1_lon) * (p2_lat - p1_lat) / (p2_lon - p1_lon) + p1_lat
                    else:
                        lat_inters = p1_lat
                    if p1_lat == p2_lat or lat <= lat_inters:
                        inside = not inside
        p1_lat, p1_lon = p2_lat, p2_lon

    return inside


def polygon_area(polygon: list[tuple[float, float]]) -> float:
    """
    Calculate 2D planar area of a polygon using the Shoelace formula.
    Points are (lat, lon).
    """
    n = len(polygon)
    if n < 3:
        return 0.0

    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]

    return abs(area) / 2.0


@dataclass
class OptimizationZone:
    """Represents a geographic polygon zone for sequential optimization."""
    zone_id: str                          # Unique identifier, e.g. "zona_1"
    name: str                             # Display name, e.g. "Zona 1"
    polygon_coords: list[tuple[float, float]]  # List of (lat, lon)
    cell_ids: set[str] = field(default_factory=set)
    color: str = "#00d4aa"
    execution_order: int = 1
    status: str = "Pendiente"             # "Pendiente", "Optimizando", "Completada", "Cancelada"

    # Specific settings for this zone
    rules: FrequencyRules = field(default_factory=FrequencyRules)
    recalculate_bcch: bool = False
    recalculate_tch: bool = False
    new_tch_count: int = 0

    # Zone-specific neighbors and handovers if loaded
    neighbor_graph: Optional[NeighborGraph] = None
    neighbors_file_path: Optional[str] = None
    handovers_file_path: Optional[str] = None

    @property
    def area(self) -> float:
        return polygon_area(self.polygon_coords)

    @property
    def cell_count(self) -> int:
        return len(self.cell_ids)


def assign_cells_to_zones(
    cells: dict[str, Cell],
    zones: list[OptimizationZone]
) -> dict[str, set[str]]:
    """
    Assign cells to zones respecting containment hierarchy:
    If a larger polygon contains a smaller polygon, the larger polygon
    ONLY takes the cells that the smaller one does NOT contain.

    Returns:
        dict[zone_id, set[cell_ids]]
    """
    if not zones:
        return {}

    # Sort zones by area ascending: smallest inner zones claim cells first
    sorted_zones = sorted(zones, key=lambda z: z.area)
    claimed_cell_ids: set[str] = set()
    result_map: dict[str, set[str]] = {z.zone_id: set() for z in zones}

    for zone in sorted_zones:
        zone_cells = set()
        poly = zone.polygon_coords
        if len(poly) < 3:
            zone.cell_ids = set()
            continue

        for cid, cell in cells.items():
            if cid in claimed_cell_ids:
                continue

            if not cell.station:
                continue

            lat = cell.station.latitude
            lon = cell.station.longitude
            if lat == 0.0 and lon == 0.0:
                continue

            if point_in_polygon(lat, lon, poly):
                zone_cells.add(cid)
                claimed_cell_ids.add(cid)

        zone.cell_ids = zone_cells
        result_map[zone.zone_id] = zone_cells

    return result_map

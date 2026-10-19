"""
Data models for neighbor relations and handover data.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class NeighborRelation:
    """
    Represents a neighbor relationship between two cells.

    The relation is bidirectional: if A is neighbor of B, B is neighbor of A.
    ho_count may be None if handover data is not available.
    """
    cell_a_id: str
    cell_b_id: str
    ho_count: Optional[int] = None
    distance_km: float = 0.0

    def involves(self, cell_id: str) -> bool:
        """Check if this relation involves a given cell."""
        return self.cell_a_id == cell_id or self.cell_b_id == cell_id

    def other_cell(self, cell_id: str) -> str:
        """Return the other cell in this relation."""
        if self.cell_a_id == cell_id:
            return self.cell_b_id
        return self.cell_a_id

    @property
    def key(self) -> tuple[str, str]:
        """Canonical key for this relation (sorted)."""
        return tuple(sorted((self.cell_a_id, self.cell_b_id)))


def haversine_distance(lat1: float, lon1: float,
                       lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points
    on Earth using the Haversine formula.

    Returns distance in kilometers.
    """
    R = 6371.0  # Earth radius in km

    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_bearing(lat1: float, lon1: float,
                      lat2: float, lon2: float) -> float:
    """
    Calculate the initial bearing (forward azimuth) from point 1 to point 2
    in degrees in [0, 360).
    """
    if lat1 == lat2 and lon1 == lon2:
        return 0.0

    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)

    y = math.sin(dlon_r) * math.cos(lat2_r)
    x = (math.cos(lat1_r) * math.sin(lat2_r) -
         math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r))

    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def angle_diff(angle1: float, angle2: float) -> float:
    """
    Calculate the shortest angular difference in degrees between two angles in [0, 180].
    """
    return abs((angle1 - angle2 + 180.0) % 360.0 - 180.0)


class NeighborGraph:
    """
    Manages the complete set of neighbor relations and provides
    fast lookups by cell ID.
    """

    def __init__(self):
        self._relations: dict[tuple[str, str], NeighborRelation] = {}
        self._by_cell: dict[str, list[NeighborRelation]] = {}

    def add_relation(self, relation: NeighborRelation):
        """Add a neighbor relation."""
        key = relation.key
        self._relations[key] = relation

        for cid in (relation.cell_a_id, relation.cell_b_id):
            if cid not in self._by_cell:
                self._by_cell[cid] = []
            self._by_cell[cid].append(relation)

    def get_neighbors(self, cell_id: str) -> list[NeighborRelation]:
        """Get all neighbor relations for a cell."""
        return self._by_cell.get(cell_id, [])

    def get_neighbor_cell_ids(self, cell_id: str) -> list[str]:
        """Get all neighbor cell IDs for a cell."""
        return [r.other_cell(cell_id) for r in self.get_neighbors(cell_id)]

    def get_relation(self, cell_a: str, cell_b: str) -> Optional[NeighborRelation]:
        """Get the relation between two specific cells, or None."""
        key = tuple(sorted((cell_a, cell_b)))
        return self._relations.get(key)

    def are_neighbors(self, cell_a: str, cell_b: str) -> bool:
        """Check if two cells are neighbors."""
        return self.get_relation(cell_a, cell_b) is not None

    def all_relations(self) -> list[NeighborRelation]:
        """Return all relations."""
        return list(self._relations.values())

    @property
    def total_relations(self) -> int:
        return len(self._relations)

    @property
    def relations_with_ho(self) -> int:
        return sum(1 for r in self._relations.values()
                   if r.ho_count is not None)

    @property
    def has_ho_data(self) -> bool:
        return self.relations_with_ho > 0

    def get_ho_statistics(self) -> dict[str, float]:
        """
        Compute statistical summary for all valid handover relations:
        mean, median, max, min, and count.
        """
        values = [r.ho_count for r in self._relations.values()
                  if r.ho_count is not None and r.ho_count >= 0]
        if not values:
            return {
                "count": 0.0,
                "mean": 1.0,
                "median": 1.0,
                "max": 1.0,
                "min": 0.0
            }

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mean_val = float(sum(sorted_vals) / n)
        if n % 2 == 1:
            median_val = float(sorted_vals[n // 2])
        else:
            median_val = float((sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0)

        return {
            "count": float(n),
            "mean": max(1.0, mean_val),
            "median": max(1.0, median_val),
            "max": max(1.0, float(sorted_vals[-1])),
            "min": float(sorted_vals[0])
        }

    def filter_by_cells(self, cell_ids: set[str] | list[str] | tuple[str, ...]) -> NeighborGraph:
        """
        Create and return a new NeighborGraph containing only relations where
        both cell_a and cell_b are present in cell_ids.
        """
        sub_graph = NeighborGraph()
        cell_set = set(cell_ids)
        for relation in self._relations.values():
            if relation.cell_a_id in cell_set and relation.cell_b_id in cell_set:
                sub_graph.add_relation(relation)
        return sub_graph

    def clear(self):
        self._relations.clear()
        self._by_cell.clear()

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

    def clear(self):
        self._relations.clear()
        self._by_cell.clear()

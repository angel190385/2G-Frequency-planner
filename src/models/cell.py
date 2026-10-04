"""
Data models for cells, stations, and frequency assignments.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Station:
    """Represents a physical base station site."""
    station_id: str
    name: str
    latitude: float
    longitude: float

    def __hash__(self):
        return hash(self.station_id)

    def __eq__(self, other):
        if not isinstance(other, Station):
            return False
        return self.station_id == other.station_id


@dataclass
class Cell:
    """
    Represents a single cell (sector) within a station.

    A cell has one BCCH frequency and zero or more TCH frequencies.
    The fields `recalculate_bcch` and `tch_indices_to_recalculate` indicate
    which frequencies the optimizer should reassign.
    """
    cell_id: str
    station: Station
    sector: str  # e.g. "A", "B", "C"
    azimuth: float = 0.0  # Azimuth in degrees (0..360)

    # Current frequency assignments
    bcch: Optional[int] = None
    tch_list: list[int] = field(default_factory=list)

    # What to recalculate
    recalculate_bcch: bool = False
    tch_indices_to_recalculate: list[int] = field(default_factory=list)
    # Number of NEW TCH slots to add (not replacing existing ones)
    new_tch_count: int = 0

    def __hash__(self):
        return hash(self.cell_id)

    def __eq__(self, other):
        if not isinstance(other, Cell):
            return False
        return self.cell_id == other.cell_id

    @property
    def all_frequencies(self) -> list[int]:
        """Return all assigned frequencies (BCCH + TCH)."""
        freqs = []
        if self.bcch is not None:
            freqs.append(self.bcch)
        freqs.extend(self.tch_list)
        return freqs

    @property
    def total_tch_after_calc(self) -> int:
        """Total number of TCH slots after calculation."""
        return len(self.tch_list) + self.new_tch_count

    @property
    def needs_calculation(self) -> bool:
        """Whether this cell has any frequencies to recalculate."""
        return (self.recalculate_bcch or
                len(self.tch_indices_to_recalculate) > 0 or
                self.new_tch_count > 0)

    def get_fixed_tch(self) -> list[int]:
        """Return TCH frequencies that are NOT being recalculated."""
        return [f for i, f in enumerate(self.tch_list)
                if i not in self.tch_indices_to_recalculate]

    def get_fixed_bcch(self) -> Optional[int]:
        """Return BCCH if it's not being recalculated."""
        if not self.recalculate_bcch:
            return self.bcch
        return None

    def count_variables(self) -> int:
        """Number of frequency variables to optimize for this cell."""
        count = 0
        if self.recalculate_bcch:
            count += 1
        count += len(self.tch_indices_to_recalculate)
        count += self.new_tch_count
        return count

"""
Data models for frequency plan results.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


def format_number(val: float | int | None, precision: int = 2) -> str:
    """
    Format a number cleanly:
    - If val is None: returns ""
    - If val is mathematically an integer (e.g. 100.0 or 100): returns "100" without decimal point/comma.
    - If val has decimals: returns formatted with up to `precision` decimals without trailing zeros.
    """
    if val is None:
        return ""
    try:
        fval = float(val)
        if fval.is_integer():
            return str(int(fval))
        s = f"{fval:.{precision}f}".rstrip("0").rstrip(".")
        return s
    except (ValueError, TypeError):
        return str(val)


@dataclass
class FrequencyAssignment:
    """The frequency assignment for a single cell."""
    cell_id: str
    bcch: int
    tch_list: list[int] = field(default_factory=list)


@dataclass
class Violation:
    """A single co-channel or adjacent-channel violation."""
    cell_a_id: str
    cell_b_id: str
    freq_a: int
    freq_b: int
    violation_type: str  # "cochannel" or "adjacent"
    channel_type_a: str  # "BCCH" or "TCH"
    channel_type_b: str  # "BCCH" or "TCH"
    ho_count: Optional[int] = None
    distance_km: float = 0.0
    weighted_cost: float = 0.0
    is_inherited: bool = False  # True if both frequencies already existed in baseline network

    @property
    def severity(self) -> str:
        """Return severity level based on channel types."""
        if "BCCH" in (self.channel_type_a, self.channel_type_b):
            if self.channel_type_a == "BCCH" and self.channel_type_b == "BCCH":
                return "critical"
            return "high"
        return "low"

    @property
    def description(self) -> str:
        types = f"{self.channel_type_a}-{self.channel_type_b}"
        if self.violation_type == "cochannel":
            return (f"Cocanalidad {types}: {self.cell_a_id} (f={self.freq_a}) "
                    f"↔ {self.cell_b_id} (f={self.freq_b})")
        else:
            return (f"Adyacencia {types}: {self.cell_a_id} (f={self.freq_a}) "
                    f"↔ {self.cell_b_id} (f={self.freq_b})")


@dataclass
class PlanResult:
    """
    A complete frequency plan result with scoring and violation details.
    """
    plan_id: int
    run_id: int = 1
    assignments: list[FrequencyAssignment] = field(default_factory=list)

    # Aggregate score (lower = better)
    score: float = 0.0

    # Cost breakdown: inherited (already present in baseline) vs new assignments
    inherited_cost: float = 0.0
    new_assignment_cost: float = 0.0

    # Violation counts
    cochannel_bcch_bcch: int = 0
    cochannel_bcch_tch: int = 0
    cochannel_tch_tch: int = 0
    adjacent_bcch_bcch: int = 0
    adjacent_bcch_tch: int = 0
    adjacent_tch_tch: int = 0

    # HO-weighted scores
    cochannel_ho_weighted: float = 0.0
    adjacent_ho_weighted: float = 0.0

    # Same-station violations
    same_station_cochannel: int = 0
    same_station_adjacent: int = 0

    # Detailed violations
    violations: list[Violation] = field(default_factory=list)

    # Convergence history: [(iteration, best_score), ...]
    convergence_history: list[tuple[int, float]] = field(default_factory=list)

    @property
    def total_cochannel(self) -> int:
        return (self.cochannel_bcch_bcch +
                self.cochannel_bcch_tch +
                self.cochannel_tch_tch)

    @property
    def total_adjacent(self) -> int:
        return (self.adjacent_bcch_bcch +
                self.adjacent_bcch_tch +
                self.adjacent_tch_tch)

    @property
    def total_violations(self) -> int:
        return self.total_cochannel + self.total_adjacent

    @property
    def critical_violations(self) -> int:
        """BCCH-BCCH co-channels and adjacencies (most dangerous)."""
        return self.cochannel_bcch_bcch + self.adjacent_bcch_bcch

    def get_assignment(self, cell_id: str) -> Optional[FrequencyAssignment]:
        """Get the assignment for a specific cell."""
        for a in self.assignments:
            if a.cell_id == cell_id:
                return a
        return None

    def summary_dict(self) -> dict:
        """Return a summary dictionary for display."""
        return {
            "Plan": self.plan_id,
            "Score": format_number(self.score),
            "Inherited Cost": format_number(self.inherited_cost),
            "New Cost": format_number(self.new_assignment_cost),
            "Cochan BCCH-BCCH": self.cochannel_bcch_bcch,
            "Cochan BCCH-TCH": self.cochannel_bcch_tch,
            "Cochan TCH-TCH": self.cochannel_tch_tch,
            "Adj BCCH-BCCH": self.adjacent_bcch_bcch,
            "Adj BCCH-TCH": self.adjacent_bcch_tch,
            "Adj TCH-TCH": self.adjacent_tch_tch,
            "Cochan HO-weighted": format_number(self.cochannel_ho_weighted),
            "Adj HO-weighted": format_number(self.adjacent_ho_weighted),
            "Same-station Cochan": self.same_station_cochannel,
            "Same-station Adj": self.same_station_adjacent,
            "Total Violations": self.total_violations,
            "Critical": self.critical_violations,
        }


class OptimizationResultList(list):
    """
    A list of PlanResult objects that supports custom attributes like per_run mapping.
    """
    def __init__(
        self,
        iterable=(),
        per_run: Optional[dict[int, list[PlanResult]]] = None,
        per_run_history: Optional[dict[int, list[tuple[int, float]]]] = None
    ):
        super().__init__(iterable)
        self.per_run: dict[int, list[PlanResult]] = per_run or {}
        self.per_run_history: dict[int, list[tuple[int, float]]] = per_run_history or {}

    @property
    def best_plan(self) -> Optional[PlanResult]:
        """Return the overall best plan (first item) or None if list is empty."""
        return self[0] if len(self) > 0 else None


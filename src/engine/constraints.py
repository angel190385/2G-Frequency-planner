"""
Configurable frequency planning rules and constraints.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class FrequencyRules:
    """
    All configurable rules for frequency planning.

    Hard constraints (allow_* = False) will never be violated.
    Soft constraints (allow_* = True) may occur but incur a cost penalty.
    """

    # ── Co-channel rules ──────────────────────────────────────────────
    allow_cochannel_same_station: bool = False
    allow_cochannel_bcch_bcch_neighbors: bool = False
    allow_cochannel_bcch_tch_neighbors: bool = False
    allow_cochannel_tch_tch_neighbors: bool = True

    # ── Adjacent channel rules ────────────────────────────────────────
    allow_adjacent_same_station: bool = False
    allow_adjacent_bcch_bcch_neighbors: bool = False
    allow_adjacent_bcch_tch_neighbors: bool = True
    allow_adjacent_tch_tch_neighbors: bool = True

    # ── Minimum separation (in ARFCN channels) ───────────────────────
    min_separation_same_cell: int = 2
    min_separation_same_station: int = 2
    min_separation_bcch_neighbors: int = 2

    # ── Frequency pool ────────────────────────────────────────────────
    freq_range_start: int = 100
    freq_range_end: int = 124
    excluded_frequencies: list[int] = field(default_factory=list)

    # ── Cost weights ──────────────────────────────────────────────────
    weight_cochannel_bcch_bcch: float = 100.0
    weight_cochannel_bcch_tch: float = 50.0
    weight_cochannel_tch_tch: float = 10.0
    weight_adjacent_bcch_bcch: float = 80.0
    weight_adjacent_bcch_tch: float = 30.0
    weight_adjacent_tch_tch: float = 5.0

    weight_same_station_cochannel: float = 200.0
    weight_same_station_adjacent: float = 150.0

    # ── Distance penalty ──────────────────────────────────────────────
    enable_distance_penalty: bool = True
    distance_threshold_km: float = 5.0
    weight_distance: float = 20.0

    # ── Algorithm parameters ──────────────────────────────────────────
    population_size: int = 200
    num_generations: int = 500
    crossover_rate: float = 0.7
    mutation_rate: float = 0.15
    elite_count: int = 10
    top_k_results: int = 5
    num_runs: int = 3

    @property
    def available_frequencies(self) -> list[int]:
        """Return the list of usable ARFCN frequencies."""
        excluded = set(self.excluded_frequencies)
        return [f for f in range(self.freq_range_start, self.freq_range_end + 1)
                if f not in excluded]

    def is_hard_constraint_violated(
        self,
        same_station: bool,
        is_neighbor: bool,
        is_cochannel: bool,
        channel_type_a: str,
        channel_type_b: str
    ) -> bool:
        """
        Check if a given frequency conflict violates a hard constraint.

        Returns True if the conflict is NOT allowed (hard violation).
        """
        if same_station:
            if is_cochannel and not self.allow_cochannel_same_station:
                return True
            if not is_cochannel and not self.allow_adjacent_same_station:
                return True
            return False

        if not is_neighbor:
            return False

        both_types = {channel_type_a, channel_type_b}

        if is_cochannel:
            if both_types == {"BCCH"}:
                return not self.allow_cochannel_bcch_bcch_neighbors
            elif "BCCH" in both_types:
                return not self.allow_cochannel_bcch_tch_neighbors
            else:
                return not self.allow_cochannel_tch_tch_neighbors
        else:
            if both_types == {"BCCH"}:
                return not self.allow_adjacent_bcch_bcch_neighbors
            elif "BCCH" in both_types:
                return not self.allow_adjacent_bcch_tch_neighbors
            else:
                return not self.allow_adjacent_tch_tch_neighbors

    def get_weight(
        self,
        same_station: bool,
        is_cochannel: bool,
        channel_type_a: str,
        channel_type_b: str
    ) -> float:
        """Get the cost weight for a given type of violation."""
        if same_station:
            if is_cochannel:
                return self.weight_same_station_cochannel
            return self.weight_same_station_adjacent

        both_types = {channel_type_a, channel_type_b}

        if is_cochannel:
            if both_types == {"BCCH"}:
                return self.weight_cochannel_bcch_bcch
            elif "BCCH" in both_types:
                return self.weight_cochannel_bcch_tch
            else:
                return self.weight_cochannel_tch_tch
        else:
            if both_types == {"BCCH"}:
                return self.weight_adjacent_bcch_bcch
            elif "BCCH" in both_types:
                return self.weight_adjacent_bcch_tch
            else:
                return self.weight_adjacent_tch_tch

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "allow_cochannel_same_station": self.allow_cochannel_same_station,
            "allow_cochannel_bcch_bcch_neighbors": self.allow_cochannel_bcch_bcch_neighbors,
            "allow_cochannel_bcch_tch_neighbors": self.allow_cochannel_bcch_tch_neighbors,
            "allow_cochannel_tch_tch_neighbors": self.allow_cochannel_tch_tch_neighbors,
            "allow_adjacent_same_station": self.allow_adjacent_same_station,
            "allow_adjacent_bcch_bcch_neighbors": self.allow_adjacent_bcch_bcch_neighbors,
            "allow_adjacent_bcch_tch_neighbors": self.allow_adjacent_bcch_tch_neighbors,
            "allow_adjacent_tch_tch_neighbors": self.allow_adjacent_tch_tch_neighbors,
            "min_separation_same_cell": self.min_separation_same_cell,
            "min_separation_same_station": self.min_separation_same_station,
            "min_separation_bcch_neighbors": self.min_separation_bcch_neighbors,
            "freq_range_start": self.freq_range_start,
            "freq_range_end": self.freq_range_end,
            "excluded_frequencies": self.excluded_frequencies,
            "weight_cochannel_bcch_bcch": self.weight_cochannel_bcch_bcch,
            "weight_cochannel_bcch_tch": self.weight_cochannel_bcch_tch,
            "weight_cochannel_tch_tch": self.weight_cochannel_tch_tch,
            "weight_adjacent_bcch_bcch": self.weight_adjacent_bcch_bcch,
            "weight_adjacent_bcch_tch": self.weight_adjacent_bcch_tch,
            "weight_adjacent_tch_tch": self.weight_adjacent_tch_tch,
            "weight_same_station_cochannel": self.weight_same_station_cochannel,
            "weight_same_station_adjacent": self.weight_same_station_adjacent,
            "enable_distance_penalty": self.enable_distance_penalty,
            "distance_threshold_km": self.distance_threshold_km,
            "weight_distance": self.weight_distance,
            "population_size": self.population_size,
            "num_generations": self.num_generations,
            "crossover_rate": self.crossover_rate,
            "mutation_rate": self.mutation_rate,
            "elite_count": self.elite_count,
            "top_k_results": self.top_k_results,
            "num_runs": self.num_runs,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FrequencyRules:
        """Deserialize from dictionary."""
        rules = cls()
        for key, value in data.items():
            if hasattr(rules, key):
                setattr(rules, key, value)
        return rules

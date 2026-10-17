"""
Configurable frequency planning rules and constraints.
"""
from __future__ import annotations
from dataclasses import dataclass, field


# ── Handover Statistical Models ──────────────────────────────────
STATISTICAL_MODELS: dict[str, dict] = {
    "logarithmic": {
        "id": "logarithmic",
        "name": "Logarítmico (Compresión de Varianza - Estándar)",
        "short_name": "Logarítmico",
        "formula": "w = 1.0 + 0.5 * log2(1 + HO) si HO > 0 else 1.0",
        "description": "Aplica una compresión sublineal a los traspasos con base neutra 1.0. Amortigua valores extremos sin ignorar relaciones sin telemetría HO ni dejar que las arterias monopolicen la función de coste.",
        "usage": "Recomendado como estándar para la mayoría de redes GSM urbanas y mixtas.",
    },
    "linear": {
        "id": "linear",
        "name": "Lineal Normalizado (Proporcional a Carga)",
        "short_name": "Lineal",
        "formula": "w = 1.0 + (HO / media_HO) * 0.75 si HO > 0 else 1.0",
        "description": "Escala proporcionalmente al volumen de traspasos relativo a la media de la red, manteniendo base 1.0 para celdas sin estadísticas.",
        "usage": "Recomendado cuando el tráfico es homogéneo y se desea proteger los handovers en proporción exacta al volumen.",
    },
    "sigmoid": {
        "id": "sigmoid",
        "name": "Sigmoide / Curva S (Sensibilidad por Umbral Crítico)",
        "short_name": "Sigmoide",
        "formula": "w = 1.0 + 1.5 / (1 + e^(-k * (HO - mediana_HO))) si HO > 0 else 1.0",
        "description": "Función logística centrada en la mediana. Base 1.0 para bajo tráfico o sin datos, escalando suavemente al superar la mediana de la red.",
        "usage": "Ideal para redes densas donde relaciones que superan el umbral medio tienen alto riesgo de corte de llamada.",
    },
    "exponential": {
        "id": "exponential",
        "name": "Exponencial / Pareto 80-20 (Prioridad a Hotspots)",
        "short_name": "Exponencial",
        "formula": "w = 1.0 + 2.0 * (HO / max_HO)^2 si HO > 0 else 1.0",
        "description": "Aplica una ley cuadrática que concentra la máxima penalización en el top 10-20% de relaciones más cargadas de la red (principio de Pareto), flexibilizando restricciones en enlaces secundarios.",
        "usage": "Recomendado en redes con hotspots o ejes de tráfico prioritarios que no admiten degradación.",
    },
    "uniform": {
        "id": "uniform",
        "name": "Uniforme / Topológico (Sin Ponderación HO)",
        "short_name": "Uniforme",
        "formula": "w = 1.0 (neutro)",
        "description": "Asigna peso constante 1.0 a todas las relaciones de vecindad sin tener en cuenta los contadores de handover.",
        "usage": "Recomendado cuando no se dispone de datos de HO fiables o para un diseño topológico puro.",
    },
}


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

    # ── Statistical Model for Handover Weights ────────────────────────
    ho_statistical_model: str = "logarithmic"

    # ── Distance & Spatial Decoupling ──────────────────────────────────
    distance_decay: float = 0.3             # Spatial decay rate per km: 1 / (1 + decay * d)
    weight_neighbor_factor: float = 1.3     # Multiplier for confirmed neighbor graph relations
    enable_distance_penalty: bool = True
    distance_threshold_km: float = 6.0
    weight_distance: float = 25.0

    # ── Azimuth & Sector Orientation ──────────────────────────────────
    enable_azimuth_weight: bool = True
    azimuth_weight_min: float = 0.3         # Weight multiplier for back-to-back / divergent cells
    azimuth_weight_max: float = 1.8         # Weight multiplier for directly facing cells

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
            # Under no circumstances can frequencies repeat or be adjacent within the same station / distance = 0
            return True

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
            "ho_statistical_model": self.ho_statistical_model,
            "enable_distance_penalty": self.enable_distance_penalty,
            "distance_threshold_km": self.distance_threshold_km,
            "weight_distance": self.weight_distance,
            "enable_azimuth_weight": self.enable_azimuth_weight,
            "azimuth_weight_min": self.azimuth_weight_min,
            "azimuth_weight_max": self.azimuth_weight_max,
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

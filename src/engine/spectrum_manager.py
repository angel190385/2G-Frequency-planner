"""
Spectrum manager module for frequency range transition and adaptation.

Handles initial and target frequency ranges, detects frequencies that disappear,
identifies newly available spectrum, and provides strategies to substitute
disappearing frequencies and leverage new spectrum to improve the frequency plan.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.engine.constraints import FrequencyRules


@dataclass
class SpectrumShiftAnalysis:
    """Detailed differential analysis between initial and target spectrum ranges."""
    initial_start: int
    initial_end: int
    initial_excluded: list[int]
    target_start: int
    target_end: int
    target_excluded: list[int]

    initial_pool: list[int]
    target_pool: list[int]

    disappearing_freqs: list[int]
    new_freqs: list[int]
    retained_freqs: list[int]

    # {cell_id: {"station_id": str, "sector": str, "bcch_affected": bool,
    #            "bcch_val": Optional[int], "tch_affected_indices": list[int],
    #            "tch_affected_vals": list[int], "total_affected": int}}
    affected_cells: dict[str, dict] = field(default_factory=dict)

    @property
    def total_affected_cells_count(self) -> int:
        return len(self.affected_cells)

    @property
    def total_affected_bcch_count(self) -> int:
        return sum(1 for c in self.affected_cells.values() if c["bcch_affected"])

    @property
    def total_affected_tch_count(self) -> int:
        return sum(len(c["tch_affected_indices"]) for c in self.affected_cells.values())

    @property
    def has_disappearing(self) -> bool:
        return len(self.disappearing_freqs) > 0

    @property
    def has_new_spectrum(self) -> bool:
        return len(self.new_freqs) > 0


def detect_current_spectrum_range(cells: dict[str, Cell]) -> tuple[int, int, list[int]]:
    """
    Scan all loaded cells to find the actual minimum and maximum ARFCN currently in use.
    Returns (min_arfcn, max_arfcn, []).
    """
    used = set()
    for c in cells.values():
        if c.bcch is not None and c.bcch > 0:
            used.add(c.bcch)
        for f in c.tch_list:
            if f is not None and f > 0:
                used.add(f)

    if not used:
        return (100, 124, [])
    return (min(used), max(used), [])


def analyze_spectrum_shift(
    cells: dict[str, Cell],
    initial_start: int,
    initial_end: int,
    initial_excluded: Optional[list[int]] = None,
    target_start: int = 100,
    target_end: int = 124,
    target_excluded: Optional[list[int]] = None
) -> SpectrumShiftAnalysis:
    """
    Analyze the difference between initial and target spectrum pools
    and detect every cell/channel affected by disappearing frequencies.
    """
    init_excl = set(initial_excluded or [])
    tgt_excl = set(target_excluded or [])

    initial_pool = sorted([f for f in range(initial_start, initial_end + 1) if f not in init_excl])
    target_pool = sorted([f for f in range(target_start, target_end + 1) if f not in tgt_excl])

    init_set = set(initial_pool)
    tgt_set = set(target_pool)

    disappearing = sorted(init_set - tgt_set)
    new_freqs = sorted(tgt_set - init_set)
    retained = sorted(init_set & tgt_set)

    disappearing_set = set(disappearing)
    affected_cells: dict[str, dict] = {}

    for cid, cell in cells.items():
        bcch_aff = bool(cell.bcch is not None and cell.bcch in disappearing_set)
        tch_indices = []
        tch_vals = []

        for idx, f in enumerate(cell.tch_list):
            if f in disappearing_set:
                tch_indices.append(idx)
                tch_vals.append(f)

        if bcch_aff or tch_indices:
            affected_cells[cid] = {
                "cell_id": cid,
                "station_id": cell.station.station_id,
                "sector": cell.sector,
                "bcch_affected": bcch_aff,
                "bcch_val": cell.bcch if bcch_aff else None,
                "tch_affected_indices": tch_indices,
                "tch_affected_vals": tch_vals,
                "total_affected": (1 if bcch_aff else 0) + len(tch_indices)
            }

    return SpectrumShiftAnalysis(
        initial_start=initial_start,
        initial_end=initial_end,
        initial_excluded=sorted(init_excl),
        target_start=target_start,
        target_end=target_end,
        target_excluded=sorted(tgt_excl),
        initial_pool=initial_pool,
        target_pool=target_pool,
        disappearing_freqs=disappearing,
        new_freqs=new_freqs,
        retained_freqs=retained,
        affected_cells=affected_cells
    )


def apply_spectrum_shift_to_cells(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    analysis: SpectrumShiftAnalysis,
    mode: str = "improve_with_new"
) -> int:
    """
    Update rules to the target spectrum range and configure cell recalculation flags.

    Modes:
    - 'disappeared_only': flags only channels that disappear for recalculation.
    - 'improve_with_new': flags channels that disappear AND additionally flags conflicting
      neighbor channels to allow the optimizer to leverage the newly added spectrum.

    Returns the total number of variables marked for optimization.
    """
    rules.freq_range_start = analysis.target_start
    rules.freq_range_end = analysis.target_end
    rules.excluded_frequencies = list(analysis.target_excluded)

    # Reset recalculation flags for all cells first
    for cell in cells.values():
        cell.recalculate_bcch = False
        cell.tch_indices_to_recalculate = []

    # Mark affected cells that contain disappearing frequencies
    for cid, info in analysis.affected_cells.items():
        cell = cells.get(cid)
        if not cell:
            continue
        if info["bcch_affected"]:
            cell.recalculate_bcch = True
        cell.tch_indices_to_recalculate = list(info["tch_affected_indices"])

    # If mode is 'improve_with_new' and we have new spectrum or remaining conflicts,
    # expand recalculation to cells involved in neighbor violations so the new spectrum
    # can resolve pre-existing interference.
    if mode == "improve_with_new" and analysis.has_new_spectrum:
        for relation in neighbor_graph.all_relations():
            cell_a = cells.get(relation.cell_a_id)
            cell_b = cells.get(relation.cell_b_id)
            if not cell_a or not cell_b:
                continue

            # Check if there is co-channel or adjacent between them
            freqs_a = ([cell_a.bcch] if cell_a.bcch else []) + cell_a.tch_list
            freqs_b = ([cell_b.bcch] if cell_b.bcch else []) + cell_b.tch_list

            has_conflict = False
            for fa in freqs_a:
                for fb in freqs_b:
                    if abs(fa - fb) <= 1:
                        has_conflict = True
                        break
                if has_conflict:
                    break

            if has_conflict:
                # Mark one or both cells' TCHs to be improved into the new spectrum
                if not cell_a.tch_indices_to_recalculate and cell_a.tch_list:
                    cell_a.tch_indices_to_recalculate = list(range(len(cell_a.tch_list)))
                if not cell_b.tch_indices_to_recalculate and cell_b.tch_list:
                    cell_b.tch_indices_to_recalculate = list(range(len(cell_b.tch_list)))

    total_vars = 0
    for cell in cells.values():
        if cell.recalculate_bcch:
            total_vars += 1
        total_vars += len(cell.tch_indices_to_recalculate)
        total_vars += cell.new_tch_count

    return total_vars


def greedy_substitute_disappeared_frequencies(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    analysis: SpectrumShiftAnalysis
) -> dict[str, list[tuple[str, int, int]]]:
    """
    Perform immediate local greedy substitution of disappearing frequencies
    with frequencies from the target spectrum pool.

    Prioritizes new spectrum frequencies or clean frequencies that minimize
    conflicts with the cell's own station sectors and neighbor graph relations.

    Returns:
        dict: {cell_id: [(channel_type, old_freq, new_freq), ...]}
    """
    rules.freq_range_start = analysis.target_start
    rules.freq_range_end = analysis.target_end
    rules.excluded_frequencies = list(analysis.target_excluded)

    target_pool = analysis.target_pool
    if not target_pool:
        return {}

    substitutions: dict[str, list[tuple[str, int, int]]] = {}

    # Separate candidates: prefer newly available spectrum, then clean retained
    new_pool = analysis.new_freqs if analysis.new_freqs else target_pool

    for cid, info in analysis.affected_cells.items():
        cell = cells.get(cid)
        if not cell:
            continue

        sid = cell.station.station_id
        cell_subs = []

        # Find all frequencies currently in use by this cell & station
        station_cells = [c for c in cells.values() if c.station.station_id == sid]
        station_freqs = set()
        for sc in station_cells:
            if sc.bcch:
                station_freqs.add(sc.bcch)
            station_freqs.update(sc.tch_list)

        # Collect neighbor frequencies
        neighbor_freqs = set()
        for nid in neighbor_graph.get_neighbor_cell_ids(cid):
            nc = cells.get(nid)
            if nc:
                if nc.bcch:
                    neighbor_freqs.add(nc.bcch)
                neighbor_freqs.update(nc.tch_list)

        def pick_best_freq(is_bcch: bool, current_cell_freqs: set[int]) -> int:
            """Score candidate frequencies and choose the one with lowest conflict."""
            best_f = target_pool[0]
            best_cost = float('inf')

            candidates = new_pool + [f for f in target_pool if f not in new_pool]
            for f in candidates:
                cost = 0.0

                # 1. Separation within same cell
                for uf in current_cell_freqs:
                    diff = abs(f - uf)
                    if diff < rules.min_separation_same_cell:
                        cost += 5000.0

                # 2. Same station
                for sf in station_freqs:
                    diff = abs(f - sf)
                    if diff == 0:
                        cost += rules.weight_same_station_cochannel
                    elif diff == 1:
                        cost += rules.weight_same_station_adjacent

                # 3. Neighbors
                for nf in neighbor_freqs:
                    diff = abs(f - nf)
                    if diff == 0:
                        cost += (rules.weight_cochannel_bcch_bcch if is_bcch
                                 else rules.weight_cochannel_tch_tch)
                    elif diff == 1:
                        cost += (rules.weight_adjacent_bcch_bcch if is_bcch
                                 else rules.weight_adjacent_tch_tch)

                if cost < best_cost:
                    best_cost = cost
                    best_f = f
                    if cost == 0:
                        break

            return best_f

        # Replace BCCH if affected
        if info["bcch_affected"] and cell.bcch is not None:
            old_bcch = cell.bcch
            current_cell_freqs = set(cell.tch_list)
            new_bcch = pick_best_freq(is_bcch=True, current_cell_freqs=current_cell_freqs)
            cell.bcch = new_bcch
            station_freqs.discard(old_bcch)
            station_freqs.add(new_bcch)
            cell_subs.append(("BCCH", old_bcch, new_bcch))

        # Replace affected TCHs
        for idx in info["tch_affected_indices"]:
            old_tch = cell.tch_list[idx]
            current_cell_freqs = set(cell.tch_list[:idx] + cell.tch_list[idx+1:])
            if cell.bcch:
                current_cell_freqs.add(cell.bcch)

            new_tch = pick_best_freq(is_bcch=False, current_cell_freqs=current_cell_freqs)
            cell.tch_list[idx] = new_tch
            station_freqs.discard(old_tch)
            station_freqs.add(new_tch)
            cell_subs.append((f"TCH{idx+1}", old_tch, new_tch))

        if cell_subs:
            substitutions[cid] = cell_subs

    return substitutions

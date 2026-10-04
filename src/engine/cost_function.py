"""
Cost function for evaluating complete frequency plans.

Evaluates a full frequency assignment against the neighbor graph
and constraint rules, producing a weighted score and detailed violations.
"""
from __future__ import annotations
import math
from typing import Optional

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import (
    FrequencyAssignment, PlanResult, Violation
)
from src.engine.constraints import FrequencyRules


def _ho_weight(ho_count: Optional[int]) -> float:
    """
    Weight multiplier based on handover count.
    Uses log2(1 + ho) so high-HO relations are penalised more
    but the scale doesn't explode.
    If HO data is unavailable, returns 1.0 (neutral).
    """
    if ho_count is None:
        return 1.0
    return math.log2(1 + ho_count) if ho_count > 0 else 0.1


def _distance_penalty(distance_km: float, threshold_km: float) -> float:
    """
    Inverse-distance penalty for co-channel or adjacent between
    geographically close cells.
    Returns a value in [0, 1] where closer = higher penalty.
    """
    if distance_km <= 0:
        return 1.0
    if distance_km >= threshold_km:
        return 0.0
    return 1.0 - (distance_km / threshold_km)


def _get_freq_pairs(
    assignment_a: FrequencyAssignment,
    assignment_b: FrequencyAssignment
) -> list[tuple[int, str, int, str]]:
    """
    Generate all frequency pairs between two cell assignments.
    Returns: [(freq_a, type_a, freq_b, type_b), ...]
    """
    pairs = []

    freqs_a = [(assignment_a.bcch, "BCCH")]
    freqs_a.extend((f, "TCH") for f in assignment_a.tch_list)

    freqs_b = [(assignment_b.bcch, "BCCH")]
    freqs_b.extend((f, "TCH") for f in assignment_b.tch_list)

    for freq_a, type_a in freqs_a:
        for freq_b, type_b in freqs_b:
            pairs.append((freq_a, type_a, freq_b, type_b))

    return pairs


def evaluate_plan(
    assignments: dict[str, FrequencyAssignment],
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    plan_id: int = 0,
    run_id: int = 1,
    collect_violations: bool = True
) -> PlanResult:
    """
    Evaluate a complete frequency plan.
    """
    result = PlanResult(plan_id=plan_id, run_id=run_id)
    violations = [] if collect_violations else None
    total_score = 0.0

    # Track which pairs we've already checked
    checked_pairs: set[tuple[str, str]] = set()

    # ── 1. Check all neighbor pairs ────────────────────────────────
    for relation in neighbor_graph.all_relations():
        ca_id = relation.cell_a_id
        cb_id = relation.cell_b_id

        if ca_id not in assignments or cb_id not in assignments:
            continue

        pair_key = tuple(sorted((ca_id, cb_id)))
        if pair_key in checked_pairs:
            continue
        checked_pairs.add(pair_key)

        a_assign = assignments[ca_id]
        b_assign = assignments[cb_id]
        cell_a = cells.get(ca_id)
        cell_b = cells.get(cb_id)
        same_station = (cell_a and cell_b and
                        cell_a.station.station_id == cell_b.station.station_id)

        ho_w = _ho_weight(relation.ho_count)

        for freq_a, type_a, freq_b, type_b in _get_freq_pairs(a_assign, b_assign):
            diff = abs(freq_a - freq_b)

            is_cochannel = (diff == 0)
            is_adjacent = (diff == 1)

            if not is_cochannel and not is_adjacent:
                continue

            weight = rules.get_weight(
                same_station=same_station,
                is_cochannel=is_cochannel,
                channel_type_a=type_a,
                channel_type_b=type_b
            )

            is_hard = rules.is_hard_constraint_violated(
                same_station=same_station,
                is_neighbor=True,
                is_cochannel=is_cochannel,
                channel_type_a=type_a,
                channel_type_b=type_b
            )

            cost = (weight + (100000.0 if is_hard else 0.0)) * ho_w
            total_score += cost

            # Count by type
            if is_cochannel:
                if type_a == "BCCH" and type_b == "BCCH":
                    result.cochannel_bcch_bcch += 1
                elif "BCCH" in (type_a, type_b):
                    result.cochannel_bcch_tch += 1
                else:
                    result.cochannel_tch_tch += 1
                result.cochannel_ho_weighted += ho_w
                if same_station:
                    result.same_station_cochannel += 1
            else:
                if type_a == "BCCH" and type_b == "BCCH":
                    result.adjacent_bcch_bcch += 1
                elif "BCCH" in (type_a, type_b):
                    result.adjacent_bcch_tch += 1
                else:
                    result.adjacent_tch_tch += 1
                result.adjacent_ho_weighted += ho_w
                if same_station:
                    result.same_station_adjacent += 1

            if collect_violations:
                v = Violation(
                    cell_a_id=ca_id,
                    cell_b_id=cb_id,
                    freq_a=freq_a,
                    freq_b=freq_b,
                    violation_type="cochannel" if is_cochannel else "adjacent",
                    channel_type_a=type_a,
                    channel_type_b=type_b,
                    ho_count=relation.ho_count,
                    distance_km=relation.distance_km,
                    weighted_cost=cost
                )
                violations.append(v)

    # ── 2. Check same-station pairs not already in neighbors ───────
    station_cells: dict[str, list[str]] = {}
    for cid, cell in cells.items():
        if cid in assignments:
            sid = cell.station.station_id
            if sid not in station_cells:
                station_cells[sid] = []
            station_cells[sid].append(cid)

    for sid, cell_ids in station_cells.items():
        for i in range(len(cell_ids)):
            for j in range(i + 1, len(cell_ids)):
                ca_id = cell_ids[i]
                cb_id = cell_ids[j]
                pair_key = tuple(sorted((ca_id, cb_id)))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                a_assign = assignments[ca_id]
                b_assign = assignments[cb_id]

                for freq_a, type_a, freq_b, type_b in _get_freq_pairs(a_assign, b_assign):
                    diff = abs(freq_a - freq_b)
                    is_cochannel = (diff == 0)
                    is_adjacent = (diff == 1)

                    if not is_cochannel and not is_adjacent:
                        continue

                    weight = rules.get_weight(
                        same_station=True,
                        is_cochannel=is_cochannel,
                        channel_type_a=type_a,
                        channel_type_b=type_b
                    )

                    is_hard = rules.is_hard_constraint_violated(
                        same_station=True,
                        is_neighbor=False,
                        is_cochannel=is_cochannel,
                        channel_type_a=type_a,
                        channel_type_b=type_b
                    )

                    cost = weight + (100000.0 if is_hard else 0.0)
                    total_score += cost

                    if is_cochannel:
                        result.same_station_cochannel += 1
                        if type_a == "BCCH" and type_b == "BCCH":
                            result.cochannel_bcch_bcch += 1
                        elif "BCCH" in (type_a, type_b):
                            result.cochannel_bcch_tch += 1
                        else:
                            result.cochannel_tch_tch += 1
                    else:
                        result.same_station_adjacent += 1
                        if type_a == "BCCH" and type_b == "BCCH":
                            result.adjacent_bcch_bcch += 1
                        elif "BCCH" in (type_a, type_b):
                            result.adjacent_bcch_tch += 1
                        else:
                            result.adjacent_tch_tch += 1

                    if collect_violations:
                        v = Violation(
                            cell_a_id=ca_id,
                            cell_b_id=cb_id,
                            freq_a=freq_a,
                            freq_b=freq_b,
                            violation_type="cochannel" if is_cochannel else "adjacent",
                            channel_type_a=type_a,
                            channel_type_b=type_b,
                            ho_count=None,
                            distance_km=0.0,
                            weighted_cost=weight
                        )
                        violations.append(v)

    # ── 3. Internal cell separation check ──────────────────────────
    for cid, assign in assignments.items():
        all_freqs = [("BCCH", assign.bcch)] + [("TCH", f) for f in assign.tch_list]
        for i in range(len(all_freqs)):
            for j in range(i + 1, len(all_freqs)):
                type_i, freq_i = all_freqs[i]
                type_j, freq_j = all_freqs[j]
                diff = abs(freq_i - freq_j)
                if diff < rules.min_separation_same_cell:
                    penalty = rules.weight_same_station_cochannel * 2
                    total_score += penalty

    # ── 4. Distance penalty for non-neighbor close cells ───────────
    if rules.enable_distance_penalty:
        all_cell_ids = list(assignments.keys())
        for i in range(len(all_cell_ids)):
            for j in range(i + 1, len(all_cell_ids)):
                ca_id = all_cell_ids[i]
                cb_id = all_cell_ids[j]
                pair_key = tuple(sorted((ca_id, cb_id)))
                if pair_key in checked_pairs:
                    continue

                cell_a = cells.get(ca_id)
                cell_b = cells.get(cb_id)
                if not cell_a or not cell_b:
                    continue
                if cell_a.station.station_id == cell_b.station.station_id:
                    continue

                from src.models.neighbor import haversine_distance
                dist = haversine_distance(
                    cell_a.station.latitude, cell_a.station.longitude,
                    cell_b.station.latitude, cell_b.station.longitude
                )

                if dist >= rules.distance_threshold_km:
                    continue

                dp = _distance_penalty(dist, rules.distance_threshold_km)
                a_assign = assignments[ca_id]
                b_assign = assignments[cb_id]

                for freq_a, type_a, freq_b, type_b in _get_freq_pairs(a_assign, b_assign):
                    diff = abs(freq_a - freq_b)
                    if diff == 0:
                        total_score += rules.weight_distance * dp

    result.score = total_score
    result.assignments = list(assignments.values())
    if collect_violations:
        result.violations = violations

    return result


def quick_score(
    assignments: dict[str, FrequencyAssignment],
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules
) -> float:
    """
    Fast scoring without collecting detailed violations.
    Used during genetic algorithm evaluation for performance.
    """
    result = evaluate_plan(
        assignments, cells, neighbor_graph, rules,
        collect_violations=False
    )
    return result.score

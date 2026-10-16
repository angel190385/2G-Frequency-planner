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


def _ho_weight(
    ho_count: Optional[int],
    model: str = "logarithmic",
    stats: Optional[dict[str, float]] = None
) -> float:
    """
    Weight multiplier based on handover count and the configured statistical model.

    Statistical Models:
    1. 'logarithmic': w = log2(1 + ho) if ho > 0 else 0.1
       Sublinear scaling, mitigates extreme variance and prevents network bottlenecks
       from overwhelming softer neighbor relations.
    2. 'linear': w = 1.0 + (ho / mean_ho) * 1.5
       Directly proportional to handover traffic volume relative to network mean.
    3. 'sigmoid': w = 0.2 + 3.8 / (1.0 + exp(-k * (ho - median_ho)))
       S-curve centered at median handover. Soft penalty below threshold, rapid
       saturation for relations exceeding the critical threshold.
    4. 'exponential': w = 1.0 + 5.0 * (ho / max_ho) ** 2
       Quadratic Pareto law (80/20) heavily penalizing top hotspot relations.
    5. 'uniform': w = 1.0
       Pure topological assignment without handover weighting.
    """
    if ho_count is None:
        return 1.0
    if ho_count <= 0:
        return 0.1

    if model == "uniform":
        return 1.0

    mean_ho = stats.get("mean", 10.0) if stats else 10.0
    median_ho = stats.get("median", 10.0) if stats else 10.0
    max_ho = stats.get("max", 100.0) if stats else 100.0

    if model == "linear":
        ratio = ho_count / max(1.0, mean_ho)
        return max(0.1, 1.0 + ratio * 1.5)

    elif model == "sigmoid":
        scale = max(5.0, median_ho * 0.5)
        z = (ho_count - median_ho) / scale
        z = max(-10.0, min(10.0, z))  # Prevent math overflow
        sig = 1.0 / (1.0 + math.exp(-z))
        return max(0.1, 0.2 + 3.8 * sig)

    elif model == "exponential":
        ratio = ho_count / max(1.0, max_ho)
        ratio = min(2.0, ratio)
        return max(0.1, 1.0 + 5.0 * (ratio ** 2))

    else:
        # Default: logarithmic
        return math.log2(1 + ho_count)


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


def precompute_close_cell_pairs(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    threshold_km: float
) -> list[tuple[str, str, float]]:
    """
    Precompute non-neighbor, different-station cell pairs within threshold_km.
    Uses a spatial grid to achieve O(N) performance, eliminating O(N^2) pairwise loops.
    Returns: [(cell_a_id, cell_b_id, distance_penalty), ...]
    """
    from src.models.neighbor import haversine_distance

    neighbor_pairs = {
        tuple(sorted((r.cell_a_id, r.cell_b_id)))
        for r in neighbor_graph.all_relations()
    }

    station_cells: dict[str, list[str]] = {}
    stations: dict[str, tuple[float, float]] = {}
    for cid, cell in cells.items():
        sid = cell.station.station_id
        station_cells.setdefault(sid, []).append(cid)
        if sid not in stations:
            stations[sid] = (cell.station.latitude, cell.station.longitude)

    grid_size_lat = max(0.01, threshold_km / 111.0)
    grid: dict[tuple[int, int], list[str]] = {}

    for sid, (lat, lon) in stations.items():
        cos_lat = max(0.2, math.cos(math.radians(lat)))
        grid_size_lon = max(0.01, threshold_km / (111.0 * cos_lat))
        gx = int(lat / grid_size_lat)
        gy = int(lon / grid_size_lon)
        grid.setdefault((gx, gy), []).append(sid)

    checked_stations: set[tuple[str, str]] = set()
    close_cell_pairs: list[tuple[str, str, float]] = []

    for (gx, gy), sids in grid.items():
        neighbor_sids = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_sids.extend(grid.get((gx + dx, gy + dy), []))

        for sid_a in sids:
            lat_a, lon_a = stations[sid_a]
            for sid_b in neighbor_sids:
                if sid_a >= sid_b:
                    continue
                st_pair = (sid_a, sid_b)
                if st_pair in checked_stations:
                    continue
                checked_stations.add(st_pair)

                lat_b, lon_b = stations[sid_b]
                dist = haversine_distance(lat_a, lon_a, lat_b, lon_b)
                if dist >= threshold_km:
                    continue

                dp = _distance_penalty(dist, threshold_km)
                for ca_id in station_cells[sid_a]:
                    for cb_id in station_cells[sid_b]:
                        pair_key = tuple(sorted((ca_id, cb_id)))
                        if pair_key not in neighbor_pairs:
                            close_cell_pairs.append((ca_id, cb_id, dp))

    return close_cell_pairs


def evaluate_plan(
    assignments: dict[str, FrequencyAssignment],
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    plan_id: int = 0,
    run_id: int = 1,
    collect_violations: bool = True,
    precomputed_close_pairs: Optional[list[tuple[str, str, float]]] = None
) -> PlanResult:
    """
    Evaluate a complete frequency plan.
    """
    result = PlanResult(plan_id=plan_id, run_id=run_id)
    violations = [] if collect_violations else None
    total_score = 0.0

    ho_stats = neighbor_graph.get_ho_statistics()
    ho_model = getattr(rules, "ho_statistical_model", "logarithmic")

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

        # Fast range overlap check: if frequency sets cannot produce diff <= 1, skip all pairs
        all_a = [a_assign.bcch] + a_assign.tch_list
        all_b = [b_assign.bcch] + b_assign.tch_list
        if min(all_a) - 1 > max(all_b) or min(all_b) - 1 > max(all_a):
            continue

        cell_a = cells.get(ca_id)
        cell_b = cells.get(cb_id)
        same_station = bool(
            (cell_a and cell_b and cell_a.station.station_id == cell_b.station.station_id)
            or (relation.distance_km == 0.0)
        )

        ho_w = _ho_weight(relation.ho_count, model=ho_model, stats=ho_stats)

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

            hard_penalty = 10_000_000.0 if is_hard else 0.0
            cost = (weight + hard_penalty) * ho_w
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

                    hard_penalty = 10_000_000.0 if is_hard else 0.0
                    cost = weight + hard_penalty
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
                            weighted_cost=cost
                        )
                        violations.append(v)

    # ── 3. Internal cell separation check ──────────────────────────
    # Under no circumstances can frequencies repeat in the same cell between BCCH and TCH or among TCHs
    for cid, assign in assignments.items():
        all_freqs = [("BCCH", assign.bcch)] + [("TCH", f) for f in assign.tch_list]
        for i in range(len(all_freqs)):
            for j in range(i + 1, len(all_freqs)):
                type_i, freq_i = all_freqs[i]
                type_j, freq_j = all_freqs[j]
                diff = abs(freq_i - freq_j)
                if diff == 0:
                    # Critical cochannel violation within the same cell
                    hard_cost = 10_000_000.0
                    total_score += hard_cost
                    if "BCCH" in (type_i, type_j):
                        result.cochannel_bcch_tch += 1
                    else:
                        result.cochannel_tch_tch += 1
                    if collect_violations:
                        v = Violation(
                            cell_a_id=cid,
                            cell_b_id=cid,
                            freq_a=freq_i,
                            freq_b=freq_j,
                            violation_type="cochannel",
                            channel_type_a=type_i,
                            channel_type_b=type_j,
                            ho_count=None,
                            distance_km=0.0,
                            weighted_cost=hard_cost
                        )
                        violations.append(v)
                elif diff < rules.min_separation_same_cell:
                    penalty = rules.weight_same_station_adjacent * 2
                    total_score += penalty

    # ── 4. Distance penalty for non-neighbor close cells ───────────
    if rules.enable_distance_penalty:
        close_pairs = precomputed_close_pairs
        if close_pairs is None:
            close_pairs = precompute_close_cell_pairs(cells, neighbor_graph, rules.distance_threshold_km)

        for ca_id, cb_id, dp in close_pairs:
            a_assign = assignments.get(ca_id)
            b_assign = assignments.get(cb_id)
            if not a_assign or not b_assign:
                continue

            freqs_a = [a_assign.bcch] + a_assign.tch_list
            freqs_b = [b_assign.bcch] + b_assign.tch_list
            cochannel_count = len(set(freqs_a) & set(freqs_b))
            if cochannel_count > 0:
                total_score += rules.weight_distance * dp * cochannel_count

    result.score = total_score
    result.assignments = list(assignments.values())
    if collect_violations:
        result.violations = violations

    return result


def quick_score(
    assignments: dict[str, FrequencyAssignment],
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    precomputed_close_pairs: Optional[list[tuple[str, str, float]]] = None
) -> float:
    """
    Fast scoring without collecting detailed violations.
    Used during genetic algorithm evaluation for performance.
    """
    result = evaluate_plan(
        assignments, cells, neighbor_graph, rules,
        plan_id=0, run_id=1,
        collect_violations=False,
        precomputed_close_pairs=precomputed_close_pairs
    )
    return result.score

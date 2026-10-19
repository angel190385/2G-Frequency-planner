"""
Cost function for evaluating complete frequency plans.

Evaluates a full frequency assignment against the neighbor graph
and constraint rules, producing a weighted score and detailed violations.
"""
from __future__ import annotations
import math
from typing import Optional

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph, calculate_bearing, angle_diff
from src.models.frequency_plan import (
    FrequencyAssignment, PlanResult, Violation
)
from src.engine.constraints import FrequencyRules


def _azimuth_factor(
    cell_a: Optional[Cell],
    cell_b: Optional[Cell],
    distance_km: float = 0.0,
    rules: Optional[FrequencyRules] = None
) -> float:
    """
    Directional weight factor based on sector antenna azimuths and geometric bearing.

    Facing cells (enfrentadas): cells from different nearby sites whose main antenna
    beams point towards each other incur a higher interference penalty (up to azimuth_weight_max).

    Cells pointing in the same direction into different zones or divergent/back-to-back
    sectors incur significantly lower interference risk (down to azimuth_weight_min).

    If distance is 0 (colocated/same site) or cell data is missing, returns 1.0 (neutral).
    """
    if cell_a is None or cell_b is None:
        return 1.0
    if distance_km <= 0.0:
        return 1.0
    if not cell_a.station or not cell_b.station:
        return 1.0
    if cell_a.station.station_id == cell_b.station.station_id:
        return 1.0

    lat_a = cell_a.station.latitude
    lon_a = cell_a.station.longitude
    lat_b = cell_b.station.latitude
    lon_b = cell_b.station.longitude

    if lat_a == lat_b and lon_a == lon_b:
        return 1.0

    bearing_a_to_b = calculate_bearing(lat_a, lon_a, lat_b, lon_b)
    bearing_b_to_a = (bearing_a_to_b + 180.0) % 360.0

    # Angular deviation between antenna azimuth and line-of-sight towards the other station
    dev_a = angle_diff(cell_a.azimuth, bearing_a_to_b)
    dev_b = angle_diff(cell_b.azimuth, bearing_b_to_a)

    # Antenna directional envelope w(dev) in [0, 1] using cosine attenuation
    w_a = (1.0 + math.cos(math.radians(dev_a))) / 2.0
    w_b = (1.0 + math.cos(math.radians(dev_b))) / 2.0

    # Alignment coupling index:
    # 0.6 * (product of lobes) + 0.4 * (average illumination)
    alignment = 0.6 * (w_a * w_b) + 0.4 * ((w_a + w_b) / 2.0)

    min_w = getattr(rules, "azimuth_weight_min", 0.3) if rules else 0.3
    max_w = getattr(rules, "azimuth_weight_max", 1.8) if rules else 1.8

    return min_w + (max_w - min_w) * alignment


def _distance_factor(distance_km: float, decay: float = 0.3) -> float:
    """
    Continuous distance factor for RF interference potential.
    Closer cells have significantly higher interference impact.
    For d = 0 (same station / colocated): returns 2.0.
    For d > 0: returns max(0.1, 1.0 / (1.0 + decay * distance_km)).
    """
    if distance_km <= 0.0:
        return 2.0
    return max(0.1, 1.0 / (1.0 + decay * distance_km))


def _ho_weight(
    ho_count: Optional[int],
    model: str = "logarithmic",
    stats: Optional[dict[str, float]] = None
) -> float:
    """
    Weight multiplier based on handover count and the configured statistical model.
    When HO is missing or <= 0, returns 1.0 (neutral baseline, ensuring cells without
    HO telemetry are NOT ignored or discounted).
    When HO > 0, provides a smooth priority boost for active traffic routes.
    """
    if ho_count is None or ho_count <= 0:
        return 1.0

    if model == "uniform":
        return 1.0

    mean_ho = stats.get("mean", 10.0) if stats else 10.0
    median_ho = stats.get("median", 10.0) if stats else 10.0
    max_ho = stats.get("max", 100.0) if stats else 100.0

    if model == "linear":
        ratio = ho_count / max(1.0, mean_ho)
        return min(3.0, 1.0 + ratio * 0.75)

    elif model == "sigmoid":
        scale = max(5.0, median_ho * 0.5)
        z = (ho_count - median_ho) / scale
        z = max(-10.0, min(10.0, z))  # Prevent math overflow
        sig = 1.0 / (1.0 + math.exp(-z))
        return 1.0 + 1.5 * sig

    elif model == "exponential":
        ratio = min(2.0, ho_count / max(1.0, max_ho))
        return 1.0 + 2.0 * (ratio ** 2)

    else:
        # Default: logarithmic
        return 1.0 + 0.5 * math.log2(1.0 + ho_count)


def _is_freq_inherited(cell: Optional[Cell], channel_type: str, freq: int) -> bool:
    """Check if a frequency was already present in the baseline cell configuration."""
    if not cell:
        return False
    if channel_type == "BCCH":
        return cell.bcch == freq
    else:
        return freq in cell.tch_list


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
    threshold_km: float,
    rules: Optional[FrequencyRules] = None,
    active_cell_ids: Optional[set[str]] = None
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
                    cell_a = cells.get(ca_id)
                    for cb_id in station_cells[sid_b]:
                        if active_cell_ids is not None:
                            if ca_id not in active_cell_ids and cb_id not in active_cell_ids:
                                continue
                        pair_key = tuple(sorted((ca_id, cb_id)))
                        if pair_key not in neighbor_pairs:
                            cell_b = cells.get(cb_id)
                            az_f = _azimuth_factor(cell_a, cell_b, dist, rules=rules) if (rules and getattr(rules, "enable_azimuth_weight", True)) else 1.0
                            close_cell_pairs.append((ca_id, cb_id, dp * az_f))

    return close_cell_pairs


def evaluate_plan(
    assignments: dict[str, FrequencyAssignment],
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    plan_id: int = 0,
    run_id: int = 1,
    collect_violations: bool = True,
    precomputed_close_pairs: Optional[list[tuple[str, str, float]]] = None,
    active_cell_ids: Optional[set[str]] = None
) -> PlanResult:
    """
    Evaluate a complete frequency plan.
    """
    result = PlanResult(plan_id=plan_id, run_id=run_id)
    violations = [] if collect_violations else None
    total_score = 0.0
    inherited_cost = 0.0
    new_assignment_cost = 0.0

    ho_stats = neighbor_graph.get_ho_statistics()
    ho_model = getattr(rules, "ho_statistical_model", "logarithmic")
    decay = getattr(rules, "distance_decay", 0.3)
    neighbor_mult = getattr(rules, "weight_neighbor_factor", 1.3)

    # Track which pairs we've already checked
    checked_pairs: set[tuple[str, str]] = set()

    # ── 1. Check all neighbor pairs ────────────────────────────────
    for relation in neighbor_graph.all_relations():
        ca_id = relation.cell_a_id
        cb_id = relation.cell_b_id

        if ca_id not in assignments or cb_id not in assignments:
            continue

        if active_cell_ids is not None:
            if ca_id not in active_cell_ids and cb_id not in active_cell_ids:
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

        dist_f = _distance_factor(relation.distance_km, decay=decay)
        ho_w = _ho_weight(relation.ho_count, model=ho_model, stats=ho_stats)
        az_f = _azimuth_factor(cell_a, cell_b, relation.distance_km, rules=rules) if getattr(rules, "enable_azimuth_weight", True) else 1.0
        rel_multiplier = 2.0 if same_station else (dist_f * neighbor_mult * ho_w * az_f)

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
            cost = (weight + hard_penalty) * rel_multiplier
            total_score += cost

            # Cost attribution: inherited vs new assignment
            a_inh = _is_freq_inherited(cell_a, type_a, freq_a)
            b_inh = _is_freq_inherited(cell_b, type_b, freq_b)
            is_inh = a_inh and b_inh
            if is_inh:
                inherited_cost += cost
            else:
                new_assignment_cost += cost

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
                    weighted_cost=cost,
                    is_inherited=is_inh
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

                if active_cell_ids is not None:
                    if ca_id not in active_cell_ids and cb_id not in active_cell_ids:
                        continue

                pair_key = tuple(sorted((ca_id, cb_id)))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                a_assign = assignments[ca_id]
                b_assign = assignments[cb_id]
                cell_a = cells.get(ca_id)
                cell_b = cells.get(cb_id)

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

                    a_inh = _is_freq_inherited(cell_a, type_a, freq_a)
                    b_inh = _is_freq_inherited(cell_b, type_b, freq_b)
                    is_inh = a_inh and b_inh
                    if is_inh:
                        inherited_cost += cost
                    else:
                        new_assignment_cost += cost

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
                            weighted_cost=cost,
                            is_inherited=is_inh
                        )
                        violations.append(v)

    # ── 3. Internal cell separation check ──────────────────────────
    # Under no circumstances can frequencies repeat in the same cell between BCCH and TCH or among TCHs
    for cid, assign in assignments.items():
        if active_cell_ids is not None and cid not in active_cell_ids:
            continue
        cell_orig = cells.get(cid)
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
                    a_inh = _is_freq_inherited(cell_orig, type_i, freq_i)
                    b_inh = _is_freq_inherited(cell_orig, type_j, freq_j)
                    is_inh = a_inh and b_inh
                    if is_inh:
                        inherited_cost += hard_cost
                    else:
                        new_assignment_cost += hard_cost

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
                            weighted_cost=hard_cost,
                            is_inherited=is_inh
                        )
                        violations.append(v)
                elif diff < rules.min_separation_same_cell:
                    penalty = rules.weight_same_station_adjacent * 2
                    total_score += penalty
                    a_inh = _is_freq_inherited(cell_orig, type_i, freq_i)
                    b_inh = _is_freq_inherited(cell_orig, type_j, freq_j)
                    is_inh = a_inh and b_inh
                    if is_inh:
                        inherited_cost += penalty
                    else:
                        new_assignment_cost += penalty

    # ── 4. Distance penalty for non-neighbor close cells ───────────
    if rules.enable_distance_penalty:
        close_pairs = precomputed_close_pairs
        if close_pairs is None:
            close_pairs = precompute_close_cell_pairs(cells, neighbor_graph, rules.distance_threshold_km, rules=rules, active_cell_ids=active_cell_ids)

        for ca_id, cb_id, dp in close_pairs:
            if active_cell_ids is not None:
                if ca_id not in active_cell_ids and cb_id not in active_cell_ids:
                    continue
            a_assign = assignments.get(ca_id)
            b_assign = assignments.get(cb_id)
            if not a_assign or not b_assign:
                continue

            freqs_a = [a_assign.bcch] + a_assign.tch_list
            freqs_b = [b_assign.bcch] + b_assign.tch_list
            common_freqs = set(freqs_a) & set(freqs_b)
            if common_freqs:
                cost = rules.weight_distance * dp * len(common_freqs)
                total_score += cost
                cell_a = cells.get(ca_id)
                cell_b = cells.get(cb_id)
                for cf in common_freqs:
                    a_inh = (cell_a.bcch == cf or cf in cell_a.tch_list) if cell_a else False
                    b_inh = (cell_b.bcch == cf or cf in cell_b.tch_list) if cell_b else False
                    unit_cost = rules.weight_distance * dp
                    if a_inh and b_inh:
                        inherited_cost += unit_cost
                    else:
                        new_assignment_cost += unit_cost

    result.score = total_score
    result.inherited_cost = inherited_cost
    result.new_assignment_cost = new_assignment_cost
    result.assignments = list(assignments.values())
    if collect_violations:
        result.violations = violations

    return result


def quick_score(
    assignments: dict[str, FrequencyAssignment],
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    precomputed_close_pairs: Optional[list[tuple[str, str, float]]] = None,
    active_cell_ids: Optional[set[str]] = None
) -> float:
    """
    Fast scoring without collecting detailed violations.
    Used during genetic algorithm evaluation for performance.
    """
    result = evaluate_plan(
        assignments, cells, neighbor_graph, rules,
        plan_id=0, run_id=1,
        collect_violations=False,
        precomputed_close_pairs=precomputed_close_pairs,
        active_cell_ids=active_cell_ids
    )
    return result.score

"""
High-performance vectorized plan evaluator.
Precomputes indices, static weights, and spatial relations so scoring can run
in microseconds using numpy vectorization without Python object overhead.
"""
from __future__ import annotations
import math
import numpy as np
from typing import Optional

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import FrequencyAssignment, PlanResult, Violation
from src.engine.constraints import FrequencyRules
from src.engine.cost_function import (
    _ho_weight, _distance_factor, _distance_penalty, _azimuth_factor, precompute_close_cell_pairs
)

HARD_PENALTY: float = 10_000_000.0


class FastPlanEvaluator:
    """
    Optimized plan evaluator that pre-indexes all cell and graph topology
    for vector-accelerated fitness evaluation.
    """

    def __init__(
        self,
        cells: dict[str, Cell],
        neighbor_graph: NeighborGraph,
        rules: FrequencyRules,
        precomputed_close_pairs: Optional[list[tuple[str, str, float]]] = None
    ):
        self.cells = cells
        self.neighbor_graph = neighbor_graph
        self.rules = rules

        # 1. Map cells to integer indices
        self.cell_ids = sorted(cells.keys())
        self.cell_to_idx = {cid: i for i, cid in enumerate(self.cell_ids)}
        self.n_cells = len(self.cell_ids)

        # Max TCH per cell
        self.max_tch = max((len(c.tch_list) + getattr(c, "new_tch_count", 0) for c in cells.values()), default=0)

        # Base fixed frequencies matrix (N, 1 + max_tch) where col 0 is BCCH, cols 1.. are TCH
        self.base_freq_matrix = np.zeros((self.n_cells, 1 + self.max_tch), dtype=np.int32)
        # Mask indicating which slots are active (exist)
        self.slot_mask = np.zeros((self.n_cells, 1 + self.max_tch), dtype=bool)

        for cid, cell in cells.items():
            idx = self.cell_to_idx[cid]
            if cell.bcch is not None and cell.bcch > 0:
                self.base_freq_matrix[idx, 0] = cell.bcch
                self.slot_mask[idx, 0] = True
            for s, f in enumerate(cell.tch_list):
                if f > 0:
                    self.base_freq_matrix[idx, 1 + s] = f
                    self.slot_mask[idx, 1 + s] = True
            for k in range(getattr(cell, "new_tch_count", 0)):
                self.slot_mask[idx, 1 + len(cell.tch_list) + k] = True

        # Precompute close pairs if enabled
        if rules.enable_distance_penalty:
            if precomputed_close_pairs is None:
                self.close_pairs_raw = precompute_close_cell_pairs(
                    cells, neighbor_graph, rules.distance_threshold_km, rules=rules
                )
            else:
                self.close_pairs_raw = precomputed_close_pairs
        else:
            self.close_pairs_raw = []

        # Track which (cell_idx, col_idx) slots were marked for recalculation
        self.recalculated_slots: set[tuple[int, int]] = set()
        for cid, cell in cells.items():
            if cid not in self.cell_to_idx:
                continue
            c_idx = self.cell_to_idx[cid]
            if cell.recalculate_bcch:
                self.recalculated_slots.add((c_idx, 0))
            for s_idx in cell.tch_indices_to_recalculate:
                self.recalculated_slots.add((c_idx, 1 + s_idx))
            for i in range(cell.new_tch_count):
                self.recalculated_slots.add((c_idx, 1 + len(cell.tch_list) + i))

        # 2. Build indexed relation structures
        self._build_relations()

    def _build_relations(self):
        """Index all neighbor relations and same-station pairs into integer arrays."""
        rules = self.rules
        ho_stats = self.neighbor_graph.get_ho_statistics()
        ho_model = getattr(rules, "ho_statistical_model", "logarithmic")

        checked_pairs = set()

        # Structures for neighbor relations
        rel_a_list = []
        rel_b_list = []
        same_st_list = []
        ho_w_list = []
        dist_list = []
        ho_count_list = []

        for relation in self.neighbor_graph.all_relations():
            ca_id = relation.cell_a_id
            cb_id = relation.cell_b_id
            if ca_id not in self.cell_to_idx or cb_id not in self.cell_to_idx:
                continue

            pair_key = tuple(sorted((ca_id, cb_id)))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            idx_a = self.cell_to_idx[ca_id]
            idx_b = self.cell_to_idx[cb_id]

            cell_a = self.cells[ca_id]
            cell_b = self.cells[cb_id]
            same_st = bool(
                (cell_a.station.station_id == cell_b.station.station_id)
                or (relation.distance_km == 0.0)
            )

            decay = getattr(rules, "distance_decay", 0.3)
            neighbor_mult = getattr(rules, "weight_neighbor_factor", 1.3)
            dist_f = _distance_factor(relation.distance_km, decay=decay)
            ho_w = _ho_weight(relation.ho_count, model=ho_model, stats=ho_stats)
            az_f = _azimuth_factor(cell_a, cell_b, relation.distance_km, rules=rules) if getattr(rules, "enable_azimuth_weight", True) else 1.0
            rel_mult = 2.0 if same_st else (dist_f * neighbor_mult * ho_w * az_f)

            rel_a_list.append(idx_a)
            rel_b_list.append(idx_b)
            same_st_list.append(same_st)
            ho_w_list.append(rel_mult)
            dist_list.append(relation.distance_km)
            ho_count_list.append(relation.ho_count if relation.ho_count is not None else -1)

        raw_rel_a = np.array(rel_a_list, dtype=np.int32)
        raw_rel_b = np.array(rel_b_list, dtype=np.int32)
        raw_same_st = np.array(same_st_list, dtype=bool)
        raw_ho_w = np.array(ho_w_list, dtype=np.float64)
        self.raw_rel_dist = np.array(dist_list, dtype=np.float64)
        self.raw_rel_ho_count = np.array(ho_count_list, dtype=np.int32)

        # Same station pairs not in neighbor graph
        st_cells: dict[str, list[int]] = {}
        for cid, cell in self.cells.items():
            sid = cell.station.station_id
            st_cells.setdefault(sid, []).append(self.cell_to_idx[cid])

        same_st_a = []
        same_st_b = []
        for sid, idxs in st_cells.items():
            for i in range(len(idxs)):
                for j in range(i + 1, len(idxs)):
                    idx_a = idxs[i]
                    idx_b = idxs[j]
                    ca_id = self.cell_ids[idx_a]
                    cb_id = self.cell_ids[idx_b]
                    pair_key = tuple(sorted((ca_id, cb_id)))
                    if pair_key in checked_pairs:
                        continue
                    checked_pairs.add(pair_key)
                    same_st_a.append(idx_a)
                    same_st_b.append(idx_b)

        raw_same_st_a = np.array(same_st_a, dtype=np.int32)
        raw_same_st_b = np.array(same_st_b, dtype=np.int32)

        # Close pairs for distance penalty
        cp_a = []
        cp_b = []
        cp_dp = []
        for ca_id, cb_id, dp in self.close_pairs_raw:
            if ca_id in self.cell_to_idx and cb_id in self.cell_to_idx:
                cp_a.append(self.cell_to_idx[ca_id])
                cp_b.append(self.cell_to_idx[cb_id])
                cp_dp.append(dp)

        self.close_a = np.array(cp_a, dtype=np.int32)
        self.close_b = np.array(cp_b, dtype=np.int32)
        self.close_dp = np.array(cp_dp, dtype=np.float64)

        # Precompute slot pairs for neighbor relations
        self.slot_pairs_rel = []
        if len(raw_rel_a) > 0:
            for col_a in range(1 + self.max_tch):
                for col_b in range(1 + self.max_tch):
                    valid = self.slot_mask[raw_rel_a, col_a] & self.slot_mask[raw_rel_b, col_b]
                    if not np.any(valid):
                        continue

                    a_filt = raw_rel_a[valid]
                    b_filt = raw_rel_b[valid]
                    st_filt = raw_same_st[valid]
                    hw_filt = raw_ho_w[valid]
                    orig_indices = np.where(valid)[0]

                    is_bcch_bcch = (col_a == 0 and col_b == 0)
                    is_bcch_tch = ((col_a == 0 and col_b > 0) or (col_a > 0 and col_b == 0))

                    # Cochannel weights vector
                    cochan_hard = st_filt.copy()  # same_station cochannel is ALWAYS hard
                    if is_bcch_bcch:
                        cochan_w = np.where(st_filt, rules.weight_same_station_cochannel, rules.weight_cochannel_bcch_bcch)
                        cochan_hard |= (~st_filt & (not rules.allow_cochannel_bcch_bcch_neighbors))
                    elif is_bcch_tch:
                        cochan_w = np.where(st_filt, rules.weight_same_station_cochannel, rules.weight_cochannel_bcch_tch)
                        cochan_hard |= (~st_filt & (not rules.allow_cochannel_bcch_tch_neighbors))
                    else:
                        cochan_w = np.where(st_filt, rules.weight_same_station_cochannel, rules.weight_cochannel_tch_tch)
                        cochan_hard |= (~st_filt & (not rules.allow_cochannel_tch_tch_neighbors))

                    cost_cochan_vec = (cochan_w + np.where(cochan_hard, HARD_PENALTY, 0.0)) * hw_filt

                    # Adjacent weights vector
                    if is_bcch_bcch:
                        adj_w = np.where(st_filt, rules.weight_same_station_adjacent, rules.weight_adjacent_bcch_bcch)
                        adj_hard = np.where(st_filt, True, not rules.allow_adjacent_bcch_bcch_neighbors)
                    elif is_bcch_tch:
                        adj_w = np.where(st_filt, rules.weight_same_station_adjacent, rules.weight_adjacent_bcch_tch)
                        adj_hard = np.where(st_filt, True, not rules.allow_adjacent_bcch_tch_neighbors)
                    else:
                        adj_w = np.where(st_filt, rules.weight_same_station_adjacent, rules.weight_adjacent_tch_tch)
                        adj_hard = np.where(st_filt, True, not rules.allow_adjacent_tch_tch_neighbors)

                    cost_adj_vec = (adj_w + np.where(adj_hard, HARD_PENALTY, 0.0)) * hw_filt

                    self.slot_pairs_rel.append({
                        "col_a": col_a,
                        "col_b": col_b,
                        "a_idx": a_filt,
                        "b_idx": b_filt,
                        "st": st_filt,
                        "hw": hw_filt,
                        "orig_idx": orig_indices,
                        "cost_cochan": cost_cochan_vec,
                        "cost_adj": cost_adj_vec,
                        "is_bcch_bcch": is_bcch_bcch,
                        "is_bcch_tch": is_bcch_tch
                    })

        # Precompute slot pairs for same station non-neighbors
        self.slot_pairs_same_st = []
        if len(raw_same_st_a) > 0:
            for col_a in range(1 + self.max_tch):
                for col_b in range(1 + self.max_tch):
                    valid = self.slot_mask[raw_same_st_a, col_a] & self.slot_mask[raw_same_st_b, col_b]
                    if not np.any(valid):
                        continue

                    sa_filt = raw_same_st_a[valid]
                    sb_filt = raw_same_st_b[valid]

                    is_bcch_bcch = (col_a == 0 and col_b == 0)
                    is_bcch_tch = ((col_a == 0 and col_b > 0) or (col_a > 0 and col_b == 0))

                    cost_cochan_val = rules.weight_same_station_cochannel + HARD_PENALTY
                    cost_adj_val = rules.weight_same_station_adjacent + HARD_PENALTY

                    self.slot_pairs_same_st.append({
                        "col_a": col_a,
                        "col_b": col_b,
                        "sa_idx": sa_filt,
                        "sb_idx": sb_filt,
                        "cost_cochan": cost_cochan_val,
                        "cost_adj": cost_adj_val,
                        "is_bcch_bcch": is_bcch_bcch,
                        "is_bcch_tch": is_bcch_tch
                    })

        # Internal cell slot pairs (col_i, col_j)
        self.internal_slot_pairs = []
        for i in range(1 + self.max_tch):
            for j in range(i + 1, 1 + self.max_tch):
                valid_cells = np.where(self.slot_mask[:, i] & self.slot_mask[:, j])[0]
                if len(valid_cells) > 0:
                    self.internal_slot_pairs.append({
                        "i": i,
                        "j": j,
                        "cells": valid_cells
                    })

    def quick_score_matrix(self, freq_matrix: np.ndarray) -> float:
        """
        Fastest evaluation method. Computes total score using pure vector operations.
        Returns float score in microseconds.
        """
        rules = self.rules
        total_score = 0.0

        # 1. Neighbor relations
        for sp in self.slot_pairs_rel:
            fa = freq_matrix[sp["a_idx"], sp["col_a"]]
            fb = freq_matrix[sp["b_idx"], sp["col_b"]]
            diff = np.abs(fa - fb)
            co = (diff == 0)
            ad = (diff == 1)
            if np.any(co):
                total_score += np.sum(co * sp["cost_cochan"])
            if np.any(ad):
                total_score += np.sum(ad * sp["cost_adj"])

        # 2. Same-station non-neighbors
        for sp in self.slot_pairs_same_st:
            fa = freq_matrix[sp["sa_idx"], sp["col_a"]]
            fb = freq_matrix[sp["sb_idx"], sp["col_b"]]
            diff = np.abs(fa - fb)
            n_co = np.count_nonzero(diff == 0)
            if n_co > 0:
                total_score += n_co * sp["cost_cochan"]
            n_ad = np.count_nonzero(diff == 1)
            if n_ad > 0:
                total_score += n_ad * sp["cost_adj"]

        # 3. Internal cell separation check
        # Under no circumstances can frequencies repeat within the same cell
        for sp in self.internal_slot_pairs:
            c_idxs = sp["cells"]
            fi = freq_matrix[c_idxs, sp["i"]]
            fj = freq_matrix[c_idxs, sp["j"]]
            diff = np.abs(fi - fj)

            n_same = np.count_nonzero(diff == 0)
            if n_same > 0:
                total_score += n_same * HARD_PENALTY

            n_close = np.count_nonzero((diff > 0) & (diff < rules.min_separation_same_cell))
            if n_close > 0:
                total_score += n_close * (rules.weight_same_station_adjacent * 2)

        # 4. Distance penalty for close pairs
        if rules.enable_distance_penalty and len(self.close_a) > 0:
            ca = self.close_a
            cb = self.close_b
            dp = self.close_dp

            cochan_count = np.zeros(len(ca), dtype=np.int32)
            for col_a in range(1 + self.max_tch):
                for col_b in range(1 + self.max_tch):
                    valid = self.slot_mask[ca, col_a] & self.slot_mask[cb, col_b]
                    if not np.any(valid):
                        continue
                    fa = freq_matrix[ca, col_a]
                    fb = freq_matrix[cb, col_b]
                    cochan_count += ((fa == fb) & valid).astype(np.int32)

            has_co = cochan_count > 0
            if np.any(has_co):
                total_score += np.sum(rules.weight_distance * dp[has_co] * cochan_count[has_co])

        return float(total_score)

    def evaluate_matrix(
        self,
        freq_matrix: np.ndarray,
        collect_violations: bool = False,
        plan_id: int = 0,
        run_id: int = 1
    ) -> tuple[float, Optional[PlanResult]]:
        """
        Evaluate fitness from the (N_cells, 1 + max_tch) frequency matrix.
        If collect_violations is False, calls fast vector scoring.
        If collect_violations is True, builds full PlanResult with detailed Violations.
        """
        if not collect_violations:
            score = self.quick_score_matrix(freq_matrix)
            return score, None

        rules = self.rules
        total_score = 0.0
        inherited_cost = 0.0
        new_assignment_cost = 0.0

        result = PlanResult(plan_id=plan_id, run_id=run_id)
        violations = []

        # ── 1. Check neighbor relations ───────────────────────────
        for sp in self.slot_pairs_rel:
            col_a = sp["col_a"]
            col_b = sp["col_b"]
            a_filt = sp["a_idx"]
            b_filt = sp["b_idx"]
            st_filt = sp["st"]
            hw_filt = sp["hw"]
            orig_indices = sp["orig_idx"]

            fa = freq_matrix[a_filt, col_a]
            fb = freq_matrix[b_filt, col_b]
            diff = np.abs(fa - fb)

            is_cochan = (diff == 0)
            is_adj = (diff == 1)

            type_a = "BCCH" if col_a == 0 else "TCH"
            type_b = "BCCH" if col_b == 0 else "TCH"
            is_bcch_bcch = sp["is_bcch_bcch"]
            is_bcch_tch = sp["is_bcch_tch"]

            for idx_k in np.where(is_cochan)[0]:
                c_val = float(sp["cost_cochan"][idx_k])
                total_score += c_val
                hw = float(hw_filt[idx_k])
                is_st = bool(st_filt[idx_k])
                orig_i = orig_indices[idx_k]

                if is_bcch_bcch:
                    result.cochannel_bcch_bcch += 1
                elif is_bcch_tch:
                    result.cochannel_bcch_tch += 1
                else:
                    result.cochannel_tch_tch += 1
                result.cochannel_ho_weighted += hw
                if is_st:
                    result.same_station_cochannel += 1

                ai = a_filt[idx_k]
                bi = b_filt[idx_k]
                is_a_new = ((ai, col_a) in self.recalculated_slots) or (int(fa[idx_k]) != int(self.base_freq_matrix[ai, col_a]))
                is_b_new = ((bi, col_b) in self.recalculated_slots) or (int(fb[idx_k]) != int(self.base_freq_matrix[bi, col_b]))
                is_inh = not (is_a_new or is_b_new)

                if is_inh:
                    inherited_cost += c_val
                else:
                    new_assignment_cost += c_val

                violations.append(Violation(
                    cell_a_id=self.cell_ids[ai],
                    cell_b_id=self.cell_ids[bi],
                    freq_a=int(fa[idx_k]),
                    freq_b=int(fb[idx_k]),
                    violation_type="cochannel",
                    channel_type_a=type_a,
                    channel_type_b=type_b,
                    ho_count=int(self.raw_rel_ho_count[orig_i]) if self.raw_rel_ho_count[orig_i] >= 0 else None,
                    distance_km=float(self.raw_rel_dist[orig_i]),
                    weighted_cost=c_val,
                    is_inherited=is_inh
                ))

            for idx_k in np.where(is_adj)[0]:
                c_val = float(sp["cost_adj"][idx_k])
                total_score += c_val
                hw = float(hw_filt[idx_k])
                is_st = bool(st_filt[idx_k])
                orig_i = orig_indices[idx_k]

                if is_bcch_bcch:
                    result.adjacent_bcch_bcch += 1
                elif is_bcch_tch:
                    result.adjacent_bcch_tch += 1
                else:
                    result.adjacent_tch_tch += 1
                result.adjacent_ho_weighted += hw
                if is_st:
                    result.same_station_adjacent += 1

                ai = a_filt[idx_k]
                bi = b_filt[idx_k]
                is_a_new = ((ai, col_a) in self.recalculated_slots) or (int(fa[idx_k]) != int(self.base_freq_matrix[ai, col_a]))
                is_b_new = ((bi, col_b) in self.recalculated_slots) or (int(fb[idx_k]) != int(self.base_freq_matrix[bi, col_b]))
                is_inh = not (is_a_new or is_b_new)

                if is_inh:
                    inherited_cost += c_val
                else:
                    new_assignment_cost += c_val

                violations.append(Violation(
                    cell_a_id=self.cell_ids[ai],
                    cell_b_id=self.cell_ids[bi],
                    freq_a=int(fa[idx_k]),
                    freq_b=int(fb[idx_k]),
                    violation_type="adjacent",
                    channel_type_a=type_a,
                    channel_type_b=type_b,
                    ho_count=int(self.raw_rel_ho_count[orig_i]) if self.raw_rel_ho_count[orig_i] >= 0 else None,
                    distance_km=float(self.raw_rel_dist[orig_i]),
                    weighted_cost=c_val,
                    is_inherited=is_inh
                ))

        # ── 2. Same-station non-neighbors ─────────────────────────
        for sp in self.slot_pairs_same_st:
            col_a = sp["col_a"]
            col_b = sp["col_b"]
            sa_filt = sp["sa_idx"]
            sb_filt = sp["sb_idx"]

            fa = freq_matrix[sa_filt, col_a]
            fb = freq_matrix[sb_filt, col_b]
            diff = np.abs(fa - fb)

            type_a = "BCCH" if col_a == 0 else "TCH"
            type_b = "BCCH" if col_b == 0 else "TCH"
            is_bcch_bcch = sp["is_bcch_bcch"]
            is_bcch_tch = sp["is_bcch_tch"]

            for idx_k in np.where(diff == 0)[0]:
                c_val = float(sp["cost_cochan"])
                total_score += c_val
                result.same_station_cochannel += 1
                if is_bcch_bcch:
                    result.cochannel_bcch_bcch += 1
                elif is_bcch_tch:
                    result.cochannel_bcch_tch += 1
                else:
                    result.cochannel_tch_tch += 1

                ai = sa_filt[idx_k]
                bi = sb_filt[idx_k]
                is_a_new = ((ai, col_a) in self.recalculated_slots) or (int(fa[idx_k]) != int(self.base_freq_matrix[ai, col_a]))
                is_b_new = ((bi, col_b) in self.recalculated_slots) or (int(fb[idx_k]) != int(self.base_freq_matrix[bi, col_b]))
                is_inh = not (is_a_new or is_b_new)

                if is_inh:
                    inherited_cost += c_val
                else:
                    new_assignment_cost += c_val

                violations.append(Violation(
                    cell_a_id=self.cell_ids[ai],
                    cell_b_id=self.cell_ids[bi],
                    freq_a=int(fa[idx_k]),
                    freq_b=int(fb[idx_k]),
                    violation_type="cochannel",
                    channel_type_a=type_a,
                    channel_type_b=type_b,
                    ho_count=None,
                    distance_km=0.0,
                    weighted_cost=c_val,
                    is_inherited=is_inh
                ))

            for idx_k in np.where(diff == 1)[0]:
                c_val = float(sp["cost_adj"])
                total_score += c_val
                result.same_station_adjacent += 1
                if is_bcch_bcch:
                    result.adjacent_bcch_bcch += 1
                elif is_bcch_tch:
                    result.adjacent_bcch_tch += 1
                else:
                    result.adjacent_tch_tch += 1

                ai = sa_filt[idx_k]
                bi = sb_filt[idx_k]
                is_a_new = ((ai, col_a) in self.recalculated_slots) or (int(fa[idx_k]) != int(self.base_freq_matrix[ai, col_a]))
                is_b_new = ((bi, col_b) in self.recalculated_slots) or (int(fb[idx_k]) != int(self.base_freq_matrix[bi, col_b]))
                is_inh = not (is_a_new or is_b_new)

                if is_inh:
                    inherited_cost += c_val
                else:
                    new_assignment_cost += c_val

                violations.append(Violation(
                    cell_a_id=self.cell_ids[ai],
                    cell_b_id=self.cell_ids[bi],
                    freq_a=int(fa[idx_k]),
                    freq_b=int(fb[idx_k]),
                    violation_type="adjacent",
                    channel_type_a=type_a,
                    channel_type_b=type_b,
                    ho_count=None,
                    distance_km=0.0,
                    weighted_cost=c_val,
                    is_inherited=is_inh
                ))

        # ── 3. Internal cell separation check ───────────────────────
        for sp in self.internal_slot_pairs:
            c_idxs = sp["cells"]
            fi = freq_matrix[c_idxs, sp["i"]]
            fj = freq_matrix[c_idxs, sp["j"]]
            diff = np.abs(fi - fj)

            t_i = "BCCH" if sp["i"] == 0 else "TCH"
            t_j = "BCCH" if sp["j"] == 0 else "TCH"

            for k in np.where(diff == 0)[0]:
                total_score += HARD_PENALTY
                c_idx = c_idxs[k]
                cid = self.cell_ids[c_idx]
                if "BCCH" in (t_i, t_j):
                    result.cochannel_bcch_tch += 1
                else:
                    result.cochannel_tch_tch += 1

                is_i_new = ((c_idx, sp["i"]) in self.recalculated_slots) or (int(fi[k]) != int(self.base_freq_matrix[c_idx, sp["i"]]))
                is_j_new = ((c_idx, sp["j"]) in self.recalculated_slots) or (int(fj[k]) != int(self.base_freq_matrix[c_idx, sp["j"]]))
                is_inh = not (is_i_new or is_j_new)

                if is_inh:
                    inherited_cost += HARD_PENALTY
                else:
                    new_assignment_cost += HARD_PENALTY

                violations.append(Violation(
                    cell_a_id=cid,
                    cell_b_id=cid,
                    freq_a=int(fi[k]),
                    freq_b=int(fj[k]),
                    violation_type="cochannel",
                    channel_type_a=t_i,
                    channel_type_b=t_j,
                    ho_count=None,
                    distance_km=0.0,
                    weighted_cost=HARD_PENALTY,
                    is_inherited=is_inh
                ))

            for k in np.where((diff > 0) & (diff < rules.min_separation_same_cell))[0]:
                penalty = rules.weight_same_station_adjacent * 2
                total_score += penalty
                c_idx = c_idxs[k]
                is_i_new = ((c_idx, sp["i"]) in self.recalculated_slots) or (int(fi[k]) != int(self.base_freq_matrix[c_idx, sp["i"]]))
                is_j_new = ((c_idx, sp["j"]) in self.recalculated_slots) or (int(fj[k]) != int(self.base_freq_matrix[c_idx, sp["j"]]))
                is_inh = not (is_i_new or is_j_new)
                if is_inh:
                    inherited_cost += penalty
                else:
                    new_assignment_cost += penalty

        # ── 4. Distance penalty for close pairs ─────────────────────
        if rules.enable_distance_penalty and len(self.close_a) > 0:
            ca = self.close_a
            cb = self.close_b
            dp = self.close_dp

            cochan_count = np.zeros(len(ca), dtype=np.int32)
            for col_a in range(1 + self.max_tch):
                for col_b in range(1 + self.max_tch):
                    valid = self.slot_mask[ca, col_a] & self.slot_mask[cb, col_b]
                    if not np.any(valid):
                        continue
                    fa = freq_matrix[ca, col_a]
                    fb = freq_matrix[cb, col_b]
                    cochan_count += ((fa == fb) & valid).astype(np.int32)

            has_co = cochan_count > 0
            if np.any(has_co):
                dist_penalties = rules.weight_distance * dp[has_co] * cochan_count[has_co]
                total_score += np.sum(dist_penalties)

                # Attribute distance penalty
                for idx_p, p_val in zip(np.where(has_co)[0], dist_penalties):
                    ca_idx = ca[idx_p]
                    cb_idx = cb[idx_p]
                    is_inh_p = True
                    for col_a in range(1 + self.max_tch):
                        for col_b in range(1 + self.max_tch):
                            if self.slot_mask[ca_idx, col_a] and self.slot_mask[cb_idx, col_b]:
                                if freq_matrix[ca_idx, col_a] == freq_matrix[cb_idx, col_b]:
                                    is_a_n = ((ca_idx, col_a) in self.recalculated_slots) or (int(freq_matrix[ca_idx, col_a]) != int(self.base_freq_matrix[ca_idx, col_a]))
                                    is_b_n = ((cb_idx, col_b) in self.recalculated_slots) or (int(freq_matrix[cb_idx, col_b]) != int(self.base_freq_matrix[cb_idx, col_b]))
                                    if is_a_n or is_b_n:
                                        is_inh_p = False
                                        break
                    if is_inh_p:
                        inherited_cost += float(p_val)
                    else:
                        new_assignment_cost += float(p_val)

        result.score = total_score
        result.inherited_cost = float(inherited_cost)
        result.new_assignment_cost = float(new_assignment_cost)
        result.violations = violations

        # Populate assignments
        result.assignments = []
        for idx, cid in enumerate(self.cell_ids):
            bcch = int(freq_matrix[idx, 0])
            tch_list = [int(freq_matrix[idx, 1 + s]) for s in range(self.max_tch) if self.slot_mask[idx, 1 + s]]
            result.assignments.append(FrequencyAssignment(cell_id=cid, bcch=bcch, tch_list=tch_list))

        return total_score, result

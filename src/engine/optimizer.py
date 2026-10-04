"""
Genetic algorithm optimizer for frequency planning.

Performs global optimization of frequency assignments across multiple cells
simultaneously, evaluating complete plans rather than assigning cell-by-cell.
"""
from __future__ import annotations
import random
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import FrequencyAssignment, PlanResult, OptimizationResultList
from src.engine.constraints import FrequencyRules
from src.engine.cost_function import evaluate_plan, quick_score, _get_freq_pairs


@dataclass
class _Variable:
    """A single frequency variable to optimize."""
    cell_id: str
    slot_type: str       # "BCCH" or "TCH"
    slot_index: int      # Index within TCH list (-1 for BCCH)


class FrequencyOptimizer:
    """
    Genetic Algorithm optimizer for 2G frequency planning.

    The optimizer encodes each candidate solution as a chromosome where
    each gene represents a frequency assignment for a single variable
    (a BCCH or TCH slot that needs recalculation).

    It evaluates each chromosome as a COMPLETE plan (global evaluation),
    not cell-by-cell, ensuring optimal co-channel/adjacent avoidance
    across the entire network.
    """

    def __init__(
        self,
        cells: dict[str, Cell],
        neighbor_graph: NeighborGraph,
        rules: FrequencyRules,
        progress_callback: Optional[Callable[[int, int, float], None]] = None
    ):
        self.cells = cells
        self.neighbor_graph = neighbor_graph
        self.rules = rules
        self.progress_callback = progress_callback
        self._cancelled = False

        # Build the list of variables to optimize
        self.variables: list[_Variable] = []
        self._build_variables()

        # Available frequency pool
        self.freq_pool = np.array(rules.available_frequencies, dtype=np.int32)
        if len(self.freq_pool) == 0:
            raise ValueError("No available frequencies in the configured range")

        # Pre-build fixed assignments (frequencies that are NOT changing)
        self.fixed_assignments: dict[str, FrequencyAssignment] = {}
        self._build_fixed_assignments()

    def cancel(self):
        """Cancel the optimization (called from another thread)."""
        self._cancelled = True

    def _build_variables(self):
        """Identify all frequency slots that need optimization."""
        self.variables = []
        for cell in self.cells.values():
            if not cell.needs_calculation:
                continue

            if cell.recalculate_bcch:
                self.variables.append(_Variable(
                    cell_id=cell.cell_id,
                    slot_type="BCCH",
                    slot_index=-1
                ))

            for idx in cell.tch_indices_to_recalculate:
                self.variables.append(_Variable(
                    cell_id=cell.cell_id,
                    slot_type="TCH",
                    slot_index=idx
                ))

            for i in range(cell.new_tch_count):
                self.variables.append(_Variable(
                    cell_id=cell.cell_id,
                    slot_type="TCH",
                    slot_index=len(cell.tch_list) + i
                ))

    def _build_fixed_assignments(self):
        """Build assignments for all cells with their fixed frequencies."""
        # No longer used for decoding, kept for compatibility
        pass

    def _chromosome_to_assignments(
        self, chromosome: np.ndarray
    ) -> dict[str, FrequencyAssignment]:
        """
        Decode a chromosome into a full set of FrequencyAssignments.

        Each gene in the chromosome maps to one variable (one frequency slot).
        The chromosome value is an index into the frequency pool.
        """
        # Step 1: Collect all optimized values by cell
        cell_vars: dict[str, dict[tuple[str, int], int]] = {}
        for i, var in enumerate(self.variables):
            freq_idx = int(chromosome[i]) % len(self.freq_pool)
            freq = int(self.freq_pool[freq_idx])
            if var.cell_id not in cell_vars:
                cell_vars[var.cell_id] = {}
            key = (var.slot_type, var.slot_index)
            cell_vars[var.cell_id][key] = freq

        # Step 2: Build full assignments for every cell
        assignments: dict[str, FrequencyAssignment] = {}
        for cid, cell in self.cells.items():
            vars_for_cell = cell_vars.get(cid, {})

            # BCCH: use optimized value if available, else keep original
            if ("BCCH", -1) in vars_for_cell:
                bcch = vars_for_cell[("BCCH", -1)]
            else:
                bcch = cell.bcch if cell.bcch is not None else 0

            # TCH: rebuild full list slot by slot
            tch_list = []
            for idx in range(len(cell.tch_list)):
                if idx in cell.tch_indices_to_recalculate:
                    # Use optimized value
                    key = ("TCH", idx)
                    if key in vars_for_cell:
                        tch_list.append(vars_for_cell[key])
                    else:
                        tch_list.append(cell.tch_list[idx])
                else:
                    # Keep original fixed value
                    tch_list.append(cell.tch_list[idx])

            # Append new TCH slots
            for i in range(cell.new_tch_count):
                key = ("TCH", len(cell.tch_list) + i)
                if key in vars_for_cell:
                    tch_list.append(vars_for_cell[key])

            assignments[cid] = FrequencyAssignment(
                cell_id=cid,
                bcch=bcch,
                tch_list=tch_list
            )

        return assignments

    def _check_hard_constraints(self, assignments: dict[str, FrequencyAssignment]) -> bool:
        """
        Quick check if a solution violates any hard constraints.
        Returns True if the solution is FEASIBLE (no hard violations).
        """
        rules = self.rules
        checked_pairs: set[tuple[str, str]] = set()

        for relation in self.neighbor_graph.all_relations():
            ca_id = relation.cell_a_id
            cb_id = relation.cell_b_id
            if ca_id not in assignments or cb_id not in assignments:
                continue

            pair_key = tuple(sorted((ca_id, cb_id)))
            checked_pairs.add(pair_key)

            a = assignments[ca_id]
            b = assignments[cb_id]
            cell_a = self.cells.get(ca_id)
            cell_b = self.cells.get(cb_id)
            same_station = (cell_a and cell_b and
                            cell_a.station.station_id == cell_b.station.station_id)

            for freq_a, type_a, freq_b, type_b in _get_freq_pairs(a, b):
                diff = abs(freq_a - freq_b)
                if diff > 1:
                    continue
                is_cochannel = (diff == 0)
                if rules.is_hard_constraint_violated(
                    same_station, True, is_cochannel, type_a, type_b
                ):
                    return False

        # Check all same-station pairs not in neighbor graph
        station_cells: dict[str, list[str]] = {}
        for cid, cell in self.cells.items():
            if cid in assignments:
                sid = cell.station.station_id
                station_cells.setdefault(sid, []).append(cid)

        for sid, cell_ids in station_cells.items():
            for i in range(len(cell_ids)):
                for j in range(i + 1, len(cell_ids)):
                    ca_id = cell_ids[i]
                    cb_id = cell_ids[j]
                    pair_key = tuple(sorted((ca_id, cb_id)))
                    if pair_key in checked_pairs:
                        continue
                    checked_pairs.add(pair_key)

                    a = assignments[ca_id]
                    b = assignments[cb_id]
                    for freq_a, type_a, freq_b, type_b in _get_freq_pairs(a, b):
                        diff = abs(freq_a - freq_b)
                        if diff > 1:
                            continue
                        is_cochannel = (diff == 0)
                        if rules.is_hard_constraint_violated(
                            True, False, is_cochannel, type_a, type_b
                        ):
                            return False

        # Check same-cell separation
        for cid, assign in assignments.items():
            all_freqs = [assign.bcch] + assign.tch_list
            for i in range(len(all_freqs)):
                for j in range(i + 1, len(all_freqs)):
                    if abs(all_freqs[i] - all_freqs[j]) < rules.min_separation_same_cell:
                        return False

        return True

    def _generate_smart_chromosome(self) -> np.ndarray:
        """
        Generate a chromosome that tries to respect basic constraints.
        Uses a greedy approach with randomization for the initial population.
        """
        n_vars = len(self.variables)
        chromosome = np.zeros(n_vars, dtype=np.int32)
        pool_size = len(self.freq_pool)

        # Track used frequencies per cell and per station
        cell_freqs: dict[str, set[int]] = {}
        station_freqs: dict[str, set[int]] = {}

        # Initialize with fixed frequencies
        for cid, cell in self.cells.items():
            cell_freqs[cid] = set()
            sid = cell.station.station_id
            if sid not in station_freqs:
                station_freqs[sid] = set()

            if cell.get_fixed_bcch() is not None:
                cell_freqs[cid].add(cell.get_fixed_bcch())
                station_freqs[sid].add(cell.get_fixed_bcch())
            for f in cell.get_fixed_tch():
                cell_freqs[cid].add(f)
                station_freqs[sid].add(f)

        # Collect neighbor frequencies
        def get_neighbor_freqs(cell_id: str) -> set[int]:
            nf = set()
            for nid in self.neighbor_graph.get_neighbor_cell_ids(cell_id):
                if nid in cell_freqs:
                    nf.update(cell_freqs[nid])
            return nf

        # Assign variables with preference for separation
        # Process BCCH variables first (they're more constrained)
        bcch_indices = [i for i, v in enumerate(self.variables) if v.slot_type == "BCCH"]
        tch_indices = [i for i, v in enumerate(self.variables) if v.slot_type == "TCH"]

        for idx in bcch_indices + tch_indices:
            var = self.variables[idx]
            cid = var.cell_id
            sid = self.cells[cid].station.station_id
            min_sep = self.rules.min_separation_same_cell

            neighbor_freqs = get_neighbor_freqs(cid)
            used = cell_freqs.get(cid, set()) | station_freqs.get(sid, set())

            # Find candidates that don't conflict
            candidates = []
            for fi in range(pool_size):
                f = int(self.freq_pool[fi])
                # Check same-cell/station separation
                too_close = False
                for uf in used:
                    if abs(f - uf) < min_sep:
                        too_close = True
                        break
                if too_close:
                    continue

                # For BCCH, also avoid neighbor frequencies
                if var.slot_type == "BCCH":
                    neighbor_conflict = False
                    for nf in neighbor_freqs:
                        if abs(f - nf) < self.rules.min_separation_bcch_neighbors:
                            neighbor_conflict = True
                            break
                    if neighbor_conflict:
                        continue

                candidates.append(fi)

            if candidates:
                chosen = random.choice(candidates)
            else:
                chosen = random.randint(0, pool_size - 1)

            chromosome[idx] = chosen
            freq = int(self.freq_pool[chosen])
            cell_freqs.setdefault(cid, set()).add(freq)
            station_freqs.setdefault(sid, set()).add(freq)

        return chromosome

    def _crossover(self, parent_a: np.ndarray, parent_b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Two-point crossover."""
        n = len(parent_a)
        if n < 2:
            return parent_a.copy(), parent_b.copy()

        pt1 = random.randint(0, n - 2)
        pt2 = random.randint(pt1 + 1, n - 1)

        child_a = parent_a.copy()
        child_b = parent_b.copy()

        child_a[pt1:pt2] = parent_b[pt1:pt2]
        child_b[pt1:pt2] = parent_a[pt1:pt2]

        return child_a, child_b

    def _mutate(self, chromosome: np.ndarray) -> np.ndarray:
        """Mutation: randomly change some genes."""
        result = chromosome.copy()
        pool_size = len(self.freq_pool)

        for i in range(len(result)):
            if random.random() < self.rules.mutation_rate:
                result[i] = random.randint(0, pool_size - 1)

        return result

    def _tournament_select(
        self,
        population: list[np.ndarray],
        scores: np.ndarray,
        tournament_size: int = 3
    ) -> np.ndarray:
        """Tournament selection: pick the best from a random subset."""
        indices = random.sample(range(len(population)), min(tournament_size, len(population)))
        best = min(indices, key=lambda i: scores[i])
        return population[best].copy()

    def optimize(self) -> list[PlanResult]:
        """
        Run the genetic algorithm optimization across rules.num_runs independent passes.

        Returns top-K global best plans found across all runs, with .per_run attribute
        containing the top-K results for each individual run.
        """
        self._cancelled = False
        n_vars = len(self.variables)

        if n_vars == 0:
            assignments = self._chromosome_to_assignments(np.array([], dtype=np.int32))
            result = evaluate_plan(
                assignments, self.cells, self.neighbor_graph, self.rules, plan_id=1, run_id=1
            )
            res_list = [result]
            res_list.per_run = {1: [result]}
            return res_list

        pop_size = self.rules.population_size
        n_gen = self.rules.num_generations
        elite_count = min(self.rules.elite_count, pop_size // 2)
        top_k = self.rules.top_k_results
        num_runs = max(1, self.rules.num_runs)

        per_run_results: dict[int, list[PlanResult]] = {}
        all_candidates: list[tuple[float, np.ndarray, int]] = []  # (score, chrom, run_id)

        def _get_signature(chrom: np.ndarray) -> tuple:
            assigns = self._chromosome_to_assignments(chrom)
            return tuple(
                (cid, assigns[cid].bcch, tuple(assigns[cid].tch_list))
                for cid in sorted(assigns.keys())
            )

        # ── Outer loop over independent runs ───────────────────────
        for run_idx in range(1, num_runs + 1):
            if self._cancelled:
                break

            # ── Initialize fresh population for this run ───────────
            population: list[np.ndarray] = []
            for _ in range(pop_size):
                chrom = self._generate_smart_chromosome()
                population.append(chrom)

            run_best_solutions: list[tuple[float, np.ndarray]] = []
            run_signatures: set[tuple] = set()

            best_score_ever = float('inf')

            for gen in range(n_gen):
                if self._cancelled:
                    break

                scores = np.zeros(pop_size)
                for i, chrom in enumerate(population):
                    assignments = self._chromosome_to_assignments(chrom)
                    scores[i] = quick_score(
                        assignments, self.cells, self.neighbor_graph, self.rules
                    )

                sorted_indices = np.argsort(scores)
                for idx in sorted_indices[:top_k * 3]:
                    score = float(scores[idx])
                    chrom = population[idx]
                    sig = _get_signature(chrom)

                    if sig not in run_signatures:
                        run_signatures.add(sig)
                        run_best_solutions.append((score, chrom.copy()))
                        run_best_solutions.sort(key=lambda x: x[0])
                        if len(run_best_solutions) > top_k * 5:
                            run_best_solutions = run_best_solutions[:top_k * 5]
                            run_signatures = {_get_signature(c) for _, c in run_best_solutions}

                gen_best = scores[sorted_indices[0]]
                if gen_best < best_score_ever:
                    best_score_ever = gen_best

                # Progress callback: (run_idx, total_runs, gen, total_gens, best_score)
                if self.progress_callback:
                    try:
                        self.progress_callback(run_idx, num_runs, gen + 1, n_gen, best_score_ever)
                    except TypeError:
                        self.progress_callback(gen + 1, n_gen, best_score_ever)

                # Next generation evolution
                new_population = []
                for idx in sorted_indices[:elite_count]:
                    new_population.append(population[idx].copy())

                if gen > 0 and gen % 50 == 0:
                    inject_count = max(1, pop_size // 10)
                    for _ in range(inject_count):
                        new_population.append(self._generate_smart_chromosome())

                while len(new_population) < pop_size:
                    if random.random() < self.rules.crossover_rate:
                        p1 = self._tournament_select(population, scores)
                        p2 = self._tournament_select(population, scores)
                        c1, c2 = self._crossover(p1, p2)
                        c1 = self._mutate(c1)
                        c2 = self._mutate(c2)
                        new_population.append(c1)
                        if len(new_population) < pop_size:
                            new_population.append(c2)
                    else:
                        p = self._tournament_select(population, scores)
                        p = self._mutate(p)
                        new_population.append(p)

                population = new_population

            # Build final evaluated PlanResult list for this run
            run_results: list[PlanResult] = []
            run_final_sigs: set[tuple] = set()

            for (score, chrom) in run_best_solutions:
                if len(run_results) >= top_k:
                    break
                sig = _get_signature(chrom)
                if sig in run_final_sigs:
                    continue
                run_final_sigs.add(sig)

                assignments = self._chromosome_to_assignments(chrom)
                res = evaluate_plan(
                    assignments, self.cells, self.neighbor_graph, self.rules,
                    plan_id=len(run_results) + 1, run_id=run_idx, collect_violations=True
                )
                run_results.append(res)
                all_candidates.append((score, chrom.copy(), run_idx))

            if not run_results and population:
                assignments = self._chromosome_to_assignments(population[0])
                res = evaluate_plan(
                    assignments, self.cells, self.neighbor_graph, self.rules,
                    plan_id=1, run_id=run_idx, collect_violations=True
                )
                run_results.append(res)
                all_candidates.append((res.score, population[0].copy(), run_idx))

            per_run_results[run_idx] = run_results

        # ── Global best aggregation across all runs ────────────────
        all_candidates.sort(key=lambda x: x[0])
        global_results: list[PlanResult] = []
        global_sigs: set[tuple] = set()

        for score, chrom, r_id in all_candidates:
            if len(global_results) >= top_k:
                break
            sig = _get_signature(chrom)
            if sig in global_sigs:
                continue
            global_sigs.add(sig)

            assignments = self._chromosome_to_assignments(chrom)
            res = evaluate_plan(
                assignments, self.cells, self.neighbor_graph, self.rules,
                plan_id=len(global_results) + 1, run_id=r_id, collect_violations=True
            )
            global_results.append(res)

        if not global_results:
            global_results = per_run_results.get(1, [])

        return OptimizationResultList(global_results, per_run=per_run_results)



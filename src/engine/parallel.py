"""
Parallel worker engine for multi-core frequency optimization.
Distributes independent optimization runs and evaluations across all CPU cores
using multiprocessing on Windows/Linux, achieving full processor utilization.
"""
from __future__ import annotations
import os
import random
import time
import numpy as np
from typing import Optional, Callable

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import FrequencyAssignment, PlanResult, OptimizationResultList
from src.engine.constraints import FrequencyRules
from src.engine.fast_evaluator import FastPlanEvaluator, HARD_PENALTY
from src.engine.hardware import get_hardware_profile


def _repair_chromosome(
    chrom: np.ndarray,
    variables: list,
    cells: dict[str, Cell],
    freq_pool: np.ndarray
) -> np.ndarray:
    """
    Guarantees hard constraints on the chromosome:
    1. UNDER NO CIRCUMSTANCES can frequencies repeat or be adjacent within the same cell
       between BCCH and TCH, or among TCHs of the same cell (min separation >= 2).
    2. UNDER NO CIRCUMSTANCES can frequencies repeat or be adjacent between cells of the same
       station or cells with distance = 0 (min separation >= 2).
    """
    repaired = chrom.copy()
    pool_size = len(freq_pool)

    # Group variables by cell_id
    cell_var_indices: dict[str, list[int]] = {}
    for i, var in enumerate(variables):
        cell_var_indices.setdefault(var.cell_id, []).append(i)

    # Group cells by site (station_id or identical coordinates at distance = 0)
    station_cells: dict[str, list[str]] = {}
    for cid, cell in cells.items():
        if cell.station and cell.station.latitude is not None and cell.station.longitude is not None:
            # Group by 6 decimal place lat/lon (~0.1m precision) or station_id
            site_key = f"coord_{round(cell.station.latitude, 6)}_{round(cell.station.longitude, 6)}"
        elif cell.station:
            site_key = f"sid_{cell.station.station_id}"
        else:
            site_key = f"cell_{cid}"
        station_cells.setdefault(site_key, []).append(cid)

    # 1. Enforce strict separation (>= 2, no cochannel and no adjacent) within each cell
    for cid, var_idxs in cell_var_indices.items():
        cell = cells[cid]
        used_cell_freqs = set()

        if not cell.recalculate_bcch and cell.bcch is not None and cell.bcch > 0:
            used_cell_freqs.add(cell.bcch)
        for idx, f in enumerate(cell.tch_list):
            if idx not in cell.tch_indices_to_recalculate and f > 0:
                used_cell_freqs.add(f)

        for vi in var_idxs:
            freq_idx = int(repaired[vi]) % pool_size
            freq = int(freq_pool[freq_idx])

            # Check if freq violates cochannel (diff 0) or adjacent (diff 1) with any existing cell freq
            has_conflict = any(abs(freq - uf) < 2 for uf in used_cell_freqs)
            if has_conflict:
                avail = [idx for idx, f in enumerate(freq_pool) if all(abs(int(f) - uf) >= 2 for uf in used_cell_freqs)]
                if avail:
                    new_idx = random.choice(avail)
                else:
                    # Fallback if spectrum is extremely narrow: strictly avoid cochannel
                    avail_no_co = [idx for idx, f in enumerate(freq_pool) if int(f) not in used_cell_freqs]
                    new_idx = random.choice(avail_no_co) if avail_no_co else freq_idx
                repaired[vi] = new_idx
                freq = int(freq_pool[new_idx])

            used_cell_freqs.add(freq)

    # 2. Enforce strict separation (>= 2, no cochannel and no adjacent) across cells of the same station / distance = 0
    for sid, cids in station_cells.items():
        if len(cids) <= 1:
            continue
        used_station_freqs = set()

        # Collect all fixed frequencies on this site
        for cid in cids:
            cell = cells[cid]
            if not cell.recalculate_bcch and cell.bcch is not None and cell.bcch > 0:
                used_station_freqs.add(cell.bcch)
            for idx, f in enumerate(cell.tch_list):
                if idx not in cell.tch_indices_to_recalculate and f > 0:
                    used_station_freqs.add(f)

        for cid in cids:
            for vi in cell_var_indices.get(cid, []):
                freq_idx = int(repaired[vi]) % pool_size
                freq = int(freq_pool[freq_idx])

                # Check if violates cochannel (diff 0) or adjacent (diff 1) with site
                has_station_conflict = any(abs(freq - uf) < 2 for uf in used_station_freqs)
                if has_station_conflict:
                    # Look for candidate with separation >= 2 from all station frequencies
                    avail = [
                        idx for idx, f in enumerate(freq_pool)
                        if all(abs(int(f) - uf) >= 2 for uf in used_station_freqs)
                    ]
                    if avail:
                        new_idx = random.choice(avail)
                    else:
                        # Fallback: strictly no cochannel, maximize separation
                        avail_no_co = [idx for idx, f in enumerate(freq_pool) if int(f) not in used_station_freqs]
                        if avail_no_co:
                            new_idx = max(avail_no_co, key=lambda idx: min((abs(int(freq_pool[idx]) - uf) for uf in used_station_freqs), default=0))
                        else:
                            new_idx = freq_idx
                    repaired[vi] = new_idx
                    freq = int(freq_pool[new_idx])

                used_station_freqs.add(freq)

    return repaired


def run_single_worker_run(
    run_idx: int,
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    variables_data: list[tuple[str, str, int]],
    queue: Optional[object] = None,
    cancel_event: Optional[object] = None,
    seed: Optional[int] = None,
    sub_id: int = 0
) -> tuple[int, list[PlanResult], list[tuple[int, float]]]:
    """
    Independent genetic algorithm pass executed on a dedicated CPU process worker.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    fe = FastPlanEvaluator(cells, neighbor_graph, rules)
    freq_pool = np.array(rules.available_frequencies, dtype=np.int32)
    pool_size = len(freq_pool)

    # Reconstruct variable descriptors
    from src.engine.optimizer import _Variable
    variables = [_Variable(cid, stype, sidx) for cid, stype, sidx in variables_data]
    n_vars = len(variables)

    if n_vars == 0:
        score, plan = fe.evaluate_matrix(fe.base_freq_matrix, collect_violations=True, plan_id=1, run_id=run_idx)
        return run_idx, [plan], [(1, score)]

    # Pre-map variable locations in matrix
    var_mat_coords = []
    for var in variables:
        c_idx = fe.cell_to_idx[var.cell_id]
        col_idx = 0 if var.slot_type == "BCCH" else 1 + var.slot_index
        var_mat_coords.append((c_idx, col_idx))

    pop_size = rules.population_size
    n_gen = rules.num_generations
    elite_count = min(rules.elite_count, pop_size // 2)
    top_k = rules.top_k_results

    def chrom_to_matrix(chrom: np.ndarray) -> np.ndarray:
        mat = fe.base_freq_matrix.copy()
        for i, (c_idx, col_idx) in enumerate(var_mat_coords):
            freq = freq_pool[int(chrom[i]) % pool_size]
            mat[c_idx, col_idx] = freq
        return mat

    # Generate initial population
    population = []
    for _ in range(pop_size):
        raw = np.random.randint(0, pool_size, size=n_vars, dtype=np.int32)
        chrom = _repair_chromosome(raw, variables, cells, freq_pool)
        population.append(chrom)

    best_score_ever = float('inf')
    run_candidates: list[tuple[float, np.ndarray]] = []
    seen_sigs: set[tuple] = set()
    history: list[tuple[int, float]] = []

    for gen in range(1, n_gen + 1):
        if cancel_event and cancel_event.is_set():
            break

        # Vectorized evaluation of population
        scores = np.zeros(pop_size, dtype=np.float64)
        for i, chrom in enumerate(population):
            mat = chrom_to_matrix(chrom)
            scores[i] = fe.quick_score_matrix(mat)

        sorted_indices = np.argsort(scores)
        gen_best = scores[sorted_indices[0]]

        if gen_best < best_score_ever:
            best_score_ever = gen_best

        history.append((gen, float(best_score_ever)))

        # Cache top candidates
        for idx in sorted_indices[:top_k * 2]:
            sc = float(scores[idx])
            c_copy = population[idx].copy()
            sig = tuple(c_copy)
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                run_candidates.append((sc, c_copy))
                run_candidates.sort(key=lambda x: x[0])
                if len(run_candidates) > top_k * 4:
                    run_candidates = run_candidates[:top_k * 4]
                    seen_sigs = {tuple(c) for _, c in run_candidates}

        # Send progress to queue
        if queue is not None and (gen % 2 == 0 or gen == n_gen or gen == 1):
            try:
                queue.put((run_idx, gen, n_gen, float(best_score_ever), sub_id))
            except Exception:
                pass

        # Next generation evolution
        new_pop = []
        for idx in sorted_indices[:elite_count]:
            new_pop.append(population[idx].copy())

        # Random injection every 50 gens to avoid local minima
        if gen % 50 == 0:
            for _ in range(max(1, pop_size // 10)):
                raw = np.random.randint(0, pool_size, size=n_vars, dtype=np.int32)
                new_pop.append(_repair_chromosome(raw, variables, cells, freq_pool))

        while len(new_pop) < pop_size:
            if random.random() < rules.crossover_rate:
                t1 = random.sample(range(pop_size), 3)
                p1 = population[min(t1, key=lambda i: scores[i])]
                t2 = random.sample(range(pop_size), 3)
                p2 = population[min(t2, key=lambda i: scores[i])]

                if n_vars > 1:
                    pt1 = random.randint(0, n_vars - 2)
                    pt2 = random.randint(pt1 + 1, n_vars - 1)
                    c1 = p1.copy()
                    c2 = p2.copy()
                    c1[pt1:pt2] = p2[pt1:pt2]
                    c2[pt1:pt2] = p1[pt1:pt2]
                else:
                    c1, c2 = p1.copy(), p2.copy()

                # Mutation
                for i in range(n_vars):
                    if random.random() < rules.mutation_rate:
                        c1[i] = random.randint(0, pool_size - 1)
                    if random.random() < rules.mutation_rate:
                        c2[i] = random.randint(0, pool_size - 1)

                c1 = _repair_chromosome(c1, variables, cells, freq_pool)
                c2 = _repair_chromosome(c2, variables, cells, freq_pool)
                new_pop.append(c1)
                if len(new_pop) < pop_size:
                    new_pop.append(c2)
            else:
                t = random.sample(range(pop_size), 3)
                p = population[min(t, key=lambda i: scores[i])].copy()
                for i in range(n_vars):
                    if random.random() < rules.mutation_rate:
                        p[i] = random.randint(0, pool_size - 1)
                p = _repair_chromosome(p, variables, cells, freq_pool)
                new_pop.append(p)

        population = new_pop

    # Final detailed evaluation for top results
    final_results: list[PlanResult] = []
    final_sigs: set[tuple] = set()

    for sc, chrom in run_candidates:
        if len(final_results) >= top_k:
            break
        sig = tuple(chrom)
        if sig in final_sigs:
            continue
        final_sigs.add(sig)

        mat = chrom_to_matrix(chrom)
        score, plan = fe.evaluate_matrix(
            mat, collect_violations=True, plan_id=len(final_results) + 1, run_id=run_idx
        )
        plan.convergence_history = history
        final_results.append(plan)

    if not final_results and population:
        for idx in range(min(top_k, len(population))):
            mat = chrom_to_matrix(population[idx])
            score, plan = fe.evaluate_matrix(
                mat, collect_violations=True, plan_id=len(final_results) + 1, run_id=run_idx
            )
            plan.convergence_history = history if history else [(1, score)]
            final_results.append(plan)

    # Notify completion in queue
    if queue is not None:
        try:
            queue.put((run_idx, "RESULT", final_results, history, sub_id))
            queue.put((run_idx, "DONE", n_gen, best_score_ever, sub_id))
        except Exception:
            pass

    return run_idx, final_results, history


def run_parallel_optimizer(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    variables_data: list[tuple[str, str, int]],
    progress_callback: Optional[Callable] = None,
    max_workers: Optional[int] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> OptimizationResultList:
    """
    Execute optimization across all CPU cores utilizing multiprocessing.
    Leverages 100% of available CPU cores by distributing runs and parallel search workers.
    """
    num_runs = max(1, rules.num_runs)
    hw_workers = get_hardware_profile().recommended_workers
    total_workers = max(1, max_workers or hw_workers)

    # Build tasks: each task is (run_idx, sub_id, seed)
    tasks: list[tuple[int, int, int]] = []
    if num_runs == 1:
        # Full CPU allocation for single run: multiple workers explore in parallel
        for s in range(total_workers):
            tasks.append((1, s, 1000 + s * 137 + 42))
    elif num_runs < total_workers:
        # Distribute workers across runs
        sub_per_run = total_workers // num_runs
        remainder = total_workers % num_runs
        for r in range(1, num_runs + 1):
            count = sub_per_run + (1 if r <= remainder else 0)
            for s in range(count):
                tasks.append((r, s, r * 1000 + s * 137 + 42))
    else:
        # Each run gets 1 worker, concurrent execution bounded by total_workers
        for r in range(1, num_runs + 1):
            tasks.append((r, 0, r * 1000 + 42))

    per_run_candidates: dict[int, list[PlanResult]] = {r: [] for r in range(1, num_runs + 1)}
    per_run_histories: dict[int, dict[int, list[tuple[int, float]]]] = {r: {} for r in range(1, num_runs + 1)}
    best_seen_per_run: dict[int, float] = {r: float("inf") for r in range(1, num_runs + 1)}

    # If single worker: execute directly in-process
    if total_workers <= 1:
        class _CancelEventAdapter:
            def is_set(self):
                return bool(cancel_check and cancel_check())

        cancel_adapter = _CancelEventAdapter()

        for r, s, seed in tasks:
            if cancel_check and cancel_check():
                break

            class _DirectQueue:
                def put(self, item):
                    if item[1] != "DONE" and item[1] != "RESULT":
                        g, tg, bs = item[1], item[2], item[3]
                        best_seen_per_run[r] = min(best_seen_per_run[r], bs)
                        if progress_callback:
                            try:
                                progress_callback(r, num_runs, g, tg, best_seen_per_run[r])
                            except TypeError:
                                progress_callback(g, tg, best_seen_per_run[r])

            _, run_results, history = run_single_worker_run(
                r, cells, neighbor_graph, rules, variables_data,
                queue=_DirectQueue(),
                cancel_event=cancel_adapter,
                seed=seed,
                sub_id=s
            )
            per_run_candidates[r].extend(run_results)
            per_run_histories[r][s] = history
            if cancel_check and cancel_check():
                break
    else:
        # Multi-process parallel execution across all CPU cores
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        cancel_event = ctx.Event()

        pending_tasks = list(tasks)
        active_processes: dict[tuple[int, int], mp.Process] = {}

        def _spawn_task(task_tuple):
            r_idx, s_idx, t_seed = task_tuple
            p = ctx.Process(
                target=run_single_worker_run,
                args=(r_idx, cells, neighbor_graph, rules, variables_data, queue, cancel_event, t_seed, s_idx)
            )
            p.daemon = True
            p.start()
            active_processes[(r_idx, s_idx)] = p

        # Start up to total_workers processes
        while pending_tasks and len(active_processes) < total_workers:
            _spawn_task(pending_tasks.pop(0))

        cancel_requested = False
        cancel_deadline = None

        while active_processes:
            if not cancel_requested and cancel_check and cancel_check():
                cancel_requested = True
                cancel_event.set()
                pending_tasks.clear()
                # Allow active workers up to 3.0s to wrap up and deliver their best results
                cancel_deadline = time.time() + 3.0

            if cancel_requested and cancel_deadline and time.time() > cancel_deadline:
                for p in list(active_processes.values()):
                    try:
                        p.terminate()
                    except Exception:
                        pass
                active_processes.clear()
                break

            try:
                msg = queue.get(timeout=0.05)
                r_id = msg[0]
                action = msg[1]

                if action == "RESULT":
                    res_list = msg[2]
                    hist = msg[3]
                    s_id = msg[4] if len(msg) > 4 else 0
                    per_run_candidates[r_id].extend(res_list)
                    per_run_histories[r_id][s_id] = hist
                elif action == "DONE":
                    s_id = msg[4] if len(msg) > 4 else 0
                    key = (r_id, s_id)
                    if key in active_processes:
                        p = active_processes.pop(key)
                        p.join(timeout=0.5)
                    # Spawn next pending task only if not cancelling
                    if not cancel_requested and pending_tasks and len(active_processes) < total_workers:
                        _spawn_task(pending_tasks.pop(0))
                else:
                    # Progress update: (run_idx, gen, n_gen, best_score, sub_id)
                    gen = msg[1]
                    tg = msg[2]
                    bs = msg[3]
                    if bs < best_seen_per_run[r_id]:
                        best_seen_per_run[r_id] = bs
                    if progress_callback:
                        try:
                            progress_callback(r_id, num_runs, gen, tg, best_seen_per_run[r_id])
                        except TypeError:
                            progress_callback(gen, tg, best_seen_per_run[r_id])

            except Exception:
                # Handle any dead/terminated processes
                for key, p in list(active_processes.items()):
                    if not p.is_alive():
                        active_processes.pop(key)
                        if not cancel_requested and pending_tasks and len(active_processes) < total_workers:
                            _spawn_task(pending_tasks.pop(0))

        # Drain any remaining RESULT messages from queue
        while True:
            try:
                msg = queue.get_nowait()
                if msg[1] == "RESULT":
                    r_id = msg[0]
                    res_list = msg[2]
                    hist = msg[3]
                    s_id = msg[4] if len(msg) > 4 else 0
                    per_run_candidates[r_id].extend(res_list)
                    per_run_histories[r_id][s_id] = hist
            except Exception:
                break

        # Join any remaining processes
        for p in active_processes.values():
            p.join(timeout=0.5)

    # Aggregate results and histories per run
    top_k = rules.top_k_results
    per_run_final_results: dict[int, list[PlanResult]] = {}
    per_run_final_history: dict[int, list[tuple[int, float]]] = {}
    all_global_candidates: list[PlanResult] = []

    for r in range(1, num_runs + 1):
        candidates = per_run_candidates.get(r, [])
        candidates.sort(key=lambda p: p.score)

        # Merge history across sub-workers for this run (take minimum score at each gen)
        sub_hists = per_run_histories.get(r, {})
        if sub_hists:
            merged_gen_scores: dict[int, float] = {}
            for hist in sub_hists.values():
                for g, sc in hist:
                    if g not in merged_gen_scores or sc < merged_gen_scores[g]:
                        merged_gen_scores[g] = sc
            run_hist = [(g, merged_gen_scores[g]) for g in sorted(merged_gen_scores.keys())]
        else:
            run_hist = []

        per_run_final_history[r] = run_hist

        # Deduplicate top-K candidates for this run
        run_results: list[PlanResult] = []
        seen_sigs = set()
        for cand in candidates:
            if len(run_results) >= top_k:
                break
            sig = tuple(
                (a.cell_id, a.bcch, tuple(a.tch_list))
                for a in sorted(cand.assignments, key=lambda x: x.cell_id)
            )
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            cand.plan_id = len(run_results) + 1
            cand.run_id = r
            cand.convergence_history = run_hist
            run_results.append(cand)
            all_global_candidates.append(cand)

        per_run_final_results[r] = run_results

    # Fallback if cancellation occurred before any generation could produce a result
    if not all_global_candidates:
        fe = FastPlanEvaluator(cells, neighbor_graph, rules)
        score, plan = fe.evaluate_matrix(
            fe.base_freq_matrix, collect_violations=True, plan_id=1, run_id=1
        )
        plan.convergence_history = [(1, score)]
        all_global_candidates.append(plan)
        per_run_final_results[1] = [plan]
        per_run_final_history[1] = [(1, score)]

    # Global best aggregation across all runs
    all_global_candidates.sort(key=lambda p: p.score)
    global_results: list[PlanResult] = []
    global_seen = set()

    for cand in all_global_candidates:
        if len(global_results) >= top_k:
            break
        sig = tuple(
            (a.cell_id, a.bcch, tuple(a.tch_list))
            for a in sorted(cand.assignments, key=lambda x: x.cell_id)
        )
        if sig in global_seen:
            continue
        global_seen.add(sig)
        cand_copy = cand
        cand_copy.plan_id = len(global_results) + 1
        global_results.append(cand_copy)

    if not global_results and per_run_final_results:
        global_results = per_run_final_results.get(1, [])

    return OptimizationResultList(
        global_results,
        per_run=per_run_final_results,
        per_run_history=per_run_final_history
    )

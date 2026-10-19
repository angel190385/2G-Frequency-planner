"""
Cluster and Zone-based sequential optimizer with frontier protection.

Executes sequential optimization of geographic zones (polygons):
1. Determines active cells for the current zone.
2. Identifies border / frontier cells from the rest of the network within
   distance_threshold_km of any active cell.
3. Treats border cells as FIXED constraints so the active zone's frequencies
   do not cause co-channel or adjacent interference with the surrounding network
   or previously optimized zones.
4. Updates the global network with the optimized frequencies and proceeds to
   the next zone in order.
"""
from __future__ import annotations
import copy
from typing import Optional, Callable
from dataclasses import dataclass

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph, haversine_distance
from src.models.zone import OptimizationZone
from src.models.frequency_plan import PlanResult, OptimizationResultList
from src.engine.optimizer import FrequencyOptimizer

from PyQt6.QtCore import QThread, pyqtSignal


@dataclass
class ZoneOptimizationProgress:
    zone_index: int
    total_zones: int
    zone_name: str
    current_run: int
    total_runs: int
    current_gen: int
    total_gens: int
    best_score: float
    global_run: int = 1
    total_global_runs: int = 1


def find_frontier_cells(
    active_cell_ids: set[str],
    all_cells: dict[str, Cell],
    threshold_km: float = 8.0,
    neighbor_graph: Optional[NeighborGraph] = None
) -> set[str]:
    """
    Find cells in all_cells outside active_cell_ids that are within threshold_km
    of any cell in active_cell_ids, or connected via the neighbor graph.
    These form the protective buffer / frontier.
    """
    frontier_ids = set()
    active_stations = set()

    for cid in active_cell_ids:
        c = all_cells.get(cid)
        if c and c.station:
            active_stations.add(c.station)

    # 1. Geographic distance check
    for cid, cell in all_cells.items():
        if cid in active_cell_ids:
            continue
        if not cell.station:
            continue

        lat = cell.station.latitude
        lon = cell.station.longitude
        if lat == 0.0 and lon == 0.0:
            continue

        # Check distance to any active station
        for ast in active_stations:
            dist = haversine_distance(lat, lon, ast.latitude, ast.longitude)
            if dist <= threshold_km:
                frontier_ids.add(cid)
                break

    # 2. Add any direct neighbors if graph provided
    if neighbor_graph:
        for cid in active_cell_ids:
            for n_id in neighbor_graph.get_neighbor_cell_ids(cid):
                if n_id not in active_cell_ids and n_id in all_cells:
                    frontier_ids.add(n_id)

    return frontier_ids


class ClusterOptimizer:
    """
    Sequential zone optimizer managing multi-polygon network planning.
    """

    def __init__(
        self,
        cells: dict[str, Cell],
        zones: list[OptimizationZone],
        global_neighbor_graph: Optional[NeighborGraph] = None,
        progress_callback: Optional[Callable[[ZoneOptimizationProgress], None]] = None,
        frontier_distance_km: float = 8.0,
        max_workers: Optional[int] = None,
        respect_cell_flags: bool = False
    ):
        self.cells = copy.deepcopy(cells)
        # Sort zones by execution_order
        self.zones = sorted(zones, key=lambda z: z.execution_order)
        self.global_neighbor_graph = global_neighbor_graph
        self.progress_callback = progress_callback
        self.frontier_distance_km = frontier_distance_km
        self.max_workers = max_workers
        self.respect_cell_flags = respect_cell_flags
        self._cancelled = False
        self._current_optimizer: Optional[FrequencyOptimizer] = None

    def cancel(self):
        """Cancel the cluster optimization."""
        self._cancelled = True
        if self._current_optimizer:
            self._current_optimizer.cancel()

    def run(self) -> dict[str, PlanResult]:
        """
        Execute optimization zone by zone in sequence.
        Returns:
            dict[zone_id, PlanResult]
        """
        self._cancelled = False
        zone_results: dict[str, PlanResult] = {}
        total_zones = len(self.zones)

        for z_idx, zone in enumerate(self.zones, start=1):
            if self._cancelled:
                break

            active_cell_ids = zone.cell_ids
            if not active_cell_ids:
                zone.status = "Omitida (Sin celdas)"
                continue

            zone.status = "Optimizando"

            # 1. Prepare cells for this zone
            # Active cells have recalculation flags enabled
            # Frontier cells are included with needs_calculation = False
            frontier_ids = find_frontier_cells(
                active_cell_ids,
                self.cells,
                threshold_km=getattr(zone.rules, "distance_threshold_km", self.frontier_distance_km),
                neighbor_graph=zone.neighbor_graph or self.global_neighbor_graph
            )

            cells_to_eval: dict[str, Cell] = {}

            # Add active cells with zone recalculation config
            for cid in active_cell_ids:
                if cid not in self.cells:
                    continue
                c = copy.deepcopy(self.cells[cid])
                if not self.respect_cell_flags:
                    allowed_pool = set(zone.rules.available_frequencies) if hasattr(zone, "rules") and zone.rules else set()
                    if zone.recalculate_bcch:
                        c.recalculate_bcch = True
                    else:
                        c.recalculate_bcch = bool(c.bcch is None or c.bcch <= 0 or (allowed_pool and c.bcch not in allowed_pool))

                    if zone.recalculate_tch:
                        c.tch_indices_to_recalculate = list(range(len(c.tch_list)))
                    else:
                        if allowed_pool:
                            c.tch_indices_to_recalculate = [
                                i for i, f in enumerate(c.tch_list)
                                if f is None or f <= 0 or f not in allowed_pool
                            ]
                        else:
                            c.tch_indices_to_recalculate = []
                    c.new_tch_count = zone.new_tch_count
                cells_to_eval[cid] = c

            if self.respect_cell_flags:
                if not any(cells_to_eval[cid].needs_calculation for cid in active_cell_ids if cid in cells_to_eval):
                    zone.status = "Omitida (Sin celdas a recalcular)"
                    continue

            # Add frontier cells as strictly FIXED
            for fid in frontier_ids:
                if fid in self.cells:
                    c = copy.deepcopy(self.cells[fid])
                    c.recalculate_bcch = False
                    c.tch_indices_to_recalculate = []
                    c.new_tch_count = 0
                    cells_to_eval[fid] = c

            # Build neighbor graph: zone specific, or fallback to global filtered
            ng = zone.neighbor_graph
            if not ng:
                if self.global_neighbor_graph:
                    ng = self.global_neighbor_graph.filter_by_cells(set(cells_to_eval.keys()))
                else:
                    ng = NeighborGraph()

            # Define progress relay
            def on_progress(*args):
                if len(args) == 5:
                    run_idx, total_runs, gen, total_gens, best_score = args
                elif len(args) == 3:
                    gen, total_gens, best_score = args
                    run_idx = 1
                    total_runs = zone.rules.num_runs
                else:
                    return

                if self.progress_callback:
                    p = ZoneOptimizationProgress(
                        zone_index=z_idx,
                        total_zones=total_zones,
                        zone_name=zone.name,
                        current_run=run_idx,
                        total_runs=total_runs,
                        current_gen=gen,
                        total_gens=total_gens,
                        best_score=best_score
                    )
                    self.progress_callback(p)

            # 2. Run FrequencyOptimizer for this zone
            self._current_optimizer = FrequencyOptimizer(
                cells=cells_to_eval,
                neighbor_graph=ng,
                rules=zone.rules,
                progress_callback=on_progress,
                max_workers=self.max_workers
            )

            res_list = self._current_optimizer.optimize()
            if self._cancelled:
                zone.status = "Cancelada"
                break

            best_plan = res_list.best_plan
            zone_results[zone.zone_id] = res_list
            zone.status = "Completada"

            # 3. Consolidate results: update global cells with the new frequencies
            for assign in best_plan.assignments:
                if assign.cell_id in active_cell_ids:
                    global_cell = self.cells[assign.cell_id]
                    if assign.bcch is not None and assign.bcch > 0:
                        global_cell.bcch = assign.bcch
                    global_cell.tch_list = list(assign.tch_list)

        return zone_results


class ClusterOptimizerThread(QThread):
    """Background worker thread for running sequential cluster/zone optimization."""
    progress = pyqtSignal(object)              # ZoneOptimizationProgress
    zone_completed = pyqtSignal(str, float, int)  # zone_name, score, cell_count
    finished = pyqtSignal(dict, dict)          # dict[zone_id, PlanResult], dict[cell_id, Cell] (updated)
    error = pyqtSignal(str)

    def __init__(
        self,
        cells: dict[str, Cell],
        zones: list[OptimizationZone],
        global_neighbor_graph: Optional[NeighborGraph] = None,
        frontier_distance_km: float = 8.0,
        max_workers: Optional[int] = None,
        respect_cell_flags: bool = False
    ):
        super().__init__()
        self.cells = cells
        self.zones = zones
        self.global_neighbor_graph = global_neighbor_graph
        self.frontier_distance_km = frontier_distance_km
        self.max_workers = max_workers
        self.respect_cell_flags = respect_cell_flags
        self.optimizer: Optional[ClusterOptimizer] = None

    def run(self):
        try:
            def _progress_cb(p: ZoneOptimizationProgress):
                self.progress.emit(p)

            self.optimizer = ClusterOptimizer(
                cells=self.cells,
                zones=self.zones,
                global_neighbor_graph=self.global_neighbor_graph,
                progress_callback=_progress_cb,
                frontier_distance_km=self.frontier_distance_km,
                max_workers=self.max_workers,
                respect_cell_flags=self.respect_cell_flags
            )

            # We can also track when each zone completes
            zone_results = {}
            total_zones = len(self.zones)
            sorted_zones = sorted(self.zones, key=lambda z: z.execution_order)

            # Determine valid zones that actually have active calculation variables
            if self.respect_cell_flags:
                valid_zones = [
                    z for z in sorted_zones
                    if any(self.optimizer.cells.get(cid) and self.optimizer.cells[cid].needs_calculation for cid in z.cell_ids)
                ]
            else:
                valid_zones = [z for z in sorted_zones if z.cell_ids]

            total_global_runs = sum(getattr(z.rules, "num_runs", 1) for z in valid_zones)
            if total_global_runs == 0:
                total_global_runs = max(1, sum(getattr(z.rules, "num_runs", 1) for z in sorted_zones))

            for z_idx, zone in enumerate(sorted_zones, start=1):
                if self.optimizer._cancelled:
                    break

                active_cell_ids = zone.cell_ids
                if not active_cell_ids:
                    zone.status = "Omitida (Sin celdas)"
                    self.zone_completed.emit(zone.name, 0.0, 0)
                    continue

                cells_to_eval: dict[str, Cell] = {}
                for cid in active_cell_ids:
                    if cid not in self.optimizer.cells:
                        continue
                    c = copy.deepcopy(self.optimizer.cells[cid])
                    if not self.respect_cell_flags:
                        allowed_pool = set(zone.rules.available_frequencies) if hasattr(zone, "rules") and zone.rules else set()
                        if zone.recalculate_bcch:
                            c.recalculate_bcch = True
                        else:
                            c.recalculate_bcch = bool(c.bcch is None or c.bcch <= 0 or (allowed_pool and c.bcch not in allowed_pool))

                        if zone.recalculate_tch:
                            c.tch_indices_to_recalculate = list(range(len(c.tch_list)))
                        else:
                            if allowed_pool:
                                c.tch_indices_to_recalculate = [
                                    i for i, f in enumerate(c.tch_list)
                                    if f is None or f <= 0 or f not in allowed_pool
                                ]
                            else:
                                c.tch_indices_to_recalculate = []
                        c.new_tch_count = zone.new_tch_count
                    cells_to_eval[cid] = c

                if self.respect_cell_flags:
                    active_calc_cells = [cid for cid in active_cell_ids if cid in cells_to_eval and cells_to_eval[cid].needs_calculation]
                    if not active_calc_cells:
                        zone.status = "Omitida (Sin celdas a recalcular)"
                        self.zone_completed.emit(zone.name, 0.0, 0)
                        continue

                zone.status = "Optimizando"

                frontier_ids = find_frontier_cells(
                    active_cell_ids,
                    self.optimizer.cells,
                    threshold_km=getattr(zone.rules, "distance_threshold_km", self.frontier_distance_km),
                    neighbor_graph=zone.neighbor_graph or self.global_neighbor_graph
                )

                for fid in frontier_ids:
                    if fid in self.optimizer.cells:
                        c = copy.deepcopy(self.optimizer.cells[fid])
                        c.recalculate_bcch = False
                        c.tch_indices_to_recalculate = []
                        c.new_tch_count = 0
                        cells_to_eval[fid] = c

                ng = zone.neighbor_graph
                if not ng:
                    if self.global_neighbor_graph:
                        ng = self.global_neighbor_graph.filter_by_cells(set(cells_to_eval.keys()))
                    else:
                        ng = NeighborGraph()

                runs_before = sum(
                    getattr(sorted_zones[i].rules, "num_runs", 1)
                    for i in range(z_idx - 1)
                    if sorted_zones[i].cell_ids
                )

                def on_progress(*args):
                    if len(args) == 5:
                        run_idx, total_runs, gen, total_gens, best_score = args
                    elif len(args) == 3:
                        gen, total_gens, best_score = args
                        run_idx = 1
                        total_runs = getattr(zone.rules, "num_runs", 1)
                    else:
                        return

                    global_run_idx = runs_before + run_idx

                    p = ZoneOptimizationProgress(
                        zone_index=z_idx,
                        total_zones=total_zones,
                        zone_name=zone.name,
                        current_run=run_idx,
                        total_runs=total_runs,
                        current_gen=gen,
                        total_gens=total_gens,
                        best_score=best_score,
                        global_run=global_run_idx,
                        total_global_runs=total_global_runs
                    )
                    self.progress.emit(p)

                self.optimizer._current_optimizer = FrequencyOptimizer(
                    cells=cells_to_eval,
                    neighbor_graph=ng,
                    rules=zone.rules,
                    progress_callback=on_progress,
                    max_workers=self.max_workers
                )

                res_list = self.optimizer._current_optimizer.optimize()
                if self.optimizer._cancelled:
                    zone.status = "Cancelada"
                    break

                best_plan = res_list.best_plan
                zone_results[zone.zone_id] = res_list
                zone.status = "Completada"

                # Update global cells
                for assign in best_plan.assignments:
                    if assign.cell_id in active_cell_ids:
                        global_cell = self.optimizer.cells[assign.cell_id]
                        if assign.bcch is not None and assign.bcch > 0:
                            global_cell.bcch = assign.bcch
                        global_cell.tch_list = list(assign.tch_list)

                self.zone_completed.emit(zone.name, best_plan.score, len(active_cell_ids))

            self.finished.emit(zone_results, self.optimizer.cells)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        if self.optimizer:
            self.optimizer.cancel()

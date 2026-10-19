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
        max_workers: Optional[int] = None
    ):
        self.cells = copy.deepcopy(cells)
        # Sort zones by execution_order
        self.zones = sorted(zones, key=lambda z: z.execution_order)
        self.global_neighbor_graph = global_neighbor_graph
        self.progress_callback = progress_callback
        self.frontier_distance_km = frontier_distance_km
        self.max_workers = max_workers
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
                c = copy.deepcopy(self.cells[cid])
                c.recalculate_bcch = zone.recalculate_bcch
                if zone.recalculate_tch:
                    c.tch_indices_to_recalculate = list(range(len(c.tch_list)))
                else:
                    c.tch_indices_to_recalculate = []
                c.new_tch_count = zone.new_tch_count
                cells_to_eval[cid] = c

            # Add frontier cells as strictly FIXED
            for fid in frontier_ids:
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
            zone_results[zone.zone_id] = best_plan
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
        max_workers: Optional[int] = None
    ):
        super().__init__()
        self.cells = cells
        self.zones = zones
        self.global_neighbor_graph = global_neighbor_graph
        self.frontier_distance_km = frontier_distance_km
        self.max_workers = max_workers
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
                max_workers=self.max_workers
            )

            # We can also track when each zone completes
            zone_results = {}
            total_zones = len(self.zones)
            sorted_zones = sorted(self.zones, key=lambda z: z.execution_order)

            for z_idx, zone in enumerate(sorted_zones, start=1):
                if self.optimizer._cancelled:
                    break

                active_cell_ids = zone.cell_ids
                if not active_cell_ids:
                    zone.status = "Omitida (Sin celdas)"
                    continue

                zone.status = "Optimizando"

                frontier_ids = find_frontier_cells(
                    active_cell_ids,
                    self.optimizer.cells,
                    threshold_km=getattr(zone.rules, "distance_threshold_km", self.frontier_distance_km),
                    neighbor_graph=zone.neighbor_graph or self.global_neighbor_graph
                )

                cells_to_eval: dict[str, Cell] = {}
                for cid in active_cell_ids:
                    c = copy.deepcopy(self.optimizer.cells[cid])
                    c.recalculate_bcch = zone.recalculate_bcch
                    if zone.recalculate_tch:
                        c.tch_indices_to_recalculate = list(range(len(c.tch_list)))
                    else:
                        c.tch_indices_to_recalculate = []
                    c.new_tch_count = zone.new_tch_count
                    cells_to_eval[cid] = c

                for fid in frontier_ids:
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

                def on_progress(*args):
                    if len(args) == 5:
                        run_idx, total_runs, gen, total_gens, best_score = args
                    elif len(args) == 3:
                        gen, total_gens, best_score = args
                        run_idx = 1
                        total_runs = zone.rules.num_runs
                    else:
                        return

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
                zone_results[zone.zone_id] = best_plan
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

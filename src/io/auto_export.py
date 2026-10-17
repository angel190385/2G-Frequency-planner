"""
Automatic export manager for frequency planning optimization runs.

When an optimization run completes, this module automatically exports:
1. CSV results file: 'resultados_frecuencias.csv'
2. Dynamic interactive HTML report: 'informe_optimizacion.html'
3. Comprehensive debug and configuration text file: 'configuracion_y_depuracion.txt'

All files are bundled in a timestamped folder 'ejecucion_YYYYMMDD_HHMMSS' inside
the application root directory.
"""
from __future__ import annotations
import os
import sys
import platform
import multiprocessing
from datetime import datetime
from collections import Counter
from typing import Optional

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import PlanResult, format_number
from src.engine.constraints import FrequencyRules
from src.io.data_loader import export_results
from src.io.html_exporter import export_plan_html, generate_baseline_plan


def get_application_root_dir() -> str:
    """
    Get the application root directory.
    Works in development and in packaged/frozen environments (e.g. PyInstaller).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # Located in src/io/ -> up two levels to reach project root
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current_dir, "..", ".."))


def generate_debug_info_text(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    results: list[PlanResult],
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    was_cancelled: bool = False,
    workers_used: Optional[int] = None,
    app_dir: Optional[str] = None,
    export_dir: Optional[str] = None,
) -> str:
    """
    Generate an exhaustive, structured plaintext report detailing:
    - Execution metadata, timing, and cancellation status
    - System environment and hardware specs
    - Optimization rules, constraints, cost weights, and GA parameters
    - Topology and input cell statistics
    - Overall results, baseline score, and improvement metrics
    - Deep-dive into the best plan (conflict breakdown, top violations, ARFCN usage)
    - Comparative summary table of all generated plans
    """
    end_time = end_time or datetime.now()
    app_dir = app_dir or get_application_root_dir()
    export_dir = export_dir or app_dir

    duration_str = "Desconocida"
    duration_sec = 0.0
    if start_time and end_time:
        duration_sec = max(0.0, (end_time - start_time).total_seconds())
        mins = int(duration_sec // 60)
        secs = duration_sec % 60
        duration_str = f"{duration_sec:.2f} s ({mins}m {secs:.1f}s)" if mins > 0 else f"{duration_sec:.2f} s"

    status_str = "DETENIDO / CANCELADO POR EL USUARIO (Planes parciales recuperados)" if was_cancelled else "COMPLETADO CON ÉXITO"

    # Baseline evaluation
    baseline_plan = None
    baseline_score = None
    try:
        baseline_plan = generate_baseline_plan(cells, neighbor_graph, rules)
        baseline_score = baseline_plan.score
    except Exception:
        baseline_plan = None
        baseline_score = None

    best_plan = min(results, key=lambda r: r.score) if results else None
    worst_plan = max(results, key=lambda r: r.score) if results else None
    avg_score = (sum(r.score for r in results) / len(results)) if results else 0.0

    improvement_pct = 0.0
    improvement_abs = 0.0
    if baseline_score is not None and best_plan is not None:
        improvement_abs = baseline_score - best_plan.score
        if baseline_score > 0:
            improvement_pct = (improvement_abs / baseline_score) * 100.0

    # Cell statistics
    total_cells = len(cells)
    unique_stations = {getattr(c.station, "station_id", getattr(c.station, "id", str(c.station))) for c in cells.values() if getattr(c, "station", None)}
    unique_sectors = {c.sector for c in cells.values() if getattr(c, "sector", None)}
    cells_needing_calc = sum(1 for c in cells.values() if getattr(c, "needs_calculation", False))
    cells_bcch_recalc = sum(1 for c in cells.values() if getattr(c, "recalculate_bcch", False))
    cells_tch_recalc = sum(1 for c in cells.values() if len(getattr(c, "tch_indices_to_recalculate", [])) > 0)
    total_new_tch = sum(getattr(c, "new_tch_count", 0) for c in cells.values())
    cells_fixed = total_cells - cells_needing_calc

    # Neighbor relations statistics
    total_relations = neighbor_graph.total_relations if hasattr(neighbor_graph, "total_relations") else 0
    relations_with_ho = neighbor_graph.relations_with_ho if hasattr(neighbor_graph, "relations_with_ho") else 0
    all_rels = neighbor_graph.all_relations() if hasattr(neighbor_graph, "all_relations") else []
    distances = [r.distance_km for r in all_rels if hasattr(r, "distance_km")]
    min_dist = min(distances) if distances else 0.0
    max_dist = max(distances) if distances else 0.0
    mean_dist = (sum(distances) / len(distances)) if distances else 0.0
    ho_stats = neighbor_graph.get_ho_statistics() if hasattr(neighbor_graph, "get_ho_statistics") else {}

    # ARFCN usage analysis for the best plan
    channel_counter = Counter()
    bcch_counter = Counter()
    tch_counter = Counter()
    if best_plan:
        for assign in best_plan.assignments:
            if assign.bcch:
                channel_counter[assign.bcch] += 1
                bcch_counter[assign.bcch] += 1
            for tch in assign.tch_list:
                channel_counter[tch] += 1
                tch_counter[tch] += 1

    available_freqs = rules.available_frequencies
    unused_freqs = sorted([f for f in available_freqs if channel_counter[f] == 0])

    lines: list[str] = []
    w = 80

    def header(title: str):
        lines.append("")
        lines.append("=" * w)
        lines.append(f" {title}")
        lines.append("=" * w)

    def subheader(title: str):
        lines.append("")
        lines.append(f"--- {title} " + "-" * max(0, w - len(title) - 5))

    # Title
    lines.append("=" * w)
    lines.append("       FREQ PLANNER 2G — REPORTE DE CONFIGURACIÓN Y DEPURACIÓN")
    lines.append("=" * w)

    # 1. Metadata
    header("1. METADATOS Y ESTADO DE LA EJECUCIÓN")
    lines.append(f"  * Estado:                 {status_str}")
    lines.append(f"  * Fecha y Hora de Inicio: {start_time.strftime('%Y-%m-%d %H:%M:%S') if start_time else 'N/A'}")
    lines.append(f"  * Fecha y Hora de Fin:    {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  * Duración Total:         {duration_str}")
    lines.append(f"  * Directorio Aplicación:  {app_dir}")
    lines.append(f"  * Directorio Exportación: {export_dir}")
    lines.append("  * Ficheros del Paquete:   ")
    lines.append("      - resultados_frecuencias.csv      (Asignaciones por celda y conflictos)")
    lines.append("      - informe_optimizacion.html       (Dashboard dinámico interactivo)")
    lines.append("      - configuracion_y_depuracion.txt  (Este informe de auditoría)")

    # 2. System environment
    header("2. ENTORNO DEL SISTEMA Y RECURSOS")
    lines.append(f"  * Sistema Operativo:      {platform.system()} {platform.release()} (Versión: {platform.version()})")
    lines.append(f"  * Arquitectura:           {platform.machine()} ({platform.architecture()[0]})")
    lines.append(f"  * Versión de Python:      {sys.version.split()[0]} ({platform.python_implementation()})")
    lines.append(f"  * Ejecutable Python:      {sys.executable}")
    lines.append(f"  * CPUs / Cores del Host:  {multiprocessing.cpu_count()}")
    lines.append(f"  * Workers Configurados:   {workers_used if workers_used else multiprocessing.cpu_count()}")

    # 3. Rules & Constraints
    header("3. CONFIGURACIÓN DE REGLAS Y PARÁMETROS (FrequencyRules)")
    subheader("Espectro y Canales ARFCN")
    lines.append(f"  * Rango de Frecuencias:   {rules.freq_range_start} a {rules.freq_range_end} (Total banda: {rules.freq_range_end - rules.freq_range_start + 1} canales)")
    lines.append(f"  * Canales Excluidos:      {rules.excluded_frequencies if rules.excluded_frequencies else 'Ninguno'}")
    lines.append(f"  * Canales Disponibles:    {len(available_freqs)} canales utilizables")

    subheader("Separaciones Co-Sitio Requeridas")
    lines.append(f"  * Misma Celda:            {rules.min_separation_same_cell} canales")
    lines.append(f"  * Misma Estación:         {rules.min_separation_same_station} canales")
    lines.append(f"  * Vecinas BCCH:           {rules.min_separation_bcch_neighbors} canales")

    subheader("Restricciones Duras (Hard Constraints)")
    lines.append(f"  * Co-canal Misma Estación:       {'Permitido' if rules.allow_cochannel_same_station else 'PROHIBIDO'}")
    lines.append(f"  * Co-canal BCCH-BCCH Vecinas:    {'Permitido' if rules.allow_cochannel_bcch_bcch_neighbors else 'PROHIBIDO'}")
    lines.append(f"  * Co-canal BCCH-TCH Vecinas:     {'Permitido' if rules.allow_cochannel_bcch_tch_neighbors else 'PROHIBIDO'}")
    lines.append(f"  * Co-canal TCH-TCH Vecinas:      {'Permitido' if rules.allow_cochannel_tch_tch_neighbors else 'PROHIBIDO'}")
    lines.append(f"  * Adyacente Misma Estación:      {'Permitido' if rules.allow_adjacent_same_station else 'PROHIBIDO'}")
    lines.append(f"  * Adyacente BCCH-BCCH Vecinas:   {'Permitido' if rules.allow_adjacent_bcch_bcch_neighbors else 'PROHIBIDO'}")
    lines.append(f"  * Adyacente BCCH-TCH Vecinas:    {'Permitido' if rules.allow_adjacent_bcch_tch_neighbors else 'PROHIBIDO'}")
    lines.append(f"  * Adyacente TCH-TCH Vecinas:     {'Permitido' if rules.allow_adjacent_tch_tch_neighbors else 'PROHIBIDO'}")

    subheader("Pesos de Coste (Penalizaciones Soft Constraints)")
    lines.append(f"  * Co-canal BCCH-BCCH:            {rules.weight_cochannel_bcch_bcch:.1f}")
    lines.append(f"  * Co-canal BCCH-TCH:             {rules.weight_cochannel_bcch_tch:.1f}")
    lines.append(f"  * Co-canal TCH-TCH:              {rules.weight_cochannel_tch_tch:.1f}")
    lines.append(f"  * Adyacente BCCH-BCCH:           {rules.weight_adjacent_bcch_bcch:.1f}")
    lines.append(f"  * Adyacente BCCH-TCH:            {rules.weight_adjacent_bcch_tch:.1f}")
    lines.append(f"  * Adyacente TCH-TCH:             {rules.weight_adjacent_tch_tch:.1f}")
    lines.append(f"  * Misma Estación Co-canal:       {rules.weight_same_station_cochannel:.1f}")
    lines.append(f"  * Misma Estación Adyacente:      {rules.weight_same_station_adjacent:.1f}")

    subheader("Ponderación por Traspasos (Handover)")
    lines.append(f"  * Modelo Estadístico:            {rules.ho_statistical_model}")

    subheader("Ponderación Espacial / Distancia")
    lines.append(f"  * Factor Atenuación Distancia:   {rules.distance_decay:.2f} / km")
    lines.append(f"  * Multiplicador Vecina Grafo:    {rules.weight_neighbor_factor:.2f}")
    lines.append(f"  * Penalización Distancia Activa: {'Sí' if rules.enable_distance_penalty else 'No'}")
    lines.append(f"  * Umbral Distancia:              {rules.distance_threshold_km:.1f} km")
    lines.append(f"  * Peso por Distancia:            {rules.weight_distance:.1f}")

    subheader("Ponderación por Azimuth y Orientación de Sectores")
    lines.append(f"  * Ponderación Azimuth Activa:    {'Sí' if rules.enable_azimuth_weight else 'No'}")
    lines.append(f"  * Factor Mínimo (Divergentes):   {rules.azimuth_weight_min:.2f}x")
    lines.append(f"  * Factor Máximo (Enfrentadas):   {rules.azimuth_weight_max:.2f}x")

    subheader("Parámetros del Algoritmo Genético")
    lines.append(f"  * Tamaño de Población:           {rules.population_size}")
    lines.append(f"  * Número de Generaciones:        {rules.num_generations}")
    lines.append(f"  * Ejecuciones Paralelas (Runs):  {rules.num_runs}")
    lines.append(f"  * Tasa de Cruce (Crossover):     {rules.crossover_rate:.2f}")
    lines.append(f"  * Tasa de Mutación:              {rules.mutation_rate:.2f}")
    lines.append(f"  * Individuos de Élite:           {rules.elite_count}")
    lines.append(f"  * Top-K Resultados Guardados:    {rules.top_k_results}")

    # 4. Topology & Inputs
    header("4. TOPOLOGÍA DE RED Y DATOS DE ENTRADA")
    lines.append(f"  * Total Celdas Cargadas:         {total_cells}")
    lines.append(f"  * Emplazamientos Únicos (Sites): {len(unique_stations)}")
    lines.append(f"  * Sectores Únicos:               {len(unique_sectors)}")
    lines.append(f"  * Celdas a Optimizar:            {cells_needing_calc} ({total_cells - cells_fixed} variables, {cells_fixed} fijas)")
    lines.append(f"      - Con BCCH a Recalcular:     {cells_bcch_recalc}")
    lines.append(f"      - Con TCH a Recalcular:      {cells_tch_recalc}")
    lines.append(f"      - Nuevos TCH Añadidos:       {total_new_tch}")
    lines.append(f"  * Relaciones de Vecindad:        {total_relations}")
    lines.append(f"      - Con Estadísticas HO:       {relations_with_ho}")
    lines.append(f"      - Sin Estadísticas HO:       {total_relations - relations_with_ho}")
    lines.append(f"  * Estadísticas de Distancia:")
    lines.append(f"      - Mínima:                    {min_dist:.2f} km")
    lines.append(f"      - Máxima:                    {max_dist:.2f} km")
    lines.append(f"      - Media:                     {mean_dist:.2f} km")
    if ho_stats.get("count", 0) > 0:
        lines.append(f"  * Estadísticas de Traspasos (HO):")
        lines.append(f"      - Total Enlaces con HO:      {int(ho_stats.get('count', 0))}")
        lines.append(f"      - HO Máximo en un Enlace:    {int(ho_stats.get('max', 0))}")
        lines.append(f"      - HO Medio:                  {ho_stats.get('mean', 0):.1f}")
        lines.append(f"      - HO Mediana:                {ho_stats.get('median', 0):.1f}")

    # 5. Global Results Summary
    header("5. RESUMEN GLOBAL DE RESULTADOS DE LA OPTIMIZACIÓN")
    lines.append(f"  * Total Planes Devueltos:        {len(results)}")
    if baseline_score is not None:
        lines.append(f"  * Score Plan Base (Original):    {format_number(baseline_score)}")
    if best_plan:
        lines.append(f"  * Mejor Score Obtenido:          {format_number(best_plan.score)}")
    if worst_plan:
        lines.append(f"  * Peor Score Obtenido:           {format_number(worst_plan.score)}")
    lines.append(f"  * Score Promedio:                {format_number(avg_score)}")
    if baseline_score is not None and best_plan is not None:
        lines.append(f"  * Reducción Absoluta Coste:      {format_number(improvement_abs)}")
        lines.append(f"  * Mejora Porcentual vs Base:     {improvement_pct:.2f}%")

    # 6. Best Plan Deep-Dive
    if best_plan:
        header(f"6. DETALLE EXHAUSTIVO DEL MEJOR PLAN (Plan #{best_plan.plan_id})")
        lines.append(f"  * Score Total:                   {format_number(best_plan.score)}")
        lines.append(f"  * Coste Heredado (Original):     {format_number(best_plan.inherited_cost)}")
        lines.append(f"  * Coste Nuevas Asignaciones:     {format_number(best_plan.new_assignment_cost)}")
        lines.append(f"  * Coste Ponderado por HO (Co):   {format_number(best_plan.cochannel_ho_weighted)}")
        lines.append(f"  * Coste Ponderado por HO (Adj):  {format_number(best_plan.adjacent_ho_weighted)}")
        lines.append("  * Conteo de Conflictos / Violaciones:")
        lines.append(f"      - Co-canal BCCH-BCCH:        {best_plan.cochannel_bcch_bcch}")
        lines.append(f"      - Co-canal BCCH-TCH:         {best_plan.cochannel_bcch_tch}")
        lines.append(f"      - Co-canal TCH-TCH:          {best_plan.cochannel_tch_tch}")
        lines.append(f"      - Adyacente BCCH-BCCH:       {best_plan.adjacent_bcch_bcch}")
        lines.append(f"      - Adyacente BCCH-TCH:        {best_plan.adjacent_bcch_tch}")
        lines.append(f"      - Adyacente TCH-TCH:         {best_plan.adjacent_tch_tch}")
        lines.append(f"      - Co-canal Misma Estación:   {best_plan.same_station_cochannel}")
        lines.append(f"      - Adyacente Misma Estación:  {best_plan.same_station_adjacent}")
        lines.append(f"      - TOTAL Violaciones:         {best_plan.total_violations}")
        lines.append(f"      - Violaciones Críticas:      {best_plan.critical_violations}")

        # Top violations list
        subheader(f"Top 30 Conflictos Más Severos (de {len(best_plan.violations)} detectados)")
        sorted_violations = sorted(best_plan.violations, key=lambda v: getattr(v, "weighted_cost", 0.0), reverse=True)
        top_v = sorted_violations[:30]

        if top_v:
            lines.append(f"{'#':<3} | {'Tipo':<10} | {'Severidad':<8} | {'Celda A':<14} | {'Celda B':<14} | {'Freqs':<9} | {'Dist(km)':<8} | {'HO':<5} | {'Coste':<8} | {'Heredado'}")
            lines.append("-" * w)
            for idx, v in enumerate(top_v, 1):
                ho_str = str(v.ho_count) if v.ho_count is not None else "-"
                freq_str = f"{v.freq_a}↔{v.freq_b}"
                heredado_str = "Sí" if getattr(v, "is_inherited", False) else "No"
                dist_str = f"{v.distance_km:.2f}"
                cost_str = format_number(v.weighted_cost, precision=1)
                lines.append(
                    f"{idx:<3} | {v.violation_type:<10} | {v.severity:<8} | "
                    f"{v.cell_a_id:<14} | {v.cell_b_id:<14} | {freq_str:<9} | "
                    f"{dist_str:<8} | {ho_str:<5} | {cost_str:<8} | {heredado_str}"
                )
        else:
            lines.append("  (No se registraron conflictos ni violaciones en el mejor plan)")

        # Spectrum histogram
        subheader("Distribución de Frecuencias Asignadas (Histograma ARFCN)")
        lines.append(f"  * Canales ARFCN Utilizados:      {len(channel_counter)} / {len(available_freqs)}")
        lines.append(f"  * Canales Libres (Sin Uso):      {len(unused_freqs)}")
        if unused_freqs:
            lines.append(f"      {', '.join(str(f) for f in unused_freqs[:30])}" + ("..." if len(unused_freqs) > 30 else ""))

        lines.append("")
        lines.append(f"{'ARFCN':<6} | {'Total Usos':<10} | {'BCCH':<6} | {'TCH':<6} | {'Gráfico de Ocupación'}")
        lines.append("-" * 65)
        for f in available_freqs:
            tot = channel_counter[f]
            b = bcch_counter[f]
            t = tch_counter[f]
            bar = "█" * min(tot, 35)
            lines.append(f"{f:<6} | {tot:<10} | {b:<6} | {t:<6} | {bar}")

    # 7. Comparative table of all plans
    header("7. TABLA COMPARATIVA DE TODOS LOS PLANES GENERADOS")
    lines.append(f"{'#':<3} | {'Plan':<5} | {'Score':<8} | {'Co-BCCH':<7} | {'Co-TCH':<7} | {'Adj-BCCH':<8} | {'Adj-TCH':<7} | {'Misma Est':<9} | {'Total Viol':<10} | {'Mejora vs Base'}")
    lines.append("-" * w)
    for idx, plan in enumerate(results, 1):
        plan_co_bcch = plan.cochannel_bcch_bcch + plan.cochannel_bcch_tch
        plan_co_tch = plan.cochannel_tch_tch
        plan_adj_bcch = plan.adjacent_bcch_bcch + plan.adjacent_bcch_tch
        plan_adj_tch = plan.adjacent_tch_tch
        same_st = plan.same_station_cochannel + plan.same_station_adjacent
        mejora_str = "-"
        if baseline_score and baseline_score > 0:
            pct = ((baseline_score - plan.score) / baseline_score) * 100.0
            mejora_str = f"{pct:+.1f}%"

        lines.append(
            f"{idx:<3} | {plan.plan_id:<5} | {format_number(plan.score):<8} | "
            f"{plan_co_bcch:<7} | {plan_co_tch:<7} | {plan_adj_bcch:<8} | "
            f"{plan_adj_tch:<7} | {same_st:<9} | {plan.total_violations:<10} | {mejora_str}"
        )

    lines.append("")
    lines.append("=" * w)
    lines.append("                 FIN DEL REPORTE DE DEPURACIÓN")
    lines.append("=" * w)
    lines.append("")

    return "\n".join(lines)


def export_execution_bundle(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    results: list[PlanResult],
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    was_cancelled: bool = False,
    workers_used: Optional[int] = None,
    app_dir: Optional[str] = None,
    folder_prefix: str = "ejecucion"
) -> dict[str, str]:
    """
    Bundle execution exports into a timestamped directory in the application root path.

    Creates:
    - <app_dir>/<folder_prefix>_YYYYMMDD_HHMMSS/
        ├── resultados_frecuencias.csv
        ├── informe_optimizacion.html
        └── configuracion_y_depuracion.txt

    Returns:
        dict with keys: 'folder', 'csv', 'html', 'debug'
    """
    if not results:
        raise ValueError("No hay resultados de optimización para exportar.")

    app_dir = app_dir or get_application_root_dir()
    end_time = end_time or datetime.now()

    # Timestamp formatted folder
    timestamp_str = end_time.strftime("%Y%m%d_%H%M%S")
    folder_name = f"{folder_prefix}_{timestamp_str}"
    export_dir = os.path.join(app_dir, folder_name)

    # Avoid collision in rapid successive calls
    counter = 1
    base_export_dir = export_dir
    while os.path.exists(export_dir):
        export_dir = f"{base_export_dir}_{counter}"
        counter += 1

    os.makedirs(export_dir, exist_ok=True)

    csv_path = os.path.join(export_dir, "resultados_frecuencias.csv")
    html_path = os.path.join(export_dir, "informe_optimizacion.html")
    debug_path = os.path.join(export_dir, "configuracion_y_depuracion.txt")

    # 1. Export CSV
    export_results(csv_path, results, cells)

    # 2. Export HTML
    export_plan_html(
        file_path=html_path,
        cells=cells,
        neighbor_graph=neighbor_graph,
        rules=rules,
        results=results
    )

    # 3. Export detailed debug text report
    debug_text = generate_debug_info_text(
        cells=cells,
        neighbor_graph=neighbor_graph,
        rules=rules,
        results=results,
        start_time=start_time,
        end_time=end_time,
        was_cancelled=was_cancelled,
        workers_used=workers_used,
        app_dir=app_dir,
        export_dir=export_dir
    )
    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(debug_text)

    return {
        "folder": export_dir,
        "csv": csv_path,
        "html": html_path,
        "debug": debug_path
    }

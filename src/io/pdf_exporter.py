"""
PDF exporter for frequency planning results and statistical comparison.

Generates comprehensive, professional PDF reports comparing the original
baseline plan against up to 3 calculated scenarios, including statistical weight
methodology, metric tables, cell-by-cell assignment changes, and residual violations.
"""
from __future__ import annotations
import os
import sys
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QTextDocument, QPdfWriter, QPageLayout, QPageSize
from PyQt6.QtCore import QMarginsF

from src.models.cell import Cell
from src.models.neighbor import NeighborGraph
from src.models.frequency_plan import PlanResult, FrequencyAssignment
from src.engine.constraints import FrequencyRules, STATISTICAL_MODELS
from src.engine.cost_function import evaluate_plan


def generate_baseline_plan(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules
) -> PlanResult:
    """Evaluate the current/original cell frequencies to obtain a baseline PlanResult."""
    assignments: dict[str, FrequencyAssignment] = {}
    for cid, cell in cells.items():
        assignments[cid] = FrequencyAssignment(
            cell_id=cid,
            bcch=cell.bcch if cell.bcch is not None else 0,
            tch_list=list(cell.tch_list)
        )

    return evaluate_plan(
        assignments=assignments,
        cells=cells,
        neighbor_graph=neighbor_graph,
        rules=rules,
        plan_id=0,
        run_id=0,
        collect_violations=True
    )


def export_plan_pdf(
    file_path: str,
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    results: list[PlanResult],
    selected_plan_id: Optional[int] = None
) -> str:
    """
    Generate and save a comprehensive PDF report.

    Args:
        file_path: Target path for the PDF file.
        cells: Loaded network cells dictionary.
        neighbor_graph: Neighbor relations and handover graph.
        rules: Active frequency rules and configuration.
        results: List of calculated PlanResult objects.
        selected_plan_id: ID of the selected plan to highlight (defaults to first).

    Returns:
        The written file path.
    """
    app = QApplication.instance() or QApplication(sys.argv)

    # 1. Baseline evaluation
    baseline = generate_baseline_plan(cells, neighbor_graph, rules)

    # 2. Extract up to 3 candidate scenarios
    scenarios: list[PlanResult] = []
    if results:
        # Sort by score ascending (best first)
        sorted_results = sorted(results, key=lambda r: r.score)
        scenarios = sorted_results[:3]

    # Find the target selected plan
    selected_plan = None
    if selected_plan_id is not None:
        for r in results:
            if r.plan_id == selected_plan_id:
                selected_plan = r
                break
    if not selected_plan and scenarios:
        selected_plan = scenarios[0]

    # 3. Model info
    stat_key = getattr(rules, "ho_statistical_model", "logarithmic")
    stat_info = STATISTICAL_MODELS.get(stat_key, STATISTICAL_MODELS["logarithmic"])
    ho_stats = neighbor_graph.get_ho_statistics()

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    # 4. Build HTML content
    html = _build_report_html(
        cells=cells,
        neighbor_graph=neighbor_graph,
        rules=rules,
        baseline=baseline,
        scenarios=scenarios,
        selected_plan=selected_plan,
        stat_info=stat_info,
        ho_stats=ho_stats,
        now_str=now_str
    )

    # 5. Render via QPdfWriter + QTextDocument
    writer = QPdfWriter(file_path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    writer.setTitle("Informe de Planificación de Frecuencias GSM 2G")
    writer.setCreator("Freq Planner 2G")

    doc = QTextDocument()
    doc.setHtml(html)
    doc.print(writer)
    del writer

    return file_path


def _calc_improvement(old_val: float, new_val: float) -> str:
    """Calculate percentage improvement: (old - new) / old * 100."""
    if old_val <= 0:
        if new_val == 0:
            return "0.0%"
        return "N/A"
    diff = old_val - new_val
    pct = (diff / old_val) * 100.0
    if pct > 0:
        return f"+{pct:.1f}%"
    elif pct == 0:
        return "0.0%"
    else:
        return f"{pct:.1f}%"


def _build_report_html(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    baseline: PlanResult,
    scenarios: list[PlanResult],
    selected_plan: Optional[PlanResult],
    stat_info: dict,
    ho_stats: dict,
    now_str: str
) -> str:
    """Construct HTML with CSS for the PDF document."""

    # Scenario column headers
    sc_headers = []
    for i, sc in enumerate(scenarios, start=1):
        label = f"Escenario {i}"
        if i == 1:
            label += " (Mejor)"
        sc_headers.append((sc, label))

    # Metric table rows
    def metric_row(label: str, base_val, get_val_fn, highlight_best: bool = True):
        tds = [f"<td style='padding:5px; font-weight:bold;'>{label}</td>"]
        tds.append(f"<td style='padding:5px; text-align:center;'>{base_val}</td>")

        for sc, _ in sc_headers:
            val = get_val_fn(sc)
            tds.append(f"<td style='padding:5px; text-align:center;'>{val}</td>")

        # Fill remaining columns if fewer than 3 scenarios
        for _ in range(3 - len(sc_headers)):
            tds.append("<td style='padding:5px; text-align:center; color:#888;'>—</td>")

        # Improvement of best scenario vs baseline
        if scenarios:
            best_val = get_val_fn(scenarios[0])
            try:
                b_num = float(base_val)
                s_num = float(best_val)
                imp = _calc_improvement(b_num, s_num)
                color = "#00b894" if imp.startswith("+") else "#2d3436"
                tds.append(f"<td style='padding:5px; text-align:center; font-weight:bold; color:{color};'>{imp}</td>")
            except (ValueError, TypeError):
                tds.append("<td style='padding:5px; text-align:center;'>—</td>")
        else:
            tds.append("<td style='padding:5px; text-align:center;'>—</td>")

        return f"<tr>{''.join(tds)}</tr>"

    comparison_rows = [
        metric_row("Score Global (Coste)", f"{baseline.score:.1f}", lambda s: f"{s.score:.1f}"),
        metric_row("Violaciones Críticas (BCCH-BCCH)", baseline.critical_violations, lambda s: s.critical_violations),
        metric_row("Total Violaciones (Cocanal + Adj)", baseline.total_violations, lambda s: s.total_violations),
        metric_row("Cocanalidad BCCH-BCCH", baseline.cochannel_bcch_bcch, lambda s: s.cochannel_bcch_bcch),
        metric_row("Cocanalidad BCCH-TCH", baseline.cochannel_bcch_tch, lambda s: s.cochannel_bcch_tch),
        metric_row("Cocanalidad TCH-TCH", baseline.cochannel_tch_tch, lambda s: s.cochannel_tch_tch),
        metric_row("Adyacencia BCCH-BCCH", baseline.adjacent_bcch_bcch, lambda s: s.adjacent_bcch_bcch),
        metric_row("Adyacencia BCCH-TCH", baseline.adjacent_bcch_tch, lambda s: s.adjacent_bcch_tch),
        metric_row("Adyacencia TCH-TCH", baseline.adjacent_tch_tch, lambda s: s.adjacent_tch_tch),
        metric_row("Cocanalidad Ponderada por HO", f"{baseline.cochannel_ho_weighted:.1f}", lambda s: f"{s.cochannel_ho_weighted:.1f}"),
        metric_row("Adyacencia Ponderada por HO", f"{baseline.adjacent_ho_weighted:.1f}", lambda s: f"{s.adjacent_ho_weighted:.1f}"),
        metric_row("Misma Estación (Cocanal + Adj)", baseline.same_station_cochannel + baseline.same_station_adjacent,
                   lambda s: s.same_station_cochannel + s.same_station_adjacent),
    ]

    # Cell assignment changes table (Before vs After in selected plan)
    plan_to_detail = selected_plan if selected_plan else (scenarios[0] if scenarios else baseline)
    assign_dict = {a.cell_id: a for a in plan_to_detail.assignments}

    cell_rows = []
    total_cells = len(cells)
    changed_cells = 0

    for cid in sorted(cells.keys()):
        c = cells[cid]
        a = assign_dict.get(cid)
        if not a:
            continue

        bcch_before = c.bcch if c.bcch is not None else "—"
        bcch_after = a.bcch
        bcch_changed = (c.bcch != a.bcch)

        tch_before = ",".join(str(f) for f in c.tch_list) if c.tch_list else "—"
        tch_after = ",".join(str(f) for f in a.tch_list) if a.tch_list else "—"
        tch_changed = (tch_before != tch_after)

        if bcch_changed or tch_changed:
            changed_cells += 1
            status_html = "<span style='color:#e67e22; font-weight:bold;'>✓ Modificado</span>"
        else:
            status_html = "<span style='color:#7f8c8d;'>Sin cambio</span>"

        bcch_after_style = "color:#d63031; font-weight:bold;" if bcch_changed else ""
        tch_after_style = "color:#0984e3; font-weight:bold;" if tch_changed else ""

        cell_rows.append(f"""
            <tr>
                <td style='padding:4px; font-weight:bold;'>{c.cell_id}</td>
                <td style='padding:4px;'>{c.station.name}</td>
                <td style='padding:4px; text-align:center;'>{c.sector}</td>
                <td style='padding:4px; text-align:center;'>{bcch_before}</td>
                <td style='padding:4px; text-align:center; {bcch_after_style}'>{bcch_after}</td>
                <td style='padding:4px;'>{tch_before}</td>
                <td style='padding:4px; {tch_after_style}'>{tch_after}</td>
                <td style='padding:4px; text-align:center;'>{status_html}</td>
            </tr>
        """)

    # Top residual violations
    viol_rows = []
    if plan_to_detail.violations:
        for v in sorted(plan_to_detail.violations, key=lambda x: x.weighted_cost, reverse=True)[:15]:
            both = f"{v.channel_type_a}-{v.channel_type_b}"
            kind = "Cocanal" if v.violation_type == "cochannel" else "Adyacente"
            ho_str = str(v.ho_count) if v.ho_count is not None else "—"
            dist_str = f"{v.distance_km:.2f} km" if v.distance_km > 0 else "0.0 km"
            viol_rows.append(f"""
                <tr>
                    <td style='padding:4px;'>{kind} ({both})</td>
                    <td style='padding:4px; font-weight:bold;'>{v.cell_a_id}</td>
                    <td style='padding:4px; font-weight:bold;'>{v.cell_b_id}</td>
                    <td style='padding:4px; text-align:center;'>{v.freq_a}</td>
                    <td style='padding:4px; text-align:center;'>{v.freq_b}</td>
                    <td style='padding:4px; text-align:center;'>{ho_str}</td>
                    <td style='padding:4px; text-align:center;'>{dist_str}</td>
                    <td style='padding:4px; text-align:center; font-weight:bold;'>{v.weighted_cost:.1f}</td>
                </tr>
            """)
    else:
        viol_rows.append("<tr><td colspan='8' style='padding:8px; text-align:center; color:#00b894; font-weight:bold;'>✅ Excelente: Plan óptimo sin violaciones registradas.</td></tr>")

    # Complete HTML assembly
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 11px;
            color: #2d3436;
            margin: 0;
            padding: 0;
        }}
        h1 {{
            color: #0984e3;
            font-size: 20px;
            margin-bottom: 2px;
        }}
        h2 {{
            color: #2d3436;
            font-size: 14px;
            border-bottom: 2px solid #0984e3;
            padding-bottom: 3px;
            margin-top: 16px;
            margin-bottom: 8px;
        }}
        .header-box {{
            background-color: #f1f2f6;
            border: 1px solid #dfe4ea;
            border-radius: 6px;
            padding: 8px;
            margin-bottom: 12px;
        }}
        .card-box {{
            background-color: #f8f9fa;
            border-left: 4px solid #00b894;
            padding: 8px 12px;
            margin-bottom: 12px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 12px;
            font-size: 10px;
        }}
        th {{
            background-color: #2f3542;
            color: #ffffff;
            padding: 6px;
            text-align: center;
        }}
        tr:nth-child(even) {{
            background-color: #f8f9fa;
        }}
        td {{
            border-bottom: 1px solid #e1e2e6;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: bold;
        }}
    </style>
    </head>
    <body>

    <!-- Header -->
    <table style='width:100%; margin-bottom:4px;'>
        <tr>
            <td>
                <h1>📡 Freq Planner 2G — Informe de Planificación y Optimización</h1>
                <div style='color:#57606f; font-size:11px;'>Comparativa Estadística del Plan Actual y Escenarios de Frecuencias Calculados</div>
            </td>
            <td style='text-align:right; vertical-align:top; color:#747d8c;'>
                <b>Generado:</b> {now_str}<br>
                <b>Celdas:</b> {total_cells} | <b>Vecindades:</b> {neighbor_graph.total_relations}
            </td>
        </tr>
    </table>

    <div class='header-box'>
        <table style='width:100%; margin:0;'>
            <tr>
                <td style='border:none; width:33%;'>
                    <b>Rango ARFCN:</b> {rules.freq_range_start} a {rules.freq_range_end} ({len(rules.available_frequencies)} disponibles)<br>
                    <b>Excluidas:</b> {', '.join(str(f) for f in rules.excluded_frequencies) if rules.excluded_frequencies else 'Ninguna'}
                </td>
                <td style='border:none; width:34%;'>
                    <b>Modelo Estadístico HO:</b> {stat_info['name']}<br>
                    <b>Handovers cargados:</b> {int(ho_stats.get('count', 0))} (Media: {ho_stats.get('mean', 0):.1f})
                </td>
                <td style='border:none; width:33%;'>
                    <b>Configuración Algoritmo:</b> {rules.population_size} indiv. × {rules.num_generations} gen.<br>
                    <b>Ejecuciones independientes:</b> {rules.num_runs} pasadas
                </td>
            </tr>
        </table>
    </div>

    <!-- Section 1: Methodology and Statistical Weight Calculation -->
    <h2>1. Metodología Estadística de Cálculo de Pesos e Interferencia</h2>
    <div class='card-box'>
        <b>Modelo Seleccionado: {stat_info['name']}</b><br>
        <div style='color:#0984e3; font-weight:bold; margin:3px 0;'>📐 Formulación Matemática: {stat_info['formula']}</div>
        <p style='margin: 4px 0;'><b>Fundamento Estadístico:</b> {stat_info['description']}</p>
        <p style='margin: 2px 0; color:#e67e22;'><b>Criterio Operativo:</b> {stat_info['usage']}</p>
        <p style='margin: 4px 0; font-size: 9.5px; color:#57606f;'>
            La función de coste global evalúa simultáneamente las restricciones duras (Hard Constraints)
            y blandas (Soft Constraints). Para cada par de canales cocanal o adyacente entre celdas vecinas,
            el coste se multiplica por el factor de riesgo estadístico de tráfico <i>w</i><sub>HO</sub> y
            se penaliza adicionalmente por distancia geográfica inversa.
        </p>
    </div>

    <!-- Section 2: Scenario Comparison Table -->
    <h2>2. Comparativa de Escenarios: Plan Actual vs Escenarios Calculados</h2>
    <p style='color:#57606f; margin-top:2px; margin-bottom:6px;'>
        Esta tabla permite contrastar el plan original con los 3 mejores escenarios encontrados por el optimizador genético,
        facilitando la toma de decisiones y el almacenamiento de estadísticas de mejora.
    </p>

    <table>
        <thead>
            <tr>
                <th style='text-align:left; width:32%;'>Métrica de Calidad de Red</th>
                <th style='width:13%; background-color:#747d8c;'>Plan Actual (Original)</th>
                <th style='width:13%; background-color:#00b894;'>Escenario 1 (Recomendado)</th>
                <th style='width:13%;'>Escenario 2</th>
                <th style='width:13%;'>Escenario 3</th>
                <th style='width:16%; background-color:#2f3542;'>% Mejora Esc. 1</th>
            </tr>
        </thead>
        <tbody>
            {''.join(comparison_rows)}
        </tbody>
    </table>

    <div style='background-color:#e8f8f5; border:1px solid #a3e4d7; border-radius:4px; padding:6px; margin-bottom:12px;'>
        <b>💡 Conclusión y Recomendación:</b> El <b>Escenario 1</b> ofrece una reducción global de coste
        respecto al Plan Actual, habiendo modificado {changed_cells} de {total_cells} celdas ({changed_cells/total_cells*100:.1f}% de la red).
        Preserva la continuidad operativa y minimiza interferencias en las adyacencias con mayor volumen de traspasos.
    </div>

    <!-- Section 3: Cell Modifications Details -->
    <h2>3. Detalle de Asignaciones por Celda ({'Plan Seleccionado: Escenario 1' if not selected_plan else f'Plan {selected_plan.plan_id}'})</h2>
    <p style='color:#57606f; margin-top:2px; margin-bottom:6px;'>
        Canales BCCH y TCH asignados antes y después de la optimización en las celdas de la red:
    </p>

    <table>
        <thead>
            <tr>
                <th style='width:12%;'>Cell ID</th>
                <th style='width:15%;'>Estación</th>
                <th style='width:8%;'>Sector</th>
                <th style='width:10%;'>BCCH Antes</th>
                <th style='width:12%;'>BCCH Después</th>
                <th style='width:18%;'>TCH Antes</th>
                <th style='width:18%;'>TCH Después</th>
                <th style='width:15%;'>Estado</th>
            </tr>
        </thead>
        <tbody>
            {''.join(cell_rows[:120])}
        </tbody>
    </table>
    {'<p style="color:#747d8c; font-style:italic;">* Mostrando las primeras 120 celdas para optimizar extensión del informe.</p>' if len(cell_rows) > 120 else ''}

    <!-- Section 4: Residual Violations -->
    <h2>4. Relaciones Críticas y Violaciones Residuales</h2>
    <p style='color:#57606f; margin-top:2px; margin-bottom:6px;'>
        Principales conflictos remanentes ordenados por coste ponderado de interferencia:
    </p>

    <table>
        <thead>
            <tr>
                <th style='width:18%;'>Tipo Conflicto</th>
                <th style='width:13%;'>Celda A</th>
                <th style='width:13%;'>Celda B</th>
                <th style='width:9%;'>Freq A</th>
                <th style='width:9%;'>Freq B</th>
                <th style='width:11%;'>Handovers</th>
                <th style='width:13%;'>Distancia</th>
                <th style='width:14%;'>Coste Ponderado</th>
            </tr>
        </thead>
        <tbody>
            {''.join(viol_rows)}
        </tbody>
    </table>

    <div style='text-align:center; font-size:9.5px; color:#a4b0be; margin-top:16px; border-top:1px solid #dfe4ea; padding-top:6px;'>
        Informe generado automáticamente por <b>Freq Planner 2G</b> — Todos los derechos reservados.
    </div>

    </body>
    </html>
    """
    return html

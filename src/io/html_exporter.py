"""
Dynamic HTML report exporter for frequency planning results and scenario comparison.

Generates self-contained, interactive, modern HTML dashboards with live scenario switching,
search and filter capabilities for cell assignments, statistical charts, and full print/export styling.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime
from typing import Optional

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


def export_plan_html(
    file_path: str,
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    results: list[PlanResult],
    selected_plan_id: Optional[int] = None
) -> str:
    """
    Generate and save a rich dynamic interactive HTML dashboard report.

    Args:
        file_path: Target path for the HTML file (.html).
        cells: Loaded network cells dictionary.
        neighbor_graph: Neighbor relations and handover graph.
        rules: Active frequency rules and configuration.
        results: List of calculated PlanResult objects.
        selected_plan_id: ID of the selected plan to highlight.

    Returns:
        The written file path.
    """
    # 1. Baseline evaluation
    baseline = generate_baseline_plan(cells, neighbor_graph, rules)

    # 2. Extract best candidate scenario per execution/run
    scenarios: list[PlanResult] = []
    per_run = getattr(results, "per_run", None)

    if per_run:
        for run_id in sorted(per_run.keys()):
            run_plans = per_run[run_id]
            if run_plans:
                best_of_run = min(run_plans, key=lambda r: r.score)
                scenarios.append(best_of_run)
    elif results:
        runs_map: dict[int, list[PlanResult]] = {}
        for r in results:
            runs_map.setdefault(r.run_id, []).append(r)

        for run_id in sorted(runs_map.keys()):
            best_of_run = min(runs_map[run_id], key=lambda r: r.score)
            scenarios.append(best_of_run)

    # Sort scenarios from best score (lowest) to worst across all executions
    scenarios.sort(key=lambda r: r.score)

    if not scenarios and results:
        scenarios = [min(results, key=lambda r: r.score)]

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

    # 4. Prepare JSON data payload for dynamic JS dashboard
    payload = _build_payload(
        cells=cells,
        neighbor_graph=neighbor_graph,
        rules=rules,
        baseline=baseline,
        scenarios=scenarios,
        stat_info=stat_info,
        ho_stats=ho_stats,
        now_str=now_str
    )

    # 5. Build HTML file
    html = _build_html_template(payload)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html)

    return file_path


def _calc_improvement(old_val: float, new_val: float) -> str:
    if old_val <= 0:
        return "0.0%" if new_val == 0 else "N/A"
    diff = old_val - new_val
    pct = (diff / old_val) * 100.0
    return f"+{pct:.1f}%" if pct > 0 else f"{pct:.1f}%"


def _compute_channel_stats(plan: PlanResult, cells: dict[str, Cell]) -> dict[str, dict]:
    """Calculate frequency-level aggregated statistics for a plan."""
    assign_map = {a.cell_id: a for a in plan.assignments}
    stats: dict[int, dict] = {}

    def get_entry(freq: int) -> dict:
        if freq not in stats:
            stats[freq] = {
                "freq": freq,
                "bcch_count": 0,
                "tch_count": 0,
                "total_count": 0,
                "cochannel_conflicts": 0,
                "adjacent_conflicts": 0,
                "total_conflicts": 0,
                "conflict_cost": 0.0,
                "cells": []
            }
        return stats[freq]

    for cid, a in assign_map.items():
        cell = cells.get(cid)
        st_name = cell.station.name if cell and cell.station else ""
        sector = cell.sector if cell else ""
        if a.bcch and a.bcch > 0:
            e = get_entry(a.bcch)
            e["bcch_count"] += 1
            e["total_count"] += 1
            e["cells"].append({"cell_id": cid, "role": "BCCH", "station": st_name, "sector": sector})
        for f in a.tch_list:
            if f and f > 0:
                e = get_entry(f)
                e["tch_count"] += 1
                e["total_count"] += 1
                e["cells"].append({"cell_id": cid, "role": "TCH", "station": st_name, "sector": sector})

    for v in plan.violations:
        for f in (v.freq_a, v.freq_b):
            if f and f > 0:
                e = get_entry(f)
                if v.violation_type == "cochannel":
                    e["cochannel_conflicts"] += 1
                else:
                    e["adjacent_conflicts"] += 1
                e["total_conflicts"] += 1
                e["conflict_cost"] = round(e["conflict_cost"] + (v.weighted_cost / 2.0), 1)

    return {str(k): v for k, v in sorted(stats.items())}


def _plan_to_dict(plan: PlanResult, name: str, cells: dict[str, Cell]) -> dict:
    assign_map = {a.cell_id: a for a in plan.assignments}

    # Count modified cells
    modified_cells = 0
    cell_details = []

    for cid in sorted(cells.keys()):
        c = cells[cid]
        a = assign_map.get(cid)
        if not a:
            continue

        bcch_before = c.bcch
        bcch_after = a.bcch
        bcch_changed = (bcch_before != bcch_after)

        tch_before = list(c.tch_list)
        tch_after = list(a.tch_list)
        tch_changed = (tch_before != tch_after)

        is_changed = bcch_changed or tch_changed
        if is_changed:
            modified_cells += 1

        cell_details.append({
            "cell_id": c.cell_id,
            "station": c.station.name if c.station else "",
            "station_id": c.station.station_id if c.station else "",
            "sector": c.sector,
            "azimuth": c.azimuth,
            "lat": c.station.latitude if c.station else 0.0,
            "lon": c.station.longitude if c.station else 0.0,
            "bcch_before": bcch_before,
            "bcch_after": bcch_after,
            "bcch_changed": bcch_changed,
            "tch_before": tch_before,
            "tch_after": tch_after,
            "tch_changed": tch_changed,
            "is_changed": is_changed
        })

    violations_data = []
    for v in sorted(plan.violations, key=lambda x: x.weighted_cost, reverse=True):
        both = f"{v.channel_type_a}-{v.channel_type_b}"
        c_a = cells.get(v.cell_a_id)
        c_b = cells.get(v.cell_b_id)

        a_preexisting = False
        if c_a:
            if v.channel_type_a == "BCCH":
                a_preexisting = (c_a.bcch == v.freq_a)
            else:
                a_preexisting = (v.freq_a in c_a.tch_list)

        b_preexisting = False
        if c_b:
            if v.channel_type_b == "BCCH":
                b_preexisting = (c_b.bcch == v.freq_b)
            else:
                b_preexisting = (v.freq_b in c_b.tch_list)

        if plan.plan_id == 0 or name.startswith("Plan Actual"):
            is_inh = True
        else:
            is_inh = (a_preexisting and b_preexisting)
            if getattr(v, "is_inherited", False):
                is_inh = True

        lat1 = c_a.station.latitude if (c_a and c_a.station) else 0.0
        lon1 = c_a.station.longitude if (c_a and c_a.station) else 0.0
        lat2 = c_b.station.latitude if (c_b and c_b.station) else 0.0
        lon2 = c_b.station.longitude if (c_b and c_b.station) else 0.0

        violations_data.append({
            "type": "Cocanal" if v.violation_type == "cochannel" else "Adyacente",
            "raw_type": v.violation_type,
            "channel_types": both,
            "cell_a": v.cell_a_id,
            "cell_b": v.cell_b_id,
            "lat1": lat1,
            "lon1": lon1,
            "lat2": lat2,
            "lon2": lon2,
            "channel_type_a": v.channel_type_a,
            "channel_type_b": v.channel_type_b,
            "freq_a": v.freq_a,
            "freq_b": v.freq_b,
            "ho": v.ho_count if v.ho_count is not None else 0,
            "dist": int(v.distance_km) if v.distance_km.is_integer() else round(v.distance_km, 2),
            "cost": int(v.weighted_cost) if v.weighted_cost.is_integer() else round(v.weighted_cost, 1),
            "severity": "critical" if both == "BCCH-BCCH" else ("high" if "BCCH" in both else "medium"),
            "is_inherited": is_inh,
            "origin": "Preexistente" if is_inh else "Nueva Asignación"
        })

    channel_stats = _compute_channel_stats(plan, cells)

    inherited_cost = float(getattr(plan, "inherited_cost", 0.0))
    new_assignment_cost = float(getattr(plan, "new_assignment_cost", 0.0))

    if inherited_cost == 0.0 and new_assignment_cost == 0.0 and violations_data:
        for vd in violations_data:
            if vd["is_inherited"]:
                inherited_cost += float(vd["cost"])
            else:
                new_assignment_cost += float(vd["cost"])

    score_val = plan.score

    inherited_cost_fmt = int(inherited_cost) if inherited_cost.is_integer() else round(inherited_cost, 1)
    new_cost_fmt = int(new_assignment_cost) if new_assignment_cost.is_integer() else round(new_assignment_cost, 1)

    inherited_pct = round((inherited_cost / score_val * 100.0), 1) if score_val > 0 else 0.0
    new_pct = round((new_assignment_cost / score_val * 100.0), 1) if score_val > 0 else 0.0

    return {
        "id": plan.plan_id,
        "name": name,
        "score": int(plan.score) if plan.score.is_integer() else round(plan.score, 1),
        "inherited_cost": inherited_cost_fmt,
        "new_assignment_cost": new_cost_fmt,
        "inherited_pct": inherited_pct,
        "new_pct": new_pct,
        "critical_violations": plan.critical_violations,
        "total_violations": plan.total_violations,
        "cochannel_bcch_bcch": plan.cochannel_bcch_bcch,
        "cochannel_bcch_tch": plan.cochannel_bcch_tch,
        "cochannel_tch_tch": plan.cochannel_tch_tch,
        "adjacent_bcch_bcch": plan.adjacent_bcch_bcch,
        "adjacent_bcch_tch": plan.adjacent_bcch_tch,
        "adjacent_tch_tch": plan.adjacent_tch_tch,
        "cochannel_ho_weighted": int(plan.cochannel_ho_weighted) if plan.cochannel_ho_weighted.is_integer() else round(plan.cochannel_ho_weighted, 1),
        "adjacent_ho_weighted": int(plan.adjacent_ho_weighted) if plan.adjacent_ho_weighted.is_integer() else round(plan.adjacent_ho_weighted, 1),
        "same_station": plan.same_station_cochannel + plan.same_station_adjacent,
        "modified_cells": modified_cells,
        "total_cells": len(cells),
        "cells": cell_details,
        "violations": violations_data,
        "channel_stats": channel_stats
    }


def _build_payload(
    cells: dict[str, Cell],
    neighbor_graph: NeighborGraph,
    rules: FrequencyRules,
    baseline: PlanResult,
    scenarios: list[PlanResult],
    stat_info: dict,
    ho_stats: dict,
    now_str: str
) -> dict:
    baseline_dict = _plan_to_dict(baseline, "Plan Actual (Línea Base)", cells)

    scenarios_dicts = []
    for idx, sc in enumerate(scenarios, start=1):
        run_lbl = f" (Ejecución {sc.run_id})" if getattr(sc, "run_id", 0) > 0 else ""
        name = f"Escenario {idx}{run_lbl}" + (" (Recomendado)" if idx == 1 else "")
        sc_dict = _plan_to_dict(sc, name, cells)
        sc_dict["improvement_score"] = _calc_improvement(baseline.score, sc.score)
        sc_dict["improvement_critical"] = _calc_improvement(baseline.critical_violations, sc.critical_violations)
        sc_dict["improvement_violations"] = _calc_improvement(baseline.total_violations, sc.total_violations)
        elim_val = max(0.0, baseline.score - getattr(sc, "inherited_cost", sc.score))
        sc_dict["eliminated_cost"] = int(elim_val) if elim_val.is_integer() else round(elim_val, 1)
        scenarios_dicts.append(sc_dict)

    # Collect comprehensive set of all observed and configured channels
    all_channels_set = set(range(rules.freq_range_start, rules.freq_range_end + 1))
    if rules.available_frequencies:
        all_channels_set.update(rules.available_frequencies)
    all_channels_set.update(int(k) for k in baseline_dict["channel_stats"].keys())
    for sc in scenarios_dicts:
        all_channels_set.update(int(k) for k in sc["channel_stats"].keys())
    all_channels = sorted(all_channels_set)

    return {
        "metadata": {
            "title": "Informe de Planificación y Optimización de Frecuencias GSM 2G",
            "generated_at": now_str,
            "total_cells": len(cells),
            "total_stations": len(set(c.station.station_id for c in cells.values())),
            "total_relations": neighbor_graph.total_relations,
            "freq_range_start": rules.freq_range_start,
            "freq_range_end": rules.freq_range_end,
            "available_freqs": len(rules.available_frequencies),
            "excluded_freqs": rules.excluded_frequencies,
            "all_channels": all_channels,
            "stat_model": stat_info,
            "ho_stats": ho_stats,
            "population_size": rules.population_size,
            "generations": rules.num_generations,
            "num_runs": rules.num_runs,
            "has_coordinates": any(c.station and (c.station.latitude != 0.0 or c.station.longitude != 0.0) for c in cells.values()),
        },
        "baseline": baseline_dict,
        "scenarios": scenarios_dicts
    }


def _build_html_template(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")

    leaflet_dir = os.path.join(os.path.dirname(__file__), "..", "ui", "assets", "leaflet")
    css_file = os.path.join(leaflet_dir, "leaflet.css")
    js_file = os.path.join(leaflet_dir, "leaflet.js")

    if os.path.isfile(css_file) and os.path.isfile(js_file):
        try:
            with open(css_file, "r", encoding="utf-8") as f:
                leaflet_css = f"<style>{f.read()}</style>"
            with open(js_file, "r", encoding="utf-8") as f:
                leaflet_js = f"<script>{f.read()}</script>"
        except Exception:
            leaflet_css = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />'
            leaflet_js = '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    else:
        leaflet_css = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />'
        leaflet_js = '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'

    return f"""<!DOCTYPE html>
<html lang="es" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Freq Planner 2G — Dashboard Dinámico</title>
    {leaflet_css}
    {leaflet_js}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-body: #0c1017;
            --bg-surface: #141a24;
            --bg-card: #1c2432;
            --bg-card-hover: #232e3f;
            --border: #2b384e;
            --border-subtle: #1e2838;
            --text-main: #f0f4f8;
            --text-muted: #8b9bb4;
            --text-dim: #5c6b84;
            --primary: #00d4aa;
            --primary-glow: rgba(0, 212, 170, 0.2);
            --primary-dark: #00a884;
            --accent-blue: #38bdf8;
            --accent-purple: #a855f7;
            --accent-orange: #fb923c;
            --danger: #f43f5e;
            --warning: #f59e0b;
            --success: #10b981;
            --shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
        }}

        [data-theme="light"] {{
            --bg-body: #f8fafc;
            --bg-surface: #ffffff;
            --bg-card: #f1f5f9;
            --bg-card-hover: #e2e8f0;
            --border: #cbd5e1;
            --border-subtle: #e2e8f0;
            --text-main: #0f172a;
            --text-muted: #475569;
            --text-dim: #94a3b8;
            --primary: #0d9488;
            --primary-glow: rgba(13, 148, 136, 0.15);
            --primary-dark: #0f766e;
            --accent-blue: #0284c7;
            --accent-purple: #9333ea;
            --accent-orange: #ea580c;
            --danger: #e11d48;
            --warning: #d97706;
            --success: #059669;
            --shadow: 0 10px 25px -10px rgba(0, 0, 0, 0.1);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            transition: background-color 0.2s ease, border-color 0.2s ease;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-body);
            color: var(--text-main);
            line-height: 1.5;
            padding: 24px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        /* Header Navigation */
        header {{
            background: linear-gradient(135deg, var(--bg-surface) 0%, rgba(20, 26, 36, 0.8) 100%);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 24px 32px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: var(--shadow);
            backdrop-filter: blur(12px);
        }}

        .header-brand h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 26px;
            font-weight: 800;
            letter-spacing: -0.5px;
            background: linear-gradient(90deg, #38bdf8 0%, #00d4aa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .header-brand p {{
            color: var(--text-muted);
            font-size: 13px;
            margin-top: 4px;
        }}

        .header-actions {{
            display: flex;
            gap: 12px;
            align-items: center;
        }}

        .btn {{
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            font-weight: 600;
            padding: 10px 18px;
            border-radius: var(--radius-sm);
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text-main);
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            user-select: none;
        }}

        .btn:hover {{
            background: var(--bg-card-hover);
            transform: translateY(-1px);
        }}

        .btn-primary {{
            background: var(--primary);
            color: #0c1017;
            border-color: var(--primary);
            font-weight: 700;
        }}

        .btn-primary:hover {{
            background: var(--primary-dark);
            box-shadow: 0 0 15px var(--primary-glow);
        }}

        /* Scenario Selector Pills */
        .scenario-selector-wrap {{
            margin-bottom: 24px;
        }}

        .section-label {{
            font-family: 'Outfit', sans-serif;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-dim);
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .scenario-tabs {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 14px;
        }}

        .scenario-card {{
            background: var(--bg-surface);
            border: 2px solid var(--border);
            border-radius: var(--radius-md);
            padding: 18px;
            cursor: pointer;
            position: relative;
            overflow: hidden;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .scenario-card:hover {{
            border-color: var(--text-dim);
            transform: translateY(-2px);
        }}

        .scenario-card.active {{
            border-color: var(--primary);
            background: linear-gradient(180deg, rgba(0, 212, 170, 0.08) 0%, var(--bg-surface) 100%);
            box-shadow: 0 0 20px -5px var(--primary-glow);
        }}

        .scenario-card .badge-rec {{
            position: absolute;
            top: 12px;
            right: 12px;
            background: var(--primary);
            color: #0c1017;
            font-size: 10px;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 20px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .scenario-card .title {{
            font-family: 'Outfit', sans-serif;
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 6px;
            color: var(--text-main);
        }}

        .scenario-card .metric-score {{
            font-size: 24px;
            font-weight: 800;
            color: var(--primary);
            font-family: 'JetBrains Mono', monospace;
        }}

        .scenario-card.is-baseline .metric-score {{
            color: var(--text-muted);
        }}

        .scenario-card .stats-row {{
            display: flex;
            gap: 12px;
            margin-top: 10px;
            font-size: 12px;
            color: var(--text-muted);
        }}

        .scenario-card .stat-pill {{
            background: var(--bg-card);
            padding: 4px 8px;
            border-radius: 6px;
            font-family: 'JetBrains Mono', monospace;
        }}

        /* KPI Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}

        .kpi-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 18px;
            position: relative;
        }}

        .kpi-title {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 6px;
        }}

        .kpi-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 26px;
            font-weight: 800;
            color: var(--text-main);
        }}

        .kpi-change {{
            font-size: 12px;
            font-weight: 700;
            margin-top: 4px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}

        .change-good {{ color: var(--success); }}
        .change-bad {{ color: var(--danger); }}
        .change-neutral {{ color: var(--text-dim); }}

        /* Two-Column Main Content */
        .grid-2col {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-bottom: 24px;
        }}

        @media (max-width: 1024px) {{
            .grid-2col {{ grid-template-columns: 1fr; }}
        }}

        .panel {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 24px;
            box-shadow: var(--shadow);
        }}

        .panel-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-subtle);
        }}

        .panel-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 17px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        /* Comparison Table */
        .data-table-wrap {{
            overflow-x: auto;
            border-radius: var(--radius-sm);
            border: 1px solid var(--border-subtle);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}

        th {{
            background: var(--bg-card);
            color: var(--text-muted);
            padding: 12px 14px;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border);
        }}

        td {{
            padding: 12px 14px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-main);
        }}

        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        .font-mono {{
            font-family: 'JetBrains Mono', monospace;
        }}

        .badge-pill {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 700;
        }}

        .badge-danger {{ background: rgba(244, 63, 94, 0.15); color: var(--danger); border: 1px solid rgba(244, 63, 94, 0.3); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.3); }}
        .badge-success {{ background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }}
        .badge-info {{ background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); border: 1px solid rgba(56, 189, 248, 0.3); }}

        /* Filter Controls */
        .filter-bar {{
            display: flex;
            gap: 12px;
            margin-bottom: 14px;
            align-items: center;
            flex-wrap: wrap;
        }}

        .search-input {{
            flex: 1;
            min-width: 220px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 9px 14px;
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            outline: none;
        }}

        .search-input:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-glow);
        }}

        .toggle-label {{
            font-size: 13px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            user-select: none;
        }}

        .toggle-label input {{
            accent-color: var(--primary);
            cursor: pointer;
        }}

        /* Methodology Card */
        .methodology-box {{
            background: linear-gradient(135deg, rgba(0, 212, 170, 0.05) 0%, rgba(56, 189, 248, 0.05) 100%);
            border: 1px solid var(--border);
            border-left: 4px solid var(--primary);
            border-radius: var(--radius-md);
            padding: 20px;
            margin-bottom: 24px;
        }}

        .methodology-box h3 {{
            font-family: 'Outfit', sans-serif;
            font-size: 16px;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 8px;
        }}

        .formula-card {{
            background: var(--bg-card);
            padding: 10px 14px;
            border-radius: var(--radius-sm);
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: var(--accent-blue);
            margin: 8px 0;
            border: 1px solid var(--border);
        }}

        /* Channel Analytics Panel */
        .channel-controls-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 16px;
            background: var(--bg-card);
            padding: 10px 14px;
            border-radius: var(--radius-sm);
            border: 1px solid var(--border-subtle);
        }}

        .metric-pill-group {{
            display: inline-flex;
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 3px;
            gap: 2px;
        }}

        .metric-pill {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 16px;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .metric-pill:hover {{
            color: var(--text-main);
        }}

        .metric-pill.active {{
            color: #ffffff;
            font-weight: 700;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        }}

        .metric-pill.active[data-metric="total"] {{
            background: linear-gradient(135deg, #00d4aa 0%, #00a884 100%);
            color: #07251e;
        }}

        .metric-pill.active[data-metric="bcch"] {{
            background: linear-gradient(135deg, #a855f7 0%, #7e22ce 100%);
            color: #ffffff;
        }}

        .metric-pill.active[data-metric="tch"] {{
            background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
            color: #ffffff;
        }}

        .metric-pill.active[data-metric="violations"] {{
            background: linear-gradient(135deg, #f43f5e 0%, #be123c 100%);
            color: #ffffff;
        }}

        .select-styled {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: var(--radius-sm);
            font-size: 12px;
            font-weight: 500;
            outline: none;
            cursor: pointer;
        }}

        .select-styled:focus {{
            border-color: var(--primary);
        }}

        .chart-container-box {{
            position: relative;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-sm);
            padding: 16px 16px 8px 16px;
            min-height: 250px;
        }}

        .chart-canvas {{
            display: block;
            width: 100%;
            height: 230px;
            cursor: crosshair;
        }}

        .chart-tooltip {{
            position: absolute;
            display: none;
            pointer-events: none;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 10px 14px;
            font-size: 12px;
            color: #f8fafc;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            backdrop-filter: blur(8px);
            z-index: 1000;
            white-space: nowrap;
        }}

        .channel-kpis-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-top: 14px;
        }}

        .mini-kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-sm);
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .mini-kpi-title {{
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .mini-kpi-val {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 18px;
            font-weight: 700;
            color: var(--text-main);
        }}

        .channel-inspector-card {{
            margin-top: 14px;
            background: var(--bg-card);
            border: 1px solid var(--primary);
            border-radius: var(--radius-sm);
            padding: 14px 18px;
            display: none;
        }}

        .cost-breakdown-panel {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 18px 20px;
            margin-bottom: 24px;
        }}

        .cost-breakdown-bar {{
            display: flex;
            height: 18px;
            border-radius: 9px;
            overflow: hidden;
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            margin: 14px 0 16px 0;
        }}

        .cost-bar-inherited {{
            background: linear-gradient(90deg, #2563eb 0%, #06b6d4 100%);
            height: 100%;
            transition: width 0.4s ease;
        }}

        .cost-bar-new {{
            background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%);
            height: 100%;
            transition: width 0.4s ease;
        }}

        .cost-breakdown-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
        }}

        .cost-attr-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-sm);
            padding: 12px 14px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .cost-attr-title {{
            font-size: 11px;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .cost-attr-val {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 20px;
            font-weight: 800;
        }}

        .cost-attr-sub {{
            font-size: 11px;
            color: var(--text-muted);
            line-height: 1.4;
        }}

        /* Map Styles */
        #map {{
            height: 520px;
            width: 100%;
            border-radius: var(--radius-md);
            border: 1px solid var(--border);
            background: #0c1017;
            position: relative;
            z-index: 1;
        }}

        .leaflet-container {{
            background-color: #0c1017 !important;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        }}

        .map-controls-bar {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 12px 16px;
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            margin-bottom: 12px;
        }}

        .map-control-group {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .map-legend {{
            display: flex;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
            font-size: 11px;
            color: var(--text-muted);
            padding: 8px 14px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-sm);
            margin-top: 10px;
        }}

        .map-legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .legend-line-cochannel {{
            width: 18px;
            height: 3px;
            background: #ef4444;
            border-radius: 2px;
            display: inline-block;
        }}

        .legend-line-adjacent {{
            width: 18px;
            height: 3px;
            border-top: 3px dashed #f97316;
            display: inline-block;
        }}

        .legend-dot-modified {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #ff007f;
            box-shadow: 0 0 6px rgba(255, 0, 127, 0.6);
            display: inline-block;
        }}

        .legend-dot-isolated {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #38bdf8;
            box-shadow: 0 0 6px rgba(56, 189, 248, 0.6);
            display: inline-block;
        }}

        .sector-popup {{
            color: #1a1a2e;
            font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
            font-size: 12px;
            min-width: 210px;
        }}

        .sector-popup h3 {{
            margin: 2px 0 6px 0;
            font-size: 14px;
            color: #0f3460;
        }}

        .badge-modified {{
            display: inline-block;
            background: linear-gradient(135deg, #ff007f, #9b59b6);
            color: #ffffff;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 4px;
            margin-bottom: 6px;
            letter-spacing: 0.5px;
            box-shadow: 0 2px 4px rgba(255, 0, 127, 0.3);
        }}

        .conflict-tooltip {{
            background: #16213e !important;
            color: #e0e0e8 !important;
            border: 1px solid #00d4aa !important;
            border-radius: 4px !important;
            padding: 4px 8px !important;
            font-size: 11px !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4) !important;
        }}

        /* Print Style */
        @media print {{
            body {{
                background: #ffffff !important;
                color: #000000 !important;
                padding: 0 !important;
            }}
            header, .scenario-selector-wrap, .filter-bar, .header-actions {{
                display: none !important;
            }}
            .panel, .cost-breakdown-panel {{
                border: 1px solid #ccc !important;
                box-shadow: none !important;
                page-break-inside: avoid;
                margin-bottom: 20px;
            }}
            th {{
                background: #eee !important;
                color: #000 !important;
            }}
        }}
    </style>
</head>
<body>

<div class="container">
    <!-- Header -->
    <header>
        <div class="header-brand">
            <h1>📡 Freq Planner 2G <span>•</span> Dashboard Dinámico</h1>
            <p>Análisis Comparativo del Plan Actual vs Escenarios Calculados • Generado: <span id="meta-date"></span></p>
        </div>
        <div class="header-actions">
            <button class="btn" onclick="toggleTheme()">🌓 Modo Tema</button>
            <button class="btn" onclick="window.print()">🖨️ Imprimir / Guardar</button>
            <button class="btn btn-primary" onclick="exportActiveCsv()">📥 Exportar CSV</button>
        </div>
    </header>

    <!-- Methodology Overview -->
    <div class="methodology-box">
        <h3 id="stat-model-name">Modelo de Cálculo Estadístico</h3>
        <div class="formula-card" id="stat-model-formula">Fórmula</div>
        <p id="stat-model-desc" style="font-size: 13px; color: var(--text-muted); margin-bottom: 6px;"></p>
        <p id="stat-model-usage" style="font-size: 12px; color: var(--accent-orange);"></p>
    </div>

    <!-- Scenario Selector -->
    <div class="scenario-selector-wrap">
        <div class="section-label">
            <span>Selecciona un Escenario para Inspeccionar</span>
            <span id="label-cells-summary"></span>
        </div>
        <div class="scenario-tabs" id="scenario-tabs-container"></div>
    </div>

    <!-- Cost Decomposition Section (Task 4) -->
    <div class="cost-breakdown-panel" id="cost-breakdown-section">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
            <div>
                <h3 style="font-size: 16px; font-weight: 700; color: var(--text-main); margin-bottom: 2px;">
                    🎯 Descomposición y Atribución del Coste de Interferencia (<span id="cost-active-plan-title"></span>)
                </h3>
                <p style="font-size: 12px; color: var(--text-muted);">
                    Diferenciación exacta entre el coste preexistente heredado y el coste generado por las nuevas asignaciones.
                </p>
            </div>
            <div id="cost-breakdown-summary-badge" class="badge-pill badge-info"></div>
        </div>

        <!-- Stacked Visual Bar -->
        <div class="cost-breakdown-bar">
            <div id="cost-bar-seg-inherited" class="cost-bar-inherited" style="width: 50%;"></div>
            <div id="cost-bar-seg-new" class="cost-bar-new" style="width: 50%;"></div>
        </div>

        <!-- Metric Attribution Cards -->
        <div class="cost-breakdown-grid">
            <div class="cost-attr-card" style="border-left: 3px solid #06b6d4;">
                <span class="cost-attr-title">🟦 Coste Heredado (Preexistente)</span>
                <span class="cost-attr-val" id="cost-val-inherited" style="color: #38bdf8;">0</span>
                <span class="cost-attr-sub" id="cost-sub-inherited">Interferencias que ya existían en la red base porque las frecuencias no se modificaron.</span>
            </div>
            <div class="cost-attr-card" style="border-left: 3px solid #ec4899;">
                <span class="cost-attr-title">🟪 Coste por Nuevas Asignaciones</span>
                <span class="cost-attr-val" id="cost-val-new" style="color: #f472b6;">0</span>
                <span class="cost-attr-sub" id="cost-sub-new">Interferencias generadas específicamente por las frecuencias recién reasignadas.</span>
            </div>
            <div class="cost-attr-card" style="border-left: 3px solid #10b981;">
                <span class="cost-attr-title">🟢 Coste Original Eliminado</span>
                <span class="cost-attr-val" id="cost-val-eliminated" style="color: #34d399;">0</span>
                <span class="cost-attr-sub" id="cost-sub-eliminated">Reducción absoluta conseguida sobre las interferencias del plan original.</span>
            </div>
        </div>
    </div>

    <!-- Dynamic KPI Grid -->
    <div class="kpi-grid" id="kpi-grid-container"></div>

    <!-- Global Comparison Matrix Table -->
    <div class="panel" style="margin-bottom: 24px;">
        <div class="panel-header">
            <div class="panel-title">⚖️ Matriz Comparativa de Escenarios</div>
            <div style="font-size: 12px; color: var(--text-muted);">Línea Base vs Alternativas del Optimizador</div>
        </div>
        <div class="data-table-wrap">
            <table id="matrix-table">
                <thead>
                    <tr>
                        <th>Métrica de Red</th>
                        <th>Plan Actual</th>
                        <th id="th-sc1">Escenario 1</th>
                        <th id="th-sc2">Escenario 2</th>
                        <th id="th-sc3">Escenario 3</th>
                        <th>% Mejora (Mejor Escenario)</th>
                    </tr>
                </thead>
                <tbody id="matrix-tbody"></tbody>
            </table>
        </div>
    </div>

    <!-- Channel Usage & Coincidence Analytics (Interactive Bar Chart) -->
    <div class="panel" style="margin-bottom: 24px;">
        <div class="panel-header">
            <div class="panel-title">📊 Estadística Agregada y Coincidencias por Canal (ARFCN)</div>
            <div style="display: flex; gap: 8px; align-items: center;">
                <span class="badge-pill badge-info" id="channel-summary-badge">Rango de Canales</span>
                <button class="btn" style="padding: 4px 10px; font-size: 11px;" onclick="toggleChannelTable()">📋 Ver / Ocultar Tabla</button>
            </div>
        </div>

        <div class="channel-controls-bar">
            <!-- Metric selector pills -->
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">Métrica:</span>
                <div class="metric-pill-group">
                    <button class="metric-pill active" data-metric="total" onclick="setChannelMetric('total')">🔵 Coincidencias Totales</button>
                    <button class="metric-pill" data-metric="bcch" onclick="setChannelMetric('bcch')">🟣 Solo BCCH</button>
                    <button class="metric-pill" data-metric="tch" onclick="setChannelMetric('tch')">🔷 Solo TCH</button>
                    <button class="metric-pill" data-metric="violations" onclick="setChannelMetric('violations')">🔴 Conflictos / Coste</button>
                </div>
            </div>

            <!-- View mode & Filter selectors -->
            <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px; color: var(--text-muted);">Modo:</span>
                    <select id="channel-compare-mode" class="select-styled" onchange="setChannelCompareMode(this.value)">
                        <option value="single">Escenario Seleccionado</option>
                        <option value="compare">Comparar con Plan Actual (Línea Base)</option>
                    </select>
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px; color: var(--text-muted);">Filtro:</span>
                    <select id="channel-filter-mode" class="select-styled" onchange="setChannelFilter(this.value)">
                        <option value="all">Todos los Canales</option>
                        <option value="used">Solo Canales en Uso (>0)</option>
                        <option value="conflicts">Solo con Conflictos</option>
                        <option value="top10">Top 10 más Usados</option>
                    </select>
                </div>
            </div>
        </div>

        <!-- Canvas Chart Box -->
        <div class="chart-container-box" id="chart-wrapper">
            <canvas id="channel-canvas" class="chart-canvas"></canvas>
            <div id="chart-tooltip" class="chart-tooltip"></div>
        </div>

        <!-- Mini KPI Row -->
        <div class="channel-kpis-row">
            <div class="mini-kpi-card">
                <span class="mini-kpi-title">Canales en Uso</span>
                <span class="mini-kpi-val" id="mini-kpi-used">0</span>
            </div>
            <div class="mini-kpi-card">
                <span class="mini-kpi-title">Reuso Máximo (Pico)</span>
                <span class="mini-kpi-val" id="mini-kpi-peak" style="color: var(--accent-orange);">0</span>
            </div>
            <div class="mini-kpi-card">
                <span class="mini-kpi-title">Reuso Medio por Canal</span>
                <span class="mini-kpi-val" id="mini-kpi-avg" style="color: var(--primary);">0</span>
            </div>
            <div class="mini-kpi-card">
                <span class="mini-kpi-title">Canales Libres / Vacantes</span>
                <span class="mini-kpi-val" id="mini-kpi-free" style="color: var(--accent-blue);">0</span>
            </div>
        </div>

        <!-- Inspector Card (Click on channel) -->
        <div class="channel-inspector-card" id="channel-inspector">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h4 style="font-size: 14px; font-weight: 700; color: var(--primary);" id="inspector-title">Detalle del Canal</h4>
                <button class="btn" style="padding: 2px 8px; font-size: 11px;" onclick="closeChannelInspector()">✕ Cerrar</button>
            </div>
            <div id="inspector-content" style="font-size: 12px; color: var(--text-muted);"></div>
        </div>

        <!-- Collapsible Aggregate Table -->
        <div id="channel-table-collapse" style="display: none; margin-top: 16px;">
            <div class="data-table-wrap" style="max-height: 320px; overflow-y: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Canal (ARFCN)</th>
                            <th>Usos BCCH</th>
                            <th>Usos TCH</th>
                            <th>Coincidencias Totales</th>
                            <th>Conflictos Cocanal</th>
                            <th>Conflictos Adyacente</th>
                            <th>Coste Ponderado</th>
                            <th>Celdas Asignadas</th>
                        </tr>
                    </thead>
                    <tbody id="channel-table-tbody"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Interactive Network Map Panel -->
    <div class="panel" style="margin-bottom: 24px;" id="map-panel-container">
        <div class="panel-header">
            <div class="panel-title">🗺️ Mapa Geográfico de Sectores y Conflictos (<span id="map-active-scenario-title"></span>)</div>
            <div style="display: flex; gap: 8px; align-items: center;">
                <span class="badge-pill badge-info" id="map-summary-badge">Visualización interactiva</span>
                <button class="btn" style="padding: 4px 10px; font-size: 11px;" onclick="resetMapView()">🎯 Centrar Mapa</button>
            </div>
        </div>

        <div class="map-controls-bar">
            <!-- Selector de Celda -->
            <div class="map-control-group">
                <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">Celda:</span>
                <select id="map-cell-select" class="select-styled" onchange="onMapCellSelected(this.value)" style="min-width: 170px;">
                    <option value="">🔍 Todas las celdas</option>
                </select>
            </div>

            <!-- Canal / ARFCN -->
            <div class="map-control-group">
                <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">Canal:</span>
                <select id="map-channel-select" class="select-styled" onchange="onMapFilterChanged()">
                    <option value="-1">Todos los canales</option>
                </select>
            </div>

            <!-- Conflictos overlay -->
            <div class="map-control-group">
                <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">Líneas de Conflicto:</span>
                <select id="map-conflicts-select" class="select-styled" onchange="onMapFilterChanged()">
                    <option value="all">Todas las coincidencias</option>
                    <option value="cochannel">Solo Cocanalidades (Rojo)</option>
                    <option value="adjacent">Solo Adyacencias (Naranja)</option>
                    <option value="none">Ocultar líneas</option>
                </select>
            </div>

            <!-- Toggles / Checks -->
            <div class="map-control-group" style="gap: 14px;">
                <label class="toggle-label" title="Oculta líneas que conectan fuera de la vista visible">
                    <input type="checkbox" id="map-toggle-viewport" onchange="onMapViewportToggled(this.checked)" checked>
                    👁️ Vista actual
                </label>
                <label class="toggle-label" title="Muestra únicamente las celdas modificadas en este escenario">
                    <input type="checkbox" id="map-toggle-modified" onchange="onMapFilterChanged()">
                    🔄 Solo modificadas
                </label>
                <label class="toggle-label" title="Aísla la celda seleccionada sombreando el resto y colorea celdas en conflicto">
                    <input type="checkbox" id="map-toggle-isolate" onchange="onMapIsolateToggled(this.checked)">
                    🎯 Aislar celda
                </label>
            </div>
        </div>

        <div id="map"></div>

        <div class="map-legend">
            <span style="font-weight: 700; color: var(--text-main);">Leyenda:</span>
            <div class="map-legend-item">
                <span class="legend-line-cochannel"></span>
                <span>Cocanal (Rojo)</span>
            </div>
            <div class="map-legend-item">
                <span class="legend-line-adjacent"></span>
                <span>Adyacente (Naranja discontinua)</span>
            </div>
            <div class="map-legend-item">
                <span class="legend-dot-modified"></span>
                <span>Celda Modificada</span>
            </div>
            <div class="map-legend-item">
                <span class="legend-dot-isolated"></span>
                <span>Celda Aislada</span>
            </div>
            <div class="map-legend-item">
                <span>🎨 Color de sector: Distribución áurea según ARFCN asignado</span>
            </div>
        </div>
    </div>

    <!-- Cell Assignments Detail Table -->
    <div class="panel" style="margin-bottom: 24px;">
        <div class="panel-header">
            <div class="panel-title">📋 Detalle de Asignaciones por Celda (<span id="active-scenario-title"></span>)</div>
            <span class="badge-pill badge-info" id="active-changed-count">0 cambios</span>
        </div>

        <div class="filter-bar">
            <input type="text" id="search-cells" class="search-input" placeholder="🔍 Buscar por Cell ID, Estación o Sector..." oninput="renderCellsTable()">
            <label class="toggle-label">
                <input type="checkbox" id="toggle-only-changed" onchange="renderCellsTable()" checked>
                Mostrar solo celdas modificadas
            </label>
        </div>

        <div class="data-table-wrap" style="max-height: 480px; overflow-y: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Cell ID</th>
                        <th>Estación</th>
                        <th>Sector</th>
                        <th>BCCH Antes</th>
                        <th>BCCH Después</th>
                        <th>TCH Antes</th>
                        <th>TCH Después</th>
                        <th>Estado</th>
                    </tr>
                </thead>
                <tbody id="cells-tbody"></tbody>
            </table>
        </div>
    </div>

    <!-- Residual Violations Panel -->
    <div class="panel">
        <div class="panel-header">
            <div class="panel-title">⚠️ Relaciones Críticas y Violaciones Residuales (<span id="active-violations-title"></span>)</div>
            <span class="badge-pill badge-danger" id="active-violations-count">0 violaciones</span>
        </div>

        <div class="filter-bar">
            <input type="text" id="search-viol" class="search-input" placeholder="🔍 Filtrar por Celda A o Celda B..." oninput="renderViolationsTable()">
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="font-size: 12px; color: var(--text-muted);">Origen:</span>
                <select id="filter-viol-origin" class="select-styled" onchange="renderViolationsTable()">
                    <option value="all">Todo el Origen</option>
                    <option value="inherited">Solo Preexistente (Heredado)</option>
                    <option value="new">Solo Nuevas Asignaciones</option>
                </select>
            </div>
        </div>

        <div class="data-table-wrap" style="max-height: 380px; overflow-y: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Origen Coste</th>
                        <th>Tipo Conflicto</th>
                        <th>Celda A</th>
                        <th>Celda B</th>
                        <th>Freq A</th>
                        <th>Freq B</th>
                        <th>Handovers</th>
                        <th>Distancia</th>
                        <th>Coste Ponderado</th>
                        <th>Severidad</th>
                    </tr>
                </thead>
                <tbody id="violations-tbody"></tbody>
            </table>
        </div>
    </div>

</div>

<script>
    // Embedded Data Payload
    const data = {payload_json};

    let activeScenarioIndex = 0; // 0: Scenario 1, 1: Scenario 2, etc. (or -1 for baseline)

    function escapeHtml(str) {{
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }}

    function sanitizeCsv(val) {{
        let s = String(val ?? '');
        if (/^[=\\+\\-@\\t\\r]/.test(s)) {{
            s = "'" + s;
        }}
        return s;
    }}

    function fmtNum(val) {{
        if (val === undefined || val === null || val === '') return '—';
        const num = Number(val);
        if (isNaN(num)) return val;
        if (Number.isInteger(num)) return Math.trunc(num).toString();
        const s = num.toFixed(1);
        return s.endsWith('.0') ? s.slice(0, -2) : s;
    }}

    function init() {{
        document.getElementById('meta-date').innerText = data.metadata.generated_at;
        document.getElementById('label-cells-summary').innerText = 
            `${{data.metadata.total_cells}} celdas • ${{data.metadata.total_relations}} relaciones de vecindad • Rango ARFCN ${{data.metadata.freq_range_start}}-${{data.metadata.freq_range_end}}`;

        // Methodology
        const m = data.metadata.stat_model;
        document.getElementById('stat-model-name').innerText = `📐 Modelo Estadístico: ${{m.name}}`;
        document.getElementById('stat-model-formula').innerText = `Formulación: ${{m.formula}}`;
        document.getElementById('stat-model-desc').innerText = m.description;
        document.getElementById('stat-model-usage').innerText = `💡 Criterio de uso: ${{m.usage}}`;

        setupCanvasEvents();
        renderScenarioTabs();
        renderMatrixTable();
        initMap();
        selectScenario(0); // Select first scenario by default
    }}

    function renderScenarioTabs() {{
        const container = document.getElementById('scenario-tabs-container');
        container.innerHTML = '';

        // Baseline card
        const base = data.baseline;
        const baseCard = document.createElement('div');
        baseCard.className = `scenario-card is-baseline ${{activeScenarioIndex === -1 ? 'active' : ''}}`;
        baseCard.onclick = () => selectScenario(-1);
        baseCard.innerHTML = `
            <div class="title">🏛️ Plan Actual (Original)</div>
            <div class="metric-score">${{fmtNum(base.score)}}</div>
            <div class="stats-row">
                <span class="stat-pill">Críticas: ${{base.critical_violations}}</span>
                <span class="stat-pill">Total Viol: ${{base.total_violations}}</span>
            </div>
        `;
        container.appendChild(baseCard);

        // Scenarios cards
        data.scenarios.forEach((sc, idx) => {{
            const card = document.createElement('div');
            card.className = `scenario-card ${{activeScenarioIndex === idx ? 'active' : ''}}`;
            card.onclick = () => selectScenario(idx);

            const recBadge = idx === 0 ? '<span class="badge-rec">Recomendado</span>' : '';
            card.innerHTML = `
                ${{recBadge}}
                <div class="title">${{sc.name}}</div>
                <div class="metric-score">${{fmtNum(sc.score)}}</div>
                <div class="stats-row">
                    <span class="stat-pill">Críticas: ${{sc.critical_violations}}</span>
                    <span class="stat-pill">Total: ${{sc.total_violations}}</span>
                    <span class="stat-pill" style="color: var(--success); font-weight: bold;">${{sc.improvement_score}}</span>
                </div>
            `;
            container.appendChild(card);
        }});
    }}

    function selectScenario(index) {{
        activeScenarioIndex = index;
        renderScenarioTabs();

        const activePlan = index === -1 ? data.baseline : data.scenarios[index];
        const base = data.baseline;

        document.getElementById('active-scenario-title').innerText = activePlan.name;
        document.getElementById('active-changed-count').innerText = `${{activePlan.modified_cells}} de ${{activePlan.total_cells}} celdas modificadas`;
        document.getElementById('active-violations-title').innerText = activePlan.name;
        document.getElementById('active-violations-count').innerText = `${{activePlan.violations.length}} violaciones`;

        // Render KPI Cards
        renderKpis(activePlan, base);
        renderChannelAnalytics();

        // Render Map
        renderMap();

        // Render Tables
        renderCellsTable();
        renderViolationsTable();
    }}

    function renderKpis(plan, base) {{
        const container = document.getElementById('kpi-grid-container');

        // Update Cost Decomposition Component
        const decTitle = document.getElementById('cost-active-plan-title');
        if (decTitle) decTitle.innerText = plan.name;

        const inhVal = document.getElementById('cost-val-inherited');
        if (inhVal) inhVal.innerText = `${{fmtNum(plan.inherited_cost)}} (${{plan.inherited_pct}}%)`;

        const newVal = document.getElementById('cost-val-new');
        if (newVal) newVal.innerText = `${{fmtNum(plan.new_assignment_cost)}} (${{plan.new_pct}}%)`;

        const elimVal = document.getElementById('cost-val-eliminated');
        if (elimVal) {{
            const eliminated = activeScenarioIndex === -1 ? 0 : (plan.eliminated_cost || Math.max(0, base.score - plan.inherited_cost));
            const elimPct = base.score > 0 ? fmtNum((eliminated / base.score) * 100) : '0';
            elimVal.innerText = activeScenarioIndex === -1 ? '0 (Línea base)' : `-${{fmtNum(eliminated)}} (-${{elimPct}}%)`;
        }}

        const barInh = document.getElementById('cost-bar-seg-inherited');
        const barNew = document.getElementById('cost-bar-seg-new');
        if (barInh && barNew) {{
            const total = (plan.inherited_cost + plan.new_assignment_cost) || 1;
            const inhP = Math.max(0, Math.min(100, (plan.inherited_cost / total) * 100));
            const newP = Math.max(0, Math.min(100, (plan.new_assignment_cost / total) * 100));
            barInh.style.width = `${{inhP}}%`;
            barNew.style.width = `${{newP}}%`;
            barInh.title = `Coste Heredado: ${{fmtNum(plan.inherited_cost)}} (${{inhP.toFixed(1)}}%)`;
            barNew.title = `Coste Nuevas Asignaciones: ${{fmtNum(plan.new_assignment_cost)}} (${{newP.toFixed(1)}}%)`;
        }}

        const badgeSummary = document.getElementById('cost-breakdown-summary-badge');
        if (badgeSummary) {{
            badgeSummary.innerText = `${{plan.inherited_pct}}% Heredado • ${{plan.new_pct}}% Nuevas`;
        }}

        function diffHtml(newVal, oldVal, lowerIsBetter = true) {{
            if (activeScenarioIndex === -1) return '';
            const diff = oldVal - newVal;
            if (diff === 0) return '<div class="kpi-change change-neutral">Sin cambio</div>';
            const isGood = lowerIsBetter ? (diff > 0) : (diff < 0);
            const pct = oldVal > 0 ? fmtNum((Math.abs(diff) / oldVal) * 100) : '0';
            const sign = diff > 0 ? '-' : '+';
            return `<div class="kpi-change ${{isGood ? 'change-good' : 'change-bad'}}">${{sign}}${{Math.abs(diff)}} (${{pct}}% ${{isGood ? 'mejora' : 'degradación'}})</div>`;
        }}

        container.innerHTML = `
            <div class="kpi-card">
                <div class="kpi-title">Score Global (Coste)</div>
                <div class="kpi-value">${{fmtNum(plan.score)}}</div>
                ${{activeScenarioIndex !== -1 ? diffHtml(plan.score, base.score) : '<div class="kpi-change change-neutral">Línea base</div>'}}
            </div>
            <div class="kpi-card" style="border-top: 3px solid #06b6d4;">
                <div class="kpi-title">Coste Heredado (Preexistente)</div>
                <div class="kpi-value" style="color: #38bdf8;">${{fmtNum(plan.inherited_cost)}} <span style="font-size: 13px; font-weight: normal; color: var(--text-muted);">(${{plan.inherited_pct}}%)</span></div>
                <div class="kpi-change change-neutral">Coste original invariable</div>
            </div>
            <div class="kpi-card" style="border-top: 3px solid #ec4899;">
                <div class="kpi-title">Coste Nuevas Asignaciones</div>
                <div class="kpi-value" style="color: #f472b6;">${{fmtNum(plan.new_assignment_cost)}} <span style="font-size: 13px; font-weight: normal; color: var(--text-muted);">(${{plan.new_pct}}%)</span></div>
                <div class="kpi-change change-neutral">Generado por reasignación</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Violaciones Críticas (BCCH-BCCH)</div>
                <div class="kpi-value" style="color: ${{plan.critical_violations > 0 ? 'var(--danger)' : 'var(--success)'}};">${{plan.critical_violations}}</div>
                ${{activeScenarioIndex !== -1 ? diffHtml(plan.critical_violations, base.critical_violations) : '<div class="kpi-change change-neutral">Línea base</div>'}}
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Total Violaciones</div>
                <div class="kpi-value" style="color: ${{plan.total_violations > 0 ? 'var(--warning)' : 'var(--success)'}};">${{plan.total_violations}}</div>
                ${{activeScenarioIndex !== -1 ? diffHtml(plan.total_violations, base.total_violations) : '<div class="kpi-change change-neutral">Línea base</div>'}}
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Celdas Modificadas</div>
                <div class="kpi-value" style="color: var(--accent-orange);">${{plan.modified_cells}} <span style="font-size: 14px; font-weight: normal; color: var(--text-muted);">/ ${{plan.total_cells}}</span></div>
                <div class="kpi-change change-neutral">${{fmtNum((plan.modified_cells / plan.total_cells) * 100)}}% de la red</div>
            </div>
        `;
    }}

    function renderMatrixTable() {{
        const tbody = document.getElementById('matrix-tbody');
        const b = data.baseline;
        const s1 = data.scenarios[0] || b;
        const s2 = data.scenarios[1] || null;
        const s3 = data.scenarios[2] || null;

        const metrics = [
            {{ name: "Score Global (Coste Total)", b: fmtNum(b.score), s1: fmtNum(s1.score), s2: s2 ? fmtNum(s2.score) : "—", s3: s3 ? fmtNum(s3.score) : "—", imp: s1.improvement_score }},
            {{ name: "Coste Heredado (Preexistente)", b: fmtNum(b.inherited_cost), s1: `${{fmtNum(s1.inherited_cost)}} (${{s1.inherited_pct}}%)`, s2: s2 ? `${{fmtNum(s2.inherited_cost)}} (${{s2.inherited_pct}}%)` : "—", s3: s3 ? `${{fmtNum(s3.inherited_cost)}} (${{s3.inherited_pct}}%)` : "—", imp: "-" }},
            {{ name: "Coste por Nuevas Asignaciones", b: fmtNum(b.new_assignment_cost), s1: `${{fmtNum(s1.new_assignment_cost)}} (${{s1.new_pct}}%)`, s2: s2 ? `${{fmtNum(s2.new_assignment_cost)}} (${{s2.new_pct}}%)` : "—", s3: s3 ? `${{fmtNum(s3.new_assignment_cost)}} (${{s3.new_pct}}%)` : "—", imp: "-" }},
            {{ name: "Coste Original Eliminado", b: "0", s1: s1.eliminated_cost ? `-${{fmtNum(s1.eliminated_cost)}}` : "—", s2: s2 && s2.eliminated_cost ? `-${{fmtNum(s2.eliminated_cost)}}` : "—", s3: s3 && s3.eliminated_cost ? `-${{fmtNum(s3.eliminated_cost)}}` : "—", imp: "-" }},
            {{ name: "Violaciones Críticas (BCCH-BCCH)", b: b.critical_violations, s1: s1.critical_violations, s2: s2 ? s2.critical_violations : "—", s3: s3 ? s3.critical_violations : "—", imp: s1.improvement_critical }},
            {{ name: "Total Violaciones (Cocanal + Adj)", b: b.total_violations, s1: s1.total_violations, s2: s2 ? s2.total_violations : "—", s3: s3 ? s3.total_violations : "—", imp: s1.improvement_violations }},
            {{ name: "Cocanalidad BCCH-BCCH", b: b.cochannel_bcch_bcch, s1: s1.cochannel_bcch_bcch, s2: s2 ? s2.cochannel_bcch_bcch : "—", s3: s3 ? s3.cochannel_bcch_bcch : "—", imp: "-" }},
            {{ name: "Cocanalidad BCCH-TCH", b: b.cochannel_bcch_tch, s1: s1.cochannel_bcch_tch, s2: s2 ? s2.cochannel_bcch_tch : "—", s3: s3 ? s3.cochannel_bcch_tch : "—", imp: "-" }},
            {{ name: "Cocanalidad TCH-TCH", b: b.cochannel_tch_tch, s1: s1.cochannel_tch_tch, s2: s2 ? s2.cochannel_tch_tch : "—", s3: s3 ? s3.cochannel_tch_tch : "—", imp: "-" }},
            {{ name: "Adyacencia BCCH-BCCH", b: b.adjacent_bcch_bcch, s1: s1.adjacent_bcch_bcch, s2: s2 ? s2.adjacent_bcch_bcch : "—", s3: s3 ? s3.adjacent_bcch_bcch : "—", imp: "-" }},
            {{ name: "Adyacencia BCCH-TCH", b: b.adjacent_bcch_tch, s1: s1.adjacent_bcch_tch, s2: s2 ? s2.adjacent_bcch_tch : "—", s3: s3 ? s3.adjacent_bcch_tch : "—", imp: "-" }},
            {{ name: "Adyacencia TCH-TCH", b: b.adjacent_tch_tch, s1: s1.adjacent_tch_tch, s2: s2 ? s2.adjacent_tch_tch : "—", s3: s3 ? s3.adjacent_tch_tch : "—", imp: "-" }},
            {{ name: "Ponderación HO Cocanal", b: fmtNum(b.cochannel_ho_weighted), s1: fmtNum(s1.cochannel_ho_weighted), s2: s2 ? fmtNum(s2.cochannel_ho_weighted) : "—", s3: s3 ? fmtNum(s3.cochannel_ho_weighted) : "—", imp: "-" }},
            {{ name: "Ponderación HO Adyacente", b: fmtNum(b.adjacent_ho_weighted), s1: fmtNum(s1.adjacent_ho_weighted), s2: s2 ? fmtNum(s2.adjacent_ho_weighted) : "—", s3: s3 ? fmtNum(s3.adjacent_ho_weighted) : "—", imp: "-" }},
            {{ name: "Conflictos Misma Estación", b: b.same_station, s1: s1.same_station, s2: s2 ? s2.same_station : "—", s3: s3 ? s3.same_station : "—", imp: "-" }},
            {{ name: "Celdas Modificadas", b: "0", s1: `${{s1.modified_cells}} (${{fmtNum((s1.modified_cells/b.total_cells)*100)}}%)`, s2: s2 ? `${{s2.modified_cells}}` : "—", s3: s3 ? `${{s3.modified_cells}}` : "—", imp: "-" }}
        ];

        tbody.innerHTML = metrics.map(m => `
            <tr>
                <td style="font-weight: 600;">${{m.name}}</td>
                <td class="font-mono" style="color: var(--text-muted);">${{m.b}}</td>
                <td class="font-mono" style="color: var(--primary); font-weight: bold;">${{m.s1}}</td>
                <td class="font-mono">${{m.s2}}</td>
                <td class="font-mono">${{m.s3}}</td>
                <td class="font-mono" style="color: ${{m.imp.startsWith('+') ? 'var(--success)' : 'inherit'}}; font-weight: bold;">${{m.imp}}</td>
            </tr>
        `).join('');
    }}

    function renderCellsTable() {{
        const tbody = document.getElementById('cells-tbody');
        const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
        const search = document.getElementById('search-cells').value.toLowerCase().trim();
        const onlyChanged = document.getElementById('toggle-only-changed').checked;

        const filtered = plan.cells.filter(c => {{
            if (onlyChanged && !c.is_changed) return false;
            if (search) {{
                const text = `${{c.cell_id}} ${{c.station}} ${{c.station_id}} ${{c.sector}}`.toLowerCase();
                if (!text.includes(search)) return false;
            }}
            return true;
        }});

        if (filtered.length === 0) {{
            tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 24px;">No se encontraron celdas con los filtros actuales.</td></tr>`;
            return;
        }}

        const maxToRender = 1000;
        const toDisplay = filtered.slice(0, maxToRender);

        let rowsHtml = toDisplay.map(c => {{
            const bcchBefore = c.bcch_before !== null ? c.bcch_before : '—';
            const bcchAfter = c.bcch_after !== null ? c.bcch_after : '—';
            const bcchStyle = c.bcch_changed ? 'color: var(--danger); font-weight: bold;' : '';

            const tchBefore = c.tch_before.length ? c.tch_before.join(', ') : '—';
            const tchAfter = c.tch_after.length ? c.tch_after.join(', ') : '—';
            const tchStyle = c.tch_changed ? 'color: var(--accent-blue); font-weight: bold;' : '';

            const statusBadge = c.is_changed 
                ? '<span class="badge-pill badge-warning">✓ Modificado</span>'
                : '<span class="badge-pill" style="color: var(--text-dim);">Sin cambio</span>';

            return `
                <tr>
                    <td style="font-weight: 700; cursor: pointer; color: var(--primary);" onclick="focusCellOnMap('${{escapeHtml(c.cell_id)}}')" title="Centrar celda en el mapa">📡 ${{escapeHtml(c.cell_id)}}</td>
                    <td>${{escapeHtml(c.station)}}</td>
                    <td><span class="badge-pill" style="background: var(--bg-card);">${{escapeHtml(c.sector)}}</span></td>
                    <td class="font-mono">${{bcchBefore}}</td>
                    <td class="font-mono" style="${{bcchStyle}}">${{bcchAfter}}</td>
                    <td class="font-mono" style="font-size: 12px;">${{tchBefore}}</td>
                    <td class="font-mono" style="font-size: 12px; ${{tchStyle}}">${{tchAfter}}</td>
                    <td>${{statusBadge}}</td>
                </tr>
            `;
        }}).join('');

        if (filtered.length > maxToRender) {{
            rowsHtml += `
                <tr style="background: var(--bg-card);">
                    <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 10px; font-style: italic;">
                        ⚡ Mostrando las primeras ${{maxToRender}} de ${{filtered.length}} celdas cargadas. Usa el buscador superior para filtrar de forma instantánea.
                    </td>
                </tr>
            `;
        }}

        tbody.innerHTML = rowsHtml;
    }}

    function renderViolationsTable() {{
        const tbody = document.getElementById('violations-tbody');
        const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
        const search = document.getElementById('search-viol').value.toLowerCase().trim();
        const originFilter = document.getElementById('filter-viol-origin') ? document.getElementById('filter-viol-origin').value : 'all';

        const filtered = plan.violations.filter(v => {{
            if (originFilter === 'inherited' && !v.is_inherited) return false;
            if (originFilter === 'new' && v.is_inherited) return false;
            if (search) {{
                const text = `${{v.cell_a}} ${{v.cell_b}} ${{v.type}} ${{v.channel_types}} ${{v.origin}}`.toLowerCase();
                if (!text.includes(search)) return false;
            }}
            return true;
        }});

        if (filtered.length === 0) {{
            tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--success); font-weight: bold; padding: 24px;">✅ No hay violaciones registradas en este plan con los filtros seleccionados.</td></tr>`;
            return;
        }}

        const maxToRender = 1000;
        const toDisplay = filtered.slice(0, maxToRender);

        let violRowsHtml = toDisplay.map(v => {{
            let badgeClass = 'badge-info';
            if (v.severity === 'critical') badgeClass = 'badge-danger';
            else if (v.severity === 'high') badgeClass = 'badge-warning';

            const originBadge = v.is_inherited
                ? '<span class="badge-pill" style="background: #1e293b; color: #38bdf8; border: 1px solid #0284c7; font-size: 11px;">📌 Preexistente</span>'
                : '<span class="badge-pill" style="background: #2e1065; color: #f472b6; border: 1px solid #c026d3; font-size: 11px;">✨ Nueva Asignación</span>';

            return `
                <tr>
                    <td>${{originBadge}}</td>
                    <td><span class="badge-pill ${{badgeClass}}">${{escapeHtml(v.type)}} (${{escapeHtml(v.channel_types)}})</span></td>
                    <td style="font-weight: 700; cursor: pointer; color: var(--primary);" onclick="focusCellOnMap('${{escapeHtml(v.cell_a)}}')" title="Centrar celda en el mapa">${{escapeHtml(v.cell_a)}}</td>
                    <td style="font-weight: 700; cursor: pointer; color: var(--primary);" onclick="focusCellOnMap('${{escapeHtml(v.cell_b)}}')" title="Centrar celda en el mapa">${{escapeHtml(v.cell_b)}}</td>
                    <td class="font-mono">${{v.freq_a}}</td>
                    <td class="font-mono">${{v.freq_b}}</td>
                    <td class="font-mono">${{v.ho || '—'}}</td>
                    <td class="font-mono">${{v.dist}} km</td>
                    <td class="font-mono" style="font-weight: bold; color: var(--danger);">${{v.cost}}</td>
                    <td><span class="badge-pill ${{badgeClass}}">${{escapeHtml(v.severity.toUpperCase())}}</span></td>
                </tr>
            `;
        }}).join('');

        if (filtered.length > maxToRender) {{
            violRowsHtml += `
                <tr style="background: var(--bg-card);">
                    <td colspan="10" style="text-align: center; color: var(--text-muted); padding: 10px; font-style: italic;">
                        ⚡ Mostrando las primeras ${{maxToRender}} de ${{filtered.length}} violaciones. Usa el filtro superior para buscar celdas concretas.
                    </td>
                </tr>
            `;
        }}

        tbody.innerHTML = violRowsHtml;
    }}

    function toggleTheme() {{
        const html = document.documentElement;
        const current = html.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
    }}

    function exportActiveCsv() {{
        const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
        let csv = "Cell_ID;Station;Sector;BCCH_Antes;BCCH_Despues;TCH_Antes;TCH_Despues;Modificado\\n";

        plan.cells.forEach(c => {{
            const safeCell = sanitizeCsv(c.cell_id);
            const safeStation = sanitizeCsv(c.station);
            const safeSector = sanitizeCsv(c.sector);
            csv += `${{safeCell}};${{safeStation}};${{safeSector}};${{c.bcch_before || ''}};${{c.bcch_after || ''}};${{c.tch_before.join(',')}};${{c.tch_after.join(',')}};${{c.is_changed ? 'SI' : 'NO'}}\\n`;
        }});

        const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `plan_${{plan.name.replace(/[^a-zA-Z0-9]/g, '_')}}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }}

    // ── Channel Analytics Functions ──────────────────────────────
    let currentChannelMetric = 'total';
    let channelCompareMode = 'single';
    let channelFilterMode = 'all';
    let selectedChannelDetail = null;
    let channelBarsHitMap = [];

    function setChannelMetric(metric) {{
        currentChannelMetric = metric;
        document.querySelectorAll('.metric-pill').forEach(btn => {{
            btn.classList.toggle('active', btn.getAttribute('data-metric') === metric);
        }});
        renderChannelAnalytics();
    }}

    function setChannelCompareMode(mode) {{
        channelCompareMode = mode;
        renderChannelAnalytics();
    }}

    function setChannelFilter(mode) {{
        channelFilterMode = mode;
        renderChannelAnalytics();
    }}

    function toggleChannelTable() {{
        const wrap = document.getElementById('channel-table-collapse');
        wrap.style.display = (wrap.style.display === 'none') ? 'block' : 'none';
    }}

    function closeChannelInspector() {{
        document.getElementById('channel-inspector').style.display = 'none';
        selectedChannelDetail = null;
        renderChannelAnalytics();
    }}

    function showChannelDetail(ch) {{
        selectedChannelDetail = ch;
        const inspector = document.getElementById('channel-inspector');
        const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
        const stat = plan.channel_stats[String(ch)] || {{ bcch_count: 0, tch_count: 0, total_count: 0, total_conflicts: 0, conflict_cost: 0, cells: [] }};
        
        document.getElementById('inspector-title').innerText = `📡 ARFCN ${{ch}} — Detalle de Uso y Celdas Asignadas`;
        
        if (!stat.cells || stat.cells.length === 0) {{
            document.getElementById('inspector-content').innerHTML = `
                <p style="color: var(--text-dim); padding: 8px 0;">Este canal no se encuentra asignado a ninguna celda en el plan ${{plan.name}}.</p>
            `;
        }} else {{
            const cellsHtml = stat.cells.map(c => `
                <div style="background: var(--bg-surface); padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border-subtle); display: inline-flex; align-items: center; gap: 8px;">
                    <span style="font-weight: 700; color: var(--text-main);">${{escapeHtml(c.cell_id)}}</span>
                    <span style="color: var(--text-muted); font-size: 11px;">(${{escapeHtml(c.station || 'Estación')}} - Sec ${{escapeHtml(c.sector)}})</span>
                    <span class="badge-pill ${{c.role === 'BCCH' ? 'badge-info' : 'badge-warning'}}">${{escapeHtml(c.role)}}</span>
                </div>
            `).join(' ');

            document.getElementById('inspector-content').innerHTML = `
                <div style="margin-bottom: 8px; display: flex; gap: 16px; flex-wrap: wrap;">
                    <span><strong>Total Usos:</strong> ${{stat.total_count}} (BCCH: ${{stat.bcch_count}}, TCH: ${{stat.tch_count}})</span>
                    <span><strong>Conflictos:</strong> ${{stat.total_conflicts}}</span>
                    <span><strong>Coste Ponderado:</strong> ${{stat.conflict_cost}}</span>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 8px; max-height: 140px; overflow-y: auto; padding: 4px 0;">
                    ${{cellsHtml}}
                </div>
            `;
        }}
        inspector.style.display = 'block';
        renderChannelAnalytics();
    }}

    function _drawRoundRect(ctx, x, y, width, height, radius) {{
        if (width < 2 * radius) radius = width / 2;
        if (height < 2 * radius) radius = height / 2;
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.arcTo(x + width, y, x + width, y + height, radius);
        ctx.arcTo(x + width, y + height, x, y + height, 0);
        ctx.arcTo(x, y + height, x, y, 0);
        ctx.arcTo(x, y, x + width, y, radius);
        ctx.closePath();
    }}

    function renderChannelAnalytics() {{
        const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
        const base = data.baseline;
        const allChannels = data.metadata.all_channels || [];

        // Filter channels
        let channels = allChannels.slice();
        if (channelFilterMode === 'used') {{
            channels = channels.filter(ch => {{
                const s = plan.channel_stats[String(ch)];
                const b = base.channel_stats[String(ch)];
                return (s && s.total_count > 0) || (channelCompareMode === 'compare' && b && b.total_count > 0);
            }});
        }} else if (channelFilterMode === 'conflicts') {{
            channels = channels.filter(ch => {{
                const s = plan.channel_stats[String(ch)];
                const b = base.channel_stats[String(ch)];
                return (s && s.total_conflicts > 0) || (channelCompareMode === 'compare' && b && b.total_conflicts > 0);
            }});
        }} else if (channelFilterMode === 'top10') {{
            channels = channels.slice().sort((a, b) => {{
                const sa = plan.channel_stats[String(a)] ? plan.channel_stats[String(a)].total_count : 0;
                const sb = plan.channel_stats[String(b)] ? plan.channel_stats[String(b)].total_count : 0;
                return sb - sa;
            }}).slice(0, 10);
        }}

        // Summary Badge
        document.getElementById('channel-summary-badge').innerText = 
            `Mostrando ${{channels.length}} de ${{allChannels.length}} canales (${{plan.name}})`;

        // Mini KPIs
        let usedCount = 0;
        let peakReusage = 0;
        let peakChannel = null;
        let totalReusage = 0;

        allChannels.forEach(ch => {{
            const s = plan.channel_stats[String(ch)];
            const val = s ? s.total_count : 0;
            if (val > 0) {{
                usedCount++;
                totalReusage += val;
                if (val > peakReusage) {{
                    peakReusage = val;
                    peakChannel = ch;
                }}
            }}
        }});

        const freeCount = allChannels.length - usedCount;
        const avgReusage = usedCount > 0 ? fmtNum(totalReusage / usedCount) : '0';

        document.getElementById('mini-kpi-used').innerText = `${{usedCount}} / ${{allChannels.length}}`;
        document.getElementById('mini-kpi-peak').innerText = peakChannel ? `${{peakReusage}} celdas (ARFCN ${{peakChannel}})` : '0';
        document.getElementById('mini-kpi-avg').innerText = `${{avgReusage}} celdas/canal`;
        document.getElementById('mini-kpi-free').innerText = `${{freeCount}} libres`;

        // Canvas drawing
        const canvas = document.getElementById('channel-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        const width = rect.width || canvas.parentElement.clientWidth || 800;
        const height = 230;

        canvas.width = width * dpr;
        canvas.height = height * dpr;
        ctx.resetTransform();
        ctx.scale(dpr, dpr);

        channelBarsHitMap = [];

        if (channels.length === 0) {{
            ctx.fillStyle = '#64748b';
            ctx.font = '14px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('No hay canales que coincidan con los filtros seleccionados.', width / 2, height / 2);
            renderChannelTable(channels, plan);
            return;
        }}

        // Extract values function
        function getVal(p, ch) {{
            const s = p.channel_stats[String(ch)];
            if (!s) return 0;
            if (currentChannelMetric === 'total') return s.total_count;
            if (currentChannelMetric === 'bcch') return s.bcch_count;
            if (currentChannelMetric === 'tch') return s.tch_count;
            if (currentChannelMetric === 'violations') return s.total_conflicts;
            return 0;
        }}

        // Find max value
        let maxVal = 1;
        channels.forEach(ch => {{
            const v1 = getVal(plan, ch);
            if (v1 > maxVal) maxVal = v1;
            if (channelCompareMode === 'compare') {{
                const v2 = getVal(base, ch);
                if (v2 > maxVal) maxVal = v2;
            }}
        }});

        maxVal = Math.ceil(maxVal * 1.15);
        if (maxVal < 4) maxVal = 4;

        const padLeft = 35;
        const padRight = 15;
        const padTop = 15;
        const padBottom = 30;
        const chartW = width - padLeft - padRight;
        const chartH = height - padTop - padBottom;

        // Grid lines (3-4 horizontal lines)
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
        ctx.fillStyle = '#64748b';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'right';

        const gridSteps = 4;
        for (let i = 0; i <= gridSteps; i++) {{
            const yVal = Math.round((maxVal / gridSteps) * i);
            const yPos = padTop + chartH - (i / gridSteps) * chartH;
            ctx.beginPath();
            ctx.moveTo(padLeft, yPos);
            ctx.lineTo(padLeft + chartW, yPos);
            ctx.stroke();
            ctx.fillText(yVal, padLeft - 6, yPos + 3);
        }}

        // Palette per metric
        const colors = {{
            total: {{ c1: '#00d4aa', c2: '#00a884', glow: 'rgba(0, 212, 170, 0.3)' }},
            bcch: {{ c1: '#a855f7', c2: '#7e22ce', glow: 'rgba(168, 85, 247, 0.3)' }},
            tch: {{ c1: '#38bdf8', c2: '#0284c7', glow: 'rgba(56, 189, 248, 0.3)' }},
            violations: {{ c1: '#f43f5e', c2: '#be123c', glow: 'rgba(244, 63, 94, 0.3)' }}
        }}[currentChannelMetric];

        const slotW = chartW / channels.length;
        const isCompare = (channelCompareMode === 'compare' && activeScenarioIndex !== -1);

        channels.forEach((ch, idx) => {{
            const slotX = padLeft + idx * slotW;
            const valPlan = getVal(plan, ch);
            const valBase = getVal(base, ch);

            if (!isCompare) {{
                // Single bar
                const barW = Math.max(3, Math.min(24, slotW * 0.7));
                const barX = slotX + (slotW - barW) / 2;
                const barH = (valPlan / maxVal) * chartH;
                const barY = padTop + chartH - barH;

                if (barH > 0) {{
                    const grad = ctx.createLinearGradient(0, barY, 0, barY + barH);
                    grad.addColorStop(0, colors.c1);
                    grad.addColorStop(1, colors.c2);
                    ctx.fillStyle = grad;
                    _drawRoundRect(ctx, barX, barY, barW, barH, Math.min(3, barW / 2));
                    ctx.fill();

                    if (ch === selectedChannelDetail) {{
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 2;
                        ctx.stroke();
                    }}
                }}

                channelBarsHitMap.push({{
                    ch: ch,
                    x: slotX,
                    w: slotW,
                    valPlan: valPlan,
                    valBase: valBase,
                    isCompare: false
                }});

            }} else {{
                // Double grouped bar
                const barW = Math.max(2, Math.min(12, (slotW * 0.8) / 2));
                const barX1 = slotX + (slotW - barW * 2 - 2) / 2;
                const barX2 = barX1 + barW + 2;

                const barH1 = (valBase / maxVal) * chartH;
                const barY1 = padTop + chartH - barH1;
                const barH2 = (valPlan / maxVal) * chartH;
                const barY2 = padTop + chartH - barH2;

                // Base bar (Muted gray)
                if (barH1 > 0) {{
                    ctx.fillStyle = 'rgba(148, 163, 184, 0.4)';
                    _drawRoundRect(ctx, barX1, barY1, barW, barH1, Math.min(2, barW / 2));
                    ctx.fill();
                }}

                // Active bar (Vivid)
                if (barH2 > 0) {{
                    const grad = ctx.createLinearGradient(0, barY2, 0, barY2 + barH2);
                    grad.addColorStop(0, colors.c1);
                    grad.addColorStop(1, colors.c2);
                    ctx.fillStyle = grad;
                    _drawRoundRect(ctx, barX2, barY2, barW, barH2, Math.min(2, barW / 2));
                    ctx.fill();

                    if (ch === selectedChannelDetail) {{
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 2;
                        ctx.stroke();
                    }}
                }}

                channelBarsHitMap.push({{
                    ch: ch,
                    x: slotX,
                    w: slotW,
                    valPlan: valPlan,
                    valBase: valBase,
                    isCompare: true
                }});
            }}

            // X-Axis channel labels
            const showEvery = slotW < 16 ? (slotW < 8 ? 5 : 2) : 1;
            if (idx % showEvery === 0) {{
                ctx.fillStyle = (ch === selectedChannelDetail) ? 'var(--primary)' : '#94a3b8';
                ctx.font = (ch === selectedChannelDetail) ? 'bold 10px JetBrains Mono' : '9px JetBrains Mono';
                ctx.textAlign = 'center';
                ctx.fillText(ch, slotX + slotW / 2, height - 10);
            }}
        }});

        renderChannelTable(channels, plan);
    }}

    function setupCanvasEvents() {{
        const canvas = document.getElementById('channel-canvas');
        const tooltip = document.getElementById('chart-tooltip');
        if (!canvas || !tooltip) return;

        canvas.addEventListener('mousemove', (e) => {{
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;

            const hit = channelBarsHitMap.find(b => mouseX >= b.x && mouseX < b.x + b.w);
            if (!hit) {{
                tooltip.style.display = 'none';
                return;
            }}

            const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
            const stat = plan.channel_stats[String(hit.ch)] || {{ bcch_count: 0, tch_count: 0, total_count: 0, total_conflicts: 0 }};

            let diffText = '';
            if (hit.isCompare) {{
                const diff = hit.valPlan - hit.valBase;
                const sign = diff > 0 ? '+' : '';
                diffText = `<div style="font-size: 11px; margin-top: 4px; color: ${{diff <= 0 ? 'var(--success)' : 'var(--danger)'}};">Plan Actual: ${{hit.valBase}} → ${{plan.name}}: ${{hit.valPlan}} (${{sign}}${{diff}})</div>`;
            }}

            tooltip.innerHTML = `
                <div style="font-weight: 700; color: var(--primary); font-size: 13px; margin-bottom: 4px;">📡 ARFCN ${{hit.ch}}</div>
                <div><strong>Coincidencias:</strong> ${{stat.total_count}} (BCCH: ${{stat.bcch_count}}, TCH: ${{stat.tch_count}})</div>
                <div><strong>Conflictos:</strong> ${{stat.total_conflicts}}</div>
                ${{diffText}}
                <div style="font-size: 10px; color: var(--text-dim); margin-top: 4px;">👉 Haz clic para ver celdas asignadas</div>
            `;

            let left = mouseX + 16;
            if (left + 220 > rect.width) left = mouseX - 230;
            tooltip.style.left = `${{left}}px`;
            tooltip.style.top = `${{Math.max(10, mouseY - 50)}}px`;
            tooltip.style.display = 'block';
        }});

        canvas.addEventListener('mouseleave', () => {{
            tooltip.style.display = 'none';
        }});

        canvas.addEventListener('click', (e) => {{
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const hit = channelBarsHitMap.find(b => mouseX >= b.x && mouseX < b.x + b.w);
            if (hit) {{
                showChannelDetail(hit.ch);
            }}
        }});

        window.addEventListener('resize', () => {{
            renderChannelAnalytics();
            if (mapInstance) mapInstance.invalidateSize();
        }});
    }}

    function renderChannelTable(channels, plan) {{
        const tbody = document.getElementById('channel-table-tbody');
        if (!tbody) return;

        tbody.innerHTML = channels.map(ch => {{
            const s = plan.channel_stats[String(ch)] || {{ bcch_count: 0, tch_count: 0, total_count: 0, cochannel_conflicts: 0, adjacent_conflicts: 0, conflict_cost: 0, cells: [] }};
            const cellsList = s.cells.map(c => `${{escapeHtml(c.cell_id)}} (${{escapeHtml(c.role)}})`).join(', ') || '—';

            return `
                <tr>
                    <td class="font-mono" style="font-weight: 700; color: var(--primary); cursor: pointer;" onclick="showChannelDetail(${{ch}})">📡 ${{ch}}</td>
                    <td class="font-mono">${{s.bcch_count}}</td>
                    <td class="font-mono">${{s.tch_count}}</td>
                    <td class="font-mono" style="font-weight: bold; color: ${{s.total_count > 0 ? 'var(--text-main)' : 'var(--text-dim)'}};">${{s.total_count}}</td>
                    <td class="font-mono" style="color: ${{s.cochannel_conflicts > 0 ? 'var(--danger)' : 'inherit'}};">${{s.cochannel_conflicts}}</td>
                    <td class="font-mono" style="color: ${{s.adjacent_conflicts > 0 ? 'var(--warning)' : 'inherit'}};">${{s.adjacent_conflicts}}</td>
                    <td class="font-mono">${{s.conflict_cost}}</td>
                    <td style="font-size: 11px; max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${{cellsList}}</td>
                </tr>
            `;
        }}).join('');
    }}

    // ── Map Module ────────────────────────────────────────────────
    let mapInstance = null;
    let sectorsLayer = null;
    let conflictsLayer = null;
    let cellLayersMap = {{}};
    let currentMapConflicts = [];
    let focusedCellId = null;
    let viewportFiltering = true;
    let isolateCellMode = false;

    function _freqToColor(freq) {{
        if (!freq || freq <= 0) return '#8888a0';
        const hue = (freq * 137.508) % 360;
        return `hsl(${{Math.round(hue)}}, 85%, 55%)`;
    }}

    function getDestinationPoint(lat, lon, brng, distMeters) {{
        const R = 6371000;
        const rad = brng * Math.PI / 180;
        const lat1 = lat * Math.PI / 180;
        const lon1 = lon * Math.PI / 180;

        const lat2 = Math.asin(Math.sin(lat1) * Math.cos(distMeters / R) +
                     Math.cos(lat1) * Math.sin(distMeters / R) * Math.cos(rad));
        const lon2 = lon1 + Math.atan2(Math.sin(rad) * Math.sin(distMeters / R) * Math.cos(lat1),
                     Math.cos(distMeters / R) - Math.sin(lat1) * Math.sin(lat2));

        return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
    }}

    function initMap() {{
        const mapContainer = document.getElementById('map');
        if (!mapContainer || typeof L === 'undefined') return;

        const allCells = data.baseline ? data.baseline.cells : [];
        const validCoords = allCells.filter(c => (c.lat && c.lat !== 0) || (c.lon && c.lon !== 0));
        if (validCoords.length === 0) {{
            const panel = document.getElementById('map-panel-container');
            if (panel) {{
                panel.innerHTML = `
                    <div class="panel-header">
                        <div class="panel-title">🗺️ Mapa Geográfico de Red</div>
                    </div>
                    <div style="text-align: center; color: var(--text-dim); padding: 36px 16px;">
                        ℹ️ No se detectaron coordenadas geográficas (latitud/longitud) en las estaciones de esta red.
                    </div>
                `;
            }}
            return;
        }}

        const cellSelect = document.getElementById('map-cell-select');
        if (cellSelect) {{
            const sortedCells = [...validCoords].sort((a, b) => a.cell_id.localeCompare(b.cell_id));
            sortedCells.forEach(c => {{
                const opt = document.createElement('option');
                opt.value = c.cell_id;
                opt.textContent = `${{c.cell_id}} (${{c.station || ''}})`;
                cellSelect.appendChild(opt);
            }});
        }}

        const chanSelect = document.getElementById('map-channel-select');
        if (chanSelect && data.metadata && data.metadata.all_channels) {{
            data.metadata.all_channels.forEach(ch => {{
                const opt = document.createElement('option');
                opt.value = ch;
                opt.textContent = `ARFCN ${{ch}}`;
                chanSelect.appendChild(opt);
            }});
        }}

        let avgLat = 0, avgLon = 0;
        validCoords.forEach(c => {{ avgLat += c.lat; avgLon += c.lon; }});
        avgLat /= validCoords.length;
        avgLon /= validCoords.length;

        mapInstance = L.map('map', {{ preferCanvas: true }}).setView([avgLat, avgLon], 13);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors'
        }}).addTo(mapInstance);

        sectorsLayer = L.layerGroup().addTo(mapInstance);
        conflictsLayer = L.layerGroup().addTo(mapInstance);

        mapInstance.on('moveend', () => {{
            if (viewportFiltering && !isolateCellMode) {{
                renderMapConflicts();
            }}
        }});

        renderMap();
        resetMapView();
    }}

    function renderMap() {{
        if (!mapInstance || !sectorsLayer || !conflictsLayer) return;

        const plan = activeScenarioIndex === -1 ? data.baseline : data.scenarios[activeScenarioIndex];
        if (!plan) return;

        const titleEl = document.getElementById('map-active-scenario-title');
        if (titleEl) titleEl.innerText = plan.name;

        const chanSelect = document.getElementById('map-channel-select');
        const chanVal = chanSelect ? chanSelect.value : '-1';
        const targetFreq = parseInt(chanVal, 10);
        const conflictsSelect = document.getElementById('map-conflicts-select');
        const conflictMode = conflictsSelect ? conflictsSelect.value : 'all';
        const modCheck = document.getElementById('map-toggle-modified');
        const onlyModified = modCheck ? modCheck.checked : false;
        const isoCheck = document.getElementById('map-toggle-isolate');
        isolateCellMode = isoCheck ? isoCheck.checked : false;

        sectorsLayer.clearLayers();
        conflictsLayer.clearLayers();
        cellLayersMap = {{}};

        currentMapConflicts = [];
        const modifiedCids = new Set();
        plan.cells.forEach(c => {{
            if (c.is_changed) modifiedCids.add(c.cell_id);
        }});

        if (conflictMode !== 'none' && plan.violations) {{
            plan.violations.forEach(v => {{
                const rType = v.raw_type || (v.type === 'Cocanal' ? 'cochannel' : 'adjacent');
                if (conflictMode === 'cochannel' && rType !== 'cochannel') return;
                if (conflictMode === 'adjacent' && rType !== 'adjacent') return;
                if (!v.lat1 || !v.lon1 || !v.lat2 || !v.lon2) return;

                if (onlyModified && !modifiedCids.has(v.cell_a) && !modifiedCids.has(v.cell_b)) {{
                    return;
                }}

                if (targetFreq > 0 && v.freq_a !== targetFreq && v.freq_b !== targetFreq) {{
                    return;
                }}

                const lineText = `⚠️ ${{rType.toUpperCase()}}: ${{v.cell_a}} (${{v.channel_type_a || ''}} ${{v.freq_a}}) ↔ ${{v.cell_b}} (${{v.channel_type_b || ''}} ${{v.freq_b}}) | Dist: ${{v.dist}} km | Coste: ${{v.cost}}`;
                currentMapConflicts.push({{
                    lat1: v.lat1,
                    lon1: v.lon1,
                    lat2: v.lat2,
                    lon2: v.lon2,
                    cell_a_id: v.cell_a,
                    cell_b_id: v.cell_b,
                    type: rType,
                    text: lineText
                }});
            }});
        }}

        const conflictsWithFocused = {{}};
        if (isolateCellMode && focusedCellId && currentMapConflicts.length > 0) {{
            currentMapConflicts.forEach(cf => {{
                let other = null;
                if (cf.cell_a_id === focusedCellId) other = cf.cell_b_id;
                else if (cf.cell_b_id === focusedCellId) other = cf.cell_a_id;
                if (other) {{
                    if (cf.type === 'cochannel') {{
                        conflictsWithFocused[other] = 'cochannel';
                    }} else if (cf.type === 'adjacent' && conflictsWithFocused[other] !== 'cochannel') {{
                        conflictsWithFocused[other] = 'adjacent';
                    }}
                }}
            }});
        }}

        const validCells = plan.cells.filter(c => (c.lat && c.lat !== 0) || (c.lon && c.lon !== 0));
        validCells.forEach(c => {{
            const isMod = !!c.is_changed;
            if (onlyModified && !isMod) return;

            const bcch = c.bcch_after !== null && c.bcch_after !== undefined ? c.bcch_after : c.bcch_before;
            const tchList = c.tch_after && c.tch_after.length ? c.tch_after : c.tch_before;

            if (targetFreq > 0 && bcch !== targetFreq && (!tchList || !tchList.includes(targetFreq))) {{
                return;
            }}

            const isFocused = (focusedCellId && c.cell_id === focusedCellId);
            const conflictType = conflictsWithFocused[c.cell_id];

            let fillColor = _freqToColor(bcch);
            let strokeColor = isMod ? '#ff007f' : fillColor;
            let fillOpacity = isMod ? 0.75 : 0.5;
            let strokeWeight = isMod ? 3.5 : 2;

            if (isolateCellMode && focusedCellId) {{
                if (isFocused) {{
                    fillColor = '#38bdf8';
                    strokeColor = '#ffffff';
                    fillOpacity = 0.95;
                    strokeWeight = 4;
                }} else if (conflictType === 'cochannel') {{
                    fillColor = '#ef4444';
                    strokeColor = '#b91c1c';
                    fillOpacity = 0.85;
                    strokeWeight = 3;
                }} else if (conflictType === 'adjacent') {{
                    fillColor = '#f97316';
                    strokeColor = '#c2410c';
                    fillOpacity = 0.85;
                    strokeWeight = 3;
                }} else {{
                    fillColor = '#334155';
                    strokeColor = '#1e293b';
                    fillOpacity = 0.12;
                    strokeWeight = 1;
                }}
            }}

            const points = [[c.lat, c.lon]];
            const beamWidth = 60;
            const halfWidth = beamWidth / 2;
            const startAngle = (c.azimuth || 0) - halfWidth;
            const endAngle = (c.azimuth || 0) + halfWidth;
            const radiusMeters = 220;
            const step = (validCells.length > 500) ? 15 : 6;

            for (let a = startAngle; a <= endAngle; a += step) {{
                points.push(getDestinationPoint(c.lat, c.lon, a, radiusMeters));
            }}
            points.push([c.lat, c.lon]);

            const polygon = L.polygon(points, {{
                color: strokeColor,
                fillColor: fillColor,
                fillOpacity: fillOpacity,
                weight: strokeWeight,
                dashArray: (isMod && strokeColor === '#ff007f') ? '4, 4' : null
            }});

            let popupContent = `<div class="sector-popup">`;
            if (isMod) {{
                popupContent += `<div class="badge-modified">✨ Modificada</div><br/>`;
            }}
            popupContent += `<h3>📡 ${{escapeHtml(c.cell_id)}}</h3>` +
                `<b>Estación:</b> ${{escapeHtml(c.station || '')}}<br/>` +
                `<b>Sector:</b> ${{escapeHtml(c.sector || '')}} | <b>Azimut:</b> ${{c.azimuth || 0}}°<br/>`;

            if (isMod) {{
                const bcchB = c.bcch_before !== null ? c.bcch_before : '—';
                const bcchA = c.bcch_after !== null ? c.bcch_after : '—';
                const tchB = c.tch_before && c.tch_before.length ? c.tch_before.join(', ') : '—';
                const tchA = c.tch_after && c.tch_after.length ? c.tch_after.join(', ') : '—';
                popupContent += `<div style="margin-top:6px;padding:6px;background:#f4f5f8;border-left:3px solid #ff007f;border-radius:3px;">` +
                    `<b>BCCH:</b> ${{bcchB}} ➔ <b style="color:#ff007f;font-size:13px;">${{bcchA}}</b><br/>` +
                    `<b>TCH:</b> ${{tchB}} ➔ <b style="color:#008b8b;font-size:13px;">${{tchA}}</b>` +
                    `</div>`;
            }} else {{
                popupContent += `<b>BCCH:</b> <span style="font-weight:bold;color:#00b894;">${{bcch || '—'}}</span><br/>` +
                    `<b>TCH:</b> ${{tchList && tchList.length ? tchList.join(', ') : '—'}}`;
            }}
            popupContent += `</div>`;

            polygon.bindPopup(popupContent);
            sectorsLayer.addLayer(polygon);

            if (isMod) {{
                const stMarker = L.circleMarker([c.lat, c.lon], {{
                    radius: 5,
                    color: '#ffffff',
                    fillColor: '#ff007f',
                    fillOpacity: 1.0,
                    weight: 2
                }});
                stMarker.bindPopup(popupContent);
                sectorsLayer.addLayer(stMarker);
            }}

            cellLayersMap[c.cell_id] = {{
                polygon: polygon,
                lat: c.lat,
                lon: c.lon,
                data: c
            }};
        }});

        renderMapConflicts();
    }}

    function renderMapConflicts() {{
        if (!conflictsLayer || !mapInstance) return;
        conflictsLayer.clearLayers();
        if (!currentMapConflicts || currentMapConflicts.length === 0) return;

        const bounds = mapInstance.getBounds();
        currentMapConflicts.forEach(cf => {{
            const isLineForFocused = (focusedCellId && (cf.cell_a_id === focusedCellId || cf.cell_b_id === focusedCellId));
            if (isolateCellMode && focusedCellId) {{
                if (!isLineForFocused) return;
            }} else if (viewportFiltering && !isLineForFocused) {{
                const in1 = bounds.contains([cf.lat1, cf.lon1]);
                const in2 = bounds.contains([cf.lat2, cf.lon2]);
                if (!in1 || !in2) return;
            }}

            const isCo = (cf.type === 'cochannel');
            const color = isCo ? '#ef4444' : '#f97316';
            const line = L.polyline([[cf.lat1, cf.lon1], [cf.lat2, cf.lon2]], {{
                color: color,
                weight: isCo ? 3.5 : 2.5,
                opacity: 0.9,
                dashArray: isCo ? null : '6, 6'
            }});
            line.bindTooltip(escapeHtml(cf.text), {{ sticky: true, className: 'conflict-tooltip' }});
            conflictsLayer.addLayer(line);
        }});
    }}

    function onMapFilterChanged() {{
        renderMap();
    }}

    function onMapViewportToggled(checked) {{
        viewportFiltering = checked;
        renderMapConflicts();
    }}

    function onMapIsolateToggled(checked) {{
        isolateCellMode = checked;
        renderMap();
    }}

    function onMapCellSelected(cid) {{
        focusedCellId = cid || null;
        if (isolateCellMode) {{
            renderMap();
        }}
        if (cid && cellLayersMap[cid]) {{
            const item = cellLayersMap[cid];
            mapInstance.setView([item.lat, item.lon], 16, {{ animate: true }});
            setTimeout(() => {{ item.polygon.openPopup(); }}, 300);
        }} else if (!cid) {{
            resetMapView();
        }}
        renderMapConflicts();
    }}

    function resetMapView() {{
        if (!mapInstance) return;
        focusedCellId = null;
        const cellSelect = document.getElementById('map-cell-select');
        if (cellSelect) cellSelect.value = '';

        const b = L.latLngBounds();
        let count = 0;
        for (const k in cellLayersMap) {{
            b.extend([cellLayersMap[k].lat, cellLayersMap[k].lon]);
            count++;
        }}
        if (count > 0) {{
            mapInstance.fitBounds(b, {{ padding: [50, 50] }});
        }}
        renderMapConflicts();
    }}

    function focusCellOnMap(cellId) {{
        const cellSelect = document.getElementById('map-cell-select');
        if (cellSelect) cellSelect.value = cellId;
        onMapCellSelected(cellId);
        const mapContainer = document.getElementById('map-panel-container');
        if (mapContainer) {{
            mapContainer.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        }}
    }}

    // Auto-init on load
    window.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>
"""

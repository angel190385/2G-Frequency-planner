"""
Map widget displaying cell sector wedges ("quesitos") and frequency conflicts.
Uses Leaflet.js + OpenStreetMap in a PyQt6 QWebEngineView.
"""
from __future__ import annotations
import json
import math
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QGroupBox, QSplitter, QCheckBox
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings

from src.models.cell import Cell
from src.models.frequency_plan import PlanResult, FrequencyAssignment, format_number
from src.ui.styles import Colors


def _freq_to_color(freq: Optional[int]) -> str:
    """Generate a deterministic vibrant color for a frequency."""
    if freq is None or freq <= 0:
        return "#8888a0"
    hue = (freq * 137.508) % 360  # Golden ratio color distribution
    return f"hsl({int(hue)}, 85%, 55%)"


class MapWidget(QWidget):
    """
    Interactive map widget for visualizing 2G cell sectors and frequency conflicts.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells: dict[str, Cell] = {}
        self.results: list[PlanResult] = []
        self.neighbor_graph = None
        self.rules = None
        self.baseline_plan: Optional[PlanResult] = None
        self.current_plan_idx: int = -1  # -1 = Original, 0..N = Plan 1..N

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Controls header
        ctrl_box = QGroupBox("🗺️ Filtros y Controles del Mapa")
        ctrl_main_layout = QVBoxLayout(ctrl_box)
        ctrl_main_layout.setContentsMargins(8, 6, 8, 6)
        ctrl_main_layout.setSpacing(6)

        # Row 1: Primary Selectors
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        # Plan Selector
        row1.addWidget(QLabel("Plan:"))
        self.combo_plan = QComboBox()
        self.combo_plan.addItem("Original (Antes de calcular)", -1)
        self.combo_plan.currentIndexChanged.connect(self._on_filter_changed)
        row1.addWidget(self.combo_plan)

        # Channel / ARFCN Filter
        row1.addWidget(QLabel("Canal:"))
        self.combo_freq = QComboBox()
        self.combo_freq.addItem("Todos", -1)
        self.combo_freq.currentIndexChanged.connect(self._on_filter_changed)
        row1.addWidget(self.combo_freq)

        # Cell Selector (Requirement 1)
        row1.addWidget(QLabel("Celda:"))
        self.combo_cell = QComboBox()
        self.combo_cell.addItem("🔍 Todas las celdas", "")
        self.combo_cell.currentIndexChanged.connect(self._on_cell_selected)
        row1.addWidget(self.combo_cell)

        # Conflict Overlay Selector
        row1.addWidget(QLabel("Coincidencias:"))
        self.combo_conflicts = QComboBox()
        self.combo_conflicts.addItem("Todas", "all")
        self.combo_conflicts.addItem("Solo Cocanalidades (Rojo)", "cochannel")
        self.combo_conflicts.addItem("Solo Adyacencias (Naranja)", "adjacent")
        self.combo_conflicts.addItem("Ocultar líneas", "none")
        self.combo_conflicts.currentIndexChanged.connect(self._on_filter_changed)
        row1.addWidget(self.combo_conflicts)

        ctrl_main_layout.addLayout(row1)

        # Row 2: Dynamic Viewport & Layer Filters
        row2 = QHBoxLayout()
        row2.setSpacing(14)

        # Viewport Filtering Toggle
        self.chk_viewport_filter = QCheckBox("👁️ Filtrar líneas a la vista actual")
        self.chk_viewport_filter.setChecked(True)
        self.chk_viewport_filter.setToolTip(
            "Oculta automáticamente las líneas de conflicto que conectan con celdas fuera del encuadre actual del mapa"
        )
        self.chk_viewport_filter.toggled.connect(self._on_viewport_filter_toggled)
        row2.addWidget(self.chk_viewport_filter)

        # Only Modified Cells Toggle
        self.chk_only_modified = QCheckBox("🔄 Solo celdas modificadas")
        self.chk_only_modified.setChecked(False)
        self.chk_only_modified.setToolTip("Muestra únicamente las celdas cuyas frecuencias han sido modificadas por el optimizador")
        self.chk_only_modified.toggled.connect(self._on_filter_changed)
        row2.addWidget(self.chk_only_modified)

        # Isolate Cell Checkbox (Requirement 5)
        self.chk_isolate_cell = QCheckBox("🎯 Aislar celda y colorear conflictos")
        self.chk_isolate_cell.setChecked(False)
        self.chk_isolate_cell.setToolTip(
            "Al seleccionar una celda, aísla su visualización sombreando el resto del mapa, "
            "oculta líneas de conflicto ajenas y colorea las celdas en conflicto con ella (Rojo = Cocanal, Naranja = Adyacente)"
        )
        self.chk_isolate_cell.toggled.connect(self._on_isolate_cell_toggled)
        row2.addWidget(self.chk_isolate_cell)

        row2.addStretch()

        # Reset Map button
        btn_center = QPushButton("🎯 Centrar Mapa")
        btn_center.clicked.connect(self._reset_map_view)
        row2.addWidget(btn_center)

        ctrl_main_layout.addLayout(row2)
        layout.addWidget(ctrl_box)

        # WebEngine Map View with hardened security settings
        self.web_view = QWebEngineView()
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        layout.addWidget(self.web_view, 1)

        # Load initial empty map HTML
        self._load_html_template()

    def _load_html_template(self):
        """Generate and load the Leaflet HTML map document."""
        import os
        leaflet_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "leaflet")
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

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>2G Frequency Map</title>
    {leaflet_css}
    {leaflet_js}
    <style>
        html, body, #map {{
            height: 100%;
            width: 100%;
            margin: 0;
            padding: 0;
            background: #1a1a2e;
        }}
        .leaflet-container {{
            background-color: #1a1a2e !important;
        }}
        .sector-popup {{
            color: #1a1a2e;
            font-family: 'Segoe UI', Arial, sans-serif;
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
            background: #16213e;
            color: #e0e0e8;
            border: 1px solid #00d4aa;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 11px;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        // Use Canvas renderer for high-performance handling of thousands of sectors
        var map = L.map('map', {{ preferCanvas: true }}).setView([40.4168, -3.7038], 13);
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            maxZoom: 16,
            attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ'
        }}).addTo(map);

        var sectorsLayer = L.layerGroup().addTo(map);
        var conflictsLayer = L.layerGroup().addTo(map);
        window.totalCellsCount = 0;
        var cellLayers = {{}};
        var allConflicts = [];
        var viewportFiltering = true;
        var focusedCellId = null;

        function escapeHtml(str) {{
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }}

        // Helper: calculate destination point given distance & bearing
        function getDestinationPoint(lat, lon, brng, distMeters) {{
            var R = 6371000;
            var rad = brng * Math.PI / 180;
            var lat1 = lat * Math.PI / 180;
            var lon1 = lon * Math.PI / 180;

            var lat2 = Math.asin(Math.sin(lat1)*Math.cos(distMeters/R) +
                       Math.cos(lat1)*Math.sin(distMeters/R)*Math.cos(rad));
            var lon2 = lon1 + Math.atan2(Math.sin(rad)*Math.sin(distMeters/R)*Math.cos(lat1),
                              Math.cos(distMeters/R)-Math.sin(lat1)*Math.sin(lat2));

            return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
        }}

        // Draw sector wedge (quesito)
        function drawSector(lat, lon, azimuth, beamWidth, radiusMeters, fillColor, strokeColor, fillOpacity, strokeWeight, cellData) {{
            var points = [[lat, lon]];
            var halfWidth = beamWidth / 2;
            var startAngle = azimuth - halfWidth;
            var endAngle = azimuth + halfWidth;

            // Adaptive step angle for large scale networks (>500 cells)
            var step = (window.totalCellsCount > 500) ? 15 : 5;
            for (var a = startAngle; a <= endAngle; a += step) {{
                var pt = getDestinationPoint(lat, lon, a, radiusMeters);
                points.push(pt);
            }}
            points.push([lat, lon]);

            var isMod = !!cellData.is_modified;
            var polygon = L.polygon(points, {{
                color: strokeColor,
                fillColor: fillColor,
                fillOpacity: fillOpacity,
                weight: strokeWeight,
                dashArray: (isMod && strokeColor === '#ff007f') ? '4, 4' : null
            }});

            var popupContent = '<div class="sector-popup">';
            if (isMod) {{
                popupContent += '<div class="badge-modified">✨ Modificada</div><br/>';
            }}
            popupContent += '<h3>📡 ' + escapeHtml(cellData.cell_id) + '</h3>' +
                '<b>Estación:</b> ' + escapeHtml(cellData.station_name) + '<br/>' +
                '<b>Sector:</b> ' + escapeHtml(cellData.sector) + ' | <b>Azimut:</b> ' + cellData.azimuth + '°<br/>';

            if (isMod) {{
                popupContent += '<div style="margin-top:6px;padding:6px;background:#f4f5f8;border-left:3px solid #ff007f;border-radius:3px;">' +
                    '<b>BCCH:</b> ' + (cellData.orig_bcch || '—') + ' ➔ <b style="color:#ff007f;font-size:13px;">' + (cellData.bcch || '—') + '</b><br/>' +
                    '<b>TCH:</b> ' + (cellData.orig_tch ? cellData.orig_tch.join(', ') : '—') + ' ➔ <b style="color:#008b8b;font-size:13px;">' + (cellData.tch_list ? cellData.tch_list.join(', ') : '—') + '</b>' +
                    '</div>';
            }} else {{
                popupContent += '<b>BCCH:</b> <span style="font-weight:bold;color:#00b894;">' + (cellData.bcch || '—') + '</span><br/>' +
                    '<b>TCH:</b> ' + (cellData.tch_list ? cellData.tch_list.join(', ') : '—');
            }}
            popupContent += '</div>';

            polygon.bindPopup(popupContent);
            sectorsLayer.addLayer(polygon);

            if (isMod) {{
                var stationMarker = L.circleMarker([lat, lon], {{
                    radius: 5,
                    color: '#ffffff',
                    fillColor: '#ff007f',
                    fillOpacity: 1.0,
                    weight: 2
                }});
                stationMarker.bindPopup(popupContent);
                sectorsLayer.addLayer(stationMarker);
            }}

            cellLayers[cellData.cell_id] = {{
                polygon: polygon,
                lat: lat,
                lon: lon,
                data: cellData
            }};
        }}

        // Draw line connecting conflict cells
        function drawConflictLine(lat1, lon1, lat2, lon2, type, text) {{
            var isCo = (type === 'cochannel');
            var color = isCo ? '#ef4444' : '#f97316';
            var line = L.polyline([[lat1, lon1], [lat2, lon2]], {{
                color: color,
                weight: isCo ? 3.5 : 2.5,
                opacity: 0.9,
                dashArray: isCo ? null : '6, 6'
            }});

            line.bindTooltip(escapeHtml(text), {{sticky: true, className: 'conflict-tooltip'}});
            conflictsLayer.addLayer(line);
        }}

        function renderConflicts() {{
            conflictsLayer.clearLayers();
            if (!allConflicts || allConflicts.length === 0) return;

            var bounds = map.getBounds();
            allConflicts.forEach(function(cf) {{
                var isLineForFocused = (focusedCellId && (cf.cell_a_id === focusedCellId || cf.cell_b_id === focusedCellId));
                if (isolateCellMode && focusedCellId) {{
                    if (!isLineForFocused) {{
                        return; // Ocultar líneas que no sean de la celda aislada
                    }}
                }} else if (viewportFiltering && !isLineForFocused) {{
                    var in1 = bounds.contains([cf.lat1, cf.lon1]);
                    var in2 = bounds.contains([cf.lat2, cf.lon2]);
                    if (!in1 || !in2) {{
                        return; // Ocultar línea si alguna de las estaciones queda fuera de la vista
                    }}
                }}
                drawConflictLine(cf.lat1, cf.lon1, cf.lat2, cf.lon2, cf.type, cf.text);
            }});
        }}

        function setViewportFiltering(enabled) {{
            viewportFiltering = enabled;
            renderConflicts();
        }}

        function focusCell(cellId) {{
            focusedCellId = cellId || null;
            if (cellId && cellLayers[cellId]) {{
                var item = cellLayers[cellId];
                map.setView([item.lat, item.lon], 16, {{ animate: true }});
                setTimeout(function() {{
                    item.polygon.openPopup();
                }}, 300);
            }} else {{
                var b = L.latLngBounds();
                var count = 0;
                for (var k in cellLayers) {{
                    b.extend([cellLayers[k].lat, cellLayers[k].lon]);
                    count++;
                }}
                if (count > 0) {{
                    map.fitBounds(b, {{ padding: [50, 50] }});
                }}
            }}
            renderConflicts();
        }}

        map.on('moveend', function() {{
            if (viewportFiltering && !isolateCellMode) {{
                renderConflicts();
            }}
        }});

        function updateMapData(data) {{
            sectorsLayer.clearLayers();
            conflictsLayer.clearLayers();
            cellLayers = {{}};
            allConflicts = data.conflicts || [];
            viewportFiltering = (data.viewport_filter !== undefined) ? data.viewport_filter : true;
            isolateCellMode = !!data.isolate_cell;
            focusedCellId = data.focused_cell_id || null;
            window.totalCellsCount = (data.cells ? data.cells.length : 0);

            // Compute conflict relationship with focusedCellId
            var conflictsWithFocused = {{}}; // cell_id -> 'cochannel' | 'adjacent'
            if (isolateCellMode && focusedCellId && allConflicts) {{
                allConflicts.forEach(function(cf) {{
                    var other = null;
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

            var bounds = L.latLngBounds();

            // Render sector pie slices (quesitos)
            data.cells.forEach(function(c) {{
                var isFocused = (focusedCellId && c.cell_id === focusedCellId);
                var conflictType = conflictsWithFocused[c.cell_id];

                var fillColor = c.color;
                var strokeColor = c.is_modified ? '#ff007f' : c.color;
                var fillOpacity = c.is_modified ? 0.7 : 0.5;
                var strokeWeight = c.is_modified ? 3.5 : 2;

                if (isolateCellMode && focusedCellId) {{
                    if (isFocused) {{
                        fillColor = '#38bdf8'; // Bright cyan focused
                        strokeColor = '#ffffff';
                        fillOpacity = 0.95;
                        strokeWeight = 4;
                    }} else if (conflictType === 'cochannel') {{
                        fillColor = '#ef4444'; // Red for cochannel
                        strokeColor = '#b91c1c';
                        fillOpacity = 0.85;
                        strokeWeight = 3;
                    }} else if (conflictType === 'adjacent') {{
                        fillColor = '#f97316'; // Orange for adjacent
                        strokeColor = '#c2410c';
                        fillOpacity = 0.85;
                        strokeWeight = 3;
                    }} else {{
                        // Shaded / dimmed
                        fillColor = '#334155';
                        strokeColor = '#1e293b';
                        fillOpacity = 0.12;
                        strokeWeight = 1;
                    }}
                }}

                drawSector(c.lat, c.lon, c.azimuth, 60, 220, fillColor, strokeColor, fillOpacity, strokeWeight, c);
                bounds.extend([c.lat, c.lon]);
            }});

            // Render conflict lines
            renderConflicts();

            if (data.cells.length > 0 && !focusedCellId) {{
                map.fitBounds(bounds, {{padding: [50, 50]}});
            }} else if (focusedCellId && cellLayers[focusedCellId]) {{
                focusCell(focusedCellId);
            }}
        }}
    </script>
</body>
</html>"""
        self.web_view.setHtml(html)

    def load_data(
        self,
        cells: dict[str, Cell],
        results: list[PlanResult] = None,
        neighbor_graph: Optional[object] = None,
        rules: Optional[object] = None
    ):
        """Update map with loaded cells and plan results."""
        self.cells = cells
        self.results = results or []
        if neighbor_graph is not None:
            self.neighbor_graph = neighbor_graph
        if rules is not None:
            self.rules = rules
        self.baseline_plan = None

        # Populate plan combo box
        self.combo_plan.blockSignals(True)
        self.combo_plan.clear()
        self.combo_plan.addItem("Original (Antes de calcular)", None)

        per_run = getattr(self.results, "per_run", {})

        if per_run and len(per_run) > 1:
            for r in self.results:
                self.combo_plan.addItem(
                    f"🏆 Mejor Global - Plan {r.plan_id} (Score: {format_number(r.score)}, Ej. {r.run_id})", r
                )
            for run_idx in sorted(per_run.keys()):
                for r in per_run[run_idx]:
                    self.combo_plan.addItem(
                        f"Ejecución {run_idx} - Plan {r.plan_id} (Score: {format_number(r.score)})", r
                    )
        else:
            for r in self.results:
                self.combo_plan.addItem(f"Plan {r.plan_id} (Score: {format_number(r.score)})", r)

        self.combo_plan.blockSignals(False)

        # Populate cell combo box
        self.combo_cell.blockSignals(True)
        self.combo_cell.clear()
        self.combo_cell.addItem("🔍 Todas las celdas", "")
        for cid, cell in sorted(self.cells.items()):
            st_name = cell.station.name if cell.station else ""
            self.combo_cell.addItem(f"{cid} ({st_name})", cid)
        self.combo_cell.blockSignals(False)

        # Populate available frequencies combo box
        all_freqs = set()
        for cell in self.cells.values():
            if cell.bcch is not None:
                all_freqs.add(cell.bcch)
            all_freqs.update(cell.tch_list)
        for r in self.results:
            for assign in r.assignments:
                all_freqs.add(assign.bcch)
                all_freqs.update(assign.tch_list)

        self.combo_freq.blockSignals(True)
        self.combo_freq.clear()
        self.combo_freq.addItem("Todos los canales", -1)
        for f in sorted(all_freqs):
            self.combo_freq.addItem(f"ARFCN {f}", f)
        self.combo_freq.blockSignals(False)

        self._refresh_map()

    def _on_filter_changed(self):
        self._refresh_map()

    def _on_cell_selected(self):
        cid = self.combo_cell.currentData() or ""
        if self.chk_isolate_cell.isChecked():
            self._refresh_map()
        else:
            js_code = f"focusCell({json.dumps(cid)});"
            self.web_view.page().runJavaScript(js_code)

    def _on_isolate_cell_toggled(self, checked: bool):
        self._refresh_map()

    def _on_viewport_filter_toggled(self, checked: bool):
        js_code = f"setViewportFiltering({json.dumps(checked)});"
        self.web_view.page().runJavaScript(js_code)

    def _reset_map_view(self):
        self.combo_cell.blockSignals(True)
        self.combo_cell.setCurrentIndex(0)
        self.combo_cell.blockSignals(False)
        if self.chk_isolate_cell.isChecked():
            self._refresh_map()
        else:
            js_code = "focusCell('');"
            self.web_view.page().runJavaScript(js_code)

    def _refresh_map(self):
        """Build JSON payload and trigger JS map update."""
        if not self.cells:
            return

        selected_plan: Optional[PlanResult] = self.combo_plan.currentData()
        target_freq = self.combo_freq.currentData()
        conflict_mode = self.combo_conflicts.currentData()

        # Build current frequency assignments map
        assignments: dict[str, tuple[int, list[int]]] = {}

        if selected_plan is not None:
            plan_res = selected_plan
            for assign in plan_res.assignments:
                assignments[assign.cell_id] = (assign.bcch, assign.tch_list)
        else:
            # Baseline evaluation so original conflicts are visible
            if self.baseline_plan is None and self.cells:
                from src.models.neighbor import NeighborGraph
                from src.engine.constraints import FrequencyRules
                from src.io.html_exporter import generate_baseline_plan

                ng = self.neighbor_graph if self.neighbor_graph is not None else NeighborGraph()
                ru = self.rules if self.rules is not None else FrequencyRules()
                self.baseline_plan = generate_baseline_plan(self.cells, ng, ru)

            plan_res = self.baseline_plan
            for cid, cell in self.cells.items():
                assignments[cid] = (cell.bcch or 0, cell.tch_list)

        # Prepare cell sector objects for Leaflet
        cell_objs = []
        modified_cids = set()

        for cid, cell in sorted(self.cells.items()):
            bcch, tch_list = assignments.get(cid, (cell.bcch or 0, cell.tch_list))
            orig_bcch = cell.bcch
            orig_tch = sorted(cell.tch_list)
            cur_bcch = bcch
            cur_tch = sorted(tch_list)

            is_modified = bool(selected_plan is not None and ((orig_bcch != cur_bcch) or (orig_tch != cur_tch)))
            if is_modified:
                modified_cids.add(cid)

            # Filter if 'Solo celdas modificadas' is checked
            if self.chk_only_modified.isChecked() and not is_modified:
                continue

            # Filter by target frequency if selected
            if target_freq is not None and target_freq > 0:
                if bcch != target_freq and target_freq not in tch_list:
                    continue

            lat = cell.station.latitude if cell.station else 0.0
            lon = cell.station.longitude if cell.station else 0.0
            st_name = cell.station.name if cell.station else ""

            color = _freq_to_color(bcch)
            cell_objs.append({
                "cell_id": cell.cell_id,
                "station_name": st_name,
                "sector": cell.sector,
                "azimuth": cell.azimuth,
                "lat": lat,
                "lon": lon,
                "bcch": bcch,
                "tch_list": tch_list,
                "orig_bcch": orig_bcch,
                "orig_tch": orig_tch,
                "is_modified": is_modified,
                "color": color
            })

        # Prepare conflict line objects
        conflict_objs = []

        if conflict_mode != "none" and plan_res and plan_res.violations:
            for v in plan_res.violations:
                if conflict_mode == "cochannel" and v.violation_type != "cochannel":
                    continue
                if conflict_mode == "adjacent" and v.violation_type != "adjacent":
                    continue

                cell_a = self.cells.get(v.cell_a_id)
                cell_b = self.cells.get(v.cell_b_id)
                if not cell_a or not cell_b or not cell_a.station or not cell_b.station:
                    continue

                # If showing only modified cells, conflict must touch at least one modified cell
                if self.chk_only_modified.isChecked():
                    if cell_a.cell_id not in modified_cids and cell_b.cell_id not in modified_cids:
                        continue

                # Filter conflict lines if specific ARFCN filter is active
                if target_freq is not None and target_freq > 0:
                    if v.freq_a != target_freq and v.freq_b != target_freq:
                        continue

                text = (f"⚠️ {v.violation_type.upper()}: {v.cell_a_id} ({v.channel_type_a} {v.freq_a}) ↔ "
                        f"{v.cell_b_id} ({v.channel_type_b} {v.freq_b})")

                conflict_objs.append({
                    "lat1": cell_a.station.latitude,
                    "lon1": cell_a.station.longitude,
                    "lat2": cell_b.station.latitude,
                    "lon2": cell_b.station.longitude,
                    "cell_a_id": cell_a.cell_id,
                    "cell_b_id": cell_b.cell_id,
                    "type": v.violation_type,
                    "text": text
                })

        payload = {
            "cells": cell_objs,
            "conflicts": conflict_objs,
            "viewport_filter": self.chk_viewport_filter.isChecked(),
            "isolate_cell": self.chk_isolate_cell.isChecked(),
            "focused_cell_id": self.combo_cell.currentData() or None
        }

        js_code = f"updateMapData({json.dumps(payload)});"
        self.web_view.page().runJavaScript(js_code)

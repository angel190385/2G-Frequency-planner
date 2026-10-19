"""
Map widget displaying cell sector wedges ("quesitos"), frequency conflicts,
and interactive polygon drawing for sequential zone-based cluster optimization.
Uses Leaflet.js + OpenStreetMap in a PyQt6 QWebEngineView.
"""
from __future__ import annotations
import json
import math
import copy
from typing import Optional

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QGroupBox, QSplitter, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QDoubleSpinBox, QSpinBox,
    QGridLayout, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage

from src.models.cell import Cell
from src.models.zone import OptimizationZone, assign_cells_to_zones, ZONE_COLORS
from src.engine.constraints import FrequencyRules
from src.engine.cluster_optimizer import ClusterOptimizerThread, ZoneOptimizationProgress
from src.models.frequency_plan import PlanResult, FrequencyAssignment, format_number
from src.ui.dialogs.zone_config_dialog import ZoneConfigDialog
from src.ui.dialogs.cluster_progress_dialog import ClusterProgressDialog
from src.ui.styles import Colors


def _freq_to_color(freq: Optional[int]) -> str:
    """Generate a deterministic vibrant color for a frequency."""
    if freq is None or freq <= 0:
        return "#8888a0"
    hue = (freq * 137.508) % 360  # Golden ratio color distribution
    return f"hsl({int(hue)}, 85%, 55%)"


class CustomMapPage(QWebEnginePage):
    """Custom WebEnginePage that intercepts JS console messages."""
    def __init__(self, parent=None, console_handler=None):
        super().__init__(parent)
        self.console_handler = console_handler

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        if self.console_handler:
            self.console_handler(message)


class MapWidget(QWidget):
    """
    Interactive map widget for visualizing 2G cell sectors, frequency conflicts,
    and interactive polygon drawing for sequential zone optimization.
    """
    cluster_optimization_completed = pyqtSignal(dict, dict)  # (zone_results, updated_cells)
    zone_selection_applied = pyqtSignal()
    zones_changed = pyqtSignal(list)

    def __init__(self, mode: str = "full", parent=None):
        super().__init__(parent)
        self.mode = mode  # "zones", "results", or "full"
        self.cells: dict[str, Cell] = {}
        self.results: list[PlanResult] = []
        self.neighbor_graph = None
        self.rules = None
        self.baseline_plan: Optional[PlanResult] = None
        self.current_plan_idx: int = -1  # -1 = Original, 0..N = Plan 1..N

        # Cluster Zones state
        self.zones: list[OptimizationZone] = []
        self._is_drawing_zone = False
        self.cluster_thread: Optional[ClusterOptimizerThread] = None
        self.cluster_dialog: Optional[ClusterProgressDialog] = None

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(6)

        # Main horizontal splitter: Left = Map & filters, Right = Zones management panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── LEFT: Map Container ──────────────────────────────────────────
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # Initialize common controls
        self.combo_plan = QComboBox()
        self.combo_plan.addItem("Original (Antes de calcular)", -1)
        self.combo_plan.currentIndexChanged.connect(self._on_filter_changed)

        self.combo_freq = QComboBox()
        self.combo_freq.addItem("Todos", -1)
        self.combo_freq.currentIndexChanged.connect(self._on_filter_changed)

        self.combo_cell = QComboBox()
        self.combo_cell.addItem("🔍 Todas las celdas", "")
        self.combo_cell.currentIndexChanged.connect(self._on_cell_selected)

        self.combo_conflicts = QComboBox()
        self.combo_conflicts.addItem("Todas", "all")
        self.combo_conflicts.addItem("Solo Cocanalidades (Rojo)", "cochannel")
        self.combo_conflicts.addItem("Solo Adyacencias (Naranja)", "adjacent")
        self.combo_conflicts.addItem("Ocultar líneas", "none")
        self.combo_conflicts.currentIndexChanged.connect(self._on_filter_changed)

        self.chk_viewport_filter = QCheckBox("👁️ Filtrar líneas a la vista actual")
        self.chk_viewport_filter.setChecked(True)
        self.chk_viewport_filter.toggled.connect(self._on_viewport_filter_toggled)

        self.chk_only_modified = QCheckBox("🔄 Solo celdas modificadas")
        self.chk_only_modified.setChecked(False)
        self.chk_only_modified.toggled.connect(self._on_filter_changed)

        self.chk_isolate_cell = QCheckBox("🎯 Aislar celda y colorear conflictos")
        self.chk_isolate_cell.setChecked(False)
        self.chk_isolate_cell.toggled.connect(self._on_isolate_cell_toggled)

        btn_center = QPushButton("🎯 Centrar Mapa")
        btn_center.clicked.connect(self._reset_map_view)

        if self.mode == "zones":
            # Clean toolbar specifically tailored for defining clusters/zones
            ctrl_box = QGroupBox("🗺️ Controles del Mapa de Zonas")
            ctrl_layout = QHBoxLayout(ctrl_box)
            ctrl_layout.setContentsMargins(8, 6, 8, 6)
            ctrl_layout.setSpacing(10)

            ctrl_layout.addWidget(QLabel("Canal BCCH:"))
            ctrl_layout.addWidget(self.combo_freq)
            ctrl_layout.addWidget(QLabel("Buscar Celda:"))
            ctrl_layout.addWidget(self.combo_cell)
            ctrl_layout.addStretch()
            ctrl_layout.addWidget(btn_center)
            left_layout.addWidget(ctrl_box)

        else:
            # Full analytical controls for Results Map or Full Map
            ctrl_box = QGroupBox("🗺️ Filtros y Controles del Mapa")
            ctrl_main_layout = QVBoxLayout(ctrl_box)
            ctrl_main_layout.setContentsMargins(8, 6, 8, 6)
            ctrl_main_layout.setSpacing(6)

            # Row 1: Primary Selectors
            row1 = QHBoxLayout()
            row1.setSpacing(10)
            row1.addWidget(QLabel("Plan:"))
            row1.addWidget(self.combo_plan)
            row1.addWidget(QLabel("Canal:"))
            row1.addWidget(self.combo_freq)
            row1.addWidget(QLabel("Celda:"))
            row1.addWidget(self.combo_cell)
            row1.addWidget(QLabel("Coincidencias:"))
            row1.addWidget(self.combo_conflicts)
            ctrl_main_layout.addLayout(row1)

            # Row 2: Dynamic Viewport & Layer Filters
            row2 = QHBoxLayout()
            row2.setSpacing(14)
            row2.addWidget(self.chk_viewport_filter)
            row2.addWidget(self.chk_only_modified)
            row2.addWidget(self.chk_isolate_cell)
            row2.addStretch()
            row2.addWidget(btn_center)
            ctrl_main_layout.addLayout(row2)

            left_layout.addWidget(ctrl_box)

        # WebEngine Map View with custom page
        self.web_view = QWebEngineView()
        self.web_page = CustomMapPage(self.web_view, self._on_js_console_message)
        self.web_view.setPage(self.web_page)

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        left_layout.addWidget(self.web_view, 1)

        self.splitter.addWidget(left_widget)

        # ── RIGHT: Zones & Clusters Panel ────────────────────────────────
        if self.mode != "results":
            self.zone_panel = self._build_zone_panel()
            self.splitter.addWidget(self.zone_panel)
            self.splitter.setSizes([850, 370])
        else:
            self.zone_panel = None
            self.splitter.setSizes([1200, 0])
            self.splitter.setHandleWidth(0)

        main_layout.addWidget(self.splitter)

        # Load initial empty map HTML
        self._load_html_template()

    def _build_zone_panel(self) -> QWidget:
        """Build the side panel for managing and optimizing polygon zones."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 4, 4, 4)
        layout.setSpacing(8)

        # Header box
        box_hdr = QGroupBox("🎯 Zonas de Optimización (Polígonos)")
        box_hdr.setStyleSheet("QGroupBox { font-weight: bold; color: #00d4aa; }")
        l_hdr = QVBoxLayout(box_hdr)
        l_hdr.setSpacing(6)

        # Buttons toolbar
        row_tools = QHBoxLayout()
        self.btn_draw_zone = QPushButton("➕ Trazar Zona")
        self.btn_draw_zone.setStyleSheet("""
            QPushButton {
                background-color: #1e2b3e;
                color: #00d4aa;
                border: 1px solid #00d4aa;
                font-weight: bold;
                border-radius: 4px;
                padding: 5px 8px;
            }
            QPushButton:hover { background-color: #00d4aa; color: #1e272e; }
        """)
        self.btn_draw_zone.clicked.connect(self._toggle_draw_zone)
        row_tools.addWidget(self.btn_draw_zone)

        self.btn_cancel_draw = QPushButton("❌ Cancelar")
        self.btn_cancel_draw.setEnabled(False)
        self.btn_cancel_draw.clicked.connect(self._cancel_drawing_zone)
        row_tools.addWidget(self.btn_cancel_draw)

        self.btn_clear_zones = QPushButton("🧹 Borrar Zonas")
        self.btn_clear_zones.clicked.connect(self._clear_all_zones)
        row_tools.addWidget(self.btn_clear_zones)
        l_hdr.addLayout(row_tools)

        self.lbl_draw_hint = QLabel("💡 Haz clic en 'Trazar Zona', marca vértices en el mapa y haz doble clic para cerrar.")
        self.lbl_draw_hint.setStyleSheet("color: #8888a0; font-size: 11px;")
        self.lbl_draw_hint.setWordWrap(True)
        l_hdr.addWidget(self.lbl_draw_hint)

        layout.addWidget(box_hdr)

        # Zones table
        self.table_zones = QTableWidget()
        self.table_zones.setColumnCount(5)
        self.table_zones.setHorizontalHeaderLabels(["Zona", "Celdas", "BCCH", "TCH", "Acciones"])
        self.table_zones.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_zones.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_zones.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_zones.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_zones.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table_zones.setColumnWidth(4, 78)
        self.table_zones.verticalHeader().setVisible(False)
        self.table_zones.verticalHeader().setDefaultSectionSize(34)
        self.table_zones.setStyleSheet("""
            QTableWidget {
                background-color: #0f172a;
                border: 1px solid #334155;
                gridline-color: #1e293b;
                color: #e2e8f0;
            }
        """)
        layout.addWidget(self.table_zones, 1)

        # Optimization Configuration (Frontier, Runs, CPUs)
        cfg_box = QGroupBox("⚙️ Parámetros de Optimización")
        cfg_layout = QGridLayout(cfg_box)
        cfg_layout.setContentsMargins(8, 8, 8, 8)
        cfg_layout.setSpacing(6)

        # 1. Frontier protection distance
        cfg_layout.addWidget(QLabel("Distancia frontera:"), 0, 0)
        self.spin_frontier = QDoubleSpinBox()
        self.spin_frontier.setRange(1.0, 30.0)
        self.spin_frontier.setValue(8.0)
        self.spin_frontier.setSuffix(" km")
        self.spin_frontier.setToolTip("Radio de protección alrededor de la zona donde las celdas exteriores actúan como restricciones fijas")
        cfg_layout.addWidget(self.spin_frontier, 0, 1)

        # 2. Number of runs per zone
        cfg_layout.addWidget(QLabel("Pasadas / Runs:"), 1, 0)
        self.spin_runs = QSpinBox()
        self.spin_runs.setRange(1, 20)
        self.spin_runs.setValue(3)
        self.spin_runs.setToolTip("Número de ejecuciones independientes del algoritmo genético por cada zona")
        self.spin_runs.valueChanged.connect(self._on_runs_changed)
        cfg_layout.addWidget(self.spin_runs, 1, 1)

        # 3. CPU Cores / Parallel Workers
        cfg_layout.addWidget(QLabel("CPUs / Núcleos:"), 2, 0)
        self.spin_workers = QSpinBox()
        hw_cores = os.cpu_count() or 4
        self.spin_workers.setRange(1, max(32, hw_cores))
        self.spin_workers.setValue(hw_cores)
        self.spin_workers.setSuffix(f" / {hw_cores}")
        self.spin_workers.setToolTip("Número de procesos CPU concurrentes a utilizar en la optimización")
        cfg_layout.addWidget(self.spin_workers, 2, 1)

        layout.addWidget(cfg_box)

        # Apply selection to Calculation tab button
        self.btn_apply_to_calc = QPushButton("☑️ Aplicar a Tabla de Cálculo")
        self.btn_apply_to_calc.setToolTip("Aplica la selección de las zonas a la tabla y abre la pestaña 'Cálculo'")
        self.btn_apply_to_calc.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00d4aa, stop:1 #00b894);
                color: #0f172a;
                font-weight: bold;
                font-size: 13px;
                border-radius: 6px;
                padding: 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38ef7d, stop:1 #11998e);
            }
        """)
        self.btn_apply_to_calc.clicked.connect(self._apply_zones_to_calc_table)
        layout.addWidget(self.btn_apply_to_calc)

        return panel

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
        var map = L.map('map', {{ preferCanvas: true }}).setView([40.4168, -3.7038], 13);
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            maxZoom: 16,
            attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ'
        }}).addTo(map);

        var sectorsLayer = L.layerGroup().addTo(map);
        var conflictsLayer = L.layerGroup().addTo(map);
        var zonesLayer = L.layerGroup().addTo(map);

        window.totalCellsCount = 0;
        var cellLayers = {{}};
        var allConflicts = [];
        var viewportFiltering = true;
        var focusedCellId = null;

        // Polygon drawing state
        var isDrawingPolygon = false;
        var currentPolyPoints = [];
        var currentPolyMarkers = [];
        var currentPolyLine = null;

        function startDrawingPolygon() {{
            isDrawingPolygon = true;
            currentPolyPoints = [];
            currentPolyMarkers = [];
            if (currentPolyLine) {{
                map.removeLayer(currentPolyLine);
                currentPolyLine = null;
            }}
            map.getContainer().style.cursor = 'crosshair';
            map.doubleClickZoom.disable();
        }}

        function cancelDrawingPolygon() {{
            isDrawingPolygon = false;
            currentPolyPoints = [];
            currentPolyMarkers.forEach(function(m) {{ map.removeLayer(m); }});
            currentPolyMarkers = [];
            if (currentPolyLine) {{
                map.removeLayer(currentPolyLine);
                currentPolyLine = null;
            }}
            map.getContainer().style.cursor = '';
            map.doubleClickZoom.enable();
        }}

        map.on('click', function(e) {{
            if (!isDrawingPolygon) return;
            var lat = e.latlng.lat;
            var lon = e.latlng.lng;
            currentPolyPoints.push([lat, lon]);

            var marker = L.circleMarker([lat, lon], {{
                radius: 4,
                color: '#ff007f',
                fillColor: '#ffffff',
                fillOpacity: 1
            }}).addTo(map);
            currentPolyMarkers.push(marker);

            if (currentPolyPoints.length > 1) {{
                if (currentPolyLine) map.removeLayer(currentPolyLine);
                currentPolyLine = L.polyline(currentPolyPoints, {{
                    color: '#ff007f',
                    weight: 2,
                    dashArray: '4, 4'
                }}).addTo(map);
            }}
        }});

        map.on('dblclick', function(e) {{
            if (!isDrawingPolygon) return;
            if (currentPolyPoints.length >= 3) {{
                var finalCoords = currentPolyPoints.slice();
                cancelDrawingPolygon();
                console.log("PY_POLYGON:" + JSON.stringify(finalCoords));
            }}
        }});

        function renderZones(zonesData) {{
            zonesLayer.clearLayers();
            if (!zonesData || zonesData.length === 0) return;

            zonesData.forEach(function(z) {{
                if (!z.coords || z.coords.length < 3) return;
                var poly = L.polygon(z.coords, {{
                    color: z.color || '#00d4aa',
                    weight: 3,
                    fillColor: z.color || '#00d4aa',
                    fillOpacity: 0.20,
                    dashArray: '5, 5'
                }});
                var tooltip = '<b>📍 ' + escapeHtml(z.name) + '</b><br/>' +
                              'Celdas: <b>' + (z.cell_count || 0) + '</b><br/>' +
                              'Orden: #' + (z.order || 1);
                poly.bindTooltip(tooltip, {{ sticky: true, className: 'conflict-tooltip' }});
                zonesLayer.addLayer(poly);
            }});
        }}

        function escapeHtml(str) {{
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }}

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

        function drawSector(lat, lon, azimuth, beamWidth, radiusMeters, fillColor, strokeColor, fillOpacity, strokeWeight, cellData) {{
            var points = [[lat, lon]];
            var halfWidth = beamWidth / 2;
            var startAngle = azimuth - halfWidth;
            var endAngle = azimuth + halfWidth;

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
            if (cellData.zone_name) {{
                popupContent += '<div style="margin-bottom:5px;"><span style="background:' + escapeHtml(cellData.zone_color || '#00d4aa') + ';color:#0f172a;font-size:10px;font-weight:bold;padding:2px 6px;border-radius:3px;">📍 ' + escapeHtml(cellData.zone_name) + '</span></div>';
            }}
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

            cellLayers[cellData.cell_id] = {{
                polygon: polygon,
                lat: lat,
                lon: lon,
                data: cellData
            }};
        }}

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
                    if (!isLineForFocused) return;
                }} else if (viewportFiltering && !isLineForFocused) {{
                    var in1 = bounds.contains([cf.lat1, cf.lon1]);
                    var in2 = bounds.contains([cf.lat2, cf.lon2]);
                    if (!in1 || !in2) return;
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

            var conflictsWithFocused = {{}};
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

            data.cells.forEach(function(c) {{
                var isFocused = (focusedCellId && c.cell_id === focusedCellId);
                var conflictType = conflictsWithFocused[c.cell_id];

                var fillColor = c.color;
                var strokeColor = c.is_modified ? '#ff007f' : c.color;
                var fillOpacity = c.is_modified ? 0.7 : 0.5;
                var strokeWeight = c.is_modified ? 3.5 : 2;

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

                drawSector(c.lat, c.lon, c.azimuth, 60, 220, fillColor, strokeColor, fillOpacity, strokeWeight, c);
                bounds.extend([c.lat, c.lon]);
            }});

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

    def _on_js_console_message(self, message: str):
        """Handle console messages from Leaflet JS."""
        if message.startswith("PY_POLYGON:"):
            raw_json = message[len("PY_POLYGON:"):].strip()
            try:
                coords = json.loads(raw_json)
                self._on_polygon_drawn(coords)
            except Exception as e:
                print("Error parsing drawn polygon:", e)

    def _toggle_draw_zone(self):
        """Toggle polygon drawing mode on the map."""
        self._is_drawing_zone = True
        self.btn_draw_zone.setEnabled(False)
        self.btn_cancel_draw.setEnabled(True)
        self.lbl_draw_hint.setText("✏️ Modo trazado ACTIVO: Haz clic en el mapa para marcar vértices y DOBLE CLIC para cerrar.")
        self.lbl_draw_hint.setStyleSheet("color: #ff007f; font-weight: bold;")
        self.web_view.page().runJavaScript("startDrawingPolygon();")

    def _cancel_drawing_zone(self):
        """Cancel current drawing mode."""
        self._is_drawing_zone = False
        self.btn_draw_zone.setEnabled(True)
        self.btn_cancel_draw.setEnabled(False)
        self.lbl_draw_hint.setText("💡 Haz clic en 'Trazar Zona', marca vértices en el mapa y haz doble clic para cerrar.")
        self.lbl_draw_hint.setStyleSheet("color: #8888a0; font-size: 11px;")
        self.web_view.page().runJavaScript("cancelDrawingPolygon();")

    def _on_polygon_drawn(self, coords: list[list[float]]):
        """Called when a polygon is completed on the map."""
        self._cancel_drawing_zone()
        if len(coords) < 3:
            return

        poly_tuples = [(float(pt[0]), float(pt[1])) for pt in coords]

        zone_idx = len(self.zones) + 1
        zone_id = f"zona_{zone_idx}"
        name = f"Zona {zone_idx}"
        color = ZONE_COLORS[(zone_idx - 1) % len(ZONE_COLORS)]

        zone_rules = copy.deepcopy(self.rules) if self.rules is not None else FrequencyRules()
        if hasattr(self, "spin_runs"):
            zone_rules.num_runs = self.spin_runs.value()

        new_zone = OptimizationZone(
            zone_id=zone_id,
            name=name,
            polygon_coords=poly_tuples,
            color=color,
            execution_order=zone_idx,
            rules=zone_rules
        )
        self.zones.append(new_zone)

        # Enforce containment: smaller inner polygons take precedence
        assign_cells_to_zones(self.cells, self.zones)

        self._refresh_zones_on_map()
        self._update_zones_table()
        self._refresh_map()

    def _refresh_zones_on_map(self):
        """Send current zones to Leaflet for rendering."""
        zones_data = []
        for z in self.zones:
            zones_data.append({
                "id": z.zone_id,
                "name": z.name,
                "coords": z.polygon_coords,
                "color": z.color,
                "cell_count": z.cell_count,
                "order": z.execution_order
            })
        js_code = f"renderZones({json.dumps(zones_data)});"
        self.web_view.page().runJavaScript(js_code)
        self.zones_changed.emit(self.zones)

    def set_zones(self, zones: list[OptimizationZone]):
        """Set zones externally (e.g. synchronized from another map widget)."""
        self.zones = list(zones)
        if hasattr(self, "table_zones") and self.table_zones:
            assign_cells_to_zones(self.cells, self.zones)
            self._update_zones_table()
        zones_data = []
        for z in self.zones:
            zones_data.append({
                "id": z.zone_id,
                "name": z.name,
                "coords": z.polygon_coords,
                "color": z.color,
                "cell_count": z.cell_count,
                "order": z.execution_order
            })
        js_code = f"renderZones({json.dumps(zones_data)});"
        self.web_view.page().runJavaScript(js_code)

    def _update_zones_table(self):
        """Refresh the zones QTableWidget."""
        if not hasattr(self, "table_zones") or not self.table_zones:
            return
        self.table_zones.blockSignals(True)
        self.table_zones.setRowCount(len(self.zones))

        for row, z in enumerate(self.zones):
            # 1. Name with color badge
            item_name = QTableWidgetItem(f"● {z.name}")
            item_name.setForeground(Qt.GlobalColor.white)
            self.table_zones.setItem(row, 0, item_name)

            # 2. Cell count
            item_count = QTableWidgetItem(str(z.cell_count))
            item_count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_zones.setItem(row, 1, item_count)

            # 3. BCCH checkbox
            chk_b = QCheckBox()
            chk_b.setChecked(z.recalculate_bcch)
            chk_b.setToolTip(
                "Marcar para forzar el recálculo de TODOS los BCCH de la zona.\n"
                "Desmarcado: recalcula solo los BCCH fuera del rango permitido."
            )
            chk_b.toggled.connect(lambda chk, zone=z: setattr(zone, "recalculate_bcch", chk))
            w_b = QWidget()
            l_b = QHBoxLayout(w_b)
            l_b.setContentsMargins(0, 0, 0, 0)
            l_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l_b.addWidget(chk_b)
            self.table_zones.setCellWidget(row, 2, w_b)

            # 4. TCH checkbox
            chk_t = QCheckBox()
            chk_t.setChecked(z.recalculate_tch)
            chk_t.setToolTip(
                "Marcar para forzar el recálculo de TODOS los TCH de la zona.\n"
                "Desmarcado: recalcula solo los TCH fuera del rango permitido."
            )
            chk_t.toggled.connect(lambda chk, zone=z: setattr(zone, "recalculate_tch", chk))
            w_t = QWidget()
            l_t = QHBoxLayout(w_t)
            l_t.setContentsMargins(0, 0, 0, 0)
            l_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l_t.addWidget(chk_t)
            self.table_zones.setCellWidget(row, 3, w_t)

            # 5. Actions: Edit and Delete buttons
            w_act = QWidget()
            l_act = QHBoxLayout(w_act)
            l_act.setContentsMargins(2, 2, 2, 2)
            l_act.setSpacing(6)
            l_act.setAlignment(Qt.AlignmentFlag.AlignCenter)

            btn_cfg = QPushButton("⚙")
            btn_cfg.setToolTip("Configurar reglas y vecindades de esta zona")
            btn_cfg.setFixedSize(28, 28)
            btn_cfg.setStyleSheet("""
                QPushButton {
                    background-color: #1e293b;
                    color: #38bdf8;
                    border: 1px solid #0284c7;
                    border-radius: 4px;
                    padding: 0px;
                    margin: 0px;
                    font-size: 14px;
                    font-weight: bold;
                    min-height: 0px;
                    min-width: 0px;
                }
                QPushButton:hover {
                    background-color: #0284c7;
                    color: #ffffff;
                }
            """)
            btn_cfg.clicked.connect(lambda _, zone=z: self._open_zone_config(zone))
            l_act.addWidget(btn_cfg)

            btn_del = QPushButton("🗑")
            btn_del.setToolTip("Eliminar zona")
            btn_del.setFixedSize(28, 28)
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: #1e293b;
                    color: #f87171;
                    border: 1px solid #ef4444;
                    border-radius: 4px;
                    padding: 0px;
                    margin: 0px;
                    font-size: 14px;
                    font-weight: bold;
                    min-height: 0px;
                    min-width: 0px;
                }
                QPushButton:hover {
                    background-color: #ef4444;
                    color: #ffffff;
                }
            """)
            btn_del.clicked.connect(lambda _, zone_idx=row: self._delete_zone(zone_idx))
            l_act.addWidget(btn_del)

            self.table_zones.setCellWidget(row, 4, w_act)

        self.table_zones.blockSignals(False)

    def _open_zone_config(self, zone: OptimizationZone):
        """Open ZoneConfigDialog for editing a zone."""
        dlg = ZoneConfigDialog(zone, self)
        if dlg.exec():
            assign_cells_to_zones(self.cells, self.zones)
            self._refresh_zones_on_map()
            self._update_zones_table()

    def _delete_zone(self, idx: int):
        """Remove a zone and reassign remaining cells."""
        if 0 <= idx < len(self.zones):
            del self.zones[idx]
            # Re-index execution order
            for i, z in enumerate(self.zones, start=1):
                z.execution_order = i
            assign_cells_to_zones(self.cells, self.zones)
            self._refresh_zones_on_map()
            self._update_zones_table()
            self._refresh_map()

    def _clear_all_zones(self):
        """Remove all defined zones."""
        self.zones.clear()
        self._refresh_zones_on_map()
        self._update_zones_table()
        self._refresh_map()

    def _apply_zones_to_calc_table(self):
        """Apply the cells of the defined zones to the main Calculation table and open Calculation tab."""
        if not self.zones or sum(z.cell_count for z in self.zones) == 0:
            # No zones painted: clear sequential selection flag and open Calculation tab for single optimization
            self._selection_applied_to_calc = False
            self.zone_selection_applied.emit()
            return

        # Reset all cells
        for c in self.cells.values():
            c.recalculate_bcch = False
            c.tch_indices_to_recalculate = []
            c.new_tch_count = 0

        # Mark cells inside zones (defaulting to minimum change: only frequencies out of allowed pool)
        selected_count = 0
        for z in self.zones:
            active_rules = z.rules if (hasattr(z, "rules") and z.rules) else self.rules
            allowed_pool = set(active_rules.available_frequencies) if active_rules else set()

            for cid in z.cell_ids:
                if cid in self.cells:
                    c = self.cells[cid]
                    # BCCH: If z.recalculate_bcch is True, force all BCCHs in the zone to recalculate.
                    # Otherwise, only recalculate out-of-range BCCHs (or missing/invalid ones).
                    if z.recalculate_bcch:
                        c.recalculate_bcch = True
                    else:
                        c.recalculate_bcch = bool(c.bcch is None or c.bcch <= 0 or (allowed_pool and c.bcch not in allowed_pool))

                    # TCH: If z.recalculate_tch is True, force all TCHs in the zone to recalculate.
                    # Otherwise, only recalculate TCH indices with frequencies outside the allowed pool.
                    if z.recalculate_tch:
                        c.tch_indices_to_recalculate = list(range(len(c.tch_list)))
                    else:
                        if allowed_pool:
                            c.tch_indices_to_recalculate = [
                                i for i, f in enumerate(c.tch_list)
                                if f is None or f <= 0 or f not in allowed_pool
                            ]
                        else:
                            c.tch_indices_to_recalculate = []

                    c.new_tch_count = z.new_tch_count
                    selected_count += 1

        self._selection_applied_to_calc = True
        self.zone_selection_applied.emit()

    def _on_runs_changed(self, val: int):
        """Update default runs count across existing zones."""
        for z in self.zones:
            if hasattr(z, "rules") and z.rules:
                z.rules.num_runs = val

    def _run_cluster_optimization(self):
        """Launch the sequential cluster optimization."""
        if not self.zones:
            QMessageBox.warning(self, "Sin zonas", "Debes trazar al menos un polígono de zona en el mapa.")
            return

        total_cells_in_zones = sum(z.cell_count for z in self.zones)
        if total_cells_in_zones == 0:
            QMessageBox.warning(self, "Zonas vacías", "Ninguna de las zonas definidas contiene celdas.")
            return

        if hasattr(self, "btn_run_sequential"):
            self.btn_run_sequential.setEnabled(False)

        # Open progress dialog
        self.cluster_dialog = ClusterProgressDialog(total_zones=len(self.zones), parent=self)
        self.cluster_dialog.show()

        frontier_dist = self.spin_frontier.value()
        workers = self.spin_workers.value() if hasattr(self, "spin_workers") else None
        runs = self.spin_runs.value() if hasattr(self, "spin_runs") else 3

        # Ensure all zones have runs defined
        for z in self.zones:
            if hasattr(z, "rules") and z.rules:
                if not hasattr(z.rules, "num_runs") or z.rules.num_runs <= 0:
                    z.rules.num_runs = runs

        respect_flags = getattr(self, "_selection_applied_to_calc", False)
        self.cluster_thread = ClusterOptimizerThread(
            cells=self.cells,
            zones=self.zones,
            global_neighbor_graph=self.neighbor_graph,
            frontier_distance_km=frontier_dist,
            max_workers=workers,
            respect_cell_flags=respect_flags
        )

        self.cluster_thread.progress.connect(self.cluster_dialog.update_progress)
        self.cluster_thread.zone_completed.connect(self.cluster_dialog.log_zone_completed)
        self.cluster_thread.finished.connect(self._on_cluster_finished)
        self.cluster_thread.error.connect(self._on_cluster_error)
        self.cluster_dialog.cancelled.connect(self.cluster_thread.cancel)

        self.cluster_thread.start()

    def _on_cluster_finished(self, zone_results: dict, updated_cells: dict):
        """Handle cluster optimization completion."""
        if hasattr(self, "btn_run_sequential"):
            self.btn_run_sequential.setEnabled(True)
        if self.cluster_dialog:
            self.cluster_dialog.accept()

        self.cells = updated_cells
        self.cluster_optimization_completed.emit(zone_results, updated_cells)

        QMessageBox.information(
            self, "Optimización Finalizada",
            f"Se ha completado la optimización secuencial de {len(zone_results)} zonas.\n"
            "Las frecuencias han sido consolidadas en la red."
        )
        self._refresh_map()

    def _on_cluster_error(self, err_msg: str):
        """Handle error during cluster optimization."""
        if hasattr(self, "btn_run_sequential"):
            self.btn_run_sequential.setEnabled(True)
        if self.cluster_dialog:
            self.cluster_dialog.reject()
        QMessageBox.critical(self, "Error de Optimización", f"Ocurrió un error:\n{err_msg}")

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

        # Reassign zones if any exist
        if self.zones:
            assign_cells_to_zones(self.cells, self.zones)
            if hasattr(self, "table_zones") and self.table_zones:
                self._update_zones_table()
            self._refresh_zones_on_map()

        # Populate plan combo box
        self.combo_plan.blockSignals(True)
        self.combo_plan.clear()
        self.combo_plan.addItem("Original (Antes de calcular)", None)

        per_run = getattr(self.results, "per_run", {})

        if per_run and len(per_run) > 1:
            for run_id, run_plans in sorted(per_run.items()):
                best_of_run = min(run_plans, key=lambda r: r.score)
                diff = ""
                if self.baseline_plan:
                    base_sc = self.baseline_plan.score
                    if base_sc > 0:
                        pct = ((base_sc - best_of_run.score) / base_sc) * 100.0
                        diff = f" (-{pct:.1f}%)" if pct >= 0 else f" (+{abs(pct):.1f}%)"
                self.combo_plan.addItem(f"Ejecución {run_id} — Score: {format_number(best_of_run.score)}{diff}", best_of_run)
        elif self.results:
            for i, r in enumerate(self.results):
                diff = ""
                if self.baseline_plan:
                    base_sc = self.baseline_plan.score
                    if base_sc > 0:
                        pct = ((base_sc - r.score) / base_sc) * 100.0
                        diff = f" (-{pct:.1f}%)" if pct >= 0 else f" (+{abs(pct):.1f}%)"
                lbl = f"Plan {i + 1} — Score: {format_number(r.score)}{diff}"
                self.combo_plan.addItem(lbl, r)

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

        # Mapping for cell -> zone tag
        zone_by_cell = {}
        for z in self.zones:
            for cid in z.cell_ids:
                zone_by_cell[cid] = (z.name, z.color)

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

            if self.chk_only_modified.isChecked() and not is_modified:
                continue

            if target_freq is not None and target_freq > 0:
                if bcch != target_freq and target_freq not in tch_list:
                    continue

            lat = cell.station.latitude if cell.station else 0.0
            lon = cell.station.longitude if cell.station else 0.0
            st_name = cell.station.name if cell.station else ""

            color = _freq_to_color(bcch)
            z_name, z_color = zone_by_cell.get(cid, (None, None))

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
                "color": color,
                "zone_name": z_name,
                "zone_color": z_color
            })

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

                if self.chk_only_modified.isChecked():
                    if cell_a.cell_id not in modified_cids and cell_b.cell_id not in modified_cids:
                        continue

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

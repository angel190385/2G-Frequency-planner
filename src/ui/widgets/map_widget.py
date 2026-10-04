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
    QPushButton, QGroupBox, QSplitter
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView

from src.models.cell import Cell
from src.models.frequency_plan import PlanResult, FrequencyAssignment
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
        self.current_plan_idx: int = -1  # -1 = Original, 0..N = Plan 1..N

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Controls header
        ctrl_box = QGroupBox("🗺️ Filtros y Controles del Mapa")
        ctrl_layout = QHBoxLayout(ctrl_box)
        ctrl_layout.setContentsMargins(8, 6, 8, 6)
        ctrl_layout.setSpacing(12)

        # Plan Selector
        ctrl_layout.addWidget(QLabel("Plan:"))
        self.combo_plan = QComboBox()
        self.combo_plan.addItem("Original (Antes de calcular)", -1)
        self.combo_plan.currentIndexChanged.connect(self._on_filter_changed)
        ctrl_layout.addWidget(self.combo_plan)

        # Channel / ARFCN Filter
        ctrl_layout.addWidget(QLabel("Filtrar Canal:"))
        self.combo_freq = QComboBox()
        self.combo_freq.addItem("Todos los canales", -1)
        self.combo_freq.currentIndexChanged.connect(self._on_filter_changed)
        ctrl_layout.addWidget(self.combo_freq)

        # Conflict Overlay Selector
        ctrl_layout.addWidget(QLabel("Mostrar Coincidencias:"))
        self.combo_conflicts = QComboBox()
        self.combo_conflicts.addItem("Todas las coincidencias", "all")
        self.combo_conflicts.addItem("Solo Cocanalidades (Rojo)", "cochannel")
        self.combo_conflicts.addItem("Solo Adyacencias (Naranja)", "adjacent")
        self.combo_conflicts.addItem("Ocultar líneas de conflicto", "none")
        self.combo_conflicts.currentIndexChanged.connect(self._on_filter_changed)
        ctrl_layout.addWidget(self.combo_conflicts)

        ctrl_layout.addStretch()

        # Reset Map button
        btn_center = QPushButton("🎯 Centrar Mapa")
        btn_center.clicked.connect(self._reset_map_view)
        ctrl_layout.addWidget(btn_center)

        layout.addWidget(ctrl_box)

        # WebEngine Map View
        self.web_view = QWebEngineView()
        layout.addWidget(self.web_view, 1)

        # Load initial empty map HTML
        self._load_html_template()

    def _load_html_template(self):
        """Generate and load the Leaflet HTML map document."""
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>2G Frequency Map</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        html, body, #map {
            height: 100%;
            width: 100%;
            margin: 0;
            padding: 0;
            background: #1a1a2e;
        }
        .leaflet-container {
            background-color: #1a1a2e !important;
        }
        .sector-popup {
            color: #1a1a2e;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 12px;
        }
        .sector-popup b {
            color: #0f3460;
        }
        .conflict-tooltip {
            background: #16213e;
            color: #e0e0e8;
            border: 1px solid #00d4aa;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 11px;
        }
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([40.4168, -3.7038], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }).addTo(map);

        var sectorsLayer = L.layerGroup().addTo(map);
        var conflictsLayer = L.layerGroup().addTo(map);

        // Helper: calculate destination point given distance & bearing
        function getDestinationPoint(lat, lon, brng, distMeters) {
            var R = 6371000;
            var rad = brng * Math.PI / 180;
            var lat1 = lat * Math.PI / 180;
            var lon1 = lon * Math.PI / 180;

            var lat2 = Math.asin(Math.sin(lat1)*Math.cos(distMeters/R) +
                       Math.cos(lat1)*Math.sin(distMeters/R)*Math.cos(rad));
            var lon2 = lon1 + Math.atan2(Math.sin(rad)*Math.sin(distMeters/R)*Math.cos(lat1),
                              Math.cos(distMeters/R)-Math.sin(lat1)*Math.sin(lat2));

            return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
        }

        // Draw sector wedge (quesito)
        function drawSector(lat, lon, azimuth, beamWidth, radiusMeters, color, cellData) {
            var points = [[lat, lon]];
            var halfWidth = beamWidth / 2;
            var startAngle = azimuth - halfWidth;
            var endAngle = azimuth + halfWidth;

            for (var a = startAngle; a <= endAngle; a += 5) {
                var pt = getDestinationPoint(lat, lon, a, radiusMeters);
                points.push(pt);
            }
            points.push([lat, lon]);

            var polygon = L.polygon(points, {
                color: color,
                fillColor: color,
                fillOpacity: 0.5,
                weight: 2
            });

            var popupContent = '<div class="sector-popup">' +
                '<h3>📡 ' + cellData.cell_id + '</h3>' +
                '<b>Estación:</b> ' + cellData.station_name + '<br/>' +
                '<b>Sector:</b> ' + cellData.sector + ' | <b>Azimut:</b> ' + cellData.azimuth + '°<br/>' +
                '<b>BCCH:</b> <span style="font-weight:bold;color:#00b894;">' + cellData.bcch + '</span><br/>' +
                '<b>TCH:</b> ' + cellData.tch_list.join(', ') +
                '</div>';

            polygon.bindPopup(popupContent);
            sectorsLayer.addLayer(polygon);
        }

        // Draw line connecting conflict cells
        function drawConflictLine(lat1, lon1, lat2, lon2, type, text) {
            var isCo = (type === 'cochannel');
            var color = isCo ? '#e74c3c' : '#f39c12';
            var line = L.polyline([[lat1, lon1], [lat2, lon2]], {
                color: color,
                weight: isCo ? 3 : 2,
                opacity: 0.8,
                dashArray: isCo ? null : '6, 6'
            });

            line.bindTooltip(text, {sticky: true, className: 'conflict-tooltip'});
            conflictsLayer.addLayer(line);
        }

        function updateMapData(data) {
            sectorsLayer.clearLayers();
            conflictsLayer.clearLayers();

            var bounds = L.latLngBounds();

            // Render sector pie slices (quesitos)
            data.cells.forEach(function(c) {
                drawSector(c.lat, c.lon, c.azimuth, 60, 220, c.color, c);
                bounds.extend([c.lat, c.lon]);
            });

            // Render conflict lines
            if (data.conflicts) {
                data.conflicts.forEach(function(cf) {
                    drawConflictLine(cf.lat1, cf.lon1, cf.lat2, cf.lon2, cf.type, cf.text);
                });
            }

            if (data.cells.length > 0) {
                map.fitBounds(bounds, {padding: [50, 50]});
            }
        }
    </script>
</body>
</html>"""
        self.web_view.setHtml(html)

    def load_data(self, cells: dict[str, Cell], results: list[PlanResult] = None):
        """Update map with loaded cells and plan results."""
        self.cells = cells
        self.results = results or []

        # Populate plan combo box
        self.combo_plan.blockSignals(True)
        self.combo_plan.clear()
        self.combo_plan.addItem("Original (Antes de calcular)", None)

        per_run = getattr(self.results, "per_run", {})

        if per_run and len(per_run) > 1:
            for r in self.results:
                self.combo_plan.addItem(
                    f"🏆 Mejor Global - Plan {r.plan_id} (Score: {r.score:.1f}, Ej. {r.run_id})", r
                )
            for run_idx in sorted(per_run.keys()):
                for r in per_run[run_idx]:
                    self.combo_plan.addItem(
                        f"Ejecución {run_idx} - Plan {r.plan_id} (Score: {r.score:.1f})", r
                    )
        else:
            for r in self.results:
                self.combo_plan.addItem(f"Plan {r.plan_id} (Score: {r.score:.1f})", r)

        self.combo_plan.blockSignals(False)

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

    def _reset_map_view(self):
        self._refresh_map()

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
            plan_res = None
            for cid, cell in self.cells.items():
                assignments[cid] = (cell.bcch or 0, cell.tch_list)

        # Prepare cell sector objects for Leaflet
        cell_objs = []
        for cid, cell in sorted(self.cells.items()):
            bcch, tch_list = assignments.get(cid, (cell.bcch or 0, cell.tch_list))

            # Filter by target frequency if selected
            if target_freq is not None and target_freq > 0:
                if bcch != target_freq and target_freq not in tch_list:
                    continue

            color = _freq_to_color(bcch)
            cell_objs.append({
                "cell_id": cell.cell_id,
                "station_name": cell.station.name,
                "sector": cell.sector,
                "azimuth": cell.azimuth,
                "lat": cell.station.latitude,
                "lon": cell.station.longitude,
                "bcch": bcch,
                "tch_list": tch_list,
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
                if not cell_a or not cell_b:
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
                    "type": v.violation_type,
                    "text": text
                })

        payload = {
            "cells": cell_objs,
            "conflicts": conflict_objs
        }

        js_code = f"updateMapData({json.dumps(payload)});"
        self.web_view.page().runJavaScript(js_code)

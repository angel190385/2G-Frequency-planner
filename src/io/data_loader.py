"""
Data loading and export utilities.
Supports CSV (comma and semicolon separated) and Excel files.
"""
from __future__ import annotations
import csv
import os
from typing import Optional

from src.models.cell import Cell, Station
from src.models.neighbor import NeighborRelation, NeighborGraph, haversine_distance


def _detect_separator(file_path: str) -> str:
    """Auto-detect CSV separator (comma vs semicolon)."""
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        first_line = f.readline()
        if ';' in first_line and ',' not in first_line:
            return ';'
        if first_line.count(';') > first_line.count(','):
            return ';'
        return ','


def _read_csv(file_path: str) -> list[dict]:
    """Read a CSV file and return a list of row dictionaries."""
    sep = _detect_separator(file_path)
    rows = []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=sep)
        for row in reader:
            # Strip whitespace from keys and values
            clean_row = {k.strip(): v.strip() if v else ''
                         for k, v in row.items() if k}
            rows.append(clean_row)
    return rows


def _read_excel(file_path: str, sheet_name: Optional[str] = None) -> list[dict]:
    """Read an Excel file and return a list of row dictionaries."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ImportError("openpyxl is required for Excel files. "
                          "Install with: pip install openpyxl")

    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active

    rows = []
    headers = None
    for row in ws.iter_rows(values_only=True):
        if headers is None:
            headers = [str(h).strip() if h else f"col_{i}"
                       for i, h in enumerate(row)]
            continue
        row_dict = {}
        for i, val in enumerate(row):
            if i < len(headers):
                row_dict[headers[i]] = str(val).strip() if val is not None else ''
        rows.append(row_dict)

    wb.close()
    return rows


def _read_file(file_path: str) -> list[dict]:
    """Read either CSV or Excel based on extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.xlsx', '.xls'):
        return _read_excel(file_path)
    else:
        return _read_csv(file_path)


def _parse_int(value: str) -> Optional[int]:
    """Parse integer, returning None for empty/invalid values."""
    if not value or value.lower() in ('', 'none', 'n/a', '-'):
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _parse_float(value: str) -> float:
    """Parse float, returning 0.0 for empty/invalid values."""
    if not value:
        return 0.0
    try:
        return float(value.replace(',', '.'))
    except (ValueError, TypeError):
        return 0.0


def load_cells(file_path: str) -> tuple[dict[str, Cell], dict[str, Station], list[str]]:
    """
    Load cells from a CSV/Excel file.

    Expected columns:
        station_id, station_name, lat, lon, cell_id, sector, bcch,
        tch1, tch2, tch3, ...

    Returns:
        - dict of Cell objects keyed by cell_id
        - dict of Station objects keyed by station_id
        - list of warning messages
    """
    rows = _read_file(file_path)
    cells: dict[str, Cell] = {}
    stations: dict[str, Station] = {}
    warnings: list[str] = []

    # Detect TCH / TRX columns
    sample_keys = list(rows[0].keys())
    tch_columns = []
    for k in sample_keys:
        kl = k.lower().strip()
        if kl in ('cellname', 'cell_name', 'cell_id', 'cellid', 'station_id', 'station_name', 'lat', 'lon', 'sector', 'azimuth', 'azimut', 'bcch'):
            continue
        if (kl.startswith('tch') or kl.startswith('trx') or
            'tch' in kl or 'trx' in kl or
            (kl.startswith('ch') and kl not in ('channel', 'canal')) or
            (('freq' in kl or 'frec' in kl) and 'bcch' not in kl)):
            tch_columns.append(k)
    tch_columns.sort()

    for i, row in enumerate(rows, start=2):  # row 2+ (1 is header)
        try:
            station_id = row.get('station_id', '').strip()
            station_name = row.get('station_name', station_id).strip()
            lat = _parse_float(row.get('lat', '0'))
            lon = _parse_float(row.get('lon', '0'))

            # Prioritize CellName over legacy cell_id; never pick Cellid if CellName is present
            cell_id = ""
            for key_candidate in ('CellName', 'cellname', 'cell_name', 'cell_id', 'Cell_ID'):
                val = row.get(key_candidate, '').strip()
                if val:
                    cell_id = val
                    break
            if not cell_id:
                # Case-insensitive lookup for CellName first
                for k, v in row.items():
                    if k.lower().replace('_', '') == 'cellname' and v.strip():
                        cell_id = v.strip()
                        break
            if not cell_id:
                # Fallback to cell_id only if CellName was not provided at all
                for k, v in row.items():
                    if k.lower().replace('_', '') in ('cellid', 'idcelda') and v.strip():
                        cell_id = v.strip()
                        break

            sector = row.get('sector', '').strip()
            bcch = _parse_int(row.get('bcch', ''))
            azimuth_val = row.get('azimuth', row.get('azimut', '')).strip()
            if azimuth_val:
                azimuth = _parse_float(azimuth_val)
            else:
                # Default azimuth by sector letter if missing
                sector_upper = sector.upper()
                if sector_upper == 'A':
                    azimuth = 0.0
                elif sector_upper == 'B':
                    azimuth = 120.0
                elif sector_upper == 'C':
                    azimuth = 240.0
                elif sector_upper == 'D':
                    azimuth = 300.0
                else:
                    azimuth = 0.0

            if not cell_id:
                warnings.append(f"Fila {i}: CellName vacío, ignorada")
                continue

            if not station_id:
                station_id = cell_id[:cell_id.rfind(sector)] if sector else cell_id

            # Parse TCH frequencies (handles single or delimiter-separated multiple values)
            tch_list = []
            import re
            for tc in tch_columns:
                raw_tc = str(row.get(tc, '')).strip()
                if not raw_tc or raw_tc.lower() in ('', 'none', 'n/a', '-'):
                    continue
                # Find all integer numbers in the field
                for tok in re.findall(r'\b\d+\b', raw_tc):
                    try:
                        f_int = int(tok)
                        if f_int > 0:
                            tch_list.append(f_int)
                    except ValueError:
                        pass

            # Create or reuse Station
            if station_id not in stations:
                stations[station_id] = Station(
                    station_id=station_id,
                    name=station_name,
                    latitude=lat,
                    longitude=lon
                )

            cells[cell_id] = Cell(
                cell_id=cell_id,
                station=stations[station_id],
                sector=sector,
                azimuth=azimuth,
                bcch=bcch,
                tch_list=tch_list
            )

        except Exception as e:
            warnings.append(f"Fila {i}: Error procesando - {e}")

    return cells, stations, warnings


def load_neighbors(
    file_path: str,
    cells: dict[str, Cell]
) -> tuple[NeighborGraph, list[str]]:
    """
    Load neighbor relations from a CSV/Excel file.

    Expected columns:
        cell_a, cell_b

    Distances are automatically calculated from station coordinates.

    Returns:
        - NeighborGraph
        - list of warning messages
    """
    rows = _read_file(file_path)
    graph = NeighborGraph()
    warnings: list[str] = []

    for i, row in enumerate(rows, start=2):
        try:
            cell_a = row.get('cell_a', '').strip()
            cell_b = row.get('cell_b', '').strip()

            if not cell_a or not cell_b:
                warnings.append(f"Fila {i}: celda vacía, ignorada")
                continue

            if cell_a not in cells:
                warnings.append(f"Fila {i}: celda '{cell_a}' no encontrada en datos de celdas")
                continue
            if cell_b not in cells:
                warnings.append(f"Fila {i}: celda '{cell_b}' no encontrada en datos de celdas")
                continue

            # Calculate distance from station coordinates
            sta = cells[cell_a].station
            stb = cells[cell_b].station
            dist = haversine_distance(
                sta.latitude, sta.longitude,
                stb.latitude, stb.longitude
            )

            relation = NeighborRelation(
                cell_a_id=cell_a,
                cell_b_id=cell_b,
                distance_km=dist
            )
            graph.add_relation(relation)

        except Exception as e:
            warnings.append(f"Fila {i}: Error procesando - {e}")

    return graph, warnings


def load_handovers(
    file_path: str,
    neighbor_graph: NeighborGraph
) -> list[str]:
    """
    Load handover data and merge it into the existing neighbor graph.

    Expected columns:
        cell_a, cell_b, ho_count

    Only updates existing neighbor relations; does NOT create new ones.

    Returns:
        list of warning messages
    """
    rows = _read_file(file_path)
    warnings: list[str] = []
    matched = 0
    unmatched = 0

    for i, row in enumerate(rows, start=2):
        try:
            cell_a = row.get('cell_a', '').strip()
            cell_b = row.get('cell_b', '').strip()
            ho_count = _parse_int(row.get('ho_count', ''))

            if not cell_a or not cell_b:
                continue

            relation = neighbor_graph.get_relation(cell_a, cell_b)
            if relation:
                relation.ho_count = ho_count
                matched += 1
            else:
                unmatched += 1

        except Exception as e:
            warnings.append(f"Fila {i}: Error procesando - {e}")

    warnings.append(
        f"HO cargados: {matched} coincidencias con vecinas, "
        f"{unmatched} sin vecindad definida"
    )

    return warnings


def _sanitize_csv_field(val: any) -> str:
    """Sanitize string values to prevent CSV formula injection (DDE attacks)."""
    s = str(val) if val is not None else ''
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


def export_results(file_path: str, results: list, cells: dict[str, Cell]):
    """
    Export calculation results to CSV with formula injection protection.
    """
    with open(file_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')

        writer.writerow([
            'Plan', 'Score', 'CellName', 'Station', 'Sector',
            'BCCH_Antes', 'BCCH_Después',
            'TCH_Antes', 'TCH_Después',
            'Cochan_BCCH', 'Cochan_TCH',
            'Adj_BCCH', 'Adj_TCH'
        ])

        for result in results:
            for assign in result.assignments:
                cell = cells.get(assign.cell_id)
                if not cell:
                    continue
                writer.writerow([
                    result.plan_id,
                    f"{result.score:.1f}",
                    _sanitize_csv_field(assign.cell_id),
                    _sanitize_csv_field(cell.station.name if cell else ''),
                    _sanitize_csv_field(cell.sector if cell else ''),
                    cell.bcch if cell else '',
                    assign.bcch,
                    ','.join(str(f) for f in (cell.tch_list if cell else [])),
                    ','.join(str(f) for f in assign.tch_list),
                    result.cochannel_bcch_bcch + result.cochannel_bcch_tch,
                    result.cochannel_tch_tch,
                    result.adjacent_bcch_bcch + result.adjacent_bcch_tch,
                    result.adjacent_tch_tch,
                ])

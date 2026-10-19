"""
Importer for t_Planner_Global_Cells.csv and t_Planner_Global_Sectors.csv exports.

Applies filtering criteria:
- cells.MNC == 1 (or '01')
- cells.Tech == '2G'
- cells.Band == 900 (or 'GSM900', 'EGSM900', etc.)
- cells.Status == 'In Service'

Mapping:
- station_id <- cells.SiteId
- station_name <- cells.SiteName
- lat <- cells.WGS84_LAT_DEC
- lon <- cells.WGS84_LON_DEC
- cell_id <- cells.CellName
- sector <- last numeric character of CellName
- azimuth <- sectors.SectorAzimuth (first match)
- bcch <- cells.BCCH
- tch_list <- cells.TCHs (comma-separated integers)
"""
from __future__ import annotations
import csv
import re
from typing import Optional, Any

from src.models.cell import Cell, Station


def _detect_delimiter(file_path: str) -> str:
    """Detect delimiter (prioritize semicolon ; then comma ,)."""
    with open(file_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        first_line = f.readline()
        if ';' in first_line:
            return ';'
        if '\t' in first_line:
            return '\t'
        return ','


def _normalize_key(key: str) -> str:
    """Normalize column header for case-insensitive matching."""
    return re.sub(r'[\s_]+', '', key.strip().lower()) if key else ''


def _parse_float(val: Any) -> float:
    """Safely parse float supporting comma or dot as decimal separator."""
    if val is None:
        return 0.0
    s = str(val).strip().replace(',', '.')
    if not s or s.lower() in ('nan', 'none', 'null', '-', ''):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_int(val: Any) -> Optional[int]:
    """Safely parse integer."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ('nan', 'none', 'null', '-', ''):
        return None
    try:
        return int(float(s.replace(',', '.')))
    except (ValueError, TypeError):
        return None


def _extract_last_digit(s: str) -> str:
    """Extract the last numeric character from a string, or fallback to '1'."""
    digits = re.findall(r'\d', s)
    return digits[-1] if digits else "1"


def load_planner_sectors(sectors_file_path: str) -> dict[str, float]:
    """
    Load sectors file and build an azimuth lookup map.
    Returns:
        dict mapping:
          - cell_name.upper() -> azimuth
          - f"{site_id.upper()}_{sector}" -> azimuth
          - site_id.upper() -> azimuth (first match fallback)
    """
    azimuth_map: dict[str, float] = {}
    delimiter = _detect_delimiter(sectors_file_path)

    with open(sectors_file_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return azimuth_map

        headers = [_normalize_key(h) for h in raw_headers]
        
        # Identify indices
        idx_azimuth = None
        idx_cell = None
        idx_site = None
        idx_sector = None

        for i, h in enumerate(headers):
            if 'azimuth' in h:
                idx_azimuth = i
            elif h in ('cellname', 'cell_name') or h.startswith('cellname'):
                idx_cell = i
            elif 'siteid' in h or h == 'site':
                idx_site = i
            elif 'sector' in h and 'azimuth' not in h:
                idx_sector = i

        if idx_azimuth is None:
            # Fallback: search for any column with azimuth
            for i, raw_h in enumerate(raw_headers):
                if 'azimuth' in raw_h.lower() or 'azimut' in raw_h.lower():
                    idx_azimuth = i
                    break

        if idx_azimuth is None:
            return azimuth_map

        for row in reader:
            if not row or len(row) <= idx_azimuth:
                continue

            raw_az = row[idx_azimuth].strip()
            if not raw_az:
                continue
            az_val = _parse_float(raw_az)

            cell_val = row[idx_cell].strip().upper() if idx_cell is not None and len(row) > idx_cell else ''
            site_val = row[idx_site].strip().upper() if idx_site is not None and len(row) > idx_site else ''
            sec_val = row[idx_sector].strip().upper() if idx_sector is not None and len(row) > idx_sector else ''

            if cell_val and cell_val not in azimuth_map:
                azimuth_map[cell_val] = az_val

            if site_val and sec_val:
                key_sec = f"{site_val}_{sec_val}"
                if key_sec not in azimuth_map:
                    azimuth_map[key_sec] = az_val

            if site_val and site_val not in azimuth_map:
                azimuth_map[site_val] = az_val

    return azimuth_map


from typing import Optional, Any, Callable


def load_planner_global_cells(
    cells_file_path: str,
    sectors_file_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> tuple[dict[str, Cell], dict[str, Station], dict[str, Any]]:
    """
    Import network cells from t_Planner_Global_Cells.csv.
    
    Filters:
    - MNC == 1 (or '01')
    - Tech == 2G
    - Band == 900 (or 'GSM900', 'EGSM', 'PGSM')
    - Status == 'In Service' (case-insensitive)

    Returns:
    - cells: dict[cell_id, Cell]
    - stations: dict[station_id, Station]
    - stats: dict containing counts of processed, filtered, and accepted cells
    """
    azimuth_map: dict[str, float] = {}
    if sectors_file_path:
        try:
            azimuth_map = load_planner_sectors(sectors_file_path)
        except Exception as e:
            pass

    delimiter = _detect_delimiter(cells_file_path)
    cells: dict[str, Cell] = {}
    stations: dict[str, Station] = {}

    stats = {
        "total_rows": 0,
        "filtered_mnc": 0,
        "filtered_tech": 0,
        "filtered_band": 0,
        "filtered_status": 0,
        "accepted": 0,
        "stations_count": 0,
        "warnings": []
    }

    with open(cells_file_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return cells, stations, stats

        header_norm = [_normalize_key(h) for h in raw_headers]
        col_map: dict[str, int] = {}

        # 1. Prioritize CellName specifically: NEVER pick Cellid
        for i, h in enumerate(header_norm):
            if h in ('cellname', 'cell_name') or h.startswith('cellname'):
                col_map['cellname'] = i
                break

        # 2. Match other columns flexibly
        for i, h in enumerate(header_norm):
            if h in ('mnc',):
                col_map['mnc'] = i
            elif h in ('tech', 'technology'):
                col_map['tech'] = i
            elif h in ('band', 'frecband'):
                col_map['band'] = i
            elif h in ('status', 'estado'):
                col_map['status'] = i
            elif h in ('siteid', 'site', 'stationid'):
                col_map['siteid'] = i
            elif h in ('sitename', 'stationname'):
                col_map['sitename'] = i
            elif 'lat' in h and ('dec' in h or 'wgs' in h or h == 'lat'):
                col_map['lat'] = i
            elif 'lon' in h and ('dec' in h or 'wgs' in h or h == 'lon'):
                col_map['lon'] = i
            elif h in ('bcch', 'bcchfreq', 'arfcn'):
                col_map['bcch'] = i
            elif h in ('tchs', 'tch', 'trxs', 'trx'):
                col_map['tchs'] = i

        # Fallbacks if some headers differ slightly (strictly looking for CellName, never Cellid)
        for i, raw_h in enumerate(raw_headers):
            k = raw_h.strip().lower()
            if 'siteid' in k and 'siteid' not in col_map:
                col_map['siteid'] = i
            elif 'sitename' in k and 'sitename' not in col_map:
                col_map['sitename'] = i
            elif 'wgs84_lat' in k and 'lat' not in col_map:
                col_map['lat'] = i
            elif 'wgs84_lon' in k and 'lon' not in col_map:
                col_map['lon'] = i
            elif 'cellname' in k and 'cellname' not in col_map:
                col_map['cellname'] = i
            elif 'tchs' in k and 'tchs' not in col_map:
                col_map['tchs'] = i

        def get_val(row: list[str], key: str, default: str = '') -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return default

        for line_num, row in enumerate(reader, start=2):
            if not row or not any(row):
                continue
            stats["total_rows"] += 1
            if progress_callback and stats["total_rows"] % 2000 == 0:
                progress_callback(stats["total_rows"], stats["accepted"])

            # 1. Filter: MNC == 1 (or '01')
            mnc = get_val(row, 'mnc')
            if mnc and mnc not in ('1', '01', '1.0'):
                stats["filtered_mnc"] += 1
                continue

            # 2. Filter: Tech == 2G
            tech = get_val(row, 'tech').upper()
            if tech and tech != '2G':
                stats["filtered_tech"] += 1
                continue

            # 3. Filter: Band == 900
            band_str = get_val(row, 'band')
            if band_str:
                # Keep if contains 900 or egsm / pgsm
                clean_band = re.sub(r'[^\d]', '', band_str)
                if clean_band != '900' and '900' not in band_str.upper() and 'EGSM' not in band_str.upper() and 'PGSM' not in band_str.upper():
                    stats["filtered_band"] += 1
                    continue

            # 4. Filter: Status == 'In Service'
            status = get_val(row, 'status').lower()
            if status and status != 'in service':
                stats["filtered_status"] += 1
                continue

            # 5. Extract fields
            site_id = get_val(row, 'siteid')
            site_name = get_val(row, 'sitename', site_id) or site_id
            lat = _parse_float(get_val(row, 'lat'))
            lon = _parse_float(get_val(row, 'lon'))
            cell_name = get_val(row, 'cellname')

            if not cell_name:
                continue
            if not site_id:
                site_id = cell_name

            # Sector is the last numeric character of CellName
            sector = _extract_last_digit(cell_name)

            # Azimuth resolution from sectors file
            azimuth = 0.0
            cell_key = cell_name.upper()
            site_sec_key = f"{site_id.upper()}_{sector}"
            site_key = site_id.upper()

            if cell_key in azimuth_map:
                azimuth = azimuth_map[cell_key]
            elif site_sec_key in azimuth_map:
                azimuth = azimuth_map[site_sec_key]
            elif site_key in azimuth_map:
                azimuth = azimuth_map[site_key]
            else:
                # Default azimuth based on sector digit if missing
                try:
                    sec_num = int(sector)
                    azimuth = ((sec_num - 1) * 120.0) % 360.0
                except (ValueError, TypeError):
                    azimuth = 0.0

            # Frequencies
            bcch = _parse_int(get_val(row, 'bcch'))

            # TCHs from comma-separated list
            raw_tchs = get_val(row, 'tchs')
            tch_list = []
            if raw_tchs:
                # Parse all integers separated by comma or other punctuation
                for tok in re.findall(r'\b\d+\b', raw_tchs):
                    try:
                        f_val = int(tok)
                        if f_val > 0 and f_val not in tch_list:
                            tch_list.append(f_val)
                    except ValueError:
                        pass

            # Register Station
            if site_id not in stations:
                stations[site_id] = Station(
                    station_id=site_id,
                    name=site_name,
                    latitude=lat,
                    longitude=lon
                )
            station = stations[site_id]

            # Register Cell
            cell = Cell(
                cell_id=cell_name,
                station=station,
                sector=sector,
                azimuth=azimuth,
                bcch=bcch,
                tch_list=tch_list
            )
            cells[cell_name] = cell
            stats["accepted"] += 1

    stats["stations_count"] = len(stations)
    return cells, stations, stats

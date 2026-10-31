# -*- coding: utf-8 -*-
"""
1:10,000 contour elevation label-point generator
PRODUCTION V3.4 - adjacent-first high-side + north-normal priority - Python 3.10 only

Core production principles
--------------------------
1) Analyse contours globally (or in a buffered test area), NOT sheet-by-sheet.
2) Map-sheet boundaries constrain FINAL label placement only; they do not cut the
   terrain evidence used to determine the higher side.
3) Natural contour breaks (e.g. at rivers / reservoirs) are NOT bridged.  Same-
   elevation pieces are merged only when their endpoints genuinely meet within a
   very small tolerance and the endpoint topology is degree-2.
4) Higher-side judgement uses the FIRST different-elevation contour met along the local left/right normal rays.
5) DEM is used as a consistency check for contour-resolved sites and as fallback when adjacent-contour evidence is insufficient.
6) Candidate points are searched densely along each logical contour, rather than
   relying on a few segment midpoints.  Open-line endpoints are a hard exclusion.
7) Text top must face higher terrain and lie within 75 degrees of north. Due east/west and all southern directions are rejected; northward normal direction ranks first.
8) OBJE_CODE is preserved throughout.  The program supports three scopes:
      index_only : process only index contours
      all        : process all contours
      both       : process all contours once, write an all/master layer and an
                   index-contour subset without rerunning terrain analysis
9) Output is FileGDB for direct use in ArcMap 10.2.2.

Recommended ArcMap label settings
---------------------------------
Label field: LABEL_TXT
Placement: Place label at an angle specified by a field
Rotation field: ANGLE_GEO
Rotation style: Geographic
Place label perpendicular to the angle: UNCHECKED
Do NOT enable an additional automatic 180-degree "keep upright" flip.
V3.4 hard-requires TOP_AZ within 75 degrees of north, with <=45 degrees ranked highest.

Recommended Python 3.10 packages
--------------------------------
numpy, pandas, geopandas, shapely>=2, pyogrio, rasterio, pyproj, tqdm
"""

from __future__ import annotations

import os
import sys
import math
import time
import shutil
import hashlib
import traceback
from collections import defaultdict

import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
import pyogrio
import rasterio
from pyproj import CRS, Transformer
from shapely.geometry import Point, LineString, MultiLineString, GeometryCollection
from shapely.ops import linemerge
from shapely.strtree import STRtree
from tqdm import tqdm


# =====================================================================
# 1. CONFIGURATION / COMMAND LINE (algorithm functions below are retained)
# =====================================================================

import argparse
from pathlib import Path
import runpy

_EXAMPLE_CONFIG = Path(__file__).resolve().parent / "config" / "config.example.py"
_DEFAULT_SETTINGS = runpy.run_path(str(_EXAMPLE_CONFIG))
_CONFIG_NAMES = frozenset(k for k in _DEFAULT_SETTINGS if k.isupper())
globals().update({k: _DEFAULT_SETTINGS[k] for k in _CONFIG_NAMES})


def load_config(path):
    """Load a trusted local Python config; importing this module does no GIS I/O."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("Config missing: copy config/config.example.py to config/config.local.py and edit it.")
    values = runpy.run_path(str(path))
    missing = sorted(_CONFIG_NAMES - values.keys())
    if missing:
        raise ValueError("Incomplete config; copy the full example. Missing: " + ", ".join(missing))
    globals().update({k: values[k] for k in _CONFIG_NAMES})


def preflight_config():
    """Reject placeholders and overlapping output/input paths before GIS I/O."""
    required = ["CONTOUR_GDB", "CONTOUR_LAYER", "SHEET_GDB", "SHEET_LAYER", "OUTPUT_GDB"]
    if USE_DEM_FALLBACK:
        required.append("DEM_PATH")
    for name in required:
        value = globals()[name]
        if not value or "REPLACE_WITH" in str(value):
            raise ValueError("Configure " + name + " before running.")
    if INDEX_OBJE_CODES is not None and any("REPLACE_WITH" in str(v) for v in INDEX_OBJE_CODES):
        raise ValueError("Configure INDEX_OBJE_CODES using your data dictionary.")
    output = Path(OUTPUT_GDB).resolve()
    if output.suffix.lower() != ".gdb":
        raise ValueError("OUTPUT_GDB must be a dedicated .gdb directory.")
    inputs = [Path(CONTOUR_GDB).resolve(), Path(SHEET_GDB).resolve()]
    if USE_DEM_FALLBACK:
        inputs.append(Path(DEM_PATH).resolve())
    for item in inputs:
        if output == item or output in item.parents or item in output.parents:
            raise ValueError("OUTPUT_GDB must not overlap any input path.")


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Automatic contour label placement for 1:10,000 maps")
    parser.add_argument("--config", type=Path,
                        default=_EXAMPLE_CONFIG.with_name("config.local.py"),
                        help="Trusted Python config (default: config/config.local.py)")
    args = parser.parse_args(argv)
    load_config(args.config)
    preflight_config()
    main()


# =====================================================================
# 2. GENERIC HELPERS
# =====================================================================

def log(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg), flush=True)


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def normalize_code(v):
    if pd.isna(v):
        return None
    if isinstance(v, (np.integer, int)):
        return str(int(v))
    if isinstance(v, (np.floating, float)) and np.isfinite(v) and float(v).is_integer():
        return str(int(v))
    return str(v).strip()


def format_elev(v: float) -> str:
    x = float(v)
    if abs(x - round(x)) < 1e-8:
        return str(int(round(x)))
    return ("{:.3f}".format(x)).rstrip("0").rstrip(".")


def geographic_azimuth(p0: Point, p1: Point) -> float:
    """Geographic rotation: 0=N, 90=E, clockwise, [0,360)."""
    dx = p1.x - p0.x
    dy = p1.y - p0.y
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return 0.0
    return math.degrees(math.atan2(dx, dy)) % 360.0


def angular_diff(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def northward_preference_score(azimuth_deg: float) -> float:
    """
    Continuous [0,1] preference for a text-top/high-side azimuth.

    With PREFERRED_TEXT_TOP_AZ_DEG=0:
      north=1, east/west=0.5, south=0.
    This is NOT a validity test; it is only used to rank valid locations.
    """
    d = angular_diff(float(azimuth_deg), float(PREFERRED_TEXT_TOP_AZ_DEG))
    # angular_diff is [0,180]. cos maps N->1, E/W->0, S->-1.
    return float(0.5 * (1.0 + math.cos(math.radians(d))))


def explode_lines(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf.copy()
    out = gdf.explode(index_parts=False, ignore_index=True).copy()
    out = out[out.geometry.notna()].copy()
    out = out[~out.geometry.is_empty].copy()
    out = out[out.geometry.geom_type.isin(["LineString", "LinearRing"])].copy()
    if not out.empty:
        out["geometry"] = out.geometry.apply(
            lambda g: LineString(g.coords) if g.geom_type == "LinearRing" else g
        )
    return out.reset_index(drop=True)


def stable_geom_uid(geom, elev, code) -> str:
    """Stable for the same source geometry across repeat runs."""
    g = geom
    try:
        if UID_GRID_SIZE_M and UID_GRID_SIZE_M > 0:
            g = shapely.set_precision(g, UID_GRID_SIZE_M)
        g = shapely.normalize(g)
    except Exception:
        pass
    h = hashlib.sha1()
    h.update(str(format_elev(float(elev))).encode("utf-8"))
    h.update(b"|")
    h.update(str(normalize_code(code)).encode("utf-8"))
    h.update(b"|")
    h.update(bytes(g.wkb))
    return h.hexdigest()[:18]


def frame_safe_margin_m() -> float:
    extra = 0.5 * LABEL_GROUND_LENGTH_M if ADD_HALF_LABEL_TO_FRAME_MARGIN else 0.0
    return float(FRAME_BASE_INSET_M) + float(extra)


# =====================================================================
# 3. INDEX-CONTOUR IDENTIFICATION
# =====================================================================

def expected_index_elevation_mask(elev_series: pd.Series) -> np.ndarray:
    interval = float(CONTOUR_INTERVAL_M) * int(INDEX_CONTOUR_MULTIPLIER)
    vals = pd.to_numeric(elev_series, errors="coerce").to_numpy(dtype=float)
    ratio = (vals - float(INDEX_ELEV_ORIGIN_M)) / interval
    return np.isfinite(ratio) & (np.abs(ratio - np.round(ratio)) < 1e-8)


def build_obje_code_diagnostic(contours: gpd.GeoDataFrame) -> pd.DataFrame:
    tmp = contours[[OBJE_CODE_FIELD, ELEV_FIELD]].copy()
    tmp["_CODE_NORM"] = tmp[OBJE_CODE_FIELD].map(normalize_code)
    tmp["_INDEX_ELEV"] = expected_index_elevation_mask(tmp[ELEV_FIELD])
    total_expected = int(tmp["_INDEX_ELEV"].sum())
    rows = []
    for code, grp in tmp.groupby("_CODE_NORM", dropna=False):
        n = len(grp)
        idx_n = int(grp["_INDEX_ELEV"].sum())
        elevs = pd.to_numeric(grp[ELEV_FIELD], errors="coerce").dropna()
        rows.append({
            "OBJE_CODE": code,
            "FEATURES": int(n),
            "INDEX_ELEV_FEATURES": idx_n,
            "PURITY_INDEX_ELEV": idx_n / float(n) if n else 0.0,
            "COVERAGE_OF_EXPECTED_INDEX": idx_n / float(total_expected) if total_expected else 0.0,
            "ELEV_MIN": float(elevs.min()) if not elevs.empty else np.nan,
            "ELEV_MAX": float(elevs.max()) if not elevs.empty else np.nan,
            "UNIQUE_ELEV": int(elevs.nunique()) if not elevs.empty else 0,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            ["PURITY_INDEX_ELEV", "INDEX_ELEV_FEATURES"],
            ascending=[False, False]
        ).reset_index(drop=True)
    return df


def resolve_index_codes(contours: gpd.GeoDataFrame, diagnostic_csv: str):
    diag = build_obje_code_diagnostic(contours)
    ensure_parent(diagnostic_csv)
    diag.to_csv(diagnostic_csv, index=False, encoding="utf-8-sig")
    log("OBJE_CODE diagnostic: {}".format(diagnostic_csv))

    if INDEX_OBJE_CODES is not None:
        codes = {normalize_code(x) for x in INDEX_OBJE_CODES}
        codes.discard(None)
        if not codes:
            raise RuntimeError("INDEX_OBJE_CODES is empty after normalization.")
        log("Using explicit index OBJE_CODE(s): {}".format(sorted(codes)))
        return codes, "EXPLICIT_OBJE_CODE"

    if AUTO_DETECT_INDEX_OBJE_CODE:
        cand = diag[
            (diag["PURITY_INDEX_ELEV"] >= AUTO_CODE_MIN_PURITY) &
            (diag["INDEX_ELEV_FEATURES"] >= AUTO_CODE_MIN_FEATURES)
        ].copy()
        if not cand.empty:
            coverage = float(cand["COVERAGE_OF_EXPECTED_INDEX"].sum())
            codes = set(cand["OBJE_CODE"].dropna().map(normalize_code))
            if codes and coverage >= AUTO_CODE_MIN_COMBINED_COVERAGE:
                log("Auto-detected index OBJE_CODE(s): {}".format(sorted(codes)))
                return codes, "AUTO_OBJE_CODE"

    if ALLOW_ELEVATION_ONLY_FALLBACK:
        log("WARNING: index OBJE_CODE unresolved; using elevation multiples only.")
        return None, "ELEVATION_ONLY_FALLBACK"

    raise RuntimeError(
        "Could not identify index-contour OBJE_CODE safely. "
        "Open OBJE_CODE_index_contour_diagnostic.csv, confirm the code(s), "
        "then set INDEX_OBJE_CODES."
    )


def mark_index_contours(contours: gpd.GeoDataFrame, index_codes) -> gpd.GeoDataFrame:
    """Mark index contours using both OBJE_CODE and the expected 150 m sequence.

    A class code can also occur on non-index elevations. The code is the primary
    class and the elevation sequence is an additional safeguard.
    """
    out = contours.copy()
    elev_match = expected_index_elevation_mask(out[ELEV_FIELD])
    out["INDEX_ELEV_MATCH"] = elev_match.astype(np.int16)

    if index_codes is not None:
        code_norm = out[OBJE_CODE_FIELD].map(normalize_code)
        code_match = code_norm.isin(index_codes).to_numpy()
        out["INDEX_CODE_MATCH"] = code_match.astype(np.int16)
        if INDEX_REQUIRE_ELEV_PATTERN:
            is_index = code_match & elev_match
        else:
            is_index = code_match
        out["INDEX_CONFLICT"] = (code_match & (~elev_match)).astype(np.int16)
    else:
        out["INDEX_CODE_MATCH"] = np.zeros(len(out), dtype=np.int16)
        is_index = elev_match
        out["INDEX_CONFLICT"] = np.zeros(len(out), dtype=np.int16)

    out["IS_INDEX"] = np.asarray(is_index, dtype=np.int16)
    return out


# =====================================================================
# 4. CONNECTED SAME-CLASS MERGE - NEVER BRIDGE NATURAL GAPS
# =====================================================================

def _snap_key_xy(x, y, tol):
    return (int(round(float(x) / tol)), int(round(float(y) / tol)))


def merge_connected_group(group: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Merge same-elevation / same-OBJE_CODE pieces ONLY through endpoint contacts
    whose endpoint degree is exactly 2.

    Important: no buffering, nearest-neighbour gap closing, or bridge creation is
    performed.  Therefore river / reservoir breaks remain genuine breaks.
    """
    group = explode_lines(group)
    if group.empty:
        return group.copy()
    if len(group) == 1:
        out = group.copy()
        out["SRC_CNT"] = 1
        g0 = out.geometry.iloc[0]
        out["LINE_UID"] = [stable_geom_uid(g0, out[ELEV_FIELD].iloc[0], out[OBJE_CODE_FIELD].iloc[0])]
        out["LINE_LEN"] = [float(g0.length)]
        out["IS_CLOSED"] = [int(bool(g0.is_ring))]
        return out

    geoms = list(group.geometry)
    endpoint_map = defaultdict(list)
    for i, g in enumerate(geoms):
        coords = list(g.coords)
        if len(coords) < 2:
            continue
        endpoint_map[_snap_key_xy(*coords[0][:2], MERGE_ENDPOINT_TOL_M)].append(i)
        endpoint_map[_snap_key_xy(*coords[-1][:2], MERGE_ENDPOINT_TOL_M)].append(i)

    parent = list(range(len(group)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Only degree-2 endpoint topology can join two pieces.
    for ids in endpoint_map.values():
        uniq = sorted(set(ids))
        if len(uniq) == 2:
            union(uniq[0], uniq[1])

    comps = defaultdict(list)
    for i in range(len(group)):
        comps[find(i)].append(i)

    rows = []
    for ids in comps.values():
        sub = group.iloc[ids]
        # linemerge alone joins endpoint-connected pieces and does NOT create a
        # bridge across open gaps; avoid unary_union because it may node crossings.
        try:
            merged = linemerge(MultiLineString(list(sub.geometry)))
        except Exception:
            merged = MultiLineString(list(sub.geometry))

        if isinstance(merged, LineString):
            parts = [merged]
        elif isinstance(merged, MultiLineString):
            parts = list(merged.geoms)
        elif isinstance(merged, GeometryCollection):
            parts = [x for x in merged.geoms if isinstance(x, LineString)]
        else:
            parts = []

        base = sub.iloc[0].copy()
        src_ids = sorted(set([int(x) for x in sub["SRC_FID"].tolist()]))
        for part in parts:
            if part is None or part.is_empty or part.length <= 0:
                continue
            r = base.copy()
            r.geometry = part
            r["SRC_FID"] = src_ids[0] if src_ids else -1
            r["SRC_CNT"] = len(src_ids)
            r["LINE_UID"] = stable_geom_uid(part, r[ELEV_FIELD], r[OBJE_CODE_FIELD])
            r["LINE_LEN"] = float(part.length)
            r["IS_CLOSED"] = int(bool(part.is_ring))
            rows.append(r)

    if not rows:
        return gpd.GeoDataFrame(columns=group.columns, geometry="geometry", crs=group.crs)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=group.crs).reset_index(drop=True)


def build_logical_lines(contours: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    work = explode_lines(contours)
    if work.empty:
        return work

    outputs = []
    grouped = work.groupby([ELEV_FIELD, OBJE_CODE_FIELD], dropna=False, sort=False)
    for _, grp in tqdm(grouped, total=grouped.ngroups, desc="Merge connected contours"):
        m = merge_connected_group(grp)
        if not m.empty:
            outputs.append(m)

    if not outputs:
        return gpd.GeoDataFrame(columns=work.columns, geometry="geometry", crs=work.crs)
    out = gpd.GeoDataFrame(pd.concat(outputs, ignore_index=True), crs=work.crs)
    return out.reset_index(drop=True)


# =====================================================================
# 5. DENSE PER-CONTOUR CANDIDATE SEARCH
# =====================================================================

def _interp_station(line: LineString, station: float, is_closed: int) -> Point:
    L = float(line.length)
    if L <= 0:
        return Point()
    s = float(station)
    if int(is_closed) == 1:
        s = s % L
    else:
        s = min(max(s, 0.0), L)
    return line.interpolate(s)


def candidate_stations(line: LineString, is_closed: int):
    """Dense search stations.

    Open lines never sample near heads/tails. Closed lines have no endpoint and are
    sampled around the full loop. The midpoint is always added for deterministic
    coverage on open lines.
    """
    L = float(line.length)
    if L <= 0:
        return []

    if int(is_closed) == 1:
        if L < LABEL_GROUND_LENGTH_M:
            return []
        start = 0.5 * float(CANDIDATE_STEP_M)
        stations = list(np.arange(start, L, float(CANDIDATE_STEP_M)))
        if not stations:
            stations = [0.5 * L]
        return sorted(set(round(float(s), 3) for s in stations))

    clear = float(MIN_END_CLEARANCE_M)
    if L <= 2.0 * clear:
        return []
    stations = list(np.arange(clear, L - clear + 1e-9, float(CANDIDATE_STEP_M)))
    stations.append(0.5 * L)
    return sorted(set(round(float(s), 3) for s in stations))


def local_geometry_metrics(line: LineString, station: float, is_closed: int):
    """Measure whether the label footprint lies on a genuinely smooth reach.

    Geometry is resampled at constant ground spacing around the candidate.  This is
    more robust than checking only the two endpoints of a long window and catches a
    sharp corner even when the overall chord looks deceptively straight.
    """
    L = float(line.length)
    if L <= 0:
        return None

    closed = int(is_closed) == 1
    if not closed:
        end_clear = min(float(station), L - float(station))
        if end_clear < float(MIN_END_CLEARANCE_M) - 1e-9:
            return None
    else:
        end_clear = float("inf")

    # Local tangent used by ArcMap rotation and high-side judgement.
    th = float(TANGENT_HALF_WINDOW_M)
    if not closed:
        th = min(th, float(station), L - float(station))
    if th <= 1e-6:
        return None
    tp0 = _interp_station(line, station - th, is_closed)
    tp1 = _interp_station(line, station + th, is_closed)
    tang = geographic_azimuth(tp0, tp1)

    half = float(STRAIGHTNESS_CONTEXT_HALF_M)
    if not closed:
        half = min(half, float(station), L - float(station))
    if half < max(LABEL_HALF_GROUND_M, 20.0):
        return None

    # Resample from -half to +half with a fixed metric spacing.
    step = float(STRAIGHTNESS_SAMPLE_STEP_M)
    offsets = list(np.arange(-half, half + 1e-9, step))
    if offsets[-1] < half - 1e-9:
        offsets.append(half)
    if 0.0 not in offsets:
        offsets.append(0.0)
    offsets = sorted(set(float(x) for x in offsets))
    pts = [_interp_station(line, station + off, is_closed) for off in offsets]
    if len(pts) < 3:
        return None

    seg_az = []
    for a, b in zip(pts[:-1], pts[1:]):
        if a.distance(b) <= 1e-9:
            continue
        seg_az.append(geographic_azimuth(a, b))
    if len(seg_az) < 2:
        return None

    step_turns = [angular_diff(a, b) for a, b in zip(seg_az[:-1], seg_az[1:])]
    max_step_turn = max(step_turns) if step_turns else 0.0
    total_turn = float(sum(step_turns))

    pm = _interp_station(line, station, is_closed)
    label_half = min(float(LABEL_HALF_GROUND_M), half)
    p0 = _interp_station(line, station - label_half, is_closed)
    p1 = _interp_station(line, station + label_half, is_closed)
    az0 = geographic_azimuth(p0, pm)
    az1 = geographic_azimuth(pm, p1)
    bend = angular_diff(az0, az1)

    path_len = 2.0 * half
    chord = pts[0].distance(pts[-1])
    chord_ratio = chord / path_len if path_len > 0 else 0.0

    hard_ok = (
        bend <= float(HARD_MAX_BEND_DEG)
        and chord_ratio >= float(HARD_MIN_CHORD_RATIO)
        and total_turn <= float(HARD_MAX_TOTAL_TURN_DEG)
        and max_step_turn <= float(HARD_MAX_STEP_TURN_DEG)
    )
    preferred = (
        bend <= float(PREFERRED_MAX_BEND_DEG)
        and chord_ratio >= float(PREFERRED_MIN_CHORD_RATIO)
        and total_turn <= float(PREFERRED_MAX_TOTAL_TURN_DEG)
        and max_step_turn <= float(PREFERRED_MAX_STEP_TURN_DEG)
    )

    # Smooth 0..1 score; hard_ok is still evaluated separately.
    bend_score = max(0.0, 1.0 - bend / max(HARD_MAX_BEND_DEG, 1e-6))
    chord_score = max(0.0, min(1.0, (chord_ratio - HARD_MIN_CHORD_RATIO) /
                                    max(1.0 - HARD_MIN_CHORD_RATIO, 1e-6)))
    total_score = max(0.0, 1.0 - total_turn / max(HARD_MAX_TOTAL_TURN_DEG, 1e-6))
    step_score = max(0.0, 1.0 - max_step_turn / max(HARD_MAX_STEP_TURN_DEG, 1e-6))
    flat_score = 0.30 * bend_score + 0.35 * chord_score + 0.20 * total_score + 0.15 * step_score

    if closed:
        center_score = 1.0
        end_clear_out = np.nan
    else:
        # 0 at endpoints, 1 at exact line midpoint. Hard endpoint exclusion already
        # guarantees no head/tail placement; this score pushes the winner inward.
        center_score = max(0.0, 1.0 - abs(2.0 * float(station) / L - 1.0))
        end_clear_out = float(end_clear)

    return {
        "point": pm,
        "tangent_az": float(tang),
        "bend_deg": float(bend),
        "chord_ratio": float(chord_ratio),
        "total_turn": float(total_turn),
        "max_step_turn": float(max_step_turn),
        "flat_score": float(flat_score),
        "flat_class": 2 if preferred else (1 if hard_ok else 0),
        "hard_ok": bool(hard_ok),
        "center_score": float(center_score),
        "end_clear_m": end_clear_out,
    }


def _geometry_reject_reason(m):
    if m["bend_deg"] > HARD_MAX_BEND_DEG:
        return "LOCAL_BEND_TOO_HIGH"
    if m["chord_ratio"] < HARD_MIN_CHORD_RATIO:
        return "LOCAL_CHORD_RATIO_LOW"
    if m["total_turn"] > HARD_MAX_TOTAL_TURN_DEG:
        return "LOCAL_TOTAL_TURN_TOO_HIGH"
    if m["max_step_turn"] > HARD_MAX_STEP_TURN_DEG:
        return "LOCAL_CORNER_TOO_SHARP"
    return "LOCAL_GEOMETRY_UNSUITABLE"


def generate_raw_candidates(logical_lines: gpd.GeoDataFrame):
    candidates = []
    rejects = []

    for i, row in enumerate(tqdm(logical_lines.itertuples(index=False), total=len(logical_lines), desc="Dense candidate search"), 1):
        geom = row.geometry
        data = row._asdict()
        if geom is None or geom.is_empty:
            continue

        is_closed = int(data.get("IS_CLOSED", 0))
        stations = candidate_stations(geom, is_closed)
        if not stations:
            rejects.append({
                ELEV_FIELD: float(data[ELEV_FIELD]),
                OBJE_CODE_FIELD: data[OBJE_CODE_FIELD],
                "IS_INDEX": int(data.get("IS_INDEX", 0)),
                "LINE_UID": data.get("LINE_UID"),
                "LINE_LEN": float(geom.length),
                "IS_CLOSED": is_closed,
                "REJECT_REASON": "NO_INTERIOR_AFTER_END_CLEARANCE",
                "geometry": geom.interpolate(0.5, normalized=True),
            })
            continue

        for s in stations:
            m = local_geometry_metrics(geom, s, is_closed)
            if m is None:
                continue

            base = {
                ELEV_FIELD: float(data[ELEV_FIELD]),
                OBJE_CODE_FIELD: data[OBJE_CODE_FIELD],
                "IS_INDEX": int(data.get("IS_INDEX", 0)),
                "INDEX_CONFLICT": int(data.get("INDEX_CONFLICT", 0)),
                "SRC_FID": int(data.get("SRC_FID", -1)),
                "SRC_CNT": int(data.get("SRC_CNT", 1)),
                "LINE_UID": data.get("LINE_UID"),
                "LINE_LEN": float(data.get("LINE_LEN", geom.length)),
                "IS_CLOSED": is_closed,
                "STATION_M": float(s),
                "END_CLR_M": m["end_clear_m"],
                "CENTER_SC": m["center_score"],
                "BEND_DEG": m["bend_deg"],
                "CHORD_RT": m["chord_ratio"],
                "TOT_TURN": m["total_turn"],
                "MAX_TURN": m["max_step_turn"],
                "FLAT_SCORE": m["flat_score"],
                "FLAT_CLASS": m["flat_class"],
                "TANG_AZ": m["tangent_az"],
                "geometry": m["point"],
            }

            if not m["hard_ok"]:
                rr = base.copy()
                rr["REJECT_REASON"] = _geometry_reject_reason(m)
                rejects.append(rr)
                continue
            candidates.append(base)

        if VERBOSE_EVERY_LINES and i % VERBOSE_EVERY_LINES == 0:
            log("Dense-search progress: {}/{} logical lines; usable={}, rejected={}".format(
                i, len(logical_lines), len(candidates), len(rejects)
            ))

    return candidates, rejects


# =====================================================================
# 6. MAP-SHEET ASSIGNMENT / HARD FRAME AVOIDANCE
# =====================================================================

class SheetAssigner:
    def __init__(self, sheets: gpd.GeoDataFrame):
        self.sheets = sheets.reset_index(drop=True).copy()
        self.original_geoms = list(self.sheets.geometry)
        self.original_tree = STRtree(self.original_geoms)
        margin = frame_safe_margin_m()
        self.safe_geoms = []
        for g in self.original_geoms:
            sg = g.buffer(-margin) if margin > 0 else g
            self.safe_geoms.append(sg)
        valid_idx = [i for i, g in enumerate(self.safe_geoms) if g is not None and not g.is_empty]
        self.safe_valid_idx = np.array(valid_idx, dtype=int)
        self.safe_valid_geoms = [self.safe_geoms[i] for i in valid_idx]
        self.safe_tree = STRtree(self.safe_valid_geoms) if self.safe_valid_geoms else None
        log("Frame safe margin = {:.1f} m (base {:.1f} + half-label {}).".format(
            margin, FRAME_BASE_INSET_M, "yes" if ADD_HALF_LABEL_TO_FRAME_MARGIN else "no"
        ))

    @staticmethod
    def _contains_or_covers(poly, point):
        try:
            return bool(poly.covers(point))
        except Exception:
            return bool(poly.contains(point) or poly.touches(point))

    def _find_original_sheet(self, point: Point):
        idxs = self.original_tree.query(point)
        for idx in idxs:
            i = int(idx)
            if self._contains_or_covers(self.original_geoms[i], point):
                return i
        return None

    def assign(self, point: Point):
        if self.safe_tree is not None:
            idxs = self.safe_tree.query(point)
            for local_idx in idxs:
                li = int(local_idx)
                gi = int(self.safe_valid_idx[li])
                if self._contains_or_covers(self.safe_valid_geoms[li], point):
                    try:
                        frame_clear = float(point.distance(self.original_geoms[gi].boundary))
                    except Exception:
                        frame_clear = float("nan")
                    return {
                        "status": "OK",
                        "sheet_index": gi,
                        "tfbh": self.sheets.iloc[gi][TFBH_FIELD],
                        "frame_clear_m": frame_clear,
                    }

        orig = self._find_original_sheet(point)
        if orig is not None:
            try:
                frame_clear = float(point.distance(self.original_geoms[orig].boundary))
            except Exception:
                frame_clear = float("nan")
            return {
                "status": "TOO_CLOSE_TO_FRAME",
                "sheet_index": orig,
                "tfbh": self.sheets.iloc[orig][TFBH_FIELD],
                "frame_clear_m": frame_clear,
            }
        return {"status": "OUTSIDE_STANDARD_SHEETS", "sheet_index": None, "tfbh": None, "frame_clear_m": float("nan")}


def apply_sheet_assignment(candidates, rejects, assigner: SheetAssigner):
    kept = []
    for rec in tqdm(candidates, desc="Frame safety / sheet assignment"):
        a = assigner.assign(rec["geometry"])
        if a["status"] == "OK":
            r = rec.copy()
            r[TFBH_FIELD] = a["tfbh"]
            r["FRAME_CLR"] = float(a.get("frame_clear_m", np.nan))
            kept.append(r)
        else:
            r = rec.copy()
            r[TFBH_FIELD] = a.get("tfbh")
            r["FRAME_CLR"] = float(a.get("frame_clear_m", np.nan))
            r["FRAME_REQ_M"] = float(frame_safe_margin_m())
            r["REJECT_REASON"] = a["status"]
            rejects.append(r)
    return kept


# =====================================================================
# 7. NEARBY-CONTOUR HIGHER-SIDE EVIDENCE (PRIMARY)
# =====================================================================

class ContourSideIndex:
    """Higher-side judgement from the first adjacent contour on normal rays.

    For each candidate and each along-line sample position, cast one ray to the
    local LEFT and one to the local RIGHT.  On each ray keep only the first
    different-elevation contour that is actually intersected.  This deliberately
    ignores farther folds and nearby-but-not-crossed contours.
    """
    def __init__(self, evidence_lines: gpd.GeoDataFrame):
        e = explode_lines(evidence_lines[[ELEV_FIELD, OBJE_CODE_FIELD, "geometry"]].copy())
        self.geoms = list(e.geometry)
        self.elev = pd.to_numeric(e[ELEV_FIELD], errors="coerce").to_numpy(dtype=float)
        self.tree = STRtree(self.geoms)

    @staticmethod
    def _normal_vectors(tangent_az: float):
        rad = math.radians(float(tangent_az))
        ux, uy = math.sin(rad), math.cos(rad)
        # Geographic azimuth tangent -> mathematical left/right normals.
        lx, ly = -uy, ux
        rx, ry = uy, -ux
        return ux, uy, lx, ly, rx, ry

    @staticmethod
    def _intersection_distance(base: Point, inter):
        """Return the nearest positive distance from base to an intersection geometry."""
        if inter is None or inter.is_empty:
            return None
        gt = inter.geom_type
        if gt == "Point":
            d = float(base.distance(inter))
            return d if d >= float(NORMAL_RAY_MIN_HIT_M) else None
        if gt in ("MultiPoint", "GeometryCollection"):
            vals = []
            for g in inter.geoms:
                d = ContourSideIndex._intersection_distance(base, g)
                if d is not None:
                    vals.append(d)
            return min(vals) if vals else None
        if gt in ("LineString", "LinearRing"):
            # Collinear/overlap is unusual for different elevations.  Use the
            # nearest endpoint strictly away from the candidate as a conservative hit.
            vals = []
            for xy in list(inter.coords):
                d = float(base.distance(Point(xy[0], xy[1])))
                if d >= float(NORMAL_RAY_MIN_HIT_M):
                    vals.append(d)
            return min(vals) if vals else None
        if gt == "MultiLineString":
            vals = []
            for g in inter.geoms:
                d = ContourSideIndex._intersection_distance(base, g)
                if d is not None:
                    vals.append(d)
            return min(vals) if vals else None
        return None

    def _first_hit(self, base: Point, nx: float, ny: float, current_elev: float):
        end = Point(
            base.x + float(nx) * float(NORMAL_RAY_MAX_M),
            base.y + float(ny) * float(NORMAL_RAY_MAX_M),
        )
        ray = LineString([(base.x, base.y), (end.x, end.y)])
        # Buffer only for the spatial-index candidate query.  Actual ranking uses
        # exact intersection with the unbuffered ray, so off-ray contours cannot win.
        query_geom = ray.buffer(float(NORMAL_RAY_QUERY_BUFFER_M), cap_style=2)
        try:
            idxs = self.tree.query(query_geom, predicate="intersects")
        except Exception:
            idxs = self.tree.query(query_geom)

        best = None
        for idx in idxs:
            i = int(idx)
            z = self.elev[i]
            if not np.isfinite(z) or abs(float(z) - float(current_elev)) <= ELEV_EQUAL_TOL:
                continue
            g = self.geoms[i]
            try:
                inter = g.intersection(ray)
            except Exception:
                continue
            d = self._intersection_distance(base, inter)
            if d is None or d > float(NORMAL_RAY_MAX_M) + 1e-9:
                continue
            if best is None or d < best[0]:
                best = (float(d), float(z), int(i))
        return best

    @staticmethod
    def _vote_from_hits(current_elev: float, left_hit, right_hit):
        """Return (side, weight, reason) for one cross section."""
        lz = left_hit[1] if left_hit is not None else None
        rz = right_hit[1] if right_hit is not None else None

        if lz is not None and rz is not None:
            ldz = float(lz) - float(current_elev)
            rdz = float(rz) - float(current_elev)
            # The clean, expected contour relationship: one adjacent contour higher,
            # the other lower than the current contour.
            if ldz > ELEV_EQUAL_TOL and rdz < -ELEV_EQUAL_TOL:
                return "LEFT", float(TWO_SIDED_SECTION_WEIGHT), "BRACKET"
            if ldz < -ELEV_EQUAL_TOL and rdz > ELEV_EQUAL_TOL:
                return "RIGHT", float(TWO_SIDED_SECTION_WEIGHT), "BRACKET"
            # Both sides above or both below is locally ambiguous (ridge/valley fold,
            # saddle, ray hitting another loop, etc.).  Do not guess.
            return None, 0.0, "AMBIGUOUS_BOTH_SIDES"

        if not ALLOW_ONE_SIDED_ADJACENT_EVIDENCE:
            return None, 0.0, "ONE_SIDE_DISABLED"

        if lz is not None:
            # Left higher => left is high side; left lower => right is high side.
            return (
                "LEFT" if float(lz) > float(current_elev) else "RIGHT",
                float(ONE_SIDED_SECTION_WEIGHT),
                "LEFT_ONLY",
            )
        if rz is not None:
            # Right higher => right is high side; right lower => left is high side.
            return (
                "RIGHT" if float(rz) > float(current_elev) else "LEFT",
                float(ONE_SIDED_SECTION_WEIGHT),
                "RIGHT_ONLY",
            )
        return None, 0.0, "NO_HIT"

    def judge(self, point: Point, tangent_az: float, current_elev: float):
        ux, uy, lx, ly, rx, ry = self._normal_vectors(tangent_az)
        sections = []
        votes = []

        for off in NORMAL_SECTION_ALONG_OFFSETS_M:
            base = Point(point.x + ux * float(off), point.y + uy * float(off))
            lh = self._first_hit(base, lx, ly, current_elev)
            rh = self._first_hit(base, rx, ry, current_elev)
            side, weight, why = self._vote_from_hits(current_elev, lh, rh)
            sec = {
                "offset": float(off),
                "left_z": np.nan if lh is None else float(lh[1]),
                "left_d": np.nan if lh is None else float(lh[0]),
                "right_z": np.nan if rh is None else float(rh[1]),
                "right_d": np.nan if rh is None else float(rh[0]),
                "vote": side,
                "weight": float(weight),
                "why": why,
            }
            sections.append(sec)
            if side is not None and weight > 0:
                votes.append((side, float(weight)))

        # Prefer diagnostics from the centre section (offset closest to zero).
        centre = min(sections, key=lambda x: abs(x["offset"])) if sections else None
        two_sided_sections = sum(1 for x in sections if x["why"] == "BRACKET")
        one_sided_sections = sum(1 for x in sections if x["why"] in ("LEFT_ONLY", "RIGHT_ONLY"))
        diag = {
            "near_l_z": np.nan if centre is None else centre["left_z"],
            "near_l_d": np.nan if centre is None else centre["left_d"],
            "near_r_z": np.nan if centre is None else centre["right_z"],
            "near_r_d": np.nan if centre is None else centre["right_d"],
            "section_votes": int(len(votes)),
            "two_sided_sections": int(two_sided_sections),
            "one_sided_sections": int(one_sided_sections),
            "section_detail": " | ".join(
                "off={:.1f}:L={}/{} R={}/{} {}".format(
                    x["offset"],
                    "NA" if not np.isfinite(x["left_z"]) else format_elev(x["left_z"]),
                    "NA" if not np.isfinite(x["left_d"]) else "{:.1f}m".format(x["left_d"]),
                    "NA" if not np.isfinite(x["right_z"]) else format_elev(x["right_z"]),
                    "NA" if not np.isfinite(x["right_d"]) else "{:.1f}m".format(x["right_d"]),
                    x["why"],
                ) for x in sections
            )[:250],
        }

        if len(votes) < int(MIN_ADJACENT_SECTION_VOTES):
            return self._fail("INSUFFICIENT_ADJACENT_SECTIONS", diag=diag)

        left_score = sum(w for side, w in votes if side == "LEFT")
        right_score = sum(w for side, w in votes if side == "RIGHT")
        total = left_score + right_score
        if total <= 0:
            return self._fail("NO_ADJACENT_CONTOUR_EVIDENCE", diag=diag)
        if abs(left_score - right_score) < 1e-12:
            return self._fail("ADJACENT_SIDE_TIE", votes=len(votes), conf=0.5, diag=diag)

        side = "LEFT" if left_score > right_score else "RIGHT"
        conf = max(left_score, right_score) / total
        if conf + 1e-12 < float(MIN_ADJACENT_SIDE_CONSISTENCY):
            return self._fail("LOW_ADJACENT_SIDE_CONSISTENCY", votes=len(votes), conf=conf, diag=diag)

        out = {
            "status": "OK",
            "higher_side": side,
            "confidence": float(conf),
            "votes": int(len(votes)),
            "left_score": float(left_score),
            "right_score": float(right_score),
        }
        out.update(diag)
        return out

    @staticmethod
    def _fail(status, votes=0, conf=0.0, diag=None):
        out = {
            "status": status,
            "higher_side": None,
            "confidence": float(conf),
            "votes": int(votes),
            "left_score": np.nan,
            "right_score": np.nan,
            "near_l_z": np.nan,
            "near_l_d": np.nan,
            "near_r_z": np.nan,
            "near_r_d": np.nan,
            "section_votes": 0,
            "two_sided_sections": 0,
            "one_sided_sections": 0,
            "section_detail": "",
        }
        if diag:
            out.update(diag)
        return out


# =====================================================================
# 8. DEM FALLBACK - BATCH SAMPLING
# =====================================================================

class DEMBatchSampler:
    def __init__(self, dem_path: str, vector_crs):
        self.src = rasterio.open(dem_path)
        if self.src.crs is None:
            raise RuntimeError("DEM has no CRS: {}".format(dem_path))
        self.vector_crs = CRS.from_user_input(vector_crs)
        self.dem_crs = CRS.from_user_input(self.src.crs)
        self.transformer = None
        if self.vector_crs != self.dem_crs:
            self.transformer = Transformer.from_crs(
                self.vector_crs, self.dem_crs, always_xy=True
            )
        self.nodata = self.src.nodata

    def close(self):
        self.src.close()

    def _transform(self, coords):
        if self.transformer is None:
            return coords
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        xx, yy = self.transformer.transform(xs, ys)
        return list(zip(xx, yy))

    def _sample_many(self, coords):
        if not coords:
            return []
        coords2 = self._transform(coords)
        vals = []
        for arr in self.src.sample(coords2, indexes=1, masked=True):
            try:
                v0 = arr[0]
                if np.ma.is_masked(v0):
                    vals.append(np.nan); continue
                v = float(v0)
                if self.nodata is not None and abs(v - float(self.nodata)) < 1e-8:
                    vals.append(np.nan)
                elif not np.isfinite(v) or abs(v) >= 1e20:
                    vals.append(np.nan)
                else:
                    vals.append(v)
            except Exception:
                vals.append(np.nan)
        return vals

    def judge_batch(self, records):
        """Return one DEM-side result for each input record, preserving order."""
        if not records:
            return []

        coords = []
        meta = []  # (record_i, dist_i, side)
        for ri, rec in enumerate(records):
            p = rec["geometry"]
            az = float(rec["TANG_AZ"])
            rad = math.radians(az)
            ux, uy = math.sin(rad), math.cos(rad)
            lx, ly = -uy, ux
            rx, ry = uy, -ux
            for di, sd in enumerate(SIDE_DISTANCES_M):
                for side, nx, ny in [("L", lx, ly), ("R", rx, ry)]:
                    for along in ALONG_OFFSETS_M:
                        bx = p.x + ux * float(along)
                        by = p.y + uy * float(along)
                        coords.append((bx + nx * float(sd), by + ny * float(sd)))
                        meta.append((ri, di, side))

        vals = self._sample_many(coords)
        buckets = defaultdict(list)
        for m, v in zip(meta, vals):
            if np.isfinite(v):
                buckets[m].append(float(v))

        results = []
        for ri, rec in enumerate(records):
            votes = []
            lmeds, rmeds = [], []
            for di, sd in enumerate(SIDE_DISTANCES_M):
                lv = buckets.get((ri, di, "L"), [])
                rv = buckets.get((ri, di, "R"), [])
                if not lv or not rv:
                    continue
                lm = float(np.median(lv)); rm = float(np.median(rv))
                lmeds.append(lm); rmeds.append(rm)
                dz = lm - rm
                if abs(dz) >= MIN_SIDE_DZ_M:
                    votes.append("LEFT" if dz > 0 else "RIGHT")

            if len(votes) < MIN_VALID_SIDE_VOTES:
                results.append(self._fail("INSUFFICIENT_DEM_EVIDENCE", len(votes)))
                continue
            ln, rn = votes.count("LEFT"), votes.count("RIGHT")
            if ln == rn:
                results.append(self._fail("DEM_SIDE_TIE", len(votes), 0.5)); continue
            side = "LEFT" if ln > rn else "RIGHT"
            conf = max(ln, rn) / float(len(votes))
            if conf < MIN_DEM_SIDE_CONSISTENCY:
                results.append(self._fail("LOW_DEM_SIDE_CONSISTENCY", len(votes), conf)); continue
            lm = float(np.median(lmeds)) if lmeds else np.nan
            rm = float(np.median(rmeds)) if rmeds else np.nan
            results.append({
                "status": "OK",
                "higher_side": side,
                "confidence": float(conf),
                "votes": int(len(votes)),
                "dem_l": lm,
                "dem_r": rm,
                "dem_dz": lm - rm if np.isfinite(lm) and np.isfinite(rm) else np.nan,
            })
        return results

    @staticmethod
    def _fail(status, votes=0, conf=0.0):
        return {
            "status": status,
            "higher_side": None,
            "confidence": float(conf),
            "votes": int(votes),
            "dem_l": np.nan,
            "dem_r": np.nan,
            "dem_dz": np.nan,
        }


# =====================================================================
# 9. ORIENTATION + FINAL PER-CONTOUR SELECTION
# =====================================================================

def text_top_north_deviation(az: float) -> float:
    """Absolute angular deviation of the text-top/high-side normal from north."""
    return angular_diff(float(az) % 360.0, 0.0)


def text_top_direction_class(az: float) -> int:
    """2 = preferred northward normal (<=45 deg), 1 = acceptable (<=75 deg), 0 = invalid."""
    dev = text_top_north_deviation(az)
    if dev <= float(BEST_TEXT_TOP_DEVIATION_FROM_NORTH_DEG) + 1e-9:
        return 2
    if dev <= float(MAX_TEXT_TOP_DEVIATION_FROM_NORTH_DEG) + 1e-9:
        return 1
    return 0


def orient_record(rec, higher_side, side_source, confidence, votes,
                  near_l_z=np.nan, near_l_d=np.nan, near_r_z=np.nan, near_r_d=np.nan,
                  side_detail="", dem_l=np.nan, dem_r=np.nan, dem_dz=np.nan):
    # TANG_AZ is the local contour tangent. Rotate the baseline so its text-top normal
    # points to the higher side. We never flip the label independently of high-side.
    tang = float(rec["TANG_AZ"])
    angle = tang if higher_side == "LEFT" else (tang + 180.0) % 360.0
    text_top_az = (angle - 90.0) % 360.0
    up_score = northward_preference_score(text_top_az)
    north_dev = text_top_north_deviation(text_top_az)
    direction_class = text_top_direction_class(text_top_az)

    out = rec.copy()
    out.update({
        "LABEL_TXT": format_elev(rec[ELEV_FIELD]),
        "HIGH_SIDE": higher_side,
        "SIDE_SRC": side_source,
        "SIDE_CONF": float(confidence),
        "SIDE_VOTE": int(votes),
        "ANGLE_GEO": float(angle),
        "TOP_AZ": float(text_top_az),
        "TOP_NORTH_DEV": float(north_dev),
        "TOP_DIR_CLASS": int(direction_class),
        "UP_SCORE": float(up_score),
        "NEAR_L_Z": near_l_z,
        "NEAR_L_D": near_l_d,
        "NEAR_R_Z": near_r_z,
        "NEAR_R_D": near_r_d,
        "SIDE_DETAIL": str(side_detail)[:250],
        "DEM_L": dem_l,
        "DEM_R": dem_r,
        "DEM_DZ": dem_dz,
    })

    # Hard cartographic rule: the text-top normal must point to higher terrain and
    # lie inside the northward cone. This excludes due east/west and all southward
    # directions; another candidate is sought along the same contour if this fails.
    if REQUIRE_TEXT_TOP_NORTH_CONE and north_dev > float(MAX_TEXT_TOP_DEVIATION_FROM_NORTH_DEG) + 1e-9:
        out["REJECT_REASON"] = "TEXT_TOP_OUTSIDE_NORTH_CONE"
        return None, out

    # With the north-cone rule, ANGLE_GEO should naturally lie between 15 and 165 deg.
    # Keep the check as a safeguard for ArcMap readability.
    if not (0.0 <= angle <= 180.0):
        out["REJECT_REASON"] = "UPSIDE_DOWN_IF_HIGH_SIDE_CORRECT"
        return None, out

    out["DIR_OK"] = 1
    return out, None


def _quality_tuple(r):
    """Rank the desired northward normal first, then line and placement quality."""
    frame = float(r.get("FRAME_CLR", 0.0))
    if not np.isfinite(frame):
        frame = 0.0
    top_az = float(r.get("TOP_AZ", 180.0))
    dir_class = int(r.get("TOP_DIR_CLASS", text_top_direction_class(top_az)))
    return (
        dir_class,                         # <=45 deg from north wins over 45-75 deg
        float(r.get("UP_SCORE", 0.0)),    # within class, closer to north wins
        int(r.get("FLAT_CLASS", 0)),      # candidates already passed hard flatness
        float(r.get("FLAT_SCORE", 0.0)),
        float(r.get("CENTER_SC", 0.0)),
        float(r.get("SIDE_CONF", 0.0)),
        1 if str(r.get("SIDE_SRC", "")).startswith("ADJ_CONTOUR") else 0,
        frame,
        -abs(float(r.get("STATION_M", 0.0)) - 0.5 * float(r.get("LINE_LEN", 0.0))),
    )


def _candidate_identity(r):
    return (
        str(r.get("LINE_UID")),
        str(r.get(TFBH_FIELD)),
        round(float(r.get("STATION_M", 0.0)), 3),
        round(float(r.get(ELEV_FIELD, 0.0)), 6),
    )


def select_labels_with_elevation_coverage(records, rejects, required_index_elevations):
    """Select moderate-density labels while guaranteeing index-elevation coverage.

    The cartographic hard rules have already been enforced before this function:
    safe map-sheet interior, open-line endpoint clearance, higher-side correctness,
    non-southward text top, and readable ArcMap angle.

    Stage 0 (coverage seeds): for every index-contour elevation that exists in the
    source data, lock the best valid candidate.  These seeds cannot later be removed
    by density thinning.  This prevents entire elevation levels (e.g. 150/300/450)
    from disappearing simply because a global quality threshold was too strict.

    Stage A: add at most one best candidate per logical contour per map sheet.
    Stage B: suppress locally repetitive equal-elevation labels in map space, but
    never suppress a mandatory coverage seed.
    """
    if not records:
        return [], []

    required = sorted({round(float(x), 6) for x in required_index_elevations})
    by_elev = defaultdict(list)
    for r in records:
        by_elev[round(float(r[ELEV_FIELD]), 6)].append(r)

    # 0. Mandatory coverage seeds.
    seeds = []
    seed_ids = set()
    missing_no_valid_candidate = []
    n_need = max(1, int(MIN_LABELS_PER_INDEX_ELEV))
    for elev in required:
        grp = sorted(by_elev.get(elev, []), key=_quality_tuple, reverse=True)
        if not grp:
            missing_no_valid_candidate.append(elev)
            continue
        for r0 in grp[:n_need]:
            r = r0.copy()
            r["SELECT_STAGE"] = "ELEV_COVERAGE"
            r["COVERAGE_SEED"] = 1
            seeds.append(r)
            seed_ids.add(_candidate_identity(r))

    # A. Best candidate per logical contour / map sheet. Coverage seeds are fixed.
    groups = defaultdict(list)
    for r in records:
        groups[(r.get("LINE_UID"), str(r.get(TFBH_FIELD)))].append(r)

    def mark_not_selected(candidate, winner):
        rr = candidate.copy()
        rr["REJECT_REASON"] = "NOT_SELECTED_SAME_LINE_SHEET"
        # Store the selected alternative's metrics for transparent QA comparison.
        rr["WIN_STATION_M"] = float(winner.get("STATION_M", np.nan))
        rr["WIN_TOP_AZ"] = float(winner.get("TOP_AZ", np.nan))
        rr["WIN_TOP_DEV"] = float(winner.get("TOP_NORTH_DEV", np.nan))
        rr["WIN_UP_SCORE"] = float(winner.get("UP_SCORE", np.nan))
        rr["WIN_FLAT_CLASS"] = int(winner.get("FLAT_CLASS", 0))
        rr["WIN_FLAT_SCORE"] = float(winner.get("FLAT_SCORE", np.nan))
        rr["WIN_CENTER_SC"] = float(winner.get("CENTER_SC", np.nan))
        rr["WIN_FRAME_CLR"] = float(winner.get("FRAME_CLR", np.nan))
        rr["WIN_DIR_CLASS"] = int(winner.get("TOP_DIR_CLASS", 0))
        rejects.append(rr)

    primary = list(seeds)
    primary_ids = set(seed_ids)
    for key, grp in groups.items():
        grp = sorted(grp, key=_quality_tuple, reverse=True)
        seed_in_group = [r for r in grp if _candidate_identity(r) in seed_ids]
        if seed_in_group:
            # The coverage seed occupies this line/sheet slot and cannot be displaced.
            winner = seed_in_group[0]
            for rr0 in grp:
                if _candidate_identity(rr0) in seed_ids:
                    continue
                mark_not_selected(rr0, winner)
            continue

        keep_n = max(1, int(MAX_LABELS_PER_LINE_PER_SHEET))
        winners = grp[:keep_n]
        for r0 in winners:
            rid = _candidate_identity(r0)
            if rid in primary_ids:
                continue
            r = r0.copy()
            r["SELECT_STAGE"] = "NORMAL"
            r["COVERAGE_SEED"] = 0
            primary.append(r)
            primary_ids.add(rid)
        winner = winners[0] if winners else None
        for rr0 in grp[keep_n:]:
            if winner is not None:
                mark_not_selected(rr0, winner)
            else:
                rr = rr0.copy()
                rr["REJECT_REASON"] = "NOT_SELECTED_SAME_LINE_SHEET"
                rejects.append(rr)

    # B. Local equal-elevation thinning. Seeds are always processed first/protected.
    final = []
    elev_groups = defaultdict(list)
    for r in primary:
        elev_groups[(str(r.get(TFBH_FIELD)), round(float(r[ELEV_FIELD]), 6))].append(r)

    min_d = float(MIN_SAME_ELEV_LABEL_DISTANCE_M)
    for key, grp in elev_groups.items():
        grp = sorted(
            grp,
            key=lambda r: (int(r.get("COVERAGE_SEED", 0)),) + _quality_tuple(r),
            reverse=True,
        )
        kept = []
        for r in grp:
            is_seed = int(r.get("COVERAGE_SEED", 0)) == 1
            if is_seed:
                kept.append(r)
                continue
            too_close = any(r["geometry"].distance(k["geometry"]) < min_d for k in kept)
            if too_close:
                rr = r.copy()
                rr["REJECT_REASON"] = "NOT_SELECTED_LOCAL_SAME_ELEV"
                rejects.append(rr)
            else:
                kept.append(r)
        final.extend(kept)

    # Final coverage verification (citywide, based on actual source index elevations).
    counts = defaultdict(int)
    for r in final:
        counts[round(float(r[ELEV_FIELD]), 6)] += 1
    missing_after = [e for e in required if counts.get(e, 0) < n_need]
    # If a seed somehow disappeared (should not happen), re-add it defensively.
    if missing_after:
        for elev in list(missing_after):
            options = [r for r in seeds if round(float(r[ELEV_FIELD]), 6) == elev]
            for r in options:
                if _candidate_identity(r) not in {_candidate_identity(x) for x in final}:
                    final.append(r)
            if sum(round(float(x[ELEV_FIELD]), 6) == elev for x in final) >= n_need:
                missing_after.remove(elev)

    return final, sorted(set(missing_no_valid_candidate + missing_after))

def build_line_audit(logical_lines, final_records, rejects):
    """One QA point per logical line so a missing 900 m contour can be diagnosed."""
    final_count = defaultdict(int)
    for r in final_records:
        final_count[r.get("LINE_UID")] += 1

    reason_count = defaultdict(lambda: defaultdict(int))
    for r in rejects:
        uid = r.get("LINE_UID")
        reason = r.get("REJECT_REASON")
        if uid is not None and reason:
            reason_count[uid][str(reason)] += 1

    rows = []
    for row in logical_lines.itertuples(index=False):
        d = row._asdict()
        uid = d.get("LINE_UID")
        geom = row.geometry
        reasons = reason_count.get(uid, {})
        if reasons:
            dom = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            detail = ";".join("{}:{}".format(k, v) for k, v in sorted(reasons.items()))
        else:
            dom = "NONE"
            detail = ""
        rows.append({
            "LINE_UID": uid,
            ELEV_FIELD: float(d[ELEV_FIELD]),
            OBJE_CODE_FIELD: d[OBJE_CODE_FIELD],
            "IS_INDEX": int(d.get("IS_INDEX", 0)),
            "LINE_LEN": float(d.get("LINE_LEN", geom.length)),
            "IS_CLOSED": int(d.get("IS_CLOSED", 0)),
            "FINAL_N": int(final_count.get(uid, 0)),
            "DOM_REASON": dom,
            "REASON_SUM": detail[:250],
            "geometry": geom.interpolate(0.5, normalized=True),
        })
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=logical_lines.crs)


# =====================================================================
# 10. GDB OUTPUT
# =====================================================================

def prepare_output_gdb():
    ensure_parent(OUTPUT_GDB)
    drv = pyogrio.list_drivers().get("OpenFileGDB", "")
    if "w" not in str(drv).lower():
        raise RuntimeError(
            "Current Python/GDAL cannot WRITE FileGDB. OpenFileGDB driver={!r}. "
            "Use a Python 3.10 GIS environment where OpenFileGDB is rw."
            .format(drv)
        )
    if os.path.exists(OUTPUT_GDB):
        if OVERWRITE_OUTPUT_GDB:
            log("Deleting dedicated output GDB: {}".format(OUTPUT_GDB))
            shutil.rmtree(OUTPUT_GDB)
        else:
            raise RuntimeError("OUTPUT_GDB exists and overwrite is disabled: {}".format(OUTPUT_GDB))


def sanitize_for_gdb(gdf: gpd.GeoDataFrame):
    out = gdf.copy()
    for c in list(out.columns):
        if c.startswith("_"):
            out.drop(columns=[c], inplace=True)
    for c in out.columns:
        if c == out.geometry.name:
            continue
        if pd.api.types.is_integer_dtype(out[c].dtype):
            if out[c].isna().any():
                out[c] = out[c].astype("float64")
            else:
                mn = int(out[c].min()) if len(out) else 0
                mx = int(out[c].max()) if len(out) else 0
                if -2147483648 <= mn <= mx <= 2147483647:
                    out[c] = out[c].astype(np.int32)
                else:
                    out[c] = out[c].astype("float64")
    return out


def write_gdb_layer(gdf: gpd.GeoDataFrame, layer: str):
    if gdf.empty:
        log("Skip empty layer: {}".format(layer)); return
    out = sanitize_for_gdb(gdf)
    pyogrio.write_dataframe(
        out,
        OUTPUT_GDB,
        layer=layer,
        driver="OpenFileGDB",
        append=False,
        use_arrow=False,
    )
    log("Wrote {} features -> {}\\{}".format(len(out), OUTPUT_GDB, layer))


# =====================================================================
# 11. INPUT / TEST-AREA PREPARATION
# =====================================================================

def read_gdb_layer_with_fid(gdb, layer):
    try:
        g = pyogrio.read_dataframe(gdb, layer=layer, fid_as_index=True)
        g = g.reset_index().rename(columns={g.index.name or "index": "SRC_FID"})
        if "SRC_FID" not in g.columns:
            # reset_index() may have used 'fid'.
            first = g.columns[0]
            if first not in [ELEV_FIELD, OBJE_CODE_FIELD, "geometry"]:
                g.rename(columns={first: "SRC_FID"}, inplace=True)
        return g
    except Exception:
        g = pyogrio.read_dataframe(gdb, layer=layer)
        g = g.copy()
        g["SRC_FID"] = np.arange(len(g), dtype=np.int64)
        return g


def validate_inputs(contours, sheets):
    for p, name in [(CONTOUR_GDB, "CONTOUR_GDB"), (SHEET_GDB, "SHEET_GDB")]:
        if not os.path.exists(p):
            raise RuntimeError("{} does not exist: {}".format(name, p))
    if USE_DEM_FALLBACK and not os.path.exists(DEM_PATH):
        raise RuntimeError("USE_DEM_FALLBACK=True but DEM does not exist: {}".format(DEM_PATH))
    for f in [ELEV_FIELD, OBJE_CODE_FIELD]:
        if f not in contours.columns:
            raise RuntimeError("Contour field not found: {}".format(f))
    if TFBH_FIELD not in sheets.columns:
        raise RuntimeError("Sheet field not found: {}".format(TFBH_FIELD))
    if contours.crs is None or sheets.crs is None:
        raise RuntimeError("Contour and sheet CRS must both be defined.")
    if contours.crs.is_geographic:
        raise RuntimeError("Contour CRS must be projected in metres, not geographic degrees.")
    if LABEL_SCOPE not in {"index_only", "all", "both"}:
        raise RuntimeError("LABEL_SCOPE must be index_only / all / both.")


def select_test_sheets(sheets: gpd.GeoDataFrame):
    if ONLY_TFBH is None:
        return sheets.copy()
    wanted = {str(x) for x in ONLY_TFBH}
    out = sheets[sheets[TFBH_FIELD].astype(str).isin(wanted)].copy()
    if out.empty:
        raise RuntimeError("ONLY_TFBH selected no sheets: {}".format(ONLY_TFBH))
    log("TEST MODE sheets: {}".format(sorted(wanted)))
    return out


def restrict_contours_for_test(contours, selected_sheets):
    if ONLY_TFBH is None:
        return contours.copy()
    # Keep FULL source contour features that intersect a buffered analysis area.
    # Do not clip them at the sheet frame, so cross-sheet terrain relationships remain.
    # GeoPandas 0.14.x compatibility:
    # GeoSeries.union_all() was introduced after the production environment.
    # Use unary_union as a fallback; both produce one merged geometry here.
    try:
        area_geom = selected_sheets.geometry.union_all()
    except AttributeError:
        area_geom = selected_sheets.geometry.unary_union

    area = area_geom.buffer(
        max(NORMAL_RAY_MAX_M, frame_safe_margin_m()) + 100.0
    )
    idx = contours.sindex.query(area, predicate="intersects")
    return contours.iloc[idx].copy().reset_index(drop=True)


# =====================================================================
# 12. MAIN
# =====================================================================

def main():
    t0 = time.time()
    log("============================================================")
    log("Contour label workflow PRODUCTION V3.4 ADJACENT-FIRST HIGH-SIDE started")
    log("Python: {}".format(sys.executable))
    log("pyogrio {} / GDAL {} / OpenFileGDB {}".format(
        pyogrio.__version__,
        getattr(pyogrio, "__gdal_version_string__", getattr(pyogrio, "__gdal_version__", "unknown")),
        pyogrio.list_drivers().get("OpenFileGDB")
    ))
    log("LABEL_SCOPE = {}".format(LABEL_SCOPE))
    log("High-side priority: first adjacent contour on normal rays -> DEM consistency/fallback -> reject")
    log("Natural gaps are never bridged; map-sheet frame is placement-only.")
    log("Placement: dense search -> adjacent-first high-side -> DEM consistency -> north-cone direction -> best site.")

    log("Reading contour GDB...")
    contours = read_gdb_layer_with_fid(CONTOUR_GDB, CONTOUR_LAYER)
    log("Contours read: {}".format(len(contours)))

    log("Reading 1:10,000 sheet GDB...")
    sheets = pyogrio.read_dataframe(SHEET_GDB, layer=SHEET_LAYER)
    log("Sheets read: {}".format(len(sheets)))

    validate_inputs(contours, sheets)
    if sheets.crs != contours.crs:
        log("Reprojecting sheets to contour CRS: {}".format(contours.crs))
        sheets = sheets.to_crs(contours.crs)

    contours = contours.copy()
    contours[ELEV_FIELD] = pd.to_numeric(contours[ELEV_FIELD], errors="coerce")
    contours = contours[contours[ELEV_FIELD].notna()].copy()
    contours = contours[contours.geometry.notna()].copy()
    contours = contours[~contours.geometry.is_empty].copy()
    if "SRC_FID" not in contours.columns:
        contours["SRC_FID"] = np.arange(len(contours), dtype=np.int64)

    selected_sheets = select_test_sheets(sheets)
    analysis_contours = restrict_contours_for_test(contours, selected_sheets)
    log("Contours in analysis set: {}".format(len(analysis_contours)))

    # Index code is resolved even in BOTH/ALL mode so IS_INDEX can be preserved.
    diag_csv = os.path.join(os.path.dirname(OUTPUT_GDB), "OBJE_CODE_index_contour_diagnostic.csv")
    index_codes, index_mode = resolve_index_codes(analysis_contours, diag_csv)
    analysis_contours = mark_index_contours(analysis_contours, index_codes)
    log("Index-identification mode: {}".format(index_mode))
    log("Index source features: {} / {}".format(
        int(analysis_contours["IS_INDEX"].sum()), len(analysis_contours)
    ))

    # All contours remain evidence regardless of output scope.
    evidence_contours = analysis_contours.copy()

    if LABEL_SCOPE == "index_only":
        label_source = analysis_contours[analysis_contours["IS_INDEX"] == 1].copy()
    else:
        label_source = analysis_contours.copy()
    if label_source.empty:
        raise RuntimeError("No contours remain for LABEL_SCOPE={}.".format(LABEL_SCOPE))

    log("Building logical contour lines; open water gaps will remain open...")
    logical = build_logical_lines(label_source)
    if logical.empty:
        raise RuntimeError("No logical contour lines were produced.")
    log("Logical lines: {}".format(len(logical)))

    raw_candidates, rejects = generate_raw_candidates(logical)
    log("Geometry-valid candidates before frame test: {}".format(len(raw_candidates)))

    assigner = SheetAssigner(selected_sheets)
    candidates = apply_sheet_assignment(raw_candidates, rejects, assigner)
    log("Candidates inside safe sheet interiors: {}".format(len(candidates)))

    side_index = ContourSideIndex(evidence_contours)
    oriented = []
    contour_resolved = []
    fallback = []

    log("Primary higher-side judgement: first adjacent contour along left/right normal rays...")
    for rec in tqdm(candidates, desc="Adjacent-contour side"):
        side = side_index.judge(rec["geometry"], rec["TANG_AZ"], rec[ELEV_FIELD])
        if side["status"] == "OK":
            rr = rec.copy()
            rr["_SIDE_RESULT"] = side
            contour_resolved.append(rr)
        else:
            rr = rec.copy()
            rr["CONTOUR_FAIL"] = side["status"]
            rr["CONTOUR_CONF"] = side.get("confidence", 0.0)
            rr["CONTOUR_VOTE"] = side.get("votes", 0)
            rr["NEAR_L_Z"] = side.get("near_l_z", np.nan)
            rr["NEAR_L_D"] = side.get("near_l_d", np.nan)
            rr["NEAR_R_Z"] = side.get("near_r_z", np.nan)
            rr["NEAR_R_D"] = side.get("near_r_d", np.nan)
            rr["SIDE_DETAIL"] = side.get("section_detail", "")
            fallback.append(rr)

    log("Adjacent-contour resolved candidates: {}".format(len(contour_resolved)))
    log("Need DEM fallback because adjacent contours were insufficient/ambiguous: {}".format(len(fallback)))

    # DEM is used in two roles:
    # 1) consistency check for adjacent-contour decisions;
    # 2) fallback when adjacent-contour evidence is insufficient.
    if USE_DEM_FALLBACK and (contour_resolved or fallback):
        dem = DEMBatchSampler(DEM_PATH, contours.crs)
        try:
            resolved_dem = dem.judge_batch(contour_resolved) if (VALIDATE_ADJACENT_SIDE_WITH_DEM and contour_resolved) else [None] * len(contour_resolved)
            fallback_dem = dem.judge_batch(fallback) if fallback else []
        finally:
            dem.close()

        for rec, dem_side in zip(contour_resolved, resolved_dem):
            side = rec.pop("_SIDE_RESULT")
            source = "ADJ_CONTOUR"
            dem_l = dem_r = dem_dz = np.nan
            if dem_side is not None and dem_side.get("status") == "OK":
                dem_l = dem_side.get("dem_l", np.nan)
                dem_r = dem_side.get("dem_r", np.nan)
                dem_dz = dem_side.get("dem_dz", np.nan)
                if dem_side["higher_side"] != side["higher_side"]:
                    # Two-sided immediate bracketing on >=2 cross sections is stronger
                    # than the derived DEM.  Keep that direct contour evidence and only
                    # record the DEM conflict for QA.  One-sided inference is weaker;
                    # a decisive DEM conflict rejects that candidate so another location
                    # on the same contour can be tried.
                    if int(side.get("two_sided_sections", 0)) >= 2:
                        source = "ADJ_CONTOUR_STRONG_DEM_CONFLICT"
                    else:
                        rr = rec.copy()
                        rr.update({
                            "HIGH_SIDE": side["higher_side"],
                            "SIDE_SRC": "CONFLICT",
                            "SIDE_CONF": side.get("confidence", 0.0),
                            "SIDE_VOTE": side.get("votes", 0),
                            "NEAR_L_Z": side.get("near_l_z", np.nan),
                            "NEAR_L_D": side.get("near_l_d", np.nan),
                            "NEAR_R_Z": side.get("near_r_z", np.nan),
                            "NEAR_R_D": side.get("near_r_d", np.nan),
                            "SIDE_DETAIL": side.get("section_detail", ""),
                            "DEM_L": dem_l,
                            "DEM_R": dem_r,
                            "DEM_DZ": dem_dz,
                            "REJECT_REASON": "ONE_SIDED_CONTOUR_DEM_CONFLICT",
                        })
                        if REJECT_CONTOUR_DEM_CONFLICT:
                            rejects.append(rr)
                            continue
                else:
                    source = "ADJ_CONTOUR+DEM"

            ok, rej = orient_record(
                rec,
                side["higher_side"],
                source,
                side["confidence"],
                side["votes"],
                near_l_z=side.get("near_l_z", np.nan),
                near_l_d=side.get("near_l_d", np.nan),
                near_r_z=side.get("near_r_z", np.nan),
                near_r_d=side.get("near_r_d", np.nan),
                side_detail=side.get("section_detail", ""),
                dem_l=dem_l,
                dem_r=dem_r,
                dem_dz=dem_dz,
            )
            if ok is not None:
                oriented.append(ok)
            else:
                rejects.append(rej)

        for rec, side in zip(fallback, fallback_dem):
            if side["status"] == "OK":
                ok, rej = orient_record(
                    rec,
                    side["higher_side"],
                    "DEM",
                    side["confidence"],
                    side["votes"],
                    near_l_z=rec.get("NEAR_L_Z", np.nan),
                    near_l_d=rec.get("NEAR_L_D", np.nan),
                    near_r_z=rec.get("NEAR_R_Z", np.nan),
                    near_r_d=rec.get("NEAR_R_D", np.nan),
                    side_detail=rec.get("SIDE_DETAIL", ""),
                    dem_l=side["dem_l"],
                    dem_r=side["dem_r"],
                    dem_dz=side["dem_dz"],
                )
                if ok is not None:
                    oriented.append(ok)
                else:
                    rejects.append(rej)
            else:
                rr = rec.copy()
                rr["SIDE_SRC"] = "UNRESOLVED"
                rr["REJECT_REASON"] = side["status"]
                rejects.append(rr)
    else:
        # No DEM available.  Trust only the adjacent-contour decisions that already
        # passed the multi-section consistency requirement; unresolved candidates reject.
        for rec in contour_resolved:
            side = rec.pop("_SIDE_RESULT")
            ok, rej = orient_record(
                rec,
                side["higher_side"],
                "ADJ_CONTOUR",
                side["confidence"],
                side["votes"],
                near_l_z=side.get("near_l_z", np.nan),
                near_l_d=side.get("near_l_d", np.nan),
                near_r_z=side.get("near_r_z", np.nan),
                near_r_d=side.get("near_r_d", np.nan),
                side_detail=side.get("section_detail", ""),
            )
            if ok is not None:
                oriented.append(ok)
            else:
                rejects.append(rej)
        for rec in fallback:
            rr = rec.copy()
            rr["SIDE_SRC"] = "UNRESOLVED"
            rr["REJECT_REASON"] = rec.get("CONTOUR_FAIL", "NO_SIDE_EVIDENCE")
            rejects.append(rr)

    log("Direction-valid candidates before thinning: {}".format(len(oriented)))

    required_index_elevations = sorted(
        pd.to_numeric(
            analysis_contours.loc[analysis_contours["IS_INDEX"] == 1, ELEV_FIELD],
            errors="coerce",
        ).dropna().astype(float).unique().tolist()
    )
    log("Required index-elevation coverage levels ({}): {}".format(
        len(required_index_elevations), required_index_elevations
    ))

    final_records, missing_coverage = select_labels_with_elevation_coverage(
        oriented, rejects, required_index_elevations
    )
    log("Final accepted labels after mandatory elevation coverage + moderate thinning: {}".format(len(final_records)))
    if missing_coverage:
        log("WARNING - no direction-valid candidate exists for index elevation(s): {}".format(missing_coverage))

    if not final_records:
        raise RuntimeError("No final labels generated; inspect reject reasons / OBJE_CODE / source data.")

    final_gdf = gpd.GeoDataFrame(final_records, geometry="geometry", crs=contours.crs)
    reject_gdf = (
        gpd.GeoDataFrame(rejects, geometry="geometry", crs=contours.crs)
        if rejects else
        gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=contours.crs)
    )
    audit_gdf = build_line_audit(logical, final_records, rejects)

    # Stable order for QA.
    sort_cols = [c for c in [TFBH_FIELD, ELEV_FIELD, "LINE_UID", "STATION_M"] if c in final_gdf.columns]
    if sort_cols:
        final_gdf = final_gdf.sort_values(sort_cols).reset_index(drop=True)

    prepare_output_gdb()

    # Master = all accepted points for the selected processing scope.
    write_gdb_layer(final_gdf, OUTPUT_MASTER_LAYER)

    if LABEL_SCOPE in {"index_only", "both"}:
        index_gdf = final_gdf[final_gdf["IS_INDEX"] == 1].copy()
        if index_gdf.empty:
            raise RuntimeError("No index-contour labels were generated.")
        write_gdb_layer(index_gdf, OUTPUT_INDEX_LAYER)

    if WRITE_REJECT_LAYER and not reject_gdf.empty:
        write_gdb_layer(reject_gdf, OUTPUT_REJECT_LAYER)
    if not audit_gdf.empty:
        write_gdb_layer(audit_gdf, OUTPUT_AUDIT_LAYER)

    # Elevation-level QA: this is the key production coverage report.
    coverage_csv = os.path.join(os.path.dirname(OUTPUT_GDB), "contour_label_elevation_coverage.csv")
    src_idx = analysis_contours[analysis_contours["IS_INDEX"] == 1].copy()
    logical_idx = logical[logical["IS_INDEX"] == 1].copy() if "IS_INDEX" in logical.columns else logical.iloc[0:0].copy()
    cov_rows = []
    for elev in required_index_elevations:
        src_n = int(np.isclose(pd.to_numeric(src_idx[ELEV_FIELD], errors="coerce"), elev).sum())
        log_n = int(np.isclose(pd.to_numeric(logical_idx[ELEV_FIELD], errors="coerce"), elev).sum()) if len(logical_idx) else 0
        ori_n = sum(abs(float(r[ELEV_FIELD]) - float(elev)) < 1e-6 for r in oriented)
        fin_n = sum(abs(float(r[ELEV_FIELD]) - float(elev)) < 1e-6 for r in final_records)
        seed_n = sum(abs(float(r[ELEV_FIELD]) - float(elev)) < 1e-6 and int(r.get("COVERAGE_SEED", 0)) == 1 for r in final_records)
        cov_rows.append({
            "ELEV": float(elev),
            "SOURCE_FEATURES": src_n,
            "LOGICAL_LINES": log_n,
            "DIRECTION_VALID_CANDIDATES": int(ori_n),
            "FINAL_LABELS": int(fin_n),
            "COVERAGE_SEEDS": int(seed_n),
            "COVERED": int(fin_n >= max(1, int(MIN_LABELS_PER_INDEX_ELEV))),
        })
    pd.DataFrame(cov_rows).to_csv(coverage_csv, index=False, encoding="utf-8-sig")
    log("Elevation coverage QA: {}".format(coverage_csv))

    # Lightweight text summary next to the GDB.
    summary_csv = os.path.join(os.path.dirname(OUTPUT_GDB), "contour_label_summary.csv")
    summary = pd.DataFrame([
        {"ITEM": "LABEL_SCOPE", "VALUE": LABEL_SCOPE},
        {"ITEM": "LABEL_GROUND_LENGTH_M", "VALUE": LABEL_GROUND_LENGTH_M},
        {"ITEM": "CANDIDATE_STEP_M", "VALUE": CANDIDATE_STEP_M},
        {"ITEM": "END_CLEARANCE_M", "VALUE": MIN_END_CLEARANCE_M},
        {"ITEM": "FRAME_BASE_INSET_M", "VALUE": FRAME_BASE_INSET_M},
        {"ITEM": "MAX_TEXT_TOP_DEVIATION_FROM_NORTH_DEG", "VALUE": MAX_TEXT_TOP_DEVIATION_FROM_NORTH_DEG},
        {"ITEM": "BEST_TEXT_TOP_DEVIATION_FROM_NORTH_DEG", "VALUE": BEST_TEXT_TOP_DEVIATION_FROM_NORTH_DEG},
        {"ITEM": "SAME_ELEV_MIN_DIST_M", "VALUE": MIN_SAME_ELEV_LABEL_DISTANCE_M},
        {"ITEM": "MIN_LABELS_PER_INDEX_ELEV", "VALUE": MIN_LABELS_PER_INDEX_ELEV},
        {"ITEM": "REQUIRED_INDEX_ELEV_LEVELS", "VALUE": len(required_index_elevations)},
        {"ITEM": "MISSING_INDEX_ELEV_LEVELS", "VALUE": ",".join(map(str, missing_coverage)) if missing_coverage else ""},
        {"ITEM": "SOURCE_CONTOURS", "VALUE": len(analysis_contours)},
        {"ITEM": "LOGICAL_LINES", "VALUE": len(logical)},
        {"ITEM": "FINAL_MASTER", "VALUE": len(final_gdf)},
        {"ITEM": "FINAL_INDEX", "VALUE": int((final_gdf["IS_INDEX"] == 1).sum())},
        {"ITEM": "SIDE_FROM_ADJ_CONTOUR", "VALUE": int(final_gdf["SIDE_SRC"].astype(str).str.startswith("ADJ_CONTOUR").sum())},
        {"ITEM": "SIDE_FROM_DEM", "VALUE": int((final_gdf["SIDE_SRC"] == "DEM").sum())},
        {"ITEM": "REJECTS", "VALUE": len(reject_gdf)},
        {"ITEM": "FRAME_SAFE_MARGIN_M", "VALUE": frame_safe_margin_m()},
        {"ITEM": "PREFER_NORTHWARD_TEXT_TOP", "VALUE": int(bool(PREFER_NORTHWARD_TEXT_TOP))},
        {"ITEM": "MEAN_UP_SCORE", "VALUE": float(final_gdf["UP_SCORE"].mean()) if "UP_SCORE" in final_gdf.columns and len(final_gdf) else np.nan},
        {"ITEM": "ELAPSED_MIN", "VALUE": round((time.time() - t0) / 60.0, 3)},
    ])
    summary.to_csv(summary_csv, index=False, encoding="utf-8-sig")

    log("============================================================")
    log("DONE")
    log("Output GDB: {}".format(OUTPUT_GDB))
    log("Master labels: {}".format(len(final_gdf)))
    log("Index labels: {}".format(int((final_gdf["IS_INDEX"] == 1).sum())))
    log("High side from adjacent contours: {}".format(int(final_gdf["SIDE_SRC"].astype(str).str.startswith("ADJ_CONTOUR").sum())))
    log("High side from DEM fallback: {}".format(int((final_gdf["SIDE_SRC"] == "DEM").sum())))
    log("Rejected candidates: {}".format(len(reject_gdf)))
    log("ArcMap: LABEL_TXT + ANGLE_GEO (Geographic), perpendicular unchecked; TOP_AZ north cone <= {:.1f} deg".format(MAX_TEXT_TOP_DEVIATION_FROM_NORTH_DEG))
    log("Elapsed: {:.2f} min".format((time.time() - t0) / 60.0))


if __name__ == "__main__":
    try:
        cli()
    except Exception as e:
        print("\n[FATAL] {}".format(e), file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

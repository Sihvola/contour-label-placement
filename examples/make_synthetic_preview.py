"""Create README SVGs from synthetic contours using the unchanged core functions.

Run from the repository root: python examples/make_synthetic_preview.py
No real datasets, DEM, GDB, network resources or third-party plotting tools are used.
"""
from contextlib import redirect_stderr, redirect_stdout
from html import escape
import io
import math
from pathlib import Path
import sys

import geopandas as gpd
from shapely.geometry import LineString, box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import contour_label as core


def synthetic_workflow():
    xs = range(0, 2201, 10)
    geoms = [LineString([(x, y + 100 * math.sin(x / 350) + 25 * math.sin(x / 110)) for x in xs])
             for y in (370, 620, 870, 1120, 1370)]
    contours = gpd.GeoDataFrame({core.ELEV_FIELD: [120., 150., 180., 210., 240.],
                                core.OBJE_CODE_FIELD: ['SYNTHETIC_INDEX'] * 5,
                                'SRC_FID': list(range(5))}, geometry=geoms, crs='EPSG:3857')
    sheets = gpd.GeoDataFrame({core.TFBH_FIELD: ['SYNTHETIC_SHEET']},
                              geometry=[box(0, 0, 2200, 1700)], crs=contours.crs)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        marked = core.mark_index_contours(contours, {'SYNTHETIC_INDEX'})
        logical = core.build_logical_lines(marked)
        raw, rejects = core.generate_raw_candidates(logical)
        inside = core.apply_sheet_assignment(raw, rejects, core.SheetAssigner(sheets))
        side_index = core.ContourSideIndex(contours)
        oriented = []
        for rec in inside:
            side = side_index.judge(rec['geometry'], rec['TANG_AZ'], rec[core.ELEV_FIELD])
            if side['status'] != 'OK':
                rejects.append(dict(rec, REJECT_REASON=side['status']))
                continue
            ok, rejected = core.orient_record(rec, side['higher_side'], 'ADJ_CONTOUR',
                                              side['confidence'], side['votes'])
            if ok is not None:
                oriented.append(ok)
            else:
                rejects.append(rejected)
        final, missing = core.select_labels_with_elevation_coverage(oriented, rejects, [150.])
    assert not missing and len(final) == 5
    assert all(r['HIGH_SIDE'] == 'LEFT' and r['TOP_DIR_CLASS'] > 0 for r in final)
    return contours, raw, final, rejects


def text(x, y, value, size=16, color='#243745', extra=''):
    return f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{color}" {extra}>{escape(str(value))}</text>'


def line(x1, y1, x2, y2, color='#7d8d95', width=2, extra=''):
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width}" {extra}/>'


def start(width, height, title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title>',
            '<desc id="desc">Generated only from synthetic geometry using the stable contour label algorithm. No real terrain or production data.</desc>',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="context-stroke"/></marker></defs>',
            '<g font-family="Arial, sans-serif"><rect width="100%" height="100%" fill="#f7fafb"/>']


def map_point(point, x, y, scale):
    return x + point.x * scale, y + (1700 - point.y) * scale


def contour_path(geom, x, y, scale):
    points = [(x + px * scale, y + (1700 - py) * scale) for px, py in geom.coords]
    return 'M' + ' L'.join(f'{px:.2f},{py:.2f}' for px, py in points)


def comparison(contours, raw, final):
    svg = start(1240, 705, 'Contour candidate points to final labels - synthetic example')
    svg += [text(40, 48, 'From dense candidates to final contour labels', 28),
            text(40, 78, 'Stable V3.4 core  /  synthetic contours  /  adjacent-contour evidence, no DEM', 15, '#607681')]
    scale = .235
    for x, title, subtitle, final_view in [(44, '01  Candidate search', f'{len(raw)} geometry-valid candidates before frame filtering', False),
                                           (680, '02  Final annotation', f'{len(final)} labels after direction and density selection', True)]:
        svg += [text(x, 124, title, 21), text(x, 150, subtitle, 14, '#607681')]
        y = 182
        w, h = 2200 * scale, 1700 * scale
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#ffffff" stroke="#aab9bf"/>')
        margin = core.frame_safe_margin_m() * scale
        svg.append(f'<rect x="{x + margin}" y="{y + margin}" width="{w - 2 * margin}" height="{h - 2 * margin}" fill="none" stroke="#73a5a0" stroke-dasharray="5 5"/>')
        for geom in contours.geometry:
            svg.append(f'<path d="{contour_path(geom, x, y, scale)}" stroke="#a48159" stroke-width="1.8" fill="none"/>')
        if not final_view:
            for rec in raw:
                px, py = map_point(rec['geometry'], x, y, scale)
                svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="2.15" fill="#cc8730" fill-opacity=".77"/>')
        else:
            for rec in final:
                px, py = map_point(rec['geometry'], x, y, scale)
                rotation = rec['ANGLE_GEO'] - 90
                label = text(0, 6, rec['LABEL_TXT'], 20, '#16746b', 'text-anchor="middle"')
                svg.append(f'<g transform="translate({px:.2f} {py:.2f}) rotate({rotation:.2f})"><rect x="-24" y="-13" width="48" height="26" rx="5" fill="#ffffff" stroke="#dce9e5"/>{label}</g>')
        svg += [line(x + w - 26, y + 60, x + w - 26, y + 20, '#243745', 2, 'marker-end="url(#arrow)"'),
                text(x + w - 26, y + 14, 'N', 13, extra='text-anchor="middle"')]
        svg += [line(x + 18, y + h + 24, x + 18 + 250 * scale, y + h + 24, '#607681', 3),
                text(x + 18, y + h + 45, '250 m (synthetic coordinates)', 12, '#607681')]
    svg += [line(586, 376, 643, 376, '#16746b', 3, 'marker-end="url(#arrow)"'),
            text(40, 673, 'Dashed frame: safe interior. Brown: contours. Amber: candidates. Teal: final labels. Font enlarged for illustration.', 14, '#607681'),
            '</g></svg>']
    return '\n'.join(svg)


def orientation(contours, final, rejects):
    rec = next(r for r in final if r[core.ELEV_FIELD] == 150.)
    px, py = rec['geometry'].x, rec['geometry'].y
    svg = start(1240, 650, 'Final contour label direction and high-side evidence - synthetic example')
    svg += [text(40, 48, 'Why this candidate becomes a final label', 28),
            text(40, 78, 'A close-up of a selected synthetic contour label; orientation comes from the unchanged core.', 15, '#607681')]
    # North-up zoom of the same synthetic geometry, clipped to the panel.
    left, top, w, h, scale = 44, 115, 710, 440, .64
    cx, cy = left + w / 2, top + h / 2
    svg.append(f'<defs><clipPath id="zoom"><rect x="{left}" y="{top}" width="{w}" height="{h}" rx="8"/></clipPath></defs>')
    svg.append(f'<rect x="{left}" y="{top}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="#d0dce1"/>')
    svg.append('<g clip-path="url(#zoom)">')
    for elev, geom in zip(contours[core.ELEV_FIELD], contours.geometry):
        pts = [(cx + (x - px) * scale, cy - (y - py) * scale) for x, y in geom.coords]
        path = 'M' + ' L'.join(f'{x:.2f},{y:.2f}' for x, y in pts)
        svg.append(f'<path d="{path}" stroke="#a48159" stroke-width="{2.5 if elev == 150 else 1.8}" fill="none"/>')
    svg.append('</g>')
    az = math.radians(rec['TANG_AZ'])
    dx, dy = math.sin(az), -math.cos(az)
    svg += [line(cx - dx * 105, cy - dy * 105, cx + dx * 140, cy + dy * 140, '#6b7f89', 2, 'stroke-dasharray="5 4" marker-end="url(#arrow)"')]
    top_az = math.radians(rec['TOP_AZ'])
    nx, ny = math.sin(top_az), -math.cos(top_az)
    svg += [line(cx, cy, cx + nx * 110, cy + ny * 110, '#16746b', 3, 'marker-end="url(#arrow)"'),
            text(cx + nx * 130, cy + ny * 130, 'HIGH SIDE / TEXT TOP', 14, '#16746b', 'text-anchor="middle"')]
    rotation = rec['ANGLE_GEO'] - 90
    label = text(0, 9, '150', 30, '#16746b', 'text-anchor="middle"')
    svg.append(f'<g transform="translate({cx} {cy}) rotate({rotation:.2f})"><rect x="-34" y="-19" width="68" height="38" rx="5" fill="#ffffff" stroke="#dce9e5"/>{label}</g>')
    svg += [line(709, 185, 709, 144, '#243745', 2, 'marker-end="url(#arrow)"'), text(709, 136, 'N', 13, extra='text-anchor="middle"')]
    svg += [text(800, 146, 'COMPUTED ATTRIBUTES', 16, '#607681'),
            text(800, 192, 'LABEL_TXT    150', 22),
            text(800, 234, f'ANGLE_GEO    {rec["ANGLE_GEO"]:.2f} deg', 19),
            text(800, 273, f'TOP_AZ       {rec["TOP_AZ"]:.2f} deg', 19),
            text(800, 312, f'HIGH_SIDE    {rec["HIGH_SIDE"]}', 19),
            text(800, 351, f'North deviation    {rec["TOP_NORTH_DEV"]:.2f} deg', 17),
            text(800, 404, 'Higher-side direction retained', 17, '#16746b'),
            text(800, 434, 'Within the northward 75-degree cone', 16, '#607681'),
            text(800, 464, 'One winner per logical line / sheet', 16, '#607681'),
            text(44, 595, f'{len(final)} final labels; {len(rejects)} rejected or unselected candidates recorded by the synthetic workflow.', 16, '#607681'),
            text(44, 623, 'Schematic label display, not an ArcMap screenshot or a real production result. All coordinates are synthetic.', 14, '#607681'),
            '</g></svg>']
    return '\n'.join(svg)


def main():
    contours, raw, final, rejects = synthetic_workflow()
    destination = ROOT / 'docs' / 'images'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'candidates-to-labels.svg').write_text(comparison(contours, raw, final), encoding='utf-8')
    (destination / 'label-orientation.svg').write_text(orientation(contours, final, rejects), encoding='utf-8')
    print(f'Synthetic preview: {len(raw)} candidates, {len(final)} labels, {len(rejects)} QA records; no real data.')


if __name__ == '__main__':
    main()

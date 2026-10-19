"""The Excel the lab actually needs: one sheet per day + an 'Analiz' summary.

Layout (follows the lab's own workbook):
  "<N>. Gün" sheets   shapes side by side (Piramit | Oval | Yamuk); inside a shape, concentrations side by side
                      with 4 columns each (Alan, Çevre, Çap, Çembersellik); well sizes (600, 400, 200) stacked;
                      one row per spheroid, then Ortalama / SD rows written as AVERAGE / STDEV formulas.
  "Analiz"            tables ÇAP, ALAN, ÇEVRE, ÇEMBERSELLİK; per shape: rows = days, columns = size x
                      concentration with mean and SD (static numbers).

What goes in: approved objects and objects drawn by the researcher (status 'accepted') of photos that are
not hidden, measured as the 'cells' region in um (Çap = equivalent circular diameter).
Nothing is written unless Session.export_blockers() is empty.
"""
import os
import statistics

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

from .labels import CONCS, DAYS, SHAPES, SIZES
from .store import now

METRICS = [('Alan', 'area', 1), ('Çevre', 'perimeter', 1), ('Çap', 'ecd', 1), ('Çembersellik', 'circularity', 3)]
ANALIZ = [('ÇAP', 'ecd', 1), ('ALAN', 'area', 1), ('ÇEVRE', 'perimeter', 1), ('ÇEMBERSELLİK', 'circularity', 3)]
REGION = 'cells'
F = Font(name='Arial', size=10)
B = Font(name='Arial', size=10, bold=True)
FILL = PatternFill('solid', fgColor='DDE4EE')
FILL2 = PatternFill('solid', fgColor='F1F4F8')
CENTER = Alignment(horizontal='center', vertical='center')


class NotReady(ValueError):
    pass


def collect(session):
    """{(day, shape, size, conc): [ {area, perimeter, ecd, circularity}, ... ]} in image / object order."""
    groups = {}
    for im in session.data['images']:
        if im.get('hidden'):
            continue
        lb = im['labels']
        cal = session.calibration_state(im['image_id'])
        if not cal['um_per_px']:
            continue
        s = cal['um_per_px']
        key = (lb['day'], lb['shape'], lb['size'], lb['conc'])
        img_objs = sorted(
            (o for o in session.data['objects'].values()
             if o['image_id'] == im['image_id'] and o['status'] == 'accepted' and o.get('measure_px')),
            key=lambda o: o['object_id'])
        for idx, o in enumerate(img_objs, 1):
            m = o['measure_px'][REGION]
            if not m:
                continue
            groups.setdefault(key, []).append(dict(
                area=m['area'] * s * s, perimeter=m['perimeter'] * s, ecd=m['ecd'] * s,
                circularity=m['circularity'], image=im['original_filename'], object=f'OBJ {idx}'))
    return groups


def _r(v, nd):
    return None if v is None else round(float(v), nd)


def _day_sheet(wb, day, groups):
    ws = wb.create_sheet(f'{day}. Gün')
    shapes = [sh for sh in SHAPES if any(k[0] == day and k[1] == sh for k in groups)]
    concs = CONCS
    cols_per_conc = 6  # Görsel + Obje + Alan + Çevre + Çap + Çembersellik
    width = len(concs) * cols_per_conc
    ws.cell(1, 1, f'{day}. Gün').font = B
    row = 3
    for size in SIZES:
        sizes_present = [k for k in groups if k[0] == day and k[2] == size]
        if not sizes_present:
            continue
        nmax = max(len(groups[k]) for k in sizes_present)
        ws.cell(row, 1, f'{size}').font = B
        ws.cell(row, 1).fill = FILL
        for si, sh in enumerate(shapes):
            c0 = 2 + si * (width + 1)
            ws.cell(row, c0, sh).font = B
            ws.merge_cells(start_row=row, start_column=c0, end_row=row, end_column=c0 + width - 1)
            ws.cell(row, c0).alignment = CENTER
            ws.cell(row, c0).fill = FILL
            for ci, conc in enumerate(concs):
                cc = c0 + ci * cols_per_conc
                ws.cell(row + 1, cc, conc).font = B
                ws.merge_cells(start_row=row + 1, start_column=cc, end_row=row + 1, end_column=cc + cols_per_conc - 1)
                ws.cell(row + 1, cc).alignment = CENTER
                ws.cell(row + 1, cc).fill = FILL2
                for hdr, hcol in [('Görsel', cc), ('Obje', cc + 1)]:
                    h = ws.cell(row + 2, hcol, hdr); h.font = B; h.alignment = CENTER
                for mi, (title, _, _) in enumerate(METRICS):
                    h = ws.cell(row + 2, cc + 2 + mi, title)
                    h.font = B; h.alignment = CENTER
                vals = groups.get((day, sh, size, conc), [])
                for ri, v in enumerate(vals):
                    ws.cell(row + 3 + ri, cc, v['image']).font = F
                    ws.cell(row + 3 + ri, cc + 1, v['object']).font = F
                    for mi, (_, key, nd) in enumerate(METRICS):
                        ws.cell(row + 3 + ri, cc + 2 + mi, _r(v[key], nd)).font = F
                first, last = row + 3, row + 2 + max(nmax, 1)
                for mi in range(4):
                    col = L(cc + 2 + mi)
                    rng = f'{col}{first}:{col}{last}'
                    nd = METRICS[mi][2]
                    a = ws.cell(last + 2, cc + 2 + mi, f'=IFERROR(ROUND(AVERAGE({rng}),{nd}),"")')
                    d = ws.cell(last + 3, cc + 2 + mi, f'=IFERROR(ROUND(STDEV({rng}),{nd}),"")')
                    a.font = d.font = B
        last = row + 2 + max(nmax, 1)
        ws.cell(last + 2, 1, 'Ortalama').font = B
        ws.cell(last + 3, 1, 'SD').font = B
        row = last + 6
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions[L(2)].width = 22  # Görsel column of first group
    for c in range(2, 2 + max(len(shapes), 1) * (width + 1)):
        ws.column_dimensions[L(c)].width = 11
    # widen Görsel and Obje columns
    for si in range(len(shapes)):
        for ci in range(len(concs)):
            gc = 2 + si * (width + 1) + ci * cols_per_conc
            ws.column_dimensions[L(gc)].width = 22
            ws.column_dimensions[L(gc + 1)].width = 10
    ws.freeze_panes = 'B3'
    return ws


def _stats(vals):
    if not vals:
        return None, None
    return statistics.fmean(vals), (statistics.stdev(vals) if len(vals) > 1 else None)


def _analiz(wb, groups):
    ws = wb.create_sheet('Analiz')
    combos = [(s, c) for s in SIZES for c in CONCS]
    row = 1
    for title, key, nd in ANALIZ:
        ws.cell(row, 1, title).font = Font(name='Arial', size=12, bold=True)
        row += 1
        for sh in SHAPES:
            if not any(k[1] == sh for k in groups):
                continue
            ws.cell(row, 1, sh).font = B
            ws.cell(row, 1).fill = FILL
            for i, (sz, cn) in enumerate(combos):
                c = 2 + i * 2
                ws.cell(row, c, f'{sz} / {cn}').font = B
                ws.merge_cells(start_row=row, start_column=c, end_row=row, end_column=c + 1)
                ws.cell(row, c).alignment = CENTER
                ws.cell(row, c).fill = FILL
                ws.cell(row + 1, c, 'Ort.').font = B
                ws.cell(row + 1, c + 1, 'SD').font = B
            for di, day in enumerate(DAYS):
                r = row + 2 + di
                ws.cell(r, 1, f'{day}. Gün').font = B
                for i, (sz, cn) in enumerate(combos):
                    vs = [v[key] for v in groups.get((day, sh, sz, cn), [])]
                    mean, sd = _stats(vs)
                    if mean is not None:
                        ws.cell(r, 2 + i * 2, _r(mean, nd)).font = F
                    if sd is not None:
                        ws.cell(r, 3 + i * 2, _r(sd, nd)).font = F
            row += 2 + len(DAYS) + 1
        row += 1
    ws.column_dimensions['A'].width = 12
    for c in range(2, 2 + len(combos) * 2):
        ws.column_dimensions[L(c)].width = 9
    return ws


def export_groups(session):
    bl = session.export_blockers()
    if not bl['ready']:
        parts = []
        if not bl['n_visible']:
            parts.append('there are no visible photos')
        if bl['scale']:
            parts.append('the scale is not confirmed')
        if bl['missing_labels']:
            parts.append(f"{len(bl['missing_labels'])} photo(s) have incomplete labels")
        if bl['unprocessed']:
            parts.append(f"{len(bl['unprocessed'])} photo(s) are not detected yet")
        if bl['to_review']:
            parts.append(f"{bl['to_review']} object(s) are still to review")
        raise NotReady('Excel is not ready: ' + '; '.join(parts))
    groups = collect(session)
    wb = Workbook()
    wb.remove(wb.active)
    for day in DAYS:
        if any(k[0] == day for k in groups):
            _day_sheet(wb, day, groups)
    if not wb.sheetnames:       # every photo had zero approved objects
        ws = wb.create_sheet('Boş')
        ws['A1'] = 'No approved objects.'
    else:
        _analiz(wb, groups)
    rel_dir = os.path.join('exports', 'excel_' + now().replace(':', '').replace('-', '')[:15])
    os.makedirs(session.abspath(rel_dir), exist_ok=True)
    rel = os.path.join(rel_dir, 'spheroid_measurements.xlsx')
    wb.save(session.abspath(rel))
    return dict(xlsx=rel, n_objects=sum(len(v) for v in groups.values()), sheets=wb.sheetnames)

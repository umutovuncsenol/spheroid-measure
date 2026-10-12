"""Reference outlines drawn independently in ImageJ, for comparison only.

The researcher draws outlines in ImageJ (ROI Manager -> More -> Save... gives RoiSet.zip, or
single .roi files) and can also save the Results table (Analyze -> Measure, File -> Save As ->
Results.csv). Uploaded to one image, these become REFERENCES: shown as a separate layer,
never counted as objects, and compared in the export with the automatic and the final masks.

Coordinates: ImageJ area selections use the same convention as this app (integer coordinates
are pixel corners; pixel (r, c) covers [c, c+1) x [r, r+1)). Masks are made by pixel centre:
polygon / freehand / traced selections with the same even-odd rule as polygons drawn in the
app, rectangles exactly from their bounds, ovals from the analytic ellipse. Whether this
reproduces ImageJ's own masks pixel for pixel is checked per ROI when a Results table with
Area is uploaded (column "same-ROI area check"), not assumed.

Parsing uses the `roifile` package (C. Gohlke). Line, point and angle selections are not
areas; they are listed as skipped with the reason, never silently dropped.
"""
import csv
import io
import zipfile

import numpy as np
import roifile

AREA_TYPES = {roifile.ROI_TYPE.POLYGON: 'polygon', roifile.ROI_TYPE.RECT: 'rectangle', roifile.ROI_TYPE.OVAL: 'oval',
              roifile.ROI_TYPE.FREEHAND: 'freehand', roifile.ROI_TYPE.TRACED: 'traced'}


def _roi_record(roi, name, index):
    """dict for one ROI, or (None, reason) when it is not an area selection."""
    rtype = roi.roitype
    composite = bool(getattr(roi, 'composite', False) or (roi.multi_coordinates is not None))
    if rtype not in AREA_TYPES and not composite:
        return None, f'not an area selection (ImageJ type {getattr(rtype, "name", rtype)})'
    if composite:
        polys = [np.asarray(c, float)[:, :2] for c in roi.coordinates(multi=True) if len(c) >= 3]
        kind = 'composite'
    elif rtype in (roifile.ROI_TYPE.RECT, roifile.ROI_TYPE.OVAL):
        if getattr(roi, 'subpixelrect', None) and roi.widthd:
            l, t, w, h = float(roi.xd), float(roi.yd), float(roi.widthd), float(roi.heightd)
        else:
            l, t = float(roi.left), float(roi.top)
            w, h = float(roi.right - roi.left), float(roi.bottom - roi.top)
        if w <= 0 or h <= 0:
            return None, 'empty rectangle/oval'
        kind = AREA_TYPES[rtype]
        if kind == 'rectangle':
            polys = [np.array([[l, t], [l + w, t], [l + w, t + h], [l, t + h]])]
        else:
            n = int(min(1440, max(24, np.ceil(np.pi * (w + h) / 2 / 1.0))))
            a = np.linspace(0, 2 * np.pi, n, endpoint=False)
            polys = [np.c_[l + w / 2 + w / 2 * np.cos(a), t + h / 2 + h / 2 * np.sin(a)]]
        bounds = [l, t, l + w, t + h]
    else:
        c = np.asarray(roi.coordinates(), float)[:, :2]
        if len(c) >= 2 and np.allclose(c[0], c[-1]):
            c = c[:-1]
        if len(c) < 3:
            return None, 'fewer than 3 vertices'
        polys = [c]
        kind = AREA_TYPES[rtype]
    if not polys:
        return None, 'no outline'
    if kind not in ('rectangle', 'oval'):
        allp = np.vstack(polys)
        bounds = [float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max())]
    return dict(name=name or (roi.name or f'roi {index + 1}'), index=index, kind=kind,
                subpixel=roi.subpixel_coordinates is not None or bool(getattr(roi, 'subpixelrect', None)),
                bounds=[round(v, 3) for v in bounds],
                polygons=[[[round(float(x), 3), round(float(y), 3)] for x, y in p] for p in polys]), None


def parse_roi_file(filename, data):
    """Return (rois, skipped) from the bytes of a .roi or a RoiSet .zip."""
    rois, skipped = [], []
    if filename.lower().endswith('.zip') or data[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = [n for n in z.namelist() if n.lower().endswith('.roi')]
            if not entries:
                raise ValueError('the zip contains no .roi files')
            for i, n in enumerate(entries):
                try:
                    roi = roifile.ImagejRoi.frombytes(z.read(n))
                except Exception as e:   # noqa: BLE001 - reported per ROI
                    skipped.append(dict(index=i, name=n, reason=f'unreadable: {e}'))
                    continue
                rec, why = _roi_record(roi, roi.name or n[:-4], i)
                (rois.append(rec) if rec else skipped.append(dict(index=i, name=n, reason=why)))
    else:
        roi = roifile.ImagejRoi.frombytes(data)
        rec, why = _roi_record(roi, roi.name or filename.rsplit('.', 1)[0], 0)
        (rois.append(rec) if rec else skipped.append(dict(index=0, name=filename, reason=why)))
    return rois, skipped


def parse_results_csv(data):
    """ImageJ Results table (CSV saved from the Results window)."""
    text = data.decode('utf-8-sig', errors='replace')
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError('empty Results file')
    head = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        rec = {}
        for h, v in zip(head, r):
            v = v.strip()
            try:
                rec[h] = float(v)
            except ValueError:
                rec[h] = v
        out.append(rec)
    return dict(columns=head, rows=out)


def reference_mask(ref, shape, offset):
    """Boolean mask of a reference in a crop of `shape` whose top-left image pixel is `offset`."""
    from .store import polygon_mask        # same rasteriser as polygons drawn in the app
    H, W = shape
    ox, oy = offset
    if ref['kind'] == 'rectangle':
        l, t, r, b = ref['bounds']
        m = np.zeros(shape, bool)
        c0, c1 = int(np.ceil(l - 0.5 - ox)), int(np.floor(r - 0.5 - ox)) + 1
        r0, r1 = int(np.ceil(t - 0.5 - oy)), int(np.floor(b - 0.5 - oy)) + 1
        m[max(r0, 0):max(min(r1, H), 0), max(c0, 0):max(min(c1, W), 0)] = True
        return m
    if ref['kind'] == 'oval':
        l, t, r, b = ref['bounds']
        a, bb = (r - l) / 2, (b - t) / 2
        yy, xx = np.mgrid[0:H, 0:W]
        return ((xx + ox + 0.5 - (l + a)) / a) ** 2 + ((yy + oy + 0.5 - (t + bb)) / bb) ** 2 <= 1.0
    m = np.zeros(shape, bool)
    for p in ref['polygons']:
        m ^= polygon_mask(p, shape, offset)      # even-odd over sub-paths (composite selections)
    return m


def results_row_for(ref, refs_in_file, results):
    """The Results row of this ROI: by ROI name in the Label column, else by order when the
    number of rows equals the number of area ROIs uploaded."""
    if not results:
        return None, ''
    rows = results['rows']
    lab = next((c for c in results['columns'] if c.lower() == 'label'), None)
    if lab:
        hits = [r for r in rows if isinstance(r.get(lab), str) and
                (r[lab] == ref['name'] or r[lab].endswith(':' + ref['name']) or (':' + ref['name'] + ':') in r[lab])]
        if len(hits) == 1:
            return hits[0], 'by label'
    if len(rows) == len(refs_in_file):
        return rows[refs_in_file.index(ref)], 'by order'
    return None, 'no matching row'

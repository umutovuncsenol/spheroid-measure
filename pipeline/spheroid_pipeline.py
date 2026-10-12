"""Standalone spheroid detection and measurement pipeline.

  python pipeline/spheroid_pipeline.py IMAGE [IMAGE ...] [--out DIR] [--model PATH_OR_hf:USER/REPO] [--diameter 40]
  python pipeline/spheroid_pipeline.py --label-set label_set.json --root sessions/test2 --out results

Steps: Cellpose -> merge nearby pieces (one spheroid per well) -> numbered contour image, candidates.json
and measurements.csv. The raw mask is cached next to each image as <image>_<tag>.npy and is not recomputed
if it already exists.

Measurements follow ImageJ (Analyze > Measure). Pixel size comes from the blue 500 um scale bar at the
bottom-left of each image when present; otherwise the median over the images that have one is used.
"""
import argparse, glob, json, os, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

MIN_AREA = 400      # px; pieces smaller than this are discarded
GAP = 30            # px; pieces closer than this are merged into one spheroid

# um/px measured from the 500 um bars of 49 images at 4x magnification; used only if no bar is found at all
DEFAULT_UM_PER_PX = 1.6807
BAR_UM = 500.0      # real length of the scale bar at the bottom-left (um)


def detect_scale(img):
    """Return um/px from the blue scale bar at the bottom-left, or None.
    Only the 500 um bar (11 ticks) is recognised. The distance between the first and last tick centres is used."""
    H, W = img.shape[:2]
    y0 = int(H * 0.8)
    reg = img[y0:, :int(W * 0.3)].astype(int)
    blue = (reg[..., 2] > 140) & (reg[..., 0] < 90) & (reg[..., 1] < 90)
    rows = blue.sum(1)
    y = int(rows.argmax())
    if rows[y] < 280 or rows[y] > 320:
        return None
    up = blue[max(y - 14, 0):y - 2].sum(0)
    xs = np.nonzero(up >= 4)[0]
    cent, start, prev = [], None, None
    for x in xs:
        if start is None or x > prev + 1:
            if start is not None:
                cent.append((start + prev) / 2)
            start = x
        prev = x
    if start is not None:
        cent.append((start + prev) / 2)
    if len(cent) != 11:
        return None
    return BAR_UM / (cent[-1] - cent[0])


def _trace(mask):
    """Trace the outer boundary of one component along pixel edges (like ImageJ's traced selection).
    Returns the corner points where the direction changes: [(x, y), ...]."""
    b = np.pad(mask.astype(bool), 2)
    ys, xs = np.nonzero(b)
    k = np.lexsort((xs, ys))[0]            # first pixel in raster order
    x0, y0 = int(xs[k]), int(ys[k])
    px = lambda x, y: bool(b[y, x])
    # direction: 0 right, 1 down, 2 left, 3 up; the object stays on the right-hand side
    d, x, y = 0, x0, y0
    dx = (1, 0, -1, 0); dy = (0, 1, 0, -1)
    pts = []
    first = True
    while True:
        if d == 0:   lf, rf = px(x, y - 1), px(x, y)
        elif d == 1: lf, rf = px(x, y), px(x - 1, y)
        elif d == 2: lf, rf = px(x - 1, y), px(x - 1, y - 1)
        else:        lf, rf = px(x - 1, y - 1), px(x, y - 1)
        nd = (d - 1) % 4 if lf else (d if rf else (d + 1) % 4)
        if nd != d or first:
            pts.append((x - 2, y - 2))
        first = False
        d = nd
        x += dx[d]; y += dy[d]
        if x == x0 and y == y0:
            break
    return pts


def traced_perimeter(pts):
    """ImageJ PolygonRoi.getTracedPerimeter: (horizontal + vertical edge lengths) - n_corners * (2 - sqrt(2))."""
    n = len(pts)
    xp = [q[0] for q in pts]; yp = [q[1] for q in pts]
    sumdx = sumdy = 0
    ncorners = 0
    dx1 = xp[0] - xp[-1]; dy1 = yp[0] - yp[-1]
    side1 = abs(dx1) + abs(dy1)
    corner = False
    for i in range(n):
        nxt = (i + 1) % n
        dx2 = xp[nxt] - xp[i]; dy2 = yp[nxt] - yp[i]
        sumdx += abs(dx1); sumdy += abs(dy1)
        side2 = abs(dx2) + abs(dy2)
        if side1 > 1 or not corner:
            corner = True
            ncorners += 1
        else:
            corner = False
        dx1, dy1, side1 = dx2, dy2, side2
    return sumdx + sumdy - ncorners * (2.0 - np.sqrt(2.0))


def shape_stats(m):
    """Measurements in pixels, computed like ImageJ (Analyze > Measure):
    area, perimeter (traced outline with corner correction), circularity (4*pi*A/P^2, capped at 1),
    Feret and minimum Feret diameter, fitted-ellipse major/minor axes (same area and second moments),
    aspect ratio, roundness and solidity."""
    import cv2
    lab, n = ndi.label(m)
    area = float(m.sum())
    per = 0.0
    hull_pts = []
    for k in range(1, n + 1):
        pts = _trace(lab == k)
        per += traced_perimeter(pts)
        hull_pts += pts
    circ = min(1.0, 4 * np.pi * area / per ** 2) if per > 0 else 0.0
    h = cv2.convexHull(np.array(hull_pts, dtype=np.float32)).reshape(-1, 2)
    d = np.sqrt(((h[:, None, :] - h[None, :, :]) ** 2).sum(-1))
    feret = float(d.max())
    ang = np.deg2rad(np.arange(0, 180, 0.5))
    proj = h @ np.stack([np.cos(ang), np.sin(ang)])
    min_feret = float((proj.max(0) - proj.min(0)).min())
    hull_area = float(cv2.contourArea(h))
    yy, xx = np.nonzero(m)
    c = np.cov(np.stack([xx, yy]), bias=True) + np.eye(2) / 12.0
    l1, l2 = sorted(np.linalg.eigvalsh(c), reverse=True)
    a_, b_ = 4 * np.sqrt(l1), 4 * np.sqrt(max(l2, 1e-9))
    sc = np.sqrt(area / (np.pi * a_ * b_ / 4))
    major, minor = a_ * sc, b_ * sc
    return dict(area=area, perimeter=per, circularity=circ, feret=feret, min_feret=min_feret,
                major=major, minor=minor, aspect=major / minor,
                roundness=4 * area / (np.pi * major ** 2), solidity=area / hull_area if hull_area else 0.0)


def run_cellpose(model, img, diameter):
    masks, _, _ = model.eval(img, diameter=diameter)
    return masks.astype(np.int32)


def run_scaled(model, img, scale, cellprob=0.0):
    """For the fine-tuned model: downscale the image, run without a diameter, upscale the mask to the original size."""
    H, W = img.shape[:2]
    small = np.asarray(Image.fromarray(img).resize((round(W * scale), round(H * scale)), Image.LANCZOS))
    m, _, _ = model.eval(small, diameter=None, cellprob_threshold=cellprob)
    return np.asarray(Image.fromarray(m.astype(np.int32)).resize((W, H), Image.NEAREST)).astype(np.int32)


def merge(M):
    """Group Cellpose masks by proximity and turn each group into a single candidate."""
    ids = [k for k in range(1, M.max() + 1) if (M == k).sum() > MIN_AREA]
    dist = {k: ndi.distance_transform_edt(M != k) for k in ids}
    par = {k: k for k in ids}

    def find(a):
        while par[a] != a:
            a = par[a]
        return a
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if dist[a][M == b].min() < GAP:
                par[find(a)] = find(b)
    groups = {}
    for k in ids:
        groups.setdefault(find(k), []).append(k)
    out = []
    for ks in groups.values():
        m = np.isin(M, ks)
        if len(ks) > 1:
            m = ndi.binary_closing(m, structure=np.ones((3, 3)), iterations=GAP // 2)
        m = ndi.binary_fill_holes(m)
        out.append((m, len(ks)))
    out.sort(key=lambda t: tuple(np.array(ndi.center_of_mass(t[0]))[::-1]))
    return out


def contour(m):
    from skimage import measure
    c = max(measure.find_contours(np.pad(m, 1).astype(float), 0.5), key=len)
    return [[float(x - 1), float(y - 1)] for y, x in c[::4]]


def main():
    ap = argparse.ArgumentParser(description='Detect and measure spheroids in microwell images.')
    ap.add_argument('images', nargs='*', help='image files (wildcards are expanded, also on Windows)')
    ap.add_argument('--label-set', help='JSON list of images ({"path": ...}) relative to --root')
    ap.add_argument('--root', default='.')
    ap.add_argument('--out', default='pipeline_out')
    ap.add_argument('--model', default=None,
                    help='fine-tuned Cellpose model: a local file, or hf:USER/REPO[/FILE] to download from Hugging Face')
    ap.add_argument('--diameter', type=float, default=40, help='cell diameter for the base model (only without --model)')
    ap.add_argument('--scale', type=float, default=None,
                    help='downscale factor for the fine-tuned model (default 0.5 with --model); --diameter is not used then')
    ap.add_argument('--cellprob', type=float, default=0.0)
    ap.add_argument('--um-per-px', type=float, default=None,
                    help='micrometres per pixel; if omitted it is measured from the 500 um scale bars in the images')
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # avoid encoding errors on Windows consoles
    except Exception:
        pass
    if a.model and a.model.startswith('hf:'):       # e.g. hf:user/repo  or  hf:user/repo/file_name
        from huggingface_hub import hf_hub_download
        parts = a.model[3:].split('/')
        repo, fname = '/'.join(parts[:2]), ('/'.join(parts[2:]) or 'spheroid_v2')
        print(f'downloading model (or using the local cache): {repo}/{fname}')
        a.model = hf_hub_download(repo_id=repo, filename=fname)
    paths = []
    for g in a.images:                              # Windows shells do not expand * and ?, so we do it here
        paths += sorted(glob.glob(g)) if any(c in g for c in '*?[') else [g]
    if a.label_set:
        paths += [os.path.join(a.root, s['path']) for s in json.load(open(a.label_set))]
    if not paths:
        ap.error('no images given')
    os.makedirs(a.out, exist_ok=True)
    scales = {}
    for p in paths:
        v = detect_scale(np.asarray(Image.open(p).convert('RGB')))
        if v:
            scales[p] = v
    if a.um_per_px:
        glob_scale = a.um_per_px
        print(f'scale: given manually, {glob_scale:.4f} um/px')
    elif scales:
        vals = sorted(scales.values())
        glob_scale = float(np.median(vals))
        print(f'scale: 500 um bar found in {len(scales)}/{len(paths)} images -> {glob_scale:.4f} um/px '
              f'(min {vals[0]:.4f}, max {vals[-1]:.4f}); images without a bar use this value')
    else:
        glob_scale = DEFAULT_UM_PER_PX
        print(f'WARNING: no scale bar found in any image, using the default measured at 4x: {glob_scale} um/px '
              '(pass --um-per-px if the magnification is different)')
    model = None
    results = {}
    info = {}
    if a.model and a.scale is None:
        a.scale = 0.5
    tag = f'ft_{os.path.basename(a.model)}_s{a.scale}' if a.model else f'cp{int(a.diameter)}'
    for n, p in enumerate(paths, 1):
        img = np.asarray(Image.open(p).convert('RGB'))
        npy = p.rsplit('.', 1)[0] + f'_{tag}.npy'
        if os.path.exists(npy):
            M = np.load(npy)
        else:
            if model is None:
                from cellpose import models
                model = models.CellposeModel(gpu=True, pretrained_model=a.model) if a.model else models.CellposeModel(gpu=True)
            M = run_scaled(model, img, a.scale, a.cellprob) if a.scale else run_cellpose(model, img, a.diameter)
            np.save(npy, M)
        cands = merge(M)
        im = Image.fromarray(img)
        dr = ImageDraw.Draw(im)
        um = scales.get(p) or glob_scale
        usrc = ('scale bar in image' if p in scales else
                ('manual' if a.um_per_px else ('median of series' if scales else 'default (4x)')))
        rec = []
        for i, (m, nparts) in enumerate(cands, 1):
            edge = m & ~ndi.binary_erosion(m, iterations=3)
            for y, x in zip(*np.nonzero(edge)):
                dr.point((x, y), fill=(255, 220, 0))
            cy, cx = ndi.center_of_mass(m)
            dr.rectangle([cx - 12, cy - 12, cx + 12, cy + 12], fill=(0, 0, 0))
            dr.text((cx - 7, cy - 7), str(i), fill=(255, 255, 0))
            st = shape_stats(m)
            ex = dict(area_px=int(st['area']), perimeter_px=round(st['perimeter'], 1), circularity=round(st['circularity'], 3),
                      feret_px=round(st['feret'], 1), min_feret_px=round(st['min_feret'], 1),
                      major_px=round(st['major'], 1), minor_px=round(st['minor'], 1),
                      aspect_ratio=round(st['aspect'], 3), roundness=round(st['roundness'], 3), solidity=round(st['solidity'], 3),
                      eq_diameter_px=round(float(2 * np.sqrt(st['area'] / np.pi)), 1))
            if um:
                ex.update(area_um2=round(st['area'] * um ** 2, 1), perimeter_um=round(st['perimeter'] * um, 1),
                          feret_um=round(st['feret'] * um, 1), min_feret_um=round(st['min_feret'] * um, 1),
                          major_um=round(st['major'] * um, 1), minor_um=round(st['minor'] * um, 1),
                          eq_diameter_um=round(ex['eq_diameter_px'] * um, 1))
            ys, xs = np.nonzero(m)
            rec.append(dict(id=i, **ex, parts=nparts, bbox=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                            polygon=contour(m)))
        base = os.path.splitext(os.path.basename(p))[0].replace(' ', '_')
        im.save(os.path.join(a.out, base + '_result.jpg'), quality=88)
        results[p] = rec
        info[p] = (um, usrc)
        print(n, len(paths), os.path.basename(p), '->', len(rec), 'spheroids', flush=True)
    json.dump(results, open(os.path.join(a.out, 'candidates.json'), 'w'), indent=1)
    import csv
    cols = [('area_um2', 'area_um2'), ('perimeter_um', 'perimeter_um'), ('circularity', 'circularity'),
            ('feret_um', 'feret_um'), ('min_feret_um', 'min_feret_um'), ('major_axis_um', 'major_um'),
            ('minor_axis_um', 'minor_um'), ('aspect_ratio', 'aspect_ratio'), ('roundness', 'roundness'),
            ('solidity', 'solidity'), ('equivalent_diameter_um', 'eq_diameter_um'),
            ('area_px', 'area_px'), ('perimeter_px', 'perimeter_px'), ('feret_px', 'feret_px')]
    with open(os.path.join(a.out, 'measurements.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['image', 'spheroid_id', 'um_per_px', 'scale_source'] + [c[0] for c in cols])
        for pth, rec in results.items():
            um, usrc = info[pth]
            for c in rec:
                w.writerow([os.path.basename(pth), c['id'], round(um, 4) if um else '', usrc if um else ''] +
                           [c.get(k, '') for _, k in cols])
    print('DONE ->', a.out)


if __name__ == '__main__':
    main()

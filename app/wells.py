"""Classical automatic well detection (no training). Separate from cell segmentation.

Most images show a regular grid of identical wells. Method (all on a downsampled copy):
  1. illumination correction: gray / large-scale Gaussian background
  2. edge map: gradient magnitude after light smoothing, robustly normalised
  3. grid period (x, y) from the autocorrelation of the edge map
  4. "mean well": average of the edge map over all grid cells (suppresses cells, debris
     and noise, keeps the shared well outline)
  5. outline fit on the mean well: zero-mean templates of a square outline, an inverted
     pyramid (outline + diagonals) or a ring, over a range of sizes; the score is mean
     edge strength on the outline minus mean edge strength just inside it
  6. placement at every grid position, then per well: local re-fit of position (+-15%) and
     size (+-10%), and per-side refinement for square shapes
Every well carries its own outline score relative to the image median; low-score wells
and wells cut by the image border are flagged. If no grid or no outline is found, the
result is status 'failed' with a reason -- never a silent guess. All parameters are
returned so they can be stored with the image.
"""
import math

import numpy as np
from scipy import ndimage as ndi
from scipy.signal import fftconvolve

METHOD_VERSION = 'lattice-1'

DEFAULTS = dict(
    work_long_side=640,          # downsample so the long side is this many px
    illum_sigma_frac=0.06,       # background Gaussian sigma as a fraction of long side
    edge_sigma=1.5,              # smoothing before gradient (work px)
    size_min_frac=0.10,          # well outer size range as a fraction of image height
    size_max_frac=0.95,
    size_step=1.06,              # geometric step between template sizes
    line_frac=0.035,             # outline thickness as a fraction of size (min 2 px)
    band_frac=0.06,              # inside/outside reference band thickness
    outside_weight=0.0,          # share of the negative weight on the OUTSIDE band (0 = ignore clutter around wells)
    diag_weight=0.4,             # 'pyramid' template: share of positive weight on the two diagonals
    shapes=('square', 'pyramid', 'round'),  # outline shapes tried on the mean well
    min_score=0.06,              # minimum outline score of the mean well (else FAILED)
    refine_frac=0.12,            # per-side search window as a fraction of size
)


def _gray(rgb):
    a = rgb.astype(np.float32)
    return (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) / 255.0 if a.ndim == 3 else a / 255.0


def _edge_map(rgb, p):
    g = _gray(rgb)
    H, W = g.shape
    f = max(1, int(round(max(H, W) / p['work_long_side'])))
    small = g[: H - H % f, : W - W % f].reshape(H // f, f, W // f, f).mean(axis=(1, 3))
    bg = ndi.gaussian_filter(small, p['illum_sigma_frac'] * max(small.shape))
    corr = small / np.maximum(bg, 1e-3)
    gs = ndi.gaussian_filter(corr, p['edge_sigma'])
    gy, gx = ndi.sobel(gs, 0), ndi.sobel(gs, 1)
    gm = np.hypot(gy, gx)
    n = max(np.percentile(gm, 99.5), 1e-6)
    return np.clip(gm / n, 0, 1.5), f, gy / n, gx / n, gs


def _template(shape, s, p):
    """Zero-mean outline template of outer size s (work px)."""
    lw = max(2.0, p['line_frac'] * s)
    bw = max(2.0, p['band_frac'] * s)
    n = int(math.ceil(s + 2 * bw + 2))
    c = (n - 1) / 2.0
    yy, xx = np.mgrid[:n, :n]
    if shape in ('square', 'pyramid'):
        d = np.maximum(np.abs(yy - c), np.abs(xx - c))          # Chebyshev distance
    else:
        d = np.hypot(yy - c, xx - c)
    r = s / 2.0
    on = np.abs(d - r) <= lw / 2
    inside = (d < r - lw / 2) & (d >= r - lw / 2 - bw)
    outside = (d > r + lw / 2) & (d <= r + lw / 2 + bw)
    t = np.zeros((n, n), np.float32)
    ow = float(p['outside_weight'])
    if shape == 'pyramid':          # outline + both diagonals (facet edges of an inverted pyramid)
        diag = ((np.abs((yy - c) - (xx - c)) <= lw / 2) | (np.abs((yy - c) + (xx - c)) <= lw / 2)) & (d < r - lw / 2)
        dw = float(p['diag_weight'])
        t[on] = (1 - dw) / on.sum()
        t[diag] = dw / diag.sum()
        inside = inside & ~diag
        face = (d < r - lw / 2) & ~diag
        t[face] = -(1 - ow) / face.sum()        # facets should be smooth
    else:
        t[on] = 1.0 / on.sum()
        t[inside] = -(1 - ow) / inside.sum()
    if ow > 0:
        t[outside] = -ow / outside.sum()
    return t


def _scores(E, shape, sizes, p):
    out = []
    for s in sizes:
        t = _template(shape, s, p)
        out.append(fftconvolve(E, t[::-1, ::-1], mode='same'))
    return np.stack(out)


def _refine_square(E, cx, cy, s, p, grad=None, polarity=0):
    """Move each side of a square independently to the best nearby edge.
    polarity = +1: well interior brighter than its rim -> look for bright-to-dark edges going
    outward; -1: the opposite; 0: unsigned edge strength. With two parallel edges (inner floor
    edge and outer wall edge) the signed version picks the interior boundary."""
    H, W = E.shape
    r = s / 2.0
    win = max(2, int(p['refine_frac'] * s))
    half = int(0.35 * s)                       # use the middle 70% of each side
    gy, gx = grad if grad is not None else (None, None)
    edges = {}
    for side in ('top', 'bottom', 'left', 'right'):
        best, best_pos = -np.inf, None
        for d in range(-win, win + 1):
            if side in ('top', 'bottom'):
                y = int(round(cy + (-r if side == 'top' else r) + d))
                if not 0 <= y < H:
                    continue
                xs = slice(max(0, int(cx - half)), min(W, int(cx + half)))
                if xs.stop <= xs.start:
                    continue
                if polarity and gy is not None:
                    out = -gy if side == 'top' else gy          # derivative along the outward normal
                    v = -polarity * out[max(0, y - 1):y + 2, xs].mean()
                else:
                    v = E[max(0, y - 1):y + 2, xs].mean()
                pos = y
            else:
                x = int(round(cx + (-r if side == 'left' else r) + d))
                if not 0 <= x < W:
                    continue
                ys = slice(max(0, int(cy - half)), min(H, int(cy + half)))
                if ys.stop <= ys.start:
                    continue
                if polarity and gx is not None:
                    out = -gx if side == 'left' else gx
                    v = -polarity * out[ys, max(0, x - 1):x + 2].mean()
                else:
                    v = E[ys, max(0, x - 1):x + 2].mean()
                pos = x
            if v > best:
                best, best_pos = v, pos
        if best_pos is None:   # side outside the image: keep the template position, clipped
            best_pos = {'top': max(0, cy - r), 'bottom': min(H - 1, cy + r),
                        'left': max(0, cx - r), 'right': min(W - 1, cx + r)}[side]
        edges[side] = float(best_pos)
    return edges


# ---------------------------------------------------------------------------------------
# Lattice method: most images show a regular grid of identical wells.
#   period (px, py) from the autocorrelation of the edge map  ->  average all unit cells
#   into one low-noise "mean well"  ->  find its outline  ->  place it at every lattice
#   position  ->  refine each side of each well locally.
# ---------------------------------------------------------------------------------------
LATTICE_DEFAULTS = dict(
    min_period_frac=0.12,     # smallest period as a fraction of the image dimension
    max_period_frac=0.75,
    min_peak=0.08,            # normalised autocorrelation needed to accept a period
    cell_size_min=0.35,       # well size as a fraction of the shorter period
    cell_size_max=0.98,
    min_visible=0.5,          # keep a lattice well if >= 50% of its box is inside the image
    min_well_rel=0.35,        # wells whose own outline score < 35% of the median are flagged
    lattice_refine_frac=0.06,  # per-side refinement window (fraction of size) for lattice wells
    max_size_dev=0.08,         # a refined side that changes the well size by > 8% is reverted
    local_shift_frac=0.15,     # each lattice well is re-centred within +-15% of the size ...
    local_size_range=0.10,     # ... and re-sized within +-10% (best outline response)
    grid_cells=True,           # fallback when no well OUTLINE is found but a clear 2-D period of dark
                               # objects exists: propose one grid cell per period, labelled as such
    grid_cell_frac=0.92,       # grid cell size as a fraction of the period
    grid_period_ratio=1.35,    # x and y periods must agree within this ratio
    grid_min_contrast=0.15,    # mean-cell darkness peak vs its surroundings (corrected units)
    inner_floor=False,         # option: move each side inward past a dark wall frame to the bright floor (see _inner_floor)
    inner_max_frac=0.30,       # ... searching at most 30% of the well size inward
    inner_min_step=0.25,       # ... only if the median brightness rises by >= 0.25 (corrected units) across the step
    polarity=0,                # side refinement: 0 = unsigned edges; 1 / -1 = interior brighter / darker
                               # than its rim; 'auto' = estimated from the mean well (unreliable when
                               # spheroids fill the wells, see docs/WELLS.md)
)


def _inner_floor(G, box, p):
    """Per side: walk inward from the box edge; if the side starts in a dark band (a wall
    frame) and the median brightness along the side's middle 60% then rises sharply, move
    the side to that rise. Uses medians so a cluster covering < half of a side is ignored.
    Returns the new box and the per-side shifts (work px)."""
    x0, y0, x1, y1 = box
    H, W = G.shape
    size = min(x1 - x0, y1 - y0)
    n = max(3, int(p['inner_max_frac'] * size))
    shifts = {}
    new = dict(left=x0, right=x1, top=y0, bottom=y1)
    for side in ('left', 'right', 'top', 'bottom'):
        prof = []
        for k in range(n):
            if side in ('left', 'right'):
                x = int(round(x0 + k if side == 'left' else x1 - 1 - k))
                ya, yb = int(y0 + 0.2 * (y1 - y0)), int(y1 - 0.2 * (y1 - y0))
                if not (0 <= x < W) or yb <= ya:
                    prof.append(np.nan); continue
                prof.append(float(np.median(G[max(0, ya):min(H, yb), x])))
            else:
                y = int(round(y0 + k if side == 'top' else y1 - 1 - k))
                xa, xb = int(x0 + 0.2 * (x1 - x0)), int(x1 - 0.2 * (x1 - x0))
                if not (0 <= y < H) or xb <= xa:
                    prof.append(np.nan); continue
                prof.append(float(np.median(G[y, max(0, xa):min(W, xb)])))
        prof = np.array(prof)
        if np.isnan(prof).all():
            shifts[side] = 0
            continue
        prof = np.nan_to_num(prof, nan=np.nanmedian(prof))
        sm = ndi.uniform_filter1d(prof, 3)
        floor = float(np.max(sm))
        # step = largest rise between a window before and after position k
        best_k, best_rise = 0, 0.0
        w = 2
        for k in range(w, n - w):
            rise = sm[k:k + w].mean() - sm[max(0, k - w):k].mean()
            if rise > best_rise:
                best_rise, best_k = rise, k
        dark_start = sm[:max(1, best_k)].mean() < floor - p['inner_min_step']
        if best_rise >= p['inner_min_step'] / 2 and dark_start and best_k > 1:
            shifts[side] = best_k
            if side == 'left':
                new['left'] = x0 + best_k
            elif side == 'right':
                new['right'] = x1 - best_k
            elif side == 'top':
                new['top'] = y0 + best_k
            else:
                new['bottom'] = y1 - best_k
        else:
            shifts[side] = 0
    return new, shifts


def _autocorr(E):
    e = E - E.mean()
    H, W = e.shape
    F = np.fft.rfft2(e, s=(2 * H, 2 * W))
    ac = np.fft.irfft2(F * np.conj(F), s=(2 * H, 2 * W))
    ones = np.fft.rfft2(np.ones_like(e), s=(2 * H, 2 * W))
    cnt = np.fft.irfft2(ones * np.conj(ones), s=(2 * H, 2 * W))
    ac = ac / np.maximum(cnt, 1)
    return ac / max(ac[0, 0], 1e-9)          # ac[dy, dx], negative lags wrap


def _period(ac, axis, n, q):
    lo, hi = int(q['min_period_frac'] * n), int(q['max_period_frac'] * n)
    if hi <= lo + 2:
        return None, 0.0
    if axis == 1:
        prof = np.max(np.stack([ac[dy % ac.shape[0], lo:hi] for dy in range(-3, 4)]), axis=0)
    else:
        prof = np.max(np.stack([ac[lo:hi, dx % ac.shape[1]] for dx in range(-3, 4)]), axis=0)
    prof = ndi.uniform_filter1d(prof, 3)
    k = int(np.argmax(prof))
    lag, val = lo + k, float(prof[k])
    # prefer the fundamental if half the lag is also a strong peak
    half = lag // 2
    if half >= lo and prof[half - lo] >= 0.7 * val:
        lag, val = half, float(prof[half - lo])
    return (lag if val >= q['min_peak'] else None), val


def detect_wells(rgb, params=None):
    """Lattice-based detection (see module docstring). Returns status 'ok' or 'failed'."""
    p = dict(DEFAULTS)
    p.update(LATTICE_DEFAULTS)
    p.update(params or {})
    E, f, GY, GX, G = _edge_map(rgb, p)
    H, W = E.shape
    ac = _autocorr(E)
    px, vx = _period(ac, 1, W, p)
    py, vy = _period(ac, 0, H, p)
    info = dict(period_x=px and px * f, period_y=py and py * f, ac_x=round(vx, 3), ac_y=round(vy, 3), downsample=f)
    if px is None and py is None:
        return dict(status='failed', reason='no periodic well grid found', wells=[], params=p, info=info)
    tx = px or W
    ty = py or H
    # mean unit cell (edge map), accumulated over all lattice translates
    acc = np.zeros((ty, tx))
    accg = np.zeros((ty, tx))
    cnt = np.zeros((ty, tx))
    for y0 in range(0, H, ty):
        for x0 in range(0, W, tx):
            blk = E[y0:y0 + ty, x0:x0 + tx]
            acc[:blk.shape[0], :blk.shape[1]] += blk
            accg[:blk.shape[0], :blk.shape[1]] += G[y0:y0 + ty, x0:x0 + tx]
            cnt[:blk.shape[0], :blk.shape[1]] += 1
    cell = acc / np.maximum(cnt, 1)
    gcell = accg / np.maximum(cnt, 1)
    tiled = np.tile(cell, (3, 3))                        # circular search: well may wrap the tile edge
    short = min(tx if px else 10 ** 6, ty if py else 10 ** 6)
    sizes, s = [], p['cell_size_min'] * short
    while s <= p['cell_size_max'] * short:
        sizes.append(s)
        s *= p['size_step']
    best = None
    for shape in p['shapes']:
        S = _scores(tiled, shape, sizes, p)[:, ty:2 * ty, tx:2 * tx]
        k, y, x = np.unravel_index(int(np.argmax(S)), S.shape)
        if best is None or S[k, y, x] > best[0]:
            best = (float(S[k, y, x]), shape, x, y, sizes[k])
    sc, shape, cx, cy, size = best
    # polarity: is the mean well interior brighter (+1) or darker (-1) than its rim?
    gt = np.tile(gcell, (3, 3))
    yy, xx = np.mgrid[:gt.shape[0], :gt.shape[1]]
    dd = (np.maximum(np.abs(yy - (cy + ty)), np.abs(xx - (cx + tx))) if shape != 'round'
          else np.hypot(yy - (cy + ty), xx - (cx + tx)))
    band = max(2.0, 0.08 * size)
    g_in = gt[(dd < size / 2 - 1) & (dd >= size / 2 - 1 - band)].mean()
    g_out = gt[(dd > size / 2 + 1) & (dd <= size / 2 + 1 + band)].mean()
    est = 1 if g_in > g_out else -1
    polarity = est if p.get('polarity') == 'auto' else int(p.get('polarity') or 0)
    info.update(cell_score=round(sc, 4), shape=shape, size_px=round(size * f, 1), polarity=polarity, polarity_estimate=est,
                rim_contrast=round(float(g_in - g_out), 3))
    if sc < p['min_score']:
        reason = f'mean-well outline score {sc:.3f} < {p["min_score"]}'
        if p.get('grid_cells') and px and py:
            res = _grid_cells(rgb, G, f, px, py, p, info)
            if res is not None:
                return res
            reason += '; no regular pattern of dark objects either'
        return dict(status='failed', reason=reason, wells=[], params=p, info=info)
    wells = []
    t = _template(shape, size, p)
    loc_sizes = list(np.linspace(size * (1 - p['local_size_range']), size * (1 + p['local_size_range']), 5))
    Sloc = _scores(E, shape, loc_sizes, p)
    for ky in range(-1, H // ty + 2):
        for kx in range(-1, W // tx + 2):
            x = cx + kx * tx
            y = cy + ky * ty
            r = size / 2
            vis = max(0, min(W, x + r) - max(0, x - r)) * max(0, min(H, y + r) - max(0, y - r)) / (size * size)
            if vis < p['min_visible']:
                continue
            # local re-fit: best outline response near the lattice position (position and size)
            w_ = int(p['local_shift_frac'] * size)
            ya, yb = max(0, int(y) - w_), min(H, int(y) + w_ + 1)
            xa, xb = max(0, int(x) - w_), min(W, int(x) + w_ + 1)
            own = -1.0
            if yb > ya and xb > xa:
                sub = Sloc[:, ya:yb, xa:xb]
                k, yy_, xx_ = np.unravel_index(int(np.argmax(sub)), sub.shape)
                own = float(sub[k, yy_, xx_])
                x, y, size_w = xa + xx_, ya + yy_, loc_sizes[k]
            else:
                size_w = size
            r = size_w / 2
            if shape in ('square', 'pyramid'):
                e = _refine_square(E, x, y, size_w, dict(p, refine_frac=p['lattice_refine_frac']), (GY, GX), polarity)
                base = dict(left=x - r, right=x + r, top=y - r, bottom=y + r)
                # keep the fitted well size: revert a side whose move changes width/height too much
                for a, b in (('left', 'right'), ('top', 'bottom')):
                    if abs((e[b] - e[a]) - size_w) > p['max_size_dev'] * size_w:
                        da, db = abs(e[a] - base[a]), abs(e[b] - base[b])
                        if da >= db:
                            e[a] = base[a]
                        else:
                            e[b] = base[b]
                    if abs((e[b] - e[a]) - size_w) > p['max_size_dev'] * size_w:
                        e[a], e[b] = base[a], base[b]
                inner = {}
                if p.get('inner_floor'):
                    nb, inner = _inner_floor(G, (e['left'], e['top'], e['right'], e['bottom']), p)
                    e.update(nb)
                X0, X1 = (e['left'] + 0.5) * f, (e['right'] + 0.5) * f
                Y0, Y1 = (e['top'] + 0.5) * f, (e['bottom'] + 0.5) * f
                poly = [[X0, Y0], [X1, Y0], [X1, Y1], [X0, Y1]]
            else:
                R = r * f
                poly = [[(x + 0.5) * f + R * math.cos(a), (y + 0.5) * f + R * math.sin(a)]
                        for a in np.linspace(0, 2 * math.pi, 32, endpoint=False)]
            H0, W0 = rgb.shape[:2]
            clipped = [[min(max(a, 0.0), float(W0)), min(max(b, 0.0), float(H0))] for a, b in poly]
            partial = clipped != poly
            wells.append(dict(polygon=[[round(a, 1), round(b, 1)] for a, b in clipped], score=round(own, 4),
                              shape=shape, partial=partial, visible_fraction=round(vis, 2),
                              inner_frame_shift_px={k: v * f for k, v in (inner if shape != 'round' else {}).items()}))
    if not wells:
        return dict(status='failed', reason='lattice found but no well inside the image', wells=[], params=p, info=info)
    med = float(np.median([w['score'] for w in wells]))
    for w in wells:
        w['rel_score'] = round(w['score'] / med, 3) if med > 0 else 0.0
    _assign_ids(wells)
    info['low_confidence'] = [w['well_id'] for w in wells if w['rel_score'] < p['min_well_rel'] or w['partial']]
    return dict(status='ok', reason='', wells=wells, params=p, info=info)


def _grid_cells(rgb, G, f, px, py, p, info):
    """No well outline, but a 2-D period: average the DARKNESS over all period cells; if the
    mean cell has one clear dark peak (the typical object position), propose one square cell
    per period centred on that phase. These are grid cells, not detected well outlines."""
    if max(px, py) / min(px, py) > p['grid_period_ratio']:
        return None
    H, W = G.shape
    D = ndi.gaussian_filter(1.0 - G / max(np.median(G), 1e-6), 2)
    acc = np.zeros((py, px))
    cnt = np.zeros((py, px))
    for y0 in range(0, H, py):
        for x0 in range(0, W, px):
            blk = D[y0:y0 + py, x0:x0 + px]
            acc[:blk.shape[0], :blk.shape[1]] += blk
            cnt[:blk.shape[0], :blk.shape[1]] += 1
    cell = ndi.gaussian_filter(np.tile(acc / np.maximum(cnt, 1), (3, 3)), 0.06 * min(px, py))[py:2 * py, px:2 * px]
    cy, cx = np.unravel_index(int(np.argmax(cell)), cell.shape)
    contrast = float(cell.max() - np.median(cell))
    info.update(grid_contrast=round(contrast, 3))
    if contrast < p['grid_min_contrast']:
        return None
    size = p['grid_cell_frac'] * min(px, py)
    r = size / 2
    wells = []
    H0, W0 = rgb.shape[:2]
    for ky in range(-1, H // py + 2):
        for kx in range(-1, W // px + 2):
            x, y = cx + kx * px, cy + ky * py
            vis = max(0, min(W, x + r) - max(0, x - r)) * max(0, min(H, y + r) - max(0, y - r)) / (size * size)
            if vis < p['min_visible']:
                continue
            X0, X1, Y0, Y1 = (x - r + 0.5) * f, (x + r + 0.5) * f, (y - r + 0.5) * f, (y + r + 0.5) * f
            poly = [[X0, Y0], [X1, Y0], [X1, Y1], [X0, Y1]]
            clipped = [[min(max(a, 0.0), float(W0)), min(max(b, 0.0), float(H0))] for a, b in poly]
            # local darkness peak inside this cell (object present?)
            ys, xs = slice(max(0, int(y - r)), min(H, int(y + r))), slice(max(0, int(x - r)), min(W, int(x + r)))
            wells.append(dict(polygon=[[round(a, 1), round(b, 1)] for a, b in clipped], score=None, shape='grid_cell',
                              partial=clipped != poly, visible_fraction=round(vis, 2), rel_score=None,
                              grid_cell=True, cell_darkness=round(float(D[ys, xs].max()), 3)))
    if not wells:
        return None
    _assign_ids(wells, prefix='G')
    info.update(method_detail='grid cells (no well outline found)', grid_cell_size_px=round(size * f, 1),
                low_confidence=[w['well_id'] for w in wells])
    return dict(status='ok', reason='no well outline found: GRID CELLS proposed from the regular pattern of dark '
                                    'objects; they are not well boundaries', wells=wells, params=p, info=info)


def _assign_ids(wells, prefix='W'):
    """Reading order: rows (by centre y, grouped) then x. Wells are W1..Wn; grid cells (working
    regions without a detected well outline) are G1..Gn so the two are never confused."""
    if not wells:
        return
    hs = np.median([max(q[1] for q in w['polygon']) - min(q[1] for q in w['polygon']) for w in wells])
    wells.sort(key=lambda w: np.mean([q[1] for q in w['polygon']]))
    rows, cur, last = [], [], None
    for w in wells:
        c = np.mean([q[1] for q in w['polygon']])
        if last is not None and c - last > 0.5 * hs:
            rows.append(cur)
            cur = []
        cur.append(w)
        last = c
    rows.append(cur)
    out = []
    for row in rows:
        out += sorted(row, key=lambda w: np.mean([q[0] for q in w['polygon']]))
    for i, w in enumerate(out):
        w['well_id'] = f'{prefix}{i + 1}'
    wells[:] = out

"""Second classical segmenter, scale-aware and self-calibrating per well (no training).

Features (each computed on the well crop):
  * illumination correction: divide gray by a large Gaussian background estimated from
    the well's own pixels (sigma = bg_sigma_frac x well size); "darkness" = 1 - corrected
  * texture: local standard deviation of the corrected image (window ~ texture_frac x size)
  * edges: Sobel gradient magnitude of the corrected image (optional term)
Self-calibration (local/adaptive thresholding at well scale): for each feature the well's
background level is estimated from its LEAST cell-like pixels (the `bg_quantile` lower
part of the distribution), and the spread from the same pixels (MAD). A pixel is a
candidate cell pixel if its darkness z-score > k_dark, OR its texture z-score > k_tex.
This picks up pale but textured cells (not only dark cores).
Then: wall-band removal (distance to the well boundary < wall_margin_frac x size),
closing of tiny gaps (radius close_px; this joins pixels of one textured cluster, it does
NOT join separate clusters farther apart than ~2 x close_px), hole filling inside parts
(fill_holes, default true), speck removal, and the same wall/shadow filter as the
classical segmenter. Every removed pixel is returned with its reason (base.py); pixels
added by closing / hole filling are returned in `added` and reported per object.
Optional watershed split (split_touching=True): components whose distance transform has
>1 well-separated maxima are split along the watershed line, so two touching clusters
become two candidates. The line pixels are returned as removed['split_line'] (one part per
cut, next to the pieces), so the reviewer can re-merge the pieces without losing a pixel.
"""
import numpy as np
from scipy import ndimage as ndi
from skimage import color, filters, measure, morphology, segmentation, feature

from .base import Segmenter, SegmentationResult
from .classical import _remove_small

FOUR = ndi.generate_binary_structure(2, 1)
EIGHT = ndi.generate_binary_structure(2, 2)


def _smooth(a, sigma):
    """Gaussian smoothing. For very large sigma (> 100 px: whole-image or magnified regions) it
    is computed on a block-averaged copy and interpolated back, which is ~100x faster and
    indistinguishable for a slowly varying background. Ordinary wells use the exact filter."""
    if sigma <= 100:
        return ndi.gaussian_filter(a, sigma)
    k = int(sigma // 25)
    H, W = a.shape
    Hp, Wp = -(-H // k) * k, -(-W // k) * k
    pad = np.pad(a, ((0, Hp - H), (0, Wp - W)), mode='edge')
    small = pad.reshape(Hp // k, k, Wp // k, k).mean(axis=(1, 3))
    small = ndi.gaussian_filter(small, sigma / k)
    big = ndi.zoom(small, k, order=1)[:H, :W]
    return big


def _robust_bg(v, q):
    """Location and spread of the lowest q-fraction of v (the 'background-like' pixels)."""
    t = np.quantile(v, q)
    low = v[v <= t]
    med = float(np.median(low))
    mad = float(np.median(np.abs(low - med))) * 1.4826
    return med, max(mad, 1e-4)


class AdaptiveSegmenter(Segmenter):
    name = 'adaptive'

    def segment(self, rgb, region, p):
        g = color.rgb2gray(rgb[..., :3]) if rgb.ndim == 3 else rgb.astype(float) / 255.0
        H, W = g.shape
        if not region.any():
            e = np.zeros(region.shape, bool)
            return SegmentationResult(e, e, {'note': 'empty region'}, {}, e, e)
        ys, xs = np.nonzero(region)
        size = float(min(ys.max() - ys.min() + 1, xs.max() - xs.min() + 1))   # well size (px)
        g = filters.gaussian(g, p.get('pre_smooth_sigma_px', 1.0))

        # illumination correction using only in-well pixels (normalised convolution)
        s_bg = max(3.0, p['bg_sigma_frac'] * size)
        if p.get('illumination_correction', True):
            num, den = _smooth(np.where(region, g, 0.0), s_bg), _smooth(region.astype(float), s_bg)
            bg = num / np.maximum(den, 1e-6)
        else:                       # comparison variant: one global floor level for the well
            bg = np.full(g.shape, float(np.median(g[region])))
        corr = g / np.maximum(bg, 1e-3)
        dark = 1.0 - corr

        w = max(3, int(round(p['texture_frac'] * size)) | 1)
        m1 = ndi.uniform_filter(corr, w)
        m2 = ndi.uniform_filter(corr * corr, w)
        tex = np.sqrt(np.clip(m2 - m1 * m1, 0, None))

        inner = ndi.distance_transform_edt(np.pad(region, 1))[1:-1, 1:-1]
        margin = p['wall_margin_frac'] * size
        core_zone = inner > margin
        q = p['bg_quantile']
        d_med, d_mad = _robust_bg(dark[core_zone], q)
        t_med, t_mad = _robust_bg(tex[core_zone], q)
        zd = (dark - d_med) / d_mad
        zt = (tex - t_med) / t_mad
        # texture counts only where the pixel is at least mildly darker than the floor
        # (k_tex_dark): this keeps pale-but-textured cells and drops bright facet/rim edges
        cell = (zd > p['k_dark']) | ((zt > p['k_tex']) & (zd > p.get('k_tex_dark', -1e9)))
        if p.get('k_edge'):
            gm = np.hypot(ndi.sobel(corr, 0), ndi.sobel(corr, 1))
            e_med, e_mad = _robust_bg(gm[core_zone], q)
            cell |= (gm - e_med) / e_mad > p['k_edge']
        raw = cell & region                       # every pixel that passed the threshold
        removed = {}
        removed['wall_band'] = raw & ~core_zone
        cell = raw & core_zone
        if p.get('close_px', 0) > 0:
            r = int(p['close_px'])
            cell = ndi.binary_closing(np.pad(cell, r + 1), morphology.disk(r))[r + 1:-(r + 1), r + 1:-(r + 1)] & core_zone
        r_open = max(int(p.get('open_px', 0)), int(round(p.get('open_frac', 0) * size)))
        if r_open > 0:   # removes thin lines (facet edges, wall seams) narrower than ~2 r_open
            before = cell
            fp = morphology.disk(r_open, decomposition='sequence') if r_open > 6 else morphology.disk(r_open)
            cell = morphology.opening(cell, fp)
            removed['thin'] = before & ~cell & raw
        if p.get('fill_holes', True):
            cell = ndi.binary_fill_holes(cell, structure=FOUR)
        min_px = max(int(p['min_component_frac'] * size * size), 4)
        before = cell
        cell = _remove_small(cell, min_px)
        removed['small'] = before & ~cell & raw

        # wall / shadow filter (same rules as classical, band relative to well size)
        band = inner <= margin + p['wall_band_frac'] * size
        lab, n = ndi.label(cell, structure=EIGHT)
        excluded = np.zeros_like(cell)
        for r in measure.regionprops(lab):
            m = lab == r.label
            bf = band[m].mean()
            elong = r.axis_major_length / max(r.axis_minor_length, 1)
            if (elong > p.get('wall_elong', 3) and bf > 0.4) or \
               (bf > p.get('wall_band_frac_lowsolid', 0.5) and r.solidity < p.get('wall_solidity', 0.5)) or bf > 0.9:
                excluded |= m
        cell &= ~excluded

        info = dict(well_size_px=size, bg_sigma_px=round(s_bg, 1), texture_window_px=w, open_radius_px=r_open,
                    dark_bg=round(d_med, 4), dark_mad=round(d_mad, 4), tex_bg=round(t_med, 4), tex_mad=round(t_mad, 4))
        selected = cell | excluded
        if p.get('split_touching'):
            before_split = cell
            cell, nsplit = _watershed_split(cell, p, size)
            # the cut between the pieces is kept as ONE reasoned part per cut (never dropped): assigning
            # it back to a piece, together with the other piece, restores exactly the unsplit mask
            removed['split_line'] = before_split & ~cell
            info['watershed_splits'] = nsplit
            info['split_line_px'] = dict(total=int(removed['split_line'].sum()),
                                         above_threshold=int((removed['split_line'] & raw).sum()))
        # pixels in the result (kept, wall-shaped, or on a split line) that did NOT pass the threshold:
        # added by gap closing / hole filling. Computed after every step, so it matches the final masks.
        added = selected & ~raw
        info['removed_px'] = {k: int(v.sum()) for k, v in removed.items()}
        info['added_px'] = int(added.sum())
        return SegmentationResult(cell, excluded, info, removed, added, raw)


def _watershed_split(cell, p, size):
    """Split components with >1 distance-transform maxima separated by a narrow neck."""
    lab, n = ndi.label(cell, structure=FOUR)
    out = np.zeros(cell.shape, np.int32)
    nxt = 1
    nsplit = 0
    min_d = max(3, int(p.get('ws_min_distance_frac', 0.12) * size))
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        m = lab[sl] == i
        dist = ndi.distance_transform_edt(m)
        pk = feature.peak_local_max(dist, min_distance=min_d, threshold_abs=p.get('ws_min_radius_frac', 0.04) * size,
                                    labels=m.astype(int), exclude_border=False)
        if len(pk) > 1:
            markers = np.zeros(m.shape, np.int32)
            for j, (y, x) in enumerate(pk, start=1):
                markers[y, x] = j
            ws = segmentation.watershed(-dist, markers, mask=m, watershed_line=True)
            # accept the split only where the neck is narrow relative to the pieces
            pieces = [ws == j for j in range(1, len(pk) + 1)]
            radii = [dist[pc].max() for pc in pieces]
            line = m & (ws == 0)
            neck = 2 * dist[line].max() if line.any() else 0
            if line.any() and neck < p.get('ws_neck_ratio', 0.7) * 2 * min(radii):
                for pc in pieces:
                    out[sl][pc] = nxt
                    nxt += 1
                nsplit += 1
                continue
        out[sl][m] = nxt
        nxt += 1
    return out > 0, nsplit

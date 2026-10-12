"""Classical baseline segmenter (ported from baseline_v0/spheroid.py, generalised to
polygon wells and pixel-unit parameters). No training.

cell pixel  <=>  darkness > T_d   OR  (local texture > T_t AND darkness > T_d / 2)
darkness    =  (background - gray) / background, background = local bright floor estimate.
Then: wall margin removed, thin structures opened away, holes inside parts filled,
specks removed, and wall/shadow-like parts moved to `excluded` (still shown for review).

All thresholds were tuned by eye on the three example images. They are NOT validated.
"""
import numpy as np
from scipy import ndimage as ndi
from skimage import color, filters, measure, morphology

from .base import Segmenter, SegmentationResult

FOUR = ndi.generate_binary_structure(2, 1)


class ClassicalSegmenter(Segmenter):
    name = 'classical'

    def segment(self, rgb, region, p):
        g = color.rgb2gray(rgb[..., :3]) if rgb.ndim == 3 else rgb.astype(float) / 255.0
        g = filters.gaussian(g, p.get('pre_smooth_sigma_px', 1.0))
        inside = g[region]
        if inside.size == 0:
            empty = np.zeros(region.shape, bool)
            return SegmentationResult(empty, empty, {'note': 'empty region'}, {}, empty, empty)

        if p['bg_mode'] == 'max':
            gg = np.where(region, g, 0.0)                     # outside pixels cannot raise the floor
            bg = ndi.maximum_filter(gg, size=int(p['bg_size_px']))
            bg = filters.gaussian(bg, p['bg_size_px'] / 2)
        else:                                                 # robust local floor (median)
            gg = np.where(region, g, np.median(inside))
            f = 4
            small = gg[::f, ::f]
            med = ndi.median_filter(small, size=max(3, int(p['bg_size_px']) // f), mode='nearest')
            bg = np.kron(med, np.ones((f, f)))[:g.shape[0], :g.shape[1]]
            bg = filters.gaussian(bg, 4)
        bg = np.maximum(bg, 1e-3)
        dark = np.clip((bg - g) / bg, 0, 1)

        w = int(p.get('texture_window_px', 7))
        m1 = ndi.uniform_filter(g, w)
        m2 = ndi.uniform_filter(g * g, w)
        tex = np.sqrt(np.clip(m2 - m1 * m1, 0, None))

        td, tt = p['darkness_threshold'], p['texture_threshold']
        cell = (dark > td) | ((tex > tt) & (dark > td * 0.5))

        # distance of each pixel to the well boundary (inside the well)
        dist_in = ndi.distance_transform_edt(np.pad(region, 1))[1:-1, 1:-1]
        margin = float(p['wall_margin_px'])
        raw = cell & region                       # every pixel that passed the threshold
        removed = {'wall_band': raw & ~(dist_in > margin)}
        cell = cell & (dist_in > margin)
        if p.get('opening_radius_px', 2) > 0:
            before = cell
            cell = morphology.opening(cell, morphology.disk(int(p['opening_radius_px'])))
            removed['thin'] = before & ~cell & raw
        if p.get('fill_holes', True):
            cell = ndi.binary_fill_holes(cell, structure=FOUR)
        before = cell
        cell = _remove_small(cell, int(p['min_component_px']))
        removed['small'] = before & ~cell & raw

        # wall / shadow filter: long thin, L-shaped or edge-hugging parts
        band = dist_in <= margin + float(p.get('wall_band_extra_px', 15))
        conn = FOUR if int(p.get('wall_filter_connectivity', 8)) == 4 else ndi.generate_binary_structure(2, 2)
        lab, n = ndi.label(cell, structure=conn)   # 8-connectivity = v0 behaviour
        excluded = np.zeros_like(cell)
        for r in measure.regionprops(lab):
            m = lab == r.label
            bf = band[m].mean()
            elong = r.axis_major_length / max(r.axis_minor_length, 1)
            if (elong > p.get('wall_elong', 3) and bf > p.get('wall_band_frac_elong', 0.4)) or \
               (bf > p.get('wall_band_frac_lowsolid', 0.5) and r.solidity < p.get('wall_solidity', 0.5)) or \
               bf > p.get('wall_band_frac_any', 0.9):
                excluded |= m
        added = cell & ~raw                       # hole filling (not above threshold)
        info = dict(removed_px={k: int(v.sum()) for k, v in removed.items()}, added_px=int(added.sum()))
        return SegmentationResult(cell & ~excluded, excluded, info, removed, added, raw)


def _remove_small(mask, max_size):
    lab, n = ndi.label(mask, structure=FOUR)
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes > max_size
    keep[0] = False
    return keep[lab]

"""Deterministic 2D measurements of binary masks.

Boundary definition (used for EVERY value below, so area, perimeter, Feret and
circularity always describe the same geometry):
    the binary mask is blurred with a Gaussian of sigma = BOUNDARY_SIGMA_PX (default 1.0 px)
    and the 0.5 iso-contour is traced (marching squares). This sub-pixel polygon removes the
    pixel staircase, which otherwise inflates perimeters of round objects by ~5.5%.
    Chosen by synthetic tests (tests/test_measure.py, docs/METHODS.md).
    Side effects: parts separated by gaps of <= ~1 px may be traced as one boundary, and
    objects smaller than ~10 px radius lose a few % of area. `mask_pixels` (raw count) and
    `n_parts` (4-connected pixel parts) are always reported from the unblurred mask.

    area        = signed polygon area (outer boundaries minus holes)
    perimeter   = total length of all boundary polygons (outer + hole boundaries)
    ECD         = 2 * sqrt(area / pi)
    max Feret   = largest distance between any two boundary vertices (all parts together)
    circularity = 4 * pi * area / perimeter**2   (not clipped; pixelisation can push tiny
                  objects slightly above 1)

Multi-part regions are measured as a union: areas and perimeters are summed, Feret spans
all parts. For multi-part regions circularity is NOT a single-object shape descriptor;
`n_parts` is reported next to it.

Regions (see region_masks):
    cells    : exactly the pixels assigned to the object (core + assigned dispersed parts).
               Gaps between parts are NOT filled.
    envelope : fill_holes(closing(cells, disk(r))) OR cells. Bridges gaps up to ~2r px.
               Contains every cell pixel by construction (verified in tests).
    core     : the largest connected part of `cells`.
"""
import math

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull
from skimage import measure

FOUR_CONN = ndi.generate_binary_structure(2, 1)


BOUNDARY_SIGMA_PX = 1.0


def _contours(mask, sigma=None):
    sigma = BOUNDARY_SIGMA_PX if sigma is None else sigma
    pad = 2 + int(math.ceil(3 * sigma))
    padded = np.pad(mask.astype(np.float32), pad)
    if sigma > 0:
        padded = ndi.gaussian_filter(padded, sigma)
    cs = measure.find_contours(padded, 0.5, fully_connected='low', positive_orientation='high')
    return [c - float(pad) for c in cs if len(c) >= 3]  # back to mask coordinates (row, col)


def _signed_area(c):
    y, x = c[:, 0], c[:, 1]
    return 0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _length(c):
    d = np.diff(np.vstack([c, c[:1]]), axis=0)
    return float(np.sqrt((d ** 2).sum(axis=1)).sum())


def _max_feret(points):
    if len(points) < 2:
        return 0.0
    pts = np.unique(points, axis=0)
    if len(pts) > 3:
        try:
            pts = pts[ConvexHull(pts).vertices]
        except Exception:  # collinear points
            pass
    d = pts[:, None, :] - pts[None, :, :]
    return float(np.sqrt((d ** 2).sum(-1)).max())


def measure_mask(mask, sigma=None):
    """Measurements in pixel units for a boolean mask (any shape, any number of parts)."""
    mask = np.asarray(mask, bool)
    n_px = int(mask.sum())
    if n_px == 0:
        return None
    cs = _contours(mask, sigma)
    if not cs:
        return None
    signed = [_signed_area(c) for c in cs]
    area = abs(float(sum(signed)))
    perim = float(sum(_length(c) for c in cs))
    pts = np.vstack(cs)
    _, n_parts = ndi.label(mask, structure=FOUR_CONN)
    filled = ndi.binary_fill_holes(mask, structure=FOUR_CONN)
    _, n_holes = ndi.label(filled & ~mask, structure=ndi.generate_binary_structure(2, 2))
    return dict(
        mask_pixels=n_px,
        area=area,
        perimeter=perim,
        ecd=2.0 * math.sqrt(area / math.pi),
        feret_max=_max_feret(pts),
        circularity=(4.0 * math.pi * area / perim ** 2) if perim > 0 else float('nan'),
        n_parts=int(n_parts),
        n_holes=int(n_holes),
    )


def to_physical(m, um_per_px):
    """Convert a pixel-unit measurement dict to µm. Returns None if no calibration."""
    if m is None or not um_per_px:
        return None
    s = float(um_per_px)
    return dict(area=m['area'] * s * s, perimeter=m['perimeter'] * s, ecd=m['ecd'] * s,
                feret_max=m['feret_max'] * s, circularity=m['circularity'])


def envelope_of(cells, radius_px):
    """Outer envelope. Contains all `cells` pixels by construction.
    Closing with a Euclidean disk of radius r, computed exactly via distance transforms
    (dilate: dist-to-cells <= r; erode: dist-to-background-of-dilation > r)."""
    r = float(radius_px)
    if r <= 0:
        return ndi.binary_fill_holes(cells, structure=FOUR_CONN) | cells
    pad = int(math.ceil(r)) + 2
    p = np.pad(cells, pad)
    dil = ndi.distance_transform_edt(~p) <= r
    clo = ndi.distance_transform_edt(dil) > r
    clo = ndi.binary_fill_holes(clo, structure=FOUR_CONN)
    return clo[pad:-pad, pad:-pad] | cells


def core_of(cells):
    lab, n = ndi.label(cells, structure=FOUR_CONN)
    if n == 0:
        return cells.copy()
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == sizes.argmax()


def region_masks(cells, envelope_radius_px):
    return dict(cells=cells, envelope=envelope_of(cells, envelope_radius_px), core=core_of(cells))


def measure_object(cells, envelope_radius_px, um_per_px=None):
    """All three regions, pixel and (if calibrated) physical units."""
    out = {}
    regs = region_masks(cells, envelope_radius_px)
    assert not (regs['cells'] & ~regs['envelope']).any(), 'envelope must contain all cell pixels'
    for name, m in regs.items():
        px = measure_mask(m)
        out[name] = dict(px=px, um=to_physical(px, um_per_px))
    return out


REGION_DEFINITIONS = {
    'cells': 'Cell-covered region: exactly the pixels assigned to the object (dense core + assigned '
             'dispersed parts). Gaps between parts are not filled. Measured as a union.',
    'envelope': 'Outer envelope: binary closing of the cell-covered region with a disk of radius R px, '
                'holes filled, OR-ed with the cell region (contains all cell pixels). R is recorded per object.',
    'core': 'Dense core: the largest 4-connected part of the cell-covered region.',
}

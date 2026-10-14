"""Find the blue scale bar of a microscope photo (bottom-left corner).

This only PROPOSES a scale: the researcher always sees the detected line and confirms it (or draws
the line by hand). The bar is assumed to be 500 um long (the microscope default); the length is
editable in the UI because bars of other lengths exist.
"""
import numpy as np

ASSUMED_BAR_UM = 500.0


def _runs(idx, gap=2):
    """Group sorted integer positions into [start, end] runs, tolerating small gaps."""
    out = []
    for x in idx:
        if out and x <= out[-1][1] + gap + 1:
            out[-1][1] = int(x)
        else:
            out.append([int(x), int(x)])
    return out


def detect_scale_bar(rgb, bar_um=ASSUMED_BAR_UM):
    H, W = rgb.shape[:2]
    y0, x1lim = int(H * 0.75), int(W * 0.45)
    reg = rgb[y0:, :x1lim].astype(int)
    blue = (reg[..., 2] > 140) & (reg[..., 0] < 90) & (reg[..., 1] < 90)
    rows = blue.sum(1)
    if rows.size == 0 or rows.max() < 40:
        return dict(found=False, reason='no blue scale bar found in the bottom-left corner')
    y = int(rows.argmax())
    band = blue[max(y - 1, 0):y + 2].any(0)
    runs = _runs(np.nonzero(band)[0])
    xa, xb = max(runs, key=lambda r: r[1] - r[0])
    if xb - xa < 40:
        return dict(found=False, reason='the blue line in the corner is too short to be a scale bar')
    # tick marks above the bar: 11 ticks -> use first/last tick centres (more exact than the bar ends)
    up = blue[max(y - 14, 0):max(y - 2, 0)].sum(0) if y > 2 else np.zeros(blue.shape[1], int)
    cent = [(a + b) / 2 for a, b in _runs(np.nonzero(up >= 4)[0], gap=0)]
    if len(cent) == 11:
        length, xs, method = cent[-1] - cent[0], (cent[0], cent[-1]), 'ticks'
    else:
        length, xs, method = float(xb - xa), (float(xa), float(xb)), 'bar_extent'
    yy = float(y + y0)
    ys = np.nonzero(blue.any(1))[0]
    return dict(found=True, method=method, n_ticks=len(cent), length_px=float(length),
                line=[[float(xs[0]), yy], [float(xs[1]), yy]],
                bbox=[int(xa), int(ys.min() + y0), int(xb), int(ys.max() + y0)],
                known_length=bar_um, unit='µm', um_per_px=bar_um / float(length))

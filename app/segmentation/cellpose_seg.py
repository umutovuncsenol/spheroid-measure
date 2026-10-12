"""Fine-tuned Cellpose segmenter (spheroid_v2). Plugs into the review app like any other segmenter:
one well crop in -> boolean 'cell pixels' mask out. Nearby Cellpose pieces are merged into one
spheroid (same GAP / MIN_AREA rules as pipeline/spheroid_pipeline.py).

The model is downloaded once from Hugging Face (umutovuncsenol/sferoid) and cached.
Override with the SPHEROID_MODEL environment variable (a local model file path).
"""
import os

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from .base import Segmenter, SegmentationResult

HF_REPO = 'umutovuncsenol/sferoid'
HF_FILE = 'spheroid_v2'
_MODEL = {}


def _model():
    if 'm' not in _MODEL:
        from cellpose import models
        path = os.environ.get('SPHEROID_MODEL')
        if not path:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(repo_id=HF_REPO, filename=HF_FILE)
        _MODEL['m'] = models.CellposeModel(gpu=True, pretrained_model=path)
    return _MODEL['m']


def _merge(M, min_area, gap):
    ids = [k for k in range(1, int(M.max()) + 1) if (M == k).sum() > min_area]
    if not ids:
        return np.zeros(M.shape, bool)
    dist = {k: ndi.distance_transform_edt(M != k) for k in ids}
    par = {k: k for k in ids}

    def find(a):
        while par[a] != a:
            a = par[a]
        return a
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if dist[a][M == b].min() < gap:
                par[find(a)] = find(b)
    groups = {}
    for k in ids:
        groups.setdefault(find(k), []).append(k)
    out = np.zeros(M.shape, bool)
    for ks in groups.values():
        m = np.isin(M, ks)
        if len(ks) > 1:
            m = ndi.binary_closing(m, structure=np.ones((3, 3)), iterations=gap // 2)
        out |= ndi.binary_fill_holes(m)
    return out


class CellposeSegmenter(Segmenter):
    name = 'cellpose'

    def segment(self, rgb, region, p):
        H, W = region.shape
        empty = np.zeros((H, W), bool)
        if H < 16 or W < 16 or not region.any():
            return SegmentationResult(empty, empty, {'note': 'empty region'}, {}, empty, empty)
        s = float(p.get('scale', 0.5))
        img = np.ascontiguousarray(rgb[..., :3]) if rgb.ndim == 3 else np.stack([rgb] * 3, -1)
        small = np.asarray(Image.fromarray(img).resize((max(16, round(W * s)), max(16, round(H * s))), Image.LANCZOS))
        m, _, _ = _model().eval(small, diameter=None, cellprob_threshold=float(p.get('cellprob', 0.0)))
        M = np.asarray(Image.fromarray(m.astype(np.int32)).resize((W, H), Image.NEAREST)).astype(np.int32)
        M[~region] = 0
        raw = M > 0
        cells = _merge(M, int(p.get('min_area_px', 400)), int(p.get('merge_gap_px', 30))) & region
        removed = {'small': raw & ~cells}
        added = cells & ~raw
        return SegmentationResult(cells, empty, dict(cellpose_pieces=int(M.max())), removed, added, raw)

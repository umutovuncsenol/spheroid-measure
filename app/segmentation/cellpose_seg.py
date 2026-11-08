"""Fine-tuned Cellpose segmenter (spheroid_v2). Plugs into the review app like any other segmenter:
one well crop in -> boolean 'cell pixels' mask out. Nearby Cellpose pieces are merged into one
spheroid (same GAP / MIN_AREA rules as pipeline/spheroid_pipeline.py).

The model is downloaded once from Hugging Face (umutovuncsenol/sferoid) and cached.
Override with the SPHEROID_MODEL environment variable (a local model file path).

SPHEROID_GPU=modal runs the model on a GPU at Modal instead of on this computer
(see deploy/modal/sferoid_gpu.py; needs MODAL_TOKEN_ID and MODAL_TOKEN_SECRET).
"""
import io
import os
import threading

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from .base import Segmenter, SegmentationResult

HF_REPO = 'umutovuncsenol/sferoid'
HF_FILE = 'spheroid_v2'
_MODEL = {}
_MODEL_LOCK = threading.Lock()


def _download():
    """Fetch the fine-tuned weights (about 1 GB, only on the first run) and turn every failure into
    a message a lab user can act on, instead of a bare exception class name."""
    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import HfHubHTTPError
    except ImportError as e:
        raise RuntimeError(f'huggingface_hub is not installed ({e}). Run the installer again.') from e
    print(f'Loading the spheroid model from Hugging Face ({HF_REPO}). The first run downloads about 1 GB; '
          'this can take several minutes on a slow connection...', flush=True)
    try:
        return hf_hub_download(repo_id=HF_REPO, filename=HF_FILE)
    except HfHubHTTPError as e:
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        if code in (401, 403, 404):
            raise RuntimeError(
                f'Could not download the model {HF_REPO}/{HF_FILE} (HTTP {code}). The model repository is '
                'private or was renamed. Make it public on Hugging Face, or copy the model file to this '
                'computer and start the app with the SPHEROID_MODEL environment variable set to its path.') from e
        raise RuntimeError(f'Model download failed ({e}). Check the internet connection and try again.') from e
    except OSError as e:   # no connection, proxy, disk full ...
        raise RuntimeError(f'Model download failed ({e}). Check the internet connection and free disk space, '
                           'then try again.') from e


def _model():
    with _MODEL_LOCK:      # the batch job and a single "Detect" click may ask at the same time
        if 'm' not in _MODEL:
            from cellpose import models
            path = os.environ.get('SPHEROID_MODEL')
            if path and not os.path.isfile(path):
                raise RuntimeError(f'SPHEROID_MODEL points to a file that does not exist: {path}')
            path = path or _download()
            _MODEL['m'] = models.CellposeModel(gpu=True, pretrained_model=path)
            print('Spheroid model loaded.', flush=True)
        return _MODEL['m']


MODAL_APP = 'sferoid-gpu'
_REMOTE = {}


def _use_modal():
    return os.environ.get('SPHEROID_GPU', '').strip().lower() == 'modal'


def _remote():
    """The GPU model on Modal. The first call starts a GPU container (about 30-60 s)."""
    with _MODEL_LOCK:
        if 'r' not in _REMOTE:
            try:
                import modal
            except ImportError as e:
                raise RuntimeError(f'The modal package is not installed ({e}). Rebuild the app (update.sh).') from e
            if not (os.environ.get('MODAL_TOKEN_ID') and os.environ.get('MODAL_TOKEN_SECRET')):
                raise RuntimeError('SPHEROID_GPU=modal is set but MODAL_TOKEN_ID / MODAL_TOKEN_SECRET are missing. '
                                   'Run deploy/oracle/enable_modal.sh on the server.')
            print('Connecting to the GPU model on Modal (the first request starts a GPU, about a minute)...', flush=True)
            r = modal.Cls.from_name(MODAL_APP, 'Segmenter')()
            try:
                r.ping.remote()
            except Exception as e:
                raise RuntimeError(f'Could not reach the GPU model on Modal ({type(e).__name__}: {e}). Check that '
                                   f'"modal deploy deploy/modal/sferoid_gpu.py" was run, that the Modal token is '
                                   f'right and that the Modal account still has credit.') from e
            _REMOTE['r'] = r
            print('GPU model on Modal is ready.', flush=True)
        return _REMOTE['r']


def _remote_eval(small, cellprob):
    buf = io.BytesIO()
    Image.fromarray(small).save(buf, 'PNG')
    try:
        out = _remote().eval.remote(buf.getvalue(), float(cellprob))
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f'The GPU model on Modal failed ({type(e).__name__}: {e}). Try again; if it keeps '
                           f'failing, check the Modal dashboard (credit, app "{MODAL_APP}").') from e
    return np.load(io.BytesIO(out))['m']


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

    def prepare(self):
        _remote() if _use_modal() else _model()

    def segment(self, rgb, region, p):
        H, W = region.shape
        empty = np.zeros((H, W), bool)
        if H < 16 or W < 16 or not region.any():
            return SegmentationResult(empty, empty, {'note': 'empty region'}, {}, empty, empty)
        s = float(p.get('scale', 0.5))
        img = np.ascontiguousarray(rgb[..., :3]) if rgb.ndim == 3 else np.stack([rgb] * 3, -1)
        small = np.asarray(Image.fromarray(img).resize((max(16, round(W * s)), max(16, round(H * s))), Image.LANCZOS))
        cellprob = float(p.get('cellprob', 0.0))
        if _use_modal():
            m = _remote_eval(np.ascontiguousarray(small), cellprob)
        else:
            m, _, _ = _model().eval(small, diameter=None, cellprob_threshold=cellprob)
        M = np.asarray(Image.fromarray(m.astype(np.int32)).resize((W, H), Image.NEAREST)).astype(np.int32)
        M[~region] = 0
        raw = M > 0
        cells = _merge(M, int(p.get('min_area_px', 400)), int(p.get('merge_gap_px', 30))) & region
        removed = {'small': raw & ~cells}
        added = cells & ~raw
        return SegmentationResult(cells, empty, dict(cellpose_pieces=int(M.max())), removed, added, raw)

"""The spheroid model on a rented GPU (Modal), so the website itself can live on a small free server.

Deploy once from your own computer (needs a free Modal account, https://modal.com):

    pip install modal
    modal setup                                   # opens the browser to log in
    modal deploy deploy/modal/sferoid_gpu.py

Then tell the website to use it: on the server run  bash ~/spheroid-measure/deploy/oracle/enable_modal.sh

How it works: the website sends one (already shrunk) well image as PNG, the GPU runs the fine-tuned
Cellpose-SAM model and sends back the label mask. A GPU container starts on the first request
(about 30-60 s), stays warm for a few minutes after the last one, then shuts down, so you only pay
(from Modal's free monthly credit) while photos are actually being detected.
"""
import io

import modal

APP_NAME = 'sferoid-gpu'          # app/segmentation/cellpose_seg.py looks it up by this name
HF_REPO = 'umutovuncsenol/sferoid'
HF_FILE = 'spheroid_v2'
MODEL_DIR = '/models'


def _download_model():
    from huggingface_hub import hf_hub_download
    hf_hub_download(repo_id=HF_REPO, filename=HF_FILE, local_dir=MODEL_DIR)


image = (
    modal.Image.debian_slim(python_version='3.12')
    .apt_install('libglib2.0-0', 'libgl1')
    .pip_install('cellpose>=4.2', 'huggingface_hub', 'numpy', 'pillow', 'opencv-python-headless')
    .run_function(_download_model)        # the weights are baked into the image: no download per start
)

app = modal.App(APP_NAME, image=image)


@app.cls(gpu='T4', scaledown_window=180, timeout=600, max_containers=2)
class Segmenter:
    @modal.enter()
    def load(self):
        from cellpose import models
        self.model = models.CellposeModel(gpu=True, pretrained_model=f'{MODEL_DIR}/{HF_FILE}')
        print('Spheroid model loaded on the GPU.', flush=True)

    @modal.method()
    def ping(self) -> str:
        return 'ok'

    @modal.method()
    def eval(self, png: bytes, cellprob: float = 0.0) -> bytes:
        import numpy as np
        from PIL import Image
        img = np.asarray(Image.open(io.BytesIO(png)).convert('RGB'))
        masks, _, _ = self.model.eval(img, diameter=None, cellprob_threshold=float(cellprob))
        buf = io.BytesIO()
        np.savez_compressed(buf, m=masks.astype(np.int32))
        return buf.getvalue()

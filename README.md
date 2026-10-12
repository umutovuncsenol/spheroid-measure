# Spheroid detection and measurement

Detects spheroids (cell clusters) in microwell photos with a fine-tuned Cellpose model and measures them
ImageJ-style (area, perimeter, circularity, Feret, ellipse fit, ...) in micrometers, using the scale bar in the image.

## Setup

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Windows with an NVIDIA GPU: install the CUDA build of PyTorch first (https://pytorch.org). Without a GPU it runs on CPU, which is slow.

## Model

The trained model (~1.2 GB) is in a private Hugging Face repo, not in git. Log in once (you need access to the repo):

```
hf auth login
```

Then pass `--model hf:umutovuncsenol/sferoid` and it is downloaded and cached automatically. A local model folder also works: `--model path/to/spheroid_v2`.

## Run

```
python pipeline/spheroid_pipeline.py "images/*.jpg" --model hf:umutovuncsenol/sferoid --out results
```

Output in `results/`:

- `*.jpg` - overlay images with the detected spheroids
- `candidates.json` - polygons of every spheroid (for the app / review page)
- `measurements.csv` - one row per spheroid

### Scale

The 500 um scale bar (bottom-left) is detected in each image: um/px = 500 / distance between the first and last tick.
If an image has no bar, the median of the series is used. Override with `--um-per-px 1.68`.
The `scale_source` column says which was used: `scale bar in image`, `manual`, `median of series`, or `default (4x)`.

### Measurement columns (`measurements.csv`)

| column | meaning |
|---|---|
| image, spheroid_id | image name, spheroid number in that image |
| um_per_px, scale_source | scale used |
| area_um2 | area |
| perimeter_um | ImageJ traced perimeter (corner-corrected) |
| circularity | 4*pi*area / perimeter^2 (max 1) |
| feret_um, min_feret_um | max / min caliper diameter |
| major_axis_um, minor_axis_um | fitted ellipse axes |
| aspect_ratio, roundness, solidity | ImageJ definitions |
| equivalent_diameter_um | diameter of a circle with the same area |
| area_px, perimeter_px, feret_px | raw pixel values |

The CSV is UTF-8 with BOM, so Excel opens it directly.

## Review and retrain

```
python pipeline/make_review_page.py --cands results/candidates.json --out review.html
```

Open `review.html` in a browser, accept/reject candidates or draw missed ones, download `labels.json`. Then:

```
python pipeline/finetune.py --labels labels.json --epochs 100
```

This trains a new model and compares it with the base pipeline on held-out images (precision / recall / F1 at IoU 0.5).

## Notes

- Images are 1920x1080 JPGs from the 4x objective; detection runs at 0.5x scale.
- Works well on the well types it was trained on. Pyramid wells are not supported yet.

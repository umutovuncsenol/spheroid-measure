# Spheroid Review: local review and measurement prototype (v0.6)

A local app for proposing wells and spheroid candidates in microwell images, reviewing and correcting them, and exporting measurements of approved objects to Excel. All processing is in Python. Nothing leaves your computer.

> **What this is:** a **local browser prototype**. A Python process serves the interface at `127.0.0.1`, and it opens in your browser.
>
> **What it is not (yet):** an installable desktop app (`.app` / `.exe`). See [docs/DESKTOP.md](docs/DESKTOP.md) for what packaging involves.
>
> **Validation status:**
> - Measurements, calibration rules, editing, saving and export are tested.
> - Automatic well detection and cell segmentation are **classical image processing, not validated** against researcher-approved boundaries. See [docs/EVALUATION.md](docs/EVALUATION.md) for overlays, failure cases and correction effort on 11 real images.

## Kurulum (tek seferlik)

### 1. Repoyu indir
```bash
git clone https://github.com/umutovuncsenol/spheroid-measure.git
```

### 2. Kurulum scriptini çalıştır

| Sistem | Yapılacak |
|---|---|
| macOS | Terminalde: `bash spheroid-measure/install.sh` |
| Windows | `install.bat` dosyasına çift tıkla |

İlk kurulum 5–10 dakika sürer (internet bağlantısı gerekli).

### 3. Uygulamayı aç

Kurulum bittikten sonra masaüstünde **`Sferoid Measure`** dosyası oluşur.  
Bundan sonra her zaman ona çift tıklamak yeterli.  
Uygulama her açılışta güncellemeleri otomatik kontrol eder.

---

## Start (Developers)

Requires Python 3.10 or newer (tested with 3.13 on Linux; not yet tested on macOS or Windows).

| System | From this folder |
|---|---|
| macOS / Linux | `./run.sh` |
| Windows | `run.bat` |

- The first start creates a project-local `.venv/` and installs `requirements.txt` into it.
- The app opens **http://127.0.0.1:8765/**. Options: `--port 9000`, `--no-browser`. Stop with Ctrl+C.
- Everything is saved after every action, so you can stop and resume at any time.
- Flask's console notice *"This is a development server"* is expected; the app listens only on your own computer.

### Demo session

If `sessions/demo` is not in your copy (it is shipped separately to keep the download small), create it once with `.venv/bin/python scripts/build_demo.py` (about a minute). Likewise, `docs/evaluation/` (overlays) is a separate download; all of it can be regenerated with the evaluation scripts listed below.

Open **`demo`** from the session dropdown. It contains:
- **11 real images:**
  - The 3 original examples, with hand-placed preset wells and an unconfirmed scale estimate.
  - 8 more images from the archive:
    - automatic wells on 4 grid images
    - **grid cells** G1…G18 on I095 (no well outline visible; working regions, not wells)
    - manual wells on 2 images where detection failed
    - 1 image (I121) left showing the **well-detection failure** message
- **All real candidates are unreviewed.** No researcher has approved anything.
- **1 clearly labelled synthetic test image** with accepted test objects. It demonstrates the approved-only Excel export.

## Workflow

The header shows five steps. A step gets a check mark when it is done; you can go back to any step at any time.

1. **Upload.** Drop all photos of the experiment (or choose a folder). Originals are copied byte-for-byte; the file name and SHA-256 are recorded.
2. **Photos.** A grid of thumbnails.
   - **Hide** a bad photo (or tick several and **Hide selected**). Nothing is deleted: hidden photos are skipped by detection, review, scale and Excel, and **show hidden → Restore** brings them back.
   - Every photo has four labels: **Day** (1/3/5/7), **Shape** (Piramit/Oval/Yamuk), **Well size** (600/400/200), **Concentration** (50/100/150/200). They are read from the file name (`o 600 50 d5`, `OVAL_50_600_D1_2_KUYU`, `P_RAM_T 50 600 D1 4`, …) and can be edited per photo or for the selected photos at once. Photos with a missing label are highlighted.
3. **Scale.** One scale for the whole session.
   - Pick **one** photo that shows the scale bar. The app finds the blue bar (11 tick marks), draws the line on the photo and shows pixels and µm/px. Assumed length: 500 µm (editable).
   - Check both ends, tick that all photos share the same magnification, **Confirm**. The same scale is applied to every photo (hidden ones too, so restoring needs nothing more).
   - No bar found, or the line is off? **Draw line by hand** (Shift = horizontal) or drag an end of the line.
   - Measurements are converted to µm when read, so changing the scale later re-calculates every object, approved ones included.
4. **Detect.** **Detect all photos** runs the model on every visible photo, with progress. **Re-run** works per photo. Re-running keeps approved, rejected, edited and drawn objects. *Advanced* changes the model.
5. **Review.** Per photo:
   - Hover a spheroid: diameter, perimeter, area and circularity appear next to it. Click it: the same numbers show in the right panel with **Approve** (`A`) / **Reject** (`R`).
   - **Approve all remaining** / **Reset all** for the photo (one undo step).
   - **Draw your own outline, as in ImageJ:** **Draw: new** adds an object (approved on creation); **Draw +** / **Draw −** add to or erase from the selected object. The shape menu chooses **polygon** (click vertices, double-click/Enter), **freehand** (drag), **ellipse** or **rectangle** (drag a box; Shift = circle/square). **Brush ±** for small touch-ups.
   - **Assign parts.** Orange dashed outlines are unassigned parts; purple ones were removed as wall or shadow. Click a part to add it to the selected object, click one of the object's own parts to remove it; with nothing selected (or Shift+click) the part becomes its own object. Parts attached only by proximity are yellow-dashed with a **Detach them** button.
   - *layers*: envelope, unassigned parts, removed pixels (pink = wall band, blue = thin, green = small). Nothing is discarded silently.
   - **Editing the mask of an approved object sets it back to "to review".**
   - **Undo:** Ctrl+Z (⌘Z) or **↶ Undo** (last 30 steps per photo). While drawing a polygon, Ctrl+Z removes the last vertex.
   - **Re-run detection** on this photo only. *Advanced*: model per photo / for all, and ImageJ reference outlines (RoiSet `.zip` / `.roi`, Results `.csv`) for validation: [docs/RESEARCHER_VALIDATION.md](docs/RESEARCHER_VALIDATION.md).
   - Shortcuts: `V` select · `B`/`E` brush ± · `D` draw · `A`/`R` approve/reject · `N`/`P` next/previous photo · `+`/`−`/`F` zoom · `Esc` cancel · `Enter` finish polygon.
   - Review time is still measured silently (counts only while the window is in front and you used mouse or keyboard within 30 s).
6. **Export Excel** (button appears in Review). It stays **disabled until no object is left "to review"** in any visible photo (and every visible photo is labelled, detected, and has the confirmed scale); hover it to see what is missing. The server enforces the same rules.

### The Excel file

`sessions/<name>/exports/excel_<timestamp>/spheroid_measurements.xlsx`, laid out like the lab's own workbook:

- **`1. Gün`, `3. Gün`, `5. Gün`, `7. Gün`**: shapes side by side (Piramit | Oval | Yamuk); well sizes 600 / 400 / 200 stacked; per concentration four columns **Alan, Çevre, Çap, Çembersellik**; one row per spheroid; then **Ortalama** and **SD** rows as `AVERAGE` / `STDEV` formulas.
- **`Analiz`**: tables ÇAP, ALAN, ÇEVRE, ÇEMBERSELLİK; per shape, rows = days, columns = well size × concentration with mean and SD.

Only approved and self-drawn objects of non-hidden photos are written, measured as the **cell-covered** region in µm; Çap is the equivalent circular diameter. Values are rounded like the lab's sheet (1 decimal, circularity 3). See [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) for the three assumptions still to be confirmed.

The previous multi-sheet export (Measurements, Review log, Calibrations, Auto vs final, Review effort, Reference comparison, Processing parameters, Settings) is still available through the API (`POST /api/s/<name>/export_full`); it is no longer in the UI.

Zoom and pan never change stored coordinates or measurements.

## What is measured

Three regions per object, each with its own area **and** perimeter; circularity always uses one region's own area and perimeter:

| Region | Definition |
|---|---|
| **Cell-covered** (primary) | Exactly the assigned pixels. Gaps between parts are not filled. |
| **Envelope** | Closing (radius 18 px) + holes filled, OR-ed with the cells. Always contains every cell pixel. |
| **Dense core** | The largest connected part. |

The measured quantities are area, perimeter, ECD = 2√(A/π), maximum Feret diameter, circularity 4πA/P², and the number of parts. All come from one sub-pixel boundary (Gaussian σ = 1 px, 0.5 contour) and describe the 2D projection. Accuracy on known shapes is in [docs/METHODS.md](docs/METHODS.md).

## Configuration

- **`config/profiles.json`**: segmentation profiles, grouping rules, measurement settings and well-detection overrides. The adaptive profiles scale with the well size.
- **`config/example_presets.json`**: wells, flags and the unconfirmed scale for the 3 original images only. Applied only on an exact filename + SHA-256 match.
- **`config/eval_variants.json`**: the segmentation variants compared in the evaluation.

## Tests and evaluation scripts

```
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                         # 81 tests
.venv/bin/python scripts/ui_flow.py                    # 36 browser checks of the 5-step workflow (needs: python -m playwright install chromium)
.venv/bin/python scripts/evaluate_pipeline.py adaptive_sparse   # full-image overlays, 11 real images
.venv/bin/python scripts/eval_segmentation.py         # variant comparison sheets
.venv/bin/python scripts/well_review_sheets.py        # per-well review sheets
.venv/bin/python scripts/correction_examples.py       # measured correction examples
.venv/bin/python scripts/split_line_example.py        # watershed cut kept as a part (I032 W3)
.venv/bin/python scripts/batch_benchmark.py 60        # batch timing / memory
.venv/bin/python scripts/batch_benchmark.py 350 --mixed   # large session: processing, accept/open/export latency
```

## Layout

```
app/        server.py, store.py (sessions, edits, processing), wells.py (well detection),
            segmentation/ (base interface, adaptive, classical), candidates.py, measure.py, export.py,
            references.py (ImageJ reference outlines), labels.py (file-name labels), scalebar.py (scale-bar finder),
            export_groups.py (the lab's Excel), static/ (UI)
config/     profiles.json, example_presets.json, eval_variants.json
examples/   images/ (3 originals), eval_images/ (8 archive images + manifest + regions), synthetic/
sessions/demo/   ready-made demo session
baseline_v0/     the earliest prototype, unchanged, with its outputs
docs/       STATUS.md, REVIEW_RESPONSE.md, RESEARCHER_VALIDATION.md, EVALUATION.md, METHODS.md, AUDIT.md,
            DESKTOP.md, evaluation/ (overlays), screenshots/
scripts/    evaluation, demo builder, UI test, benchmark
tests/      test_measure.py, test_wells_segmentation.py, test_workflow.py, test_review_integrity.py,
            test_undo.py, test_references.py, test_new_flow.py
```

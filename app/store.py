"""Session storage and all review/edit operations.

Layout of a session folder (sessions/<name>/):
    session.json                 all metadata (images, wells, objects, calibrations, review state)
    originals/<IMG-id>/<name>    byte-identical copy of each uploaded file (original filename kept)
    display/<IMG-id>.png         8-bit browser copy, only for formats browsers cannot show (e.g. TIFF)
    parts/<IMG-id>/<well>.npz    automatic part map of each well (labels + kinds), from the last run
    masks/<IMG-id>/<OBJ-id>.png          current object mask (crop; bbox in session.json)
    masks/<IMG-id>/<OBJ-id>_auto.png     the untouched automatic prediction (never edited)
    references/                  reserved for researcher-approved reference annotations (validation)
    exports/<timestamp>/         Excel + masks + annotated images

Coordinates: continuous image coordinates (x right, y down), pixel (row r, col c) covers
[c, c+1) x [r, r+1); its centre is (c + 0.5, r + 0.5). Display zoom never enters here.
"""
import base64
import contextlib
import copy
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import threading
import unicodedata

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage import draw, measure as skmeasure

from . import measure as M
from .candidates import group_parts, resolve_grouping, touches_region_edge
from .wells import METHOD_VERSION as WELL_METHOD, detect_wells
from .segmentation import get_segmenter

APP_VERSION = '0.6.0'
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(ROOT, 'config')
BROWSER_OK = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
UNITS_TO_UM = {'µm': 1.0, 'um': 1.0, 'micron': 1.0, 'mm': 1000.0, 'nm': 0.001, 'm': 1e6}
MANUAL_FLAGS = ['ambiguous_boundary', 'unreliable_segmentation', 'partial_object']
Image.MAX_IMAGE_PIXELS = None


def now():
    return dt.datetime.now().isoformat(timespec='seconds')


def load_config():
    with open(os.path.join(CONFIG_DIR, 'profiles.json'), encoding='utf-8') as f:
        return json.load(f)


def load_presets():
    p = os.path.join(CONFIG_DIR, 'example_presets.json')
    if not os.path.exists(p):
        return []
    with open(p, encoding='utf-8') as f:
        return json.load(f).get('images', [])


def params_hash(profile_cfg):
    s = json.dumps(profile_cfg, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name):
    name = os.path.basename(name.replace('\\', '/'))
    return name if name not in ('', '.', '..') else 'unnamed'


def disk_name(name):
    """File name used ON DISK for a stored original. The exact original name is kept in the
    session metadata and the export; on disk, control characters and characters that are
    unsafe on Windows/macOS or in zip archives are replaced, so sessions can be copied and
    zipped safely (an archive name with U+0098 broke unzipping on macOS)."""
    n = unicodedata.normalize('NFC', name)
    out = ''.join('_' if (unicodedata.category(c).startswith('C') or c in '<>:"/\\|?*') else c for c in n)
    out = out.strip(' .') or 'unnamed'
    return out[:180]


def load_rgb8(path):
    """8-bit RGB array for processing/display. The original file is never modified."""
    im = Image.open(path)
    im.load()
    if im.mode in ('I;16', 'I;16B', 'I;16L', 'I', 'F'):
        a = np.asarray(im, dtype=np.float64)
        lo, hi = np.percentile(a, [0.5, 99.5])
        a = np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)
        a = (a * 255).astype(np.uint8)
        return np.dstack([a, a, a])
    return np.asarray(im.convert('RGB'))


def polygon_mask(poly, shape, offset=(0, 0)):
    """Rasterise a polygon given in continuous image coords into a bool array of `shape`
    whose top-left pixel is image pixel (offset_x, offset_y). Pixel centres inside -> True."""
    ox, oy = offset
    p = np.asarray(poly, float)
    xs, ys = np.unique(p[:, 0]), np.unique(p[:, 1])
    if len(p) == 4 and len(xs) == 2 and len(ys) == 2:     # axis-aligned rectangle: exact fast path
        m = np.zeros(shape, bool)
        # pixel centres on the boundary count as inside (same convention as skimage.draw.polygon)
        c0 = max(0, int(np.ceil(xs[0] - 0.5 - ox))); c1 = min(shape[1], int(np.floor(xs[1] - 0.5 - ox)) + 1)
        r0 = max(0, int(np.ceil(ys[0] - 0.5 - oy))); r1 = min(shape[0], int(np.floor(ys[1] - 0.5 - oy)) + 1)
        if c1 > c0 and r1 > r0:
            m[r0:r1, c0:c1] = True
        return m
    rr, cc = draw.polygon(p[:, 1] - 0.5 - oy, p[:, 0] - 0.5 - ox, shape)
    m = np.zeros(shape, bool)
    m[rr, cc] = True
    return m


def poly_bbox(poly, W, H, pad=0):
    p = np.asarray(poly, float)
    x0 = max(0, int(math.floor(p[:, 0].min())) - pad)
    y0 = max(0, int(math.floor(p[:, 1].min())) - pad)
    x1 = min(W, int(math.ceil(p[:, 0].max())) + pad)
    y1 = min(H, int(math.ceil(p[:, 1].max())) + pad)
    return x0, y0, x1, y1


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


UNDO_DEPTH = 30              # undo steps kept per image (persisted in undo/<image>.json)
UNDO_FIELDS = ('bbox', 'mask_version', 'mask_sha256', 'added_px', 'measure_px', 'centroid', 'parts', 'edited',
               'status', 'approval', 'envelope_radius_px', 'boundary_sigma_px')   # notes/manual flags stay current
SHAPES = ('polygon', 'freehand', 'ellipse', 'rectangle')   # drawing tools (all sent as polygons)
ACTIVE_WINDOW_S = 30          # a second counts as active only if an input event happened within this window
MAX_ACTIVE_REPORT_S = 300     # one browser report covers at most this many seconds


def region_kind(w):
    """'well' (a detected, preset, copied or hand-drawn well), 'grid cell' (working region from the
    regular pattern of dark objects; NOT a well boundary) or 'whole image'."""
    if not w:
        return ''
    if (w.get('detection') or {}).get('grid_cell'):
        return 'grid cell'
    return 'whole image' if w.get('source') == 'whole_image' else 'well'


def wall_like(w):
    """True if the region boundary is a physical well wall (not a grid cell or the whole image)."""
    det = w.get('detection') or {}
    return not (det.get('grid_cell') or w.get('source') == 'whole_image')


def mask_digest(m, bbox):
    h = hashlib.sha256()
    h.update(json.dumps(list(map(int, bbox))).encode())
    h.update(np.packbits(np.asarray(m, bool), axis=None).tobytes())
    return h.hexdigest()[:16]


def contours_xy(mask, offset, sigma):
    """Boundary polygons in continuous image coords (same boundary as the measurements)."""
    if mask is None or not mask.any():
        return []
    cs = M._contours(mask, sigma)
    out = []
    for c in cs:
        c = _drop_collinear(c) if sigma == 0 else _simplify(c, 0.08, passes=3)
        c = np.round(c[:, ::-1] + 0.5 + np.asarray(offset, float), 2)
        out.append(c.tolist())
    return out


def _simplify(c, tol, passes=3):
    """Fast vectorised outline thinning for DISPLAY (measurements never use it): a vertex is
    dropped when it lies within `tol` px of the line through its two neighbours, never two
    neighbours in the same pass, so the drawn outline stays within passes x tol px (here 0.24 px)
    of the measured boundary. Replaces skimage.approximate_polygon, whose Python loop made the
    first opening of an image with many objects take 1-1.5 s."""
    if len(c) > 1 and np.allclose(c[0], c[-1]):
        c = c[:-1]
    for _ in range(passes):
        n = len(c)
        if n < 8:
            break
        a, b = np.roll(c, 1, axis=0), np.roll(c, -1, axis=0)
        ab = b - a
        L = np.hypot(ab[:, 0], ab[:, 1])
        dev = np.abs(ab[:, 0] * (c[:, 1] - a[:, 1]) - ab[:, 1] * (c[:, 0] - a[:, 0])) / np.maximum(L, 1e-9)
        drop = (dev < tol) & (np.arange(n) % 2 == 1)
        if drop.sum() == 0:
            break
        c = c[~drop]
    return c


def _drop_collinear(c):
    """Remove points lying on straight runs (exact, fast; for unsmoothed pixel outlines)."""
    if len(c) > 1 and np.allclose(c[0], c[-1]):
        c = c[:-1]                      # closed contour: drop the repeated end point
    if len(c) < 4:
        return c
    d = np.diff(np.vstack([c[-1:], c]), axis=0)
    d2 = np.roll(d, -1, axis=0)
    cross = d[:, 0] * d2[:, 1] - d[:, 1] * d2[:, 0]
    keep = np.abs(cross) > 1e-9
    return c[keep] if keep.sum() >= 3 else c


from .labels import parse_labels, clean_labels, labels_complete


class Session:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.lock = threading.RLock()
        self.path = os.path.join(self.root, 'session.json')
        self.job = {'running': False, 'done': 0, 'total': 0, 'current': None, 'errors': [], 'started': None,
                    'finished': None}
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as f:
                self.data = json.load(f)
            for im in self.data.get('images', []):      # sessions made before photo labels / hiding existed
                im.setdefault('hidden', False)
                if 'labels' not in im:
                    im['labels'] = parse_labels(im.get('original_filename', ''))
        else:
            os.makedirs(self.root, exist_ok=True)
            for d in ('originals', 'display', 'parts', 'masks', 'references', 'exports'):
                os.makedirs(os.path.join(self.root, d), exist_ok=True)
            with open(os.path.join(self.root, 'references', 'README.txt'), 'w', encoding='utf-8') as f:
                f.write('Reserved for researcher-approved reference masks used to VALIDATE segmentation.\n'
                        'Keep them separate from predictions (masks/*_auto.png) and from reviewed working\n'
                        'masks. Accuracy must be measured on images NOT used to tune the profiles.\n')
            self.data = dict(schema_version=1, app_version=APP_VERSION, name=os.path.basename(self.root),
                             created=now(), updated=now(), counters=dict(image=0, object=0, calibration=0),
                             images=[], objects={}, calibrations={})
            self.save()

    # ------------------------------------------------------------------ persistence
    def save(self):
        with self.lock:
            self.data['updated'] = now()
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                # one json.dumps + write: ~5x faster than json.dump(f), which encodes in pure Python
                # (12.7 MB session.json at 350 images: one accept 0.70 s -> 0.20 s, see docs/STATUS.md)
                f.write(json.dumps(self.data, ensure_ascii=False, indent=1))
            os.replace(tmp, self.path)

    def _next(self, kind, prefix, width):
        self.data['counters'][kind] += 1
        return f"{prefix}-{self.data['counters'][kind]:0{width}d}"

    def image(self, image_id):
        for im in self.data['images']:
            if im['image_id'] == image_id:
                return im
        raise KeyError(image_id)

    def obj(self, object_id):
        return self.data['objects'][object_id]

    def abspath(self, rel):
        return os.path.join(self.root, rel)

    # ------------------------------------------------------------------ images
    def add_image(self, src_path, original_filename):
        """Copy an uploaded file into the session (byte-identical), register it."""
        original_filename = safe_name(original_filename)
        with self.lock:
            image_id = self._next('image', 'IMG', 4)
        dst_dir = self.abspath(os.path.join('originals', image_id))
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, disk_name(original_filename))
        shutil.copyfile(src_path, dst)
        digest = sha256_file(dst)
        with Image.open(dst) as im:
            W, H = im.size
            mode = im.mode
        ext = os.path.splitext(original_filename)[1].lower()
        display = None
        if ext not in BROWSER_OK or mode not in ('RGB', 'RGBA', 'L', 'P', 'LA'):
            display = os.path.join('display', image_id + '.png')
            Image.fromarray(load_rgb8(dst)).save(self.abspath(display))
        cfg = load_config()
        rec = dict(image_id=image_id, original_filename=original_filename,
                   stored_path=os.path.join('originals', image_id, disk_name(original_filename)), display_path=display,
                   sha256=digest, width=W, height=H, mode=mode, added=now(), profile=cfg['default_profile'],
                   calibration_id=None, wells=[], processing=dict(status='not_processed'), image_flags=[],
                   notes='', review_done=False, used_for_tuning=False, preset_applied=None, well_detection=None,
                   hidden=False, labels=parse_labels(original_filename),
                   events=[[now(), 'uploaded', None]])
        for p in load_presets():
            if p['filename'] == original_filename and p['sha256'] == digest:
                rec['profile'] = p['profile']
                rec['wells'] = [dict(well_id=w['well_id'], polygon=w['polygon'], source='example_preset',
                                     no_measurable_object=False, note='') for w in p['wells']]
                rec['image_flags'] = [dict(f, set_by='example_preset', time=now()) for f in p.get('image_flags', [])]
                rec['used_for_tuning'] = bool(p.get('used_for_tuning'))
                rec['preset_applied'] = 'config/example_presets.json'
                se = p.get('scale_estimate')
                with self.lock:
                    self.data['images'].append(rec)
                if se:
                    self.add_calibration(image_id, se['line'], se['known_length'], se['unit'], confirmed=False,
                                         note=se.get('note', ''))
                break
        else:
            with self.lock:
                self.data['images'].append(rec)
        cid = self.data.get('scale_cal_id')
        if cid:
            self.apply_calibration(cid, [image_id], True)
        self.save()
        return rec

    # ------------------------------------------------------------------ photo labels / hiding
    def set_labels(self, image_ids, labels):
        """Set (partial) labels on one or many photos. Unknown keys are ignored, bad values raise."""
        lb = clean_labels(labels)
        with self.lock:
            for iid in image_ids:
                im = self.image(iid)
                im['labels'] = dict(im.get('labels') or parse_labels(''), **lb)
                self._event(iid, 'labels', None)
            self.save()
        return lb

    def set_hidden(self, image_ids, hidden):
        """Hide / show photos. Hidden photos stay in the session (nothing is deleted) but are skipped by
        detection, review, scale application and export."""
        with self.lock:
            for iid in image_ids:
                self.image(iid)['hidden'] = bool(hidden)
                self._event(iid, 'hidden' if hidden else 'shown', None)
            self.save()

    def delete_images(self, image_ids):
        """Permanently remove photos and all associated data from the session."""
        with self.lock:
            for iid in image_ids:
                im = self.image(iid)
                # remove associated objects and their masks
                to_del = [oid for oid, o in self.data['objects'].items() if o['image_id'] == iid]
                for oid in to_del:
                    o = self.data['objects'][oid]
                    for suffix in ('', '_auto'):
                        p = self.abspath(o.get('mask_file', '').replace('.png', suffix + '.png'))
                        if os.path.exists(p):
                            os.remove(p)
                    del self.data['objects'][oid]
                # remove files on disk
                for subdir in ('originals', 'parts', 'masks', 'references'):
                    d = self.abspath(os.path.join(subdir, iid))
                    if os.path.isdir(d):
                        shutil.rmtree(d)
                if im.get('display_path'):
                    p = self.abspath(im['display_path'])
                    if os.path.exists(p):
                        os.remove(p)
                undo_p = self._undo_path(iid)
                if os.path.exists(undo_p):
                    os.remove(undo_p)
                self.data['images'] = [i for i in self.data['images'] if i['image_id'] != iid]
            self.save()

    def visible_ids(self):
        return [i['image_id'] for i in self.data['images'] if not i.get('hidden')]

    def review_counts(self, image_id):
        c = dict(total=0, unreviewed=0, accepted=0, rejected=0)
        for o in self.data['objects'].values():
            if o['image_id'] == image_id:
                c['total'] += 1
                c[o['status']] += 1
        return c

    def reviewed(self, image_id):
        """Derived 'review complete': processed and no object is left in 'to review'."""
        im = self.image(image_id)
        return im['processing'].get('status') == 'processed' and self.review_counts(image_id)['unreviewed'] == 0

    def ensure_region(self, image_id):
        """Wells are not part of the researcher's workflow: every photo gets ONE hidden region covering
        the whole image (the region the segmenters and the drawing tools work in)."""
        with self.lock:
            im = self.image(image_id)
            if not im['wells']:
                self.whole_image_region(image_id)

    def export_blockers(self):
        """Why the grouped Excel cannot be produced yet (empty list = ready)."""
        with self.lock:
            out = dict(unprocessed=[], to_review=0, to_review_images=[], missing_labels=[], scale=[])
            vis = [i for i in self.data['images'] if not i.get('hidden')]
            for im in vis:
                iid = im['image_id']
                if im['processing'].get('status') != 'processed':
                    out['unprocessed'].append(iid)
                n = self.review_counts(iid)['unreviewed']
                if n:
                    out['to_review'] += n
                    out['to_review_images'].append(iid)
                if not labels_complete(im.get('labels')):
                    out['missing_labels'].append(iid)
                if self.calibration_state(iid)['status'] != 'confirmed':
                    out['scale'].append(iid)
            out['n_visible'] = len(vis)
            out['ready'] = bool(vis) and not (out['unprocessed'] or out['to_review'] or out['missing_labels']
                                              or out['scale'])
            return out

    def set_profile(self, image_id, profile):
        cfg = load_config()
        if profile not in cfg['profiles']:
            raise ValueError(f'unknown profile {profile}')
        with self.lock:
            self.image(image_id)['profile'] = profile
            self._event(image_id, 'profile')
            self.save()

    def set_wells(self, image_id, wells):
        with self.lock:
            im = self.image(image_id)
            old = {w['well_id']: w for w in im['wells']}
            new = []
            for w in wells:
                poly = [[float(x), float(y)] for x, y in w['polygon']]
                if len(poly) < 3:
                    raise ValueError('a well needs at least 3 vertices')
                o = old.get(w['well_id'], {})
                moved = o and o.get('polygon') != poly
                src = w.get('source', o.get('source', 'manual'))
                if moved and src in ('auto', 'example_preset'):
                    src = src + ' (edited)'
                elif moved and src == 'auto (grid cell)':
                    src = 'auto (grid cell, edited)'
                new.append(dict(well_id=str(w['well_id']), polygon=poly, source=src,
                                detection=w.get('detection', o.get('detection')),
                                no_measurable_object=bool(w.get('no_measurable_object', o.get('no_measurable_object', False))),
                                note=w.get('note', o.get('note', ''))))
            ids = [w['well_id'] for w in new]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate well ids')
            im['wells'] = new
            if im['processing'].get('status') == 'processed':
                im['processing']['wells_changed_since_run'] = True
            self._event(image_id, 'wells_edit')
            self.save()
            return new

    def copy_wells(self, image_id, from_image_id):
        src = self.image(from_image_id)
        wells = [dict(well_id=w['well_id'], polygon=copy.deepcopy(w['polygon']), source=f'copied from {from_image_id}',
                      detection=copy.deepcopy(w.get('detection')))     # keeps the grid-cell marker
                 for w in src['wells']]
        return self.set_wells(image_id, wells)

    def set_image_meta(self, image_id, notes=None, review_done=None, flags=None):
        with self.lock:
            im = self.image(image_id)
            if notes is not None:
                im['notes'] = notes
            if review_done is not None:
                im['review_done'] = bool(review_done)
            if flags is not None:   # list of {"flag","reason"} set by the user
                keep = [f for f in im['image_flags'] if f.get('set_by') != 'user']
                im['image_flags'] = keep + [dict(flag=f['flag'], reason=f.get('reason', ''), set_by='user', time=now())
                                            for f in flags]
            self._event(image_id, 'review_done' if review_done else 'image_meta')
            self.save()

    def remove_image_flag(self, image_id, flag):
        with self.lock:
            im = self.image(image_id)
            im['image_flags'] = [f for f in im['image_flags'] if f['flag'] != flag]
            self.save()

    def set_well_meta(self, image_id, well_id, no_measurable_object=None, note=None):
        with self.lock:
            for w in self.image(image_id)['wells']:
                if w['well_id'] == well_id:
                    if no_measurable_object is not None:
                        w['no_measurable_object'] = bool(no_measurable_object)
                    if note is not None:
                        w['note'] = note
                    self._event(image_id, 'well_meta')
                    self.save()
                    return w
        raise KeyError(well_id)

    # ------------------------------------------------------------------ calibration
    def add_calibration(self, image_id, line, known_length, unit, confirmed, note=''):
        unit = unit.strip()
        if unit not in UNITS_TO_UM:
            raise ValueError(f'unit must be one of {sorted(UNITS_TO_UM)}')
        (x1, y1), (x2, y2) = line
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 2:
            raise ValueError('scale line is too short')
        if float(known_length) <= 0:
            raise ValueError('known length must be positive')
        um_per_px = float(known_length) * UNITS_TO_UM[unit] / L
        with self.lock:
            cal_id = self._next('calibration', 'CAL', 3)
            self.data['calibrations'][cal_id] = dict(
                cal_id=cal_id, um_per_px=um_per_px, line=[[x1, y1], [x2, y2]], line_length_px=L,
                known_length=float(known_length), unit=unit, source_image_id=image_id,
                status='confirmed' if confirmed else 'unconfirmed_estimate', note=note, created=now(),
                confirmed_at=now() if confirmed else None, applied_to=[image_id],
                compatibility_confirmed_for=[image_id] if confirmed else [])
            self.image(image_id)['calibration_id'] = cal_id
            self._event(image_id, 'calibration' if confirmed else 'calibration_estimate')
            self.save()
            return self.data['calibrations'][cal_id]

    def confirm_calibration(self, cal_id, image_id):
        """User reviewed the scale line on `image_id` and confirms it."""
        with self.lock:
            c = self.data['calibrations'][cal_id]
            if self.image(image_id)['calibration_id'] != cal_id:
                raise ValueError('image does not use this calibration')
            c['status'] = 'confirmed'
            c['confirmed_at'] = now()
            if image_id not in c['compatibility_confirmed_for']:
                c['compatibility_confirmed_for'].append(image_id)
            self._event(image_id, 'calibration_confirm')
            self.save()

    def apply_calibration(self, cal_id, image_ids, compatible_confirmed):
        if not compatible_confirmed:
            raise ValueError('Applying a calibration to other images requires confirming that they share '
                             'acquisition and resizing conditions.')
        with self.lock:
            c = self.data['calibrations'][cal_id]
            if c['status'] != 'confirmed':
                raise ValueError('confirm the calibration on its source image first')
            for iid in image_ids:
                self.image(iid)['calibration_id'] = cal_id
                for lst in (c['applied_to'], c['compatibility_confirmed_for']):
                    if iid not in lst:
                        lst.append(iid)
            self.save()

    # ------------------------------------------------------------------ one scale for the whole session
    def set_session_scale(self, line, known_length, unit, source_image_id):
        """The researcher confirmed the scale bar on ONE photo; the same scale is used for every photo of
        the session (hidden ones included, so restoring a photo needs no extra step). Measurements are
        converted to um when they are read, so changing the scale later re-scales every object."""
        cal = self.add_calibration(source_image_id, line, known_length, unit, confirmed=True,
                                   note='session scale (confirmed by researcher)')
        with self.lock:
            self.data['scale_cal_id'] = cal['cal_id']
            self.apply_calibration(cal['cal_id'], [i['image_id'] for i in self.data['images']], True)
        return cal

    def scale_state(self):
        with self.lock:
            cid = self.data.get('scale_cal_id')
            c = self.data['calibrations'].get(cid) if cid else None
            if not c:
                return dict(status='none', um_per_px=None, cal_id=None)
            src = self.image(c['source_image_id'])
            odd = [i['image_id'] for i in self.data['images']
                   if (i['width'], i['height']) != (src['width'], src['height'])]
            return dict(status='confirmed', um_per_px=c['um_per_px'], cal_id=cid, line=c['line'],
                        known_length=c['known_length'], unit=c['unit'], source_image_id=c['source_image_id'],
                        different_size=odd)

    def clear_calibration(self, image_id):
        with self.lock:
            im = self.image(image_id)
            cid = im['calibration_id']
            im['calibration_id'] = None
            if cid:
                c = self.data['calibrations'][cid]
                c['compatibility_confirmed_for'] = [i for i in c['compatibility_confirmed_for'] if i != image_id]
            self.save()

    def calibration_state(self, image_id):
        im = self.image(image_id)
        cid = im['calibration_id']
        if not cid:
            return dict(status='none', um_per_px=None, cal_id=None, estimate=None)
        c = self.data['calibrations'][cid]
        ok = c['status'] == 'confirmed' and image_id in c['compatibility_confirmed_for']
        return dict(status='confirmed' if ok else 'unconfirmed', um_per_px=c['um_per_px'] if ok else None,
                    cal_id=cid, estimate=None if ok else c['um_per_px'])

    # ------------------------------------------------------------------ masks
    def _mask_rel(self, image_id, object_id, auto=False):
        return os.path.join('masks', image_id, object_id + ('_auto' if auto else '') + '.png')

    def load_mask(self, o, auto=False):
        p = self.abspath(o['auto_mask_file'] if auto else o['mask_file'])
        if not os.path.exists(p):
            return None
        return np.asarray(Image.open(p)) > 0

    def _write_mask(self, rel, mask):
        p = self.abspath(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Image.fromarray(mask.astype(np.uint8) * 255).convert('1').save(p, optimize=True)

    def _store_mask(self, o, full_crop, offset):
        """Tighten to bbox, save, re-measure, update flags."""
        self._snap(o)
        ys, xs = np.nonzero(full_crop)
        if len(ys) == 0:
            o['bbox'] = None
            o['measure_px'] = None
            p = self.abspath(o['mask_file'])
            if os.path.exists(p):
                os.remove(p)
            o['flags']['touches_well_boundary'] = False
            o['flags']['touches_image_edge'] = False
            return
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        m = full_crop[y0:y1, x0:x1]
        o['bbox'] = [int(offset[0] + x0), int(offset[1] + y0), int(offset[0] + x1), int(offset[1] + y1)]
        self._write_mask(o['mask_file'], m)
        o['mask_version'] = int(o.get('mask_version') or 0) + 1
        o['mask_sha256'] = mask_digest(m, o['bbox'])
        o['added_px'] = self._added_px(o, m)
        self._measure(o, m)
        self._auto_flags(o, m)

    def _added_px(self, o, m):
        """Object pixels that were ADDED by gap closing / hole filling (not above threshold)."""
        x0, y0, x1, y1 = o['bbox']
        n = 0
        for w in self.image(o['image_id'])['wells']:
            p = self.abspath(os.path.join('parts', o['image_id'], f"{w['well_id']}.npz"))
            if not os.path.exists(p):
                continue
            z = np.load(p)
            if 'added' not in z:
                continue
            ad = z['added']
            ox, oy = (int(v) for v in z['offset'])
            ax0, ay0 = max(x0, ox), max(y0, oy)
            ax1, ay1 = min(x1, ox + ad.shape[1]), min(y1, oy + ad.shape[0])
            if ax1 <= ax0 or ay1 <= ay0:
                continue
            n += int((ad[ay0 - oy:ay1 - oy, ax0 - ox:ax1 - ox] & m[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]).sum())
        return n

    def _mask_edited(self, o, action):
        """Every mask change: history entry; a previous accept/reject no longer covers the new
        mask, so the object goes back to 'unreviewed' (the old approval stays in history)."""
        o['edited'] = True
        self._touch(o, action)
        if o['status'] != 'unreviewed':
            self._touch(o, f"{o['status']} reset to unreviewed: mask changed after review "
                           f"(approved version {(o.get('approval') or {}).get('mask_version')})")
            o['status'] = 'unreviewed'
            o['approval'] = None
        self._event(o['image_id'], action.split(' ')[0], o['object_id'])

    def _event(self, image_id, action, object_id=None):
        im = self.image(image_id)
        im.setdefault('events', []).append([now(), action, object_id])

    def _measure(self, o, m):
        cfg = load_config()['measurement']
        M.BOUNDARY_SIGMA_PX = float(cfg['boundary_sigma_px'])
        res = M.measure_object(m, cfg['envelope_radius_px'])
        o['measure_px'] = {k: v['px'] for k, v in res.items()}
        o['envelope_radius_px'] = cfg['envelope_radius_px']
        o['boundary_sigma_px'] = cfg['boundary_sigma_px']
        ys, xs = np.nonzero(m)
        o['centroid'] = [round(float(xs.mean()) + 0.5 + o['bbox'][0], 2), round(float(ys.mean()) + 0.5 + o['bbox'][1], 2)]

    def _well(self, im, well_id):
        for w in im['wells']:
            if w['well_id'] == well_id:
                return w
        return None

    def _auto_flags(self, o, m):
        im = self.image(o['image_id'])
        x0, y0, x1, y1 = o['bbox']
        o['flags']['touches_image_edge'] = bool(x0 <= 0 or y0 <= 0 or x1 >= im['width'] or y1 >= im['height'])
        w = self._well(im, o.get('well_id'))
        if w is None:
            o['flags']['touches_well_boundary'] = False
            return
        prof = load_config()['profiles'].get(o.get('settings', {}).get('profile') or im['profile'], {})
        pp = prof.get('params', {})
        wb = poly_bbox(w['polygon'], im['width'], im['height'])
        well_size = min(wb[2] - wb[0], wb[3] - wb[1])
        # same exclusion band the segmenter used (px for classical, fraction of well size for adaptive)
        margin = float(pp.get('wall_margin_px', 0)) or float(pp.get('wall_margin_frac', 0)) * well_size
        if not wall_like(w):
            margin = 0.0
        pad = int(margin) + 4
        X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
        X1, Y1 = min(im['width'], x1 + pad), min(im['height'], y1 + pad)
        full = np.zeros((Y1 - Y0, X1 - X0), bool)
        full[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = m
        # distance to the well boundary must be computed on the whole well, not the crop:
        wx0, wy0, wx1, wy1 = poly_bbox(w['polygon'], im['width'], im['height'], pad=2)
        wreg = polygon_mask(w['polygon'], (wy1 - wy0, wx1 - wx0), (wx0, wy0))
        dist = ndi.distance_transform_edt(np.pad(wreg, 1))[1:-1, 1:-1]
        sy0, sx0 = max(Y0, wy0), max(X0, wx0)
        sy1, sx1 = min(Y1, wy1), min(X1, wx1)
        near = np.ones_like(full)   # outside the well bbox counts as "at/over the boundary"
        if sy1 > sy0 and sx1 > sx0:
            near[sy0 - Y0:sy1 - Y0, sx0 - X0:sx1 - X0] = dist[sy0 - wy0:sy1 - wy0, sx0 - wx0:sx1 - wx0] <= margin + 2
        o['flags']['touches_well_boundary'] = bool((full & near).any())

    def _new_object(self, image_id, well_id, source, settings):
        with self.lock:
            oid = self._next('object', 'OBJ', 6)
        o = dict(object_id=oid, image_id=image_id, well_id=well_id, source=source, edited=False,
                 status='unreviewed', bbox=None, mask_file=self._mask_rel(image_id, oid),
                 auto_mask_file=self._mask_rel(image_id, oid, auto=True) if source == 'auto' else None,
                 parts=[], flags=dict(touches_well_boundary=False, touches_image_edge=False,
                                      ambiguous_boundary=False, unreliable_segmentation=False, partial_object=False),
                 notes='', measure_px=None, settings=settings, created=now(), updated=now(),
                 mask_version=0, mask_sha256=None, auto_bbox=None, approval=None, added_px=0,
                 history=[dict(time=now(), action=f'created ({source})')])
        return o

    # ------------------------------------------------------------------ automatic detection
    def process_image(self, image_id, whole_region=False):
        """Run the configured segmenter on every well of one image. Keeps reviewed, manual
        and edited objects; replaces only unreviewed, unedited automatic ones."""
        im = self.image(image_id)
        _prof = load_config()['profiles'][im['profile']]
        if whole_region and not im['wells']:
            self.ensure_region(image_id)
        if _prof['params'].get('whole_image'):
            # whole-image models (Cellpose) do not need wells: use one region covering the image
            ws = im['wells']
            if not (len(ws) == 1 and ws[0].get('source') == 'whole_image'):
                self.whole_image_region(image_id)
        if not im['wells']:
            with self.lock:
                im['processing'] = dict(status='needs_wells', time=now(),
                                        message='No well regions. Draw wells (or copy them) before detection.')
                self.save()
            return im['processing']
        cfg = load_config()
        prof = cfg['profiles'][im['profile']]
        seg = get_segmenter(prof['segmenter'])
        settings = dict(profile=im['profile'], segmenter=prof['segmenter'], params_hash=params_hash(prof),
                        app_version=APP_VERSION)
        rgb = load_rgb8(self.abspath(im['stored_path']))
        H, W = rgb.shape[:2]
        with self.lock:
            kept = [o for o in self.data['objects'].values() if o['image_id'] == image_id and
                    (o['status'] != 'unreviewed' or o['source'] != 'auto' or o['edited'])]
            drop = [o for o in self.data['objects'].values() if o['image_id'] == image_id and o not in kept]
            for o in drop:
                for rel in (o['mask_file'], o.get('auto_mask_file')):
                    if rel and os.path.exists(self.abspath(rel)):
                        os.remove(self.abspath(rel))
                del self.data['objects'][o['object_id']]
            if os.path.exists(self._undo_path(image_id)):   # undo steps refer to the replaced objects/parts
                self._undo_write(image_id, [])
        for o in kept:   # part ids refer to the previous run's part maps, which are replaced now
            if o['parts']:
                o['parts'] = []
                self._touch(o, 'part references cleared (detection re-run); mask unchanged')
        kept_masks = [(o, self.load_mask(o)) for o in kept if o['bbox']]
        parts_dir = self.abspath(os.path.join('parts', image_id))
        if os.path.isdir(parts_dir):
            shutil.rmtree(parts_dir)
        os.makedirs(parts_dir, exist_ok=True)
        n_new = 0
        per_well = {}
        for w in im['wells']:
            x0, y0, x1, y1 = poly_bbox(w['polygon'], W, H)
            if x1 - x0 < 3 or y1 - y0 < 3:
                continue
            region = polygon_mask(w['polygon'], (y1 - y0, x1 - x0), (x0, y0))
            params = dict(prof['params'])
            if not wall_like(w):
                # grid cells / whole image: the region edge is not a physical wall -> no wall band
                params.update(wall_margin_frac=0.0, wall_margin_px=0, wall_band_frac=0.0, wall_band_extra_px=0)
            res = seg.segment(rgb[y0:y1, x0:x1], region, params)
            rys, rxs = np.nonzero(region)
            well_size = int(min(np.ptp(rys), np.ptp(rxs)) + 1) if len(rys) else 0
            gp = resolve_grouping(prof['grouping'], well_size)
            labels, meta = group_parts(res.cells, res.excluded, gp, res.removed)
            by_reason = {}
            for m in meta:
                if m['kind'] == 'excluded':
                    r_ = by_reason.setdefault(m.get('reason', 'wall_shape'), [0, 0])
                    r_[0] += 1
                    r_[1] += m['area']
            per_well[w['well_id']] = dict(well_size_px=well_size, grouping_px={k: round(float(v), 2) for k, v in gp.items()
                                                                                if isinstance(v, (int, float))},
                                          segmenter_info=res.info,
                                          n_parts=sum(m['kind'] != 'excluded' for m in meta),
                                          removed_parts={k: dict(parts=v[0], pixels=v[1]) for k, v in by_reason.items()})
            added = res.added if res.added is not None else np.zeros(labels.shape, bool)
            np.savez_compressed(os.path.join(parts_dir, f"{w['well_id']}.npz"), labels=labels.astype(np.int32),
                                offset=np.array([x0, y0]), meta=json.dumps(meta), added=added)
            self._write_part_contours(image_id, w['well_id'], labels, (x0, y0), meta)
            for sd in [m for m in meta if m['kind'] == 'seed']:
                mem = [m for m in meta if m['seed'] == sd['part'] and m['kind'] in ('seed', 'assigned')]
                members = [m['part'] for m in mem]
                mask = np.isin(labels, members)
                if self._overlaps_kept(mask, (x0, y0), kept_masks):
                    continue
                o = self._new_object(image_id, w['well_id'], 'auto', settings)
                o['parts'] = [dict(well_id=w['well_id'], part=m['part'],
                                   how='seed' if m['kind'] == 'seed' else 'proximity') for m in mem]
                self._store_mask(o, mask, (x0, y0))
                o['auto_bbox'] = list(o['bbox'])          # placement of the *_auto.png crop, never changed
                ys, xs = np.nonzero(mask)
                self._write_mask(o['auto_mask_file'], mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
                with self.lock:
                    self.data['objects'][o['object_id']] = o
                n_new += 1
            del res, labels
        with self.lock:
            im['processing'] = dict(status='processed', time=now(), new_objects=n_new, kept_objects=len(kept),
                                    params=copy.deepcopy(prof), per_well=per_well, **settings)
            self._event(image_id, 'detect_objects')
            self.save()
        del rgb
        return im['processing']

    def _overlaps_kept(self, mask, off, kept_masks):
        ys, xs = np.nonzero(mask)
        if len(ys) == 0:
            return True
        pts = set(zip((ys + off[1]).tolist(), (xs + off[0]).tolist()))
        for o, km in kept_masks:
            if km is None:
                continue
            bx0, by0 = o['bbox'][0], o['bbox'][1]
            kys, kxs = np.nonzero(km)
            inter = len(pts.intersection(zip((kys + by0).tolist(), (kxs + bx0).tolist())))
            if inter > 0.5 * len(pts):
                return True
        return False

    def detect_wells(self, image_id, replace=False, params=None):
        """Automatic well proposal (app/wells.py). Stores status, reason, info and the full
        parameter set on the image. Existing wells are only replaced when `replace` is True
        or the image has none. Failure leaves existing wells untouched."""
        im = self.image(image_id)
        cfg = load_config().get('well_detection', {})
        p = dict(cfg.get('params', {}))
        p.update(params or {})
        rgb = load_rgb8(self.abspath(im['stored_path']))
        res = detect_wells(rgb, p)
        del rgb
        rec = dict(status=res['status'], reason=res.get('reason', ''), method=WELL_METHOD, time=now(),
                   params={k: (list(v) if isinstance(v, tuple) else v) for k, v in res['params'].items()},
                   info=res.get('info', {}), n_wells=len(res['wells']), applied=False)
        with self.lock:
            if res['status'] == 'ok' and (replace or not im['wells']):
                im['wells'] = [dict(well_id=w['well_id'], polygon=w['polygon'],
                                    source='auto (grid cell)' if w.get('grid_cell') else 'auto',
                                    no_measurable_object=False, note='',
                                    detection=dict(score=w['score'], rel_score=w['rel_score'], partial=w['partial'],
                                                   shape=w['shape'], grid_cell=bool(w.get('grid_cell'))))
                               for w in res['wells']]
                rec['applied'] = True
                if im['processing'].get('status') == 'processed':
                    im['processing']['wells_changed_since_run'] = True
            elif res['status'] == 'ok':
                rec['reason'] = 'wells found but not applied: image already has wells (use replace)'
            im['well_detection'] = rec
            self._event(image_id, 'detect_wells')
            self.save()
        return rec

    # ------------------------------------------------------------------ ImageJ reference outlines
    def add_reference_file(self, image_id, filename, data):
        """Store an ImageJ RoiSet .zip / .roi (reference outlines) or a Results .csv for one image.
        The file is kept byte-identical under references/<image>/. References are for comparison
        only: they are never objects and never enter the Measurements sheet."""
        from . import references as REF
        im = self.image(image_id)
        is_csv = filename.lower().endswith('.csv')
        try:
            if is_csv:
                res = REF.parse_results_csv(data)
            else:
                rois, skipped = REF.parse_roi_file(filename, data)
        except Exception as e:   # noqa: BLE001 - any parse error is reported to the user as such
            raise ValueError(f'could not read {filename!r} as an ImageJ {"Results table" if is_csv else "ROI file"}: {e}')
        with self.lock:
            k = len(im.get('reference_files', [])) + 1
            rel = os.path.join('references', image_id, f'{k:02d}_{disk_name(filename)}')
            os.makedirs(os.path.dirname(self.abspath(rel)), exist_ok=True)
            with open(self.abspath(rel), 'wb') as f:
                f.write(data)
            rec = dict(file_id=f'F{k}', original_filename=filename, stored_path=rel,
                       sha256=hashlib.sha256(data).hexdigest(), uploaded=now(), kind='results' if is_csv else 'rois')
            if is_csv:
                rec.update(n_rows=len(res['rows']), columns=res['columns'])
                im['imagej_results'] = dict(res, file_id=rec['file_id'], original_filename=filename)
            else:
                refs = im.setdefault('references', [])
                W, H = im['width'], im['height']
                n0 = len(refs)
                for r in rois:
                    l, t, rr, b = r['bounds']
                    if rr <= 0 or b <= 0 or l >= W or t >= H:
                        skipped.append(dict(index=r['index'], name=r['name'],
                                            reason=f'outside this image ({W}x{H} px): drawn on another image or size?'))
                        continue
                    r.update(ref_id=f'R{len(refs) + 1}', file_id=rec['file_id'],
                             partly_outside=bool(l < 0 or t < 0 or rr > W or b > H))
                    refs.append(r)
                rec.update(n_rois=len(refs) - n0, skipped=skipped)
            im.setdefault('reference_files', []).append(rec)
            self._event(image_id, 'reference_upload')
            self.save()
            return rec

    def clear_references(self, image_id):
        with self.lock:
            im = self.image(image_id)
            for rec in im.get('reference_files', []):
                p = self.abspath(rec['stored_path'])
                if os.path.exists(p):
                    os.remove(p)
            im['references'], im['reference_files'] = [], []
            im.pop('imagej_results', None)
            self._event(image_id, 'reference_clear')
            self.save()

    def whole_image_region(self, image_id):
        im = self.image(image_id)
        W, H = im['width'], im['height']
        return self.set_wells(image_id, [dict(well_id='IMG', polygon=[[0, 0], [W, 0], [W, H], [0, H]],
                                              source='whole_image')])

    def set_profile_many(self, image_ids, profile):
        for iid in image_ids:
            self.set_profile(iid, profile)

    def start_batch(self, image_ids, detect_wells_first=True, whole_region=False):
        image_ids = [i for i in image_ids if not self.image(i).get('hidden')]
        with self.lock:
            if self.job['running']:
                raise RuntimeError('a batch is already running')
            self.job = dict(running=True, done=0, total=len(image_ids), current=None, errors=[], started=now(),
                            finished=None)

        def work():
            for iid in image_ids:
                self.job['current'] = iid
                try:
                    if detect_wells_first and not self.image(iid)['wells'] and not load_config()['profiles'][self.image(iid)['profile']]['params'].get('whole_image'):
                        self.detect_wells(iid)
                    self.process_image(iid, whole_region=whole_region)
                except Exception as e:  # keep going; report per image
                    self.job['errors'].append(dict(image_id=iid, error=repr(e)))
                    with self.lock:
                        self.image(iid)['processing'] = dict(status='error', time=now(), error=repr(e))
                        self.save()
                self.job['done'] += 1
            self.job['running'] = False
            self.job['current'] = None
            self.job['finished'] = now()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        return self.job

    # ------------------------------------------------------------------ parts (dispersed components)
    def load_parts(self, image_id, well_id):
        p = self.abspath(os.path.join('parts', image_id, f'{well_id}.npz'))
        if not os.path.exists(p):
            return None, None, None
        z = np.load(p)
        return z['labels'], tuple(int(v) for v in z['offset']), json.loads(str(z['meta']))

    def _write_part_contours(self, image_id, well_id, labels, off, meta):
        sl_all = ndi.find_objects(labels)
        out = {}
        for m in meta:
            sl = sl_all[m['part'] - 1] if m['part'] - 1 < len(sl_all) else None
            if sl is None:
                continue
            ox, oy = off[0] + sl[1].start, off[1] + sl[0].start
            if m['area'] < 16:   # tiny fragment: its pixel bbox is an adequate outline for display
                h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
                out[str(m['part'])] = [[[ox, oy], [ox + w, oy], [ox + w, oy + h], [ox, oy + h]]]
                continue
            sub = labels[sl] == m['part']
            out[str(m['part'])] = contours_xy(sub, (ox, oy), 0.0)
        with open(self.abspath(os.path.join('parts', image_id, f'{well_id}_contours.json')), 'w') as f:
            f.write(json.dumps(out, separators=(',', ':')))

    def _part_contours(self, image_id, well_id):
        p = self.abspath(os.path.join('parts', image_id, f'{well_id}_contours.json'))
        if not os.path.exists(p):           # sessions from older versions: build the cache once
            labels, off, meta = self.load_parts(image_id, well_id)
            if labels is None:
                return {}
            self._write_part_contours(image_id, well_id, labels, off, meta)
        with open(p) as f:
            return json.load(f)

    def part_owner_map(self, image_id):
        own = {}
        for o in self.data['objects'].values():
            if o['image_id'] == image_id:
                for p in o['parts']:
                    own[(p['well_id'], p['part'])] = o['object_id']
        return own

    # ------------------------------------------------------------------ edits
    def _object_canvas(self, o, extra_box=None):
        """Return (full_mask, offset) covering the object's bbox and an extra box."""
        im = self.image(o['image_id'])
        boxes = []
        if o['bbox']:
            boxes.append(o['bbox'])
        if extra_box:
            boxes.append(extra_box)
        x0 = max(0, min(b[0] for b in boxes))
        y0 = max(0, min(b[1] for b in boxes))
        x1 = min(im['width'], max(b[2] for b in boxes))
        y1 = min(im['height'], max(b[3] for b in boxes))
        full = np.zeros((y1 - y0, x1 - x0), bool)
        if o['bbox']:
            m = self.load_mask(o)
            bx0, by0, bx1, by1 = o['bbox']
            full[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0] = m
        return full, (x0, y0)

    def _touch(self, o, action):
        o['updated'] = now()
        o['history'].append(dict(time=now(), action=action))

    # ------------------------------------------------------------------ undo
    # One reviewer action (brush stroke, drawn shape, part assignment incl. moving a part between
    # objects, new object, accept/reject, delete) = one undo step. Before an object changes, its
    # state and mask file are copied into the step; undo puts them back exactly (mask, bbox, parts,
    # version, status and approval). The object history is never rewritten: undo adds an entry.
    @contextlib.contextmanager
    def _undoable(self, image_id, label):
        outer = getattr(self, '_undo_cur', None) is None
        if outer:
            self._undo_cur = dict(image_id=image_id, label=label, time=now(), objects={})
        try:
            yield
            if outer and self._undo_cur['objects']:
                stack = self._undo_stack(image_id)
                stack.append(self._undo_cur)
                self._undo_write(image_id, stack[-UNDO_DEPTH:])
        finally:
            if outer:
                self._undo_cur = None

    def _snap(self, o):
        u = getattr(self, '_undo_cur', None)
        if u is None or o['object_id'] in u['objects']:
            return
        p = self.abspath(o['mask_file'])
        u['objects'][o['object_id']] = dict(
            existed=o['object_id'] in self.data['objects'], obj=copy.deepcopy(o),
            mask_b64=base64.b64encode(open(p, 'rb').read()).decode() if os.path.exists(p) else None)

    def _undo_path(self, image_id):
        return self.abspath(os.path.join('undo', f'{image_id}.json'))

    def _undo_stack(self, image_id):
        p = self._undo_path(image_id)
        if not os.path.exists(p):
            return []
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def _undo_write(self, image_id, stack):
        p = self._undo_path(image_id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + '.tmp', 'w', encoding='utf-8') as f:
            f.write(json.dumps(stack, ensure_ascii=False))
        os.replace(p + '.tmp', p)

    def undo_info(self, image_id):
        stack = self._undo_stack(image_id)
        return dict(steps=len(stack), next=stack[-1]['label'] if stack else None)

    def undo(self, image_id):
        """Revert the most recent reviewer action on this image."""
        with self.lock:
            stack = self._undo_stack(image_id)
            if not stack:
                raise ValueError('nothing to undo on this image')
            step = stack.pop()
            changed = []
            for oid, rec in step['objects'].items():
                cur = self.data['objects'].get(oid)
                if not rec['existed']:            # created by the undone action: remove it again
                    if cur:
                        for rel in (cur['mask_file'], cur.get('auto_mask_file')):
                            if rel and os.path.exists(self.abspath(rel)):
                                os.remove(self.abspath(rel))
                        del self.data['objects'][oid]
                        changed.append(dict(object_id=oid, restored='removed (it was created by this action)'))
                    continue
                snap = rec['obj']
                if cur is None:                   # deleted by the undone action: bring it back
                    cur = copy.deepcopy(snap)
                    self.data['objects'][oid] = cur
                else:
                    for k in UNDO_FIELDS:
                        cur[k] = copy.deepcopy(snap.get(k))
                    for f in ('touches_well_boundary', 'touches_image_edge'):
                        cur['flags'][f] = snap['flags'].get(f, False)
                p = self.abspath(cur['mask_file'])
                if rec['mask_b64'] is not None:
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, 'wb') as f:
                        f.write(base64.b64decode(rec['mask_b64']))
                elif os.path.exists(p):
                    os.remove(p)
                self._touch(cur, f"undo of '{step['label']}': mask v{cur.get('mask_version')} and status "
                                 f"{cur['status']} restored")
                changed.append(dict(object_id=oid, restored=f"mask v{cur.get('mask_version')}, {cur['status']}"))
            self._undo_write(image_id, stack)
            self._event(image_id, 'undo')
            self.save()
            return dict(undone=step['label'], objects=changed, **self.undo_info(image_id))

    def edit_brush(self, object_id, points, radius, mode):
        with self.lock, self._undoable(self.obj(object_id)['image_id'], f'brush {mode}'):
            o = self.obj(object_id)
            im = self.image(o['image_id'])
            r = float(radius)
            pts = np.asarray(points, float)
            box = [int(math.floor(pts[:, 0].min() - r - 1)), int(math.floor(pts[:, 1].min() - r - 1)),
                   int(math.ceil(pts[:, 0].max() + r + 1)), int(math.ceil(pts[:, 1].max() + r + 1))]
            box = [max(0, box[0]), max(0, box[1]), min(im['width'], box[2]), min(im['height'], box[3])]
            full, (ox, oy) = self._object_canvas(o, box)
            stroke = np.zeros_like(full)
            seq = [pts[0]]
            for a, b in zip(pts[:-1], pts[1:]):
                n = max(1, int(math.ceil(np.hypot(*(b - a)) / max(r / 3, 0.5))))
                seq += [a + (b - a) * t for t in np.linspace(0, 1, n + 1)[1:]]
            for x, y in seq:
                rr, cc = draw.disk((y - 0.5 - oy, x - 0.5 - ox), r, shape=full.shape)
                stroke[rr, cc] = True
            full = (full | stroke) if mode == 'add' else (full & ~stroke)
            self._store_mask(o, full, (ox, oy))
            self._mask_edited(o, f'brush_{mode} r={r:g}px n={len(points)}')
            self.save()
            return o

    def edit_polygon(self, object_id, polygon, mode, shape='polygon'):
        """Add/erase a drawn outline. `shape` (polygon, freehand, ellipse, rectangle) is recorded in
        the history only: every shape arrives as a polygon and is rasterised the same way."""
        shape = shape if shape in SHAPES else 'polygon'
        with self.lock, self._undoable(self.obj(object_id)['image_id'], f'draw {mode} ({shape})'):
            o = self.obj(object_id)
            im = self.image(o['image_id'])
            box = list(poly_bbox(polygon, im['width'], im['height'], pad=1))
            full, (ox, oy) = self._object_canvas(o, box)
            pm = polygon_mask(polygon, full.shape, (ox, oy))
            full = (full | pm) if mode == 'add' else (full & ~pm)
            self._store_mask(o, full, (ox, oy))
            self._mask_edited(o, f'polygon_{mode} ({shape}, {len(polygon)} vertices)')
            self.save()
            return o

    def add_manual_object(self, image_id, polygon, well_id=None, shape='polygon', accept=False):
        shape = shape if shape in SHAPES else 'polygon'
        with self.lock, self._undoable(image_id, f'new object ({shape})'):
            im = self.image(image_id)
            if well_id is None:
                cx = float(np.mean([p[0] for p in polygon]))
                cy = float(np.mean([p[1] for p in polygon]))
                for w in im['wells']:
                    if point_in_poly(cx, cy, w['polygon']):
                        well_id = w['well_id']
                        break
            o = self._new_object(image_id, well_id, 'manual',
                                 dict(profile=None, segmenter='manual', params_hash=None, app_version=APP_VERSION))
            box = list(poly_bbox(polygon, im['width'], im['height'], pad=1))
            full = polygon_mask(polygon, (box[3] - box[1], box[2] - box[0]), (box[0], box[1]))
            if not full.any():
                raise ValueError('polygon covers no pixel centre')
            self._store_mask(o, full, (box[0], box[1]))
            o['history'][-1]['action'] = f'created (manual, drawn {shape}, {len(polygon)} vertices)'
            if accept:      # the researcher drew it themselves: that is the decision
                o['status'] = 'accepted'
                o['approval'] = dict(status='accepted', by='', at=now(), mask_version=o.get('mask_version'),
                                     mask_sha256=o.get('mask_sha256'), proximity_parts=0)
            self.data['objects'][o['object_id']] = o
            self._event(image_id, 'manual_polygon_object', o['object_id'])
            self.save()
            return o

    def assign_part(self, object_id, well_id, part, assign=True):
        with self.lock, self._undoable(self.obj(object_id)['image_id'],
                                       f"{'assign' if assign else 'remove'} part {well_id}:{part}"):
            o = self.obj(object_id)
            self._snap(o)                      # before the parts list changes
            labels, off, meta = self.load_parts(o['image_id'], well_id)
            if labels is None:
                raise ValueError('no automatic parts stored for this well (run detection first)')
            pm = labels == int(part)
            if not pm.any():
                raise ValueError('unknown part')
            owner = self.part_owner_map(o['image_id']).get((well_id, int(part)))
            if assign and owner and owner != object_id:
                self.assign_part(owner, well_id, part, assign=False)   # move, never share a part
            ys, xs = np.nonzero(pm)
            box = [off[0] + xs.min(), off[1] + ys.min(), off[0] + xs.max() + 1, off[1] + ys.max() + 1]
            full, (ox, oy) = self._object_canvas(o, box)
            sub = pm[box[1] - off[1]:box[3] - off[1], box[0] - off[0]:box[2] - off[0]]
            region = full[box[1] - oy:box[3] - oy, box[0] - ox:box[2] - ox]
            if assign:
                region |= sub
                if not any(p['well_id'] == well_id and p['part'] == int(part) for p in o['parts']):
                    o['parts'].append(dict(well_id=well_id, part=int(part), how='user'))
            else:
                region &= ~sub
                o['parts'] = [p for p in o['parts'] if not (p['well_id'] == well_id and p['part'] == int(part))]
            self._store_mask(o, full, (ox, oy))
            self._mask_edited(o, f"{'assign' if assign else 'unassign'}_part {well_id}:{part}")
            self.save()
            return o

    def object_from_part(self, image_id, well_id, part):
        """Promote one free (unassigned or wall-excluded) detected part to its own new object."""
        with self.lock, self._undoable(image_id, f'new object from part {well_id}:{part}'):
            labels, off, meta = self.load_parts(image_id, well_id)
            if labels is None:
                raise ValueError('no automatic parts stored for this well (run detection first)')
            owner = self.part_owner_map(image_id).get((well_id, int(part)))
            if owner:
                raise ValueError(f'part already belongs to {owner}')
            pm = labels == int(part)
            if not pm.any():
                raise ValueError('unknown part')
            kind = next((m['kind'] for m in meta if m['part'] == int(part)), '')
            o = self._new_object(image_id, well_id, 'manual',
                                 dict(profile=None, segmenter=f'part ({kind})', params_hash=None, app_version=APP_VERSION))
            reason = next((m.get('reason') for m in meta if m['part'] == int(part)), None)
            o['parts'] = [dict(well_id=well_id, part=int(part), how='user')]
            o['history'][-1]['action'] = f'created from detected {kind} part {well_id}:{part}' + (f' ({reason})' if reason else '')
            self._store_mask(o, pm, off)
            self.data['objects'][o['object_id']] = o
            self._event(image_id, 'object_from_part', o['object_id'])
            self.save()
            return o

    def detach_proximity_parts(self, object_id):
        """Remove every part that was attached to the object only by the proximity rule."""
        with self.lock, self._undoable(self.obj(object_id)['image_id'], 'detach proximity parts'):
            o = self.obj(object_id)
            for p in [p for p in o['parts'] if p.get('how') == 'proximity']:
                self.assign_part(object_id, p['well_id'], p['part'], assign=False)
            return self.obj(object_id)

    def set_status(self, object_id, status, reviewer=None):
        """Accept/reject records WHO decided, WHEN, and WHICH mask version (+ digest) the
        decision covers. Any later mask edit resets the status (see _mask_edited)."""
        if status not in ('unreviewed', 'accepted', 'rejected'):
            raise ValueError('bad status')
        with self.lock, self._undoable(self.obj(object_id)['image_id'], {'accepted': 'accept', 'rejected': 'reject'}
                                       .get(status, 'reset status')):
            o = self.obj(object_id)
            self._snap(o)
            if status == 'accepted' and not o['measure_px']:
                raise ValueError('an empty object cannot be accepted')
            o['status'] = status
            reviewer = (reviewer or '').strip()
            o['approval'] = None if status == 'unreviewed' else dict(
                status=status, by=reviewer, at=now(), mask_version=o.get('mask_version'),
                mask_sha256=o.get('mask_sha256'),
                proximity_parts=sum(1 for p in o['parts'] if p.get('how') == 'proximity'))
            self._touch(o, f'status -> {status}' + (f' by {reviewer}' if reviewer else ''))
            self._event(o['image_id'], status, object_id)
            self.save()
            return o

    def set_status_many(self, image_id, status, only_status=None):
        """Approve all remaining / reset all decisions of one photo in ONE undo step."""
        n = 0
        with self.lock, self._undoable(image_id, {'accepted': 'approve all remaining',
                                                    'unreviewed': 'reset all decisions'}.get(status, 'set status')):
            for o in sorted((o for o in self.data['objects'].values() if o['image_id'] == image_id),
                            key=lambda o: o['object_id']):
                if only_status and o['status'] != only_status:
                    continue
                if o['status'] == status or (status == 'accepted' and not o['measure_px']):
                    continue
                self.set_status(o['object_id'], status)
                n += 1
        return n

    def set_object_meta(self, object_id, notes=None, flags=None, well_id=None):
        with self.lock:
            o = self.obj(object_id)
            if notes is not None:
                o['notes'] = notes
            if flags:
                for k, v in flags.items():
                    if k in MANUAL_FLAGS:
                        o['flags'][k] = bool(v)
            if well_id is not None:
                o['well_id'] = well_id or None
                if o['bbox']:
                    self._auto_flags(o, self.load_mask(o))
            self._touch(o, 'metadata edited')
            self._event(o['image_id'], 'object_meta', object_id)
            self.save()
            return o

    def delete_object(self, object_id):
        with self.lock, self._undoable(self.obj(object_id)['image_id'], f'delete {object_id}'):
            o = self.obj(object_id)
            if o['source'] == 'auto':
                raise ValueError('automatic candidates are rejected, not deleted (keeps the review trail)')
            self._snap(o)
            if os.path.exists(self.abspath(o['mask_file'])):
                os.remove(self.abspath(o['mask_file']))
            del self.data['objects'][object_id]
            self._event(o['image_id'], 'delete_object', object_id)
            self.save()

    def view_image(self, image_id):
        """Called when the reviewer opens an image (for review-time estimates)."""
        with self.lock:
            self._event(image_id, 'view')
            self.save()

    def add_active_time(self, image_id, seconds):
        """Measured in-app active review time, reported by the browser (app.js, 'measured active
        review time'): a second counts only while this image is open in a visible, focused tab AND
        the reviewer moved, clicked, typed or scrolled within the previous ACTIVE_WINDOW_S seconds."""
        s = float(seconds)
        if not (0 <= s <= MAX_ACTIVE_REPORT_S):
            raise ValueError(f'active time report must be 0..{MAX_ACTIVE_REPORT_S} s')
        with self.lock:
            im = self.image(image_id)
            im['active_review_s'] = round(float(im.get('active_review_s') or 0) + s, 1)
            self.save()
            return im['active_review_s']

    def review_effort(self, image_id, idle_cap_s=120):
        """Two review-time figures for one image:
        * active_review_s: MEASURED in the app (add_active_time); None if no browser reported any
          (e.g. review through the API only).
        * active_seconds_est: ROUGH ESTIMATE from the event log = sum of gaps between consecutive
          reviewer events, each gap capped at idle_cap_s. Thinking time before the first action,
          pauses and time spent elsewhere all distort it; use it only when nothing was measured.
        Automatic steps (upload, detection) are not reviewer events."""
        im = self.image(image_id)
        auto_actions = {'uploaded', 'detect_objects', 'detect_wells', 'calibration_estimate'}
        ev = [e for e in im.get('events', []) if e[1] not in auto_actions]
        t = [dt.datetime.fromisoformat(e[0]) for e in ev]
        active = sum(min((b - a).total_seconds(), idle_cap_s) for a, b in zip(t, t[1:]))
        counts = {}
        for e in ev:
            counts[e[1]] = counts.get(e[1], 0) + 1
        return dict(events=len(ev), active_review_s=im.get('active_review_s'), active_window_s=ACTIVE_WINDOW_S,
                    active_seconds_est=round(active, 1), idle_cap_s=idle_cap_s,
                    first=ev[0][0] if ev else None, last=ev[-1][0] if ev else None, counts=counts)

    # ------------------------------------------------------------------ views
    def object_view(self, o, cal=None):
        cal = cal or self.calibration_state(o['image_id'])
        mp = o.get('measure_px')
        meas = None
        if mp:
            meas = {}
            for reg, px in mp.items():
                um = M.to_physical(px, cal['um_per_px']) if px else None
                meas[reg] = dict(px=px, um=um)
        im = self.image(o['image_id'])
        flags = dict(o['flags'])
        for f in im['image_flags']:
            if f['flag'] == 'unreliable_segmentation':
                flags['unreliable_segmentation_image'] = True
        flags['calibration_unconfirmed'] = cal['status'] != 'confirmed'
        return dict({k: o[k] for k in ('object_id', 'image_id', 'well_id', 'source', 'edited', 'status', 'bbox',
                                         'notes', 'parts', 'centroid', 'envelope_radius_px', 'boundary_sigma_px',
                                         'settings', 'mask_file', 'auto_mask_file', 'auto_bbox', 'mask_version',
                                         'mask_sha256', 'approval', 'added_px') if k in o},
                    proximity_parts=sum(1 for p in o.get('parts', []) if p.get('how') == 'proximity'),
                    flags=flags, measurements=meas)

    def image_detail(self, image_id, with_contours=True):
        with self.lock:
            im = copy.deepcopy(self.image(image_id))
            objs = [o for o in self.data['objects'].values() if o['image_id'] == image_id]
            cal = self.calibration_state(image_id)
            calrec = self.data['calibrations'].get(im['calibration_id']) if im['calibration_id'] else None
        sigma = float(load_config()['measurement']['boundary_sigma_px'])
        env_r = int(load_config()['measurement']['envelope_radius_px'])
        cache = self.__dict__.setdefault('_contour_cache', {})
        out_objs = []
        for o in sorted(objs, key=lambda o: o['object_id']):
            v = self.object_view(o, cal)
            if with_contours and o['bbox']:
                key = (o['object_id'], o.get('mask_sha256'), sigma, o.get('envelope_radius_px', env_r))
                if key not in cache:
                    m = self.load_mask(o)
                    off = (o['bbox'][0], o['bbox'][1])
                    cache[key] = (contours_xy(m, off, sigma),
                                  contours_xy(M.envelope_of(m, o.get('envelope_radius_px', env_r)), off, sigma))
                v['contours'], v['envelope_contours'] = cache[key]
            out_objs.append(v)
        owner = self.part_owner_map(image_id)
        how_map = {}
        for o in objs:
            for p in o['parts']:
                how_map[(p['well_id'], p['part'])] = p.get('how')
        parts = []
        if with_contours:
            for w in im['wells']:
                mp = self.abspath(os.path.join('parts', image_id, f"{w['well_id']}.npz"))
                if not os.path.exists(mp):
                    continue
                meta = json.loads(str(np.load(mp)['meta']))
                cont = self._part_contours(image_id, w['well_id'])
                for m in meta:
                    key = (w['well_id'], m['part'])
                    parts.append(dict(well_id=w['well_id'], part=m['part'], kind=m['kind'], area_px=m['area'],
                                      reason=m.get('reason'), next_to=m.get('next_to'), between=m.get('between'),
                                      owner=owner.get(key),
                                      how=how_map.get(key), contours=cont.get(str(m['part']), [])))
        return dict(image=im, calibration=dict(cal, record=calrec), objects=out_objs, parts=parts,
                    profiles=sorted(load_config()['profiles']), undo=self.undo_info(image_id))

    @staticmethod
    def _wells_status(im):
        wd = im.get('well_detection') or {}
        if im['wells']:
            srcs = {w['source'] for w in im['wells']}
            if srcs <= {'auto', 'auto (grid cell)'}:
                return 'auto (grid cells)' if 'auto (grid cell)' in srcs else 'auto'
            if any(str(x).startswith('auto') for x in srcs):
                return 'auto (edited)'
            return 'preset' if srcs == {'example_preset'} else 'manual'
        return 'failed' if wd.get('status') == 'failed' else 'none'

    def summary(self):
        with self.lock:
            imgs = []
            counts = {}
            for o in self.data['objects'].values():
                c = counts.setdefault(o['image_id'], dict(total=0, unreviewed=0, accepted=0, rejected=0))
                c['total'] += 1
                c[o['status']] += 1
            for im in self.data['images']:
                c = counts.get(im['image_id'], dict(total=0, unreviewed=0, accepted=0, rejected=0))
                imgs.append(dict(image_id=im['image_id'], original_filename=im['original_filename'],
                                 width=im['width'], height=im['height'], profile=im['profile'],
                                 processing=im['processing'].get('status'), review_done=im['review_done'],
                                 hidden=bool(im.get('hidden')), labels=im.get('labels') or parse_labels(''),
                                 labels_complete=labels_complete(im.get('labels')),
                                 reviewed=im['processing'].get('status') == 'processed' and c['unreviewed'] == 0,
                                 n_wells=len(im['wells']),
                                 n_grid_cells=sum(region_kind(w) == 'grid cell' for w in im['wells']), calibration=self.calibration_state(im['image_id'])['status'],
                                 wells_status=self._wells_status(im),
                                 flags=[f['flag'] for f in im['image_flags']], used_for_tuning=im['used_for_tuning'],
                                 objects=c))
            return dict(name=self.data['name'], app_version=APP_VERSION, images=imgs,
                        calibrations=list(self.data['calibrations'].values()), job=dict(self.job),
                        progress=dict(images_total=len(imgs), images_reviewed=sum(1 for i in imgs if i['review_done']),
                                      objects_total=sum(c['total'] for c in counts.values()),
                                      objects_unreviewed=sum(c['unreviewed'] for c in counts.values())),
                        scale=self.scale_state(),
                        blockers=self.export_blockers())

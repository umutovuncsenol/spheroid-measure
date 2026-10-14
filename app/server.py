"""Local Flask server. Binds to 127.0.0.1 only. One process can hold several sessions."""
import os
import re
import tempfile
import threading

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from .export import export_session
from .export_groups import export_groups
from .scalebar import detect_scale_bar
from .store import load_rgb8
from .store import ROOT, Session, load_config

SESSIONS_DIR = os.environ.get('SPHEROID_SESSIONS', os.path.join(ROOT, 'sessions'))
STATIC = os.path.join(os.path.dirname(__file__), 'static')
_open = {}
_open_lock = threading.Lock()
NAME_RE = re.compile(r'^[A-Za-z0-9_.\- ]{1,80}$')


def get_session(name, create=False):
    if not NAME_RE.match(name or '') or name.startswith('.'):
        abort(400, 'invalid session name (letters, digits, space, _ . -)')
    with _open_lock:
        if name not in _open:
            path = os.path.join(SESSIONS_DIR, name)
            if not create and not os.path.exists(os.path.join(path, 'session.json')):
                abort(404, 'no such session')
            _open[name] = Session(path)
        return _open[name]


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 ** 3

    def ok(x=None):
        return jsonify(x if x is not None else {'ok': True})

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(RuntimeError)
    def bad(e):
        return jsonify(error=str(e)), 400

    @app.get('/')
    def index():
        return send_from_directory(STATIC, 'index.html')

    @app.get('/static/<path:p>')
    def static_files(p):
        return send_from_directory(STATIC, p)

    @app.get('/api/sessions')
    def sessions():
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        names = sorted(d for d in os.listdir(SESSIONS_DIR)
                       if os.path.exists(os.path.join(SESSIONS_DIR, d, 'session.json')))
        return ok(dict(sessions=names, folder=SESSIONS_DIR))

    @app.post('/api/sessions')
    def new_session():
        name = (request.json or {}).get('name', '').strip()
        s = get_session(name, create=True)
        return ok(s.summary())

    @app.get('/api/s/<name>/summary')
    def summary(name):
        return ok(get_session(name).summary())

    @app.post('/api/s/<name>/upload')
    def upload(name):
        s = get_session(name)
        added = []
        for f in request.files.getlist('files'):
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                f.save(tmp.name)
            try:
                rec = s.add_image(tmp.name, f.filename)
                if request.form.get('whole_region'):
                    s.ensure_region(rec['image_id'])
                added.append(rec)
            finally:
                os.unlink(tmp.name)
        return ok(dict(added=[a['image_id'] for a in added], summary=s.summary()))

    @app.get('/api/s/<name>/image/<iid>')
    def image_detail(name, iid):
        return ok(get_session(name).image_detail(iid))

    @app.get('/api/s/<name>/image/<iid>/file')
    def image_file(name, iid):
        s = get_session(name)
        im = s.image(iid)
        rel = im['display_path'] or im['stored_path']
        return send_file(s.abspath(rel), max_age=3600)

    @app.put('/api/s/<name>/image/<iid>/wells')
    def set_wells(name, iid):
        return ok(dict(wells=get_session(name).set_wells(iid, request.json['wells'])))

    @app.post('/api/s/<name>/image/<iid>/copy_wells')
    def copy_wells(name, iid):
        return ok(dict(wells=get_session(name).copy_wells(iid, request.json['from_image_id'])))

    @app.put('/api/s/<name>/image/<iid>/well/<wid>')
    def well_meta(name, iid, wid):
        j = request.json
        return ok(get_session(name).set_well_meta(iid, wid, j.get('no_measurable_object'), j.get('note')))

    @app.put('/api/s/<name>/image/<iid>/profile')
    def set_profile(name, iid):
        get_session(name).set_profile(iid, request.json['profile'])
        return ok()

    @app.put('/api/s/<name>/image/<iid>/meta')
    def image_meta(name, iid):
        j = request.json
        get_session(name).set_image_meta(iid, j.get('notes'), j.get('review_done'), j.get('flags'))
        return ok()

    @app.delete('/api/s/<name>/image/<iid>/flag/<flag>')
    def del_flag(name, iid, flag):
        get_session(name).remove_image_flag(iid, flag)
        return ok()

    @app.post('/api/s/<name>/image/<iid>/process')
    def process(name, iid):
        return ok(get_session(name).process_image(iid, whole_region=bool((request.get_json(silent=True) or {}).get('whole_region'))))

    @app.post('/api/s/<name>/process_all')
    def process_all(name):
        s = get_session(name)
        j = request.json or {}
        ids = j.get('image_ids') or [i['image_id'] for i in s.data['images']]
        return ok(s.start_batch(ids, detect_wells_first=bool(j.get('detect_wells_first', True)),
                                whole_region=bool(j.get('whole_region'))))

    @app.post('/api/s/<name>/image/<iid>/detect_wells')
    def detect_wells(name, iid):
        j = request.json or {}
        return ok(get_session(name).detect_wells(iid, replace=bool(j.get('replace')), params=j.get('params')))

    @app.post('/api/s/<name>/image/<iid>/whole_image')
    def whole_image(name, iid):
        return ok(dict(wells=get_session(name).whole_image_region(iid)))

    @app.put('/api/s/<name>/profile_many')
    def profile_many(name):
        j = request.json
        get_session(name).set_profile_many(j['image_ids'], j['profile'])
        return ok()

    @app.get('/api/s/<name>/job')
    def job(name):
        return ok(dict(get_session(name).job))

    @app.post('/api/s/<name>/image/<iid>/objects')
    def add_object(name, iid):
        j = request.json
        return ok(get_session(name).add_manual_object(iid, j['polygon'], j.get('well_id'), j.get('shape', 'polygon'),
                                                      accept=bool(j.get('accept'))))

    @app.post('/api/s/<name>/image/<iid>/object_from_part')
    def object_from_part(name, iid):
        j = request.json
        return ok(get_session(name).object_from_part(iid, j['well_id'], int(j['part'])))

    @app.post('/api/s/<name>/object/<oid>/status')
    def status(name, oid):
        j = request.json
        return ok(get_session(name).set_status(oid, j['status'], j.get('reviewer')))

    @app.post('/api/s/<name>/object/<oid>/detach_proximity')
    def detach_proximity(name, oid):
        return ok(get_session(name).detach_proximity_parts(oid))

    @app.post('/api/s/<name>/image/<iid>/view')
    def view_image(name, iid):
        get_session(name).view_image(iid)
        return ok()

    @app.post('/api/s/<name>/image/<iid>/references')
    def add_references(name, iid):
        s = get_session(name)
        out = [s.add_reference_file(iid, f.filename, f.read()) for f in request.files.getlist('files')]
        if not out:
            raise ValueError('no file received')
        return ok(dict(files=out))

    @app.delete('/api/s/<name>/image/<iid>/references')
    def clear_references(name, iid):
        get_session(name).clear_references(iid)
        return ok()

    @app.post('/api/s/<name>/image/<iid>/undo')
    def undo(name, iid):
        return ok(get_session(name).undo(iid))

    @app.post('/api/s/<name>/image/<iid>/active_time')
    def active_time(name, iid):
        # sent by the browser every 20 s of measured activity, and by sendBeacon when the tab is hidden
        j = request.get_json(force=True, silent=True) or {}
        return ok(dict(active_review_s=get_session(name).add_active_time(iid, j.get('seconds', 0))))

    @app.get('/api/s/<name>/image/<iid>/effort')
    def effort(name, iid):
        return ok(get_session(name).review_effort(iid))

    @app.post('/api/s/<name>/object/<oid>/brush')
    def brush(name, oid):
        j = request.json
        return ok(get_session(name).edit_brush(oid, j['points'], j['radius'], j['mode']))

    @app.post('/api/s/<name>/object/<oid>/polygon')
    def polygon(name, oid):
        j = request.json
        return ok(get_session(name).edit_polygon(oid, j['polygon'], j['mode'], j.get('shape', 'polygon')))

    @app.post('/api/s/<name>/object/<oid>/assign')
    def assign(name, oid):
        j = request.json
        return ok(get_session(name).assign_part(oid, j['well_id'], int(j['part']), bool(j.get('assign', True))))

    @app.put('/api/s/<name>/object/<oid>/meta')
    def obj_meta(name, oid):
        j = request.json
        return ok(get_session(name).set_object_meta(oid, j.get('notes'), j.get('flags'), j.get('well_id')))

    @app.delete('/api/s/<name>/object/<oid>')
    def delete_obj(name, oid):
        get_session(name).delete_object(oid)
        return ok()

    @app.post('/api/s/<name>/image/<iid>/calibration')
    def add_cal(name, iid):
        j = request.json
        return ok(get_session(name).add_calibration(iid, j['line'], j['known_length'], j['unit'],
                                                    confirmed=bool(j.get('confirmed')), note=j.get('note', '')))

    @app.post('/api/s/<name>/calibration/<cid>/confirm')
    def confirm_cal(name, cid):
        get_session(name).confirm_calibration(cid, request.json['image_id'])
        return ok()

    @app.post('/api/s/<name>/calibration/<cid>/apply')
    def apply_cal(name, cid):
        j = request.json
        get_session(name).apply_calibration(cid, j['image_ids'], bool(j.get('compatible_confirmed')))
        return ok()

    @app.delete('/api/s/<name>/image/<iid>/calibration')
    def clear_cal(name, iid):
        get_session(name).clear_calibration(iid)
        return ok()

    @app.post('/api/s/<name>/export_full')
    def export_full(name):
        return ok(export_session(get_session(name)))

    @app.post('/api/s/<name>/export')
    def export(name):
        s = get_session(name)
        try:
            return ok(export_groups(s))
        except ValueError as e:
            return jsonify(error=str(e), blockers=s.export_blockers()), 409

    @app.put('/api/s/<name>/labels')
    def labels(name):
        j = request.json
        get_session(name).set_labels(j['image_ids'], j['labels'])
        return ok()

    @app.put('/api/s/<name>/hidden')
    def hidden(name):
        j = request.json
        get_session(name).set_hidden(j['image_ids'], bool(j['hidden']))
        return ok()

    @app.post('/api/s/<name>/image/<iid>/status_all')
    def status_all(name, iid):
        j = request.json
        return ok(dict(changed=get_session(name).set_status_many(iid, j['status'], j.get('only_status'))))

    @app.get('/api/s/<name>/image/<iid>/thumb')
    def thumb(name, iid):
        s = get_session(name)
        im = s.image(iid)
        rel = os.path.join('display', f'thumb_{iid}.jpg')
        p = s.abspath(rel)
        if not os.path.exists(p):
            from PIL import Image
            a = load_rgb8(s.abspath(im['stored_path']))
            t = Image.fromarray(a)
            t.thumbnail((360, 270))
            t.save(p, quality=80)
        return send_file(p, max_age=86400)

    @app.post('/api/s/<name>/image/<iid>/detect_scale')
    def detect_scale_ep(name, iid):
        s = get_session(name)
        im = s.image(iid)
        r = detect_scale_bar(load_rgb8(s.abspath(im['stored_path'])))
        return ok(r)

    @app.post('/api/s/<name>/scale')
    def set_scale(name):
        j = request.json
        c = get_session(name).set_session_scale(j['line'], j['known_length'], j['unit'], j['source_image_id'])
        return ok(c)

    @app.get('/api/s/<name>/files/<path:rel>')
    def session_file(name, rel):
        s = get_session(name)
        p = os.path.abspath(s.abspath(rel))
        if not p.startswith(s.root + os.sep) or not os.path.isfile(p):
            abort(404)
        return send_file(p, as_attachment=rel.endswith('.xlsx'))

    @app.get('/api/config')
    def config():
        return ok(load_config())

    return app

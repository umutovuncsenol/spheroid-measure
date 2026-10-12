// Spheroid Review — browser UI. All coordinates sent to the server are ORIGINAL-image
// pixel coordinates (continuous: pixel (r,c) spans [c,c+1)x[r,r+1)). Zoom/pan only change
// the view transform below and never what is stored or measured.
'use strict';
const $ = s => document.querySelector(s);
const st = {
  session: null, summary: null, imageId: null, detail: null, img: null,
  view: { s: 1, tx: 0, ty: 0 }, tool: 'select', selected: null, hover: null,
  drag: null, poly: [], stroke: null, busy: false,
};
const cv = $('#cv'), ctx = cv.getContext('2d');

// ---------------------------------------------------------------- API helpers
async function api(method, url, body, isForm) {
  st.pending = (st.pending || 0) + 1; $('#busy').hidden = false;
  try { return await api0(method, url, body, isForm); }
  finally { st.pending--; if (!st.pending) $('#busy').hidden = true; }
}
async function api0(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body; else { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok || j.error) { msg(j.error || ('HTTP ' + r.status), true); throw new Error(j.error); }
  return j;
}
const S = p => `/api/s/${encodeURIComponent(st.session)}${p}`;
function msg(t, err) { const m = $('#msg'); m.textContent = t || ''; m.className = 'msg' + (err ? ' err' : ''); if (t && !err) setTimeout(() => { if (m.textContent === t) m.textContent = ''; }, 5000); }
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ---------------------------------------------------------------- measured active review time
// A second is counted for the open image only while the tab is visible and focused AND the
// reviewer moved, clicked, typed or scrolled within the last ACTIVE_WINDOW_S seconds. Seconds are
// sent every 20 s, and when the image changes or the tab is hidden/closed (sendBeacon).
const ACTIVE_WINDOW_S = 30;
const act = { last: 0, pending: 0, session: null, image: null, total: {} };
['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart'].forEach(ev =>
  window.addEventListener(ev, () => { act.last = Date.now(); }, { passive: true, capture: true }));
function flushActive(beacon) {
  if (!act.pending || !act.image) return;
  const url = `/api/s/${encodeURIComponent(act.session)}/image/${act.image}/active_time`;
  const body = JSON.stringify({ seconds: act.pending }); act.pending = 0;
  if (beacon && navigator.sendBeacon) navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
  else fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }).catch(() => {});
}
const fmtDur = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
function showActive() {
  const el = document.getElementById('actTime'); if (!el || !st.imageId) return;
  el.textContent = fmtDur(act.total[st.session + '/' + st.imageId] || 0);
}
setInterval(() => {
  if (!st.session || !st.imageId || document.visibilityState !== 'visible' || !document.hasFocus()) return;
  if (Date.now() - act.last > ACTIVE_WINDOW_S * 1000) return;
  if (act.image !== st.imageId || act.session !== st.session) { flushActive(); act.image = st.imageId; act.session = st.session; }
  act.pending += 1;
  const k = st.session + '/' + st.imageId; act.total[k] = (act.total[k] || 0) + 1; showActive();
  if (act.pending >= 20) flushActive();
}, 1000);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushActive(true); });
window.addEventListener('pagehide', () => flushActive(true));

// ---------------------------------------------------------------- sessions
async function loadSessions(select) {
  const j = await api('GET', '/api/sessions');
  const sel = $('#sessionSel');
  sel.innerHTML = '<option value="">— open session —</option>' + j.sessions.map(n => `<option>${esc(n)}</option>`).join('');
  if (select) sel.value = select;
}
async function openSession(name) {
  if (!name) return;
  flushActive();
  st.session = name; st.imageId = null; st.detail = null; st.selected = null;
  localStorage.setItem('lastSession', name);
  await refreshSummary();
  const first = st.summary.images.find(i => !i.review_done) || st.summary.images[0];
  if (first) await openImage(first.image_id); else render();
}
$('#sessionSel').onchange = e => openSession(e.target.value);
$('#btnNewSession').onclick = async () => {
  const n = $('#newSession').value.trim(); if (!n) return msg('Enter a session name', true);
  await api('POST', '/api/sessions', { name: n }); await loadSessions(n); $('#newSession').value = ''; openSession(n);
};

// ---------------------------------------------------------------- summary / image list
async function refreshSummary() {
  st.summary = await api('GET', S('/summary'));
  const p = st.summary.progress;
  $('#progress').innerHTML = `<b>Session:</b> ${esc(st.summary.name)}<br>
    Images reviewed: ${p.images_reviewed}/${p.images_total}
    <div class="bar"><i style="width:${p.images_total ? 100 * p.images_reviewed / p.images_total : 0}%"></i></div>
    Objects: ${p.objects_total} (${p.objects_unreviewed} unreviewed)`;
  $('#imageList').innerHTML = st.summary.images.map((im, i) => {
    const o = im.objects;
    const b = [];
    b.push(im.processing === 'processed' ? `<span class="badge">${o.total} obj</span>` :
      `<span class="badge warn">${esc(im.processing)}</span>`);
    if (o.accepted) b.push(`<span class="badge ok">${o.accepted} ✓</span>`);
    if (o.rejected) b.push(`<span class="badge bad">${o.rejected} ✗</span>`);
    if (o.unreviewed) b.push(`<span class="badge warn">${o.unreviewed} ?</span>`);
    if (im.wells_status === 'failed') b.push('<span class="badge bad">wells: detection failed</span>');
    else if (im.wells_status === 'none') b.push('<span class="badge warn">no wells</span>');
    b.push(im.calibration === 'confirmed' ? '<span class="badge ok">scale ✓</span>' : `<span class="badge warn">scale ${im.calibration}</span>`);
    if (im.flags.length) b.push(`<span class="badge bad">${im.flags.map(esc).join(', ')}</span>`);
    if (im.review_done) b.push('<span class="badge acc">reviewed</span>');
    return `<li data-id="${im.image_id}" class="${im.image_id === st.imageId ? 'cur' : ''}">
      <div class="fn">${i + 1}. ${esc(im.original_filename)}</div>
      <div class="small muted">${im.image_id} · ${im.width}×${im.height} · ${im.n_grid_cells ? (im.n_grid_cells === im.n_wells ? `${im.n_wells} grid cells (not wells)` : `${im.n_wells} regions, ${im.n_grid_cells} grid cells`) : `${im.n_wells} wells`}</div><div>${b.join('')}</div></li>`;
  }).join('');
  for (const li of document.querySelectorAll('#imageList li')) li.onclick = () => openImage(li.dataset.id);
  const job = st.summary.job;
  $('#jobStatus').textContent = job.running ? `Detecting ${job.done}/${job.total}…` :
    (job.finished ? `Last batch: ${job.done}/${job.total} done${job.errors.length ? ', ' + job.errors.length + ' errors' : ''}` : '');
}

async function openImage(id, keepView) {
  const changed = id !== st.imageId;
  st.imageId = id; if (changed) { st.selected = null; st.poly = []; st.hover = null; $('#tip').hidden = true; }
  if (changed) { flushActive(); api('POST', S(`/image/${id}/view`)).catch(() => {}); }
  st.detail = await api('GET', S(`/image/${id}`));
  const ak = st.session + '/' + id;
  if (act.total[ak] === undefined) act.total[ak] = st.detail.image.active_review_s || 0;
  if (changed || !st.img) {
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = S(`/image/${id}/file`); });
    st.img = img;
    if (!keepView) fit();
  }
  document.querySelectorAll('#imageList li').forEach(li => li.classList.toggle('cur', li.dataset.id === id));
  if (st.selected && !st.detail.objects.some(o => o.object_id === st.selected)) st.selected = null;
  updateUndo();
  $('#empty').hidden = true;
  renderPanels(); render();
}
async function reload() {
  st.pending = (st.pending || 0) + 1; $('#busy').hidden = false;
  try { await openImage(st.imageId, true); await refreshSummary(); }
  finally { st.pending--; if (!st.pending) $('#busy').hidden = true; }
}

// ---------------------------------------------------------------- view transform
function resize() { const r = cv.getBoundingClientRect(); cv.width = r.width * devicePixelRatio; cv.height = r.height * devicePixelRatio; render(); }
window.addEventListener('resize', resize);
// the canvas also changes size when the header or toolbar wraps (e.g. after an export link appears)
if (window.ResizeObserver) new ResizeObserver(() => resize()).observe(cv);
function fit() {
  if (!st.img) return; const r = cv.getBoundingClientRect();
  const s = Math.min(r.width / st.img.naturalWidth, r.height / st.img.naturalHeight) * 0.98;
  st.view = { s, tx: (r.width - st.img.naturalWidth * s) / 2, ty: (r.height - st.img.naturalHeight * s) / 2 }; render();
}
function zoomAt(f, sx, sy) {
  const v = st.view, r = cv.getBoundingClientRect();
  if (sx === undefined) { sx = r.width / 2; sy = r.height / 2; }
  const ix = (sx - v.tx) / v.s, iy = (sy - v.ty) / v.s;
  v.s = Math.min(40, Math.max(0.05, v.s * f)); v.tx = sx - ix * v.s; v.ty = sy - iy * v.s; render();
}
const toImg = (sx, sy) => [(sx - st.view.tx) / st.view.s, (sy - st.view.ty) / st.view.s];
const toScr = (x, y) => [x * st.view.s + st.view.tx, y * st.view.s + st.view.ty];
$('#zIn').onclick = () => zoomAt(1.25); $('#zOut').onclick = () => zoomAt(0.8); $('#zFit').onclick = fit;
$('#z1').onclick = () => zoomAt(1 / st.view.s);

// ---------------------------------------------------------------- rendering
const COL = { accepted: '#2bd65a', rejected: '#ff4d4d', unreviewed: '#ffcc00' };
const PART_COL = { unassigned: '#ffa657', assigned: '#ffa657', wall_shape: '#d2a8ff', wall_band: '#ff7eb6', thin: '#7dd3fc', small: '#a3e635',
  split_line: '#ffffff' };
const REASON_TXT = { wall_shape: 'removed: wall/shadow shape', wall_band: 'removed: inside wall band', thin: 'removed: thin (opening)',
  small: 'removed: smaller than speck limit', split_line: 'cut line of a watershed split (assign it back to re-merge)',
  unassigned: 'unassigned part' };
function pathOf(polys) {
  const p = new Path2D();
  for (const c of polys) { c.forEach(([x, y], i) => { const [a, b] = toScr(x, y); i ? p.lineTo(a, b) : p.moveTo(a, b); }); p.closePath(); }
  return p;
}
function render() {
  const dpr = devicePixelRatio; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const r = cv.getBoundingClientRect(); ctx.clearRect(0, 0, r.width, r.height);
  if (!st.img || !st.detail) return;
  const v = st.view;
  ctx.imageSmoothingEnabled = v.s < 2;
  ctx.drawImage(st.img, v.tx, v.ty, st.img.naturalWidth * v.s, st.img.naturalHeight * v.s);
  const d = st.detail;
  // wells
  ctx.lineWidth = 1.5; ctx.font = '12px sans-serif';
  for (const w of d.image.wells) {
    const weak = wellWeak(w);
    const wc = w.no_measurable_object ? '#ff8a8a' : (weak ? '#ff9f1a' : '#58a6ff');
    ctx.strokeStyle = wc; ctx.setLineDash([6, 4]);
    ctx.stroke(pathOf([w.polygon])); ctx.setLineDash([]);
    const [a, b] = toScr(w.polygon[0][0], w.polygon[0][1]);
    const tag = w.detection && w.detection.grid_cell ? '' : weak;   // grid cells: G-ID only (the panel explains)
    ctx.fillStyle = wc; ctx.fillText(w.well_id + (w.no_measurable_object ? ' (not measurable)' : '') + (tag ? ' (' + tag + ')' : ''), a + 3, b + 13);
    if (st.tool === 'well') for (const [x, y] of w.polygon) { const [p, q] = toScr(x, y); ctx.fillRect(p - 3, q - 3, 6, 6); }
  }
  // parts
  const showFree = $('#tParts').checked || st.tool === 'assign', showRemoved = $('#tRemoved').checked;
  for (const p of d.parts) {
    if (p.owner) {
      // parts attached only by the proximity rule are unconfirmed: always outlined for the selected object
      if (p.how === 'proximity' && (p.owner === st.selected || st.tool === 'assign')) {
        ctx.strokeStyle = '#ffe066'; ctx.setLineDash([3, 2]); ctx.lineWidth = 2; ctx.stroke(pathOf(p.contours)); ctx.setLineDash([]);
      } else if (st.tool === 'assign') {
        ctx.strokeStyle = p.owner === st.selected ? '#7ee787' : '#888'; ctx.setLineDash([2, 2]); ctx.lineWidth = 1;
        ctx.stroke(pathOf(p.contours)); ctx.setLineDash([]);
      }
      continue;
    }
    const removed = p.kind === 'excluded' && p.reason && p.reason !== 'wall_shape' && p.reason !== 'split_line';
    if (removed ? !showRemoved : !showFree) continue;
    if (p.reason === 'split_line') {     // watershed cut: solid white with a dark halo, visible on any background
      const path = pathOf(p.contours);
      ctx.strokeStyle = '#000'; ctx.lineWidth = 3.5; ctx.stroke(path);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke(path); continue;
    }
    ctx.strokeStyle = PART_COL[p.reason || p.kind] || '#ffa657';
    ctx.setLineDash(removed ? [2, 3] : [4, 3]); ctx.lineWidth = 1;
    ctx.stroke(pathOf(p.contours)); ctx.setLineDash([]);
  }
  // ImageJ reference outlines: cyan, dashed, labelled; not objects, not clickable
  if (st.showRefs !== false && d.image.references) {
    ctx.strokeStyle = '#22d3ee'; ctx.fillStyle = '#22d3ee'; ctx.lineWidth = 1.5; ctx.setLineDash([7, 3]);
    for (const r of d.image.references) {
      ctx.stroke(pathOf(r.polygons));
      const [a, b] = toScr(r.bounds[0], r.bounds[3]); ctx.fillText(r.ref_id + ' (ImageJ)', a, b + 13);   // below: object IDs sit above
    }
    ctx.setLineDash([]);
  }
  // objects
  const fill = $('#tFill').checked;
  for (const o of d.objects) {
    if (!o.contours) continue;
    const path = pathOf(o.contours);
    const c = COL[o.status];
    if (fill) { ctx.fillStyle = c + '33'; ctx.fill(path, 'evenodd'); }
    ctx.strokeStyle = c; ctx.lineWidth = (o.object_id === st.selected) ? 3 : (o.object_id === st.hover ? 2.5 : 1.5);
    ctx.stroke(path);
    if ($('#tEnv').checked && o.envelope_contours) { ctx.setLineDash([5, 3]); ctx.lineWidth = 1; ctx.stroke(pathOf(o.envelope_contours)); ctx.setLineDash([]); }
    const [a, b] = toScr(o.bbox[0], o.bbox[1]);
    ctx.fillStyle = '#000a'; const label = o.object_id.replace('OBJ-0000', '').replace('OBJ-', '');
    ctx.fillRect(a, b - 15, ctx.measureText(label).width + 6, 14); ctx.fillStyle = c; ctx.fillText(label, a + 3, b - 4);
  }
  // calibration line (if drawn on this image)
  const cal = d.calibration.record;
  if (cal && cal.source_image_id === d.image.image_id) {
    const [[x1, y1], [x2, y2]] = cal.line, [a, b] = toScr(x1, y1), [p, q] = toScr(x2, y2);
    ctx.strokeStyle = cal.status === 'confirmed' ? '#2bd65a' : '#ff9f1a'; ctx.lineWidth = 2; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(p, q); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = ctx.strokeStyle; ctx.fillText(`${cal.known_length} ${cal.unit} (${cal.status})`, a, b - 6);
  }
  // in-progress drawing
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
  if (st.drag && st.drag.kind === 'rect') { const [a, b] = toScr(...st.drag.p0), [p, q] = toScr(...st.drag.p1); ctx.strokeRect(a, b, p - a, q - b); }
  if (st.drag && st.drag.kind === 'line') { const [a, b] = toScr(...st.drag.p0), [p, q] = toScr(...st.drag.p1); ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(p, q); ctx.stroke(); }
  if (st.drag && st.drag.kind === 'shape') {
    const sp = st.drag.shape === 'freehand' ? st.drag.pts : shapePolygon(st.drag);
    if (sp && sp.length > 1) {
      ctx.strokeStyle = st.tool === 'polyErase' ? '#ff6b6b' : (st.tool === 'polyAdd' ? '#2bd65a' : '#fff');
      ctx.beginPath(); sp.forEach(([x, y], i) => { const [a, b] = toScr(x, y); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
      if (st.drag.shape !== 'freehand') ctx.closePath(); ctx.stroke(); ctx.strokeStyle = '#fff';
    }
  }
  if (st.poly.length) { ctx.beginPath(); st.poly.forEach(([x, y], i) => { const [a, b] = toScr(x, y); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); }); if (st.mouse) ctx.lineTo(...toScr(...st.mouse)); ctx.stroke(); }
  if (st.stroke) {
    ctx.fillStyle = st.tool === 'brushAdd' ? '#2bd65a66' : '#ff4d4d66';
    for (const [x, y] of st.stroke) { const [a, b] = toScr(x, y); ctx.beginPath(); ctx.arc(a, b, brushR() * v.s, 0, 7); ctx.fill(); }
  }
  if ((st.tool === 'brushAdd' || st.tool === 'brushErase') && st.mouse) {
    const [a, b] = toScr(...st.mouse); ctx.strokeStyle = '#fff'; ctx.beginPath(); ctx.arc(a, b, brushR() * v.s, 0, 7); ctx.stroke();
  }
}
function wellWeak(w) {
  const d = w.detection; if (!d || String(w.source).includes('edited')) return '';
  if (d.grid_cell) return 'grid cell — no well outline';
  if (d.partial) return 'cut by image edge';
  if (d.rel_score != null && d.rel_score < 0.35) return 'low confidence';
  return '';
}
async function editCall(path, body) {
  const before = (st.detail.objects.find(o => o.object_id === st.selected) || {}).status;
  const o = await api('POST', S(path), body);
  if (before && before !== 'unreviewed' && o.status === 'unreviewed')
    msg(`${o.object_id}: was ${before}; the mask changed, so it is unreviewed again — review it again.`, true);
  return o;
}
const brushR = () => Math.max(1, +$('#brushR').value || 6);

// ---------------------------------------------------------------- hit testing (image coords)
function pip(x, y, poly) {
  let ins = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) ins = !ins;
  }
  return ins;
}
const inPolys = (x, y, polys) => polys.reduce((a, c) => a ^ pip(x, y, c), false);  // even-odd (holes)
function objectAt(x, y) {
  let best = null;
  for (const o of st.detail.objects) {
    if (!o.contours || !o.bbox) continue;
    const [x0, y0, x1, y1] = o.bbox; if (x < x0 - 2 || y < y0 - 2 || x > x1 + 2 || y > y1 + 2) continue;
    if (inPolys(x, y, o.contours)) { const a = (x1 - x0) * (y1 - y0); if (!best || a < best.a) best = { o, a }; }
  }
  return best && best.o;
}
function partAt(x, y) {
  for (const p of st.detail.parts) if (inPolys(x, y, p.contours)) return p;
  // small parts: accept clicks within 3 screen px of the outline
  let best = null;
  for (const p of st.detail.parts) for (const c of p.contours) for (const [px, py] of c) {
    const d = Math.hypot(px - x, py - y) * st.view.s; if (d < 4 && (!best || d < best.d)) best = { p, d };
  }
  return best && best.p;
}

// ---------------------------------------------------------------- formatting
function fmt(v, d = 1) { return v === null || v === undefined || isNaN(v) ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }); }
function measRows(o, regions) {
  const m = o.measurements; if (!m) return '<tr><td colspan=6>empty mask — no measurements</td></tr>';
  const um = m.cells.um !== null;
  const L = um ? 'µm' : 'px', A = um ? 'µm²' : 'px²';
  let h = `<tr class="h"><td>region</td><td>area ${A}</td><td>perim ${L}</td><td>ECD ${L}</td><td>Feret ${L}</td><td>circ.</td><td>parts</td></tr>`;
  for (const [k, name] of regions) {
    const r = m[k]; if (!r || !r.px) continue; const u = r.um || r.px;
    h += `<tr><td>${name}</td><td class="n">${fmt(u.area, 0)}</td><td class="n">${fmt(u.perimeter)}</td><td class="n">${fmt(u.ecd)}</td><td class="n">${fmt(u.feret_max)}</td><td class="n">${fmt(r.px.circularity, 3)}</td><td class="n">${r.px.n_parts}</td></tr>`;
  }
  return h;
}
const REGIONS = [['cells', 'cells'], ['envelope', 'envelope'], ['core', 'core']];
const FLAGNAMES = { touches_well_boundary: 'touches well/region boundary', touches_image_edge: 'touches image edge',
  ambiguous_boundary: 'ambiguous boundary', unreliable_segmentation: 'unreliable segmentation',
  partial_object: 'partial object (provisional)', unreliable_segmentation_image: 'image: unreliable segmentation',
  calibration_unconfirmed: 'calibration not confirmed (pixel units only)' };
const flagList = o => Object.entries(o.flags).filter(([k, v]) => v).map(([k]) => FLAGNAMES[k] || k);

function showTip(o, sx, sy) {
  const t = $('#tip');
  if (!o) { t.hidden = true; return; }
  const cal = st.detail.calibration;
  t.innerHTML = `<div><b>${o.object_id}</b> · ${o.well_id || 'no well'} · ${o.status} · ${o.source}${o.edited ? ' (edited)' : ''}</div>
    <table>${measRows(o, REGIONS)}</table>
    <div class="small">${cal.status === 'confirmed' ? `scale ${fmt(cal.um_per_px, 4)} µm/px (confirmed)` : 'pixel units — calibration ' + cal.status}</div>
    ${(() => { const f = flagList({ flags: { ...o.flags, calibration_unconfirmed: false } }); return f.length ? `<div class="small" style="color:#ffcc66">${f.join(' · ')}</div>` : ''; })()}`;
  t.hidden = false;
  const r = $('#stage').getBoundingClientRect();
  t.style.left = Math.min(sx + 14, r.width - t.offsetWidth - 4) + 'px';
  t.style.top = Math.min(sy + 14, r.height - t.offsetHeight - 4) + 'px';
}

// ---------------------------------------------------------------- panels
function renderPanels() {
  const d = st.detail, im = d.image;
  const tune = im.used_for_tuning ? '<span class="badge warn" title="Profiles were tuned on this image: do not use it to measure segmentation accuracy">used for tuning</span>' : '';
  $('#imgInfo').innerHTML = `<div class="fn" style="font-weight:600;word-break:break-all">${esc(im.original_filename)}</div>
    <div class="small muted">${im.image_id} · ${im.width}×${im.height} px · SHA-256 ${im.sha256.slice(0, 12)}… ${tune}</div>
    <div class="row">Profile <select id="profSel">${d.profiles.map(p => `<option ${p === im.profile ? 'selected' : ''}>${p}</option>`).join('')}</select>
      <button id="btnDetect">Detect objects</button><button id="btnProfAll" class="small" title="Use this profile for every image in the session">for all images</button></div>
    <div class="small muted" title="${esc((st.config && st.config.profiles[im.profile] || {}).description || '')}">${esc(((st.config && st.config.profiles[im.profile] || {}).description || '').slice(0, 140))}…</div>
    <div class="small muted">Detection: ${esc(im.processing.status)}${im.processing.time ? ' · ' + im.processing.time : ''}${im.processing.wells_changed_since_run ? ' · <span class="warnline">wells changed since run</span>' : ''}${im.processing.message ? ' · ' + esc(im.processing.message) : ''}</div>
    <div class="row">${im.image_flags.map(f => `<span class="badge bad" title="${esc(f.reason)} (set by ${esc(f.set_by)})">${esc(f.flag)} <a href="#" data-unflag="${esc(f.flag)}">×</a></span>`).join('')}
      <button id="btnFlagImg" class="small">Flag image unreliable…</button></div>
    <div class="row"><label><input id="revDone" type="checkbox" ${im.review_done ? 'checked' : ''}> Image review complete</label>
      <button id="prevImg">◀ Prev</button><button id="nextImg">Next ▶</button>
      <span class="small muted" title="Measured: counts only while this image is open in a visible, focused window and you moved, clicked, typed or scrolled within the last ${ACTIVE_WINDOW_S} s. Exported in the Review effort sheet.">active review time <b id="actTime">0:00</b></span></div>
    <textarea id="imgNotes" rows="2" placeholder="image notes" style="width:100%">${esc(im.notes)}</textarea>`;
  $('#profSel').onchange = async e => { await api('PUT', S(`/image/${im.image_id}/profile`), { profile: e.target.value }); msg('Profile set. Run detection to apply it.'); reload(); };
  $('#btnDetect').onclick = detectThis;
  $('#btnProfAll').onclick = async () => {
    if (!confirm(`Use profile "${im.profile}" for all ${st.summary.images.length} images? (Takes effect at the next detection.)`)) return;
    await api('PUT', S('/profile_many'), { image_ids: st.summary.images.map(i => i.image_id), profile: im.profile }); msg('Profile set for all images.'); reload();
  };
  showActive();
  $('#revDone').onchange = async e => { await api('PUT', S(`/image/${im.image_id}/meta`), { review_done: e.target.checked }); refreshSummary(); };
  $('#imgNotes').onchange = e => api('PUT', S(`/image/${im.image_id}/meta`), { notes: e.target.value });
  $('#prevImg').onclick = () => step(-1); $('#nextImg').onclick = () => step(1);
  $('#btnFlagImg').onclick = async () => {
    const reason = prompt('Reason for flagging this image as unreliable segmentation:'); if (reason === null) return;
    const keep = im.image_flags.filter(f => f.set_by === 'user').map(f => ({ flag: f.flag, reason: f.reason }));
    await api('PUT', S(`/image/${im.image_id}/meta`), { flags: [...keep, { flag: 'unreliable_segmentation', reason }] }); reload();
  };
  document.querySelectorAll('[data-unflag]').forEach(a => a.onclick = async e => { e.preventDefault(); await api('DELETE', S(`/image/${im.image_id}/flag/${a.dataset.unflag}`)); reload(); });
  renderCal(); renderWells(); renderRefs(); renderObjects();
}

function renderCal() {
  const d = st.detail, c = d.calibration, rec = c.record, im = d.image;
  let h = '<h4>Scale</h4>';
  if (c.status === 'confirmed') {
    h += `<div><span class="badge ok">confirmed</span> ${fmt(c.um_per_px, 5)} µm/px · ${rec.cal_id}: line ${fmt(rec.line_length_px, 1)} px = ${rec.known_length} ${esc(rec.unit)}${rec.source_image_id !== im.image_id ? ` (drawn on ${rec.source_image_id})` : ''}</div>
      <div class="row"><button id="calApply">Apply to other images…</button><button id="calClear">Remove from this image</button></div>`;
  } else if (rec) {
    h += `<div><span class="badge warn">unconfirmed</span> estimate ${fmt(rec.um_per_px, 5)} µm/px (${rec.cal_id}, line ${fmt(rec.line_length_px, 1)} px = ${rec.known_length} ${esc(rec.unit)}).
      Physical values are hidden until confirmed.</div><div class="small muted">${esc(rec.note)}</div>
      <div class="row">${rec.source_image_id === im.image_id ? '<button id="calConfirm">Review & confirm this line…</button>' : ''}<button id="calClear">Remove</button></div>`;
  } else {
    h += `<div><span class="badge warn">none</span> Measurements are in pixels. Use the <b>Scale</b> tool: drag a line along a known reference (e.g. the scale bar), then enter its length.</div>`;
  }
  $('#calBox').innerHTML = h;
  const b1 = $('#calConfirm'); if (b1) b1.onclick = confirmEstimate;
  const b2 = $('#calClear'); if (b2) b2.onclick = async () => { await api('DELETE', S(`/image/${im.image_id}/calibration`)); reload(); };
  const b3 = $('#calApply'); if (b3) b3.onclick = applyCalDialog;
}

function renderWells() {
  const im = st.detail.image, wd = im.well_detection;
  const others = st.summary.images.filter(i => i.image_id !== im.image_id && i.n_wells);
  let det = '';
  if (wd && wd.status === 'failed') det = `<div class="warnline"><b>Automatic well detection failed:</b> ${esc(wd.reason)}. Draw wells with the Well tool, copy them, or use the whole image as one region.</div>`;
  else if (wd && wd.status === 'ok') {
    const weak = im.wells.filter(wellWeak).map(w => w.well_id + ': ' + wellWeak(w));
    const grid = String(wd.info.method_detail || '').startsWith('grid');
    det = grid ? `<div class="small"><span class="warnline">No well outline found.</span> Proposed ${wd.n_wells} <b>grid cells</b> (G1…G${wd.n_wells}): working regions from the regular pattern of dark objects, <b>not wells</b>. A cell can hold several objects or none, and objects can be cut by its edge — check them.${wd.applied ? '' : ' <b>Not applied</b>: ' + esc(wd.reason)}</div>` :
      `<div class="small">Auto-detected ${wd.n_wells} ${esc(wd.info.shape || '')} wells (${esc(wd.method)}${wd.params && wd.params.inner_floor ? ', inner floor' : ''}, grid ${Math.round(wd.info.period_x || 0)}×${Math.round(wd.info.period_y || 0)} px)${wd.applied ? '' : ' — <b>not applied</b>: ' + esc(wd.reason)}.
      ${weak.length ? `<span class="warnline">Check: ${weak.map(esc).join(', ')}</span>` : ''} Proposals — check every outline.</div>`;
  } else if (im.preset_applied) det = '<div class="small muted">Wells from the example preset (hand-placed in the prototype).</div>';
  const nGrid = im.wells.filter(w => w.detection && w.detection.grid_cell).length;
  $('#wellBox').innerHTML = `<h4>Wells / regions (${im.wells.length}${nGrid ? `, of which ${nGrid} grid cells — not wells` : ''})${im.wells.some(w => w.no_measurable_object) ? ' · some marked not measurable' : ''}</h4>
    ${det}
    <div class="row"><button id="btnDetWells" title="Classical grid/outline detection; proposals are editable">Detect wells</button>
      <label class="small" title="For wells surrounded by a thick dark frame (e.g. YAMUK / y-type images): move each side inward to the bright floor. Not for pyramid wells."><input type="checkbox" id="wellInner" ${st.wellInner ? 'checked' : ''}> dark frame: fit inner floor</label>
      <button id="btnWholeImg" class="small" title="Use the whole image as one region (when wells are not visible)">Whole image as one region</button></div>
    <details ${im.wells.length ? '' : 'open'}><summary>Well list and editing</summary>
    <div class="small muted">Edit: <b>Well</b> tool — drag a rectangle to add, drag a corner to resize, Alt+drag for a single corner.</div>
    ${im.wells.map(w => `<div class="row" title="source: ${esc(w.source)}${w.detection ? ' · score ' + w.detection.rel_score : ''}"><input data-wid="${esc(w.well_id)}" value="${esc(w.well_id)}" size="5" title="rename">
      <span class="small muted">${esc(w.source)}</span>${wellWeak(w) ? `<span class="badge warn">${wellWeak(w)}</span>` : ''}
      <label class="small"><input type="checkbox" data-nmo="${esc(w.well_id)}" ${w.no_measurable_object ? 'checked' : ''}> no measurable object</label>
      <button class="small" data-delw="${esc(w.well_id)}">delete</button></div>`).join('')}
    ${others.length ? `<div class="row">Copy wells from <select id="copyFrom">${others.map(i => `<option value="${i.image_id}">${i.image_id} ${esc(i.original_filename.slice(0, 24))}</option>`).join('')}</select><button id="btnCopyWells">Copy</button></div>` : ''}</details>`;
  document.querySelectorAll('[data-wid]').forEach(inp => inp.onchange = () => {
    const ws = im.wells.map(w => w.well_id === inp.dataset.wid ? { ...w, well_id: inp.value.trim() } : w); saveWells(ws);
  });
  document.querySelectorAll('[data-nmo]').forEach(cb => cb.onchange = async () => { await api('PUT', S(`/image/${im.image_id}/well/${encodeURIComponent(cb.dataset.nmo)}`), { no_measurable_object: cb.checked }); reload(); });
  document.querySelectorAll('[data-delw]').forEach(b => b.onclick = () => { if (confirm('Delete well ' + b.dataset.delw + '? (objects are kept)')) saveWells(im.wells.filter(w => w.well_id !== b.dataset.delw)); });
  const cp = $('#btnCopyWells'); if (cp) cp.onclick = async () => { await api('POST', S(`/image/${im.image_id}/copy_wells`), { from_image_id: $('#copyFrom').value }); msg('Wells copied — check and adjust them for this image.'); reload(); };
  $('#btnDetWells').onclick = async () => {
    const replace = im.wells.length ? confirm(`Replace the ${im.wells.length} existing wells with automatic proposals? Objects are kept.`) : true;
    if (im.wells.length && !replace) return;
    st.wellInner = $('#wellInner').checked;
    const r = await api('POST', S(`/image/${im.image_id}/detect_wells`), { replace, params: { inner_floor: st.wellInner } });
    const g = r.info && String(r.info.method_detail || '').startsWith('grid');
    msg(r.status === 'ok' ? (g ? `No well outline found: ${r.n_wells} grid cells (working regions, not wells) proposed — check them` : `${r.n_wells} wells proposed — check them`) : 'Well detection failed: ' + r.reason, r.status !== 'ok'); reload();
  };
  $('#btnWholeImg').onclick = async () => {
    if (im.wells.length && !confirm('Replace existing wells with one region covering the whole image?')) return;
    await api('POST', S(`/image/${im.image_id}/whole_image`)); reload();
  };
}
// ---------------------------------------------------------------- ImageJ reference outlines (comparison only)
function renderRefs() {
  const im = st.detail.image, refs = im.references || [], files = im.reference_files || [];
  const skipped = files.flatMap(f => (f.skipped || []).map(k => `${esc(k.name)}: ${esc(k.reason)}`));
  const res = im.imagej_results;
  $('#refBox').innerHTML = `<details ${refs.length ? 'open' : ''}><summary><b>ImageJ reference outlines</b> (${refs.length})</summary>
    <div class="small muted">For comparison only: never objects, never in the Measurements sheet. Draw them in ImageJ independently (before looking at the candidates), save the ROI Manager as RoiSet.zip, and optionally the Results table as .csv. The export then compares them with the automatic and the final masks.</div>
    <div class="row"><label class="btn small">Upload RoiSet.zip / .roi / Results.csv<input id="refUpload" type="file" multiple accept=".zip,.roi,.csv" hidden></label>
      ${refs.length || res ? '<label class="small"><input id="tRefs" type="checkbox" ' + (st.showRefs !== false ? 'checked' : '') + '> show</label><button id="refClear" class="small">remove all</button>' : ''}</div>
    ${files.length ? `<div class="small">${files.map(f => `${esc(f.original_filename)}: ${f.kind === 'results' ? f.n_rows + ' Results rows' : f.n_rois + ' outline(s)'}`).join(' · ')}</div>` : ''}
    ${skipped.length ? `<div class="small warnline">Skipped: ${skipped.join('; ')}</div>` : ''}</details>`;
  $('#refUpload').onchange = async e => {
    const fd = new FormData(); [...e.target.files].forEach(f => fd.append('files', f, f.name));
    const r = await api('POST', S(`/image/${im.image_id}/references`), fd, true);
    const n = r.files.reduce((a, f) => a + (f.n_rois || 0), 0), k = r.files.reduce((a, f) => a + ((f.skipped || []).length), 0);
    msg(`Reference files stored: ${n} outline(s)${k ? `, ${k} skipped (see panel)` : ''}.`); reload();
  };
  const t = $('#tRefs'); if (t) t.onchange = () => { st.showRefs = t.checked; render(); };
  const c = $('#refClear'); if (c) c.onclick = async () => { if (confirm('Remove all reference files of this image?')) { await api('DELETE', S(`/image/${im.image_id}/references`)); reload(); } };
}

async function saveWells(wells) { await api('PUT', S(`/image/${st.detail.image.image_id}/wells`), { wells }); reload(); }

function renderObjects() {
  const objs = st.detail.objects;
  const n = s => objs.filter(o => o.status === s).length;
  $('#objBox').innerHTML = `<h4>Objects (${objs.length}: ${n('accepted')} accepted, ${n('rejected')} rejected, ${n('unreviewed')} unreviewed)</h4>
    ${objs.length ? '' : '<div class="muted">No objects. Draw wells and run detection, or add one with “Draw: new”.</div>'}
    ${objs.map(o => `<div class="card ${o.object_id === st.selected ? 'sel' : ''}" data-oid="${o.object_id}">
      <div class="t"><b>${o.object_id}</b><span class="small">${esc(o.well_id || 'no well')} · ${o.source}${o.edited ? ' · edited' : ''}</span>
        <span class="badge ${o.status === 'accepted' ? 'ok' : o.status === 'rejected' ? 'bad' : 'warn'}">${o.status}</span></div>
      <table>${measRows(o, REGIONS)}</table>
      ${flagList(o).length ? `<div class="small warnline">${flagList(o).join(' · ')}</div>` : ''}
      ${o.proximity_parts ? `<div class="small" style="color:#9a6700">${o.proximity_parts} part(s) attached only by proximity (yellow dashed) — unconfirmed <button class="small" data-detach="1">Detach them</button></div>` : ''}
      <div class="small muted">mask v${o.mask_version || 1}${o.added_px ? ` · ${o.added_px} px added by gap closing/hole filling` : ''}${o.approval ? ` · ${o.approval.status} by ${esc(o.approval.by || '(no name)')} on v${o.approval.mask_version}` : ''}</div>
      <div class="row">
        <button class="ok" data-act="accepted">Accept (A)</button><button class="bad" data-act="rejected">Reject (R)</button>
        <button data-act="unreviewed" class="small">Reset</button>
        ${o.source === 'manual' ? '<button class="small" data-del="1">Delete</button>' : ''}</div>
      <div class="row small">${['ambiguous_boundary', 'partial_object', 'unreliable_segmentation'].map(f =>
        `<label><input type="checkbox" data-flag="${f}" ${o.flags[f] ? 'checked' : ''}> ${FLAGNAMES[f]}</label>`).join('')}</div>
      <textarea rows="1" data-notes placeholder="notes">${esc(o.notes)}</textarea></div>`).join('')}`;
  for (const card of document.querySelectorAll('.card')) {
    const oid = card.dataset.oid;
    card.onclick = e => { if (e.target.closest('button,input,textarea,label')) return; select(oid); };
    card.querySelectorAll('[data-act]').forEach(b => b.onclick = () => setStatus(oid, b.dataset.act));
    card.querySelectorAll('[data-flag]').forEach(cb => cb.onchange = async () => { await api('PUT', S(`/object/${oid}/meta`), { flags: { [cb.dataset.flag]: cb.checked } }); reload(); });
    const ta = card.querySelector('[data-notes]'); ta.onchange = () => api('PUT', S(`/object/${oid}/meta`), { notes: ta.value });
    const det = card.querySelector('[data-detach]'); if (det) det.onclick = async () => { await api('POST', S(`/object/${oid}/detach_proximity`)); msg('Proximity parts detached'); reload(); };
    const del = card.querySelector('[data-del]'); if (del) del.onclick = async () => { if (confirm('Delete manual object ' + oid + '?')) { await api('DELETE', S(`/object/${oid}`)); if (st.selected === oid) st.selected = null; reload(); } };
  }
}
function select(oid) {
  st.selected = oid; renderObjects(); render();
  const c = document.querySelector(`.card[data-oid="${oid}"]`); if (c) c.scrollIntoView({ block: 'nearest' });
}
async function setStatus(oid, status) {
  const reviewer = ($('#reviewer').value || '').trim();
  if (status !== 'unreviewed' && !reviewer && !st.warnedReviewer) { st.warnedReviewer = true; msg('Tip: enter your name in "Reviewer" so approvals record who decided.'); }
  await api('POST', S(`/object/${oid}/status`), { status, reviewer }); await reload();
}
function step(k) {
  const ims = st.summary.images; const i = ims.findIndex(x => x.image_id === st.imageId);
  const j = i + k; if (j >= 0 && j < ims.length) openImage(ims[j].image_id);
}

// ---------------------------------------------------------------- detection
async function detectThis() {
  const im = st.detail.image;
  const kept = st.detail.objects.filter(o => o.status !== 'unreviewed' || o.source !== 'auto' || o.edited).length;
  const drop = st.detail.objects.length - kept;
  if (drop && !confirm(`Re-run detection? ${drop} unreviewed, unedited automatic objects will be replaced. ${kept} reviewed/edited/manual objects are kept.`)) return;
  msg('Detecting…'); const p = await api('POST', S(`/image/${im.image_id}/process`));
  msg(p.status === 'processed' ? `Detection done: ${p.new_objects} new candidates` : p.message || p.status, p.status !== 'processed');
  reload();
}
$('#btnDetectAll').onclick = async () => {
  if (!st.session) return;
  if (!confirm('Run detection on all images? Images without wells first get automatic well detection; where that fails the image is reported as "needs_wells". Unreviewed, unedited automatic candidates are replaced; reviewed/edited/manual objects are kept.')) return;
  await api('POST', S('/process_all'), {}); pollJob();
};
async function pollJob() {
  const j = await api('GET', S('/job'));
  $('#jobStatus').textContent = j.running ? `Detecting ${j.done}/${j.total}… (${j.current || ''})` : `Batch done: ${j.done}/${j.total}${j.errors.length ? ', ' + j.errors.length + ' errors' : ''}`;
  if (j.running) setTimeout(pollJob, 1000); else reload();
}

// ---------------------------------------------------------------- calibration dialogs
function dialog(html, onOk) {
  const d = $('#dlg'); $('#dlgForm').innerHTML = html + '<div class="row" style="justify-content:flex-end;margin-top:10px"><button value="cancel">Cancel</button><button value="ok" id="dlgOk">OK</button></div>';
  d.showModal(); d.onclose = () => { if (d.returnValue === 'ok') onOk(new FormData($('#dlgForm'))); };
}
function scaleDialog(line) {
  const L = Math.hypot(line[1][0] - line[0][0], line[1][1] - line[0][1]);
  dialog(`<h3>Set scale</h3><div>Line length: <b>${fmt(L, 2)} px</b> (original image pixels; zoom does not matter).</div>
    <label>Known length <input name="len" type="number" step="any" min="0" required></label>
    <label>Unit <select name="unit"><option>µm</option><option>mm</option><option>nm</option></select></label>
    <label><input type="checkbox" name="conf" required> I confirm this line spans exactly the known reference length.</label>`, async f => {
    if (!f.get('conf')) return msg('Calibration not saved: confirmation not ticked', true);
    await api('POST', S(`/image/${st.imageId}/calibration`), { line, known_length: +f.get('len'), unit: f.get('unit'), confirmed: true });
    msg('Scale saved and confirmed for this image.'); reload();
  });
}
function confirmEstimate() {
  const rec = st.detail.calibration.record;
  dialog(`<h3>Confirm estimated scale</h3><div>The orange dashed line on the image (${fmt(rec.line_length_px, 1)} px) is assumed to be <b>${rec.known_length} ${esc(rec.unit)}</b>.
    Zoom in and check that it matches the reference end to end. If not, cancel and draw a new line with the Scale tool.</div>
    <label><input type="checkbox" name="conf" required> I checked the line and it spans exactly ${rec.known_length} ${esc(rec.unit)}.</label>`, async f => {
    if (!f.get('conf')) return;
    await api('POST', S(`/calibration/${rec.cal_id}/confirm`), { image_id: st.imageId }); msg('Scale confirmed for this image.'); reload();
  });
}
function applyCalDialog() {
  const rec = st.detail.calibration.record;
  const others = st.summary.images.filter(i => i.image_id !== st.imageId);
  dialog(`<h3>Apply ${rec.cal_id} (${fmt(rec.um_per_px, 5)} µm/px) to other images</h3>
    <div style="max-height:240px;overflow:auto">${others.map(i => `<label><input type="checkbox" name="img" value="${i.image_id}"> ${i.image_id} ${esc(i.original_filename)} <span class="muted">(${i.width}×${i.height}, scale ${i.calibration})</span></label>`).join('')}</div>
    <label><input type="checkbox" name="compat" required> I confirm the selected images were acquired with the same objective/camera settings and were not resized or cropped differently.</label>`, async f => {
    const ids = f.getAll('img'); if (!ids.length) return;
    await api('POST', S(`/calibration/${rec.cal_id}/apply`), { image_ids: ids, compatible_confirmed: !!f.get('compat') });
    msg(`Calibration applied to ${ids.length} image(s).`); reload();
  });
}

// ---------------------------------------------------------------- tools & mouse
function setTool(t) {
  st.tool = t; st.poly = []; st.drag = null; st.stroke = null;
  document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
  const H = {
    select: 'Hover an object to see its measurements; click to select. Drag empty space to pan, wheel to zoom.',
    well: 'Drag a rectangle to add a well region. Drag a corner square to resize; Alt+drag moves a single corner (non-rectangular wells).',
    scale: 'Drag a line exactly along a known reference (e.g. the 500 µm bar). You will enter its length next.',
    brushAdd: 'Paint to ADD pixels to the selected object. Set brush radius (image px) in the toolbar.',
    brushErase: 'Paint to ERASE pixels from the selected object.',
    polyNew: 'Draw a NEW manual object. ' + SHAPE_HINT[drawShape()],
    polyAdd: 'Draw a shape to ADD to the selected object. ' + SHAPE_HINT[drawShape()],
    polyErase: 'Draw a shape to ERASE from the selected object. ' + SHAPE_HINT[drawShape()],
    assign: 'Orange = unassigned, purple = wall/shadow shape, white = cut line of a watershed split (assign it and the other piece to re-merge); with "removed pixels" on: pink = wall band, blue = thin, green = small. Yellow dashed = attached only by proximity. Click a part to add it to the selected object (click one of its own parts to remove it). No object selected, or Shift+click: the part becomes its own new object.',
  };
  $('#hint').textContent = H[t]; render();
}
document.querySelectorAll('[data-tool]').forEach(b => b.onclick = () => setTool(b.dataset.tool));
const needSel = () => { if (!st.selected) { msg('Select an object first (Select tool, click it).', true); return false; } return true; };

// ---------------------------------------------------------------- drawing shapes (ImageJ-like)
// Every shape is turned into a polygon in image coordinates and sent to the same endpoints;
// the server rasterises it (pixel centres inside the polygon). Ellipses use vertices <= 1.5 px apart.
const SHAPE_HINT = {
  polygon: 'Polygon: click vertices; double-click or Enter to finish; Ctrl+Z removes the last vertex; Esc cancels.',
  freehand: 'Freehand: press and drag around the region; release to close the outline.',
  ellipse: 'Ellipse: drag a box; the ellipse fills it. Hold Shift for a circle.',
  rectangle: 'Rectangle: drag a box. Hold Shift for a square.',
};
const isDraw = t => t === 'polyNew' || t === 'polyAdd' || t === 'polyErase';
const drawShape = () => ($('#drawShape') || {}).value || 'polygon';
$('#drawShape').value = localStorage.getItem('drawShape') || 'polygon';
$('#drawShape').onchange = e => { localStorage.setItem('drawShape', e.target.value); setTool(isDraw(st.tool) ? st.tool : 'polyNew'); };
const r2 = v => Math.round(v * 100) / 100;
function shapePolygon(d) {
  if (d.shape === 'freehand') {
    const pts = d.pts.filter((q, i, a) => i === 0 || Math.hypot(q[0] - a[i - 1][0], q[1] - a[i - 1][1]) > 0.25);
    return pts.length >= 3 ? pts.map(q => [r2(q[0]), r2(q[1])]) : null;
  }
  let [x0, y0] = d.p0, [x1, y1] = d.p1;
  if (d.shift) {                         // circle / square, as in ImageJ
    const s = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
    x1 = x0 + (x1 < x0 ? -s : s); y1 = y0 + (y1 < y0 ? -s : s);
  }
  const xa = Math.min(x0, x1), xb = Math.max(x0, x1), ya = Math.min(y0, y1), yb = Math.max(y0, y1);
  if (xb - xa < 1 || yb - ya < 1) return null;
  if (d.shape === 'rectangle') return [[xa, ya], [xb, ya], [xb, yb], [xa, yb]].map(q => q.map(r2));
  const cx = (xa + xb) / 2, cy = (ya + yb) / 2, a = (xb - xa) / 2, b = (yb - ya) / 2;
  const per = Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)));   // Ramanujan
  const n = Math.min(1440, Math.max(24, Math.ceil(per / 1.5)));
  return Array.from({ length: n }, (_, i) => [r2(cx + a * Math.cos(2 * Math.PI * i / n)), r2(cy + b * Math.sin(2 * Math.PI * i / n))]);
}
async function submitDrawn(pts, shape) {
  if (st.tool === 'polyNew') {
    const o = await api('POST', S(`/image/${st.imageId}/objects`), { polygon: pts, shape }); st.selected = o.object_id;
    msg(`New manual object ${o.object_id} (${shape})`);
  } else {
    if (!needSel()) return render();
    await editCall(`/object/${st.selected}/polygon`, { polygon: pts, mode: st.tool === 'polyAdd' ? 'add' : 'erase', shape });
  }
  reload();
}

function evPos(e) { const r = cv.getBoundingClientRect(); const sx = e.clientX - r.left, sy = e.clientY - r.top; return { sx, sy, p: toImg(sx, sy) }; }
cv.addEventListener('wheel', e => { e.preventDefault(); const { sx, sy } = evPos(e); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, sx, sy); }, { passive: false });
cv.addEventListener('contextmenu', e => e.preventDefault());
cv.addEventListener('mousedown', e => {
  if (!st.detail) return;
  const { sx, sy, p } = evPos(e);
  if (e.button === 1 || e.button === 2 || (st.tool === 'select' && !objectAt(...p)) || e.shiftKey && st.tool === 'select') {
    st.drag = { kind: 'pan', sx, sy, tx: st.view.tx, ty: st.view.ty }; return;
  }
  if (st.tool === 'select') { const o = objectAt(...p); if (o) select(o.object_id); return; }
  if (st.tool === 'well') {
    for (const w of st.detail.image.wells) w.polygon.forEach((q, i) => {
      if (!st.drag && Math.hypot(q[0] - p[0], q[1] - p[1]) * st.view.s < 8) st.drag = { kind: 'vertex', well: w.well_id, i, orig: w.polygon.map(v => [...v]) };
    });
    if (!st.drag) st.drag = { kind: 'rect', p0: p, p1: p };
    return;
  }
  if (st.tool === 'scale') { st.drag = { kind: 'line', p0: p, p1: p }; return; }
  if (isDraw(st.tool) && drawShape() !== 'polygon') {
    if (st.tool !== 'polyNew' && !needSel()) return;
    st.drag = { kind: 'shape', shape: drawShape(), p0: p, p1: p, pts: [p], shift: e.shiftKey }; return;
  }
  if (st.tool === 'brushAdd' || st.tool === 'brushErase') { if (!needSel()) return; st.stroke = [p]; render(); return; }
});
cv.addEventListener('mousemove', e => {
  if (!st.detail) return;
  const { sx, sy, p } = evPos(e); st.mouse = p;
  $('#coords').textContent = `x ${p[0].toFixed(1)}, y ${p[1].toFixed(1)} px · zoom ${Math.round(st.view.s * 100)}%`;
  const d = st.drag;
  if (d && d.kind === 'pan') { st.view.tx = d.tx + sx - d.sx; st.view.ty = d.ty + sy - d.sy; render(); return; }
  if (d && (d.kind === 'rect' || d.kind === 'line')) { d.p1 = p; render(); return; }
  if (d && d.kind === 'shape') { d.p1 = p; d.shift = e.shiftKey; d.pts.push(p); render(); return; }
  if (d && d.kind === 'vertex') {
    const w = st.detail.image.wells.find(w => w.well_id === d.well);
    if (w.polygon.length === 4 && !e.altKey && isRect(d.orig)) {
      // rectangle: opposite corner stays fixed (Alt-drag moves a single vertex instead)
      const o = d.orig[(d.i + 2) % 4];
      const x0 = Math.min(o[0], p[0]), x1 = Math.max(o[0], p[0]), y0 = Math.min(o[1], p[1]), y1 = Math.max(o[1], p[1]);
      w.polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
    } else w.polygon[d.i] = [p[0], p[1]];
    d.moved = true; render(); return;
  }
  if (st.stroke) { st.stroke.push(p); render(); return; }
  if (st.tool === 'select' || st.tool === 'assign') {
    const o = objectAt(...p); const id = o ? o.object_id : null;
    if (id !== st.hover) { st.hover = id; render(); }
    showTip(o, sx, sy);
  } else { $('#tip').hidden = true; render(); }
});
function isRect(P) {
  if (P.length !== 4) return false;
  const xs = new Set(P.map(q => q[0])), ys = new Set(P.map(q => q[1]));
  return xs.size === 2 && ys.size === 2;
}
window.addEventListener('mouseup', async e => {
  const d = st.drag; st.drag = null;
  if (st.stroke) {
    const pts = st.stroke; st.stroke = null;
    await editCall(`/object/${st.selected}/brush`, { points: pts, radius: brushR(), mode: st.tool === 'brushAdd' ? 'add' : 'erase' });
    return reload();
  }
  if (!d) return;
  if (d.kind === 'rect') {
    const [x0, x1] = [d.p0[0], d.p1[0]].sort((a, b) => a - b), [y0, y1] = [d.p0[1], d.p1[1]].sort((a, b) => a - b);
    if ((x1 - x0) * st.view.s < 6 || (y1 - y0) * st.view.s < 6) return render();
    const ids = new Set(st.detail.image.wells.map(w => w.well_id)); let k = st.detail.image.wells.length + 1; while (ids.has('W' + k)) k++;
    const W = st.img.naturalWidth, H = st.img.naturalHeight, c = (v, m) => Math.max(0, Math.min(m, v));
    const poly = [[c(x0, W), c(y0, H)], [c(x1, W), c(y0, H)], [c(x1, W), c(y1, H)], [c(x0, W), c(y1, H)]];
    return saveWells([...st.detail.image.wells, { well_id: 'W' + k, polygon: poly, source: 'manual' }]);
  }
  if (d.kind === 'vertex' && d.moved) return saveWells(st.detail.image.wells);
  if (d.kind === 'shape') {
    const pts = shapePolygon(d);
    if (!pts) { render(); return msg('Shape too small: drag a larger outline.', true); }
    return submitDrawn(pts, d.shape);
  }
  if (d.kind === 'line') {
    const line = [d.p0, d.p1]; if (Math.hypot(line[1][0] - line[0][0], line[1][1] - line[0][1]) < 2) return render();
    render(); return scaleDialog(line);
  }
});
cv.addEventListener('click', async e => {
  if (!st.detail) return;
  const { p } = evPos(e);
  if (isDraw(st.tool)) { if (drawShape() === 'polygon') { st.poly.push(p); render(); } return; }
  if (st.tool === 'assign') {
    const part = partAt(...p); if (!part) return msg('No part here.', true);
    if (!part.owner && (!st.selected || e.shiftKey)) {          // free part -> its own new object
      const o = await api('POST', S(`/image/${st.imageId}/object_from_part`), { well_id: part.well_id, part: part.part });
      st.selected = o.object_id; msg(`New object ${o.object_id} from part ${part.well_id}:${part.part} (${REASON_TXT[part.reason || part.kind] || part.kind})`); return reload();
    }
    if (!needSel()) return;
    const assign = part.owner !== st.selected;
    if (part.owner && part.owner !== st.selected && !confirm(`This part belongs to ${part.owner}. Move it to ${st.selected}?`)) return;
    const wasReviewed = (st.detail.objects.find(o => o.object_id === st.selected) || {}).status !== 'unreviewed';
    await editCall(`/object/${st.selected}/assign`, { well_id: part.well_id, part: part.part, assign });
    if (!wasReviewed) msg(`${assign ? 'Assigned' : 'Removed'} part ${part.well_id}:${part.part} (${REASON_TXT[part.reason || part.kind] || part.kind})`);
    reload();
  }
});
cv.addEventListener('dblclick', e => { if (isDraw(st.tool) && drawShape() === 'polygon') { e.preventDefault(); finishPoly(); } });
async function finishPoly() {
  // a double-click adds the same vertex twice; drop near-duplicates
  const pts = st.poly.filter((q, i, a) => i === 0 || Math.hypot(q[0] - a[i - 1][0], q[1] - a[i - 1][1]) > 0.5);
  st.poly = [];
  if (pts.length < 3) { render(); return msg('A polygon needs at least 3 vertices', true); }
  return submitDrawn(pts, 'polygon');
}
// ---------------------------------------------------------------- undo
function updateUndo() {
  const u = (st.detail && st.detail.undo) || { steps: 0 }, b = $('#btnUndo');
  b.disabled = !u.steps;
  b.title = u.steps ? `Undo: ${u.next} (Ctrl+Z / ⌘Z) — ${u.steps} step(s) on this image` : 'Nothing to undo on this image';
}
async function doUndo() {
  if (!st.imageId || !st.detail) return;
  if (!st.detail.undo || !st.detail.undo.steps) return msg('Nothing to undo on this image.', true);
  const r = await api('POST', S(`/image/${st.imageId}/undo`));
  await reload();
  msg(`Undone: ${r.undone}` + (r.objects.length ? ' — ' + r.objects.map(o => `${o.object_id}: ${o.restored}`).join('; ') : ''));
}
$('#btnUndo').onclick = doUndo;

window.addEventListener('keydown', e => {
  if (e.target.matches('input,textarea,select')) return;        // text fields keep the browser's own undo
  const k = e.key.toLowerCase();
  if (e.ctrlKey || e.metaKey) {
    if (k === 'z' && !e.shiftKey) {
      e.preventDefault();
      if (st.poly.length) { st.poly.pop(); render(); }           // while drawing a polygon: remove the last vertex
      else doUndo();
    }
    return;                                                     // no single-key shortcuts with Ctrl/⌘ held
  }
  if (k === 'escape') { st.poly = []; st.stroke = null; st.drag = null; render(); }
  else if (k === 'enter' && isDraw(st.tool) && drawShape() === 'polygon') finishPoly();
  else if (k === 'd') setTool('polyNew');
  else if (k === 'a' && st.selected) setStatus(st.selected, 'accepted');
  else if (k === 'r' && st.selected) setStatus(st.selected, 'rejected');
  else if (k === 'v') setTool('select'); else if (k === 'b') setTool('brushAdd'); else if (k === 'e') setTool('brushErase');
  else if (k === 'n') step(1); else if (k === 'p') step(-1);
  else if (k === '+' || k === '=') zoomAt(1.25); else if (k === '-') zoomAt(0.8); else if (k === 'f') fit();
});
['tEnv', 'tParts', 'tFill', 'tRemoved'].forEach(id => $('#' + id).onchange = render);
$('#reviewer').value = localStorage.getItem('reviewer') || '';
$('#reviewer').onchange = e => localStorage.setItem('reviewer', e.target.value.trim());

// ---------------------------------------------------------------- upload / export
$('#upload').onchange = async e => {
  if (!st.session) return msg('Create or open a session first', true);
  const files = [...e.target.files]; if (!files.length) return;
  for (let i = 0; i < files.length; i += 10) {           // small chunks keep memory and request size low
    const fd = new FormData(); files.slice(i, i + 10).forEach(f => fd.append('files', f, f.name));
    $('#jobStatus').textContent = `Uploading ${Math.min(i + 10, files.length)}/${files.length}…`;
    await api('POST', S('/upload'), fd, true);
  }
  e.target.value = ''; $('#jobStatus').textContent = ''; await refreshSummary();
  if (!st.imageId && st.summary.images.length) openImage(st.summary.images[0].image_id);
  msg(`${files.length} file(s) uploaded`);
};
$('#btnExport').onclick = async () => {
  if (!st.session) return;
  const j = await api('POST', S('/export'));
  $('#exportLink').innerHTML = `<a href="${S('/files/' + j.xlsx)}">spheroid_measurements.xlsx</a> <span class="muted small">(${j.accepted_rows} accepted, ${j.review_log_rows} log rows; folder ${esc(j.folder)})</span>`;
  msg('Export written.');
};

// ---------------------------------------------------------------- start
setTool('select'); resize();
api('GET', '/api/config').then(c => { st.config = c; });
loadSessions().then(() => {
  const want = new URLSearchParams(location.search).get('session') || localStorage.getItem('lastSession');
  if (want && [...$('#sessionSel').options].some(o => o.value === want)) { $('#sessionSel').value = want; openSession(want); }
});

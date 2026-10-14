// Spheroid Review — browser UI. All coordinates sent to the server are ORIGINAL-image
// pixel coordinates (continuous: pixel (r,c) spans [c,c+1)x[r,r+1)). Zoom/pan only change
// the view transform below and never what is stored or measured.
'use strict';
const $ = s => document.querySelector(s);
const st = {
  session: null, summary: null, imageId: null, detail: null, img: null,
  view: { s: 1, tx: 0, ty: 0 }, tool: 'select', selected: null, hover: null,
  drag: null, poly: [], stroke: null, busy: false,
  step: 'upload', phSel: new Set(), scaleLine: null, scaleDet: {},
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

// ---------------------------------------------------------------- steps
const STEPS = [['upload', 'Upload'], ['photos', 'Photos'], ['scale', 'Scale'], ['detect', 'Detect'], ['review', 'Review']];
const visImgs = () => (st.summary ? st.summary.images.filter(i => !i.hidden) : []);
const labText = lb => lb ? [lb.day ? 'D' + lb.day : '?', lb.shape || '?', lb.size || '?', lb.conc || '?'].join(' · ') : '';
function stepInfo(k) {
  const s = st.summary; if (!s) return { done: false, sub: '' };
  const v = visImgs(), bl = s.blockers;
  if (k === 'upload') return { done: s.images.length > 0, sub: s.images.length ? String(s.images.length) : '' };
  if (k === 'photos') {
    const need = v.filter(i => !i.labels_complete).length, hid = s.images.length - v.length;
    return { done: v.length > 0 && !need, sub: need ? `${need} need labels` : (hid ? `${hid} hidden` : '') };
  }
  if (k === 'scale') return { done: s.scale.status === 'confirmed', sub: s.scale.status === 'confirmed' ? `${fmt(s.scale.um_per_px, 3)} µm/px` : '' };
  if (k === 'detect') { const n = v.filter(i => i.processing === 'processed').length; return { done: v.length > 0 && n === v.length, sub: v.length ? `${n}/${v.length}` : '' }; }
  return { done: !!bl.ready || (v.length > 0 && !bl.to_review && !bl.unprocessed.length), sub: bl.to_review ? `${bl.to_review} to review` : '' };
}
function renderStepper() {
  $('#stepper').innerHTML = STEPS.map(([k, name], i) => {
    const inf = stepInfo(k);
    return `${i ? '<span class="sep">›</span>' : ''}<button data-step="${k}" class="${k === st.step ? 'cur' : ''} ${inf.done ? 'done' : ''}"><span class="n">${inf.done ? '✓' : i + 1}</span>${name}${inf.sub ? `<span class="sub">${esc(inf.sub)}</span>` : ''}</button>`;
  }).join('');
  document.querySelectorAll('#stepper button').forEach(b => b.onclick = () => go(b.dataset.step));
}
function blockText(bl) {
  const p = [];
  if (!bl.n_visible) p.push('no visible photos');
  if (bl.missing_labels.length) p.push(`${bl.missing_labels.length} photo(s) need labels`);
  if (bl.scale.length) p.push('scale not confirmed');
  if (bl.unprocessed.length) p.push(`${bl.unprocessed.length} photo(s) not detected yet`);
  if (bl.to_review) p.push(`${bl.to_review} object(s) still to review in ${bl.to_review_images.length} photo(s)`);
  return p.join(' · ');
}
function renderExportBtn() {
  const b = $('#btnExport'); b.hidden = st.step !== 'review';
  if (!st.summary) return;
  const bl = st.summary.blockers;
  b.disabled = !bl.ready;
  b.title = bl.ready ? 'Write the Excel file (one sheet per day + Analiz)' : 'Not ready: ' + blockText(bl);
  $('#exportLink').hidden = st.step !== 'review';
}
function autoStep() {
  const s = st.summary, v = visImgs(), bl = s.blockers;
  if (!s.images.length) return 'upload';
  if (v.some(i => !i.labels_complete)) return 'photos';
  if (s.scale.status !== 'confirmed') return 'scale';
  if (bl.unprocessed.length) return 'detect';
  return 'review';
}
async function go(step) {
  if (!st.session) return msg('Create or open a session first', true);
  st.step = step; document.body.dataset.step = step; st.tool === 'scale' && step !== 'scale' && setTool('select');
  $('#tip').hidden = true;
  if (step === 'scale' || step === 'review') {
    await refreshSummary();
    const v = visImgs();
    let cur = v.find(i => i.image_id === st.imageId);
    if (!cur && v.length) cur = (step === 'review' && v.find(i => i.objects.unreviewed)) || v[0];
    if (cur) await openImage(cur.image_id); else { st.detail = null; $('#empty').hidden = false; $('#empty').textContent = 'No visible photos.'; render(); }
    if (step === 'scale') { setTool('select'); } else setTool(st.tool === 'scale' ? 'select' : st.tool);
    resize(); fit();
    if (step === 'scale' && st.detail) autoDetectScale();
  } else await refreshSummary();
  renderStepper(); renderExportBtn();
}

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
  st.session = name; st.imageId = null; st.detail = null; st.selected = null; st.phSel = new Set(); st.scaleLine = null; st.scaleDet = {};
  localStorage.setItem('lastSession', name);
  await refreshSummary();
  await go(autoStep());
}
$('#sessionSel').onchange = e => openSession(e.target.value);
$('#btnNewSession').onclick = async () => {
  const n = $('#newSession').value.trim(); if (!n) return msg('Enter a session name', true);
  await api('POST', '/api/sessions', { name: n }); await loadSessions(n); $('#newSession').value = ''; openSession(n);
};

// ---------------------------------------------------------------- summary / photo list
async function refreshSummary() {
  st.summary = await api('GET', S('/summary'));
  const p = st.summary.progress, v = visImgs();
  const done = v.filter(i => i.reviewed).length;
  $('#progress').innerHTML = `<span><b>${done}/${v.length}</b> photos done</span><span class="muted">${st.summary.blockers.to_review} to review</span>
    <div class="bar"><i style="width:${v.length ? 100 * done / v.length : 0}%"></i></div>`;
  renderImageList();
  if (st.step === 'photos') renderPhotos();
  if (st.step === 'detect') renderDetect();
  if (st.step === 'upload') renderUploadInfo();
  renderStepper(); renderExportBtn();
}
function renderImageList() {
  const only = $('#onlyTodo').checked && st.step === 'review';
  const list = visImgs().filter(im => !only || im.objects.unreviewed || im.processing !== 'processed');
  $('#imageList').innerHTML = list.map(im => {
    const o = im.objects; let b = '';
    if (st.step === 'review') {
      if (im.processing !== 'processed') b = `<span class="badge warn">${esc(im.processing)}</span>`;
      else if (o.unreviewed) b = `<span class="badge warn">${o.unreviewed} to review</span>`;
      else b = '<span class="badge ok">done ✓</span>';
    } else if (!im.labels_complete) b = '<span class="badge warn">needs labels</span>';
    return `<li data-id="${im.image_id}" class="${im.image_id === st.imageId ? 'cur' : ''}" title="${esc(im.original_filename)}">
      <div class="fn">${esc(im.original_filename)}</div>
      <div class="sub"><span>${esc(labText(im.labels))}</span>${b}</div></li>`;
  }).join('') || '<li class="muted">No photos.</li>';
  for (const li of document.querySelectorAll('#imageList li[data-id]')) li.onclick = () => openImage(li.dataset.id);
}
$('#onlyTodo').onchange = renderImageList;

async function openImage(id, keepView) {
  const changed = id !== st.imageId;
  st.imageId = id; if (changed) { st.selected = null; st.poly = []; st.hover = null; $('#tip').hidden = true; st.scaleLine = null; }
  if (changed) { flushActive(); api('POST', S(`/image/${id}/view`)).catch(() => {}); }
  let detail = await api('GET', S(`/image/${id}`));
  if (!detail.image.wells.length) { await api('POST', S(`/image/${id}/whole_image`)); detail = await api('GET', S(`/image/${id}`)); }  // one hidden whole-image region
  st.detail = detail;
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
  $('#empty').hidden = true; fixState();
  if (st.step === 'scale') { renderScale(); if (changed) autoDetectScale(); }
  else renderPanels();
  render();
}
async function reload() {
  st.pending = (st.pending || 0) + 1; $('#busy').hidden = false;
  try { await openImage(st.imageId, true); await refreshSummary(); }
  finally { st.pending--; if (!st.pending) $('#busy').hidden = true; }
}

// ---------------------------------------------------------------- 1 · upload
function renderUploadInfo() {
  const n = st.summary ? st.summary.images.length : 0;
  $('#uploadInfo').innerHTML = n ? `<div class="panelcard"><b>${n}</b> photo(s) in this session. <button class="primary" id="toPhotos">Continue to Photos →</button></div>` : '';
  const b = $('#toPhotos'); if (b) b.onclick = () => go('photos');
}
async function uploadFiles(files) {
  if (!st.session) return msg('Create or open a session first', true);
  files = [...files].filter(f => /\.(jpe?g|png|tiff?|bmp)$/i.test(f.name)); if (!files.length) return msg('No image files found', true);
  for (let i = 0; i < files.length; i += 10) {           // small chunks keep memory and request size low
    const fd = new FormData(); files.slice(i, i + 10).forEach(f => fd.append('files', f, f.name)); fd.append('whole_region', '1');
    $('#uploadStatus').textContent = `Uploading ${Math.min(i + 10, files.length)}/${files.length}…`;
    await api('POST', S('/upload'), fd, true);
  }
  $('#uploadStatus').textContent = ''; await refreshSummary();
  msg(`${files.length} photo(s) uploaded`);
  await go('photos');
}
$('#upload').onchange = async e => { const f = [...e.target.files]; e.target.value = ''; await uploadFiles(f); };
$('#uploadDir').onchange = async e => { const f = [...e.target.files]; e.target.value = ''; await uploadFiles(f); };
{
  const d = $('#drop');
  ['dragenter', 'dragover'].forEach(ev => d.addEventListener(ev, e => { e.preventDefault(); d.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => d.addEventListener(ev, e => { e.preventDefault(); d.classList.remove('over'); }));
  d.addEventListener('drop', e => uploadFiles(e.dataTransfer.files));
}

// ---------------------------------------------------------------- 2 · photos (labels, hide)
function renderPhotos() {
  const s = st.summary; if (!s) return;
  const showHidden = $('#phHidden').checked, needs = $('#phNeeds').checked;
  const all = s.images, v = visImgs(), need = v.filter(i => !i.labels_complete).length;
  $('#phSummary').textContent = `${all.length} photo(s) · ${all.length - v.length} hidden · ${need} need labels. Hidden photos are kept but skipped by detection, review and Excel.`;
  let shown = all.filter(i => (showHidden || !i.hidden) && (!needs || (!i.labels_complete && !i.hidden)));
  const sel = st.phSel = new Set([...st.phSel].filter(id => shown.some(i => i.image_id === id)));
  const opt = (vals, cur) => '<option value="">—</option>' + vals.map(x => `<option ${String(x) === String(cur) ? 'selected' : ''}>${x}</option>`).join('');
  $('#phGrid').innerHTML = shown.map(im => {
    const lb = im.labels, miss = k => lb[k] == null ? 'miss' : '';
    return `<div class="pcard ${!im.labels_complete && !im.hidden ? 'need' : ''} ${sel.has(im.image_id) ? 'sel' : ''} ${im.hidden ? 'hid' : ''}" data-id="${im.image_id}">
      <img loading="lazy" src="${S('/image/' + im.image_id + '/thumb')}" alt="">
      <div class="top"><label class="small"><input type="checkbox" data-pick ${sel.has(im.image_id) ? 'checked' : ''}></label>
        <button class="small" data-hide="${im.hidden ? 0 : 1}">${im.hidden ? 'Restore' : 'Hide'}</button></div>
      <div class="fn">${esc(im.original_filename)}</div>
      <div class="lab">
        <select data-k="day" class="${miss('day')}" title="Day">${'<option value="">Day —</option>' + [1, 3, 5, 7].map(x => `<option value="${x}" ${lb.day === x ? 'selected' : ''}>Day ${x}</option>`).join('')}</select>
        <select data-k="shape" class="${miss('shape')}" title="Shape">${'<option value="">Shape —</option>' + ['Piramit', 'Oval', 'Yamuk'].map(x => `<option ${lb.shape === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
        <select data-k="size" class="${miss('size')}" title="Well size">${'<option value="">Well size —</option>' + [600, 400, 200].map(x => `<option value="${x}" ${lb.size === x ? 'selected' : ''}>Well ${x}</option>`).join('')}</select>
        <select data-k="conc" class="${miss('conc')}" title="Concentration">${'<option value="">Conc. —</option>' + [50, 100, 150, 200].map(x => `<option value="${x}" ${lb.conc === x ? 'selected' : ''}>Conc. ${x}</option>`).join('')}</select>
      </div></div>`;
  }).join('') || '<div class="muted">Nothing to show.</div>';
  $('#phSel').textContent = `${sel.size} selected`;
  $('#phAll').checked = shown.length > 0 && sel.size === shown.length;
  document.querySelectorAll('#phGrid .pcard').forEach(card => {
    const id = card.dataset.id;
    const pick = (on) => { if (on === undefined) on = !st.phSel.has(id); on ? st.phSel.add(id) : st.phSel.delete(id); renderPhotos(); };
    card.querySelector('[data-pick]').onchange = e => pick(e.target.checked);
    card.querySelector('img').onclick = () => pick();
    card.querySelector('[data-hide]').onclick = async e => {
      await api('PUT', S('/hidden'), { image_ids: [id], hidden: e.target.dataset.hide === '1' }); st.phSel.delete(id); refreshSummary();
    };
    card.querySelectorAll('select[data-k]').forEach(sl => sl.onchange = async () => {
      const k = sl.dataset.k; await api('PUT', S('/labels'), { image_ids: [id], labels: { [k]: sl.value === '' ? null : sl.value } }); refreshSummary();
    });
  });
}
$('#phAll').onchange = e => {
  const showHidden = $('#phHidden').checked, needs = $('#phNeeds').checked;
  const shown = st.summary.images.filter(i => (showHidden || !i.hidden) && (!needs || (!i.labels_complete && !i.hidden)));
  st.phSel = e.target.checked ? new Set(shown.map(i => i.image_id)) : new Set(); renderPhotos();
};
$('#phHidden').onchange = renderPhotos; $('#phNeeds').onchange = renderPhotos;
$('#bApply').onclick = async () => {
  const ids = [...st.phSel]; if (!ids.length) return msg('Select photos first (tick the boxes).', true);
  const labels = {};
  for (const [id, k] of [['bDay', 'day'], ['bShape', 'shape'], ['bSize', 'size'], ['bConc', 'conc']]) if ($('#' + id).value) labels[k] = $('#' + id).value;
  if (!Object.keys(labels).length) return msg('Choose at least one label to apply.', true);
  await api('PUT', S('/labels'), { image_ids: ids, labels }); msg(`Labels set on ${ids.length} photo(s).`);
  ['bDay', 'bShape', 'bSize', 'bConc'].forEach(id => $('#' + id).value = ''); refreshSummary();
};
$('#bHide').onclick = async () => {
  const ids = [...st.phSel]; if (!ids.length) return msg('Select photos first (tick the boxes).', true);
  await api('PUT', S('/hidden'), { image_ids: ids, hidden: true }); st.phSel = new Set();
  msg(`${ids.length} photo(s) hidden. Tick "show hidden" to restore them.`); refreshSummary();
};

// ---------------------------------------------------------------- 3 · scale
async function autoDetectScale() {
  const id = st.imageId; if (!id) return;
  let r = st.scaleDet[id];
  if (!r) { try { r = st.scaleDet[id] = await api('POST', S(`/image/${id}/detect_scale`)); } catch (e) { r = { found: false, reason: 'detection failed' }; } }
  if (id !== st.imageId) return;
  if (r.found && !st.scaleLine) st.scaleLine = { line: r.line, source: 'auto', known: r.known_length, unit: r.unit, bbox: r.bbox };
  renderScale(); render();
  if (r.found && st.scaleLine && st.scaleLine.bbox && st.scaleLine.source === 'auto') focusOn(r.bbox);
}
function focusOn(b) {   // zoom to the bar so the researcher can check its ends
  const r = cv.getBoundingClientRect(), pad = 60;
  const w = b[2] - b[0] + 2 * pad, h = Math.max(b[3] - b[1] + 2 * pad, 120);
  const s = Math.min(r.width / w, r.height / h, 4);
  st.view = { s, tx: r.width / 2 - (b[0] + b[2]) / 2 * s, ty: r.height / 2 - (b[1] + b[3]) / 2 * s }; render();
}
const sLen = l => Math.hypot(l.line[1][0] - l.line[0][0], l.line[1][1] - l.line[0][1]);
function renderScale() {
  const d = st.detail; if (!d) return;
  const sc = st.summary.scale, im = d.image, L = st.scaleLine, det = st.scaleDet[im.image_id];
  const conf = sc.status === 'confirmed';
  const odd = visImgs().filter(i => i.width !== im.width || i.height !== im.height).length;
  let h = `<h3 style="margin:2px 0 6px">3 · Scale</h3>
    <div class="small muted">Pick <b>one</b> photo that shows the scale bar (list on the left). Check the line, confirm — the same scale is used for <b>all</b> photos.</div>`;
  if (conf) h += `<div class="res"><b>Confirmed:</b> ${fmt(sc.um_per_px, 5)} µm/px<br><span class="small">line ${fmt(sc.known_length ? Math.hypot(sc.line[1][0] - sc.line[0][0], sc.line[1][1] - sc.line[0][1]) : 0, 1)} px = ${fmt(sc.known_length, 0)} ${esc(sc.unit)} · taken from ${sc.source_image_id}${sc.different_size.length ? ` · <span class="warnline">${sc.different_size.length} photo(s) have another pixel size</span>` : ''}</span></div>`;
  if (det && det.found) h += `<div class="small"><span class="badge ok">scale bar found</span> ${det.n_ticks === 11 ? '11 tick marks' : 'bar ends'} · ${fmt(det.length_px, 1)} px (assuming ${det.known_length} ${esc(det.unit)}). Check both ends, then confirm.</div>`;
  else if (det) h += `<div class="small"><span class="badge warn">no scale bar found automatically</span> ${esc(det.reason || '')} Draw the line by hand: tool “Draw line by hand”, drag from one end of the bar to the other (Shift = horizontal).</div>`;
  else h += '<div class="small muted">Looking for the scale bar…</div>';
  if (L) {
    const len = +(L.known ?? 500), um = len * ({ 'µm': 1, mm: 1000, nm: 0.001 }[L.unit || 'µm']) / sLen(L);
    h += `<div class="box"><div class="row">Line length <b>${fmt(sLen(L), 1)} px</b> <span class="small muted">(${L.source === 'auto' ? 'detected' : 'drawn by hand'}; drag an end to adjust)</span></div>
      <div class="row">equals <input id="slLen" type="number" step="any" min="0" value="${len}" style="width:6em"> <select id="slUnit"><option ${L.unit === 'µm' || !L.unit ? 'selected' : ''}>µm</option><option ${L.unit === 'mm' ? 'selected' : ''}>mm</option><option ${L.unit === 'nm' ? 'selected' : ''}>nm</option></select>
        <span class="small muted">(the bar is assumed to be 500 µm — change it if yours differs)</span></div>
      <div class="res ${len > 0 ? '' : 'no'}" id="slRes"><b>${len > 0 ? fmt(um, 5) : '—'} µm per pixel</b></div>
      ${odd ? `<div class="small warnline">${odd} photo(s) have a different pixel size than this one. The scale is only valid for photos taken with the same objective and not resized.</div>` : ''}
      <label class="row small"><input type="checkbox" id="slCompat"> All photos of this session have the same magnification and were not resized differently.</label>
      <div class="row"><button class="primary" id="slConfirm" disabled>Confirm scale for all photos</button></div></div>`;
  }
  h += `<div class="row"><button id="slAgain">Detect again</button>${conf ? '<button id="slNext" class="primary">Next: Detect →</button>' : ''}</div>`;
  $('#scalePanel').innerHTML = h;
  const upd = () => { const ok = L && +$('#slLen').value > 0 && $('#slCompat').checked; $('#slConfirm').disabled = !ok; };
  if (L) {
    $('#slLen').oninput = () => { L.known = +$('#slLen').value; upd(); scaleNumbers(); };
    $('#slUnit').onchange = () => { L.unit = $('#slUnit').value; renderScale(); };
    $('#slCompat').onchange = upd;
    $('#slConfirm').onclick = async () => {
      await api('POST', S('/scale'), { line: L.line, known_length: +$('#slLen').value, unit: $('#slUnit').value, source_image_id: st.imageId });
      msg('Scale confirmed for all photos. Measurements are re-calculated with it.'); st.scaleLine = null; await reload(); renderScale();
    };
  }
  $('#slAgain').onclick = () => { delete st.scaleDet[st.imageId]; st.scaleLine = null; renderScale(); autoDetectScale(); };
  const nx = $('#slNext'); if (nx) nx.onclick = () => go('detect');
}
function scaleNumbers() {
  const L = st.scaleLine, el = $('#slRes'); if (!L || !el) return;
  const len = +$('#slLen').value, um = len * ({ 'µm': 1, mm: 1000, nm: 0.001 }[$('#slUnit').value]) / sLen(L);
  el.className = 'res' + (len > 0 ? '' : ' no'); el.innerHTML = `<b>${len > 0 ? fmt(um, 5) : '—'} µm per pixel</b>`;
}

// ---------------------------------------------------------------- 4 · detect
function renderDetect() {
  const s = st.summary; if (!s) return;
  const v = visImgs(), job = s.job, n = v.filter(i => i.processing === 'processed').length;
  const err = v.filter(i => i.processing === 'error' || i.processing === 'needs_wells').length;
  const profiles = st.config ? Object.keys(st.config.profiles) : [];
  const cur = v.length ? v[0].profile : '';
  $('#detPanel').innerHTML = `<div class="panelcard">
    <div class="stat"><div><b>${v.length}</b>photos to detect</div><div><b>${n}</b>done</div><div><b>${v.length - n}</b>waiting</div>${err ? `<div><b style="color:var(--bad)">${err}</b>failed</div>` : ''}</div>
    ${job.running ? `<div class="big">Detecting ${job.done}/${job.total}… <span class="muted small">${esc(job.current || '')}</span></div><div class="bar"><i style="width:${job.total ? 100 * job.done / job.total : 0}%"></i></div>` :
      `<div class="row"><button class="primary" id="detGo">${n ? 'Detect again (all photos)' : 'Detect all photos'}</button>
        ${n === v.length && v.length ? '<button id="detNext" class="primary">Next: Review →</button>' : ''}
        ${job.finished ? `<span class="muted small">Last run: ${job.done}/${job.total}${job.errors.length ? ', ' + job.errors.length + ' error(s)' : ''}</span>` : ''}</div>`}
    <div class="small muted">Model: <b>${esc(cur)}</b>. Hidden photos are skipped. Objects you already approved, rejected, edited or drew are kept when you detect again.</div>
    <details style="margin-top:6px"><summary>Advanced</summary>
      <div class="row">Model for all photos <select id="detProf">${profiles.map(p => `<option ${p === cur ? 'selected' : ''}>${p}</option>`).join('')}</select> <button id="detProfSet" class="small">Use</button></div>
      <div class="small muted">${esc(((st.config && st.config.profiles[cur]) || {}).description || '')}</div></details></div>`;
  $('#detList').innerHTML = v.map(im => `<div class="listrow"><span class="fn">${esc(im.original_filename)} <span class="muted small">${esc(labText(im.labels))}</span></span>
    ${im.processing === 'processed' ? `<span class="badge ok">${im.objects.total} object(s)</span>` : `<span class="badge ${im.processing === 'error' || im.processing === 'needs_wells' ? 'bad' : 'warn'}">${esc(im.processing)}</span>`}
    <button class="small" data-rerun="${im.image_id}" ${job.running ? 'disabled' : ''}>${im.processing === 'processed' ? 'Re-run' : 'Detect'}</button></div>`).join('');
  const go1 = $('#detGo'); if (go1) go1.onclick = async () => {
    if (n && !confirm('Detect again on all photos? Unreviewed, unedited automatic objects are replaced; approved, rejected, edited and drawn objects are kept.')) return;
    await api('POST', S('/process_all'), { image_ids: v.map(i => i.image_id), whole_region: true }); pollJob();
  };
  const nx = $('#detNext'); if (nx) nx.onclick = () => go('review');
  const ps = $('#detProfSet'); if (ps) ps.onclick = async () => {
    await api('PUT', S('/profile_many'), { image_ids: s.images.map(i => i.image_id), profile: $('#detProf').value }); msg('Model set for all photos.'); refreshSummary();
  };
  document.querySelectorAll('[data-rerun]').forEach(b => b.onclick = async () => {
    const id = b.dataset.rerun; b.disabled = true; msg('Detecting…');
    const p = await api('POST', S(`/image/${id}/process`), { whole_region: true });
    msg(p.status === 'processed' ? `Done: ${p.new_objects} new object(s)` : p.message || p.status, p.status !== 'processed'); refreshSummary();
  });
}
async function pollJob() {
  const j = await api('GET', S('/job'));
  await refreshSummary();
  if (j.running) setTimeout(pollJob, 1000); else msg(`Detection finished: ${j.done}/${j.total}${j.errors.length ? ', ' + j.errors.length + ' error(s)' : ''}`, j.errors.length > 0);
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
  if (st.step === 'scale') return drawScaleLine();
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


function scaleHandleAt(p) {
  const l = st.scaleLine && st.scaleLine.line; if (!l) return null;
  for (let i = 0; i < 2; i++) if (Math.hypot(l[i][0] - p[0], l[i][1] - p[1]) * st.view.s < 10) return i;
  return null;
}
function drawScaleLine() {
  const sc = st.summary && st.summary.scale, S0 = st.scaleLine;
  let line = null, col = '#ff9f1a', label = '';
  if (S0) { line = S0.line; label = `${S0.known ?? 500} ${S0.unit || 'µm'} = ${fmt(sLen(S0), 1)} px`; }
  else if (sc && sc.status === 'confirmed' && sc.source_image_id === st.imageId) { line = sc.line; col = '#2bd65a'; label = `confirmed · ${sc.known_length} ${sc.unit}`; }
  if (!line) return;
  const [a, b] = toScr(...line[0]), [p, q] = toScr(...line[1]);
  ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 2; ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(p, q); ctx.stroke();
  for (const [x, y] of [[a, b], [p, q]]) { ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill(); }
  ctx.font = '13px sans-serif'; ctx.fillText(label, Math.min(a, p), Math.min(b, q) - 10);
  if (st.drag && st.drag.kind === 'line') { const [c, d] = toScr(...st.drag.p0), [e, f] = toScr(...st.drag.p1); ctx.strokeStyle = '#fff'; ctx.beginPath(); ctx.moveTo(c, d); ctx.lineTo(e, f); ctx.stroke(); }
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
  if (st.step !== 'review') return null;
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

// ---------------------------------------------------------------- 5 · review: panels
const cellUnits = o => {          // the four numbers the researcher works with (region "cells", µm when the scale is confirmed)
  const m = o.measurements; if (!m || !m.cells || !m.cells.px) return null;
  const c = m.cells, u = c.um || c.px, um = !!c.um;
  return { um, diameter: u.ecd, perimeter: u.perimeter, area: u.area, circ: c.px.circularity };
};
function measure4(o) {
  const v = cellUnits(o); if (!v) return '<div class="muted">empty mask — no measurements</div>';
  const L = v.um ? 'µm' : 'px', A = v.um ? 'µm²' : 'px²';
  return `<div class="measure4"><div>Diameter <b>${fmt(v.diameter)} ${L}</b></div><div>Perimeter <b>${fmt(v.perimeter)} ${L}</b></div>
    <div>Area <b>${fmt(v.area, 0)} ${A}</b></div><div>Circularity <b>${fmt(v.circ, 3)}</b></div></div>`;
}
function showTip(o, sx, sy) {
  const t = $('#tip');
  if (!o || st.step !== 'review') { t.hidden = true; return; }
  const v = cellUnits(o), L = v && v.um ? 'µm' : 'px', A = v && v.um ? 'µm²' : 'px²';
  t.innerHTML = `<div><b>${o.object_id}</b> · ${o.status === 'unreviewed' ? 'to review' : o.status === 'accepted' ? 'approved' : 'rejected'}${o.source === 'manual' ? ' · drawn by you' : ''}</div>` +
    (v ? `<table><tr><td>Diameter</td><td class="n">${fmt(v.diameter)} ${L}</td></tr><tr><td>Perimeter</td><td class="n">${fmt(v.perimeter)} ${L}</td></tr>
      <tr><td>Area</td><td class="n">${fmt(v.area, 0)} ${A}</td></tr><tr><td>Circularity</td><td class="n">${fmt(v.circ, 3)}</td></tr></table>` : '<div>empty mask</div>') +
    (v && !v.um ? '<div class="small" style="color:#ffcc66">scale not confirmed — values in pixels</div>' : '') +
    '<div class="small" style="color:#9ecbff">click to select</div>';
  t.hidden = false;
  const r = $('#stage').getBoundingClientRect();
  t.style.left = Math.min(sx + 14, r.width - t.offsetWidth - 4) + 'px';
  t.style.top = Math.min(sy + 14, r.height - t.offsetHeight - 4) + 'px';
}

function renderPanels() {
  const d = st.detail, im = d.image;
  const sm = st.summary.images.find(i => i.image_id === im.image_id) || { labels: {} };
  const sc = st.summary.scale;
  const tune = im.used_for_tuning ? '<span class="badge warn" title="Profiles were tuned on this photo: do not use it to measure segmentation accuracy">used for tuning</span>' : '';
  $('#imgInfo').innerHTML = `<div class="fn" style="font-weight:600;word-break:break-all">${esc(im.original_filename)}</div>
    <div class="crumb small muted">${esc(labText(sm.labels))} ${tune}${sc.status === 'confirmed' ? '' : ' · <span class="warnline">scale not confirmed (values in px)</span>'}</div>
    <div class="row"><button id="prevImg">◀ Prev</button><button id="nextImg">Next ▶</button>
      <button id="btnDetect" title="Run detection again on this photo only">Re-run detection</button>
      <span class="small muted">${im.processing.status === 'processed' ? '' : esc(im.processing.status)}${im.processing.message ? ' · ' + esc(im.processing.message) : ''}</span></div>`;
  $('#btnDetect').onclick = detectThis;
  $('#prevImg').onclick = () => step(-1); $('#nextImg').onclick = () => step(1);
  showActive();
  $('#profBox').innerHTML = `<div class="row">Model <select id="profSel">${d.profiles.map(p => `<option ${p === im.profile ? 'selected' : ''}>${p}</option>`).join('')}</select>
    <button id="btnProfAll" class="small" title="Use this model for every photo (takes effect at the next detection)">for all photos</button></div>
    <div class="small muted">${esc(((st.config && st.config.profiles[im.profile]) || {}).description || '').slice(0, 200)}</div>`;
  $('#profSel').onchange = async e => { await api('PUT', S(`/image/${im.image_id}/profile`), { profile: e.target.value }); msg('Model set. Re-run detection to apply it.'); reload(); };
  $('#btnProfAll').onclick = async () => {
    if (!confirm(`Use model "${im.profile}" for all ${st.summary.images.length} photos? (Takes effect at the next detection.)`)) return;
    await api('PUT', S('/profile_many'), { image_ids: st.summary.images.map(i => i.image_id), profile: im.profile }); msg('Model set for all photos.'); reload();
  };
  renderRefs(); renderObjects();
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



function renderObjects() {
  const objs = st.detail.objects;
  const n = s => objs.filter(o => o.status === s).length;
  const todo = n('unreviewed');
  $('#objBox').innerHTML = `<h4>Objects · <span class="${todo ? 'warnline' : ''}">${todo} to review</span> · ${n('accepted')} approved · ${n('rejected')} rejected</h4>
    ${objs.length ? `<div class="row"><button id="btnAllOk" ${todo ? '' : 'disabled'} title="Approve every object of this photo that is still to review">Approve all remaining (${todo})</button>
      <button id="btnAllReset" class="small" ${objs.some(o => o.status !== 'unreviewed') ? '' : 'disabled'} title="Put every decision of this photo back to “to review”">Reset all</button></div>` :
      '<div class="muted">No objects found. Use “New object” (D) to draw one yourself, or re-run detection.</div>'}
    ${objs.map(o => `<div class="card ${o.object_id === st.selected ? 'sel' : ''}" data-oid="${o.object_id}">
      <div class="t"><b>${o.object_id}</b><span class="small muted">${[o.source === 'manual' ? 'drawn by you' : '', o.edited ? 'edited' : ''].filter(Boolean).join(' · ')}</span>
        <span class="badge ${o.status === 'accepted' ? 'ok' : o.status === 'rejected' ? 'bad' : 'warn'}">${o.status === 'unreviewed' ? 'to review' : o.status === 'accepted' ? 'approved' : 'rejected'}</span></div>
      ${measure4(o)}
      ${flagList(o).length ? `<div class="small warnline">${flagList(o).join(' · ')}</div>` : ''}
      ${o.proximity_parts ? `<div class="small" style="color:#9a6700">${o.proximity_parts} part(s) attached only by proximity (yellow dashed) — unconfirmed <button class="small" data-detach="1">Detach them</button></div>` : ''}
      <div class="row">
        <button class="ok" data-act="accepted">Approve (A)</button><button class="bad" data-act="rejected">Reject (R)</button>
        <button data-act="unreviewed" class="small">Reset</button>
        ${o.source === 'manual' ? '<button class="small" data-del="1">Delete</button>' : ''}</div>
      <details><summary>all measurements</summary><table>${measRows(o, REGIONS)}</table></details></div>`).join('')}`;
  const ba = $('#btnAllOk'); if (ba) ba.onclick = async () => { const r = await api('POST', S(`/image/${st.imageId}/status_all`), { status: 'accepted', only_status: 'unreviewed' }); msg(`${r.changed} object(s) approved.`); reload(); };
  const br = $('#btnAllReset'); if (br) br.onclick = async () => { if (!confirm('Put all decisions of this photo back to “to review”?')) return; await api('POST', S(`/image/${st.imageId}/status_all`), { status: 'unreviewed' }); reload(); };
  for (const card of document.querySelectorAll('.card')) {
    const oid = card.dataset.oid;
    card.onclick = e => { if (e.target.closest('button,input,textarea,label,details')) return; select(oid); };
    card.querySelectorAll('[data-act]').forEach(b => b.onclick = () => setStatus(oid, b.dataset.act));
    const det = card.querySelector('[data-detach]'); if (det) det.onclick = async () => { await api('POST', S(`/object/${oid}/detach_proximity`)); msg('Proximity parts detached'); reload(); };
    const del = card.querySelector('[data-del]'); if (del) del.onclick = async () => { if (confirm('Delete your drawn object ' + oid + '?')) { await api('DELETE', S(`/object/${oid}`)); if (st.selected === oid) st.selected = null; reload(); } };
  }
}
function fixState() { const g = $('#fixGroup'); if (g) g.classList.toggle('off', !st.selected); }
function select(oid) {
  st.selected = oid; fixState(); renderObjects(); render();
  const c = document.querySelector(`.card[data-oid="${oid}"]`); if (c) c.scrollIntoView({ block: 'nearest' });
}
async function setStatus(oid, status) {
  await api('POST', S(`/object/${oid}/status`), { status }); await reload();
}
function step(k) {
  const ims = visImgs(); const i = ims.findIndex(x => x.image_id === st.imageId);
  const j = i + k; if (j >= 0 && j < ims.length) openImage(ims[j].image_id);
}

// ---------------------------------------------------------------- detection of one photo
async function detectThis() {
  const im = st.detail.image;
  const kept = st.detail.objects.filter(o => o.status !== 'unreviewed' || o.source !== 'auto' || o.edited).length;
  const drop = st.detail.objects.length - kept;
  if (drop && !confirm(`Re-run detection on this photo? ${drop} unreviewed, unedited detected objects will be replaced. ${kept} approved/rejected/edited/drawn objects are kept.`)) return;
  msg('Detecting…'); const p = await api('POST', S(`/image/${im.image_id}/process`), { whole_region: true });
  msg(p.status === 'processed' ? `Detection done: ${p.new_objects} new objects` : p.message || p.status, p.status !== 'processed');
  reload();
}

// ---------------------------------------------------------------- export
$('#btnExport').onclick = async () => {
  if (!st.session) return;
  try {
    const j = await api('POST', S('/export'));
    const href = S('/files/' + j.xlsx);
    $('#exportLink').innerHTML = `<a href="${href}">spheroid_measurements.xlsx</a> <span class="muted small">✓ ${j.n_objects} spheroid(s)</span>`;
    const a = document.createElement('a'); a.href = href; a.download = 'spheroid_measurements.xlsx'; document.body.appendChild(a); a.click(); a.remove();
    msg('Excel written.');
  } catch (e) { refreshSummary(); }
};

// ---------------------------------------------------------------- tools & mouse
function setTool(t) {
  st.tool = t; st.poly = []; st.drag = null; st.stroke = null;
  document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
  const H = {
    select: 'Hover an object to see its measurements; click to select. Drag empty space to pan, wheel to zoom.',
    scale: 'Drag a line along the scale bar, from one end to the other (Shift = horizontal). Drag an end of the line to adjust it.',
    brushAdd: 'Paint to ADD pixels to the selected object. Set brush radius (image px) in the toolbar.',
    brushErase: 'Paint to ERASE pixels from the selected object.',
    polyNew: 'Draw a NEW manual object. ' + SHAPE_HINT[drawShape()],
    polyAdd: 'Draw a shape to ADD to the selected object. ' + SHAPE_HINT[drawShape()],
    polyErase: 'Draw a shape to ERASE from the selected object. ' + SHAPE_HINT[drawShape()],
    assign: 'Orange = unassigned, purple = wall/shadow shape, white = cut line of a watershed split (assign it and the other piece to re-merge); with "removed pixels" on: pink = wall band, blue = thin, green = small. Yellow dashed = attached only by proximity. Click a part to add it to the selected object (click one of its own parts to remove it). No object selected, or Shift+click: the part becomes its own new object.',
  };
  $('#hint').textContent = st.step === 'scale' ? 'Drag an end of the orange line to adjust it. Wheel = zoom, drag = pan. “Draw line by hand”: ' + H.scale : H[t]; render();
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
    const o = await api('POST', S(`/image/${st.imageId}/objects`), { polygon: pts, shape, accept: true }); st.selected = o.object_id;
    msg(`Your object ${o.object_id} was added and approved (${shape}).`);
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
  if (st.step === 'scale' && st.scaleLine && e.button === 0) {
    const h = scaleHandleAt(p); if (h !== null) { st.drag = { kind: 'sline', i: h }; return; }
  }
  if (e.button === 1 || e.button === 2 || (st.tool === 'select' && !objectAt(...p)) || e.shiftKey && st.tool === 'select') {
    st.drag = { kind: 'pan', sx, sy, tx: st.view.tx, ty: st.view.ty }; return;
  }
  if (st.tool === 'select') { const o = objectAt(...p); if (o) select(o.object_id); return; }
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
  if (d && d.kind === 'line') { d.p1 = e.shiftKey ? [p[0], d.p0[1]] : p; render(); return; }
  if (d && d.kind === 'shape') { d.p1 = p; d.shift = e.shiftKey; d.pts.push(p); render(); return; }
  if (d && d.kind === 'sline') { st.scaleLine.line[d.i] = [p[0], p[1]]; render(); return; }
  if (st.stroke) { st.stroke.push(p); render(); return; }
  if (st.step === 'review' && (st.tool === 'select' || st.tool === 'assign')) {
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
  if (d.kind === 'sline') { st.scaleLine.source = 'manual'; renderScale(); return render(); }
  if (d.kind === 'shape') {
    const pts = shapePolygon(d);
    if (!pts) { render(); return msg('Shape too small: drag a larger outline.', true); }
    return submitDrawn(pts, d.shape);
  }
  if (d.kind === 'line') {
    if (Math.hypot(d.p1[0] - d.p0[0], d.p1[1] - d.p0[1]) < 2) return render();
    const prev = st.scaleLine || {};
    st.scaleLine = { line: [d.p0, d.p1], source: 'manual', known: prev.known ?? 500, unit: prev.unit || 'µm' };
    renderScale(); return render();
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
  if (st.step !== 'review' && st.step !== 'scale') return;
  const k = e.key.toLowerCase();
  if (st.step === 'scale') { if (k === '+' || k === '=') zoomAt(1.25); else if (k === '-') zoomAt(0.8); else if (k === 'f') fit(); return; }
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

// ---------------------------------------------------------------- start
setTool('select'); resize();
api('GET', '/api/config').then(c => { st.config = c; if (st.step === 'detect') renderDetect(); });
loadSessions().then(() => {
  const want = new URLSearchParams(location.search).get('session') || localStorage.getItem('lastSession');
  if (want && [...$('#sessionSel').options].some(o => o.value === want)) { $('#sessionSel').value = want; openSession(want); }
});

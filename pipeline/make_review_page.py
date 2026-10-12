# Usage: python pipeline/make_review_page.py [--cands results/candidates.json] [--out review.html]
# Builds a single-file review page from candidates.json: accept or reject the model's detections and draw missed
# spheroids. Images are loaded by relative path, so open the page from the project folder (e.g. `open review.html`).
# Export the result with the 'Download labels.json' button; it is the input for pipeline/finetune.py.
import argparse, json, os

ap = argparse.ArgumentParser()
ap.add_argument('--cands', default='results/candidates.json')
ap.add_argument('--out', default='review.html')
ap.add_argument('--focus', default='', help='image numbers to show first/only (1-based list order), e.g. 1,5,6,7,12,22')
ap.add_argument('--key', default='spheroid-review-v1', help='browser storage key; use a different one per review round')
a = ap.parse_args()

cands = json.load(open(a.cands))
items = []
for p, rec in cands.items():
    items.append(dict(
        path=p.replace(os.sep, '/'),
        name=os.path.basename(p),
        cands=[dict(id=c['id'], area=c['area_px'], parts=c['parts'],
                    poly=[[round(x), round(y)] for x, y in c['polygon']]) for c in rec],
    ))

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spheroid review</title>
<style>
:root{--bg:#14161a;--panel:#1d2026;--line:#2d323b;--fg:#e8eaee;--mut:#8b93a1;--acc:#4da3ff;--ok:#2ecc71;--bad:#ff5d5d;--pend:#ffd400}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;height:100vh;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap}
header h1{font-size:15px;margin:0 8px 0 0;font-weight:600}
button{background:#2a2f38;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 11px;font:inherit;cursor:pointer}
button:hover{border-color:var(--acc)} button.on{background:var(--acc);color:#06121f;border-color:var(--acc)}
button.ok{border-color:#1f7a45} button.bad{border-color:#8a3535}
.sp{flex:1}
.mut{color:var(--mut)}
main{flex:1;display:flex;min-height:0}
aside{width:300px;border-right:1px solid var(--line);overflow:auto;background:var(--panel)}
aside .it{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:8px;align-items:center}
aside .it:hover{background:#252a33} aside .it.cur{background:#26324a}
aside .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{font-size:12px;padding:1px 7px;border-radius:10px;background:#2a2f38;color:var(--mut)}
.badge.done{background:#17432b;color:#7ee2a8}
section{flex:1;display:flex;flex-direction:column;min-width:0}
.bar{display:flex;gap:8px;align-items:center;padding:8px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.wrap{flex:1;overflow:auto;padding:12px;background:#0d0e11}
canvas{display:block;background:#000;cursor:pointer}
canvas.add{cursor:crosshair}
.foot{padding:8px 14px;border-top:1px solid var(--line);display:flex;gap:8px;flex-wrap:wrap;align-items:center;min-height:42px}
.chip{background:#1b2a42;border:1px solid #2c4a77;border-radius:12px;padding:2px 4px 2px 10px;font-size:12px}
.chip b{cursor:pointer;color:#ff9a9a;padding:0 6px}
.help{font-size:12px;color:var(--mut)}
kbd{background:#2a2f38;border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px}
</style></head><body>
<header>
  <h1>Spheroid review</h1>
  <span id="prog" class="mut"></span><button id="flt"></button><span class="sp"></span>
  <button id="imp">Import…</button><input id="impf" type="file" accept=".json" hidden>
  <button id="exp" class="on">Download labels.json</button>
</header>
<main>
  <aside id="list"></aside>
  <section>
    <div class="bar">
      <button id="prev">← Previous</button><button id="next">Next →</button>
      <b id="title" style="margin:0 8px"></b><span class="sp"></span>
      <button id="accAll" class="ok">Accept pending <kbd>A</kbd></button>
      <button id="rejAll" class="bad">Reject pending <kbd>R</kbd></button>
      <button id="addBtn">Draw missed <kbd>N</kbd></button>
      <button id="emptyBtn">No spheroid</button>
      <button id="hideBtn">Accepted only <kbd>H</kbd></button>
      <button id="rst" class="bad">Reset</button>
      <button id="zout">−</button><span id="zv" class="mut">100%</span><button id="zin">+</button>
    </div>
    <div class="wrap" id="wrap"><canvas id="cv"></canvas></div>
    <div class="foot" id="foot"></div>
    <div class="foot help">
      Click: <span style="color:var(--pend)">yellow</span> → <span style="color:var(--ok)">accepted</span> → <span style="color:var(--bad)">rejected</span> → yellow.
      Draw missed: click points around the spheroid, close with <kbd>Enter</kbd> or by clicking the first point, <kbd>Backspace</kbd> removes the last point, <kbd>Esc</kbd> cancels.
      Navigate: <kbd>←</kbd> <kbd>→</kbd>, zoom <kbd>+</kbd> <kbd>−</kbd>. Your work is saved automatically in the browser.
    </div>
  </section>
</main>
<script>
const DATA = __DATA__;
const FOCUS = __FOCUS__;
const KEY = __KEY__;
let S = {}; try { S = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
const st = p => (S[p] = S[p] || {status: {}, added: [], empty: false});
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };
let cur = 0, zoom = 1, addMode = false, draft = [], imgEl = new Image(), hideRej = false, filt = FOCUS.length > 0;
let vis = filt ? FOCUS.slice() : DATA.map((_, i) => i);
const cv = document.getElementById('cv'), ctx = cv.getContext('2d'), wrap = document.getElementById('wrap');

function pending(it) { const s = st(it.path); return it.cands.filter(c => !s.status[c.id]).length; }
function isDone(it) { const s = st(it.path); return s.empty || (it.cands.length > 0 && pending(it) === 0) || (it.cands.length === 0 && s.added.length > 0); }

function buildList() {
  const L = document.getElementById('list'); L.innerHTML = '';
  vis.forEach(i => {
    const it = DATA[i];
    const d = document.createElement('div'); d.className = 'it' + (i === cur ? ' cur' : '');
    const s = st(it.path), rev = it.cands.length - pending(it);
    d.innerHTML = `<span class="mut">${i + 1}</span><span class="nm" title="${it.name}">${it.name}</span><span class="badge ${isDone(it) ? 'done' : ''}">${s.empty ? 'empty' : rev + '/' + it.cands.length}${s.added.length ? ' +' + s.added.length : ''}</span>`;
    d.onclick = () => go(i); L.appendChild(d);
  });
  const done = DATA.filter(isDone).length;
  document.getElementById('prog').textContent = `${done} / ${DATA.length} images done`;
}

function go(i) {
  cur = Math.max(0, Math.min(DATA.length - 1, i)); addMode = false; draft = []; syncBtns();
  const it = DATA[cur]; document.getElementById('title').textContent = `${cur + 1}/${DATA.length}  ${it.name}`;
  imgEl = new Image(); imgEl.onload = () => { cv.width = imgEl.naturalWidth; cv.height = imgEl.naturalHeight; applyZoom(); draw(); };
  imgEl.src = encodeURI(it.path); buildList(); foot();
}
function applyZoom() { const fw = wrap.clientWidth - 24, fh = (wrap.clientHeight - 24) * cv.width / cv.height; cv.style.width = Math.round(Math.min(fw, fh) * zoom) + 'px'; document.getElementById('zv').textContent = Math.round(zoom * 100) + '%'; }
function syncBtns() {
  document.getElementById('addBtn').classList.toggle('on', addMode); cv.classList.toggle('add', addMode);
  document.getElementById('emptyBtn').classList.toggle('on', st(DATA[cur].path).empty);
}
function path(poly) { ctx.beginPath(); poly.forEach((p, k) => k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); }
function draw() {
  const it = DATA[cur], s = st(it.path);
  ctx.clearRect(0, 0, cv.width, cv.height); ctx.drawImage(imgEl, 0, 0);
  ctx.lineWidth = 3; ctx.font = 'bold 26px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  it.cands.forEach(c => {
    if (hideRej && s.status[c.id] !== 'a') return;
    const v = s.status[c.id], col = v === 'a' ? '#2ecc71' : v === 'r' ? '#ff5d5d' : '#ffd400';
    path(c.poly); ctx.setLineDash(v === 'r' ? [10, 8] : []); ctx.strokeStyle = col; ctx.stroke();
    ctx.fillStyle = v === 'a' ? 'rgba(46,204,113,.18)' : v === 'r' ? 'rgba(255,93,93,.14)' : 'rgba(255,212,0,.06)'; ctx.fill();
    const m = cen(c.poly); ctx.setLineDash([]); ctx.fillStyle = 'rgba(0,0,0,.75)'; ctx.fillRect(m[0] - 17, m[1] - 15, 34, 30);
    ctx.fillStyle = col; ctx.fillText(c.id, m[0], m[1] + 1);
  });
  ctx.setLineDash([]);
  s.added.forEach(poly => { path(poly); ctx.strokeStyle = '#4da3ff'; ctx.stroke(); ctx.fillStyle = 'rgba(77,163,255,.18)'; ctx.fill(); });
  if (draft.length) {
    ctx.strokeStyle = '#4da3ff'; ctx.beginPath(); draft.forEach((p, k) => k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
    draft.forEach((p, k) => { ctx.beginPath(); ctx.arc(p[0], p[1], k ? 5 : 9, 0, 7); ctx.fillStyle = k ? '#4da3ff' : '#fff'; ctx.fill(); });
  }
}
function cen(poly) { let x = 0, y = 0; poly.forEach(p => { x += p[0]; y += p[1]; }); return [x / poly.length, y / poly.length]; }
function inside(pt, poly) {
  let r = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) r = !r;
  } return r;
}
function area(poly) { let a = 0; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) a += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]; return Math.abs(a / 2); }
function pos(e) { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; }

cv.addEventListener('click', e => {
  const pt = pos(e), it = DATA[cur], s = st(it.path);
  if (addMode) {
    if (draft.length > 2 && Math.hypot(pt[0] - draft[0][0], pt[1] - draft[0][1]) < 14 * cv.width / cv.getBoundingClientRect().width) return closeDraft();
    draft.push(pt.map(Math.round)); return draw();
  }
  const hit = it.cands.filter(c => !(hideRej && s.status[c.id] !== 'a') && inside(pt, c.poly)).sort((a, b) => area(a.poly) - area(b.poly))[0];
  if (!hit) return;
  const v = s.status[hit.id];
  if (!v) s.status[hit.id] = 'a'; else if (v === 'a') s.status[hit.id] = 'r'; else delete s.status[hit.id];
  s.empty = false; commit();
});
function closeDraft() { if (draft.length > 2) { st(DATA[cur].path).added.push(draft.slice()); st(DATA[cur].path).empty = false; } draft = []; addMode = false; syncBtns(); commit(); }
function commit() { save(); draw(); buildList(); foot(); syncBtns(); }
function foot() {
  const F = document.getElementById('foot'), s = st(DATA[cur].path); F.innerHTML = '';
  const a = Object.values(s.status).filter(v => v === 'a').length, r = Object.values(s.status).filter(v => v === 'r').length;
  F.insertAdjacentHTML('beforeend', `<span class="mut">${a} accepted · ${r} rejected · ${pending(DATA[cur])} pending · ${s.added.length} added</span>`);
  s.added.forEach((p, i) => { const c = document.createElement('span'); c.className = 'chip'; c.innerHTML = `added ${i + 1}<b title="delete">×</b>`; c.querySelector('b').onclick = () => { s.added.splice(i, 1); commit(); }; F.appendChild(c); });
}
document.getElementById('accAll').onclick = () => bulk('a'); document.getElementById('rejAll').onclick = () => bulk('r');
function bulk(v) { const it = DATA[cur], s = st(it.path); it.cands.forEach(c => { if (!s.status[c.id]) s.status[c.id] = v; }); s.empty = false; commit(); }
document.getElementById('addBtn').onclick = toggleAdd;
function toggleAdd() { addMode = !addMode; draft = []; syncBtns(); draw(); }
document.getElementById('emptyBtn').onclick = () => { const s = st(DATA[cur].path); s.empty = !s.empty; if (s.empty) { DATA[cur].cands.forEach(c => s.status[c.id] = 'r'); } else { s.status = {}; } commit(); };
function step(d) { const k = vis.indexOf(cur); go(vis[k < 0 ? 0 : Math.max(0, Math.min(vis.length - 1, k + d))]); }
document.getElementById('prev').onclick = () => step(-1); document.getElementById('next').onclick = () => step(1);
document.getElementById('zin').onclick = () => { zoom = Math.min(4, zoom + .25); applyZoom(); }; document.getElementById('zout').onclick = () => { zoom = Math.max(.5, zoom - .25); applyZoom(); };
addEventListener('resize', applyZoom);
addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') step(1); else if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'h' || e.key === 'H') toggleHide();
  else if (e.key === 'a' || e.key === 'A') bulk('a'); else if (e.key === 'r' || e.key === 'R') bulk('r');
  else if (e.key === 'n' || e.key === 'N') toggleAdd();
  else if (e.key === 'Enter' && addMode) closeDraft();
  else if (e.key === 'Escape') { draft = []; addMode = false; syncBtns(); draw(); }
  else if (e.key === 'Backspace' && addMode) { draft.pop(); draw(); e.preventDefault(); }
  else if (e.key === '+' || e.key === '=') document.getElementById('zin').click(); else if (e.key === '-') document.getElementById('zout').click();
});
document.getElementById('exp').onclick = () => {
  const out = DATA.map(it => { const s = st(it.path); return {
    path: it.path, name: it.name, reviewed: isDone(it), empty: s.empty,
    accepted: it.cands.filter(c => s.status[c.id] === 'a').map(c => ({id: c.id, polygon: c.poly})),
    rejected: it.cands.filter(c => s.status[c.id] === 'r').map(c => ({id: c.id, polygon: c.poly})),
    pending: it.cands.filter(c => !s.status[c.id]).map(c => c.id),
    added: s.added }; });
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify({version: 1, images: out}, null, 1)], {type: 'application/json'}));
  a.download = 'labels.json'; a.click();
};
document.getElementById('imp').onclick = () => document.getElementById('impf').click();
document.getElementById('impf').onchange = async e => {
  const f = e.target.files[0]; if (!f) return; const j = JSON.parse(await f.text());
  (j.images || []).forEach(o => { if (!DATA.some(d => d.path === o.path)) return; const s = st(o.path); s.status = {}; (o.accepted || []).forEach(c => s.status[c.id] = 'a'); (o.rejected || []).forEach(c => s.status[c.id] = 'r'); s.added = o.added || []; s.empty = !!o.empty; });
  save(); go(cur);
};
function toggleHide() { hideRej = !hideRej; document.getElementById('hideBtn').classList.toggle('on', hideRej); draw(); }
document.getElementById('hideBtn').onclick = toggleHide;
document.getElementById('rst').onclick = () => { if (!confirm('Delete all marks on this image (accepted, rejected, drawn)?')) return; S[DATA[cur].path] = {status: {}, added: [], empty: false}; commit(); };
function setFlt() {
  const b = document.getElementById('flt'); b.style.display = FOCUS.length ? '' : 'none';
  b.textContent = filt ? `Showing only selected images (${FOCUS.length}) · show all` : 'Show only the selected images';
  b.classList.toggle('on', filt);
}
document.getElementById('flt').onclick = () => { filt = !filt; vis = filt ? FOCUS.slice() : DATA.map((_, i) => i); setFlt(); if (vis.includes(cur)) buildList(); else go(vis[0]); };
setFlt(); go(vis[0] || 0);
</script></body></html>
'''
focus = [int(x) - 1 for x in a.focus.split(',') if x.strip()]
open(a.out, 'w', encoding='utf-8').write(HTML.replace('__DATA__', json.dumps(items, ensure_ascii=False)).replace('__FOCUS__', json.dumps(focus)).replace('__KEY__', json.dumps(a.key)))
print('written ->', a.out, f'({len(items)} images, {sum(len(i["cands"]) for i in items)} candidates)')

// Renderery wizualizacji (DOM/SVG). Każdy: create(host, ctx) → {update(step, data, {dur}), destroy()}.
// Elementy są kluczowane (id), więc zmiany między krokami animują się płynnie (CSS transitions albo tween w rAF).
import { gridToText } from './graphs.js';

const NS = 'http://www.w3.org/2000/svg';
const reduceMotion = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };

function S(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  for (const k of kids) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
function D(cls, text) { const e = document.createElement('div'); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function setText(e, t) { const s = t === null || t === undefined ? '' : String(t); if (e._t !== s) { e.textContent = s; e._t = s; } }
function setCls(e, c) { const f = e._enter ? c + ' av-enter' : c; if (e._c !== f) { e.setAttribute('class', f); e._c = f; } }
const ease = (k) => 1 - Math.pow(1 - k, 3);
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const lerp = (a, b, k) => a + (b - a) * k;

/** Prosty tween: animuje k ∈ [0,1] przez dur ms; anuluje poprzedni. */
function makeTween() {
  let raf = 0;
  return (dur, frame) => {
    cancelAnimationFrame(raf);
    if (!dur || dur < 16 || reduceMotion()) { frame(1); return; }
    const t0 = performance.now();
    const loop = (now) => {
      const k = Math.min(1, (now - t0) / dur);
      frame(ease(k));
      if (k < 1) raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  };
}

function arrowDefs(svg, id, tones) {
  const defs = S('defs');
  for (const t of tones) {
    const m = S('marker', { id: `${id}-${t}`, viewBox: '0 0 10 10', refX: 8.5, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse', markerUnits: 'userSpaceOnUse' });
    m.append(S('path', { d: 'M0,0 L10,5 L0,10 z', class: `av-arrowhead t-${t}` }));
    defs.append(m);
  }
  svg.append(defs);
}
let UID = 0;

// =========================================================================================== tablice
export function arraysRenderer(host) {
  const root = D('av-arr');
  const links = S('svg', { class: 'av-links' });
  const lid = 'avl' + (++UID);
  arrowDefs(links, lid, ['cmp', 'hit', 'minus', 'path', 'dep']);
  const linkG = S('g');
  links.append(linkG);
  root.append(links);
  host.append(root);
  const pool = new Map();
  function get(key, cls) {
    let e = pool.get(key);
    if (!e) {
      e = D(cls + ' av-enter');
      e._enter = true;
      root.append(e);
      pool.set(key, e);
      requestAnimationFrame(() => requestAnimationFrame(() => { e._enter = false; e.classList.remove('av-enter'); e._c = e.getAttribute('class'); }));
    }
    return e;
  }
  function update(step, data) {
    const rows = step.rows || [];
    const W = Math.max(300, root.clientWidth || host.clientWidth || 640);
    const labelW = rows.some((r) => r.label) ? 62 : 10;
    const slots = Math.max(1, ...rows.map((r) => (r.slots ?? r.items.length) + ((r.ptrs || []).some((p) => p.s >= (r.slots ?? r.items.length)) ? 1 : 0)));
    const cw = clamp(Math.floor((W - labelW - 14) / slots), 20, 62);
    const gap = cw >= 36 ? 6 : cw >= 26 ? 4 : 3;
    const w = cw - gap;
    const x0 = labelW + Math.max(0, Math.floor((W - labelW - 14 - slots * cw) / 2));
    const sx = (s) => x0 + s * cw;
    const seen = new Set();
    const geo = {};
    let y = 8;
    for (const row of rows) {
      if (row.gap) y += 8;
      const kind = row.kind || 'cells';
      const spans = row.spans || [];
      if (spans.some((s) => s.pos === 'over')) y += 18;
      const rh = kind === 'bars' ? (row.h || 200) : Math.max(26, Math.min(w, 46));
      const top = y;
      geo[row.key] = { top, rh };
      const lab = get('L:' + row.key, 'av-rowlabel');
      seen.add('L:' + row.key);
      setText(lab, row.label || '');
      lab.style.transform = `translate(4px, ${top + (kind === 'bars' ? rh - 24 : rh / 2 - 10)}px)`;
      spans.forEach((sp, k) => {
        const key = `S:${row.key}:${k}`;
        const e = get(key, 'av-span');
        seen.add(key);
        setCls(e, `av-span t-${sp.t} pos-${sp.pos || 'bg'}`);
        const left = sx(sp.from) - gap / 2, width = Math.max(4, (sp.to - sp.from + 1) * cw);
        const pos = sp.pos || 'bg';
        const ty = pos === 'over' ? top - 15 : pos === 'under' ? top + rh + (row.idx ? 18 : 4) : top - 5;
        e.style.width = width + 'px';
        e.style.height = (pos === 'bg' ? rh + 10 : 7) + 'px';
        e.style.transform = `translate(${left}px, ${ty}px)`;
        setText(e, sp.label || '');
      });
      const maxV = kind === 'bars' ? Math.max(1, data.maxV || 0, ...row.items.map((it) => Math.abs(+it.v) || 0)) : 1;
      for (const it of row.items) {
        const key = 'I:' + it.id;
        const e = get(key, 'av-item');
        seen.add(key);
        setCls(e, `av-item av-${kind}${it.t ? ' t-' + it.t : ''}${w < 26 ? ' tight' : ''}`);
        const x = sx(it.s);
        if (kind === 'bars') {
          const hh = Math.max(8, Math.round((Math.abs(+it.v) / maxV) * (rh - 20)));
          e.style.width = w + 'px';
          e.style.height = hh + 'px';
          e.style.transform = `translate(${x}px, ${top + rh - hh}px)`;
          setText(e, w >= 16 ? it.v : '');
        } else {
          e.style.width = w + 'px';
          e.style.height = rh + 'px';
          e.style.transform = `translate(${x}px, ${top}px)`;
          const txt = it.sub ? `${it.v}\n${it.sub}` : it.v;
          if (e._t !== String(txt)) {
            e._t = String(txt);
            e.textContent = '';
            e.append(D('v', String(it.v)));
            if (it.sub) e.append(D('sub', it.sub));
          }
        }
      }
      y += rh;
      if (row.idx) {
        const n = row.slots ?? row.items.length;
        for (let s = 0; s < n; s++) {
          const key = `X:${row.key}:${s}`;
          const e = get(key, 'av-idx');
          seen.add(key);
          setText(e, s + (row.idxFrom || 0));
          e.style.width = w + 'px';
          e.style.transform = `translate(${sx(s)}px, ${y + 2}px)`;
        }
        y += 17;
      }
      if (spans.some((s) => s.pos === 'under')) y += 16;
      const ptrs = row.ptrs || [];
      if (ptrs.length) {
        const stack = {};
        let maxStack = 1;
        for (const p of ptrs) {
          const k = (stack[p.s] = (stack[p.s] ?? -1) + 1);
          maxStack = Math.max(maxStack, k + 1);
          const key = `P:${row.key}:${p.name}`;
          const e = get(key, 'av-ptr');
          seen.add(key);
          setCls(e, `av-ptr t-${p.t || 'act'}`);
          setText(e, p.name);
          e.style.width = Math.max(w, 26) + 'px';
          e.style.transform = `translate(${sx(p.s) - (Math.max(w, 26) - w) / 2}px, ${y + 2 + k * 18}px)`;
        }
        y += 18 * maxStack + 6;
      }
      y += 14;
    }
    root.style.height = Math.max(120, y) + 'px';
    for (const [k, e] of pool) {
      if (seen.has(k)) continue;
      pool.delete(k);
      e.classList.add('av-leave');
      setTimeout(() => e.remove(), 450);
    }
    // strzałki zależności
    linkG.textContent = '';
    links.setAttribute('width', W);
    links.setAttribute('height', Math.max(120, y));
    for (const l of step.links || []) {
      const [ra, sa] = l.from, [rb, sb] = l.to;
      const A = geo[ra], B = geo[rb];
      if (!A || !B) continue;
      const xa = sx(sa) + w / 2, xb = sx(sb) + w / 2;
      let d;
      if (ra === rb) {
        const yy = A.top - 2;
        const lift = 18 + Math.min(46, Math.abs(xb - xa) * 0.22);
        d = `M${xa},${yy} C${xa},${yy - lift} ${xb},${yy - lift} ${xb},${yy - 4}`;
      } else {
        const down = B.top > A.top;
        const ya = down ? A.top + A.rh + 1 : A.top - 1, yb = down ? B.top - 4 : B.top + B.rh + 4;
        const my = (ya + yb) / 2;
        d = `M${xa},${ya} C${xa},${my} ${xb},${my} ${xb},${yb}`;
      }
      linkG.append(S('path', { d, class: `av-link t-${l.t || 'cmp'}`, 'marker-end': `url(#${lid}-${['cmp', 'hit', 'minus', 'path', 'dep'].includes(l.t) ? l.t : 'cmp'})` }));
    }
  }
  return { update, destroy() { root.remove(); } };
}

// =========================================================================================== siatka
const GRID_TONES = ['', 't-frontier', 't-visited', 't-current', 't-path', 't-dead', 't-stack'];

export function gridRenderer(host, ctx) {
  const wrap = D('av-gridwrap');
  const grid = D('av-grid');
  const hint = D('av-gridhint', 'Kliknij i przeciągnij, aby rysować lub zmazywać ściany. Przeciągnij S lub T, aby przenieść start i metę.');
  wrap.append(grid, hint);
  host.append(wrap);
  let cells = [], key = null, g = null, cs = 24;
  let drag = null;
  function build(data) {
    g = { R: data.R, C: data.C, walls: data.walls.slice(), s: data.s, t: data.t };
    grid.textContent = '';
    cells = [];
    const W = Math.max(300, host.clientWidth || 640) - 8;
    cs = clamp(Math.floor(W / g.C), 13, 36);
    grid.style.gridTemplateColumns = `repeat(${g.C}, ${cs}px)`;
    grid.style.setProperty('--cs', cs + 'px');
    for (let i = 0; i < g.R * g.C; i++) {
      const c = D('av-cell');
      c.dataset.i = i;
      grid.append(c);
      cells.push(c);
    }
    key = data;
  }
  function paintStatic() {
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      c.classList.toggle('wall', !!g.walls[i]);
      c.classList.toggle('is-s', i === g.s);
      c.classList.toggle('is-t', i === g.t);
    }
  }
  function update(step, data) {
    if (key !== data) build(data);
    paintStatic();
    const codes = step.cells || '';
    const dist = step.dist;
    const showNum = cs >= 22;
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      const code = +codes[i] || 0;
      const cls = 'av-cell' + (g.walls[i] ? ' wall' : '') + (i === g.s ? ' is-s' : '') + (i === g.t ? ' is-t' : '') + (code ? ' ' + GRID_TONES[code] : '');
      setCls(c, cls);
      const label = i === g.s ? 'S' : i === g.t ? 'T' : (showNum && dist && dist[i] >= 0 && !g.walls[i] ? dist[i] : '');
      setText(c, label);
    }
  }
  const cellAt = (ev) => {
    const r = grid.getBoundingClientRect();
    const c = Math.floor((ev.clientX - r.left) / (cs + 1)), rr = Math.floor((ev.clientY - r.top) / (cs + 1));
    if (c < 0 || rr < 0 || c >= g.C || rr >= g.R) return -1;
    return rr * g.C + c;
  };
  grid.addEventListener('pointerdown', (ev) => {
    if (!g || ev.button !== 0) return;
    const i = cellAt(ev);
    if (i < 0) return;
    ev.preventDefault();
    grid.setPointerCapture(ev.pointerId);
    if (i === g.s) drag = { mode: 's' };
    else if (i === g.t) drag = { mode: 't' };
    else { drag = { mode: 'paint', val: g.walls[i] ? 0 : 1 }; g.walls[i] = drag.val; }
    drag.dirty = drag.mode === 'paint';
    grid.classList.add('editing');
    for (const c of cells) setCls(c, (c._c || 'av-cell').replace(/ t-\w+/g, ''));
    paintStatic();
  });
  grid.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    const i = cellAt(ev);
    if (i < 0) return;
    if (drag.mode === 'paint') { if (i !== g.s && i !== g.t && g.walls[i] !== drag.val) { g.walls[i] = drag.val; drag.dirty = true; } }
    else if (!g.walls[i] && i !== g.s && i !== g.t) { if (drag.mode === 's') g.s = i; else g.t = i; drag.dirty = true; }
    for (let k = 0; k < cells.length; k++) {
      setText(cells[k], k === g.s ? 'S' : k === g.t ? 'T' : '');
      cells[k].className = 'av-cell' + (g.walls[k] ? ' wall' : '') + (k === g.s ? ' is-s' : '') + (k === g.t ? ' is-t' : '');
      cells[k]._c = cells[k].className;
    }
  });
  const end = () => {
    if (!drag) return;
    const dirty = drag.dirty;
    drag = null;
    grid.classList.remove('editing');
    if (dirty && ctx.onGridEdit) ctx.onGridEdit(gridToText(g));
  };
  grid.addEventListener('pointerup', end);
  grid.addEventListener('pointercancel', end);
  return { update, destroy() { wrap.remove(); }, rebuild() { key = null; } };
}

// =========================================================================================== graf
export function graphRenderer(host) {
  const svg = S('svg', { class: 'av-graph' });
  const gid = 'avg' + (++UID);
  const TONES = ['def', 'cmp', 'tree', 'back', 'mst', 'dim', 'rejected'];
  arrowDefs(svg, gid, TONES);
  const gE = S('g'), gL = S('g'), gN = S('g');
  svg.append(gE, gL, gN);
  host.append(svg);
  let data = null, nodes = [], edges = [], W = 600, H = 380, R = 19, lastStep = null;
  function px(v) { const p = data.pos[v] || [0.5, 0.5]; return [28 + p[0] * (W - 56), 28 + p[1] * (H - 56)]; }
  function build(d) {
    data = d;
    gE.textContent = ''; gL.textContent = ''; gN.textContent = '';
    R = d.n > 12 ? 16 : 19;
    edges = d.edges.map((e, id) => {
      const path = S('path', { class: 'av-gedge' });
      const label = d.weighted ? S('text', { class: 'av-gw' }, String(e.w)) : null;
      gE.append(path);
      if (label) gL.append(label);
      const rev = d.directed && d.edges.some((f) => f.u === e.v && f.v === e.u);
      return { e, id, path, label, rev };
    });
    nodes = [null];
    for (let v = 1; v <= d.n; v++) {
      const g = S('g', { class: 'av-gnode' });
      const c = S('circle', { r: R });
      const t = S('text', { class: 'av-gid', dy: '0.35em' }, String(v));
      const sub = S('text', { class: 'av-gsub', dy: R + 15 });
      g.append(c, t, sub);
      gN.append(g);
      nodes.push({ g, c, t, sub });
      drag(g, v);
    }
    layout();
  }
  function layout() {
    W = Math.max(320, host.clientWidth || 640);
    H = clamp(Math.round(W * 0.56), 300, 460);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    for (let v = 1; v <= data.n; v++) { const [x, y] = px(v); nodes[v].g.setAttribute('transform', `translate(${x},${y})`); }
    for (const E of edges) {
      const [x1, y1] = px(E.e.u), [x2, y2] = px(E.e.v);
      const dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy) || 1;
      const ux = dx / L, uy = dy / L;
      let d, mx, my;
      if (E.e.u === E.e.v) {
        d = `M${x1 - 8},${y1 - R + 2} C${x1 - 30},${y1 - R - 40} ${x1 + 30},${y1 - R - 40} ${x1 + 8},${y1 - R + 2}`;
        mx = x1; my = y1 - R - 30;
      } else if (E.rev) {
        const off = 16, nx = -uy * off, ny = ux * off;
        const cx = (x1 + x2) / 2 + nx, cy = (y1 + y2) / 2 + ny;
        const sx1 = x1 + ux * R, sy1 = y1 + uy * R, ex = x2 - ux * (R + 4), ey = y2 - uy * (R + 4);
        d = `M${sx1 + nx * 0.3},${sy1 + ny * 0.3} Q${cx},${cy} ${ex + nx * 0.3},${ey + ny * 0.3}`;
        mx = cx; my = cy;
      } else {
        const tail = data.directed ? R + 4 : R;
        d = `M${x1 + ux * R},${y1 + uy * R} L${x2 - ux * tail},${y2 - uy * tail}`;
        mx = (x1 + x2) / 2 - uy * 10; my = (y1 + y2) / 2 + ux * 10;
      }
      E.path.setAttribute('d', d);
      if (E.label) { E.label.setAttribute('x', mx); E.label.setAttribute('y', my + 4); }
    }
  }
  function drag(g, v) {
    g.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      g.setPointerCapture(ev.pointerId);
      g.classList.add('dragging');
      const move = (e2) => {
        const r = svg.getBoundingClientRect();
        const x = ((e2.clientX - r.left) / r.width) * W, y = ((e2.clientY - r.top) / r.height) * H;
        data.pos[v] = [clamp((x - 28) / (W - 56), 0, 1), clamp((y - 28) / (H - 56), 0, 1)];
        layout();
      };
      const up = () => { g.classList.remove('dragging'); g.removeEventListener('pointermove', move); g.removeEventListener('pointerup', up); };
      g.addEventListener('pointermove', move);
      g.addEventListener('pointerup', up);
    });
  }
  function update(step, d) {
    if (data !== d) build(d);
    else if (Math.abs((host.clientWidth || 640) - W) > 4) layout();
    lastStep = step;
    for (let v = 1; v <= data.n; v++) {
      const N = nodes[v];
      const tone = (step.ns && step.ns[v]) || '';
      const comp = step.comp && step.comp[v] >= 0 ? ` comp c${step.comp[v] % 8}` : '';
      setCls(N.g, `av-gnode${tone ? ' t-' + tone : ''}${comp}${step.vars && (step.vars.v === v || step.vars.u === v) ? ' focus' : ''}`);
      setText(N.sub, step.nl ? step.nl[v] : '');
    }
    for (const E of edges) {
      const tone = (step.es && step.es[E.id]) || '';
      setCls(E.path, `av-gedge${tone ? ' t-' + tone : ''}`);
      if (data.directed) E.path.setAttribute('marker-end', `url(#${gid}-${TONES.includes(tone) ? tone : 'def'})`);
      if (E.label) setCls(E.label, `av-gw${tone ? ' t-' + tone : ''}`);
    }
  }
  return { update, destroy() { svg.remove(); }, resize() { if (data) layout(); } };
}

// =========================================================================================== las DSU
export function forestRenderer(host) {
  const svg = S('svg', { class: 'av-forest' });
  const fid = 'avf' + (++UID);
  arrowDefs(svg, fid, ['def', 'path', 'new']);
  const gE = S('g'), gN = S('g'), gA = S('g');
  svg.append(gE, gN, gA);
  host.append(svg);
  const tween = makeTween();
  let n = 0, nodes = [], edges = [], cells = [], cur = [], key = null, W = 600, H = 360;
  function build(d) {
    n = d.n; key = d;
    gE.textContent = ''; gN.textContent = ''; gA.textContent = '';
    nodes = [null]; edges = [null]; cells = [null]; cur = [null];
    for (let v = 1; v <= n; v++) {
      const line = S('line', { class: 'av-fedge', 'marker-end': `url(#${fid}-def)` });
      gE.append(line);
      edges.push(line);
      const g = S('g', { class: 'av-gnode' });
      g.append(S('circle', { r: 17 }), S('text', { class: 'av-gid', dy: '0.35em' }, String(v)));
      gN.append(g);
      nodes.push(g);
      const cg = S('g', { class: 'av-pcell' });
      const rect = S('rect', { rx: 6, width: 34, height: 30 });
      const val = S('text', { class: 'av-pval', x: 17, y: 20 });
      const idx = S('text', { class: 'av-pidx', x: 17, y: 44 }, String(v));
      cg.append(rect, val, idx);
      gA.append(cg);
      cells.push({ g: cg, val });
      cur.push(null);
    }
  }
  function layoutOf(parent) {
    const kids = Array.from({ length: n + 1 }, () => []);
    const roots = [];
    for (let v = 1; v <= n; v++) (parent[v] === v ? roots : kids[parent[v]]).push(v);
    const pos = [];
    let slot = 0, maxD = 0;
    const place = (v, d) => {
      maxD = Math.max(maxD, d);
      if (!kids[v].length) { pos[v] = [slot++, d]; return; }
      kids[v].forEach((c) => place(c, d + 1));
      pos[v] = [(pos[kids[v][0]][0] + pos[kids[v][kids[v].length - 1]][0]) / 2, d];
    };
    roots.forEach((r, i) => { if (i) slot += 0.45; place(r, 0); });
    return { pos, slots: Math.max(1, slot), maxD };
  }
  function update(step, d) {
    if (key !== d) build(d);
    W = Math.max(320, host.clientWidth || 640);
    const { pos, slots, maxD } = layoutOf(step.parent);
    const dx = clamp((W - 60) / slots, 34, 90), dy = 62;
    const treeH = 40 + (maxD + 1) * dy;
    H = treeH + 70;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    const offX = (W - (slots - 1) * dx) / 2;
    const target = [null];
    for (let v = 1; v <= n; v++) target.push([offX + pos[v][0] * dx, 34 + pos[v][1] * dy]);
    const from = cur.map((p, v) => p || target[v]);
    for (let v = 1; v <= n; v++) {
      const tone = (step.ns && step.ns[v]) || '';
      setCls(nodes[v], `av-gnode${tone ? ' t-' + tone : ''}`);
      const isRoot = step.parent[v] === v;
      setCls(edges[v], `av-fedge${tone === 'path' || tone === 'new' ? ' t-' + tone : ''}${isRoot ? ' hidden' : ''}`);
      edges[v].setAttribute('marker-end', `url(#${fid}-${tone === 'path' || tone === 'new' ? tone : 'def'})`);
    }
    // tablica p[]
    const cw = clamp((W - 40) / n, 30, 46);
    const ax = (W - n * cw) / 2;
    for (let v = 1; v <= n; v++) {
      const c = cells[v];
      c.g.setAttribute('transform', `translate(${ax + (v - 1) * cw + (cw - 34) / 2},${treeH + 8})`);
      setText(c.val, step.parent[v]);
      const tone = (step.ns && step.ns[v]) || '';
      setCls(c.g, `av-pcell${tone ? ' t-' + tone : ''}${step.parent[v] === v ? ' root' : ''}`);
    }
    tween(dur(step), (k) => {
      for (let v = 1; v <= n; v++) {
        const x = lerp(from[v][0], target[v][0], k), y = lerp(from[v][1], target[v][1], k);
        cur[v] = [x, y];
        nodes[v].setAttribute('transform', `translate(${x},${y})`);
      }
      for (let v = 1; v <= n; v++) {
        const p = step.parent[v];
        if (p === v) continue;
        const [x1, y1] = cur[v], [x2, y2] = cur[p];
        const L = Math.hypot(x2 - x1, y2 - y1) || 1;
        const ux = (x2 - x1) / L, uy = (y2 - y1) / L;
        const e = edges[v];
        e.setAttribute('x1', x1 + ux * 17); e.setAttribute('y1', y1 + uy * 17);
        e.setAttribute('x2', x2 - ux * 21); e.setAttribute('y2', y2 - uy * 21);
      }
    });
  }
  let dur = () => 300;
  return { update(step, d, opts = {}) { dur = () => opts.dur ?? 300; update(step, d); }, destroy() { svg.remove(); } };
}

// =========================================================================================== drzewo przedziałowe
export function segtreeRenderer(host) {
  const svg = S('svg', { class: 'av-seg' });
  const gE = S('g'), gN = S('g'), gA = S('g'), gR = S('g');
  svg.append(gR, gE, gN, gA);
  host.append(svg);
  let key = null, n = 0, nodes = new Map(), cells = [], band = null, W = 600, H = 400;
  function build(d) {
    key = d; n = d.n;
    gE.textContent = ''; gN.textContent = ''; gA.textContent = ''; gR.textContent = '';
    nodes = new Map(); cells = [];
    W = Math.max(340, host.clientWidth || 640);
    const maxD = Math.max(...d.nodes.map((x) => x.d));
    const pad = 16, cw = (W - 2 * pad) / n, lh = clamp(64, 50, 70);
    H = 30 + (maxD + 1) * lh + 76;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    const pos = new Map();
    for (const nd of d.nodes) pos.set(nd.v, [pad + ((nd.l + nd.r + 1) / 2) * cw, 26 + nd.d * lh]);
    for (const nd of d.nodes) {
      if (nd.v > 1) {
        const [x1, y1] = pos.get(nd.v >> 1), [x2, y2] = pos.get(nd.v);
        const e = S('line', { x1, y1: y1 + 15, x2, y2: y2 - 15, class: 'av-segedge' });
        gE.append(e);
        nd.edge = e;
      }
      const [x, y] = pos.get(nd.v);
      const bw = clamp(cw * (nd.l === nd.r ? 0.86 : 0.8), 30, 64);
      const g = S('g', { class: 'av-segnode', transform: `translate(${x},${y})` });
      const rect = S('rect', { x: -bw / 2, y: -15, width: bw, height: 30, rx: 8 });
      const val = S('text', { class: 'av-segval', dy: '0.35em' });
      const lab = S('text', { class: 'av-seglab', y: -20 }, cw * (nd.r - nd.l + 1) > 44 || nd.l === nd.r ? (nd.l === nd.r ? `${nd.l}` : `[${nd.l},${nd.r}]`) : '');
      g.append(rect, val, lab);
      g.append(S('title', {}, `węzeł ${nd.v}: a[${nd.l}…${nd.r}]`));
      gN.append(g);
      nodes.set(nd.v, { g, val, edge: nd.edge });
    }
    const ay = 26 + (maxD + 1) * lh + 6;
    band = S('rect', { class: 'av-segband', y: ay - 6, height: 52, rx: 10, x: 0, width: 0 });
    gR.append(band);
    for (let i = 0; i < n; i++) {
      const g = S('g', { class: 'av-acell', transform: `translate(${pad + i * cw + 2},${ay})` });
      const w = Math.max(18, cw - 4);
      g.append(S('rect', { width: w, height: 30, rx: 7 }), S('text', { class: 'av-aval', x: w / 2, y: 20 }), S('text', { class: 'av-aidx', x: w / 2, y: 44 }, String(i)));
      gA.append(g);
      cells.push(g);
    }
    band._geo = { pad, cw };
  }
  function update(step, d) {
    if (key !== d || Math.abs((host.clientWidth || 640) - W) > 30) build(d);
    for (const [v, N] of nodes) {
      const tone = step.ns[v] || '';
      const val = step.t[v];
      setCls(N.g, `av-segnode${tone ? ' t-' + tone : ''}${val === null ? ' empty' : ''}${step.vars && step.vars.v === v ? ' focus' : ''}`);
      setText(N.val, val === null ? '·' : val);
      if (N.edge) setCls(N.edge, `av-segedge${tone && tone !== 'miss' ? ' t-' + tone : ''}`);
    }
    cells.forEach((g, i) => {
      setCls(g, `av-acell${step.na[i] ? ' t-' + step.na[i] : ''}`);
      setText(g.childNodes[1], step.a[i]);
    });
    const { pad, cw } = band._geo;
    if (step.range) {
      band.setAttribute('x', pad + step.range[0] * cw - 2);
      band.setAttribute('width', (step.range[1] - step.range[0] + 1) * cw + 4);
      band.classList.add('on');
    } else band.classList.remove('on');
  }
  return { update, destroy() { svg.remove(); } };
}

// =========================================================================================== Fenwick
export function fenwickRenderer(host) {
  const svg = S('svg', { class: 'av-fen' });
  host.append(svg);
  let key = null, n = 0, W = 600, cols = [], bars = [], acells = [], colBand = null, geo = null;
  function build(d) {
    key = d; n = d.n;
    svg.textContent = '';
    W = Math.max(360, host.clientWidth || 640);
    const pad = 46, cw = (W - pad - 12) / n;
    const levels = Math.floor(Math.log2(n)) + 1;
    const top = 8, rowA = top + 44, barTop = rowA + 48, bh = 30, lg = 6;
    const H = barTop + levels * (bh + lg) + 10;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    colBand = S('rect', { class: 'av-fencol', y: top, height: H - top - 4, width: cw - 2, rx: 8 });
    svg.append(colBand);
    svg.append(S('text', { class: 'av-fenlab', x: 4, y: rowA + 20 }, 'a'), S('text', { class: 'av-fenlab', x: 4, y: barTop + 20 }, 't'));
    cols = []; bars = []; acells = [];
    for (let i = 1; i <= n; i++) {
      const x = pad + (i - 1) * cw;
      const lb = i & -i;
      const b = i.toString(2).padStart(Math.floor(Math.log2(n)) + 1, '0');
      const low = b.lastIndexOf('1');
      const idx = S('text', { class: 'av-fenidx', x: x + cw / 2, y: top + 14 }, String(i));
      const bt = S('text', { class: 'av-fenbin', x: x + cw / 2, y: top + 30 });
      bt.append(S('tspan', {}, b.slice(0, low)), S('tspan', { class: 'lowbit' }, b[low]), S('tspan', {}, b.slice(low + 1)));
      svg.append(idx, bt);
      const ag = S('g', { class: 'av-acell', transform: `translate(${x + 2},${rowA})` });
      ag.append(S('rect', { width: cw - 4, height: 30, rx: 7 }), S('text', { class: 'av-aval', x: (cw - 4) / 2, y: 20 }));
      svg.append(ag);
      acells.push(ag);
      const k = Math.log2(lb);
      const bx = pad + (i - lb) * cw + 2, bw = lb * cw - 4;
      const g = S('g', { class: 'av-fenbar', transform: `translate(${bx},${barTop + k * (bh + lg)})` });
      g.append(S('rect', { width: bw, height: bh, rx: 8 }), S('text', { class: 'av-fenval', x: bw - 8, y: 20 }), S('text', { class: 'av-fenname', x: 8, y: 20 }, bw > 70 ? `t[${i}]` : ''));
      g.append(S('title', {}, `t[${i}] = suma a[${i - lb + 1}…${i}] (lowbit = ${lb})`));
      svg.append(g);
      bars.push(g);
      cols.push(x);
    }
    geo = { pad, cw };
  }
  function update(step, d) {
    if (key !== d || Math.abs((host.clientWidth || 640) - W) > 30) build(d);
    for (let i = 1; i <= n; i++) {
      const g = bars[i - 1];
      setCls(g, `av-fenbar${step.ns[i] ? ' t-' + step.ns[i] : ''}${step.cur === i ? ' focus' : ''}`);
      setText(g.childNodes[1], step.t[i]);
      const a = acells[i - 1];
      setCls(a, `av-acell${step.na[i] ? ' t-' + step.na[i] : ''}`);
      setText(a.childNodes[1], step.a[i]);
    }
    if (step.cur) { colBand.setAttribute('x', geo.pad + (step.cur - 1) * geo.cw + 1); colBand.classList.add('on'); }
    else colBand.classList.remove('on');
  }
  return { update, destroy() { svg.remove(); } };
}

// =========================================================================================== tabela DP
export function tableRenderer(host) {
  const wrap = D('av-tablewrap');
  const box = D('av-table');
  const svg = S('svg', { class: 'av-tlinks' });
  const tid = 'avt' + (++UID);
  arrowDefs(svg, tid, ['dep', 'win']);
  const gl = S('g');
  svg.append(gl);
  box.append(svg);
  wrap.append(box);
  host.append(wrap);
  let key = null, cells = [], geo = null;
  function build(d) {
    key = d;
    box.querySelectorAll('.av-tc, .av-th').forEach((e) => e.remove());
    const R = d.rows.length, C = d.cols.length;
    const W = Math.max(320, host.clientWidth || 640);
    const headW = clamp(Math.max(...d.rows.map((r) => r.length)) * 7.4 + 18, 34, 128);
    const cs = clamp(Math.floor((W - headW - 12) / C), 28, 52);
    geo = { headW, cs, R, C };
    const corner = D('av-th corner', d.corner || '');
    corner.style.cssText = `left:0;top:0;width:${headW}px;height:${cs}px`;
    box.append(corner);
    d.cols.forEach((c, j) => { const e = D('av-th col', c); e.style.cssText = `left:${headW + j * cs}px;top:0;width:${cs}px;height:${cs}px`; box.append(e); });
    d.rows.forEach((r, i) => { const e = D('av-th row', r); e.style.cssText = `left:0;top:${(i + 1) * cs}px;width:${headW}px;height:${cs}px`; box.append(e); });
    cells = [];
    for (let i = 0; i < R; i++) {
      const row = [];
      for (let j = 0; j < C; j++) {
        const e = D('av-tc');
        e.style.cssText = `left:${headW + j * cs}px;top:${(i + 1) * cs}px;width:${cs - 3}px;height:${cs - 3}px`;
        box.append(e);
        row.push(e);
      }
      cells.push(row);
    }
    const tw = headW + C * cs, th = (R + 1) * cs;
    box.style.width = tw + 'px';
    box.style.height = th + 'px';
    svg.setAttribute('width', tw);
    svg.setAttribute('height', th);
  }
  function update(step, d) {
    if (key !== d) build(d);
    const tones = new Map();
    for (const [r, c, t] of step.marks || []) tones.set(r + ',' + c, t);
    for (const [r, c, t] of step.deps || []) tones.set(r + ',' + c, t);
    if (step.cur) tones.set(step.cur.join(','), 'cmp');
    for (let i = 0; i < geo.R; i++) for (let j = 0; j < geo.C; j++) {
      const e = cells[i][j];
      const v = step.g[i][j];
      const t = tones.get(i + ',' + j);
      setCls(e, `av-tc${v === null ? ' empty' : ''}${t ? ' t-' + t : ''}${step.cur && step.cur[0] === i && step.cur[1] === j ? ' focus' : ''}`);
      setText(e, v === null ? '' : v);
    }
    gl.textContent = '';
    if (step.cur) {
      const { headW, cs } = geo;
      const cx = (j) => headW + j * cs + (cs - 3) / 2, cy = (i) => (i + 1) * cs + (cs - 3) / 2;
      for (const [r, c, t] of step.deps || []) {
        const x1 = cx(c), y1 = cy(r), x2 = cx(step.cur[1]), y2 = cy(step.cur[0]);
        const L = Math.hypot(x2 - x1, y2 - y1) || 1;
        const ux = (x2 - x1) / L, uy = (y2 - y1) / L;
        const bend = r !== step.cur[0] && c !== step.cur[1] ? 0 : 0.18;
        const mx = (x1 + x2) / 2 - uy * L * bend, my = (y1 + y2) / 2 + ux * L * bend;
        gl.append(S('path', { d: `M${x1 + ux * 8},${y1 + uy * 8} Q${mx},${my} ${x2 - ux * 13},${y2 - uy * 13}`, class: `av-link t-${t}`, 'marker-end': `url(#${tid}-${t === 'dep' ? 'dep' : 'win'})` }));
      }
    }
  }
  return { update, destroy() { wrap.remove(); } };
}

// =========================================================================================== sito
export function sieveRenderer(host) {
  const grid = D('av-sieve');
  host.append(grid);
  let key = null, cells = [];
  const SMALL = [2, 3, 5, 7, 11, 13, 17, 19];
  function build(d) {
    key = d;
    grid.textContent = '';
    grid.style.gridTemplateColumns = `repeat(${d.cols}, minmax(0, 1fr))`;
    cells = [null];
    for (let x = 1; x <= d.N; x++) { const c = D('av-sv', String(x)); grid.append(c); cells.push(c); }
  }
  function update(step, d) {
    if (key !== d) build(d);
    const st = step.st.split(',').map(Number);
    for (let x = 1; x <= d.N; x++) {
      const c = cells[x];
      const code = st[x];
      const k = SMALL.indexOf(step.by[x]);
      const cls = code === 9 ? 'na' : code === 1 ? 't-done prime' : code === 3 ? 't-act prime' : code === 4 ? 't-new crossed' : code === 2 ? `crossed c${k >= 0 ? k : 7}` : '';
      setCls(c, `av-sv ${cls}${step.vars && step.vars.k === x ? ' focus' : ''}`);
    }
  }
  return { update, destroy() { grid.remove(); } };
}

// =========================================================================================== drzewo wywołań (+ paliki Hanoi)
export function calltreeRenderer(host) {
  const wrap = D('av-ctwrap');
  const pegsBox = D('av-pegs');
  const scroller = D('av-ctscroll');
  const svg = S('svg', { class: 'av-ct' });
  const gE = S('g'), gN = S('g');
  svg.append(gE, gN);
  scroller.append(svg);
  wrap.append(pegsBox, scroller);
  host.append(wrap);
  const tween = makeTween();
  let pool = new Map(), cur = new Map(), key = null, disks = [], pegNames = [];
  function buildPegs(n) {
    pegsBox.textContent = '';
    disks = [];
    pegNames = ['A', 'B', 'C'].map((nm) => { const p = D('av-peg'); p.append(D('pole'), D('base'), D('name', nm)); pegsBox.append(p); return p; });
    for (let s = 1; s <= n; s++) { const dd = D(`av-disk c${(s - 1) % 8}`, String(s)); pegsBox.append(dd); disks[s] = dd; }
  }
  function layout(nodes) {
    const kids = new Map(nodes.map((n) => [n.id, []]));
    const roots = [];
    for (const nd of nodes) (nd.p < 0 ? roots : kids.get(nd.p)).push(nd.id);
    const pos = new Map();
    let leaf = 0, maxD = 0;
    const place = (id, d) => {
      maxD = Math.max(maxD, d);
      const k = kids.get(id);
      if (!k.length) { pos.set(id, [leaf++, d]); return; }
      k.forEach((c) => place(c, d + 1));
      pos.set(id, [(pos.get(k[0])[0] + pos.get(k[k.length - 1])[0]) / 2, d]);
    };
    roots.forEach((r) => place(r, 0));
    return { pos, leaves: Math.max(1, leaf), maxD };
  }
  function update(step, d, opts = {}) {
    if (key !== d) {
      key = d;
      gE.textContent = ''; gN.textContent = '';
      pool = new Map(); cur = new Map();
      pegsBox.hidden = d.kind !== 'hanoi';
      if (d.kind === 'hanoi') buildPegs(d.n);
    }
    const W = Math.max(320, host.clientWidth || 640);
    const nodes = step.nodes || [];
    const { pos, leaves, maxD } = layout(nodes);
    const bw = d.kind === 'hanoi' ? 74 : 54;
    const dx = clamp((W - 30) / leaves, bw + 6, 120), dy = 64;
    const totalW = Math.max(W, leaves * dx + 30);
    const H = 34 + (maxD + 1) * dy;
    svg.setAttribute('viewBox', `0 0 ${totalW} ${H}`);
    svg.setAttribute('width', totalW);
    svg.setAttribute('height', H);
    const offX = (totalW - (leaves - 1) * dx) / 2;
    const target = new Map();
    for (const nd of nodes) { const [x, y] = pos.get(nd.id); target.set(nd.id, [offX + x * dx, 26 + y * dy]); }
    const seen = new Set();
    for (const nd of nodes) {
      seen.add(nd.id);
      let P = pool.get(nd.id);
      if (!P) {
        const g = S('g', { class: 'av-ctnode' });
        const rect = S('rect', { x: -bw / 2, y: -15, width: bw, height: 30, rx: 9 });
        const lab = S('text', { class: 'av-ctlab', dy: '0.35em' });
        const val = S('text', { class: 'av-ctval', y: 29 });
        g.append(rect, lab, val);
        gN.append(g);
        const edge = nd.p >= 0 ? S('line', { class: 'av-ctedge' }) : null;
        if (edge) gE.append(edge);
        P = { g, lab, val, edge };
        pool.set(nd.id, P);
        const start = nd.p >= 0 && cur.get(nd.p) ? cur.get(nd.p) : target.get(nd.id);
        cur.set(nd.id, start.slice());
        g.classList.add('av-pop');
      }
      setText(P.lab, nd.label);
      setText(P.val, nd.val === null || nd.val === undefined ? '' : `= ${nd.val}`);
      setCls(P.g, `av-ctnode t-${nd.st}${nd.id === step.cur ? ' focus' : ''}`);
      if (P.edge) setCls(P.edge, `av-ctedge t-${nd.st}`);
    }
    for (const [id, P] of pool) if (!seen.has(id)) { P.g.remove(); P.edge?.remove(); pool.delete(id); cur.delete(id); }
    const from = new Map([...cur].map(([k, v]) => [k, v.slice()]));
    tween(opts.dur ?? 300, (k) => {
      for (const nd of nodes) {
        const a = from.get(nd.id) || target.get(nd.id), b = target.get(nd.id);
        const p = [lerp(a[0], b[0], k), lerp(a[1], b[1], k)];
        cur.set(nd.id, p);
        pool.get(nd.id).g.setAttribute('transform', `translate(${p[0]},${p[1]})`);
      }
      for (const nd of nodes) {
        const P = pool.get(nd.id);
        if (!P.edge) continue;
        const a = cur.get(nd.p), b = cur.get(nd.id);
        if (!a || !b) continue;
        P.edge.setAttribute('x1', a[0]); P.edge.setAttribute('y1', a[1] + 15);
        P.edge.setAttribute('x2', b[0]); P.edge.setAttribute('y2', b[1] - 15);
      }
    });
    if (step.focusScroll !== false && step.cur >= 0 && target.get(step.cur) && totalW > W) {
      const x = target.get(step.cur)[0];
      if (x < scroller.scrollLeft + 40 || x > scroller.scrollLeft + scroller.clientWidth - 40) scroller.scrollTo({ left: x - scroller.clientWidth / 2, behavior: 'smooth' });
    }
    if (d.kind === 'hanoi' && step.pegs) {
      const PW = pegsBox.clientWidth || W;
      const pegW = PW / 3, maxDisk = d.n;
      step.pegs.forEach((peg, pi) => {
        peg.forEach((s, h) => {
          const dd = disks[s];
          const width = 34 + (s / maxDisk) * (pegW * 0.72 - 34);
          dd.style.width = width + 'px';
          dd.style.transform = `translate(${pi * pegW + (pegW - width) / 2}px, ${118 - (h + 1) * 22}px)`;
          dd.classList.toggle('moving', !!(step.move && step.move[1] === pi && h === peg.length - 1));
        });
      });
    }
  }
  return { update, destroy() { wrap.remove(); } };
}

// =========================================================================================== oś (binsearch po wyniku)
export function axisRenderer(host) {
  const svg = S('svg', { class: 'av-axis' });
  host.append(svg);
  const tween = makeTween();
  let st = null, els = null, key = null, W = 600;
  function build(d) {
    key = d;
    svg.textContent = '';
    W = Math.max(340, host.clientWidth || 640);
    svg.setAttribute('viewBox', `0 0 ${W} 190`);
    svg.setAttribute('height', 190);
    const bandF = S('rect', { class: 'av-axband no', y: 78, height: 26, rx: 6 });
    const bandU = S('rect', { class: 'av-axband unk', y: 78, height: 26, rx: 6 });
    const bandT = S('rect', { class: 'av-axband yes', y: 78, height: 26, rx: 6 });
    const axis = S('line', { class: 'av-axline', x1: 30, x2: W - 30, y1: 91, y2: 91 });
    const ticks = S('g', { class: 'av-axticks' });
    for (let i = 0; i <= 10; i++) {
      const v = Math.round((d.H * i) / 10);
      const x = 30 + (i / 10) * (W - 60);
      ticks.append(S('line', { x1: x, x2: x, y1: 106, y2: 112 }), S('text', { x, y: 126 }, String(v)));
    }
    const mk = (cls, up) => {
      const g = S('g', { class: 'av-axmark ' + cls });
      if (up) g.append(S('path', { d: 'M0,72 L-8,58 L8,58 z' }), S('rect', { x: -34, y: 30, width: 68, height: 24, rx: 8 }), S('text', { y: 47 }));
      else g.append(S('path', { d: 'M0,110 L-8,124 L8,124 z' }), S('rect', { x: -34, y: 130, width: 68, height: 24, rx: 8 }), S('text', { y: 147 }));
      return g;
    };
    const mLo = mk('lo', false), mHi = mk('hi', false), mMid = mk('mid', true);
    svg.append(bandF, bandU, bandT, axis, ticks, mLo, mHi, mMid);
    els = { bandF, bandU, bandT, mLo, mHi, mMid };
    st = null;
  }
  function update(step, d, opts = {}) {
    if (key !== d || Math.abs((host.clientWidth || 640) - W) > 30) build(d);
    const X = (v) => 30 + (v / d.H) * (W - 60);
    const target = { lo: step.lo, hi: step.hi, mid: step.mid ?? (step.lo + step.hi) / 2 };
    const from = st || target;
    els.mMid.style.opacity = step.mid === null || step.mid === undefined ? 0 : 1;
    setCls(els.mMid, `av-axmark mid${step.ok === true ? ' t-hit' : step.ok === false ? ' t-miss' : ' t-cmp'}`);
    els.mLo.lastChild.textContent = `lo = ${step.lo}`;
    els.mHi.lastChild.textContent = `hi = ${step.hi}`;
    if (step.mid !== null && step.mid !== undefined) els.mMid.lastChild.textContent = `mid = ${step.mid}`;
    tween(opts.dur ?? 300, (k) => {
      const lo = lerp(from.lo, target.lo, k), hi = lerp(from.hi, target.hi, k), mid = lerp(from.mid, target.mid, k);
      st = { lo, hi, mid };
      els.bandF.setAttribute('x', X(0)); els.bandF.setAttribute('width', Math.max(0, X(lo) - X(0)));
      els.bandU.setAttribute('x', X(lo)); els.bandU.setAttribute('width', Math.max(0, X(hi) - X(lo)));
      els.bandT.setAttribute('x', X(hi)); els.bandT.setAttribute('width', Math.max(0, X(d.H) - X(hi)));
      els.mLo.setAttribute('transform', `translate(${X(lo)},0)`);
      els.mHi.setAttribute('transform', `translate(${X(hi)},0)`);
      els.mMid.setAttribute('transform', `translate(${X(mid)},0)`);
    });
  }
  return { update, destroy() { svg.remove(); } };
}

export const RENDERERS = {
  arrays: arraysRenderer, grid: gridRenderer, graph: graphRenderer, forest: forestRenderer, segtree: segtreeRenderer,
  fenwick: fenwickRenderer, table: tableRenderer, sieve: sieveRenderer, calltree: calltreeRenderer, axis: axisRenderer,
};

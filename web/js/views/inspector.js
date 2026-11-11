// (M) Inspektor wejścia/wyjścia — wklejasz test, a widok pokazuje go czytelnie: tablice, macierze (mapa ciepła),
// siatki znaków, grafy (układ siłowy, przeciąganie, zoom), drzewa (warstwy od korzenia), przedziały na osi, multitesty.
// Wyjście jest nakładane na wejście (ścieżka na grafie, etykiety wierzchołków, odpowiedzi na zapytania).
import { h, icon, button, viewHeader, textarea, segmented, checkbox, badge, toast, clear, emptyState, input, debounce } from '../ui.js';
import { api, local } from '../api.js';

const EXAMPLES = [
  { label: 'Graf ważony', input: '6 8\n1 2 4\n1 3 2\n2 3 5\n2 4 10\n3 5 3\n5 4 4\n4 6 11\n5 6 7\n', output: '1 3 5 4 6\n' },
  { label: 'Drzewo', input: '9\n1 2\n1 3\n2 4\n2 5\n3 6\n6 7\n6 8\n6 9\n', output: '0 1 1 2 2 2 3 3 3\n' },
  { label: 'Siatka (labirynt)', input: '6 9\nS..#.....\n.#.#.###.\n.#...#...\n.####.#.#\n......#..\n##.#....E\n', output: '' },
  { label: 'Tablica + zapytania', input: '8 4\n3 1 4 1 5 9 2 6\n1 4\n2 6\n5 8\n3 3\n', output: '9\n20\n22\n4\n' },
  { label: 'Macierz', input: '4 5\n1 3 1 2 9\n4 2 8 1 1\n7 5 3 6 2\n1 1 4 2 8\n', output: '' },
  { label: 'Przedziały', input: '5\n1 4\n2 6\n8 10\n3 5\n9 12\n', output: '' },
  { label: 'Multitest', input: '3\n4\n1 2 3 4\n3\n5 5 5\n2\n7 1\n', output: 'TAK\nNIE\nTAK\n' },
  { label: 'Tablica rodziców', input: '7\n1 1 2 2 3 3\n', output: '' },
];

const CHAR_TONES = ['var(--accent)', 'var(--info)', 'var(--success)', 'var(--warn)', 'var(--danger)', 'var(--tok-builtin)', 'var(--tok-kw)', 'var(--tok-def)'];
const fmt = (x) => (typeof x === 'number' && !Number.isInteger(x) ? +x.toFixed(4) : x);
const pct = (c) => `${Math.round(c * 100)}%`;

function charStyle(ch, idx) {
  if (ch === '#') return { cls: 'wall' };
  if (ch === '.' || ch === '_' || ch === ' ') return { cls: 'floor' };
  if (/[0-9]/.test(ch)) return { cls: 'digit', tone: `color-mix(in srgb, var(--accent) ${10 + 8 * (+ch)}%, var(--panel))` };
  return { cls: 'other', tone: `color-mix(in srgb, ${CHAR_TONES[idx % CHAR_TONES.length]} 34%, var(--panel))` };
}

// ============================================================================================ renderery
function rValues(p) {
  return h('div', { class: 'ins-values' }, p.names.map((n, i) => h('div', { class: 'ins-val' }, h('span', { class: 'mono ins-val-n' }, n),
    h('span', { class: 'ins-val-eq' }, '='), h('span', { class: 'mono ins-val-v' }, String(fmt(p.values[i]))))));
}

function rArray(p, opts, ov) {
  const vals = p.values;
  const base = opts.base;
  const nums = vals.filter((x) => typeof x === 'number');
  const mn = p.min, mx = p.max;
  const span = mx - mn || 1;
  const outRow = ov && ov.kind === 'array' ? ov.values : null;
  const marks = ov && ov.kind === 'indices' ? new Set(ov.values) : null;
  const shown = vals.slice(0, 400);
  const cells = shown.map((v, i) => {
    const isMin = typeof v === 'number' && v === mn, isMax = typeof v === 'number' && v === mx;
    return h('div', { class: ['ins-cell', isMin ? 'min' : '', isMax ? 'max' : '', marks && marks.has(i + base) ? 'marked' : ''].join(' '),
      title: `[${i + base}] = ${v}`, style: { '--fill': typeof v === 'number' ? `${Math.round(((v - mn) / span) * 100)}%` : '0%' } }, String(fmt(v)));
  });
  const idx = shown.map((_, i) => h('div', { class: 'ins-ix' }, String(i + base)));
  const rows = [h('div', { class: 'ins-cells bars' }, cells), h('div', { class: 'ins-idx' }, idx)];
  if (outRow) {
    rows.push(h('div', { class: 'ins-row-lbl small muted' }, 'wyjście:'));
    rows.push(h('div', { class: 'ins-cells' }, outRow.slice(0, 400).map((v, i) => h('div', { class: ['ins-cell out', String(v) !== String(vals[i]) ? 'diff' : 'same'].join(' '),
      title: String(v) !== String(vals[i]) ? `zmiana: ${vals[i]} → ${v}` : 'bez zmian' }, String(fmt(v))))));
  }
  const stats = h('div', { class: 'row gap-s ins-stats' },
    badge(`n = ${p.n}`, 'neutral'), nums.length ? badge(`min ${fmt(mn)}`, 'info') : null, nums.length ? badge(`max ${fmt(mx)}`, 'warn') : null,
    nums.length ? badge(`suma ${fmt(p.sum)}`, 'neutral') : null, p.sorted ? badge('posortowana', 'success', 'check') : null,
    nums.length && new Set(nums).size !== nums.length ? badge(`${nums.length - new Set(nums).size} powtórzeń`, 'neutral') : null,
    vals.length > 400 ? badge(`pokazano 400 z ${vals.length}`, 'warn') : null);
  return h('div', { class: 'stack gap-s' }, stats, h('div', { class: 'ins-scroll' }, rows));
}

function rMatrix(p, opts) {
  const R = p.rows.length, C = Math.max(0, ...p.rows.map((r) => r.length));
  const mn = p.min ?? 0, mx = p.max ?? 1;
  const span = mx - mn || 1;
  const base = opts.base;
  const grid = h('div', { class: 'ins-grid', style: { gridTemplateColumns: `30px repeat(${C}, minmax(32px, auto))` } });
  grid.append(h('div', { class: 'ins-gh' }));
  for (let c = 0; c < C; c++) grid.append(h('div', { class: 'ins-gh' }, String(c + base)));
  p.rows.forEach((row, r) => {
    grid.append(h('div', { class: 'ins-gh' }, String(r + base)));
    for (let c = 0; c < C; c++) {
      const v = row[c];
      grid.append(h('div', { class: 'ins-mc', title: `[${r + base}][${c + base}] = ${v}`,
        style: { '--heat': typeof v === 'number' ? `${Math.round(((v - mn) / span) * 70)}%` : '0%' } }, v === undefined ? '' : String(fmt(v))));
    }
  });
  return h('div', { class: 'stack gap-s' }, h('div', { class: 'row gap-s' }, badge(`${p.n} × ${p.m}`, 'neutral'), badge(`min ${fmt(mn)}`, 'info'), badge(`max ${fmt(mx)}`, 'warn'),
    h('span', { class: 'ins-heat-legend' }, h('span', null, String(fmt(mn))), h('i'), h('span', null, String(fmt(mx)))),
    p.truncated ? badge('fragment', 'warn') : null), h('div', { class: 'ins-scroll' }, grid));
}

function rGrid(p, opts) {
  const chars = Object.keys(p.legend);
  const order = new Map(chars.map((c, i) => [c, i]));
  const C = p.m;
  const base = opts.base;
  const grid = h('div', { class: 'ins-cgrid', style: { gridTemplateColumns: `24px repeat(${C}, var(--cg, 24px))` } });
  grid.append(h('div', { class: 'ins-gh' }));
  for (let c = 0; c < C; c++) grid.append(h('div', { class: 'ins-gh' }, (c + base) % 5 === 0 || C <= 20 ? String(c + base) : ''));
  p.rows.forEach((row, r) => {
    grid.append(h('div', { class: 'ins-gh' }, String(r + base)));
    for (let c = 0; c < C; c++) {
      const ch = row[c] ?? '';
      const st = charStyle(ch, order.get(ch) ?? 0);
      grid.append(h('div', { class: `ins-cc ${st.cls}`, style: st.tone ? { background: st.tone } : null, title: `(${r + base}, ${c + base}) = '${ch}'` }, st.cls === 'floor' || st.cls === 'wall' ? '' : ch));
    }
  });
  const legend = h('div', { class: 'ins-legend' }, chars.map((ch) => {
    const st = charStyle(ch, order.get(ch));
    return h('div', { class: 'ins-leg' }, h('span', { class: `ins-cc ${st.cls}`, style: st.tone ? { background: st.tone } : null }, st.cls === 'floor' || st.cls === 'wall' ? '' : ch),
      h('span', { class: 'mono' }, `'${ch}'`), h('span', { class: 'muted small' }, `× ${p.legend[ch]}`));
  }));
  return h('div', { class: 'stack gap-s' }, h('div', { class: 'row gap-s' }, badge(`${p.n} × ${p.m}`, 'neutral'), p.truncated ? badge('fragment', 'warn') : null), legend,
    h('div', { class: 'ins-scroll' }, grid));
}

// ---------------------------------------------------------------- graf / drzewo (SVG)
const SVGNS = 'http://www.w3.org/2000/svg';
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const c of kids.flat()) if (c !== null && c !== undefined) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

function forceLayout(n, edges, W, H) {
  const pos = [];
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / Math.max(1, n);
    pos.push({ x: W / 2 + Math.cos(a) * W * 0.35 + (Math.random() - 0.5) * 4, y: H / 2 + Math.sin(a) * H * 0.35 + (Math.random() - 0.5) * 4 });
  }
  if (n <= 1) { if (n === 1) pos[0] = { x: W / 2, y: H / 2 }; return pos; }
  const k = Math.sqrt((W * H) / n) * 0.75;
  const iters = n > 250 ? 90 : n > 100 ? 160 : 300;
  let t = W / 8;
  for (let it = 0; it < iters; it++) {
    const disp = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = Math.random(); dy = Math.random(); d2 = 1; }
        const d = Math.sqrt(d2);
        const f = (k * k) / d;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        disp[i].x += fx; disp[i].y += fy; disp[j].x -= fx; disp[j].y -= fy;
      }
    }
    for (const [a, b] of edges) {
      if (a === b) continue;
      const dx = pos[a].x - pos[b].x, dy = pos[a].y - pos[b].y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d * d) / k;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      disp[a].x -= fx; disp[a].y -= fy; disp[b].x += fx; disp[b].y += fy;
    }
    for (let i = 0; i < n; i++) {
      disp[i].x += (W / 2 - pos[i].x) * 0.01 * k / 10;
      disp[i].y += (H / 2 - pos[i].y) * 0.01 * k / 10;
      const d = Math.sqrt(disp[i].x ** 2 + disp[i].y ** 2) || 1;
      pos[i].x += (disp[i].x / d) * Math.min(d, t);
      pos[i].y += (disp[i].y / d) * Math.min(d, t);
    }
    t *= 0.97;
  }
  // dopasuj do pola
  const xs = pos.map((p) => p.x), ys = pos.map((p) => p.y);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const sc = Math.min((W - 80) / Math.max(1, maxx - minx), (H - 80) / Math.max(1, maxy - miny), 3);
  return pos.map((p) => ({ x: 40 + (p.x - minx) * sc + ((W - 80) - (maxx - minx) * sc) / 2, y: 40 + (p.y - miny) * sc + ((H - 80) - (maxy - miny) * sc) / 2 }));
}

function treeLayout(n, edges, root, W) {
  const adj = Array.from({ length: n }, () => []);
  for (const [a, b] of edges) { adj[a].push(b); adj[b].push(a); }
  const depth = new Array(n).fill(-1);
  const parent = new Array(n).fill(-1);
  const order = [root];
  depth[root] = 0;
  for (let q = 0; q < order.length; q++) {
    const v = order[q];
    for (const u of adj[v]) if (depth[u] < 0) { depth[u] = depth[v] + 1; parent[u] = v; order.push(u); }
  }
  const kids = Array.from({ length: n }, () => []);
  for (const v of order) if (parent[v] >= 0) kids[parent[v]].push(v);
  const x = new Array(n).fill(0);
  let leaf = 0;
  const visit = (v) => {                    // iteracyjnie: post-order
    const st = [[v, 0]];
    while (st.length) {
      const top = st[st.length - 1];
      const [u, i] = top;
      if (i < kids[u].length) { top[1]++; st.push([kids[u][i], 0]); continue; }
      st.pop();
      if (!kids[u].length) x[u] = leaf++;
      else x[u] = (x[kids[u][0]] + x[kids[u][kids[u].length - 1]]) / 2;
    }
  };
  visit(root);
  let extra = leaf;
  for (let v = 0; v < n; v++) if (depth[v] < 0) { depth[v] = 0; x[v] = extra++; }   // las: pozostałe składowe
  const maxD = Math.max(...depth);
  const cols = Math.max(1, extra - 1);
  const gap = Math.max(46, Math.min(90, (W - 80) / cols));
  const width = Math.max(W, cols * gap + 80);
  const pos = x.map((xx, v) => ({ x: 40 + (cols ? (xx / cols) * (width - 80) : (width - 80) / 2), y: 46 + depth[v] * 78 }));
  return { pos, depth, maxD, width, height: 92 + maxD * 78, parent };
}

function rGraph(p, opts, ov, isTree) {
  const n = Math.min(p.n, 2000);
  const base = p.base;
  const edges = p.edges.map((e) => [e[0] - base, e[1] - base, e[2]]).filter((e) => e[0] >= 0 && e[1] >= 0 && e[0] < n && e[1] < n);
  const wrap = h('div', { class: 'ins-graph-wrap' });
  const W = 760, Hh = Math.max(380, Math.min(620, 140 + n * 9));
  let directed = !!(opts.directed || p.directed);
  let root = Math.max(0, Math.min(n - 1, (opts.root ?? p.root ?? base) - base));
  const pathSet = new Set();
  const pathOrder = new Map();
  if (ov && ov.kind === 'path') ov.vertices.forEach((v, i) => { pathOrder.set(v - base, (pathOrder.get(v - base) || []).concat(i + 1)); });
  if (ov && ov.kind === 'path') for (let i = 0; i + 1 < ov.vertices.length; i++) pathSet.add(`${ov.vertices[i] - base}-${ov.vertices[i + 1] - base}`);
  const labels = ov && ov.kind === 'node_labels' ? ov.values : null;   // labels[i] ↔ wierzchołek base + i
  const nodeSet = ov && ov.kind === 'node_set' ? new Set(ov.vertices.map((v) => v - base)) : null;
  const deg = new Array(n).fill(0);
  for (const [a, b] of edges) { deg[a]++; deg[b]++; }
  const maxDeg = Math.max(1, ...deg);
  let layout = null;
  let view = { x: 0, y: 0, w: W, h: Hh };
  const svg = s('svg', { class: 'ins-svg', viewBox: `0 0 ${W} ${Hh}`, preserveAspectRatio: 'xMidYMid meet' });
  const info = h('div', { class: 'small muted ins-graph-info' });

  function compute() {
    if (isTree) {
      const t = treeLayout(n, edges, root, W);
      layout = t.pos;
      view = { x: 0, y: 0, w: t.width, h: Math.max(t.height, 200) };
      info.textContent = `korzeń ${root + base} · wysokość ${t.maxD} · liście: ${deg.filter((d, i) => d <= 1 && i !== root).length}`;
      layout.depth = t.depth;
    } else {
      layout = forceLayout(n, edges, W, Hh);
      view = { x: 0, y: 0, w: W, h: Hh };
      const comps = components(n, edges);
      info.textContent = `${comps} ${comps === 1 ? 'spójna składowa' : 'składowych'} · maks. stopień ${maxDeg}` + (p.loops ? ` · pętle: ${p.loops}` : '') + (p.multi_edges ? ` · krawędzie wielokrotne: ${p.multi_edges}` : '');
    }
    draw();
  }

  function draw() {
    clear(svg);
    svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
    const defs = s('defs', {}, s('marker', { id: 'ins-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
      s('path', { d: 'M0,0 L10,5 L0,10 z', class: 'ins-arrowhead' })),
    s('marker', { id: 'ins-arrow-hl', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
      s('path', { d: 'M0,0 L10,5 L0,10 z', class: 'ins-arrowhead hl' })));
    svg.append(defs);
    const gE = s('g', { class: 'ins-edges' });
    const gW = s('g', { class: 'ins-weights' });
    const gN = s('g', { class: 'ins-nodes' });
    const R = (v) => 11 + (n <= 60 ? 6 * (deg[v] / maxDeg) : 0);
    const edgeEls = [];
    edges.forEach(([a, b, w], idx) => {
      const pa = layout[a], pb = layout[b];
      const hl = pathSet.has(`${a}-${b}`) || (!directed && pathSet.has(`${b}-${a}`));
      let line;
      if (a === b) {
        line = s('path', { d: `M${pa.x} ${pa.y - R(a)} c -26 -34, 26 -34, 0 0`, class: ['ins-edge', hl ? 'hl' : ''].join(' '), fill: 'none' });
      } else {
        const dx = pb.x - pa.x, dy = pb.y - pa.y, d = Math.hypot(dx, dy) || 1;
        const x2 = directed ? pb.x - (dx / d) * (R(b) + 2) : pb.x, y2 = directed ? pb.y - (dy / d) * (R(b) + 2) : pb.y;
        line = s('line', { x1: pa.x, y1: pa.y, x2, y2, class: ['ins-edge', hl ? 'hl' : ''].join(' '),
          'marker-end': directed ? (hl ? 'url(#ins-arrow-hl)' : 'url(#ins-arrow)') : null });
      }
      line.append(s('title', {}, `${a + base} — ${b + base}${w !== undefined ? ` (waga ${w})` : ''}`));
      edgeEls.push([line, a, b, idx]);
      gE.append(line);
      if (w !== undefined && edges.length <= 400) {
        const mx = (pa.x + pb.x) / 2, my = (pa.y + pb.y) / 2;
        gW.append(s('g', { class: ['ins-w', hl ? 'hl' : ''].join(' ') }, s('rect', { x: mx - 11, y: my - 9, width: 22, height: 16, rx: 5 }), s('text', { x: mx, y: my + 3 }, String(w))));
      }
    });
    for (let v = 0; v < n; v++) {
      const pv = layout[v];
      const onPath = pathOrder.has(v);
      const inSet = nodeSet && nodeSet.has(v);
      const g = s('g', { class: ['ins-node', onPath || inSet ? 'hl' : '', isTree && v === root ? 'root' : ''].join(' '), transform: `translate(${pv.x},${pv.y})` },
        s('circle', { r: R(v) }), n <= 400 ? s('text', { class: 'ins-node-id', y: 4 }, String(v + base)) : null,
        s('title', {}, `wierzchołek ${v + base} · stopień ${deg[v]}${isTree && layout.depth ? ` · głębokość ${layout.depth[v]}` : ''}`));
      const lab = labels ? labels[v] : undefined;
      if (lab !== undefined && lab !== null) g.append(s('text', { class: 'ins-node-lbl', y: R(v) + 14 }, String(lab)));
      if (onPath) g.append(s('text', { class: 'ins-node-ord', x: R(v) + 2, y: -R(v) + 2 }, pathOrder.get(v).join(',')));
      if (isTree && layout.depth && n <= 200) g.append(s('text', { class: 'ins-node-depth', x: -R(v) - 4, y: -R(v) + 2 }, `d${layout.depth[v]}`));
      enableDrag(g, v);
      gN.append(g);
    }
    svg.append(gE, gW, gN);
    svg._edgeEls = edgeEls;
  }

  function redrawEdges() { draw(); }
  function fit() {
    if (!layout) return;
    const xs = layout.map((q) => q.x), ys = layout.map((q) => q.y);
    const pad = 40;
    const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
    view = { x: x0, y: y0, w: Math.max(120, Math.max(...xs) + pad - x0), h: Math.max(120, Math.max(...ys) + pad - y0) };
    svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  }

  function toSvg(e) {
    const r = svg.getBoundingClientRect();
    const sc = Math.max(view.w / r.width, view.h / r.height);
    const offX = (r.width * sc - view.w) / 2, offY = (r.height * sc - view.h) / 2;
    return { x: view.x + (e.clientX - r.left) * sc - offX, y: view.y + (e.clientY - r.top) * sc - offY, sc };
  }
  function enableDrag(g, v) {
    g.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      g.setPointerCapture(e.pointerId);
      g.classList.add('drag');
      const move = (ev) => { const pt = toSvg(ev); layout[v] = { x: pt.x, y: pt.y }; g.setAttribute('transform', `translate(${pt.x},${pt.y})`); };
      const up = () => { g.classList.remove('drag'); g.removeEventListener('pointermove', move); g.removeEventListener('pointerup', up); redrawEdges(); };
      g.addEventListener('pointermove', move);
      g.addEventListener('pointerup', up);
    });
  }
  // przesuwanie tła i zoom kółkiem
  svg.addEventListener('pointerdown', (e) => {
    const start = toSvg(e);
    const v0 = { ...view };
    svg.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const r = svg.getBoundingClientRect();
      const sc = Math.max(v0.w / r.width, v0.h / r.height);
      view.x = v0.x - (ev.clientX - e.clientX) * sc;
      view.y = v0.y - (ev.clientY - e.clientY) * sc;
      svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
    };
    const up = () => { svg.removeEventListener('pointermove', move); svg.removeEventListener('pointerup', up); };
    svg.addEventListener('pointermove', move);
    svg.addEventListener('pointerup', up);
    void start;
  });
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const pt = toSvg(e);
    const f = e.deltaY > 0 ? 1.12 : 1 / 1.12;
    view = { x: pt.x - (pt.x - view.x) * f, y: pt.y - (pt.y - view.y) * f, w: view.w * f, h: view.h * f };
    svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  }, { passive: false });

  const tools = h('div', { class: 'row gap-s ins-graph-tools' },
    badge(`n = ${p.n}`, 'neutral'), badge(`m = ${p.m}`, 'neutral'), p.weighted ? badge('ważony', 'accent') : null, badge(`indeksy od ${base}`, 'info'),
    isTree ? h('label', { class: 'ins-root' }, 'korzeń:', input({ value: String(root + base), size: 'sm', mono: true, type: 'number',
      onChange: (v) => { const r = parseInt(v, 10) - base; if (r >= 0 && r < n) { root = r; opts.root = r + base; compute(); } } })) : null,
    !isTree || p.directed ? checkbox('skierowany', { checked: directed, onChange: (c) => { directed = c; opts.directed = c; draw(); } }) : null,
    h('span', { class: 'grow' }),
    button('Rozmieść ponownie', { icon: 'refresh-cw', size: 'sm', variant: 'ghost', onClick: () => compute() }),
    button('Dopasuj widok', { icon: 'maximize-2', size: 'sm', variant: 'ghost', onClick: () => fit() }));
  wrap.append(tools, h('div', { class: 'ins-svg-box' }, svg), info);
  if (p.truncated) wrap.append(h('div', { class: 'small tone-warn' }, 'Duży graf — pokazano fragment.'));
  if (ov && (ov.kind === 'path' || ov.kind === 'node_labels' || ov.kind === 'node_set')) wrap.append(h('div', { class: 'ins-ov-note small' }, icon('sparkles', 13), ' ', ov.label));
  requestAnimationFrame(compute);
  return wrap;
}

function components(n, edges) {
  const p = Array.from({ length: n }, (_, i) => i);
  const f = (x) => { while (p[x] !== x) { p[x] = p[p[x]]; x = p[x]; } return x; };
  let c = n;
  for (const [a, b] of edges) { const ra = f(a), rb = f(b); if (ra !== rb) { p[ra] = rb; c--; } }
  return c;
}

// ---------------------------------------------------------------- przedziały / pary / zapytania
function rIntervals(p, opts, ov) {
  const items = p.items;
  const answers = ov && ov.kind === 'answers' ? ov.values : null;
  if (p.kind === 'queries' || p.kind === 'table') {
    const types = new Map();
    items.forEach((it) => { const t = it[0]; types.set(t, (types.get(t) || 0) + 1); });
    return h('div', { class: 'stack gap-s' },
      h('div', { class: 'row gap-s' }, badge(`q = ${p.q}`, 'neutral'), p.kind === 'queries' ? [...types].map(([t, c]) => badge(`typ ${t}: ${c}`, 'accent')) : null),
      h('div', { class: 'ins-qtable' }, items.slice(0, 400).map((it, i) => h('div', { class: 'ins-q' },
        h('span', { class: 'ins-q-i mono' }, `#${i + 1}`),
        p.kind === 'queries' ? h('span', { class: `ins-q-t t${hashT(it[0])}` }, String(it[0])) : null,
        h('span', { class: 'mono' }, (p.kind === 'queries' ? it.slice(1) : it).join('  ')),
        answers ? h('span', { class: 'ins-ans mono' }, '→ ', String(answers[i] ?? '')) : null))));
  }
  if (p.kind === 'points') {
    const xs = items.map((x) => x[0]), ys = items.map((x) => x[1]);
    const W = 520, H = 300;
    const mnx = Math.min(...xs), mxx = Math.max(...xs), mny = Math.min(...ys), mxy = Math.max(...ys);
    const X = (x) => 30 + ((x - mnx) / (mxx - mnx || 1)) * (W - 60), Y = (y) => H - 30 - ((y - mny) / (mxy - mny || 1)) * (H - 60);
    const svg = s('svg', { class: 'ins-svg points', viewBox: `0 0 ${W} ${H}` },
      s('line', { x1: 30, y1: H - 30, x2: W - 20, y2: H - 30, class: 'ins-axis' }), s('line', { x1: 30, y1: 20, x2: 30, y2: H - 30, class: 'ins-axis' }),
      items.slice(0, 2000).map((it, i) => s('g', { class: 'ins-pt' }, s('circle', { cx: X(it[0]), cy: Y(it[1]), r: 4.5 }),
        items.length <= 40 ? s('text', { x: X(it[0]) + 7, y: Y(it[1]) - 6 }, `(${it[0]}, ${it[1]})`) : null, s('title', {}, `#${i + 1}: (${it[0]}, ${it[1]})`))),
      s('text', { x: 30, y: H - 12, class: 'ins-axis-lbl' }, String(mnx)), s('text', { x: W - 20, y: H - 12, class: 'ins-axis-lbl', 'text-anchor': 'end' }, String(mxx)));
    return h('div', { class: 'stack gap-s' }, h('div', { class: 'row gap-s' }, badge(`${p.q} par`, 'neutral'), badge(`x ∈ [${mnx}, ${mxx}]`, 'info'), badge(`y ∈ [${mny}, ${mxy}]`, 'info')), svg);
  }
  // przedziały na osi: zachłanne przydzielanie torów
  const lo = p.min, hi = p.max;
  const span = hi - lo || 1;
  const sorted = items.map((it, i) => ({ l: it[0], r: it[1], i })).sort((a, b) => a.l - b.l || a.r - b.r);
  const lanes = [];
  for (const it of sorted) {
    let k = lanes.findIndex((end) => end < it.l);
    if (k < 0) { k = lanes.length; lanes.push(it.r); } else lanes[k] = it.r;
    it.lane = k;
  }
  const W = 640, laneH = 26, H = 40 + lanes.length * laneH;
  const X = (x) => 24 + ((x - lo) / span) * (W - 48);
  const ticks = [];
  const step = Math.max(1, Math.ceil(span / 12));
  for (let t = lo; t <= hi; t += step) ticks.push(t);
  const cover = new Map();
  for (const it of items) for (let x = it[0]; x <= it[1] && x - it[0] < 2000; x++) cover.set(x, (cover.get(x) || 0) + 1);
  const maxCover = Math.max(0, ...cover.values());
  const svg = s('svg', { class: 'ins-svg intervals', viewBox: `0 0 ${W} ${H}` },
    s('line', { x1: 24, y1: H - 18, x2: W - 24, y2: H - 18, class: 'ins-axis' }),
    ticks.map((t) => s('g', {}, s('line', { x1: X(t), y1: H - 22, x2: X(t), y2: H - 14, class: 'ins-axis' }), s('text', { x: X(t), y: H - 3, class: 'ins-axis-lbl', 'text-anchor': 'middle' }, String(t)))),
    sorted.slice(0, 400).map((it) => {
      const y = 10 + it.lane * laneH;
      const x1 = X(it.l), x2 = Math.max(X(it.r), x1 + 6);
      return s('g', { class: `ins-iv t${it.i % 6}` }, s('rect', { x: x1, y, width: x2 - x1, height: 16, rx: 6 }),
        s('text', { x: (x1 + x2) / 2, y: y + 12, 'text-anchor': 'middle' }, items.length <= 40 ? `#${it.i + 1}` : ''),
        answers ? s('text', { x: x2 + 5, y: y + 12, class: 'ins-iv-ans' }, `= ${answers[it.i]}`) : null,
        s('title', {}, `#${it.i + 1}: [${it.l}, ${it.r}] · długość ${it.r - it.l + 1}${answers ? ` · odpowiedź ${answers[it.i]}` : ''}`));
    }));
  return h('div', { class: 'stack gap-s' },
    h('div', { class: 'row gap-s' }, badge(`${p.q} przedziałów`, 'neutral'), badge(`zakres [${lo}, ${hi}]`, 'info'), badge(`maks. pokrycie ${maxCover}`, 'warn'),
      badge(`${lanes.length} ${lanes.length === 1 ? 'tor' : 'torów'} bez nakładania`, 'neutral')),
    h('div', { class: 'ins-scroll' }, svg));
}
function hashT(t) { return Math.abs(parseInt(t, 10) || 0) % 6; }

function rStrings(p, opts, ov) {
  const answers = ov && ov.kind === 'answers' ? ov.values : null;
  return h('div', { class: 'ins-strings' }, p.items.slice(0, 300).map((str, i) => {
    const pal = str.length > 1 && str === [...str].reverse().join('');
    return h('div', { class: 'ins-str' }, h('span', { class: 'ins-q-i mono' }, `#${i + 1}`),
      str.length <= 40 && !/\s/.test(str)
        ? h('div', { class: 'ins-chars' }, [...str].map((c, k) => h('span', { class: 'ins-ch', title: `[${k + opts.base}] = '${c}'` }, c)))
        : h('span', { class: 'mono ins-str-txt' }, str),
      h('span', { class: 'small muted' }, `${str.length} zn.`), pal ? badge('palindrom', 'success') : null,
      answers ? h('span', { class: 'ins-ans mono' }, '→ ', String(answers[i] ?? '')) : null);
  }));
}

function rText(p) { return h('pre', { class: 'ins-text mono' }, (p.lines_text || []).join('\n')); }

function rMultitest(p, opts, ov, renderParts) {
  const answers = ov && ov.kind === 'per_test' ? ov.values : null;
  const host = h('div', { class: 'ins-mt-body' });
  let cur = 0;
  const btns = p.blocks.map((b, i) => h('button', { type: 'button', class: 'chip', onclick: () => show(i) }, `Test ${i + 1}`,
    answers ? h('span', { class: 'ins-mt-ans mono' }, String((answers[i] || []).join(' '))) : null));
  function show(i) {
    cur = i;
    btns.forEach((b, k) => b.classList.toggle('active', k === i));
    clear(host).append(renderParts(p.blocks[i].parts, null));
  }
  const list = h('div', { class: 'ins-mt-list' }, btns.slice(0, 200));
  setTimeout(() => show(cur), 0);
  return h('div', { class: 'stack gap-s' }, h('div', { class: 'row gap-s' }, badge(`t = ${p.t}`, 'accent'), h('span', { class: 'small muted' }, p.summary)), list, host);
}

// ============================================================================================ widok
export default {
  id: 'inspector',
  title: 'Inspektor wejścia/wyjścia',
  icon: 'scan-search',
  group: 'Testowanie',
  order: 70,
  description: 'Wklej test, a zobaczysz go czytelnie: tablice, macierze (mapa ciepła), siatki, grafy i drzewa do przeciągania, przedziały na osi i multitesty. Wyjście nakłada się na wejście — np. ścieżka z odpowiedzi podświetla się na grafie.',
  keywords: ['inspektor', 'wejście', 'wyjście', 'wizualizacja testu', 'graf', 'drzewo', 'siatka', 'macierz', 'tablica', 'przedziały',
    'multitest', 'podgląd testu', 'input', 'output'],

  render(root) {
    const view = this;
    const opts = { base: parseInt(local.get('inspector:base', '0'), 10) || 0, directed: false, root: null };
    let last = null;
    let chosen = null;          // id kandydata wybranego ręcznie
    let reqId = 0;

    const inEl = textarea({ value: local.get('inspector:input', EXAMPLES[0].input), rows: 10, placeholder: 'Wklej wejście testu…', onInput: () => { local.set('inspector:input', inEl.value); chosen = null; auto(); } });
    const outEl = textarea({ value: local.get('inspector:output', EXAMPLES[0].output), rows: 4, placeholder: 'Wyjście (opcjonalnie) — zostanie nałożone na wejście', onInput: () => { local.set('inspector:output', outEl.value); auto(); } });
    const candHost = h('div', { class: 'ins-cands' });
    const partsHost = h('div', { class: 'ins-parts' });
    const statsLine = h('div', { class: 'small muted' });
    const baseSeg = segmented([{ value: '0', label: 'od 0' }, { value: '1', label: 'od 1' }], { value: String(opts.base), onChange: (v) => { opts.base = +v; local.set('inspector:base', v); draw(); } });
    const exRow = h('div', { class: 'ins-examples' }, EXAMPLES.map((e) => h('button', { type: 'button', class: 'chip', onclick: () => {
      inEl.value = e.input; outEl.value = e.output; local.set('inspector:input', e.input); local.set('inspector:output', e.output); chosen = null; analyze();
    } }, e.label)));

    const leftCol = h('div', { class: 'ins-left' },
      h('div', { class: 'ins-field' }, h('div', { class: 'row between' }, h('span', { class: 'section-title' }, 'Wejście'), statsLine), inEl),
      h('div', { class: 'ins-field' }, h('span', { class: 'section-title' }, 'Wyjście'), outEl),
      h('div', { class: 'ins-field' }, h('span', { class: 'section-title' }, 'Interpretacja'), candHost),
      h('div', { class: 'row gap-s' }, h('span', { class: 'small muted' }, 'Indeksy tablic i siatek:'), baseSeg),
      h('div', { class: 'ins-field' }, h('span', { class: 'section-title' }, 'Przykłady'), exRow));
    const rightCol = h('div', { class: 'ins-right' }, partsHost);
    root.append(viewHeader(this, button('Analizuj', { icon: 'scan-search', variant: 'primary', onClick: () => analyze() })),
      h('div', { class: 'ins-body' }, leftCol, rightCol));

    const auto = debounce(() => analyze(true), 450);

    async function analyze(quiet = false) {
      const text = inEl.value;
      if (!text.trim()) { clear(candHost); partsHost.replaceChildren(emptyState({ icon: 'scan-search', title: 'Wklej wejście testu', text: 'Rozpoznam tablice, macierze, siatki, grafy, drzewa, przedziały i multitesty.' })); return; }
      const my = ++reqId;
      try {
        const r = await api.post('/api/inspector/detect', { text, output: outEl.value }, { quiet });
        if (my !== reqId) return;
        last = r;
        if (!chosen || !r.candidates.some((c) => c.id === chosen)) chosen = null;
        statsLine.textContent = `${r.stats.lines} linii · ${r.stats.tokens} tokenów`;
        draw();
      } catch (e) {
        if (!quiet) toast(e.message, 'error');
      }
    }

    function current() {
      if (!last || !last.candidates.length) return null;
      return last.candidates.find((c) => c.id === chosen) || last.candidates[0];
    }

    function draw() {
      const cand = current();
      clear(candHost);
      if (!last) return;
      last.candidates.slice(0, 7).forEach((c, i) => {
        const on = cand && c.id === cand.id;
        candHost.append(h('button', { type: 'button', class: ['ins-cand', on ? 'active' : ''].join(' '), onclick: () => { chosen = c.id; draw(); } },
          h('div', { class: 'ins-cand-top' }, h('b', null, c.label), i === 0 ? badge('auto', 'accent') : null, h('span', { class: 'grow' }),
            h('span', { class: 'ins-conf mono' }, pct(c.confidence))),
          h('div', { class: 'ins-conf-bar' }, h('i', { style: { width: pct(c.confidence) } })),
          h('div', { class: 'small muted ins-cand-sum' }, c.summary)));
      });
      if (!cand) { partsHost.replaceChildren(emptyState({ icon: 'scan-search', title: 'Brak interpretacji' })); return; }
      const ov = last.output_overlay ? last.output_overlay[cand.id] : null;
      partsHost.replaceChildren(renderParts(cand.parts, ov), outCard(ov));
    }

    function renderParts(parts, ov) {
      const wrap = h('div', { class: 'stack' });
      for (const p of parts) {
        const kind = p.kind;
        let body;
        try {
          if (kind === 'values') body = rValues(p);
          else if (kind === 'array') body = rArray(p, opts, ov && (ov.kind === 'array' || ov.kind === 'indices') ? ov : null);
          else if (kind === 'matrix') body = rMatrix(p, opts);
          else if (kind === 'grid') body = rGrid(p, opts);
          else if (kind === 'graph') body = rGraph(p, opts, ov, false);
          else if (kind === 'tree') body = rGraph(p, opts, ov, true);
          else if (['intervals', 'points', 'queries', 'table'].includes(kind)) body = rIntervals(p, opts, ov);
          else if (kind === 'strings') body = rStrings(p, opts, ov);
          else if (kind === 'multitest') body = rMultitest(p, opts, ov, renderParts);
          else body = rText(p);
        } catch (e) { console.error(e); body = h('div', { class: 'tone-danger small' }, `Nie udało się narysować: ${e.message}`); }
        if (kind === 'values') { wrap.append(h('div', { class: 'ins-part values' }, body)); continue; }
        const lines = p.lines ? (p.lines[0] === p.lines[1] ? `linia ${p.lines[0] + 1}` : `linie ${p.lines[0] + 1}–${p.lines[1] + 1}`) : '';
        wrap.append(h('div', { class: `ins-part k-${kind}` },
          h('div', { class: 'ins-part-head' }, icon(KIND_ICONS[kind] || 'table', 15), h('b', null, p.label || kind), h('span', { class: 'small muted' }, lines)),
          body));
      }
      return wrap;
    }

    function outCard(ov) {
      if (!ov || !last.output_parsed) return null;
      const op = last.output_parsed;
      return h('div', { class: 'ins-part k-output' }, h('div', { class: 'ins-part-head' }, icon('file-text', 15), h('b', null, 'Wyjście'),
        h('span', { class: 'small muted' }, `${op.lines} linii · ${op.tokens} tokenów`), h('span', { class: 'grow' }), badge(ov.label, 'accent')),
      h('pre', { class: 'ins-text mono' }, outEl.value.slice(0, 4000)));
    }

    view._set = (inp, out) => {
      if (inp !== undefined) { inEl.value = inp; local.set('inspector:input', inp); }
      if (out !== undefined) { outEl.value = out; local.set('inspector:output', out); }
      chosen = null;
      analyze();
    };
    analyze(true);
  },

  onShow(params = {}) {
    if (this._set && (params.input !== undefined || params.output !== undefined)) this._set(params.input, params.output);
  },
};

const KIND_ICONS = { array: 'binary', matrix: 'grid-3x3', grid: 'grid-3x3', graph: 'network', tree: 'git-branch', intervals: 'list-checks',
  points: 'target', queries: 'list-checks', table: 'table', strings: 'file-text', multitest: 'layers', text: 'file-text' };

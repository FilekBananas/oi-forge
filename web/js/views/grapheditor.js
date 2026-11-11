// Edytor grafów: rysujesz graf myszką, a obok na żywo powstaje tekst wejścia (lista krawędzi, sąsiedztwa, rodzice…).
import {
  h, icon, button, iconButton, viewHeader, card, badge, toast, segmented, select, checkbox, textarea, input, clear,
  copyButton, debounce, kbd, callout,
} from '../ui.js';
import { api, local } from '../api.js';

const NS = 'http://www.w3.org/2000/svg';
const R = 17;                 // promień wierzchołka (jednostki SVG)
const MAX_DRAW = 600;         // powyżej tylu wierzchołków nie rysujemy (tylko tekst)

function svgEl(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  for (const k of kids) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}

// ------------------------------------------------------------------------------------------- analiza grafu
function adjacency(g, undirected = true) {
  const adj = g.nodes.map(() => []);
  g.edges.forEach((e, i) => {
    adj[e.u].push({ v: e.v, i });
    if (undirected || !g.directed) { if (e.u !== e.v) adj[e.v].push({ v: e.u, i }); }
  });
  return adj;
}

function analyse(g) {
  const n = g.nodes.length, m = g.edges.length;
  const par = [...Array(n).keys()];
  const find = (x) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  let comps = n;
  for (const e of g.edges) { const a = find(e.u), b = find(e.v); if (a !== b) { par[a] = b; comps--; } }
  const connected = n <= 1 || comps === 1;
  let acyclic;
  if (g.directed) {
    const indeg = new Array(n).fill(0);
    const out = g.nodes.map(() => []);
    for (const e of g.edges) { out[e.u].push(e.v); indeg[e.v]++; }
    const st = []; indeg.forEach((d, v) => { if (!d) st.push(v); });
    let seen = 0;
    while (st.length) { const v = st.pop(); seen++; for (const w of out[v]) if (--indeg[w] === 0) st.push(w); }
    acyclic = seen === n;
  } else acyclic = m === n - comps;
  const adj = adjacency(g, true);
  const col = new Array(n).fill(-1);
  let bip = true;
  for (let s = 0; s < n && bip; s++) {
    if (col[s] >= 0) continue;
    col[s] = 0;
    const st = [s];
    while (st.length && bip) {
      const v = st.pop();
      for (const { v: w } of adj[v]) {
        if (col[w] < 0) { col[w] = col[v] ^ 1; st.push(w); } else if (col[w] === col[v]) { bip = false; break; }
      }
    }
  }
  const deg = g.nodes.map((_, v) => adj[v].length);
  return { n, m, comps, connected, acyclic, tree: connected && m === n - 1 && n > 0, bipartite: bip,
    minDeg: n ? Math.min(...deg) : 0, maxDeg: n ? Math.max(...deg) : 0 };
}

function parentsOf(g, root) {
  const n = g.nodes.length;
  const adj = adjacency(g, true);
  const par = new Array(n).fill(-2);
  par[root] = -1;
  const q = [root];
  for (let h0 = 0; h0 < q.length; h0++) { const v = q[h0]; for (const { v: w } of adj[v]) if (par[w] === -2) { par[w] = v; q.push(w); } }
  return par;
}

// ------------------------------------------------------------------------------------------- tekst wejścia
export function graphToText(g, format, base = 1, root = 0) {
  const n = g.nodes.length, m = g.edges.length;
  const L = (i) => i + base;
  const wt = (e) => (g.weighted ? ` ${e.w ?? 1}` : '');
  const info = analyse(g);
  if (format === 'edges') return { text: [`${n} ${m}`, ...g.edges.map((e) => `${L(e.u)} ${L(e.v)}${wt(e)}`)].join('\n') + '\n' };
  if (format === 'tree') {
    if (!info.tree) return { text: '', error: 'To nie jest drzewo — potrzeba spójnego grafu z dokładnie n−1 krawędziami.' };
    return { text: [`${n}`, ...g.edges.map((e) => `${L(e.u)} ${L(e.v)}${wt(e)}`)].join('\n') + '\n' };
  }
  if (format === 'adj') {
    const lines = [`${n}`];
    for (let v = 0; v < n; v++) {
      const nb = [];
      g.edges.forEach((e) => {
        if (e.u === v) nb.push(g.weighted ? `${L(e.v)} ${e.w ?? 1}` : `${L(e.v)}`);
        else if (!g.directed && e.v === v) nb.push(g.weighted ? `${L(e.u)} ${e.w ?? 1}` : `${L(e.u)}`);
      });
      lines.push([nb.length, ...nb].join(' '));
    }
    return { text: lines.join('\n') + '\n', note: g.weighted ? 'Każda linia: stopień, a potem pary „sąsiad waga”.' : 'Każda linia: stopień, a potem sąsiedzi.' };
  }
  if (format === 'parents') {
    if (!info.tree) return { text: '', error: 'Tablica rodziców wymaga drzewa (spójny graf, n−1 krawędzi).' };
    const r = Math.min(Math.max(root, 0), n - 1);
    const par = parentsOf(g, r);
    if (r === 0) {
      const vals = par.slice(1).map((p) => L(p));
      return { text: `${n}\n${vals.join(' ')}\n`, note: `Korzeń: ${L(0)}. Druga linia: rodzice wierzchołków ${L(1)}…${L(n - 1)}.` };
    }
    const vals = par.map((p) => (p < 0 ? (base ? 0 : -1) : L(p)));
    return { text: `${n}\n${vals.join(' ')}\n`, note: `Korzeń: ${L(r)} (ma rodzica ${base ? 0 : -1}). Druga linia: rodzic każdego wierzchołka.` };
  }
  if (format === 'matrix') {
    const M = g.nodes.map(() => new Array(n).fill(0));
    for (const e of g.edges) { const val = g.weighted ? (e.w ?? 1) : 1; M[e.u][e.v] = val; if (!g.directed) M[e.v][e.u] = val; }
    return { text: [`${n}`, ...M.map((r) => r.join(' '))].join('\n') + '\n' };
  }
  return { text: '' };
}

/** Wczytuje graf z tekstu: „n m” + krawędzie, „n” + n−1 krawędzi, „n” + rodzice, listy sąsiedztwa, macierz, same krawędzie. */
export function parseGraphText(txt) {
  const rows = txt.replace(/\r/g, '').split('\n').map((l) => l.trim()).filter(Boolean).map((l) => l.split(/[\s,;]+/).filter(Boolean));
  if (!rows.length) throw new Error('Pusty tekst.');
  const nums = rows.map((r) => r.map(Number));
  if (nums.some((r) => r.some((x) => !Number.isInteger(x)))) throw new Error('Tekst zawiera coś innego niż liczby całkowite.');
  const first = nums[0];
  let n = null, edges = [], kind = '', directedHint = false;
  const isEdgeLine = (r) => r.length === 2 || r.length === 3;
  if (first.length === 2 && nums.length - 1 >= first[1] && nums.slice(1, 1 + first[1]).every(isEdgeLine) && first[1] >= 0) {
    n = first[0];
    edges = nums.slice(1, 1 + first[1]);
    kind = `„n m” + ${first[1]} krawędzi`;
  } else if (first.length === 1 && nums.length === 2 && nums[1].length === first[0] - 1) {
    n = first[0];
    const p = nums[1];
    const base = p.some((x) => x === 0) ? 0 : 1;
    edges = p.map((x, i) => [x, i + 1 + base]);
    kind = 'tablica rodziców';
  } else if (first.length === 1 && nums.length === 2 && nums[1].length === first[0] && nums[1].filter((x) => x <= 0).length === 1) {
    n = first[0];
    const p = nums[1];
    const root = p.findIndex((x) => x <= 0);
    const base = root >= 0 && p[root] === -1 ? 0 : 1;
    p.forEach((x, i) => { if (x > 0 || (base === 0 && x >= 0)) edges.push([x, i + base]); });
    kind = 'tablica rodziców (z korzeniem)';
  } else if (first.length === 1 && nums.length - 1 === first[0] - 1 && nums.slice(1).every(isEdgeLine)) {
    n = first[0];
    edges = nums.slice(1);
    kind = 'drzewo („n” + n−1 krawędzi)';
  } else if (first.length === 1 && nums.length - 1 === first[0] && nums.slice(1).every((r) => r.length === first[0])) {
    n = first[0];
    const M = nums.slice(1);
    const sym = M.every((r, i) => r.every((x, j) => x === M[j][i]));
    directedHint = !sym;
    const weighted = M.some((r) => r.some((x) => x !== 0 && x !== 1));
    for (let i = 0; i < n; i++) for (let j = sym ? i + 1 : 0; j < n; j++) if (M[i][j] && i !== j) edges.push(weighted ? [i + 1, j + 1, M[i][j]] : [i + 1, j + 1]);
    kind = 'macierz sąsiedztwa';
  } else if (first.length === 1 && nums.length - 1 === first[0] && nums.slice(1).every((r) => r.length === r[0] + 1 || r.length === 2 * r[0] + 1)) {
    n = first[0];
    const weighted = nums.slice(1).some((r) => r.length === 2 * r[0] + 1 && r[0] > 0);
    const seen = new Set();
    nums.slice(1).forEach((r, i) => {
      const step = weighted ? 2 : 1;
      for (let k = 1; k < r.length; k += step) {
        const a = i + 1, b = r[k];
        const key = Math.min(a, b) + ',' + Math.max(a, b);
        if (seen.has(key)) { directedHint = directedHint || false; continue; }
        seen.add(key);
        edges.push(weighted ? [a, b, r[k + 1]] : [a, b]);
      }
    });
    kind = 'listy sąsiedztwa';
  } else if (nums.every(isEdgeLine)) {
    edges = nums;
    kind = 'lista krawędzi';
  } else {
    throw new Error('Nie rozpoznaję formatu. Obsługiwane: „n m” + krawędzie, „n” + n−1 krawędzi, „n” + rodzice, listy sąsiedztwa, macierz.');
  }
  const labels = edges.flatMap((e) => [e[0], e[1]]);
  const base = labels.some((x) => x === 0) ? 0 : 1;
  if (n === null) n = labels.length ? Math.max(...labels) + (base === 0 ? 1 : 0) : 0;
  if (n > MAX_DRAW) throw new Error(`Za duży graf do rysowania (n = ${n}, limit ${MAX_DRAW}).`);
  const out = [];
  for (const e of edges) {
    const u = e[0] - base, v = e[1] - base;
    if (u < 0 || v < 0 || u >= n || v >= n) throw new Error(`Krawędź ${e[0]} ${e[1]} wychodzi poza wierzchołki ${base}..${n - 1 + base}.`);
    if (u === v) continue;
    out.push({ u, v, w: e.length === 3 ? e[2] : 1 });
  }
  return { n, edges: out, weighted: edges.some((e) => e.length === 3), base, kind, directedHint };
}

// ------------------------------------------------------------------------------------------- układy
function forceLayout(g, W, H, iters = 300) {
  const n = g.nodes.length;
  if (!n) return [];
  const pos = g.nodes.map((p) => ({ x: p.x, y: p.y }));
  const spread = new Set(pos.map((p) => `${Math.round(p.x / 5)},${Math.round(p.y / 5)}`)).size;
  if (spread < Math.max(2, n * 0.6)) pos.forEach((p, i) => { const a = (2 * Math.PI * i) / n; p.x = W / 2 + (W / 3) * Math.cos(a) + Math.random() * 10; p.y = H / 2 + (H / 3) * Math.sin(a) + Math.random() * 10; });
  const k = Math.sqrt((W * H) / n) * 0.75;
  const it = n > 300 ? 120 : iters;
  const frames = [];
  for (let s = 0; s < it; s++) {
    const t = (Math.max(W, H) / 8) * (1 - s / it) + 1;
    const dx = new Float64Array(n), dy = new Float64Array(n);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      let ex = pos[i].x - pos[j].x, ey = pos[i].y - pos[j].y;
      let d = Math.hypot(ex, ey);
      if (d < 0.01) { ex = Math.random() - 0.5; ey = Math.random() - 0.5; d = 0.5; }
      const f = (k * k) / d;
      dx[i] += (ex / d) * f; dy[i] += (ey / d) * f; dx[j] -= (ex / d) * f; dy[j] -= (ey / d) * f;
    }
    for (const e of g.edges) {
      const ex = pos[e.u].x - pos[e.v].x, ey = pos[e.u].y - pos[e.v].y;
      const d = Math.max(0.01, Math.hypot(ex, ey));
      const f = (d * d) / k;
      dx[e.u] -= (ex / d) * f; dy[e.u] -= (ey / d) * f; dx[e.v] += (ex / d) * f; dy[e.v] += (ey / d) * f;
    }
    for (let i = 0; i < n; i++) {
      dx[i] += (W / 2 - pos[i].x) * 0.04 * k / 40; dy[i] += (H / 2 - pos[i].y) * 0.04 * k / 40;
      const d = Math.hypot(dx[i], dy[i]);
      if (d > 0) { pos[i].x += (dx[i] / d) * Math.min(d, t); pos[i].y += (dy[i] / d) * Math.min(d, t); }
    }
    if (s % 12 === 11 || s === it - 1) frames.push(pos.map((p) => ({ x: p.x, y: p.y })));
  }
  return frames;
}

function treeLayout(g, root, W) {
  const n = g.nodes.length;
  const adj = adjacency(g, true);
  const depth = new Array(n).fill(-1);
  const children = g.nodes.map(() => []);
  const order = [];
  const roots = [];
  const tryRoot = (r) => {
    if (depth[r] >= 0) return;
    roots.push(r); depth[r] = 0;
    const q = [r];
    for (let i = 0; i < q.length; i++) {
      const v = q[i]; order.push(v);
      for (const { v: w } of adj[v]) if (depth[w] < 0) { depth[w] = depth[v] + 1; children[v].push(w); q.push(w); }
    }
  };
  tryRoot(Math.min(Math.max(root, 0), n - 1));
  for (let v = 0; v < n; v++) tryRoot(v);
  const x = new Array(n).fill(0);
  let leaf = 0;
  const place = (v) => {
    if (!children[v].length) { x[v] = leaf++; return; }
    for (const c of children[v]) place(c);
    x[v] = (x[children[v][0]] + x[children[v][children[v].length - 1]]) / 2;
  };
  for (const r of roots) { place(r); leaf += 0.6; }
  const maxD = Math.max(...depth, 0);
  const gapX = Math.max(46, Math.min(90, (W - 80) / Math.max(1, leaf)));
  const gapY = Math.max(64, Math.min(100, 440 / Math.max(1, maxD)));
  return g.nodes.map((_, v) => ({ x: 50 + x[v] * gapX, y: 50 + depth[v] * gapY }));
}

function circleLayout(n, W, H) {
  const r = Math.max(80, Math.min(W, H) / 2 - 50);
  return Array.from({ length: n }, (_, i) => ({ x: W / 2 + r * Math.cos((2 * Math.PI * i) / n - Math.PI / 2), y: H / 2 + r * Math.sin((2 * Math.PI * i) / n - Math.PI / 2) }));
}

// ------------------------------------------------------------------------------------------- widok
let VIEW = null;

export default {
  id: 'grapheditor', title: 'Edytor grafów', icon: 'network', group: 'Testowanie', order: 45, layout: 'full',
  description: 'Narysuj graf myszką — obok na żywo powstaje wejście: „n m” + krawędzie, lista sąsiedztwa, tablica rodziców. Wklej wejście, a graf się narysuje.',
  keywords: ['graf', 'drzewo', 'rysowanie', 'krawędzie', 'wierzchołki', 'wejście', 'test', 'edytor'],

  render(root, ctx) {
    const saved = local.get('grapheditor:state', null);
    const g = saved && Array.isArray(saved.nodes) ? { nodes: saved.nodes, edges: saved.edges || [], directed: !!saved.directed, weighted: !!saved.weighted }
      : { nodes: [], edges: [], directed: false, weighted: false };
    const ui = {
      base: saved?.base ?? 1, format: saved?.format || 'edges', tool: 'edge', root: saved?.root ?? 0, layout: saved?.layout || 'auto',
      sel: null, hover: -1, drag: null, undo: [], redo: [], vb: { x: 0, y: 0, w: 900, h: 560 }, gridShape: null,
    };
    VIEW = { root, g, ui };

    // ----------------------------------------------------------------------- historia
    const snap = () => JSON.stringify({ nodes: g.nodes, edges: g.edges, directed: g.directed, weighted: g.weighted, root: ui.root });
    const restore = (s) => { const o = JSON.parse(s); g.nodes = o.nodes; g.edges = o.edges; g.directed = o.directed; g.weighted = o.weighted; ui.root = o.root || 0; ui.sel = null; syncToggles(); changed(); };
    const push = (s = snap()) => { ui.undo.push(s); if (ui.undo.length > 200) ui.undo.shift(); ui.redo = []; updateUndo(); };
    const undo = () => { if (!ui.undo.length) return; ui.redo.push(snap()); restore(ui.undo.pop()); updateUndo(); };
    const redo = () => { if (!ui.redo.length) return; ui.undo.push(snap()); restore(ui.redo.pop()); updateUndo(); };

    // ----------------------------------------------------------------------- płótno
    const svg = svgEl('svg', { class: 'ge-canvas', role: 'img', 'aria-label': 'Płótno grafu' });
    const defs = svgEl('defs', {},
      svgEl('marker', { id: 'ge-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, svgEl('path', { d: 'M0,0 L10,5 L0,10 z', class: 'ge-arrow-head' })),
      svgEl('marker', { id: 'ge-arrow-sel', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, svgEl('path', { d: 'M0,0 L10,5 L0,10 z', class: 'ge-arrow-head sel' })));
    const gEdges = svgEl('g', { class: 'ge-edges' });
    const rubber = svgEl('line', { class: 'ge-rubber', visibility: 'hidden' });
    const gNodes = svgEl('g', { class: 'ge-nodes' });
    svg.append(defs, gEdges, rubber, gNodes);
    const wInput = h('input', { class: 'input ge-weight-input mono', type: 'text', inputmode: 'numeric', hidden: true, spellcheck: 'false' });
    const emptyHint = h('div', { class: 'ge-empty' }, icon('network', 26), h('div', { class: 'bold' }, 'Kliknij, aby dodać wierzchołek'),
      h('div', { class: 'muted small' }, 'Przeciągnij z wierzchołka na wierzchołek, aby dodać krawędź. Albo wygeneruj losowe drzewo / graf po prawej.'));
    const hint = h('div', { class: 'ge-hint' });
    const wrap = h('div', { class: 'ge-canvas-wrap' }, svg, wInput, emptyHint, hint);

    const toolSeg = segmented([{ value: 'edge', label: 'Krawędzie', icon: 'git-branch', title: 'Przeciągnij z wierzchołka na wierzchołek (E)' }, { value: 'move', label: 'Przesuwanie', icon: 'maximize-2', title: 'Przeciągaj wierzchołki (M)' }],
      { value: ui.tool, onChange: (v) => { ui.tool = v; updateHint(); } });
    const undoBtn = iconButton('rotate-ccw', { title: 'Cofnij (Ctrl+Z)', onClick: undo });
    const redoBtn = iconButton('rotate-ccw', { title: 'Ponów (Ctrl+Y)', onClick: redo });
    redoBtn.classList.add('ge-redo');
    const layoutSel = select([{ value: 'auto', label: 'Auto' }, { value: 'force', label: 'Siłowy' }, { value: 'tree', label: 'Drzewo (warstwy)' }, { value: 'circle', label: 'Okrąg' }],
      { value: ui.layout, size: 'sm', onChange: (v) => { ui.layout = v; persist(); } });
    layoutSel.style.width = 'auto';
    const countBadge = h('span', { class: 'ge-count' });
    const canvasBar = h('div', { class: 'ge-bar' }, toolSeg, undoBtn, redoBtn, h('span', { class: 'ge-sep' }),
      button('Auto-układ', { icon: 'sparkles', size: 'sm', onClick: () => autoLayout(), title: 'Rozmieść wierzchołki automatycznie' }), layoutSel,
      iconButton('maximize-2', { title: 'Dopasuj widok', onClick: () => fitView() }),
      h('div', { class: 'grow' }), countBadge,
      button('Wyczyść', { icon: 'trash-2', size: 'sm', variant: 'ghost', onClick: () => { if (!g.nodes.length) return; push(); g.nodes = []; g.edges = []; ui.sel = null; ui.root = 0; changed(); toast('Wyczyszczono (Ctrl+Z cofa)', 'info', { timeout: 1500 }); } }));

    // ----------------------------------------------------------------------- panel boczny
    const dirCb = checkbox('Skierowany', { checked: g.directed, onChange: (v) => { push(); g.directed = v; changed(); } });
    const wCb = checkbox('Ważony', { checked: g.weighted, onChange: (v) => { push(); g.weighted = v; g.edges.forEach((e) => { if (e.w === undefined || e.w === null) e.w = 1; }); changed(); } });
    const baseSeg = segmented([{ value: 1, label: 'od 1' }, { value: 0, label: 'od 0' }], { value: ui.base, onChange: (v) => { ui.base = +v; changed(false); } });
    function syncToggles() { dirCb.checked = g.directed; wCb.checked = g.weighted; baseSeg.setValue(ui.base); }

    const nInp = input({ value: String(local.get('grapheditor:n', 8)), size: 'sm', mono: true, attrs: { inputmode: 'numeric', title: 'Liczba wierzchołków' } });
    const mInp = input({ value: String(local.get('grapheditor:m', 11)), size: 'sm', mono: true, attrs: { inputmode: 'numeric', title: 'Liczba krawędzi (graf, DAG)' } });
    const shapeSel = select([{ value: 'random', label: 'losowe' }, { value: 'line', label: 'ścieżka' }, { value: 'star', label: 'gwiazda' }, { value: 'binary', label: 'binarne' },
      { value: 'caterpillar', label: 'gąsienica' }, { value: 'parent', label: 'płytkie' }, { value: 'deep', label: 'głębokie' }], { value: local.get('grapheditor:shape', 'random'), size: 'sm' });
    const genCard = card({ title: 'Losowe struktury', icon: 'shuffle', body: h('div', { class: 'stack gap-s' },
      h('div', { class: 'ge-gen-row' },
        h('label', { class: 'ge-lbl' }, 'n', nInp), h('label', { class: 'ge-lbl' }, 'm', mInp), h('label', { class: 'ge-lbl' }, 'kształt', shapeSel)),
      h('div', { class: 'row gap-s' },
        button('Losowe drzewo', { icon: 'list-tree', size: 'sm', onClick: () => generate('tree') }),
        button('Losowy graf', { icon: 'network', size: 'sm', onClick: () => generate(g.directed ? 'graph_dir' : 'graph') }),
        button('DAG', { icon: 'workflow', size: 'sm', onClick: () => generate('dag') }),
        button('Siatka', { icon: 'grid-3x3', size: 'sm', onClick: () => generate('grid_graph'), title: 'Siatka ≈ n wierzchołków (prawie kwadratowa)' }))) });

    const fmtSel = select([{ value: 'edges', label: '„n m” + lista krawędzi' }, { value: 'tree', label: 'Drzewo: „n” + n−1 krawędzi' }, { value: 'adj', label: 'Listy sąsiedztwa' },
      { value: 'parents', label: 'Tablica rodziców' }, { value: 'matrix', label: 'Macierz sąsiedztwa' }], { value: ui.format, size: 'sm', onChange: (v) => { ui.format = v; changed(false); } });
    const outArea = textarea({ rows: 12, mono: true, attrs: { readonly: true, spellcheck: 'false' } });
    outArea.classList.add('ge-out');
    const outNote = h('div', { class: 'muted small' });
    const props = h('div', { class: 'ge-props' });
    const outCard = card({ title: 'Wejście (tekst)', icon: 'file-text', actions: [copyButton(() => outArea.value, { label: 'Kopiuj' })],
      body: h('div', { class: 'stack gap-s' }, h('div', { class: 'row gap-s' }, fmtSel, baseSeg), h('div', { class: 'row gap-l' }, dirCb, wCb), outArea, outNote, props) });

    const impArea = textarea({ rows: 6, mono: true, placeholder: '4 3\n1 2\n2 3\n2 4' });
    const impCard = card({ title: 'Import z tekstu', icon: 'clipboard-paste', body: h('div', { class: 'stack gap-s' }, impArea,
      h('div', { class: 'row gap-s' }, button('Narysuj', { icon: 'play', size: 'sm', variant: 'primary', onClick: () => importText() }),
        h('span', { class: 'muted small' }, 'Wklej wejście zadania — rozpoznaję format sam.'))) });

    const helpCard = card({ title: 'Sterowanie', icon: 'keyboard', body: h('div', { class: 'ge-help' },
      h('div', null, h('b', null, 'Klik w puste'), ' — nowy wierzchołek'),
      h('div', null, h('b', null, 'Przeciągnij z wierzchołka'), ' na inny — krawędź; w puste — nowy wierzchołek z krawędzią'),
      h('div', null, kbd('Shift'), ' + przeciągnij — przesuwanie (albo tryb „Przesuwanie”)'),
      h('div', null, h('b', null, 'Klik w krawędź'), ' — zaznaczenie / edycja wagi'),
      h('div', null, h('b', null, 'Dwuklik w wierzchołek'), ' — korzeń (tablica rodziców, układ drzewa)'),
      h('div', null, kbd('Delete'), ' usuń · ', kbd('Ctrl'), '+', kbd('Z'), ' / ', kbd('Ctrl'), '+', kbd('Y'), ' cofnij / ponów · kółko — zoom, przeciągnij tło — przesuń')) });

    const side = h('div', { class: 'ge-side' }, outCard, genCard, impCard, helpCard);
    root.append(viewHeader(this), h('div', { class: 'ge-layout' }, h('div', { class: 'ge-main' }, canvasBar, wrap), side));

    // ----------------------------------------------------------------------- rysowanie
    const L = (i) => i + ui.base;
    function setViewBox() { svg.setAttribute('viewBox', `${ui.vb.x} ${ui.vb.y} ${ui.vb.w} ${ui.vb.h}`); }

    function edgeGeom(e) {
      const a = g.nodes[e.u], b = g.nodes[e.v];
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.max(1, Math.hypot(dx, dy));
      const curved = g.directed && g.edges.some((f) => f.u === e.v && f.v === e.u);
      let cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      if (curved) { const off = Math.min(60, len * 0.22); cx += (-dy / len) * off; cy += (dx / len) * off; }
      const ux = cx - a.x, uy = cy - a.y, ul = Math.max(1, Math.hypot(ux, uy));
      const vx = b.x - cx, vy = b.y - cy, vl = Math.max(1, Math.hypot(vx, vy));
      const sx = a.x + (ux / ul) * R, sy = a.y + (uy / ul) * R;
      const ex = b.x - (vx / vl) * (R + (g.directed ? 3 : 0)), ey = b.y - (vy / vl) * (R + (g.directed ? 3 : 0));
      const d = curved ? `M${sx},${sy} Q${cx},${cy} ${ex},${ey}` : `M${sx},${sy} L${ex},${ey}`;
      const mx = curved ? 0.25 * a.x + 0.5 * cx + 0.25 * b.x : (a.x + b.x) / 2;
      const my = curved ? 0.25 * a.y + 0.5 * cy + 0.25 * b.y : (a.y + b.y) / 2;
      return { d, mx, my };
    }

    function draw() {
      clear(gEdges); clear(gNodes);
      emptyHint.hidden = g.nodes.length > 0;
      if (g.nodes.length > MAX_DRAW) return;
      g.edges.forEach((e, i) => {
        const { d, mx, my } = edgeGeom(e);
        const sel = ui.sel && ui.sel.type === 'edge' && ui.sel.i === i;
        const grp = svgEl('g', { class: ['ge-edge-g', sel ? 'sel' : ''].join(' '), 'data-edge': i },
          svgEl('path', { d, class: 'ge-edge-hit' }),
          svgEl('path', { d, class: ['ge-edge', sel ? 'sel' : ''].join(' '), 'marker-end': g.directed ? (sel ? 'url(#ge-arrow-sel)' : 'url(#ge-arrow)') : null }));
        if (g.weighted) {
          const t = String(e.w ?? 1);
          const wdt = 10 + t.length * 7.2;
          grp.append(svgEl('g', { class: 'ge-wlabel' }, svgEl('rect', { x: mx - wdt / 2, y: my - 9.5, width: wdt, height: 19, rx: 6 }),
            svgEl('text', { x: mx, y: my + 0.5 }, t)));
        }
        gEdges.append(grp);
      });
      g.nodes.forEach((p, i) => {
        const sel = ui.sel && ui.sel.type === 'node' && ui.sel.i === i;
        const grp = svgEl('g', { class: ['ge-node', sel ? 'sel' : '', ui.hover === i ? 'hover' : '', ui.root === i && g.nodes.length > 1 ? 'root' : ''].join(' '),
          'data-node': i, transform: `translate(${p.x},${p.y})` });
        if (ui.root === i && g.nodes.length > 1) grp.append(svgEl('circle', { r: R + 5, class: 'ge-root-ring' }));
        grp.append(svgEl('circle', { r: R }), svgEl('text', { y: 0.5 }, L(i)));
        gNodes.append(grp);
      });
    }

    function updateOutput() {
      const r = graphToText(g, ui.format, ui.base, ui.root);
      outArea.value = r.error ? '' : r.text;
      outArea.placeholder = r.error || '';
      outArea.classList.toggle('err', !!r.error);
      outNote.textContent = r.error || r.note || '';
      outNote.className = r.error ? 'small tone-warn' : 'muted small';
      const a = analyse(g);
      clear(props);
      const chip = (ok, yes, no) => h('span', { class: ['chip', ok ? 'ok' : 'no'].join(' ') }, icon(ok ? 'check' : 'x', 12), ok ? yes : no);
      props.append(
        chip(a.connected, g.directed ? 'słabo spójny' : 'spójny', `${a.comps} ${a.comps >= 2 && a.comps <= 4 ? 'składowe' : 'składowych'}`),
        chip(a.tree, 'drzewo', 'nie drzewo'),
        chip(a.acyclic, g.directed ? 'DAG (acykliczny)' : 'acykliczny (las)', 'ma cykl'),
        chip(a.bipartite, 'dwudzielny', 'niedwudzielny'),
        h('span', { class: 'chip' }, `stopnie ${a.minDeg}…${a.maxDeg}`));
      countBadge.replaceChildren(badge(`n = ${a.n}`, 'accent'), ' ', badge(`m = ${a.m}`, 'neutral'));
    }

    const persist = debounce(() => local.set('grapheditor:state', { nodes: g.nodes, edges: g.edges, directed: g.directed, weighted: g.weighted, base: ui.base, format: ui.format, root: ui.root, layout: ui.layout }), 300);
    function changed() { if (ui.root >= g.nodes.length) ui.root = 0; draw(); updateOutput(); persist(); }
    function updateUndo() { undoBtn.disabled = !ui.undo.length; redoBtn.disabled = !ui.redo.length; }
    function updateHint() {
      hint.textContent = ui.tool === 'edge' ? 'Tryb: krawędzie — przeciągnij z wierzchołka (Shift = przesuń)' : 'Tryb: przesuwanie — przeciągaj wierzchołki (Shift = krawędź)';
    }

    // ----------------------------------------------------------------------- współrzędne
    function toSvg(ev) {
      const pt = svg.createSVGPoint();
      pt.x = ev.clientX; pt.y = ev.clientY;
      const m = svg.getScreenCTM();
      return m ? pt.matrixTransform(m.inverse()) : { x: 0, y: 0 };
    }
    function toClient(x, y) {
      const pt = svg.createSVGPoint();
      pt.x = x; pt.y = y;
      const m = svg.getScreenCTM();
      const p = m ? pt.matrixTransform(m) : { x: 0, y: 0 };
      const r = wrap.getBoundingClientRect();
      return { x: p.x - r.left, y: p.y - r.top };
    }
    function nodeAt(p, exclude = -1) {
      let best = -1, bd = (R + 6) ** 2;
      g.nodes.forEach((q, i) => { if (i === exclude) return; const d = (q.x - p.x) ** 2 + (q.y - p.y) ** 2; if (d < bd) { bd = d; best = i; } });
      return best;
    }

    // ----------------------------------------------------------------------- operacje
    function addNode(p) { g.nodes.push({ x: Math.round(p.x), y: Math.round(p.y) }); return g.nodes.length - 1; }
    function hasEdge(u, v) { return g.edges.some((e) => (e.u === u && e.v === v) || (!g.directed && e.u === v && e.v === u)); }
    function addEdge(u, v, quiet = false) {
      if (u === v) { if (!quiet) toast('Pętle własne nie są obsługiwane.', 'info', { timeout: 1500 }); return false; }
      if (hasEdge(u, v)) { if (!quiet) toast('Taka krawędź już istnieje.', 'info', { timeout: 1500 }); return false; }
      g.edges.push({ u, v, w: 1 });
      return true;
    }
    function deleteSel() {
      if (!ui.sel) return;
      push();
      if (ui.sel.type === 'edge') g.edges.splice(ui.sel.i, 1);
      else {
        const i = ui.sel.i;
        g.nodes.splice(i, 1);
        g.edges = g.edges.filter((e) => e.u !== i && e.v !== i).map((e) => ({ ...e, u: e.u > i ? e.u - 1 : e.u, v: e.v > i ? e.v - 1 : e.v }));
        if (ui.root === i) ui.root = 0; else if (ui.root > i) ui.root--;
      }
      ui.sel = null;
      changed();
    }

    function editWeight(i) {
      const e = g.edges[i];
      if (!e) return;
      const { mx, my } = edgeGeom(e);
      const c = toClient(mx, my);
      wInput.hidden = false;
      wInput.value = String(e.w ?? 1);
      wInput.style.left = `${c.x - 38}px`;
      wInput.style.top = `${c.y - 16}px`;
      wInput.dataset.edge = i;
      setTimeout(() => { wInput.focus(); wInput.select(); }, 0);
    }
    function commitWeight(save) {
      if (wInput.hidden) return;
      const i = +wInput.dataset.edge;
      wInput.hidden = true;
      if (!save || !g.edges[i]) return;
      const v = wInput.value.trim();
      if (!/^-?\d{1,18}$/.test(v)) { toast('Waga musi być liczbą całkowitą.', 'warn', { timeout: 1800 }); return; }
      if (String(g.edges[i].w) === v) return;
      push();
      g.edges[i].w = parseInt(v, 10);
      changed();
    }
    wInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commitWeight(true); } else if (e.key === 'Escape') { e.preventDefault(); commitWeight(false); } e.stopPropagation(); });
    wInput.addEventListener('blur', () => commitWeight(true));

    // ----------------------------------------------------------------------- mysz
    svg.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      commitWeight(true);
      const p = toSvg(ev);
      const nodeEl = ev.target.closest('[data-node]');
      const edgeEl = ev.target.closest('[data-edge]');
      svg.setPointerCapture(ev.pointerId);
      if (nodeEl) {
        const i = +nodeEl.getAttribute('data-node');
        const mode = (ui.tool === 'edge') !== ev.shiftKey ? 'edge' : 'move';
        ui.drag = { kind: 'node', i, mode, cx: ev.clientX, cy: ev.clientY, moved: false, before: snap(), off: { x: g.nodes[i].x - p.x, y: g.nodes[i].y - p.y } };
      } else if (edgeEl) {
        ui.drag = { kind: 'edge', i: +edgeEl.getAttribute('data-edge'), cx: ev.clientX, cy: ev.clientY, moved: false };
      } else {
        ui.drag = { kind: 'pan', cx: ev.clientX, cy: ev.clientY, moved: false, vb0: { ...ui.vb }, p };
      }
    });
    svg.addEventListener('pointermove', (ev) => {
      const d = ui.drag;
      if (!d) return;
      if (!d.moved && Math.hypot(ev.clientX - d.cx, ev.clientY - d.cy) > 4) d.moved = true;
      if (!d.moved) return;
      const p = toSvg(ev);
      if (d.kind === 'node' && d.mode === 'move') {
        g.nodes[d.i].x = Math.round(p.x + d.off.x);
        g.nodes[d.i].y = Math.round(p.y + d.off.y);
        draw();
      } else if (d.kind === 'node') {
        const a = g.nodes[d.i];
        rubber.setAttribute('x1', a.x); rubber.setAttribute('y1', a.y); rubber.setAttribute('x2', p.x); rubber.setAttribute('y2', p.y);
        rubber.setAttribute('visibility', 'visible');
        const t = nodeAt(p, d.i);
        if (t !== ui.hover) { ui.hover = t; draw(); }
      } else if (d.kind === 'pan') {
        const r = svg.getBoundingClientRect();
        const sc = Math.max(ui.vb.w / r.width, ui.vb.h / r.height);
        ui.vb.x = d.vb0.x - (ev.clientX - d.cx) * sc;
        ui.vb.y = d.vb0.y - (ev.clientY - d.cy) * sc;
        setViewBox();
      }
    });
    const endDrag = (ev) => {
      const d = ui.drag;
      ui.drag = null;
      rubber.setAttribute('visibility', 'hidden');
      if (!d) return;
      const p = toSvg(ev);
      if (d.kind === 'node') {
        if (!d.moved) { ui.sel = ui.sel && ui.sel.type === 'node' && ui.sel.i === d.i ? null : { type: 'node', i: d.i }; draw(); return; }
        if (d.mode === 'move') { push(d.before); changed(); return; }
        const t = nodeAt(p, d.i);
        ui.hover = -1;
        if (t >= 0) { if (!hasEdge(d.i, t) && d.i !== t) { push(); addEdge(d.i, t); changed(); } else { addEdge(d.i, t); draw(); } }
        else { push(); const nv = addNode(p); addEdge(d.i, nv, true); changed(); }
      } else if (d.kind === 'edge') {
        if (d.moved) return;
        ui.sel = { type: 'edge', i: d.i };
        draw();
        if (g.weighted) editWeight(d.i);
      } else if (d.kind === 'pan') {
        if (d.moved) return;
        if (ui.sel) { ui.sel = null; }
        push();
        addNode(p);
        changed();
      }
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', () => { ui.drag = null; rubber.setAttribute('visibility', 'hidden'); });
    svg.addEventListener('dblclick', (ev) => {
      const nodeEl = ev.target.closest('[data-node]');
      if (nodeEl) { ui.root = +nodeEl.getAttribute('data-node'); changed(); toast(`Korzeń: ${L(ui.root)}`, 'info', { timeout: 1200 }); return; }
      const edgeEl = ev.target.closest('[data-edge]');
      if (edgeEl) { if (!g.weighted) { push(); g.weighted = true; syncToggles(); changed(); } editWeight(+edgeEl.getAttribute('data-edge')); }
    });
    svg.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const p = toSvg(ev);
      const f = Math.exp(Math.sign(ev.deltaY) * Math.min(0.25, Math.abs(ev.deltaY) / 400));
      const nw = Math.min(8000, Math.max(120, ui.vb.w * f)), nh = ui.vb.h * (nw / ui.vb.w);
      ui.vb.x = p.x - ((p.x - ui.vb.x) * nw) / ui.vb.w;
      ui.vb.y = p.y - ((p.y - ui.vb.y) * nh) / ui.vb.h;
      ui.vb.w = nw; ui.vb.h = nh;
      setViewBox();
      commitWeight(true);
    }, { passive: false });

    // ----------------------------------------------------------------------- klawiatura
    const onKey = (e) => {
      if (root.hidden || !root.isConnected) return;
      const t = e.target;
      if (t && (t.closest?.('input, textarea, select, .CodeMirror, [contenteditable="true"]'))) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) { e.preventDefault(); redo(); }
      else if (e.key === 'Delete' || e.key === 'Backspace') { if (ui.sel) { e.preventDefault(); deleteSel(); } }
      else if (e.key === 'Escape') { ui.sel = null; commitWeight(false); draw(); }
      else if (!mod && e.key.toLowerCase() === 'e') { ui.tool = 'edge'; toolSeg.setValue('edge'); updateHint(); }
      else if (!mod && e.key.toLowerCase() === 'm') { ui.tool = 'move'; toolSeg.setValue('move'); updateHint(); }
    };
    document.addEventListener('keydown', onKey);

    // ----------------------------------------------------------------------- układy
    function canvasSize() { return { W: 900, H: 560 }; }
    function animateTo(frames) {
      if (!frames.length) return;
      let k = 0;
      const step = () => {
        const f = frames[k];
        f.forEach((p, i) => { if (g.nodes[i]) { g.nodes[i].x = Math.round(p.x); g.nodes[i].y = Math.round(p.y); } });
        draw();
        k++;
        if (k < frames.length) requestAnimationFrame(step);
        else { fitView(); changed(); }
      };
      requestAnimationFrame(step);
    }
    function autoLayout(kind = ui.layout, record = true) {
      if (!g.nodes.length) return;
      if (record) push();
      const { W, H } = canvasSize();
      const a = analyse(g);
      let mode = kind;
      if (mode === 'auto') mode = ui.gridShape && ui.gridShape.n === g.nodes.length ? 'grid' : (a.acyclic && !g.directed && g.nodes.length > 1 ? 'tree' : 'force');
      if (mode === 'grid') {
        const { cols } = ui.gridShape;
        g.nodes.forEach((p, i) => { p.x = 60 + (i % cols) * 78; p.y = 60 + Math.floor(i / cols) * 78; });
        fitView(); changed(); return;
      }
      if (mode === 'tree') { treeLayout(g, ui.root, W).forEach((p, i) => { g.nodes[i].x = Math.round(p.x); g.nodes[i].y = Math.round(p.y); }); fitView(); changed(); return; }
      if (mode === 'circle') { circleLayout(g.nodes.length, W, H).forEach((p, i) => { g.nodes[i].x = Math.round(p.x); g.nodes[i].y = Math.round(p.y); }); fitView(); changed(); return; }
      animateTo(forceLayout(g, W, H));
    }
    function fitView() {
      const r = svg.getBoundingClientRect();
      const aspect = r.width > 10 && r.height > 10 ? r.width / r.height : 900 / 560;
      if (!g.nodes.length) { ui.vb = { x: 0, y: 0, w: 900, h: 900 / aspect }; setViewBox(); return; }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const p of g.nodes) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
      const pad = 50;
      let w = Math.max(320, x1 - x0 + 2 * pad), hh = Math.max(220, y1 - y0 + 2 * pad);
      if (w / hh > aspect) hh = w / aspect; else w = hh * aspect;
      ui.vb = { x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - hh / 2, w, h: hh };
      setViewBox();
    }

    // ----------------------------------------------------------------------- generowanie / import
    async function generate(kind) {
      const n = Math.max(1, Math.min(MAX_DRAW, parseInt(nInp.value, 10) || 8));
      let m = Math.max(0, parseInt(mInp.value, 10) || n);
      local.set('grapheditor:n', n); local.set('grapheditor:m', m); local.set('grapheditor:shape', shapeSel.value);
      const body = { seed: Math.floor(Math.random() * 1e9), base: 1 };
      let directed = g.directed;
      ui.gridShape = null;
      if (kind === 'tree') Object.assign(body, { kind: 'tree', n, shape: shapeSel.value });
      else if (kind === 'graph' || kind === 'graph_dir') {
        const mx = kind === 'graph_dir' ? n * (n - 1) : (n * (n - 1)) / 2;
        m = Math.min(Math.max(m, n - 1), mx);
        Object.assign(body, { kind: 'graph', n, m, directed: kind === 'graph_dir', connected: true });
      } else if (kind === 'dag') {
        m = Math.min(m, (n * (n - 1)) / 2);
        Object.assign(body, { kind: 'dag', n, m });
        directed = true;
      } else if (kind === 'grid_graph') {
        const rows = Math.max(1, Math.round(Math.sqrt(n))), cols = Math.max(1, Math.ceil(n / rows));
        Object.assign(body, { kind: 'grid_graph', rows, cols });
        ui.gridShape = { n: rows * cols, cols };
        directed = false;
      }
      if (g.weighted) body.weights = [1, 20];
      let r;
      try { r = await api.post('/api/randstruct/generate', body); } catch { return; }
      push();
      g.directed = directed;
      g.nodes = Array.from({ length: r.n }, () => ({ x: 450, y: 280 }));
      g.edges = r.edges.map((e) => ({ u: e[0] - 1, v: e[1] - 1, w: e.length > 2 ? e[2] : 1 }));
      ui.sel = null; ui.root = 0;
      syncToggles();
      const mode = kind === 'grid_graph' ? 'grid' : kind === 'tree' ? 'tree' : 'force';
      autoLayout(ui.layout === 'auto' ? mode : ui.layout, false);
    }

    function importText() {
      let res;
      try { res = parseGraphText(impArea.value); } catch (e) { toast(e.message, 'warn'); return; }
      push();
      g.nodes = Array.from({ length: res.n }, () => ({ x: 450, y: 280 }));
      g.edges = res.edges;
      g.weighted = res.weighted;
      if (res.directedHint) g.directed = true;
      ui.base = res.base;
      ui.sel = null; ui.root = 0; ui.gridShape = null;
      syncToggles();
      const a = analyse(g);
      autoLayout(ui.layout === 'auto' ? (a.tree ? 'tree' : 'force') : ui.layout, false);
      toast(`Wczytano: ${res.kind} (n = ${res.n}, m = ${res.edges.length}, numeracja od ${res.base})`, 'success', { timeout: 2400 });
    }

    // ----------------------------------------------------------------------- start
    updateHint(); updateUndo(); syncToggles();
    setViewBox();
    changed();
    requestAnimationFrame(() => fitView());
    new ResizeObserver(() => { if (!root.hidden) { const r = svg.getBoundingClientRect(); if (r.width > 10) { const aspect = r.width / r.height; ui.vb.h = ui.vb.w / aspect; setViewBox(); } } }).observe(wrap);
    VIEW.importText = (t) => { impArea.value = t; importText(); };
    if (ctx.params && ctx.params.input) VIEW.importText(ctx.params.input);
  },

  onShow(params) {
    if (VIEW && params && (params.input || params.graph)) VIEW.importText(params.input || params.graph);
  },
};

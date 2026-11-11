// Wspólne narzędzia generatorów kroków (czysty JS, bez DOM — testowalne w node).
// Każdy algorytm zwraca {steps, data, truncated}; krok = {line, desc, vars?, panels?, ...stan dla renderera}.

export const MAX_STEPS = 3000;
export const STOP = Symbol('av-stop');

/** Kod C++ ze znacznikami `@@nazwa` na końcu linii → {code, L: {nazwa: numer_linii}}. */
export function cpp(src) {
  const L = {};
  const lines = String(src).replace(/^\n+/, '').replace(/\s+$/, '').split('\n');
  const out = lines.map((ln, i) => ln.replace(/\s*@@([\w,]+)\s*$/, (_, names) => {
    for (const n of names.split(',')) L[n] = i + 1;
    return '';
  }).replace(/\s+$/, ''));
  return { code: out.join('\n'), L };
}

const clone = typeof structuredClone === 'function' ? structuredClone : (x) => JSON.parse(JSON.stringify(x));

/** Rejestrator kroków. add(linia|nazwa, opis, stan) — stan jest kopiowany. */
export class Rec {
  constructor(code, max = MAX_STEPS) {
    this.code = code;
    this.steps = [];
    this.max = max;
    this.truncated = false;
  }
  add(line, desc, state = {}) {
    if (this.steps.length >= this.max) { this.truncated = true; throw STOP; }
    const ln = typeof line === 'string' ? (this.code.L[line] ?? 0) : (line || 0);
    this.steps.push({ line: ln, desc, ...clone(state) });
  }
  get last() { return this.steps[this.steps.length - 1]; }
}

/** Uruchamia generator, przechwytując przekroczenie limitu kroków. */
export function exec(rec, fn, data = {}) {
  try { fn(); } catch (e) { if (e !== STOP) throw e; }
  if (rec.truncated && rec.steps.length) {
    rec.steps[rec.steps.length - 1].desc += ` ⚠️ Przerwano po ${rec.max} krokach — zmniejsz dane wejściowe.`;
  }
  return { steps: rec.steps, data, truncated: rec.truncated };
}

// ------------------------------------------------------------------------------------------- losowość
export function rng(seed = Date.now()) {
  let a = (seed >>> 0) || 0x9e3779b9;
  const next = () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const int = (lo, hi) => lo + Math.floor(next() * (hi - lo + 1));
  return {
    next, int,
    pick: (arr) => arr[Math.floor(next() * arr.length)],
    shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(next() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; },
    ints: (n, lo, hi) => Array.from({ length: n }, () => int(lo, hi)),
  };
}

// ------------------------------------------------------------------------------------------- parsowanie
export class InputError extends Error {}

export function parseInts(text, { name = 'tablica', min = -999, max = 999, minLen = 1, maxLen = 16 } = {}) {
  const toks = String(text ?? '').split(/[\s,;]+/).filter(Boolean);
  if (toks.length < minLen) throw new InputError(`${name}: potrzeba co najmniej ${minLen} ${minLen === 1 ? 'liczby' : 'liczb'}.`);
  if (toks.length > maxLen) throw new InputError(`${name}: maksymalnie ${maxLen} liczb (żeby animacja była czytelna).`);
  return toks.map((t) => {
    if (!/^[+-]?\d+$/.test(t)) throw new InputError(`${name}: „${t}” nie jest liczbą całkowitą.`);
    const v = parseInt(t, 10);
    if (v < min || v > max) throw new InputError(`${name}: liczby muszą być z zakresu ${min}…${max}.`);
    return v;
  });
}

export function parseInt1(text, { name = 'liczba', min = -1e9, max = 1e9 } = {}) {
  const t = String(text ?? '').trim();
  if (!/^[+-]?\d+$/.test(t)) throw new InputError(`${name}: podaj liczbę całkowitą.`);
  const v = parseInt(t, 10);
  if (v < min || v > max) throw new InputError(`${name}: wartość musi być z zakresu ${min}…${max}.`);
  return v;
}

/** Krawędzie: linie „u v” lub „u v w”; opcjonalnie pierwsza linia z samym n. Wierzchołki 1..n. */
export function parseEdges(text, { weighted = false, maxN = 16, maxM = 40, minW = 0, maxW = 99 } = {}) {
  const lines = String(text ?? '').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#') && !l.startsWith('//'));
  let n = 0;
  const edges = [];
  for (const [k, ln] of lines.entries()) {
    const nums = ln.split(/[\s,;]+/).filter(Boolean);
    if (k === 0 && nums.length === 1) { n = parseInt1(nums[0], { name: 'liczba wierzchołków', min: 1, max: maxN }); continue; }
    if (nums.length !== (weighted ? 3 : 2) && !(weighted && nums.length === 2)) {
      throw new InputError(`Linia „${ln}”: oczekiwano ${weighted ? '„u v w”' : '„u v”'}.`);
    }
    const u = parseInt1(nums[0], { name: 'wierzchołek', min: 1, max: maxN });
    const v = parseInt1(nums[1], { name: 'wierzchołek', min: 1, max: maxN });
    const w = weighted ? (nums.length === 3 ? parseInt1(nums[2], { name: 'waga', min: minW, max: maxW }) : 1) : 1;
    edges.push({ u, v, w });
  }
  for (const e of edges) n = Math.max(n, e.u, e.v);
  if (!n) throw new InputError('Podaj krawędzie grafu, np. „1 2”.');
  if (n > maxN) throw new InputError(`Maksymalnie ${maxN} wierzchołków.`);
  if (edges.length > maxM) throw new InputError(`Maksymalnie ${maxM} krawędzi.`);
  return { n, edges };
}

export function adjacency(n, edges, directed) {
  const g = Array.from({ length: n + 1 }, () => []);
  edges.forEach((e, id) => {
    g[e.u].push({ to: e.v, w: e.w, id });
    if (!directed && e.u !== e.v) g[e.v].push({ to: e.u, w: e.w, id });
  });
  for (const l of g) l.sort((a, b) => a.to - b.to || a.id - b.id);
  return g;
}

export function edgesToText(edges, weighted, n) {
  const used = new Set(edges.flatMap((e) => [e.u, e.v]));
  const head = n && used.size < n ? `${n}\n` : '';
  return head + edges.map((e) => (weighted ? `${e.u} ${e.v} ${e.w}` : `${e.u} ${e.v}`)).join('\n');
}

/** Losowy spójny graf: drzewo + dodatkowe krawędzie (bez multikrawędzi). */
export function randomGraph(r, n, extra, { weighted = false, maxW = 9, directed = false, dag = false } = {}) {
  const key = (u, v) => (directed ? `${u}>${v}` : `${Math.min(u, v)}-${Math.max(u, v)}`);
  const seen = new Set();
  const edges = [];
  const order = r.shuffle(Array.from({ length: n }, (_, i) => i + 1));
  const rank = new Map(order.map((v, i) => [v, i]));
  const add = (u, v) => {
    if (u === v) return false;
    if (dag && rank.get(u) > rank.get(v)) [u, v] = [v, u];
    const k = key(u, v);
    if (seen.has(k) || (!directed && seen.has(key(v, u)))) return false;
    seen.add(k);
    edges.push({ u, v, w: weighted ? r.int(1, maxW) : 1 });
    return true;
  };
  for (let i = 1; i < n; i++) add(order[r.int(0, i - 1)], order[i]);
  let tries = 0;
  while (extra > 0 && tries++ < 500) if (add(r.int(1, n), r.int(1, n))) extra--;
  return edges;
}

// ------------------------------------------------------------------------------------------- układ grafu
/** Fruchterman–Reingold (deterministyczny) → pos[1..n] = [x, y] w [0, 1]. */
export function layoutForce(n, edges, seed = 7) {
  const r = rng(seed);
  const pos = [[0.5, 0.5]];
  for (let i = 1; i <= n; i++) {
    const a = (2 * Math.PI * (i - 1)) / n;
    pos.push([0.5 + 0.38 * Math.cos(a) + (r.next() - 0.5) * 0.02, 0.5 + 0.38 * Math.sin(a) + (r.next() - 0.5) * 0.02]);
  }
  if (n <= 1) return pos;
  const k = 0.9 * Math.sqrt(1 / n);
  let temp = 0.12;
  for (let it = 0; it < 400; it++) {
    const disp = Array.from({ length: n + 1 }, () => [0, 0]);
    for (let i = 1; i <= n; i++) {
      for (let j = i + 1; j <= n; j++) {
        let dx = pos[i][0] - pos[j][0], dy = pos[i][1] - pos[j][1];
        let d = Math.hypot(dx, dy) || 1e-4;
        const f = (k * k) / d;
        dx /= d; dy /= d;
        disp[i][0] += dx * f; disp[i][1] += dy * f;
        disp[j][0] -= dx * f; disp[j][1] -= dy * f;
      }
    }
    for (const e of edges) {
      if (e.u === e.v) continue;
      let dx = pos[e.u][0] - pos[e.v][0], dy = pos[e.u][1] - pos[e.v][1];
      const d = Math.hypot(dx, dy) || 1e-4;
      const f = (d * d) / k;
      dx /= d; dy /= d;
      disp[e.u][0] -= dx * f; disp[e.u][1] -= dy * f;
      disp[e.v][0] += dx * f; disp[e.v][1] += dy * f;
    }
    for (let i = 1; i <= n; i++) {
      // lekka grawitacja do środka
      disp[i][0] += (0.5 - pos[i][0]) * 0.05; disp[i][1] += (0.5 - pos[i][1]) * 0.05;
      const d = Math.hypot(disp[i][0], disp[i][1]) || 1e-9;
      const s = Math.min(d, temp) / d;
      pos[i][0] += disp[i][0] * s; pos[i][1] += disp[i][1] * s;
    }
    temp *= 0.985;
  }
  return normalize(pos, n);
}

function normalize(pos, n) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 1; i <= n; i++) {
    x0 = Math.min(x0, pos[i][0]); x1 = Math.max(x1, pos[i][0]);
    y0 = Math.min(y0, pos[i][1]); y1 = Math.max(y1, pos[i][1]);
  }
  const sx = x1 - x0 || 1, sy = y1 - y0 || 1;
  for (let i = 1; i <= n; i++) {
    pos[i] = [x1 === x0 ? 0.5 : 0.04 + 0.92 * (pos[i][0] - x0) / sx, y1 === y0 ? 0.5 : 0.06 + 0.88 * (pos[i][1] - y0) / sy];
  }
  return pos;
}

/** Układ warstwowy dla DAG-u (warstwa = najdłuższa ścieżka od źródła); przy cyklu — układ siłowy. */
export function layoutLayers(n, edges) {
  const indeg = Array(n + 1).fill(0);
  const g = Array.from({ length: n + 1 }, () => []);
  for (const e of edges) { g[e.u].push(e.v); indeg[e.v]++; }
  const layer = Array(n + 1).fill(0);
  const q = [];
  for (let v = 1; v <= n; v++) if (!indeg[v]) q.push(v);
  let seen = 0;
  const deg = indeg.slice();
  while (q.length) {
    const v = q.shift(); seen++;
    for (const u of g[v]) { layer[u] = Math.max(layer[u], layer[v] + 1); if (--deg[u] === 0) q.push(u); }
  }
  if (seen < n) return layoutForce(n, edges);
  const L = Math.max(...layer.slice(1));
  const byLayer = Array.from({ length: L + 1 }, () => []);
  for (let v = 1; v <= n; v++) byLayer[layer[v]].push(v);
  // kilka przebiegów barycentrycznych, żeby zmniejszyć przecięcia
  const posIn = new Map();
  byLayer.forEach((vs) => vs.forEach((v, i) => posIn.set(v, i / Math.max(1, vs.length - 1))));
  const preds = Array.from({ length: n + 1 }, () => []);
  for (const e of edges) preds[e.v].push(e.u);
  for (let pass = 0; pass < 4; pass++) {
    for (let l = 1; l <= L; l++) {
      byLayer[l].sort((a, b) => bary(a) - bary(b));
      byLayer[l].forEach((v, i) => posIn.set(v, i / Math.max(1, byLayer[l].length - 1)));
    }
  }
  function bary(v) { const p = preds[v]; return p.length ? p.reduce((s, u) => s + posIn.get(u), 0) / p.length : posIn.get(v); }
  const pos = [[0.5, 0.5]];
  for (let l = 0; l <= L; l++) {
    const vs = byLayer[l];
    vs.forEach((v, i) => { pos[v] = [L ? 0.05 + 0.9 * (l / L) : 0.5, vs.length > 1 ? 0.08 + 0.84 * (i / (vs.length - 1)) : 0.5]; });
  }
  return pos;
}

// ------------------------------------------------------------------------------------------- drobne
export const fmtInf = (x) => (x === Infinity || x === null || x === undefined ? '∞' : String(x));
export const lowbit = (i) => i & -i;
export const bin = (i, w = 5) => i.toString(2).padStart(w, '0');
export function range(a, b) { const out = []; for (let i = a; i <= b; i++) out.push(i); return out; }

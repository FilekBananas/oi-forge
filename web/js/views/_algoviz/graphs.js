// Grafy: BFS/DFS na siatce i na grafie, Dijkstra, Find & Union, Kruskal, sortowanie topologiczne (Kahn).
import { cpp, Rec, exec, rng, parseEdges, parseInt1, adjacency, randomGraph, edgesToText, layoutForce, layoutLayers, InputError, fmtInf } from './core.js';

const CAT = 'Grafy';

// =========================================================================================== siatka
export function parseGrid(text) {
  const lines = String(text ?? '').split('\n').map((l) => l.replace(/\s+/g, '')).filter(Boolean);
  if (!lines.length) throw new InputError('Plansza jest pusta.');
  const R = lines.length, C = Math.max(...lines.map((l) => l.length));
  if (R < 2 || C < 2) throw new InputError('Plansza musi mieć co najmniej 2×2 pola.');
  if (R > 24 || C > 40) throw new InputError('Maksymalny rozmiar planszy to 24×40.');
  const walls = Array(R * C).fill(0);
  let s = -1, t = -1;
  lines.forEach((ln, r) => {
    for (let c = 0; c < C; c++) {
      const ch = ln[c] || '.';
      if (ch === '#') walls[r * C + c] = 1;
      else if (ch === 'S' || ch === 's') s = r * C + c;
      else if (ch === 'T' || ch === 't' || ch === 'M' || ch === 'E') t = r * C + c;
      else if (ch !== '.') throw new InputError(`Nieznany znak „${ch}” na planszy (dozwolone: . # S T).`);
    }
  });
  if (s < 0) s = walls.findIndex((w) => !w);
  if (t < 0) { for (let i = R * C - 1; i >= 0; i--) if (!walls[i] && i !== s) { t = i; break; } }
  if (s < 0 || t < 0) throw new InputError('Brakuje wolnych pól na start (S) i metę (T).');
  walls[s] = 0; walls[t] = 0;
  return { R, C, walls, s, t };
}

export function gridToText({ R, C, walls, s, t }) {
  const out = [];
  for (let r = 0; r < R; r++) {
    let ln = '';
    for (let c = 0; c < C; c++) { const i = r * C + c; ln += i === s ? 'S' : i === t ? 'T' : walls[i] ? '#' : '.'; }
    out.push(ln);
  }
  return out.join('\n');
}

/** Labirynt (DFS z nawrotami) z kilkoma dodatkowymi przejściami, żeby BFS i DFS dawały różne ścieżki. */
export function mazeText(r, R = 13, C = 25, loops = 0.12) {
  const walls = Array(R * C).fill(1);
  const st = [[1, 1]];
  walls[1 * C + 1] = 0;
  while (st.length) {
    const [y, x] = st[st.length - 1];
    const nb = [[-2, 0], [0, 2], [2, 0], [0, -2]].map(([dy, dx]) => [y + dy, x + dx, dy, dx])
      .filter(([ny, nx]) => ny > 0 && nx > 0 && ny < R - 1 && nx < C - 1 && walls[ny * C + nx]);
    if (!nb.length) { st.pop(); continue; }
    const [ny, nx, dy, dx] = r.pick(nb);
    walls[(y + dy / 2) * C + (x + dx / 2)] = 0;
    walls[ny * C + nx] = 0;
    st.push([ny, nx]);
  }
  for (let y = 1; y < R - 1; y++) for (let x = 1; x < C - 1; x++) {
    const i = y * C + x;
    if (walls[i] && ((y % 2 === 1) !== (x % 2 === 1)) && r.next() < loops) walls[i] = 0;
  }
  return gridToText({ R, C, walls, s: 1 * C + 1, t: (R - 2) * C + (C - 2) });
}

export function scatterText(r, R = 13, C = 25, density = 0.27) {
  const walls = Array.from({ length: R * C }, () => (r.next() < density ? 1 : 0));
  return gridToText({ R, C, walls, s: Math.floor(R / 2) * C + 1, t: Math.floor(R / 2) * C + C - 2 });
}

export function emptyText(R = 13, C = 25) {
  return gridToText({ R, C, walls: Array(R * C).fill(0), s: Math.floor(R / 2) * C + 2, t: Math.floor(R / 2) * C + C - 3 });
}

const DIRS = [[-1, 0], [0, 1], [1, 0], [0, -1]];
const DIR_NAMES = ['góra', 'prawo', 'dół', 'lewo'];
const rc = (i, C) => `(${Math.floor(i / C)}, ${i % C})`;

const GRID_BFS = cpp(`
// dist[][] = -1 dla nieodwiedzonych; kierunki: góra, prawo, dół, lewo
queue<pair<int,int>> q;
dist[sr][sc] = 0; q.push({sr, sc});                      @@init
while (!q.empty()) {                                     @@loop
    auto [r, c] = q.front(); q.pop();                    @@pop
    for (int d = 0; d < 4; d++) {
        int nr = r + dr[d], nc = c + dc[d];
        if (nr < 0 || nr >= R || nc < 0 || nc >= C) continue;
        if (plansza[nr][nc] == '#' || dist[nr][nc] != -1) continue;   @@check
        dist[nr][nc] = dist[r][c] + 1;                   @@relax
        q.push({nr, nc});
    }
}                                                        @@end
`);

function gridBfs(vals) {
  const g = parseGrid(vals.grid);
  const { R, C, walls, s, t } = g;
  const N = R * C;
  const cells = Array(N).fill(0); // 0 nic, 1 w kolejce, 2 odwiedzone, 3 bieżące, 4 ścieżka
  const dist = Array(N).fill(-1);
  const r = new Rec(GRID_BFS, 2500);
  const q = [];
  let head = 0;
  const S = (vars = {}) => ({
    cells: cells.join(''), dist: dist.slice(),
    vars: { kolejka: q.length - head, ...vars },
    panels: [{ title: 'Kolejka (przód → tył)', items: q.slice(head, head + 12).map((i) => ({ v: rc(i, C), sub: 'd=' + dist[i] })).concat(q.length - head > 12 ? [{ v: `… +${q.length - head - 12}`, t: 'dim' }] : []) }],
  });
  return exec(r, () => {
    dist[s] = 0; q.push(s); cells[s] = 1;
    r.add('init', `BFS zaczyna od startu ${rc(s, C)} z odległością 0. Kolejka FIFO sprawia, że pola odwiedzamy warstwami: najpierw wszystkie w odległości 1, potem 2… — dlatego BFS znajduje najkrótszą drogę.`, S());
    let found = false;
    while (head < q.length) {
      const v = q[head++];
      cells[v] = 3;
      r.add('pop', `Zdejmuję ${rc(v, C)} z kolejki (dist = ${dist[v]}).`, S({ dist: dist[v] }));
      if (v === t) { found = true; cells[v] = 2; break; }
      const fresh = [];
      for (let d = 0; d < 4; d++) {
        const nr = Math.floor(v / C) + DIRS[d][0], nc = (v % C) + DIRS[d][1];
        if (nr < 0 || nr >= R || nc < 0 || nc >= C) continue;
        const u = nr * C + nc;
        if (walls[u] || dist[u] !== -1) continue;
        dist[u] = dist[v] + 1; q.push(u); cells[u] = 1; fresh.push(`${DIR_NAMES[d]} ${rc(u, C)}`);
      }
      r.add(fresh.length ? 'relax' : 'check', fresh.length
        ? `Nowi sąsiedzi (dist = ${dist[v] + 1}): ${fresh.join(', ')} — trafiają na koniec kolejki.`
        : 'Brak nowych sąsiadów (ściany, krawędź planszy albo pola już odwiedzone).', S({ dist: dist[v] }));
      cells[v] = 2;
    }
    if (found) {
      let v = t;
      const path = [t];
      while (v !== s) {
        const vr = Math.floor(v / C), vc = v % C;
        for (const [dy, dx] of DIRS) {
          const nr = vr + dy, nc = vc + dx;
          if (nr < 0 || nr >= R || nc < 0 || nc >= C) continue;
          const u = nr * C + nc;
          if (dist[u] === dist[v] - 1 && !walls[u]) { v = u; break; }
        }
        path.push(v);
      }
      for (const p of path) cells[p] = 4;
      r.add('end', `Dotarliśmy do mety ${rc(t, C)}: najkrótsza droga ma ${dist[t]} kroków. Odtwarzamy ją od mety, idąc zawsze do sąsiada o dist mniejszym o 1.`, S({ wynik: dist[t] }));
    } else {
      r.add('end', `Kolejka jest pusta, a meta ${rc(t, C)} nie została odwiedzona — nie da się do niej dojść.`, S({ wynik: '—' }));
    }
  }, { ...g, kind: 'bfs' });
}

const GRID_DFS = cpp(`
// kierunki: góra, prawo, dół, lewo
bool dfs(int r, int c) {
    if (r == tr && c == tc) return true;      // meta!     @@goal
    odw[r][c] = true;                                      @@visit
    for (int d = 0; d < 4; d++) {
        int nr = r + dr[d], nc = c + dc[d];
        if (nr < 0 || nr >= R || nc < 0 || nc >= C) continue;
        if (plansza[nr][nc] == '#' || odw[nr][nc]) continue;
        if (dfs(nr, nc)) return true;                      @@rec
    }
    return false;            // ślepy zaułek — wracamy     @@back
}
`);

function gridDfs(vals) {
  const g = parseGrid(vals.grid);
  const { R, C, walls, s, t } = g;
  const cells = Array(R * C).fill(0); // 2 odwiedzone (porzucone), 3 bieżące, 4 ścieżka, 6 na stosie
  const r = new Rec(GRID_DFS, 2500);
  const stack = [];
  let visitedCount = 0;
  const S = (vars = {}) => ({
    cells: cells.join(''),
    vars: { głębokość: stack.length, odwiedzone: visitedCount, ...vars },
    panels: [{ title: 'Stos wywołań (dno → szczyt)', items: stack.slice(-10).map((i) => ({ v: rc(i, C) })).reverse().concat(stack.length > 10 ? [{ v: `… +${stack.length - 10}`, t: 'dim' }] : []).reverse() }],
  });
  function dfs(v) {
    stack.push(v);
    if (v === t) {
      for (const p of stack) cells[p] = 4;
      r.add('goal', `dfs${rc(v, C)} — to meta! Zwracamy true w górę stosu. Ścieżka ma ${stack.length - 1} kroków — DFS nie gwarantuje najkrótszej.`, S({ wynik: stack.length - 1 }));
      return true;
    }
    cells[v] = 3; visitedCount++;
    r.add('visit', `dfs${rc(v, C)}: zaznaczam jako odwiedzone i próbuję kolejnych kierunków (góra, prawo, dół, lewo).`, S());
    cells[v] = 6;
    for (let d = 0; d < 4; d++) {
      const nr = Math.floor(v / C) + DIRS[d][0], nc = (v % C) + DIRS[d][1];
      if (nr < 0 || nr >= R || nc < 0 || nc >= C) continue;
      const u = nr * C + nc;
      if (walls[u] || cells[u]) continue;
      if (dfs(u)) return true;
      cells[v] = 3;
      r.add('rec', `Wróciliśmy do ${rc(v, C)} — próbuję następnego kierunku.`, S());
      cells[v] = 6;
    }
    cells[v] = 5;
    stack.pop();
    r.add('back', `${rc(v, C)} to ślepy zaułek — wszystkie kierunki sprawdzone, cofamy się (return false).`, S());
    return false;
  }
  return exec(r, () => {
    r.add('goal', `DFS idzie „w głąb”: z każdego pola od razu wchodzi do pierwszego nieodwiedzonego sąsiada, a cofa się dopiero w ślepym zaułku. Start: ${rc(s, C)}, meta: ${rc(t, C)}.`, S());
    if (!dfs(s)) r.add('back', 'Przeszukaliśmy wszystko, co osiągalne — meta jest niedostępna.', S({ wynik: '—' }));
  }, { ...g, kind: 'dfs' });
}

// =========================================================================================== grafy
const DEFAULT_GRAPH = '1 2\n1 3\n2 4\n3 4\n3 5\n4 6\n5 6\n5 7\n6 8\n7 8';
const DEFAULT_WGRAPH = '1 2 4\n1 3 1\n3 2 2\n2 4 5\n3 4 8\n3 5 10\n4 5 2\n4 6 6\n5 6 3\n6 7 1\n5 7 7';
const DEFAULT_DAG = '1 3\n2 3\n2 4\n3 5\n4 5\n4 6\n5 7\n6 7\n6 8\n7 8';
const DIR_INPUT = { key: 'dir', label: 'Graf', kind: 'select', def: 'u', options: [{ value: 'u', label: 'nieskierowany' }, { value: 'd', label: 'skierowany' }] };

function graphData(n, edges, directed, weighted, layout) {
  return { n, edges: edges.map((e) => ({ u: e.u, v: e.v, w: e.w })), directed, weighted, pos: layout === 'layers' ? layoutLayers(n, edges) : layoutForce(n, edges, n * 31 + edges.length) };
}

const G_BFS = cpp(`
vector<int> dist(n + 1, -1);
queue<int> q;
dist[s] = 0; q.push(s);                  @@init
while (!q.empty()) {                     @@loop
    int v = q.front(); q.pop();          @@pop
    for (int u : g[v]) {                 @@edge
        if (dist[u] != -1) continue;     @@check
        dist[u] = dist[v] + 1;           @@relax
        q.push(u);
    }
}                                        @@end
`);

function graphBfs(vals) {
  const directed = vals.dir === 'd';
  const { n, edges } = parseEdges(vals.edges, { maxN: 16, maxM: 40 });
  const s = parseInt1(vals.s, { name: 'Start', min: 1, max: n });
  const g = adjacency(n, edges, directed);
  const r = new Rec(G_BFS);
  const ns = Array(n + 1).fill(''), es = Array(edges.length).fill('');
  const dist = Array(n + 1).fill(-1);
  const q = [];
  let head = 0;
  const S = (vars = {}) => ({
    ns: ns.slice(), es: es.slice(), nl: dist.map((d) => (d < 0 ? '' : 'd=' + d)),
    vars, panels: [{ title: 'Kolejka', items: q.slice(head).map((v) => ({ v: String(v), sub: 'd=' + dist[v] })) }],
  });
  return exec(r, () => {
    dist[s] = 0; q.push(s); ns[s] = 'frontier';
    r.add('init', `BFS z wierzchołka ${s}: dist[${s}] = 0. Sąsiadów przeglądamy w kolejności rosnącej.`, S());
    while (head < q.length) {
      const v = q[head++];
      ns[v] = 'current';
      r.add('pop', `Zdejmuję ${v} z kolejki (dist = ${dist[v]}). Sąsiedzi: ${g[v].map((x) => x.to).join(', ') || 'brak'}.`, S({ v }));
      for (const { to: u, id } of g[v]) {
        const prev = es[id];
        es[id] = 'cmp';
        if (dist[u] !== -1) {
          r.add('check', `Krawędź ${v}–${u}: ${u} ma już dist = ${dist[u]}, pomijam.`, S({ v, u }));
          es[id] = prev || 'dim';
          continue;
        }
        dist[u] = dist[v] + 1; q.push(u); ns[u] = 'frontier';
        es[id] = 'tree';
        r.add('relax', `Krawędź ${v}–${u}: ${u} nieodwiedzony → dist[${u}] = ${dist[v]} + 1 = ${dist[u]}, dodaję na koniec kolejki.`, S({ v, u }));
      }
      ns[v] = 'done';
    }
    const unreachable = [];
    for (let v = 1; v <= n; v++) if (dist[v] < 0) { unreachable.push(v); ns[v] = 'dim'; }
    r.add('end', `Kolejka pusta — koniec. Pogrubione krawędzie tworzą drzewo BFS (najkrótsze ścieżki od ${s}).${unreachable.length ? ` Nieosiągalne: ${unreachable.join(', ')}.` : ''}`, S());
  }, graphData(n, edges, directed, false));
}

const G_DFS = cpp(`
int czas = 0;
void dfs(int v) {
    odw[v] = true;
    wejscie[v] = ++czas;                 @@visit
    for (int u : g[v]) {                 @@edge
        if (!odw[u]) dfs(u);             @@rec
    }
    wyjscie[v] = ++czas;                 @@exit
}
`);

function graphDfs(vals) {
  const directed = vals.dir === 'd';
  const { n, edges } = parseEdges(vals.edges, { maxN: 16, maxM: 40 });
  const s = parseInt1(vals.s, { name: 'Start', min: 1, max: n });
  const g = adjacency(n, edges, directed);
  const r = new Rec(G_DFS);
  const ns = Array(n + 1).fill(''), es = Array(edges.length).fill('');
  const tin = Array(n + 1).fill(0), tout = Array(n + 1).fill(0);
  const stack = [], order = [];
  let time = 0, cycle = false;
  const S = (vars = {}) => ({
    ns: ns.slice(), es: es.slice(), nl: tin.map((x, v) => (x ? `${x}/${tout[v] || '?'}` : '')), vars: { czas: time, ...vars },
    panels: [{ title: 'Stos wywołań', items: stack.map((v) => ({ v: `dfs(${v})` })) }, { title: 'Kolejność odwiedzin', items: order.map((v) => ({ v: String(v) })) }],
  });
  function dfs(v, viaEdge) {
    stack.push(v); order.push(v);
    tin[v] = ++time; ns[v] = 'current';
    r.add('visit', `Wchodzę do ${v}: czas wejścia = ${tin[v]}.`, S({ v }));
    ns[v] = 'stack';
    for (const { to: u, id } of g[v]) {
      if (id === viaEdge && !directed) continue;
      const prev = es[id];
      es[id] = 'cmp';
      if (!tin[u]) {
        r.add('rec', `Krawędź ${v}→${u}: ${u} nieodwiedzony — wchodzę głębiej.`, S({ v, u }));
        es[id] = 'tree';
        dfs(u, id);
        ns[v] = 'current';
        r.add('edge', `Wróciłem do ${v}.`, S({ v }));
        ns[v] = 'stack';
      } else if (!tout[u]) {
        cycle = true;
        es[id] = 'back';
        r.add('edge', `Krawędź ${v}→${u}: ${u} jest na stosie — to krawędź powrotna, czyli w grafie jest cykl!`, S({ v, u }));
      } else {
        r.add('edge', `Krawędź ${v}→${u}: ${u} już przetworzony — pomijam.`, S({ v, u }));
        es[id] = prev || 'dim';
      }
    }
    tout[v] = ++time; ns[v] = 'done'; stack.pop();
    r.add('exit', `Wychodzę z ${v}: czas wyjścia = ${tout[v]}.`, S({ v }));
  }
  return exec(r, () => {
    r.add('visit', `DFS z wierzchołka ${s}. Przy każdym wierzchołku pokazuję czasy wejścia/wyjścia — przydają się np. do sprawdzania, czy u leży w poddrzewie v.`, S());
    dfs(s, -1);
    const unreached = [];
    for (let v = 1; v <= n; v++) if (!tin[v]) { unreached.push(v); ns[v] = 'dim'; }
    r.add('exit', `Koniec DFS. Kolejność: ${order.join(' → ')}.${cycle ? ' Znaleziono krawędź powrotną (cykl).' : ''}${unreached.length ? ` Nieosiągalne z ${s}: ${unreached.join(', ')}.` : ''}`, S());
  }, graphData(n, edges, directed, false));
}

const DIJKSTRA = cpp(`
vector<long long> dist(n + 1, INF);
priority_queue<pair<long long,int>, vector<pair<long long,int>>, greater<>> pq;
dist[s] = 0; pq.push({0, s});                       @@init
while (!pq.empty()) {                               @@loop
    auto [d, v] = pq.top(); pq.pop();               @@pop
    if (d > dist[v]) continue;    // nieaktualny     @@stale
    for (auto [u, w] : g[v]) {                      @@edge
        if (dist[v] + w < dist[u]) {                @@check
            dist[u] = dist[v] + w;                  @@relax
            pq.push({dist[u], u});
        }
    }
}                                                   @@end
`);

export function dijkstraDistances(n, edges, s, directed) {
  const g = adjacency(n, edges, directed);
  const dist = Array(n + 1).fill(Infinity);
  dist[s] = 0;
  const pq = [[0, s]];
  while (pq.length) {
    pq.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const [d, v] = pq.shift();
    if (d > dist[v]) continue;
    for (const { to, w } of g[v]) if (d + w < dist[to]) { dist[to] = d + w; pq.push([dist[to], to]); }
  }
  return dist;
}

function dijkstra(vals) {
  const directed = vals.dir === 'd';
  const { n, edges } = parseEdges(vals.edges, { weighted: true, maxN: 14, maxM: 36, minW: 0 });
  const s = parseInt1(vals.s, { name: 'Start', min: 1, max: n });
  const g = adjacency(n, edges, directed);
  const r = new Rec(DIJKSTRA);
  const ns = Array(n + 1).fill(''), es = Array(edges.length).fill('');
  const dist = Array(n + 1).fill(Infinity), parentEdge = Array(n + 1).fill(-1);
  const done = Array(n + 1).fill(false);
  let pq = [];
  const S = (vars = {}) => ({
    ns: ns.slice(), es: es.slice(), nl: dist.map((d) => fmtInf(d)), vars,
    panels: [
      { title: 'Kolejka priorytetowa (min na górze)', items: pq.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]).map(([d, v]) => ({ v: `(${d}, ${v})`, t: d > dist[v] ? 'dim' : '' })) },
      { title: 'dist[]', items: dist.slice(1).map((d, i) => ({ v: fmtInf(d), sub: String(i + 1), t: done[i + 1] ? 'done' : '' })) },
    ],
  });
  return exec(r, () => {
    dist[s] = 0; pq.push([0, s]); ns[s] = 'frontier';
    r.add('init', `Dijkstra z ${s}: dist[${s}] = 0, reszta = ∞. Zawsze wyciągamy z kolejki wierzchołek o najmniejszym dist — przy wagach ≥ 0 jego odległość jest już ostateczna.`, S());
    while (pq.length) {
      pq.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      const [d, v] = pq.shift();
      if (d > dist[v]) {
        r.add('stale', `Zdejmuję (${d}, ${v}), ale dist[${v}] = ${dist[v]} < ${d} — to nieaktualny wpis (lazy deletion), pomijam.`, S({ d, v }));
        continue;
      }
      ns[v] = 'current'; done[v] = true;
      r.add('pop', `Zdejmuję (${d}, ${v}) — najmniejsza odległość w kolejce. dist[${v}] = ${d} jest już ostateczne.`, S({ d, v }));
      for (const { to: u, w, id } of g[v]) {
        const prev = es[id];
        es[id] = 'cmp';
        const cand = dist[v] + w;
        if (cand < dist[u]) {
          const old = dist[u];
          r.add('check', `Krawędź ${v}–${u} (waga ${w}): ${dist[v]} + ${w} = ${cand} < ${fmtInf(old)} → poprawiam!`, S({ v, u, w }));
          if (parentEdge[u] >= 0) es[parentEdge[u]] = 'dim';
          dist[u] = cand; parentEdge[u] = id; es[id] = 'tree'; pq.push([cand, u]);
          if (!done[u]) ns[u] = 'frontier';
          r.add('relax', `dist[${u}] = ${cand}, wrzucam (${cand}, ${u}) do kolejki.${old !== Infinity ? ' Stary wpis zostaje w kolejce jako nieaktualny.' : ''}`, S({ v, u, w }));
        } else {
          r.add('check', `Krawędź ${v}–${u} (waga ${w}): ${dist[v]} + ${w} = ${cand} ≥ dist[${u}] = ${fmtInf(dist[u])} — bez zmian.`, S({ v, u, w }));
          es[id] = prev === 'cmp' ? '' : prev;
        }
      }
      ns[v] = 'done';
    }
    for (let v = 1; v <= n; v++) if (dist[v] === Infinity) ns[v] = 'dim';
    r.add('end', `Gotowe! Odległości od ${s}: ${dist.slice(1).map((d, i) => `${i + 1}:${fmtInf(d)}`).join(', ')}. Pogrubione krawędzie to drzewo najkrótszych ścieżek. Złożoność O((n + m) log m).`, S());
  }, graphData(n, edges, directed, true));
}

// =========================================================================================== Find & Union
const DSU = cpp(`
int find(int v) {
    if (p[v] == v) return v;                 @@root
    return p[v] = find(p[v]);  // kompresja  @@compress
}
void unite(int a, int b) {
    a = find(a); b = find(b);                @@finds
    if (a == b) return;                      @@same
    if (sz[a] < sz[b]) swap(a, b);           @@size
    p[b] = a; sz[a] += sz[b];                @@link
}
`);

export function parseDsuOps(text, n) {
  const ops = [];
  for (const raw of String(text ?? '').split('\n')) {
    const ln = raw.trim();
    if (!ln || ln.startsWith('#') || ln.startsWith('//')) continue;
    const p = ln.split(/[\s,]+/);
    const kw = p[0].toLowerCase();
    const nums = p.slice(1).map((x) => parseInt1(x, { name: `Operacja „${ln}”`, min: 1, max: n }));
    if (['union', 'u', 'unite', '+', 'polacz', 'połącz'].includes(kw) && nums.length === 2) ops.push({ op: 'union', a: nums[0], b: nums[1] });
    else if (['find', 'f', 'znajdz', 'znajdź'].includes(kw) && nums.length === 1) ops.push({ op: 'find', a: nums[0] });
    else if (['?', 'same', 'q', 'czy'].includes(kw) && nums.length === 2) ops.push({ op: 'same', a: nums[0], b: nums[1] });
    else throw new InputError(`Nie rozumiem operacji „${ln}”. Użyj: union a b · find a · ? a b`);
  }
  if (ops.length > 30) throw new InputError('Maksymalnie 30 operacji.');
  return ops;
}

function dsu(vals) {
  const n = parseInt1(vals.n, { name: 'n', min: 2, max: 16 });
  const ops = parseDsuOps(vals.ops, n);
  const p = Array.from({ length: n + 1 }, (_, i) => i), sz = Array(n + 1).fill(1);
  const r = new Rec(DSU);
  let ns = Array(n + 1).fill('');
  const S = (vars = {}) => ({ parent: p.slice(), sz: sz.slice(), ns: ns.slice(), vars });
  const clearTones = () => { ns = Array(n + 1).fill(''); };
  function find(v, label) {
    const path = [v];
    while (p[path[path.length - 1]] !== path[path.length - 1]) {
      const x = path[path.length - 1];
      ns[x] = 'path';
      r.add('compress', `${label}: p[${x}] = ${p[x]} ≠ ${x}, idę w górę.`, S({ v: x }));
      path.push(p[x]);
    }
    const root = path[path.length - 1];
    ns[root] = 'hit';
    r.add('root', `${label}: p[${root}] = ${root} — ${root} to korzeń (reprezentant zbioru).`, S({ root }));
    for (let k = path.length - 3; k >= 0; k--) {
      const x = path[k];
      p[x] = root; ns[x] = 'new';
      r.add('compress', `Kompresja ścieżki: p[${x}] = ${root} — następnym razem ${x} trafi do korzenia w jednym kroku.`, S({ root }));
    }
    return root;
  }
  return exec(r, () => {
    r.add('root', `Na początku każdy z ${n} elementów jest osobnym zbiorem: p[v] = v. Operacje: ${ops.map((o) => (o.op === 'union' ? `union ${o.a} ${o.b}` : o.op === 'find' ? `find ${o.a}` : `? ${o.a} ${o.b}`)).join(', ')}.`, S());
    for (const o of ops) {
      clearTones();
      if (o.op === 'find') {
        ns[o.a] = 'cmp';
        r.add('root', `find(${o.a}) — szukam reprezentanta zbioru zawierającego ${o.a}.`, S());
        const root = find(o.a, `find(${o.a})`);
        r.add('root', `find(${o.a}) = ${root}.`, S({ wynik: root }));
      } else if (o.op === 'same') {
        ns[o.a] = 'cmp'; ns[o.b] = 'cmp';
        r.add('finds', `Czy ${o.a} i ${o.b} są w jednym zbiorze? Porównuję ich korzenie.`, S());
        const ra = find(o.a, `find(${o.a})`), rb = find(o.b, `find(${o.b})`);
        r.add('same', ra === rb ? `find(${o.a}) = find(${o.b}) = ${ra} → TAK, ten sam zbiór.` : `find(${o.a}) = ${ra} ≠ find(${o.b}) = ${rb} → NIE.`, S({ wynik: ra === rb ? 'tak' : 'nie' }));
      } else {
        ns[o.a] = 'cmp'; ns[o.b] = 'cmp';
        r.add('finds', `unite(${o.a}, ${o.b}): najpierw znajduję korzenie obu zbiorów.`, S());
        let a = find(o.a, `find(${o.a})`), b = find(o.b, `find(${o.b})`);
        if (a === b) { r.add('same', `Oba elementy mają korzeń ${a} — są już w jednym zbiorze, nic nie robię.`, S()); continue; }
        if (sz[a] < sz[b]) {
          r.add('size', `sz[${a}] = ${sz[a]} < sz[${b}] = ${sz[b]} — zamieniam, żeby mniejsze drzewo podpiąć pod większe (drzewa zostają płytkie).`, S());
          [a, b] = [b, a];
        } else r.add('size', `sz[${a}] = ${sz[a]} ≥ sz[${b}] = ${sz[b]} — podpinam ${b} pod ${a}.`, S());
        p[b] = a; sz[a] += sz[b];
        clearTones(); ns[a] = 'hit'; ns[b] = 'new';
        r.add('link', `p[${b}] = ${a}, sz[${a}] = ${sz[a]}. Zbiory połączone.`, S());
      }
    }
    clearTones();
    const roots = []; for (let v = 1; v <= n; v++) if (p[v] === v) roots.push(v);
    r.add('link', `Koniec: ${roots.length} ${roots.length === 1 ? 'zbiór' : roots.length < 5 ? 'zbiory' : 'zbiorów'} (korzenie: ${roots.join(', ')}). Dzięki kompresji i łączeniu według rozmiaru operacje działają w praktyce w O(1) (dokładnie O(α(n))).`, S());
  }, { n });
}

// =========================================================================================== Kruskal
const KRUSKAL = cpp(`
sort(e.begin(), e.end());   // krawędzie {w, a, b} rosnąco po wadze   @@sort
long long koszt = 0; int wziete = 0;
for (auto [w, a, b] : e) {                                 @@loop
    if (find(a) == find(b)) continue;  // zamknęłaby cykl   @@skip
    unite(a, b);                                           @@take
    koszt += w;
    if (++wziete == n - 1) break;      // drzewo gotowe     @@full
}
`);

function kruskal(vals) {
  const { n, edges } = parseEdges(vals.edges, { weighted: true, maxN: 14, maxM: 36, minW: -99 });
  const r = new Rec(KRUSKAL);
  const order = edges.map((e, id) => ({ ...e, id })).sort((a, b) => a.w - b.w || a.id - b.id);
  const p = Array.from({ length: n + 1 }, (_, i) => i), size = Array(n + 1).fill(1);
  const find = (v) => (p[v] === v ? v : (p[v] = find(p[v])));
  const color = Array(n + 1).fill(-1);
  const freeColors = [0, 1, 2, 3, 4, 5, 6, 7];
  const es = Array(edges.length).fill('');
  const status = Array(edges.length).fill('');
  let cost = 0, taken = 0, curIdx = -1;
  const comp = () => Array.from({ length: n + 1 }, (_, v) => (v ? (size[find(v)] > 1 ? color[find(v)] : -1) : -1));
  const S = (vars = {}) => ({
    ns: Array(n + 1).fill(''), es: es.slice(), nl: Array(n + 1).fill(''), comp: comp(), vars: { koszt: cost, krawędzie: `${taken}/${n - 1}`, ...vars },
    panels: [{ title: 'Krawędzie posortowane po wadze', items: order.map((e, k) => ({ v: `${e.u}–${e.v}`, sub: 'w=' + e.w, t: k === curIdx ? 'cmp' : status[e.id] })) }],
  });
  return exec(r, () => {
    r.add('sort', `Kruskal: sortujemy krawędzie rosnąco po wadze i bierzemy kolejne, o ile nie tworzą cyklu. Do sprawdzania cyklu używamy Find & Union. Kolory = spójne składowe budowanego lasu.`, S());
    for (let k = 0; k < order.length; k++) {
      const e = order[k];
      curIdx = k;
      es[e.id] = 'cmp';
      const ra = find(e.u), rb = find(e.v);
      r.add('loop', `Krawędź ${e.u}–${e.v} o wadze ${e.w}: find(${e.u}) = ${ra}, find(${e.v}) = ${rb}.`, S({ w: e.w }));
      if (ra === rb) {
        es[e.id] = 'rejected'; status[e.id] = 'miss';
        r.add('skip', `${e.u} i ${e.v} są już połączone — ta krawędź zamknęłaby cykl, pomijam.`, S({ w: e.w }));
        continue;
      }
      let a = ra, b = rb;
      if (size[a] < size[b]) [a, b] = [b, a];
      const ca = size[a] > 1 ? color[a] : -1, cb = size[b] > 1 ? color[b] : -1;
      let c = ca >= 0 ? ca : cb >= 0 ? cb : freeColors.shift() ?? (a % 8);
      if (ca >= 0 && cb >= 0 && cb !== ca) freeColors.push(cb);
      p[b] = a; size[a] += size[b]; color[a] = c;
      es[e.id] = 'mst'; status[e.id] = 'done';
      cost += e.w; taken++;
      r.add('take', `Biorę krawędź ${e.u}–${e.v} (waga ${e.w}) i łączę składowe. Koszt = ${cost}.`, S({ w: e.w }));
      if (taken === n - 1) { curIdx = -1; r.add('full', `Mamy n − 1 = ${n - 1} krawędzi — minimalne drzewo rozpinające gotowe, reszty nie trzeba sprawdzać.`, S()); break; }
    }
    curIdx = -1;
    const roots = new Set(); for (let v = 1; v <= n; v++) roots.add(find(v));
    r.add('full', roots.size === 1
      ? `MST ma koszt ${cost}. Złożoność: O(m log m) na sortowanie + prawie O(1) na każdą operację DSU.`
      : `Graf nie jest spójny (${roots.size} składowe) — powstał minimalny las rozpinający o koszcie ${cost}.`, S());
  }, graphData(n, edges, false, true));
}

// =========================================================================================== toposort
const TOPO = cpp(`
vector<int> indeg(n + 1, 0);
for (int v = 1; v <= n; v++) for (int u : g[v]) indeg[u]++;   @@indeg
queue<int> q;
for (int v = 1; v <= n; v++) if (indeg[v] == 0) q.push(v);   @@start
vector<int> kolejnosc;
while (!q.empty()) {                                @@loop
    int v = q.front(); q.pop();                     @@pop
    kolejnosc.push_back(v);
    for (int u : g[v])                              @@edge
        if (--indeg[u] == 0) q.push(u);             @@dec
}
if ((int)kolejnosc.size() < n) cout << "cykl!";     @@cycle
`);

function topo(vals) {
  const { n, edges } = parseEdges(vals.edges, { maxN: 16, maxM: 40 });
  const g = adjacency(n, edges, true);
  const r = new Rec(TOPO);
  const indeg = Array(n + 1).fill(0);
  const ns = Array(n + 1).fill(''), es = Array(edges.length).fill('');
  const q = [], order = [];
  let head = 0;
  const S = (vars = {}) => ({
    ns: ns.slice(), es: es.slice(), nl: indeg.map((d, v) => (v && !order.includes(v) ? 'in=' + d : '')), vars,
    panels: [{ title: 'Kolejka (indeg = 0)', items: q.slice(head).map((v) => ({ v: String(v) })) }, { title: 'Kolejność topologiczna', items: order.map((v) => ({ v: String(v), t: 'done' })) }],
  });
  return exec(r, () => {
    for (const e of edges) indeg[e.v]++;
    r.add('indeg', `Liczymy stopnie wejściowe: indeg[v] = liczba krawędzi wchodzących do v. Wierzchołek z indeg = 0 nie zależy od niczego — może iść pierwszy.`, S());
    for (let v = 1; v <= n; v++) if (!indeg[v]) { q.push(v); ns[v] = 'frontier'; }
    r.add('start', `Do kolejki trafiają wierzchołki bez wymagań: ${q.join(', ') || 'brak (!)'}.`, S());
    while (head < q.length) {
      const v = q[head++];
      order.push(v); ns[v] = 'current';
      r.add('pop', `Zdejmuję ${v} i dopisuję do kolejności. Usuwam jego krawędzie wychodzące.`, S({ v }));
      for (const { to: u, id } of g[v]) {
        es[id] = 'cmp';
        indeg[u]--;
        if (indeg[u] === 0) { q.push(u); ns[u] = 'frontier'; r.add('dec', `${v}→${u}: indeg[${u}] spada do 0 — wszystkie wymagania ${u} spełnione, do kolejki.`, S({ v, u })); }
        else r.add('dec', `${v}→${u}: indeg[${u}] = ${indeg[u]} — ${u} wciąż czeka.`, S({ v, u }));
        es[id] = 'dim';
      }
      ns[v] = 'done';
    }
    if (order.length < n) {
      for (let v = 1; v <= n; v++) if (!order.includes(v)) ns[v] = 'miss';
      r.add('cycle', `Kolejka pusta, a zostały wierzchołki z indeg > 0 (czerwone) — w grafie jest cykl, porządek topologiczny nie istnieje.`, S());
    } else {
      r.add('cycle', `Kolejność topologiczna: ${order.join(' → ')}. Każda krawędź prowadzi „w prawo” tej listy. Złożoność O(n + m).`, S());
    }
  }, graphData(n, edges, true, false, 'layers'));
}

// =========================================================================================== rejestr
const GRID_INPUTS = [{ key: 'grid', label: 'Plansza', kind: 'grid', def: null }];
let defaultMaze = null;
const mazeDefault = () => (defaultMaze ??= mazeText(rng(2024), 13, 25, 0.1));
const GRID_LEGEND_BFS = [['frontier', 'w kolejce'], ['visited', 'odwiedzone'], ['current', 'bieżące'], ['path', 'najkrótsza ścieżka'], ['wall', 'ściana']];
const GRID_LEGEND_DFS = [['stack', 'na stosie (bieżąca droga)'], ['current', 'bieżące'], ['dead', 'ślepy zaułek'], ['path', 'znaleziona ścieżka'], ['wall', 'ściana']];
const GRAPH_LEGEND = [['frontier', 'w kolejce'], ['current', 'bieżący'], ['done', 'przetworzony'], ['tree', 'krawędź drzewa'], ['cmp', 'sprawdzana krawędź']];

function randGraphInput(r, weighted) {
  const n = r.int(6, 9);
  return { edges: edgesToText(randomGraph(r, n, r.int(2, 5), { weighted, maxW: 9 }), weighted), s: '1' };
}

export const GRAPHS = [
  { id: 'gridbfs', name: 'BFS na siatce (labirynt)', cat: CAT, kb: 'graph-bfs-grid', complexity: 'O(R·C)', view: 'grid', code: GRID_BFS,
    about: 'Najkrótsza droga w labiryncie: kolejka odwiedza pola warstwami. Rysuj ściany myszą, przeciągaj S i T.',
    inputs: GRID_INPUTS, defaults: () => ({ grid: mazeDefault() }), random: (r) => ({ grid: mazeText(r, 13, 25, 0.12) }), run: gridBfs, legend: GRID_LEGEND_BFS, gridTools: true },
  { id: 'griddfs', name: 'DFS na siatce (labirynt)', cat: CAT, kb: 'graph-dfs', complexity: 'O(R·C)', view: 'grid', code: GRID_DFS,
    about: 'Przeszukiwanie w głąb z nawrotami — znajduje jakąś drogę, niekoniecznie najkrótszą.',
    inputs: GRID_INPUTS, defaults: () => ({ grid: mazeDefault() }), random: (r) => ({ grid: mazeText(r, 13, 25, 0.12) }), run: gridDfs, legend: GRID_LEGEND_DFS, gridTools: true },
  { id: 'graphbfs', name: 'BFS na grafie', cat: CAT, kb: 'graph-bfs', complexity: 'O(n + m)', view: 'graph', code: G_BFS,
    about: 'Odległości (w liczbie krawędzi) od wierzchołka startowego.',
    inputs: [{ key: 'edges', label: 'Krawędzie „u v”', kind: 'area', rows: 6, def: DEFAULT_GRAPH }, { key: 's', label: 'Start', kind: 'text', def: '1' }, DIR_INPUT],
    random: (r) => ({ ...randGraphInput(r, false), dir: 'u' }), run: graphBfs, legend: GRAPH_LEGEND },
  { id: 'graphdfs', name: 'DFS na grafie', cat: CAT, kb: 'graph-dfs', complexity: 'O(n + m)', view: 'graph', code: G_DFS,
    about: 'Rekurencyjne przeszukiwanie w głąb z czasami wejścia/wyjścia i wykrywaniem cykli.',
    inputs: [{ key: 'edges', label: 'Krawędzie „u v”', kind: 'area', rows: 6, def: DEFAULT_GRAPH }, { key: 's', label: 'Start', kind: 'text', def: '1' }, DIR_INPUT],
    random: (r) => ({ ...randGraphInput(r, false), dir: 'u' }), run: graphDfs,
    legend: [['current', 'bieżący'], ['stack', 'na stosie'], ['done', 'przetworzony'], ['tree', 'krawędź drzewa'], ['back', 'krawędź powrotna (cykl)']] },
  { id: 'dijkstra', name: 'Algorytm Dijkstry', cat: CAT, kb: 'graph-dijkstra', complexity: 'O((n + m) log m)', view: 'graph', code: DIJKSTRA,
    about: 'Najkrótsze ścieżki z jednego źródła przy nieujemnych wagach — kolejka priorytetowa.',
    inputs: [{ key: 'edges', label: 'Krawędzie „u v waga”', kind: 'area', rows: 6, def: DEFAULT_WGRAPH }, { key: 's', label: 'Start', kind: 'text', def: '1' }, DIR_INPUT],
    random: (r) => ({ ...randGraphInput(r, true), dir: 'u' }), run: dijkstra, legend: [['frontier', 'w kolejce'], ['current', 'zdjęty z kolejki'], ['done', 'ostateczny dist'], ['tree', 'drzewo ścieżek'], ['cmp', 'relaksowana krawędź']] },
  { id: 'dsu', name: 'Find & Union (DSU)', cat: CAT, kb: 'graph-dsu', complexity: 'O(α(n)) na operację', view: 'forest', code: DSU,
    about: 'Las zbiorów rozłącznych z kompresją ścieżek i łączeniem według rozmiaru.',
    inputs: [{ key: 'n', label: 'Liczba elementów n', kind: 'text', def: '8' },
      { key: 'ops', label: 'Operacje (union a b · find a · ? a b)', kind: 'area', rows: 7, def: 'union 1 2\nunion 3 4\nunion 2 4\nunion 5 6\nunion 7 8\nunion 6 8\nfind 4\nunion 1 5\nfind 8\n? 3 7' }],
    random: (r) => { const n = r.int(7, 10); const ops = []; for (let k = 0; k < r.int(7, 11); k++) { const x = r.next(); ops.push(x < 0.65 ? `union ${r.int(1, n)} ${r.int(1, n)}` : x < 0.85 ? `find ${r.int(1, n)}` : `? ${r.int(1, n)} ${r.int(1, n)}`); } return { n: String(n), ops: ops.join('\n') }; },
    run: dsu, legend: [['cmp', 'argument operacji'], ['path', 'ścieżka do korzenia'], ['hit', 'korzeń'], ['new', 'nowy rodzic']] },
  { id: 'kruskal', name: 'Algorytm Kruskala (MST)', cat: CAT, kb: 'graph-mst-kruskal', complexity: 'O(m log m)', view: 'graph', code: KRUSKAL,
    about: 'Minimalne drzewo rozpinające: najtańsze krawędzie, które nie tworzą cyklu.',
    inputs: [{ key: 'edges', label: 'Krawędzie „u v waga”', kind: 'area', rows: 7, def: DEFAULT_WGRAPH }],
    random: (r) => ({ edges: edgesToText(randomGraph(r, r.int(6, 9), r.int(4, 8), { weighted: true, maxW: 12 }), true) }), run: kruskal,
    legend: [['cmp', 'sprawdzana'], ['mst', 'w drzewie'], ['rejected', 'odrzucona (cykl)']] },
  { id: 'toposort', name: 'Sortowanie topologiczne (Kahn)', cat: CAT, kb: 'graph-toposort', complexity: 'O(n + m)', view: 'graph', code: TOPO,
    about: 'Kolejność zadań z zależnościami: zdejmujemy wierzchołki o stopniu wejściowym 0.',
    inputs: [{ key: 'edges', label: 'Krawędzie skierowane „u v” (u przed v)', kind: 'area', rows: 6, def: DEFAULT_DAG }],
    random: (r) => ({ edges: edgesToText(randomGraph(r, r.int(6, 9), r.int(2, 5), { directed: true, dag: true }), false) }), run: topo,
    legend: [['frontier', 'w kolejce (indeg 0)'], ['current', 'bieżący'], ['done', 'w kolejności'], ['miss', 'na cyklu']] },
];

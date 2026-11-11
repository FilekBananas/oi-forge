// (x) Wizualny debugger — śledzenie programu linia po linii: zmienne, tablice ze wskaźnikami, macierze (mapa ciepła),
// stos wywołań (rekurencja), wyjście programu. Backend: POST /api/debugger/trace (zadanie w tle).
import {
  h, icon, button, iconButton, viewHeader, split, textarea, input, select, segmented, badge, toast, modal, clear,
  emptyState, progressBar, callout, kbd, debounce,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const STARTER = `#include <bits/stdc++.h>
using namespace std;

int main() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    // sortowanie przez wstawianie
    for (int i = 1; i < n; i++) {
        int x = a[i];
        int j = i - 1;
        while (j >= 0 && a[j] > x) {
            a[j + 1] = a[j];
            j--;
        }
        a[j + 1] = x;
    }
    for (int i = 0; i < n; i++) cout << a[i] << " ";
    cout << "\\n";
}
`;

// zmienne indeksowe rysowane jako wskaźniki nad komórkami tablic
const POINTER_NAMES = new Set(['i', 'j', 'k', 'l', 'r', 'p', 'q', 'lo', 'hi', 'mid', 'left', 'right', 'pos', 'idx', 'ptr',
  'cur', 'u', 'v', 'st', 'en', 'beg', 'end', 'start', 'stop', 'L', 'R', 'lft', 'rgt', 'a', 'b', 'x', 'y', 'w', 'top',
  'head', 'tail', 'fr', 'bk', 'id', 'ind', 'pivot', 'ii', 'jj', 'kk', 'i1', 'i2', 'j1', 'j2']);
const POINTER_RX = /^([ijklrpq]|lo|hi|mid)\d?$/;
const ROW_NAMES = ['i', 'r', 'row', 'x', 'y1', 'ii', 'a'];
const COL_NAMES = ['j', 'c', 'col', 'y', 'x1', 'jj', 'b'];
const SPEEDS = [{ value: '0.5', label: '0,5×' }, { value: '1', label: '1×' }, { value: '2', label: '2×' }, { value: '4', label: '4×' }, { value: '10', label: '10×' }];

const isIntStr = (s) => typeof s === 'string' && /^-?\d+$/.test(s);
const asNode = (x) => (x && typeof x === 'object' ? x : { kind: 'scalar', value: x });
const cellText = (x) => {
  if (x === null || x === undefined) return '';
  if (typeof x !== 'object') return String(x);
  if (x.kind === 'pair') return '(' + (x.items || []).map(cellText).join(', ') + ')';
  if (x.kind === 'string') return '"' + x.value + '"';
  if (x.kind === 'scalar') return String(x.value);
  if (x.items) return '[' + x.items.slice(0, 6).map(cellText).join(', ') + (x.items.length > 6 ? ', …' : '') + ']';
  if (x.fields) return '{' + Object.entries(x.fields).slice(0, 4).map(([k, v]) => `${k}: ${cellText(v)}`).join(', ') + '}';
  return '…';
};
const numOf = (x) => {
  if (typeof x === 'number') return x;
  if (typeof x !== 'string') return NaN;
  if (/^'.'$/.test(x)) return x.charCodeAt(1);
  if (x === 'true') return 1;
  if (x === 'false') return 0;
  return parseFloat(x);
};
const short = (s, n = 18) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s);

function hashHue(name) {
  let x = 0;
  for (const c of name) x = (x * 31 + c.charCodeAt(0)) >>> 0;
  return x % 6;
}

function flash(el, cls = 'dbg-flash') {
  if (!el) return;
  el.classList.remove(cls);
  void el.offsetWidth;          // restart animacji
  el.classList.add(cls);
}

// ============================================================================================ widżety zmiennych
function scalarWidget(name) {
  const val = h('span', { class: 'dbg-sv-val mono' });
  const el = h('div', { class: 'dbg-scalar' }, h('span', { class: 'dbg-sv-name mono' }, name), h('span', { class: 'dbg-sv-eq' }, '='), val);
  return {
    el, kind: 'scalar',
    update(node) {
      const v = node.kind === 'string' ? '"' + node.value + '"' : String(node.value ?? '');
      val.textContent = short(v, 60);
      el.title = `${node.type || ''} ${name} = ${v}`.trim();
      el.classList.toggle('is-new', !!node.new);
      el.classList.toggle('is-arg', node.scope === 'arg');
      el.classList.toggle('is-global', node.scope === 'global');
      el.classList.toggle('is-ptr', !!node._ptr);
      el.dataset.hue = node._ptr ? hashHue(name) : '';
      if (node.changed) flash(el);
    },
  };
}

// tablica / wektor / napis jako rząd komórek ze wskaźnikami
function arrayWidget(name, view) {
  const head = h('div', { class: 'dbg-w-head' });
  const ptrRow = h('div', { class: 'dbg-ptrs' });
  const cellsRow = h('div', { class: 'dbg-cells' });
  const idxRow = h('div', { class: 'dbg-idx' });
  const lane = h('div', { class: 'dbg-lane' }, ptrRow, cellsRow, idxRow);
  const scroller = h('div', { class: 'dbg-scroll' }, lane);
  const note = h('div', { class: 'dbg-w-note small muted' });
  const el = h('div', { class: 'dbg-widget dbg-array' }, head, scroller, note);
  let cells = [];
  let idxs = [];
  const ptrEls = new Map();
  let showAll = false;
  let lastNode = null;
  let lastPtrs = [];
  const toggle = h('button', { class: 'dbg-mini-btn', type: 'button', onclick: () => { showAll = !showAll; w.update(lastNode, lastPtrs, true); } });

  const w = {
    el, kind: 'array',
    update(node, ptrs = [], force = false) {
      lastNode = node;
      lastPtrs = ptrs;
      const isStr = node.kind === 'string';
      const items = isStr ? [...(node.value || '')].map((c) => `'${c}'`) : (node.items || []);
      const size = isStr ? items.length : (node.size ?? items.length);
      // auto-przycinanie dużych tablic: do ostatniego „używanego” indeksu
      let shown = items.length;
      const used = view.usedIndex(name);
      const maxPtr = ptrs.reduce((m, p) => Math.max(m, p.value), -1);
      if (!showAll && items.length > 40) shown = Math.min(items.length, Math.max(12, used + 3, maxPtr + 3));
      clear(head).append(
        h('span', { class: 'dbg-w-name mono' }, name),
        h('span', { class: 'dbg-w-type' }, node.type || (isStr ? 'string' : '')),
        h('span', { class: 'dbg-w-size' }, isStr ? `długość ${size}` : `rozmiar ${size}`),
        node.note ? badge(node.note, 'info') : null,
        node.scope === 'global' ? badge('globalna', 'neutral') : node.scope === 'arg' ? badge('argument', 'accent') : null,
        items.length > 40 ? (toggle.textContent = showAll ? 'Przytnij' : `Pokaż całość (${items.length})`, toggle) : null);
      // komórki (przebudowa tylko przy zmianie liczby)
      const n = Math.min(shown, items.length);
      if (cells.length !== n || force) {
        clear(cellsRow); clear(idxRow);
        cells = []; idxs = [];
        for (let i = 0; i < n; i++) {
          const c = h('div', { class: 'dbg-cell' });
          const ix = h('div', { class: 'dbg-ix' }, String(i));
          cells.push(c); idxs.push(ix);
          cellsRow.append(c); idxRow.append(ix);
        }
        if (n < size) {
          cellsRow.append(h('div', { class: 'dbg-cell dbg-more', title: 'Pozostałe elementy ukryte' }, `+${size - n}`));
          idxRow.append(h('div', { class: 'dbg-ix' }, '…'));
        }
      }
      const changed = new Set(node.changed_idx || []);
      const nums = items.slice(0, n).map((x) => numOf(typeof x === 'object' ? NaN : x));
      const finite = nums.filter((x) => Number.isFinite(x));
      const mn = Math.min(...finite), mx = Math.max(...finite);
      const bars = finite.length > 1 && mx > mn && !isStr && finite.length === nums.length;
      el.classList.toggle('with-bars', bars);
      for (let i = 0; i < n; i++) {
        const txt = cellText(items[i]);
        const c = cells[i];
        if (c.textContent !== txt) c.textContent = txt;
        c.title = `${name}[${i}] = ${txt}`;
        c.classList.toggle('long', txt.length > 6);
        if (bars) c.style.setProperty('--fill', `${Math.round(((nums[i] - mn) / (mx - mn)) * 100)}%`);
        else c.style.removeProperty('--fill');
        if (changed.has(i) || (node.changed && node.new)) flash(c, 'dbg-cell-flash');
      }
      // wskaźniki: strzałki nad komórkami (płynnie przesuwane)
      const live = new Set();
      const slot = new Map();
      for (const p of ptrs) {
        if (p.value < 0 || p.value > n) continue;
        live.add(p.name);
        let pe = ptrEls.get(p.name);
        if (!pe) {
          pe = h('div', { class: 'dbg-ptr' }, h('span', { class: 'dbg-ptr-name mono' }, p.name), h('span', { class: 'dbg-ptr-arrow' }));
          pe.dataset.hue = hashHue(p.name);
          ptrEls.set(p.name, pe);
          ptrRow.append(pe);
        }
        const k = slot.get(p.value) || 0;
        slot.set(p.value, k + 1);
        let left = 0, width = 38;
        if (p.value < n) { left = cells[p.value].offsetLeft; width = cells[p.value].offsetWidth; }
        else if (n) { const lc = cells[n - 1]; left = lc.offsetLeft + lc.offsetWidth + 4; width = lc.offsetWidth; }
        pe.style.left = `${left}px`;
        pe.style.width = `${width}px`;
        pe.style.setProperty('--lvl', k);
        pe.classList.toggle('past-end', p.value === n);
        pe.title = `${p.name} = ${p.value}`;
      }
      for (const [nm, pe] of ptrEls) if (!live.has(nm)) { pe.remove(); ptrEls.delete(nm); }
      const lv = Math.max(0, ...slot.values());
      ptrRow.style.height = `${Math.max(1, lv) * 18 + 6}px`;
      for (let i = 0; i < n; i++) cells[i].classList.toggle('pointed', slot.has(i));
      note.textContent = node.truncated ? `Pokazano ${items.length} z ${size} elementów (limit śledzenia).` : '';
    },
  };
  return w;
}

// macierz 2D jako siatka z mapą ciepła
function matrixWidget(name) {
  const head = h('div', { class: 'dbg-w-head' });
  const grid = h('div', { class: 'dbg-grid' });
  const el = h('div', { class: 'dbg-widget dbg-matrix' }, head, h('div', { class: 'dbg-scroll' }, grid), h('div', { class: 'dbg-w-note small muted' }));
  let dims = '';
  let cellEls = [];
  return {
    el, kind: 'matrix',
    update(node, ptrs = []) {
      const rows = node.rows || [];
      const R = rows.length;
      const C = Math.max(0, ...rows.map((r) => r.length));
      const isChar = node.elem === 'char' || rows.some((r) => r.some((x) => typeof x === 'string' && /^'.*'$/.test(x)));
      clear(head).append(h('span', { class: 'dbg-w-name mono' }, name), h('span', { class: 'dbg-w-type' }, node.type || ''),
        h('span', { class: 'dbg-w-size' }, `${(node.size || [R, C])[0]} × ${(node.size || [R, C])[1]}`),
        node.scope === 'global' ? badge('globalna', 'neutral') : null);
      const key = `${R}x${C}:${rows.map((r) => r.length).join(',')}`;
      if (key !== dims) {
        dims = key;
        clear(grid);
        grid.style.gridTemplateColumns = `28px repeat(${C}, minmax(30px, auto))`;
        grid.append(h('div', { class: 'dbg-gh corner' }));
        for (let c = 0; c < C; c++) grid.append(h('div', { class: 'dbg-gh col', dataset: { c } }, String(c)));
        cellEls = [];
        for (let r = 0; r < R; r++) {
          grid.append(h('div', { class: 'dbg-gh row', dataset: { r } }, String(r)));
          const rowEls = [];
          for (let c = 0; c < C; c++) {
            const ce = h('div', { class: c < rows[r].length ? 'dbg-mc' : 'dbg-mc void' });
            rowEls.push(ce);
            grid.append(ce);
          }
          cellEls.push(rowEls);
        }
      }
      let mn = Infinity, mx = -Infinity;
      if (!isChar) for (const r of rows) for (const x of r) { const v = numOf(x); if (Number.isFinite(v)) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }
      const changed = new Set((node.changed_cells || []).map(([r, c]) => r * 100000 + c));
      const ri = ptrs.find((p) => ROW_NAMES.includes(p.name) && p.value < R);
      const ci = ptrs.find((p) => COL_NAMES.includes(p.name) && p.value < C && p !== ri);
      for (let r = 0; r < R; r++) {
        for (let c = 0; c < rows[r].length; c++) {
          const ce = cellEls[r][c];
          const x = rows[r][c];
          const t = isChar ? String(x).replace(/^'(.*)'$/, '$1') : String(x);
          if (ce.textContent !== t) ce.textContent = t;
          const v = numOf(x);
          if (!isChar && Number.isFinite(v) && mx > mn) ce.style.setProperty('--heat', `${Math.round(((v - mn) / (mx - mn)) * 62)}%`);
          else ce.style.setProperty('--heat', '0%');
          if (isChar) ce.dataset.ch = t;
          ce.classList.toggle('in-row', !!ri && ri.value === r);
          ce.classList.toggle('in-col', !!ci && ci.value === c);
          ce.classList.toggle('cross', !!ri && !!ci && ri.value === r && ci.value === c);
          ce.title = `${name}[${r}][${c}] = ${x}`;
          if (changed.has(r * 100000 + c)) flash(ce, 'dbg-cell-flash');
        }
      }
      grid.querySelectorAll('.dbg-gh.row').forEach((e) => e.classList.toggle('on', !!ri && +e.dataset.r === ri.value));
      grid.querySelectorAll('.dbg-gh.col').forEach((e) => e.classList.toggle('on', !!ci && +e.dataset.c === ci.value));
      el.classList.toggle('chars', isChar);
      el.querySelector('.dbg-w-note').textContent = [
        ri ? `wiersz: ${ri.name}=${ri.value}` : '', ci ? `kolumna: ${ci.name}=${ci.value}` : '',
        node.truncated ? 'pokazano fragment (limit śledzenia)' : ''].filter(Boolean).join(' · ');
    },
  };
}

// mapa: tabela klucz → wartość
function mapWidget(name) {
  const head = h('div', { class: 'dbg-w-head' });
  const body = h('div', { class: 'dbg-map' });
  const el = h('div', { class: 'dbg-widget' }, head, body);
  return {
    el, kind: 'map',
    update(node) {
      clear(head).append(h('span', { class: 'dbg-w-name mono' }, name), h('span', { class: 'dbg-w-type' }, node.type || ''),
        h('span', { class: 'dbg-w-size' }, `${node.size ?? (node.items || []).length} el.`), node.scope === 'global' ? badge('globalna', 'neutral') : null);
      clear(body);
      const ch = new Set(node.changed_idx || []);
      (node.items || []).forEach(([k, v], i) => {
        const row = h('div', { class: 'dbg-map-row' }, h('span', { class: 'dbg-map-k mono' }, cellText(k)), h('span', { class: 'dbg-map-arrow' }, '→'),
          h('span', { class: 'dbg-map-v mono' }, cellText(v)));
        body.append(row);
        if (ch.has(i)) flash(row, 'dbg-cell-flash');
      });
      if (!(node.items || []).length) body.append(h('div', { class: 'muted small' }, 'pusta'));
      if (node.truncated) body.append(h('div', { class: 'muted small' }, `… i ${node.size - node.items.length} więcej`));
    },
  };
}

// set, para/krotka, struktura
function genericWidget(name) {
  const head = h('div', { class: 'dbg-w-head' });
  const body = h('div', { class: 'dbg-generic' });
  const el = h('div', { class: 'dbg-widget' }, head, body);
  return {
    el, kind: 'generic',
    update(node) {
      clear(head).append(h('span', { class: 'dbg-w-name mono' }, name), h('span', { class: 'dbg-w-type' }, node.type || ''),
        node.size !== undefined ? h('span', { class: 'dbg-w-size' }, `${node.size} el.`) : null,
        node.scope === 'global' ? badge('globalna', 'neutral') : node.scope === 'arg' ? badge('argument', 'accent') : null);
      clear(body);
      const ch = new Set(node.changed_idx || []);
      if (node.kind === 'set') {
        (node.items || []).forEach((x, i) => { const p = h('span', { class: 'dbg-pill mono' }, cellText(x)); body.append(p); if (ch.has(i)) flash(p, 'dbg-cell-flash'); });
        if (!(node.items || []).length) body.append(h('span', { class: 'muted small' }, 'pusty'));
      } else if (node.kind === 'pair') {
        body.append(h('span', { class: 'dbg-tuple mono' }, '(', (node.items || []).map((x, i) => [i ? ', ' : '', h('span', { class: ch.has(i) ? 'dbg-hl' : '' }, cellText(x))]), ')'));
      } else if (node.kind === 'struct') {
        const chf = new Set(node.changed_fields || []);
        for (const [k, v] of Object.entries(node.fields || {})) {
          const r = h('div', { class: 'dbg-map-row' }, h('span', { class: 'dbg-map-k mono' }, '.' + k), h('span', { class: 'dbg-map-arrow' }, '='), h('span', { class: 'dbg-map-v mono' }, cellText(v)));
          body.append(r);
          if (chf.has(k)) flash(r, 'dbg-cell-flash');
        }
      } else {
        body.append(h('span', { class: 'mono' }, cellText(node)));
      }
      if (node.changed && !node.new) flash(el, 'dbg-flash-soft');
    },
  };
}

function widgetKind(node) {
  const k = node.kind;
  if (k === 'array' || k === 'vector') {
    const its = node.items || [];
    if (its.some((x) => x && typeof x === 'object' && x.kind !== 'pair' && x.kind !== 'scalar')) return 'generic-seq';
    return 'array';
  }
  if (k === 'string') return (node.value || '').length <= 48 ? 'array' : 'scalar';
  if (k === 'matrix') return 'matrix';
  if (k === 'map') return 'map';
  if (k === 'set' || k === 'pair' || k === 'struct') return 'generic';
  return 'scalar';
}

// ============================================================================================ widok
export default {
  id: 'debugger',
  title: 'Wizualny debugger',
  icon: 'footprints',
  group: 'Kod',
  order: 30,
  layout: 'full',
  description: 'Śledź program linia po linii: wartości zmiennych, tablice ze wskaźnikami i, j, l, r, mid, macierze jako mapa ciepła, rekurencja na stosie i wyjście programu — z przewijaniem w przód i w tył.',
  keywords: ['debugger', 'debug', 'krok po kroku', 'linia po linii', 'wizualizacja', 'zmienne', 'tablice', 'gdb', 'lldb',
    'śledzenie', 'trace', 'rekurencja', 'stos wywołań', 'wskaźniki'],

  render(root, ctx) {
    const view = this;
    let caps = null;
    let trace = null;          // wynik śledzenia
    let cur = 0;               // indeks kroku
    let playing = null;        // id interwału
    let speed = parseFloat(local.get('debugger:speed', '1')) || 1;
    let jobCtl = null;
    let lineHits = new Map();  // linia -> [indeksy kroków]
    let usedIdx = new Map();   // nazwa tablicy -> maks. używany indeks (do przycinania)
    const widgets = new Map(); // klucz -> widżet
    view._state = () => ({ trace, cur });

    // ---------------------------------------------------------------- lewa kolumna: kod + wejście + opcje
    const edHost = h('div', { class: 'dbg-editor' });
    const inputEl = textarea({ value: local.get('debugger:input', '5\n5 2 4 6 1\n'), rows: 4, placeholder: 'Wejście programu (stdin)…',
      onInput: debounce((v) => local.set('debugger:input', v), 300) });
    const maxStepsEl = input({ value: local.get('debugger:maxSteps', '3000'), type: 'number', size: 'sm', mono: true,
      attrs: { min: 10, max: 20000, step: 100, title: 'Limit kroków (10–20000)' }, onInput: (v) => local.set('debugger:maxSteps', v) });
    const watchEl = input({ value: local.get('debugger:watch', ''), size: 'sm', mono: true, placeholder: 'np. a, i, j (puste = wszystkie)',
      onInput: (v) => local.set('debugger:watch', v), onEnter: () => run() });
    const langEl = select([{ value: 'auto', label: 'Język: auto' }, { value: 'cpp', label: 'C++' }, { value: 'python', label: 'Python' }],
      { value: local.get('debugger:lang', 'auto'), size: 'sm', onChange: (v) => { local.set('debugger:lang', v); if (v !== 'auto') ed.setLang(v); } });
    const runBtn = button('Śledź program', { icon: 'footprints', variant: 'primary', onClick: () => run(), title: 'Ctrl+Enter' });
    const editBtn = button('Edytuj kod', { icon: 'pencil', variant: 'ghost', onClick: () => leaveTrace() });
    editBtn.hidden = true;
    const progress = progressBar({ label: 'Śledzenie…' });
    const cancelBtn = button('Przerwij', { icon: 'circle-stop', size: 'sm', variant: 'ghost', onClick: () => jobCtl && jobCtl.cancel() });
    const progressBox = h('div', { class: 'dbg-progress', hidden: true }, progress.el, cancelBtn);
    const capsLine = h('div', { class: 'dbg-caps small muted' });
    const left = h('div', { class: 'dbg-col dbg-left' },
      edHost,
      h('div', { class: 'dbg-inputs' },
        h('div', { class: 'dbg-in-head' }, h('span', { class: 'section-title' }, 'Wejście programu'), h('span', { class: 'grow' }), capsLine),
        inputEl,
        h('div', { class: 'dbg-opts' },
          h('label', { class: 'dbg-opt' }, h('span', null, 'Limit kroków'), maxStepsEl),
          h('label', { class: 'dbg-opt grow' }, h('span', null, 'Śledź tylko zmienne:'), watchEl),
          langEl),
        h('div', { class: 'dbg-run-row' }, runBtn, editBtn, h('span', { class: 'grow' }), h('span', { class: 'small muted' }, kbd('Ctrl'), '+', kbd('Enter'))),
        progressBox));

    const ed = createEditor(edHost, { value: STARTER, lang: 'cpp', height: '100%', storageKey: 'debugger:code', title: 'Kod programu',
      onRun: () => run(), langSelect: false });
    const cm = ed.cm;
    cm.setOption('gutters', ['dbg-gutter', ...(cm.getOption('gutters') || [])]);
    cm.on('gutterClick', (_cm, line, gutter) => {
      if (!trace) return;
      if (gutter === 'CodeMirror-linenumbers' || gutter === 'dbg-gutter') nextExecutionOf(line + 1);
    });

    // ---------------------------------------------------------------- prawa kolumna
    const bFirst = iconButton('skip-back', { title: 'Pierwszy krok (Home)', onClick: () => go(0) });
    const bPrev = iconButton('chevron-left', { title: 'Poprzedni krok (←)', onClick: () => go(cur - 1) });
    const bPlay = button('Odtwarzaj', { icon: 'play', variant: 'primary', size: 'sm', onClick: () => togglePlay(), title: 'Spacja' });
    const bNext = iconButton('chevron-right', { title: 'Następny krok (→)', onClick: () => go(cur + 1) });
    const bLast = iconButton('skip-forward', { title: 'Ostatni krok (End)', onClick: () => go((trace?.steps.length || 1) - 1) });
    const speedEl = segmented(SPEEDS, { value: String(speed), onChange: (v) => { speed = parseFloat(v); local.set('debugger:speed', v); if (playing) { stop(); play(); } } });
    const slider = h('input', { type: 'range', class: 'dbg-slider', min: 0, max: 0, value: 0, oninput: (e) => go(+e.target.value) });
    const stepLbl = h('div', { class: 'dbg-step-lbl mono' }, 'krok 0 / 0');
    const lineLbl = h('div', { class: 'dbg-line-lbl' });
    const flowCanvas = h('canvas', { class: 'dbg-flow', height: 46 });
    flowCanvas.addEventListener('pointerdown', (e) => seekFlow(e));
    flowCanvas.addEventListener('pointermove', (e) => { if (e.buttons & 1) seekFlow(e); });
    const controls = h('div', { class: 'dbg-controls' },
      h('div', { class: 'dbg-ctrl-row' }, bFirst, bPrev, bPlay, bNext, bLast, h('span', { class: 'grow' }), speedEl),
      h('div', { class: 'dbg-ctrl-row' }, stepLbl, slider),
      flowCanvas, lineLbl);
    const banner = h('div', { class: 'dbg-banner', hidden: true });
    const varsHost = h('div', { class: 'dbg-vars' });
    const stackHost = h('div', { class: 'dbg-stack' });
    const outHost = h('pre', { class: 'dbg-stdout mono' });
    const statsLine = h('div', { class: 'small muted dbg-stats' });
    const placeholder = emptyState({ icon: 'footprints', title: 'Prześledź swój program krok po kroku',
      text: 'Wpisz kod i wejście, kliknij „Śledź program”. Zobaczysz każdą wykonaną linię, zmiany zmiennych, tablice ze wskaźnikami i stos rekurencji. Klikaj w numery linii, by skoczyć do ich następnego wykonania.',
      action: button('Przykłady', { icon: 'library', onClick: () => gallery() }) });
    const traceBox = h('div', { class: 'dbg-trace', hidden: true },
      controls, banner,
      h('div', { class: 'dbg-sec' }, h('div', { class: 'dbg-sec-title' }, icon('braces', 14), 'Zmienne'), varsHost),
      h('div', { class: 'dbg-bottom' },
        h('div', { class: 'dbg-sec' }, h('div', { class: 'dbg-sec-title' }, icon('layers', 14), 'Stos wywołań'), stackHost),
        h('div', { class: 'dbg-sec' }, h('div', { class: 'dbg-sec-title' }, icon('square-terminal', 14), 'Wyjście programu', h('span', { class: 'grow' }), statsLine), outHost)));
    const errorBox = h('div', { class: 'dbg-error', hidden: true });
    const right = h('div', { class: 'dbg-col dbg-right' }, placeholder, errorBox, traceBox);

    root.append(
      viewHeader(this, button('Przykłady', { icon: 'library', onClick: () => gallery() })),
      h('div', { class: 'dbg-body' }, split(left, right, { ratio: 0.46, min: 340, storageKey: 'debugger' })));
    root.tabIndex = -1;

    api.get('/api/debugger/capabilities', null, { quiet: true }).then((r) => {
      caps = r.capabilities;
      capsLine.replaceChildren(
        caps.cpp ? badge(`C++: ${caps.cpp}`, 'success', 'cpu') : badge('C++: brak debuggera', 'warn', 'triangle-alert'),
        badge('Python', 'info'));
      if (caps.notes?.length) capsLine.title = caps.notes.join('\n');
    }).catch(() => {});

    // ---------------------------------------------------------------- śledzenie
    async function run() {
      const code = ed.getValue();
      if (!code.trim()) { toast('Wpisz kod programu.', 'warn'); return; }
      stop();
      const langSel = langEl.value;
      const body = { code, input: inputEl.value, max_steps: parseInt(maxStepsEl.value, 10) || 3000, watch: watchEl.value };
      if (langSel !== 'auto') body.lang = langSel;
      runBtn.disabled = true;
      progressBox.hidden = false;
      progress.set(0, 'Przygotowanie…');
      errorBox.hidden = true;
      ed.clearMarks();
      try {
        jobCtl = api.job('/api/debugger/trace', body, { onProgress: (p, msg) => progress.set(p, msg || 'Śledzenie…'), quiet: true });
        const res = await jobCtl.promise;
        load(res);
      } catch (e) {
        if (!e.cancelled) showError(e.message || String(e));
        else toast('Przerwano śledzenie.', 'info');
      } finally {
        runBtn.disabled = false;
        progressBox.hidden = true;
        jobCtl = null;
      }
    }

    function showError(msg, res) {
      placeholder.hidden = true;
      traceBox.hidden = true;
      errorBox.hidden = false;
      const parts = [callout(msg, 'danger')];
      if (res?.suggest_python) parts.push(callout('Możesz prześledzić ten sam algorytm napisany w **Pythonie** — wybierz język „Python”.', 'info'));
      if (res?.compile_errors?.length) {
        parts.push(h('div', { class: 'dbg-cerrs' }, res.compile_errors.slice(0, 8).map((e) =>
          h('div', { class: 'dbg-cerr' }, h('span', { class: 'mono dbg-cerr-line' }, e.line ? `linia ${e.line}` : ''), h('b', null, e.title || ''),
            e.explanation ? h('div', { class: 'small muted' }, e.explanation) : null))));
        ed.markLines(res.compile_errors.filter((e) => e.line).map((e) => ({ line: e.line, type: 'error', message: e.title })));
      } else if (res?.compile?.stderr) {
        parts.push(h('pre', { class: 'dbg-stderr mono' }, res.compile.stderr.slice(0, 4000)));
      }
      if (res?.error_line) ed.markLines([{ line: res.error_line, type: 'error', message: msg }]);
      errorBox.replaceChildren(...parts);
    }

    function load(res) {
      if (res.error && !(res.steps || []).length) { showError(res.error, res); return; }
      trace = res;
      if (res.lang && res.lang !== ed.getLang()) ed.setLang(res.lang);
      ed.setValue(res.source ?? ed.getValue());
      enterTrace();
      lineHits = new Map();
      usedIdx = new Map();
      res.steps.forEach((s, i) => {
        if (!lineHits.has(s.line)) lineHits.set(s.line, []);
        lineHits.get(s.line).push(i);
        for (const [nm, nd] of Object.entries(s.vars || {})) {
          if (nd.kind !== 'array' && nd.kind !== 'vector') continue;
          const its = nd.items || [];
          let last = usedIdx.get(nm) ?? 0;
          for (let k = its.length - 1; k > last; k--) { if (its[k] !== '0' && its[k] !== 'false' && its[k] !== "'\\0'" && its[k] !== '') { last = k; break; } }
          for (const [pn, pnd] of Object.entries(s.vars)) if (isPointerVar(pn, pnd)) { const v = +pnd.value; if (v > last && v < its.length) last = v; }
          usedIdx.set(nm, last);
        }
      });
      gutterCounts();
      widgets.clear();
      clear(varsHost);
      slider.max = Math.max(0, res.steps.length - 1);
      placeholder.hidden = true;
      errorBox.hidden = true;
      traceBox.hidden = false;
      drawFlow();
      const st = res.stats || {};
      statsLine.textContent = [`${res.steps.length} kroków`, st.elapsed ? `${st.elapsed.toFixed(1)} s` : '', st.steps_per_s ? `${Math.round(st.steps_per_s)} kroków/s` : '',
        res.backend ? `backend: ${res.backend}` : ''].filter(Boolean).join(' · ');
      cur = -1;
      go(0);
      if (res.error) toast(res.error, 'warn');
    }
    view._load = load;

    function enterTrace() {
      ed.setReadOnly(true);
      ed.el.classList.add('dbg-tracing');
      editBtn.hidden = false;
    }

    function leaveTrace() {
      stop();
      trace = null;
      ed.setReadOnly(false);
      ed.el.classList.remove('dbg-tracing');
      ed.highlightLine(0, 'dbg-line-current');
      ed.highlightLine(0, 'dbg-line-prev');
      ed.highlightLine(0, 'dbg-line-crash');
      cm.clearGutter('dbg-gutter');
      editBtn.hidden = true;
      traceBox.hidden = true;
      placeholder.hidden = false;
      ed.focus();
    }

    function gutterCounts() {
      cm.clearGutter('dbg-gutter');
      for (const [ln, idx] of lineHits) {
        if (ln < 1 || ln > cm.lineCount()) continue;
        cm.setGutterMarker(ln - 1, 'dbg-gutter', h('div', { class: 'dbg-gm', title: `Linia ${ln} wykonana ${idx.length}× — kliknij, by przejść do następnego wykonania` }, String(idx.length)));
      }
    }

    function nextExecutionOf(line) {
      const idx = lineHits.get(line);
      if (!idx || !idx.length) { toast(`Linia ${line} nie została wykonana w prześledzonej części programu.`, 'info', { timeout: 2200 }); return; }
      const nxt = idx.find((i) => i > cur);
      go(nxt !== undefined ? nxt : idx[0]);
      if (nxt === undefined) toast(`Wrócono do pierwszego wykonania linii ${line}.`, 'info', { timeout: 1600 });
    }

    function isPointerVar(name, node) {
      if (!node || node.kind !== 'scalar' || !isIntStr(String(node.value))) return false;
      const base = name.replace(/^::/, '');
      return POINTER_NAMES.has(base) || POINTER_RX.test(base);
    }

    // ---------------------------------------------------------------- krok
    let prevArrowLine = null;
    function go(i) {
      if (!trace || !trace.steps.length) return;
      i = Math.max(0, Math.min(trace.steps.length - 1, i));
      if (i === cur) return;
      const back = i < cur;
      cur = i;
      const st = trace.steps[i];
      const prev = i > 0 ? trace.steps[i - 1] : null;
      slider.value = i;
      stepLbl.textContent = `krok ${i + 1} / ${trace.steps.length}`;
      // edytor: bieżąca linia + strzałka, poprzednia linia lżej
      ed.highlightLine(st.line, 'dbg-line-current');
      ed.highlightLine(prev && prev.line !== st.line ? prev.line : 0, 'dbg-line-prev', { scroll: false });
      ed.highlightLine(st.crash ? st.line : 0, 'dbg-line-crash', { scroll: false });
      if (prevArrowLine) {
        const hits = lineHits.get(prevArrowLine) || [];
        if (prevArrowLine <= cm.lineCount()) cm.setGutterMarker(prevArrowLine - 1, 'dbg-gutter', hits.length ? h('div', { class: 'dbg-gm' }, String(hits.length)) : null);
      }
      if (st.line <= cm.lineCount()) {
        const hits = lineHits.get(st.line) || [];
        const nth = hits.indexOf(i) + 1;
        cm.setGutterMarker(st.line - 1, 'dbg-gutter', h('div', { class: 'dbg-gm cur', title: `wykonanie ${nth} z ${hits.length}` }, icon('play', 11)));
      }
      prevArrowLine = st.line;
      lineLbl.replaceChildren(
        h('span', { class: 'dbg-fn mono' }, `${st.func}()`), h('span', { class: 'muted' }, ' · linia '), h('b', { class: 'mono' }, String(st.line)),
        h('span', { class: 'muted' }, ` · głębokość ${st.depth}`),
        st.ret ? h('span', { class: 'dbg-ret' }, icon('rotate-ccw', 12), ` ${st.ret.from}() zwróciło `, h('b', { class: 'mono' }, st.ret.value ?? '…')) : null,
        back ? h('span', { class: 'dbg-back' }, icon('history', 12), ' cofanie') : null);
      renderBanner(st, i);
      renderVars(st);
      renderStack(st);
      renderOut(st, prev);
      drawFlowCursor();
    }

    function renderBanner(st, i) {
      const last = i === trace.steps.length - 1;
      let msg = null, tone = 'info', ic = 'info';
      if (st.crash) { msg = st.crash; tone = 'danger'; ic = 'triangle-alert'; }
      else if (st.timeout) { msg = st.timeout; tone = 'warn'; ic = 'timer'; }
      else if (last && trace.reason === 'exit') { msg = `Program zakończył działanie (kod wyjścia ${trace.exit_code ?? 0}).`; tone = 'success'; ic = 'circle-check'; }
      else if (last && trace.message) { msg = trace.message; tone = trace.reason === 'crash' ? 'danger' : 'warn'; ic = 'triangle-alert'; }
      banner.hidden = !msg;
      banner.className = `dbg-banner ${tone}`;
      if (msg) banner.replaceChildren(icon(ic, 16), h('span', null, msg));
    }

    function renderVars(st) {
      const vars = st.vars || {};
      const ptrs = [];
      for (const [nm, nd] of Object.entries(vars)) if (isPointerVar(nm, nd)) ptrs.push({ name: nm.replace(/^::/, ''), value: parseInt(nd.value, 10) });
      const ptrSet = new Set(ptrs.map((p) => p.name));
      const entries = Object.entries(vars);
      const order = (nd) => ({ arg: 0, local: 1, global: 2 }[nd.scope] ?? 1);
      entries.sort((a, b) => order(a[1]) - order(b[1]));
      const groups = { scalars: [], big: [] };
      for (const [nm, nd] of entries) {
        const wk = widgetKind(nd);
        nd._ptr = ptrSet.has(nm);
        (wk === 'scalar' ? groups.scalars : groups.big).push([nm, nd, wk]);
      }
      const seen = new Set();
      // skalary — zwarty panel
      let sHost = varsHost.querySelector(':scope > .dbg-scalars');
      if (!sHost) { sHost = h('div', { class: 'dbg-scalars' }); varsHost.prepend(sHost); }
      const sOrder = [];
      for (const [nm, nd] of groups.scalars) {
        const key = `${st.frame}|${nm}|s`;
        const altKey = `*|${nm}|s`;
        let w = widgets.get(key) || widgets.get(altKey);
        if (!w || w.kind !== 'scalar') { w = scalarWidget(nm); }
        widgets.delete(altKey);
        widgets.set(key, w);
        w.update(nd);
        seen.add(key);
        sOrder.push(w.el);
      }
      if (!groups.scalars.length) sOrder.push(h('div', { class: 'small muted' }, 'Brak zmiennych prostych w tym miejscu.'));
      sHost.replaceChildren(...sOrder);
      // kontenery
      let bHost = varsHost.querySelector(':scope > .dbg-bigs');
      if (!bHost) { bHost = h('div', { class: 'dbg-bigs' }); varsHost.append(bHost); }
      const bOrder = [];
      const todo = [];
      for (const [nm, nd, wk] of groups.big) {
        const key = `${nm}|${wk}`;
        let w = widgets.get(key);
        if (!w) {
          w = wk === 'array' ? arrayWidget(nm, view) : wk === 'matrix' ? matrixWidget(nm) : wk === 'map' ? mapWidget(nm) : genericWidget(nm);
          widgets.set(key, w);
        }
        const myPtrs = wk === 'array' || wk === 'matrix' ? ptrs.filter((p) => p.name !== nm) : [];
        todo.push([w, nd, myPtrs]);
        seen.add(key);
        bOrder.push(w.el);
      }
      bHost.replaceChildren(...bOrder);       // najpierw w DOM — wskaźniki mierzą pozycje komórek
      for (const [w, nd, myPtrs] of todo) { try { w.update(nd, myPtrs); } catch (e) { console.error(e); } }
      if (!groups.big.length) bHost.replaceChildren(h('div', { class: 'small muted' }, 'Brak tablic i kontenerów w tym miejscu.'));
      for (const k of [...widgets.keys()]) if (!seen.has(k)) widgets.delete(k);
    }
    view.usedIndex = (name) => usedIdx.get(name) ?? 0;

    function renderStack(st) {
      const frames = st.stack || [];
      const depth = st.depth ?? frames.length - 1;
      const els = [];
      const shown = frames.slice(0, 14);
      shown.forEach((f, k) => {
        const lvl = depth - k;
        els.push(h('div', { class: ['dbg-frame', k === 0 ? 'top' : ''].join(' '), style: { '--lvl': Math.min(lvl, 12) },
          title: k === 0 ? 'Bieżąca ramka' : `Wywołanie czeka w linii ${f.line}`,
          onclick: () => { if (f.line) ed.scrollToLine(f.line, true); } },
          h('span', { class: 'dbg-frame-depth mono' }, String(lvl)),
          h('span', { class: 'dbg-frame-fn mono' }, f.func, h('span', { class: 'dbg-frame-args' }, `(${short(f.args || '', 40)})`)),
          h('span', { class: 'dbg-frame-line mono' }, `:${f.line}`)));
      });
      if (frames.length > shown.length) els.push(h('div', { class: 'small muted dbg-frame-more' }, `… i ${frames.length - shown.length} głębszych wywołań (łącznie ${depth + 1})`));
      if (st.ret) els.unshift(h('div', { class: 'dbg-frame ret' }, icon('rotate-ccw', 12), h('span', { class: 'mono' }, `${st.ret.from}() → ${st.ret.value ?? '?'}`)));
      stackHost.replaceChildren(...els);
    }

    function renderOut(st) {
      const full = trace.stdout || '';
      const upto = full.slice(0, st.out ?? 0);
      const fresh = st.stdout || '';
      const oldPart = fresh && upto.endsWith(fresh) ? upto.slice(0, upto.length - fresh.length) : upto;
      outHost.replaceChildren(oldPart, fresh ? h('span', { class: 'dbg-out-new' }, fresh) : null);
      if (!upto) outHost.replaceChildren(h('span', { class: 'muted' }, '(jeszcze nic nie wypisano)'));
      outHost.scrollTop = outHost.scrollHeight;
    }

    // ---------------------------------------------------------------- mapa przepływu (linia w czasie)
    function cssVar(name) { return getComputedStyle(root).getPropertyValue(name).trim() || '#888'; }
    function drawFlow() {
      if (!trace) return;
      const steps = trace.steps;
      const w = flowCanvas.clientWidth || 600;
      const hgt = 46;
      const dpr = window.devicePixelRatio || 1;
      flowCanvas.width = Math.round(w * dpr);
      flowCanvas.height = Math.round(hgt * dpr);
      const g = flowCanvas.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, hgt);
      const lines = steps.map((s) => s.line);
      const lo = Math.min(...lines), hi = Math.max(...lines);
      const span = Math.max(1, hi - lo);
      const accent = cssVar('--accent'), danger = cssVar('--danger'), info = cssVar('--info');
      const maxDepth = Math.max(1, ...steps.map((s) => s.depth || 0));
      const n = steps.length;
      for (let k = 0; k < n; k++) {
        const s = steps[k];
        const x = n === 1 ? w / 2 : (k / (n - 1)) * (w - 6) + 3;
        const y = 4 + ((s.line - lo) / span) * (hgt - 8);
        g.globalAlpha = 0.35 + 0.65 * ((s.depth || 0) / maxDepth);
        g.fillStyle = s.crash ? danger : (s.depth ? info : accent);
        g.fillRect(x - 1, y - 1, n > 600 ? 1.2 : 2.2, 2.2);
      }
      g.globalAlpha = 1;
      flowCanvas._geom = { w, n };
      drawFlowCursor();
    }
    function drawFlowCursor() {
      const box = flowCanvas.parentElement;
      let cur_ = box.querySelector('.dbg-flow-cursor');
      if (!cur_) { cur_ = h('div', { class: 'dbg-flow-cursor' }); box.append(cur_); }
      const geo = flowCanvas._geom;
      if (!geo || !trace) return;
      const x = geo.n === 1 ? geo.w / 2 : (cur / (geo.n - 1)) * (geo.w - 6) + 3;
      cur_.style.transform = `translateX(${flowCanvas.offsetLeft + x}px)`;
      cur_.style.top = `${flowCanvas.offsetTop}px`;
    }
    function seekFlow(e) {
      if (!trace) return;
      const r = flowCanvas.getBoundingClientRect();
      const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      go(Math.round(f * (trace.steps.length - 1)));
    }
    const ro = new ResizeObserver(debounce(() => { if (trace && !traceBox.hidden) drawFlow(); }, 120));
    ro.observe(flowCanvas);

    // ---------------------------------------------------------------- odtwarzanie i klawiatura
    function play() {
      if (!trace) return;
      if (cur >= trace.steps.length - 1) go(0);
      bPlay.replaceChildren(icon('pause', 15), h('span', null, 'Pauza'));
      playing = setInterval(() => {
        if (!trace || cur >= trace.steps.length - 1) { stop(); return; }
        go(cur + 1);
      }, Math.max(30, 700 / speed));
    }
    function stop() {
      if (playing) clearInterval(playing);
      playing = null;
      bPlay.replaceChildren(icon('play', 15), h('span', null, 'Odtwarzaj'));
    }
    function togglePlay() { if (playing) stop(); else play(); }
    view._stop = stop;

    const onKey = (e) => {
      if (!trace || root.hidden || !root.isConnected || traceBox.hidden) return;
      const t = e.target;
      const editable = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' ||
        (t.closest && t.closest('.CodeMirror') && !cm.getOption('readOnly')));
      if (editable || e.metaKey || e.ctrlKey || e.altKey) return;
      if (document.querySelector('.modal-backdrop, .modal')?.offsetParent) return;
      const k = e.key;
      if (k === 'ArrowRight' || k === 'l') { e.preventDefault(); stop(); go(cur + (e.shiftKey ? 10 : 1)); }
      else if (k === 'ArrowLeft' || k === 'h') { e.preventDefault(); stop(); go(cur - (e.shiftKey ? 10 : 1)); }
      else if (k === ' ') { e.preventDefault(); togglePlay(); }
      else if (k === 'Home') { e.preventDefault(); go(0); }
      else if (k === 'End') { e.preventDefault(); go(trace.steps.length - 1); }
      else if (k === 'n') { e.preventDefault(); nextExecutionOf(trace.steps[cur].line); }
    };
    document.addEventListener('keydown', onKey);

    // ---------------------------------------------------------------- przykłady
    async function gallery() {
      let ex = [];
      try { ex = (await api.get('/api/debugger/examples')).examples || []; } catch { return; }
      const m = modal({ title: 'Przykładowe programy do prześledzenia', icon: 'library', width: '860px',
        body: h('div', { class: 'dbg-gallery' }, ex.map((e) => h('button', { class: 'dbg-ex', type: 'button',
          onclick: () => { useExample(e); m.close(); } },
          h('div', { class: 'row gap-s' }, badge(e.lang === 'python' ? 'Python' : 'C++', e.lang === 'python' ? 'info' : 'accent'), h('b', null, e.title)),
          h('pre', { class: 'mono' }, e.code.split('\n').slice(0, 7).join('\n'))))) });
    }
    function useExample(e) {
      if (trace) leaveTrace();
      ed.setValue(e.code);
      ed.setLang(e.lang === 'python' ? 'python' : 'cpp');
      langEl.value = e.lang === 'python' ? 'python' : 'auto';
      inputEl.value = e.input || '';
      local.set('debugger:input', inputEl.value);
      toast(`Wczytano: ${e.title}. Kliknij „Śledź program”.`, 'success', { timeout: 1800 });
    }
    view._useExample = useExample;
    view._leave = () => { if (trace) leaveTrace(); };
    view._ed = ed;
    view._input = inputEl;
    view._run = run;
    view._langEl = langEl;
  },

  onShow(params = {}) {
    if (!this._ed) return;
    if (params.code) {
      this._leave();
      this._ed.setValue(params.code);
      const lang = params.lang || (/^\s*(def |import |from |print\()/m.test(params.code) && !params.code.includes('#include') ? 'python' : 'cpp');
      this._ed.setLang(lang);
      if (this._langEl) this._langEl.value = lang === 'python' ? 'python' : 'auto';
    }
    if (params.input !== undefined) this._input.value = params.input;
    if (params.code && (params.run || params.autorun)) this._run();
  },

  onHide() { if (this._stop) this._stop(); },
};

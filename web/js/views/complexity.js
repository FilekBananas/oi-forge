// (g) Analiza złożoności: statyczna (drzewo pętli, hot-spoty, pamięć, werdykt dla limitu) + pomiar empiryczny z wykresem.
import {
  h, icon, button, viewHeader, split, tabs, field, input, textarea, select, segmented, badge, callout, markdown, toast,
  clear, emptyState, progressBar, table, stat, fileDrop, readFileText, debounce, iconButton, fmtTime, fmtMem, modal,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';
import { lineChart } from '../charts.js';

const VERDICT = {
  ok: { tone: 'success', icon: 'circle-check', label: 'Zmieści się' },
  warn: { tone: 'warn', icon: 'triangle-alert', label: 'Ryzykowne' },
  tle: { tone: 'danger', icon: 'timer', label: 'TLE' },
  unknown: { tone: 'info', icon: 'info', label: 'Brak ograniczeń' },
};
const PATTERN_LABEL = {
  linear: 'liniowa', dependent: 'zależna', harmonic: 'harmoniczna', sieve: 'sito', sqrt: '√', log: 'log', const: 'stała',
  binary_search: 'binsearch', two_pointers: 'dwa wskaźniki', monotonic_stack: 'stos monotoniczny', adjacency: 'sąsiedzi',
  bfs: 'BFS', pq_graph: 'kopiec', queue: 'kolejka', countdown: 'licznik', until_input: 'do końca wejścia',
  until_stable: 'do stabilizacji', permutations: 'permutacje', walk: 'wskaźniki', unknown: '?', dsu_walk: 'DSU',
  query_range: 'przedział', amortized_walk: 'amortyzacja', tests: 'testy',
};
const EXAMPLE = `#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    vector<long long> a(n);
    for (auto &x : a) cin >> x;
    sort(a.begin(), a.end());
    while (q--) {
        long long x;
        cin >> x;
        int lo = 0, hi = n;
        while (lo < hi) {
            int mid = (lo + hi) / 2;
            if (a[mid] < x) lo = mid + 1;
            else hi = mid;
        }
        cout << lo << '\\n';
    }
}
`;

function fmtOps(x) {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  if (x < 1000) return String(Math.round(x));
  const e = Math.floor(Math.log10(x));
  if (e > 18) return `~10^${e}`;
  const m = x / 10 ** e;
  return `${m.toFixed(1).replace(/\.0$/, '')}·10^${e}`;
}

export default {
  id: 'complexity',
  title: 'Analiza złożoności',
  icon: 'gauge',
  group: 'Kod',
  order: 90,
  layout: 'full',
  description: 'Złożoność czasowa i pamięciowa w najgorszym przypadku: drzewo pętli, koszty STL, rekurencja (twierdzenie '
    + 'o rekurencji uniwersalnej), hot-spoty i werdykt dla limitu czasu. Do tego pomiar empiryczny z dopasowaniem krzywej.',
  keywords: ['złożoność', 'complexity', 'big o', 'tle', 'czy zmieszczę się', 'ile operacji', 'pomiar', 'wykres', 'pamięć'],

  render(root, ctx) {
    let last = null;
    let constraints = local.get('cx:constraints', [{ k: 'n', v: '200 000' }]);
    let statement = local.get('cx:statement', '');

    // ------------------------------------------------------------------ lewa kolumna
    const edHost = h('div', { class: 'cx-editor' });
    const consHost = h('div', { class: 'cx-cons' });
    const tlSel = select([{ value: '0.5', label: '0,5 s' }, { value: '1', label: '1 s' }, { value: '2', label: '2 s' },
      { value: '3', label: '3 s' }, { value: '5', label: '5 s' }, { value: '10', label: '10 s' }],
    { value: local.get('cx:tl', '1'), size: 'sm', onChange: (v) => { local.set('cx:tl', v); rerun(); } });
    const mlInp = input({ value: local.get('cx:ml', '256'), size: 'sm', mono: true, attrs: { style: 'width:78px' },
      onChange: (v) => { local.set('cx:ml', v); rerun(); } });
    const stmtBadge = h('span');
    const analyzeBtn = button('Analizuj', { icon: 'gauge', variant: 'primary', onClick: () => analyze(), title: 'Ctrl+Enter' });

    const left = h('div', { class: 'cx-col' },
      edHost,
      h('div', { class: 'cx-limits panel' },
        h('div', { class: 'row between' },
          h('div', { class: 'section-title', style: 'margin:0' }, 'Ograniczenia z treści'),
          h('div', { class: 'row gap-s' },
            button('Treść zadania', { icon: 'file-text', size: 'sm', variant: 'ghost', onClick: () => statementDialog() }), stmtBadge,
            button('Dodaj', { icon: 'plus', size: 'sm', variant: 'ghost', onClick: () => { constraints.push({ k: '', v: '' }); saveCons(); renderCons(true); } }))),
        consHost,
        h('div', { class: 'row gap-s cx-limit-row' },
          h('span', { class: 'muted small' }, 'Limit czasu'), tlSel,
          h('span', { class: 'muted small' }, 'pamięć'), mlInp, h('span', { class: 'muted small' }, 'MB'),
          h('div', { class: 'grow' }), analyzeBtn)));

    // ------------------------------------------------------------------ prawa kolumna
    const staticHost = h('div', { class: 'cx-static' });
    const empHost = h('div', { class: 'cx-emp' });
    const tb = tabs([
      { id: 'static', label: 'Analiza statyczna', icon: 'scan-search', render: () => staticHost },
      { id: 'emp', label: 'Pomiar empiryczny', icon: 'activity', render: () => empHost },
    ], { active: local.get('cx:tab', 'static'), onChange: (id) => local.set('cx:tab', id) });
    const right = h('div', { class: 'cx-col cx-right' }, tb.el);

    root.append(viewHeader(this,
      button('Przykład', { icon: 'sparkles', variant: 'ghost', onClick: () => { ed.setValue(EXAMPLE); analyze(); } }),
      button('Objaśnij kod', { icon: 'graduation-cap', variant: 'ghost', onClick: () => ctx.navigate('explain', { code: ed.getValue() }) }),
      button('Optymalizuj', { icon: 'zap', variant: 'ghost', onClick: () => ctx.navigate('optimizer', { code: ed.getValue() }) })),
    h('div', { class: 'cx-body' }, split(left, right, { ratio: 0.47, min: 340, storageKey: 'complexity' })));

    const ed = createEditor(edHost, {
      value: '', lang: 'cpp', height: '100%', storageKey: 'complexity', toolbar: true, title: 'Kod do analizy',
      placeholder: 'Wklej rozwiązanie w C++ — policzę złożoność i sprawdzę, czy zmieści się w limicie…',
      onRun: () => analyze(),
      onChange: debounce(() => { if (last) staticHost.classList.add('stale'); }, 200),
    });
    this._ed = ed;
    renderCons();
    renderStatic();
    renderEmp();
    updateStmtBadge();
    if (ed.getValue().trim()) setTimeout(() => analyze({ quiet: true }), 120);

    // ------------------------------------------------------------------ ograniczenia
    function saveCons() { local.set('cx:constraints', constraints); }
    function renderCons(focusLast = false) {
      clear(consHost);
      constraints.forEach((c, i) => {
        const k = input({ value: c.k, placeholder: 'n', size: 'sm', mono: true, attrs: { style: 'width:70px' },
          onInput: (v) => { c.k = v.trim(); saveCons(); }, onEnter: () => analyze() });
        const v = input({ value: c.v, placeholder: '200 000 albo 2e5', size: 'sm', mono: true,
          onInput: (val) => { c.v = val; saveCons(); }, onEnter: () => analyze() });
        consHost.append(h('div', { class: 'cx-cons-row' }, k, h('span', { class: 'muted' }, '≤'), v,
          iconButton('x', { title: 'Usuń', onClick: () => { constraints.splice(i, 1); saveCons(); renderCons(); } })));
        if (focusLast && i === constraints.length - 1) setTimeout(() => k.focus(), 10);
      });
      if (!constraints.length) consHost.append(h('div', { class: 'muted small' }, 'Brak — dodaj np. n ≤ 200 000 albo wklej treść zadania.'));
    }
    function consObject() {
      const o = {};
      for (const c of constraints) if (c.k && String(c.v).trim()) o[c.k] = String(c.v).trim();
      return o;
    }
    function updateStmtBadge() {
      clear(stmtBadge);
      if (statement.trim()) stmtBadge.append(badge('treść ✓', 'success'));
    }
    function statementDialog() {
      const ta = textarea({ value: statement, rows: 12, mono: false, placeholder: 'Wklej treść zadania (z sekcją „Wejście” i ograniczeniami)…' });
      const drop = fileDrop({ accept: '.txt,.pdf,.html', label: 'albo upuść plik z treścią (PDF / TXT)', onFiles: async (f) => {
        try {
          if (f.name.toLowerCase().endsWith('.pdf')) {
            const m = await import('../pdf.js');
            ta.value = await m.extractPdfText(f);
          } else ta.value = await readFileText(f);
        } catch (e) { toast('Nie udało się odczytać pliku: ' + e.message, 'error'); }
      } });
      const preview = h('div', { class: 'small muted' });
      const md = modal({
        title: 'Treść zadania', icon: 'file-text', width: 720,
        body: h('div', { class: 'stack' }, ta, drop, preview),
        actions: [
          button('Wyczyść', { variant: 'ghost', onClick: () => { ta.value = ''; } }),
          button('Wyciągnij ograniczenia', { icon: 'filter', onClick: async () => {
            const r = await api.post('/api/complexity/parse_constraints', { text: ta.value });
            const entries = Object.entries(r.constraints || {});
            preview.textContent = entries.length ? 'Znalezione: ' + entries.map(([k, v]) => `${k} ≤ ${fmtOps(v)}`).join(', ')
              + (r.time_limit ? ` · limit czasu ${r.time_limit} s` : '') + (r.memory_limit ? ` · pamięć ${r.memory_limit} MB` : '')
              : 'Nie znalazłem ograniczeń w tym tekście.';
            const plain = entries.filter(([k]) => !k.startsWith('max('));
            if (plain.length) {
              constraints = plain.map(([k, v]) => ({ k, v: String(v) }));
              saveCons(); renderCons();
            }
            if (r.time_limit) { tlSel.value = String(r.time_limit); local.set('cx:tl', String(r.time_limit)); }
            if (r.memory_limit) { mlInp.value = String(r.memory_limit); local.set('cx:ml', String(r.memory_limit)); }
          } }),
          button('Zapisz', { variant: 'primary', onClick: () => { statement = ta.value; local.set('cx:statement', statement); updateStmtBadge(); md.close(); rerun(); } }),
        ],
      });
    }

    // ------------------------------------------------------------------ analiza
    const rerun = debounce(() => { if (last) analyze({ quiet: true }); }, 250);
    async function analyze({ quiet = false } = {}) {
      const code = ed.getValue();
      if (!code.trim()) { if (!quiet) toast('Wklej kod do analizy.', 'info'); return; }
      staticHost.classList.add('busy');
      try {
        const r = await api.post('/api/complexity/analyze', {
          code, constraints: consObject(), statement: statement || undefined,
          time_limit: parseFloat(tlSel.value) || 1, memory_limit: parseFloat(mlInp.value) || 256,
        }, { quiet });
        last = r;
        ed.markLines((r.line_marks || []).map((m) => ({ line: m.line, type: m.type, message: m.message })));
        renderStatic();
        tb.select('static');
      } catch (e) { /* toast pokazany przez api */ } finally { staticHost.classList.remove('busy', 'stale'); }
    }

    function renderStatic() {
      clear(staticHost);
      if (!last) {
        staticHost.append(emptyState({ icon: 'gauge', title: 'Wklej kod i kliknij „Analizuj”',
          text: 'Policzę złożoność w najgorszym przypadku, pokażę, które linie wykonują najwięcej pracy, i ocenię, czy program zmieści się w limicie czasu i pamięci.',
          action: button('Wczytaj przykład', { icon: 'sparkles', onClick: () => { ed.setValue(EXAMPLE); analyze(); } }) }));
        return;
      }
      const r = last;
      const t = r.time;
      const v = VERDICT[t.verdict] || VERDICT.unknown;
      // --- karta werdyktu
      const memTone = r.memory.verdict === 'mle' ? 'danger' : r.memory.verdict === 'warn' ? 'warn' : 'success';
      staticHost.append(h('div', { class: `cx-verdict tone-bg-${v.tone}` },
        h('div', { class: 'cx-v-main' },
          h('div', { class: `cx-v-icon tone-${v.tone}` }, icon(v.icon, 26)),
          h('div', { class: 'grow' },
            h('div', { class: 'cx-v-o mono' }, r.complexity),
            h('div', { class: 'cx-v-label' }, t.label),
            h('div', { class: 'cx-v-text muted' }, t.explanation))),
        h('div', { class: 'grid-4 cx-stats' },
          stat({ label: 'Szac. operacji', value: fmtOps(t.ops), sub: `budżet ${fmtOps(t.budget)} (${t.limit_s} s)`, tone: v.tone, icon: 'cpu' }),
          stat({ label: 'Względem limitu', value: t.ratio !== null && t.ratio !== undefined ? `${t.ratio < 0.01 ? '<1' : Math.round(t.ratio * 100)}%` : '—', sub: v.label, tone: v.tone, icon: 'timer' }),
          stat({ label: 'Pamięć', value: `${r.memory.total_mb} MB`, sub: `limit ${r.memory.limit_mb} MB`, tone: memTone, icon: 'database' }),
          stat({ label: 'Pewność analizy', value: `${Math.round((r.confidence || 0) * 100)}%`, sub: r.unknown_parts ? 'są nierozpoznane fragmenty' : 'wzorce rozpoznane', icon: 'scan-search' }))));
      if (r.terms && r.terms.length > 1) {
        staticHost.append(h('div', { class: 'cx-terms' }, h('span', { class: 'muted small' }, 'Składniki: '),
          r.terms.map((x) => badge(`O(${x.expr}) ≈ ${x.ops_fmt}`, 'neutral'))));
      }
      if (r.assumptions && r.assumptions.length) {
        staticHost.append(callout('**Założenia** (brak w ograniczeniach): ' + r.assumptions.join('; ') + '. Dodaj brakujące ograniczenia po lewej, żeby wynik był dokładny.', 'info'));
      }
      if (r.lang === 'python') staticHost.append(callout('Analiza statyczna jest dostosowana do C++ — dla Pythona użyj pomiaru empirycznego.', 'warn'));
      for (const w of (r.warnings || []).slice(0, 6)) {
        staticHost.append(h('div', { class: 'cx-warn', onclick: () => w.line && ed.scrollToLine(w.line) },
          icon('triangle-alert', 15), h('span', { class: 'grow' }, w.message), w.line ? h('span', { class: 'cx-line' }, `linia ${w.line}`) : null));
      }
      // --- skąd ta złożoność
      if (r.derivation && r.derivation.length) staticHost.append(derivation(r));
      if (r.algorithms && r.algorithms.length) {
        staticHost.append(h('div', { class: 'row gap-s cx-algos' }, h('span', { class: 'muted small' }, 'Rozpoznane techniki:'),
          r.algorithms.map((a) => h('span', { class: 'chip', title: (a.evidence || []).join('\n') }, a.name, h('span', { class: 'faint' }, ` ${Math.round(a.confidence * 100)}%`)))));
      }
      // --- hot-spoty
      if (r.hotspots && r.hotspots.length) {
        staticHost.append(h('div', { class: 'section-title mt-m' }, 'Hot-spoty — gdzie program spędza czas'),
          table([
            { key: 'line', label: 'Linia', align: 'right', width: '58px' },
            { key: 'code', label: 'Kod', render: (x) => h('code', { class: 'cx-code' }, x.code) },
            { key: 'expr', label: 'Ile razy', render: (x) => h('span', { class: 'mono small' }, `O(${x.expr})`) },
            { key: 'ops', label: 'Operacji', align: 'right', render: (x) => x.ops_fmt },
            { key: 'share', label: 'Udział', render: (x) => h('div', { class: 'cx-bar', title: `${Math.round(x.share * 100)}%` }, h('i', { style: { width: `${Math.max(2, x.share * 100)}%` } })) },
          ], r.hotspots, { compact: true, onRowClick: (x) => { ed.highlightLine(x.line); ed.scrollToLine(x.line, false); } }));
      }
      // --- drzewo pętli
      staticHost.append(h('div', { class: 'section-title mt-m' }, 'Drzewo pętli i funkcji'), loopTree(r.loops || []));
      // --- pamięć
      if (r.memory.items && r.memory.items.length) {
        const rows = r.memory.items.slice(0, 12);
        staticHost.append(h('div', { class: 'section-title mt-m' }, 'Pamięć'),
          table([
            { key: 'name', label: 'Zmienna', render: (x) => h('code', null, x.name) },
            { key: 'type', label: 'Typ', render: (x) => h('span', { class: 'mono small' }, x.type) },
            { key: 'elements', label: 'Elementów', render: (x) => h('span', { class: 'mono small' }, x.elements) },
            { key: 'mb', label: 'MB', align: 'right', render: (x) => x.mb.toFixed(2) },
            { key: 'note', label: '', render: (x) => h('span', { class: 'muted small' }, x.note || '') },
          ], rows, { compact: true, onRowClick: (x) => x.line && ed.scrollToLine(x.line) }));
        if (r.memory.stack) {
          const s = r.memory.stack;
          staticHost.append(h('div', { class: 'muted small mt-s' }, `Stos rekurencji ${s.func}(): głębokość do ~${s.depth_fmt} → ok. ${s.mb} MB`
            + (s.depth > 1e5 ? ' — przy głębokiej rekurencji sprawdź limit stosu (na OI zwykle = limit pamięci).' : '')));
        }
      }
      // --- tabela limitów
      staticHost.append(h('div', { class: 'section-title mt-m' }, `Ile n zmieści się w ${t.limit_s} s?`),
        h('div', { class: 'cx-limits-tbl' }, (r.limits_table || []).map((row) =>
          h('div', { class: ['cx-lim', row.yours ? 'yours' : ''].join(' ') },
            h('div', { class: 'mono' }, row.class), h('div', { class: 'cx-lim-n mono' }, `n ≤ ${row.max_n_fmt}`),
            row.yours ? badge('Twój program', 'accent') : null))));
      // --- symbole
      if (r.symbols && r.symbols.length) {
        staticHost.append(h('details', { class: 'mt-m' }, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Zmienne w wyrażeniu i przyjęte wartości'),
          table([
            { key: 'name', label: 'Symbol', render: (x) => h('code', null, x.name) },
            { key: 'value_fmt', label: 'Wartość' },
            { key: 'source', label: 'Skąd', render: (x) => h('span', { class: 'muted small' }, (x.from_constraints ? 'z ograniczeń · ' : '') + (x.source || x.kind)) },
          ], r.symbols, { compact: true })));
      }
    }

    function goLine(n) { if (n) { ed.highlightLine(n); ed.scrollToLine(n, false); } }
    function lineChip(n) { return h('button', { class: 'cx-dline', type: 'button', onclick: () => goLine(n), title: 'Pokaż w edytorze' }, `linia ${n}`); }
    function stepRow(st) {
      return h('div', { class: 'cx-dstep' }, icon(st.amortized ? 'footprints' : 'rotate-ccw', 13), lineChip(st.line),
        h('code', { class: 'cx-dcode' }, st.code),
        h('span', { class: 'cx-dcount mono' }, `${st.count} ×`),
        badge(PATTERN_LABEL[st.pattern] || st.pattern, st.pattern === 'unknown' ? 'warn' : (st.amortized ? 'success' : 'neutral')),
        st.note && !['linear', 'const', 'countdown'].includes(st.pattern) ? h('div', { class: 'cx-dnote' }, st.note) : null);
    }
    function derivation(r) {
      const box = h('div', { class: 'cx-deriv' },
        h('div', { class: 'cx-deriv-h' }, icon('list-tree', 16), h('span', null, 'Skąd ta złożoność?'),
          h('span', { class: 'muted small' }, 'najcięższe linie i pętle, które je mnożą')));
      const explained = new Set();
      for (const it of r.derivation) {
        const item = h('div', { class: 'cx-ditem' },
          h('div', { class: 'cx-dhead' }, lineChip(it.line), h('code', { class: 'cx-dcode grow' }, it.code),
            h('span', { class: 'mono small' }, `O(${it.expr})${it.func && it.func !== 'main' ? ' / wywołanie' : ''}`),
            h('div', { class: 'cx-bar', title: `${Math.round(it.share * 100)}% pracy` }, h('i', { style: { width: `${Math.max(2, it.share * 100)}%` } }))));
        for (const v of it.via || []) {
          if (v.recursion) {
            item.append(h('div', { class: 'cx-dstep' }, icon('square-function', 13), lineChip(v.line), h('code', { class: 'cx-dcode' }, v.code),
              h('div', { class: 'cx-dnote' }, v.recursion)));
            continue;
          }
          item.append(h('div', { class: 'cx-dstep' }, icon('square-function', 13), h('span', { class: 'small' }, `${it.func}() wołana w`), lineChip(v.line),
            h('span', { class: 'mono small muted' }, (v.loops || []).map((x) => x.count).join(' × ') || 'poza pętlą')));
        }
        for (const st of it.loops || []) {
          if (explained.has(st.line)) { item.append(h('div', { class: 'cx-dstep dim' }, icon('rotate-ccw', 13), lineChip(st.line), h('span', { class: 'mono small muted' }, `${st.count} × (jak wyżej)`))); continue; }
          explained.add(st.line);
          item.append(stepRow(st));
        }
        box.append(item);
      }
      box.append(h('div', { class: 'cx-dsum small' }, 'Razem dominuje ', h('b', { class: 'mono' }, r.complexity),
        ' — mniejsze składniki znikają w notacji O.'));
      return box;
    }

    function loopTree(nodes) {
      const wrap = h('div', { class: 'cx-tree' });
      const renderNode = (nd, depth) => {
        const isFn = nd.kind === 'function';
        const head = h('div', { class: ['cx-node', isFn ? 'fn' : '', nd.amortized ? 'amort' : ''].join(' '), style: { '--d': depth },
          onclick: () => { ed.highlightLine(nd.line); ed.scrollToLine(nd.line, false); } },
        icon(isFn ? 'square-function' : (nd.kind === 'while' || nd.kind === 'do' ? 'refresh-cw' : 'rotate-ccw'), 14),
        h('code', { class: 'cx-node-h grow' }, nd.header),
        isFn ? (nd.per_call ? badge(`O(${nd.per_call})${nd.one_time ? ' · łącznie O(' + nd.one_time + ')' : ''}`, 'accent') : null)
          : h('span', { class: 'row gap-s nowrap' },
            badge(PATTERN_LABEL[nd.pattern] || nd.pattern, nd.pattern === 'unknown' ? 'warn' : (nd.amortized ? 'success' : 'neutral')),
            h('span', { class: 'mono small', title: 'Liczba obrotów na jedno wejście do pętli' }, nd.count),
            h('span', { class: 'faint small' }, '→'),
            h('span', { class: 'mono small bold', title: 'Łączna liczba wykonań ciała' }, nd.total)),
        h('span', { class: 'cx-line' }, `${nd.line}`));
        const box = h('div', { class: 'cx-node-box' }, head);
        const note = isFn ? (nd.recursion ? nd.recursion.text : '') : nd.note;
        if (note) box.append(h('div', { class: 'cx-node-note', style: { '--d': depth } }, note));
        for (const ch of nd.children || []) box.append(renderNode(ch, depth + 1));
        return box;
      };
      const meaningful = nodes.filter((n) => n.kind !== 'function' || (n.children && n.children.length) || n.per_call);
      if (!meaningful.length) wrap.append(h('div', { class: 'muted small' }, 'Brak pętli — program wykonuje stałą liczbę kroków.'));
      for (const n of meaningful) wrap.append(renderNode(n, 0));
      return wrap;
    }

    // ------------------------------------------------------------------ pomiar empiryczny
    let job = null;
    function renderEmp() {
      clear(empHost);
      const tplTa = textarea({ value: local.get('cx:tpl', ''), rows: 4, placeholder: '{n}\n{arr:n:1:1000000000}', onInput: debounce((v) => local.set('cx:tpl', v), 300) });
      const genHost = h('div', { class: 'cx-gen' });
      let genEd = null;
      const mode = segmented([{ value: 'template', label: 'Szablon wejścia', icon: 'file-input' }, { value: 'generator', label: 'Generator', icon: 'shuffle' }],
        { value: local.get('cx:empmode', 'template'), onChange: (v) => { local.set('cx:empmode', v); showMode(v); } });
      const sizesInp = input({ value: local.get('cx:sizes', ''), placeholder: 'automatycznie: 1000, 2000, 4000, … aż do limitu', mono: true, size: 'sm',
        onInput: (v) => local.set('cx:sizes', v) });
      const scale = segmented([{ value: 'lin', label: 'liniowa' }, { value: 'log', label: 'log-log' }], { value: local.get('cx:scale', 'log'), onChange: (v) => { local.set('cx:scale', v); if (lastMeasure) drawChart(lastMeasure); } });
      const prog = progressBar({ label: '' });
      prog.el.hidden = true;
      const out = h('div', { class: 'stack' });
      const runBtn = button('Zmierz', { icon: 'play', variant: 'primary', onClick: () => measure() });
      const stopBtn = button('Zatrzymaj', { icon: 'circle-stop', variant: 'danger', onClick: () => job && job.cancel() });
      stopBtn.hidden = true;
      const tplBox = h('div', { class: 'stack gap-s' }, tplTa,
        h('div', { class: 'row gap-s' },
          button('Zgadnij z kodu', { icon: 'wand-sparkles', size: 'sm', variant: 'ghost', onClick: async () => {
            const r = await api.post('/api/complexity/template', { code: ed.getValue() });
            tplTa.value = r.template; local.set('cx:tpl', r.template);
          } }),
          h('span', { class: 'muted small' }, 'Konstrukcje: {n} {rand:1:1e9} {arr:n:1:1e9} {perm:n} {str:n:az} {pairs:m:1:n} {tree:n} {graph:n:m} {lines:q:[rand:1:n] [rand:1:n]}')));
      function showMode(m) {
        tplBox.hidden = m !== 'template';
        genHost.hidden = m !== 'generator';
        if (m === 'generator' && !genEd) {
          genEd = createEditor(genHost, { value: local.get('cx:gen', '#include <bits/stdc++.h>\nusing namespace std;\nint main(int argc, char** argv) {\n    int n = atoi(argv[1]);\n    mt19937 rng(atoi(argv[2]));\n    cout << n << "\\n";\n    for (int i = 0; i < n; i++) cout << rng() % 1000000000 + 1 << " ";\n    cout << "\\n";\n}\n'),
            lang: 'cpp', height: '220px', toolbar: true, title: 'Generator (argv[1] = rozmiar, argv[2] = ziarno)', onChange: (v) => local.set('cx:gen', v) });
        }
      }
      empHost.append(
        h('div', { class: 'stack gap-s' },
          h('div', { class: 'row between' }, mode, h('div', { class: 'row gap-s' }, runBtn, stopBtn)),
          tplBox, genHost,
          field('Rozmiary (opcjonalnie)', sizesInp, 'Puste = podwajanie od 1000, dopóki czas nie zbliży się do limitu.'),
          prog.el, out));
      showMode(mode.value);
      if (!tplTa.value && ed.getValue().trim()) {
        api.post('/api/complexity/template', { code: ed.getValue() }, { quiet: true }).then((r) => { if (!tplTa.value) { tplTa.value = r.template; } }).catch(() => {});
      }

      let lastMeasure = null;
      const chartHost = h('div', { class: 'cx-chart' });
      function drawChart(res) {
        clear(chartHost);
        const log = scale.value === 'log';
        chartHost.append(lineChart(res.series.map((s, i) => ({ ...s, dashed: i > 0 })), { xLabel: 'n', yLabel: 'czas [s]', logX: log, logY: log, height: 280,
          yFormat: (y) => (y < 1 ? `${Math.round(y * 1000)}ms` : `${y.toFixed(1)}s`) }));
      }
      async function measure() {
        const code = ed.getValue();
        if (!code.trim()) { toast('Najpierw wklej kod.', 'info'); return; }
        const body = { code, time_limit: parseFloat(tlSel.value) || 1, sizes: sizesInp.value.trim() || undefined };
        if (mode.value === 'generator') body.generator = genEd ? genEd.getValue() : '';
        else body.input_template = tplTa.value;
        clear(out);
        const pts = [];
        const live = h('div', { class: 'cx-live' });
        out.append(live);
        prog.el.hidden = false;
        prog.set(0, 'Start…');
        runBtn.hidden = true; stopBtn.hidden = false;
        job = api.job('/api/complexity/measure', body, {
          onProgress: (p, msg) => prog.set(p, msg),
          onEvent: (ev) => {
            if (ev.type !== 'point') return;
            pts.push(ev);
            live.append(h('span', { class: 'chip' }, `n=${ev.n.toLocaleString('pl-PL')}: `, ev.verdict === 'OK' ? fmtTime(ev.time) : badge(ev.verdict, 'danger')));
          },
        });
        try {
          const res = await job.promise;
          clear(out);
          if (!res.ok) {
            out.append(callout(res.error || 'Pomiar nie powiódł się.', 'danger'));
            if (res.compile && res.compile.stderr) out.append(h('pre', { class: 'out err' }, res.compile.stderr.slice(0, 3000)));
            return;
          }
          lastMeasure = res;
          const fit = res.fit || {};
          const best = fit.best_fit;
          out.append(h('div', { class: 'grid-3' },
            stat({ label: 'Dopasowana złożoność', value: fit.best ? `O(${fit.best})` : '—', tone: 'accent', icon: 'activity',
              sub: best ? `błąd względny ${(best.rel_err * 100).toFixed(1)}% · R² ${best.r2.toFixed(3)}` : 'za mało punktów' }),
            stat({ label: 'Nachylenie log-log', value: fit.slope ? fit.slope.toFixed(2) : '—', sub: 'czas ~ n^k (k ≈ wykładnik)', icon: 'gauge' }),
            stat({ label: `Max n w ${res.time_limit} s`, value: res.max_n_fmt || '—', sub: 'z ekstrapolacji dopasowania', icon: 'target' })));
          if (static_mismatch(last, fit.best)) {
            out.append(callout(`Analiza statyczna mówi **${last.complexity}**, pomiar wygląda na **O(${fit.best})**. Na małych danych dominują stałe (wczytywanie, start programu) — zwiększ rozmiary albo sprawdź, czy szablon wejścia generuje przypadek pesymistyczny.`, 'info'));
          }
          out.append(h('div', { class: 'row between mt-s' }, h('div', { class: 'section-title', style: 'margin:0' }, 'Wykres'), scale), chartHost);
          drawChart(res);
          if (res.stopped) out.append(h('div', { class: 'muted small' }, 'Zatrzymano: ' + res.stopped));
          if (res.input_source === 'inferred' && res.template) {
            out.append(h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Użyty (zgadnięty) szablon wejścia'), h('pre', { class: 'out' }, res.template)));
          }
          out.append(table([
            { key: 'n', label: 'n', align: 'right', render: (x) => x.n.toLocaleString('pl-PL') },
            { key: 'verdict', label: 'Werdykt', render: (x) => badge(x.verdict, x.verdict === 'OK' ? 'success' : 'danger') },
            { key: 'time', label: 'Czas', align: 'right', render: (x) => fmtTime(x.time) },
            { key: 'mem_kb', label: 'Pamięć', align: 'right', render: (x) => fmtMem(x.mem_kb) },
            { key: 'input_bytes', label: 'Wejście', align: 'right', render: (x) => `${(x.input_bytes / 1024).toFixed(0)} KB` },
          ], res.events || [], { compact: true }));
          if (fit.models && fit.models.length) {
            out.append(h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Porównanie modeli'),
              table([{ key: 'model', label: 'Model', render: (x) => `O(${x.model})` },
                { key: 'rel_err', label: 'Błąd wzgl.', align: 'right', render: (x) => `${(x.rel_err * 100).toFixed(1)}%` },
                { key: 'r2', label: 'R²', align: 'right', render: (x) => x.r2.toFixed(4) }], fit.models, { compact: true })));
          }
        } catch (e) {
          if (!e.cancelled) out.append(callout(e.message || 'Błąd pomiaru', 'danger'));
          else out.append(h('div', { class: 'muted' }, 'Pomiar anulowany.'));
        } finally {
          prog.el.hidden = true;
          runBtn.hidden = false; stopBtn.hidden = true;
          job = null;
        }
      }
    }

    function static_mismatch(stat_, best) {
      if (!stat_ || !best) return false;
      const norm = (s) => String(s || '').replace(/[O() ]/g, '').replace(/²/g, '2');
      const dc = stat_.dominant_class;
      return dc && norm(dc) !== norm(best);
    }

    this._load = (params) => {
      if (params.constraints && typeof params.constraints === 'object') {
        constraints = Object.entries(params.constraints).map(([k, v]) => ({ k, v: String(typeof v === 'object' ? v.max ?? '' : v) }));
        saveCons(); renderCons();
      }
      if (params.statement) { statement = params.statement; local.set('cx:statement', statement); updateStmtBadge(); }
      if (params.code !== undefined) { ed.setValue(params.code); analyze({ quiet: true }); }
    };
  },

  onShow(params) {
    if (params && this._load) this._load(params);
    setTimeout(() => this._ed && this._ed.refresh(), 30);
  },
};

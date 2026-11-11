// (h) Objaśniacz kodu — po polsku, jak nauczyciel: wejście, struktury, algorytm, funkcje, role zmiennych, adnotacje linii.
import {
  h, icon, button, viewHeader, split, badge, markdown, toast, clear, emptyState, segmented, input, debounce,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const SECTION_ICON = {
  input: 'file-input', structures: 'layers', algorithm: 'lightbulb', functions: 'square-function', variables: 'table',
  output: 'send', complexity: 'gauge',
};

const EXAMPLE = `#include <bits/stdc++.h>
using namespace std;

int p[200005], sz[200005];

int znajdz(int x) {
    if (p[x] == x) return x;
    return p[x] = znajdz(p[x]);
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, m;
    cin >> n >> m;
    for (int i = 1; i <= n; i++) { p[i] = i; sz[i] = 1; }
    int skladowe = n;
    for (int i = 0; i < m; i++) {
        int a, b;
        cin >> a >> b;
        a = znajdz(a);
        b = znajdz(b);
        if (a != b) {
            if (sz[a] < sz[b]) swap(a, b);
            p[b] = a;
            sz[a] += sz[b];
            skladowe--;
        }
        cout << skladowe << '\\n';
    }
}
`;

export default {
  id: 'explain',
  title: 'Objaśniacz kodu',
  icon: 'graduation-cap',
  group: 'Kod',
  order: 50,
  layout: 'full',
  description: 'Wklej program, a wyjaśnię go po polsku krok po kroku: co wczytuje, jakich struktur używa, jaki to algorytm '
    + 'i dlaczego działa, co robią funkcje i zmienne. Kliknij notatkę, żeby zobaczyć linię w kodzie.',
  keywords: ['wyjaśnij', 'objaśnij', 'co robi ten kod', 'jak działa', 'zrozum', 'explain', 'komentarz', 'nauka'],

  render(root, ctx) {
    let res = null;
    let noteEls = new Map();
    let filterText = '';

    const edHost = h('div', { class: 'ex-editor' });
    const explainBtn = button('Objaśnij', { icon: 'graduation-cap', variant: 'primary', onClick: () => run(), title: 'Ctrl+Enter' });
    const left = h('div', { class: 'ex-col' }, edHost,
      h('div', { class: 'ex-actions' },
        button('Przykład', { icon: 'sparkles', size: 'sm', variant: 'ghost', onClick: () => { ed.setValue(EXAMPLE); run(); } }),
        h('div', { class: 'grow' }),
        button('Złożoność', { icon: 'gauge', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('complexity', { code: ed.getValue() }) }),
        button('Optymalizuj', { icon: 'zap', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('optimizer', { code: ed.getValue() }) }),
        explainBtn));

    const out = h('div', { class: 'ex-out' });
    const notesHost = h('div', { class: 'ex-notes' });
    const mode = segmented([{ value: 'explain', label: 'Wyjaśnienie', icon: 'book-open' }, { value: 'lines', label: 'Linia po linii', icon: 'list-tree' }],
      { value: local.get('ex:mode', 'explain'), onChange: (v) => { local.set('ex:mode', v); show(); } });
    const search = input({ placeholder: 'Szukaj w notatkach…', size: 'sm', onInput: debounce((v) => { filterText = v.toLowerCase(); renderNotes(); }, 150) });
    const right = h('div', { class: 'ex-col ex-right' },
      h('div', { class: 'row between ex-toolbar' }, mode, search), out, notesHost);

    root.append(viewHeader(this), h('div', { class: 'ex-body' }, split(left, right, { ratio: 0.5, min: 340, storageKey: 'explain' })));

    const ed = createEditor(edHost, {
      value: '', lang: 'cpp', height: '100%', storageKey: 'explain', toolbar: true, title: 'Kod',
      placeholder: 'Wklej program w C++, który chcesz zrozumieć…', onRun: () => run(),
      onChange: () => { if (res) out.classList.add('stale'); },
    });
    this._ed = ed;
    ed.cm.on('cursorActivity', () => {
      const ln = ed.cm.getCursor().line + 1;
      for (const [l, el] of noteEls) el.classList.toggle('active', l === ln);
      const el = noteEls.get(ln);
      if (el && mode.value === 'lines') el.scrollIntoView({ block: 'nearest' });
    });
    show();
    if (ed.getValue().trim()) setTimeout(() => run({ quiet: true }), 120);

    async function run({ quiet = false } = {}) {
      const code = ed.getValue();
      if (!code.trim()) { if (!quiet) toast('Wklej kod do objaśnienia.', 'info'); return; }
      out.classList.add('busy');
      try {
        res = await api.post('/api/explain', { code }, { quiet });
        ed.markLines((res.line_notes || []).map((n) => ({ line: n.line, type: 'hint', message: n.note })));
        show();
      } catch { /* toast z api */ } finally { out.classList.remove('busy', 'stale'); }
    }

    function show() {
      clear(out);
      clear(notesHost);
      search.hidden = mode.value !== 'lines';
      if (!res) {
        out.append(emptyState({ icon: 'graduation-cap', title: 'Wklej kod i kliknij „Objaśnij”',
          text: 'Wyjaśnię, co program wczytuje, jaki algorytm realizuje, dlaczego to działa i do czego służą poszczególne zmienne oraz funkcje.',
          action: button('Wczytaj przykład (Find & Union)', { icon: 'sparkles', onClick: () => { ed.setValue(EXAMPLE); run(); } }) }));
        return;
      }
      if (mode.value === 'lines') { renderNotes(); return; }
      out.append(h('div', { class: 'ex-overview' }, h('div', { class: 'ex-ov-icon' }, icon('lightbulb', 20)), h('div', { class: 'grow' }, markdown(res.overview_md))));
      // „co program liczy” — klikalne skróty do linii, w których dzieje się główne obliczenie
      const computes = ((res.summary || {}).computes || []).filter((c) => c.line);
      if (computes.length) {
        out.append(h('div', { class: 'ex-algos' }, computes.slice(0, 4).map((c) =>
          h('button', { class: 'ex-algo', type: 'button', title: 'Pokaż w kodzie', onclick: () => goto(c.line) },
            icon('target', 13),
            h('span', null, String(c.what || '').replace(/\*\*/g, '').replace(/`/g, '').slice(0, 70)),
            h('span', { class: 'ex-conf' }, `linia ${c.line}`)))));
      }
      if (res.algorithms && res.algorithms.length) {
        out.append(h('div', { class: 'ex-algos' }, res.algorithms.slice(0, 6).map((a) =>
          h('button', { class: 'ex-algo', type: 'button', title: (a.evidence || []).join('\n') + '\n\nKliknij, aby zapytać asystenta',
            onclick: () => ctx.navigate('assistant', { q: a.name }) },
          h('span', null, a.name + (a.variant ? ` (${a.variant})` : '')),
          h('span', { class: 'ex-conf', style: { '--c': a.confidence } }, `${Math.round(a.confidence * 100)}%`)))));
      }
      for (const s of res.sections || []) {
        const body = markdown(s.md);
        linkLines(body);
        out.append(h('details', { class: 'ex-sec', open: s.id !== 'variables' || (res.variables || []).length <= 8 },
          h('summary', null, icon(SECTION_ICON[s.id] || 'info', 16), h('span', { class: 'grow' }, s.title)),
          h('div', { class: 'ex-sec-body' }, body)));
      }
      out.append(h('div', { class: 'ex-hint muted small' }, icon('info', 13),
        ' Notatki do poszczególnych linii widać po najechaniu na kropki obok numerów linii albo w trybie „Linia po linii”.'));
    }

    // zamienia „linia 12” / „linie 4–9” w tekście na klikalne odnośniki
    function linkLines(root_) {
      const walker = document.createTreeWalker(root_, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const tn of nodes) {
        const txt = tn.nodeValue;
        if (!/lini[aei]\s+\d+/.test(txt) || tn.parentElement?.closest('code, pre, a')) continue;
        const frag = document.createDocumentFragment();
        let last = 0;
        txt.replace(/(lini[aei]\s+)(\d+)/g, (m, pre, num, off) => {
          frag.append(txt.slice(last, off), pre);
          frag.append(h('a', { class: 'ex-ln', href: '#', onclick: (e) => { e.preventDefault(); goto(+num); } }, num));
          last = off + m.length;
          return m;
        });
        frag.append(txt.slice(last));
        tn.replaceWith(frag);
      }
    }

    function goto(line) {
      ed.highlightLine(line);
      ed.scrollToLine(line, false);
      for (const [l, el] of noteEls) el.classList.toggle('active', l === line);
    }

    function renderNotes() {
      clear(notesHost);
      noteEls = new Map();
      if (!res) return;
      const lines = ed.getValue().split('\n');
      const notes = (res.line_notes || []).filter((n) => !filterText || n.note.toLowerCase().includes(filterText)
        || (lines[n.line - 1] || '').toLowerCase().includes(filterText));
      if (!notes.length) { notesHost.append(h('div', { class: 'muted small pad' }, 'Brak notatek pasujących do wyszukiwania.')); return; }
      for (const n of notes) {
        const el = h('div', { class: 'ex-note', onclick: () => goto(n.line) },
          h('span', { class: 'ex-note-ln mono' }, String(n.line)),
          h('div', { class: 'grow' },
            h('code', { class: 'ex-note-code' }, (lines[n.line - 1] || '').trim().slice(0, 120)),
            h('div', { class: 'ex-note-text' }, markdown(n.note))));
        noteEls.set(n.line, el);
        notesHost.append(el);
      }
    }

    this._load = (params) => {
      if (params.code !== undefined) { ed.setValue(params.code); run({ quiet: true }); }
    };
  },

  onShow(params) {
    if (params && this._load) this._load(params);
    setTimeout(() => this._ed && this._ed.refresh(), 30);
  },
};

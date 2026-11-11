// (N) C++ Playground — fragment bez main → automatyczny szkielet programu, REPL (`? wyrażenie`), dbg(...), cout << kontener.
import {
  h, icon, button, viewHeader, split, textarea, outputPanel, modal, tabs, codeBlock, markdown, callout, badge, kbd,
  input, toast, clear, emptyState, debounce, copyButton,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const STARTER = `// Pisz od razu instrukcje — bez main i bez #include.
vector<int> v = {5, 2, 8, 1, 9};
sort(v.begin(), v.end());
cout << v << '\\n';            // kontenery wypisują się jednym cout
dbg(v.size(), v.back());      // nazwy i wartości → stderr
? accumulate(v.begin(), v.end(), 0)
v.front() * v.back()          // ostatnie wyrażenie bez ; jest wypisywane
`;

const QUICK = ['lower-bound', 'pq-min', 'int128', 'structured-bindings', 'bitset', 'repl'];

export default {
  id: 'playground',
  title: 'C++ Playground',
  icon: 'square-terminal',
  group: 'Kod',
  order: 1,
  layout: 'full',
  description: 'Sprawdź fragment kodu bez pisania main: szkielet programu tworzy się sam, ostatnie wyrażenie i linie `?` są wypisywane, działa dbg(...) i cout << vector.',
  keywords: ['playground', 'repl', 'fragment', 'szybki test', 'bez main', 'dbg', 'eksperyment', 'uruchom fragment'],

  render(root, ctx) {
    const self = this;
    let examples = [];
    let last = null;          // ostatnia odpowiedź /api/playground/run
    let running = false;

    const inputEl = textarea({ value: local.get('playground:input', ''), rows: 4, placeholder: 'Wejście programu (stdin) — opcjonalnie…',
      onInput: debounce((v) => local.set('playground:input', v), 300) });
    const out = outputPanel();
    const notesHost = h('div', { class: 'pg-notes' });
    const quickHost = h('div', { class: 'pg-quick row gap-s' });
    const runBtn = button('Uruchom', { icon: 'play', variant: 'primary', onClick: () => run(), title: 'Ctrl+Enter' });
    const showBtn = button('Pokaż wygenerowany program', { icon: 'file-code', variant: 'ghost', onClick: () => showProgram() });

    const edHost = h('div', { class: 'pg-editor' });
    const left = h('div', { class: 'pg-col' }, quickHost, edHost);
    const right = h('div', { class: 'pg-col pg-right' },
      h('div', { class: 'row between' }, h('div', { class: 'section-title', style: 'margin:0' }, 'Wejście'),
        h('div', { class: 'row gap-s' }, h('span', { class: 'small muted' }, kbd('Ctrl'), '+', kbd('Enter')), runBtn)),
      inputEl,
      h('div', { class: 'section-title' }, 'Wynik'),
      out.el,
      notesHost,
      cheatsheet());

    root.append(
      viewHeader(this,
        button('Przykłady', { icon: 'library', onClick: () => gallery() }),
        showBtn),
      h('div', { class: 'pg-body' }, split(left, right, { ratio: 0.56, min: 300, storageKey: 'playground' })));

    const ed = createEditor(edHost, {
      value: STARTER, lang: 'cpp', height: '100%', storageKey: 'playground', toolbar: true, title: 'Fragment kodu (bez main)',
      placeholder: 'Wpisz instrukcje C++…', onRun: () => run(),
      actions: [button('Wyczyść', { icon: 'trash-2', size: 'sm', variant: 'ghost', onClick: () => { ed.setValue(''); ed.clearMarks(); out.clear(); clear(notesHost); ed.focus(); } })],
    });
    this._ed = ed;
    this._input = inputEl;
    this._run = run;
    out.el.addEventListener('gotoline', (e) => ed.scrollToLine(e.detail));
    loadExamples();

    // ------------------------------------------------------------------ uruchamianie
    async function run() {
      if (running) return;
      const code = ed.getValue();
      if (!code.trim()) { toast('Wpisz fragment kodu.', 'info'); return; }
      running = true;
      local.set('playground:input', inputEl.value);
      ed.clearMarks();
      out.setBusy(true, 'Buduję program i uruchamiam…');
      try {
        const res = await api.post('/api/playground/run', { code, input: inputEl.value }, { quiet: true });
        last = res;
        renderNotes(res);
        if (!res.compile || !res.compile.ok) {
          await out.showCompile(res.compile || { stderr: 'Nie udało się skompilować.' }, code);
          markErrors(res.compile?.stderr || '');
        } else {
          out.showRun(res.run, { compile: res.compile });
        }
      } catch (e) {
        out.showText(e.message, 'danger');
      } finally {
        running = false;
      }
    }

    function markErrors(stderr) {
      const marks = [];
      const seen = new Set();
      const rx = /^main\.cpp:(\d+):(?:(\d+):)?\s*(fatal error|error|warning):\s*(.*)$/gm;
      let m;
      while ((m = rx.exec(stderr))) {
        const line = parseInt(m[1], 10);
        const key = line + m[3];
        if (seen.has(key)) continue;
        seen.add(key);
        marks.push({ line, type: m[3] === 'warning' ? 'warn' : 'error', message: m[4] });
      }
      if (marks.length) { ed.markLines(marks); const firstErr = marks.find((x) => x.type === 'error'); if (firstErr) ed.scrollToLine(firstErr.line, false); }
    }

    function renderNotes(res) {
      clear(notesHost);
      if (!res || !res.notes || !res.notes.length) return;
      const items = res.notes.map((n) => h('li', null, markdown(n)));
      const head = h('summary', null, icon('wand-sparkles', 14), h('span', null, 'Co zrobił Playground'),
        res.has_main ? badge('pełny program', 'info') : badge(`${res.stats?.statements ?? 0} instr. w main`, 'accent'));
      const det = h('details', { class: 'pg-notes-box', open: local.get('playground:notesOpen', false) }, head, h('ul', null, items));
      det.addEventListener('toggle', () => local.set('playground:notesOpen', det.open));
      notesHost.append(det);
    }

    // ------------------------------------------------------------------ wygenerowany program
    async function showProgram() {
      const code = ed.getValue();
      if (!code.trim()) { toast('Najpierw wpisz fragment kodu.', 'info'); return; }
      let res;
      try { res = await api.post('/api/playground/wrap', { code }); } catch { return; }
      const pretty = res.pretty || res.program;
      const t = tabs([
        { id: 'pretty', label: 'Czytelny', icon: 'eye', render: () => h('div', { class: 'stack gap-s' },
          h('div', { class: 'small muted' }, 'Sformatowany, bez dyrektyw #line; nagłówek Playgroundu (wypisywanie kontenerów, dbg) zwinięty do komentarza.'),
          codeBlock(pretty, 'cpp', { maxHeight: '58vh', title: 'program.cpp' })) },
        { id: 'exact', label: 'Dokładny (kompilowany)', icon: 'code-xml', render: () => h('div', { class: 'stack gap-s' },
          h('div', { class: 'small muted' }, 'Dokładnie to trafia do kompilatora. Dyrektywy #line sprawiają, że błędy wskazują linie Twojego fragmentu.'),
          codeBlock(res.program, 'cpp', { maxHeight: '58vh', title: 'program.cpp (z nagłówkiem)', lineNumbers: false })) },
      ], { active: 'pretty' });
      const m = modal({
        title: 'Wygenerowany program', icon: 'file-code', width: 900,
        body: h('div', { class: 'stack' }, res.notes?.length ? h('ul', { class: 'pg-modal-notes small' }, res.notes.map((n) => h('li', null, markdown(n)))) : null, t.el),
        actions: [
          copyButton(() => res.program, { label: 'Kopiuj pełny program', size: 'md' }),
          button('Upiększacz', { icon: 'wand-sparkles', onClick: () => { m.close(); ctx.navigate('beautifier', { code: pretty }); } }),
          button('Sprawdzarka', { icon: 'bug', onClick: () => { m.close(); ctx.navigate('checker', { code: res.program }); } }),
          button('Zamknij', { variant: 'primary', onClick: () => m.close() }),
        ],
      });
    }

    // ------------------------------------------------------------------ przykłady
    async function loadExamples() {
      try {
        examples = (await api.get('/api/playground/examples', null, { quiet: true })).examples || [];
      } catch { examples = []; }
      clear(quickHost);
      if (!examples.length) return;
      quickHost.append(h('span', { class: 'small muted' }, icon('lightbulb', 13), ' Wypróbuj:'));
      for (const id of QUICK) {
        const ex = examples.find((e) => e.id === id);
        if (ex) quickHost.append(h('button', { class: 'chip', type: 'button', title: ex.description, onclick: () => loadExample(ex, true) }, ex.title.split(' — ')[0].split(' (')[0]));
      }
      quickHost.append(h('button', { class: 'chip', type: 'button', onclick: () => gallery() }, icon('library', 13), `Wszystkie (${examples.length})`));
    }

    function loadExample(ex, autorun) {
      ed.setValue(ex.code);
      inputEl.value = ex.input || '';
      local.set('playground:input', inputEl.value);
      ed.clearMarks();
      clear(notesHost);
      out.clear();
      if (autorun) run();
      else ed.focus();
    }

    function gallery() {
      if (!examples.length) { toast('Brak przykładów (data/playground_examples.json).', 'warn'); return; }
      let cat = 'Wszystkie';
      let q = '';
      const cats = ['Wszystkie', ...new Set(examples.map((e) => e.category || 'Inne'))];
      const chips = h('div', { class: 'row gap-s' });
      const grid = h('div', { class: 'pg-gallery' });
      const search = input({ placeholder: 'Szukaj: lower_bound, bitset, mapa…', onInput: (v) => { q = v.toLowerCase(); draw(); } });
      const m = modal({ title: 'Galeria przykładów', icon: 'library', width: 980, body: h('div', { class: 'stack' }, search, chips, grid) });
      function drawChips() {
        clear(chips);
        for (const c of cats) chips.append(h('button', { class: ['chip', c === cat ? 'active' : ''].join(' '), type: 'button', onclick: () => { cat = c; drawChips(); draw(); } }, c));
      }
      function draw() {
        clear(grid);
        const list = examples.filter((e) => (cat === 'Wszystkie' || (e.category || 'Inne') === cat) &&
          (!q || [e.title, e.description, ...(e.tags || [])].join(' ').toLowerCase().includes(q)));
        if (!list.length) { grid.append(emptyState({ icon: 'search', title: 'Nic nie znaleziono', text: 'Spróbuj innego słowa.' })); return; }
        for (const e of list) {
          grid.append(h('button', { class: 'pg-ex', type: 'button', onclick: () => { m.close(); loadExample(e, true); } },
            h('div', { class: 'row between nowrap' }, h('div', { class: 'pg-ex-title' }, e.title), badge(e.category || 'Inne', 'neutral')),
            h('div', { class: 'small muted pg-ex-desc' }, e.description),
            h('pre', { class: 'pg-ex-code' }, e.code.split('\n').slice(0, 4).join('\n')),
            e.input ? h('div', { class: 'tiny faint' }, icon('file-input', 12), ' z danymi wejściowymi') : null));
        }
      }
      drawChips(); draw();
      setTimeout(() => search.focus(), 40);
    }

    function cheatsheet() {
      const rows = [
        ['vector<int> v = {3, 1, 2};', 'instrukcje trafiają do main(), definicje (funkcje, struct, using) przed main'],
        ['? v.size()', 'linia zaczynająca się od ? — wypisz wartość wyrażenia'],
        ['v', 'ostatnie wyrażenie bez średnika jest wypisywane (REPL)'],
        ['cout << v << m;', 'vector, set, map, pair, tuple, deque, queue, priority_queue, __int128'],
        ['dbg(a, v, x * 2);', '[linia 7] a = 1, v = [3, 1, 2], x * 2 = 10 — na stderr'],
      ];
      const body = h('div', { class: 'pg-cheat' }, rows.map(([c, d]) => h('div', { class: 'pg-cheat-row' }, h('code', null, c), h('span', { class: 'small muted' }, d))));
      const det = h('details', { class: 'pg-notes-box', open: local.get('playground:cheatOpen', true) },
        h('summary', null, icon('lightbulb', 14), h('span', null, 'Ściąga Playgroundu')), body,
        h('div', { class: 'small muted', style: 'padding: 0 12px 10px' }, 'Masz już pełny program z main? Też zadziała — zostanie uruchomiony bez zmian.'));
      det.addEventListener('toggle', () => local.set('playground:cheatOpen', det.open));
      return det;
    }
  },

  onShow(params) {
    if (!params || !this._ed) return;
    if (params.code !== undefined) {
      this._ed.setValue(String(params.code));
      this._ed.clearMarks();
    }
    if (params.input !== undefined && this._input) this._input.value = String(params.input);
    if (params.run && this._run) this._run();
  },
};

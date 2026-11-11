// Tłumacz Python → C++ („wysyłam w Pythonie, zmienia na C++”). Silnik: analiza AST + inferencja typów (bez AI).
import {
  h, icon, button, viewHeader, split, tabs, select, checkbox, segmented, badge, verdictBadge, toast, callout,
  emptyState, debounce, clear, copyButton, textarea, stat, spinner, markdown, outputPanel, fmtTime, plural,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const DEFAULT_CODE = `n = int(input())
a = list(map(int, input().split()))
a.sort()
print(sum(a), a[0], a[-1])
print(*a)
`;
const DEFAULT_INPUT = '5\n3 -1 4 1 5\n';
const SEV = {
  error: { icon: 'circle-alert', tone: 'danger', label: 'Błąd', mark: 'error' },
  warn: { icon: 'triangle-alert', tone: 'warn', label: 'Uwaga', mark: 'warn' },
  info: { icon: 'info', tone: 'info', label: 'Info', mark: 'info' },
};

export default {
  id: 'py2cpp',
  title: 'Tłumacz Python → C++',
  icon: 'languages',
  group: 'Kod',
  order: 9,
  layout: 'full',
  description: 'Piszesz w Pythonie, wysyłasz w C++: inferencja typów, // i % jak w Pythonie, heapq → priority_queue, '
    + 'bisect → lower_bound, f-stringi, mapa linii i sprawdzenie równoważności z pomiarem przyspieszenia.',
  keywords: ['python', 'c++', 'tłumacz', 'transpiler', 'konwersja', 'przepisz', 'py2cpp', 'heapq', 'typy'],

  render(root, ctx) {
    const opts = local.get('py2cpp:opts', { int_type: 'long long', use_unordered: false, fast_io: true, live: true });
    const st = { seq: 0, res: null, pyToCpp: new Map(), cppToPy: new Map(), hl: { py: [], cpp: [] }, hoverPy: 0,
      hoverCpp: 0, verifying: null, examples: [] };
    this._st = st;

    // ------------------------------------------------------------------ pasek opcji
    const status = h('div', { class: 'p2c-status small muted' });
    const intSeg = segmented([{ value: 'long long', label: 'long long' }, { value: 'int', label: 'int' }],
      { value: opts.int_type, onChange: (v) => { opts.int_type = v; saveOpts(); translate(); } });
    intSeg.title = 'Typ C++ dla liczb całkowitych Pythona (long long jest bezpieczniejszy — int może się przepełnić)';
    const unordCb = checkbox('unordered_map / unordered_set', {
      checked: opts.use_unordered, title: 'Szybsze słowniki/zbiory (haszowanie), ale bez kolejności kluczy',
      onChange: (v) => { opts.use_unordered = v; saveOpts(); translate(); },
    });
    const fastCb = checkbox('Szybkie I/O', {
      checked: opts.fast_io, title: 'ios::sync_with_stdio(false); cin.tie(nullptr);',
      onChange: (v) => { opts.fast_io = v; saveOpts(); translate(); },
    });
    const liveCb = checkbox('Na żywo', {
      checked: opts.live, title: 'Tłumacz automatycznie podczas pisania',
      onChange: (v) => { opts.live = v; saveOpts(); if (v) translate(); },
    });
    const exampleSel = select([{ value: '', label: 'Przykładowe programy…' }], {
      size: 'sm', onChange: (v) => { if (v) loadExample(v); exampleSel.value = ''; },
    });
    exampleSel.classList.add('p2c-examples');
    const toolbar = h('div', { class: 'p2c-toolbar' },
      h('div', { class: 'p2c-opt' }, h('span', { class: 'p2c-opt-label' }, 'Liczby całkowite'), intSeg),
      unordCb, fastCb, h('div', { class: 'p2c-sep' }), liveCb, h('div', { class: 'grow' }), status);

    // ------------------------------------------------------------------ edytory
    const pyHost = h('div', { class: 'p2c-pane' });
    const cppHost = h('div', { class: 'p2c-pane' });
    const cppEmpty = h('div', { class: 'p2c-empty' }, emptyState({ icon: 'languages', title: 'Tu pojawi się kod C++',
      text: 'Wklej program w Pythonie po lewej — tłumaczenie robi się samo (albo Ctrl+Enter).' }));
    const editors = split(pyHost, h('div', { class: 'p2c-pane p2c-right' }, cppHost, cppEmpty),
      { ratio: 0.48, min: 300, storageKey: 'py2cpp' });
    editors.classList.add('p2c-split');

    // ------------------------------------------------------------------ panel dolny: ostrzeżenia + równoważność
    const warnHost = h('div', { class: 'p2c-warns' });
    const inputTa = textarea({ value: local.get('py2cpp:input', DEFAULT_INPUT), rows: 6, placeholder: 'Wejście programu…',
      onInput: (v) => local.set('py2cpp:input', v) });
    inputTa.classList.add('p2c-input');
    const verifyRes = h('div', { class: 'p2c-verify-res' });
    const verifyBtn = button('Sprawdź równoważność', { icon: 'git-compare', variant: 'primary', onClick: () => verify() });
    const verifyPane = h('div', { class: 'p2c-verify' },
      h('div', { class: 'p2c-verify-in' },
        h('div', { class: 'row between' }, h('div', { class: 'section-title' }, 'Wejście'),
          h('span', { class: 'small muted', title: 'Kilka testów naraz' }, 'kilka testów: oddziel linią ', h('code', null, '==='))),
        inputTa,
        h('div', { class: 'row gap-s' }, verifyBtn),
        h('div', { class: 'small muted' }, 'Uruchamia Pythona i przetłumaczony C++ na tym samym wejściu, porównuje wyjścia i mierzy czas.')),
      verifyRes);
    const bottomTabs = tabs([
      { id: 'warn', label: 'Ostrzeżenia', icon: 'triangle-alert', render: () => warnHost },
      { id: 'verify', label: 'Równoważność', icon: 'git-compare', render: () => verifyPane },
    ], { active: local.get('py2cpp:tab', 'warn'), onChange: (id) => local.set('py2cpp:tab', id) });
    bottomTabs.el.classList.add('p2c-bottom-tabs');
    const layout = split(editors, bottomTabs.el, { ratio: 0.64, min: 160, direction: 'v', storageKey: 'py2cpp-v' });
    layout.classList.add('p2c-layout');

    const copyCpp = copyButton(() => st.res?.cpp || '', { label: 'Kopiuj C++', size: 'md' });
    copyCpp.classList.remove('ghost');
    root.append(
      viewHeader(this, exampleSel,
        button('Tłumacz', { icon: 'languages', variant: 'primary', onClick: () => translate(true), title: 'Ctrl+Enter w edytorze' }),
        copyCpp,
        button('Otwórz w Sprawdzarce', { icon: 'bug', onClick: () => openIn('checker'), title: 'Sprawdź kod C++ statyczną analizą i sanitizerami' }),
        button('Playground', { icon: 'square-terminal', variant: 'ghost', onClick: () => openIn('playground'), title: 'Uruchom kod C++ w Playground' })),
      toolbar, layout);

    const pyEd = createEditor(pyHost, {
      value: DEFAULT_CODE, lang: 'python', height: '100%', storageKey: 'py2cpp-py', toolbar: true, title: 'Python',
      placeholder: 'Wklej program w Pythonie…', onChange: () => { if (opts.live) schedule(); },
      onRun: () => translate(true),
    });
    const cppEd = createEditor(cppHost, { value: '', lang: 'cpp', height: '100%', readOnly: true, toolbar: true, title: 'C++' });
    this._pyEd = pyEd;
    this._cppEd = cppEd;

    const schedule = debounce(() => translate(), 450);
    this._translate = (force) => translate(force);
    wireHover();
    loadExamples();
    translate();

    // ------------------------------------------------------------------ tłumaczenie
    function saveOpts() { local.set('py2cpp:opts', opts); }

    async function translate(force = false) {
      const code = pyEd.getValue();
      const seq = ++st.seq;
      if (!code.trim()) {
        st.res = null;
        cppEd.setValue('');
        cppEmpty.hidden = false;
        renderWarnings([]);
        status.replaceChildren();
        return;
      }
      status.replaceChildren(spinner(13), h('span', null, 'Tłumaczę…'));
      try {
        const r = await api.post('/api/py2cpp/translate', {
          code, options: { int_type: opts.int_type, use_unordered: opts.use_unordered, fast_io: opts.fast_io },
        }, { quiet: !force });
        if (seq !== st.seq) return;
        st.res = r;
        buildMaps(r.line_map || []);
        cppEmpty.hidden = !!r.cpp;
        cppEd.setValue(r.cpp || '', true);
        renderWarnings(r.warnings || []);
        const nErr = (r.warnings || []).filter((w) => w.severity === 'error').length;
        const lines = (r.cpp || '').split('\n').length;
        status.replaceChildren(
          r.translated === false ? badge('Błąd składni', 'danger', 'circle-alert')
            : nErr ? badge(`${nErr} ${plural(nErr, 'fragment', 'fragmenty', 'fragmentów')} do ręcznej poprawy`, 'warn', 'triangle-alert')
              : badge('Przetłumaczono', 'success', 'circle-check'),
          h('span', null, `${lines} ${plural(lines, 'linia', 'linie', 'linii')} C++`),
          r.time_ms !== undefined ? h('span', null, `${Math.round(r.time_ms)} ms`) : null);
      } catch (e) {
        if (seq !== st.seq) return;
        status.replaceChildren(badge('Błąd', 'danger', 'circle-alert'), h('span', null, e.message));
      }
    }

    function buildMaps(lineMap) {
      st.pyToCpp = new Map();
      st.cppToPy = new Map();
      for (const [c, p] of lineMap) {
        if (!st.pyToCpp.has(p)) st.pyToCpp.set(p, []);
        st.pyToCpp.get(p).push(c);
        st.cppToPy.set(c, p);
      }
      clearHl();
    }

    // ------------------------------------------------------------------ ostrzeżenia
    function renderWarnings(ws) {
      clear(warnHost);
      const marks = [];
      const counts = { error: 0, warn: 0, info: 0 };
      for (const w of ws) counts[w.severity] = (counts[w.severity] || 0) + 1;
      bottomTabs.setBadge('warn', ws.length ? String(ws.length) : '', counts.error ? 'danger' : counts.warn ? 'warn' : 'neutral');
      if (!ws.length) {
        warnHost.append(h('div', { class: 'p2c-allgood' }, icon('circle-check', 16),
          h('span', null, st.res ? 'Brak uwag — tłumaczenie nie wymaga ręcznych poprawek.' : 'Tu pojawią się uwagi do tłumaczenia.')));
        pyEd.clearMarks();
        return;
      }
      const summary = h('div', { class: 'row gap-s p2c-wsum' },
        counts.error ? badge(`${counts.error} ${plural(counts.error, 'błąd', 'błędy', 'błędów')}`, 'danger', 'circle-alert') : null,
        counts.warn ? badge(`${counts.warn} ${plural(counts.warn, 'uwaga', 'uwagi', 'uwag')}`, 'warn', 'triangle-alert') : null,
        counts.info ? badge(`${counts.info} ${plural(counts.info, 'informacja', 'informacje', 'informacji')}`, 'info', 'info') : null,
        h('span', { class: 'small muted' }, 'Kliknij, aby przejść do linii w Pythonie.'));
      warnHost.append(summary);
      const order = { error: 0, warn: 1, info: 2 };
      const sorted = [...ws].sort((a, b) => (order[a.severity] - order[b.severity]) || ((a.line || 0) - (b.line || 0)));
      for (const w of sorted) {
        const s = SEV[w.severity] || SEV.info;
        const item = h('div', { class: `p2c-warn ${w.severity}`, onclick: () => { if (w.line) { pyEd.scrollToLine(w.line); highlightFromPy(w.line); } } },
          h('span', { class: `p2c-wicon tone-${s.tone}` }, icon(s.icon, 15)),
          w.line ? h('span', { class: 'p2c-wline' }, `linia ${w.line}`) : h('span', { class: 'p2c-wline muted' }, 'ogólne'),
          h('div', { class: 'grow p2c-wmsg' }, markdown(w.message)));
        warnHost.append(item);
        if (w.line && w.severity !== 'info') marks.push({ line: w.line, type: s.mark, message: w.message });
      }
      pyEd.markLines(marks);
    }

    // ------------------------------------------------------------------ podświetlanie odpowiadających linii
    function clearHl() {
      for (const [ed, key] of [[pyEd, 'py'], [cppEd, 'cpp']]) {
        for (const lh of st.hl[key]) { try { ed.cm.removeLineClass(lh, 'background', 'p2c-hl'); } catch { /* */ } }
        st.hl[key] = [];
      }
    }
    function addHl(ed, key, line) {
      if (line < 1 || line > ed.cm.lineCount()) return;
      st.hl[key].push(ed.cm.addLineClass(line - 1, 'background', 'p2c-hl'));
    }
    function highlightFromPy(pyLine, scroll = true) {
      clearHl();
      const cppLines = st.pyToCpp.get(pyLine) || [];
      if (!cppLines.length) return;
      addHl(pyEd, 'py', pyLine);
      for (const c of cppLines) addHl(cppEd, 'cpp', c);
      if (scroll) cppEd.cm.scrollIntoView({ line: cppLines[0] - 1, ch: 0 }, 60);
    }
    function highlightFromCpp(cppLine, scroll = true) {
      clearHl();
      const p = st.cppToPy.get(cppLine);
      if (!p) return;
      addHl(pyEd, 'py', p);
      for (const c of st.pyToCpp.get(p) || []) addHl(cppEd, 'cpp', c);
      if (scroll) pyEd.cm.scrollIntoView({ line: p - 1, ch: 0 }, 60);
    }
    function lineAt(cm, e) {
      const pos = cm.coordsChar({ left: e.clientX, top: e.clientY }, 'window');
      return pos && pos.outside !== true ? pos.line + 1 : (pos ? pos.line + 1 : 0);
    }
    function wireHover() {
      const pw = pyEd.cm.getWrapperElement();
      const cw = cppEd.cm.getWrapperElement();
      pw.addEventListener('mousemove', (e) => {
        const l = lineAt(pyEd.cm, e);
        if (l && l !== st.hoverPy) { st.hoverPy = l; st.hoverCpp = 0; highlightFromPy(l); }
      });
      cw.addEventListener('mousemove', (e) => {
        const l = lineAt(cppEd.cm, e);
        if (l && l !== st.hoverCpp) { st.hoverCpp = l; st.hoverPy = 0; highlightFromCpp(l); }
      });
      for (const w of [pw, cw]) w.addEventListener('mouseleave', () => { st.hoverPy = st.hoverCpp = 0; clearHl(); });
      cw.addEventListener('click', () => {
        const c = cppEd.cm.getCursor().line + 1;
        const p = st.cppToPy.get(c);
        if (p) pyEd.scrollToLine(p);
      });
      pyEd.cm.on('cursorActivity', () => { if (!st.hoverPy) highlightFromPy(pyEd.cm.getCursor().line + 1); });
    }

    // ------------------------------------------------------------------ przykłady
    async function loadExamples() {
      try {
        const r = await api.get('/api/py2cpp/examples', null, { quiet: true });
        st.examples = r.examples || [];
        for (const ex of st.examples) exampleSel.append(h('option', { value: ex.id, title: ex.description }, ex.title));
      } catch { /* bez przykładów */ }
    }
    function loadExample(id) {
      const ex = st.examples.find((x) => x.id === id);
      if (!ex) return;
      pyEd.setValue(ex.code);
      if (ex.input) { inputTa.value = ex.input; local.set('py2cpp:input', ex.input); }
      translate(true);
      toast(`Wczytano przykład: ${ex.title}`, 'success', { timeout: 1600 });
    }

    function openIn(view) {
      if (!st.res?.cpp) { toast('Najpierw przetłumacz program.', 'warn'); return; }
      ctx.navigate(view, { code: st.res.cpp });
    }

    // ------------------------------------------------------------------ równoważność
    function splitInputs(text) {
      const parts = text.replace(/\r\n/g, '\n').split(/^===+\s*$/m).map((s) => s.replace(/^\n/, ''));
      const out = parts.filter((p, i) => p.trim() !== '' || (i === 0 && parts.length === 1));
      return out.length ? out : [''];
    }

    async function verify() {
      const code = pyEd.getValue();
      if (!code.trim()) { toast('Wklej program w Pythonie.', 'warn'); return; }
      bottomTabs.select('verify');
      const inputs = splitInputs(inputTa.value);
      const prog = h('div', { class: 'p2c-progress row gap-s' }, spinner(16), h('span', { class: 'muted' }, 'Tłumaczę i kompiluję…'));
      verifyRes.replaceChildren(prog);
      const ctl = api.job('/api/py2cpp/verify_start', {
        code, inputs, options: { int_type: opts.int_type, use_unordered: opts.use_unordered, fast_io: opts.fast_io },
      }, { onProgress: (p, msg) => { prog.lastChild.textContent = msg || 'Pracuję…'; } });
      st.verifying = ctl;
      try {
        const res = await ctl.promise;
        renderVerify(res, inputs);
      } catch (e) {
        if (!e.cancelled) verifyRes.replaceChildren(callout(`Nie udało się sprawdzić: ${e.message}`, 'danger'));
      } finally { st.verifying = null; }
    }

    function renderVerify(res, inputs) {
      clear(verifyRes);
      if (res.translation?.cpp && st.res && res.translation.cpp !== st.res.cpp) {
        // wynik dotyczy aktualnej wersji — zsynchronizuj prawy edytor
        st.res = { ...st.res, cpp: res.translation.cpp, line_map: res.translation.line_map };
        buildMaps(res.translation.line_map || []);
        cppEd.setValue(res.translation.cpp, true);
      }
      if (res.compile && !res.compile.ok) {
        const op = outputPanel({ title: 'Kompilacja C++' });
        verifyRes.append(callout('Przetłumaczony kod **nie kompiluje się** — zobacz uwagi tłumacza (zakładka „Ostrzeżenia”) i błędy poniżej.', 'danger'), op.el);
        op.showCompile(res.compile, res.translation?.cpp);
        return;
      }
      if (res.error) { verifyRes.append(callout(res.error, 'danger')); return; }
      const tests = res.tests || [];
      const allEq = res.equal;
      const sp = res.speedup;
      verifyRes.append(h('div', { class: 'p2c-summary' },
        h('div', { class: ['p2c-verdict', allEq ? 'ok' : 'bad'].join(' ') },
          icon(allEq ? 'circle-check' : 'circle-alert', 22),
          h('div', null, h('div', { class: 'p2c-vt' }, allEq ? 'Wyjścia identyczne' : 'Wyjścia się różnią'),
            h('div', { class: 'small muted' }, `${tests.length} ${plural(tests.length, 'test', 'testy', 'testów')}, porównanie po tokenach (runner.outputs_equal)`))),
        stat({ label: 'Python', value: fmtTime(res.python_time), icon: 'timer' }),
        stat({ label: 'C++', value: fmtTime(res.cpp_time), icon: 'zap' }),
        stat({ label: 'Przyspieszenie', value: sp ? `×${sp >= 100 ? Math.round(sp) : sp}` : '—', tone: sp && sp >= 1 ? 'success' : undefined,
          icon: 'gauge', sub: sp ? 'czas CPU Pythona / C++' : undefined })));
      tests.forEach((t, i) => {
        const eqBadge = t.equal ? badge('zgodne', 'success', 'check') : badge('różne', 'danger', 'x');
        const outs = h('div', { class: 'p2c-outs' },
          outBox('Python', t.python), outBox('C++', t.cpp));
        const diff = t.first_diff ? callout(`Pierwsza różnica w linii **${t.first_diff.line}**: Python \`${short(t.first_diff.python)}\`, C++ \`${short(t.first_diff.cpp)}\`.`, 'warn') : null;
        const floatNote = t.float_close ? callout('Wyjścia różnią się tylko zaokrągleniem liczb rzeczywistych (różnica < 1e-6).', 'info') : null;
        verifyRes.append(h('details', { class: 'p2c-test', open: !t.equal || tests.length === 1 },
          h('summary', null, h('span', { class: 'p2c-tname' }, `Test ${i + 1}`), eqBadge,
            h('span', { class: 'p2c-tmeta' }, 'Python ', verdictBadge(t.python.verdict), ' ', fmtTime(t.python.time)),
            h('span', { class: 'p2c-tmeta' }, 'C++ ', verdictBadge(t.cpp.verdict), ' ', fmtTime(t.cpp.time)),
            t.speedup ? h('span', { class: 'p2c-speed' }, icon('zap', 13), `×${t.speedup}`) : null,
            h('div', { class: 'grow' }), h('span', { class: 'small muted' }, `${(inputs[i] || '').split('\n').length - 1} linii wejścia`)),
          h('div', { class: 'stack gap-s p2c-tbody' }, diff, floatNote, outs)));
      });
    }

    function short(s) { if (s === null || s === undefined) return '(brak linii)'; s = String(s); return s.length > 60 ? s.slice(0, 57) + '…' : s || '(pusta)'; }

    function outBox(label, run) {
      return h('div', { class: 'p2c-out' },
        h('div', { class: 'out-label' }, label, copyButton(() => run.stdout)),
        h('pre', { class: 'out' }, run.stdout || h('span', { class: 'faint' }, '(puste wyjście)')),
        run.stderr && run.stderr.trim() ? h('pre', { class: 'out err' }, run.stderr.slice(-4000)) : null);
    }
  },

  onShow(params) {
    if (!params) return;
    if (params.code && this._pyEd) {
      this._pyEd.setValue(String(params.code));
      this._translate && this._translate(true);
    }
    if (params.input && this._st) {
      const ta = document.querySelector('.view[data-view="py2cpp"] .p2c-input');
      if (ta) ta.value = String(params.input);
    }
    setTimeout(() => { this._pyEd?.refresh(); this._cppEd?.refresh(); }, 30);
  },
};

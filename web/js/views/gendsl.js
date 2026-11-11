// Generator testów (DSL): mini-język do pisania generatorów bez C++ — podgląd na żywo, przykłady, eksport do C++/Pythona.
import {
  h, icon, button, iconButton, viewHeader, split, tabs, badge, stat, toast, modal, codeBlock, copyButton, callout,
  emptyState, input, select, segmented, debounce, clear, spinner, plural, kbd,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

export const DSL_KEYWORDS = ['tests', 'seed', 'print', 'repeat', 'for', 'in', 'if', 'elif', 'else', 'while', 'break',
  'continue', 'assert', 'and', 'or', 'not'];
export const DSL_BUILTINS = ['int', 'float', 'chance', 'choice', 'char', 'string', 'palindrome', 'periodic', 'antihash',
  'array', 'matrix', 'distinct', 'perm', 'seq', 'range', 'sorted', 'reverse', 'shuffle', 'unique', 'len', 'sum', 'min',
  'max', 'count', 'put', 'flat', 'rows', 'lines', 'join', 'str', 'abs', 'floor', 'ceil', 'round', 'sqrt', 'gcd', 'lcm',
  'tree', 'parents', 'forest', 'graph', 'dag', 'bipartite', 'grid', 'pairs', 'interval', 'points', 'queries'];
const PREDEF = new Set(['testno', 'ntests', 'size']);

const DEFAULT_SRC = `# Generator testów — mini-język OI Forge (Ctrl+Spacja = podpowiedzi)
tests 1000          # ile testów (oddzielonych pustą linią — format Testerki)
n = int(1, 10)
a = array(n, int(-5, 5))
print n
print a
`;

/** Rejestruje tryb CodeMirror „oigen” (kolorowanie, wcięcia po „{”, komentarze #). */
export function ensureDslMode() {
  const CM = window.CodeMirror;
  if (!CM || CM.modes.oigen) return;
  const KW = new Set(DSL_KEYWORDS);
  const BI = new Set(DSL_BUILTINS);
  CM.defineMode('oigen', (cfg) => ({
    startState: () => ({ depth: 0 }),
    copyState: (s) => ({ depth: s.depth }),
    token(stream, state) {
      if (stream.eatSpace()) return null;
      const ch = stream.peek();
      if (ch === '#') { stream.skipToEnd(); return 'comment'; }
      if (ch === '"' || ch === "'") {
        stream.next();
        let esc = false;
        while (!stream.eol()) { const c = stream.next(); if (c === ch && !esc) return 'string'; esc = !esc && c === '\\'; }
        return 'string error';
      }
      if (stream.match(/^\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?/)) return 'number';
      if (stream.match('..')) return 'operator';
      const m = stream.match(/^[A-Za-z_]\w*/);
      if (m) {
        const w = m[0];
        if (w === 'true' || w === 'false') return 'atom';
        if (KW.has(w)) return 'keyword';
        if (PREDEF.has(w)) return 'variable-2';
        if ((w === 'sep' || w === 'end') && stream.match(/^\s*=(?!=)/, false)) return 'property';
        if (BI.has(w) && stream.match(/^\s*\(/, false)) return 'builtin';
        if (stream.match(/^\s*=(?!=)/, false)) return 'def';
        return 'variable';
      }
      if (ch === '{') { stream.next(); state.depth++; return 'bracket'; }
      if (ch === '}') { stream.next(); state.depth = Math.max(0, state.depth - 1); return 'bracket'; }
      if (stream.match(/^(\*\*|\/\/=?|==|!=|<=|>=|\+=|-=|\*=|%=|&&|\|\||[-+*/%<>=!])/)) return 'operator';
      stream.next();
      return null;
    },
    indent(state, textAfter) {
      const d = state.depth - (/^\s*\}/.test(textAfter) ? 1 : 0);
      return Math.max(0, d) * (cfg.indentUnit || 4);
    },
    electricChars: '}',
    lineComment: '#',
  }));
}

/** Podpowiedzi Ctrl+Spacja: funkcje (z sygnaturą), słowa kluczowe, zmienne z programu. */
export function dslHints(cm, reference = []) {
  const CM = window.CodeMirror;
  if (!CM.showHint) return;
  const sigs = new Map(reference.map((r) => [r.name, r]));
  CM.showHint(cm, () => {
    const cur = cm.getCursor();
    const line = cm.getLine(cur.line);
    let start = cur.ch;
    while (start > 0 && /\w/.test(line[start - 1])) start--;
    const word = line.slice(start, cur.ch).toLowerCase();
    const vars = new Set();
    cm.getValue().replace(/^\s*([A-Za-z_]\w*)\s*(?:\+|-|\*|\/\/|%)?=(?!=)/gm, (_, v) => { vars.add(v); return ''; });
    cm.getValue().replace(/\bfor\s+([A-Za-z_]\w*)\s+in\b/g, (_, v) => { vars.add(v); return ''; });
    const list = [];
    const add = (text, kind, desc, insert) => {
      if (word && !text.toLowerCase().startsWith(word)) return;
      list.push({
        text: insert || text, displayText: text,
        render: (el) => { el.append(h('b', null, text), h('span', { class: 'h-desc' }, `${kind}${desc ? ' · ' + desc : ''}`)); },
      });
    };
    for (const b of DSL_BUILTINS) { const r = sigs.get(b); add(b, 'funkcja', r ? r.sig : '', b + '('); }
    for (const k of DSL_KEYWORDS) add(k, 'słowo kluczowe', '', k + ' ');
    for (const v of [...vars, ...PREDEF]) add(v, 'zmienna', '');
    return { list: list.slice(0, 80), from: CM.Pos(cur.line, start), to: CM.Pos(cur.line, cur.ch) };
  }, { completeSingle: false });
}

/** Dostosowuje edytor do mini-języka: tryb, Tab = wcięcie (bez snippetów C++), Ctrl+Spacja = podpowiedzi. */
export function setupDslEditor(ed, getReference) {
  ensureDslMode();
  ed.cm.setOption('mode', 'oigen');
  const keys = { ...(ed.cm.getOption('extraKeys') || {}) };
  keys.Tab = (cm) => { if (cm.somethingSelected()) cm.execCommand('indentMore'); else cm.replaceSelection('    '); };
  keys['Ctrl-Space'] = (cm) => dslHints(cm, getReference ? getReference() : []);
  keys['Shift-Ctrl-F'] = () => toast('Mini-język nie ma formatowania — wcięcia ustawiają się same po „{”.', 'info', { timeout: 2000 });
  keys['Shift-Cmd-F'] = keys['Shift-Ctrl-F'];
  ed.cm.setOption('extraKeys', keys);
  ed.cm.setOption('smartIndent', true);
  ed.format = () => toast('Mini-język nie ma formatowania.', 'info', { timeout: 1500 });
}

function bytesLabel(b) {
  if (b === null || b === undefined) return '—';
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1).replace('.', ',')} KB`;
  return `${(b / 1024 / 1024).toFixed(2).replace('.', ',')} MB`;
}

function download(name, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = h('a', { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

let view = null;

export default {
  id: 'gendsl', title: 'Generator testów (DSL)', icon: 'wand-sparkles', group: 'Testowanie', order: 40, layout: 'full',
  description: 'Mini-język do pisania generatorów testów bez C++: tablice, grafy, drzewa, siatki, napisy, zapytania. Podgląd na żywo i eksport do samodzielnego C++/Pythona.',
  keywords: ['generator', 'dsl', 'testy', 'losowe', 'graf', 'drzewo', 'tablica', 'mini-język', 'gen'],

  render(root, ctx) {
    const st = {
      seed: local.get('gendsl:seed', ''), size: local.get('gendsl:size', 10), previewN: local.get('gendsl:previewN', 6),
      reference: [], examples: [], last: null, full: null, exportLang: local.get('gendsl:exportLang', 'cpp'),
      exportCode: { cpp: null, python: null }, exportFor: null, reqNo: 0, errorLine: null,
    };
    view = { st };
    let ed = null;

    // ----------------------------------------------------------------------- nagłówek
    const sendBtn = button('Wyślij do testerki', { icon: 'send', variant: 'primary', onClick: () => sendToTester(), title: 'Eksport do C++ i otwarcie w Testerce jako generator' });
    root.append(viewHeader(this,
      button('Przykłady', { icon: 'library', onClick: () => openExamples() }),
      button('Eksportuj do C++', { icon: 'file-code', variant: 'ghost', onClick: () => showExport('cpp') }),
      button('Python', { icon: 'terminal', variant: 'ghost', onClick: () => showExport('python') }),
      sendBtn));

    // ----------------------------------------------------------------------- lewa strona: edytor
    const edHost = h('div', { class: 'gd-editor-host' });
    const status = h('div', { class: 'gd-status' });
    const left = h('div', { class: 'gd-left' }, edHost, status);

    // ----------------------------------------------------------------------- prawa strona: zakładki
    const seedInp = input({ value: st.seed, placeholder: 'z programu', size: 'sm', mono: true, attrs: { inputmode: 'numeric', title: 'Ziarno losowania (puste = „seed” z programu albo 1)' },
      onInput: (v) => { st.seed = v.trim(); local.set('gendsl:seed', st.seed); refresh(); } });
    seedInp.classList.add('gd-seed');
    const sizeInp = input({ value: String(st.size), size: 'sm', mono: true, attrs: { inputmode: 'numeric', title: 'Wartość zmiennej size (w Porównaniu rozwiązań to kolejne rozmiary)' },
      onInput: (v) => { st.size = parseInt(v, 10) || 10; local.set('gendsl:size', st.size); refresh(); } });
    sizeInp.classList.add('gd-size');
    const prevSel = select([3, 6, 12, 24].map((x) => ({ value: x, label: `${x} ${plural(x, 'test', 'testy', 'testów')}` })),
      { value: st.previewN, size: 'sm', onChange: (v) => { st.previewN = +v; local.set('gendsl:previewN', st.previewN); refresh(); } });
    prevSel.style.width = 'auto';
    const dice = iconButton('shuffle', { title: 'Losowe ziarno', onClick: () => { seedInp.value = String(Math.floor(Math.random() * 1e9)); seedInp.dispatchEvent(new Event('input')); } });
    const statsEl = h('div', { class: 'gd-stats' });
    const warnEl = h('div', { class: 'gd-warn stack gap-s' });
    const fullEl = h('div', { class: 'gd-full' });
    const testsEl = h('div', { class: 'gd-tests' });
    const busyEl = h('span', { class: 'gd-busy', hidden: true }, spinner(14), 'generuję…');

    const previewPane = h('div', { class: 'gd-pane' },
      h('div', { class: 'gd-toolbar' },
        h('label', { class: 'gd-lbl' }, 'Ziarno', seedInp, dice),
        h('label', { class: 'gd-lbl', title: 'Zmienna size' }, h('code', null, 'size'), sizeInp),
        h('label', { class: 'gd-lbl' }, 'Podgląd', prevSel), busyEl,
        h('div', { class: 'grow' }),
        button('Generuj wszystko', { icon: 'play', size: 'sm', onClick: () => runFull(), title: 'Wygeneruj wszystkie testy (Ctrl+Enter)' })),
      statsEl, warnEl, fullEl, testsEl);

    const exportCodeEl = h('div', { class: 'gd-export-code' });
    const langSeg = segmented([{ value: 'cpp', label: 'C++', icon: 'file-code' }, { value: 'python', label: 'Python', icon: 'terminal' }],
      { value: st.exportLang, onChange: (v) => { st.exportLang = v; local.set('gendsl:exportLang', v); renderExport(); } });
    const exportPane = h('div', { class: 'gd-pane' },
      h('div', { class: 'gd-toolbar' }, langSeg, h('div', { class: 'grow' }),
        button('Kopiuj', { icon: 'copy', size: 'sm', onClick: async () => { const c = await getExport(st.exportLang); if (c) { await copyText(c); toast('Skopiowano kod generatora', 'success', { timeout: 1500 }); } } }),
        button('Pobierz', { icon: 'download', size: 'sm', onClick: async () => { const c = await getExport(st.exportLang); if (c) download(st.exportLang === 'cpp' ? 'gen.cpp' : 'gen.py', c); } }),
        button('Do testerki', { icon: 'send', size: 'sm', variant: 'secondary', onClick: () => sendToTester() }),
        button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', variant: 'ghost', title: 'Zapisz generator (C++) jako plik w edytorze', onClick: () => openInEditor() })),
      callout('Samodzielny program: `./gen <seed> [liczba_testów] [size]`. Losowanie jest zgodne z podglądem — **ten sam seed daje identyczne testy**. Testerka podaje seed w argv[1] i liczbę testów w argv[2].', 'info'),
      exportCodeEl);

    const refSearch = input({ placeholder: 'Szukaj funkcji… (np. graf, drzewo, napis)', size: 'sm', onInput: () => renderReference() });
    const refList = h('div', { class: 'gd-ref' });
    const refPane = h('div', { class: 'gd-pane' }, h('div', { class: 'gd-toolbar' }, refSearch), refList);

    const tb = tabs([
      { id: 'preview', label: 'Podgląd', icon: 'eye', render: () => previewPane },
      { id: 'export', label: 'Eksport', icon: 'code-xml', render: () => exportPane },
      { id: 'ref', label: 'Ściąga', icon: 'book-open', render: () => refPane },
    ], { active: local.get('gendsl:tab', 'preview'), onChange: (id) => { local.set('gendsl:tab', id); if (id === 'export' && ed) renderExport(); } });
    tb.el.classList.add('gd-tabs');
    const right = h('div', { class: 'gd-right' }, tb.el);

    root.append(h('div', { class: 'gd-body' }, split(left, right, { ratio: 0.5, min: 320, storageKey: 'gendsl' })));

    ed = createEditor(edHost, {
      value: DEFAULT_SRC, lang: 'text', height: '100%', storageKey: 'gendsl:src', toolbar: true, title: 'Generator (mini-język)',
      placeholder: 'n = int(1, 10)\nprint n', onRun: () => runFull(),
      actions: [iconButton('rotate-ccw', { title: 'Przywróć przykład startowy', onClick: () => { ed.setValue(DEFAULT_SRC); } })],
      onChange: () => { st.exportCode = { cpp: null, python: null }; st.full = null; clear(fullEl); refresh(); },
    });
    setupDslEditor(ed, () => st.reference);
    view.ed = ed;

    // ----------------------------------------------------------------------- podgląd na żywo
    const refresh = debounce(() => runPreview(), 420);
    view.refresh = refresh;

    async function runPreview() {
      const source = ed.getValue();
      const no = ++st.reqNo;
      if (!source.trim()) { showError(null); clear(testsEl); clear(statsEl); testsEl.append(emptyState({ icon: 'wand-sparkles', title: 'Pusty generator', text: 'Napisz program albo wybierz przykład.' })); return; }
      busyEl.hidden = false;
      try {
        const r = await api.post('/api/gendsl/run', { source, seed: st.seed || null, tests: st.previewN, preview: st.previewN, size: st.size, include_output: false, time_limit: 6 }, { quiet: true });
        if (no !== st.reqNo) return;
        st.last = r;
        showError(null);
        renderPreview(r);
      } catch (e) {
        if (no !== st.reqNo) return;
        showError(e);
      } finally {
        if (no === st.reqNo) busyEl.hidden = true;
      }
    }

    function showError(e) {
      ed.clearMarks();
      clear(status);
      st.errorLine = null;
      if (!e) {
        const r = st.last;
        status.className = 'gd-status ok';
        status.append(icon('circle-check', 15), h('span', null, 'Program poprawny'),
          r ? h('span', { class: 'muted' }, ` · ${r.declared_tests} ${plural(r.declared_tests, 'test', 'testy', 'testów')} · seed ${r.seed}`) : null,
          h('span', { class: 'grow' }), h('span', { class: 'muted small' }, kbd('Ctrl'), '+', kbd('Spacja'), ' podpowiedzi · ', kbd('Ctrl'), '+', kbd('Enter'), ' wszystko'));
        return;
      }
      const data = e.data || {};
      const errs = data.errors && data.errors.length ? data.errors : [{ line: data.line, col: data.col, message: e.message, test: data.test }];
      ed.markLines(errs.filter((x) => x.line).map((x) => ({ line: x.line, type: 'error', message: x.message })));
      status.className = 'gd-status err';
      const first = errs[0];
      st.errorLine = first.line;
      status.append(icon('circle-alert', 15),
        h('span', { class: 'grow' }, first.text || e.message),
        first.line ? button(`linia ${first.line}`, { size: 'sm', variant: 'ghost', onClick: () => ed.scrollToLine(first.line) }) : null);
      clear(statsEl);
      clear(warnEl);
      clear(testsEl);
      if (data.preview && data.preview.length) renderTests(data.preview, `Testy przed błędem (${data.preview.length})`);
      else testsEl.append(callout(`**Błąd:** ${(first.text || e.message).replace(/\*/g, '')}\n\nPopraw program — podgląd odświeży się sam.`, 'danger'));
    }

    function renderPreview(r) {
      clear(statsEl);
      const s = r.stats || {};
      statsEl.append(
        stat({ label: 'Testów w programie', value: String(r.declared_tests), icon: 'layers', sub: r.declared_tests > 100000 ? 'bardzo dużo!' : null }),
        stat({ label: 'Średnio na test', value: bytesLabel(Math.round(s.avg_test_bytes || 0)), icon: 'file-text', sub: `największy: ${bytesLabel(s.max_test_bytes)}` }),
        stat({ label: 'Linii (podgląd)', value: String(s.lines ?? '—'), icon: 'list-checks', sub: `maks. ${s.max_test_lines ?? 0} w teście` }),
        stat({ label: 'Czas podglądu', value: `${s.time_ms ?? 0} ms`, icon: 'timer', sub: r.declared_tests > s.tests ? `szac. całość ~${estimateTotal(r)}` : null }));
      clear(warnEl);
      for (const w of r.warnings || []) warnEl.append(callout(w, 'warn'));
      renderTests(r.preview || [], `Pierwsze ${r.preview.length} ${plural(r.preview.length, 'test', 'testy', 'testów')}`);
    }

    function estimateTotal(r) {
      const s = r.stats;
      const per = (s.time_ms || 0) / Math.max(1, s.tests);
      const t = per * r.declared_tests;
      return t < 1000 ? `${Math.max(1, Math.round(t))} ms` : `${(t / 1000).toFixed(1).replace('.', ',')} s`;
    }

    function renderTests(list, title) {
      clear(testsEl);
      if (!list.length) { testsEl.append(emptyState({ icon: 'file-text', title: 'Brak testów', text: 'Program nie wygenerował żadnego testu (tests 0?).' })); return; }
      testsEl.append(h('div', { class: 'section-title' }, title));
      list.forEach((t, i) => {
        const lines = t.replace(/\n$/, '').split('\n');
        const big = t.length > 6000;
        const body = big ? t.slice(0, 6000) + '\n…' : t.replace(/\n$/, '');
        testsEl.append(h('div', { class: 'gd-test' },
          h('div', { class: 'gd-test-head' }, h('b', null, `Test ${i + 1}`),
            h('span', { class: 'muted small' }, `${lines.length} ${plural(lines.length, 'linia', 'linie', 'linii')} · ${bytesLabel(t.length)}`),
            h('div', { class: 'grow' }), copyButton(() => t)),
          h('pre', { class: 'gd-test-body' }, body || h('span', { class: 'faint' }, '(pusty test)'))));
      });
    }

    async function runFull() {
      tb.select('preview');
      clear(fullEl);
      fullEl.append(h('div', { class: 'row gap-s muted small' }, spinner(14), 'Generuję wszystkie testy…'));
      try {
        const r = await api.post('/api/gendsl/run', { source: ed.getValue(), seed: st.seed || null, size: st.size, preview: st.previewN, include_output: true });
        st.full = r;
        clear(fullEl);
        const s = r.stats;
        fullEl.append(h('div', { class: 'gd-full-card' },
          icon('circle-check', 18),
          h('div', { class: 'grow' },
            h('div', { class: 'bold' }, `Wygenerowano ${r.tests_count} ${plural(r.tests_count, 'test', 'testy', 'testów')} — ${bytesLabel(s.bytes)}, ${s.lines} ${plural(s.lines, 'linia', 'linie', 'linii')}`),
            h('div', { class: 'muted small' }, `czas ${s.time_ms} ms · największy test ${bytesLabel(s.max_test_bytes)} · seed ${r.seed}${r.output_truncated ? ' · wyjście obcięte do 8 MB' : ''}`)),
          button('Kopiuj', { icon: 'copy', size: 'sm', onClick: async () => { await copyText(r.output || ''); toast('Skopiowano wszystkie testy', 'success', { timeout: 1500 }); } }),
          button('Pobierz .txt', { icon: 'download', size: 'sm', onClick: () => download('testy.txt', r.output || '') })));
        clear(warnEl);
        for (const w of r.warnings || []) warnEl.append(callout(w, 'warn'));
      } catch (e) {
        clear(fullEl);
        showError(e);
      }
    }

    // ----------------------------------------------------------------------- eksport
    async function getExport(lang) {
      const src = ed.getValue();
      if (st.exportCode[lang] && st.exportFor === src) return st.exportCode[lang];
      try {
        const r = await api.post('/api/gendsl/export', { source: src, lang });
        if (st.exportFor !== src) st.exportCode = { cpp: null, python: null };
        st.exportFor = src;
        st.exportCode[lang] = r.code;
        return r.code;
      } catch (e) { showError(e); return null; }
    }

    async function renderExport() {
      clear(exportCodeEl);
      exportCodeEl.append(h('div', { class: 'row gap-s muted small' }, spinner(14), 'Eksportuję…'));
      const code = await getExport(st.exportLang);
      clear(exportCodeEl);
      if (!code) { exportCodeEl.append(callout('Popraw błędy w programie, żeby wyeksportować generator.', 'danger')); return; }
      const lines = code.split('\n').length;
      exportCodeEl.append(codeBlock(code, st.exportLang, { title: `${st.exportLang === 'cpp' ? 'gen.cpp' : 'gen.py'} · ${lines} linii`, maxHeight: 'calc(100vh - 360px)' }));
    }

    function showExport(lang) {
      st.exportLang = lang;
      langSeg.setValue(lang);
      local.set('gendsl:exportLang', lang);
      tb.select('export');
      renderExport();
    }

    async function openInEditor() {
      const code = await getExport('cpp');
      if (code) ctx.navigate('workspace', { code, name: 'gen.cpp' });
    }

    async function sendToTester() {
      const code = await getExport('cpp');
      if (!code) return;
      ctx.navigate('tester', { generator: code, langs: { generator: 'cpp' } });
      toast('Generator (C++) przekazany do Testerki', 'success', { timeout: 2200 });
    }

    // ----------------------------------------------------------------------- ściąga
    function renderReference() {
      clear(refList);
      const q = refSearch.value.trim().toLowerCase();
      const items = st.reference.filter((r) => !q || `${r.name} ${r.sig} ${r.desc} ${r.cat}`.toLowerCase().includes(q));
      if (!items.length) { refList.append(emptyState({ icon: 'search', title: 'Nic nie znaleziono', text: 'Spróbuj innego słowa (np. „drzewo”, „graf”, „napis”).' })); return; }
      let cat = null;
      for (const r of items) {
        if (r.cat !== cat) { cat = r.cat; refList.append(h('div', { class: 'section-title gd-ref-cat' }, cat)); }
        refList.append(h('div', { class: 'gd-ref-item' },
          h('div', { class: 'row nowrap' }, h('code', { class: 'gd-ref-sig' }, r.sig), h('div', { class: 'grow' }),
            r.example ? iconButton('plus', { title: 'Wstaw przykład do edytora', onClick: () => insertSnippet(r.example) }) : null),
          h('div', { class: 'gd-ref-desc' }, r.desc.replace(/`/g, '')),
          r.example ? h('pre', { class: 'gd-ref-ex' }, r.example) : null));
      }
    }

    function insertSnippet(text) {
      const cm = ed.cm;
      const cur = cm.getCursor();
      const lineText = cm.getLine(cur.line);
      const prefix = lineText.trim() ? '\n' : '';
      cm.replaceRange(prefix + text + '\n', { line: cur.line, ch: lineText.length });
      cm.focus();
      toast('Wstawiono przykład', 'success', { timeout: 1000 });
    }

    // ----------------------------------------------------------------------- przykłady
    function openExamples() {
      const cats = ['Wszystkie', ...new Set(st.examples.map((e) => e.category))];
      let cur = 'Wszystkie';
      const chips = h('div', { class: 'row gap-s gd-chips' });
      const grid = h('div', { class: 'gd-ex-grid' });
      const draw = () => {
        clear(chips);
        for (const c of cats) chips.append(h('button', { class: ['chip', c === cur ? 'active' : ''].join(' '), type: 'button', onclick: () => { cur = c; draw(); } }, c));
        clear(grid);
        for (const ex of st.examples.filter((e) => cur === 'Wszystkie' || e.category === cur)) {
          grid.append(h('div', { class: 'gd-ex-card', tabindex: 0, onclick: () => pick(ex), onkeydown: (e) => { if (e.key === 'Enter') pick(ex); } },
            h('div', { class: 'row nowrap' }, h('b', { class: 'grow' }, ex.title), badge(ex.category, 'accent')),
            h('div', { class: 'muted small' }, ex.description),
            h('pre', { class: 'gd-ex-code' }, ex.source.split('\n').filter((l) => l.trim()).slice(0, 6).join('\n'))));
        }
      };
      const m = modal({ title: 'Przykłady generatorów', icon: 'library', width: 980, body: h('div', { class: 'stack' }, chips, grid) });
      function pick(ex) {
        ed.setValue(ex.source);
        m.close();
        toast(`Wczytano „${ex.title}” (Ctrl+Z cofa)`, 'success', { timeout: 1800 });
        tb.select('preview');
      }
      draw();
    }

    async function copyText(text) {
      try { await navigator.clipboard.writeText(text); } catch {
        const ta = h('textarea', { style: 'position:fixed;opacity:0' }); ta.value = text; document.body.append(ta); ta.select();
        document.execCommand('copy'); ta.remove();
      }
    }

    // ----------------------------------------------------------------------- dane startowe
    api.get('/api/gendsl/reference', null, { quiet: true }).then((r) => { st.reference = r.functions || []; renderReference(); }).catch(() => {
      refList.append(callout('Nie udało się wczytać ściągi z serwera.', 'warn'));
    });
    api.get('/api/gendsl/examples', null, { quiet: true }).then((r) => { st.examples = r.examples || []; }).catch(() => {});
    if (ctx.params && (ctx.params.source || ctx.params.generator)) ed.setValue(ctx.params.source || ctx.params.generator);
    runPreview();
    if (tb.active === 'export') renderExport();
    requestAnimationFrame(() => ed.refresh());
  },

  onShow(params) {
    if (!view || !params) return;
    const src = params.source || params.dsl;
    if (src && view.ed && src !== view.ed.getValue()) { view.ed.setValue(src); }
    requestAnimationFrame(() => view.ed && view.ed.refresh());
  },
};

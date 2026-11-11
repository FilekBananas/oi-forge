// Edytor kodu (CodeMirror 5) z snippetami (Tab), formatowaniem (Ctrl+Shift+F), uruchamianiem (Ctrl+Enter),
// znacznikami błędów i autosave. Patrz docs/CONTRACT.md 4.4.
import { h, icon, iconButton, toast, cmMode, select } from './ui.js';
import { api, local, settings, onSettings } from './api.js';

const CM = () => window.CodeMirror;
let snippetCache = null;
let snippetPromise = null;
const editors = new Set();

export function loadSnippets(force = false) {
  if (force) { snippetCache = null; snippetPromise = null; }
  if (snippetCache) return Promise.resolve(snippetCache);
  if (!snippetPromise) {
    snippetPromise = api.get('/api/snippets', null, { quiet: true })
      .then((r) => { snippetCache = r.snippets || []; return snippetCache; })
      .catch(() => { snippetPromise = null; return []; });
  }
  return snippetPromise;
}
window.addEventListener('oif:snippets-changed', () => loadSnippets(true));

export function allEditors() { return [...editors]; }
export function refreshEditorsIn(root) {
  root?.querySelectorAll?.('.CodeMirror').forEach((c) => c.CodeMirror?.refresh());
}

// ------------------------------------------------------------------------------------- snippety
function parseSnippet(code) {
  // ${1:tekst} / ${1} / $1 → placeholdery
  const phs = [];
  let out = '';
  let i = 0;
  while (i < code.length) {
    const m = code.slice(i).match(/^\$\{(\d+)(?::([^}]*))?\}|^\$(\d+)/);
    if (m) {
      const n = +(m[1] || m[3]);
      const text = m[2] || '';
      phs.push({ n, start: out.length, end: out.length + text.length });
      out += text;
      i += m[0].length;
    } else { out += code[i]; i++; }
  }
  phs.sort((a, b) => (a.n || 999) - (b.n || 999));
  return { text: out, phs };
}

function offsetToPos(base, text, off) {
  const before = text.slice(0, off).split('\n');
  return before.length === 1 ? { line: base.line, ch: base.ch + before[0].length } : { line: base.line + before.length - 1, ch: before[before.length - 1].length };
}

function braceDepthAt(cm, pos) {
  // przybliżona głębokość klamr (pomija komentarze i literały prostym skanem)
  const text = cm.getRange({ line: 0, ch: 0 }, pos);
  let d = 0;
  let inStr = null; let inLine = false; let inBlock = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i], n = text[i + 1];
    if (inLine) { if (c === '\n') inLine = false; continue; }
    if (inBlock) { if (c === '*' && n === '/') { inBlock = false; i++; } continue; }
    if (inStr) { if (c === '\\') { i++; continue; } if (c === inStr) inStr = null; continue; }
    if (c === '/' && n === '/') { inLine = true; i++; continue; }
    if (c === '/' && n === '*') { inBlock = true; i++; continue; }
    if (c === '"' || c === "'") { inStr = c; continue; }
    if (c === '{') d++; else if (c === '}') d = Math.max(0, d - 1);
  }
  return d;
}

function topLevelStartLine(cm, line) {
  // linia, od której zaczyna się definicja najwyższego poziomu zawierająca `line`
  for (let l = line; l >= 0; l--) {
    const depth = braceDepthAt(cm, { line: l, ch: 0 });
    const txt = cm.getLine(l);
    if (depth === 0 && txt.trim() && !txt.trim().startsWith('#') && !txt.trim().startsWith('//')) {
      // cofnij się przez linie szablonu/komentarza tuż nad
      let s = l;
      while (s > 0 && /^\s*(template\s*<|\/\/)/.test(cm.getLine(s - 1))) s--;
      return s;
    }
  }
  return 0;
}

function expandSnippet(ed, cm, sn, from, to) {
  const lineText = cm.getLine(from.line);
  const indent = lineText.match(/^\s*/)[0];
  const lang = ed.lang;
  if (sn.lang && lang !== 'text' && sn.lang !== lang && !(sn.lang === 'cpp' && lang === 'c')) return false;
  const depth = braceDepthAt(cm, from);
  const { text, phs } = parseSnippet(sn.code);
  if ((sn.scope || 'global') === 'global' && depth > 0 && lang === 'cpp') {
    // definicja trafia nad bieżącą funkcję, a w miejscu kursora zostaje przykład użycia
    const insLine = topLevelStartLine(cm, from.line);
    const usage = (sn.usage || '').split('\n')[0] || '';
    cm.operation(() => {
      cm.replaceRange(usage, from, to);
      cm.replaceRange(text.replace(/\s+$/, '') + '\n\n', { line: insLine, ch: 0 });
    });
    toast(`Wstawiono „${sn.name || sn.trigger}” nad funkcją (linia ${insLine + 1}).`, 'success', { timeout: 2200 });
    return true;
  }
  const indented = text.split('\n').map((l, i) => (i === 0 ? l : (l ? indent + l : l))).join('\n');
  // przelicz offsety placeholderów po dodaniu wcięć
  const shift = (off) => {
    const pre = text.slice(0, off);
    const nl = (pre.match(/\n/g) || []).length;
    let extra = 0;
    let idx = 0;
    for (let k = 0; k < nl; k++) {
      idx = text.indexOf('\n', idx) + 1;
      if (text[idx] !== '\n' && idx < text.length) extra += indent.length;
    }
    return off + extra;
  };
  cm.operation(() => {
    cm.replaceRange(indented, from, to);
    ed._clearSnippet();
    const marks = phs.map((p) => {
      const a = offsetToPos(from, indented, shift(p.start));
      const b = offsetToPos(from, indented, shift(p.end));
      return cm.markText(a, b, { className: 'cm-snippet-ph', inclusiveLeft: true, inclusiveRight: true, clearWhenEmpty: false });
    });
    if (marks.length) {
      ed._snip = { marks, idx: 0 };
      const r = marks[0].find();
      if (r) cm.setSelection(r.from, r.to);
    }
  });
  return true;
}

// ------------------------------------------------------------------------------------- createEditor
export function createEditor(parent, opts = {}) {
  const {
    value = '', lang = 'cpp', readOnly = false, height = '420px', placeholder, storageKey, onChange, onRun,
    lineNumbers = true, minimal = false, title, toolbar = !!title, langSelect = false, actions = [], wrap,
  } = opts;
  if (!CM()) throw new Error('CodeMirror nie został załadowany');
  const initial = storageKey ? (local.get('ed:' + storageKey) ?? value) : value;
  const host = h('div', { class: 'ed-host', style: 'flex:1;min-height:0;display:flex;flex-direction:column' });
  const hint = h('div', { class: 'ed-hint', style: 'position:absolute;right:10px;bottom:8px;z-index:5;font-size:11.5px;pointer-events:none;opacity:0;transition:opacity .15s' });
  const wrapEl = h('div', { class: ['editor', height === 'auto' ? 'auto' : ''].join(' ') });
  if (height !== 'auto' && height !== '100%') wrapEl.style.height = height;
  if (height === '100%') { wrapEl.style.height = '100%'; wrapEl.style.flex = '1'; }
  let langSel = null;
  if (toolbar || langSelect || actions.length) {
    const bar = h('div', { class: 'ed-bar' }, title ? h('span', { class: 'ed-title' }, title) : null);
    if (langSelect) {
      langSel = select([{ value: 'cpp', label: 'C++' }, { value: 'python', label: 'Python' }], { value: lang, size: 'sm', onChange: (v) => ed.setLang(v) });
      langSel.style.width = 'auto';
      bar.append(langSel);
    }
    const acts = h('div', { class: 'ed-actions' }, actions,
      !readOnly ? iconButton('align-left', { title: 'Formatuj kod (Ctrl+Shift+F)', onClick: () => ed.format() }) : null,
      iconButton('copy', { title: 'Kopiuj', onClick: async () => { await navigator.clipboard.writeText(ed.getValue()).catch(() => {}); toast('Skopiowano', 'success', { timeout: 1200 }); } }));
    bar.append(acts);
    wrapEl.append(bar);
  }
  wrapEl.append(host, hint);
  parent.append(wrapEl);

  const mod = navigator.platform.toUpperCase().includes('MAC') ? 'Cmd' : 'Ctrl';
  const cm = CM()(host, {
    value: initial, mode: cmMode(lang), theme: 'oiforge', lineNumbers: lineNumbers && !minimal, readOnly,
    matchBrackets: true, autoCloseBrackets: !readOnly, styleActiveLine: !readOnly && !minimal, indentUnit: 4, tabSize: 4,
    indentWithTabs: false, lineWrapping: !!wrap, foldGutter: lineNumbers && !minimal, placeholder,
    gutters: minimal || !lineNumbers ? [] : ['cm-marks', 'CodeMirror-linenumbers', 'CodeMirror-foldgutter'],
    viewportMargin: height === 'auto' ? Infinity : 10, cursorScrollMargin: 40, dragDrop: true, spellcheck: false,
    autocorrect: false, autocapitalize: false, inputStyle: 'contenteditable',
  });
  const ed = {
    cm, el: wrapEl, lang, _snip: null, _marks: [], _hl: {},
    getValue: () => cm.getValue(),
    setValue(v, keepCursor = false) {
      const cur = cm.getCursor(); const info = cm.getScrollInfo();
      cm.setValue(v ?? '');
      if (keepCursor) { cm.setCursor(cur); cm.scrollTo(info.left, info.top); }
    },
    setLang(l) { ed.lang = l; cm.setOption('mode', cmMode(l)); if (langSel && langSel.value !== l) langSel.value = l; opts.onLangChange && opts.onLangChange(l); },
    getLang: () => ed.lang,
    focus: () => cm.focus(),
    refresh: () => cm.refresh(),
    setReadOnly: (r) => cm.setOption('readOnly', r),
    async format() {
      if (ed.lang !== 'cpp') { toast('Formatowanie działa dla C++.', 'info'); return; }
      try {
        const r = await api.post('/api/format', { code: cm.getValue() }, { quiet: true });
        if (r.code !== undefined && r.code !== cm.getValue()) { ed.setValue(r.code, true); toast('Sformatowano kod', 'success', { timeout: 1400 }); }
        else toast('Kod już jest sformatowany', 'info', { timeout: 1400 });
      } catch (e) { toast(e.status === 404 ? 'Moduł formatowania nie jest jeszcze dostępny.' : `Nie udało się sformatować: ${e.message}`, 'warn'); }
    },
    markLines(items) {
      ed.clearMarks();
      for (const it of items || []) {
        const ln = (it.line || 1) - 1;
        if (ln < 0 || ln >= cm.lineCount()) continue;
        const type = { warning: 'warn', perf: 'info', style: 'hint' }[it.type] || it.type || 'error';
        const m = h('div', { class: `cm-mark ${type}`, title: it.message || '' }, h('i'));
        cm.setGutterMarker(ln, 'cm-marks', m);
        const lh = cm.addLineClass(ln, 'background', `cm-line-${type}`);
        ed._marks.push({ ln, lh, cls: `cm-line-${type}` });
      }
    },
    clearMarks() {
      cm.clearGutter('cm-marks');
      for (const m of ed._marks) cm.removeLineClass(m.lh, 'background', m.cls);
      ed._marks = [];
    },
    highlightLine(n, cls = 'cm-line-current', { scroll = true } = {}) {
      if (ed._hl[cls] !== undefined && ed._hl[cls] !== null) { try { cm.removeLineClass(ed._hl[cls], 'background', cls); } catch { /* */ } }
      if (!n) { ed._hl[cls] = null; return; }
      const ln = n - 1;
      if (ln < 0 || ln >= cm.lineCount()) return;
      ed._hl[cls] = cm.addLineClass(ln, 'background', cls);
      if (scroll) cm.scrollIntoView({ line: ln, ch: 0 }, 80);
    },
    scrollToLine(n, flash = true) {
      const ln = Math.max(0, Math.min(cm.lineCount() - 1, (n || 1) - 1));
      cm.scrollIntoView({ line: ln, ch: 0 }, 120);
      cm.setCursor({ line: ln, ch: 0 });
      if (flash) { const lh = cm.addLineClass(ln, 'background', 'cm-line-flash'); setTimeout(() => cm.removeLineClass(lh, 'background', 'cm-line-flash'), 1300); }
      cm.focus();
    },
    /** Własny margines (np. licznik wykonań w debuggerze). Zwraca nazwę klasy marginesu. */
    addGutter(name, { before = 'CodeMirror-linenumbers' } = {}) {
      const g = [...(cm.getOption('gutters') || [])];
      if (!g.includes(name)) {
        const i = g.indexOf(before);
        g.splice(i >= 0 ? i : g.length, 0, name);
        cm.setOption('gutters', g);
      }
      return name;
    },
    setGutterMarker(line, gutter, el) { const ln = line - 1; if (ln >= 0 && ln < cm.lineCount()) cm.setGutterMarker(ln, gutter, el || null); },
    clearGutter(gutter) { cm.clearGutter(gutter); },
    onGutterClick(fn) { cm.on('gutterClick', (_c, ln, gutter, ev) => fn(ln + 1, gutter, ev)); },
    _clearSnippet() { if (ed._snip) { ed._snip.marks.forEach((m) => m.clear()); ed._snip = null; } },
    destroy() { editors.delete(ed); wrapEl.remove(); },
  };
  editors.add(ed);
  wrapEl.editor = ed;

  // Tab: snippet / następny placeholder / wcięcie
  // następne pole wstawki (Tab, a w „vector<int|>” także „>”); poza wstawką — zwykłe zachowanie
  const nextField = (cmi) => {
    const s = ed._snip;
    if (!s) return false;
    const rs = s.marks.map((mk) => mk.find()).filter(Boolean);
    const cl = cmi.getCursor().line;
    if (!rs.length || cl < Math.min(...rs.map((r) => r.from.line)) || cl > Math.max(...rs.map((r) => r.to.line))) { ed._clearSnippet(); return false; }
    s.idx++;
    while (s.idx < s.marks.length && !s.marks[s.idx].find()) s.idx++;
    if (s.idx < s.marks.length) { const r = s.marks[s.idx].find(); cmi.setSelection(r.from, r.to); if (s.idx === s.marks.length - 1) ed._snip = { marks: s.marks, idx: s.idx, last: true }; return true; }
    ed._clearSnippet();
    return false;
  };
  const tab = (cmi) => {
    if (ed._snip && !ed._snip.last && nextField(cmi)) return;
    if (ed._snip) ed._clearSnippet();
    if (cmi.somethingSelected()) { cmi.execCommand('indentMore'); return; }
    const cur = cmi.getCursor();
    const before = cmi.getLine(cur.line).slice(0, cur.ch);
    const after = cmi.getLine(cur.line).slice(cur.ch);
    const m = before.match(/([A-Za-z_][A-Za-z0-9_\-]*)$/);
    if (m && !/^[A-Za-z0-9_]/.test(after) && !readOnly && ed.lang !== 'text') {
      const sn = snippetCache && snippetCache.find((x) => x.trigger === m[1]);
      const from = { line: cur.line, ch: cur.ch - m[1].length };
      if (sn && expandSnippet(ed, cmi, sn, from, cur)) return;
      // cout → „cout << ;”, vector → „vector<> ;”, for → pętla z klamrami (także gdy lista podpowiedzi jest zamknięta)
      if (ed.lang === 'cpp' || ed.lang === 'c') {
        const sym = docSymbols(ed, cmi);
        const cs = contSnippet(m[1], before.slice(0, from.ch), after, loopInfo(sym, visibleVars(sym, cmi.indexFromPos(from))));
        if (cs && expandSnippet(ed, cmi, cs, from, cur)) { if (cs.openHints) setTimeout(() => showHints(ed, cmi, true), 30); return; }
      } else if (ed.lang === 'python') {
        // for → „for i in range(n):”, def → „def nazwa():”, print → „print()” (także gdy lista podpowiedzi jest zamknięta)
        const sym = docSymbols(ed, cmi);
        const pos = cmi.indexFromPos(from);
        const vis = [...sym.vars].filter(([, v]) => v.pos < pos);
        const bound = ['n', 'm', 'k', 'q', 't'].find((x) => vis.some(([n, v]) => n === x && (!v.type || v.type === 'int'))) || '';
        const cs = pyCont(m[1], before.slice(0, from.ch), after, { used: new Set(vis.map(([n]) => n)), bound });
        if (cs && expandSnippet(ed, cmi, cs, from, cur)) return;
      }
    }
    cmi.replaceSelection(' '.repeat(4 - (cur.ch % 4 || 0) || 4));
  };
  // „>” i „;” we wstawce: przeskocz istniejący znak zamiast dopisywać drugi (vector<int|> , cout << x|;)
  const overtype = (ch) => (cmi) => {
    const cur = cmi.getCursor();
    const ln = cmi.getLine(cur.line);
    const s = ed._snip;
    if (s && !cmi.somethingSelected() && ln[cur.ch] === ch) {
      const r = s.marks[s.idx] && s.marks[s.idx].find();
      const typed = r ? cmi.getRange(r.from, cur) : '';
      if (ch === '>' && (typed.match(/</g) || []).length > (typed.match(/>/g) || []).length) return CM().Pass;
      cmi.setCursor({ line: cur.line, ch: cur.ch + 1 });
      if (ch === '>') { if (!nextField(cmi)) ed._clearSnippet(); } else ed._clearSnippet();
      return undefined;
    }
    return CM().Pass;
  };
  const keys = {
    Tab: tab,
    "'>'": overtype('>'),
    "';'": overtype(';'),
    'Shift-Tab': (c) => c.execCommand('indentLess'),
    Esc: () => { ed._clearSnippet(); },
    [`${mod}-Enter`]: () => { onRun && onRun(ed); },
    'Ctrl-Enter': () => { onRun && onRun(ed); },
    [`Shift-${mod}-F`]: () => ed.format(),
    'Shift-Alt-F': () => ed.format(),
    [`${mod}-/`]: (c) => c.toggleComment({ indent: true }),
    'Ctrl-Space': (c) => showHints(ed, c, false),
    [`${mod}-S`]: () => { opts.onSave ? opts.onSave(ed) : toast('Zapisywane automatycznie ✓', 'success', { timeout: 1000 }); },
  };
  cm.setOption('extraKeys', keys);
  if (!readOnly && opts.autocomplete !== false) attachAutocomplete(ed, cm);

  // podpowiedź „Tab → snippet”
  const updateHint = () => {
    if (readOnly || !snippetCache || ed.lang === 'text') { hint.style.opacity = '0'; return; }
    const cur = cm.getCursor();
    const before = cm.getLine(cur.line).slice(0, cur.ch);
    const m = before.match(/([A-Za-z_][A-Za-z0-9_\-]*)$/);
    const sn = m && snippetCache.find((s) => s.trigger === m[1]);
    if (sn) {
      hint.replaceChildren(h('span', { class: 'badge accent' }, icon('braces', 12), `Tab → ${sn.name || sn.trigger}`));
      hint.style.opacity = '1';
    } else hint.style.opacity = '0';
  };

  const save = storageKey ? debounceLocal(() => local.set('ed:' + storageKey, cm.getValue()), 400) : null;
  cm.on('change', () => {
    if (save) save();
    if (onChange) onChange(cm.getValue(), ed);
  });
  cm.on('cursorActivity', updateHint);
  cm.on('blur', () => { hint.style.opacity = '0'; });
  if (!readOnly) loadSnippets();
  if (opts.autofocus) setTimeout(() => cm.focus(), 50);
  setTimeout(() => cm.refresh(), 0);
  return ed;
}

function debounceLocal(fn, ms) { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; }

// ------------------------------------------------------------------------------------- podpowiedzi
const CPP_WORDS = ('int long double float char bool void auto const constexpr static struct class template typename return if else for while do ' +
  'break continue switch case default namespace using include define vector set map multiset multimap unordered_map unordered_set queue ' +
  'priority_queue stack deque pair tuple string bitset array list sort stable_sort lower_bound upper_bound binary_search unique reverse ' +
  'accumulate iota fill memset min max min_element max_element swap make_pair emplace_back push_back pop_back insert erase find count ' +
  'begin end rbegin rend size empty clear resize reserve front back top push pop first second greater less cin cout cerr endl getline ' +
  'ios_base sync_with_stdio tie setprecision fixed printf scanf __gcd gcd lcm abs llabs sqrt pow ceil floor round __builtin_popcount ' +
  'next_permutation nth_element partial_sum to_string stoi stoll numeric_limits INT_MAX INT_MIN LLONG_MAX mt19937 chrono').split(' ');
const CPP_SET = new Set(CPP_WORDS.concat(('std cin cout cerr endl tie sync_with_stdio ios_base nullptr NULL true false begin end size first second ' +
  'push_back emplace_back pop_back insert erase find count clear empty front back top push pop resize assign').split(' ')));

// ------------------------------------------------------------------------------------- autouzupełnianie
// Model z /api/complete/model (Twoje kody z SIO2) — obecnie NIE używany w podpowiedziach (użytkownik nie chce nazw z innych programów);
// podpowiedzi biorą tylko symbole bieżącego pliku, C++/STL, wstawki (cout << ;, vector<>, for) i snippety.
let personal = null;
let personalPromise = null;
let acEnabled = true;
settings().then((S) => { acEnabled = !(S.editor && S.editor.autocomplete === false); }).catch(() => {});
onSettings((S) => { acEnabled = !(S.editor && S.editor.autocomplete === false); });

export function loadPersonalModel(force = false) {
  if (force) { personal = null; personalPromise = null; }
  if (personal) return Promise.resolve(personal);
  if (!personalPromise) {
    personalPromise = api.get('/api/complete/model', null, { quiet: true }).then((r) => {
      const m = r.model || {};
      personal = { words: m.words || [], members: m.members || [], next: m.next || {}, next2: m.next2 || {}, lines: m.lines || [], files: m.files || 0,
        funcs: m.funcs || [], types: m.types || [] };
      return personal;
    }).catch(() => { personalPromise = null; return null; });
  }
  return personalPromise;
}
window.addEventListener('oif:complete-refresh', () => loadPersonalModel(true));

const MEMBER_WORDS = ('push_back emplace_back pop_back size empty clear begin end rbegin rend front back insert erase find count ' +
  'lower_bound upper_bound first second top push pop resize reserve assign substr length c_str append at swap emplace ' +
  'contains count find_first_of rfind').split(' ');

// ---- symbole bieżącego pliku (jak „local completer” w onlinegdb/Ace, ale z rozróżnieniem rodzajów):
// zmienne TYLKO zadeklarowane (int x, vector<int> a, parametry, pętle, auto [a, b]), funkcje zdefiniowane albo
// choć raz wywołane, typy (typedef/using/struct) i makra (#define). Komentarze i napisy są pomijane.
const KW = new Set(('if else for while do switch case default return break continue goto sizeof new delete throw try catch ' +
  'namespace using typedef template typename struct class public private protected operator this true false nullptr ' +
  'const constexpr static inline extern volatile mutable register signed unsigned friend virtual explicit auto ' +
  'and or not include define pragma main').split(' '));
const BASE_TYPES = new Set(('int long short double float char bool void auto size_t string ll ull ld lld uint ' +
  'vector set map multiset multimap unordered_map unordered_set queue priority_queue stack deque pair tuple bitset array list ' +
  'int64_t uint64_t int32_t uint32_t __int128 mt19937 mt19937_64 complex function').split(' '));
const TYPE_MODS = new Set(['const', 'static', 'unsigned', 'signed', 'long', 'short', 'constexpr', 'inline', 'register', 'volatile', 'std', 'struct']);

function stripCode(text) {
  // komentarze i literały → spacje (zachowujemy pozycje, żeby wiedzieć, co jest przed kursorem)
  return text.replace(/\/\/[^\n]*|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/g, (m) => m.replace(/[^\n]/g, ' '));
}

function docSymbols(ed, cm) {
  const gen = cm.changeGeneration();
  if (ed._sym && ed._sym.gen === gen) return ed._sym;
  const sym = { gen, vars: new Map(), funcs: new Map(), types: new Map(), macros: new Map(), used: new Map(), decls: [],
    occ: new Map(), reads: new Set(), assigned: new Map(), alias: new Map(), structs: new Map() };
  const addVar = (n, v) => { sym.vars.set(n, v); sym.decls.push({ name: n, ...v }); };
  const text = cm.getValue();
  if (text.length > 400000) { ed._sym = sym; return sym; }
  if (ed.lang === 'python') {
    // Python: zmienne z przypisań (z prostym typem z prawej strony), parametry, pętle, with/except as, importy, def, class
    const t = stripCode(text.replace(/#[^\n]*/g, (m) => ' '.repeat(m.length)));
    sym.modules = new Map();     // nazwa w kodzie → moduł (import math as m → m: math)
    const setV = (n, type, pos) => {
      if (!/^[A-Za-z_]\w*$/.test(n) || PY_KW.has(n)) return;
      const p = sym.vars.get(n);
      if (!p || (!p.type && type)) sym.vars.set(n, { type: type || p?.type || '', pos: p ? Math.min(p.pos, pos) : pos });
    };
    for (const m of t.matchAll(/^\s*def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)/gm)) {
      sym.funcs.set(m[1], { sig: `(${m[2].replace(/\s+/g, ' ').trim()})`, pos: m.index });
      for (const pm of m[2].split(',')) setV(pm.trim().split(/[=:\s]/)[0].replace(/^\*+/, ''), 'parametr', m.index);
    }
    for (const m of t.matchAll(/^\s*class\s+([A-Za-z_]\w*)/gm)) sym.types.set(m[1], { pos: m.index });
    for (const m of t.matchAll(/^\s*import\s+([^\n;]+)/gm)) {
      for (const part of m[1].split(',')) { const mm = /^\s*([\w.]+)(?:\s+as\s+(\w+))?/.exec(part); if (mm) sym.modules.set(mm[2] || mm[1].split('.')[0], mm[1]); }
    }
    for (const m of t.matchAll(/^\s*from\s+([\w.]+)\s+import\s+([^\n;]+)/gm)) {
      for (const part of m[2].replace(/[()]/g, '').split(',')) { const mm = /^\s*(\w+)(?:\s+as\s+(\w+))?/.exec(part); if (mm) sym.funcs.set(mm[2] || mm[1], { sig: `z ${m[1]}`, pos: m.index, imported: true }); }
    }
    for (const m of t.matchAll(/(?:^|[;\n])[ \t]*([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:[-+*/%|&^]|\/\/|\*\*)?=(?!=)([^\n;]*)/g)) {
      const names = m[1].split(',').map((x) => x.trim());
      const rhs = m[2].trim();
      let ty = pyType(rhs);
      if (names.length > 1) ty = /^map\(\s*int\b/.test(rhs) ? 'int' : /^map\(\s*float\b/.test(rhs) ? 'float' : /\.split\(\)\s*$/.test(rhs) ? 'str' : '';
      for (const n of names) setV(n, ty, m.index + m[0].indexOf(n));
    }
    for (const m of t.matchAll(/\bfor\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+in\s+([^\n:]*)/g)) {
      const ty = /^range\(/.test(m[2].trim()) ? 'int' : '';
      for (const n of m[1].split(',')) setV(n.trim(), ty || 'pętla', m.index);
    }
    for (const m of t.matchAll(/\b(?:as)\s+([A-Za-z_]\w*)/g)) if (!sym.modules.has(m[1])) setV(m[1], '', m.index);
    for (const m of t.matchAll(/\b([A-Za-z_]\w*)\s*\(/g)) if (!PY_KW.has(m[1])) sym.used.set(m[1], (sym.used.get(m[1]) || 0) + 1);
    for (const m of t.matchAll(/[A-Za-z_]\w*/g)) { let a = sym.occ.get(m[0]); if (!a) sym.occ.set(m[0], (a = [])); a.push(m.index); }
    ed._sym = sym;
    return sym;
  }
  const t = stripCode(text);
  for (const m of t.matchAll(/^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(\([^)]*\))?/gm)) sym.macros.set(m[1], { sig: m[2] || '', pos: m.index });
  for (const m of t.matchAll(/\btypedef\b[^;{}]*?\b([A-Za-z_]\w*)\s*;|\busing\s+([A-Za-z_]\w*)\s*=|\b(?:struct|class)\s+([A-Za-z_]\w*)/g)) {
    sym.types.set(m[1] || m[2] || m[3], { pos: m.index });
  }
  const toks = [];
  for (const m of t.replace(/^[ \t]*#[^\n]*/gm, (x) => ' '.repeat(x.length)).matchAll(/[A-Za-z_]\w*|\d[\w.']*|::|->|<<|>>|&&|\|\||\S/g)) toks.push({ s: m[0], i: m.index });
  const isId = (x) => x && /^[A-Za-z_]\w*$/.test(x.s) && !KW.has(x.s);
  const isTypeName = (x) => x && (BASE_TYPES.has(x.s) || sym.types.has(x.s) || sym.macros.has(x.s) && /^[A-Z_a-z]+$/.test(x.s) && ['ll', 'ull', 'ld', 'pii', 'vi', 'vll'].includes(x.s));
  // przeskocz typ zaczynający się w toks[i]; zwraca {end, text} albo null
  const parseType = (i) => {
    let j = i; let txt = ''; let saw = false;
    while (j < toks.length) {
      const x = toks[j];
      if (TYPE_MODS.has(x.s)) {
        if (['long', 'short', 'unsigned', 'signed'].includes(x.s)) saw = true;   // „long long x”, „unsigned x”
        txt += x.s === 'std' ? '' : x.s + ' '; j++; if (toks[j] && toks[j].s === '::') j++; continue;
      }
      if (isTypeName(x) || (saw === false && x.s === 'auto')) {
        txt += x.s; j++; saw = true;
        if (toks[j] && toks[j].s === '<') {        // argumenty szablonu, z zagnieżdżeniem
          let d = 0; const st = j;
          for (; j < toks.length; j++) {
            if (toks[j].s === '<') d++; else if (toks[j].s === '>') d--; else if (toks[j].s === '>>') d -= 2;
            else if (toks[j].s === ';' || toks[j].s === '{') break;
            if (d <= 0) { j++; break; }
          }
          txt += toks.slice(st, j).map((y) => y.s).join('').replace(/,/g, ', ');
        }
        if (toks[j] && toks[j].s === '::') { j++; continue; }   // np. vector<int>::iterator
        continue;
      }
      break;
    }
    if (!saw) return null;
    while (toks[j] && (toks[j].s === '*' || toks[j].s === '&' || toks[j].s === '&&')) { txt += toks[j].s; j++; }
    return { end: j, text: txt.trim() };
  };
  const skipTo = (j, stops) => {   // przeskocz inicjalizator do „,” / „;” / „)” na zerowym poziomie
    let d = 0;
    for (; j < toks.length; j++) {
      const s = toks[j].s;
      if (s === '(' || s === '[' || s === '{') d++;
      else if (s === ')' || s === ']' || s === '}') { if (d === 0) return j; d--; }
      else if (d === 0 && stops.includes(s)) return j;
    }
    return j;
  };
  for (let i = 0; i < toks.length; i++) {
    const x = toks[i];
    // wywołania funkcji (nie metody): f(…)
    if (isId(x) && toks[i + 1] && toks[i + 1].s === '(' && !(toks[i - 1] && ['.', '->', '::'].includes(toks[i - 1].s)) && !BASE_TYPES.has(x.s)) {
      sym.used.set(x.s, (sym.used.get(x.s) || 0) + 1);
    }
    // structured bindings: auto [a, b] / auto& [a, b]
    if (x.s === 'auto') {
      let j = i + 1; while (toks[j] && (toks[j].s === '&' || toks[j].s === '&&' || toks[j].s === 'const')) j++;
      if (toks[j] && toks[j].s === '[') {
        for (j++; toks[j] && toks[j].s !== ']'; j++) if (isId(toks[j])) addVar(toks[j].s, { type: 'auto', pos: toks[j].i });
        continue;
      }
    }
    if (!(TYPE_MODS.has(x.s) || isTypeName(x))) continue;
    if (toks[i - 1] && (['.', '->', '::'].includes(toks[i - 1].s) || /^[A-Za-z_]\w*$/.test(toks[i - 1].s) && !KW.has(toks[i - 1].s) && !TYPE_MODS.has(toks[i - 1].s))) continue;
    const ty = parseType(i);
    if (!ty) continue;
    let j = ty.end;
    if (!isId(toks[j])) { i = Math.max(i, ty.end - 1); continue; }
    const name = toks[j];
    const nx = toks[j + 1] ? toks[j + 1].s : '';
    if (nx === '(') {
      // funkcja (parametry z typami + „{” albo „;”) czy zmienna z konstruktorem: vector<int> a(n);
      const close = skipTo(j + 2, []);
      const inner = toks.slice(j + 2, close);
      const after = toks[close + 1] ? toks[close + 1].s : '';
      const looksFunc = inner.length === 0 ? after === '{' || after === 'const' : inner.some((y, k) => (isTypeName(y) || TYPE_MODS.has(y.s)) && (k === 0 || inner[k - 1].s === ','));
      if (looksFunc) {
        const sig = '(' + inner.map((y) => y.s).join(' ').replace(/ ([,)&*<>]) ?/g, '$1 ').replace(/ ?([<(]) /g, '$1').replace(/\s+/g, ' ').trim() + ')';
        sym.funcs.set(name.s, { sig: sig.replace(/ \)/, ')'), ret: ty.text, pos: name.i });
        i = j + 1;   // parametry przeanalizuje dalsza część pętli
        continue;
      }
      addVar(name.s, { type: ty.text, pos: name.i });
      i = close;
      continue;
    }
    if (['=', ';', ',', '[', '{', ':', ')'].includes(nx)) {
      addVar(name.s, { type: ty.text, pos: name.i, arr: nx === '[', of: nx === ':' && toks[j + 2] && isId(toks[j + 2]) ? toks[j + 2].s : '' });
      // kolejne deklaratory: int a = 1, b, c[5];
      let k = skipTo(j + 1, [',', ';', ':']);
      while (toks[k] && toks[k].s === ',' && toks[k - 1] && !(toks[k].s === ')')) {
        let q = k + 1; let ptr = '';
        while (toks[q] && (toks[q].s === '*' || toks[q].s === '&')) { ptr += toks[q].s; q++; }
        if (!isId(toks[q]) || (toks[q + 1] && !['=', ';', ',', '[', '{', '(', ')'].includes(toks[q + 1].s))) break;
        if (isTypeName(toks[q])) break;   // „int a, int b” w parametrach — obsłuży główna pętla
        addVar(toks[q].s, { type: ty.text + ptr, pos: toks[q].i, arr: !!(toks[q + 1] && toks[q + 1].s === '[') });
        k = skipTo(q + 1, [',', ';']);
      }
      i = j;
    }
  }
  finishSymbols(sym, t, toks);
  ed._sym = sym;
  return sym;
}

// ---- zasięgi, wczytania, przypisania, aliasy typów i pola struktur (do „sprytnej” kolejności podpowiedzi)
function stmtEnd(t, pos, parenMatch, braceMatch) {
  // koniec instrukcji zaczynającej się po `pos` (ciało pętli bez klamer, także zagnieżdżone for/if)
  let p = pos;
  for (let guard = 0; guard < 8; guard++) {
    p += /^\s*/.exec(t.slice(p, p + 200))[0].length;
    if (t[p] === '{') return braceMatch.has(p) ? braceMatch.get(p) : Infinity;
    const k = /^(for|while|if)\s*\(/.exec(t.slice(p, p + 12));
    if (k) { const cl = parenMatch.get(p + k[0].length - 1); if (cl === undefined) return Infinity; p = cl + 1; continue; }
    const k2 = /^(else|do)\b/.exec(t.slice(p, p + 5));
    if (k2) { p += k2[0].length; continue; }
    const sc = t.indexOf(';', p);
    return sc < 0 ? Infinity : sc;
  }
  return Infinity;
}

function splitTop(s) {
  // podział po przecinkach poza <> () — „pair<int, int> a, b” → ["pair<int, int> a", " b"]
  const out = []; let d = 0; let cur = '';
  for (const ch of s) {
    if (ch === '<' || ch === '(') d++; else if (ch === '>' || ch === ')') d = Math.max(0, d - 1);
    if (ch === ',' && d === 0) { out.push(cur); cur = ''; } else cur += ch;
  }
  if (cur.trim()) out.push(cur);
  return out;
}

function finishSymbols(sym, t, toks) {
  const braceMatch = new Map(); const parenMatch = new Map();
  const bs = []; const ps = [];
  const decls = sym.decls.sort((a, b) => a.pos - b.pos);
  let di = 0;
  for (let i = 0; i <= t.length; i++) {
    while (di < decls.length && decls[di].pos <= i) {
      const d = decls[di++];
      d.open = bs.length ? bs[bs.length - 1] : -1;
      d.paren = ps.length ? ps[ps.length - 1] : -1;
    }
    if (i === t.length) break;
    const c = t[i];
    if (c === '{') bs.push(i);
    else if (c === '}') { const o = bs.pop(); if (o !== undefined) braceMatch.set(o, i); }
    else if (c === '(') ps.push(i);
    else if (c === ')') { const o = ps.pop(); if (o !== undefined) parenMatch.set(o, i); }
  }
  for (const d of decls) {
    if (d.paren > d.open) {            // parametr funkcji / zmienna z nagłówka pętli
      d.loop = /\bfor\s*$/.test(t.slice(Math.max(0, d.paren - 12), d.paren));
      d.param = !d.loop;
      const pc = parenMatch.get(d.paren);
      d.end = pc === undefined ? Infinity : stmtEnd(t, pc + 1, parenMatch, braceMatch);
    } else d.end = d.open < 0 ? Infinity : (braceMatch.has(d.open) ? braceMatch.get(d.open) : Infinity);
  }
  for (let i = 0; i < toks.length; i++) {
    const x = toks[i];
    if (!/^[A-Za-z_]\w*$/.test(x.s)) continue;
    let a = sym.occ.get(x.s);
    if (!a) sym.occ.set(x.s, (a = []));
    a.push(x.i);
    const pv = toks[i - 1]; const nx = toks[i + 1]; const nx2 = toks[i + 2];
    if (pv && pv.s === '>>') sym.reads.add(x.s);                       // cin >> x, cin >> a[i]
    if (nx && ((nx.s === '=' && !(nx2 && nx2.s === '=')) || (/^[-+*/%|&^]$/.test(nx.s) && nx2 && (nx2.s === '=' || nx2.s === nx.s && /[-+]/.test(nx.s))))) {
      sym.assigned.set(x.s, x.i);
    }
  }
  for (const m of t.matchAll(/\btypedef\s+([^;{}]+?)\s+([A-Za-z_]\w*)\s*;/g)) sym.alias.set(m[2], m[1].trim());
  for (const m of t.matchAll(/\busing\s+([A-Za-z_]\w*)\s*=\s*([^;]+);/g)) sym.alias.set(m[1], m[2].trim());
  for (const m of t.matchAll(/^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\n]+)/gm)) {
    if (/^[\w:<>, ]+$/.test(m[2].trim()) && /[a-z]/.test(m[2])) sym.alias.set(m[1], m[2].trim());
  }
  for (const m of t.matchAll(/\b(?:struct|class)\s+([A-Za-z_]\w*)[^;{()]*\{/g)) {
    const open = m.index + m[0].length - 1;
    const close = braceMatch.has(open) ? braceMatch.get(open) : t.length;
    const pieces = []; let depth = 0; let cur = '';
    for (const ch of t.slice(open + 1, close)) {
      if (ch === '{') { depth++; continue; }
      if (ch === '}') { depth = Math.max(0, depth - 1); if (!depth) cur = ''; continue; }
      if (depth) continue;
      if (ch === ';') { pieces.push(cur); cur = ''; } else cur += ch;
    }
    const fields = [];
    for (let p of pieces) {
      p = p.replace(/\b(public|private|protected)\s*:/g, '').trim();
      if (!p || p.includes('(') || /^(using|typedef|friend|static|template)\b/.test(p)) continue;
      const segs = splitTop(p.replace(/=[^,]*/g, '').replace(/\[[^\]]*\]/g, '[]'));
      const first = /^([\s\S]*?)([A-Za-z_]\w*)\s*(\[\])*\s*$/.exec(segs[0] || '');
      if (!first || !first[1].trim()) continue;
      const ty = first[1].trim();
      fields.push({ name: first[2], type: ty, arr: !!first[3] });
      for (const sg of segs.slice(1)) {
        const mm = /([A-Za-z_]\w*)\s*(\[\])*\s*$/.exec(sg);
        if (mm) fields.push({ name: mm[1], type: ty, arr: !!mm[2] });
      }
    }
    sym.structs.set(m[1], fields);
  }
}

function resolveType(sym, ty) {
  let t = String(ty || '').replace(/std::/g, '').replace(/\b(const|static|constexpr|inline|struct|unsigned)\b/g, '').replace(/[&*]/g, '').trim();
  for (let k = 0; k < 3; k++) {
    const m = /^([A-Za-z_]\w*)([\s\S]*)$/.exec(t);
    if (m && sym.alias.has(m[1]) && sym.alias.get(m[1]) !== m[1]) t = (sym.alias.get(m[1]).replace(/std::/g, '') + m[2]).trim();
    else break;
  }
  return t;
}
function headName(t) { const m = /^([A-Za-z_]\w*)/.exec(t || ''); return m ? m[1] : ''; }
function templateArgs(t) {
  const i = t.indexOf('<');
  if (i < 0) return [];
  let d = 0; let j = i;
  for (; j < t.length; j++) { if (t[j] === '<') d++; else if (t[j] === '>') { d--; if (!d) break; } }
  return splitTop(t.slice(i + 1, j)).map((x) => x.trim());
}
function elemType(sym, ty) {
  const t = resolveType(sym, ty);
  const head = headName(t); const args = templateArgs(t);
  if (['vector', 'deque', 'array', 'set', 'multiset', 'unordered_set', 'valarray'].includes(head)) return args[0] || '';
  if (['map', 'multimap', 'unordered_map'].includes(head)) return args[1] || '';
  if (head === 'string') return 'char';
  return '';
}
function declType(sym, vis, d, seen = 0) {
  if (d.type === 'auto' && d.of && seen < 3) {          // for (auto x : a) → typ elementu a
    const src = vis.get(d.of) || sym.vars.get(d.of);
    if (src) { let ty = declType(sym, vis, src, seen + 1); if (!src.arr) ty = elemType(sym, ty); return ty; }
  }
  return d.type || '';
}
function visibleVars(sym, curPos) {
  const vis = new Map();
  for (const d of sym.decls) {
    if (!(d.pos < curPos) || !(curPos <= d.end)) continue;
    const p = vis.get(d.name);
    if (!p || p.pos < d.pos) vis.set(d.name, d);
  }
  return vis;
}

// ---- wstawki: cout/cin z operatorem, kontenery z <> (kursor w środku, Tab → następne pole)
const STREAM_CONT = { cout: ' << ', cerr: ' << ', cin: ' >> ' };
const TEMPLATE_WORDS = new Set('vector set multiset map multimap unordered_map unordered_set queue priority_queue stack deque pair tuple bitset array greater less'.split(' '));
const CONTAINERS = new Set('vector set multiset map multimap unordered_map unordered_set queue priority_queue stack deque bitset array'.split(' '));
const ITER_FUNCS = {
  sort: '', stable_sort: '', reverse: '', next_permutation: '', prev_permutation: '', max_element: '', min_element: '', unique: '',
  accumulate: ', 0LL', lower_bound: ', ', upper_bound: ', ', binary_search: ', ', fill: ', ', count: ', ', find: ', ', iota: ', 0',
};
const ITER_RE = new RegExp(`\\b(${Object.keys(ITER_FUNCS).join('|')})\\s*\\($`);
const TEMPL_RE = /\b(vector|set|multiset|map|multimap|unordered_map|unordered_set|queue|priority_queue|stack|deque|pair|tuple|array|greater|less)\s*<([^;()<>]*)$/;
const CIN_RE = /\bcin\b[^;]*>>\s*$/;
const RESULT_RE = /^(wynik|wyn|res|ans|odp|sum|suma|cnt|ile|best|mx|mn|maks|mini|result|out|odpowiedz)$/i;
// metody wg typu: „x()” bez argumentów, „x(” z argumentem (kursor w nawiasie), „x” pole
const MEMBERS = {
  vector: 'push_back( size() back() pop_back() begin() end() clear() resize( empty() emplace_back( front() insert( erase( assign( rbegin() rend()',
  deque: 'push_back( push_front( pop_back() pop_front() front() back() size() empty() clear() begin() end()',
  string: 'size() length() substr( push_back( pop_back() back() begin() end() find( empty() c_str() front() insert( erase( rbegin() rend()',
  set: 'insert( count( erase( find( begin() end() size() lower_bound( upper_bound( empty() clear() rbegin() rend()',
  map: 'count( find( erase( begin() end() size() lower_bound( upper_bound( empty() clear() insert( rbegin() rend()',
  queue: 'push( pop() front() empty() size() back()',
  stack: 'push( pop() top() empty() size()',
  priority_queue: 'push( pop() top() empty() size()',
  pair: 'first second',
  bitset: 'count() set( reset( flip( test( size() any() none()',
  array: 'size() begin() end() fill( front() back()',
};
const KIND_ALIAS = { multiset: 'set', unordered_set: 'set', multimap: 'map', unordered_map: 'map' };

function contSnippet(word, ctxText, after, info = null) {
  const afterEmpty = /^\s*\}?\s*$/.test(after);
  const stmtStart = /(^|[;{}]|\)|\belse)\s*((const|static)\s+)*$/.test(ctxText);
  if (['for', 'if', 'while', 'else'].includes(word) && afterEmpty && /(^|[;{}]|\)|\belse)\s*$/.test(ctxText)) {
    const body = '{\n    ${2}\n}${0}';
    if (word === 'else') return { trigger: word, name: word, scope: 'local', lang: 'cpp', code: 'else {\n    ${1}\n}${0}', display: 'else { }' };
    if (word === 'for' && info) {
      const v = ['i', 'j', 'k', 'l', 'ii'].find((x) => !info.used.has(x));
      const b = info.bound && info.bound !== v ? info.bound : 'n';
      return { trigger: word, name: word, scope: 'local', lang: 'cpp', code: `for (int ${v} = 0; ${v} < \${1:${b}}; ${v}++) ` + body, display: `for (int ${v} = 0; ${v} < ${b}; ${v}++) { }` };
    }
    return { trigger: word, name: word, scope: 'local', lang: 'cpp', code: `${word} (\${1}) ` + body, display: `${word} ( ) { }` };
  }
  if (STREAM_CONT[word]) {
    const semi = afterEmpty && stmtStart;
    return { trigger: word, name: word, scope: 'local', lang: 'cpp', code: `${word}${STREAM_CONT[word]}\${1}${semi ? ';${0}' : ''}`, display: `${word}${STREAM_CONT[word].trimEnd()}${semi ? ' ;' : ''}`, openHints: word === 'cin' };
  }
  if (TEMPLATE_WORDS.has(word)) {
    const decl = stmtStart && afterEmpty && !['greater', 'less'].includes(word);
    return { trigger: word, name: word, scope: 'local', lang: 'cpp', code: decl ? `${word}<\${1}> \${2};\${0}` : `${word}<\${1}>\${0}`, display: decl ? `${word}<> ;` : `${word}<>`, openHints: true };
  }
  return null;
}

// ---- Python: osobny zestaw (słowa kluczowe, funkcje wbudowane, metody wg typu, moduły, wstawki z dwukropkiem i wcięciem)
const PY_KW = new Set(('False None True and as assert async await break class continue def del elif else except finally for from global ' +
  'if import in is lambda nonlocal not or pass raise return try while with yield').split(' '));
const PY_BUILTINS = ('print input int str float list dict set tuple range len sorted map sum min max abs enumerate zip reversed ' +
  'filter any all round divmod pow ord chr bin hex open iter next isinstance type bool format').split(' ');
const PY_MEMBERS = {
  list: 'append pop sort extend insert remove index count reverse copy clear',
  str: 'split strip join replace find count startswith endswith lower upper isdigit isalpha rstrip lstrip format index zfill',
  dict: 'get items keys values setdefault pop update copy clear',
  set: 'add remove discard pop union intersection difference update clear copy',
  deque: 'append appendleft pop popleft extend clear rotate',
  int: 'bit_length bit_count to_bytes',
  float: 'is_integer',
};
const PY_MODULES = {
  sys: 'stdin stdout setrecursionlimit exit argv maxsize', 'sys.stdin': 'readline read readlines buffer',
  math: 'sqrt isqrt gcd lcm inf ceil floor log log2 log10 pi comb perm factorial fabs hypot',
  heapq: 'heappush heappop heapify nsmallest nlargest heappushpop', bisect: 'bisect_left bisect_right insort',
  collections: 'deque defaultdict Counter OrderedDict', itertools: 'permutations combinations product accumulate count chain',
  functools: 'lru_cache cache reduce cmp_to_key', random: 'randint random choice shuffle sample seed',
  string: 'ascii_lowercase ascii_uppercase digits', time: 'time perf_counter', os: 'path getcwd listdir',
};
const PY_ARGS = new Set('print input int str float list dict set tuple range len sorted map sum min max abs enumerate zip reversed filter any all round divmod pow ord chr bin hex open iter next isinstance type bool'.split(' '));
const PY_WORDS = [...PY_KW, ...PY_BUILTINS];

function pyType(rhs) {
  const r = rhs.trim();
  if (/^\[/.test(r) || /^(list|sorted)\(/.test(r) || (/\.split\([^)]*\)\s*$/.test(r) && !/^map\(/.test(r)) || /^list\(map\(/.test(r)) return 'list';
  if (/^(\{\s*\}|dict\(|defaultdict\(|Counter\()/.test(r) || /^\{[^}]*:/.test(r)) return 'dict';
  if (/^set\(/.test(r) || /^\{[^:}]+\}$/.test(r)) return 'set';
  if (/^deque\(/.test(r)) return 'deque';
  if (/^(input\(\)(\.strip\(\))?|str\(.*\)|sys\.stdin\.readline\(\)(\.strip\(\))?|['"].*)$/.test(r)) return 'str';
  if (/^(int\(|len\(|sum\(|-?\d+$|abs\(|ord\()/.test(r)) return 'int';
  if (/^(float\(|-?\d+\.\d*$)/.test(r)) return 'float';
  if (/^(True|False)$/.test(r)) return 'bool';
  if (/^map\(/.test(r)) return 'map';
  return '';
}

function pyCont(word, ctxText, after, info) {
  const afterEmpty = /^\s*$/.test(after);
  const lineStart = /^\s*$/.test(ctxText);
  const S = (code, display) => ({ trigger: word, name: word, scope: 'local', lang: 'python', code, display });
  if (lineStart && afterEmpty) {
    if (word === 'for') {
      const v = ['i', 'j', 'k', 'l'].find((x) => !info.used.has(x)) || 'i';
      return S(`for ${v} in range(\${1:${info.bound || 'n'}}):\n    \${2}\${0}`, `for ${v} in range(${info.bound || 'n'}):`);
    }
    if (word === 'if' || word === 'while' || word === 'elif') return S(`${word} \${1}:\n    \${2}\${0}`, `${word} …:`);
    if (word === 'else' || word === 'try' || word === 'finally') return S(`${word}:\n    \${1}\${0}`, `${word}:`);
    if (word === 'except') return S('except ${1:Exception}:\n    ${2}${0}', 'except …:');
    if (word === 'def') return S('def ${1:nazwa}(${2}):\n    ${3}${0}', 'def nazwa():');
    if (word === 'class') return S('class ${1:Nazwa}:\n    ${2}${0}', 'class Nazwa:');
  }
  if (word === 'input') return S('input()${0}', 'input()');
  if (word === 'map') return S('map(${1:int}, input().split())${0}', 'map(int, input().split())');
  if (PY_ARGS.has(word)) return S(`${word}(\${1})\${0}`, `${word}()`);
  return null;
}

function pyHints(ed, cm, sym, { word, lw, ctxText, tctx, after, curPos, put, match, curLine }) {
  const lineOf = (pos) => cm.posFromIndex(pos).line;
  const prox = (n) => { const a = sym.occ.get(n) || []; let p = -1; for (let k = a.length - 1; k >= 0; k--) if (a[k] < curPos) { p = a[k]; break; } if (p < 0) return 0; const dl = curLine - lineOf(p); return dl <= 2 ? 10 : dl <= 8 ? 6 : dl <= 25 ? 3 : 0; };
  // obj. — metody wg typu albo zawartość modułu
  const mm = /([A-Za-z_][\w.]*?)\s*(\[[^\]]*\])?\s*\.$/.exec(tctx);
  if (/\.$/.test(tctx)) {
    let list = null; let kind = '';
    if (mm) {
      const obj = mm[1];
      const modName = sym.modules?.get(obj.split('.')[0]);
      const full = modName ? [modName, ...obj.split('.').slice(1)].join('.') : obj;
      if (PY_MODULES[full]) { list = PY_MODULES[full].split(' '); kind = full; }
      else if (!mm[2]) {
        const v = sym.vars.get(obj);
        const ty = v && v.type;
        if (ty && PY_MEMBERS[ty]) { list = PY_MEMBERS[ty].split(' '); kind = ty; }
      }
      if (!list && /^['"]/.test(obj)) { list = PY_MEMBERS.str.split(' '); kind = 'str'; }
    }
    if (!list) { list = [...new Set(Object.values(PY_MEMBERS).join(' ').split(' '))]; kind = 'Python'; }
    list.forEach((m, i) => { if (match(m)) put(m, 200 - i, kind, { _ctx: true }); });
    return true;
  }
  // import … / from x import …
  const fm = /^\s*from\s+([\w.]+)\s+import\s+(?:[\w\s,]*,\s*)?$/.exec(ctxText);
  if (fm) { (PY_MODULES[fm[1]] || '').split(' ').filter(Boolean).forEach((m, i) => { if (match(m)) put(m, 200 - i, fm[1], { _ctx: true }); }); return true; }
  if (/^\s*(import|from)\s+$/.test(ctxText)) { Object.keys(PY_MODULES).filter((m) => !m.includes('.')).forEach((m, i) => { if (match(m)) put(m, 200 - i, 'moduł', { _ctx: true }); }); return true; }
  // nowa nazwa (def f / class K / parametry def / for x / as y / „x, y = …” przed =) — nic nie podpowiadamy
  if (/^\s*(def|class)\s+$/.test(ctxText) || /^\s*def\s+\w+\s*\([^)]*$/.test(ctxText) || /(^|[;\s])(for|as)\s+([\w\s]*,\s*)?$/.test(ctxText)) return true;
  const forIn = /\bfor\s+[\w\s,]+\s+in\s+$/.test(ctxText);
  for (const [n, v] of sym.vars) {
    if (!match(n)) continue;
    let s = (v.pos < curPos ? 64 : 30) + prox(n);
    if (forIn && ['list', 'str', 'dict', 'set', 'deque'].includes(v.type)) s += 25;
    put(n, s, 'zmienna', { _detail: v.type || '' });
  }
  for (const [n, v] of sym.funcs) if (match(n)) put(n, (v.imported ? 58 : 66) + prox(n), 'funkcja', { _detail: v.sig || '()' });
  for (const [n] of sym.types) if (match(n)) put(n, 52, 'klasa');
  for (const [n] of sym.modules || []) if (match(n)) put(n, 54, 'moduł');
  for (const [n, c] of sym.used) if (match(n) && !sym.vars.has(n) && !sym.funcs.has(n) && !PY_BUILTINS.includes(n) && !sym.types.has(n)) put(n, 50 + Math.min(c, 10), 'funkcja', { _detail: 'użyta w kodzie' });
  const vis = new Map([...sym.vars].filter(([, v]) => v.pos < curPos).map(([n, v]) => [n, { name: n, ...v }]));
  const bound = ['n', 'm', 'k', 'q', 't'].find((x) => vis.has(x) && (!vis.get(x).type || vis.get(x).type === 'int')) || '';
  const info = { used: new Set(vis.keys()), bound };
  if (forIn && match('range')) put('range()', 300, 'Python', { _cont: pyCont('range', '', '', info), _ctx: true });
  for (const w of PY_WORDS) {
    if (!match(w)) continue;
    const sn = pyCont(w, ctxText, after, info);
    if (sn) put(sn.display, lw === w ? 3e6 : lw.length >= 3 ? 60 : 9, 'Python', { _cont: sn });
    else put(w, 8, 'Python');
  }
  return false;
}

// ---- pliki tekstowe (.txt, .md, testy): tylko słowa, które już są w tym pliku
const TEXT_WORD = /[\p{L}_][\p{L}\p{N}_-]*/gu;
function textHints(ed, cm, auto) {
  const CMx = CM();
  const cur = cm.getCursor();
  const line = cm.getLine(cur.line);
  let start = cur.ch;
  while (start > 0 && /[\p{L}\p{N}_-]/u.test(line[start - 1])) start--;
  const word = line.slice(start, cur.ch);
  if (word.length < (auto ? 3 : 1) || /^\d/.test(word)) return null;
  const lw = word.toLocaleLowerCase('pl');
  const text = cm.getValue();
  if (text.length > 400000) return null;
  const curIdx = cm.indexFromPos({ line: cur.line, ch: start });
  const seen = new Map();
  for (const m of text.matchAll(TEXT_WORD)) {
    const w = m[0];
    if (w.length < 3 || m.index === curIdx) continue;
    const s = seen.get(w) || { c: 0, near: 1e9 };
    s.c++;
    s.near = Math.min(s.near, Math.abs(m.index - curIdx));
    seen.set(w, s);
  }
  const list = [...seen].filter(([w]) => w !== word && w.toLocaleLowerCase('pl').startsWith(lw))
    .map(([w, s]) => ({ w, score: Math.log2(1 + s.c) * 10 + (s.near < 300 ? 8 : s.near < 3000 ? 4 : 0) }))
    .sort((a, b) => b.score - a.score).slice(0, 10);
  if (!list.length) return null;
  return {
    list: list.map((x) => ({ text: x.w, render: (el) => { el.append(h('span', { class: 'h-text' }, x.w), h('span', { class: 'h-kind k-tekst' }, 'w pliku')); } })),
    from: CMx.Pos(cur.line, start), to: CMx.Pos(cur.line, cur.ch),
  };
}

function loopInfo(sym, vis) {
  // nazwy zajęte w tym miejscu + najpewniejsza granica pętli (n, m, … albo pierwsza wczytana liczba)
  const used = new Set(vis.keys());
  const scal = [...vis.values()].filter((d) => !d.loop && !d.arr && !CONTAINERS.has(headName(resolveType(sym, d.type))) && /int|ll|long|short|size_t|auto/.test(resolveType(sym, d.type) + ' ' + d.type));
  const pick = ['n', 'm', 'k', 'q', 'N', 't'].find((x) => scal.some((d) => d.name === x))
    || (scal.find((d) => sym.reads.has(d.name)) || {}).name || '';
  return { used, bound: pick };
}

function buildHints(ed, cm, auto) {
  if (ed.lang === 'text') return textHints(ed, cm, auto);
  const CMx = CM();
  const cur = cm.getCursor();
  const line = cm.getLine(cur.line);
  const before = line.slice(0, cur.ch);
  const after = line.slice(cur.ch);
  let start = cur.ch;
  while (start > 0 && /\w/.test(line[start - 1])) start--;
  const word = line.slice(start, cur.ch);
  const lw = word.toLowerCase();
  const ctxText = before.slice(0, start);
  const tctx = ctxText.replace(/\s+$/, '');
  const python = ed.lang === 'python';
  const from = CMx.Pos(cur.line, start);
  const sym = docSymbols(ed, cm);
  const items = new Map();
  const put = (text, score, kind, extra = {}) => {
    if (!text || text === word) return;
    const p = items.get(text);
    if (!p || p._score < score) items.set(text, { text, _score: score, _kind: kind, ...extra });
  };
  const match = (t) => !lw || t.toLowerCase().startsWith(lw);
  let noSnip = false;   // po „a.” i w „vector<…>” snippety nie mają sensu
  const snipItem = (sn) => ({ hint: (c) => { if (expandSnippet(ed, c, sn, from, c.getCursor()) && sn.openHints) setTimeout(() => showHints(ed, c, true), 30); } });

  if (python) {
    noSnip = pyHints(ed, cm, sym, { word, lw, ctxText, tctx, after, curPos: cm.indexFromPos(from), put, match, curLine: cur.line });
    for (const it of items.values()) if (it._cont) Object.assign(it, snipItem(it._cont));
  } else if (/(\.|->)$/.test(tctx)) {
    noSnip = true;
    // a. / a[i]. / p-> — metody i pola pasujące do typu
    const mm = /([A-Za-z_]\w*)((?:\s*\[[^[\]]*\])*)\s*(\.|->)$/.exec(tctx);
    let list = null;
    if (mm) {
      const vis = visibleVars(sym, cm.indexFromPos(from));
      const d = vis.get(mm[1]) || sym.vars.get(mm[1]);
      if (d) {
        let ty = declType(sym, vis, d);
        const nIdx = (mm[2].match(/\[/g) || []).length;
        for (let k = 0; k < nIdx; k++) { if (!(k === 0 && d.arr)) ty = elemType(sym, ty); }
        ty = resolveType(sym, ty);
        const head = headName(ty);
        if (nIdx === 0 && d.arr) list = null;
        else if (sym.structs.has(head)) list = sym.structs.get(head).map((f) => ({ t: f.name, kind: 'pole', det: f.type + (f.arr ? '[]' : '') }));
        else if (MEMBERS[KIND_ALIAS[head] || head]) list = MEMBERS[KIND_ALIAS[head] || head].split(' ').map((x) => ({ t: x, kind: head }));
      }
    }
    if (!list) list = MEMBER_WORDS.map((x) => ({ t: x, kind: 'C++' }));
    list.forEach((it, i) => {
      const name = it.t.replace(/\($|\(\)$/, '');
      if (!match(name)) return;
      if (it.t.endsWith('(')) {
        const sn = { trigger: name, name, scope: 'local', lang: 'cpp', code: `${name}(\${1})\${0}` };
        put(`${name}(…)`, 200 - i, it.kind, { ...snipItem(sn), _ctx: true });
      } else put(it.t, 200 - i, it.kind, { _detail: it.det, _ctx: true });
    });
  } else if (TEMPL_RE.test(ctxText) && !/^\s*#/.test(line)) {
    noSnip = true;
    // vector<|> — tylko typy
    const [, c, inside] = TEMPL_RE.exec(ctxText);
    const ll = sym.alias.has('ll') || sym.types.has('ll') ? 'll' : 'long long';
    const own = [...sym.structs.keys()].concat([...sym.alias.keys()].filter((a) => a !== 'll'));
    const base = ['int', ll, 'pair<int, int>', 'string', ...own, 'char', 'double', 'bool', 'vector<int>', 'long long'];
    let list = base;
    const firstArg = !inside.trim();
    if (c === 'pair' && firstArg) list = ['int, int', `${ll}, ${ll}`, `int, ${ll}`, ...base];
    else if (/map$/.test(c) && firstArg) list = ['int, int', 'string, int', `${ll}, ${ll}`, 'int, vector<int>', ...base];
    else if (c === 'priority_queue' && firstArg) list = ['int', `pair<${ll}, int>, vector<pair<${ll}, int>>, greater<>`, 'int, vector<int>, greater<int>', 'pair<int, int>', ...base];
    else if ((c === 'greater' || c === 'less') && firstArg) list = ['int', ll, 'pair<int, int>', ...base];
    list.forEach((t, i) => { if (match(t)) put(t, 300 - i, 'typ', { _ctx: true }); });
  } else {
    // nowa nazwa w deklaracji („int |”, „vector<int> |”, „P |”, „int a, |”) — nic nie podpowiadamy, żeby Tab nie wstawił czegoś obcego
    const prevTok = /(?:^|[^\w])([A-Za-z_]\w*)\s*[*&]*\s+$/.exec(ctxText);
    const isTypeWord = (w) => BASE_TYPES.has(w) || sym.types.has(w) || sym.alias.has(w) || ['long', 'unsigned', 'signed', 'short'].includes(w);
    const declPos = /\b(vector|set|multiset|map|multimap|unordered_map|unordered_set|queue|priority_queue|stack|deque|pair|tuple|array|bitset)\s*<[^;()]*>\s*[*&]*\s+$/.test(ctxText)
      || (prevTok && isTypeWord(prevTok[1]) && !/\b(return|else|case)\s*$/.test(ctxText))
      || /(^|[;{(]\s*)(const\s+)?(int|ll|long long|long|double|char|bool|string|auto|unsigned)\b[^;(){}=<>"']*,\s*$/.test(ctxText);
    if (auto && declPos) return null;
    const curPos = cm.indexFromPos(from);
    const vis = visibleVars(sym, curPos);
    const lineOf = (pos) => cm.posFromIndex(pos).line;
    const lastBefore = (n) => { const a = sym.occ.get(n) || []; for (let k = a.length - 1; k >= 0; k--) if (a[k] < curPos) return a[k]; return -1; };
    const prox = (n) => { const p = lastBefore(n); if (p < 0) return 0; const dl = cur.line - lineOf(p); return dl <= 2 ? 10 : dl <= 8 ? 6 : dl <= 25 ? 3 : 0; };
    const freq = (n) => Math.min(6, Math.log2(1 + (sym.occ.get(n) || []).length) * 2);
    const cinCtx = CIN_RE.test(ctxText);
    const coutCtx = /\b(cout|cerr)\b[^;]*<<\s*$/.test(ctxText);
    const forCond = /\bfor\s*\([^;]*;\s*[A-Za-z_]\w*\s*<=?\s*$/.test(ctxText);
    const indexCtx = /\[\s*$/.test(ctxText);
    const iterM = ITER_RE.exec(tctx);
    const isScalar = (d) => { const h0 = headName(resolveType(sym, declType(sym, vis, d))); return !d.arr && !CONTAINERS.has(h0) && h0 !== 'pair' && h0 !== 'tuple'; };
    // zmienne widoczne w tym miejscu (zasięg klamr / pętli / parametrów)
    let innerLoop = null;
    for (const d of vis.values()) if (d.loop && (!innerLoop || d.pos > innerLoop.pos)) innerLoop = d;
    for (const [n, d] of vis) {
      if (!match(n)) continue;
      let s = 64 + prox(n) + freq(n);
      if (coutCtx && (RESULT_RE.test(n) || (sym.assigned.has(n) && sym.assigned.get(n) < curPos && cur.line - lineOf(sym.assigned.get(n)) <= 15))) s += 20;
      if (forCond && !d.loop && isScalar(d)) s += 25 + (/^[nmkq]$/.test(n) ? 5 : 0);
      if (indexCtx && d.loop) s += 30 + (d === innerLoop ? 5 : 0);
      if (cinCtx && !isScalar(d)) s -= 30;
      put(n, s, 'zmienna', { _detail: declType(sym, vis, d) });
    }
    // po „cin >>”: najpierw jeszcze niewczytane zmienne w kolejności deklaracji, potem a[i] w pętli
    if (cinCtx) {
      let rank = 0;
      for (const d of [...vis.values()].sort((a, b) => a.pos - b.pos)) {
        const ty = resolveType(sym, declType(sym, vis, d));
        const h0 = headName(ty);
        if (d.loop || d.param && false) continue;
        if (isScalar(d)) { if (!sym.reads.has(d.name) && match(d.name)) put(d.name, 260 - rank++, 'zmienna', { _detail: ty, _ctx: true }); continue; }
        if (innerLoop && (d.arr || ['vector', 'deque', 'array'].includes(h0)) && match(d.name)) {
          put(`${d.name}[${innerLoop.name}]`, (sym.reads.has(d.name) ? 120 : 250) - rank++, 'zmienna', { _detail: ty, _ctx: true });
        }
      }
    }
    // sort( → a.begin(), a.end()
    if (iterM) {
      const suffix = ITER_FUNCS[iterM[1]];
      for (const d of vis.values()) {
        const h0 = headName(resolveType(sym, declType(sym, vis, d)));
        if (d.arr || !['vector', 'string', 'deque', 'array'].includes(h0) || !match(d.name)) continue;
        put(`${d.name}.begin(), ${d.name}.end()${suffix}`, 300 + prox(d.name), 'zakres', { _ctx: true });
      }
    }
    // zmienne zadeklarowane gdzie indziej (inna funkcja / blok) — tylko gdy wyraźnie wpisujesz nazwę
    if (lw.length >= 2) for (const d of sym.decls) if (!vis.has(d.name) && match(d.name) && !(d.open >= 0 && sym.structs.size && false)) put(d.name, d.pos > curPos && d.open < 0 && !(d.paren > d.open) ? 40 : 5, 'zmienna', { _detail: d.type });
    for (const [n, v] of sym.funcs) if (match(n)) put(n, (cinCtx ? 20 : 66) + prox(n), 'funkcja', { _detail: v.sig || '()' });
    for (const [n, c] of sym.used) {
      if (sym.vars.has(n) || sym.funcs.has(n) || sym.types.has(n) || sym.macros.has(n) || !match(n)) continue;
      put(n, (cinCtx ? 15 : 56) + Math.min(c, 10), 'funkcja', { _detail: 'użyta w kodzie' });
    }
    for (const [n] of sym.types) if (match(n)) put(n, cinCtx ? 10 : 52, 'typ');
    for (const [n, v] of sym.macros) if (match(n)) put(n, cinCtx ? 10 : 50, 'makro', { _detail: v.sig || '' });
    const info = loopInfo(sym, vis);
    for (const w of CPP_WORDS) {
      if (!match(w)) continue;
      const sn = contSnippet(w, ctxText, after, info);
      if (sn) put(sn.display, lw === w ? 3e6 : lw.length >= 3 ? 60 : 9, 'C++', snipItem(sn));
      else put(w, 8, 'C++');
    }
  }
  let list = [...items.values()];
  if (!word && auto) list = list.filter((it) => it._ctx);
  // snippety: dokładny trigger („seg”) na samą górę; sam prefiks — pod zmiennymi i funkcjami, najwyżej 3
  if (lw && snippetCache && !noSnip) {
    let partial = 0;
    for (const sn of snippetCache) {
      if (!sn.trigger.toLowerCase().startsWith(lw) || (sn.lang || 'cpp') !== (python ? 'python' : 'cpp')) continue;
      if (sn.trigger.toLowerCase() === lw) list.push({ text: sn.trigger, _score: 2e6, _kind: 'snippet', _snippet: sn });
      else if (lw.length >= 2 && partial++ < 3) list.push({ text: sn.trigger, _score: 30, _kind: 'snippet', _snippet: sn });
    }
  }
  list.sort((a, b) => b._score - a._score);
  const out = list.slice(0, 14).map((it) => ({
    text: it.text,
    render: (el) => {
      el.append(h('span', { class: 'h-text' }, it.text,
        it._detail && it._kind === 'funkcja' && it._detail.startsWith('(') ? h('span', { class: 'h-sig' }, it._detail.length > 34 ? it._detail.slice(0, 33) + '…)' : it._detail) : null));
      const kindTxt = it._snippet ? `⧉ ${it._snippet.name || 'snippet'}`
        : (it._kind === 'zmienna' || it._kind === 'pole') && it._detail ? (it._detail.length > 22 ? it._detail.slice(0, 21) + '…' : it._detail)
        : it._kind;
      el.append(h('span', { class: `h-kind k-${it._kind.split(' ')[0]}` }, kindTxt));
    },
    hint: it._snippet ? (c) => expandSnippet(ed, c, it._snippet, from, c.getCursor()) : it.hint,
  }));
  if (!out.length) return null;
  return { list: out, from, to: CMx.Pos(cur.line, cur.ch) };
}

function showHints(ed, cm, auto = false) {
  const CMx = CM();
  if (!CMx.showHint) return;
  CMx.showHint(cm, () => buildHints(ed, cm, auto), {
    completeSingle: false, alignWithWord: true, closeOnUnfocus: true,
    closeCharacters: /[\s()\[\]{};,]/,
  });
}

function attachAutocomplete(ed, cm) {
  cm.on('inputRead', (c, change) => {
    if (!acEnabled) return;
    if (change.origin !== '+input') return;
    const ch = change.text.join('');
    if (ch.length !== 1) return;
    if (ed.lang === 'text') {
      // tekst: słowa z tego pliku, od 3. litery (także polskie znaki)
      if (c.state.completionActive || !/\p{L}/u.test(ch)) return;
      const cur = c.getCursor();
      if (!/(^|[^\p{L}\p{N}_-])[\p{L}_][\p{L}\p{N}_-]{2,}$/u.test(c.getLine(cur.line).slice(0, cur.ch))) return;
      clearTimeout(ed._acTimer);
      ed._acTimer = setTimeout(() => { if (!c.state.completionActive) showHints(ed, c, true); }, 80);
      return;
    }
    if (c.state.completionActive) {                 // otwarta lista filtruje się sama; „ ” / „.” / „<” mogą otworzyć nową
      if (/[\w]/.test(ch)) return;
      c.state.completionActive.close();
    }
    const type = c.getTokenTypeAt(c.getCursor()) || '';
    if (/comment|string/.test(type)) return;
    const cur = c.getCursor();
    const before = c.getLine(cur.line).slice(0, cur.ch);
    let trigger = false;
    // jak w onlinegdb: od pierwszej litery nazwy (ale nie w środku liczby, np. 1e9 / 0x1f)
    if (/[A-Za-z_]/.test(ch)) trigger = /(^|[^\w])[A-Za-z_]\w*$/.test(before);
    else if (ch === '.' || (ch === '>' && before.endsWith('->'))) trigger = true;
    else if (ch === '<') trigger = ed.lang !== 'python' && !/^\s*#/.test(before) && TEMPL_RE.test(before);   // vector< → typy
    else if (ch === ' ') trigger = ed.lang === 'python' ? /^\s*(import|from\s+[\w.]+\s+import|from)\s$/.test(before) || /\bfor\s+[\w\s,]+\s+in\s$/.test(before)
      : CIN_RE.test(before);                                                                                 // cin >> → niewczytane zmienne
    else if (ch === '(') trigger = ed.lang !== 'python' && ITER_RE.test(before);                             // sort( → a.begin(), a.end()
    if (!trigger) return;
    clearTimeout(ed._acTimer);
    ed._acTimer = setTimeout(() => { if (c.state.completionActive && !/\w/.test(ch)) c.state.completionActive.close(); if (!c.state.completionActive) showHints(ed, c, true); }, 60);
  });
  // „(” i „<” wpisane przez autodomykanie nawiasów nie generują inputRead — łapiemy je z klawiatury
  cm.on('keyup', (c, e) => {
    if (!acEnabled || ed.lang === 'text' || ed.lang === 'python' || c.state.completionActive) return;
    if (e.key !== '(') return;
    const bf = c.getLine(c.getCursor().line).slice(0, c.getCursor().ch);
    if (!ITER_RE.test(bf)) return;
    const type = c.getTokenTypeAt(c.getCursor()) || '';
    if (/comment|string|meta/.test(type)) return;
    clearTimeout(ed._acTimer);
    ed._acTimer = setTimeout(() => { if (!c.state.completionActive) showHints(ed, c, true); }, 40);
  });
}

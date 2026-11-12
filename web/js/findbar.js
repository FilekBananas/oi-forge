// Pasek „Znajdź i zamień” w edytorze kodu (jak w VS Code): Ctrl/⌘+F otwiera, Ctrl+H albo ⌘+⌥+F od razu w polu
// „Zamień na”. Opcje: wielkość liter (Alt+C), całe słowa (Alt+W), wyrażenia regularne (Alt+R; w zamianie $1, $&, \n).
// Enter / Shift+Enter — następne / poprzednie, w polu zamiany: Enter — zamień, Ctrl/⌘+Enter — zamień wszystkie
// (jedno Ctrl+Z cofa całą zamianę). Wszystkie trafienia są podświetlone w kodzie i na pasku przewijania.
import { h, icon, toast } from './ui.js';
import { local } from './api.js';

const MAX_MATCHES = 20000;      // więcej nie liczymy (i tak nikt ich nie przejrzy)
const MAX_MARKS = 3000;         // tyle trafień podświetlamy w kodzie
const opts = Object.assign({ caseSensitive: false, word: false, regex: false, showReplace: true }, local.get('find:opts', {}) || {});
let lastQuery = '';
let lastReplace = '';

const saveOpts = () => local.set('find:opts', opts);
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const plural = (n, one, few, many) => {
  if (n === 1) return one;
  const d = n % 10, dd = n % 100;
  return d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many;
};

function buildRegex(q) {
  if (!q) return { re: null };
  let src = opts.regex ? q : escapeRe(q);
  if (opts.word) src = `(?<![A-Za-z0-9_])(?:${src})(?![A-Za-z0-9_])`;
  try { return { re: new RegExp(src, `gm${opts.caseSensitive ? '' : 'i'}`) }; } catch (e) { return { re: null, error: e.message }; }
}

// $& $1 $<nazwa> $$ oraz \n \t \\ w tekście zamiany (tylko w trybie wyrażeń regularnych)
function expand(rep, m) {
  if (!opts.regex) return rep;
  return rep.replace(/\$(\$|&|\d{1,2}|<[^>]*>)|\\([nt\\])/g, (all, d, esc) => {
    if (esc) return esc === 'n' ? '\n' : esc === 't' ? '\t' : '\\';
    if (d === '$') return '$';
    if (d === '&') return m[0];
    if (d[0] === '<') return m.groups?.[d.slice(1, -1)] ?? '';
    if (+d < m.length) return m[+d] ?? '';
    if (d.length === 2 && +d[0] < m.length) return (m[+d[0]] ?? '') + d[1];
    return all;
  });
}

const posEq = (a, b) => a.line === b.line && a.ch === b.ch;
const posLe = (a, b) => a.line < b.line || (a.line === b.line && a.ch <= b.ch);

/** Podłącza pasek do edytora z createEditor. Zwraca {open(replace), close(), next(), prev(), isOpen()}. */
export function attachFindBar(ed, host, { readOnly = false } = {}) {
  const cm = ed.cm;
  let open = false;
  let matches = [];             // [{from, to, m}]
  let marks = [];
  let curMark = null;
  let scrollAnn = null;
  let anchor = null;            // skąd szukamy podczas pisania
  let truncated = false;

  const findIn = h('input', { class: 'fr-input', type: 'text', placeholder: 'Znajdź', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Znajdź' });
  const repIn = h('input', { class: 'fr-input', type: 'text', placeholder: 'Zamień na', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Zamień na' });
  const toggleBtn = (label, key, title) => {
    const b = h('button', { type: 'button', class: 'fr-opt', title, 'aria-pressed': String(!!opts[key]),
      onmousedown: (e) => e.preventDefault(), onclick: () => flip(key) }, label);
    return b;
  };
  const caseBtn = toggleBtn('Aa', 'caseSensitive', 'Uwzględnij wielkość liter (Alt+C)');
  const wordBtn = toggleBtn(h('span', { class: 'fr-word' }, 'ab'), 'word', 'Całe słowa (Alt+W)');
  const reBtn = toggleBtn('.*', 'regex', 'Wyrażenie regularne (Alt+R) — w zamianie działa $1, $& i \\n');
  const count = h('span', { class: 'fr-count', 'aria-live': 'polite' });
  const navBtn = (ic, title, fn, cls = '') => h('button', { type: 'button', class: `fr-btn ${cls}`, title, onmousedown: (e) => e.preventDefault(), onclick: fn }, icon(ic, 15));
  const prevBtn = navBtn('chevron-down', 'Poprzednie (Shift+Enter)', () => step(-1), 'up');
  const nextBtn = navBtn('chevron-down', 'Następne (Enter)', () => step(1));
  const closeBtn = navBtn('x', 'Zamknij (Esc)', () => close());
  const expandBtn = h('button', { type: 'button', class: 'fr-expand', title: 'Pokaż / ukryj zamianę (Ctrl+H)',
    onmousedown: (e) => e.preventDefault(), onclick: () => setReplace(!opts.showReplace, true) }, icon('chevron-right', 14));
  const repOne = h('button', { type: 'button', class: 'fr-act', title: 'Zamień bieżące trafienie (Enter w polu zamiany)', onmousedown: (e) => e.preventDefault(), onclick: () => replaceOne() }, 'Zamień');
  const repAll = h('button', { type: 'button', class: 'fr-act', title: `Zamień wszystkie (${navigator.platform.toUpperCase().includes('MAC') ? '⌘' : 'Ctrl'}+Enter w polu zamiany)`,
    onmousedown: (e) => e.preventDefault(), onclick: () => replaceAll() }, 'Wszystkie');
  const repRow = h('div', { class: 'fr-row fr-rep' }, h('div', { class: 'fr-field' }, repIn), repOne, repAll);
  const bar = h('div', { class: `fr-bar${readOnly ? ' ro' : ''}`, hidden: true, role: 'search' },
    readOnly ? null : expandBtn,
    h('div', { class: 'fr-rows' },
      h('div', { class: 'fr-row' }, h('div', { class: 'fr-field' }, findIn, h('div', { class: 'fr-opts' }, caseBtn, wordBtn, reBtn)), count, prevBtn, nextBtn, closeBtn),
      readOnly ? null : repRow));
  host.append(bar);

  function flip(key) {
    opts[key] = !opts[key];
    saveOpts();
    syncOpts();
    refresh({ jump: true });
  }
  function syncOpts() {
    caseBtn.setAttribute('aria-pressed', String(opts.caseSensitive));
    wordBtn.setAttribute('aria-pressed', String(opts.word));
    reBtn.setAttribute('aria-pressed', String(opts.regex));
  }
  function setReplace(show, focus = false) {
    if (readOnly) show = false;
    opts.showReplace = show;
    saveOpts();
    repRow.hidden = !show;
    bar.classList.toggle('with-rep', show);
    if (focus) (show ? repIn : findIn).focus();
  }

  function clearMarks() {
    for (const m of marks) m.clear();
    marks = [];
    if (curMark) { curMark.clear(); curMark = null; }
    if (scrollAnn) scrollAnn.update([]);
  }
  function compute() {
    matches = [];
    truncated = false;
    const { re, error } = buildRegex(findIn.value);
    findIn.classList.toggle('bad', !!error);
    findIn.title = error ? `Błędne wyrażenie: ${error}` : '';
    if (!re) return;
    const text = cm.getValue();
    let m;
    while ((m = re.exec(text))) {
      if (!m[0].length) { re.lastIndex++; continue; }
      matches.push({ from: cm.posFromIndex(m.index), to: cm.posFromIndex(m.index + m[0].length), m });
      if (matches.length >= MAX_MATCHES) { truncated = true; break; }
    }
  }
  function currentIndex() {
    const a = cm.getCursor('from'), b = cm.getCursor('to');
    return matches.findIndex((x) => posEq(x.from, a) && posEq(x.to, b));
  }
  function paintCurrent() {
    if (curMark) { curMark.clear(); curMark = null; }
    const i = currentIndex();
    if (i >= 0) curMark = cm.markText(matches[i].from, matches[i].to, { className: 'cm-fr-current' });
    const n = matches.length;
    count.classList.toggle('none', !!findIn.value && !n);
    count.textContent = !findIn.value ? '' : !n ? 'Brak wyników'
      : i >= 0 ? `${i + 1} z ${n}${truncated ? '+' : ''}` : `${n}${truncated ? '+' : ''} ${plural(n, 'wynik', 'wyniki', 'wyników')}`;
    prevBtn.disabled = nextBtn.disabled = !n;
    repOne.disabled = repAll.disabled = !n;
  }
  function paint() {
    clearMarks();
    cm.operation(() => {
      for (const x of matches.slice(0, MAX_MARKS)) marks.push(cm.markText(x.from, x.to, { className: 'cm-fr-match' }));
      paintCurrent();
    });
    if (cm.annotateScrollbar) {
      if (!scrollAnn) scrollAnn = cm.annotateScrollbar({ className: 'cm-fr-scroll', listenForChanges: false });
      scrollAnn.update(matches.slice(0, MAX_MARKS).map((x) => ({ from: x.from, to: x.to })));
    }
  }
  function select(i) {
    const x = matches[i];
    if (!x) return;
    cm.setSelection(x.from, x.to, { scroll: false });
    cm.scrollIntoView({ from: x.from, to: x.to }, 70);
  }
  function refresh({ jump = false } = {}) {
    lastQuery = findIn.value;
    compute();
    if (jump && matches.length) {
      const from = anchor || cm.getCursor('from');
      let i = matches.findIndex((x) => posLe(from, x.from));
      if (i < 0) i = 0;
      select(i);
    }
    paint();
  }
  function step(dir) {
    if (!findIn.value) { if (open) findIn.focus(); else openBar(); return; }
    compute();
    if (!matches.length) { paint(); return; }
    const a = cm.getCursor('from'), b = cm.getCursor('to');
    let i;
    if (dir > 0) {
      i = matches.findIndex((x) => posLe(b, x.from) && !(posEq(x.from, a) && posEq(x.to, b)));
      if (i < 0) i = 0;
    } else {
      i = -1;
      for (let k = matches.length - 1; k >= 0; k--) if (posLe(matches[k].to, a) && !posEq(matches[k].from, a)) { i = k; break; }
      if (i < 0) i = matches.length - 1;
    }
    select(i);
    anchor = matches[i].from;
    paint();
  }
  function replaceOne() {
    if (readOnly || !findIn.value) return;
    compute();
    const i = currentIndex();
    if (i < 0) { step(1); return; }
    const x = matches[i];
    const text = expand(repIn.value, x.m);
    cm.replaceRange(text, x.from, x.to, '+replace');
    lastReplace = repIn.value;
    const after = cm.posFromIndex(cm.indexFromPos(x.from) + text.length);
    cm.setCursor(after);
    compute();
    const j = matches.findIndex((y) => posLe(after, y.from));
    if (j >= 0) select(j); else if (matches.length) select(0);
    if (matches.length) anchor = cm.getCursor('from');
    paint();
  }
  function replaceAll() {
    if (readOnly || !findIn.value) return;
    compute();
    const n = matches.length;
    if (!n) { paint(); return; }
    const rep = repIn.value;
    lastReplace = rep;
    cm.operation(() => {
      for (let k = n - 1; k >= 0; k--) cm.replaceRange(expand(rep, matches[k].m), matches[k].from, matches[k].to, '*replace');
    });
    refresh();
    toast(`Zamieniono ${n}${truncated ? '+' : ''} ${plural(n, 'wystąpienie', 'wystąpienia', 'wystąpień')} (Ctrl+Z cofa)`, 'success', { timeout: 1800 });
  }

  function openBar(replace = false) {
    const sel = cm.getSelection();
    if (sel && !sel.includes('\n') && sel.length <= 200) findIn.value = opts.regex ? escapeRe(sel) : sel;
    else if (!findIn.value && lastQuery) findIn.value = lastQuery;
    if (!repIn.value && lastReplace) repIn.value = lastReplace;
    anchor = cm.getCursor('from');
    if (!open) { open = true; bar.hidden = false; }
    syncOpts();
    setReplace(readOnly ? false : (replace || opts.showReplace));
    refresh({ jump: !!findIn.value && currentIndex() < 0 });
    const target = replace && !readOnly && findIn.value ? repIn : findIn;
    target.focus();
    target.select();
  }
  function close(focus = true) {
    if (!open) return;
    open = false;
    bar.hidden = true;
    clearMarks();
    if (focus) cm.focus();
  }

  findIn.addEventListener('input', () => refresh({ jump: true }));
  const mac = navigator.platform.toUpperCase().includes('MAC');
  const onKey = (e, inReplace) => {
    const mod = mac ? e.metaKey : e.ctrlKey;
    // obsłużone klawisze nie trafiają do skrótów widoku (np. Ctrl+Enter = uruchom w edytorze projektów)
    const stop = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === 'Escape') { stop(); close(); return; }
    if (e.altKey && !mod && ['KeyC', 'KeyW', 'KeyR'].includes(e.code)) {
      stop();
      flip({ KeyC: 'caseSensitive', KeyW: 'word', KeyR: 'regex' }[e.code]);
      return;
    }
    if ((mod || e.ctrlKey) && e.code === 'KeyF' && !e.shiftKey) { stop(); if (mac && e.altKey) setReplace(true, true); else { findIn.focus(); findIn.select(); } return; }
    if (e.ctrlKey && e.code === 'KeyH') { stop(); setReplace(inReplace ? !opts.showReplace : true, true); return; }
    if ((mod && e.code === 'KeyG') || e.key === 'F3') { stop(); step(e.shiftKey ? -1 : 1); return; }
    if (e.key === 'Enter' && !e.isComposing) {
      stop();
      if (inReplace) { if (mod || e.ctrlKey) replaceAll(); else replaceOne(); } else step(e.shiftKey ? -1 : 1);
    }
  };
  findIn.addEventListener('keydown', (e) => onKey(e, false));
  repIn.addEventListener('keydown', (e) => onKey(e, true));

  // trafienia nadążają za edycją kodu i za przełączaniem plików (swapDoc w edytorze projektów)
  let tmr = null;
  cm.on('changes', () => { if (!open) return; clearTimeout(tmr); tmr = setTimeout(() => { compute(); paint(); }, 120); });
  cm.on('swapDoc', () => { if (!open) return; compute(); paint(); });
  cm.on('cursorActivity', () => { if (open && document.activeElement !== findIn && document.activeElement !== repIn) paintCurrent(); });

  return { open: openBar, close, next: () => step(1), prev: () => step(-1), isOpen: () => open };
}

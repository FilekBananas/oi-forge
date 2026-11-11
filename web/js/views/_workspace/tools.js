// Edytor projektów — prawy panel „Narzędzia”: wszystkie funkcje OI Forge wywoływane NA MIEJSCU, przy kodzie
// (sprawdzarka, złożoność, objaśnienia, optymalizacja, upiększanie, Python→C++, testerka, testy brzegowe,
// auto-solver, objaśnienie treści, doradca algorytmów, asystent, debugger).
import { h, icon, button, iconButton, clear, toast, badge, verdictBadge, progressBar, markdown, codeBlock, callout,
  confirmDialog, fmtTime, spinner } from '../../ui.js';
import { isStarred, qualityNote } from '../../quality.js';
import { api } from '../../api.js';
import { ws, persist, baseName, dirName, joinPath, modKey } from './util.js';
// przyciski panelu → widok, którego ocenę jakości pokazujemy (★)
const TOOL_VIEW = { optimize: 'optimizer', beautify: 'beautifier', sanitize: 'checker' };

const SEV = {
  error: { tone: 'danger', icon: 'circle-alert', label: 'Błąd', mark: 'error' },
  warning: { tone: 'warn', icon: 'triangle-alert', label: 'Ostrzeżenie', mark: 'warn' },
  perf: { tone: 'info', icon: 'zap', label: 'Wydajność', mark: 'info' },
  style: { tone: 'neutral', icon: 'paintbrush', label: 'Styl', mark: 'hint' },
  info: { tone: 'neutral', icon: 'info', label: 'Info', mark: 'hint' },
};

const GROUPS = [
  { id: 'kod', label: 'Kod', items: [
    { id: 'checker', label: 'Sprawdź błędy', icon: 'bug', hint: 'Statyczna analiza: przepełnienia, wyjście poza tablicę, niezainicjowane zmienne…' },
    { id: 'complexity', label: 'Złożoność', icon: 'gauge', hint: 'Szacowana złożoność, hot-spoty i czy zmieści się w limicie' },
    { id: 'explain', label: 'Objaśnij kod', icon: 'book-open', hint: 'Co robi program — opis i notatki przy liniach' },
    { id: 'optimize', label: 'Optymalizuj', icon: 'zap', hint: 'Bezpieczne przyspieszenia (szybkie IO, endl, kopiowanie…)' },
    { id: 'beautify', label: 'Upiększ', icon: 'wand-sparkles', hint: 'Porządkuje kod: formatowanie, include, puste linie' },
    { id: 'py2cpp', label: 'Python → C++', icon: 'languages', hint: 'Tłumaczy bieżący plik .py na C++ (nowy plik obok)' },
  ] },
  { id: 'testy', label: 'Testy', items: [
    { id: 'tester', label: 'Testerka', icon: 'flask-conical', hint: 'rozw vs brute na losowych testach z gen.py (pliki z folderu zadania)' },
    { id: 'sanitize', label: 'Sanitizery', icon: 'microscope', hint: 'Uruchom z AddressSanitizer/UBSan na bieżącym wejściu' },
    { id: 'edgecases', label: 'Testy brzegowe', icon: 'target', hint: 'Przypadki brzegowe na podstawie tresc.md' },
  ] },
  { id: 'zadanie', label: 'Zadanie', items: [
    { id: 'taskexplain', label: 'Objaśnij treść', icon: 'file-text', hint: 'Treść „po ludzku”: dane, cel, ograniczenia, pułapki' },
    { id: 'suggest', label: 'Jaki algorytm?', icon: 'lightbulb', hint: 'Doradca algorytmów + podobne zadania z Twoich programów' },
    { id: 'autosolver', label: 'Auto-solver', icon: 'sparkles', hint: 'Próbuje ułożyć rozwiązanie z szablonów i Twoich kodów' },
  ] },
  { id: 'pomoc', label: 'Pomoc', items: [
    { id: 'assistant', label: 'Zapytaj', icon: 'message-square', hint: 'Asystent z lokalnej bazy wiedzy' },
    { id: 'debugger', label: 'Debuguj', icon: 'footprints', hint: 'Krok po kroku ze zmiennymi (otwiera Debugger)' },
  ] },
];

/** tresc.md (Markdown) → zwykły tekst treści, jaki rozumieją parsery treści (nagłówki, bloki ``` i ** znikają). */
export function mdToStatement(md) {
  const out = [];
  for (let ln of String(md || '').replace(/\r\n?/g, '\n').split('\n')) {
    if (/^\s*```/.test(ln)) continue;
    ln = ln.replace(/^\s{0,3}#{1,6}\s*/, '').replace(/^\s*>\s?/, '').replace(/\*\*|__|`/g, '');
    out.push(ln);
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

/** app: {ctx, ed, active(), code(), setCode(text), openFile(path), goto(path, line), saveAll(), reloadTree(), bottom, toggleTools(open)} */
export function createTools(app) {
  let current = null;           // id narzędzia, którego wynik jest pokazany
  let running = null;           // {cancel} dla zadań w tle
  let markedPath = null;
  const btns = new Map();

  const grid = h('div', { class: 'ws-tools-groups' });
  for (const g of GROUPS) {
    grid.append(h('div', { class: 'ws-tg' }, h('div', { class: 'ws-tg-title' }, g.label),
      h('div', { class: 'ws-tg-items' }, g.items.map((it) => {
        const vid = TOOL_VIEW[it.id] || it.id;
        const star = isStarred(vid);
        const b = h('button', { type: 'button', class: 'ws-tool', title: `${it.hint}${qualityNote(vid) ? '\n' + (star ? '★ ' : 'Podpowiedź: ') + qualityNote(vid) : ''}`, onclick: () => runTool(it.id) },
          icon(it.icon, 15), h('span', null, it.label), star ? h('span', { class: 'q-star' }, '★') : null);
        btns.set(it.id, b);
        return b;
      }))));
  }
  const resHead = h('div', { class: 'ws-res-head' });
  const resBody = h('div', { class: 'ws-res-body' });
  const result = h('div', { class: 'ws-res', hidden: true }, resHead, resBody);
  const intro = h('div', { class: 'ws-tools-intro muted small' }, 'Wybierz narzędzie — wynik pojawi się tutaj, a uwagi zaznaczą się przy liniach kodu.');
  const allLink = h('button', { type: 'button', class: 'ws-linkbtn ws-all-tools', onclick: () => app.ctx.navigate('home') },
    'Wszystkie narzędzia', icon('arrow-right', 12));
  const scroll = h('div', { class: 'ws-tools-scroll' }, grid, allLink, intro, result);
  const el = h('div', { class: 'ws-tools-inner' },
    h('div', { class: 'ws-tools-head' }, icon('blocks', 15), h('span', { class: 'grow' }, 'Narzędzia'),
      iconButton('x', { title: 'Zwiń panel narzędzi', onClick: () => app.toggleTools(false) })),
    scroll);

  // ---------------------------------------------------------------- pomocnicze
  function file() {
    const f = app.active();
    if (!f) { toast('Najpierw otwórz plik.', 'info'); return null; }
    return f;
  }
  function needCode(f) {
    const code = app.code();
    if (!code.trim()) { toast('Plik jest pusty.', 'info'); return null; }
    if (f.lang !== 'cpp' && f.lang !== 'python') { toast('To narzędzie działa na plikach C++ (.cpp) i Pythona (.py).', 'info'); return null; }
    return code;
  }
  function start(id, title, sub) {
    if (running) { running.cancel?.(); running = null; }
    current = id;
    btns.forEach((b, k) => b.classList.toggle('active', k === id));
    intro.hidden = true;
    result.hidden = false;
    clear(resHead); clear(resBody);
    const it = GROUPS.flatMap((g) => g.items).find((x) => x.id === id);
    resHead.append(icon(it?.icon || 'blocks', 15), h('div', { class: 'grow' }, h('div', { class: 'ws-res-title' }, title || it?.label), sub ? h('div', { class: 'ws-res-sub' }, sub) : null),
      iconButton('x', { title: 'Zamknij wynik i wyczyść znaczniki', onClick: () => closeResult() }));
    resBody.append(h('div', { class: 'ws-res-loading' }, spinner(16), h('span', { class: 'muted small' }, 'Pracuję…')));
    requestAnimationFrame(() => { scroll.scrollTop = grid.offsetHeight - 8; });
    return resBody;
  }
  function done(...nodes) { clear(resBody); resBody.append(...nodes); }
  function fail(e) {
    if (e?.cancelled) { done(h('div', { class: 'muted small' }, 'Zatrzymano.')); return; }
    const msg = e?.status === 404 ? 'Ten moduł nie jest dostępny w tej instalacji.' : (e?.message || String(e));
    done(callout(msg, 'danger'));
  }
  function closeResult() {
    if (running) { running.cancel?.(); running = null; }
    current = null;
    result.hidden = true;
    intro.hidden = false;
    btns.forEach((b) => b.classList.remove('active'));
    clearMarks();
  }
  function mark(path, items) {
    const f = app.active();
    if (!f || f.path !== path) return;
    app.ed.markLines(items.filter((x) => x.line > 0));
    markedPath = path;
  }
  function clearMarks() { if (markedPath && app.active()?.path === markedPath) app.ed.clearMarks(); markedPath = null; }
  const lineLink = (path, line, label) => (line > 0 ? h('button', { type: 'button', class: 'ws-ln', title: 'Przejdź do linii', onclick: () => app.goto(path, line) }, label || `linia ${line}`) : null);
  const sec = (title, ...children) => h('div', { class: 'ws-sec' }, h('div', { class: 'ws-sec-title' }, title), children);
  const more = (view, params, label = 'Otwórz pełny widok') => h('button', { type: 'button', class: 'ws-linkbtn ws-more-link', onclick: () => app.ctx.navigate(view, params) }, label, ' ', icon('external-link', 12));
  const pre = (text, cls = '') => h('pre', { class: ['ws-pre', cls].join(' ') }, text || h('span', { class: 'faint' }, '(puste)'));

  async function taskCtx(withContent = true) {
    const f = app.active();
    if (!f) return null;
    try { return await ws.task(f.path, withContent); } catch { return null; }
  }
  function isTemplateStatement(s) {
    return !s || !s.trim() || (s.includes('Wklej tutaj treść zadania') && s.length < 600);
  }
  async function statementOf() {
    await app.saveAll();
    const t = await taskCtx(true);
    const st = t?.statement?.content || '';
    if (isTemplateStatement(st) && t && t.dir !== undefined) {
      // pusta tresc.md (sam szablon) — treść wklejasz od razu tutaj: zapisujemy ją w pliku i uruchamiamy narzędzie
      const toolId = current;
      const ta = h('textarea', { class: 'textarea ws-paste', rows: 9, spellcheck: 'false',
        placeholder: 'Wklej tu treść zadania (z PDF, SIO2 albo własnymi słowami) — najlepiej razem z przykładem wejścia i wyjścia.' });
      const go = async () => {
        const text = ta.value.trim();
        if (!text) { toast('Najpierw wklej treść zadania.', 'info'); ta.focus(); return; }
        try {
          if (t.statement?.path) {
            await ws.save({ path: t.statement.path, content: text + '\n', force: true });
            app.dropFile?.(t.statement.path);
          } else {
            await ws.create({ path: joinPath(t.dir, 'tresc.md'), kind: 'file', content: text + '\n' });
            await app.reloadTree();
          }
        } catch (e) { toast(e.message || String(e), 'error'); return; }
        toast('Zapisano treść w tresc.md.', 'success');
        runTool(toolId);
      };
      ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); e.stopPropagation(); go(); } });
      done(callout('W **tresc.md** tego zadania nie ma jeszcze treści. Wklej ją poniżej — zapiszę ją w pliku i od razu uruchomię narzędzie.', 'info'),
        ta,
        h('div', { class: 'row gap-s' },
          button('Zapisz w tresc.md i uruchom', { icon: 'play', size: 'sm', variant: 'primary', onClick: go }),
          h('span', { class: 'muted tiny grow' }, `${modKey()}+Enter`),
          t.statement ? button('Otwórz tresc.md', { icon: 'file-text', size: 'sm', variant: 'ghost', onClick: () => app.openFile(t.statement.path) }) : null));
      requestAnimationFrame(() => ta.focus());
      return null;
    }
    if (isTemplateStatement(st)) {
      done(callout('Brak treści zadania. Wklej treść do pliku **tresc.md** w folderze zadania (albo utwórz zadanie przyciskiem „Nowe zadanie” i wklej ją tam).', 'info'),
        t?.statement ? button('Otwórz tresc.md', { icon: 'file-text', size: 'sm', onClick: () => app.openFile(t.statement.path) })
          : (t && t.dir !== undefined ? button('Utwórz tresc.md', { icon: 'plus', size: 'sm', onClick: async () => {
            const r = await ws.create({ path: joinPath(t.dir, 'tresc.md'), kind: 'file' }); await app.reloadTree(); app.openFile(r.path);
          } }) : null));
      return null;
    }
    return { task: t, statement: mdToStatement(st), raw: st };
  }
  async function saveAsFile(dir, name, content, { open = true, overwriteAsk = true } = {}) {
    const path = joinPath(dir, name);
    let r;
    try {
      r = await api.post('/api/ws/create', { path, kind: 'file', content }, { quiet: true });
    } catch (e) {
      if (e.status === 409 && overwriteAsk) {
        const ok = await confirmDialog(`Plik „${name}” już istnieje. Nadpisać go? (Stara wersja trafi do kosza.)`, { title: 'Plik istnieje', ok: 'Nadpisz', danger: true });
        if (ok) {
          await ws.del(path);
          r = await ws.create({ path, kind: 'file', content });
          app.dropFile?.(path);
        } else r = await ws.create({ path, kind: 'file', content, unique: true });
      } else { toast(e.message, 'error'); return null; }
    }
    await app.reloadTree();
    if (open) app.openFile(r.path);
    toast(`Zapisano ${r.path}`, 'success', { timeout: 1600 });
    return r;
  }
  const copyText = async (text) => { await navigator.clipboard.writeText(text).catch(() => {}); toast('Skopiowano', 'success', { timeout: 1000 }); };
  const codeActions = (s, name, code) => h('div', { class: 'row gap-s' },
    button(`Zapisz jako ${name}`, { icon: 'save', size: 'sm', variant: 'primary', onClick: () => saveAsFile(s.task.dir, name, code) }),
    button('Kopiuj', { icon: 'copy', size: 'sm', variant: 'ghost', onClick: () => copyText(code) }));
  // auto-solver: inne programy zgodne z przykładami, ale różne na losowym wejściu
  const competitors = (r) => ((r.competitors || []).length ? sec('Inne programy też pasują do przykładów',
    h('div', { class: 'muted tiny' }, 'Przykłady ich nie rozróżniają — sprawdź, który liczy to, o co pyta treść.'),
    ...r.competitors.slice(0, 4).map((c) => h('details', { class: 'ws-det' }, h('summary', null, c.desc || 'kandydat'),
      c.input ? h('div', { class: 'small' }, h('div', { class: 'muted' }, 'Różnią się np. na wejściu:'), pre(c.input.trim()),
        h('div', null, 'wybrany: ', h('code', null, c.best_output || '—'), ' · ten: ', h('code', null, c.output || '—'))) : null,
      c.code ? codeBlock(c.code, 'cpp', { maxHeight: 260 }) : null))) : null);
  // gotowe wczytywanie danych z formatu treści — wyraźnie opisane, że to nie rozwiązanie
  const readerBlock = (s, r) => (r.reader ? sec('Gotowe wczytywanie danych',
    h('div', { class: 'muted tiny' }, 'To nie jest rozwiązanie — tylko wczytuje dane w formacie z treści.'),
    h('div', { class: 'row gap-s' },
      button('Zapisz jako wczytywanie.cpp', { icon: 'save', size: 'sm', onClick: () => saveAsFile(s.task.dir, 'wczytywanie.cpp', r.reader) }),
      button('Kopiuj', { icon: 'copy', size: 'sm', variant: 'ghost', onClick: () => copyText(r.reader) })),
    codeBlock(r.reader, 'cpp', { maxHeight: 300 })) : null);

  // ---------------------------------------------------------------- narzędzia
  const TOOLS = {
    // ---------------------------------- Sprawdź błędy
    async checker() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      start('checker', 'Sprawdź błędy', f.name);
      try {
        const r = await api.post('/api/checker/analyze', { code }, { quiet: true });
        renderIssues(f, r.issues || [], r.stats, r.note);
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Sanitizery (dynamicznie)
    async sanitize() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      if (f.lang !== 'cpp') { toast('Sanitizery działają dla C++.', 'info'); return; }
      start('sanitize', 'Uruchom z sanitizerami', `${f.name} · wejście z dolnego panelu`);
      const pb = progressBar();
      pb.indeterminate('Kompiluję z ASan/UBSan…');
      done(pb.el);
      const ctl = api.job('/api/checker/run', { code, inputs: [app.bottom.input || ''] }, { quiet: true, onProgress: (p, m) => pb.set(p, m) });
      running = ctl;
      try {
        const r = await ctl.promise;
        running = null;
        const s = r.summary || {};
        const head = h('div', { class: 'row gap-s' }, s.health ? badge(s.health.sure ? s.health.label : `${s.health.score}/100 · ${s.health.label}`, s.health.tone) : null,
          s.verdict ? h('span', { class: 'small' }, s.verdict) : null, s.sanitizers === false ? badge('bez sanitizerów', 'warn') : null);
        renderIssues(f, r.issues || [], null, null, head, (r.notes || []).join('\n\n'));
      } catch (e) { running = null; fail(e); }
    },

    // ---------------------------------- Złożoność
    async complexity() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      start('complexity', 'Złożoność', f.name);
      const t = await taskCtx(true);
      const st = t?.statement?.content;
      try {
        const r = await api.post('/api/complexity/analyze', { code, statement: isTemplateStatement(st) ? undefined : mdToStatement(st) }, { quiet: true });
        mark(f.path, r.line_marks || []);
        const tone = { ok: 'success', warn: 'warn', tle: 'danger' }[r.time?.verdict] || 'neutral';
        const nodes = [
          h('div', { class: 'ws-big' }, h('span', { class: 'ws-big-val mono' }, r.complexity || '?'),
            r.time?.label ? badge(r.time.label, tone) : null),
          r.time?.explanation ? h('div', { class: 'small' }, markdown(r.time.explanation)) : null,
          h('div', { class: 'ws-kv' },
            r.time?.ops_fmt ? [h('span', null, 'operacji'), h('b', null, r.time.ops_fmt)] : null,
            r.time?.budget_fmt ? [h('span', null, 'budżet'), h('b', null, r.time.budget_fmt)] : null,
            r.memory?.total_mb !== undefined ? [h('span', null, 'pamięć'), h('b', { class: r.memory.verdict === 'mle' ? 'tone-danger' : '' }, `${(+r.memory.total_mb).toFixed(1)} MB`)] : null,
            r.confidence !== undefined ? [h('span', null, 'pewność'), h('b', null, `${Math.round(r.confidence * 100)}%`)] : null),
          (r.assumptions || []).length ? sec('Założenia', h('ul', { class: 'ws-ul' }, r.assumptions.map((a) => h('li', null, markdown(a))))) : null,
          (r.hotspots || []).length ? sec('Najgorętsze miejsca', h('div', { class: 'ws-list' }, r.hotspots.slice(0, 8).map((x) =>
            h('div', { class: 'ws-hot' }, lineLink(f.path, x.line), h('code', { class: 'ws-hot-code' }, (x.code || '').trim()),
              h('span', { class: 'ws-hot-ops mono' }, x.ops_fmt || `O(${x.expr})`),
              h('span', { class: 'ws-share' }, h('i', { style: { width: `${Math.round((x.share || 0) * 100)}%` } })))))) : null,
          (r.warnings || []).length ? sec('Uwagi', h('div', { class: 'ws-list' }, r.warnings.slice(0, 8).map((w) =>
            h('div', { class: 'ws-note' }, lineLink(f.path, w.line), h('div', { class: 'grow small' }, markdown(w.message || '')))))) : null,
          (r.algorithms || []).length ? sec('Rozpoznane algorytmy', h('div', { class: 'row gap-s' }, r.algorithms.slice(0, 8).map((a) => badge(a.name, 'accent')))) : null,
          more('complexity', { code }, 'Pomiar empiryczny i drzewo pętli'),
        ];
        done(...nodes);
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Objaśnij kod
    async explain() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      start('explain', 'Objaśnij kod', f.name);
      try {
        const r = await api.post('/api/explain', { code }, { quiet: true });
        mark(f.path, (r.line_notes || []).map((n) => ({ line: n.line, type: 'hint', message: n.note })));
        done(
          r.overview_md ? h('div', { class: 'md small' }, markdown(r.overview_md)) : null,
          (r.algorithms || []).length ? h('div', { class: 'row gap-s' }, r.algorithms.slice(0, 8).map((a) => badge(a.name, 'accent'))) : null,
          ...(r.sections || []).map((s, i) => h('details', { class: 'ws-det', open: i < 2 }, h('summary', null, s.title), h('div', { class: 'md small' }, markdown(s.md || '')))),
          (r.line_notes || []).length ? h('details', { class: 'ws-det' }, h('summary', null, `Linia po linii (${r.line_notes.length})`),
            h('div', { class: 'ws-list' }, r.line_notes.map((n) => h('div', { class: 'ws-note' }, lineLink(f.path, n.line), h('div', { class: 'grow small md' }, markdown(n.note)))))) : null);
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Optymalizuj
    async optimize() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      if (f.lang !== 'cpp') { toast('Optymalizator działa dla C++.', 'info'); return; }
      start('optimize', 'Optymalizuj', f.name);
      try {
        const r = await api.post('/api/optimize', { code, apply: [] }, { quiet: true });
        const changes = r.changes || [];
        mark(f.path, changes.map((c) => ({ line: c.line, type: c.has_edit ? (c.safe ? 'info' : 'warn') : 'hint', message: `${c.title}: ${c.why}` })));
        if (!changes.length) { done(callout('Nie widzę nic do przyspieszenia — kod wygląda dobrze. 👍', 'tip')); return; }
        const chosen = new Set(changes.filter((c) => c.has_edit && c.safe).map((c) => `${c.rule}:${c.line}`));
        const applyBtn = button('Zastosuj zaznaczone', { icon: 'check', size: 'sm', variant: 'primary', onClick: async () => {
          if (!chosen.size) { toast('Zaznacz zmiany do zastosowania.', 'info'); return; }
          const rr = await api.post('/api/optimize', { code: app.code(), apply: [...chosen] });
          if (rr.optimized_code !== undefined && rr.optimized_code !== app.code()) {
            app.setCode(rr.optimized_code);
            toast(`Zastosowano ${(rr.applied || []).length || chosen.size} zmian (Ctrl+Z cofa).`, 'success');
            TOOLS.optimize();
          } else toast('Brak zmian do zastosowania.', 'info');
        } });
        done(h('div', { class: 'row gap-s' }, applyBtn, h('span', { class: 'muted small' }, `${changes.length} ${changes.length === 1 ? 'sugestia' : 'sugestii'}`)),
          h('div', { class: 'ws-list' }, changes.map((c) => {
            const k = `${c.rule}:${c.line}`;
            const cb = c.has_edit ? h('input', { type: 'checkbox', checked: chosen.has(k), onchange: (e) => { if (e.target.checked) chosen.add(k); else chosen.delete(k); } }) : null;
            return h('div', { class: 'ws-card' },
              h('div', { class: 'ws-card-head' }, cb, h('b', { class: 'grow' }, c.title), lineLink(f.path, c.line),
                c.has_edit ? badge(c.safe ? 'bezpieczne' : 'sprawdź', c.safe ? 'success' : 'warn') : badge('rada', 'neutral')),
              c.gain ? h('div', { class: 'small' }, badge(c.gain, c.category === 'algorytm' ? 'accent' : 'neutral')) : null,
              c.why ? h('div', { class: 'small muted' }, c.why) : null,
              c.before || c.after ? h('div', { class: 'ws-ba' }, c.before ? h('code', { class: 'del' }, c.before) : null, c.after ? h('code', { class: 'add' }, c.after) : null) : null,
              c.speedup_hint ? h('div', { class: 'tiny tone-success' }, c.speedup_hint) : null,
              c.suggestion_md ? h('details', { class: 'ws-det', open: !c.has_edit }, h('summary', null, 'Wyjaśnienie'), h('div', { class: 'md small' }, markdown(c.suggestion_md))) : null);
          })));
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Upiększ
    async beautify() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      if (f.lang !== 'cpp') { toast('Upiększanie działa dla C++.', 'info'); return; }
      start('beautify', 'Upiększ', f.name);
      try {
        const r = await api.post('/api/beautify', { code }, { quiet: true });
        if (!r.changed) { done(callout('Kod już jest uporządkowany — nic do zmiany.', 'tip'), more('beautifier', { code }, 'Więcej opcji upiększacza')); return; }
        app.setCode(r.code);
        done(callout(`Upiększono kod (${r.stats ? `+${r.stats.added} / −${r.stats.removed} linii` : 'zmiany zastosowane'}). Możesz cofnąć: ${navigator.platform.toUpperCase().includes('MAC') ? '⌘' : 'Ctrl'}+Z.`, 'tip'),
          (r.changes || []).length ? h('div', { class: 'row gap-s' }, r.changes.map((c) => badge(`${c.label}${c.count > 1 ? ` ×${c.count}` : ''}`, 'accent'))) : null,
          ...(r.warnings || []).map((w) => callout(w, 'warn')),
          h('div', { class: 'row gap-s' }, button('Cofnij', { icon: 'rotate-ccw', size: 'sm', onClick: () => { app.ed.cm.undo(); } }), more('beautifier', { code }, 'Więcej opcji upiększacza')));
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Python → C++
    async py2cpp() {
      const f = file(); if (!f) return;
      if (f.lang !== 'python') { toast('Otwórz plik .py — przetłumaczę go na C++.', 'info'); return; }
      const code = needCode(f); if (!code) return;
      start('py2cpp', 'Python → C++', f.name);
      try {
        const r = await api.post('/api/py2cpp/translate', { code }, { quiet: true });
        const warns = (r.warnings || []).filter((w) => w.severity !== 'info' || w.line);
        mark(f.path, warns.filter((w) => w.severity !== 'info').map((w) => ({ line: w.line, type: w.severity === 'error' ? 'error' : 'warn', message: w.message })));
        if (!r.cpp) { done(callout(r.error || 'Nie udało się przetłumaczyć (błąd składni?).', 'danger'), ...warns.map((w) => warnRow(f, w))); return; }
        const name = baseName(f.path).replace(/\.pyw?$/i, '') + '.cpp';
        done(h('div', { class: 'row gap-s' },
          button(`Zapisz jako ${name}`, { icon: 'save', size: 'sm', variant: 'primary', onClick: () => saveAsFile(dirName(f.path), name, r.cpp) }),
          more('py2cpp', { code }, 'Weryfikacja na testach')),
        warns.length ? sec(`Uwagi (${warns.length})`, h('div', { class: 'ws-list' }, warns.slice(0, 20).map((w) => warnRow(f, w)))) : null,
        codeBlock(r.cpp, 'cpp', { maxHeight: 420 }));
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Testerka
    async tester() {
      const f = file(); if (!f) return;
      await app.saveAll();
      start('tester', 'Testerka', 'rozw vs brute na testach z generatora');
      const t = await taskCtx(true);
      if (!t) { fail(new Error('Nie udało się odczytać folderu zadania.')); return; }
      const missing = [];
      if (!t.solution?.content?.trim()) missing.push(['solution', 'rozw.cpp', 'cpp']);
      if (!t.brute?.content?.trim()) missing.push(['brute', 'brute.cpp', 'brute']);
      if (!t.generator?.content?.trim()) missing.push(['generator', 'gen.py', 'gen']);
      const roles = h('div', { class: 'ws-roles' }, ['solution', 'brute', 'generator'].map((r) => h('div', { class: 'ws-role' },
        h('span', { class: 'muted tiny' }, { solution: 'Rozwiązanie', brute: 'Brute', generator: 'Generator' }[r]),
        t[r] ? h('button', { type: 'button', class: 'ws-linkbtn mono', onclick: () => app.openFile(t[r].path) }, baseName(t[r].path)) : h('span', { class: 'tone-warn small' }, 'brak'))));
      if (missing.length) {
        done(roles, callout(`Testerka potrzebuje w folderze zadania plików: ${missing.map((m) => `**${m[1]}**`).join(', ')}. Brute = wolne, ale na pewno poprawne rozwiązanie; generator wypisuje wiele małych testów oddzielonych pustą linią.`, 'info'),
          h('div', { class: 'row gap-s' }, missing.filter((m) => m[0] !== 'solution').map((m) => button(`Utwórz ${m[1]}`, { icon: 'plus', size: 'sm', onClick: async () => {
            const r = await ws.create({ path: joinPath(t.dir, m[1]), kind: 'file', template: m[2] }); await app.reloadTree(); app.openFile(r.path);
          } }))));
        return;
      }
      const count = persist.get('testerCount', 1000);
      const sel = h('select', { class: 'select sm', style: 'width:auto', onchange: () => persist.set('testerCount', +sel.value) },
        [100, 500, 1000, 5000, 20000].map((n) => h('option', { value: n, selected: n === count }, `${n} testów`)));
      const pb = progressBar();
      const stats = h('div', { class: 'ws-tstats' });
      const failBox = h('div', { class: 'stack gap-s' });
      const log = h('div', { class: 'ws-tlog' });
      const stopBtn = button('Zatrzymaj', { icon: 'circle-stop', size: 'sm', variant: 'ghost', onClick: () => running?.cancel() });
      const runBtn = button('Start', { icon: 'play', size: 'sm', variant: 'primary', onClick: () => go() });
      done(roles, h('div', { class: 'row gap-s' }, runBtn, sel, stopBtn), pb.el, stats, log, failBox);
      stopBtn.hidden = true;
      pb.el.hidden = true;
      let best = null;
      async function go() {
        const tt = await taskCtx(true);
        best = null;
        clear(failBox); clear(log); clear(stats);
        pb.el.hidden = false; pb.set(0, 'Kompiluję…');
        runBtn.hidden = true; stopBtn.hidden = false;
        const body = {
          solution: tt.solution.content, brute: tt.brute.content, generator: tt.generator.content,
          langs: { solution: tt.solution.lang, brute: tt.brute.lang, generator: tt.generator.lang },
          tests_per_run: +sel.value, stop_on_first: true, shrink: true,
        };
        const ctl = api.job('/api/tester/start', body, {
          quiet: true,
          onProgress: (p, m) => pb.set(p, m),
          onEvent: (ev) => {
            if (ev.type === 'stats') {
              const c = ev.counts || {};
              stats.replaceChildren(h('b', null, `${ev.done}/${ev.total}`),
                ...Object.entries(c).filter(([, n]) => n).map(([k, n]) => h('span', { class: 'ws-cnt' }, verdictBadge(k), ` ${n}`)),
                h('span', { class: 'muted tiny' }, `rozw max ${fmtTime(ev.sol_max)} · brute max ${fmtTime(ev.brute_max)}`));
            } else if (ev.type === 'compile_error') {
              failBox.append(callout(`**Błąd kompilacji: ${ev.name || ev.which}**` + (ev.explanations?.[0] ? ` — ${ev.explanations[0].title}` : ''), 'danger'), pre(ev.stderr, 'bad'));
            } else if (ev.type === 'failure' || ev.type === 'minimized' || ev.type === 'shrunk') {
              best = ev;
              showFailure(ev, tt);
            } else if (ev.type === 'warning' || ev.type === 'hint' || ev.type === 'generator_error') {
              log.append(h('div', { class: 'small tone-warn' }, '⚠ ', ev.message));
            }
          },
        });
        running = ctl;
        try {
          const r = await ctl.promise;
          pb.el.hidden = true;
          const ok = r.status === 'OK';
          failBox.prepend(callout(r.message || (ok ? 'Wszystkie testy zgodne z brute.' : `Wynik: ${r.status}`), ok ? 'tip' : (r.status === 'CE' || r.status === 'ERROR' ? 'danger' : 'warn')));
          if (!best && r.first_failure) showFailure(r.shrunk || r.minimized || r.first_failure, tt);
        } catch (e) { if (!e.cancelled) failBox.append(callout(e.message, 'danger')); else failBox.append(h('div', { class: 'muted small' }, 'Zatrzymano.')); pb.el.hidden = true; }
        finally { running = null; runBtn.hidden = false; stopBtn.hidden = true; }
      }
      function showFailure(ev, tt) {
        clear(failBox);
        const label = { failure: 'Kontrprzykład', minimized: 'Kontrprzykład (zmniejszony)', shrunk: 'Kontrprzykład (najmniejszy)' }[ev.type] || 'Kontrprzykład';
        failBox.append(h('div', { class: 'ws-card bad' },
          h('div', { class: 'ws-card-head' }, verdictBadge(ev.verdict), h('b', { class: 'grow' }, label), ev.size ? h('span', { class: 'muted tiny' }, `${ev.size} B`) : null),
          ev.message ? h('div', { class: 'small' }, markdown(ev.message)) : null,
          h('div', { class: 'ws-td-label' }, 'Wejście'), pre(ev.input),
          h('div', { class: 'ws-two' },
            h('div', null, h('div', { class: 'ws-td-label' }, 'Brute (oczekiwane)'), pre(ev.expected)),
            h('div', null, h('div', { class: 'ws-td-label' }, 'Twoje wyjście'), pre(ev.output, 'bad'))),
          ev.first?.message ? h('div', { class: 'small tone-danger' }, ev.first.message) : null,
          h('div', { class: 'row gap-s' },
            button('Zapisz jako test', { icon: 'save', size: 'sm', variant: 'primary', onClick: async () => {
              const r = await ws.saveTest({ dir: tt.dir || app.active().path, input: ev.input, output: ev.expected ?? null });
              toast(`Zapisano test ${r.number} (${r.in}).`, 'success', { timeout: 1800 });
              app.reloadTree(); app.bottom.refreshTests(true);
            } }),
            button('Użyj jako wejście', { icon: 'file-input', size: 'sm', onClick: () => app.bottom.setCustomInput(ev.input) }),
            button('Debuguj', { icon: 'footprints', size: 'sm', variant: 'ghost', onClick: () => app.ctx.navigate('debugger', { code: tt.solution.content, lang: tt.solution.lang, input: ev.input, run: true }) }))));
      }
      go();
    },

    // ---------------------------------- Testy brzegowe
    async edgecases() {
      if (!file()) return;
      start('edgecases', 'Testy brzegowe', 'z treści zadania (tresc.md)');
      const s = await statementOf(); if (!s) return;
      const sol = s.task.solution?.content;
      try {
        const r = await api.post('/api/edgecases', { statement: s.statement, code: sol && sol.trim() ? sol : undefined,
          brute: s.task.brute?.content?.trim() ? s.task.brute.content : undefined }, { quiet: true });
        const runs = new Map((r.run?.results || []).map((x) => [x.id, x]));
        const tests = r.tests || [];
        const saveOne = async (tc) => {
          const rr = runs.get(tc.id);
          const outText = rr && rr.expected !== undefined ? rr.expected : null;
          const res = await ws.saveTest({ dir: s.task.dir || app.active().path, input: tc.input, output: outText });
          return res;
        };
        done(
          r.title ? h('div', { class: 'small muted' }, r.title) : null,
          r.run?.compile && r.run.compile.ok === false ? callout('Twoje rozwiązanie się nie kompiluje — testy pokazane bez uruchomienia.', 'warn') : null,
          (r.notes || []).length ? h('div', { class: 'small muted' }, r.notes.join(' ')) : null,
          tests.length ? h('div', { class: 'row gap-s' }, button(`Zapisz wszystkie (${tests.length}) jako testy`, { icon: 'save', size: 'sm', variant: 'primary', onClick: async () => {
            for (const tc of tests) await saveOne(tc);
            toast(`Zapisano ${tests.length} testów w testy/.`, 'success'); app.reloadTree(); app.bottom.refreshTests(true);
          } }), r.generator_max ? button('Generator maks. testu → gen_max.cpp', { icon: 'shuffle', size: 'sm', variant: 'ghost', onClick: () => saveAsFile(s.task.dir, 'gen_max.cpp', r.generator_max) }) : null) : callout('Nie udało się ułożyć testów brzegowych z tej treści.', 'info'),
          h('div', { class: 'ws-list' }, tests.map((tc) => {
            const rr = runs.get(tc.id);
            return h('div', { class: 'ws-card' },
              h('div', { class: 'ws-card-head' }, rr ? verdictBadge(rr.verdict) : null, h('b', { class: 'grow' }, tc.title),
                rr?.time !== undefined ? h('span', { class: 'muted tiny' }, fmtTime(rr.time)) : null),
              tc.why ? h('div', { class: 'small muted' }, tc.why) : null,
              pre(tc.input.length > 3000 ? tc.input.slice(0, 3000) + '\n…' : tc.input),
              rr?.stdout !== undefined ? h('details', { class: 'ws-det' }, h('summary', null, 'Wyjście programu'), pre(rr.stdout)) : null,
              h('div', { class: 'row gap-s' },
                button('Zapisz jako test', { icon: 'save', size: 'sm', onClick: async () => { const x = await saveOne(tc); toast(`Zapisano test ${x.number}.`, 'success', { timeout: 1500 }); app.reloadTree(); app.bottom.refreshTests(true); } }),
                button('Użyj jako wejście', { icon: 'file-input', size: 'sm', variant: 'ghost', onClick: () => app.bottom.setCustomInput(tc.input) })));
          })));
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Auto-solver
    async autosolver() {
      if (!file()) return;
      start('autosolver', 'Auto-solver', 'szablony + Twoje rozwiązania');
      const s = await statementOf(); if (!s) return;
      const pb = progressBar();
      pb.indeterminate('Dopasowuję szablony i Twoje kody, sprawdzam na przykładach…');
      const cands = h('div', { class: 'ws-list' });
      done(pb.el, cands);
      const ctl = api.job('/api/autosolver/solve', { statement: s.statement }, { quiet: true, onProgress: (p, m) => pb.set(p, m),
        onEvent: (ev) => { if (ev.type === 'candidate') cands.append(h('div', { class: 'small' }, ev.passed === ev.total && ev.total ? '✓ ' : '· ', ev.name, h('span', { class: 'muted' }, ` ${ev.passed}/${ev.total}`))); } });
      running = ctl;
      try {
        const r = await ctl.promise;
        running = null;
        const code = r.best?.code || '';
        const solved = r.status === 'solved';
        const brute = r.status === 'brute';       // zweryfikowany brute force, ale za wolny na pełne limity
        const weak = r.status === 'weak';         // pasuje do przykładów, ale dowód jest za słaby
        const lead = weak ? '**Niepewne dopasowanie — to nie jest sprawdzone rozwiązanie.** '
          : brute ? '**Poprawny brute force, za wolny na pełne limity.** ' : '';
        done(callout(lead + (r.message || (solved ? 'Znaleziono rozwiązanie zgodne z przykładami.' : 'Nie znalazłem rozwiązania zgodnego z przykładami.')), solved ? 'tip' : (weak ? 'warn' : 'info')),
          r.warning ? callout(r.warning, brute ? 'info' : 'warn') : null,
          r.best ? h('div', { class: 'small' }, h('b', null, r.best.name), ` — przykłady: ${r.best.passed}/${r.best.total}`, weak ? h('span', { class: 'tone-warn' }, ' · słaby dowód') : null, r.best.path ? h('span', { class: 'muted' }, ` · ${r.best.path}`) : null) : null,
          code ? codeActions(s, brute ? 'brute.cpp' : 'rozw_auto.cpp', code) : null,
          code ? codeBlock(code, 'cpp', { maxHeight: 420 }) : null,
          competitors(r),
          !solved ? readerBlock(s, r) : null,
          !solved && (r.hints || []).length ? sec('Wskazówki', h('ul', { class: 'ws-ul' }, r.hints.map((t) => h('li', null, t)))) : null);
      } catch (e) { running = null; fail(e); }
    },

    // ---------------------------------- Objaśnij treść
    async taskexplain() {
      if (!file()) return;
      start('taskexplain', 'Objaśnij treść', 'tresc.md po ludzku');
      const s = await statementOf(); if (!s) return;
      try {
        const r = await api.post('/api/taskexplain', { statement: s.statement }, { quiet: true });
        const lim = [r.limits?.time ? `czas: ${r.limits.time}` : null, r.limits?.memory ? `pamięć: ${r.limits.memory}` : null].filter(Boolean).join(' · ');
        done(
          r.title ? h('div', { class: 'ws-res-big' }, r.title, lim ? h('span', { class: 'muted small' }, ` (${lim})`) : null) : null,
          r.brief?.text ? sec('W skrócie', h('div', { class: 'small' }, r.brief.text,
            r.task_type?.label ? h('div', { class: 'muted tiny' }, `${(r.task_type.labels || [r.task_type.label]).join(' · ')} — ${r.task_type.consequence || ''}`) : null)) : null,
          (r.summary || []).length ? sec(r.brief?.text ? 'Z treści' : 'W skrócie', h('ul', { class: 'ws-ul' }, r.summary.map((x) => h('li', null, x)))) : null,
          (r.given || []).length ? sec('Dane', h('ul', { class: 'ws-ul' }, r.given.map((x) => h('li', null, x)))) : null,
          (r.goal || []).length ? sec('Cel', h('ul', { class: 'ws-ul' }, r.goal.map((x) => h('li', null, x)))) : null,
          (r.input?.format || []).length ? sec('Format wejścia', h('div', { class: 'ws-list' }, r.input.format.map((ln) => h('div', { class: 'small' }, h('code', null, ln.text || (ln.vars || []).join(' ')), ln.desc ? h('span', { class: 'muted' }, ' — ' + ln.desc) : null)))) : null,
          (r.variables || []).length ? sec('Zmienne', h('div', { class: 'ws-vars' }, r.variables.map((v) => h('div', { class: 'ws-var' },
            h('code', null, v.name), h('span', { class: 'grow small' }, v.meaning || ''), (v.min_fmt || v.max_fmt) ? h('span', { class: 'muted tiny mono' }, `${v.min_fmt ?? '?'} … ${v.max_fmt ?? '?'}`) : null)))) : null,
          r.complexity?.complexity ? sec('Docelowa złożoność', h('div', { class: 'small' }, h('b', { class: 'mono' }, r.complexity.complexity), r.complexity.hint ? ` — ${r.complexity.hint}` : '')) : null,
          (r.pitfalls || []).length ? sec('Pułapki', ...r.pitfalls.map((p) => callout(`**${p.title}** — ${p.text}`, p.severity === 'high' ? 'warn' : 'info'))) : null,
          (r.subtasks || []).length ? sec('Podzadania', h('ul', { class: 'ws-ul' }, r.subtasks.map((st) => h('li', null, h('b', null, `${st.points ?? '?'} pkt`), ` — ${st.conditions || ''}`, st.complexity ? h('span', { class: 'muted' }, ` (${st.complexity})`) : null)))) : null,
          more('taskexplain', { statement: s.statement }));
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Doradca algorytmów
    async suggest() {
      if (!file()) return;
      start('suggest', 'Jaki algorytm?', 'doradca na podstawie tresc.md');
      const s = await statementOf(); if (!s) return;
      try {
        const r = await api.post('/api/suggest', { statement: s.statement }, { quiet: true });
        done(
          r.complexity?.complexity ? h('div', { class: 'ws-big' }, h('span', { class: 'ws-big-val mono' }, r.complexity.complexity),
            r.complexity.max_fmt ? h('span', { class: 'muted small' }, `dla ${r.complexity.var} ≤ ${r.complexity.max_fmt}`) : null) : null,
          r.complexity?.hint ? h('div', { class: 'small muted' }, r.complexity.hint) : null,
          sec('Pasujące algorytmy', h('div', { class: 'ws-list' }, (r.algorithms || []).map((a) => h('div', { class: 'ws-card' },
            h('div', { class: 'ws-card-head' }, h('b', { class: 'grow' }, a.name), a.complexity ? h('code', { class: 'tiny' }, a.complexity) : null,
              h('span', { class: 'ws-share', title: `pewność ${Math.round((a.confidence || 0) * 100)}%` }, h('i', { style: { width: `${Math.round((a.confidence || a.relative || 0) * 100)}%` } }))),
            a.when ? h('div', { class: 'small muted' }, a.when) : null,
            (a.reasons || []).length ? h('div', { class: 'tiny muted' }, a.reasons.slice(0, 2).map((x) => x.reason).join(' · ')) : null,
            (a.kb || []).length ? h('div', { class: 'row gap-s' }, a.kb.slice(0, 3).map((k) => h('button', { type: 'button', class: 'chip', onclick: () => app.ctx.navigate('docs', { id: k.id }) }, icon('book-open', 12), k.title))) : null)))),
          (r.similar || []).length ? sec('Podobne zadania z Twoich programów', h('div', { class: 'ws-list' }, r.similar.slice(0, 6).map((x) => h('div', { class: 'ws-sim' },
            h('div', { class: 'grow' }, h('div', { class: 'small bold' }, x.title || x.short), h('div', { class: 'tiny muted' }, [x.contest_name, x.round, x.message].filter(Boolean).join(' · '))),
            x.program_id ? button('Otwórz', { size: 'sm', variant: 'ghost', icon: 'download', title: 'Zaimportuj jako zadanie w edytorze', onClick: async () => {
              const rr = await ws.importProgram(x.program_id); await app.reloadTree(); app.openFile(rr.open);
            } }) : null)))) : null,
          more('suggest', { statement: s.statement }));
      } catch (e) { fail(e); }
    },

    // ---------------------------------- Asystent
    async assistant() {
      start('assistant', 'Zapytaj asystenta', 'lokalna baza wiedzy');
      const history = [];
      const answers = h('div', { class: 'stack gap-s' });
      const inp = h('input', { class: 'input sm', placeholder: 'np. jak działa lower_bound? dlaczego mam WA?', spellcheck: 'false',
        onkeydown: (e) => { if (e.key === 'Enter' && inp.value.trim()) { ask(inp.value.trim()); inp.value = ''; } } });
      done(h('div', { class: 'row nowrap gap-s' }, inp, button(null, { icon: 'send', size: 'sm', variant: 'primary', title: 'Zapytaj', onClick: () => { if (inp.value.trim()) { ask(inp.value.trim()); inp.value = ''; } } })),
        h('div', { class: 'row gap-s' }, ['jak przyspieszyć cin', 'set vs multiset', 'przepełnienie int', 'jak napisać brute'].map((q) => h('button', { type: 'button', class: 'chip', onclick: () => ask(q) }, q))),
        answers);
      setTimeout(() => inp.focus(), 30);
      async function ask(q) {
        const box = h('div', { class: 'ws-ans' }, h('div', { class: 'ws-ans-q' }, icon('message-square', 13), q), h('div', { class: 'ws-res-loading' }, spinner(14)));
        answers.prepend(box);
        try {
          const r = await api.post('/api/assistant/ask', { q, history: history.slice(-6) }, { quiet: true });
          history.push({ q, topic: r.topic?.id || null });
          box.lastChild.replaceWith(h('div', { class: 'stack gap-s' },
            r.answer_title ? h('b', null, r.answer_title) : null,
            ...(r.blocks || []).map(renderBlock),
            (r.suggestions || []).length ? h('div', { class: 'row gap-s' }, r.suggestions.slice(0, 4).map((s) => h('button', { type: 'button', class: 'chip', onclick: () => ask(s) }, s))) : null,
            r.topic?.id ? more('docs', { id: r.topic.id }, `Artykuł: ${r.topic.title || r.topic.id}`) : null));
        } catch (e) { box.lastChild.replaceWith(callout(e.message, 'danger')); }
      }
    },

    // ---------------------------------- Debugger
    async debugger() {
      const f = file(); if (!f) return;
      const code = needCode(f); if (!code) return;
      await app.saveAll();
      app.ctx.navigate('debugger', { code, lang: f.lang, input: app.bottom.input || '', run: true });
    },
  };

  function warnRow(f, w) {
    return h('div', { class: 'ws-note' }, lineLink(f.path, w.line), badge(w.severity === 'error' ? 'błąd' : w.severity === 'warn' ? 'uwaga' : 'info', w.severity === 'error' ? 'danger' : w.severity === 'warn' ? 'warn' : 'neutral'),
      h('div', { class: 'grow small md' }, markdown(w.message || '')));
  }

  function renderBlock(b) {
    switch (b.type) {
      case 'text': return h('div', { class: 'md small' }, markdown(b.md || ''));
      case 'code': return codeBlock(b.code || '', b.lang || 'cpp', { title: b.title, maxHeight: 320 });
      case 'notice': return callout(b.md || '', b.tone === 'warn' ? 'warn' : 'info');
      case 'warn': return callout(b.md || '', 'warn');
      case 'complexity': return callout((b.title ? `**${b.title}** — ` : '') + (b.text || ''), 'info');
      case 'functions': return h('div', { class: 'ws-list' }, (b.items || []).slice(0, 12).map((x) => h('div', { class: 'small' }, h('code', null, x.sig), x.desc ? ` — ${x.desc}` : '', x.complexity ? h('span', { class: 'muted' }, ` (${x.complexity})`) : null)));
      case 'pitfalls': return h('div', null, b.title ? h('b', { class: 'small' }, b.title) : null, h('ul', { class: 'ws-ul' }, (b.items || []).map((x) => h('li', { class: 'md small' }, markdown(x)))));
      case 'table': return h('div', { class: 'md small' }, markdown([b.title ? `**${b.title}**\n` : '', `| ${(b.headers || []).join(' | ')} |`, `|${(b.headers || []).map(() => '---').join('|')}|`, ...(b.rows || []).map((r) => `| ${r.join(' | ')} |`)].join('\n')));
      case 'errors': return h('div', { class: 'ws-list' }, (b.items || []).slice(0, 6).map((e) => h('div', { class: 'err-card' }, h('div', { class: 'ec-title' }, e.title || 'Błąd'), e.explanation ? h('div', { class: 'ec-text' }, markdown(e.explanation)) : null, e.fix ? h('div', { class: 'ec-fix' }, markdown('**Jak naprawić:** ' + e.fix)) : null)));
      case 'topics': return h('div', { class: 'row gap-s' }, (b.items || []).slice(0, 8).map((t) => h('button', { type: 'button', class: 'chip', onclick: () => app.ctx.navigate('docs', { id: t.id }) }, icon('book-open', 12), t.title || t.id)));
      default: return b.md ? h('div', { class: 'md small' }, markdown(b.md)) : null;
    }
  }

  function renderIssues(f, issues, stats, note, extraHead, notesMd) {
    const shown = issues.filter((i) => i.severity !== 'info' || i.line);
    mark(f.path, shown.filter((i) => i.severity !== 'info').map((i) => ({ line: i.line, type: SEV[i.severity]?.mark || 'warn', message: i.title })));
    const counts = {};
    for (const i of shown) counts[i.severity] = (counts[i.severity] || 0) + 1;
    const head = h('div', { class: 'row gap-s' },
      stats?.health ? badge(`${stats.health.score}/100 · ${stats.health.label}`, stats.health.tone) : null,
      ...Object.entries(counts).map(([k, n]) => badge(`${SEV[k]?.label || k}: ${n}`, SEV[k]?.tone || 'neutral')));
    const list = h('div', { class: 'ws-list' }, shown.slice(0, 80).map((i) => {
      const s = SEV[i.severity] || SEV.warning;
      return h('div', { class: `ws-issue sev-${i.severity}` },
        h('div', { class: 'ws-card-head' }, h('span', { class: `tone-${s.tone === 'neutral' ? 'info' : s.tone}` }, icon(s.icon, 14)), h('b', { class: 'grow' }, i.title || i.rule), lineLink(f.path, i.line),
          i.confirmed_by?.length ? badge('potwierdzone', 'danger') : null),
        i.code ? h('code', { class: 'ws-issue-code' }, i.code) : null,
        i.message ? h('div', { class: 'md small' }, markdown(i.message)) : null,
        i.fix ? h('details', { class: 'ws-det' }, h('summary', null, 'Jak naprawić'), h('div', { class: 'md small' }, markdown(i.fix))) : null,
        i.evidence ? h('details', { class: 'ws-det' }, h('summary', null, 'Szczegóły'), pre(i.evidence)) : null,
        i.kb ? h('button', { type: 'button', class: 'ws-linkbtn tiny', onclick: () => app.ctx.navigate('docs', { id: i.kb }) }, icon('book-open', 12), ' artykuł') : null);
    }));
    done(extraHead || null, head, note ? callout(note, 'info') : null, notesMd ? h('div', { class: 'md small muted' }, markdown(notesMd)) : null,
      shown.length ? list : callout('Nie znalazłem problemów. 👍 (To nie gwarancja poprawności — przetestuj na testach!)', 'tip'),
      f.lang === 'cpp' && current === 'checker' ? h('div', { class: 'row gap-s' },
        button('Uruchom z sanitizerami', { icon: 'microscope', size: 'sm', onClick: () => TOOLS.sanitize() }),
        more('checker', { code: app.code() })) : null);
  }

  async function runTool(id) {
    const fn = TOOLS[id];
    if (!fn) return;
    try { await fn(); } catch (e) { console.error(e); fail(e); }
  }

  return {
    el, runTool,
    onFileChange(f) {
      // py2cpp tylko dla .py; testy/zadanie dla wszystkiego
      btns.get('py2cpp')?.classList.toggle('dim', !f || f.lang !== 'python');
      if (markedPath && (!f || f.path !== markedPath)) markedPath = null;
    },
    get current() { return current; },
  };
}

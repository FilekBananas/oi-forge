// Edytor projektów — dolny panel: „Wejście | Wyjście” obok siebie (jak w onlinegdb) oraz lista testów z folderu testy/.
import { h, icon, button, iconButton, clear, toast, modal, field, textarea, outputPanel, verdictBadge, badge, progressBar,
  fmtTime, fmtMem, copyButton, callout } from '../../ui.js';
import { api } from '../../api.js';
import { xicon, ws, persist, baseName } from './util.js';

const CUSTOM = '__custom__';

/** app: {active(), openFile(path, opts), goto(path, line), saveAll(), reloadTree(), runTarget(), show()} */
export function createBottom(app) {
  let tab = persist.get('bottomTab', 'io');
  let testsInfo = { tests_dir: null, tests: [] };
  let testsFor = null;                 // ścieżka pliku, dla której wczytano testy
  const results = new Map();           // `${tests_dir}/${name}` → zdarzenie testu
  let job = null;
  let expandedTest = null;
  let lastExpected = null;

  // ---------------------------------------------------------------- nagłówek z zakładkami
  const tabBtn = (id, ic, label) => h('button', { type: 'button', class: 'ws-btab', dataset: { tab: id }, onclick: () => select(id) }, icon(ic, 14), h('span', null, label), h('span', { class: 'ws-btab-badge' }));
  const tabs = { io: tabBtn('io', 'terminal', 'Wejście i wyjście'), tests: tabBtn('tests', 'list-checks', 'Testy') };
  const headRight = h('div', { class: 'ws-bhead-right' });
  const head = h('div', { class: 'ws-bhead' }, tabs.io, tabs.tests, h('div', { class: 'grow' }), headRight,
    iconButton('chevron-down', { title: `Schowaj panel (${navigator.platform.toUpperCase().includes('MAC') ? '⌘' : 'Ctrl'}+J)`, onClick: () => app.toggleBottom(false) }));

  // ---------------------------------------------------------------- Wejście | Wyjście
  const inputSel = h('select', { class: 'select sm ws-insel', title: 'Skąd brać wejście', onchange: () => onInputSel() });
  const inputTa = h('textarea', { class: 'ws-input mono', spellcheck: 'false', placeholder: 'Tu wpisz dane wejściowe (stdin)…\nAlbo wybierz test z listy powyżej.' });
  const inputNote = h('div', { class: 'ws-input-note' });
  inputTa.addEventListener('input', () => {
    const f = app.active();
    if (f && inputSel.value === CUSTOM) persist.set('input:' + f.path, inputTa.value);
  });
  inputTa.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); app.run(); }
    if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); inputTa.setRangeText('    ', inputTa.selectionStart, inputTa.selectionEnd, 'end'); }
  });
  const out = outputPanel();
  out.el.addEventListener('gotoline', (e) => { e.stopPropagation(); if (lastRunPath) app.goto(lastRunPath, e.detail); });
  const outEmpty = h('div', { class: 'ws-out-empty' }, icon('play', 18), h('div', null, 'Naciśnij ', h('b', null, 'Uruchom'), ' albo ', h('kbd', null, (navigator.platform.toUpperCase().includes('MAC') ? '⌘' : 'Ctrl') + '+Enter'), ', aby zobaczyć wynik.'));
  const expectedBox = h('div', { class: 'ws-expected', hidden: true });
  // strażnik pamięci: po zwykłym uruchomieniu ten sam program z sanitizerami na tym samym wejściu (w tle)
  const guardBox = h('div', { class: 'ws-guard', hidden: true });
  let guardOn = persist.get('guard', true) !== false;
  let guardToken = 0;
  const guardBtn = iconButton('shield', { title: '', onClick: () => { guardOn = !guardOn; persist.set('guard', guardOn); syncGuardBtn(); if (!guardOn) { guardBox.hidden = true; app.ed?.clearMarks?.(); } } });
  const syncGuardBtn = () => { guardBtn.classList.toggle('active', guardOn); guardBtn.title = guardOn ? 'Sprawdzanie pamięci po uruchomieniu: WŁ. (wyjście poza tablicę, overflow…) — kliknij, aby wyłączyć' : 'Sprawdzanie pamięci po uruchomieniu: WYŁ. — kliknij, aby włączyć'; };
  syncGuardBtn();
  const outBody = h('div', { class: 'ws-out-body' }, outEmpty, guardBox, out.el, expectedBox);
  const ioPane = h('div', { class: 'ws-io' },
    h('div', { class: 'ws-io-col ws-io-in' },
      h('div', { class: 'ws-io-head' }, icon('file-input', 14), h('span', { class: 'ws-io-title' }, 'Wejście'), inputSel,
        iconButton('save', { title: 'Zapisz to wejście jako nowy test', onClick: () => addTestDialog(inputTa.value) })),
      inputNote, inputTa),
    h('div', { class: 'ws-io-col ws-io-out' },
      h('div', { class: 'ws-io-head' }, xicon('file-output', 14), h('span', { class: 'ws-io-title' }, 'Wyjście'), h('div', { class: 'grow' }), guardBtn,
        iconButton('x', { title: 'Wyczyść wyjście', onClick: () => { out.clear(); outEmpty.hidden = false; expectedBox.hidden = true; guardBox.hidden = true; } })),
      outBody));
  let lastRunPath = null;

  // ---------------------------------------------------------------- Testy
  const testsHead = h('div', { class: 'ws-tests-head' });
  const testsProg = progressBar();
  testsProg.el.hidden = true;
  const testsSummary = h('div', { class: 'ws-tests-summary' });
  const testsList = h('div', { class: 'ws-tests-list' });
  const testsCompile = outputPanel();
  testsCompile.el.hidden = true;
  testsCompile.el.addEventListener('gotoline', (e) => { e.stopPropagation(); const f = app.runTarget?.(); if (f) app.goto(f.path, e.detail); });
  const testsPane = h('div', { class: 'ws-tests' }, testsHead, testsProg.el, testsSummary, testsCompile.el, testsList);

  // ---------------------------------------------------------------- Terminal (program czeka na dane wpisywane na żywo)
  let ioMode = persist.get('ioMode', 'batch') === 'term' ? 'term' : 'batch';
  const modeBtn = (id, ic, label, title) => h('button', { type: 'button', class: 'ws-mode-btn', dataset: { mode: id }, title, onclick: () => setMode(id) }, icon(ic, 13), label);
  const modeSeg = h('div', { class: 'ws-mode', role: 'group', 'aria-label': 'Sposób podawania wejścia' },
    modeBtn('batch', 'file-input', 'Dane z góry', 'Całe wejście wpisujesz przed uruchomieniem (jak na sprawdzarce)'),
    modeBtn('term', 'terminal', 'Terminal', 'Program startuje od razu i czeka, aż wpiszesz dane — jak w konsoli'));
  const termOut = h('pre', { class: 'ws-term-out', tabindex: '-1' });
  const termHint = h('div', { class: 'ws-term-hint' }, 'Naciśnij ', h('b', null, 'Uruchom'), ' — program wystartuje i będzie czekał na dane. Wpisz linię i Enter; ',
    h('kbd', null, 'Ctrl+D'), ' = koniec danych.');
  const termStatusEl = h('div', { class: 'ws-term-status' });
  const termInput = h('input', { class: 'ws-term-input mono', type: 'text', spellcheck: 'false', autocomplete: 'off', disabled: true, placeholder: 'wpisz dane i Enter…' });
  const termBtns = h('div', { class: 'ws-term-btns' });
  const termCompile = outputPanel();
  termCompile.el.hidden = true;
  termCompile.el.addEventListener('gotoline', (e) => { e.stopPropagation(); if (lastRunPath) app.goto(lastRunPath, e.detail); });
  const termPane = h('div', { class: 'ws-term', hidden: true },
    h('div', { class: 'ws-term-body' }, termHint, termCompile.el, termOut),
    h('div', { class: 'ws-term-row' }, h('span', { class: 'ws-term-prompt' }, '›'), termInput, termBtns),
    termStatusEl);
  let termId = null;
  let termToken = 0;
  const termHist = [];
  let histPos = 0;

  function setMode(m) {
    ioMode = m;
    persist.set('ioMode', m);
    for (const b of modeSeg.children) b.classList.toggle('active', b.dataset.mode === m);
    select(tab);
  }
  function termStatus(text, tone) {
    termStatusEl.className = `ws-term-status ${tone || ''}`;
    termStatusEl.replaceChildren(h('i'), h('span', null, text));
    clear(termBtns);
    if (tone === 'run') {
      termBtns.append(
        button('Koniec danych', { size: 'sm', variant: 'ghost', title: 'Zamknij wejście (Ctrl+D) — dla programów czytających do końca pliku', onClick: () => termSend('', true) }),
        button('Zatrzymaj', { icon: 'circle-stop', size: 'sm', variant: 'ghost', onClick: () => termKill() }));
    }
  }
  async function termSend(text, eof = false) {
    if (!termId) return;
    try { await api.post('/api/term/write', { id: termId, data: text, eof }, { quiet: true }); } catch { /* sesja już się skończyła */ }
  }
  function termKill() {
    if (termId) api.post('/api/term/kill', { id: termId }, { quiet: true }).catch(() => {});
  }
  termInput.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); app.run(); return; }
    if (e.ctrlKey && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); if (termInput.value) termSend(termInput.value + '\n'); termInput.value = ''; termSend('', true); return; }
    if (e.ctrlKey && (e.key === 'c' || e.key === 'C') && !termInput.selectionEnd) { e.preventDefault(); termKill(); return; }
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = termInput.value;
      if (v.trim()) { termHist.push(v); }
      histPos = termHist.length;
      termInput.value = '';
      termSend(v + '\n');
      return;
    }
    if (e.key === 'ArrowUp' && termHist.length) { e.preventDefault(); histPos = Math.max(0, histPos - 1); termInput.value = termHist[histPos] || ''; }
    if (e.key === 'ArrowDown' && termHist.length) { e.preventDefault(); histPos = Math.min(termHist.length, histPos + 1); termInput.value = termHist[histPos] || ''; }
  });
  termInput.addEventListener('paste', (e) => {
    const t = e.clipboardData?.getData('text') || '';
    if (!t.includes('\n')) return;
    e.preventDefault();
    const all = termInput.value + t;
    termInput.value = '';
    termSend(all.endsWith('\n') ? all : all + '\n');
  });
  termPane.addEventListener('mouseup', () => { if (!window.getSelection()?.toString() && !termInput.disabled) termInput.focus(); });

  async function runTerminal(target) {
    app.toggleBottom(true);
    select('io');
    const my = ++termToken;
    const prev = termId;
    termId = null;
    lastRunPath = target.path;
    termHint.hidden = true;
    termCompile.el.hidden = true;
    termOut.replaceChildren();
    termInput.disabled = true;
    termStatus(target.lang === 'python' ? 'Uruchamiam…' : 'Kompiluję…', 'busy');
    let r;
    try { r = await api.post('/api/term/start', { code: target.code, lang: target.lang, replace: prev }, { quiet: true }); } catch (e) { termStatus(e.message || String(e), 'bad'); return null; }
    if (my !== termToken) { if (r?.id) api.post('/api/term/kill', { id: r.id }, { quiet: true }).catch(() => {}); return null; }
    if (!r.started) {
      termCompile.el.hidden = false;
      await termCompile.showResult({ compile: r.compile }, { source: target.code });
      termStatus('Błąd kompilacji — popraw kod i uruchom ponownie', 'bad');
      return r;
    }
    termId = r.id;
    termInput.disabled = false;
    termInput.focus();
    termStatus('Działa — czeka na Twoje dane (Enter wysyła linię, Ctrl+D kończy dane)', 'run');
    const spans = [];
    let since = 0, rev = -1;
    while (my === termToken) {
      let x;
      try { x = await api.get('/api/term/read', { id: r.id, since, rev }, { quiet: true }); } catch { break; }
      if (my !== termToken) break;
      x.chunks.forEach((c, i) => {
        const k = x.start + i;
        if (!spans[k]) { spans[k] = h('span', { class: `t-${c.k}` }); termOut.append(spans[k]); }
        spans[k].textContent = c.t;
      });
      since = x.next; rev = x.rev;
      termOut.scrollTop = termOut.scrollHeight;
      if (x.done) {
        termId = null;
        termInput.disabled = true;
        const t = x.time >= 1 ? `${x.time.toFixed(2)} s` : `${Math.round(x.time * 1000)} ms`;
        if (x.exit_code === 0) termStatus(`Program zakończył działanie · ${t}`, 'ok');
        else termStatus(`Błąd wykonania (kod ${x.exit_code}${x.signal ? `, ${x.signal}` : ''}) · ${t} — sprawdź „Sanitizery”, żeby zobaczyć linię`, 'bad');
        break;
      }
    }
    return r;
  }

  const content = h('div', { class: 'ws-bcontent' }, ioPane, termPane, testsPane);
  const el = h('div', { class: 'ws-bottom-inner' }, head, content);

  function select(id) {
    tab = id;
    persist.set('bottomTab', id);
    for (const [k, b] of Object.entries(tabs)) b.classList.toggle('active', k === id);
    ioPane.hidden = id !== 'io' || ioMode === 'term';
    termPane.hidden = id !== 'io' || ioMode !== 'term';
    testsPane.hidden = id !== 'tests';
    for (const b of modeSeg.children) b.classList.toggle('active', b.dataset.mode === ioMode);
    clear(headRight);
    if (id === 'tests') {
      headRight.append(
        job ? button('Zatrzymaj', { icon: 'circle-stop', size: 'sm', variant: 'ghost', onClick: () => job?.cancel() })
          : button('Uruchom wszystkie', { icon: 'list-checks', size: 'sm', variant: 'secondary', onClick: () => runAll(), title: 'Uruchom program na wszystkich testach z folderu testy/' }),
        iconButton('plus', { title: 'Dodaj test', onClick: () => addTestDialog('') }),
        iconButton('refresh-cw', { title: 'Odśwież listę testów', onClick: () => refreshTests(true) }));
      if (testsFor !== app.active()?.path) refreshTests();
    } else {
      headRight.append(modeSeg, h('span', { class: 'muted tiny ws-run-hint' }, 'Uruchom: ', h('kbd', null, (navigator.platform.toUpperCase().includes('MAC') ? '⌘' : 'Ctrl') + '+Enter')));
    }
  }

  // ---------------------------------------------------------------- testy: dane
  async function refreshTests(force = false) {
    const f = app.active();
    if (!f) { testsInfo = { tests_dir: null, tests: [] }; testsFor = null; renderTests(); fillInputSel(); return; }
    if (!force && testsFor === f.path) return;
    testsFor = f.path;
    try { testsInfo = await ws.tests(f.path); } catch { testsInfo = { tests_dir: null, tests: [] }; }
    renderTests();
    fillInputSel();
  }

  function key(t) { return `${testsInfo.tests_dir}/${t.name}`; }

  function renderTests() {
    clear(testsHead); clear(testsList);
    const f = app.active();
    const tests = testsInfo.tests || [];
    const tb = tabs.tests.querySelector('.ws-btab-badge');
    const res = tests.map((t) => results.get(key(t))).filter(Boolean);
    const bad = res.filter((r) => r.verdict !== 'OK').length;
    tb.textContent = tests.length ? (res.length ? (bad ? `${bad} ✗` : `${res.length} ✓`) : String(tests.length)) : '';
    tb.className = 'ws-btab-badge' + (res.length ? (bad ? ' bad' : ' good') : '');
    if (!f) { testsList.append(h('div', { class: 'ws-tests-empty muted small' }, 'Otwórz plik z rozwiązaniem, aby zobaczyć jego testy.')); return; }
    testsHead.append(h('span', { class: 'muted small' }, testsInfo.tests_dir ? [icon('folder', 13), ' ', testsInfo.tests_dir] : 'Brak folderu testy/ dla tego pliku'));
    if (!tests.length) {
      testsList.append(h('div', { class: 'ws-tests-empty' }, h('div', { class: 'muted small' }, 'Nie ma jeszcze testów. Test to para plików ', h('code', null, 'N.in'), ' + ', h('code', null, 'N.out'), ' w folderze ', h('code', null, 'testy/'), '.'),
        button('Dodaj pierwszy test', { icon: 'plus', size: 'sm', variant: 'primary', onClick: () => addTestDialog(inputTa.value) })));
      return;
    }
    for (const t of tests) {
      const r = results.get(key(t));
      const open = expandedTest === key(t);
      const row = h('div', { class: ['ws-test', open ? 'open' : '', r ? 'v-' + r.verdict : ''].join(' ') },
        h('div', { class: 'ws-test-row', onclick: () => { expandedTest = open ? null : key(t); renderTests(); } },
          h('span', { class: 'ws-test-v' }, r ? (r.verdict === 'OK' && !r.checked ? badge('wykonano', 'neutral') : verdictBadge(r.verdict)) : h('span', { class: 'ws-test-dot' })),
          h('span', { class: 'ws-test-name mono' }, t.name),
          h('span', { class: 'ws-test-prev mono' }, (t.in_preview || '').replace(/\s+/g, ' ').slice(0, 80) || '(puste)'),
          t.out ? null : h('span', { class: 'badge', title: 'Brak pliku z oczekiwanym wyjściem — test tylko uruchamia program' }, 'bez .out'),
          r ? h('span', { class: 'ws-test-meta' }, fmtTime(r.time), r.mem_kb ? ' · ' + fmtMem(r.mem_kb) : '') : null,
          icon(open ? 'chevron-down' : 'chevron-right', 14)));
      if (open) row.append(testDetail(t, r));
      testsList.append(row);
    }
  }

  function testDetail(t, r) {
    const box = h('div', { class: 'ws-test-detail' });
    const inPre = h('pre', { class: 'ws-pre' }, t.in_preview ?? '…');
    const outPre = h('pre', { class: 'ws-pre' }, t.out ? (t.out_preview ?? '…') : h('span', { class: 'faint' }, '(brak pliku .out)'));
    // pełna treść (podgląd z listy jest obcięty)
    ws.file(t.in, { quiet: true }).then((x) => { inPre.textContent = x.content.length > 20000 ? x.content.slice(0, 20000) + '\n…' : x.content; }).catch(() => {});
    if (t.out) ws.file(t.out, { quiet: true }).then((x) => { outPre.textContent = x.content.length > 20000 ? x.content.slice(0, 20000) + '\n…' : x.content; }).catch(() => {});
    const cols = [
      h('div', { class: 'ws-td-col' }, h('div', { class: 'ws-td-label' }, 'Wejście', h('span', { class: 'grow' }), iconButton('pencil', { title: 'Otwórz plik .in', onClick: () => app.openFile(t.in) })), inPre),
      h('div', { class: 'ws-td-col' }, h('div', { class: 'ws-td-label' }, 'Oczekiwane', h('span', { class: 'grow' }), t.out ? iconButton('pencil', { title: 'Otwórz plik .out', onClick: () => app.openFile(t.out) }) : null), outPre),
    ];
    if (r) {
      cols.push(h('div', { class: 'ws-td-col' }, h('div', { class: 'ws-td-label' }, 'Twoje wyjście', h('span', { class: 'grow' }), copyButton(() => r.stdout || '')),
        h('pre', { class: ['ws-pre', r.verdict === 'WA' ? 'bad' : ''].join(' ') }, r.stdout || h('span', { class: 'faint' }, '(puste)'))));
    }
    box.append(h('div', { class: 'ws-td-cols' }, cols));
    if (r?.diff) {
      const d = r.diff;
      box.append(callout(`**Pierwsza różnica w linii ${d.line}${d.token ? ` (słowo ${d.token})` : ''}:** oczekiwano \`${d.expected ?? '(koniec wyjścia)'}\`, otrzymano \`${d.got ?? '(koniec wyjścia)'}\`.`, 'danger'));
    }
    if (r && (r.verdict === 'RE' || r.verdict === 'TLE' || r.verdict === 'MLE')) {
      box.append(callout(r.verdict === 'TLE' ? `Przekroczony limit czasu (${fmtTime(r.time)}).` : `**${r.signal || 'Błąd wykonania'}** (kod wyjścia ${r.exit_code}).` + (r.stderr ? '\n\n```\n' + r.stderr.slice(0, 1500) + '\n```' : ''), r.verdict === 'TLE' ? 'warn' : 'danger'));
    }
    box.append(h('div', { class: 'row gap-s' },
      button('Użyj jako wejście', { icon: 'file-input', size: 'sm', onClick: () => { useTest(t); select('io'); } }),
      button('Uruchom ten test', { icon: 'play', size: 'sm', onClick: () => runAll([t.name]) }),
      r && r.stdout !== undefined && (!t.out || r.verdict === 'WA') ? button(t.out ? 'Zastąp oczekiwane moim wyjściem' : 'Zapisz moje wyjście jako .out', {
        icon: 'save', size: 'sm', variant: 'ghost', onClick: async () => {
          const outPath = t.out || t.in.replace(/\.in$/i, '.out');
          await ws.save({ path: outPath, content: r.stdout, force: true });
          toast('Zapisano oczekiwane wyjście.', 'success', { timeout: 1500 });
          results.delete(key(t)); await refreshTests(true); app.reloadTree();
        } }) : null,
      h('span', { class: 'grow' }),
      button('Usuń test', { icon: 'trash-2', size: 'sm', variant: 'ghost', onClick: async () => {
        await ws.del(t.in); if (t.out) await ws.del(t.out);
        results.delete(key(t)); toast(`Test ${t.name} przeniesiono do kosza.`, 'info', { timeout: 2000 });
        await refreshTests(true); app.reloadTree();
      } })));
    return box;
  }

  // ---------------------------------------------------------------- testy: uruchamianie
  async function runAll(only) {
    const target = await app.runTarget();
    if (!target) return;
    if (job) return;
    await app.saveAll();
    app.toggleBottom(true);
    select('tests');
    if (testsFor !== app.active()?.path) await refreshTests(true);
    const names = only || (testsInfo.tests || []).map((t) => t.name);
    for (const n of names) results.delete(`${testsInfo.tests_dir}/${n}`);
    testsCompile.el.hidden = true;
    testsProg.el.hidden = false;
    testsProg.set(0, 'Kompiluję…');
    clear(testsSummary);
    renderTests();
    const ctl = api.job('/api/ws/run_tests', { path: target.path, tests: only || undefined }, {
      onProgress: (p, msg) => testsProg.set(p, msg),
      onEvent: (ev) => {
        if (ev.type === 'test') {
          results.set(`${testsInfo.tests_dir}/${ev.name}`, ev);
          if (ev.verdict !== 'OK' && !expandedTest && !only) expandedTest = `${testsInfo.tests_dir}/${ev.name}`;
          renderTests();
        } else if (ev.type === 'compile_error') {
          testsCompile.el.hidden = false;
          testsCompile.showCompile(ev.compile, target.code);
        }
      },
    });
    job = ctl;
    select('tests');
    try {
      const res = await ctl.promise;
      showSummary(res);
      app.onTestsDone?.(res);
    } catch (e) {
      if (!e.cancelled) testsSummary.replaceChildren(callout(e.message, 'danger'));
      else testsSummary.replaceChildren(h('span', { class: 'muted small' }, 'Zatrzymano.'));
    } finally {
      job = null;
      testsProg.el.hidden = true;
      if (tab === 'tests') select('tests');
    }
  }

  function showSummary(res) {
    clear(testsSummary);
    if (!res) return;
    if (res.status === 'CE') { testsSummary.append(verdictBadge('CE', 'Błąd kompilacji')); return; }
    const counts = Object.entries(res.counts || {}).filter(([k]) => k !== 'OK');
    testsSummary.append(
      res.all_ok ? verdictBadge('OK', `${res.passed}/${res.total} OK`) : badge(`${res.passed}/${res.total} OK`, res.passed ? 'warn' : 'danger'),
      ...counts.map(([k, n]) => h('span', { class: 'ws-cnt' }, verdictBadge(k), ` ×${n}`)),
      h('span', { class: 'muted small' }, `max ${fmtTime(res.max_time)}`, res.max_mem_kb ? ` · ${fmtMem(res.max_mem_kb)}` : '', ` · limit ${fmtTime(res.timeout)}`),
      res.unchecked ? h('span', { class: 'muted small' }, ` · ${res.unchecked} bez .out (nie sprawdzono)`) : null);
  }

  // ---------------------------------------------------------------- dodawanie testu
  function addTestDialog(initialInput = '', initialOutput = '') {
    const f = app.active();
    if (!f) { toast('Najpierw otwórz plik z zadania.', 'info'); return; }
    const inTa = textarea({ value: initialInput, rows: 7, placeholder: 'Dane wejściowe testu' });
    const outTa = textarea({ value: initialOutput, rows: 5, placeholder: 'Oczekiwane wyjście (opcjonalnie — puste = test tylko uruchamia program)' });
    const m = modal({
      title: 'Nowy test', icon: 'plus', width: 620,
      body: h('div', { class: 'stack' }, field('Wejście (.in)', inTa), field('Oczekiwane wyjście (.out)', outTa,
        `Zapisze się jako kolejny numer w ${testsInfo.tests_dir || 'testy/ (folder zostanie utworzony)'}.`)),
      actions: [button('Anuluj', { variant: 'ghost', onClick: () => m.close() }),
        button('Zapisz test', { variant: 'primary', icon: 'save', onClick: async () => {
          if (!inTa.value.trim()) { toast('Wejście testu jest puste.', 'warn'); return; }
          const r = await ws.saveTest({ dir: f.path, input: inTa.value, output: outTa.value.trim() ? outTa.value : null });
          m.close();
          toast(`Dodano test ${r.number} (${r.in}).`, 'success', { timeout: 1800 });
          await refreshTests(true);
          app.reloadTree();
        } })],
    });
  }

  // ---------------------------------------------------------------- wejście programu
  function fillInputSel() {
    const f = app.active();
    const want = f ? persist.get('insel:' + f.path, CUSTOM) : CUSTOM;
    clear(inputSel);
    inputSel.append(h('option', { value: CUSTOM }, 'Własne wejście'));
    for (const t of testsInfo.tests || []) inputSel.append(h('option', { value: t.in }, `Test ${t.name}${t.out ? '' : ' (bez .out)'}`));
    inputSel.value = [...inputSel.options].some((o) => o.value === want) ? want : CUSTOM;
    onInputSel(true);
  }

  async function onInputSel(silent) {
    const f = app.active();
    const v = inputSel.value;
    if (f) persist.set('insel:' + f.path, v);
    lastExpected = null;
    if (v === CUSTOM) {
      inputTa.readOnly = false;
      inputTa.classList.remove('from-test');
      inputTa.value = f ? (persist.get('input:' + f.path, '') || '') : '';
      inputNote.hidden = true;
      return;
    }
    const t = (testsInfo.tests || []).find((x) => x.in === v);
    inputTa.readOnly = true;
    inputTa.classList.add('from-test');
    inputNote.hidden = false;
    inputNote.replaceChildren(icon('info', 13), h('span', null, `Wejście z pliku ${baseName(v)}`, t?.out ? ', wynik zostanie porównany z ' + baseName(t.out) : ''),
      h('button', { type: 'button', class: 'ws-linkbtn', onclick: () => app.openFile(v) }, 'edytuj plik'));
    try { inputTa.value = (await ws.file(v, { quiet: true })).content; } catch { inputTa.value = ''; if (!silent) toast('Nie udało się wczytać testu.', 'warn'); }
    if (t?.out) { try { lastExpected = (await ws.file(t.out, { quiet: true })).content; } catch { lastExpected = null; } }
  }

  function useTest(t) { inputSel.value = t.in; onInputSel(); }

  function setCustomInput(text) {
    inputSel.value = CUSTOM;
    const f = app.active();
    if (f) { persist.set('insel:' + f.path, CUSTOM); persist.set('input:' + f.path, text); }
    onInputSel(true);
    inputTa.value = text;
    select('io');
    app.toggleBottom(true);
  }

  // ---------------------------------------------------------------- uruchamianie
  async function run(target) {
    if (ioMode === 'term') return runTerminal(target);
    app.toggleBottom(true);
    select('io');
    outEmpty.hidden = true;
    expectedBox.hidden = true;
    out.setBusy(true, target.lang === 'python' ? 'Uruchamiam…' : 'Kompiluję i uruchamiam…');
    lastRunPath = target.path;
    const expected = inputSel.value !== CUSTOM ? lastExpected : null;
    try {
      const res = await api.post('/api/run', { code: target.code, lang: target.lang, input: inputTa.value }, { quiet: true });
      await out.showResult(res, { source: target.code, expected: expected ?? undefined });
      if (res.run && expected !== null && expected !== undefined) showExpected(res.run.stdout, expected);
      guard(target, res);
      return res;
    } catch (e) {
      out.showText(e.message || String(e), 'danger');
      return null;
    }
  }

  async function guard(target, res) {
    const my = ++guardToken;
    guardBox.hidden = true;
    if (!guardOn || target.lang === 'python' || !res?.run || !(res.compile?.ok ?? true)) return;
    guardBox.hidden = false;
    guardBox.className = 'ws-guard checking';
    guardBox.replaceChildren(h('span', { class: 'spinner', style: 'width:12px;height:12px' }), h('span', null, 'Sprawdzam pamięć (wyjście poza tablicę, overflow)…'));
    let g;
    try { g = await api.post('/api/checker/guard', { code: target.code, input: inputTa.value }, { quiet: true }); } catch { g = null; }
    if (my !== guardToken) return;
    if (!g || !g.checked) { guardBox.hidden = true; return; }
    if (!g.issues.length) {
      guardBox.className = 'ws-guard ok';
      guardBox.replaceChildren(icon('shield-check', 14), h('span', null, 'Pamięć OK — bez wyjścia poza tablicę i niezdefiniowanych zachowań na tym wejściu.'));
      if (app.active()?.path === target.path) app.ed?.clearMarks?.();
      return;
    }
    guardBox.className = 'ws-guard bad';
    const first = g.issues[0];
    guardBox.replaceChildren(
      h('div', { class: 'ws-guard-head' }, icon('triangle-alert', 15),
        h('b', null, first.title || 'Błąd pamięci'),
        first.line ? h('button', { type: 'button', class: 'ws-ln', title: 'Przejdź do linii', onclick: () => app.goto(target.path, first.line) }, `linia ${first.line}`) : null),
      first.code ? h('pre', { class: 'ws-guard-code' }, first.code) : null,
      h('div', { class: 'ws-guard-msg' }, 'Wypisany wynik jest przypadkowy — program czyta albo zapisuje nie swoją pamięć.'),
      h('details', null, h('summary', null, 'Szczegóły i jak naprawić'), h('div', null, first.message || ''), first.fix ? h('div', { style: 'margin-top:6px' }, first.fix) : null),
      g.issues.length > 1 ? h('div', { class: 'ws-guard-more' }, `+ ${g.issues.length - 1} kolejny błąd: `, g.issues.slice(1).map((i) => `${i.title}${i.line ? ` (linia ${i.line})` : ''}`).join(' · ')) : null);
    if (app.active()?.path === target.path) app.ed?.markLines?.(g.issues.filter((i) => i.line > 0).map((i) => ({ line: i.line, type: 'error', message: i.title })));
  }

  function showExpected(got, exp) {
    const norm = (s) => s.trim().split(/\s+/).join(' ');
    if (norm(got) === norm(exp)) { expectedBox.hidden = true; return; }
    const g = got.replace(/\r/g, '').split('\n'), e = exp.replace(/\r/g, '').split('\n');
    let line = 0;
    while (line < Math.max(g.length, e.length) && (g[line] || '').trim().split(/\s+/).join(' ') === (e[line] || '').trim().split(/\s+/).join(' ')) line++;
    expectedBox.hidden = false;
    expectedBox.replaceChildren(
      h('div', { class: 'out-label' }, 'Oczekiwane wyjście', h('span', { class: 'muted', style: 'text-transform:none;letter-spacing:0;font-weight:500' }, ` — pierwsza różnica w linii ${line + 1}`)),
      h('pre', { class: 'out' }, e.map((ln, i) => h('span', { class: i === line ? 'ws-diff-line' : '' }, ln + (i < e.length - 1 ? '\n' : '')))));
  }

  // ---------------------------------------------------------------- API panelu
  select(tab);
  return {
    el, select, run, runAll, refreshTests, setCustomInput, addTestDialog, fillInputSel,
    get input() { return inputTa.value; },
    get tab() { return tab; },
    onFileChange() { testsFor = null; expandedTest = null; refreshTests(); },
    get busy() { return !!job; },
  };
}

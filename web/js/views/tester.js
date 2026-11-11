// (d) Testerka uniwersalna: Twoje rozwiązanie vs brute na testach z TWOJEGO generatora (N testów z pustymi liniami).
import { h, icon, button, viewHeader, card, field, input, select, checkbox, segmented, badge, verdictBadge, more,
  progressBar, stat, table, callout, codeBlock, clear, toast, modal, fmtTime, plural, markdown } from '../ui.js';
import { api, local, store } from '../api.js';
import { createEditor } from '../editor.js';
import { diffResult } from './diff.js';

const ROLES = [
  { id: 'solution', label: 'Rozwiązanie', icon: 'file-code', hint: 'Twój kod, który chcesz sprawdzić' },
  { id: 'brute', label: 'Brute', icon: 'hammer', hint: 'Wolne, ale pewne rozwiązanie wzorcowe' },
  { id: 'generator', label: 'Generator', icon: 'shuffle', hint: 'Wypisuje N testów oddzielonych pustą linią (argv[1] = seed, argv[2] = N)' },
];
const SOL_TEMPLATE = `#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    long long s = 0;
    for (int i = 0; i < n; i++) { long long x; cin >> x; s += x; }
    cout << s << "\\n";
}
`;

function download(name, text) {
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/plain' })), download: name });
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

export default {
  id: 'tester', title: 'Testerka', icon: 'flask-conical', group: 'Testowanie', order: 10, layout: 'full',
  description: 'Twoje rozwiązanie kontra brute na tysiącach testów z Twojego generatora — równolegle, z minimalnym kontrprzykładem.',
  keywords: ['stress test', 'brute', 'generator', 'testerka', 'wzorcówka', 'kontrprzykład', 'testy'],

  async render(root, ctx) {
    const self = this;
    const st = Object.assign({ mode: 'multi', tests: 1000, runs: 1, seed: '', timeout: 2, compare: 'tokens', eps: '1e-6',
      checker: '', separator: '', stop: true, shrink: true, layout: 'tabs' }, local.get('tester:opts', {}) || {});
    const saveOpts = () => local.set('tester:opts', st);
    let templates = { generators: [], checkers: [] };
    try { templates = await api.get('/api/tester/templates', null, { quiet: true }); } catch { /* */ }
    let customChecker = local.get('tester:checker_code', '') || '';

    // ---------------------------------------------------------------- edytory
    const eds = {};
    const holders = {};
    const langs = local.get('tester:langs', {}) || {};
    for (const r of ROLES) {
      holders[r.id] = h('div', { class: 'ts-ed-holder' });
      eds[r.id] = createEditor(holders[r.id], {
        lang: langs[r.id] || 'cpp', height: '100%', storageKey: 'tester-' + r.id, langSelect: true, title: r.label,
        onLangChange: (l) => { langs[r.id] = l; local.set('tester:langs', langs); },
        value: r.id === 'solution' ? SOL_TEMPLATE : r.id === 'generator' ? (templates.generators[0]?.code || '') : '',
        placeholder: r.hint, onRun: () => start(),
      });
    }
    const edArea = h('div', { class: 'ts-editors' });
    const tabsBar = h('div', { class: 'ts-ed-tabs' });
    let activeEd = 'solution';
    function layoutEditors() {
      clear(edArea); clear(tabsBar);
      edArea.classList.toggle('cols', st.layout === 'cols');
      if (st.layout === 'cols') {
        for (const r of ROLES) edArea.append(holders[r.id]);
        tabsBar.hidden = true;
      } else {
        tabsBar.hidden = false;
        for (const r of ROLES) {
          tabsBar.append(h('button', { type: 'button', class: ['ts-tab', r.id === activeEd ? 'active' : ''].join(' '), title: r.hint,
            onclick: () => { activeEd = r.id; layoutEditors(); } }, icon(r.icon, 14), r.label));
        }
        edArea.append(holders[activeEd]);
      }
      requestAnimationFrame(() => ROLES.forEach((r) => eds[r.id].refresh()));
    }

    // ---------------------------------------------------------------- ustawienia
    const num = (key, opts = {}) => {
      const el = input({ value: st[key] ?? '', type: 'number', mono: true, size: 'sm', placeholder: opts.placeholder || '',
        onInput: (v) => { st[key] = v; saveOpts(); }, attrs: { min: opts.min ?? 0, step: opts.step ?? 1 } });
      el.style.width = opts.width || '96px';
      return el;
    };
    const modeSeg = segmented([
      { value: 'multi', label: 'Wiele testów z generatora', title: 'Generator wypisuje N testów oddzielonych pustymi liniami' },
      { value: 'seed', label: 'Seed → 1 test', title: 'Każde uruchomienie generatora z innym seedem = jeden test' }],
    { value: st.mode, onChange: (v) => { st.mode = v; saveOpts(); syncMode(); } });
    const testsInp = num('tests', { min: 1, width: '90px' });
    const runsInp = num('runs', { min: 1, width: '70px' });
    const runsField = field('Uruchomień generatora', runsInp);
    const testsLabel = h('label', null, 'Testów na uruchomienie');
    const testsField = h('div', { class: 'field' }, testsLabel, testsInp);
    const seedInp = input({ value: st.seed, placeholder: 'losowy', mono: true, size: 'sm', onInput: (v) => { st.seed = v.trim(); saveOpts(); } });
    seedInp.style.width = '100px';
    const epsInp = input({ value: st.eps, mono: true, size: 'sm', onInput: (v) => { st.eps = v.trim(); saveOpts(); } });
    epsInp.style.width = '86px';
    const epsWrap = h('span', { class: 'row gap-s' }, h('span', { class: 'muted small' }, 'eps'), epsInp);
    const cmpSel = select([{ value: 'tokens', label: 'Tokeny (jak OI)' }, { value: 'exact', label: 'Dokładnie (linie)' }, { value: 'float', label: 'Liczby z tolerancją' }],
      { value: st.compare, size: 'sm', onChange: (v) => { st.compare = v; saveOpts(); epsWrap.hidden = v !== 'float'; } });
    epsWrap.hidden = st.compare !== 'float';
    const chkOpts = [{ value: '', label: 'Bez checkera (porównanie)' }, ...templates.checkers.filter((c) => c.id !== 'custom_template').map((c) => ({ value: c.id, label: c.name })),
      { value: '__custom', label: 'Własny checker…' }];
    const chkSel = select(chkOpts, { value: st.checker, size: 'sm', onChange: (v) => { st.checker = v; saveOpts(); if (v === '__custom') editChecker(); } });
    const sepInp = input({ value: st.separator, placeholder: 'pusta linia', mono: true, size: 'sm', onInput: (v) => { st.separator = v; saveOpts(); } });
    sepInp.style.width = '110px';
    const sepField = field('Separator testów', sepInp);
    const stopBox = checkbox('Zatrzymaj na pierwszym błędzie', { checked: st.stop, onChange: (v) => { st.stop = v; saveOpts(); } });
    const shrinkBox = checkbox('Zmniejszaj kontrprzykład', { checked: st.shrink, onChange: (v) => { st.shrink = v; saveOpts(); },
      title: 'Po znalezieniu błędu szuka najmniejszego błędnego testu i próbuje go dalej zmniejszyć (rozpoznając format wejścia)' });
    function syncMode() {
      const multi = st.mode === 'multi';
      runsField.hidden = !multi; sepField.hidden = !multi;
      testsLabel.textContent = multi ? 'Testów na uruchomienie' : 'Liczba testów (seedów)';
    }
    syncMode();

    function editChecker() {
      const holder = h('div', { style: 'height:420px;display:flex;flex-direction:column' });
      const tpl = templates.checkers.find((c) => c.id === 'custom_template');
      const m = modal({
        title: 'Własny checker', icon: 'list-checks', width: 860,
        body: h('div', { class: 'stack gap-s' },
          h('div', { class: 'muted small' }, 'Program uruchamiany jako ', h('code', null, 'checker wejście wyjście_programu wyjście_brute'),
            ' — kod wyjścia 0 = OK, inny = błąd. Komunikat wypisz na standardowe wyjście.'), holder),
        actions: [
          button('Wstaw szablon', { variant: 'ghost', icon: 'file-input', onClick: () => ced.setValue(tpl?.code || '') }),
          button('Zapisz', { variant: 'primary', icon: 'check', onClick: () => { customChecker = ced.getValue(); local.set('tester:checker_code', customChecker); m.close(); toast('Zapisano checker', 'success'); } })],
      });
      const ced = createEditor(holder, { value: customChecker || tpl?.code || '', lang: 'cpp', height: '100%' });
      setTimeout(() => ced.refresh(), 50);
    }

    function insertGenerator(gid) {
      const g = templates.generators.find((x) => x.id === gid);
      if (!g) return;
      eds.generator.setValue(g.code);
      eds.generator.setLang(g.lang);
      if (g.mode && g.mode !== st.mode) { st.mode = g.mode; modeSeg.setValue(g.mode); saveOpts(); syncMode(); }
      if (st.layout === 'tabs') { activeEd = 'generator'; layoutEditors(); }
      toast(`Wstawiono szablon: ${g.name}`, 'success', { timeout: 1600 });
    }
    const genMenu = select([{ value: '', label: 'Wstaw szablon generatora…' }, ...templates.generators.map((g) => ({ value: g.id, label: g.name }))],
      { size: 'sm', onChange: (v, el) => { if (v) insertGenerator(v); el.value = ''; } });

    const layoutSeg = segmented([{ value: 'tabs', label: 'Zakładki', icon: 'layers' }, { value: 'cols', label: 'Kolumny', icon: 'table' }],
      { value: st.layout, onChange: (v) => { st.layout = v; saveOpts(); layoutEditors(); } });

    // ---------------------------------------------------------------- wyniki
    const prog = progressBar({ label: 'Gotowy' });
    const counters = h('div', { class: 'ts-counters' });
    const timesBox = h('div', { class: 'ts-times muted small' });
    const msgBox = h('div', { class: 'stack gap-s' });
    const failBox = h('div', { class: 'stack' });
    const testRows = [];
    const testTable = table([
      { key: 'index', label: '#', render: (r) => String(r.index + 1) },
      { key: 'verdict', label: 'Werdykt', render: (r) => verdictBadge(r.verdict === 'SKIP' ? 'SKIP' : r.verdict) },
      { key: 'time', label: 'Czas', align: 'right', render: (r) => fmtTime(r.time) },
      { key: 'brute_time', label: 'Brute', align: 'right', render: (r) => fmtTime(r.brute_time) },
      { key: 'size', label: 'Rozmiar', align: 'right', render: (r) => `${r.size} zn.` },
    ], [], { compact: true, maxHeight: '320px', empty: 'Tu pojawią się wyniki pierwszych 200 testów.' });
    const summaryBox = h('div');
    let ctl = null;
    let running = false;
    const startBtn = button('Start', { icon: 'play', variant: 'primary', size: 'lg', onClick: () => start() });
    const stopBtn = button('Zatrzymaj', { icon: 'circle-stop', variant: 'danger', size: 'lg', onClick: () => ctl && ctl.cancel() });
    stopBtn.hidden = true;

    function renderCounters(s) {
      const c = (s && s.counts) || {};
      const done = s?.done ?? 0;
      clear(counters).append(
        stat({ label: 'Testy', value: s?.total ? `${done} / ${s.total}` : String(done), icon: 'list-checks' }),
        stat({ label: 'OK', value: c.OK ?? 0, tone: 'success', icon: 'circle-check' }),
        stat({ label: 'WA', value: c.WA ?? 0, tone: (c.WA ? 'danger' : undefined), icon: 'x' }),
        stat({ label: 'TLE', value: c.TLE ?? 0, tone: (c.TLE ? 'warn' : undefined), icon: 'timer' }),
        stat({ label: 'RE', value: (c.RE ?? 0) + (c.MLE ?? 0), tone: (c.RE || c.MLE ? 'danger' : undefined), icon: 'bug' }),
        stat({ label: 'Pominięte', value: c.SKIP ?? 0, tone: c.SKIP ? 'warn' : undefined, icon: 'skip-forward', sub: 'brute nie dał rady' }));
      if (s) {
        clear(timesBox).append(
          h('span', null, 'Rozwiązanie: maks ', h('b', null, fmtTime(s.sol_max)), ', śr. ', h('b', null, fmtTime(s.sol_avg))),
          h('span', null, 'Brute: maks ', h('b', null, fmtTime(s.brute_max)), ', śr. ', h('b', null, fmtTime(s.brute_avg))),
          s.max_size ? h('span', null, 'największy test: ', h('b', null, `${s.max_size} zn.`)) : null);
      }
    }

    function failureCard(ev, { title, icon: ic = 'bug', tone = 'danger' } = {}) {
      const name = ev.index >= 0 ? `test #${ev.index + 1}` : 'zmniejszony test';
      const save = () => {
        const base = `kontrprzyklad_${ev.index >= 0 ? ev.index + 1 : 'min'}`;
        download(base + '.in', ev.input);
        if (ev.expected) setTimeout(() => download(base + '.out', ev.expected), 300);
        store.get('tester_saved', []).then((list) => store.set('tester_saved', [{ input: ev.input, expected: ev.expected, output: ev.output, verdict: ev.verdict, time: Date.now() }, ...(list || [])].slice(0, 50)));
        toast('Zapisano test (.in / .out) i dodano do historii kontrprzykładów', 'success');
      };
      const actions = [
        ev.expected !== undefined && ev.expected !== null && ev.expected !== '' ? button('Otwórz w Diff', { icon: 'git-compare', size: 'sm', variant: 'ghost',
          onClick: () => ctx.navigate('diff', { a: ev.output || '', b: ev.expected || '' }) }) : null,
        button('Zapisz test', { icon: 'download', size: 'sm', variant: 'ghost', onClick: save }),
        button('Debuguj', { icon: 'bug', size: 'sm', variant: 'ghost', title: 'Otwórz rozwiązanie z tym wejściem w Playgroundzie',
          onClick: () => ctx.navigate('playground', { code: eds.solution.getValue(), input: ev.input }) }),
      ].filter(Boolean);
      const body = h('div', { class: 'stack gap-s' },
        h('div', { class: 'row gap-s' }, verdictBadge(ev.verdict), h('span', { class: 'grow' }, ev.message || ''),
          ev.time !== undefined && ev.time !== null ? badge(`czas ${fmtTime(ev.time)}`, 'neutral', 'timer') : null,
          ev.size ? badge(`${ev.size} znaków`, 'neutral') : null),
        ev.checker_message ? callout(`**Checker:** ${ev.checker_message}`, 'info') : null,
        h('div', { class: 'ts-io' },
          codeBlock(ev.input, 'text', { title: 'Wejście', maxHeight: 260 }),
          codeBlock(ev.output || '', 'text', { title: 'Twoje wyjście', maxHeight: 260 }),
          ev.expected !== undefined && ev.expected !== null ? codeBlock(ev.expected, 'text', { title: 'Brute (oczekiwane)', maxHeight: 260 }) : null),
        ev.stderr && ev.stderr.trim() ? h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'stderr programu'),
          h('pre', { class: 'out err mt-s' }, ev.stderr)) : null,
        ev.diff && !ev.diff.equal ? diffResult(ev.diff, { maxHeight: '300px' }) : null,
        ev.spec && ev.spec.length ? h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Rozpoznany format wejścia'),
          h('ul', { class: 'small mt-s' }, ev.spec.map((l) => h('li', null, l)))) : null);
      return card({ title: title || `Błędny ${name}`, icon: ic, subtitle: ev.run >= 0 && ev.run !== undefined ? `uruchomienie generatora ${ev.run + 1}` : undefined,
        actions, body, class: 'ts-fail ' + tone });
    }

    function compileErrorCard(ev) {
      const items = (ev.explanations || []).filter((x) => x.severity !== 'note').slice(0, 6);
      return card({ title: `Błąd kompilacji: ${ev.name}`, icon: 'triangle-alert', class: 'ts-fail danger', body: h('div', { class: 'stack gap-s' },
        items.map((e) => h('div', { class: 'err-card' }, h('div', { class: 'ec-title' }, icon('circle-alert', 15), h('span', { class: 'grow' }, e.title || 'Błąd'),
          e.line ? h('span', { class: 'ec-line', onclick: () => { activeEd = ev.which; if (st.layout === 'tabs') layoutEditors(); eds[ev.which]?.scrollToLine?.(e.line); } }, `linia ${e.line}`) : null),
        e.explanation ? h('div', { class: 'ec-text' }, markdown(e.explanation)) : null, e.fix ? h('div', { class: 'ec-fix' }, markdown('**Jak naprawić:** ' + e.fix)) : null)),
        h('details', { open: !items.length }, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Komunikat kompilatora'), h('pre', { class: 'out err mt-s' }, ev.stderr || ''))) });
    }

    function onEvent(ev) {
      switch (ev.type) {
        case 'compiled':
          msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('check', 13), `${ev.name}: skompilowano${ev.cached ? ' (z pamięci podręcznej)' : ` w ${fmtTime(ev.time)}`}${ev.warnings ? `, ostrzeżeń: ${ev.warnings}` : ''}`));
          break;
        case 'compile_error': failBox.append(compileErrorCard(ev)); break;
        case 'generator':
          msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('shuffle', 13), `Generator (seed ${ev.seed}): ${ev.tests} ${plural(ev.tests, 'test', 'testy', 'testów')} w ${fmtTime(ev.time)}`));
          break;
        case 'generator_error':
          failBox.append(card({ title: 'Generator nie zadziałał', icon: 'triangle-alert', class: 'ts-fail danger', body: h('div', { class: 'stack gap-s' }, ev.message, ev.stderr ? h('pre', { class: 'out err' }, ev.stderr) : null) }));
          break;
        case 'warning': msgBox.append(callout(ev.message, 'warn')); break;
        case 'hint': msgBox.append(callout(ev.message, 'tip')); break;
        case 'test':
          testRows.push(ev);
          if (testRows.length <= 200) testTable.update(testRows.slice().sort((a, b) => a.index - b.index));
          break;
        case 'stats': renderCounters(ev); break;
        case 'failure': failBox.prepend(failureCard(ev, { title: `Pierwszy błędny test: #${ev.index + 1}` })); break;
        case 'minimized':
          if (!ev.same_as_first) failBox.append(failureCard(ev, { title: `Najmniejszy błędny test: #${ev.index + 1} (${ev.size} znaków)`, icon: 'target', tone: 'warn' }));
          else msgBox.append(callout(`Pierwszy błędny test jest zarazem najmniejszym (${ev.size} znaków) spośród sprawdzonych.`, 'info'));
          break;
        case 'shrink_start': msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('wand-sparkles', 13), ev.message)); break;
        case 'shrunk': failBox.append(failureCard(ev, { title: `Zmniejszony kontrprzykład (${ev.original_size} → ${ev.size} znaków)`, icon: 'wand-sparkles', tone: 'accent' })); break;
        case 'shrink_skipped': msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('info', 13), ev.message)); break;
        default: break;
      }
    }

    async function start() {
      if (running) return;
      const body = {
        solution: eds.solution.getValue(), brute: eds.brute.getValue(), generator: eds.generator.getValue(),
        langs: { solution: eds.solution.getLang(), brute: eds.brute.getLang(), generator: eds.generator.getLang() },
        mode: st.mode, tests_per_run: Number(st.tests) || 1000, tests: Number(st.tests) || 1000, runs: Number(st.runs) || 1,
        seed: st.seed === '' ? null : Number(st.seed), timeout: Number(st.timeout) || 2, compare: st.compare,
        float_eps: st.compare === 'float' ? (st.eps || '1e-6') : null, separator: st.separator || null,
        stop_on_first: st.stop, shrink: st.shrink,
      };
      if (st.checker === '__custom') {
        if (!customChecker.trim()) { editChecker(); return; }
        body.checker = customChecker;
      } else if (st.checker) body.checker = st.checker;
      running = true; startBtn.hidden = true; stopBtn.hidden = false;
      clear(msgBox); clear(failBox); clear(summaryBox); testRows.length = 0; testTable.update([]); renderCounters(null); clear(timesBox);
      prog.set(0, 'Start…');
      const t0 = performance.now();
      ctl = api.job('/api/tester/start', body, { onEvent, onProgress: (p, m) => prog.set(p, m || '') });
      try {
        const res = await ctl.promise;
        prog.set(1, res.message || 'Gotowe');
        const okAll = res.status === 'OK';
        clear(summaryBox).append(h('div', { class: ['ts-summary', okAll ? 'ok' : res.status === 'CE' || res.status === 'ERROR' ? 'bad' : 'bad'].join(' ') },
          icon(okAll ? 'circle-check' : 'circle-alert', 22),
          h('div', { class: 'grow' }, h('div', { class: 'bold' }, res.message || res.status),
            h('div', { class: 'muted small' }, [`seed ${res.seed}`, res.elapsed ? `czas ${fmtTime(res.elapsed)}` : null,
              res.stats?.done ? `${Math.round(res.stats.done / Math.max(0.001, (performance.now() - t0) / 1000))} testów/s` : null].filter(Boolean).join(' · '))),
          res.seed !== undefined ? button('Powtórz z tym seedem', { size: 'sm', variant: 'ghost', icon: 'rotate-ccw', onClick: () => { st.seed = String(res.seed); seedInp.value = st.seed; saveOpts(); start(); } }) : null));
      } catch (e) {
        if (e.cancelled) { prog.set(0, 'Anulowano'); clear(summaryBox).append(callout('Testowanie przerwane.', 'info')); } else clear(summaryBox).append(callout(e.message, 'danger'));
      } finally {
        running = false; startBtn.hidden = false; stopBtn.hidden = true; ctl = null;
      }
    }

    // ---------------------------------------------------------------- układ
    // na wierzchu tylko to, co zmienia się często; reszta pod „Więcej opcji”
    const settings = h('div', { class: 'ts-settings' },
      testsField, field('Limit czasu [s]', num('timeout', { min: 0.1, step: 0.1, width: '76px' })),
      more('Więcej opcji (tryb, seed, porównanie, checker…)', [
        field('Tryb', modeSeg), runsField, field('Seed', seedInp),
        field('Porównanie', h('div', { class: 'row gap-s' }, cmpSel, epsWrap)), field('Checker', chkSel), sepField,
        h('div', { class: 'stack gap-s ts-checks' }, stopBox, shrinkBox)], { key: 'tester' }));
    const left = h('div', { class: 'ts-left' },
      h('div', { class: 'row between ts-ed-head' }, tabsBar, h('div', { class: 'row gap-s' }, genMenu, layoutSeg)),
      edArea);
    const right = h('div', { class: 'ts-right stack' },
      h('div', { class: 'row gap-s' }, startBtn, stopBtn, h('span', { class: 'muted tiny' }, 'albo Ctrl+Enter w edytorze')),
      card({ title: 'Ustawienia', icon: 'settings', body: settings }),
      prog.el, counters, timesBox, summaryBox, msgBox, failBox,
      card({ title: 'Testy', icon: 'table', subtitle: 'Pierwsze 200 wyników (kolejność po zakończeniu)', padded: false, body: testTable }));
    root.append(viewHeader(this), h('div', { class: 'ts-layout' }, left, right));
    renderCounters(null);
    layoutEditors();

    self._fill = (p) => {
      let any = false;
      // gotowe testy (np. z „Testów brzegowych”) → generator w Pythonie wypisujący je rozdzielone pustą linią
      const fixed = Array.isArray(p.tests) ? p.tests.map((t) => (typeof t === 'string' ? t : t && t.input)).filter((t) => typeof t === 'string' && t.trim())
        : (typeof p.input === 'string' && p.input.trim() ? [p.input] : []);
      if (fixed.length && !p.generator) {
        p = { ...p, generator: `# Testy przekazane z innego narzędzia (${fixed.length}) — każde uruchomienie wypisuje je wszystkie\nTESTS = ${JSON.stringify(fixed.map((t) => t.replace(/\s+$/, '')))}\nprint("\\n\\n".join(TESTS))\n`, langs: { ...(p.langs || {}), generator: 'python' } };
      }
      for (const r of ROLES) {
        const v = p[r.id] ?? (r.id === 'solution' ? p.code : undefined);
        if (typeof v === 'string' && v.trim()) { eds[r.id].setValue(v); eds[r.id].setLang(p.langs?.[r.id] || (/^\s*(def |import |print\(|for .* in )/m.test(v) && !/#include/.test(v) ? 'python' : 'cpp')); any = true; }
      }
      if (any) { toast('Wczytano kod do testerki', 'info', { timeout: 1500 }); layoutEditors(); }
    };
    if (self._pending) { self._fill(self._pending); self._pending = null; }
  },

  onShow(params) {
    if (!params || !(params.solution || params.brute || params.generator || params.code || params.tests || params.input)) return;
    if (this._fill) this._fill(params); else this._pending = params;
  },
};

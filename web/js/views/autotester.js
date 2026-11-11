// (z) Auto-testerka: sama buduje testerkę z przykładów — wnioskuje format, generuje testy różnymi strategiami,
// porównuje z brute’em albo robi crash-test (RE/TLE/UB z sanitizerami) i zmniejsza kontrprzykład.
import { h, icon, button, iconButton, viewHeader, card, field, input, textarea, select, checkbox, badge, verdictBadge,
  progressBar, stat, table, callout, codeBlock, clear, toast, fmtTime, emptyState, fileDrop, plural } from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';
import { readStatementFile } from '../pdf.js';
import { diffResult } from './diff.js';

const SAMPLE_PLACEHOLDER_IN = 'Wejście przykładu, np.\n5\n1 2 3 4 5';
const SAMPLE_PLACEHOLDER_OUT = 'Oczekiwane wyjście (opcjonalnie)';

function confTone(c) { return c >= 0.85 ? 'success' : c >= 0.6 ? 'warn' : 'danger'; }

export default {
  id: 'autotester', title: 'Auto-testerka', icon: 'wand-sparkles', group: 'Testowanie', order: 20,
  description: 'Rozpoznaje format wejścia z przykładów, sama generuje testy (losowe, skrajne, drzewa-ścieżki…) i szuka błędu — z brute’em albo bez.',
  keywords: ['auto', 'generator', 'format wejścia', 'crash test', 'sanitizer', 'ub', 'shrinking', 'kontrprzykład'],

  async render(root, ctx) {
    const self = this;
    const saved = local.get('autotester:state', {}) || {};
    let samples = saved.samples && saved.samples.length ? saved.samples : [{ input: '', output: '' }];
    let statement = saved.statement || '';
    let spec = saved.spec || null;
    let strategiesAll = [];
    let sizes = [];
    try { const r = await api.get('/api/autotester/strategies', null, { quiet: true }); strategiesAll = r.strategies; sizes = r.sizes; } catch { /* */ }
    const chosen = new Set(saved.strategies || ['random', 'random_full', 'min', 'max', 'extreme']);
    const opt = Object.assign({ count: 300, timeout: 2, maxSize: 'large', sanitize: true }, saved.opt || {});
    const persist = () => local.set('autotester:state', { samples, statement, spec, strategies: [...chosen], opt });

    // ---------------------------------------------------------------- przykłady
    const samplesBox = h('div', { class: 'stack gap-s' });
    function renderSamples() {
      clear(samplesBox);
      samples.forEach((s, i) => {
        const tin = textarea({ value: s.input, rows: 5, placeholder: SAMPLE_PLACEHOLDER_IN, onInput: (v) => { s.input = v; persist(); } });
        const tout = textarea({ value: s.output || '', rows: 5, placeholder: SAMPLE_PLACEHOLDER_OUT, onInput: (v) => { s.output = v; persist(); } });
        samplesBox.append(h('div', { class: 'at-sample' },
          h('div', { class: 'row between' }, h('b', { class: 'small' }, s.name || `Przykład ${i + 1}`),
            samples.length > 1 ? iconButton('x', { title: 'Usuń przykład', onClick: () => { samples.splice(i, 1); persist(); renderSamples(); } }) : null),
          h('div', { class: 'grid-2' }, tin, tout)));
      });
    }
    const stTa = textarea({ value: statement, rows: 6, mono: false, placeholder: 'Opcjonalnie: wklej treść zadania (albo upuść PDF/HTML) — z niej biorę ograniczenia (np. 1 ≤ n ≤ 10^5) i przykłady.',
      onInput: (v) => { statement = v; persist(); } });
    const stDrop = fileDrop({ accept: '.pdf,.txt,.html,.htm', label: 'Upuść treść zadania (PDF, HTML, TXT)', onFiles: async (f) => {
      try {
        const name = (f.name || '').toLowerCase();
        statement = name.endsWith('.html') || name.endsWith('.htm') ? await f.text() : await readStatementFile(f);
        stTa.value = statement; persist();
        toast('Wczytano treść zadania', 'success', { timeout: 1500 });
        await extractSamples(true);
      } catch (e) { toast('Nie udało się odczytać pliku: ' + e.message, 'error'); }
    } });
    async function extractSamples(quiet) {
      if (!statement.trim()) { toast('Najpierw wklej treść zadania.', 'warn'); return; }
      const r = await api.post('/api/autotester/extract', { statement });
      if (!r.count) { if (!quiet) toast('Nie znalazłem przykładów w treści.', 'warn'); return; }
      samples = r.samples.map((s) => ({ input: s.input, output: s.output, name: s.name }));
      persist(); renderSamples();
      toast(`Wyciągnięto ${r.count} ${plural(r.count, 'przykład', 'przykłady', 'przykładów')} z treści`, 'success');
    }

    // ---------------------------------------------------------------- specyfikacja
    const specBox = h('div', { class: 'stack' });
    const specJson = textarea({ rows: 14, placeholder: 'Specyfikacja formatu (JSON) pojawi się po wnioskowaniu.' });
    specJson.classList.add('at-json');
    const exampleBox = h('div');
    const exStrategy = select(strategiesAll.map((s) => ({ value: s.id, label: s.name })), { value: 'random', size: 'sm' });
    const exSize = select(sizes.map((s) => ({ value: s.id, label: s.name })), { value: 'small', size: 'sm' });

    async function infer() {
      const smp = samples.filter((s) => s.input.trim());
      if (!smp.length && !statement.trim()) { toast('Dodaj przykład wejścia albo treść zadania.', 'warn'); return; }
      const r = await api.post('/api/autotester/infer', { samples: smp, statement });
      if (r.extracted) { samples = r.samples.map((s) => ({ input: s.input, output: s.output || '', name: s.name })); renderSamples(); }
      spec = r.spec;
      if (r.suggested_strategies && !saved.strategies) { chosen.clear(); r.suggested_strategies.forEach((x) => chosen.add(x)); renderStrategies(); }
      persist();
      renderSpec(r);
    }
    function renderSpec(r) {
      clear(specBox);
      if (!spec) { specBox.append(emptyState({ icon: 'scan-search', title: 'Format nieznany', text: 'Kliknij „Wnioskuj format” — rozpoznam strukturę wejścia z przykładów.' })); return; }
      specJson.value = JSON.stringify(spec, null, 2);
      const conf = r?.confidence;
      specBox.append(
        h('div', { class: 'row gap-s' }, h('b', null, 'Rozpoznany format'),
          conf !== undefined && conf !== null ? badge(`pewność ${Math.round(conf * 100)}%`, confTone(conf)) : null,
          (r?.sample_parse || []).map((ok, i) => badge(`przykład ${i + 1}`, ok ? 'success' : 'danger', ok ? 'check' : 'x'))),
        h('ol', { class: 'at-desc' }, (r?.description || []).map((l) => h('li', { class: l.startsWith('  ') ? 'nested' : '' }, l.trim()))),
        (r?.warnings || []).map((w) => callout(w, 'warn')),
        h('div', { class: 'row gap-s' }, h('span', { class: 'muted small' }, 'Przykładowy test:'), exStrategy, exSize,
          button('Losuj', { icon: 'shuffle', size: 'sm', onClick: () => regen() })),
        exampleBox,
        h('details', { class: 'at-spec-edit' }, h('summary', null, icon('braces', 14), ' Edytuj specyfikację (JSON)'),
          h('div', { class: 'stack gap-s mt-s' },
            h('div', { class: 'muted small' }, 'Możesz poprawić zakresy (np. „max”: 100000 albo „n”), kolejność („order”: „sorted”), alfabet, właściwości grafu („tree”, „connected”, „simple”).'),
            specJson,
            h('div', { class: 'row gap-s' }, button('Zastosuj zmiany', { icon: 'check', variant: 'primary', size: 'sm', onClick: () => applySpec() }),
              button('Przywróć', { icon: 'rotate-ccw', variant: 'ghost', size: 'sm', onClick: () => { specJson.value = JSON.stringify(spec, null, 2); } })))));
      if (r?.example) exampleBox.replaceChildren(codeBlock(r.example, 'text', { title: 'Test (losowe, małe)', maxHeight: 220 }));
      else regen();
    }
    async function regen() {
      if (!spec) return;
      try {
        const r = await api.post('/api/autotester/generate', { spec, strategy: exStrategy.value, size: exSize.value, seed: Math.floor(Math.random() * 1e6) });
        exampleBox.replaceChildren(codeBlock(r.input, 'text', { title: `Test (${exStrategy.selectedOptions[0]?.text || ''}, ${exSize.selectedOptions[0]?.text || ''})`, maxHeight: 260 }),
          r.valid ? null : callout('Wygenerowany test nie spełnia specyfikacji: ' + r.error, 'warn'));
      } catch { /* toast już pokazany */ }
    }
    async function applySpec() {
      let s;
      try { s = JSON.parse(specJson.value); } catch (e) { toast('To nie jest poprawny JSON: ' + e.message, 'error'); return; }
      const v = await api.post('/api/autotester/validate', { spec: s, samples: samples.filter((x) => x.input.trim()) });
      spec = s; persist();
      renderSpec({ description: v.description, sample_parse: v.results.map((x) => x.valid), warnings: v.results.filter((x) => !x.valid).map((x) => `${x.name}: ${x.error}`) });
      toast('Zastosowano specyfikację', 'success', { timeout: 1500 });
    }

    // ---------------------------------------------------------------- strategie i opcje
    const stratBox = h('div', { class: 'row gap-s at-strats' });
    function renderStrategies() {
      clear(stratBox);
      for (const s of strategiesAll) {
        const on = chosen.has(s.id);
        stratBox.append(h('button', { type: 'button', class: ['chip', on ? 'active' : ''].join(' '), title: s.description,
          onclick: () => { if (on) chosen.delete(s.id); else chosen.add(s.id); persist(); renderStrategies(); } }, on ? icon('check', 13) : null, s.name));
      }
    }
    const countInp = input({ value: opt.count, type: 'number', mono: true, size: 'sm', onInput: (v) => { opt.count = Number(v) || 300; persist(); } });
    countInp.style.width = '90px';
    const toInp = input({ value: opt.timeout, type: 'number', mono: true, size: 'sm', onInput: (v) => { opt.timeout = Number(v) || 2; persist(); }, attrs: { step: 0.1, min: 0.1 } });
    toInp.style.width = '80px';
    const maxSel = select(sizes.map((s) => ({ value: s.id, label: s.name })), { value: opt.maxSize, size: 'sm', onChange: (v) => { opt.maxSize = v; persist(); } });
    const sanBox = checkbox('Sanitizery (wykrywanie UB)', { checked: opt.sanitize, onChange: (v) => { opt.sanitize = v; persist(); },
      title: 'Dodatkowo uruchamia wersję z AddressSanitizer + UBSan — wykrywa wyjście poza tablicę i przepełnienia nawet gdy program „przechodzi”' });

    // ---------------------------------------------------------------- kod
    const edSol = h('div', { class: 'at-ed' });
    const edBrute = h('div', { class: 'at-ed' });
    const langs = local.get('autotester:langs', {}) || {};
    const keepLang = (k) => (l) => { langs[k] = l; local.set('autotester:langs', langs); };
    const sol = createEditor(edSol, { lang: langs.sol || 'cpp', height: '100%', storageKey: 'autotester-sol', langSelect: true, title: 'Rozwiązanie',
      onLangChange: keepLang('sol'), onRun: () => startTest() });
    const brute = createEditor(edBrute, { lang: langs.brute || 'cpp', height: '100%', storageKey: 'autotester-brute', langSelect: true, title: 'Brute (opcjonalnie)',
      onLangChange: keepLang('brute'),
      placeholder: 'Bez brute’a: crash-test (RE / TLE / UB) + zgodność z przykładowymi wyjściami.', onRun: () => startTest() });

    // ---------------------------------------------------------------- wyniki
    const prog = progressBar({ label: 'Gotowy' });
    const counters = h('div', { class: 'at-counters' });
    const resultBox = h('div', { class: 'stack' });
    const msgBox = h('div', { class: 'stack gap-s' });
    const rows = [];
    const testsTable = table([
      { key: 'index', label: '#', render: (r) => String(r.index + 1) },
      { key: 'strategy', label: 'Strategia', render: (r) => (strategiesAll.find((s) => s.id === r.strategy)?.name || r.strategy) },
      { key: 'size_class', label: 'Rozmiar', render: (r) => r.size_class },
      { key: 'verdict', label: 'Werdykt', render: (r) => verdictBadge(r.verdict === 'UB' ? 'RE' : r.verdict, r.verdict === 'UB' ? 'UB' : undefined) },
      { key: 'time', label: 'Czas', align: 'right', render: (r) => fmtTime(r.time) },
    ], [], { compact: true, maxHeight: '300px', empty: 'Tu pojawią się wyniki testów.' });
    let ctl = null;
    const testBtn = button('Testuj', { icon: 'play', variant: 'primary', size: 'lg', onClick: () => startTest() });
    const stopBtn = button('Zatrzymaj', { icon: 'circle-stop', variant: 'danger', size: 'lg', onClick: () => ctl && ctl.cancel() });
    stopBtn.hidden = true;

    function renderCounters(ev) {
      const c = ev?.counts || {};
      clear(counters).append(
        stat({ label: 'Testy', value: ev ? `${ev.done} / ${ev.total}` : '—', icon: 'list-checks' }),
        stat({ label: 'OK', value: c.OK ?? 0, tone: 'success', icon: 'circle-check' }),
        stat({ label: 'WA', value: c.WA ?? 0, tone: c.WA ? 'danger' : undefined, icon: 'x' }),
        stat({ label: 'RE / UB', value: (c.RE ?? 0) + (c.UB ?? 0) + (c.MLE ?? 0), tone: (c.RE || c.UB) ? 'danger' : undefined, icon: 'bug' }),
        stat({ label: 'TLE', value: c.TLE ?? 0, tone: c.TLE ? 'warn' : undefined, icon: 'timer' }),
        stat({ label: 'Maks. czas', value: ev ? fmtTime(ev.sol_max) : '—', icon: 'gauge' }));
    }
    function failureCard(ev, title, ic, tone) {
      return card({ title, icon: ic, class: 'at-fail ' + tone,
        actions: [
          ev.expected ? button('Otwórz w Diff', { icon: 'git-compare', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('diff', { a: ev.output || '', b: ev.expected || '' }) }) : null,
          button('Do Testerki', { icon: 'flask-conical', size: 'sm', variant: 'ghost', title: 'Przenieś rozwiązanie i brute do Testerki',
            onClick: () => ctx.navigate('tester', { solution: sol.getValue(), brute: brute.getValue() }) }),
        ].filter(Boolean),
        body: h('div', { class: 'stack gap-s' },
          h('div', { class: 'row gap-s' }, verdictBadge(ev.verdict === 'UB' ? 'RE' : ev.verdict, ev.verdict === 'UB' ? 'UB' : undefined), h('span', { class: 'grow' }, ev.message),
            ev.strategy ? badge(strategiesAll.find((s) => s.id === ev.strategy)?.name || ev.strategy, 'info') : null, badge(`${ev.size} znaków`)),
          ev.explanation ? callout(`**Co się stało:** ${ev.explanation}`, 'danger') : null,
          h('div', { class: 'at-io' }, codeBlock(ev.input, 'text', { title: 'Wejście', maxHeight: 240 }),
            codeBlock(ev.output || '', 'text', { title: 'Twoje wyjście', maxHeight: 240 }),
            ev.expected !== null && ev.expected !== undefined ? codeBlock(ev.expected, 'text', { title: 'Brute', maxHeight: 240 }) : null),
          ev.sanitizer ? h('details', { open: true }, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Raport sanitizera'), h('pre', { class: 'out err mt-s' }, ev.sanitizer)) : null,
          !ev.sanitizer && ev.stderr ? h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'stderr'), h('pre', { class: 'out err mt-s' }, ev.stderr)) : null,
          ev.diff && !ev.diff.equal ? diffResult(ev.diff, { maxHeight: '260px', tableB: 'Brute' }) : null) });
    }
    function onEvent(ev) {
      switch (ev.type) {
        case 'compiled': msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('check', 13), `${ev.name}: skompilowano${ev.cached ? ' (z pamięci podręcznej)' : ''}`)); break;
        case 'compile_error':
          resultBox.append(card({ title: `Błąd kompilacji: ${ev.name}`, icon: 'triangle-alert', class: 'at-fail danger',
            body: h('div', { class: 'stack gap-s' }, (ev.explanations || []).slice(0, 5).map((e) => callout(`**${e.title || 'Błąd'}**${e.line ? ` (linia ${e.line})` : ''} — ${e.explanation || ''}`, 'danger')),
              h('pre', { class: 'out err' }, ev.stderr || '')) }));
          break;
        case 'warning': msgBox.append(callout(ev.message, 'warn')); break;
        case 'hint': msgBox.append(callout(ev.message, 'tip')); break;
        case 'sample':
          msgBox.append(h('div', { class: 'row gap-s small' }, verdictBadge(ev.verdict), h('span', null, `Przykład „${ev.name}”`),
            ev.verdict === 'OK' ? h('span', { class: 'muted' }, fmtTime(ev.time)) : h('span', { class: 'tone-danger' }, ev.first?.message || ev.explanation || ''),
            ev.format_ok === false ? badge('nie pasuje do formatu', 'warn') : null));
          if (ev.diff && !ev.diff.equal) msgBox.append(diffResult(ev.diff, { maxHeight: '200px', tableB: 'Oczekiwane (z treści)' }));
          break;
        case 'test': rows.push(ev); testsTable.update(rows.slice().sort((a, b) => a.index - b.index)); break;
        case 'stats': renderCounters(ev); break;
        case 'failure': resultBox.prepend(failureCard(ev, `Znaleziony błąd: test #${ev.index + 1}`, 'bug', 'danger')); break;
        case 'shrink_start': msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('wand-sparkles', 13), ev.message)); break;
        case 'shrunk': resultBox.append(failureCard(ev, `Minimalny kontrprzykład (${ev.original_size} → ${ev.size} znaków)`, 'target', 'accent')); break;
        case 'shrink_skipped': msgBox.append(h('div', { class: 'muted small row gap-s' }, icon('info', 13), ev.message)); break;
        default: break;
      }
    }
    async function startTest() {
      if (ctl) return;
      if (!spec) { await infer(); if (!spec) return; }
      if (!chosen.size) { toast('Wybierz co najmniej jedną strategię.', 'warn'); return; }
      clear(resultBox); clear(msgBox); rows.length = 0; testsTable.update([]); renderCounters(null);
      testBtn.hidden = true; stopBtn.hidden = false;
      prog.set(0, 'Start…');
      ctl = api.job('/api/autotester/start', {
        solution: sol.getValue(), brute: brute.getValue(), spec, strategies: [...chosen], count: opt.count, timeout: opt.timeout,
        samples: samples.filter((s) => s.input.trim()), sanitize: opt.sanitize, max_size: opt.maxSize,
        langs: { solution: sol.getLang(), brute: brute.getLang() },
      }, { onEvent, onProgress: (p, m) => prog.set(p, m || '') });
      try {
        const res = await ctl.promise;
        prog.set(1, res.message);
        const ok = res.status === 'OK';
        resultBox.prepend(h('div', { class: ['at-summary', ok ? 'ok' : 'bad'].join(' ') }, icon(ok ? 'circle-check' : 'circle-alert', 22),
          h('div', { class: 'grow' }, h('div', { class: 'bold' }, res.message), h('div', { class: 'muted small' }, `${res.mode} · seed ${res.seed} · ${fmtTime(res.elapsed)}`)),
          res.per_strategy ? h('div', { class: 'row gap-s' }, Object.entries(res.per_strategy).filter(([, v]) => v.OK + v.FAIL + v.SKIP > 0).map(([k, v]) =>
            badge(`${strategiesAll.find((s) => s.id === k)?.name || k}: ${v.OK}/${v.OK + v.FAIL + v.SKIP}`, v.FAIL ? 'danger' : 'success'))) : null));
      } catch (e) {
        if (e.cancelled) prog.set(0, 'Anulowano'); else resultBox.prepend(callout(e.message, 'danger'));
      } finally { ctl = null; testBtn.hidden = false; stopBtn.hidden = true; }
    }

    // ---------------------------------------------------------------- układ
    renderSamples(); renderStrategies(); renderCounters(null);
    const step = (n, title, sub) => h('div', { class: 'at-step' }, h('span', { class: 'at-step-n' }, String(n)), h('div', null, h('div', { class: 'bold' }, title), sub ? h('div', { class: 'muted small' }, sub) : null));
    root.append(
      viewHeader(this, button('Wnioskuj format', { icon: 'scan-search', onClick: () => infer() }), testBtn, stopBtn),
      h('div', { class: 'at-grid' },
        h('div', { class: 'stack' },
          card({ title: 'Przykłady', icon: 'file-input', subtitle: 'Wejścia (i wyjścia) z treści — im więcej, tym pewniejsze wnioskowanie',
            actions: [button('Dodaj', { icon: 'plus', size: 'sm', variant: 'ghost', onClick: () => { samples.push({ input: '', output: '' }); persist(); renderSamples(); } })],
            body: samplesBox }),
          card({ title: 'Treść zadania', icon: 'file-text', subtitle: 'Opcjonalnie — ograniczenia (zakresy) i przykłady',
            actions: [button('Wyciągnij przykłady', { icon: 'download', size: 'sm', variant: 'ghost', onClick: () => extractSamples(false) })],
            body: h('div', { class: 'stack gap-s' }, stTa, stDrop) }),
          card({ title: 'Specyfikacja wejścia', icon: 'braces', body: specBox,
            actions: [button('Wnioskuj format', { icon: 'scan-search', size: 'sm', variant: 'primary', onClick: () => infer() })] })),
        h('div', { class: 'stack' },
          card({ title: 'Strategie i ustawienia', icon: 'settings', body: h('div', { class: 'stack gap-s' }, stratBox,
            h('div', { class: 'row gap-l mt-s' }, field('Liczba testów', countInp), field('Limit czasu [s]', toInp), field('Maks. rozmiar', maxSel), sanBox)) }),
          h('div', { class: 'at-editors' }, edSol, edBrute),
          h('div', { class: 'stack gap-s' }, step(1, 'Przykłady → format', 'Wnioskowanie rozpoznaje liczności, tablice, grafy, siatki, multitesty…'),
            step(2, 'Strategie → testy', 'Małe losowe, skrajne wartości, posortowane, ścieżki, gwiazdy…'),
            step(3, 'Błąd → minimalny kontrprzykład', 'Delta debugging zmniejsza n i wartości, zachowując poprawny format.')))),
      h('div', { class: 'stack mt-l' }, prog.el, counters, resultBox, msgBox,
        card({ title: 'Testy', icon: 'table', subtitle: 'Pierwsze 200 wyników', padded: false, body: testsTable })));
    renderSpec(spec ? { description: [], sample_parse: [] } : null);
    if (spec) { try { const v = await api.post('/api/autotester/validate', { spec, samples: samples.filter((x) => x.input.trim()) }, { quiet: true }); renderSpec({ description: v.description, sample_parse: v.results.map((x) => x.valid) }); } catch { /* */ } }

    self._fill = (p) => {
      if (p.solution || p.code) sol.setValue(p.solution || p.code);
      if (p.brute) brute.setValue(p.brute);
      if (p.statement) { statement = p.statement; stTa.value = statement; }
      if (Array.isArray(p.samples) && p.samples.length) { samples = p.samples.map((s) => (typeof s === 'string' ? { input: s, output: '' } : { input: s.input || '', output: s.output || '', name: s.name })); renderSamples(); }
      persist();
      if (p.samples || p.statement) infer().catch(() => {});
    };
    if (self._pending) { self._fill(self._pending); self._pending = null; }
  },

  onShow(params) {
    if (!params || !(params.solution || params.code || params.brute || params.samples || params.statement)) return;
    if (this._fill) this._fill(params); else this._pending = params;
  },
};

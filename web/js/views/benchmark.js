// Porównanie rozwiązań (benchmark): czasy A i B na rosnących testach, zgodność wyników, złożoność i werdykt.
import {
  h, icon, button, viewHeader, card, field, input, select, segmented, toast, callout, stat, table, verdictBadge,
  badge, progressBar, outputPanel, clear, debounce, emptyState, fmtTime, fmtMem,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';
import { lineChart } from '../charts.js';
import { setupDslEditor } from './gendsl.js';

const DEF_A = `#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    vector<long long> a(n);
    for (auto& x : a) cin >> x;
    // sortowanie bąbelkowe: O(n²)
    for (int i = 0; i < n; i++)
        for (int j = 0; j + 1 < n - i; j++)
            if (a[j] > a[j + 1]) swap(a[j], a[j + 1]);
    long long s = 0;
    for (int i = 0; i < n; i++) s += a[i] % 1000 * (i + 1);
    cout << s << '\\n';
}
`;
const DEF_B = DEF_A.replace(/\/\/ sortowanie bąbelkowe[\s\S]*?swap\(a\[j\], a\[j \+ 1\]\);\n/, 'sort(a.begin(), a.end());   // O(n log n)\n');
const DEF_GEN_DSL = '# size = bieżący rozmiar testu (z listy rozmiarów)\nn = size\nprint n\nprint array(n, int(1, 1e9))\n';
const DEF_GEN_CODE = `// Generator jednego testu: argv[1] = rozmiar, argv[2] = ziarno
#include <bits/stdc++.h>
using namespace std;

int main(int argc, char** argv) {
    int n = argc > 1 ? atoi(argv[1]) : 10;
    mt19937 rng(argc > 2 ? atoi(argv[2]) : 1);
    cout << n << '\\n';
    for (int i = 0; i < n; i++) cout << rng() % 1000000000 + 1 << " \\n"[i == n - 1];
}
`;

function fmtN(n) {
  if (n >= 1000) {
    const e = Math.floor(Math.log10(n));
    const m = n / 10 ** e;
    if (Math.abs(m - Math.round(m)) < 1e-9) return Math.round(m) === 1 ? `10^${e}` : `${Math.round(m)}·10^${e}`;
  }
  return String(n);
}
const supN = (n) => fmtN(n).replace(/\^(\d+)/, (_, d) => d.split('').map((c) => '⁰¹²³⁴⁵⁶⁷⁸⁹'[+c]).join(''));
function bytes(b) { return b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1).replace('.', ',')} KB` : `${(b / 1048576).toFixed(1).replace('.', ',')} MB`; }

let VIEW = null;

export default {
  id: 'benchmark', title: 'Porównanie rozwiązań', icon: 'gauge', group: 'Testowanie', order: 50,
  description: 'Uruchamia dwa rozwiązania na rosnących testach: wykres czasów, pamięć, zgodność wyników i szacowana złożoność — „B jest ~3,4× szybsze dla n ≥ 10⁵”.',
  keywords: ['benchmark', 'porównanie', 'czas', 'złożoność', 'wykres', 'szybkość', 'wydajność', 'tle'],

  render(root, ctx) {
    const st = { genKind: local.get('bench:genKind', 'dsl'), running: null, rows: [], templates: [] };
    VIEW = { st };

    // ----------------------------------------------------------------------- nagłówek
    const tplSel = select([{ value: '', label: 'Wczytaj przykład…' }], { size: 'sm', onChange: (v) => { if (v) loadTemplate(v); tplSel.value = ''; } });
    tplSel.style.width = 'auto';
    const startBtn = button('Start', { icon: 'play', variant: 'primary', onClick: () => start(), title: 'Uruchom porównanie' });
    const cancelBtn = button('Anuluj', { icon: 'circle-stop', variant: 'danger', onClick: () => st.running && st.running.cancel() });
    cancelBtn.hidden = true;
    root.append(viewHeader(this, tplSel, cancelBtn, startBtn));

    // ----------------------------------------------------------------------- edytory A / B
    const mkSol = (key, title, def, sub) => {
      const host = h('div', { class: 'bm-ed' });
      const out = outputPanel();
      out.el.classList.add('bm-ce');
      const c = card({ title, icon: 'file-code', subtitle: sub, body: [host, out.el], class: 'bm-sol' });
      const ed = createEditor(host, { value: def, lang: local.get(`bench:lang_${key}`, 'cpp'), height: '320px', storageKey: `bench:${key}`, langSelect: true,
        onLangChange: (l) => local.set(`bench:lang_${key}`, l) });
      out.el.addEventListener('gotoline', (e) => ed.scrollToLine(e.detail));
      return { card: c, ed, out };
    };
    const A = mkSol('a', 'Rozwiązanie A', DEF_A, 'np. brute / wolniejsze');
    const B = mkSol('b', 'Rozwiązanie B', DEF_B, 'np. wzorcówka / szybsze (puste = mierz tylko A)');
    VIEW.A = A; VIEW.B = B;

    // ----------------------------------------------------------------------- generator
    const genHost = h('div', { class: 'bm-ed' });
    const genHint = h('div', { class: 'muted small' });
    const genOut = outputPanel();
    genOut.el.classList.add('bm-ce');
    const toDsl = button('Otwórz w generatorze DSL', { icon: 'wand-sparkles', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('gendsl', { source: genEd.getValue() }) });
    const kindSeg = segmented([{ value: 'dsl', label: 'Mini-język (size)', icon: 'wand-sparkles' }, { value: 'code', label: 'Program (argv[1] = rozmiar)', icon: 'file-code' }],
      { value: st.genKind, onChange: (v) => setGenKind(v, true) });
    const genCard = card({ title: 'Generator testu', icon: 'shuffle', actions: [toDsl], body: h('div', { class: 'stack gap-s' }, kindSeg, genHost, genHint, genOut.el) });
    const genEd = createEditor(genHost, { value: st.genKind === 'dsl' ? DEF_GEN_DSL : DEF_GEN_CODE, lang: st.genKind === 'dsl' ? 'text' : 'cpp', height: '210px',
      storageKey: `bench:gen_${st.genKind}` });
    VIEW.genEd = genEd;
    const origKeys = genEd.cm.getOption('extraKeys');
    const origFormat = genEd.format;
    function setGenKind(v, swap) {
      if (swap && v !== st.genKind) {
        local.set(`ed:bench:gen_${st.genKind}`, genEd.getValue());
        const stored = local.get(`ed:bench:gen_${v}`, null);
        genEd.setValue(stored ?? (v === 'dsl' ? DEF_GEN_DSL : DEF_GEN_CODE));
      }
      st.genKind = v;
      local.set('bench:genKind', v);
      if (v === 'dsl') { genEd.lang = 'text'; setupDslEditor(genEd); }
      else {
        genEd.cm.setOption('extraKeys', origKeys);
        genEd.format = origFormat;
        genEd.setLang(/^\s*(import|from|def)\b/m.test(genEd.getValue()) && !/#include/.test(genEd.getValue()) ? 'python' : 'cpp');
      }
      toDsl.hidden = v !== 'dsl';
      clear(genHint);
      genHint.append(v === 'dsl'
        ? h('span', null, 'Zmienna ', h('code', null, 'size'), ' przyjmuje kolejne rozmiary z listy. Generowany jest ', h('b', null, 'jeden'), ' test na rozmiar (instrukcja tests jest pomijana).')
        : h('span', null, 'Program dostaje rozmiar w ', h('code', null, 'argv[1]'), ' i ziarno w ', h('code', null, 'argv[2]'), ' i wypisuje jeden test (C++ albo Python).'));
    }
    setGenKind(st.genKind, false);

    // ----------------------------------------------------------------------- ustawienia
    const sizesInp = input({ value: local.get('bench:sizes', '1e3, 2e3, 5e3, 1e4, 2e4'), mono: true, placeholder: '1e3, 1e4, 1e5, 2e5',
      onInput: (v) => { local.set('bench:sizes', v); checkSizes(); } });
    const sizesHint = h('div', { class: 'hint' });
    const repInp = input({ value: String(local.get('bench:repeats', 3)), mono: true, attrs: { inputmode: 'numeric' }, onInput: (v) => local.set('bench:repeats', v) });
    const toInp = input({ value: String(local.get('bench:timeout', 5)), mono: true, attrs: { inputmode: 'decimal' }, onInput: (v) => local.set('bench:timeout', v) });
    const seedInp = input({ value: String(local.get('bench:seed', 1)), mono: true, attrs: { inputmode: 'numeric' }, onInput: (v) => local.set('bench:seed', v) });
    const checkSizes = debounce(async () => {
      try {
        const r = await api.post('/api/benchmark/sizes', { text: sizesInp.value }, { quiet: true });
        sizesHint.textContent = `${r.sizes.length} ${r.sizes.length === 1 ? 'rozmiar' : r.sizes.length < 5 ? 'rozmiary' : 'rozmiarów'}: ${r.sizes.map(supN).join(', ')}`;
        sizesHint.className = 'hint';
      } catch (e) { sizesHint.textContent = e.message; sizesHint.className = 'hint tone-danger'; }
    }, 300);
    const settings = card({ title: 'Ustawienia pomiaru', icon: 'settings', body: h('div', { class: 'bm-settings' },
      field('Rozmiary testów (n)', sizesInp, sizesHint),
      field('Powtórzenia', repInp, 'mediana czasu z powtórzeń'),
      field('Limit czasu [s]', toInp, 'na jedno uruchomienie'),
      field('Ziarno', seedInp, 'generatora'),
      h('div', { class: 'bm-start' }, button('Start', { icon: 'play', variant: 'primary', size: 'lg', onClick: () => start() }))) });

    // ----------------------------------------------------------------------- wyniki
    const prog = progressBar({ label: 'Gotowe do startu' });
    prog.el.hidden = true;
    const results = h('div', { class: 'stack' });
    results.append(emptyState({ icon: 'gauge', title: 'Porównaj dwa rozwiązania', text: 'Wklej rozwiązania A i B, ustaw generator i rozmiary, a potem kliknij Start. Zobaczysz wykres czasów, tabelę i werdykt.' }));

    root.append(h('div', { class: 'stack gap-l bm-root' },
      h('div', { class: 'grid-2' }, A.card, B.card),
      h('div', { class: 'bm-row2' }, genCard, settings),
      prog.el, results));

    // ----------------------------------------------------------------------- start
    async function start() {
      if (st.running) { toast('Porównanie już trwa.', 'info'); return; }
      A.out.clear(); B.out.clear(); genOut.clear();
      const body = {
        a: A.ed.getValue(), b: B.ed.getValue(), lang_a: A.ed.getLang(), lang_b: B.ed.getLang(),
        generator: genEd.getValue(), gen_kind: st.genKind, gen_lang: st.genKind === 'code' ? genEd.getLang() : undefined,
        sizes: sizesInp.value, repeats: +repInp.value || 3, timeout: +toInp.value || 5, seed: +seedInp.value || 1,
      };
      if (!body.b.trim()) toast('Brak rozwiązania B — zmierzę tylko A.', 'info', { timeout: 2000 });
      st.rows = [];
      clear(results);
      const live = buildLive(!!body.b.trim());
      results.append(live.el);
      prog.el.hidden = false;
      prog.set(0, 'Start…');
      startBtn.hidden = true; cancelBtn.hidden = false;
      const ctl = api.job('/api/benchmark/start', body, {
        onProgress: (p, msg) => prog.set(p, msg),
        onEvent: (ev) => {
          if (ev.type === 'size') { st.rows.push(ev.row); live.addRow(ev.row); }
          else if (ev.type === 'compile_error') {
            const target = ev.which === 'a' ? A.out : ev.which === 'b' ? B.out : genOut;
            target.showCompile(ev.compile || { stderr: ev.message }, ev.source);
          } else if (ev.type === 'generator_error') {
            genOut.showText(`${ev.message}${ev.stderr ? '\n\n' + ev.stderr : ''}`, 'danger');
            if (ev.line) genEd.scrollToLine(ev.line);
          } else if (ev.type === 'warning') live.warn(ev.message);
        },
      });
      st.running = ctl;
      try {
        const res = await ctl.promise;
        prog.set(1, res.status === 'done' ? `Gotowe w ${String(res.elapsed).replace('.', ',')} s` : res.status === 'cancelled' ? 'Anulowano' : 'Przerwano');
        if (res.status === 'compile_error') {
          clear(results);
          results.append(callout(`Błąd kompilacji (${res.which === 'generator' ? 'generator' : 'rozwiązanie ' + res.which.toUpperCase()}) — szczegóły pod edytorem.`, 'danger'));
        } else live.finish(res);
      } catch (e) {
        if (e.cancelled) { prog.set(1, 'Anulowano'); live.warn('Porównanie anulowane — pokazuję dotychczasowe pomiary.'); }
        else {
          prog.set(1, 'Błąd');
          const errs = e.data?.errors;
          if (errs && errs.length && st.genKind === 'dsl') { genEd.markLines(errs.filter((x) => x.line).map((x) => ({ line: x.line, type: 'error', message: x.message }))); }
          genOut.showText(e.message, 'danger');
        }
      } finally {
        st.running = null;
        startBtn.hidden = false; cancelBtn.hidden = true;
      }
    }

    function buildLive(hasB) {
      const el = h('div', { class: 'stack' });
      const verdictBox = h('div', { class: 'stack gap-s' });
      const statsBox = h('div', { class: 'bm-stats' });
      const chartBox = h('div', { class: 'bm-chart' });
      const warnBox = h('div', { class: 'stack gap-s' });
      const cols = [
        { key: 'n', label: 'n', render: (r) => h('b', { class: 'mono' }, supN(r.n)) },
        { key: 'input_bytes', label: 'Test', align: 'right', render: (r) => h('span', { class: 'muted' }, bytes(r.input_bytes)) },
        { key: 'a', label: 'A czas', align: 'right', render: (r) => cell(r.a) },
        { key: 'am', label: 'A pamięć', align: 'right', render: (r) => h('span', { class: 'muted' }, fmtMem(r.a?.mem_kb)) },
      ];
      if (hasB) cols.push(
        { key: 'b', label: 'B czas', align: 'right', render: (r) => cell(r.b) },
        { key: 'bm', label: 'B pamięć', align: 'right', render: (r) => h('span', { class: 'muted' }, fmtMem(r.b?.mem_kb)) },
        { key: 'ratio', label: 'A / B', align: 'right', render: (r) => (r.ratio ? h('span', { class: r.ratio > 1.25 ? 'tone-success bold' : r.ratio < 0.8 ? 'tone-warn bold' : 'muted' }, `${r.ratio.toFixed(r.ratio < 10 ? 2 : 0).replace('.', ',')}×`) : '—') },
        { key: 'same', label: 'Wyniki', render: (r) => (r.same === true ? badge('zgodne', 'success', 'check') : r.same === false ? h('span', { title: r.diff ? `token ${r.diff.token}: A = ${r.diff.a}, B = ${r.diff.b}` : '' }, badge('RÓŻNE', 'danger', 'x')) : h('span', { class: 'faint' }, '—')) });
      const tbl = table(cols, [], { compact: true, empty: 'Czekam na pierwsze pomiary…' });
      el.append(verdictBox, statsBox, warnBox,
        card({ title: 'Czas działania', icon: 'activity', subtitle: 'skala logarytmiczna na obu osiach · linie przerywane = dopasowana złożoność', body: chartBox }),
        card({ title: 'Pomiary', icon: 'table', padded: false, body: tbl }));
      const series = { A: [], B: [] };
      const redraw = (extra = []) => {
        clear(chartBox);
        const s = [{ name: 'A', points: series.A, color: 'var(--warn)' }];
        if (hasB) s.push({ name: 'B', points: series.B, color: 'var(--accent-2)' });
        for (const x of extra) s.push({ ...x, color: x.name.startsWith('A') ? 'var(--warn)' : 'var(--accent-2)' });
        chartBox.append(lineChart(s.filter((x) => x.points.length), { logX: true, logY: true, xLabel: 'n', yLabel: 'czas [ms]', height: 300, width: 760,
          xFormat: (v) => supN(Math.round(v)), yFormat: (v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1).replace('.', ',')} s` : v >= 1 ? `${Math.round(v)}` : v.toPrecision(1)) }));
      };
      redraw();
      return {
        el,
        addRow(r) {
          tbl.update(st.rows);
          if (r.a?.verdict === 'OK' && r.a.time != null) series.A.push([r.n, Math.max(r.a.time, 1e-5) * 1000]);
          if (r.b?.verdict === 'OK' && r.b.time != null) series.B.push([r.n, Math.max(r.b.time, 1e-5) * 1000]);
          redraw();
        },
        warn(msg) { warnBox.append(callout(msg, 'warn')); },
        finish(res) {
          tbl.update(res.rows);
          redraw((res.series || []).filter((s) => s.dashed));
          clear(verdictBox);
          const v = res.verdict || {};
          verdictBox.append(h('div', { class: 'bm-verdict' }, icon('trophy', 22), h('div', { class: 'grow' }, h('div', { class: 'bm-headline' }, v.headline || 'Gotowe.'),
            h('div', { class: 'muted small' }, `${res.rows.length} ${res.rows.length === 1 ? 'rozmiar' : 'rozmiarów'} · ${res.settings.repeats}× powtórzenia (mediana) · limit ${String(res.settings.timeout).replace('.', ',')} s · ${res.gen_kind === 'dsl' ? 'generator: mini-język' : 'generator: program'}`))));
          for (const m of v.messages || []) verdictBox.append(callout(m.text, m.tone === 'danger' ? 'danger' : m.tone === 'warn' ? 'warn' : 'info'));
          clear(statsBox);
          const cx = res.complexity || {};
          const cxStat = (k, name) => {
            const c = cx[k];
            if (!c) return null;
            return stat({ label: `${name}: szacowana złożoność`, value: c.label || '?', icon: 'sigma', tone: k === 'a' ? 'warn' : 'accent',
              sub: c.slope != null ? `nachylenie log-log ${String(c.slope).replace('.', ',')}${c.confidence === 'low' ? ' · mało pewne' : ''}${c.note ? ' · ' + c.note : ''}` : c.note });
          };
          const last = [...res.rows].reverse().find((r) => r.ratio);
          const diffs = res.rows.filter((r) => r.same === false).length;
          statsBox.append(cxStat('a', 'A'), res.has_b ? cxStat('b', 'B') : null,
            res.has_b ? stat({ label: 'Przyspieszenie (największe n)', value: last ? `${(last.ratio >= 1 ? last.ratio : 1 / last.ratio).toFixed(1).replace('.', ',')}×` : '—', icon: 'zap',
              sub: last ? `${last.ratio >= 1 ? 'B szybsze' : 'A szybsze'} dla n = ${supN(last.n)}` : 'brak wspólnych pomiarów' }) : null,
            res.has_b ? stat({ label: 'Zgodność wyników', value: diffs ? `${diffs} różnic` : 'zgodne', icon: diffs ? 'triangle-alert' : 'circle-check', tone: diffs ? 'danger' : 'success',
              sub: diffs ? 'co najmniej jedno rozwiązanie jest błędne' : 'na wszystkich rozmiarach' }) : null);
        },
      };
    }

    function cell(x) {
      if (!x) return '—';
      if (x.verdict === 'OK') return h('span', { class: 'mono', title: x.times?.length > 1 ? `pomiary: ${x.times.map((t) => fmtTime(t)).join(', ')}` : '' }, fmtTime(x.time));
      if (x.verdict === 'SKIP') return h('span', { class: 'faint', title: x.note || '' }, 'pominięto');
      return h('span', { class: 'row gap-s nowrap', style: 'justify-content:flex-end' }, verdictBadge(x.verdict), x.time != null && x.verdict !== 'TLE' ? h('span', { class: 'muted small' }, fmtTime(x.time)) : null);
    }

    // ----------------------------------------------------------------------- szablony
    function loadTemplate(id) {
      const t = st.templates.find((x) => x.id === id);
      if (!t) return;
      A.ed.setValue(t.a); A.ed.setLang(/#include/.test(t.a) ? 'cpp' : 'python');
      B.ed.setValue(t.b); B.ed.setLang(/#include/.test(t.b) ? 'cpp' : 'python');
      if (st.genKind !== (t.gen_kind || 'dsl')) { kindSeg.setValue(t.gen_kind || 'dsl'); setGenKind(t.gen_kind || 'dsl', true); }
      genEd.setValue(t.generator);
      sizesInp.value = t.sizes; local.set('bench:sizes', t.sizes); checkSizes();
      toast(`Wczytano: ${t.name}`, 'success', { timeout: 1800 });
    }
    api.get('/api/benchmark/templates', null, { quiet: true }).then((r) => {
      st.templates = r.templates || [];
      for (const t of st.templates) tplSel.append(h('option', { value: t.id }, t.name));
    }).catch(() => {});
    checkSizes();
    VIEW.apply = (p) => {
      if (p.a || p.solution) A.ed.setValue(p.a || p.solution);
      if (p.b || p.brute) B.ed.setValue(p.b || p.brute);
      if (p.generator) {
        const isCode = /#include|\bint\s+main\s*\(|^\s*(import|def)\b/m.test(p.generator);
        const kind = isCode ? 'code' : 'dsl';
        if (kind !== st.genKind) { kindSeg.setValue(kind); setGenKind(kind, true); }
        genEd.setValue(p.generator);
        if (kind === 'code') genEd.setLang(/#include/.test(p.generator) ? 'cpp' : 'python');
      }
    };
    if (ctx.params) VIEW.apply(ctx.params);
  },

  onShow(params) {
    if (VIEW && params && Object.keys(params).length) VIEW.apply(params);
    if (VIEW) requestAnimationFrame(() => { VIEW.A?.ed.refresh(); VIEW.B?.ed.refresh(); VIEW.genEd?.refresh(); });
  },
};

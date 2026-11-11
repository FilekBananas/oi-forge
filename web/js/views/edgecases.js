// (l) Testy brzegowe: krótkie podchwytliwe testy z wyjaśnieniem + generator C++ testu maksymalnego / małych testów.
import { h, icon, button, viewHeader, card, badge, emptyState, clear, codeBlock, tabs, callout, table, toast, verdictBadge, copyButton } from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';
import { statementPanel, toolSwitcher, loadingCard, warningsBlock, sampleBox, shortInput, bigInput, fmtLen } from './taskexplain.js';

const TAG_TONE = { bignum: 'accent', overflow: 'danger', min: 'info', graph: 'accent', string: 'accent', negative: 'warn', zero: 'warn', ties: 'warn', multitest: 'warn' };
const TAG_NAME = { bignum: 'liczba jako napis', overflow: 'przepełnienie', min: 'minimum', max: 'maksimum', equal: 'równe', ties: 'remisy', sorted: 'posortowane', zero: 'zera',
  negative: 'ujemne', graph: 'graf', path: 'ścieżka', star: 'gwiazda', string: 'napis', grid: 'plansza', multitest: 'wiele testów', queries: 'zapytania' };

export default {
  id: 'edgecases',
  title: 'Testy brzegowe',
  icon: 'target',
  group: 'Zadania',
  order: 30,
  description: 'Konkretne, krótkie testy łapiące typowe błędy (n minimalne, overflow, równe elementy, graf niespójny…) i generator C++ testu maksymalnego.',
  keywords: ['testy', 'brzegowe', 'edge case', 'overflow', 'generator', 'test maksymalny', 'podchwytliwe'],

  render(root, ctx) {
    const self = this;
    let last = null;
    const out = h('div', { class: 'tk-results stack' });
    const panel = statementPanel({ actionLabel: 'Wygeneruj testy', actionIcon: 'target', onRun: (t) => build(t) });
    const codeHost = h('div', { class: 'ec-code' });
    const myCode = card({ title: 'Sprawdź swój program na tych testach', icon: 'play', subtitle: 'opcjonalnie — wklej rozwiązanie, uruchomię je na wszystkich testach',
      body: codeHost, class: 'ec-mycode', actions: [button('Uruchom', { icon: 'play', size: 'sm', onClick: () => runMine() })] });
    root.append(
      viewHeader(this),
      h('div', { class: 'tk-layout' },
        h('div', { class: 'tk-left stack' }, panel.el, toolSwitcher(ctx, 'edgecases', panel.getText), myCode),
        out));
    const ed = createEditor(codeHost, { value: local.get('edgecases:code', ''), lang: 'cpp', height: '240px', placeholder: '// Twoje rozwiązanie (C++ albo Python)…',
      onChange: () => local.set('edgecases:code', ed.getValue()), onRun: () => runMine() });
    out.append(emptyState({ icon: 'target', title: 'Testy, które łapią błędy',
      text: 'Na podstawie formatu wejścia i ograniczeń przygotuję małe testy brzegowe z wyjaśnieniem, co sprawdzają, oraz generator dużego testu do pobrania.' }));
    self._panel = panel;
    self._build = build;

    async function build(text) {
      clear(out).append(loadingCard('Buduję testy z formatu wejścia…'));
      try {
        last = await api.post('/api/edgecases', { statement: text });
        clear(out).append(...renderResult(last, ctx, text));
      } catch (e) {
        clear(out).append(emptyState({ icon: 'circle-alert', title: 'Nie udało się wygenerować testów', text: e.message || String(e) }));
      }
    }

    async function runMine() {
      const code = ed.getValue();
      if (!code.trim()) { toast('Wklej najpierw swój kod.', 'info'); return; }
      if (!panel.getText().trim()) { toast('Brak treści zadania.', 'info'); return; }
      const box = h('div', { class: 'stack' }, loadingCard('Kompiluję i uruchamiam na testach…'));
      const holder = card({ title: 'Twój program na testach', icon: 'play', body: box });
      out.prepend(holder);
      try {
        const r = await api.post('/api/edgecases', { statement: panel.getText(), code });
        last = r;
        clear(box);
        const run = r.run || {};
        if (!run.ok) { box.append(callout('Błąd kompilacji:\n```\n' + (run.compile?.stderr || '').slice(0, 3000) + '\n```', 'danger')); return; }
        const titles = Object.fromEntries([...(r.tests || []).map((t) => [t.id, t.title]), ...(r.samples || []).map((s, i) => [`s${i + 1}`, `Przykład ${i + 1}`])]);
        const sampleOut = Object.fromEntries((r.samples || []).map((s, i) => [`s${i + 1}`, s.output]));
        box.append(table([
          { key: 'id', label: 'Test', render: (x) => h('span', null, titles[x.id] || x.id) },
          { key: 'verdict', label: 'Wynik', render: (x) => {
            let v = x.verdict;
            if (v === 'OK' && sampleOut[x.id] !== undefined) v = sameOut(x.stdout, sampleOut[x.id]) ? 'OK' : 'WA';
            return verdictBadge(v);
          } },
          { key: 'time', label: 'Czas', align: 'right', render: (x) => `${Math.round((x.time || 0) * 1000)} ms` },
          { key: 'stdout', label: 'Wyjście', render: (x) => h('code', { class: 'ec-out' }, (x.stdout || '').trim().slice(0, 80) || '—') },
        ], run.results || [], { compact: true }));
        box.append(h('div', { class: 'small muted' }, 'Dla testów brzegowych nie znam poprawnej odpowiedzi — sprawdź, czy wyniki mają sens (RE/TLE to pewny błąd). Przykłady z treści są porównywane z oczekiwanym wyjściem.'));
      } catch (e) {
        clear(box).append(callout(e.message || String(e), 'danger'));
      }
    }
  },

  onShow(params) {
    if (params && params.statement && this._panel) { this._panel.setText(params.statement); this._build(params.statement); }
    else if (this._panel) this._panel.refresh();
  },
};

function sameOut(a, b) { return (a || '').trim().split(/\s+/).join(' ') === (b || '').trim().split(/\s+/).join(' '); }

function download(name, text) {
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/plain' })), download: name });
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

function renderResult(r, ctx, statement = '') {
  const parts = [];
  const w = warningsBlock(r.notes);
  if (w) parts.push(w);
  const tests = r.tests || [];
  parts.push(h('div', { class: 'row between tk-sec' },
    h('div', { class: 'section-title', style: 'margin:0' }, icon('target', 15), ` Testy brzegowe (${tests.length})`),
    tests.length ? h('div', { class: 'row gap-s' },
      button('Wszystkie do testerki', { icon: 'send', size: 'sm', onClick: () => ctx.navigate('tester', { tests: tests.map((t) => ({ name: t.title, input: t.input })) }) }),
      statement ? button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', title: 'Nowe zadanie w edytorze: treść, rozw.cpp i te testy w testy/ — uruchomisz je u siebie', onClick: () => ctx.navigate('workspace', { statement, tests: tests.map((t) => ({ input: t.input })) }) }) : null,
      button('Pobierz wszystkie', { icon: 'download', size: 'sm', variant: 'ghost', onClick: () => download('testy_brzegowe.txt', tests.map((t) => `# ${t.title}\n${t.input}`).join('\n')) })) : null));
  if (!tests.length) parts.push(emptyState({ icon: 'search', title: 'Nie umiem zbudować testów', text: 'Nie rozpoznałem formatu wejścia. Sprawdź sekcję „Wejście” w objaśniaczu treści.' }));
  parts.push(h('div', { class: 'ec-tests' }, tests.map((t) => h('div', { class: 'ec-test card' },
    h('div', { class: 'card-body stack gap-s' },
      h('div', { class: 'row between nowrap' }, h('b', null, t.title),
        h('div', { class: 'row gap-s nowrap' }, copyButton(() => t.input),
          button(null, { icon: 'send', size: 'sm', variant: 'ghost', title: 'Wyślij do testerki', onClick: () => ctx.navigate('tester', { input: t.input, tests: [{ name: t.title, input: t.input }] }) }))),
      h('div', { class: 'small muted' }, t.why),
      bigInput(t.input) ? h('div', { class: 'small muted' }, `Duży test: ${fmtLen(t.input.length)} — poniżej tylko podgląd, „Kopiuj” i „Wyślij do testerki” biorą całość.`) : null,
      h('pre', { class: 'tk-pre ec-pre' }, shortInput(t.input)),
      t.tags?.length ? h('div', { class: 'row gap-s' }, t.tags.map((g) => badge(TAG_NAME[g] || g, TAG_TONE[g] || 'neutral'))) : null)))));
  // generatory
  const genTabs = tabs([
    { id: 'max', label: 'Test maksymalny', icon: 'zap', render: () => genPanel(r.generator_max, 'gen_max.cpp', ctx, statement, 'Wypisuje test o maksymalnych rozmiarach — sprawdzisz nim czas działania i pamięć.') },
    { id: 'small', label: 'Małe losowe (do testerki)', icon: 'shuffle', render: () => genPanel(r.generator_small, 'gen_small.cpp', ctx, statement, 'Małe losowe testy (ziarno z argumentu) — idealne do porównania z brute force w testerce.') },
  ]);
  parts.push(card({ title: 'Generator C++', icon: 'file-code', body: genTabs.el }));
  if (r.samples?.length) parts.push(card({ title: 'Przykłady z treści', icon: 'flask-conical', body: h('div', { class: 'stack' }, r.samples.map((s, i) => sampleBox(s, i))) }));
  return parts;
}

function genPanel(code, fname, ctx, statement, text) {
  const preview = h('div', null);
  return h('div', { class: 'stack gap-s' },
    h('div', { class: 'small muted' }, text),
    codeBlock(code || '', 'cpp', { lineNumbers: true, maxHeight: 420 }),
    h('div', { class: 'row gap-s' },
      button('Pobierz', { icon: 'download', size: 'sm', onClick: () => download(fname, code) }),
      button('Sprawdź generator', { icon: 'play', size: 'sm', onClick: async () => {
        clear(preview).append(loadingCard('Kompiluję i uruchamiam generator…'));
        try {
          const r = await api.post('/api/edgecases/generator', { code, seed: 1 });
          clear(preview);
          if (!r.compile?.ok) { preview.append(callout('Generator się nie kompiluje:\n```\n' + (r.compile?.stderr || '').slice(0, 2000) + '\n```', 'danger')); return; }
          preview.append(h('div', { class: 'row gap-s' }, verdictBadge(r.verdict), h('span', { class: 'small muted' }, `${(r.size / 1024).toFixed(1)} KB wyjścia, ${Math.round((r.time || 0) * 1000)} ms`)),
            h('pre', { class: 'tk-pre ec-pre' }, (r.preview || '').slice(0, 1500) + (r.size > 1500 ? '\n…' : '')));
        } catch (e) { clear(preview).append(callout(e.message || String(e), 'danger')); }
      } }),
      button('Do testerki jako generator', { icon: 'send', size: 'sm', variant: 'primary', onClick: () => ctx.navigate('tester', { generator: code }) }),
      button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', title: statement ? 'Nowe zadanie w edytorze z tym generatorem (gen.cpp)' : 'Otwórz generator w edytorze',
        onClick: () => ctx.navigate('workspace', statement ? { statement, code, target: 'gen' } : { code, name: fname }) })),
    preview);
}

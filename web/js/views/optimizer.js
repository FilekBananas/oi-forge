// (i) Optymalizator — reguły przyspieszające kod bez zmiany wyniku: bezpieczne stosowane od razu, ryzykowne jako sugestie.
import {
  h, icon, button, viewHeader, split, tabs, badge, callout, markdown, toast, clear, emptyState, checkbox, textarea,
  input, field, progressBar, stat, codeBlock, fmtTime, fmtMem, copyButton, confirmDialog,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';
import { barChart } from '../charts.js';

const EXAMPLE = `#include <bits/stdc++.h>
using namespace std;

long long suma(vector<long long> v) {
    long long s = 0;
    for (auto x : v) s += x;
    return s;
}

int main() {
    int n, q;
    cin >> n >> q;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    set<long long> s(a.begin(), a.end());
    while (q--) {
        int typ;
        cin >> typ;
        if (typ == 1) {
            int l, r;
            cin >> l >> r;
            long long wynik = 0;
            for (int i = l; i <= r; i++) wynik += a[i];
            cout << wynik << endl;
        } else {
            long long x;
            cin >> x;
            auto it = lower_bound(s.begin(), s.end(), x);
            if (it == s.end()) cout << -1 << endl;
            else cout << *it << endl;
        }
    }
    cout << suma(a) << endl;
}
`;

const KIND = (c) => (c.has_edit ? (c.safe ? ['Bezpieczna', 'success', 'circle-check'] : ['Ryzykowna', 'warn', 'triangle-alert']) : ['Sugestia', 'info', 'lightbulb']);
const CATEGORY = {
  algorytm: ['Algorytm', 'accent', 'sparkles'],
  'poprawność': ['Poprawność', 'danger', 'triangle-alert'],
  'pamięć': ['Pamięć', 'neutral', 'layers'],
  stos: ['Stos', 'neutral', 'layers'],
};

// tabela różnic (wspólna dla zakładki „Różnice” i diffu pojedynczej zmiany)
function diffTable(rows, { compact = false } = {}) {
  const tbl = h('div', { class: ['op-dtable', compact ? 'compact' : ''].join(' ') },
    compact ? null : h('div', { class: 'op-drow head' }, h('span', null, ''), h('span', null, 'Oryginał'), h('span', null, ''), h('span', null, 'Po zmianach')));
  let prev = null;
  for (const r of rows) {
    if (compact && prev !== null && r.a && prev.a && r.a > prev.a + 1) tbl.append(h('div', { class: 'op-dgap' }, '⋯'));
    const cls = { eq: '', del: 'del', add: 'add', chg: 'chg' }[r.type];
    tbl.append(h('div', { class: `op-drow ${cls}` },
      h('span', { class: 'op-dln' }, r.a ?? ''), h('code', { class: r.type === 'del' || r.type === 'chg' ? 'diff-del' : 'diff-eq' }, r.a_text || ' '),
      h('span', { class: 'op-dln' }, r.b ?? ''), h('code', { class: r.type === 'add' || r.type === 'chg' ? 'diff-add' : 'diff-eq' }, r.b_text || ' ')));
    if (r.a) prev = r;
  }
  return tbl;
}

export default {
  id: 'optimizer',
  title: 'Optymalizator',
  icon: 'zap',
  group: 'Kod',
  order: 60,
  layout: 'full',
  description: 'Przyspiesza program bez zmiany wyniku. Najpierw szuka lepszego algorytmu (sumy prefiksowe zamiast pętli w '
    + 'zapytaniach, sparse table, zbiór zamiast liniowego szukania, deque, spamiętywanie, sort przed pętlą…) i pokazuje zysk '
    + 'złożoności, potem stałe: szybkie I/O, endl → \\n, const &, reserve. Benchmark porówna obie wersje.',
  keywords: ['optymalizacja', 'przyspiesz', 'szybciej', 'tle', 'fast io', 'endl', 'const ref', 'benchmark', 'wydajność',
    'sumy prefiksowe', 'sparse table', 'memo', 'deque', 'złożoność'],

  render(root, ctx) {
    let res = null;
    let selected = new Set();
    let optimizedCode = '';

    const edHost = h('div', { class: 'op-editor' });
    const analyzeBtn = button('Optymalizuj', { icon: 'zap', variant: 'primary', onClick: () => run(null), title: 'Ctrl+Enter' });
    const left = h('div', { class: 'op-col' }, edHost,
      h('div', { class: 'op-actions' },
        button('Przykład', { icon: 'sparkles', size: 'sm', variant: 'ghost', onClick: () => { ed.setValue(EXAMPLE); run(null); } }),
        h('div', { class: 'grow' }),
        button('Złożoność', { icon: 'gauge', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('complexity', { code: ed.getValue() }) }),
        analyzeBtn));

    const changesHost = h('div', { class: 'op-changes' });
    const diffHost = h('div', { class: 'op-diff' });
    const codeHost = h('div', { class: 'op-code' });
    const benchHost = h('div', { class: 'op-bench' });
    const tb = tabs([
      { id: 'changes', label: 'Zmiany', icon: 'list-checks', render: () => changesHost },
      { id: 'diff', label: 'Różnice', icon: 'git-compare', render: () => diffHost },
      { id: 'code', label: 'Kod po zmianach', icon: 'file-code', render: () => codeHost },
      { id: 'bench', label: 'Benchmark', icon: 'timer', render: () => benchHost },
    ], { active: 'changes' });
    const right = h('div', { class: 'op-col op-right' }, tb.el);

    root.append(viewHeader(this), h('div', { class: 'op-body' }, split(left, right, { ratio: 0.46, min: 340, storageKey: 'optimizer' })));
    const ed = createEditor(edHost, {
      value: '', lang: 'cpp', height: '100%', storageKey: 'optimizer', toolbar: true, title: 'Oryginalny kod',
      placeholder: 'Wklej rozwiązanie — znajdę, co je spowalnia…', onRun: () => run(null),
      onChange: () => { if (res) changesHost.classList.add('stale'); },
    });
    this._ed = ed;
    renderAll();
    renderBench();
    if (ed.getValue().trim()) setTimeout(() => run(null, { quiet: true }), 150);

    async function run(apply, { quiet = false, verify = false } = {}) {
      const code = ed.getValue();
      if (!code.trim()) { if (!quiet) toast('Wklej kod do optymalizacji.', 'info'); return; }
      changesHost.classList.add('busy');
      try {
        res = await api.post('/api/optimize', { code, apply: apply === null ? undefined : apply, verify }, { quiet });
        optimizedCode = res.optimized_code;
        selected = new Set(res.changes.filter((c) => c.applied).map((c) => c.rule + ':' + c.line));
        ed.markLines(res.changes.map((c) => ({ line: c.line, type: c.has_edit ? (c.safe ? 'info' : 'warn') : 'hint', message: `${c.title}: ${c.why}` })));
        renderAll();
        if (res.compile) {
          if (res.compile.ok) toast('Zoptymalizowany kod się kompiluje ✓', 'success');
          else toast('Uwaga: zoptymalizowany kod się NIE kompiluje — zobacz zakładkę „Kod po zmianach”.', 'warn');
        }
      } catch { /* toast z api */ } finally { changesHost.classList.remove('busy', 'stale'); }
    }

    function renderAll() { renderChanges(); renderDiff(); renderCode(); updateBadges(); }

    function updateBadges() {
      if (!res) return;
      const n = res.changes.filter((c) => c.applied).length;
      tb.setBadge('changes', String(res.changes.length), n ? 'accent' : 'neutral');
      tb.setBadge('diff', n ? String(n) : '', 'success');
    }

    // ------------------------------------------------------------------ lista zmian
    function renderChanges() {
      clear(changesHost);
      if (!res) {
        changesHost.append(emptyState({ icon: 'zap', title: 'Wklej kod i kliknij „Optymalizuj”',
          text: 'Pokażę konkretne zmiany: co przyspieszają, dlaczego i czy są bezpieczne. Bezpieczne zastosuję automatycznie.',
          action: button('Wczytaj przykład', { icon: 'sparkles', onClick: () => { ed.setValue(EXAMPLE); run(null); } }) }));
        return;
      }
      const ch = res.changes;
      const applied = ch.filter((c) => c.applied).length;
      const sugg = ch.filter((c) => !c.has_edit).length;
      const algo = ch.filter((c) => c.category === 'algorytm').length;
      changesHost.append(h('div', { class: 'grid-4' },
        stat({ label: 'Poprawki algorytmu', value: String(algo), tone: algo ? 'accent' : undefined, icon: 'sparkles' }),
        stat({ label: 'Zastosowane zmiany', value: String(applied), tone: applied ? 'success' : undefined, icon: 'circle-check' }),
        stat({ label: 'Do rozważenia', value: String(ch.filter((c) => c.has_edit && !c.applied).length), tone: 'warn', icon: 'triangle-alert' }),
        stat({ label: 'Sugestie', value: String(sugg), tone: 'info', icon: 'lightbulb' })));
      const top = ch[0];
      if (top && top.category === 'algorytm') {
        changesHost.append(h('div', { class: 'op-top' }, icon('sparkles', 18),
          h('div', { class: 'grow' },
            h('div', { class: 'op-top-t' }, 'Najważniejsza poprawka: ', h('b', null, top.title)),
            top.gain ? h('div', { class: 'op-top-g mono' }, top.gain) : null),
          h('button', { class: 'op-ch-line', type: 'button', onclick: () => { ed.highlightLine(top.line); ed.scrollToLine(top.line, false); } }, `linia ${top.line}`)));
      }
      if (res.interactive) changesHost.append(callout('Program wygląda na **interaktywny** (wypisuje i czeka na odpowiedź) — `endl` i synchronizacja strumieni są tu potrzebne, więc ich nie ruszam.', 'warn'));
      if (res.mixed_io) changesHost.append(callout('Program miesza `cin/cout` ze `scanf/printf`. Po `sync_with_stdio(false)` kolejność wyjścia może się pomieszać — najpierw ujednolić wejście/wyjście.', 'warn'));
      if (!ch.length) { changesHost.append(callout('Nie znalazłem nic do poprawienia — kod jest już w dobrej formie. 🎉', 'tip')); return; }
      const bar = h('div', { class: 'row gap-s op-bar' },
        button('Zastosuj zaznaczone', { icon: 'check', variant: 'primary', size: 'sm', onClick: () => run([...selected]) }),
        button('Tylko bezpieczne', { icon: 'circle-check', size: 'sm', variant: 'ghost', onClick: () => run(null) }),
        button('Odznacz wszystkie', { icon: 'x', size: 'sm', variant: 'ghost', onClick: () => run([]) }),
        h('div', { class: 'grow' }),
        button('Sprawdź kompilację', { icon: 'cpu', size: 'sm', variant: 'ghost', onClick: () => run([...selected], { verify: true }) }));
      changesHost.append(bar);
      for (const c of ch) {
        const [label, tone, ic] = KIND(c);
        const key = c.rule + ':' + c.line;
        const box = c.has_edit ? checkbox('', { checked: c.applied, onChange: (v) => { if (v) selected.add(key); else selected.delete(key); } }) : h('span', { class: 'op-nobox' }, icon('lightbulb', 15));
        const cat = CATEGORY[c.category];
        const card_ = h('div', { class: ['op-change', c.applied ? 'applied' : '', `tone-${tone}-border`, c.category === 'algorytm' ? 'algo' : ''].join(' ') },
          h('div', { class: 'op-ch-head' }, box,
            h('div', { class: 'grow' },
              h('div', { class: 'row gap-s wrap' }, h('span', { class: 'op-ch-title' }, c.title), cat ? badge(cat[0], cat[1], cat[2]) : null,
                badge(label, tone, ic), c.applied ? badge('zastosowano', 'accent', 'check') : null)),
            h('button', { class: 'op-ch-line', type: 'button', onclick: () => { ed.highlightLine(c.line); ed.scrollToLine(c.line, false); } },
              c.lines && c.lines.length > 1 ? `linie ${c.lines.slice(0, 4).join(', ')}${c.lines.length > 4 ? '…' : ''}` : `linia ${c.line}`)),
          h('div', { class: 'op-ch-why' }, c.why),
          c.gain ? h('div', { class: 'op-ch-gain' }, icon('gauge', 14), h('span', { class: 'muted' }, 'Zysk:'), h('code', null, c.gain)) : null,
          c.before || c.after ? h('div', { class: 'op-ch-ba' },
            c.before ? h('code', { class: 'op-before' }, c.before) : null,
            c.before && c.after ? icon('arrow-right', 14) : null,
            c.after ? h('code', { class: 'op-after' }, c.after) : null) : null,
          c.speedup_hint ? h('div', { class: 'op-ch-hint' }, icon('zap', 13), c.speedup_hint) : null,
          c.patch && c.patch.length ? h('details', { class: 'op-ch-sugg', open: c.category === 'algorytm' },
            h('summary', null, 'Zmiana w kodzie'), diffTable(c.patch, { compact: true })) : null,
          c.suggestion_md ? h('details', { class: 'op-ch-sugg', open: !c.has_edit }, h('summary', null, c.has_edit ? 'Dlaczego to działa' : 'Jak to zrobić'), markdown(c.suggestion_md)) : null);
        changesHost.append(card_);
      }
    }

    // ------------------------------------------------------------------ diff
    function renderDiff() {
      clear(diffHost);
      if (!res) { diffHost.append(emptyState({ icon: 'git-compare', title: 'Brak zmian do pokazania' })); return; }
      if (!res.changed) { diffHost.append(callout('Kod po zmianach jest identyczny z oryginałem (żadna zmiana nie jest zaznaczona).', 'info')); return; }
      const rows = res.diff_rows;
      // pokaż tylko okolice zmian (±3 linie), resztę zwiń
      const show = new Array(rows.length).fill(false);
      rows.forEach((r, i) => { if (r.type !== 'eq') for (let k = Math.max(0, i - 3); k <= Math.min(rows.length - 1, i + 3); k++) show[k] = true; });
      const tbl = h('div', { class: 'stack gap-s' });
      let chunk = [];
      let hidden = 0;
      const flush = () => {
        if (chunk.length) { tbl.append(diffTable(chunk)); chunk = []; }
        if (hidden) { tbl.append(h('div', { class: 'op-dgap' }, `⋯ ${hidden} niezmienionych linii`)); hidden = 0; }
      };
      rows.forEach((r, i) => {
        if (!show[i]) { if (chunk.length) { tbl.append(diffTable(chunk)); chunk = []; } hidden++; return; }
        if (hidden) flush();
        chunk.push(r);
      });
      flush();
      diffHost.append(h('div', { class: 'row between mb-s' }, h('span', { class: 'muted small' }, 'Zmienione fragmenty (zielone = nowa wersja).'),
        copyButton(() => res.diff, { label: 'Kopiuj diff' })), tbl);
    }

    // ------------------------------------------------------------------ kod po zmianach
    function renderCode() {
      clear(codeHost);
      if (!res) { codeHost.append(emptyState({ icon: 'file-code', title: 'Najpierw uruchom optymalizację' })); return; }
      if (res.compile && !res.compile.ok) {
        codeHost.append(callout('Zoptymalizowany kod się nie kompiluje — odznacz ryzykowne zmiany i spróbuj ponownie.', 'danger'),
          h('pre', { class: 'out err' }, res.compile.stderr || ''));
      } else if (res.compile && res.compile.ok) codeHost.append(callout('Kompiluje się poprawnie ✓', 'tip'));
      codeHost.append(h('div', { class: 'row gap-s mb-s' },
        button('Wstaw do edytora', { icon: 'clipboard-paste', size: 'sm', onClick: async () => {
          if (await confirmDialog('Zastąpić kod w edytorze wersją zoptymalizowaną?', { ok: 'Zastąp' })) { ed.setValue(optimizedCode); run(null, { quiet: true }); }
        } }),
        button('Uruchom w piaskownicy', { icon: 'play', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('playground', { code: optimizedCode }) }),
        button('Benchmark', { icon: 'timer', size: 'sm', variant: 'ghost', onClick: () => tb.select('bench') })),
      codeBlock(optimizedCode, 'cpp', { lineNumbers: true, title: 'zoptymalizowany.cpp', maxHeight: 640 }));
    }

    // ------------------------------------------------------------------ benchmark
    function renderBench() {
      clear(benchHost);
      const inpTa = textarea({ value: local.get('op:benchin', ''), rows: 5, placeholder: 'Wejście testowe (puste = wygeneruję losowe z formatu zgadniętego z kodu)',
        onInput: (v) => local.set('op:benchin', v.length < 200000 ? v : '') });
      const sizeInp = input({ value: local.get('op:benchsize', '200000'), size: 'sm', mono: true, attrs: { style: 'width:110px' }, onInput: (v) => local.set('op:benchsize', v) });
      const prog = progressBar({ label: '' });
      prog.el.hidden = true;
      const out = h('div', { class: 'stack mt-m' });
      const go = button('Uruchom benchmark', { icon: 'timer', variant: 'primary', onClick: () => bench() });
      benchHost.append(h('div', { class: 'stack gap-s' },
        callout('Kompiluję obie wersje (-O2), uruchamiam na tym samym wejściu kilka razy, porównuję wyjścia i czasy (najlepszy z przebiegów).', 'info'),
        field('Wejście', inpTa),
        h('div', { class: 'row gap-s' }, h('span', { class: 'muted small' }, 'Rozmiar generowanego wejścia n ='), sizeInp, h('div', { class: 'grow' }), go),
        prog.el, out));
      async function bench() {
        const original = ed.getValue();
        if (!original.trim()) { toast('Najpierw wklej kod.', 'info'); return; }
        if (!res) await run(null, { quiet: true });
        if (!res || !res.changed) { toast('Brak zmian — nie ma czego porównywać. Zaznacz jakieś zmiany.', 'info'); return; }
        clear(out);
        prog.el.hidden = false;
        prog.set(0, 'Start…');
        const runs = h('div', { class: 'row gap-s' });
        out.append(runs);
        try {
          const r = await api.job('/api/optimize/benchmark', {
            original, optimized: optimizedCode, input: inpTa.value || undefined, size: sizeInp.value, repeats: 3,
          }, {
            onProgress: (p, msg) => prog.set(p, msg),
            onEvent: (ev) => { if (ev.type === 'run') runs.append(h('span', { class: 'chip' }, `#${ev.index + 1}: ${fmtTime(ev.orig.time)} → ${fmtTime(ev.opt.time)}`)); },
          }).promise;
          clear(out);
          if (!r.ok) { out.append(callout(r.error || 'Benchmark nie powiódł się.', 'danger')); if (r.compile?.stderr) out.append(h('pre', { class: 'out err' }, r.compile.stderr)); return; }
          const sp = r.speedup;
          out.append(h('div', { class: 'grid-3' },
            stat({ label: 'Oryginał', value: fmtTime(r.original.time), sub: `${r.original.verdict} · ${fmtMem(r.original.mem_kb)}`, icon: 'timer' }),
            stat({ label: 'Po zmianach', value: fmtTime(r.optimized.time), sub: `${r.optimized.verdict} · ${fmtMem(r.optimized.mem_kb)}`, icon: 'zap', tone: 'success' }),
            stat({ label: 'Przyspieszenie', value: sp ? `${sp.toFixed(2)}×` : '—', tone: sp && sp >= 1.1 ? 'success' : (sp && sp < 0.95 ? 'danger' : undefined), icon: 'gauge',
              sub: sp ? (sp >= 1.1 ? 'szybciej' : sp < 0.95 ? 'wolniej!' : 'bez istotnej różnicy') : '' })));
          out.append(r.same_output ? callout('Wyjścia obu wersji są **identyczne** ✓', 'tip')
            : callout('**Wyjścia się różnią!** Któraś zmiana zmieniła działanie programu (albo program zależy od niezdefiniowanego zachowania) — odznacz ryzykowne zmiany.', 'danger'));
          if (r.original.time && r.optimized.time) {
            out.append(barChart([{ label: 'Oryginał', value: r.original.time, tone: 'warn' }, { label: 'Po zmianach', value: r.optimized.time, tone: 'success' }],
              { format: (v) => fmtTime(v) }));
          }
          if (r.input_source === 'generated') out.append(h('div', { class: 'muted small' }, `Wejście wygenerowane automatycznie (${(r.input_bytes / 1024).toFixed(0)} KB) — może nie spełniać wszystkich warunków zadania.`));
          if (!r.same_output) {
            out.append(h('div', { class: 'grid-2' },
              h('div', null, h('div', { class: 'out-label' }, 'Oryginał'), h('pre', { class: 'out' }, r.output_preview.original)),
              h('div', null, h('div', { class: 'out-label' }, 'Po zmianach'), h('pre', { class: 'out' }, r.output_preview.optimized))));
          }
        } catch (e) { if (!e.cancelled) out.append(callout(e.message || 'Błąd benchmarku', 'danger')); } finally { prog.el.hidden = true; }
      }
    }

    this._load = (params) => {
      if (params.code !== undefined) { ed.setValue(params.code); run(null, { quiet: true }); }
    };
  },

  onShow(params) {
    if (params && this._load) this._load(params);
    setTimeout(() => this._ed && this._ed.refresh(), 30);
  },
};

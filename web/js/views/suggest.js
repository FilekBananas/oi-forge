// (u) Doradca algorytmów: dopuszczalna złożoność z ograniczeń, ranking algorytmów z uzasadnieniem,
// klasyfikator uczony na zadaniach ucznia, podobne zadania ucznia z jego kodem.
import { h, icon, button, viewHeader, card, badge, table, emptyState, clear, codeBlock, modal, callout, kv } from '../ui.js';
import { api } from '../api.js';
import { statementPanel, toolSwitcher, confidenceBar, loadingCard, warningsBlock, kbChips } from './taskexplain.js';

export default {
  id: 'suggest',
  title: 'Doradca algorytmów',
  icon: 'brain-circuit',
  group: 'Zadania',
  order: 20,
  description: 'Z ograniczeń wylicza dopuszczalną złożoność, proponuje algorytmy z uzasadnieniem i pokazuje podobne zadania, które już rozwiązałeś.',
  keywords: ['algorytm', 'złożoność', 'jaki algorytm', 'podpowiedź', 'klasyfikator', 'podobne zadania', 'ograniczenia', 'n ≤'],

  render(root, ctx) {
    const self = this;
    const out = h('div', { class: 'tk-results stack' });
    const panel = statementPanel({ actionLabel: 'Doradź algorytm', actionIcon: 'brain-circuit', onRun: (t) => analyze(t) });
    root.append(
      viewHeader(this, button('Jak dobrze to działa?', { icon: 'activity', variant: 'ghost', onClick: () => showEval() })),
      h('div', { class: 'tk-layout' },
        h('div', { class: 'tk-left stack' }, panel.el, toolSwitcher(ctx, 'suggest', panel.getText), complexityLegend()),
        out));
    out.append(emptyState({ icon: 'brain-circuit', title: 'Wklej treść, a podpowiem podejście',
      text: 'Odczytam ograniczenia (n ≤ 200 000 → O(n log n)), rozpoznam słowa-klucze („najkrótsza droga”, „modulo 10^9+7”, „minimalne X takie, że”) i porównam z zadaniami, które już rozwiązałeś.' }));
    self._panel = panel;
    self._analyze = analyze;

    async function analyze(text) {
      clear(out).append(loadingCard('Analizuję ograniczenia i słowa kluczowe…'));
      try {
        const r = await api.post('/api/suggest', { statement: text });
        clear(out).append(...renderResult(r, ctx));
      } catch (e) {
        clear(out).append(emptyState({ icon: 'circle-alert', title: 'Nie udało się przeanalizować', text: e.message || String(e) }));
      }
    }

    async function showEval() {
      const body = h('div', { class: 'stack' }, loadingCard('Liczę walidację krzyżową na Twoich zadaniach…'));
      modal({ title: 'Skuteczność doradcy na Twoich zadaniach', icon: 'activity', body, width: 720 });
      try {
        const r = (await api.get('/api/suggest/eval')).eval;
        clear(body);
        if (r.error) { body.append(callout(r.error, 'warn')); return; }
        const names = { nb: 'Naive Bayes', centroid: 'Centroid TF-IDF', 'nb+centroid': 'NB + centroid', signals: 'Słownik sygnałów', blend: 'Mieszanka (używana)', lr: 'Regresja logistyczna' };
        body.append(
          h('p', { class: 'small muted' }, `${r.n_docs} treści z etykietami z nazw rund (${r.n_groups} różnych zadań), ${r.folds}-krotna walidacja krzyżowa — to samo zadanie z różnych konkursów nigdy nie trafia jednocześnie do treningu i testu.`),
          table([
            { key: 'name', label: 'Model', render: (x) => h('b', null, names[x.name] || x.name) },
            { key: 'top1', label: 'Top-1', align: 'right', render: (x) => `${Math.round(100 * x.top1)}%` },
            { key: 'top3', label: 'Top-3', align: 'right', render: (x) => `${Math.round(100 * x.top3)}%` },
          ], Object.entries(r.models).map(([name, v]) => ({ name, ...v })), { compact: true }),
          h('div', { class: 'section-title' }, 'Trafność top-1 w tematach'),
          h('div', { class: 'tk-chips' }, Object.entries(r.per_class_top1).sort((a, b) => b[1].n - a[1].n).map(([c, v]) =>
            badge(`${c}: ${v.ok}/${v.n}`, v.acc >= 0.6 ? 'success' : v.acc >= 0.35 ? 'warn' : 'danger'))));
      } catch (e) {
        clear(body).append(callout(e.message || String(e), 'danger'));
      }
    }
  },

  onShow(params) {
    if (params && params.statement && this._panel) { this._panel.setText(params.statement); this._analyze(params.statement); }
    else if (this._panel) this._panel.refresh();
  },
};

function complexityLegend() {
  return h('details', { class: 'tk-legend card' },
    h('summary', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('gauge', 16)), h('div', { class: 'card-title grow' }, 'Ściąga: n → złożoność')),
    h('div', { class: 'card-body small muted' }, 'Około 10^8 prostych operacji na sekundę. Limit n podpowiada, jakiej złożoności szuka autor zadania.'));
}

function renderResult(r, ctx) {
  const parts = [];
  const cx = r.complexity || {};
  // --- złożoność
  // rozmiary danych (liczności): każdy z własną złożonością; wymiar „po wartości” (np. kwota w DP) osobnym kolorem
  const sizes = (cx.sizes || []).map((s) => h('span', { title: s.value_dim ? 'wartość, którą można indeksować tablicę (DP po wartości)' : 'rozmiar danych (liczność)' },
    badge(`${s.var} ≤ ${s.max_fmt}${s.complexity ? ' → ' + s.complexity : ''}`, s.value_dim ? 'info' : 'accent')));
  parts.push(card({
    title: 'Dopuszczalna złożoność', icon: 'gauge',
    subtitle: cx.complexity ? `${cx.var} ≤ ${cx.max_fmt} → celuj w ${cx.complexity}` : 'nie znalazłem ograniczeń — przyjmij O(n log n)',
    actions: sizes,
    padded: false,
    body: h('div', { class: 'stack gap-s' },
      table([
        { key: 'label', label: 'Ograniczenie', render: (x) => h('span', { class: 'mono' }, x.label) },
        { key: 'complexity', label: 'Złożoność', render: (x) => h('b', null, x.complexity) },
        { key: 'hint', label: 'Typowe podejścia', render: (x) => h('span', { class: 'small' }, x.hint) },
        { key: 'ops', label: 'Operacji', align: 'right', render: (x) => h('span', { class: 'small muted' }, x.ops) },
      ], cx.table || [], { compact: true }),
      cx.note ? h('div', { class: 'tk-pad' }, callout(cx.note, 'info')) : null),
  }));
  // zaznacz wiersz
  requestAnimationFrame(() => {
    const idx = (cx.table || []).findIndex((x) => x.selected);
    const rows = parts[0].querySelectorAll('tbody tr');
    if (idx >= 0 && rows[idx]) rows[idx].classList.add('tk-row-on');
  });
  if (cx.subtasks?.length) {
    parts.push(card({ title: 'Podzadania', icon: 'list-checks', padded: false,
      body: table([
        { key: 'id', label: '#', render: (s) => h('b', null, String(s.id ?? '')) },
        { key: 'conditions', label: 'Warunki', render: (s) => h('span', { class: 'small' }, s.conditions || '') },
        { key: 'points', label: 'Pkt', align: 'right', render: (s) => s.points ?? '—' },
        { key: 'complexity', label: 'Wystarczy', render: (s) => h('code', null, s.complexity || '—') },
      ], cx.subtasks, { compact: true }) }));
  }
  const w = warningsBlock(r.parsed?.warnings);
  if (w) parts.push(w);
  // --- algorytmy
  const algos = r.algorithms || [];
  parts.push(h('div', { class: 'section-title tk-sec' }, icon('sparkles', 15), ' Proponowane algorytmy'));
  if (!algos.length) parts.push(emptyState({ icon: 'search', title: 'Brak wyraźnych sygnałów', text: 'Treść nie zawiera typowych słów-kluczy. Spróbuj objaśniacza treści albo auto-solvera.' }));
  parts.push(h('div', { class: 'tk-algos' }, algos.map((a, i) => h('div', { class: ['tk-algo card', i === 0 ? 'top' : ''].join(' ') },
    h('div', { class: 'card-body stack gap-s' },
      h('div', { class: 'row between nowrap' },
        h('div', { class: 'row gap-s nowrap', style: 'min-width:0' }, h('span', { class: 'tk-rank' }, String(i + 1)), h('b', { class: 'tk-algo-name' }, a.name)),
        a.complexity ? badge(a.complexity, 'neutral', 'timer') : null),
      confidenceBar(a.confidence),
      a.when ? h('div', { class: 'small muted' }, 'Kiedy: ', a.when) : null,
      a.reasons?.length ? h('ul', { class: 'tk-reasons' }, a.reasons.map((x) => h('li', null,
        h('span', { class: 'tk-origin' }, x.origin || 'treść'), ' ', x.reason,
        x.match ? h('span', { class: 'tk-match mono' }, ` „${x.match}”`) : null))) : null,
      kbChips(ctx, a.kb))))));
  // --- tematy z klasyfikatora
  if (r.topics?.length) {
    parts.push(card({ title: 'Temat według Twoich zadań', icon: 'graduation-cap',
      subtitle: r.classifier?.available ? `klasyfikator uczony na ${r.classifier.n_train} Twoich zadaniach (nazwy rund jako etykiety)` : 'klasyfikator niedostępny',
      body: h('div', { class: 'stack gap-s' }, r.topics.map((t) => h('div', { class: 'row nowrap gap-s' },
        h('span', { class: 'tk-topic' }, t.name), h('div', { class: 'grow' }, confidenceBar(t.p, { label: 'Prawdopodobieństwo' }))))) }));
  }
  // --- podobne zadania
  const sim = r.similar || [];
  parts.push(card({
    title: 'Podobne zadania, które już rozwiązałeś', icon: 'git-compare',
    body: sim.length ? h('div', { class: 'stack gap-s' }, sim.map((s) => similarItem(s, ctx)))
      : h('div', { class: 'muted' }, 'Nie znalazłem podobnych treści wśród Twoich zadań (albo brak pobranych treści w „Moich programach”).'),
  }));
  return parts;
}

function similarItem(s, ctx) {
  return h('div', { class: ['tk-sim', s.same_task ? 'same' : ''].join(' ') },
    h('div', { class: 'tk-sim-pct' }, `${Math.round(100 * s.similarity)}%`),
    h('div', { class: 'grow', style: 'min-width:0' },
      h('div', { class: 'row gap-s' }, h('b', null, s.title), badge(s.short, 'neutral'), s.score !== null && s.score !== undefined ? badge(`${s.score} pkt`, s.score >= 100 ? 'success' : 'warn') : null,
        s.label_name ? badge(s.label_name, 'accent') : null),
      h('div', { class: 'small muted' }, s.message),
      h('div', { class: 'small faint' }, [s.contest_name, s.round].filter(Boolean).join(' · '))),
    h('div', { class: 'row gap-s' },
      s.code ? button('Kod', { icon: 'code-xml', size: 'sm', onClick: () => showCode(s, ctx) }) : null,
      s.program_id ? button(null, { icon: 'folder', size: 'sm', variant: 'ghost', title: 'Otwórz w „Moich programach”', onClick: () => ctx.navigate('programs', { id: s.program_id }) }) : null));
}

function showCode(s, ctx) {
  const m = modal({
    title: `${s.title} (${s.short}) — Twoje rozwiązanie`, icon: 'code-xml', width: 860,
    body: h('div', { class: 'stack' }, kv([['Konkurs', s.contest_name], ['Runda', s.round], ['Wynik', s.score !== undefined ? `${s.score} pkt` : '—'], ['Podobieństwo treści', `${Math.round(100 * s.similarity)}%`]]),
      codeBlock(s.code, 'cpp', { lineNumbers: true, maxHeight: 520 })),
    actions: [
      button('Otwórz w Testerce', { icon: 'flask-conical', onClick: () => { m.close(); ctx.navigate('tester', { solution: s.code }); } }),
      button('Otwórz w edytorze', { icon: 'file-code', onClick: () => { m.close(); ctx.navigate('workspace', s.program_id ? { program_id: s.program_id } : { code: s.code, name: s.short || s.title }); } }),
      button('Otwórz w Sprawdzarce', { icon: 'bug', variant: 'primary', onClick: () => { m.close(); ctx.navigate('checker', { code: s.code }); } }),
    ],
  });
}

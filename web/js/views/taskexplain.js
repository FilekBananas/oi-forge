// (W) Objaśniacz treści zadania + wspólne elementy widoków „Zadania” (panel treści, przełącznik narzędzi, przykłady).
import {
  h, icon, button, viewHeader, card, textarea, fileDrop, badge, table, callout, emptyState, copyButton,
  toast, clear, spinner, markdown, more,
} from '../ui.js';
import { api, local } from '../api.js';
import { extractPdfText } from '../pdf.js';

// ============================================================================================ część wspólna
export const TASK_TOOLS = [
  { id: 'taskexplain', label: 'Objaśnij', icon: 'book-open' },
  { id: 'suggest', label: 'Doradca algorytmów', icon: 'brain-circuit' },
  { id: 'edgecases', label: 'Testy brzegowe', icon: 'target' },
  { id: 'autosolver', label: 'Auto-solver', icon: 'wand-sparkles' },
];

const STORE_KEY = 'task:statement';

export const EXAMPLE_STATEMENT = `Wycieczka rowerowa

Bajtazar planuje wycieczkę po Bajtocji. W kraju jest n miast połączonych m dwukierunkowymi drogami. Każda droga
łączy dwa różne miasta. Bajtazar mieszka w mieście numer 1 i chciałby wiedzieć, do ilu najmniej dróg musi przejechać,
aby dotrzeć do każdego z pozostałych miast.

Wejście
W pierwszym wierszu wejścia znajdują się dwie liczby całkowite n, m (1 ≤ n ≤ 100 000, 0 ≤ m ≤ 200 000), oznaczające
odpowiednio liczbę miast i liczbę dróg. W każdym z kolejnych m wierszy znajdują się dwie liczby całkowite a, b
(1 ≤ a, b ≤ n, a ≠ b) oznaczające drogę łączącą miasta a i b. Miasta są ponumerowane od 1 do n.

Wyjście
Na wyjściu należy wypisać n wierszy. W i-tym wierszu powinna znaleźć się najmniejsza liczba dróg, którymi trzeba
przejechać z miasta 1 do miasta i, lub -1, jeśli do miasta i nie da się dojechać.

Przykład
Wejście dla testu wyc0:
5 4
1 2
2 3
1 3
4 5
Wyjście dla testu wyc0:
0
1
1
-1
-1

Ocenianie
Podzadanie Ograniczenia Limit czasu Liczba punktów
1 n, m ≤ 1000 1 s 40
2 brak dodatkowych założeń 1 s 60
`;

/** Wczytuje plik zadania jako tekst: PDF → pdf.js, HTML → surowy HTML (parser backendu korzysta z <sup>, <pre>), TXT. */
export async function readTaskFile(file) {
  const name = (file.name || '').toLowerCase();
  if (name.endsWith('.pdf') || file.type === 'application/pdf') return extractPdfText(file);
  return file.text();
}

/**
 * Panel treści zadania: pole tekstowe, upuszczanie PDF/HTML/TXT, przycisk akcji. Treść jest wspólna dla
 * wszystkich narzędzi „Zadania” (localStorage), więc wystarczy wkleić ją raz.
 */
export function statementPanel({ actionLabel, actionIcon = 'play', onRun, hint } = {}) {
  const ta = textarea({
    value: local.get(STORE_KEY, ''), rows: 14, mono: false, class: 'tk-text',
    placeholder: 'Wklej treść zadania — z PDF, ze strony SIO2/OI albo własną. Możesz też upuścić plik PDF poniżej.',
    onInput: (v) => { local.set(STORE_KEY, v); updateInfo(); },
  });
  const info = h('div', { class: 'tk-info small muted' });
  const runBtn = button(actionLabel || 'Analizuj', { icon: actionIcon, variant: 'primary', onClick: () => run() });
  const drop = fileDrop({
    accept: '.pdf,.txt,.html,.htm,application/pdf,text/plain,text/html',
    label: 'Upuść PDF / HTML / TXT z treścią',
    onFiles: async (file) => {
      try {
        info.textContent = 'Czytam plik…';
        const text = await readTaskFile(file);
        if (!text || !text.trim()) { toast('Nie udało się odczytać tekstu z pliku (może to skan?).', 'warn'); updateInfo(); return; }
        setText(text);
        toast(`Wczytano „${file.name}”`, 'success');
        run();
      } catch (e) {
        console.error(e);
        toast('Nie udało się odczytać pliku: ' + (e.message || e), 'error');
        updateInfo();
      }
    },
  });
  const el = h('div', { class: 'tk-panel card' },
    h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('file-text', 17)),
      h('div', { class: 'grow' }, h('div', { class: 'card-title' }, 'Treść zadania'),
        h('div', { class: 'card-sub' }, hint || 'Jedna treść dla wszystkich narzędzi z grupy „Zadania”.')),
      h('div', { class: 'card-actions' },
        button(null, { icon: 'clipboard-paste', variant: 'ghost', size: 'sm', title: 'Wklej ze schowka', onClick: async () => {
          try { const t = await navigator.clipboard.readText(); if (t) { setText(t); run(); } } catch { toast('Przeglądarka nie pozwoliła odczytać schowka — użyj Ctrl+V.', 'info'); }
        } }),
        button(null, { icon: 'sparkles', variant: 'ghost', size: 'sm', title: 'Wstaw przykładową treść', onClick: () => { setText(EXAMPLE_STATEMENT); run(); } }),
        button(null, { icon: 'trash-2', variant: 'ghost', size: 'sm', title: 'Wyczyść', onClick: () => { setText(''); ta.focus(); } }))),
    h('div', { class: 'card-body stack gap-s' }, ta, drop, h('div', { class: 'row between' }, info, runBtn)));
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } });
  function setText(t) { ta.value = t; local.set(STORE_KEY, t); updateInfo(); }
  function getText() { return ta.value; }
  function updateInfo() {
    const t = ta.value.trim();
    if (!t) { info.textContent = 'Ctrl+Enter — uruchom'; return; }
    const first = t.split('\n').find((x) => x.trim()) || '';
    info.textContent = `${t.length.toLocaleString('pl-PL')} znaków · ${first.slice(0, 48)}${first.length > 48 ? '…' : ''}`;
  }
  async function run() {
    if (!ta.value.trim()) { toast('Najpierw wklej treść zadania (albo kliknij ✨ po przykład).', 'info'); ta.focus(); return; }
    if (onRun) await onRun(ta.value);
  }
  updateInfo();
  return { el, getText, setText, run, runBtn, textarea: ta, refresh: () => { const v = local.get(STORE_KEY, ''); if (v !== ta.value) { ta.value = v; updateInfo(); } } };
}

/** Przyciski przejścia do innych narzędzi z tą samą treścią. */
export function toolSwitcher(ctx, current, getText) {
  return h('div', { class: 'tk-switch' }, TASK_TOOLS.filter((t) => t.id !== current).map((t) =>
    h('button', { class: 'chip', type: 'button', title: `${t.label} — ta sama treść`, onclick: () => ctx.navigate(t.id, { statement: getText() }) },
      icon(t.icon, 14), t.label)));
}

/** Przykład wejście → wyjście w ramkach. */
export function sampleBox(s, i, { extra } = {}) {
  return h('div', { class: 'tk-sample' },
    h('div', { class: 'tk-sample-head' }, icon('flask-conical', 14), h('b', null, s.name || `Przykład ${i + 1}`),
      s.source === 'ocen' ? badge('test „ocen” z treści', 'info') : null,
      s.uncertain ? badge('podział zgadnięty', 'warn') : null, s.split_from_merged ? badge('rozdzielony', 'info') : null,
      bigInput(s.input) ? badge(`duże wejście: ${fmtLen(s.input.length)}`, 'neutral') : null,
      h('div', { class: 'grow' }), extra || null),
    h('div', { class: 'tk-sample-grid' },
      h('div', { class: 'tk-io' }, h('div', { class: 'tk-io-label' }, 'Wejście', copyButton(() => s.input)), h('pre', { class: 'tk-pre' }, shortInput(s.input))),
      h('div', { class: 'tk-io' }, h('div', { class: 'tk-io-label' }, 'Wyjście', copyButton(() => s.output)), h('pre', { class: 'tk-pre' }, shortInput(s.output)))),
    s.explanation ? h('div', { class: 'tk-expl small' }, icon('info', 13), h('span', null, s.explanation)) : null);
}

/** Bardzo długie wejście (np. liczba o milionie cyfr z testu „ocen”) — pokazujemy tylko początek i koniec. */
const BIG_SHOW = 4000;
export function bigInput(text) { return (text || '').length > BIG_SHOW; }
export function fmtLen(n) { return n >= 1e6 ? `${(n / 1e6).toFixed(1).replace('.', ',')} mln znaków` : n >= 1e4 ? `${Math.round(n / 1e3)} tys. znaków` : `${n} znaków`; }
export function shortInput(text) {
  const t = text || '';
  if (t.length <= BIG_SHOW) return t;
  return `${t.slice(0, 400)}\n… (pominięto ${fmtLen(t.length - 520)} — „Kopiuj” kopiuje całość) …\n${t.slice(-120)}`;
}

/** Pasek pewności 0..1. */
export function confidenceBar(p, { label } = {}) {
  const pct = Math.round(100 * Math.max(0, Math.min(1, p || 0)));
  const tone = pct >= 66 ? 'high' : pct >= 33 ? 'mid' : 'low';
  return h('div', { class: `tk-conf ${tone}`, title: `${label || 'Pewność'}: ${pct}%` },
    h('div', { class: 'tk-conf-track' }, h('div', { class: 'tk-conf-fill', style: { width: pct + '%' } })),
    h('span', { class: 'tk-conf-pct' }, pct + '%'));
}

export function loadingCard(text) {
  return h('div', { class: 'tk-loading card' }, h('div', { class: 'card-body row' }, spinner(18), h('span', { class: 'muted' }, text)));
}

export function warningsBlock(warnings) {
  if (!warnings || !warnings.length) return null;
  return h('div', { class: 'stack gap-s' }, warnings.map((w) => callout(w, 'warn')));
}

export function kbChips(ctx, links) {
  if (!links || !links.length) return null;
  return h('div', { class: 'row gap-s tk-kb' }, links.map((k) => h('button', { class: 'chip', type: 'button', title: k.category || 'Baza wiedzy',
    onclick: () => ctx.navigate('docs', { id: k.id }) }, icon('book-open', 13), k.title)));
}

// ============================================================================================ widok
const PIT_TONE = { danger: 'danger', warn: 'warn', info: 'info', ok: 'tip' };

export default {
  id: 'taskexplain',
  title: 'Objaśniacz treści',
  icon: 'book-open',
  group: 'Zadania',
  order: 10,
  description: 'W skrócie: dane → cel → postać odpowiedzi; typ zadania, struktura wejścia (drzewo, graf, zapytania…), zmienne, przykład rozpisany na zmienne, pułapki i budżet złożoności.',
  keywords: ['treść', 'zadanie', 'objaśnij', 'streszczenie', 'w skrócie', 'format wejścia', 'ograniczenia', 'pułapki', 'pdf', 'podzadania', 'struktura', 'drzewo', 'zapytania'],

  render(root, ctx) {
    const self = this;
    const out = h('div', { class: 'tk-results stack' });
    const panel = statementPanel({ actionLabel: 'Objaśnij treść', actionIcon: 'book-open', onRun: (t) => analyze(t) });
    root.append(
      viewHeader(this),
      h('div', { class: 'tk-layout' },
        h('div', { class: 'tk-left stack' }, panel.el, toolSwitcher(ctx, 'taskexplain', panel.getText)),
        out));
    out.append(emptyState({ icon: 'book-open', title: 'Wklej treść zadania',
      text: 'Dostaniesz jednozdaniowe „W skrócie”, typ zadania, rozpoznaną strukturę wejścia, przykład rozpisany na zmienne, pułapki (long long, modulo, indeksy od 1…) i budżet złożoności.' }));
    self._panel = panel;
    self._analyze = analyze;

    async function analyze(text) {
      clear(out).append(loadingCard('Czytam treść…'));
      try {
        const r = await api.post('/api/taskexplain', { statement: text });
        clear(out).append(...renderResult(r, ctx));
      } catch (e) {
        clear(out).append(emptyState({ icon: 'circle-alert', title: 'Nie udało się przeanalizować treści', text: e.message || String(e) }));
      }
    }
  },

  onShow(params) {
    if (params && params.statement && this._panel) {
      this._panel.setText(params.statement);
      this._analyze(params.statement);
    } else if (this._panel) this._panel.refresh();
  },
};

const TYPE_TONE = { optimize: 'accent', count: 'info', decision: 'warn', construct: 'success', queries: 'info',
  simulate: 'neutral', interactive: 'danger', compute: 'neutral' };
const KIND_ICON = { tree: 'list-tree', graph: 'network', grid: 'grid-3x3', strings: 'file-text', array: 'layers', pairs: 'layers',
  values: 'sigma', queries: 'list-checks', unknown: 'info' };

function renderResult(r, ctx) {
  const parts = [];
  // --- nagłówek: tytuł, limity, „W skrócie”, typ zadania
  const lim = [];
  if (r.limits?.time) lim.push(badge(`czas ${r.limits.time} s`, 'info', 'timer'));
  if (r.limits?.memory) lim.push(badge(`pamięć ${r.limits.memory}`, 'info', 'database'));
  if (r.complexity?.complexity) lim.push(badge(`cel: ${r.complexity.complexity}`, 'accent', 'gauge'));
  if (r.meta?.code) lim.push(badge(r.meta.code, 'neutral', 'bookmark'));
  const tt = r.task_type || {};
  const typeChips = (tt.tags?.length ? tt.tags : [tt.id]).filter(Boolean).map((t, i) =>
    badge((tt.labels || [])[i] || tt.label || t, TYPE_TONE[t] || 'neutral'));
  const briefText = r.brief?.text || (r.summary || [])[0] || '';
  parts.push(h('div', { class: 'tk-hero card' }, h('div', { class: 'card-body stack gap-s' },
    h('div', { class: 'row between' }, h('h2', { class: 'tk-title' }, r.title || 'Zadanie'), h('div', { class: 'row gap-s' }, lim)),
    briefText ? h('div', { class: 'tk-brief' }, h('span', { class: 'tk-brief-label' }, 'W skrócie'), h('span', null, briefText))
      : h('div', { class: 'muted' }, 'Nie udało się zbudować streszczenia.'),
    typeChips.length ? h('div', { class: 'tk-type' }, h('div', { class: 'row gap-s' }, typeChips),
      tt.consequence ? h('div', { class: 'small tk-type-cons' }, markdown(tt.consequence)) : null) : null,
    r.meta?.header_note ? h('div', { class: 'small muted' }, icon('info', 13), ' Nagłówek: ', r.meta.header_note) : null)));
  const w = warningsBlock(r.warnings);
  if (w) parts.push(w);

  // --- struktura danych
  const st = r.structure || {};
  const items = st.items || [];
  if (items.length || st.label) {
    const qs = (st.queries || []).filter((q) => q.tag || (q.params || []).length);
    parts.push(card({
      title: 'Struktura wejścia', icon: KIND_ICON[st.kind] || 'layers',
      subtitle: st.verified ? 'potwierdzona na przykładzie ✔' : 'niepotwierdzona na przykładzie — sprawdź',
      actions: st.label ? [badge(st.label, 'accent')] : [],
      body: h('div', { class: 'stack gap-s' },
        h('ol', { class: 'tk-format' }, items.map((f) => h('li', null,
          h('div', { class: 'tk-fmt-row' }, h('span', { class: 'tk-line mono' }, `wiersz ${f.line ?? '?'}`),
            h('span', null, f.text || (f.vars || []).join(', ')))))),
        (st.notes || []).length ? h('ul', { class: 'tk-notes small' }, st.notes.map((n) => h('li', null, n))) : null,
        qs.length ? queryTable(qs) : null),
    }));
  }

  // --- zmienne
  const vars = r.variables || [];
  if (vars.length) {
    parts.push(card({
      title: 'Zmienne i zakresy', icon: 'table', padded: false,
      body: table([
        { key: 'name', label: 'Zmienna', render: (v) => h('code', { title: v.auto_name ? 'nazwa nadana (w treści bez nazwy)' : '' }, v.name) },
        { key: 'meaning', label: 'Znaczenie', render: (v) => h('span', { class: 'small' }, v.meaning || '—') },
        { key: 'range', label: 'Zakres', align: 'right', render: (v) => h('span', { class: 'mono small nowrap' },
          (v.min_fmt || v.max_fmt) ? `${v.min_fmt || '?'} … ${v.max_fmt || '?'}` : '—',
          v.guessed ? h('span', { class: 'tone-warn', title: 'Potęga odtworzona z PDF — sprawdź' }, ' ?') : null) },
        { key: 'type', label: 'Typ', render: (v) => badge(v.type, v.type === 'long long' ? 'warn' : v.type?.startsWith('string') ? 'info' : 'neutral') },
        { key: 'role', label: 'Rola', render: (v) => h('span', { class: `small tk-role ${v.kind || ''}` }, v.role) },
      ], vars, { compact: true }),
    }));
  }

  // --- przykład rozpisany
  const walk = r.walkthrough || [];
  const samples = r.samples || [];
  if (walk.length || samples.length) {
    parts.push(card({
      title: walk.length ? 'Przykład rozpisany' : `Przykłady (${samples.length})`, icon: 'flask-conical',
      body: h('div', { class: 'stack gap-s' },
        walk.map((wk) => h('div', { class: 'tk-walk' },
          h('div', { class: 'tk-walk-head' }, icon('flask-conical', 13), h('b', null, wk.name)),
          h('ul', { class: 'tk-walk-in mono' }, wk.input.map((x) => h('li', null, x))),
          wk.output ? h('div', { class: 'tk-walk-out mono' }, icon('arrow-right', 13), ' ', wk.output) : null,
          wk.explanation ? h('div', { class: 'tk-expl small' }, icon('info', 13), h('span', null, wk.explanation)) : null)),
        samples.length ? (walk.length
          ? more(`Pełne przykłady (${samples.length})`, h('div', { class: 'stack' }, samples.map((s, i) => sampleBox(s, i))), { key: 'tk-samples' })
          : h('div', { class: 'stack' }, samples.map((s, i) => sampleBox(s, i)))) : null),
    }));
  }

  // --- pułapki
  const pits = r.pitfalls || [];
  parts.push(card({
    title: 'Na co uważać', icon: 'triangle-alert', subtitle: pits.length ? `${pits.length} wykrytych spraw` : 'nic szczególnego nie wykryłem',
    body: pits.length ? h('div', { class: 'tk-pits' }, pits.map((p) => h('div', { class: `tk-pit ${PIT_TONE[p.severity] || 'info'}` },
      h('div', { class: 'tk-pit-icon' }, icon(p.icon || 'info', 16)),
      h('div', { class: 'grow' }, h('div', { class: 'tk-pit-title' }, p.title), h('div', { class: 'small' }, markdown(p.text))))))
      : h('div', { class: 'muted' }, 'Sprawdź mimo to typy (int/long long) i przypadki skrajne.'),
  }));

  // --- złożoność i budżet
  const cx = r.complexity || {};
  if (cx.budget_text || cx.complexity) {
    const rows = [];
    if (cx.budget_text) rows.push(h('div', { class: 'small' }, h('b', null, 'Budżet: '), cx.budget_text));
    if (cx.complexity) rows.push(h('div', { class: 'small' }, h('b', null, 'Wystarczy: '), h('code', null, cx.complexity),
      cx.var && cx.max_fmt ? h('span', { class: 'muted' }, ` (${cx.var} ≤ ${cx.max_fmt}${cx.basis === 'wartość' ? ', to wartość — nie liczba danych' : ''})`) : null));
    if (cx.note) rows.push(h('div', { class: 'small muted' }, cx.note));
    if ((cx.hints || []).length) rows.push(h('ul', { class: 'tk-hints small' }, cx.hints.map((x) => h('li', null, x.text,
      x.why ? h('span', { class: 'muted' }, ` — ${x.why}`) : null))));
    parts.push(card({ title: 'Złożoność', icon: 'gauge', body: h('div', { class: 'stack gap-s' }, rows) }));
  }

  // --- podzadania
  const subs = r.subtasks || [];
  if (subs.length) {
    parts.push(card({
      title: 'Podzadania', icon: 'list-checks', padded: false,
      body: table([
        { key: 'id', label: '#', render: (s) => h('b', null, String(s.id ?? '')) },
        { key: 'conditions', label: 'Warunki', render: (s) => h('span', { class: 'small' }, s.conditions || '—') },
        { key: 'points', label: 'Punkty', align: 'right', render: (s) => s.points !== undefined && s.points !== null ? badge(`${s.points}`, 'accent') : '—' },
        { key: 'complexity', label: 'Wystarczy', render: (s) => s.complexity ? h('span', { title: s.hint || '' }, h('code', null, s.complexity)) : '—' },
      ], subs, { compact: true }),
    }));
  }

  // --- szczegóły (schowane)
  const details = [];
  if ((r.summary || []).length || (r.given || []).length || (r.goal || []).length) {
    details.push(more('Zdania z treści (streszczenie, dane, cel)', h('div', { class: 'stack gap-s' },
      (r.summary || []).length ? h('div', { class: 'tk-summary' }, r.summary.map((s) => h('p', null, s))) : null,
      h('div', { class: 'grid-2 tk-grid' },
        h('div', null, h('div', { class: 'tk-sub' }, 'Co jest dane'), listOrEmpty(r.given, 'Brak wyraźnego opisu danych w treści.')),
        h('div', null, h('div', { class: 'tk-sub' }, 'Co trzeba obliczyć'), listOrEmpty(r.goal, 'Zajrzyj do sekcji Wyjście.')))),
    { key: 'tk-sentences' }));
  }
  const fmt = r.input?.format || [];
  if (fmt.length || r.input?.text || r.output?.text) {
    details.push(more('Wejście i wyjście — tekst z treści', h('div', { class: 'stack gap-s small' },
      r.input?.text ? h('div', null, h('div', { class: 'tk-sub' }, 'Wejście'), h('div', null, r.input.text)) : null,
      r.output?.text ? h('div', null, h('div', { class: 'tk-sub' }, 'Wyjście'), h('div', null, r.output.text),
        r.output.items?.length ? h('div', { class: 'row gap-s' }, r.output.items.map((x) => badge(x, 'accent'))) : null) : null),
    { key: 'tk-io' }));
  }
  const gl = r.glossary || [];
  if (gl.length) {
    details.push(more(`Słowniczek (${gl.length})`, h('div', { class: 'tk-gloss' }, gl.map((g) => h('div', { class: 'tk-gl' },
      h('div', { class: 'tk-gl-term' }, g.term), h('div', { class: 'small' }, g.definition), kbChips(ctx, g.kb)))), { key: 'tk-gloss' }));
  }
  if (details.length) parts.push(h('div', { class: 'card tk-details' }, h('div', { class: 'card-body stack gap-s' }, details)));
  return parts;
}

function queryTable(qs) {
  return h('div', { class: 'tk-queries' }, table([
    { key: 'tag', label: 'Zapytanie', render: (q) => h('code', null, [q.tag, ...(q.params || [])].filter(Boolean).join(' ') || '—') },
    { key: 'meaning', label: 'Znaczenie', render: (q) => h('span', { class: 'small' }, q.meaning || '—',
      q.param_meaning ? h('span', { class: 'muted' }, ` (${(q.params || []).join(', ')}: ${q.param_meaning})`) : null) },
    { key: 'prints', label: 'Wypisuje?', render: (q) => q.prints === true ? badge('tak', 'accent') : q.prints === false ? h('span', { class: 'muted small' }, 'nie') : '—' },
  ], qs, { compact: true }));
}

function listOrEmpty(items, empty) {
  if (!items || !items.length) return h('div', { class: 'muted small' }, empty);
  return h('ul', { class: 'tk-list' }, items.map((s) => h('li', null, s)));
}

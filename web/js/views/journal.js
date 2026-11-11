// Dziennik postępów i błędów: rozwiązane zadania z SIO2 (oś czasu, tematy, serie, heatmapa) + ranking najczęstszych błędów.
import { h, icon, button, viewHeader, card, tabs, stat, badge, emptyState, segmented, clear, plural, table, callout, timeAgo, modal, markdown } from '../ui.js';
import { api, local } from '../api.js';
import { lineChart, barChart, heatmap } from '../charts.js';

const DAY_MS = 86400000;
const SOURCE_TONE = { checker: 'accent', compile: 'danger', runtime: 'warn', tester: 'info' };
const SEV_LABEL = { error: 'błąd', warning: 'ostrzeżenie', perf: 'wydajność', style: 'styl', info: 'info' };
const VERDICT_TONE = { OK: 'success', WA: 'danger', RE: 'danger', TLE: 'warn', MLE: 'warn', CE: 'danger' };
const KIND_LABEL = {
  run: ['Uruchomienie', 'play'], tester_result: ['Testerka', 'flask-conical'], compile_error: ['Błąd kompilacji', 'triangle-alert'],
  checker_issue: ['Sprawdzarka', 'bug'], kb_view: ['Artykuł', 'book-open'], assistant_q: ['Pytanie', 'message-square'],
  flashcard_review: ['Fiszka', 'rotate-ccw'], contest_start: ['Start zawodów', 'timer'], contest_stop: ['Koniec zawodów', 'trophy'],
};

let ctxRef = null;
const cache = { progress: null, progressAt: 0, stats: null, statsAt: 0 };

const dayNum = (iso) => Math.round(Date.parse(iso + 'T00:00:00Z') / DAY_MS);
const fmtDay = (n, opts = { day: '2-digit', month: '2-digit' }) => new Date(n * DAY_MS).toLocaleDateString('pl-PL', { ...opts, timeZone: 'UTC' });
const pct = (x) => (x === null || x === undefined ? '—' : `${Math.round(x * 100)}%`);

function openKb(id, from = 'journal') {
  if (!id) return;
  api.post('/api/journal/log', { kind: 'kb_view', data: { id, from } }, { quiet: true }).catch(() => {});
  if (window.OIF?.views?.has('docs')) { ctxRef.navigate('docs', { id }); return; }
  api.get('/api/kb/article', { id }).then(({ article: a }) => {
    const blocks = (a.body || []).filter((b) => b.md).slice(0, 4).map((b) => b.md).join('\n\n');
    modal({ title: a.title, icon: 'book-open', width: 720, body: h('div', { class: 'md' }, markdown(`${a.summary || ''}\n\n${blocks}`)) });
  }).catch(() => {});
}
function openProgram(p) {
  ctxRef.navigate('programs', { id: p.id, q: p.problem_name });
}

export default {
  id: 'journal', title: 'Dziennik postępów', icon: 'activity', group: 'Trening', order: 2,
  description: 'Twoje postępy z SIO2 (tematy, serie, kalendarz) i dziennik najczęstszych błędów z linkami do bazy wiedzy.',
  keywords: ['dziennik', 'postępy', 'statystyki', 'błędy', 'seria', 'streak', 'heatmapa', 'progres', 'journal'],
  render(root, ctx) {
    ctxRef = ctx;
    const t = tabs([
      { id: 'progress', label: 'Postępy', icon: 'trophy', render: () => panel(renderProgress) },
      { id: 'errors', label: 'Błędy i nawyki', icon: 'bug', render: () => panel(renderErrors) },
      { id: 'activity', label: 'Aktywność', icon: 'history', render: () => panel(renderActivity) },
    ], { active: local.get('journal:tab', 'progress'), onChange: (id) => local.set('journal:tab', id) });
    this._tabs = t;
    root.append(viewHeader(this, button('Odśwież', { icon: 'refresh-cw', variant: 'ghost', onClick: () => refreshAll(t) })), t.el);
  },
  onShow() {
    if (this._tabs && Date.now() - Math.max(cache.progressAt, cache.statsAt) > 120000) refreshAll(this._tabs);
  },
};

function panel(fn) {
  const host = h('div', { class: 'jr-panel' });
  host._render = () => fn(host);
  host._render();
  return host;
}
function refreshAll(t) {
  cache.progress = null; cache.stats = null;
  for (const id of ['progress', 'errors', 'activity']) {
    const p = t.panel(id)?.firstChild;
    if (p && p._render) p._render();
  }
}
function loading(host) {
  clear(host).append(h('div', { class: 'grid-4' }, [0, 1, 2, 3].map(() => h('div', { class: 'stat skeleton', style: 'height:86px' }))),
    h('div', { class: 'skeleton mt-m', style: 'height:280px' }));
}
async function getProgress() {
  if (!cache.progress) { cache.progress = await api.get('/api/journal/progress'); cache.progressAt = Date.now(); }
  return cache.progress;
}
async function getStats(days) {
  if (!cache.stats || cache.stats.days !== days) { cache.stats = await api.get('/api/journal/stats', { days }); cache.statsAt = Date.now(); }
  return cache.stats;
}

// ================================================================================================ POSTĘPY
async function renderProgress(host) {
  loading(host);
  let p;
  try { p = await getProgress(); } catch (e) { clear(host).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać postępów', text: e.message })); return; }
  clear(host);
  if (!p.has_data) {
    host.append(emptyState({ icon: 'trophy', title: 'Brak rozwiązań do pokazania',
      text: 'Dziennik postępów liczy się z Twoich zaakceptowanych rozwiązań. Zsynchronizuj je z SIO2 w „Moich programach”.',
      action: button('Moje programy', { icon: 'folder', variant: 'primary', onClick: () => ctxRef.navigate('programs') }) }));
    return;
  }
  const st = p.streak;
  const lastAgo = p.last ? Math.round((Date.now() - Date.parse(p.last + 'T12:00:00')) / DAY_MS) : null;
  let motto;
  if (st.active_today) motto = 'Dziś już jest rozwiązane zadanie — tak trzymaj!';
  else if (st.current > 0) motto = `Rozwiąż dziś cokolwiek, żeby przedłużyć serię ${st.current} ${plural(st.current, 'dnia', 'dni', 'dni')}.`;
  else if (st.current_weeks > 0) motto = `Masz serię ${st.current_weeks} ${plural(st.current_weeks, 'tygodnia', 'tygodni', 'tygodni')} z rzędu — jedno zadanie w tym tygodniu ją przedłuży.`;
  else motto = 'Zacznij nową serię — jedno zadanie dziennie robi ogromną różnicę.';
  host.append(h('div', { class: 'jr-hero' },
    h('div', { class: 'jr-hero-num' }, h('div', { class: 'jr-big' }, String(p.totals.problems)), h('div', { class: 'muted' }, plural(p.totals.problems, 'rozwiązane zadanie', 'rozwiązane zadania', 'rozwiązanych zadań'))),
    h('div', { class: 'grow' },
      h('div', { class: 'jr-hero-title' }, motto),
      h('div', { class: 'jr-hero-sub' }, `Od ${fmtDay(dayNum(p.first), { day: 'numeric', month: 'long', year: 'numeric' })}: ${p.totals.submissions} zgłoszeń za 100 pkt w ${p.totals.contests} ${plural(p.totals.contests, 'konkursie', 'konkursach', 'konkursach')}`
        + (lastAgo !== null ? ` · ostatnie ${lastAgo <= 0 ? 'dziś' : lastAgo === 1 ? 'wczoraj' : `${lastAgo} dni temu`}` : '') + '.'),
      h('div', { class: 'row gap-s mt-s' },
        button('Ścieżka nauki', { icon: 'git-branch', size: 'sm', onClick: () => ctxRef.navigate('roadmap') }))),
    h('div', { class: 'jr-flame' }, icon('zap', 26), h('div', { class: 'jr-flame-num' }, String(st.current_weeks)), h('div', { class: 'tiny muted' }, plural(st.current_weeks, 'tydzień', 'tygodnie', 'tygodni')))));

  host.append(h('div', { class: 'grid-4 mt-m' },
    stat({ label: 'Rozwiązane zadania', value: p.totals.problems, sub: `${p.totals.submissions} zgłoszeń za 100 pkt`, icon: 'circle-check', tone: 'success' }),
    stat({ label: 'Aktywne dni', value: p.totals.active_days, sub: `średnio ${String(p.totals.per_week).replace('.', ',')} zadania tygodniowo`, icon: 'activity' }),
    stat({ label: 'Seria dni', value: `${st.current}`, sub: `rekord: ${st.longest} ${plural(st.longest, 'dzień', 'dni', 'dni')}`, icon: 'zap', tone: st.current ? 'accent' : null }),
    stat({ label: 'Seria tygodni', value: `${st.current_weeks}`, sub: `rekord: ${st.longest_weeks} ${plural(st.longest_weeks, 'tydzień', 'tygodnie', 'tygodni')}`, icon: 'trophy', tone: st.current_weeks ? 'accent' : null })));

  // --- oś czasu i kalendarz
  const cum = p.cumulative.map((c) => [dayNum(c.date), c.total]);
  const months = p.months.slice(-14);
  host.append(h('div', { class: 'jr-grid-wide mt-m' },
    card({ title: 'Rozwiązane zadania w czasie', icon: 'activity', subtitle: 'Łączna liczba unikalnych zadań',
      body: lineChart([{ name: 'Zadania', points: cum }], { height: 240, xFormat: (x) => fmtDay(Math.round(x), { month: 'short', year: '2-digit' }), yFormat: (y) => String(Math.round(y)) }) }),
    card({ title: 'Miesiące', icon: 'table', subtitle: 'Zgłoszenia za 100 pkt',
      body: barChart(months.map((m) => ({ label: new Date(m.month + '-01T00:00:00').toLocaleDateString('pl-PL', { month: 'short' }), value: m.value })), { horizontal: false, height: 200 }) })));

  const maxDay = Math.max(1, ...Object.values(p.heatmap));
  host.append(card({ title: 'Kalendarz aktywności', icon: 'grid-3x3', subtitle: 'Każdy kwadrat to dzień — im ciemniejszy, tym więcej rozwiązań', class: 'mt-m',
    body: h('div', { class: 'stack gap-s' }, h('div', { class: 'jr-heat' }, heatmap(p.heatmap, { weeks: 53 })),
      h('div', { class: 'row between small muted' },
        h('span', null, `Najaktywniejszy dzień: ${maxDay} ${plural(maxDay, 'rozwiązanie', 'rozwiązania', 'rozwiązań')}`),
        h('span', { class: 'row gap-s' }, 'mniej', [0, 0.3, 0.55, 0.8, 1].map((o) => h('i', { class: 'jr-sw', style: o ? `background:var(--accent);opacity:${0.25 + 0.75 * o}` : '' })), 'więcej'))) }));

  // --- tematy
  const strongest = p.strongest || [];
  const weakest = p.weakest || [];
  host.append(h('div', { class: 'grid-2 mt-m' },
    card({ title: 'Najmocniejsze tematy', icon: 'star', subtitle: 'Najwięcej rozwiązanych zadań',
      body: strongest.length ? barChart(strongest.map((t) => ({ label: t.title, value: t.solved, tone: 'success' })), { format: (v) => `${v}` })
        : emptyState({ icon: 'star', text: 'Brak dopasowanych tematów.' }) }),
    card({ title: 'Do podciągnięcia', icon: 'target', subtitle: 'Odblokowane tematy z najmniejszym postępem',
      body: weakest.length ? h('div', { class: 'jr-weak' }, weakest.map((t) => weakRow(t))) : emptyState({ icon: 'circle-check', text: 'Wszystkie odblokowane tematy mają komplet zadań!' }) })));

  const withTasks = (p.topics || []).filter((t) => t.solved > 0).sort((a, b) => b.solved - a.solved);
  host.append(h('div', { class: 'jr-grid-wide mt-m' },
    card({ title: 'Tematy na ścieżce nauki', icon: 'git-branch', subtitle: `${withTasks.length} ${plural(withTasks.length, 'temat', 'tematy', 'tematów')} z rozwiązanymi zadaniami`,
      actions: [button('Ścieżka', { size: 'sm', variant: 'ghost', iconRight: 'arrow-right', onClick: () => ctxRef.navigate('roadmap') })],
      body: barChart(withTasks.map((t) => ({ label: t.title, value: t.solved, tone: t.solved >= t.target ? 'success' : undefined })), { format: (v) => String(v) }) }),
    card({ title: 'Dni tygodnia', icon: 'clock', subtitle: 'Kiedy rozwiązujesz',
      body: barChart(p.by_weekday.map((d) => ({ label: d.label, value: d.value })), { horizontal: false, height: 180 }) })));

  host.append(card({ title: 'Ostatnio rozwiązane', icon: 'history', class: 'mt-m', padded: false,
    body: table([
      { key: 'problem_name', label: 'Zadanie', render: (r) => h('div', null, h('b', null, r.problem_name), ' ', h('span', { class: 'muted mono small' }, r.problem_short || '')) },
      { key: 'contest_name', label: 'Konkurs', render: (r) => h('span', { class: 'muted' }, r.contest_name || '') },
      { key: 'round', label: 'Runda', render: (r) => r.round ? badge(r.round, 'neutral') : '' },
      { key: 'time', label: 'Kiedy', align: 'right', render: (r) => h('span', { title: r.time }, r.time ? timeAgo(r.time) : '—') },
    ], p.recent, { onRowClick: openProgram, compact: true }) }));

  if ((p.unmatched_rounds || []).length) {
    host.append(h('div', { class: 'muted small mt-m' }, 'Rundy bez przypisanego tematu (np. warsztaty, zawody): ',
      p.unmatched_rounds.map((r) => h('span', { class: 'chip jr-chip', onclick: () => ctxRef.navigate('programs', { q: r }) }, r))));
  }
}

function weakRow(t) {
  const prog = t.has_tasks ? Math.min(1, t.solved / Math.max(1, t.target)) : t.progress;
  return h('div', { class: 'jr-weak-row' },
    h('div', { class: 'grow' },
      h('div', { class: 'row between' }, h('div', { class: 'bold' }, t.title), h('span', { class: 'muted small' }, t.has_tasks ? `${t.solved}/${t.target} zadań` : pct(t.progress))),
      h('div', { class: 'jr-bar' }, h('div', { style: { width: `${Math.round(prog * 100)}%` } })),
      h('div', { class: 'tiny muted' }, t.stage_title)),
    h('div', { class: 'row gap-s nowrap' },
      t.kb_first ? button('Czytaj', { icon: 'book-open', size: 'sm', variant: 'ghost', onClick: () => openKb(t.kb_first) }) : null,
      button('Na ścieżce', { size: 'sm', iconRight: 'arrow-right', onClick: () => ctxRef.navigate('roadmap', { topic: t.id }) })));
}

// ================================================================================================ BŁĘDY
async function renderErrors(host) {
  const days = local.get('journal:days', 30);
  loading(host);
  let s;
  try { s = await getStats(days); } catch (e) { clear(host).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać dziennika', text: e.message })); return; }
  clear(host);
  const seg = segmented([7, 30, 90, 365].map((d) => ({ value: String(d), label: `${d} dni` })), {
    value: String(days), onChange: (v) => { local.set('journal:days', Number(v)); renderErrors(host); },
  });
  host.append(h('div', { class: 'row between' }, h('div', { class: 'muted small' }, s.first_event ? `Dziennik prowadzony od ${new Date(s.first_event).toLocaleDateString('pl-PL')}` : 'Dziennik jeszcze pusty'), seg));

  if (!s.window_events) {
    host.append(emptyState({ icon: 'bug', title: 'Brak zdarzeń w tym okresie',
      text: 'Dziennik zapełnia się sam: uruchamiaj programy, sprawdzaj kod w Sprawdzarce, testuj w Testerce i czytaj artykuły. Tu zobaczysz, jakie błędy powtarzają się najczęściej.',
      action: button('Otwórz Sprawdzarkę', { icon: 'bug', variant: 'primary', onClick: () => ctxRef.navigate('checker') }) }));
    return;
  }
  host.append(h('div', { class: 'grid-4 mt-m' },
    stat({ label: 'Uruchomienia', value: s.runs.total, sub: s.runs.ok_rate !== null ? `${pct(s.runs.ok_rate)} bez błędu` : 'brak', icon: 'play' }),
    stat({ label: 'Testy w Testerce', value: s.tester.tests, sub: `${s.tester.sessions} ${plural(s.tester.sessions, 'sesja', 'sesje', 'sesji')}, ${s.tester.failed} z błędem`, icon: 'flask-conical' }),
    stat({ label: 'Błędy kompilacji', value: s.compile_errors, icon: 'triangle-alert', tone: s.compile_errors ? 'warn' : null }),
    stat({ label: 'Uwagi Sprawdzarki', value: s.checker_issues, icon: 'bug', tone: s.checker_issues ? 'warn' : null })));

  const w = s.week;
  if (w.errors_now || w.errors_prev) {
    const diff = w.errors_now - w.errors_prev;
    host.append(h('div', { class: 'mt-m' }, diff < 0
      ? callout(`W tym tygodniu **${-diff} ${plural(-diff, 'błąd', 'błędy', 'błędów')} mniej** niż w poprzednim (${w.errors_now} vs ${w.errors_prev}). Świetnie — nawyki działają!`, 'tip')
      : diff > 0 ? callout(`W tym tygodniu ${w.errors_now} ${plural(w.errors_now, 'błąd', 'błędy', 'błędów')} (tydzień wcześniej ${w.errors_prev}). Zajrzyj do rankingu poniżej — jeden artykuł może wyeliminować całą grupę.`, 'info')
        : callout(`Tyle samo błędów co tydzień temu (${w.errors_now}).`, 'info')));
  }

  const top = s.top_errors || [];
  host.append(card({ title: 'Najczęstsze błędy', icon: 'list-checks', class: 'mt-m', subtitle: 'Ranking z ostatnich ' + s.days + ' dni — kliknij „Jak unikać?”, żeby przeczytać o przyczynie',
    body: top.length ? h('div', { class: 'jr-errs' }, top.slice(0, 15).map((e) => errRow(e, top[0].count))) : emptyState({ icon: 'circle-check', title: 'Bez błędów!', text: 'W tym okresie nie zanotowano żadnego błędu.' }) }));

  const tr = s.trend;
  const x0 = tr.length ? dayNum(tr[0].date) : 0;
  host.append(h('div', { class: 'jr-grid-wide mt-m' },
    card({ title: 'Trend', icon: 'activity', subtitle: 'Dziennie: błędy i uruchomienia',
      body: lineChart([
        { name: 'Błędy', points: tr.map((d, i) => [x0 + i, d.errors]), color: 'var(--danger)' },
        { name: 'Uruchomienia', points: tr.map((d, i) => [x0 + i, d.runs]), color: 'var(--info)' },
        { name: 'Testy (×100)', points: tr.map((d, i) => [x0 + i, Math.round(d.tests / 100)]), color: 'var(--success)', dashed: true },
      ], { height: 240, xFormat: (x) => fmtDay(Math.round(x)), yFormat: (y) => String(Math.round(y)) }) }),
    card({ title: 'Werdykty', icon: 'gauge', subtitle: 'Uruchomienia i Testerka',
      body: h('div', { class: 'stack' },
        s.runs.verdicts.length ? barChart(s.runs.verdicts.map((v) => ({ label: `Uruchomienie: ${v.label}`, value: v.value, tone: VERDICT_TONE[v.label] }))) : null,
        s.tester.verdicts.length ? barChart(s.tester.verdicts.map((v) => ({ label: `Testerka: ${v.label}`, value: v.value, tone: VERDICT_TONE[v.label] }))) : null,
        !s.runs.verdicts.length && !s.tester.verdicts.length ? h('div', { class: 'muted small' }, 'Brak uruchomień w tym okresie.') : null) })));
}

function errRow(e, max) {
  const trend = e.trend === 'up' ? h('span', { class: 'jr-trend up', title: `Ostatnie 7 dni: ${e.recent}, tydzień wcześniej: ${e.prev}` }, '▲ ' + e.recent)
    : e.trend === 'down' ? h('span', { class: 'jr-trend down', title: `Ostatnie 7 dni: ${e.recent}, tydzień wcześniej: ${e.prev}` }, '▼ ' + e.recent)
      : h('span', { class: 'jr-trend flat', title: 'Bez zmian tydzień do tygodnia' }, '– ' + e.recent);
  return h('div', { class: 'jr-err' },
    h('div', { class: ['jr-rank', e.rank <= 3 ? 'top' : ''].join(' ') }, String(e.rank)),
    h('div', { class: 'grow' },
      h('div', { class: 'jr-err-title' }, e.title),
      h('div', { class: 'row gap-s mt-s' }, badge(e.source_label, SOURCE_TONE[e.source] || 'neutral'),
        e.severity && SEV_LABEL[e.severity] ? h('span', { class: 'tiny muted' }, SEV_LABEL[e.severity]) : null,
        h('span', { class: 'tiny muted' }, `ostatnio ${timeAgo(new Date(e.last * 1000))}`)),
      h('div', { class: 'jr-bar thin' }, h('div', { style: { width: `${Math.round((100 * e.count) / Math.max(1, max))}%` } }))),
    h('div', { class: 'jr-err-count' }, h('div', { class: 'jr-count' }, String(e.count)), trend),
    e.kb ? button('Jak unikać?', { icon: 'book-open', size: 'sm', title: e.kb_title || '', onClick: () => openKb(e.kb) })
      : h('span', { class: 'jr-nokb' }));
}

// ================================================================================================ AKTYWNOŚĆ
async function renderActivity(host) {
  loading(host);
  let s, evs;
  try {
    [s, evs] = await Promise.all([getStats(local.get('journal:days', 30)), api.get('/api/journal/events', { limit: 60 }, { quiet: true }).then((r) => r.events).catch(() => [])]);
  } catch (e) { clear(host).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać aktywności', text: e.message })); return; }
  clear(host);
  if (!s.total_events) {
    host.append(emptyState({ icon: 'history', title: 'Jeszcze nic tu nie ma', text: 'Każde uruchomienie, test, przeczytany artykuł i pytanie do asystenta trafia do dziennika — lokalnie, tylko na tym komputerze.' }));
    return;
  }
  host.append(card({ title: 'Aktywność w OI Forge', icon: 'grid-3x3', subtitle: 'Zdarzenia dziennika w ostatnim roku', body: h('div', { class: 'jr-heat' }, heatmap(s.activity || {}, { weeks: 53 })) }));
  host.append(h('div', { class: 'grid-2 mt-m' },
    card({ title: 'Najczęściej czytane', icon: 'book-open', subtitle: `${s.kb_unique} ${plural(s.kb_unique, 'artykuł', 'artykuły', 'artykułów')} w ${s.days} dni`,
      body: s.kb_top.length ? h('div', { class: 'list' }, s.kb_top.map((a) => h('div', { class: 'list-item', onclick: () => openKb(a.id) },
        icon('book-open', 15), h('span', { class: 'grow ellipsis' }, a.title), badge(String(a.count), 'accent'))))
        : h('div', { class: 'muted small' }, 'Brak przeczytanych artykułów w tym okresie.') }),
    card({ title: 'Ostatnie pytania do asystenta', icon: 'message-square',
      body: s.questions.length ? h('div', { class: 'list' }, s.questions.slice(0, 12).map((q) => h('div', { class: 'list-item', onclick: () => ctxRef.navigate('assistant', { q: q.q }) },
        icon('message-square', 15), h('span', { class: 'grow ellipsis' }, q.q), h('span', { class: 'tiny muted nowrap' }, timeAgo(new Date(q.t * 1000))))))
        : h('div', { class: 'muted small' }, 'Brak pytań w tym okresie.') })));
  if ((s.question_terms || []).length) host.append(card({ title: 'O co pytasz najczęściej', icon: 'search', class: 'mt-m', body: barChart(s.question_terms) }));
  host.append(card({ title: 'Ostatnie zdarzenia', icon: 'history', class: 'mt-m', padded: false,
    body: h('div', { class: 'jr-feed' }, evs.map((e) => {
      const [label, ic] = KIND_LABEL[e.kind] || [e.kind, 'info'];
      return h('div', { class: 'jr-ev' }, h('span', { class: 'jr-ev-icon' }, icon(ic, 14)), h('span', { class: 'jr-ev-kind' }, label),
        h('span', { class: 'grow ellipsis muted' }, describe(e)), h('span', { class: 'tiny muted nowrap' }, timeAgo(new Date(e.t * 1000))));
    })) }));
}

function describe(e) {
  const d = e.data || {};
  switch (e.kind) {
    case 'run': return `werdykt ${d.verdict || '?'}`;
    case 'tester_result': return `${d.ok ? 'OK' : (d.verdict || 'błąd')} — ${d.tests ?? '?'} testów`;
    case 'compile_error': return d.title || '';
    case 'checker_issue': return `${d.title || d.rule || ''}${d.severity ? ` (${SEV_LABEL[d.severity] || d.severity})` : ''}`;
    case 'kb_view': return d.id || '';
    case 'assistant_q': return d.q || '';
    case 'flashcard_review': return `talia ${d.deck || '?'}, ocena ${d.grade ?? '?'}`;
    case 'contest_stop': return `${d.full ?? 0}/${d.tasks ?? 0} pełnych, ${d.points ?? 0} pkt`;
    default: return Object.entries(d).slice(0, 3).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ');
  }
}

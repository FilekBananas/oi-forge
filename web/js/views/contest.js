// Tryb zawodów OI: duży zegar, statusy zadań z notatkami, checklista przed wysłaniem, przypomnienia, historia sesji.
import {
  h, icon, button, iconButton, viewHeader, card, field, input, textarea, segmented, badge, toast, modal, markdown,
  emptyState, checkbox, confirmDialog, promptDialog, stat, clear, plural, debounce, callout, table,
} from '../ui.js';
import { api, local } from '../api.js';
import { barChart } from '../charts.js';

const PRESETS = [
  { id: 'oi', title: 'Symulacja II/III etapu', sub: '3 zadania · 5 godzin · bez pauz', duration: 300, tasks: 3, mode: 'contest', icon: 'trophy' },
  { id: 'i', title: 'Duży zestaw', sub: '5 zadań · 5 godzin', duration: 300, tasks: 5, mode: 'training', icon: 'layers' },
  { id: 'train', title: 'Trening', sub: '3 zadania · 3 godziny', duration: 180, tasks: 3, mode: 'training', icon: 'target' },
  { id: 'sprint', title: 'Sprint', sub: '2 zadania · 90 minut', duration: 90, tasks: 2, mode: 'training', icon: 'zap' },
];
const STATUS_TONE = { unread: 'neutral', reading: 'info', brute: 'warn', partial: 'accent', full: 'success' };
const EVENT_ICON = { start: 'circle-play', stop: 'circle-stop', status: 'refresh-cw', note: 'pencil', pause: 'pause', resume: 'play',
  reminder: 'clock', task: 'plus', mode: 'info', extend: 'timer' };

let S = null;              // ostatni stan z serwera
let offset = 0;            // zegar serwera − zegar klienta (s)
let ticker = null;
let poller = null;
let view = null;           // {root, ctx, body, actions, ...} gdy widok jest wyrenderowany
const taskViews = new Map();

// ------------------------------------------------------------------------------------------------ czas
const nowS = () => Date.now() / 1000 + offset;
function elapsedNow() {
  const s = S?.session;
  if (!s) return 0;
  return s.paused ? s.elapsed : s.elapsed + Math.max(0, nowS() - S.now);
}
function fmtClock(sec, { signed = false } = {}) {
  const neg = sec < 0;
  sec = Math.abs(Math.floor(sec));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  return `${neg && signed ? '−' : ''}${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}
function fmtShort(sec) {
  sec = Math.max(0, Math.round(sec));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60);
  if (hh) return `${hh} h ${String(mm).padStart(2, '0')} min`;
  if (mm) return `${mm} min`;
  return `${sec} s`;
}
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function clockTone(rem, paused) {
  if (paused) return 'paused';
  if (rem <= 0) return 'over';
  if (rem <= 300) return 'critical';
  if (rem <= 600) return 'danger';
  if (rem <= 1800) return 'warn';
  return 'ok';
}

// ------------------------------------------------------------------------------------------------ powiadomienia
function beep(times = 2) {
  if (local.get('contest:sound', true) === false) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ac = new Ctx();
    for (let i = 0; i < times; i++) {
      const o = ac.createOscillator(); const g = ac.createGain();
      o.type = 'sine'; o.frequency.value = i % 2 ? 660 : 880;
      const t = ac.currentTime + i * 0.28;
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
      o.connect(g).connect(ac.destination); o.start(t); o.stop(t + 0.25);
    }
    setTimeout(() => ac.close().catch(() => {}), 1500);
  } catch { /* brak audio */ }
}
function notify(text, tone = 'info') {
  toast(text, tone, { timeout: 14000 });
  beep(tone === 'warn' ? 3 : 2);
  try {
    if (local.get('contest:notify', false) && 'Notification' in window && Notification.permission === 'granted') {
      const n = new Notification('OI Forge — zawody', { body: text, tag: 'oiforge-contest', renotify: true });
      n.onclick = () => { window.focus(); window.OIF?.navigate?.('contest'); n.close(); };
    }
  } catch { /* Notification API niedostępne */ }
}

function checkReminders() {
  const s = S?.session;
  if (!s || s.paused) return;
  const el = elapsedNow();
  const due = (s.reminders || []).filter((r) => !r.fired && el >= r.at);
  if (!due.length) return;
  due.forEach((r) => { r.fired = true; });
  const last = due[due.length - 1];       // po długiej nieobecności pokazujemy tylko najnowsze
  notify(last.text, last.kind === 'backup' ? 'info' : 'warn');
  api.post('/api/contest/update', { ack: due.map((r) => r.id) }, { quiet: true }).then((st) => applyState(st)).catch(() => {});
}

// ------------------------------------------------------------------------------------------------ stan
function setState(st) {
  S = st;
  offset = (st.now || Date.now() / 1000) - Date.now() / 1000;
  ensureTimers();
}
function applyState(st) {
  if (!st) return;
  const prevActive = !!S?.active;
  const prevSig = taskSig(S);
  setState(st);
  if (!view) return;
  if (prevActive !== !!st.active || prevSig !== taskSig(st) || (st.active && !view.activeRendered)) renderBody();
  else if (st.active) refreshActive();
  else refreshHistory();
}
function taskSig(st) { return st?.session ? st.session.id + '|' + st.session.tasks.map((t) => t.id).join(',') : ''; }

function ensureTimers() {
  const active = !!S?.active;
  if (active && !ticker) ticker = setInterval(tick, 1000);
  if (!active && ticker) { clearInterval(ticker); ticker = null; }
  if (active && !poller) poller = setInterval(() => api.get('/api/contest/state', null, { quiet: true }).then((st) => applyState(st)).catch(() => {}), 30000);
  if (!active && poller) { clearInterval(poller); poller = null; }
}
function tick() {
  checkReminders();
  if (view && S?.active) refreshClock();
}

async function update(body) {
  try {
    const st = await api.post('/api/contest/update', body);
    applyState(st);
    return st;
  } catch { return null; }
}

// Start w tle: przypomnienia działają w całej aplikacji, nie tylko na tym widoku.
setTimeout(() => { api.get('/api/contest/state', null, { quiet: true }).then((st) => { if (!S) setState(st); }).catch(() => {}); }, 1200);

// ------------------------------------------------------------------------------------------------ widok
export default {
  id: 'contest', title: 'Tryb zawodów', icon: 'timer', group: 'Trening', order: 1,
  description: 'Symulacja zawodów OI: duży zegar, statusy zadań, notatki, checklista przed wysłaniem i przypomnienia o backupie.',
  keywords: ['zawody', 'contest', 'timer', 'zegar', 'symulacja', 'olimpiada', 'checklista', 'stoper'],
  async render(root, ctx) {
    const actions = h('div', { class: 'row gap-s' });
    const body = h('div', { class: 'ct-body' });
    root.append(viewHeader(this, actions), body);
    view = { root, ctx, body, actions, activeRendered: false };
    body.append(h('div', { class: 'stat skeleton', style: 'height:220px' }));
    try {
      setState(await api.get('/api/contest/state'));
      renderBody();
    } catch (e) {
      clear(body).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać stanu zawodów', text: e.message }));
    }
  },
  onShow() {
    if (view && S) api.get('/api/contest/state', null, { quiet: true }).then((st) => applyState(st)).catch(() => {});
  },
};

function renderBody() {
  if (!view) return;
  taskViews.clear();
  clear(view.body);
  clear(view.actions);
  view.activeRendered = false;
  if (S?.active) renderActive();
  else renderSetup();
}

// ------------------------------------------------------------------------------------------------ przygotowanie sesji
function renderSetup() {
  const { body, actions } = view;
  let preset = local.get('contest:preset', 'oi');
  const p0 = PRESETS.find((p) => p.id === preset) || PRESETS[0];
  const form = { title: '', duration: p0.duration, mode: p0.mode, tasks: Array.from({ length: p0.tasks }, () => ({ short: '', name: '' })) };
  const saved = local.get('contest:draft', null);
  if (saved && Array.isArray(saved.tasks)) Object.assign(form, saved);

  const titleIn = input({ value: form.title, placeholder: 'np. OI 32 — II etap, dzień 1', onInput: (v) => { form.title = v; saveDraft(); } });
  const durIn = input({ value: String(form.duration), type: 'number', attrs: { min: 5, max: 720, step: 5 }, onInput: (v) => { form.duration = parseInt(v, 10) || 0; saveDraft(); } });
  const modeSeg = segmented([{ value: 'contest', label: 'Zawody', icon: 'trophy', title: 'Bez pauz — jak na prawdziwych zawodach' },
    { value: 'training', label: 'Trening', icon: 'pause', title: 'Z możliwością pauzy' }], { value: form.mode, onChange: (v) => { form.mode = v; saveDraft(); } });
  const tasksBox = h('div', { class: 'stack gap-s' });
  const saveDraft = debounce(() => local.set('contest:draft', form), 300);

  function renderTasks() {
    clear(tasksBox);
    form.tasks.forEach((t, i) => {
      const letter = String.fromCharCode(65 + i);
      tasksBox.append(h('div', { class: 'ct-task-row-edit' },
        h('div', { class: 'ct-letter sm' }, letter),
        input({ value: t.short, placeholder: 'skrót', mono: true, class: 'ct-short-in', onInput: (v) => { t.short = v; saveDraft(); } }),
        input({ value: t.name, placeholder: `Nazwa zadania ${letter}`, onInput: (v) => { t.name = v; saveDraft(); } }),
        iconButton('x', { title: 'Usuń zadanie', onClick: () => { if (form.tasks.length > 1) { form.tasks.splice(i, 1); renderTasks(); saveDraft(); } } })));
    });
    if (form.tasks.length < 12) tasksBox.append(button('Dodaj zadanie', { icon: 'plus', variant: 'ghost', size: 'sm', onClick: () => { form.tasks.push({ short: '', name: '' }); renderTasks(); saveDraft(); } }));
  }
  renderTasks();

  const presetsEl = h('div', { class: 'ct-presets' }, PRESETS.map((p) => {
    const el = h('button', { type: 'button', class: ['ct-preset', p.id === preset ? 'active' : ''].join(' '),
      onclick: () => {
        preset = p.id; local.set('contest:preset', p.id);
        presetsEl.querySelectorAll('.ct-preset').forEach((b) => b.classList.toggle('active', b === el));
        form.duration = p.duration; durIn.value = String(p.duration);
        form.mode = p.mode; modeSeg.setValue(p.mode);
        const keep = form.tasks.filter((t) => t.name || t.short);
        form.tasks = Array.from({ length: Math.max(p.tasks, keep.length) }, (_, i) => keep[i] || { short: '', name: '' });
        renderTasks(); saveDraft();
      } },
    h('div', { class: 'ct-preset-icon' }, icon(p.icon, 18)),
    h('div', { class: 'grow' }, h('div', { class: 'ct-preset-title' }, p.title), h('div', { class: 'ct-preset-sub' }, p.sub)));
    return el;
  }));

  const start = async () => {
    const dur = parseInt(durIn.value, 10);
    if (!(dur >= 5 && dur <= 720)) { toast('Czas trwania: od 5 do 720 minut.', 'warn'); return; }
    const tasks = form.tasks.map((t, i) => ({ short: t.short.trim(), name: t.name.trim() || `Zadanie ${String.fromCharCode(65 + i)}` }));
    try {
      const st = await api.post('/api/contest/start', { duration_min: dur, tasks, title: titleIn.value.trim(), mode: form.mode });
      local.set('contest:draft', { ...form, title: '', tasks: form.tasks.map(() => ({ short: '', name: '' })) });
      if (local.get('contest:notify', false) && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
      applyState(st);
      toast('Powodzenia! Zacznij od przeczytania wszystkich zadań.', 'success');
    } catch { /* toast z api */ }
  };

  const setup = card({
    title: 'Nowa sesja', icon: 'timer', subtitle: 'Wybierz format, wpisz zadania i startuj',
    body: h('div', { class: 'stack' },
      presetsEl,
      h('div', { class: 'grid-2' }, field('Nazwa sesji', titleIn), h('div', { class: 'row gap-l top' },
        field('Czas (minuty)', durIn), field('Tryb', modeSeg))),
      field('Zadania', tasksBox, 'Skrót z SIO2 (np. kur) jest opcjonalny — przyda się w historii.'),
      h('div', { class: 'row' }, button('Start zawodów', { icon: 'play', variant: 'primary', size: 'lg', onClick: start }),
        h('span', { class: 'muted small' }, 'Stan zapisuje się na bieżąco — odświeżenie strony ani restart programu go nie skasują.'))),
  });

  const how = card({
    title: 'Jak to działa', icon: 'info',
    body: markdown([
      '- **Zegar** odlicza pozostały czas; żółknie 30 minut przed końcem, czerwienieje w ostatnich 10 minutach.',
      '- **Co godzinę** przypomnienie: *zrób backup i wyślij to, co masz*. 30 minut przed końcem: *wyślij wszystko, sprawdź long longi*.',
      '- **Status zadania** (nieczytane → czytam → brute → częściowe → pełne) liczy czas spędzony nad każdym zadaniem.',
      '- **Notatki** w markdown — pomysły, złożoność, przypadki brzegowe.',
      '- **Checklista** przed wysłaniem: long long, rozmiary tablic, szybkie IO, debugi, przykład, n = 1, pamięć.',
      '- Po zakończeniu dostajesz **podsumowanie**: czas na zadanie, punkty i wskazówki na następny raz.',
    ].join('\n')),
  });

  const histHost = h('div');
  view.historyHost = histHost;
  body.append(h('div', { class: 'ct-setup' }, setup, h('div', { class: 'stack' }, how, notifyCard())), histHost);
  refreshHistory();
  actions.append(button('Powtórka fiszek', { icon: 'rotate-ccw', variant: 'ghost', onClick: () => view.ctx.navigate('flashcards') }));
}

function notifyCard() {
  const supported = 'Notification' in window;
  const notifyBox = checkbox('Powiadomienia systemowe (gdy okno jest w tle)', {
    checked: !!local.get('contest:notify', false) && supported && Notification.permission === 'granted',
    onChange: async (v) => {
      if (v && supported && Notification.permission !== 'granted') {
        const perm = await Notification.requestPermission().catch(() => 'denied');
        if (perm !== 'granted') { toast('Przeglądarka nie pozwoliła na powiadomienia — zostaną same komunikaty w programie.', 'warn'); notifyBox.checked = false; local.set('contest:notify', false); return; }
      }
      local.set('contest:notify', v);
    },
  });
  if (!supported) { notifyBox.input.disabled = true; }
  const soundBox = checkbox('Dźwięk przy przypomnieniu', { checked: local.get('contest:sound', true) !== false, onChange: (v) => local.set('contest:sound', v) });
  return card({ title: 'Przypomnienia', icon: 'clock', body: h('div', { class: 'stack gap-s' }, notifyBox, soundBox,
    h('div', { class: 'row gap-s' }, button('Test przypomnienia', { size: 'sm', icon: 'play', onClick: () => notify('Tak będzie wyglądać przypomnienie: zrób backup i wyślij to, co masz.') }))) });
}

function refreshHistory() {
  const host = view?.historyHost;
  if (!host || !S) return;
  clear(host);
  const hist = S.history || [];
  if (!hist.length) {
    host.append(card({ title: 'Historia sesji', icon: 'history', body: emptyState({ icon: 'trophy', title: 'Jeszcze żadnej sesji', text: 'Po zakończeniu pierwszych zawodów zobaczysz tu podsumowania i czasy na zadania.' }) }));
    return;
  }
  const rows = hist.map((x) => ({ ...x }));
  const tbl = table([
    { key: 'started_at', label: 'Data', render: (r) => fmtDate(r.started_at) },
    { key: 'title', label: 'Sesja', render: (r) => h('div', null, h('div', { class: 'bold' }, r.title), r.aborted ? badge('przerwana', 'warn') : null) },
    { key: 'mode', label: 'Tryb', render: (r) => badge(r.mode === 'contest' ? 'zawody' : 'trening', r.mode === 'contest' ? 'accent' : 'neutral') },
    { key: 'elapsed', label: 'Czas', align: 'right', render: (r) => fmtShort(r.elapsed) + ` / ${fmtShort(r.duration_min * 60)}` },
    { key: 'solved_full', label: 'Pełne', align: 'right', render: (r) => `${r.solved_full} / ${r.tasks_count}` },
    { key: 'points_total', label: 'Punkty', align: 'right', render: (r) => h('b', null, String(r.points_total ?? 0)) },
  ], rows, { onRowClick: (r) => openSummary(r.id) });
  host.append(card({ title: 'Historia sesji', icon: 'history', subtitle: `${S.history_count} ${plural(S.history_count, 'sesja', 'sesje', 'sesji')}`, body: tbl, padded: false }));
}

async function openSummary(id, summary) {
  let s = summary;
  if (!s) {
    try { s = (await api.get('/api/contest/history', { id })).session; } catch { return; }
  }
  const m = modal({ title: s.title, icon: 'trophy', width: 860, body: summaryBody(s),
    actions: [
      button('Usuń z historii', { variant: 'danger', icon: 'trash-2', onClick: async () => {
        if (!await confirmDialog('Usunąć tę sesję z historii?', { danger: true, ok: 'Usuń' })) return;
        try { const r = await api.post('/api/contest/history/delete', { id: s.id }); S.history = r.history; S.history_count = r.history_count; refreshHistory(); m.close(); } catch { /* */ }
      } }),
      button('Zamknij', { variant: 'primary', onClick: () => m.close() })] });
}

function summaryBody(s) {
  const pts = s.points_total ?? 0;
  const BAR_TONE = { reading: 'info', brute: 'warn', full: 'success' };
  const bars = s.tasks.map((t) => ({ label: `${t.short} · ${t.status_label}`, value: Math.round(t.time_s / 60), tone: BAR_TONE[t.status] }));
  return h('div', { class: 'stack' },
    h('div', { class: 'grid-4' },
      stat({ label: 'Czas aktywny', value: fmtShort(s.elapsed), sub: `z ${fmtShort(s.duration_min * 60)}`, icon: 'timer' }),
      stat({ label: 'Pełne rozwiązania', value: `${s.solved_full} / ${s.tasks_count}`, tone: s.solved_full ? 'success' : null, icon: 'circle-check' }),
      stat({ label: 'Punkty (szacunkowo)', value: String(pts), sub: s.solved_partial ? `${s.solved_partial} częściowe/brute` : null, icon: 'trophy' }),
      stat({ label: 'Pauzy', value: String(s.pauses || 0), sub: s.paused_total ? fmtShort(s.paused_total) : 'bez pauz', icon: 'pause' })),
    h('div', { class: 'grid-2' },
      h('div', { class: 'stack gap-s' }, h('div', { class: 'section-title' }, 'Czas na zadanie (minuty)'),
        barChart(bars, { format: (v) => `${v} min` }),
        s.unassigned_s > 60 ? h('div', { class: 'muted small' }, `Poza zadaniami (bez wybranego zadania): ${fmtShort(s.unassigned_s)}`) : null),
      h('div', { class: 'stack gap-s' }, h('div', { class: 'section-title' }, 'Wskazówki na następny raz'),
        (s.advice || []).map((a) => callout(a, 'tip')))),
    table([
      { key: 'short', label: 'Zadanie', render: (t) => h('div', null, h('b', null, t.short), ' ', h('span', { class: 'muted' }, t.name)) },
      { key: 'status', label: 'Status', render: (t) => badge(t.status_label, STATUS_TONE[t.status]) },
      { key: 'time_s', label: 'Czas', align: 'right', render: (t) => fmtShort(t.time_s) },
      { key: 'solved_at', label: 'Pełne po', align: 'right', render: (t) => (t.solved_at != null ? fmtClock(t.solved_at) : '—') },
      { key: 'points', label: 'Pkt', align: 'right', render: (t) => (t.points ?? '—') },
    ], s.tasks, { compact: true }),
    s.tasks.some((t) => t.notes && t.notes.trim()) ? h('details', null, h('summary', { class: 'section-title', style: 'cursor:pointer' }, 'Notatki do zadań'),
      h('div', { class: 'stack mt-s' }, s.tasks.filter((t) => t.notes && t.notes.trim()).map((t) => h('div', { class: 'panel' }, h('div', { class: 'bold' }, `${t.short} — ${t.name}`), h('div', { class: 'md mt-s' }, markdown(t.notes)))))) : null,
    h('details', null, h('summary', { class: 'section-title', style: 'cursor:pointer' }, `Oś zdarzeń (${(s.events || []).length})`), timelineList(s.events || [], true)));
}

// ------------------------------------------------------------------------------------------------ trwająca sesja
function renderActive() {
  const { body, actions } = view;
  const s = S.session;
  view.activeRendered = true;

  // --- przyciski w nagłówku
  view.pauseBtn = button('Pauza', { icon: 'pause', onClick: togglePause, title: 'Zatrzymaj zegar (trening)' });
  view.resumeBtn = button('Wznów', { icon: 'play', variant: 'primary', onClick: togglePause });
  actions.append(
    button('Zadanie', { icon: 'plus', variant: 'ghost', onClick: async () => {
      const name = await promptDialog('Nazwa nowego zadania:', { title: 'Dodaj zadanie', placeholder: 'np. Kurierzy' });
      if (name !== null) update({ action: 'add_task', name: name.trim() });
    } }),
    view.pauseBtn, view.resumeBtn,
    button('Zakończ', { icon: 'circle-stop', variant: 'danger', onClick: stopSession }));

  // --- zegar
  view.clockTime = h('div', { class: 'ct-time' }, '0:00:00');
  view.clockLabel = h('div', { class: 'ct-label' }, 'Pozostało');
  view.barFill = h('div', { class: 'ct-bar-fill' });
  const marks = (s.reminders || []).filter((r) => r.kind !== 'end').map((r) => h('div', { class: ['ct-mark', r.kind].join(' '), style: { left: `${(100 * r.at) / s.duration_s}%` }, title: `${fmtClock(r.at)} — ${r.text}` }));
  view.bar = h('div', { class: 'ct-bar' }, view.barFill, marks);
  view.meta = h('div', { class: 'ct-meta' });
  view.next = h('div', { class: 'ct-next' });
  view.clock = h('div', { class: 'ct-clock' },
    h('div', { class: 'ct-clock-head' }, h('div', { class: 'row gap-s' }, badge(s.mode === 'contest' ? 'Zawody' : 'Trening', s.mode === 'contest' ? 'accent' : 'neutral', s.mode === 'contest' ? 'trophy' : 'target'),
      h('span', { class: 'ct-title' }, s.title)), view.pausedBadge = badge('PAUZA', 'warn', 'pause')),
    view.clockLabel, view.clockTime, view.bar, view.meta, view.next);

  // --- zadania
  view.tasksGrid = h('div', { class: 'ct-tasks' });
  for (const t of s.tasks) view.tasksGrid.append(taskCard(t));

  // --- oś zdarzeń
  const noteIn = input({ placeholder: 'Notatka na osi czasu (Enter)…', onEnter: async (v) => { if (v.trim()) { await update({ action: 'note', text: v.trim() }); noteIn.value = ''; } } });
  view.timeline = h('div', { class: 'ct-timeline' });
  view.remList = h('div', { class: 'ct-reminders' });
  const side = h('div', { class: 'stack' },
    card({ title: 'Przypomnienia', icon: 'clock', body: view.remList }),
    card({ title: 'Oś zdarzeń', icon: 'history', body: h('div', { class: 'stack gap-s' }, noteIn, view.timeline) }));

  body.append(h('div', { class: 'ct-active' }, h('div', { class: 'stack' }, view.clock, view.tasksGrid), side));
  refreshActive();
}

function taskCard(t0) {
  const tid = t0.id;
  const cur = () => S?.session?.tasks.find((x) => x.id === tid) || t0;
  const t = t0;
  const seg = segmented((S.statuses || []).map((st) => ({ value: st.id, label: st.label, icon: st.icon })), {
    value: t.status, onChange: (v) => update({ task_id: tid, status: v }),
  });
  seg.classList.add('ct-status');
  const timeEl = h('span', { class: 'ct-task-time', title: 'Czas spędzony nad zadaniem' }, icon('clock', 13), h('span', null, '0:00:00'));
  const focusBtn = button('Pracuję nad tym', { icon: 'target', size: 'sm', variant: 'ghost', title: 'Licz czas na tym zadaniu',
    onClick: () => update({ task_id: tid, focus: S.session.focus !== tid }) });
  const ptsIn = input({ value: t.points ?? '', type: 'number', size: 'sm', placeholder: '—', attrs: { min: 0, max: 100, title: 'Szacowane punkty (0–100)' },
    onChange: (v) => update({ task_id: tid, points: v === '' ? null : Number(v) }) });
  ptsIn.classList.add('ct-points');

  // notatki (markdown) z podglądem
  const notes = textarea({ value: t.notes || '', rows: 5, mono: false, placeholder: 'Pomysły, złożoność, przypadki brzegowe… (markdown)' });
  const preview = h('div', { class: 'md ct-notes-preview', hidden: true });
  let lastSaved = t.notes || '';
  const saveNotes = debounce((v) => { if (v === lastSaved) return; lastSaved = v; update({ task_id: tid, notes: v }); }, 700);
  notes.addEventListener('input', () => saveNotes(notes.value));
  notes.addEventListener('blur', () => saveNotes.flush(notes.value));
  const notesMode = segmented([{ value: 'edit', label: 'Edytuj', icon: 'pencil' }, { value: 'view', label: 'Podgląd', icon: 'eye' }], {
    value: 'edit', onChange: (v) => {
      const show = v === 'view';
      notes.hidden = show; preview.hidden = !show;
      if (show) clear(preview).append(notes.value.trim() ? markdown(notes.value) : h('span', { class: 'muted' }, 'Brak notatek.'));
    },
  });
  notesMode.classList.add('sm');

  // checklista
  const checks = new Map();
  const checkCount = h('span', { class: 'ct-check-count' });
  const checkList = h('div', { class: 'ct-checklist' }, (S.checklist || []).map((c) => {
    const cb = checkbox(c.label, { checked: !!(t.checklist || {})[c.id], onChange: (v) => {
      const ct = cur(); ct.checklist = { ...(ct.checklist || {}), [c.id]: v }; refreshTask(ct);
      update({ task_id: tid, checklist: { [c.id]: v } });
    } });
    checks.set(c.id, cb);
    return h('div', { class: 'ct-check-item' }, cb, c.kb ? h('a', { class: 'ct-kb', href: '#/docs', title: 'Dlaczego to ważne? (baza wiedzy)', onclick: (e) => { e.preventDefault(); openKb(c.kb); } }, icon('book-open', 13)) : null);
  }));
  const details = h('details', { class: 'ct-check' }, h('summary', null, icon('list-checks', 15), h('span', { class: 'grow' }, 'Przed wysłaniem'), checkCount), checkList);

  const nameEl = h('div', { class: 'ct-task-name', title: 'Kliknij dwukrotnie, aby zmienić nazwę', ondblclick: async () => {
    const n = await promptDialog('Nazwa zadania:', { title: 'Zmień nazwę', value: cur().name });
    if (n && n.trim()) update({ task_id: tid, name: n.trim() });
  } }, t.name);
  const letter = h('div', { class: 'ct-letter' }, t.short);
  const el = h('div', { class: 'ct-task', dataset: { status: t.status } },
    h('div', { class: 'ct-task-head' }, letter, h('div', { class: 'grow' }, nameEl, h('div', { class: 'row gap-s small muted' }, timeEl)), focusBtn),
    seg,
    h('div', { class: 'row between' }, notesMode, h('label', { class: 'row gap-s small muted' }, 'Punkty', ptsIn)),
    notes, preview, details);
  taskViews.set(tid, { el, seg, timeEl, focusBtn, ptsIn, notes, checks, checkCount, nameEl, letter, details });
  refreshTask(t);
  return el;
}

function refreshTask(t) {
  const v = taskViews.get(t.id);
  if (!v) return;
  v.el.dataset.status = t.status;
  v.seg.setValue(t.status);
  v.nameEl.textContent = t.name;
  v.letter.textContent = t.short;
  const focused = S.session.focus === t.id;
  v.el.classList.toggle('focused', focused);
  v.focusBtn.classList.toggle('secondary', focused);
  v.focusBtn.classList.toggle('ghost', !focused);
  const lab = v.focusBtn.querySelector('span:not(.spinner)');
  if (lab) lab.textContent = focused ? 'W toku' : 'Pracuję nad tym';
  if (document.activeElement !== v.ptsIn) v.ptsIn.value = t.points ?? '';
  if (document.activeElement !== v.notes && v.notes.value !== (t.notes || '')) v.notes.value = t.notes || '';
  let n = 0;
  for (const [id, cb] of v.checks) { const on = !!(t.checklist || {})[id]; cb.checked = on; if (on) n++; }
  const total = v.checks.size;
  v.checkCount.textContent = `${n}/${total}`;
  v.checkCount.className = ['ct-check-count', n === total ? 'done' : ''].join(' ');
  if ((t.status === 'partial' || t.status === 'full' || t.status === 'brute') && n < total && !v.details.dataset.auto) { v.details.open = true; v.details.dataset.auto = '1'; }
}

function refreshActive() {
  if (!view?.activeRendered || !S?.session) return;
  const s = S.session;
  for (const t of s.tasks) refreshTask(t);
  view.pausedBadge.hidden = !s.paused;
  view.pauseBtn.hidden = !!s.paused;
  view.resumeBtn.hidden = !s.paused;
  // przypomnienia
  clear(view.remList);
  for (const r of s.reminders || []) {
    view.remList.append(h('div', { class: ['ct-rem', r.fired ? 'fired' : '', r.kind].join(' ') },
      h('span', { class: 'ct-rem-at mono' }, fmtClock(r.at)), h('span', { class: 'grow' }, r.text), r.fired ? icon('check', 14) : null));
  }
  clear(view.timeline).append(timelineList(s.events || []));
  refreshClock();
}

function timelineList(events, full = false) {
  const evs = [...events].reverse().slice(0, full ? 400 : 60);
  if (!evs.length) return h('div', { class: 'muted small' }, 'Brak zdarzeń.');
  return h('div', { class: 'ct-events' }, evs.map((e) => h('div', { class: ['ct-ev', e.kind].join(' ') },
    h('span', { class: 'ct-ev-at mono' }, fmtClock(e.e || 0)), h('span', { class: 'ct-ev-icon' }, icon(EVENT_ICON[e.kind] || 'info', 13)),
    h('span', { class: 'grow' }, e.text))));
}

function refreshClock() {
  if (!view?.activeRendered || !S?.session) return;
  const s = S.session;
  const el = elapsedNow();
  const rem = s.duration_s - el;
  const tone = clockTone(rem, s.paused);
  view.clock.dataset.tone = tone;
  view.clockTime.textContent = fmtClock(Math.max(0, rem));
  view.clockLabel.textContent = s.paused ? 'Pauza — zegar zatrzymany' : (rem <= 0 ? 'Czas minął!' : 'Pozostało');
  view.barFill.style.width = `${Math.min(100, (100 * el) / s.duration_s).toFixed(2)}%`;
  const endAt = new Date((nowS() + Math.max(0, rem) - offset) * 1000);
  clear(view.meta).append(
    h('span', null, 'Upłynęło ', h('b', null, fmtClock(el))),
    h('span', null, s.paused ? 'Koniec przesuwa się podczas pauzy' : ['Koniec o ', h('b', null, endAt.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' }))]),
    s.paused_total > 0 || s.paused ? h('span', null, 'Pauzy ', h('b', null, fmtShort(s.paused_total + (s.paused ? Math.max(0, nowS() - S.now) : 0)))) : null);
  const next = (s.reminders || []).find((r) => !r.fired && r.at > el);
  clear(view.next);
  if (rem <= 0) view.next.append(icon('circle-stop', 15), h('span', null, 'Koniec czasu — kliknij „Zakończ”, żeby zobaczyć podsumowanie.'));
  else if (next) view.next.append(icon('clock', 15), h('span', null, `Za ${fmtClock(next.at - el)}: `), h('span', { class: 'muted' }, next.text));
  // czas na zadaniach (fokus rośnie na żywo)
  const live = s.paused ? 0 : Math.max(0, nowS() - S.now);
  for (const t of s.tasks) {
    const v = taskViews.get(t.id);
    if (!v) continue;
    const sec = (s.task_times?.[t.id] || 0) + (s.focus === t.id ? live : 0);
    v.timeEl.lastChild.textContent = fmtClock(sec);
  }
}

async function togglePause() {
  const s = S.session;
  if (!s.paused && s.mode === 'contest') {
    const ok = await confirmDialog('Na prawdziwych zawodach nie ma pauzy. Zatrzymać zegar i zamienić sesję w trening?', { title: 'Pauza', ok: 'Pauza' });
    if (!ok) return;
  }
  await update({ action: s.paused ? 'resume' : 'pause' });
}

async function stopSession() {
  const s = S.session;
  const rem = s.duration_s - elapsedNow();
  const msg = rem > 60 ? `Zostało jeszcze ${fmtShort(rem)}. Zakończyć sesję i zapisać podsumowanie?` : 'Zakończyć sesję i zapisać podsumowanie?';
  const m = modal({ title: 'Zakończ sesję', icon: 'circle-stop', width: 480, body: h('div', { class: 'stack gap-s' }, h('div', null, msg),
    h('div', { class: 'muted small' }, 'Sesję możesz też odrzucić bez zapisywania w historii.')),
  actions: [
    button('Odrzuć', { variant: 'ghost', icon: 'trash-2', onClick: async () => {
      if (!await confirmDialog('Odrzucić sesję bez zapisu? Tego nie da się cofnąć.', { danger: true, ok: 'Odrzuć' })) return;
      m.close();
      try { applyState(await api.post('/api/contest/stop', { discard: true })); } catch { /* */ }
    } }),
    button('Anuluj', { variant: 'ghost', onClick: () => m.close() }),
    button('Zakończ i podsumuj', { variant: 'primary', icon: 'circle-check', onClick: async () => {
      m.close();
      try {
        const r = await api.post('/api/contest/stop', {});
        applyState(r);
        if (r.summary) openSummary(r.summary.id, r.summary);
      } catch { /* */ }
    } })] });
}

async function openKb(id) {
  api.post('/api/journal/log', { kind: 'kb_view', data: { id, from: 'contest' } }, { quiet: true }).catch(() => {});
  if (window.OIF?.views?.has('docs')) { view.ctx.navigate('docs', { id }); return; }
  try {
    const a = (await api.get('/api/kb/article', { id })).article;
    const blocks = (a.body || []).filter((b) => b.md).slice(0, 4).map((b) => b.md).join('\n\n');
    modal({ title: a.title, icon: 'book-open', width: 720, body: h('div', { class: 'md' }, markdown(`${a.summary || ''}\n\n${blocks}`)) });
  } catch { /* */ }
}

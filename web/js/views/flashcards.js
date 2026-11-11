// Fiszki z powtórkami SM-2: talie ręczne i generowane z bazy wiedzy, karta z animacją odwracania, skróty klawiszowe.
import { h, icon, button, viewHeader, card, stat, badge, emptyState, clear, plural, markdown, kbd, select, toast, modal } from '../ui.js';
import { api, settings, updateSettings } from '../api.js';
import { barChart } from '../charts.js';

const GRADE_META = [
  { g: 1, label: 'Nie wiem', tone: 'danger', icon: 'x' },
  { g: 2, label: 'Trudne', tone: 'warn', icon: 'triangle-alert' },
  { g: 3, label: 'Dobrze', tone: 'success', icon: 'check' },
  { g: 4, label: 'Łatwe', tone: 'info', icon: 'zap' },
];
const KIND_LABEL = { complexity: 'złożoność', function: 'funkcja', qa: 'pytanie' };

let V = null;          // stan widoku
let pending = null;    // parametry z navigate, zanim widok się załaduje

export default {
  id: 'flashcards', title: 'Fiszki', icon: 'brain-circuit', group: 'Trening', order: 3,
  description: 'Powtórki z algorytmem SM-2: złożoności STL, „jaki algorytm do…”, pułapki, wzory i karty z bazy wiedzy.',
  keywords: ['fiszki', 'powtórki', 'flashcards', 'sm-2', 'anki', 'nauka', 'pamięć', 'quiz'],
  async render(root, ctx) {
    const body = h('div', { class: 'fc-body' });
    const actions = h('div', { class: 'row gap-s' });
    root.append(viewHeader(this, actions), body);
    V = { root, ctx, body, actions, decks: null, study: null };
    await loadHome();
    if (pending) { const p = pending; pending = null; handleParams(p); }
  },
  onShow(params) {
    if (!V || !V.decks) { pending = params; return; }
    if (params && (params.deck || params.topic)) handleParams(params);
    else if (!V.study) loadHome();
  },
};

function handleParams(p) {
  if (p?.topic) startStudy({ topic: p.topic, title: p.title ? `Temat: ${p.title}` : 'Fiszki z tematu' });
  else if (p?.deck) {
    const d = V.decks?.decks.find((x) => x.id === p.deck);
    startStudy({ deck: p.deck, title: d ? d.title : 'Talia' });
  }
}

// ================================================================================================ ekran talii
async function loadHome() {
  V.study = null;
  const { body, actions } = V;
  clear(actions);
  if (!V.decks) clear(body).append(h('div', { class: 'grid-4' }, [0, 1, 2, 3].map(() => h('div', { class: 'stat skeleton', style: 'height:86px' }))));
  let d;
  try { d = await api.get('/api/flashcards/decks'); } catch (e) {
    clear(body).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać fiszek', text: e.message }));
    return;
  }
  V.decks = d;
  if (V.study) return;   // użytkownik zdążył zacząć naukę
  clear(body);
  const t = d.totals, st = d.stats;
  actions.append(button(t.due_today ? `Powtórka dnia (${t.due_today})` : 'Powtórka dnia', { icon: 'rotate-ccw', variant: 'primary', onClick: () => startStudy({ title: 'Powtórka dnia' }) }));

  const today = st.today || { reviews: 0, correct: 0 };
  body.append(h('div', { class: 'grid-4' },
    stat({ label: 'Do powtórki dziś', value: t.due_today, sub: `${t.due} ${plural(t.due, 'powtórka', 'powtórki', 'powtórek')} + ${t.new_today} ${plural(t.new_today, 'nowa', 'nowe', 'nowych')}`, icon: 'rotate-ccw', tone: t.due_today ? 'accent' : 'success' }),
    stat({ label: 'Seria dni', value: st.streak.current, sub: st.streak.active_today ? 'dziś już powtarzałeś — brawo!' : `rekord: ${st.streak.longest}`, icon: 'zap', tone: st.streak.current ? 'warn' : null }),
    stat({ label: 'Dziś powtórzone', value: today.reviews || 0, sub: today.reviews ? `${Math.round((100 * (today.correct || 0)) / today.reviews)}% bez „Nie wiem”` : 'jeszcze nic', icon: 'check' }),
    stat({ label: 'Opanowane karty', value: t.learned, sub: st.retention_30d !== null ? `skuteczność 30 dni: ${Math.round(st.retention_30d * 100)}%` : `z ${t.cards} wszystkich`, icon: 'brain-circuit' })));

  // --- powtórka dnia + historia
  const hist = st.history || [];
  body.append(h('div', { class: 'fc-today mt-m' },
    h('div', { class: 'fc-today-main' },
      h('div', { class: 'fc-today-icon' }, icon('rotate-ccw', 26)),
      h('div', { class: 'grow' },
        h('div', { class: 'fc-today-title' }, t.due_today ? `${t.due_today} ${plural(t.due_today, 'karta czeka', 'karty czekają', 'kart czeka')} na powtórkę` : 'Na dziś wszystko powtórzone!'),
        h('div', { class: 'muted small' }, t.due_today ? 'Kilka minut dziennie wystarczy — algorytm SM-2 pokaże kartę dokładnie wtedy, gdy zaczynasz ją zapominać.'
          : 'Możesz dołożyć nowe karty albo przejrzeć talię z bazy wiedzy.')),
      t.due_today ? button('Zacznij', { icon: 'play', variant: 'primary', size: 'lg', onClick: () => startStudy({ title: 'Powtórka dnia' }) })
        : button('Dodaj 10 nowych', { icon: 'plus', size: 'lg', onClick: () => startStudy({ title: 'Nowe karty', extra_new: 10 }) })),
    h('div', { class: 'fc-today-chart' }, h('div', { class: 'tiny muted' }, 'Powtórki — ostatnie 30 dni'),
      barChart(hist.map((x) => ({ label: x.date.slice(8), value: x.reviews })), { horizontal: false, height: 74 }))));

  // --- talie ręczne
  const hand = d.decks.filter((x) => !x.auto);
  const auto = d.decks.filter((x) => x.auto);
  body.append(h('div', { class: 'fc-section' }, h('h2', null, 'Twoje talie'), h('span', { class: 'muted small' }, `${t.hand_cards} ręcznie przygotowanych kart`)),
    h('div', { class: 'fc-decks' }, hand.map((x) => deckTile(x, false))));
  body.append(h('div', { class: 'fc-section' }, h('h2', null, 'Z bazy wiedzy'),
    h('span', { class: 'muted small' }, `${t.auto_cards} kart wygenerowanych z artykułów: złożoności, funkcje i pytania. Nowe karty z tych talii pojawiają się, gdy wejdziesz do talii.`)),
  h('div', { class: 'fc-decks small' }, auto.map((x) => deckTile(x, true))));

  // --- ustawienia
  const s = await settings();
  const npd = (s.flashcards && s.flashcards.new_per_day) || d.new_per_day || 15;
  const sel = select([5, 10, 15, 20, 30, 50].map((n) => ({ value: n, label: `${n} dziennie` })), { value: npd, size: 'sm', onChange: async (v) => {
    await updateSettings({ flashcards: { new_per_day: Number(v) } });
    toast('Zapisano limit nowych kart.', 'success');
    loadHome();
  } });
  sel.style.width = 'auto';
  body.append(h('div', { class: 'fc-foot' }, icon('settings', 14), h('span', null, 'Nowe karty:'), sel,
    h('span', { class: 'muted' }, '· Skróty w nauce:'), kbd('Spacja'), h('span', { class: 'muted' }, 'odkryj'), kbd('1'), kbd('2'), kbd('3'), kbd('4'), h('span', { class: 'muted' }, 'ocena'),
    (d.errors || []).length ? badge(`${d.errors.length} błędów w pliku fiszek`, 'warn') : null));
}

function deckTile(x, compact) {
  const learnedPct = x.total ? Math.round((100 * x.learned) / x.total) : 0;
  return h('div', { class: ['fc-deck', compact ? 'compact' : ''].join(' '), onclick: () => startStudy({ deck: x.id, title: x.title }) },
    h('div', { class: 'fc-deck-head' },
      h('div', { class: 'fc-deck-icon' }, icon(x.icon || 'layers', compact ? 16 : 19)),
      h('div', { class: 'grow' }, h('div', { class: 'fc-deck-title' }, x.title), compact ? null : h('div', { class: 'fc-deck-desc' }, x.description)),
      x.due ? badge(String(x.due), 'accent', 'rotate-ccw') : null),
    h('div', { class: 'fc-deck-bar', title: `Opanowane: ${x.learned} z ${x.total}` }, h('div', { style: { width: `${learnedPct}%` } })),
    h('div', { class: 'fc-deck-meta' },
      h('span', null, `${x.total} ${plural(x.total, 'karta', 'karty', 'kart')}`),
      h('span', null, `${x.learned} ${plural(x.learned, 'opanowana', 'opanowane', 'opanowanych')}`),
      x.new && !compact ? h('span', null, `${x.new} ${plural(x.new, 'nowa', 'nowe', 'nowych')}`) : null));
}

// ================================================================================================ nauka
async function startStudy(opts) {
  const { body, actions } = V;
  const params = { limit: 40 };
  if (opts.deck) params.deck = opts.deck;
  if (opts.topic) params.topic = opts.topic;
  if (opts.extra_new) params.extra_new = opts.extra_new;
  V.study = { opts, queue: [], i: 0, flipped: false, done: 0, good: 0, bad: 0, busy: false };
  clear(actions).append(button('Talie', { icon: 'chevron-left', variant: 'ghost', onClick: () => loadHome() }));
  clear(body).append(h('div', { class: 'skeleton', style: 'height:340px;border-radius:22px' }));
  let r;
  try { r = await api.get('/api/flashcards/due', params); } catch { loadHome(); return; }
  if (!V.study || V.study.opts !== opts) return;
  V.study.queue = r.cards;
  V.study.counts = r.counts;
  renderStudy();
}

function renderStudy() {
  const S = V.study;
  const { body } = V;
  clear(body);
  if (!S.queue.length) {
    const c = S.counts || {};
    body.append(h('div', { class: 'fc-done' }, emptyState({
      icon: 'circle-check', title: 'Wszystko powtórzone!',
      text: c.new_available ? `W tej talii czeka jeszcze ${c.new_available} ${plural(c.new_available, 'nowa karta', 'nowe karty', 'nowych kart')} — dzienny limit nowych już wykorzystany.`
        : 'Brak kart do powtórki. Wróć jutro — algorytm sam zaplanuje kolejne powtórki.',
      action: h('div', { class: 'row' },
        c.new_available ? button('Dodaj 10 nowych', { icon: 'plus', variant: 'primary', onClick: () => startStudy({ ...S.opts, extra_new: (S.opts.extra_new || 0) + 10 }) }) : null,
        button('Wróć do talii', { icon: 'chevron-left', onClick: () => loadHome() })) })));
    return;
  }
  S.els = {};
  const total = S.queue.length;
  S.els.counter = h('span', { class: 'fc-counter' });
  S.els.bar = h('div', { class: 'fc-progress-fill' });
  S.els.score = h('span', { class: 'fc-score' });
  const top = h('div', { class: 'fc-study-top' },
    h('div', { class: 'grow' }, h('div', { class: 'fc-study-title' }, S.opts.title || 'Nauka'), h('div', { class: 'fc-progress' }, S.els.bar)),
    S.els.score, S.els.counter);
  S.els.front = h('div', { class: 'fc-face front' });
  S.els.back = h('div', { class: 'fc-face back' });
  S.els.card = h('div', { class: 'fc-card', onclick: (e) => { if (!S.flipped && !e.target.closest('a,button')) flip(); } }, S.els.front, S.els.back);
  S.els.scene = h('div', { class: 'fc-scene' }, S.els.card);
  S.els.controls = h('div', { class: 'fc-controls' });
  body.append(h('div', { class: 'fc-study' }, top, S.els.scene, S.els.controls,
    h('div', { class: 'fc-hints' }, kbd('Spacja'), ' odkryj · ', kbd('1'), '–', kbd('4'), ' ocena · ', kbd('S'), ' pomiń')));
  S.total0 = total;
  showCard(false);
}

function cardFaces(c) {
  const S = V.study;
  const tags = h('div', { class: 'fc-tags' },
    badge(deckTitle(c.deck), c.source === 'kb' ? 'neutral' : 'accent', c.source === 'kb' ? 'library' : 'layers'),
    c.kind ? badge(KIND_LABEL[c.kind] || c.kind, 'neutral') : null,
    c.is_new ? badge('nowa', 'info', 'sparkles') : (c.state?.lapses ? badge(`pomyłki: ${c.state.lapses}`, 'warn') : null));
  clear(S.els.front).append(tags, h('div', { class: 'fc-q md' }, markdown(c.q)), h('div', { class: 'fc-tap muted small' }, icon('eye', 14), ' Kliknij albo naciśnij spację, żeby zobaczyć odpowiedź'));
  clear(S.els.back).append(
    h('div', { class: 'fc-back-q' }, markdown(c.q)),
    h('div', { class: 'fc-a md' }, markdown(c.a)),
    c.kb ? h('div', { class: 'fc-kb' }, button(c.kb_title ? `Więcej: ${c.kb_title}` : 'Artykuł w bazie wiedzy', { icon: 'book-open', size: 'sm', variant: 'ghost',
      onClick: () => openKb(c.kb) })) : null);
}

function deckTitle(id) { return V.decks?.decks.find((d) => d.id === id)?.title || id; }

function showCard(animate = true) {
  const S = V.study;
  const c = S.queue[S.i];
  S.flipped = false;
  S.els.card.classList.add('no-anim');
  S.els.card.classList.remove('flipped');
  void S.els.card.offsetWidth;
  cardFaces(c);
  S.els.card.classList.remove('no-anim');
  if (animate) { S.els.scene.classList.remove('enter'); void S.els.scene.offsetWidth; S.els.scene.classList.add('enter'); }
  S.els.counter.textContent = `${Math.min(S.i + 1, S.queue.length)} / ${S.queue.length}`;
  S.els.bar.style.width = `${(100 * S.i) / Math.max(1, S.queue.length)}%`;
  S.els.score.replaceChildren(icon('check', 14), ` ${S.good}  `, icon('x', 14), ` ${S.bad}`);
  clear(S.els.controls).append(button('Pokaż odpowiedź', { icon: 'eye', variant: 'primary', size: 'lg', onClick: flip, class: 'fc-show' }));
}

function flip() {
  const S = V.study;
  if (!S || S.flipped) return;
  S.flipped = true;
  S.els.card.classList.add('flipped');
  const c = S.queue[S.i];
  clear(S.els.controls).append(h('div', { class: 'fc-grades' }, GRADE_META.map((m) => h('button', {
    type: 'button', class: `fc-grade ${m.tone}`, onclick: () => grade(m.g), title: `${m.label} (klawisz ${m.g})`,
  }, h('span', { class: 'fc-grade-label' }, icon(m.icon, 15), m.label), h('span', { class: 'fc-grade-next' }, c.preview?.[String(m.g)]?.next || ''), h('kbd', null, String(m.g))))));
}

async function grade(g) {
  const S = V.study;
  if (!S || !S.flipped || S.busy) return;
  S.busy = true;
  const c = S.queue[S.i];
  let r;
  try { r = await api.post('/api/flashcards/review', { card_id: c.id, grade: g }); } catch { S.busy = false; return; }
  S.busy = false;
  S.done += 1;
  if (g === 1) {
    S.bad += 1;
    S.queue.push({ ...c, is_new: false, state: r.state, preview: r.preview || c.preview });   // wróci jeszcze w tej sesji
  } else S.good += 1;
  next();
}

function skip() {
  const S = V.study;
  if (!S || S.busy) return;
  const c = S.queue[S.i];
  if (S.queue.length - S.i > 1) { S.queue.push(c); next(); }
}

function next() {
  const S = V.study;
  S.i += 1;
  if (S.i >= S.queue.length) { finish(); return; }
  showCard(true);
}

async function finish() {
  const S = V.study;
  const { body } = V;
  const total = S.good + S.bad;
  let streak = null;
  try { const d = await api.get('/api/flashcards/decks', null, { quiet: true }); V.decks = d; streak = d.stats.streak; } catch { /* */ }
  clear(body).append(h('div', { class: 'fc-finish' },
    h('div', { class: 'fc-finish-icon' }, icon('trophy', 34)),
    h('h2', null, 'Sesja zakończona!'),
    h('p', { class: 'muted' }, `${total} ${plural(total, 'ocena', 'oceny', 'ocen')} · ${S.good} dobrze · ${S.bad} do powtórki`
      + (streak ? ` · seria: ${streak.current} ${plural(streak.current, 'dzień', 'dni', 'dni')}` : '')),
    h('div', { class: 'fc-finish-ring', style: { '--p': total ? S.good / total : 0 } }, h('span', null, total ? `${Math.round((100 * S.good) / total)}%` : '—')),
    h('div', { class: 'row', style: 'justify-content:center' },
      button('Jeszcze 10 nowych', { icon: 'plus', onClick: () => startStudy({ ...S.opts, extra_new: (S.opts.extra_new || 0) + 10 }) }),
      button('Wróć do talii', { icon: 'chevron-left', variant: 'primary', onClick: () => loadHome() }))));
}

function openKb(id) {
  api.post('/api/journal/log', { kind: 'kb_view', data: { id, from: 'flashcards' } }, { quiet: true }).catch(() => {});
  if (window.OIF?.views?.has('docs')) { V.ctx.navigate('docs', { id }); return; }
  api.get('/api/kb/article', { id }).then(({ article: a }) => {
    const blocks = (a.body || []).filter((b) => b.md).slice(0, 4).map((b) => b.md).join('\n\n');
    modal({ title: a.title, icon: 'book-open', width: 720, body: h('div', { class: 'md' }, markdown(`${a.summary || ''}\n\n${blocks}`)) });
  }).catch(() => {});
}

// ------------------------------------------------------------------------------------------------ skróty
document.addEventListener('keydown', (e) => {
  if (!V?.study?.els || !V.root.isConnected || V.root.hidden) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t && (t.closest?.('input, textarea, select, [contenteditable="true"], .CodeMirror') || document.querySelector('.backdrop'))) return;
  const S = V.study;
  if (e.key === ' ' || e.key === 'Enter') {
    if (!S.flipped) { e.preventDefault(); flip(); }
    return;
  }
  if (S.flipped && ['1', '2', '3', '4'].includes(e.key)) { e.preventDefault(); grade(Number(e.key)); return; }
  if (e.key === 's' || e.key === 'S') { e.preventDefault(); skip(); }
});

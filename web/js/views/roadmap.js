// Ścieżka nauki: etapy Podstawy → I → II → III etap OI, tematy jako węzły z pierścieniami postępu, odblokowywanie etapów.
import { h, icon, button, viewHeader, card, badge, emptyState, clear, plural, markdown, modal, timeAgo } from '../ui.js';
import { api, local } from '../api.js';

const NS = 'http://www.w3.org/2000/svg';
const STATUS = { done: ['Opanowany', 'success'], progress: ['W toku', 'accent'], started: ['Początek', 'info'], new: ['Nowy', 'neutral'] };

let V = null;
let pending = null;

function svg(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  for (const k of kids) if (k) e.append(k);
  return e;
}
function ring(p, size = 76, stroke = 6) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  return svg('svg', { class: 'rm-ring', width: size, height: size, viewBox: `0 0 ${size} ${size}`, 'aria-hidden': 'true' },
    svg('circle', { class: 'rm-ring-track', cx: size / 2, cy: size / 2, r, 'stroke-width': stroke, fill: 'none' }),
    svg('circle', { class: 'rm-ring-fill', cx: size / 2, cy: size / 2, r, 'stroke-width': stroke, fill: 'none', 'stroke-linecap': 'round',
      'stroke-dasharray': c.toFixed(2), 'stroke-dashoffset': (c * (1 - Math.max(0, Math.min(1, p)))).toFixed(2), transform: `rotate(-90 ${size / 2} ${size / 2})` }));
}
function lockIcon(size = 16) {
  const s = svg('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', class: 'icon' });
  s.innerHTML = '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>';
  return s;
}
const pct = (x) => `${Math.round((x || 0) * 100)}%`;

export default {
  id: 'roadmap', title: 'Ścieżka nauki', icon: 'git-branch', group: 'Trening', order: 4,
  description: 'Od podstaw C++ do finału OI: tematy z artykułami, Twoimi zadaniami z SIO2 i fiszkami — z postępem i odblokowywaniem etapów.',
  keywords: ['ścieżka', 'roadmap', 'plan nauki', 'etapy', 'olimpiada', 'tematy', 'postęp', 'program nauki'],
  async render(root, ctx) {
    const body = h('div', { class: 'rm-body' });
    const actions = h('div', { class: 'row gap-s' });
    root.append(viewHeader(this, actions), body);
    V = { root, ctx, body, actions, data: null, selected: local.get('roadmap:selected', null), nodes: new Map() };
    actions.append(button('Odśwież', { icon: 'refresh-cw', variant: 'ghost', onClick: () => load() }));
    await load();
    if (pending) { const p = pending; pending = null; if (p.topic) select(p.topic, true); }
  },
  onShow(params) {
    if (!V?.data) { pending = params; return; }
    if (params?.topic) select(params.topic, true);
    else if (Date.now() - (V.loadedAt || 0) > 60000) load();
  },
};

async function load() {
  const { body } = V;
  if (!V.data) clear(body).append(h('div', { class: 'skeleton', style: 'height:160px;border-radius:20px' }), h('div', { class: 'skeleton mt-m', style: 'height:480px;border-radius:20px' }));
  try { V.data = await api.get('/api/roadmap'); V.loadedAt = Date.now(); } catch (e) {
    clear(body).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać ścieżki', text: e.message }));
    return;
  }
  render();
}

function allTopics() { return V.data.stages.flatMap((s) => s.topics.map((t) => ({ ...t, stageObj: s }))); }
function findTopic(id) { return allTopics().find((t) => t.id === id) || null; }

function render() {
  const { body, data } = V;
  clear(body);
  V.nodes.clear();
  const ov = data.overall;
  const nextT = data.next ? findTopic(data.next.id) : null;
  if (!V.selected || !findTopic(V.selected)) V.selected = nextT?.id || data.stages[0]?.topics[0]?.id;

  // --- podsumowanie
  const big = h('div', { class: 'rm-hero-ring' }, ring(ov.progress, 112, 10), h('div', { class: 'rm-hero-pct' }, h('b', null, pct(ov.progress)), h('span', null, 'całej ścieżki')));
  body.append(h('div', { class: 'rm-hero' }, big,
    h('div', { class: 'grow' },
      h('div', { class: 'rm-hero-kicker' }, 'Jesteś na etapie'),
      h('div', { class: 'rm-hero-title' }, ov.current_stage_title || '—'),
      h('div', { class: 'rm-hero-stats' },
        h('span', null, icon('circle-check', 14), ` ${ov.done_topics}/${ov.total_topics} ${plural(ov.total_topics, 'temat', 'tematy', 'tematów')} opanowanych`),
        h('span', null, icon('trophy', 14), ` ${ov.solved_matched} ${plural(ov.solved_matched, 'zadanie', 'zadania', 'zadań')} w tematach`),
        h('span', null, icon('book-open', 14), ` ${ov.read_articles} ${plural(ov.read_articles, 'przeczytany artykuł', 'przeczytane artykuły', 'przeczytanych artykułów')}`)),
      nextT ? h('div', { class: 'row gap-s mt-m' },
        button(`Następny krok: ${nextT.title}`, { icon: 'target', variant: 'primary', onClick: () => select(nextT.id, true) }),
        nextT.kb[0] ? button('Czytaj', { icon: 'book-open', variant: 'ghost', onClick: () => openKb((nextT.kb.find((k) => !k.read) || nextT.kb[0]).id) }) : null) : null),
    h('div', { class: 'rm-legend' },
      legendItem('done', 'opanowany (≥ 85%)'), legendItem('progress', 'w toku'), legendItem('new', 'nowy'), h('div', { class: 'rm-legend-item' }, h('span', { class: 'rm-dot locked' }, lockIcon(10)), 'zablokowany'),
      h('div', { class: 'tiny muted mt-s' }, 'Postęp tematu: 60% zadania z SIO2, 25% przeczytane artykuły, 15% opanowane fiszki.'))));

  // --- ścieżka + panel szczegółów
  const path = h('div', { class: 'rm-path' });
  V.detail = h('div', { class: 'rm-detail-host' });
  body.append(h('div', { class: 'rm-layout' }, path, V.detail));
  data.stages.forEach((s, si) => path.append(stageBlock(s, si, nextT)));
  renderDetail();
  requestAnimationFrame(drawAllPaths);
  if (!V.ro) {
    V.ro = new ResizeObserver(() => { if (V.root.isConnected && !V.root.hidden) relayout(); });
  }
  V.ro.disconnect();
  V.ro.observe(path);
}

function legendItem(st, label) {
  return h('div', { class: 'rm-legend-item' }, h('span', { class: `rm-dot ${st}` }), label);
}

function stageBlock(s, si, nextT) {
  const head = h('div', { class: 'rm-stage-head' },
    h('div', { class: 'rm-stage-num' }, s.unlocked ? (s.done ? icon('check', 18) : String(si + 1)) : lockIcon(16)),
    h('div', { class: 'grow' },
      h('div', { class: 'row gap-s' }, h('h2', null, s.title), s.done ? badge('ukończony', 'success', 'circle-check') : (!s.unlocked ? badge('zablokowany', 'neutral') : null)),
      h('div', { class: 'muted small' }, s.subtitle)),
    h('div', { class: 'rm-stage-prog' }, h('div', { class: 'rm-stage-pct' }, pct(s.progress)),
      h('div', { class: 'rm-stage-bar' }, h('div', { style: { width: pct(s.progress) } })),
      h('div', { class: 'tiny muted' }, `${s.done_topics}/${s.topics.length} ${plural(s.topics.length, 'temat', 'tematy', 'tematów')} · ${s.solved} ${plural(s.solved, 'zadanie', 'zadania', 'zadań')}`)));
  const info = h('details', { class: 'rm-stage-info', open: s.id === V.data.overall.current_stage || null },
    h('summary', null, icon('info', 14), ' O etapie i kryteria'),
    h('div', { class: 'stack gap-s' }, h('div', { class: 'muted' }, s.description),
      h('ul', { class: 'rm-criteria' }, s.criteria.map((c) => h('li', null, icon('circle-check', 14), h('span', null, c))))));
  const grid = h('div', { class: 'rm-grid' });
  const svgLayer = svg('svg', { class: 'rm-lines', 'aria-hidden': 'true' });
  const wrap = h('div', { class: 'rm-grid-wrap' }, svgLayer, grid);
  s.topics.forEach((t, i) => {
    const n = node(t, s, nextT && nextT.id === t.id);
    n.dataset.index = i;
    grid.append(n);
  });
  const block = h('section', { class: ['rm-stage', s.unlocked ? '' : 'locked', s.done ? 'done' : ''].join(' '), dataset: { stage: s.id } },
    head, info, !s.unlocked ? h('div', { class: 'rm-locked-note' }, lockIcon(14),
      h('span', null, si > 0 ? `Odblokuje się, gdy ${V.data.stages[si - 1].title} osiągnie 45% (albo gdy rozwiążesz zadanie z tego etapu). Możesz już podglądać tematy.` : '')) : null,
    wrap);
  block._grid = grid; block._svg = svgLayer; block._wrap = wrap; block._topics = s.topics;
  return block;
}

function node(t, s, isNext) {
  const status = !s.unlocked ? 'locked' : t.status;
  const center = h('div', { class: 'rm-node-center' }, status === 'done' ? icon('check', 24) : (status === 'locked' ? lockIcon(20) : icon(t.icon || 'circle-check', 22)));
  const el = h('button', { type: 'button', class: ['rm-node', status, isNext ? 'next' : '', V.selected === t.id ? 'selected' : ''].join(' '),
    title: `${t.title} — ${pct(t.progress)}`, onclick: () => select(t.id) },
  h('div', { class: 'rm-node-ring' }, ring(t.progress), center, isNext ? h('span', { class: 'rm-next-tag' }, 'dalej') : null),
  h('div', { class: 'rm-node-title' }, t.title),
  h('div', { class: 'rm-node-sub' }, t.has_tasks ? `${Math.min(t.solved, t.target)}/${t.target} ${plural(t.target, 'zadanie', 'zadania', 'zadań')}` : `${pct(t.progress)} · teoria`));
  V.nodes.set(t.id, el);
  return el;
}

// ------------------------------------------------------------------------------------------------ układ „węża” i linie
function relayout() { drawAllPaths(); }

function drawAllPaths() {
  V.body.querySelectorAll('.rm-stage').forEach((block) => layoutStage(block));
}

function layoutStage(block) {
  const grid = block._grid, svgLayer = block._svg, wrap = block._wrap;
  const width = wrap.clientWidth;
  if (!width) return;
  const cols = Math.max(2, Math.min(5, Math.floor(width / 175)));
  grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  const nodes = [...grid.children];
  nodes.forEach((n, i) => {
    const row = Math.floor(i / cols), k = i % cols;
    n.style.gridRow = String(row + 1);
    n.style.gridColumn = String(row % 2 === 0 ? k + 1 : cols - k);
  });
  // środki pierścieni po ułożeniu
  const base = wrap.getBoundingClientRect();
  const pts = nodes.map((n) => {
    const r = n.querySelector('.rm-node-ring').getBoundingClientRect();
    return [r.left - base.left + r.width / 2, r.top - base.top + r.height / 2];
  });
  svgLayer.setAttribute('width', String(width));
  svgLayer.setAttribute('height', String(wrap.clientHeight));
  svgLayer.setAttribute('viewBox', `0 0 ${width} ${wrap.clientHeight}`);
  while (svgLayer.firstChild) svgLayer.removeChild(svgLayer.firstChild);
  const topics = block._topics;
  for (let i = 0; i + 1 < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
    let d;
    if (Math.abs(y1 - y2) < 4) d = `M${x1},${y1} L${x2},${y2}`;
    else {
      const side = x1 > width / 2 ? 1 : -1;
      const room = side > 0 ? width - x1 : x1;
      const bulge = side * Math.max(24, Math.min(64, room - 6));
      d = `M${x1},${y1} C${x1 + bulge},${y1} ${x2 + bulge},${y2} ${x2},${y2}`;
    }
    const a = topics[i], b = topics[i + 1];
    const cls = a.status === 'done' && b.progress > 0 ? 'done' : (a.progress > 0 && b.progress > 0 ? 'active' : '');
    svgLayer.append(svg('path', { d, class: `rm-line ${cls}` }));
  }
}

// ------------------------------------------------------------------------------------------------ szczegóły tematu
function select(id, scroll = false) {
  if (!findTopic(id)) return;
  V.selected = id;
  local.set('roadmap:selected', id);
  for (const [tid, el] of V.nodes) el.classList.toggle('selected', tid === id);
  renderDetail();
  if (scroll) V.nodes.get(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function renderDetail() {
  const t = findTopic(V.selected);
  clear(V.detail);
  if (!t) return;
  const s = t.stageObj;
  const [stLabel, stTone] = !s.unlocked ? ['Zablokowany', 'neutral'] : STATUS[t.status] || STATUS.new;
  const parts = [];
  if (t.has_tasks) parts.push(partBar('Zadania z SIO2', t.parts.tasks, `${t.solved}/${t.target}`, 'trophy'));
  parts.push(partBar('Artykuły', t.parts.read, `${t.read}/${t.read_target}`, 'book-open'));
  if (t.cards_total) parts.push(partBar('Fiszki', t.parts.cards, `${t.cards_learned}/${t.cards_target}`, 'brain-circuit'));

  const unread = t.kb.find((k) => !k.read) || t.kb[0];
  const tasks = t.tasks || [];
  V.detail.append(card({
    class: 'rm-detail',
    body: h('div', { class: 'stack' },
      h('div', { class: 'rm-detail-head' },
        h('div', { class: 'rm-detail-ring' }, ring(t.progress, 84, 7), h('div', { class: 'rm-detail-pct' }, pct(t.progress))),
        h('div', { class: 'grow' }, h('div', { class: 'tiny muted' }, s.title), h('h3', null, t.title), h('div', { class: 'row gap-s mt-s' }, badge(stLabel, stTone)))),
      h('div', { class: 'muted' }, t.description),
      h('div', { class: 'stack gap-s' }, parts),
      h('div', null, h('div', { class: 'section-title' }, 'Kryteria'),
        h('div', { class: 'rm-crit' }, t.criteria.map((c) => h('div', { class: ['rm-crit-item', c.done ? 'done' : ''].join(' ') },
          icon(c.done ? 'circle-check' : 'circle-play', 15), h('span', { class: 'grow' }, c.label), h('span', { class: 'mono small' }, `${c.value}/${c.target}`))))),
      h('div', { class: 'row gap-s' },
        unread ? button(unread.read ? 'Powtórz artykuł' : 'Czytaj', { icon: 'book-open', variant: 'primary', size: 'sm', onClick: () => openKb(unread.id) }) : null,
        t.cards_total ? button('Fiszki z tematu', { icon: 'brain-circuit', size: 'sm', onClick: () => V.ctx.navigate('flashcards', { topic: t.id, title: t.title }) }) : null,
        t.has_tasks ? button('Moje zadania', { icon: 'folder', size: 'sm', variant: 'ghost', onClick: () => V.ctx.navigate('programs', { q: t.title }) }) : null),
      h('div', null, h('div', { class: 'section-title' }, `Artykuły (${t.kb.length})`),
        h('div', { class: 'list rm-list' }, t.kb.map((k) => h('div', { class: ['list-item', k.read ? 'rm-read' : ''].join(' '), onclick: () => openKb(k.id) },
          k.read ? icon('circle-check', 15) : icon('book-open', 15), h('span', { class: 'grow' }, k.title),
          k.level ? h('span', { class: 'rm-level', title: `Poziom ${k.level}/5` }, '●'.repeat(Math.min(5, k.level))) : null)))),
      t.has_tasks ? h('div', null, h('div', { class: 'section-title' }, `Twoje zadania z tematu (${tasks.length})`),
        tasks.length ? h('div', { class: 'list rm-list' }, tasks.slice(0, 12).map((p) => h('div', { class: 'list-item', title: p.round || '', onclick: () => V.ctx.navigate('programs', { id: p.id, q: p.problem_name }) },
          icon('file-code', 15), h('span', { class: 'grow ellipsis' }, p.problem_name, ' ', h('span', { class: 'muted mono small' }, p.problem_short || '')),
          h('span', { class: 'tiny muted nowrap' }, p.time ? timeAgo(p.time) : ''))))
          : h('div', { class: 'muted small' }, 'Jeszcze żadnego zadania z tego tematu — znajdziesz je na kółku, w archiwum OI albo na SIO2.'),
        tasks.length > 12 ? h('div', { class: 'tiny muted mt-s' }, `…i ${tasks.length - 12} więcej`) : null) : null),
  }));
}

function partBar(label, value, text, ic) {
  return h('div', { class: 'rm-part' },
    h('div', { class: 'row between small' }, h('span', { class: 'row gap-s' }, icon(ic, 14), label), h('span', { class: 'mono muted' }, text)),
    h('div', { class: 'rm-part-bar' }, h('div', { style: { width: pct(value) } })));
}

function openKb(id) {
  api.post('/api/journal/log', { kind: 'kb_view', data: { id, from: 'roadmap' } }, { quiet: true }).catch(() => {});
  if (V?.data) {           // od razu zaznacz jako przeczytany (pełne odświeżenie przy następnym wejściu)
    for (const s of V.data.stages) for (const t of s.topics) for (const k of t.kb) if (k.id === id) k.read = true;
    V.loadedAt = 0;
  }
  if (window.OIF?.views?.has('docs')) { V.ctx.navigate('docs', { id }); return; }
  api.get('/api/kb/article', { id }).then(({ article: a }) => {
    const blocks = (a.body || []).filter((b) => b.md).slice(0, 5).map((b) => b.md).join('\n\n');
    modal({ title: a.title, icon: 'book-open', width: 760, body: h('div', { class: 'md' }, markdown(`${a.summary || ''}\n\n${blocks}`)) });
    renderDetail();
  }).catch(() => {});
}

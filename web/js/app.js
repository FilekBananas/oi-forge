// Powłoka aplikacji OI Forge: wąski pasek z 5 miejscami (Kod, Szukaj, Nauka, Programy, Narzędzia), router,
// paleta komend (Ctrl+K), motyw, status systemu. Start = edytor. Żadne narzędzie nie znika — wszystkie są w „Narzędziach”,
// w palecie i w panelu narzędzi edytora.
import { h, icon, iconButton, toast, kbd, emptyState, clear, button, debounce, markdown } from './ui.js';
import { api, settings, updateSettings, onSettings, local } from './api.js';
import { refreshEditorsIn } from './editor.js';
import { isStarred } from './quality.js';

const views = new Map();      // id -> {meta, root, rendered, error}
const pendingParams = new Map();
let current = null;
let caps = null;
let health = null;
let S = {};

const app = document.getElementById('app');
const els = {};

// ------------------------------------------------------------------------------------- motyw
function applyTheme(s) {
  const root = document.documentElement;
  const theme = s.theme === 'system' ? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : (s.theme || 'dark');
  root.dataset.theme = theme;
  root.dataset.accent = s.accent || 'violet';
  root.dataset.density = s.density || 'comfortable';
  root.style.setProperty('--editor-font', `${(s.editor && s.editor.font_size) || 14}px`);
  local.set('theme', { theme: s.theme, accent: s.accent, density: s.density });
  if (els.themeBtn) els.themeBtn.replaceChildren(icon(theme === 'dark' ? 'sun' : 'moon', 18));
}

// ------------------------------------------------------------------------------------- szkielet
// Pasek ułożony pod przebieg zawodów: piszesz kod → rozumiesz zadanie → testujesz → szukasz w ściągawce.
// Każdy widok „należy” do jednego miejsca (podświetlenie), reszta → Narzędzia (tam jest WSZYSTKO, także trening).
const RAIL = [
  { id: 'code', view: ['workspace', 'scratchpad'], icon: 'code-xml', label: 'Kod', title: 'Edytor — piszesz, uruchamiasz, a wszystkie narzędzia masz pod ręką' },
  { id: 'task', view: ['taskexplain', 'suggest'], icon: 'file-text', label: 'Zadanie', title: 'Wklej treść: o co chodzi, jaki algorytm, testy brzegowe, brute, auto-solver' },
  { id: 'tests', view: ['tester', 'autotester'], icon: 'flask-conical', label: 'Testy', title: 'Rozwiązanie kontra brute na tysiącach testów, generatory, porównanie wyjść' },
  { id: 'search', view: ['search', 'assistant'], icon: 'search', label: 'Szukaj', title: 'Ściągawka: STL, algorytmy, snippety, asystent i Twoje programy' },
  { id: 'programs', view: ['programs'], icon: 'folder', label: 'Programy', title: 'Twoje rozwiązania z SIO2' },
  { id: 'tools', view: ['home'], icon: 'blocks', label: 'Narzędzia', title: 'Wszystkie narzędzia w jednym miejscu' },
];
const SECTION_OF = {
  workspace: 'code', scratchpad: 'code', playground: 'code',
  taskexplain: 'task', suggest: 'task', autosolver: 'task', edgecases: 'task',
  tester: 'tests', autotester: 'tests', diff: 'tests', benchmark: 'tests', gendsl: 'tests', grapheditor: 'tests', inspector: 'tests',
  search: 'search', assistant: 'search', docs: 'search', snippets: 'search',
  programs: 'programs',
};
const START_VIEW = () => (views.has('workspace') ? 'workspace' : 'home');

function buildShell() {
  els.nav = h('nav', { class: 'rail-nav' });
  els.status = h('button', { class: 'rail-status', type: 'button', title: 'Sprawdzam narzędzia…', onclick: () => navigate('settings', { tab: 'system' }) },
    h('span', { class: 'status-dot' }));
  els.themeBtn = h('button', { class: 'rail-btn', type: 'button', title: 'Przełącz motyw jasny/ciemny', onclick: toggleTheme }, icon('sun', 18));
  const rail = h('aside', { class: 'rail' },
    h('button', { class: 'rail-logo', type: 'button', title: 'OI Forge — edytor', onclick: () => navigate(START_VIEW()) }, icon('activity', 19, { strokeWidth: 2.4 })),
    els.nav,
    h('div', { class: 'rail-foot' },
      h('button', { class: 'rail-btn', type: 'button', title: `Szybkie wyszukiwanie (${isMac() ? '⌘K' : 'Ctrl+K'})`, onclick: () => openPalette() }, icon('command', 18)),
      els.themeBtn,
      h('button', { class: 'rail-btn', type: 'button', dataset: { view: 'settings' }, title: 'Ustawienia', onclick: () => navigate('settings') }, icon('settings', 18)),
      els.status));
  els.content = h('div', { class: 'content' });
  app.append(rail, h('div', { class: 'main' }, els.content));
}

function isMac() { return navigator.platform.toUpperCase().includes('MAC'); }
async function toggleTheme() {
  const cur = document.documentElement.dataset.theme;
  S = await updateSettings({ theme: cur === 'dark' ? 'light' : 'dark' });
}

// ------------------------------------------------------------------------------------- widoki
async function loadViews() {
  let list = [];
  try { list = (await api.get('/api/views', null, { quiet: true })).views || []; } catch { list = []; }
  setBoot(`Ładuję ${list.length} narzędzi…`);
  const results = await Promise.allSettled(list.map(async (v) => {
    if (v.css) document.head.append(h('link', { rel: 'stylesheet', href: `${v.css}?v=${v.cv || 0}` }));
    const mod = await import(`${v.js}?v=${v.v || 0}`);
    const meta = mod.default;
    if (!meta || typeof meta.render !== 'function') throw new Error('Moduł widoku nie eksportuje domyślnie {render}.');
    return { id: v.id, meta };
  }));
  results.forEach((r, i) => {
    const id = list[i].id;
    if (r.status === 'fulfilled') views.set(id, { meta: { id, group: 'Narzędzia', order: 100, icon: 'blocks', title: id, ...r.value.meta }, root: null, rendered: false });
    else {
      console.error(`Widok ${id}:`, r.reason);
      views.set(id, { meta: { id, title: id, group: 'Narzędzia', icon: 'triangle-alert', order: 999, render() {} }, error: String(r.reason?.message || r.reason), root: null, rendered: false });
    }
  });
}

function buildNav() {
  clear(els.nav);
  for (const r of RAIL) {
    const target = r.view.find((v) => views.has(v));
    if (!target) continue;
    els.nav.append(h('button', { class: 'rail-item', type: 'button', dataset: { section: r.id }, title: r.title, onclick: () => navigate(target) },
      h('span', { class: 'rail-ic' }, icon(r.icon, 21)), h('span', { class: 'rail-label' }, r.label)));
  }
}

function ctxFor(id) {
  return {
    navigate, id,
    hasView: (v) => views.has(v),
    get params() { return pendingParams.get(id) || {}; },
    get settings() { return S; },
    get caps() { return caps; },
    toast, api,
  };
}

function show(id, params) {
  if (!views.has(id)) id = views.has(START_VIEW()) ? START_VIEW() : [...views.keys()][0];
  if (!id) { els.content.replaceChildren(emptyState({ icon: 'triangle-alert', title: 'Brak widoków', text: 'Nie znaleziono żadnego widoku w web/js/views.' })); return; }
  const v = views.get(id);
  if (current && current !== id) {
    const prev = views.get(current);
    if (prev?.root) prev.root.hidden = true;
    try { prev?.meta.onHide && prev.meta.onHide(); } catch (e) { console.error(e); }
  }
  if (!v.root) {
    v.root = h('div', { class: ['view', v.meta.layout === 'full' ? 'full' : ''].join(' '), dataset: { view: id } });
    els.content.append(v.root);
  }
  v.root.hidden = false;
  if (!v.rendered) {
    v.rendered = true;
    if (v.error) {
      v.root.append(h('div', { class: 'view-error' }, emptyState({ icon: 'triangle-alert', title: `Nie udało się załadować narzędzia „${id}”`, text: v.error })));
    } else {
      try {
        const r = v.meta.render(v.root, ctxFor(id));
        if (r && typeof r.catch === 'function') r.catch((e) => renderError(v, e));
      } catch (e) { renderError(v, e); }
    }
  }
  current = id;
  pendingParams.set(id, params || {});
  if (v.meta.onShow && !v.error) {
    try { const r = v.meta.onShow(params || {}, ctxFor(id)); if (r && r.catch) r.catch((e) => console.error(e)); } catch (e) { console.error(e); }
  }
  const section = SECTION_OF[id] || (id === 'settings' ? null : 'tools');
  els.nav.querySelectorAll('.rail-item').forEach((b) => b.classList.toggle('active', b.dataset.section === section));
  app.querySelector('.rail-btn[data-view="settings"]')?.classList.toggle('active', id === 'settings');
  document.title = `${v.meta.title} · OI Forge`;
  els.content.scrollTop = 0;
  requestAnimationFrame(() => refreshEditorsIn(v.root));
}

function renderError(v, e) {
  console.error(e);
  v.root.append(h('div', { class: 'view-error' }, emptyState({ icon: 'bug', title: `Błąd w narzędziu „${v.meta.title}”`, text: String(e?.message || e) })));
}

export function navigate(id, params) {
  const target = `#/${id}`;
  if (params && Object.keys(params).length) pendingParams.set(id, params);
  else pendingParams.delete(id);
  if (location.hash === target) show(id, params || {});
  else { navigate._params = params || {}; location.hash = target; }
}

function onHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [id, qs] = raw.split('?');
  let params = navigate._params || pendingParams.get(id) || {};
  navigate._params = null;
  if (qs) params = { ...Object.fromEntries(new URLSearchParams(qs)), ...params };
  show(id || START_VIEW(), params);
}

// ------------------------------------------------------------------------------------- paleta komend
function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l'); }
function scoreView(meta, q) {
  const t = norm(meta.title), d = norm(meta.description), k = norm((meta.keywords || []).join(' '));
  if (!q) return 1;
  if (t.startsWith(q)) return 100;
  if (t.split(/\s+/).some((w) => w.startsWith(q))) return 80;
  if (t.includes(q)) return 60;
  if (k.split(/\s+/).some((w) => w.startsWith(q))) return 45;
  if (k.includes(q)) return 35;
  if (d.includes(q)) return 20;
  const words = q.split(/\s+/).filter(Boolean);
  if (words.length > 1 && words.every((w) => (t + ' ' + k + ' ' + d).includes(w))) return 30;
  return 0;
}

function openPalette(initial = '') {
  if (document.querySelector('.palette')) return;
  const inp = h('input', { placeholder: 'Wpisz nazwę narzędzia, temat (np. „set”, „dijkstra”) albo pytanie…', value: initial, spellcheck: 'false' });
  const list = h('div', { class: 'palette-list' });
  const box = h('div', { class: 'modal palette' },
    h('div', { class: 'palette-input' }, icon('search', 18), inp, kbd('Esc')), list,
    h('div', { class: 'palette-foot' }, h('span', null, kbd('↑'), ' ', kbd('↓'), ' wybór'), h('span', null, kbd('Enter'), ' otwórz'),
      h('span', { class: 'grow right' }, 'OI Forge · wszystko działa lokalnie')));
  const bd = h('div', { class: 'backdrop', onmousedown: (e) => { if (e.target === bd) close(); } }, box);
  document.body.append(bd);
  let items = [];
  let sel = 0;
  let reqId = 0;
  function close() { bd.remove(); document.removeEventListener('keydown', key, true); }
  function render(remote = []) {
    const q = norm(inp.value.trim());
    const viewItems = [...views.values()].filter((v) => !v.meta.hidden)
      .map((v) => ({ v, s: scoreView(v.meta, q) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s).slice(0, q ? 8 : 40)
      .map(({ v }) => ({ section: 'Narzędzia', icon: v.meta.icon, title: v.meta.title + (isStarred(v.meta.id) ? '  ★' : ''), sub: v.meta.description, run: () => navigate(v.meta.id) }));
    const actions = [];
    if (q) {
      if (views.has('assistant')) actions.push({ section: 'Akcje', icon: 'message-square', title: `Zapytaj asystenta: „${inp.value.trim()}”`, sub: 'Odpowiedź z lokalnej bazy wiedzy', run: () => navigate('assistant', { q: inp.value.trim() }) });
      if (views.has('programs')) actions.push({ section: 'Akcje', icon: 'folder', title: `Szukaj w moich programach: „${inp.value.trim()}”`, sub: 'Twoje rozwiązania z SIO2', run: () => navigate('programs', { q: inp.value.trim() }) });
    }
    actions.push({ section: 'Akcje', icon: document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon', title: 'Przełącz motyw jasny/ciemny', run: toggleTheme, hide: q && !'motyw jasny ciemny theme'.includes(q) });
    items = [...viewItems, ...actions.filter((a) => !a.hide), ...remote];
    sel = Math.min(sel, Math.max(0, items.length - 1));
    clear(list);
    let lastSection = null;
    items.forEach((it, i) => {
      if (it.section !== lastSection) { list.append(h('div', { class: 'palette-section' }, it.section)); lastSection = it.section; }
      const row = h('div', { class: ['palette-item', i === sel ? 'sel' : ''].join(' '), onmouseenter: () => { sel = i; mark(); }, onclick: () => { close(); it.run(); } },
        h('div', { class: 'pi-icon' }, icon(it.icon || 'file-text', 16)),
        h('div', { class: 'pi-text' }, h('div', { class: 'pi-title' }, it.title), it.sub ? h('div', { class: 'pi-sub' }, it.sub) : null),
        it.badge ? h('span', { class: 'badge' }, it.badge) : null);
      row.dataset.idx = i;
      list.append(row);
    });
    if (!items.length) list.append(h('div', { class: 'empty' }, 'Nic nie znaleziono.'));
  }
  function mark() { list.querySelectorAll('.palette-item').forEach((r) => r.classList.toggle('sel', +r.dataset.idx === sel)); list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' }); }
  const remoteSearch = debounce(async () => {
    const q = inp.value.trim();
    const my = ++reqId;
    if (q.length < 2) return;
    let res = [];
    try {
      const r = await api.get('/api/search/global', { q, k: 8 }, { quiet: true });
      res = (r.results || []).map((x) => resultItem(x));
    } catch {
      try {
        const r = await api.get('/api/kb/search', { q, k: 6 }, { quiet: true });
        res = (r.results || []).map((x) => ({ section: 'Baza wiedzy', icon: 'book-open', title: x.title, sub: String(x.summary || '').replace(/[`*_]{1,2}/g, ''), badge: x.category, run: () => navigate('docs', { id: x.id }) }));
      } catch { res = []; }
    }
    if (my === reqId && document.body.contains(bd)) render(res);
  }, 160);
  function resultItem(x) {
    const type = x.type || 'article';
    const section = { article: 'Baza wiedzy', qa: 'Pytania i odpowiedzi', snippet: 'Snippety', program: 'Moje programy', section: 'Baza wiedzy' }[type] || 'Wyniki';
    const ic = { article: 'book-open', qa: 'message-square', snippet: 'braces', program: 'file-code', section: 'book-open' }[type] || 'file-text';
    return {
      section, icon: ic, title: x.title || x.q || x.name || x.problem_name || x.id,
      sub: String(x.snippet || x.summary || x.description || '').replace(/[`*_]{1,2}/g, ''), badge: x.badge || x.category,
      run: () => {
        if (x.view && views.has(x.view)) navigate(x.view, x.params || {});
        else if (type === 'program') navigate('programs', { id: x.id, q: inp.value.trim() });
        else if (type === 'snippet') navigate('snippets', { trigger: x.trigger || x.id });
        else if (type === 'qa') navigate('assistant', { q: x.q || x.title || inp.value.trim() });
        else navigate('docs', { id: x.article_id || x.id });
      },
    };
  }
  function key(e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); const it = items[sel]; if (it) { close(); it.run(); } }
  }
  document.addEventListener('keydown', key, true);
  inp.addEventListener('input', () => { sel = 0; render(); remoteSearch(); });
  render();
  if (initial) remoteSearch();
  setTimeout(() => inp.focus(), 20);
}

// ------------------------------------------------------------------------------------- status systemu
async function checkHealth() {
  const dot = els.status.querySelector('.status-dot');
  const txt = { set textContent(t) { els.status.title = `${t} — kliknij, aby zobaczyć szczegóły`; } };
  try {
    [health, caps] = await Promise.all([api.get('/api/health', null, { quiet: true }), api.get('/api/capabilities', null, { quiet: true }).then((r) => r.capabilities)]);
    const cxx = caps.gcc_version ? caps.gcc_version.replace(/^.*?(\d+\.\d+(\.\d+)?).*$/, 'g++ $1') : (caps.clang ? 'clang' : null);
    const parts = [cxx || 'brak kompilatora!', caps.debugger || null].filter(Boolean);
    const broken = Object.keys(health.tool_errors || {});
    dot.className = 'status-dot ' + (!caps.cpp ? 'bad' : broken.length ? 'warn' : 'ok');
    txt.textContent = parts.join(' · ') + (broken.length ? ` · ${broken.length} moduł(y) z błędem` : '');
    if (!caps.cpp) toast('Nie znaleziono kompilatora C++ — część funkcji nie zadziała. Zobacz Ustawienia → System.', 'warn', { timeout: 9000 });
    window.OIF_CAPS = caps;
  } catch {
    dot.className = 'status-dot bad';
    txt.textContent = 'Brak połączenia z serwerem';
  }
}

// ------------------------------------------------------------------------------------- start
function setBoot(t) { const b = document.getElementById('boot-text'); if (b) b.textContent = t; }

async function boot() {
  S = await settings();
  applyTheme(S);
  onSettings((s) => { S = s; applyTheme(s); });
  matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', () => { if (S.theme === 'system') applyTheme(S); });
  buildShell();
  await loadViews();
  buildNav();
  window.addEventListener('hashchange', onHash);
  window.addEventListener('oif:navigate', (e) => navigate(e.detail.id, e.detail.params));
  document.addEventListener('keydown', (e) => {
    const m = e.metaKey || e.ctrlKey;
    if (m && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  });
  onHash();
  checkHealth();
  document.getElementById('boot')?.classList.add('done');
  setTimeout(() => document.getElementById('boot')?.remove(), 400);
}

window.OIF = { navigate, openPalette, views };
boot().catch((e) => {
  console.error(e);
  setBoot('Błąd uruchamiania: ' + (e?.message || e));
});

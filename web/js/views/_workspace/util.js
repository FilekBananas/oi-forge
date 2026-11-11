// Edytor projektów — wspólne helpery: ikony plików, ścieżki, menu kontekstowe, szybkie otwieranie, dialogi.
import { h, icon, button, input, textarea, field, modal, clear, toast } from '../../ui.js';
import { api, local } from '../../api.js';

// ------------------------------------------------------------------------------------- ikony
// Kilka ikon Lucide (ISC), których nie ma w web/js/icons.js — trzymamy je lokalnie, żeby nie ruszać rdzenia.
const EXTRA = {
  'folder-open': '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
  'folder-plus': '<path d="M12 10v6"/><path d="M9 13h6"/><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  'file-plus': '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 18v-6"/>',
  'file': '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  'file-output': '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M4 7V4a2 2 0 0 1 2-2 2 2 0 0 0-2 2"/><path d="M4.063 20.999a2 2 0 0 0 2 1L18 22a2 2 0 0 0 2-2V7l-5-5H6"/><path d="m5 11-3 3"/><path d="m5 17-3-3h10"/>',
  'more': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  'panel-left': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/>',
  'panel-right': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/>',
  'panel-bottom': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 15h18"/>',
  'chevrons-down-up': '<path d="m7 20 5-5 5 5"/><path d="m7 4 5 5 5-5"/>',
  'undo': '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>',
  'markdown': '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="M6 15V9l2.5 3L11 9v6"/><path d="m15 13 2 2 2-2"/><path d="M17 9v6"/>',
};
export function xicon(name, size = 16) {
  if (!EXTRA[name]) return icon(name, size);
  const svg = icon('check', size);
  svg.innerHTML = EXTRA[name];
  svg.classList.remove('icon-check');
  svg.classList.add(`icon-${name}`);
  return svg;
}

export function extOf(name) { const m = /\.([^.\/]+)$/.exec(name || ''); return m ? m[1].toLowerCase() : ''; }
export function baseName(path) { return String(path || '').split('/').pop(); }
export function dirName(path) { const p = String(path || '').split('/'); p.pop(); return p.join('/'); }
export function joinPath(dir, name) { return dir ? `${dir}/${name}` : name; }

const CPP = new Set(['cpp', 'cc', 'cxx', 'c++', 'hpp', 'hh', 'h', 'c']);
export function langOf(name) {
  const e = extOf(name);
  if (CPP.has(e)) return 'cpp';
  if (e === 'py' || e === 'pyw') return 'python';
  if (e === 'md' || e === 'markdown') return 'markdown';
  return 'text';
}
export const LANG_LABEL = { cpp: 'C++', python: 'Python', markdown: 'Markdown', text: 'Tekst' };
export function isRunnable(name) { const l = langOf(name); return l === 'cpp' || l === 'python'; }

/** Ikona + klasa koloru dla pliku/folderu (spójne z drzewem i kartami). */
export function fileIcon(name, { dir = false, open = false, size = 15 } = {}) {
  if (dir) {
    const lower = String(name).toLowerCase();
    const tests = ['testy', 'tests'].includes(lower);
    const el = xicon(open ? 'folder-open' : 'folder', size);
    el.classList.add('wsi', tests ? 'wsi-tests' : 'wsi-dir');
    return el;
  }
  const e = extOf(name);
  let ic = 'file', cls = 'wsi-file';
  if (CPP.has(e)) { ic = 'file-code'; cls = 'wsi-cpp'; }
  else if (e === 'py') { ic = 'file-code'; cls = 'wsi-py'; }
  else if (e === 'in') { ic = 'file-input'; cls = 'wsi-in'; }
  else if (['out', 'ans', 'ok', 'wy'].includes(e)) { ic = 'file-output'; cls = 'wsi-out'; }
  else if (e === 'md' || e === 'markdown') { ic = 'file-text'; cls = 'wsi-md'; }
  else if (['txt', 'log'].includes(e)) { ic = 'file-text'; cls = 'wsi-txt'; }
  else if (['json', 'yml', 'yaml', 'toml'].includes(e)) { ic = 'braces'; cls = 'wsi-json'; }
  else if (e === 'pdf') { ic = 'book-open'; cls = 'wsi-pdf'; }
  const el = xicon(ic, size);
  el.classList.add('wsi', cls);
  return el;
}

export function fmtSize(b) {
  if (b === null || b === undefined) return '';
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(b < 10240 ? 1 : 0)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}

export const isMac = () => navigator.platform.toUpperCase().includes('MAC');
export const modKey = () => (isMac() ? '⌘' : 'Ctrl');

/** Normalizacja do filtrowania (bez ogonków, małe litery). */
export function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l'); }

/** Dopasowanie „fuzzy” (podciąg) — wynik > 0 gdy pasuje; wyżej = lepiej. */
export function fuzzyScore(text, q) {
  if (!q) return 1;
  const t = norm(text), qq = norm(q);
  const idx = t.indexOf(qq);
  const base = baseName(t);
  if (base.startsWith(qq)) return 1000 - base.length;
  if (base.includes(qq)) return 800 - base.length;
  if (idx >= 0) return 600 - t.length;
  let ti = 0, score = 0, streak = 0;
  for (const ch of qq) {
    const f = t.indexOf(ch, ti);
    if (f < 0) return 0;
    streak = f === ti ? streak + 1 : 0;
    score += 1 + streak * 2;
    ti = f + 1;
  }
  return 100 + score - t.length * 0.1;
}

// ------------------------------------------------------------------------------------- API workspace
export const ws = {
  tree: () => api.get('/api/ws/tree', null, { quiet: true }),
  file: (path, opts) => api.get('/api/ws/file', { path }, opts),
  save: (body) => api.post('/api/ws/save', body, { quiet: true }),
  create: (body) => api.post('/api/ws/create', body),
  rename: (body) => api.post('/api/ws/rename', body),
  del: (path) => api.post('/api/ws/delete', { path }),
  restore: (trash_id) => api.post('/api/ws/restore', { trash_id }),
  trash: () => api.get('/api/ws/trash', null, { quiet: true }),
  duplicate: (path) => api.post('/api/ws/duplicate', { path }),
  newTask: (body) => api.post('/api/ws/new_task', body),
  newProject: (body) => api.post('/api/ws/new_project', body),
  importProgram: (program_id) => api.post('/api/ws/import_program', { program_id }),
  task: (path, content = false) => api.get('/api/ws/task', { path, content: content ? 1 : 0 }, { quiet: true }),
  tests: (path) => api.get('/api/ws/tests', { path }, { quiet: true }),
  saveTest: (body) => api.post('/api/ws/save_test', body),
  search: (q) => api.get('/api/ws/search', { q }, { quiet: true }),
  info: () => api.get('/api/ws/info', null, { quiet: true }),
};

// ------------------------------------------------------------------------------------- menu kontekstowe
let openMenu = null;
export function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }

/** items: [{label, icon, onClick, kbd, danger, disabled} | '-' ] */
export function contextMenu(x, y, items) {
  closeMenu();
  const el = h('div', { class: 'ws-menu', role: 'menu' });
  for (const it of items) {
    if (it === '-' || !it) { if (it === '-') el.append(h('div', { class: 'ws-menu-sep' })); continue; }
    el.append(h('button', { type: 'button', role: 'menuitem', class: ['ws-menu-item', it.danger ? 'danger' : ''].join(' '), disabled: it.disabled,
      onclick: (e) => { e.stopPropagation(); closeMenu(); it.onClick && it.onClick(); } },
    it.icon ? xicon(it.icon, 14) : h('span', { class: 'ws-menu-noicon' }), h('span', { class: 'grow' }, it.label),
    it.kbd ? h('span', { class: 'ws-menu-kbd' }, it.kbd) : null));
  }
  document.body.append(el);
  const r = el.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  el.style.left = Math.max(6, Math.min(x, vw - r.width - 6)) + 'px';
  el.style.top = Math.max(6, Math.min(y, vh - r.height - 6)) + 'px';
  openMenu = el;
  el.querySelector('button:not([disabled])')?.focus();
  el.addEventListener('keydown', (e) => {
    const btns = [...el.querySelectorAll('button:not([disabled])')];
    const i = btns.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length]?.focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); closeMenu(); }
  });
  return el;
}
document.addEventListener('mousedown', (e) => { if (openMenu && !openMenu.contains(e.target)) closeMenu(); }, true);
window.addEventListener('blur', closeMenu);
window.addEventListener('resize', closeMenu);

// ------------------------------------------------------------------------------------- szybkie otwieranie (Ctrl+P)
export function quickOpen(files, { onPick, recent = [], placeholder = 'Wpisz nazwę pliku…' } = {}) {
  if (document.querySelector('.ws-quick')) return;
  const inp = h('input', { placeholder, spellcheck: 'false' });
  const list = h('div', { class: 'palette-list' });
  const box = h('div', { class: 'modal palette ws-quick' },
    h('div', { class: 'palette-input' }, icon('search', 18), inp, h('kbd', null, 'Esc')), list,
    h('div', { class: 'palette-foot' }, h('span', null, h('kbd', null, '↑'), ' ', h('kbd', null, '↓'), ' wybór'), h('span', null, h('kbd', null, 'Enter'), ' otwórz'),
      h('span', { class: 'grow right' }, `${files.length} plików w projektach`)));
  const bd = h('div', { class: 'backdrop', onmousedown: (e) => { if (e.target === bd) close(); } }, box);
  document.body.append(bd);
  let items = [];
  let sel = 0;
  const recentSet = new Map(recent.map((p, i) => [p, i]));
  function close() { bd.remove(); document.removeEventListener('keydown', key, true); }
  function render() {
    const q = inp.value.trim();
    if (!q) {
      const rec = recent.filter((p) => files.includes(p));
      items = [...rec, ...files.filter((p) => !recentSet.has(p))].slice(0, 60);
    } else {
      items = files.map((p) => ({ p, s: fuzzyScore(p, q) + (recentSet.has(p) ? 50 - recentSet.get(p) : 0) }))
        .filter((x) => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 60).map((x) => x.p);
    }
    sel = Math.min(sel, Math.max(0, items.length - 1));
    clear(list);
    if (!items.length) { list.append(h('div', { class: 'empty' }, 'Nic nie znaleziono.')); return; }
    items.forEach((p, i) => {
      list.append(h('div', { class: ['palette-item', i === sel ? 'sel' : ''].join(' '), dataset: { idx: i },
        onmouseenter: () => { sel = i; mark(); }, onclick: () => { close(); onPick(p); } },
      h('div', { class: 'pi-icon' }, fileIcon(baseName(p))),
      h('div', { class: 'pi-text' }, h('div', { class: 'pi-title' }, baseName(p)), h('div', { class: 'pi-sub' }, dirName(p) || 'katalog główny')),
      recentSet.has(p) && !q ? h('span', { class: 'badge' }, 'ostatnio') : null));
    });
  }
  function mark() { list.querySelectorAll('.palette-item').forEach((r) => r.classList.toggle('sel', +r.dataset.idx === sel)); list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' }); }
  function key(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); const p = items[sel]; if (p) { close(); onPick(p); } }
  }
  document.addEventListener('keydown', key, true);
  inp.addEventListener('input', () => { sel = 0; render(); });
  render();
  setTimeout(() => inp.focus(), 20);
}

// ------------------------------------------------------------------------------------- dialogi
/** Modal „Nowe zadanie”: nazwa + opcjonalna treść (wklejona). Zwraca {name, statement} albo null. */
export function newTaskDialog({ title = 'Nowe zadanie', withStatement = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const name = input({ placeholder: 'np. Suma liczb, OI XXXI — Zapobiegliwy student…', onEnter: () => submit() });
    const stmt = withStatement ? textarea({ rows: 9, mono: false, placeholder: 'Wklej treść zadania (opcjonalnie). Przykłady z treści same staną się testami w folderze testy/.' }) : null;
    const submit = () => {
      if (!name.value.trim() && !(stmt && stmt.value.trim())) { name.focus(); toast('Podaj nazwę zadania albo wklej treść.', 'warn'); return; }
      done = true; resolve({ name: name.value.trim(), statement: stmt ? stmt.value : '' }); m.close();
    };
    const m = modal({
      title, icon: withStatement ? 'file-code' : 'folder', width: 620,
      body: h('div', { class: 'stack' },
        field('Nazwa', name),
        withStatement ? field('Treść zadania', stmt, 'Powstanie folder z plikami: rozw.cpp, brute.cpp, gen.py, testy/, tresc.md, notatki.md.') : null),
      onClose: () => { if (!done) resolve(null); },
      actions: [button('Anuluj', { variant: 'ghost', onClick: () => m.close() }),
        button(withStatement ? 'Utwórz zadanie' : 'Utwórz projekt', { variant: 'primary', icon: 'plus', onClick: submit })],
    });
  });
}

/** Wybór programu z modułu „Moje programy” (import do edytora). */
export async function pickProgram() {
  let list = [];
  try { list = (await api.get('/api/programs/list', null, { quiet: true })).programs || []; } catch (e) {
    toast('Moduł „Moje programy” jest niedostępny.', 'warn'); return null;
  }
  if (!list.length) { toast('Brak zapisanych programów (zaimportuj je w „Moje programy”).', 'info'); return null; }
  return new Promise((resolve) => {
    let done = false;
    const results = h('div', { class: 'ws-pick-list' });
    const q = input({ placeholder: 'Szukaj zadania (nazwa, skrót, konkurs)…', onInput: () => render() });
    function render() {
      clear(results);
      const qq = norm(q.value.trim());
      const hits = list.filter((p) => !qq || norm(`${p.problem_name} ${p.problem_short} ${p.contest_name} ${p.round || ''}`).includes(qq)).slice(0, 80);
      if (!hits.length) results.append(h('div', { class: 'muted small pad' }, 'Nic nie znaleziono.'));
      for (const p of hits) {
        results.append(h('button', { type: 'button', class: 'ws-pick-item', onclick: () => { done = true; resolve(p.id); m.close(); } },
          xicon('file-code', 15), h('div', { class: 'grow' }, h('div', { class: 'bold' }, p.problem_name || p.problem_short || p.id),
            h('div', { class: 'muted tiny' }, [p.problem_short, p.contest_name, p.round].filter(Boolean).join(' · '))),
          p.score !== undefined && p.score !== null ? h('span', { class: 'badge success' }, `${p.score} pkt`) : null));
      }
    }
    const m = modal({
      title: 'Importuj z „Moich programów”', icon: 'download', width: 620,
      body: h('div', { class: 'stack gap-s' }, q, results),
      onClose: () => { if (!done) resolve(null); },
      actions: [button('Anuluj', { variant: 'ghost', onClick: () => m.close() })],
    });
    render();
  });
}

/** Toast z przyciskiem akcji (np. „Cofnij”). */
export function actionToast(text, actionLabel, onAction, { tone = 'info', timeout = 7000 } = {}) {
  const btn = h('button', { type: 'button', class: 'btn sm ws-toast-btn', onclick: (e) => { e.stopPropagation(); t.close(); onAction(); } }, xicon('undo', 13), actionLabel);
  const t = toast(h('div', { class: 'row nowrap gap-s', style: 'justify-content:space-between;width:100%' }, h('span', null, text), btn), tone, { timeout });
  return t;
}

// ------------------------------------------------------------------------------------- stan (localStorage)
export const persist = {
  get: (k, d) => local.get('ws:' + k, d),
  set: (k, v) => local.set('ws:' + k, v),
};

/** Przeciągany uchwyt do zmiany rozmiaru panelu. axis: 'x' | 'y'; onMove(delta) dostaje przesunięcie od startu. */
export function dragHandle(axis, { onStart, onMove, onEnd, title } = {}) {
  const el = h('div', { class: `ws-handle ws-handle-${axis}`, role: 'separator', title });
  el.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    el.classList.add('drag');
    document.body.classList.add(axis === 'x' ? 'ws-resizing-x' : 'ws-resizing-y');
    const start = axis === 'x' ? e.clientX : e.clientY;
    onStart && onStart();
    const move = (ev) => onMove && onMove((axis === 'x' ? ev.clientX : ev.clientY) - start);
    const up = () => {
      el.classList.remove('drag');
      document.body.classList.remove('ws-resizing-x', 'ws-resizing-y');
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      onEnd && onEnd();
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  });
  return el;
}

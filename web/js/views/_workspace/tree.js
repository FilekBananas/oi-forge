// Edytor projektów — lewy panel: drzewo plików (rozwijanie, menu kontekstowe, zmiana nazwy w miejscu, przeciąganie,
// filtr + szukanie w treści plików).
import { h, icon, clear, toast, confirmDialog, debounce, readFileText } from '../../ui.js';
import { xicon, fileIcon, contextMenu, closeMenu, ws, persist, baseName, dirName, joinPath, norm, fmtSize, modKey, actionToast } from './util.js';

const MAX_DROP = 5 * 1024 * 1024;

/**
 * opts: { onOpen(path, {line}), onRenamed(old, neu), onDeleted(path), onCreated(path, kind), onNewTask(dir), onRunTests(path),
 *         getActive() -> path|null }
 */
export function createTree(opts) {
  const expanded = new Set(persist.get('expanded', []) || []);
  let data = [];
  let byPath = new Map();
  let filter = '';
  let editing = null;             // {path|null, dir, kind, mode: 'rename'|'create'}
  let focusPath = null;

  const saveExpanded = debounce(() => persist.set('expanded', [...expanded]), 300);

  // ---------------------------------------------------------------- szkielet
  const filterInput = h('input', { class: 'ws-filter-input', placeholder: 'Szukaj plików i tekstu…', spellcheck: 'false',
    oninput: () => { filter = filterInput.value.trim(); render(); contentSearch(); },
    onkeydown: (e) => {
      if (e.key === 'Escape') { filterInput.value = ''; filter = ''; render(); contentSearch(); filterInput.blur(); }
      if (e.key === 'ArrowDown') { e.preventDefault(); body.querySelector('.ws-row')?.focus(); }
      if (e.key === 'Enter') { const first = body.querySelector('.ws-row[data-type="file"]'); if (first) opts.onOpen(first.dataset.path); }
    } });
  const filterBox = h('div', { class: 'ws-filter' }, icon('search', 14), filterInput,
    h('button', { type: 'button', class: 'ws-filter-x', title: 'Wyczyść', onclick: () => { filterInput.value = ''; filter = ''; render(); contentSearch(); } }, icon('x', 13)));
  const body = h('div', { class: 'ws-tree-body', tabindex: -1 });
  const hits = h('div', { class: 'ws-hits', hidden: true });
  const scroller = h('div', { class: 'ws-tree-scroll' }, body, hits);
  const el = h('div', { class: 'ws-tree' }, filterBox, scroller);

  // ---------------------------------------------------------------- dane
  function index(nodes, depth = 0, parent = null) {
    for (const n of nodes) {
      n.depth = depth;
      n.parent = parent;
      byPath.set(n.path, n);
      if (n.children) index(n.children, depth + 1, n);
    }
  }
  function setData(tree) {
    data = tree || [];
    byPath = new Map();
    index(data);
    // usuń z rozwiniętych foldery, których już nie ma
    let changed = false;
    for (const p of [...expanded]) if (!byPath.has(p)) { expanded.delete(p); changed = true; }
    if (changed) saveExpanded();
    render();
  }
  function files() {
    const out = [];
    const walk = (nodes) => { for (const n of nodes) { if (n.type === 'file') out.push(n.path); else walk(n.children || []); } };
    walk(data);
    return out;
  }
  function dirs() {
    const out = [];
    const walk = (nodes) => { for (const n of nodes) if (n.type === 'dir') { out.push(n.path); walk(n.children || []); } };
    walk(data);
    return out;
  }

  // ---------------------------------------------------------------- render
  function matches(n) {
    if (!filter) return true;
    const q = norm(filter);
    if (norm(n.name).includes(q)) return true;
    return (n.children || []).some(matches);
  }

  function render() {
    const keepScroll = scroller.scrollTop;
    clear(body);
    const active = opts.getActive();
    const frag = document.createDocumentFragment();
    const walk = (nodes, depth) => {
      // tworzenie nowego elementu w katalogu głównym
      if (depth === 0 && editing?.mode === 'create' && editing.dir === '') frag.append(editRow(editing, 0));
      for (const n of nodes) {
        if (!matches(n)) continue;
        const open = n.type === 'dir' && (expanded.has(n.path) || !!filter);
        if (editing?.mode === 'rename' && editing.path === n.path) frag.append(editRow(editing, depth, n));
        else frag.append(row(n, depth, open, active));
        if (n.type === 'dir' && open) {
          if (editing?.mode === 'create' && editing.dir === n.path) frag.append(editRow(editing, depth + 1));
          walk(n.children || [], depth + 1);
        }
      }
    };
    walk(data, 0);
    body.append(frag);
    if (!data.length && !editing) {
      body.append(h('div', { class: 'ws-tree-empty' }, h('div', { class: 'muted small' }, 'Brak projektów.'),
        h('button', { type: 'button', class: 'btn sm primary', onclick: () => opts.onNewTask('') }, icon('plus', 14), 'Nowe zadanie')));
    } else if (filter && !body.querySelector('.ws-row')) {
      body.append(h('div', { class: 'ws-tree-empty muted small' }, 'Żadna nazwa pliku nie pasuje.'));
    }
    scroller.scrollTop = keepScroll;
    const inp = body.querySelector('.ws-edit-input');
    if (inp) { inp.focus(); const dot = inp.value.lastIndexOf('.'); inp.setSelectionRange(0, editing.kind === 'file' && dot > 0 ? dot : inp.value.length); }
  }

  function indent(depth) {
    const g = h('span', { class: 'ws-indent', style: { width: `${depth * 14}px` } });
    for (let i = 0; i < depth; i++) g.append(h('i', { style: { left: `${i * 14 + 13}px` } }));
    return g;
  }

  function row(n, depth, open, active) {
    const isDir = n.type === 'dir';
    const r = h('div', {
      class: ['ws-row', isDir ? 'dir' : 'file', n.path === active ? 'active' : '', n.path === focusPath ? 'focused' : ''].join(' '),
      tabindex: -1, draggable: 'true', role: 'treeitem', 'aria-expanded': isDir ? String(open) : undefined,
      dataset: { path: n.path, type: n.type }, title: isDir ? n.path : `${n.path} · ${fmtSize(n.size)}`,
      onclick: (e) => { focusPath = n.path; if (isDir) toggle(n.path); else opts.onOpen(n.path, { preview: !e.detail || e.detail === 1 }); },
      oncontextmenu: (e) => { e.preventDefault(); focusPath = n.path; menuFor(n, e.clientX, e.clientY); },
      onkeydown: (e) => keyNav(e, n),
    },
    indent(depth),
    h('span', { class: 'ws-chev' }, isDir ? icon(open ? 'chevron-down' : 'chevron-right', 14) : null),
    fileIcon(n.name, { dir: isDir, open }),
    h('span', { class: 'ws-name' }, highlight(n.name)),
    h('button', { type: 'button', class: 'ws-more', title: 'Więcej akcji', tabindex: -1,
      onclick: (e) => { e.stopPropagation(); const b = e.currentTarget.getBoundingClientRect(); menuFor(n, b.left, b.bottom + 2); } }, xicon('more', 15)));
    // przeciąganie: plik/folder → folder
    r.addEventListener('dragstart', (e) => { e.dataTransfer.setData('application/x-oif-path', n.path); e.dataTransfer.effectAllowed = 'move'; r.classList.add('dragging'); });
    r.addEventListener('dragend', () => r.classList.remove('dragging'));
    return r;
  }

  function highlight(name) {
    if (!filter) return name;
    const i = norm(name).indexOf(norm(filter));
    if (i < 0) return name;
    return [name.slice(0, i), h('mark', null, name.slice(i, i + filter.length)), name.slice(i + filter.length)];
  }

  function editRow(ed, depth, node) {
    const isDir = ed.kind === 'dir';
    const inp = h('input', { class: 'ws-edit-input', value: ed.mode === 'rename' ? node.name : (ed.value || ''), spellcheck: 'false',
      placeholder: isDir ? 'nazwa folderu' : 'nazwa.cpp' });
    let done = false;
    const commit = async () => {
      if (done) return;
      done = true;
      const v = inp.value.trim();
      const cur = editing;
      editing = null;
      if (!v || (cur.mode === 'rename' && v === node.name)) { render(); return; }
      try {
        if (cur.mode === 'rename') {
          const r = await ws.rename({ path: cur.path, new_name: v });
          await opts.reload();
          opts.onRenamed(cur.path, r.path);
          select(r.path);
        } else {
          const r = await ws.create({ path: joinPath(cur.dir, v), kind: cur.kind });
          if (cur.dir) expanded.add(cur.dir);
          if (cur.kind === 'dir') expanded.add(r.path);
          saveExpanded();
          await opts.reload();
          if (r.path !== joinPath(cur.dir, v)) toast(`Nazwa poprawiona na „${baseName(r.path)}” (niedozwolone znaki).`, 'info');
          if (cur.kind === 'file') opts.onOpen(r.path); else select(r.path);
        }
      } catch { render(); }
    };
    inp.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { e.preventDefault(); done = true; editing = null; render(); }
    });
    inp.addEventListener('blur', () => setTimeout(commit, 0));
    const name = ed.mode === 'rename' ? node.name : (isDir ? 'folder' : (inp.value || 'plik'));
    return h('div', { class: 'ws-row editing' }, indent(depth), h('span', { class: 'ws-chev' }), fileIcon(name, { dir: isDir }), inp);
  }

  // ---------------------------------------------------------------- akcje
  function toggle(path, force) {
    const open = force ?? !expanded.has(path);
    if (open) expanded.add(path); else expanded.delete(path);
    saveExpanded();
    render();
  }
  function select(path) { focusPath = path; reveal(path); }
  function reveal(path) {
    if (!path) return;
    let d = dirName(path);
    let changed = false;
    while (d) { if (!expanded.has(d)) { expanded.add(d); changed = true; } d = dirName(d); }
    if (changed) saveExpanded();
    render();
    const r = body.querySelector(`.ws-row[data-path="${CSS.escape(path)}"]`);
    r?.scrollIntoView({ block: 'nearest' });
  }
  function startCreate(dir, kind) {
    dir = dir || '';
    if (dir && byPath.get(dir)?.type === 'file') dir = dirName(dir);
    if (dir) { expanded.add(dir); saveExpanded(); }
    if (filter) { filter = ''; filterInput.value = ''; contentSearch(); }
    editing = { mode: 'create', dir, kind };
    render();
  }
  function startRename(path) {
    if (!byPath.has(path)) return;
    if (filter) { filter = ''; filterInput.value = ''; contentSearch(); }
    reveal(path);
    editing = { mode: 'rename', path, kind: byPath.get(path).type };
    render();
  }
  async function remove(path) {
    const n = byPath.get(path);
    if (!n) return;
    if (n.type === 'dir' && (n.children || []).length > 0) {
      const ok = await confirmDialog(`Przenieść folder „${n.name}” razem z zawartością do kosza? Można go potem przywrócić.`,
        { title: 'Usuń folder', ok: 'Przenieś do kosza', danger: true });
      if (!ok) return;
    }
    const r = await ws.del(path);
    opts.onDeleted(path);
    await opts.reload();
    actionToast(`Przeniesiono „${n.name}” do kosza.`, 'Cofnij', async () => {
      const rr = await ws.restore(r.trash_id);
      await opts.reload();
      select(rr.path);
      if (n.type === 'file') opts.onOpen(rr.path);
      toast('Przywrócono.', 'success', { timeout: 1500 });
    });
  }
  async function duplicate(path) {
    const r = await ws.duplicate(path);
    await opts.reload();
    select(r.path);
  }
  async function copyPath(path, abs) {
    const text = abs ? `${opts.rootPath() || ''}/${path}`.replace(/\\/g, '/') : path;
    try { await navigator.clipboard.writeText(text); toast('Skopiowano ścieżkę', 'success', { timeout: 1200 }); } catch { toast(text, 'info'); }
  }

  function menuFor(n, x, y) {
    const isDir = n.type === 'dir';
    const dir = isDir ? n.path : dirName(n.path);
    const m = modKey();
    contextMenu(x, y, [
      !isDir ? { label: 'Otwórz', icon: 'file-code', onClick: () => opts.onOpen(n.path) } : null,
      { label: 'Nowy plik tutaj', icon: 'file-plus', onClick: () => startCreate(dir, 'file') },
      { label: 'Nowy folder tutaj', icon: 'folder-plus', onClick: () => startCreate(dir, 'dir') },
      isDir ? { label: 'Nowe zadanie w tym folderze', icon: 'plus', onClick: () => opts.onNewTask(n.path) } : null,
      '-',
      isDir || /\.(cpp|cc|cxx|py)$/i.test(n.name) ? { label: 'Uruchom wszystkie testy', icon: 'list-checks', onClick: () => opts.onRunTests(n.path) } : null,
      { label: 'Zmień nazwę', icon: 'pencil', kbd: 'F2', onClick: () => startRename(n.path) },
      { label: 'Duplikuj', icon: 'copy', onClick: () => duplicate(n.path) },
      { label: 'Kopiuj ścieżkę', icon: 'clipboard-paste', onClick: () => copyPath(n.path, true) },
      { label: 'Kopiuj ścieżkę względną', icon: 'clipboard-paste', onClick: () => copyPath(n.path, false) },
      '-',
      { label: 'Usuń (do kosza)', icon: 'trash-2', kbd: m === '⌘' ? '⌘⌫' : 'Del', danger: true, onClick: () => remove(n.path) },
    ].filter((x) => x !== null));
  }

  function keyNav(e, n) {
    if (editing) return;
    const rows = [...body.querySelectorAll('.ws-row:not(.editing)')];
    const i = rows.indexOf(e.currentTarget);
    const focusRow = (r) => { if (r) { focusPath = r.dataset.path; rows.forEach((x) => x.classList.toggle('focused', x === r)); r.focus(); } };
    if (e.key === 'ArrowDown') { e.preventDefault(); focusRow(rows[i + 1]); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (i <= 0) filterInput.focus(); else focusRow(rows[i - 1]); }
    else if (e.key === 'ArrowRight' && n.type === 'dir') { e.preventDefault(); if (!expanded.has(n.path)) toggle(n.path, true); else focusRow(rows[i + 1]); body.querySelector(`.ws-row[data-path="${CSS.escape(focusPath)}"]`)?.focus(); }
    else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      if (n.type === 'dir' && expanded.has(n.path)) { toggle(n.path, false); body.querySelector(`.ws-row[data-path="${CSS.escape(n.path)}"]`)?.focus(); }
      else if (n.parent) { focusPath = n.parent.path; render(); body.querySelector(`.ws-row[data-path="${CSS.escape(n.parent.path)}"]`)?.focus(); }
    }
    else if (e.key === 'Enter') { e.preventDefault(); if (n.type === 'dir') { toggle(n.path); body.querySelector(`.ws-row[data-path="${CSS.escape(n.path)}"]`)?.focus(); } else opts.onOpen(n.path); }
    else if (e.key === 'F2') { e.preventDefault(); startRename(n.path); }
    else if (e.key === 'Delete' || (e.key === 'Backspace' && (e.metaKey || e.ctrlKey))) { e.preventDefault(); remove(n.path); }
  }

  // ---------------------------------------------------------------- przeciąganie (wewnątrz drzewa i pliki z dysku)
  let dropTarget = null;
  const targetDir = (e) => {
    const r = e.target.closest?.('.ws-row');
    if (!r || !r.dataset.path) return '';
    return r.dataset.type === 'dir' ? r.dataset.path : dirName(r.dataset.path);
  };
  const markDrop = (dir) => {
    body.querySelectorAll('.drop-into').forEach((x) => x.classList.remove('drop-into'));
    el.classList.toggle('drop-root', dir === '');
    if (dir) body.querySelector(`.ws-row[data-path="${CSS.escape(dir)}"]`)?.classList.add('drop-into');
    dropTarget = dir;
  };
  scroller.addEventListener('dragover', (e) => {
    const types = [...(e.dataTransfer?.types || [])];
    if (!types.includes('Files') && !types.includes('application/x-oif-path')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = types.includes('Files') ? 'copy' : 'move';
    markDrop(targetDir(e));
  });
  scroller.addEventListener('dragleave', (e) => { if (!scroller.contains(e.relatedTarget)) { markDrop(null); el.classList.remove('drop-root'); } });
  scroller.addEventListener('drop', async (e) => {
    e.preventDefault();
    const dir = dropTarget ?? targetDir(e);
    markDrop(null);
    el.classList.remove('drop-root');
    const src = e.dataTransfer.getData('application/x-oif-path');
    if (src) {
      if (src === dir || dirName(src) === dir) return;
      if (dir === src || dir.startsWith(src + '/')) { toast('Nie można przenieść folderu do jego wnętrza.', 'warn'); return; }
      try {
        const r = await ws.rename({ path: src, new_path: joinPath(dir, baseName(src)) });
        if (dir) expanded.add(dir);
        saveExpanded();
        await opts.reload();
        opts.onRenamed(src, r.path);
        select(r.path);
        toast(`Przeniesiono do „${dir || 'katalogu głównego'}”.`, 'success', { timeout: 1500 });
      } catch { /* toast z api */ }
      return;
    }
    const filesIn = [...(e.dataTransfer.files || [])];
    if (!filesIn.length) return;
    let added = 0, last = null;
    for (const f of filesIn.slice(0, 50)) {
      if (f.size > MAX_DROP) { toast(`„${f.name}” jest za duży (limit 5 MB).`, 'warn'); continue; }
      if (!f.type && f.size === 0 && !/\.[a-z0-9]+$/i.test(f.name)) continue;   // folder z dysku — pomijamy
      try {
        const text = await readFileText(f);
        if (text.includes('\u0000')) { toast(`„${f.name}” wygląda na plik binarny — pominięto.`, 'warn'); continue; }
        const r = await ws.create({ path: joinPath(dir, f.name), kind: 'file', content: text, unique: true });
        added++; last = r.path;
      } catch { /* toast */ }
    }
    if (added) {
      if (dir) { expanded.add(dir); saveExpanded(); }
      await opts.reload();
      toast(`Dodano ${added} ${added === 1 ? 'plik' : added < 5 ? 'pliki' : 'plików'}.`, 'success', { timeout: 1800 });
      if (added === 1 && last) opts.onOpen(last);
    }
  });

  // ---------------------------------------------------------------- szukanie w treści
  let searchReq = 0;
  const contentSearch = debounce(async () => {
    const q = filter;
    const my = ++searchReq;
    if (q.length < 2) { hits.hidden = true; clear(hits); return; }
    let r;
    try { r = await ws.search(q); } catch { return; }
    if (my !== searchReq) return;
    clear(hits);
    const text = (r.results || []).filter((x) => x.kind === 'text');
    hits.hidden = false;
    hits.append(h('div', { class: 'ws-hits-head' }, icon('scan-search', 13), `W treści plików: ${text.length}${r.truncated ? '+' : ''}`));
    if (!text.length) { hits.append(h('div', { class: 'muted tiny ws-hits-none' }, 'Brak wystąpień w treści.')); return; }
    let lastFile = null;
    for (const x of text.slice(0, 200)) {
      if (x.path !== lastFile) {
        lastFile = x.path;
        hits.append(h('div', { class: 'ws-hit-file', title: x.path, onclick: () => opts.onOpen(x.path) }, fileIcon(baseName(x.path), { size: 13 }),
          h('span', { class: 'ws-name' }, baseName(x.path)), h('span', { class: 'ws-hit-dir' }, dirName(x.path))));
      }
      const t = x.text;
      const i = norm(t).indexOf(norm(q));
      hits.append(h('div', { class: 'ws-hit', title: `${x.path}:${x.line}`, onclick: () => opts.onOpen(x.path, { line: x.line }) },
        h('span', { class: 'ws-hit-ln' }, String(x.line)),
        h('span', { class: 'ws-hit-text' }, i >= 0 ? [t.slice(0, i), h('mark', null, t.slice(i, i + q.length)), t.slice(i + q.length)] : t)));
    }
  }, 220);

  return {
    el, setData, render, reveal, select, startCreate, startRename, remove, files, dirs,
    get filterInput() { return filterInput; },
    collapseAll() { expanded.clear(); saveExpanded(); render(); },
    focusFilter() { filterInput.focus(); filterInput.select(); },
    renamePrefix(oldP, newP) {
      for (const p of [...expanded]) {
        if (p === oldP || p.startsWith(oldP + '/')) { expanded.delete(p); expanded.add(newP + p.slice(oldP.length)); }
      }
      saveExpanded();
    },
    closeMenu,
  };
}

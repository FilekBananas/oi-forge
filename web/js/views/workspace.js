// Edytor projektów: prawdziwe pliki ucznia (projekty i zadania) w układzie jak onlinegdb/VS Code —
// drzewo z lewej, karty + kod w środku, „Wejście | Wyjście” na dole, „Narzędzia” wysuwane z prawej.
import { h, icon, button, iconButton, clear, toast, confirmDialog, modal, markdown, cmMode, kbd, debounce, codeBlock } from '../ui.js';
import { api } from '../api.js';
import { createEditor } from '../editor.js';
import { xicon, fileIcon, ws, persist, baseName, dirName, joinPath, langOf, LANG_LABEL, isRunnable, quickOpen, newTaskDialog,
  pickProgram, contextMenu, dragHandle, modKey, isMac, actionToast } from './_workspace/util.js';
import { createTree } from './_workspace/tree.js';
import { createBottom } from './_workspace/bottom.js';
import { createTools } from './_workspace/tools.js';

const EXT_FOR = { cpp: '.cpp', python: '.py', text: '.txt' };
const edLang = (l) => (l === 'cpp' || l === 'python' ? l : 'text');

export default {
  id: 'workspace',
  title: 'Edytor projektów',
  icon: 'folder',
  group: 'Kod',
  order: 0,
  layout: 'full',
  description: 'Twoje zadania i projekty jako prawdziwe pliki: drzewo, karty, uruchamianie z wejściem, testy z folderu testy/ i wszystkie narzędzia przy kodzie.',
  keywords: ['edytor', 'projekty', 'pliki', 'ide', 'vs code', 'onlinegdb', 'zadanie', 'workspace', 'folder', 'testy', 'kod'],

  render(root, ctx) {
    this._ws = buildWorkspace(root, ctx);
  },
  onShow(params) { return this._ws?.onShow(params || {}); },
  onHide() { this._ws?.onHide(); },
};

function buildWorkspace(root, ctx) {
  root.classList.add('ws-root');
  const M = modKey();
  const S = Object.assign({ open: [], active: null, sideW: 260, toolsW: 400, bottomH: 270, sideHidden: false, toolsOpen: false,
    bottomHidden: false, preview: {} }, persist.get('state', {}) || {});
  const saveState = debounce(() => persist.set('state', S), 250);
  const files = new Map();             // path → {path, name, lang, doc, mtime, cleanGen, saving, pending, conflict, timer}
  let treeData = [];
  let rootPath = '';
  let visible = true;
  let resolveReady;
  const ready = new Promise((r) => { resolveReady = r; });

  // ================================================================ pasek narzędzi (jak onlinegdb)
  const runBtn = button('Uruchom', { icon: 'play', variant: 'success', class: 'ws-run', title: `Uruchom (${M}+Enter)`, onClick: () => run() });
  const debugBtn = button('Debuguj', { icon: 'footprints', size: 'sm', variant: 'ghost', title: 'Krok po kroku ze zmiennymi', onClick: () => debug() });
  const saveBtn = button('Zapisz', { icon: 'save', size: 'sm', variant: 'ghost', title: `Zapisz (${M}+S) — zapisuje się też samo`, onClick: () => saveActive(true) });
  const fmtBtn = button('Formatuj', { icon: 'align-left', size: 'sm', variant: 'ghost', title: 'Formatuj kod (Ctrl+Shift+F)', onClick: () => format() });
  const langSel = h('select', { class: 'select sm ws-lang', title: 'Język pliku (zmienia rozszerzenie)', onchange: () => changeLang(langSel.value) },
    h('option', { value: 'cpp' }, 'C++'), h('option', { value: 'python' }, 'Python'), h('option', { value: 'text' }, 'Tekst'));
  const testsBtn = button('Testy', { icon: 'list-checks', size: 'sm', variant: 'ghost', title: 'Uruchom na wszystkich testach z folderu testy/', onClick: () => bottom.runAll() });
  const toolsBtn = button('Narzędzia', { icon: 'blocks', size: 'sm', class: 'ws-tools-btn', title: 'Sprawdź błędy, złożoność, testerka, treść zadania…', onClick: () => toggleTools() });
  const sideBtn = iconButton('panel-left', { title: 'Pokaż/ukryj projekty (Ctrl+Shift+E)', onClick: () => toggleSide() });
  sideBtn.replaceChildren(xicon('panel-left', 16));
  const bottomBtn = iconButton('panel-bottom', { title: `Pokaż/ukryj wejście i wyjście (${M}+J)`, onClick: () => toggleBottom() });
  bottomBtn.replaceChildren(xicon('panel-bottom', 16));
  const quickBtn = iconButton('search', { title: `Szybkie otwieranie pliku (${M}+P)`, onClick: () => openQuick() });
  const top = h('div', { class: 'ws-top' },
    sideBtn, h('div', { class: 'ws-sep' }),
    runBtn, debugBtn, saveBtn, langSel, fmtBtn, testsBtn,
    h('div', { class: 'grow' }),
    quickBtn, bottomBtn, toolsBtn);

  // ================================================================ lewy panel: projekty
  const tree = createTree({
    onOpen: (p, o = {}) => openFile(p, o),
    onRenamed: (a, b) => handleRenamed(a, b),
    onDeleted: (p) => handleDeleted(p),
    onNewTask: (dir) => newTask(dir),
    onRunTests: async (p) => {
      let target = p;
      if (!tree.files().includes(p)) {
        let t = null;
        try { t = await ws.task(p); } catch { /* */ }
        if (!t?.solution) { toast('W tym folderze nie ma pliku rozw.cpp / rozw.py.', 'info'); return; }
        target = t.solution.path;
      }
      await openFile(target);
      bottom.runAll();
    },
    getActive: () => S.active,
    reload: () => reloadTree(),
    rootPath: () => rootPath,
  });
  const sideHead = h('div', { class: 'ws-side-head' },
    h('span', { class: 'ws-side-title' }, 'Projekty'),
    h('div', { class: 'grow' }),
    button('Zadanie', { icon: 'plus', size: 'sm', variant: 'secondary', class: 'ws-newtask', title: 'Nowe zadanie: folder z rozw.cpp, brute.cpp, gen.py, testy/, tresc.md', onClick: () => newTask('') }),
    iconBtn('file-plus', 'Nowy plik', () => tree.startCreate(currentDir(), 'file')),
    iconBtn('folder-plus', 'Nowy folder', () => tree.startCreate(currentDir(), 'dir')),
    iconBtn('more', 'Więcej', (e) => sideMenu(e)));
  const side = h('aside', { class: 'ws-side' }, sideHead, tree.el);

  function iconBtn(name, title, onClick) {
    const b = h('button', { type: 'button', class: 'btn ghost sm icon', title, onclick: onClick });
    b.append(xicon(name, 15));
    return b;
  }
  function currentDir() {
    if (!S.active) return '';
    return dirName(S.active);
  }
  function sideMenu(e) {
    const r = e.currentTarget.getBoundingClientRect();
    contextMenu(r.left, r.bottom + 4, [
      { label: 'Nowe zadanie…', icon: 'plus', onClick: () => newTask('') },
      { label: 'Nowy projekt (pusty folder)…', icon: 'folder-plus', onClick: () => newProject() },
      { label: 'Importuj z „Moich programów”…', icon: 'download', onClick: () => importProgram() },
      '-',
      { label: 'Odśwież', icon: 'refresh-cw', onClick: () => reloadTree(true) },
      { label: 'Zwiń wszystkie foldery', icon: 'chevrons-down-up', onClick: () => tree.collapseAll() },
      { label: 'Kosz…', icon: 'trash-2', onClick: () => showTrash() },
      { label: 'Kopiuj ścieżkę katalogu projektów', icon: 'clipboard-paste', onClick: async () => { try { await navigator.clipboard.writeText(rootPath); toast('Skopiowano: ' + rootPath, 'success'); } catch { toast(rootPath, 'info'); } } },
    ]);
  }

  // ================================================================ środek: karty + edytor
  const tabsEl = h('div', { class: 'ws-tabs', role: 'tablist' });
  const tabsRight = h('div', { class: 'ws-tabs-right' });
  const tabsBar = h('div', { class: 'ws-tabbar' }, tabsEl, tabsRight);
  const banner = h('div', { class: 'ws-banner', hidden: true });
  const edHost = h('div', { class: 'ws-edhost' });
  const welcome = h('div', { class: 'ws-welcome', hidden: true });
  const mdPreview = h('div', { class: 'ws-mdpreview md', hidden: true });
  const edArea = h('div', { class: 'ws-edarea' }, edHost, mdPreview, welcome);
  const bottomWrap = h('div', { class: 'ws-bottom' });
  const center = h('div', { class: 'ws-center' }, tabsBar, banner, edArea,
    dragHandle('y', { title: 'Przeciągnij, aby zmienić wysokość', onStart: () => { hStart = S.bottomH; }, onMove: (d) => setBottomH(hStart - d), onEnd: () => endResize() }),
    bottomWrap);
  let hStart = 0, wStart = 0;

  const toolsWrap = h('aside', { class: 'ws-tools' });
  const sideHandle = dragHandle('x', { title: 'Przeciągnij, aby zmienić szerokość', onStart: () => { wStart = S.sideW; }, onMove: (d) => setSideW(wStart + d), onEnd: () => endResize() });
  const toolsHandle = dragHandle('x', { title: 'Przeciągnij, aby zmienić szerokość', onStart: () => { wStart = S.toolsW; }, onMove: (d) => setToolsW(wStart - d), onEnd: () => endResize() });
  sideHandle.classList.add('ws-h-side');
  toolsHandle.classList.add('ws-h-tools');
  const mainRow = h('div', { class: 'ws-main' }, side, sideHandle, center, toolsHandle, toolsWrap);

  // ================================================================ pasek stanu
  const stLang = h('span', { class: 'ws-st-item' });
  const stPos = h('span', { class: 'ws-st-item mono' });
  const stSave = h('span', { class: 'ws-st-item ws-st-save' });
  const stPath = h('button', { type: 'button', class: 'ws-st-item ws-st-path', title: 'Kliknij, aby skopiować pełną ścieżkę', onclick: () => copyActivePath() });
  const status = h('div', { class: 'ws-status' }, stLang, stPos, stSave, h('div', { class: 'grow' }), stPath,
    h('span', { class: 'ws-st-item muted' }, 'UTF-8 · LF · 4 spacje'));

  const app_ = h('div', { class: 'ws-app' }, top, mainRow, status);
  root.append(app_);

  // ================================================================ edytor (jeden, dokumenty podmieniane)
  const ed = createEditor(edHost, {
    value: '', lang: 'cpp', height: '100%', personal: true, placeholder: 'Pisz kod…',
    onRun: () => run(), onSave: () => saveActive(true),
  });
  const emptyDoc = ed.cm.getDoc();
  ed.cm.on('changes', () => {
    const f = activeFile();
    if (!f || ed.cm.getDoc() !== f.doc) return;
    updateDirty(f);
    scheduleSave(f);
    if (!mdPreview.hidden) renderPreview();
  });
  ed.cm.on('cursorActivity', () => updatePos());
  ed.cm.on('scroll', debounce(() => rememberCursor(), 400));

  // ================================================================ moduły paneli
  const app = {
    ctx, ed,
    active: () => activeFile(),
    code: () => (activeFile() ? activeFile().doc.getValue() : ''),
    setCode: (text) => setCode(text),
    openFile: (p, o) => openFile(p, o),
    goto: (p, line) => openFile(p, { line }),
    saveAll: () => saveAll(),
    reloadTree: () => reloadTree(),
    run: () => run(),
    runTarget: () => runTarget(),
    toggleBottom: (v) => toggleBottom(v),
    toggleTools: (v) => toggleTools(v),
    dropFile: (p) => dropFile(p),
    get bottom() { return bottom; },
  };
  const bottom = createBottom(app);
  bottomWrap.append(bottom.el);
  const tools = createTools(app);
  toolsWrap.append(tools.el);

  applyLayout();
  renderTabs();
  updateStatus();
  load();

  // ================================================================ ładowanie
  async function load() {
    try {
      await reloadTree();
      const exist = new Set(tree.files());
      const opens = (S.open || []).filter((p) => exist.has(p));
      S.open = [];
      for (const p of opens) {
        try { await loadFile(p); S.open.push(p); } catch { /* plik zniknął */ }
      }
      const act = S.active && files.has(S.active) ? S.active : S.open[S.open.length - 1];
      if (act) activate(act, { focus: false }); else showWelcome();
      if (!S.open.length && treeData.length && !persist.get('opened-once')) {
        // pierwsze uruchomienie: otwórz przykładowy projekt, żeby było widać, jak to działa
        const ex = tree.files().find((p) => /rozw\.cpp$/.test(p) && p.startsWith('Przykład'));
        if (ex) { persist.set('opened-once', true); await openFile(ex, { focus: false }); }
      }
    } finally {
      resolveReady();
    }
  }

  async function reloadTree(showToast = false) {
    try {
      const r = await ws.tree();
      treeData = r.tree || [];
      rootPath = r.root || rootPath;
      tree.setData(treeData);
      if (showToast) toast('Odświeżono', 'success', { timeout: 900 });
      if (!S.open.length) renderWelcome();
    } catch (e) {
      toast('Nie udało się wczytać projektów: ' + e.message, 'error');
    }
  }

  // ================================================================ pliki i karty
  function activeFile() { return S.active ? files.get(S.active) || null : null; }

  async function loadFile(path) {
    if (files.has(path)) return files.get(path);
    const r = await ws.file(path, { quiet: true });
    if (r.binary) throw Object.assign(new Error('binary'), { binary: true });
    const lang = langOf(r.name || path);
    const doc = new window.CodeMirror.Doc(r.content, cmMode(edLang(lang)));
    const f = { path, name: baseName(path), lang, doc, mtime: r.mtime, cleanGen: doc.changeGeneration(true), saving: false, pending: false, conflict: null, timer: null };
    const cur = (persist.get('cursors', {}) || {})[path];
    if (cur) { try { doc.setCursor({ line: cur.line, ch: cur.ch }); f.scrollTop = cur.top || 0; } catch { /* */ } }
    files.set(path, f);
    return f;
  }

  async function openFile(path, { line, focus = true } = {}) {
    if (!path) return;
    let f;
    try { f = await loadFile(path); } catch (e) {
      if (e.binary) toast(`„${baseName(path)}” to plik binarny — nie da się go edytować.`, 'warn');
      else toast(`Nie udało się otworzyć „${baseName(path)}”: ${e.message}`, 'error');
      return;
    }
    if (!S.open.includes(path)) {
      const i = S.open.indexOf(S.active);
      S.open.splice(i >= 0 ? i + 1 : S.open.length, 0, path);
    }
    activate(path, { focus });
    if (line) requestAnimationFrame(() => ed.scrollToLine(line));
    return f;
  }

  function activate(path, { focus = true } = {}) {
    const f = files.get(path);
    if (!f) return;
    rememberCursor();
    const prev = activeFile();
    if (prev && prev !== f) ed.clearMarks();
    S.active = path;
    if (ed.cm.getDoc() !== f.doc) {
      ed.cm.swapDoc(f.doc);
      if (f.scrollTop) { ed.cm.scrollTo(null, f.scrollTop); f.scrollTop = 0; }
    }
    ed.lang = edLang(f.lang);
    ed.setLang(edLang(f.lang));
    welcome.hidden = true;
    edHost.hidden = false;
    const recent = (persist.get('recent', []) || []).filter((p) => p !== path);
    recent.unshift(path);
    persist.set('recent', recent.slice(0, 12));
    saveState();
    renderTabs();
    updateStatus();
    tree.reveal(path);
    bottom.onFileChange(f);
    tools.onFileChange(f);
    applyPreview();
    requestAnimationFrame(() => { ed.refresh(); if (focus) ed.focus(); });
    checkDisk(f);
  }

  function renderTabs() {
    clear(tabsEl);
    for (const p of S.open) {
      const f = files.get(p);
      if (!f) continue;
      const dirty = isDirty(f);
      const dup = S.open.filter((q) => baseName(q) === f.name).length > 1;
      const t = h('div', {
        class: ['ws-tab', p === S.active ? 'active' : '', dirty ? 'dirty' : '', f.conflict ? 'conflict' : ''].join(' '), role: 'tab', title: p, draggable: 'true',
        onclick: () => activate(p),
        onauxclick: (e) => { if (e.button === 1) { e.preventDefault(); closeTab(p); } },
        onmousedown: (e) => { if (e.button === 1) e.preventDefault(); },
        oncontextmenu: (e) => { e.preventDefault(); tabMenu(p, e.clientX, e.clientY); },
      }, fileIcon(f.name, { size: 14 }), h('span', { class: 'ws-tab-name' }, f.name),
      dup ? h('span', { class: 'ws-tab-dir' }, baseName(dirName(p)) || '/') : null,
      h('button', { type: 'button', class: 'ws-tab-x', title: dirty ? 'Niezapisane zmiany — zamknij' : `Zamknij (${isMac() ? '⌘W / ⌥W' : 'Ctrl+W / Alt+W'})`,
        onclick: (e) => { e.stopPropagation(); closeTab(p); } }, h('span', { class: 'ws-dot' }), icon('x', 13)));
      t.addEventListener('dragstart', (e) => { e.dataTransfer.setData('application/x-oif-tab', p); e.dataTransfer.effectAllowed = 'move'; });
      t.addEventListener('dragover', (e) => { if ([...e.dataTransfer.types].includes('application/x-oif-tab')) { e.preventDefault(); t.classList.add('drop'); } });
      t.addEventListener('dragleave', () => t.classList.remove('drop'));
      t.addEventListener('drop', (e) => {
        t.classList.remove('drop');
        const src = e.dataTransfer.getData('application/x-oif-tab');
        if (!src || src === p) return;
        e.preventDefault();
        S.open.splice(S.open.indexOf(src), 1);
        S.open.splice(S.open.indexOf(p) + (e.offsetX > t.offsetWidth / 2 ? 1 : 0), 0, src);
        saveState(); renderTabs();
      });
      tabsEl.append(t);
    }
    tabsEl.querySelector('.ws-tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    // prawa strona paska kart: podgląd Markdown
    clear(tabsRight);
    const f = activeFile();
    if (f && f.lang === 'markdown') {
      const on = !!S.preview[f.path];
      tabsRight.append(h('div', { class: 'segmented ws-seg' },
        h('button', { type: 'button', class: on ? '' : 'active', onclick: () => setPreview(false) }, 'Kod'),
        h('button', { type: 'button', class: on ? 'active' : '', onclick: () => setPreview(true) }, 'Podgląd')));
    }
  }

  function tabMenu(p, x, y) {
    contextMenu(x, y, [
      { label: 'Zamknij', icon: 'x', onClick: () => closeTab(p) },
      { label: 'Zamknij pozostałe', onClick: () => S.open.filter((q) => q !== p).forEach((q) => closeTab(q)) },
      { label: 'Zamknij wszystkie', onClick: () => [...S.open].forEach((q) => closeTab(q)) },
      '-',
      { label: 'Pokaż w drzewie', icon: 'list-tree', onClick: () => { toggleSide(true); tree.select(p); } },
      { label: 'Zmień nazwę', icon: 'pencil', onClick: () => { toggleSide(true); tree.startRename(p); } },
      { label: 'Kopiuj ścieżkę', icon: 'clipboard-paste', onClick: () => copyPath(p) },
    ]);
  }

  async function closeTab(p) {
    const f = files.get(p);
    if (f) {
      if (isDirty(f) && !f.conflict) await save(f);
      if (isDirty(f)) {
        const ok = await confirmDialog(`Plik „${f.name}” ma niezapisane zmiany (${f.conflict ? 'konflikt z wersją na dysku' : 'zapis się nie udał'}). Zamknąć mimo to?`,
          { title: 'Niezapisane zmiany', ok: 'Zamknij bez zapisu', danger: true });
        if (!ok) return;
      }
      clearTimeout(f.timer);
    }
    if (S.active === p) rememberCursor();
    const i = S.open.indexOf(p);
    if (i >= 0) S.open.splice(i, 1);
    files.delete(p);
    if (S.active === p) {
      const next = S.open[Math.min(i, S.open.length - 1)];
      if (next) activate(next); else { S.active = null; showWelcome(); }
    }
    saveState();
    renderTabs();
  }

  function dropFile(p) {
    // plik zmieniony poza edytorem (np. nadpisany przez narzędzie) — wczytaj od nowa przy następnym otwarciu
    const f = files.get(p);
    if (!f) return;
    clearTimeout(f.timer);
    files.delete(p);
    const wasActive = S.active === p;
    if (wasActive) { S.active = null; openFile(p); }
  }

  function showWelcome() {
    S.active = null;
    ed.clearMarks();
    ed.cm.swapDoc(emptyDoc);
    edHost.hidden = true;
    mdPreview.hidden = true;
    welcome.hidden = false;
    banner.hidden = true;
    renderWelcome();
    renderTabs();
    updateStatus();
    tree.render();
    bottom.onFileChange(null);
    tools.onFileChange(null);
    saveState();
  }

  function renderWelcome() {
    if (welcome.hidden) return;
    const recent = (persist.get('recent', []) || []).filter((p) => tree.files().includes(p)).slice(0, 6);
    const sc = (k, label) => h('div', { class: 'ws-sc' }, kbd(k), h('span', null, label));
    welcome.replaceChildren(h('div', { class: 'ws-wel' },
      h('div', { class: 'ws-wel-logo' }, xicon('folder-open', 30)),
      h('h2', null, 'Edytor projektów'),
      h('p', { class: 'muted' }, 'Każde zadanie to folder z prawdziwymi plikami — możesz je otworzyć także w VS Code.'),
      h('div', { class: 'ws-wel-actions' },
        bigAction('plus', 'Nowe zadanie', 'rozw.cpp, brute, generator, testy, treść', () => newTask(''), true),
        bigAction('folder-plus', 'Nowy projekt', 'pusty folder na własne pliki', () => newProject()),
        bigAction('search', 'Otwórz plik', `${M}+P — szybkie wyszukiwanie`, () => openQuick()),
        bigAction('download', 'Z „Moich programów”', 'zaimportuj swoje rozwiązanie', () => importProgram())),
      recent.length ? h('div', { class: 'ws-wel-sec' }, h('div', { class: 'ws-wel-title' }, 'Ostatnio otwierane'),
        h('div', { class: 'ws-wel-recent' }, recent.map((p) => h('button', { type: 'button', class: 'ws-wel-file', onclick: () => openFile(p) },
          fileIcon(baseName(p), { size: 15 }), h('span', { class: 'ws-name' }, baseName(p)), h('span', { class: 'muted tiny ellipsis' }, dirName(p)))))) : null,
      h('div', { class: 'ws-wel-sec' }, h('div', { class: 'ws-wel-title' }, 'Skróty'),
        h('div', { class: 'ws-wel-sc' },
          sc(`${M}+Enter`, 'uruchom'), sc(`${M}+S`, 'zapisz'), sc(`${M}+P`, 'otwórz plik'), sc(isMac() ? '⌥W' : 'Alt+W', 'zamknij kartę'),
          sc(`${M}+J`, 'wejście / wyjście'), sc('Ctrl+Shift+F', 'formatuj'), sc(`${M}+/`, 'komentarz'), sc('Tab', 'snippet (np. seg)'),
          sc('F2', 'zmień nazwę (w drzewie)'), sc('Ctrl+Space', 'podpowiedzi'))),
      rootPath ? h('div', { class: 'ws-wel-path muted tiny' }, icon('folder', 12), ' ', rootPath) : null));
  }
  function bigAction(ic, title, sub, onClick, primary) {
    const i = xicon(ic, 20);
    return h('button', { type: 'button', class: ['ws-big-act', primary ? 'primary' : ''].join(' '), onclick: onClick },
      h('span', { class: 'ws-big-ic' }, i), h('span', { class: 'ws-big-t' }, title), h('span', { class: 'ws-big-s' }, sub));
  }

  // ================================================================ zapis
  function isDirty(f) { return !f.doc.isClean(f.cleanGen); }
  function updateDirty(f) {
    const tab = [...tabsEl.children].find((t) => t.title === f.path);
    if (tab) tab.classList.toggle('dirty', isDirty(f));
    if (f === activeFile()) updateSaveStatus();
  }
  function scheduleSave(f) {
    clearTimeout(f.timer);
    if (f.conflict) return;
    f.timer = setTimeout(() => save(f), 800);
  }
  async function save(f, { force = false, manual = false } = {}) {
    clearTimeout(f.timer);
    if (f.saving) { f.pending = true; return; }
    if (!isDirty(f) && !force) { if (manual) flashSaved(); return; }
    if (f.conflict && !force) return;
    f.saving = true;
    updateSaveStatus();
    const gen = f.doc.changeGeneration(true);   // true: następne pisanie = nowe zdarzenie historii (nowa generacja)
    const content = f.doc.getValue();
    try {
      const r = await ws.save({ path: f.path, content, mtime: f.mtime, force });
      if (r.conflict) {
        f.conflict = r;
        showBanner(f);
      } else {
        f.mtime = r.mtime;
        f.cleanGen = gen;
        f.conflict = null;
        f.error = null;
        if (f === activeFile()) banner.hidden = true;
        if (manual) flashSaved();
      }
    } catch (e) {
      f.error = e.message;
      if (manual || !f.errorShown) { toast(`Nie udało się zapisać „${f.name}”: ${e.message}`, 'error'); f.errorShown = true; }
    } finally {
      f.saving = false;
      updateDirty(f);
      renderTabs();
      if (f.pending) { f.pending = false; save(f); }
    }
  }
  async function saveAll() {
    await Promise.all([...files.values()].filter((f) => isDirty(f) && !f.conflict).map((f) => save(f)));
  }
  function saveActive(manual) { const f = activeFile(); if (f) return save(f, { manual }); }
  function flashSaved() { stSave.classList.add('flash'); setTimeout(() => stSave.classList.remove('flash'), 700); }

  function showBanner(f) {
    if (f !== activeFile()) { renderTabs(); return; }
    const c = f.conflict;
    banner.hidden = false;
    banner.replaceChildren(icon('triangle-alert', 16),
      h('div', { class: 'grow' }, h('b', null, c.deleted ? 'Plik zniknął z dysku. ' : 'Plik zmienił się na dysku. '),
        h('span', null, c.deleted ? 'Ktoś (albo inny program) go usunął lub przeniósł.' : 'Zmieniono go poza edytorem, a Ty też masz zmiany. Autozapis wstrzymany.')),
      c.deleted ? null : button('Porównaj', { size: 'sm', variant: 'ghost', icon: 'git-compare', onClick: () => compareDisk(f) }),
      c.deleted ? null : button('Wczytaj z dysku', { size: 'sm', icon: 'download', onClick: () => takeDisk(f) }),
      button(c.deleted ? 'Zapisz ponownie' : 'Nadpisz moją wersją', { size: 'sm', variant: 'primary', icon: 'save', onClick: async () => { f.conflict = null; await save(f, { force: true }); } }));
  }
  function takeDisk(f) {
    const c = f.conflict;
    if (!c || c.disk_content === null || c.disk_content === undefined) return;
    replaceDocKeepCursor(f, c.disk_content);
    f.mtime = c.mtime;
    f.cleanGen = f.doc.changeGeneration(true);
    f.conflict = null;
    banner.hidden = true;
    updateDirty(f);
    renderTabs();
    toast('Wczytano wersję z dysku (Ctrl+Z przywraca Twoją).', 'info');
  }
  function compareDisk(f) {
    const c = f.conflict;
    modal({ title: `Porównanie: ${f.name}`, width: 1000, icon: 'git-compare',
      body: h('div', { class: 'ws-two ws-compare' },
        h('div', null, h('div', { class: 'ws-td-label' }, 'Twoja wersja (edytor)'), codeBlock(f.doc.getValue(), edLang(f.lang), { maxHeight: 520, lineNumbers: true })),
        h('div', null, h('div', { class: 'ws-td-label' }, 'Wersja na dysku'), codeBlock(c.disk_content || '', edLang(f.lang), { maxHeight: 520, lineNumbers: true }))) });
  }
  function replaceDocKeepCursor(f, text) {
    const cur = f.doc.getCursor();
    const last = f.doc.lastLine();
    f.doc.replaceRange(text, { line: 0, ch: 0 }, { line: last, ch: f.doc.getLine(last).length }, 'disk');
    try { f.doc.setCursor(cur); } catch { /* */ }
  }
  function setCode(text) {
    const f = activeFile();
    if (!f) return;
    replaceDocKeepCursor(f, text);
  }
  async function checkDisk(f) {
    if (!f || isDirty(f) || f.saving || f.conflict) return;
    try {
      const r = await ws.file(f.path, { quiet: true });
      if (r.mtime !== f.mtime && !isDirty(f) && r.content !== f.doc.getValue()) {
        replaceDocKeepCursor(f, r.content);
        f.cleanGen = f.doc.changeGeneration(true);
        toast(`„${f.name}” zmienił się na dysku — wczytano nową wersję.`, 'info', { timeout: 2500 });
      }
      f.mtime = r.mtime;
      updateDirty(f);
    } catch (e) {
      if (e.status === 404) { f.conflict = { deleted: true }; showBanner(f); }
    }
  }

  // ================================================================ zmiana nazwy / usuwanie z zewnątrz drzewa
  function handleRenamed(oldP, newP) {
    tree.renamePrefix(oldP, newP);
    const moved = [];
    for (const p of [...S.open]) {
      if (p === oldP || p.startsWith(oldP + '/')) {
        const np = newP + p.slice(oldP.length);
        const f = files.get(p);
        if (f) {
          files.delete(p);
          f.path = np; f.name = baseName(np);
          const nl = langOf(f.name);
          if (nl !== f.lang) { f.lang = nl; if (S.active === p) ed.setLang(edLang(nl)); }
          files.set(np, f);
        }
        S.open[S.open.indexOf(p)] = np;
        if (S.active === p) S.active = np;
        moved.push([p, np]);
      }
    }
    const rec = (persist.get('recent', []) || []).map((p) => (p === oldP || p.startsWith(oldP + '/') ? newP + p.slice(oldP.length) : p));
    persist.set('recent', rec);
    saveState();
    renderTabs();
    updateStatus();
    if (moved.length && activeFile()) { ed.setLang(edLang(activeFile().lang)); tools.onFileChange(activeFile()); bottom.onFileChange(activeFile()); }
  }
  function handleDeleted(p) {
    for (const q of [...S.open]) {
      if (q === p || q.startsWith(p + '/')) {
        const f = files.get(q);
        if (f) { clearTimeout(f.timer); f.conflict = { deleted: true }; }
        const i = S.open.indexOf(q);
        S.open.splice(i, 1);
        files.delete(q);
        if (S.active === q) S.active = null;
      }
    }
    if (!S.active) { if (S.open.length) activate(S.open[S.open.length - 1]); else showWelcome(); }
    saveState();
    renderTabs();
  }

  // ================================================================ akcje projektu
  async function newTask(parent) {
    const r = await newTaskDialog();
    if (!r) return;
    const res = await ws.newTask({ name: r.name, statement: r.statement, parent: parent || undefined });
    await reloadTree();
    tree.select(res.path);
    await openFile(res.open);
    toast(res.samples ? `Utworzono zadanie „${res.title}” (${res.samples} ${res.samples === 1 ? 'test' : 'testy'} z przykładów).` : `Utworzono zadanie „${res.title}”.`, 'success');
  }
  async function newProject() {
    const r = await newTaskDialog({ title: 'Nowy projekt', withStatement: false });
    if (!r) return;
    const res = await ws.newProject({ name: r.name });
    await reloadTree();
    toggleSide(true);
    tree.select(res.path);
    tree.startCreate(res.path, 'file');
  }
  async function importProgram(id) {
    const pid = id || await pickProgram();
    if (!pid) return;
    const res = await ws.importProgram(pid);
    await reloadTree();
    tree.select(res.path);
    await openFile(res.open);
    toast(`Zaimportowano „${res.title}”.`, 'success');
  }
  async function showTrash() {
    let items = [];
    try { items = (await ws.trash()).items || []; } catch { /* */ }
    const list = h('div', { class: 'ws-pick-list' });
    const fill = () => {
      clear(list);
      if (!items.length) { list.append(h('div', { class: 'muted small pad' }, 'Kosz jest pusty. Usunięte pliki trzymamy tu przez 30 dni.')); return; }
      for (const it of items) {
        list.append(h('div', { class: 'ws-pick-item' }, fileIcon(it.name, { dir: it.kind === 'dir' }),
          h('div', { class: 'grow' }, h('div', { class: 'bold' }, it.name), h('div', { class: 'muted tiny' }, `${it.path} · usunięto ${it.deleted_at}`)),
          button('Przywróć', { size: 'sm', icon: 'rotate-ccw', onClick: async () => {
            const r = await ws.restore(it.id);
            items = items.filter((x) => x.id !== it.id); fill();
            await reloadTree(); tree.select(r.path);
            toast(`Przywrócono ${r.path}`, 'success', { timeout: 1600 });
          } })));
      }
    };
    fill();
    const m = modal({ title: 'Kosz', icon: 'trash-2', width: 620, body: list,
      actions: [button('Opróżnij kosz', { variant: 'danger', icon: 'trash-2', onClick: async () => {
        if (!items.length) return;
        if (!(await confirmDialog('Trwale usunąć wszystko z kosza? Tego nie da się cofnąć.', { title: 'Opróżnij kosz', ok: 'Usuń na zawsze', danger: true }))) return;
        await api.post('/api/ws/trash/empty', {}); items = []; fill();
      } }), button('Zamknij', { variant: 'ghost', onClick: () => m.close() })] });
  }
  function openQuick() {
    quickOpen(tree.files(), { recent: persist.get('recent', []) || [], onPick: (p) => openFile(p) });
  }
  async function copyPath(p) {
    const full = rootPath ? `${rootPath}/${p}` : p;
    try { await navigator.clipboard.writeText(full); toast('Skopiowano ścieżkę', 'success', { timeout: 1200 }); } catch { toast(full, 'info'); }
  }
  function copyActivePath() { if (S.active) copyPath(S.active); }

  // ================================================================ uruchamianie, debug, formatowanie, język
  async function runTarget() {
    const f = activeFile();
    if (!f) { toast('Otwórz plik z programem (C++ albo Python).', 'info'); return null; }
    if (isRunnable(f.name)) return { path: f.path, name: f.name, lang: f.lang, code: f.doc.getValue() };
    // .in / .md / inne: uruchom rozwiązanie zadania
    let t = null;
    try { t = await ws.task(f.path, true); } catch { /* */ }
    if (t?.solution) {
      const open = files.get(t.solution.path);
      return { path: t.solution.path, name: baseName(t.solution.path), lang: t.solution.lang, code: open ? open.doc.getValue() : (t.solution.content || '') };
    }
    toast('Ten plik nie jest programem — otwórz plik .cpp albo .py.', 'info');
    return null;
  }
  async function run() {
    const target = await runTarget();
    if (!target) return;
    await saveAll();
    runBtn.classList.add('busy');
    try { await bottom.run(target); } finally { runBtn.classList.remove('busy'); }
  }
  async function debug() {
    const target = await runTarget();
    if (!target) return;
    await saveAll();
    ctx.navigate('debugger', { code: target.code, lang: target.lang, input: bottom.input || '', run: true });
  }
  function format() {
    const f = activeFile();
    if (!f) return;
    if (f.lang !== 'cpp') { toast('Formatowanie działa dla plików C++.', 'info'); return; }
    ed.format();
  }
  async function changeLang(l) {
    const f = activeFile();
    if (!f) { langSel.value = 'cpp'; return; }
    const cur = edLang(f.lang);
    if (l === cur) return;
    const stem = f.name.includes('.') ? f.name.slice(0, f.name.lastIndexOf('.')) : f.name;
    const newName = stem + EXT_FOR[l];
    const ok = await confirmDialog(`Zmienić język pliku na ${LANG_LABEL[l]}? Plik „${f.name}” zostanie przemianowany na „${newName}”.`, { title: 'Zmiana języka', ok: 'Zmień' });
    if (!ok) { langSel.value = cur; return; }
    await save(f);
    try {
      const r = await ws.rename({ path: f.path, new_name: newName });
      await reloadTree();
      handleRenamed(f.path, r.path);
    } catch { langSel.value = cur; }
  }

  // ================================================================ podgląd Markdown
  function setPreview(on) {
    const f = activeFile();
    if (!f) return;
    if (on) S.preview[f.path] = true; else delete S.preview[f.path];
    saveState();
    applyPreview();
    renderTabs();
  }
  function applyPreview() {
    const f = activeFile();
    const on = !!(f && f.lang === 'markdown' && S.preview[f.path]);
    mdPreview.hidden = !on;
    edHost.hidden = !f || on;
    if (on) renderPreview();
  }
  function renderPreview() {
    const f = activeFile();
    if (!f) return;
    mdPreview.replaceChildren(h('div', { class: 'ws-md-inner' }, markdown(f.doc.getValue())));
  }

  // ================================================================ stan i układ
  function updateStatus() {
    const f = activeFile();
    stLang.replaceChildren(f ? [fileIcon(f.name, { size: 13 }), ' ', LANG_LABEL[f.lang] || 'Tekst'] : '');
    stPath.textContent = f ? f.path : '';
    langSel.value = f ? edLang(f.lang) : 'cpp';
    langSel.disabled = !f;
    const runnable = !!f;
    runBtn.disabled = !runnable;
    debugBtn.disabled = !f || !isRunnable(f.name);
    fmtBtn.disabled = !f || f.lang !== 'cpp';
    saveBtn.disabled = !f;
    testsBtn.disabled = !f;
    updatePos();
    updateSaveStatus();
  }
  function updatePos() {
    const f = activeFile();
    if (!f) { stPos.textContent = ''; return; }
    const c = ed.cm.getCursor();
    const sel = ed.cm.getSelection();
    stPos.textContent = `Wiersz ${c.line + 1}, kol. ${c.ch + 1}` + (sel ? ` (zazn. ${sel.length})` : '');
  }
  function updateSaveStatus() {
    const f = activeFile();
    stSave.className = 'ws-st-item ws-st-save';
    if (!f) { stSave.textContent = ''; return; }
    if (f.conflict) { stSave.classList.add('bad'); stSave.replaceChildren(icon('triangle-alert', 12), ' Konflikt — nie zapisano'); }
    else if (f.saving) { stSave.classList.add('busy'); stSave.replaceChildren(icon('loader', 12), ' Zapisywanie…'); }
    else if (f.error && isDirty(f)) { stSave.classList.add('bad'); stSave.replaceChildren(icon('circle-alert', 12), ' Błąd zapisu'); }
    else if (isDirty(f)) { stSave.classList.add('dirty'); stSave.replaceChildren('● Niezapisane'); }
    else { stSave.classList.add('ok'); stSave.replaceChildren(icon('check', 12), ' Zapisano'); }
  }
  function rememberCursor() {
    const f = activeFile();
    if (!f || ed.cm.getDoc() !== f.doc) return;
    const c = f.doc.getCursor();
    const all = persist.get('cursors', {}) || {};
    all[f.path] = { line: c.line, ch: c.ch, top: ed.cm.getScrollInfo().top };
    const keys = Object.keys(all);
    if (keys.length > 60) delete all[keys[0]];
    persist.set('cursors', all);
  }

  function applyLayout() {
    app_.style.setProperty('--ws-side-w', `${S.sideW}px`);
    app_.style.setProperty('--ws-tools-w', `${S.toolsW}px`);
    app_.style.setProperty('--ws-bottom-h', `${S.bottomH}px`);
    app_.classList.toggle('no-side', !!S.sideHidden);
    app_.classList.toggle('tools-open', !!S.toolsOpen);
    app_.classList.toggle('no-bottom', !!S.bottomHidden);
    sideBtn.classList.toggle('on', !S.sideHidden);
    bottomBtn.classList.toggle('on', !S.bottomHidden);
    toolsBtn.classList.toggle('on', !!S.toolsOpen);
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function setSideW(w) { S.sideW = clamp(Math.round(w), 180, 520); applyLayout(); }
  function setToolsW(w) { S.toolsW = clamp(Math.round(w), 300, Math.min(760, window.innerWidth * 0.6)); applyLayout(); }
  function setBottomH(hh) { S.bottomH = clamp(Math.round(hh), 110, Math.max(160, center.clientHeight - 140)); applyLayout(); }
  function endResize() { saveState(); ed.refresh(); }
  function toggleSide(v) { S.sideHidden = v === undefined ? !S.sideHidden : !v; applyLayout(); saveState(); requestAnimationFrame(() => ed.refresh()); }
  function toggleTools(v) { S.toolsOpen = v === undefined ? !S.toolsOpen : !!v; applyLayout(); saveState(); requestAnimationFrame(() => ed.refresh()); }
  function toggleBottom(v) { S.bottomHidden = v === undefined ? !S.bottomHidden : !v; applyLayout(); saveState(); requestAnimationFrame(() => ed.refresh()); }

  // ================================================================ skróty klawiszowe (tylko gdy widok jest widoczny)
  document.addEventListener('keydown', (e) => {
    if (!visible || root.hidden || document.querySelector('.backdrop')) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key.toLowerCase();
    if (mod && k === 's' && !e.shiftKey) { if (!e.defaultPrevented) { e.preventDefault(); saveActive(true); } }
    else if (mod && e.key === 'Enter') { if (!e.defaultPrevented) { e.preventDefault(); run(); } }
    else if (mod && k === 'p' && !e.shiftKey) { e.preventDefault(); openQuick(); }
    else if ((mod && k === 'w' && !e.shiftKey) || (e.altKey && e.code === 'KeyW')) { if (S.active) { e.preventDefault(); closeTab(S.active); } }
    else if (mod && k === 'j') { e.preventDefault(); toggleBottom(); }
    else if (mod && e.shiftKey && k === 'e') { e.preventDefault(); toggleSide(); }
  });

  // zapisz przy opuszczaniu strony (keepalive — przeglądarka dokończy żądanie)
  window.addEventListener('beforeunload', () => {
    rememberCursor();
    for (const f of files.values()) {
      if (!isDirty(f) || f.conflict) continue;
      try {
        fetch('/api/ws/save', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-OIForge': '1' },
          body: JSON.stringify({ path: f.path, content: f.doc.getValue(), mtime: f.mtime }) });
      } catch { /* */ }
    }
  });
  window.addEventListener('focus', () => { if (visible && !root.hidden) { reloadTree(); checkDisk(activeFile()); } });
  window.addEventListener('resize', debounce(() => { if (S.bottomH > center.clientHeight - 140) setBottomH(S.bottomH); }, 200));

  // ================================================================ API widoku
  return {
    async onShow(params) {
      visible = true;
      await ready;
      requestAnimationFrame(() => ed.refresh());
      try {
        if (params.path) { await reloadTree(); await openFile(params.path, { line: params.line ? +params.line : undefined }); }
        else if (params.program_id) await importProgram(params.program_id);
        else if (params.statement) {
          const res = await ws.newTask({ name: params.name || '', statement: params.statement });
          // kod trafia do właściwego pliku zadania: rozwiązanie → rozw.cpp, brute → brute.cpp, generator → gen.cpp/gen.py
          let open = res.open;
          if (params.code) {
            const py = /^\s*(def |import |from \w+ import |print\()/m.test(params.code) && !/#include/.test(params.code);
            if (params.target === 'brute') open = joinPath(res.path, 'brute.cpp');
            else if (params.target === 'gen') open = joinPath(res.path, py ? 'gen.py' : 'gen.cpp');
            await ws.save({ path: open, content: params.code, force: true });
          }
          for (const t of params.tests || []) {
            try { await ws.saveTest({ dir: res.path, input: t.input, output: t.output ?? null }); } catch (e) { console.error(e); }
          }
          await reloadTree(); tree.select(res.path); await openFile(open);
          const extra = (params.tests || []).length ? ` (+${params.tests.length} ${params.tests.length === 1 ? 'test' : 'testów'} w testy/)` : '';
          toast(`Utworzono zadanie „${res.title}”${extra}. ${modKey()}+Enter — uruchom.`, 'success');
        } else if (params.code) {
          const lang = params.lang || (/^\s*(#include|using namespace|int main)/m.test(params.code) ? 'cpp' : (/^\s*(def |import |print\()/m.test(params.code) ? 'python' : 'cpp'));
          let name = params.name || `kod${lang === 'python' ? '.py' : '.cpp'}`;
          if (!/\.[a-z0-9+]+$/i.test(name)) name += lang === 'python' ? '.py' : '.cpp';
          const r = await ws.create({ path: joinPath(params.dir || 'Szkice', name), kind: 'file', content: params.code, unique: true });
          await reloadTree(); await openFile(r.path);
          actionToast(`Zapisano kod jako ${r.path}.`, 'Cofnij', async () => { await closeTab(r.path); await ws.del(r.path); await reloadTree(); }, { tone: 'success' });
        } else {
          reloadTree();
          checkDisk(activeFile());
        }
      } catch (e) { console.error(e); }
    },
    onHide() {
      visible = false;
      rememberCursor();
      saveAll();
    },
  };
}

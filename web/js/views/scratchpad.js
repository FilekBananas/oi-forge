// (t) Brudnopis: wiele kart roboczych do szybkich eksperymentów — bez plików i projektów.
import { h, icon, button, iconButton, viewHeader, split, outputPanel, textarea, clear, debounce, toast, confirmDialog, promptDialog, select } from '../ui.js';
import { api, store, local } from '../api.js';
import { createEditor } from '../editor.js';

const TEMPLATE_CPP = `#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);

    int n;
    cin >> n;
    cout << n * 2 << '\\n';
    return 0;
}
`;
const TEMPLATE_PY = `n = int(input())
print(n * 2)
`;

let state = { tabs: [], active: null };
let saveRemote = null;

function newTab(name, lang = 'cpp') {
  return { id: Math.random().toString(36).slice(2, 9), name, lang, code: lang === 'cpp' ? TEMPLATE_CPP : TEMPLATE_PY, input: '5\n', created: Date.now() };
}

export default {
  id: 'scratchpad', title: 'Brudnopis', icon: 'layers', group: 'Kod', order: 2, layout: 'full',
  description: 'Kilka kart roboczych do szybkiego eksperymentowania w C++ i Pythonie — bez tworzenia plików.',
  keywords: ['scratchpad', 'notatnik', 'karty', 'eksperyment', 'brudnopis'],
  async render(root, ctx) {
    const tabBar = h('div', { class: 'scratch-tabs grow' });
    const body = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column' });
    root.append(viewHeader(this,
      button('Nowa karta C++', { icon: 'plus', onClick: () => addTab('cpp') }),
      button('Python', { icon: 'plus', variant: 'ghost', onClick: () => addTab('python') })),
    h('div', { class: 'row nowrap', style: 'margin-bottom:10px' }, tabBar), body);

    const remote = await store.get('scratchpads', null);
    state = remote && remote.tabs && remote.tabs.length ? remote : (local.get('scratchpads') || { tabs: [newTab('Karta 1')], active: null });
    if (!state.tabs.length) state.tabs.push(newTab('Karta 1'));
    if (!state.active || !state.tabs.find((t) => t.id === state.active)) state.active = state.tabs[0].id;
    saveRemote = debounce(() => store.set('scratchpads', state), 800);
    const persist = () => { local.set('scratchpads', state); saveRemote(); };

    let ed = null; let inputEl = null; let out = null;
    const tabById = (id) => state.tabs.find((t) => t.id === id);

    function renderTabs() {
      clear(tabBar);
      for (const t of state.tabs) {
        const el = h('div', { class: ['scratch-tab', t.id === state.active ? 'active' : ''].join(' '), title: 'Kliknij dwukrotnie, aby zmienić nazwę',
          onclick: () => { if (state.active !== t.id) { saveCurrent(); state.active = t.id; persist(); renderTabs(); renderBody(); } },
          ondblclick: async () => { const n = await promptDialog('Nowa nazwa karty:', { value: t.name, title: 'Zmień nazwę' }); if (n && n.trim()) { t.name = n.trim(); persist(); renderTabs(); } } },
          icon(t.lang === 'python' ? 'terminal' : 'file-code', 13), t.name,
          h('span', { class: 'x', title: 'Zamknij kartę', onclick: async (e) => { e.stopPropagation(); await closeTab(t); } }, icon('x', 12)));
        tabBar.append(el);
      }
    }
    function saveCurrent() {
      const t = tabById(state.active);
      if (t && ed) { t.code = ed.getValue(); t.lang = ed.getLang(); t.input = inputEl.value; }
    }
    async function closeTab(t) {
      if (state.tabs.length === 1) { toast('To ostatnia karta.', 'info'); return; }
      if (t.code.trim() && t.code !== TEMPLATE_CPP && t.code !== TEMPLATE_PY) {
        const ok = await confirmDialog(`Zamknąć kartę „${t.name}”? Jej kod zostanie usunięty.`, { title: 'Zamknij kartę', ok: 'Zamknij', danger: true });
        if (!ok) return;
      }
      const i = state.tabs.indexOf(t);
      state.tabs.splice(i, 1);
      if (state.active === t.id) state.active = state.tabs[Math.max(0, i - 1)].id;
      persist(); renderTabs(); renderBody();
    }
    function addTab(lang) {
      saveCurrent();
      const t = newTab(`Karta ${state.tabs.length + 1}`, lang);
      state.tabs.push(t); state.active = t.id; persist(); renderTabs(); renderBody();
    }
    async function run() {
      saveCurrent(); persist();
      const t = tabById(state.active);
      out.setBusy(true);
      try {
        const res = await api.post('/api/run', { code: t.code, lang: t.lang, input: t.input });
        out.showResult(res, { source: t.code });
      } catch (e) { out.showText(e.message, 'danger'); }
    }
    function renderBody() {
      clear(body);
      const t = tabById(state.active);
      const edHost = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column' });
      inputEl = textarea({ value: t.input, rows: 6, placeholder: 'Wejście programu (stdin)…', onInput: () => { t.input = inputEl.value; persist(); } });
      out = outputPanel();
      out.el.addEventListener('gotoline', (e) => ed.scrollToLine(e.detail));
      const langSel = select([{ value: 'cpp', label: 'C++' }, { value: 'python', label: 'Python' }], { value: t.lang, size: 'sm', onChange: (v) => { ed.setLang(v); t.lang = v; persist(); renderTabs(); } });
      langSel.style.width = 'auto';
      const send = (id, key) => { saveCurrent(); ctx.navigate(id, { [key || 'code']: t.code }); };
      const right = h('div', { class: 'stack', style: 'overflow:auto;height:100%;padding-left:4px' },
        h('div', { class: 'row between' }, h('div', { class: 'section-title', style: 'margin:0' }, 'Wejście'),
          button('Uruchom', { icon: 'play', variant: 'primary', size: 'sm', onClick: run, title: 'Ctrl+Enter' })),
        inputEl, h('div', { class: 'section-title' }, 'Wynik'), out.el,
        h('div', { class: 'section-title mt-m' }, 'Wyślij do'),
        h('div', { class: 'row gap-s' },
          button('Sprawdzarki', { icon: 'bug', size: 'sm', onClick: () => send('checker') }),
          button('Debuggera', { icon: 'footprints', size: 'sm', onClick: () => { saveCurrent(); ctx.navigate('debugger', { code: t.code, input: t.input }); } }),
          button('Objaśniacza', { icon: 'book-open', size: 'sm', onClick: () => send('explain') }),
          button('Złożoności', { icon: 'gauge', size: 'sm', onClick: () => send('complexity') }),
          button('Testerki', { icon: 'flask-conical', size: 'sm', onClick: () => send('tester', 'solution') })));
      body.append(split(edHost, right, { ratio: 0.6, storageKey: 'scratch', min: 280 }));
      ed = createEditor(edHost, { value: t.code, lang: t.lang, height: '100%', onRun: run, toolbar: true, title: t.name, actions: [langSel],
        onChange: debounce((v) => { t.code = v; persist(); }, 300) });
      requestAnimationFrame(() => ed.refresh());
    }
    renderTabs();
    renderBody();
    window.addEventListener('beforeunload', () => { saveCurrent(); local.set('scratchpads', state); });
  },
};

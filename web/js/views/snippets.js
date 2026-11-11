// (r) Snippety: wpisz trigger (np. seg, dij, bs) w edytorze i wciśnij Tab — rozwija się Twoja implementacja.
import { h, icon, button, viewHeader, card, input, select, badge, codeBlock, clear, emptyState, toast, confirmDialog, field, markdown, callout, kbd } from '../ui.js';
import { api } from '../api.js';
import { createEditor, loadSnippets } from '../editor.js';

export default {
  id: 'snippets', title: 'Snippety', icon: 'braces', group: 'Asystent', order: 5,
  description: 'Twoje gotowe implementacje: wpisz seg, dij, bs… i wciśnij Tab w dowolnym edytorze.',
  keywords: ['snippet', 'szablon', 'template', 'seg', 'dij', 'bs', 'skrót'],
  render(root, ctx) {
    let all = [];
    let selected = null;
    const search = input({ placeholder: 'Szukaj snippetu (trigger, nazwa, tag)…', onInput: () => renderList() });
    const list = h('div', { class: 'list', style: 'overflow:auto;max-height:calc(100vh - 290px)' });
    const detail = h('div', { class: 'stack' });
    root.append(viewHeader(this, button('Nowy snippet', { icon: 'plus', variant: 'primary', onClick: () => edit(null) })),
      callout(`W każdym edytorze OI Forge wpisz **trigger** (np. \`seg\`) i wciśnij ${'`Tab`'}. Definicje globalne (struktury, funkcje) wpisane wewnątrz \`main\` trafią automatycznie **nad** funkcję. \`Ctrl+Spacja\` pokazuje listę podpowiedzi.`, 'tip'),
      h('div', { class: 'grid-2 mt-m', style: 'grid-template-columns:minmax(260px,360px) 1fr;align-items:start' },
        card({ title: 'Biblioteka', icon: 'library', body: h('div', { class: 'stack gap-s' }, search, list) }), detail));

    async function load(force = false) {
      all = await loadSnippets(force);
      renderList();
      if (!selected && all.length) show(all[0]);
    }
    function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
    function renderList() {
      const q = norm(search.value.trim());
      clear(list);
      const items = all.filter((s) => !q || norm([s.trigger, s.name, s.description, (s.tags || []).join(' ')].join(' ')).includes(q));
      if (!items.length) { list.append(emptyState({ icon: 'search', title: 'Brak snippetów', text: all.length ? 'Zmień zapytanie.' : 'Biblioteka snippetów jeszcze się nie załadowała.' })); return; }
      for (const s of items) {
        list.append(h('div', { class: ['list-item', selected && selected.trigger === s.trigger ? 'active' : ''].join(' '), onclick: () => show(s) },
          h('span', { class: 'badge accent mono', style: 'min-width:54px;justify-content:center' }, s.trigger),
          h('div', { class: 'grow ellipsis' }, s.name || s.trigger),
          s.source === 'user' ? badge('mój', 'success') : null));
      }
    }
    function show(s) {
      selected = s;
      renderList();
      clear(detail);
      detail.append(card({
        title: s.name || s.trigger, icon: 'braces', subtitle: s.description || '',
        actions: [
          s.kb ? button('Artykuł', { icon: 'book-open', size: 'sm', onClick: () => ctx.navigate('docs', { id: s.kb }) }) : null,
          button('Playground', { icon: 'play', size: 'sm', onClick: () => ctx.navigate('playground', { code: s.code.replace(/\$\{\d+:([^}]*)\}/g, '$1').replace(/\$\d/g, '') }) }),
          button(s.source === 'user' ? 'Edytuj' : 'Dostosuj', { icon: 'pencil', size: 'sm', onClick: () => edit(s) }),
          s.source === 'user' ? button('Usuń', { icon: 'trash-2', size: 'sm', variant: 'danger', onClick: () => del(s) }) : null,
        ].filter(Boolean),
        body: h('div', { class: 'stack' },
          h('div', { class: 'row gap-s' }, h('span', { class: 'muted small' }, 'Wpisz'), kbd(s.trigger), h('span', { class: 'muted small' }, 'i wciśnij'), kbd('Tab'),
            badge(s.scope === 'local' ? 'wewnątrz funkcji' : 'definicja globalna', 'info'), s.complexity ? badge(s.complexity, 'neutral', 'gauge') : null,
            ...(s.tags || []).slice(0, 5).map((t) => badge(t))),
          s.usage ? h('div', null, h('div', { class: 'section-title' }, 'Użycie'), codeBlock(s.usage, s.lang || 'cpp', { head: false })) : null,
          h('div', null, h('div', { class: 'section-title' }, 'Kod'), codeBlock(s.code.replace(/\$\{\d+:([^}]*)\}/g, '$1').replace(/\$\d/g, ''), s.lang || 'cpp', { lineNumbers: true, maxHeight: 640 }))),
      }));
    }
    function edit(s) {
      clear(detail);
      const trig = input({ value: s ? s.trigger : '', placeholder: 'np. seg2', mono: true });
      const name = input({ value: s ? s.name || '' : '', placeholder: 'Nazwa, np. Drzewo przedziałowe (max)' });
      const desc = input({ value: s ? s.description || '' : '', placeholder: 'Krótki opis' });
      const usage = input({ value: s ? s.usage || '' : '', placeholder: 'Linia użycia, np. SegTree st(n);', mono: true });
      const scope = select([{ value: 'global', label: 'Definicja globalna (nad main)' }, { value: 'local', label: 'Instrukcje (w miejscu kursora)' }], { value: s ? s.scope || 'global' : 'global' });
      const lang = select([{ value: 'cpp', label: 'C++' }, { value: 'python', label: 'Python' }], { value: s ? s.lang || 'cpp' : 'cpp' });
      const edHost = h('div');
      detail.append(card({
        title: s ? `Edycja: ${s.trigger}` : 'Nowy snippet', icon: 'pencil',
        subtitle: s && s.source !== 'user' ? 'Zapisanie utworzy Twoją wersję, która zastąpi wbudowaną.' : 'Placeholdery: ${1:nazwa} — Tab przeskakuje między nimi.',
        body: h('div', { class: 'stack' },
          h('div', { class: 'grid-2' }, field('Trigger', trig), field('Nazwa', name)),
          h('div', { class: 'grid-2' }, field('Zakres', scope), field('Język', lang)),
          field('Opis', desc), field('Użycie (opcjonalnie)', usage, 'Gdy snippet globalny rozwiniesz w funkcji, w miejscu kursora wstawi się ta linia.'),
          field('Kod', edHost),
          h('div', { class: 'row' }, button('Zapisz', { icon: 'save', variant: 'primary', onClick: save }), button('Anuluj', { variant: 'ghost', onClick: () => (s ? show(s) : all[0] && show(all[0])) }))),
      }));
      const ed = createEditor(edHost, { value: s ? s.code : '', lang: s ? s.lang || 'cpp' : 'cpp', height: '380px' });
      async function save() {
        const r = await api.post('/api/snippets/save', { snippet: { trigger: trig.value.trim(), name: name.value.trim(), description: desc.value.trim(), usage: usage.value, scope: scope.value, lang: lang.value, code: ed.getValue() } });
        window.dispatchEvent(new Event('oif:snippets-changed'));
        all = await loadSnippets(true);
        toast('Zapisano snippet', 'success');
        show(all.find((x) => x.trigger === trig.value.trim()) || all[0]);
        return r;
      }
    }
    async function del(s) {
      if (!(await confirmDialog(`Usunąć Twój snippet „${s.trigger}”?`, { title: 'Usuń snippet', ok: 'Usuń', danger: true }))) return;
      await api.post('/api/snippets/delete', { trigger: s.trigger });
      window.dispatchEvent(new Event('oif:snippets-changed'));
      selected = null;
      await load(true);
    }
    this._select = (trigger) => { const s = all.find((x) => x.trigger === trigger); if (s) show(s); };
    load();
  },
  onShow(params) { if (params.trigger && this._select) this._select(params.trigger); },
};

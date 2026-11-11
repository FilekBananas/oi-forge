// „Narzędzia” — jedna prosta strona ze WSZYSTKIMI narzędziami (nic nie znika), pogrupowanymi według tego, co robisz.
// Pole u góry od razu zawęża kafelki; Enter bez trafień = szukaj w bazie wiedzy.
import { h, icon } from '../ui.js';
import { isStarred, qualityNote, HINT_ONLY } from '../quality.js';

// kolejność i nazwy grup na tej stronie (grupy widoków z meta.group)
const SECTIONS = [
  { group: 'Kod', title: 'Kod', text: 'Pisz, sprawdzaj, przyspieszaj', icon: 'code-xml' },
  { group: 'Zadania', title: 'Zadanie', text: 'Od treści do pomysłu, brute i rozwiązania', icon: 'file-text' },
  { group: 'Testowanie', title: 'Testy', text: 'Brute kontra wzorcówka, generatory, porównania', icon: 'flask-conical' },
  { group: 'Asystent', title: 'Ściągawka i szukanie', text: 'STL, algorytmy, snippety, asystent', icon: 'search' },
  { group: 'Moje programy', title: 'Moje programy', text: 'Rozwiązania z SIO2', icon: 'folder' },
  { group: 'Narzędzia', title: 'Inne', text: 'Kalkulatory, wizualizacje, ustawienia', icon: 'blocks' },
  { group: 'Trening', title: 'Trening (poza zawodami)', text: 'Symulacja etapu, fiszki, ścieżka, dziennik', icon: 'trophy' },
];

const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l');

export default {
  id: 'home', title: 'Narzędzia', icon: 'blocks', group: null, order: 0,
  description: 'Wszystkie narzędzia OI Forge na jednej stronie', keywords: ['narzędzia', 'wszystkie', 'start', 'home', 'lista'],
  render(root, ctx) {
    const q = h('input', { class: 'input hub-filter', placeholder: 'Czego potrzebujesz? np. „test”, „błąd”, „złożoność”, „python”…', spellcheck: 'false' });
    const body = h('div', { class: 'hub-sections' });
    const none = h('div', { class: 'hub-none muted', hidden: true });
    root.append(
      h('div', { class: 'hub-head' },
        h('div', null, h('h1', null, 'Narzędzia'), h('p', { class: 'muted' }, 'Wszystko w jednym miejscu. Najważniejsze masz też w edytorze pod przyciskiem „Narzędzia”.')),
        h('div', { class: 'hub-search' }, icon('search', 17), q)),
      h('div', { class: 'hub-legend' },
        h('span', null, h('b', null, '★'), ' — sprawdzone, działa pewnie'),
        h('span', null, 'bez gwiazdki — podpowiedź, wynik sprawdzaj (najedź, żeby zobaczyć dlaczego)')),
      body, none);

    const all = [...(window.OIF?.views?.values() || [])].filter((v) => v.meta.id !== 'home' && !v.meta.hidden && !v.error);
    const tiles = [];
    for (const sec of SECTIONS) {
      const items = all.filter((v) => (SECTIONS.some((s) => s.group === v.meta.group) ? v.meta.group : 'Narzędzia') === sec.group)
        // najpierw to, co działa pewnie (★), potem podpowiedzi
        .sort((a, b) => (isStarred(b.meta.id) - isStarred(a.meta.id)) || (a.meta.order ?? 100) - (b.meta.order ?? 100) || a.meta.title.localeCompare(b.meta.title, 'pl'));
      if (!items.length) continue;
      const grid = h('div', { class: 'hub-grid' });
      for (const v of items) {
        const note = qualityNote(v.meta.id);
        const t = h('button', { class: 'hub-tile', type: 'button', title: note || v.meta.description || '', onclick: () => ctx.navigate(v.meta.id) },
          h('span', { class: 'hub-ic' }, icon(v.meta.icon || 'blocks', 19)),
          h('span', { class: 'hub-txt' },
            h('b', null, v.meta.title, isStarred(v.meta.id) ? h('span', { class: 'q-star' }, '★') : null),
            h('small', null, HINT_ONLY[v.meta.id] ? 'Podpowiedź — sprawdzaj wynik' : (v.meta.description || ''))));
        t._hay = norm([v.meta.title, v.meta.description, (v.meta.keywords || []).join(' '), sec.title].join(' '));
        tiles.push(t);
        grid.append(t);
      }
      const block = h('section', { class: 'hub-sec' },
        h('div', { class: 'hub-sec-head' }, icon(sec.icon, 16), h('h2', null, sec.title), h('span', { class: 'muted small' }, sec.text)), grid);
      block._grid = grid;
      body.append(block);
    }

    const filter = () => {
      const words = norm(q.value.trim()).split(/\s+/).filter(Boolean);
      let shown = 0;
      for (const t of tiles) { const ok = words.every((w) => t._hay.includes(w)); t.hidden = !ok; if (ok) shown++; }
      for (const b of body.children) b.hidden = ![...b._grid.children].some((t) => !t.hidden);
      none.hidden = shown > 0;
      if (!shown) {
        none.replaceChildren('Brak narzędzia o takiej nazwie. ',
          h('button', { class: 'linklike', type: 'button', onclick: () => ctx.navigate('search', { q: q.value.trim() }) }, `Szukaj „${q.value.trim()}” w bazie wiedzy →`));
      }
    };
    q.addEventListener('input', filter);
    q.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { q.value = ''; filter(); }
      if (e.key !== 'Enter') return;
      const first = tiles.find((t) => !t.hidden);
      if (q.value.trim() && first && tiles.filter((t) => !t.hidden).length <= 3) first.click();
      else if (q.value.trim() && !first) ctx.navigate('search', { q: q.value.trim() });
    });
    this._focus = () => setTimeout(() => q.focus(), 40);
    this._focus();
  },
  onShow() { this._focus?.(); },
};

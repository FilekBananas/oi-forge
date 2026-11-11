// Wyszukiwarka — klasyczne wyniki (jak w wyszukiwarce internetowej) z całego „lokalnego internetu”:
// artykuły, pytania i odpowiedzi, funkcje STL, snippety, Twoje programy z SIO2 i narzędzia aplikacji. Z podglądem obok.
import { h, icon, button, badge, markdown, codeBlock, clear, emptyState, debounce, kbd, spinner, escapeHtml } from '../ui.js';
import { api, local } from '../api.js';

const FILTERS = [
  { id: 'all', label: 'Wszystko', icon: 'search' },
  { id: 'article', label: 'Artykuły', icon: 'book-open' },
  { id: 'qa', label: 'Pytania', icon: 'message-square' },
  { id: 'function', label: 'Funkcje', icon: 'square-function' },
  { id: 'snippet', label: 'Snippety', icon: 'braces' },
  { id: 'program', label: 'Moje programy', icon: 'folder' },
  { id: 'tool', label: 'Narzędzia', icon: 'blocks' },
];
const TYPE_LABEL = { article: 'Artykuł', qa: 'Pytanie i odpowiedź', function: 'Funkcja', snippet: 'Snippet', program: 'Mój program', tool: 'Narzędzie', section: 'Artykuł' };
const POPULAR = ['binsearch', 'set', 'dijkstra', 'zaokrąglenie', 'drzewo przedziałowe', 'sort malejąco', 'priority_queue', 'sumy prefiksowe', 'long long', 'kmp'];

function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/ł/g, 'l'); }

function inlineMd(text) {
  // fragment z **pogrubieniami** z wyszukiwarki → bezpieczny HTML
  const html = escapeHtml(String(text || '')).replace(/\*\*([^*]+)\*\*/g, '<mark>$1</mark>').replace(/`([^`]+)`/g, '<code>$1</code>');
  return h('span', { html });
}

export default {
  id: 'search', title: 'Wyszukiwarka', icon: 'search', group: 'Asystent', order: 0, layout: 'full',
  description: 'Szukaj jak w Google — w artykułach, pytaniach, funkcjach STL, snippetach i Twoich programach. Z podglądem obok.',
  keywords: ['szukaj', 'search', 'wyszukiwarka', 'znajdź', 'google'],
  render(root, ctx) {
    let state = { q: '', filter: 'all', results: [], sel: -1, req: 0, programs: null };
    const inp = h('input', { class: 'input lg', placeholder: 'Czego szukasz? np. „binsearch”, „set”, „jak wczytać do końca pliku”…', spellcheck: 'false', autocomplete: 'off' });
    const clearBtn = h('button', { class: 'btn ghost icon sr-clear', title: 'Wyczyść', onclick: () => { inp.value = ''; run(); inp.focus(); } }, icon('x', 16));
    const chips = h('div', { class: 'sr-filters' });
    const meta = h('div', { class: 'sr-meta muted small' });
    const list = h('div', { class: 'sr-list' });
    const preview = h('div', { class: 'sr-preview' });
    root.append(
      h('div', { class: 'sr-top' },
        h('div', { class: 'sr-box' }, h('div', { class: 'sr-icon' }, icon('search', 20)), inp, clearBtn,
          button('Zapytaj asystenta', { icon: 'message-square', variant: 'secondary', onClick: () => inp.value.trim() && ctx.navigate('assistant', { q: inp.value.trim() }) })),
        chips, meta),
      h('div', { class: 'sr-body' }, list, preview));

    inp.addEventListener('input', debounce(() => run(), 180));
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); select(Math.min(visible().length - 1, state.sel + 1)); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); select(Math.max(0, state.sel - 1)); }
      else if (e.key === 'Enter') { e.preventDefault(); const r = visible()[state.sel]; if (r) open(r); else run(); }
    });

    function visible() {
      return state.filter === 'all' ? state.results : state.results.filter((r) => r.type === state.filter || (state.filter === 'article' && r.type === 'section'));
    }
    function renderChips() {
      clear(chips);
      const counts = {};
      for (const r of state.results) { const t = r.type === 'section' ? 'article' : r.type; counts[t] = (counts[t] || 0) + 1; }
      for (const f of FILTERS) {
        const n = f.id === 'all' ? state.results.length : counts[f.id] || 0;
        if (f.id !== 'all' && !n && state.q) continue;
        chips.append(h('button', { class: ['sr-chip', state.filter === f.id ? 'active' : ''].join(' '), type: 'button',
          onclick: () => { state.filter = f.id; state.sel = 0; renderChips(); renderList(); } },
          icon(f.icon, 14), f.label, state.q ? h('span', { class: 'sr-count' }, String(n)) : null));
      }
    }
    function toolResults(q) {
      const nq = norm(q);
      if (nq.length < 2) return [];
      const out = [];
      for (const v of window.OIF?.views?.values() || []) {
        const m = v.meta;
        if (m.hidden || m.id === 'search') continue;
        const hay = norm([m.title, m.description, (m.keywords || []).join(' ')].join(' '));
        const words = nq.split(/\s+/).filter(Boolean);
        if (words.every((w) => hay.includes(w))) {
          out.push({ type: 'tool', id: 'tool:' + m.id, title: m.title, subtitle: m.group || 'Narzędzie', snippet: m.description || '', view: m.id, icon: m.icon, score: norm(m.title).includes(nq) ? 2 : 0.5 });
        }
      }
      return out.sort((a, b) => b.score - a.score).slice(0, 6);
    }
    async function run() {
      const q = inp.value.trim();
      state.q = q;
      clearBtn.style.visibility = q ? 'visible' : 'hidden';
      const my = ++state.req;
      if (!q) { state.results = []; state.sel = -1; renderChips(); renderEmpty(); return; }
      meta.replaceChildren(spinner(14), ' Szukam…');
      let res = [];
      let dym = null;
      const t0 = performance.now();
      try {
        const [g, p] = await Promise.all([
          api.get('/api/search/global', { q, k: 50 }, { quiet: true }).catch(() => null),
          api.get('/api/programs/search', { q, k: 15 }, { quiet: true }).catch(() => null),
        ]);
        if (g) { res = g.results || []; dym = g.did_you_mean; }
        else {
          const kb = await api.get('/api/kb/search', { q, k: 30 }, { quiet: true }).catch(() => ({ results: [] }));
          res = (kb.results || []).map((x) => ({ type: 'article', id: x.id, article_id: x.id, title: x.title, subtitle: x.category, category: x.category, snippet: x.summary, view: 'docs', params: { id: x.id } }));
        }
        // więcej Twoich programów (wyszukiwarka programów rozumie tematy rund i kod)
        if (p && p.results) {
          const have = new Set(res.filter((r) => r.type === 'program').map((r) => r.id));
          for (const x of p.results) {
            if (have.has(x.id)) continue;
            res.push({ type: 'program', id: x.id, title: x.problem_name, subtitle: x.contest_name, category: 'Moje programy',
              snippet: (x.why && x.why[0]) || x.snippet || '', view: 'programs', params: { id: x.id, q }, score: (x.score || 0) * 0.5 });
          }
        }
      } catch { /* */ }
      if (my !== state.req) return;
      res = [...toolResults(q), ...res];
      state.results = res;
      state.sel = res.length ? 0 : -1;
      const ms = Math.round(performance.now() - t0);
      meta.replaceChildren(`${res.length} ${res.length === 1 ? 'wynik' : 'wyników'} · ${ms} ms`,
        dym ? h('span', null, ' · Czy chodziło Ci o: ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); inp.value = dym; run(); } }, dym), '?') : null);
      if (q.length >= 3) {
        const recent = [q, ...(local.get('search:recent', []) || []).filter((x) => x !== q)].slice(0, 8);
        local.set('search:recent', recent);
      }
      renderChips();
      renderList();
    }
    function renderEmpty() {
      meta.replaceChildren();
      clear(list);
      const recent = local.get('search:recent', []) || [];
      list.append(h('div', { class: 'sr-empty' },
        emptyState({ icon: 'search', title: 'Wyszukiwarka OI Forge', text: 'Przeszukuje 370 artykułów, 3300+ pytań i odpowiedzi, 1700+ funkcji z dokumentacji, snippety i Twoje programy z SIO2. Działa offline, z poprawianiem literówek.' }),
        recent.length ? h('div', { class: 'sr-sugg' }, h('div', { class: 'section-title' }, 'Ostatnio szukane'),
          h('div', { class: 'row gap-s' }, recent.map((r) => h('span', { class: 'chip', onclick: () => { inp.value = r; run(); } }, icon('history', 13), r)))) : null,
        h('div', { class: 'sr-sugg' }, h('div', { class: 'section-title' }, 'Popularne'),
          h('div', { class: 'row gap-s' }, POPULAR.map((r) => h('span', { class: 'chip', onclick: () => { inp.value = r; run(); } }, icon('sparkles', 13), r))))));
      clear(preview);
      preview.append(h('div', { class: 'sr-hint' }, h('div', { class: 'muted small' }, 'Skróty'),
        h('div', { class: 'small' }, kbd('↑'), ' ', kbd('↓'), ' wybór wyniku · ', kbd('Enter'), ' otwórz')));
    }
    function renderList() {
      clear(list);
      const vis = visible();
      if (!vis.length) {
        list.append(emptyState({ icon: 'search', title: 'Brak wyników', text: 'Spróbuj innych słów albo zapytaj asystenta — on rozumie pełne pytania.',
          action: button('Zapytaj asystenta', { icon: 'message-square', variant: 'primary', onClick: () => ctx.navigate('assistant', { q: state.q }) }) }));
        clear(preview);
        return;
      }
      vis.forEach((r, i) => {
        const t = r.type === 'section' ? 'article' : r.type;
        const el = h('div', { class: ['sr-item', i === state.sel ? 'sel' : ''].join(' '), tabindex: -1,
          onclick: () => select(i), ondblclick: () => open(r) },
          h('div', { class: `sr-ti t-${t}` }, icon(r.icon || FILTERS.find((f) => f.id === t)?.icon || 'file-text', 16)),
          h('div', { class: 'grow', style: 'min-width:0' },
            h('div', { class: 'sr-crumb' }, TYPE_LABEL[t] || t, r.subtitle ? h('span', null, ' › ' + r.subtitle) : null),
            h('div', { class: 'sr-title' }, r.title || r.q || r.id),
            r.snippet ? h('div', { class: 'sr-snip' }, inlineMd(r.snippet)) : null));
        el.dataset.idx = i;
        list.append(el);
      });
      select(Math.max(0, Math.min(state.sel, vis.length - 1)), false);
    }
    function select(i, scroll = true) {
      const vis = visible();
      if (i < 0 || i >= vis.length) return;
      state.sel = i;
      list.querySelectorAll('.sr-item').forEach((el) => el.classList.toggle('sel', +el.dataset.idx === i));
      if (scroll) list.querySelector('.sr-item.sel')?.scrollIntoView({ block: 'nearest' });
      showPreview(vis[i]);
    }
    function open(r) {
      if (r.type === 'tool') return ctx.navigate(r.view);
      if (r.type === 'qa') return ctx.navigate('assistant', { q: r.q || r.title });
      if (r.type === 'snippet') return ctx.navigate('snippets', { trigger: r.trigger || r.id });
      if (r.type === 'program') return ctx.navigate('programs', r.params || { id: r.id });
      return ctx.navigate('docs', { id: r.article_id || r.id, anchor: r.params && r.params.anchor });
    }
    let pvReq = 0;
    async function showPreview(r) {
      const my = ++pvReq;
      clear(preview);
      const head = h('div', { class: 'sr-pv-head' },
        h('div', { class: 'sr-crumb' }, TYPE_LABEL[r.type] || r.type, r.category ? ' › ' + r.category : ''),
        h('h2', null, r.title || r.q || r.id),
        h('div', { class: 'row gap-s mt-s' }, button(r.type === 'qa' ? 'Zapytaj asystenta' : 'Otwórz', { icon: 'arrow-right', variant: 'primary', size: 'sm', onClick: () => open(r) })));
      const body = h('div', { class: 'sr-pv-body' }, h('div', { class: 'skeleton', style: 'height:120px' }));
      preview.append(head, body);
      try {
        let content;
        if (r.type === 'program') {
          const d = await api.get('/api/programs/get', { id: r.id }, { quiet: true });
          const p = d.program || d;
          content = [h('div', { class: 'row gap-s' }, p.round ? badge(p.round, 'accent') : null, ...(p.tags || []).slice(0, 4).map((t) => badge(typeof t === 'string' ? t : t.name || t.id))),
            h('div', { class: 'row gap-s mt-s' },
              button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', onClick: () => ctx.navigate('workspace', { code: p.code, name: p.problem_name }) }),
              button('Objaśnij', { icon: 'book-open', size: 'sm', onClick: () => ctx.navigate('explain', { code: p.code }) })),
            codeBlock(p.code || '', 'cpp', { lineNumbers: true, maxHeight: 520 })];
        } else if (r.type === 'snippet') {
          const sn = await api.get('/api/snippets', null, { quiet: true });
          const s = (sn.snippets || []).find((x) => x.trigger === (r.trigger || r.id || '').replace(/^s:/, '')) || null;
          content = s ? [markdown(`Wpisz \`${s.trigger}\` w edytorze i wciśnij **Tab**. ${s.description || ''}`), codeBlock(s.code.replace(/\$\{\d+:([^}]*)\}/g, '$1').replace(/\$\d/g, ''), s.lang || 'cpp', { lineNumbers: true, maxHeight: 520 })]
            : [markdown(r.snippet || '')];
        } else if (r.type === 'tool') {
          content = [markdown(r.snippet || ''), button('Otwórz narzędzie', { icon: r.icon || 'arrow-right', onClick: () => open(r) })];
        } else {
          const d = await api.get('/api/docs/article', { id: r.article_id || r.id }, { quiet: true }).catch(() => api.get('/api/kb/article', { id: r.article_id || r.id }, { quiet: true }));
          const a = d.article || d;
          content = [];
          if (r.type === 'qa') {
            const qa = (a.qa || []).find((x) => (Array.isArray(x.q) ? x.q : [x.q]).some((q) => norm(q) === norm(r.q)));
            if (qa) { content.push(markdown(qa.a || '')); if (qa.code) content.push(codeBlock(qa.code, qa.lang || 'cpp')); }
            content.push(h('div', { class: 'divider' }), h('div', { class: 'muted small' }, 'Z artykułu: ' + a.title));
          }
          if (r.type === 'function') {
            const fns = a.functions || [];
            content.push(h('table', { class: 'tbl compact' }, h('tbody', null, fns.slice(0, 14).map((f) => h('tr', null,
              h('td', { class: 'mono small' }, f.sig), h('td', { class: 'small' }, f.desc), h('td', { class: 'mono small muted' }, f.complexity || ''))))));
          }
          if (r.type !== 'qa') {
            if (a.summary) content.push(markdown(a.summary));
            if (a.complexity) content.push(h('div', { class: 'row gap-s' }, badge(a.complexity, 'accent', 'gauge')));
            for (const b of (a.body || []).slice(0, 4)) {
              if (b.type === 'code') content.push(codeBlock(b.code, b.lang || 'cpp', { title: b.title, maxHeight: 360 }));
              else if (b.type === 'table') content.push(markdown(['| ' + b.headers.join(' | ') + ' |', '|' + b.headers.map(() => '---').join('|') + '|', ...b.rows.map((rw) => '| ' + rw.join(' | ') + ' |')].join('\n')));
              else if (b.md) content.push(markdown(b.type === 'tip' ? '> [!TIP] ' + b.md : b.type === 'warn' ? '> [!WARN] ' + b.md : b.md));
            }
            content.push(button('Czytaj cały artykuł', { icon: 'book-open', size: 'sm', onClick: () => open(r) }));
          }
        }
        if (my !== pvReq) return;
        body.replaceChildren(...content.filter(Boolean));
      } catch (e) {
        if (my !== pvReq) return;
        body.replaceChildren(markdown(r.snippet || ''));
      }
    }
    this._run = (q) => { inp.value = q; run(); };
    renderChips();
    renderEmpty();
    setTimeout(() => inp.focus(), 50);
  },
  onShow(params) {
    if (params && params.q && this._run) this._run(params.q);
  },
};

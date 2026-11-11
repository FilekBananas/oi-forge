// (p) Dokumentacja offline — kategorie, czytnik artykułów (spis treści, funkcje, pułapki, QA) i wyszukiwarka.
import {
  h, icon, button, iconButton, viewHeader, badge, codeBlock, markdown, callout, clear, emptyState, debounce, input, spinner,
} from '../ui.js';
import { api, local } from '../api.js';

const LEVEL_TONE = { 1: 'success', 2: 'info', 3: 'warn', 4: 'danger', 5: 'danger' };

export default {
  id: 'docs',
  title: 'Dokumentacja',
  icon: 'book-open',
  group: 'Asystent',
  order: 2,
  description: 'Baza wiedzy offline: C++, STL, wejście/wyjście, algorytmy i struktury danych — z kodem, złożonością i pułapkami.',
  keywords: ['docs', 'baza wiedzy', 'artykuły', 'stl', 'cppreference', 'dokumentacja', 'algorytmy', 'kb'],

  render(root, ctx) {
    const st = { tree: null, current: null, collapsed: new Set(local.get('docs:collapsed', []) || []), query: '', results: null, pendingAnchor: null };
    this._st = st;

    const filter = input({
      placeholder: 'Szukaj w dokumentacji…', attrs: { 'aria-label': 'Szukaj w dokumentacji' },
      onInput: () => { st.query = filter.value.trim(); runSearch(); },
      onEnter: () => { st.query = filter.value.trim(); runSearch.flush(); },
    });
    const sideList = h('div', { class: 'docs-side-list' });
    const counter = h('div', { class: 'docs-side-count muted small' });
    const side = h('aside', { class: 'docs-side' },
      h('div', { class: 'docs-search' }, icon('search', 15), filter,
        iconButton('x', { title: 'Wyczyść', onClick: () => { filter.value = ''; st.query = ''; st.results = null; renderSide(); } })),
      counter, sideList);
    const reader = h('article', { class: 'docs-reader' });
    const tocBox = h('nav', { class: 'docs-toc', 'aria-label': 'Spis treści' });
    root.append(viewHeader(this,
      button('Zapytaj asystenta', {
        icon: 'bot', title: 'Zadaj pytanie o bieżący temat',
        onClick: () => ctx.navigate('assistant', st.current ? { q: `Co to ${st.current.article.title.split(/\s+[—–-]\s+|\s*\(/)[0]}?` } : {}),
      })),
    h('div', { class: 'docs-layout' }, side, h('div', { class: 'docs-center' }, reader), tocBox));

    // ------------------------------------------------------------------ drzewo kategorii
    async function loadTree() {
      clear(sideList).append(h('div', { class: 'pad center' }, spinner(18)));
      try {
        st.tree = await api.get('/api/docs/tree');
      } catch {
        clear(sideList).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać bazy wiedzy' }));
        return;
      }
      renderSide();
      if (!st.current) renderLanding();
    }

    function renderSide() {
      clear(sideList);
      if (st.query && st.results) return renderResults();
      const tree = st.tree;
      if (!tree) return;
      counter.textContent = `${tree.total} artykułów w ${tree.categories.length} kategoriach`;
      for (const cat of tree.categories) {
        const open = !st.collapsed.has(cat.name);
        const head = h('button', { class: ['docs-cat', open ? 'open' : ''].join(' '), onclick: () => toggleCat(cat.name) },
          icon('chevron-right', 14, { strokeWidth: 2.4 }), icon(cat.icon || 'book-open', 15),
          h('span', { class: 'grow ellipsis' }, cat.name), h('span', { class: 'docs-cat-count' }, String(cat.count)));
        const items = h('div', { class: 'docs-cat-items', hidden: !open },
          cat.articles.map((a) => h('button', {
            class: ['docs-item', st.current?.article.id === a.id ? 'active' : ''].join(' '), title: a.summary || a.title,
            dataset: { id: a.id }, onclick: () => openArticle(a.id),
          }, h('span', { class: `docs-level lv${a.level || 0}`, title: a.level_label ? `Poziom: ${a.level_label}` : '' }),
          h('span', { class: 'grow ellipsis' }, a.title))));
        sideList.append(h('div', { class: 'docs-cat-wrap' }, head, items));
      }
    }

    function toggleCat(name) {
      if (st.collapsed.has(name)) st.collapsed.delete(name); else st.collapsed.add(name);
      local.set('docs:collapsed', [...st.collapsed]);
      renderSide();
    }

    // ------------------------------------------------------------------ wyszukiwanie
    let searchSeq = 0;
    const runSearch = debounce(async () => {
      const q = st.query;
      if (q.length < 2) { st.results = null; renderSide(); return; }
      const my = ++searchSeq;
      try {
        const r = await api.get('/api/docs/search', { q, k: 30 }, { quiet: true });
        if (my !== searchSeq) return;
        st.results = r;
      } catch { st.results = { results: [] }; }
      renderSide();
    }, 180);

    function renderResults() {
      const r = st.results;
      counter.textContent = r.results.length ? `${r.results.length} wyników` : '';
      if (r.did_you_mean) {
        sideList.append(h('div', { class: 'docs-dym small' }, 'Szukam: ', h('b', null, r.did_you_mean.text)));
      }
      if (!r.results.length) {
        sideList.append(emptyState({ icon: 'search', title: 'Nic nie znaleziono', text: 'Spróbuj innych słów albo zapytaj asystenta.',
          action: button('Zapytaj asystenta', { icon: 'bot', size: 'sm', onClick: () => ctx.navigate('assistant', { q: st.query }) }) }));
        return;
      }
      for (const x of r.results) {
        sideList.append(h('button', {
          class: ['docs-result', st.current?.article.id === x.id ? 'active' : ''].join(' '),
          onclick: () => openArticle(x.id, x.anchor),
        },
        h('div', { class: 'row gap-s' }, h('span', { class: `docs-level lv${x.level || 0}` }), h('b', { class: 'grow ellipsis' }, x.title)),
        h('div', { class: 'docs-result-meta' }, x.category, x.hit_kind && x.hit_kind !== 'article' ? ` · ${x.hit_label}` : ''),
        x.snippet ? h('div', { class: 'docs-result-snip' }, markdown(x.snippet)) : null));
      }
    }

    // ------------------------------------------------------------------ strona startowa
    function renderLanding() {
      clear(reader);
      clear(tocBox);
      const tree = st.tree;
      if (!tree) return;
      reader.append(h('div', { class: 'docs-landing' },
        h('div', { class: 'docs-landing-head' }, h('h2', null, 'Baza wiedzy OI Forge'),
          h('p', { class: 'muted' }, `${tree.total} artykułów z kodem, złożonością, pułapkami i setkami pytań. Wszystko offline. `,
            'Wybierz kategorię albo wpisz szukane słowo — rozumiem literówki i polskie odmiany.')),
        h('div', { class: 'docs-cat-grid' }, tree.categories.map((c) => h('div', { class: 'docs-cat-card' },
          h('div', { class: 'docs-cat-card-head' }, h('div', { class: 'docs-cat-ic' }, icon(c.icon || 'book-open', 17)),
            h('b', { class: 'grow' }, c.name), badge(String(c.count))),
          h('div', { class: 'docs-cat-card-list' }, c.articles.slice(0, 5).map((a) => h('button', {
            class: 'linklike docs-cat-link ellipsis', onclick: () => openArticle(a.id),
          }, a.title))),
          c.articles.length > 5 ? h('button', { class: 'linklike small muted', onclick: () => { st.collapsed.delete(c.name); renderSide(); sideList.querySelector(`[data-id="${c.articles[0].id}"]`)?.scrollIntoView({ block: 'center' }); } },
            `i ${c.articles.length - 5} więcej…`) : null)))));
    }

    // ------------------------------------------------------------------ czytnik
    async function openArticle(id, anchor) {
      if (!id) return;
      st.pendingAnchor = anchor || null;
      if (st.current?.article.id === id) { scrollToAnchor(anchor); return; }
      clear(reader).append(h('div', { class: 'pad center' }, spinner(22)));
      let data;
      try {
        data = await api.get('/api/docs/article', { id });
      } catch (e) {
        clear(reader).append(emptyState({ icon: 'triangle-alert', title: 'Nie znaleziono artykułu', text: String(e.message || e) }));
        return;
      }
      st.current = data;
      local.set('docs:last', id);
      renderArticle(data);
      markActive();
      scrollToAnchor(st.pendingAnchor);
    }
    this._open = openArticle;

    function markActive() {
      sideList.querySelectorAll('.docs-item, .docs-result').forEach((b) => b.classList.remove('active'));
      const el = sideList.querySelector(`.docs-item[data-id="${st.current?.article.id}"]`);
      if (el) { el.classList.add('active'); el.scrollIntoView({ block: 'nearest' }); }
    }

    function scrollToAnchor(anchor) {
      const scroller = root.closest('.content') || document.scrollingElement;
      if (!anchor) { scroller.scrollTo?.({ top: 0 }); return; }
      requestAnimationFrame(() => {
        const el = reader.querySelector(`[data-anchor="${anchor}"]`);
        if (!el) return;
        if (el.tagName === 'DETAILS') el.open = true;
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        el.classList.add('docs-flash');
        setTimeout(() => el.classList.remove('docs-flash'), 1600);
      });
    }

    function renderArticle(data) {
      const a = data.article;
      clear(reader);
      const lvTone = LEVEL_TONE[a.level] || 'neutral';
      reader.append(h('header', { class: 'docs-art-head', dataset: { anchor: 'top' } },
        h('div', { class: 'docs-crumbs' }, icon(data.category_icon || 'book-open', 14), a.category,
          a.level_label ? badge(`poziom: ${a.level_label}`, lvTone) : null),
        h('h1', null, a.title),
        a.summary ? h('p', { class: 'docs-summary' }, markdown(a.summary)) : null,
        h('div', { class: 'docs-meta' },
          a.complexity ? h('div', { class: 'docs-complexity' }, icon('gauge', 15), h('span', null, markdown(a.complexity))) : null,
          h('div', { class: 'docs-meta-row' },
            badge(`${data.stats.reading_min} min czytania`, 'neutral', 'clock'),
            data.stats.code ? badge(`${data.stats.code} przykł. kodu`, 'neutral', 'code-xml') : null,
            data.stats.qa ? badge(`${data.stats.qa} pytań`, 'neutral', 'message-square') : null,
            (a.tags || []).slice(0, 6).map((t) => h('span', { class: 'docs-tag' }, '#' + t))))));

      // body
      (a.body || []).forEach((b, i) => {
        const node = renderBodyBlock(b, i);
        if (node) reader.append(node);
      });

      if ((a.functions || []).length) {
        reader.append(h('section', { class: 'docs-section', dataset: { anchor: 'functions' } },
          h('h2', null, icon('square-function', 18), 'Najważniejsze funkcje'),
          h('div', { class: 'table-wrap' }, h('table', { class: 'tbl docs-fn-table' },
            h('thead', null, h('tr', null, h('th', null, 'Wywołanie'), h('th', null, 'Opis'), h('th', null, 'Złożoność'))),
            h('tbody', null, a.functions.map((f) => h('tr', null,
              h('td', null, h('code', { class: 'docs-sig' }, f.sig), f.returns ? h('div', { class: 'faint small docs-fn-ret' }, '→ ' + f.returns) : null),
              h('td', null, markdown(f.desc || ''), f.example ? h('code', { class: 'docs-fn-ex' }, f.example) : null),
              h('td', { class: 'nowrap' }, f.complexity ? badge(f.complexity, 'info') : '—'))))))));
      }

      if ((a.pitfalls || []).length) {
        reader.append(h('section', { class: 'docs-section', dataset: { anchor: 'pitfalls' } },
          h('h2', null, icon('triangle-alert', 18), 'Pułapki'),
          h('div', { class: 'docs-pitfalls' }, a.pitfalls.map((p) => h('div', { class: 'docs-pitfall' }, icon('circle-alert', 15), markdown(p))))));
      }

      if ((a.qa || []).length) {
        reader.append(h('section', { class: 'docs-section', dataset: { anchor: 'qa' } },
          h('div', { class: 'row' }, h('h2', { class: 'grow' }, icon('message-square', 18), 'Pytania i odpowiedzi'),
            button('Rozwiń wszystkie', { size: 'sm', variant: 'ghost', icon: 'chevron-down', onClick: () => reader.querySelectorAll('details.docs-qa').forEach((d) => { d.open = true; }) })),
          h('div', { class: 'docs-qas' }, a.qa.map((qa, j) => renderQA(qa, j)))));
      }

      if ((data.related || []).length) {
        reader.append(h('section', { class: 'docs-section', dataset: { anchor: 'related' } },
          h('h2', null, icon('git-branch', 18), 'Powiązane tematy'),
          h('div', { class: 'docs-related' }, data.related.map((r) => h('button', { class: 'docs-rel-card', onclick: () => openArticle(r.id) },
            h('div', { class: 'row gap-s' }, h('span', { class: `docs-level lv${r.level || 0}` }), h('b', { class: 'ellipsis grow' }, r.title)),
            h('div', { class: 'muted small docs-rel-sum' }, r.summary || ''),
            h('div', { class: 'faint small' }, r.category))))));
      }

      const nb = data.neighbors || {};
      reader.append(h('nav', { class: 'docs-pager' },
        nb.prev ? h('button', { class: 'docs-pager-btn', onclick: () => openArticle(nb.prev.id) }, icon('chevron-left', 16),
          h('div', null, h('div', { class: 'faint small' }, 'Poprzedni'), h('b', null, nb.prev.title))) : h('span'),
        nb.next ? h('button', { class: 'docs-pager-btn next', onclick: () => openArticle(nb.next.id) },
          h('div', null, h('div', { class: 'faint small' }, 'Następny'), h('b', null, nb.next.title)), icon('chevron-right', 16)) : h('span')));

      renderToc(data.toc || []);
    }

    function renderBodyBlock(b, i) {
      const anchor = `b${i}`;
      switch (b.type) {
        case 'code': {
          const actions = [button('Playground', { icon: 'play', size: 'sm', variant: 'ghost', title: 'Otwórz ten kod w Playground', onClick: () => ctx.navigate('playground', { code: b.code }) })];
          return h('div', { class: 'docs-block', dataset: { anchor } },
            codeBlock(b.code || '', b.lang || 'cpp', { title: b.title || undefined, actions, lineNumbers: (b.code || '').split('\n').length > 6 }));
        }
        case 'table':
          return h('div', { class: 'docs-block', dataset: { anchor } }, b.title ? h('div', { class: 'docs-table-title' }, b.title) : null,
            h('div', { class: 'table-wrap' }, h('table', { class: 'tbl compact docs-table' },
              h('thead', null, h('tr', null, (b.headers || []).map((x) => h('th', null, inlineMd(x))))),
              h('tbody', null, (b.rows || []).map((row) => h('tr', null, row.map((x) => h('td', null, inlineMd(x)))))))));
        case 'tip': return h('div', { class: 'docs-block', dataset: { anchor } }, callout(b.md || '', 'tip'));
        case 'warn': return h('div', { class: 'docs-block', dataset: { anchor } }, callout(b.md || '', 'warn'));
        default: {
          const md = markdown(b.md || '');
          md.querySelectorAll('h1, h2, h3, h4').forEach((hd, j) => { hd.dataset.anchor = `${anchor}-h${j}`; });
          return h('div', { class: 'docs-block docs-text', dataset: { anchor } }, md);
        }
      }
    }

    function renderQA(qa, j) {
      const qs = Array.isArray(qa.q) ? qa.q : [qa.q];
      const first = cap(qs[0] || '');
      return h('details', { class: 'docs-qa', dataset: { anchor: `qa-${j}` } },
        h('summary', null, icon('chevron-right', 14, { strokeWidth: 2.4 }), h('span', { class: 'grow' }, first),
          qs.length > 1 ? h('span', { class: 'faint small' }, `+${qs.length - 1}`) : null),
        h('div', { class: 'docs-qa-body' },
          qs.length > 1 ? h('div', { class: 'docs-qa-variants' }, qs.slice(1).map((v) => h('span', { class: 'docs-variant' }, v))) : null,
          markdown(qa.a || ''),
          qa.code ? codeBlock(qa.code, qa.lang || 'cpp', {
            actions: [button('Playground', { icon: 'play', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('playground', { code: qa.code }) })],
          }) : null,
          h('div', { class: 'row gap-s' }, h('button', { class: 'linklike small', onclick: () => ctx.navigate('assistant', { q: qs[0] }) }, 'Zapytaj asystenta o to'))));
    }

    function renderToc(items) {
      clear(tocBox);
      if (!items.length) return;
      tocBox.append(h('div', { class: 'docs-toc-title' }, icon('list-tree', 14), 'Na tej stronie'),
        h('div', { class: 'docs-toc-items' }, items.map((t) => h('button', {
          class: `docs-toc-item d${t.depth || 0} k-${t.kind}`, onclick: () => scrollToAnchor(t.anchor), title: t.title,
        }, t.title))),
        h('button', { class: 'linklike small docs-toc-top', onclick: () => scrollToAnchor(null) }, icon('chevron-down', 12), 'Do góry'));
    }

    loadTree().then(() => {
      // artykuł zamówiony przez onShow({id}) zanim drzewo się wczytało
      if (this._pendingOpen) { const p = this._pendingOpen; this._pendingOpen = null; openArticle(p.id, p.anchor); }
    });

    function inlineMd(x) {
      const n = markdown(String(x ?? ''));
      n.classList.add('docs-inline-md');
      return n;
    }
  },

  onShow(params) {
    if (params && params.id) {
      if (this._open && this._st?.tree) this._open(params.id, params.anchor);
      else this._pendingOpen = { id: params.id, anchor: params.anchor };
    }
  },
};

function cap(s) {
  s = String(s || '').trim();
  if (!s) return s;
  s = s[0].toUpperCase() + s.slice(1);
  if (/^(jak|co|czy|ile|dlaczego|kiedy|gdzie|który|która|które|jaki|jaka|jakie|czym|po co|w jaki)\b/i.test(s) && !s.endsWith('?')) s += '?';
  return s;
}


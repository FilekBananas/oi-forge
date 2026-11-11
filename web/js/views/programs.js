// (e) Moje programy — wszystkie Twoje rozwiązania za 100 pkt z SIO2 + MEGA SMART wyszukiwarka (bez AI):
// literówki („binserch”), synonimy, tematy rund, tagi algorytmów z kodu, szukanie po kodzie i po treści zadania.
import {
  h, icon, button, viewHeader, card, tabs, field, input, select, checkbox, segmented, badge, toast, emptyState,
  codeBlock, copyButton, progressBar, stat, kv, clear, debounce, callout, fileDrop, readFileText, plural, timeAgo,
  spinner, confirmDialog,
} from '../ui.js';
import { api, local } from '../api.js';
import { barChart } from '../charts.js';
import { extractPdfText } from '../pdf.js';

const EXAMPLES = ['binserch', 'dikstra', 'drzewo przedziałowe', 'find union', 'plecak', 'sumy prefiksowe', 'toposort',
  'kopiec', 'lower_bound', 'najkrótsza ścieżka', 'priority_queue', 'sito'];
const SOURCE_ICON = { round: 'list-tree', statement_topic: 'file-text', name: 'bookmark', code: 'code-xml', statement: 'file-text', import: 'folder' };

const norm = (s) => String(s || '').toLowerCase().replace(/ł/g, 'l').normalize('NFD').replace(/[\u0300-\u036f]/g, '');

/** Tekst z **pogrubieniami** (z backendu) → węzły z <mark>. */
function marked(text) {
  const out = [];
  String(text || '').split(/\*\*(.+?)\*\*/g).forEach((part, i) => {
    if (!part) return;
    out.push(i % 2 ? h('mark', { class: 'pg-mark' }, part) : document.createTextNode(part));
  });
  return out;
}

/** Podświetla w tekście dosłowne wystąpienia `needle` (bez rozróżniania wielkości liter). */
function markLiteral(text, needle) {
  const t = String(text || '');
  const n = String(needle || '').trim();
  if (n.length < 2) return [t];
  const lt = t.toLowerCase();
  const ln = n.toLowerCase();
  const out = [];
  let pos = 0;
  for (;;) {
    const i = lt.indexOf(ln, pos);
    if (i < 0) break;
    if (i > pos) out.push(t.slice(pos, i));
    out.push(h('mark', { class: 'pg-mark' }, t.slice(i, i + n.length)));
    pos = i + n.length;
  }
  out.push(t.slice(pos));
  return out;
}

/** Podświetla słowa zaczynające się od podanych stemów (jak w wyszukiwarce). */
function markStems(text, stems) {
  const ss = (stems || []).filter((s) => s && s.length >= 3).map(norm);
  if (!ss.length) return [String(text || '')];
  const out = [];
  const re = /[\p{L}\p{N}_]+/gu;
  const t = String(text || '');
  let last = 0;
  let m;
  while ((m = re.exec(t))) {
    const w = norm(m[0]);
    if (ss.some((s) => w.startsWith(s))) {
      if (m.index > last) out.push(t.slice(last, m.index));
      out.push(h('mark', { class: 'pg-mark' }, m[0]));
      last = m.index + m[0].length;
    }
  }
  out.push(t.slice(last));
  return out;
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function fileToBase64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(',').pop());
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

function fmtDate(t) {
  if (!t) return '—';
  const [d, tm] = String(t).split(' ');
  const [y, mo, da] = d.split('-');
  return `${da}.${mo}.${y}${tm ? ' ' + tm.slice(0, 5) : ''}`;
}

const MONTHS = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];
const monthLabel = (m) => { const [y, mo] = m.split('-'); return `${MONTHS[+mo - 1]} ${y.slice(2)}`; };

export default {
  id: 'programs',
  title: 'Moje programy',
  icon: 'library',
  group: 'Moje programy',
  order: 1,
  description: 'Twoje rozwiązania za 100 pkt (z Szkopułu, dowolnej strony SIO2 albo z folderu) i sprytna wyszukiwarka: wpisz „binserch”, „dijkstra” albo „lower_bound”.',
  keywords: ['sio2', 'moje rozwiązania', 'wyszukiwarka', 'szukaj programu', 'binsearch', 'synchronizacja', 'szkopuł', 'archiwum'],

  render(root, ctx) {
    const self = this;
    const st = {
      q: local.get('programs:q', ''),
      filters: local.get('programs:filters', { contest: [], topic: [], tag: [], from: '', to: '' }),
      results: [], selected: null, sel: -1, seq: 0, facets: null, count: null, detail: null, status: null,
      syncCtl: null, extracting: false,
    };
    for (const k of ['contest', 'topic', 'tag']) if (!Array.isArray(st.filters[k])) st.filters[k] = [];
    // elementy zakładek (deklarowane PRZED tabs(), bo zakładka wyszukiwarki renderuje się od razu)
    let qInput, metaLine, chipsBox, listBox, detailBox, examplesBox, statsBox;
    let syncUI = null;
    const runSearchDebounced = debounce(() => runSearch(), 170);

    // ------------------------------------------------------------------ nagłówek + zakładki
    const countBadge = badge('…', 'accent', 'library');
    const header = viewHeader(this, countBadge,
      button('Importuj swoje programy', { icon: 'refresh-cw', variant: 'primary', onClick: () => tb.select('sync') }));
    const tb = tabs([
      { id: 'search', label: 'Wyszukiwarka', icon: 'search', render: renderSearchTab },
      { id: 'stats', label: 'Statystyki', icon: 'activity', render: renderStatsTab },
      { id: 'sync', label: 'Synchronizacja i import', icon: 'refresh-cw', render: renderSyncTab },
    ], { active: 'search', onChange: (id) => { if (id === 'stats') loadStats(); if (id === 'sync') loadStatus(); } });
    root.append(header, tb.el);

    // ================================================================== WYSZUKIWARKA
    function renderSearchTab() {
      qInput = input({
        value: st.q, placeholder: 'Szukaj w swoich programach… np. binserch, dijkstra, drzewo przedziałowe, lower_bound, najkrótsza ścieżka',
        class: 'pg-q', attrs: { autocomplete: 'off', 'aria-label': 'Szukaj w moich programach' },
        onInput: (v) => { st.q = v; local.set('programs:q', v); runSearchDebounced(); },
      });
      qInput.addEventListener('keydown', onSearchKey);
      const clearBtn = button(null, { icon: 'x', variant: 'ghost', size: 'sm', title: 'Wyczyść (Esc)', onClick: () => { qInput.value = ''; st.q = ''; local.set('programs:q', ''); runSearch(); qInput.focus(); } });
      const hero = h('div', { class: 'pg-hero' }, h('div', { class: 'pg-search' }, icon('search', 20), qInput, clearBtn));
      metaLine = h('div', { class: 'pg-meta' });
      examplesBox = h('div', { class: 'pg-examples' }, h('span', { class: 'muted small' }, 'Spróbuj:'),
        EXAMPLES.map((ex) => h('button', { class: 'chip', type: 'button', onclick: () => { qInput.value = ex; st.q = ex; local.set('programs:q', ex); runSearch(); } }, ex)));
      chipsBox = h('div', { class: 'pg-filters' });
      listBox = h('div', { class: 'pg-list', role: 'listbox' });
      detailBox = h('div', { class: 'pg-detail' }, emptyState({ icon: 'arrow-right', title: 'Wybierz program', text: 'Kliknij wynik, aby zobaczyć kod, treść zadania, tagi i podobne programy.' }));
      setTimeout(() => qInput.focus(), 60);
      runSearch();
      return h('div', { class: 'stack' }, hero, metaLine, examplesBox, chipsBox,
        h('div', { class: 'pg-layout' }, h('div', { class: 'pg-col-list' }, listBox), detailBox));
    }

    function onSearchKey(e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!st.results.length) return;
        st.sel = Math.max(0, Math.min(st.results.length - 1, st.sel + (e.key === 'ArrowDown' ? 1 : -1)));
        openResult(st.results[st.sel], st.sel);
        listBox.children[st.sel]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        runSearchDebounced.cancel();
        runSearch().then(() => { if (st.results.length) openResult(st.results[Math.max(0, st.sel)], Math.max(0, st.sel)); });
      } else if (e.key === 'Escape') {
        qInput.value = ''; st.q = ''; local.set('programs:q', ''); runSearch();
      }
    }

    function filterParams() {
      const f = st.filters;
      const p = {};
      if (f.contest.length) p.contest = f.contest.join(',');
      if (f.topic.length) p.topic = f.topic.join(',');
      if (f.tag.length) p.tag = f.tag.join(',');
      if (f.from) p.from = f.from;
      if (f.to) p.to = f.to;
      return p;
    }

    async function runSearch() {
      if (!listBox) return;
      const seq = ++st.seq;
      const q = (st.q || '').trim();
      examplesBox.hidden = !!q;
      if (!listBox.children.length || listBox.querySelector('.empty')) {
        clear(listBox).append(h('div', { class: 'pg-loading' }, spinner(18), h('span', { class: 'muted' }, 'Ładuję indeks programów… (pierwsze uruchomienie trwa kilka sekund)')));
      }
      let r;
      try {
        r = await api.get('/api/programs/search', { q, k: 60, ...filterParams() }, { quiet: true });
      } catch (e) {
        if (seq !== st.seq) return;
        clear(listBox).append(emptyState({ icon: 'triangle-alert', title: 'Wyszukiwarka niedostępna', text: e.message }));
        return;
      }
      if (seq !== st.seq) return;
      st.results = r.results || [];
      st.facets = r.facets;
      st.count = r.count;
      countBadge.lastChild.textContent = `${r.count ?? 0} ${plural(r.count ?? 0, 'program', 'programy', 'programów')}`;
      renderMeta(r, q);
      renderChips();
      renderList(q, r);
      if (st.pendingOpen) {
        const id = st.pendingOpen;
        st.pendingOpen = null;
        const i = st.results.findIndex((x) => x.id === id);
        openResult(i >= 0 ? st.results[i] : { id }, i);
      } else if (st.results.length && (!st.selected || !st.results.some((x) => x.id === st.selected))) {
        st.sel = 0;
        openResult(st.results[0], 0);
      } else {
        st.sel = st.results.findIndex((x) => x.id === st.selected);
        markActive();
      }
    }

    function renderMeta(r, q) {
      clear(metaLine);
      if (!q && !Object.keys(filterParams()).length) {
        metaLine.append(h('span', null, `Wszystkie programy (${r.total}) — od najnowszych.`));
        return;
      }
      metaLine.append(h('span', null, h('b', null, String(r.total)), ` ${plural(r.total, 'wynik', 'wyniki', 'wyników')}`),
        h('span', { class: 'faint' }, ` · ${r.took_ms} ms`));
      if (r.concepts?.length) {
        metaLine.append(h('span', { class: 'pg-meta-sep' }, '·'), h('span', { class: 'muted' }, 'rozpoznano: '),
          r.concepts.map((c) => badge(c.name, 'accent', 'sparkles')));
      }
      if (r.corrections?.length) {
        metaLine.append(h('span', { class: 'pg-meta-sep' }, '·'), h('span', { class: 'muted' }, 'poprawiono: '),
          r.corrections.map(([a, b]) => h('span', { class: 'pg-fix' }, h('s', null, a), ' → ', h('b', null, b))));
      }
      if (r.code_query) {
        metaLine.append(h('span', { class: 'pg-meta-sep' }, '·'),
          r.literal_hits ? badge(`dokładnie w kodzie: ${r.literal_hits}`, 'success', 'code-xml')
            : r.partial_hits ? badge('brak dokładnych trafień w kodzie — pokazuję najbliższe', 'warn', 'code-xml')
              : badge('szukanie po kodzie', 'info', 'code-xml'));
      }
    }

    function renderChips() {
      clear(chipsBox);
      const fc = st.facets || {};
      const groups = [
        ['contest', 'Konkurs', 'trophy', (fc.contests || []).map((x) => ({ id: x.id, name: x.name, count: x.count }))],
        ['topic', 'Temat rundy', 'list-tree', fc.topics || []],
        ['tag', 'Algorytm', 'brain-circuit', fc.tags || []],
      ];
      for (const [key, label, ic, items] of groups) {
        const active = st.filters[key];
        const shown = items.slice(0, key === 'contest' ? 12 : 14);
        // zaznaczone filtry zawsze widoczne (nawet jeśli nie ma ich w bieżących facetach)
        for (const a of active) if (!shown.some((x) => String(x.id) === String(a))) shown.unshift({ id: a, name: a, count: 0 });
        if (!shown.length) continue;
        const more = items.length > shown.length ? h('button', { class: 'chip pg-more', type: 'button', onclick: (e) => { e.target.remove(); row.append(...items.slice(shown.length).map(mk)); } }, `+${items.length - shown.length}`) : null;
        const mk = (it) => h('button', {
          class: ['chip', active.includes(String(it.id)) ? 'active' : ''].join(' '), type: 'button', title: it.name,
          onclick: () => toggleFilter(key, String(it.id)),
        }, h('span', { class: 'ellipsis', style: 'max-width:240px' }, it.name), it.count ? h('span', { class: 'pg-chip-n' }, String(it.count)) : null);
        const row = h('div', { class: 'pg-chiprow' }, shown.map(mk), more);
        chipsBox.append(h('div', { class: 'pg-fgroup' }, h('div', { class: 'pg-flabel' }, icon(ic, 14), label), row));
      }
      const from = input({ type: 'month', value: st.filters.from || '', size: 'sm', onChange: (v) => { st.filters.from = v; saveFilters(); runSearch(); } });
      const to = input({ type: 'month', value: st.filters.to || '', size: 'sm', onChange: (v) => { st.filters.to = v; saveFilters(); runSearch(); } });
      const anyActive = st.filters.contest.length + st.filters.topic.length + st.filters.tag.length + (st.filters.from ? 1 : 0) + (st.filters.to ? 1 : 0);
      chipsBox.append(h('div', { class: 'pg-fgroup' }, h('div', { class: 'pg-flabel' }, icon('clock', 14), 'Okres'),
        h('div', { class: 'pg-chiprow' }, h('span', { class: 'muted small' }, 'od'), from, h('span', { class: 'muted small' }, 'do'), to,
          anyActive ? button('Wyczyść filtry', { icon: 'x', size: 'sm', variant: 'ghost', onClick: () => { st.filters = { contest: [], topic: [], tag: [], from: '', to: '' }; saveFilters(); runSearch(); } }) : null)));
    }

    function toggleFilter(key, id) {
      const arr = st.filters[key];
      const i = arr.indexOf(id);
      if (i >= 0) arr.splice(i, 1); else arr.push(id);
      saveFilters();
      runSearch();
    }
    function saveFilters() { local.set('programs:filters', st.filters); }

    function renderList(q, r) {
      clear(listBox);
      if (!st.results.length) {
        if (!r.count) {
          listBox.append(emptyState({
            icon: 'library', title: 'Nie masz jeszcze programów',
            text: 'Zaimportuj swoje rozwiązania: zaloguj się do Szkopułu albo dowolnej strony SIO2, albo wskaż folder z programami.',
            action: button('Przejdź do synchronizacji', { icon: 'refresh-cw', variant: 'primary', onClick: () => tb.select('sync') }),
          }));
        } else {
          listBox.append(emptyState({ icon: 'search', title: 'Nic nie znaleziono', text: `Brak programów dla „${q}”${Object.keys(filterParams()).length ? ' przy wybranych filtrach' : ''}. Spróbuj innego słowa — np. nazwy algorytmu, funkcji z kodu albo fragmentu treści.` }));
        }
        clear(detailBox).append(emptyState({ icon: 'search', title: 'Brak wyniku do pokazania' }));
        return;
      }
      st.results.forEach((res, i) => listBox.append(resultItem(res, i, q, r)));
      if (r.total > st.results.length) listBox.append(h('div', { class: 'muted small center pad' }, `Pokazuję ${st.results.length} z ${r.total} — zawęź zapytanie albo użyj filtrów.`));
    }

    function resultItem(res, i, q, r) {
      const snip = res.snippet ? h('div', { class: ['pg-snip', res.snippet_kind === 'kod' ? 'code' : ''].join(' ') },
        res.snippet_kind === 'kod' ? [res.snippet_line ? h('span', { class: 'pg-ln' }, `L${res.snippet_line}`) : null,
          h('span', { class: 'mono' }, r.code_query ? markLiteral(res.snippet, q) : markStems(res.snippet, res.matched))]
          : marked(res.snippet)) : null;
      const why = (res.why || []).slice(0, 2);
      return h('div', {
        class: 'pg-item', role: 'option', tabindex: -1, dataset: { id: res.id },
        onclick: () => { st.sel = i; openResult(res, i); },
      },
      h('div', { class: 'pg-item-head' },
        h('div', { class: 'pg-item-title ellipsis' }, res.problem_name || res.problem_short),
        res.problem_short ? h('span', { class: 'badge mono' }, res.problem_short) : null,
        h('span', { class: 'grow' }),
        res.score === 100 ? badge('100', 'success') : res.score != null ? badge(String(res.score), 'warn') : badge('import', 'info')),
      h('div', { class: 'pg-item-sub ellipsis' }, res.contest_name, res.round ? h('span', { class: 'faint' }, ' · ') : null,
        res.round ? h('span', { class: 'pg-round' }, res.round) : null, h('span', { class: 'faint' }, ' · ' + fmtDate(res.time).split(' ')[0])),
      res.tags?.length ? h('div', { class: 'pg-tags' }, res.tags.slice(0, 4).map((t, k) => badge(t, k === 0 && r.concepts?.length ? 'accent' : 'neutral'))) : null,
      snip,
      why.length ? h('div', { class: 'pg-why' }, icon('sparkles', 13), h('span', { class: 'ellipsis' }, why.join(' · '))) : null);
    }

    function markActive() {
      [...listBox.querySelectorAll('.pg-item')].forEach((el) => el.classList.toggle('active', el.dataset.id === st.selected));
    }

    // ------------------------------------------------------------------ szczegóły programu
    async function openResult(res, i) {
      if (!res) return;
      st.selected = res.id;
      if (i !== undefined && i >= 0) st.sel = i;
      markActive();
      clear(detailBox).append(h('div', { class: 'pg-loading' }, spinner(18), h('span', { class: 'muted' }, 'Wczytuję program…')));
      let p;
      try { p = (await api.get('/api/programs/get', { id: res.id }, { quiet: true })).program; } catch (e) {
        clear(detailBox).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się wczytać programu', text: e.message }));
        return;
      }
      if (st.selected !== res.id) return;
      st.detail = p;
      renderDetail(p, res);
    }

    function renderDetail(p, res) {
      clear(detailBox);
      const code = p.code || '';
      const lang = p.lang === 'python' ? 'python' : 'cpp';
      const hlLine = res?.snippet_kind === 'kod' && res.snippet_line ? res.snippet_line : null;
      const actions = h('div', { class: 'pg-actions' },
        button('Sprawdzarka', { icon: 'bug', size: 'sm', title: 'Statyczna analiza i sanitizery', onClick: () => ctx.navigate('checker', { code }) }),
        button('Objaśniacz', { icon: 'lightbulb', size: 'sm', title: 'Wyjaśnij krok po kroku', onClick: () => ctx.navigate('explain', { code }) }),
        button('Playground', { icon: 'play', size: 'sm', onClick: () => ctx.navigate('playground', { code }) }),
        button('Testerka', { icon: 'flask-conical', size: 'sm', title: 'Użyj jako rozwiązania w testerce', onClick: () => ctx.navigate('tester', { solution: code }) }),
        button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', title: 'Zadanie w edytorze: treść, testy z przykładów i ten kod — uruchomisz go u siebie', onClick: () => ctx.navigate('workspace', { program_id: p.id }) }),
        p.url ? button('Otwórz w SIO2', { icon: 'external-link', size: 'sm', variant: 'ghost', onClick: () => window.open(p.url, '_blank', 'noopener') }) : null,
        copyButton(() => code, { label: 'Kopiuj kod' }));
      const head = h('div', { class: 'pg-dhead' },
        h('div', { class: 'row gap-s', style: 'align-items:flex-start' },
          h('div', { class: 'grow' },
            h('div', { class: 'pg-dtitle' }, p.problem_name || p.problem_short, p.problem_short ? h('span', { class: 'badge mono' }, p.problem_short) : null),
            h('div', { class: 'pg-dsub' }, icon('trophy', 13), p.contest_name || '—', p.round ? [h('span', { class: 'faint' }, ' · '), icon('list-tree', 13), p.round] : null)),
          p.score === 100 ? badge('100 pkt', 'success', 'circle-check') : p.score != null ? badge(`${p.score} pkt`, 'warn') : badge('import', 'info', 'folder')),
        res?.why?.length ? h('div', { class: 'pg-whybox' }, icon('sparkles', 14), h('div', null, h('b', null, 'Dlaczego to trafienie: '), res.why.join(' · '))) : null,
        p.tags?.length ? h('div', { class: 'pg-tags' }, (p.tags_detail || []).map((t) => {
          const b = badge(t.name, t.source === 'round' ? 'accent' : t.source === 'code' ? 'info' : 'neutral', SOURCE_ICON[t.source] || 'bookmark');
          b.title = `${t.source_label}${t.evidence ? ': ' + t.evidence : ''} (pewność ${Math.round(100 * t.strength)}%)`;
          b.style.cursor = 'pointer';
          b.addEventListener('click', () => { if (!st.filters.tag.includes(t.id)) toggleFilter('tag', t.id); });
          return b;
        })) : null,
        actions);

      const codeTab = () => {
        const cb = codeBlock(code, lang, { lineNumbers: true, highlightLines: hlLine ? [hlLine] : [], maxHeight: 'calc(100vh - 330px)',
          title: `${lang === 'python' ? 'Python' : 'C++'} · ${p.lines || code.split('\n').length} linii · ${fmtDate(p.time)}` });
        if (hlLine) {
          requestAnimationFrame(() => {
            const pre = cb.querySelector('pre');
            const line = cb.querySelector('.hl-line');
            if (pre && line) pre.scrollTop = Math.max(0, line.offsetTop - pre.offsetTop - pre.clientHeight / 2);
          });
        }
        return cb;
      };
      const statementTab = () => {
        const box = h('div', { class: 'stack' });
        if (p.statement) {
          box.append(h('div', { class: 'pg-statement' }, markStems(p.statement, res?.matched || [])));
        } else if (p.has_pdf) {
          box.append(callout('Treść jest w PDF i nie ma jeszcze wersji tekstowej. Wyciągnę tekst w przeglądarce (pdf.js) — wtedy będzie też przeszukiwana.', 'info'),
            h('div', { class: 'row gap-s' },
              button('Wyciągnij tekst z PDF', { icon: 'file-text', variant: 'primary', onClick: async () => { await extractAllPending(); await reopen(); } }),
              button('Pokaż PDF', { icon: 'eye', onClick: () => showPdf(box, p) })));
        } else {
          box.append(emptyState({ icon: 'file-text', title: 'Brak treści zadania', text: 'Treść pobiera się podczas importu z Szkopułu / SIO2 (PDF albo HTML).' }));
        }
        if (p.statement && p.has_pdf) box.append(h('div', null, button('Pokaż oryginalny PDF', { icon: 'eye', size: 'sm', variant: 'ghost', onClick: (e) => { e.target.closest('button').remove(); showPdf(box, p); } })));
        if (p.problem_url) box.append(h('div', { class: 'muted small' }, 'Zadanie w SIO2: ', h('a', { href: p.problem_url, target: '_blank', rel: 'noopener' }, p.problem_url)));
        return box;
      };
      const tagsTab = () => {
        const rows = (p.tags_all || []).map((t) => h('div', { class: 'pg-tagrow' },
          h('div', { class: 'pg-tagname' }, icon(SOURCE_ICON[t.source] || 'bookmark', 14), t.name),
          h('div', { class: 'pg-tagbar' }, h('div', { style: { width: `${Math.round(100 * t.strength)}%` } })),
          h('div', { class: 'muted small' }, `${t.source_label}${t.evidence ? ': ' + t.evidence : ''}${t.via ? ' (przez: ' + t.via + ')' : ''}`)));
        const algos = (p.algorithms || []).map((a) => h('li', null, h('b', null, a.name), ` — ${Math.round(100 * a.confidence)}%`,
          a.evidence ? h('span', { class: 'muted' }, ` (${a.evidence})`) : null, a.lines?.length ? h('span', { class: 'faint' }, ` · linie ${a.lines.slice(0, 5).join(', ')}`) : null,
          h('span', { class: 'faint small' }, ` · ${a.via || ''}`)));
        return h('div', { class: 'stack' },
          rows.length ? h('div', { class: 'stack gap-s' }, rows) : emptyState({ icon: 'brain-circuit', title: 'Brak tagów' }),
          algos.length ? h('div', null, h('div', { class: 'section-title' }, 'Wykryte w kodzie'), h('ul', { class: 'pg-algos' }, algos)) : null,
          p.stl?.length ? h('div', null, h('div', { class: 'section-title' }, 'Użyte z STL'), h('div', { class: 'pg-tags' }, p.stl.map((s) => h('button', { class: 'chip mono', type: 'button', onclick: () => searchFor(s) }, s)))) : null,
          p.defines?.length ? h('div', null, h('div', { class: 'section-title' }, 'Makra i aliasy'), codeBlock(p.defines.join('\n'), 'cpp', { head: false })) : null,
          kv([['Konkurs', p.contest_name], ['Runda', p.round || '—'], ['Temat z treści', p.statement_topic || '—'], ['Zgłoszenie', p.submission_id],
            ['Data', fmtDate(p.time)], ['Status', p.status || '—'], ['Plik', h('span', { class: 'mono small' }, p.path)]]));
      };
      const similarTab = () => {
        const box = h('div', { class: 'stack gap-s' }, h('div', { class: 'pg-loading' }, spinner(16), h('span', { class: 'muted' }, 'Szukam podobnych…')));
        api.get('/api/programs/similar', { id: p.id, k: 10 }, { quiet: true }).then((r) => {
          clear(box);
          if (!r.results?.length) { box.append(emptyState({ icon: 'git-compare', title: 'Brak podobnych programów' })); return; }
          for (const s of r.results) {
            box.append(h('div', { class: 'pg-sim', onclick: () => { st.pendingOpen = null; openResult(s); } },
              h('div', { class: 'pg-simpct' }, `${Math.round(100 * s.similarity)}%`),
              h('div', { class: 'grow', style: 'min-width:0' },
                h('div', { class: 'row gap-s' }, h('b', { class: 'ellipsis' }, s.problem_name), s.problem_short ? h('span', { class: 'badge mono' }, s.problem_short) : null),
                h('div', { class: 'muted small ellipsis' }, `${s.contest_name}${s.round ? ' · ' + s.round : ''}`),
                h('div', { class: 'small pg-simwhy' }, (s.reasons || []).join(' · ')))));
          }
        }).catch((e) => { clear(box).append(emptyState({ icon: 'triangle-alert', title: 'Błąd', text: e.message })); });
        return box;
      };
      const dt = tabs([
        { id: 'code', label: 'Kod', icon: 'code-xml', render: codeTab },
        { id: 'st', label: 'Treść zadania', icon: 'file-text', badge: p.statement ? null : (p.has_pdf ? 'PDF' : null), render: statementTab },
        { id: 'tags', label: 'Tagi i szczegóły', icon: 'brain-circuit', render: tagsTab },
        { id: 'sim', label: 'Podobne', icon: 'git-compare', render: similarTab },
      ], { active: res?.snippet_kind === 'treść' ? 'st' : 'code', class: 'pg-dtabs' });
      detailBox.append(card({ body: h('div', { class: 'stack' }, head, dt.el), class: 'pg-dcard' }));
    }

    async function reopen() {
      if (!st.selected) return;
      const res = st.results.find((x) => x.id === st.selected) || { id: st.selected };
      await openResult(res);
    }

    function searchFor(q) {
      tb.select('search');
      if (qInput) { qInput.value = q; st.q = q; local.set('programs:q', q); runSearch(); }
    }

    async function showPdf(box, p) {
      const holder = h('div', { class: 'pg-pdf' }, h('div', { class: 'pg-loading' }, spinner(16), h('span', { class: 'muted' }, 'Wczytuję PDF…')));
      box.append(holder);
      try {
        const r = await api.get('/api/programs/statement/pdf', { id: p.id });
        const url = URL.createObjectURL(new Blob([b64ToBytes(r.b64)], { type: 'application/pdf' }));
        clear(holder).append(h('iframe', { src: url, title: 'Treść zadania (PDF)' }));
      } catch (e) { clear(holder).append(h('div', { class: 'muted' }, e.message)); }
    }

    // ================================================================== STATYSTYKI
    function renderStatsTab() {
      statsBox = h('div', { class: 'stack' }, h('div', { class: 'pg-loading' }, spinner(18), h('span', { class: 'muted' }, 'Liczę statystyki…')));
      return statsBox;
    }
    async function loadStats() {
      if (!statsBox) return;
      let s;
      try { s = (await api.get('/api/programs/stats', null, { quiet: true })).stats; } catch (e) {
        clear(statsBox).append(emptyState({ icon: 'triangle-alert', title: 'Nie udało się policzyć statystyk', text: e.message }));
        return;
      }
      clear(statsBox);
      const t = s.totals;
      if (!t.programs) {
        statsBox.append(emptyState({ icon: 'activity', title: 'Brak danych', text: 'Zaimportuj swoje programy (Szkopuł, SIO2 albo folder), żeby zobaczyć statystyki.', action: button('Import', { icon: 'refresh-cw', variant: 'primary', onClick: () => tb.select('sync') }) }));
        return;
      }
      const clickable = (items, key) => h('div', { class: 'pg-tags' }, items.slice(0, 30).map((x) => h('button', { class: 'chip', type: 'button', onclick: () => {
        st.filters = { contest: [], topic: [], tag: [], from: '', to: '' };
        st.filters[key].push(String(key === 'topic' ? x.label : x.id));
        saveFilters(); tb.select('search');
        if (qInput) { qInput.value = ''; st.q = ''; local.set('programs:q', ''); runSearch(); }
      } }, x.label, h('span', { class: 'pg-chip-n' }, String(x.value)))));
      statsBox.append(
        h('div', { class: 'grid-4' },
          stat({ label: 'Programy za 100 pkt', value: t.programs, icon: 'circle-check', tone: 'success', sub: `${t.lines.toLocaleString('pl-PL')} linii kodu` }),
          stat({ label: 'Rozwiązane zadania', value: t.problems, icon: 'target', sub: `w ${t.contests} ${plural(t.contests, 'konkursie', 'konkursach', 'konkursach')}` }),
          stat({ label: 'Tematy i algorytmy', value: `${t.topics} / ${t.algorithms}`, icon: 'brain-circuit', sub: 'tematy rund / wykryte algorytmy' }),
          stat({ label: 'Treści zadań', value: t.with_statement, icon: 'file-text', sub: t.pending_statements ? `${t.pending_statements} PDF czeka na tekst` : `od ${fmtDate(s.first).split(' ')[0]} do ${fmtDate(s.last).split(' ')[0]}`, tone: t.pending_statements ? 'warn' : undefined })),
        h('div', { class: 'grid-2' },
          card({ title: 'Zadania per temat rundy', icon: 'list-tree', subtitle: 'Kliknij temat, aby zobaczyć programy', body: h('div', { class: 'stack' }, barChart(s.by_topic.slice(0, 16)), clickable(s.by_topic, 'topic')) }),
          card({ title: 'Algorytmy (z tematów i kodu)', icon: 'brain-circuit', subtitle: 'Liczba różnych zadań', body: h('div', { class: 'stack' }, barChart(s.by_algorithm.slice(0, 16)), clickable(s.by_algorithm, 'tag')) })),
        card({ title: 'Oś czasu', icon: 'clock', subtitle: 'Zaakceptowane programy w kolejnych miesiącach', body: barChart(s.timeline.map((m) => ({ label: monthLabel(m.month), value: m.count })), { horizontal: false, height: 190 }) }),
        h('div', { class: 'grid-2' },
          card({ title: 'Konkursy', icon: 'trophy', body: h('div', { class: 'stack' }, barChart(s.by_contest), clickable(s.by_contest, 'contest')) }),
          card({ title: 'Kiedy programujesz', icon: 'timer', subtitle: 'Dzień tygodnia i godzina wysłania', body: h('div', { class: 'stack' },
            barChart(s.by_weekday, { horizontal: false, height: 120 }),
            barChart(s.by_hour.filter((x, i) => i >= 6 || x.value), { horizontal: false, height: 110 })) })),
        card({ title: 'Najczęściej używane w kodzie', icon: 'code-xml', body: h('div', { class: 'pg-tags' }, s.stl_top.map((x) => h('button', { class: 'chip mono', type: 'button', onclick: () => searchFor(x.label) }, x.label, h('span', { class: 'pg-chip-n' }, String(x.value))))) }));
    }

    // ================================================================== SYNCHRONIZACJA I IMPORT
    function renderSyncTab() {
      const SZKOPUL = 'https://szkopul.edu.pl';
      const base = input({ value: '', placeholder: 'https://sio2.twojaszkola.pl', mono: true });
      const baseField = field('Adres strony (dowolna strona działająca na systemie SIO2 / oioioi)', base, 'Wpisz adres, pod którym logujesz się do SIO2 — np. serwer Twojej szkoły albo kółka.');
      const srcMode = segmented([{ value: 'szkopul', label: 'Szkopuł', icon: 'graduation-cap' }, { value: 'sio2', label: 'Inna strona SIO2', icon: 'external-link' }], {
        value: 'szkopul', onChange: (v) => setSource(v, true),
      });
      function setSource(v, byUser) {
        baseField.hidden = v !== 'sio2';
        if (v === 'szkopul') base.value = SZKOPUL;
        else if (base.value === SZKOPUL) base.value = '';
        if (byUser) { base.dataset.touched = '1'; if (v === 'sio2') base.focus(); }
      }
      setSource('szkopul');
      const user = input({ placeholder: 'login (ten sam co na stronie)', attrs: { autocomplete: 'username' } });
      const pass = input({ type: 'password', placeholder: 'hasło (nie jest zapisywane)', attrs: { autocomplete: 'current-password' }, onEnter: () => startSync() });
      const cookie = input({ placeholder: 'sessionid=… (z narzędzi deweloperskich przeglądarki)', mono: true, onEnter: () => startSync() });
      const remember = checkbox('Zapamiętaj sesję na tym komputerze (tylko ciasteczko — hasło nigdy nie jest zapisywane)', { checked: true });
      const contests = input({ placeholder: 'np. kolo-1, oi32-etap1 (puste = wszystkie + archiwum zadań)', mono: true });
      const delay = input({ type: 'number', value: '0.35', attrs: { min: 0.1, max: 5, step: 0.05 } });
      const full = checkbox('Pełne skanowanie (przejrzyj ponownie wszystkie strony zgłoszeń)');
      const withSt = checkbox('Pobieraj treści zadań (PDF/HTML)', { checked: true });
      const credBox = h('div', { class: 'grid-2' }, field('Login', user), field('Hasło', pass));
      const cookieBox = h('div', { hidden: true }, field('Ciasteczko sesji', cookie, 'Zaloguj się w przeglądarce, otwórz narzędzia deweloperskie → Aplikacja/Storage → Cookies i skopiuj wartość „sessionid”.'));
      const mode = segmented([{ value: 'pass', label: 'Login i hasło', icon: 'keyboard' }, { value: 'cookie', label: 'Ciasteczko sesji', icon: 'clipboard-paste' }, { value: 'saved', label: 'Zapamiętana sesja', icon: 'history' }], {
        value: 'pass', onChange: (v) => { credBox.hidden = v !== 'pass'; cookieBox.hidden = v !== 'cookie'; savedBox.hidden = v !== 'saved'; },
      });
      const savedBox = h('div', { hidden: true, class: 'muted small' });
      const syncBtn = button('Synchronizuj', { icon: 'refresh-cw', variant: 'primary', onClick: () => startSync() });
      const cancelBtn = button('Przerwij', { icon: 'circle-stop', variant: 'danger', onClick: () => st.syncCtl?.cancel() });
      cancelBtn.hidden = true;
      const prog = progressBar({ label: 'Gotowe do synchronizacji' });
      prog.el.hidden = true;
      const log = h('div', { class: 'pg-log' });
      const summary = h('div');
      const statusLine = h('div', { class: 'pg-status muted small' }, spinner(14), ' Sprawdzam stan…');

      const syncCard = card({
        title: 'Import z Szkopułu lub SIO2', icon: 'refresh-cw', subtitle: 'Logujesz się i OI Forge pobiera wszystkie Twoje rozwiązania za 100 pkt — ze wszystkich konkursów i z archiwum zadań, przyrostowo, z tematami rund i treściami zadań.',
        body: h('div', { class: 'stack' }, statusLine,
          h('div', null, srcMode), baseField,
          h('div', null, mode), credBox, cookieBox, savedBox, remember,
          h('details', { class: 'pg-adv' }, h('summary', null, 'Opcje zaawansowane'), h('div', { class: 'stack mt-s' },
            field('Tylko wybrane konkursy (ID)', contests), h('div', { class: 'grid-2' }, field('Przerwa między żądaniami (s)', delay, 'Grzecznie dla serwera — nie schodź poniżej 0.2 s.'), h('div', { class: 'stack gap-s', style: 'justify-content:flex-end' }, withSt, full)))),
          h('div', { class: 'row gap-s' }, syncBtn, cancelBtn, h('span', { class: 'grow' }),
            h('span', { class: 'muted small' }, icon('info', 13), ' Python nie łączy się z serwerem? Użyj skryptu do konsoli poniżej.')),
          prog.el, log, summary),
      });

      // --- ekstrakcja PDF
      const pdfInfo = h('div', { class: 'muted small' }, '…');
      const pdfProg = progressBar({ label: '' });
      pdfProg.el.hidden = true;
      const pdfBtn = button('Wyciągnij tekst z PDF-ów', { icon: 'file-text', onClick: () => extractAllPending() });
      const pdfCard = card({ title: 'Treści w PDF → tekst', icon: 'file-text', subtitle: 'Tekst z PDF wyciąga przeglądarka (pdf.js, offline) — dzięki temu wyszukiwarka przeszukuje też treści.',
        body: h('div', { class: 'stack' }, pdfInfo, h('div', { class: 'row' }, pdfBtn), pdfProg.el) });

      // --- import
      const importResult = h('div');
      const drop = fileDrop({ accept: '.zip,.json', label: 'Upuść plik .zip (kody .cpp/.py) albo .json (ze skryptu do konsoli) — lub kliknij, aby wybrać', onFiles: (f) => importFile(f) });
      const folder = input({ placeholder: 'np. ~/Documents/olimpiada  albo  C:\\Users\\Ja\\oi', mono: true, onEnter: () => importFolder() });
      const importCard = card({ title: 'Import z folderu z programami', icon: 'upload', subtitle: 'Wskaż folder ze swoimi rozwiązaniami (albo upuść .zip). Nazwy zadań i tematy zgaduję z nazw plików, folderów (np. „Dijkstra/lot.cpp”) i komentarzy „// Zadanie: …”.',
        body: h('div', { class: 'stack' },
          callout('Po imporcie OI Forge sam dostraja się do Ciebie: wyszukiwarka programów, podpowiedzi w edytorze, doradca algorytmów i asystent uczą się z **Twoich** programów — lokalnie, **bez AI** i bez wysyłania czegokolwiek.', 'info'),
          drop,
          field('Folder z programami na tym komputerze', h('div', { class: 'row gap-s' }, h('div', { class: 'grow' }, folder), button('Importuj folder', { icon: 'folder', onClick: () => importFolder() }))),
          importResult) });

      // --- skrypt do konsoli
      const scriptBox = h('div', { class: 'stack' }, h('div', { class: 'muted small' }, 'Ładuję skrypt…'));
      const consoleCard = card({ title: 'Skrypt do konsoli przeglądarki (awaryjnie)', icon: 'square-terminal',
        subtitle: 'Gdy synchronizacja z programu nie może połączyć się z serwerem — przeglądarka, w której jesteś zalogowany, pobierze wszystko sama i zapisze plik JSON do zaimportowania.',
        body: scriptBox });

      syncUI = { base, srcMode, setSource, user, pass, cookie, remember, contests, delay, full, withSt, mode, savedBox, syncBtn, cancelBtn, prog, log, summary, statusLine, pdfInfo, pdfProg, pdfBtn, importResult, folder, scriptBox };
      loadStatus();
      loadScript();
      return h('div', { class: 'stack' }, h('div', { class: 'pg-sync-grid' }, h('div', { class: 'stack' }, syncCard, pdfCard), h('div', { class: 'stack' }, importCard, consoleCard)));
    }

    async function loadStatus() {
      if (!syncUI) return;
      let s;
      try { s = await api.get('/api/programs/sync/status', null, { quiet: true }); } catch (e) { syncUI.statusLine.textContent = e.message; return; }
      st.status = s;
      if (!syncUI.base.dataset.touched && s.base_url) {
        const isSzk = s.base_url.replace(/\/+$/, '') === (s.szkopul || 'https://szkopul.edu.pl');
        syncUI.srcMode.setValue?.(isSzk ? 'szkopul' : 'sio2');
        syncUI.setSource(isSzk ? 'szkopul' : 'sio2');
        syncUI.base.value = s.base_url;
      }
      syncUI.base.addEventListener('input', () => { syncUI.base.dataset.touched = '1'; }, { once: true });
      clear(syncUI.statusLine).append(icon('database', 14), ` W bazie: ${s.programs} ${plural(s.programs, 'program', 'programy', 'programów')}`,
        s.last_sync ? ` · ostatnia synchronizacja ${timeAgo(s.last_sync.at)}${s.last_sync.user ? ' (' + s.last_sync.user + ')' : ''}` : (s.fetched_at ? ` · dane z ${fmtDate(s.fetched_at.replace('T', ' ').slice(0, 16))}` : ''),
        s.pending_statements ? h('span', { class: 'tone-warn' }, ` · ${s.pending_statements} PDF bez tekstu`) : null);
      clear(syncUI.savedBox);
      if (s.saved_session) {
        syncUI.savedBox.append(h('div', { class: 'row gap-s' }, icon('circle-check', 14), `Zapamiętana sesja: ${s.saved_session.username || '?'} na ${s.saved_session.base_url} (od ${s.saved_session.saved_at}).`,
          button('Zapomnij sesję', { size: 'sm', variant: 'ghost', icon: 'trash-2', onClick: async () => { await api.post('/api/programs/sync/forget', {}); toast('Zapomniano sesję.', 'success'); loadStatus(); } })));
        if (!syncUI.mode.dataset.init) { syncUI.mode.dataset.init = '1'; syncUI.mode.setValue('saved'); syncUI.mode.querySelector('[data-value="saved"]').click(); }
      } else {
        syncUI.savedBox.append('Brak zapamiętanej sesji — zaloguj się loginem i hasłem albo wklej ciasteczko.');
      }
      syncUI.pdfInfo.textContent = s.pending_statements ? `${s.pending_statements} ${plural(s.pending_statements, 'treść czeka', 'treści czekają', 'treści czeka')} na wyciągnięcie tekstu z PDF.` : 'Wszystkie treści w PDF mają już wersję tekstową.';
      syncUI.pdfBtn.disabled = !s.pending_statements || st.extracting;
      tb.setBadge('sync', s.pending_statements ? String(s.pending_statements) : null, 'warn');
      if (s.running_job && !st.syncCtl) attachJob(s.running_job);
    }

    async function loadScript() {
      let s;
      try { s = await api.get('/api/programs/sync/script', { base_url: syncUI.base.value }, { quiet: true }); } catch (e) { clear(syncUI.scriptBox).append(h('div', { class: 'muted' }, e.message)); return; }
      const bm = h('a', { class: 'btn sm pg-bookmarklet', href: s.bookmarklet, title: 'Przeciągnij na pasek zakładek', onclick: (e) => { e.preventDefault(); toast('Przeciągnij ten przycisk na pasek zakładek przeglądarki, a potem kliknij zakładkę na stronie SIO2.', 'info', { timeout: 6000 }); } }, icon('bookmark', 14), 'OI Forge: eksport z SIO2');
      clear(syncUI.scriptBox).append(
        h('ol', { class: 'pg-steps' }, s.instructions.map((t) => h('li', null, t))),
        h('div', { class: 'row gap-s' }, copyButton(() => s.script, { label: 'Kopiuj skrypt', size: 'md' }), bm,
          h('span', { class: 'muted small' }, `pomija ${s.known} ${plural(s.known, 'znane zgłoszenie', 'znane zgłoszenia', 'znanych zgłoszeń')}`)),
        h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Pokaż kod skryptu (możesz sprawdzić, co robi)'),
          codeBlock(s.script, 'text', { title: 'JavaScript', maxHeight: 360 })));
    }

    function logLine(text, tone = 'info', ic = 'info') {
      const ui = syncUI;
      if (!ui) return;
      ui.log.append(h('div', { class: `pg-logline ${tone}` }, icon(ic, 13), h('span', null, text)));
      while (ui.log.children.length > 400) ui.log.firstChild.remove();
      ui.log.scrollTop = ui.log.scrollHeight;
    }

    function onSyncEvent(ev) {
      if (ev.type === 'log') logLine(ev.message, ev.level === 'warn' ? 'warn' : ev.level === 'success' ? 'success' : ev.level === 'error' ? 'danger' : 'info', ev.level === 'warn' ? 'triangle-alert' : ev.level === 'success' ? 'circle-check' : 'info');
      else if (ev.type === 'program') logLine(`+ ${ev.problem_name} (${ev.problem_short}) — ${ev.contest_name}${ev.round ? ' · ' + ev.round : ''}`, 'success', 'plus');
      else if (ev.type === 'statement') logLine(`treść ${ev.short}: ${ev.kind === 'pdf' ? 'PDF' : 'HTML'}`, 'muted', 'file-text');
      else if (ev.type === 'contests') logLine(`Konkursy: ${ev.items.map((c) => c.name).join(', ')}`, 'muted', 'trophy');
    }

    async function startSync() {
      const ui = syncUI;
      const m = ui.mode.value;
      const body = { base_url: ui.base.value.trim(), remember: ui.remember.checked, contests: ui.contests.value.trim(),
        delay: parseFloat(ui.delay.value) || 0.35, full: ui.full.checked, statements: ui.withSt.checked };
      if (m === 'pass') {
        if (!ui.user.value.trim() || !ui.pass.value) { toast('Podaj login i hasło do SIO2.', 'warn'); (ui.user.value.trim() ? ui.pass : ui.user).focus(); return; }
        body.username = ui.user.value.trim();
        body.password = ui.pass.value;
      } else if (m === 'cookie') {
        if (!ui.cookie.value.trim()) { toast('Wklej ciasteczko sessionid.', 'warn'); ui.cookie.focus(); return; }
        body.sessionid = ui.cookie.value.trim();
      }
      clear(ui.log); clear(ui.summary);
      const ctl = api.job('/api/programs/sync', body, { onEvent: onSyncEvent, onProgress: (p, msg) => ui.prog.set(p, msg), quiet: true });
      ui.pass.value = '';                    // hasło nie zostaje w formularzu
      watchJob(ctl);
    }

    function attachJob(jobId) {
      // zadanie już trwa (np. po przełączeniu widoku) — podepnij się do niego
      const ctl = { cancel: () => api.post(`/api/jobs/${jobId}/cancel`, {}, { quiet: true }) };
      ctl.promise = (async () => {
        let since = 0;
        for (;;) {
          await new Promise((r) => setTimeout(r, 300));
          const s = await api.get(`/api/jobs/${jobId}`, { since }, { quiet: true });
          since = s.next;
          (s.events || []).forEach(onSyncEvent);
          syncUI?.prog.set(s.progress, s.message);
          if (s.status === 'done') return s.result;
          if (s.status === 'error') throw new Error(s.error);
          if (s.status === 'cancelled') { const e = new Error('Anulowano.'); e.cancelled = true; throw e; }
        }
      })();
      watchJob(ctl);
    }

    async function watchJob(ctl) {
      const ui = syncUI;
      st.syncCtl = ctl;
      ui.syncBtn.hidden = true; ui.cancelBtn.hidden = false; ui.prog.el.hidden = false;
      ui.prog.indeterminate('Łączenie z SIO2…');
      try {
        const res = await ctl.promise;
        if (res?.cancelled) { logLine('Przerwano — pobrane programy zostały zapisane.', 'warn', 'triangle-alert'); return; }
        ui.prog.set(1, 'Gotowe');
        clear(ui.summary).append(callout(`**Synchronizacja zakończona.** Nowe programy: **${res.new_programs}** (razem ${res.total}). Konkursy: ${res.contests}, zgłoszenia przejrzane: ${res.submissions}, za 100 pkt: ${res.accepted}. Nowe treści: ${res.statements_new}${res.pending_statements ? `, PDF do ekstrakcji: ${res.pending_statements}` : ''}. Czas: ${res.duration} s.${res.errors?.length ? `\n\nOstrzeżenia (${res.errors.length}): ${res.errors.slice(0, 5).join('; ')}` : ''}`, res.errors?.length ? 'warn' : 'success'));
        toast(res.new_programs ? `Pobrano ${res.new_programs} ${plural(res.new_programs, 'nowy program', 'nowe programy', 'nowych programów')}!` : 'Wszystko aktualne — brak nowych programów.', 'success');
        afterDataChange();
        if (res.pending_statements) await extractAllPending();
      } catch (e) {
        if (e.cancelled) logLine('Przerwano synchronizację.', 'warn', 'triangle-alert');
        else { logLine(e.message, 'danger', 'circle-alert'); clear(ui.summary).append(callout(`**Błąd:** ${e.message}`, 'danger')); }
      } finally {
        st.syncCtl = null;
        ui.syncBtn.hidden = false; ui.cancelBtn.hidden = true;
        loadStatus();
      }
    }

    function afterDataChange() {
      runSearch();
      if (statsBox) loadStats();
      loadStatus();
    }

    async function extractAllPending() {
      if (st.extracting) return;
      st.extracting = true;
      const ui = syncUI;
      try {
        const { items } = await api.get('/api/programs/statements/pending', null, { quiet: true });
        if (!items.length) { toast('Brak PDF-ów do przetworzenia.', 'info'); return; }
        if (ui) { ui.pdfProg.el.hidden = false; ui.pdfBtn.disabled = true; }
        let done = 0, ok = 0;
        let batch = [];
        const flush = async () => {
          if (!batch.length) return;
          const r = await api.post('/api/programs/statements/text', { items: batch }, { quiet: true });
          ok += r.saved;
          batch = [];
        };
        for (const it of items) {
          ui?.pdfProg.set(done / items.length, `${done + 1}/${items.length}: ${it.problem_name || it.short}`);
          try {
            const r = await api.get('/api/programs/statement/pdf', { key: it.key }, { quiet: true });
            const text = await extractPdfText(b64ToBytes(r.b64).buffer);
            if (text && text.trim()) batch.push({ key: it.key, text });
          } catch (e) { console.warn('PDF', it.key, e); }
          done++;
          if (batch.length >= 10) await flush();
        }
        await flush();
        ui?.pdfProg.set(1, `Gotowe: ${ok} z ${items.length} treści zamienionych na tekst`);
        toast(`Wyciągnięto tekst z ${ok} ${plural(ok, 'PDF-a', 'PDF-ów', 'PDF-ów')}.`, ok ? 'success' : 'warn');
        afterDataChange();
      } catch (e) {
        toast(`Ekstrakcja PDF nie powiodła się: ${e.message}`, 'error');
      } finally {
        st.extracting = false;
        if (ui) ui.pdfBtn.disabled = false;
      }
    }

    function showImportResult(r, label) {
      const ui = syncUI;
      const list = (r.programs || []).slice(0, 12).map((p) => h('li', null, h('b', null, p.problem_name), ` (${p.problem_short})`,
        p.round ? h('span', { class: 'muted' }, ` · ${p.round}`) : null, p.matched ? badge('dopasowano treść', 'success') : null, h('span', { class: 'faint small' }, ` · ${p.origin}`)));
      clear(ui.importResult).append(callout(`**${label}:** dodano **${r.added}** ${plural(r.added, 'program', 'programy', 'programów')}` +
        `${r.duplicates ? `, pominięto duplikaty: ${r.duplicates}` : ''}${r.matched ? `, dopasowano do znanych zadań: ${r.matched}` : ''}` +
        `${r.statements_new ? `, nowe treści: ${r.statements_new}` : ''}${r.skipped?.length ? `, pominięte pliki: ${r.skipped.length}` : ''}. W bazie: ${r.total}.`, r.added ? 'success' : 'info'),
      list.length ? h('ul', { class: 'pg-implist' }, list) : null);
    }

    async function importFile(file) {
      const name = file.name || 'plik';
      const big = file.size > 80 * 1024 * 1024;
      if (big) { toast('Plik jest za duży (limit 80 MB).', 'error'); return; }
      try {
        let r;
        if (/\.json$/i.test(name)) {
          const text = await readFileText(file);
          r = await api.post('/api/programs/import/json', { json: text });
          showImportResult(r, `Import ${name}`);
          toast(`Zaimportowano ${r.added} ${plural(r.added, 'program', 'programy', 'programów')} z ${name}.`, 'success');
          afterDataChange();
          if (r.pending_statements) await extractAllPending();
        } else if (/\.zip$/i.test(name)) {
          const b64 = await fileToBase64(file);
          r = await api.post('/api/programs/import/zip', { name, b64 });
          showImportResult(r, `Import ${name}`);
          afterDataChange();
          if (r.export?.pending_statements) await extractAllPending();
        } else {
          toast('Obsługiwane są pliki .zip i .json.', 'warn');
        }
      } catch (e) { /* toast pokazany przez api */ }
    }

    async function importFolder() {
      const path = syncUI.folder.value.trim();
      if (!path) { toast('Podaj ścieżkę do folderu.', 'warn'); syncUI.folder.focus(); return; }
      if (!(await confirmDialog(`Zaimportować pliki .cpp/.py z folderu „${path}” (z podfolderami)? Pliki zostaną skopiowane do OI Forge.`, { title: 'Import folderu', ok: 'Importuj' }))) return;
      try {
        const r = await api.post('/api/programs/import/folder', { path });
        showImportResult(r, `Folder ${r.folder || path}`);
        afterDataChange();
      } catch (e) { /* toast */ }
    }

    // ------------------------------------------------------------------ reakcje na nawigację
    self._apply = (params = {}) => {
      if (params.tab) tb.select(params.tab);
      if (params.q !== undefined || params.id) {
        tb.select('search');
        if (params.id) st.pendingOpen = params.id;
        if (params.q !== undefined && qInput) { qInput.value = params.q; st.q = params.q; local.set('programs:q', params.q); }
        if (qInput) runSearch();
      }
    };
    self._apply(ctx.params || {});
  },

  onShow(params) {
    if (this._apply && params && Object.keys(params).length) this._apply(params);
  },
};

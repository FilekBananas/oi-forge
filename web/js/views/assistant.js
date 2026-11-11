// (a) Asystent — czatbot nad lokalną bazą wiedzy (BM25F + mała sieć neuronowa, bez LLM).
import {
  h, icon, button, iconButton, viewHeader, badge, codeBlock, markdown, callout, clear, emptyState, toast, debounce, kbd,
  copyButton, spinner, verdictBadge,
} from '../ui.js';
import { api, local } from '../api.js';

const STORE_KEY = 'assistant:chat';
const MAX_SAVED = 40;

const KIND_ICON = {
  qa: 'message-square', article: 'book-open', code: 'code-xml', text: 'align-left', tip: 'lightbulb', warn: 'triangle-alert',
  table: 'table', function: 'square-function', pitfall: 'triangle-alert', snippet: 'braces',
};
const INTENT_ICON = {
  howto: 'wand-sparkles', define: 'graduation-cap', complexity: 'gauge', code: 'code-xml', compare: 'git-compare',
  error: 'bug', compiler: 'languages', code_paste: 'file-code', task: 'file-text', general: 'message-square',
  run: 'play', command: 'terminal', clarify: 'lightbulb', tool: 'hammer',
};
const CLEAR_RX = /^\/(clear|wyczysc|wyczyść|reset|nowa|clean)\s*$/i;

export default {
  id: 'assistant',
  title: 'Asystent',
  icon: 'bot',
  group: 'Asystent',
  order: 1,
  layout: 'full',
  description: 'Zadaj pytanie po polsku — odpowiedź z lokalnej bazy wiedzy: kod, złożoność, pułapki i powiązane tematy.',
  keywords: ['chat', 'czat', 'pytanie', 'pomoc', 'jak', 'co to', 'baza wiedzy', 'stl', 'algorytm', 'ai'],

  render(root, ctx) {
    this._ctx = ctx;
    const state = { messages: local.get(STORE_KEY, []) || [], busy: false, sugg: [], suggSel: -1, lastSuggestQ: '' };
    this._state = state;

    // ------------------------------------------------------------------ szkielet
    const nnBadge = h('span', { class: 'asst-nn', title: 'Stan lokalnej sieci neuronowej wspierającej ranking' });
    const header = viewHeader(this, nnBadge,
      button('Nowa rozmowa', { icon: 'plus', onClick: () => newChat() }));
    const thread = h('div', { class: 'asst-thread', role: 'log', 'aria-live': 'polite' });
    const scroller = h('div', { class: 'asst-scroll' }, thread);

    const ta = h('textarea', {
      class: 'asst-input', rows: 1, spellcheck: 'false',
      placeholder: 'Zapytaj np. „jak usunąć element z vectora”, „co wypisze cout << \'a\' + 1;”… wklej kod (uruchomię go) albo wpisz / po komendy',
    });
    const suggBox = h('div', { class: 'asst-suggest', hidden: true, role: 'listbox' });
    const sendBtn = button(null, { icon: 'send', variant: 'primary', title: 'Wyślij (Enter)', onClick: () => submit() });
    const composer = h('div', { class: 'asst-composer' },
      suggBox,
      h('div', { class: 'asst-box' }, ta, sendBtn),
      h('div', { class: 'asst-hint' },
        kbd('Enter'), ' wyślij', h('span', { class: 'sep' }, '·'), kbd('Shift+Enter'), ' nowa linia',
        h('span', { class: 'sep' }, '·'), kbd('/'), ' komendy', h('span', { class: 'sep' }, '·'), kbd('/clear'), ' czyści kontekst',
        h('span', { class: 'grow' }),
        h('span', { class: 'muted' }, 'Działa offline — bez chmury i bez LLM.')));
    root.append(header, h('div', { class: 'asst-main' }, scroller, composer));

    // ------------------------------------------------------------------ pole tekstowe + autouzupełnianie
    const autosize = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 220) + 'px'; };
    ta.addEventListener('input', () => { autosize(); fetchSuggest(); });
    ta.addEventListener('keydown', (e) => {
      if (!suggBox.hidden && state.sugg.length) {
        if (e.key === 'ArrowDown') { e.preventDefault(); moveSugg(1); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); moveSugg(-1); return; }
        if (e.key === 'Escape') { e.preventDefault(); hideSugg(); return; }
        if ((e.key === 'Tab' || e.key === 'Enter') && state.suggSel >= 0) {
          e.preventDefault();
          const s = state.sugg[state.suggSel];
          hideSugg();
          if (e.key === 'Enter' && !s.needs) { ta.value = s.text; submit(); } else { ta.value = s.text; autosize(); ta.focus(); }
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
    ta.addEventListener('blur', () => setTimeout(hideSugg, 150));

    let commands = null;
    async function commandSuggest(q) {
      if (!commands) {
        try { commands = (await api.get('/api/assistant/commands', null, { quiet: true })).commands || []; } catch { commands = []; }
      }
      const w = q.slice(1).toLowerCase();
      state.sugg = commands.filter((c) => !w || c.name.startsWith(w) || c.aliases.some((a) => a.startsWith(w)))
        .map((c) => ({ text: `/${c.name}` + (c.needs ? ' ' : ''), kind: 'cmd', title: c.desc, needs: !!c.needs }));
      state.suggSel = state.sugg.length ? 0 : -1;
      renderSugg();
    }
    const fetchSuggest = debounce(async () => {
      const q = ta.value.trim();
      if (/^\/[\wąćęłńóśźż-]*$/i.test(q)) { commandSuggest(q); return; }
      if (q.length < 2 || q.includes('\n') || q.length > 80) { hideSugg(); return; }
      state.lastSuggestQ = q;
      try {
        const r = await api.get('/api/assistant/suggest', { q, k: 7 }, { quiet: true });
        if (state.lastSuggestQ !== q || ta.value.trim() !== q) return;
        state.sugg = (r.suggestions || []).filter((s) => s.text.toLowerCase() !== q.toLowerCase());
        state.suggSel = -1;
        renderSugg();
      } catch { hideSugg(); }
    }, 120);

    function renderSugg() {
      clear(suggBox);
      if (!state.sugg.length) { suggBox.hidden = true; return; }
      state.sugg.forEach((s, i) => {
        suggBox.append(h('div', {
          class: ['asst-sugg-item', i === state.suggSel ? 'sel' : ''].join(' '), role: 'option',
          onmousedown: (e) => { e.preventDefault(); ta.value = s.text; hideSugg(); if (s.needs) { autosize(); ta.focus(); } else submit(); },
        }, icon(s.kind === 'cmd' ? 'terminal' : s.kind === 'qa' ? 'message-square' : 'book-open', 14), h('span', { class: 'grow ellipsis' }, s.text),
        s.title && (s.kind === 'qa' || s.kind === 'cmd') ? h('span', { class: 'asst-sugg-meta ellipsis' }, s.title) : null));
      });
      suggBox.hidden = false;
    }
    function moveSugg(d) {
      const n = state.sugg.length;
      let i = state.suggSel + d;
      if (i < -1) i = n - 1;
      if (i >= n) i = -1;
      state.suggSel = i;
      renderSugg();
    }
    function hideSugg() { suggBox.hidden = true; state.sugg = []; state.suggSel = -1; }

    // ------------------------------------------------------------------ rozmowa
    function save() {
      const slim = state.messages.slice(-MAX_SAVED).map((m) => (m.role === 'user' ? m : { ...m, resp: slimResp(m.resp) }));
      local.set(STORE_KEY, slim);
    }
    function historyForApi() {
      const out = [];
      for (const m of state.messages) {
        if (m.role === 'assistant' && m.resp) {
          out.push({ q: m.q, topic: m.resp.topic?.id || null, uid: m.resp.used_uid || null,
            clarify: m.resp.clarify || 0, examples: m.resp.examples || 0 });
        }
      }
      return out.slice(-6);
    }
    function newChat() {
      state.messages = [];
      save();
      renderAll();
      ta.focus();
    }

    const ask = async (q) => {
      q = String(q || '').trim();
      if (!q || state.busy) return;
      hideSugg();
      if (CLEAR_RX.test(q)) { newChat(); toast('Wyczyściłem rozmowę i kontekst.', 'success'); return; }
      state.busy = true;
      sendBtn.disabled = true;
      const userMsg = { role: 'user', text: q, t: Date.now() };
      state.messages.push(userMsg);
      if (state.messages.length === 1) clear(thread);
      thread.append(renderUser(userMsg));
      const pending = h('div', { class: 'asst-msg bot' }, avatar(), h('div', { class: 'asst-bubble typing' },
        h('span', { class: 'dot' }), h('span', { class: 'dot' }), h('span', { class: 'dot' })));
      thread.append(pending);
      scrollDown();
      try {
        const resp = await api.post('/api/assistant/ask', { q, history: historyForApi() });
        if (resp.clear) { pending.remove(); state.busy = false; sendBtn.disabled = false; newChat(); toast('Wyczyściłem rozmowę i kontekst.', 'success'); return; }
        const m = { role: 'assistant', q, resp, t: Date.now() };
        state.messages.push(m);
        pending.replaceWith(renderBot(m));
        updateNN(resp.nn);
      } catch (e) {
        pending.replaceWith(h('div', { class: 'asst-msg bot' }, avatar(),
          h('div', { class: 'asst-bubble' }, callout(`Nie udało się uzyskać odpowiedzi: ${e.message || e}`, 'danger'))));
      } finally {
        state.busy = false;
        sendBtn.disabled = false;
        save();
        scrollDown();
      }
    };
    this._ask = ask;

    function submit() {
      const q = ta.value.trim();
      if (!q) return;
      ta.value = '';
      autosize();
      ask(q);
    }

    function scrollDown() { requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; }); }

    // ------------------------------------------------------------------ renderowanie
    function avatar() { return h('div', { class: 'asst-avatar' }, icon('bot', 17)); }

    function renderUser(m) {
      const multi = m.text.includes('\n');
      return h('div', { class: 'asst-msg user' },
        h('div', { class: 'asst-bubble' }, multi ? h('pre', { class: 'asst-user-pre' }, m.text) : m.text));
    }

    function renderBot(m) {
      const r = m.resp || {};
      const bubble = h('div', { class: 'asst-bubble' });
      // nagłówek: intencja, temat, pewność
      const head = h('div', { class: 'asst-head' },
        badge(r.intent?.label || 'Odpowiedź', 'accent', INTENT_ICON[r.intent?.id] || 'sparkles'),
        r.topic ? h('button', { class: 'asst-topic', title: 'Otwórz artykuł w dokumentacji', onclick: () => ctx.navigate('docs', { id: r.topic.id }) },
          icon('book-open', 13), r.topic.title) : null,
        h('span', { class: 'grow' }),
        typeof r.confidence === 'number' ? confidenceMeter(r.confidence, r.confidence_label) : null);
      bubble.append(head);
      if (r.did_you_mean) {
        bubble.append(h('div', { class: 'asst-dym' }, icon('wand-sparkles', 14), 'Czy chodziło Ci o: ',
          h('button', { class: 'linklike', onclick: () => ask(r.did_you_mean.text) }, `„${r.did_you_mean.text}”`), '?'));
      }
      const body = h('div', { class: 'asst-body' });
      for (const b of r.blocks || []) {
        const node = renderBlock(b);
        if (node) body.append(node);
      }
      bubble.append(body);
      // przy niskiej pewności: najbliższe tematy (jeśli nie ma ich już w treści)
      if ((r.alternatives || []).length && !(r.blocks || []).some((b) => b.type === 'topics')) {
        bubble.append(h('div', { class: 'asst-section' }, h('div', { class: 'asst-section-title' }, icon('target', 13), 'Najbliższe tematy'),
          renderBlock({ type: 'topics', items: r.alternatives })));
      }
      // akcje
      if ((r.actions || []).length) {
        const acts = r.actions.filter((a) => !ctx.hasView || ctx.hasView(a.view));
        if (acts.length) bubble.append(h('div', { class: 'asst-actions' }, acts.map((a) => button(a.label, {
          icon: a.icon, size: 'sm', onClick: () => ctx.navigate(a.view, a.params || {}),
        }))));
      }
      // źródła
      if ((r.sources || []).length) {
        bubble.append(h('div', { class: 'asst-section' }, h('div', { class: 'asst-section-title' }, icon('library', 13), 'Źródła'),
          h('div', { class: 'asst-sources' }, r.sources.map((s) => h('button', {
            class: 'asst-source', title: 'Otwórz w dokumentacji',
            onclick: () => s.id && ctx.navigate('docs', { id: s.id, anchor: s.anchor || '' }),
          },
          h('div', { class: 'asst-source-top' }, icon(KIND_ICON[s.kind] || 'file-text', 13), h('span', { class: 'ellipsis' }, s.kind_label)),
          h('div', { class: 'asst-source-title ellipsis' }, s.kind === 'article' ? s.title : `${s.article || ''}`),
          s.kind !== 'article' ? h('div', { class: 'asst-source-sub ellipsis' }, s.title) : null)))));
      }
      // zobacz też
      if ((r.see_also || []).length) {
        bubble.append(h('div', { class: 'asst-section' }, h('div', { class: 'asst-section-title' }, icon('arrow-right', 13), 'Zobacz też'),
          h('div', { class: 'asst-chips' }, r.see_also.map((a) => h('button', {
            class: 'chip', title: a.summary || '', onclick: () => ctx.navigate('docs', { id: a.id }),
          }, icon('book-open', 13), a.title)))));
      }
      // twoje rozwiązania — dociągane osobnym żądaniem, żeby sama odpowiedź przychodziła od razu
      const progHost = h('div');
      bubble.append(progHost);
      if ((r.programs || []).length) {
        progHost.append(programsSection(r.programs, m));
      } else if (r.programs_query && !r.programs_loaded) {
        api.get('/api/assistant/programs', { q: r.programs_query }, { quiet: true }).then((pr) => {
          r.programs = (pr && pr.programs) || [];
          r.programs_loaded = true;
          if (r.programs.length) {
            progHost.append(programsSection(r.programs, m));
            save();
          }
        }).catch(() => { r.programs_loaded = true; });
      }
      // stopka
      bubble.append(h('div', { class: 'asst-foot' },
        r.timing_ms !== undefined ? h('span', { class: 'faint small' }, `${Math.round(r.timing_ms)} ms`) : null,
        r.nn && !r.nn.ready ? h('span', { class: 'faint small' }, '· sieć jeszcze się uczy') : null,
        h('span', { class: 'grow' }),
        copyButton(() => plainAnswer(r), { label: 'Kopiuj' })));
      const wrap = h('div', { class: 'asst-msg bot' }, avatar(), h('div', { class: 'asst-col' }, bubble,
        (r.suggestions || []).length ? h('div', { class: 'asst-followups' }, r.suggestions.map((s) => h('button', {
          class: 'chip', onclick: () => ask(s),
        }, icon('sparkles', 12), s))) : null));
      return wrap;
    }

    function programsSection(programs, m) {
      return h('div', { class: 'asst-section' }, h('div', { class: 'asst-section-title' }, icon('folder', 13), 'Twoje rozwiązania'),
        h('div', { class: 'asst-programs' }, programs.map((p) => h('button', {
          class: 'asst-program', onclick: () => ctx.navigate('programs', { id: p.id, path: p.path, q: m.q }),
        }, icon('file-code', 14), h('div', { class: 'grow', style: 'min-width:0' },
          h('div', { class: 'ellipsis' }, h('b', null, p.problem_name || p.path), p.contest_name ? h('span', { class: 'muted small' }, ` · ${p.contest_name}`) : null),
          p.why ? h('div', { class: 'faint small ellipsis' }, p.why) : null)))));
    }

    function renderBlock(b) {
      switch (b.type) {
        case 'text': return markdown(b.md || '');
        case 'code': {
          const runHost = h('div');
          const lang = b.lang || 'cpp';
          const actions = [];
          if (lang === 'cpp' || lang === 'python') {
            actions.push(button('Uruchom', { icon: 'play', size: 'sm', variant: 'ghost', title: 'Uruchom ten kod w piaskownicy', onClick: async (ev) => {
              const btn = ev?.currentTarget;
              if (btn) btn.disabled = true;
              clear(runHost).append(h('div', { class: 'row gap-s small muted' }, spinner(14), 'Kompiluję i uruchamiam…'));
              try {
                const rr = await api.post('/api/assistant/run', { code: b.code, lang }, { quiet: true });
                clear(runHost).append(renderRun(rr.run || {}));
              } catch (e) { clear(runHost).append(callout(e.message || String(e), 'danger')); }
              finally { if (btn) btn.disabled = false; }
            } }));
          }
          if (!ctx.hasView || ctx.hasView('workspace')) actions.push(button('W edytorze', { icon: 'file-code', size: 'sm', variant: 'ghost', onClick: () => ctx.navigate('workspace', { code: b.code, name: lang === 'python' ? 'czat.py' : 'czat.cpp' }) }));
          return h('div', { class: 'stack gap-s' }, codeBlock(b.code || '', lang, { title: b.title || undefined, actions, maxHeight: 520 }), runHost);
        }
        case 'run': return renderRun(b);
        case 'tip': return callout(b.md || '', 'tip');
        case 'table': return renderTable(b);
        case 'complexity':
          return h('div', { class: 'asst-complexity' }, icon('gauge', 16),
            h('div', { class: 'grow' }, h('div', { class: 'asst-mini-title' }, b.title || 'Złożoność'), markdown(b.text || '')));
        case 'functions':
          return h('div', { class: 'asst-fns' }, h('div', { class: 'asst-mini-title' }, icon('square-function', 13), 'Funkcje'),
            (b.items || []).map((f) => h('div', { class: 'asst-fn' },
              h('code', { class: 'asst-fn-sig' }, f.sig),
              h('span', { class: 'asst-fn-desc' }, f.desc || ''),
              f.complexity ? h('span', { class: 'badge' }, f.complexity) : null)));
        case 'pitfalls':
          return h('div', { class: 'asst-pitfalls' }, h('div', { class: 'asst-mini-title' }, icon('triangle-alert', 13), b.title || 'Uważaj na'),
            h('ul', null, (b.items || []).map((p) => h('li', null, markdown(p)))));
        case 'notice': return callout(b.md || '', b.tone === 'warn' ? 'warn' : b.tone || 'info');
        case 'warn': return callout(b.md || '', 'warn');
        case 'errors':
          return h('div', { class: 'stack gap-s' }, (b.items || []).map((e) => h('div', { class: ['err-card', e.severity === 'warning' ? 'warning' : ''].join(' ') },
            h('div', { class: 'ec-title' }, icon(e.severity === 'warning' ? 'triangle-alert' : 'circle-alert', 15),
              h('span', { class: 'grow' }, e.title || 'Błąd'), e.line ? h('span', { class: 'ec-line' }, `linia ${e.line}`) : null),
            e.explanation ? h('div', { class: 'ec-text' }, markdown(e.explanation)) : null,
            e.fix ? h('div', { class: 'ec-fix' }, markdown('**Jak naprawić:** ' + e.fix)) : null)));
        case 'topics':
          return h('div', { class: 'asst-topics' }, (b.items || []).map((t) => h('button', {
            class: 'asst-topic-card', onclick: () => ctx.navigate('docs', { id: t.id }),
          }, h('div', { class: 'row gap-s' }, icon('book-open', 14), h('b', { class: 'ellipsis' }, t.title)),
          t.summary ? h('div', { class: 'muted small' }, t.summary) : null,
          h('div', { class: 'row gap-s mt-s' }, t.category ? badge(t.category) : null,
            h('button', { class: 'linklike small', onclick: (e) => { e.stopPropagation(); ask(`Co to ${t.title}?`); } }, 'zapytaj')))));
        default: return b.md ? markdown(b.md) : null;
      }
    }

    function renderRun(b) {
      const ce = b.stage === 'compile';
      const ok = !!b.ok;
      const parts = [h('div', { class: 'asst-run-head' }, icon(ce ? 'circle-alert' : ok ? 'circle-check' : 'triangle-alert', 15),
        h('b', null, b.title || 'Wynik uruchomienia'), b.verdict ? verdictBadge(b.verdict) : null, h('span', { class: 'grow' }),
        typeof b.time === 'number' && !ce ? h('span', { class: 'muted small' }, `${Math.round(b.time * 1000)} ms`) : null)];
      if (ce) {
        parts.push(h('div', { class: 'small' }, 'Kod się nie kompiluje:'));
        if ((b.errors || []).length) parts.push(renderBlock({ type: 'errors', items: b.errors }));
        else if (b.raw) parts.push(h('pre', { class: 'asst-run-pre err' }, b.raw));
      } else {
        if (b.stdin) parts.push(h('div', { class: 'asst-run-label' }, 'Wejście'), h('pre', { class: 'asst-run-pre' }, b.stdin));
        parts.push(h('div', { class: 'asst-run-label' }, 'Wyjście'), h('pre', { class: 'asst-run-pre out' }, b.stdout ? b.stdout : '(program nic nie wypisał)'));
        if (b.truncated) parts.push(h('div', { class: 'faint tiny' }, 'Wyjście obcięte — program wypisał bardzo dużo.'));
        if (!ok) {
          const ex = b.explanation || {};
          if (ex.title) parts.push(callout(`**${ex.title}** — ${ex.explanation || ''}` + ((ex.causes || []).length ? '\n\nNajczęstsze przyczyny:\n' + ex.causes.slice(0, 4).map((c) => '- ' + c).join('\n') : ''), 'warn'));
          if (b.stderr) parts.push(h('pre', { class: 'asst-run-pre err' }, b.stderr));
        }
      }
      if (b.wrapped && b.source) {
        parts.push(h('details', { class: 'asst-run-src' }, h('summary', null, 'Pełny program (dopisałem #include i main)'),
          codeBlock(b.source, 'cpp', { maxHeight: 360 })));
      }
      if (b.sandbox) parts.push(h('div', { class: 'faint tiny' }, icon('shield', 11), ' ', b.sandbox));
      return h('div', { class: ['asst-run', ce ? 'ce' : ok ? 'ok' : 'bad'].join(' ') }, ...parts);
    }

    function renderTable(b) {
      const cell = (c, tag) => h(tag, null, markdown(String(c ?? '')));
      return h('div', { class: 'asst-table' }, b.title ? h('div', { class: 'asst-mini-title' }, icon('git-compare', 13), b.title) : null,
        h('div', { class: 'table-wrap' }, h('table', { class: 'tbl compact' },
          h('thead', null, h('tr', null, (b.headers || []).map((x) => cell(x, 'th')))),
          h('tbody', null, (b.rows || []).map((row) => h('tr', null, row.map((x, i) => cell(x, i === 0 ? 'th' : 'td'))))))));
    }

    function confidenceMeter(c, label) {
      const tone = c >= 0.7 ? 'success' : c >= 0.42 ? 'warn' : 'danger';
      const n = c >= 0.7 ? 3 : c >= 0.42 ? 2 : 1;
      return h('span', { class: `asst-conf ${tone}`, title: `Pewność odpowiedzi: ${Math.round(c * 100)}%` },
        h('span', { class: 'bars' }, [1, 2, 3].map((i) => h('i', { class: i <= n ? 'on' : '' }))),
        `pewność ${label || ''}`);
    }

    function renderEmpty() {
      const ex = h('div', { class: 'asst-examples' }, spinner(16));
      const el = h('div', { class: 'asst-empty' },
        h('div', { class: 'asst-hero-icon' }, icon('bot', 30)),
        h('h2', null, 'O co chcesz zapytać?'),
        h('p', { class: 'muted' }, 'C++, STL, algorytmy, błędy kompilatora — pisz normalnie, literówki nie przeszkadzają.'),
        ex);
      api.get('/api/assistant/examples', { k: 6 }, { quiet: true }).then((r) => {
        clear(ex);
        for (const e of r.examples || []) ex.append(h('button', { class: 'chip', onclick: () => ask(e.text), title: e.category || '' }, icon('sparkles', 12), e.text));
      }).catch(() => {
        clear(ex);
        for (const t of ['Jak użyć seta?', 'Dijkstra kod', 'multiset vs set', 'Co to lazy propagation?', 'Jak wczytać do końca pliku?']) {
          ex.append(h('button', { class: 'chip', onclick: () => ask(t) }, icon('sparkles', 12), t));
        }
      });
      return el;
    }
    function capability(ic, title, text) {
      return h('div', { class: 'asst-cap' }, icon(ic, 16), h('div', null, h('b', null, title), h('div', { class: 'muted small' }, text)));
    }

    function renderAll() {
      clear(thread);
      if (!state.messages.length) { thread.append(renderEmpty()); return; }
      for (const m of state.messages) thread.append(m.role === 'user' ? renderUser(m) : renderBot(m));
      scrollDown();
    }

    function updateNN(nn) {
      if (!nn) return;
      clear(nnBadge);
      const ready = nn.ready && nn.state === 'ready';
      nnBadge.className = `asst-nn ${ready ? 'ready' : 'training'}`;
      nnBadge.append(icon('brain-circuit', 14), ready ? 'sieć gotowa' : nn.ready ? 'sieć: douczanie' : 'sieć się uczy');
    }
    api.get('/api/assistant/status', null, { quiet: true }).then((s) => {
      const nn = s.nn || {};
      updateNN({ ready: nn.ready, state: nn.state });
      nnBadge.title = `Baza: ${s.articles} artykułów, ${s.units} jednostek wyszukiwania. ${nn.message || ''}`;
    }).catch(() => {});

    renderAll();
    setTimeout(() => ta.focus(), 50);
    this._focus = () => ta.focus();
  },

  onShow(params) {
    if (params && params.q && this._ask) {
      const q = String(params.q);
      if (this._lastAsked !== q || Date.now() - (this._lastAskedAt || 0) > 1500) {
        this._lastAsked = q;
        this._lastAskedAt = Date.now();
        this._ask(q);
      }
    } else if (this._focus) setTimeout(() => this._focus(), 30);
  },
};

// ------------------------------------------------------------------------------------- pomocnicze
function slimResp(r) {
  if (!r) return r;
  const { q, intent, confidence, confidence_label, topic, blocks, sources, see_also, suggestions, actions, programs, did_you_mean, timing_ms } = r;
  return {
    q, intent, confidence, confidence_label, topic, blocks, sources, see_also, suggestions, programs, did_you_mean, timing_ms,
    // kod w parametrach akcji bywa duży — w historii zostaje tylko jego pierwsze 20 kB
    actions: (actions || []).map((a) => ({ ...a, params: trimParams(a.params) })),
  };
}

function trimParams(p) {
  if (!p) return p;
  const out = {};
  for (const [k, v] of Object.entries(p)) out[k] = typeof v === 'string' && v.length > 20000 ? v.slice(0, 20000) : v;
  return out;
}

function plainAnswer(r) {
  const out = [];
  for (const b of r.blocks || []) {
    if (b.type === 'text' || b.type === 'notice' || b.type === 'warn') out.push(b.md || '');
    else if (b.type === 'code') out.push('```' + (b.lang || 'cpp') + '\n' + (b.code || '') + '\n```');
    else if (b.type === 'run') out.push('Wynik:\n```\n' + (b.stdout || '') + '\n```');
    else if (b.type === 'complexity') out.push('Złożoność: ' + (b.text || ''));
    else if (b.type === 'pitfalls') out.push((b.items || []).map((p) => '- ' + p).join('\n'));
    else if (b.type === 'functions') out.push((b.items || []).map((f) => `- ${f.sig} — ${f.desc || ''} ${f.complexity ? '(' + f.complexity + ')' : ''}`).join('\n'));
    else if (b.type === 'table') out.push([(b.headers || []).join(' | '), ...(b.rows || []).map((row) => row.join(' | '))].join('\n'));
  }
  return out.join('\n\n');
}

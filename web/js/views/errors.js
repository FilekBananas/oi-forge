// (B1) Tłumacz błędów C++ — wklej komunikat kompilatora (GCC/Clang/linker) albo opis błędu wykonania,
// a dostaniesz polskie wyjaśnienie, konkretną poprawkę i fragment kodu z zaznaczoną kolumną.
import {
  h, icon, button, viewHeader, split, textarea, badge, callout, markdown, toast, clear, emptyState,
  select, input, field, segmented, iconButton, copyButton,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const SIGNAL_OPTS = [
  { value: '', label: '— brak / nie wiem —' }, { value: 'SIGSEGV', label: 'SIGSEGV (Segmentation fault)' },
  { value: 'SIGFPE', label: 'SIGFPE (Floating point exception)' }, { value: 'SIGABRT', label: 'SIGABRT (Aborted)' },
  { value: 'SIGBUS', label: 'SIGBUS (Bus error)' }, { value: 'SIGILL', label: 'SIGILL (Illegal instruction)' },
  { value: 'SIGKILL', label: 'SIGKILL (Killed)' }, { value: 'SIGTRAP', label: 'SIGTRAP (Trace/breakpoint trap)' },
];
const VERDICT_OPTS = [{ value: 'RE', label: 'RE — błąd wykonania' }, { value: 'TLE', label: 'TLE — przekroczony czas' },
  { value: 'MLE', label: 'MLE — przekroczona pamięć' }];

export default {
  id: 'errors',
  title: 'Tłumacz błędów C++',
  icon: 'languages',
  group: 'Kod',
  order: 11,
  layout: 'full',
  description: 'Wklej komunikat kompilatora (GCC, Clang, linker) albo błąd wykonania — dostaniesz wyjaśnienie po polsku, '
    + 'konkretną poprawkę i wskazanie miejsca w kodzie. Spam szablonów jest upraszczany, a błędy kaskadowe grupowane.',
  keywords: ['błąd kompilacji', 'error', 'kompilator', 'tłumacz', 'stderr', 'segfault', 'sigsegv', 'undefined reference',
    'was not declared', 'expected', 'gcc', 'clang'],

  render(root, ctx) {
    const self = this;
    let mode = local.get('errors:mode', 'compile');
    let examples = [];
    let lastItems = null;

    // ------------------------------------------------------------------ lewa kolumna
    const stderrEl = textarea({ value: local.get('errors:stderr', ''), rows: 12,
      placeholder: 'Wklej tutaj komunikat kompilatora, np.\nmain.cpp:7:5: error: \'cin\' was not declared in this scope…',
      onInput: (v) => local.set('errors:stderr', v) });
    stderrEl.classList.add('er-stderr');
    stderrEl.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); translate(); } });
    const srcHost = h('div', { class: 'er-src' });
    const srcBox = h('details', { class: 'er-srcbox', open: local.get('errors:srcOpen', false) },
      h('summary', null, icon('file-code', 14), ' Kod źródłowy (opcjonalnie — lepsze podpowiedzi i fragment z zaznaczeniem)'), srcHost);
    srcBox.addEventListener('toggle', () => { local.set('errors:srcOpen', srcBox.open); if (srcBox.open) setTimeout(() => ed.refresh(), 30); });
    const examplesHost = h('div', { class: 'er-examples' });

    const compileBox = h('div', { class: 'stack gap-s er-compile' },
      h('div', { class: 'row between' }, h('div', { class: 'section-title', style: 'margin:0' }, 'Komunikat kompilatora (stderr)'),
        h('div', { class: 'row gap-s' },
          button('Wklej', { icon: 'clipboard-paste', size: 'sm', variant: 'ghost', onClick: () => pasteClipboard() }),
          button('Wyczyść', { icon: 'trash-2', size: 'sm', variant: 'ghost', onClick: () => { stderrEl.value = ''; local.set('errors:stderr', ''); lastItems = null; renderEmpty(); } }))),
      stderrEl,
      srcBox,
      h('div', { class: 'row gap-s er-actions' },
        button('Przetłumacz', { icon: 'languages', variant: 'primary', onClick: () => translate(), title: 'Ctrl+Enter' }),
        h('span', { class: 'muted small' }, 'Ctrl+Enter')),
      h('div', { class: 'section-title' }, 'Przykładowe błędy — kliknij, żeby zobaczyć tłumaczenie'),
      examplesHost);

    // tryb błędu wykonania
    const sigSel = select(SIGNAL_OPTS, { value: '' });
    const verdictSel = select(VERDICT_OPTS, { value: 'RE' });
    const codeInp = input({ placeholder: 'np. 139, -11, 3221225477, 0xC0000005', mono: true });
    const rtStderr = textarea({ rows: 8, placeholder: 'stderr programu (opcjonalnie), np.\nterminate called after throwing an instance of \'std::out_of_range\'…' });
    const runtimeBox = h('div', { class: 'stack er-runtime' },
      callout('Program się skompilował, ale **wysypał się w trakcie działania**? Podaj, co pokazała sprawdzarka albo terminal — '
        + 'werdykt, sygnał lub kod wyjścia (także kody Windows, np. `3221225477` = 0xC0000005) i ewentualny komunikat błędu.', 'info'),
      h('div', { class: 'grid-2' }, field('Werdykt', verdictSel), field('Sygnał', sigSel)),
      field('Kod wyjścia', codeInp, 'Na Linuksie/macOS 139 = SIGSEGV, 136 = SIGFPE, 134 = SIGABRT; na Windows liczby typu 3221225477.'),
      field('Komunikat (stderr)', rtStderr),
      h('div', { class: 'row gap-s' }, button('Wyjaśnij błąd wykonania', { icon: 'stethoscope', variant: 'primary', onClick: () => explainRuntime() })));

    const modeSeg = segmented([{ value: 'compile', label: 'Błąd kompilacji', icon: 'cpu' }, { value: 'runtime', label: 'Błąd wykonania', icon: 'bug' }],
      { value: mode, onChange: (v) => { mode = v; local.set('errors:mode', v); applyMode(); } });
    const left = h('div', { class: 'er-col er-left' }, h('div', null, modeSeg), compileBox, runtimeBox);

    // ------------------------------------------------------------------ prawa kolumna
    const resultsHost = h('div', { class: 'er-results' });
    const right = h('div', { class: 'er-col er-right' }, resultsHost);

    root.append(viewHeader(this,
      button('Sprawdź kod w Sprawdzarce', { icon: 'bug', variant: 'ghost', onClick: () => {
        const code = ed.getValue();
        if (!code.trim()) { toast('Najpierw wklej kod źródłowy (sekcja „Kod źródłowy”).', 'info'); srcBox.open = true; return; }
        ctx.navigate('checker', { code });
      } })),
    h('div', { class: 'er-body' }, split(left, right, { ratio: 0.46, min: 320, storageKey: 'errors' })));

    const ed = createEditor(srcHost, { value: '', lang: 'cpp', height: '260px', storageKey: 'errors-src', placeholder: 'Wklej kod, którego dotyczy błąd…' });
    applyMode();
    renderEmpty();
    loadExamples();
    if (stderrEl.value.trim()) setTimeout(() => translate({ quiet: true }), 100);

    function applyMode() {
      compileBox.hidden = mode !== 'compile';
      runtimeBox.hidden = mode !== 'runtime';
    }

    async function pasteClipboard() {
      try {
        const t = await navigator.clipboard.readText();
        if (t) { stderrEl.value = t; local.set('errors:stderr', t); translate(); }
      } catch { toast('Przeglądarka nie pozwoliła odczytać schowka — wklej ręcznie (Ctrl+V).', 'warn'); }
    }

    async function loadExamples() {
      try {
        const r = await api.get('/api/errors/examples', null, { quiet: true });
        examples = r.examples || [];
      } catch { examples = []; }
      clear(examplesHost);
      examplesHost.append(...examples.map((ex) => h('button', { class: 'chip', type: 'button', onclick: () => useExample(ex) },
        icon(ex.runtime ? 'bug' : 'circle-alert', 13), ex.title)));
    }

    function useExample(ex) {
      mode = 'compile'; modeSeg.setValue('compile'); applyMode();
      stderrEl.value = ex.stderr;
      local.set('errors:stderr', ex.stderr);
      if (ex.source) { ed.setValue(ex.source); srcBox.open = true; setTimeout(() => ed.refresh(), 30); }
      translate();
    }

    // ------------------------------------------------------------------ tłumaczenie
    async function translate({ quiet = false } = {}) {
      const stderr = stderrEl.value;
      if (!stderr.trim()) { if (!quiet) toast('Wklej komunikat kompilatora.', 'info'); return; }
      const source = ed.getValue();
      try {
        const r = await api.post('/api/errors/translate', { stderr, source: source.trim() ? source : null }, { quiet });
        lastItems = r.items || [];
        renderItems(r);
      } catch (e) { if (!quiet) console.error(e); }
    }

    async function explainRuntime() {
      const raw = codeInp.value.trim();
      let code = null;
      if (raw) code = /^0x/i.test(raw) ? parseInt(raw, 16) : parseInt(raw, 10);
      if (raw && Number.isNaN(code)) { toast('Kod wyjścia musi być liczbą (np. 139 albo 0xC0000005).', 'warn'); return; }
      const run = { verdict: verdictSel.value, signal: sigSel.value || null, exit_code: code, stderr: rtStderr.value,
        timed_out: verdictSel.value === 'TLE' };
      try {
        const r = await api.post('/api/errors/runtime', { run, source: ed.getValue() || null });
        renderRuntime(r.explanation);
      } catch (e) { console.error(e); }
    }

    // ------------------------------------------------------------------ renderowanie
    function renderEmpty() {
      clear(resultsHost);
      resultsHost.append(emptyState({ icon: 'languages', title: 'Wklej komunikat błędu',
        text: 'Rozpoznaję ponad 130 rodzajów komunikatów GCC, Clanga, linkera i Pythona. Pierwszy błąd jest zwykle główną przyczyną — kolejne często są jego skutkiem.' }));
    }

    function renderItems(r) {
      clear(resultsHost);
      const items = r.items || [];
      const s = r.summary || {};
      if (!items.length && !r.runtime) {
        resultsHost.append(emptyState({ icon: 'search', title: 'Nie znalazłem komunikatów kompilatora',
          text: 'Upewnij się, że wklejasz wyjście kompilatora (linie w stylu `main.cpp:12:5: error: …`). Dla błędów w trakcie działania wybierz tryb „Błąd wykonania”.' }));
        return;
      }
      if (items.length) {
        resultsHost.append(h('div', { class: `er-summary ${s.errors ? 'bad' : 'ok'}` },
          h('div', { class: 'er-sum-icon' }, icon(s.errors ? 'circle-alert' : 'triangle-alert', 20)),
          h('div', { class: 'grow' }, h('div', { class: 'er-headline' }, s.headline || ''),
            h('div', { class: 'muted small' }, [
              s.errors ? `${s.errors} ${plural(s.errors, 'błąd', 'błędy', 'błędów')}` : null,
              s.warnings ? `${s.warnings} ${plural(s.warnings, 'ostrzeżenie', 'ostrzeżenia', 'ostrzeżeń')}` : null,
              s.cascades ? `${s.cascades} prawdopodobnie kaskadowych (skutki pierwszego)` : null,
            ].filter(Boolean).join(' · ')))));
        if (s.errors > 1) resultsHost.append(callout('Poprawiaj błędy **od pierwszego** i kompiluj ponownie — jeden brakujący średnik albo nawias potrafi wygenerować kilkanaście kolejnych komunikatów.', 'tip'));
      }
      const list = h('div', { class: 'er-list' }, items.map((it) => itemCard(it)));
      resultsHost.append(list);
      if (r.runtime) resultsHost.append(runtimeCard(r.runtime));
      markSource(items);
    }

    function markSource(items) {
      if (!ed.getValue().trim()) return;
      ed.markLines(items.filter((i) => i.line > 0).map((i) => ({ line: i.line, type: i.severity === 'error' ? 'error' : 'warn', message: i.title })));
    }

    function plural(n, one, few, many) {
      const n10 = n % 10, n100 = n % 100;
      if (n === 1) return one;
      if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
      return many;
    }

    function itemCard(it) {
      const isErr = it.severity === 'error';
      const goto = () => {
        if (!it.line) return;
        if (!ed.getValue().trim()) { toast(`Błąd jest w linii ${it.line}. Wklej kod w sekcji „Kod źródłowy”, żeby go podświetlić.`, 'info'); return; }
        srcBox.open = true;
        setTimeout(() => { ed.refresh(); ed.scrollToLine(it.line); }, 40);
      };
      const head = h('div', { class: 'er-head' },
        h('span', { class: `er-icon ${isErr ? 'tone-danger' : 'tone-warn'}` }, icon(isErr ? 'circle-alert' : 'triangle-alert', 17)),
        h('div', { class: 'grow' },
          h('div', { class: 'er-title' }, markdown(it.title)),
          h('div', { class: 'er-meta' },
            it.primary ? badge('Główna przyczyna', 'danger', 'target') : null,
            it.cascade ? badge('Prawdopodobnie skutek wcześniejszego błędu', 'neutral') : null,
            !isErr ? badge('Ostrzeżenie', 'warn') : null,
            it.count > 1 ? badge(`×${it.count}`, 'neutral') : null,
            it.flag ? h('span', { class: 'mono small muted' }, it.flag) : null,
            it.in_header ? h('span', { class: 'small muted', title: it.file || '' }, icon('library', 12), ' zgłoszony w bibliotece') : null)),
        it.line ? button(`linia ${it.line}${it.col ? ':' + it.col : ''}`, { size: 'sm', variant: 'ghost', icon: 'arrow-right', onClick: goto }) : null);
      const card = h('div', { class: ['er-item', isErr ? 'sev-error' : 'sev-warning', it.primary ? 'primary' : '', it.cascade ? 'cascade' : ''].join(' ') },
        head,
        it.explanation ? h('div', { class: 'er-expl md' }, markdown(it.explanation)) : null,
        it.fix ? h('div', { class: 'er-fix' }, h('span', { class: 'er-fix-icon' }, icon('lightbulb', 14)), h('div', { class: 'md grow' }, markdown(it.fix))) : null,
        it.snippet ? snippetEl(it.snippet, goto) : null,
        h('div', { class: 'er-orig' }, h('span', { class: 'er-orig-label' }, 'Kompilator:'), h('code', null, it.original || ''),
          copyButton(() => it.raw || it.original || '')),
        (it.notes || []).length ? h('details', { class: 'er-notes' }, h('summary', null, `Notatki kompilatora (${it.notes.length})`),
          h('ul', null, it.notes.map((n) => h('li', null, h('code', null, n))))) : null,
        it.kb ? h('div', { class: 'row' }, button('Przeczytaj w bazie wiedzy', { size: 'sm', variant: 'ghost', icon: 'book-open', onClick: () => ctx.navigate('docs', { id: it.kb }) })) : null);
      return card;
    }

    function snippetEl(sn, goto) {
      const pre = h('div', { class: 'er-snippet', onclick: goto, title: 'Pokaż w kodzie' });
      for (const row of sn.lines || []) {
        pre.append(h('div', { class: ['er-sl', row.focus ? 'focus' : ''].join(' ') }, h('span', { class: 'er-ln' }, String(row.n)), h('span', { class: 'er-lt' }, row.text || ' ')));
        if (row.focus) {
          const pad = (row.text || '').slice(0, Math.max(0, (sn.col || 1) - 1)).replace(/[^\t]/g, ' ');
          pre.append(h('div', { class: 'er-sl caret' }, h('span', { class: 'er-ln' }, ''), h('span', { class: 'er-lt' }, pad + '^' + '~'.repeat(Math.max(0, (sn.len || 1) - 1)))));
        }
      }
      return pre;
    }

    function runtimeCard(rt) {
      return h('div', { class: `er-item er-runtime-card ${rt.severity === 'error' ? 'sev-error' : 'sev-warning'}` },
        h('div', { class: 'er-head' }, h('span', { class: 'er-icon tone-danger' }, icon('bug', 17)),
          h('div', { class: 'grow' }, h('div', { class: 'er-title' }, rt.title || 'Błąd wykonania'),
            h('div', { class: 'er-meta' }, badge('Błąd wykonania', 'danger'), rt.kind ? h('span', { class: 'mono small muted' }, rt.kind) : null)),
          rt.line ? button(`linia ${rt.line}`, { size: 'sm', variant: 'ghost', icon: 'arrow-right', onClick: () => { srcBox.open = true; setTimeout(() => { ed.refresh(); ed.scrollToLine(rt.line); }, 40); } }) : null),
        rt.explanation ? h('div', { class: 'er-expl md' }, markdown(rt.explanation)) : null,
        (rt.causes || []).length ? h('div', { class: 'er-causes' }, h('div', { class: 'section-title' }, 'Najczęstsze przyczyny'),
          h('ul', null, rt.causes.map((c) => h('li', null, markdown(c))))) : null,
        rt.fix ? h('div', { class: 'er-fix' }, h('span', { class: 'er-fix-icon' }, icon('lightbulb', 14)), h('div', { class: 'md grow' }, markdown(rt.fix))) : null,
        h('div', { class: 'row gap-s' }, button('Znajdź linię w Sprawdzarce', { size: 'sm', icon: 'microscope', variant: 'ghost', onClick: () => {
          const code = ed.getValue();
          if (!code.trim()) { toast('Wklej kod źródłowy, a Sprawdzarka uruchomi go z sanitizerami.', 'info'); srcBox.open = true; return; }
          ctx.navigate('checker', { code });
        } })));
    }

    function renderRuntime(rt) {
      clear(resultsHost);
      if (!rt || rt.kind === 'ok') {
        resultsHost.append(emptyState({ icon: 'circle-check', title: 'To nie wygląda na błąd', text: 'Program zakończył się kodem 0 bez sygnału. Jeśli wynik jest zły (WA), sprawdź logikę albo użyj Testerki z brutem.' }));
        return;
      }
      resultsHost.append(runtimeCard(rt));
    }

    self._load = (params) => {
      if (params.source !== undefined || params.code !== undefined) { ed.setValue(String(params.source ?? params.code ?? '')); srcBox.open = true; }
      if (params.stderr !== undefined) {
        mode = 'compile'; modeSeg.setValue('compile'); applyMode();
        stderrEl.value = String(params.stderr);
        local.set('errors:stderr', stderrEl.value);
        translate();
      } else if (params.run) {
        mode = 'runtime'; modeSeg.setValue('runtime'); applyMode();
        api.post('/api/errors/runtime', { run: params.run, source: ed.getValue() || null }).then((r) => renderRuntime(r.explanation)).catch(() => {});
      }
    };
  },

  onShow(params) {
    if (params && this._load) this._load(params);
  },
};

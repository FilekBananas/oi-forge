// (f) Auto-solver: kandydaci (szablony + Twój kod z podobnych zadań) weryfikowani na przykładach z treści.
import { h, icon, button, viewHeader, card, badge, emptyState, clear, codeBlock, modal, callout, progressBar, table, verdictBadge, toast, copyButton } from '../ui.js';
import { api } from '../api.js';
import { createEditor } from '../editor.js';
import { statementPanel, toolSwitcher, confidenceBar, sampleBox } from './taskexplain.js';

export default {
  id: 'autosolver',
  title: 'Auto-solver',
  icon: 'wand-sparkles',
  group: 'Zadania',
  order: 40,
  description: 'Próbuje napisać rozwiązanie: klasyczne szablony, Twój kod z podobnych zadań i synteza programów (bez AI) — każdy kandydat jest sprawdzany na przykładach z treści.',
  keywords: ['rozwiąż', 'automatycznie', 'solver', 'szablon', 'rozwiązanie', 'wygeneruj kod', 'przykłady'],

  render(root, ctx) {
    const self = this;
    let ctl = null;
    let editor = null;
    let lastText = '';
    const out = h('div', { class: 'tk-results stack' });
    const panel = statementPanel({ actionLabel: 'Szukaj rozwiązania', actionIcon: 'wand-sparkles', onRun: (t) => solve(t) });
    root.append(
      viewHeader(this),
      h('div', { class: 'tk-layout' },
        h('div', { class: 'tk-left stack' }, panel.el, toolSwitcher(ctx, 'autosolver', panel.getText),
          callout('Auto-solver **często się myli** — ale proste zadania (suma, NWD, sito, BFS, plecak, LIS…) potrafi rozwiązać. Każdy kandydat jest uruchamiany na przykładach z treści, więc wiesz, na czym stoisz.', 'info')),
        out));
    out.append(emptyState({ icon: 'wand-sparkles', title: 'Wklej treść i kliknij „Szukaj rozwiązania”',
      text: 'Sprawdzę szablony klasycznych zadań, Twoje rozwiązania podobnych zadań i tysiące programów z syntezy. Wynik: działający kod („przechodzi przykłady 2/2 ✔”), zweryfikowany brute albo uczciwe „nie znalazłem” z gotowym wczytywaniem danych.' }));
    self._panel = panel;
    self._solve = solve;

    async function solve(text) {
      lastText = text;
      if (ctl) { try { await ctl.cancel(); } catch { /* */ } }
      clear(out);
      const pb = progressBar({ label: 'Analizuję treść…' });
      const live = h('div', { class: 'as-live' });
      const compileLine = h('div', { class: 'small muted' });
      const warmBox = h('div');
      const cancelBtn = button('Przerwij', { icon: 'circle-stop', size: 'sm', variant: 'ghost', onClick: () => ctl && ctl.cancel() });
      out.append(card({ title: 'Szukam rozwiązania', icon: 'loader', actions: [cancelBtn], body: h('div', { class: 'stack gap-s' }, pb.el, compileLine, warmBox, live) }));
      // rozgrzewka cache binarek w tle (start serwera) — uprzedź, że pierwsze rozwiązywanie potrwa dłużej
      api.get('/api/autosolver/warmup', {}, { quiet: true }).then((w) => {
        const st = w?.warmup;
        if (st && st.running && !st.ready) warmBox.append(callout(`Trwa rozgrzewka kompilatora w tle: ${st.done}/${st.total} szablonów gotowych. Pierwsze rozwiązywanie może potrwać dłużej — potem kandydaci są brani z cache.`, 'info'));
      }).catch(() => {});
      let compiled = 0, cached = 0;
      ctl = api.job('/api/autosolver/solve', { statement: text }, {
        onProgress: (p, msg) => pb.set(p, msg || ''),
        onEvent: (ev) => {
          if (ev.type === 'compiled') {
            compiled += 1; if (ev.cached) cached += 1;
            compileLine.textContent = `Skompilowano ${compiled}/${ev.total} kandydatów (${cached} z cache, ${compiled - cached} kompilowanych teraz)`;
            return;
          }
          if (ev.type !== 'candidate') return;
          const ok = ev.total > 0 && ev.passed === ev.total;
          live.append(h('div', { class: ['as-live-item', ok ? 'ok' : ''].join(' ') },
            icon(ok ? 'circle-check' : (ev.compiled ? 'x' : 'triangle-alert'), 14),
            h('span', { class: 'grow ellipsis' }, ev.name), badge(`${ev.passed}/${ev.total}`, ok ? 'success' : ev.passed ? 'warn' : 'neutral')));
          live.scrollTop = live.scrollHeight;
        },
      });
      try {
        const r = await ctl.promise;
        clear(out).append(...renderResult(r));
      } catch (e) {
        if (e?.cancelled) { clear(out).append(emptyState({ icon: 'circle-stop', title: 'Przerwano' })); return; }
        clear(out).append(emptyState({ icon: 'circle-alert', title: 'Auto-solver zakończył się błędem', text: e.message || String(e) }));
      } finally { ctl = null; }
    }

    function renderResult(r) {
      const parts = [];
      editor = null;
      const solved = r.status === 'solved';
      const brute = r.status === 'brute' && r.best;   // zweryfikowany brute force, ale za wolny na limity
      const weak = r.status === 'weak' && r.best;     // pasuje do przykładów, ale dowód jest za słaby
      const hasCode = solved || brute || weak;
      const src = r.best ? `${r.best.kind === 'user' ? 'Źródło: ' : r.best.kind === 'synth' ? '' : 'Szablon: '}${r.best.name}` : '';
      const tone = solved ? 'ok' : (weak || brute ? 'weak' : 'no');
      const title = solved ? r.message : brute ? 'Mam poprawny brute force — szybkiego rozwiązania nie znalazłem'
        : weak ? 'Niepewne dopasowanie — nie traktuj tego jak rozwiązania'
          : (r.status === 'no_samples' ? 'Brak przykładów w treści' : 'Nie znalazłem rozwiązania');
      const sub = solved ? `${src}${r.best.renamed && Object.keys(r.best.renamed).length ? ' · nazwy zmiennych jak w treści' : ''}`
        : (brute || weak) ? `${r.message}` : (r.message || '');
      parts.push(h('div', { class: ['as-verdict card', tone].join(' ') }, h('div', { class: 'card-body row nowrap' },
        h('div', { class: 'as-v-icon' }, icon(solved ? 'circle-check' : (brute ? 'hammer' : weak ? 'triangle-alert' : 'circle-alert'), 26)),
        h('div', { class: 'grow' }, h('div', { class: 'as-v-title' }, title), h('div', { class: 'small muted' }, sub)),
        hasCode ? h('div', { style: 'width:180px' }, confidenceBar(r.best.confidence, { label: 'Pewność' })) : null)));
      if (r.warning) parts.push(callout(r.warning, brute ? 'info' : 'warn'));
      const edHost = h('div', { class: 'as-editor' });
      if (hasCode) {
        parts.push(card({
          title: solved ? 'Najlepszy kod' : brute ? 'Brute force (zweryfikowany, za wolny na pełne limity)' : 'Kod kandydata (niepewny — sprawdź testerką)',
          icon: 'file-code', padded: false,
          actions: [
            button('Sprawdzarka', { icon: 'bug', size: 'sm', onClick: () => ctx.navigate('checker', { code: editor.getValue() }) }),
            brute ? button('Testerka (jako brute)', { icon: 'flask-conical', size: 'sm', onClick: () => ctx.navigate('tester', { brute: editor.getValue() }) })
              : button('Testerka', { icon: 'flask-conical', size: 'sm', onClick: () => ctx.navigate('tester', { solution: editor.getValue() }) }),
            button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', title: `Nowe zadanie w edytorze: treść, testy z przykładów i ten kod w ${brute ? 'brute.cpp' : 'rozw.cpp'} — uruchomisz go u siebie`,
              onClick: () => ctx.navigate('workspace', { statement: lastText, code: editor.getValue(), target: brute ? 'brute' : undefined }) }),
            button('Uruchom na przykładach', { icon: 'play', size: 'sm', variant: 'primary', onClick: () => rerun(r) }),
          ],
          body: edHost,
        }));
        requestAnimationFrame(() => { editor = createEditor(edHost, { value: r.best.code, lang: 'cpp', height: '460px' }); });
      }
      if (r.competitors?.length) parts.push(competitorsCard(r.competitors, (code) => editor && editor.setValue(code)));
      if (!solved && r.reader) parts.push(readerCard(r.reader, ctx, hasCode ? (code) => editor && editor.setValue(code) : null, lastText));
      if (!solved && r.hints?.length) parts.push(card({ title: 'Wskazówki', icon: 'lightbulb', body: h('ul', { class: 'as-hints' }, r.hints.map((t) => h('li', null, t))) }));
      const rerunBox = h('div', { class: 'stack gap-s' });
      parts.push(rerunBox);
      self._rerunBox = rerunBox;
      if (!solved && r.closest) {
        parts.push(card({ title: `Najbliżej był: ${r.closest.name} (${r.closest.passed}/${r.closest.total})`, icon: 'target',
          subtitle: 'nie przechodzi wszystkich przykładów',
          actions: hasCode ? [button('Wstaw do edytora', { icon: 'arrow-right', size: 'sm', onClick: () => editor && editor.setValue(r.closest.code) })] : [],
          body: codeBlock(r.closest.code, 'cpp', { maxHeight: 260 }) }));
      }
      if (r.alternatives?.length) {
        parts.push(card({ title: 'Inne kandydaty, które też przechodzą przykłady', icon: 'git-branch',
          body: h('div', { class: 'row gap-s' }, r.alternatives.map((a) => button(a.name, { icon: 'code-xml', size: 'sm', onClick: () => showCode(a.name, a.code) }))) }));
      }
      // klasyfikacja
      const cl = r.classification || {};
      parts.push(card({ title: 'Jak sklasyfikowałem zadanie', icon: 'brain-circuit',
        body: h('div', { class: 'stack gap-s' },
          h('div', { class: 'row gap-s' }, h('span', { class: 'small muted' }, 'Kształt wejścia:'), (r.shape || []).filter((x) => x !== 'ANY').map((s) => badge(s, 'neutral')),
            cl.complexity ? [h('span', { class: 'small muted' }, ' · cel:'), badge(cl.complexity, 'accent')] : null),
          cl.algorithms?.length ? h('div', { class: 'row gap-s' }, h('span', { class: 'small muted' }, 'Doradca:'), cl.algorithms.map((a) => badge(`${a.name} ${Math.round(100 * a.confidence)}%`, 'info'))) : null,
          cl.topics?.length ? h('div', { class: 'row gap-s' }, h('span', { class: 'small muted' }, 'Temat (Twoje zadania):'), cl.topics.map((t) => badge(`${t.name} ${Math.round(100 * t.p)}%`, 'neutral'))) : null) }));
      // kandydaci
      const cands = r.candidates || [];
      parts.push(card({
        title: `Kandydaci (${cands.length})`, icon: 'list-checks', padded: false,
        body: table([
          { key: 'name', label: 'Kandydat', render: (c) => h('div', { class: 'stack gap-s', style: 'gap:2px' }, h('b', null, c.name),
            c.reasons?.length ? h('span', { class: 'small muted' }, c.reasons.join(' · ')) : null) },
          { key: 'kind', label: 'Źródło', render: (c) => badge(c.kind === 'user' ? 'Twój kod' : c.kind === 'synth' ? 'synteza' : 'szablon', c.kind === 'user' ? 'accent' : 'neutral') },
          { key: 'passed', label: 'Przykłady', render: (c) => !c.compiled && c.compiled !== undefined ? verdictBadge('CE') : h('span', { class: 'row gap-s nowrap' },
            badge(`${c.passed}/${c.total}`, c.total && c.passed === c.total ? (c.sig_hit ? 'success' : 'warn') : c.passed ? 'warn' : 'danger'),
            c.skipped ? h('span', { class: 'small muted', title: 'Bardzo duże przykłady pominięte (kandydat oblał wcześniejszy przykład)' }, `+${c.skipped} pominięte`) : null,
            c.total && c.passed === c.total && !c.sig_hit ? badge('tylko kształt', 'warn') : null) },
          { key: 'confidence', label: 'Pewność', render: (c) => h('div', { style: 'width:120px' }, confidenceBar(c.confidence)) },
        ], cands, { compact: true, onRowClick: (c) => showCandidate(c) }),
      }));
      if (r.samples?.length) parts.push(card({ title: 'Przykłady z treści', icon: 'flask-conical', body: h('div', { class: 'stack' }, r.samples.map((s, i) => sampleBox(s, i))) }));
      return parts;
    }

    async function rerun(r) {
      const box = self._rerunBox;
      if (!box || !editor) return;
      clear(box);
      for (const [i, s] of (r.samples || []).entries()) {
        const row = h('div', { class: 'row gap-s' }, h('b', null, s.name || `Przykład ${i + 1}`), h('span', { class: 'spinner' }));
        box.append(row);
        try {
          const res = await api.post('/api/run', { code: editor.getValue(), input: s.input, timeout: (s.input || '').length > 100000 ? 5 : undefined }, { quiet: true });
          row.querySelector('.spinner')?.remove();
          if (!res.ok || (res.compile && !res.compile.ok)) { row.append(verdictBadge('CE')); continue; }
          const same = (res.run.stdout || '').trim().split(/\s+/).join(' ') === (s.output || '').trim().split(/\s+/).join(' ');
          row.append(verdictBadge(res.run.verdict === 'OK' ? (same ? 'OK' : 'WA') : res.run.verdict),
            !same ? h('code', { class: 'small' }, (res.run.stdout || '').trim().slice(0, 60) || '(puste)') : null);
        } catch (e) { row.querySelector('.spinner')?.remove(); row.append(badge('błąd', 'danger')); }
      }
    }

    function showCandidate(c) {
      const body = h('div', { class: 'stack' });
      (c.results || []).forEach((x, i) => x && body.append(h('div', { class: 'row gap-s' }, h('b', null, x.name || `Przykład ${i + 1}`), x.skipped ? badge('pominięty (duże wejście)', 'neutral') : verdictBadge(x.ok ? 'OK' : (x.verdict || 'WA')),
        !x.ok && !x.skipped ? h('span', { class: 'small muted' }, 'otrzymano: ', h('code', null, (x.got || '').trim().slice(0, 80) || '(puste)'), ' · oczekiwano: ', h('code', null, (x.expected || '').trim().slice(0, 80))) : null)));
      if (c.compile_error) body.append(callout('Błąd kompilacji:\n```\n' + c.compile_error.slice(0, 2000) + '\n```', 'danger'));
      if (c.code) body.append(codeBlock(c.code, 'cpp', { lineNumbers: true, maxHeight: 460 }));
      const m = modal({ title: c.name, icon: 'code-xml', width: 860, body,
        actions: [button('Wstaw do edytora', { icon: 'arrow-right', variant: 'primary', onClick: () => {
          if (editor && c.code) { editor.setValue(c.code); toast('Wstawiono kod kandydata', 'success'); }
          m.close();
        } })] });
    }
  },

  onShow(params) {
    if (params && params.statement && this._panel) { this._panel.setText(params.statement); this._solve(params.statement); }
    else if (this._panel) this._panel.refresh();
  },
};

function showCode(title, code) {
  modal({ title, icon: 'code-xml', width: 860, body: codeBlock(code, 'cpp', { lineNumbers: true, maxHeight: 520 }) });
}

// Gotowe wczytywanie danych — osobno, wyraźnie opisane: to NIE jest rozwiązanie.
export function readerCard(reader, ctx, insert, statement = '', target) {
  return card({ title: 'Gotowe wczytywanie danych', icon: 'file-input', subtitle: 'to nie jest rozwiązanie — tylko wczytuje dane z formatu treści',
    actions: [copyButton(() => reader, { label: 'Kopiuj' }),
      insert ? button('Wstaw do kodu powyżej', { icon: 'arrow-up', size: 'sm', onClick: () => insert(reader) }) : null,
      button('Otwórz w edytorze', { icon: 'file-code', size: 'sm', title: statement ? 'Nowe zadanie w edytorze: treść, testy z przykładów i to wczytywanie jako punkt wyjścia' : 'Otwórz w edytorze',
        onClick: () => ctx.navigate('workspace', statement ? { statement, code: reader, target } : { code: reader, name: 'wczytywanie.cpp' }) })],
    body: codeBlock(reader, 'cpp', { copy: false, maxHeight: 300 }) });
}

// Kilka programów pasuje do przykładów, ale różnią się na losowym teście — pokazujemy, na którym.
export function competitorsCard(list, insert) {
  return card({ title: 'Inne programy też pasują do przykładów', icon: 'git-compare', subtitle: 'przykłady ich nie rozróżniają — zdecyduj, który liczy to, o co pyta treść',
    body: h('div', { class: 'stack gap-s' }, list.map((c) => h('div', { class: 'as-comp' },
      h('div', { class: 'row nowrap gap-s' }, h('b', { class: 'grow' }, c.desc),
        c.code ? button(null, { icon: 'eye', size: 'sm', variant: 'ghost', title: 'Podgląd kodu', onClick: () => showCode(c.desc, c.code) }) : null,
        c.code && insert ? button(null, { icon: 'arrow-right', size: 'sm', variant: 'ghost', title: 'Wstaw do edytora', onClick: () => insert(c.code) }) : null),
      c.input ? h('div', { class: 'as-comp-diff small' },
        h('div', { class: 'muted' }, 'Różnią się np. na wejściu:'), h('pre', { class: 'mono' }, c.input.trim()),
        h('div', null, 'wybrany: ', h('code', null, c.best_output || '—'), ' · ten: ', h('code', null, c.output || '—'))) : null))) });
}

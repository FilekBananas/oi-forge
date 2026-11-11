// (s) Output Difference Viewer — porównanie dwóch wyjść: pierwsza różnica, widok obok siebie, statystyki.
import { h, icon, button, iconButton, viewHeader, card, input, textarea, segmented, checkbox, callout, stat,
  clear, debounce, toast, fileDrop, readFileText, copyButton } from '../ui.js';
import { api, local } from '../api.js';

const MODE_OPTS = [
  { value: 'tokens', label: 'Tokeny', title: 'Białe znaki nieistotne — tak porównuje sprawdzarka OI' },
  { value: 'lines', label: 'Linie', title: 'Porównanie linia po linii' },
  { value: 'chars', label: 'Znaki', title: 'Dokładne porównanie znak po znaku' },
];

// ------------------------------------------------------------------------------------------- wspólne renderery
/** Widoczny znak w miejscu różnicy (spacja, koniec tekstu…). */
function showTok(t) {
  if (t === null || t === undefined) return h('span', { class: 'df-eof' }, 'koniec');
  if (t === ' ') return h('span', { class: 'df-ws' }, '␠');
  if (t === '\n') return h('span', { class: 'df-ws' }, '↵');
  if (t === '\t') return h('span', { class: 'df-ws' }, '⇥');
  return h('code', null, String(t).length > 80 ? String(t).slice(0, 80) + '…' : String(t));
}

/** Baner „pierwsza różnica: linia X, znak Y, token Z”. */
export function diffBanner(res, { labelA = 'Twoje', labelB = 'oczekiwane' } = {}) {
  if (!res) return null;
  if (res.equal) {
    return h('div', { class: 'df-banner ok' }, icon('circle-check', 20),
      h('div', { class: 'grow' }, h('div', { class: 'df-b-title' }, 'Wyjścia są zgodne'),
        h('div', { class: 'df-b-sub' }, res.hints && res.hints.length ? res.hints[0] : 'Brak różnic w wybranym trybie porównania.')));
  }
  const f = res.first || {};
  const where = [];
  if (f.line) where.push(h('span', { class: 'df-pill' }, 'linia ', h('b', null, String(f.line))));
  if (f.col) where.push(h('span', { class: 'df-pill' }, 'znak ', h('b', null, String(f.col))));
  if (f.token_no) where.push(h('span', { class: 'df-pill' }, 'token ', h('b', null, String(f.token_no))));
  const kindTxt = { missing: 'Za krótkie wyjście', extra: 'Za długie wyjście' }[f.kind] || 'Pierwsza różnica';
  const ctx = (arr, start, idx) => (arr || []).map((t, i) => h('span', { class: start + i === idx ? 'df-ctx-hit' : 'df-ctx' }, t));
  return h('div', { class: 'df-banner bad' }, icon('git-compare', 20),
    h('div', { class: 'grow stack gap-s' },
      h('div', { class: 'row gap-s' }, h('span', { class: 'df-b-title' }, kindTxt + ':'), where),
      h('div', { class: 'df-vs' },
        h('div', null, h('span', { class: 'df-lab' }, labelA), showTok(f.token_a)),
        h('div', null, h('span', { class: 'df-lab' }, labelB), showTok(f.token_b))),
      f.context_a && (f.context_a.length || f.context_b.length) ? h('div', { class: 'df-context mono small' },
        h('div', null, h('span', { class: 'df-lab' }, 'kontekst ' + labelA.toLowerCase()), ctx(f.context_a, f.context_start, f.index)),
        h('div', null, h('span', { class: 'df-lab' }, 'kontekst ' + labelB.toLowerCase()), ctx(f.context_b, f.context_start, f.index))) : null,
      h('div', { class: 'df-b-sub' }, f.message || '')));
}

function segs(list) {
  return (list || []).map(([t, changed]) => (changed ? h('span', { class: 'diff-mark' }, t) : t));
}

/** Tabela różnic obok siebie (wiersze z /api/diff). */
export function diffTable(res, { labelA = 'Twoje wyjście', labelB = 'Oczekiwane', maxHeight = '520px' } = {}) {
  const rows = res?.rows || [];
  const body = h('tbody');
  for (const r of rows) {
    if (r.type === 'skip') {
      body.append(h('tr', { class: 'df-skip' }, h('td', { colspan: 4 },
        r.truncated ? '… (dalsze różnice pominięte — za dużo, by pokazać)' : `⋯ ${r.count} jednakowych linii ⋯`)));
      continue;
    }
    const cls = { eq: 'diff-eq', chg: 'df-chg', del: 'diff-del', add: 'diff-add' }[r.type] || '';
    const a = r.type === 'chg' && r.seg_a ? segs(r.seg_a) : (r.a ?? '');
    const b = r.type === 'chg' && r.seg_b ? segs(r.seg_b) : (r.b ?? '');
    body.append(h('tr', { class: cls },
      h('td', { class: 'df-no' }, r.a_no ?? ''),
      h('td', { class: ['df-cell', r.type === 'del' || r.type === 'chg' ? 'df-a-bad' : ''].join(' ') }, r.a === null ? h('span', { class: 'faint' }, '—') : a),
      h('td', { class: 'df-no' }, r.b_no ?? ''),
      h('td', { class: ['df-cell', r.type === 'add' || r.type === 'chg' ? 'df-b-bad' : ''].join(' ') }, r.b === null ? h('span', { class: 'faint' }, '—') : b)));
  }
  if (!rows.length) body.append(h('tr', null, h('td', { colspan: 4, class: 'muted center' }, '(oba teksty puste)')));
  return h('div', { class: 'df-table-wrap', style: { maxHeight } },
    h('table', { class: 'df-table mono' },
      h('thead', null, h('tr', null, h('th', { class: 'df-no' }, '#'), h('th', null, labelA), h('th', { class: 'df-no' }, '#'), h('th', null, labelB))),
      body));
}

/** Kafelki statystyk. */
export function diffStats(res) {
  const s = res?.stats;
  if (!s) return null;
  const tone = (v) => (v ? 'danger' : 'success');
  return h('div', { class: 'df-stats' },
    stat({ label: 'Linie (A / B)', value: `${s.lines_a} / ${s.lines_b}`, tone: s.lines_a !== s.lines_b ? 'warn' : undefined }),
    stat({ label: 'Tokeny (A / B)', value: `${s.tokens_a} / ${s.tokens_b}`, tone: s.token_count_differs ? 'warn' : undefined }),
    stat({ label: 'Różne linie', value: s.diff_lines, tone: tone(s.diff_lines), sub: s.approximate ? 'diff przybliżony' : `${s.changed_lines} zm., ${s.added_lines} brak, ${s.deleted_lines} nadm.` }),
    stat({ label: 'Różne tokeny', value: s.diff_tokens, tone: tone(s.diff_tokens) }),
    s.missing_at_end ? stat({ label: 'Brak na końcu', value: `${s.missing_at_end} tok.`, tone: 'danger' }) : null,
    s.extra_at_end ? stat({ label: 'Nadmiar na końcu', value: `${s.extra_at_end} tok.`, tone: 'danger' }) : null);
}

/** Kompletny blok wyniku porównania (baner + podpowiedzi + tabela) — używany też przez testerki. */
export function diffResult(res, opts = {}) {
  if (!res) return null;
  return h('div', { class: 'stack gap-s' }, diffBanner(res, opts),
    !res.equal && res.hints && res.hints.length ? h('div', { class: 'stack gap-s' }, res.hints.map((x) => callout(x, 'tip'))) : null,
    opts.stats ? diffStats(res) : null,
    !res.equal ? diffTable(res, { labelA: opts.tableA || 'Twoje wyjście', labelB: opts.tableB || 'Oczekiwane', maxHeight: opts.maxHeight }) : null);
}

// ------------------------------------------------------------------------------------------- widok
export default {
  id: 'diff', title: 'Porównanie wyjść', icon: 'git-compare', group: 'Testowanie', order: 30,
  description: 'Porównaj dwa wyjścia: pierwsza różnica (linia, znak, token), widok obok siebie i statystyki — jak sprawdzarka OI.',
  keywords: ['diff', 'różnice', 'porównaj', 'wyjście', 'output', 'compare', 'wa'],

  render(root, ctx) {
    const saved = local.get('diff:opts', {}) || {};
    const opts = { mode: saved.mode || 'tokens', iw: saved.iw !== false, eps: saved.eps || '' };
    const saveOpts = () => local.set('diff:opts', opts);
    const resultBox = h('div', { class: 'stack' });
    let lastReq = 0;

    const taA = textarea({ rows: 14, placeholder: 'Wklej wyjście swojego programu… (albo upuść plik)', onInput: () => auto() });
    const taB = textarea({ rows: 14, placeholder: 'Wklej oczekiwane wyjście (np. z .out albo z brute’a)…', onInput: () => auto() });
    taA.classList.add('df-input'); taB.classList.add('df-input');
    const countA = h('span', { class: 'muted tiny' });
    const countB = h('span', { class: 'muted tiny' });
    const updCounts = () => {
      const c = (t) => { const s = t.value; return `${s ? s.split('\n').length : 0} linii · ${s.split(/\s+/).filter(Boolean).length} tokenów`; };
      countA.textContent = c(taA); countB.textContent = c(taB);
    };
    const dropOn = (ta) => {
      ta.addEventListener('dragover', (e) => { e.preventDefault(); ta.classList.add('over'); });
      ta.addEventListener('dragleave', () => ta.classList.remove('over'));
      ta.addEventListener('drop', async (e) => {
        e.preventDefault(); ta.classList.remove('over');
        const f = e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) { ta.value = await readFileText(f); auto(); }
      });
    };
    dropOn(taA); dropOn(taB);
    const loadBtn = (ta) => {
      const inp = h('input', { type: 'file', style: 'display:none', onchange: async () => { const f = inp.files[0]; if (f) { ta.value = await readFileText(f); inp.value = ''; auto(); } } });
      return h('span', null, inp, iconButton('upload', { title: 'Wczytaj z pliku', onClick: () => inp.click() }));
    };

    const modeSeg = segmented(MODE_OPTS, { value: opts.mode, onChange: (v) => { opts.mode = v; saveOpts(); run(); } });
    const iwBox = checkbox('Ignoruj białe znaki', { checked: opts.iw, onChange: (v) => { opts.iw = v; saveOpts(); run(); } });
    const epsInp = input({ value: opts.eps, placeholder: 'np. 1e-6', mono: true, size: 'sm', onInput: (v) => { opts.eps = v.trim(); saveOpts(); auto(); } });
    epsInp.style.width = '110px';

    async function run() {
      updCounts();
      const a = taA.value; const b = taB.value;
      if (!a && !b) { clear(resultBox).append(emptyHint()); return; }
      const req = ++lastReq;
      try {
        const res = await api.post('/api/diff', { a, b, mode: opts.mode, ignore_whitespace: opts.iw, float_eps: opts.eps || null, context: 3 });
        if (req !== lastReq) return;
        clear(resultBox).append(
          diffBanner(res, { labelA: 'A (Twoje)', labelB: 'B (oczekiwane)' }),
          !res.equal && res.hints?.length ? h('div', { class: 'stack gap-s' }, res.hints.map((x) => callout(x, 'tip'))) : null,
          diffStats(res),
          res.equal ? null : card({ title: 'Obok siebie', icon: 'table', padded: false,
            subtitle: res.truncated ? 'Pokazano część różnic' : 'Zmienione fragmenty są podświetlone; długie zgodne fragmenty zwinięte',
            body: diffTable(res, { labelA: 'A — Twoje wyjście', labelB: 'B — oczekiwane', maxHeight: '60vh' }) }));
      } catch (e) {
        if (req === lastReq) clear(resultBox).append(callout(e.message, 'danger'));
      }
    }
    const autoDeb = debounce(run, 450);
    function auto() { updCounts(); if (taA.value.length + taB.value.length < 400_000) autoDeb(); }
    const emptyHint = () => callout('Wklej oba wyjścia (albo upuść pliki na pola). Porównanie odświeża się samo. '
      + 'Tryb **Tokeny** działa jak sprawdzarka OI — liczą się tylko kolejne „słowa”, nie spacje i entery.', 'info');

    root.append(
      viewHeader(this,
        button('Zamień A ↔ B', { icon: 'refresh-cw', variant: 'ghost', onClick: () => { const t = taA.value; taA.value = taB.value; taB.value = t; run(); } }),
        button('Wyczyść', { icon: 'trash-2', variant: 'ghost', onClick: () => { taA.value = ''; taB.value = ''; run(); } }),
        button('Porównaj', { icon: 'git-compare', variant: 'primary', onClick: () => run() })),
      h('div', { class: 'toolbar df-toolbar' }, h('span', { class: 'muted small' }, 'Tryb'), modeSeg, h('span', { class: 'sep' }), iwBox,
        h('span', { class: 'sep' }), h('span', { class: 'muted small' }, 'Tolerancja liczb'), epsInp,
        h('span', { class: 'muted tiny grow right' }, 'Ctrl+Enter — porównaj')),
      h('div', { class: 'grid-2 mt-m' },
        h('div', { class: 'stack gap-s' }, h('div', { class: 'row between' }, h('b', null, 'A — Twoje wyjście'), h('div', { class: 'row gap-s' }, countA, loadBtn(taA), copyButton(() => taA.value))), taA),
        h('div', { class: 'stack gap-s' }, h('div', { class: 'row between' }, h('b', null, 'B — oczekiwane'), h('div', { class: 'row gap-s' }, countB, loadBtn(taB), copyButton(() => taB.value))), taB)),
      h('div', { class: 'mt-m' }, fileDrop({ multiple: true, label: 'Albo upuść tu dwa pliki naraz (pierwszy → A, drugi → B)',
        onFiles: async (files) => {
          const list = Array.isArray(files) ? files : [files];
          if (list[0]) taA.value = await readFileText(list[0]);
          if (list[1]) taB.value = await readFileText(list[1]);
          run();
        } })),
      h('div', { class: 'mt-l' }, resultBox));
    root.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run(); } });
    resultBox.append(emptyHint());
    this._set = (a, b) => { if (a !== undefined) taA.value = a ?? ''; if (b !== undefined) taB.value = b ?? ''; run(); };
    if (ctx?.params && (ctx.params.a !== undefined || ctx.params.b !== undefined)) this._set(ctx.params.a, ctx.params.b);
  },

  onShow(params) {
    if (params && (params.a !== undefined || params.b !== undefined) && this._set) {
      this._set(params.a, params.b);
      toast('Wczytano wyjścia do porównania', 'info', { timeout: 1500 });
    }
  },
};

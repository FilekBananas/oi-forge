// Kalkulatory OI (agent J): modularna, liczby, bity, systemy, limity złożoności, pamięć. Backend: POST /api/calc/<op>.
import { h, icon, button, iconButton, viewHeader, tabs, input, textarea, select, segmented, badge, callout, copyButton, clear, debounce, timeAgo, plural, table } from '../ui.js';
import { api, store, local, settings } from '../api.js';

const TAB_META = {
  modular: { label: 'Modularna', icon: 'square-function' },
  numbers: { label: 'Liczby', icon: 'sigma' },
  bits: { label: 'Bity', icon: 'binary' },
  bases: { label: 'Systemy', icon: 'languages' },
  limits: { label: 'Limity', icon: 'timer' },
  memory: { label: 'Pamięć', icon: 'database' },
};
const MOD_PRESETS = [['10⁹+7', '1e9+7'], ['998244353', '998244353'], ['10⁹+9', '1e9+9'], ['2⁶¹−1', '2^61-1'], ['10⁹', '1e9']];

// ------------------------------------------------------------------------------------------- pomocnicze
function tile(label, value, { tone, sub, copy = true, wide = false, big = false } = {}) {
  const has = value !== null && value !== undefined && value !== '';
  return h('div', { class: ['calc-tile', tone ? 'tone-' + tone : '', wide ? 'wide' : '', big ? 'big' : '', has ? '' : 'na'].join(' ') },
    h('div', { class: 'ct-head' }, h('span', { class: 'ct-label' }, label), copy && has ? copyButton(() => String(value)) : null),
    h('div', { class: 'ct-value mono' }, has ? String(value) : '—'),
    sub ? h('div', { class: 'ct-sub' }, sub) : null);
}

function pretty(fact) {
  const out = h('span', { class: 'calc-fact' });
  String(fact).split(' · ').forEach((part, i) => {
    if (i) out.append(h('span', { class: 'dot' }, '·'));
    const m = part.match(/^(\[?)([^\^\]]+)(\]?)(?:\^(\d+))?$/);
    if (!m) { out.append(part); return; }
    out.append(h('span', { class: m[1] ? 'p unk' : 'p' }, m[1] + m[2] + m[3], m[4] ? h('sup', null, m[4]) : null));
  });
  return out;
}

function errBox(msg) { return h('div', { class: 'calc-err' }, icon('circle-alert', 15), h('span', null, msg)); }

/** Sekcja z polami i wynikiem liczonym na żywo (opóźnienie, ignorowanie spóźnionych odpowiedzi). */
function liveSection({ title, icon: ic, subtitle, controls, compute, render, onResult, delay = 230, actions = [] }) {
  const out = h('div', { class: 'calc-out' });
  const spin = h('span', { class: 'spinner calc-spin', hidden: true });
  let seq = 0;
  const run = async () => {
    const my = ++seq;
    spin.hidden = false;
    try {
      const res = await compute();
      if (my !== seq) return;
      clear(out);
      if (res === null || res === undefined) return;
      out.append(render(res));
      onResult && onResult(res);
    } catch (e) {
      if (my !== seq) return;
      clear(out);
      out.append(errBox(e.message || String(e)));
    } finally {
      if (my === seq) spin.hidden = true;
    }
  };
  const d = debounce(run, delay);
  const el = h('div', { class: 'card calc-card' },
    h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon(ic, 17)),
      h('div', { class: 'grow' }, h('div', { class: 'card-title' }, title), subtitle ? h('div', { class: 'card-sub' }, subtitle) : null), spin, actions),
    h('div', { class: 'card-body' }, h('div', { class: 'calc-controls' }, controls), out));
  return { el, run: d, now: () => d.flush() };
}

function field(label, ctl, hint) {
  return h('label', { class: 'calc-field' }, h('span', { class: 'lbl' }, label), ctl, hint ? h('span', { class: 'hint' }, hint) : null);
}

function inp(value, onInput, { ph = '', mono = true, cls } = {}) {
  const e = input({ value, placeholder: ph, mono, onInput: () => onInput(e.value) });
  if (cls) e.classList.add(cls);
  return e;
}

function chips(list, onPick) {
  return h('div', { class: 'calc-chips' }, list.map(([label, value]) => h('button', { class: 'chip', type: 'button', onclick: () => onPick(value) }, label)));
}

const post = (op, body) => api.post('/api/calc/' + op, body, { quiet: true });

// ------------------------------------------------------------------------------------------- historia
function makeHistory(onRestore) {
  let items = local.get('calc:history', []) || [];
  const list = h('div', { class: 'calc-hist-list' });
  const saveRemote = debounce(() => store.set('calc_history', items).catch(() => {}), 1200);
  const save = () => { local.set('calc:history', items); saveRemote(); };
  store.get('calc_history', null).then((remote) => { if (Array.isArray(remote) && remote.length && (!items.length || remote[0].ts > (items[0]?.ts || 0))) { items = remote; draw(); } });
  function draw() {
    clear(list);
    if (!items.length) { list.append(h('div', { class: 'calc-hist-empty' }, icon('history', 22), h('div', null, 'Tu pojawią się ostatnie obliczenia. Kliknij wpis, aby do niego wrócić.'))); return; }
    for (const it of items) {
      const meta = TAB_META[it.tab] || { icon: 'calculator' };
      list.append(h('button', { class: 'calc-hist-item', type: 'button', title: 'Przywróć', onclick: () => onRestore(it) },
        h('span', { class: 'hi-icon' }, icon(meta.icon, 14)),
        h('span', { class: 'hi-body' }, h('span', { class: 'hi-title' }, it.title), h('span', { class: 'hi-sum mono' }, it.summary), h('span', { class: 'hi-time' }, timeAgo(new Date(it.ts))))));
    }
  }
  const pending = {};
  function add(tab, title, summary, inputs) {
    clearTimeout(pending[tab]);
    pending[tab] = setTimeout(() => {
      const now = Date.now();
      const head = items[0];
      const entry = { tab, title, summary: String(summary).slice(0, 160), inputs, ts: now };
      if (head && head.tab === tab && head.title === title && now - head.ts < 12000) items[0] = entry;
      else if (!(head && head.tab === tab && head.summary === entry.summary)) items.unshift(entry);
      items = items.slice(0, 40);
      save(); draw();
    }, 1400);
  }
  draw();
  const el = h('aside', { class: 'card calc-hist' },
    h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('history', 17)), h('div', { class: 'grow card-title' }, 'Historia'),
      iconButton('trash-2', { title: 'Wyczyść historię', onClick: () => { items = []; save(); draw(); } })),
    list);
  setInterval(() => { if (el.isConnected && !el.closest('[hidden]')) draw(); }, 60000);
  return { el, add };
}

// ===========================================================================================  widok
export default {
  id: 'calculators',
  title: 'Kalkulatory',
  icon: 'calculator',
  group: 'Narzędzia',
  order: 20,
  description: 'Kalkulator OI (modulo, nCr, rozkład na czynniki do 10¹⁸, NWD/CRT, bity i double, systemy liczbowe) oraz kalkulator limitów czasu i pamięci dla Twoich ograniczeń.',
  keywords: ['kalkulator', 'modulo', 'mod', 'odwrotność', 'potęga', 'ncr', 'dwumian', 'czynniki', 'pierwsza', 'nwd', 'nww', 'crt', 'bity', 'popcount', 'double', 'system', 'hex', 'binarny', 'złożoność', 'limit czasu', 'pamięć', 'mb', 'memory'],

  async render(root) {
    const S = await settings().catch(() => ({}));
    const sections = {};
    const hist = makeHistory((it) => restore(it));
    const t = tabs(Object.entries(TAB_META).map(([id, m]) => ({ id, label: m.label, icon: m.icon, render: () => TABS[id](sections, hist, S) })),
      { active: local.get('calc:tab', 'modular'), onChange: (id) => local.set('calc:tab', id) });
    this._tabs = t;
    this._sections = sections;
    root.append(viewHeader(this), h('div', { class: 'calc-root' }, h('div', { class: 'calc-main' }, t.el), hist.el));
    function restore(it) {
      t.select(it.tab);
      const sec = sections[it.tab];
      if (sec && sec.restore) sec.restore(it.inputs || {});
    }
  },

  onShow(params) {
    if (!params || !this._tabs) return;
    const tab = params.tab && TAB_META[params.tab] ? params.tab : (params.decls ? 'memory' : null);
    if (!tab) return;
    this._tabs.select(tab);
    const sec = this._sections[tab];
    if (sec && sec.restore) sec.restore(params);
  },
};

// ===========================================================================================  zakładki
const TABS = {
  // ------------------------------------------------------------------------------------- modularna
  modular(sections, hist) {
    const st = { a: '123456789', b: '987654321', m: '1e9+7', n: '1000', k: '500', p: '1e9+7', sn: '100000', sm: '1e9+7', kind: 'factorial', ...(local.get('calc:modular', {}) || {}) };
    const persist = () => local.set('calc:modular', st);
    const aI = inp(st.a, (v) => { st.a = v; persist(); ar.run(); }, { ph: 'np. 2^30 albo -5' });
    const bI = inp(st.b, (v) => { st.b = v; persist(); ar.run(); }, { ph: 'np. 10^18' });
    const mI = inp(st.m, (v) => { st.m = v; persist(); ar.run(); }, { ph: 'np. 1e9+7' });
    const ar = liveSection({
      title: 'Arytmetyka modulo m', icon: 'square-function', subtitle: 'Wpisuj wyrażenia: 2^61-1, 1e9+7, 1<<20 — wyniki liczą się na żywo (dowolnie duże liczby).',
      controls: [h('div', { class: 'calc-row' }, field('a', aI), field('b (też wykładnik)', bI), field('m', mI)), chips(MOD_PRESETS, (v) => { mI.value = v; st.m = v; persist(); ar.now(); })],
      compute: async () => {
        const [r, pw] = await Promise.all([post('modular', { a: st.a, b: st.b, m: st.m }), post('pow', { a: st.a, e: st.b, m: st.m }).catch(() => null)]);
        return { r, pw };
      },
      render: ({ r, pw }) => h('div', { class: 'stack gap-s' },
        h('div', { class: 'calc-tiles' },
          tile('a + b', r.sum), tile('a − b', r.diff), tile('a · b', r.prod), tile('a / b = a · b⁻¹', r.quot, { sub: r.quot === null ? `brak: NWD(b, m) = ${r.gcd_b}` : null }),
          tile('a^b', r.pow, { tone: 'accent' }), tile('a⁻¹', r.inv_a, { sub: r.inv_a === null ? `brak: NWD(a, m) = ${r.gcd_a}` : null }),
          tile('b⁻¹', r.inv_b), tile('a mod m', r.a, { sub: r.a_raw_negative ? 'po normalizacji ujemnego a' : null })),
        h('div', { class: 'row gap-s' }, r.m_prime === true ? badge('m jest pierwsze', 'success', 'check') : r.m_prime === false ? badge('m jest złożone', 'warn') : null, badge(`m = ${r.m}`, 'neutral')),
        ...(r.hints || []).map((x) => callout(x, 'warn')),
        pw && pw.steps && pw.steps.length ? h('details', { class: 'calc-details' }, h('summary', null, `Szybkie potęgowanie: ${pw.steps.length} kroków (bity wykładnika od najmłodszego)`),
          table([{ key: 'i', label: '#' }, { key: 'bit', label: 'bit' }, { key: 'base', label: 'a^(2^i) mod m' }, { key: 'res', label: 'wynik po kroku' }],
            pw.steps.map((s, i) => ({ i, bit: s.bit, base: s.base, res: s.res })), { compact: true, maxHeight: '260px' }),
          h('div', { class: 'muted small mt-s' }, 'W C++: `while (e) { if (e & 1) r = r * a % m; a = a * a % m; e >>= 1; }` — O(log e).')) : null),
      onResult: (res) => hist.add('modular', 'Modulo', `a=${st.a}, b=${st.b}, m=${st.m} → a^b=${res.r.pow ?? '—'}`, { a: st.a, b: st.b, m: st.m }),
    });

    const nI = inp(st.n, (v) => { st.n = v; persist(); nc.run(); });
    const kI = inp(st.k, (v) => { st.k = v; persist(); nc.run(); });
    const pI = inp(st.p, (v) => { st.p = v; persist(); nc.run(); }, { ph: 'puste = dokładnie' });
    const nc = liveSection({
      title: 'Dwumian Newtona C(n, k) mod p', icon: 'sigma', subtitle: 'Silnie z odwrotnościami, Lucas dla małego p (n do 10¹⁸), Lucas + CRT dla modułów bezkwadratowych.',
      controls: [h('div', { class: 'calc-row' }, field('n', nI), field('k', kI), field('p (moduł)', pI))],
      compute: () => post('ncr', { n: st.n, k: st.k, p: st.p }),
      render: (r) => h('div', { class: 'stack gap-s' },
        h('div', { class: 'calc-tiles' }, tile(`C(n, k)${st.p ? ' mod p' : ''}`, r.value, { tone: 'accent', big: true }),
          r.exact && st.p ? tile('dokładna wartość', r.exact, { sub: r.digits ? `${r.digits} ${plural(r.digits, 'cyfra', 'cyfry', 'cyfr')}` : null, wide: true }) : null),
        h('div', { class: 'muted small' }, icon('info', 13), ' Metoda: ', r.method),
        r.note ? callout(r.note, 'warn') : null),
      onResult: (r) => hist.add('modular', 'nCr', `C(${st.n}, ${st.k})${st.p ? ' mod ' + st.p : ''} = ${r.value}`, { n: st.n, k: st.k, p: st.p }),
    });

    const snI = inp(st.sn, (v) => { st.sn = v; persist(); sq.run(); });
    const smI = inp(st.sm, (v) => { st.sm = v; persist(); sq.run(); }, { ph: 'puste = dokładnie' });
    const kindSeg = segmented([{ value: 'factorial', label: 'n!' }, { value: 'fibonacci', label: 'F(n)' }], { value: st.kind, onChange: (v) => { st.kind = v; persist(); sq.now(); } });
    const sq = liveSection({
      title: 'Silnia i Fibonacci modulo m', icon: 'activity', subtitle: 'n! mod m (z tw. Wilsona dla n blisko m) i F(n) mod m szybkim podwajaniem — n nawet 10¹⁸.',
      controls: [h('div', { class: 'calc-row' }, field('ciąg', kindSeg), field('n', snI), field('m', smI))],
      compute: () => post('sequence', { kind: st.kind, n: st.sn, m: st.sm }),
      render: (r) => h('div', { class: 'stack gap-s' }, h('div', { class: 'calc-tiles' }, tile(st.kind === 'factorial' ? 'n!' + (st.sm ? ' mod m' : '') : 'F(n)' + (st.sm ? ' mod m' : ''), r.value, { tone: 'accent', big: true, wide: !st.sm, sub: r.digits ? `${r.digits} cyfr` : null })),
        h('div', { class: 'muted small' }, icon('info', 13), ' ', r.method)),
      onResult: (r) => hist.add('modular', st.kind === 'factorial' ? 'Silnia' : 'Fibonacci', `${st.kind === 'factorial' ? st.sn + '!' : 'F(' + st.sn + ')'}${st.sm ? ' mod ' + st.sm : ''} = ${String(r.value).slice(0, 40)}`, { sn: st.sn, sm: st.sm, kind: st.kind }),
    });
    sections.modular = {
      restore(x) {
        for (const [k, el] of [['a', aI], ['b', bI], ['m', mI], ['n', nI], ['k', kI], ['p', pI], ['sn', snI], ['sm', smI]]) if (x[k] !== undefined) { st[k] = x[k]; el.value = x[k]; }
        if (x.kind) { st.kind = x.kind; kindSeg.setValue(x.kind); }
        persist(); ar.now(); nc.now(); sq.now();
      },
    };
    ar.now(); nc.now(); sq.now();
    return h('div', { class: 'calc-grid' }, ar.el, h('div', { class: 'stack' }, nc.el, sq.el));
  },

  // ------------------------------------------------------------------------------------- liczby
  numbers(sections, hist) {
    const st = { n: '999999999999999999', a: '240', b: '46', crt: [['2', '3'], ['3', '5'], ['2', '7']], ...(local.get('calc:numbers', {}) || {}) };
    const persist = () => local.set('calc:numbers', st);
    const nI = inp(st.n, (v) => { st.n = v; persist(); fz.run(); }, { ph: 'do ~10³⁰ (Pollard rho)' });
    let showAll = false;
    const fz = liveSection({
      title: 'Rozkład na czynniki i dzielniki', icon: 'blocks', subtitle: 'Pollard rho + deterministyczny Miller–Rabin. Liczba dzielników, ich suma, φ(n), sąsiednie liczby pierwsze.',
      controls: [h('div', { class: 'calc-row' }, field('n', nI)), chips([['10¹⁸−1', '10^18-1'], ['2⁶¹−1', '2^61-1'], ['wysoko złożona', '897612484786617600'], ['półpierwsza', '999999937*999999929'], ['Carmichael', '561']], (v) => { nI.value = v; st.n = v; persist(); fz.now(); })],
      compute: async () => {
        const [f, p] = await Promise.all([post('factorize', { n: st.n }), post('prime', { n: st.n }).catch(() => null)]);
        return { f, p };
      },
      render: ({ f, p }) => {
        const divs = f.divisors || [];
        const shown = showAll ? divs : divs.slice(0, 240);
        const divBox = h('div', { class: 'calc-divs' }, shown.map((d) => h('span', { class: 'chip mono' }, d)),
          divs.length > shown.length ? h('button', { class: 'chip', type: 'button', onclick: () => { showAll = true; fz.now(); } }, `+${divs.length - shown.length} więcej…`) : null);
        return h('div', { class: 'stack gap-s' },
          h('div', { class: 'calc-factbox' }, h('div', { class: 'grow' }, h('div', { class: 'ct-label' }, `${f.n} =`), pretty(f.pretty)),
            f.is_prime ? badge('liczba pierwsza', 'success', 'check') : badge('złożona', 'neutral'), copyButton(() => `${f.n} = ${f.pretty}`)),
          f.incomplete && f.incomplete.length ? callout(`Nie udało się rozłożyć w limicie czasu: ${f.incomplete.join(', ')} (w nawiasach kwadratowych).`, 'warn') : null,
          h('div', { class: 'calc-tiles' },
            tile('liczba dzielników d(n)', f.divisor_count), tile('suma dzielników σ(n)', f.divisor_sum), tile('φ(n) Eulera', f.phi),
            tile('test pierwszości', f.is_prime ? 'pierwsza' : 'złożona', { copy: false, tone: f.is_prime ? 'success' : null, sub: f.certainty }),
            p ? tile('następna pierwsza', p.next) : null, p ? tile('poprzednia pierwsza', p.prev) : null,
            tile('bezkwadratowa', f.squarefree === undefined ? null : f.squarefree ? 'tak' : 'nie', { copy: false }), tile('kwadrat liczby', f.perfect_square === undefined ? null : f.perfect_square ? 'tak' : 'nie', { copy: false })),
          divs.length ? h('div', { class: 'stack gap-s' }, h('div', { class: 'section-title row between' }, `Dzielniki (${f.divisor_count})`, copyButton(() => divs.join(' '))), divBox)
            : f.divisors_truncated ? h('div', { class: 'muted small' }, `Dzielników jest ${f.divisor_count} — za dużo, żeby je wypisać.`) : null);
      },
      onResult: ({ f }) => hist.add('numbers', 'Rozkład', `${f.n} = ${f.pretty}`, { n: st.n }),
    });

    const aI = inp(st.a, (v) => { st.a = v; persist(); gd.run(); });
    const bI = inp(st.b, (v) => { st.b = v; persist(); gd.run(); });
    const gd = liveSection({
      title: 'NWD, NWW i rozszerzony Euklides', icon: 'git-compare', subtitle: 'Współczynniki x, y z a·x + b·y = NWD(a, b) — potrzebne do odwrotności i równań diofantycznych.',
      controls: [h('div', { class: 'calc-row' }, field('a', aI), field('b', bI))],
      compute: () => post('gcd', { a: st.a, b: st.b }),
      render: (r) => h('div', { class: 'stack gap-s' },
        h('div', { class: 'calc-tiles' }, tile('NWD(a, b)', r.gcd, { tone: 'accent' }), tile('NWW(a, b)', r.lcm, { sub: r.lcm_overflow_ll ? 'nie mieści się w long long!' : null, tone: r.lcm_overflow_ll ? 'warn' : null }),
          tile('x', r.x), tile('y', r.y), tile('najmniejsze x > 0', r.x_min_pos)),
        h('div', { class: 'calc-check mono' }, icon('check', 14), r.check),
        r.general ? h('div', { class: 'muted small mono' }, 'Wszystkie rozwiązania: ', r.general) : null,
        r.steps && r.steps.length ? h('details', { class: 'calc-details' }, h('summary', null, `Kroki algorytmu (${r.steps.length - 2} dzieleń)`),
          table([{ key: 'r', label: 'r' }, { key: 'q', label: 'iloraz q' }, { key: 'x', label: 'x' }, { key: 'y', label: 'y' }], r.steps, { compact: true, maxHeight: '260px' }),
          h('div', { class: 'muted small mt-s' }, 'W każdym wierszu a·x + b·y = r. Ostatnie niezerowe r to NWD.')) : null),
      onResult: (r) => hist.add('numbers', 'NWD', `NWD(${st.a}, ${st.b}) = ${r.gcd}, x=${r.x}, y=${r.y}`, { a: st.a, b: st.b }),
    });

    const rowsBox = h('div', { class: 'calc-crt-rows' });
    const drawRows = () => {
      clear(rowsBox);
      st.crt.forEach((pair, i) => {
        const a = inp(pair[0], (v) => { pair[0] = v; persist(); cr.run(); }, { cls: 'sm' });
        const m = inp(pair[1], (v) => { pair[1] = v; persist(); cr.run(); }, { cls: 'sm' });
        rowsBox.append(h('div', { class: 'calc-crt-row' }, h('span', { class: 'mono muted' }, 'x ≡'), a, h('span', { class: 'mono muted' }, '(mod'), m, h('span', { class: 'mono muted' }, ')'),
          st.crt.length > 1 ? iconButton('x', { title: 'Usuń', onClick: () => { st.crt.splice(i, 1); persist(); drawRows(); cr.now(); } }) : null));
      });
    };
    drawRows();
    const cr = liveSection({
      title: 'Chińskie twierdzenie o resztach', icon: 'workflow', subtitle: 'Układ kongruencji — także z modułami, które nie są względnie pierwsze.',
      controls: [rowsBox, h('div', { class: 'row gap-s' }, button('Dodaj kongruencję', { icon: 'plus', size: 'sm', onClick: () => { st.crt.push(['0', '2']); persist(); drawRows(); cr.now(); } }))],
      compute: () => post('crt', { congruences: st.crt }),
      render: (r) => r.solvable
        ? h('div', { class: 'stack gap-s' }, h('div', { class: 'calc-tiles' }, tile('x', r.x, { tone: 'accent', big: true }), tile('modulo M = NWW', r.M)),
          h('div', { class: 'calc-check mono' }, icon('check', 14), r.general),
          h('div', { class: 'row gap-s' }, r.checks.map((c) => badge(`x mod ${c.m} = ${c.a}`, c.ok ? 'success' : 'danger', c.ok ? 'check' : 'x'))),
          r.coprime ? null : h('div', { class: 'muted small' }, 'Moduły nie są parami względnie pierwsze — użyto uogólnionego CRT (łączenie przez NWD).'))
        : errBox(r.message),
      onResult: (r) => hist.add('numbers', 'CRT', st.crt.map(([a, m]) => `x≡${a} (${m})`).join(', ') + ` → ${r.solvable ? r.x + ' mod ' + r.M : 'sprzeczne'}`, { crt: JSON.parse(JSON.stringify(st.crt)) }),
    });
    sections.numbers = {
      restore(x) {
        if (x.n !== undefined) { st.n = String(x.n); nI.value = st.n; }
        if (x.a !== undefined) { st.a = x.a; aI.value = x.a; }
        if (x.b !== undefined) { st.b = x.b; bI.value = x.b; }
        if (Array.isArray(x.crt)) { st.crt = x.crt; drawRows(); }
        persist(); fz.now(); gd.now(); cr.now();
      },
    };
    fz.now(); gd.now(); cr.now();
    return h('div', { class: 'calc-grid' }, fz.el, h('div', { class: 'stack' }, gd.el, cr.el));
  },

  // ------------------------------------------------------------------------------------- bity
  bits(sections, hist) {
    const st = { v: '-5', type: 'int32', d: '0.1', ...(local.get('calc:bits', {}) || {}) };
    const persist = () => local.set('calc:bits', st);
    const vI = inp(st.v, (v) => { st.v = v; persist(); bt.run(); }, { ph: 'np. 13, -1, 0xFF, 1<<31' });
    const typeSeg = segmented(['int32', 'uint32', 'int64', 'uint64'].map((x) => ({ value: x, label: x })), { value: st.type, onChange: (v) => { st.type = v; persist(); bt.now(); } });
    const W = { int32: 32, uint32: 32, int64: 64, uint64: 64 };
    function strip(r) {
      const w = r.width;
      const box = h('div', { class: 'calc-bits' });
      for (let byte = 0; byte < w / 8; byte++) {
        const grp = h('div', { class: 'cb-byte' });
        for (let k = 0; k < 8; k++) {
          const pos = w - 1 - (byte * 8 + k);
          const bit = r.bits[byte * 8 + k];
          grp.append(h('button', { class: ['cb-bit', bit === '1' ? 'on' : '', pos === w - 1 && r.signed ? 'sign' : ''].join(' '), type: 'button', title: `bit ${pos} (2^${pos})`,
            onclick: () => toggle(r, pos) }, bit));
        }
        grp.append(h('div', { class: 'cb-idx' }, h('span', null, String(w - 1 - byte * 8)), h('span', null, String(w - 8 - byte * 8))));
        box.append(grp);
      }
      return box;
    }
    function toggle(r, pos) {
      const w = BigInt(r.width);
      let u = BigInt(r.unsigned) ^ (1n << BigInt(pos));
      if (r.signed && u >> (w - 1n)) u -= 1n << w;
      st.v = u.toString(); vI.value = st.v; persist(); bt.now();
    }
    const bt = liveSection({
      title: 'Bity liczby całkowitej', icon: 'binary', subtitle: 'Kliknij bit, aby go przełączyć. U2 (kod uzupełnień do dwóch), maski, popcount, najwyższy/najniższy bit.',
      controls: [h('div', { class: 'calc-row' }, field('liczba (wyrażenie)', vI), field('typ', typeSeg))],
      compute: () => post('bits', { value: st.v, type: st.type }),
      render: (r) => h('div', { class: 'stack gap-s' },
        r.overflow ? callout(r.overflow_note, 'danger') : null,
        strip(r),
        h('div', { class: 'calc-tiles' },
          tile(`wartość (${r.cname})`, r.value, { tone: 'accent' }), tile('bez znaku', r.unsigned), tile('hex', r.hex), tile('ósemkowo', r.oct),
          tile('popcount', r.popcount), tile('najwyższy bit (__lg)', r.highest), tile('najniższy bit (ctz)', r.lowest), tile('zera wiodące (clz)', r.clz),
          tile('parzystość', r.parity), tile('potęga dwójki?', r.is_pow2 ? 'tak' : 'nie', { copy: false }), tile('zakres typu', `${r.min} … ${r.max}`, { wide: true })),
        r.u2 ? h('div', { class: 'calc-u2' }, h('div', { class: 'ct-label' }, 'Liczba ujemna w U2'), h('div', { class: 'mono small' }, `|x|   = ${r.u2.abs_bits}`), h('div', { class: 'mono small' }, `~|x|  = ${r.u2.not_bits}`),
          h('div', { class: 'mono small tone-accent' }, `+1    = ${r.u2.result_bits}`), h('div', { class: 'muted small mt-s' }, r.u2.text)) : null,
        h('div', { class: 'grid-2' },
          table([{ key: 'expr', label: 'Wyrażenie', render: (o) => h('code', null, o.expr) }, { key: 'value', label: 'Wynik', align: 'right', render: (o) => h('span', { class: 'mono' }, o.value) }, { key: 'desc', label: 'Znaczenie', render: (o) => h('span', { class: 'muted small' }, o.desc) }], r.ops, { compact: true }),
          r.builtins.length ? table([{ key: 'expr', label: 'Wbudowane GCC', render: (o) => h('code', null, o.expr) }, { key: 'value', label: 'Wynik', align: 'right', render: (o) => h('span', { class: 'mono' }, o.value) }], r.builtins, { compact: true }) : h('div'))),
      onResult: (r) => hist.add('bits', 'Bity', `${st.v} (${st.type}) = ${r.hex}, popcount ${r.popcount}`, { v: st.v, type: st.type }),
    });

    const dI = inp(st.d, (v) => { st.d = v; persist(); db.run(); }, { ph: 'np. 0.1, 1e-9, 0.1+0.2, 2^53+1' });
    function dstrip(r) {
      const box = h('div', { class: 'calc-dbl' });
      const seg = (cls, label, bits, from) => h('div', { class: 'cd-seg ' + cls }, h('div', { class: 'cd-label' }, label),
        h('div', { class: 'cd-bits' }, bits.split('').map((b, i) => h('button', { class: ['cd-bit', b === '1' ? 'on' : ''].join(' '), type: 'button', title: `bit ${63 - (from + i)}`, onclick: () => flip(r, from + i) }, b))));
      box.append(seg('s', 'znak', r.bits.slice(0, 1), 0), seg('e', `wykładnik (11 b) = ${r.exponent_raw}`, r.bits.slice(1, 12), 1), seg('m', 'mantysa (52 b)', r.bits.slice(12), 12));
      return box;
    }
    function flip(r, i) {
      const bits = r.bits.split('');
      bits[i] = bits[i] === '1' ? '0' : '1';
      const dv = new DataView(new ArrayBuffer(8));
      dv.setBigUint64(0, BigInt('0b' + bits.join('')));
      const x = dv.getFloat64(0);
      st.d = Object.is(x, -0) ? '-0.0' : Number.isNaN(x) ? 'nan' : String(x);
      dI.value = st.d; persist(); db.now();
    }
    const db = liveSection({
      title: 'Reprezentacja double (IEEE 754)', icon: 'microscope', subtitle: 'Znak, wykładnik i mantysa; dokładna wartość przechowywana w pamięci — zobacz, dlaczego 0.1 + 0.2 ≠ 0.3.',
      controls: [h('div', { class: 'calc-row' }, field('liczba lub wyrażenie', dI)), chips([['0.1', '0.1'], ['0.1+0.2', '0.1+0.2'], ['1e-9', '1e-9'], ['2^53+1', '2**53+1'], ['1/3', '1/3'], ['-0.0', '-0.0'], ['∞', 'inf']], (v) => { dI.value = v; st.d = v; persist(); db.now(); })],
      compute: () => post('double', { value: st.d }),
      render: (r) => h('div', { class: 'stack gap-s' },
        dstrip(r),
        r.formula ? h('div', { class: 'calc-check mono' }, r.formula) : null,
        h('div', { class: 'calc-tiles' },
          tile('najkrótszy zapis (repr)', r.value, { tone: 'accent' }), tile('rodzaj', r.kind, { copy: false }), tile('wykładnik (bez biasu)', r.exponent), tile('mantysa (1.m)', r.mantissa_value),
          tile('hex (bity)', r.hex), tile('hex float', r.float_hex), tile('ulp (odstęp do sąsiada)', r.ulp), tile('dokładnie reprezentowalna?', r.exactly_representable === null || r.exactly_representable === undefined ? null : r.exactly_representable ? 'tak' : 'nie', { copy: false, tone: r.exactly_representable === false ? 'warn' : null, sub: r.error && r.error !== '0' ? `błąd zapisu: ${r.error}` : null }),
          tile('następna w górę', r.next_up), tile('następna w dół', r.next_down),
          tile('jako float (32 b)', r.float32.value, { sub: r.float32.rel_error ? `błąd względny ${r.float32.rel_error}` : null })),
        r.exact ? h('div', { class: 'calc-exact' }, h('div', { class: 'ct-head' }, h('span', { class: 'ct-label' }, 'Dokładna wartość w pamięci'), copyButton(() => r.exact)), h('div', { class: 'mono' }, r.exact)) : null,
        r.int_note ? callout(r.int_note, 'warn') : null),
      onResult: (r) => hist.add('bits', 'Double', `${st.d} → ${r.value} (${r.hex})`, { d: st.d }),
    });
    sections.bits = {
      restore(x) {
        if (x.v !== undefined) { st.v = String(x.v); vI.value = st.v; }
        if (x.type && W[x.type]) { st.type = x.type; typeSeg.setValue(x.type); }
        if (x.d !== undefined) { st.d = String(x.d); dI.value = st.d; }
        persist(); bt.now(); db.now();
      },
    };
    bt.now(); db.now();
    return h('div', { class: 'stack' }, bt.el, db.el);
  },

  // ------------------------------------------------------------------------------------- systemy
  bases(sections, hist) {
    const st = { v: '255', from: '10', to: '2', ...(local.get('calc:bases', {}) || {}) };
    const persist = () => local.set('calc:bases', st);
    const opts = (auto) => [...(auto ? [{ value: '0', label: 'auto (0x, 0b, 0o)' }] : []), ...Array.from({ length: 35 }, (_, i) => ({ value: String(i + 2), label: `${i + 2}${{ 2: ' (dwójkowy)', 8: ' (ósemkowy)', 10: ' (dziesiętny)', 16: ' (szesnastkowy)' }[i + 2] || ''}` }))];
    const vI = inp(st.v, (v) => { st.v = v; persist(); bs.run(); }, { ph: 'np. ff, 1011, -42, 0x1F' });
    const fS = select(opts(true), { value: st.from, onChange: (v) => { st.from = v; persist(); bs.now(); } });
    const tS = select(opts(false), { value: st.to, onChange: (v) => { st.to = v; persist(); bs.now(); } });
    const swap = iconButton('refresh-cw', { title: 'Zamień kierunek', onClick: () => { if (st.from === '0') return; const r = bs._last; [st.from, st.to] = [st.to, st.from]; fS.value = st.from; tS.value = st.to; if (r) { st.v = r.result; vI.value = st.v; } persist(); bs.now(); } });
    const bs = liveSection({
      title: 'Zamiana systemów liczbowych (2–36)', icon: 'languages', subtitle: 'Dowolnie duże liczby, także ujemne. Kroki: dzielenie przez podstawę i schemat potęg.',
      controls: [h('div', { class: 'calc-row' }, field('liczba', vI), field('z systemu', fS), h('div', { class: 'calc-swap' }, swap), field('na system', tS)),
        chips([['→ 2', '2'], ['→ 8', '8'], ['→ 10', '10'], ['→ 16', '16'], ['→ 36', '36']], (v) => { st.to = v; tS.value = v; persist(); bs.now(); })],
      compute: () => post('base', { value: st.v, from: st.from, to: st.to }),
      render: (r) => {
        bs._last = r;
        return h('div', { class: 'stack gap-s' },
          h('div', { class: 'calc-tiles' }, tile(`wynik (podstawa ${r.to})`, r.result, { tone: 'accent', big: true, wide: true, sub: r.digits ? `${r.digits} ${plural(r.digits, 'cyfra', 'cyfry', 'cyfr')}, ${r.bits} bitów` : null })),
          h('div', { class: 'calc-tiles' }, r.common.map((c) => tile(`${c.name} (${c.base})`, c.text))),
          r.div_steps && r.div_steps.length ? h('details', { class: 'calc-details', open: r.div_steps.length <= 12 }, h('summary', null, `Dzielenie przez ${r.to} (reszty czytamy od dołu)`),
            table([{ key: 'n', label: 'liczba' }, { key: 'q', label: `÷ ${r.to}` }, { key: 'r', label: 'reszta', render: (x) => h('b', { class: 'tone-accent mono' }, x.r) }], r.div_steps, { compact: true, maxHeight: '280px' })) : null,
          r.horner ? h('details', { class: 'calc-details' }, h('summary', null, `Rozwinięcie w systemie ${r.from}`),
            h('div', { class: 'mono small calc-horner' }, r.horner.map((x) => `${x.digit}·${r.from}^${x.power}`).join(' + '), ' = ', r.value)) : null);
      },
      onResult: (r) => hist.add('bases', 'Systemy', `${st.v} (${st.from === '0' ? 'auto' : st.from}) → ${r.result} (${st.to})`, { v: st.v, from: st.from, to: st.to }),
    });
    sections.bases = {
      restore(x) {
        if (x.v !== undefined) { st.v = String(x.v); vI.value = st.v; }
        if (x.from !== undefined) { st.from = String(x.from); fS.value = st.from; }
        if (x.to !== undefined) { st.to = String(x.to); tS.value = st.to; }
        persist(); bs.now();
      },
    };
    bs.now();
    return h('div', { class: 'stack' }, bs.el);
  },

  // ------------------------------------------------------------------------------------- limity
  limits(sections, hist, S) {
    const st = { n: '2·10^5', q: '10^5', m: '', tl: String(S && S.time_limit ? S.time_limit : 1), speed: '2e8', ...(local.get('calc:limits', {}) || {}) };
    const persist = () => local.set('calc:limits', st);
    const nI = inp(st.n, (v) => { st.n = v; persist(); lm.run(); }, { ph: 'np. 2·10^5 albo n ≤ 10^6' });
    const qI = inp(st.q, (v) => { st.q = v; persist(); lm.run(); }, { ph: 'opcjonalnie' });
    const mI = inp(st.m, (v) => { st.m = v; persist(); lm.run(); }, { ph: 'krawędzie (opcj.)' });
    const tI = inp(st.tl, (v) => { st.tl = v; persist(); lm.run(); }, { ph: '1' });
    const sp = segmented([{ value: '1e8', label: '10⁸/s', title: 'zachowawczo (stare sprawdzarki, ciężkie operacje)' }, { value: '2e8', label: '2·10⁸/s', title: 'typowo' },
      { value: '5e8', label: '5·10⁸/s', title: 'proste pętle' }, { value: '1e9', label: '10⁹/s', title: 'optymistycznie (bitsety, proste dodawania)' }], { value: st.speed, onChange: (v) => { st.speed = v; persist(); lm.now(); } });
    const V = { ok: ['success', 'zmieści się', 'circle-check'], risky: ['warn', 'ryzykowne', 'triangle-alert'], tle: ['danger', 'TLE', 'timer'] };
    const fmtSec = (s) => (s === null || s === undefined ? '≫ wiek wszechświata' : s < 1e-3 ? '< 1 ms' : s < 1 ? `${Math.round(s * 1000)} ms` : s < 120 ? `${s.toFixed(s < 10 ? 2 : 0)} s` : s < 7200 ? `${Math.round(s / 60)} min` : s < 86400 * 365 ? `${Math.round(s / 3600)} h` : `${(s / (86400 * 365)).toExponential(1)} lat`);
    function bar(row, logBudget) {
      const max = logBudget + 2;
      const f = Math.max(0.02, Math.min(1, row.log_ops / max));
      return h('div', { class: 'calc-lbar' }, h('div', { class: `fill v-${row.verdict}`, style: { width: (f * 100).toFixed(1) + '%' } }), h('div', { class: 'mark', style: { left: ((logBudget / max) * 100).toFixed(1) + '%' }, title: 'budżet operacji' }));
    }
    function rowsTable(rows, r, withMax) {
      return table([
        { key: 'name', label: 'Złożoność', render: (x) => h('span', { class: ['mono bold', x.name === r.best ? 'tone-accent' : ''].join(' ') }, x.name) },
        { key: 'ops', label: 'Operacji', align: 'right', render: (x) => h('span', { class: 'mono' }, x.ops) },
        { key: 'seconds', label: 'Czas ≈', align: 'right', render: (x) => h('span', { class: 'mono small' }, fmtSec(x.seconds)) },
        { key: 'bar', label: '', render: (x) => bar(x, r.log_budget), width: '18%' },
        { key: 'verdict', label: 'Werdykt', render: (x) => badge(V[x.verdict][1], V[x.verdict][0], V[x.verdict][2]) },
        ...(withMax ? [{ key: 'max_n', label: 'Maks. n', align: 'right', render: (x) => h('span', { class: 'mono small' }, x.max_n === null ? '∞' : x.max_n) }] : []),
        { key: 'hint', label: 'Pasujące algorytmy', render: (x) => h('span', { class: 'small muted' }, x.hint) },
      ], rows, { compact: true });
    }
    const lm = liveSection({
      title: 'Kalkulator złożoności dla ograniczeń', icon: 'gauge', subtitle: 'Wpisz ograniczenia z treści zadania — zobaczysz, które złożoności zmieszczą się w limicie czasu.',
      controls: [h('div', { class: 'calc-row' }, field('n ≤', nI), field('q ≤ (zapytania)', qI), field('m ≤ (krawędzie)', mI), field('limit czasu [s]', tI)),
        h('div', { class: 'row gap-s' }, h('span', { class: 'muted small' }, 'szybkość sprawdzarki:'), sp),
        chips([['n ≤ 20', '20'], ['n ≤ 500', '500'], ['n ≤ 5000', '5000'], ['n ≤ 10⁵', '10^5'], ['n ≤ 2·10⁵', '2·10^5'], ['n ≤ 10⁶', '10^6'], ['n ≤ 10⁹', '10^9'], ['n ≤ 10¹⁸', '10^18']], (v) => { nI.value = v; st.n = v; persist(); lm.now(); })],
      compute: () => post('limits', { n: st.n, q: st.q, m: st.m, time_limit: st.tl, ops_per_sec: st.speed }),
      render: (r) => h('div', { class: 'stack gap-s' },
        callout(`**${r.recommendation}** Budżet: ~${r.budget} prostych operacji.`, r.best ? 'tip' : 'warn'),
        rowsTable(r.rows, r, true),
        r.extra && r.extra.length ? h('div', { class: 'stack gap-s' }, h('div', { class: 'section-title' }, 'Z zapytaniami i krawędziami'), rowsTable(r.extra, r, false)) : null,
        h('div', { class: 'muted small' }, 'Szacunek: stała ukryta w O(·) bywa 1–20× (set/map, dzielenie, modulo i pamięć podręczna spowalniają). „Ryzykowne” = warto zoptymalizować stałą albo szukać lepszego algorytmu.')),
      onResult: (r) => hist.add('limits', 'Limity', `n=${st.n}${st.q ? ', q=' + st.q : ''}, ${st.tl}s → ${r.best || 'nic'}`, { n: st.n, q: st.q, m: st.m, tl: st.tl, speed: st.speed }),
    });
    const cheat = h('div', { class: 'card calc-card' }, h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('lightbulb', 17)), h('div', { class: 'card-title' }, 'Ściąga: ograniczenie → oczekiwana złożoność')),
      h('div', { class: 'card-body' }, table([{ key: 'n', label: 'n ≤' }, { key: 'c', label: 'Złożoność' }, { key: 'e', label: 'Przykłady' }], [
        { n: '10–11', c: 'O(n!)', e: 'permutacje' }, { n: '20–25', c: 'O(2ⁿ·n)', e: 'maski bitowe, meet in the middle (40)' }, { n: '100–500', c: 'O(n³)', e: 'Floyd–Warshall, DP przedziałowe' },
        { n: '2 000–5 000', c: 'O(n²)', e: 'DP kwadratowe, LCS' }, { n: '10⁵–10⁶', c: 'O(n log n)', e: 'sortowanie, drzewa przedziałowe, Dijkstra' }, { n: '10⁷–10⁸', c: 'O(n)', e: 'sito, dwa wskaźniki, prefiksy' },
        { n: '10⁹–10¹⁸', c: 'O(√n), O(log n), O(1)', e: 'dzielniki, binsearch, szybkie potęgowanie, wzór' },
      ].map((x) => ({ n: h('span', { class: 'mono' }, x.n), c: h('span', { class: 'mono bold' }, x.c), e: h('span', { class: 'small muted' }, x.e) })), { compact: true })));
    sections.limits = {
      restore(x) {
        for (const [k, el] of [['n', nI], ['q', qI], ['m', mI], ['tl', tI]]) if (x[k] !== undefined) { st[k] = String(x[k]); el.value = st[k]; }
        if (x.speed) { st.speed = x.speed; sp.setValue(x.speed); }
        persist(); lm.now();
      },
    };
    lm.now();
    return h('div', { class: 'stack' }, lm.el, cheat);
  },

  // ------------------------------------------------------------------------------------- pamięć
  memory(sections, hist, S) {
    const DEF = '// wklej deklaracje albo cały program\nconst int N = 2e5 + 5;\nint a[1000000];\nlong long dp[5005][5005];\nvector<int> g[N];\nbitset<100000> b;\n\nint main() {\n    int n; cin >> n;\n    vector<long long> d(n + 1);\n    int lokalna[300000];\n}';
    const st = { decls: DEF, limit: String(S && S.memory_limit ? S.memory_limit : 256), vars: 'n = 2e5', ...(local.get('calc:memory', {}) || {}) };
    const persist = () => local.set('calc:memory', st);
    const ta = textarea({ value: st.decls, rows: 12, onInput: (v) => { st.decls = v; persist(); mm.run(); } });
    ta.classList.add('calc-code');
    const lI = inp(st.limit, (v) => { st.limit = v; persist(); mm.run(); }, { ph: '256' });
    const vI = inp(st.vars, (v) => { st.vars = v; persist(); mm.run(); }, { ph: 'n = 2e5, m = 1e5' });
    const verdict = { ok: ['success', 'mieści się'], risky: ['warn', 'na granicy'], mle: ['danger', 'MLE — za dużo'] };
    const mm = liveSection({
      title: 'Kalkulator pamięci', icon: 'database', subtitle: 'Typy, wymiary z wyrażeniami (2e5+5, 1<<20, N), struktury, vector(n), tablice lokalne na stosie.', delay: 350,
      controls: [h('div', { class: 'calc-memgrid' }, field('Deklaracje C++', ta),
        h('div', { class: 'stack gap-s' }, field('limit pamięci [MB]', lI), chips([['64', '64'], ['128', '128'], ['256', '256'], ['512', '512'], ['1024', '1024']], (v) => { lI.value = v; st.limit = v; persist(); mm.now(); }),
          field('Zmienne (dla vector(n), VLA)', vI, 'np. n = 2e5, m = 1e5'),
          h('div', { class: 'muted small' }, 'Rozmiary jak na x86-64 Linux (sprawdzarka OI): int 4 B, long long 8 B, long double 16 B, wskaźnik 8 B, vector 24 B, string 32 B.')))],
      compute: () => post('memory', { decls: st.decls, limit_mb: st.limit, vars: st.vars }),
      render: (r) => {
        const pct = Math.min(100, r.percent);
        const [tone, label] = verdict[r.verdict];
        const shareBar = (x) => h('div', { class: 'calc-share' }, h('div', { class: 'fill', style: { width: Math.min(100, x.share * 100).toFixed(1) + '%' } }));
        return h('div', { class: 'stack gap-s' },
          h('div', { class: `calc-gauge v-${r.verdict}` },
            h('div', { class: 'row between' }, h('div', { class: 'calc-gauge-val' }, r.total_human, h('span', { class: 'muted' }, ` / ${r.limit_mb} MB`)), badge(`${r.percent}% — ${label}`, tone)),
            h('div', { class: 'calc-gauge-track' }, h('div', { class: 'calc-gauge-fill', style: { width: pct + '%' } }), h('div', { class: 'calc-gauge-80' }))),
          h('div', { class: 'calc-tiles' }, tile('globalne / static', r.static_human, { copy: false }), tile('stos (lokalne)', r.stack_human, { copy: false, tone: r.stack > 8 * 1024 * 1024 ? 'danger' : null }),
            tile('sterta (vector)', r.heap_human, { copy: false }), tile('razem', `${r.total_mb} MB`, { tone: 'accent' })),
          ...r.notes.map((n) => callout((n.line ? `**Linia ${n.line}:** ` : '') + n.text, n.tone === 'danger' ? 'danger' : n.tone === 'warn' ? 'warn' : 'info')),
          r.items.length ? table([
            { key: 'line', label: 'Linia', align: 'right', render: (x) => h('span', { class: 'mono muted' }, x.line) },
            { key: 'name', label: 'Nazwa', render: (x) => h('span', { class: 'mono bold' }, x.name) },
            { key: 'type', label: 'Typ', render: (x) => h('span', { class: 'mono small' }, x.type + x.dims.map((d) => `[${d}]`).join('')) },
            { key: 'count', label: 'Elementów', align: 'right', render: (x) => h('span', { class: 'mono small' }, x.count === null ? '?' : x.count.toLocaleString('pl-PL')) },
            { key: 'human', label: 'Rozmiar', align: 'right', render: (x) => h('span', { class: ['mono', x.unknown ? 'muted' : 'bold'].join(' ') }, x.unknown ? '?' : x.human) },
            { key: 'share', label: 'Udział', render: shareBar, width: '12%' },
            { key: 'storage', label: 'Gdzie', render: (x) => badge(x.storage === 'stack' ? 'stos' : 'globalnie', x.storage === 'stack' ? 'warn' : 'neutral') },
            { key: 'notes', label: 'Uwagi', render: (x) => h('div', { class: 'calc-notes' }, x.warnings.map((w) => h('div', { class: 'w' }, icon('triangle-alert', 12), w)), x.heap ? h('div', { class: 'n' }, `+ ${fmtB(x.heap)} na stercie`) : null, x.notes.map((n) => h('div', { class: 'n' }, n))) },
          ], r.items, { compact: true }) : h('div', { class: 'muted' }, 'Nie znalazłem deklaracji tablic ani kontenerów.'),
          r.consts.length ? h('div', { class: 'row gap-s' }, h('span', { class: 'muted small' }, 'Stałe:'), r.consts.map((c) => h('span', { class: 'chip mono', title: `linia ${c.line}: ${c.expr}` }, `${c.name} = ${Number(c.value).toLocaleString('pl-PL')}`))) : null);
      },
      onResult: (r) => hist.add('memory', 'Pamięć', `${r.items.length} deklaracji → ${r.total_human} / ${r.limit_mb} MB`, { decls: st.decls, limit: st.limit, vars: st.vars }),
    });
    sections.memory = {
      restore(x) {
        if (x.decls !== undefined) { st.decls = String(x.decls); ta.value = st.decls; }
        if (x.limit !== undefined || x.limit_mb !== undefined) { st.limit = String(x.limit ?? x.limit_mb); lI.value = st.limit; }
        if (x.vars !== undefined) { st.vars = String(x.vars); vI.value = st.vars; }
        persist(); mm.now();
      },
    };
    mm.now();
    return h('div', { class: 'stack' }, mm.el);
  },
};

function fmtB(b) { return b < 1024 ? `${b} B` : b < 1024 ** 2 ? `${(b / 1024).toFixed(1)} KB` : b < 1024 ** 3 ? `${(b / 1024 ** 2).toFixed(2)} MB` : `${(b / 1024 ** 3).toFixed(2)} GB`; }

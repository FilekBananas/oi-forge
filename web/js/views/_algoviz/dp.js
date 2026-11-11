// Programowanie dynamiczne: plecak 0/1, LCS (tabela 2D z zależnościami) i LIS (O(n²) z odtwarzaniem).
import { cpp, Rec, exec, parseInts, parseInt1, InputError } from './core.js';

const CAT = 'Programowanie dynamiczne';

// ------------------------------------------------------------------------------------------- plecak 0/1
const KNAP = cpp(`
// dp[i][c] = najlepsza wartość z pierwszych i przedmiotów przy pojemności c
for (int c = 0; c <= W; c++) dp[0][c] = 0;                  @@zero
for (int i = 1; i <= n; i++)                                @@loopI
    for (int c = 0; c <= W; c++) {
        dp[i][c] = dp[i - 1][c];             // nie bierzemy i  @@skip
        if (c >= w[i])                                      @@fits
            dp[i][c] = max(dp[i][c], dp[i - 1][c - w[i]] + v[i]);  @@take
    }
// odpowiedź: dp[n][W]                                      @@ans
`);

function parseItems(text) {
  const items = String(text ?? '').split('\n').map((l) => l.trim()).filter(Boolean).map((ln) => {
    const p = ln.split(/[\s,;]+/);
    if (p.length !== 2) throw new InputError(`Przedmiot „${ln}”: podaj „waga wartość”.`);
    return { w: parseInt1(p[0], { name: 'waga', min: 1, max: 15 }), v: parseInt1(p[1], { name: 'wartość', min: 0, max: 99 }) };
  });
  if (!items.length) throw new InputError('Podaj przedmioty: po jednym „waga wartość” w linii.');
  if (items.length > 7) throw new InputError('Maksymalnie 7 przedmiotów (tabela musi się zmieścić).');
  return items;
}

function knapsack(vals) {
  const items = parseItems(vals.items);
  const W = parseInt1(vals.W, { name: 'Pojemność W', min: 1, max: 16 });
  const n = items.length;
  const g = Array.from({ length: n + 1 }, () => Array(W + 1).fill(null));
  const marks = [];
  const r = new Rec(KNAP);
  const S = ({ cur = null, deps = [], vars = {} } = {}) => ({ g, cur, deps, marks: marks.slice(), vars });
  return exec(r, () => {
    for (let c = 0; c <= W; c++) g[0][c] = 0;
    r.add('zero', `Plecak o pojemności ${W}, ${n} przedmiotów. Wiersz 0 (brak przedmiotów) to same zera.`, S());
    for (let i = 1; i <= n; i++) {
      const { w, v } = items[i - 1];
      r.add('loopI', `Przedmiot ${i}: waga ${w}, wartość ${v}. Dla każdej pojemności c wybieramy: nie brać (dp[${i - 1}][c]) albo wziąć (dp[${i - 1}][c − ${w}] + ${v}).`, S({ vars: { i, w, v } }));
      for (let c = 0; c <= W; c++) {
        const skip = g[i - 1][c];
        if (c >= w) {
          const take = g[i - 1][c - w] + v;
          g[i][c] = Math.max(skip, take);
          r.add('take', `dp[${i}][${c}] = max(dp[${i - 1}][${c}] = ${skip}, dp[${i - 1}][${c - w}] + ${v} = ${take}) = ${g[i][c]}${take > skip ? ' → opłaca się wziąć przedmiot ' + i : ' → lepiej nie brać'}.`,
            S({ cur: [i, c], deps: [[i - 1, c, take > skip ? 'dep' : 'win'], [i - 1, c - w, take > skip ? 'win' : 'dep']], vars: { i, c, w, v } }));
        } else {
          g[i][c] = skip;
          r.add('skip', `c = ${c} < waga ${w}: przedmiot się nie mieści, dp[${i}][${c}] = dp[${i - 1}][${c}] = ${skip}.`, S({ cur: [i, c], deps: [[i - 1, c, 'win']], vars: { i, c, w, v } }));
        }
      }
    }
    let c = W;
    const taken = [];
    for (let i = n; i >= 1; i--) {
      marks.push([i, c, 'path']);
      if (g[i][c] !== g[i - 1][c]) { taken.push(i); r.add('ans', `dp[${i}][${c}] = ${g[i][c]} ≠ dp[${i - 1}][${c}] = ${g[i - 1][c]} → przedmiot ${i} został wzięty, c −= ${items[i - 1].w}.`, S({ cur: [i, c] })); c -= items[i - 1].w; }
      else r.add('ans', `dp[${i}][${c}] = dp[${i - 1}][${c}] → przedmiotu ${i} nie bierzemy.`, S({ cur: [i, c] }));
    }
    marks.push([0, c, 'path']);
    taken.reverse();
    const tw = taken.reduce((s, i) => s + items[i - 1].w, 0);
    r.add('ans', `Odpowiedź dp[${n}][${W}] = ${g[n][W]}. Bierzemy przedmioty ${taken.join(', ') || '— żadne'} (łączna waga ${tw}). Złożoność O(n·W).`, S({ vars: { wynik: g[n][W] } }));
  }, { rows: ['∅', ...items.map((it, i) => `${i + 1}: w=${it.w} v=${it.v}`)], cols: Array.from({ length: W + 1 }, (_, c) => String(c)), corner: 'i \\ c' });
}

// ------------------------------------------------------------------------------------------- LCS
const LCS = cpp(`
// dp[i][j] = LCS prefiksów a[0..i) i b[0..j)
for (int i = 1; i <= n; i++)
    for (int j = 1; j <= m; j++) {                       @@loop
        if (a[i - 1] == b[j - 1])                        @@eq
            dp[i][j] = dp[i - 1][j - 1] + 1;             @@diag
        else
            dp[i][j] = max(dp[i - 1][j], dp[i][j - 1]);  @@max
    }
// odpowiedź: dp[n][m]                                   @@ans
`);

function lcs(vals) {
  const a = String(vals.a ?? '').replace(/\s+/g, ''), b = String(vals.b ?? '').replace(/\s+/g, '');
  if (!a.length || !b.length) throw new InputError('Podaj oba napisy.');
  if (a.length > 10 || b.length > 12) throw new InputError('Maksymalnie 10 znaków w pierwszym i 12 w drugim napisie.');
  const n = a.length, m = b.length;
  const g = Array.from({ length: n + 1 }, (_, i) => Array.from({ length: m + 1 }, (_, j) => (i === 0 || j === 0 ? 0 : null)));
  const marks = [];
  const r = new Rec(LCS);
  const S = ({ cur = null, deps = [], vars = {} } = {}) => ({ g, cur, deps, marks: marks.slice(), vars });
  return exec(r, () => {
    r.add('loop', `Najdłuższy wspólny podciąg napisów „${a}” i „${b}”. Pierwszy wiersz i kolumna to zera (pusty prefiks).`, S());
    for (let i = 1; i <= n; i++) {
      for (let j = 1; j <= m; j++) {
        if (a[i - 1] === b[j - 1]) {
          g[i][j] = g[i - 1][j - 1] + 1;
          r.add('diag', `a[${i - 1}] = b[${j - 1}] = '${a[i - 1]}' → dp[${i}][${j}] = dp[${i - 1}][${j - 1}] + 1 = ${g[i][j]}.`, S({ cur: [i, j], deps: [[i - 1, j - 1, 'dep']], vars: { i, j } }));
        } else {
          g[i][j] = Math.max(g[i - 1][j], g[i][j - 1]);
          const up = g[i - 1][j] >= g[i][j - 1];
          r.add('max', `'${a[i - 1]}' ≠ '${b[j - 1]}' → dp[${i}][${j}] = max(dp[${i - 1}][${j}] = ${g[i - 1][j]}, dp[${i}][${j - 1}] = ${g[i][j - 1]}) = ${g[i][j]}.`,
            S({ cur: [i, j], deps: [[i - 1, j, up ? 'dep' : 'win'], [i, j - 1, up ? 'win' : 'dep']], vars: { i, j } }));
        }
      }
    }
    let i = n, j = m;
    const out = [];
    while (i > 0 && j > 0) {
      marks.push([i, j, 'path']);
      if (a[i - 1] === b[j - 1]) { out.push(a[i - 1]); r.add('ans', `'${a[i - 1]}' pasuje — należy do LCS, idę po przekątnej.`, S({ cur: [i, j] })); i--; j--; }
      else if (g[i - 1][j] >= g[i][j - 1]) { r.add('ans', `Wartość przyszła z góry — idę w górę.`, S({ cur: [i, j] })); i--; }
      else { r.add('ans', `Wartość przyszła z lewej — idę w lewo.`, S({ cur: [i, j] })); j--; }
    }
    marks.push([i, j, 'path']);
    r.add('ans', `LCS ma długość ${g[n][m]}: „${out.reverse().join('')}”. Złożoność O(n·m).`, S({ vars: { wynik: g[n][m] } }));
  }, { rows: ['∅', ...a.split('')], cols: ['∅', ...b.split('')], corner: 'a \\ b' });
}

// ------------------------------------------------------------------------------------------- LIS
const LIS = cpp(`
// dp[i] = długość najdłuższego rosnącego podciągu kończącego się na a[i]
for (int i = 0; i < n; i++) {
    dp[i] = 1; prv[i] = -1;                         @@init
    for (int j = 0; j < i; j++)                     @@loopJ
        if (a[j] < a[i] && dp[j] + 1 > dp[i]) {     @@cmp
            dp[i] = dp[j] + 1;                      @@upd
            prv[i] = j;
        }
}
// wynik: max dp[i]; podciąg odtwarzamy po prv[]   @@ans
`);

function lis(vals) {
  const a = parseInts(vals.a, { name: 'Tablica', min: -99, max: 99, minLen: 2, maxLen: 12 });
  const n = a.length;
  const dp = [], prv = [];
  const r = new Rec(LIS);
  let chosen = new Set();
  const S = ({ ta = {}, td = {}, links = [], ptrs = [], vars = {} } = {}) => ({
    rows: [
      { key: 'a', label: 'a', kind: 'cells', idx: true, slots: n, items: a.map((v, i) => ({ id: 'a' + i, v, s: i, t: ta[i] || (chosen.has(i) ? 'path' : '') })), ptrs },
      { key: 'dp', label: 'dp', kind: 'cells', slots: n, gap: true, items: dp.map((v, i) => ({ id: 'd' + i, v, s: i, t: td[i] || (chosen.has(i) ? 'path' : '') })) },
      { key: 'prv', label: 'prv', kind: 'cells', slots: n, items: prv.map((v, i) => ({ id: 'p' + i, v, s: i, t: chosen.has(i) ? 'path' : 'dim' })) },
    ],
    links, vars,
  });
  return exec(r, () => {
    r.add('init', `Najdłuższy podciąg ściśle rosnący. Dla każdego i sprawdzamy wszystkie wcześniejsze j: jeśli a[j] < a[i], to podciąg kończący się na j można przedłużyć o a[i].`, S());
    for (let i = 0; i < n; i++) {
      dp[i] = 1; prv[i] = -1;
      r.add('init', `i = ${i}, a[i] = ${a[i]}: na start dp[${i}] = 1 (sam element).`, S({ ta: { [i]: 'key' }, td: { [i]: 'new' }, ptrs: [{ name: 'i', s: i }], vars: { i } }));
      for (let j = 0; j < i; j++) {
        const ok = a[j] < a[i] && dp[j] + 1 > dp[i];
        r.add('cmp', a[j] < a[i]
          ? `a[${j}] = ${a[j]} < ${a[i]}: dp[${j}] + 1 = ${dp[j] + 1} ${ok ? `> dp[${i}] = ${dp[i]} → poprawiam!` : `≤ dp[${i}] = ${dp[i]} — nic nowego.`}`
          : `a[${j}] = ${a[j]} ≥ ${a[i]} — nie da się przedłużyć.`,
        S({ ta: { [i]: 'key', [j]: a[j] < a[i] ? 'cmp' : 'miss' }, td: { [j]: 'cmp' }, links: a[j] < a[i] ? [{ from: ['a', j], to: ['a', i], t: ok ? 'hit' : 'cmp' }] : [], ptrs: [{ name: 'j', s: j }, { name: 'i', s: i }], vars: { i, j } }));
        if (ok) {
          dp[i] = dp[j] + 1; prv[i] = j;
          r.add('upd', `dp[${i}] = ${dp[i]}, prv[${i}] = ${j}.`, S({ ta: { [i]: 'key', [j]: 'hit' }, td: { [i]: 'new' }, links: [{ from: ['a', j], to: ['a', i], t: 'hit' }], ptrs: [{ name: 'j', s: j }, { name: 'i', s: i }], vars: { i, j } }));
        }
      }
    }
    let best = 0;
    for (let i = 1; i < n; i++) if (dp[i] > dp[best]) best = i;
    const seq = [];
    for (let k = best; k !== -1; k = prv[k]) seq.push(k);
    seq.reverse();
    chosen = new Set(seq);
    const links = seq.slice(1).map((k, x) => ({ from: ['a', seq[x]], to: ['a', k], t: 'path' }));
    r.add('ans', `Najdłuższy rosnący podciąg ma długość ${dp[best]}: ${seq.map((k) => a[k]).join(' < ')} (odtworzony po prv[] od i = ${best}). Złożoność O(n²); da się O(n log n) z wyszukiwaniem binarnym.`, S({ links, vars: { wynik: dp[best] } }));
  }, {});
}

export const DP = [
  { id: 'knapsack', name: 'Plecak 0/1', cat: CAT, kb: 'dp-knapsack-01', complexity: 'O(n·W)', view: 'table', code: KNAP,
    about: 'Wypełnianie tabeli dp[i][c] z zależnościami i odtworzenie wybranych przedmiotów.',
    inputs: [{ key: 'items', label: 'Przedmioty „waga wartość”', kind: 'area', rows: 5, def: '2 3\n3 4\n4 5\n5 8' }, { key: 'W', label: 'Pojemność W', kind: 'text', def: '9' }],
    random: (r) => ({ items: Array.from({ length: r.int(3, 5) }, () => `${r.int(1, 6)} ${r.int(1, 12)}`).join('\n'), W: String(r.int(6, 12)) }),
    run: knapsack, legend: [['cmp', 'liczona komórka'], ['dep', 'wybrane źródło'], ['win', 'druga opcja'], ['path', 'odtwarzanie']] },
  { id: 'lcs', name: 'Najdłuższy wspólny podciąg (LCS)', cat: CAT, kb: 'dp-lcs', complexity: 'O(n·m)', view: 'table', code: LCS,
    about: 'Tabela dp[i][j] dla prefiksów dwóch napisów i odtworzenie podciągu.',
    inputs: [{ key: 'a', label: 'Napis a', kind: 'text', def: 'ABCBDAB' }, { key: 'b', label: 'Napis b', kind: 'text', def: 'BDCABA' }],
    random: (r) => { const al = 'ABCD'; const mk = (k) => Array.from({ length: k }, () => r.pick(al.split(''))).join(''); return { a: mk(r.int(5, 8)), b: mk(r.int(5, 8)) }; },
    run: lcs, legend: [['cmp', 'liczona komórka'], ['dep', 'skąd wartość'], ['win', 'druga opcja'], ['path', 'odtwarzanie']] },
  { id: 'lis', name: 'Najdłuższy podciąg rosnący (LIS)', cat: CAT, kb: 'dp-lis', complexity: 'O(n²)', view: 'arrays', code: LIS,
    about: 'dp[i] = najdłuższy rosnący podciąg kończący się na a[i]; odtwarzanie przez prv[].',
    inputs: [{ key: 'a', label: 'Tablica', kind: 'text', def: '3 10 2 1 20 4 6 7 5 9', ph: 'do 12 liczb' }],
    random: (r) => ({ a: r.ints(r.int(8, 11), 1, 30).join(' ') }), run: lis,
    legend: [['key', 'a[i]'], ['cmp', 'a[j] < a[i]'], ['miss', 'a[j] ≥ a[i]'], ['new', 'zmienione dp'], ['path', 'znaleziony podciąg']] },
];

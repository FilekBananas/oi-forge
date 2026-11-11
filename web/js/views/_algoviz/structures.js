// Struktury danych: drzewo przedziałowe (suma) i drzewo Fenwicka.
import { cpp, Rec, exec, parseInts, InputError, lowbit, bin } from './core.js';

const CAT = 'Struktury danych';

function parseOps(text, n, { allowRange = true, oneBased = false } = {}) {
  const lo = oneBased ? 1 : 0, hi = oneBased ? n : n - 1;
  const ops = [];
  for (const raw of String(text ?? '').split('\n')) {
    const ln = raw.trim();
    if (!ln || ln.startsWith('#') || ln.startsWith('//')) continue;
    const p = ln.split(/[\s,]+/);
    const kind = p[0];
    const nums = p.slice(1).map((x) => {
      if (!/^-?\d+$/.test(x)) throw new InputError(`Operacja „${ln}”: „${x}” nie jest liczbą.`);
      return parseInt(x, 10);
    });
    if (kind === '?' && (nums.length === 2 || (nums.length === 1 && !allowRange))) {
      if (nums.some((x) => x < lo || x > hi)) throw new InputError(`Operacja „${ln}”: indeksy muszą być z zakresu ${lo}…${hi}.`);
      if (nums.length === 2 && nums[0] > nums[1]) throw new InputError(`Operacja „${ln}”: musi być l ≤ r.`);
      ops.push({ op: 'query', l: nums[0], r: nums[1] ?? nums[0] });
    } else if (kind === '?' && nums.length === 1) {
      if (nums[0] < lo || nums[0] > hi) throw new InputError(`Operacja „${ln}”: indeks musi być z zakresu ${lo}…${hi}.`);
      ops.push({ op: 'prefix', i: nums[0] });
    } else if ((kind === '!' || kind === '=' || kind === '+') && nums.length === 2) {
      if (nums[0] < lo || nums[0] > hi) throw new InputError(`Operacja „${ln}”: indeks musi być z zakresu ${lo}…${hi}.`);
      if (Math.abs(nums[1]) > 999) throw new InputError(`Operacja „${ln}”: wartość z zakresu −999…999.`);
      ops.push({ op: kind === '+' ? 'add' : 'set', i: nums[0], v: nums[1] });
    } else throw new InputError(`Nie rozumiem operacji „${ln}”.`);
  }
  if (ops.length > 8) throw new InputError('Maksymalnie 8 operacji.');
  return ops;
}

// ------------------------------------------------------------------------------------------- drzewo przedziałowe
const SEG = cpp(`
void build(int v, int l, int r) {
    if (l == r) { t[v] = a[l]; return; }                @@leaf
    int m = (l + r) / 2;
    build(2 * v, l, m); build(2 * v + 1, m + 1, r);     @@rec
    t[v] = t[2 * v] + t[2 * v + 1];                     @@pull
}
long long query(int v, int l, int r, int ql, int qr) {
    if (qr < l || r < ql) return 0;        // rozłączne  @@out
    if (ql <= l && r <= qr) return t[v];   // w całości  @@full
    int m = (l + r) / 2;                                @@split
    return query(2 * v, l, m, ql, qr)
         + query(2 * v + 1, m + 1, r, ql, qr);
}
void update(int v, int l, int r, int pos, int val) {
    if (l == r) { t[v] = val; return; }                 @@uleaf
    int m = (l + r) / 2;
    if (pos <= m) update(2 * v, l, m, pos, val);        @@udown
    else update(2 * v + 1, m + 1, r, pos, val);
    t[v] = t[2 * v] + t[2 * v + 1];                     @@upull
}
`);

function segtree(vals) {
  const a = parseInts(vals.a, { name: 'Tablica', min: -99, max: 99, minLen: 2, maxLen: 16 });
  const n = a.length;
  const ops = parseOps(vals.ops, n).map((o) => (o.op === 'prefix' ? { op: 'query', l: 0, r: o.i } : o));
  const nodes = [];
  (function lay(v, l, r, d) { nodes.push({ v, l, r, d }); if (l < r) { const m = (l + r) >> 1; lay(2 * v, l, m, d + 1); lay(2 * v + 1, m + 1, r, d + 1); } })(1, 0, n - 1, 0);
  const size = 4 * n;
  const t = Array(size).fill(null);
  let ns = Array(size).fill('');
  const na = Array(n).fill('');
  let range = null, visited = 0, changed = 0;
  const r = new Rec(SEG);
  const S = (vars = {}) => ({ t: t.slice(), ns: ns.slice(), a: a.slice(), na: na.slice(), range, vars });
  const reset = () => { ns = Array(size).fill(''); na.fill(''); };
  function build(v, l, rr) {
    if (l === rr) {
      t[v] = a[l]; ns[v] = 'done'; na[l] = 'cmp';
      r.add('leaf', `build(${v}, ${l}, ${rr}): liść — t[${v}] = a[${l}] = ${a[l]}.`, S({ v, l, r: rr }));
      na[l] = '';
      return;
    }
    ns[v] = 'active';
    r.add('rec', `build(${v}, ${l}, ${rr}): węzeł odpowiada za a[${l}…${rr}]; buduję lewe (${2 * v}) i prawe (${2 * v + 1}) dziecko.`, S({ v, l, r: rr }));
    const m = (l + rr) >> 1;
    build(2 * v, l, m); build(2 * v + 1, m + 1, rr);
    t[v] = t[2 * v] + t[2 * v + 1]; ns[v] = 'done';
    r.add('pull', `t[${v}] = t[${2 * v}] + t[${2 * v + 1}] = ${t[2 * v]} + ${t[2 * v + 1]} = ${t[v]}.`, S({ v, l, r: rr }));
  }
  function query(v, l, rr, ql, qr) {
    visited++;
    if (qr < l || rr < ql) { ns[v] = 'miss'; r.add('out', `Węzeł ${v} [${l}, ${rr}] jest rozłączny z [${ql}, ${qr}] → 0.`, S({ v })); return 0; }
    if (ql <= l && rr <= qr) {
      ns[v] = 'hit';
      for (let i = l; i <= rr; i++) na[i] = 'hit';
      r.add('full', `Węzeł ${v} [${l}, ${rr}] leży w całości w zapytaniu → biorę gotowe t[${v}] = ${t[v]} i NIE schodzę niżej.`, S({ v }));
      return t[v];
    }
    ns[v] = 'cmp';
    r.add('split', `Węzeł ${v} [${l}, ${rr}] częściowo pokrywa [${ql}, ${qr}] → pytam oba dzieci.`, S({ v }));
    const m = (l + rr) >> 1;
    const res = query(2 * v, l, m, ql, qr) + query(2 * v + 1, m + 1, rr, ql, qr);
    r.add('split', `Węzeł ${v} [${l}, ${rr}] zwraca ${res}.`, S({ v, suma: res }));
    return res;
  }
  function update(v, l, rr, pos, val) {
    changed++;
    if (l === rr) { t[v] = val; ns[v] = 'new'; a[pos] = val; na[pos] = 'new'; r.add('uleaf', `Liść ${v}: t[${v}] = ${val}.`, S({ v })); return; }
    ns[v] = 'cmp';
    const m = (l + rr) >> 1;
    r.add('udown', `Węzeł ${v} [${l}, ${rr}]: pozycja ${pos} jest w ${pos <= m ? `lewym dziecku [${l}, ${m}]` : `prawym dziecku [${m + 1}, ${rr}]`}.`, S({ v }));
    if (pos <= m) update(2 * v, l, m, pos, val); else update(2 * v + 1, m + 1, rr, pos, val);
    t[v] = t[2 * v] + t[2 * v + 1]; ns[v] = 'new';
    r.add('upull', `Poprawiam w drodze powrotnej: t[${v}] = ${t[2 * v]} + ${t[2 * v + 1]} = ${t[v]}.`, S({ v }));
  }
  return exec(r, () => {
    r.add('rec', `Drzewo przedziałowe na ${n} elementach: każdy węzeł v trzyma sumę swojego przedziału, dzieci to 2v i 2v+1. Zaczynamy od build(1, 0, ${n - 1}).`, S());
    build(1, 0, n - 1);
    reset();
    r.add('pull', `Drzewo zbudowane w O(n). Korzeń t[1] = ${t[1]} to suma całej tablicy.`, S());
    for (const o of ops) {
      reset(); visited = 0;
      if (o.op === 'query') {
        range = [o.l, o.r];
        r.add('out', `Zapytanie: suma a[${o.l}…${o.r}].`, S());
        const res = query(1, 0, n - 1, o.l, o.r);
        r.add('full', `Wynik: suma a[${o.l}…${o.r}] = ${res}. Odwiedzono ${visited} węzłów — zawsze O(log n), bo na każdym poziomie najwyżej 2 węzły są „częściowe”.`, S({ wynik: res }));
        range = null;
      } else {
        range = [o.i, o.i];
        const val = o.op === 'add' ? a[o.i] + o.v : o.v;
        r.add('udown', `Aktualizacja: a[${o.i}] = ${val}. Zmieniają się tylko węzły na ścieżce od liścia do korzenia.`, S());
        changed = 0;
        update(1, 0, n - 1, o.i, val);
        r.add('upull', `Gotowe — zmieniono ${changed} węzłów (ścieżka liść → korzeń), O(log n). Nowa suma całości: ${t[1]}.`, S());
        range = null;
      }
    }
    reset();
    r.add('pull', ops.length ? 'Koniec operacji.' : 'Dodaj operacje „? l r” (suma) albo „! i v” (przypisanie), żeby zobaczyć zapytania i aktualizacje.', S());
  }, { n, nodes });
}

// ------------------------------------------------------------------------------------------- Fenwick
const FEN = cpp(`
// t[i] = suma a[i − lowbit(i) + 1 … i],  lowbit(i) = i & −i
void add(int i, long long x) {            // a[i] += x
    for (; i <= n; i += i & -i)           @@aloop
        t[i] += x;                        @@aupd
}
long long prefix(int i) {                 // a[1] + … + a[i]
    long long s = 0;
    for (; i > 0; i -= i & -i)            @@qloop
        s += t[i];                        @@qadd
    return s;                             @@qret
}
`);

function fenwick(vals) {
  const arr = parseInts(vals.a, { name: 'Tablica', min: -99, max: 99, minLen: 2, maxLen: 16 });
  const n = arr.length;
  const ops = parseOps(vals.ops, n, { oneBased: true });
  const a = [0, ...Array(n).fill(0)];
  const t = Array(n + 1).fill(0);
  let ns = Array(n + 1).fill(''), na = Array(n + 1).fill('');
  let cur = 0;
  const r = new Rec(FEN);
  const S = (vars = {}) => ({ a: a.slice(), t: t.slice(), ns: ns.slice(), na: na.slice(), cur, vars });
  const reset = () => { ns = Array(n + 1).fill(''); na = Array(n + 1).fill(''); cur = 0; };
  function add(i0, x, verbose) {
    let i = i0;
    const chain = [];
    a[i0] += x; na[i0] = 'new';
    while (i <= n) {
      chain.push(i);
      if (verbose) { cur = i; ns[i] = 'cmp'; r.add('aloop', `i = ${i} (${bin(i)}₂), lowbit = ${lowbit(i)} → t[${i}] pokrywa a[${i - lowbit(i) + 1}…${i}].`, S({ i, lowbit: lowbit(i) })); }
      t[i] += x;
      if (verbose) { ns[i] = 'new'; r.add('aupd', `t[${i}] += ${x} → ${t[i]}. Następny: i + lowbit(i) = ${i + lowbit(i)}.`, S({ i })); }
      i += lowbit(i);
    }
    return chain;
  }
  function prefix(i0, label) {
    let i = i0, s = 0;
    while (i > 0) {
      cur = i; ns[i] = 'cmp';
      r.add('qloop', `${label}: i = ${i} (${bin(i)}₂), t[${i}] = suma a[${i - lowbit(i) + 1}…${i}].`, S({ i, s }));
      s += t[i]; ns[i] = 'hit';
      for (let k = i - lowbit(i) + 1; k <= i; k++) na[k] = 'hit';
      r.add('qadd', `s += t[${i}] = ${t[i]} → s = ${s}. Następny: i − lowbit(i) = ${i - lowbit(i)} (zgaszony najniższy bit).`, S({ i, s }));
      i -= lowbit(i);
    }
    cur = 0;
    return s;
  }
  return exec(r, () => {
    r.add('aloop', `Drzewo Fenwicka (BIT) dla ${n} elementów, indeksy od 1. Każde t[i] pamięta sumę ostatnich lowbit(i) elementów kończących się na i. Budujemy je przez ${n} operacji add.`, S());
    for (let i = 1; i <= n; i++) {
      reset();
      const chain = add(i, arr[i - 1], false);
      chain.forEach((k) => { ns[k] = 'new'; });
      cur = i;
      r.add('aupd', `add(${i}, ${arr[i - 1]}): zmieniają się t[${chain.join('], t[')}] (i += lowbit(i)).`, S({ i }));
    }
    reset();
    r.add('qret', `Zbudowane. Zapytanie o prefiks i aktualizacja przechodzą po ≤ log₂(n) + 1 = ${Math.floor(Math.log2(n)) + 1} komórkach.`, S());
    for (const o of ops) {
      reset();
      if (o.op === 'prefix') {
        const s = prefix(o.i, `prefix(${o.i})`);
        r.add('qret', `prefix(${o.i}) = ${s}.`, S({ wynik: s }));
      } else if (o.op === 'query') {
        const pr = prefix(o.r, `prefix(${o.r})`);
        const pl = o.l > 1 ? prefix(o.l - 1, `prefix(${o.l - 1})`) : 0;
        for (let k = 1; k <= n; k++) na[k] = k >= o.l && k <= o.r ? 'win' : '';
        r.add('qret', `suma a[${o.l}…${o.r}] = prefix(${o.r}) − prefix(${o.l - 1}) = ${pr} − ${pl} = ${pr - pl}.`, S({ wynik: pr - pl }));
      } else {
        const x = o.op === 'add' ? o.v : o.v - a[o.i];
        r.add('aloop', o.op === 'add' ? `add(${o.i}, ${x}): a[${o.i}] += ${x}.` : `Przypisanie a[${o.i}] = ${o.v} to add(${o.i}, ${o.v} − ${a[o.i]}) = add(${o.i}, ${x}).`, S());
        add(o.i, x, true);
        cur = 0;
        r.add('aupd', `Gotowe: a[${o.i}] = ${a[o.i]}.`, S());
      }
    }
    reset();
    r.add('qret', ops.length ? 'Koniec operacji.' : 'Dodaj operacje: „? i” (prefiks), „? l r” (suma przedziału), „+ i x” (dodaj).', S());
  }, { n });
}

export const STRUCTURES = [
  { id: 'segtree', name: 'Drzewo przedziałowe', cat: CAT, kb: 'ds-segment-tree', complexity: 'O(log n) na operację', view: 'segtree', code: SEG,
    about: 'Budowa, zapytanie o sumę na przedziale i aktualizacja punktu — z podświetleniem odwiedzanych węzłów.',
    inputs: [{ key: 'a', label: 'Tablica a[0…n−1]', kind: 'text', def: '5 3 8 1 9 2 7 4', ph: 'do 16 liczb' },
      { key: 'ops', label: 'Operacje („? l r” suma, „! i v” przypisz)', kind: 'area', rows: 3, def: '? 2 6\n! 3 10\n? 0 4' }],
    random: (r) => { const n = r.int(6, 10); const l = r.int(0, n - 2); return { a: r.ints(n, 0, 9).join(' '), ops: `? ${l} ${r.int(l + 1, n - 1)}\n! ${r.int(0, n - 1)} ${r.int(0, 20)}\n? 0 ${r.int(1, n - 1)}` }; },
    run: segtree, legend: [['active', 'budowany'], ['done', 'policzony'], ['cmp', 'częściowo / na ścieżce'], ['hit', 'w całości w zapytaniu'], ['miss', 'rozłączny'], ['new', 'zaktualizowany']] },
  { id: 'fenwick', name: 'Drzewo Fenwicka (BIT)', cat: CAT, kb: 'ds-fenwick', complexity: 'O(log n) na operację', view: 'fenwick', code: FEN,
    about: 'Sumy prefiksowe z aktualizacjami: skoki o lowbit(i) = i & −i w górę (add) i w dół (prefix).',
    inputs: [{ key: 'a', label: 'Tablica a[1…n]', kind: 'text', def: '3 2 -1 6 5 4 -3 3 7 2 3 1', ph: 'do 16 liczb' },
      { key: 'ops', label: 'Operacje („? i”, „? l r”, „+ i x”)', kind: 'area', rows: 3, def: '? 11\n+ 3 5\n? 2 7' }],
    random: (r) => { const n = r.int(8, 16); const l = r.int(1, n - 1); return { a: r.ints(n, -5, 9).join(' '), ops: `? ${r.int(3, n)}\n+ ${r.int(1, n)} ${r.int(1, 9)}\n? ${l} ${r.int(l, n)}` }; },
    run: fenwick, legend: [['cmp', 'bieżące t[i]'], ['hit', 'zsumowane'], ['new', 'zmienione'], ['win', 'przedział zapytania']] },
];

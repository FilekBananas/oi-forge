// Wyszukiwanie binarne (w tablicy i po wyniku), dwa wskaźniki, okno przesuwne z kolejką monotoniczną, sumy prefiksowe.
import { cpp, Rec, exec, parseInts, parseInt1, InputError } from './core.js';

const CAT = 'Wyszukiwanie i techniki';

// ------------------------------------------------------------------------------------------- binsearch w tablicy
const BS = cpp(`
// pierwszy indeks i, dla którego a[i] >= x (jak lower_bound)
int lowerBound(const vector<int>& a, int x) {
    int l = 0, r = a.size();      // szukamy w [l, r)   @@init
    while (l < r) {                                      @@loop
        int mid = (l + r) / 2;                           @@mid
        if (a[mid] >= x) r = mid;                        @@right
        else l = mid + 1;                                @@left
    }
    return l;                                            @@ret
}
`);

function binsearch(vals) {
  let a = parseInts(vals.a, { name: 'Tablica', min: -999, max: 999, minLen: 1, maxLen: 20 });
  const x = parseInt1(vals.x, { name: 'Szukana wartość x', min: -9999, max: 9999 });
  const wasSorted = a.every((v, i) => !i || a[i - 1] <= v);
  a = a.slice().sort((p, q) => p - q);
  const n = a.length;
  const r = new Rec(BS);
  let l = 0, rr = n;
  const S = ({ mid = null, t = {}, res = null, vars = {} } = {}) => ({
    rows: [{
      key: 'a', label: 'a', kind: 'cells', idx: true, slots: n + 1,
      items: a.map((v, i) => ({ id: 'c' + i, v, s: i, t: t[i] || (res !== null ? (i === res ? 'hit' : 'dim') : (i < l || i >= rr ? 'dim' : '')) })),
      spans: l < rr ? [{ from: l, to: rr - 1, t: 'range' }] : [],
      ptrs: [{ name: 'l', s: l, t: 'lo' }, { name: 'r', s: rr, t: 'hi' }, mid !== null ? { name: 'mid', s: mid, t: 'cmp' } : null].filter(Boolean),
    }],
    vars: { x, l, r: rr, ...(mid !== null ? { mid } : {}), ...vars },
  });
  return exec(r, () => {
    r.add('init', `${wasSorted ? '' : 'Tablica nie była posortowana — posortowałem ją, bo binsearch działa tylko na posortowanych danych. '}Szukamy pierwszego indeksu z a[i] ≥ ${x}. Przedział kandydatów to [l, r) = [0, ${n}).`, S());
    let it = 0;
    while (l < rr) {
      it++;
      const mid = (l + rr) >> 1;
      r.add('mid', `Krok ${it}: mid = (${l} + ${rr}) / 2 = ${mid}, a[mid] = ${a[mid]}.`, S({ mid, t: { [mid]: 'cmp' } }));
      if (a[mid] >= x) {
        r.add('right', `a[${mid}] = ${a[mid]} ≥ ${x} → mid może być odpowiedzią, ale szukamy jeszcze bardziej na lewo: r = ${mid}.`, S({ mid, t: { [mid]: 'hit' } }));
        rr = mid;
      } else {
        r.add('left', `a[${mid}] = ${a[mid]} < ${x} → odpowiedź jest na prawo od mid: l = ${mid + 1}.`, S({ mid, t: { [mid]: 'miss' } }));
        l = mid + 1;
      }
      r.add('loop', `Zostało ${rr - l} kandydatów: [${l}, ${rr}).`, S());
    }
    const found = l < n && a[l] === x;
    r.add('ret', l === n
      ? `l = r = ${n}: wszystkie elementy są mniejsze od ${x} (lower_bound zwraca end()). Wykonano ${it} kroków.`
      : `l = r = ${l}: a[${l}] = ${a[l]} to pierwszy element ≥ ${x}. ${found ? `Znaleziono ${x}!` : `${x} nie występuje w tablicy.`} Wykonano ${it} kroków ≈ log₂(${n}).`,
    S({ res: l < n ? l : null, vars: { wynik: l } }));
  }, {});
}

// ------------------------------------------------------------------------------------------- binsearch po wyniku
const BSA = cpp(`
// czy w czasie T maszyny zrobią co najmniej K produktów?
bool check(long long T) {
    long long ile = 0;
    for (int x : t) ile += T / x;                    @@sum
    return ile >= K;                                 @@cret
}
// ...
long long lo = 0;                     // check(lo) = false
long long hi = 1LL * *min_element(t.begin(), t.end()) * K;   // check(hi) = true  @@init
while (hi - lo > 1) {                                @@loop
    long long mid = (lo + hi) / 2;                   @@mid
    if (check(mid)) hi = mid;                        @@yes
    else lo = mid;                                   @@no
}
cout << hi;   // najmniejsze T z check(T) = true     @@ans
`);

function bsAnswer(vals) {
  const t = parseInts(vals.t, { name: 'Czasy maszyn', min: 1, max: 50, minLen: 1, maxLen: 8 });
  const K = parseInt1(vals.k, { name: 'K', min: 1, max: 200 });
  const H = Math.min(...t) * K;
  const r = new Rec(BSA);
  let lo = 0, hi = H;
  const check = (T) => t.reduce((s, x) => s + Math.floor(T / x), 0);
  const S = ({ mid = null, ok = null, vars = {}, panel = null } = {}) => ({
    lo, hi, mid, ok, H,
    vars: { lo, hi, ...(mid !== null ? { mid } : {}), K, ...vars },
    panels: panel ? [panel] : [],
  });
  return exec(r, () => {
    r.add('init', `Szukamy najmniejszego czasu T, w którym maszyny (czasy ${t.join(', ')}) zrobią ${K} produktów. check(T) jest monotoniczne: fałsz… fałsz, prawda… prawda — więc szukamy binarnie miejsca zmiany. lo = 0 (na pewno za mało), hi = ${H} (najszybsza maszyna sama zrobi K).`, S());
    let it = 0;
    while (hi - lo > 1) {
      it++;
      const mid = Math.floor((lo + hi) / 2);
      const c = check(mid);
      const panel = { title: `check(${mid})`, items: t.map((x, i) => ({ v: `${mid} / ${x} = ${Math.floor(mid / x)}`, sub: `maszyna ${i + 1}` })).concat([{ v: `suma = ${c} ${c >= K ? '≥' : '<'} ${K}`, t: c >= K ? 'hit' : 'miss' }]) };
      r.add('mid', `Krok ${it}: mid = (${lo} + ${hi}) / 2 = ${mid}. Liczę check(${mid}).`, S({ mid, panel }));
      r.add('sum', `W czasie ${mid} maszyny zrobią ${t.map((x) => Math.floor(mid / x)).join(' + ')} = ${c} produktów.`, S({ mid, ok: c >= K, panel }));
      if (c >= K) { r.add('yes', `${c} ≥ ${K} → czas ${mid} wystarcza, odpowiedź jest ≤ ${mid}: hi = ${mid}.`, S({ mid, ok: true, panel })); hi = mid; }
      else { r.add('no', `${c} < ${K} → czas ${mid} to za mało: lo = ${mid}.`, S({ mid, ok: false, panel })); lo = mid; }
    }
    r.add('ans', `hi − lo = 1, więc koniec: odpowiedź T = ${hi} (check(${lo}) = fałsz, check(${hi}) = prawda). ${it} kroków zamiast sprawdzania ${H} czasów po kolei.`, S({ vars: { wynik: hi } }));
  }, { H });
}

// ------------------------------------------------------------------------------------------- dwa wskaźniki
const TP = cpp(`
// najdłuższy spójny fragment o sumie <= S (liczby nieujemne)
int l = 0, best = 0;
long long suma = 0;                                     @@init
for (int r = 0; r < n; r++) {                           @@loop
    suma += a[r];              // głowa idzie naprzód   @@add
    while (suma > S) {         // za dużo — ogon dogania  @@while
        suma -= a[l];                                    @@sub
        l++;
    }
    best = max(best, r - l + 1);                        @@best
}
`);

function twoPointers(vals) {
  const a = parseInts(vals.a, { name: 'Tablica', min: 0, max: 99, minLen: 1, maxLen: 18 });
  const Smax = parseInt1(vals.s, { name: 'S', min: 0, max: 9999 });
  const n = a.length;
  const r = new Rec(TP);
  let l = 0, sum = 0, best = 0, bl = 0, br = -1, rr = -1;
  const S = ({ t = {}, vars = {} } = {}) => ({
    rows: [{
      key: 'a', label: 'a', kind: 'cells', idx: true, slots: n,
      items: a.map((v, i) => ({ id: 'c' + i, v, s: i, t: t[i] || (i >= l && i <= rr ? 'win' : '') })),
      spans: [rr >= l ? { from: l, to: rr, t: 'win' } : null, br >= bl && best ? { from: bl, to: br, t: 'best', pos: 'under', label: `najlepszy: ${best}` } : null].filter(Boolean),
      ptrs: [{ name: 'l', s: l, t: 'lo' }, rr >= 0 ? { name: 'r', s: rr, t: 'hi' } : null].filter(Boolean),
    }],
    vars: { S: Smax, suma: sum, best, ...vars },
  });
  return exec(r, () => {
    r.add('init', `Szukamy najdłuższego fragmentu o sumie ≤ ${Smax}. Gąsienica: głowa r idzie zawsze naprzód, ogon l tylko dogania — każdy wskaźnik przejdzie tablicę raz, więc O(n).`, S());
    for (rr = 0; rr < n; rr++) {
      sum += a[rr];
      r.add('add', `Głowa r = ${rr}: suma += ${a[rr]} → ${sum}.`, S({ t: { [rr]: 'new' } }));
      while (sum > Smax) {
        r.add('while', `suma = ${sum} > ${Smax} — za dużo, ogon musi się przesunąć.`, S({ t: { [l]: 'miss' } }));
        sum -= a[l]; l++;
        r.add('sub', `Odejmuję a[${l - 1}] = ${a[l - 1]}, l = ${l}, suma = ${sum}.`, S());
      }
      if (rr - l + 1 > best) { best = rr - l + 1; bl = l; br = rr; r.add('best', `Okno [${l}, ${rr}] ma długość ${best} — nowy rekord!`, S({ t: {} })); }
      else r.add('best', `Okno [${l}, ${rr}] ma długość ${rr - l + 1} (rekord: ${best}).`, S());
    }
    rr = n - 1;
    r.add('best', `Koniec: najdłuższy fragment o sumie ≤ ${Smax} ma długość ${best}${best ? ` (a[${bl}…${br}])` : ''}.`, S());
  }, {});
}

// ------------------------------------------------------------------------------------------- okno przesuwne
const SW = cpp(`
// maksimum w każdym oknie długości k
deque<int> dq;          // indeksy; wartości a[] malejące
for (int i = 0; i < n; i++) {                                      @@loop
    while (!dq.empty() && a[dq.back()] <= a[i]) dq.pop_back();     @@popBack
    dq.push_back(i);                                               @@push
    if (dq.front() <= i - k) dq.pop_front();   // wypadł z okna    @@popFront
    if (i >= k - 1) cout << a[dq.front()] << ' ';                  @@out
}
`);

function slidingWindow(vals) {
  const a = parseInts(vals.a, { name: 'Tablica', min: -99, max: 99, minLen: 2, maxLen: 16 });
  const n = a.length;
  const k = parseInt1(vals.k, { name: 'k', min: 1, max: n });
  const r = new Rec(SW);
  const dq = [];
  const out = [];
  let i = -1;
  const S = ({ ta = {}, td = {}, vars = {} } = {}) => ({
    rows: [
      { key: 'a', label: 'a', kind: 'cells', idx: true, slots: n,
        items: a.map((v, j) => ({ id: 'a' + j, v, s: j, t: ta[j] || (i >= 0 && j > i - k && j <= i ? 'win' : '') })),
        spans: i >= 0 ? [{ from: Math.max(0, i - k + 1), to: i, t: 'win' }] : [], ptrs: i >= 0 ? [{ name: 'i', s: i }] : [] },
      { key: 'dq', label: 'deque', kind: 'cells', slots: n, gap: true,
        items: dq.map((j, p) => ({ id: 'd' + j, v: a[j], sub: 'i=' + j, s: p, t: td[j] || (p === 0 ? 'hit' : '') })) },
      { key: 'out', label: 'maks.', kind: 'cells', slots: n, items: out.map((v, p) => ({ id: 'o' + p, v, s: p + k - 1, t: 'done' })) },
    ],
    vars: { i, k, ...vars },
  });
  return exec(r, () => {
    r.add('loop', `Dla każdego okna ${k} kolejnych elementów chcemy maksimum. Kolejka monotoniczna trzyma indeksy o malejących wartościach — maksimum jest zawsze na początku. Każdy indeks wchodzi i wychodzi raz → O(n).`, S());
    for (i = 0; i < n; i++) {
      r.add('loop', `i = ${i}, a[i] = ${a[i]}.`, S({ ta: { [i]: 'cmp' } }));
      while (dq.length && a[dq[dq.length - 1]] <= a[i]) {
        const b = dq[dq.length - 1];
        r.add('popBack', `a[${b}] = ${a[b]} ≤ ${a[i]} — element ${a[b]} nigdy już nie będzie maksimum (jest starszy i nie większy), usuwam go z końca.`, S({ ta: { [i]: 'cmp' }, td: { [b]: 'miss' } }));
        dq.pop();
        r.add('popBack', `Usunięto indeks ${b} z końca kolejki.`, S({ ta: { [i]: 'cmp' } }));
      }
      dq.push(i);
      r.add('push', `Dokładam indeks ${i} na koniec kolejki.`, S({ ta: { [i]: 'cmp' }, td: { [i]: 'new' } }));
      if (dq[0] <= i - k) {
        const f = dq[0];
        r.add('popFront', `Indeks ${f} jest już poza oknem [${i - k + 1}, ${i}] — usuwam go z początku.`, S({ td: { [f]: 'miss' } }));
        dq.shift();
        r.add('popFront', `Usunięto indeks ${f} z początku kolejki.`, S());
      }
      if (i >= k - 1) {
        out.push(a[dq[0]]);
        r.add('out', `Okno [${i - k + 1}, ${i}]: maksimum = a[${dq[0]}] = ${a[dq[0]]} (początek kolejki).`, S({ ta: { [dq[0]]: 'hit' } }));
      }
    }
    i = n - 1;
    r.add('out', `Gotowe! Maksima okien: ${out.join(', ')}.`, S());
  }, {});
}

// ------------------------------------------------------------------------------------------- sumy prefiksowe
const PREF = cpp(`
// pref[i] = a[1] + a[2] + ... + a[i]
pref[0] = 0;                                    @@zero
for (int i = 1; i <= n; i++)                    @@loop
    pref[i] = pref[i - 1] + a[i];               @@build
// suma na przedziale [l, r] w O(1):
long long suma(int l, int r) {
    return pref[r] - pref[l - 1];               @@query
}
`);

function prefixSums(vals) {
  const a = parseInts(vals.a, { name: 'Tablica', min: -99, max: 99, minLen: 1, maxLen: 15 });
  const n = a.length;
  const qs = String(vals.q || '').split(/[\n;]+/).map((s) => s.trim()).filter(Boolean).map((s) => {
    const p = s.split(/[\s,]+/).map(Number);
    if (p.length !== 2 || p.some((x) => !Number.isInteger(x))) throw new InputError(`Zapytanie „${s}”: podaj „l r”.`);
    if (p[0] < 1 || p[1] > n || p[0] > p[1]) throw new InputError(`Zapytanie „${s}”: musi być 1 ≤ l ≤ r ≤ ${n}.`);
    return p;
  });
  if (qs.length > 6) throw new InputError('Maksymalnie 6 zapytań.');
  const pref = [0];
  const r = new Rec(PREF);
  const S = ({ ta = {}, tp = {}, links = [], spans = [], vars = {} } = {}) => ({
    rows: [
      { key: 'a', label: 'a', kind: 'cells', idx: true, slots: n + 1, items: a.map((v, i) => ({ id: 'a' + (i + 1), v, s: i + 1, t: ta[i + 1] || '' })), spans },
      { key: 'p', label: 'pref', kind: 'cells', idx: true, slots: n + 1, gap: true, items: pref.map((v, i) => ({ id: 'p' + i, v, s: i, t: tp[i] || '' })) },
    ],
    links, vars,
  });
  return exec(r, () => {
    r.add('zero', 'Sumy prefiksowe: raz liczymy pref[i] = suma pierwszych i elementów (O(n)), a potem każde zapytanie o sumę przedziału to jedno odejmowanie (O(1)). Tablica a jest indeksowana od 1.', S({ tp: { 0: 'new' } }));
    for (let i = 1; i <= n; i++) {
      pref.push(pref[i - 1] + a[i - 1]);
      r.add('build', `pref[${i}] = pref[${i - 1}] + a[${i}] = ${pref[i - 1]} + ${a[i - 1]} = ${pref[i]}.`,
        S({ ta: { [i]: 'cmp' }, tp: { [i - 1]: 'cmp', [i]: 'new' }, links: [{ from: ['p', i - 1], to: ['p', i], t: 'cmp' }, { from: ['a', i], to: ['p', i], t: 'cmp' }], vars: { i } }));
    }
    r.add('loop', `Tablica pref gotowa: pref[${n}] = ${pref[n]} to suma całej tablicy.`, S());
    for (const [l, rr] of qs) {
      const res = pref[rr] - pref[l - 1];
      const ta = {};
      for (let i = l; i <= rr; i++) ta[i] = 'win';
      r.add('query', `Zapytanie [${l}, ${rr}]: pref[${rr}] − pref[${l - 1}] = ${pref[rr]} − ${pref[l - 1]} = ${res}. (Sprawdzenie: ${a.slice(l - 1, rr).join(' + ')} = ${res}.)`,
        S({ ta, tp: { [rr]: 'plus', [l - 1]: 'minus' }, spans: [{ from: l, to: rr, t: 'win' }], links: [{ from: ['p', l - 1], to: ['p', rr], t: 'minus' }], vars: { l, r: rr, wynik: res } }));
    }
    if (!qs.length) r.add('query', 'Dodaj zapytania „l r” w polu po lewej, aby zobaczyć odpowiedzi w O(1).', S());
  }, {});
}

export const SEARCHING = [
  { id: 'binsearch', name: 'Wyszukiwanie binarne', cat: CAT, kb: 'tech-binary-search', complexity: 'O(log n)', view: 'arrays', code: BS,
    about: 'lower_bound: połowienie przedziału kandydatów [l, r) na posortowanej tablicy.',
    inputs: [{ key: 'a', label: 'Tablica (zostanie posortowana)', kind: 'text', def: '2 3 5 7 11 13 17 19 23 29 31 37', ph: 'np. 1 4 4 8 10' },
      { key: 'x', label: 'Szukane x', kind: 'text', def: '19', ph: 'np. 7' }],
    random: (r) => { const a = [...new Set(r.ints(r.int(9, 15), 1, 60))].sort((p, q) => p - q); return { a: a.join(' '), x: String(r.next() < 0.7 ? r.pick(a) : r.int(1, 60)) }; },
    run: binsearch, legend: [['range', 'kandydaci [l, r)'], ['cmp', 'mid'], ['hit', 'a[mid] ≥ x'], ['miss', 'a[mid] < x'], ['dim', 'odrzucone']] },
  { id: 'bsanswer', name: 'Wyszukiwanie binarne po wyniku', cat: CAT, kb: 'tech-binary-search-answer', complexity: 'O(log W · n)', view: 'axis', code: BSA,
    about: 'Klasyk: minimalny czas, w którym maszyny wyprodukują K produktów. Binsearch po osi odpowiedzi.',
    inputs: [{ key: 't', label: 'Czasy maszyn tᵢ', kind: 'text', def: '3 5 7', ph: 'np. 2 3 7' }, { key: 'k', label: 'K produktów', kind: 'text', def: '20', ph: 'np. 10' }],
    random: (r) => ({ t: r.ints(r.int(2, 4), 2, 9).join(' '), k: String(r.int(8, 40)) }), run: bsAnswer,
    legend: [['miss', 'check = fałsz'], ['hit', 'check = prawda'], ['cmp', 'mid'], ['range', 'nieznane']] },
  { id: 'twopointers', name: 'Dwa wskaźniki (gąsienica)', cat: CAT, kb: 'tech-two-pointers', complexity: 'O(n)', view: 'arrays', code: TP,
    about: 'Najdłuższy fragment o sumie ≤ S: głowa idzie naprzód, ogon dogania.',
    inputs: [{ key: 'a', label: 'Tablica (liczby ≥ 0)', kind: 'text', def: '3 1 2 7 4 2 1 1 5 3 2', ph: 'np. 2 1 3 4' }, { key: 's', label: 'S (maks. suma)', kind: 'text', def: '8' }],
    random: (r) => ({ a: r.ints(r.int(9, 15), 0, 9).join(' '), s: String(r.int(7, 18)) }), run: twoPointers,
    legend: [['win', 'okno [l, r]'], ['new', 'nowy element'], ['miss', 'wypada z okna'], ['best', 'najlepsze okno']] },
  { id: 'window', name: 'Okno przesuwne (kolejka monotoniczna)', cat: CAT, kb: 'ds-monotonic-queue', complexity: 'O(n)', view: 'arrays', code: SW,
    about: 'Maksimum w każdym oknie długości k z użyciem deque trzymającego malejące wartości.',
    inputs: [{ key: 'a', label: 'Tablica', kind: 'text', def: '1 3 -1 -3 5 3 6 7 2 4', ph: 'np. 4 2 12 3' }, { key: 'k', label: 'k (długość okna)', kind: 'text', def: '3' }],
    random: (r) => ({ a: r.ints(r.int(9, 14), -9, 20).join(' '), k: String(r.int(2, 4)) }), run: slidingWindow,
    legend: [['win', 'bieżące okno'], ['cmp', 'a[i]'], ['hit', 'maksimum (przód kolejki)'], ['miss', 'usuwany'], ['new', 'dodany']] },
  { id: 'prefix', name: 'Sumy prefiksowe', cat: CAT, kb: 'ds-prefix-sums', complexity: 'O(n) + O(1) na zapytanie', view: 'arrays', code: PREF,
    about: 'Budowa tablicy pref i odpowiedzi na zapytania o sumę przedziału jednym odejmowaniem.',
    inputs: [{ key: 'a', label: 'Tablica a[1…n]', kind: 'text', def: '3 -1 4 1 5 -9 2 6', ph: 'np. 1 2 3 4' },
      { key: 'q', label: 'Zapytania „l r” (po jednym w linii)', kind: 'area', rows: 3, def: '2 5\n1 8\n4 4' }],
    random: (r) => { const n = r.int(6, 12); const q = []; for (let k = 0; k < 3; k++) { const l = r.int(1, n); q.push(`${l} ${r.int(l, n)}`); } return { a: r.ints(n, -9, 9).join(' '), q: q.join('\n') }; },
    run: prefixSums, legend: [['cmp', 'używane w kroku'], ['new', 'policzone pref[i]'], ['plus', '+ pref[r]'], ['minus', '− pref[l−1]'], ['win', 'przedział zapytania']] },
];

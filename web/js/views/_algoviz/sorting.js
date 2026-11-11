// Sortowania: bąbelkowe, przez wstawianie, przez scalanie, szybkie, przez zliczanie.
import { cpp, Rec, exec, parseInts } from './core.js';

const CAT = 'Sortowanie';
const ARR_INPUT = [{ key: 'a', label: 'Tablica', kind: 'text', def: '5 2 9 1 7 3 8 4', ph: 'np. 5 2 9 1 7', hint: 'do 16 liczb z zakresu 1…99' }];
const randArr = (r) => ({ a: r.ints(r.int(7, 12), 1, 60).join(' ') });
const elems = (vals) => vals.map((v, i) => ({ id: 'e' + i, v }));
const parseArr = (vals) => parseInts(vals.a, { name: 'Tablica', min: 1, max: 99, minLen: 2, maxLen: 16 });

function barsRow(a, toneOf, extra = {}) {
  return {
    key: 'a', label: 'a', kind: 'bars', idx: true, slots: a.length,
    items: a.map((x, i) => (x ? { id: x.id, v: x.v, s: i, t: toneOf(i, x) || '' } : null)).filter(Boolean),
    ...extra,
  };
}

const LEGEND_BASIC = [['cmp', 'porównywane'], ['swap', 'zamieniane'], ['done', 'na swoim miejscu']];

// ------------------------------------------------------------------------------------------- bąbelkowe
const BUBBLE = cpp(`
void bubbleSort(vector<int>& a) {
    int n = a.size();
    for (int i = 0; i < n - 1; i++) {        @@outer
        bool zamiana = false;
        for (int j = 0; j + 1 < n - i; j++) {
            if (a[j] > a[j + 1]) {           @@cmp
                swap(a[j], a[j + 1]);        @@swap
                zamiana = true;
            }
        }
        if (!zamiana) break;                 @@early
    }
}                                            @@end
`);

function bubble(vals) {
  const a = elems(parseArr(vals));
  const n = a.length;
  const r = new Rec(BUBBLE);
  const done = new Set();
  let cmps = 0, swaps = 0;
  const S = (t = {}, ptrs = [], vars = {}) => ({
    rows: [barsRow(a, (i) => t[i] || (done.has(i) ? 'done' : ''), { ptrs })], vars: { ...vars, porównania: cmps, zamiany: swaps },
  });
  return exec(r, () => {
    r.add('outer', `Sortujemy ${n} liczb. W każdym przebiegu największy element z nieposortowanej części „wypływa” na koniec jak bąbelek.`, S());
    for (let i = 0; i < n - 1; i++) {
      let swapped = false;
      r.add('outer', `Przebieg ${i + 1}: porównujemy sąsiednie pary na pozycjach 0…${n - 1 - i}.`, S({}, [], { i }));
      for (let j = 0; j + 1 < n - i; j++) {
        const gt = a[j].v > a[j + 1].v;
        cmps++;
        r.add('cmp', `Porównuję a[${j}] = ${a[j].v} i a[${j + 1}] = ${a[j + 1].v}: ${gt ? `${a[j].v} > ${a[j + 1].v}, więc trzeba je zamienić.` : 'kolejność jest dobra.'}`,
          S({ [j]: 'cmp', [j + 1]: 'cmp' }, [{ name: 'j', s: j }], { i, j }));
        if (gt) {
          [a[j], a[j + 1]] = [a[j + 1], a[j]];
          swapped = true; swaps++;
          r.add('swap', `Zamiana: teraz a[${j}] = ${a[j].v}, a[${j + 1}] = ${a[j + 1].v}.`, S({ [j]: 'swap', [j + 1]: 'swap' }, [{ name: 'j', s: j }], { i, j }));
        }
      }
      done.add(n - 1 - i);
      if (!swapped) {
        for (let k = 0; k < n; k++) done.add(k);
        r.add('early', `W całym przebiegu nie było zamiany — tablica jest już posortowana, więc kończymy wcześniej (to dlatego najlepszy przypadek to O(n)).`, S({}, [], { i }));
        break;
      }
      r.add('early', `Koniec przebiegu ${i + 1}: element ${a[n - 1 - i].v} trafił na swoje miejsce (pozycja ${n - 1 - i}).`, S({}, [], { i }));
    }
    for (let k = 0; k < n; k++) done.add(k);
    r.add('end', `Posortowane! ${cmps} porównań i ${swaps} zamian. Złożoność O(n²) — przy n = 10⁵ to już ~5·10⁹ porównań, za dużo.`, S());
  }, { maxV: Math.max(...a.map((x) => x.v)) });
}

// ------------------------------------------------------------------------------------------- wstawianie
const INSERTION = cpp(`
void insertionSort(vector<int>& a) {
    int n = a.size();
    for (int i = 1; i < n; i++) {               @@outer
        int x = a[i];        // wstawiany klucz @@key
        int j = i - 1;
        while (j >= 0 && a[j] > x) {            @@cmp
            a[j + 1] = a[j]; // przesuń w prawo @@shift
            j--;
        }
        a[j + 1] = x;                           @@place
    }
}                                               @@end
`);

function insertion(vals) {
  const a = elems(parseArr(vals));
  const n = a.length;
  const r = new Rec(INSERTION);
  let sortedTo = 0;
  let allDone = false;
  const S = (t = {}, ptrs = [], vars = {}) => ({
    rows: [barsRow(a, (i) => t[i] || (allDone ? 'done' : i <= sortedTo ? 'sorted' : ''), { ptrs })], vars,
  });
  return exec(r, () => {
    r.add('outer', 'Lewa część tablicy jest zawsze posortowana. Bierzemy kolejny element i „wsuwamy” go w dobre miejsce, przesuwając większe w prawo — jak przy układaniu kart w ręce.', S());
    for (let i = 1; i < n; i++) {
      const key = a[i];
      let p = i;
      r.add('key', `Klucz x = a[${i}] = ${key.v}. Fragment a[0…${i - 1}] jest posortowany.`, S({ [p]: 'key' }, [{ name: 'i', s: i }], { i, x: key.v }));
      let j = i - 1;
      while (j >= 0) {
        const gt = a[j].v > key.v;
        r.add('cmp', `Porównuję a[${j}] = ${a[j].v} z x = ${key.v}: ${gt ? `${a[j].v} > ${key.v}, więc przesuwam ${a[j].v} w prawo.` : `${a[j].v} ≤ ${key.v} — tu się zatrzymujemy.`}`,
          S({ [j]: 'cmp', [p]: 'key' }, [{ name: 'j', s: j }, { name: 'i', s: i }], { i, j, x: key.v }));
        if (!gt) break;
        [a[j], a[j + 1]] = [a[j + 1], a[j]];
        p = j;
        r.add('shift', `a[${j + 1}] = a[${j}] (${a[j + 1].v} przesunięte w prawo).`, S({ [j + 1]: 'swap', [p]: 'key' }, [{ name: 'j', s: j }, { name: 'i', s: i }], { i, j, x: key.v }));
        j--;
      }
      if (j < 0) r.add('cmp', `j = −1 — doszliśmy do początku tablicy, x jest najmniejszy z dotychczasowych.`, S({ [p]: 'key' }, [{ name: 'i', s: i }], { i, j, x: key.v }));
      sortedTo = i;
      r.add('place', `Wstawiam x = ${key.v} na pozycję ${p}. Teraz a[0…${i}] jest posortowane.`, S({ [p]: 'done' }, [{ name: 'i', s: i }], { i, x: key.v }));
    }
    allDone = true;
    r.add('end', 'Posortowane! Dla prawie posortowanych danych to sortowanie jest bardzo szybkie (mało przesunięć), w najgorszym razie O(n²).', S());
  }, { maxV: Math.max(...a.map((x) => x.v)) });
}

// ------------------------------------------------------------------------------------------- scalanie
const MERGE = cpp(`
vector<int> tmp;
void mergeSort(vector<int>& a, int l, int r) {
    if (l >= r) return;       // 0 lub 1 element       @@base
    int m = (l + r) / 2;                               @@mid
    mergeSort(a, l, m);                                @@left
    mergeSort(a, m + 1, r);                            @@right
    tmp.clear();
    int i = l, j = m + 1;                              @@init
    while (i <= m && j <= r) {
        if (a[i] <= a[j]) tmp.push_back(a[i++]);       @@takeL
        else tmp.push_back(a[j++]);                    @@takeR
    }
    while (i <= m) tmp.push_back(a[i++]);              @@restL
    while (j <= r) tmp.push_back(a[j++]);              @@restR
    for (int k = l; k <= r; k++) a[k] = tmp[k - l];    @@copy
}
`);

function merge(vals) {
  const init = elems(parseArr(vals));
  const n = init.length;
  const slots = init.slice();
  const r = new Rec(MERGE);
  let tmp = [], tmpBase = 0;
  const sorted = new Set();
  let finished = false;
  const S = ({ t = {}, spans = [], ptrs = [], vars = {} } = {}) => ({
    rows: [
      barsRow(slots, (i, x) => t[i] || (finished ? 'done' : sorted.has(x.id) ? 'sorted' : ''), { spans, ptrs }),
      { key: 'tmp', label: 'tmp', kind: 'bars', h: 110, slots: n, items: tmp.map((x, k) => ({ id: x.id, v: x.v, s: tmpBase + k, t: 'new' })) },
    ],
    vars,
  });
  function rec(l, rr, depth) {
    r.add('base', `mergeSort(${l}, ${rr}) — ${l >= rr ? 'jeden element, nic do roboty.' : `fragment [${l}, ${rr}] (${rr - l + 1} elementów).`}`,
      S({ spans: [{ from: l, to: rr, t: 'range' }], vars: { l, r: rr, głębokość: depth } }));
    if (l >= rr) { sorted.add(slots[l].id); return; }
    const m = (l + rr) >> 1;
    r.add('mid', `Dzielę na połowy: [${l}, ${m}] i [${m + 1}, ${rr}].`,
      S({ spans: [{ from: l, to: m, t: 'lo' }, { from: m + 1, to: rr, t: 'hi' }], vars: { l, m, r: rr, głębokość: depth } }));
    rec(l, m, depth + 1);
    rec(m + 1, rr, depth + 1);
    tmp = []; tmpBase = l;
    let i = l, j = m + 1;
    const spans = [{ from: l, to: m, t: 'lo' }, { from: m + 1, to: rr, t: 'hi' }];
    const P = () => [i <= m ? { name: 'i', s: i } : null, j <= rr ? { name: 'j', s: j } : null].filter(Boolean);
    r.add('init', `Scalam posortowane połowy [${l}, ${m}] i [${m + 1}, ${rr}]. Wskaźniki i = ${i}, j = ${j}.`, S({ spans, ptrs: P(), vars: { l, m, r: rr, i, j } }));
    while (i <= m && j <= rr) {
      const left = slots[i].v <= slots[j].v;
      const msg = `a[i] = ${slots[i].v} ${left ? '≤' : '>'} a[j] = ${slots[j].v} → do tmp trafia ${left ? slots[i].v + ' z lewej połowy' : slots[j].v + ' z prawej połowy'}.`;
      r.add(left ? 'takeL' : 'takeR', msg, S({ t: { [i]: 'cmp', [j]: 'cmp' }, spans, ptrs: P(), vars: { l, m, r: rr, i, j } }));
      if (left) { tmp.push(slots[i]); slots[i] = null; i++; } else { tmp.push(slots[j]); slots[j] = null; j++; }
      r.add(left ? 'takeL' : 'takeR', `tmp = [${tmp.map((x) => x.v).join(', ')}]`, S({ spans, ptrs: P(), vars: { l, m, r: rr, i, j } }));
    }
    while (i <= m) { tmp.push(slots[i]); slots[i] = null; i++; r.add('restL', `Prawa połowa się skończyła — dopisuję resztę lewej: ${tmp[tmp.length - 1].v}.`, S({ spans, ptrs: P(), vars: { l, m, r: rr, i, j } })); }
    while (j <= rr) { tmp.push(slots[j]); slots[j] = null; j++; r.add('restR', `Lewa połowa się skończyła — dopisuję resztę prawej: ${tmp[tmp.length - 1].v}.`, S({ spans, ptrs: P(), vars: { l, m, r: rr, i, j } })); }
    for (let k = l; k <= rr; k++) { slots[k] = tmp[k - l]; sorted.add(slots[k].id); }
    tmp = [];
    r.add('copy', `Kopiuję tmp z powrotem do a[${l}…${rr}] — ten fragment jest teraz posortowany.`, S({ spans: [{ from: l, to: rr, t: 'range' }], vars: { l, m, r: rr } }));
  }
  return exec(r, () => {
    rec(0, n - 1, 0);
    finished = true;
    r.add('copy', `Posortowane! Głębokość rekurencji to ⌈log₂ n⌉, a na każdym poziomie scalamy łącznie n elementów → O(n log n) zawsze, ale potrzebna jest dodatkowa tablica tmp.`, S());
  }, { maxV: Math.max(...init.map((x) => x.v)) });
}

// ------------------------------------------------------------------------------------------- szybkie
const QUICK = cpp(`
int partition(vector<int>& a, int l, int r) {
    int pivot = a[r];          // ostatni jako pivot     @@pivot
    int i = l;                 // koniec „mniejszych”    @@iinit
    for (int j = l; j < r; j++) {
        if (a[j] < pivot) {                              @@cmp
            swap(a[i], a[j]);                            @@swap
            i++;
        }
    }
    swap(a[i], a[r]);          // pivot na swoje miejsce @@place
    return i;
}
void quickSort(vector<int>& a, int l, int r) {
    if (l >= r) return;                                  @@base
    int p = partition(a, l, r);                          @@part
    quickSort(a, l, p - 1);                              @@left
    quickSort(a, p + 1, r);                              @@right
}
`);

function quick(vals) {
  const a = elems(parseArr(vals));
  const n = a.length;
  const r = new Rec(QUICK);
  const done = new Set();
  const S = ({ t = {}, spans = [], ptrs = [], vars = {} } = {}) => ({
    rows: [barsRow(a, (i, x) => t[i] || (done.has(x.id) ? 'done' : ''), { spans, ptrs })], vars,
  });
  function qs(l, rr, depth) {
    if (l >= rr) {
      if (l === rr) done.add(a[l].id);
      r.add('base', l === rr ? `quickSort(${l}, ${rr}): jeden element — jest już na swoim miejscu.` : `quickSort(${l}, ${rr}): pusty fragment.`,
        S({ spans: l <= rr ? [{ from: l, to: rr, t: 'range' }] : [], vars: { l, r: rr, głębokość: depth } }));
      return;
    }
    r.add('part', `quickSort(${l}, ${rr}): dzielę fragment [${l}, ${rr}] względem pivota.`, S({ spans: [{ from: l, to: rr, t: 'range' }], vars: { l, r: rr, głębokość: depth } }));
    const pivot = a[rr];
    let i = l;
    r.add('pivot', `pivot = a[${rr}] = ${pivot.v}. Elementy mniejsze od niego zbieramy na początku fragmentu.`, S({ t: { [rr]: 'pivot' }, ptrs: [{ name: 'i', s: i }], vars: { l, r: rr, pivot: pivot.v, i } }));
    for (let j = l; j < rr; j++) {
      const less = a[j].v < pivot.v;
      const spans = [{ from: l, to: i - 1, t: 'lo' }, { from: i, to: j - 1, t: 'hi' }].filter((s) => s.from <= s.to);
      r.add('cmp', `a[${j}] = ${a[j].v} ${less ? '<' : '≥'} pivot ${pivot.v}${less ? ` → zamieniam z a[${i}] i przesuwam granicę i.` : ' → zostaje po prawej stronie granicy.'}`,
        S({ t: { [j]: 'cmp', [rr]: 'pivot' }, spans, ptrs: [{ name: 'i', s: i }, { name: 'j', s: j }], vars: { l, r: rr, pivot: pivot.v, i, j } }));
      if (less) {
        [a[i], a[j]] = [a[j], a[i]];
        r.add('swap', `swap(a[${i}], a[${j}]) → a[${i}] = ${a[i].v}; potem i = ${i + 1}.`,
          S({ t: { [i]: 'swap', [j]: 'swap', [rr]: 'pivot' }, spans: [{ from: l, to: i, t: 'lo' }], ptrs: [{ name: 'i', s: i }, { name: 'j', s: j }], vars: { l, r: rr, pivot: pivot.v, i, j } }));
        i++;
      }
    }
    [a[i], a[rr]] = [a[rr], a[i]];
    done.add(a[i].id);
    r.add('place', `Wstawiam pivot ${pivot.v} na pozycję ${i}: po lewej są mniejsze, po prawej ≥ ${pivot.v}. Pivot jest już na swoim docelowym miejscu.`,
      S({ spans: [{ from: l, to: i - 1, t: 'lo' }, { from: i + 1, to: rr, t: 'hi' }].filter((s) => s.from <= s.to), ptrs: [{ name: 'p', s: i }], vars: { l, r: rr, p: i } }));
    qs(l, i - 1, depth + 1);
    qs(i + 1, rr, depth + 1);
  }
  return exec(r, () => {
    r.add('part', 'Quicksort: wybieramy pivot, dzielimy tablicę na mniejsze i większe od niego, a potem sortujemy obie części rekurencyjnie.', S());
    qs(0, n - 1, 0);
    a.forEach((x) => done.add(x.id));
    r.add('right', 'Posortowane! Średnio O(n log n). Uwaga: przy pivocie = ostatni element posortowane dane dają O(n²) — dlatego std::sort używa sprytniejszego wyboru (introsort).', S());
  }, { maxV: Math.max(...a.map((x) => x.v)) });
}

// ------------------------------------------------------------------------------------------- zliczanie
const COUNTING = cpp(`
void countingSort(vector<int>& a, int K) {   // wartości 0..K
    vector<int> cnt(K + 1, 0);                        @@alloc
    for (int x : a) cnt[x]++;                         @@count
    int k = 0;
    for (int v = 0; v <= K; v++)                      @@vals
        while (cnt[v] > 0) { a[k++] = v; cnt[v]--; }  @@write
}                                                     @@end
`);

function counting(vals) {
  const a = parseInts(vals.a, { name: 'Tablica', min: 0, max: 9, minLen: 2, maxLen: 16 });
  const n = a.length;
  const K = Math.max(...a);
  const cnt = Array(K + 1).fill(0);
  const out = [];
  const r = new Rec(COUNTING);
  const S = ({ ta = {}, tc = {}, to = {}, ptrs = [], vars = {} } = {}) => ({
    rows: [
      { key: 'a', label: 'a', kind: 'cells', idx: true, slots: Math.max(n, K + 1), items: a.map((v, i) => ({ id: 'a' + i, v, s: i, t: ta[i] || '' })), ptrs },
      { key: 'cnt', label: 'cnt', kind: 'cells', idx: true, slots: Math.max(n, K + 1), items: cnt.map((c, v) => ({ id: 'c' + v, v: c, s: v, t: tc[v] || (c ? '' : 'dim') })) },
      { key: 'out', label: 'wynik', kind: 'cells', idx: true, slots: Math.max(n, K + 1), items: out.map((v, k) => ({ id: 'o' + k, v, s: k, t: to[k] || 'done' })) },
    ],
    vars,
  });
  return exec(r, () => {
    r.add('alloc', `Wartości są z zakresu 0…${K}, więc tworzymy tablicę liczników cnt[0…${K}] wypełnioną zerami. Zero porównań między elementami!`, S());
    for (let i = 0; i < n; i++) {
      cnt[a[i]]++;
      r.add('count', `a[${i}] = ${a[i]} → cnt[${a[i]}]++ (teraz ${cnt[a[i]]}).`, S({ ta: { [i]: 'cmp' }, tc: { [a[i]]: 'hit' }, ptrs: [{ name: 'i', s: i }], vars: { i, x: a[i] } }));
    }
    let k = 0;
    for (let v = 0; v <= K; v++) {
      if (!cnt[v]) { r.add('vals', `cnt[${v}] = 0 — wartość ${v} nie występuje.`, S({ tc: { [v]: 'cmp' }, vars: { v, k } })); continue; }
      while (cnt[v] > 0) {
        out.push(v); cnt[v]--;
        r.add('write', `Wypisuję ${v} na pozycję ${k} (zostało cnt[${v}] = ${cnt[v]}).`, S({ tc: { [v]: 'cmp' }, to: { [k]: 'new' }, vars: { v, k } }));
        k++;
      }
    }
    r.add('end', `Posortowane w O(n + K) = O(${n} + ${K}). Działa tylko dla małych liczb całkowitych — dla wartości do 10⁹ tablica cnt byłaby za duża.`, S({ vars: { n, K } }));
  }, { maxV: Math.max(...a, 1) });
}

export const SORTING = [
  { id: 'bubble', name: 'Sortowanie bąbelkowe', cat: CAT, kb: 'stl-sort', complexity: 'O(n²)', view: 'arrays', code: BUBBLE,
    about: 'Porównuje sąsiednie pary i zamienia je, aż tablica będzie posortowana.', inputs: ARR_INPUT, random: randArr, run: bubble, legend: LEGEND_BASIC },
  { id: 'insertion', name: 'Sortowanie przez wstawianie', cat: CAT, kb: 'stl-sort', complexity: 'O(n²)', view: 'arrays', code: INSERTION,
    about: 'Wsuwa kolejne elementy w posortowany początek tablicy.', inputs: ARR_INPUT, random: randArr, run: insertion,
    legend: [['key', 'klucz x'], ['cmp', 'porównywany'], ['swap', 'przesunięty'], ['sorted', 'posortowany początek'], ['done', 'gotowe']] },
  { id: 'merge', name: 'Sortowanie przez scalanie', cat: CAT, kb: 'tech-divide-and-conquer', complexity: 'O(n log n)', view: 'arrays', code: MERGE,
    about: 'Dziel i zwyciężaj: sortuje połowy rekurencyjnie i scala je w jedną.', inputs: ARR_INPUT, random: randArr, run: merge,
    legend: [['lo', 'lewa połowa'], ['hi', 'prawa połowa'], ['cmp', 'porównywane'], ['new', 'w tmp'], ['sorted', 'posortowany fragment']] },
  { id: 'quick', name: 'Sortowanie szybkie (quicksort)', cat: CAT, kb: 'stl-sort', complexity: 'O(n log n) średnio', view: 'arrays', code: QUICK,
    about: 'Dzieli tablicę względem pivota (schemat Lomuto) i sortuje części rekurencyjnie.', inputs: ARR_INPUT, random: randArr, run: quick,
    legend: [['pivot', 'pivot'], ['cmp', 'porównywany'], ['lo', '< pivot'], ['hi', '≥ pivot'], ['done', 'na swoim miejscu']] },
  { id: 'counting', name: 'Sortowanie przez zliczanie', cat: CAT, kb: 'stl-sort', complexity: 'O(n + K)', view: 'arrays', code: COUNTING,
    about: 'Liczy wystąpienia każdej wartości i wypisuje je po kolei — bez porównań.',
    inputs: [{ key: 'a', label: 'Tablica (cyfry 0–9)', kind: 'text', def: '3 1 4 1 5 9 2 6 5 3 5', ph: 'np. 3 1 4 1 5', hint: 'do 16 liczb z zakresu 0…9' }],
    random: (r) => ({ a: r.ints(r.int(8, 14), 0, r.int(4, 9)).join(' ') }), run: counting,
    legend: [['cmp', 'bieżący element'], ['hit', 'zwiększony licznik'], ['new', 'właśnie wypisany'], ['done', 'wynik']] },
];

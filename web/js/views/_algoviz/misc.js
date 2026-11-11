// Teksty (funkcja prefiksowa KMP), matematyka (sito Eratostenesa), rekurencja (drzewo wywołań Fibonacciego i Hanoi).
import { cpp, Rec, exec, parseInt1, InputError } from './core.js';

// ------------------------------------------------------------------------------------------- KMP
const KMP = cpp(`
vector<int> prefiksowa(const string& s) {
    int n = s.size();
    vector<int> pi(n, 0);
    for (int i = 1; i < n; i++) {                       @@loop
        int k = pi[i - 1];                              @@k
        while (k > 0 && s[i] != s[k]) k = pi[k - 1];    @@fall
        if (s[i] == s[k]) k++;                          @@match
        pi[i] = k;                                      @@set
    }
    return pi;                                          @@ret
}
`);

function kmp(vals) {
  const s = String(vals.s ?? '').replace(/\s+/g, '');
  if (s.length < 2) throw new InputError('Napis musi mieć co najmniej 2 znaki.');
  if (s.length > 24) throw new InputError('Maksymalnie 24 znaki (żeby animacja była czytelna).');
  const n = s.length;
  const pi = [0];
  const r = new Rec(KMP);
  let i = 0, k = 0;
  const S = ({ ts = {}, tp = {}, vars = {}, showMatch = true } = {}) => ({
    rows: [
      { key: 's', label: 's', kind: 'chars', idx: true, slots: n, items: s.split('').map((ch, j) => ({ id: 's' + j, v: ch, s: j, t: ts[j] || (ch === '#' ? 'dim' : '') })),
        spans: showMatch && k > 0 && i > 0 ? [{ from: 0, to: k - 1, t: 'pre', pos: 'under', label: 'prefiks' }, { from: i - k, to: i - 1, t: 'suf', pos: 'over', label: 'sufiks' }] : [],
        ptrs: i > 0 ? [{ name: 'i', s: i, t: 'hi' }, { name: 'k', s: k, t: 'lo' }] : [] },
      { key: 'pi', label: 'π', kind: 'cells', slots: n, gap: true, items: pi.map((v, j) => ({ id: 'p' + j, v, s: j, t: tp[j] || '' })) },
    ],
    vars: { i, k, ...vars },
  });
  const hash = s.indexOf('#');
  return exec(r, () => {
    r.add('loop', `π[i] = długość najdłuższego właściwego prefiksu s[0…i], który jest też jego sufiksem. π[0] = 0. Kluczowy trik: przy niezgodności nie zaczynamy od zera, tylko cofamy k = π[k − 1].`, S());
    for (i = 1; i < n; i++) {
      k = pi[i - 1];
      r.add('k', `i = ${i}: zaczynamy od k = π[${i - 1}] = ${k} — tyle znaków już pasuje (prefiks s[0…${k - 1}] = sufiks kończący się na ${i - 1}).`, S({ ts: { [i]: 'key' } }));
      while (k > 0 && s[i] !== s[k]) {
        r.add('fall', `s[${i}] = '${s[i]}' ≠ s[${k}] = '${s[k]}' → nie da się przedłużyć, cofam k = π[${k - 1}] = ${pi[k - 1]}.`, S({ ts: { [i]: 'miss', [k]: 'miss' } }));
        k = pi[k - 1];
      }
      if (s[i] === s[k]) {
        r.add('match', `s[${i}] = s[${k}] = '${s[i]}' → przedłużam dopasowanie: k = ${k + 1}.`, S({ ts: { [i]: 'hit', [k]: 'hit' } }));
        k++;
      } else {
        r.add('match', `s[${i}] = '${s[i]}' ≠ s[0] = '${s[0]}' i k = 0 → π[${i}] = 0.`, S({ ts: { [i]: 'miss', [0]: 'miss' } }));
      }
      pi.push(k);
      r.add('set', `π[${i}] = ${k}.`, S({ tp: { [i]: 'new' } }));
    }
    i = 0; k = 0;
    if (hash > 0) {
      const m = hash;
      const occ = [];
      const tp = {};
      pi.forEach((v, j) => { if (j > m && v === m) { occ.push(j - 2 * m); tp[j] = 'hit'; } });
      const ts = {};
      for (const st of occ) for (let q = 0; q < m; q++) ts[m + 1 + st + q] = 'hit';
      r.add('ret', occ.length
        ? `Wzorzec „${s.slice(0, m)}” występuje w tekście na pozycjach ${occ.join(', ')} (tam, gdzie π = |wzorca| = ${m}). Całość w O(n + m).`
        : `π nigdzie nie osiąga ${m} = |wzorca| — wzorzec nie występuje w tekście.`, S({ ts, tp, showMatch: false }));
    } else {
      r.add('ret', `Gotowe: π = [${pi.join(', ')}]. Wskazówka: wpisz „wzorzec#tekst”, a miejsca z π = |wzorca| to wystąpienia wzorca. Złożoność O(n), bo k rośnie o ≤ 1 na krok.`, S({ showMatch: false }));
    }
  }, {});
}

// ------------------------------------------------------------------------------------------- sito
const SIEVE = cpp(`
vector<bool> zlozona(N + 1, false);
for (int p = 2; p * p <= N; p++) {          @@loop
    if (zlozona[p]) continue;               @@skip
    for (int k = p * p; k <= N; k += p)     @@mult
        zlozona[k] = true;                  @@mark
}
// pierwsze: p ≥ 2 z !zlozona[p]            @@done
`);

function sieve(vals) {
  const N = parseInt1(vals.n, { name: 'N', min: 10, max: 200 });
  const st = Array(N + 1).fill(0); // 0 nieznana, 1 pierwsza, 2 złożona, 3 bieżące p, 4 właśnie skreślona
  const by = Array(N + 1).fill(0);
  st[0] = st[1] = 9;
  const r = new Rec(SIEVE);
  const perMark = N <= 120;
  const S = (vars = {}) => ({ st: st.join(','), by: by.slice(), vars });
  return exec(r, () => {
    r.add('loop', `Sito Eratostenesa do N = ${N}: bierzemy kolejne liczby pierwsze p i skreślamy ich wielokrotności. Zaczynamy od p², bo mniejsze wielokrotności skreśliły już mniejsze liczby pierwsze.`, S());
    let crossed = 0;
    for (let p = 2; p * p <= N; p++) {
      if (st[p] === 2) { r.add('skip', `${p} jest skreślona (złożona) — jej wielokrotności skreśliła już liczba ${by[p]}.`, S({ p })); continue; }
      st[p] = 3;
      r.add('loop', `${p} nie jest skreślona → to liczba pierwsza. Skreślam ${p * p}, ${p * p + p}, ${p * p + 2 * p}, …`, S({ p }));
      const fresh = [];
      for (let k = p * p; k <= N; k += p) {
        const was = st[k] === 2;
        if (!was) { st[k] = 4; by[k] = p; crossed++; }
        fresh.push(k);
        if (perMark) { r.add('mark', `zlozona[${k}] = true${was ? ` (już była skreślona przez ${by[k]})` : ''}.`, S({ p, k })); if (!was) st[k] = 2; }
      }
      if (!perMark) { r.add('mark', `Skreślono wielokrotności ${p}: ${fresh.slice(0, 10).join(', ')}${fresh.length > 10 ? ', …' : ''}.`, S({ p })); fresh.forEach((k) => { if (st[k] === 4) st[k] = 2; }); }
      st[p] = 1;
    }
    let cnt = 0;
    for (let x = 2; x <= N; x++) if (st[x] === 0 || st[x] === 1) { st[x] = 1; cnt++; }
    r.add('done', `Wszystkie nieskreślone liczby są pierwsze: jest ich ${cnt} w przedziale [2, ${N}]. Złożoność O(N log log N) — sito do 10⁷ działa w ułamku sekundy.`, S({ pierwsze: cnt }));
  }, { N, cols: N <= 100 ? 10 : 15 });
}

// ------------------------------------------------------------------------------------------- Fibonacci
const FIB_PLAIN = cpp(`
long long fib(int n) {
    if (n <= 1) return n;                       @@base
    long long a = fib(n - 1);                   @@left
    long long b = fib(n - 2);                   @@right
    return a + b;                               @@ret
}
`);
const FIB_MEMO = cpp(`
long long memo[100];   // wypełnione -1
long long fib(int n) {
    if (n <= 1) return n;                       @@base
    if (memo[n] != -1) return memo[n];          @@memo
    long long a = fib(n - 1);                   @@left
    long long b = fib(n - 2);                   @@right
    return memo[n] = a + b;                     @@ret
}
`);

function fib(vals) {
  const memoOn = vals.memo === 'tak';
  const n = parseInt1(vals.n, { name: 'n', min: 1, max: memoOn ? 14 : 7 });
  const r = new Rec(memoOn ? FIB_MEMO : FIB_PLAIN);
  const nodes = [];
  const stack = [];
  const memo = {};
  let calls = 0;
  const S = (cur, vars = {}) => ({ nodes, cur, vars: { wywołania: calls, ...vars }, panels: [{ title: 'Stos wywołań', items: stack.map((id) => ({ v: nodes[id].label })) }].concat(memoOn ? [{ title: 'memo[]', items: Object.entries(memo).map(([k, v]) => ({ v: String(v), sub: 'n=' + k })) }] : []) });
  function go(k, parent) {
    const id = nodes.length;
    nodes.push({ id, p: parent, label: `fib(${k})`, val: null, st: 'active' });
    calls++; stack.push(id);
    if (k <= 1) {
      nodes[id].val = k; nodes[id].st = 'done';
      r.add('base', `fib(${k}) — przypadek bazowy, zwracam ${k}.`, S(id));
      stack.pop();
      return k;
    }
    if (memoOn && memo[k] !== undefined) {
      nodes[id].val = memo[k]; nodes[id].st = 'memo';
      r.add('memo', `fib(${k}) jest już w memo — zwracam ${memo[k]} bez liczenia całego poddrzewa!`, S(id));
      stack.pop();
      return memo[k];
    }
    r.add('left', `fib(${k}): potrzebuję fib(${k - 1}) i fib(${k - 2}). Najpierw fib(${k - 1}).`, S(id));
    const a = go(k - 1, id);
    nodes[id].st = 'active';
    r.add('right', `fib(${k}): mam fib(${k - 1}) = ${a}, teraz fib(${k - 2}).`, S(id));
    const b = go(k - 2, id);
    nodes[id].val = a + b; nodes[id].st = 'done';
    if (memoOn) memo[k] = a + b;
    r.add('ret', `fib(${k}) = ${a} + ${b} = ${a + b}${memoOn ? ' (zapisuję w memo)' : ''}.`, S(id));
    stack.pop();
    return a + b;
  }
  return exec(r, () => {
    r.add('base', memoOn
      ? `fib(${n}) ze spamiętywaniem: każda wartość liczona jest raz, potem brana z memo → O(n) wywołań.`
      : `fib(${n}) naiwnie: każde wywołanie robi dwa kolejne, więc drzewo rośnie wykładniczo (≈ 1,618ⁿ). Zobacz, ile razy liczymy to samo!`, S(-1));
    const v = go(n, -1);
    r.add('ret', `Wynik: fib(${n}) = ${v}. Liczba wywołań: ${calls}.${memoOn ? '' : ` Ze spamiętywaniem byłoby ich tylko ${2 * n - 1}.`}`, S(-1, { wynik: v }));
  }, { kind: 'fib' });
}

// ------------------------------------------------------------------------------------------- Hanoi
const HANOI = cpp(`
void hanoi(int n, char z, char na, char przez) {
    if (n == 0) return;                               @@base
    hanoi(n - 1, z, przez, na);  // odłóż n−1 na bok   @@first
    cout << z << " -> " << na << '\\n';  // krążek n   @@move
    hanoi(n - 1, przez, na, z);  // i połóż na nim    @@second
}
`);

function hanoi(vals) {
  const n = parseInt1(vals.n, { name: 'n', min: 1, max: 4 });
  const r = new Rec(HANOI);
  const nodes = [];
  const stack = [];
  const pegs = [Array.from({ length: n }, (_, i) => n - i), [], []];
  const names = ['A', 'B', 'C'];
  let moves = 0, move = null;
  const S = (cur, vars = {}) => ({ nodes, cur, pegs, move, vars: { ruchy: moves, ...vars }, panels: [{ title: 'Stos wywołań', items: stack.map((id) => ({ v: nodes[id].label })) }] });
  function go(k, z, na, przez, parent) {
    const id = nodes.length;
    nodes.push({ id, p: parent, label: `h(${k}, ${names[z]}→${names[na]})`, val: null, st: 'active' });
    stack.push(id); move = null;
    r.add('first', `hanoi(${k}, ${names[z]}, ${names[na]}, ${names[przez]}): ${k > 1 ? `najpierw przenoszę ${k - 1} mniejsze krążki na ${names[przez]}.` : 'jeden krążek — wywołania dla n = 0 nic nie robią.'}`, S(id));
    if (k > 1) { go(k - 1, z, przez, na, id); nodes[id].st = 'active'; }
    const disk = pegs[z].pop();
    pegs[na].push(disk);
    moves++; move = [z, na];
    nodes[id].st = 'cmp';
    r.add('move', `Ruch ${moves}: krążek ${disk} z ${names[z]} na ${names[na]}.`, S(id));
    move = null;
    if (k > 1) {
      r.add('second', `Teraz ${k - 1} krążki z ${names[przez]} na ${names[na]} (na krążek ${disk}).`, S(id));
      go(k - 1, przez, na, z, id);
    }
    nodes[id].st = 'done'; nodes[id].val = `${(1 << k) - 1} r.`;
    r.add('base', `hanoi(${k}, ${names[z]}→${names[na]}) zakończone (${(1 << k) - 1} ruchów).`, S(id));
    stack.pop();
  }
  return exec(r, () => {
    r.add('base', `Wieże Hanoi: ${n} krążków z A na C. Rekurencja: przenieś n−1 na B, największy na C, potem n−1 z B na C. Razem 2ⁿ − 1 = ${(1 << n) - 1} ruchów.`, S(-1));
    go(n, 0, 2, 1, -1);
    r.add('base', `Gotowe w ${moves} ruchach. Każdy poziom drzewa wywołań podwaja liczbę wywołań → O(2ⁿ).`, S(-1));
  }, { kind: 'hanoi', n });
}

export const MISC = [
  { id: 'kmp', name: 'Funkcja prefiksowa (KMP)', cat: 'Teksty', kb: 'str-kmp', complexity: 'O(n)', view: 'arrays', code: KMP,
    about: 'π[i] = najdłuższy prefiks będący też sufiksem s[0…i]. Wpisz „wzorzec#tekst”, by wyszukać wzorzec.',
    inputs: [{ key: 's', label: 'Napis (lub wzorzec#tekst)', kind: 'text', def: 'aba#abacabab', ph: 'np. abacaba' }],
    random: (r) => { const al = r.pick(['ab', 'abc', 'aab']); const mk = (k) => Array.from({ length: k }, () => r.pick(al.split(''))).join(''); return { s: r.next() < 0.6 ? `${mk(3)}#${mk(r.int(9, 14))}` : mk(r.int(10, 16)) }; },
    run: kmp, legend: [['key', 's[i]'], ['hit', 'zgodne'], ['miss', 'niezgodne'], ['pre', 'prefiks'], ['suf', 'sufiks'], ['new', 'nowe π[i]']] },
  { id: 'sieve', name: 'Sito Eratostenesa', cat: 'Matematyka', kb: 'math-sieve', complexity: 'O(N log log N)', view: 'sieve', code: SIEVE,
    about: 'Skreślanie wielokrotności kolejnych liczb pierwszych; kolor = najmniejszy dzielnik pierwszy.',
    inputs: [{ key: 'n', label: 'N (10–200)', kind: 'text', def: '100' }], random: (r) => ({ n: String(r.pick([50, 60, 80, 100, 120, 150])) }), run: sieve,
    legend: [['act', 'bieżące p'], ['new', 'właśnie skreślona'], ['done', 'liczba pierwsza'], ['dim', 'złożona']] },
  { id: 'fib', name: 'Rekurencja: Fibonacci', cat: 'Rekurencja', kb: 'dp-stairs-fibonacci', complexity: 'O(φⁿ) / O(n) z memo', view: 'calltree',
    code: (vals) => (vals && vals.memo === 'tak' ? FIB_MEMO : FIB_PLAIN),
    about: 'Drzewo wywołań fib(n) — bez spamiętywania i ze spamiętywaniem (memo).',
    inputs: [{ key: 'n', label: 'n', kind: 'text', def: '5' }, { key: 'memo', label: 'Spamiętywanie', kind: 'select', def: 'nie', options: [{ value: 'nie', label: 'bez memo' }, { value: 'tak', label: 'z memo' }] }],
    random: (r) => { const m = r.next() < 0.5 ? 'tak' : 'nie'; return { n: String(m === 'tak' ? r.int(5, 9) : r.int(4, 6)), memo: m }; }, run: fib,
    legend: [['active', 'w trakcie'], ['done', 'zwrócone'], ['memo', 'z memo'], ['cur', 'bieżące']] },
  { id: 'hanoi', name: 'Rekurencja: wieże Hanoi', cat: 'Rekurencja', kb: 'tech-divide-and-conquer', complexity: 'O(2ⁿ)', view: 'calltree', code: HANOI,
    about: 'Drzewo wywołań hanoi(n) i ruchy krążków na trzech palikach.',
    inputs: [{ key: 'n', label: 'Liczba krążków (1–4)', kind: 'text', def: '3' }], random: (r) => ({ n: String(r.int(2, 4)) }), run: hanoi,
    legend: [['active', 'w trakcie'], ['cmp', 'wykonuje ruch'], ['done', 'zakończone'], ['cur', 'bieżące']] },
];

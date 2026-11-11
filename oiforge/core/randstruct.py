"""Losowe struktury do generatorów testów (OI Forge) — drzewa, grafy, DAG-i, siatki, permutacje, ciągi, napisy,
przedziały, punkty, zapytania. Używane przez mini-język generatorów (`oiforge.core.gendsl`), dostępne dla innych.

Zasady:
- Każda funkcja przyjmuje `rng` = `Rng` | int (ziarno) | `random.Random` | None (losowe ziarno).
- Wszystkie algorytmy korzystają WYŁĄCZNIE z `Rng.below / randint / bits / random / shuffle` i nie iterują po
  zbiorach (`set` służy tylko do sprawdzania przynależności). Dzięki temu są przeniesione 1:1 do eksportu C++
  w `gendsl.CPP_RUNTIME` (MT19937 zgodny z Pythonem) — ten sam seed daje IDENTYCZNE testy w podglądzie,
  w eksporcie Pythona i w eksporcie C++. Zmieniając algorytm tutaj, zmień go też w CPP_RUNTIME (testy to wykryją).
- Wierzchołki/pozycje: parametr `base` (domyślnie 1) — numeracja od 1 albo od 0.

Publiczne API (najważniejsze):
    Rng(seed)                                        .below(n) .randint(a, b) .random() .bits(k) .shuffle(x) .choice(s)
    random_tree(n, shape="random", rng, base=1, shuffle=True, weights=None) -> [(u, v[, w])]
    tree_parents(n, shape="random", rng, shuffle=True) -> par (0-based, par[0] = -1, par[i] < i)
    random_forest(n, k, rng, base=1, weights=None) -> [(u, v[, w])]           (las: k drzew, n-k krawędzi)
    random_graph(n, m, rng, connected=True, directed=False, loops=False, multi=False, base=1, weights=None)
    random_dag(n, m, rng, connected=False, base=1, weights=None)             (krawędzie zgodne z losowym porządkiem)
    random_bipartite(n1, n2, m, rng, base=1, shift=False, weights=None)
    random_grid(rows, cols, rng, alphabet="#.", p=None, path=False) -> [str]
    grid_graph(rows, cols, base=1) -> [(u, v)]                               (graf siatki r×c, deterministyczny)
    permutation(n, rng, base=1) ; distinct_ints(n, lo, hi, rng) ; sequence(n, lo, hi, kind, rng)
    random_string(n, alphabet, rng) ; palindrome(...) ; periodic_string(...) ; thue_morse(n, alphabet="ab")
    random_intervals(k, lo, hi, rng, distinct=False) ; random_points(k, lo, hi, rng, distinct=True)
    random_queries(q, n, kinds, rng, lo=1, hi=10**9)
    expand_alphabet(spec) ; tree_shape(name) ; sequence_kind(name)
    Walidatory: components(n, edges, base), is_connected, is_tree, has_cycle(n, edges, directed, base),
                is_bipartite, is_permutation(seq, base)
"""
from __future__ import annotations

import math
import random

__all__ = [
    "StructError", "Rng", "TREE_SHAPES", "SEQ_KINDS", "QUERY_KINDS", "ALPHABETS",
    "tree_shape", "sequence_kind", "query_kind", "expand_alphabet", "weight_range",
    "random_tree", "tree_parents", "random_forest", "random_graph", "random_dag", "random_bipartite",
    "random_grid", "grid_graph", "permutation", "distinct_ints", "sequence", "random_string", "palindrome",
    "periodic_string", "thue_morse", "random_intervals", "random_points", "random_queries", "add_weights",
    "max_edges", "components", "is_connected", "is_tree", "has_cycle", "is_bipartite", "is_permutation",
]

MAX_N = 10_000_000          # bezpiecznik rozmiaru pojedynczej struktury


class StructError(ValueError):
    """Niepoprawne parametry struktury (komunikat po polsku)."""


# ============================================================================================== RNG
class Rng:
    """Generator zgodny 1:1 z eksportem C++ (MT19937 + init_by_array Pythona, getrandbits, własne below/shuffle).

    `below` NIE korzysta z `random.randrange`, tylko z `getrandbits` — ten algorytm jest stały między wersjami
    Pythona i odtworzony w C++."""

    __slots__ = ("_r", "_gb")

    def __init__(self, seed=None):
        if isinstance(seed, Rng):
            self._r = seed._r
        elif isinstance(seed, random.Random):
            self._r = seed
        else:
            if seed is None:
                seed = random.SystemRandom().getrandbits(48)
            self._r = random.Random(abs(int(seed)) % (1 << 64))
        self._gb = self._r.getrandbits

    def bits(self, k):
        return self._gb(k) if k > 0 else 0

    def below(self, n):
        """Losowa liczba z [0, n)."""
        if n <= 0:
            raise StructError("Losowanie z pustego zakresu.")
        gb = self._gb
        k = n.bit_length()
        r = gb(k)
        while r >= n:
            r = gb(k)
        return r

    def randint(self, a, b):
        """Losowa liczba całkowita z [a, b]."""
        if a > b:
            raise StructError(f"Pusty przedział [{a}, {b}].")
        return a + self.below(b - a + 1)

    def random(self):
        """Liczba z [0, 1) (53 bity, jak random.random)."""
        return self._r.random()

    def shuffle(self, x):
        """Fisher–Yates w miejscu (ta sama kolejność losowań co w C++)."""
        below = self.below
        for i in range(len(x) - 1, 0, -1):
            j = below(i + 1)
            x[i], x[j] = x[j], x[i]

    def choice(self, seq):
        if not len(seq):
            raise StructError("Losowanie z pustej listy.")
        return seq[self.below(len(seq))]


def _rng(rng) -> Rng:
    return rng if isinstance(rng, Rng) else Rng(rng)


def _check_n(n, name="n", lo=0):
    if not isinstance(n, int) or isinstance(n, bool):
        raise StructError(f"{name} musi być liczbą całkowitą (jest: {n!r}).")
    if n < lo:
        raise StructError(f"{name} = {n} — musi być co najmniej {lo}.")
    if n > MAX_N:
        raise StructError(f"{name} = {n} — za dużo (limit {MAX_N:,}).".replace(",", " "))


# ============================================================================================== nazwy / aliasy
TREE_SHAPES = ("random", "parent", "line", "star", "binary", "caterpillar", "deep")
TREE_SHAPE_NAMES = {
    "random": "losowe (kod Prüfera, jednostajnie)", "parent": "losowy rodzic (płytkie, głębokość ~log n)",
    "line": "ścieżka (głęboka rekurencja!)", "star": "gwiazda", "binary": "pełne binarne",
    "caterpillar": "gąsienica (ścieżka + liście)", "deep": "głębokie (rodzic wśród 3 poprzednich)",
}
_SHAPE_ALIASES = {
    "losowe": "random", "losowy": "random", "prufer": "random", "uniform": "random",
    "rodzic": "parent", "random_parent": "parent", "shallow": "parent", "plytkie": "parent", "płytkie": "parent",
    "path": "line", "sciezka": "line", "ścieżka": "line", "lancuch": "line", "łańcuch": "line", "chain": "line",
    "gwiazda": "star", "binarne": "binary", "bin": "binary", "heap": "binary",
    "gasienica": "caterpillar", "gąsienica": "caterpillar", "glebokie": "deep", "głębokie": "deep",
}

SEQ_KINDS = ("uniform", "sorted", "desc", "nearly", "repeats", "equal", "zigzag", "distinct")
SEQ_KIND_NAMES = {
    "uniform": "jednostajny", "sorted": "posortowany rosnąco", "desc": "posortowany malejąco",
    "nearly": "prawie posortowany (kilka zamian)", "repeats": "dużo powtórzeń (~√n różnych wartości)",
    "equal": "wszystkie równe", "zigzag": "piła: min, max, min, max…", "distinct": "różne wartości",
}
_SEQ_ALIASES = {
    "random": "uniform", "losowy": "uniform", "jednostajny": "uniform", "asc": "sorted", "rosnacy": "sorted",
    "rosnący": "sorted", "malejacy": "desc", "malejący": "desc", "reversed": "desc", "prawie": "nearly",
    "nearly_sorted": "nearly", "almost": "nearly", "powtorzenia": "repeats", "powtórzenia": "repeats",
    "few": "repeats", "rowne": "equal", "równe": "equal", "same": "equal", "pila": "zigzag", "piła": "zigzag",
    "alternating": "zigzag", "rozne": "distinct", "różne": "distinct", "unique": "distinct",
}

QUERY_KINDS = ("point", "range", "value", "point_value", "range_value", "pair", "none")
_QUERY_ALIASES = {
    "punkt": "point", "pozycja": "point", "przedzial": "range", "przedział": "range", "wartosc": "value",
    "wartość": "value", "para": "pair", "brak": "none", "pusty": "none", "empty": "none",
    "set": "point_value", "update": "point_value", "add": "range_value", "sum": "range", "query": "range",
}

ALPHABETS = {
    "lower": "abcdefghijklmnopqrstuvwxyz", "upper": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "letters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", "digits": "0123456789",
    "alnum": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "binary": "01",
    "dna": "ACGT", "hex": "0123456789abcdef",
}


def _canon(name, canon: tuple, aliases: dict, what: str) -> str:
    s = str(name).strip().lower()
    if s in canon:
        return s
    if s in aliases:
        return aliases[s]
    raise StructError(f"Nieznany {what} „{name}”. Dostępne: {', '.join(canon)}.")


def tree_shape(name) -> str:
    return _canon(name, TREE_SHAPES, _SHAPE_ALIASES, "kształt drzewa")


def sequence_kind(name) -> str:
    return _canon(name, SEQ_KINDS, _SEQ_ALIASES, "rodzaj ciągu")


def query_kind(name) -> str:
    return _canon(name, QUERY_KINDS, _QUERY_ALIASES, "rodzaj zapytania")


def _cls(c):
    if "a" <= c <= "z":
        return 1
    if "A" <= c <= "Z":
        return 2
    if "0" <= c <= "9":
        return 3
    return 0


def expand_alphabet(spec) -> str:
    """„a-z0-9” → wszystkie znaki; nazwy: lower, upper, letters, digits, alnum, binary, dna, hex.
    Powtórzone znaki zostają (dają większą wagę: „aaab” = 75% „a”)."""
    spec = str(spec)
    if spec in ALPHABETS:
        return ALPHABETS[spec]
    out = []
    i = 0
    while i < len(spec):
        c = spec[i]
        if i + 2 < len(spec) and spec[i + 1] == "-" and _cls(c) and _cls(c) == _cls(spec[i + 2]) and c <= spec[i + 2]:
            for k in range(ord(c), ord(spec[i + 2]) + 1):
                out.append(chr(k))
            i += 3
        else:
            out.append(c)
            i += 1
    if not out:
        raise StructError("Pusty alfabet.")
    return "".join(out)


def weight_range(weights):
    """None/0/False → None; liczba W > 0 → (1, W); para (lo, hi) → (lo, hi)."""
    if weights is None or weights is False or weights == 0:
        return None
    if isinstance(weights, (list, tuple)):
        if len(weights) != 2:
            raise StructError("Wagi podaj jako parę (min, max), np. weights=(1, 100).")
        lo, hi = weights
    else:
        lo, hi = 1, weights
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)):
        raise StructError("Wagi muszą być liczbami całkowitymi.")
    if lo > hi:
        raise StructError(f"Pusty przedział wag [{lo}, {hi}].")
    return lo, hi


def add_weights(edges, lo, hi, rng=None):
    """Dokleja wagę z [lo, hi] do każdej krawędzi (w kolejności listy)."""
    R = _rng(rng)
    return [tuple(e) + (R.randint(lo, hi),) for e in edges]


def _finish(R, edges, base, weights):
    wr = weight_range(weights)
    if wr is None:
        return [(u + base, v + base) for u, v in edges]
    lo, hi = wr
    return [(u + base, v + base, R.randint(lo, hi)) for u, v in edges]


def _relabel_shuffle(R, n, edges, flip):
    perm = list(range(n))
    R.shuffle(perm)
    e = [(perm[u], perm[v]) for u, v in edges]
    R.shuffle(e)
    if flip:
        e = [(v, u) if R.bits(1) else (u, v) for u, v in e]
    return e


# ============================================================================================== drzewa
def _prufer_tree(R, n):
    if n <= 1:
        return []
    if n == 2:
        return [(0, 1)]
    code = [R.below(n) for _ in range(n - 2)]
    degree = [1] * n
    for x in code:
        degree[x] += 1
    ptr = 0
    while degree[ptr] != 1:
        ptr += 1
    leaf = ptr
    edges = []
    for x in code:
        edges.append((x, leaf))
        degree[leaf] -= 1
        degree[x] -= 1
        if degree[x] == 1 and x < ptr:
            leaf = x
        else:
            ptr += 1
            while degree[ptr] != 1:
                ptr += 1
            leaf = ptr
    edges.append((n - 1, leaf))
    return edges


def _tree_raw(R, n, shape):
    """Krawędzie (rodzic, dziecko) 0-based; dla kształtów innych niż random rodzic < dziecko."""
    if shape == "random":
        return _prufer_tree(R, n)
    edges = []
    if shape == "parent":
        for i in range(1, n):
            edges.append((R.below(i), i))
    elif shape == "line":
        for i in range(1, n):
            edges.append((i - 1, i))
    elif shape == "star":
        for i in range(1, n):
            edges.append((0, i))
    elif shape == "binary":
        for i in range(1, n):
            edges.append(((i - 1) // 2, i))
    elif shape == "caterpillar":
        s = max(1, (n + 1) // 2)
        for i in range(1, n):
            edges.append((i - 1, i) if i < s else (R.below(s), i))
    elif shape == "deep":
        for i in range(1, n):
            edges.append((i - 1 - R.below(min(i, 3)), i))
    return edges


def random_tree(n, shape="random", rng=None, *, base=1, shuffle=True, weights=None):
    """Losowe drzewo o n wierzchołkach: lista n−1 krawędzi (u, v) lub (u, v, w).
    shape: random (Prüfer, jednostajnie), parent, line, star, binary, caterpillar, deep.
    shuffle=True: losowe etykiety, kolejność krawędzi i kierunek każdej krawędzi."""
    _check_n(n, "n", 1)
    shape = tree_shape(shape)
    R = _rng(rng)
    edges = _tree_raw(R, n, shape)
    if shuffle:
        edges = _relabel_shuffle(R, n, edges, True)
    return _finish(R, edges, base, weights)


def tree_parents(n, shape="random", rng=None, *, shuffle=True):
    """Drzewo ukorzenione w 0 jako tablica rodziców (0-based): par[0] = -1, par[i] < i dla i ≥ 1.
    shuffle=True: losowa numeracja zgodna z przodkami (losowy porządek topologiczny)."""
    _check_n(n, "n", 1)
    shape = tree_shape(shape)
    R = _rng(rng)
    edges = _tree_raw(R, n, shape)
    par = [-1] * n
    if shape != "random" and not shuffle:
        for p, c in edges:
            par[c] = p
        return par
    adj = [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    seen = [False] * n
    seen[0] = True
    p0 = [-1] * n
    order = []
    if shuffle:
        avail = [0]
        while avail:
            j = R.below(len(avail))
            v = avail[j]
            avail[j] = avail[-1]
            avail.pop()
            order.append(v)
            for w in adj[v]:
                if not seen[w]:
                    seen[w] = True
                    p0[w] = v
                    avail.append(w)
    else:
        order = [0]
        h = 0
        while h < len(order):
            v = order[h]
            h += 1
            for w in adj[v]:
                if not seen[w]:
                    seen[w] = True
                    p0[w] = v
                    order.append(w)
    lab = [0] * n
    for k, v in enumerate(order):
        lab[v] = k
    for v in range(n):
        if p0[v] >= 0:
            par[lab[v]] = lab[p0[v]]
    return par


def random_forest(n, k, rng=None, *, base=1, weights=None):
    """Las k losowych drzew na n wierzchołkach (n − k krawędzi, dokładnie k spójnych składowych)."""
    _check_n(n, "n", 1)
    _check_n(k, "k", 1)
    if k > n:
        raise StructError(f"Las: liczba drzew k = {k} większa niż liczba wierzchołków n = {n}.")
    R = _rng(rng)
    order = list(range(n))
    R.shuffle(order)
    cuts = sorted(distinct_ints(k - 1, 1, n - 1, R)) if k > 1 else []
    bounds = [0] + cuts + [n]
    edges = []
    for g in range(k):
        a, b = bounds[g], bounds[g + 1]
        for u, v in _prufer_tree(R, b - a):
            edges.append((order[a + u], order[a + v]))
    R.shuffle(edges)
    edges = [(v, u) if R.bits(1) else (u, v) for u, v in edges]
    return _finish(R, edges, base, weights)


# ============================================================================================== grafy
def max_edges(n, directed=False, loops=False):
    m = n * (n - 1) if directed else n * (n - 1) // 2
    return m + (n if loops else 0)


def _sample_pairs(R, n, k, used, directed, loops, edges):
    """Dobiera k nowych par (u, v) spoza `used`; gęsto — wyliczenie i częściowy Fisher–Yates, rzadko — losowanie."""
    if k <= 0:
        return
    avail = max_edges(n, directed, loops) - len(used)
    if 2 * k > avail:
        cand = []
        for u in range(n):
            for v in (range(n) if directed else range(u, n)):
                if u == v and not loops:
                    continue
                if u * n + v in used:
                    continue
                cand.append((u, v))
        for i in range(k):
            j = i + R.below(len(cand) - i)
            cand[i], cand[j] = cand[j], cand[i]
            edges.append(cand[i])
        return
    while k > 0:
        u = R.below(n)
        v = R.below(n)
        if u == v and not loops:
            continue
        key = u * n + v if directed or u <= v else v * n + u
        if key in used:
            continue
        used.add(key)
        edges.append((u, v))
        k -= 1


def random_graph(n, m, rng=None, *, connected=True, directed=False, loops=False, multi=False, base=1, weights=None):
    """Losowy graf: n wierzchołków, m krawędzi. connected=True → spójny (skierowany: słabo spójny);
    loops — pętle własne, multi — krawędzie wielokrotne. Wynik: [(u, v)] lub [(u, v, w)]."""
    _check_n(n, "n", 1)
    _check_n(m, "m", 0)
    R = _rng(rng)
    if connected and m < n - 1:
        raise StructError(f"Graf spójny o n = {n} wierzchołkach potrzebuje co najmniej n−1 = {n - 1} krawędzi (jest m = {m}).")
    mx = max_edges(n, directed, loops)
    if not multi and m > mx:
        raise StructError(f"Za dużo krawędzi: graf {'skierowany' if directed else 'nieskierowany'} o n = {n} "
                          f"wierzchołkach{' z pętlami' if loops else ''} ma ich najwyżej {mx} (jest m = {m}). "
                          "Zmniejsz m albo dodaj multi=1.")
    if multi and m > 0 and n == 1 and not loops:
        raise StructError("Graf z jednym wierzchołkiem bez pętli nie może mieć krawędzi.")
    edges = []
    used = set()
    if connected and n > 1:
        for u, v in _prufer_tree(R, n):
            if directed and R.bits(1):
                u, v = v, u
            edges.append((u, v))
            used.add(u * n + v if directed or u <= v else v * n + u)
    k = m - len(edges)
    if multi:
        for _ in range(k):
            while True:
                u = R.below(n)
                v = R.below(n)
                if u != v or loops:
                    break
            edges.append((u, v))
    else:
        _sample_pairs(R, n, k, used, directed, loops, edges)
    edges = _relabel_shuffle(R, n, edges, not directed)
    return _finish(R, edges, base, weights)


def random_dag(n, m, rng=None, *, connected=False, base=1, weights=None):
    """Losowy DAG: krawędzie u → v zgodne z losowym porządkiem topologicznym (zawsze acykliczny)."""
    _check_n(n, "n", 1)
    _check_n(m, "m", 0)
    R = _rng(rng)
    mx = n * (n - 1) // 2
    if m > mx:
        raise StructError(f"DAG o n = {n} wierzchołkach ma najwyżej n(n−1)/2 = {mx} krawędzi (jest m = {m}).")
    if connected and m < n - 1:
        raise StructError(f"Spójny DAG o n = {n} wierzchołkach potrzebuje co najmniej {n - 1} krawędzi (jest m = {m}).")
    order = list(range(n))
    R.shuffle(order)
    pe = []
    used = set()
    if connected:
        for j in range(1, n):
            i = R.below(j)
            pe.append((i, j))
            used.add(i * n + j)
    _sample_pairs(R, n, m - len(pe), used, False, False, pe)
    edges = [(order[min(i, j)], order[max(i, j)]) for i, j in pe]
    R.shuffle(edges)
    return _finish(R, edges, base, weights)


def random_bipartite(n1, n2, m, rng=None, *, base=1, shift=False, weights=None):
    """Graf dwudzielny: lewa strona 1..n1, prawa 1..n2 (shift=True → prawa n1+1..n1+n2), m krawędzi bez powtórzeń."""
    _check_n(n1, "n1", 1)
    _check_n(n2, "n2", 1)
    _check_n(m, "m", 0)
    R = _rng(rng)
    mx = n1 * n2
    if m > mx:
        raise StructError(f"Graf dwudzielny {n1}×{n2} ma najwyżej {mx} krawędzi (jest m = {m}).")
    edges = []
    if 2 * m > mx:
        cand = [(u, v) for u in range(n1) for v in range(n2)]
        for i in range(m):
            j = i + R.below(len(cand) - i)
            cand[i], cand[j] = cand[j], cand[i]
            edges.append(cand[i])
    else:
        used = set()
        while len(edges) < m:
            u = R.below(n1)
            v = R.below(n2)
            if u * n2 + v in used:
                continue
            used.add(u * n2 + v)
            edges.append((u, v))
    off = n1 if shift else 0
    wr = weight_range(weights)
    if wr is None:
        return [(u + base, v + base + off) for u, v in edges]
    return [(u + base, v + base + off, R.randint(wr[0], wr[1])) for u, v in edges]


def grid_graph(rows, cols, *, base=1):
    """Graf siatki rows×cols: wierzchołek (i, j) ma numer i·cols + j + base; krawędzie do prawego i dolnego sąsiada."""
    _check_n(rows, "rows", 1)
    _check_n(cols, "cols", 1)
    edges = []
    for i in range(rows):
        for j in range(cols):
            v = i * cols + j
            if j + 1 < cols:
                edges.append((v + base, v + 1 + base))
            if i + 1 < rows:
                edges.append((v + base, v + cols + base))
    return edges


# ============================================================================================== siatki
def random_grid(rows, cols, rng=None, *, alphabet="#.", p=None, path=False):
    """Siatka znaków: lista `rows` napisów długości `cols`.
    p (0..1): prawdopodobieństwo pierwszego znaku alfabetu (np. „#” = ściana), reszta równomiernie;
    p=None → wszystkie znaki równomiernie. path=True → wycina losową monotoniczną ścieżkę z (0,0) do (r−1,c−1)
    z OSTATNIEGO znaku alfabetu (w „#.” to „.”)."""
    _check_n(rows, "rows", 1)
    _check_n(cols, "cols", 1)
    if rows * cols > MAX_N:
        raise StructError(f"Siatka {rows}×{cols} jest za duża.")
    R = _rng(rng)
    alph = expand_alphabet(alphabet)
    L = len(alph)
    use_p = p is not None and p >= 0 and L >= 2
    g = []
    for _ in range(rows):
        row = []
        for _ in range(cols):
            if use_p:
                if R.random() < p:
                    row.append(alph[0])
                elif L == 2:
                    row.append(alph[1])
                else:
                    row.append(alph[1 + R.below(L - 1)])
            else:
                row.append(alph[R.below(L)])
        g.append(row)
    if path:
        free = alph[-1]
        i = j = 0
        g[0][0] = free
        while i != rows - 1 or j != cols - 1:
            if i == rows - 1:
                j += 1
            elif j == cols - 1:
                i += 1
            elif R.bits(1):
                i += 1
            else:
                j += 1
            g[i][j] = free
    return ["".join(r) for r in g]


# ============================================================================================== ciągi
def permutation(n, rng=None, *, base=1):
    """Losowa permutacja liczb base..base+n−1."""
    _check_n(n, "n", 0)
    R = _rng(rng)
    p = list(range(base, base + n))
    R.shuffle(p)
    return p


def distinct_ints(n, lo, hi, rng=None):
    """n RÓŻNYCH liczb z [lo, hi] w losowej kolejności."""
    _check_n(n, "n", 0)
    size = hi - lo + 1
    if n > size:
        raise StructError(f"Przedział [{lo}, {hi}] ma tylko {max(0, size)} liczb, a potrzeba {n} różnych.")
    R = _rng(rng)
    if n == 0:
        return []
    if size <= 4 * n:
        pool = list(range(lo, hi + 1))
        for i in range(n):
            j = i + R.below(size - i)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:n]
    seen = set()
    out = []
    while len(out) < n:
        x = lo + R.below(size)
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def sequence(n, lo, hi, kind="uniform", rng=None):
    """Ciąg n liczb z [lo, hi] o zadanym rozkładzie: uniform, sorted, desc, nearly, repeats, equal, zigzag, distinct."""
    _check_n(n, "n", 0)
    kind = sequence_kind(kind)
    if lo > hi:
        raise StructError(f"Pusty przedział [{lo}, {hi}].")
    R = _rng(rng)
    if kind == "distinct":
        return distinct_ints(n, lo, hi, R)
    if kind == "equal":
        if n == 0:
            return []
        v = R.randint(lo, hi)
        return [v] * n
    if kind == "repeats":
        k = max(1, math.isqrt(n))
        pool = [R.randint(lo, hi) for _ in range(k)]
        return [pool[R.below(k)] for _ in range(n)]
    a = [R.randint(lo, hi) for _ in range(n)]
    if kind == "uniform":
        return a
    a.sort()
    if kind == "desc":
        a.reverse()
    elif kind == "nearly":
        if n >= 2:
            for _ in range(max(1, n // 20)):
                i = R.below(n)
                j = R.below(n)
                a[i], a[j] = a[j], a[i]
    elif kind == "zigzag":
        out = []
        i, j = 0, n - 1
        while i <= j:
            out.append(a[i])
            if i != j:
                out.append(a[j])
            i += 1
            j -= 1
        a = out
    return a


# ============================================================================================== napisy
def random_string(n, alphabet="a-z", rng=None):
    _check_n(n, "długość", 0)
    R = _rng(rng)
    alph = expand_alphabet(alphabet)
    L = len(alph)
    return "".join([alph[R.below(L)] for _ in range(n)])


def palindrome(n, alphabet="a-z", rng=None):
    """Losowy palindrom długości n."""
    _check_n(n, "długość", 0)
    half = random_string((n + 1) // 2, alphabet, rng)
    return half + half[: n // 2][::-1]


def periodic_string(n, period, alphabet="a-z", rng=None):
    """Napis długości n o okresie `period` (losowy wzorzec powtórzony)."""
    _check_n(n, "długość", 0)
    _check_n(period, "okres", 1)
    p = min(period, n)
    if p == 0:
        return ""
    base = random_string(p, alphabet, rng)
    return (base * (n // p + 1))[:n]


def thue_morse(n, alphabet="ab"):
    """Słowo Thue–Morse’a — klasyczny antytest na haszowanie modulo 2^64 (n ≥ 2048 łamie typowy hasz)."""
    _check_n(n, "długość", 0)
    alph = expand_alphabet(alphabet)
    if len(alph) < 2:
        raise StructError("Thue–Morse potrzebuje alfabetu z co najmniej 2 znakami.")
    a, b = alph[0], alph[1]
    return "".join([b if bin(i).count("1") & 1 else a for i in range(n)])


# ============================================================================================== przedziały, punkty, zapytania
def random_intervals(k, lo, hi, rng=None, *, distinct=False):
    """k przedziałów [l, r], lo ≤ l ≤ r ≤ hi (distinct=True → l < r)."""
    _check_n(k, "k", 0)
    if lo > hi or (distinct and lo == hi):
        raise StructError(f"Za mały zakres [{lo}, {hi}] na przedziały{' z l < r' if distinct else ''}.")
    R = _rng(rng)
    out = []
    for _ in range(k):
        a = R.randint(lo, hi)
        b = R.randint(lo, hi)
        if distinct:
            while b == a:
                b = R.randint(lo, hi)
        out.append((a, b) if a <= b else (b, a))
    return out


def random_points(k, lo, hi, rng=None, *, distinct=True):
    """k punktów (x, y) o współrzędnych z [lo, hi] (domyślnie bez powtórzeń)."""
    _check_n(k, "k", 0)
    if lo > hi:
        raise StructError(f"Pusty przedział [{lo}, {hi}].")
    side = hi - lo + 1
    if distinct and k > side * side:
        raise StructError(f"Nie ma {k} różnych punktów o współrzędnych z [{lo}, {hi}].")
    R = _rng(rng)
    out = []
    seen = set()
    while len(out) < k:
        x = R.randint(lo, hi)
        y = R.randint(lo, hi)
        if distinct:
            if (x, y) in seen:
                continue
            seen.add((x, y))
        out.append((x, y))
    return out


def random_queries(q, n, kinds=("range", "point_value"), rng=None, *, lo=1, hi=10 ** 9):
    """q zapytań; każde: numer typu (1..len(kinds)) + argumenty. Rodzaje: point (i), range (l r), value (x),
    point_value (i x), range_value (l r x), pair (u v, u ≠ v), none (bez argumentów). Pozycje z 1..n."""
    _check_n(q, "q", 0)
    _check_n(n, "n", 1)
    if isinstance(kinds, str):
        kinds = kinds.replace(",", " ").split()
    kinds = [query_kind(k) for k in kinds]
    if not kinds:
        raise StructError("Podaj co najmniej jeden rodzaj zapytania.")
    if "pair" in kinds and n < 2:
        raise StructError("Zapytania „pair” wymagają n ≥ 2.")
    R = _rng(rng)
    out = []
    for _ in range(q):
        t = R.below(len(kinds))
        kd = kinds[t]
        row = [t + 1]
        if kd == "point":
            row.append(R.randint(1, n))
        elif kd == "range" or kd == "range_value":
            a = R.randint(1, n)
            b = R.randint(1, n)
            row += [a, b] if a <= b else [b, a]
            if kd == "range_value":
                row.append(R.randint(lo, hi))
        elif kd == "value":
            row.append(R.randint(lo, hi))
        elif kd == "point_value":
            row.append(R.randint(1, n))
            row.append(R.randint(lo, hi))
        elif kd == "pair":
            u = R.randint(1, n)
            v = R.randint(1, n - 1)
            if v >= u:
                v += 1
            row += [u, v]
        out.append(tuple(row))
    return out


# ============================================================================================== walidatory
def _norm_edges(n, edges, base):
    out = []
    for e in edges:
        u, v = int(e[0]) - base, int(e[1]) - base
        if not (0 <= u < n and 0 <= v < n):
            raise StructError(f"Krawędź ({e[0]}, {e[1]}) wychodzi poza zakres wierzchołków.")
        out.append((u, v))
    return out


def components(n, edges, base=1) -> int:
    """Liczba spójnych składowych (krawędzie traktowane jako nieskierowane)."""
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    c = n
    for u, v in _norm_edges(n, edges, base):
        a, b = find(u), find(v)
        if a != b:
            par[a] = b
            c -= 1
    return c


def is_connected(n, edges, base=1) -> bool:
    return n <= 1 or components(n, edges, base) == 1


def is_tree(n, edges, base=1) -> bool:
    return len(edges) == n - 1 and is_connected(n, edges, base)


def has_cycle(n, edges, directed=False, base=1) -> bool:
    es = _norm_edges(n, edges, base)
    if not directed:
        return len(es) > n - components(n, edges, base)
    indeg = [0] * n
    adj = [[] for _ in range(n)]
    for u, v in es:
        adj[u].append(v)
        indeg[v] += 1
    st = [v for v in range(n) if indeg[v] == 0]
    seen = 0
    while st:
        v = st.pop()
        seen += 1
        for w in adj[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                st.append(w)
    return seen != n


def is_bipartite(n, edges, base=1) -> bool:
    adj = [[] for _ in range(n)]
    for u, v in _norm_edges(n, edges, base):
        adj[u].append(v)
        adj[v].append(u)
    col = [-1] * n
    for s in range(n):
        if col[s] >= 0:
            continue
        col[s] = 0
        st = [s]
        while st:
            v = st.pop()
            for w in adj[v]:
                if col[w] < 0:
                    col[w] = col[v] ^ 1
                    st.append(w)
                elif col[w] == col[v]:
                    return False
    return True


def is_permutation(seq, base=1) -> bool:
    return sorted(seq) == list(range(base, base + len(seq)))

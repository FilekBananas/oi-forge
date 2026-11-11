"""Rozpoznawanie algorytmów i technik w kodzie C++ (bez AI): ważone wzorce na tokenach i strukturze programu.

API (stała sygnatura, CONTRACT 3.5):
    detect(code: str) -> list[{"id", "name", "confidence", "lines", "evidence", "variant"?}]

Każdy detektor zbiera sygnały (waga 0..1, linia, opis). Pewność = „noisy-OR” sygnałów (1 − Π(1 − w)), z bramkami
(np. Dijkstra wymaga kopca/zbioru ORAZ relaksacji) i karami. Wzorce są strukturalne — działają niezależnie
od nazw zmiennych (`odw`, `kolejka`, `znajdz`, `drzewo` …), nazwy dają tylko słabe podpowiedzi.
Wagi dobrane na korpusie prawdziwych programów olimpijskich (raport: `python3 -m oiforge.core.algodetect --eval`).
"""
from __future__ import annotations

import bisect
import re
import sys

try:
    from oiforge.tools._cppstruct import Code, normalize_expr
except Exception:  # noqa: BLE001
    Code = None
    normalize_expr = None

NAMES = {
    "binary_search": "Wyszukiwanie binarne", "two_pointers": "Dwa wskaźniki (gąsienica)",
    "prefix_sums": "Sumy prefiksowe", "sorting": "Sortowanie", "greedy": "Algorytm zachłanny",
    "dp": "Programowanie dynamiczne", "bitmask_dp": "DP na maskach bitowych", "bfs": "BFS (przeszukiwanie wszerz)",
    "dfs": "DFS (przeszukiwanie w głąb)", "dijkstra": "Algorytm Dijkstry", "bellman_ford": "Algorytm Bellmana-Forda",
    "floyd": "Algorytm Floyda-Warshalla", "topo_sort": "Sortowanie topologiczne", "dsu": "Find & Union (DSU)",
    "mst": "Minimalne drzewo rozpinające", "lca": "LCA (najniższy wspólny przodek)",
    "segment_tree": "Drzewo przedziałowe", "lazy_segment_tree": "Drzewo przedziałowe z propagacją (lazy)",
    "fenwick": "Drzewo Fenwicka (BIT)", "sparse_table": "Sparse table", "kmp": "KMP (funkcja prefiksowa)",
    "z_function": "Funkcja Z", "hashing": "Haszowanie napisów", "trie": "Drzewo trie", "manacher": "Algorytm Manachera",
    "sieve": "Sito Eratostenesa", "fast_pow": "Szybkie potęgowanie", "mod_inverse": "Odwrotność modularna",
    "gcd": "NWD (algorytm Euklidesa)", "matrix_pow": "Potęgowanie macierzy", "convex_hull": "Otoczka wypukła",
    "geometry": "Geometria obliczeniowa", "monotonic_stack": "Stos monotoniczny", "sliding_window": "Okno przesuwne",
    "recursion": "Rekurencja", "backtracking": "Przeszukiwanie z nawrotami (backtracking)", "bitset": "Bitset",
    "scc": "Silnie spójne składowe", "bridges": "Mosty i punkty artykulacji", "bipartite": "Graf dwudzielny",
    "flow": "Maksymalny przepływ", "matching": "Skojarzenia (graf dwudzielny)", "mo": "Algorytm Mo",
    "sqrt_decomp": "Dekompozycja pierwiastkowa", "coordinate_compression": "Skalowanie (kompresja współrzędnych)",
    "meet_in_middle": "Meet in the middle", "simulation": "Symulacja", "math": "Matematyka / teoria liczb",
    "combinatorics": "Kombinatoryka", "string_processing": "Przetwarzanie napisów",
    "graph": "Graf (lista/macierz sąsiedztwa)", "tree": "Drzewo", "grid": "Plansza (siatka 2D)",
}
IDS = list(NAMES)
THRESHOLD = 0.35

# słabe podpowiedzi z nazw (identyfikatory małymi literami, bez podkreśleń na końcach)
NAME_HINTS = {
    "dijkstra": r"dijkstr|^dist|^odl|^odleg|^najkr",
    "bfs": r"^bfs|^kolejk|^que",
    "dfs": r"^dfs|^odwiedz|^vis|^used|^odw$",
    "dsu": r"^find$|^znajdz|^fin$|^root$|^lider|^parent$|^rodzic|^unite|^union|^polacz|^dsu|^fu$|^repr",
    "segment_tree": r"^seg|^drzew|^segtree|^tree$|^st$",
    "fenwick": r"^fen|^bit$|^fw$|^ft$|^fenwick",
    "sieve": r"^sit|^sieve|^prime|^pierw|^is_?prime|^zlozon|^czy_?pierw",
    "kmp": r"^kmp|^lps$|^fail|^pi$|^prefix_?fun",
    "topo_sort": r"^topo|^wchodz|^indeg|^stopien|^deg$|^in_?deg|^kolejnosc",
    "binary_search": r"^bin|^serch|^search|^szukaj|^wyszuk|^spr$|^check$|^ok$|^mid$|^srodek|^sr$",
    "fast_pow": r"^pot$|^potega|^power|^binpow|^fastpow|^modpow|^qpow|^fpow|^szybk",
    "gcd": r"^nwd|^gcd|^nww|^lcm|^euklid",
    "dp": r"^dp$|^memo|^pamiec|^dyn",
    "mst": r"^mst|^kruskal|^prim$|^rozpin",
    "lca": r"^lca|^przodek|^anc|^jump|^skok|^up$",
    "geometry": r"^punkt|^point|^wektor|^cross|^iloczyn|^trojkat|^odleglosc|^pole$",
    "convex_hull": r"^hull|^otoczk|^graham|^andrew",
    "trie": r"^trie|^wezel|^node$",
    "hashing": r"^hash|^hasz|^h$",
    "bipartite": r"^kolor|^color|^col$|^dwudziel|^bipart",
    "scc": r"^scc|^silnie|^spojn|^low$|^comp$",
    "matching": r"^match|^skoj|^mt$",
    "flow": r"^flow|^przeplyw|^dinic|^cap$|^maxflow",
    "tree": r"^drzew|^rodzic|^syn$|^dzieci|^poddrzew|^lisc|^subtree|^sz$|^glebok|^depth",
    "grid": r"^plansz|^mapa$|^grid|^siatk|^dx$|^dy$|^pole$",
    "combinatorics": r"^silni|^fact|^newton|^dwumian|^ncr$|^binom",
    "sqrt_decomp": r"^blok|^block|^kubel|^bucket",
    "coordinate_compression": r"^skal|^kompres|^compress|^ranga|^rank$",
    "string_processing": r"^napis|^slowo|^tekst|^str$|^word",
    "greedy": r"^zachlan|^greedy",
    "simulation": r"^symul|^krok|^ruch",
}


# ============================================================================================== kontekst
_RX_CACHE: dict = {}
_PROFILE: dict | None = None


def _rx(rx: str, flags: int = 0):
    key = (rx, flags)
    p = _RX_CACHE.get(key)
    if p is None:
        if len(_RX_CACHE) > 4000:
            _RX_CACHE.clear()
        try:
            p = re.compile(rx, flags)
        except re.error:
            p = False
        _RX_CACHE[key] = p
    return p or None


class Ctx:
    def __init__(self, code: str):
        self.raw = code or ""
        self.c = Code(self.raw)
        c = self.c
        parts, offs = [], []
        pos = 0
        prev_word = False
        for t in c.toks:
            w = t.text if t.kind != "str" else '""'
            if len(w) > 40 and t.kind in ("id", "num", "op", "char"):
                w = w[:40]          # ochrona przed kwadratowym backtrackingiem regexów na absurdalnych tokenach
            is_word = bool(w) and (w[0].isalnum() or w[0] == "_")
            if is_word and prev_word:
                parts.append(" ")
                pos += 1
            offs.append(pos)
            parts.append(w)
            pos += len(w)
            prev_word = is_word and (w[-1].isalnum() or w[-1] == "_")
        self.flat = "".join(parts)
        self.offs = offs
        self.ids = {}
        for t in c.toks:
            if t.kind == "id":
                k = t.text.lower().strip("_")
                self.ids[k] = self.ids.get(k, 0) + 1
        self.user_funcs = {f.name for f in c.functions}
        self.recursive = [f for f in c.functions if f.self_calls]
        self.comment_text = " ".join(t.text for t in c.comments).lower()

    # --- linie
    def line_at(self, off: int) -> int:
        k = bisect.bisect_right(self.offs, off) - 1
        if 0 <= k < len(self.c.toks):
            return self.c.toks[k].line
        return 0

    def find(self, rx: str, flags=0) -> list:
        """Lista (match, linia) dla regexu na spłaszczonym kodzie."""
        pat = _rx(rx, flags)
        if pat is None:
            return []
        if _PROFILE is not None:
            import time as _t
            t0 = _t.perf_counter()
            r = [(m, self.line_at(m.start())) for m in pat.finditer(self.flat)]
            _PROFILE[rx] = _PROFILE.get(rx, 0.0) + _t.perf_counter() - t0
            return r
        return [(m, self.line_at(m.start())) for m in pat.finditer(self.flat)]

    def first(self, rx: str) -> int | None:
        r = self.find(rx)
        return r[0][1] if r else None

    def name_hint(self, aid: str) -> list:
        rx = NAME_HINTS.get(aid)
        if not rx:
            return []
        return [k for k in self.ids if re.search(rx, k)]

    def kind(self, name: str) -> str | None:
        d = self.c.decl_of(name)
        return d.kind if d else None

    def decls(self, *kinds) -> list:
        return [d for d in self.c.decls if d.kind in kinds]

    def calls(self, *names) -> list:
        return [cl for cl in self.c.calls if cl.name in names]


class Det:
    """Zbieracz sygnałów dla jednego algorytmu."""

    def __init__(self, aid: str):
        self.id = aid
        self.sig: list[tuple] = []
        self.variant: str | None = None
        self.penalty = 1.0
        self.cap = 0.99

    def add(self, w: float, line, ev: str):
        if line is None:
            line = 0
        self.sig.append((w, line, ev))
        return self

    def has(self, ev_prefix: str) -> bool:
        return any(e.startswith(ev_prefix) for _, _, e in self.sig)

    def conf(self) -> float:
        p = 1.0
        for w, _, _ in self.sig:
            p *= (1 - max(0.0, min(0.98, w)))
        return min(self.cap, (1 - p) * self.penalty)


def _nz(x):
    return x if x else 0


# ============================================================================================== wzorce wspólne
def _relaxations(x: Ctx) -> list[dict]:
    """Relaksacje: if (D[v] > E + w) D[v] = E + w;  albo  D[v] = min(D[v], E + w)."""
    c = x.c
    res = []
    for a in c.assigns:
        if a.op != "=" or not a.rhs or not a.indexed:
            continue
        lhs = normalize_expr(a.target_text)
        rhs = normalize_expr(c.text(*a.rhs))
        m = re.fullmatch(r"min\((.+)\)", rhs)
        if m:
            args = _split_args(m.group(1))
            if len(args) == 2 and lhs in args:
                other = args[1] if args[0] == lhs else args[0]
                res.append({"line": a.line, "target": a.target, "lhs": lhs, "rhs": other, "kind": "min",
                            "plus": _top_plus(other) or ("+" in other and "max(" in other), "idx": a.idx, "unit": bool(re.search(r"\+1$", other)),
                            "self": a.target + "[" in other})
            continue
        # if (lhs > rhs) lhs = rhs  /  if (rhs < lhs)
        j = a.idx - 1
        depth = 0
        while j > 0 and j > a.idx - 200:
            t = c.texts[j]
            if t == ")" and j in c.pairs:
                o = c.pairs[j]
                if o > 0 and c.texts[o - 1] == "if":
                    cond = normalize_expr(c.text(o + 1, j - 1))
                    for part in re.split(r"&&", cond):
                        mm = re.fullmatch(r"(.+?)(>|>=)(.+)", part) or None
                        mm2 = re.fullmatch(r"(.+?)(<|<=)(.+)", part) or None
                        cand = None
                        if mm and mm.group(1) == lhs:
                            cand = mm.group(3)
                        elif mm2 and mm2.group(3) == lhs:
                            cand = mm2.group(1)
                        if cand is not None and (cand == rhs or _top_plus(cand)):
                            res.append({"line": a.line, "target": a.target, "lhs": lhs, "rhs": rhs, "kind": "if",
                                        "plus": _top_plus(rhs) or ("+" in rhs and "max(" in rhs), "idx": a.idx, "unit": bool(re.search(r"\+1$", rhs)),
                                        "self": a.target + "[" in rhs})
                            break
                    break
                j = o - 1
                continue
            if t in (";",) and depth == 0:
                # pozwól na blok { ... } z kilkoma instrukcjami
                pass
            if t == "{" and j in c.pairs and c.pairs[j] < a.idx:
                pass
            j -= 1
    return res


def _split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _top_plus(s: str) -> bool:
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "+" and depth == 0 and i > 0 and s[i - 1] != "+" and (i + 1 < len(s) and s[i + 1] != "+"):
            return True
    return False


def _self_refs(x: Ctx) -> list[dict]:
    """Przypisania X[...] = f(X[...inny indeks...]) — przejścia DP (i podobne)."""
    c = x.c
    res = []
    for a in c.assigns:
        if not a.indexed or not a.rhs or a.op not in ("=", "+=", "-=", "*=", "%=", "|=", "^=", "&="):
            continue
        rhs = normalize_expr(c.text(*a.rhs))
        lhs = normalize_expr(a.target_text)
        base = a.target
        if not re.search(rf"(?<![\w.]){re.escape(base)}\[", rhs):
            continue
        refs = re.findall(rf"(?<![\w.]){re.escape(base)}((?:\[[^\[\]]*(?:\[[^\[\]]*\])?[^\[\]]*\])+)", rhs)
        lidx = lhs[len(base):]
        other = [r for r in refs if r != lidx]
        if not other and a.op == "=":
            continue
        res.append({"line": a.line, "target": base, "lhs": lhs, "rhs": rhs, "refs": refs, "op": a.op,
                    "idx": a.idx, "loop": c.innermost_loop(a.idx) is not None,
                    "minmax": bool(re.match(r"(max|min)\(", rhs)) or "max(" in rhs or "min(" in rhs,
                    "dims": len(a.index_texts)})
    return res


def _mid_rx(p: str, q: str) -> str:
    ep, eq = re.escape(p), re.escape(q)
    return (rf"\(?\(?{ep}\+{eq}([+-]1)?\)?\)?(/2|>>1|/2\.0|\*0\.5|/2LL)\)?([+-]1)?|"
            rf"{ep}\+\(\(?{eq}-{ep}([+-]1)?\)?\)?(/2|>>1)([+-]1)?|{ep}\+\({eq}-{ep}\)/2([+-]1)?")


def _mid_loops(x: Ctx) -> list[tuple]:
    """Pętle z mid = (l + r) / 2 i zawężaniem l/r: [(loop, lo, hi, ternary, floating, mid, line)]."""
    c = x.c
    res = []
    for k, L in enumerate(c.loops):
        if L.kind not in ("while", "do", "for") or not L.cond:
            continue
        ids = list(dict.fromkeys(c.ids_in(*L.cond)))
        if len(ids) < 2:
            # warunek nie mówi o l/r (np. interaktywne „zgadywanie”) — weź zmienne z przypisań mid w ciele
            ids = list(dict.fromkeys(ids + [a.target for a in c.assigns_in(L.body[0], L.body[1])
                                            if a.op == "=" and not a.indexed]))
        for a in c.assigns_in(L.body[0], L.body[1]):
            if a.op != "=" or not a.rhs or a.indexed:
                continue
            r = normalize_expr(c.text(*a.rhs))
            hit = None
            for p in ids:
                for q in ids:
                    if p == q:
                        continue
                    ep, eq = re.escape(p), re.escape(q)
                    if re.fullmatch(_mid_rx(p, q), r):
                        hit = (p, q, False)
                    elif re.fullmatch(rf"{ep}\+\({eq}-{ep}\)/3|\(2\*{ep}\+{eq}\)/3|{eq}-\({eq}-{ep}\)/3|"
                                      rf"\({ep}\*2\+{eq}\)/3", r):
                        hit = (p, q, True)
            if hit:
                mid = a.target
                assigned = {z.target for z in c.assigns_in(L.body[0], L.body[1]) if z.rhs and z.op == "=" and
                            re.fullmatch(rf"{re.escape(mid)}([+-]1)?|{re.escape(mid)}([+-]1e-?\d+)?",
                                         normalize_expr(c.text(*z.rhs)))}
                if assigned & {hit[0], hit[1]} or hit[2]:
                    res.append((L, hit[0], hit[1], hit[2], "." in r or "2.0" in r, mid, a.line))
                    break
    return res


def _queue_loops(x: Ctx) -> list[dict]:
    """Pętle przetwarzające kolejkę/stos/kopiec: while(!q.empty()) / while(q.size()) / vector + wskaźnik."""
    c = x.c
    res = []
    for k, L in enumerate(c.loops):
        if L.kind not in ("while", "for", "do") or not L.cond:
            continue
        ct = normalize_expr(c.text(*L.cond))
        m = re.search(r"!(\w+)\.empty\(\)|(\w+)\.size\(\)(>0|!=0)?(?![<>=]\w)|(\w+)\.empty\(\)==(false|0)", ct)
        name = None
        vecq = False
        if m:
            name = m.group(1) or m.group(2) or m.group(4)
        m2 = re.search(r"(\w+)\.size\(\)(>|!=|==)(\w+)(\+1)?|(\w+)<(\w+)\.size\(\)", ct)
        if m2:
            qn = m2.group(1) or m2.group(6)
            ptr = m2.group(3) or m2.group(5)
            if qn and ptr and re.search(rf"{re.escape(qn)}\[{re.escape(ptr)}\]", normalize_expr(c.text(*L.body))) and \
                    re.search(rf"{re.escape(qn)}\.(push_back|emplace_back)\(", normalize_expr(c.text(*L.body))):
                name, vecq = qn, True
        if not name:
            continue
        if "&&" in ct or "||" in ct:
            continue          # stos/kolejka monotoniczna, nie przegląd
        kind = x.kind(name)
        if kind not in ("queue", "pq", "deque", "stack", "vector", "set", "multiset") or (kind == "vector" and not vecq
                                                                                          and not re.search(
                    rf"{re.escape(name)}\.(pop_back|back)\(", normalize_expr(c.text(*L.body)))):
            continue
        body = normalize_expr(c.text(*L.body))
        res.append({"loop": L, "k": k, "name": name, "kind": "vector_queue" if vecq else kind, "body": body,
                    "line": L.line, "front": bool(re.search(rf"{re.escape(name)}\.(front|top)\(\)|"
                                                            rf"{re.escape(name)}\[\w+\]|"
                                                            rf"{re.escape(name)}\.(begin)\(\)", body)),
                    "adj": [ch for ch in L.children if _adj_of(x, c.loops[ch])]})
    return res


def _adj_of(x: Ctx, L) -> str | None:
    c = x.c
    if L.kind == "range":
        a, b = L.range_expr
    elif L.cond:
        a, b = L.cond
    else:
        return None
    T = c.texts
    for i in range(a, b + 1):
        if c.toks[i].kind == "id" and i + 1 <= b and T[i + 1] == "[" and (i + 1) in c.pairs:
            j = c.pairs[i + 1]
            if (L.kind == "range" and j == b) or (j + 2 < c.n and T[j + 1] in (".", "->") and T[j + 2] in ("size",)):
                d = c.decl_of(T[i])
                if d and (d.nested >= 2 or (d.kind == "array" and re.search(r"vector|set|deque|list", d.type))):
                    return T[i]
    return None


def _adj_lists(x: Ctx) -> list[dict]:
    """Budowa listy sąsiedztwa: g[a].push_back(b) (wewnątrz pętli czytającej)."""
    c = x.c
    res = []
    for cl in c.calls:
        if cl.name not in ("push_back", "emplace_back", "push", "insert", "emplace") or not cl.receiver:
            continue
        if cl.receiver_text == cl.receiver or "[" not in cl.receiver_text:
            continue
        d = c.decl_of(cl.receiver)
        if d is None or not (d.nested >= 2 or (d.kind == "array" and re.search(r"vector|set|deque|list", d.type))):
            continue
        k = c.innermost_loop(cl.idx)
        reads = False
        bound = ""
        if k is not None:
            L = c.loops[k]
            reads = any(io.kind == "read" and L.body[0] <= io.idx <= L.body[1] for io in c.io)
            bound = normalize_expr(c.text(*L.cond)) if L.cond else ""
            init = normalize_expr(c.text(*L.init)) if L.init else ""
        else:
            init = ""
        res.append({"name": cl.receiver, "line": cl.line, "reads": reads, "bound": bound, "init": init,
                    "arg": normalize_expr(c.text(*cl.args[0])) if cl.args else "", "call": cl})
    return res


# ============================================================================================== detektory
def d_binary_search(x: Ctx, D: Det):
    c = x.c
    ml = _mid_loops(x)
    for L, lo, hi, ternary, floating, mid, line in ml:
        if ternary:
            D.add(0.8, L.line, f"wyszukiwanie ternarne: dzielenie przedziału [{lo}, {hi}] na trzy części")
            D.variant = "ternarne"
        else:
            D.add(0.82, L.line, f"pętla zawężająca przedział [{lo}, {hi}] przez środek {mid}")
            if floating:
                D.variant = "na liczbach rzeczywistych"
        # predykat sprawdzający środek (binsearch po wyniku)
        body = normalize_expr(c.text(*L.body))
        m = re.search(rf"if\(!?(\w+)\([^)]*\b{re.escape(mid)}\b[^)]*\)\)", body)
        if m and m.group(1) in x.user_funcs:
            D.add(0.2, L.line, f"sprawdzanie odpowiedzi funkcją {m.group(1)}({mid}) — wyszukiwanie binarne po wyniku")
            D.variant = D.variant or "po wyniku"
    # pętla for po stałej liczbie kroków z mid (binsearch na doublach)
    for cl in x.calls("lower_bound", "upper_bound", "binary_search", "equal_range", "partition_point"):
        if cl.is_method:
            D.add(0.25, cl.line, f"{cl.receiver}.{cl.name} — wyszukiwanie w zbiorze/mapie")
        else:
            D.add(0.6, cl.line, f"std::{cl.name} na posortowanym zakresie")
            D.variant = D.variant or "lower_bound"
    # skoki potęgami dwójki: for (k = LOG; k >= 0; k--) if (pos + (1 << k) <= n && ...) pos += 1 << k
    for m, ln in x.find(r"(\w+)\+=\(?1(LL)?<<\w+\)?;"):
        D.add(0.35, ln, "wyszukiwanie binarne skokami potęg dwójki")
    if ml and x.name_hint("binary_search"):
        D.add(0.1, None, "nazwy zmiennych/funkcji typowe dla binsearcha")


def d_two_pointers(x: Ctx, D: Det):
    c = x.c
    mids = {id(t[0]) for t in _mid_loops(x)}
    qloops = {id(q["loop"]) for q in _queue_loops(x)}
    for k, L in enumerate(c.loops):
        if id(L) in mids or id(L) in qloops:
            continue
        if L.parent is not None:
            P = c.loops[L.parent]
            if id(P) in mids:
                continue
            if L.kind in ("while", "do") or (L.kind == "for" and (not L.init or L.init[0] > L.init[1])):
                ct = normalize_expr(c.text(*L.cond)) if L.cond else ""
                if re.search(r"\.empty\(\)|\.size\(\)&&|\.top\(\)|\.back\(\)", ct):
                    continue
                for a in c.assigns_in(L.body[0], L.body[1]) + (c.assigns_in(*L.step) if L.step else []):
                    if a.indexed or a.target_text != a.target or a.op not in ("++", "--", "+=", "-="):
                        continue
                    if c.innermost_loop(a.idx) != k:
                        continue
                    p = a.target
                    if p == P.var or (a.op in ("+=", "-=") and not (a.rhs and re.fullmatch(
                            r"\d+", normalize_expr(c.text(*a.rhs))))):
                        continue
                    resets = [z for z in c.assigns_in(P.body[0], P.body[1], p) if z.op == "=" and
                              z.target_text == p and not (L.idx <= z.idx <= L.body[1])]
                    inside = any(d.name == p and P.body[0] <= d.idx <= P.body[1] for d in c.decls)
                    used_as_index = bool(re.search(rf"\w+\[{re.escape(p)}([+-]\d)?\]", normalize_expr(
                        c.text(L.idx, L.body[1]))))
                    if not resets and not inside and used_as_index:
                        D.add(0.72, L.line, f"wskaźnik {p} przesuwa się tylko do przodu i nie jest cofany w pętli "
                                            f"zewnętrznej — dwa wskaźniki")
                        break
        if L.kind == "while" and L.cond and L.parent is None or L.kind == "while":
            ids = list(dict.fromkeys(c.ids_in(*L.cond))) if L.cond else []
            if len(ids) < 2:
                continue
            ups, downs = set(), set()
            for a in c.assigns_in(L.body[0], L.body[1]):
                if a.target_text != a.target or c.innermost_loop(a.idx) != k:
                    continue
                if a.op == "++" or (a.op == "+=" and a.rhs and re.fullmatch(r"\d+", normalize_expr(c.text(*a.rhs)))):
                    ups.add(a.target)
                elif a.op == "--" or (a.op == "-=" and a.rhs and re.fullmatch(r"\d+", normalize_expr(c.text(*a.rhs)))):
                    downs.add(a.target)
            cond_ids = set(ids)
            divided = {a.target for a in c.assigns_in(L.body[0], L.body[1]) if a.op in ("/=", ">>=", "*=", "<<=")}
            if divided & (ups | downs):
                continue
            # gąsienica w jednej pętli: dwa wskaźniki rosną w różnych gałęziach i indeksują tę samą tablicę
            body = normalize_expr(c.text(*L.body))
            if len(ups) >= 2 and not (len(ups & cond_ids) >= 2):
                arrs = {}
                for v in ups:
                    for mm in re.finditer(rf"(\w+)\[{re.escape(v)}\]", body):
                        arrs.setdefault(mm.group(1), set()).add(v)
                if any(len(vs) >= 2 for vs in arrs.values()) and "else" in c.texts[L.body[0]:L.body[1] + 1]:
                    D.add(0.66, L.line, f"dwa wskaźniki ({', '.join(sorted(ups))}) na tej samej tablicy — "
                                        f"w każdym kroku przesuwa się jeden z nich")
                    continue
            if (ups & cond_ids) and (downs & cond_ids) and (ups & cond_ids) != (downs & cond_ids):
                D.add(0.65, L.line, f"wskaźniki z dwóch końców ({', '.join(sorted(ups & cond_ids))} rośnie, "
                                    f"{', '.join(sorted(downs & cond_ids))} maleje)")
            elif len(ups & cond_ids) >= 2:
                inc_blocks = set()
                for a in c.assigns_in(L.body[0], L.body[1]):
                    if a.target in ups & cond_ids and a.op in ("++", "+="):
                        j = a.idx
                        depth = 0
                        while j > L.body[0]:
                            if c.texts[j] == "}" and j in c.pairs:
                                j = c.pairs[j] - 1
                                continue
                            if c.texts[j] == "{":
                                break
                            j -= 1
                        inc_blocks.add(j)
                if len(inc_blocks) >= 2 and all(re.search(rf"\w+\[{re.escape(v)}\]", body) for v in ups & cond_ids):
                    D.add(0.5, L.line, f"dwa indeksy ({', '.join(sorted(ups & cond_ids))}) przesuwane naprzemiennie "
                                       f"(np. scalanie)")
    if D.sig and x.name_hint("two_pointers"):
        D.add(0.05, None, "nazwy wskaźników")


def d_sliding_window(x: Ctx, D: Det):
    c = x.c
    for m, ln in x.find(r"(\w+)(\+=|-=)\w+\[\w+\]-\w+\[\w+-\w+\]"):
        D.add(0.6, ln, "suma okna aktualizowana o nowy i usuwany element")
    for m, ln in x.find(r"(\w+)-=\w+\[\w+-\w+(\+1)?\]"):
        D.add(0.4, ln, "usuwanie elementu wypadającego z okna")
    for m, ln in x.find(r"(\w+)\.front\(\)(<=|<)\w+-\w+|(\w+)\.front\(\)\+\w+(<=|<)\w+"):
        D.add(0.55, ln, "kolejka monotoniczna: usuwanie indeksów spoza okna")
    # gąsienica z agregatem: S += a[r] i S -= a[l]
    adds = {(m.group(1), m.group(2)) for m, _ in x.find(r"(\w+)\+=\w+\[(\w+)\]")}
    subs = {(m.group(1), m.group(2)) for m, _ in x.find(r"(\w+)-=\w+\[(\w+)\]")}
    for s_, i1 in adds:
        for s2, i2 in subs:
            if s_ == s2 and i1 != i2:
                D.add(0.45, x.first(rf"{re.escape(s_)}-=\w+\[{re.escape(i2)}\]"),
                      f"agregat okna {s_}: dodawanie a[{i1}], usuwanie a[{i2}]")
                break


def d_prefix_sums(x: Ctx, D: Det):
    c = x.c
    for a in c.assigns:
        if not a.indexed or not a.rhs:
            continue
        lhs = normalize_expr(a.target_text)
        rhs = normalize_expr(c.text(*a.rhs))
        b = re.escape(a.target)
        if len(a.index_texts) == 1:
            i = a.index_texts[0].replace(" ", "")
            ei = re.escape(i)
            prev = None
            m = re.fullmatch(r"(\w+)\+1", i)
            if a.op == "=" and (re.fullmatch(rf"{b}\[{ei}-1\]\+.+|.+\+{b}\[{ei}-1\]", rhs) or
                                (m and re.fullmatch(rf"{b}\[{re.escape(m.group(1))}\]\+.+|.+\+{b}\[{re.escape(m.group(1))}\]",
                                                    rhs))):
                other = re.sub(rf"{b}\[[^\]]*\]", "", rhs)
                if a.target + "[" not in other and not re.search(r"max\(|min\(|%", rhs):
                    D.add(0.75, a.line, f"{a.target}[{i}] = {a.target}[poprzedni] + … — sumy prefiksowe")
                    continue
            if a.op == "+=" and re.fullmatch(rf"{b}\[{ei}-1\]", rhs):
                D.add(0.75, a.line, f"{a.target}[{i}] += {a.target}[{i}-1] — sumy prefiksowe w miejscu")
                continue
            if a.op == "=" and re.fullmatch(rf"(max|min)\({b}\[{ei}-1\],.+\)|(max|min)\(.+,{b}\[{ei}-1\]\)", rhs):
                D.add(0.45, a.line, "prefiksowe maksimum/minimum")
                D.variant = "prefiksowe maksimum"
        elif len(a.index_texts) == 2 and a.op == "=":
            if re.search(rf"{b}\[\w+[-+]1\]\[\w+\]", rhs) and re.search(rf"{b}\[\w+\]\[\w+[-+]1\]", rhs) and \
                    re.search(rf"-{b}\[\w+[-+]1\]\[\w+[-+]1\]", rhs):
                D.add(0.8, a.line, "dwuwymiarowe sumy prefiksowe (wzór włączeń i wyłączeń)")
                D.variant = "2D"
    for cl in x.calls("push_back", "emplace_back"):
        if cl.args:
            t = normalize_expr(c.text(*cl.args[0]))
            r = re.escape(cl.receiver or "~")
            if re.fullmatch(rf"{r}\.back\(\)\+.+|.+\+{r}\.back\(\)|{r}\[\w+(-1)?\]\+.+|.+\+{r}\[\w+-1\]", t):
                D.add(0.72, cl.line, f"{cl.receiver}.push_back(poprzednia suma + …) — sumy prefiksowe")
            elif re.fullmatch(rf"(max|min)\({r}(\.back\(\)|\[\w+-1\]),.+\)", t):
                D.add(0.45, cl.line, "prefiksowe maksimum/minimum")
                D.variant = "prefiksowe maksimum"
    for cl in x.calls("partial_sum", "inclusive_scan"):
        D.add(0.7, cl.line, "std::partial_sum")
    # zapytanie przedziałowe P[r] - P[l-1]
    for m, ln in x.find(r"(\w+)\[(\w+)\]-(\w+)\[(\w+)-1\]|(\w+)\[(\w+)\+1\]-(\w+)\[(\w+)\]"):
        g = m.groups()
        if (g[0] and g[0] == g[2] and g[1] != g[3]) or (g[4] and g[4] == g[6] and g[5] != g[7]):
            if D.sig:
                D.add(0.3, ln, "suma na przedziale jako różnica dwóch sum prefiksowych")
            else:
                D.add(0.25, ln, "różnica dwóch elementów tej samej tablicy (zapytanie o przedział?)")
    # tablica różnicowa
    for m, ln in x.find(r"(\w+)\[\w+\](\+\+|\+=\w+);(\w+)\[\w+\+1\](--|-=\w+);"):
        if m.group(1) == m.group(3):
            D.add(0.5, ln, "tablica różnicowa: +x na początku, −x za końcem przedziału")
            D.variant = "tablica różnicowa"


def d_sorting(x: Ctx, D: Det):
    c = x.c
    for cl in x.calls("sort", "stable_sort", "qsort", "partial_sort"):
        if cl.is_method:
            continue
        D.add(0.72, cl.line, f"std::{cl.name}")
    for cl in x.calls("nth_element", "make_heap", "sort_heap"):
        D.add(0.35, cl.line, f"std::{cl.name}")
    # bąbelkowe: porównanie sąsiadów + zamiana
    for m, ln in x.find(r"if\((\w+)\[(\w+)\]>(\w+)\[(\w+)\+1\]\)"):
        if m.group(1) == m.group(3) and m.group(2) == m.group(4):
            D.add(0.8, ln, "porównanie sąsiednich elementów i zamiana — sortowanie bąbelkowe")
            D.variant = "bąbelkowe"
    # wybieranie: if (a[j] < a[mn]) mn = j; ... swap(a[i], a[mn])
    for m, ln in x.find(r"if\((\w+)\[(\w+)\](<|>)(\w+)\[(\w+)\]\)(\{)?(\w+)=(\w+);"):
        if m.group(1) == m.group(4) and m.group(7) == m.group(5) and m.group(8) == m.group(2):
            if x.find(rf"swap\({re.escape(m.group(1))}\[\w+\],{re.escape(m.group(1))}\[{re.escape(m.group(7))}\]\)"):
                D.add(0.8, ln, "szukanie minimum i zamiana — sortowanie przez wybieranie")
                D.variant = "przez wybieranie"
    # wstawianie
    for m, ln in x.find(r"while\((\w+)>=0&&(\w+)\[(\w+)\]>(\w+)\)"):
        D.add(0.75, ln, "przesuwanie większych elementów — sortowanie przez wstawianie")
        D.variant = "przez wstawianie"
    # scalanie / quicksort: rekurencja z dwoma wywołaniami na połówkach
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        if len(f.self_calls) >= 2 and re.search(r"\((\w+)\+(\w+)\)/2|\w+\+\(\w+-\w+\)/2", body) and \
                not re.search(r"(2\*\w+|\w+\*2|\w+<<1)(\+1|\|1)?,", body):
            others = [cl for cl in f.calls if cl.name in x.user_funcs and cl.name != f.name]
            merge_rx = r"while\(\w+<=\w+&&\w+<=\w+\)|while\(\w+<\w+&&\w+<\w+\)|\bmerge\(|inplace_merge\(|\w+\[\w+\+\+\]=\w+\[\w+\+\+\]"
            merged = re.search(merge_rx, body) or any(
                re.search(merge_rx, normalize_expr(c.text(g.open_idx, g.close_idx)))
                for g in c.functions if g.name in {o.name for o in others})
            if merged:
                D.add(0.8, f.line, f"{f.name}(): dziel na połowy, posortuj rekurencyjnie i scal — sortowanie przez scalanie")
                D.variant = "przez scalanie"
        if len(f.self_calls) >= 2 and re.search(r"pivot|piwot|\w+\[\(\w+\+\w+\)/2\]", body) and \
                re.search(r"swap\(", body):
            D.add(0.8, f.line, f"{f.name}(): podział względem elementu osiowego — quicksort")
            D.variant = "szybkie (quicksort)"
    # zliczanie
    for m, ln in x.find(r"(\w+)\[(\w+)(\[\w+\])?\]\+\+"):
        arr = m.group(1)
        d = c.decl_of(arr)
        if d is None or d.nested >= 2:
            continue
        if x.find(rf"while\({re.escape(arr)}\[\w+\]-->0\)|while\({re.escape(arr)}\[\w+\]--\)|"
                  rf"for\(int \w+=0;\w+<{re.escape(arr)}\[\w+\];\w+\+\+\)|"
                  rf"if\({re.escape(arr)}\[(\w+)\](>0|!=0)\)\{{?(std::)?cout<<\1"):
            D.add(0.75, ln, f"tablica zliczeń {arr} i wypisanie wartości po kolei — sortowanie przez zliczanie")
            D.variant = "przez zliczanie"
            break


def d_greedy(x: Ctx, D: Det):
    c = x.c
    D.cap = 0.7
    sorts = [cl for cl in x.calls("sort", "stable_sort") if not cl.is_method]
    if sorts:
        after = [L for L in c.loops if L.idx > sorts[0].idx and L.parent is None]
        if after:
            body = normalize_expr(c.text(*after[0].body))
            if re.search(r"if\(.*(<=|>=|<|>).*\)\{?\w+(\+\+|\+=|=)", body):
                D.add(0.35, after[0].line, "po posortowaniu jedno przejście z lokalną decyzją (wybór zachłanny)")
        if any(len(cl.args) >= 3 for cl in sorts):
            D.add(0.15, sorts[0].line, "sortowanie z własnym komparatorem (kryterium zachłanne)")
    pqs = x.decls("pq")
    if pqs and not _relaxations(x) and not _indegree(x):
        D.add(0.4, pqs[0].line, "kopiec (priority_queue) do wybierania najlepszego elementu w każdym kroku")
    if x.name_hint("greedy") or "zachłan" in x.comment_text or "zachlan" in x.comment_text:
        D.add(0.3, None, "komentarz/nazwy: zachłanne")


def d_dp(x: Ctx, D: Det):
    c = x.c
    relax_lines = {r["line"] for r in _relaxations(x)}
    qbodies = [(q["loop"].body[0], q["loop"].body[1]) for q in _queue_loops(x)]
    seg_like = lambda r: bool(re.search(r"\[(2\*\w+|\w+\*2)(\+1)?\]|<<1", r["rhs"]))
    sparse = lambda r: "<<" in r["rhs"]
    lift = lambda r: bool(re.search(rf"{re.escape(r['target'])}\[{re.escape(r['target'])}\[", r["rhs"]))
    hashy = lambda r: bool(re.search(r"\*\w+\+\w+\[\w+\]|\*\w+%|\*(\d{2,}|\w+)\+\(?\w+\[\w+\]-'", r["rhs"])) and "%" in r["rhs"]
    n_trans = 0
    adj_bodies = [(L.body[0], L.body[1]) for L in c.loops if _adj_of(x, L)]
    for r in _self_refs(x):
        in_graph = any(a <= r["idx"] <= b for a, b in qbodies + adj_bodies)
        if (r["line"] in relax_lines and in_graph) or seg_like(r) or sparse(r) or lift(r) or hashy(r):
            continue
        # indeksy muszą zależeć od zmiennych pętli (inaczej to np. łączenie zbiorów w DSU)
        loop_vars = {c.loops[k].var for k in c.loops_containing(r["idx"]) if c.loops[k].var}
        idx_ids = set(re.findall(r"[A-Za-z_]\w*", r["lhs"][len(r["target"]):] + "".join(r["refs"])))
        if not (idx_ids & loop_vars) and not any(f.open_idx < r["idx"] < f.close_idx for f in x.recursive):
            continue
        if in_graph and re.search(r"\+1$", r["rhs"]):
            continue          # odległość BFS (+1), nie DP
        # sumy prefiksowe 2D liczone od końca (P[i][j] z P[i+1][j], P[i][j+1]) bez max/min
        if r["dims"] >= 2 and not r["minmax"] and re.search(r"-\w+\[\w+[+-]1\]\[\w+[+-]1\]", r["rhs"]):
            continue
        if in_graph and not r["minmax"]:
            # dp na DAG-u w kolejce (toposort) — liczymy, ale słabiej
            D.add(0.45, r["line"], f"{r['lhs']} liczone z wartości sąsiada w kolejce (DP na grafie)")
            D.variant = D.variant or "na grafie"
            n_trans += 1
            continue
        rhs = r["rhs"]
        # tablica logarytmów lg[i] = lg[i/2] + 1, potęgi pw[i] = pw[i-1] * B % M
        if re.fullmatch(rf"{re.escape(r['target'])}\[\w+/2\]\+1|\(?{re.escape(r['target'])}\[\w+-1\]\*\w+\)?(%\w+)?", rhs):
            continue
        # zwykłe kopiowanie/przesuwanie elementów: X[i] = X[i+1]
        if re.fullmatch(rf"{re.escape(r['target'])}(\[[^\[\]]+\])+", rhs) and r["op"] == "=":
            continue
        # prefiks liczony do przodu: X[i+1] = X[i] + …
        if len(r["refs"]) == 1 and not r["minmax"] and r["op"] == "=" and \
                re.fullmatch(r"\[(\w+)\+1\]", r["lhs"][len(r["target"]):]) and \
                r["refs"][0] == "[" + re.fullmatch(r"\[(\w+)\+1\]", r["lhs"][len(r["target"]):]).group(1) + "]":
            continue
        # prefiks: X[i] = X[i-1] + a[i]  (jedno odwołanie, przesunięcie 1, bez min/max)
        if len(r["refs"]) == 1 and not r["minmax"] and re.fullmatch(r"\[\w+-1\]", r["refs"][0]) and \
                re.fullmatch(rf"{re.escape(r['target'])}\[\w+-1\][+]\w+(\[\w+\])?|\w+(\[\w+\])?\+{re.escape(r['target'])}"
                             rf"\[\w+-1\]", rhs):
            continue
        if not r["loop"] and r["op"] == "=" and not r["minmax"]:
            continue
        w = 0.62 if r["minmax"] else 0.5
        if r["dims"] >= 2:
            w += 0.1
        D.add(w, r["line"], f"przejście: {r['lhs']} zależy od wcześniej policzonych stanów "
                            f"({', '.join(r['target'] + z for z in r['refs'][:2])})")
        n_trans += 1
        if re.search(rf"max\({re.escape(r['lhs'])},{re.escape(r['target'])}\[\w+-\w+\]\+\w+(\[\w+\])?\)", rhs):
            # sprawdź pętlę malejącą — plecak 0/1
            k = c.innermost_loop(r["idx"])
            if k is not None and c.loops[k].step and re.search(r"--", c.text(*c.loops[k].step)):
                D.variant = "problem plecakowy 0/1"
                D.add(0.4, c.loops[k].line, "pętla po pojemności od końca — klasyczny plecak 0/1")
            else:
                D.variant = D.variant or "plecak"
        elif re.search(rf"min\({re.escape(r['lhs'])},{re.escape(r['target'])}\[\w+-\w+\]\+1\)", rhs):
            D.variant = D.variant or "wydawanie reszty / plecak nieograniczony"
    # tablice toczące: now[j] = f(dp[j], now[j-1]) + dp = now
    for m, ln in x.find(r"(\w+)=(\w+);"):
        a_, b_ = m.group(1), m.group(2)
        if a_ != b_ and x.kind(a_) == "vector" and x.kind(b_) == "vector" and \
                x.find(rf"{re.escape(b_)}\[\w+\]=[^;]*{re.escape(a_)}\[\w+\]"):
            D.add(0.55, ln, f"tablica stanów {a_} zastępowana nowym wierszem {b_} (DP wierszami)")
            break
    # rekurencja ze spamiętywaniem
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        if re.search(r"if\(\w+(\[[^\]]+\])+(!=-1|!=0|>=0|!=\w+)\)return \w+(\[[^\]]+\])+;|"
                     r"if\(\w+\.count\([^)]*\)\)return|if\(\w+\[\w+\]\[\w+\]\)return", body) or \
                re.search(r"return \w+(\[\w+\])+=", body):
            D.add(0.78, f.line, f"{f.name}(): rekurencja ze spamiętywaniem (memoizacja)")
            D.variant = D.variant or "memoizacja"
    # LIS przez lower_bound na tablicy końców
    for cl in x.calls("lower_bound", "upper_bound"):
        if cl.args and not cl.is_method:
            seg = normalize_expr(c.text(cl.idx, min(c.n - 1, cl.close_idx + 30)))
            if re.search(r"\.end\(\)\)\w*;?.*(push_back|==\w+\.end\(\))", seg) and \
                    re.search(r"\*\w+=|\.push_back\(", normalize_expr(c.text(cl.idx, min(c.n - 1, cl.close_idx + 40)))):
                D.add(0.35, cl.line, "LIS: lower_bound na tablicy najmniejszych końców")
    names = [k for k in x.ids if re.fullmatch(r"dp\d?|memo|pamiec|tab_dp", k)]
    if names and D.sig:
        D.add(0.15, None, f"tablica {names[0]}")
    elif names:
        D.add(0.25, None, f"tablica o nazwie {names[0]}")


def d_bitmask_dp(x: Ctx, D: Det):
    loops = x.find(r"for\(\w+ \w+=0;\w+<\(?1(LL)?<<\w+\)?;")
    tests = x.find(r"\w+&\(?1(LL)?<<\w+\)?|\(\w+>>\w+\)&1")
    dpmask = x.find(r"\w+\[\w+\|\(?1(LL)?<<\w+\)?\]|\w+\[\w+\^\(?1(LL)?<<\w+\)?\]|\w+\[\w+&~\(?1<<\w+\)?\]")
    if loops:
        D.add(0.4, loops[0][1], "pętla po wszystkich maskach 0 … 2ⁿ−1")
    if tests:
        D.add(0.2, tests[0][1], "sprawdzanie bitów maski")
    if dpmask:
        D.add(0.6, dpmask[0][1], "stan indeksowany maską z dodanym/usuniętym bitem")
    if loops and not dpmask:
        D.variant = "przegląd podzbiorów"
        D.penalty = 0.8
    if x.calls("__builtin_popcount", "__builtin_popcountll", "popcount") and D.sig:
        D.add(0.1, None, "__builtin_popcount")


def d_bfs(x: Ctx, D: Det):
    c = x.c
    for q in _queue_loops(x):
        if q["kind"] in ("pq", "stack", "set", "multiset"):
            continue
        body = q["body"]
        if q["kind"] == "vector" and not q["front"]:
            continue
        w = 0.55 if q["kind"] in ("queue", "deque", "vector_queue") else 0.3
        D.add(w, q["line"], f"pętla przetwarzająca kolejkę {q['name']} (FIFO)")
        if q["adj"]:
            D.add(0.25, c.loops[q["adj"][0]].line, "dla każdego wierzchołka przegląd sąsiadów")
        if re.search(r"if\(!\w+\[|if\(\w+\[[^\]]+\]==(-1|0|false)\)|if\(\w+\[[^\]]+\]>\w+\[[^\]]+\]\+1\)|"
                     r"\w+\[[^\]]+\]=\w+\[[^\]]+\]\+1", body):
            D.add(0.25, q["line"], "odległość = odległość rodzica + 1 / sprawdzanie odwiedzenia")
        if re.search(r"d[xy]\[|\+1,\w+\)|\w+-1,\w+\)", body) or re.search(r"\[\w+[+-]1\]\[\w+\]", body):
            D.add(0.15, q["line"], "ruchy po sąsiednich polach planszy")
        if q["kind"] == "deque" and ".push_front(" in body:
            D.variant = "0-1 BFS"
    if D.sig and x.name_hint("bfs"):
        D.add(0.05, None, "nazwa kolejki")
    if any(e.startswith("pętla przetwarzająca") for _, _, e in D.sig) and _indegree(x):
        D.penalty = 0.85


def _indegree(x: Ctx) -> list:
    """Licznik stopni wejściowych zwiększany przy wczytywaniu krawędzi i porównywany z zerem."""
    c = x.c
    res = []
    adjl = _adj_lists(x)
    for a in c.assigns:
        if a.op not in ("++", "+=") or not a.indexed:
            continue
        k = c.innermost_loop(a.idx)
        if k is None:
            continue
        L = c.loops[k]
        if not any(L.body[0] <= al["call"].idx <= L.body[1] for al in adjl):
            continue
        arr = re.escape(a.target)
        if x.find(rf"if\(!?{arr}\[[^\]]+\](==0)?\)|if\(--{arr}\[[^\]]+\]==0\)|{arr}\[\w+\]==0"):
            res.append(a)
    return res


def d_dfs(x: Ctx, D: Det):
    c = x.c
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        in_adj = False
        for cl in f.self_calls:
            k = c.innermost_loop(cl.idx)
            if k is not None and _adj_of(x, c.loops[k]):
                in_adj = True
        p0 = re.escape(f.params[0].name) if f.params else r"\w+"
        marks = re.search(rf"\w+\[{p0}\]=(true|1|\w+);", body)
        guard = re.search(r"if\(!\w+\[|if\(\w+\[[^\]]+\]==(0|false|\d+)(\)|&&|\|\|)|if\(\w+!=\w+\)|==\w+\)continue|"
                          r"if\(\w+\[[^\]]+\]\)continue", body)
        if in_adj:
            D.add(0.72, f.line, f"{f.name}(): rekurencyjne wejście do każdego nieodwiedzonego sąsiada")
            if marks:
                D.add(0.2, f.line, "oznaczanie wierzchołka jako odwiedzonego")
        elif marks and guard and len(f.self_calls) >= 1 and any(
                re.search(r"[+-]1|d[xy]\[|\w+\[\w+\]", normalize_expr(c.text(*cl.args[0]))) for cl in f.self_calls
                if cl.args):
            D.add(0.55, f.line, f"{f.name}(): rekurencyjne odwiedzanie sąsiednich stanów (flood fill)")
        elif marks and guard:
            D.add(0.45, f.line, f"{f.name}(): rekurencyjne odwiedzanie stanów z tablicą odwiedzin")
    for q in _queue_loops(x):
        if q["kind"] == "stack" or (q["kind"] == "vector" and re.search(r"\.pop_back\(\)", q["body"]) and q["adj"]):
            if q["adj"]:
                D.add(0.6, q["line"], f"iteracyjny DFS ze stosem {q['name']}")
    if D.sig and x.name_hint("dfs"):
        D.add(0.08, None, "nazwy: dfs/odwiedzone")


def d_dijkstra(x: Ctx, D: Det):
    c = x.c
    relax = [r for r in _relaxations(x) if r["plus"]]
    weighted = [r for r in relax if not r["unit"]]
    pqs = x.decls("pq")
    sets = [d for d in x.decls("set") if "pair" in d.elem or "tuple" in d.elem]
    qls = _queue_loops(x)
    pq_loop = [q for q in qls if q["kind"] in ("pq", "set")]
    if weighted:
        D.add(0.45, weighted[0]["line"], f"relaksacja krawędzi: {weighted[0]['lhs']} = min(…, odległość + waga)")
    elif relax:
        D.add(0.2, relax[0]["line"], "relaksacja odległości")
    if pqs:
        D.add(0.35, pqs[0].line, f"kolejka priorytetowa {pqs[0].name}")
    if sets and re.search(r"\.erase\(\w+\.begin\(\)\)|\.begin\(\)", c.code):
        D.add(0.3, sets[0].line, f"zbiór par {sets[0].name} jako kolejka priorytetowa")
    if pq_loop:
        D.add(0.2, pq_loop[0]["line"], "zdejmowanie wierzchołka o najmniejszej odległości")
    if pqs and re.search(r"greater<|\*-1|\*\(-1\)|push\(\{?-|make_pair\(-|\{-\w+", x.flat):
        D.add(0.1, None, "min-kopiec (greater<> albo ujemne odległości)")
    # gęsta Dijkstra O(n²): wybór minimum nieodwiedzonego + relaksacja z dodawaniem
    if not pqs and not sets and weighted:
        for L in c.loops:
            body = normalize_expr(c.text(*L.body))
            if L.children and re.search(r"if\(!\w+\[\w+\]&&\(\w+==-1\|\|\w+\[\w+\]<\w+\[\w+\]\)\)", body):
                D.add(0.4, L.line, "wybór najbliższego nieodwiedzonego wierzchołka (Dijkstra O(n²))")
                D.variant = "O(n²)"
                break
    if D.sig and x.name_hint("dijkstra"):
        D.add(0.08, None, "nazwy odległości")
    if not (pqs or sets or D.variant):
        D.penalty = 0.35
    elif not relax:
        D.penalty = 0.45


def d_bellman_ford(x: Ctx, D: Det):
    c = x.c
    relax = [r for r in _relaxations(x) if r["plus"]]
    for r in relax:
        ls = c.loops_containing(r["idx"])
        if len(ls) >= 2:
            outer = c.loops[ls[0]]
            inner = c.loops[ls[-1]]
            ot = normalize_expr(c.text(*outer.cond)) if outer.cond else ""
            it = normalize_expr(c.text(*inner.cond)) if inner.cond else (
                normalize_expr(c.text(*inner.range_expr)) if inner.range_expr else "")
            if re.search(r"<\w+-1$|<=\w+-1$|<\w+$", ot) and (inner.kind == "range" or re.search(r"<m|size\(\)", it)) \
                    and not x.decls("pq"):
                edges = [d for d in c.decls if d.kind == "vector" and re.search(r"tuple|pair<\w+,pair|array<", d.type)]
                if edges or inner.kind == "range":
                    D.add(0.7, outer.line, "n−1 rund relaksacji wszystkich krawędzi")
                    break
    if re.search(r"ujemn|negative", x.comment_text):
        D.add(0.2, None, "komentarz o ujemnych wagach")


def d_floyd(x: Ctx, D: Det):
    for m, ln in x.find(r"(\w+)\[(\w+)\]\[(\w+)\]=min\(\1\[\2\]\[\3\],\1\[\2\]\[(\w+)\]\+\1\[\4\]\[\3\]\)|"
                        r"if\((\w+)\[(\w+)\]\[(\w+)\]\+\5\[\7\]\[(\w+)\]<\5\[\6\]\[\8\]\)"):
        D.add(0.92, ln, "d[i][j] = min(d[i][j], d[i][k] + d[k][j]) — Floyd-Warshall")
        break


def d_topo_sort(x: Ctx, D: Det):
    c = x.c
    ind = _indegree(x)
    if ind:
        D.add(0.6, ind[0].line, f"liczenie stopni wejściowych ({ind[0].target}) przy wczytywaniu krawędzi")
        arr = re.escape(ind[0].target)
        dec = x.find(rf"{arr}\[[^\]]+\]--|--{arr}\[[^\]]+\]|{arr}\[[^\]]+\]-=1")
        if dec:
            D.add(0.35, dec[0][1], "zmniejszanie stopnia po usunięciu krawędzi; wierzchołek ze stopniem 0 do kolejki")
            D.variant = "algorytm Kahna"
    # DFS z kolejnością post-order
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        m = re.search(r"(\w+)\.push_back\(\w+\);\}?$", body.rstrip("}")) or re.search(
            r"\}(\w+)\.push_back\(" + (re.escape(f.params[0].name) if f.params else r"\w+") + r"\);", body)
        if m and x.calls("reverse") and any(cl.args and m.group(1) in normalize_expr(c.text(*cl.args[0]))
                                            for cl in x.calls("reverse")):
            D.add(0.6, f.line, f"{f.name}(): wierzchołek dopisywany po przetworzeniu potomków, potem odwrócenie")
            D.variant = "DFS (post-order)"
    if D.sig and x.name_hint("topo_sort"):
        D.add(0.1, None, "nazwy: stopnie wejściowe / kolejność")


def d_dsu(x: Ctx, D: Det):
    c = x.c
    for f in x.recursive:
        if not f.params:
            continue
        p = re.escape(f.params[0].name)
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        fn = re.escape(f.name)
        if re.search(rf"(\w+)\[{p}\]={fn}\(\1\[{p}\]\)", body):
            D.add(0.8, f.line, f"{f.name}(): szukanie reprezentanta z kompresją ścieżki")
        elif re.search(rf"return {fn}\((\w+)\[{p}\]\)", body) and re.search(rf"(\w+)\[{p}\]==?{p}|{p}==(\w+)\[{p}\]",
                                                                          body):
            D.add(0.65, f.line, f"{f.name}(): szukanie reprezentanta (bez kompresji ścieżki)")
    for m, ln in x.find(r"while\((\w+)\[(\w+)\]!=\2\)\2=\1\[\2\]|while\((\w+)!=(\w+)\[\3\]\)\3=\4\[\3\]"):
        D.add(0.6, ln, "iteracyjne szukanie reprezentanta")
    find_names = [f.name for f in c.functions if f.self_calls and f.params and D.sig]
    if find_names:
        fnm = re.escape(find_names[0])
        if x.find(rf"{fnm}\(\w+\)!={fnm}\(\w+\)|(\w+)=={fnm}\(\w+\)|(\w+)!=(\w+)\)\{{?(if|\w+\[)"):
            D.add(0.25, None, "łączenie zbiorów, gdy reprezentanci są różni")
    if x.find(r"(\w+)\[(\w+)\]\+=\1\[(\w+)\];\w+\[\3\]=\2|(\w+)\[(\w+)\]\+=\4\[(\w+)\]") and D.sig:
        D.add(0.15, None, "łączenie według rozmiaru")
    if x.find(r"for\([^;]*;[^;]*;[^)]*\)(\w+)\[(\w+)\]=\2;|for\([^;]*;[^;]*;[^)]*\)\{?(\w+)\.push_back\((\w+)\);"
              r"|iota\(") and D.sig:
        D.add(0.1, None, "inicjalizacja: każdy element jest swoim reprezentantem")
    if D.sig and x.name_hint("dsu"):
        D.add(0.08, None, "nazwy: find/union/rodzic")


def d_mst(x: Ctx, D: Det, dsu_conf: float):
    c = x.c
    sorts = [cl for cl in x.calls("sort") if not cl.is_method]
    edge_vecs = [d for d in c.decls if d.kind in ("vector", "array") and re.search(r"tuple|pair<\w+( \w+)?,pair|"
                                                                                       r"array<|pair<\w+( \w+)?,\w+( \w+)?>",
                                                                                       d.type)]
    if dsu_conf >= 0.5 and sorts:
        D.add(0.55, sorts[0].line, "sortowanie krawędzi po wadze + DSU — algorytm Kruskala")
        if x.find(r"\+=\w+(\.first|\[\w+\]\.first|\.w|\.koszt|\.waga)|\+=get<0>|"
                  r"if\(\w+\(\w+\)!=\w+\(\w+\)\)\{[^}]*\+=\w+;"):
            D.add(0.2, None, "sumowanie wag dodanych krawędzi")
        D.variant = "Kruskal"
    # Prim O(n²): dist[v] = min(dist[v], w) bez dodawania odległości
    for r in _relaxations(x):
        if not r["plus"] and r["kind"] == "min" and not r["self"]:
            D.add(0.5, r["line"], f"{r['lhs']} = min({r['lhs']}, waga krawędzi) — klucz Prima")
            D.variant = "Prim"
            break
    if re.search(r"intree|in_tree|wdrzewie|w_drzewie|used", x.flat.lower()) and D.has("") and D.variant == "Prim":
        D.add(0.2, None, "tablica wierzchołków już w drzewie")
    if x.name_hint("mst") or re.search(r"\bmst\b|rozpinaj|kruskal|prim\b", x.comment_text):
        D.add(0.25, None, "nazwy/komentarz: MST")


def d_lca(x: Ctx, D: Det):
    for m, ln in x.find(r"(\w+)\[(\w+)\]\[(\w+)\]=\1\[\1\[\2\]\[\3-1\]\]\[\3-1\]|"
                        r"(\w+)\[(\w+)\]\[(\w+)\]=\4\[\5-1\]\[\4\[\5-1\]\[\6\]\]"):
        D.add(0.85, ln, "tablica skoków o 2^k (binary lifting)")
        break
    for m, ln in x.find(r"while\((\w+)\[(\w+)\]>(\w+)\[(\w+)\]\)\2=(\w+)\[\2\]"):
        D.add(0.55, ln, "wyrównywanie głębokości i wspinanie się do rodzica")
        break
    if x.find(r"for\(\w+ \w+=\w+(-1)?;\w+>=0;\w+--\)if\(\w+\[\w+\]\[\w+\]!=\w+\[\w+\]\[\w+\]\)"):
        D.add(0.4, x.first(r"for\(\w+ \w+=\w+(-1)?;\w+>=0;\w+--\)if"), "skoki od największej potęgi dwójki")
    if D.sig and x.name_hint("lca"):
        D.add(0.1, None, "nazwy: lca/przodek")


def d_segment_tree(x: Ctx, D: Det):
    c = x.c
    comb = x.find(r"(\w+)\[(\w+)\]=(?:\w+\()?\1\[(?:2\*\2|\2\*2|\2<<1)\](?:[-+^|&*,]|\)|\w+\()+.*?\1\[(?:2\*\2\+1|\2\*2\+1|"
                  r"\2<<1\|1|\(\2<<1\)\|1|2\*\2\+2)\]")
    if comb:
        D.add(0.7, comb[0][1], "węzeł = połączenie dwóch synów (2v i 2v+1)")
    elif x.find(r"\w+\[(2\*\w+|\w+\*2)\]") and x.find(r"\w+\[(2\*\w+\+1|\w+\*2\+1)\]"):
        D.add(0.2, x.first(r"\w+\[(2\*\w+|\w+\*2)\]"), "indeksy synów 2v i 2v+1")
    size = x.find(r"\w+\[(4\*\w+|\w+\*4|2\*\w+|\w+\*2|\(?1<<\d+\)?\*2|2\*\(1<<\d+\))\];|resize\((2\*\w+|4\*\w+|\w+\*2)")
    if size:
        D.add(0.25, size[0][1], "tablica rozmiaru 2·N / 4·N")
    # iteracyjne (od dołu)
    up = x.find(r"(\w+)(\+=|=\w+\+)\w+(-1)?;.{0,80}?while\(\1>1\)\{?\1/=2|while\((\w+)>1\)\{\4/=2;|"
                r"while\((\w+)>1\)\{\5>>=1;")
    if up:
        D.add(0.35, up[0][1], "przejście od liścia do korzenia (v /= 2)")
    q = x.find(r"while\((\w+)/2!=(\w+)/2\)|while\((\w+)<=(\w+)\)\{if\(\3%2==1\)|if\((\w+)%2==1\)\w+\+=\w+\[\5\+\+\]|"
               r"if\((\w+)&1\)\w+(\+=|=)[^;]*\w+\[\5(\+\+|--)?\]")
    if q:
        D.add(0.35, q[0][1], "zapytanie od dołu: lewy/prawy koniec wędruje do góry")
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        if len(f.params) >= 3 and re.search(r"\((\w+)\+(\w+)\)/2|\(\w+\+\w+\)>>1", body) and \
                re.search(r"(2\*\w+|\w+\*2|\w+<<1)(\+1|\|1)?,", body):
            D.add(0.55, f.line, f"{f.name}(węzeł, l, r, …): rekurencja na dwie połówki przedziału")
        elif len(f.params) >= 4 and len(f.self_calls) >= 2 and re.search(r"\(\w+\+\w+\)/2", body) and \
                re.search(r"\w+\*2", body) and re.search(r"(\w+)\+=" + re.escape(f.name) + r"\(", body):
            D.add(0.5, f.line, f"{f.name}(): zapytanie na przedziale — suma z dwóch połówek")
    if D.sig and x.name_hint("segment_tree"):
        D.add(0.08, None, "nazwa drzewa")
    # kopiec (heap) a nie drzewo przedziałowe
    if not comb and re.search(r"swap\(\w+\[\w+\],\w+\[\w+/2\]\)|swap\(\w+\[\w+\],\w+\[(2\*\w+|\w+\*2)(\+1)?\]\)", x.flat):
        D.penalty = 0.4


def d_lazy(x: Ctx, D: Det, seg_conf: float):
    if seg_conf < 0.45:
        return
    m = x.find(r"(lazy|lz|tag|add|push|odl|zaleg|lzy|pending)\w*\[(2\*\w+|\w+\*2|\w+<<1)\](\+=|=)")
    if m:
        D.add(0.8, m[0][1], "przekazywanie odroczonej zmiany do synów (lazy propagation)")
    elif x.find(r"\w+\[(2\*\w+|\w+\*2)\](\+=|=)\w+\[\w+\];\w+\[(2\*\w+\+1|\w+\*2\+1)\](\+=|=)\w+\[\w+\];"):
        D.add(0.6, None, "spychanie wartości węzła do synów")


def d_fenwick(x: Ctx, D: Det):
    m = x.find(r"\w+&-\w+|\w+&\(-\w+\)|\(-\w+\)&\w+|\w+&\(~\w+\+1\)")
    if m:
        D.add(0.85, m[0][1], "najniższy zapalony bit i & -i — drzewo Fenwicka")
    elif x.find(r"\(\w+&\(\w+\+1\)\)-1|\w+\|=\w+\+1"):
        D.add(0.75, x.first(r"\(\w+&\(\w+\+1\)\)-1|\w+\|=\w+\+1"), "indeksowanie Fenwicka (i & (i+1)) − 1")
    if D.sig and x.name_hint("fenwick"):
        D.add(0.08, None, "nazwa BIT/Fenwick")


def d_sparse_table(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]\[(\w+)\]=(min|max|__gcd|gcd)\(\1\[\2-1\]\[\3\],\1\[\2-1\]\[\3\+\(?1<<\(?\2-1\)?\)?\]\)|"
               r"(\w+)\[(\w+)\]\[(\w+)\]=(min|max|__gcd|gcd)\(\5\[\6\]\[\7-1\],\5\[\6\+\(?1<<\(?\7-1\)?\)?\]\[\7-1\]\)")
    if m:
        D.add(0.88, m[0][1], "st[k][i] z dwóch przedziałów długości 2^(k−1)")
    lg = x.find(r"(\w+)\[(\w+)\]=\1\[\2/2\]\+1")
    if lg and m:
        D.add(0.2, lg[0][1], "tablica logarytmów")


def d_kmp(x: Ctx, D: Det):
    m = x.find(r"while\((\w+)>0&&(\w+)\[(\w+)\]!=\2\[\1\]\)\1=(\w+)\[\1-1\]|"
               r"while\((\w+)&&(\w+)\[(\w+)\]!=\6\[\5\]\)\5=(\w+)\[\5-1\]|"
               r"while\((\w+)>0&&(\w+)\[\w+\]!=(\w+)\[\9\]\)\9=(\w+)\[\9-1\]|"
               r"while\((\w+)!=-1&&\w+\[\w+\]!=\w+\[\13\+1\]\)\13=\w+\[\13\]")
    if m:
        D.add(0.88, m[0][1], "cofanie się po funkcji prefiksowej przy niedopasowaniu")
    if D.sig and x.name_hint("kmp"):
        D.add(0.1, None, "nazwy: pi/kmp")


def d_z_function(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]=min\(\w+-\2(\+1)?,\1\[\2-\w+\]\)|while\(\w+\+(\w+)\[(\w+)\]<\w+&&\w+\[\4\[\5\]\]==\w+\[\5\+\4\[\5\]\]\)")
    if m:
        D.add(0.88, m[0][1], "z[i] = min(r − i, z[i − l]) i rozszerzanie dopasowania")


def d_hashing(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]=\(?\1\[\2-1\]\*\w+\+(\(?\w+\[\w+(-1)?\]|\w+\)|\w+\[\w+(-1)?\]-'\w')|"
               r"(\w+)\[(\w+)\+1\]=\(?\6\[\7\]\*\w+\+")
    if m:
        D.add(0.7, m[0][1], "hasz prefiksu: h[i] = h[i−1]·B + s[i] (mod M)")
    pw = x.find(r"(\w+)\[(\w+)\]=\(?\1\[\2-1\]\*\w+%\w+|(\w+)\[(\w+)\]=\3\[\4-1\]\*\w+;")
    if pw and D.sig:
        D.add(0.25, pw[0][1], "potęgi podstawy B^i")
    sub = x.find(r"(\w+)\[\w+\]-\1\[\w+\]\*\w+\[\w+-\w+(\+1)?\]")
    if sub:
        D.add(0.3, sub[0][1], "hasz podsłowa z różnicy haszy prefiksów")


def d_trie(x: Ctx, D: Det):
    m = x.find(r"if\(!?(\w+)\[(\w+)\]\[(\w+)\](==0)?\)\{?\1\[\2\]\[\3\]=(\+\+\w+|\w+\+\+|\w+);?\}?\2=\1\[\2\]\[\3\]")
    if m:
        D.add(0.85, m[0][1], "przejście po krawędzi litery, tworzenie nowego węzła gdy brak")
    arr = x.find(r"\w+\[\w+\]\[26\]|\w+\[26\];|\w+\[\w+\]\[2\];")
    if arr and (m or x.name_hint("trie")):
        D.add(0.3, arr[0][1], "tablica dzieci [26]")


def d_manacher(x: Ctx, D: Det):
    m = x.find(r"min\((\w+)\[\w+\+\w+-\w+(\+1)?\],\w+-\w+(\+1)?\)|min\(\w+-\w+(\+1)?,(\w+)\[\w+\+\w+-\w+\]\)|"
               r"min\(\w+-\w+,(\w+)\[2\*\w+-\w+\]\)|(\w+)\[\w+\]=min\(\7\[2\*\w+-\w+\],\w+-\w+\)")
    if m:
        D.add(0.86, m[0][1], "promień palindromu z odbicia względem środka (Manacher)")


def d_sieve(x: Ctx, D: Det):
    c = x.c
    for k, L in enumerate(c.loops):
        if L.parent is None or not L.step:
            continue
        P = c.loops[L.parent]
        st = normalize_expr(c.text(*L.step))
        pv = P.var
        cond = normalize_expr(c.text(*L.cond)) if L.cond else ""
        body = normalize_expr(c.text(*L.body))
        hit = False
        # mnożnik: for (x = 2; x * i < N; x++) t[i * x] = 1  (i nie musi być zmienną pętli zewnętrznej)
        mm = re.search(rf"(\w+)\*(\w+)<", cond)
        if mm and L.var in (mm.group(1), mm.group(2)):
            other = mm.group(2) if mm.group(1) == L.var else mm.group(1)
            if re.search(rf"\w+\[({re.escape(other)}\*{re.escape(L.var)}|{re.escape(L.var)}\*{re.escape(other)})\]=",
                         body):
                pv = other
        if pv and re.fullmatch(rf"\w+\+={re.escape(pv)}", st) and re.search(r"\w+\[\w+\]=(true|false|1|0)|\w+\[\w+\]\+\+|"
                                                                               r"\w+\[\w+\]\.push_back", body):
            hit = True
        if pv and re.search(rf"\w+\*{re.escape(pv)}<|{re.escape(pv)}\*\w+<", cond) and re.search(
                rf"\w+\[{re.escape(pv)}\*\w+\]=|\w+\[\w+\*{re.escape(pv)}\]=", body):
            hit = True
        if not hit:
            continue
        pre = normalize_expr(c.text(P.body[0], L.idx))
        guard = re.search(rf"if\(!?\w+\[{re.escape(pv)}\](==0|==1|==false|==true)?\)", pre)
        init = normalize_expr(c.text(*L.init)) if L.init else ""
        if guard or re.search(rf"={re.escape(pv)}\*{re.escape(pv)}", init):
            D.add(0.85, L.line, f"wykreślanie wielokrotności liczby {pv} — sito Eratostenesa")
        else:
            D.add(0.45, L.line, f"przechodzenie po wielokrotnościach {pv} (sito dzielników / suma harmoniczna)")
            D.variant = "po wielokrotnościach"
    for m, ln in x.find(r"(\w+)\[(\w+)\*(\w+)\]=\3"):
        D.add(0.6, ln, "sito liniowe: najmniejszy dzielnik pierwszy")
        break
    big = [d for d in c.decls if d.kind == "array" and "bool" in d.elem and d.dims and
           (c._eval_const_text(d.dims[0]) or 0) >= 10 ** 5]
    if big and D.sig:
        D.add(0.1, big[0].line, "duża tablica logiczna")
    if D.sig and x.name_hint("sieve"):
        D.add(0.1, None, "nazwy: sito/pierwsze")


def d_fast_pow(x: Ctx, D: Det):
    c = x.c
    for L in c.loops:
        if L.kind not in ("while", "for") or not L.cond:
            continue
        body = normalize_expr(c.text(*L.body))
        ids = c.ids_in(*L.cond)
        if not ids:
            continue
        e = re.escape(ids[0])
        if re.search(rf"{e}(>>=1|/=2|=\w+/2|=\w+>>1)", body) and re.search(rf"if\({e}&1\)|if\({e}%2(==1|!=0)?\)|"
                                                                         rf"if\(\({e}&1\)(==1)?\)", body) and \
                re.search(r"(\w+)\*=\1|(\w+)=\2\*\2|(\w+)=\(?\3\*\3\)?%|(\w+)=\w+\(\4,\4\)", body):
            D.add(0.88, L.line, f"wykładnik {ids[0]} połowiony, podstawa podnoszona do kwadratu")
            break
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        if len(f.params) >= 2 and re.search(rf"{re.escape(f.name)}\(\w+,\w+/2(,\w+)?\)|{re.escape(f.name)}\(\w+,\w+>>1",
                                            body) and re.search(r"\*\w+|%2|&1", body):
            D.add(0.85, f.line, f"{f.name}(a, b/2): rekurencyjne szybkie potęgowanie")
    if D.sig and x.name_hint("fast_pow"):
        D.add(0.08, None, "nazwa potęgowania")


def d_mod_inverse(x: Ctx, D: Det, pow_conf: float):
    m = x.find(r"\w+\(\w+,\w+-2(,\w+)?\)|\w+\(\w+,\(?(MOD|mod|M|P|p|md)-2\)?")
    if m and pow_conf >= 0.4:
        D.add(0.85, m[0][1], "a^(p−2) mod p — odwrotność z małego twierdzenia Fermata")
    if x.find(r"(\w+)\[(\w+)\]=\(?\(?(\w+)-\3/\2\)?\*\1\[\3%\2\]"):
        D.add(0.8, None, "odwrotności 1..n wzorem rekurencyjnym")
    for f in x.c.functions:
        if f.self_calls and len(f.params) >= 3 and re.search(r"ext|rozsz|exgcd|egcd", f.name.lower()):
            D.add(0.75, f.line, f"{f.name}(): rozszerzony algorytm Euklidesa")


def d_gcd(x: Ctx, D: Det):
    for cl in x.calls("__gcd", "gcd", "lcm", "__lcm"):
        if cl.name in x.user_funcs:
            continue
        D.add(0.75, cl.line, f"{cl.name}() z biblioteki")
        break
    for f in x.recursive:
        body = normalize_expr(x.c.text(f.open_idx, f.close_idx))
        if re.search(rf"{re.escape(f.name)}\((\w+),(\w+)%\1\)|{re.escape(f.name)}\((\w+),\w+-\3\)", body):
            D.add(0.85, f.line, f"{f.name}(b, a % b): algorytm Euklidesa")
    m = x.find(r"while\((\w+)(!=0|>0)?\)\{?(\w+)=(\w+)%\1;\4=\1;\1=\3|while\((\w+)(!=0|>0)?\)\{?(\w+)%=\5;swap\(")
    if m:
        D.add(0.85, m[0][1], "pętla Euklidesa: (a, b) → (b, a mod b)")
    m = x.find(r"while\((\w+)!=(\w+)\)\{?if\(\1>\2\)\1-=\2")
    if m:
        D.add(0.75, m[0][1], "Euklides przez odejmowanie")
    if D.sig and x.name_hint("gcd"):
        D.add(0.1, None, "nazwy: nwd/gcd")


def d_matrix_pow(x: Ctx, D: Det, pow_conf: float):
    mul = x.find(r"(\w+)\[(\w+)\]\[(\w+)\](\+=|=\(?\1\[\2\]\[\3\]\+)\(?(\w+)\[\2\]\[(\w+)\]\*(\w+)\[\6\]\[\3\]")
    if mul:
        D.add(0.5, mul[0][1], "mnożenie macierzy c[i][j] += a[i][k]·b[k][j]")
        if pow_conf >= 0.5 or x.find(r"while\(\w+\)\{?if\(\w+&1\)|while\(\w+>0\)\{?if\(\w+%2"):
            D.add(0.5, None, "potęgowanie przez podnoszenie do kwadratu")


def d_convex_hull(x: Ctx, D: Det):
    m = x.find(r"while\((\w+)\.size\(\)>=?2&&\w+\([^;]*\)(<=|<|>=|>)0\)\{?\1\.pop_back\(\)|"
               r"while\(\w+>=2&&\w+\([^;]*\)(<=|<)0\)")
    if m:
        D.add(0.85, m[0][1], "usuwanie punktów tworzących zły skręt (iloczyn wektorowy)")
    if D.sig and x.name_hint("convex_hull"):
        D.add(0.1, None, "nazwy: otoczka/hull")


def d_geometry(x: Ctx, D: Det):
    cr = x.find(r"\(\w+(\.x|\[\d\])?-\w+(\.x|\[\d\])?\)\*\(\w+(\.y|\[\d\])?-\w+(\.y|\[\d\])?\)-\(\w+(\.y|\[\d\])?-"
                r"\w+(\.y|\[\d\])?\)\*\(\w+(\.x|\[\d\])?-\w+(\.x|\[\d\])?\)|\w+\.x\*\w+\.y-\w+\.y\*\w+\.x|"
                r"\w+\*\w+-\w+\*\w+;?//.*(cross|wektor)")
    if cr:
        D.add(0.65, cr[0][1], "iloczyn wektorowy (orientacja trzech punktów)")
    dist = x.find(r"sqrt\(\(?\w+(\.x|\[\w+\]\.first)?-\w+(\.x|\[\w+\]\.first)?\)?\*|hypot\(|sqrtl?\(\w+\*\w+\+\w+\*\w+\)|"
                  r"\(\w+-\w+\)\*\(\w+-\w+\)\+\(\w+-\w+\)\*\(\w+-\w+\)")
    if dist:
        D.add(0.45, dist[0][1], "odległość euklidesowa między punktami")
    if x.find(r"sqrtl?\(pow\(|pow\(\w+(\.\w+)?-\w+(\.\w+)?,2\)\+pow\(|pow\(max\(\w+,\w+\)-min\("):
        D.add(0.55, x.first(r"sqrtl?\(pow\(|pow\("), "odległość ze wzoru √((x₁−x₂)² + (y₁−y₂)²)")
    sh = x.find(r"\w+\*\(\w+-\w+\)\+\w+\*\(\w+-\w+\)\+\w+\*\(\w+-\w+\)")
    if sh:
        D.add(0.6, sh[0][1], "pole trójkąta ze współrzędnych (wzór Gaussa / iloczyn wektorowy)")
    if x.calls("atan2", "acos", "asin") or "M_PI" in x.c.texts:
        D.add(0.35, None, "funkcje trygonometryczne / kąty")
    pts = [s for s in x.c.structs if {f.name for f in x.c.structs[s]} >= {"x", "y"}]
    if pts:
        D.add(0.3, None, f"struktura punktu {pts[0]} (x, y)")
    if D.sig and x.name_hint("geometry"):
        D.add(0.15, None, "nazwy geometryczne")


def d_monotonic_stack(x: Ctx, D: Det):
    c = x.c
    for L in c.loops:
        if L.kind != "while" or not L.cond or L.parent is None:
            continue
        ct = normalize_expr(c.text(*L.cond))
        m = re.search(r"!(\w+)\.empty\(\)&&.*(\1\.top\(\)|\1\.back\(\))|(\w+)\.size\(\)(>0)?&&.*(\3\.top\(\)|\3\.back\(\))", ct)
        if not m:
            continue
        st = m.group(1) or m.group(3)
        body = normalize_expr(c.text(*L.body))
        if re.search(rf"{re.escape(st)}\.(pop|pop_back)\(\)", body):
            P = c.loops[L.parent]
            if re.search(rf"{re.escape(st)}\.(push|push_back|emplace|emplace_back)\(", normalize_expr(c.text(*P.body))):
                D.add(0.82, L.line, f"zdejmowanie z {st} elementów gorszych od bieżącego, potem wrzucenie bieżącego")


def d_recursion(x: Ctx, D: Det):
    for f in x.recursive:
        body = normalize_expr(x.c.text(f.open_idx, f.close_idx))
        D.add(0.6, f.line, f"{f.name}() wywołuje samą siebie")
        if re.match(r"\{if\(", body):
            D.add(0.15, f.line, "warunek bazowy na początku funkcji")
        break


def d_backtracking(x: Ctx, D: Det):
    c = x.c
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        fn = re.escape(f.name)
        if re.search(rf"(\w+)\[\w+\]=(true|1);{fn}\([^;]*\);\1\[\w+\]=(false|0);", body):
            D.add(0.82, f.line, f"{f.name}(): oznacz wybór → rekurencja → cofnij oznaczenie")
        elif re.search(rf"(\w+)\.push_back\([^;]*\);{fn}\([^;]*\);\1\.pop_back\(\);", body):
            D.add(0.8, f.line, f"{f.name}(): dodaj element → rekurencja → usuń (nawrót)")
        elif len(f.self_calls) == 2 and any(re.search(r"\w+\+1", normalize_expr(c.text(*cl.args[0])))
                                            for cl in f.self_calls if cl.args) and \
                re.search(rf"{fn}\(\w+\+1,[^;]*\+[^;]*\);{fn}\(\w+\+1,", body):
            D.add(0.6, f.line, f"{f.name}(i+1, …): bierzemy albo nie bierzemy elementu — przegląd podzbiorów")
            D.variant = "podzbiory"
    for cl in x.calls("next_permutation", "prev_permutation"):
        D.add(0.55, cl.line, "przegląd wszystkich permutacji (next_permutation)")
        D.variant = D.variant or "permutacje"
        break


def d_bitset(x: Ctx, D: Det):
    bs = x.decls("bitset")
    if bs:
        D.add(0.85, bs[0].line, f"bitset {bs[0].name}")


def d_scc(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]=min\(\1\[\2\],\1\[(\w+)\]\)")
    onst = x.find(r"(onstack|on_stack|nastosie|na_stosie|instack|in_stack)\w*\[")
    if m and (onst or x.find(r"if\(\w+\[\w+\]==\w+\[\w+\]\)\{?while")):
        D.add(0.8, m[0][1], "low-link (Tarjan): low[v] = min(low[v], low[u]) + stos")
    adj = _adj_lists(x)
    names = {a["name"] for a in adj}
    if len(names) >= 2:
        pairs = [(a, b) for a in adj for b in adj if a["name"] != b["name"] and a["line"] - b["line"] in (-1, 1)]
        for a, b in pairs:
            if a["arg"] and b["arg"] and re.search(r"\w+", a["arg"]) and a["arg"] != b["arg"]:
                if x.recursive and len(x.recursive) >= 2 or (x.recursive and x.calls("reverse")):
                    D.add(0.7, a["line"], "graf i graf odwrócony + dwa przejścia DFS (Kosaraju)")
                    break
    if D.sig and x.name_hint("scc"):
        D.add(0.1, None, "nazwy: low/spójne")


def d_bridges(x: Ctx, D: Det):
    m = x.find(r"if\((\w+)\[(\w+)\]>(\w+)\[(\w+)\]\)|if\((\w+)\[(\w+)\]>=(\w+)\[(\w+)\]\)")
    low = x.find(r"(\w+)\[(\w+)\]=min\(\1\[\2\],(\w+)\[(\w+)\]\)")
    if m and low and any(re.search(r"low|niski", g or "", re.I) for g in m[0][0].groups()):
        D.add(0.82, m[0][1], "porównanie low[u] z czasem wejścia — mosty/punkty artykulacji")


def d_bipartite(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]=(1-|3-|!)\1\[(\w+)\]|(\w+)\[(\w+)\]=\5\[(\w+)\]\^1")
    if m:
        D.add(0.6, m[0][1], "sąsiad dostaje przeciwny kolor")
        chk = x.find(r"if\((\w+)\[(\w+)\]==\1\[(\w+)\]\)")
        if chk:
            D.add(0.35, chk[0][1], "konflikt kolorów → graf nie jest dwudzielny")
    if D.sig and x.name_hint("bipartite"):
        D.add(0.1, None, "nazwy: kolor")


def d_flow(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]\[(\w+)\]-=(\w+);\1\[\3\]\[\2\]\+=\4|(\w+)\[(\w+)\]\.(cap|flow|c|f)(-=|\+=)\w+;\5\[\6\^1\]")
    if m:
        D.add(0.82, m[0][1], "aktualizacja sieci residualnej (krawędź i krawędź odwrotna)")
    if x.find(r"\blevel\[|\bpoziom\[") and x.find(r"\bptr\[|\bit\[") and D.sig:
        D.add(0.2, None, "poziomy BFS i wskaźniki krawędzi (Dinic)")
    if D.sig and x.name_hint("flow"):
        D.add(0.1, None, "nazwy: przepływ")


def d_matching(x: Ctx, D: Det):
    for f in x.recursive:
        body = normalize_expr(x.c.text(f.open_idx, f.close_idx))
        if re.search(rf"if\((\w+)\[(\w+)\]==-1\|\|{re.escape(f.name)}\(\1\[\2\]\)\)", body) or \
                re.search(rf"if\(!(\w+)\[(\w+)\]\|\|{re.escape(f.name)}\(\1\[\2\]\)\)", body):
            D.add(0.88, f.line, f"{f.name}(): ścieżka powiększająca (algorytm Kuhna)")


def d_mo(x: Ctx, D: Det):
    m = x.find(r"\w+\.\w+/(\w+)!=\w+\.\w+/\1|\w+/(BLOCK|BLOK|B|K|S|block|blok|sq|SQ)\w*<\w+/\2|"
               r"make_pair\(\w+\.\w+/\w+,\w+\.\w+\)<make_pair")
    if m and x.find(r"while\(\w+<\w+(\.\w+)?\)\w+\(\+\+\w+\)|while\(\w+>\w+(\.\w+)?\)\w+\(\w+--\)"):
        D.add(0.88, m[0][1], "zapytania posortowane po blokach + przesuwanie końców przedziału")
    elif m:
        D.add(0.4, m[0][1], "sortowanie zapytań po blokach")


def d_sqrt_decomp(x: Ctx, D: Det, mo_conf: float):
    bl = x.find(r"(\w+)=\(?(int|long long)?\)?sqrtl?\(\w+\)(\+1)?;|const int (\w+)=(3\d\d|4\d\d|5\d\d|7\d\d);")
    idx = x.find(r"\w+\[\w+/\w+\](\+=|=|\+\+|--)")
    if bl and idx:
        D.add(0.7, idx[0][1], "elementy pogrupowane w bloki rozmiaru ~√n")
    elif idx and x.name_hint("sqrt_decomp"):
        D.add(0.45, idx[0][1], "tablica bloków")
    if mo_conf >= 0.5:
        D.penalty = 0.5


def d_coord(x: Ctx, D: Det):
    m = x.find(r"(\w+)\.erase\(unique\(\1\.begin\(\),\1\.end\(\)\),\1\.end\(\)\)|"
               r"(\w+)\.resize\(unique\(\2\.begin\(\),\2\.end\(\)\)-\2\.begin\(\)\)")
    if m:
        D.add(0.65, m[0][1], "sortowanie + usunięcie duplikatów (unique)")
        nm = re.escape(m[0][0].group(1) or m[0][0].group(2))
        if x.find(rf"lower_bound\({nm}\.begin\(\),{nm}\.end\(\),[^)]*\)-{nm}\.begin\(\)"):
            D.add(0.3, None, "zamiana wartości na pozycję w posortowanej liście (lower_bound)")
    # numerowanie po sortowaniu par (wartość, indeks)
    r = x.find(r"if\(\w+!=0&&(\w+)\[(\w+)\]\.first==\1\[\2-1\]\.first\)\w+--|if\(\w+>0&&(\w+)\[(\w+)\]\.first!=\3\[\4-1\]\.first\)\w+\+\+|"
               r"if\((\w+)\[(\w+)\]\.first!=\5\[\6-1\]\.first\)\w+\+\+")
    if r:
        D.add(0.55, r[0][1], "nadawanie kolejnych numerów różnym wartościom po sortowaniu")
    else:
        rk = x.find(r"(\w+)\[\w+\]\.(first|second)=\w+(\+1)?;")
        sorts = x.calls("sort")
        if rk and len(sorts) >= 2 and x.find(r"swap\(\w+\[\w+\]\.first,\w+\[\w+\]\.second\)"):
            D.add(0.45, rk[0][1], "zastąpienie wartości ich rangami (po sortowaniu)")
    if D.sig and x.name_hint("coordinate_compression"):
        D.add(0.1, None, "nazwy: skalowanie")


def d_meet(x: Ctx, D: Det):
    if x.find(r"\w+/2") and len(x.find(r"<\(?1(LL)?<<\(?\w+(/2|-\w+)?\)?\)?;")) >= 2:
        D.add(0.75, x.first(r"<\(?1(LL)?<<"), "dwa przeglądy podzbiorów połówek + łączenie wyników")


def d_simulation(x: Ctx, D: Det):
    D.cap = 0.6
    if x.find(r"\w+\+=d[xy]\[\w+\]|\w+=\w+\+d[xy]\[\w+\]") and not _queue_loops(x):
        D.add(0.45, x.first(r"d[xy]\["), "ruch po planszy zgodnie z kierunkami")
    if x.find(r"\(\w+\+1\)%4|\(\w+\+3\)%4|kierunek|dir=") and x.find(r"while|for"):
        D.add(0.3, x.first(r"\(\w+\+1\)%4|\(\w+\+3\)%4|kierunek|dir="), "zmiana kierunku ruchu")
    if x.name_hint("simulation"):
        D.add(0.15, None, "nazwy: krok/ruch/symulacja")


def d_math(x: Ctx, D: Det):
    c = x.c
    D.cap = 0.85
    mod = [k for k, v in c.consts.items() if v in (1e9 + 7, 998244353.0, 1e9 + 9)]
    lit = x.find(r"%(1000000007|1000000009|998244353)\b|%\(?1e9\+7\)?")
    if mod or lit:
        D.add(0.3, (lit[0][1] if lit else c.const_lines.get(mod[0])), "arytmetyka modulo 10^9+7")
    m = x.find(r"for\([^;]*;\w+\*\w+<=\w+;[^)]*\)\{?if\(\w+%\w+==0\)")
    if m:
        D.add(0.6, m[0][1], "dzielniki do √n (test pierwszości / rozkład na czynniki)")
        D.variant = "dzielniki"
    m = x.find(r"while\(\w+%(\w+)==0\)\{?\w+/=\1")
    if m:
        D.add(0.55, m[0][1], "rozkład na czynniki pierwsze")
        D.variant = "rozkład na czynniki"
    m = x.find(r"(\w+)%10.{0,60}?\1/=10|(\w+)/=10.{0,40}?\2%10")
    if m:
        D.add(0.45, m[0][1], "operacje na cyfrach (x % 10, x / 10)")
        D.variant = D.variant or "cyfry liczby"
    if x.find(r"(przen|carry|reszta)\w*=\(?\w+(\+\w+)*\)?/10|(przen|carry)\w*=\w+/10"):
        D.add(0.5, x.first(r"(przen|carry|reszta)\w*=|carry"), "arytmetyka dużych liczb z przeniesieniem")
        D.variant = "duże liczby"
    if x.find(r"\w+\*\(\w+\+1\)/2|\w+\*\(\w+-1\)/2"):
        D.add(0.3, x.first(r"\w+\*\(\w+\+1\)/2|\w+\*\(\w+-1\)/2"), "wzór na sumę ciągu arytmetycznego")
    if re.search(r"\bnwd\b|\bnww\b|pierwsz|dzielnik|modulo|parzyst", x.comment_text):
        D.add(0.1, None, "komentarze matematyczne")


def d_combinatorics(x: Ctx, D: Det):
    m = x.find(r"(\w+)\[(\w+)\]=\(?\1\[\2-1\]\*\2(%\w+|\)%\w+)?;")
    if m:
        D.add(0.6, m[0][1], "silnie: f[i] = f[i−1]·i")
    m = x.find(r"(\w+)\[(\w+)\]\[(\w+)\]=\(?\1\[\2-1\]\[\3-1\]\+\1\[\2-1\]\[\3\]")
    if m:
        D.add(0.82, m[0][1], "trójkąt Pascala: C[i][j] = C[i−1][j−1] + C[i−1][j]")
    if x.name_hint("combinatorics") and D.sig:
        D.add(0.15, None, "nazwy: silnia/dwumian")
    elif x.name_hint("combinatorics"):
        D.add(0.3, None, "nazwy: silnia/dwumian")


def d_strings(x: Ctx, D: Det):
    c = x.c
    strs = [d for d in c.decls if d.kind == "string"]
    reads = [io for io in c.io if io.kind == "read" and any(
        (c.decl_of(re.match(r"\w+", t).group(0)) or None) and c.decl_of(re.match(r"\w+", t).group(0)).kind == "string"
        for t in io.targets if re.match(r"\w+", t))]
    char_ops = x.find(r"\w+\[\w+\]-'[aA0]'|'[a-zA-Z0-9]'(<=|>=)\w+\[\w+\]|\w+\[\w+\](<=|>=|==|!=)'[^']+'|"
                      r"(isalpha|isdigit|isupper|islower|tolower|toupper|isspace)\(")
    if strs and (reads or c.io and any(io.api == "getline" for io in c.io)):
        D.add(0.3, strs[0].line, "wczytywanie napisu")
    if char_ops:
        D.add(0.45, char_ops[0][1], "operacje na pojedynczych znakach")
    elif strs:
        cc = x.find(r"\w+(==|!=)'[^']'")
        if cc:
            D.add(0.3, cc[0][1], "porównania ze znakami")
    m = x.find(r"(\w+)\[\w+\[\w+\]-'[aA]'\]\+\+")
    if m:
        D.add(0.3, m[0][1], "zliczanie wystąpień liter")
    ascii_ops = x.find(r"\w+(\[\w+\])?(<|>|<=|>=)'[a-zA-Z]'|-=?(65|97)\b|\+=?26\b|-=26\b|=\s?'[aA]';\w+<=?'[zZ]'|"
                       r"char\(\w+\)|\(char\)")
    for mm, ln in x.find(r"\w+\[(\w+)\[\w+\]\](\+\+|\+=1)"):
        d_ = c.decl_of(mm.group(1))
        if d_ is not None and d_.kind == "string":
            ascii_ops = ascii_ops or [(mm, ln)]
    if ascii_ops:
        D.add(0.4, ascii_ops[0][1], "arytmetyka na kodach znaków (litery, przesunięcia)")
    loops_s = [L for L in c.loops if L.cond and re.search(r"<\w+\.(size|length)\(\)", normalize_expr(c.text(*L.cond)))
               and any(d.kind == "string" for d in c.decls if re.search(rf"<{re.escape(d.name)}\.(size|length)",
                                                                       normalize_expr(c.text(*L.cond))))]
    if loops_s:
        D.add(0.3, loops_s[0].line, "pętla po znakach napisu")
    meth = [cl for cl in c.calls if cl.is_method and cl.name in ("substr", "find", "rfind", "replace", "append",
                                                                  "compare", "c_str", "length")]
    if meth:
        D.add(0.25, meth[0].line, f"metody napisów ({meth[0].name})")
    if x.find(r"\(\w+(\[\w+\])?-'[aA]'\+\w+\)%26"):
        D.add(0.4, x.first(r"%26"), "przesunięcie liter modulo 26 (szyfr Cezara)")
        D.variant = "szyfr przesuwający"


def d_graph(x: Ctx, D: Det):
    c = x.c
    adj = _adj_lists(x)
    rd = [a for a in adj if a["reads"]]
    if rd:
        g = rd[0]["name"]
        D.add(0.5, rd[0]["line"], f"lista sąsiedztwa {g} budowana z wczytanych krawędzi")
        names = [a for a in rd if a["name"] == g]
        if len(names) >= 2:
            D.add(0.3, names[1]["line"], "krawędzie dodawane w obie strony (graf nieskierowany)")
        strong = any(q["adj"] for q in _queue_loops(x)) or any(
            _adj_of(x, c.loops[c.innermost_loop(cl.idx)]) == g for f in x.recursive for cl in f.self_calls
            if c.innermost_loop(cl.idx) is not None)
        trav = strong or any(_adj_of(x, L) == g for L in c.loops)
        if strong:
            D.add(0.35, None, "przechodzenie grafu po sąsiadach (BFS/DFS)")
        elif trav:
            D.add(0.1, None, "przegląd list sąsiedztwa")
        trav = strong
        # kubełki (sortowanie kubełkowe): przegląd całego zakresu indeksów bez przechodzenia po sąsiadach
        if not trav and len(names) < 2 and not x.recursive:
            D.penalty = 0.6
    elif adj:
        D.add(0.3, adj[0]["line"], f"kontener list {adj[0]['name']}")
    m = x.find(r"(\w+)\[(\w+)(-1)?\]\[(\w+)(-1)?\]=(1|true);(\1\[\4(-1)?\]\[\2(-1)?\]=(1|true);)?")
    if m:
        d = x.c.decl_of(m[0][0].group(1))
        if d and (d.kind == "array" and len(d.dims) == 2 or d.nested >= 2):
            k = x.c.innermost_loop(_tok_at(x, m[0][0].start()))
            if k is not None and any(io.kind == "read" and x.c.loops[k].body[0] <= io.idx <= x.c.loops[k].body[1]
                                     for io in x.c.io):
                D.add(0.6, m[0][1], "macierz sąsiedztwa wypełniana z wczytanych krawędzi")


def _tok_at(x: Ctx, off: int) -> int:
    return max(0, bisect.bisect_right(x.offs, off) - 1)


def d_tree(x: Ctx, D: Det):
    c = x.c
    adj = _adj_lists(x)
    for a in adj:
        if not a["reads"]:
            continue
        b = a["bound"]
        init = a["init"]
        if re.search(r"<\w+-1$|<=\w+-1$|<=\w+-2$", b) or (re.search(r"=1$|=2$", init) and re.search(r"<\w+$|<=\w+$", b)):
            D.add(0.55, a["line"], "wczytywanie n−1 krawędzi (drzewo)")
            break
    for f in x.recursive:
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        if len(f.params) >= 2 and re.search(rf"if\(\w+==({re.escape(f.params[1].name)})\)continue|if\(\w+!={re.escape(f.params[1].name)}\)",
                                            body):
            D.add(0.4, f.line, f"{f.name}(v, rodzic): pomijanie krawędzi do rodzica")
        if re.search(r"(\w+)\[(\w+)\]\+=\1\[(\w+)\]", body):
            D.add(0.35, f.line, "rozmiar poddrzewa sumowany z dzieci")
        if re.search(r"(\w+)\[(\w+)\]=(\w+)(\+\+)?;.*\3(\+\+)?;.*\w+\[\2\]=\3", body) or \
                re.search(r"(\w+)\[\w+\]=(\w+);\2\+\+;", body):
            D.add(0.3, f.line, "numeracja wierzchołków w kolejności wejścia/wyjścia (pre/post-order)")
    tchk = x.find(r"if\(\w+!=\w+-1\)|if\(\w+==\w+-1\)|if\(\w+-1!=\w+\)")
    if tchk and _adj_lists(x):
        D.add(0.45, tchk[0][1], "sprawdzenie warunku drzewa: m = n − 1 krawędzi")
    m = x.find(r"(\w+)\[(\w+)\]=\1\[(\w+)\]\+1")
    if m and (D.sig or x.recursive):
        D.add(0.15, m[0][1], "głębokość = głębokość rodzica + 1")
    for io in c.io:
        if io.kind != "read":
            continue
        k = c.innermost_loop(io.idx)
        if k is None:
            continue
        L = c.loops[k]
        init = normalize_expr(c.text(*L.init)) if L.init else ""
        if re.search(r"=1$|=2$", init) and adj and any(L.body[0] <= a["call"].idx <= L.body[1] and
                                                      re.fullmatch(r"\w+", a["arg"] or "") and a["arg"] == L.var
                                                      for a in adj):
            D.add(0.5, L.line, "wczytywanie rodzica każdego wierzchołka")
            break
    if D.sig and x.name_hint("tree"):
        D.add(0.08, None, "nazwy: drzewo/rodzic")


def d_grid(x: Ctx, D: Det):
    c = x.c
    d2 = [d for d in c.decls if (d.kind == "array" and len(d.dims) == 2) or (d.kind == "vector" and d.nested >= 2
                                                                                and "vector" in d.elem and "pair" not in d.type)
          or (d.kind == "vector" and d.elem == "string") or (d.kind == "array" and d.elem == "string")]
    readgrid = False
    for io in c.io:
        if io.kind != "read":
            continue
        ls = c.loops_containing(io.idx)
        if len(ls) >= 2 and any(re.fullmatch(r"\w+\[\w+\]\[\w+\]", normalize_expr(t)) for t in io.targets):
            readgrid = True
            nm = re.match(r"\s*(\w+)", io.targets[0])
            dd = c.decl_of(nm.group(1)) if nm else None
            charish = dd is not None and re.search(r"char|bool|string", dd.type or "")
            D.add(0.42 if charish else 0.25, io.line, "wczytywanie planszy pole po polu" if charish else
                  "wczytywanie tablicy dwuwymiarowej")
            break
        if len(ls) >= 1 and any(re.fullmatch(r"\w+\[\w+\]", normalize_expr(t)) and
                                (c.decl_of(re.match(r"\w+", t).group(0)) or None) and
                                c.decl_of(re.match(r"\w+", t).group(0)).elem == "string" for t in io.targets):
            readgrid = True
            D.add(0.4, io.line, "wczytywanie planszy wiersz po wierszu")
            break
    if x.find(r"\bd[xy]\[\w*\]=\{|int d[xy]\[\]|\bd[xy]\[4\]|\bd[xy]\[8\]|\bdir\[4\]\[2\]"):
        D.add(0.5, x.first(r"d[xy]\[|dir\["), "tablice kierunków dx/dy")
    nb = x.find(r"\w+>=0&&\w+<\w+&&\w+>=0&&\w+<\w+|\w+<0\|\|\w+>=\w+\|\|\w+<0\|\|\w+>=\w+|"
                r"\w+>0(and|&&)\w+\[\w+\]\[\w+\]")
    if nb and (d2 or readgrid):
        D.add(0.35, nb[0][1], "sprawdzanie, czy pole mieści się na planszy")
    nbs = [x.find(r"\w+\[\w+-1\]\[\w+\]"), x.find(r"\w+\[\w+\+1\]\[\w+\]"), x.find(r"\w+\[\w+\]\[\w+-1\]"),
           x.find(r"\w+\[\w+\]\[\w+\+1\]")]
    if sum(1 for z in nbs if z) >= 3 and d2:
        D.add(0.45 if not readgrid else 0.3, next(z for z in nbs if z)[0][1], "sąsiednie pola (góra/dół/lewo/prawo)")
    if D.sig and x.name_hint("grid"):
        D.add(0.08, None, "nazwy: plansza")


# ============================================================================================== detect
_ORDER = ["dijkstra", "bellman_ford", "floyd", "bfs", "dfs", "topo_sort", "dsu", "mst", "lca", "scc", "bridges",
          "bipartite", "flow", "matching", "segment_tree", "lazy_segment_tree", "fenwick", "sparse_table", "mo",
          "sqrt_decomp", "kmp", "z_function", "hashing", "trie", "manacher", "sieve", "fast_pow", "mod_inverse",
          "gcd", "matrix_pow", "convex_hull", "geometry", "binary_search", "two_pointers", "sliding_window",
          "prefix_sums", "monotonic_stack", "coordinate_compression", "meet_in_middle", "sorting", "bitmask_dp", "dp",
          "backtracking", "recursion", "bitset", "combinatorics", "math", "string_processing", "graph", "tree", "grid",
          "greedy", "simulation"]


def analyze(code: str) -> dict:
    """Wszystkie detektory z pełną listą sygnałów (także poniżej progu) — do debugowania i kalibracji."""
    if Code is None or not (code or "").strip():
        return {}
    try:
        x = Ctx(code)
    except Exception:  # noqa: BLE001
        return {}
    dets: dict[str, Det] = {aid: Det(aid) for aid in IDS}
    simple = {
        "binary_search": d_binary_search, "two_pointers": d_two_pointers, "sliding_window": d_sliding_window,
        "prefix_sums": d_prefix_sums, "sorting": d_sorting, "greedy": d_greedy, "dp": d_dp,
        "bitmask_dp": d_bitmask_dp, "bfs": d_bfs, "dfs": d_dfs, "dijkstra": d_dijkstra,
        "bellman_ford": d_bellman_ford, "floyd": d_floyd, "topo_sort": d_topo_sort, "dsu": d_dsu, "lca": d_lca,
        "segment_tree": d_segment_tree, "fenwick": d_fenwick, "sparse_table": d_sparse_table, "kmp": d_kmp,
        "z_function": d_z_function, "hashing": d_hashing, "trie": d_trie, "manacher": d_manacher, "sieve": d_sieve,
        "fast_pow": d_fast_pow, "gcd": d_gcd, "convex_hull": d_convex_hull, "geometry": d_geometry,
        "monotonic_stack": d_monotonic_stack, "recursion": d_recursion, "backtracking": d_backtracking,
        "bitset": d_bitset, "scc": d_scc, "bridges": d_bridges, "bipartite": d_bipartite, "flow": d_flow,
        "matching": d_matching, "mo": d_mo, "coordinate_compression": d_coord, "meet_in_middle": d_meet,
        "simulation": d_simulation, "math": d_math, "combinatorics": d_combinatorics,
        "string_processing": d_strings, "graph": d_graph, "tree": d_tree, "grid": d_grid,
    }
    for aid, fn in simple.items():
        try:
            fn(x, dets[aid])
        except Exception:  # noqa: BLE001 — jeden detektor nie może zepsuć reszty
            pass
    # detektory zależne od innych
    try:
        d_mst(x, dets["mst"], dets["dsu"].conf())
        d_lazy(x, dets["lazy_segment_tree"], dets["segment_tree"].conf())
        d_mod_inverse(x, dets["mod_inverse"], dets["fast_pow"].conf())
        d_matrix_pow(x, dets["matrix_pow"], dets["fast_pow"].conf())
        d_sqrt_decomp(x, dets["sqrt_decomp"], dets["mo"].conf())
    except Exception:  # noqa: BLE001
        pass
    _interplay(x, dets)
    return dets


def _interplay(x: Ctx, d: dict):
    """Relacje między algorytmami (żeby ogólne etykiety nie zagłuszały konkretnych)."""
    conf = {k: v.conf() for k, v in d.items()}
    # BFS z kopcem to Dijkstra
    if conf["dijkstra"] >= 0.6:
        d["bfs"].penalty *= 0.5
    # DSU/DFS/merge sort/drzewo przedziałowe/potęgowanie tłumaczą rekurencję
    if max(conf["dsu"], conf["dfs"], conf["segment_tree"], conf["fast_pow"], conf["gcd"]) >= 0.6 or \
            d["sorting"].variant == "przez scalanie":
        d["recursion"].penalty *= 0.75
    if conf["lazy_segment_tree"] >= 0.6:
        d["segment_tree"].penalty *= 1.0
    if conf["mst"] >= 0.5 and d["mst"].variant == "Prim":
        d["dijkstra"].penalty *= 0.6
    # konkretne algorytmy, które formalnie są DP — nie podbijaj ogólnej etykiety
    if max(conf["z_function"], conf["kmp"], conf["manacher"], conf["hashing"], conf["sparse_table"],
           conf["combinatorics"], conf["bipartite"], conf["bellman_ford"], conf["lca"]) >= 0.7:
        d["dp"].penalty *= 0.6
    if max(conf["scc"], conf["bridges"]) >= 0.7:
        d["mst"].penalty *= 0.3
    if conf["floyd"] >= 0.8:
        d["dp"].penalty *= 0.75
    if conf["coordinate_compression"] >= 0.7 and not d["binary_search"].has("pętla"):
        d["binary_search"].penalty *= 0.7
    # sumy prefiksowe vs DP: jeśli tylko jedna transformacja prefiksowa, DP słabsze
    if conf["prefix_sums"] >= 0.7 and conf["dp"] < 0.6:
        d["dp"].penalty *= 0.8
    # toposort Kahna używa kolejki — BFS w tle
    if conf["topo_sort"] >= 0.6:
        d["bfs"].penalty *= 0.7
    # gąsienica: okno przesuwne jako uzupełnienie
    if conf["two_pointers"] < 0.4 and conf["sliding_window"] >= 0.4 and d["sliding_window"].has("agregat"):
        d["sliding_window"].penalty *= 0.8
    # drzewo/plansza jako ogólne etykiety — graf zawsze przy drzewie
    if conf["tree"] >= 0.5 and conf["graph"] < 0.35:
        d["graph"].add(0.4, None, "drzewo to szczególny graf")
    if conf["topo_sort"] >= 0.6 and conf["graph"] < 0.5:
        d["graph"].add(0.45, None, "sortowanie topologiczne działa na grafie skierowanym")
    if conf["grid"] >= 0.35 and conf["string_processing"] < 0.6 and not any(
            dd.kind == "string" for dd in x.c.decls if not dd.is_param):
        d["string_processing"].penalty *= 0.7
    if conf["grid"] >= 0.35 and max(conf["bfs"], conf["dfs"]) >= 0.5:
        d["graph"].add(0.4, None, "plansza jako graf (pola = wierzchołki, sąsiednie pola = krawędzie)")
    elif conf["dfs"] >= 0.45 and conf["graph"] < 0.35:
        d["graph"].add(0.38, None, "graf niejawny przeszukiwany rekurencyjnie")


def detect(code: str) -> list[dict]:
    """Rozpoznane algorytmy posortowane malejąco po pewności (tylko powyżej progu)."""
    dets = analyze(code)
    if not dets:
        return []
    out = []
    for aid in _ORDER:
        D = dets.get(aid)
        if D is None or not D.sig:
            continue
        cf = D.conf()
        if cf < THRESHOLD:
            continue
        lines = sorted({ln for _, ln, _ in D.sig if ln})[:8]
        ev = []
        for w, _, e in sorted(D.sig, key=lambda s: -s[0]):
            if e not in ev:
                ev.append(e)
        item = {"id": aid, "name": NAMES[aid], "confidence": round(cf, 2), "lines": lines, "evidence": ev[:6]}
        if D.variant:
            item["variant"] = D.variant
        out.append(item)
    out.sort(key=lambda r: (-r["confidence"], _ORDER.index(r["id"])))
    return out


def detect_ids(code: str, min_conf: float = THRESHOLD) -> list[str]:
    return [r["id"] for r in detect(code) if r["confidence"] >= min_conf]


# ============================================================================================== kalibracja
# Tabele kalibracji (nazwy rund i zadań z konkretnych konkursów, ręcznie sprawdzone zgłoszenia) to dane autora —
# są w osobnym, prywatnym pliku, który nie trafia do wersji publicznej. Do samego wykrywania nie są potrzebne;
# korzysta z nich tylko raport `python3 -m oiforge.core.algodetect --eval`.
try:
    from ._algodetect_private import LABEL_NOISE, PROBLEM_LABELS, ROUND_ALLOWED, ROUND_LABELS
except ImportError:
    ROUND_LABELS: dict = {}
    ROUND_ALLOWED: dict = {}
    PROBLEM_LABELS: dict = {}
    LABEL_NOISE: dict = {}
HAVE_CALIBRATION = bool(ROUND_LABELS)


def evaluate(index_path: str | None = None, min_conf: float = THRESHOLD, verbose: bool = False,
             raw: bool = False) -> dict:
    """Precision/recall per algorytm na programach ucznia (etykiety z nazw rund). Zwraca słownik z raportem."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent / "user_data" / "programs"
    idx = Path(index_path) if index_path else root / "index.json"
    data = json.loads(idx.read_text(encoding="utf-8"))
    progs = data.get("programs", data) if isinstance(data, dict) else data
    # propagacja etykiet po nazwie zadania do rund bez tematu
    by_problem = {}
    for p in progs:
        r = p.get("round")
        if r in ROUND_LABELS:
            by_problem.setdefault(p["problem_name"].lower(), r)
    stats = {aid: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for aid in IDS}
    details = []
    for p in progs:
        r = p.get("round")
        expected, allowed, labeled = set(), set(), False
        if r in ROUND_LABELS:
            expected, allowed = set(ROUND_LABELS[r][0]), set(ROUND_LABELS[r][1])
            labeled = True
        elif r in ROUND_ALLOWED:
            allowed = set(ROUND_ALLOWED[r])
            labeled = True
        elif p["problem_name"].lower() in by_problem:
            rr = by_problem[p["problem_name"].lower()]
            expected, allowed = set(ROUND_LABELS[rr][0]), set(ROUND_LABELS[rr][1])
            labeled = True
        if p["problem_name"] in PROBLEM_LABELS and labeled:
            extra = PROBLEM_LABELS[p["problem_name"]]
            if extra:
                allowed |= expected
                expected = expected | extra if r in ROUND_LABELS else set(extra)
                allowed |= expected
        fname = p["path"].rsplit("/", 1)[-1]
        if fname in LABEL_NOISE and not raw:
            expected = set()
            allowed |= set(ROUND_LABELS.get(r, (set(), set()))[0])
        if not labeled:
            continue
        try:
            code = (root / p["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        got = {d["id"] for d in detect(code) if d["confidence"] >= min_conf}
        ok_set = expected | allowed
        for aid in expected:
            stats[aid]["support"] += 1
            if aid in got:
                stats[aid]["tp"] += 1
            else:
                stats[aid]["fn"] += 1
        for aid in got:
            if aid not in ok_set:
                stats[aid]["fp"] += 1
            elif aid not in expected:
                pass
        details.append({"path": p["path"], "round": r, "problem": p["problem_name"], "expected": sorted(expected),
                        "got": sorted(got), "missing": sorted(expected - got), "unexpected": sorted(got - ok_set)})
    report = {}
    for aid, s in stats.items():
        det_correct = s["tp"]
        prec_den = s["tp"] + s["fp"]
        rec_den = s["tp"] + s["fn"]
        if s["support"] == 0 and s["fp"] == 0:
            continue
        report[aid] = {"precision": round(det_correct / prec_den, 3) if prec_den else None,
                       "recall": round(s["tp"] / rec_den, 3) if rec_den else None, **s}
    return {"per_algorithm": report, "programs": len(details), "details": details}


def _main(argv):
    if "--eval" in argv:
        res = evaluate(verbose="-v" in argv, raw="--raw" in argv)
        print(f"Programów z etykietami: {res['programs']}")
        print(f"{'algorytm':24s} {'precision':>9s} {'recall':>7s}  tp  fp  fn  support")
        for aid, r in sorted(res["per_algorithm"].items(), key=lambda kv: -kv[1]["support"]):
            p = "-" if r["precision"] is None else f"{r['precision']:.2f}"
            rc = "-" if r["recall"] is None else f"{r['recall']:.2f}"
            print(f"{aid:24s} {p:>9s} {rc:>7s} {r['tp']:3d} {r['fp']:3d} {r['fn']:3d} {r['support']:5d}")
        if "-v" in argv:
            for d in res["details"]:
                if d["missing"] or d["unexpected"]:
                    print(f"  {d['path'].split('/')[-1]:28s} [{d['round']}] brak={d['missing']} nadmiar={d['unexpected']}"
                          f" wykryto={d['got']}")
        return 0
    code = open(argv[1], encoding="utf-8").read() if len(argv) > 1 else sys.stdin.read()
    for r in detect(code):
        print(f"{r['confidence']:.2f}  {r['id']:22s} {r['name']}  linie {r['lines']}  {r.get('variant', '')}")
        for e in r["evidence"]:
            print(f"        · {e}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

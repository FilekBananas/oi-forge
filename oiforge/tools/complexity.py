"""(g) Analiza złożoności czasowej i pamięciowej (worst-case) + pomiar empiryczny.

Statycznie: drzewo pętli z granicami (n, m, q, stałe, √n, log przez ×2 i /2, suma harmoniczna `j += i`,
pętle zależne `j < i` → n²/2, dwa wskaźniki i stos monotoniczny → amortyzacja, przegląd grafu V+E),
koszty STL (sort, set/map/priority_queue, lower_bound, unordered_*), rekurencja (twierdzenie o rekurencji
uniwersalnej, rekurencja ze spamiętywaniem = stany × przejście, DFS po grafie, find w DSU), hot-spoty per linia,
szacowana liczba operacji i werdykt dla limitu czasu, pamięć (tablice, vectory, mapy) vs limit.

Empirycznie (zadanie w tle): program uruchamiany dla rosnących rozmiarów, dopasowanie modeli
(log n, √n, n, n log n, n², n³, 2^n) metodą najmniejszych kwadratów + nachylenie log-log.

HTTP:
  POST /api/complexity/analyze {code, constraints?, statement?, time_limit?, memory_limit?}
  POST /api/complexity/measure {code, generator? | input_template?, sizes?, time_limit?}   (job)
  POST /api/complexity/parse_constraints {text}
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from oiforge.server_api import ApiError
from oiforge.tools._cppstruct import Code, Cost, Fac, Term, fmt_num, fmt_ops, normalize_expr

try:
    from oiforge.core import jobs, runner, store
except Exception:  # noqa: BLE001 — pozwala używać analizy bez reszty rdzenia (testy)
    jobs = runner = store = None

OPS_PER_SEC = 2e8          # „proste operacje” na sekundę (reguła kciuka dla C++ -O2)
WARN_FACTOR = 3.0          # do 3× budżetu = ryzykowne (stała ma znaczenie)
DEFAULT_SIZE = 1e5         # gdy nie ma ograniczeń
DEFAULT_VALUE = 1e9

TEST_COUNT_NAMES = {"t", "T", "tests", "testy", "z", "Z", "tt", "tc", "TC", "liczba_testow"}

# ---------------------------------------------------------------------------------------------- koszty STL
LOG_KINDS = {"set", "multiset", "map", "multimap"}
LOG_METHODS = {"insert", "emplace", "emplace_hint", "erase", "find", "count", "lower_bound", "upper_bound",
               "equal_range", "at", "contains"}
HASH_KINDS = {"umap", "uset"}
HASH_METHODS = {"insert", "emplace", "erase", "find", "count", "at", "contains"}
PQ_LOG = {"push", "emplace", "pop"}
LINEAR_FREE = {"reverse", "fill", "iota", "accumulate", "count", "count_if", "find", "find_if", "max_element",
               "min_element", "minmax_element", "unique", "copy", "partial_sum", "adjacent_difference", "rotate",
               "is_sorted", "equal", "merge", "inplace_merge", "remove", "remove_if", "replace", "transform",
               "for_each", "all_of", "any_of", "none_of", "shuffle", "random_shuffle", "set_union",
               "set_intersection", "set_difference", "includes", "nth_element", "fill_n", "copy_n", "reduce",
               "inner_product", "is_permutation", "lexicographical_compare", "search", "mismatch"}
NLOGN_FREE = {"sort", "stable_sort", "partial_sort", "make_heap", "sort_heap"}
LOG_FREE = {"lower_bound", "upper_bound", "binary_search", "equal_range", "partition_point", "push_heap",
            "pop_heap"}


@dataclass
class Mag:
    cost: Cost
    deps: set = field(default_factory=set)
    flags: set = field(default_factory=set)
    num: float | None = None


@dataclass
class LoopInfo:
    count: Cost
    pattern: str = "linear"
    note: str = ""
    deps: set = field(default_factory=set)
    confidence: float = 0.8
    amort_over: int | None = None
    amort_total: Cost | None = None


@dataclass
class FuncInfo:
    per_call: Cost
    one_time: Cost | None = None
    work: Cost | None = None
    recursion: dict | None = None
    depth: Cost | None = None
    size_params: list = field(default_factory=list)
    reset_arrays: list = field(default_factory=list)


def _one() -> Cost:
    return Cost.const(1)


def _inv(c: Cost) -> Cost:
    d = c.dominant()
    if d is None:
        return _one()
    return Cost([Term(1.0 / max(d.coef, 1e-9), {k: Fac(-v.p, -v.l, -v.ll, 0, 0) for k, v in d.f.items()})])


def _main_var(c: Cost) -> str | None:
    d = c.dominant()
    if d is None or not d.f:
        return None
    return max(d.f.items(), key=lambda kv: kv[1].key())[0]


def _log_of(c: Cost, coef: float = 1.0) -> Cost:
    """log₂ z kosztu (log n dla n, log C dla stałej C, liczba dla wartości liczbowej)."""
    if c.is_const():
        v = c.const_value()
        return Cost.const(max(1.0, math.log2(max(v, 2.0))) * coef)
    mv = _main_var(c)
    return Cost.log(mv, coef) if mv else Cost.const(coef)


def _sqrt_of(c: Cost) -> Cost:
    if c.is_const():
        return Cost.const(math.sqrt(max(c.const_value(), 1.0)))
    d = c.dominant()
    return Cost([Term(math.sqrt(max(d.coef, 1e-9)), {k: Fac(v.p / 2, v.l / 2, v.ll / 2, 0, 0)
                                                    for k, v in d.f.items()})])


# ============================================================================================== analizator
class Analyzer:
    def __init__(self, code: str, values: dict | None = None):
        self.src_offset = 0
        c = Code(code)
        if not c.functions and c.n:
            c = Code("int main() {\n" + code + "\n}")
            self.src_offset = 1
        self.c = c
        self.syms: dict[str, dict] = {}
        self.notes: list[str] = []
        self.warnings: list[dict] = []
        self.loop_info: dict[int, LoopInfo] = {}
        self._mult: dict[int, Cost] = {}
        self.func_info: dict[str, FuncInfo] = {}
        self._csize: dict[str, Cost] = {}
        self._busy: set = set()
        self._busy_funcs: set = set()
        self.values_hint = values or {}
        self.read_targets = self._read_target_names()
        self.iter_reads = self._iter_read_names()   # wartości wczytywane w pętli (np. l, r zapytania) — nie rozmiary
        self._index_params: dict = {}             # (funkcja, parametr) -> dziedzina (parametr = granica przedziału w tablicy)
        self._gcd_funcs: dict = {}                # funkcja -> (a, b): NWD w pętli while (b) {...}
        self._pq_graph: dict = {}                 # nazwa kolejki priorytetowej w Dijkstrze -> liczba wierzchołków
        self.one_time: list[tuple] = []        # (func, Cost, line)
        self.line_cost: dict[int, Cost] = {}   # linia -> koszt (względem wywołania funkcji)
        self.line_func: dict[int, str] = {}
        self.call_costs: list[dict] = []

    def _hint(self, name: str) -> float:
        if name in self.values_hint:
            return float(self.values_hint[name])
        s = self.syms.get(name)
        if s and s.get("value"):
            return float(s["value"])
        if name in self.c.consts:
            return float(self.c.consts[name])
        return DEFAULT_SIZE

    def _log(self, c: Cost, coef: float = 1.0) -> Cost:
        """log₂ kosztu; przy sumie wybiera składnik o największej wartości (dla podpowiedzi ograniczeń)."""
        if c.is_const() or len(c.terms) <= 1:
            return _log_of(c, coef)
        best = max(c.terms, key=lambda t: t.value({k: self._hint(k) for k in t.f}))
        return _log_of(Cost([best]), coef)

    # ------------------------------------------------------------------ symbole
    def sym(self, name: str, kind: str = "input", value: float | None = None, line: int | None = None,
            source: str = "") -> str:
        if name not in self.syms:
            self.syms[name] = {"name": name, "kind": kind, "value": value, "line": line, "source": source}
        return name

    def _read_target_names(self) -> set:
        s = set()
        for io in self.c.io:
            if io.kind != "read":
                continue
            for t in io.targets:
                m = re.match(r"\s*&?\s*([A-Za-z_]\w*)", t)
                if m:
                    s.add(m.group(1))
        return s

    def _is_test_loop(self, k: int) -> bool:
        """Pętla po zestawach testowych: while (t--), for (tc = 0; tc < T; tc++)."""
        L = self.c.loops[k]
        part = L.cond if L.cond else None
        if not part or part[0] > part[1]:
            return False
        ids = set(self.c.ids_in(*part))
        if L.kind == "for":
            bound = ids - {L.var}             # for (int t = 0; t < q; t++) — t to tylko zmienna pętli
            return bool(bound & TEST_COUNT_NAMES) and len(bound) <= 1
        return bool(ids & TEST_COUNT_NAMES) and len(ids) <= 1

    def _iter_read_names(self) -> set:
        """Zmienne wczytywane wyłącznie wewnątrz pętli (poza pętlą po zestawach testowych) — to WARTOŚCI
        (np. l, r, x z zapytania), a nie rozmiary danych."""
        c = self.c
        inside, outside = set(), set()
        for io in c.io:
            if io.kind != "read":
                continue
            ks = [k for k in c.loops_containing(io.idx) if not self._is_test_loop(k)]
            for t in io.targets:
                m = re.match(r"\s*&?\s*([A-Za-z_]\w*)\s*$", t)
                if m:
                    (inside if ks else outside).add(m.group(1))
        return inside - outside

    def _input_mag(self, name: str) -> Mag:
        kind = "test" if name in TEST_COUNT_NAMES else "input"
        if name in self.iter_reads:
            self.sym(name, "value", None, None, "wartość wczytywana w pętli (np. w zapytaniu)")
            return Mag(Cost.var(name), set(), {"input", "itervalue"})
        self.sym(name, kind, None, None, "wczytywana z wejścia" if name in self.read_targets else "zmienna")
        return Mag(Cost.var(name), set(), {"input"})

    def _indexed_array(self, k: int, var: str | None) -> str | None:
        """Tablica indeksowana zmienną sterującą pętli k (a[i], a[i+1], a[i-1]) w jej ciele."""
        r = self._indexed_dim(k, var)
        return r[0] if r else None

    def _indexed_dim(self, k: int, var: str | None):
        """(tablica, numer wymiaru) dla dostępu a[i] / t[x][i] zmienną sterującą pętli k w jej ciele."""
        if not var:
            return None
        c = self.c
        L = c.loops[k]
        a, b = L.body
        T = c.texts
        for i in range(a, min(b, c.n - 1)):
            if c.toks[i].kind != "id" or T[i + 1] != "[" or (i + 1) not in c.pairs:
                continue
            if i > 0 and T[i - 1] in (".", "->"):
                continue
            j = i + 1
            pos = 0
            while j < c.n and T[j] == "[" and j in c.pairs:
                inner = normalize_expr(c.text(j + 1, c.pairs[j] - 1))
                if re.fullmatch(rf"{re.escape(var)}([+-]\d+)?", inner):
                    d = c.decl_of(T[i], i)
                    if d is not None and d.kind in ("array", "vector", "string", "deque", "stdarray"):
                        return T[i], pos
                j = c.pairs[j] + 1
                pos += 1
        return None

    def _dim_size(self, arr: str, pos: int, at: int) -> Cost:
        """Rozmiar wymiaru pos tablicy arr (stały wymiar planszy → wczytane n / m)."""
        c = self.c
        if pos == 0:
            return self._domain(self.csize(arr, at))
        d = c.decl_of(arr, at)
        dm = None
        if d is not None and d.kind == "array" and pos < len(d.dims):
            dm = self._text_mag(d.dims[pos], d.idx).cost
        elif d is not None and d.kind == "vector" and pos == 1 and len(d.ctor) >= 2:
            m2 = re.search(r"\(([^,()]+)", d.ctor[1])
            dm = self._text_mag(m2.group(1), d.idx).cost if m2 else None
        if dm is None:
            dm = self.elem_size(arr, at)
        if dm.is_const() and dm.const_value() > 64 and pos < 2 and ("n", "m")[pos] in self.read_targets:
            return Cost.var(self.sym(("n", "m")[pos], "input"))
        return dm

    def line_of(self, idx: int) -> int:
        return self.c.toks[idx].line - self.src_offset if 0 <= idx < self.c.n else 0

    # ------------------------------------------------------------------ pomocnicze tokenowe
    def _strip_parens(self, a: int, b: int) -> tuple:
        c = self.c
        guard = 0
        while a < b and c.texts[a] == "(" and c.pairs.get(a) == b and guard < 20:
            a, b = a + 1, b - 1
            guard += 1
        # rzutowanie (long long)x / (int)(...)
        if a < b and c.texts[a] == "(" and a in c.pairs:
            k = c.pairs[a]
            if k < b and all(c.texts[j] in ("int", "long", "unsigned", "double", "float", "short", "char", "size_t",
                                            "ll", "LL", "ull") or c.texts[j] in c.aliases
                             for j in range(a + 1, k)) and k > a + 1:
                return self._strip_parens(k + 1, b)
        if a + 1 < b and c.texts[a] in ("static_cast",) and c.texts[a + 1] == "<":
            k = c._skip_template(a + 1)
            if k is not None and k + 1 <= b and c.texts[k + 1] == "(":
                return self._strip_parens(k + 1, b)
        return a, b

    def _split_additive(self, a: int, b: int) -> list:
        c = self.c
        res = []
        sign = 1
        start = a
        i = a
        if c.texts[a] in ("-", "+"):
            sign = -1 if c.texts[a] == "-" else 1
            start = i = a + 1
        while i <= b:
            t = c.texts[i]
            if t in ("(", "[", "{") and i in c.pairs and c.pairs[i] > i:
                i = c.pairs[i] + 1
                continue
            if t in ("+", "-") and i > start and (c.toks[i - 1].kind in ("id", "num") or c.texts[i - 1] in (")", "]")):
                res.append((sign, (start, i - 1)))
                sign = 1 if t == "+" else -1
                start = i + 1
            i += 1
        res.append((sign, (start, b)))
        return res

    def _split_mult(self, a: int, b: int) -> list:
        c = self.c
        res = []
        op = "*"
        start = a
        i = a
        while i <= b:
            t = c.texts[i]
            if t in ("(", "[", "{") and i in c.pairs and c.pairs[i] > i:
                i = c.pairs[i] + 1
                continue
            if t in ("*", "/", "%") and i > start and (c.toks[i - 1].kind in ("id", "num") or
                                                      c.texts[i - 1] in (")", "]")):
                res.append((op, (start, i - 1)))
                op = t
                start = i + 1
            i += 1
        res.append((op, (start, b)))
        return res

    # ------------------------------------------------------------------ wielkość wyrażenia
    def mag(self, a: int, b: int, at: int | None = None, depth: int = 0) -> Mag:
        c = self.c
        if a is None or b is None or a > b or depth > 7 or a >= c.n:
            return Mag(_one(), num=1.0)
        a, b = self._strip_parens(a, b)
        if a > b:
            return Mag(_one(), num=1.0)
        at = a if at is None else at
        # warunek trójargumentowy: weź większą gałąź
        q = c.find_top(a, b, ("?",))
        if q:
            col = c.find_top(q[0] + 1, b, (":",))
            if col:
                m1 = self.mag(q[0] + 1, col[0] - 1, at, depth + 1)
                m2 = self.mag(col[0] + 1, b, at, depth + 1)
                return self._max_mag(m1, m2)
        parts = self._split_additive(a, b)
        if len(parts) > 1 or parts[0][0] < 0:
            total = None
            deps, flags = set(), set()
            num, allnum = 0.0, True
            for sign, (x, y) in parts:
                mm = self.mag(x, y, at, depth + 1)
                deps |= mm.deps
                flags |= mm.flags
                if mm.num is not None:
                    num += sign * mm.num
                else:
                    allnum = False
                    if sign > 0:
                        total = mm.cost if total is None else total + mm.cost
                    else:
                        flags.add("minus")
            if allnum:
                return Mag(Cost.const(max(num, 1.0)), deps, flags, num)
            return Mag(total if total is not None else _one(), deps, flags, None)
        parts = self._split_mult(a, b)
        if len(parts) > 1:
            acc = _one()
            deps, flags = set(), set()
            num, allnum = 1.0, True
            for op, (x, y) in parts:
                mm = self.mag(x, y, at, depth + 1)
                deps |= mm.deps
                flags |= mm.flags
                if op == "*":
                    acc = acc * mm.cost
                    if mm.num is not None and num is not None:
                        num *= mm.num
                    else:
                        allnum = False
                elif op == "/":
                    if mm.num is not None:
                        acc = acc * (1.0 / max(mm.num, 1e-9))
                        if num is not None:
                            num /= max(mm.num, 1e-9)
                    else:
                        allnum = False
                        if mm.deps and "loopvar" in mm.flags:
                            flags.add("harmonic")
                            acc = acc * self._log(mm.cost) * _inv(mm.cost)
                        else:
                            acc = acc * _inv(mm.cost)
                elif op == "%":
                    acc = mm.cost
                    num = mm.num
                    allnum = mm.num is not None
            if allnum and num is not None:
                return Mag(Cost.const(max(num, 1.0)), deps, flags, num)
            return Mag(acc, deps, flags, None)
        # przesunięcia bitowe
        sh = c.find_top(a, b, ("<<", ">>"))
        if sh:
            k = sh[0]
            left = self.mag(a, k - 1, at, depth + 1)
            right = self.mag(k + 1, b, at, depth + 1)
            if c.texts[k] == "<<":
                if left.num is not None and right.num is not None and right.num < 63:
                    v = left.num * 2 ** right.num
                    return Mag(Cost.const(v), left.deps | right.deps, left.flags, v)
                mv = _main_var(right.cost)
                if mv:
                    return Mag(left.cost * Cost.var(mv, 0, e=2), left.deps | right.deps, left.flags | {"exp"}, None)
                return left
            if right.num is not None:
                v = (left.num / 2 ** right.num) if left.num is not None else None
                return Mag(left.cost * (1.0 / 2 ** min(right.num, 60)), left.deps, left.flags, v)
            return left
        T = c.texts
        if T[a] in ("-", "+", "!", "~", "*", "&") and a < b:
            return self.mag(a + 1, b, at, depth + 1)
        if a == b:
            t = c.toks[a]
            if t.kind == "num":
                from oiforge.tools._cppstruct import parse_number
                v = parse_number(t.text) or 0.0
                return Mag(Cost.const(max(abs(v), 1.0)), num=v)
            if t.kind == "id":
                return self.ident(t.text, at, depth)
            if t.text in ("true", "false"):
                return Mag(_one(), num=1.0)
            if t.kind in ("char",):
                return Mag(Cost.const(128), num=128.0)
            return Mag(_one(), num=1.0)
        # wywołanie funkcji
        if c.toks[a].kind == "id" and a + 1 <= b and T[a + 1] == "(" and c.pairs.get(a + 1) == b:
            name = T[a]
            args = c.split_top(a + 2, b - 1, (",",)) if b - 1 >= a + 2 else []
            if name in ("sqrt", "sqrtl", "sqrtf", "isqrt"):
                m = self.mag(*args[0], at, depth + 1) if args else Mag(_one())
                v = math.sqrt(m.num) if m.num is not None and m.num >= 0 else None
                return Mag(_sqrt_of(m.cost), m.deps, m.flags | {"sqrt"}, v)
            if name == "cbrt":
                m = self.mag(*args[0], at, depth + 1) if args else Mag(_one())
                d = m.cost.dominant()
                if d and d.f:
                    return Mag(Cost([Term(1, {k: Fac(v.p / 3) for k, v in d.f.items()})]), m.deps, m.flags)
                return m
            if name in ("log", "log2", "log10", "logl", "__lg", "lg", "log2l"):
                m = self.mag(*args[0], at, depth + 1) if args else Mag(_one())
                v = math.log2(max(m.num, 2)) if m.num is not None else None
                return Mag(self._log(m.cost), m.deps, m.flags | {"log"}, v)
            if name in ("min", "max") and len(args) >= 2:
                ms = [self.mag(x, y, at, depth + 1) for x, y in args]
                if name == "max":
                    r = ms[0]
                    for mm in ms[1:]:
                        r = self._max_mag(r, mm)
                    return r
                return min(ms, key=lambda mm: self._eval(mm.cost))
            if name in ("abs", "llabs", "fabs", "ceil", "floor", "round", "llround", "int", "ll"):
                return self.mag(*args[0], at, depth + 1) if args else Mag(_one())
            if name == "pow" and len(args) == 2:
                base = self.mag(*args[0], at, depth + 1)
                ex = self.mag(*args[1], at, depth + 1)
                if ex.num is not None and base.num is not None and ex.num < 64:
                    v = base.num ** ex.num
                    return Mag(Cost.const(v), num=v)
                if ex.num is not None:
                    d = base.cost.dominant()
                    if d and d.f:
                        return Mag(Cost([Term(1, {k: Fac(v.p * ex.num, v.l * ex.num) for k, v in d.f.items()})]),
                                   base.deps, base.flags)
                return base
            if name in ("size", "ssize") and args:
                return self._size_mag(args[0][0], args[0][1], at)
            if name == "strlen" and args:
                ids = c.ids_in(*args[0])
                return Mag(self.csize(ids[0], at) if ids else Cost.var("|s|"), set(), {"size"})
            return Mag(Cost.var(self.sym("?", "unknown", source=f"wynik funkcji {name}()")), set(), {"call"})
        # metoda .size() / .length()
        if T[b] == ")" and b in c.pairs:
            o = c.pairs[b]
            if o >= 2 and T[o - 1] in ("size", "length") and T[o - 2] in (".", "->"):
                return self._size_mag(a, o - 3, at)
        # dostęp do tablicy -> wartość z danych
        if T[b] == "]" and c.toks[a].kind == "id":
            base = T[a]
            if base in c.consts:
                return self.ident(base, at, depth)
            nm = self.sym(f"max({base})", "value", source=f"wartości w tablicy {base}")
            return Mag(Cost.var(nm), set(), {"value"})
        # łańcuch postfiksowy a[i].second / p.first / v[i][j]
        if c.toks[a].kind == "id" and T[a + 1] in ("[", ".", "->"):
            j = a + 1
            ok = True
            while j <= b:
                if T[j] == "[" and j in c.pairs:
                    j = c.pairs[j] + 1
                elif T[j] in (".", "->") and j + 1 <= b and c.toks[j + 1].kind == "id" and (j + 2 > b or T[j + 2] != "("):
                    j += 2
                else:
                    ok = False
                    break
            if ok and j == b + 1:
                nm = self.sym(f"max({T[a]})", "value", source=f"wartości w {T[a]}")
                return Mag(Cost.var(nm), set(), {"value"})
        # pole struktury / pary
        if c.toks[a].kind == "id" and T[a + 1] in (".", "->"):
            nm = self.sym(f"max({T[a]})", "value", source=f"wartości w {T[a]}")
            return Mag(Cost.var(nm), set(), {"value"})
        ids = [k for k in range(a, b + 1) if c.toks[k].kind == "id"]
        if len(ids) == 1:
            return self.ident(T[ids[0]], at, depth)
        return Mag(Cost.var(self.sym("?", "unknown", source="nierozpoznane wyrażenie")), set(), {"unknown"})

    def _max_mag(self, m1: Mag, m2: Mag) -> Mag:
        if m1.num is not None and m2.num is not None:
            return m1 if m1.num >= m2.num else m2
        if m1.num is not None:
            return Mag(m2.cost, m1.deps | m2.deps, m1.flags | m2.flags, None)
        if m2.num is not None:
            return Mag(m1.cost, m1.deps | m2.deps, m1.flags | m2.flags, None)
        return Mag(m1.cost + m2.cost, m1.deps | m2.deps, m1.flags | m2.flags, None)

    def _size_mag(self, a: int, b: int, at: int) -> Mag:
        c = self.c
        if a > b:
            return Mag(Cost.var("|?|"), set(), {"size"})
        ids = [k for k in range(a, b + 1) if c.toks[k].kind == "id"]
        if not ids:
            return Mag(Cost.var("|?|"), set(), {"size"})
        name = c.texts[ids[0]]
        if "[" in c.texts[a:b + 1]:
            return Mag(self.elem_size(name, at), set(), {"size", "deg"})
        return Mag(self.csize(name, at), set(), {"size"})

    def ident(self, name: str, at: int, depth: int = 0) -> Mag:
        c = self.c
        # zmienna sterująca pętli obejmującej
        for k in reversed(c.loops_containing(at)):
            L = c.loops[k]
            if L.var == name and L.kind != "range":
                bm = self.loop_var_range(k)
                return Mag(bm.cost, bm.deps | {name}, bm.flags | {"loopvar"}, bm.num)
        if name in c.consts:
            v = c.consts[name]
            if abs(v) <= 64:
                return Mag(Cost.const(max(abs(v), 1.0)), num=v)
            self.sym(name, "const", v, c.const_lines.get(name), "stała w kodzie")
            return Mag(Cost.var(name), set(), {"const"}, None)
        f = c.enclosing_func(at)
        if f is not None and any(p.name == name for p in f.params):
            return Mag(Cost.var("§" + name), set(), {"param"})
        if name in c.defines and name not in c.consts:
            return Mag(Cost.var(self.sym(name, "const", None, None, "makro")), set(), {"const"})
        # zmienna lokalna/globalna — czy ma jednoznaczną definicję?
        if name not in self.read_targets and depth < 5 and name not in self._busy:
            fname = f.name if f else None
            defs = [x for x in c.assigns if x.target == name and not x.indexed and x.target_text == name
                    and (x.func == fname or x.func is None)]
            eqs = [x for x in defs if x.op == "="]
            mods = [x for x in defs if x.op != "="]
            if len(eqs) == 1 and not mods and eqs[0].rhs:
                self._busy.add(name)
                try:
                    m = self.mag(eqs[0].rhs[0], eqs[0].rhs[1], eqs[0].rhs[0], depth + 1)
                finally:
                    self._busy.discard(name)
                if not ("unknown" in m.flags and m.cost.vars() == {"?"}):
                    return m
            if eqs and mods and all(x.op == "++" or (x.op == "+=" and x.rhs and
                                                     normalize_expr(c.text(*x.rhs)) == "1") for x in mods):
                # licznik budowany w pętli (np. cnt++): wartość ≤ liczba obrotów pętli, w której rośnie
                ks = {c.innermost_loop(x.idx) for x in mods}
                if len(ks) == 1 and None not in ks:
                    kc = ks.pop()
                    reset = max(eqs, key=lambda z: z.idx)
                    rk = c.innermost_loop(reset.idx)
                    if kc != rk and (rk is None or rk in c.loops_containing(c.loops[kc].idx)):
                        info = self.loop(kc)
                        val = info.count if c.loops[kc].parent == rk else self.mult(kc)
                        fl = {"counter", f"counter:{kc}"}
                        if kc in c.loops_containing(at):
                            fl.add("loopvar")      # rośnie co obrót pętli obejmującej — jak zmienna sterująca
                        return Mag(val, set(info.deps) | ({name} if "loopvar" in fl else set()), fl, None)
            if len(eqs) >= 2 and not mods and depth < 4:
                # kilka prostych przypisań (x = -1; … x = i;) → największa z wartości
                ms = []
                self._busy.add(name)
                try:
                    for x in eqs:
                        if not x.rhs or name in c.ids_in(*x.rhs):
                            ms = []
                            break
                        mm = self.mag(x.rhs[0], x.rhs[1], x.rhs[0], depth + 1)
                        if "unknown" in mm.flags or "call" in mm.flags or "value" in mm.flags:
                            ms = []
                            break
                        ms.append(mm)
                finally:
                    self._busy.discard(name)
                if ms:
                    r = ms[0]
                    for mm in ms[1:]:
                        r = self._max_mag(r, mm)
                    return Mag(r.cost, r.deps, r.flags - {"loopvar"}, r.num)
        return self._input_mag(name)

    # ------------------------------------------------------------------ rozmiary kontenerów
    def csize(self, name: str, at: int | None = None) -> Cost:
        key = name
        if key in self._csize:
            return self._csize[key]
        if key in self._busy:
            return Cost.var(f"|{name}|")
        self._busy.add(key)
        try:
            res = self._csize_compute(name, at)
        finally:
            self._busy.discard(key)
        self._csize[key] = res
        return res

    def _csize_compute(self, name: str, at: int | None) -> Cost:
        c = self.c
        d = c.decl_of(name, at)
        if d is not None and d.is_param:
            return Cost.var("§" + name)
        if d is not None and d.kind == "array" and d.dims:
            p = _one()
            for dim in d.dims:
                p = p * self._text_mag(dim, d.idx).cost
            return self._prefer_input(p)
        # kopia innego kontenera: vector<int> b = a;  vector<int> b(a);  set<int> s(a.begin(), a.end());
        src = None
        if d is not None and not d.is_param:
            ini = normalize_expr(d.init)
            if ini and re.fullmatch(r"\w+", ini):
                src = ini
            elif len(d.ctor) == 1 and re.fullmatch(r"\w+", normalize_expr(d.ctor[0])):
                src = normalize_expr(d.ctor[0])
            elif len(d.ctor) == 2:
                m0 = re.fullmatch(r"(\w+)\.begin\(\)", normalize_expr(d.ctor[0]))
                src = m0.group(1) if m0 else None
        if src and src != name:
            d2 = c.decl_of(src, d.idx)
            if d2 is not None and d2.kind in ("vector", "deque", "string", "set", "multiset", "map", "multimap",
                                              "umap", "uset", "array", "stdarray"):
                return self.csize(src, d.idx)
        if d is not None and d.ctor:
            m = self._text_mag(d.ctor[0], d.idx)
            if d.kind in ("vector", "deque", "string", "bitset", "stdarray") and not (m.num is not None and m.num == 0):
                return m.cost
        total = None
        if d is not None and d.kind in ("map", "multimap", "umap"):
            # m[x] = …, m[x]++ — operator[] dokłada brakujący klucz
            for asg in c.assigns:
                if asg.target == name and asg.indexed and asg.target_text.startswith(name + "[") and \
                        asg.target_text.count("[") == 1:
                    mm = self.mult_at(asg.idx)
                    total = mm if total is None else total + mm
        for cl in c.calls:
            if cl.receiver != name or cl.receiver_text != name:
                continue
            if cl.name in ("resize", "assign") and cl.args:
                m = self.mag(cl.args[0][0], cl.args[0][1], cl.idx)
                return m.cost
            if cl.name in ("push_back", "emplace_back", "push", "emplace", "insert", "push_front", "emplace_front",
                           "append"):
                mm = self.mult_at(cl.idx)
                total = mm if total is None else total + mm
        for asg in c.assigns:
            if asg.target == name and asg.target_text == name and (asg.op == "+=" or (
                    asg.op == "=" and asg.rhs and c.texts[asg.rhs[0]] == name and asg.rhs[0] + 1 <= asg.rhs[1]
                    and c.texts[asg.rhs[0] + 1] == "+")):
                mm = self.mult_at(asg.idx)
                total = mm if total is None else total + mm
        if total is not None and not total.is_const():
            return total
        if total is not None and total.const_value() > 1:
            return total
        if d is not None and d.kind == "string" or (d is None and name in self.read_targets):
            nm = self.sym(f"|{name}|", "size", source=f"długość napisu {name}")
            return Cost.var(nm)
        nm = self.sym(f"|{name}|", "size", source=f"rozmiar {name}")
        return Cost.var(nm)

    def _prefer_input(self, p: Cost) -> Cost:
        """Tablica o stałym rozmiarze N — dla pętli i tak zwykle chodzi o wczytane n."""
        return p

    def elem_size(self, name: str, at: int | None = None) -> Cost:
        """Rozmiar elementu kontenera zagnieżdżonego (np. stopień wierzchołka g[v]) — łącznie E, w najgorszym razie."""
        return self.edges_of(name)

    def edges_of(self, name: str) -> Cost:
        key = "E:" + name
        if key in self._csize:
            return self._csize[key]
        if key in self._busy:
            return Cost.var("m")
        self._busy.add(key)
        try:
            total = None
            for cl in self.c.calls:
                if cl.receiver == name and cl.receiver_text != name and cl.name in (
                        "push_back", "emplace_back", "push", "insert", "emplace", "push_front"):
                    mm = self.mult_at(cl.idx)
                    total = mm if total is None else total + mm
            if total is None:
                for asg in self.c.assigns:
                    if asg.target == name and len(asg.index_texts) >= 2:
                        mm = self.mult_at(asg.idx)
                        total = mm if total is None else total + mm
            if total is None:
                total = Cost.var(self.sym("m", "input", source="liczba krawędzi (założenie)")) \
                    if "m" in self.read_targets else self.vertices_of(name)
        finally:
            self._busy.discard(key)
        self._csize[key] = total
        return total

    def vertices_of(self, name: str) -> Cost:
        c = self.c
        d = c.decl_of(name)
        if d is not None and (d.kind == "array" and d.dims or d.ctor):
            v = self.csize(name)
            if not v.is_const() and not (v.vars() & set(c.consts)):
                return v
        else:
            v = self.csize(name)
            if not v.is_const() and not any(k.startswith("|") for k in v.vars()):
                return v
        if "n" in self.read_targets or "n" in c.texts:
            return Cost.var(self.sym("n", "input"))
        return self.csize(name)

    def _text_mag(self, text: str, at: int) -> Mag:
        """Wielkość wyrażenia podanego tekstem (np. wymiar tablicy) — szuka tokenów w okolicy `at`."""
        c = self.c
        norm = normalize_expr(text)
        # spróbuj znaleźć ten tekst jako zakres tokenów blisko `at`
        for start in range(max(0, at), min(c.n, at + 60)):
            acc = ""
            for end in range(start, min(c.n, start + 40)):
                acc += c.texts[end]
                if acc == norm:
                    return self.mag(start, end, at)
                if len(acc) >= len(norm):
                    break
        v = c._eval_const_text(text)
        if v is not None:
            if v <= 64:
                return Mag(Cost.const(v), num=v)
            ids = re.findall(r"[A-Za-z_]\w*", text)
            if ids and ids[0] in c.consts:
                self.sym(ids[0], "const", c.consts[ids[0]], c.const_lines.get(ids[0]), "stała w kodzie")
                return Mag(Cost.var(ids[0]) * (v / max(c.consts[ids[0]], 1)), set(), {"const"})
            nm = self.lit_sym(v, c.toks[at].line if at < c.n else None)
            return Mag(Cost.var(nm), set(), {"const"})
        ids = re.findall(r"[A-Za-z_]\w*", text)
        if ids:
            return Mag(Cost.var(self.sym(ids[0], "input")), set(), {"input"})
        return Mag(_one(), num=1.0)

    def lit_sym(self, v: float, line: int | None) -> str:
        for k, s in self.syms.items():
            if s["kind"] == "literal" and s["value"] == v:
                return k
        idx = sum(1 for s in self.syms.values() if s["kind"] == "literal")
        nm = "C" if idx == 0 else f"C{idx + 1}"
        self.sym(nm, "literal", v, line, f"stała {fmt_num(v)}")
        return nm

    def _big_num(self, m: Mag, line: int | None) -> Mag:
        """Liczba ≥ 1000 jako granica pętli → symbol C (żeby było widać w O(...))."""
        if m.num is not None and abs(m.num) >= 1000:
            return Mag(Cost.var(self.lit_sym(abs(m.num), line)), m.deps, m.flags | {"const"}, None)
        return m

    # ------------------------------------------------------------------ zakres zmiennej pętli
    def loop_var_range(self, k: int) -> Mag:
        """Największa wartość, jaką przyjmuje zmienna sterująca pętli k (do pętli zależnych)."""
        c = self.c
        L = c.loops[k]
        if L.kind == "for" and L.cond:
            cmp_ = self._cond_cmp(L.cond, L.var)
            if cmp_:
                op, side, bound, special = cmp_
                if op in ("<", "<=", "!=") and not special:
                    return self._big_num(self.mag(bound[0], bound[1], L.idx), L.line)
                if op in (">", ">=") and L.init:
                    eq = c.find_top(L.init[0], L.init[1], ("=",))
                    if eq:
                        return self._big_num(self.mag(eq[0] + 1, L.init[1], L.idx), L.line)
        info = self.loop(k)
        return Mag(info.count, info.deps, set(), None)

    def _cond_cmp(self, cond: tuple, var: str | None):
        """Znajdź w warunku porównanie ze zmienną var: (op, 'left'|'right', zakres_granicy, special)."""
        c = self.c
        if not cond or cond[0] > cond[1] or not var:
            return None
        for a, b in c.split_top(cond[0], cond[1], ("&&",)):
            for k in c.find_top(a, b, ("<", "<=", ">", ">=", "!=")):
                left = (a, k - 1)
                right = (k + 1, b)
                op = c.texts[k]
                lids = [c.texts[i] for i in range(left[0], left[1] + 1) if c.toks[i].kind == "id"]
                rids = [c.texts[i] for i in range(right[0], right[1] + 1) if c.toks[i].kind == "id"]
                flip = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "!=": "!="}
                if var in lids:
                    side, other, me = "left", right, left
                elif var in rids:
                    side, other, me = "right", left, right
                    op = flip[op]
                else:
                    continue
                special = None
                me_txt = normalize_expr(c.text(*me))
                if re.fullmatch(rf"(\(?\w*\)?\*?)?{re.escape(var)}\*{re.escape(var)}", me_txt) or \
                        re.fullmatch(rf"1LL\*{re.escape(var)}\*{re.escape(var)}|\(longlong\){re.escape(var)}\*"
                                     rf"{re.escape(var)}", me_txt):
                    special = "sqrt"
                elif re.fullmatch(rf"\(?1(LL|ll|ULL)?<<{re.escape(var)}\)?", me_txt):
                    special = "pow2"
                elif me_txt != var:
                    m = re.fullmatch(rf"{re.escape(var)}\*(\w+)|(\w+)\*{re.escape(var)}", me_txt)
                    if m:
                        special = "mul:" + (m.group(1) or m.group(2))
                    elif re.fullmatch(rf"{re.escape(var)}[+-]\w+|\w+[+-]{re.escape(var)}", me_txt):
                        special = None
                    else:
                        special = "expr"
                return op, side, other, special
        return None

    # ------------------------------------------------------------------ analiza pętli
    def loop(self, k: int) -> LoopInfo:
        if k in self.loop_info:
            return self.loop_info[k]
        key = ("loop", k)
        if key in self._busy:
            return LoopInfo(Cost.var("n"), "unknown", "cykliczna zależność", confidence=0.3)
        self._busy.add(key)
        try:
            try:
                info = self._loop_compute(k)
            except RecursionError:
                info = LoopInfo(Cost.var("n"), "unknown", "nie udało się przeanalizować", confidence=0.2)
            info = self._amortize(k, info)
        finally:
            self._busy.discard(key)
        self.loop_info[k] = info
        return info

    def _loop_compute(self, k: int) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        if L.kind == "range":
            return self._range_loop(k)
        if L.kind == "for":
            return self._shadow_tests(k, self._for_loop(k))
        return self._shadow_tests(k, self._while_loop(k))

    def _shadow_tests(self, k: int, info: LoopInfo) -> LoopInfo:
        """for (i < n) { … cin >> n … } — w każdym obrocie wczytywane jest NOWE n (zwykle w funkcji), więc zewnętrzna
        pętla przebiega po zestawach testowych, a nie po elementach."""
        c = self.c
        L = c.loops[k]
        if L.parent is not None or info.amort_over is not None:
            return info
        vs = info.count.vars()
        if len(vs) != 1 or len(info.count.terms) != 1:
            return info
        v = next(iter(vs))
        if v not in self.read_targets or v in self.iter_reads:
            return info
        rngs = [(L.body[0], L.body[1])]
        for cl in c.calls:
            if L.body[0] <= cl.idx <= L.body[1] and not cl.is_method and cl.name in c.func_by_name:
                f = c.func_by_name[cl.name]
                rngs.append((f.open_idx, f.close_idx))
        again = any(io.kind == "read" and any(a_ <= io.idx <= b_ for a_, b_ in rngs) and
                    any(re.fullmatch(rf"\s*{re.escape(v)}\s*", t) for t in io.targets) for io in c.io)
        if not again:
            return info
        nm = "t" if "t" not in self.read_targets else "z"
        self.sym(nm, "test", None, L.line, f"liczba zestawów testowych (pętla po {v}, która w środku wczytuje nowe {v})")
        return LoopInfo(Cost.var(nm), "tests", f"pętla po zestawach testowych: w każdym obrocie wczytywane jest nowe "
                                               f"{v}, więc to liczba testów, a nie rozmiar danych", confidence=0.7)

    def reading_loop_symbols(self) -> set:
        """Symbole, które są liczbą obrotów pętli wczytującej dane w każdym obrocie (≤ rozmiar wejścia)."""
        c = self.c
        res = set()
        for k, L in enumerate(c.loops):
            if not any(io.kind == "read" and L.body[0] <= io.idx <= L.body[1] for io in c.io):
                continue
            info = self.loop(k)
            vs = info.count.vars()
            if len(vs) == 1 and len(info.count.terms) == 1:
                res |= vs
        return res

    def _range_loop(self, k: int) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        a, b = L.range_expr
        a, b = self._strip_parens(a, b)
        if c.texts[a] == "{":
            cnt = len(c.split_top(a + 1, c.pairs.get(a, b) - 1, (",",)))
            return LoopInfo(Cost.const(cnt), "const", f"pętla po {cnt} elementach listy")
        ids = [i for i in range(a, b + 1) if c.toks[i].kind == "id"]
        if not ids:
            return LoopInfo(Cost.var("n"), "unknown", "pętla po zakresie", confidence=0.4)
        name = c.texts[ids[0]]
        if "[" in c.texts[a:b + 1]:
            E = self.edges_of(name)
            return LoopInfo(E, "adjacency", f"pętla po sąsiadach ({c.text(a, b)}) — stopień wierzchołka",
                            confidence=0.7)
        size = self.csize(name, L.idx)
        return LoopInfo(size, "linear", f"pętla po wszystkich elementach {name} → {size.expr()} iteracji")

    def _step_kind(self, a: int, b: int, var: str, at: int):
        """Rodzaj kroku: ('lin', c) | ('lin_sym', Mag) | ('harm', outer_var) | ('mul', c) | ('div', c) |
        ('fenw_up',) | ('fenw_down',) | ('walk',) | None"""
        c = self.c
        if a > b:
            return None
        for x, y in c.split_top(a, b, (",",)):
            txt = normalize_expr(c.text(x, y))
            v = re.escape(var)
            if re.fullmatch(rf"(\+\+{v}|{v}\+\+)", txt):
                return ("lin", 1.0)
            if re.fullmatch(rf"(--{v}|{v}--)", txt):
                return ("lin", -1.0)
            m = re.fullmatch(rf"{v}([+-])=(.+)", txt) or re.fullmatch(rf"{v}=({v})([+-])(.+)", txt)
            if m:
                if len(m.groups()) == 3:
                    sign, rhs = m.group(2), m.group(3)
                else:
                    sign, rhs = m.group(1), m.group(2)
                if re.search(rf"{v}&\(?-{v}\)?|{v}&~\(?{v}-1\)?|\(-{v}\)&{v}", rhs):
                    return ("fenw_up",) if sign == "+" else ("fenw_down",)
                # znajdź zakres tokenów rhs
                eq = c.find_top(x, y, ("+=", "-=", "="))
                if eq:
                    rs = eq[0] + 1
                    if c.texts[eq[0]] == "=":
                        plus = c.find_top(rs, y, ("+", "-"))
                        rs = plus[0] + 1 if plus else rs
                    mm = self.mag(rs, y, at)
                    if mm.num is not None:
                        return ("lin", mm.num if sign == "+" else -mm.num)
                    if "loopvar" in mm.flags:
                        return ("harm", mm)
                    return ("lin_sym", mm, 1 if sign == "+" else -1)
                return ("lin", 1.0 if sign == "+" else -1.0)
            m = re.fullmatch(rf"{v}(\*=|<<=)(\d+)", txt) or re.fullmatch(rf"{v}=({v})(\*|<<)(\d+)", txt) or \
                re.fullmatch(rf"{v}=(\d+)(\*)({v})", txt)
            if m:
                return ("mul", 2.0)
            m = re.fullmatch(rf"{v}(/=|>>=)(\d+)", txt) or re.fullmatch(rf"{v}={v}(/|>>)(\d+)", txt)
            if m:
                return ("div", 2.0)
            if re.fullmatch(rf"{v}=\({v}&\({v}\+1\)\)-1|{v}\|={v}\+1|{v}=\({v}\|\({v}\+1\)\)", txt):
                return ("fenw_down",) if "&" in txt else ("fenw_up",)
            if re.fullmatch(rf"{v}=\w+\[{v}([+-]\d+)?\]", txt) or re.fullmatch(rf"{v}={v}(->|\.)\w+", txt):
                return ("walk",)
        return None

    def _for_loop(self, k: int) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        var = L.var
        line = L.line
        if not L.cond or L.cond[0] > L.cond[1]:
            return LoopInfo(Cost.var(self.sym("?", "unknown", source="pętla bez warunku")), "unknown",
                            "pętla bez warunku (wyjście przez break/return) — liczba iteracji nieznana", confidence=0.3)
        step = self._step_kind(*L.step, var, L.idx) if (L.step and var) else None
        cmp_ = self._cond_cmp(L.cond, var)
        start = None
        if L.init and var:
            eq = c.find_top(L.init[0], L.init[1], ("=",))
            if eq:
                start = self.mag(eq[0] + 1, L.init[1], L.idx)
        if start is None and var:
            st = self._value_before(var, L.idx)
            start = st if st is not None else Mag(_one(), num=0.0)
        return self._count_from(k, var, cmp_, step, start, line)

    def _count_from(self, k: int, var: str, cmp_, step, start: Mag, line: int) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        if cmp_ is None:
            # warunek nie dotyczy zmiennej sterującej
            if step and step[0] in ("mul", "div"):
                return LoopInfo(self._log(Cost.var("n")), "log", "krok mnożący — logarytmiczna liczba kroków", confidence=0.5)
            return LoopInfo(Cost.var(self.sym("?", "unknown", source="warunek pętli")), "unknown",
                            "warunek nie zależy od zmiennej sterującej — liczba iteracji zależy od danych",
                            confidence=0.3)
        op, side, bound, special = cmp_
        bm = self._big_num(self.mag(bound[0], bound[1], L.idx), line)
        if special == "sqrt":
            if start is not None and "sqrt" in start.flags:
                return LoopInfo(Cost.const(2), "const", f"start od ⌊√…⌋ — pętla tylko poprawia zaokrąglenie "
                                                        f"(kilka kroków)", bm.deps)
            cnt = _sqrt_of(bm.cost)
            return LoopInfo(cnt, "sqrt", f"warunek {var}·{var} ≤ … → około {_cnt_str(cnt)} iteracji (pierwiastek)",
                            bm.deps)
        if special == "pow2":
            return LoopInfo(self._log(bm.cost), "log", f"warunek (1 << {var}) ≤ … → log iteracji", bm.deps)
        if special and special.startswith("mul:"):
            other = special[4:]
            om = self.ident(other, L.idx)
            if "loopvar" in om.flags:
                return self._harmonic(k, bm, om, f"{var}·{other} < {bm.cost.expr()}", line)
            bm = Mag(bm.cost * _inv(om.cost), bm.deps | om.deps, bm.flags)
        if step is None:
            body_step = self._body_step(k, var)
            step = body_step
        if step is None:
            return LoopInfo(Cost.var(self.sym("?", "unknown", source="krok pętli")), "unknown",
                            "nie rozpoznano kroku pętli", confidence=0.3)
        kind = step[0]
        bm = self._big_num(bm, line)
        start = self._big_num(start, line) if start is not None else Mag(_one(), num=0.0)
        if kind in ("lin", "lin_sym"):
            stepv = step[1] if kind == "lin" else None
            sign = (1 if stepv > 0 else -1) if kind == "lin" else step[2]
            increasing = sign > 0
            if increasing and op in (">", ">="):
                return LoopInfo(Cost.var("n"), "unknown", "warunek i krok się nie zgadzają (pętla nieskończona?)",
                                confidence=0.2)
            if increasing:
                hi, lo = bm, start
            else:
                hi, lo = start, bm
            deps = hi.deps | lo.deps
            if hi.num is not None and lo.num is not None:
                n_it = max(0.0, (hi.num - lo.num) / abs(stepv or 1)) + (1 if op in ("<=", ">=") else 0)
                return LoopInfo(Cost.const(max(n_it, 0.0)), "const", f"stała liczba iteracji: {fmt_num(n_it)}")
            span = hi.cost if hi.num is None else lo.cost
            note_dep = ""
            pattern = "linear"
            bounded = self._query_range_bound(k, var, hi, lo, span)
            if bounded is not None:
                span, note_dep = bounded
                pattern = "query_range"
            if deps & self._outer_vars(k):
                span = span * 0.5
                pattern = "dependent"
                dv = ", ".join(sorted(deps & self._outer_vars(k)))
                note_dep = f" (pętla zależna od {dv} — średnio połowa, łącznie ~{(span * 2).expr()}²/2)"
            if kind == "lin" and abs(stepv) > 1:
                span = span * (1.0 / abs(stepv))
            if kind == "lin_sym":
                sm = step[1]
                span = span * _inv(sm.cost)
                note_dep += f" (krok {sm.cost.expr()})"
            if "harmonic" in hi.flags | lo.flags:
                pattern = "harmonic"
                note_dep = " (suma harmoniczna)"
            ck = next((int(f.split(":")[1]) for f in hi.flags if f.startswith("counter:")), None)
            if ck is not None:
                cinfo = self.loop(ck)
                if cinfo.amort_over is not None and cinfo.amort_over in c.loops_containing(L.idx):
                    return LoopInfo(span, "adjacency", f"pętla do licznika policzonego w pętli z linii "
                                                       f"{c.loops[ck].line - self.src_offset} — łącznie tyle obrotów, "
                                                       f"co tamta pętla ({_cnt_str(cinfo.amort_total)}, amortyzacja)",
                                    deps, amort_over=cinfo.amort_over, amort_total=cinfo.amort_total, confidence=0.7)
                note_dep += f" (licznik z pętli w linii {c.loops[ck].line - self.src_offset})"
            rng = f"{c.text(*L.init) if L.init else ''}"
            txt = f"{span.expr()} iteracji{note_dep}"
            if "value" in hi.flags:
                txt += " — granica zależy od wartości z danych"
            return LoopInfo(span, pattern, txt, deps, confidence=0.85 if pattern != "unknown" else 0.4)
        if kind == "harm":
            om = step[1]
            return self._harmonic(k, bm, om, f"krok {var} += …", line)
        if kind == "mul":
            if bm.num is not None:
                v = max(1.0, math.log2(max(bm.num, 2.0) / max(start.num or 1.0, 1.0)))
                return LoopInfo(Cost.const(v), "log", f"krok mnożący ×2 → {v:.0f} iteracji")
            return LoopInfo(self._log(bm.cost), "log", f"krok mnożący ×2 → log({bm.cost.expr()}) iteracji",
                            bm.deps)
        if kind == "div":
            src = start if start is not None else bm
            if src.num is not None:
                v = max(1.0, math.log2(max(src.num, 2.0)))
                return LoopInfo(Cost.const(v), "log", f"krok dzielący /2 → {v:.0f} iteracji")
            return LoopInfo(self._log(src.cost), "log", f"krok dzielący /2 → log({src.cost.expr()}) iteracji",
                            src.deps)
        if kind in ("fenw_up", "fenw_down"):
            # liczba kroków ≤ log(rozmiar drzewa), niezależnie od tego, skąd przyszedł indeks
            arr = self._indexed_array(k, var)
            if kind == "fenw_up":
                src = bm.cost
            else:
                src = start.cost if start is not None and start.num is None else bm.cost
            if arr is not None and (src.vars() & self.iter_reads or any(v.startswith("§") for v in src.vars())
                                    or kind == "fenw_down"):
                src = self._domain(self.csize(arr, L.idx))
            arrow = "+=" if kind == "fenw_up" else "-="
            return LoopInfo(self._log(src), "log", f"krok i {arrow} i & -i (drzewo Fenwicka) → log({src.expr()}) "
                                                   f"iteracji", bm.deps)
        if kind == "walk":
            dsu = self._dsu_walk(k, var)
            if dsu is not None:
                return dsu
            return LoopInfo(Cost.var(self.sym("n", "input")), "walk",
                            "przejście po wskaźnikach (np. po rodzicach) — w najgorszym razie n kroków", confidence=0.5)
        return LoopInfo(Cost.var("n"), "unknown", "nierozpoznana pętla", confidence=0.3)

    def _query_range_bound(self, k: int, var: str, hi: Mag, lo: Mag, span: Cost):
        """Pętla po przedziale [l, r] wczytanym w zapytaniu (albo przekazanym do funkcji): liczba obrotów to długość
        przedziału, czyli najwyżej rozmiar tablicy, po której chodzi pętla — a nie wartość r."""
        c = self.c
        L = c.loops[k]
        flags = hi.flags | lo.flags
        if not ({"itervalue", "param"} & flags):
            return None
        sv = span.vars()
        if not (sv & self.iter_reads) and not any(v.startswith("§") for v in sv):
            return None
        ad = self._indexed_dim(k, var)
        if ad is None:
            return None
        arr, pos = ad
        if any(io.kind == "read" and L.body[0] <= io.idx <= L.body[1] for io in c.io):
            return None
        dom = self._dim_size(arr, pos, L.idx)
        if dom.vars() & self.iter_reads or any(v.startswith("§") for v in dom.vars()):
            return None
        if "param" in flags and "itervalue" not in flags:
            # parametr funkcji: zapamiętaj dziedzinę — przy wywołaniu z wartością z zapytania podstawimy ją
            f = c.enclosing_func(L.idx)
            if f is not None:
                for m in (hi, lo):
                    for v in m.cost.vars():
                        if v.startswith("§"):
                            self._index_params[(f.name, v[1:])] = dom
            return None
        names = ", ".join(sorted({v for v in (hi.cost.vars() | lo.cost.vars()) if v in self.iter_reads})) or "l, r"
        return dom, (f" — granice ({names}) to wartości z danych, ale przedział leży w tablicy {arr}, "
                     f"więc najwyżej {dom.expr()} obrotów")

    def _harmonic(self, k: int, bm: Mag, om: Mag, what: str, line: int) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        # sito: pętla wewnętrzna pod warunkiem  if (!zlozona[i])  (i = zmienna pętli zewnętrznej albo krok j += i)
        sieve = False
        if L.parent is not None:
            P = c.loops[L.parent]
            for pv in dict.fromkeys([P.var] + sorted(om.deps)):
                if not pv or sieve:
                    continue
                epv = re.escape(pv)
                j = L.idx - 1
                while j >= P.body[0] and c.texts[j] not in (";", "{", "}"):
                    j -= 1
                seg = normalize_expr(c.text(max(j + 1, P.body[0]), L.idx - 1))
                if re.search(rf"if\(!?\w+\[{epv}\](==0|==1|==false|==true|!=0)?\)", seg):
                    sieve = True
                body_txt = normalize_expr(c.text(*P.body))
                if re.search(rf"if\(!?\w+\[{epv}\](==0|==1|==false|==true|!=0)?\)(continue|\{{?continue)", body_txt):
                    sieve = True
                # if (!zlozona[i]) { …; for (j = …; j += i) … } — pętla w bloku warunku
                for i in range(P.body[0], L.idx):
                    if sieve or c.texts[i] != "if" or c.texts[i + 1] != "(" or (i + 1) not in c.pairs:
                        continue
                    cc = c.pairs[i + 1]
                    if c.stmt_end(cc + 1) < L.idx:
                        continue
                    cond = normalize_expr(c.text(i + 2, cc - 1))
                    if re.fullmatch(rf"!\w+\[{epv}\]|\w+\[{epv}\]==(0|false)|\w+\[{epv}\](==1|==true)?", cond):
                        sieve = True
        U = om.cost
        if sieve:
            cnt = bm.cost * Cost([Term(1, {_main_var(bm.cost) or "n": Fac(0, 0, 1)})]) * _inv(U)
            return LoopInfo(cnt, "sieve", f"{what}: tylko dla liczb pierwszych — łącznie n/2 + n/3 + n/5 + … "
                                          f"≈ {bm.cost.expr()} log log {bm.cost.expr()} (sito)", om.deps | bm.deps)
        cnt = bm.cost * self._log(U) * _inv(U)
        return LoopInfo(cnt, "harmonic", f"{what}: suma harmoniczna n/1 + n/2 + n/3 + … ≈ "
                                         f"{bm.cost.expr()} · ln({_cnt_str(U)})", om.deps | bm.deps)

    def _outer_vars(self, k: int) -> set:
        c = self.c
        res = set()
        p = c.loops[k].parent
        while p is not None:
            if c.loops[p].var:
                res.add(c.loops[p].var)
            p = c.loops[p].parent
        return res

    def _body_step(self, k: int, var: str):
        """Krok zmiennej modyfikowanej w ciele (dla while i for bez kroku)."""
        c = self.c
        L = c.loops[k]
        a, b = L.body
        found = None
        for asg in c.assigns_in(a, b, var):
            if asg.target_text != var:
                continue
            # pomiń przypisania w zagnieżdżonych pętlach
            inner = c.innermost_loop(asg.idx)
            if inner is not None and inner != k:
                continue
            if asg.op in ("++", "--"):
                sk = ("lin", 1.0 if asg.op == "++" else -1.0)
            else:
                end = asg.rhs[1] if asg.rhs else c.stmt_end(asg.idx)
                sk = self._step_kind(asg.idx, end, var, asg.idx)
            if sk:
                if sk[0] in ("mul", "div", "fenw_up", "fenw_down"):
                    return sk
                found = found or sk
        return found

    def _value_before(self, var: str, idx: int) -> Mag | None:
        c = self.c
        fname = c.func_name_at(idx)
        cands = [x for x in c.assigns if x.target == var and x.target_text == var and x.op == "="
                 and x.idx < idx and x.func == fname and x.rhs]
        if not cands:
            if var in self.read_targets:
                return self._input_mag(var)
            f = c.enclosing_func(idx)
            if f and any(p.name == var for p in f.params):
                base = Cost.var("§" + var)
                for x in c.assigns:
                    if x.target == var and x.target_text == var and x.op == "+=" and x.idx < idx and \
                            x.func == fname and x.rhs and c.innermost_loop(x.idx) is None:
                        base = base + self.mag(x.rhs[0], x.rhs[1], x.rhs[0]).cost
                return Mag(base, set(), {"param"})
            return None
        x = max(cands, key=lambda z: z.idx)
        # jeśli zmienna jest też wczytywana później niż przypisanie — weź wejście
        reads_after = [io for io in c.io if io.kind == "read" and x.idx < io.idx < idx and
                       any(re.match(rf"\s*{re.escape(var)}\b", t) for t in io.targets)]
        if reads_after:
            return self._input_mag(var)
        return self.mag(x.rhs[0], x.rhs[1], x.rhs[0])

    # ------------------------------------------------------------------ while / do-while
    def _while_loop(self, k: int) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        T = c.texts
        a, b = L.cond
        if a > b:
            return LoopInfo(Cost.var("?"), "unknown", "pusty warunek", confidence=0.2)
        ctxt = normalize_expr(c.text(a, b))
        body = L.body
        # while (true) / while (1)
        if re.fullmatch(r"true|1|1==1|\(1\)|!0", ctxt):
            reads = [io for io in c.io if io.kind == "read" and body[0] <= io.idx <= body[1]]
            if reads:
                nm = self.sym("k", "input", source="liczba zapytań do końca wejścia")
                return LoopInfo(Cost.var(nm), "until_input", "pętla do napotkania znacznika końca danych — "
                                                             "tyle iteracji, ile zapytań na wejściu", confidence=0.5)
            return LoopInfo(Cost.var(self.sym("?", "unknown", source="while(true)")), "unknown",
                            "pętla nieskończona z break — liczba iteracji zależy od danych", confidence=0.3)
        # pętla „aż nic się nie zmieni”: while (flaga) { flaga = false; … flaga = true; }
        if re.fullmatch(r"!?\w+", ctxt):
            fv = ctxt.lstrip("!")
            sets = [x for x in c.assigns_in(body[0], body[1], fv) if x.op == "=" and x.rhs]
            vals_ = {normalize_expr(c.text(*x.rhs)) for x in sets}
            if len(sets) >= 2 and vals_ & {"true", "1", "false", "0"} and len(vals_) >= 2 and L.children:
                inner = self.loop(L.children[0]).count
                return LoopInfo(inner, "until_stable", f"pętla powtarzana, dopóki coś się zmienia (flaga {fv}) — "
                                                       f"w najgorszym razie ~{_cnt_str(inner)} przebiegów "
                                                       f"(np. sortowanie bąbelkowe, relaksacja Bellmana-Forda)",
                                confidence=0.55)
        # while (cin >> x)
        if re.match(r"(std::)?cin>>|scanf\(", ctxt):
            nm = self.sym("k", "input", source="liczba elementów do końca wejścia")
            return LoopInfo(Cost.var(nm), "until_input", "czyta do końca wejścia", confidence=0.6)
        # do { } while (next_permutation(...))
        if "next_permutation" in ctxt or "prev_permutation" in ctxt:
            m = re.search(r"(?:next|prev)_permutation\((\w+)", ctxt)
            size = self.csize(m.group(1), L.idx) if m else Cost.var("n")
            mv = _main_var(size) or "n"
            return LoopInfo(Cost([Term(1, {mv: Fac(0, 0, 0, 0, 1)})]), "permutations",
                            f"przegląd wszystkich permutacji → {mv}! iteracji")
        # kolejka / stos / kopiec
        qm = re.search(r"!(\w+)\.empty\(\)|(\w+)\.size\(\)|(\w+)\.empty\(\)==(false|0)", ctxt)
        if qm:
            qname = qm.group(1) or qm.group(2) or qm.group(3)
            res = self._queue_loop(k, qname, ctxt)
            if res is not None:
                return res
        # wyszukiwanie binarne
        bs = self._binsearch(k)
        if bs is not None:
            return bs
        # gcd: while (b) { t = a % b; a = b; b = t; }
        body_txt = normalize_expr(c.text(*body))
        ids = [T[i] for i in range(a, b + 1) if c.toks[i].kind == "id"]
        if ids and "%" in body_txt and re.fullmatch(r"\w+(!=0)?|\w+>0", ctxt):
            v = ids[0]
            mg = re.search(rf"(\w+)%{re.escape(v)}\b", body_txt)
            if mg and re.search(rf"{re.escape(v)}=\w+%{re.escape(v)}|\w+=\w+%{re.escape(v)};", body_txt):
                other = mg.group(1)
                f = c.enclosing_func(L.idx)
                pnames = [x.name for x in f.params] if f else []
                if f is not None and f.name != "main" and v in pnames and other in pnames:
                    self._gcd_funcs[f.name] = (other, v)
                    cnt = Cost.log("§" + v)
                else:
                    cnt = self._log(self._gcd_value_of(self.ident(other, L.idx), self.ident(v, L.idx), other, v))
                return LoopInfo(cnt, "log", f"algorytm Euklidesa: para ({other}, {v}) maleje co najmniej jak liczby "
                                            f"Fibonacciego → O(log min({other}, {v})) kroków", confidence=0.8)
        # licznik w dół: while (q--)
        m = re.fullmatch(r"(\w+)--(>0)?|--(\w+)|(\w+)-->0", ctxt)
        if m:
            v = m.group(1) or m.group(3) or m.group(4)
            st = self._value_before(v, L.idx)
            cnt = st.cost if st is not None else Cost.var(self.sym(v, "input"))
            if st is not None and st.num is not None:
                cnt = Cost.const(max(st.num, 1))
            what = "zestawów testowych" if v in TEST_COUNT_NAMES else "iteracji"
            return LoopInfo(cnt, "countdown", f"while ({v}--) → {cnt.expr()} {what}")
        # warunek z ||: while (i < m || j < r) — każdy wskaźnik idzie osobno, liczba obrotów = suma długości
        ors = c.split_top(a, b, ("||",))
        if len(ors) >= 2:
            tot = None
            for x, y in ors:
                part = None
                for v in dict.fromkeys(T[i] for i in range(x, y + 1) if c.toks[i].kind == "id"):
                    step = self._body_step(k, v)
                    cmp_ = self._cond_cmp((x, y), v) if step is not None else None
                    if cmp_ is None:
                        continue
                    start = self._value_before(v, L.idx) or Mag(_one(), num=0.0)
                    part = self._count_from(k, v, cmp_, step, start, L.line)
                    break
                if part is None or part.pattern == "unknown":
                    tot = None
                    break
                tot = part.count if tot is None else tot + part.count
            if tot is not None:
                return LoopInfo(tot, "two_pointers", "warunek z ||: każdy wskaźnik przesuwa się tylko do przodu → "
                                                     f"łącznie {tot.expr()} obrotów (suma długości zakresów)",
                                confidence=0.75)
        # ogólny: zmienna z warunku modyfikowana w ciele
        best = None
        for v in dict.fromkeys(ids):
            step = self._body_step(k, v)
            if step is None:
                continue
            cmp_ = self._cond_cmp(L.cond, v)
            if cmp_ is None:
                # while (x) / while (x > 0) z x /= 10
                if step[0] == "div":
                    st = self._value_before(v, L.idx)
                    src = st.cost if st is not None else Cost.var(self.sym(v, "input"))
                    if st is not None and st.num is not None:
                        return LoopInfo(Cost.const(math.log2(max(st.num, 2))), "log", "dzielenie → log kroków")
                    return LoopInfo(self._log(src), "log", f"{v} jest dzielone w każdym kroku → log({src.expr()})")
                if step[0] == "lin" and step[1] < 0:
                    st = self._value_before(v, L.idx)
                    src = st.cost if st is not None else Cost.var(self.sym(v, "input"))
                    return LoopInfo(src, "countdown", f"{v} maleje do zera → {src.expr()} iteracji")
                continue
            start = self._value_before(v, L.idx) or Mag(_one(), num=0.0)
            info = self._count_from(k, v, cmp_, step, start, L.line)
            # dwa wskaźniki w jednej pętli (kroki w różnych gałęziach)
            others = [w for w in dict.fromkeys(ids) if w != v and self._body_step(k, w)]
            if others and info.pattern in ("linear", "dependent"):
                info.note += " · dwa wskaźniki — każdy porusza się monotonicznie, łącznie liniowo"
                info.pattern = "two_pointers"
            if best is None or info.confidence > best.confidence:
                best = info
        if best is not None:
            return best
        # przejście po wskaźnikach: while (x != -1) x = nxt[x]
        for v in ids:
            for asg in c.assigns_in(body[0], body[1], v):
                if asg.op == "=" and asg.rhs and re.fullmatch(r"\w+\[" + re.escape(v) + r"\]",
                                                               normalize_expr(c.text(*asg.rhs))):
                    arr = normalize_expr(c.text(*asg.rhs)).split("[")[0]
                    size = self.csize(arr, L.idx)
                    if c.func_by_name.get(L.func or "") and self._is_dsu_array(arr):
                        return LoopInfo(self._log(size), "dsu_walk", "szukanie reprezentanta w DSU (z łączeniem wg "
                                                                    "rozmiaru: O(log n))", confidence=0.5)
                    return LoopInfo(size, "walk", f"przejście po wskaźnikach {arr}[…] — w najgorszym razie "
                                                  f"{size.expr()} kroków", confidence=0.55)
        return LoopInfo(Cost.var(self.sym("?", "unknown", source="warunek pętli while")), "unknown",
                        "nie rozpoznano, ile razy wykona się ta pętla", confidence=0.25)

    def _gcd_value_of(self, m1: Mag, m2: Mag, n1: str | None, n2: str | None) -> Cost:
        """Symbol wartości dla NWD(x, y): min(a,b) dla dwóch wczytanych liczb, V dla wartości z tablic/pętli."""
        nums = [m.num for m in (m1, m2) if m.num is not None]
        if nums:
            return Cost.const(max(min(abs(x) for x in nums), 2.0))
        plain = []
        for m, nm in ((m1, n1), (m2, n2)):
            if nm and re.fullmatch(r"\w+", nm) and nm in self.read_targets and nm not in self.iter_reads and \
                    "itervalue" not in m.flags and m.cost.vars() == {nm}:
                plain.append(nm)
        if len(plain) == 2 and plain[0] != plain[1]:
            a_, b_ = sorted(plain)
            return Cost.var(self.sym(f"min({a_}, {b_})", "value", None, None, f"mniejsza z liczb {a_}, {b_} (NWD)"))
        if len(plain) == 2:
            return Cost.var(plain[0])
        return Cost.var(self.sym("V", "value", None, None, "największa wartość liczb w danych"))

    def _gcd_value(self, f, cl, ga: str, gb: str) -> Cost:
        c = self.c
        names = [p.name for p in f.params]
        ms, ns = [], []
        for pn in (ga, gb):
            i = names.index(pn) if pn in names else -1
            if 0 <= i < len(cl.args):
                x, y = cl.args[i]
                ms.append(self.mag(x, y, cl.idx))
                ns.append(normalize_expr(c.text(x, y)))
            else:
                ms.append(Mag(Cost.var("V")))
                ns.append(None)
        return self._gcd_value_of(ms[0], ms[1], ns[0], ns[1])

    def _dsu_walk(self, k: int, var: str) -> LoopInfo | None:
        """while (p[x] != x) x = p[x]; — find w DSU. Z łączeniem wg rozmiaru/rangi albo kompresją ścieżki: O(log n)."""
        c = self.c
        L = c.loops[k]
        if not L.cond:
            return None
        ct = normalize_expr(c.text(*L.cond))
        v = re.escape(var)
        m = re.fullmatch(rf"(\w+)\[{v}\]!={v}|{v}!=(\w+)\[{v}\]", ct)
        if not m:
            return None
        arr = m.group(1) or m.group(2)
        body = normalize_expr(c.text(*L.body))
        f = c.enclosing_func(L.idx)
        dom = self._domain(self.csize(arr, L.idx))
        compress = bool(re.search(rf"{re.escape(arr)}\[{v}\]={re.escape(arr)}\[{re.escape(arr)}\[{v}\]\]", body))
        by_rank = self._dsu_union_by_rank(f, arr) if f is not None else False
        if by_rank or compress:
            how = "łączenie według rozmiaru/rangi" if by_rank else "skracanie ścieżki (p[x] = p[p[x]])"
            return LoopInfo(self._log(dom), "dsu_walk", f"find w DSU po tablicy {arr}: {how} → drzewa mają "
                                                        f"głębokość O(log n)", confidence=0.7)
        return LoopInfo(dom, "walk", f"find w DSU bez kompresji i bez łączenia według rozmiaru — ścieżka do korzenia "
                                     f"może mieć długość {dom.expr()}", confidence=0.6)

    def _is_dsu_array(self, arr: str) -> bool:
        return any(re.search(rf"{re.escape(arr)}\[\w+\]={re.escape(arr)}\[", normalize_expr(self.c.text(a.idx, a.rhs[1])))
                   for a in self.c.assigns if a.target == arr and a.rhs) or False

    def _binsearch(self, k: int) -> LoopInfo | None:
        c = self.c
        L = c.loops[k]
        a, b = L.cond
        ids = [c.texts[i] for i in range(a, b + 1) if c.toks[i].kind == "id"]
        uniq = list(dict.fromkeys(ids))
        if len(uniq) < 2:
            return None
        body = L.body
        mid_var = None
        lo = hi = None
        ternary = False
        floating = False
        for asg in c.assigns_in(body[0], body[1]):
            if asg.op != "=" or not asg.rhs or asg.indexed:
                continue
            r = normalize_expr(c.text(*asg.rhs))
            for x in uniq:
                for y in uniq:
                    if x == y:
                        continue
                    ex, ey = re.escape(x), re.escape(y)
                    if re.fullmatch(rf"\(?\(?{ex}\+{ey}(\+1)?\)?(/2|>>1|/2\.0|\*0\.5)\)?(\+1)?", r) or \
                            re.fullmatch(rf"{ex}\+\({ey}-{ex}\)(/2|>>1)(\+1)?", r) or \
                            re.fullmatch(rf"{ex}\+\({ey}-{ex}(\+1)?\)(/2|>>1)", r):
                        mid_var, lo, hi = asg.target, x, y
                        floating = "." in r
                    elif re.fullmatch(rf"{ex}\+\({ey}-{ex}\)/3|\(2\*{ex}\+{ey}\)/3|{ey}-\({ey}-{ex}\)/3", r):
                        mid_var, lo, hi, ternary = asg.target, x, y, True
            if mid_var:
                break
        if not mid_var:
            return None
        # (lo, hi) mogą być w złej kolejności — ustal po przypisaniach mid
        assigned = {x.target for x in c.assigns_in(body[0], body[1]) if x.rhs and mid_var in c.ids_in(*x.rhs)}
        if not ({lo, hi} & assigned) and not ternary:
            return None
        st_lo = self._value_before(lo, L.idx)
        st_hi = self._value_before(hi, L.idx)
        rng = None
        if st_hi is not None and st_lo is not None and st_hi.num is not None and st_lo.num is not None:
            v = abs(st_hi.num - st_lo.num)
            rng = Cost.const(max(v, 2))
            if floating:
                eps = 1e-9
                m = re.search(r"(\d+(?:\.\d+)?[eE]-?\d+|0\.\d+)", normalize_expr(c.text(a, b)))
                if m:
                    try:
                        eps = float(m.group(1))
                    except ValueError:
                        pass
                steps = math.log2(max(v, 1e-9) / max(eps, 1e-18))
                return LoopInfo(Cost.const(max(steps, 1)), "binary_search",
                                f"wyszukiwanie binarne na liczbach rzeczywistych → ok. {steps:.0f} kroków")
            steps = math.log2(max(v, 2)) * (1.71 if ternary else 1)
            nm = self.lit_sym(v, L.line) if v >= 1000 else None
            if nm:
                return LoopInfo(Cost.log(nm, 1.71 if ternary else 1.0), "binary_search",
                                f"wyszukiwanie {'ternarne' if ternary else 'binarne'} na przedziale o długości "
                                f"{fmt_num(v)} → ok. {steps:.0f} kroków")
            return LoopInfo(Cost.const(steps), "binary_search", f"wyszukiwanie binarne → {steps:.0f} kroków")
        src = st_hi if st_hi is not None and st_hi.num is None else (st_lo if st_lo is not None else None)
        if src is None or src.num is not None:
            src = Mag(Cost.var(self.sym("n", "input")))
        rng = src.cost
        return LoopInfo(self._log(rng, 1.71 if ternary else 1.0), "binary_search",
                        f"wyszukiwanie {'ternarne' if ternary else 'binarne'}: przedział [{lo}, {hi}] zmniejsza się "
                        f"{'o 1/3' if ternary else 'o połowę'} w każdym kroku → log({rng.expr()}) kroków")

    def _queue_loop(self, k: int, qname: str, ctxt: str) -> LoopInfo | None:
        c = self.c
        L = c.loops[k]
        d = c.decl_of(qname, L.idx)
        kind = d.kind if d else None
        if kind not in ("queue", "pq", "deque", "stack", "vector", "set", "multiset", "map"):
            return None
        body = L.body
        pushes_in = [cl for cl in c.calls if cl.receiver == qname and body[0] <= cl.idx <= body[1] and cl.name in (
            "push", "push_back", "emplace", "emplace_back", "push_front", "insert", "emplace_front")]
        pops_in = [cl for cl in c.calls if cl.receiver == qname and body[0] <= cl.idx <= body[1] and cl.name in (
            "pop", "pop_back", "pop_front", "erase", "extract")]
        # pętla po sąsiadach w środku?
        adj = None
        for k2 in L.children:
            g = self._adj_name(c.loops[k2])
            if g:
                adj = g
                break
        vector_queue = kind == "vector" and ("akt" in ctxt or re.search(r"size\(\)(>|==|!=)\w+|\w+<\w+\.size", ctxt))
        if kind == "vector" and not vector_queue and not pops_in:
            return None
        if adj:
            V = self.vertices_of(adj)
            E = self.edges_of(adj)
            if kind == "pq" or kind in ("set", "multiset"):
                note = (f"Dijkstra/kolejka priorytetowa: wierzchołki zdejmowane z kopca, krawędzie relaksowane — "
                        f"łącznie O(({V.expr()} + {E.expr()}) · log {V.expr()}) (kopiec ma ≤ {E.expr()} elementów, "
                        f"a log {E.expr()} ≤ 2 log {V.expr()})")
                info = LoopInfo(V + E, "pq_graph", note, confidence=0.75)
                self._pq_graph[qname] = V
            else:
                weighted_relax = self._relaxation_with_weight(body)
                note = f"przegląd grafu kolejką (BFS): każdy wierzchołek raz → {V.expr()} iteracji"
                info = LoopInfo(V, "bfs", note, confidence=0.75)
                if weighted_relax:
                    info.note += " · uwaga: relaksacja z wagami na zwykłej kolejce (SPFA) może w najgorszym razie " \
                                 "wrzucić wierzchołek wiele razy — O(V·E)"
                    self.warnings.append({"line": L.line - self.src_offset, "message":
                                          "Relaksacja z wagami na zwykłej kolejce (SPFA) — worst-case O(V·E). "
                                          "Dla wag nieujemnych użyj Dijkstry z priority_queue.", "type": "warn"})
            info.amort_children = adj  # type: ignore[attr-defined]
            return info
        # BFS po planszy / po stanach: wrzucamy tylko nieodwiedzone pola (tablica odwiedzin/odległości)
        if pushes_in and kind in ("queue", "deque", "stack", "vector"):
            st = self._bfs_states(k, pushes_in)
            if st is not None:
                cnt, arr, how = st
                return LoopInfo(cnt, "bfs", f"przegląd stanów kolejką: każdy stan ({arr}[…]) trafia do kolejki najwyżej "
                                            f"raz, bo pilnuje tego {how} → {cnt.expr()} obrotów", confidence=0.75)
        # kolejka/kopiec bez grafu: liczba iteracji = liczba elementów wrzuconych przed pętlą
        pushes_before = [cl for cl in c.calls if cl.receiver == qname and cl.idx < L.idx and cl.name in (
            "push", "push_back", "emplace", "emplace_back", "push_front", "insert")]
        total = None
        for cl in pushes_before:
            mm = self.mult_at(cl.idx)
            total = mm if total is None else total + mm
        if total is None:
            total = self.csize(qname, L.idx)
        if pushes_in and len(pushes_in) >= len(pops_in) and not any(c.innermost_loop(cl.idx) != k for cl in pushes_in):
            note = f"pętla na strukturze {qname}: elementy dokładane i zdejmowane — szacowane {total.expr()} iteracji"
            return LoopInfo(total, "queue", note, confidence=0.5)
        return LoopInfo(total, "queue", f"pętla, aż {qname} będzie pusta → {total.expr()} iteracji", confidence=0.6)

    def _bfs_states(self, k: int, pushes_in: list):
        """(liczba stanów, tablica, opis) dla BFS bez listy sąsiedztwa (plansza, liczby 1..n), jeśli wrzucanie do kolejki
        jest pilnowane tablicą odwiedzin/odległości: if (d[x][y] != -1) continue; d[x][y] = …; q.push(…)."""
        c = self.c
        L = c.loops[k]
        body_txt = normalize_expr(c.text(*L.body))
        for asg in c.assigns_in(L.body[0], L.body[1]):
            if not asg.indexed or asg.op != "=" or not asg.index_texts:
                continue
            X = asg.target
            ex = re.escape(X)
            idx_pat = r"\[[^\]]+\]" * len(asg.index_texts)
            guard = re.search(rf"!{ex}{idx_pat}|{ex}{idx_pat}(!=|==)(-1|0|false|INF|inf|\w*INF\w*|1e9|-INF)|"
                              rf"{ex}{idx_pat}(\)|\|\||&&)", body_txt)
            if not guard:
                continue
            if not any(abs(cl.idx - asg.idx) < 80 for cl in pushes_in):
                continue
            d = c.decl_of(X, asg.idx)
            if d is None:
                continue
            dims = []
            for pos, it in enumerate(asg.index_texts):
                itn = normalize_expr(it)
                bound = None
                mb = re.fullmatch(r"(\w+)([+-]\d+)?", itn)
                if mb:
                    v_ = re.escape(mb.group(1))
                    cands = [m.group(3) for m in re.finditer(rf"\b{v_}([+-]\d+)?(<|>=)(\w+)", body_txt)] + \
                        [m.group(1) for m in re.finditer(rf"\b(\w+)(>|<=){v_}([+-]\d+)?\b", body_txt)]
                    cands = [x for x in cands if not x.isdigit()]
                    if cands:
                        bound = self.ident(cands[0], L.idx).cost
                if bound is None:
                    if d.kind == "array" and pos < len(d.dims):
                        bound = self._text_mag(d.dims[pos], d.idx).cost
                    elif d.kind == "vector" and pos == 0 and d.ctor:
                        bound = self._text_mag(d.ctor[0], d.idx).cost
                    elif d.kind == "vector" and pos == 1 and len(d.ctor) >= 2:
                        m2 = re.search(r"\(([^,()]+)", d.ctor[1])
                        bound = self._text_mag(m2.group(1), d.idx).cost if m2 else None
                    if bound is None:
                        bound = self.csize(X, asg.idx)
                    if bound.is_const() and bound.const_value() >= 1000:
                        # stały wymiar tablicy (np. vis[1005][1005]) — dla planszy chodzi o wczytane n, m
                        guess = ("n", "m")[pos] if pos < 2 else None
                        if guess and guess in self.read_targets:
                            bound = Cost.var(self.sym(guess, "input"))
                    if len(asg.index_texts) == 1:
                        bound = self._domain(bound)
                dims.append(bound)
            cnt = _one()
            for b_ in dims:
                cnt = cnt * b_
            how = "tablica " + X
            return cnt, X, how
        return None

    def _relaxation_with_weight(self, body: tuple) -> bool:
        c = self.c
        txt = normalize_expr(c.text(*body))
        return bool(re.search(r"if\(\w+\[[^\]]+\]>\w+(\[[^\]]+\])?(\.first|\.second)?\+(?!1\))[\w\[]", txt))

    # ------------------------------------------------------------------ amortyzacja
    def _amortize(self, k: int, info: LoopInfo) -> LoopInfo:
        c = self.c
        L = c.loops[k]
        if L.parent is None:
            return info
        P = c.loops[L.parent]
        # 1) pętla po sąsiadach wewnątrz przeglądu grafu albo pętli po wierzchołkach
        if info.pattern == "adjacency" or self._is_adj_loop(L):
            g = self._adj_name(L)
            pinfo = self.loop(L.parent)
            idx_t = self._adj_index(L) or ""
            queue_index = bool(re.fullmatch(r"\w+\[\w+\]", idx_t)) and any(
                cl.receiver == idx_t.split("[")[0] and cl.name in ("push_back", "push", "emplace_back")
                for cl in c.calls if P.body[0] <= cl.idx <= P.body[1])
            if pinfo.pattern in ("bfs", "pq_graph", "queue", "two_pointers") or queue_index:
                E = self.edges_of(g) if g else Cost.var("m")
                over = pinfo.amort_over if pinfo.amort_over is not None else L.parent
                return LoopInfo(info.count, "adjacency", f"pętla po sąsiadach — łącznie dla całego przeglądu "
                                                         f"{E.expr()} iteracji (każda krawędź raz)",
                                amort_over=over, amort_total=E, confidence=0.8)
            idx_txt = self._adj_index(L)
            if P.var and idx_txt == P.var and pinfo.pattern in ("linear", "const"):
                E = self.edges_of(g) if g else Cost.var("m")
                return LoopInfo(info.count, "adjacency", f"suma stopni po wszystkich wierzchołkach = {E.expr()}",
                                amort_over=L.parent, amort_total=E, confidence=0.8)
            return info
        # 2) dwa wskaźniki: zmienna sterująca nie jest resetowana w ciele rodzica
        var = L.var
        if var and L.kind in ("while", "do") and var == P.var and P.kind in ("while", "do"):
            st = self._body_step(k, var)
            if st and st[0] == "lin" and st[1] > 0:
                pinfo = self.loop(L.parent)
                return LoopInfo(info.count, "two_pointers",
                                f"ta sama zmienna {var} rośnie w obu pętlach → łącznie {_cnt_str(pinfo.count)} kroków",
                                confidence=0.75, amort_over=L.parent, amort_total=pinfo.count)
        if var and L.kind in ("while", "for", "do") and var != P.var:
            if L.kind == "for" and L.init and L.init[0] <= L.init[1]:
                return info
            moved = self._body_step(k, var) if L.kind != "for" else (
                self._step_kind(*L.step, var, L.idx) if L.step else None)
            if moved and moved[0] in ("lin",) and info.pattern not in ("binary_search", "countdown", "log"):
                resets = [a for a in c.assigns_in(P.body[0], P.body[1], var)
                          if a.op == "=" and a.target_text == var and not (L.body[0] <= a.idx <= L.body[1])
                          and a.idx != L.idx and not self._monotone_reset(a, var, P)]
                declared_inside = any(d.name == var and P.body[0] <= d.idx <= P.body[1] for d in c.decls)
                if not resets and not declared_inside:
                    pinfo = self.loop(L.parent)
                    cmp_ = self._cond_cmp(L.cond, var)
                    rng = info.count if info.pattern != "unknown" else pinfo.count
                    if info.pattern in ("bfs", "pq_graph", "queue"):
                        cmp_ = None
                    if cmp_:
                        bm = self.mag(cmp_[2][0], cmp_[2][1], L.idx)
                        if bm.num is None:
                            rng = bm.cost
                    return LoopInfo(info.count, "two_pointers",
                                    f"dwa wskaźniki: {var} tylko rośnie i nie jest cofany w pętli zewnętrznej → "
                                    f"łącznie {rng.expr()} kroków (amortyzacja)",
                                    amort_over=L.parent, amort_total=rng, confidence=0.75)
        # 2b) gąsienica: w ciele pętli wewnętrznej rośnie wskaźnik, który nie jest cofany w pętli zewnętrznej
        if L.kind in ("while", "do") and info.pattern not in ("binary_search", "countdown", "log", "bfs",
                                                                "pq_graph", "queue"):
            for asg in c.assigns_in(L.body[0], L.body[1]):
                if asg.indexed or asg.target_text != asg.target:
                    continue
                if c.innermost_loop(asg.idx) != k:
                    continue
                p = asg.target
                if p == P.var:
                    continue
                # licznik (cnt++) to nie wskaźnik: wskaźnik ogranicza pętlę (warunek) albo indeksuje tablicę
                if L.cond and p not in c.ids_in(*L.cond) and not re.search(
                        rf"\w\[{re.escape(p)}([+-]\d+)?\]", normalize_expr(c.text(*L.body))):
                    continue
                e = c.stmt_end(asg.idx)
                sk = self._step_kind(asg.idx, e - 1 if c.texts[e] == ";" else e, p, asg.idx)
                if not sk or sk[0] != "lin":
                    continue
                resets = [x for x in c.assigns_in(P.body[0], P.body[1], p)
                          if x.op == "=" and x.target_text == p and not (L.body[0] <= x.idx <= L.body[1])
                          and not self._monotone_reset(x, p, P)]
                declared_inside = any(dd.name == p and P.body[0] <= dd.idx <= P.body[1] for dd in c.decls)
                if resets or declared_inside:
                    continue
                # zakres wskaźnika: rozmiar tablicy indeksowanej przez p albo liczba iteracji pętli zewnętrznej
                rng = None
                for i in range(L.body[0], L.body[1]):
                    if c.toks[i].kind == "id" and c.texts[i + 1] == "[" and (i + 1) in c.pairs and \
                            normalize_expr(c.text(i + 2, c.pairs[i + 1] - 1)) in (p, p + "+1", p + "-1"):
                        rng = self.csize(c.texts[i], i)
                        break
                pinfo = self.loop(L.parent)
                if rng is None:
                    rng = pinfo.count
                return LoopInfo(info.count, "two_pointers",
                                f"gąsienica / dwa wskaźniki: {p} tylko rośnie i nie jest cofany → łącznie "
                                f"{rng.expr()} kroków dla całej pętli zewnętrznej (amortyzacja)",
                                confidence=0.7, amort_over=L.parent, amort_total=rng)
        # 2c) funkcja prefiksowa / KMP: while (k > 0 && …) k = pi[k - 1]; a w pętli zewnętrznej pi[i] = k
        if L.kind in ("while", "do") and info.pattern in ("walk", "unknown", "linear", "countdown") and P.var:
            for asg in c.assigns_in(L.body[0], L.body[1]):
                if asg.op != "=" or not asg.rhs or asg.indexed:
                    continue
                v = asg.target
                m = re.fullmatch(rf"(\w+)\[{re.escape(v)}([+-]\d+)?\]", normalize_expr(c.text(*asg.rhs)))
                if not m:
                    continue
                arr = m.group(1)
                if any(x.target == arr and x.indexed and x.index_texts and
                       normalize_expr(x.index_texts[0]) in (P.var, P.var + "+1")
                       for x in c.assigns_in(P.body[0], P.body[1])):
                    pinfo = self.loop(L.parent)
                    return LoopInfo(info.count, "amortized_walk",
                                    f"funkcja prefiksowa (KMP): {v} cofa się po tablicy {arr}, a w każdym obrocie pętli "
                                    f"zewnętrznej rośnie najwyżej o 1 → łącznie najwyżej {_cnt_str(pinfo.count)} "
                                    f"cofnięć (amortyzacja)", amort_over=L.parent, amort_total=pinfo.count,
                                    confidence=0.8)
        # 3) stos monotoniczny / kolejka monotoniczna
        if L.kind == "while" and L.cond:
            ctxt = normalize_expr(c.text(*L.cond))
            m = re.search(r"!(\w+)\.empty\(\)&&|(\w+)\.size\(\)(>0)?&&|&&!(\w+)\.empty\(\)", ctxt)
            if m:
                st = m.group(1) or m.group(2) or m.group(4)
                pops = [cl for cl in c.calls if cl.receiver == st and L.body[0] <= cl.idx <= L.body[1] and
                        cl.name in ("pop", "pop_back", "pop_front")]
                pushes = [cl for cl in c.calls if cl.receiver == st and P.body[0] <= cl.idx <= P.body[1] and
                          not (L.body[0] <= cl.idx <= L.body[1]) and cl.name in ("push", "push_back", "emplace",
                                                                                  "emplace_back", "push_front")]
                if pops and pushes:
                    pinfo = self.loop(L.parent)
                    return LoopInfo(info.count, "monotonic_stack",
                                    f"każdy element trafia na {st} raz i schodzi najwyżej raz → łącznie "
                                    f"{pinfo.count.expr()} zdjęć (amortyzacja)",
                                    amort_over=L.parent, amort_total=pinfo.count, confidence=0.8)
        return info

    def _monotone_reset(self, asg, var: str, P) -> bool:
        """Przypisanie, które nie cofa wskaźnika: j = i, j = i + 1 (i rośnie w pętli zewnętrznej), j = max(j, …)."""
        if not asg.rhs:
            return False
        r = normalize_expr(self.c.text(*asg.rhs))
        pv = P.var
        if pv and re.fullmatch(rf"{re.escape(pv)}(\+\d+)?", r):
            st = self._step_kind(*P.step, pv, P.idx) if (P.kind == "for" and P.step) else None
            return st is None or (st[0] == "lin" and st[1] > 0)
        ev = re.escape(var)
        return bool(re.fullmatch(rf"max\({ev},.+\)|max\(.+,{ev}\)", r))

    def _is_adj_loop(self, L) -> bool:
        return self._adj_name(L) is not None

    def _adj_access(self, L):
        """(nazwa_kontenera, tekst_indeksu) jeśli pętla L przechodzi po elemencie kontenera zagnieżdżonego
        (for (auto u : g[v]) albo for (i < g[v].size()))."""
        c = self.c
        T = c.texts
        if L.kind == "range":
            a, b = L.range_expr
        elif L.cond:
            a, b = L.cond
        else:
            return None
        for i in range(a, b + 1):
            if c.toks[i].kind != "id" or i + 1 > b or T[i + 1] != "[" or (i + 1) not in c.pairs:
                continue
            if i > a and T[i - 1] in (".", "->"):
                continue
            j = c.pairs[i + 1]
            ok = False
            if L.kind == "range" and j == b:
                ok = True
            elif j + 3 <= b + 3 and j + 2 < c.n and T[j + 1] in (".", "->") and T[j + 2] in ("size", "length"):
                ok = True
            if not ok:
                continue
            g = T[i]
            d = c.decl_of(g, L.idx)
            if d and (d.nested >= 2 or (d.kind == "array" and re.search(r"vector|set|deque|list|string", d.type))):
                return g, normalize_expr(c.text(i + 2, j - 1))
        return None

    def _adj_name(self, L) -> str | None:
        r = self._adj_access(L)
        return r[0] if r else None

    def _adj_index(self, L) -> str | None:
        r = self._adj_access(L)
        return r[1] if r else None

    # ------------------------------------------------------------------ krotności
    def mult(self, k: int) -> Cost:
        """Ile razy wykonuje się ciało pętli k (względem jednego wejścia do funkcji)."""
        if k in self._mult:
            return self._mult[k]
        key = ("mult", k)
        if key in self._busy:
            return Cost.var("n")
        self._busy.add(key)
        try:
            c = self.c
            L = c.loops[k]
            info = self.loop(k)
            if info.amort_over is not None:
                # łączna liczba iteracji na jedno wejście do pętli-przodka A × liczba wejść do A
                A = c.loops[info.amort_over]
                entries = self.mult_at(A.idx) if A.parent is not None else _one()
                res = entries * info.amort_total
            else:
                parent = self.mult_at(L.idx) if L.parent is not None else _one()
                res = parent * info.count
        finally:
            self._busy.discard(key)
        self._mult[k] = res
        return res

    def mult_at(self, idx: int) -> Cost:
        """Krotność wykonania pozycji idx (z uwzględnieniem gałęzi kończących się return/break)."""
        c = self.c
        k = c.innermost_loop(idx)
        if k is None:
            return _one()
        ex = self._exit_kind(idx, k)
        if ex == "break":
            return self.mult_at(c.loops[k].idx)
        if ex == "return":
            return _one()
        return self.mult(k)

    def _exit_kind(self, idx: int, k: int) -> str | None:
        """'return'/'break', jeśli pozycja idx leży (w pętli k) w bloku if/else, który kończy się return/break —
        taki kod wykona się najwyżej raz na wejście do pętli (break) albo do funkcji (return)."""
        key = (idx, k)
        cache = self.__dict__.setdefault("_exit_cache", {})
        if key in cache:
            return cache[key]
        c = self.c
        L = c.loops[k]
        T = c.texts
        res = None
        lo = L.body[0]
        for i in range(lo, idx):
            t = T[i]
            if t == "if":
                if i + 1 >= c.n or T[i + 1] != "(" or (i + 1) not in c.pairs:
                    continue
                ts = c.pairs[i + 1] + 1
            elif t == "else" and i + 1 < c.n and T[i + 1] != "if":
                ts = i + 1
            else:
                continue
            if ts >= c.n:
                continue
            te = c.stmt_end(ts)
            if not (ts <= idx <= te):
                continue
            if T[ts] == "{" and te - 1 >= ts + 1:
                stmts = c.block_statements(ts + 1, te - 1)
                ls = stmts[-1][0] if stmts else ts
            else:
                ls = ts
            # break w zagnieżdżonej pętli/switchu nie wychodzi z pętli k
            if T[ls] == "break" and (c.innermost_loop(ls) != k or "switch" in T[L.body[0]:ls]):
                continue
            if T[ls] in ("return", "break") or (T[ls] == "exit" and ls + 1 < c.n and T[ls + 1] == "("):
                res = "break" if T[ls] == "break" else "return"
                break
        cache[key] = res
        return res

    # ------------------------------------------------------------------ koszt wywołań
    def range_arg_size(self, args: list, at: int) -> Cost:
        """Rozmiar zakresu z argumentów (begin, end) albo all(v); fragment [l, r) z zapytania ≤ rozmiar kontenera."""
        res = self._range_arg_size(args, at)
        if res.vars() & self.iter_reads and args:
            ids = self.c.ids_in(*args[0])
            d = self.c.decl_of(ids[0], at) if ids else None
            if d is not None and d.kind in ("vector", "array", "string", "deque"):
                dom = self._domain(self.csize(ids[0], at))
                if not (dom.vars() & self.iter_reads):
                    return dom
        return res

    def _range_arg_size(self, args: list, at: int) -> Cost:
        c = self.c
        if not args:
            return Cost.var("n")
        t0 = normalize_expr(c.text(*args[0]))
        m = re.fullmatch(r"(?:all|ALL|rall)\((\w+)\)", t0)
        if m:
            return self.csize(m.group(1), at)
        if len(args) >= 2:
            t1 = normalize_expr(c.text(*args[1]))
            m0 = re.fullmatch(r"(\w+)\.r?begin\(\)", t0)
            m1 = re.fullmatch(r"(\w+)\.r?end\(\)", t1)
            if m0 and m1 and m0.group(1) == m1.group(1):
                return self.csize(m0.group(1), at)
            m1b = re.fullmatch(r"(\w+)\.begin\(\)\+(.+)", t1)
            if m1b:
                plus = c.find_top(args[1][0], args[1][1], ("+",))
                if plus:
                    return self.mag(plus[0] + 1, args[1][1], at).cost
            plus = c.find_top(args[1][0], args[1][1], ("+",))
            if plus:
                return self.mag(plus[0] + 1, args[1][1], at).cost
            mm = re.fullmatch(r"(\w+)\.begin\(\)", t0)
            if mm:
                return self.csize(mm.group(1), at)
        ids = c.ids_in(*args[0])
        return self.csize(ids[0], at) if ids else Cost.var("n")

    def _nested_range(self, cl):
        """sort(g[i].begin(), g[i].end()) w pętli po i — suma po wszystkich listach to E log E."""
        c = self.c
        if not cl.args:
            return None
        t0 = normalize_expr(c.text(*cl.args[0]))
        m = re.fullmatch(r"(\w+)\[(\w+)\]\.begin\(\)", t0)
        if not m:
            return None
        g, iv = m.group(1), m.group(2)
        d = c.decl_of(g, cl.idx)
        if not d or not (d.nested >= 2 or d.kind == "array"):
            return None
        E = self.edges_of(g)
        k = c.innermost_loop(cl.idx)
        if k is not None and c.loops[k].var == iv:
            return E * self._log(E) * _inv(self.loop(k).count)
        return E * self._log(E)

    def call_cost(self, cl, stack: tuple = ()) -> tuple[Cost, str | None]:
        """(koszt jednego wykonania wywołania, opis). Koszty funkcji użytkownika w symbolach parametrów
        wywołującego (§x)."""
        c = self.c
        name = cl.name
        rk = None
        if cl.is_method and cl.receiver:
            d = c.decl_of(cl.receiver, cl.idx)
            rk = d.kind if d else None
            indexed = cl.receiver_text != cl.receiver
            if indexed and d is not None and d.kind in ("vector", "array"):
                # g[v].push_back — kontener w kontenerze
                inner_type = d.elem if d.kind == "vector" else d.type
                rk = c.classify_type(inner_type)[0] if d.kind == "vector" else c.classify_type(d.type)[0]
            size = self.edges_of(cl.receiver) if indexed else self.csize(cl.receiver, cl.idx)
            if rk in LOG_KINDS and name in LOG_METHODS:
                if cl.receiver in self._pq_graph:
                    size = self._pq_graph[cl.receiver]
                return self._log(size), f"{cl.receiver}.{name}: O(log n)"
            if rk in HASH_KINDS and name in HASH_METHODS:
                return Cost.const(3), f"{cl.receiver}.{name}: średnio O(1)"
            if rk == "pq" and name in PQ_LOG:
                if cl.receiver in self._pq_graph:
                    size = self._pq_graph[cl.receiver]
                return self._log(size), f"{cl.receiver}.{name}: O(log n)"
            if rk in ("vector", "string", "deque") and name in ("insert", "erase"):
                if cl.args and re.search(r"begin\(\)", c.text(*cl.args[0])) and rk != "deque":
                    return size, f"{cl.receiver}.{name} w środku/na początku: O(n)"
                if rk == "deque":
                    return _one(), None
                return size, f"{cl.receiver}.{name}: O(n)"
            if rk == "string" and name in ("substr", "find", "rfind", "compare", "replace", "find_first_of"):
                return size, f"{cl.receiver}.{name}: O(długość)"
            if rk in ("vector", "string", "deque") and name in ("resize", "assign") and cl.args:
                return self.mag(cl.args[0][0], cl.args[0][1], cl.idx).cost, f"{cl.receiver}.{name}: O(rozmiar)"
            if rk == "bitset" and name in ("count", "any", "none", "all") or rk == "bitset" and name in (
                    "set", "reset", "flip") and not cl.args:
                return size * (1 / 64), f"{cl.receiver}.{name}: O(n/64)"
            if rk in ("set", "multiset", "map", "multimap", "umap", "uset", "vector", "string", "deque") and \
                    name == "clear":
                return _one(), None
            if name in c.func_by_name and rk in ("struct", "other", None):
                return self.user_call_cost(cl, stack)
            return _one(), None
        # własna funkcja o nazwie jak w STL (find, count, merge, …) — liczy się ciało funkcji ucznia
        if name in c.func_by_name and not cl.std:
            return self.user_call_cost(cl, stack)
        if name in NLOGN_FREE:
            nested = self._nested_range(cl)
            if nested is not None:
                return nested, f"{name} na każdej liście — łącznie O(E log E)"
            n = self.range_arg_size(cl.args, cl.idx)
            return n * self._log(n), f"{name}: O(n log n) dla n = {n.expr()}"
        if name in LOG_FREE:
            t0 = normalize_expr(c.text(*cl.args[0])) if cl.args else ""
            m0 = re.fullmatch(r"(\w+)\.begin\(\)", t0)
            if m0:
                d = c.decl_of(m0.group(1), cl.idx)
                if d and d.kind in ("set", "multiset", "map", "multimap"):
                    n = self.csize(m0.group(1), cl.idx)
                    self.warnings.append({"line": cl.line - self.src_offset, "type": "warn", "message":
                                          f"std::{name} na {d.kind} działa w O(n) — użyj {m0.group(1)}.{name}(x)."})
                    return n, f"std::{name} na secie/mapie: O(n)!"
            n = self.range_arg_size(cl.args, cl.idx)
            return self._log(n), f"{name}: O(log n)"
        if name in LINEAR_FREE:
            n = self.range_arg_size(cl.args, cl.idx)
            return n, f"{name}: O(n)"
        if name in ("memset", "memcpy", "fill_n") and cl.args:
            ids = c.ids_in(*cl.args[0])
            n = self.csize(ids[0], cl.idx) if ids else Cost.var("n")
            return n, f"{name}: O(rozmiar tablicy) = {n.expr()}"
        if name in ("__gcd", "gcd", "lcm", "__lcm") and len(cl.args) == 2:
            m1 = self.mag(*cl.args[0], cl.idx)
            m2 = self.mag(*cl.args[1], cl.idx)
            v = self._gcd_value_of(m1, m2, normalize_expr(c.text(*cl.args[0])), normalize_expr(c.text(*cl.args[1])))
            return self._log(v), f"{name}: O(log {v.expr()}) (algorytm Euklidesa)"
        if name in ("pow", "powl", "sqrt", "sqrtl", "log", "log2", "exp", "atan2", "sin", "cos", "hypot", "cbrt"):
            return Cost.const(4), None
        if name in ("next_permutation", "prev_permutation"):
            return Cost.const(2), None
        if name == "getline":
            return _one(), None
        if name in c.func_by_name:
            return self.user_call_cost(cl, stack)
        return _one(), None

    def user_call_cost(self, cl, stack: tuple = ()) -> tuple[Cost, str | None]:
        c = self.c
        f = c.func_by_name.get(cl.name)
        if f is None:
            return _one(), None
        caller = c.enclosing_func(cl.idx)
        if caller is not None and caller.name == f.name:
            return _one(), None           # wywołanie rekurencyjne liczone w analizie rekurencji
        if f.name in stack:
            return Cost.var("§N"), None
        fi = self.func(f.name, stack)
        mapping = {}
        extra = _one() * 0
        for i, p in enumerate(f.params):
            if i < len(cl.args):
                a, b = cl.args[i]
                mm = self.mag(a, b, cl.idx)
                if p.kind in ("vector", "string", "set", "multiset", "map", "multimap", "umap", "uset", "deque") \
                        and not p.is_ref:
                    ids = c.ids_in(a, b)
                    if ids:
                        csz = self.csize(ids[0], cl.idx)
                        mapping["§" + p.name] = csz
                        extra = extra + csz
                        continue
                if p.is_ref and p.kind in ("vector", "string", "set", "map", "umap", "deque", "multiset"):
                    ids = c.ids_in(a, b)
                    if ids:
                        mapping["§" + p.name] = self.csize(ids[0], cl.idx)
                        continue
                if "itervalue" in mm.flags and (f.name, p.name) in self._index_params:
                    mapping["§" + p.name] = self._index_params[(f.name, p.name)]
                    continue
                mapping["§" + p.name] = mm.cost
        if f.name in self._gcd_funcs:
            ga, gb = self._gcd_funcs[f.name]
            vs = self._gcd_value(f, cl, ga, gb)
            for pn in (ga, gb):
                mapping["§" + pn] = vs
        bounds = (fi.recursion or {}).get("bounds") or {}
        if fi.size_params:
            for sp in fi.size_params:
                if bounds.get(sp) and (fi.recursion or {}).get("type") not in ("memo",):
                    b = bounds[sp]
                    mapping["§" + sp] = Cost.var(b if b in c.consts else self.sym(b, "input"))
            sizes = [mapping.get("§" + p) for p in fi.size_params if mapping.get("§" + p) is not None]
            if sizes:
                tot = sizes[0]
                for s_ in sizes[1:]:
                    tot = tot + s_
                mapping["§N"] = tot.simplified()
        per = fi.per_call.subst(mapping)
        if fi.one_time is not None:
            ot = fi.one_time.subst(mapping)
            self._register_one_time(f, cl, ot)
        if not extra.is_zero():
            per = per + extra
            self.warnings.append({"line": cl.line - self.src_offset, "type": "warn", "message":
                                  f"Kontener przekazywany przez wartość do {f.name}() — kopiowany przy każdym "
                                  f"wywołaniu. Użyj const &."})
        return per, f"wywołanie {f.name}(): {per.big_o()}"

    def _register_one_time(self, f, cl, cost: Cost):
        c = self.c
        fi = self.func_info.get(f.name)
        factor = _one()
        # reset tablicy odwiedzin/memo w pętli → koszt mnożony przez tę pętlę
        if fi and fi.reset_arrays:
            loops_call = set(c.loops_containing(cl.idx))
            for arr in fi.reset_arrays:
                for idx in self._reset_sites(arr):
                    common = loops_call & set(c.loops_containing(idx))
                    if common:
                        kk = max(common, key=lambda z: c.loops[z].depth)
                        factor = self.mult(kk)
        for (fn, ln, _) in self.one_time:
            if fn == f.name and ln == cl.line:
                return
        self.one_time.append((f.name, cl.line, cost * factor))

    def _reset_sites(self, arr: str) -> list[int]:
        c = self.c
        res = []
        for cl in c.calls:
            if cl.name in ("memset", "fill", "fill_n") and cl.args and arr in c.ids_in(*cl.args[0]):
                res.append(cl.idx)
            if cl.receiver == arr and cl.name in ("assign", "clear", "resize"):
                res.append(cl.idx)
        for a in c.assigns:
            if a.target == arr and a.indexed and a.rhs and normalize_expr(c.text(*a.rhs)) in (
                    "0", "false", "-1", "INF", "inf") and c.innermost_loop(a.idx) is not None:
                f = c.enclosing_func(a.idx)
                if f is None or f.name not in self.func_info or True:
                    res.append(a.idx)
        return res

    # ------------------------------------------------------------------ koszty funkcji
    def _stmt_starts(self, a: int, b: int) -> list[int]:
        """Indeksy pierwszych tokenów instrukcji w zakresie [a, b]."""
        c = self.c
        T = c.texts
        res = []
        header_close = set()
        inside_header = set()
        for L in c.loops:
            if L.kind != "do":
                header_close.add(L.head[1])
            if L.kind in ("for", "range"):
                inside_header.update(range(L.head[0], L.head[1] + 1))
        for i in range(a, b + 1):
            if T[i] in ("{", "}", ";", ")") or i in inside_header:
                continue
            p = T[i - 1] if i > 0 else ";"
            starts = p in (";", "{", "}", "else", "do") or (p == ")" and (i - 1) in header_close) or \
                (p == ")" and (i - 1) in c.pairs and c.pairs[i - 1] > 0 and T[c.pairs[i - 1] - 1] == "if")
            if starts:
                # pomiń średniki w nagłówku for(;;)
                res.append(i)
        return res

    def body_cost(self, f, stack: tuple = (), record: bool = False, exclude_self: bool = True) -> Cost:
        """Suma kosztów linii w ciele funkcji (względem jednego wywołania)."""
        c = self.c
        total = _one() * 0
        lines_seen: dict[int, Cost] = {}
        a, b = f.open_idx + 1, f.close_idx - 1
        # podstawowy koszt instrukcji: każda instrukcja liczona w swojej linii z krotnością pętli
        loop_at = {L.idx: z for z, L in enumerate(c.loops)}
        for i in self._stmt_starts(a, b):
            ln = c.toks[i].line
            if i in loop_at:
                m = self.mult(loop_at[i])
            else:
                m = self.mult_at(i)
            lines_seen[ln] = lines_seen.get(ln, _one() * 0) + m
        # wywołania
        for cl in c.calls:
            if not (a <= cl.idx <= b):
                continue
            cc, why = self.call_cost(cl, stack + (f.name,))
            if cc.is_zero():
                continue
            m = self.mult_at(cl.idx)
            add = m * cc
            lines_seen[cl.line] = lines_seen.get(cl.line, _one() * 0) + add
            if record and why and not cc.is_const():
                self.call_costs.append({"line": cl.line - self.src_offset, "what": why, "func": f.name})
        # indeksowanie mapy m[x] → log
        for i in range(a, b):
            if c.toks[i].kind == "id" and c.texts[i + 1] == "[" and (i == 0 or c.texts[i - 1] not in (".", "->")):
                d = c.decl_of(c.texts[i], i)
                if d and d.kind in LOG_KINDS:
                    add = self.mult_at(i) * self._log(self.csize(c.texts[i], i))
                    lines_seen[c.toks[i].line] = lines_seen.get(c.toks[i].line, _one() * 0) + add
                elif d and d.kind in HASH_KINDS:
                    add = self.mult_at(i) * 3
                    lines_seen[c.toks[i].line] = lines_seen.get(c.toks[i].line, _one() * 0) + add
        # konkatenacja napisów s = s + x, kopie kontenerów, kontenery tworzone w pętli
        for asg in c.assigns:
            if not (a <= asg.idx <= b) or asg.op != "=" or not asg.rhs:
                continue
            d = c.decl_of(asg.target, asg.idx)
            if d is None or asg.indexed:
                continue
            rtxt = normalize_expr(c.text(*asg.rhs))
            if d.kind == "string" and re.match(rf"{re.escape(asg.target)}\+", rtxt):
                add = self.mult_at(asg.idx) * self.csize(asg.target, asg.idx)
                lines_seen[asg.line] = lines_seen.get(asg.line, _one() * 0) + add
                self.warnings.append({"line": asg.line - self.src_offset, "type": "warn", "message":
                                      f"{asg.target} = {asg.target} + … kopiuje cały napis (O(n) za każdym razem) — "
                                      f"użyj {asg.target} += …"})
            elif d.kind in ("vector", "string", "set", "map", "deque") and re.fullmatch(r"\w+", rtxt):
                d2 = c.decl_of(rtxt, asg.idx)
                if d2 and d2.kind == d.kind and c.innermost_loop(asg.idx) is not None:
                    add = self.mult_at(asg.idx) * self.csize(rtxt, asg.idx)
                    lines_seen[asg.line] = lines_seen.get(asg.line, _one() * 0) + add
        for d in c.decls:
            if not (a <= d.idx <= b) or d.is_param:
                continue
            if d.kind in ("vector", "string", "deque") and d.ctor:
                k = c.innermost_loop(d.idx)
                if k is not None:
                    sz = self._text_mag(d.ctor[0], d.idx).cost
                    lines_seen[d.line] = lines_seen.get(d.line, _one() * 0) + self.mult(k) * sz
        if record:
            for ln, cst in lines_seen.items():
                self.line_cost[ln] = cst
                self.line_func[ln] = f.name
        for cst in lines_seen.values():
            total = total + cst
        return total

    def func(self, name: str, stack: tuple = ()) -> FuncInfo:
        if name in self.func_info:
            return self.func_info[name]
        if name in self._busy_funcs:
            return FuncInfo(per_call=Cost.var("§N"))
        self._busy_funcs.add(name)
        try:
            f = self.c.func_by_name[name]
            if f.self_calls:
                fi = self._recursive(f, stack)
            else:
                fi = FuncInfo(per_call=self.body_cost(f, stack))
        finally:
            self._busy_funcs.discard(name)
        self.func_info[name] = fi
        return fi

    # ------------------------------------------------------------------ rekurencja
    def _arg_kind(self, f, call, pi: int) -> str:
        c = self.c
        if pi >= len(call.args) or pi >= len(f.params):
            return "other"
        p = f.params[pi].name
        a, b = call.args[pi]
        t = normalize_expr(c.text(a, b))
        ep = re.escape(p)
        if t == p:
            return "same"
        if re.fullmatch(rf"{ep}-\d+|{ep}-1", t):
            return "dec"
        if re.fullmatch(rf"{ep}\+\d+", t):
            return "inc"
        if re.fullmatch(rf"{ep}(/2|>>1|/3)|\({ep}\)/2", t):
            return "half"
        if re.fullmatch(rf"2\*{ep}(\+1)?|{ep}\*2(\+1)?|{ep}<<1(\|1)?|\({ep}<<1\)(\|1|\+1)?|2\*{ep}\+2", t):
            return "double"
        if re.search(rf"\(\w+\+\w+\)/2|\(\w+\+\w+\)>>1", t):
            return "half"
        ids = c.ids_in(a, b)
        # zmienna lokalna typu mid = (l + r) / 2
        for v in ids:
            if v in (x.name for x in f.params):
                continue
            for asg in c.assigns_in(f.open_idx, f.close_idx, v):
                if asg.op == "=" and asg.rhs:
                    r = normalize_expr(c.text(*asg.rhs))
                    if re.search(r"\(\w+\+\w+\)(/2|>>1)|\w+\+\(\w+-\w+\)/2|/2$|>>1$", r):
                        return "half"
        if re.search(rf"%{ep}\b|\b{ep}%", t) or re.fullmatch(r"\w+%\w+", t):
            return "mod"
        if re.fullmatch(rf"\w+\[{ep}\]", t):
            return "index"
        return "other"

    def _if_structs(self, f) -> list:
        """Lista (then_range, else_range|None, then_returns, stmt_end) dla instrukcji if w funkcji."""
        c = self.c
        res = []
        for i in range(f.open_idx, f.close_idx):
            if c.texts[i] != "if" or i + 1 >= c.n or c.texts[i + 1] != "(" or (i + 1) not in c.pairs:
                continue
            cc = c.pairs[i + 1]
            ts, te = c.body_range(cc + 1)
            else_r = None
            end = te
            if te + 1 < c.n and c.texts[te + 1] == "else":
                es, ee = c.body_range(te + 2)
                else_r = (es, ee)
                end = ee
            then_returns = "return" in c.texts[ts:te + 1]
            res.append(((ts, te), else_r, then_returns, end))
        # operator ?:
        for i in range(f.open_idx, f.close_idx):
            if c.texts[i] == "?":
                col = None
                depth = 0
                for j in range(i + 1, f.close_idx):
                    t = c.texts[j]
                    if t in ("(", "["):
                        depth += 1
                    elif t in (")", "]"):
                        if depth == 0:
                            break
                        depth -= 1
                    elif t == ":" and depth == 0:
                        col = j
                        break
                    elif t in (";", ",") and depth == 0:
                        break
                if col:
                    e = col + 1
                    depth = 0
                    while e < f.close_idx:
                        t = c.texts[e]
                        if t in ("(", "["):
                            depth += 1
                        elif t in (")", "]"):
                            if depth == 0:
                                break
                            depth -= 1
                        elif t in (";", ",") and depth == 0:
                            break
                        e += 1
                    res.append(((i + 1, col - 1), (col + 1, e - 1), False, e - 1))
        return res

    def _guard_text(self, f, cl) -> str:
        """Warunki if obejmujące wywołanie + wcześniejsze `if (…) continue/return` w tej samej pętli."""
        c = self.c
        parts = []
        for i in range(f.open_idx, cl.idx):
            if c.texts[i] != "if" or c.texts[i + 1] != "(" or (i + 1) not in c.pairs:
                continue
            cc = c.pairs[i + 1]
            ts, te = c.body_range(cc + 1)
            if ts <= cl.idx <= te or (te < cl.idx and c.texts[cc + 1] in ("continue", "return")):
                parts.append(normalize_expr(c.text(i + 2, cc - 1)) +
                             ("#skip" if te < cl.idx else ""))
        return " ".join(parts)

    def _exclusive_calls(self, f, calls) -> int:
        """Ile wywołań rekurencyjnych wykona się w jednym wywołaniu funkcji (maksymalny zbiór wywołań, które nie
        wykluczają się nawzajem: różne gałęzie if/else, ?:, albo wcześniejszy return)."""
        if len(calls) <= 1:
            return len(calls)
        ifs = self._if_structs(f)

        def excl(x, y):
            a, b = x.idx, y.idx
            for (ts, te), er, tr, end in ifs:
                in_t = lambda z: ts <= z <= te
                in_e = (lambda z: er[0] <= z <= er[1]) if er else (lambda z: False)
                if (in_t(a) and in_e(b)) or (in_t(b) and in_e(a)):
                    return True
                if tr and ((in_t(a) and b > end) or (in_t(b) and a > end)):
                    return True
            return False
        n = len(calls)
        best = 1
        idxs = list(range(n))[:8]
        from itertools import combinations
        for r in range(len(idxs), 1, -1):
            for comb in combinations(idxs, r):
                if all(not excl(calls[i], calls[j]) for i, j in combinations(comb, 2)):
                    return r
        return best

    def _recursive(self, f, stack: tuple) -> FuncInfo:
        c = self.c
        calls = f.self_calls
        params = [p.name for p in f.params]
        kinds = [[self._arg_kind(f, cl, i) for i in range(len(params))] for cl in calls]
        body_txt = normalize_expr(c.text(f.open_idx, f.close_idx))
        in_loop = [c.innermost_loop(cl.idx) is not None and c.loops[c.innermost_loop(cl.idx)].func == f.name
                   for cl in calls]
        work = self.body_cost(f, stack)
        size_params = []
        fi = FuncInfo(per_call=Cost.var("§N"), work=work)
        # --- DSU find
        if len(params) >= 1 and any(k[0] == "index" for k in kinds):
            p = params[0]
            arr = None
            for cl in calls:
                m = re.fullmatch(r"(\w+)\[" + re.escape(p) + r"\]", normalize_expr(c.text(*cl.args[0])))
                if m:
                    arr = m.group(1)
            compress = bool(arr and re.search(rf"{re.escape(arr)}\[{re.escape(p)}\]={re.escape(f.name)}\(", body_txt))
            union_size = self._dsu_union_by_rank(f, arr)
            V = self._domain(self.csize(arr)) if arr else Cost.var("n")
            mv = _main_var(V) or "n"
            if compress and union_size:
                fi.per_call = Cost([Term(1, {mv: Fac(a=1)})])
                fi.recursion = {"type": "dsu", "text": "find z kompresją ścieżki + łączenie według rangi/rozmiaru — "
                                                       "zamortyzowane O(α(n)) na operację (α ≤ 4 w praktyce)"}
                fi.depth = self._log(V)
            elif compress:
                fi.per_call = self._log(V)
                fi.recursion = {"type": "dsu", "text": "find z samą kompresją ścieżki — zamortyzowane O(log n) na "
                                                       "operację (z łączeniem według rangi byłoby O(α(n)))"}
                fi.depth = V
            elif union_size:
                fi.per_call = self._log(V)
                fi.recursion = {"type": "dsu", "text": "find bez kompresji, ale z łączeniem według rozmiaru — O(log n)"}
                fi.depth = self._log(V)
            else:
                fi.per_call = V
                fi.recursion = {"type": "dsu", "text": "find bez kompresji ścieżki i bez łączenia według rozmiaru — "
                                                       "w najgorszym razie O(n) na wywołanie!"}
                fi.depth = V
                self.warnings.append({"line": f.line - self.src_offset, "type": "warn", "message":
                                      "find() bez kompresji ścieżki: dodaj p[x] = find(p[x])."})
            return fi
        # --- DFS po grafie / drzewie
        adj = None
        for cl, il in zip(calls, in_loop):
            if not il:
                continue
            L = c.loops[c.innermost_loop(cl.idx)]
            g = self._adj_name(L)
            if g:
                adj = g
                break
        if adj:
            V = self.vertices_of(adj)
            E = self.edges_of(adj)
            guard = bool(re.search(r"if\(!\w+\[|if\(\w+\[\w+\]==(0|false|-1|\d+)\)|if\(\w+!=\w+\)|if\(\w+==\w+\)"
                                   r"continue|if\(\w+\[[^\]]+\]\)continue|if\(!\w+\[[^\]]+\]\)", body_txt))
            vis_arr = None
            m = re.search(r"(\w+)\[" + (re.escape(params[0]) if params else r"\w+") + r"\]=(true|1)", body_txt)
            if m:
                vis_arr = m.group(1)
            fi.per_call = Cost.const(1)
            fi.one_time = V + E
            fi.depth = V
            fi.recursion = {"type": "dfs", "text": f"DFS: każdy wierzchołek odwiedzany raz, każda krawędź "
                                                   f"przeglądana raz → O({V.expr()} + {E.expr()}) łącznie"}
            if not guard:
                fi.recursion["text"] += " (nie znaleziono tablicy odwiedzonych — zakładam drzewo lub warunek rodzica)"
            fi.reset_arrays = [vis_arr] if vis_arr else []
            return fi
        # --- odwiedzanie stanów raz (DFS po grafie niejawnym / siatce): vis[x] = 1 na wejściu, wywołania pod
        #     warunkiem !vis[...]
        if params:
            pv = re.escape(params[0])
            mv = re.search(rf"(\w+)\[{pv}\](\[\w+\])?=(true|1)", body_txt)
            if mv:
                vis = mv.group(1)
                ev = re.escape(vis)
                guarded = all(re.search(rf"(!{ev}\[|{ev}\[[^\]]+\](\[[^\]]+\])?(==(0|false)|!=1)|"
                                        rf"{ev}\[[^\]]+\](\[[^\]]+\])?(==(1|true))?#skip)", self._guard_text(f, cl))
                              or re.search(rf"if\({ev}\[{pv}\](\[\w+\])?(==(1|true))?\)return", body_txt)
                              for cl in calls)
                if guarded:
                    asg = next((x for x in c.assigns_in(f.open_idx, f.close_idx, vis) if x.indexed and
                                x.index_texts and normalize_expr(x.index_texts[0]) == params[0]), None)
                    idx_list = [normalize_expr(t) for t in asg.index_texts] if asg else [params[0]]
                    V = self._state_dims(f, vis, idx_list, params)
                    lo = {v for v in V.vars() if v.startswith("§") and v[1:] not in params}
                    if lo:
                        V = V.subst({v: self._param_fallback(v) for v in lo})
                    w = work.subst({"§" + p: V for p in params}) if not work.is_zero() else _one()
                    fi.per_call = Cost.const(1)
                    fi.one_time = V * w
                    fi.depth = V
                    fi.reset_arrays = [vis]
                    fi.recursion = {"type": "visit_once", "text": f"każdy stan ({vis}[…]) odwiedzany najwyżej raz → "
                                                                  f"liczba stanów × praca w wywołaniu = "
                                                                  f"{V.expr()} × {w.expr()}"}
                    return fi
        # --- memoizacja
        memo = re.search(r"if\((\w+)((\[[^\]]+\])+)(!=-1|!=0|>=0|!=INF|!=-INF|!=\w+)\)return|"
                         r"if\(\w+((\[[^\]]+\])+)\)return(\w+)", body_txt)
        map_memo = None if memo else (
            re.search(r"if\((\w+)\.(count|find)\([^)]*\)[^)]*\)return", body_txt) or
            re.search(r"=(\w+)\.find\([^;]*\);if\(\w+!=\1\.end\(\)\)return", body_txt))
        if memo or map_memo:
            if memo:
                arr = memo.group(1) or memo.group(7)
                idx_list = re.findall(r"\[([^\]]+)\]", memo.group(2) or memo.group(5) or "")
            else:
                # spamiętywanie w mapie: klucz = argumenty funkcji
                arr = map_memo.group(1)
                idx_list = list(params)
            states = _one()
            used = []
            for it in idx_list:
                m = re.fullmatch(r"\w+", it)
                if m and it in params:
                    used.append(it)
                    states = states * Cost.var("§" + it)
                else:
                    states = states * Cost.var("§N")
            if not idx_list:
                states = Cost.var("§N")
            # liczba stanów: dla każdego indeksu — warunek bazowy, pętla generująca argument, wymiar tablicy
            states = self._memo_states(f, arr, idx_list, params, calls)
            fi.per_call = Cost.const(1)
            transitions = work if not work.is_zero() else _one()
            fi.one_time = states * transitions
            fi.recursion = {"type": "memo", "text": f"rekurencja ze spamiętywaniem: liczba stanów × koszt przejścia "
                                                    f"= ({states.expr()}) × ({transitions.expr()})".replace("§", "")}
            fi.size_params = used
            fi.depth = Cost.var("§N")
            fi.reset_arrays = [arr] if arr else []
            # parametry przyrostowe (i+1 aż do n): rozmiar stanów z warunku bazowego
            self._memo_bounds(f, fi)
            return fi
        # --- dziel i zwyciężaj
        halving = [i for i in range(len(params)) if any(k[i] == "half" for k in kinds)]
        if halving or any("double" in k for k in kinds) and any("half" in k for k in kinds):
            size_params = [params[i] for i in halving]
            # parametry, które się zmieniają (l, r) i nie są indeksem węzła
            for i, p in enumerate(params):
                if any(k[i] in ("half", "inc", "dec") for k in kinds) and p not in size_params:
                    size_params.append(p)
            a_eff = self._exclusive_calls(f, calls)
            if any(in_loop):
                a_eff = max(a_eff, 2)
            sub = {"§" + p: Cost.var("§N") for p in params}
            fN = work.subst(sub)
            fN = fN.simplified()
            d_poly = 0.0
            d_log = 0.0
            dom = fN.dominant()
            if dom and "§N" in dom.f:
                d_poly = dom.f["§N"].p
                d_log = dom.f["§N"].l
            b_ = 3.0 if any(re.search(r"/3", normalize_expr(c.text(*cl.args[i]))) for cl in calls
                            for i in range(min(len(cl.args), len(params)))) else 2.0
            pruned = self._has_pruning(f, size_params)
            crit = math.log(a_eff, b_) if a_eff > 0 else 0
            if a_eff >= 2 and pruned and d_poly < 0.5:
                T = Cost.log("§N")
                text = (f"T(n) = {a_eff}·T(n/2) + O(1), ale gałęzie poza przedziałem zapytania są od razu ucinane — "
                        f"na każdym poziomie odwiedzamy O(1) węzłów → O(log n) na wywołanie (drzewo przedziałowe)")
                case = "pruned"
            elif a_eff <= 1:
                if d_poly > 0:
                    T = fN
                    text = f"T(n) = T(n/2) + O({fN.expr()}) → O({fN.expr()})".replace("§N", "n")
                else:
                    T = Cost.log("§N") * (Cost([Term(1, {"§N": Fac(0, d_log)})]) if d_log else _one())
                    text = "T(n) = T(n/2) + O(1) → O(log n) (każde wywołanie zmniejsza problem o połowę)"
                case = "1"
            else:
                if abs(d_poly - crit) < 1e-9:
                    T = Cost([Term(1, {"§N": Fac(d_poly, d_log + 1)})])
                    case = "2"
                    rule = f"a = b^d ({a_eff} = {b_:g}^{d_poly:g}) → O(n^d · log n)"
                elif d_poly > crit:
                    T = fN
                    case = "3"
                    rule = f"a < b^d → O(f(n))"
                else:
                    T = Cost([Term(1, {"§N": Fac(crit, 0)})])
                    case = "1"
                    rule = f"a > b^d → O(n^log_b(a)) = O(n^{crit:.3g})"
                text = (f"T(n) = {a_eff}·T(n/{b_:g}) + O({(fN.expr() if not fN.is_const() else '1')}) — "
                        f"twierdzenie o rekurencji uniwersalnej (przypadek {case}): {rule} → "
                        f"O({T.expr()})").replace("§N", "n")
            fi.per_call = T
            fi.size_params = size_params
            fi.depth = self._log(Cost.var("§N"))
            fi.recursion = {"type": "dc", "a": a_eff, "b": b_, "f": fN.expr().replace("§N", "n"),
                            "text": text.replace("§N", "n"), "case": case}
            return fi
        # --- Euklides
        if any("mod" in k for k in kinds):
            if len(params) >= 2:
                self._gcd_funcs[f.name] = (params[0], params[1])
                fi.per_call = Cost.log("§" + params[1])
                fi.depth = Cost.log("§" + params[1])
            else:
                fi.per_call = Cost.const(60)
                fi.depth = Cost.const(60)
            fi.recursion = {"type": "gcd", "text": "argumenty maleją jak w algorytmie Euklidesa (co dwa kroki co "
                                                   "najmniej o połowę) → O(log min(a, b)) wywołań"}
            return fi
        # --- zmniejszanie o stałą / backtracking
        dec_params = [params[i] for i in range(len(params)) if any(k[i] in ("dec", "inc") for k in kinds)]
        if dec_params:
            size_params = dec_params[:1]
            a_eff = self._exclusive_calls(f, calls)
            loop_calls = [cl for cl, il in zip(calls, in_loop) if il]
            uses_used = bool(re.search(r"(\w+)\[\w+\]=(true|1);" + re.escape(f.name) + r"\(", body_txt) and
                             re.search(r"\w+\[\w+\]=(false|0);", body_txt))
            per_sub = work.subst({"§" + p: Cost.var("§N") for p in params})
            fN = per_sub.simplified()
            if loop_calls:
                L = c.loops[c.innermost_loop(loop_calls[0].idx)]
                kcount = self.loop(c.innermost_loop(loop_calls[0].idx)).count.subst(
                    {"§" + p: Cost.var("§N") for p in params})
                if uses_used:
                    T = Cost([Term(1, {"§N": Fac(0, 0, 0, 0, 1)})]) * Cost.var("§N")
                    text = "backtracking po permutacjach (oznaczanie użytych) → O(n! · n)"
                    typ = "permutations"
                else:
                    if kcount.is_const():
                        base = max(kcount.const_value(), 2.0)
                        T = Cost([Term(1, {"§N": Fac(0, 0, 0, base)})])
                        text = f"w każdym wywołaniu {base:g} wyborów, głębokość n → O({base:g}^n)"
                    else:
                        T = Cost([Term(1, {"§N": Fac(1, 0, 0, 0, 1)})]) * _one()
                        T = Cost([Term(1, {"§N": Fac(0, 0, 0, 0, 1)})])
                        text = "pętla z wywołaniem rekurencyjnym na każdym poziomie → wykładniczo (rzędu n!)"
                    typ = "backtracking"
                fi.per_call = T * (fN if not fN.is_const() else _one())
                fi.recursion = {"type": typ, "text": text}
            elif a_eff <= 1:
                fi.per_call = Cost.var("§N") * (fN if not fN.is_zero() else _one())
                fi.recursion = {"type": "linear", "text": "T(n) = T(n−1) + O(" + (fN.expr().replace("§N", "n") if
                                not fN.is_const() else "1") + ") → głębokość n"}
            else:
                decs = [k for k in kinds]
                two_steps = any("dec" in k for k in kinds) and len({normalize_expr(c.text(*cl.args[
                    params.index(dec_params[0])])) for cl in calls}) >= 2
                base = 1.618 if two_steps and a_eff == 2 else float(a_eff)
                fi.per_call = Cost([Term(1, {"§N": Fac(0, 0, 0, base)})])
                if base == 1.618:
                    txt = "T(n) = T(n−1) + T(n−2) — jak liczby Fibonacciego → O(φ^n) ≈ O(1.618^n). Dodaj " \
                          "spamiętywanie (memo), żeby było O(n)!"
                else:
                    txt = f"T(n) = {a_eff}·T(n−1) + O(1) → O({a_eff}^n) — wykładniczo"
                fi.recursion = {"type": "exponential", "text": txt}
                self.warnings.append({"line": f.line - self.src_offset, "type": "warn", "message":
                                      f"Rekurencja {f.name}() bez spamiętywania rośnie wykładniczo."})
            fi.size_params = size_params
            fi.depth = Cost.var("§N")
            self._dec_bounds(f, fi, dec_params[0], kinds, calls, params)
            return fi
        # --- siatka (flood fill)
        if len(params) >= 2 and re.search(r"\w+\[\w+\]\[\w+\]", body_txt) and \
                any(re.search(r"[+-]1|d[xy]\[", normalize_expr(c.text(*cl.args[0]))) for cl in calls if cl.args):
            fi.per_call = Cost.const(1)
            fi.one_time = Cost.var("§N")
            fi.size_params = params[:2]
            fi.recursion = {"type": "grid", "text": "przeszukiwanie siatki (flood fill): każde pole odwiedzone raz"}
            fi.depth = Cost.var("§N")
            return fi
        fi.per_call = Cost.var("§N") * (work if not work.is_zero() else _one())
        fi.recursion = {"type": "unknown", "text": "nie rozpoznano schematu rekurencji — przyjęto O(n) wywołań"}
        fi.size_params = params[:1]
        fi.depth = Cost.var("§N")
        return fi

    def _dsu_union_by_rank(self, f, arr: str | None) -> bool:
        """Czy w kodzie, który woła find, jest łączenie według rangi/rozmiaru (albo losowe)?"""
        c = self.c
        txt = ""
        for g in c.functions:
            if g is f:
                continue
            if any(cl.name == f.name for cl in g.calls) or g.name == "main":
                txt += normalize_expr(c.text(g.open_idx, g.close_idx))
        if not txt:
            txt = normalize_expr(c.code)
        ea = re.escape(arr) if arr else r"\b_never_"
        for m in re.finditer(r"(\w+)\[[^\]]+\][<>]=?(\w+)\[[^\]]+\]", txt):
            if m.group(1) == m.group(2) and m.group(1) != arr:
                return True
        if re.search(r"(\w+)\[\w+\]\+=(\1)\[\w+\]", txt) and not re.search(rf"{ea}\[\w+\]\+=", txt):
            return True
        if re.search(r"(rozmiar|wielk|size|sz|rank|ranga|ile|cnt)\w*\[\w+\][<>]", txt) or \
                re.search(r"if\(\w*(sz|size|rank|wielk|rozm)\w*\[", txt):
            return True
        return bool(re.search(r"rand\(\)%2|rand\(\)&1|rng\(\)%2|rng\(\)&1", txt))

    def _domain(self, c: Cost) -> Cost:
        """Rozmiar „dziedziny” (np. tablicy p[MAXN] w DSU, drzewa Fenwicka): stała z kodu → wczytane n, jeśli jest."""
        vs = c.vars()
        if c.is_const() or all(self.syms.get(v, {}).get("kind") in ("literal", "const") for v in vs):
            for cand in ("n", "N"):
                if cand in self.read_targets:
                    return Cost.var(self.sym(cand, "input"))
        return c

    def _has_pruning(self, f, size_params) -> bool:
        c = self.c
        cnt = 0
        sp = set(size_params)
        others = {p.name for p in f.params} - sp
        i = f.open_idx
        while i < f.close_idx:
            if c.texts[i] == "if" and i + 1 < c.n and c.texts[i + 1] == "(" and (i + 1) in c.pairs:
                cc = c.pairs[i + 1]
                nxt = c.texts[cc + 1] if cc + 1 < c.n else ""
                nxt2 = c.texts[cc + 2] if cc + 2 < c.n else ""
                if nxt == "return" or (nxt == "{" and "return" in c.texts[cc + 1: c.pairs.get(cc + 1, cc + 1)]):
                    ids = set(c.ids_in(i + 2, cc - 1))
                    if ids & sp and (ids - sp - {f.name}) and any(
                            c.texts[j] in ("<", ">", "<=", ">=") for j in range(i + 2, cc)):
                        if ids & others or (ids - sp - {p.name for p in f.params}):
                            cnt += 1
                i = cc
            i += 1
        return cnt >= 1 and len(f.params) >= 3 or cnt >= 2

    def _state_dims(self, f, arr: str, idx_list: list, params: list) -> Cost:
        """Liczba stanów tablicy odwiedzin/memo: dla każdego indeksu granica z warunku (x >= n, i == n), potem wymiar
        tablicy (stały wymiar planszy → wczytane n / m), na końcu sam parametr (podstawiany przy wywołaniu)."""
        c = self.c
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        d = c.decl_of(arr) if arr else None
        states = _one()
        for pos, it in enumerate(idx_list):
            cst = None
            if it in params:
                ep = re.escape(it)
                cands = [m.group(2) for m in re.finditer(rf"(?<![\w.]){ep}(>=|>|==)(\w+)", body)] + \
                    [m.group(1) for m in re.finditer(rf"(?<![\w.])(\w+)(<=|<|==){ep}(?!\w)", body)]
                cands = [x for x in cands if not x.isdigit() and x not in params and x != f.name
                         and x not in ("true", "false")]
                if cands:
                    b = cands[0]
                    cst = Cost.var(b) if b in c.consts else self.ident(b, f.open_idx + 1).cost
            if cst is None and d is not None and d.kind == "array" and pos < len(d.dims):
                dm = self._text_mag(d.dims[pos], d.idx).cost
                if dm.is_const() and dm.const_value() >= 1000 and len(idx_list) == 2 and \
                        ("n", "m")[pos] in self.read_targets:
                    cst = Cost.var(self.sym(("n", "m")[pos], "input"))
                elif not dm.is_const() or it not in params:
                    cst = dm if len(idx_list) >= 2 else self._domain(dm)
            if cst is None and d is not None and d.kind == "vector" and d.ctor and pos == 0:
                cst = self._text_mag(d.ctor[0], d.idx).cost
            if cst is None and d is not None and d.kind != "array" and len(idx_list) == 1:
                cs = self.csize(arr)
                if not cs.is_const() and not any(v.startswith("§") or v.startswith("|") for v in cs.vars()):
                    cst = self._domain(cs)
            if cst is None and it in params:
                cst = Cost.var("§" + it)
            states = states * (cst if cst is not None else Cost.var("§N"))
        return states

    def _memo_states(self, f, arr, idx_list, params, calls) -> Cost:
        c = self.c
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        d = c.decl_of(arr) if arr else None
        states = _one()
        for pos, it in enumerate(idx_list):
            cst = None
            if it in params:
                pi = params.index(it)
                m = re.search(rf"if\({re.escape(it)}(==|>=|>)(\w+)\)", body) or \
                    re.search(rf"if\((\w+)(==|<=|<){re.escape(it)}\)", body)
                if m:
                    bnd = m.group(2) if m.group(1) == it or m.group(1) in ("==", ">=", ">") else m.group(1)
                    bnd = m.group(2) if not re.fullmatch(r"\w+", m.group(1)) or m.group(1) == it else m.group(1)
                    if not bnd.isdigit():
                        cst = Cost.var(bnd) if bnd in c.consts else Cost.var(self.sym(bnd, "input"))
                if cst is None:
                    # argument z pętli: f(i+1, t) dla t < k
                    for cl in calls:
                        if pi < len(cl.args):
                            ids = c.ids_in(*cl.args[pi])
                            for k in c.loops_containing(cl.idx):
                                L = c.loops[k]
                                if L.var in ids and L.func == f.name:
                                    cst = self.loop(k).count
                                    break
                        if cst is not None:
                            break
            if cst is None and it in params:
                # granica z dowolnego porównania w ciele: if (i >= n || j >= m) return …
                ep = re.escape(it)
                cands = [m_.group(2) for m_ in re.finditer(rf"(?<![\w.]){ep}(>=|>|==)(\w+)", body)] + \
                    [m_.group(1) for m_ in re.finditer(rf"(?<![\w.])(\w+)(<=|<){ep}(?!\w)", body)]
                cands = [x for x in cands if not x.isdigit() and x not in params and x != f.name
                         and x not in ("true", "false")]
                if cands:
                    b0 = cands[0]
                    cst = Cost.var(b0) if b0 in c.consts else self.ident(b0, f.open_idx + 1).cost
            # memo[100] z parametrem n: liczba stanów to zakres PARAMETRU (np. n), a nie stały rozmiar tablicy
            if cst is None and it in params and d is not None and d.kind == "array" and pos < len(d.dims) \
                    and re.fullmatch(r"\s*\d+\s*", str(d.dims[pos])):
                cst = Cost.var("§" + it)
            if cst is None and d is not None and d.kind == "vector" and not d.ctor and arr:
                ds = self._dim_size(arr, pos, f.open_idx)
                if not any(v.startswith("|") for v in ds.vars()):
                    cst = ds
            if cst is None and d is not None and d.kind == "array" and pos < len(d.dims):
                cst = self._text_mag(d.dims[pos], d.idx).cost
            if cst is None and d is not None and d.ctor and pos == 0:
                cst = self._text_mag(d.ctor[0], d.idx).cost
            if cst is None and it in params:
                cst = Cost.var("§" + it)
            states = states * (cst if cst is not None else Cost.var("§N"))
        return states

    def _memo_bounds(self, f, fi):
        """Dla parametrów rosnących (i+1 aż do n) — rozmiar przestrzeni stanów z warunku bazowego."""
        c = self.c
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        mapping = {}
        for p in fi.size_params:
            m = re.search(rf"if\({re.escape(p)}(==|>=|>)(\w+)\)", body)
            if m:
                bound = m.group(2)
                if not bound.isdigit():
                    mapping[p] = bound
        fi.recursion["bounds"] = mapping

    def _dec_bounds(self, f, fi, p, kinds, calls, params):
        c = self.c
        body = normalize_expr(c.text(f.open_idx, f.close_idx))
        pi = params.index(p)
        if any(k[pi] == "inc" for k in kinds):
            m = re.search(rf"if\({re.escape(p)}(==|>=|>)(\w+)\)", body)
            if m and not m.group(2).isdigit():
                fi.recursion["bounds"] = {p: m.group(2)}

    # ------------------------------------------------------------------ cały program
    def run(self) -> dict:
        c = self.c
        main = c.func_by_name.get("main")
        roots = [main] if main else c.functions
        for k in range(len(c.loops)):     # wzorce pętli (np. Dijkstra) wpływają na koszt operacji w innych liniach
            try:
                self.loop(k)
            except Exception:  # noqa: BLE001
                pass
        total = _one() * 0
        for f in roots:
            if f is None:
                continue
            total = total + self.body_cost(f, record=True)
        # funkcje nie-main: zapisz koszty linii (do hot-spotów) — względem jednego wywołania
        for f in c.functions:
            if main is not None and f is main:
                continue
            self.func(f.name)
            self.body_cost(f, record=True)
        for fname, ln, cst in self.one_time:
            total = total + cst
        # nierozwiązane symbole parametrów → n
        leftovers = {v for v in total.vars() if v.startswith("§")}
        if leftovers:
            total = total.subst({v: self._param_fallback(v) for v in leftovers})
        self.total = self._unify_logs(total)
        return {"total": self.total}

    def _unify_logs(self, total: Cost) -> Cost:
        """m log m + m log n → m log m: a·log b + a·log c ≤ 2a·log max(b, c), więc zostaje składnik z większym
        logarytmem (współczynniki się sumują, żeby liczba operacji się nie zmniejszyła)."""
        terms = list(Cost(total.terms).merged().terms)

        def split(t):
            poly = tuple(sorted((k, (round(v.p, 6), v.ll, v.e, v.fact, v.a)) for k, v in t.f.items()
                                if v.p or v.ll or v.e or v.fact or v.a))
            logs = {k: v.l for k, v in t.f.items() if v.l}
            return poly, logs
        changed = True
        while changed and len(terms) > 1:
            changed = False
            for i in range(len(terms)):
                for j in range(len(terms)):
                    if i == j:
                        continue
                    pi_, li = split(terms[i])
                    pj, lj = split(terms[j])
                    if pi_ != pj or len(li) != 1 or len(lj) != 1:
                        continue
                    (vi, di), = li.items()
                    (vj, dj), = lj.items()
                    if vi == vj or abs(di - dj) > 1e-9:
                        continue
                    # tylko rozmiary danych (n, m, |s|) — log wartości (log C, log max(a)) to inna informacja
                    if not all(self.syms.get(v, {}).get("kind") in ("input", "size") and v not in self.iter_reads
                               for v in (vi, vj)):
                        continue
                    hi_, hj = self._hint(vi), self._hint(vj)
                    poly_vars = {k for k, _ in pi_}
                    keep_i = hi_ > hj or (hi_ == hj and ((vi in poly_vars) > (vj in poly_vars) or
                                                         ((vi in poly_vars) == (vj in poly_vars) and i < j)))
                    ki, kd = (i, j) if keep_i else (j, i)
                    terms[ki] = Term(terms[ki].coef + terms[kd].coef, terms[ki].f)
                    del terms[kd]
                    changed = True
                    break
                if changed:
                    break
        return Cost(terms) if terms else total

    def _param_fallback(self, v: str) -> Cost:
        name = v[1:]
        # bounds z rekurencji (f(i+1) aż do i == n)
        for fi in self.func_info.values():
            if fi.recursion and fi.recursion.get("bounds", {}).get(name):
                b = fi.recursion["bounds"][name]
                return Cost.var(self.sym(b, "input")) if b not in self.c.consts else Cost.var(b)
        if "n" in self.syms or "n" in self.read_targets:
            return Cost.var(self.sym("n", "input"))
        inputs = [k for k, s in self.syms.items() if s["kind"] == "input"]
        return Cost.var(inputs[0]) if inputs else Cost.var(self.sym("n", "input"))

    def _eval(self, cost: Cost) -> float:
        vals = {k: (s["value"] or DEFAULT_SIZE) for k, s in self.syms.items()}
        return cost.value(vals)


# ============================================================================================== ograniczenia
_LE = r"(?:≤|<=|¬|⩽|\\leq?|\\leqslant|=<)"
_NUM = r"(?:10\s*\^\s*\{?\d+\}?|\d+(?:[.,]\d+)?\s*[·*x×]\s*10\s*\^\s*\{?\d+\}?|\d+(?:\.\d+)?e\d+|\d+(?:[ \u00a0\u2009]\d{3})*(?:[.,]\d+)?)"


def _to_number(s: str) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, dict):
        return _to_number(s.get("max"))
    if isinstance(s, (list, tuple)):
        return _to_number(s[-1]) if s else None
    t = str(s).strip().replace(" ", " ").replace(" ", " ").replace("\\cdot", "·")
    t = t.replace("{", "").replace("}", "")
    # 10^K z ogromnym K (np. „n ≤ 10^1000000” — liczba podana jako napis) nie mieści się we floacie → brak liczby
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*[·*x×]\s*10\s*\^\s*(\d+)", t)
    if m:
        return float(m.group(1).replace(",", ".")) * 10.0 ** int(m.group(2)) if int(m.group(2)) <= 300 else None
    m = re.fullmatch(r"10\s*\^\s*(\d+)", t)
    if m:
        return 10.0 ** int(m.group(1)) if int(m.group(1)) <= 300 else None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)[eE](\d+)", t)
    if m:
        return float(m.group(1)) * 10.0 ** int(m.group(2)) if int(m.group(2)) <= 300 else None
    t2 = re.sub(r"(?<=\d)[ '](?=\d{3}\b)", "", t)
    try:
        return float(t2.replace(",", "."))
    except ValueError:
        return None


def parse_constraints_text(text: str) -> dict:
    """Wyciąga ograniczenia z treści zadania (także z tekstu z PDF, gdzie 10^5 wygląda jak '10\\n5').
    Zwraca {"constraints": {zmienna: max}, "time_limit": s|None, "memory_limit": MB|None}."""
    if not text:
        return {"constraints": {}, "time_limit": None, "memory_limit": None}
    t = text.replace("\r\n", "\n")
    # potęgi z PDF: "10\n5" albo "10\n9\n)" → 10^5
    t = re.sub(r"\b10\s*\n\s*(\d{1,2})\b", r"10^\1", t)
    t = t.replace("¬", "≤").replace("⩽", "≤").replace("<=", "≤").replace("\\leqslant", "≤").replace("\\leq", "≤") \
        .replace("\\le", "≤").replace("\\cdot", "·").replace("$", "")
    t = re.sub(r"(\d)\s*\*\s*10\^", r"\1·10^", t)
    res: dict[str, float] = {}
    # osobne ograniczenia rozdzielone przecinkiem/średnikiem przed kolejną liczbą: "2 ≤ n ≤ 10^5, 1 ≤ m ≤ 2·10^5"
    t = re.sub(r"(≤\s*" + _NUM + r")\s*[,;]\s*(?=\d)", r"\1\n", t)
    # łańcuchy:  1 ≤ k ≤ n ≤ 10^6 ,  1 ≤ n, m ≤ 500 000,  n ≤ 10^5
    for m in re.finditer(r"((?:[^\n≤]{0,40}≤){1,4}[^\n≤]{0,40})", t):
        chain = m.group(1)
        parts = [p.strip() for p in chain.split("≤")]
        if len(parts) < 2:
            continue
        last = parts[-1]
        nm = re.match(rf"\s*({_NUM})", last)
        if not nm:
            continue
        val = _to_number(nm.group(1))
        if val is None:
            continue
        for p in parts[:-1]:
            # usuń liczbę z początku (dolne ograniczenie)
            p2 = re.sub(rf"^.*?({_NUM})\s*$", "", p) if re.fullmatch(rf".*?{_NUM}\s*", p) and not re.search(
                r"[A-Za-z]\w*\s*$", p) else p
            names = re.findall(r"(?<![\w])([A-Za-z](?:_?\{?[A-Za-z0-9]\}?)?|[A-Za-z]{1,3}_?i)(?=\s*(?:,|$|\)|\s))",
                               p2.strip()[-40:])
            # tylko końcowa lista zmiennych: "n, m", "a_i", "k"
            # nazwa zmiennej to osobne słowo — nie końcówka wyrazu („naturalnych” ≠ zmienna „nych”)
            tail = re.search(r"(?<![A-Za-z0-9_ąćęłńóśźżĄĆĘŁŃÓŚŹŻ])((?:[A-Za-z][A-Za-z0-9_{}]{0,3}\s*,\s*)*"
                             r"[A-Za-z][A-Za-z0-9_{}]{0,3})\s*$", p2.strip())
            if not tail:
                continue
            for name in re.split(r"\s*,\s*", tail.group(1)):
                name = name.replace("{", "").replace("}", "")
                if len(name) > 4 or name.lower() in ("i", "oraz", "dla", "gdzie", "od", "do"):
                    continue
                if re.fullmatch(r"[a-z]_?i", name):           # a_i, ai -> wartości tablicy a
                    key = f"max({name[0]})"
                    res[key] = max(res.get(key, 0), val)
                    res.setdefault(name[0].upper(), val)
                    continue
                res[name] = max(res.get(name, 0), val)
    tl = None
    m = re.search(r"(?:limit\s+czasu|czas(?:\s+wykonania)?|time\s+limit)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(s|sek|ms)",
                  t, re.I)
    if m:
        tl = float(m.group(1).replace(",", ".")) / (1000 if m.group(2).lower() == "ms" else 1)
    ml = None
    m = re.search(r"(?:pamię[ćc]|memory(?:\s+limit)?|limit\s+pamięci)[^\n\d]{0,20}(\d+)\s*(MB|MiB|GB|KB)", t, re.I)
    if m:
        ml = float(m.group(1)) * (1024 if m.group(2).upper() == "GB" else (1 / 1024 if m.group(2).upper() == "KB" else 1))
    return {"constraints": res, "time_limit": tl, "memory_limit": ml}


def _constraints_from(body: dict) -> tuple[dict, str, float | None, float | None]:
    cons = {}
    src = "default"
    tl = ml = None
    raw = body.get("constraints") or {}
    if isinstance(raw, str):
        raw = parse_constraints_text(raw)["constraints"]
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in ("time_limit", "memory_limit"):
                continue
            num = _to_number(v)
            if num is not None and str(k).strip():
                cons[str(k).strip()] = num
        if cons:
            src = "body"
    st = body.get("statement")
    if st and isinstance(st, str):
        parsed = None
        try:
            from oiforge.core import statement as _stmt  # moduł innego agenta (E2)
            parsed = _stmt.parse(st)
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed and isinstance(parsed, dict):
            for k, v in (parsed.get("constraints") or {}).items():
                num = _to_number(v)
                if num is not None and k not in cons:
                    cons[k] = num
            tl = _to_number(parsed.get("time_limit"))
            ml = _to_number(parsed.get("memory_limit"))
        fb = parse_constraints_text(st)
        for k, v in fb["constraints"].items():
            cons.setdefault(k, v)
        tl = tl or fb["time_limit"]
        ml = ml or fb["memory_limit"]
        if cons and src == "default":
            src = "statement"
    if isinstance(raw, dict):
        tl = _to_number(raw.get("time_limit")) or tl
        ml = _to_number(raw.get("memory_limit")) or ml
    return cons, src, tl, ml


def _settings() -> dict:
    try:
        return store.settings() if store else {}
    except Exception:  # noqa: BLE001
        return {}


# ============================================================================================== API: analyze
LIMIT_CLASSES = [
    ("log n", Cost.log("n")), ("√n", Cost.var("n", 0.5)), ("n", Cost.var("n")),
    ("n log n", Cost.var("n") * Cost.log("n")), ("n √n", Cost.var("n", 1.5)), ("n log² n", Cost.var("n", 1, 2)),
    ("n²", Cost.var("n", 2)), ("n² log n", Cost.var("n", 2, 1)), ("n³", Cost.var("n", 3)),
    ("2ⁿ · n", Cost([Term(1, {"n": Fac(1, 0, 0, 2)})])), ("2ⁿ", Cost([Term(1, {"n": Fac(0, 0, 0, 2)})])),
    ("n!", Cost([Term(1, {"n": Fac(0, 0, 0, 0, 1)})])),
]


def _max_n_for(cost: Cost, budget: float) -> float:
    lo, hi = 1.0, 1e18
    if cost.value({"n": hi}) <= budget:
        return hi
    for _ in range(200):
        mid = math.sqrt(lo * hi) if hi / lo > 4 else (lo + hi) / 2
        if cost.value({"n": mid}) <= budget:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1:
            break
    return lo


def limits_table(time_limit: float, dominant_class: str | None) -> list[dict]:
    budget = OPS_PER_SEC * time_limit
    rows = []
    for label, cst in LIMIT_CLASSES:
        mx = _max_n_for(cst, budget)
        rows.append({"class": f"O({label})", "max_n": mx, "max_n_fmt": "≥ 10^18" if mx >= 1e18 else fmt_num(round(mx)),
                     "yours": label == dominant_class})
    return rows


def _classify_single(cost: Cost) -> str | None:
    d = cost.dominant()
    if d is None or not d.f:
        return None
    if len(d.f) == 1:
        (v, f), = d.f.items()
        for label, c in LIMIT_CLASSES:
            (_, f2), = c.terms[0].f.items()
            if f.key() == f2.key():
                return label
    # wielu zmiennych: przyjmij n = m = q
    vals = Cost([Term(1, {"n": Fac(sum(x.p for x in d.f.values()), sum(x.l for x in d.f.values()),
                                   0, max([x.e for x in d.f.values()] + [0]), sum(x.fact for x in d.f.values()))})])
    f = vals.terms[0].f["n"]
    for label, c in LIMIT_CLASSES:
        (_, f2), = c.terms[0].f.items()
        if f.key() == f2.key():
            return label
    return None


def _is_size_key(k: str) -> bool:
    """Klucz ograniczenia opisujący ROZMIAR danych (n, m, q, |s|), a nie zakres wartości (a_i, max(a), A, x ≤ 10^18)."""
    if re.fullmatch(r"\|[^|]+\|", k):
        return True
    return bool(re.fullmatch(r"[a-z]{1,3}", k)) and not re.fullmatch(r"[a-z]_?i", k)


def _size_cons(cons: dict) -> dict:
    return {k: v for k, v in cons.items() if _is_size_key(k) and v <= 5e7 and k not in TEST_COUNT_NAMES}


def _value_cons(cons: dict) -> list:
    return [v for k, v in cons.items() if k.startswith("max(") or re.fullmatch(r"[A-Z]|[a-z]_?i|[VW]", k)]


def _symbol_values(an: Analyzer, cons: dict) -> tuple[dict, list[str]]:
    vals = {}
    assumed = []
    cons_l = {k.lower(): v for k, v in cons.items()}
    sizes = _size_cons(cons)
    biggest = max(sizes.values()) if sizes else None
    values = _value_cons(cons)
    poly_vars = set()
    for t in getattr(an, "total", Cost.zero()).terms:
        poly_vars |= {k for k, f in t.f.items() if f.p > 0 or f.e or f.fact}
    try:
        reading = an.reading_loop_symbols()
    except Exception:  # noqa: BLE001
        reading = set()
    for name, s in an.syms.items():
        if s["value"] is not None and s["kind"] in ("const", "literal"):
            vals[name] = s["value"]
            continue
        if name in cons:
            vals[name] = cons[name]
            if name in reading and cons[name] > 5e7:
                # pętla wczytuje dane w każdym obrocie — nie może obrócić się 10^9 razy; w treści to zakres wartości
                vals[name] = biggest or 1e7
                assumed.append(f"{name} ≤ {fmt_num(cons[name])} z treści to raczej zakres wartości — pętla wczytuje dane "
                               f"w każdym obrocie, więc przyjmuję {name} = {fmt_num(vals[name])}")
            continue
        if name.lower() in cons_l and len(name) <= 3:
            vals[name] = cons_l[name.lower()]
            continue
        if s["kind"] == "value":
            m = re.fullmatch(r"min\((\w+),\s*(\w+)\)", name)
            if m:
                got = [cons[x] for x in (m.group(1), m.group(2)) if x in cons]
                if got:
                    vals[name] = min(got)
                    continue
            cands = [name, f"{name}_i", f"{name}i", f"max({name})", name.upper()] if name != "V" else ["V", "A"]
            hit = next((cons[c] for c in cands if c in cons), None)
            if hit is None and name == "V" and values:
                hit = max(values)
            if hit is not None:
                vals[name] = hit
                continue
            if name in poly_vars:
                vals[name] = biggest or DEFAULT_SIZE
                assumed.append(f"{name} ≤ {fmt_num(vals[name])} (wartość z danych użyta jako liczba powtórzeń)")
            else:
                vals[name] = DEFAULT_VALUE
                assumed.append(f"{'wartości liczb' if name in ('V',) or name.startswith('min(') else name} ≤ "
                               f"{fmt_num(DEFAULT_VALUE)}")
            continue
        if name.startswith("|") and name.endswith("|"):
            inner = name[1:-1]
            for cand in (inner, f"|{inner}|", "n", "len", "l"):
                if cand in cons:
                    vals[name] = cons[cand]
                    break
            else:
                vals[name] = biggest or DEFAULT_SIZE
                assumed.append(f"{name} = {fmt_num(vals[name])}")
            continue
        if name.startswith("max("):
            inner = name[4:-1]
            for cand in (name, inner.upper(), f"{inner}_i", f"{inner}i", "A", "max(a)"):
                if cand in cons:
                    vals[name] = cons[cand]
                    break
            else:
                vals[name] = max(values) if values else DEFAULT_VALUE
                assumed.append(f"wartości {inner} ≤ {fmt_num(vals[name])}")
            continue
        if s["kind"] == "test":
            vals[name] = cons.get(name, 1.0)
            if name not in cons:
                assumed.append(f"{name} = 1 zestaw (sprawdź, czy suma rozmiarów jest ograniczona)")
            continue
        if name in ("?", "k") and s["kind"] in ("unknown", "input") and name not in cons:
            vals[name] = biggest or DEFAULT_SIZE
            assumed.append(f"nieznana liczba iteracji ≈ {fmt_num(vals[name])}")
            continue
        vals[name] = biggest or DEFAULT_SIZE
        if s["kind"] in ("input", "size", "unknown"):
            assumed.append(f"{name} = {fmt_num(vals[name])}")
    for k in ("V",):
        vals.setdefault(k, cons.get("n", biggest or DEFAULT_SIZE))
    return vals, assumed


def _product_caps(cons: dict) -> dict:
    """Ograniczenia iloczynu z treści, np. „n·k ≤ 10^7” → {frozenset({n, k}): 1e7}."""
    res = {}
    for k, v in cons.items():
        m = re.fullmatch(r"\s*([A-Za-z]\w*)\s*[·*×]\s*([A-Za-z]\w*)\s*", str(k))
        if m and m.group(1) != m.group(2):
            res[frozenset((m.group(1), m.group(2)))] = v
    return res


def _term_ops(t: Term, vals: dict, caps: dict) -> float:
    val = t.value(vals)
    if caps:
        pv = sorted(k for k, f in t.f.items() if f.p >= 1)
        for i, a_ in enumerate(pv):
            for b_ in pv[i + 1:]:
                cap = caps.get(frozenset((a_, b_)))
                if cap:
                    val *= min(1.0, cap / max(float(vals.get(a_, 1)) * float(vals.get(b_, 1)), 1.0))
    return val


def _ops_of(cost: Cost, vals: dict, caps: dict) -> float:
    return sum(_term_ops(t, vals, caps) for t in cost.terms)


def _memory(an: Analyzer, vals: dict) -> dict:
    c = an.c
    items = []
    total = 0.0
    for d in c.decls:
        if d.is_param or d.kind in ("scalar", "pointer", "pair", "tuple", "other"):
            continue
        f = c.func_by_name.get(d.scope) if d.scope != "global" else None
        if f is not None and f.name != "main" and not f.self_calls:
            in_loop = False
        elem_bytes = 0
        elems = None
        note = ""
        try:
            if d.kind == "array":
                elems = _one()
                for dim in d.dims:
                    elems = elems * an._text_mag(dim, d.idx).cost
                elem_bytes = c.sizeof(d.elem)
                inner_k, _, _ = c.classify_type(d.elem)
                if inner_k in ("vector", "set", "map", "deque", "multiset"):
                    # tablica wektorów (lista sąsiedztwa)
                    edges = an.edges_of(d.name)
                    inner_bytes = c.sizeof(c.classify_type(d.elem)[1] or "int")
                    elems_v = elems.value(vals)
                    eb = edges.value(vals) * inner_bytes * (1.5 if inner_k == "vector" else 40 / max(inner_bytes, 1))
                    bytes_ = elems_v * c.sizeof(d.elem) + eb
                    items.append({"name": d.name, "line": d.line - an.src_offset, "type": d.type + "[" +
                                  "][".join(d.dims) + "]", "elements": f"{elems.expr()} list + {edges.expr()} elementów",
                                  "bytes": bytes_, "mb": round(bytes_ / 2 ** 20, 2), "note": "lista sąsiedztwa"})
                    total += bytes_
                    continue
            elif d.kind in ("vector", "deque", "string", "stdarray"):
                elems = an.csize(d.name, d.idx)
                elem_bytes = c.sizeof(d.elem) if d.kind != "string" else 1
                if d.nested >= 2:
                    inner = c.classify_type(d.elem)[1]
                    # vector<vector<T>> v(n, vector<T>(m))
                    m2 = re.search(r"\(([^,()]+)", d.ctor[1]) if len(d.ctor) >= 2 else None
                    if m2:
                        inner_cnt = an._text_mag(m2.group(1), d.idx).cost
                        elems = elems * inner_cnt
                        elem_bytes = c.sizeof(inner)
                        note = "tablica 2D (vector<vector>)"
                    else:
                        edges = an.edges_of(d.name)
                        bytes_ = elems.value(vals) * 24 + edges.value(vals) * c.sizeof(inner) * 1.5
                        items.append({"name": d.name, "line": d.line - an.src_offset, "type": d.type,
                                      "elements": f"{elems.expr()} list + {edges.expr()} elementów",
                                      "bytes": bytes_, "mb": round(bytes_ / 2 ** 20, 2),
                                      "note": "kontener kontenerów (np. lista sąsiedztwa)"})
                        total += bytes_
                        continue
                if any(cl.receiver == d.name and cl.name in ("push_back", "emplace_back") for cl in c.calls):
                    note = (note + "; " if note else "") + "push_back: pojemność może być do 2× większa"
            elif d.kind in ("set", "multiset", "map", "multimap", "umap", "uset"):
                elems = an.csize(d.name, d.idx)
                inner = d.elem
                parts = [p.strip() for p in re.split(r",(?![^<]*>)", inner)]
                elem_bytes = sum(c.sizeof(p) for p in parts[:2]) + (32 if d.kind in ("set", "multiset", "map",
                                                                                      "multimap") else 24)
                note = "węzeł drzewa/hasza ma narzut ~32 B na element"
            elif d.kind in ("pq", "queue", "stack"):
                elems = an.csize(d.name, d.idx)
                elem_bytes = c.sizeof(d.elem)
            elif d.kind == "bitset":
                v = c._eval_const_text(d.elem) or 64
                elems = Cost.const(v / 8)
                elem_bytes = 1
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if elems is None:
            continue
        n_el = elems.value(vals)
        if any(k.startswith("§") for k in elems.vars()):
            continue
        b = n_el * elem_bytes
        if f is not None and f.self_calls and d.kind in ("vector", "string") and d.ctor:
            note = "lokalny w funkcji rekurencyjnej — osobna kopia na każdym poziomie"
        items.append({"name": d.name, "line": d.line - an.src_offset, "type": d.type + (
            "[" + "][".join(d.dims) + "]" if d.dims else ""), "elements": elems.expr(),
            "elem_bytes": elem_bytes, "bytes": b, "mb": round(b / 2 ** 20, 2), "note": note})
        total += b
    items.sort(key=lambda x: -x["bytes"])
    # stos rekurencji
    stack_note = None
    deepest = None
    for name, fi in an.func_info.items():
        if fi.depth is None or not an.c.func_by_name.get(name) or not an.c.func_by_name[name].self_calls:
            continue
        dep = fi.depth
        leftovers = {v for v in dep.vars() if v.startswith("§")}
        if leftovers:
            dep = dep.subst({v: an._param_fallback(v) for v in leftovers})
        dv = dep.value(vals)
        if deepest is None or dv > deepest[1]:
            deepest = (name, dv)
    if deepest:
        name, dv = deepest
        frame = 64 + 16 * len(an.c.func_by_name[name].params)
        sb = dv * frame
        stack_note = {"func": name, "depth": dv, "depth_fmt": fmt_num(round(dv)), "bytes": sb,
                      "mb": round(sb / 2 ** 20, 2)}
        total += sb
    return {"items": items[:30], "total_bytes": total, "total_mb": round(total / 2 ** 20, 2), "stack": stack_note}


def _cnt_str(c: Cost) -> str:
    """Liczba iteracji do wyświetlenia: stała jako liczba, w przeciwnym razie wyrażenie."""
    if c.is_const():
        v = c.const_value()
        return fmt_num(round(v)) if v >= 1 else "1"
    return c.expr()


def _loop_tree(an: Analyzer, vals: dict) -> list[dict]:
    c = an.c
    nodes = {}
    for k, L in enumerate(c.loops):
        info = an.loop(k)
        try:
            m = an.mult(k)
        except Exception:  # noqa: BLE001
            m = info.count
        cnt = info.count
        def fix(x):
            if x is None:
                return _one()
            lo = {v for v in x.vars() if v.startswith("§")}
            return x.subst({v: an._param_fallback(v) for v in lo}) if lo else x
        head = c.text(*L.head) if L.kind != "do" else "do … while " + c.text(*L.head)
        head = ("for " if L.kind in ("for", "range") else ("while " if L.kind == "while" else "")) + head
        nodes[k] = {"id": f"L{k}", "kind": L.kind, "line": L.line - an.src_offset,
                    "end_line": L.end_line - an.src_offset, "header": head[:160], "var": L.var,
                    "count": _cnt_str(fix(cnt)) if info.amort_over is None else
                    f"łącznie {_cnt_str(fix(info.amort_total))}",
                    "total": _cnt_str(fix(m)), "total_ops": fix(m).value(vals), "pattern": info.pattern,
                    "note": info.note.replace("§", ""), "amortized": info.amort_over is not None,
                    "confidence": info.confidence, "func": L.func, "children": []}
    roots_by_func: dict = {}
    for k, L in enumerate(c.loops):
        if L.parent is not None and L.parent in nodes:
            nodes[L.parent]["children"].append(nodes[k])
        else:
            roots_by_func.setdefault(L.func or "(globalnie)", []).append(nodes[k])
    out = []
    for f in c.functions:
        fi = an.func_info.get(f.name)
        node = {"id": f"F{f.name}", "kind": "function", "name": f.name, "line": f.line - an.src_offset,
                "end_line": f.end_line - an.src_offset, "header": f"{f.ret} {f.name}(" +
                ", ".join(f"{p.type}{' &' if p.is_ref else ''} {p.name}" for p in f.params) + ")",
                "children": roots_by_func.get(f.name, [])}
        if fi is not None and f.name != "main":
            node["per_call"] = fi.per_call.expr().replace("§N", "n").replace("§", "")
            if fi.one_time is not None:
                node["one_time"] = fi.one_time.expr().replace("§N", "n").replace("§", "")
            if fi.recursion:
                node["recursion"] = fi.recursion
        out.append(node)
    return out


def analyze(code: str, constraints: dict | None = None, statement: str | None = None,
            time_limit: float | None = None, memory_limit: float | None = None) -> dict:
    """Pełna analiza statyczna. Zwraca słownik gotowy do JSON."""
    body = {"constraints": constraints or {}, "statement": statement}
    cons, src, tl_s, ml_s = _constraints_from(body)
    s = _settings()
    tl = float(time_limit or tl_s or s.get("time_limit") or 1.0)
    ml = float(memory_limit or ml_s or s.get("memory_limit") or 256)
    an = Analyzer(code, values=cons)
    an.run()
    total = an.total
    vals, assumed = _symbol_values(an, cons)
    tv = total.vars()
    # założenia tylko dla symboli, które naprawdę są w wyniku (np. l, r z zapytań już nie)
    assumed = [a_ for a_ in assumed if any(re.match(rf"(wartości )?{re.escape(v)}\b", a_) or
                                           a_.startswith(f"{v} ") for v in tv) or not re.match(r"[\w|(]", a_)
               or a_.startswith(("nieznana", "wartości liczb"))]
    caps = _product_caps(cons)
    # wartości dla nazw zmiennych użytych w O(...) (np. V)
    ops = _ops_of(total, vals, caps)
    budget = OPS_PER_SEC * tl
    unknown = "?" in total.vars()
    # są ograniczenia, ale żadne nie mówi o rozmiarze danych (np. tylko zakres wartości) — nie zgaduj werdyktu
    sized = bool(_size_cons(cons)) or any(v in cons or v.lower() in {k.lower() for k in cons} for v in total.vars())
    if not cons or (not sized and not total.is_const()):
        verdict = "unknown"
    elif ops <= budget:
        verdict = "ok"
    elif ops <= budget * WARN_FACTOR:
        verdict = "warn"
    else:
        verdict = "tle"
    labels = {"ok": "✅ Zmieści się w limicie", "warn": "⚠️ Ryzykowne — zależy od stałej",
              "tle": "❌ Przekroczenie limitu czasu (TLE)", "unknown": "❓ Brak ograniczeń — podaj n, m, …"}
    simplified = total.simplified()
    terms = []
    for t in sorted(simplified.terms, key=lambda t: -_term_ops(t, vals, caps)):
        tc = Cost([t])
        tv = _term_ops(t, vals, caps)
        terms.append({"expr": tc.expr(), "ops": tv, "ops_fmt": fmt_ops(tv)})
    # hot-spoty
    line_ops = {}
    fn_calls = _function_call_totals(an, vals)
    for ln, cst in an.line_cost.items():
        fname = an.line_func.get(ln)
        lo = {v for v in cst.vars() if v.startswith("§")}
        cst2 = cst.subst({v: an._param_fallback(v) for v in lo}) if lo else cst
        v = _ops_of(cst2, vals, caps)
        if fname and fname != "main":
            v *= fn_calls.get(fname, 1.0)
        line_ops[ln] = (v, cst2)
    tot_line = sum(v for v, _ in line_ops.values()) or 1.0
    hot = []
    for ln, (v, cst2) in sorted(line_ops.items(), key=lambda kv: -kv[1][0])[:10]:
        if v < 1:
            continue
        hot.append({"line": ln - an.src_offset, "code": an.c.line_text(ln).strip()[:140], "expr": cst2.expr(),
                    "ops": v, "ops_fmt": fmt_ops(v), "share": round(min(1.0, v / tot_line), 4),
                    "func": an.line_func.get(ln)})
    mem = _memory(an, vals)
    mem_verdict = "ok" if mem["total_mb"] <= ml * 0.8 else ("warn" if mem["total_mb"] <= ml else "mle")
    dom_cls = _classify_single(total)
    marks = []
    for h in hot[:6]:
        if h["share"] >= 0.05:
            marks.append({"line": h["line"], "type": "warn" if h["share"] >= 0.3 else "info",
                          "message": f"≈ {h['ops_fmt']} operacji ({round(100 * h['share'])}%) · O({h['expr']})"})
    for k, L in enumerate(an.c.loops):
        info = an.loop(k)
        if info.pattern in ("two_pointers", "monotonic_stack", "harmonic", "sieve", "binary_search", "dependent",
                            "adjacency", "bfs", "pq_graph", "sqrt", "log"):
            marks.append({"line": L.line - an.src_offset, "type": "hint", "message": info.note.replace("§", "")})
    for w in an.warnings:
        marks.append({"line": w["line"], "type": w.get("type", "warn"), "message": w["message"]})
    seen = set()
    uniq_marks = []
    for mk in marks:
        key = (mk["line"], mk["message"])
        if key not in seen and mk["line"] > 0:
            seen.add(key)
            uniq_marks.append(mk)
    used = set(total.vars())
    for h_ in hot:
        used |= set(re.findall(r"[A-Za-z_|][\w|()]*", h_["expr"]))
    symbols = []
    for name, sdef in an.syms.items():
        if name.startswith("§") or name not in used:
            continue
        symbols.append({"name": name, "kind": sdef["kind"], "value": vals.get(name),
                        "value_fmt": fmt_num(vals.get(name)) if vals.get(name) is not None else "?",
                        "source": sdef["source"], "from_constraints": name in cons})
    explanation = _verdict_text(verdict, ops, budget, tl, total, cons)
    confidence = _confidence(an)
    warn_list = []
    seenw = set()
    for w in an.warnings:
        if (w["line"], w["message"]) not in seenw:
            seenw.add((w["line"], w["message"]))
            warn_list.append(w)
    return {
        "complexity": total.big_o(), "expr": total.expr(), "terms": terms, "dominant_class": dom_cls,
        "time": {"ops": ops, "ops_fmt": fmt_ops(ops), "limit_s": tl, "budget": budget, "budget_fmt": fmt_ops(budget),
                 "verdict": verdict, "label": labels[verdict], "explanation": explanation,
                 "ratio": round(ops / budget, 4) if budget else None},
        "constraints": cons, "constraints_source": src, "assumptions": assumed, "symbols": symbols,
        "loops": _loop_tree(an, vals), "hotspots": hot, "line_marks": uniq_marks,
        "memory": dict(mem, limit_mb=ml, verdict=mem_verdict), "limits_table": limits_table(tl, dom_cls),
        "warnings": warn_list, "calls": an.call_costs[:40], "confidence": confidence, "unknown_parts": unknown,
        **_derivation(an, vals, hot, total),
    }


PATTERN_PL = {
    "linear": "pętla liniowa", "dependent": "pętla zależna od zewnętrznej (≈ połowa)", "harmonic": "suma harmoniczna",
    "sieve": "sito", "sqrt": "pierwiastek", "log": "logarytm (mnożenie/dzielenie)", "const": "stała liczba obrotów",
    "binary_search": "wyszukiwanie binarne", "two_pointers": "dwa wskaźniki (amortyzacja)",
    "monotonic_stack": "stos monotoniczny (amortyzacja)", "adjacency": "sąsiedzi wierzchołka (łącznie krawędzie)",
    "bfs": "przegląd kolejką (każdy stan raz)", "pq_graph": "Dijkstra / kopiec", "queue": "kolejka",
    "countdown": "licznik w dół", "until_input": "do końca wejścia", "until_stable": "do stabilizacji",
    "permutations": "wszystkie permutacje", "walk": "przejście po wskaźnikach", "dsu_walk": "find w DSU",
    "query_range": "przedział z zapytania", "amortized_walk": "cofanie po tablicy (amortyzacja)",
    "tests": "zestawy testowe", "unknown": "nierozpoznana",
}


def _first_tok_of_line(an: Analyzer, ln: int) -> int | None:
    c = an.c
    for i, t in enumerate(c.toks):
        if t.line == ln and c.texts[i] not in ("{", "}"):
            return i
    return None


_NOTE_PATTERNS = {"query_range", "two_pointers", "harmonic", "sieve", "bfs", "adjacency", "amortized_walk",
                  "binary_search", "log", "dependent", "pq_graph", "dsu_walk", "tests", "monotonic_stack", "sqrt",
                  "until_stable", "walk", "unknown", "permutations"}


def _loop_steps(an: Analyzer, idx: int, vals: dict, line: int | None = None) -> list:
    c = an.c
    steps = []
    ks = list(c.loops_containing(idx))
    if line is not None:   # pętla zaczynająca się w tej samej linii (for (…) s += a[i];)
        ks += [k for k, L in enumerate(c.loops) if L.line == line and k not in ks and L.idx >= idx]
        ks = sorted(set(ks), key=lambda z: c.loops[z].depth)
    for k in ks:
        L = c.loops[k]
        info = an.loop(k)
        lo = {v for v in info.count.vars() if v.startswith("§")}
        cnt = info.count.subst({v: an._param_fallback(v) for v in lo}) if lo else info.count
        if info.amort_over is not None and info.amort_total is not None:
            at = info.amort_total
            lo = {v for v in at.vars() if v.startswith("§")}
            at = at.subst({v: an._param_fallback(v) for v in lo}) if lo else at
            count = f"łącznie {_cnt_str(at)}"
        else:
            count = _cnt_str(cnt)
        head = c.text(*L.head) if L.kind != "do" else "do … while " + c.text(*L.head)
        head = ("for " if L.kind in ("for", "range") else ("while " if L.kind == "while" else "")) + head
        steps.append({"line": L.line - an.src_offset, "code": head[:120], "count": count, "pattern": info.pattern,
                      "pattern_pl": PATTERN_PL.get(info.pattern, info.pattern), "note": info.note.replace("§", "")[:260],
                      "amortized": info.amort_over is not None})
    return steps


def _derivation(an: Analyzer, vals: dict, hot: list, total: Cost) -> dict:
    """„Skąd ta złożoność?” — najcięższe linie z łańcuchem pętli (i wywołań funkcji), które je mnożą."""
    c = an.c
    items = []
    cum = 0.0
    for h in hot:
        if len(items) >= 4 or (items and (h["share"] < 0.03 or cum >= 0.97)):
            break
        cum += h["share"]
        ln = h["line"] + an.src_offset
        idx = _first_tok_of_line(an, ln)
        if idx is None:
            continue
        steps = _loop_steps(an, idx, vals, ln)
        via = []
        fname = h.get("func")
        f = c.func_by_name.get(fname) if fname else None
        if f is not None and f.name != "main":
            fi = an.func_info.get(f.name)
            rec = (fi.recursion or {}).get("text") if fi and fi.recursion else None
            sites = [cl for cl in c.calls if cl.name == f.name and not cl.is_method and
                     (c.enclosing_func(cl.idx) is None or c.enclosing_func(cl.idx).name != f.name)]
            seen_lines = set()
            for cl in sites:
                if cl.line in seen_lines or len(seen_lines) >= 2:
                    continue
                seen_lines.add(cl.line)
                via.append({"line": cl.line - an.src_offset, "code": c.line_text(cl.line).strip()[:120],
                            "func": cl.func, "loops": _loop_steps(an, cl.idx, vals)})
            if rec:
                via.append({"line": f.line - an.src_offset, "code": f"{f.name}(…)", "func": f.name, "loops": [],
                            "recursion": rec.replace("§", "")})
        items.append({"line": h["line"], "code": h["code"], "expr": h["expr"].replace("§", ""), "ops_fmt": h["ops_fmt"],
                      "share": h["share"], "func": fname, "loops": steps, "via": via})
    md = []
    explained = set()
    for it in items:
        per = " na jedno wywołanie funkcji" if it["func"] and it["func"] != "main" else ""
        md.append(f"- **linia {it['line']}** `{it['code'][:70]}` — wykonuje się ≈ O({it['expr']}) razy{per} "
                  f"({round(100 * it['share'])}% pracy)")
        for v in it["via"]:
            if v.get("recursion"):
                md.append(f"  - funkcja `{v['code']}` (linia {v['line']}): {v['recursion']}")
                continue
            chain = " × ".join(f"{s['count']} (linia {s['line']})" for s in v["loops"]) or "poza pętlą"
            md.append(f"  - funkcja {it['func']}() wołana w linii {v['line']}: {chain}")
        for s_ in it["loops"]:
            if s_["line"] in explained:
                md.append(f"  - linia {s_['line']}: {s_['count']} obrotów (jak wyżej)")
                continue
            explained.add(s_["line"])
            md.append(f"  - linia {s_['line']}: `{s_['code'][:60]}` → {s_['count']} obrotów — {s_['pattern_pl']}"
                      + (f"; {s_['note']}" if s_["note"] and s_["pattern"] in _NOTE_PATTERNS else ""))
    simplified = total.simplified()
    if items:
        md.append(f"\nRazem: suma tych kosztów, z której liczy się składnik dominujący → **{simplified.big_o()}**.")
    return {"derivation": items, "derivation_md": "\n".join(md)}


def _function_call_totals(an: Analyzer, vals: dict) -> dict:
    """Ile razy (łącznie) wywoływana jest każda funkcja — do rozkładu hot-spotów."""
    c = an.c
    res = {}
    for f in c.functions:
        if f.name == "main":
            continue
        tot = 0.0
        for cl in c.calls:
            if cl.name != f.name or cl.is_method:
                continue
            caller = c.enclosing_func(cl.idx)
            if caller is not None and caller.name == f.name:
                continue
            m = an.mult_at(cl.idx)
            lo = {v for v in m.vars() if v.startswith("§")}
            if lo:
                m = m.subst({v: an._param_fallback(v) for v in lo})
            tot += m.value(vals)
        fi = an.func_info.get(f.name)
        if fi is not None and f.self_calls:
            # liczba wywołań rekurencyjnych ≈ łączny koszt / praca na wywołanie
            per = fi.one_time if fi.one_time is not None else fi.per_call
            lo = {v for v in per.vars() if v.startswith("§")}
            per2 = per.subst({v: an._param_fallback(v) for v in lo}) if lo else per
            w = fi.work or _one()
            lo = {v for v in w.vars() if v.startswith("§")}
            w2 = w.subst({v: an._param_fallback(v) for v in lo}) if lo else w
            inv = per2.value(vals) / max(w2.value(vals), 1.0)
            tot = max(tot, 1.0) * max(inv, 1.0) if fi.one_time is None else max(inv, 1.0)
        res[f.name] = max(tot, 1.0)
    return res


def _confidence(an: Analyzer) -> float:
    if not an.loop_info:
        return 0.9
    vals = [i.confidence for i in an.loop_info.values()]
    return round(min(0.95, sum(vals) / len(vals)), 2)


def _verdict_text(verdict: str, ops: float, budget: float, tl: float, total: Cost, cons: dict) -> str:
    o = fmt_ops(ops)
    if verdict == "unknown":
        return (f"Złożoność: {total.big_o()}. Nie znam ograniczeń, więc przyjąłem 10^5 dla każdej zmiennej "
                f"(≈ {o} operacji). Wpisz ograniczenia z treści (np. n ≤ 200 000), żeby dostać werdykt.")
    if verdict == "ok":
        return (f"Szacunkowo {o} prostych operacji przy budżecie ≈ {fmt_ops(budget)} na {tl:g} s "
                f"(≈ 2·10^8/s). Spory zapas — ta złożoność przejdzie.")
    if verdict == "warn":
        return (f"Szacunkowo {o} operacji — to {ops / budget:.1f}× budżetu na {tl:g} s. Przejdzie tylko przy małej "
                f"stałej (proste operacje, szybkie wejście, brak set/map w pętli). Warto szukać lepszego algorytmu "
                f"albo optymalizacji stałej.")
    return (f"Szacunkowo {o} operacji — ok. {ops / budget:.0f}× więcej niż mieści się w {tl:g} s. "
            f"Potrzebny asymptotycznie szybszy algorytm.")


def handle_analyze(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod do analizy złożoności.")
    if len(code) > 400_000:
        raise ApiError("Kod jest za długi do analizy (limit 400 kB).")
    lang = None
    try:
        lang = runner.detect_lang(code) if runner else "cpp"
    except Exception:  # noqa: BLE001
        lang = "cpp"
    res = analyze(code, body.get("constraints") or {}, body.get("statement"),
                  _to_number(body.get("time_limit")), _to_number(body.get("memory_limit")))
    if lang == "python":
        res["warnings"].insert(0, {"line": 0, "type": "info", "message":
                                   "Analiza statyczna jest dostosowana do C++ — dla Pythona wyniki są orientacyjne "
                                   "(użyj pomiaru empirycznego). Pamiętaj: Python jest ~30–50× wolniejszy."})
        res["lang"] = "python"
    else:
        res["lang"] = "cpp"
    try:
        from oiforge.core import algodetect
        res["algorithms"] = algodetect.detect(code)[:6]
    except Exception:  # noqa: BLE001
        res["algorithms"] = []
    return res


def handle_parse_constraints(body: dict) -> dict:
    text = body.get("text") or body.get("statement") or ""
    cons, src, tl, ml = _constraints_from({"statement": text})
    return {"constraints": cons, "time_limit": tl, "memory_limit": ml}


# ============================================================================================== pomiar empiryczny
MODELS = [
    ("1", lambda n: 1.0), ("log n", lambda n: math.log2(max(n, 2))), ("√n", lambda n: math.sqrt(n)),
    ("n", lambda n: n), ("n log n", lambda n: n * math.log2(max(n, 2))), ("n√n", lambda n: n ** 1.5),
    ("n log² n", lambda n: n * math.log2(max(n, 2)) ** 2), ("n²", lambda n: n * n),
    ("n² log n", lambda n: n * n * math.log2(max(n, 2))), ("n³", lambda n: n ** 3),
    ("2ⁿ", lambda n: 2.0 ** min(n, 1000)),
]
_MODEL_RANK = {m[0]: i for i, m in enumerate(MODELS)}


def fit_models(points: list) -> dict:
    """points: [[n, t], ...] (t w sekundach). Dopasowuje t ≈ a + b·f(n) dla każdego modelu i nachylenie log-log."""
    pts = [(float(n), float(t)) for n, t in points if n and n > 0 and t is not None and t >= 0]
    if len(pts) < 3:
        return {"best": None, "models": [], "slope": None}
    # nachylenie log-log po odjęciu stałego kosztu startu procesu (≈ najmniejszy zmierzony czas)
    base = min(t for _, t in pts) * 0.9
    xs = [math.log(n) for n, _ in pts]
    ys = [math.log(max(t - base, 1e-6)) for _, t in pts]
    big = [(x, y) for (x, y), (_, t) in zip(zip(xs, ys), pts) if t - base > 0.003]
    slope = None
    if len(big) >= 2:
        mx = sum(x for x, _ in big) / len(big)
        my = sum(y for _, y in big) / len(big)
        sxx = sum((x - mx) ** 2 for x, _ in big)
        if sxx > 0:
            slope = sum((x - mx) * (y - my) for x, y in big) / sxx
    res = []
    tmean = sum(t for _, t in pts) / len(pts)
    sst = sum((t - tmean) ** 2 for _, t in pts) or 1e-18
    for name, fn in MODELS:
        if name == "1":
            continue
        try:
            fx = [fn(n) for n, _ in pts]
        except OverflowError:
            continue
        if any(not math.isfinite(v) for v in fx):
            continue
        # regresja ważona względnie (czasy rosną o rzędy wielkości): minimalizuj Σ ((a + b f - t)/t)²
        w = [1.0 / max(t, 1e-4) ** 2 for _, t in pts]
        sw = sum(w)
        sx = sum(wi * f for wi, f in zip(w, fx))
        sy = sum(wi * t for wi, (_, t) in zip(w, pts))
        sxx = sum(wi * f * f for wi, f in zip(w, fx))
        sxy = sum(wi * f * t for wi, f, (_, t) in zip(w, fx, pts))
        den = sw * sxx - sx * sx
        if abs(den) < 1e-30:
            continue
        b = (sw * sxy - sx * sy) / den
        a = (sy - b * sx) / sw
        if b <= 0:
            a, b = 0.0, (sxy / sxx if sxx else 0.0)
            if b <= 0:
                continue
        a = max(a, 0.0)
        pred = [a + b * f for f in fx]
        sse = sum((p - t) ** 2 for p, (_, t) in zip(pred, pts))
        rel = sum(((p - t) / max(t, 1e-4)) ** 2 for p, (_, t) in zip(pred, pts)) / len(pts)
        r2 = 1 - sse / sst
        res.append({"model": name, "a": a, "b": b, "r2": round(r2, 5), "rel_err": round(math.sqrt(rel), 4),
                    "curve": [[n, a + b * f] for (n, _), f in zip(pts, fx)]})
    if not res:
        return {"best": None, "models": [], "slope": slope}
    res.sort(key=lambda m: (m["rel_err"], -m["r2"]))
    best = res[0]
    # preferuj prostszy model, jeśli prawie tak samo dobry
    for m in res[1:4]:
        if _MODEL_RANK[m["model"]] < _MODEL_RANK[best["model"]] and m["rel_err"] <= best["rel_err"] * 1.06 + 0.004:
            best = m
    # nachylenie log-log jako druga opinia
    if slope is not None and best["rel_err"] > 0.08:
        expected = {"n": 1.0, "n log n": 1.1, "n²": 2.0, "n³": 3.0, "√n": 0.5, "log n": 0.1, "n√n": 1.5,
                    "n log² n": 1.2, "n² log n": 2.1}
        exp_b = expected.get(best["model"])
        if exp_b is not None and abs(exp_b - slope) > 0.6:
            alt = min(res, key=lambda m: abs(expected.get(m["model"], 99) - slope) + m["rel_err"])
            best = alt
    return {"best": best["model"], "best_fit": best, "models": res[:6], "slope": round(slope, 3) if slope else None}


def render_template(template: str, size: int, rng: random.Random) -> str:
    """Mini-szablon wejścia. Zmienne n/m/q/k = rozmiar. Konstrukcje:
    {n} {n/2} {2*n}  · {rand:1:1e9} · {arr:n:1:1e9} · {perm:n} · {str:n:az} · {pairs:m:1:n} · {tree:n} ·
    {graph:n:m} · {lines:K:SZABLON}. Skróty: `a_1..a_n` / `a1 ... an` → n losowych liczb."""
    t = template.replace("\r\n", "\n")
    t = re.sub(r"\b([a-z])_?1\s*(?:\.\.\.?|…)\s*\1_?([a-z]\w*)", r"{arr:\2:1:1000000000}", t)
    t = re.sub(r"\b([a-z])_?1\s*,?\s*\1_?2\s*,?\s*(?:\.\.\.?|…)\s*,?\s*\1_?([a-z]\w*)", r"{arr:\2:1:1000000000}", t)

    def num(expr: str) -> int:
        e = expr.strip().replace("^", "**")
        e = re.sub(r"\b[nmqkNMQK]\b", str(size), e)
        e = re.sub(r"(\d+(?:\.\d+)?)e(\d+)", lambda mm: str(int(float(mm.group(1)) * 10 ** int(mm.group(2)))), e)
        if not re.fullmatch(r"[0-9+\-*/(). ]+", e):
            raise ValueError(f"Niepoprawne wyrażenie w szablonie: {expr}")
        return int(eval(e, {"__builtins__": {}}, {}))  # noqa: S307 — tylko cyfry i operatory

    def rep(m) -> str:
        parts = m.group(1).split(":")
        kind = parts[0].strip()
        if len(parts) == 1:
            return str(num(kind))
        if kind == "rand":
            return str(rng.randint(num(parts[1]), num(parts[2])))
        if kind == "arr":
            cnt = num(parts[1])
            lo = num(parts[2]) if len(parts) > 2 else 1
            hi = num(parts[3]) if len(parts) > 3 else 10 ** 9
            return " ".join(str(rng.randint(lo, hi)) for _ in range(cnt))
        if kind == "perm":
            cnt = num(parts[1])
            p = list(range(1, cnt + 1))
            rng.shuffle(p)
            return " ".join(map(str, p))
        if kind == "str":
            cnt = num(parts[1])
            alpha = parts[2] if len(parts) > 2 else "az"
            letters = "abcdefghijklmnopqrstuvwxyz" if alpha == "az" else (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if alpha == "AZ" else ("01" if alpha == "01" else alpha))
            return "".join(rng.choice(letters) for _ in range(cnt))
        if kind == "pairs":
            cnt = num(parts[1])
            lo = num(parts[2]) if len(parts) > 2 else 1
            hi = num(parts[3]) if len(parts) > 3 else size
            return "\n".join(f"{rng.randint(lo, hi)} {rng.randint(lo, hi)}" for _ in range(cnt))
        if kind == "tree":
            cnt = num(parts[1])
            return "\n".join(f"{rng.randint(1, i - 1)} {i}" for i in range(2, cnt + 1))
        if kind == "graph":
            nv = num(parts[1])
            ne = num(parts[2]) if len(parts) > 2 else nv
            w = num(parts[3]) if len(parts) > 3 else 0
            lines = []
            for _ in range(ne):
                a, b = rng.randint(1, nv), rng.randint(1, nv)
                lines.append(f"{a} {b}" + (f" {rng.randint(1, w)}" if w else ""))
            return "\n".join(lines)
        if kind == "lines":
            cnt = num(parts[1])
            sub = ":".join(parts[2:])
            return "\n".join(render_template(sub.replace("[", "{").replace("]", "}"), size, rng).rstrip("\n")
                             for _ in range(cnt))
        raise ValueError(f"Nieznana konstrukcja szablonu: {{{m.group(1)}}}")

    out = re.sub(r"\{([^{}]+)\}", rep, t)
    out = re.sub(r"(?<![\w{])([nmqk])(?![\w}])", lambda mm: str(size), out) if re.fullmatch(r"[\s\w{}:.,/*+\-]*", t) \
        and re.search(r"(?m)^\s*[nmqk](\s+[nmqk])*\s*$", t) else out
    if not out.endswith("\n"):
        out += "\n"
    return out


def infer_template(code: str) -> str | None:
    """Zgaduje format wejścia z kodu (kolejność cin/scanf, pętle) → szablon dla render_template."""
    an = Analyzer(code)
    c = an.c
    reads = [io for io in c.io if io.kind == "read"]
    if not reads:
        return None
    main = c.func_by_name.get("main")
    lines = []
    # zmienne porównywane z literałami (typ zapytania)
    literal_choices = {}
    for m in re.finditer(r"(\w+)\s*==\s*(\"[^\"]*\"|'[^']*')", c.code):
        literal_choices.setdefault(m.group(1), []).append(m.group(2)[1:-1])
    used_as_bound = set()
    for L in c.loops:
        for part in (L.cond,):
            if part:
                used_as_bound |= set(c.ids_in(*part))
    loops_done = set()
    for io in reads:
        k = c.innermost_loop(io.idx)
        vals = []
        for t in io.targets:
            name = re.match(r"\s*([A-Za-z_]\w*)", t)
            nm = name.group(1) if name else t
            d = c.decl_of(nm, io.idx)
            if nm in literal_choices:
                vals.append("{choice:" + "|".join(sorted(set(literal_choices[nm]))) + "}")
            elif d is not None and d.kind == "string":
                vals.append("{str:n:az}")
            elif d is not None and d.kind == "scalar" and "char" in d.type:
                vals.append("{rand:0:0}" if False else "a")
            elif k is None and (nm in used_as_bound or nm in ("n", "m", "q", "k", "t")):
                vals.append("{n}" if nm not in TEST_COUNT_NAMES else "1")
            elif k is None:
                vals.append("{rand:1:n}")
            else:
                vals.append("{rand:1:n-1}")
        line = " ".join(vals)
        if k is None:
            lines.append(line)
            continue
        # odczyt w pętli — pętla na najwyższym poziomie w main
        top = k
        while c.loops[top].parent is not None:
            top = c.loops[top].parent
        if (top, io.line) in loops_done:
            continue
        loops_done.add((top, io.line))
        L = c.loops[k]
        cnt = "n"
        cmp_ = an._cond_cmp(L.cond, L.var) if L.cond and L.var else None
        if cmp_:
            btxt = normalize_expr(c.text(*cmp_[2]))
            if re.fullmatch(r"\w+([+-]\d+)?", btxt) and not btxt.isdigit():
                cnt = "n-1" if re.search(r"-1$", btxt) else "n"
        elif L.cond and re.match(r"\w+--", normalize_expr(c.text(*L.cond))):
            cnt = "n"
        if len(io.targets) == 1 and line.startswith("{rand"):
            lines.append("{arr:" + cnt + ":1:" + ("n-1" if "n-1" in line else "n") + "}")
        else:
            lines.append("{lines:" + cnt + ":" + line.replace("{", "[").replace("}", "]") + "}")
    return "\n".join(lines)


def _render_choice(text: str, rng: random.Random) -> str:
    return re.sub(r"\{choice:([^}]*)\}", lambda m: rng.choice(m.group(1).split("|") or [""]), text)


def _gen_input(template: str, size: int, rng: random.Random) -> str:
    t = template
    # choice wewnątrz lines używa nawiasów kwadratowych
    t = t.replace("[choice:", "{choice:")
    out = render_template(t.replace("{choice:", "\x00choice:"), size, rng)
    out = re.sub(r"\x00choice:([^}\]]*)[}\]]", lambda m: rng.choice(m.group(1).split("|") or [""]), out)
    return out


def _measure_work(job, body: dict):
    if runner is None:
        raise RuntimeError("Brak modułu runner.")
    code = body.get("code") or ""
    tl = float(_to_number(body.get("time_limit")) or _settings().get("time_limit") or 1.0)
    run_cap = max(0.5, min(tl * 2.5, 10.0))
    rng = random.Random(int(body.get("seed") or 12345))
    job.progress(0.02, "Kompilacja programu…")
    prog = runner.prepare(code, lang=body.get("lang"), mode="release")
    if not prog.ok:
        return {"ok": False, "error": "Błąd kompilacji programu.", "compile": prog.compile.to_dict() if prog.compile else None}
    gen = None
    gen_src = (body.get("generator") or "").strip()
    template = (body.get("input_template") or "").strip()
    source = "template"
    if gen_src:
        job.progress(0.05, "Kompilacja generatora…")
        gen = runner.prepare(gen_src, lang=body.get("generator_lang"), mode="release")
        if not gen.ok:
            return {"ok": False, "error": "Błąd kompilacji generatora.",
                    "compile": gen.compile.to_dict() if gen.compile else None}
        source = "generator"
    elif not template:
        template = infer_template(code) or "{n}\n{arr:n:1:1000000000}"
        source = "inferred"
    sizes = body.get("sizes")
    if isinstance(sizes, str):
        sizes = [int(_to_number(x) or 0) for x in re.split(r"[,\s;]+", sizes) if x.strip()]
    auto = not sizes
    if auto:
        start = int(_to_number(body.get("start")) or 1000)
        sizes = [start * 2 ** i for i in range(22)]
    max_size = int(_to_number(body.get("max_size")) or 5 * 10 ** 7)
    points = []
    events = []
    stopped = None
    for i, n in enumerate(sizes):
        if job.cancelled:
            stopped = "anulowano"
            break
        if n > max_size:
            stopped = f"osiągnięto maksymalny rozmiar {fmt_num(max_size)}"
            break
        job.progress(min(0.95, 0.08 + 0.85 * i / max(len(sizes), 1)), f"Rozmiar n = {fmt_num(n)}…")
        if gen is not None:
            g = runner.run(gen.cmd, "", timeout=30.0, args=[str(n), str(rng.randint(1, 10 ** 9))])
            if g.verdict != "OK":
                stopped = f"generator zakończył się błędem dla n = {n} ({g.verdict})"
                break
            inp = g.stdout
        else:
            try:
                inp = _gen_input(template, n, rng)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"Błąd szablonu wejścia: {e}"}
        if len(inp) > 150_000_000:
            stopped = "wejście za duże (>150 MB)"
            break
        times = []
        rr = None
        reps = 2 if (points and points[-1][1] < 0.2) else 1
        for _ in range(reps):
            rr = runner.run(prog.cmd, inp, timeout=run_cap)
            if rr.verdict != "OK":
                break
            times.append(rr.time)
        ev = {"type": "point", "n": n, "verdict": rr.verdict if rr else "?", "time": min(times) if times else None,
              "mem_kb": rr.mem_kb if rr else None, "input_bytes": len(inp)}
        job.push(ev)
        events.append(ev)
        if rr is None or rr.verdict != "OK":
            if rr is not None and rr.verdict == "TLE":
                stopped = f"przekroczono {run_cap:g} s dla n = {fmt_num(n)}"
            else:
                stopped = f"program zakończył się werdyktem {rr.verdict if rr else '?'} dla n = {fmt_num(n)}" + (
                    f": {rr.stderr[:300]}" if rr and rr.stderr else "")
            break
        t = min(times)
        points.append([n, t])
        if auto and t > tl * 0.9:
            stopped = f"czas przekroczył ~limit ({tl:g} s) — dalsze podwajanie nie ma sensu"
            break
        if auto and t > run_cap / 3:
            stopped = "czas zbliża się do limitu pomiaru"
            break
    fit = fit_models(points)
    max_ok = None
    if fit.get("best_fit"):
        bf = fit["best_fit"]
        fn = dict(MODELS)[fit["best"]]
        lo, hi = 1.0, 1e12
        for _ in range(120):
            mid = math.sqrt(lo * hi)
            try:
                v = bf["a"] + bf["b"] * fn(mid)
            except OverflowError:
                v = float("inf")
            if v <= tl:
                lo = mid
            else:
                hi = mid
        max_ok = lo
    return {"ok": True, "points": points, "events": events, "fit": fit, "stopped": stopped, "input_source": source,
            "template": template if source != "generator" else None, "time_limit": tl,
            "max_n_in_limit": max_ok, "max_n_fmt": fmt_num(round(max_ok)) if max_ok else None,
            "series": [{"name": "pomiar", "points": points}] + ([{"name": f"dopasowanie O({fit['best']})",
                                                                  "points": fit["best_fit"]["curve"]}]
                                                                 if fit.get("best_fit") else [])}


def handle_measure(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod do pomiaru.")
    if jobs is None:
        raise ApiError("Brak modułu zadań w tle.", 500)
    return {"job": jobs.submit(_measure_work, body, title="Pomiar złożoności")}


def handle_template(body: dict) -> dict:
    code = body.get("code") or ""
    return {"template": infer_template(code) or "{n}\n{arr:n:1:1000000000}"}


ROUTES = {
    "POST /api/complexity/analyze": handle_analyze,
    "POST /api/complexity/measure": handle_measure,
    "POST /api/complexity/parse_constraints": handle_parse_constraints,
    "POST /api/complexity/template": handle_template,
}

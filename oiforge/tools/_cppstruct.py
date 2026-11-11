"""Analiza struktury kodu C++ na tokenach (`oiforge.core.cpp_lexer`): deklaracje, pętle, wywołania, przypisania,
odczyty/zapisy wejścia-wyjścia, stałe. Współdzielone przez `core.algodetect`, `tools.complexity`, `tools.explainer`
i `tools.optimizer`.

To nie jest parser C++ — to zbiór odpornych heurystyk, które nigdy nie rzucają wyjątku na dziwnym kodzie
(najwyżej zwracają mniej informacji). Wszystkie indeksy `*_idx` to indeksy w `Code.toks` (tokeny bez komentarzy
i dyrektyw preprocesora).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from oiforge.core import cpp_lexer as lx

# ----------------------------------------------------------------------------------------------- słowniki typów
BASE_TYPE_WORDS = {
    "int", "long", "short", "unsigned", "signed", "char", "bool", "double", "float", "void", "auto", "size_t",
    "wchar_t", "int64_t", "uint64_t", "int32_t", "uint32_t", "int8_t", "uint8_t", "int16_t", "uint16_t",
    "__int128", "__int128_t", "__uint128_t", "char16_t", "char32_t", "ssize_t", "ptrdiff_t",
}
TEMPLATE_TYPES = {
    "vector", "deque", "list", "forward_list", "set", "multiset", "unordered_set", "unordered_multiset", "map",
    "multimap", "unordered_map", "unordered_multimap", "priority_queue", "queue", "stack", "pair", "tuple",
    "array", "bitset", "basic_string", "greater", "less", "function", "optional", "valarray", "complex",
    "gp_hash_table", "cc_hash_table", "tree", "greater_equal", "less_equal", "hash", "span",
}
PLAIN_TYPES = {"string", "wstring", "string_view", "istringstream", "stringstream", "ostringstream"}
QUALIFIERS = {"const", "static", "constexpr", "volatile", "register", "inline", "extern", "thread_local",
              "mutable", "constinit", "typename", "struct", "class", "unsigned", "signed"}

KIND_OF = {
    "vector": "vector", "valarray": "vector", "deque": "deque", "list": "list", "forward_list": "list",
    "set": "set", "multiset": "multiset", "unordered_set": "uset", "unordered_multiset": "uset",
    "map": "map", "multimap": "multimap", "unordered_map": "umap", "unordered_multimap": "umap",
    "gp_hash_table": "umap", "cc_hash_table": "umap", "tree": "set",
    "priority_queue": "pq", "queue": "queue", "stack": "stack", "bitset": "bitset", "array": "stdarray",
    "string": "string", "basic_string": "string", "wstring": "string", "pair": "pair", "tuple": "tuple",
}
CONTAINER_KINDS = {"vector", "deque", "list", "set", "multiset", "uset", "map", "multimap", "umap", "pq", "queue",
                   "stack", "bitset", "stdarray", "string"}

SCALAR_SIZES = {
    "char": 1, "bool": 1, "int8_t": 1, "uint8_t": 1, "short": 2, "int16_t": 2, "uint16_t": 2,
    "int": 4, "unsigned": 4, "signed": 4, "float": 4, "int32_t": 4, "uint32_t": 4, "char32_t": 4, "wchar_t": 4,
    "long": 8, "long long": 8, "unsigned long long": 8, "double": 8, "size_t": 8, "int64_t": 8, "uint64_t": 8,
    "ssize_t": 8, "ptrdiff_t": 8, "long double": 16, "__int128": 16, "__int128_t": 16, "__uint128_t": 16,
}

_IO_NAMES = {"cin", "cout", "cerr", "scanf", "printf", "puts", "getchar", "putchar", "getline", "gets", "fgets"}


@dataclass
class Decl:
    name: str
    type: str                  # tekst typu (np. "vector<vector<int>>")
    kind: str                  # scalar | array | vector | set | map | umap | pq | queue | stack | deque | string |
                               # bitset | pair | struct | other  (dla tablic C: kind="array", elem=typ bazowy)
    elem: str = ""             # typ elementu (dla kontenerów i tablic)
    dims: list = field(default_factory=list)       # wymiary tablicy C (tekst wyrażeń)
    ctor: list = field(default_factory=list)       # argumenty konstruktora (tekst) np. vector<int> v(n, 0)
    init: str = ""             # tekst inicjalizatora po '='
    line: int = 0
    idx: int = 0               # indeks tokenu nazwy
    scope: str = "global"      # "global" albo nazwa funkcji
    is_param: bool = False
    is_ref: bool = False
    is_const: bool = False
    is_ptr: bool = False
    nested: int = 0            # liczba zagnieżdżeń kontenerów (vector<vector<int>> -> 2)


@dataclass
class Func:
    name: str
    ret: str
    params: list               # list[Decl]
    line: int
    end_line: int
    open_idx: int              # indeks '{'
    close_idx: int             # indeks '}'
    owner: str | None = None
    calls: list = field(default_factory=list)      # list[Call] wewnątrz ciała
    self_calls: list = field(default_factory=list)  # list[Call] rekurencyjne


@dataclass
class Call:
    name: str
    idx: int                   # indeks tokenu nazwy
    line: int
    receiver: str | None = None    # dla metod: nazwa bazowej zmiennej (g w g[v].push_back)
    receiver_text: str = ""        # pełny tekst odbiorcy (g[v])
    args: list = field(default_factory=list)      # list[(start, end)] zakresy tokenów argumentów (włącznie)
    open_idx: int = 0
    close_idx: int = 0
    func: str | None = None    # w której funkcji leży wywołanie
    is_method: bool = False
    std: bool = False          # std::name


@dataclass
class Assign:
    target: str                # bazowa nazwa zmiennej (dp w dp[i][j])
    target_text: str           # pełny tekst lewej strony
    op: str                    # = += -= *= /= %= <<= >>= &= |= ^= ++ --
    rhs: tuple | None          # zakres tokenów prawej strony (start, end) albo None
    idx: int                   # indeks pierwszego tokenu lewej strony
    line: int
    func: str | None = None
    indexed: bool = False
    index_texts: list = field(default_factory=list)   # teksty indeksów [i], [j-1]


@dataclass
class Loop:
    kind: str                  # for | while | do | range
    idx: int                   # indeks słowa kluczowego
    line: int
    end_line: int
    head: tuple                # (open, close) nawiasy nagłówka; dla do: nawiasy warunku while
    body: tuple                # (start, end) zakres ciała (włącznie); dla bloku: start='{'
    parent: int | None = None  # indeks w Code.loops
    depth: int = 0
    func: str | None = None
    var: str | None = None     # zmienna sterująca
    init: tuple | None = None  # zakresy części nagłówka for
    cond: tuple | None = None
    step: tuple | None = None
    range_expr: tuple | None = None   # dla range-for: zakres wyrażenia po ':'
    children: list = field(default_factory=list)


@dataclass
class IOStmt:
    kind: str                  # read | write
    api: str                   # cin | scanf | getline | cout | printf | puts ...
    targets: list              # dla read: lista tekstów celów; dla write: lista tekstów wypisywanych wyrażeń
    line: int
    idx: int
    end_idx: int
    func: str | None = None
    target_ranges: list = field(default_factory=list)


# ----------------------------------------------------------------------------------------------- pomocnicze
_NUM_SUFFIX = re.compile(r"[uUlLzZ]+$")


def parse_number(text: str) -> float | None:
    """Wartość literału liczbowego C++ (1e9, 1'000'000, 0x1F, 100LL, 2.5) albo None."""
    t = text.replace("'", "")
    try:
        if t.lower().startswith("0x"):
            return float(int(_NUM_SUFFIX.sub("", t), 16))
        if t.lower().startswith("0b"):
            return float(int(_NUM_SUFFIX.sub("", t)[2:], 2))
        t2 = _NUM_SUFFIX.sub("", t)
        if t2.endswith(("f", "F")) and ("." in t2 or "e" in t2.lower()):
            t2 = t2[:-1]
        if len(t2) > 1 and t2.startswith("0") and t2.isdigit():
            return float(int(t2, 8))
        return float(t2)
    except (ValueError, OverflowError):
        return None


def fmt_num(v: float) -> str:
    """Ładny zapis liczby: 200000 -> 2·10^5, 1000000007 -> 10^9+7, 512 -> 512."""
    if v is None:
        return "?"
    if v != v or v in (math.inf, -math.inf):
        return "∞"
    a = abs(v)
    if a < 10000:
        return str(int(v)) if float(v).is_integer() else f"{v:g}"
    e = int(math.floor(math.log10(a)))
    m = a / 10 ** e
    sign = "-" if v < 0 else ""
    rest = a - round(m) * 10 ** e if abs(m - round(m)) < 1e-9 else None
    if abs(m - 1) < 1e-9:
        return f"{sign}10^{e}"
    if rest is not None and abs(rest) < 1e-9:
        return f"{sign}{int(round(m))}·10^{e}"
    if abs(m - round(m, 1)) < 1e-9:
        return f"{sign}{round(m, 1):g}·10^{e}"
    # np. 1000000007 = 10^9 + 7
    base = 10 ** e
    if abs(a - base) < 1000 and float(a).is_integer():
        d = int(a - base)
        return f"{sign}10^{e}{'+' if d >= 0 else '-'}{abs(d)}"
    return f"{sign}{m:.2f}·10^{e}"


_NO_SPACE_BEFORE = {")", "]", ",", ";", ".", "->", "::"}
_NO_SPACE_AFTER = {"(", "[", ".", "->", "::", "!", "~"}
_UNARY_PREV = {"(", "[", ",", "{", ";", "?", ":", "return", "case"}


def join_tokens(toks: list) -> str:
    """Skleja tokeny w czytelny tekst (spacje tylko tam, gdzie trzeba)."""
    out = []
    tmpl = 0
    for k, t in enumerate(toks):
        s = t.text
        if k == 0:
            out.append(s)
            continue
        p = toks[k - 1]
        pp = toks[k - 2] if k >= 2 else None
        space = True
        if s in _NO_SPACE_BEFORE:
            space = False
        elif p.text in _NO_SPACE_AFTER:
            space = False
        elif s in ("(", "[") and (p.kind == "id" or p.text in (")", "]", ">", "sizeof", "operator")):
            space = False
        elif s in ("++", "--") and (p.kind in ("id", "num") or p.text in (")", "]")):
            space = False
        elif p.text in ("++", "--") and (pp is None or pp.kind == "op" or pp.text in _UNARY_PREV):
            space = False
        elif p.text in ("-", "+", "*", "&", "!", "~") and p.kind == "op" and \
                (pp is None or (pp.kind == "op" and pp.text not in ("++", "--")) or pp.text in _UNARY_PREV):
            space = False
        if s == "<" and p.text in TEMPLATE_TYPES:
            space = False
            tmpl += 1
        elif tmpl > 0 and p.text == "<":
            space = False
        elif tmpl > 0 and s in (">", ">>"):
            space = False
            tmpl = max(0, tmpl - len(s))
        out.append((" " if space else "") + s)
    return "".join(out)


def normalize_expr(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


# ----------------------------------------------------------------------------------------------- Code
class Code:
    """Wynik analizy: wszystkie struktury dostępne jako atrybuty. Konstrukcja jest tania (~ms na 100 linii)."""

    def __init__(self, code: str):
        self.code = (code or "").replace("\r\n", "\n").replace("\r", "\n")
        self.lines = self.code.split("\n")
        try:
            self.all_toks = lx.tokenize(self.code)
        except Exception:  # noqa: BLE001
            self.all_toks = []
        self.toks = lx.significant(self.all_toks)
        self.pp = [t for t in self.all_toks if t.kind == "pp"]
        self.comments = [t for t in self.all_toks if t.kind == "comment"]
        self.n = len(self.toks)
        self.texts = [t.text for t in self.toks]
        self.pairs = lx.match_brackets(self.toks)
        self.pos2idx = {t.pos: i for i, t in enumerate(self.toks)}
        self.aliases: dict[str, str] = {}      # ll -> long long, vi -> vector<int>
        self.defines: dict[str, str] = {}      # NAZWA -> tekst
        self.consts: dict[str, float] = {}     # N -> 100005
        self.const_lines: dict[str, int] = {}
        self.structs: dict[str, list] = {}     # nazwa -> lista Decl pól
        self.functions: list[Func] = []
        self.func_by_name: dict[str, Func] = {}
        self.decls: list[Decl] = []
        self.loops: list[Loop] = []
        self.calls: list[Call] = []
        self.assigns: list[Assign] = []
        self.io: list[IOStmt] = []
        self.int_is_ll = False
        self._decl_by_scope: dict[tuple, Decl] = {}
        self._parse_pp()
        self._safe(self._find_structs)
        self._safe(self._find_aliases)
        self._safe(self._find_functions)
        self._safe(self._find_decls)
        self._safe(self._find_loops)
        self._safe(self._find_calls)
        self._safe(self._find_assigns)
        self._safe(self._find_io)
        self._safe(self._find_consts)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 — heurystyki nigdy nie mogą wywalić analizy
            pass

    # ------------------------------------------------------------------ podstawowe narzędzia na tokenach
    def text(self, a: int, b: int) -> str:
        """Tekst tokenów [a, b] (włącznie)."""
        if a is None or b is None or a > b:
            return ""
        return join_tokens(self.toks[max(0, a): min(self.n, b + 1)])

    def src(self, a: int, b: int) -> str:
        """Oryginalny fragment źródła od tokenu a do b (włącznie)."""
        if a is None or b is None or a > b or a >= self.n:
            return ""
        b = min(b, self.n - 1)
        return self.code[self.toks[a].pos: self.toks[b].end]

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 1 <= line <= len(self.lines) else ""

    def split_top(self, a: int, b: int, seps: tuple) -> list[tuple]:
        """Dzieli zakres [a, b] po separatorach na głębokości 0 (nawiasy (), [], {})."""
        parts = []
        start = a
        i = a
        while i <= b:
            t = self.texts[i]
            if t in ("(", "[", "{") and i in self.pairs and self.pairs[i] > i:
                i = self.pairs[i] + 1
                continue
            if t in seps:
                parts.append((start, i - 1))
                start = i + 1
            i += 1
        parts.append((start, b))
        return parts

    def find_top(self, a: int, b: int, texts: tuple) -> list[int]:
        """Indeksy tokenów o danym tekście na głębokości 0 w zakresie [a, b]."""
        res = []
        i = a
        while i <= b:
            t = self.texts[i]
            if t in ("(", "[", "{") and i in self.pairs and self.pairs[i] > i:
                i = self.pairs[i] + 1
                continue
            if t in texts:
                res.append(i)
            i += 1
        return res

    def ids_in(self, a: int, b: int) -> list[str]:
        return [self.texts[i] for i in range(max(0, a), min(self.n, b + 1)) if self.toks[i].kind == "id"]

    def stmt_end(self, i: int) -> int:
        """Indeks ostatniego tokenu instrukcji zaczynającej się w i."""
        n = self.n
        if i >= n:
            return n - 1
        t = self.texts[i]
        if t == "{":
            return self.pairs.get(i, n - 1)
        if t in ("for", "while", "switch") and i + 1 < n and self.texts[i + 1] == "(":
            j = self.pairs.get(i + 1)
            if j is None:
                return n - 1
            if j + 1 < n and self.texts[j + 1] == ";":
                return j + 1
            return self.stmt_end(j + 1)
        if t == "if":
            k = i + 1
            if k < n and self.texts[k] == "constexpr":
                k += 1
            j = self.pairs.get(k)
            if j is None:
                return n - 1
            e = self.stmt_end(j + 1)
            if e + 1 < n and self.texts[e + 1] == "else":
                return self.stmt_end(e + 2)
            return e
        if t == "else":
            return self.stmt_end(i + 1)
        if t == "do":
            e = self.stmt_end(i + 1)
            if e + 2 < n and self.texts[e + 1] == "while" and self.texts[e + 2] == "(":
                j = self.pairs.get(e + 2, e + 2)
                return j + 1 if j + 1 < n and self.texts[j + 1] == ";" else j
            return e
        if t in ("case", "default"):
            j = i
            while j < n and self.texts[j] != ":":
                j += 1
            return j
        j = i
        while j < n:
            tj = self.texts[j]
            if tj in ("(", "[", "{") and j in self.pairs and self.pairs[j] > j:
                j = self.pairs[j] + 1
                continue
            if tj == ";":
                return j
            if tj == "}":
                return j - 1
            j += 1
        return n - 1

    def block_statements(self, a: int, b: int) -> list[tuple]:
        """Instrukcje najwyższego poziomu w zakresie [a, b] (np. wnętrze bloku bez klamer)."""
        res = []
        i = a
        guard = 0
        while i <= b and guard < 100000:
            guard += 1
            e = min(self.stmt_end(i), b)
            if e < i:
                e = i
            res.append((i, e))
            i = e + 1
        return res

    def body_range(self, start: int) -> tuple:
        """Zakres instrukcji-ciała (dla bloku: '{'..'}')."""
        return start, self.stmt_end(start)

    def enclosing_func(self, idx: int) -> Func | None:
        for f in self.functions:
            if f.open_idx <= idx <= f.close_idx:
                return f
        return None

    def func_name_at(self, idx: int) -> str | None:
        f = self.enclosing_func(idx)
        return f.name if f else None

    def loops_containing(self, idx: int) -> list[int]:
        """Indeksy pętli (od najbardziej zewnętrznej) zawierających token idx w ciele lub nagłówku."""
        res = [k for k, L in enumerate(self.loops) if L.idx <= idx <= L.body[1] and idx != L.idx]
        res.sort(key=lambda k: self.loops[k].depth)
        return res

    def innermost_loop(self, idx: int) -> int | None:
        ls = self.loops_containing(idx)
        return ls[-1] if ls else None

    def decl_of(self, name: str, at_idx: int | None = None) -> Decl | None:
        """Najbliższa deklaracja zmiennej (lokalna w funkcji ma pierwszeństwo przed globalną)."""
        best = None
        fname = self.func_name_at(at_idx) if at_idx is not None else None
        for d in self.decls:
            if d.name != name:
                continue
            if at_idx is not None and d.scope != "global":
                if d.scope != fname:
                    continue
                if d.idx > at_idx and not d.is_param:
                    continue
                if best is None or best.scope == "global" or d.idx > best.idx:
                    best = d
            elif best is None:
                best = d
        if best is None and at_idx is None:
            return None
        if best is None:
            for d in self.decls:
                if d.name == name:
                    return d
        return best

    def resolve_type(self, type_text: str) -> str:
        """Rozwija aliasy (ll -> long long, vi -> vector<int>)."""
        t = type_text
        for _ in range(4):
            changed = False
            for a, v in self.aliases.items():
                nt = re.sub(rf"\b{re.escape(a)}\b", v, t)
                if nt != t:
                    t = nt
                    changed = True
            if not changed:
                break
        if self.int_is_ll:
            t = re.sub(r"(?<!long )\bint\b", "long long", t)
        return re.sub(r"\bstd::", "", t)

    # ------------------------------------------------------------------ preprocesor, aliasy, stałe
    def _parse_pp(self):
        for t in self.pp:
            m = re.match(r"#\s*define\s+(\w+)\s+(.+)$", t.text.strip())
            if m:
                name, val = m.group(1), m.group(2).strip()
                if name == "int" and re.fullmatch(r"long\s+long(\s+int)?", val):
                    self.int_is_ll = True
                    continue
                self.defines[name] = val
                if re.fullmatch(r"(unsigned\s+|signed\s+)?(long\s+long|long|int|short|char|double|long\s+double|"
                                r"unsigned)(\s+int)?", val) or re.match(r"(std::)?(vector|pair|map|set|priority_queue|"
                                                                        r"unordered_map|string|tuple|array)\b", val):
                    self.aliases[name] = val
                else:
                    v = self._eval_const_text(val)
                    if v is not None:
                        self.consts[name] = v
                        self.const_lines[name] = t.line

    def _find_aliases(self):
        T = self.texts
        for i, t in enumerate(T):
            if t == "typedef":
                e = self.stmt_end(i)
                if e - 1 > i and self.toks[e - 1].kind == "id":
                    self.aliases[T[e - 1]] = self.text(i + 1, e - 2)
            elif t == "using" and i + 2 < self.n and self.toks[i + 1].kind == "id" and T[i + 2] == "=":
                e = self.stmt_end(i)
                self.aliases[T[i + 1]] = self.text(i + 3, e - 1)

    def _find_structs(self):
        T = self.texts
        for i, t in enumerate(T):
            if t in ("struct", "class") and i + 2 < self.n and self.toks[i + 1].kind == "id":
                j = i + 2
                while j < self.n and T[j] not in ("{", ";", "(", ")", ","):
                    j += 1
                if j < self.n and T[j] == "{" and j in self.pairs:
                    self.structs[T[i + 1]] = []
                    self._struct_ranges = getattr(self, "_struct_ranges", [])
                    self._struct_ranges.append((T[i + 1], j, self.pairs[j]))

    def _eval_const_text(self, text: str) -> float | None:
        try:
            toks = lx.significant(lx.tokenize(text))
        except Exception:  # noqa: BLE001
            return None
        return self._eval_tokens(toks)

    def _eval_tokens(self, toks: list) -> float | None:
        """Bezpieczna ewaluacja stałego wyrażenia (liczby, + - * / % << >> nawiasy, znane stałe)."""
        parts = []
        for t in toks:
            if t.kind == "num":
                v = parse_number(t.text)
                if v is None:
                    return None
                parts.append(repr(v) if not float(v).is_integer() else str(int(v)))
            elif t.kind == "id":
                if t.text in self.consts:
                    v = self.consts[t.text]
                    parts.append(str(int(v)) if float(v).is_integer() else repr(v))
                elif t.text in ("LLONG_MAX", "INT_MAX", "LONG_LONG_MAX"):
                    parts.append(str(2 ** 63 - 1 if "LL" in t.text or "LONG" in t.text else 2 ** 31 - 1))
                elif t.text in ("INT_MIN",):
                    parts.append(str(-2 ** 31))
                else:
                    return None
            elif t.kind == "kw" and t.text in ("int", "long", "unsigned"):
                continue        # rzutowania typu (int)1e9
            elif t.text in ("+", "-", "*", "/", "%", "<<", ">>", "(", ")", "|", "&", "^"):
                parts.append(t.text)
            else:
                return None
        expr = " ".join(parts)
        if not expr or len(expr) > 200:
            return None
        expr = re.sub(r"\(\s*\)", "", expr)
        try:
            if re.search(r"[^0-9eE.+\-*/%<>()|&^ ]", expr):
                return None
            # dzielenie całkowite jak w C++ dla liczb całkowitych
            val = eval(expr.replace("/", "//") if "." not in expr and "e" not in expr.lower() else expr,
                       {"__builtins__": {}}, {})
            return float(val)
        except Exception:  # noqa: BLE001
            return None

    def _find_consts(self):
        # const int N = 1e5 + 5;  constexpr ll MOD = 1e9+7;  int MAX = 1 << 20; (globalne inicjalizowane)
        for d in self.decls:
            if d.kind != "scalar" or not d.init or d.is_param:
                continue
            if d.scope != "global" and not d.is_const:
                continue
            if not d.is_const and any(a.target == d.name and a.idx != d.idx for a in self.assigns):
                continue
            try:
                toks = lx.significant(lx.tokenize(d.init))
            except Exception:  # noqa: BLE001
                continue
            v = self._eval_tokens(toks)
            if v is not None and d.name not in self.consts:
                self.consts[d.name] = v
                self.const_lines[d.name] = d.line

    # ------------------------------------------------------------------ funkcje
    def _find_functions(self):
        try:
            fns = lx.find_functions(self.code)
        except Exception:  # noqa: BLE001
            fns = []
        for f in fns:
            oi = self.pos2idx.get(f["body_start"])
            ci = self.pos2idx.get(f["body_end"])
            if oi is None or ci is None:
                continue
            fn = Func(name=f["name"], ret=f.get("ret") or "", params=[], line=f["line"], end_line=f["end_line"],
                      open_idx=oi, close_idx=ci, owner=f.get("owner"))
            # parametry: zakres nawiasów przed ciałem
            j = oi - 1
            while j > 0 and self.texts[j] != ")":
                j -= 1
            if j in self.pairs:
                po = self.pairs[j]
                if j - po > 1:
                    for a, b in self.split_top(po + 1, j - 1, (",",)):
                        d = self._parse_param(a, b, fn.name)
                        if d:
                            fn.params.append(d)
            self.functions.append(fn)
            self.func_by_name.setdefault(fn.name, fn)

    def _parse_param(self, a: int, b: int, fname: str) -> Decl | None:
        if a > b:
            return None
        T = self.texts
        # domyślna wartość
        eq = self.find_top(a, b, ("=",))
        if eq:
            b = eq[0] - 1
        # nazwa = ostatni identyfikator (pomijając [] )
        k = b
        dims = []
        while k >= a and T[k] == "]":
            o = self.pairs.get(k, k)
            dims.insert(0, self.text(o + 1, k - 1))
            k = o - 1
        if k < a or self.toks[k].kind != "id":
            return None
        name = T[k]
        type_toks = self.toks[a:k]
        type_text = join_tokens(type_toks)
        is_ref = "&" in [t.text for t in type_toks] or "&&" in [t.text for t in type_toks]
        is_ptr = "*" in [t.text for t in type_toks]
        is_const = "const" in [t.text for t in type_toks]
        clean = re.sub(r"\bconst\b|&|\*", "", type_text).strip()
        kind, elem, nested = self.classify_type(clean)
        if dims or is_ptr and kind == "scalar":
            kind = "array"
            elem = clean
        d = Decl(name=name, type=clean, kind=kind, elem=elem, dims=dims, line=self.toks[k].line, idx=k, scope=fname,
                 is_param=True, is_ref=is_ref, is_const=is_const, is_ptr=is_ptr, nested=nested)
        self.decls.append(d)
        return d

    # ------------------------------------------------------------------ typy
    def classify_type(self, type_text: str) -> tuple[str, str, int]:
        """(kind, elem, nested) dla tekstu typu."""
        t = self.resolve_type(type_text).strip()
        t = re.sub(r"^(const|static|constexpr|inline|volatile|register|mutable|extern)\s+", "", t)
        m = re.match(r"(?:__gnu_pbds::)?(\w+)\s*<(.*)>\s*$", t, re.S)
        if m:
            base, inner = m.group(1), m.group(2)
            kind = KIND_OF.get(base, "other")
            nested = 1
            if kind in ("vector", "deque", "list", "set", "multiset", "uset", "pq", "queue", "stack", "stdarray"):
                first = _split_template_args(inner)[0] if inner else ""
                k2, _, n2 = self.classify_type(first)
                if k2 in CONTAINER_KINDS:
                    nested += n2
                return kind, first.strip(), nested
            if kind in ("map", "multimap", "umap"):
                return kind, inner.strip(), 1
            if kind == "bitset":
                return "bitset", inner.strip(), 1
            if kind in ("pair", "tuple"):
                return kind, inner.strip(), 0
            return kind, inner.strip(), 0
        base = t.split("::")[-1].strip()
        if base in ("string", "wstring", "basic_string"):
            return "string", "char", 1
        if base in self.structs:
            return "struct", base, 0
        words = t.replace("unsigned", "").split()
        if t and all(w in BASE_TYPE_WORDS or w in ("long", "unsigned") for w in t.split()):
            return "scalar", t, 0
        if words and words[-1] in BASE_TYPE_WORDS:
            return "scalar", t, 0
        return "other", t, 0

    def sizeof(self, type_text: str, depth: int = 0) -> int:
        """Przybliżony sizeof typu w bajtach."""
        if depth > 6:
            return 8
        t = self.resolve_type(type_text).strip()
        t = re.sub(r"\b(const|static|constexpr|volatile|mutable|unsigned|signed)\b", "", t).strip()
        t = re.sub(r"\s+", " ", t)
        if t in ("", "int"):
            return 4
        if t in SCALAR_SIZES:
            return SCALAR_SIZES[t]
        if t in ("long int", "long long int"):
            return 8
        m = re.match(r"(\w+)\s*<(.*)>$", t, re.S)
        if m:
            base, inner = m.group(1), m.group(2)
            args = _split_template_args(inner)
            if base == "pair" and len(args) == 2:
                a, b = self.sizeof(args[0], depth + 1), self.sizeof(args[1], depth + 1)
                al = max(a, b, 1)
                al = min(al, 8)
                return int(math.ceil((math.ceil(a / al) * al + b) / al) * al)
            if base == "tuple":
                s = sum(self.sizeof(x, depth + 1) for x in args)
                return int(math.ceil(s / 8) * 8) if s > 4 else s
            if base == "array" and len(args) == 2:
                v = self._eval_const_text(args[1]) or 1
                return int(self.sizeof(args[0], depth + 1) * v)
            if base == "bitset":
                v = self._eval_const_text(args[0]) or 64
                return int(math.ceil(v / 64) * 8)
            if base in ("vector", "deque", "string", "basic_string", "priority_queue", "queue", "stack"):
                return 24 if base == "vector" else (32 if base in ("string", "basic_string") else 80)
            return 48
        if t in ("string", "wstring"):
            return 32
        if t in self.structs:
            fields = self.structs.get(t) or []
            s = sum(self.sizeof(f.type, depth + 1) * (int(_prod_dims(self, f.dims)) if f.dims else 1) for f in fields)
            return max(1, int(math.ceil(s / 8) * 8)) if s > 4 else max(1, s)
        if "*" in t:
            return 8
        return 8

    # ------------------------------------------------------------------ deklaracje
    def _type_end(self, i: int) -> int | None:
        """Jeśli w i zaczyna się typ, zwraca indeks ostatniego tokenu typu; inaczej None."""
        T = self.texts
        n = self.n
        j = i
        seen_type = False
        while j < n and T[j] in QUALIFIERS and T[j] not in ("struct", "class"):
            if T[j] in ("unsigned", "signed"):
                seen_type = True
            j += 1
        if j < n and T[j] == "std" and j + 1 < n and T[j + 1] == "::":
            j += 2
        if j >= n:
            return None
        t = T[j]
        kind = self.toks[j].kind
        if t in BASE_TYPE_WORDS or (kind == "id" and t in self.aliases) or t in PLAIN_TYPES or \
                (kind == "id" and t in self.structs):
            k = j
            while k + 1 < n and (T[k + 1] in BASE_TYPE_WORDS or T[k + 1] in ("long", "int", "unsigned")):
                k += 1
            if k + 1 < n and T[k + 1] == "<" and t in self.aliases:
                k = self._skip_template(k + 1)
                if k is None:
                    return None
            return k
        if t in TEMPLATE_TYPES and j + 1 < n and T[j + 1] == "<":
            k = self._skip_template(j + 1)
            if k is None:
                return None
            # ::iterator itp.
            while k + 2 < n and T[k + 1] == "::" and self.toks[k + 2].kind == "id":
                k += 2
            return k
        if seen_type:
            return j - 1
        return None

    def _skip_template(self, lt: int) -> int | None:
        """lt = indeks '<'; zwraca indeks zamykającego '>' (obsługuje '>>')."""
        T = self.texts
        depth = 0
        k = lt
        while k < self.n:
            s = T[k]
            if s == "<":
                depth += 1
            elif s == ">":
                depth -= 1
            elif s == ">>":
                depth -= 2
            elif s in (";", "{", "}"):
                return None
            elif s == "(" and k in self.pairs:
                k = self.pairs[k]
            if depth <= 0:
                return k
            k += 1
        return None

    def _find_decls(self):
        T = self.texts
        n = self.n
        starts = {0}
        for i, t in enumerate(T):
            if t in (";", "{", "}"):
                starts.add(i + 1)
            elif t == "(" and i > 0 and T[i - 1] == "for":
                starts.add(i + 1)
            elif t == ")" and i in self.pairs and self.pairs[i] > 0 and T[self.pairs[i] - 1] in ("if", "while") :
                starts.add(i + 1)
            elif t == ":" and i > 0 and T[i - 1] in ("public", "private", "protected"):
                starts.add(i + 1)
        func_heads = set()
        for f in self.functions:
            func_heads.add(f.open_idx)
        struct_ranges = getattr(self, "_struct_ranges", [])
        for i in sorted(starts):
            if i >= n:
                continue
            if T[i] in ("return", "typedef", "using", "template", "friend", "operator", "delete", "goto"):
                continue
            te = self._type_end(i)
            if te is None or te + 1 >= n:
                continue
            type_text = self.text(i, te)
            if self.toks[te + 1].kind not in ("id",) and T[te + 1] not in ("*", "&", "&&"):
                continue
            j = te + 1
            fname = self.func_name_at(i)
            owner_struct = None
            for sname, so, sc in struct_ranges:
                if so < i < sc and not (fname and self.func_by_name.get(fname) and
                                        so < self.func_by_name[fname].open_idx < sc and
                                        self.func_by_name[fname].open_idx < i):
                    owner_struct = sname
            guard = 0
            while j < n and guard < 200:
                guard += 1
                is_ref = is_ptr = False
                while j < n and T[j] in ("*", "&", "&&"):
                    is_ref = is_ref or T[j] in ("&", "&&")
                    is_ptr = is_ptr or T[j] == "*"
                    j += 1
                if j >= n or self.toks[j].kind != "id":
                    break
                name_idx = j
                name = T[j]
                j += 1
                dims = []
                while j < n and T[j] == "[" and j in self.pairs:
                    c = self.pairs[j]
                    dims.append(self.text(j + 1, c - 1))
                    j = c + 1
                ctor = []
                init = ""
                if j < n and T[j] == "(" and j in self.pairs:
                    c = self.pairs[j]
                    # funkcja (prototyp/definicja)?  int f(int a) {   albo   int f(int);
                    if (c + 1 < n and T[c + 1] in ("{", "const", "noexcept", "->", "override")) or \
                            (c > j + 1 and self._type_end(j + 1) is not None and
                             (c + 1 < n and T[c + 1] == ";")):
                        break
                    ctor = [self.text(a, b) for a, b in self.split_top(j + 1, c - 1, (",",))] if c > j + 1 else []
                    j = c + 1
                elif j < n and T[j] == "{" and j in self.pairs:
                    c = self.pairs[j]
                    init = self.text(j + 1, c - 1)
                    j = c + 1
                if j < n and T[j] == "=":
                    e = j + 1
                    while e < n:
                        s = T[e]
                        if s in ("(", "[", "{") and e in self.pairs and self.pairs[e] > e:
                            e = self.pairs[e] + 1
                            continue
                        if s in (",", ";", ")") or (s == ":" and T[i - 1] == "(" if i > 0 else False):
                            break
                        e += 1
                    init = self.text(j + 1, e - 1)
                    j = e
                full_type = type_text
                kind, elem, nested = self.classify_type(type_text)
                if dims:
                    elem = type_text
                    kind = "array"
                if is_ptr and kind == "scalar":
                    kind = "pointer"
                d = Decl(name=name, type=full_type, kind=kind, elem=elem, dims=dims, ctor=ctor, init=init,
                         line=self.toks[name_idx].line, idx=name_idx,
                         scope=fname or "global", is_ref=is_ref, is_ptr=is_ptr,
                         is_const=bool(re.search(r"\b(const|constexpr)\b", self.text(i, te))), nested=nested)
                if owner_struct and fname is None:
                    self.structs.setdefault(owner_struct, []).append(d)
                else:
                    self.decls.append(d)
                if j < n and T[j] == ",":
                    j += 1
                    continue
                break

    # ------------------------------------------------------------------ pętle
    def _find_loops(self):
        T = self.texts
        n = self.n
        do_whiles = set()
        for i, t in enumerate(T):
            if t == "do" and self.toks[i].kind == "kw":
                bs, be = self.body_range(i + 1)
                if be + 2 < n and T[be + 1] == "while" and T[be + 2] == "(" and (be + 2) in self.pairs:
                    co = be + 2
                    cc = self.pairs[co]
                    do_whiles.add(be + 1)
                    self.loops.append(Loop(kind="do", idx=i, line=self.toks[i].line,
                                           end_line=self.toks[min(cc, n - 1)].line, head=(co, cc), body=(bs, be),
                                           cond=(co + 1, cc - 1)))
        for i, t in enumerate(T):
            if t not in ("for", "while") or self.toks[i].kind != "kw" or i in do_whiles:
                continue
            if i + 1 >= n or T[i + 1] != "(" or (i + 1) not in self.pairs:
                continue
            o, c = i + 1, self.pairs[i + 1]
            if c + 1 < n and T[c + 1] == ";":
                body = (c + 1, c + 1)
            else:
                body = self.body_range(c + 1)
            L = Loop(kind=t, idx=i, line=self.toks[i].line, end_line=self.toks[min(body[1], n - 1)].line,
                     head=(o, c), body=body)
            if t == "for":
                semis = self.find_top(o + 1, c - 1, (";",))
                if len(semis) >= 2:
                    L.init = (o + 1, semis[0] - 1)
                    L.cond = (semis[0] + 1, semis[1] - 1)
                    L.step = (semis[1] + 1, c - 1)
                    L.var = self._for_var(L)
                else:
                    colon = self.find_top(o + 1, c - 1, (":",))
                    if colon:
                        L.kind = "range"
                        L.range_expr = (colon[0] + 1, c - 1)
                        ids = [k for k in range(o + 1, colon[0]) if self.toks[k].kind == "id"]
                        if T[colon[0] - 1] == "]":      # structured binding  auto [a, b] : v
                            L.var = T[ids[0]] if ids else None
                        else:
                            L.var = T[ids[-1]] if ids else None
            else:
                L.cond = (o + 1, c - 1)
                ids = [self.texts[k] for k in range(o + 1, c) if self.toks[k].kind == "id"]
                L.var = ids[0] if ids else None
            self.loops.append(L)
        self.loops.sort(key=lambda L: L.idx)
        # rodzice i głębokość
        for k, L in enumerate(self.loops):
            best = None
            for k2, P in enumerate(self.loops):
                if k2 == k:
                    continue
                if P.body[0] <= L.idx <= P.body[1] and (best is None or self.loops[best].body[0] < P.body[0]):
                    best = k2
            L.parent = best
            L.func = self.func_name_at(L.idx)
        for k, L in enumerate(self.loops):
            d = 0
            p = L.parent
            seen = set()
            while p is not None and p not in seen:
                seen.add(p)
                d += 1
                p = self.loops[p].parent
            L.depth = d
            if L.parent is not None:
                self.loops[L.parent].children.append(k)

    def _for_var(self, L: Loop) -> str | None:
        T = self.texts
        if L.init and L.init[0] <= L.init[1]:
            eqs = self.find_top(L.init[0], L.init[1], ("=",))
            if eqs:
                k = eqs[0] - 1
                if k >= L.init[0] and self.toks[k].kind == "id":
                    return T[k]
            ids = [T[k] for k in range(L.init[0], L.init[1] + 1) if self.toks[k].kind == "id"]
            if ids:
                return ids[-1]
        if L.cond and L.cond[0] <= L.cond[1]:
            ids = [T[k] for k in range(L.cond[0], L.cond[1] + 1) if self.toks[k].kind == "id"]
            if ids:
                return ids[0]
        if L.step and L.step[0] <= L.step[1]:
            ids = [T[k] for k in range(L.step[0], L.step[1] + 1) if self.toks[k].kind == "id"]
            if ids:
                return ids[0]
        return None

    # ------------------------------------------------------------------ wywołania
    def _find_calls(self):
        T = self.texts
        decl_name_idx = {d.idx for d in self.decls}
        for i in range(self.n - 1):
            t = self.toks[i]
            if t.kind != "id" or T[i + 1] != "(" or i in decl_name_idx:
                continue
            if i > 0 and T[i - 1] in ("struct", "class"):
                continue
            close = self.pairs.get(i + 1)
            if close is None:
                continue
            # pomiń definicje funkcji
            if any(f.open_idx == close + 1 or (f.open_idx > close and f.open_idx - close <= 3 and f.line == t.line)
                   for f in self.functions if f.name == t.text):
                continue
            c = Call(name=t.text, idx=i, line=t.line, open_idx=i + 1, close_idx=close)
            c.args = self.split_top(i + 2, close - 1, (",",)) if close > i + 2 else []
            if i >= 1 and T[i - 1] in (".", "->"):
                c.is_method = True
                r_end = i - 2
                r_start = self._expr_start(r_end)
                c.receiver_text = self.text(r_start, r_end)
                ids = [k for k in range(r_start, r_end + 1) if self.toks[k].kind == "id"]
                c.receiver = T[ids[0]] if ids else None
            if i >= 2 and T[i - 1] == "::" and T[i - 2] == "std":
                c.std = True
            c.func = self.func_name_at(i)
            self.calls.append(c)
            f = self.func_by_name.get(c.name)
            if f and not c.is_method:
                ef = self.enclosing_func(i)
                if ef is not None:
                    ef.calls.append(c)
                    if ef.name == c.name:
                        ef.self_calls.append(c)
        for f in self.functions:
            if not f.calls:
                f.calls = [c for c in self.calls if f.open_idx < c.idx < f.close_idx]

    def _expr_start(self, end: int) -> int:
        """Początek wyrażenia-postfiksu kończącego się w end (np. g[v] w g[v].size())."""
        T = self.texts
        k = end
        while k >= 0:
            s = T[k]
            if s in (")", "]") and k in self.pairs:
                k = self.pairs[k] - 1
                continue
            if self.toks[k].kind in ("id", "kw") and s not in ("return", "if", "while", "for", "else"):
                if k - 1 >= 0 and T[k - 1] in (".", "->", "::"):
                    k -= 2
                    continue
                return k
            return k + 1
        return 0

    # ------------------------------------------------------------------ przypisania
    _ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "|=", "^="}

    def _find_assigns(self):
        T = self.texts
        n = self.n
        decl_names = {d.idx for d in self.decls}
        for i in range(n):
            s = T[i]
            if s in self._ASSIGN_OPS and self.toks[i].kind == "op":
                lend = i - 1
                if lend < 0:
                    continue
                lstart = self._expr_start(lend)
                if lstart > lend:
                    continue
                if T[lstart] in ("operator",) or (lstart > 0 and T[lstart - 1] == "operator"):
                    continue
                # deklaracja z inicjalizacją: int x = 5 → też przypisanie (ważne dla ról)
                rs = i + 1
                re_ = rs
                while re_ < n:
                    tt = T[re_]
                    if tt in ("(", "[", "{") and re_ in self.pairs and self.pairs[re_] > re_:
                        re_ = self.pairs[re_] + 1
                        continue
                    if tt in (";", ",", ")", "}") or (tt == ":" and s == "="):
                        break
                    re_ += 1
                base_ids = [k for k in range(lstart, lend + 1) if self.toks[k].kind == "id"]
                if not base_ids:
                    continue
                base = T[base_ids[0]]
                if T[lstart] == "*" or (lstart > 0 and T[lstart - 1] == "*" and lstart - 1 not in decl_names):
                    pass
                idx_texts = []
                k = lstart
                while k <= lend:
                    if T[k] == "[" and k in self.pairs:
                        idx_texts.append(self.text(k + 1, self.pairs[k] - 1))
                        k = self.pairs[k] + 1
                        continue
                    k += 1
                self.assigns.append(Assign(target=base, target_text=self.text(lstart, lend), op=s,
                                           rhs=(rs, re_ - 1) if re_ - 1 >= rs else None, idx=lstart,
                                           line=self.toks[i].line, func=self.func_name_at(i),
                                           indexed="[" in T[lstart:lend + 1], index_texts=idx_texts))
            elif s in ("++", "--"):
                # postfiks: x++  / prefiks: ++x
                if i > 0 and (self.toks[i - 1].kind == "id" or T[i - 1] in ("]", ")")):
                    lend = i - 1
                    lstart = self._expr_start(lend)
                elif i + 1 < n and self.toks[i + 1].kind == "id":
                    lstart = i + 1
                    lend = lstart
                    while lend + 1 < n and T[lend + 1] in ("[", ".", "->"):
                        if T[lend + 1] == "[" and (lend + 1) in self.pairs:
                            lend = self.pairs[lend + 1]
                        elif lend + 2 < n:
                            lend += 2
                        else:
                            break
                else:
                    continue
                ids = [k for k in range(lstart, lend + 1) if self.toks[k].kind == "id"]
                if not ids:
                    continue
                idx_texts = []
                k = lstart
                while k <= lend:
                    if T[k] == "[" and k in self.pairs:
                        idx_texts.append(self.text(k + 1, self.pairs[k] - 1))
                        k = self.pairs[k] + 1
                        continue
                    k += 1
                self.assigns.append(Assign(target=T[ids[0]], target_text=self.text(lstart, lend), op=s, rhs=None,
                                           idx=lstart, line=self.toks[i].line, func=self.func_name_at(i),
                                           indexed="[" in T[lstart:lend + 1], index_texts=idx_texts))

    def assigns_in(self, a: int, b: int, target: str | None = None) -> list[Assign]:
        return [x for x in self.assigns if a <= x.idx <= b and (target is None or x.target == target)]

    # ------------------------------------------------------------------ wejście / wyjście
    def _find_io(self):
        T = self.texts
        n = self.n
        i = 0
        while i < n:
            s = T[i]
            if s in ("cin", "cout", "cerr") and self.toks[i].kind == "id" and (i == 0 or T[i - 1] not in (".", "->")):
                e = self.stmt_end(i)
                op = ">>" if s == "cin" else "<<"
                if i + 1 < n and T[i + 1] == op:
                    parts = self.split_top(i + 2, e - 1 if T[e] == ";" else e, (op,))
                    texts = [self.text(a, b) for a, b in parts if a <= b]
                    self.io.append(IOStmt(kind="read" if s == "cin" else "write", api=s, targets=texts,
                                          line=self.toks[i].line, idx=i, end_idx=e, func=self.func_name_at(i),
                                          target_ranges=[(a, b) for a, b in parts if a <= b]))
                    i = e + 1
                    continue
            if s in ("scanf", "printf", "puts", "getline", "putchar", "getchar", "gets", "fgets") and i + 1 < n \
                    and T[i + 1] == "(" and (i + 1) in self.pairs:
                c = self.pairs[i + 1]
                args = self.split_top(i + 2, c - 1, (",",)) if c > i + 2 else []
                if s == "scanf":
                    targets = [self.text(a, b).lstrip("&").strip() for a, b in args[1:]]
                    self.io.append(IOStmt("read", "scanf", targets, self.toks[i].line, i, c, self.func_name_at(i),
                                          target_ranges=[(a + (1 if T[a] == "&" else 0), b) for a, b in args[1:]]))
                elif s == "getline":
                    targets = [self.text(a, b) for a, b in args[1:2]]
                    self.io.append(IOStmt("read", "getline", targets, self.toks[i].line, i, c, self.func_name_at(i),
                                          target_ranges=list(args[1:2])))
                elif s in ("getchar",):
                    self.io.append(IOStmt("read", s, [], self.toks[i].line, i, c, self.func_name_at(i)))
                elif s in ("gets", "fgets"):
                    self.io.append(IOStmt("read", s, [self.text(a, b) for a, b in args[:1]], self.toks[i].line, i,
                                          c, self.func_name_at(i)))
                else:
                    targets = [self.text(a, b) for a, b in (args[1:] if s == "printf" else args)]
                    self.io.append(IOStmt("write", s, targets, self.toks[i].line, i, c, self.func_name_at(i),
                                          target_ranges=list(args[1:] if s == "printf" else args)))
                i = c + 1
                continue
            i += 1

    # ------------------------------------------------------------------ pytania pomocnicze
    def has_id(self, *names: str) -> bool:
        s = set(self.texts)
        return any(n in s for n in names)

    def count_id(self, name: str) -> int:
        return sum(1 for t in self.toks if t.text == name)

    def uses_fast_io(self) -> bool:
        return any(c.name == "sync_with_stdio" for c in self.calls)

    def var_kind(self, name: str, at_idx: int | None = None) -> str | None:
        d = self.decl_of(name, at_idx)
        return d.kind if d else None


def _split_template_args(inner: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in inner:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _prod_dims(code: Code, dims: list) -> float:
    p = 1.0
    for d in dims:
        v = code._eval_const_text(d)
        p *= v if v else 1
    return p


def analyze(code: str) -> Code:
    return Code(code)


# =============================================================================================== algebra kosztów
# Sumy jednomianów c · Π v^p · log^l v · loglog^k v · b^v · v! — do zapisu O(...) i szacowania liczby operacji.

_ORDER = {"n": 0, "m": 1, "q": 2, "k": 3, "t": 4, "V": 5, "E": 6}
_SUP = {"2": "²", "3": "³", "4": "⁴"}


@dataclass(frozen=True)
class Fac:
    p: float = 0.0        # wykładnik wielomianowy
    l: float = 0.0        # wykładnik logarytmu
    ll: float = 0.0       # log log
    e: float = 0.0        # podstawa wykładnicza (0 = brak), np. 2 dla 2^n
    fact: int = 0         # silnia
    a: float = 0.0        # odwrotna funkcja Ackermanna α(v) (DSU z kompresją i łączeniem wg rangi)

    def key(self):
        return (self.fact, self.e, round(self.p, 6), round(self.l, 6), round(self.ll, 6), round(self.a, 6))

    def is_one(self) -> bool:
        return not (self.p or self.l or self.ll or self.e or self.fact or self.a)

    def mul(self, o: "Fac") -> "Fac":
        e = self.e * o.e if (self.e and o.e) else (self.e or o.e)
        return Fac(self.p + o.p, self.l + o.l, self.ll + o.ll, e, self.fact + o.fact, self.a + o.a)


def alpha(v: float) -> float:
    """Odwrotna funkcja Ackermanna — w praktyce ≤ 4 dla każdej rozsądnej wartości."""
    if v <= 2:
        return 1.0
    if v <= 7:
        return 2.0
    if v <= 2047:
        return 3.0
    return 4.0


class Term:
    __slots__ = ("coef", "f")

    def __init__(self, coef: float = 1.0, f: dict | None = None):
        self.coef = float(coef)
        self.f = {k: v for k, v in (f or {}).items() if not v.is_one()}

    def sig(self) -> tuple:
        return tuple(sorted((k, v.key()) for k, v in self.f.items()))

    def mul(self, o: "Term") -> "Term":
        f = dict(self.f)
        for k, v in o.f.items():
            f[k] = f[k].mul(v) if k in f else v
        return Term(self.coef * o.coef, f)

    def le(self, o: "Term") -> bool:
        """self ≤ o asymptotycznie (dla każdej zmiennej wykładniki nie większe)."""
        keys = set(self.f) | set(o.f)
        for k in keys:
            a = self.f.get(k, Fac()).key()
            b = o.f.get(k, Fac()).key()
            if a > b:
                return False
        return True

    def log10_value(self, values: dict) -> float:
        """log10 wartości jednomianu dla podanych wartości zmiennych."""
        s = math.log10(max(self.coef, 1e-300))
        for k, fac in self.f.items():
            v = max(float(values.get(k, 1e5)), 1.0)
            lg = math.log2(v) if v > 1 else 0.0
            if fac.fact:
                s += fac.fact * math.lgamma(v + 1) / math.log(10)
            if fac.e:
                s += v * math.log10(fac.e)
            if fac.p:
                s += fac.p * math.log10(v)
            if fac.l:
                s += fac.l * math.log10(max(lg, 1.0))
            if fac.ll:
                s += fac.ll * math.log10(max(math.log2(max(lg, 2.0)), 1.0))
            if fac.a:
                s += fac.a * math.log10(alpha(v))
        return s

    def value(self, values: dict) -> float:
        lv = self.log10_value(values)
        return 10 ** lv if lv < 300 else 1e300

    def __repr__(self):
        return f"Term({self.coef}, {self.f})"


class Cost:
    """Suma jednomianów. Niemutowalna z punktu widzenia użytkownika (operacje zwracają nowe obiekty)."""
    __slots__ = ("terms",)

    def __init__(self, terms=None):
        self.terms = [t for t in (terms or []) if t.coef > 0]

    # --- konstruktory
    @staticmethod
    def const(c: float = 1.0) -> "Cost":
        return Cost([Term(c)]) if c > 0 else Cost([])

    @staticmethod
    def zero() -> "Cost":
        return Cost([])

    @staticmethod
    def var(name: str, p: float = 1.0, l: float = 0.0, coef: float = 1.0, ll: float = 0.0, e: float = 0.0,
            fact: int = 0) -> "Cost":
        return Cost([Term(coef, {name: Fac(p, l, ll, e, fact)})])

    @staticmethod
    def log(name: str, coef: float = 1.0) -> "Cost":
        return Cost.var(name, 0, 1, coef)

    # --- operacje
    def __add__(self, o: "Cost") -> "Cost":
        return Cost(self.terms + o.terms).merged()

    def __mul__(self, o) -> "Cost":
        if isinstance(o, (int, float)):
            return Cost([Term(t.coef * o, t.f) for t in self.terms])
        if not self.terms or not o.terms:
            return Cost([])
        return Cost([a.mul(b) for a in self.terms for b in o.terms]).merged()

    __rmul__ = __mul__

    def merged(self) -> "Cost":
        d: dict = {}
        for t in self.terms:
            s = t.sig()
            if s in d:
                d[s] = Term(d[s].coef + t.coef, d[s].f)
            else:
                d[s] = Term(t.coef, t.f)
        terms = list(d.values())
        if len(terms) > 24:     # ochrona przed eksplozją — zostaw największe
            terms = Cost(terms).simplified().terms
        return Cost(terms)

    def is_zero(self) -> bool:
        return not self.terms

    def is_const(self) -> bool:
        return all(not t.f for t in self.terms)

    def const_value(self) -> float:
        return sum(t.coef for t in self.terms if not t.f)

    def vars(self) -> set:
        s = set()
        for t in self.terms:
            s |= set(t.f)
        return s

    def simplified(self) -> "Cost":
        """Usuwa składniki zdominowane (asymptotycznie) — wynik do zapisu O(...)."""
        terms = Cost(self.terms).merged().terms if self.terms else []
        # wyczyść ujemne wykładniki (np. po uproszczeniu sumy harmonicznej)
        clean = []
        for t in terms:
            f = {k: Fac(max(v.p, 0.0), max(v.l, 0.0), max(v.ll, 0.0), v.e, v.fact, max(v.a, 0.0)) for k, v in t.f.items()}
            clean.append(Term(t.coef, f))
        terms = Cost(clean).merged().terms
        keep = []
        for i, a in enumerate(terms):
            dominated = False
            for j, b in enumerate(terms):
                if i == j:
                    continue
                if a.le(b) and (not b.le(a) or j < i):
                    dominated = True
                    break
            if not dominated:
                keep.append(a)
        return Cost(keep)

    def dominant(self) -> Term | None:
        s = self.simplified()
        if not s.terms:
            return None
        return max(s.terms, key=lambda t: _growth_key(t))

    def value(self, values: dict) -> float:
        return sum(t.value(values) for t in self.terms)

    def subst(self, mapping: dict) -> "Cost":
        """Podstawia zmienne: mapping[name] = Cost (używany dominujący jednomian) — przybliżenie dla wykładników."""
        out = []
        for t in self.terms:
            acc = Cost([Term(t.coef)])
            for k, fac in t.f.items():
                if k not in mapping:
                    acc = acc * Cost([Term(1, {k: fac})])
                    continue
                rep = mapping[k]
                acc = acc * _power_of(rep, fac)
            out.extend(acc.terms)
        return Cost(out).merged()

    # --- prezentacja
    def big_o(self) -> str:
        return "O(" + self.expr() + ")"

    def expr(self) -> str:
        s = self.simplified()
        if not s.terms:
            return "1"
        terms = sorted(s.terms, key=lambda t: tuple(-x for x in _growth_key(t)[:4]) +
                       (_var_sort_key(max(t.f.items(), key=lambda kv: (kv[1].p, -_var_sort_key(kv[0])[0]))[0])
                        if t.f else (99, ""),))
        return " + ".join(term_str(t) for t in terms)

    def __repr__(self):
        return f"Cost({self.expr()})"


def _growth_key(t: Term):
    # porządek do sortowania: największa łączna "moc"
    fact = sum(v.fact for v in t.f.values())
    e = max([v.e for v in t.f.values()] + [0])
    p = sum(v.p for v in t.f.values())
    lg = sum(v.l for v in t.f.values())
    return (fact, e, p, lg, len(t.f), t.coef)


def _power_of(rep: Cost, fac: Fac) -> Cost:
    """rep^p · log^l(rep) · ... dla jednomianu dominującego rep."""
    if rep.is_zero():
        return Cost.const(1)
    if rep.is_const():
        v = max(rep.const_value(), 1.0)
        val = 1.0
        lg = math.log2(v) if v > 1 else 1.0
        if fac.p:
            val *= v ** fac.p
        if fac.l:
            val *= max(lg, 1.0) ** fac.l
        if fac.ll:
            val *= max(math.log2(max(lg, 2.0)), 1.0) ** fac.ll
        if fac.e:
            val *= min(fac.e ** min(v, 200), 1e300)
        if fac.fact:
            val *= math.exp(min(math.lgamma(v + 1), 600))
        if fac.a:
            val *= alpha(v) ** fac.a
        return Cost.const(val)
    d = rep.dominant()
    res = Cost.const(1)
    if fac.p:
        f = {k: Fac(v.p * fac.p, v.l * fac.p, v.ll * fac.p, v.e ** fac.p if v.e else 0.0, v.fact)
             for k, v in d.f.items()}
        res = res * Cost([Term(d.coef ** fac.p if d.coef > 0 else 1.0, f)])
    if fac.l:
        # log(Π v^p) ~ Σ log v  -> bierzemy log zmiennej o największym wykładniku
        main = max(d.f.items(), key=lambda kv: kv[1].key())[0] if d.f else None
        if main:
            res = res * Cost([Term(1.0, {main: Fac(0, fac.l, 0)})])
    if fac.ll:
        main = max(d.f.items(), key=lambda kv: kv[1].key())[0] if d.f else None
        if main:
            res = res * Cost([Term(1.0, {main: Fac(0, 0, fac.ll)})])
    if fac.e or fac.fact:
        main = max(d.f.items(), key=lambda kv: kv[1].key())[0] if d.f else None
        if main:
            res = res * Cost([Term(1.0, {main: Fac(0, 0, 0, fac.e, fac.fact)})])
    if fac.a:
        main = max(d.f.items(), key=lambda kv: kv[1].key())[0] if d.f else None
        if main:
            res = res * Cost([Term(1.0, {main: Fac(a=fac.a)})])
    return res


def _var_sort_key(name: str):
    return (_ORDER.get(name, 50), name)


def _pow_str(name: str, p: float) -> str:
    if abs(p - 1) < 1e-9:
        return name
    if abs(p - 0.5) < 1e-9:
        return "√" + name
    if abs(p - 1.5) < 1e-9:
        return f"{name}√{name}"
    if abs(p - round(p)) < 1e-9:
        r = str(int(round(p)))
        return name + _SUP.get(r, "^" + r)
    if abs(p - 1 / 3) < 1e-9:
        return "∛" + name
    return f"{name}^{p:g}"


def term_str(t: Term) -> str:
    if not t.f:
        return "1"
    names = sorted(t.f, key=_var_sort_key)
    poly, logs, special, alphas = [], [], [], []
    for k in names:
        f = t.f[k]
        nm = k if len(k) <= 2 or k.startswith("|") else k
        if f.fact:
            special.append(f"{nm}!" if f.fact == 1 else f"({nm}!)^{f.fact}")
        if f.e:
            b = f"{f.e:.3g}".rstrip("0").rstrip(".") if not float(f.e).is_integer() else str(int(f.e))
            special.append(f"{b}^{nm}")
        if f.p > 1e-9:
            poly.append(_pow_str(nm, f.p))
        if f.a > 1e-9:
            alphas.append(f"α({nm})" if abs(f.a - 1) < 1e-9 else f"α({nm})^{f.a:g}")
        if f.l > 1e-9:
            if abs(f.l - 1) < 1e-9:
                logs.append(f"log {nm}")
            elif abs(f.l - round(f.l)) < 1e-9:
                logs.append(f"log{_SUP.get(str(int(round(f.l))), '^' + str(int(round(f.l))))} {nm}")
            else:
                logs.append(f"log^{f.l:g} {nm}")
        if f.ll > 1e-9:
            logs.append(f"log log {nm}")
    parts = []
    if special:
        parts.append(" · ".join(special))
    poly += alphas
    if poly:
        parts.append("·".join(poly))
    s = " · ".join(parts)
    if logs:
        s = (s + " " if s else "") + " ".join(logs)
    return s or "1"


def fmt_ops(x: float) -> str:
    if x is None:
        return "?"
    if x >= 1e300:
        return "≫10^18"
    if x < 1000:
        return f"{x:.0f}"
    e = int(math.floor(math.log10(x)))
    m = x / 10 ** e
    if e > 18:
        return f"~10^{e}"
    return f"{m:.1f}·10^{e}".replace(".0·", "·")

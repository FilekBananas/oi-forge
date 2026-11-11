"""Silnik analizy statycznej Sprawdzarki kodu (OI Forge).

Bez AI: lekser C++ z rdzenia (`cpp_lexer`), rozwijanie makr na tokenach, lekki model programu
(typy, zasięgi, zmienne, funkcje, pętle) i mały parser wyrażeń (Pratt) z inferencją typów.
Na tym modelu działa ponad 50 reguł wykrywających typowe błędy olimpijskie (przepełnienia, UB,
wyjście poza tablicę, pułapki wejścia/wyjścia, wydajność). Każde zgłoszenie ma polskie wyjaśnienie,
konkretną poprawkę i link do bazy wiedzy.

Publiczne API: `analyze(code) -> {"issues": [...], "stats": {...}}`, `RULES` (katalog reguł).
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from oiforge.core import cpp_lexer
from oiforge.core.cpp_lexer import Token

INT_MAX = 2 ** 31 - 1
INT_MIN = -(2 ** 31)
LL_MAX = 2 ** 63 - 1

SEVERITY_ORDER = {"error": 0, "warning": 1, "perf": 2, "style": 3, "info": 4}


# =============================================================================================== tokeny
@dataclass(repr=False)
class XTok(Token):
    """Token po rozwinięciu makr (macro = nazwa makra, z którego pochodzi, albo None)."""
    macro: str | None = None


def _xt(t: Token, text: str | None = None, kind: str | None = None, col_shift: int = 0, macro=None) -> XTok:
    return XTok(kind or t.kind, t.text if text is None else text, t.line, t.col + col_shift, t.pos + col_shift,
                macro if macro is not None else getattr(t, "macro", None))


@dataclass
class Macro:
    name: str
    params: list | None
    body: list
    variadic: bool = False
    line: int = 0


_DEFINE_RE = re.compile(r"#\s*define\s+([A-Za-z_]\w*)(\([^)]*\))?(.*)$", re.S)


def _parse_define(text: str, line: int) -> Macro | None:
    text = re.sub(r"\\\r?\n", " ", text)
    m = _DEFINE_RE.match(text)
    if not m:
        return None
    name, params, body = m.group(1), m.group(2), m.group(3)
    # makro funkcyjne tylko gdy '(' bezpośrednio po nazwie
    if params is not None and not text[m.end(1):].startswith("("):
        body = (params or "") + body
        params = None
    toks = cpp_lexer.significant(cpp_lexer.tokenize(body.strip(), keep_comments=False))
    if any(t.text in ("#", "##") for t in toks):
        return None
    plist = None
    variadic = False
    if params is not None:
        plist = [p.strip() for p in params[1:-1].split(",") if p.strip()]
        if plist and plist[-1] == "...":
            plist[-1] = "__VA_ARGS__"
            variadic = True
    return Macro(name, plist, toks, variadic, line)


def _expand(tokens: list, macros: dict, disabled: frozenset = frozenset(), depth: int = 0) -> list:
    out = []
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        mac = macros.get(t.text) if t.kind in ("id", "kw") else None
        if mac is None or t.text in disabled or depth > 24:
            out.append(t)
            i += 1
            continue
        if mac.params is None:
            body = [_xt(t, b.text, b.kind, macro=mac.name) for b in mac.body]
            out.extend(_expand(body, macros, disabled | {mac.name}, depth + 1))
            i += 1
            continue
        if i + 1 >= n or tokens[i + 1].text != "(":
            out.append(t)
            i += 1
            continue
        j, lvl, args = i + 2, 1, [[]]
        while j < n:
            x = tokens[j]
            if x.kind == "punct" and x.text in "([{":
                lvl += 1
            elif x.kind == "punct" and x.text in ")]}":
                lvl -= 1
                if lvl == 0:
                    break
            elif x.text == "," and lvl == 1:
                args.append([])
                j += 1
                continue
            args[-1].append(x)
            j += 1
        if j >= n:
            out.append(t)
            i += 1
            continue
        params = mac.params
        body = []
        for b in mac.body:
            if b.kind in ("id", "kw") and b.text in params:
                k = params.index(b.text)
                if mac.variadic and b.text == "__VA_ARGS__":
                    rest = []
                    for q, a in enumerate(args[k:]):
                        if q:
                            rest.append(_xt(t, ",", "punct", macro=mac.name))
                        rest.extend(a)
                    body.extend(_expand(rest, macros, disabled, depth + 1))
                else:
                    body.extend(_expand(args[k] if k < len(args) else [], macros, disabled, depth + 1))
            else:
                body.append(_xt(t, b.text, b.kind, macro=mac.name))
        out.extend(_expand(body, macros, disabled | {mac.name}, depth + 1))
        i = j + 1
    return out


_PP_IF_RE = re.compile(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b\s*(.*)", re.S)


def preprocess(code: str):
    """Zwraca (tokeny_po_rozwinięciu_makr, lista_pp, makra). Pomija bloki #if 0 / #ifdef NIEZDEFINIOWANE."""
    raw = cpp_lexer.tokenize(code, keep_comments=False)
    macros: dict[str, Macro] = {}
    defined: set[str] = set()
    pps = []
    out: list = []
    chunk: list = []
    stack: list[tuple[bool, bool]] = []      # (aktywny, czy już wybrano gałąź)

    def active() -> bool:
        return all(a for a, _ in stack)

    def flush():
        if chunk:
            out.extend(_expand(chunk, macros))
            chunk.clear()

    for t in raw:
        if t.kind == "pp":
            pps.append(t)
            m = _PP_IF_RE.match(t.text)
            if m:
                flush()
                kw, arg = m.group(1), m.group(2).strip()
                if kw in ("if", "ifdef", "ifndef"):
                    if kw == "ifdef":
                        cond = arg.split()[0] in defined if arg else False
                    elif kw == "ifndef":
                        cond = arg.split()[0] not in defined if arg else True
                    else:
                        a = re.sub(r"//.*|/\*.*?\*/", "", arg).strip()
                        cond = not (a == "0" or a.lower() == "false")
                        dm = re.match(r"defined\s*\(?\s*(\w+)\s*\)?$", a)
                        if dm:
                            cond = dm.group(1) in defined
                        dm = re.match(r"!\s*defined\s*\(?\s*(\w+)\s*\)?$", a)
                        if dm:
                            cond = dm.group(1) not in defined
                    stack.append((cond, cond))
                elif kw == "elif" and stack:
                    _, taken = stack[-1]
                    stack[-1] = (not taken, True) if not taken else (False, True)
                elif kw == "else" and stack:
                    _, taken = stack[-1]
                    stack[-1] = (not taken, True)
                elif kw == "endif" and stack:
                    stack.pop()
                continue
            if not active():
                continue
            if re.match(r"#\s*define\b", t.text):
                mac = _parse_define(t.text, t.line)
                dm = re.match(r"#\s*define\s+(\w+)", t.text)
                if dm:
                    defined.add(dm.group(1))
                if mac:
                    flush()
                    macros[mac.name] = mac
            elif re.match(r"#\s*undef\b", t.text):
                um = re.match(r"#\s*undef\s+(\w+)", t.text)
                if um:
                    flush()
                    macros.pop(um.group(1), None)
                    defined.discard(um.group(1))
            continue
        if t.kind == "comment" or not active():
            continue
        chunk.append(XTok(t.kind, t.text, t.line, t.col, t.pos, None))
    flush()
    return out, pps, macros


# =============================================================================================== typy
INT_SIZES = {"bool": 1, "char": 1, "schar": 1, "uchar": 1, "short": 2, "ushort": 2, "int": 4, "uint": 4,
             "ll": 8, "ull": 8, "i128": 16, "u128": 16}
FLOAT_SIZES = {"float": 4, "double": 8, "ldouble": 16}
UNSIGNED = {"uchar", "ushort", "uint", "ull", "u128", "bool"}
SEQ_CONTAINERS = {"vector", "deque", "array", "list", "basic_string", "valarray"}
MAP_CONTAINERS = {"map", "umap", "mmap", "ummap"}
SET_CONTAINERS = {"set", "uset", "mset", "umset"}
ADAPTERS = {"queue", "stack", "pq"}
HEAVY = SEQ_CONTAINERS | MAP_CONTAINERS | SET_CONTAINERS | ADAPTERS | {"string"}

STD_TEMPLATES = {
    "vector": "vector", "deque": "deque", "array": "array", "list": "list", "forward_list": "list",
    "map": "map", "unordered_map": "umap", "multimap": "mmap", "unordered_multimap": "ummap",
    "set": "set", "unordered_set": "uset", "multiset": "mset", "unordered_multiset": "umset",
    "queue": "queue", "stack": "stack", "priority_queue": "pq", "pair": "pair", "tuple": "tuple",
    "bitset": "bitset", "complex": "complex", "function": "function", "optional": "optional",
    "basic_string": "string", "valarray": "valarray", "gp_hash_table": "umap", "cc_hash_table": "umap",
    "tree": "set", "greater": "functor", "less": "functor", "greater_equal": "functor", "less_equal": "functor",
    "hash": "functor", "numeric_limits": "limits", "initializer_list": "vector", "span": "vector",
    "shared_ptr": "smartptr", "unique_ptr": "smartptr", "atomic": "atomic", "reference_wrapper": "unknown",
}
SIMPLE_TYPES = {
    "string": "string", "wstring": "string", "string_view": "string", "size_t": "ull", "ssize_t": "ll",
    "ptrdiff_t": "ll", "int64_t": "ll", "uint64_t": "ull", "int32_t": "int", "uint32_t": "uint",
    "int16_t": "short", "uint16_t": "ushort", "int8_t": "schar", "uint8_t": "uchar", "intmax_t": "ll",
    "uintmax_t": "ull", "__int128": "i128", "__int128_t": "i128", "__uint128_t": "u128", "clock_t": "ll",
    "time_t": "ll", "istream": "stream", "ostream": "stream", "iostream": "stream", "ifstream": "stream",
    "ofstream": "stream", "fstream": "stream", "stringstream": "stream", "istringstream": "stream",
    "ostringstream": "stream", "mt19937": "rng", "mt19937_64": "rng", "random_device": "rng",
    "default_random_engine": "rng", "FILE": "file", "uint_fast64_t": "ull", "int_fast64_t": "ll",
    "uint_fast32_t": "uint", "int_fast32_t": "int", "u64": "ull", "i64": "ll", "u32": "uint", "i32": "int",
    "ios_base": "unknown", "ios": "unknown", "regex": "unknown", "smatch": "unknown", "any": "unknown",
}
TEMPLATE_CALLABLES = {
    "static_cast", "reinterpret_cast", "const_cast", "dynamic_cast", "max", "min", "make_pair", "make_tuple",
    "get", "accumulate", "swap", "abs", "bit_cast", "numeric_limits", "max_element", "min_element", "sort",
    "lower_bound", "upper_bound", "gcd", "lcm", "clamp", "uniform_int_distribution", "uniform_real_distribution",
    "is_same", "is_same_v", "enable_if", "conditional", "decay_t", "remove_reference_t", "chrono", "duration_cast",
    "milliseconds", "tie", "as_const", "declval", "iota", "fill", "exchange", "minmax",
}
STREAMS = {"cin": "stream", "cout": "stream", "cerr": "stream", "clog": "stream", "wcin": "stream",
           "wcout": "stream", "fin": "stream", "fout": "stream", "stdin": "file", "stdout": "file", "stderr": "file"}
CONSTANTS = {
    "INT_MAX": ("int", INT_MAX), "INT_MIN": ("int", INT_MIN), "LLONG_MAX": ("ll", LL_MAX),
    "LLONG_MIN": ("ll", -LL_MAX - 1), "LONG_LONG_MAX": ("ll", LL_MAX), "UINT_MAX": ("uint", 2 ** 32 - 1),
    "ULLONG_MAX": ("ull", 2 ** 64 - 1), "SHRT_MAX": ("int", 32767), "CHAR_MAX": ("int", 127),
    "LONG_MAX": ("ll", LL_MAX), "LONG_MIN": ("ll", -LL_MAX - 1), "INT32_MAX": ("int", INT_MAX),
    "INT64_MAX": ("ll", LL_MAX), "INT32_MIN": ("int", INT_MIN), "EOF": ("int", -1), "NULL": ("ll", 0),
    "M_PI": ("double", math.pi), "RAND_MAX": ("int", INT_MAX), "DBL_MAX": ("double", 1.7e308),
    "true": ("bool", 1), "false": ("bool", 0), "nullptr": ("ptr", 0),
}


@dataclass
class CType:
    base: str = "unknown"
    args: list = field(default_factory=list)
    ptr: int = 0
    ref: bool = False
    const: bool = False
    dims: list = field(default_factory=list)       # wymiary tablicy C (Node | None), od zewnętrznego
    text: str = ""
    num: int | None = None                         # np. array<int, 5> albo bitset<N>

    # --- predykaty
    def scalar(self) -> bool:
        return not self.dims and self.ptr == 0

    def is_int(self) -> bool:
        return self.base in INT_SIZES and self.scalar()

    def is_float(self) -> bool:
        return self.base in FLOAT_SIZES and self.scalar()

    def is_arith(self) -> bool:
        return self.is_int() or self.is_float()

    def is_wide(self) -> bool:
        return self.scalar() and self.base in ("ll", "ull", "i128", "u128")

    def is_int32(self) -> bool:
        """Po promocji całkowitej: int albo unsigned (32 bity)."""
        return self.scalar() and self.base in ("bool", "char", "schar", "uchar", "short", "ushort", "int", "uint")

    def is_unsigned(self) -> bool:
        return self.scalar() and self.base in UNSIGNED and self.base != "bool"

    def is_container(self) -> bool:
        return self.scalar() and self.base in HEAVY

    def is_array(self) -> bool:
        return bool(self.dims)

    def known(self) -> bool:
        return self.base not in ("unknown", "auto")

    def elem(self) -> "CType":
        if self.dims:
            return CType(self.base, self.args, self.ptr, False, self.const, self.dims[1:], self.text, self.num)
        if self.ptr:
            return CType(self.base, self.args, self.ptr - 1, False, self.const, [], self.text, self.num)
        if self.base in SEQ_CONTAINERS or self.base in ("valarray",):
            return _copy(self.args[0]) if self.args else CType()
        if self.base in ("map", "umap"):
            return _copy(self.args[1]) if len(self.args) > 1 else CType()
        if self.base == "string":
            return CType("char")
        if self.base == "bitset":
            return CType("bool")
        if self.base == "smartptr":
            return _copy(self.args[0]) if self.args else CType()
        return CType()

    def deref(self) -> "CType":
        if self.ptr:
            return self.elem()
        if self.base == "iter" and self.args:
            c = self.args[0]
            if c.base in ("map", "umap", "mmap", "ummap") and len(c.args) > 1:
                return CType("pair", [c.args[0], c.args[1]])
            if c.base in SET_CONTAINERS or c.base in ("pq",):
                return _copy(c.args[0]) if c.args else CType()
            return c.elem()
        return CType()

    def value_type(self) -> "CType":
        """Typ elementu przy iteracji (range-for)."""
        if self.dims:
            return self.elem()
        if self.base in ("map", "umap", "mmap", "ummap"):
            return CType("pair", list(self.args[:2]))
        if self.base in SET_CONTAINERS or self.base in ("pq", "queue", "stack", "list"):
            return _copy(self.args[0]) if self.args else CType()
        return self.elem()

    def sizeof(self) -> int | None:
        if self.ptr:
            return 8
        b = self.base
        if b in INT_SIZES:
            return INT_SIZES[b]
        if b in FLOAT_SIZES:
            return FLOAT_SIZES[b]
        if b == "pair" and len(self.args) == 2:
            x, y = self.args[0].sizeof(), self.args[1].sizeof()
            if x and y:
                return max(x, y) * 2
        if b in ("vector", "string", "deque"):
            return 24
        if b in ("map", "set", "umap", "uset", "mset", "mmap"):
            return 48
        if b == "array" and self.args and self.num:
            s = self.args[0].sizeof()
            return s * self.num if s else None
        if b == "bitset" and self.num:
            return max(8, self.num // 8)
        return None

    def __str__(self) -> str:
        return self.text or self.base


def _copy(t: CType) -> CType:
    return CType(t.base, list(t.args), t.ptr, t.ref, t.const, list(t.dims), t.text, t.num)


_BASE_NAMES = {"int": "int", "uint": "unsigned int", "ll": "long long", "ull": "unsigned long long",
               "i128": "__int128", "u128": "unsigned __int128", "double": "double", "float": "float",
               "ldouble": "long double", "char": "char", "bool": "bool", "short": "short",
               "ushort": "unsigned short", "schar": "signed char", "uchar": "unsigned char"}


def tname(t: CType | None) -> str:
    if t is None:
        return "?"
    if t.text:
        return t.text
    return _BASE_NAMES.get(t.base, t.base)


def _int_words_to_base(words: list[str]) -> str | None:
    w = [x for x in words if x not in ("const", "volatile", "int") or x == "int"]
    unsigned = "unsigned" in w
    longs = w.count("long")
    if "double" in w:
        return "ldouble" if longs else "double"
    if "float" in w:
        return "float"
    if "bool" in w:
        return "bool"
    if "char" in w:
        return "uchar" if unsigned else ("schar" if "signed" in w else "char")
    if "short" in w:
        return "ushort" if unsigned else "short"
    if "__int128" in w:
        return "u128" if unsigned else "i128"
    if longs >= 1:
        return "ull" if unsigned else "ll"        # long = 64 bity na Linuksie (OI)
    if unsigned:
        return "uint"
    if "int" in w or "signed" in w:
        return "int"
    if "void" in w:
        return "void"
    if "auto" in w:
        return "auto"
    if "wchar_t" in w or "char16_t" in w or "char32_t" in w or "char8_t" in w:
        return "int"
    return None


def common_type(a: CType | None, b: CType | None) -> CType:
    """Zwykłe konwersje arytmetyczne C++ (uproszczone)."""
    if a is None or b is None or not a.is_arith() or not b.is_arith():
        if a is not None and a.is_arith() and (b is None or not b.known()):
            return CType("unknown")
        return CType("unknown")
    if a.is_float() or b.is_float():
        ranks = {"float": 1, "double": 2, "ldouble": 3}
        fa = ranks.get(a.base, 0)
        fb = ranks.get(b.base, 0)
        return CType(a.base if fa >= fb else b.base)
    ra, rb = _irank(a.base), _irank(b.base)
    pa, pb = _promote(a.base), _promote(b.base)
    if pa == pb:
        return CType(pa)
    ua, ub = pa in UNSIGNED, pb in UNSIGNED
    hi, lo = (pa, pb) if _irank(pa) >= _irank(pb) else (pb, pa)
    if _irank(pa) == _irank(pb):
        return CType(pa if ua else pb)
    if hi in UNSIGNED:
        return CType(hi)
    # hi signed, wyższa ranga — mieści lo (ll vs uint) → hi
    del ra, rb, ua, ub, lo
    return CType(hi)


def _promote(b: str) -> str:
    return "int" if b in ("bool", "char", "schar", "uchar", "short", "ushort") else b


def _irank(b: str) -> int:
    return {"bool": 0, "char": 1, "schar": 1, "uchar": 1, "short": 2, "ushort": 2, "int": 3, "uint": 3,
            "ll": 4, "ull": 4, "i128": 5, "u128": 5}.get(b, 3)


# =============================================================================================== liczby
def parse_number(text: str):
    """(wartość, baza_typu) dla literału liczbowego."""
    s = text.replace("'", "")
    low = s.lower()
    is_hex = low.startswith("0x")
    suffix = ""
    while low and low[-1] in "ulfz" and not (is_hex and low[-1] == "f"):
        suffix = low[-1] + suffix
        low = low[:-1]
        if not is_hex and low.endswith("e"):
            break
    body = low
    try:
        if is_hex and ("." in body or "p" in body):
            return float.fromhex(body), "double"
        if is_hex:
            v = int(body, 16)
        elif body.startswith("0b"):
            v = int(body[2:], 2)
        elif re.fullmatch(r"\d+", body):
            v = int(body, 8) if len(body) > 1 and body.startswith("0") and re.fullmatch(r"[0-7]+", body) else int(body)
        else:
            f = float(body)
            return f, ("float" if "f" in suffix else ("ldouble" if "l" in suffix else "double"))
    except ValueError:
        return None, "int"
    if "u" in suffix:
        base = "ull" if ("l" in suffix or v > 2 ** 32 - 1) else "uint"
    elif "l" in suffix:
        base = "ll"
    else:
        base = "int" if v <= INT_MAX else ("ll" if v <= LL_MAX else "ull")
    return v, base


# =============================================================================================== model
@dataclass(eq=False)
class Var:
    name: str
    type: CType
    idx: int                   # indeks tokena nazwy
    line: int
    col: int
    scope_s: int
    scope_e: int
    kind: str                  # global | local | param | field
    func: "Func | None" = None
    has_init: bool = False
    init: tuple | None = None  # (s, e) wyrażenia inicjalizującego
    init_kind: str | None = None   # '=' | '()' | '{}' | 'range'
    ctor: list = field(default_factory=list)   # [(s, e)] argumenty konstruktora
    is_static: bool = False
    is_const: bool = False
    loop: "Loop | None" = None
    structured: bool = False
    uses: list = field(default_factory=list)
    vid: int = 0
    stmt_s: int = 0


@dataclass(eq=False)
class Func:
    name: str
    ret: CType | None
    params: list
    name_idx: int
    body_s: int                # indeks '{'
    body_e: int                # indeks '}'
    line: int
    owner: str | None = None
    is_lambda: bool = False
    end_line: int = 0
    calls: set = field(default_factory=set)
    hdr_s: int = 0


@dataclass(eq=False)
class Loop:
    kind: str                  # for | while | do | range
    kw: int
    hdr: tuple                 # (indeks '(', indeks ')')
    body: tuple                # (s, e) włącznie
    init: tuple | None = None
    cond: tuple | None = None
    incr: tuple | None = None
    var: str | None = None
    var_obj: Var | None = None
    start: "Node | None" = None
    cmp: str | None = None
    bound: "Node | None" = None
    step: int | None = None
    range_expr: tuple | None = None
    func: Func | None = None


@dataclass(eq=False)
class Site:
    s: int
    e: int
    kind: str                  # init | expr | cond | return | incr | forcond | arg
    dest: CType | None = None
    var: Var | None = None
    func: Func | None = None
    stmt: str = ""
    node: "Node | None" = None


@dataclass(eq=False)
class Node:
    kind: str
    op: str = ""
    kids: list = field(default_factory=list)
    s: int = 0
    e: int = 0
    type: CType = field(default_factory=CType)
    value: object = None
    name: str = ""
    var: Var | None = None
    paren: bool = False
    parent: "Node | None" = None
    targs: list = field(default_factory=list)    # argumenty szablonu (CType)
    qual: str = ""

    def walk(self):
        yield self
        for k in self.kids:
            if k is not None:
                yield from k.walk()


BIN_PREC = {
    "=": 2, "+=": 2, "-=": 2, "*=": 2, "/=": 2, "%=": 2, "<<=": 2, ">>=": 2, "&=": 2, "^=": 2, "|=": 2,
    "?": 3, "||": 4, "or": 4, "&&": 5, "and": 5, "|": 6, "bitor": 6, "^": 7, "xor": 7, "&": 8, "bitand": 8,
    "==": 9, "!=": 9, "not_eq": 9, "<": 10, ">": 10, "<=": 10, ">=": 10, "<=>": 11, "<<": 12, ">>": 12,
    "+": 13, "-": 13, "*": 14, "/": 14, "%": 14, ".*": 15, "->*": 15,
}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "^=", "|="}
CMP_OPS = {"==", "!=", "<", ">", "<=", ">=", "not_eq"}
ALIAS_OPS = {"or": "||", "and": "&&", "bitor": "|", "xor": "^", "bitand": "&", "not_eq": "!="}

DOUBLE_FUNCS = {"sqrt", "pow", "log", "log2", "log10", "exp", "sin", "cos", "tan", "atan", "atan2", "asin", "acos",
                "floor", "ceil", "round", "trunc", "fabs", "hypot", "cbrt", "sqrtl", "powl", "logl", "fmod",
                "sinh", "cosh", "tanh", "expl", "fabsl", "floorl", "ceill", "roundl", "erf", "tgamma", "lgamma",
                "atof", "stod", "stold", "strtod", "exp2", "log1p", "expm1", "fmin", "fmax"}
INT_FUNCS = {"__builtin_popcount": "int", "__builtin_popcountll": "int", "__builtin_clz": "int",
             "__builtin_clzll": "int", "__builtin_ctz": "int", "__builtin_ctzll": "int", "__lg": "int",
             "__builtin_ffs": "int", "__builtin_parity": "int", "stoi": "int", "atoi": "int", "getchar": "int",
             "getchar_unlocked": "int", "stoll": "ll", "atoll": "ll", "stoull": "ull", "stol": "ll",
             "strlen": "ull", "llabs": "ll", "llround": "ll", "lround": "ll", "rand": "int", "putchar": "int",
             "printf": "int", "scanf": "int", "popcount": "int", "countl_zero": "int", "countr_zero": "int",
             "bit_width": "int", "isdigit": "int", "isalpha": "int", "islower": "int", "isupper": "int",
             "tolower": "int", "toupper": "int", "isspace": "int", "isalnum": "int", "clock": "ll", "time": "ll",
             "distance": "ll", "strcmp": "int", "memcmp": "int", "__builtin_popcountl": "int"}
SIZE_METHODS = {"size", "length", "count", "capacity", "max_size", "find_first_of", "rfind", "npos"}


class Model:
    """Model programu na tokenach: zmienne, zasięgi, funkcje, pętle, miejsca wyrażeń."""

    def __init__(self, code: str):
        self.code = code.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = self.code.split("\n")
        toks, self.pps, self.macros = preprocess(self.code)
        self.raw = cpp_lexer.tokenize(self.code)
        self.aliases: dict[str, CType] = {}
        self.structs: dict[str, dict] = {}      # nazwa -> {pole: CType}
        self.template_names: set[str] = set(STD_TEMPLATES) | TEMPLATE_CALLABLES
        self._pre_scan_types(toks)
        self.T = self._split_shifts(toks)
        self.pairs = cpp_lexer.match_brackets(self.T)
        self.n = len(self.T)
        self.vars: list[Var] = []
        self.by_name: dict[str, list[Var]] = {}
        self.funcs: list[Func] = []
        self.func_by_name: dict[str, list[Func]] = {}
        self.protos: dict[str, CType] = {}
        self.loops: list[Loop] = []
        self.sites: list[Site] = []
        self.conds: list[tuple] = []            # (kw_idx, s, e, kw)
        self.cases: list[int] = []
        self.switches: list[tuple] = []         # (kw, body_s, body_e)
        self.decl_names: set[int] = set()
        self.empty_bodies: list[tuple] = []     # (kw_idx, semicolon_idx, kw)
        self.lambdas: list[Func] = []
        self.blocks: list[tuple] = []           # (open, close)
        self.consts: dict[str, object] = {}
        self.uses_ns_std = bool(re.search(r"using\s+namespace\s+std\s*;", self.code))
        self.includes = cpp_lexer.includes(self.code)
        self._collect_macro_consts()
        self._walk_block(0, self.n, "global", None)
        self._resolve_uses()
        self._parse_sites()

    # ------------------------------------------------------------------ wstępne skanowanie typów
    def _pre_scan_types(self, toks):
        n = len(toks)
        for i, t in enumerate(toks):
            if t.kind == "kw" and t.text in ("struct", "class", "union") and i + 1 < n and toks[i + 1].kind == "id":
                prev = toks[i - 1].text if i else ""
                if prev in ("<", ","):
                    continue
                self.structs.setdefault(toks[i + 1].text, {})
                j = i - 1
                if j >= 0 and toks[j].text == ">":
                    k = j
                    while k >= 0 and toks[k].text != "template":
                        k -= 1
                    if k >= 0:
                        self.template_names.add(toks[i + 1].text)
            if t.kind == "kw" and t.text == "template":
                # template<class T> -> T jest typem
                j = i + 1
                if j < n and toks[j].text == "<":
                    k = j + 1
                    while k < n and toks[k].text not in (">", "{", ";"):
                        if toks[k].text in ("class", "typename") and k + 1 < n and toks[k + 1].kind == "id":
                            self.structs.setdefault(toks[k + 1].text, {})
                        k += 1
            if t.kind == "kw" and t.text == "enum":
                j = i + 1
                if j < n and toks[j].text in ("class", "struct"):
                    j += 1
                if j < n and toks[j].kind == "id":
                    self.aliases[toks[j].text] = CType("int", text=toks[j].text)

    def _split_shifts(self, toks):
        """Dzieli '>>' na dwa '>' wewnątrz argumentów szablonów (vector<vector<int>>)."""
        out = []
        depth = 0
        n = len(toks)
        for i, t in enumerate(toks):
            prev = toks[i - 1] if i else None
            if t.text == "<" and prev is not None and prev.kind == "id" and (
                    prev.text in self.template_names or prev.text in self.structs and prev.text in self.template_names):
                depth += 1
                out.append(t)
                continue
            if depth > 0:
                if t.text == ">":
                    depth -= 1
                elif t.text == ">>":
                    out.append(_xt(t, ">", "op"))
                    out.append(_xt(t, ">", "op", col_shift=1))
                    depth = max(0, depth - 2)
                    continue
                elif t.text == ">>=":
                    out.append(_xt(t, ">", "op"))
                    out.append(_xt(t, ">=", "op", col_shift=1))
                    depth = max(0, depth - 1)
                    continue
                elif t.text in (";", "{", "}"):
                    depth = 0
            out.append(t)
        del n
        return out

    def _collect_macro_consts(self):
        for name, mac in self.macros.items():
            if mac.params is None and mac.body:
                v = _const_eval_tokens(mac.body, self.consts)
                if v is not None:
                    self.consts[name] = v

    # ------------------------------------------------------------------ pomocnicze
    def tok(self, i):
        return self.T[i] if 0 <= i < self.n else None

    def text(self, i):
        return self.T[i].text if 0 <= i < self.n else ""

    def src(self, s, e) -> str:
        """Tekst tokenów [s, e) połączony spacjami (czytelny)."""
        out = []
        for k in range(max(0, s), min(self.n, e)):
            t = self.T[k]
            if out and (t.text in (",", ";", ")", "]", ".", "->", "(", "[", "++", "--", "::")
                        or out[-1] in ("(", "[", ".", "->", "::", "!", "~")):
                out.append(t.text)
            else:
                out.append((" " if out else "") + t.text)
        return "".join(out).strip()

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 1 <= line <= len(self.lines) else ""

    def match_angle(self, i: int) -> int | None:
        """Indeks '>' zamykającego '<' na pozycji i (po rozbiciu '>>')."""
        depth = 0
        j = i
        while j < self.n:
            t = self.T[j].text
            if t == "<":
                depth += 1
            elif t == ">":
                depth -= 1
                if depth == 0:
                    return j
            elif t in (";", "{", "}") or (t in (")", "]") and depth <= 1 and j not in self.pairs):
                return None
            elif t in ("(", "[") and j in self.pairs:
                j = self.pairs[j]
            elif t in ("&&", "||"):
                return None
            j += 1
        return None

    def stmt_end(self, i: int, limit: int | None = None) -> int:
        """Indeks ostatniego tokena instrukcji zaczynającej się na i."""
        limit = self.n if limit is None else limit
        if i >= limit:
            return limit - 1
        t = self.T[i]
        if t.text == "{" and i in self.pairs:
            return self.pairs[i]
        if t.kind == "kw":
            if t.text in ("for", "while", "switch") and i + 1 < limit and self.text(i + 1) == "(" and i + 1 in self.pairs:
                close = self.pairs[i + 1]
                if t.text == "while" and close + 1 < limit and self.text(close + 1) == ";":
                    return close + 1
                return self.stmt_end(close + 1, limit)
            if t.text == "if":
                j = i + 1
                if self.text(j) == "constexpr":
                    j += 1
                if self.text(j) == "(" and j in self.pairs:
                    e = self.stmt_end(self.pairs[j] + 1, limit)
                    if e + 1 < limit and self.text(e + 1) == "else":
                        return self.stmt_end(e + 2, limit)
                    return e
            if t.text == "do":
                e = self.stmt_end(i + 1, limit)
                j = e + 1
                if self.text(j) == "while" and self.text(j + 1) == "(" and j + 1 in self.pairs:
                    k = self.pairs[j + 1] + 1
                    return k if self.text(k) == ";" else k - 1
                return e
            if t.text == "else":
                return self.stmt_end(i + 1, limit)
            if t.text == "try":
                e = self.stmt_end(i + 1, limit)
                while self.text(e + 1) == "catch" and self.text(e + 2) == "(" and e + 2 in self.pairs:
                    e = self.stmt_end(self.pairs[e + 2] + 1, limit)
                return e
            if t.text in ("case", "default"):
                j = i + 1
                while j < limit and not (self.text(j) == ":" ):
                    j += 1
                return j
        j = i
        while j < limit:
            x = self.T[j]
            if x.kind == "punct":
                if x.text == ";":
                    return j
                if x.text in "([{" and j in self.pairs:
                    j = self.pairs[j] + 1
                    continue
                if x.text == "}":
                    return j - 1
            j += 1
        return limit - 1

    # ------------------------------------------------------------------ parsowanie typów
    def parse_type(self, i: int, limit: int | None = None, allow_unknown: bool = False):
        """(CType, indeks_po_typie) albo None."""
        limit = self.n if limit is None else limit
        T = self.T
        j = i
        const = False
        static = False
        while j < limit and T[j].text in ("const", "volatile", "constexpr", "static", "inline", "extern", "register",
                                          "mutable", "thread_local", "constinit", "typename", "struct", "class",
                                          "enum", "union", "unsigned", "signed"):
            if T[j].text in ("unsigned", "signed"):
                break
            if T[j].text in ("const", "constexpr", "constinit"):
                const = True
            if T[j].text == "static":
                static = True
            if T[j].text in ("struct", "class", "enum", "union") and not (j + 1 < limit and T[j + 1].kind == "id"):
                return None
            j += 1
        if j >= limit:
            return None
        if T[j].text == "std" and j + 1 < limit and T[j + 1].text == "::":
            j += 2
        elif T[j].text == "::" and j + 1 < limit and T[j + 1].kind == "id":
            j += 1
        if j >= limit:
            return None
        t = T[j]
        ct: CType | None = None
        if t.kind == "kw" and t.text in ("int", "long", "short", "signed", "unsigned", "char", "bool", "double",
                                         "float", "void", "auto", "wchar_t", "char8_t", "char16_t", "char32_t"):
            words = []
            start = j
            while j < limit and ((T[j].kind == "kw" and T[j].text in (
                    "int", "long", "short", "signed", "unsigned", "char", "bool", "double", "float", "void", "auto",
                    "wchar_t", "char8_t", "char16_t", "char32_t", "const", "volatile")) or T[j].text == "__int128"):
                if T[j].text == "const":
                    const = True
                words.append(T[j].text)
                j += 1
            base = _int_words_to_base(words)
            if base is None:
                return None
            ct = CType(base, text=" ".join(w for w in words if w not in ("const", "volatile")))
            del start
        elif t.kind == "id":
            name = t.text
            if name in self.aliases:
                ct = _copy(self.aliases[name])
                j += 1
            elif name in SIMPLE_TYPES:
                ct = CType(SIMPLE_TYPES[name], text=name)
                j += 1
                if name == "__int128" and j < limit and T[j].text == "unsigned":
                    j += 1
            elif name in STD_TEMPLATES and j + 1 < limit and T[j + 1].text == "<":
                close = self.match_angle(j + 1)
                if close is None or close >= limit:
                    return None
                args, num = self._template_args(j + 2, close)
                ct = CType(STD_TEMPLATES[name], args=args, text=self.src(j, close + 1), num=num)
                if name == "basic_string":
                    ct.base = "string"
                j = close + 1
            elif name in ("string", "bitset"):
                ct = CType(SIMPLE_TYPES.get(name, name), text=name)
                j += 1
            elif name in self.structs:
                ct = CType("struct", text=name)
                j += 1
                if j < limit and T[j].text == "<":
                    close = self.match_angle(j)
                    if close is not None:
                        ct.text = self.src(j - 1, close + 1)
                        j = close + 1
            elif allow_unknown:
                ct = CType("unknown", text=name)
                j += 1
                if j < limit and T[j].text == "<" and name[:1].isupper():
                    close = self.match_angle(j)
                    if close is not None:
                        j = close + 1
            else:
                return None
            # Typ::iterator, Typ::value_type
            while j + 1 < limit and T[j].text == "::" and T[j + 1].kind == "id":
                sub = T[j + 1].text
                if sub in ("iterator", "const_iterator", "reverse_iterator", "const_reverse_iterator"):
                    ct = CType("iter", args=[ct], text=ct.text + "::" + sub)
                elif sub == "size_type":
                    ct = CType("ull", text="size_t")
                elif sub == "value_type":
                    ct = ct.value_type()
                else:
                    ct = CType("unknown", text=ct.text + "::" + sub)
                j += 2
        else:
            return None
        while j < limit and T[j].text in ("const", "volatile"):
            const = True
            j += 1
        ct.const = const or ct.const
        if static:
            ct.text = ct.text
        return ct, j

    def _template_args(self, s: int, e: int):
        args, num = [], None
        depth = 0
        start = s
        parts = []
        for k in range(s, e + 1):
            t = self.text(k)
            if k == e or (t == "," and depth == 0):
                parts.append((start, k))
                start = k + 1
                continue
            if t in ("<", "(", "["):
                depth += 1
            elif t in (">", ")", "]"):
                depth -= 1
        for a, b in parts:
            if a >= b:
                continue
            r = self.parse_type(a, b, allow_unknown=True)
            if r is not None and r[1] >= b:
                ct, _ = r
                while a < b and self.text(b - 1) in ("*",):
                    ct.ptr += 1
                    b -= 1
                args.append(ct)
            else:
                v = _const_eval_tokens(self.T[a:b], self.consts)
                if isinstance(v, (int, float)) and num is None:
                    num = int(v)
                args.append(CType("unknown", text=self.src(a, b)))
        return args, num

    # ------------------------------------------------------------------ deklaracje zmiennych
    def _add_var(self, v: Var):
        v.vid = len(self.vars)
        self.vars.append(v)
        self.by_name.setdefault(v.name, []).append(v)
        self.decl_names.add(v.idx)

    def lookup(self, name: str, i: int) -> Var | None:
        best = None
        for v in self.by_name.get(name, ()):
            if v.scope_s <= i <= v.scope_e and (v.idx <= i or v.kind in ("field", "param")):
                if best is None or v.scope_s > best.scope_s or (v.scope_s == best.scope_s and v.idx > best.idx):
                    best = v
        return best

    def _try_decl(self, i: int, limit: int, scope_e: int, kind: str, func, loop=None, in_for=False):
        """Próbuje sparsować deklarację zaczynającą się na i. Zwraca indeks po deklaracji (po ';') albo None."""
        T = self.T
        j = i
        is_static = False
        is_const = False
        while j < limit and T[j].text in ("static", "const", "constexpr", "inline", "extern", "register",
                                          "thread_local", "volatile", "mutable", "constinit"):
            if T[j].text == "static":
                is_static = True
            if T[j].text in ("const", "constexpr", "constinit"):
                is_const = True
            j += 1
        if j >= limit:
            return None
        first = T[j]
        if first.kind == "id" and first.text in STREAMS:
            return None
        known_start = first.kind == "kw" or first.text in self.aliases or first.text in SIMPLE_TYPES or \
            first.text in STD_TEMPLATES or first.text in self.structs or first.text in ("std", "::")
        r = self.parse_type(j, limit, allow_unknown=True)
        if r is None:
            return None
        ct, k = r
        if k >= limit:
            return None
        if ct.base == "void" and self.text(k) != "*":
            return None
        if not known_start or ct.base == "unknown":
            # nieznany identyfikator: tylko wzorzec `Typ nazwa (=|;|,|[|(|{)` lub `Typ& nazwa`, `Typ* nazwa =`
            kk = k
            while kk < limit and T[kk].text in ("*", "&", "&&"):
                kk += 1
            if not (kk < limit and T[kk].kind == "id" and kk + 1 < limit and
                    T[kk + 1].text in ("=", ";", ",", "[", "{", "(", ":")):
                return None
            if kk > k and T[kk + 1].text not in ("=", ";", ",", "["):
                return None
            if T[kk + 1].text == "(" and kind != "global":
                pass
        ct.const = ct.const or is_const
        # structured bindings: auto [a, b] = ... / auto& [a, b] : m
        kk = k
        while kk < limit and T[kk].text in ("&", "&&"):
            kk += 1
        if ct.base == "auto" and kk < limit and T[kk].text == "[" and kk in self.pairs:
            close = self.pairs[kk]
            names = [m for m in range(kk + 1, close) if T[m].kind == "id"]
            nxt = close + 1
            init = None
            init_kind = None
            if self.text(nxt) == "=":
                e = self._init_end(nxt + 1, limit)
                init = (nxt + 1, e)
                init_kind = "="
                nxt = e
            elif self.text(nxt) == ":":
                init_kind = "range"
            for m in names:
                self._add_var(Var(T[m].text, CType("unknown"), m, T[m].line, T[m].col, m, scope_e, kind, func,
                                  has_init=True, init=init, init_kind=init_kind, structured=True, loop=loop,
                                  stmt_s=i))
            if self.text(nxt) == ";":
                return nxt + 1
            return nxt
        first_decl = True
        while k < limit:
            dt = _copy(ct)
            while k < limit and T[k].text in ("*", "&", "&&", "const"):
                if T[k].text == "*":
                    dt.ptr += 1
                elif T[k].text in ("&", "&&"):
                    dt.ref = True
                k += 1
            if k >= limit or T[k].kind != "id":
                return None if first_decl else k
            name_idx = k
            k += 1
            dims = []
            while k < limit and T[k].text == "[" and k in self.pairs:
                close = self.pairs[k]
                dims.append((k + 1, close))
                k = close + 1
            v = Var(T[name_idx].text, dt, name_idx, T[name_idx].line, T[name_idx].col, name_idx, scope_e, kind, func,
                    is_static=is_static, is_const=is_const or dt.const, loop=loop, stmt_s=i)
            if dims:
                dt.dims = [self.parse_expr(a, b) if b > a else None for a, b in dims]
            nt = self.text(k)
            if nt == "=":
                e = self._init_end(k + 1, limit)
                v.has_init, v.init, v.init_kind = True, (k + 1, e), "="
                k = e
            elif nt == "(" and k in self.pairs:
                close = self.pairs[k]
                if kind == "global" and self._looks_like_params(k + 1, close):
                    return None
                v.has_init, v.init_kind = True, "()"
                v.ctor = self._split_args(k + 1, close)
                v.init = (k + 1, close)
                k = close + 1
            elif nt == "{" and k in self.pairs:
                close = self.pairs[k]
                v.has_init, v.init_kind = True, "{}"
                v.ctor = self._split_args(k + 1, close)
                v.init = (k + 1, close)
                k = close + 1
            elif nt == ":" and in_for:
                v.has_init, v.init_kind = True, "range"
            elif nt == ":" and kind == "field":
                k = self._init_end(k + 1, limit)    # pole bitowe
            elif nt not in (",", ";", ")"):
                if first_decl:
                    return None
            self._add_var(v)
            if v.has_init and v.init_kind == "=" and v.is_const and dt.is_arith() and not dims:
                val = _const_eval_tokens(T[v.init[0]:v.init[1]], self.consts)
                if val is not None:
                    self.consts[v.name] = val
            if v.init_kind in ("=",) and v.init:
                self.sites.append(Site(v.init[0], v.init[1], "init", dest=dt, var=v, func=func))
            elif v.init_kind in ("()", "{}") and v.ctor and dt.is_arith():
                a, b = v.ctor[0]
                self.sites.append(Site(a, b, "init", dest=dt, var=v, func=func))
            elif v.ctor:
                for a, b in v.ctor:
                    self.sites.append(Site(a, b, "arg", func=func))
            first_decl = False
            if self.text(k) == ",":
                k += 1
                continue
            if self.text(k) == ";":
                return k + 1
            return k
        return k

    def _looks_like_params(self, s: int, e: int) -> bool:
        if s >= e:
            return True
        for a, b in self._split_args(s, e):
            if self.text(a) in ("void", "...") :
                continue
            r = self.parse_type(a, b, allow_unknown=False)
            if r is None:
                return False
        return True

    def _init_end(self, s: int, limit: int) -> int:
        j = s
        depth = 0
        while j < limit:
            t = self.T[j]
            if t.kind == "punct":
                if t.text in "([{" and j in self.pairs:
                    j = self.pairs[j] + 1
                    continue
                if t.text in (",", ";", ")", "}", "]") and depth == 0:
                    return j
            elif t.text == "<" and j > 0 and self.text(j - 1) in self.template_names:
                c = self.match_angle(j)
                if c is not None:
                    j = c + 1
                    continue
            j += 1
        return limit

    def _split_args(self, s: int, e: int) -> list[tuple]:
        out = []
        start = s
        j = s
        while j < e:
            t = self.T[j]
            if t.kind == "punct" and t.text in "([{" and j in self.pairs:
                j = self.pairs[j] + 1
                continue
            if t.text == "<" and j > 0 and self.text(j - 1) in self.template_names:
                c = self.match_angle(j)
                if c is not None and c < e:
                    j = c + 1
                    continue
            if t.text == ",":
                out.append((start, j))
                start = j + 1
            j += 1
        if start < e:
            out.append((start, e))
        return out

    # ------------------------------------------------------------------ funkcje
    def _try_function(self, i: int, limit: int, ctx: str, owner: str | None):
        """Definicja/deklaracja funkcji na poziomie globalnym/struktury. Zwraca indeks po niej albo None."""
        T = self.T
        j = i
        while j < limit and T[j].text in ("static", "inline", "constexpr", "virtual", "explicit", "friend",
                                          "extern", "consteval", "[[nodiscard]]"):
            j += 1
        ret = None
        name_idx = None
        r = self.parse_type(j, limit, allow_unknown=True)
        if r is not None:
            ct, k = r
            while k < limit and T[k].text in ("*", "&", "&&"):
                if T[k].text == "*":
                    ct.ptr += 1
                else:
                    ct.ref = True
                k += 1
            # nazwa (może być kwalifikowana A::f albo operator)
            if k < limit and T[k].kind == "id":
                while k + 2 < limit and T[k + 1].text == "::" and T[k + 2].kind in ("id", "kw"):
                    k += 2
                name_idx = k
                k += 1
                ret = ct
            elif k < limit and T[k].text == "operator":
                name_idx = k
                k += 1
                while k < limit and T[k].text != "(":
                    k += 1
                if k + 1 < limit and T[k].text == "(" and T[k + 1].text == ")" and T[k + 2].text == "(":
                    k += 2       # operator()
                ret = ct
            elif ct.base == "unknown" and owner and ct.text.split("<")[0] == owner and T[k].text == "(":
                name_idx = k - 1          # konstruktor
                ret = None
            else:
                return None
        elif T[j].text == "~" and j + 1 < limit and T[j + 1].kind == "id":
            name_idx = j + 1
            k = j + 2
        elif T[j].text == "operator":
            return None
        else:
            return None
        if k >= limit or T[k].text != "(" or k not in self.pairs:
            return None
        popen, pclose = k, self.pairs[k]
        k = pclose + 1
        while k < limit and (T[k].text in ("const", "noexcept", "override", "final", "&", "&&", "mutable", "volatile")
                             or T[k].text == "throw"):
            if T[k].text in ("noexcept", "throw") and self.text(k + 1) == "(" and k + 1 in self.pairs:
                k = self.pairs[k + 1] + 1
                continue
            k += 1
        if k < limit and T[k].text == "->":
            r2 = self.parse_type(k + 1, limit, allow_unknown=True)
            if r2:
                ret, k = r2
            while k < limit and T[k].text not in ("{", ";"):
                k += 1
        if k < limit and T[k].text == ":" and (owner or ret is None or self.text(name_idx - 1) == "::"):
            k += 1
            while k < limit and T[k].text != "{":
                if T[k].text in ("(",) and k in self.pairs:
                    k = self.pairs[k] + 1
                    continue
                if T[k].kind == "id" and self.text(k + 1) == "{" and k + 1 in self.pairs:
                    k = self.pairs[k + 1] + 1
                    continue
                k += 1
        if k < limit and T[k].text == "=":       # = default / = delete / = 0
            while k < limit and T[k].text != ";":
                k += 1
            return k + 1
        name = T[name_idx].text
        if k < limit and T[k].text == ";":
            if ctx == "global" and not self._looks_like_params(popen + 1, pclose):
                return None
            if ret is not None:
                self.protos[name] = ret
            return k + 1
        if k >= limit or T[k].text != "{" or k not in self.pairs:
            return None
        if ctx != "global" and ctx != "struct":
            return None
        body_s, body_e = k, self.pairs[k]
        f = Func(name, ret, [], name_idx, body_s, body_e, T[name_idx].line, owner=owner,
                 end_line=T[body_e].line, hdr_s=i)
        self._parse_params(f, popen, pclose)
        self.funcs.append(f)
        self.func_by_name.setdefault(name, []).append(f)
        self._walk_block(body_s + 1, body_e, "function", f)
        return body_e + 1

    def _parse_params(self, f: Func, popen: int, pclose: int):
        for a, b in self._split_args(popen + 1, pclose):
            if self.text(a) in ("void", "...") and b - a == 1:
                continue
            r = self.parse_type(a, b, allow_unknown=True)
            if not r:
                continue
            ct, k = r
            while k < b and self.text(k) in ("*", "&", "&&", "const"):
                if self.text(k) == "*":
                    ct.ptr += 1
                elif self.text(k) in ("&", "&&"):
                    ct.ref = True
                k += 1
            if k < b and self.T[k].kind == "id":
                nm = k
                k += 1
                arr = False
                while k < b and self.text(k) == "[" and k in self.pairs:
                    if not arr:
                        ct.ptr += 1             # int a[] w parametrze to wskaźnik
                        arr = True
                    else:
                        c = self.pairs[k]
                        ct.dims.append(self.parse_expr(k + 1, c) if c > k + 1 else None)
                    k = self.pairs[k] + 1
                v = Var(self.T[nm].text, ct, nm, self.T[nm].line, self.T[nm].col, f.body_s, f.body_e, "param", f,
                        has_init=True, init_kind="param", stmt_s=a)
                if arr:
                    v.init_kind = "array_param"
                self._add_var(v)
                f.params.append(v)
            elif k < b and self.text(k) == "(":
                continue

    def _try_lambda(self, i: int, limit: int, func):
        """'[' na pozycji i rozpoczyna lambdę? Zwraca indeks po jej ciele albo None."""
        if self.text(i) != "[" or i not in self.pairs:
            return None
        prev = self.text(i - 1) if i else ""
        if not (prev in ("=", "(", ",", "{", ";", "return", "}", ":", "?", "&&", "||", "<<") or i == 0):
            return None
        close = self.pairs[i]
        k = close + 1
        popen = pclose = None
        if self.text(k) == "(" and k in self.pairs:
            popen, pclose = k, self.pairs[k]
            k = pclose + 1
        while k < limit and self.text(k) in ("mutable", "constexpr", "noexcept", "static"):
            k += 1
        ret = None
        if self.text(k) == "->":
            r = self.parse_type(k + 1, limit, allow_unknown=True)
            if r:
                ret, k = r
            while k < limit and self.text(k) != "{":
                k += 1
        if self.text(k) != "{" or k not in self.pairs:
            return None
        body_s, body_e = k, self.pairs[k]
        f = Func("<lambda>", ret or CType("auto"), [], i, body_s, body_e, self.T[i].line, is_lambda=True,
                 end_line=self.T[body_e].line, hdr_s=i)
        if popen is not None:
            self._parse_params(f, popen, pclose)
        self.lambdas.append(f)
        self._walk_block(body_s + 1, body_e, "function", f)
        return body_e + 1

    def _scan_lambdas(self, s: int, e: int, func):
        j = s
        while j < e:
            if self.text(j) == "[":
                r = self._try_lambda(j, e, func)
                if r:
                    j = r
                    continue
            j += 1

    # ------------------------------------------------------------------ przejście po blokach
    def _walk_block(self, s: int, e: int, ctx: str, func, owner: str | None = None):
        if ctx != "global" and s > 0 and self.text(s - 1) == "{":
            self.blocks.append((s - 1, e))
        i = s
        guard = 0
        while i < e:
            guard += 1
            if guard > 200000:
                break
            nxt = self._walk_stmt(i, e, ctx, func, owner)
            i = nxt if nxt > i else i + 1

    def _walk_stmt(self, i: int, e: int, ctx: str, func, owner=None) -> int:
        T = self.T
        t = T[i]
        tx = t.text
        if tx == ";":
            return i + 1
        if tx == "{" and i in self.pairs:
            close = self.pairs[i]
            self._walk_block(i + 1, close, "block" if ctx != "global" else "global", func, owner)
            return close + 1
        if tx == "}":
            return i + 1
        if tx == "template" and self.text(i + 1) == "<":
            c = self.match_angle(i + 1)
            return (c + 1) if c is not None else i + 1
        if tx in ("public", "private", "protected") and self.text(i + 1) == ":":
            return i + 2
        if tx in ("struct", "class", "union") and t.kind == "kw":
            j = i + 1
            name = self.text(j) if self.T[j].kind == "id" else None
            while j < e and self.text(j) not in ("{", ";"):
                if self.text(j) == "(":
                    return self._expr_stmt(i, e, ctx, func)
                j += 1
            if j < e and self.text(j) == "{" and j in self.pairs:
                close = self.pairs[j]
                if name:
                    fields = self.structs.setdefault(name, {})
                    nv = len(self.vars)
                    self._walk_block(j + 1, close, "struct", func, owner=name)
                    for v in self.vars[nv:]:
                        if v.kind == "field":
                            fields[v.name] = v.type
                else:
                    self._walk_block(j + 1, close, "struct", func, owner="(anon)")
                k = close + 1
                if self.text(k) != ";" and name:
                    self.aliases.setdefault(name, CType("struct", text=name))
                    r = self._decl_after_struct(k, e, name, ctx, func)
                    return r
                return k + 1 if self.text(k) == ";" else k
            if self.text(j) == ";" and name and ctx != "struct":
                return j + 1
        if tx == "enum":
            j = i
            while j < e and self.text(j) not in ("{", ";"):
                j += 1
            if self.text(j) == "{" and j in self.pairs:
                close = self.pairs[j]
                val = -1
                for a, b in self._split_args(j + 1, close):
                    if T[a].kind == "id":
                        if b - a > 2 and self.text(a + 1) == "=":
                            v = _const_eval_tokens(T[a + 2:b], self.consts)
                            val = v if isinstance(v, int) else val + 1
                        else:
                            val += 1
                        self.consts[T[a].text] = val
                k = close + 1
                while k < e and self.text(k) != ";":
                    k += 1
                return k + 1
            return j + 1
        if tx == "namespace":
            j = i + 1
            while j < e and self.text(j) not in ("{", ";"):
                j += 1
            if self.text(j) == "{" and j in self.pairs:
                close = self.pairs[j]
                self._walk_block(j + 1, close, "global", func)
                return close + 1
            return j + 1
        if tx == "using":
            j = i + 1
            if self.text(j) == "namespace":
                return self.stmt_end(i, e) + 1
            if T[j].kind == "id" and self.text(j + 1) == "=":
                r = self.parse_type(j + 2, e, allow_unknown=True)
                if r:
                    self.aliases[T[j].text] = r[0]
                    if r[0].base in STD_TEMPLATES.values():
                        pass
            return self.stmt_end(i, e) + 1
        if tx == "typedef":
            end = self.stmt_end(i, e)
            r = self.parse_type(i + 1, end, allow_unknown=True)
            if r:
                ct, k = r
                while k < end and self.text(k) in ("*", "&"):
                    ct.ptr += self.text(k) == "*"
                    k += 1
                if k < end and T[k].kind == "id":
                    nm = T[k].text
                    if self.text(k + 1) == "[" and k + 1 in self.pairs:
                        ct.dims = [self.parse_expr(k + 2, self.pairs[k + 1])]
                    ct.text = ct.text or nm
                    self.aliases[nm] = ct
            return end + 1
        if tx == "static_assert":
            return self.stmt_end(i, e) + 1
        if ctx in ("global", "struct"):
            r = self._try_function(i, e, ctx, owner)
            if r is not None:
                return r
            scope_e = e if ctx == "struct" else self.n
            kind = "field" if ctx == "struct" else "global"
            s0 = (i if ctx != "struct" else (self._struct_open(i)))
            r = self._try_decl(i, e, scope_e, kind, func)
            if r is not None:
                if ctx == "struct":
                    for v in self.vars:
                        if v.kind == "field" and v.idx >= i and v.idx < r:
                            v.scope_s = s0
                self._scan_lambdas(i, r, func)
                return r
            end = self.stmt_end(i, e)
            return end + 1
        # --- wewnątrz funkcji
        if t.kind == "kw":
            if tx == "if":
                j = i + 1
                if self.text(j) == "constexpr":
                    j += 1
                if self.text(j) == "(" and j in self.pairs:
                    close = self.pairs[j]
                    self.conds.append((i, j + 1, close, "if"))
                    semi = self._find_top(j + 1, close, ";")
                    if semi is not None:
                        self._try_decl(j + 1, semi + 1, self.stmt_end(i, e), "local", func)
                        self.sites.append(Site(semi + 1, close, "cond", func=func, stmt="if"))
                    else:
                        if self._try_decl(j + 1, close, self.stmt_end(i, e), "local", func) is None:
                            self.sites.append(Site(j + 1, close, "cond", func=func, stmt="if"))
                    self._scan_lambdas(j + 1, close, func)
                    body = close + 1
                    if self.text(body) == ";":
                        self.empty_bodies.append((i, body, "if"))
                    be = self._walk_sub(body, e, func)
                    if self.text(be + 1) == "else":
                        be2 = self._walk_sub(be + 2, e, func)
                        return be2 + 1
                    return be + 1
            if tx in ("while", "switch") and self.text(i + 1) == "(" and i + 1 in self.pairs:
                close = self.pairs[i + 1]
                self.conds.append((i, i + 2, close, tx))
                if self._try_decl(i + 2, close, self.stmt_end(i, e), "local", func) is None:
                    self.sites.append(Site(i + 2, close, "cond", func=func, stmt=tx))
                self._scan_lambdas(i + 2, close, func)
                body = close + 1
                if tx == "while":
                    if self.text(body) == ";":
                        self.empty_bodies.append((i, body, "while"))
                    be = self.stmt_end(body, e)
                    lp = Loop("while", i, (i + 1, close), (body, be), cond=(i + 2, close), func=func)
                    self.loops.append(lp)
                    self._walk_sub(body, e, func)
                    return be + 1
                be = self.stmt_end(body, e)
                self.switches.append((i, body, be))
                self._walk_sub(body, e, func)
                return be + 1
            if tx == "do":
                be = self.stmt_end(i + 1, e)
                self._walk_sub(i + 1, e, func)
                j = be + 1
                if self.text(j) == "while" and self.text(j + 1) == "(" and j + 1 in self.pairs:
                    close = self.pairs[j + 1]
                    self.conds.append((j, j + 2, close, "while"))
                    self.sites.append(Site(j + 2, close, "cond", func=func, stmt="while"))
                    self.loops.append(Loop("do", i, (j + 1, close), (i + 1, be), cond=(j + 2, close), func=func))
                    k = close + 1
                    return k + 1 if self.text(k) == ";" else k
                return be + 1
            if tx == "for" and self.text(i + 1) == "(" and i + 1 in self.pairs:
                return self._walk_for(i, e, func)
            if tx == "return":
                end = self.stmt_end(i, e)
                if end > i + 1:
                    self.sites.append(Site(i + 1, end, "return", dest=func.ret if func else None, func=func))
                    self._scan_lambdas(i + 1, end, func)
                return end + 1
            if tx in ("case", "default"):
                self.cases.append(i)
                j = i + 1
                while j < e and not (self.text(j) == ":" and self.text(j - 1) != ":"):
                    j += 1
                return j + 1
            if tx in ("break", "continue", "goto"):
                return self.stmt_end(i, e) + 1
            if tx == "else":
                return self._walk_sub(i + 1, e, func) + 1
            if tx == "try":
                be = self._walk_sub(i + 1, e, func)
                k = be + 1
                while self.text(k) == "catch" and self.text(k + 1) == "(" and k + 1 in self.pairs:
                    k = self._walk_sub(self.pairs[k + 1] + 1, e, func) + 1
                return k
        if t.kind == "id" and self.text(i + 1) == ":" and self.text(i + 2) != ":":
            return i + 2       # etykieta
        # deklaracja lokalna?
        stmt_scope_end = self._block_end_of(i, e)
        r = self._try_decl(i, e, stmt_scope_end, "local", func)
        if r is not None:
            self._scan_lambdas(i, r, func)
            return r
        return self._expr_stmt(i, e, ctx, func)

    def _struct_open(self, i):
        k = i
        depth = 0
        while k > 0:
            k -= 1
            if self.text(k) == "}":
                depth += 1
            elif self.text(k) == "{":
                if depth == 0:
                    return k
                depth -= 1
        return 0

    def _decl_after_struct(self, k, e, name, ctx, func):
        """`struct P {...} a, b[10];`"""
        T = self.T
        kind = "global" if ctx == "global" else "local"
        scope_e = self.n if ctx == "global" else self._block_end_of(k, e)
        while k < e and self.text(k) != ";":
            dt = CType("struct", text=name)
            while self.text(k) in ("*", "&"):
                dt.ptr += self.text(k) == "*"
                k += 1
            if T[k].kind == "id":
                nm = k
                k += 1
                dims = []
                while self.text(k) == "[" and k in self.pairs:
                    dims.append(self.parse_expr(k + 1, self.pairs[k]))
                    k = self.pairs[k] + 1
                dt.dims = dims
                v = Var(T[nm].text, dt, nm, T[nm].line, T[nm].col, nm, scope_e, kind, func, stmt_s=nm)
                if self.text(k) == "=":
                    end = self._init_end(k + 1, e)
                    v.has_init, v.init, v.init_kind = True, (k + 1, end), "="
                    k = end
                self._add_var(v)
            if self.text(k) == ",":
                k += 1
                continue
            if self.text(k) != ";":
                k += 1
        return k + 1

    def _block_end_of(self, i, e):
        """Indeks '}' zamykającego blok, w którym jest token i (albo e)."""
        return e

    def _find_top(self, s, e, text):
        j = s
        while j < e:
            if self.text(j) in ("(", "[", "{") and j in self.pairs:
                j = self.pairs[j] + 1
                continue
            if self.text(j) == text:
                return j
            j += 1
        return None

    def _walk_sub(self, i: int, e: int, func) -> int:
        """Przechodzi pojedynczą instrukcję (ciało if/for/while). Zwraca indeks jej ostatniego tokena."""
        if i >= e:
            return e - 1
        end = self.stmt_end(i, e)
        if self.text(i) == "{":
            self._walk_block(i + 1, end, "block", func)
        else:
            self._walk_block(i, end + 1, "block", func)
        return end

    def _expr_stmt(self, i, e, ctx, func):
        end = self.stmt_end(i, e)
        if end >= i:
            self.sites.append(Site(i, end if self.text(end) == ";" else end + 1, "expr", func=func))
            self._scan_lambdas(i, end + 1, func)
        return end + 1

    def _walk_for(self, i: int, e: int, func) -> int:
        popen = i + 1
        close = self.pairs[popen]
        body = close + 1
        be = self.stmt_end(body, e)
        if self.text(body) == ";":
            self.empty_bodies.append((i, body, "for"))
        colon = None
        semis = []
        j = popen + 1
        while j < close:
            x = self.text(j)
            if x in ("(", "[", "{") and j in self.pairs:
                j = self.pairs[j] + 1
                continue
            if x == ";":
                semis.append(j)
            elif x == ":" and not semis and colon is None:
                colon = j
            j += 1
        if colon is not None and not semis:
            lp = Loop("range", i, (popen, close), (body, be), range_expr=(colon + 1, close), func=func)
            nv = len(self.vars)
            self._try_decl(popen + 1, colon + 1, be, "local", func, loop=lp, in_for=True)
            rng = self.parse_expr(colon + 1, close)
            for v in self.vars[nv:]:
                v.scope_s = popen
                if not v.structured and v.type.base == "auto" and rng is not None and rng.type.known():
                    vt = rng.type.value_type()
                    vt.ref = v.type.ref
                    v.type = vt
                lp.var = lp.var or v.name
                lp.var_obj = lp.var_obj or v
            self.sites.append(Site(colon + 1, close, "arg", func=func))
            self.loops.append(lp)
            self._walk_sub(body, e, func)
            return be + 1
        lp = Loop("for", i, (popen, close), (body, be), func=func)
        if len(semis) >= 2:
            s1, s2 = semis[0], semis[1]
            lp.init = (popen + 1, s1)
            lp.cond = (s1 + 1, s2)
            lp.incr = (s2 + 1, close)
            nv = len(self.vars)
            if s1 > popen + 1:
                if self._try_decl(popen + 1, s1 + 1, be, "local", func, loop=lp) is None:
                    self.sites.append(Site(popen + 1, s1, "expr", func=func, stmt="for-init"))
            for v in self.vars[nv:]:
                v.scope_s = popen
            if s2 > s1 + 1:
                self.sites.append(Site(s1 + 1, s2, "cond", func=func, stmt="for"))
                self.conds.append((i, s1 + 1, s2, "for"))
            if close > s2 + 1:
                self.sites.append(Site(s2 + 1, close, "incr", func=func, stmt="for"))
            self._scan_lambdas(popen + 1, close, func)
            self._analyze_for(lp, nv)
        self.loops.append(lp)
        self._walk_sub(body, e, func)
        return be + 1

    def _analyze_for(self, lp: Loop, nv: int):
        """Rozpoznaje zmienną pętli, początek, warunek i krok: for (int i = A; i < B; i++)."""
        T = self.T
        a, b = lp.init
        var = None
        start = None
        new = [v for v in self.vars[nv:] if v.loop is lp]
        if new:
            v = new[0]
            var = v.name
            lp.var_obj = v
            if v.init:
                start = self.parse_expr(*v.init)
        elif b - a >= 3 and T[a].kind == "id" and self.text(a + 1) == "=":
            var = T[a].text
            e = self._init_end(a + 2, b)
            start = self.parse_expr(a + 2, e)
            lp.var_obj = self.lookup(var, a)
        if var is None:
            return
        lp.var = var
        lp.start = start
        if lp.cond:
            c = self.parse_expr(*lp.cond)
            if c is not None and c.kind == "binary" and c.op in ("<", "<=", ">", ">=", "!="):
                l, r = c.kids
                if l.kind == "id" and l.name == var:
                    lp.cmp, lp.bound = c.op, r
                elif r.kind == "id" and r.name == var:
                    flip = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "!=": "!="}
                    lp.cmp, lp.bound = flip[c.op], l
        if lp.incr:
            s, e = lp.incr
            txt = " ".join(self.text(k) for k in range(s, e))
            if txt in (f"{var} ++", f"++ {var}", f"{var} += 1", f"{var} = {var} + 1"):
                lp.step = 1
            elif txt in (f"{var} --", f"-- {var}", f"{var} -= 1", f"{var} = {var} - 1"):
                lp.step = -1

    # ------------------------------------------------------------------ użycia
    def _resolve_uses(self):
        T = self.T
        for k, t in enumerate(T):
            if t.kind != "id" or k in self.decl_names:
                continue
            p = self.text(k - 1)
            if p in (".", "->") or (p == "::" and self.text(k - 2) not in ("", ";", "{", "}", "(", ",", "=")):
                continue
            if self.text(k + 1) == "::":
                continue
            v = self.lookup(t.text, k)
            if v is not None:
                v.uses.append(k)
        for f in self.funcs + self.lambdas:
            for k in range(f.body_s, f.body_e):
                if T[k].kind == "id" and self.text(k + 1) == "(" and self.text(k - 1) not in (".", "->"):
                    f.calls.add(T[k].text)

    def func_at(self, i: int) -> Func | None:
        best = None
        for f in self.funcs + self.lambdas:
            if f.body_s <= i <= f.body_e and (best is None or f.body_s > best.body_s):
                best = f
        return best

    def loops_at(self, i: int) -> list[Loop]:
        return [lp for lp in self.loops if lp.body[0] <= i <= lp.body[1] or (lp.hdr[0] < i < lp.hdr[1] and lp.kind != "range")]

    def in_loop(self, i: int) -> bool:
        return any(lp.body[0] <= i <= lp.body[1] for lp in self.loops)

    # ------------------------------------------------------------------ wyrażenia
    def parse_expr(self, s: int, e: int) -> Node | None:
        if s >= e:
            return None
        try:
            p = _Parser(self, s, e)
            node = p.parse()
            _set_parents(node, None)
            return node
        except RecursionError:
            return None

    def _parse_sites(self):
        for st in self.sites:
            st.node = self.parse_expr(st.s, st.e)

    def all_nodes(self):
        for st in self.sites:
            if st.node is not None:
                for nd in st.node.walk():
                    yield st, nd


def _set_parents(node: Node | None, parent: Node | None):
    if node is None:
        return
    stack = [(node, parent)]
    while stack:
        nd, par = stack.pop()
        nd.parent = par
        for k in nd.kids:
            if k is not None:
                stack.append((k, nd))


def _const_eval_tokens(toks, consts) -> object:
    """Ewaluacja prostych wyrażeń stałych na tokenach (liczby, + - * / % << >> | & ^, nawiasy, stałe)."""
    expr = []
    for t in toks:
        if t.kind == "num":
            v, base = parse_number(t.text)
            if v is None:
                return None
            expr.append(repr(v))
        elif t.kind == "id" and t.text in consts and isinstance(consts[t.text], (int, float)):
            expr.append(repr(consts[t.text]))
        elif t.kind == "id" and t.text in CONSTANTS:
            expr.append(repr(CONSTANTS[t.text][1]))
        elif t.text in ("+", "-", "*", "/", "%", "(", ")", "<<", ">>", "|", "&", "^", "~"):
            expr.append(t.text)
        elif t.kind == "kw" and t.text in ("int", "long", "unsigned", "double"):
            continue
        elif t.text in ("LL", "ll"):
            continue
        else:
            return None
    if not expr or len(expr) > 60:
        return None
    s = " ".join(expr)
    if "**" in s:
        return None
    try:
        int_div = not any(isinstance(x, float) for x in [])
        v = _safe_eval(s)
        del int_div
        return v
    except Exception:  # noqa: BLE001
        return None


def _safe_eval(s: str):
    import ast
    tree = ast.parse(s, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return v
            if isinstance(n.op, ast.Invert) and isinstance(v, int):
                return ~v
            raise ValueError
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            op = n.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                r = a * b
                if isinstance(r, int) and abs(r) > 2 ** 130:
                    raise ValueError
                return r
            if isinstance(op, ast.Div):
                if b == 0:
                    raise ValueError
                if isinstance(a, int) and isinstance(b, int):
                    q = abs(a) // abs(b)
                    return q if (a >= 0) == (b >= 0) else -q
                return a / b
            if isinstance(op, ast.Mod):
                if b == 0 or not (isinstance(a, int) and isinstance(b, int)):
                    raise ValueError
                return int(math.fmod(a, b))
            if isinstance(op, ast.LShift) and isinstance(a, int) and isinstance(b, int) and 0 <= b < 130:
                return a << b
            if isinstance(op, ast.RShift) and isinstance(a, int) and isinstance(b, int) and b >= 0:
                return a >> b
            if isinstance(op, ast.BitOr) and isinstance(a, int) and isinstance(b, int):
                return a | b
            if isinstance(op, ast.BitAnd) and isinstance(a, int) and isinstance(b, int):
                return a & b
            if isinstance(op, ast.BitXor) and isinstance(a, int) and isinstance(b, int):
                return a ^ b
        raise ValueError
    return ev(tree)


# =============================================================================================== parser wyrażeń
class _Parser:
    def __init__(self, m: Model, s: int, e: int):
        self.m = m
        self.T = m.T
        self.i = s
        self.s = s
        self.e = e
        self.depth = 0

    def peek(self, k=0):
        j = self.i + k
        return self.T[j] if j < self.e else None

    def ptext(self, k=0):
        j = self.i + k
        return self.T[j].text if j < self.e else ""

    def parse(self) -> Node:
        node = self.comma()
        return node

    def comma(self) -> Node:
        s = self.i
        left = self.expr(2)
        while self.ptext() == ",":
            self.i += 1
            right = self.expr(2)
            left = Node("binary", ",", [left, right], s, self.i, type=right.type)
        # pomiń resztę (np. niezrozumiałe tokeny)
        return left

    def expr(self, minp: int) -> Node:
        self.depth += 1
        if self.depth > 400:
            raise RecursionError
        s = self.i
        left = self.unary()
        while True:
            t = self.peek()
            if t is None:
                break
            op = t.text
            if t.kind == "punct":
                break
            p = BIN_PREC.get(op)
            if p is None or p < minp:
                break
            if op == "?":
                self.i += 1
                mid = self.comma_until_colon()
                if self.ptext() == ":":
                    self.i += 1
                right = self.expr(2)
                left = Node("ternary", "?", [left, mid, right], s, self.i)
                left.type = _ternary_type(mid.type, right.type)
                continue
            self.i += 1
            if op in ASSIGN_OPS:
                right = self.expr(p)
                left = Node("assign", op, [left, right], s, self.i, type=_copy(left.type))
                continue
            right = self.expr(p + 1)
            op2 = ALIAS_OPS.get(op, op)
            nd = Node("binary", op2, [left, right], s, self.i)
            _type_binary(nd)
            left = nd
        self.depth -= 1
        return left

    def comma_until_colon(self) -> Node:
        return self.expr(2)

    def unary(self) -> Node:
        t = self.peek()
        s = self.i
        if t is None:
            return Node("unknown", s=s, e=s)
        tx = t.text
        if (t.kind == "op" and tx in ("+", "-", "!", "~", "++", "--", "*", "&", "&&")) or (t.kind == "kw" and tx in ("not", "compl")):
            self.i += 1
            operand = self.unary()
            nd = Node("unary", {"not": "!", "compl": "~"}.get(tx, tx), [operand], s, self.i)
            ot = operand.type
            if nd.op in ("!",):
                nd.type = CType("bool")
            elif nd.op == "*":
                nd.type = ot.deref() if ot else CType()
            elif nd.op == "&":
                nd.type = CType(ot.base, ot.args, ot.ptr + 1) if ot and ot.known() else CType("unknown")
                if ot is not None and ot.is_array():
                    nd.type = CType("unknown")
            elif nd.op in ("++", "--"):
                nd.type = _copy(ot)
            else:
                nd.type = CType(_promote(ot.base)) if ot.is_int() else _copy(ot)
            if operand.value is not None and isinstance(operand.value, (int, float)):
                if nd.op == "-":
                    nd.value = -operand.value
                elif nd.op == "+":
                    nd.value = operand.value
                elif nd.op == "~" and isinstance(operand.value, int):
                    nd.value = ~operand.value
            return nd
        if tx == "sizeof":
            self.i += 1
            if self.ptext() == "..." :
                self.i += 1
            if self.ptext() == "(" and self.i in self.m.pairs:
                c = self.m.pairs[self.i]
                inner = self.m.parse_expr(self.i + 1, c) if c < self.e else None
                self.i = min(c + 1, self.e)
                return Node("sizeof", "sizeof", [inner] if inner else [], s, self.i, type=CType("ull"))
            operand = self.unary()
            return Node("sizeof", "sizeof", [operand], s, self.i, type=CType("ull"))
        if tx in ("new", "delete", "throw", "co_await"):
            self.i += 1
            if tx == "delete" and self.ptext() == "[":
                self.i += 2
            if tx == "new":
                r = self.m.parse_type(self.i, self.e, allow_unknown=True)
                if r:
                    ct, k = r
                    self.i = k
                    if self.ptext() == "[" and self.i in self.m.pairs:
                        self.i = self.m.pairs[self.i] + 1
                    elif self.ptext() in ("(", "{") and self.i in self.m.pairs:
                        self.i = self.m.pairs[self.i] + 1
                    ct.ptr += 1
                    return Node("new", "new", [], s, self.i, type=ct)
            operand = self.unary()
            return Node("unary", tx, [operand], s, self.i, type=CType("unknown"))
        if tx == "(" and self.i in self.m.pairs:
            c = self.m.pairs[self.i]
            # rzutowanie (typ) wyrażenie
            r = self.m.parse_type(self.i + 1, c, allow_unknown=False)
            if r is not None:
                ct, k = r
                while k < c and self.T[k].text in ("*", "&"):
                    if self.T[k].text == "*":
                        ct.ptr += 1
                    k += 1
                if k == c and c + 1 < self.e:
                    nt = self.T[c + 1]
                    if nt.kind in ("id", "num", "char", "str", "kw") or nt.text in ("(", "-", "+", "!", "~", "*", "&", "++", "--"):
                        if not (nt.kind == "kw" and nt.text in ("and", "or")):
                            self.i = c + 1
                            operand = self.unary()
                            nd = Node("cast", "c-cast", [operand], s, self.i, type=ct)
                            nd.value = operand.value if ct.is_int() and isinstance(operand.value, (int, float)) else None
                            if nd.value is not None and isinstance(nd.value, float):
                                nd.value = int(nd.value)
                            return nd
        return self.postfix(self.primary())

    def primary(self) -> Node:
        t = self.peek()
        s = self.i
        if t is None:
            return Node("unknown", s=s, e=s)
        tx = t.text
        if t.kind == "num":
            self.i += 1
            v, base = parse_number(tx)
            return Node("num", "", [], s, self.i, type=CType(base), value=v)
        if t.kind in ("str",):
            self.i += 1
            while self.peek() is not None and self.peek().kind == "str":
                self.i += 1
            return Node("str", "", [], s, self.i, type=CType("cstr"), value=tx)
        if t.kind == "char":
            self.i += 1
            ch = tx[1:-1]
            val = None
            if len(ch) == 1:
                val = ord(ch)
            elif ch.startswith("\\") and len(ch) == 2:
                val = {"n": 10, "t": 9, "0": 0, "\\": 92, "'": 39, '"': 34, "r": 13}.get(ch[1])
            return Node("char", "", [], s, self.i, type=CType("char"), value=val)
        if tx == "(" and self.i in self.m.pairs:
            c = self.m.pairs[self.i]
            inner = self.m.parse_expr(self.i + 1, min(c, self.e))
            self.i = min(c + 1, self.e)
            if inner is None:
                return Node("unknown", s=s, e=self.i)
            inner.paren = True
            nd = Node("paren", "()", [inner], s, self.i, type=inner.type, value=inner.value)
            return nd
        if tx == "{" and self.i in self.m.pairs:
            c = self.m.pairs[self.i]
            kids = [self.m.parse_expr(a, b) for a, b in self.m._split_args(self.i + 1, c)]
            self.i = min(c + 1, self.e)
            return Node("init_list", "{}", [k for k in kids if k is not None], s, self.i, type=CType("unknown"))
        if tx == "[" and self.i in self.m.pairs:
            # lambda
            c = self.m.pairs[self.i]
            k = c + 1
            if k < self.e and self.T[k].text == "(" and k in self.m.pairs:
                k = self.m.pairs[k] + 1
            while k < self.e and self.T[k].text not in ("{",):
                if self.T[k].text in (";", ","):
                    break
                k += 1
            if k < self.e and self.T[k].text == "{" and k in self.m.pairs:
                end = self.m.pairs[k] + 1
                self.i = min(end, self.e)
                return Node("lambda", "", [], s, self.i, type=CType("lambda"), value=k)
            self.i = min(c + 1, self.e)
            return Node("unknown", s=s, e=self.i)
        if t.kind == "kw":
            if tx in ("true", "false"):
                self.i += 1
                return Node("num", "", [], s, self.i, type=CType("bool"), value=1 if tx == "true" else 0, name=tx)
            if tx == "nullptr":
                self.i += 1
                return Node("num", "", [], s, self.i, type=CType("ptr"), value=0, name=tx)
            if tx == "this":
                self.i += 1
                return Node("id", "", [], s, self.i, type=CType("struct", ptr=1), name="this")
            if tx in ("static_cast", "reinterpret_cast", "const_cast", "dynamic_cast") and self.ptext(1) == "<":
                c = self.m.match_angle(self.i + 1)
                if c is not None and c + 1 < self.e and self.T[c + 1].text == "(":
                    r = self.m.parse_type(self.i + 2, c, allow_unknown=True)
                    ct = r[0] if r else CType()
                    k = r[1] if r else c
                    while k < c and self.T[k].text in ("*", "&"):
                        if self.T[k].text == "*":
                            ct.ptr += 1
                        k += 1
                    pc = self.m.pairs.get(c + 1, c + 1)
                    inner = self.m.parse_expr(c + 2, pc)
                    self.i = min(pc + 1, self.e)
                    nd = Node("cast", tx, [inner] if inner else [], s, self.i, type=ct)
                    if inner is not None and ct.is_int() and isinstance(inner.value, (int, float)):
                        nd.value = int(inner.value)
                    return nd
            # rzutowanie funkcyjne: int(x), double(x), long long? (nie), unsigned(x)
            r = self.m.parse_type(self.i, self.e, allow_unknown=False)
            if r is not None:
                ct, k = r
                if k < self.e and self.T[k].text in ("(", "{") and k in self.m.pairs:
                    pc = self.m.pairs[k]
                    inner = self.m.parse_expr(k + 1, pc)
                    self.i = min(pc + 1, self.e)
                    nd = Node("cast", "f-cast", [inner] if inner else [], s, self.i, type=ct)
                    if inner is not None and ct.is_int() and isinstance(inner.value, (int, float)):
                        nd.value = int(inner.value)
                    return nd
                self.i = k
                return Node("type", "", [], s, self.i, type=ct)
            if tx == "operator":
                self.i += 1
                return Node("unknown", s=s, e=self.i)
            self.i += 1
            return Node("unknown", s=s, e=self.i)
        if t.kind == "id" or tx == "::":
            parts = []
            j = self.i
            if tx == "::":
                j += 1
            while j < self.e and self.T[j].kind == "id":
                parts.append(self.T[j].text)
                if j + 2 < self.e and self.T[j + 1].text == "::" and self.T[j + 2].kind in ("id", "kw"):
                    j += 2
                    continue
                if j + 1 < self.e and self.T[j + 1].text == "<" and self.T[j].text in self.m.template_names:
                    c = self.m.match_angle(j + 1)
                    if c is not None and c + 1 <= self.e:
                        nxt = self.T[c + 1].text if c + 1 < self.e else ""
                        if nxt in ("(", "::", "{") or c + 1 >= self.e:
                            targs, _ = self.m._template_args(j + 2, c)
                            name = self.T[j].text
                            j = c + 1
                            if nxt == "::" and j + 1 < self.e and self.T[j + 1].kind in ("id", "kw"):
                                parts.append(self.T[j + 1].text)
                                j += 2
                                nd = Node("id", "", [], s, j, name=parts[-1], qual="::".join(parts), targs=targs)
                                self.i = j
                                return self._finish_id(nd)
                            self.i = j
                            nd = Node("id", "", [], s, j, name=name, qual="::".join(parts), targs=targs)
                            return self._finish_id(nd)
                j += 1
                break
            if not parts:
                self.i += 1
                return Node("unknown", s=s, e=self.i)
            self.i = j
            nd = Node("id", "", [], s, j, name=parts[-1], qual="::".join(parts))
            return self._finish_id(nd)
        self.i += 1
        return Node("unknown", s=s, e=self.i)

    def _finish_id(self, nd: Node) -> Node:
        name = nd.name
        m = self.m
        q = nd.qual
        if q.endswith("numeric_limits::max") or q.endswith("numeric_limits::min"):
            ta = nd.targs[0] if nd.targs else CType("int")
            nd.type = _copy(ta)
            if ta.base == "int":
                nd.value = INT_MAX if q.endswith("max") else INT_MIN
            elif ta.base == "ll":
                nd.value = LL_MAX if q.endswith("max") else -LL_MAX - 1
            nd.name = "INT_MAX" if ta.base == "int" and q.endswith("max") else nd.name
            return nd
        if q.startswith("std::") or q.count("::") == 0 or q.startswith("::"):
            pass
        v = m.lookup(name, nd.s if not q or "::" not in q else nd.e - 1)
        if v is not None and (q == name or q == "::" + name):
            nd.var = v
            nd.type = _copy(v.type)
            if name in m.consts and v.is_const:
                nd.value = m.consts[name]
            return nd
        if name in CONSTANTS:
            b, val = CONSTANTS[name]
            nd.type = CType(b)
            nd.value = val
            return nd
        if name in m.consts:
            val = m.consts[name]
            nd.value = val
            nd.type = CType("double" if isinstance(val, float) else ("int" if -2 ** 31 <= val <= INT_MAX else "ll"))
            return nd
        if name in STREAMS:
            nd.type = CType(STREAMS[name])
            return nd
        if name in ("endl", "flush", "ws", "fixed", "setprecision", "setw", "setfill", "boolalpha", "hex", "dec"):
            nd.type = CType("manip")
            return nd
        if name in m.aliases or name in SIMPLE_TYPES or name in STD_TEMPLATES or name in m.structs:
            r = m.parse_type(nd.s, self.e, allow_unknown=False)
            if r is not None and r[1] >= nd.e:
                nd.kind = "type"
                nd.type = r[0]
                self.i = max(self.i, r[1])
                nd.e = self.i
                return nd
        return nd

    def postfix(self, nd: Node) -> Node:
        m = self.m
        while True:
            t = self.peek()
            if t is None:
                return nd
            tx = t.text
            if tx == "(" and self.i in m.pairs:
                c = m.pairs[self.i]
                args = [m.parse_expr(a, b) for a, b in m._split_args(self.i + 1, min(c, self.e))]
                args = [a if a is not None else Node("unknown") for a in args]
                self.i = min(c + 1, self.e)
                call = Node("call", "()", [nd] + args, nd.s, self.i)
                _type_call(m, call)
                nd = call
                continue
            if tx == "{" and nd.kind == "type" and self.i in m.pairs:
                c = m.pairs[self.i]
                args = [m.parse_expr(a, b) for a, b in m._split_args(self.i + 1, min(c, self.e))]
                self.i = min(c + 1, self.e)
                nd = Node("cast", "brace", [nd] + [a for a in args if a], nd.s, self.i, type=_copy(nd.type))
                continue
            if tx == "[" and self.i in m.pairs:
                c = m.pairs[self.i]
                idx = m.parse_expr(self.i + 1, min(c, self.e))
                self.i = min(c + 1, self.e)
                ix = Node("index", "[]", [nd, idx if idx is not None else Node("unknown")], nd.s, self.i)
                bt = nd.type
                ix.type = bt.elem() if bt is not None else CType()
                if bt is not None and bt.base in ("map", "umap") and not bt.dims and not bt.ptr:
                    ix.type = _copy(bt.args[1]) if len(bt.args) > 1 else CType()
                nd = ix
                continue
            if tx in (".", "->") and self.i + 1 < self.e:
                self.i += 1
                if self.ptext() == "template":
                    self.i += 1
                mt = self.peek()
                if mt is None:
                    return nd
                self.i += 1
                mem = Node("member", tx, [nd], nd.s, self.i, name=mt.text)
                bt = nd.type if tx == "." else (nd.type.deref() if nd.type else CType())
                mem.type = _member_type(m, bt, mt.text)
                if self.ptext() == "<" and mt.text in ("get",):
                    c = m.match_angle(self.i)
                    if c is not None:
                        self.i = c + 1
                nd = mem
                continue
            if tx in ("++", "--") and t.kind == "op":
                self.i += 1
                nd = Node("postfix", tx, [nd], nd.s, self.i, type=_copy(nd.type))
                continue
            return nd


def _member_type(m: Model, bt: CType, name: str) -> CType:
    if bt is None:
        return CType()
    if bt.base == "pair" and not bt.dims and not bt.ptr:
        if name == "first" and bt.args:
            return _copy(bt.args[0])
        if name == "second" and len(bt.args) > 1:
            return _copy(bt.args[1])
    if bt.base == "struct":
        fields = m.structs.get(bt.text.split("<")[0], {})
        if name in fields:
            return _copy(fields[name])
    return CType("method:" + name, args=[bt])


def _type_call(m: Model, call: Node):
    fn = call.kids[0]
    args = call.kids[1:]
    call.type = CType()
    if fn.kind == "member":
        meth = fn.name
        obj = fn.kids[0].type if fn.op == "." else (fn.kids[0].type.deref() if fn.kids[0].type else CType())
        call.name = meth
        if meth in ("size", "length", "count", "capacity"):
            call.type = CType("ull", text="size_t")
        elif meth in ("front", "back", "top", "at"):
            if obj.base in ("set", "mset", "pq", "uset") and obj.args:
                call.type = _copy(obj.args[0])
            elif obj.base in ("queue", "stack", "deque", "vector", "list") and obj.args:
                call.type = _copy(obj.args[0])
            elif obj.base == "string":
                call.type = CType("char")
            elif obj.base in ("map", "umap") and meth == "at" and len(obj.args) > 1:
                call.type = _copy(obj.args[1])
        elif meth in ("begin", "end", "find", "lower_bound", "upper_bound", "rbegin", "rend", "insert", "erase"):
            call.type = CType("iter", args=[obj]) if meth not in ("erase",) or obj.base not in ("set", "map") else CType("iter", args=[obj])
            if meth == "find" and obj.base == "string":
                call.type = CType("ull", text="size_t")
        elif meth in ("substr",):
            call.type = CType("string")
        elif meth in ("empty",):
            call.type = CType("bool")
        elif meth in ("c_str", "data"):
            call.type = CType("char", ptr=1)
        elif meth in ("to_ullong", "to_ulong"):
            call.type = CType("ull")
        elif meth == "count" or meth == "any" or meth == "none" or meth == "test":
            call.type = CType("bool")
        elif meth in ("first", "second"):
            pass
        elif obj.base == "struct":
            for f in m.func_by_name.get(meth, []):
                if f.ret is not None:
                    call.type = _copy(f.ret)
                    break
        return
    name = fn.name if fn.kind in ("id",) else ""
    call.name = name
    if fn.kind == "type":
        call.type = _copy(fn.type)
        call.kind = "cast"
        call.op = "f-cast"
        call.kids = args
        if args and fn.type.is_int() and isinstance(args[0].value, (int, float)):
            call.value = int(args[0].value)
        return
    if fn.var is not None and fn.var.type.base in ("lambda", "function", "auto", "unknown"):
        return
    if name in ("max", "min") and args:
        if fn.targs:
            call.type = _copy(fn.targs[0])
        elif len(args) >= 2:
            call.type = common_type(args[0].type, args[1].type) if args[0].type.base == args[1].type.base or True else CType()
            if args[0].type.known() and args[0].type.base == args[1].type.base:
                call.type = _copy(args[0].type)
        else:
            call.type = CType()
        vals = [a.value for a in args[:2]]
        if all(isinstance(v, (int, float)) for v in vals) and len(vals) == 2:
            call.value = max(vals) if name == "max" else min(vals)
        return
    if name in ("abs", "llabs", "fabs", "labs"):
        if args:
            at = args[0].type
            call.type = CType("double") if name == "fabs" else (CType(_promote(at.base)) if at.is_arith() else CType())
            if name == "llabs":
                call.type = CType("ll")
        return
    if name in DOUBLE_FUNCS:
        call.type = CType("ldouble" if name.endswith("l") and name not in ("ceil", "fmodl") else "double")
        if name in ("sqrtl", "powl", "logl", "expl", "fabsl", "floorl", "ceill", "roundl"):
            call.type = CType("ldouble")
        return
    if name in INT_FUNCS:
        call.type = CType(INT_FUNCS[name])
        return
    if name in ("gcd", "lcm", "__gcd"):
        if len(args) >= 2:
            call.type = common_type(args[0].type, args[1].type)
        return
    if name == "accumulate" and len(args) >= 3:
        call.type = _copy(args[2].type)
        return
    if name in ("to_string",):
        call.type = CType("string")
        return
    if name in ("make_pair",) and len(args) == 2:
        call.type = CType("pair", [_copy(args[0].type), _copy(args[1].type)])
        return
    if name in ("sizeof",):
        call.type = CType("ull")
        return
    for f in m.func_by_name.get(name, []):
        if f.ret is not None:
            call.type = _copy(f.ret)
            call.type.ref = False
            return
    if name in m.protos:
        call.type = _copy(m.protos[name])
        return
    if name in m.structs:
        call.type = CType("struct", text=name)


def _ternary_type(a: CType, b: CType) -> CType:
    if a is None or b is None:
        return CType()
    if a.is_arith() and b.is_arith():
        return common_type(a, b)
    if a.base == b.base:
        return _copy(a)
    return CType()


def _type_binary(nd: Node):
    a, b = nd.kids
    op = nd.op
    at, bt = a.type, b.type
    if op in CMP_OPS or op in ("&&", "||"):
        nd.type = CType("bool")
    elif op in ("<<", ">>"):
        if at.base in ("stream", "file"):
            nd.type = CType("stream")
        elif at.is_int():
            nd.type = CType(_promote(at.base))
        else:
            nd.type = CType()
    elif op in ("+", "-", "*", "/", "%", "&", "|", "^"):
        if at.ptr and bt.is_int() and op in ("+", "-"):
            nd.type = _copy(at)
        elif at.is_arith() and bt.is_arith():
            nd.type = common_type(at, bt)
        elif op in ("+",) and (at.base in ("string",) or bt.base in ("string",)):
            nd.type = CType("string")
        elif at.base == "iter" and op in ("+", "-"):
            nd.type = _copy(at) if op == "+" or bt.base != "iter" else CType("ll")
        else:
            nd.type = CType()
    elif op == "<=>":
        nd.type = CType("int")
    else:
        nd.type = CType()
    av, bv = a.value, b.value
    if isinstance(av, (int, float)) and isinstance(bv, (int, float)) and not isinstance(av, bool):
        try:
            if op == "+":
                nd.value = av + bv
            elif op == "-":
                nd.value = av - bv
            elif op == "*":
                nd.value = av * bv
            elif op == "/" and bv != 0:
                if isinstance(av, int) and isinstance(bv, int):
                    q = abs(av) // abs(bv)
                    nd.value = q if (av >= 0) == (bv >= 0) else -q
                else:
                    nd.value = av / bv
            elif op == "%" and bv != 0 and isinstance(av, int) and isinstance(bv, int):
                nd.value = int(math.fmod(av, bv))
            elif op == "<<" and isinstance(av, int) and isinstance(bv, int) and 0 <= bv < 128:
                nd.value = av << bv
            elif op == ">>" and isinstance(av, int) and isinstance(bv, int) and bv >= 0:
                nd.value = av >> bv
            elif op in ("|", "&", "^") and isinstance(av, int) and isinstance(bv, int):
                nd.value = {"|": av | bv, "&": av & bv, "^": av ^ bv}[op]
        except (OverflowError, ValueError):
            nd.value = None


# =============================================================================================== reguły — infrastruktura
@dataclass
class RuleMeta:
    id: str
    title: str
    severity: str
    category: str
    kb: list
    desc: str
    query: str = ""


RULES: dict[str, RuleMeta] = {}
_CHECKS: list = []

CATEGORIES = {
    "overflow": "Przepełnienia i typy", "memory": "Pamięć i tablice", "logic": "Logika", "ub": "Niezdefiniowane zachowanie",
    "io": "Wejście/wyjście", "perf": "Wydajność", "stl": "STL", "style": "Styl i czytelność", "compile": "Kompilacja",
    "portability": "Przenośność",
}


def meta(rid, title, severity, category, kb, desc, query=""):
    RULES[rid] = RuleMeta(rid, title, severity, category, list(kb), desc, query or title)


def check(fn):
    _CHECKS.append(fn)
    return fn


KB_FALLBACK = {}


class Ctx:
    def __init__(self, m: Model, kb_ids: set | None):
        self.m = m
        self.issues: list[dict] = []
        self._seen: set = set()
        self.kb_ids = kb_ids

    def kb_for(self, rid: str):
        cands = RULES[rid].kb if rid in RULES else []
        if self.kb_ids is None:
            return cands[0] if cands else None
        for c in cands:
            if c in self.kb_ids:
                return c
        return None

    def report(self, rid: str, at, message: str, fix: str = "", severity: str | None = None, title: str | None = None,
               confidence: float = 0.8, line: int | None = None, col: int | None = None, key=None, extra: dict | None = None):
        m = self.m
        r = RULES[rid]
        if line is None:
            if isinstance(at, Node):
                at = at.s
            t = m.tok(at) if isinstance(at, int) else None
            line = t.line if t else 1
            col = t.col if t else 1
        k = (rid, line, key)
        if k in self._seen:
            return
        self._seen.add(k)
        d = {"line": line, "col": col or 1, "severity": severity or r.severity, "rule": rid, "title": title or r.title,
             "message": message, "fix": fix, "kb": self.kb_for(rid), "kb_query": r.query, "category": r.category,
             "confidence": round(confidence, 2), "code": m.line_text(line).strip()[:200]}
        if extra:
            d.update(extra)
        self.issues.append(d)


def _strip(nd: Node | None) -> Node | None:
    while nd is not None and nd.kind == "paren" and nd.kids:
        nd = nd.kids[0]
    return nd


def _src(m: Model, nd: Node | None) -> str:
    return m.src(nd.s, nd.e) if nd is not None else "?"


def _is_const(nd: Node | None) -> bool:
    nd = _strip(nd)
    return nd is not None and isinstance(nd.value, (int, float)) and nd.value is not None


def _stmt_start(m: Model, k: int) -> int:
    j = k - 1
    while j >= 0:
        t = m.text(j)
        if t in (")", "]") and j in m.pairs:
            j = m.pairs[j] - 1
            continue
        if t in (";", "{", "}", "("):
            return j + 1
        j -= 1
    return 0


def _chain_head(m: Model, k: int) -> int:
    """Początek łańcucha `a >> b >> c` (indeks pierwszego tokenu pierwszego operandu)."""
    j = k
    guard = 0
    while m.text(j - 1) in (">>", "<<") and guard < 500:
        guard += 1
        q = j - 2
        while q >= 0:
            t = m.text(q)
            if t in (")", "]") and q in m.pairs:
                q = m.pairs[q] - 1
                continue
            if m.T[q].kind in ("id", "kw", "num", "str", "char"):
                if m.text(q - 1) in (".", "->", "::"):
                    q -= 2
                    continue
            break
        j = max(q, 0)
    return j


def _is_input_stmt(m: Model, k: int) -> bool:
    s = _chain_head(m, k)
    t = m.text(s)
    if t == "std" and m.text(s + 1) == "::":
        t = m.text(s + 2)
    if t in ("cin", "fin", "in", "inp", "wcin"):
        return True
    v = m.lookup(t, s)
    return v is not None and v.type.base == "stream"


READ_FUNCS = {"max", "min", "abs", "printf", "sqrt", "pow", "to_string", "gcd", "__gcd", "lcm", "puts", "putchar",
              "labs", "llabs", "fabs", "floor", "ceil", "log", "log2", "log10", "exp", "sin", "cos", "assert",
              "__builtin_popcount", "__builtin_popcountll", "__lg", "make_pair", "pair", "sqrtl", "cbrt", "round",
              "llround", "exit", "hypot", "atan2", "fprintf", "bitset", "string", "char", "int", "double", "long",
              "malloc", "calloc", "isdigit", "isalpha", "tolower", "toupper", "static_cast", "max_element"}
READ_METHODS = {"push_back", "emplace_back", "push", "emplace", "insert", "count", "find", "erase", "lower_bound",
                "upper_bound", "at", "push_front", "assign", "resize", "contains", "set", "reset", "flip", "test",
                "append", "substr"}


def access_mode(m: Model, k: int) -> str:
    """Jak użyto identyfikatora na pozycji k: write | rw | read | cin | addr | refarg | none."""
    prv = m.text(k - 1)
    j = k + 1
    while m.text(j) == "[" and j in m.pairs:
        j = m.pairs[j] + 1
    nxt = m.text(j)
    if prv in ("++", "--"):
        return "rw"
    if nxt in ("++", "--"):
        return "rw"
    if nxt == "=":
        return "write"
    if nxt in ASSIGN_OPS:
        return "rw"
    if nxt in (".", "->"):
        mem = m.text(j + 1)
        if m.text(j + 2) == "=" or mem in ("clear", "resize", "assign", "push_back", "insert", "emplace_back", "push",
                                          "emplace", "swap", "fill", "reset", "set"):
            return "write"
        return "read"
    if prv == "&" and m.text(k - 2) in ("(", ",", "=", "return", "{", ":", "?", "&&", "||", "<<"):
        return "addr"
    if prv == ">>" and _is_input_stmt(m, k):
        return "cin"
    if prv == "(" and m.text(k - 2) in ("sizeof", "decltype", "alignof", "typeid"):
        return "none"
    if prv in ("(", ",") and nxt in (")", ","):
        # bezpośredni argument wywołania
        d = 0
        q = k - 1
        while q >= 0:
            t = m.text(q)
            if t == ")" and q in m.pairs:
                q = m.pairs[q] - 1
                continue
            if t == "(":
                break
            if t in (";", "{", "}"):
                q = -1
                break
            q -= 1
        del d
        if q > 0:
            fname = m.text(q - 1)
            ft = m.tok(q - 1)
            if (q - 1) in m.decl_names:
                return "read"           # argument konstruktora w deklaracji: vector<int> v(n)
            if ft is None or (ft.kind not in ("id", "kw") and fname != ">") or \
                    (ft.kind == "kw" and fname not in ("int", "long", "double", "char", "bool", "unsigned")):
                return "read"           # zwykły nawias w wyrażeniu, nie wywołanie
            if m.text(q - 2) in (".", "->"):
                return "read" if fname in READ_METHODS else "refarg"
            if fname in READ_FUNCS or fname in ("if", "while", "switch", "return", "for"):
                return "read"
            if fname in ("swap", "tie", "getline", "scanf", "read", "readInt", "sscanf", "fill", "memset", "iota",
                         "copy", "fread", "gets", "fgets", "cin", "readln", "in", "rd", "input"):
                return "refarg"
            # funkcja użytkownika: czy parametr jest referencją?
            fs = m.func_by_name.get(fname)
            if fs:
                # numer argumentu
                idx = 0
                r = q + 1
                while r < k:
                    t = m.text(r)
                    if t in ("(", "[", "{") and r in m.pairs:
                        r = m.pairs[r] + 1
                        continue
                    if t == ",":
                        idx += 1
                    r += 1
                for f in fs:
                    if idx < len(f.params):
                        p = f.params[idx]
                        if p.type.ref or p.type.ptr or p.init_kind == "array_param":
                            return "refarg"
                        return "read"
                return "refarg"
            return "refarg"
    return "read"


def _uses_in(v: Var, s: int, e: int) -> list[int]:
    return [u for u in v.uses if s <= u < e]


def _func_of_var(m: Model, v: Var) -> Func | None:
    return v.func


def _loop_bodies_containing(m: Model, k: int) -> list[Loop]:
    return [lp for lp in m.loops if lp.body[0] <= k <= lp.body[1]]


def _ancestors(nd: Node):
    p = nd.parent
    while p is not None:
        yield p
        p = p.parent


def _mentions(m: Model, s: int, e: int, name: str) -> bool:
    return any(m.T[k].text == name and m.T[k].kind == "id" for k in range(max(0, s), min(m.n, e)))


def _guarded(m: Model, nd: Node, name: str, lp: Loop | None = None) -> bool:
    """Czy dostęp jest chroniony warunkiem wspominającym zmienną `name`."""
    k = nd.s
    for kw, s, e, kwn in m.conds:
        if kwn in ("if", "while") and kw < k <= m.stmt_end(kw) and _mentions(m, s, e, name):
            return True
        if lp is not None and kwn == "if" and lp.body[0] <= kw < k and _mentions(m, s, e, name):
            end = m.stmt_end(kw)
            if any(m.text(q) in ("break", "continue", "return") for q in range(kw, end + 1)):
                return True
    child = nd
    for p in _ancestors(nd):
        if p.kind == "binary" and p.op in ("&&", "||") and p.kids[1] is child:
            if _mentions(m, p.kids[0].s, p.kids[0].e, name):
                return True
        if p.kind == "ternary" and child is not p.kids[0] and _mentions(m, p.kids[0].s, p.kids[0].e, name):
            return True
        child = p
    return False


def _linear(m: Model, nd: Node | None):
    """(symbol, stała) dla wyrażeń: x, x+c, x-c, c+x, c, v.size(), (int)v.size()-c. Inaczej None."""
    nd = _strip(nd)
    if nd is None:
        return None
    if isinstance(nd.value, (int, float)) and nd.kind != "id":
        try:
            return (None, int(nd.value))
        except (OverflowError, ValueError):
            return None
    if nd.kind == "id":
        if isinstance(nd.value, (int, float)):
            return (None, int(nd.value))
        return (nd.name, 0)
    if nd.kind == "cast" and nd.kids:
        return _linear(m, nd.kids[-1])
    if nd.kind == "call" and nd.kids and nd.kids[0].kind == "member" and nd.kids[0].name in ("size", "length"):
        obj = _strip(nd.kids[0].kids[0])
        if obj is not None and obj.kind == "id":
            return (obj.name + ".size()", 0)
        return None
    if nd.kind == "binary" and nd.op in ("+", "-"):
        a, b = _linear(m, nd.kids[0]), _linear(m, nd.kids[1])
        if a is None or b is None:
            return None
        if nd.op == "+":
            if a[0] and b[0]:
                return None
            return (a[0] or b[0], a[1] + b[1])
        if b[0]:
            return None
        return (a[0], a[1] - b[1])
    return None


def _container_size(m: Model, v: Var):
    """(symbol, stała) rozmiaru tablicy/vectora, jeśli znany i stały w programie."""
    t = v.type
    if t.dims:
        return _sym_stable(m, v, _linear(m, t.dims[0])) if t.dims[0] is not None else None
    if t.base in ("vector", "deque") and t.scalar() and v.init_kind in ("()",) and v.ctor:
        # jeśli zmieniany rozmiar — nieznany
        for u in v.uses:
            if m.text(u + 1) in (".", "->") and m.text(u + 2) in ("resize", "assign", "push_back", "emplace_back",
                                                                   "pop_back", "insert", "erase", "clear", "swap",
                                                                   "emplace", "push_front", "pop_front"):
                return None
            if m.text(u + 1) == "=" or m.text(u - 1) in ("swap", "&") or (m.text(u - 1) in ("(", ",") and m.text(u + 1) in (")", ",")):
                return None
        a, b = v.ctor[0]
        nd = m.parse_expr(a, b)
        if nd is not None and nd.type.base in ("iter",):
            return None
        return _sym_stable(m, v, _linear(m, nd))
    if t.base == "array" and t.num:
        return (None, t.num)
    return None


def _sym_stable(m: Model, v: Var, lin):
    """Rozmiar symboliczny ma sens tylko, gdy zmienna-symbol nie zmienia się po deklaracji kontenera."""
    if lin is None or lin[0] is None or "." in lin[0]:
        return lin
    x = m.lookup(lin[0], v.idx)
    if x is None:
        return None
    after = v.idx
    if v.init:
        after = max(after, v.init[1])
    if v.type.dims:
        after = max(after, max((d.e for d in v.type.dims if d is not None), default=after))
    for u in x.uses:
        if u > after and access_mode(m, u) in ("write", "rw", "cin", "addr", "refarg"):
            return None
    return lin


def _all_index_nodes(m: Model, s: int, e: int):
    for st in m.sites:
        if st.node is None or st.s < s or st.e > e + 1:
            continue
        for nd in st.node.walk():
            if nd.kind == "index":
                yield st, nd


def _call_name(nd: Node) -> str:
    if nd.kind != "call" or not nd.kids:
        return ""
    f = nd.kids[0]
    if f.kind == "id":
        return f.name
    return ""


def _method(nd: Node):
    """(obiekt, nazwa_metody) dla wywołania metody."""
    if nd.kind == "call" and nd.kids and nd.kids[0].kind == "member":
        return _strip(nd.kids[0].kids[0]), nd.kids[0].name
    return None, ""


def _base_var(nd: Node | None) -> Var | None:
    nd = _strip(nd)
    while nd is not None and nd.kind in ("index", "member") and nd.kids:
        nd = _strip(nd.kids[0])
    if nd is not None and nd.kind == "id":
        return nd.var
    return None


def _fmt_num(v) -> str:
    try:
        v = int(v)
    except (TypeError, ValueError, OverflowError):
        return str(v)
    s = f"{abs(v):,}".replace(",", " ")
    return ("-" if v < 0 else "") + s


# =============================================================================================== reguły — przepełnienia
meta("overflow-int-mul", "Mnożenie intów przepełnia się przed konwersją do long long", "warning", "overflow",
     ["pit-overflow", "num-overflow", "num-int-types"],
     "Iloczyn dwóch intów jest liczony w typie int (max ≈ 2,1·10⁹), nawet gdy wynik trafia do long long.",
     "przepełnienie int mnożenie long long")
meta("overflow-int-sum", "Suma intów może się przepełnić przed konwersją do long long", "warning", "overflow",
     ["pit-overflow", "num-overflow"], "Suma dwóch intów ≈ 10⁹ przekracza zakres int, zanim trafi do long long.",
     "przepełnienie int suma")
meta("overflow-mod-mul", "Mnożenie modulo w typie int przepełnia się", "error", "overflow",
     ["math-modular", "pit-overflow", "num-overflow"],
     "a * b % MOD dla a, b < MOD ≈ 10⁹ daje iloczyn ~10¹⁸ — nie mieści się w int.", "mnożenie modulo przepełnienie")
meta("overflow-cast-late", "Rzutowanie na long long dopiero po mnożeniu", "error", "overflow",
     ["pit-overflow", "num-overflow"], "(long long)(a * b) rzutuje już przepełniony wynik.", "rzutowanie long long")
meta("shift-overflow", "Przesunięcie bitowe poza zakres typu", "error", "overflow",
     ["pit-overflow", "num-overflow", "tech-bitmask"],
     "1 << k dla k ≥ 31 przepełnia int — trzeba 1LL << k.", "przesunięcie bitowe 1LL")
meta("shift-int-wide", "1 << k liczone w int, a potrzebny jest long long", "warning", "overflow",
     ["pit-overflow", "num-overflow", "tech-bitmask"], "Literał 1 ma typ int, więc 1 << k działa tylko do k = 30.",
     "1LL << k")
meta("accumulate-int-init", "accumulate z początkową wartością 0 (int)", "error", "overflow",
     ["stl-algorithms", "pit-overflow", "num-overflow"],
     "Typ wyniku std::accumulate to typ trzeciego argumentu — 0 to int, więc suma się przepełnia/obcina.",
     "accumulate long long 0LL")
meta("int-max-plus", "Działanie na INT_MAX/INT_MIN przepełnia się", "error", "overflow",
     ["pit-overflow", "num-overflow"], "INT_MAX + 1 to przepełnienie (UB).", "INT_MAX przepełnienie")
meta("inf-overflow", "Dodawanie do „nieskończoności” = INT_MAX", "warning", "overflow",
     ["pit-overflow", "graph-dijkstra", "num-overflow"],
     "Gdy INF = INT_MAX, wyrażenie INF + w przepełnia się i staje się ujemne.", "INF INT_MAX dijkstra przepełnienie")
meta("int-literal-overflow", "Stała nie mieści się w typie int", "error", "overflow",
     ["num-int-types", "pit-overflow"], "Wartość > 2 147 483 647 przypisana do int.", "zakres int stała")
meta("const-int-overflow", "Przepełnienie w wyrażeniu stałym typu int", "error", "overflow",
     ["num-overflow", "pit-overflow"], "Wyrażenie złożone z samych stałych int przekracza zakres int.",
     "przepełnienie stałej")
meta("inf-0x3f-ll", "0x3f3f3f3f jako nieskończoność dla long long", "warning", "overflow",
     ["num-overflow", "pit-overflow"], "0x3f3f3f3f ≈ 1,06·10⁹ — dla sum w long long to często za mało.",
     "nieskończoność long long")
meta("inf-memset-mismatch", "memset(0x3f) na long long nie daje 0x3f3f3f3f", "error", "overflow",
     ["pit-memset", "num-overflow"], "memset bajtami 0x3f na long long daje 0x3f3f3f3f3f3f3f3f.", "memset 0x3f long long")
meta("narrowing-ll-to-int", "Wartość long long zapisywana do int", "warning", "overflow",
     ["num-int-types", "pit-overflow"], "Przypisanie wyniku typu long long do zmiennej int obcina wartość.",
     "konwersja long long do int")


def _int32_operand_small(m: Model, nd: Node) -> bool:
    """Czy operand jest „mały” (stała < 1000, zmienna pętli z małą stałą granicą, znak, bool)."""
    nd = _strip(nd)
    if nd is None:
        return True
    if isinstance(nd.value, (int, float)):
        return abs(nd.value) < 1000
    if nd.type.base in ("bool", "char", "schar", "uchar", "short", "ushort"):
        return True
    if nd.kind == "binary" and nd.op in ("-",) and nd.kids[0].type.base == "char":
        return True
    if nd.kind == "binary" and nd.op == "%":
        r = _strip(nd.kids[1])
        if r is not None and isinstance(r.value, (int, float)) and abs(r.value) <= 46340:
            return True
    if nd.kind == "binary" and nd.op in ("&",):
        r = _strip(nd.kids[1])
        if r is not None and isinstance(r.value, int) and 0 <= r.value <= 46340:
            return True
    if nd.kind == "id" and nd.var is not None and nd.var.loop is not None:
        lp = nd.var.loop
        if lp.bound is not None and isinstance(_strip(lp.bound).value, (int, float)) and abs(_strip(lp.bound).value) <= 46340:
            return True
    if nd.kind == "call" and _call_name(nd) in ("__builtin_popcount", "__builtin_popcountll", "__lg", "__builtin_ctz",
                                                 "__builtin_clz", "__builtin_ctzll", "__builtin_clzll"):
        return True
    return False


@check
def _check_overflow(ctx: Ctx):
    m = ctx.m

    def wide_name(t: CType) -> str:
        return tname(t) if t is not None else "long long"

    def visit(nd: Node, wide: str | None, cast_late: bool = False):
        if nd is None:
            return
        k = nd.kind
        if k == "assign":
            lhs, rhs = nd.kids
            lt = lhs.type
            w = None
            if lt.is_wide() or (lt.is_float() and False):
                w = f"przypisanie do `{_src(m, lhs)}` ({wide_name(lt)})"
            visit(lhs, None)
            visit(rhs, w)
            return
        if k == "cast":
            ct = nd.type
            inner = nd.kids[-1] if nd.kids else None
            if ct.is_wide() and inner is not None:
                visit(inner, f"rzutowanie na {tname(ct)}", cast_late=True)
            else:
                for c in nd.kids:
                    visit(c, None)
            return
        if k == "paren":
            visit(nd.kids[0], wide, cast_late)
            return
        if k == "ternary":
            visit(nd.kids[0], None)
            visit(nd.kids[1], wide)
            visit(nd.kids[2], wide)
            return
        if k == "unary" and nd.op in ("-", "+"):
            visit(nd.kids[0], wide, cast_late)
            return
        if k == "binary":
            a, b = nd.kids
            op = nd.op
            if op in ("+", "-", "*", "/", "%") and nd.type.is_wide():
                ww = wide or f"działanie z wartością typu {tname(nd.type)}"
                visit(a, ww)
                visit(b, ww)
                return
            if op in CMP_OPS and (a.type.is_wide() or b.type.is_wide()):
                visit(a, "porównanie z long long" if not a.type.is_wide() else None)
                visit(b, "porównanie z long long" if not b.type.is_wide() else None)
                return
            if op == "*" and nd.type.is_int32() and a.type.is_int32() and b.type.is_int32():
                if _is_const(nd):
                    if not (INT_MIN <= nd.value <= INT_MAX) and nd.type.base == "int":
                        ctx.report("const-int-overflow", a.e,
                                   f"`{_src(m, nd)}` = {_fmt_num(nd.value)} — to nie mieści się w int (max 2 147 483 647), "
                                   f"a obie stałe są typu int, więc wynik jest przepełniony jeszcze przed przypisaniem.",
                                   f"Dopisz sufiks LL do jednej ze stałych: `{_src(m, a)}LL * {_src(m, b)}`.")
                    return
                par = nd.parent
                while par is not None and par.kind == "paren":
                    par = par.parent
                if par is not None and par.kind == "binary" and par.op == "%" and _strip(par.kids[0]) is _strip_to(nd, par):
                    modn = _strip(par.kids[1])
                    mv = modn.value if modn is not None else None
                    big = isinstance(mv, (int, float)) and mv > 46341
                    named = modn is not None and modn.kind == "id" and re.fullmatch(r"(?i)mod\w*|m|p|md", modn.name or "")
                    if (big or named) and not _int32_operand_small(m, a) and not _int32_operand_small(m, b) \
                            and not par.type.is_wide():
                        ctx.report("overflow-mod-mul", a.e,
                                   f"`{_src(m, nd)}` jest liczone w int, zanim weźmiesz `% {_src(m, modn)}`. "
                                   f"Gdy oba czynniki są rzędu modułu (~10⁹), iloczyn ma ~10¹⁸ i nie mieści się w int "
                                   f"(max ≈ 2,1·10⁹) — wynik modulo jest wtedy śmieciowy.",
                                   f"Licz w long long: `1LL * {_src(m, a)} * {_src(m, b)} % {_src(m, modn)}` "
                                   f"(albo trzymaj wartości w long long).", confidence=0.9)
                        return
                if wide and not _int32_operand_small(m, a) and not _int32_operand_small(m, b):
                    rid = "overflow-cast-late" if cast_late else "overflow-int-mul"
                    if cast_late:
                        msg = (f"`{_src(m, nd)}` jest liczone w typie int, a rzutowanie na long long następuje dopiero "
                               f"PO mnożeniu — przepełniony wynik jest tylko rozszerzany. Dla wartości ~10⁵ × 10⁵ "
                               f"wynik 10¹⁰ > 2,1·10⁹.")
                        fix = f"Rzutuj czynnik, nie wynik: `(long long){_src(m, a)} * {_src(m, b)}`."
                    else:
                        msg = (f"Obie strony `{_src(m, nd)}` mają typ int ({tname(a.type)} × {tname(b.type)}), więc "
                               f"iloczyn jest liczony w int i dopiero potem trafia dalej ({wide}). Gdy czynniki są rzędu "
                               f"10⁵ (lub większe), wynik przekracza 2 147 483 647 — przepełnienie (UB, zwykle ujemny "
                               f"śmieć).")
                        fix = f"Wymuś long long już przy mnożeniu: `1LL * {_src(m, a)} * {_src(m, b)}` " \
                              f"albo `(long long){_src(m, a)} * {_src(m, b)}`."
                    big = _big_evidence(m, a) or _big_evidence(m, b)
                    ctx.report(rid, a.e, msg, fix, confidence=0.8 if big else 0.65,
                               severity="error" if (big or cast_late) else "warning")
                    return
            if op in ("+", "-") and nd.type.is_int32() and a.type.is_int32() and b.type.is_int32() and wide \
                    and not cast_late and wide.startswith("przypisanie") and nd.parent is None:
                aa, bb = _strip(a), _strip(b)
                if all(x is not None and x.kind in ("id", "index") and not _is_const(x) and x.type.base in ("int", "uint")
                       and not (x.kind == "id" and x.var is not None and x.var.loop is not None) for x in (aa, bb)):
                    if op == "+":
                        ctx.report("overflow-int-sum", a.e,
                                   f"`{_src(m, nd)}` to suma dwóch intów — jest liczona w int i dopiero potem trafia do "
                                   f"long long. Jeśli obie wartości mogą być rzędu 10⁹, suma (2·10⁹) przekracza "
                                   f"2 147 483 647.",
                                   f"`1LL * {_src(m, aa)} + {_src(m, bb)}` albo zadeklaruj te zmienne jako long long.",
                                   confidence=0.5)
            if op == "<<" and a.type.is_int32() and not (a.type.base in ("stream",)):
                av = _strip(a)
                bv = _strip(b)
                if av is not None and isinstance(av.value, int) and bv is not None and isinstance(bv.value, int):
                    width = 32
                    lim = 31 if a.type.base == "int" else 32
                    if bv.value >= lim or (a.type.base == "int" and (av.value << bv.value) > INT_MAX and bv.value < 64):
                        ctx.report("shift-overflow", a.e,
                                   f"`{_src(m, nd)}`: lewy operand `{_src(m, a)}` ma typ {tname(a.type)} ({width} bity), "
                                   f"więc przesunięcie o {bv.value} wychodzi poza zakres "
                                   f"({'UB' if bv.value >= width else 'wynik ujemny/przepełniony'}).",
                                   f"Użyj `1LL << {bv.value}` (long long, do 62 bitów) albo `1ULL << {bv.value}`.",
                                   confidence=0.95)
                        return
                elif av is not None and av.value == 1 and wide and not _is_const(b):
                    ctx.report("shift-int-wide", a.e,
                               f"`{_src(m, nd)}` jest liczone w int (literał 1 ma typ int), a wynik trafia dalej ({wide}). "
                               f"Dla k ≥ 31 to przepełnienie — maska przestaje działać dla wyższych bitów.",
                               f"`1LL << {_src(m, b)}`.", confidence=0.6)
                    return
            if op == "<<" and a.type.is_wide():
                av, bv = _strip(a), _strip(b)
                if av is not None and isinstance(av.value, int) and bv is not None and isinstance(bv.value, int) and bv.value >= 63:
                    ctx.report("shift-overflow", a.e,
                               f"`{_src(m, nd)}`: przesunięcie o {bv.value} bitów wychodzi poza 64-bitowy long long.",
                               "Przesunięcia ≥ 63 nie mieszczą się — rozważ __int128 albo inną reprezentację.")
            if op in ("+", "*", "-") and nd.type.base == "int":
                for x, y in ((a, b), (b, a)):
                    xs = _strip(x)
                    if xs is not None and xs.kind == "id" and xs.name in ("INT_MAX", "INT_MIN") or \
                            (xs is not None and xs.kind == "id" and "numeric_limits" in xs.qual):
                        ys = _strip(y)
                        if ys is not None and isinstance(ys.value, (int, float)) and ys.value == 0:
                            continue
                        bad = (op == "+" and xs.name == "INT_MAX") or (op == "-" and xs.name == "INT_MIN" and x is a) or op == "*"
                        if bad:
                            sev = "error" if _is_const(ys) else "warning"
                            ctx.report("int-max-plus", a.e,
                                       f"`{_src(m, nd)}` — wynik wychodzi poza zakres int (INT_MAX = 2 147 483 647). "
                                       f"To przepełnienie (UB): w praktyce dostaniesz liczbę ujemną.",
                                       "Licz w long long (`(long long)INT_MAX + 1`) albo użyj mniejszej „nieskończoności”, "
                                       "np. `INF = 1e9`, żeby INF + w się mieściło.", severity=sev)
                            break
            visit(a, None)
            visit(b, None)
            return
        if k == "call":
            fn = nd.kids[0]
            args = nd.kids[1:]
            name = _call_name(nd)
            obj, meth = _method(nd)
            if obj is not None and meth in ("push_back", "emplace_back", "push", "insert", "emplace", "push_front") \
                    and obj.type is not None:
                et = obj.type.value_type() if obj.type.base not in ("vector", "deque") else obj.type.elem()
                for a in args:
                    visit(a, f"wstawienie do kontenera {tname(et)}" if et.is_wide() else None)
                return
            fs = m.func_by_name.get(name) if name else None
            if fs:
                f = fs[0]
                for q, a in enumerate(args):
                    pt = f.params[q].type if q < len(f.params) else None
                    visit(a, f"argument `{f.params[q].name}` funkcji {name} ma typ {tname(pt)}" if pt is not None and pt.is_wide() and not pt.ref else None)
                return
            for c in nd.kids:
                visit(c, None)
            return
        for c in nd.kids:
            visit(c, None)

    for st in m.sites:
        if st.node is None:
            continue
        w = None
        if st.kind in ("init", "return") and st.dest is not None and st.dest.is_wide():
            w = (f"inicjalizacja `{st.var.name}` ({tname(st.dest)})" if st.kind == "init" and st.var
                 else f"zwracany typ {tname(st.dest)}")
            if st.kind == "init":
                w = "przypisanie: " + w
        visit(st.node, w)


def _big_evidence(m: Model, nd: Node) -> bool:
    """Czy wiadomo, że operand bywa duży (stała ≥ 46341, zmienna z taką wartością początkową)."""
    nd = _strip(nd)
    if nd is None:
        return False
    if isinstance(nd.value, (int, float)) and abs(nd.value) >= 46341:
        return True
    v = nd.var if nd.kind == "id" else None
    if v is not None and v.init_kind == "=" and v.init:
        iv = m.parse_expr(*v.init)
        iv = _strip(iv)
        if iv is not None and isinstance(iv.value, (int, float)) and abs(iv.value) >= 46341:
            return True
    return False


def _strip_to(nd: Node, par: Node) -> Node:
    """Zwraca nd (do porównania z _strip(par.kids[0]))."""
    return nd


@check
def _check_int_literal(ctx: Ctx):
    m = ctx.m
    for st in m.sites:
        nd = st.node
        if nd is None:
            continue
        targets = []
        if st.kind == "init" and st.dest is not None and st.var is not None:
            targets.append((st.dest, nd, st.var.name, nd.s))
        for x in nd.walk():
            if x.kind == "assign" and x.op == "=":
                targets.append((x.kids[0].type, x.kids[1], _src(m, x.kids[0]), x.kids[1].s))
        for dt, val, name, at in targets:
            if dt is None or not dt.is_int() or dt.base in ("ll", "ull", "i128", "u128", "bool"):
                continue
            v = _strip(val).value if _strip(val) is not None else None
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            lo, hi = (INT_MIN, INT_MAX) if dt.base == "int" else ((0, 2 ** 32 - 1) if dt.base == "uint" else (None, None))
            if lo is None:
                continue
            if not (lo <= v <= hi):
                ctx.report("int-literal-overflow", at,
                           f"`{name}` ma typ {tname(dt)} (zakres {_fmt_num(lo)} … {_fmt_num(hi)}), a przypisujesz mu "
                           f"{_src(m, val)} = {_fmt_num(v)}. Wartość zostanie obcięta/przepełniona — np. INF = 1e18 "
                           f"w int daje śmieci.",
                           f"Zmień typ na long long: `long long {name} = {_src(m, val)};`"
                           + (" (dla INF w int wystarczy 1e9)." if v > INT_MAX else ""), confidence=0.95)


@check
def _check_const_overflow_add(ctx: Ctx):
    """Stałe wyrażenia int z + i - wychodzące poza zakres (np. 2147483647 + 1)."""
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind == "binary" and nd.op in ("+", "-") and nd.type.base == "int" and _is_const(nd) \
                and isinstance(nd.value, int):
            a, b = nd.kids
            if a.type.base == "int" and b.type.base == "int" and not (INT_MIN <= nd.value <= INT_MAX):
                if nd.parent is not None and nd.parent.kind == "binary" and _is_const(nd.parent):
                    continue
                ctx.report("const-int-overflow", a.e,
                           f"`{_src(m, nd)}` = {_fmt_num(nd.value)} nie mieści się w int — przepełnienie w czasie "
                           f"kompilacji (wynik będzie ujemny).", "Użyj stałych long long (sufiks LL).")


@check
def _check_accumulate(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "call" or _call_name(nd) != "accumulate" or len(nd.kids) < 4:
            continue
        init = _strip(nd.kids[3])
        if init is None or init.type.base != "int" or not _is_const(init):
            continue
        first = _strip(nd.kids[1])
        cont_t = None
        obj, meth = _method(first) if first is not None else (None, "")
        if obj is not None and meth in ("begin", "cbegin", "rbegin"):
            cont_t = obj.type
        elif first is not None and first.kind == "id" and first.var is not None and first.var.type.dims:
            cont_t = first.var.type
        elif first is not None and first.kind == "binary" and _strip(first.kids[0]).kind == "id":
            vv = _strip(first.kids[0]).var
            cont_t = vv.type if vv is not None else None
        et = cont_t.elem() if cont_t is not None else None
        dest = None
        p = nd.parent
        while p is not None and p.kind == "paren":
            p = p.parent
        if p is None and st.kind == "init":
            dest = st.dest
        elif p is not None and p.kind == "assign":
            dest = p.kids[0].type
        bad_elem = et is not None and (et.is_wide() or et.is_float())
        bad_dest = dest is not None and (dest.is_wide() or dest.is_float())
        if bad_elem or bad_dest:
            want = "0.0" if (et is not None and et.is_float()) or (dest is not None and dest.is_float()) else "0LL"
            why = f"elementy mają typ {tname(et)}" if bad_elem else f"wynik trafia do {tname(dest)}"
            ctx.report("accumulate-int-init", nd.kids[3].s,
                       f"`accumulate(..., {_src(m, init)})` liczy sumę w typie trzeciego argumentu — tu `{_src(m, init)}` "
                       f"to int, a {why}. Suma jest więc obcinana do int (przepełnienie albo utrata części ułamkowej).",
                       f"Podaj wartość początkową właściwego typu: `accumulate(..., {want})`.", confidence=0.95)


@check
def _check_inf(ctx: Ctx):
    m = ctx.m
    inf_vars: dict[int, str] = {}

    def is_intmax(nd):
        nd = _strip(nd)
        return nd is not None and (nd.name == "INT_MAX" or (isinstance(nd.value, int) and nd.value in (INT_MAX, 0x7fffffff))) \
            and nd.type.base == "int"

    for v in m.vars:
        if v.type.base != "int":
            continue
        if v.init_kind == "=" and v.init and not v.type.dims:
            nd = m.parse_expr(*v.init)
            if is_intmax(nd):
                inf_vars[v.vid] = v.name
        if v.init_kind == "()" and len(v.ctor) >= 2 and v.type.base == "vector":
            pass
    for v in m.vars:
        if v.type.base == "vector" and v.init_kind == "()" and len(v.ctor) >= 2:
            nd = m.parse_expr(*v.ctor[1])
            if is_intmax(nd) or (nd is not None and nd.kind == "id" and nd.var is not None and nd.var.vid in inf_vars):
                et = v.type.elem()
                if et.base == "int":
                    inf_vars[v.vid] = v.name
    for st, nd in m.all_nodes():
        if nd.kind == "call" and _call_name(nd) in ("fill", "fill_n") and len(nd.kids) >= 4:
            val = nd.kids[-1]
            bv = _base_var(nd.kids[1])
            if bv is not None and (is_intmax(val) or (_strip(val).kind == "id" and _strip(val).var is not None and _strip(val).var.vid in inf_vars)):
                if bv.type.base == "int" or (bv.type.base == "vector" and bv.type.elem().base == "int"):
                    inf_vars[bv.vid] = bv.name
        if nd.kind == "assign" and nd.op == "=" and is_intmax(nd.kids[1]):
            bv = _base_var(nd.kids[0])
            if bv is not None and nd.kids[0].type.base == "int":
                inf_vars[bv.vid] = bv.name
    if not inf_vars:
        return
    for st, nd in m.all_nodes():
        if nd.kind != "binary" or nd.op != "+" or nd.type.base != "int":
            continue
        for x in nd.kids:
            bv = _base_var(x)
            xs = _strip(x)
            if bv is not None and bv.vid in inf_vars and xs is not None and xs.kind in ("index", "id"):
                if _guarded_inf(m, nd, bv.name):
                    continue
                ctx.report("inf-overflow", nd.kids[0].e,
                           f"`{bv.name}` bywa ustawiane na INT_MAX („nieskończoność”), a tu liczysz `{_src(m, nd)}`. "
                           f"Jeśli `{_src(m, xs)}` jest jeszcze nieskończone, suma przepełnia się do liczby ujemnej i "
                           f"porównanie typu `d[u] + w < d[v]` zaczyna dawać fałszywe „krótsze” ścieżki.",
                           "Użyj INF = 1e9 (int, gdy wagi są małe) albo 1e18 w long long, albo sprawdzaj "
                           f"`if ({_src(m, xs)} != INF)` przed dodawaniem.", confidence=0.55)
                break


def _guarded_inf(m: Model, nd: Node, name: str) -> bool:
    k = nd.s
    for kw, s, e, kwn in m.conds:
        if kw < k <= m.stmt_end(kw):
            txt = " ".join(m.text(q) for q in range(s, e))
            if "INT_MAX" in txt or "INF" in txt.upper().split() or re.search(r"\binf\b", txt, re.I) or "!=" in txt:
                return True
    f = m.func_at(k)
    if f is not None:
        body = " ".join(m.text(q) for q in range(f.body_s, f.body_e))
        if ". top (" in body or ". pop (" in body:
            return True          # Dijkstra z kolejką: zdejmowane wierzchołki mają skończony dystans
    return False


@check
def _check_inf_0x3f(ctx: Ctx):
    m = ctx.m
    ll_memset = set()
    for st, nd in m.all_nodes():
        if nd.kind == "call" and _call_name(nd) == "memset" and len(nd.kids) >= 4:
            bv = _base_var(nd.kids[1])
            val = _strip(nd.kids[2])
            if bv is not None and val is not None and val.value in (0x3f, 63) and bv.type.base in ("ll", "ull"):
                ll_memset.add(bv.vid)
    for v in m.vars:
        if v.type.is_wide() and not v.type.dims and v.init_kind == "=" and v.init:
            nd = _strip(m.parse_expr(*v.init))
            if nd is not None and nd.value == 0x3f3f3f3f:
                ctx.report("inf-0x3f-ll", v.idx,
                           f"`{v.name}` jest typu {tname(v.type)}, ale jako „nieskończoność” dostaje 0x3f3f3f3f ≈ "
                           f"1,06·10⁹. W zadaniach z long long (sumy wag do 10¹⁸) taka wartość jest za mała: prawdziwy "
                           f"wynik może być większy od „nieskończoności”.",
                           f"`const long long {v.name} = 0x3f3f3f3f3f3f3f3fLL;` (≈4,6·10¹⁸, a 2·INF wciąż się mieści) "
                           "albo `LLONG_MAX / 4`.", confidence=0.6)
    if ll_memset:
        for st, nd in m.all_nodes():
            if nd.kind == "binary" and nd.op in ("==", "!=", "<", ">", ">=", "<="):
                for x, y in (nd.kids, nd.kids[::-1]):
                    bv = _base_var(x)
                    ys = _strip(y)
                    if bv is not None and bv.vid in ll_memset and ys is not None and ys.value == 0x3f3f3f3f:
                        ctx.report("inf-memset-mismatch", nd.s,
                                   f"`{bv.name}` (long long) wypełniasz memset(..., 0x3f, ...), więc każdy element to "
                                   f"0x3f3f3f3f3f3f3f3f, a nie 0x3f3f3f3f. Porównanie `{_src(m, nd)}` nigdy nie zadziała.",
                                   "Porównuj z `0x3f3f3f3f3f3f3f3fLL` (zdefiniuj `const long long INF = 0x3f3f3f3f3f3f3f3fLL;`).",
                                   confidence=0.9)
                        break


@check
def _check_narrowing(ctx: Ctx):
    m = ctx.m
    for st in m.sites:
        nd = st.node
        if nd is None:
            continue
        pairs = []
        if st.kind == "init" and st.dest is not None and st.var is not None and st.var.init_kind == "=":
            pairs.append((st.dest, nd, st.var.name, st.var.idx))
        for x in nd.walk():
            if x.kind == "assign" and x.op in ("=", "+=", "-=", "*="):
                pairs.append((x.kids[0].type, x.kids[1], _src(m, x.kids[0]), x.s))
        for dt, val, name, at in pairs:
            if dt is None or dt.base != "int" or not dt.scalar():
                continue
            v = _strip(val)
            if v is None or not v.type.is_wide() or v.type.base not in ("ll", "i128") or _is_const(v):
                continue
            if v.kind in ("cast",) or (v.kind == "binary" and v.op in ("%", "/", ">>", "&")):
                continue
            if v.kind == "call" and _call_name(v) in ("min", "max", "__lg", "abs", "distance", "count", "llround", "lround"):
                continue
            if st.kind == "init" and st.var is not None and st.var.loop is not None:
                continue
            lin = _linear(m, v)
            if lin is not None:
                continue
            ctx.report("narrowing-ll-to-int", at,
                       f"`{_src(m, v)}` ma typ {tname(v.type)}, a zapisujesz go do `{name}` typu int — wartości powyżej "
                       f"2 147 483 647 zostaną obcięte bez ostrzeżenia.",
                       f"Zadeklaruj `{name}` jako long long albo, jeśli wiesz, że się mieści, rzutuj jawnie `(int)`.",
                       confidence=0.55)


# =============================================================================================== reguły — unsigned/size()
meta("size-minus-one", "v.size() - 1 przy pustym kontenerze", "warning", "ub",
     ["pit-unsigned", "stl-vector", "num-int-types"],
     "size() zwraca unsigned — dla pustego kontenera size() - 1 = 18446744073709551615.", "size() - 1 unsigned")
meta("signed-unsigned-loop", "Porównanie int z size() (signed vs unsigned)", "style", "style",
     ["pit-unsigned", "num-int-types"], "i < v.size() porównuje int z size_t; ujemne i zamienia się w ogromną liczbę.",
     "signed unsigned porównanie")
meta("unsigned-reverse-loop", "Pętla w dół na typie unsigned nigdy się nie kończy", "error", "logic",
     ["pit-unsigned", "num-int-types"], "Dla unsigned warunek i >= 0 jest zawsze prawdziwy.", "pętla unsigned i >= 0")


@check
def _check_size_minus(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "binary" or nd.op != "-":
            continue
        a, b = _strip(nd.kids[0]), _strip(nd.kids[1])
        obj, meth = _method(a) if a is not None else (None, "")
        if meth not in ("size", "length") or obj is None:
            continue
        if not nd.type.is_unsigned():
            continue
        par = nd.parent
        while par is not None and par.kind == "paren":
            par = par.parent
        in_cmp = par is not None and par.kind == "binary" and par.op in ("<", "<=", ">", ">=")
        in_loop_cond = st.kind == "cond" and st.stmt == "for"
        in_index = par is not None and par.kind == "index"
        if not (in_cmp or in_index):
            continue
        cont = _src(m, obj)
        if _mentions_empty_guard(m, nd.s, obj):
            continue
        if in_index and obj.kind == "id":
            continue
        ctx.report("size-minus-one", nd.s,
                   f"`{_src(m, nd)}`: size() zwraca typ bez znaku (size_t). Gdy `{cont}` jest pusty, `{cont}.size() - "
                   f"{_src(m, b)}` nie daje -1, tylko 18 446 744 073 709 551 615 — "
                   + ("pętla wykona się „w nieskończoność” i wyjdzie poza zakres." if in_loop_cond else
                      ("porównanie da odwrotny wynik niż zamierzony." if in_cmp else "indeks będzie ogromny.")),
                   f"Rzutuj na int: `(int){cont}.size() - {_src(m, b)}` albo przenieś odejmowanie: "
                   f"`i + {_src(m, b)} < {cont}.size()`.", confidence=0.6)


def _mentions_empty_guard(m: Model, k: int, obj: Node) -> bool:
    name = obj.name if obj.kind == "id" else None
    if not name:
        return False
    for kw, s, e, kwn in m.conds:
        if kw < k <= m.stmt_end(kw) and not (s <= k < e) and _mentions(m, s, e, name):
            txt = " ".join(m.text(q) for q in range(s, e))
            if "empty" in txt or "size" in txt:
                return True
    f = m.func_at(k)
    if f is not None:
        txt = " ".join(m.text(q) for q in range(f.body_s, k))
        if re.search(rf"\b{re.escape(name)} \. (empty|size) \( \) (==|<=|<|!) ", txt) or \
                re.search(rf"if \( ! {re.escape(name)} \. empty", txt) or re.search(rf"if \( {re.escape(name)} \. empty", txt):
            return True
    return False


@check
def _check_signed_unsigned(ctx: Ctx):
    m = ctx.m
    hits = []
    for lp in m.loops:
        if lp.kind != "for" or lp.bound is None or lp.var_obj is None:
            continue
        b = _strip(lp.bound)
        obj, meth = _method(b) if b is not None else (None, "")
        if meth in ("size", "length") and lp.var_obj.type.base in ("int", "ll") and lp.cmp in ("<", "<="):
            hits.append(lp)
    if hits:
        lp = hits[0]
        ctx.report("signed-unsigned-loop", lp.cond[0],
                   f"Warunek `{m.src(*lp.cond)}` porównuje liczbę ze znakiem z size() (bez znaku). Dla i ≥ 0 działa, "
                   f"ale gdy po lewej pojawi się wartość ujemna (np. `j - 1 < v.size()`), zostanie zamieniona na "
                   f"ogromną liczbę dodatnią i warunek się „odwróci”. Takich pętli w kodzie: {len(hits)}.",
                   "Pisz `i < (int)v.size()` albo `for (int i = 0, n = v.size(); i < n; i++)`.", confidence=0.4)
    # ujemne wartości porównywane z size()
    for st, nd in m.all_nodes():
        if nd.kind == "binary" and nd.op in ("<", "<=", ">", ">="):
            for x, y in (nd.kids, nd.kids[::-1]):
                xs, ys = _strip(x), _strip(y)
                obj, meth = _method(ys) if ys is not None else (None, "")
                if meth in ("size", "length") and xs is not None and xs.type.base in ("int", "ll") and \
                        (isinstance(xs.value, int) and xs.value < 0):
                    ctx.report("signed-unsigned-loop", nd.s,
                               f"`{_src(m, nd)}`: {xs.value} jest zamieniane na unsigned (ogromna liczba), więc "
                               f"porównanie z size() daje odwrotny wynik niż oczekujesz.",
                               f"`{_src(m, xs)} {nd.op} (int){_src(m, ys)}`.", severity="error", confidence=0.9)


@check
def _check_unsigned_reverse(ctx: Ctx):
    m = ctx.m
    for lp in m.loops:
        if lp.kind not in ("for", "while", "do") or lp.cond is None:
            continue
        cond = m.parse_expr(*lp.cond)
        cond = _strip(cond)
        if cond is None or cond.kind != "binary" or cond.op not in (">=", ">"):
            continue
        a, b = _strip(cond.kids[0]), _strip(cond.kids[1])
        if a is None or b is None or a.kind != "id" or not isinstance(b.value, (int, float)):
            continue
        if not ((cond.op == ">=" and b.value == 0) or (cond.op == ">" and b.value == -1)):
            continue
        t = a.type
        if not t.is_unsigned():
            continue
        dec = lp.step == -1 or any(m.text(u + 1) == "--" or m.text(u - 1) == "--" or
                                  (m.text(u + 1) == "-=") for u in (a.var.uses if a.var else []))
        if not dec:
            continue
        ctx.report("unsigned-reverse-loop", lp.cond[0],
                   f"`{a.name}` ma typ bez znaku ({tname(t)}), więc warunek `{_src(m, cond)}` jest ZAWSZE prawdziwy: "
                   f"po 0 następuje {('4 294 967 295' if t.base == 'uint' else '18 446 744 073 709 551 615')}. "
                   f"Pętla się nie kończy i wychodzi poza tablicę.",
                   f"Użyj typu ze znakiem: `for (int {a.name} = (int)n - 1; {a.name} >= 0; {a.name}--)`.",
                   confidence=0.95)


# =============================================================================================== reguły — logika
meta("assign-in-condition", "Przypisanie `=` zamiast porównania `==` w warunku", "warning", "logic",
     ["pit-assign-vs-compare", "cpp-operators"], "if (x = 5) przypisuje, a nie porównuje.", "= zamiast ==")
meta("uninit-local", "Zmienna lokalna użyta przed nadaniem wartości", "error", "ub",
     ["pit-uninitialized", "cpp-variables"], "Zmienne lokalne w C++ nie są zerowane — zawierają śmieci.",
     "niezainicjalizowana zmienna")
meta("uninit-local-array", "Lokalna tablica odczytywana bez wyzerowania", "error", "ub",
     ["pit-uninitialized", "cpp-arrays"], "Lokalne tablice (w funkcji) nie są zerowane — w przeciwieństwie do globalnych.",
     "tablica niezainicjalizowana memset")
meta("missing-return", "Brak return w funkcji zwracającej wartość", "error", "ub",
     ["pit-missing-return", "cpp-functions"], "Wyjście z funkcji bez return to UB (przy -O2 dzieją się cuda).",
     "brak return w funkcji")
meta("missing-return-path", "Nie każda ścieżka funkcji kończy się return", "warning", "ub",
     ["pit-missing-return", "cpp-functions"], "Jeśli wykonanie dojdzie do końca funkcji bez return, wynik jest śmieciem.",
     "control reaches end of non-void function")
meta("comparator-non-strict", "Komparator z <= lub >= (łamie ścisły porządek)", "error", "ub",
     ["pit-comparator", "stl-sort", "stl-algorithms"],
     "sort/set wymagają ścisłego porządku: cmp(a, a) musi być false. Z <= sort może wyjść poza tablicę.",
     "komparator sort <= strict weak ordering")
meta("chained-comparison", "Łańcuch porównań a < b < c", "error", "logic", ["cpp-operators"],
     "a < b < c to (a < b) < c, czyli porównanie 0/1 z c.", "a < b < c")
meta("bitwise-precedence", "Priorytet operatorów bitowych: x & 1 == 0", "error", "logic",
     ["tech-bitmask", "cpp-operators"], "== ma wyższy priorytet niż &, |, ^.", "priorytet & ==")
meta("xor-as-power", "^ to XOR, nie potęgowanie", "error", "logic", ["cpp-operators", "num-sqrt-pow"],
     "W C++ 10^9 = 3 (XOR bitowy).", "potęga ^ xor")
meta("logical-const", "Warunek zawsze prawdziwy (x == 'a' || 'b')", "error", "logic", ["cpp-operators"],
     "Niezerowa stała w || jest zawsze prawdą.", "warunek zawsze prawdziwy ||")
meta("comma-in-index", "Przecinek w indeksie tablicy a[i, j]", "error", "logic", ["cpp-arrays", "stl-vector-2d"],
     "a[i, j] to a[j] (operator przecinka) — dwuwymiarowo pisze się a[i][j].", "a[i][j] przecinek")
meta("empty-body", "Średnik tuż po if/for/while", "warning", "logic", ["cpp-control-flow"],
     "if (...); kończy instrukcję — blok poniżej wykonuje się zawsze.", "średnik po if")
meta("switch-fallthrough", "Brak break w case", "warning", "logic", ["cpp-control-flow"],
     "Bez break wykonanie „przelatuje” do następnego case.", "switch break")
meta("recursion-no-base", "Rekurencja bez warunku stopu", "error", "logic", ["tech-recursion", "pit-stack-overflow"],
     "Funkcja wywołuje samą siebie bezwarunkowo — nieskończona rekurencja i przepełnienie stosu.",
     "rekurencja warunek stopu")
meta("float-equality", "Porównanie liczb zmiennoprzecinkowych przez ==", "warning", "logic",
     ["num-floating", "pit-float-compare"], "0.1 + 0.2 != 0.3 — porównuj z epsilonem.", "porównanie double epsilon")
meta("int-division-double", "Dzielenie całkowite tam, gdzie chodzi o ułamek", "warning", "logic",
     ["num-floating", "num-int-types"], "a / b dla intów obcina część ułamkową, zanim wynik trafi do double.",
     "dzielenie całkowite double")
meta("int-div-literal", "1/2 to 0 (dzielenie całkowite)", "error", "logic", ["num-floating"],
     "Dzielenie dwóch stałych całkowitych daje wynik całkowity.", "1/2 = 0")
meta("negative-modulo", "Modulo z liczby ujemnej", "warning", "logic", ["math-modular", "num-int-types"],
     "W C++ (-3) % 5 = -3, a nie 2.", "modulo ujemne")


@check
def _check_assign_cond(ctx: Ctx):
    m = ctx.m
    for st in m.sites:
        if st.kind != "cond" or st.node is None:
            continue
        nodes = [st.node]
        if st.node.kind == "binary" and st.node.op in ("&&", "||"):
            nodes = list(st.node.kids)
        for nd in nodes:
            if nd.kind == "assign" and nd.op == "=" and not nd.paren:
                rhs = _strip(nd.kids[1])
                const = _is_const(rhs)
                ctx.report("assign-in-condition", nd.kids[0].e,
                           f"W warunku `{st.stmt}` jest `{_src(m, nd)}` — to PRZYPISANIE (zmienia `{_src(m, nd.kids[0])}`), "
                           f"a warunek jest prawdziwy, gdy przypisana wartość ≠ 0"
                           + (f" (tu zawsze {'prawda' if rhs.value else 'fałsz'})." if const else ".") +
                           " Prawie na pewno chodziło o porównanie `==`.",
                           f"`{_src(m, nd.kids[0])} == {_src(m, nd.kids[1])}`. Jeśli przypisanie jest celowe, weź je w "
                           "dodatkowe nawiasy: `if ((x = f()))`.", severity="error" if const else "warning",
                           confidence=0.9 if const else 0.7)


def _first_use_report(ctx: Ctx, v: Var):
    m = ctx.m
    uses = sorted(v.uses)
    if not uses:
        return
    for k in uses:
        f = m.func_at(k)
        if f is not v.func:
            return                     # pierwsze użycie w lambdzie — nie oceniamy
        mode = access_mode(m, k)
        if mode in ("none",):
            continue
        if mode in ("cin", "addr", "refarg"):
            return
        if mode == "write":
            end = m.stmt_end(k)
            rhs_uses = [u for u in uses if k < u <= end]
            if rhs_uses and access_mode(m, rhs_uses[0]) == "read" and m.text(k + 1) == "=":
                ctx.report("uninit-local", rhs_uses[0],
                           f"`{v.name}` nie ma wartości początkowej, a w `{m.src(k, end)}` odczytujesz jego "
                           f"(śmieciową) wartość po prawej stronie przypisania. Lokalne zmienne w C++ nie są zerowane — "
                           f"wynik zależy od przypadkowej zawartości pamięci (na sprawdzarce może być inny niż u Ciebie).",
                           f"Nadaj wartość przy deklaracji, np. `{tname(v.type)} {v.name} = 0;` "
                           f"(dla maksimum: `= INT_MIN` / `= LLONG_MIN`, dla minimum: `= INT_MAX`).", confidence=0.85)
            return
        # read / rw
        what = m.src(_stmt_start(m, k), m.stmt_end(k) + (0 if m.text(m.stmt_end(k)) == ";" else 1)).rstrip(";").strip()
        if len(what) > 80:
            what = what[:77] + "…"
        ctx.report("uninit-local", k,
                   f"`{v.name}` (linia {v.line}) nie ma wartości początkowej, a tutaj jest {'modyfikowane' if mode == 'rw' else 'odczytywane'}"
                   f" (`{what}`), zanim cokolwiek do niego przypiszesz. Zmienne lokalne NIE są zerowane — zawierają "
                   f"śmieci (tylko globalne mają 0). Program może działać u Ciebie i dawać zły wynik na sprawdzarce.",
                   f"`{tname(v.type)} {v.name} = 0;` przy deklaracji (linia {v.line}).", confidence=0.85)
        return


def _first_use_array(ctx: Ctx, v: Var):
    m = ctx.m
    uses = sorted(v.uses)
    for k in uses:
        f = m.func_at(k)
        if f is not v.func:
            return
        prv = m.text(k - 1)
        if prv == "(" and m.text(k - 2) in ("memset", "fill", "fill_n", "iota", "sizeof", "memcpy", "copy", "generate",
                                            "fread", "scanf", "gets", "fgets", "cin", "read"):
            return
        if prv == "," and _call_before(m, k) in ("copy", "memcpy", "scanf", "sscanf", "fill", "iota"):
            return
        if m.text(k + 1) != "[":
            if prv in ("(", ",") and m.text(k + 1) in (")", ",", "+"):
                fn = _call_before(m, k)
                if fn in ("sort", "max_element", "min_element", "accumulate", "lower_bound", "upper_bound",
                          "count", "find", "reverse", "unique", "printf", "puts", "strlen", "next_permutation"):
                    pass
                else:
                    return               # przekazana do funkcji — mogła ją wypełnić
            if m.text(k + 1) in (".", "->"):
                return
            if prv == ">>" or prv == "&":
                return
            continue
        mode = access_mode(m, k)
        if mode in ("write", "cin", "addr", "refarg"):
            if mode == "write" and len(v.type.dims) == 1:
                return
            if mode == "write":
                return
            return
        if mode == "none":
            continue
        what = m.src(_stmt_start(m, k), m.stmt_end(k)).rstrip(";").strip()
        if len(what) > 80:
            what = what[:77] + "…"
        dims = "".join("[…]" for _ in v.type.dims)
        is_vla = any(d is not None and not _is_const(d) for d in v.type.dims)
        fix = (f"Wyzeruj przed użyciem: `memset({v.name}, 0, sizeof {v.name});` albo użyj `vector<{tname(v.type.elem()) if len(v.type.dims) == 1 else '…'}> "
               f"{v.name}(n)` (wektor jest zerowany)." if is_vla else
               f"Zadeklaruj z inicjalizacją: `{tname(CType(v.type.base, text=v.type.text))} {v.name}{dims} = {{}};` "
               f"(albo przenieś tablicę na zewnątrz funkcji — globalne są zerowane).")
        ctx.report("uninit-local-array", k,
                   f"Tablica `{v.name}` jest lokalna (linia {v.line}) i nie została wyzerowana, a tutaj "
                   f"{'zwiększasz/modyfikujesz' if mode == 'rw' else 'odczytujesz'} jej element (`{what}`). "
                   f"Lokalne tablice zawierają śmieci — liczniki zaczną od przypadkowych wartości.", fix, confidence=0.8)
        return


def _call_before(m: Model, k: int) -> str:
    q = k - 1
    while q >= 0:
        t = m.text(q)
        if t == ")" and q in m.pairs:
            q = m.pairs[q] - 1
            continue
        if t == "(":
            return m.text(q - 1)
        if t in (";", "{", "}"):
            return ""
        q -= 1
    return ""


@check
def _check_uninit(ctx: Ctx):
    m = ctx.m
    for v in m.vars:
        if v.kind != "local" or v.has_init or v.is_static or v.structured or v.loop is not None:
            continue
        t = v.type
        if t.dims:
            if t.base in INT_SIZES or t.base in FLOAT_SIZES:
                _first_use_array(ctx, v)
            continue
        if not (t.is_arith() or t.ptr) or t.ref:
            continue
        _first_use_report(ctx, v)


def _top_statements(m: Model, s: int, e: int) -> list[tuple]:
    out = []
    i = s
    guard = 0
    while i < e and guard < 100000:
        guard += 1
        end = m.stmt_end(i, e)
        if end < i:
            end = i
        out.append((i, end))
        i = end + 1
    return out


def _terminates(m: Model, s: int, e: int) -> bool:
    t = m.text(s)
    if t in ("return", "throw"):
        return True
    if t == "{" and s in m.pairs:
        stmts = _top_statements(m, s + 1, m.pairs[s])
        return bool(stmts) and _terminates(m, *stmts[-1])
    if t == "if":
        j = s + 1
        if m.text(j) == "constexpr":
            j += 1
        if m.text(j) == "(" and j in m.pairs:
            be = m.stmt_end(m.pairs[j] + 1)
            if m.text(be + 1) == "else":
                return _terminates(m, m.pairs[j] + 1, be) and _terminates(m, be + 2, m.stmt_end(be + 2))
        return False
    if t in ("while", "for", "do"):
        hdr = " ".join(m.text(q) for q in range(s, min(e, s + 8)))
        if re.match(r"(while \( (1|true) \)|for \( ; ; \)|do )", hdr):
            body = (m.pairs[s + 1] + 1) if t != "do" else s + 1
            be = m.stmt_end(body)
            # break bezpośrednio w tej pętli (nie w zagnieżdżonej) — zachowawczo: jakikolwiek break
            return not any(m.text(q) == "break" for q in range(body, be + 1)) or t == "do" and False
        return False
    if t == "switch":
        return True
    if t == "try":
        return True
    if t in ("exit", "abort", "_Exit", "quick_exit", "__builtin_unreachable", "assert", "terminate"):
        return True
    if t == "std" and m.text(s + 2) in ("exit", "abort", "terminate", "unreachable"):
        return True
    if t == "goto":
        return True
    return False


@check
def _check_missing_return(ctx: Ctx):
    m = ctx.m
    for f in m.funcs:
        if f.ret is None or f.name in ("main", "operator") or f.ret.base in ("void", "auto", "unknown") and not f.ret.ptr:
            if f.ret is None or f.ret.base in ("void", "auto") or (f.ret.base == "unknown" and not f.ret.text):
                continue
        if f.name == "main":
            continue
        if f.ret.base == "void" and not f.ret.ptr:
            continue
        if f.ret.base in ("auto",):
            continue
        nested = [lf for lf in m.lambdas if f.body_s < lf.body_s < f.body_e]
        has_ret = False
        for q in range(f.body_s + 1, f.body_e):
            if m.text(q) == "return" and not any(lf.body_s < q < lf.body_e for lf in nested):
                has_ret = True
                break
        stmts = _top_statements(m, f.body_s + 1, f.body_e)
        if not has_ret:
            if any(m.text(q) in ("throw", "exit", "abort", "assert") for q in range(f.body_s, f.body_e)):
                continue
            ctx.report("missing-return", f.name_idx,
                       f"Funkcja `{f.name}` deklaruje zwracany typ {tname(f.ret)}, ale nie ma w niej żadnego `return`. "
                       f"Wynik wywołania to śmieć, a przy -O2 kompilator może wygenerować kod, który się wysypuje albo "
                       f"wpada w nieskończoną pętlę (UB).",
                       f"Dodaj `return …;` na końcu funkcji albo zmień typ na `void`, jeśli nic nie zwraca.",
                       confidence=0.95)
            continue
        if not stmts or not _terminates(m, *stmts[-1]):
            last_line = m.T[f.body_e].line
            ctx.report("missing-return-path", f.body_e,
                       f"Funkcja `{f.name}` (typ {tname(f.ret)}) może dojść do końca (linia {last_line}) bez `return` — "
                       f"np. gdy żaden warunek przed końcem nie zajdzie. To niezdefiniowane zachowanie: zwrócona "
                       f"wartość jest śmieciem.",
                       "Dodaj końcowe `return …;` (np. wartość „nie znaleziono”), nawet jeśli „nigdy tam nie dojdzie”.",
                       confidence=0.6)


_SORTLIKE = {"sort": 3, "stable_sort": 3, "partial_sort": 4, "nth_element": 4, "lower_bound": 4, "upper_bound": 4,
             "binary_search": 4, "equal_range": 4, "max_element": 3, "min_element": 3, "minmax_element": 3,
             "merge": 6, "inplace_merge": 4, "make_heap": 3, "push_heap": 3, "pop_heap": 3, "sort_heap": 3,
             "is_sorted": 3, "unique": 3, "partial_sort_copy": 5}


def _comparator_problems(m: Model, f: Func) -> list[tuple]:
    """Lista (indeks, opis) problemów w komparatorze f."""
    if len(f.params) < 2:
        return []
    p, q = f.params[0].name, f.params[1].name
    out = []
    rets = [st for st in m.sites if st.kind == "return" and st.func is f and st.node is not None]

    def refs(nd, name):
        return any(x.kind == "id" and x.name == name for x in nd.walk())

    def scan(nd):
        nd2 = _strip(nd)
        if nd2 is None:
            return
        if nd2.kind == "binary" and nd2.op in ("&&", "||"):
            for k in nd2.kids:
                scan(k)
            return
        if nd2.kind == "ternary":
            scan(nd2.kids[1])
            scan(nd2.kids[2])
            return
        if nd2.kind == "binary" and nd2.op in ("<=", ">="):
            a, b = nd2.kids
            if (refs(a, p) and refs(b, q)) or (refs(a, q) and refs(b, p)):
                out.append((nd2.kids[0].e, f"`{_src(m, nd2)}`"))
        if nd2.kind == "unary" and nd2.op == "!":
            inner = _strip(nd2.kids[0])
            if inner is not None and inner.kind == "binary" and inner.op in ("<", ">"):
                out.append((nd2.s, f"`{_src(m, nd2)}` (to samo co >=/<=)"))
        if nd2.kind == "binary" and nd2.op == "-" and refs(nd2, p) and refs(nd2, q):
            out.append((nd2.s, f"`{_src(m, nd2)}` (różnica zamieniona na bool = „różne”)"))

    for st in rets:
        scan(st.node)
    if len(rets) >= 2:
        last = rets[-1]
        nd = _strip(last.node)
        if nd is not None and nd.kind == "num" and nd.value == 1 and nd.name == "true":
            out.append((last.s, "`return true;` na końcu (dla równych elementów zwraca prawdę)"))
    return out


@check
def _check_comparators(ctx: Ctx):
    m = ctx.m
    checked: set[int] = set()

    def report(f: Func, used_at: int, how: str):
        if id(f) in checked:
            return
        checked.add(id(f))
        for at, what in _comparator_problems(m, f):
            ctx.report("comparator-non-strict", at,
                       f"Komparator {how} zwraca {what}. sort/set/priority_queue wymagają ŚCISŁEGO porządku: dla równych "
                       f"elementów cmp(a, b) musi być false. Z <= / >= `sort` potrafi wyjść poza tablicę (crash/RE), "
                       f"`set` gubi lub dubluje elementy, a wynik jest niedeterministyczny.",
                       "Używaj tylko `<` albo `>`: np. `return a.x < b.x;`, a przy remisie porównuj kolejne pole "
                       "(`if (a.x != b.x) return a.x < b.x; return a.y < b.y;`). Na końcu `return false;`.",
                       confidence=0.9)

    lam_by_body = {lf.body_s: lf for lf in m.lambdas}
    for st, nd in m.all_nodes():
        if nd.kind != "call":
            continue
        name = _call_name(nd)
        if name not in _SORTLIKE:
            continue
        args = nd.kids[1:]
        if len(args) < 3 and name not in ("max_element", "min_element", "sort", "stable_sort", "is_sorted",
                                           "make_heap", "push_heap", "pop_heap", "sort_heap", "unique", "minmax_element"):
            continue
        if len(args) < 3:
            continue
        cmp = _strip(args[-1])
        if cmp is None:
            continue
        if cmp.kind == "lambda" and isinstance(cmp.value, int) and cmp.value in lam_by_body:
            report(lam_by_body[cmp.value], cmp.s, f"(lambda w `{name}`)")
        elif cmp.kind == "id":
            if cmp.var is not None and cmp.var.init:
                a, b = cmp.var.init
                for lf in m.lambdas:
                    if a <= lf.body_s <= b:
                        report(lf, cmp.s, f"`{cmp.name}`")
            for f in m.func_by_name.get(cmp.name, []):
                report(f, cmp.s, f"`{cmp.name}`")
        elif cmp.kind in ("call", "cast", "type", "id") or True:
            txt = _src(m, cmp)
            if re.match(r"(std::)?(less_equal|greater_equal)\b", txt):
                ctx.report("comparator-non-strict", cmp.s,
                           f"`{txt}` jako komparator to <= / >= — łamie ścisły porządek wymagany przez `{name}`.",
                           "Użyj `less<>()` albo `greater<>()`.", confidence=0.95)
    # operator< w strukturach i komparatory w set/map/priority_queue
    for f in m.funcs:
        if f.name == "operator":
            hdr = " ".join(m.text(q) for q in range(f.name_idx, f.name_idx + 3))
            if hdr.startswith("operator <") and not hdr.startswith("operator <<") and not hdr.startswith("operator <="):
                if len(f.params) == 1 and f.owner:
                    # metoda: porównanie this z parametrem
                    pass
                elif len(f.params) >= 2:
                    report(f, f.name_idx, "`operator<`")
            if hdr.startswith("operator ( )") and f.owner and len(f.params) >= 2:
                used = re.search(rf"\b{re.escape(f.owner)}\b", m.code.split("\n", f.end_line)[-1]) if f.owner else None
                if used:
                    report(f, f.name_idx, f"`{f.owner}::operator()`")
    for v in m.vars:
        t = v.type
        if t.base in ("set", "map", "mset", "mmap", "pq") and t.args:
            txt = t.text
            if re.search(r"\b(less_equal|greater_equal)\b", txt):
                ctx.report("comparator-non-strict", v.idx,
                           f"`{txt}` — komparator <= / >= w {('kolejce' if t.base == 'pq' else 'drzewie')} łamie ścisły "
                           f"porządek: set/map uznają element za różny od samego siebie (find/erase przestają działać).",
                           "Użyj `less<…>` / `greater<…>`; dla powtórzeń użyj `multiset`.", confidence=0.95)


@check
def _check_operator_precedence(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "binary":
            continue
        op = nd.op
        a, b = nd.kids
        if op in ("<", ">", "<=", ">=") and a.kind == "binary" and a.op in ("<", ">", "<=", ">=") and not a.paren \
                and a.type.base == "bool":
            x, y = a.kids
            ctx.report("chained-comparison", a.e,
                       f"`{_src(m, nd)}` NIE sprawdza, czy `{_src(m, y)}` leży między `{_src(m, x)}` a `{_src(m, b)}`. "
                       f"C++ liczy najpierw `({_src(m, a)})` → 0 albo 1, a potem porównuje tę liczbę z `{_src(m, b)}`.",
                       f"`{_src(m, x)} {a.op} {_src(m, y)} && {_src(m, y)} {op} {_src(m, b)}`.", confidence=0.9)
        if op in ("==", "!=") and a.kind == "binary" and a.op in ("==", "!=") and not a.paren:
            ctx.report("chained-comparison", a.e,
                       f"`{_src(m, nd)}` to `({_src(m, a)}) {op} {_src(m, b)}` — porównujesz wynik porównania (0/1) "
                       f"z `{_src(m, b)}`.", f"`{_src(m, a.kids[0])} == {_src(m, a.kids[1])} && {_src(m, a.kids[1])} == "
                       f"{_src(m, b)}`.", severity="warning", confidence=0.8)
        if op in ("&", "|", "^") and nd.type.base not in ("stream",):
            for kid, other in ((b, a), (a, b)):
                if kid.kind == "binary" and kid.op in ("==", "!=", "<", ">", "<=", ">=") and not kid.paren:
                    ctx.report("bitwise-precedence", kid.s,
                               f"`{_src(m, nd)}` jest liczone jako `{_src(m, other) if kid is b else '(' + _src(m, kid) + ')'} "
                               f"{op} {('(' + _src(m, kid) + ')') if kid is b else _src(m, other)}` — operator `{kid.op}` "
                               f"ma WYŻSZY priorytet niż `{op}`. Np. `x & 1 == 0` to `x & (1 == 0)` = `x & 0` = 0 (zawsze fałsz).",
                               f"Dodaj nawiasy: `({_src(m, other)} {op} {_src(m, kid.kids[0])}) {kid.op} {_src(m, kid.kids[1])}`"
                               if kid is b else f"Dodaj nawiasy wokół działania bitowego.", confidence=0.9)
                    break
        if op == "^":
            av = _strip(a)
            if av is not None and av.kind == "num" and av.value in (2, 10) and not nd.paren:
                bv = _strip(b)
                both = bv is not None and bv.kind == "num"
                if both or (bv is not None and bv.kind == "id"):
                    val = (av.value ^ bv.value) if both and isinstance(bv.value, int) else None
                    ctx.report("xor-as-power", a.e,
                               f"`{_src(m, nd)}` to XOR bitowy, nie potęgowanie"
                               + (f" — wynik to {val}, a nie {_fmt_num(av.value ** bv.value) if bv.value < 40 else 'potęga'}."
                                  if val is not None else "."),
                               ("Potęgę 10 zapisz jako stałą: `1e9` (double) albo `1000000000` / `(int)1e9`; potęgę 2: "
                                f"`1 << {_src(m, b)}` (`1LL << …` dla long long).") if av.value in (2, 10) else "",
                               severity="error" if both else "warning", confidence=0.9 if both else 0.6)
        if op in ("||", "&&"):
            for kid in (a, b):
                ks = _strip(kid)
                if ks is not None and ks.kind in ("num", "char", "str") and ks.name not in ("true", "false") and \
                        not (ks.kind == "num" and ks.value in (0, 1)):
                    other = b if kid is a else a
                    ctx.report("logical-const", ks.s,
                               f"W `{_src(m, nd)}` operand `{_src(m, ks)}` jest stałą różną od zera, więc "
                               + ("całe wyrażenie jest zawsze prawdziwe." if op == "||" else "nic nie wnosi.") +
                               f" Prawdopodobnie chodziło o `{_src(m, other).split('==')[0].strip()} == {_src(m, ks)}`.",
                               f"Powtórz porównanie: `x == 'a' || x == 'b'`.", confidence=0.85)


@check
def _check_comma_index(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind == "index":
            idx = nd.kids[1]
            if idx is not None and idx.kind == "binary" and idx.op == ",":
                ctx.report("comma-in-index", idx.s,
                           f"`{_src(m, nd)}`: wewnątrz [] przecinek działa jak operator „,” — liczy lewą stronę i ją "
                           f"wyrzuca, więc to jest `{_src(m, nd.kids[0])}[{_src(m, idx.kids[1])}]`.",
                           f"Tablica dwuwymiarowa: `{_src(m, nd.kids[0])}[{_src(m, idx.kids[0])}][{_src(m, idx.kids[1])}]`.",
                           confidence=0.95)


@check
def _check_empty_body(ctx: Ctx):
    m = ctx.m
    for kw, semi, kind in m.empty_bodies:
        nxt = m.text(semi + 1)
        t_kw, t_semi = m.T[kw], m.T[semi]
        same_line_block = nxt == "{"
        if kind == "if" or same_line_block:
            ctx.report("empty-body", semi,
                       f"Średnik zaraz po `{kind} (...)` kończy instrukcję — ciało {kind} jest PUSTE, a następny "
                       f"blok/instrukcja wykona się {'zawsze, niezależnie od warunku' if kind == 'if' else 'tylko raz, po pętli'}.",
                       f"Usuń średnik: `{kind} (...) {{ ... }}`.", confidence=0.85 if kind == "if" else 0.8)
        del t_kw, t_semi


@check
def _check_switch(ctx: Ctx):
    m = ctx.m
    for kw, bs, be in m.switches:
        cases = [c for c in m.cases if bs < c < be and m.func_at(c) is m.func_at(kw)]
        # pomiń case z zagnieżdżonych switchy
        nested = [(a, b) for k2, a, b in m.switches if bs < a and b <= be and k2 != kw]
        cases = [c for c in cases if not any(a < c < b for a, b in nested)]
        for prev, cur in zip(cases, cases[1:]):
            j = prev + 1
            while j < cur and not (m.text(j) == ":" and m.text(j - 1) != ":"):
                j += 1
            seg_s, seg_e = j + 1, cur
            if seg_s >= seg_e:
                continue
            stmts = _top_statements(m, seg_s, seg_e)
            if not stmts:
                continue
            ls, le = stmts[-1]
            txt = m.text(ls)
            if txt == "{" and ls in m.pairs:
                inner = _top_statements(m, ls + 1, m.pairs[ls])
                txt = m.text(inner[-1][0]) if inner else ""
            if txt in ("break", "return", "continue", "throw", "goto", "exit") or "fallthrough" in m.src(ls, le + 1):
                continue
            if any(m.text(q) == "fallthrough" for q in range(seg_s, seg_e)):
                continue
            ctx.report("switch-fallthrough", cur,
                       f"Przed `{m.src(cur, min(cur + 3, be))}` brakuje `break;` — po wykonaniu poprzedniego case program "
                       f"„przelatuje” dalej i wykonuje też ten.",
                       "Dodaj `break;` na końcu poprzedniego case (albo `[[fallthrough]];`, jeśli to celowe).",
                       confidence=0.7)


@check
def _check_recursion(ctx: Ctx):
    m = ctx.m
    for f in m.funcs:
        if f.name not in f.calls or f.name == "main":
            continue
        body = [m.text(q) for q in range(f.body_s + 1, f.body_e)]
        if any(t in ("if", "?", "switch", "while", "for", "&&", "||", "do", "and", "or") for t in body):
            continue
        ctx.report("recursion-no-base", f.name_idx,
                   f"`{f.name}` wywołuje samą siebie, ale w ciele nie ma żadnego warunku (if/?:/pętli), który by "
                   f"przerwał rekurencję. Każde wywołanie robi kolejne — skończy się przepełnieniem stosu "
                   f"(Segmentation fault / RE).",
                   f"Dodaj przypadek bazowy na początku, np. `if (n == 0) return …;`.", confidence=0.9)


@check
def _check_float_eq(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "binary" or nd.op not in ("==", "!="):
            continue
        a, b = _strip(nd.kids[0]), _strip(nd.kids[1])
        if a is None or b is None:
            continue
        if not (a.type.is_float() or b.type.is_float()):
            continue
        lit = [x for x in (a, b) if _is_const(x)]
        if lit and all(isinstance(x.value, (int, float)) and float(x.value).is_integer() for x in lit):
            continue
        ctx.report("float-equality", a.e,
                   f"`{_src(m, nd)}` porównuje liczby zmiennoprzecinkowe dokładnie. Przez błędy zaokrągleń "
                   f"(np. 0.1 + 0.2 = 0.30000000000000004) takie porównanie często daje fałsz, choć matematycznie "
                   f"wartości są równe.",
                   f"Porównuj z tolerancją: `fabs({_src(m, a)} - {_src(m, b)}) {'<' if nd.op == '==' else '>'} 1e-9` "
                   "albo licz na liczbach całkowitych (np. pomnóż przez 100).", confidence=0.65)


@check
def _check_int_division(ctx: Ctx):
    m = ctx.m

    def visit(nd, fctx):
        if nd is None:
            return
        k = nd.kind
        if k == "binary" and nd.op == "/" and nd.kids[0].type.is_int() and nd.kids[1].type.is_int():
            a, b = _strip(nd.kids[0]), _strip(nd.kids[1])
            if fctx:
                if _is_const(a) and _is_const(b) and isinstance(a.value, int) and isinstance(b.value, int) and b.value \
                        and a.value % b.value != 0:
                    ctx.report("int-div-literal", nd.kids[0].e,
                               f"`{_src(m, nd)}` to dzielenie dwóch liczb całkowitych — wynik to {int(a.value / b.value)}, "
                               f"a nie {a.value / b.value:g} ({fctx}).",
                               f"Zapisz jedną ze stałych jako double: `{a.value}.0 / {b.value}`.", confidence=0.95)
                elif not (_is_const(a) and _is_const(b)):
                    ctx.report("int-division-double", nd.kids[0].e,
                               f"`{_src(m, nd)}` — obie strony są całkowite ({tname(nd.kids[0].type)} / "
                               f"{tname(nd.kids[1].type)}), więc C++ dzieli całkowicie i obcina część ułamkową, a dopiero "
                               f"potem wynik trafia do double ({fctx}). Np. 7 / 2 = 3, nie 3.5.",
                               f"Wymuś dzielenie rzeczywiste: `(double){_src(m, a)} / {_src(m, b)}` albo "
                               f"`1.0 * {_src(m, a)} / {_src(m, b)}`.", confidence=0.7)
            visit(nd.kids[0], None)
            visit(nd.kids[1], None)
            return
        if k == "assign":
            lt = nd.kids[0].type
            visit(nd.kids[0], None)
            visit(nd.kids[1], f"przypisanie do {tname(lt)} `{_src(m, nd.kids[0])}`" if lt.is_float() else None)
            return
        if k == "binary" and nd.op in ("+", "-", "*", "/") and nd.type.is_float():
            for c in nd.kids:
                visit(c, fctx or "wyrażenie typu double")
            return
        if k == "cast" and nd.type.is_float():
            for c in nd.kids:
                visit(c, f"rzutowanie na {tname(nd.type)} dzieje się PO dzieleniu")
            return
        if k == "call" and _call_name(nd) in ("sqrt", "pow", "log", "log2", "exp", "sin", "cos", "ceil", "floor",
                                               "round", "cbrt", "sqrtl", "powl"):
            for c in nd.kids[1:]:
                visit(c, f"argument {_call_name(nd)}()")
            return
        if k in ("paren",):
            visit(nd.kids[0], fctx)
            return
        if k == "ternary":
            visit(nd.kids[0], None)
            visit(nd.kids[1], fctx)
            visit(nd.kids[2], fctx)
            return
        for c in nd.kids:
            visit(c, None)

    for st in m.sites:
        if st.node is None:
            continue
        f = None
        if st.kind in ("init", "return") and st.dest is not None and st.dest.is_float():
            f = (f"inicjalizacja {tname(st.dest)} `{st.var.name}`" if st.kind == "init" and st.var else
                 f"funkcja zwraca {tname(st.dest)}")
        visit(st.node, f)


@check
def _check_negative_mod(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "binary" or nd.op != "%":
            continue
        a, b = _strip(nd.kids[0]), _strip(nd.kids[1])
        if a is None or b is None or a.kind != "binary" or a.op != "-":
            continue
        if a.type.is_unsigned() or not a.type.is_int():
            continue
        sub_r = _strip(a.kids[1])
        if sub_r is None or _is_const(sub_r):
            continue
        modv = b.value
        named = b.kind == "id" and re.fullmatch(r"(?i)mod\w*|m|p|md|n", b.name or "")
        if not ((isinstance(modv, (int, float)) and modv >= 2) or named):
            continue
        # czy dalej dodajesz moduł i znów bierzesz modulo?
        par = nd.parent
        while par is not None and par.kind == "paren":
            par = par.parent
        if par is not None and par.kind == "binary" and par.op == "+":
            continue
        if par is not None and par.kind == "call" and _call_name(par) in ("abs",):
            continue
        ctx.report("negative-modulo", a.e,
                   f"`{_src(m, nd)}`: jeśli `{_src(m, a.kids[0])}` < `{_src(m, sub_r)}`, różnica jest ujemna, a w C++ "
                   f"wynik % ma znak dzielnej — np. (-3) % 5 = -3, a nie 2. Ujemny wynik zepsuje porównania, indeksy "
                   f"tablic i hasze.",
                   f"Dodaj moduł przed ostatnim modulo: `(({_src(m, a)}) % {_src(m, b)} + {_src(m, b)}) % {_src(m, b)}`.",
                   confidence=0.6)


# =============================================================================================== reguły — pamięć i tablice
meta("index-out-of-range", "Pętla wychodzi poza rozmiar tablicy", "error", "memory",
     ["pit-out-of-bounds", "cpp-arrays", "stl-vector"],
     "Indeks dochodzi do rozmiaru tablicy (lub dalej), a ostatni poprawny indeks to rozmiar − 1.",
     "wyjście poza tablicę")
meta("index-negative", "Indeks ujemny (np. a[i-1] dla i = 0)", "error", "memory",
     ["pit-out-of-bounds", "cpp-arrays"], "Przy pierwszym obrocie pętli indeks wynosi -1.", "indeks -1")
meta("vector-no-size-index", "Indeksowanie pustego vectora", "error", "memory", ["stl-vector", "pit-out-of-bounds"],
     "vector<int> v; ma rozmiar 0 — v[i] = x pisze poza pamięć. Trzeba v.resize(n), vector<int> v(n) albo push_back.",
     "vector bez rozmiaru")
meta("size-before-read", "Rozmiar tablicy ustalony przed wczytaniem n", "error", "memory",
     ["stl-vector", "pit-uninitialized"], "vector<int> v(n) przed cin >> n tworzy wektor o rozmiarze 0 lub śmieciowym.",
     "vector przed wczytaniem n")
meta("large-local-array", "Duża tablica lokalna (na stosie)", "warning", "memory", ["pit-stack-overflow", "cpp-arrays"],
     "Tablice w funkcji leżą na stosie (często 8 MB) — duże tablice deklaruj globalnie albo jako vector.",
     "duża tablica w main stack overflow")
meta("vla", "Tablica o zmiennej długości (VLA)", "warning", "memory", ["pit-vla", "stl-vector", "cpp-arrays"],
     "int a[n] to rozszerzenie GCC: leży na stosie i nie jest zerowane.", "tablica int a[n] vla")
meta("sizeof-array-param", "sizeof na parametrze-tablicy zwraca rozmiar wskaźnika", "error", "memory",
     ["pit-memset", "cpp-arrays"], "W funkcji `int a[]` to wskaźnik — sizeof(a) = 8.", "sizeof tablica parametr")
meta("memset-value", "memset z wartością innej niż 0 / -1", "error", "memory", ["pit-memset", "cpp-arrays"],
     "memset ustawia BAJTY — memset(a, 1, …) na int daje 16843009.", "memset 1")
meta("memset-size", "memset z rozmiarem w elementach zamiast w bajtach", "warning", "memory", ["pit-memset"],
     "Trzeci argument memset to liczba BAJTÓW.", "memset rozmiar bajty")
meta("memset-vector", "memset na obiekcie vector/string", "error", "memory", ["pit-memset", "stl-vector"],
     "memset nadpisuje wewnętrzne wskaźniki vectora — crash.", "memset vector")


@check
def _check_loop_bounds(ctx: Ctx):
    m = ctx.m
    for lp in m.loops:
        if lp.kind != "for" or not lp.var or lp.step not in (1, -1) or lp.cmp is None or lp.bound is None:
            continue
        var = lp.var
        vo = lp.var_obj
        # zmienna pętli modyfikowana w ciele → pomiń
        if vo is not None:
            body_uses = [u for u in vo.uses if lp.body[0] <= u <= lp.body[1]]
            if any(access_mode(m, u) in ("write", "rw", "cin", "addr", "refarg") for u in body_uses):
                continue
        bl = _linear(m, lp.bound)
        sl = _linear(m, lp.start) if lp.start is not None else None
        if lp.step == 1:
            if bl is None or lp.cmp not in ("<", "<=", "!="):
                hi = None
            else:
                hi = (bl[0], bl[1] - (1 if lp.cmp in ("<", "!=") else 0))
            lo = sl
        else:
            hi = sl
            lo = (bl[0], bl[1] + (1 if lp.cmp == ">" else 0)) if bl is not None and lp.cmp in (">", ">=") else None
        for st, nd in _all_index_nodes(m, lp.body[0], lp.body[1]):
            base = _strip(nd.kids[0])
            if base is None or base.kind != "id" or base.var is None:
                continue
            v = base.var
            if v.type.base == "string" and not v.type.dims:
                continue
            if v.type.base in ("map", "umap", "mmap", "ummap", "unknown", "auto", "struct") and not v.type.dims:
                continue
            il = _linear(m, nd.kids[1])
            if il is None or il[0] != var:
                continue
            off = il[1]
            size = _container_size(m, v)
            if v.kind == "param":
                size = None
            # górna granica
            if hi is not None and size is not None:
                if hi[0] == size[0] or (hi[0] is None and size[0] is None):
                    max_idx = hi[1] + off
                    if max_idx > size[1] - 1 and not _guarded(m, nd, var, lp):
                        sym = hi[0] or ""
                        idx_txt = (sym + (f" + {max_idx}" if max_idx > 0 else (f" - {-max_idx}" if max_idx < 0 else ""))) if sym else str(max_idx)
                        size_txt = (size[0] + (f" + {size[1]}" if size[1] > 0 else (f" - {-size[1]}" if size[1] < 0 else ""))) if size[0] else _fmt_num(size[1])
                        ctx.report("index-out-of-range", nd.s,
                                   f"Pętla `for` dochodzi do {var} = {_lin_txt(hi)} (warunek `{m.src(*lp.cond)}`), więc "
                                   f"`{_src(m, nd)}` odwołuje się do indeksu {idx_txt}. Tablica `{v.name}` ma rozmiar "
                                   f"{size_txt} (poprawne indeksy 0 … {size_txt} − 1). To wyjście poza tablicę — UB: "
                                   f"nadpisanie innych zmiennych albo crash.",
                                   f"Powiększ tablicę (np. `{v.name}[{size_txt} + 1]` / `vector<…> {v.name}({size_txt} + 1)`) "
                                   f"albo zmień warunek pętli (np. `{var} < {size_txt}`" + (f" przy indeksowaniu od 0)." if size[0] else ")."),
                                   confidence=0.85)
                        continue
            # dolna granica
            if lo is not None and lo[0] is None and lo[1] + off < 0 and not _guarded(m, nd, var, lp):
                ctx.report("index-negative", nd.s,
                           f"Dla {var} = {lo[1]} (pierwszy/ostatni obrót pętli) `{_src(m, nd)}` odwołuje się do indeksu "
                           f"{lo[1] + off} — przed początkiem tablicy `{v.name}`. To UB (czyta śmieci albo crash).",
                           f"Zacznij pętlę od {var} = {-off} albo dodaj warunek `if ({var} > 0)`; często wygodnie jest "
                           f"indeksować od 1 i mieć `{v.name}[0]` jako wartownika.", confidence=0.85)
    # indeks równy rozmiarowi poza pętlą: vector<int> v(n); ... v[n]
    for st, nd in m.all_nodes():
        if nd.kind != "index":
            continue
        base = _strip(nd.kids[0])
        if base is None or base.kind != "id" or base.var is None or base.var.kind == "param":
            continue
        v = base.var
        if v.type.base in ("string", "map", "umap", "mmap", "ummap", "unknown", "auto", "struct") and not v.type.dims:
            continue
        il = _linear(m, nd.kids[1])
        if il is None or il[0] is None or "." in il[0]:
            continue
        if any(lp.var == il[0] for lp in m.loops if lp.body[0] <= nd.s <= lp.body[1] or lp.hdr[0] < nd.s < lp.hdr[1]):
            continue
        size = _container_size(m, v)
        if size is None or size[0] != il[0] or il[1] < size[1]:
            continue
        x = m.lookup(il[0], nd.s)
        decl_end = max(v.idx, v.init[1] if v.init else v.idx)
        if x is None or any(decl_end < u < nd.s and access_mode(m, u) in ("write", "rw", "cin", "addr", "refarg") for u in x.uses):
            continue
        if _guarded(m, nd, il[0]):
            continue
        ctx.report("index-out-of-range", nd.s,
                   f"`{_src(m, nd)}`: `{v.name}` ma rozmiar {_lin_txt(size)}, więc ostatni poprawny indeks to "
                   f"{_lin_txt((size[0], size[1] - 1))}. Indeks {_lin_txt(il)} wychodzi poza tablicę (UB — śmieci albo crash).",
                   f"Ostatni element to `{v.name}[{_lin_txt((size[0], size[1] - 1))}]` (albo `{v.name}.back()`); jeśli "
                   f"indeksujesz od 1, utwórz `{v.name}` o rozmiarze {_lin_txt((size[0], size[1] + 1))}.", confidence=0.85)
    # stałe indeksy: int a[5]; a[5] = ...
    for st, nd in m.all_nodes():
        if nd.kind != "index":
            continue
        base = _strip(nd.kids[0])
        if base is None or base.kind != "id" or base.var is None or base.var.kind == "param":
            continue
        if not base.var.type.dims and base.var.type.base not in ("vector", "deque", "array"):
            continue
        idx = _strip(nd.kids[1])
        if idx is None or not isinstance(idx.value, (int, float)):
            continue
        size = _container_size(m, base.var)
        if size is None or size[0] is not None:
            continue
        iv = int(idx.value)
        if iv >= size[1] or iv < 0:
            ctx.report("index-out-of-range", nd.s,
                       f"`{_src(m, nd)}`: tablica `{base.name}` ma rozmiar {_fmt_num(size[1])}, więc poprawne indeksy to "
                       f"0 … {_fmt_num(size[1] - 1)}; indeks {_fmt_num(iv)} wychodzi poza nią.",
                       f"Powiększ tablicę do co najmniej {_fmt_num(iv + 1)} elementów.", confidence=0.9)


def _lin_txt(x) -> str:
    sym, c = x
    if sym is None:
        return _fmt_num(c)
    return sym + (f" + {c}" if c > 0 else (f" - {-c}" if c < 0 else ""))


@check
def _check_vector_no_size(ctx: Ctx):
    m = ctx.m
    grow = {"push_back", "emplace_back", "resize", "assign", "insert", "emplace", "swap", "push_front", "reserve_",
            "operator=", "swap"}
    for v in m.vars:
        t = v.type
        if t.base not in ("vector", "deque") or not t.scalar() or v.has_init or v.kind not in ("local", "global"):
            continue
        uses = sorted(v.uses)
        if not uses:
            continue
        bad = None
        ok = False
        for u in uses:
            nx = m.text(u + 1)
            if nx in (".", "->") and m.text(u + 2) in grow:
                ok = True
                break
            if nx == "=" or nx in ASSIGN_OPS:
                ok = True
                break
            if m.text(u - 1) in ("(", ",", "&") and nx in (")", ",") :
                ok = True                  # przekazany do funkcji (np. przez referencję)
                break
            if (m.text(u - 1) == ">>" or nx == ">>") and nx != "[":
                ok = True
                break
            if m.text(u - 1) == ":" :
                continue                   # for (x : v)
            if nx == "[" and bad is None:
                bad = u
        if ok or bad is None:
            continue
        ctx.report("vector-no-size-index", bad,
                   f"`{v.name}` jest zadeklarowany bez rozmiaru (`{tname(t)} {v.name};` w linii {v.line}) i nigdzie nie "
                   f"jest powiększany (push_back/resize/assign), a tu odwołujesz się do `{m.src(bad, m.pairs.get(bad + 1, bad + 1) + 1)}`. "
                   f"Pusty vector ma rozmiar 0 — każdy indeks to wyjście poza pamięć (zapis w losowe miejsce albo crash).",
                   f"Utwórz od razu z rozmiarem: `{tname(t)} {v.name}(n);` (po wczytaniu n) albo wywołaj "
                   f"`{v.name}.resize(n);` przed użyciem; ewentualnie dodawaj elementy przez `push_back`.",
                   confidence=0.9)


@check
def _check_size_before_read(ctx: Ctx):
    m = ctx.m
    for v in m.vars:
        t = v.type
        size_ranges = []
        if t.base in ("vector", "deque", "string") and t.scalar() and v.init_kind == "()" and v.ctor:
            size_ranges.append(v.ctor[0])
        elif t.dims:
            for d in t.dims:
                if d is not None and not _is_const(d):
                    size_ranges.append((d.s, d.e))
        if not size_ranges:
            continue
        for a, b in size_ranges:
            for q in range(a, b):
                if m.T[q].kind != "id":
                    continue
                x = m.lookup(m.text(q), q)
                if x is None or x is v or x.is_const or x.type.dims or not x.type.is_int():
                    continue
                if m.text(q - 1) in (".", "->") or m.text(q + 1) == "(":
                    continue
                before = [u for u in x.uses if u < v.idx]
                after_writes = [u for u in x.uses if u > b and access_mode(m, u) in ("cin", "write", "addr", "refarg")]
                if not after_writes:
                    continue
                if x.kind == "local" and not x.has_init and x.func is v.func:
                    if any(access_mode(m, u) in ("cin", "write", "addr", "refarg", "rw") for u in before):
                        continue
                    w = after_writes[0]
                    ctx.report("size-before-read", q,
                               f"`{v.name}` dostaje rozmiar `{m.src(a, b)}` w linii {v.line}, ale `{x.name}` jest "
                               f"wczytywane/ustawiane dopiero później (linia {m.T[w].line}). W chwili tworzenia "
                               f"`{v.name}` zmienna `{x.name}` ma śmieciową wartość — rozmiar jest przypadkowy (zwykle 0 "
                               f"albo gigantyczny → crash).",
                               f"Przenieś deklarację `{v.name}` za wczytanie `{x.name}` (za linię {m.T[w].line}).",
                               confidence=0.9)
                    break
                if x.kind == "global" and v.kind == "global" and not x.is_const:
                    w = after_writes[0]
                    if m.func_at(w) is None:
                        continue
                    ctx.report("size-before-read", q,
                               f"Globalny `{v.name}` jest tworzony przy starcie programu, gdy `{x.name}` ma jeszcze "
                               f"wartość {('początkową' if x.has_init else '0')} — wczytanie `{x.name}` w linii "
                               f"{m.T[w].line} już nie zmienia rozmiaru `{v.name}`.",
                               f"Zadeklaruj globalnie `{tname(t)} {v.name};` i po wczytaniu zrób `{v.name}.resize({m.src(a, b)});`"
                               if not t.dims else f"Użyj stałej z treści zadania: `const int MAXN = 1e6 + 5; {tname(CType(t.base))} {v.name}[MAXN];`.",
                               confidence=0.9)
                    break


@check
def _check_stack_arrays(ctx: Ctx):
    m = ctx.m
    recursive = {f.name for f in m.funcs if f.name in f.calls}
    for v in m.vars:
        t = v.type
        if v.kind != "local" or v.is_static or not t.dims:
            continue
        el = CType(t.base, t.args, t.ptr, False, False, [], t.text, t.num).sizeof()
        const_dims = [_strip(d).value if d is not None and _is_const(d) else None for d in t.dims]
        if all(isinstance(x, (int, float)) for x in const_dims) and el:
            total = el
            for x in const_dims:
                total *= max(1, int(x))
            fname = v.func.name if v.func else "?"
            if fname in recursive and total >= 4096:
                ctx.report("large-local-array", v.idx,
                           f"`{v.name}` ({_human_bytes(total)}) jest lokalną tablicą w rekurencyjnej funkcji `{fname}` — "
                           f"KAŻDE wywołanie rezerwuje ją na stosie od nowa. Przy głębokości rekurencji 10⁴–10⁵ stos "
                           f"się przepełni.", "Przenieś tablicę na zewnątrz funkcji (globalnie) albo użyj vectora.",
                           confidence=0.7)
            elif total >= 8 * 1024 * 1024:
                ctx.report("large-local-array", v.idx,
                           f"`{v.name}` zajmuje {_human_bytes(total)} i jest zadeklarowana w funkcji `{fname}`, więc leży "
                           f"na STOSIE. Domyślny stos w Linuksie/Windows to 1–8 MB — taki program wysypie się zaraz po "
                           f"starcie (Segmentation fault), choć u Ciebie może działać (np. z powiększonym stosem).",
                           f"Przenieś deklarację przed `main` (zmienne globalne nie leżą na stosie i są zerowane) albo "
                           f"użyj `vector`.", confidence=0.75)
            elif total >= 1024 * 1024:
                ctx.report("large-local-array", v.idx,
                           f"`{v.name}` zajmuje {_human_bytes(total)} na stosie (lokalnie w `{fname}`). Na OI stos jest "
                           f"zwykle równy limitowi pamięci, ale w wielu środowiskach to 1–8 MB.",
                           "Bezpieczniej zadeklarować dużą tablicę globalnie albo jako vector.", severity="info",
                           confidence=0.5)
        elif any(x is None for x in const_dims):
            is_vla = True
            for d in t.dims:
                if d is not None and _is_const(d):
                    continue
                if d is None:
                    is_vla = False
            if is_vla and v.init_kind != "array_param":
                dims_txt = "".join(f"[{_src(m, d)}]" for d in t.dims)
                ctx.report("vla", v.idx,
                           f"`{tname(CType(t.base, text=t.text))} {v.name}{dims_txt}` — rozmiar zależy od zmiennej, więc to "
                           f"VLA (Variable Length Array). To nie jest standard C++ (działa tylko w GCC/Clang), leży na "
                           f"stosie (dla dużego rozmiaru: crash) i nie jest zerowana (nie można napisać `= {{}}`).",
                           f"`vector<{tname(CType(t.base, text=t.text))}> {v.name}({_src(m, t.dims[0])});` "
                           f"(dla 2D: `vector<vector<…>> {v.name}(n, vector<…>(m))`) albo globalna tablica o rozmiarze z "
                           "treści zadania.", confidence=0.6)


def _human_bytes(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


@check
def _check_memset_sizeof(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind == "sizeof" and nd.kids:
            x = _strip(nd.kids[0])
            if x is not None and x.kind == "id" and x.var is not None and x.var.kind == "param" and \
                    (x.var.init_kind == "array_param" or (x.var.type.ptr and x.var.type.base in INT_SIZES)):
                ctx.report("sizeof-array-param", nd.s,
                           f"`{_src(m, nd)}`: `{x.name}` jest parametrem funkcji, więc to wskaźnik, a nie tablica — "
                           f"sizeof zwraca 8 (rozmiar wskaźnika), a nie rozmiar całej tablicy. memset/fill wyczyści "
                           f"tylko 2 inty.", f"Przekaż rozmiar osobno (`n * sizeof({x.name}[0])`) albo użyj vector/array.",
                           confidence=0.95)
        if nd.kind != "call" or _call_name(nd) != "memset" or len(nd.kids) < 4:
            continue
        tgt, val, sz = nd.kids[1], _strip(nd.kids[2]), _strip(nd.kids[3])
        ts = _strip(tgt)
        if ts is not None and ts.kind == "unary" and ts.op == "&" and ts.kids:
            ts = _strip(ts.kids[0])
        bv = _base_var(ts)
        if bv is not None and ts is not None and ts.kind in ("id", "unary") and bv.type.base in ("vector", "string",
                                                                                                 "map", "set") \
                and not bv.type.dims and not bv.type.ptr:
            ctx.report("memset-vector", nd.s,
                       f"`memset` na `{bv.name}` ({tname(bv.type)}) nadpisuje wewnętrzne pola obiektu (wskaźnik na dane, "
                       f"rozmiar) — to zepsuje vector i program się wysypie.",
                       f"`fill({bv.name}.begin(), {bv.name}.end(), 0);` albo `{bv.name}.assign({bv.name}.size(), 0);`.",
                       confidence=0.9)
            continue
        if bv is None:
            continue
        et = bv.type
        while et.dims or et.ptr:
            et = et.elem()
        if val is not None and isinstance(val.value, (int, float)) and et.base in ("int", "uint", "ll", "ull", "short",
                                                                                     "double", "float", "ldouble"):
            v = int(val.value)
            ok_vals = {0, -1, 255, 0x3f, 0x7f, 0x80, 0xc0, 0x3f3f3f3f, -0x80, 0xff, 0x3F, 0x7F}
            if et.base in ("double", "float", "ldouble"):
                ok_vals = {0, 0x7f, 0x3f, 0x43, 0xc0, 0xfe, 0x42, 127, -1, 0x7e, 0x80}
            if v not in ok_vals and not (val.kind == "char"):
                fill = {"int": "int", "uint": "unsigned", "ll": "long long", "ull": "unsigned long long",
                        "short": "short", "double": "double", "float": "float", "ldouble": "long double"}[et.base]
                byte = v & 0xff
                rep = int.from_bytes(bytes([byte]) * (et.sizeof() or 4), "little", signed=et.base in ("int", "ll", "short"))
                shown = _fmt_num(rep) if et.base not in ("double", "float", "ldouble") else "śmieciową liczbę"
                ctx.report("memset-value", nd.kids[2].s,
                           f"`memset({_src(m, tgt)}, {_src(m, val)}, …)` ustawia KAŻDY BAJT na {byte}, a `{bv.name}` "
                           f"przechowuje {fill} ({et.sizeof()} bajty). Każdy element będzie miał wartość {shown}, "
                           f"a nie {v}. memset działa poprawnie tylko dla 0 i -1 (oraz 0x3f na „nieskończoność”).",
                           f"`fill({_fill_range(m, bv)}, {v});` (albo pętla). Dla vectora: `vector<{fill}> v(n, {v});`.",
                           confidence=0.95)
        if sz is not None and not any(x.kind == "sizeof" for x in sz.walk()) and \
                et.base in ("int", "uint", "ll", "ull", "double", "float", "short"):
            if not _is_const(sz) or (isinstance(sz.value, (int, float)) and bv.type.dims and
                                      _container_size(m, bv) and sz.value == _container_size(m, bv)[1] and et.sizeof() > 1):
                ctx.report("memset-size", nd.kids[3].s,
                           f"Trzeci argument memset to liczba BAJTÓW, a `{_src(m, sz)}` wygląda na liczbę elementów. "
                           f"Dla {tname(et)} ({et.sizeof()} B) wyczyścisz tylko 1/{et.sizeof()} tablicy.",
                           f"`memset({_src(m, tgt)}, {_src(m, val)}, sizeof({bv.name}))` albo "
                           f"`({_src(m, sz)}) * sizeof({bv.name}[0])`.", confidence=0.7)


def _fill_range(m: Model, v: Var) -> str:
    if v.type.dims and len(v.type.dims) == 1:
        return f"{v.name}, {v.name} + {_src(m, v.type.dims[0])}"
    if v.type.dims:
        return f"&{v.name}{'[0]' * len(v.type.dims)}, &{v.name}{'[0]' * len(v.type.dims)} + sizeof({v.name}) / sizeof({v.name}{'[0]' * len(v.type.dims)})"
    return f"{v.name}.begin(), {v.name}.end()"


# =============================================================================================== reguły — wejście/wyjście
meta("endl-in-loop", "endl w pętli (wolne wypisywanie)", "perf", "perf", ["io-fast-io", "io-output"],
     "endl = '\\n' + flush: opróżnia bufor przy każdym wierszu — przy 10⁵ wierszy to sekundy.", "endl wolne")
meta("no-fast-io", "Brak szybkiego wejścia (sync_with_stdio)", "perf", "perf", ["io-fast-io"],
     "cin bez ios::sync_with_stdio(false) jest kilka razy wolniejszy przy dużym wejściu.", "szybkie wczytywanie cin")
meta("mixed-io-after-sync", "Mieszanie cin/cout z scanf/printf po sync_with_stdio(false)", "error", "io",
     ["io-fast-io", "io-printf-formats"],
     "Po wyłączeniu synchronizacji strumienie C i C++ mają osobne bufory — dane się rozjeżdżają.",
     "scanf cin sync_with_stdio")
meta("scanf-format", "Zły format w scanf", "error", "io", ["io-printf-formats"],
     "Specyfikator formatu musi pasować do typu zmiennej (%d — int, %lld — long long, %lf — double).",
     "scanf %lld long long")
meta("printf-format", "Zły format w printf", "error", "io", ["io-printf-formats"],
     "printf nie zna typów argumentów — zły specyfikator wypisuje śmieci.", "printf %lld")
meta("cin-eof-loop", "while (!cin.eof()) przetwarza ostatni element dwa razy", "warning", "io",
     ["io-read-until-eof", "io-input"], "eof() staje się prawdą dopiero PO nieudanym odczycie.", "while eof")
meta("getline-after-cin", "getline po cin >> wczytuje pustą linię", "warning", "io", ["io-getline"],
     "Po cin >> n w buforze zostaje znak nowej linii — getline go „zjada” i zwraca pusty napis.", "getline po cin")
meta("freopen-left", "freopen do pliku w rozwiązaniu", "warning", "io", ["io-freopen"],
     "Na OI dane przychodzą przez standardowe wejście — freopen na plik da puste wejście.", "freopen")
meta("double-print-precision", "Wypisywanie double bez setprecision", "warning", "io", ["io-precision"],
     "cout domyślnie wypisuje 6 cyfr znaczących (np. 1.23457e+06).", "setprecision fixed")


@check
def _check_io(ctx: Ctx):
    m = ctx.m
    code_toks = [t.text for t in m.T]
    has_sync = "sync_with_stdio" in code_toks
    sync_off = False
    sync_idx = None
    for k, t in enumerate(m.T):
        if t.text == "sync_with_stdio" and m.text(k + 1) == "(" and m.text(k + 2) in ("false", "0", "NULL"):
            sync_off = True
            sync_idx = k
    uses_cin = any(t.text == "cin" for t in m.T)
    uses_cout = any(t.text == "cout" for t in m.T)
    c_in = [k for k, t in enumerate(m.T) if t.text in ("scanf", "getchar", "gets", "fgets", "getchar_unlocked", "fread")
            and m.text(k + 1) == "(" and m.text(k - 1) not in (".", "->")]
    c_out = [k for k, t in enumerate(m.T) if t.text in ("printf", "puts", "putchar", "putchar_unlocked", "fwrite")
             and m.text(k + 1) == "(" and m.text(k - 1) not in (".", "->")]
    c_in = [k for k in c_in if not (m.text(k) == "fgets" and "stdin" not in m.src(k, k + 12))]
    if sync_off:
        if uses_cin and c_in:
            k = c_in[0]
            ctx.report("mixed-io-after-sync", k,
                       f"Po `ios::sync_with_stdio(false)` (linia {m.T[sync_idx].line}) cin i `{m.text(k)}` mają OSOBNE "
                       f"bufory. cin wczytuje dane „na zapas” do swojego bufora, więc `{m.text(k)}` dostanie dane z "
                       f"innego miejsca (albo żadne) — wejście się rozjeżdża.",
                       "Używaj tylko jednego zestawu: albo wszędzie cin (z sync_with_stdio(false)), albo wszędzie scanf "
                       "(wtedy usuń sync_with_stdio).", confidence=0.9)
        if uses_cout and c_out:
            k = c_out[0]
            ctx.report("mixed-io-after-sync", k,
                       f"Po `ios::sync_with_stdio(false)` (linia {m.T[sync_idx].line}) cout i `{m.text(k)}` piszą do "
                       f"OSOBNYCH buforów — kolejność wypisanych danych może się pomieszać (np. wynik printf pojawi się "
                       f"przed wcześniejszym cout).", "Wypisuj tylko przez cout albo tylko przez printf.",
                       confidence=0.85)
    # brak fast IO
    cin_in_loop = [k for k, t in enumerate(m.T) if t.text == "cin" and m.text(k + 1) == ">>" and m.in_loop(k)]
    if cin_in_loop and not has_sync and not c_in:
        k = cin_in_loop[0]
        ctx.report("no-fast-io", k,
                   "Wczytujesz dane w pętli przez cin, ale bez `ios::sync_with_stdio(false); cin.tie(nullptr);`. "
                   "Domyślnie cin jest zsynchronizowany ze stdio i kilka razy wolniejszy — przy 10⁵–10⁶ liczb samo "
                   "wczytywanie może zająć sekundę (TLE).",
                   "Na początku main dodaj: `ios::sync_with_stdio(false); cin.tie(nullptr);` (i nie używaj potem "
                   "scanf/printf).", confidence=0.7)
    # endl w pętli
    endl_loop = [k for k, t in enumerate(m.T) if t.text == "endl" and m.in_loop(k) and getattr(t, "macro", None) is None]
    if endl_loop:
        k = endl_loop[0]
        ctx.report("endl-in-loop", k,
                   f"`endl` w pętli wypisuje '\\n' I opróżnia bufor (flush) przy każdym wierszu — to wywołanie systemowe "
                   f"za każdym razem. Przy 10⁵+ wierszy wypisywanie potrafi trwać dłużej niż całe obliczenia. "
                   f"Miejsc w pętlach: {len(endl_loop)}.",
                   "Zamień `endl` na `'\\n'` (albo dodaj `#define endl '\\n'`).", confidence=0.8)
    # while(!cin.eof())
    for kw, s, e, kwn in m.conds:
        if kwn != "while":
            continue
        txt = " ".join(m.text(q) for q in range(s, e))
        if re.search(r"! (std :: )?cin \. eof \( \)|! feof \( stdin \)|(std :: )?cin \. good \( \)|! (std :: )?cin \. fail", txt):
            ctx.report("cin-eof-loop", s,
                       f"`while ({m.src(s, e)})` — flaga EOF ustawia się dopiero PO nieudanej próbie odczytu. Ostatni "
                       f"obrót pętli wykona się z nieudanym `cin >> x` (x zostaje stare albo 0), więc ostatni element "
                       f"zostanie policzony dwa razy.",
                       "Warunkiem pętli niech będzie sam odczyt: `while (cin >> x) { ... }` "
                       "(dla scanf: `while (scanf(\"%d\", &x) == 1)`).", confidence=0.85)
    # getline po cin >>
    for f in m.funcs:
        last_cin = None
        for k in range(f.body_s, f.body_e):
            t = m.text(k)
            if t == "cin" and m.text(k + 1) == ">>":
                last_cin = k
            elif t in ("ignore", "ws") and last_cin is not None:
                last_cin = None
            elif t == "getline" and m.text(k + 1) == "(" and m.text(k + 2) == "cin" and last_cin is not None:
                end = m.stmt_end(k)
                nxt = m.text(end + 1)
                if nxt == "getline" or (m.text(end + 1) in ("while", "for")):
                    last_cin = None
                    continue
                if m.text(k - 1) == "(" and m.text(k - 2) in ("while",):
                    last_cin = None
                    continue
                ctx.report("getline-after-cin", k,
                           f"Wcześniej (linia {m.T[last_cin].line}) czytasz `cin >> …`, który zostawia w buforze znak "
                           f"końca linii. Ten `getline` przeczyta właśnie tę pustą resztę linii — dostaniesz pusty "
                           f"napis zamiast następnego wiersza.",
                           "Przed getline dodaj `cin.ignore();` (albo `getline(cin >> ws, s);`, co pomija białe znaki).",
                           confidence=0.7)
                last_cin = None
    # freopen
    for k, t in enumerate(m.T):
        if t.text == "freopen" and m.text(k + 1) == "(":
            arg = m.text(k + 2)
            if arg.startswith('"') and arg not in ('""',):
                ctx.report("freopen-left", k,
                           f"`freopen({arg}, …)` przekierowuje wejście/wyjście do pliku. Na OI/sio2 dane przychodzą przez "
                           f"standardowe wejście, a plik {arg} nie istnieje — program przeczyta pustkę i dostanie 0 pkt.",
                           "Usuń freopen przed wysłaniem albo otocz go `#ifdef LOCAL ... #endif` i kompiluj lokalnie z "
                           "`-DLOCAL`.", confidence=0.6)
            break
    # double bez setprecision
    if not any(t.text in ("setprecision", "precision", "fixed", "printf") for t in m.T):
        for st, nd in m.all_nodes():
            if nd.kind == "binary" and nd.op == "<<" and nd.type.base == "stream":
                r = _strip(nd.kids[1])
                if r is not None and r.type.is_float() and not _is_const(r):
                    ctx.report("double-print-precision", nd.kids[1].s,
                               f"Wypisujesz liczbę rzeczywistą `{_src(m, r)}` bez ustawienia precyzji. cout pokazuje "
                               f"domyślnie 6 cyfr znaczących: 1234567.89 → 1.23457e+06, 0.333333333 → 0.333333 — "
                               f"sprawdzarka może uznać to za zły wynik.",
                               "Przed wypisaniem: `cout << fixed << setprecision(6);` (liczba miejsc z treści zadania).",
                               confidence=0.6)
                    break


_FMT_RE = re.compile(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d+))?(hh|h|ll|l|L|q|j|z|t|I64)?([diouxXeEfgGaAcspn%\[])")


def _fmt_class(t: CType) -> str | None:
    if t is None or not t.known():
        return None
    if t.ptr == 1 and t.base in ("char", "schar", "uchar"):
        return "cstr"
    if t.dims and len(t.dims) == 1 and t.base in ("char", "schar", "uchar"):
        return "cstr"
    if t.base == "cstr":
        return "cstr"
    if t.ptr or t.dims:
        return "ptr"
    if t.base in ("bool", "char", "schar", "uchar", "short", "ushort", "int", "uint"):
        return "int32"
    if t.base in ("ll", "ull"):
        return "int64"
    if t.base == "float":
        return "float"
    if t.base == "double":
        return "double"
    if t.base == "ldouble":
        return "ldouble"
    if t.base == "string":
        return "string"
    if t.base in ("i128", "u128"):
        return "int128"
    return None


@check
def _check_formats(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "call":
            continue
        name = _call_name(nd)
        if name not in ("scanf", "printf", "fprintf", "sprintf", "sscanf", "fscanf"):
            continue
        args = nd.kids[1:]
        skip = 1 if name in ("fprintf", "sprintf", "sscanf", "fscanf") else 0
        if len(args) <= skip:
            continue
        fmt_node = _strip(args[skip])
        if fmt_node is None or fmt_node.kind != "str":
            continue
        fmt = "".join(m.text(q)[1:-1] for q in range(fmt_node.s, fmt_node.e) if m.T[q].kind == "str")
        rest = args[skip + 1:]
        is_scan = "scanf" in name
        specs = []
        for mt in _FMT_RE.finditer(fmt):
            flags, width, prec, length, conv = mt.groups()
            if conv == "%":
                continue
            if is_scan and "*" in (flags or "") or (is_scan and fmt[mt.start() + 1:mt.start() + 2] == "*"):
                continue
            if not is_scan and width == "*":
                specs.append(("int32", "*", mt.group(0)))
            if not is_scan and prec == "*":
                specs.append(("int32", "*", mt.group(0)))
            specs.append((length or "", conv, mt.group(0)))
        if not specs:
            continue
        if len(rest) < len(specs):
            ctx.report("scanf-format" if is_scan else "printf-format", nd.s,
                       f"Format `\"{fmt}\"` ma {len(specs)} specyfikatorów, a podajesz tylko {len(rest)} argument(y). "
                       f"Brakujące wartości {name} weźmie ze śmieci na stosie"
                       + (" — zapis pod losowy adres, crash." if is_scan else "."),
                       "Dodaj brakujące argumenty albo usuń nadmiarowe % z formatu.", confidence=0.9)
        for (length, conv, spec), arg in zip(specs, rest):
            a = _strip(arg)
            if a is None:
                continue
            if is_scan:
                if conv in ("s", "[", "c") and a.type.base == "string" and not a.type.ptr:
                    ctx.report("scanf-format", a.s,
                               f"`{spec}` w scanf wymaga tablicy char, a `{_src(m, a)}` to std::string — scanf zapisze "
                               f"znaki w pamięć obiektu string (crash).", "Wczytuj stringi przez `cin >> s` albo do `char s[N]`.",
                               confidence=0.95)
                    continue
                if a.kind == "unary" and a.op == "&":
                    target = a.kids[0].type
                elif conv in ("s", "["):
                    continue
                else:
                    if a.type.is_arith() and not a.type.ptr and a.kind in ("id", "index", "member"):
                        ctx.report("scanf-format", a.s,
                                   f"scanf potrzebuje ADRESU zmiennej: `&{_src(m, a)}`. Bez & wartość `{_src(m, a)}` "
                                   f"zostanie potraktowana jako adres i scanf zapisze w losowe miejsce pamięci (crash).",
                                   f"`scanf(\"{fmt}\", &{_src(m, a)}…)`.", confidence=0.95)
                    continue
                cls = _fmt_class(target)
                want = _scan_want(length, conv)
                if cls is None or want is None or cls == want:
                    continue
                if want in ("int32",) and cls == "int32":
                    continue
                if conv == "c" and cls == "int32" and target.base in ("char", "schar", "uchar"):
                    continue
                if conv == "c" and cls == "int32":
                    ctx.report("scanf-format", a.s,
                               f"`%c` zapisuje 1 bajt, a `{_src(m, a.kids[0])}` to {tname(target)} — reszta bajtów "
                               f"zostaje śmieciowa.", "Wczytuj do zmiennej typu char.", confidence=0.8)
                    continue
                fix_spec = _scan_spec_for(cls)
                ctx.report("scanf-format", a.s,
                           f"`{spec}` oczekuje {_want_txt(want)}, a `{_src(m, a.kids[0])}` ma typ {tname(target)}. "
                           + ("scanf zapisze 8 bajtów do 4-bajtowej zmiennej — nadpisze sąsiednią pamięć."
                              if want == "int64" and cls == "int32" else
                              "scanf zapisze tylko część bajtów — reszta zostaje śmieciowa, wartość jest zła."
                              if want == "int32" and cls == "int64" else
                              "Bity liczby zostaną zinterpretowane jako inny typ — wynik to śmieci."),
                           f"Użyj `{fix_spec}`" + ("" if fix_spec else "") + ".", confidence=0.9)
            else:
                cls = _fmt_class(a.type)
                want = _print_want(length, conv)
                if cls is None or want is None or cls == want:
                    continue
                if want == "int32" and cls == "int64":
                    mt = _method(a)[1] if a.kind == "call" else ""
                    is_size = mt in ("size", "length", "count") or a.kind == "sizeof"
                    ctx.report("printf-format", a.s,
                               f"`{spec}` wypisuje int, a `{_src(m, a)}` ma typ {tname(a.type)} (8 bajtów). printf "
                               f"przeczyta tylko część bajtów — dla dużych wartości (a na niektórych platformach zawsze) "
                               f"wypisze śmieci, a kolejne argumenty się przesuną.",
                               f"Użyj `{'%zu' if is_size else '%lld'}`" + (" (albo rzutuj `(int)`)." if is_size else "."),
                               severity="warning" if is_size else "error", confidence=0.85 if not is_size else 0.6)
                elif want == "int64" and cls == "int32":
                    ctx.report("printf-format", a.s,
                               f"`{spec}` oczekuje long long, a `{_src(m, a)}` to {tname(a.type)} — printf przeczyta "
                               f"8 bajtów zamiast 4 (śmieci w wyższej połowie).", "Użyj `%d` albo rzutuj `(long long)`.",
                               severity="warning", confidence=0.7)
                elif want in ("double", "float") and cls in ("int32", "int64"):
                    ctx.report("printf-format", a.s,
                               f"`{spec}` oczekuje liczby rzeczywistej, a `{_src(m, a)}` jest całkowite — printf "
                               f"zinterpretuje bity jako double i wypisze 0.000000 albo śmieci.",
                               f"`printf(\"{spec}\", (double){_src(m, a)})`.", confidence=0.9)
                elif want in ("int32", "int64") and cls in ("double", "float", "ldouble"):
                    ctx.report("printf-format", a.s,
                               f"`{spec}` oczekuje liczby całkowitej, a `{_src(m, a)}` to {tname(a.type)} — wypisze "
                               f"śmieci (double nie jest automatycznie zamieniany na int).",
                               "Użyj `%.6f` (albo `%lf`) albo rzutuj na int.", confidence=0.9)
                elif want == "cstr" and cls == "string":
                    ctx.report("printf-format", a.s,
                               f"`%s` wymaga `const char*`, a `{_src(m, a)}` to std::string — printf wypisze śmieci "
                               f"albo się wysypie.", f"`{_src(m, a)}.c_str()`.", confidence=0.95)
                elif want == "ldouble" and cls == "double" or want == "double" and cls == "ldouble":
                    ctx.report("printf-format", a.s,
                               f"`{spec}` nie pasuje do typu {tname(a.type)} — long double wymaga `%Lf`, double `%f`.",
                               "Dopasuj specyfikator do typu (na Windows/MinGW long double w printf bywa niewspierany — "
                               "rzutuj na double).", severity="warning", confidence=0.7)


def _scan_want(length, conv):
    if conv in ("d", "i", "u", "x", "X", "o"):
        if length in ("ll", "I64", "q", "j", "z", "t"):
            return "int64"
        if length == "l":
            return "int64"
        if length in ("h", "hh"):
            return None
        return "int32"
    if conv in ("f", "e", "g", "E", "G", "a"):
        if length == "l":
            return "double"
        if length == "L":
            return "ldouble"
        return "float"
    if conv == "c":
        return "int32"
    return None


def _print_want(length, conv):
    if conv in ("d", "i", "u", "x", "X", "o"):
        if length in ("ll", "I64", "q", "j", "z", "t", "l"):
            return "int64"
        if length in ("h", "hh"):
            return None
        return "int32"
    if conv in ("f", "e", "g", "E", "G", "a", "F"):
        if length == "L":
            return "ldouble"
        return "double"
    if conv == "s":
        return "cstr"
    return None


def _scan_spec_for(cls):
    return {"int32": "%d", "int64": "%lld", "float": "%f", "double": "%lf", "ldouble": "%Lf"}.get(cls, "właściwego specyfikatora")


def _want_txt(w):
    return {"int32": "int", "int64": "long long", "float": "float", "double": "double", "ldouble": "long double",
            "cstr": "napisu char*"}.get(w, w)


# =============================================================================================== reguły — STL
meta("erase-in-loop", "erase(it) w pętli unieważnia iterator", "error", "stl", ["stl-iterators", "stl-set"],
     "Po erase(it) iterator `it` jest nieważny — it++ to UB. Pisz it = c.erase(it).", "erase iterator pętla")
meta("modify-in-range-for", "Modyfikacja kontenera w pętli for (x : v)", "error", "stl", ["stl-iterators", "stl-vector"],
     "push_back/erase w trakcie iteracji range-for unieważnia iteratory.", "push_back w pętli for auto")
meta("erase-index-skip", "erase w pętli po indeksach pomija element", "warning", "stl", ["stl-vector"],
     "Po v.erase(v.begin() + i) następny element ma indeks i, a pętla robi i++.", "erase pętla indeks")
meta("map-index-check", "m[x] do sprawdzania istnienia klucza", "perf", "stl", ["stl-map", "stl-unordered-map"],
     "m[x] wstawia brakujący klucz z wartością 0 — rośnie mapa, a iteracja widzi „fałszywe” klucze.",
     "map count find operator[]")
meta("strlen-in-loop", "strlen w warunku pętli (O(n²))", "perf", "perf", ["stl-string", "io-input"],
     "strlen liczy długość od nowa przy każdym obrocie pętli.", "strlen pętla")
meta("pass-by-value", "Duży kontener przekazany przez wartość", "perf", "perf", ["cpp-functions", "stl-vector"],
     "vector/string/map przez wartość jest kopiowany przy każdym wywołaniu.", "przekazywanie przez referencję")
meta("string-concat-loop", "s = s + c w pętli (O(n²))", "perf", "perf", ["stl-string"],
     "s = s + c tworzy nowy napis za każdym razem.", "string += konkatenacja")
meta("multitest-no-clear", "Multitest bez czyszczenia danych globalnych", "warning", "logic",
     ["oi-multitest", "stl-vector"], "Dane z poprzedniego testu zostają w globalnych kontenerach/tablicach.",
     "wiele testów czyszczenie")


@check
def _check_iter_invalidation(ctx: Ctx):
    m = ctx.m
    for lp in m.loops:
        if lp.kind == "for" and lp.init and lp.cond:
            cond = m.src(*lp.cond)
            mt = re.match(r"(\w+)\s*!=\s*(\w+)\.end\(\)", cond.replace(" ", "")) or \
                re.match(r"(\w+)!=(\w+)\.end\(\)", cond.replace(" ", ""))
            if mt and lp.incr:
                it, cont = mt.group(1), mt.group(2)
                incr = m.src(*lp.incr).replace(" ", "")
                if incr in (f"{it}++", f"++{it}"):
                    for k in range(lp.body[0], lp.body[1] + 1):
                        if m.text(k) == cont and m.text(k + 1) == "." and m.text(k + 2) == "erase" and \
                                m.text(k + 4) == it and m.text(k + 5) == ")":
                            prev = m.text(k - 1)
                            if prev == "=" and m.text(k - 2) == it:
                                continue
                            ctx.report("erase-in-loop", k,
                                       f"`{cont}.erase({it})` usuwa element, na który wskazuje `{it}` — od tej chwili `{it}` "
                                       f"jest nieważny, a pętla robi na nim `{incr}`. To UB: pominięte elementy, "
                                       f"nieskończona pętla albo crash.",
                                       f"Pisz `{it} = {cont}.erase({it});` i wtedy NIE zwiększaj `{it}` w tym obrocie "
                                       f"(`for (auto {it} = {cont}.begin(); {it} != {cont}.end(); ) {{ if (…) {it} = "
                                       f"{cont}.erase({it}); else ++{it}; }}`).", confidence=0.9)
        if lp.kind == "range" and lp.range_expr:
            a, b = lp.range_expr
            rng = _strip(m.parse_expr(a, b))
            if rng is None or rng.kind != "id" or rng.var is None:
                continue
            cv = rng.var
            for k in range(lp.body[0], lp.body[1] + 1):
                if m.text(k) == cv.name and m.lookup(cv.name, k) is cv and m.text(k + 1) == "." and \
                        m.text(k + 3) == "(":
                    meth = m.text(k + 2)
                    node_based = cv.type.base in ("set", "map", "mset", "mmap", "uset", "umap", "list")
                    if meth in ("push_back", "emplace_back", "insert", "erase", "pop_back", "clear", "resize",
                                "emplace", "push_front", "assign"):
                        if node_based and meth in ("insert", "emplace"):
                            sev = "warning"
                        else:
                            sev = "error"
                        ctx.report("modify-in-range-for", k,
                                   f"W pętli `for (… : {cv.name})` wywołujesz `{cv.name}.{meth}(…)`. Range-for trzyma "
                                   f"iteratory do `{cv.name}`; {meth} "
                                   + ("może przenieść dane vectora w inne miejsce pamięci (realokacja)" if not node_based
                                      else "usuwa/dodaje węzły, po których właśnie iterujesz") +
                                   " — dalsza iteracja to UB (śmieci, pominięte elementy, crash).",
                                   "Zbierz zmiany w osobnym kontenerze i zastosuj je po pętli albo iteruj po indeksach "
                                   "(`for (int i = 0; i < (int)v.size(); i++)`).", severity=sev, confidence=0.85)
                        break
        if lp.kind == "for" and lp.var and lp.step == 1 and lp.bound is not None:
            b = _strip(lp.bound)
            obj, meth = _method(b) if b is not None else (None, "")
            if meth != "size" or obj is None or obj.kind != "id":
                continue
            cont = obj.name
            body_txt = " ".join(m.text(k) for k in range(lp.body[0], lp.body[1] + 1))
            if re.search(rf"\b{re.escape(cont)} \. erase \( {re.escape(cont)} \. begin \( \) \+ {re.escape(lp.var)} \)", body_txt):
                if not re.search(rf"(--|-=) ?{re.escape(lp.var)}\b|\b{re.escape(lp.var)} (--|-=)", body_txt):
                    k = next(k for k in range(lp.body[0], lp.body[1] + 1) if m.text(k) == "erase")
                    ctx.report("erase-index-skip", k,
                               f"Po `{cont}.erase({cont}.begin() + {lp.var})` kolejny element przesuwa się na indeks "
                               f"{lp.var}, a pętla od razu robi {lp.var}++ — ten element zostanie pominięty. Do tego "
                               f"każde erase w środku vectora to O(n), razem O(n²).",
                               f"Po erase zrób `{lp.var}--;` albo użyj idiomu erase-remove: "
                               f"`{cont}.erase(remove_if({cont}.begin(), {cont}.end(), warunek), {cont}.end());`.",
                               confidence=0.75)


@check
def _check_map_index(ctx: Ctx):
    m = ctx.m
    for st in m.sites:
        if st.kind != "cond" or st.node is None:
            continue
        for nd in st.node.walk():
            if nd.kind != "index":
                continue
            base = _strip(nd.kids[0])
            if base is None or base.kind != "id" or base.var is None:
                continue
            t = base.var.type
            if t.base not in ("map", "umap") or t.dims:
                continue
            par = nd.parent
            while par is not None and par.kind == "paren":
                par = par.parent
            truthy = par is None or (par.kind == "unary" and par.op == "!") or (par.kind == "binary" and par.op in ("&&", "||"))
            cmp0 = par is not None and par.kind == "binary" and par.op in ("==", "!=", ">") and \
                any(_strip(x) is not None and _strip(x).value == 0 for x in par.kids if _strip(x) is not nd)
            if not (truthy or cmp0):
                continue
            iterated = any(lp.kind == "range" and lp.range_expr and m.src(*lp.range_expr) == base.name for lp in m.loops)
            sized = any(m.text(u + 1) == "." and m.text(u + 2) == "size" for u in base.var.uses)
            sev = "warning" if iterated or sized else "perf"
            ctx.report("map-index-check", nd.s,
                       f"`{_src(m, nd)}` w warunku: operator[] na mapie WSTAWIA brakujący klucz (z wartością 0). Każde "
                       f"takie sprawdzenie powiększa `{base.name}`"
                       + (" — a potem iterujesz po mapie / liczysz jej size(), więc widzisz „fałszywe” klucze."
                          if sev == "warning" else " (więcej pamięci i czasu)."),
                       f"Sprawdzaj istnienie przez `{base.name}.count({_src(m, nd.kids[1])})` albo "
                       f"`{base.name}.find(…) != {base.name}.end()` (C++20: `{base.name}.contains(…)`).",
                       severity=sev, confidence=0.7)


@check
def _check_perf(ctx: Ctx):
    m = ctx.m
    for lp in m.loops:
        if lp.cond is None:
            continue
        for k in range(*lp.cond):
            if m.text(k) == "strlen" and m.text(k + 1) == "(":
                ctx.report("strlen-in-loop", k,
                           f"`{m.src(*lp.cond)}` — strlen przechodzi cały napis przy KAŻDYM sprawdzeniu warunku. Dla "
                           f"napisu długości 10⁵ pętla robi 10¹⁰ operacji (TLE).",
                           "Policz długość raz przed pętlą: `int len = strlen(s); for (int i = 0; i < len; i++)` "
                           "(albo użyj std::string i s.size()).", confidence=0.9)
                break
    recursive = {f.name for f in m.funcs if f.name in f.calls}
    called_in_loop = set()
    for lp in m.loops:
        for k in range(lp.body[0], lp.body[1] + 1):
            if m.T[k].kind == "id" and m.text(k + 1) == "(":
                called_in_loop.add(m.text(k))
    for f in m.funcs:
        if f.name == "main" or f.is_lambda:
            continue
        for p in f.params:
            t = p.type
            if t.ref or t.ptr or t.dims or t.base not in HEAVY or t.base in ("pair",):
                continue
            if t.base == "string" and f.name not in recursive and f.name not in called_in_loop:
                continue
            modified = any(access_mode(m, u) in ("write", "rw", "refarg") or
                           (m.text(u + 1) == "." and m.text(u + 2) in ("push_back", "pop_back", "insert", "erase",
                                                                         "clear", "resize", "push", "pop"))
                           for u in p.uses)
            rec = f.name in recursive
            sev = "warning" if rec else "perf"
            ctx.report("pass-by-value", p.idx,
                       f"Parametr `{p.name}` ({tname(t)}) jest przekazywany przez WARTOŚĆ — przy każdym wywołaniu `{f.name}` "
                       f"cały kontener jest kopiowany (O(rozmiar))"
                       + (", a funkcja jest rekurencyjna: kopia na każdym poziomie rekurencji → TLE/MLE." if rec else
                          (", a wywołujesz ją w pętli." if f.name in called_in_loop else ".")),
                       (f"`const {tname(t)}& {p.name}` (tylko odczyt) albo `{tname(t)}& {p.name}`." if not modified else
                        f"Jeśli kopia nie jest potrzebna: `{tname(t)}& {p.name}`; jeśli modyfikujesz lokalną kopię — "
                        f"zostaw, ale rozważ trzymanie danych globalnie."),
                       severity=sev, confidence=0.75 if rec else 0.6)
    for st, nd in m.all_nodes():
        if nd.kind != "assign" or nd.op != "=" or not m.in_loop(nd.s):
            continue
        lhs, rhs = _strip(nd.kids[0]), _strip(nd.kids[1])
        if lhs is None or rhs is None or lhs.kind != "id" or lhs.type.base != "string":
            continue
        if rhs.kind == "binary" and rhs.op == "+":
            left_most = rhs
            while left_most.kind == "binary" and left_most.op == "+":
                left_most = _strip(left_most.kids[0])
            right = _strip(rhs.kids[1])
            if left_most is not None and left_most.kind == "id" and left_most.name == lhs.name:
                ctx.report("string-concat-loop", nd.s,
                           f"`{_src(m, nd)}` tworzy w każdym obrocie pętli NOWY napis (kopiuje całe `{lhs.name}`), więc "
                           f"budowanie napisu długości n kosztuje O(n²).",
                           f"`{lhs.name} += {_src(m, rhs)[len(lhs.name):].lstrip(' +')};` — dopisuje w miejscu, O(1) "
                           "zamortyzowane.", confidence=0.85)
            elif right is not None and right.kind == "id" and right.name == lhs.name:
                ctx.report("string-concat-loop", nd.s,
                           f"`{_src(m, nd)}` dokleja na POCZĄTEK — za każdym razem kopiuje cały napis (O(n²)).",
                           f"Dopisuj na koniec (`{lhs.name} += …`), a po pętli odwróć: `reverse({lhs.name}.begin(), "
                           f"{lhs.name}.end());`.", confidence=0.8)


_TC_NAMES = {"t", "T", "q", "Q", "z", "Z", "tc", "tests", "testy", "test", "tt", "_t", "cases", "ile", "zestawy", "d", "D"}


@check
def _check_multitest(ctx: Ctx):
    m = ctx.m
    # pętla po testach
    for lp in m.loops:
        if lp.kind not in ("while", "for"):
            continue
        cond = m.src(*lp.cond) if lp.cond else ""
        c = cond.replace(" ", "")
        mt = re.fullmatch(r"(\w+)--|(\w+)-->0", c)
        name = None
        if mt:
            name = mt.group(1) or mt.group(2)
        elif lp.kind == "for" and lp.bound is not None and lp.cmp in ("<", "<="):
            b = _strip(lp.bound)
            if b is not None and b.kind == "id":
                name = b.name
        if not name or name not in _TC_NAMES:
            continue
        v = m.lookup(name, lp.kw)
        if v is None or not any(access_mode(m, u) in ("cin", "addr", "refarg") for u in v.uses if u < lp.kw):
            continue
        body = lp.body
        if not _reads_size_in(m, lp):
            continue
        funcs_called = _reachable_funcs(m, body)
        ranges = [body] + [(f.body_s, f.body_e) for f in funcs_called]
        dirty: dict[int, int] = {}
        reset: set[int] = set()
        for a, b in ranges:
            for k in range(a, b + 1):
                t = m.T[k]
                if t.kind != "id":
                    continue
                g = m.lookup(t.text, k)
                if g is None or g.kind != "global" or g.is_const:
                    continue
                nx = m.text(k + 1)
                if nx == "." and m.text(k + 2) in ("clear", "assign", "swap") or (nx == "=" and m.text(k + 2) != "="):
                    reset.add(g.vid)
                    continue
                if m.text(k - 1) == "(" and m.text(k - 2) in ("memset", "fill", "fill_n", "iota") or \
                        (m.text(k - 1) == "," and _call_before(m, k) in ("fill", "fill_n")):
                    reset.add(g.vid)
                    continue
                if nx == "." and m.text(k + 2) == "resize" and not g.type.dims and g.type.args and \
                        g.type.args[0].base not in ("vector", "set", "map"):
                    reset.add(g.vid)
                    continue
                if nx == "[":
                    j = k + 1
                    while m.text(j) == "[" and j in m.pairs:
                        j = m.pairs[j] + 1
                    after = m.text(j)
                    if after == "." and m.text(j + 1) in ("clear",):
                        reset.add(g.vid)
                        continue
                    if after == "=" and m.text(j + 1) in ("0", "false", "{", "-", "INF", "inf") or \
                            (after == "=" and m.lookup(m.text(j + 1), j) is not None and m.text(j + 2) == ";"):
                        if m.in_loop(k) and any(lp2.body[0] <= k <= lp2.body[1] for lp2 in m.loops if lp2 is not lp):
                            reset.add(g.vid)
                            continue
                    if after in ("++", "+=", "|=", "-=", "--") or m.text(k - 1) in ("++", "--"):
                        dirty.setdefault(g.vid, k)
                        continue
                    if after == "=" and m.text(j + 1) in ("true", "1") and g.type.dims:
                        dirty.setdefault(g.vid, k)
                        continue
                    if after == "." and m.text(j + 1) in ("push_back", "emplace_back", "insert", "push"):
                        dirty.setdefault(g.vid, k)
                        continue
                    if after == "=" and g.type.base in ("map", "umap") and not g.type.dims:
                        dirty.setdefault(g.vid, k)
                        continue
                if nx == "." and m.text(k + 2) in ("push_back", "emplace_back", "insert", "push", "emplace", "push_front"):
                    dirty.setdefault(g.vid, k)
        for vid, k in dirty.items():
            if vid in reset:
                continue
            g = m.vars[vid]
            # jeśli zmienna jest czyszczona przed pętlą testów, i tak nie wystarczy — raportujemy
            what = "tablica" if g.type.dims else tname(g.type)
            ctx.report("multitest-no-clear", k,
                       f"Program ma wiele testów (pętla po `{name}` w linii {m.T[lp.kw].line}), a globalna {what} "
                       f"`{g.name}` jest w każdym teście uzupełniana (`{m.src(k, min(k + 6, m.stmt_end(k)))}`…), ale nigdzie "
                       f"w pętli nie jest czyszczona. Dane z poprzedniego testu przechodzą do następnego — pierwszy test "
                       f"zwykle działa, kolejne dają złe wyniki.",
                       (f"Na początku każdego testu: `{g.name}.clear();`" if not g.type.dims else
                        (f"Na początku każdego testu wyczyść listy: `for (int i = 0; i <= n; i++) {g.name}[i].clear();`"
                         if g.type.base in HEAVY else
                         f"Na początku każdego testu wyzeruj używaną część: `fill({g.name}, {g.name} + n + 1, 0);` "
                         f"(memset całej dużej tablicy przy wielu testach może być za wolny).")),
                       confidence=0.7, key=g.name)
        break


def _reads_size_in(m: Model, lp: Loop) -> bool:
    """Czy w ciele pętli testów wczytywany jest rozmiar danych (używany potem jako granica pętli)?"""
    s, e = lp.body
    read_vars = set()
    for k in range(s, e + 1):
        if m.T[k].kind == "id" and access_mode(m, k) in ("cin", "addr"):
            x = m.lookup(m.text(k), k)
            if x is not None and x.type.is_int():
                read_vars.add(x.vid)
    if not read_vars:
        return False
    for inner in m.loops:
        if inner is lp or not (s <= inner.kw <= e) or inner.cond is None:
            continue
        for k in range(*inner.cond):
            x = m.lookup(m.text(k), k) if m.T[k].kind == "id" else None
            if x is not None and x.vid in read_vars:
                return True
    return False


def _reachable_funcs(m: Model, rng) -> list[Func]:
    names = set()
    for k in range(rng[0], rng[1] + 1):
        if m.T[k].kind == "id" and m.text(k + 1) == "(":
            names.add(m.text(k))
    out, seen = [], set()
    stack = list(names)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for f in m.func_by_name.get(n, []):
            out.append(f)
            stack.extend(f.calls)
    return out


# =============================================================================================== reguły — styl/kompilacja/przenośność
meta("define-int-main", "#define int long long bez signed main", "error", "compile", ["oi-define-int", "num-int-types"],
     "Po #define int long long zapis `int main` staje się `long long main` — błąd kompilacji.", "define int long long")
meta("std-name-collision", "Nazwa koliduje z biblioteką standardową", "error", "compile",
     ["pit-std-names", "cpp-namespaces"], "Z `using namespace std` globalne `count`, `next`, `y1`, `rank`… są niejednoznaczne.",
     "reference is ambiguous using namespace std")
meta("shadow-loop-var", "Wewnętrzna pętla przesłania zmienną zewnętrznej pętli", "warning", "logic", ["cpp-scope"],
     "Druga zmienna o tej samej nazwie ukrywa pierwszą.", "przesłanianie zmiennych")
meta("shadow-local", "Zmienna przesłania inną o tej samej nazwie", "style", "style", ["cpp-scope"],
     "Wewnętrzna deklaracja ukrywa zewnętrzną.", "shadowing")
meta("shadow-global", "Lokalna zmienna przesłania globalną używaną w innej funkcji", "warning", "logic",
     ["cpp-scope", "pit-uninitialized"], "main wczytuje do lokalnego n, a inna funkcja używa globalnego n (= 0).",
     "zmienna globalna lokalna ta sama nazwa")
meta("unused-variable", "Nieużywana zmienna", "style", "style", ["cpp-variables"], "Zmienna jest zadeklarowana, ale nigdy nie użyta.",
     "nieużywana zmienna")
meta("main-no-return", "main bez return 0", "info", "style", ["cpp-functions"],
     "W C++ main bez return zwraca 0 — to poprawne, ale warto być konsekwentnym.", "return 0 main")
meta("long-32bit-windows", "`long` ma 32 bity na Windows", "info", "portability", ["num-int-types"],
     "Na Linuksie long = 64 bity, ale na Windows (MinGW/MSVC) tylko 32.", "long 32 bity windows")
meta("system-pause", "system(\"pause\") w rozwiązaniu", "warning", "portability", ["oi-submit"],
     "Na sprawdzarce nie ma polecenia pause — program może dostać RE/TLE.", "system pause")
meta("non-portable-header", "Nagłówek/funkcja tylko z Windows (conio.h, getch)", "error", "portability", ["oi-submit"],
     "conio.h, windows.h i getch() nie istnieją na Linuksowej sprawdzarce.", "conio.h")
meta("rand-usage", "rand() — mały zakres i słaba losowość", "info", "portability", ["tech-random"],
     "Na Windows RAND_MAX = 32767; rand() % n jest nierówny.", "mt19937 rand")
meta("sort-range-1indexed", "Sortujesz zakres od 0, a dane są od 1", "warning", "logic", ["stl-sort", "cpp-arrays"],
     "Dane wczytane do a[1..n], a sort(a, a + n) sortuje a[0..n-1].", "sort a+1 a+n+1")
meta("pow-int", "pow() na liczbach całkowitych", "warning", "logic", ["num-sqrt-pow"],
     "pow liczy na double — dla dużych wyników traci precyzję, a rzutowanie obcina (pow(10, 2) bywa 99).",
     "pow int precyzja")
meta("sqrt-precision", "sqrt na dużych liczbach całkowitych", "info", "logic", ["num-sqrt-pow"],
     "Dla x > 2⁵² (int)sqrt(x) bywa o 1 za duże/za małe.", "sqrt long long precyzja")
meta("abs-ll", "abs() bez std może obciąć long long/double do int", "warning", "overflow", ["num-int-types", "num-overflow"],
     "Bez `using namespace std` wywołanie ::abs(x) wybiera wersję dla int.", "abs long long llabs")

_STD_COLLIDE = {"count", "next", "prev", "distance", "rank", "size", "data", "begin", "end", "left", "right",
                "hash", "swap", "sort", "find", "fill", "copy", "move", "merge", "remove", "reverse", "unique",
                "lower_bound", "upper_bound", "less", "greater", "plus", "minus", "array", "list", "map", "set",
                "vector", "string", "queue", "stack", "pair", "tuple", "bitset", "function", "ref", "any", "optional",
                "exchange", "partition", "rotate", "search", "equal", "transform", "accumulate", "iota", "replace",
                "sample", "shuffle", "clamp", "advance", "empty", "max", "min", "gcd", "lcm", "polar", "norm", "arg",
                "real", "imag", "hex", "dec", "oct", "fixed", "ws", "endl", "flush", "boolalpha", "includes",
                "max_element", "min_element", "iter_swap", "for_each", "generate", "inner_product", "partial_sum",
                "adjacent_difference", "binary_search", "equal_range", "nth_element", "is_sorted", "deque", "valarray",
                "complex", "byte", "tie", "get", "minmax", "midpoint", "lerp", "union_find", "rel_ops", "ratio",
                "multiplies", "divides", "modulus", "negate", "identity", "invoke", "apply", "visit", "launder",
                "cbegin", "cend", "rbegin", "rend", "ssize", "abs", "div", "sqrt", "exp", "log", "pow"}
_CLIB_COLLIDE = {"y0", "y1", "yn", "j0", "j1", "jn", "index", "rindex", "time", "exp", "log", "log2", "log10", "pow",
                 "sqrt", "sin", "cos", "tan", "abs", "labs", "floor", "ceil", "round", "trunc", "fmin", "fmax",
                 "hypot", "rand", "srand", "exit", "free", "malloc", "signal", "remove", "rename", "div", "gamma",
                 "lgamma", "tgamma", "erf", "atoi", "strlen", "strcmp", "clock", "select", "link", "read", "write",
                 "random", "dup", "pipe", "sleep", "fork", "kill", "wait", "access", "stat", "time", "floorf", "nan",
                 "fabs", "atan", "asin", "acos", "exp2", "logb", "isnan", "isinf", "printf", "scanf", "puts",
                 "putchar", "getchar", "fopen", "fclose", "frexp", "ldexp", "modf", "fmod", "cbrt", "lround",
                 "drem", "significand", "scalb", "finite"}


@check
def _check_style(ctx: Ctx):
    m = ctx.m
    raw_code = m.code
    # #define int long long + int main
    if re.search(r"^\s*#\s*define\s+int\s+(long\s+long|int64_t|ll)\b", raw_code, re.M):
        raw = [t for t in m.raw if t.kind not in ("comment",)]
        for k, t in enumerate(raw):
            if t.text == "int" and k + 2 < len(raw) and raw[k + 1].text == "main" and raw[k + 2].text == "(":
                ctx.report("define-int-main", None,
                           "Masz `#define int long long`, więc `int main()` po preprocesorze to `long long main()`. "
                           "Standard wymaga, żeby main zwracał int — kompilacja kończy się błędem "
                           "„'::main' must return 'int'”.",
                           "Napisz `signed main()` (albo `int32_t main()`) — `signed` to to samo co int, a makro go nie rusza.",
                           line=t.line, col=t.col, confidence=1.0)
                break
    # kolizje nazw
    for v in m.vars:
        if v.kind != "global":
            continue
        nm = v.name
        if nm in _CLIB_COLLIDE:
            ctx.report("std-name-collision", v.idx,
                       f"Globalna zmienna `{nm}` ma taką samą nazwę jak funkcja z biblioteki C (math.h/stdlib.h/time.h, "
                       f"dołączanej przez bits/stdc++.h). GCC zgłosi „'{nm}' redeclared as different kind of entity” albo "
                       f"wywołania funkcji przestaną działać.",
                       f"Zmień nazwę, np. `{nm}_` albo `{nm}{'1' if nm[-1].isalpha() else 'a'}`/bardziej opisową.",
                       confidence=0.9)
        elif nm in _STD_COLLIDE and m.uses_ns_std and v.uses:
            u = v.uses[0]
            ctx.report("std-name-collision", u,
                       f"Globalna zmienna `{nm}` + `using namespace std;` = niejednoznaczność: istnieje też `std::{nm}`. "
                       f"Kompilator zgłosi „reference to '{nm}' is ambiguous” przy każdym użyciu.",
                       f"Zmień nazwę zmiennej (np. `cnt`, `nxt`, `rnk`, `sz`, `idx`) albo przenieś ją do funkcji.",
                       confidence=0.95)
    for v in m.vars:
        if v.kind not in ("local", "param") or v.name not in ("max", "min", "swap", "sort", "abs", "pow", "sqrt",
                                                              "count", "find", "gcd", "reverse", "fill"):
            continue
        for u in v.uses:
            if m.text(u + 1) == "(":
                ctx.report("std-name-collision", u,
                           f"Zmienna `{v.name}` (linia {v.line}) przesłania funkcję `{v.name}()` — tutaj próbujesz ją "
                           f"wywołać, ale kompilator widzi zmienną (błąd „'{v.name}' cannot be used as a function”).",
                           f"Zmień nazwę zmiennej (np. `mx`, `mn`, `cnt`) albo pisz `std::{v.name}(…)`.", confidence=0.95)
                break
    # shadowing
    for v in m.vars:
        if v.kind != "local" or v.structured:
            continue
        for o in m.by_name.get(v.name, ()):
            if o is v:
                continue
            if o.kind in ("local", "param") and o.func is v.func and o.scope_s < v.idx <= o.scope_e and o.idx < v.idx \
                    and o.scope_s < v.scope_s:
                if o.loop is not None and o.loop.body[0] <= v.idx <= o.loop.body[1] and v.loop is not None:
                    ctx.report("shadow-loop-var", v.idx,
                               f"Wewnętrzna pętla deklaruje nowe `{v.name}`, które przesłania `{v.name}` z zewnętrznej "
                               f"pętli (linia {o.line}). W środku nie masz już dostępu do zewnętrznego licznika — "
                               f"prawie zawsze to pomyłka (np. miało być `j`).",
                               f"Użyj innej nazwy dla wewnętrznego licznika (`j`, `k`).", confidence=0.7)
                elif v.loop is None or o.loop is None:
                    ctx.report("shadow-local", v.idx,
                               f"`{v.name}` przesłania wcześniejszą zmienną o tej samej nazwie (linia {o.line}). Zmiany "
                               f"wewnętrznej nie są widoczne na zewnątrz bloku.",
                               "Nadaj zmiennej inną nazwę.", confidence=0.4)
                break
            if o.kind == "global" and v.func is not None and not v.func.is_lambda:
                other_funcs = {m.func_at(u) for u in o.uses} - {None, v.func}
                writes_local = any(access_mode(m, u) in ("cin", "write", "addr", "refarg") for u in v.uses)
                o_written = any(access_mode(m, u) in ("cin", "write", "addr", "refarg", "rw") for u in o.uses)
                if other_funcs and writes_local and not o_written and not o.is_const:
                    fn = sorted(f.name for f in other_funcs)[0]
                    ctx.report("shadow-global", v.idx,
                               f"W `{v.func.name}` deklarujesz lokalne `{v.name}`, które przesłania globalne `{v.name}` "
                               f"(linia {o.line}). Wartość trafia do lokalnej zmiennej, a funkcja `{fn}` używa globalnej — "
                               f"tej, która nigdy nie dostała wartości (jest 0).",
                               f"Usuń lokalną deklarację (`{tname(v.type)} {v.name};` w linii {v.line}) i wczytuj "
                               f"bezpośrednio do globalnej.", confidence=0.85)
                break
    # nieużywane zmienne
    for v in m.vars:
        if v.kind != "local" or v.uses or v.structured or v.name.startswith("_") or v.name in ("dummy", "ignore", "unused"):
            continue
        if v.type.base in ("unknown", "struct", "auto", "stream", "rng", "smartptr", "functor") or v.type.ref:
            continue
        if v.loop is not None and v.loop.kind == "range":
            continue
        ctx.report("unused-variable", v.idx, f"Zmienna `{v.name}` jest zadeklarowana, ale nigdzie nieużywana.",
                   "Usuń ją (albo sprawdź, czy nie miałeś jej użyć zamiast innej zmiennej).", confidence=0.6, key=v.name)
    # main bez return
    for f in m.funcs:
        if f.name == "main" and not f.owner:
            if not any(m.text(q) == "return" for q in range(f.body_s, f.body_e)):
                ctx.report("main-no-return", f.name_idx,
                           "main nie ma `return 0;`. W C++ to dozwolone (main zwraca wtedy 0), więc to tylko uwaga.",
                           "Możesz dopisać `return 0;` na końcu main.", confidence=0.3)
    # long 32 bity
    for k, t in enumerate(m.raw):
        if t.kind == "kw" and t.text == "long":
            prv = m.raw[k - 1].text if k else ""
            nxt = m.raw[k + 1].text if k + 1 < len(m.raw) else ""
            if prv != "long" and nxt not in ("long", "double"):
                ctx.report("long-32bit-windows", None,
                           f"`long` ma 64 bity na Linuksie (jak na OI), ale tylko 32 bity na Windows — ten sam program "
                           f"może się przepełniać u Ciebie, a działać na sprawdzarce (albo odwrotnie).",
                           "Pisz `long long` (zawsze 64 bity) albo `int64_t`.", line=t.line, col=t.col, confidence=0.4)
                break
    # system("pause"), conio
    for k, t in enumerate(m.T):
        if t.text == "system" and m.text(k + 1) == "(":
            ctx.report("system-pause", k,
                       f"`{m.src(k, m.pairs.get(k + 1, k + 1) + 1)}` uruchamia polecenie systemowe. Na sprawdzarce "
                       f"(Linux) nie ma `pause`/`cls`, a i tak nikt nie naciśnie klawisza — może to dać RE albo TLE.",
                       "Usuń to wywołanie przed wysłaniem.", confidence=0.8)
            break
    for inc in m.includes:
        if inc.lower() in ("conio.h", "windows.h", "dos.h", "graphics.h"):
            ln = next((i + 1 for i, l in enumerate(m.lines) if inc in l and "#" in l), 1)
            ctx.report("non-portable-header", None,
                       f"`#include <{inc}>` istnieje tylko na Windows — na sprawdzarce (Linux) kompilacja się nie powiedzie.",
                       "Usuń ten nagłówek i funkcje z niego (getch, Sleep, system(\"cls\")).", line=ln, col=1,
                       confidence=0.95)
    for k, t in enumerate(m.T):
        if t.text in ("getch", "_getch", "getche") and m.text(k + 1) == "(":
            ctx.report("non-portable-header", k, f"`{t.text}()` pochodzi z conio.h (Windows) — na Linuksie nie istnieje.",
                       "Usuń to wywołanie (czekanie na klawisz nie jest potrzebne).", confidence=0.9)
            break
    for k, t in enumerate(m.T):
        if t.text == "rand" and m.text(k + 1) == "(" and m.text(k + 2) == ")" and m.text(k - 1) not in (".", "->"):
            ctx.report("rand-usage", k,
                       "rand() na Windows zwraca liczby tylko do 32767, a `rand() % n` daje nierówny rozkład. "
                       "Do generatorów testów i algorytmów losowych lepszy jest mt19937.",
                       "`mt19937 rng(chrono::steady_clock::now().time_since_epoch().count());` i "
                       "`uniform_int_distribution<int>(a, b)(rng)`.", confidence=0.4)
            break


@check
def _check_math_funcs(ctx: Ctx):
    m = ctx.m
    for st, nd in m.all_nodes():
        if nd.kind != "call":
            continue
        name = _call_name(nd)
        args = nd.kids[1:]
        if name == "pow" and len(args) == 2:
            a, b = _strip(args[0]), _strip(args[1])
            if a is None or b is None or not a.type.is_int() or not b.type.is_int():
                continue
            par = nd.parent
            while par is not None and par.kind == "paren":
                par = par.parent
            int_ctx = False
            if par is not None and par.kind == "cast" and par.type.is_int():
                int_ctx = True
            elif par is None and st.kind in ("init", "return") and st.dest is not None and st.dest.is_int():
                int_ctx = True
            elif par is not None and par.kind == "assign" and par.kids[0].type.is_int():
                int_ctx = True
            elif par is not None and par.kind == "binary" and par.op in ("%", "==", "!=") :
                int_ctx = True
            elif par is not None and par.kind == "index":
                int_ctx = True
            elif par is not None and par.kind == "binary" and par.op in ("+", "-", "*") and \
                    any(x.type.is_int() for x in par.kids if x is not nd):
                pp = par.parent
                if pp is None and st.dest is not None and st.dest.is_int():
                    int_ctx = True
            if not int_ctx:
                continue
            if a.value == 2:
                ctx.report("pow-int", nd.s,
                           f"`{_src(m, nd)}` liczy potęgę dwójki na liczbach zmiennoprzecinkowych — dla 2ᵏ wynik jest "
                           f"dokładny, ale to wolne i nieczytelne, a dla k ≥ 63 i tak się nie zmieści.",
                           f"`1LL << {_src(m, b)}` (k ≤ 62).", severity="info", confidence=0.5)
            else:
                ctx.report("pow-int", nd.s,
                           f"`{_src(m, nd)}` zwraca double. Dla wyników > 2⁵³ (~9·10¹⁵) traci precyzję, a rzutowanie na "
                           f"int obcina część ułamkową — np. pow(10, 2) potrafi dać 99.9999999 → 99 (znane na "
                           f"MinGW). Do potęg całkowitych pow się nie nadaje.",
                           f"Mnóż w pętli albo użyj szybkiego potęgowania na long long; dla kwadratu: "
                           f"`{_src(m, a)} * {_src(m, a)}` (pamiętaj o 1LL).", confidence=0.6)
        if name in ("sqrt", "sqrtl", "cbrt") and args:
            a = _strip(args[0])
            if a is None or not a.type.is_wide():
                continue
            par = nd.parent
            while par is not None and par.kind == "paren":
                par = par.parent
            int_ctx = (par is not None and par.kind == "cast" and par.type.is_int()) or \
                (par is None and st.kind == "init" and st.dest is not None and st.dest.is_int())
            target = st.var.name if st.kind == "init" and st.var is not None else None
            if int_ctx and target:
                corrected = any(kwn == "while" and f"{target} * {target}" in " ".join(m.text(q) for q in range(s0, e0))
                                for kw, s0, e0, kwn in m.conds if kw > nd.s)
                if corrected:
                    continue
            if int_ctx:
                ctx.report("sqrt-precision", nd.s,
                           f"`{_src(m, nd)}` dla {tname(a.type)} rzędu 10¹⁸ liczy pierwiastek na liczbach "
                           f"zmiennoprzecinkowych ({'long double: 64 bity mantysy na x86, ale na ARM/Windows tylko 53' if name == 'sqrtl' else 'double: 53 bity mantysy'}) "
                           f"— wynik po obcięciu bywa o 1 za duży albo za mały.",
                           "Popraw wynik pętlami: `long long r = sqrtl(x); while (r * r > x) r--; while ((r + 1) * (r + 1) <= x) r++;`",
                           confidence=0.5)
        if name == "abs" and args and not m.uses_ns_std:
            a = _strip(args[0])
            fn = nd.kids[0]
            if a is not None and (a.type.is_wide() or a.type.is_float()) and "std" not in fn.qual:
                ctx.report("abs-ll", nd.s,
                           f"Bez `using namespace std;` wywołanie `abs({_src(m, a)})` może trafić w C-owe `int abs(int)` — "
                           f"wartość {tname(a.type)} zostanie obcięta do int.",
                           f"Pisz `std::abs(…)`" + (" albo `llabs(…)`." if a.type.is_wide() else " albo `fabs(…)`."),
                           confidence=0.6)


@check
def _check_sort_range(ctx: Ctx):
    m = ctx.m
    filled = {}
    for lp in m.loops:
        if lp.kind != "for" or not lp.var or lp.start is None or lp.cmp != "<=" or lp.step != 1:
            continue
        s = _strip(lp.start)
        if s is None or s.value != 1:
            continue
        bl = _linear(m, lp.bound)
        if bl is None or bl[0] is None or bl[1] != 0:
            continue
        for k in range(lp.body[0], lp.body[1] + 1):
            if m.text(k + 1) == "[" and m.text(k + 2) == lp.var and m.text(k + 3) == "]" and \
                    (m.text(k - 1) == ">>" or (m.text(k - 1) == "&" and m.text(k - 2) in (",", "("))):
                v = m.lookup(m.text(k), k)
                if v is not None and v.type.dims:
                    filled[v.vid] = bl[0]
    if not filled:
        return
    for st, nd in m.all_nodes():
        if nd.kind != "call" or _call_name(nd) not in ("sort", "stable_sort", "reverse", "accumulate") or len(nd.kids) < 3:
            continue
        a, b = _strip(nd.kids[1]), _strip(nd.kids[2])
        if a is None or b is None:
            continue
        if a.kind == "id" and a.var is not None and a.var.vid in filled:
            n = filled[a.var.vid]
            bl = _linear(m, b)
            if b.kind == "binary" and b.op == "+" and _strip(b.kids[0]).kind == "id" and _strip(b.kids[0]).name == a.name:
                bl = _linear(m, b.kids[1])
            if bl is not None and bl[0] == n and bl[1] == 0:
                ctx.report("sort-range-1indexed", nd.s,
                           f"Dane wczytujesz do `{a.name}[1]` … `{a.name}[{n}]`, a `{_src(m, nd)}` działa na zakresie "
                           f"`{a.name}[0]` … `{a.name}[{n} - 1]` — bierze nieużywane `{a.name}[0]` i pomija `{a.name}[{n}]`.",
                           f"`{_call_name(nd)}({a.name} + 1, {a.name} + {n} + 1)`.", confidence=0.85)


# najlepsze artykuły bazy wiedzy (data/kb) dla reguł — wybierany jest pierwszy istniejący
_KB_BEST = {
    "overflow-int-mul": ["pit-int-overflow", "num-overflow"], "overflow-int-sum": ["pit-int-overflow", "num-overflow"],
    "overflow-cast-late": ["pit-int-overflow", "num-overflow"], "int-max-plus": ["num-inf-constants", "pit-int-overflow"],
    "const-int-overflow": ["pit-int-overflow", "num-overflow"], "overflow-mod-mul": ["num-mod-arithmetic", "pit-int-overflow"],
    "shift-overflow": ["pit-bit-ops", "tech-bit-tricks"], "shift-int-wide": ["pit-bit-ops", "tech-bit-tricks"],
    "bitwise-precedence": ["pit-bit-ops"], "accumulate-int-init": ["stl-accumulate", "pit-int-overflow"],
    "inf-overflow": ["num-inf-constants"], "inf-0x3f-ll": ["num-inf-constants"], "inf-memset-mismatch": ["num-inf-constants", "pit-memset"],
    "int-literal-overflow": ["num-int-types", "num-inf-constants"], "narrowing-ll-to-int": ["num-int-types"],
    "long-32bit-windows": ["num-int-types"], "size-minus-one": ["pit-unsigned-size"], "signed-unsigned-loop": ["pit-unsigned-size"],
    "unsigned-reverse-loop": ["pit-unsigned-size"], "assign-in-condition": ["pit-assign-vs-compare"],
    "empty-body": ["pit-assign-vs-compare"], "chained-comparison": ["pit-assign-vs-compare"],
    "logical-const": ["pit-assign-vs-compare"], "uninit-local": ["pit-uninitialized", "cpp-global-vs-local"],
    "uninit-local-array": ["pit-uninitialized", "cpp-global-vs-local"], "missing-return": ["oi-compile-flags", "oi-debugging"],
    "missing-return-path": ["oi-compile-flags", "oi-debugging"], "comparator-non-strict": ["pit-comparator", "stl-sort"],
    "xor-as-power": ["pit-bit-ops", "num-sqrt-pow"], "comma-in-index": ["stl-vector-2d"], "switch-fallthrough": ["oi-debugging"],
    "recursion-no-base": ["pit-stack-overflow", "cpp-stack-overflow"], "float-equality": ["pit-double-equal", "num-floating"],
    "int-division-double": ["pit-integer-division"], "int-div-literal": ["pit-integer-division"],
    "negative-modulo": ["num-negative-mod", "num-mod-arithmetic"], "index-out-of-range": ["pit-out-of-bounds", "pit-off-by-one"],
    "index-negative": ["pit-out-of-bounds", "pit-off-by-one"], "vector-no-size-index": ["stl-vector", "pit-out-of-bounds"],
    "size-before-read": ["stl-vector", "pit-input-format"], "large-local-array": ["pit-stack-overflow", "cpp-global-vs-local"],
    "vla": ["cpp-global-vs-local", "stl-vector"], "sizeof-array-param": ["pit-memset", "stl-memset"],
    "memset-value": ["pit-memset", "stl-memset"], "memset-size": ["pit-memset", "stl-memset"],
    "memset-vector": ["pit-memset", "stl-memset"], "endl-in-loop": ["pit-endl-tle", "io-fast-io"], "no-fast-io": ["io-fast-io"],
    "mixed-io-after-sync": ["io-fast-io"], "scanf-format": ["io-printf-formats"], "printf-format": ["io-printf-formats"],
    "cin-eof-loop": ["io-read-until-eof"], "getline-after-cin": ["io-getline"], "freopen-left": ["io-freopen"],
    "double-print-precision": ["io-precision"], "erase-in-loop": ["pit-iterator-erase", "stl-iterators"],
    "modify-in-range-for": ["pit-iterator-erase", "stl-iterators"], "erase-index-skip": ["pit-iterator-erase", "stl-remove-erase"],
    "map-index-check": ["pit-map-bracket", "stl-map"], "strlen-in-loop": ["stl-string"], "pass-by-value": ["cpp-references"],
    "string-concat-loop": ["stl-string"], "multitest-no-clear": ["pit-multitest-reset"], "define-int-main": ["cpp-macros-typedef"],
    "std-name-collision": ["pit-name-collisions", "cpp-bits-stdcpp"], "shadow-loop-var": ["cpp-global-vs-local"],
    "shadow-local": ["cpp-global-vs-local"], "shadow-global": ["cpp-global-vs-local"], "unused-variable": ["oi-compile-flags"],
    "main-no-return": ["oi-template"], "system-pause": ["oi-sio2"], "non-portable-header": ["oi-sio2", "cpp-bits-stdcpp"],
    "rand-usage": ["num-random", "stl-shuffle-random"], "sort-range-1indexed": ["stl-sort", "pit-off-by-one"],
    "pow-int": ["num-sqrt-pow"], "sqrt-precision": ["num-sqrt-pow"], "abs-ll": ["pit-abs-long-long"],
}
for _rid, _ids in _KB_BEST.items():
    if _rid in RULES:
        RULES[_rid].kb = list(_ids) + [k for k in RULES[_rid].kb if k not in _ids]


# =============================================================================================== analiza
_SEV_WEIGHT = {"error": 24, "warning": 8, "perf": 3, "style": 1, "info": 0}


def health(issues: list[dict]) -> dict:
    """Werdykt zdrowia kodu na podstawie listy zgłoszeń (0–100)."""
    pen = sum(_SEV_WEIGHT.get(i["severity"], 0) * (0.5 + 0.5 * float(i.get("confidence", 0.7))) for i in issues)
    score = max(0, min(100, round(100 - pen)))
    errs = sum(1 for i in issues if i["severity"] == "error")
    warns = sum(1 for i in issues if i["severity"] == "warning")
    if errs:
        label, tone = ("Poważne błędy" if score < 50 else "Prawdopodobny błąd"), "danger"
    elif warns:
        label, tone = ("Podejrzany" if score < 75 else "Drobne ryzyka"), "warn"
    elif any(i["severity"] == "perf" for i in issues):
        label, tone = "Poprawny, do przyspieszenia", "info"
    else:
        label, tone = "Wygląda zdrowo", "success"
    return {"score": score, "label": label, "tone": tone}


def analyze(code: str, kb_ids: set | None = None, max_issues: int = 400) -> dict:
    """Analiza statyczna: {"issues": [...], "stats": {...}}. Nigdy nie rzuca wyjątku dla dziwnego kodu."""
    t0 = time.perf_counter()
    code = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    internal = []
    try:
        m = Model(code)
    except Exception as e:  # noqa: BLE001
        return {"issues": [], "stats": {"lines": code.count("\n") + 1, "error": f"Nie udało się przeanalizować kodu: {e}",
                                        "time_ms": round(1000 * (time.perf_counter() - t0), 1)},
                "internal_errors": [repr(e)]}
    ctx = Ctx(m, kb_ids)
    for fn in _CHECKS:
        try:
            fn(ctx)
        except RecursionError:
            internal.append(f"{fn.__name__}: zbyt głęboko zagnieżdżone wyrażenie")
        except Exception as e:  # noqa: BLE001
            internal.append(f"{fn.__name__}: {e.__class__.__name__}: {e}")
    issues = ctx.issues
    # gdy na tej samej linii jest błąd i ostrzeżenie z tej samej kategorii — zostaw mocniejsze
    best: dict = {}
    for it in issues:
        k = (it["line"], it["category"])
        if k not in best or SEVERITY_ORDER[it["severity"]] < SEVERITY_ORDER[best[k]["severity"]]:
            best[k] = it
    issues = [it for it in issues if best[(it["line"], it["category"])] is it or it["severity"] in ("error",)
              or it["category"] not in ("overflow",)]
    issues.sort(key=lambda d: (SEVERITY_ORDER.get(d["severity"], 9), -d.get("confidence", 0), d["line"], d["col"]))
    issues = issues[:max_issues]
    for n, it in enumerate(issues):
        it["id"] = f"s{n}"
        it["source"] = "static"
    by_sev = {s: 0 for s in SEVERITY_ORDER}
    for it in issues:
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
    stats = {
        "lines": len(m.lines), "tokens": m.n, "functions": len(m.funcs), "lambdas": len(m.lambdas),
        "loops": len(m.loops), "variables": len(m.vars), "macros": len(m.macros), "rules": len(RULES),
        "by_severity": by_sev, "total": len(issues), "time_ms": round(1000 * (time.perf_counter() - t0), 1),
        "health": health(issues), "uses_namespace_std": m.uses_ns_std,
        "max_loop_depth": _max_loop_depth(m),
    }
    return {"issues": issues, "stats": stats, "internal_errors": internal}


def _max_loop_depth(m: Model) -> int:
    best = 0
    for lp in m.loops:
        d = sum(1 for o in m.loops if o.body[0] <= lp.kw <= o.body[1])
        best = max(best, d + 1)
    return best


def rules_catalog() -> list[dict]:
    return [{"id": r.id, "title": r.title, "severity": r.severity, "category": r.category,
             "category_name": CATEGORIES.get(r.category, r.category), "description": r.desc, "kb": r.kb}
            for r in sorted(RULES.values(), key=lambda r: (r.category, SEVERITY_ORDER[r.severity], r.id))]

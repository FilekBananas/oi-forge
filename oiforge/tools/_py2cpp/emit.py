"""Emiter C++: nazwy, typy, funkcje, struktury, zmienne globalne i składanie wyniku z mapą linii."""
from __future__ import annotations

import ast
import contextlib

from . import runtime
from .analysis import Analysis, Symbol
from .emit_expr import ExprMixin
from .emit_stmt import StmtMixin
from .infer import FuncInfo, Infer, qual_of_call, short
from .plan import UnitPlan
from .signs import Signs, const_int
from .types import BOT, INT, NONE, Ty, contains_top, hashable_simple, is_scalar, join, tag

CPP_KEYWORDS = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor", "bool", "break", "case", "catch", "char",
    "char8_t", "char16_t", "char32_t", "class", "compl", "concept", "const", "consteval", "constexpr", "constinit",
    "const_cast", "continue", "co_await", "co_return", "co_yield", "decltype", "default", "delete", "do", "double",
    "dynamic_cast", "else", "enum", "explicit", "export", "extern", "false", "float", "for", "friend", "goto", "if",
    "inline", "int", "long", "mutable", "namespace", "new", "noexcept", "not", "not_eq", "nullptr", "operator", "or",
    "or_eq", "private", "protected", "public", "register", "reinterpret_cast", "requires", "return", "short", "signed",
    "sizeof", "static", "static_assert", "static_cast", "struct", "switch", "template", "this", "thread_local", "throw",
    "true", "try", "typedef", "typeid", "typename", "union", "unsigned", "using", "virtual", "void", "volatile",
    "wchar_t", "while", "xor", "xor_eq", "main", "NULL", "errno", "assert", "INFINITY", "NAN", "LLONG_MAX",
    "LLONG_MIN", "INT_MAX", "INT_MIN", "EOF", "stdin", "stdout", "stderr",
}
STD_LOCAL = {
    "min", "max", "swap", "sort", "stable_sort", "reverse", "count", "find", "accumulate", "abs", "gcd", "lcm", "pow",
    "sqrt", "floor", "ceil", "log", "log2", "log10", "exp", "fabs", "fmod", "hypot", "atan2", "to_string", "stoll",
    "stod", "stoi", "getline", "lower_bound", "upper_bound", "iota", "fill", "tie", "make_pair", "make_tuple", "get",
    "move", "greater", "less", "any_of", "all_of", "none_of", "count_if", "max_element", "min_element", "push_heap",
    "pop_heap", "make_heap", "isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower", "toupper", "tolower",
    "nearbyint", "round", "trunc", "clamp", "unique", "distance", "prev", "next", "binary_search", "setprecision",
    "fixed", "setw", "setfill", "numeric_limits", "views", "string", "vector", "map", "set", "pair", "tuple", "deque",
    "priority_queue", "optional", "function", "size", "begin", "end", "rbegin", "rend", "cin", "cout", "cerr", "exit",
    "unordered_map", "unordered_set", "queue", "stack", "array", "list", "bitset", "nullopt", "numbers", "type_identity_t",
    "runtime_error", "exception", "apply", "is_integral_v", "istreambuf_iterator", "streamsize", "signbit", "isnan",
    "isinf", "to_chars", "chars_format", "strtoll", "strtod", "snprintf", "string_view", "erase", "insert", "ws",
}
STD_GLOBAL = STD_LOCAL | {
    "left", "right", "data", "hash", "rank", "index", "time", "y0", "y1", "j0", "j1", "jn", "yn", "div", "ldiv",
    "link", "read", "write", "select", "signal", "remove", "rename", "clock", "free", "ref", "merge", "partition",
    "search", "transform", "replace", "copy", "equal", "rotate", "shuffle", "sample", "exchange", "forward",
    "invoke", "bind", "ignore", "plus", "minus", "multiplies", "divides", "modulus", "negate", "endl", "flush", "ends",
    "hex", "dec", "oct", "boolalpha", "byte", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
    "exp2", "cbrt", "erf", "erfc", "tgamma", "lgamma", "beta", "midpoint", "lerp", "gamma", "nan", "random", "rand",
    "srand", "labs", "llabs", "fmax", "fmin", "remainder", "nextafter", "modf", "frexp", "ldexp", "lround", "llround",
    "rint", "polar", "arg", "norm", "conj", "proj", "real", "imag", "reduce", "partial_sum", "adjacent_difference",
    "inner_product", "includes", "monostate", "any", "variant", "visit", "advance", "inserter", "back_inserter",
    "mismatch", "generate", "for_each", "minmax", "nth_element", "partial_sort", "is_sorted", "next_permutation",
    "prev_permutation", "set_union", "set_intersection", "set_difference", "equal_range", "launder", "byteswap",
    "popcount", "countl_zero", "countr_zero", "bit_width", "bit_ceil", "bit_floor", "has_single_bit", "abort", "puts",
    "printf", "scanf", "strlen", "memset", "memcpy", "system", "getchar", "putchar", "atoi", "atol", "qsort", "bsearch",
    "isprint", "ispunct", "tolower", "ssize", "format", "span", "ranges", "regex", "thread", "mutex", "locale", "ios",
    "istream", "ostream", "stringstream", "istringstream", "ostringstream", "chrono", "filesystem", "ratio", "complex",
    "valarray", "slice", "gslice", "mask_array", "first", "second", "front", "back", "empty", "top", "pop", "push",
    "clear", "assign", "resize", "reserve", "emplace", "at",
}
RESERVED_ALL = CPP_KEYWORDS | runtime.HELPER_NAMES | STD_LOCAL


class Line:
    __slots__ = ("ind", "text", "py")

    def __init__(self, ind, text, py):
        self.ind = ind
        self.text = text
        self.py = py


class Emitter(ExprMixin, StmtMixin):
    def __init__(self, source: str, tree: ast.Module, an: Analysis, inf: Infer, signs: Signs, opts: dict):
        self.source = source
        self.src_lines = source.splitlines()
        self.tree = tree
        self.an = an
        self.inf = inf
        self.signs = signs
        self.opts = opts
        self.int_t = "int" if str(opts.get("int_type", "long long")).strip() == "int" else "long long"
        self.ll_suffix = "LL" if self.int_t == "long long" else ""
        self.use_unordered = bool(opts.get("use_unordered", False))
        self.fast_io = opts.get("fast_io", True) is not False
        self.warnings: list[dict] = []
        self._wkeys: set = set()
        self.helpers: set[str] = set()
        self.buf: list[Line] = []
        self.ind = 0
        self.py = 0
        self.taken: set[str] = set(an.all_names) | RESERVED_ALL
        self.cname: dict[Symbol, str] = {}
        self.fi: FuncInfo | None = None           # bieżąca funkcja (None = main)
        self.cls_ctx = None                       # bieżąca klasa (ClassInfo) przy emisji metod
        self.plan: UnitPlan | None = None
        self.plans: list[UnitPlan] = []
        self.loops: list[dict] = []
        self.hoist_ok = True
        self.rename: dict[Symbol, str] = {}
        self.const_params: set[Symbol] = set()
        self.readline_alias: set[Symbol] = set()
        self.thread_targets: dict[Symbol, str] = {}
        self.skip_stmts: set[int] = set()
        self.global_inits: dict[Symbol, str] = {}
        self.consts: dict[Symbol, str] = {}       # symbol -> wyrażenie stałej globalnej
        self.const_notes: dict[Symbol, str] = {}
        self.notes: list[str] = []
        self.in_try = 0
        self.memo_name: str | None = None
        self.memo_key: str | None = None
        self.ret_type: Ty | None = None
        self.inf_const_used = False
        self.inf_as_int = False
        self.need_inf_const = False
        self.prototypes_needed = False
        self.uses_numbers = False
        self.io_token_needs_skip: set[int] = set()
        self.has_line_reads = False
        self.read_sites: list = []
        self.lambda_depth = 0
        self.func_cname: dict[str, str] = {}
        self.recursion = False
        self.all_alias: dict = {}
        self._once: dict = {}
        self.fresh_log: list[str] = []
        self.inf_name = "INF" if "INF" not in an.all_names else "INF_LL"

    # ------------------------------------------------------------------ ostrzeżenia
    ONCE = {"pymod", "floordiv", "stable", "heap", "list-in", "inf-int", "memo", "alias", "pow2", "round", "pop0",
            "chain-cmp", "copy", "type-unknown", "dict-order", "set-order"}

    def warn(self, code: str, msg: str, line: int | None = None, severity: str = "warn") -> None:
        line = self.py if line is None else line
        if code in self.ONCE:
            w = self._once.get(code)
            if w is not None:
                if line and line != w["line"] and line not in w["lines"]:
                    w["lines"].append(line)
                return
        key = (code, line, msg)
        if key in self._wkeys:
            return
        self._wkeys.add(key)
        w = {"line": line, "severity": severity, "code": code, "message": msg, "lines": []}
        if code in self.ONCE:
            self._once[code] = w
        self.warnings.append(w)

    def info(self, code: str, msg: str, line: int | None = None) -> None:
        self.warn(code, msg, line, "info")

    def use(self, *names: str) -> None:
        self.helpers.update(names)

    # ------------------------------------------------------------------ bufor wyjścia
    def out(self, text: str, py: int | None = None) -> None:
        if self.notes and text.rstrip().endswith((";", "{")):
            text = text + "  // " + "; ".join(self.notes)
            self.notes = []
        self.buf.append(Line(self.ind, text, self.py if py is None else py))

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    @contextlib.contextmanager
    def indent(self, n: int = 1):
        self.ind += n
        try:
            yield
        finally:
            self.ind -= n

    @contextlib.contextmanager
    def capture(self):
        """Przechwytuje linie emitowane w środku (np. do sprawdzenia, czy wyrażenie wymaga instrukcji pomocniczych)."""
        saved, saved_ind = self.buf, self.ind
        self.buf = []
        box: list = []
        try:
            yield box
        finally:
            box.extend(self.buf)
            self.buf, self.ind = saved, saved_ind

    def replay(self, lines: list) -> None:
        for ln in lines:
            self.buf.append(Line(ln.ind, ln.text, ln.py))

    def fresh(self, base: str) -> str:
        name = base
        k = 2
        while name in self.taken:
            name = f"{base}{k}"
            k += 1
        self.taken.add(name)
        self.fresh_log.append(name)
        return name

    # ------------------------------------------------------------------ symbole i nazwy
    def S(self, node) -> Symbol | None:
        sym = self.an.sym_of.get(id(node))
        return self.resolve_alias(sym)

    def resolve_alias(self, sym):
        guard = 0
        alias = self.all_alias
        while sym is not None and sym in alias and guard < 50:
            sym = alias[sym]
            guard += 1
        return sym

    def _index_names(self) -> None:
        """Jednorazowy indeks: zapisy i odczyty nazw według surowego symbolu (zamiast przechodzenia drzewa)."""
        self.raw_stores: dict = {}
        self.raw_loads: dict = {}
        self.mod_syms: set = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                s = self.an.sym_of.get(id(node))
                if s is None:
                    continue
                d = self.raw_stores if isinstance(node.ctx, ast.Store) else self.raw_loads
                d.setdefault(s, []).append(node)
            elif isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(node.op, ast.Mod):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name):
                        s = self.an.sym_of.get(id(sub))
                        if s is not None:
                            self.mod_syms.add(s)

    def members(self, sym) -> list:
        """Surowe symbole, które po scaleniu sieci oznaczają `sym`."""
        out = [sym]
        for raw, tgt in self.all_alias.items():
            if self.resolve_alias(tgt) is sym:
                out.append(raw)
        return out

    def store_count(self, sym) -> int:
        return sum(len(self.raw_stores.get(r, ())) for r in self.members(sym))

    def cn(self, sym: Symbol) -> str:
        sym = self.resolve_alias(sym)
        if sym in self.rename:
            return self.rename[sym]
        got = self.cname.get(sym)
        if got:
            return got
        root = sym.root()
        name = root.name
        if root in self.cname and sym is not root and sym.base is not None:
            name = self.cname[root]
        glob = root.scope.kind == "module" or root.kind in ("func", "class") or root.is_global
        bad = RESERVED_ALL | (STD_GLOBAL if glob else set())
        if root.kind == "func" and name == "main" and root.scope.kind == "module":
            name = "solve" if "solve" not in self.an.all_names else "main_"
        elif name in bad or name.startswith("__") or "__" in name:
            name = name.replace("__", "_d_") if "__" in name else name
            cand = name + "_"
            while cand in self.an.all_names:
                cand += "_"
            name = cand
        self.cname[sym] = name
        return name

    def T(self, node) -> Ty:
        return self.inf.node_ty.get(id(node), BOT)

    def sym_type(self, sym: Symbol) -> Ty:
        return self.inf.ty.get(sym, BOT)

    def is_pure_heap(self, sym) -> bool:
        return sym is not None and sym in self.pure_heaps

    # ------------------------------------------------------------------ typy C++
    def ct(self, t: Ty, top_ok: bool = True) -> str:
        k = t.k
        if k == "int":
            return self.int_t
        if k == "inf":
            return self.int_t if self.inf_as_int else "double"
        if k == "float":
            return "double"
        if k == "bool":
            return "bool"
        if k == "char":
            return "char"
        if k in ("str",):
            return "string"
        if k == "none":
            return "void"
        if k in ("list", "lazy"):
            return f"vector<{self.ct_elem(t.args[0])}>"
        if k == "set":
            e = t.args[0]
            if self.use_unordered and hashable_simple(e):
                return f"unordered_set<{self.ct_elem(e)}>"
            return f"set<{self.ct_elem(e)}>"
        if k == "dict":
            kt, vt = t.args
            if self.use_unordered and hashable_simple(kt) and kt.k != "bot":
                return f"unordered_map<{self.ct_elem(kt)}, {self.ct_elem(vt)}>"
            return f"map<{self.ct_elem(kt)}, {self.ct_elem(vt)}>"
        if k == "deque":
            return f"deque<{self.ct_elem(t.args[0])}>"
        if k == "tuple":
            if len(t.args) == 2:
                return f"pair<{self.ct_elem(t.args[0])}, {self.ct_elem(t.args[1])}>"
            return f"tuple<{', '.join(self.ct_elem(a) for a in t.args)}>"
        if k == "opt":
            inner = t.args[0]
            if inner.k in ("none", "bot"):
                return f"optional<{self.int_t}>"
            return f"optional<{self.ct_elem(inner)}>"
        if k == "class":
            return self.class_cname(t.extra)
        if k == "tokens":
            return "vector<string>"
        if k == "file":
            return "vector<string>"
        if k == "bot":
            return self.int_t
        return "auto" if top_ok else self.int_t

    def ct_elem(self, t: Ty) -> str:
        if t.k == "bot":
            return self.int_t
        if t.k in ("top", "func"):
            self.warn("type-unknown", "Nie udało się ustalić typu elementów kontenera — przyjęto liczbę całkowitą. "
                      "Sprawdź typy w tłumaczeniu.")
            return self.int_t
        if t.k == "none":
            return self.int_t
        return self.ct(t)

    def class_cname(self, name: str) -> str:
        ci = self.inf.classes.get(name)
        if ci is None:
            return name
        sym = self.an.sym_of.get(id(ci.node))
        return self.cn(sym) if sym else name

    def heap_type(self, t: Ty) -> str:
        e = self.ct_elem(t.args[0] if t.args else BOT)
        return f"priority_queue<{e}, vector<{e}>, greater<{e}>>"

    def sym_ct(self, sym: Symbol) -> str:
        t = self.sym_type(sym)
        if self.is_pure_heap(sym):
            return self.heap_type(t)
        if t.k == "func":
            return "auto"
        if sym in getattr(self, "iter_src", {}):
            return "size_t"
        if t.k == "builtin" and sym in self.readline_alias:
            return "auto"
        if contains_top(t):
            self.warn("type-conflict", f"Zmienna `{sym.name}` ma niejednoznaczny typ (różne typy wartości) — "
                      "użyto `auto`; sprawdź tłumaczenie.")
        return self.ct(t)

    def default_init(self, sym: Symbol) -> str:
        """Inicjalizacja przy deklaracji bez wartości (`T x;`)."""
        t = self.sym_type(sym)
        if t.k in ("int", "inf", "char"):
            return " = 0"
        if t.k == "float":
            return " = 0"
        if t.k == "bool":
            return " = false"
        return ""

    # ------------------------------------------------------------------ analiza wstępna
    def prepare(self) -> None:
        self._index_names()
        self._find_pure_heaps()
        self._find_inf_as_int()
        self._find_aliases()
        self._find_iters()
        self._find_io()
        self._find_consts()

    def _parent(self, node):
        return self.an.parent.get(id(node))

    def _find_pure_heaps(self) -> None:
        """Lista używana tylko jak kopiec (heappush/heappop/h[0]/len/while h) → priority_queue."""
        self.pure_heaps: set[Symbol] = set()
        an = self.an
        for sym in self.inf.heap_syms:
            root = sym
            ok = True
            nodes = [n for n in ast.walk(self.tree) if isinstance(n, ast.Name) and an.sym_of.get(id(n)) is root]
            for n in nodes:
                p = self._parent(n)
                if isinstance(n.ctx, ast.Store):
                    # przypisanie h = [] / h = [...] / pętla — dozwolone tylko h = [] lub lista
                    if isinstance(p, ast.Assign) and len(p.targets) == 1 and p.targets[0] is n:
                        if isinstance(p.value, ast.List) and not p.value.elts:
                            continue
                        if isinstance(p.value, (ast.List, ast.ListComp)) or (
                                isinstance(p.value, ast.Call) and qual_of_call(an, p.value.func) == "list"):
                            continue
                    ok = False
                    break
                if isinstance(p, ast.Call) and p.args and p.args[0] is n:
                    q = qual_of_call(an, p.func)
                    if short(q) in ("heappush", "heappop", "heapify") and q and ("heapq" in q or q in (
                            "heappush", "heappop", "heapify")):
                        continue
                    if q == "len":
                        continue
                    ok = False
                    break
                if isinstance(p, ast.Call) and q_is(an, p, "len"):
                    continue
                if isinstance(p, ast.Subscript) and p.value is n and const_int(p.slice) == 0 and \
                        isinstance(p.ctx, ast.Load):
                    continue
                if isinstance(p, (ast.While, ast.If)) and p.test is n:
                    continue
                if isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Not):
                    continue
                if isinstance(p, ast.BoolOp):
                    continue
                if isinstance(p, ast.Compare) and isinstance(p.comparators[0], ast.Constant):
                    continue
                ok = False
                break
            if ok and sym.scope is not None:
                self.pure_heaps.add(sym)

    def _find_inf_as_int(self) -> None:
        """float('inf') używane razem z liczbami całkowitymi → duża stała całkowita."""
        has_inf = False
        for node in ast.walk(self.tree):
            t = self.inf.node_ty.get(id(node))
            if t is not None and t.k == "inf":
                has_inf = True
                break
        if not has_inf:
            return
        for sym, t in self.inf.ty.items():
            if t.k == "inf":
                continue
            if _has_kind(t, "int") and not _has_kind(t, "float"):
                for node in ast.walk(self.tree):
                    if isinstance(node, (ast.Assign, ast.AugAssign)):
                        vals = [node.value]
                        for v in vals:
                            vt = self.inf.node_ty.get(id(v))
                            if vt is not None and _has_kind(vt, "inf"):
                                tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                                for tg in tgts:
                                    base = tg
                                    while isinstance(base, (ast.Subscript, ast.Attribute)):
                                        base = base.value
                                    s = self.an.sym_of.get(id(base))
                                    if s is not None and _has_kind(self.inf.ty.get(s, BOT), "int"):
                                        self.inf_as_int = True
                                        return
                    if isinstance(node, ast.Call) and node.args:
                        for a in node.args:
                            vt = self.inf.node_ty.get(id(a))
                            if vt is not None and vt.k == "inf":
                                pt = self.inf.node_ty.get(id(node))
                                if pt is not None and _has_kind(pt, "int"):
                                    self.inf_as_int = True
                                    return
                break
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Compare):
                ts = [self.inf.node_ty.get(id(x), BOT) for x in [node.left] + node.comparators]
                if any(t.k == "inf" for t in ts) and any(t.k == "int" for t in ts):
                    self.inf_as_int = True
                    return
            if isinstance(node, ast.Call) and q_is(self.an, node, "min", "max"):
                ts = [self.inf.node_ty.get(id(x), BOT) for x in node.args]
                if any(t.k == "inf" for t in ts) and any(t.k == "int" for t in ts):
                    self.inf_as_int = True
                    return

    def _find_aliases(self) -> None:
        """input = sys.stdin.readline, t = threading.Thread(target=main) itp."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v = node.value
                sym = self.an.sym_of.get(id(node.targets[0]))
                if sym is None:
                    continue
                q = qual_of_call(self.an, v) if isinstance(v, (ast.Attribute, ast.Name)) else None
                if q in ("sys.stdin.readline", "sys.stdin.buffer.readline", "input"):
                    self.readline_alias.add(sym)
                    self.skip_stmts.add(id(node))
                    if q != "input":
                        self.info("readline", "`input = sys.stdin.readline`: w C++ szybkie wczytywanie daje "
                                  "`ios::sync_with_stdio(false)` — linia nadal zawiera '\\n', jak w Pythonie.",
                                  node.lineno)
                elif isinstance(v, ast.Lambda) and isinstance(v.body, ast.Call) and \
                        qual_of_call(self.an, v.body.func) in ("sys.stdin.readline", "sys.stdin.buffer.readline"):
                    self.readline_alias.add(sym)
                    self.skip_stmts.add(id(node))
                elif isinstance(v, ast.Call) and qual_of_call(self.an, v.func) == "threading.Thread":
                    for kw in v.keywords:
                        if kw.arg == "target" and isinstance(kw.value, ast.Name):
                            fs = self.an.sym_of.get(id(kw.value))
                            if fs is not None:
                                self.thread_targets[sym] = self.cn(fs)
                    self.skip_stmts.add(id(node))

    def _find_iters(self) -> None:
        """it = iter(lista) → indeks następnego elementu; next(it) → lista[it++]."""
        self.iter_src: dict = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and \
                    isinstance(node.value, ast.Call) and q_is(self.an, node.value, "iter") and \
                    len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Name):
                src = self.an.sym_of.get(id(node.value.args[0]))
                sym = self.an.sym_of.get(id(node.targets[0]))
                if src is not None and sym is not None and self.inf.ty.get(src, BOT).k in ("list", "str"):
                    self.iter_src[sym] = src

    def is_readline_alias(self, func) -> bool:
        if isinstance(func, ast.Name):
            s = self.an.sym_of.get(id(func))
            return s is not None and s in self.readline_alias
        return False

    # ------------------------------------------------------------------ odczyty (tryb tokenowy / liniowy)
    def _find_io(self) -> None:
        sites = []
        order = [0]

        def walk(stmts, loops, unit):
            for s in stmts:
                order[0] += 1
                pos = order[0]
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(s.body, (), id(s))
                    continue
                if isinstance(s, ast.ClassDef):
                    for st in s.body:
                        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            walk(st.body, (), id(st))
                    continue
                kind = self.read_kind_stmt(s)
                if kind:
                    sites.append((pos, loops, unit, kind, s))
                else:
                    for sub in ast.walk(s) if not isinstance(s, (ast.For, ast.While, ast.If, ast.Try, ast.With)) \
                            else self._header_exprs(s):
                        if isinstance(sub, ast.Call) and self.is_line_read_call(sub):
                            sites.append((pos, loops, unit, "line", s))
                            break
                for f in ("body", "orelse", "finalbody"):
                    sub = getattr(s, f, None)
                    if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                        nl = loops + (id(s),) if isinstance(s, (ast.For, ast.While)) and f == "body" else loops
                        walk(sub, nl, unit)
                for h in getattr(s, "handlers", []) or []:
                    walk(h.body, loops, unit)

        walk(self.tree.body, (), 0)
        self.read_sites = sites
        lines = [x for x in sites if x[3] == "line"]
        self.has_line_reads = bool(lines)
        for pos, loops, unit, kind, s in sites:
            if kind != "token":
                continue
            for lp, ll, lu, lk, _ in lines:
                if lu != unit or lp > pos or set(loops) & set(ll):
                    self.io_token_needs_skip.add(id(s))
                    break

    def _header_exprs(self, s):
        out = []
        for f in ("test", "iter", "items"):
            v = getattr(s, f, None)
            if isinstance(v, ast.AST):
                out.extend(ast.walk(v))
            elif isinstance(v, list):
                for it in v:
                    out.extend(ast.walk(it))
        return out

    def is_line_read_call(self, node) -> bool:
        """input() / sys.stdin.readline() (jako źródło całej linii)."""
        if not isinstance(node, ast.Call):
            return False
        q = qual_of_call(self.an, node.func)
        if q in ("input", "sys.stdin.readline", "sys.stdin.buffer.readline"):
            return True
        return self.is_readline_alias(node.func)

    def line_source(self, node):
        """Jeśli node to input()/readline() z opcjonalnym .strip()/.rstrip(), zwraca węzeł wywołania."""
        while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("strip", "rstrip", "lstrip") and not node.args:
            node = node.func.value
        if self.is_line_read_call(node):
            return node
        return None

    def split_source(self, node):
        """X.split() gdzie X to odczyt linii → wywołanie odczytu."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "split" and \
                not node.args and not node.keywords:
            return self.line_source(node.func.value)
        return None

    def conv_of(self, f) -> str | None:
        q = qual_of_call(self.an, f) if isinstance(f, (ast.Name, ast.Attribute)) else None
        return q if q in ("int", "float", "str") else None

    def read_tuple_pattern(self, value):
        """map(int, input().split()) / input().split() / [int(x) for x in input().split()] → (konwersja, odczyt)."""
        v = value
        if isinstance(v, ast.Call) and q_is(self.an, v, "list", "tuple") and len(v.args) == 1:
            v = v.args[0]
        if isinstance(v, ast.Call) and q_is(self.an, v, "map") and len(v.args) == 2:
            conv = self.conv_of(v.args[0])
            src = self.split_source(v.args[1])
            if conv and src is not None:
                return conv, src
        if isinstance(v, ast.ListComp) and len(v.generators) == 1 and not v.generators[0].ifs:
            g = v.generators[0]
            src = self.split_source(g.iter)
            if src is not None and isinstance(g.target, ast.Name) and isinstance(v.elt, ast.Call) and \
                    len(v.elt.args) == 1 and isinstance(v.elt.args[0], ast.Name) and v.elt.args[0].id == g.target.id:
                conv = self.conv_of(v.elt.func)
                if conv:
                    return conv, src
        src = self.split_source(v)
        if src is not None:
            return "str", src
        return None

    def read_scalar_pattern(self, value):
        """int(input()) / float(input()) / int(input().strip())."""
        if isinstance(value, ast.Call) and len(value.args) == 1 and not value.keywords:
            conv = self.conv_of(value.func)
            if conv in ("int", "float"):
                src = self.line_source(value.args[0])
                if src is not None:
                    return conv, src
        return None

    def read_kind_stmt(self, s) -> str | None:
        if isinstance(s, ast.Assign) and len(s.targets) == 1:
            t = s.targets[0]
            if self.read_scalar_pattern(s.value) and isinstance(t, (ast.Name, ast.Subscript, ast.Attribute)):
                return "token"
            if isinstance(t, (ast.Tuple, ast.List)) and all(isinstance(e, (ast.Name, ast.Subscript, ast.Attribute))
                                                            for e in t.elts):
                if self.read_tuple_pattern(s.value):
                    return "token"
            for sub in ast.walk(s.value):
                if isinstance(sub, ast.Call) and self.is_line_read_call(sub):
                    if isinstance(s.value, ast.ListComp) and self._comp_token_read(s.value):
                        return "token"
                    return "line"
        if isinstance(s, ast.Expr):
            for sub in ast.walk(s.value):
                if isinstance(sub, ast.Call) and self.is_line_read_call(sub):
                    return "line"
        return None

    def _comp_token_read(self, comp) -> bool:
        """[int(input()) for _ in range(n)] → wektor wczytywany tokenami."""
        return len(comp.generators) == 1 and not comp.generators[0].ifs and \
            self.read_scalar_pattern(comp.elt) is not None and q_is(self.an, comp.generators[0].iter, "range")

    # ------------------------------------------------------------------ stałe globalne
    def _find_consts(self) -> None:
        """Zmienne modułu przypisane raz stałą wartością (MOD = 10**9 + 7) → `const` na poziomie globalnym."""
        stores: dict[Symbol, int] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                s = self.an.sym_of.get(id(node))
                if s is not None:
                    stores[s] = stores.get(s, 0) + 1
        for st in self.tree.body:
            if not (isinstance(st, (ast.Assign, ast.AnnAssign)) and getattr(st, "value", None) is not None):
                continue
            targets = st.targets if isinstance(st, ast.Assign) else [st.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            sym = self.an.sym_of.get(id(targets[0]))
            if sym is None or sym.scope is not self.an.module or stores.get(sym, 0) != 1 or \
                    sym in self.inf.mutated or sym.declared_global:
                continue
            t = self.sym_type(sym)
            if t.k not in ("int", "float", "bool", "str", "char", "inf"):
                continue
            if not self._is_const_expr(st.value):
                continue
            self.consts[sym] = st
            self.skip_stmts.add(id(st))

    def _is_const_expr(self, node) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (int, float, str, bool))
        if isinstance(node, ast.BinOp):
            return self._is_const_expr(node.left) and self._is_const_expr(node.right)
        if isinstance(node, ast.UnaryOp):
            return self._is_const_expr(node.operand)
        if isinstance(node, ast.Name):
            s = self.an.sym_of.get(id(node))
            return s is not None and s in self.consts
        if isinstance(node, ast.Call) and q_is(self.an, node, "float", "int") and len(node.args) == 1 and \
                isinstance(node.args[0], ast.Constant) and not node.keywords:
            return True
        if isinstance(node, ast.Attribute) and qual_of_call(self.an, node) in ("math.pi", "math.e", "math.inf",
                                                                              "math.tau", "sys.maxsize"):
            return True
        return False

    # ------------------------------------------------------------------ jednostki: main, funkcje
    def make_plan(self, scope, body, params=()) -> UnitPlan:
        plan = UnitPlan(self, scope, body, params)
        self.plans.append(plan)
        plan.on_alias = self.all_alias
        for drop in plan.resolve_conflicts(lambda s: self.sym_ct(s) if s in self.inf.ty else "?"):
            t = self.sym_type(drop)
            base = drop.root().name
            name = f"{base}_{tag(t)}"
            k = 2
            while name in self.taken:
                name = f"{base}_{tag(t)}{k}"
                k += 1
            self.taken.add(name)
            self.cname[drop] = name
        self.all_alias.update(plan.alias)
        return plan

    def emit_function(self, fi: FuncInfo, method_of=None) -> list[Line]:
        node = fi.node
        saved = (self.buf, self.ind, self.fi, self.plan, self.ret_type, self.memo_name, self.memo_key, self.loops)
        self.buf, self.ind, self.fi, self.loops = [], 0, fi, []
        self.py = node.lineno
        fsym = self.an.sym_of.get(id(node))
        name = self.cn(fsym) if fsym else fi.name
        self.func_cname[fi.fid] = name
        ret = self.ret_ct(fi)
        self.ret_type = fi.ret
        params = self.param_list(fi, with_defaults=not self.prototypes_needed or method_of is not None)
        self.plan = self.make_plan(fi.scope, node.body, [p for p in fi.params])
        if fi.yields:
            self.warn("todo", f"Funkcja `{fi.name}` jest generatorem (`yield`) — generatory nie są tłumaczone.",
                      node.lineno, "error")
        self.memo_name = self.memo_key = None
        if fi.memo:
            self._memo_decl(fi, name, ret)
        prefix = ""
        if method_of is not None:
            if fi.name == "__init__":
                self.out(f"{method_of}({params}) {{", node.lineno)
                ret = None
            else:
                mname = {"__lt__": "operator<", "__eq__": "operator==", "__str__": "toStr", "__repr__": "toStr",
                         "__le__": "operator<=", "__gt__": "operator>", "__ge__": "operator>=",
                         "__ne__": "operator!="}.get(fi.name, name)
                const = " const" if fi.name in ("__lt__", "__eq__", "__str__", "__repr__", "__le__", "__gt__",
                                                "__ge__", "__ne__") else ""
                static = "static " if any(isinstance(d, ast.Name) and d.id == "staticmethod"
                                          for d in node.decorator_list) else ""
                self.out(f"{static}{ret} {mname}({params}){const} {{", node.lineno)
        else:
            self.out(f"{prefix}{ret} {name}({params}) {{", node.lineno)
        with self.indent():
            if self.memo_name:
                key_t = self.memo_key
                self.out(f"auto key = {key_t};", node.lineno)
                self.out(f"if (auto it = {self.memo_name}.find(key); it != {self.memo_name}.end()) return it->second;",
                         node.lineno)
            self.body(node.body, docstring_ok=True)
            if ret not in (None, "void") and not _always_returns(node.body):
                if fi.ret.k == "opt":
                    self.out("return nullopt;", 0)
                else:
                    self.out("return {};", 0)
        self.out("}", 0)
        lines = self.buf
        self.buf, self.ind, self.fi, self.plan, self.ret_type, self.memo_name, self.memo_key, self.loops = saved
        return lines

    def _memo_decl(self, fi: FuncInfo, name: str, ret: str) -> None:
        if ret == "void":
            self.warn("todo", f"@lru_cache na funkcji `{fi.name}` bez wyniku — pominięto memoizację.", fi.node.lineno)
            return
        pts = [self.ct(self.sym_type(p)) for p in fi.params]
        names = [self.cn(p) for p in fi.params]
        if len(pts) == 1:
            kt, key = pts[0], names[0]
        else:
            kt = f"tuple<{', '.join(pts)}>"
            key = f"make_tuple({', '.join(names)})"
        mname = self.fresh(f"memo_{name}")
        self.memo_name = mname
        self.memo_key = key
        decl = f"map<{kt}, {ret}> {mname};  // @lru_cache: zapamiętane wyniki {fi.name}()"
        self.memo_decls.append(Line(0, decl, fi.node.lineno))
        self.info("memo", f"`@lru_cache` → memoizacja w `map` ({mname}).", fi.node.lineno)

    def ret_ct(self, fi: FuncInfo) -> str:
        r = fi.ret
        if fi.name == "__init__":
            return ""
        if r.k in ("bot", "none") and not fi.has_value_return:
            return "void"
        if r.k == "none":
            return "void"
        if r.k == "bot":
            self.warn("type-unknown", f"Nie udało się ustalić typu wyniku funkcji `{fi.name}` — użyto `auto`.",
                      fi.node.lineno)
            return "auto"
        if contains_top(r):
            self.warn("type-conflict", f"Funkcja `{fi.name}` zwraca wartości różnych typów — użyto `auto`.",
                      fi.node.lineno)
            return "auto"
        return self.ct(r)

    def param_list(self, fi: FuncInfo, with_defaults: bool = True) -> str:
        node = fi.node
        args = node.args
        pos = list(fi.param_nodes)
        all_pos = args.posonlyargs + args.args
        defaults = dict()
        off = len(all_pos) - len(args.defaults)
        for i, d in enumerate(args.defaults):
            defaults[id(all_pos[off + i])] = d
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            if d is not None:
                defaults[id(a)] = d
        out = []
        for i, (sym, a) in enumerate(zip(fi.params, pos)):
            t = self.sym_type(sym)
            name = self.cn(sym)
            if t.k in ("bot",) or contains_top(t):
                if t.k == "bot":
                    self.warn("type-unknown", f"Parametr `{sym.name}` funkcji `{fi.name}` nie ma znanego typu "
                              "(funkcja nie jest wywoływana?) — użyto `auto`.", node.lineno)
                decl = f"auto {name}"
            else:
                decl = self.param_decl(fi, i, sym, t, name)
            if with_defaults and id(a) in defaults:
                d = defaults[id(a)]
                if isinstance(d, (ast.List, ast.Dict, ast.Set)) and not getattr(d, "elts", getattr(d, "keys", None)):
                    self.warn("mutable-default", "Domyślna wartość-lista/słownik w Pythonie jest współdzielona "
                              "między wywołaniami — w C++ każde wywołanie dostaje nową.", node.lineno, "info")
                decl += " = " + self.x(d, t).c
            out.append(decl)
        if fi.vararg is not None:
            self.warn("todo", f"`*{fi.vararg.arg}` w funkcji `{fi.name}` nie jest obsługiwane.", node.lineno, "error")
        return ", ".join(out)

    def param_decl(self, fi: FuncInfo, i: int, sym: Symbol, t: Ty, name: str) -> str:
        cpp = self.ct(t)
        if is_scalar(t) or t.k in ("none", "opt") or (t.k == "tuple" and all(is_scalar(a) for a in t.args)):
            return f"{cpp} {name}"
        rebound = sym in self.inf.rebound
        mutated = sym in self.inf.mutated
        if rebound:
            return f"{cpp} {name}"
        if mutated:
            if i in fi.rvalue_args:
                return f"{cpp} {name}"
            return f"{cpp}& {name}"
        if t.k == "class":
            if fi.name.startswith("__") and fi.name.endswith("__"):
                return f"const {cpp}& {name}"
            return f"{cpp}& {name}"
        self.const_params.add(sym)
        return f"const {cpp}& {name}"

    def lambda_sig(self, fi: FuncInfo) -> tuple[str, str]:
        params = self.param_list(fi)
        return params, self.ret_ct(fi)

    # ------------------------------------------------------------------ struktury
    def emit_class(self, ci) -> list[Line]:
        saved = (self.buf, self.ind, self.cls_ctx)
        self.buf, self.ind, self.cls_ctx = [], 0, ci
        node = ci.node
        self.py = node.lineno
        name = self.class_cname(ci.name)
        if node.bases and not all(isinstance(b, ast.Name) and b.id == "object" for b in node.bases):
            self.warn("todo", f"Dziedziczenie klas (`{ci.name}`) nie jest tłumaczone.", node.lineno, "error")
        self.out(f"struct {name} {{", node.lineno)
        with self.indent():
            for fname in ci.field_order or list(ci.fields):
                t = ci.fields.get(fname, BOT)
                init = ""
                for st in node.body:
                    if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.target.id == fname \
                            and st.value is not None:
                        init = " = " + self.x(st.value, t).c
                if not init and t.k in ("int", "float", "bool", "char", "inf"):
                    init = " = 0" if t.k != "bool" else " = false"
                fl = self.field_cname(fname)
                self.out(f"{self.ct(t, top_ok=False)} {fl}{init};", node.lineno)
            init = ci.methods.get("__init__")
            if init is not None:
                if init.params:
                    self.out(f"{name}() = default;", 0)
                self.buf.extend(self._indent_lines(self.emit_function(init, method_of=name), self.ind))
            elif ci.dataclass:
                self.out(f"auto operator<=>(const {name}&) const = default;", 0)
            for m, fi in ci.methods.items():
                if m == "__init__":
                    continue
                self.buf.extend(self._indent_lines(self.emit_function(fi, method_of=name), self.ind))
        self.out("};", 0)
        lines = self.buf
        self.buf, self.ind, self.cls_ctx = saved
        return lines

    def field_cname(self, name: str) -> str:
        if name in RESERVED_ALL:
            return name + "_"
        return name

    @staticmethod
    def _indent_lines(lines, n):
        out = []
        for ln in lines:
            out.append(Line(ln.ind + n, ln.text, ln.py))
        return out

    # ------------------------------------------------------------------ cały program
    def translate(self) -> dict:
        self.memo_decls: list[Line] = []
        self.prepare()
        tree = self.tree
        top_funcs = []
        named_lambdas = []
        classes = []
        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top_funcs.append(self.inf.funcs[id(st)])
            elif isinstance(st, ast.ClassDef):
                classes.append(self.inf.classes[st.name])
            elif isinstance(st, ast.Assign) and isinstance(st.value, ast.Lambda) and len(st.targets) == 1 and \
                    isinstance(st.targets[0], ast.Name) and id(st) not in self.skip_stmts:
                sym = self.an.sym_of.get(id(st.targets[0]))
                stores = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                             and self.an.sym_of.get(id(n)) is sym)
                if stores == 1:
                    named_lambdas.append((st, self.inf.funcs[id(st.value)]))
                    self.skip_stmts.add(id(st))
            elif isinstance(st, ast.If) and _is_main_guard(st):
                for sub in st.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        top_funcs.append(self.inf.funcs[id(sub)])
        # prototypy, jeśli funkcja wywołuje funkcję zdefiniowaną później
        self.prototypes_needed = self._needs_prototypes(top_funcs)
        struct_lines = []
        for ci in classes:
            struct_lines.append(self.emit_class(ci))
        func_lines = []
        for st, fi in named_lambdas:
            fsym = self.an.sym_of.get(id(st.targets[0]))
            self.cname.setdefault(fi.node and fsym, self.cn(fsym))
            func_lines.append(self.emit_lambda_function(st, fi, fsym))
        for fi in top_funcs:
            func_lines.append(self.emit_function(fi))
        main_lines = self.emit_main()
        global_lines = self.emit_globals()
        return self.assemble(struct_lines, global_lines, func_lines, main_lines, top_funcs)

    def emit_lambda_function(self, st, fi: FuncInfo, fsym) -> list[Line]:
        """f = lambda x: … na poziomie modułu → zwykła funkcja C++."""
        saved = (self.buf, self.ind, self.fi, self.plan, self.ret_type)
        self.buf, self.ind, self.fi = [], 0, fi
        self.py = st.lineno
        name = self.cn(fsym)
        self.func_cname[fi.fid] = name
        self.plan = self.make_plan(fi.scope, [], fi.params)
        ret = self.ret_ct(fi)
        self.ret_type = fi.ret
        params = self.param_list(fi)
        self.out(f"{ret} {name}({params}) {{", st.lineno)
        with self.indent():
            body = self.x(fi.node.body, fi.ret if ret != "void" else None)
            self.out(f"return {body.c};" if ret != "void" else f"{body.c};", st.lineno)
        self.out("}", 0)
        lines = self.buf
        self.buf, self.ind, self.fi, self.plan, self.ret_type = saved
        return lines

    def _needs_prototypes(self, funcs) -> bool:
        seen = set()
        for fi in funcs:
            seen.add(fi.fid)
            for sub in ast.walk(fi.node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    s = self.an.sym_of.get(id(sub.func))
                    if s is not None and s.kind == "func" and s.scope is self.an.module:
                        other = self.inf.funcs.get(id(s.node))
                        if other is not None and other.fid not in seen and other.fid != fi.fid and \
                                other in funcs:
                            return True
        return False

    def emit_main(self) -> list[Line]:
        saved = (self.buf, self.ind, self.fi, self.plan)
        self.buf, self.ind, self.fi = [], 0, None
        self.ret_type = INT
        body = []
        for st in self.tree.body:
            if isinstance(st, ast.If) and _is_main_guard(st):
                body.extend(s for s in st.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)))
            else:
                body.append(st)
        self.plan = self.make_plan(self.an.module, body)
        self.out("int main() {", 0)
        with self.indent():
            if self.fast_io:
                self.out("ios::sync_with_stdio(false);", 0)
                self.out("cin.tie(nullptr);", 0)
            n0 = len(self.buf)
            self.body(body, main=True)
            if len(self.buf) > n0 and self.fast_io:
                self.buf.insert(n0, Line(self.ind, "", 0))
            self.out("return 0;", 0)
        self.out("}", 0)
        lines = self.buf
        self.buf, self.ind, self.fi, self.plan = saved
        return lines

    def emit_globals(self) -> list[Line]:
        saved = (self.buf, self.ind, self.fi)
        self.buf, self.ind, self.fi = [], 0, None
        for sym, st in self.consts.items():
            self.py = st.lineno
            t = self.sym_type(sym)
            val = st.value
            ct = self.ct(t)
            note = ""
            if t.k == "inf":
                e = None
                if self.inf_as_int:
                    code = "LLONG_MAX / 4" if self.int_t == "long long" else "INT_MAX / 2"
                    note = "  // float('inf') → duża liczba całkowita (INF + INF się nie przepełni)"
                else:
                    code = "INFINITY"
                negative = isinstance(val, ast.UnaryOp) and isinstance(val.op, ast.USub)
                if isinstance(val, ast.Call) and val.args and isinstance(val.args[0], ast.Constant) and \
                        str(val.args[0].value).strip().startswith("-"):
                    negative = not negative
                if negative:
                    code = f"-({code})" if " " in code else f"-{code}"
                self.out(f"const {ct} {self.cn(sym)} = {code};{note}", st.lineno)
                continue
            e = self.x(val, t)
            if any(isinstance(s, ast.BinOp) and isinstance(s.op, ast.Pow) for s in ast.walk(val)):
                note = "  // " + ast.unparse(val)
            self.out(f"const {ct} {self.cn(sym)} = {e.c};{note}", st.lineno)
        globs = []
        seen = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                s = self.S(node)
                if s is None or s in seen:
                    continue
                root = s.root()
                if root.scope is self.an.module and root.kind == "var" and (root.is_global or root.declared_global) \
                        and s not in self.consts and s not in self.readline_alias and s not in self.thread_targets:
                    seen.add(s)
                    globs.append((getattr(node, "lineno", 0), s))
        globs.sort(key=lambda x: x[0])
        for line, s in globs:
            self.py = line
            init = self.global_inits.get(s)
            ct = self.sym_ct(s)
            if ct == "auto":
                ct = self.int_t
            if init is not None:
                self.out(f"{ct} {self.cn(s)} = {init};", line)
            else:
                self.out(f"{ct} {self.cn(s)}{self.default_init(s)};", line)
        lines = self.buf
        self.buf, self.ind, self.fi = saved
        return lines

    def assemble(self, structs, globs, funcs, main, top_funcs) -> dict:
        if self.int_t == "int":
            has_mul = any(isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Pow, ast.LShift))
                          for n in ast.walk(self.tree))
            if has_mul:
                self.warn("int-type", "Wybrano `int` (32 bity, do 2 147 483 647): iloczyny (np. `a * b % MOD`), "
                          "potęgi i duże sumy mogą się przepełnić — Python tego nie zauważy. Bezpieczniej: `long long`.",
                          0, "warn")
        out: list[Line] = []

        def add(text, py=0):
            out.append(Line(0, text, py))

        add("// Przetłumaczone z Pythona przez OI Forge (py2cpp)")
        add("#include <bits/stdc++.h>")
        add("using namespace std;")
        helpers = runtime.render(self.helpers, self.int_t)
        if helpers:
            add("")
            add("// ===== pomocnicze funkcje z semantyką Pythona =====")
            for ln in helpers.split("\n"):
                add(ln)
            add("// ===== koniec funkcji pomocniczych =====")
        if self.need_inf_const:
            add("")
            add(f"const {self.int_t} {self.inf_name} = {'LLONG_MAX / 4' if self.int_t == 'long long' else 'INT_MAX / 2'};"
                "  // float('inf') → duża liczba całkowita (INF + INF się nie przepełni)")
        for block in structs:
            add("")
            out.extend(block)
        if globs:
            add("")
            out.extend(globs)
        if self.memo_decls:
            add("")
            out.extend(self.memo_decls)
        if self.prototypes_needed and top_funcs:
            add("")
            for fi in top_funcs:
                saved = self.buf
                self.buf = []
                self.fi = fi
                params = self.param_list(fi, with_defaults=True)
                self.fi = None
                self.buf = saved
                add(f"{self.ret_ct(fi)} {self.func_cname.get(fi.fid, fi.name)}({params});", fi.node.lineno)
        for block in funcs:
            add("")
            out.extend(block)
        add("")
        out.extend(main)
        text_lines = []
        line_map = []
        for i, ln in enumerate(out, 1):
            text_lines.append(("    " * ln.ind + ln.text) if ln.text else "")
            if ln.py:
                line_map.append([i, ln.py])
        cpp = "\n".join(text_lines) + "\n"
        for w in self.warnings:
            more = sorted(set(w.pop("lines", [])))
            if more:
                shown = ", ".join(map(str, more[:12])) + (" …" if len(more) > 12 else "")
                w["message"] += f" (także w {'linii' if len(more) == 1 else 'liniach'}: {shown})"
                w["also"] = more
        self.warnings.sort(key=lambda w: (w["line"] or 0, {"error": 0, "warn": 1, "info": 2}[w["severity"]]))
        return {"cpp": cpp, "warnings": self.warnings, "line_map": line_map, "helpers": sorted(self.helpers)}


def q_is(an, call, *names) -> bool:
    return isinstance(call, ast.Call) and qual_of_call(an, call.func) in names


def _has_kind(t: Ty, k: str) -> bool:
    return t.k == k or any(_has_kind(a, k) for a in t.args)


def _is_main_guard(st: ast.If) -> bool:
    t = st.test
    return isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__" and \
        len(t.comparators) == 1 and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value == "__main__"


def _always_returns(stmts) -> bool:
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _always_returns(last.body) and _always_returns(last.orelse)
    if isinstance(last, ast.While) and isinstance(last.test, ast.Constant) and last.test.value:
        return not any(isinstance(n, ast.Break) for n in _shallow_walk(last.body))
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) and isinstance(last.value.func, ast.Name) and \
            last.value.func.id in ("exit", "quit"):
        return True
    if isinstance(last, ast.Try):
        return _always_returns(last.body) and all(_always_returns(h.body) for h in last.handlers)
    return False


def _shallow_walk(stmts):
    """Węzły w bloku bez wchodzenia do zagnieżdżonych pętli i funkcji (dla break)."""
    stack = list(stmts)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.For, ast.While)):
            stack.extend(n.orelse)
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        for ch in ast.iter_child_nodes(n):
            if isinstance(ch, ast.stmt):
                stack.append(ch)
            elif isinstance(ch, ast.excepthandler):
                stack.extend(ch.body)


__all__ = ["Emitter", "Line", "q_is", "join", "NONE"]

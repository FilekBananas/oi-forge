"""Inferencja typów: przepływowa analiza całego programu do punktu stałego.

Każdy symbol (zmienna po podziale na sieci) dostaje typ będący złączeniem (`join`) typów wszystkich
przypisywanych mu wartości. Kontenery są doprecyzowywane przez użycia: `a.append(x)`, `g[u].append(v)`,
`d[k] = v`, `heappush(h, x)`… Typy parametrów funkcji pochodzą z wywołań (i adnotacji), typ wyniku —
z instrukcji `return`. Iterujemy aż nic się nie zmienia (krata ma skończoną wysokość dzięki limitowi głębokości).
"""
from __future__ import annotations

import ast

from .analysis import Analysis, Symbol
from .types import (BOOL, BOT, CHAR, FILE, FLOAT, INF, INT, NONE, STR, TOKENS, TOP, Builtin, Cls, Deque, Dict,
                    Func, Lazy, List, Module, Set, Tuple, Ty, elem, is_numeric, join)

STDOUT = Ty("stdout")
STDERR = Ty("stderr")
CMPKEY = "cmpkey"

MODULE_ATTRS = {
    "math.pi": FLOAT, "math.e": FLOAT, "math.tau": FLOAT, "math.inf": INF, "math.nan": FLOAT,
    "sys.maxsize": INT, "sys.stdin": FILE, "sys.stdout": STDOUT, "sys.stderr": STDERR, "sys.argv": List(STR),
    "string.ascii_lowercase": STR, "string.ascii_uppercase": STR, "string.ascii_letters": STR,
    "string.digits": STR, "string.punctuation": STR, "string.hexdigits": STR, "string.whitespace": STR,
    "string.octdigits": STR,
}
MATH_INT = {"gcd", "lcm", "isqrt", "factorial", "comb", "perm", "floor", "ceil", "trunc"}
MATH_FLOAT = {"sqrt", "log", "log2", "log10", "log1p", "exp", "expm1", "sin", "cos", "tan", "asin", "acos", "atan",
              "atan2", "sinh", "cosh", "tanh", "hypot", "fabs", "degrees", "radians", "pow", "fmod", "copysign",
              "dist", "cbrt", "exp2"}
MATH_BOOL = {"isclose", "isinf", "isnan", "isfinite"}

LIST_MUT = {"append", "extend", "insert", "pop", "remove", "sort", "reverse", "clear"}
DICT_MUT = {"pop", "popitem", "setdefault", "update", "clear", "subtract"}
SET_MUT = {"add", "remove", "discard", "pop", "update", "clear", "difference_update", "intersection_update",
           "symmetric_difference_update"}
DEQUE_MUT = {"append", "appendleft", "pop", "popleft", "extend", "extendleft", "rotate", "clear", "remove", "insert"}
MUTATING = LIST_MUT | DICT_MUT | SET_MUT | DEQUE_MUT
HEAPQ_FUNCS = {"heappush", "heappop", "heapify", "heappushpop", "heapreplace"}
BISECT_INSORT = {"insort", "insort_left", "insort_right"}


class FuncInfo:
    def __init__(self, node, scope, fid: str, name: str, cls: str | None = None):
        self.node = node
        self.scope = scope
        self.fid = fid
        self.name = name
        self.cls = cls
        self.is_lambda = isinstance(node, ast.Lambda)
        self.params: list[Symbol] = []
        self.param_nodes: list[ast.arg] = []
        self.self_sym: Symbol | None = None
        self.ret = BOT
        self.has_value_return = False
        self.has_bare_return = False
        self.memo = False
        self.calls = 0
        self.rvalue_args: set[int] = set()   # indeksy parametrów, do których trafia r-wartość
        self.ret_nonneg = True
        self.yields = False
        self.nested = False                  # funkcja zagnieżdżona → lambda w C++
        self.recursive = False
        self.vararg = None

    def __repr__(self):
        return f"<Func {self.name} {self.fid}>"


class ClassInfo:
    def __init__(self, name: str, node: ast.ClassDef, scope):
        self.name = name
        self.node = node
        self.scope = scope
        self.fields: dict[str, Ty] = {}
        self.methods: dict[str, FuncInfo] = {}
        self.dataclass = False
        self.field_order: list[str] = []


def qual_of_call(an: Analysis, func: ast.AST) -> str | None:
    """Kwalifikowana nazwa wywoływanej funkcji modułu/builtinu: 'heapq.heappush', 'int', 'sys.stdin.readline'."""
    if isinstance(func, ast.Name):
        sym = an.sym_of.get(id(func))
        if sym is None:
            return None
        if sym.kind == "builtin":
            return func.id
        if sym.kind == "import":
            return sym.qual
        return None
    if isinstance(func, ast.Attribute):
        base = qual_of_call(an, func.value)
        if base is None:
            return None
        if base in ("sys", "math", "heapq", "bisect", "collections", "itertools", "functools", "string", "sys.stdin",
                    "sys.stdout", "sys.stderr", "sys.stdin.buffer", "operator", "random", "threading", "os", "re",
                    "copy", "statistics", "array", "time", "decimal", "fractions", "typing", "dataclasses") \
                or base.startswith(("sys.", "math", "collections", "itertools", "heapq", "bisect", "functools")):
            return f"{base}.{func.attr}"
        return None
    return None


def short(q: str | None) -> str:
    return (q or "").rsplit(".", 1)[-1]


def is_stdin_read_split(an: Analysis, node: ast.AST) -> bool:
    """sys.stdin.read().split() / sys.stdin.buffer.read().split() / open(0).read().split()."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "split"
            and not node.args):
        return False
    inner = node.func.value
    if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "read"):
        return False
    src = inner.func.value
    q = qual_of_call(an, src) if isinstance(src, (ast.Attribute, ast.Name)) else None
    if q in ("sys.stdin", "sys.stdin.buffer"):
        return True
    return isinstance(src, ast.Call) and qual_of_call(an, src.func) == "open"


class Infer:
    def __init__(self, an: Analysis, int_type: str = "long long"):
        self.an = an
        self.ty: dict[Symbol, Ty] = {}
        self.cache: dict[int, Ty] = {}
        self.node_ty: dict[int, Ty] = {}
        self.funcs: dict[int, FuncInfo] = {}          # id(FunctionDef/Lambda) -> FuncInfo
        self.func_by_fid: dict[str, FuncInfo] = {}
        self.classes: dict[str, ClassInfo] = {}
        self.heap_syms: set[Symbol] = set()
        self.heap_any = False
        self.mutated: set[Symbol] = set()
        self.rebound: set[Symbol] = set()
        self.changed = False
        self.final = False
        self.cur: FuncInfo | None = None
        self.uses_inf_as_int = False
        self._register()

    # ------------------------------------------------------------------ rejestracja funkcji i klas
    def _register(self) -> None:
        an = self.an
        n = 0
        for node in ast.walk(an.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                n += 1
                scope = an.scope_of[id(node)]
                cls = scope.parent.name if scope.parent and scope.parent.kind == "class" else None
                name = node.name if not isinstance(node, ast.Lambda) else "<lambda>"
                fi = FuncInfo(node, scope, f"f{n}", name, cls)
                fi.nested = scope.parent is not None and scope.parent.unit().kind in ("function", "lambda")
                args = node.args
                plist = args.posonlyargs + args.args
                syms = [an.sym_of[id(a)] for a in plist]
                if cls and plist and not any(isinstance(d, ast.Name) and d.id == "staticmethod"
                                             for d in getattr(node, "decorator_list", [])):
                    fi.self_sym = syms[0]
                    syms = syms[1:]
                    plist = plist[1:]
                fi.params = syms + [an.sym_of[id(a)] for a in args.kwonlyargs]
                fi.param_nodes = plist + list(args.kwonlyargs)
                fi.vararg = args.vararg
                for d in getattr(node, "decorator_list", []):
                    dn = d.func if isinstance(d, ast.Call) else d
                    q = qual_of_call(an, dn) or (dn.attr if isinstance(dn, ast.Attribute) else "")
                    if short(q) in ("lru_cache", "cache"):
                        fi.memo = True
                if not isinstance(node, ast.Lambda):
                    for sub in ast.walk(node):
                        if isinstance(sub, (ast.Yield, ast.YieldFrom)) and self._owner(sub) is node:
                            fi.yields = True
                self.funcs[id(node)] = fi
                self.func_by_fid[fi.fid] = fi
                if fi.self_sym is not None:
                    self.ty[fi.self_sym] = Cls(cls)
            elif isinstance(node, ast.ClassDef):
                ci = ClassInfo(node.name, node, an.scope_of[id(node)])
                ci.dataclass = any((isinstance(d, ast.Name) and d.id == "dataclass") or
                                   (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                                   or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                                   for d in node.decorator_list)
                self.classes[node.name] = ci
        for fi in self.funcs.values():
            if fi.cls and fi.cls in self.classes and not fi.is_lambda:
                self.classes[fi.cls].methods[fi.name] = fi
                if fi.name in ("__lt__", "__le__", "__gt__", "__ge__", "__eq__", "__ne__") and fi.params:
                    self.ty[fi.params[0]] = Cls(fi.cls)
        for ci in self.classes.values():
            for st in ci.node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    ci.fields[st.target.id] = self.annotation(st.annotation)
                    ci.field_order.append(st.target.id)
        # adnotacje parametrów jako wstępne typy
        for fi in self.funcs.values():
            for sym, a in zip(fi.params, fi.param_nodes):
                if getattr(a, "annotation", None) is not None:
                    t = self.annotation(a.annotation)
                    if t.k != "bot":
                        self.ty[sym] = join(self.ty.get(sym, BOT), t)
            if getattr(fi.node, "returns", None) is not None:
                t = self.annotation(fi.node.returns)
                if t.k not in ("bot", "none"):
                    fi.ret = t

    def _owner(self, node):
        p = self.an.parent.get(id(node))
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            p = self.an.parent.get(id(p))
        return p

    def annotation(self, a) -> Ty:
        if a is None:
            return BOT
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            try:
                a = ast.parse(a.value, mode="eval").body
            except SyntaxError:
                return BOT
        if isinstance(a, ast.Constant) and a.value is None:
            return NONE
        if isinstance(a, ast.Name) or isinstance(a, ast.Attribute):
            n = a.id if isinstance(a, ast.Name) else a.attr
            return {"int": INT, "float": FLOAT, "str": STR, "bool": BOOL, "list": List(BOT), "List": List(BOT),
                    "dict": Dict(BOT, BOT), "Dict": Dict(BOT, BOT), "set": Set(BOT), "Set": Set(BOT),
                    "deque": Deque(BOT), "None": NONE}.get(n, Cls(n) if n in self.classes else BOT)
        if isinstance(a, ast.Subscript):
            base = a.value.id if isinstance(a.value, ast.Name) else (a.value.attr if isinstance(a.value, ast.Attribute) else "")
            sl = a.slice
            items = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            ts = [self.annotation(x) for x in items]
            b = base.lower()
            if b in ("list", "sequence", "iterable"):
                return List(ts[0])
            if b in ("set", "frozenset"):
                return Set(ts[0])
            if b == "deque":
                return Deque(ts[0])
            if b in ("dict", "mapping", "defaultdict", "ordereddict") and len(ts) == 2:
                return Dict(ts[0], ts[1], "default" if b == "defaultdict" else "")
            if b == "counter":
                return Dict(ts[0], INT, "counter")
            if b == "tuple":
                if len(items) == 2 and isinstance(items[1], ast.Constant) and items[1].value is Ellipsis:
                    return List(ts[0])
                return Tuple(*ts)
            if b == "optional":
                return join(NONE, ts[0])
        if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):
            return join(self.annotation(a.left), self.annotation(a.right))
        return BOT

    # ------------------------------------------------------------------ pętla punktu stałego
    def run(self) -> None:
        for _ in range(60):
            self.changed = False
            self.cache = {}
            self._pass()
            if not self.changed:
                break
        self.final = True
        self.cache = {}
        self._pass()
        self.node_ty = self.cache
        self._mutation_pass()

    def _pass(self) -> None:
        self.cur = None
        self.block(self.an.tree.body)

    def join_sym(self, sym: Symbol | None, t: Ty) -> None:
        if sym is None or sym.kind == "builtin" or t.k == "bot":
            return
        old = self.ty.get(sym, BOT)
        new = join(old, t)
        if new != old:
            self.ty[sym] = new
            self.changed = True

    def sym(self, node) -> Symbol | None:
        return self.an.sym_of.get(id(node))

    def type_of_sym(self, sym: Symbol) -> Ty:
        return self.ty.get(sym, BOT)

    # ------------------------------------------------------------------ instrukcje
    def block(self, stmts) -> None:
        for s in stmts:
            try:
                self.stmt(s)
            except RecursionError:
                raise
            except Exception:   # noqa: BLE001 — inferencja nigdy nie przerywa tłumaczenia
                pass

    def stmt(self, s) -> None:
        T = self.T
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fi = self.funcs[id(s)]
            for d in s.args.defaults + [x for x in s.args.kw_defaults if x is not None]:
                T(d)
            self._bind_defaults(fi)
            prev, self.cur = self.cur, fi
            self.block(s.body)
            self.cur = prev
            if not fi.yields and fi.ret.k == "bot" and not fi.has_value_return:
                pass
            return
        if isinstance(s, ast.ClassDef):
            prev, self.cur = self.cur, None
            for st in s.body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.stmt(st)
                elif isinstance(st, ast.AnnAssign) and st.value is not None:
                    ci = self.classes[s.name]
                    t = T(st.value)
                    old = ci.fields.get(st.target.id, BOT) if isinstance(st.target, ast.Name) else BOT
                    if isinstance(st.target, ast.Name):
                        nt = join(old, t)
                        if nt != old:
                            ci.fields[st.target.id] = nt
                            self.changed = True
            self.cur = prev
            return
        if isinstance(s, ast.Assign):
            t = T(s.value)
            for tg in s.targets:
                self.bind(tg, t, s.value)
            return
        if isinstance(s, ast.AnnAssign):
            at = self.annotation(s.annotation)
            if s.value is not None:
                t = join(at, T(s.value)) if at.k != "bot" else T(s.value)
                self.bind(s.target, t, s.value)
            elif at.k != "bot":
                self.bind(s.target, at, None)
            return
        if isinstance(s, ast.AugAssign):
            lt = T(s.target)
            rt = T(s.value)
            if isinstance(s.op, ast.Add) and lt.k == "list":
                self.bind(s.target, join(lt, List(elem(rt))), None)
                return
            if isinstance(s.op, (ast.BitOr, ast.BitAnd, ast.Sub, ast.BitXor)) and lt.k == "set":
                self.bind(s.target, join(lt, rt), None)
                return
            self.bind(s.target, self.binop(s.op, lt, rt, s.value), None)
            return
        if isinstance(s, ast.For):
            it = T(s.iter)
            self.bind(s.target, elem(it), None)
            self.block(s.body)
            self.block(s.orelse)
            return
        if isinstance(s, ast.While):
            T(s.test)
            self.block(s.body)
            self.block(s.orelse)
            return
        if isinstance(s, ast.If):
            T(s.test)
            self.block(s.body)
            self.block(s.orelse)
            return
        if isinstance(s, ast.Return):
            if self.cur is not None:
                if s.value is None:
                    self.cur.has_bare_return = True
                    self._join_ret(self.cur, NONE)
                else:
                    self.cur.has_value_return = True
                    self._join_ret(self.cur, T(s.value))
            return
        if isinstance(s, ast.Expr):
            T(s.value)
            return
        if isinstance(s, ast.Try) or (hasattr(ast, "TryStar") and isinstance(s, ast.TryStar)):
            self.block(s.body)
            for h in s.handlers:
                self.block(h.body)
            self.block(s.orelse)
            self.block(s.finalbody)
            return
        if isinstance(s, ast.With):
            for it in s.items:
                T(it.context_expr)
            self.block(s.body)
            return
        if isinstance(s, ast.Delete):
            for t in s.targets:
                T(t)
            return
        if isinstance(s, (ast.Assert,)):
            T(s.test)
            return
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            T(s.subject)
            for c in s.cases:
                self.block(c.body)
            return
        for ch in ast.iter_child_nodes(s):
            if isinstance(ch, ast.expr):
                T(ch)

    def _join_ret(self, fi: FuncInfo, t: Ty) -> None:
        new = join(fi.ret, t)
        if new != fi.ret:
            fi.ret = new
            self.changed = True

    def _bind_defaults(self, fi: FuncInfo) -> None:
        node = fi.node
        args = node.args
        pos = args.posonlyargs + args.args
        defaults = args.defaults
        off = len(pos) - len(defaults)
        for i, d in enumerate(defaults):
            sym = self.an.sym_of.get(id(pos[off + i]))
            self.join_sym(sym, self.T(d))
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            if d is not None:
                self.join_sym(self.an.sym_of.get(id(a)), self.T(d))

    # ------------------------------------------------------------------ wiązanie celów przypisań
    def bind(self, target, t: Ty, value=None) -> None:
        if isinstance(target, ast.Name):
            sym = self.sym(target)
            if sym is not None and sym.kind in ("var", "param"):
                self.join_sym(sym, t)
            self.cache[id(target)] = self.ty.get(sym, t) if sym else t
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            elts = target.elts
            star = [i for i, e in enumerate(elts) if isinstance(e, ast.Starred)]
            if t.k == "tuple" and not star and len(t.args) == len(elts):
                for e, et in zip(elts, t.args):
                    self.bind(e, et)
            elif t.k == "tuple" and star and len(t.args) >= len(elts) - 1:
                si = star[0]
                for i in range(si):
                    self.bind(elts[i], t.args[i])
                tail = len(elts) - si - 1
                for j in range(tail):
                    self.bind(elts[si + 1 + j], t.args[len(t.args) - tail + j])
                mid = BOT
                for x in t.args[si:len(t.args) - tail]:
                    mid = join(mid, x)
                self.bind(elts[si].value, List(mid))
            else:
                e = elem(t)
                for x in elts:
                    if isinstance(x, ast.Starred):
                        self.bind(x.value, List(e))
                    else:
                        self.bind(x, e)
            return
        if isinstance(target, ast.Subscript):
            self.T(target.slice)
            self.refine(target, t)
            self.T(target.value)
            return
        if isinstance(target, ast.Attribute):
            ot = self.T(target.value)
            if ot.k == "class" and ot.extra in self.classes:
                ci = self.classes[ot.extra]
                old = ci.fields.get(target.attr, BOT)
                new = join(old, t)
                if new != old:
                    ci.fields[target.attr] = new
                    if target.attr not in ci.field_order:
                        ci.field_order.append(target.attr)
                    self.changed = True
            return
        if isinstance(target, ast.Starred):
            self.bind(target.value, List(t))

    def refine(self, node, t: Ty) -> None:
        """Doprecyzowanie typu kontenera na ścieżce dostępu (g[u] → g: list[...])."""
        if isinstance(node, ast.Name):
            sym = self.sym(node)
            if sym is not None and sym.kind in ("var", "param"):
                self.join_sym(sym, t)
            return
        if isinstance(node, ast.Subscript):
            base = node.value
            bt = self.T(base)
            if isinstance(node.slice, ast.Slice):
                self.refine(base, t)
                return
            if bt.k == "dict":
                self.refine(base, Dict(self.T(node.slice), t, bt.extra))
            elif bt.k == "deque":
                self.refine(base, Deque(t))
            elif bt.k in ("list", "bot"):
                self.refine(base, List(t))
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("setdefault", "get") and node.args:
            dt = self.T(node.func.value)
            if dt.k == "dict":
                self.refine(node.func.value, Dict(self.T(node.args[0]), t, dt.extra))
            return
        if isinstance(node, ast.Attribute):
            ot = self.T(node.value)
            if ot.k == "class" and ot.extra in self.classes:
                ci = self.classes[ot.extra]
                old = ci.fields.get(node.attr, BOT)
                new = join(old, t)
                if new != old:
                    ci.fields[node.attr] = new
                    if node.attr not in ci.field_order:
                        ci.field_order.append(node.attr)
                    self.changed = True

    # ------------------------------------------------------------------ wyrażenia
    def T(self, node) -> Ty:
        if node is None:
            return NONE
        key = id(node)
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = BOT          # ochrona przed cyklami
        try:
            t = self._T(node)
        except RecursionError:
            raise
        except Exception:   # noqa: BLE001
            t = TOP
        self.cache[key] = t
        return t

    def _T(self, node) -> Ty:
        T = self.T
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return BOOL
            if isinstance(v, int):
                return INT
            if isinstance(v, float):
                return FLOAT
            if isinstance(v, str):
                return CHAR if len(v) == 1 and v.isascii() else STR
            if isinstance(v, bytes):
                return STR
            if v is None:
                return NONE
            return TOP
        if isinstance(node, ast.Name):
            sym = self.sym(node)
            if sym is None:
                return TOP
            if sym.kind == "builtin":
                if node.id in ("True", "False"):
                    return BOOL
                return Builtin(node.id)
            if sym.kind == "func":
                fi = self.funcs.get(id(sym.node))
                return Func(fi.fid) if fi else TOP
            if sym.kind == "class":
                return Ty("classref", (), sym.name)
            if sym.kind == "import":
                q = sym.qual
                if q in MODULE_ATTRS:
                    return MODULE_ATTRS[q]
                if "." not in q:
                    return Module(q)
                return Builtin(q)
            t = self.ty.get(sym, BOT)
            if t.k == "func":
                return t
            return t
        if isinstance(node, ast.BinOp):
            return self.binop(node.op, T(node.left), T(node.right), node.right)
        if isinstance(node, ast.UnaryOp):
            t = T(node.operand)
            if isinstance(node.op, ast.Not):
                return BOOL
            if isinstance(node.op, ast.Invert):
                return INT
            return INT if t.k == "bool" else t
        if isinstance(node, ast.BoolOp):
            ts = [T(v) for v in node.values]
            if all(t.k == "bool" for t in ts):
                return BOOL
            out = BOT
            for t in ts:
                out = join(out, t)
            return out
        if isinstance(node, ast.Compare):
            T(node.left)
            for c in node.comparators:
                T(c)
            return BOOL
        if isinstance(node, ast.IfExp):
            T(node.test)
            return join(T(node.body), T(node.orelse))
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.Subscript):
            return self.subscript(node)
        if isinstance(node, ast.Attribute):
            return self.attribute(node)
        if isinstance(node, ast.List):
            out = BOT
            for e in node.elts:
                out = join(out, elem(T(e.value)) if isinstance(e, ast.Starred) else T(e))
            return List(out)
        if isinstance(node, ast.Tuple):
            if any(isinstance(e, ast.Starred) for e in node.elts):
                out = BOT
                for e in node.elts:
                    out = join(out, elem(T(e.value)) if isinstance(e, ast.Starred) else T(e))
                return List(out)
            return Tuple(*[T(e) for e in node.elts])
        if isinstance(node, ast.Set):
            out = BOT
            for e in node.elts:
                out = join(out, elem(T(e.value)) if isinstance(e, ast.Starred) else T(e))
            return Set(out)
        if isinstance(node, ast.Dict):
            kt, vt = BOT, BOT
            for k, v in zip(node.keys, node.values):
                if k is None:
                    dt = T(v)
                    if dt.k == "dict":
                        kt, vt = join(kt, dt.args[0]), join(vt, dt.args[1])
                    continue
                kt = join(kt, T(k))
                vt = join(vt, T(v))
            return Dict(kt, vt)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for g in node.generators:
                it = T(g.iter)
                self.bind(g.target, elem(it))
                for c in g.ifs:
                    T(c)
            if isinstance(node, ast.DictComp):
                return Dict(T(node.key), T(node.value))
            et = T(node.elt)
            if isinstance(node, ast.ListComp):
                return List(et)
            if isinstance(node, ast.SetComp):
                return Set(et)
            return Lazy("gen", et)
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    T(v.value)
            return STR
        if isinstance(node, ast.FormattedValue):
            T(node.value)
            return STR
        if isinstance(node, ast.Lambda):
            fi = self.funcs[id(node)]
            for d in node.args.defaults:
                T(d)
            self._bind_defaults(fi)
            prev, self.cur = self.cur, fi
            self._join_ret(fi, T(node.body))
            fi.has_value_return = True
            self.cur = prev
            return Func(fi.fid)
        if isinstance(node, ast.NamedExpr):
            t = T(node.value)
            self.bind(node.target, t)
            return t
        if isinstance(node, ast.Starred):
            return T(node.value)
        if isinstance(node, ast.Slice):
            for x in (node.lower, node.upper, node.step):
                if x is not None:
                    T(x)
            return INT
        return TOP

    # ------------------------------------------------------------------ operatory
    def binop(self, op, lt: Ty, rt: Ty, rnode=None) -> Ty:
        if lt.k == "bot" or rt.k == "bot":
            if isinstance(op, ast.Div):
                return FLOAT
            if isinstance(op, ast.Mod) and lt.k in ("str",):
                return STR
            other = rt if lt.k == "bot" else lt
            if other.k in ("int", "float", "bool", "inf") and not isinstance(op, ast.Mult):
                return BOT
            return BOT
        num_l, num_r = is_numeric(lt), is_numeric(rt)
        if isinstance(op, ast.Add):
            if num_l and num_r:
                return self._numjoin(lt, rt)
            if lt.k in ("str", "char") and rt.k in ("str", "char"):
                return STR
            if lt.k in ("list", "tuple") and rt.k in ("list", "tuple"):
                if lt.k == "tuple" and rt.k == "tuple":
                    return Tuple(*(lt.args + rt.args))
                return List(join(elem(lt), elem(rt)))
            return TOP
        if isinstance(op, ast.Mult):
            if num_l and num_r:
                return self._numjoin(lt, rt)
            if lt.k in ("str", "char") and rt.k in ("int", "bool"):
                return STR
            if rt.k in ("str", "char") and lt.k in ("int", "bool"):
                return STR
            if lt.k in ("list", "tuple") and rt.k in ("int", "bool"):
                return List(elem(lt))
            if rt.k in ("list", "tuple") and lt.k in ("int", "bool"):
                return List(elem(rt))
            return TOP
        if isinstance(op, ast.Div):
            return INF if lt.k == "inf" else FLOAT
        if isinstance(op, ast.Mod):
            if lt.k == "str":
                return STR
            if num_l and num_r:
                return self._numjoin(lt, rt)
            return TOP
        if isinstance(op, ast.Pow):
            if num_l and num_r:
                if isinstance(rnode, ast.UnaryOp) and isinstance(rnode.op, ast.USub):
                    return FLOAT
                if isinstance(rnode, ast.Constant) and isinstance(rnode.value, (int, float)) and rnode.value < 0:
                    return FLOAT
                return self._numjoin(lt, rt)
            return TOP
        if isinstance(op, (ast.Sub, ast.FloorDiv)):
            if num_l and num_r:
                return self._numjoin(lt, rt)
            if isinstance(op, ast.Sub) and lt.k == "set":
                return lt
            return TOP
        if isinstance(op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            if lt.k == "set" and rt.k == "set":
                return join(lt, rt)
            if lt.k == "dict" and isinstance(op, ast.BitOr):
                return join(lt, rt)
            if lt.k == "bool" and rt.k == "bool":
                return BOOL
            return INT
        if isinstance(op, (ast.LShift, ast.RShift)):
            return INT
        return TOP

    @staticmethod
    def _numjoin(a: Ty, b: Ty) -> Ty:
        if a.k == "inf" or b.k == "inf":
            other = b if a.k == "inf" else a
            return FLOAT if other.k == "float" else INF
        j = join(a, b)
        return INT if j.k == "bool" else j

    # ------------------------------------------------------------------ indeksowanie i atrybuty
    def subscript(self, node: ast.Subscript) -> Ty:
        bt = self.T(node.value)
        sl = node.slice
        if isinstance(sl, ast.Slice):
            self.T(sl)
            if bt.k in ("list", "str"):
                return bt
            if bt.k == "char":
                return STR
            if bt.k == "tuple":
                return List(elem(bt))
            return TOP if bt.k != "bot" else BOT
        it = self.T(sl)
        if bt.k in ("list", "deque"):
            return bt.args[0]
        if bt.k in ("str", "char"):
            return CHAR
        if bt.k == "dict":
            return bt.args[1]
        if bt.k == "tuple":
            idx = _const_int(sl)
            if idx is not None and -len(bt.args) <= idx < len(bt.args):
                return bt.args[idx]
            return elem(bt)
        if bt.k == "bot":
            return BOT
        del it
        return TOP

    def attribute(self, node: ast.Attribute) -> Ty:
        bt = self.T(node.value)
        if bt.k == "module":
            q = f"{bt.extra}.{node.attr}"
            if q in MODULE_ATTRS:
                return MODULE_ATTRS[q]
            return Builtin(q)
        if bt.k == "file":
            return FILE if node.attr == "buffer" else Builtin(f"sys.stdin.{node.attr}")
        if bt.k == "class" and bt.extra in self.classes:
            ci = self.classes[bt.extra]
            if node.attr in ci.fields:
                return ci.fields[node.attr]
            if node.attr in ci.methods:
                return Ty("method", (), ci.methods[node.attr].fid)
            return BOT
        if bt.k == "builtin":
            return Builtin(f"{bt.extra}.{node.attr}")
        if bt.k == "bot":
            return BOT
        return Ty("attr", (), node.attr)

    # ------------------------------------------------------------------ wywołania
    def call(self, node: ast.Call) -> Ty:
        T = self.T
        f = node.func
        an = self.an
        if isinstance(f, ast.Attribute):
            bt = T(f.value)
            if bt.k == "module" or bt.k == "builtin":
                q = f"{bt.extra}.{f.attr}"
                return self.builtin_call(q, node)
            if bt.k == "class" and bt.extra in self.classes:
                ci = self.classes[bt.extra]
                fi = ci.methods.get(f.attr)
                if fi is not None:
                    return self.user_call(fi, node)
                for a in node.args:
                    T(a)
                return TOP
            if bt.k == "bot":
                for a in node.args:
                    T(a)
                return BOT
            if bt.k in ("file", "stdout", "stderr"):
                for a in node.args:
                    T(a)
                if f.attr in ("readline", "read"):
                    return STR
                if f.attr == "readlines":
                    return List(STR)
                return NONE
            return self.method_call(f.value, bt, f.attr, node)
        ft = T(f)
        if ft.k == "func":
            return self.user_call(self.func_by_fid[ft.extra], node)
        if ft.k == "classref":
            return self.construct(ft.extra, node)
        if ft.k == "builtin":
            return self.builtin_call(ft.extra, node)
        for a in node.args:
            T(a)
        for k in node.keywords:
            T(k.value)
        del an
        return BOT if ft.k == "bot" else TOP

    def construct(self, cname: str, node: ast.Call) -> Ty:
        ci = self.classes.get(cname)
        if ci is None:
            return TOP
        init = ci.methods.get("__init__")
        if init is not None:
            self.user_call(init, node)
        elif ci.dataclass:
            for name, a in zip(ci.field_order, node.args):
                t = self.T(a)
                old = ci.fields.get(name, BOT)
                if join(old, t) != old:
                    ci.fields[name] = join(old, t)
                    self.changed = True
            for k in node.keywords:
                t = self.T(k.value)
                old = ci.fields.get(k.arg, BOT)
                if join(old, t) != old:
                    ci.fields[k.arg] = join(old, t)
                    self.changed = True
        else:
            for a in node.args:
                self.T(a)
        return Cls(cname)

    def user_call(self, fi: FuncInfo, node: ast.Call) -> Ty:
        fi.calls += 1
        for i, a in enumerate(node.args):
            if isinstance(a, ast.Starred):
                self.T(a)
                continue
            t = self.T(a)
            if i < len(fi.params):
                self.join_sym(fi.params[i], t)
                if not _is_lvalue(a):
                    fi.rvalue_args.add(i)
        names = {s.name: (k, s) for k, s in enumerate(fi.params)}
        for kw in node.keywords:
            t = self.T(kw.value)
            if kw.arg in names:
                k, s = names[kw.arg]
                self.join_sym(s, t)
                if not _is_lvalue(kw.value):
                    fi.rvalue_args.add(k)
        if self.cur is fi or (self.cur is not None and self.cur.fid == fi.fid):
            fi.recursive = True
        r = fi.ret
        if r.k == "bot" and not fi.has_value_return and fi.calls > 2:
            return NONE
        return r

    def call_callable(self, ftype: Ty, fnode, arg_types: list[Ty]) -> Ty:
        """Wywołanie przekazanego obiektu wywoływalnego (map(f, …), key=f, reduce(f, …))."""
        if ftype.k == "func":
            fi = self.func_by_fid[ftype.extra]
            for s, t in zip(fi.params, arg_types):
                self.join_sym(s, t)
            if fi.is_lambda:
                self.T(fi.node)
            return fi.ret
        if ftype.k == "builtin":
            n = short(ftype.extra)
            a0 = arg_types[0] if arg_types else BOT
            return {"int": INT, "float": FLOAT, "str": STR, "bool": BOOL, "len": INT, "abs": a0, "ord": INT,
                    "chr": CHAR, "list": List(elem(a0)), "sorted": List(elem(a0)), "tuple": List(elem(a0)),
                    "set": Set(elem(a0)), "sum": elem(a0), "max": elem(a0), "min": elem(a0),
                    "reversed": Lazy("reversed", elem(a0)), "lower": STR, "upper": STR}.get(n, TOP)
        if ftype.k == "method" or ftype.k == "attr":
            return STR if ftype.extra in ("strip", "upper", "lower", "rstrip", "lstrip") else TOP
        if ftype.k == "classref":
            return Cls(ftype.extra)
        return TOP

    def bind_key(self, key_node, et: Ty) -> Ty:
        """key=lambda x: … / key=f — typ parametru = typ elementu; zwraca typ klucza."""
        if key_node is None:
            return et
        kt = self.T(key_node)
        if kt.k == CMPKEY:
            fi = self.func_by_fid.get(kt.extra)
            if fi:
                for s in fi.params[:2]:
                    self.join_sym(s, et)
            return INT
        return self.call_callable(kt, key_node, [et])

    def builtin_call(self, q: str, node: ast.Call) -> Ty:
        T = self.T
        args = node.args
        kws = {k.arg: k.value for k in node.keywords if k.arg}
        ats = [T(a) for a in args]
        for k in node.keywords:
            if k.arg not in ("key",):
                T(k.value)
        n = q
        a0 = ats[0] if ats else BOT
        if n in ("int",):
            return INT
        if n == "float":
            if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) and \
                    args[0].value.strip().lower().lstrip("+-") in ("inf", "infinity"):
                return INF
            return FLOAT
        if n == "str":
            return STR
        if n == "bool":
            return BOOL
        if n == "len":
            return INT
        if n == "abs":
            return INT if a0.k == "bool" else a0
        if n in ("min", "max"):
            if "key" in kws:
                et = elem(a0) if len(args) == 1 else _join_all(ats)
                self.bind_key(kws["key"], et)
            if len(args) == 1:
                r = elem(a0)
                if "default" in kws:
                    r = join(r, T(kws["default"]))
                return INT if r.k == "bool" else r
            r = _join_all(ats)
            return INT if r.k == "bool" else r
        if n == "sum":
            e = elem(a0)
            if e.k == "bool":
                e = INT
            if len(ats) > 1:
                e = join(e, ats[1])
            if "start" in kws:
                e = join(e, T(kws["start"]))
            return e if e.k != "bot" else INT
        if n == "sorted":
            et = elem(a0)
            if "key" in kws:
                self.bind_key(kws["key"], et)
            return List(et)
        if n == "reversed":
            return Lazy("reversed", elem(a0))
        if n == "list":
            return List(elem(a0)) if args else List(BOT)
        if n == "tuple":
            return Ty("list", (elem(a0) if args else BOT,), "tuple")
        if n in ("set", "frozenset"):
            return Set(elem(a0)) if args else Set(BOT)
        if n == "dict":
            if args:
                if a0.k == "dict":
                    return a0
                e = elem(a0)
                if e.k == "tuple" and len(e.args) == 2:
                    return Dict(e.args[0], e.args[1])
                if e.k == "list":
                    return Dict(e.args[0], e.args[0])
            out = Dict(BOT, BOT)
            for k in node.keywords:
                out = join(out, Dict(STR, T(k.value)))
            return out
        if n == "range":
            return Lazy("range", INT)
        if n == "enumerate":
            return Lazy("enumerate", Tuple(INT, elem(a0)))
        if n == "zip":
            if len(args) == 1 and isinstance(args[0], ast.Starred):
                return Lazy("zip", List(elem(elem(ats[0]))))      # zip(*macierz) = transpozycja
            return Lazy("zip", Tuple(*[elem(t) for t in ats]))
        if n == "map":
            if not args:
                return TOP
            ft = ats[0]
            r = self.call_callable(ft, args[0], [elem(t) for t in ats[1:]])
            return Lazy("map", r)
        if n == "filter":
            if len(args) == 2:
                if ats[0].k == "func":
                    self.call_callable(ats[0], args[0], [elem(ats[1])])
                return Lazy("filter", elem(ats[1]))
            return TOP
        if n == "iter":
            if args and is_stdin_read_split(self.an, args[0]):
                return TOKENS
            if a0.k == "file":
                return Lazy("iter", STR)
            return Lazy("iter", elem(a0))
        if n == "next":
            if a0.k == "tokens":
                return STR
            r = elem(a0)
            if len(ats) > 1:
                r = join(r, ats[1])
            return r
        if n == "input":
            return STR
        if n in ("print", "exit", "quit", "sys.exit", "sys.setrecursionlimit", "threading.stack_size",
                 "setrecursionlimit"):
            return NONE
        if n == "ord":
            return INT
        if n == "chr":
            return CHAR
        if n == "divmod":
            t = self._numjoin(ats[0], ats[1]) if len(ats) == 2 else INT
            return Tuple(t, t)
        if n == "pow":
            if len(ats) == 3:
                return INT
            if len(ats) == 2:
                if any(t.k == "float" for t in ats):
                    return FLOAT
                e = args[1]
                if (isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub)) or \
                        (isinstance(e, ast.Constant) and isinstance(e.value, (int, float)) and e.value < 0):
                    return FLOAT
                return INT
            return TOP
        if n == "round":
            if len(args) >= 2 and not (isinstance(args[1], ast.Constant) and args[1].value is None):
                return FLOAT if a0.k == "float" else a0
            return INT
        if n in ("any", "all", "isinstance", "callable", "hasattr", "math.isclose", "math.isinf", "math.isnan",
                 "math.isfinite"):
            return BOOL
        if n in ("hex", "bin", "oct", "repr", "ascii", "format"):
            return STR
        if n in ("id", "hash"):
            return INT
        if n == "open":
            return FILE
        if n == "type":
            return TOP
        if n.startswith("math."):
            m = n[5:]
            if m in MATH_INT:
                if m in ("floor", "ceil", "trunc") and a0.k == "inf":
                    return INF
                return INT
            if m in MATH_FLOAT:
                return FLOAT
            if m in MATH_BOOL:
                return BOOL
            if m == "prod":
                return elem(a0) if elem(a0).k != "bot" else INT
            if m == "fsum":
                return FLOAT
            return TOP
        if n.startswith("heapq."):
            m = n[6:]
            return self._heapq(m, node, ats)
        if n in ("heappush", "heappop", "heapify", "heappushpop", "heapreplace", "nlargest", "nsmallest"):
            return self._heapq(n, node, ats)
        if n.startswith("bisect.") or n in ("bisect_left", "bisect_right", "bisect", "insort", "insort_left",
                                             "insort_right"):
            m = short(n)
            if m in BISECT_INSORT:
                if len(args) >= 2:
                    self.refine(args[0], List(ats[1]))
                return NONE
            if "key" in kws and args:
                self.bind_key(kws["key"], elem(a0))
            return INT
        if n in ("collections.deque", "deque"):
            return Deque(elem(a0)) if args else Deque(BOT)
        if n in ("collections.defaultdict", "defaultdict"):
            vt = self._factory_type(args[0]) if args else BOT
            if len(args) >= 2 and ats[1].k == "dict":
                return Dict(ats[1].args[0], join(vt, ats[1].args[1]), "default")
            return Dict(BOT, vt, "default")
        if n in ("collections.Counter", "Counter"):
            if args:
                if a0.k == "dict":
                    return Dict(a0.args[0], INT, "counter")
                return Dict(elem(a0), INT, "counter")
            return Dict(BOT, INT, "counter")
        if n in ("collections.OrderedDict", "OrderedDict"):
            if args:
                e = elem(a0)
                if e.k == "tuple" and len(e.args) == 2:
                    return Dict(e.args[0], e.args[1])
            return Dict(BOT, BOT)
        if n.startswith("itertools.") or n in ("permutations", "combinations", "product", "accumulate", "chain",
                                                "combinations_with_replacement", "count", "groupby", "zip_longest"):
            m = short(n)
            if m in ("permutations", "combinations", "combinations_with_replacement"):
                return Lazy(m[:4], List(elem(a0)))
            if m == "product":
                e = _join_all([elem(t) for t in ats]) if ats else BOT
                return Lazy("product", List(e))
            if m == "accumulate":
                e = elem(a0)
                if len(args) >= 2:
                    self.call_callable(ats[1], args[1], [e, e])
                if "initial" in kws:
                    e = join(e, T(kws["initial"]))
                return Lazy("accumulate", INT if e.k == "bool" else e)
            if m == "chain":
                return Lazy("chain", _join_all([elem(t) for t in ats]))
            if m == "count":
                return Lazy("count", a0 if args else INT)
            return TOP
        if n in ("functools.reduce", "reduce"):
            if len(args) >= 2:
                e = elem(ats[1])
                init = ats[2] if len(ats) > 2 else e
                r = self.call_callable(ats[0], args[0], [join(init, e), e])
                return join(init, r)
            return TOP
        if n in ("functools.cmp_to_key", "cmp_to_key"):
            if ats and ats[0].k == "func":
                return Ty(CMPKEY, (), ats[0].extra)
            return TOP
        if n in ("sys.stdin.readline", "sys.stdin.read", "sys.stdin.buffer.readline", "sys.stdin.buffer.read"):
            return STR
        if n in ("sys.stdin.readlines", "sys.stdin.buffer.readlines"):
            return List(STR)
        if n in ("sys.stdout.write", "sys.stderr.write", "sys.stdout.flush"):
            return NONE
        if n in ("copy.copy", "copy.deepcopy"):
            return a0
        if n in ("threading.Thread",):
            return Ty("thread")
        if n in ("random.randint", "random.randrange"):
            return INT
        if n == "random.random":
            return FLOAT
        if n in ("random.choice",):
            return elem(a0)
        if n == "random.shuffle":
            return NONE
        if n in ("string.ascii_lowercase",):
            return STR
        return TOP

    def _factory_type(self, f) -> Ty:
        if isinstance(f, ast.Name):
            q = qual_of_call(self.an, f) or f.id
            return {"int": INT, "float": FLOAT, "str": STR, "bool": BOOL, "list": List(BOT), "set": Set(BOT),
                    "dict": Dict(BOT, BOT), "collections.deque": Deque(BOT), "deque": Deque(BOT),
                    "collections.Counter": Dict(BOT, INT, "counter"), "Counter": Dict(BOT, INT, "counter"),
                    "tuple": List(BOT)}.get(q, BOT)
        if isinstance(f, ast.Lambda):
            fi = self.funcs[id(f)]
            prev, self.cur = self.cur, fi
            t = self.T(f.body)
            self.cur = prev
            self._join_ret(fi, t)
            return t
        if isinstance(f, ast.Attribute):
            q = qual_of_call(self.an, f)
            if q == "collections.deque":
                return Deque(BOT)
            if q == "collections.Counter":
                return Dict(BOT, INT, "counter")
            if q == "collections.defaultdict":
                return Dict(BOT, BOT, "default")
        if isinstance(f, ast.Call):
            # defaultdict(lambda: defaultdict(int)) itp.
            return self.T(f)
        return BOT

    def _heapq(self, m: str, node: ast.Call, ats: list[Ty]) -> Ty:
        args = node.args
        self.heap_any = True
        if m in ("heappush", "heappushpop", "heapreplace") and len(args) >= 2:
            self.refine(args[0], List(ats[1]))
            self._mark_heap(args[0])
            return elem(self.T(args[0])) if m != "heappush" else NONE
        if m == "heappop" and args:
            self._mark_heap(args[0])
            return elem(ats[0])
        if m == "heapify" and args:
            self._mark_heap(args[0])
            return NONE
        if m in ("nlargest", "nsmallest") and len(args) >= 2:
            kws = {k.arg: k.value for k in node.keywords}
            if "key" in kws:
                self.bind_key(kws["key"], elem(ats[1]))
            return List(elem(ats[1]))
        return TOP

    def _mark_heap(self, node) -> None:
        if isinstance(node, ast.Name):
            s = self.sym(node)
            if s is not None:
                self.heap_syms.add(s)

    def method_call(self, recv, rt: Ty, m: str, node: ast.Call) -> Ty:
        T = self.T
        args = node.args
        ats = [T(a) for a in args]
        kws = {k.arg: k.value for k in node.keywords if k.arg}
        for k in node.keywords:
            if k.arg != "key":
                T(k.value)
        a0 = ats[0] if ats else BOT
        k = rt.k
        if k in ("str", "char"):
            if m in ("split", "rsplit", "splitlines"):
                return List(STR)
            if m in ("upper", "lower", "swapcase", "casefold") and k == "char":
                return CHAR
            if m in ("strip", "lstrip", "rstrip", "upper", "lower", "replace", "title", "capitalize", "swapcase",
                     "zfill", "center", "ljust", "rjust", "format", "join", "translate", "casefold", "removeprefix",
                     "removesuffix", "expandtabs", "encode", "decode", "format_map"):
                if m == "join" and args:
                    T(args[0])
                return STR
            if m in ("isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower", "isnumeric", "isdecimal",
                     "startswith", "endswith", "isidentifier", "istitle", "isascii", "isprintable"):
                return BOOL
            if m in ("find", "rfind", "index", "rindex", "count"):
                return INT
            if m == "partition" or m == "rpartition":
                return Tuple(STR, STR, STR)
            return TOP
        if k in ("list", "bot") and m in ("append", "extend", "insert", "pop", "remove", "sort", "reverse", "clear",
                                          "index", "count", "copy"):
            if m == "append" and args:
                self.refine(recv, List(a0))
                return NONE
            if m == "extend" and args:
                self.refine(recv, List(elem(a0)))
                return NONE
            if m == "insert" and len(args) == 2:
                self.refine(recv, List(ats[1]))
                return NONE
            if k == "bot":
                return BOT if m == "pop" else (INT if m in ("index", "count") else NONE)
            if m == "pop":
                return rt.args[0]
            if m in ("index", "count"):
                return INT
            if m == "copy":
                return rt
            if m == "sort":
                if "key" in kws:
                    self.bind_key(kws["key"], rt.args[0])
            return NONE
        if k == "dict":
            K, V = rt.args
            if m == "get":
                if args:
                    self.refine_dict_key(recv, a0, rt)
                r = V
                if len(ats) > 1:
                    r = join(r, ats[1])
                    if V.k == "bot":
                        self.refine(recv, Dict(a0, ats[1], rt.extra))
                return r if r.k != "bot" or len(ats) > 1 else Ty("opt", (V,)) if V.k != "bot" else BOT
            if m == "keys":
                return Lazy("keys", K)
            if m == "values":
                return Lazy("values", V)
            if m == "items":
                return Lazy("items", Tuple(K, V))
            if m == "pop":
                return join(V, ats[1]) if len(ats) > 1 else V
            if m == "setdefault":
                if len(args) == 2:
                    self.refine(recv, Dict(a0, ats[1], rt.extra))
                return V
            if m == "update":
                if args:
                    if a0.k == "dict":
                        self.refine(recv, Dict(a0.args[0], a0.args[1] if rt.extra != "counter" else INT, rt.extra))
                    elif rt.extra == "counter":
                        self.refine(recv, Dict(elem(a0), INT, "counter"))
                return NONE
            if m == "copy":
                return rt
            if m == "most_common":
                return List(Tuple(K, INT))
            if m == "popitem":
                return Tuple(K, V)
            if m in ("elements",):
                return Lazy("elements", K)
            if m == "total":
                return INT
            return NONE
        if k == "set":
            if m == "add" and args:
                self.refine(recv, Set(a0))
                return NONE
            if m == "update" and args:
                for t in ats:
                    self.refine(recv, Set(elem(t)))
                return NONE
            if m in ("union", "intersection", "difference", "symmetric_difference", "copy"):
                return rt
            if m in ("issubset", "issuperset", "isdisjoint"):
                return BOOL
            if m == "pop":
                return rt.args[0]
            return NONE
        if k == "deque":
            if m in ("append", "appendleft") and args:
                self.refine(recv, Deque(a0))
                return NONE
            if m in ("extend", "extendleft") and args:
                self.refine(recv, Deque(elem(a0)))
                return NONE
            if m in ("pop", "popleft"):
                return rt.args[0]
            if m in ("index", "count"):
                return INT
            if m == "copy":
                return rt
            return NONE
        if k == "thread":
            return NONE
        return BOT if k == "bot" else TOP

    def refine_dict_key(self, recv, kt: Ty, rt: Ty) -> None:
        if rt.args[0].k == "bot":
            self.refine(recv, Dict(kt, rt.args[1], rt.extra))

    # ------------------------------------------------------------------ mutacje (dla referencji/const)
    def _mutation_pass(self) -> None:
        an = self.an
        for node in ast.walk(an.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = _base_name(node.func.value)
                if base is not None and node.func.attr in MUTATING:
                    s = self.sym(base)
                    if s is not None:
                        self.mutated.add(s)
                q = qual_of_call(an, node.func)
                if short(q) in HEAPQ_FUNCS | BISECT_INSORT | {"shuffle"} and node.args:
                    b = _base_name(node.args[0])
                    if b is not None and self.sym(b):
                        self.mutated.add(self.sym(b))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                q = qual_of_call(an, node.func)
                if short(q) in HEAPQ_FUNCS | BISECT_INSORT and node.args:
                    b = _base_name(node.args[0])
                    if b is not None and self.sym(b):
                        self.mutated.add(self.sym(b))
            elif isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(getattr(node, "ctx", None),
                                                                                  (ast.Store, ast.Del)):
                b = _base_name(node.value)
                if b is not None and self.sym(b):
                    self.mutated.add(self.sym(b))
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, (ast.Subscript, ast.Attribute)):
                b = _base_name(node.target.value)
                if b is not None and self.sym(b):
                    self.mutated.add(self.sym(b))
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                s = self.sym(node.target)
                t = self.ty.get(s, BOT) if s else BOT
                if s is not None and t.k in ("list", "set", "dict", "deque"):
                    self.mutated.add(s)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                b = _base_name(node.value)
                if b is not None and self.sym(b) is not None:
                    bt = self.node_ty.get(id(node.value), BOT)
                    if bt.k == "dict":
                        self.mutated.add(self.sym(b))
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                s = self.sym(node)
                if s is not None:
                    self.rebound.add(s)
        # przekazanie do funkcji, która mutuje parametr
        for _ in range(5):
            grew = False
            for node in ast.walk(an.tree):
                if isinstance(node, ast.Call):
                    ft = self.node_ty.get(id(node.func), BOT)
                    fi = self.func_by_fid.get(ft.extra) if ft.k == "func" else None
                    if fi is None and ft.k == "method":
                        fi = self.func_by_fid.get(ft.extra)
                    if fi is None:
                        continue
                    for i, a in enumerate(node.args):
                        if i < len(fi.params) and fi.params[i] in self.mutated:
                            b = _base_name(a)
                            if b is not None and self.sym(b) is not None and self.sym(b) not in self.mutated:
                                self.mutated.add(self.sym(b))
                                grew = True
            if not grew:
                break


def _join_all(ts) -> Ty:
    out = BOT
    for t in ts:
        out = join(out, t)
    return out


def _const_int(node) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _const_int(node.operand)
        return -v if v is not None else None
    return None


def _is_lvalue(node) -> bool:
    return isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)) and not (
        isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice))


def _base_name(node):
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node if isinstance(node, ast.Name) else None

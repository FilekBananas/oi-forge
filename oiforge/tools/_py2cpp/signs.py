"""Analiza znaku: które wyrażenia całkowite są na pewno nieujemne.

W Pythonie `//` i `%` zaokrąglają w dół (−7 // 2 == −4, −7 % 3 == 2), w C++ — w stronę zera.
Dla wartości nieujemnych oba języki dają to samo, więc helpery `floordiv`/`pymod` wstawiamy tylko tam,
gdzie znak może być ujemny. Analiza jest optymistyczna (największy punkt stały): zaczynamy od „wszystko
nieujemne” i obalamy na podstawie przypisań, aż nic się nie zmienia.
"""
from __future__ import annotations

import ast

from .analysis import Analysis, Symbol
from .infer import FuncInfo, Infer, qual_of_call, short

NONNEG_FUNCS = {"len", "abs", "ord", "math.gcd", "math.lcm", "math.factorial", "math.comb", "math.perm", "math.isqrt",
                "bisect.bisect_left", "bisect.bisect_right", "bisect.bisect", "bisect_left", "bisect_right", "bisect",
                "hash", "id", "math.sqrt", "math.fabs", "math.exp"}


class Signs:
    def __init__(self, an: Analysis, inf: Infer):
        self.an = an
        self.inf = inf
        self.nn: dict[Symbol, bool] = {}
        self.nn_elem: dict[Symbol, bool] = {}
        self.ret_nn: dict[str, bool] = {}
        self.ret_elem: dict[str, bool] = {}
        self.changed = False
        self.cur: FuncInfo | None = None

    # ------------------------------------------------------------------ punkt stały
    def run(self) -> None:
        for fi in self.inf.funcs.values():
            ok = fi.calls > 0 and not fi.is_lambda
            for p in fi.params:
                self.nn[p] = ok
                self.nn_elem[p] = ok
            if fi.vararg is not None:
                s = self.an.sym_of.get(id(fi.vararg))
                if s:
                    self.nn[s] = self.nn_elem[s] = False
        for _ in range(30):
            self.changed = False
            self.cur = None
            self._block(self.an.tree.body)
            self._calls()
            if not self.changed:
                break

    def _kill(self, table: dict, sym: Symbol | None, ok: bool) -> None:
        if sym is None or ok:
            return
        if table.get(sym, True):
            table[sym] = False
            self.changed = True

    def _sym(self, node):
        return self.an.sym_of.get(id(node))

    def _calls(self) -> None:
        for node in ast.walk(self.an.tree):
            if not isinstance(node, ast.Call):
                continue
            ft = self.inf.node_ty.get(id(node.func))
            fi = None
            if ft is not None and ft.k in ("func", "method"):
                fi = self.inf.func_by_fid.get(ft.extra)
            elif ft is not None and ft.k == "classref":
                ci = self.inf.classes.get(ft.extra)
                fi = ci.methods.get("__init__") if ci else None
            if fi is None:
                continue
            for i, a in enumerate(node.args):
                if i < len(fi.params) and not isinstance(a, ast.Starred):
                    self._kill(self.nn, fi.params[i], self.nonneg(a))
                    self._kill(self.nn_elem, fi.params[i], self.elem_nonneg(a))
            names = {s.name: s for s in fi.params}
            for kw in node.keywords:
                if kw.arg in names:
                    self._kill(self.nn, names[kw.arg], self.nonneg(kw.value))
                    self._kill(self.nn_elem, names[kw.arg], self.elem_nonneg(kw.value))

    # ------------------------------------------------------------------ instrukcje
    def _block(self, stmts) -> None:
        for s in stmts:
            self._stmt(s)

    def _stmt(self, s) -> None:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fi = self.inf.funcs.get(id(s))
            prev, self.cur = self.cur, fi
            args = s.args
            pos = args.posonlyargs + args.args
            for a, d in zip(pos[len(pos) - len(args.defaults):], args.defaults):
                sym = self._sym(a)
                self._kill(self.nn, sym, self.nonneg(d))
            self._block(s.body)
            self.cur = prev
            return
        if isinstance(s, ast.ClassDef):
            for st in s.body:
                self._stmt(st)
            return
        if isinstance(s, ast.Assign):
            for t in s.targets:
                self._assign(t, s.value)
            self._scan_expr(s.value)
            return
        if isinstance(s, ast.AnnAssign):
            if s.value is not None:
                self._assign(s.target, s.value)
                self._scan_expr(s.value)
            return
        if isinstance(s, ast.AugAssign):
            self._scan_expr(s.value)
            if isinstance(s.target, ast.Name):
                sym = self._sym(s.target)
                ok = self._binop_nn(s.op, self.nn.get(sym, True), s.value, s.target)
                self._kill(self.nn, sym, ok)
                if isinstance(s.op, ast.Add):
                    self._kill(self.nn_elem, sym, self.elem_nonneg(s.value))
            elif isinstance(s.target, ast.Subscript):
                base = _base_name(s.target)
                sym = self._sym(base) if base is not None else None
                ok = self._binop_nn(s.op, self.nn_elem.get(sym, True) if sym else False, s.value, s.target)
                self._kill(self.nn_elem, sym, ok)
            return
        if isinstance(s, ast.For):
            self._scan_expr(s.iter)
            self._bind_iter(s.target, s.iter)
            self._block(s.body)
            self._block(s.orelse)
            return
        if isinstance(s, (ast.While, ast.If)):
            self._scan_expr(s.test)
            self._block(s.body)
            self._block(s.orelse)
            return
        if isinstance(s, ast.Return):
            if self.cur is not None and s.value is not None:
                if not self.nonneg(s.value) and self.ret_nn.get(self.cur.fid, True):
                    self.ret_nn[self.cur.fid] = False
                    self.changed = True
                if not self.elem_nonneg(s.value) and self.ret_elem.get(self.cur.fid, True):
                    self.ret_elem[self.cur.fid] = False
                    self.changed = True
                self._scan_expr(s.value)
            return
        if isinstance(s, ast.Expr):
            self._scan_expr(s.value)
            return
        if isinstance(s, ast.Try) or (hasattr(ast, "TryStar") and isinstance(s, ast.TryStar)):
            self._block(s.body)
            for h in s.handlers:
                self._block(h.body)
            self._block(s.orelse)
            self._block(s.finalbody)
            return
        if isinstance(s, ast.With):
            for it in s.items:
                if it.optional_vars is not None:
                    self._assign(it.optional_vars, None)
            self._block(s.body)
            return
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            for c in s.cases:
                self._block(c.body)

    def _assign(self, target, value) -> None:
        if isinstance(target, ast.Name):
            sym = self._sym(target)
            self._kill(self.nn, sym, value is not None and self.nonneg(value))
            self._kill(self.nn_elem, sym, value is not None and self.elem_nonneg(value))
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts) and \
                    not any(isinstance(e, ast.Starred) for e in target.elts + value.elts):
                for t, v in zip(target.elts, value.elts):
                    self._assign(t, v)
                return
            ok = value is not None and self.elem_nonneg(value)
            for t in target.elts:
                tt = t.value if isinstance(t, ast.Starred) else t
                self._set_all(tt, ok)
            return
        if isinstance(target, ast.Subscript):
            base = _base_name(target)
            sym = self._sym(base) if base is not None else None
            self._kill(self.nn_elem, sym, value is not None and self.nonneg(value) and self.elem_nonneg_or_scalar(value))
            return
        if isinstance(target, ast.Starred):
            self._set_all(target.value, value is not None and self.elem_nonneg(value))

    def _set_all(self, target, ok: bool) -> None:
        if isinstance(target, ast.Name):
            sym = self._sym(target)
            self._kill(self.nn, sym, ok)
            self._kill(self.nn_elem, sym, ok)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                self._set_all(t.value if isinstance(t, ast.Starred) else t, ok)
        elif isinstance(target, ast.Subscript):
            base = _base_name(target)
            if base is not None:
                self._kill(self.nn_elem, self._sym(base), ok)

    def _bind_iter(self, target, it) -> None:
        """Pętla / generator: cel dostaje nieujemność elementów iterowanego obiektu."""
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name):
            q = qual_of_call(self.an, it.func)
            if q == "range":
                self._set_all(target, self.range_nonneg(it))
                return
            if q == "enumerate" and it.args and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2:
                start_ok = True
                if len(it.args) > 1:
                    start_ok = self.nonneg(it.args[1])
                for kw in it.keywords:
                    if kw.arg == "start":
                        start_ok = self.nonneg(kw.value)
                self._set_all(target.elts[0], start_ok)
                inner = it.args[0]
                self._bind_iter(target.elts[1], inner)
                return
            if q == "zip" and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == len(it.args):
                for t, a in zip(target.elts, it.args):
                    self._bind_iter(t, a)
                return
        self._set_all(target, self.elem_nonneg(it))

    def range_nonneg(self, call: ast.Call) -> bool:
        a = call.args
        if len(a) == 1:
            return True
        if len(a) == 2:
            return self.nonneg(a[0])
        if len(a) == 3:
            st = _const(a[2])
            if st is not None and st > 0:
                return self.nonneg(a[0])
            if st is not None and st < 0:
                stop = _const(a[1])
                if stop is not None:
                    return stop >= -1
                return self.nonneg(a[1])
        return False

    def _scan_expr(self, node) -> None:
        """Efekty w wyrażeniach: append/add/heappush… i zmienne comprehension."""
        if node is None:
            return
        for sub in ast.walk(node):
            if isinstance(sub, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for g in sub.generators:
                    self._bind_iter(g.target, g.iter)
            elif isinstance(sub, ast.NamedExpr):
                self._assign(sub.target, sub.value)
            elif isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Attribute) and f.attr in ("append", "add", "appendleft", "insert") and sub.args:
                    base = _base_name(f.value)
                    v = sub.args[-1]
                    if base is not None:
                        self._kill(self.nn_elem, self._sym(base), self.nonneg(v) and self.elem_nonneg_or_scalar(v))
                elif isinstance(f, ast.Attribute) and f.attr in ("extend", "extendleft", "update") and sub.args:
                    base = _base_name(f.value)
                    if base is not None:
                        self._kill(self.nn_elem, self._sym(base), self.elem_nonneg(sub.args[0]))
                else:
                    q = qual_of_call(self.an, f)
                    if short(q) in ("heappush", "insort", "insort_left", "insort_right", "heappushpop",
                                    "heapreplace") and len(sub.args) >= 2:
                        base = _base_name(sub.args[0])
                        if base is not None:
                            v = sub.args[1]
                            self._kill(self.nn_elem, self._sym(base),
                                       self.nonneg(v) and self.elem_nonneg_or_scalar(v))
            elif isinstance(sub, ast.Lambda):
                for a in sub.args.args:
                    s = self._sym(a)
                    self._kill(self.nn, s, False)
                    self._kill(self.nn_elem, s, False)

    # ------------------------------------------------------------------ wyrażenia
    def elem_nonneg_or_scalar(self, node) -> bool:
        t = self.inf.node_ty.get(id(node))
        if t is not None and t.k in ("tuple", "list", "set", "deque", "dict"):
            return self.elem_nonneg(node)
        return True

    def nonneg(self, node) -> bool:
        if node is None:
            return False
        c = _const(node)
        if c is not None:
            return c >= 0
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (bool,)):
                return True
            if isinstance(node.value, (int, float)):
                return node.value >= 0
            return True          # str/None — nie dotyczy liczb
        if isinstance(node, ast.Name):
            sym = self._sym(node)
            if sym is None or sym.kind == "builtin":
                return node.id in ("True", "False")
            t = self.inf.ty.get(sym)
            if t is not None and t.k in ("tuple", "list", "set", "dict", "deque"):
                return self.nn_elem.get(sym, True)
            return self.nn.get(sym, True)
        if isinstance(node, ast.BinOp):
            return self._binop_nn(node.op, self.nonneg(node.left), node.right, node.left)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return True
            if isinstance(node.op, ast.UAdd):
                return self.nonneg(node.operand)
            return False
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            if isinstance(node, ast.BoolOp):
                return all(self.nonneg(v) for v in node.values)
            return True
        if isinstance(node, ast.IfExp):
            return self.nonneg(node.body) and self.nonneg(node.orelse)
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Slice):
                return self.elem_nonneg(node.value)
            bt = self.inf.node_ty.get(id(node.value))
            if bt is not None and bt.k == "tuple":
                if isinstance(node.value, (ast.Tuple,)):
                    i = _const(node.slice)
                    if i is not None and -len(node.value.elts) <= i < len(node.value.elts):
                        return self.nonneg(node.value.elts[i])
            return self.elem_nonneg(node.value)
        if isinstance(node, ast.Call):
            return self._call_nn(node)
        if isinstance(node, ast.Tuple):
            return all(self.nonneg(e) for e in node.elts)
        if isinstance(node, ast.Attribute):
            return False
        if isinstance(node, ast.NamedExpr):
            return self.nonneg(node.value)
        return False

    def _binop_nn(self, op, left_ok: bool, right, left_node=None) -> bool:
        r_ok = self.nonneg(right)
        if isinstance(op, (ast.Add, ast.Mult, ast.FloorDiv, ast.Div)):
            return left_ok and r_ok
        if isinstance(op, ast.Mod):
            # wynik % w Pythonie ma znak dzielnika
            return r_ok and _const(right) != 0
        if isinstance(op, ast.Pow):
            e = _const(right)
            if e is not None and e % 2 == 0:
                return True
            return left_ok
        if isinstance(op, ast.Sub):
            if left_node is not None:
                a, b = _const(left_node), _const(right)
                if a is not None and b is not None:
                    return a - b >= 0
            return False
        if isinstance(op, ast.BitAnd):
            return left_ok or r_ok
        if isinstance(op, (ast.BitOr, ast.BitXor)):
            return left_ok and r_ok
        if isinstance(op, (ast.RShift, ast.LShift)):
            return left_ok
        return False

    def _call_nn(self, node: ast.Call) -> bool:
        f = node.func
        q = qual_of_call(self.an, f)
        args = node.args
        if q in NONNEG_FUNCS:
            return True
        if q in ("int", "round", "math.floor", "math.ceil", "math.trunc", "float"):
            return bool(args) and self.nonneg(args[0]) and not (
                isinstance(args[0], ast.Call) and qual_of_call(self.an, args[0].func) in ("input",))
        if q == "max":
            if len(args) == 1:
                return self.elem_nonneg(args[0])
            return any(self.nonneg(a) for a in args)
        if q == "min":
            if len(args) == 1:
                return self.elem_nonneg(args[0])
            return all(self.nonneg(a) for a in args)
        if q == "sum":
            return bool(args) and self.elem_nonneg(args[0]) and (len(args) < 2 or self.nonneg(args[1]))
        if q == "pow":
            if len(args) == 3:
                return self.nonneg(args[2])
            if len(args) == 2:
                return self._binop_nn(ast.Pow(), self.nonneg(args[0]), args[1])
        if q in ("divmod",):
            return False
        if q in ("next",) and args:
            return self.elem_nonneg(args[0])
        if q and short(q) in ("heappop", "heappushpop", "heapreplace") and args:
            return self.elem_nonneg(args[0])
        if isinstance(f, ast.Attribute):
            m = f.attr
            if m in ("count", "index", "find", "rfind", "bit_length", "bit_count"):
                return m not in ("find", "rfind")
            if m in ("pop", "popleft", "get", "setdefault") and isinstance(f.value, (ast.Name, ast.Subscript,
                                                                                     ast.Attribute)):
                ok = self.elem_nonneg(f.value)
                if m in ("get", "pop") and len(args) > 1:
                    ok = ok and self.nonneg(args[1])
                return ok
        ft = self.inf.node_ty.get(id(f))
        if ft is not None and ft.k in ("func", "method"):
            return self.ret_nn.get(ft.extra, True) and self.inf.func_by_fid[ft.extra].calls > 0
        return False

    def elem_nonneg(self, node) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name):
            sym = self._sym(node)
            if sym is None:
                return False
            return self.nn_elem.get(sym, True)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self.nonneg(e.value if isinstance(e, ast.Starred) else e) and
                       self.elem_nonneg_or_scalar(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(self.nonneg(v) for v in node.values) and all(k is None or self.nonneg(k) for k in node.keys)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Mult):
                lt = self.inf.node_ty.get(id(node.left))
                if lt is not None and lt.k in ("list", "tuple"):
                    return self.elem_nonneg(node.left)
                return self.elem_nonneg(node.right)
            if isinstance(node.op, ast.Add):
                return self.elem_nonneg(node.left) and self.elem_nonneg(node.right)
            return False
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self.nonneg(node.elt) and self.elem_nonneg_or_scalar(node.elt)
        if isinstance(node, ast.DictComp):
            return self.nonneg(node.key) and self.nonneg(node.value)
        if isinstance(node, ast.Subscript):
            return self.elem_nonneg(node.value)
        if isinstance(node, ast.Attribute):
            return False
        if isinstance(node, ast.IfExp):
            return self.elem_nonneg(node.body) and self.elem_nonneg(node.orelse)
        if isinstance(node, ast.Call):
            f = node.func
            q = qual_of_call(self.an, f)
            a = node.args
            if q == "range":
                return self.range_nonneg(node)
            if q in ("sorted", "reversed", "list", "tuple", "set", "frozenset", "iter", "collections.deque", "deque",
                     "filter", "heapq.nlargest", "heapq.nsmallest", "nlargest", "nsmallest"):
                if not a:
                    return True
                return self.elem_nonneg(a[-1] if q == "filter" or "largest" in q or "smallest" in q else a[0])
            if q == "enumerate":
                return bool(a) and self.elem_nonneg(a[0])
            if q == "zip":
                return all(self.elem_nonneg(x) for x in a)
            if q in ("collections.Counter", "Counter"):
                return True
            if q in ("collections.defaultdict", "defaultdict", "dict"):
                return not a or True
            if q == "map":
                if len(a) >= 2:
                    fq = qual_of_call(self.an, a[0]) if isinstance(a[0], (ast.Name, ast.Attribute)) else None
                    if fq in ("abs", "len", "ord"):
                        return True
                    if fq in ("int", "float") or fq is None:
                        return False
                return False
            if q and short(q) in ("permutations", "combinations", "product", "accumulate", "combinations_with_replacement"):
                return bool(a) and self.elem_nonneg(a[0])
            if q and short(q) in ("heappop",) and a:
                return self.elem_nonneg(a[0])
            if isinstance(f, ast.Attribute):
                m = f.attr
                if m in ("items", "keys", "values", "copy", "most_common", "pop", "popleft", "union",
                         "intersection", "difference"):
                    return self.elem_nonneg(f.value)
                if m == "split":
                    return False
            ft = self.inf.node_ty.get(id(f))
            if ft is not None and ft.k in ("func", "method"):
                return self.ret_elem.get(ft.extra, True) and self.inf.func_by_fid[ft.extra].calls > 0
            return False
        return False


def _base_name(node):
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node if isinstance(node, ast.Name) else None


def _const(node):
    """Wartość stałego wyrażenia całkowitego (literały i proste działania), inaczej None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return int(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _const(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _const(node.left), _const(node.right)
        if a is None or b is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Pow) and 0 <= b <= 256 and abs(a) < 10 ** 6:
                return a ** b
            if isinstance(node.op, ast.FloorDiv) and b:
                return a // b
            if isinstance(node.op, ast.Mod) and b:
                return a % b
            if isinstance(node.op, ast.LShift) and 0 <= b < 200:
                return a << b
            if isinstance(node.op, ast.RShift) and 0 <= b < 200:
                return a >> b
            if isinstance(node.op, ast.BitAnd):
                return a & b
            if isinstance(node.op, ast.BitOr):
                return a | b
            if isinstance(node.op, ast.BitXor):
                return a ^ b
        except (OverflowError, ValueError, ZeroDivisionError):
            return None
    return None


const_int = _const

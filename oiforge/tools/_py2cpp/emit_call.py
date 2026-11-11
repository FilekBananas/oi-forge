"""Emisja wywołań: funkcje wbudowane, metody typów, moduły (math, heapq, bisect, itertools…), funkcje użytkownika."""
from __future__ import annotations

import ast

from .emit_expr import (E, P_ADD, P_AND, P_COND, P_MUL, P_OR, P_POST, P_PRIM, P_REL, P_UNARY, Unsupported,
                        cstr_lit, par)
from .infer import CMPKEY, qual_of_call, short
from .signs import const_int
from .types import (BOOL, BOT, CHAR, FLOAT, INT, STR, Ty, List, elem, is_intlike, is_scalar, join)

MATH_SAME = {"sqrt", "log2", "log10", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
             "hypot", "fabs", "atan2", "cbrt", "exp2", "log1p", "expm1", "fmod", "copysign"}


class CallMixin:
    def call(self, node, want, brace) -> E:
        f = node.func
        if self.is_readline_alias(f):
            self.use("readline")
            return E("readline()")
        q = qual_of_call(self.an, f)
        if isinstance(f, ast.Attribute):
            bt = self.T(f.value)
            if q is not None and bt.k in ("module", "builtin", "file", "stdout", "stderr", "bot", "top") or \
                    (q is not None and q.startswith(("sys.", "math.", "heapq.", "bisect.", "collections.",
                                                     "itertools.", "functools.", "string.", "copy."))):
                return self.builtin(q, node, want, brace)
            if bt.k == "class":
                return self.method_user(node, bt)
            if bt.k == "file":
                if f.attr == "read":
                    self.use("readAll")
                    return E("readAll()")
                if f.attr == "readline":
                    self.use("readline")
                    return E("readline()")
                if f.attr == "readlines":
                    self.use("readLines")
                    return E("readLines()")
                raise Unsupported(f"metoda pliku `.{f.attr}()`")
            return self.method(f.value, bt, f.attr, node, want, brace)
        if q is not None:
            return self.builtin(q, node, want, brace)
        ft = self.T(f)
        if ft.k == "func":
            return self.user_call(node, self.inf.func_by_fid[ft.extra])
        if ft.k == "classref":
            return self.construct(node, ft.extra, brace)
        if isinstance(f, ast.Name):
            s = self.S(f)
            if s is not None and s in self.thread_targets:
                return E(f"{self.thread_targets[s]}()")
        args = ", ".join(self.x(a).c for a in node.args)
        return E(f"{par(self.x(f), P_POST)}({args})", P_POST)

    # ------------------------------------------------------------------ funkcje użytkownika
    def call_args(self, node, fi) -> list[str]:
        out = []
        params = fi.params
        args = list(node.args)
        kws = {k.arg: k.value for k in node.keywords if k.arg}
        fnode = fi.node
        pos = fnode.args.posonlyargs + fnode.args.args
        if fi.self_sym is not None:
            pos = pos[1:]
        defaults = {}
        allpos = fnode.args.posonlyargs + fnode.args.args
        off = len(allpos) - len(fnode.args.defaults)
        for i, d in enumerate(fnode.args.defaults):
            defaults[allpos[off + i].arg] = d
        for a, d in zip(fnode.args.kwonlyargs, fnode.args.kw_defaults):
            if d is not None:
                defaults[a.arg] = d
        if any(isinstance(a, ast.Starred) for a in args):
            raise Unsupported("wywołanie funkcji z `*args`")
        last = -1
        vals = []
        for i, sym in enumerate(params):
            if i < len(args):
                vals.append(args[i])
                last = i
            elif sym.name in kws:
                vals.append(kws[sym.name])
                last = i
            else:
                vals.append(None)
        for i, sym in enumerate(params[:last + 1]):
            v = vals[i]
            pt = self.sym_type(sym)
            if v is None:
                d = defaults.get(sym.name)
                if d is None:
                    raise Unsupported("brak argumentu")
                out.append(self.x(d, pt).c)
            else:
                e = self.x(v, pt, brace=True)
                if e.cstr and pt.k == "str":
                    e = E(f"string({e.c})")
                out.append(e.c)
        return out

    def user_call(self, node, fi) -> E:
        args = self.call_args(node, fi)
        name = self.func_cname.get(fi.fid)
        if name is None:
            if isinstance(node.func, ast.Name):
                name = self.x(node.func).c
            else:
                name = fi.name
        return E(f"{name}({', '.join(args)})", P_POST)

    def method_user(self, node, bt) -> E:
        ci = self.inf.classes.get(bt.extra)
        f = node.func
        fi = ci.methods.get(f.attr) if ci else None
        if fi is None:
            raise Unsupported(f"metoda `{f.attr}` klasy {bt.extra}")
        args = self.call_args(node, fi)
        mname = {"__str__": "toStr", "__repr__": "toStr"}.get(f.attr)
        fsym = self.an.sym_of.get(id(fi.node))
        mname = mname or (self.cn(fsym) if fsym else f.attr)
        if self.cls_ctx is not None and isinstance(f.value, ast.Name) and self.fi is not None and \
                self.fi.self_sym is not None and self.an.sym_of.get(id(f.value)) is self.fi.self_sym:
            return E(f"{mname}({', '.join(args)})", P_POST)
        recv = self.x(f.value)
        return E(f"{par(recv, P_POST)}.{mname}({', '.join(args)})", P_POST)

    def construct(self, node, cname, brace) -> E:
        ci = self.inf.classes.get(cname)
        name = self.class_cname(cname)
        if ci is None:
            raise Unsupported(f"klasa {cname}")
        init = ci.methods.get("__init__")
        if init is not None:
            return E(f"{name}({', '.join(self.call_args(node, init))})", P_POST)
        if ci.dataclass:
            if node.keywords:
                order = ci.field_order
                vals = {k.arg: k.value for k in node.keywords}
                for i, a in enumerate(node.args):
                    vals[order[i]] = a
                items = [f".{self.field_cname(n)} = {self.x(vals[n], ci.fields.get(n)).c}" for n in order if n in vals]
                return E(f"{name}{{{', '.join(items)}}}")
            items = [self.x(a, ci.fields.get(n)).c for a, n in zip(node.args, ci.field_order)]
            return E(f"{name}{{{', '.join(items)}}}")
        return E(f"{name}()")

    # ------------------------------------------------------------------ funkcje wbudowane
    def builtin(self, q: str, node, want, brace) -> E:
        a = node.args
        n = q
        sh = short(q)
        t = self.T(node)
        x = self.x
        if n == "input":
            self.use("input")
            if a:
                return E(f"(cout << {x(a[0]).c} << flush, input())")
            return E("input()")
        if n in ("sys.stdin.readline", "sys.stdin.buffer.readline"):
            self.use("readline")
            return E("readline()")
        if n in ("sys.stdin.read", "sys.stdin.buffer.read"):
            self.use("readAll")
            return E("readAll()")
        if n in ("sys.stdin.readlines", "sys.stdin.buffer.readlines"):
            self.use("readLines")
            return E("readLines()")
        if n == "int":
            return self.to_int(node)
        if n == "float":
            if a and isinstance(a[0], ast.Constant) and isinstance(a[0].value, str):
                s = a[0].value.strip().lower()
                if s.lstrip("+-") in ("inf", "infinity"):
                    return self.inf_value(want, node, neg=s.startswith("-"))
                if s.lstrip("+-") == "nan":
                    return E("NAN")
            if not a:
                return E("0.0")
            at = self.T(a[0])
            if at.k == "str":
                return E(f"stod({x(a[0]).c})")
            if at.k == "char":
                return E(f"(double)({x(a[0]).c} - '0')", P_UNARY)
            return E(f"(double){par(x(a[0]), P_UNARY)}", P_UNARY)
        if n == "str":
            if not a:
                return E('string()')
            return self.val_str(a[0])
        if n == "bool":
            return self.cond(a[0]) if a else E("false")
        if n == "len":
            return self.len_expr(a[0])
        if n == "abs":
            at = self.T(a[0])
            if at.k == "bool":
                return E(f"(int){par(x(a[0]), P_UNARY)}", P_UNARY)
            return E(f"abs({x(a[0]).c})")
        if n in ("min", "max"):
            return self.minmax(node, n)
        if n == "sum":
            return self.sum_call(node)
        if n == "sorted":
            return self.sorted_value(node)
        if n == "reversed":
            arg = a[0]
            at = self.T(arg)
            if at.k in ("list", "str", "deque") and self.lval(arg):
                b = par(x(arg), P_POST)
                cont = "string" if at.k == "str" else self.ct(List(elem(at)))
                return E(f"{cont}({b}.rbegin(), {b}.rend())")
            return self.materialize(node)
        if n in ("list", "tuple"):
            return self.list_call(node, want)
        if n in ("set", "frozenset"):
            st = self.T(node)
            if want is not None and want.k == "set":
                st = want
            if not a:
                return E("{}" if brace else f"{self.ct(st)}()")
            arg = a[0]
            if self.lval(arg) and self.T(arg).k in ("list", "str", "set", "deque", "dict"):
                b, e = self.rng(arg)
                return E(f"{self.ct(st)}({b}, {e})")
            if isinstance(arg, (ast.List, ast.Tuple)):
                return E(f"{self.ct(st)}{{{', '.join(x(v, elem(st)).c for v in arg.elts)}}}")
            return self.fill_value(arg, st, "set")
        if n == "dict":
            dt = self.T(node) if want is None or want.k != "dict" else want
            if not a and not node.keywords:
                return E("{}" if brace else f"{self.ct(dt)}()")
            if a and self.T(a[0]).k == "dict" and self.lval(a[0]):
                return x(a[0])
            if a and isinstance(a[0], ast.Call) and self.q(a[0]) == "zip" and len(a[0].args) == 2:
                return self.fill_value(a[0], dt, "dict_pairs")
            if a:
                return self.fill_value(a[0], dt, "dict_pairs")
            raise Unsupported("dict(klucz=wartość)")
        if n == "range":
            self.use("rangeVec")
            return E(f"rangeVec({', '.join(self.range_args(node))})")
        if n in ("enumerate", "zip", "map", "filter"):
            return self.materialize(node)
        if n == "ord":
            at = self.T(a[0])
            if at.k == "char":
                return E(f"(int){par(x(a[0], CHAR), P_UNARY)}", P_UNARY)
            return E(f"(int){par(x(a[0]), P_POST)}[0]", P_UNARY)
        if n == "chr":
            return E(f"(char){par(x(a[0]), P_UNARY)}", P_UNARY)
        if n == "divmod":
            l, r = a[0], a[1]
            nonneg = self.signs.nonneg(l) and self.signs.nonneg(r)
            if not self.simple(l) or not self.simple(r):
                raise Unsupported("divmod ze złożonymi argumentami")
            if nonneg:
                q1 = f"{par(x(l), P_MUL)} / {par(x(r), P_MUL - 1)}"
                r1 = f"{par(x(l), P_MUL)} % {par(x(r), P_MUL - 1)}"
            else:
                self.use("floordiv", "pymod")
                q1 = f"floordiv({x(l).c}, {x(r).c})"
                r1 = f"pymod({x(l).c}, {x(r).c})"
            if brace:
                return E(f"{{{q1}, {r1}}}")
            return E(f"make_pair({q1}, {r1})")
        if n == "pow":
            if len(a) == 3:
                self.use("powmod")
                return E(f"powmod({x(a[0]).c}, {x(a[1]).c}, {x(a[2]).c})")
            return self.power(a[0], a[1], node)
        if n == "round":
            at = self.T(a[0])
            if len(a) >= 2 and not (isinstance(a[1], ast.Constant) and a[1].value is None):
                if is_intlike(at):
                    return x(a[0])
                self.use("pyround")
                return E(f"pyround({x(a[0]).c}, {x(a[1]).c})")
            if is_intlike(at):
                return x(a[0])
            self.info("round", "round() w Pythonie zaokrągla połówki do parzystej → `nearbyint`.", node.lineno)
            return E(f"({self.int_t})nearbyint({x(a[0]).c})", P_UNARY)
        if n in ("any", "all"):
            return self.any_all(node, n)
        if n in ("hex", "bin", "oct"):
            self.use("toBase")
            return E(f"toBase({x(a[0]).c}, {dict(hex=16, bin=2, oct=8)[n]})")
        if n == "isinstance":
            raise Unsupported("isinstance()")
        if n == "print":
            raise Unsupported("print() wewnątrz wyrażenia")
        if n in ("exit", "quit", "sys.exit"):
            if a and self.T(a[0]).k == "str":
                return E(f"(cerr << {x(a[0]).c} << '\\n', exit(1))")
            return E(f"exit({x(a[0]).c if a else '0'})")
        if n == "next":
            at = self.T(a[0])
            if at.k == "tokens":
                self.use("nextToken")
                return E("nextToken()")
            if isinstance(a[0], ast.Name) and self.S(a[0]) in self.iter_src:
                it = self.cn(self.S(a[0]))
                src = self.cn(self.resolve_alias(self.iter_src[self.S(a[0])]))
                if len(a) > 1:
                    return E(f"{it} < {src}.size() ? {src}[{it}++] : {self.x(a[1], elem(at)).c}", P_COND)
                return E(f"{src}[{it}++]", P_POST)
            raise Unsupported("next() dla zwykłego iteratora")
        if n == "iter":
            raise Unsupported("iter() w wyrażeniu")
        if n == "repr":
            self.use("reprcore")
            return E(f"repr({x(a[0]).c})")
        if n == "format":
            if len(a) == 2 and isinstance(a[1], ast.Constant):
                return self.val_str(a[0], None, a[1].value)
            return self.val_str(a[0])
        if n in ("id", "hash"):
            raise Unsupported(f"{n}()")
        if n == "open":
            raise Unsupported("open() — pliki nie są tłumaczone (OI czyta ze standardowego wejścia)")
        if n.startswith("math."):
            return self.math_call(sh, node)
        if sh in ("gcd", "lcm", "sqrt", "isqrt", "floor", "ceil", "factorial", "comb", "perm", "log", "inf",
                  "hypot", "log2", "log10", "exp", "pi") and n.startswith("math"):
            return self.math_call(sh, node)
        if n.startswith("heapq.") or n in ("heappush", "heappop", "heapify", "heappushpop", "heapreplace",
                                            "nlargest", "nsmallest"):
            return self.heap_call(sh, node)
        if n.startswith("bisect.") or n in ("bisect_left", "bisect_right", "bisect", "insort", "insort_left",
                                             "insort_right"):
            return self.bisect_call(sh, node)
        if n in ("collections.deque", "deque"):
            dt = self.T(node) if want is None or want.k != "deque" else want
            if self.kw(node, "maxlen") is not None:
                self.warn("todo", "deque(maxlen=...) — limit długości nie jest tłumaczony.", node.lineno)
            if not a:
                return E("{}" if brace else f"{self.ct(dt)}()")
            arg = a[0]
            if isinstance(arg, (ast.List, ast.Tuple)):
                return E(f"{self.ct(dt)}{{{', '.join(x(v, elem(dt), brace=True).c for v in arg.elts)}}}")
            if self.lval(arg):
                b, e = self.rng(arg)
                return E(f"{self.ct(dt)}({b}, {e})")
            b, e = self.range_pair(arg)
            return E(f"{self.ct(dt)}({b}, {e})")
        if n in ("collections.defaultdict", "defaultdict"):
            dt = self.T(node) if want is None or want.k != "dict" else want
            self._check_factory(a[0] if a else None, node)
            return E("{}" if brace else f"{self.ct(dt)}()")
        if n in ("collections.Counter", "Counter"):
            dt = self.T(node) if want is None or want.k != "dict" else want
            if not a:
                return E("{}" if brace else f"{self.ct(dt)}()")
            return self.fill_value(a[0], dt, "counter")
        if n in ("collections.OrderedDict", "OrderedDict"):
            self.warn("dict-order", "OrderedDict → map (posortowany po kluczu, nie w kolejności wstawiania).",
                      node.lineno)
            dt = self.T(node)
            return E(f"{self.ct(dt)}()")
        if n.startswith("itertools.") or n in ("permutations", "combinations", "product", "accumulate",
                                                "combinations_with_replacement"):
            return self.itertools_call(sh, node)
        if n in ("functools.reduce", "reduce"):
            return self.reduce_call(node)
        if n in ("copy.copy", "copy.deepcopy"):
            self.info("copy", "copy/deepcopy → zwykła kopia (w C++ przypisanie kopiuje kontenery).", node.lineno)
            return x(a[0])
        if n in ("sys.setrecursionlimit", "threading.stack_size", "setrecursionlimit"):
            return E("(void)0")
        if n in ("sys.stdout.write", "sys.stderr.write"):
            raise Unsupported("sys.stdout.write w wyrażeniu")
        if n == "sys.stdout.flush":
            return E("cout.flush()")
        raise Unsupported(f"funkcja `{n}`")

    def _check_factory(self, f, node):
        if f is None:
            return
        if isinstance(f, ast.Lambda):
            b = f.body
            ok = (isinstance(b, ast.Constant) and b.value in (0, 0.0, "", False)) or \
                (isinstance(b, (ast.List, ast.Dict, ast.Set, ast.Tuple)) and not getattr(b, "elts", getattr(b, "keys", None)))
            if not ok:
                self.warn("defaultdict", f"defaultdict(lambda: {self.src(b)}): w C++ `map[k]` tworzy wartość "
                          "domyślną (0/pusty) — inna wartość początkowa wymaga ręcznej obsługi!", node.lineno, "error")

    def to_int(self, node) -> E:
        a = node.args
        if not a:
            return E("0")
        arg = a[0]
        at = self.T(arg)
        if len(a) == 2:
            fn = "stoll" if self.int_t == "long long" else "stoi"
            return E(f"{fn}({self.x(arg).c}, nullptr, {self.x(a[1]).c})")
        if isinstance(arg, ast.Call) and self.q(arg) == "next" and arg.args and self.T(arg.args[0]).k == "tokens":
            self.use("nextInt")
            return E("nextInt()")
        if at.k == "str":
            fn = "stoll" if self.int_t == "long long" else "stoi"
            e = self.x(arg)
            if e.cstr:
                try:
                    return self.int_lit(int(arg.value))
                except (ValueError, AttributeError):
                    pass
            return E(f"{fn}({e.c})")
        if at.k == "char":
            if isinstance(arg, ast.Constant):
                try:
                    return E(str(int(arg.value)))
                except ValueError:
                    pass
            return E(f"({par(self.x(arg, CHAR), P_ADD)} - '0')")
        if at.k in ("float", "inf"):
            return E(f"({self.int_t}){par(self.x(arg), P_UNARY)}", P_UNARY)
        if at.k == "bool":
            return E(f"(int){par(self.x(arg), P_UNARY)}", P_UNARY)
        return self.x(arg)

    # ------------------------------------------------------------------ min / max / sum / any / all
    def minmax_args(self, args, t: Ty) -> list[str]:
        out = []
        for a in args:
            e = self.x(a, t)
            if is_intlike(t) and self.int_t == "long long":
                cv = const_int(a)
                if cv is not None and (isinstance(a, ast.Constant) or (isinstance(a, ast.UnaryOp) and
                                                                       isinstance(a.operand, ast.Constant))):
                    e = self.int_lit(cv, force_ll=True)
                elif self._non_ll(a):
                    e = E(f"({self.int_t}){par(e, P_UNARY)}", P_UNARY)
            if t.k == "float" and is_intlike(self.T(a)) and not isinstance(a, ast.Constant):
                e = E(f"(double){par(e, P_UNARY)}", P_UNARY)
            if t.k == "str" and e.cstr:
                e = E(f"string({e.c})")
            out.append(e.c)
        return out

    def _non_ll(self, node) -> bool:
        """Czy naturalny typ C++ wyrażenia może nie być long long (np. bisect, ord, znak)."""
        if isinstance(node, ast.Call):
            q = self.q(node)
            if q and short(q) in ("bisect_left", "bisect_right", "bisect", "ord", "int") or \
                    (isinstance(node.func, ast.Attribute) and node.func.attr in ("count", "index", "find")):
                return True
        if self.T(node).k in ("char", "bool"):
            return True
        if isinstance(node, ast.BinOp):
            return self._non_ll(node.left) and self._non_ll(node.right)
        return False

    def minmax(self, node, n) -> E:
        a = node.args
        key = self.kw(node, "key")
        default = self.kw(node, "default")
        t = self.T(node)
        if len(a) == 1:
            arg = a[0]
            at = self.T(arg)
            if at.k == "dict":
                if key is not None and isinstance(key, ast.Attribute) and key.attr == "get" and \
                        self.src(key.value) == self.src(arg):
                    d = self.x(arg).c
                    cmp = "<" if n == "max" else ">"
                    fn = "max_element"
                    return E(f"{fn}({d}.begin(), {d}.end(), [](const auto& a, const auto& b) "
                             f"{{ return a.second {cmp} b.second; }})->first", P_POST)
                if key is None:
                    d = self.x(arg).c
                    return E(f"{d}.rbegin()->first" if n == "max" else f"{d}.begin()->first", P_POST)
            if isinstance(arg, (ast.GeneratorExp, ast.ListComp)) or at.k == "lazy":
                return self.accumulate_into(arg, n, t, default)
            if key is not None:
                comp = self.key_comparator(key, elem(at), False)
                b, e = self.rng(arg) if self.lval(arg) else self._tmp_range(arg)
                return E(f"*{n}_element({b}, {e}, {comp})", P_UNARY)
            if self.lval(arg):
                b, e = self.rng(arg)
                code = E(f"*{n}_element({b}, {e})", P_UNARY)
            else:
                code = E(f"ranges::{n}({self.x(arg).c})")
            if default is not None:
                c = self.x(arg).c
                return E(f"{c}.empty() ? {self.x(default, t).c} : {code.c}", P_COND)
            return code
        if key is not None:
            comp = self.key_comparator(key, t, False)
            items = self.minmax_args(a, t)
            return E(f"{n}({{{', '.join(items)}}}, {comp})")
        items = self.minmax_args(a, t)
        if len(items) == 2:
            return E(f"{n}({items[0]}, {items[1]})")
        return E(f"{n}({{{', '.join(items)}}})")

    def _tmp_range(self, node):
        m = self.materialize(node)
        if not self.hoist_ok:
            raise Unsupported("tymczasowy kontener w tym miejscu")
        name = m.c
        if not name.isidentifier():
            tmp = self.fresh("tmp")
            self.out(f"auto {tmp} = {m.c};")
            name = tmp
        return f"{name}.begin()", f"{name}.end()"

    def accumulate_into(self, gen, kind, t: Ty, default=None, start=None) -> E:
        """sum/min/max/any/all z generatora → pętla akumulująca (wyciągnięta przed instrukcję)."""
        if not self.hoist_ok:
            raise Unsupported(f"{kind}() z generatorem w tym miejscu (np. w warunku pętli)")
        et = elem(self.T(gen))
        if kind == "sum":
            acc_t = t if t.k not in ("bot",) else INT
            name = self.fresh("total")
            init = self.x(start, acc_t).c if start is not None else ("0.0" if acc_t.k == "float" else "0")
            self.out(f"{self.ct(acc_t)} {name} = {init};")

            def act(v):
                e = self.x(v, acc_t) if isinstance(v, ast.AST) else v
                self.out(f"{name} += {e.c};")
            self.replay(self.loop_fill(gen, name, "acc", None, act))
            return E(name)
        if kind in ("min", "max"):
            acc_t = t
            name = self.fresh(f"{kind}_val")
            first = self.fresh("first")
            self.out(f"{self.ct(acc_t)} {name}{self.default_init_t(acc_t)};")
            self.out(f"bool {first} = true;")
            op = "<" if kind == "min" else ">"

            def act(v):
                e = self.x(v, acc_t) if isinstance(v, ast.AST) else v
                if not isinstance(v, ast.AST) or not self.simple(v):
                    tmpv = self.fresh("v")
                    self.out(f"{self.ct(acc_t)} {tmpv} = {e.c};")
                    e = E(tmpv)
                self.out(f"if ({first} || {par(e, P_REL)} {op} {name}) {{ {name} = {e.c}; {first} = false; }}")
            self.replay(self.loop_fill(gen, name, "acc", None, act))
            if default is not None:
                self.out(f"if ({first}) {name} = {self.x(default, acc_t).c};")
            return E(name)
        if kind in ("any", "all"):
            name = self.fresh("found" if kind == "any" else "ok")
            self.out(f"bool {name} = {'false' if kind == 'any' else 'true'};")

            def act(v):
                c = self.cond(v) if isinstance(v, ast.AST) else v
                if kind == "any":
                    self.out(f"if ({c.c}) {{ {name} = true; break; }}")
                else:
                    self.out(f"if (!{par(c, P_UNARY)}) {{ {name} = false; break; }}")
            lines = self.loop_fill(gen, name, "acc", None, act)
            depth = sum(1 for ln in lines if ln.text.startswith("for ") or ln.text.startswith("for("))
            if depth > 1:
                lines = [ln for ln in lines]
                fixed = []
                for ln in lines:
                    fixed.append(ln)
                self.warn("any-nested", "any()/all() z kilkoma `for`: `break` przerywa tylko wewnętrzną pętlę "
                          "(wynik ten sam, ale wolniej).", getattr(gen, "lineno", None), "info")
            self.replay(lines)
            return E(name)
        raise Unsupported(kind)

    def default_init_t(self, t: Ty) -> str:
        if t.k in ("int", "float", "inf", "char"):
            return " = 0"
        if t.k == "bool":
            return " = false"
        return "{}"

    def sum_call(self, node) -> E:
        a = node.args
        start = a[1] if len(a) > 1 else self.kw(node, "start")
        t = self.T(node)
        arg = a[0]
        at = self.T(arg)
        if isinstance(arg, (ast.GeneratorExp, ast.ListComp)) or at.k == "lazy":
            # sum(1 for x in a if p) → count_if
            if isinstance(arg, (ast.GeneratorExp, ast.ListComp)) and len(arg.generators) == 1 and \
                    len(arg.generators[0].ifs) == 1 and isinstance(arg.elt, ast.Constant) and arg.elt.value == 1 and \
                    self.lval(arg.generators[0].iter) and isinstance(arg.generators[0].target, ast.Name) and \
                    self.T(arg.generators[0].iter).k in ("list", "str", "deque", "set") and start is None:
                return self.len_expr(ast.copy_location(
                    ast.ListComp(elt=arg.generators[0].target, generators=arg.generators), arg)) \
                    if False else self._count_if(arg)
            return self.accumulate_into(arg, "sum", t, start=start)
        init_t = t if t.k not in ("bot",) else INT
        if start is not None:
            init = self.x(start, init_t).c
        else:
            init = "0.0" if init_t.k == "float" else ("0LL" if self.int_t == "long long" else "0")
        if self.lval(arg) and at.k in ("list", "deque", "set", "tuple"):
            if at.k == "tuple":
                raise Unsupported("sum() z krotki")
            b, e = self.rng(arg)
            return E(f"accumulate({b}, {e}, {init})")
        if at.k == "dict":
            raise Unsupported("sum() ze słownika")
        self.use("sumOf")
        return E(f"sumOf({self.x(arg).c})")

    def _count_if(self, gen) -> E:
        g = gen.generators[0]
        sym = self.an.sym_of.get(id(g.target))
        et = self.sym_type(sym)
        v = self.cn(sym)
        b, e = self.rng(g.iter)
        ptype = self.ct(et) if is_scalar(et) else "const auto&"
        saved = self.hoist_ok
        self.hoist_ok = False
        try:
            c = self.cond(g.ifs[0])
        finally:
            self.hoist_ok = saved
        return E(f"count_if({b}, {e}, [&]({ptype} {v}) {{ return {c.c}; }})")

    def any_all(self, node, n) -> E:
        arg = node.args[0]
        if isinstance(arg, (ast.GeneratorExp, ast.ListComp)) and len(arg.generators) == 1 and \
                len(arg.generators[0].ifs) <= 1 and isinstance(arg.generators[0].target, ast.Name) and \
                self.lval(arg.generators[0].iter) and self.T(arg.generators[0].iter).k in ("list", "str", "deque",
                                                                                            "set"):
            g = arg.generators[0]
            sym = self.an.sym_of.get(id(g.target))
            et = self.sym_type(sym)
            v = self.cn(sym)
            b, e = self.rng(g.iter)
            ptype = self.ct(et) if is_scalar(et) else "const auto&"
            saved = self.hoist_ok
            self.hoist_ok = False
            try:
                c = self.cond(arg.elt)
                if g.ifs:
                    f = self.cond(g.ifs[0])
                    c = E(f"{par(f, P_AND)} && {par(c, P_AND)}", P_AND) if n == "any" else \
                        E(f"!{par(f, P_UNARY)} || {par(c, P_OR)}", P_OR)
            finally:
                self.hoist_ok = saved
            return E(f"{n}_of({b}, {e}, [&]({ptype} {v}) {{ return {c.c}; }})")
        if isinstance(arg, (ast.GeneratorExp, ast.ListComp)) or self.T(arg).k == "lazy":
            return self.accumulate_into(arg, n, BOOL)
        if self.lval(arg):
            b, e = self.rng(arg)
            return E(f"{n}_of({b}, {e}, [](const auto& v) {{ return (bool)v; }})")
        raise Unsupported(f"{n}() z wyrażenia")

    # ------------------------------------------------------------------ sortowanie
    def key_comparator(self, key, et: Ty, reverse: bool) -> str:
        """key=… / reverse=… → komparator C++ (porównuje klucze)."""
        kt = self.T(key)
        if kt.k == CMPKEY:
            fi = self.inf.func_by_fid[kt.extra]
            fname = self.func_cname.get(fi.fid, fi.name)
            op = ">" if reverse else "<"
            return f"[&](const auto& a, const auto& b) {{ return {fname}(a, b) {op} 0; }}"
        if isinstance(key, ast.Lambda) and len(key.args.args) == 1:
            sym = self.an.sym_of.get(id(key.args.args[0]))
            names_in_body = {n.id for n in ast.walk(key.body) if isinstance(n, ast.Name)}
            pa, pb = next(((p, q) for p, q in (("a", "b"), ("x", "y"), ("p", "q"), ("l_", "r_"))
                           if p not in names_in_body and q not in names_in_body), ("l_", "r_"))
            saved = self.hoist_ok
            self.hoist_ok = False
            try:
                self.rename[sym] = pa
                ka = self.key_value(key.body)
                self.rename[sym] = pb
                kb = self.key_value(key.body)
            finally:
                self.rename.pop(sym, None)
                self.hoist_ok = saved
            if reverse:
                ka, kb = kb, ka
                pa_, pb_ = pa, pb
            else:
                pa_, pb_ = pa, pb
            return f"[&](const auto& {pa_}, const auto& {pb_}) {{ return {par(ka, P_REL)} < {par(kb, P_REL - 1)}; }}"
        if isinstance(key, ast.Name):
            q = qual_of_call(self.an, key)
            if q == "len":
                op = ">" if reverse else "<"
                return f"[](const auto& a, const auto& b) {{ return a.size() {op} b.size(); }}"
            if q in ("abs",):
                op = ">" if reverse else "<"
                return f"[](const auto& a, const auto& b) {{ return abs(a) {op} abs(b); }}"
            fname = self.x(key).c
            op = ">" if reverse else "<"
            return f"[&](const auto& a, const auto& b) {{ return {fname}(a) {op} {fname}(b); }}"
        if isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name) and key.value.id == "str" and \
                key.attr in ("lower", "upper", "strip", "lstrip", "rstrip", "swapcase", "title"):
            self.use(key.attr)
            op = ">" if reverse else "<"
            return f"[](const auto& a, const auto& b) {{ return {key.attr}(a) {op} {key.attr}(b); }}"
        if isinstance(key, ast.Attribute) and key.attr == "get" and self.T(key.value).k == "dict":
            d = self.x(key.value).c
            op = ">" if reverse else "<"
            self.use("dget")
            return f"[&](const auto& a, const auto& b) {{ return dget({d}, a) {op} dget({d}, b); }}"
        raise Unsupported("klucz sortowania")

    def key_value(self, body) -> E:
        """Wartość klucza: krotki → make_pair/make_tuple z jednakowymi typami."""
        if isinstance(body, ast.Tuple):
            items = []
            for e in body.elts:
                v = self.x(e)
                if isinstance(e, ast.Constant) and isinstance(e.value, int) and self.int_t == "long long":
                    v = self.int_lit(e.value, force_ll=True)
                if v.cstr:
                    v = E(f"string({v.c})")
                items.append(v.c)
            fn = "make_pair" if len(items) == 2 else "make_tuple"
            return E(f"{fn}({', '.join(items)})")
        v = self.x(body)
        return E(f"string({v.c})") if v.cstr else v

    def sort_stmt(self, target: str, et: Ty, key, reverse_node) -> None:
        """Linie sortujące kontener `target` (in place), jak list.sort(key=…, reverse=…)."""
        rev = False
        if reverse_node is not None:
            if isinstance(reverse_node, ast.Constant):
                rev = bool(reverse_node.value)
            else:
                raise Unsupported("reverse= nie będące stałą")
        if key is None:
            if rev:
                self.out(f"sort({target}.begin(), {target}.end(), greater<>());")
            else:
                self.out(f"sort({target}.begin(), {target}.end());")
            return
        comp = self.key_comparator(key, et, rev)
        self.note("sort w Pythonie jest stabilny")
        self.out(f"stable_sort({target}.begin(), {target}.end(), {comp});")
        self.info("stable", "Sortowanie z `key=` → `stable_sort` (sort w Pythonie jest stabilny).",
                  getattr(key, "lineno", None))

    def sorted_into(self, decl: str | None, name: str, node) -> None:
        """b = sorted(a, key=…, reverse=…) jako instrukcje."""
        a = node.args
        arg = a[0]
        at = self.T(arg)
        t = self.T(node)
        et = elem(t)
        key = self.kw(node, "key")
        rev = self.kw(node, "reverse")
        ctype = self.ct(t)
        if at.k == "dict":
            if self.lval(arg):
                self.use("keys")
                src = f"keys({self.x(arg).c})"
            else:
                raise Unsupported("sorted() słownika z wyrażenia")
            self.out(f"{decl + ' ' if decl else ''}{name} = {src};")
        elif self.lval(arg) and at.k == "list":
            self.out(f"{decl + ' ' if decl else ''}{name} = {self.x(arg).c};")
        elif self.lval(arg) and at.k in ("str", "set", "deque"):
            b, e = self.rng(arg)
            if decl:
                self.out(f"{ctype} {name}({b}, {e});")
            else:
                self.out(f"{name} = {ctype}({b}, {e});")
        elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "items" and \
                self.lval(arg.func.value):
            b, e = self.rng(arg.func.value)
            if decl:
                self.out(f"{ctype} {name}({b}, {e});")
            else:
                self.out(f"{name} = {ctype}({b}, {e});")
        else:
            m = self.materialize(arg)
            if decl:
                self.decl_line(decl, name, m.c)
            else:
                self.out(f"{name} = {m.c};")
        self.sort_stmt(name, et, key, rev)

    def sorted_value(self, node) -> E:
        t = self.T(node)
        key = self.kw(node, "key")
        rev = self.kw(node, "reverse")
        arg = node.args[0]
        if key is None and rev is None and self.T(arg).k in ("list", "str", "set", "deque") and \
                not self.hoist_ok:
            self.use("sorted")
            return E(f"sorted({self.x(arg).c})")
        if not self.hoist_ok:
            raise Unsupported("sorted() z kluczem w tym miejscu")
        name = self.fresh("sorted_")
        self.sorted_into(self.ct(t), name, node)
        return E(name)

    # ------------------------------------------------------------------ list()/set()/Counter() z iteratora
    def list_call(self, node, want) -> E:
        a = node.args
        t = self.T(node)
        if want is not None and want.k == "list":
            t = want
        if not a:
            return E(f"{self.ct(t)}()")
        arg = a[0]
        at = self.T(arg)
        if at.k == "list" and not isinstance(arg, (ast.ListComp,)):
            return self.x(arg)
        if isinstance(arg, ast.Call):
            q = self.q(arg)
            if q == "range" and self.hoist_ok is not None:
                self.use("rangeVec")
                return E(f"rangeVec({', '.join(self.range_args(arg))})")
            if isinstance(arg.func, ast.Attribute) and self.T(arg.func.value).k == "dict" and \
                    arg.func.attr in ("keys", "values", "items") and self.lval(arg.func.value):
                d = self.x(arg.func.value).c
                if arg.func.attr == "keys":
                    self.use("keys")
                    return E(f"keys({d})")
                if arg.func.attr == "values":
                    self.use("values")
                    return E(f"values({d})")
                return E(f"{self.ct(t)}({d}.begin(), {d}.end())")
            tr = self.read_tuple_pattern(node)
            if tr is not None and tr[0] == "int":
                self.use("readInts")
                return E("readInts()")
            if tr is not None and tr[0] == "float":
                self.use("readFloats")
                return E("readFloats()")
        if at.k == "dict" and self.lval(arg):
            self.use("keys")
            return E(f"keys({self.x(arg).c})")
        if at.k in ("str", "set", "deque", "char") and self.lval(arg):
            b, e = self.rng(arg)
            return E(f"{self.ct(t)}({b}, {e})")
        if at.k in ("str",):
            v = self.x(arg)
            if v.cstr:
                return E(f"{self.ct(t)}{{{', '.join(repr(c) for c in arg.value)}}}")
        return self.materialize(arg)

    def fill_value(self, src, cont_t: Ty, mode: str) -> E:
        """Kontener budowany pętlą (set z generatora, Counter, dict(zip(...)))."""
        if not self.hoist_ok:
            raise Unsupported("budowanie kontenera w tym miejscu")
        name = self.fresh({"set": "st", "counter": "cnt", "dict_pairs": "mp"}[mode])
        self.out(f"{self.ct(cont_t)} {name};")
        self.fill_lines(name, src, cont_t, mode)
        return E(name)

    def fill_lines(self, name, src, cont_t, mode) -> None:
        if mode == "counter":
            st = self.T(src)
            if self.lval(src) and st.k in ("list", "str", "deque", "set"):
                v = self.fresh("x")
                et = elem(st)
                ptype = self.ct(et) if is_scalar(et) else "const auto&"
                self.out(f"for ({ptype} {v} : {self.x(src).c}) {name}[{v}]++;")
                return
            if st.k == "dict":
                self.out(f"for (const auto& [k, v] : {self.x(src).c}) {name}[k] += v;")
                return

            def act(v):
                e = self.x(v, cont_t.args[0]) if isinstance(v, ast.AST) else v
                self.out(f"{name}[{e.c}]++;")
            self.replay(self.loop_fill(src, name, "acc", None, act))
            return
        if mode == "set":
            def act(v):
                e = self.x(v, cont_t.args[0]) if isinstance(v, ast.AST) else v
                self.out(f"{name}.insert({e.c});")
            self.replay(self.loop_fill(src, name, "acc", None, act))
            return
        if mode == "dict_pairs":
            kt, vt = cont_t.args
            if isinstance(src, ast.Call) and self.q(src) == "zip" and len(src.args) == 2 and \
                    all(self.lval(z) for z in src.args):
                i = self.fresh("i")
                a0, a1 = self.x(src.args[0]).c, self.x(src.args[1]).c
                self.out(f"for (size_t {i} = 0; {i} < min({a0}.size(), {a1}.size()); {i}++) "
                         f"{name}[{a0}[{i}]] = {a1}[{i}];")
                return

            def act(v):
                e = self.x(v) if isinstance(v, ast.AST) else v
                if isinstance(v, ast.Tuple) and len(v.elts) == 2:
                    self.out(f"{name}[{self.x(v.elts[0], kt).c}] = {self.x(v.elts[1], vt).c};")
                else:
                    tmp = self.fresh("kv")
                    self.out(f"auto {tmp} = {e.c};")
                    self.out(f"{name}[{tmp}.first] = {tmp}.second;")
            self.replay(self.loop_fill(src, name, "acc", None, act))

    # ------------------------------------------------------------------ math
    def math_call(self, m: str, node) -> E:
        a = node.args
        x = self.x
        at = self.T(a[0]) if a else BOT
        if m in ("gcd", "lcm"):
            if not a:
                return E("0" if m == "gcd" else "1")
            code = x(a[0]).c
            for b in a[1:]:
                code = f"{m}({code}, {x(b).c})"
            return E(code) if len(a) > 1 else x(a[0])
        if m in ("floor", "ceil", "trunc"):
            if is_intlike(at):
                return x(a[0])
            return E(f"({self.int_t}){m}({x(a[0]).c})", P_UNARY)
        if m == "isqrt":
            self.use("isqrt")
            return E(f"isqrt({x(a[0]).c})")
        if m == "sqrt":
            return E(f"sqrt({x(a[0]).c})")
        if m == "log":
            if len(a) == 2:
                return E(f"log({x(a[0]).c}) / log({x(a[1]).c})", P_MUL)
            return E(f"log({x(a[0]).c})")
        if m == "pow":
            return E(f"pow({x(a[0]).c}, {x(a[1]).c})")
        if m in MATH_SAME:
            return E(f"{m}({', '.join(x(v).c for v in a)})")
        if m == "factorial":
            self.use("factorial")
            self.warn("overflow", "math.factorial: 21! nie mieści się w 64 bitach — w C++ licz silnię modulo.",
                      node.lineno)
            return E(f"factorial({x(a[0]).c})")
        if m in ("comb", "perm"):
            self.use(m)
            return E(f"{m}({x(a[0]).c}, {x(a[1]).c})")
        if m == "isclose":
            return E(f"(fabs({x(a[0]).c} - {x(a[1]).c}) <= 1e-9 * max(fabs({x(a[0]).c}), fabs({x(a[1]).c})))")
        if m in ("isinf", "isnan", "isfinite"):
            return E(f"{m}({x(a[0]).c})")
        if m == "prod":
            arg = a[0]
            et = elem(self.T(arg))
            if self.lval(arg):
                b, e = self.rng(arg)
                one = "1.0" if et.k == "float" else ("1LL" if self.int_t == "long long" else "1")
                return E(f"accumulate({b}, {e}, {one}, multiplies<>())")
            raise Unsupported("math.prod z wyrażenia")
        if m == "degrees":
            return E(f"{par(x(a[0]), P_MUL)} * 180.0 / numbers::pi", P_MUL)
        if m == "radians":
            return E(f"{par(x(a[0]), P_MUL)} * numbers::pi / 180.0", P_MUL)
        if m == "dist":
            raise Unsupported("math.dist")
        raise Unsupported(f"math.{m}")

    # ------------------------------------------------------------------ heapq
    def heap_target(self, node):
        if isinstance(node, ast.Name):
            s = self.S(node)
            if self.is_pure_heap(s):
                return "pq", self.cn(s)
        return "vec", self.x(node).c

    def heap_call(self, m: str, node) -> E:
        a = node.args
        if m in ("nlargest", "nsmallest"):
            if not self.hoist_ok:
                raise Unsupported(f"heapq.{m} w tym miejscu")
            k = self.x(a[0]).c
            t = self.T(node)
            name = self.fresh("best")
            m2 = self.materialize(a[1]) if not self.lval(a[1]) else self.x(a[1])
            self.out(f"{self.ct(t)} {name} = {m2.c};")
            key = self.kw(node, "key")
            self.sort_stmt(name, elem(t), key, ast.Constant(m == "nlargest"))
            self.out(f"if ((long long){name}.size() > {k}) {name}.resize({k});")
            return E(name)
        mode, h = self.heap_target(a[0])
        ht = self.T(a[0])
        et = elem(ht)
        if mode == "pq":
            if m == "heappush":
                return E(f"{h}.push({self.x(a[1], et, brace=True).c})", P_POST)
            if m == "heappop":
                self.use("pqPop")
                return E(f"pqPop({h})")
            if m == "heapify":
                return E("(void)0")
            if m == "heappushpop":
                raise Unsupported("heappushpop")
            if m == "heapreplace":
                raise Unsupported("heapreplace")
        self.use("vecHeap")
        if m == "heappush":
            return E(f"heappush({h}, {self.x(a[1], et, brace=True).c})")
        if m == "heappop":
            return E(f"heappop({h})")
        if m == "heapify":
            return E(f"heapify({h})")
        raise Unsupported(f"heapq.{m}")

    # ------------------------------------------------------------------ bisect
    def bisect_call(self, m: str, node) -> E:
        a = node.args
        if self.kw(node, "key") is not None:
            raise Unsupported("bisect z key=")
        arr = a[0]
        if not self.lval(arr):
            raise Unsupported("bisect na wyrażeniu (użyj zmiennej)")
        arr_c = par(self.x(arr), P_POST)
        et = elem(self.T(arr))
        val = self.x(a[1], et).c
        lo = a[2] if len(a) > 2 else self.kw(node, "lo")
        hi = a[3] if len(a) > 3 else self.kw(node, "hi")
        b = f"{arr_c}.begin()" + (f" + {par(self.x(lo), P_ADD - 1)}" if lo is not None else "")
        e = f"{arr_c}.begin() + {par(self.x(hi), P_ADD - 1)}" if hi is not None else f"{arr_c}.end()"
        fn = "lower_bound" if m in ("bisect_left", "insort_left") else "upper_bound"
        if m.startswith("insort"):
            return E(f"{arr_c}.insert({fn}({b}, {e}, {val}), {val})", P_POST)
        return E(f"{fn}({b}, {e}, {val}) - {arr_c}.begin()", P_ADD)

    # ------------------------------------------------------------------ itertools / functools
    def itertools_call(self, m: str, node) -> E:
        a = node.args
        t = self.T(node)
        if m in ("permutations", "combinations"):
            self.use(m)
            src = self._vec_arg(a[0])
            r = a[1] if len(a) > 1 else self.kw(node, "r")
            if m == "combinations":
                return E(f"combinations({src}, {self.x(r).c})")
            return E(f"permutations({src}{', ' + self.x(r).c if r is not None else ''})")
        if m == "product":
            self.use("product")
            rep = self.kw(node, "repeat")
            et = elem(elem(t))
            pools = [self._vec_arg(p, et) for p in a]
            if rep is not None:
                if len(pools) != 1:
                    raise Unsupported("product(…, repeat=) z wieloma zbiorami")
                return E(f"product(vector<{self.ct(List(et))}>({self.x(rep).c}, {pools[0]}))")
            return E(f"product(vector<{self.ct(List(et))}>{{{', '.join(pools)}}})")
        if m == "accumulate":
            if len(a) > 1 or self.kw(node, "initial") is not None:
                raise Unsupported("accumulate z funkcją lub initial=")
            self.use("prefixSums")
            return E(f"prefixSums({self._vec_arg(a[0])})")
        raise Unsupported(f"itertools.{m}")

    def _vec_arg(self, node, et=None) -> str:
        t = self.T(node)
        if t.k == "list":
            return self.x(node).c
        if t.k == "str":
            e = self.x(node)
            c = f"string({e.c})" if e.cstr else e.c
            if self.lval(node):
                return f"vector<char>({c}.begin(), {c}.end())"
            if not self.hoist_ok:
                raise Unsupported("napis-wyrażenie w itertools")
            tmp = self.fresh("s")
            self.out(f"string {tmp} = {c};")
            return f"vector<char>({tmp}.begin(), {tmp}.end())"
        return self.materialize(node).c

    def reduce_call(self, node) -> E:
        a = node.args
        if not self.hoist_ok:
            raise Unsupported("reduce() w tym miejscu")
        t = self.T(node)
        f = a[0]
        name = self.fresh("acc")
        it = a[1]
        et = elem(self.T(it))
        if len(a) > 2:
            self.out(f"{self.ct(t)} {name} = {self.x(a[2], t).c};")
            first = None
        else:
            first = self.fresh("first")
            self.out(f"{self.ct(t)} {name}{self.default_init_t(t)};")
            self.out(f"bool {first} = true;")
        fx = self.x(f) if not isinstance(f, ast.Lambda) else self.lambda_expr(f, [t, et])
        fn = fx.c
        if isinstance(f, ast.Lambda):
            fname = self.fresh("fn")
            self.out(f"auto {fname} = {fn};")
            fn = fname

        def act(v):
            e = self.x(v, et) if isinstance(v, ast.AST) else v
            if first:
                self.out(f"if ({first}) {{ {name} = {e.c}; {first} = false; }} else {name} = {fn}({name}, {e.c});")
            else:
                self.out(f"{name} = {fn}({name}, {e.c});")
        self.replay(self.loop_fill(it, name, "acc", None, act))
        return E(name)

    # ------------------------------------------------------------------ metody typów wbudowanych
    def method(self, recv, rt: Ty, m: str, node, want, brace) -> E:
        a = node.args
        x = self.x
        k = rt.k
        if k in ("str", "char"):
            return self.str_method(recv, rt, m, node)
        if k == "list":
            r = par(x(recv), P_POST)
            et = rt.args[0]
            if m == "pop":
                if not a:
                    self.use("popBack")
                    return E(f"popBack({r})")
                i = const_int(a[0])
                if i == -1:
                    self.use("popBack")
                    return E(f"popBack({r})")
                if i == 0:
                    self.info("pop0", "`a.pop(0)` działa w O(n) — dla kolejki użyj deque.", node.lineno)
                self.use("popAt")
                return E(f"popAt({r}, {x(a[0]).c})")
            if m == "index":
                return E(f"(find({r}.begin(), {r}.end(), {x(a[0], et).c}) - {r}.begin())")
            if m == "count":
                return E(f"count({r}.begin(), {r}.end(), {x(a[0], et).c})")
            if m == "copy":
                return x(recv)
            raise Unsupported(f"list.{m}() w wyrażeniu")
        if k == "dict":
            d = par(x(recv), P_POST)
            kt, vt = rt.args
            if m == "get":
                self.use("dget")
                if len(a) > 1:
                    return E(f"dget({x(recv).c}, {x(a[0], kt).c}, {x(a[1], vt).c})")
                return E(f"dget({x(recv).c}, {x(a[0], kt).c})")
            if m == "setdefault":
                dv = a[1] if len(a) > 1 else None
                if dv is None or (isinstance(dv, (ast.List, ast.Dict, ast.Set)) and
                                  not getattr(dv, "elts", getattr(dv, "keys", None))) or \
                        (isinstance(dv, ast.Constant) and dv.value in (0, "", False)):
                    return E(f"{d}[{x(a[0], kt).c}]", P_POST)
                return E(f"{d}.try_emplace({x(a[0], kt).c}, {x(dv, vt).c}).first->second", P_POST)
            if m == "keys":
                self.use("keys")
                return E(f"keys({x(recv).c})")
            if m == "values":
                self.use("values")
                return E(f"values({x(recv).c})")
            if m == "items":
                return E(f"{self.ct(List(elem(self.T(node))))}({d}.begin(), {d}.end())")
            if m == "most_common":
                self.use("mostCommon")
                self.warn("counter-order", "most_common(): przy równych licznościach Python zachowuje kolejność "
                          "wstawiania, a C++ — kolejność kluczy.", node.lineno, "info")
                return E(f"mostCommon({x(recv).c}{', ' + x(a[0]).c if a else ''})")
            if m == "copy":
                return x(recv)
            if m == "pop":
                if not self.hoist_ok:
                    raise Unsupported("dict.pop w tym miejscu")
                tmp = self.fresh("val")
                key = x(a[0], kt).c
                if len(a) > 1:
                    self.out(f"{self.ct(vt)} {tmp} = {d}.count({key}) ? {d}[{key}] : {x(a[1], vt).c};")
                else:
                    self.out(f"{self.ct(vt)} {tmp} = {d}[{key}];")
                self.out(f"{d}.erase({key});")
                return E(tmp)
            if m == "total":
                return E(f"accumulate({d}.begin(), {d}.end(), 0LL, [](long long s, const auto& kv) "
                         "{ return s + kv.second; })")
            raise Unsupported(f"dict.{m}() w wyrażeniu")
        if k == "set":
            s = par(x(recv), P_POST)
            if m in ("union", "intersection", "difference", "symmetric_difference"):
                self.use("setops")
                fn = {"union": "setUnion", "intersection": "setInter", "difference": "setDiff",
                      "symmetric_difference": "setSymDiff"}[m]
                code = x(recv).c
                for o in a:
                    oc = x(o, rt)
                    if self.T(o).k != "set":
                        oc = E(f"{self.ct(rt)}({self.rng(o)[0]}, {self.rng(o)[1]})")
                    code = f"{fn}({code}, {oc.c})"
                return E(code)
            if m in ("issubset", "issuperset"):
                self.use("isSubset")
                o = x(a[0]).c
                return E(f"isSubset({x(recv).c}, {o})" if m == "issubset" else f"isSubset({o}, {x(recv).c})")
            if m == "isdisjoint":
                self.use("setops")
                return E(f"setInter({x(recv).c}, {x(a[0]).c}).empty()", P_POST)
            if m == "copy":
                return x(recv)
            if m == "pop":
                if not self.hoist_ok:
                    raise Unsupported("set.pop w tym miejscu")
                tmp = self.fresh("val")
                self.out(f"auto {tmp} = *{s}.begin();")
                self.out(f"{s}.erase({s}.begin());")
                return E(tmp)
            raise Unsupported(f"set.{m}() w wyrażeniu")
        if k == "deque":
            dq = par(x(recv), P_POST)
            if m == "popleft":
                self.use("popFront")
                return E(f"popFront({dq})")
            if m == "pop":
                self.use("popBack")
                return E(f"popBack({dq})")
            if m == "copy":
                return x(recv)
            if m == "count":
                return E(f"count({dq}.begin(), {dq}.end(), {x(a[0]).c})")
            raise Unsupported(f"deque.{m}() w wyrażeniu")
        if k in ("int", "bool"):
            v = x(recv)
            if m == "bit_length":
                return E(f"({self.int_t})bit_width((unsigned long long){par(v, P_UNARY)})", P_UNARY)
            if m == "bit_count":
                return E(f"popcount((unsigned long long){par(v, P_UNARY)})")
        if k == "float" and m == "is_integer":
            v = x(recv)
            return E(f"({v.c} == floor({v.c}))")
        if k == "tokens":
            raise Unsupported("metoda iteratora tokenów")
        raise Unsupported(f"metoda `.{m}()` dla typu {rt!r}")

    def str_method(self, recv, rt: Ty, m: str, node) -> E:
        a = node.args
        x = self.x
        if m == "format":
            return self.parts_to_str(self.format_method_parts(node))
        if m == "join":
            return self.join_call(recv, node)
        is_char = rt.k == "char"
        # odczyt całego wejścia: sys.stdin.read().split()
        if m == "split" and not a and isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute) and \
                recv.func.attr == "read":
            self.use("readAllTokens")
            return E("readAllTokens()")
        if m == "splitlines" and isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute) and \
                recv.func.attr == "read":
            self.use("readLines", "rstrip")
            raise Unsupported("read().splitlines() — użyj sys.stdin.readlines() albo pętli po input()")
        r = x(recv, STR if not is_char else CHAR)
        rs = r if not r.cstr else E(f"string({r.c})")
        if is_char:
            c = r.c
            if m == "upper":
                return E(f"(char)toupper({c})", P_UNARY)
            if m == "lower":
                return E(f"(char)tolower({c})", P_UNARY)
            pred = {"isdigit": "isdigit", "isalpha": "isalpha", "isalnum": "isalnum", "isspace": "isspace",
                    "isupper": "isupper", "islower": "islower", "isnumeric": "isdigit", "isdecimal": "isdigit"}
            if m in pred:
                return E(f"{pred[m]}({c})")
            rs = E(f"string(1, {c})")
        if m == "split":
            if not a or (isinstance(a[0], ast.Constant) and a[0].value is None):
                if self.kw(node, "maxsplit") is not None or len(a) > 1:
                    raise Unsupported("split z maxsplit")
                self.use("split")
                return E(f"split({rs.c})")
            self.use("splitSep")
            if len(a) > 1:
                raise Unsupported("split z maxsplit")
            return E(f"split({rs.c}, {x(a[0], STR).c})")
        if m in ("strip", "lstrip", "rstrip"):
            self.use(m)
            if a and not (isinstance(a[0], ast.Constant) and a[0].value is None):
                return E(f"{m}({rs.c}, {x(a[0], STR).c})")
            return E(f"{m}({rs.c})")
        if m in ("upper", "lower", "swapcase", "title", "capitalize"):
            self.use(m)
            return E(f"{m}({rs.c})")
        if m == "replace":
            self.use("replaceAll")
            lim = f", {x(a[2]).c}" if len(a) > 2 else ""
            return E(f"replaceAll({rs.c}, {x(a[0], STR).c}, {x(a[1], STR).c}{lim})")
        if m in ("startswith", "endswith"):
            fn = "starts_with" if m == "startswith" else "ends_with"
            if isinstance(a[0], ast.Tuple):
                parts = [f"{par(rs, P_POST)}.{fn}({x(e).c})" for e in a[0].elts]
                return E(" || ".join(parts), P_OR)
            if len(a) > 1:
                raise Unsupported(f"{m} z pozycją")
            return E(f"{par(rs, P_POST)}.{fn}({x(a[0]).c})", P_POST)
        if m in ("find", "index", "rfind", "rindex"):
            fn = "find" if m in ("find", "index") else "rfind"
            extra = f", {x(a[1]).c}" if len(a) > 1 else ""
            arg = x(a[0], CHAR if self.T(a[0]).k == "char" else STR)
            return E(f"({self.int_t}){par(rs, P_POST)}.{fn}({arg.c}{extra})", P_UNARY)
        if m == "count":
            at = self.T(a[0])
            if at.k == "char" and len(a) == 1:
                if not self.lval(recv) or r.cstr:
                    if r.cstr:
                        return E(str(recv.value.count(a[0].value))) if isinstance(a[0], ast.Constant) else \
                            E(f"ranges::count({rs.c}, {x(a[0], CHAR).c})")
                    return E(f"ranges::count({rs.c}, {x(a[0], CHAR).c})")
                return E(f"count({rs.c}.begin(), {rs.c}.end(), {x(a[0], CHAR).c})")
            # bin(x).count('1') → popcount
            self.use("countSub")
            return E(f"countSub({rs.c}, {x(a[0], STR).c})")
        if m in ("isdigit", "isalpha", "isalnum", "isspace", "isnumeric", "isdecimal"):
            self.use("strPred")
            fn = {"isnumeric": "isdigit", "isdecimal": "isdigit"}.get(m, m)
            return E(f"allChars({rs.c}, ::{fn})")
        if m in ("isupper", "islower"):
            self.use("caseCheck")
            return E(f"caseCheck({rs.c}, {'true' if m == 'isupper' else 'false'})")
        if m in ("zfill",):
            self.use("pad")
            return E(f"zfill({rs.c}, {x(a[0]).c})")
        if m in ("ljust", "rjust", "center"):
            self.use("pad")
            fill = f", {x(a[1], CHAR).c}" if len(a) > 1 else ""
            return E(f"{m}({rs.c}, {x(a[0]).c}{fill})")
        if m in ("encode", "decode"):
            return rs
        if m == "removeprefix":
            p = x(a[0], STR)
            return E(f"({par(rs, P_POST)}.starts_with({p.c}) ? {par(rs, P_POST)}.substr({p.c}.size()) : {rs.c})")
        if m == "isascii":
            return E("true")
        raise Unsupported(f"str.{m}()")

    def join_call(self, recv, node) -> E:
        sep = self.x(recv, STR)
        if sep.cstr:
            sep_c = sep.c
        else:
            sep_c = sep.c
        arg = node.args[0]
        at = self.T(arg)
        self.use("join")
        # ''.join(reversed(s)) → odwrócony napis
        if isinstance(arg, ast.Call) and self.q(arg) == "reversed" and self.T(arg.args[0]).k == "str" and \
                isinstance(recv, ast.Constant) and recv.value == "" and self.lval(arg.args[0]):
            self.helpers.discard("join")
            b = par(self.x(arg.args[0]), P_POST)
            return E(f"string({b}.rbegin(), {b}.rend())")
        # sep.join(map(str, a)) → join(a, sep) (join sam zamienia elementy na napisy)
        if isinstance(arg, ast.Call) and self.q(arg) == "map" and len(arg.args) == 2 and \
                qual_of_call(self.an, arg.args[0]) == "str":
            inner = arg.args[1]
            it = self.T(inner)
            if it.k in ("list", "set", "deque", "str") and self.lval(inner):
                self._join_elem_helpers(elem(it))
                return E(f"join({self.x(inner).c}, {sep_c})")
            m = self.materialize(inner)
            self._join_elem_helpers(elem(it))
            return E(f"join({m.c}, {sep_c})")
        if at.k in ("list", "set", "deque", "str") and not isinstance(arg, (ast.ListComp, ast.GeneratorExp)):
            self._join_elem_helpers(elem(at))
            return E(f"join({self.x(arg).c}, {sep_c})")
        m = self.materialize(arg)
        self._join_elem_helpers(elem(at))
        return E(f"join({m.c}, {sep_c})")

    def _join_elem_helpers(self, et: Ty) -> None:
        if et.k in ("list", "tuple", "set", "dict", "deque", "opt"):
            self.use("reprcore")

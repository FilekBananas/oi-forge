"""Emisja wyrażeń C++ (mixin Emittera)."""
from __future__ import annotations

import ast
import re
import string as _string

from .infer import qual_of_call, short, is_stdin_read_split
from .signs import const_int
from .types import (BOOL, BOT, CHAR, FLOAT, INT, NONE, STR, Ty, Dict, List, elem, is_intlike, is_numeric, is_scalar,
                    join)

# priorytety C++ (mniejszy = wiąże mocniej)
P_PRIM, P_POST, P_UNARY, P_MUL, P_ADD, P_SHIFT, P_REL, P_EQ, P_BAND, P_BXOR, P_BOR, P_AND, P_OR, P_COND = \
    0, 2, 3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16


class E:
    __slots__ = ("c", "p", "cstr")

    def __init__(self, c: str, p: int = P_PRIM, cstr: bool = False):
        self.c = c
        self.p = p
        self.cstr = cstr

    def __repr__(self):
        return f"E({self.c!r})"


class Unsupported(Exception):
    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


def par(e: E, maxp: int) -> str:
    return e.c if e.p <= maxp else f"({e.c})"


def cstr_lit(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 32 or o == 127:
            out.append(f"\\{o:03o}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def char_lit(c: str) -> str:
    m = {"\\": "\\\\", "'": "\\'", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\0": "\\0"}
    if c in m:
        return f"'{m[c]}'"
    if ord(c) < 32 or ord(c) == 127:
        return f"'\\{ord(c):03o}'"
    return f"'{c}'"


ARITH = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%", ast.FloorDiv: "/",
         ast.LShift: "<<", ast.RShift: ">>", ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^"}
ARITH_P = {ast.Add: P_ADD, ast.Sub: P_ADD, ast.Mult: P_MUL, ast.Div: P_MUL, ast.Mod: P_MUL, ast.FloorDiv: P_MUL,
           ast.LShift: P_SHIFT, ast.RShift: P_SHIFT, ast.BitAnd: P_BAND, ast.BitOr: P_BOR, ast.BitXor: P_BXOR}
CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
STRING_CONSTS = {"string.ascii_lowercase": _string.ascii_lowercase, "string.ascii_uppercase": _string.ascii_uppercase,
                 "string.ascii_letters": _string.ascii_letters, "string.digits": _string.digits,
                 "string.hexdigits": _string.hexdigits, "string.octdigits": _string.octdigits,
                 "string.punctuation": _string.punctuation, "string.whitespace": _string.whitespace}
PCT_RE = re.compile(r"%(?:\((\w+)\))?([-+ #0]*)(\*|\d+)?(?:\.(\*|\d+))?([diouxXeEfFgGcrsa%])")


class ExprMixin:
    # ------------------------------------------------------------------ wejście
    def x(self, node, want: Ty | None = None, brace: bool = False) -> E:
        try:
            e = self._x(node, want, brace)
        except Unsupported as u:
            e = self.todo_expr(node, u.msg, want)
        except RecursionError:
            raise
        except Exception as ex:  # noqa: BLE001 — tłumacz nigdy nie przerywa pracy
            e = self.todo_expr(node, f"błąd tłumacza ({ex.__class__.__name__})", want)
        return self.coerce(e, node, want)

    def todo_expr(self, node, msg: str, want=None) -> E:
        src = self.src(node)
        self.warn("todo", f"Nieprzetłumaczone wyrażenie `{src}`: {msg}.", getattr(node, "lineno", None), "error")
        ph = "0"
        if want is not None and want.k not in ("bot", "top", "none", "int", "float", "bool", "inf"):
            ph = f"{self.ct(want, top_ok=False)}()"
        return E(f"/* Python, nie przetłumaczone: {src.replace('*/', '* /')} */ {ph}")

    def src(self, node) -> str:
        try:
            s = ast.unparse(node)
        except Exception:  # noqa: BLE001
            s = "?"
        return s if len(s) <= 80 else s[:77] + "..."

    def coerce(self, e: E, node, want: Ty | None) -> E:
        if want is None:
            return e
        t = self.T(node)
        if want.k == "str" and t.k == "char":
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return E(cstr_lit(node.value), cstr=True)
            return E(f"string(1, {e.c})")
        if want.k == "opt" and t.k == "none":
            return E("nullopt")
        if want.k == "float" and t.k == "inf" and self.inf_as_int:
            return E("INFINITY")
        return e

    def simple(self, node) -> bool:
        """Czy wyrażenie można bezpiecznie powtórzyć (brak efektów ubocznych, tanie)."""
        if isinstance(node, (ast.Name, ast.Constant)):
            return True
        if isinstance(node, ast.Attribute):
            return self.simple(node.value)
        if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            return self.simple(node.value) and self.simple(node.slice)
        if isinstance(node, ast.UnaryOp):
            return self.simple(node.operand)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            return self.simple(node.left) and self.simple(node.right)
        return False

    def _x(self, node, want, brace) -> E:
        if isinstance(node, ast.Constant):
            return self.constant(node, want)
        if isinstance(node, ast.Name):
            return self.name(node, want)
        if isinstance(node, ast.BinOp):
            return self.binop(node, want)
        if isinstance(node, ast.UnaryOp):
            return self.unaryop(node, want)
        if isinstance(node, ast.BoolOp):
            return self.boolop(node, want)
        if isinstance(node, ast.Compare):
            return self.compare(node)
        if isinstance(node, ast.Call):
            return self.call(node, want, brace)
        if isinstance(node, ast.Subscript):
            return self.subscript(node, want)
        if isinstance(node, ast.Attribute):
            return self.attribute(node, want)
        if isinstance(node, ast.IfExp):
            t = self.T(node)
            if want is not None and want.k not in ("bot", "top"):
                t = want
            c = self.cond(node.test)
            a = self.x(node.body, t)
            b = self.x(node.orelse, t)
            if a.cstr and not b.cstr and self.T(node.orelse).k == "str":
                a = E(f"string({a.c})")
            if b.cstr and not a.cstr and self.T(node.body).k == "str":
                b = E(f"string({b.c})")
            return E(f"{par(c, P_OR)} ? {par(a, P_OR)} : {par(b, P_COND)}", P_COND)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            return self.literal(node, want, brace)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return self.comp_value(node, want)
        if isinstance(node, ast.JoinedStr):
            return self.fstring_str(node)
        if isinstance(node, ast.Lambda):
            return self.lambda_expr(node)
        if isinstance(node, ast.NamedExpr):
            tgt = self.x(node.target)
            v = self.x(node.value, self.T(node.target))
            return E(f"({tgt.c} = {v.c})")
        if isinstance(node, ast.Starred):
            raise Unsupported("rozpakowanie `*` w tym miejscu")
        raise Unsupported(f"konstrukcja {node.__class__.__name__}")

    # ------------------------------------------------------------------ literały i nazwy
    def int_lit(self, v: int, force_ll: bool = False) -> E:
        if abs(v) > 2 ** 63 - 1:
            self.warn("big-int", f"Liczba {v} nie mieści się w 64 bitach (long long). Python ma liczby "
                      "dowolnej wielkości — w C++ potrzebna arytmetyka modularna albo __int128.", None, "error")
            return E(f"/* {v} — poza zakresem long long */ 0")
        suf = ""
        if self.int_t == "long long" and (force_ll or not (-2 ** 31 <= v < 2 ** 31)):
            suf = "LL"
        if self.int_t == "int" and not (-2 ** 31 <= v < 2 ** 31):
            suf = "LL"
            self.warn("int-range", f"Liczba {v} nie mieści się w `int` (32 bity) — przy tej stałej wybierz typ "
                      "`long long`.", None, "error")
        if v < 0:
            return E(f"-{-v}{suf}", P_UNARY)
        return E(f"{v}{suf}")

    def constant(self, node, want) -> E:
        v = node.value
        if isinstance(v, bool):
            return E("true" if v else "false")
        if isinstance(v, int):
            return self.int_lit(v)
        if isinstance(v, float):
            if v != v:
                return E("NAN")
            if v in (float("inf"), float("-inf")):
                return E("INFINITY" if v > 0 else "-INFINITY", P_PRIM if v > 0 else P_UNARY)
            r = repr(v)
            if "e" not in r and "." not in r:
                r += ".0"
            return E(r)
        if isinstance(v, (str, bytes)):
            s = v.decode("latin-1") if isinstance(v, bytes) else v
            if len(s) == 1 and s.isascii() and (want is None or want.k != "str") and \
                    (want is not None and want.k == "char" or self.T(node).k == "char") and \
                    (want is None or want.k in ("char", "bot", "int")):
                return E(char_lit(s))
            return E(cstr_lit(s), cstr=True)
        if v is None:
            if want is not None and want.k not in ("opt", "none", "bot"):
                return E("{}")
            return E("nullopt")
        if v is Ellipsis:
            raise Unsupported("`...`")
        raise Unsupported(f"stała {v!r}")

    def name(self, node, want) -> E:
        sym = self.S(node)
        if sym is None:
            return E(node.id)
        if sym.kind == "builtin":
            if node.id in ("True", "False"):
                return E(node.id.lower())
            if node.id == "None":
                return E("nullopt")
            if node.id in ("int", "str", "float", "len", "abs"):
                return E(node.id)
            raise Unsupported(f"wbudowana nazwa `{node.id}` jako wartość")
        if sym in self.rename:
            return E(self.rename[sym])
        if self.cls_ctx is not None and self.fi is not None and self.fi.self_sym is not None and \
                sym is self.fi.self_sym:
            return E("*this", P_UNARY)
        if sym.kind == "import":
            q = sym.qual
            if q in STRING_CONSTS:
                return E(cstr_lit(STRING_CONSTS[q]), cstr=True)
            if q == "math.pi":
                return E("numbers::pi")
            if q == "math.e":
                return E("numbers::e")
            if q == "math.inf":
                return self.inf_value(want, node)
            return E(self.cn(sym))
        if sym.kind == "func" and sym.node is not None:
            fi = self.inf.funcs.get(id(sym.node))
            if fi is not None and fi.fid in self.func_cname:
                return E(self.func_cname[fi.fid])
        code = self.cn(sym)
        t = self.sym_type(sym)
        if t.k == "opt" and want is not None and want.k not in ("opt", "none", "bot", "top"):
            return E(f"*{code}", P_UNARY)
        return E(code)

    def inf_value(self, want, node=None, neg=False) -> E:
        as_int = self.inf_as_int and not (want is not None and want.k == "float")
        if want is not None and want.k in ("int", "bool"):
            as_int = True
        if as_int:
            self.need_inf_const = True
            self.info("inf-int", "`float('inf')` użyte z liczbami całkowitymi → duża stała całkowita "
                      "(LLONG_MAX / 4); wypisanie jej da liczbę zamiast `inf`.", getattr(node, "lineno", None))
            return E(f"-{self.inf_name}" if neg else self.inf_name, P_UNARY if neg else P_PRIM)
        return E("-INFINITY" if neg else "INFINITY", P_UNARY if neg else P_PRIM)

    # ------------------------------------------------------------------ operatory
    def folded(self, node) -> E | None:
        v = const_int(node)
        if v is None:
            return None
        has_pow = any(isinstance(s, ast.BinOp) and isinstance(s.op, (ast.Pow,)) for s in ast.walk(node))
        if has_pow or not (-2 ** 31 <= v < 2 ** 31):
            return self.int_lit(v)
        return None

    def arith(self, op: str, prec: int, l: E, r: E, bitwise: bool = False) -> E:
        if bitwise:
            return E(f"{par(l, P_UNARY if l.p != prec else prec)} {op} {par(r, P_UNARY)}", prec)
        if prec == P_SHIFT:
            return E(f"{par(l, P_UNARY if l.p != prec else prec)} {op} {par(r, P_UNARY)}", prec)
        return E(f"{par(l, prec)} {op} {par(r, prec - 1)}", prec)

    def operand(self, node, want=None) -> E:
        """Operand arytmetyki: ord(c) w + i - zostaje jako znak (C++ promuje char do int)."""
        if isinstance(node, ast.Call) and self.q(node) == "ord" and len(node.args) == 1 and \
                self.T(node.args[0]).k == "char":
            return self.x(node.args[0], CHAR)
        t = self.T(node)
        if t.k == "opt" and want is None and t.args and t.args[0].k not in ("bot", "none"):
            return self.x(node, t.args[0])
        return self.x(node, want)

    def binop(self, node, want) -> E:
        f = self.folded(node)
        if f is not None:
            return f
        op = node.op
        L, R = node.left, node.right
        lt, rt, t = self.T(L), self.T(R), self.T(node)
        if isinstance(op, ast.Add):
            if t.k == "str" or (lt.k in ("str", "char") and rt.k in ("str", "char")):
                return self.str_concat(node)
            if t.k == "list" or lt.k == "list":
                self.use("concat")
                return E(f"concat({self.x(L, t).c}, {self.x(R, t).c})")
            if t.k == "tuple" and lt.k == "tuple" and rt.k == "tuple":
                return E(f"tuple_cat({self.x(L).c}, {self.x(R).c})")
            return self.arith("+", P_ADD, self.operand(L), self.operand(R))
        if isinstance(op, ast.Sub):
            if lt.k == "set":
                self.use("setops")
                return E(f"setDiff({self.x(L).c}, {self.x(R).c})")
            return self.arith("-", P_ADD, self.operand(L), self.operand(R))
        if isinstance(op, ast.Mult):
            if t.k == "str" or (lt.k in ("str", "char") and is_intlike(rt)) or (rt.k in ("str", "char") and
                                                                                 is_intlike(lt)):
                s, n = (L, R) if lt.k in ("str", "char") else (R, L)
                if self.T(s).k == "char":
                    return E(f"string({self.x(n).c}, {self.x(s, CHAR).c})")
                self.use("repeatStr")
                return E(f"repeatStr({self.x(s, STR).c}, {self.x(n).c})")
            if lt.k in ("list", "tuple") or rt.k in ("list", "tuple"):
                s, n = (L, R) if lt.k in ("list", "tuple") else (R, L)
                et = elem(t)
                if isinstance(s, (ast.List, ast.Tuple)) and len(s.elts) == 1 and \
                        not isinstance(s.elts[0], ast.Starred):
                    return E(f"vector<{self.ct_elem(et)}>({self.x(n).c}, {self.x(s.elts[0], et).c})")
                self.use("repeatVec")
                return E(f"repeatVec({self.x(s, List(et)).c}, {self.x(n).c})")
            return self.arith("*", P_MUL, self.operand(L), self.operand(R))
        if isinstance(op, ast.Div):
            l, r = self.operand(L), self.operand(R)
            if lt.k in ("float",) or rt.k in ("float",):
                return self.arith("/", P_MUL, l, r)
            return E(f"(double){par(l, P_UNARY)} / {par(r, P_MUL - 1)}", P_MUL)
        if isinstance(op, ast.FloorDiv):
            if lt.k == "float" or rt.k == "float":
                return E(f"floor({self.arith('/', P_MUL, self.x(L), self.x(R)).c})")
            l, r = self.operand(L), self.operand(R)
            if self.signs.nonneg(L) and self.signs.nonneg(R):
                return self.arith("/", P_MUL, l, r)
            self.use("floordiv")
            self.info("floordiv", "`//` z możliwą liczbą ujemną → `floordiv()` (w C++ `/` zaokrągla do zera, "
                      "w Pythonie w dół).", node.lineno)
            return E(f"floordiv({l.c}, {r.c})")
        if isinstance(op, ast.Mod):
            if lt.k == "str":
                return self.percent_format(node)
            if lt.k == "float" or rt.k == "float":
                self.use("fpymod")
                return E(f"fpymod({self.x(L).c}, {self.x(R).c})")
            l, r = self.operand(L), self.operand(R)
            if (self.signs.nonneg(L) and self.signs.nonneg(R)) or self._mod_cmp_zero(node):
                return self.arith("%", P_MUL, l, r)
            self.use("pymod")
            self.info("pymod", "`%` z możliwą liczbą ujemną → `pymod()` (w Pythonie wynik ma znak dzielnika).",
                      node.lineno)
            return E(f"pymod({l.c}, {r.c})")
        if isinstance(op, ast.Pow):
            return self.power(L, R, node)
        if isinstance(op, ast.LShift):
            l = self.x(L)
            if isinstance(L, ast.Constant) and isinstance(L.value, int) and self.int_t == "long long":
                l = E(f"{L.value}LL")
            return self.arith("<<", P_SHIFT, l, self.x(R))
        if isinstance(op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            if lt.k == "set" and rt.k == "set":
                self.use("setops")
                fn = {ast.BitAnd: "setInter", ast.BitOr: "setUnion", ast.BitXor: "setSymDiff"}[type(op)]
                return E(f"{fn}({self.x(L).c}, {self.x(R).c})")
            if lt.k == "dict" and isinstance(op, ast.BitOr):
                raise Unsupported("łączenie słowników `|`")
            return self.arith(ARITH[type(op)], ARITH_P[type(op)], self.x(L), self.x(R), bitwise=True)
        if isinstance(op, ast.RShift):
            return self.arith(">>", P_SHIFT, self.x(L), self.x(R))
        raise Unsupported(f"operator {op.__class__.__name__}")

    def _mod_cmp_zero(self, node) -> bool:
        """`x % m == 0` / `!= 0` / `if x % m:` — znak nie ma znaczenia."""
        p = self._parent(node)
        if isinstance(p, ast.Compare) and len(p.ops) == 1 and isinstance(p.ops[0], (ast.Eq, ast.NotEq)):
            other = p.comparators[0] if p.left is node else p.left
            return const_int(other) == 0
        if isinstance(p, (ast.If, ast.While, ast.IfExp)) and p.test is node:
            return True
        if isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Not):
            return True
        if isinstance(p, ast.BoolOp):
            pp = self._parent(p)
            return isinstance(pp, (ast.If, ast.While)) and pp.test is p
        return False

    def power(self, L, R, node) -> E:
        lt, rt = self.T(L), self.T(R)
        c = const_int(R)
        if is_intlike(lt) and is_intlike(rt) and (c is None or c >= 0):
            l = self.x(L)
            if c == 0:
                return E("1")
            if c == 1:
                return l
            if c == 2 and self.simple(L):
                return E(f"{par(l, P_MUL)} * {par(l, P_MUL - 1)}", P_MUL)
            if c == 3 and self.simple(L):
                return E(f"{par(l, P_MUL)} * {par(l, P_MUL - 1)} * {par(l, P_MUL - 1)}", P_MUL)
            bv = const_int(L)
            if bv is not None and bv == 2:
                self.info("pow2", "`2 ** k` → `1LL << k`.", node.lineno)
                return self.arith("<<", P_SHIFT, E("1LL" if self.int_t == "long long" else "1"), self.x(R))
            self.use("ipow")
            if not self._under_mod(node):
                self.warn("overflow", "Potęga `**` na liczbach całkowitych może przekroczyć 64 bity (w Pythonie "
                          "liczby są nieograniczone). Jeśli liczysz modulo — użyj pow(a, b, m).", node.lineno)
            return E(f"ipow({l.c}, {self.x(R).c})")
        if isinstance(R, ast.Constant) and R.value == 0.5:
            return E(f"sqrt({self.x(L).c})")
        l = self.x(L)
        if is_intlike(lt):
            l = E(f"(double){par(l, P_UNARY)}", P_UNARY)
        return E(f"pow({l.c}, {self.x(R).c})")

    def _under_mod(self, node) -> bool:
        p = self._parent(node)
        while p is not None and isinstance(p, (ast.BinOp, ast.Call)):
            if isinstance(p, ast.BinOp) and isinstance(p.op, ast.Mod) and p.left is not node:
                return True
            if isinstance(p, ast.BinOp) and isinstance(p.op, ast.Mod):
                return True
            node, p = p, self._parent(p)
        return False

    def unaryop(self, node, want) -> E:
        op = node.op
        if isinstance(op, ast.Not):
            return E(f"!{par(self.cond(node.operand), P_UNARY)}", P_UNARY)
        if isinstance(op, ast.USub):
            if self.T(node.operand).k == "inf":
                inner = node.operand
                if isinstance(inner, ast.Call) or (isinstance(inner, ast.Attribute)):
                    return self.inf_value(want, node, neg=True)
            v = const_int(node)
            if v is not None:
                return self.int_lit(v)
            o = self.operand(node.operand)
            if o.c.startswith("-"):
                return E(f"-({o.c})", P_UNARY)
            return E(f"-{par(o, P_UNARY)}", P_UNARY)
        if isinstance(op, ast.UAdd):
            return self.x(node.operand)
        if isinstance(op, ast.Invert):
            return E(f"~{par(self.x(node.operand), P_UNARY)}", P_UNARY)
        raise Unsupported("operator jednoargumentowy")

    def boolop(self, node, want) -> E:
        t = self.T(node)
        is_and = isinstance(node.op, ast.And)
        if t.k in ("bool", "bot", "top") or (want is not None and want.k == "bool") or self._in_cond(node):
            parts = []
            for v in node.values:
                c = self.cond(v)
                if isinstance(v, ast.BoolOp) and not isinstance(v.op, type(node.op)):
                    parts.append(f"({c.c})")
                else:
                    parts.append(par(c, P_AND if is_and else P_OR))
            return E((" && " if is_and else " || ").join(parts), P_AND if is_and else P_OR)
        # wartość: `a or b` → a, jeśli a jest „prawdziwe”, w przeciwnym razie b (b liczone tylko wtedy)
        vals = node.values
        res = self.x(vals[-1], t)
        if res.cstr and t.k == "str":
            res = E(f"string({res.c})")
        for v in reversed(vals[:-1]):
            xv = self.x(v, t)
            if xv.cstr and t.k == "str":
                xv = E(f"string({xv.c})")
            if not self.simple(v):
                if not self.hoist_ok:
                    raise Unsupported("`and`/`or` zwracające wartość w tym miejscu")
                tmp = self.fresh("val")
                self.out(f"{self.ct(t)} {tmp} = {xv.c};")
                xv = E(tmp)
            cv = self.truthy_code(xv, self.T(v) if self.simple(v) else t)
            if is_and:
                res = E(f"{par(cv, P_OR)} ? {par(res, P_OR)} : {par(xv, P_COND)}", P_COND)
            else:
                res = E(f"{par(cv, P_OR)} ? {par(xv, P_OR)} : {par(res, P_COND)}", P_COND)
        return res

    def truthy_code(self, e: E, t: Ty) -> E:
        if t.k in ("str", "list", "set", "dict", "deque"):
            return E(f"!{par(e, P_POST)}.empty()", P_UNARY)
        if t.k == "opt":
            return E(f"{par(e, P_POST)}.has_value()", P_POST)
        if t.k == "char":
            return E("true")
        return e

    def _in_cond(self, node) -> bool:
        p = self._parent(node)
        while isinstance(p, (ast.BoolOp, ast.UnaryOp)):
            node, p = p, self._parent(p)
        return isinstance(p, (ast.If, ast.While, ast.IfExp, ast.Assert)) and getattr(p, "test", None) is node or \
            isinstance(p, ast.comprehension)

    def cond(self, node) -> E:
        """Wyrażenie w kontekście logicznym (if/while/not/and/or) — prawdziwość jak w Pythonie."""
        t = self.T(node)
        if isinstance(node, (ast.Compare, ast.BoolOp)) or (isinstance(node, ast.UnaryOp) and
                                                            isinstance(node.op, ast.Not)):
            return self.x(node, BOOL)
        if isinstance(node, ast.Name) and self.is_pure_heap(self.S(node)):
            return E(f"!{self.cn(self.S(node))}.empty()", P_UNARY)
        k = t.k
        if k in ("bool", "int", "float", "inf", "bot"):
            return self.x(node)
        if k in ("str", "list", "set", "dict", "deque"):
            e = self.x(node)
            return E(f"!{par(e, P_POST)}.empty()", P_UNARY)
        if k == "char":
            return E("true")
        if k == "opt":
            e = self.x(node, t)
            inner = t.args[0]
            if inner.k in ("int", "float", "bool"):
                return E(f"({e.c}.has_value() && *{par(e, P_UNARY)})", P_PRIM)
            return E(f"{par(e, P_POST)}.has_value()", P_POST)
        if k == "none":
            return E("false")
        if k == "tuple":
            return E("true")
        if k == "lazy":
            e = self.x(node)
            return E(f"!{par(e, P_POST)}.empty()", P_UNARY)
        return self.x(node)

    # ------------------------------------------------------------------ porównania
    def compare(self, node) -> E:
        parts = []
        left = node.left
        for op, right in zip(node.ops, node.comparators):
            parts.append(self.cmp1(left, op, right))
            left = right
        if len(parts) == 1:
            return parts[0]
        for i, (a, b) in enumerate(zip(node.comparators[:-1], node.comparators[1:])):
            pass
        if any(not self.simple(c) for c in node.comparators[:-1]):
            self.info("chain-cmp", "Łańcuchowe porównanie `a < f() < c`: środkowe wyrażenie jest liczone dwa razy.",
                      node.lineno)
        return E(" && ".join(par(p, P_EQ) for p in parts), P_AND)

    def is_none(self, node) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    def cmp1(self, l, op, r) -> E:
        lt, rt = self.T(l), self.T(r)
        if isinstance(op, (ast.In, ast.NotIn)):
            return self.membership(l, r, isinstance(op, ast.NotIn))
        if isinstance(op, (ast.Is, ast.IsNot)) or ((self.is_none(r) or self.is_none(l)) and
                                                    isinstance(op, (ast.Eq, ast.NotEq))):
            neg = isinstance(op, (ast.IsNot, ast.NotEq))
            if self.is_none(r) or self.is_none(l):
                other = l if self.is_none(r) else r
                ot = self.T(other)
                if ot.k == "opt":
                    e = self.x(other, ot)
                    return E(f"{par(e, P_POST)}.has_value()", P_POST) if neg else E(f"!{par(e, P_POST)}.has_value()",
                                                                                    P_UNARY)
                if ot.k == "none":
                    return E("false" if neg else "true")
                return E("true" if neg else "false")
            op = ast.NotEq() if neg else ast.Eq()
        sym = CMP[type(op)]
        prec = P_EQ if sym in ("==", "!=") else P_REL
        wl, wr = None, None
        if lt.k == "char" and rt.k == "str" and not isinstance(r, ast.Constant):
            wl = STR
        if rt.k == "char" and lt.k == "str" and not isinstance(l, ast.Constant):
            wr = STR
        if isinstance(r, ast.Constant) and isinstance(r.value, str):
            wr = CHAR if lt.k == "char" and len(r.value) == 1 else STR
        if isinstance(l, ast.Constant) and isinstance(l.value, str):
            wl = CHAR if rt.k == "char" and len(l.value) == 1 else STR
        if isinstance(r, (ast.Tuple, ast.List)) and lt.k in ("tuple", "list"):
            wr = lt
        if isinstance(l, (ast.Tuple, ast.List)) and rt.k in ("tuple", "list"):
            wl = rt
        if rt.k == "inf" or lt.k == "inf":
            if lt.k == "float" or rt.k == "float":
                wl = wl or (FLOAT if lt.k == "inf" else None)
                wr = wr or (FLOAT if rt.k == "inf" else None)
        le = self.operand(l, wl) if wl is None else self.x(l, wl)
        re_ = self.operand(r, wr) if wr is None else self.x(r, wr)
        if le.cstr and re_.cstr:
            le = E(f"string({le.c})")
        return E(f"{par(le, prec)} {sym} {par(re_, prec - 1)}", prec)

    def membership(self, item, cont, neg: bool) -> E:
        ct = self.T(cont)
        it = self.T(item)
        if isinstance(cont, (ast.Tuple, ast.List, ast.Set)) and cont.elts and len(cont.elts) <= 8 and \
                self.simple(item) and not any(isinstance(e, ast.Starred) for e in cont.elts):
            et = elem(ct)
            want = et if et.k != "bot" else it
            if it.k == "char" and et.k in ("char",):
                want = CHAR
            xi = self.x(item)
            parts = [f"{par(xi, P_EQ)} {'!=' if neg else '=='} {par(self.x(e, want), P_EQ - 1)}" for e in cont.elts]
            return E((" && " if neg else " || ").join(parts), P_AND if neg else P_OR)
        if isinstance(cont, ast.Call) and self.q(cont) == "range" and 1 <= len(cont.args) <= 2:
            xi = self.x(item)
            if not self.simple(item):
                raise Unsupported("`x in range(...)` ze złożonym x")
            lo = self.x(cont.args[0]) if len(cont.args) == 2 else E("0")
            hi = self.x(cont.args[-1])
            e = E(f"{par(lo, P_REL)} <= {par(xi, P_REL - 1)} && {par(xi, P_REL)} < {par(hi, P_REL - 1)}", P_AND)
            return E(f"!({e.c})", P_UNARY) if neg else e
        if isinstance(cont, ast.Call) and isinstance(cont.func, ast.Attribute) and cont.func.attr in ("keys",) and \
                self.T(cont.func.value).k == "dict":
            cont = cont.func.value
            ct = self.T(cont)
        k = ct.k
        if k in ("set", "dict"):
            e = self.x(cont)
            kt = ct.args[0]
            xi = self.x(item, kt)
            code = f"{par(e, P_POST)}.contains({xi.c})"
            return E(f"!{code}", P_UNARY) if neg else E(code, P_POST)
        if k in ("str", "char"):
            e = self.x(cont, STR)
            if e.cstr:
                e = E(f"string({e.c})")
            xi = self.x(item, CHAR if it.k == "char" else STR)
            return E(f"{par(e, P_POST)}.find({xi.c}) {'==' if neg else '!='} string::npos", P_EQ)
        if isinstance(cont, ast.Call) and isinstance(cont.func, ast.Attribute) and cont.func.attr == "values":
            d = self.x(cont.func.value)
            xi = self.x(item)
            code = f"any_of({d.c}.begin(), {d.c}.end(), [&](const auto& kv) {{ return kv.second == {xi.c}; }})"
            return E(f"!{code}", P_UNARY) if neg else E(code)
        if k in ("list", "deque", "tuple", "lazy"):
            et = elem(ct)
            xi = self.x(item, et if et.k not in ("bot", "top") else None)
            if isinstance(cont, ast.Name) or (isinstance(cont, ast.Attribute) and self.simple(cont)) or (
                    isinstance(cont, ast.Subscript) and self.simple(cont)):
                e = self.x(cont)
                if ct.k == "tuple":
                    raise Unsupported("`in` dla krotki-zmiennej")
                code = f"find({e.c}.begin(), {e.c}.end(), {xi.c}) {'==' if neg else '!='} {e.c}.end()"
                self.info("list-in", "`x in lista` to wyszukiwanie liniowe O(n) — dla wielu zapytań użyj set.",
                          getattr(item, "lineno", None))
                return E(code, P_EQ)
            e = self.x(cont)
            code = f"ranges::count({e.c}, {xi.c}) {'==' if neg else '>'} 0"
            return E(code, P_EQ if neg else P_REL)
        raise Unsupported(f"operator `in` dla typu {ct!r}")

    # ------------------------------------------------------------------ łączenie napisów
    def str_concat(self, node) -> E:
        parts = []

        def flat(n):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and self.T(n).k == "str":
                flat(n.left)
                flat(n.right)
            else:
                parts.append(n)
        flat(node)
        codes = []
        for i, p in enumerate(parts):
            pt = self.T(p)
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                codes.append(E(cstr_lit(p.value), cstr=True))
            elif pt.k == "char":
                if i == 0 or (i == 1 and codes and codes[0].cstr):
                    codes.append(E(f"string(1, {self.x(p, CHAR).c})"))
                else:
                    codes.append(self.x(p, CHAR))
            else:
                codes.append(self.x(p, STR))
        # łączenie sąsiednich literałów
        merged = []
        for i, (p, c) in enumerate(zip(parts, codes)):
            if merged and c.cstr and merged[-1][1].cstr:
                prev_node = merged[-1][0]
                merged[-1] = (None, E(merged[-1][1].c[:-1] + c.c[1:], cstr=True))
                del prev_node
            else:
                merged.append((p, c))
        codes = [c for _, c in merged]
        if len(codes) == 1:
            return codes[0] if not codes[0].cstr else E(f"string({codes[0].c})")
        if codes[0].cstr and (codes[1].cstr or self._is_char_code(codes[1])):
            codes[0] = E(f"string({codes[0].c})")
        out = par(codes[0], P_ADD)
        for c in codes[1:]:
            out += " + " + par(c, P_ADD - 1)
        return E(out, P_ADD)

    @staticmethod
    def _is_char_code(e: E) -> bool:
        return e.c.startswith("'") or e.c.startswith("(char)")

    # ------------------------------------------------------------------ literały kontenerów
    def literal(self, node, want, brace) -> E:
        t = self.T(node)
        if want is not None and want.k == node_kind(node) and want.k != "bot":
            t = want
        if isinstance(node, ast.Tuple) and t.k == "list":
            pass
        if isinstance(node, (ast.List, ast.Set)) or (isinstance(node, ast.Tuple) and t.k == "list"):
            et = elem(t) if t.k in ("list", "set", "tuple") else BOT
            if any(isinstance(e, ast.Starred) for e in node.elts):
                return self.starred_list(node, t)
            items = ", ".join(self.x(e, et, brace=True).c for e in node.elts)
            if brace:
                return E("{" + items + "}")
            return E(f"{self.ct(t if t.k != 'tuple' else List(et))}{{{items}}}")
        if isinstance(node, ast.Tuple):
            ts = t.args if t.k == "tuple" else tuple(self.T(e) for e in node.elts)
            items = [self.x(e, et, brace=True) for e, et in zip(node.elts, ts)]
            if brace:
                return E("{" + ", ".join(i.c for i in items) + "}")
            if want is not None and want.k == "tuple":
                return E(f"{self.ct(t)}({', '.join(i.c for i in items)})")
            fn = "make_pair" if len(items) == 2 else "make_tuple"
            fixed = []
            for e, i in zip(node.elts, items):
                if isinstance(e, ast.Constant) and isinstance(e.value, int) and not isinstance(e.value, bool) and \
                        self.int_t == "long long":
                    fixed.append(self.int_lit(e.value, force_ll=True).c)
                elif i.cstr:
                    fixed.append(f"string({i.c})")
                else:
                    fixed.append(i.c)
            return E(f"{fn}({', '.join(fixed)})")
        if isinstance(node, ast.Dict):
            kt, vt = (t.args if t.k == "dict" else (BOT, BOT))
            if any(k is None for k in node.keys):
                raise Unsupported("rozpakowanie `**` w słowniku")
            items = ", ".join("{" + self.x(k, kt, brace=True).c + ", " + self.x(v, vt, brace=True).c + "}"
                              for k, v in zip(node.keys, node.values))
            if brace:
                return E("{" + items + "}")
            return E(f"{self.ct(t)}{{{items}}}")
        raise Unsupported("literał")

    def starred_list(self, node, t) -> E:
        et = elem(t)
        if not self.hoist_ok:
            raise Unsupported("lista z `*` w tym miejscu")
        name = self.fresh("lst")
        self.out(f"{self.ct(List(et))} {name};")
        for e in node.elts:
            if isinstance(e, ast.Starred):
                src = self.x(e.value)
                if self.T(e.value).k in ("list", "str", "deque", "set") and self.simple(e.value):
                    self.out(f"{name}.insert({name}.end(), {src.c}.begin(), {src.c}.end());")
                else:
                    v = self.fresh("x")
                    self.out(f"for (const auto& {v} : {self.iterable_code(e.value)}) {name}.push_back({v});")
            else:
                self.out(f"{name}.push_back({self.x(e, et).c});")
        return E(name)

    # ------------------------------------------------------------------ lambda
    def lambda_expr(self, node, param_types=None) -> E:
        fi = self.inf.funcs[id(node)]
        params = []
        for i, sym in enumerate(fi.params):
            t = self.sym_type(sym)
            if param_types and i < len(param_types) and param_types[i] is not None:
                t = param_types[i]
            if is_scalar(t) and t.k != "bot":
                params.append(f"{self.ct(t)} {self.cn(sym)}")
            else:
                params.append(f"const auto& {self.cn(sym)}")
        saved = self.hoist_ok
        self.hoist_ok = False
        try:
            body = self.x(node.body, fi.ret if fi.ret.k not in ("bot", "top", "none") else None)
        finally:
            self.hoist_ok = saved
        return E(f"[&]({', '.join(params)}) {{ return {body.c}; }}")

    # ------------------------------------------------------------------ indeksowanie
    def subscript(self, node, want) -> E:
        base = node.value
        bt = self.T(base)
        sl = node.slice
        if isinstance(sl, ast.Slice):
            return self.slice_expr(base, bt, sl)
        if isinstance(base, ast.Name) and self.is_pure_heap(self.S(base)) and const_int(sl) == 0:
            return E(f"{self.cn(self.S(base))}.top()", P_POST)
        if bt.k == "tuple":
            i = const_int(sl)
            if i is None:
                raise Unsupported("indeks krotki musi być stałą")
            n = len(bt.args)
            if i < 0:
                i += n
            b = self.x(base)
            if n == 2 and i in (0, 1):
                return E(f"{par(b, P_POST)}.{'first' if i == 0 else 'second'}", P_POST)
            return E(f"get<{i}>({b.c})")
        b = self.x(base)
        if bt.k == "dict":
            kt = bt.args[0]
            k = self.x(sl, kt)
            if isinstance(base, ast.Name) and self.S(base) in self.const_params:
                return E(f"{par(b, P_POST)}.at({k.c})", P_POST)
            return E(f"{par(b, P_POST)}[{k.c}]", P_POST)
        if b.cstr:
            b = E(f"string({b.c})")
        i = const_int(sl)
        if i is not None and i < 0 and bt.k in ("list", "str", "deque"):
            if i == -1:
                return E(f"{par(b, P_POST)}.back()", P_POST)
            if self.simple(base):
                return E(f"{par(b, P_POST)}[{par(b, P_POST)}.size() - {-i}]", P_POST)
            raise Unsupported("ujemny indeks złożonego wyrażenia")
        idx = self.x(sl)
        if isinstance(sl, ast.UnaryOp) and isinstance(sl.op, ast.USub) and self.simple(base):
            inner = self.x(sl.operand)
            return E(f"{par(b, P_POST)}[{par(b, P_POST)}.size() - {par(inner, P_ADD - 1)}]", P_POST)
        if bt.k == "lazy":
            raise Unsupported("indeksowanie iteratora (użyj list(...))")
        return E(f"{par(b, P_POST)}[{idx.c}]", P_POST)

    def slice_expr(self, base, bt, sl) -> E:
        b = self.x(base)
        if b.cstr:
            b = E(f"string({b.c})")
        lo, hi, st = sl.lower, sl.upper, sl.step
        stv = const_int(st) if st is not None else 1
        cont = self.ct(bt) if bt.k in ("list", "str") else ("string" if bt.k == "char" else self.ct(List(elem(bt))))
        if lo is None and hi is None and stv == -1:
            if self.simple(base):
                return E(f"{cont}({par(b, P_POST)}.rbegin(), {par(b, P_POST)}.rend())")
        if lo is None and hi is None and st is None:
            return b
        self.use("slice")
        args = [b.c, self.x(lo).c if lo is not None else "NO_IDX"]
        if hi is not None or st is not None:
            args.append(self.x(hi).c if hi is not None else "NO_IDX")
        if st is not None:
            args.append(self.x(st).c)
        if args[1] == "NO_IDX" and len(args) >= 3 and (st is None or (stv is not None and stv > 0)):
            args[1] = "0"
        if bt.k == "tuple":
            raise Unsupported("wycinek krotki")
        return E(f"pySlice({', '.join(args)})")

    # ------------------------------------------------------------------ atrybuty
    def attribute(self, node, want) -> E:
        q = qual_of_call(self.an, node)
        if q in STRING_CONSTS:
            return E(cstr_lit(STRING_CONSTS[q]), cstr=True)
        if q == "math.pi":
            return E("numbers::pi")
        if q == "math.e":
            return E("numbers::e")
        if q == "math.tau":
            return E("2 * numbers::pi", P_MUL)
        if q == "math.inf":
            return self.inf_value(want, node)
        if q == "math.nan":
            return E("NAN")
        if q == "sys.maxsize":
            return E("LLONG_MAX")
        bt = self.T(node.value)
        if bt.k == "class":
            fname = self.field_cname(node.attr)
            if self.cls_ctx is not None and isinstance(node.value, ast.Name) and self.fi is not None and \
                    self.fi.self_sym is not None and self.an.sym_of.get(id(node.value)) is self.fi.self_sym:
                if self._shadowed_in_method(node.attr):
                    return E(f"this->{fname}", P_POST)
                return E(fname)
            b = self.x(node.value)
            return E(f"{par(b, P_POST)}.{fname}", P_POST)
        if node.attr in ("real",) and bt.k in ("int", "float"):
            return self.x(node.value)
        raise Unsupported(f"atrybut `.{node.attr}`")

    def _shadowed_in_method(self, attr: str) -> bool:
        if self.fi is None:
            return False
        return attr in self.fi.scope.bound and attr != "self"

    # ------------------------------------------------------------------ pomocnicze dla wywołań
    def q(self, call) -> str | None:
        if isinstance(call, ast.Call):
            return qual_of_call(self.an, call.func)
        return None

    def kw(self, node, name):
        for k in node.keywords:
            if k.arg == name:
                return k.value
        return None

    def lval(self, node) -> bool:
        return isinstance(node, (ast.Name, ast.Attribute)) or (isinstance(node, ast.Subscript) and
                                                               not isinstance(node.slice, ast.Slice))

    def rng(self, node) -> tuple[str, str]:
        """(begin, end) dla kontenera-l-wartości."""
        e = self.x(node)
        if self.T(node).k == "str" and e.cstr:
            e = E(f"string({e.c})")
        c = par(e, P_POST)
        return f"{c}.begin()", f"{c}.end()"

    def range_pair(self, node) -> tuple[str, str]:
        """(begin, end) dla dowolnego iterowalnego; obiekt tymczasowy trafia do zmiennej (jeden obiekt!)."""
        t = self.T(node)
        if self.lval(node) and t.k in ("list", "str", "set", "deque", "dict"):
            return self.rng(node)
        m = self.x(node) if t.k in ("set", "dict", "deque", "str", "list") and not isinstance(
            node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)) else self.materialize(node)
        c = m.c if not m.cstr else f"string({m.c})"
        if not c.isidentifier():
            if not self.hoist_ok:
                raise Unsupported("tymczasowy kontener w tym miejscu")
            tmp = self.fresh("tmp")
            self.out(f"auto {tmp} = {c};")
            c = tmp
        return f"{c}.begin()", f"{c}.end()"

    def len_expr(self, arg, raw=False) -> E:
        t = self.T(arg)
        cast = f"({self.int_t})"
        if isinstance(arg, ast.Name) and self.is_pure_heap(self.S(arg)):
            sz = f"{self.cn(self.S(arg))}.size()"
            return E(sz, P_POST) if raw else E(f"{cast}{sz}", P_UNARY)
        if isinstance(arg, ast.Call):
            q = self.q(arg)
            if q == "range":
                a = arg.args
                if len(a) == 1:
                    return self.x(a[0])
                if len(a) == 2:
                    return E(f"max<{self.int_t}>(0, {self.x(a[1]).c} - {par(self.x(a[0]), P_ADD - 1)})")
            if q in ("set", "frozenset") and len(arg.args) == 1 and self.lval(arg.args[0]):
                st = self.T(arg)
                b, e = self.rng(arg.args[0])
                code = f"{self.ct(st)}({b}, {e}).size()"
                return E(code, P_POST) if raw else E(f"{cast}{code}", P_UNARY)
            if q == "str" and len(arg.args) == 1 and is_intlike(self.T(arg.args[0])):
                code = f"to_string({self.x(arg.args[0]).c}).size()"
                return E(code, P_POST) if raw else E(f"{cast}{code}", P_UNARY)
        if isinstance(arg, (ast.ListComp, ast.GeneratorExp)) and len(arg.generators) == 1 and \
                len(arg.generators[0].ifs) == 1 and self.lval(arg.generators[0].iter) and self.T(arg.generators[0].iter).k in ("list", "str", "deque", "set"):
            g = arg.generators[0]
            if isinstance(g.target, ast.Name):
                sym = self.an.sym_of.get(id(g.target))
                et = self.sym_type(sym)
                v = self.cn(sym)
                b, e = self.rng(g.iter)
                ptype = self.ct(et) if is_scalar(et) else "const auto&"
                saved = self.hoist_ok
                self.hoist_ok = False
                c = self.cond(g.ifs[0])
                self.hoist_ok = saved
                return E(f"count_if({b}, {e}, [&]({ptype} {v}) {{ return {c.c}; }})")
        e = self.x(arg)
        if e.cstr:
            return E(str(len(ast.literal_eval(arg)))) if isinstance(arg, ast.Constant) else E(f"string({e.c}).size()")
        if t.k in ("list", "str", "set", "dict", "deque", "lazy", "tuple") or t.k == "bot":
            if t.k == "tuple":
                return E(str(len(t.args)))
            code = f"{par(e, P_POST)}.size()"
            return E(code, P_POST) if raw else E(f"{cast}{code}", P_UNARY)
        if t.k == "char":
            return E("1")
        raise Unsupported(f"len() dla typu {t!r}")

    def iterable_code(self, node) -> str:
        """Kod kontenera do pętli zakresowej (materializuje leniwe iteratory)."""
        t = self.T(node)
        if t.k == "lazy" or isinstance(node, (ast.GeneratorExp, ast.ListComp)):
            e = self.materialize(node)
            return e.c
        if t.k == "dict":
            self._warn_dict_order(node)
            return f"{par(self.x(node), P_POST)} | views::keys"
        e = self.x(node)
        if e.cstr:
            return f"string({e.c})"
        return e.c

    def materialize(self, node) -> E:
        """Zamienia iterowalny obiekt (map/zip/filter/generator…) na wektor."""
        busy = self.__dict__.setdefault("_materializing", set())
        if id(node) in busy:
            raise Unsupported("ten rodzaj iteratora")
        busy.add(id(node))
        try:
            return self._materialize(node)
        finally:
            busy.discard(id(node))

    def _materialize(self, node) -> E:
        t = self.T(node)
        et = elem(t)
        if isinstance(node, ast.Call):
            q = self.q(node)
            if q == "zip" and len(node.args) == 1 and isinstance(node.args[0], ast.Starred):
                self.use("transpose")
                return E(f"transpose({self.x(node.args[0].value).c})")
            tp = self.read_tuple_pattern(node) if q == "map" else None
            if tp is not None and tp[0] in ("int", "float"):
                self.use("readInts" if tp[0] == "int" else "readFloats")
                return E("readInts()" if tp[0] == "int" else "readFloats()")
            if q == "range":
                self.use("rangeVec")
                return E(f"rangeVec({', '.join(self.range_args(node))})")
            if q in ("sorted", "list", "tuple") or (q and short(q) in ("permutations", "combinations", "product",
                                                                       "accumulate")):
                return self.call(node, None, False)
            if q == "reversed" and node.args and self.T(node.args[0]).k in ("list", "str") and self.lval(node.args[0]):
                b = par(self.x(node.args[0]), P_POST)
                return E(f"{self.ct(List(et))}({b}.rbegin(), {b}.rend())")
        comp = isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp))
        if not comp and t.k == "list":
            return self.x(node)
        if not comp and t.k in ("set", "deque", "str"):
            if self.lval(node):
                b, e = self.rng(node)
                return E(f"{self.ct(List(et))}({b}, {e})")
            if self.hoist_ok:
                tmp = self.fresh("tmp")
                self.out(f"auto {tmp} = {self.x(node).c};")
                return E(f"{self.ct(List(et))}({tmp}.begin(), {tmp}.end())")
        if not comp and t.k == "dict":
            self.use("keys")
            return E(f"keys({self.x(node).c})")
        name = self.fresh("tmp")
        lines = self.loop_fill(node, name, "list", List(et))
        if self.hoist_ok:
            self.out(f"{self.ct(List(et))} {name};")
            self.replay(lines)
            return E(name)
        body = " ".join(ln.text for ln in lines)
        return E(f"[&] {{ {self.ct(List(et))} {name}; {body} return {name}; }}()")

    def apply_callable(self, f, args: list, types: list) -> E:
        """f(arg) dla funkcji przekazanej jako wartość (map(int, …), key=…, filter(f, …))."""
        a0 = args[0] if args else E("")
        t0 = types[0] if types else BOT
        if isinstance(f, ast.Lambda):
            fi = self.inf.funcs[id(f)]
            saved = dict(self.rename)
            hs = self.hoist_ok
            self.hoist_ok = False
            try:
                for sym, code in zip(fi.params, args):
                    if code.c.isidentifier():
                        self.rename[sym] = code.c
                    else:
                        raise Unsupported("lambda w map/filter na złożonym argumencie")
                return self.x(f.body)
            finally:
                self.rename = saved
                self.hoist_ok = hs
        q = qual_of_call(self.an, f) if isinstance(f, (ast.Name, ast.Attribute)) else None
        if q == "int":
            if t0.k == "str":
                return E(f"{'stoll' if self.int_t == 'long long' else 'stoi'}({a0.c})")
            if t0.k == "char":
                return E(f"({a0.c} - '0')")
            if t0.k == "float":
                return E(f"({self.int_t}){par(a0, P_UNARY)}", P_UNARY)
            return a0
        if q == "float":
            if t0.k == "str":
                return E(f"stod({a0.c})")
            return E(f"(double){par(a0, P_UNARY)}", P_UNARY)
        if q == "str":
            if t0.k == "int":
                return E(f"to_string({a0.c})")
            if t0.k == "char":
                return E(f"string(1, {a0.c})")
            if t0.k == "str":
                return a0
            self.use("strcore" if is_scalar(t0) else "reprcore")
            return E(f"str({a0.c})")
        if q == "len":
            return E(f"({self.int_t}){par(a0, P_POST)}.size()", P_UNARY)
        if q == "abs":
            return E(f"abs({a0.c})")
        if q == "ord":
            return E(f"(int){par(a0, P_UNARY)}", P_UNARY)
        if q == "chr":
            return E(f"(char){par(a0, P_UNARY)}", P_UNARY)
        if q == "bool":
            return E(f"(bool){par(a0, P_UNARY)}", P_UNARY)
        if q in ("sorted", "list", "tuple", "set"):
            raise Unsupported(f"map({q}, …)")
        if isinstance(f, ast.Attribute) and f.attr in ("upper", "lower", "strip") and isinstance(f.value, ast.Name) and \
                f.value.id == "str":
            self.use(f.attr)
            return E(f"{f.attr}({a0.c})")
        fe = self.x(f)
        return E(f"{par(fe, P_POST)}({', '.join(c.c for c in args)})", P_POST)

    def lazy_fill(self, node, sink) -> None:
        """Element po elemencie leniwego iteratora; `sink(E)` emituje akcję dla elementu."""
        t = self.T(node)
        q = self.q(node) if isinstance(node, ast.Call) else None
        a = node.args if isinstance(node, ast.Call) else []
        if q == "map" and len(a) >= 2:
            f = a[0]
            srcs = a[1:]
            if len(srcs) == 1:
                v = self.fresh("x")
                st = elem(self.T(srcs[0]))
                self.for_over(srcs[0], None, lambda: sink(self.apply_callable(f, [E(v)], [st])), var_name=v,
                              var_type=st)
                return
            k = self.fresh("k")
            conts = [self.materialize(sv).c for sv in srcs]
            bound = f"min({{{', '.join(f'({self.int_t}){c}.size()' for c in conts)}}})"
            self.out(f"for ({self.int_t} {k} = 0; {k} < {bound}; {k}++) {{")
            with self.indent():
                sink(self.apply_callable(f, [E(f"{c}[{k}]") for c in conts], [elem(self.T(sv)) for sv in srcs]))
            self.out("}")
            return
        if q == "filter" and len(a) == 2:
            f, src = a
            v = self.fresh("x")
            st = elem(self.T(src))

            def body():
                if isinstance(f, ast.Constant) and f.value is None:
                    c = E(v)
                else:
                    c = self.apply_callable(f, [E(v)], [st])
                self.out(f"if ({c.c}) {{")
                with self.indent():
                    sink(E(v))
                self.out("}")
            self.for_over(src, None, body, var_name=v, var_type=st)
            return
        if q == "enumerate" and a:
            start = a[1] if len(a) > 1 else self.kw(node, "start")
            st = self.x(start).c if start is not None else "0"
            i = self.fresh("i")
            v = self.fresh("x")
            self.out(f"{self.int_t} {i} = {st};")
            self.for_over(a[0], None, lambda: (sink(E(f"make_pair({i}, {v})")), self.out(f"{i}++;")), var_name=v,
                          var_type=elem(self.T(a[0])))
            return
        if q == "zip" and a:
            conts = []
            for z in a:
                zt = self.T(z)
                if zt.k in ("list", "str", "deque") and self.lval(z):
                    conts.append(par(self.x(z), P_POST))
                else:
                    m = self.materialize(z).c
                    if not m.isidentifier():
                        tmp = self.fresh("tmp")
                        self.out(f"auto {tmp} = {m};")
                        m = tmp
                    conts.append(m)
            k = self.fresh("k")
            sizes = [f"({self.int_t}){c}.size()" for c in conts]
            bound = sizes[0] if len(sizes) == 1 else f"min({{{', '.join(sizes)}}})"
            fn = "make_pair" if len(conts) == 2 else "make_tuple"
            self.out(f"for ({self.int_t} {k} = 0; {k} < {bound}; {k}++) {{")
            with self.indent():
                sink(E(f"{fn}({', '.join(f'{c}[{k}]' for c in conts)})"))
            self.out("}")
            return
        if q == "reversed" and a:
            src = a[0]
            if self.lval(src):
                cont = f"{par(self.x(src), P_POST)} | views::reverse"
            else:
                tmp = self.fresh("tmp")
                self.out(f"auto {tmp} = {self.x(src).c};")
                cont = f"{tmp} | views::reverse"
            v = self.fresh("x")
            self.out(f"for (const auto& {v} : {cont}) {{")
            with self.indent():
                sink(E(v))
            self.out("}")
            return
        if t.k == "lazy" and t.extra in ("map", "filter", "zip", "enumerate", "reversed", "gen"):
            raise Unsupported("iterator w tym miejscu")
        v = self.fresh("x")
        self.for_over(node, None, lambda: sink(E(v)), var_name=v, var_type=elem(t))

    def range_args(self, node) -> list[str]:
        a = node.args
        if len(a) == 1:
            return ["0", self.x(a[0]).c]
        return [self.x(v).c for v in a]

    # ------------------------------------------------------------------ comprehension
    def comp_value(self, node, want) -> E:
        t = self.T(node)
        if want is not None and want.k not in ("bot", "top"):
            if node_kind(node) == want.k or (isinstance(node, ast.GeneratorExp) and want.k == "list"):
                t = want
        kind = {ast.ListComp: "list", ast.SetComp: "set", ast.DictComp: "dict", ast.GeneratorExp: "list"}[type(node)]
        ctype = self.ct(t) if t.k in ("list", "set", "dict") else self.ct(List(elem(t)))
        name = self.fresh({"list": "lst", "set": "st", "dict": "mp"}[kind])
        fill_t = t if t.k in ("list", "set", "dict") else List(elem(t))
        lines = self.loop_fill(node, name, kind, fill_t)
        if self.hoist_ok:
            self.out(f"{ctype} {name};")
            self.replay(lines)
            return E(name)
        body = " ".join(ln.text for ln in lines)
        return E(f"[&] {{ {ctype} {name}; {body} return {name}; }}()")

    def loop_fill(self, comp, target: str, kind: str, cont_t: Ty, action=None) -> list:
        """Linie pętli wypełniającej `target` elementami comprehension/iteratora."""
        with self.capture() as box:
            saved_hoist = self.hoist_ok
            self.hoist_ok = True
            try:
                if isinstance(comp, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                    gens = comp.generators
                    self._comp_level(comp, gens, 0, target, kind, cont_t, action)
                else:
                    # iterowalny obiekt (map, zip, enumerate, filter...) — element po elemencie
                    self.lazy_fill(comp, lambda val: self._fill_action(target, kind, val, cont_t, action))
            finally:
                self.hoist_ok = saved_hoist
        return box

    def _fill_action(self, target, kind, val: E, cont_t, action):
        if action is not None:
            action(val)
            return
        if kind == "list":
            self.out(f"{target}.push_back({val.c});")
        elif kind == "set":
            self.out(f"{target}.insert({val.c});")

    def _comp_level(self, comp, gens, i, target, kind, cont_t, action):
        if i == len(gens):
            if action is not None:
                if isinstance(comp, ast.DictComp):
                    action((comp.key, comp.value))
                else:
                    action(comp.elt)
                return
            if kind == "dict":
                kt, vt = cont_t.args
                k = self.x(comp.key, kt)
                v = self.x(comp.value, vt)
                self.out(f"{target}[{k.c}] = {v.c};")
                return
            et = elem(cont_t)
            v = self.x(comp.elt, et, brace=True)
            if v.cstr and et.k == "str":
                v = E(f"string({v.c})")
            if v.c.startswith("{"):
                v = E(f"{self.ct_elem(et)}{v.c}")
            if v.c.isidentifier() and v.c in self.fresh_log and et.k in ("list", "set", "dict", "str", "deque"):
                v = E(f"move({v.c})")
            if kind == "list":
                self.out(f"{target}.push_back({v.c});")
            else:
                self.out(f"{target}.insert({v.c});")
            return
        g = gens[i]

        def inner():
            conds = list(g.ifs)
            if conds:
                c = " && ".join(par(self.cond(cnd), P_AND) for cnd in conds)
                self.out(f"if ({c}) {{")
                with self.indent():
                    self._comp_level(comp, gens, i + 1, target, kind, cont_t, action)
                self.out("}")
            else:
                self._comp_level(comp, gens, i + 1, target, kind, cont_t, action)
        self.for_over(g.iter, g.target, inner, comp_scope=True)

    # ------------------------------------------------------------------ f-stringi i formatowanie
    def fparts(self, node) -> list:
        """Lista części: ('lit', tekst) | ('val', węzeł, konwersja, spec)."""
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(("lit", str(v.value)))
            elif isinstance(v, ast.FormattedValue):
                spec = ""
                if v.format_spec is not None:
                    if all(isinstance(s, ast.Constant) for s in v.format_spec.values):
                        spec = "".join(str(s.value) for s in v.format_spec.values)
                    else:
                        raise Unsupported("dynamiczna specyfikacja formatu w f-stringu")
                conv = {115: "s", 114: "r", 97: "r"}.get(v.conversion)
                parts.append(("val", v.value, conv, spec))
        return parts

    def format_method_parts(self, call) -> list:
        fmt_node = call.func.value
        if not (isinstance(fmt_node, ast.Constant) and isinstance(fmt_node.value, str)):
            raise Unsupported("str.format na napisie, który nie jest stałą")
        args = call.args
        kws = {k.arg: k.value for k in call.keywords if k.arg}
        parts = []
        auto = 0
        for lit, field, spec, conv in _string.Formatter().parse(fmt_node.value):
            if lit:
                parts.append(("lit", lit))
            if field is None:
                continue
            if field == "":
                node = args[auto]
                auto += 1
            elif field.isdigit():
                node = args[int(field)]
            elif field in kws:
                node = kws[field]
            else:
                raise Unsupported(f"pole `{{{field}}}` w str.format")
            if spec and "{" in spec:
                raise Unsupported("zagnieżdżona specyfikacja w str.format")
            parts.append(("val", node, conv, spec or ""))
        return parts

    def percent_parts(self, node) -> list:
        fmt_node = node.left
        if not (isinstance(fmt_node, ast.Constant) and isinstance(fmt_node.value, str)):
            raise Unsupported("formatowanie % na napisie, który nie jest stałą")
        r = node.right
        if isinstance(r, ast.Tuple):
            vals = list(r.elts)
        elif self.T(r).k == "tuple":
            raise Unsupported("formatowanie % z krotką-zmienną")
        else:
            vals = [r]
        parts = []
        pos = 0
        k = 0
        s = fmt_node.value
        for m in PCT_RE.finditer(s):
            if m.start() > pos:
                parts.append(("lit", s[pos:m.start()]))
            pos = m.end()
            key, flags, width, prec, conv = m.groups()
            if conv == "%":
                parts.append(("lit", "%"))
                continue
            if key or width == "*" or prec == "*":
                raise Unsupported("formatowanie % z nazwami lub `*`")
            v = vals[k]
            k += 1
            spec = ""
            if "-" in flags:
                spec += "<"
            elif conv in "sr" and width:
                spec += ">"
            if "+" in flags:
                spec += "+"
            elif " " in flags:
                spec += " "
            if "0" in flags and "-" not in flags and conv not in "sr":
                spec += "0"
            if width:
                spec += width
            if prec is not None and conv not in "sr":
                spec += "." + prec
            cv = None
            if conv in "diu":
                spec += "d"
                if self.T(v).k == "float":
                    cv = "int"
            elif conv in "eEfFgGxXoc":
                spec += conv
            elif conv == "r":
                cv = "r"
            parts.append(("val", v, cv, spec))
        if pos < len(s):
            parts.append(("lit", s[pos:]))
        return parts

    def percent_format(self, node) -> E:
        return self.parts_to_str(self.percent_parts(node))

    def fstring_str(self, node) -> E:
        return self.parts_to_str(self.fparts(node))

    def val_str(self, node, conv=None, spec="") -> E:
        """Wartość jako std::string (str(x) / format(x, spec) w Pythonie)."""
        t = self.T(node)
        if conv == "int":
            v = self.x(node)
            return self._fmt(E(f"({self.int_t}){par(v, P_UNARY)}", P_UNARY), INT, spec)
        if conv == "r":
            self.use("reprcore")
            v = self.x(node)
            r = E(f"repr({v.c})")
            return self._fmt(r, STR, spec) if spec else r
        if spec:
            return self._fmt(self.x(node), t, spec)
        k = t.k
        if k == "int":
            return E(f"to_string({self.x(node).c})")
        if k == "str":
            e = self.x(node, STR)
            return e
        if k == "char":
            return E(f"string(1, {self.x(node, CHAR).c})")
        if k == "class":
            return E(f"{par(self.x(node), P_POST)}.toStr()", P_POST)
        if k in ("float", "bool", "inf"):
            self.use("strcore")
            return E(f"str({self.x(node).c})")
        self.use("reprcore")
        return E(f"str({self.x(node).c})")

    def _fmt(self, v: E, t: Ty, spec: str) -> E:
        self.use("fmt")
        if t.k == "inf":
            t = INT if self.inf_as_int else FLOAT
        if t.k == "int" and self.int_t == "int":
            v = E(f"(long long){par(v, P_UNARY)}", P_UNARY)
        return E(f"fmt({v.c}, {cstr_lit(spec)})")

    def parts_to_str(self, parts) -> E:
        codes = []
        for p in parts:
            if p[0] == "lit":
                if p[1]:
                    codes.append(E(cstr_lit(p[1]), cstr=True))
            else:
                _, node, conv, spec = p
                codes.append(self.val_str(node, conv, spec))
        if not codes:
            return E('""', cstr=True)
        if len(codes) == 1:
            c = codes[0]
            return E(f"string({c.c})") if c.cstr else c
        if codes[0].cstr and codes[1].cstr:
            codes[0] = E(f"string({codes[0].c})")
        if codes[0].cstr and not codes[1].cstr:
            pass
        out = par(codes[0], P_ADD)
        for c in codes[1:]:
            out += " + " + par(c, P_ADD - 1)
        return E(out, P_ADD)

    def stream_items(self, node) -> list:
        """Elementy strumienia `cout << ...` dla jednego argumentu print()."""
        if isinstance(node, ast.JoinedStr):
            return self.parts_stream(self.fparts(node))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" and \
                isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str):
            return self.parts_stream(self.format_method_parts(node))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and \
                isinstance(node.left.value, str):
            return self.parts_stream(self.percent_parts(node))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [("lit", node.value)]
        return [("code", self.stream_val(node))]

    def parts_stream(self, parts) -> list:
        items = []
        for p in parts:
            if p[0] == "lit":
                if p[1]:
                    items.append(("lit", p[1]))
                continue
            _, node, conv, spec = p
            t = self.T(node)
            m = re.fullmatch(r"\.(\d+)f", spec or "")
            if m and conv is None and (is_numeric(t)):
                v = self.x(node)
                if t.k != "float":
                    v = E(f"(double){par(v, P_UNARY)}", P_UNARY)
                items.append(("code", f"fixed << setprecision({m.group(1)}) << {par(v, P_SHIFT - 1)}"))
                continue
            if not spec and conv is None:
                items.append(("code", self.stream_val(node)))
                continue
            items.append(("code", par(self.val_str(node, conv, spec), P_SHIFT - 1)))
        return items

    def stream_val(self, node) -> str:
        t = self.T(node)
        k = t.k
        if isinstance(node, ast.IfExp) and (k == "top" or self.T(node.body).k != self.T(node.orelse).k) and \
                not (is_numeric(self.T(node.body)) and is_numeric(self.T(node.orelse))) and \
                {self.T(node.body).k, self.T(node.orelse).k} != {"str", "char"}:
            c = self.cond(node.test)
            a = self.val_str(node.body)
            b = self.val_str(node.orelse)
            a = E(f"string({a.c})") if a.cstr else a
            return f"({par(c, P_OR)} ? {par(a, P_OR)} : {par(b, P_COND)})"
        if isinstance(node, ast.Call) and self.q(node) == "len" and len(node.args) == 1:
            return par(self.len_expr(node.args[0], raw=True), P_SHIFT - 1)
        if k in ("int", "str", "char"):
            e = self.x(node)
            return par(e, P_SHIFT - 1)
        if k == "bool":
            c = self.x(node)
            return f'({par(c, P_OR)} ? "True" : "False")'
        if k == "float":
            self.use("strcore")
            return f"str({self.x(node).c})"
        if k == "inf":
            if self.inf_as_int:
                return par(self.x(node), P_SHIFT - 1)
            self.use("strcore")
            return f"str({self.x(node).c})"
        if k == "none":
            return '"None"'
        if k == "class":
            return f"{par(self.x(node), P_POST)}.toStr()"
        if k in ("set", "dict") and not isinstance(node, (ast.Set, ast.Dict)):
            self.warn("set-order", "Wypisanie zbioru/słownika: kolejność elementów w C++ (posortowana) może "
                      "różnić się od Pythona.", getattr(node, "lineno", None), "info")
        if k == "list" and t.extra == "tuple":
            self.use("reprcore")
            return f"reprAsTuple({self.x(node).c})"
        if k in ("list", "tuple", "set", "dict", "deque", "opt", "lazy"):
            self.use("reprcore")
            return f"str({self.x(node).c})"
        e = self.x(node)
        return par(e, P_SHIFT - 1)


def node_kind(node) -> str:
    return {ast.List: "list", ast.ListComp: "list", ast.Set: "set", ast.SetComp: "set", ast.Dict: "dict",
            ast.DictComp: "dict", ast.Tuple: "tuple", ast.GeneratorExp: "lazy"}.get(type(node), "")


__all__ = ["ExprMixin", "E", "Unsupported", "par", "cstr_lit", "char_lit", "P_PRIM", "P_POST", "P_UNARY", "P_MUL",
           "P_ADD", "P_SHIFT", "P_REL", "P_EQ", "P_AND", "P_OR", "P_COND", "node_kind", "NONE", "Dict", "join",
           "is_stdin_read_split", "short"]

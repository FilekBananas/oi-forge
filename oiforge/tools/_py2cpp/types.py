"""Krata typów tłumacza Python → C++.

Typy są niemutowalne (hashowalne), łączone operacją `join` (najmniejsze ograniczenie górne):
bool ⊑ int ⊑ float, char ⊑ str, none ⊔ T = optional<T>, kontenery łączone po elementach.
`bot` = jeszcze nieznany, `top` = konflikt (nie da się wyrazić jednym typem C++).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ty:
    k: str                  # rodzaj: int, float, bool, char, str, none, inf, list, set, dict, deque, tuple, opt, ...
    args: tuple = ()        # typy składowe
    extra: str = ""         # dict: '' | 'default' | 'counter'; class/func: identyfikator; lazy: rodzaj iteratora

    def __repr__(self) -> str:  # czytelne w testach i ostrzeżeniach
        if not self.args:
            return self.k if not self.extra else f"{self.k}:{self.extra}"
        inner = ", ".join(repr(a) for a in self.args)
        return f"{self.k}{'/' + self.extra if self.extra else ''}[{inner}]"


BOT = Ty("bot")
TOP = Ty("top")
INT = Ty("int")
FLOAT = Ty("float")
BOOL = Ty("bool")
CHAR = Ty("char")
STR = Ty("str")
NONE = Ty("none")
INF = Ty("inf")            # float('inf') / math.inf — łączy się z int jako duża stała całkowita
TOKENS = Ty("tokens")      # iterator po tokenach całego wejścia (iter(sys.stdin.read().split()))
FILE = Ty("file")          # sys.stdin / open(0)

NUMERIC = {"bool": 0, "int": 1, "float": 2}
SCALARS = {"int", "float", "bool", "char", "inf"}


def List(t: Ty) -> Ty:
    return Ty("list", (t,))


def Set(t: Ty) -> Ty:
    return Ty("set", (t,))


def Deque(t: Ty) -> Ty:
    return Ty("deque", (t,))


def Dict(k: Ty, v: Ty, flavor: str = "") -> Ty:
    return Ty("dict", (k, v), flavor)


def Tuple(*ts: Ty) -> Ty:
    return Ty("tuple", tuple(ts))


def Opt(t: Ty) -> Ty:
    return Ty("opt", (t,))


def Func(fid: str) -> Ty:
    return Ty("func", (), fid)


def Cls(name: str) -> Ty:
    return Ty("class", (), name)


def Lazy(kind: str, elem: Ty) -> Ty:
    """Leniwy iterowalny obiekt (range, map, zip, enumerate, generator, items…)."""
    return Ty("lazy", (elem,), kind)


def Module(name: str) -> Ty:
    return Ty("module", (), name)


def Builtin(name: str) -> Ty:
    return Ty("builtin", (), name)


def depth(t: Ty) -> int:
    return 1 + max((depth(a) for a in t.args), default=0)


def _flavor(a: str, b: str) -> str:
    if a == b:
        return a
    if "counter" in (a, b):
        return "counter"
    if "default" in (a, b):
        return "default"
    return a or b


def join(a: Ty, b: Ty) -> Ty:
    """Najmniejszy typ obejmujący oba (z promocjami jak w Pythonie)."""
    if a == b:
        return a
    if a.k == "bot":
        return b
    if b.k == "bot":
        return a
    if a.k == "top" or b.k == "top":
        return TOP
    ka, kb = a.k, b.k
    if "inf" in (ka, kb):
        other = kb if ka == "inf" else ka
        if other in ("int", "bool"):
            return INT
        if other == "float":
            return FLOAT
    if ka in NUMERIC and kb in NUMERIC:
        return a if NUMERIC[ka] >= NUMERIC[kb] else b
    if {ka, kb} == {"char", "str"}:
        return STR
    if ka == "none":
        return b if kb == "opt" else Opt(b)
    if kb == "none":
        return a if ka == "opt" else Opt(a)
    if ka == "opt" or kb == "opt":
        ia = a.args[0] if ka == "opt" else a
        ib = b.args[0] if kb == "opt" else b
        j = join(ia, ib)
        return TOP if j.k == "top" else Opt(j)
    res = TOP
    if ka == kb:
        if ka in ("list", "set", "deque"):
            res = Ty(ka, (join(a.args[0], b.args[0]),))
        elif ka == "dict":
            res = Dict(join(a.args[0], b.args[0]), join(a.args[1], b.args[1]), _flavor(a.extra, b.extra))
        elif ka == "tuple":
            if len(a.args) == len(b.args):
                res = Tuple(*[join(x, y) for x, y in zip(a.args, b.args)])
            else:
                res = List(join(elem(a), elem(b)))
        elif ka == "lazy":
            res = Lazy(a.extra, join(a.args[0], b.args[0])) if a.extra == b.extra else List(join(a.args[0], b.args[0]))
        else:
            res = a if a.extra == b.extra else TOP
    elif {ka, kb} <= {"list", "tuple", "lazy"}:
        res = List(join(elem(a), elem(b)))
    if res.k != "top" and depth(res) > 8:
        return TOP
    return res


def elem(t: Ty) -> Ty:
    """Typ elementu przy iteracji `for x in t`."""
    k = t.k
    if k in ("list", "set", "deque", "lazy"):
        return t.args[0]
    if k in ("str", "char"):
        return CHAR
    if k == "dict":
        return t.args[0]
    if k == "tuple":
        out = BOT
        for a in t.args:
            out = join(out, a)
        return out
    if k in ("tokens", "file"):
        return STR
    if k == "opt":
        return elem(t.args[0])
    if k == "bot":
        return BOT
    return TOP


def contains_top(t: Ty) -> bool:
    return t.k == "top" or any(contains_top(a) for a in t.args)


def contains_bot(t: Ty) -> bool:
    return t.k == "bot" or any(contains_bot(a) for a in t.args)


def is_scalar(t: Ty) -> bool:
    return t.k in SCALARS


def is_numeric(t: Ty) -> bool:
    return t.k in NUMERIC or t.k == "inf"


def is_intlike(t: Ty) -> bool:
    return t.k in ("int", "bool")


def is_seq(t: Ty) -> bool:
    """Indeksowalne kontenery (a[i])."""
    return t.k in ("list", "str", "deque", "tuple")


def is_container(t: Ty) -> bool:
    return t.k in ("list", "set", "dict", "deque", "str", "tuple")


def hashable_simple(t: Ty) -> bool:
    """Czy std::hash jest dostępny bez własnego hashera (dla unordered_map/set)."""
    return t.k in ("int", "bool", "char", "str", "float", "inf")


def tag(t: Ty) -> str:
    """Krótki przyrostek nazwy przy rozdzielaniu zmiennych różnych typów (x_str, a_list…)."""
    return {"int": "int", "float": "f", "bool": "b", "char": "ch", "str": "str", "list": "list", "set": "set",
            "dict": "map", "deque": "dq", "tuple": "t", "opt": "opt"}.get(t.k, "v")

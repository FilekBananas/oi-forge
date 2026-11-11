"""Synteza programów przez wyliczanie (zero AI): „napisz brute samemu” na podstawie treści i przykładów.

Pomysł: przykładowe wejścia czytamy w Pythonie DOKŁADNIE tak, jak zrobi to wygenerowany kod C++ (wspólny „plan
wczytywania” z formatu wejścia z treści), a potem wyliczamy programy z małego języka typowych rozwiązań siłowych:
  * wzory na liczbach z wejścia i stałych z treści (wyliczanie wyrażeń od najkrótszych, z utożsamianiem równoważnych),
  * agregaty ciągu (suma, max, różne wartości, k-ty, mediana, licz/sumuj spełniające warunek…),
  * wszystkie pary i < j, trójki, spójne fragmenty, podzbiory (2^n), permutacje (n!), wartości x ∈ [1..N],
  * zapytania liczone naiwnie, ciągi wynikowe (sortowanie, sumy prefiksowe…), napisy (podsłowa, palindromy, LCS…),
  * grafy (składowe, BFS/Dijkstra, drzewa), plansze (BFS, obszary), macierze, wzór dla każdego wiersza / zestawu.
Kandydat jest interpretowany w Pythonie (szybko, bez kompilacji) i porównywany token po tokenie z wyjściem KAŻDEGO
przykładu (także testów „ocen”). Ranking: krótszy opis (brzytwa Ockhama) + zgodność ze słowami treści („par” → pary,
„spójny fragment” → fragmenty…). Dopiero zwycięzca jest tłumaczony na czytelny C++ (nazwy zmiennych z treści),
kompilowany i uruchamiany na przykładach, a potem porównywany z interpreterem na losowych małych testach (`inputgen`).

Ochrona przed przeuczeniem: jeśli dwa kandydaty pasują do wszystkich przykładów, a różnią się na losowych wejściach,
wynik NIE jest „rozwiązany” — pokazujemy obu i wejście, na którym się różnią. Jeden przykład z jednocyfrowym wynikiem
to za mało, by ufać (status „weak”).

Ten sam mechanizm pilnuje rozwiązań spoza syntezy: `challenge()` uruchamia gotowy kod (szablon, kod z podobnego
zadania) na losowych testach i porównuje z wiarygodnymi programami syntezy zgodnymi z przykładami — jeśli któryś liczy
coś innego, przykłady nie rozstrzygają i narzędzie nie ogłasza sukcesu.

Publiczne API:
    synthesize(parsed, statement_text="", *, words=None, job=None, budget=6.0, want="brute") -> dict
    challenge(parsed, statement_text, code, *, words=None) -> {checked, tests, competitors, agree}
    build_plan(fmt, samples) -> Plan | None ;  Plan.read(text) -> env | None ;  Plan.cpp_reader() -> str
    reader_program(parsed) -> str | None      (kompletny program wczytujący dane — do pokazania, gdy nic nie znaleziono)
"""
from __future__ import annotations

import functools
import itertools
import math
import random
import re
import time
from collections import Counter, deque

from . import statement as ST
from . import text as T

LL_MAX = 2**63 - 1
LL_MIN = -2**63
PY_WORK = 400_000          # ile „operacji” może zająć jeden kandydat na jednym przykładzie w interpreterze
BIG_INPUT = 200_000        # przykłady dłuższe niż tyle znaków nie są interpretowane w Pythonie (tylko C++)


class Bad(Exception):
    """Kandydat nie ma sensu na tym wejściu (dzielenie przez 0, przepełnienie long long, pusty ciąg…)."""


def _ck(v):
    if isinstance(v, int) and not (LL_MIN <= v <= LL_MAX):
        raise Bad
    return v


class _None:
    """Wynik „brak odpowiedzi” (np. nie ma takiego fragmentu) — wypisywany słowem z treści (-1, NIE, BRAK…)."""
    __slots__ = ()

    def __repr__(self):
        return "NONE"


NONE = _None()


# ============================================================================================ plan wczytywania
class Len:
    """Długość/liczność: stała, zmienna (± stała, · stała) albo „do końca wejścia”."""
    __slots__ = ("kind", "var", "add", "mul", "val")

    def __init__(self, kind, var=None, add=0, mul=1, val=None):
        self.kind, self.var, self.add, self.mul, self.val = kind, var, add, mul, val

    def eval(self, env):
        if self.kind == "int":
            return self.val
        if self.kind == "var":
            v = env.get(self.var)
            if not isinstance(v, int):
                raise Bad
            return v * self.mul + self.add
        raise Bad

    def cpp(self):
        if self.kind == "int":
            return str(self.val)
        s = self.var if self.mul == 1 else f"{self.mul} * {self.var}"
        if self.add:
            s += f" {'+' if self.add > 0 else '-'} {abs(self.add)}"
        return s

    def text(self):
        return self.cpp()


def _parse_len(expr, names: dict) -> Len | None:
    """„n”, „n-1”, „2n”, „2·n”, „5” → Len; zmienne mapowane na nazwy z planu (base → nazwa C++)."""
    if expr is None:
        return None
    if isinstance(expr, int):
        return Len("int", val=expr)
    e = str(expr).replace(" ", "").replace("·", "*")
    if re.fullmatch(r"\d+", e):
        return Len("int", val=int(e))
    m = re.fullmatch(r"(?:(\d+)\*?)?([A-Za-z][A-Za-z0-9_]*)(?:\*(\d+))?(?:([-+])(\d+))?", e)
    if not m:
        return None
    var = names.get(ST.base_var(m.group(2))) or names.get(m.group(2))
    if var is None:
        return None
    mul = int(m.group(1) or m.group(3) or 1)
    add = int(m.group(5) or 0) * (-1 if m.group(4) == "-" else 1)
    return Len("var", var=var, add=add, mul=mul)


class Item:
    """Element planu. kind ∈ scal | arr | str | rows | mat | grid | ops | mt."""

    def __init__(self, kind, **kw):
        self.kind = kind
        self.name = None
        self.names: list = []
        self.types: list = []
        self.typ = "i"
        self.length: Len | None = None
        self.count: Len | None = None
        self.until = None
        self.eof = False
        self.ops: dict = {}
        self.items: list = []
        self.var = None
        self.__dict__.update(kw)


_CT = {"i": "long long", "f": "double", "s": "string"}


class _Toks:
    """Strumień tokenów jak `cin >>` (plus `getline` po `ws` dla ciągów bez znanej długości)."""

    def __init__(self, text: str):
        self.t: list[str] = []
        self.line: list[int] = []
        for li, ln in enumerate(text.replace("\r\n", "\n").split("\n")):
            for tok in ln.split():
                self.t.append(tok)
                self.line.append(li)
        self.p = 0

    def left(self):
        return len(self.t) - self.p

    def tok(self):
        if self.p >= len(self.t):
            raise Bad
        self.p += 1
        return self.t[self.p - 1]

    def rest_of_line(self):
        if self.p >= len(self.t):
            raise Bad
        li = self.line[self.p]
        out = []
        while self.p < len(self.t) and self.line[self.p] == li:
            out.append(self.t[self.p])
            self.p += 1
        return out


def _conv(tok: str, typ: str):
    if typ == "i":
        if not re.fullmatch(r"[-+]?\d+", tok) or len(tok) > 19:
            raise Bad
        v = int(tok)
        if not (LL_MIN <= v <= LL_MAX):
            raise Bad
        return v
    if typ == "f":
        try:
            return float(tok)
        except ValueError:
            raise Bad from None
    return tok


class Plan:
    """Wspólny model wczytywania: Python (`read`) i C++ (`cpp_reader`) czytają dane identycznie."""

    def __init__(self, items: list[Item], used: set[str]):
        self.items = items
        self.used = set(used)
        self.mt = items[0] if items and items[0].kind == "mt" else None

    # ---------------------------------------------------------------- Python
    def read(self, text: str):
        tk = _Toks(text)
        env: dict = {}
        try:
            self._read_items(self.items, tk, env)
        except (Bad, ValueError, OverflowError, MemoryError):
            return None
        if tk.left():
            return None
        return env

    def _read_items(self, items, tk, env):
        for it in items:
            k = it.kind
            if k == "scal":
                for nm, ty in zip(it.names, it.types):
                    env[nm] = _conv(tk.tok(), ty)
            elif k == "str":
                env[it.name] = tk.tok()
            elif k == "arr":
                if it.length is None:
                    env[it.name] = [_conv(x, it.typ) for x in tk.rest_of_line()]
                else:
                    L = it.length.eval(env)
                    if L < 0 or L > 5_000_000 or L > tk.left():
                        raise Bad
                    env[it.name] = [_conv(tk.tok(), it.typ) for _ in range(L)]
            elif k == "rows":
                cols = [[] for _ in it.names]
                if it.until is not None:
                    while tk.left():
                        first = _conv(tk.tok(), it.types[0])
                        if str(first) == it.until or tk.t[tk.p - 1] == it.until:
                            break
                        cols[0].append(first)
                        for c in range(1, len(it.names)):
                            cols[c].append(_conv(tk.tok(), it.types[c]))
                elif it.eof:
                    w = len(it.names)
                    if tk.left() % w:
                        raise Bad
                    while tk.left():
                        for c in range(w):
                            cols[c].append(_conv(tk.tok(), it.types[c]))
                else:
                    R = it.count.eval(env)
                    if R < 0 or R > 2_000_000 or R * len(it.names) > tk.left():
                        raise Bad
                    for _ in range(R):
                        for c in range(len(it.names)):
                            cols[c].append(_conv(tk.tok(), it.types[c]))
                for nm, col in zip(it.names, cols):
                    env[nm] = col
                env["#rows:" + it.name] = len(cols[0]) if cols else 0
            elif k == "mat":
                R, C = it.count.eval(env), it.length.eval(env)
                if R < 0 or C < 0 or R * C > tk.left():
                    raise Bad
                env[it.name] = [[_conv(tk.tok(), it.typ) for _ in range(C)] for _ in range(R)]
            elif k == "grid":
                R = it.count.eval(env)
                if R < 0 or R > tk.left():
                    raise Bad
                env[it.name] = [tk.tok() for _ in range(R)]
            elif k == "ops":
                R = it.count.eval(env)
                if R < 0 or R > tk.left():
                    raise Bad
                qs = []
                for _ in range(R):
                    key = tk.tok()
                    spec = it.ops.get(key)
                    if spec is None:
                        raise Bad
                    qs.append((key, tuple(_conv(tk.tok(), ty) for ty in spec)))
                env[it.name] = qs
            elif k == "mt":
                T_ = _conv(tk.tok(), "i")
                if T_ < 0 or T_ > 200_000:
                    raise Bad
                env[it.var] = T_
                tests = []
                for _ in range(T_):
                    sub: dict = {}
                    self._read_items(it.items, tk, sub)
                    tests.append(sub)
                env["#tests"] = tests

    # ---------------------------------------------------------------- C++
    def cpp_reader(self, ind: str = "    ", body: str | None = None, fresh=None, ops_info: dict | None = None) -> str:
        """Kod C++ wczytujący dane (w `main`). `body` = kod rozwiązania jednego zestawu (multitest) albo całości.
        `ops_info` (samo wczytywanie): wiersze operacji też są czytane — pętla po zapytaniach z rozgałęzieniem na typ,
        nazwy argumentów z treści ({klucz: {params, meaning, prints}})."""
        fresh = fresh or Fresh(self.used)
        out: list[str] = []
        self._cpp_items(self.items, ind, out, fresh, body, ops_info)
        if body is not None and not self.mt:
            out.append(_indent(body, ind))
        return "\n".join(out)

    def _cpp_ops(self, it, ind, fresh, info: dict) -> list[str]:
        qi, typ = fresh("qi"), fresh("typ")
        num = all(re.fullmatch(r"-?\d+", key) for key in it.ops)
        out = [f"{ind}for (int {qi} = 0; {qi} < {it.count.cpp()}; {qi}++) {{",
               f"{ind}    {'int' if num else 'string'} {typ};", f"{ind}    cin >> {typ};"]
        bad = set(Fresh._KW) | set(self.used) | {qi, typ}
        for j, (key, spec) in enumerate(it.ops.items()):
            q = info.get(key) or {}
            names = [str(x) for x in q.get("params") or []]
            if len(names) != len(spec) or len(set(names)) != len(names) or \
                    not all(re.fullmatch(r"[A-Za-z_]\w{0,15}", x) for x in names):
                names = ["x"] if len(spec) == 1 else [f"x{i + 1}" for i in range(len(spec))]
            names = [x + "_" if x in bad else x for x in names]
            cond = f"{typ} == {key}" if num else f'{typ} == "{key}"'
            out.append(f"{ind}    {'if' if j == 0 else '} else if'} ({cond}) {{")
            groups: dict = {}
            for nm_, ty in zip(names, spec):
                groups.setdefault(_CT[ty], []).append(nm_)
            for t, nms in groups.items():
                out.append(f"{ind}        {t} {', '.join(nms)};")
            if names:
                out.append(f"{ind}        cin >> {' >> '.join(names)};")
            what = " ".join([key] + names)
            mean = (q.get("meaning") or "").strip()
            note = f" — {mean}" if mean else ""
            if q.get("prints"):
                note += " (wypisuje odpowiedź)"
            out.append(f"{ind}        // {what}{note}")
        out.append(f"{ind}    }}")
        out.append(f"{ind}}}")
        return out

    def _cpp_items(self, items, ind, out, fresh, body, ops_info=None):
        for it in items:
            k = it.kind
            if k == "scal":
                groups: dict = {}
                for nm, ty in zip(it.names, it.types):
                    groups.setdefault(_CT[ty], []).append(nm)
                for t, nms in groups.items():
                    out.append(f"{ind}{t} {', '.join(nms)};")
                out.append(f"{ind}cin >> {' >> '.join(it.names)};")
            elif k == "str":
                out.append(f"{ind}string {it.name};")
                out.append(f"{ind}cin >> {it.name};")
            elif k == "arr":
                t = _CT[it.typ]
                if it.length is None:
                    ln, ss, x = fresh("linia"), fresh("ss"), fresh("x")
                    out.append(f"{ind}vector<{t}> {it.name};")
                    out.append(f"{ind}{{   // cały wiersz liczb")
                    out.append(f"{ind}    string {ln};")
                    out.append(f"{ind}    getline(cin >> ws, {ln});")
                    out.append(f"{ind}    stringstream {ss}({ln});")
                    out.append(f"{ind}    {t} {x};")
                    out.append(f"{ind}    while ({ss} >> {x}) {it.name}.push_back({x});")
                    out.append(f"{ind}}}")
                else:
                    x = fresh("x")
                    out.append(f"{ind}vector<{t}> {it.name}({it.length.cpp()});")
                    out.append(f"{ind}for (auto &{x} : {it.name}) cin >> {x};")
            elif k == "rows":
                decl: dict = {}
                for nm, ty in zip(it.names, it.types):
                    decl.setdefault(_CT[ty], []).append(nm)
                if it.until is not None or it.eof:
                    for t, nms in decl.items():
                        out.append(f"{ind}vector<{t}> {', '.join(nms)};")
                    tmp = [fresh(nm + "_") for nm in it.names]
                    out.append(f"{ind}while (true) {{")
                    for nm, ty, tv in zip(it.names, it.types, tmp):
                        out.append(f"{ind}    {_CT[ty]} {tv};")
                    if it.until is not None:
                        out.append(f"{ind}    if (!(cin >> {tmp[0]}) || {tmp[0]} == {_lit(it.until, it.types[0])}) break;"
                                   f"   // wiersz kończący dane")
                        if len(tmp) > 1:
                            out.append(f"{ind}    cin >> {' >> '.join(tmp[1:])};")
                    else:
                        out.append(f"{ind}    if (!(cin >> {' >> '.join(tmp)})) break;   // do końca wejścia")
                    for nm, tv in zip(it.names, tmp):
                        out.append(f"{ind}    {nm}.push_back({tv});")
                    out.append(f"{ind}}}")
                else:
                    cnt = it.count.cpp()
                    for t, nms in decl.items():
                        out.append(f"{ind}vector<{t}> " + ", ".join(f"{nm}({cnt})" for nm in nms) + ";")
                    i = fresh("i")
                    out.append(f"{ind}for (int {i} = 0; {i} < {cnt}; {i}++) cin >> "
                               + " >> ".join(f"{nm}[{i}]" for nm in it.names) + ";")
            elif k == "mat":
                t = _CT[it.typ]
                r, x = fresh("wiersz"), fresh("x")
                out.append(f"{ind}vector<vector<{t}>> {it.name}({it.count.cpp()}, vector<{t}>({it.length.cpp()}));")
                out.append(f"{ind}for (auto &{r} : {it.name}) for (auto &{x} : {r}) cin >> {x};")
            elif k == "grid":
                r = fresh("wiersz")
                out.append(f"{ind}vector<string> {it.name}({it.count.cpp()});")
                out.append(f"{ind}for (auto &{r} : {it.name}) cin >> {r};")
            elif k == "ops":
                # w rozwiązaniu operacje są czytane w jego pętli (każda ma własne argumenty); samo wczytywanie — tutaj
                if ops_info is not None:
                    out.extend(self._cpp_ops(it, ind, fresh, ops_info))
            elif k == "mt":
                out.append(f"{ind}int {it.var};")
                out.append(f"{ind}cin >> {it.var};")
                out.append(f"{ind}while ({it.var}--) {{")
                self._cpp_items(it.items, ind + "    ", out, fresh, body, ops_info)
                if body is not None:
                    out.append(_indent(body, ind + "    "))
                out.append(f"{ind}}}")

    def describe(self) -> list[str]:
        out = []
        for it in self.items:
            out.append(_item_desc(it))
            for sub in it.items:
                out.append("  w każdym zestawie: " + _item_desc(sub))
        return out

    def top(self) -> list[Item]:
        """Elementy, na których działa rozwiązanie (przy wielu zestawach — elementy jednego zestawu)."""
        return self.mt.items if self.mt else self.items


def _item_desc(it: Item) -> str:
    k = it.kind
    if k == "scal":
        return "liczby " + ", ".join(it.names)
    if k == "arr":
        return f"ciąg {it.name}" + (f" ({it.length.text()} elementów)" if it.length else "")
    if k == "str":
        return f"napis {it.name}"
    if k == "rows":
        return f"wiersze ({', '.join(it.names)})"
    if k == "mat":
        return f"macierz {it.name}"
    if k == "grid":
        return f"plansza {it.name}"
    if k == "ops":
        return "operacje: " + ", ".join(sorted(it.ops))
    if k == "mt":
        return f"{it.var} zestawów danych"
    return k


def _lit(tok, typ):
    if typ == "s":
        return '"' + str(tok).replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(tok)


def _indent(s: str, ind: str) -> str:
    return "\n".join((ind + ln) if ln.strip() else ln for ln in s.split("\n"))


class Fresh:
    """Nowe nazwy zmiennych lokalnych, które nie kolidują z nazwami z treści."""

    _KW = set(ST._CPP_KEYWORDS) | {"main", "cin", "cout", "endl", "ll", "int", "long", "string", "vector", "res",
                                    "INF", "NONE", "gcd", "lcm", "abs", "sqrt", "pow", "log", "exp", "sort", "count",
                                    "find", "reverse", "next", "prev", "begin", "end", "size", "rank", "array", "list"}

    def __init__(self, used):
        self.used = set(used) | self._KW

    def __call__(self, base: str) -> str:
        nm = base
        i = 2
        while nm in self.used:
            nm = f"{base}{i}"
            i += 1
        self.used.add(nm)
        return nm


def _cpp_ident(v: str) -> str:
    return ST.cpp_name(v)


def _tok_type(toks: list[str]) -> str:
    if toks and all(re.fullmatch(r"[-+]?\d{1,18}", t) for t in toks):
        return "i"
    if toks and all(re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", t) for t in toks):
        return "f"
    return "s"


def build_plan(fmt: list[dict], samples: list[dict]) -> Plan | None:
    """Plan wczytywania z formatu z treści, sprawdzony na wszystkich (nie-gigantycznych) przykładach."""
    if not fmt:
        return None
    ins = [s["input"] for s in samples if s.get("input") and len(s["input"]) <= BIG_INPUT]
    used: set[str] = set()
    names: dict[str, str] = {}

    def nm(v, default="x"):
        b = _cpp_ident(v or default)
        base = b
        i = 2
        while b in used or b in Fresh._KW:
            b = f"{base}{i}" if b in used else base + "_"
            i += 1
        used.add(b)
        names.setdefault(ST.base_var(v or default), b)
        names.setdefault(v or default, b)
        return b

    def ty(t):
        return {"double": "f", "string": "s", "bignum": "s"}.get(t or "int", "i")

    top: list[Item] = []
    sub: list[Item] = []
    for it in fmt:
        k = it.get("kind")
        vs = list(it.get("vars") or [])
        ts = list(it.get("types") or [])
        dst = sub if it.get("per_test") else top
        if k == "multitest":
            top.append(Item("mt", var=nm(vs[0] if vs else "t", "t")))
        elif k == "scalars":
            if not vs:
                continue
            dst.append(Item("scal", names=[nm(v) for v in vs], types=[ty(ts[i] if i < len(ts) else "int")
                                                                      for i in range(len(vs))]))
        elif k == "array":
            L = _parse_len(it.get("length"), names)
            dst.append(Item("arr", name=nm(vs[0] if vs else "a", "a"), typ=ty(ts[0] if ts else "int"), length=L))
        elif k == "string":
            dst.append(Item("str", name=nm(vs[0] if vs else "s", "s")))
        elif k in ("rows", "grid", "lines", "queries"):
            rep = str(it.get("repeat") or "")
            until = rep.split(":", 1)[1] if rep.startswith("until:") else None
            eof = rep in ("EOF", "?", "")
            cnt = None if (until is not None or eof) else _parse_len(rep, names)
            if cnt is None and until is None and not eof:
                return None
            if k == "rows" and it.get("length"):
                L = _parse_len(it.get("length"), names)
                if L is None or cnt is None:
                    return None
                dst.append(Item("mat", name=nm(vs[0] if vs else "a", "a"), typ=ty(ts[0] if ts else "int"), count=cnt,
                                length=L))
            elif k in ("grid", "lines"):
                if cnt is None:
                    return None
                base = vs[0] if vs and vs[0] not in ("?",) else ("plansza" if k == "grid" else "slowa")
                dst.append(Item("grid", name=nm(base if k == "grid" else base, base)))
                dst[-1].count = cnt
            elif k == "queries":
                if cnt is None:
                    return None
                dst.append(Item("ops", name=nm("zapytania"), count=cnt))
            else:
                if not vs:
                    vs = ["?"]
                if len(vs) == 1 and vs[0] != "?" and until is None and not eof:
                    # „n wierszy, w każdym jedna liczba” = ciąg długości n (po jednej liczbie w wierszu)
                    dst.append(Item("arr", name=nm(vs[0]), typ=ty(ts[0] if ts else "int"), length=cnt))
                else:
                    fields = [nm(v) for v in vs] if vs != ["?"] else []
                    dst.append(Item("rows", name=f"r{len(top) + len(sub)}", names=fields,
                                    types=[ty(ts[i] if i < len(ts) else "int") for i in range(len(fields))],
                                    count=cnt, until=until, eof=eof))
    if top and top[0].kind == "mt":
        top[0].items = sub
    elif sub:
        top += sub
    plan = Plan(top, used)
    if not _fix_with_samples(plan, ins):
        return None
    return plan


def _fix_with_samples(plan: Plan, ins: list[str]) -> bool:
    """Typy pól z przykładów (liczba/napis), pola wierszy bez nazw, operacje w zapytaniach; sprawdzenie planu."""
    if not ins:
        return False
    items = plan.top()
    # typy i nazwy pól w wierszach, operacje — symulacja czytania z „miękkim” typem
    for it in items:
        if it.kind == "rows" and not it.names:
            return False
    ops_items = [it for it in items if it.kind == "ops"]
    if ops_items:
        if not _infer_ops(plan, ops_items[0], ins):
            return False
    # typy z tokenów przykładów: spróbuj przeczytać z typem „s” tam, gdzie „i” nie pasuje
    for _ in range(3):
        bad = [i for i, s in enumerate(ins) if plan.read(s) is None]
        if not bad:
            return True
        changed = _relax_types(plan, ins[bad[0]])
        if not changed:
            return False
    return all(plan.read(s) is not None for s in ins)


def _relax_types(plan: Plan, text: str) -> bool:
    """Pola liczbowe, w których stoi słowo (np. imię), zamieniamy na napisy — jeśli to wystarcza do przeczytania."""
    changed = False
    for it in plan.top() + ([plan.mt] if plan.mt else []):
        if it is None:
            continue
        if it.kind in ("scal", "rows"):
            for j, t in enumerate(it.types):
                if t == "i":
                    it.types[j] = "s"
                    if plan.read(text) is not None:
                        return True
                    it.types[j] = "i"
        if it.kind == "arr" and it.length is None and it.typ == "i":
            continue
    return changed


def _infer_ops(plan: Plan, it: Item, ins: list[str]) -> bool:
    """Operacje w wierszach („PUSH x”, „1 l r”): klucz = pierwszy token, liczba i typy argumentów z przykładów."""
    # czytamy wszystko przed operacjami planem, a operacje — wierszami
    before = plan.top()[:plan.top().index(it)]
    after = plan.top()[plan.top().index(it) + 1:]
    if after or plan.mt:
        return False
    seen: dict[str, list[list[str]]] = {}
    for s in ins:
        tk = _Toks(s)
        env: dict = {}
        try:
            plan._read_items(before, tk, env)
            R = it.count.eval(env)
        except Bad:
            return False
        if 0 < tk.p < len(tk.t) and tk.line[tk.p - 1] == tk.line[tk.p]:
            return False                 # operacje muszą zaczynać się w nowym wierszu
        rows: list[list[str]] = []
        cur = None
        for p_ in range(tk.p, len(tk.t)):
            if tk.line[p_] != cur:
                rows.append([])
                cur = tk.line[p_]
            rows[-1].append(tk.t[p_])
        if len(rows) != R:
            return False
        for r in rows:
            seen.setdefault(r[0], []).append(r[1:])
    if not seen or len(seen) > 8:
        return False
    ops = {}
    for key, argss in seen.items():
        ar = {len(a) for a in argss}
        if len(ar) != 1:
            return False
        w = ar.pop()
        ops[key] = [_tok_type([a[j] for a in argss]) for j in range(w)]
    it.ops = ops
    return True


# ============================================================================================ wyjście
def _toks(s: str) -> list[str]:
    return (s or "").split()


def _is_num(t: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+", t))


def _is_float(t: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+\.\d+", t))


class OutSpec:
    """Jak wypisywać wyniki (słowa TAK/NIE, „brak”, miejsca po przecinku, układ ciągów) — z treści i przykładów."""

    def __init__(self, samples: list[dict], words: dict | None):
        w = words or {}
        self.yes = w.get("YES") or "TAK"
        self.no = w.get("NO") or "NIE"
        self.none = w.get("NONE") or "-1"
        decs = [len(t.split(".")[1]) for s in samples for t in _toks(s["output"]) if _is_float(t)]
        self.decimals = max(decs) if decs else None
        # układ ciągów: w jednym wierszu czy po jednym w wierszu
        self.seq_col = None
        for s in samples:
            lines = [ln.split() for ln in s["output"].strip().split("\n") if ln.strip()]
            if len(lines) == 1 and len(lines[0]) > 1:
                self.seq_col = False
                break
            if len(lines) > 1 and all(len(ln) == 1 for ln in lines):
                self.seq_col = True
                break
        words_in = {t for s in samples for t in _toks(s["output"]) if not _is_num(t) and not _is_float(t)}
        self.has_words = bool(words_in)
        # słowa TAK/NIE z przykładów, gdy treść ich nie podała
        if words_in and not ({self.yes, self.no} & words_in):
            pairs = [("TAK", "NIE"), ("YES", "NO"), ("Tak", "Nie"), ("tak", "nie"), ("T", "N")]
            for a, b in pairs:
                if a in words_in or b in words_in:
                    self.yes, self.no = a, b
                    break

    def render(self, v) -> list[str]:
        if v is NONE:
            return [self.none]
        if isinstance(v, bool):
            return [self.yes if v else self.no]
        if isinstance(v, int):
            if self.decimals is not None:
                return [f"{float(v):.{self.decimals}f}"]
            return [str(v)]
        if isinstance(v, float):
            if self.decimals is None or not math.isfinite(v):
                raise Bad
            return [f"{v:.{self.decimals}f}"]
        if isinstance(v, str):
            if not v or any(c.isspace() for c in v):
                raise Bad
            return [v]
        if isinstance(v, (list, tuple)):
            out = []
            for x in v:
                out += self.render(x)
            return out
        raise Bad

    def same(self, got: list[str], exp: list[str]) -> bool:
        if len(got) != len(exp):
            return False
        for a, b in zip(got, exp):
            if a == b:
                continue
            if self.decimals is not None and _is_float(b):
                try:
                    if abs(float(a) - float(b)) <= 0.51 * 10 ** -self.decimals + 1e-9:
                        continue
                except ValueError:
                    pass
            return False
        return True

    # ---- C++
    def cpp_word(self, w: str) -> str:
        return '"' + w.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def cpp_print(self, expr: str, kind: str, none_cond: str | None = None, end: str = '"\\n"') -> str:
        """Instrukcja C++ wypisująca jedną wartość (kind: int, bool, float, str)."""
        if kind == "bool":
            s = f"cout << (({expr}) ? {self.cpp_word(self.yes)} : {self.cpp_word(self.no)}) << {end};"
        elif kind == "float" or (kind == "int" and self.decimals is not None):
            s = f"cout << fixed << setprecision({self.decimals or 6}) << (double)({expr}) << {end};"
        else:
            s = f"cout << {expr} << {end};"
        if none_cond:
            return f"if ({none_cond}) cout << {self.cpp_word(self.none)} << {end};\nelse {s}"
        return s

    def cpp_print_seq(self, vec: str, fresh, col: bool | None = None) -> str:
        col = self.seq_col if col is None else col
        i = fresh("i")
        if self.decimals is not None:
            vec = f"{vec}"
            return (f"cout << fixed << setprecision({self.decimals});\n"
                    f"for (size_t {i} = 0; {i} < {vec}.size(); {i}++) cout << (double){vec}[{i}] << "
                    f"({i} + 1 < {vec}.size() ? \" \" : \"\\n\");")
        if col:
            return f"for (size_t {i} = 0; {i} < {vec}.size(); {i}++) cout << {vec}[{i}] << \"\\n\";"
        return (f"for (size_t {i} = 0; {i} < {vec}.size(); {i}++) cout << {vec}[{i}] << "
                f"({i} + 1 < {vec}.size() ? \" \" : \"\\n\");\nif ({vec}.empty()) cout << \"\\n\";")


# ============================================================================================ wyrażenia (wzory)
def _cdiv(a, b):
    """Dzielenie całkowite jak w C++ (obcięcie w stronę zera)."""
    if b == 0:
        raise Bad
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _cmod(a, b):
    return a - b * _cdiv(a, b)


def _isqrt(a):
    if a < 0:
        raise Bad
    return math.isqrt(a)


def _digsum(a):
    return sum(int(c) for c in str(abs(a)))


def _ndig(a):
    return len(str(abs(a)))


def _rev(a):
    if a < 0:
        raise Bad
    return _ck(int(str(a)[::-1]))


def _fact(a):
    if a < 0 or a > 20:
        raise Bad
    return math.factorial(a)


def _fib(a):
    if a < 0 or a > 92:
        raise Bad
    x, y = 0, 1
    for _ in range(a):
        x, y = y, x + y
    return x


@functools.lru_cache(maxsize=200_000)
def _ndiv(a):
    if a < 1 or a > 10**12:
        raise Bad
    c, d = 0, 1
    while d * d <= a:
        if a % d == 0:
            c += 1 if d * d == a else 2
        d += 1
    return c


@functools.lru_cache(maxsize=200_000)
def _sdiv(a):
    if a < 1 or a > 10**12:
        raise Bad
    s, d = 0, 1
    while d * d <= a:
        if a % d == 0:
            s += d if d * d == a else d + a // d
        d += 1
    return _ck(s)


@functools.lru_cache(maxsize=200_000)
def _phi(a):
    if a < 1 or a > 10**12:
        raise Bad
    r, x, p = a, a, 2
    while p * p <= x:
        if x % p == 0:
            while x % p == 0:
                x //= p
            r -= r // p
        p += 1
    if x > 1:
        r -= r // x
    return r


_SIEVE: list = []
SIEVE_N = 2_000_001


def _sieve():
    if not _SIEVE:
        sv = bytearray([1]) * SIEVE_N
        sv[0] = sv[1] = 0
        for i in range(2, int(SIEVE_N ** 0.5) + 1):
            if sv[i]:
                sv[i * i::i] = bytearray(len(range(i * i, SIEVE_N, i)))
        _SIEVE.append(sv)
    return _SIEVE[0]


@functools.lru_cache(maxsize=200_000)
def _isprime(a):
    if a > 10**12:
        raise Bad
    if a < 2:
        return False
    if a < SIEVE_N:
        return bool(_sieve()[a])
    d = 2
    while d * d <= a:
        if a % d == 0:
            return False
        d += 1
    return True


def _issq(a):
    return a >= 0 and math.isqrt(a) ** 2 == a


def _ispal(a):
    return a >= 0 and str(a) == str(a)[::-1]


def _pow(a, b):
    if b < 0 or b > 62:
        raise Bad
    r = 1
    for _ in range(b):
        r = _ck(r * a)
    return r


def _binom(a, b):
    if a < 0 or a > 66:
        raise Bad
    if b < 0 or b > a:
        return 0
    return _ck(math.comb(a, b))


def _lcm(a, b):
    if a == 0 or b == 0:
        return 0
    return _ck(abs(a) // math.gcd(a, b) * abs(b))


def _ceildiv(a, b):
    if b <= 0 or a < 0:
        raise Bad
    return (a + b - 1) // b


def _tri(a):
    if a < 0:
        raise Bad
    return _ck(a * (a + 1) // 2)


def _log2(a):
    if a < 1:
        raise Bad
    return a.bit_length() - 1


def _pow2(a):
    if a < 0 or a > 62:
        raise Bad
    return 1 << a


def _popc(a):
    if a < 0:
        raise Bad
    return bin(a).count("1")


@functools.lru_cache(maxsize=200_000)
def _spf(a):
    if a < 2 or a > 10**12:
        raise Bad
    d = 2
    while d * d <= a:
        if a % d == 0:
            return d
        d += 1
    return a


@functools.lru_cache(maxsize=200_000)
def _lpf(a):
    if a < 2 or a > 10**12:
        raise Bad
    x, d, best = a, 2, 1
    while d * d <= x:
        while x % d == 0:
            best, x = d, x // d
        d += 1
    return max(best, x) if x > 1 else best


def _fdiv(a, b):
    if b == 0:
        raise Bad
    return a / b


def _fsqrt(a):
    if a < 0:
        raise Bad
    return math.sqrt(a)


# pomocnicze funkcje C++ (wstawiane tylko, gdy użyte)
CPP_HELPERS = {
    "isqrt_": "long long isqrt_(long long x) {   // część całkowita z pierwiastka\n    long long r = sqrtl((long double)x);\n"
              "    while (r * r > x) r--;\n    while ((r + 1) * (r + 1) <= x) r++;\n    return r;\n}",
    "digsum": "long long digsum(long long x) {   // suma cyfr\n    x = llabs(x);\n    long long s = 0;\n"
              "    while (x > 0) { s += x % 10; x /= 10; }\n    return s;\n}",
    "ndig": "long long ndig(long long x) {   // liczba cyfr\n    x = llabs(x);\n    long long c = 1;\n"
            "    while (x >= 10) { x /= 10; c++; }\n    return c;\n}",
    "revnum": "long long revnum(long long x) {   // liczba czytana od tyłu\n    long long r = 0;\n"
              "    while (x > 0) { r = r * 10 + x % 10; x /= 10; }\n    return r;\n}",
    "fact": "long long fact(long long x) {   // silnia\n    long long r = 1;\n    for (long long i = 2; i <= x; i++) r *= i;\n"
            "    return r;\n}",
    "fib": "long long fib(long long x) {   // F(0) = 0, F(1) = 1\n    long long a = 0, b = 1;\n"
           "    for (long long i = 0; i < x; i++) { long long c = a + b; a = b; b = c; }\n    return a;\n}",
    "ndiv": "long long ndiv(long long x) {   // liczba dzielników\n    long long c = 0;\n"
            "    for (long long d = 1; d * d <= x; d++) if (x % d == 0) c += (d * d == x ? 1 : 2);\n    return c;\n}",
    "sdiv": "long long sdiv(long long x) {   // suma dzielników\n    long long s = 0;\n"
            "    for (long long d = 1; d * d <= x; d++) if (x % d == 0) s += (d * d == x ? d : d + x / d);\n    return s;\n}",
    "phi_": "long long phi_(long long x) {   // funkcja Eulera: ile liczb z [1, x] jest względnie pierwszych z x\n"
            "    long long r = x;\n    for (long long p = 2; p * p <= x; p++)\n        if (x % p == 0) {\n"
            "            while (x % p == 0) x /= p;\n            r -= r / p;\n        }\n    if (x > 1) r -= r / x;\n    return r;\n}",
    "isprime": "bool isprime(long long x) {   // pierwszość: sito Eratostenesa (powiększane w razie potrzeby), dalej dzielniki do √x\n"
               "    static vector<char> s;\n    if (x < 2) return false;\n    if (x < (long long)s.size()) return s[x];\n"
               "    if (x <= 50000000) {\n        long long N = max<long long>(1 << 16, min<long long>(50000000, 2 * x));\n"
               "        s.assign(N + 1, 1);\n        s[0] = s[1] = 0;\n"
               "        for (long long i = 2; i * i <= N; i++) if (s[i]) for (long long j = i * i; j <= N; j += i) s[j] = 0;\n"
               "        return s[x];\n    }\n    for (long long d = 2; d * d <= x; d++) if (x % d == 0) return false;\n    return true;\n}",
    "issq": "bool issq(long long x) {   // czy kwadrat liczby całkowitej\n    if (x < 0) return false;\n"
            "    long long r = sqrtl((long double)x);\n    while (r * r > x) r--;\n    while ((r + 1) * (r + 1) <= x) r++;\n"
            "    return r * r == x;\n}",
    "ispalnum": "bool ispalnum(long long x) {   // czy liczba jest palindromem\n    if (x < 0) return false;\n"
                "    string s = to_string(x);\n    return s == string(s.rbegin(), s.rend());\n}",
    "ipow": "long long ipow(long long a, long long b) {   // a^b (b ≤ 62)\n    long long r = 1;\n"
            "    for (long long i = 0; i < b; i++) r *= a;\n    return r;\n}",
    "binom": "long long binom(long long n, long long k) {   // dwumian Newtona\n    if (k < 0 || k > n) return 0;\n"
             "    long long r = 1;\n    for (long long i = 1; i <= k; i++) r = r * (n - k + i) / i;\n    return r;\n}",
    "spf": "long long spf(long long x) {   // najmniejszy dzielnik pierwszy\n"
           "    for (long long d = 2; d * d <= x; d++) if (x % d == 0) return d;\n    return x;\n}",
    "lpf": "long long lpf(long long x) {   // największy dzielnik pierwszy\n    long long best = 1;\n"
           "    for (long long d = 2; d * d <= x; d++) while (x % d == 0) { best = d; x /= d; }\n"
           "    return x > 1 ? max(best, x) : best;\n}",
    "powmod": "long long powmod(long long a, long long b, long long m) {   // szybkie potęgowanie modulo\n"
              "    long long r = 1 % m;\n    a %= m;\n    if (a < 0) a += m;\n    while (b > 0) {\n"
              "        if (b & 1) r = (__int128)r * a % m;\n        a = (__int128)a * a % m;\n        b >>= 1;\n    }\n"
              "    return r;\n}",
}
_HELPER_ORDER = list(CPP_HELPERS)

# operacje: nazwa → (argumenty, wynik, funkcja Pythona, format C++, koszt, tagi, pomocnicze C++)
OPS: dict[str, tuple] = {
    "add": ("ii", "i", lambda a, b: _ck(a + b), "({0} + {1})", 1.0, (), ()),
    "sub": ("ii", "i", lambda a, b: _ck(a - b), "({0} - {1})", 1.0, ("diff",), ()),
    "mul": ("ii", "i", lambda a, b: _ck(a * b), "({0} * {1})", 1.0, ("product",), ()),
    "div": ("ii", "i", _cdiv, "({0} / {1})", 1.2, ("div",), ()),
    "mod": ("ii", "i", _cmod, "({0} % {1})", 1.2, ("mod",), ()),
    "max": ("ii", "i", max, "max({0}, {1})", 1.2, ("max",), ()),
    "min": ("ii", "i", min, "min({0}, {1})", 1.2, ("min",), ()),
    "gcd": ("ii", "i", math.gcd, "gcd({0}, {1})", 1.3, ("gcd",), ()),
    "lcm": ("ii", "i", _lcm, "lcm({0}, {1})", 1.3, ("lcm",), ()),
    "pow": ("ii", "i", _pow, "ipow({0}, {1})", 1.4, ("pow",), ("ipow",)),
    "absdiff": ("ii", "i", lambda a, b: _ck(abs(a - b)), "llabs({0} - {1})", 1.3, ("diff",), ()),
    "ceildiv": ("ii", "i", _ceildiv, "(({0} + {1} - 1) / {1})", 1.5, ("div",), ()),
    "binom": ("ii", "i", _binom, "binom({0}, {1})", 1.5, ("binom",), ("binom",)),
    "abs": ("i", "i", lambda a: _ck(abs(a)), "llabs({0})", 1.2, ("abs",), ()),
    "sq": ("i", "i", lambda a: _ck(a * a), "({0} * {0})", 1.3, ("square",), ()),
    "isqrt": ("i", "i", _isqrt, "isqrt_({0})", 1.5, ("sqrt",), ("isqrt_",)),
    "popc": ("i", "i", _popc, "__builtin_popcountll({0})", 1.6, ("binary",), ()),
    "digsum": ("i", "i", _digsum, "digsum({0})", 1.5, ("digits",), ("digsum",)),
    "ndig": ("i", "i", _ndig, "ndig({0})", 1.6, ("digits",), ("ndig",)),
    "rev": ("i", "i", _rev, "revnum({0})", 1.6, ("reverse",), ("revnum",)),
    "fact": ("i", "i", _fact, "fact({0})", 1.6, ("fact",), ("fact",)),
    "fib": ("i", "i", _fib, "fib({0})", 1.6, ("fib",), ("fib",)),
    "tri": ("i", "i", _tri, "({0} * ({0} + 1) / 2)", 1.6, ("sum",), ()),
    "ndiv": ("i", "i", _ndiv, "ndiv({0})", 1.5, ("divisors",), ("ndiv",)),
    "sdiv": ("i", "i", _sdiv, "sdiv({0})", 1.6, ("divisors",), ("sdiv",)),
    "phi": ("i", "i", _phi, "phi_({0})", 1.8, ("coprime",), ("phi_",)),
    "spf": ("i", "i", _spf, "spf({0})", 1.8, ("prime",), ("spf",)),
    "lpf": ("i", "i", _lpf, "lpf({0})", 1.8, ("prime",), ("lpf",)),
    "log2": ("i", "i", _log2, "(63 - __builtin_clzll({0}))", 1.7, ("log",), ()),
    "pow2": ("i", "i", _pow2, "(1LL << {0})", 1.6, ("pow",), ()),
    # predykaty (wynik: prawda/fałsz → słowa z treści)
    "isprime": ("i", "b", _isprime, "isprime({0})", 1.3, ("prime",), ("isprime",)),
    "issq": ("i", "b", _issq, "issq({0})", 1.5, ("square",), ("issq",)),
    "even": ("i", "b", lambda a: a % 2 == 0, "({0} % 2 == 0)", 1.2, ("even",), ()),
    "odd": ("i", "b", lambda a: a % 2 != 0, "({0} % 2 != 0)", 1.2, ("odd",), ()),
    "ispal": ("i", "b", _ispal, "ispalnum({0})", 1.6, ("palin",), ("ispalnum",)),
    "lt": ("ii", "b", lambda a, b: a < b, "({0} < {1})", 1.0, (), ()),
    "le": ("ii", "b", lambda a, b: a <= b, "({0} <= {1})", 1.0, (), ()),
    "eq": ("ii", "b", lambda a, b: a == b, "({0} == {1})", 1.0, ("equal",), ()),
    "ne": ("ii", "b", lambda a, b: a != b, "({0} != {1})", 1.1, (), ()),
    "dvd": ("ii", "b", lambda a, b: b != 0 and a % b == 0, "({1} != 0 && {0} % {1} == 0)", 1.2, ("div",), ()),
    # liczby rzeczywiste
    "fdiv": ("ii", "f", _fdiv, "((double){0} / {1})", 1.2, ("avg",), ()),
    "fsqrt": ("i", "f", _fsqrt, "sqrt((double){0})", 1.4, ("sqrt",), ()),
}
_COMM = {"add", "mul", "max", "min", "gcd", "lcm", "eq", "ne"}
_HEAVY = {"ndiv", "sdiv", "phi", "isprime", "spf", "lpf", "fib", "fact"}
_UN_I = [k for k, v in OPS.items() if v[0] == "i" and v[1] == "i"]
_UN_B = [k for k, v in OPS.items() if v[0] == "i" and v[1] == "b"]
_BIN_I = [k for k, v in OPS.items() if v[0] == "ii" and v[1] == "i"]
_BIN_B = [k for k, v in OPS.items() if v[0] == "ii" and v[1] == "b"]


class Ex:
    """Wyrażenie: atom (zmienna/agregat/stała) albo operacja. `vec` = wartości we wszystkich punktach."""
    __slots__ = ("op", "args", "vec", "cost", "size", "typ", "tags", "cpp_atom", "desc_atom", "const", "helpers")

    def __init__(self, op, args, vec, cost, size, typ, tags=(), cpp_atom=None, desc_atom=None, const=False,
                 helpers=()):
        self.op, self.args, self.vec, self.cost, self.size, self.typ = op, args, vec, cost, size, typ
        self.tags, self.cpp_atom, self.desc_atom, self.const, self.helpers = tags, cpp_atom, desc_atom, const, helpers

    def cpp(self) -> str:
        if self.op == "atom":
            return self.cpp_atom
        return OPS[self.op][3].format(*(a.cpp() for a in self.args))

    def text(self) -> str:
        if self.op == "atom":
            return self.desc_atom
        fmt = {"add": "({0} + {1})", "sub": "({0} − {1})", "mul": "{0}·{1}", "div": "⌊{0} / {1}⌋", "mod": "({0} mod {1})",
               "max": "max({0}, {1})", "min": "min({0}, {1})", "gcd": "NWD({0}, {1})", "lcm": "NWW({0}, {1})",
               "pow": "{0}^{1}", "absdiff": "|{0} − {1}|", "ceildiv": "⌈{0} / {1}⌉", "binom": "C({0}, {1})",
               "abs": "|{0}|", "sq": "{0}²", "isqrt": "⌊√{0}⌋", "popc": "jedynki_bin({0})", "digsum": "suma_cyfr({0})",
               "ndig": "liczba_cyfr({0})", "rev": "odwróć({0})", "fact": "{0}!", "fib": "Fib({0})",
               "tri": "1+2+…+{0}", "ndiv": "liczba_dzielników({0})", "sdiv": "suma_dzielników({0})",
               "phi": "φ({0})", "spf": "najmn_dzielnik_pierwszy({0})", "lpf": "najw_dzielnik_pierwszy({0})",
               "log2": "⌊log₂ {0}⌋", "pow2": "2^{0}", "isprime": "{0} jest pierwsza", "issq": "{0} jest kwadratem",
               "even": "{0} parzyste", "odd": "{0} nieparzyste", "ispal": "{0} jest palindromem", "lt": "{0} < {1}",
               "le": "{0} ≤ {1}", "eq": "{0} = {1}", "ne": "{0} ≠ {1}", "dvd": "{1} dzieli {0}", "fdiv": "{0} / {1}",
               "fsqrt": "√{0}"}[self.op]
        return fmt.format(*(a.text() for a in self.args))

    def all_tags(self) -> set:
        out = set(self.tags)
        for a in self.args or ():
            out |= a.all_tags()
        return out

    def all_helpers(self) -> list:
        out = list(self.helpers)
        if self.op != "atom":
            out += list(OPS[self.op][6])
        for a in self.args or ():
            out += a.all_helpers()
        return out

    def eval(self, vals: dict):
        """Wartość w nowym punkcie (atomy: vals[desc_atom])."""
        if self.op == "atom":
            v = vals.get(self.desc_atom) if not self.const else self.vec[0]
            if v is None:
                raise Bad
            return v
        args = [a.eval(vals) for a in self.args]
        try:
            return OPS[self.op][2](*args)
        except (ZeroDivisionError, ValueError, OverflowError):
            raise Bad from None


def _apply(op, vecs):
    f = OPS[op][2]
    out = []
    for xs in zip(*vecs):
        if None in xs:
            out.append(None)
            continue
        try:
            out.append(f(*xs))
        except (Bad, ZeroDivisionError, ValueError, OverflowError, TypeError):
            out.append(None)
    return tuple(out)


def enum_formulas(atoms: list[Ex], ns: int, check, kinds: set, max_size: int = 5, deadline: float = 0.0,
                  cap: int = 40_000) -> list[Ex]:
    """Wyliczanie wyrażeń od najmniejszych; `vec[:ns]` = punkty z przykładów, reszta = losowe punkty (do utożsamiania
    wyrażeń równoważnych). `check(vec_prefix, typ)` → czy wyrażenie daje oczekiwane wyjście. Zwraca pasujące wyrażenia."""
    seen: dict = {}
    levels: dict[int, list[Ex]] = {1: []}
    found: list[Ex] = []

    def add(e: Ex):
        if any(v is None for v in e.vec[:ns]):
            return
        key = (e.typ, e.vec)
        old = seen.get(key)
        if old is not None and old.cost <= e.cost:
            return
        seen[key] = e
        levels.setdefault(e.size, []).append(e)
        if e.typ in kinds and check(e.vec[:ns], e.typ):
            found.append(e)

    for a in atoms:
        add(a)
    count = len(atoms)
    for size in range(2, max_size + 1):
        if time.time() > deadline or count > cap:
            break
        # jednoargumentowe (kosztowne funkcje teorii liczb tylko na małych wyrażeniach)
        for e in list(levels.get(size - 1, [])):
            if e.typ != "i" or e.const:
                continue
            if count % 64 == 0 and time.time() > deadline:
                break
            for op in _UN_I + (_UN_B if "b" in kinds else []):
                if e.op == op and op in ("abs", "sq"):
                    continue
                if op in _HEAVY and e.size > 1:
                    continue
                if op not in ("abs", "sq") and e.size > 2:
                    continue
                spec = OPS[op]
                vec = _apply(op, [e.vec])
                add(Ex(op, (e,), vec, e.cost + spec[4], size, spec[1], spec[5]))
                count += 1
            if "f" in kinds:
                vec = _apply("fsqrt", [e.vec])
                add(Ex("fsqrt", (e,), vec, e.cost + OPS["fsqrt"][4], size, "f", ("sqrt",)))
        # dwuargumentowe
        for ls in range(1, size - 1):
            rs = size - 1 - ls
            L = [e for e in levels.get(ls, []) if e.typ == "i"]
            R = [e for e in levels.get(rs, []) if e.typ == "i"]
            for i, a in enumerate(L):
                if time.time() > deadline or count > cap:
                    break
                if time.time() > deadline:
                    break
                for j, b in enumerate(R):
                    if a.const and b.const:
                        continue
                    for op in _BIN_I + (_BIN_B if "b" in kinds else []) + (["fdiv"] if "f" in kinds else []):
                        if op in _COMM and ls == rs and j < i:
                            continue
                        if op in _COMM and ls > rs:
                            continue
                        spec = OPS[op]
                        if op in ("pow", "binom") and not b.const and b.size > 1:
                            continue
                        vec = _apply(op, [a.vec, b.vec])
                        add(Ex(op, (a, b), vec, a.cost + b.cost + spec[4], size, spec[1], spec[5]))
                        count += 1
    found.sort(key=lambda e: (e.cost, e.size))
    return found


# ============================================================================================ słowa treści
TAGS = {
    "pairs": r"\bpar(?:a|y|e|ami|ach|om|\b)|dw[oó]ch (?:r[oó]znych )?(?:liczb|element|indeks|pozycj|os[oó]b)|"
             r"dwie (?:r[oó]zne )?(?:liczby|osoby|pozycje)|i ?< ?j\b",
    "triples": r"tr[oó]jk|trzech (?:r[oó]znych )?(?:liczb|element|patyk|odcink)|trzy (?:r[oó]zne )?(?:liczby|patyki|odcinki)",
    "subarray": r"sp[oó]jn\w* (?:fragment|podci[aą]g|przedzia|podtablic|kawa[lł]|ci[aą]g|okres|odcin|blok|seri)|fragment\w*|"
                r"przedzia[lł]\w*|"
                r"kolejn\w* (?:dni|liczb|element|p[oó]l|os[oó]b)|podtablic|segment",
    "subset": r"podzbi[oó]r|podzbior|wybra[cćn]\w*|wybier\w*",
    "perm": r"permutacj|ustawie[nń]|przestaw",
    "substr": r"pods[lł]ow|podnapis|fragment\w* (?:napisu|s[lł]owa|tekstu)",
    "sum": r"\bsum\w*|dodaj\w*|[lł][aą]cznie|\b[a-z]_?\{?[ijkl]\}?\s*\+\s*[a-z]_?\{?[ijkl]\}?",
    "product": r"iloczyn|pomn[oó][zż]|\b[a-z]_?\{?[ijkl]\}?\s*[·*×]\s*[a-z]_?\{?[ijkl]\}?",
    "max": r"najwi[eę]ksz|maksym|najd[lł]u[zż]sz|najwy[zż]sz|najwi[eę]cej|najdalej|najlepsz",
    "min": r"najmniejsz|minim|najkr[oó]tsz|najni[zż]sz|najmniej|najbli[zż]sz|najta[nń]sz",
    "count": r"\bile\b|liczb\w* (?:par|fragment|podzbior|spos|element|takich|wyst[aą]pie)|zlicz|policz",
    "exists": r"\bczy\b|istnieje|da si[eę]",
    "even": r"(?<!nie)parzyst",
    "odd": r"nieparzyst",
    "prime": r"liczb\w* pierwsz|pierwsz\w* liczb|pierwszo[sś][cć]|czynnik\w* pierwsz|dzielnik\w* pierwsz",
    "div": r"podziel|dzieli|dzielnik|wielokrotno|reszt",
    "mod": r"modulo|reszt",
    "divisors": r"dzielnik",
    "coprime": r"wzgl[eę]dnie pierwsz",
    "gcd": r"\bnwd\b|najwi[eę]ksz\w* wsp[oó]ln\w* dzielnik",
    "lcm": r"\bnww\b|najmniejsz\w* wsp[oó]ln\w* wielokrotn",
    "distinct": r"r[oó][zż]n\w* (?:liczb|warto|element|znak|liter|s[lł]ow|kolor|typ|rodzaj)|unikal|niepowtarzaj",
    "diff": r"r[oó][zż]nic|\b[a-z]_?\{?[ijkl]\}?\s*[−-]\s*[a-z]_?\{?[ijkl]\}?|\|\s*[a-z]_?\{?[ijkl]\}?\s*[−-]",
    "xor": r"\bxor\b|alternatyw\w* wykluczaj",
    "avg": r"[sś]redni",
    "median": r"median",
    "sort": r"posortuj|uporz[aą]dk|rosn[aą]c|malej[aą]c|sortow",
    "reverse": r"odwr[oó][cć]|od ty[lł]u|od ko[nń]ca|wspak",
    "palin": r"palindrom",
    "anagram": r"anagram",
    "dist": r"odleg[lł]o[sś]|najkr[oó]tsz\w* (?:drog|[sś]cie[zż]k|tras|czas)|najmniej\w* (?:krok|ruch|kraw[eę]dzi|przesiad|"
            r"skok|minut)|dotrze[cć]|doj[sś][cć]|dojecha[cć]",
    "comp": r"sp[oó]jn\w* sk[lł]adow|sk[lł]adow|grup\w*|wysp|obszar|plam|jezior",
    "connected": r"sp[oó]jn",
    "cycle": r"cykl",
    "tree": r"drzew|korze[nń]|li[sś][cć]",
    "reach": r"osi[aą]galn|dotrze[cć]|doj[sś][cć]|dosta[cć] si[eę]|dojecha[cć]",
    "square": r"kwadrat",
    "sqrt": r"pierwiast",
    "digits": r"cyfr",
    "len": r"d[lł]ugo[sś]",
    "equal": r"r[oó]wn\w*|takie same|takich samych|jednakow",
    "inversion": r"inwersj|zamian\w* s[aą]siedn|b[aą]belk",
    "pow": r"pot[eę]g|\^",
    "fact": r"silni",
    "fib": r"fibonacc",
    "binom": r"dwumian|newton",
    "binary": r"binarn|dw[oó]jkow|\bbit",
    "char": r"liter|znak",
    "vowel": r"samog[lł]osk",
    "kth": r"\bk-?t(?:y|ego|ej|a|ym|ych)\b|\bk[- ]?t[aey]?\s+(?:najmniejsz|najwieksz|element|liczb)",
    "position": r"pozycj|indeks|numer",
    "prefix": r"prefiks",
    "window": r"\bk kolejnych|po kolei|kolejnych \w+",
    "degree": r"stopie[nń]|stopnie|s[aą]siad",
    "depth": r"g[lł][eę]boko|wysoko[sś]",
    "diameter": r"[sś]rednic|najdalsz",
    "bipartite": r"dwudziel|pokoloro",
    "topo": r"topolog",
    "mst": r"rozpinaj",
    "leaf": r"li[sś][cć]",
    "record": r"rekord|wi[eę]ksz\w* od wszystkich",
    "run": r"seri[aie]|kolejn\w* (?:r[oó]wn|takich samych)",
    "increasing": r"rosn[aą]c",
    "triangle": r"tr[oó]jk[aą]t",
    "lis": r"rosn[aą]c\w* podci[aą]g|podci[aą]g\w* rosn[aą]c",
    "lcs": r"wsp[oó]ln\w* podci[aą]g",
    "edit": r"edycyj",
    "subseq": r"podci[aą]g",
    "adjacent": r"s[aą]siedn|obok siebie",
    "query": r"zapyta",
    "path": r"[sś]cie[zż]k|w prawo|w d[oó][lł]|prawo lub w d|w g[oó]r[eę]|kroki?\b|ruch\w*",
    "diag": r"po skosie|przek[aą]tn|na ukos|8 kierunk|o[sś]miu kierunk|o[sś]miu s[aą]siad|8 s[aą]siad|naro[zż]nik",
    "directed": r"(?<!nie)skierowan|jednokierunkow|w jedn\w* stron|tylko z \w+ do",
    "undirected": r"nieskierowan|dwukierunkow|w obie strony|obustronn",
    "atmost": r"nie przekracza|co najwy[zż]ej|nie wi[eę]c\w* ni[zż]|nie wi[eę]ksz|mniejsz\w* lub r[oó]wn|\bdo\b \w+ w[lł][aą]cznie",
    "atleast": r"co najmniej|nie mniej|nie mniejsz|przynajmniej|wi[eę]ksz\w* lub r[oó]wn",
    "occur": r"wyst[eę]p|wyst[aą]pi|pojawi",
    "decreasing": r"(?<!nie)malej[aą]c",
    "nondecreasing": r"niemalej[aą]c",
    "abs": r"warto[sś][cć] bezwzgl",
    "log": r"logarytm",
    "upper": r"wielk\w* liter",
    "lower": r"ma[lł]\w* liter",
}
GENERIC = {"count", "exists", "max", "min", "len", "position", "equal", "window", "query", "sum"}
_TAG_RX = {k: re.compile(v) for k, v in TAGS.items()}


def keyword_hits(parsed: dict, text: str = "") -> set:
    sec = parsed.get("sections") or {}
    blob = " ".join([parsed.get("title") or "", sec.get("tresc", ""), sec.get("wyjscie", ""), sec.get("ocenianie", "")])
    if not blob.strip():
        blob = text
    low = re.sub(r"\s+", " ", T.strip_accents(blob).lower())
    # frazy, w których „największy”/„najmniejszy” nie znaczy maksimum/minimum
    low = re.sub(r"najwieksz\w* wspoln\w* dzielnik\w*", " nwd ", low)
    low = re.sub(r"najmniejsz\w* wspoln\w* wielokrotn\w*", " nww ", low)
    return {k for k, rx in _TAG_RX.items() if rx.search(low)}


def statement_consts(parsed: dict) -> list[int]:
    """Małe stałe z treści (np. „podzielnych przez 3”, „modulo 10^9+7”) — kandydaci do wzorów."""
    sec = parsed.get("sections") or {}
    blob = " ".join([sec.get("tresc", ""), sec.get("wyjscie", "")])
    out: list[int] = []
    for m in re.finditer(r"(?<![\w.^])(\d{1,4})(?![\w.^]|\s*[\^·*]\s*\d)", blob):
        v = int(m.group(1))
        if 2 < v <= 1000 and v not in out and not (1900 <= v <= 2100):
            out.append(v)
    low = T.strip_accents(blob).lower()
    mm = re.search(r"modulo\s*(\d[\d ^+*·]*\d|\d+)", low) or re.search(r"reszt\w* z dzielenia\D{0,70}?(\d[\d ^+*·]*\d|\d+)", low)
    if mm:
        v = ST.parse_value(mm.group(1).strip())
        if isinstance(v, int) and 1 < v < 2**62 and v not in out:
            out.insert(0, v)
    return out[:6]


# ============================================================================================ kandydaci
class Cand:
    """Program z języka syntezy: `run(env)` (interpreter) + `cpp(ctx)` (kod C++ ciała, wypisuje wynik)."""
    __slots__ = ("key", "desc", "fam", "cost", "tags", "run", "cpp", "cx", "work", "score", "aligned", "sig",
                 "checked", "skipped", "helpers", "cls", "nspec")

    def __init__(self, key, desc, fam, cost, tags, run, cpp, cx="n", work=None, helpers=()):
        self.key, self.desc, self.fam, self.cost, self.tags = key, desc, fam, cost, set(tags)
        self.run, self.cpp, self.cx, self.work, self.helpers = run, cpp, cx, work, tuple(helpers)
        self.score = 0.0
        self.aligned = False
        self.nspec = 0
        self.sig = None
        self.checked = 0
        self.skipped = 0
        self.cls = None


class Sample:
    __slots__ = ("name", "env", "exp", "source", "big", "size", "out")

    def __init__(self, name, env, exp, source, big, size, out):
        self.name, self.env, self.exp, self.source, self.big, self.size, self.out = name, env, exp, source, big, size, out


class Ctx:
    """Wszystko, czego potrzebują rodziny kandydatów: plan, role zmiennych, przykłady, losowe wejścia, słowa treści."""

    def __init__(self, plan: Plan, out: OutSpec, samples: list[Sample], rnd: list[tuple[str, dict]], hits: set,
                 consts: list[int], deadline: float):
        self.plan, self.out, self.samples, self.rnd, self.hits, self.consts = plan, out, samples, rnd, hits, consts
        self.deadline = deadline
        self.unit = plan.top()
        self.mt = plan.mt is not None
        self.evs = [s for s in samples if s.env is not None]
        # jednostki (zestawy przy multiteście) z przykładów i z losowych wejść
        self.units = [u for s in self.evs for u in self.units_of(s.env)]
        self.rnd_units = [u for _, e in rnd for u in self.units_of(e)]
        self.roles = _roles(self)

    def units_of(self, env):
        return env["#tests"] if self.mt else [env]

    def time_left(self):
        return self.deadline - time.time()


def _roles(ctx: Ctx) -> dict:
    unit = ctx.unit
    sizes = set()
    for it in unit:
        for L in (it.length, it.count):
            if L is not None and L.kind == "var":
                sizes.add(L.var)
    ints = [nm for it in unit if it.kind == "scal" for nm, t in zip(it.names, it.types) if t == "i"]
    arrays = [(it.name, it.length.var if it.length is not None and it.length.kind == "var" else None)
              for it in unit if it.kind == "arr" and it.typ == "i"]
    rows = [it for it in unit if it.kind == "rows" and all(t == "i" for t in it.types)]
    params = [v for v in ints if v not in sizes]
    cols = [(f, R.count.var if R.count is not None and R.count.kind == "var" else None) for R in rows for f in R.names]
    return {"ints": ints, "sizes": sizes, "params": params, "arrays": arrays, "rows": rows, "cols": cols,
            "strs": [it.name for it in unit if it.kind == "str"],
            "grids": [it for it in unit if it.kind == "grid"],
            "mats": [it for it in unit if it.kind == "mat" and it.typ == "i"],
            "ops": [it for it in unit if it.kind == "ops"],
            "mixed_rows": [it for it in unit if it.kind == "rows" and not all(t == "i" for t in it.types)],
            "order": [it.kind for it in unit]}


def _unit_tokens(ctx: Ctx, c: Cand, env) -> list[str]:
    out: list[str] = []
    for u in ctx.units_of(env):
        out += ctx.out.render(c.run(u))
    return out


def _work(ctx: Ctx, c: Cand, env) -> float:
    if c.work is None:
        return 0
    return sum(c.work(u) for u in ctx.units_of(env))


def check_cand(ctx: Ctx, c: Cand) -> bool:
    """Czy kandydat daje oczekiwane wyjście na WSZYSTKICH przykładach, które da się zinterpretować w Pythonie."""
    checked = skipped = 0
    for s in ctx.samples:
        if s.env is None:
            skipped += 1
            continue
        try:
            if _work(ctx, c, s.env) > PY_WORK:
                skipped += 1
                continue
            got = _unit_tokens(ctx, c, s.env)
        except (Bad, ZeroDivisionError, ValueError, OverflowError, IndexError, KeyError, TypeError, RecursionError):
            return False
        if not ctx.out.same(got, s.exp):
            return False
        checked += 1
    c.checked, c.skipped = checked, skipped
    return checked > 0


def signature(ctx: Ctx, c: Cand) -> tuple:
    sig = []
    for _, env in ctx.rnd:
        try:
            if _work(ctx, c, env) > PY_WORK:
                sig.append(None)
                continue
            sig.append(tuple(_unit_tokens(ctx, c, env)))
        except (Bad, ZeroDivisionError, ValueError, OverflowError, IndexError, KeyError, TypeError, RecursionError):
            sig.append(None)
    return tuple(sig)


def _differs(a: tuple, b: tuple) -> int | None:
    """Indeks pierwszego losowego wejścia, na którym obie sygnatury są określone i różne."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x is not None and y is not None and x != y:
            return i
    return None


# pojęcia, których użycie bez wzmianki w treści jest mało prawdopodobne („podzielne”, „pierwsza”, „xor”…)
CONCEPTS = {"diff", "div", "mod", "prime", "xor", "gcd", "lcm", "square", "sqrt", "palin", "digits", "fib", "fact", "binom",
            "binary", "coprime", "divisors", "triangle", "anagram", "pow", "avg", "median", "product", "even", "odd",
            "vowel", "upper", "lower", "log", "inversion", "record", "bipartite", "topo", "mst", "edit", "lcs", "kth",
            "reverse", "prefix", "abs", "char", "depth", "diameter", "leaf", "degree", "cycle", "lis", "run", "decreasing",
            "nondecreasing", "directed", "diag", "pairs", "triples", "subarray", "subset", "perm", "substr", "subseq"}


SOFT_CONCEPTS = {"sum"}      # ogólne, ale liczenie sumy bez słowa „suma”/„łącznie” w treści jest mało prawdopodobne


def score_cand(ctx: Ctx, c: Cand) -> None:
    spec = [t for t in c.tags if t in ctx.hits and t not in GENERIC]
    gen = [t for t in c.tags if t in ctx.hits and t in GENERIC]
    miss = [t for t in c.tags if t in CONCEPTS and t not in ctx.hits]
    soft = [t for t in c.tags if t in SOFT_CONCEPTS and t not in ctx.hits]
    c.aligned = bool(spec)
    c.nspec = len(spec)
    unused = _unused_params(ctx, c)
    c.score = round(-c.cost + min(4.5, 1.5 * len(spec) + 0.4 * len(gen)) - 1.5 * len(miss) - 1.0 * len(soft)
                    - UNUSED_PENALTY * len(unused), 3)


UNUSED_PENALTY = 0.8         # program, który w ogóle nie używa liczby podanej na wejściu (np. s, t, k), jest mniej wiarygodny


def _unused_params(ctx: Ctx, c: Cand) -> list[str]:
    params = ctx.roles["params"]
    if not params:
        return []
    try:
        body = c.cpp(Gen(ctx))
    except Exception:  # noqa: BLE001
        return []
    ids = set(re.findall(r"[A-Za-z_]\w*", body))
    return [v for v in params if v not in ids]


# ============================================================================================ generowanie C++
def _t(tpl: str, **kw) -> str:
    """Szablon C++ z polami «nazwa» (nawiasy klamrowe C++ zostają nietknięte)."""
    for k, v in kw.items():
        tpl = tpl.replace("«" + k + "»", str(v))
    return tpl


class Gen:
    """Kontekst generowania kodu: świeże nazwy, wypisywanie wg treści, potrzebne funkcje pomocnicze."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.f = Fresh(ctx.plan.used)
        self.o = ctx.out
        self.helpers: list[str] = []

    def need(self, *hs):
        for h in hs:
            if h and h not in self.helpers:
                self.helpers.append(h)

    def names(self, *bases) -> dict:
        return {b: self.f(b) for b in bases}

    def pr(self, expr: str, kind: str, none_cond: str | None = None) -> str:
        return self.o.cpp_print(expr, kind, none_cond)

    def seq(self, vec: str) -> str:
        return self.o.cpp_print_seq(vec, self.f)


def _kind_of(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    return "int"


def _exp_kinds(ctx: Ctx, exps: list[list[str]]) -> set:
    toks = [t for e in exps for t in e]
    if not toks:
        return set()
    if all(_is_num(t) for t in toks):
        return {"i"}
    if all(t in (ctx.out.yes, ctx.out.no) for t in toks):
        return {"b"}
    if all(_is_num(t) or _is_float(t) for t in toks):
        return {"i", "f"}
    return set()


def _unit_exps(ctx: Ctx, per_unit_len) -> list[list[str]] | None:
    """Podział oczekiwanych tokenów na jednostki (zestawy), gdy każda jednostka daje `per_unit_len(u)` tokenów."""
    out = []
    for s in ctx.evs:
        units = ctx.units_of(s.env)
        need = [per_unit_len(u) for u in units]
        if sum(need) != len(s.exp):
            return None
        p = 0
        for k in need:
            out.append(s.exp[p:p + k])
            p += k
    return out


def _consts_atoms(ctx: Ctx, extra_small=(1, 2)) -> list[dict]:
    out = []
    for v in extra_small:
        out.append({"name": str(v), "cpp": f"{v}LL", "get": (lambda p, v=v: v), "cost": 1.0 if v == 1 else 1.3,
                    "const": True})
    for v in ctx.consts:
        if v in extra_small:
            continue
        out.append({"name": str(v), "cpp": f"{v}LL", "get": (lambda p, v=v: v),
                    "cost": 1.6, "const": True, "tags": ("mod",) if v > 1000 else ()})
    return out


def _formula_search(ctx: Ctx, points: list, exps: list[list[str]], rnd_points: list, atoms: list[dict],
                    max_size: int, budget: float) -> list[Ex]:
    kinds = _exp_kinds(ctx, exps)
    if not kinds or not points:
        return []
    ns = len(points)
    ex_atoms = []
    for a in atoms:
        vec = []
        for p in list(points) + list(rnd_points):
            try:
                v = a["get"](p)
            except (Bad, ZeroDivisionError, ValueError, IndexError, KeyError, TypeError, OverflowError):
                v = None
            if isinstance(v, bool) or not isinstance(v, int) or not (LL_MIN <= v <= LL_MAX):
                v = None
            vec.append(v)
        ex_atoms.append(Ex("atom", None, tuple(vec), a["cost"], 1, "i", a.get("tags", ()), cpp_atom=a["cpp"],
                           desc_atom=a["name"], const=a.get("const", False), helpers=a.get("helpers", ())))
    out = ctx.out

    def check(vec, typ):
        for v, e in zip(vec, exps):
            try:
                tok = out.render(bool(v) if typ == "b" else v)
            except Bad:
                return False
            if not out.same(tok, e):
                return False
        return True
    if len(ex_atoms) > 7:
        max_size = min(max_size, 4)
    return enum_formulas(ex_atoms, ns, check, kinds, max_size=max_size, deadline=time.time() + budget)


def _ex_atoms_used(ex: Ex) -> list[Ex]:
    if ex.op == "atom":
        return [ex]
    out = []
    for a in ex.args:
        out += _ex_atoms_used(a)
    return out


def _ex_cpp(ex: Ex, G: Gen, atoms_by_name: dict, repl: dict | None = None) -> tuple[str, str]:
    """(kod przygotowujący agregaty, wyrażenie C++)."""
    pre = []
    expr = ex.cpp()
    done = set()
    for a in _ex_atoms_used(ex):
        info = atoms_by_name.get(a.desc_atom) or {}
        if info.get("pre") and a.desc_atom not in done:
            done.add(a.desc_atom)
            nm = G.f(info.get("local", "v"))
            pre.append(_t(info["pre"], v=nm, w=G.f("x")))
            expr = expr.replace("@" + a.desc_atom + "@", nm)
    for k, v in (repl or {}).items():
        expr = expr.replace(k, v)
    G.need(*ex.all_helpers())
    return "\n".join(pre), expr


def _ex_value(ex: Ex, vals: dict):
    v = ex.eval(vals)
    return bool(v) if ex.typ == "b" else v


def _array_agg_atoms(ctx: Ctx) -> list[dict]:
    out = []
    for a, _ln in ctx.roles["arrays"]:
        out += [
            {"name": f"suma({a})", "cpp": f"@suma({a})@", "local": "suma", "pre":
             f"long long «v» = 0;\nfor (long long «w» : {a}) «v» += «w»;",
             "get": (lambda u, a=a: _ck(sum(u[a]))), "cost": 1.4, "tags": ("sum",)},
            {"name": f"max({a})", "cpp": f"@max({a})@", "local": "maks",
             "pre": f"long long «v» = *max_element({a}.begin(), {a}.end());",
             "get": (lambda u, a=a: max(u[a]) if u[a] else None), "cost": 1.5, "tags": ("max",)},
            {"name": f"min({a})", "cpp": f"@min({a})@", "local": "mini",
             "pre": f"long long «v» = *min_element({a}.begin(), {a}.end());",
             "get": (lambda u, a=a: min(u[a]) if u[a] else None), "cost": 1.5, "tags": ("min",)},
            {"name": f"|{a}|", "cpp": f"(long long){a}.size()", "get": (lambda u, a=a: len(u[a])), "cost": 1.5},
        ]
    for s in ctx.roles["strs"]:
        out.append({"name": f"|{s}|", "cpp": f"(long long){s}.size()", "get": (lambda u, s=s: len(u[s])), "cost": 1.4,
                    "tags": ("len",)})
    return out


def fam_formula(ctx: Ctx):
    """Jeden wynik na zestaw: wzór z liczb wejścia (i agregatów ciągów) oraz stałych z treści."""
    exps = _unit_exps(ctx, lambda u: 1)
    if exps is None:
        return
    atoms = [{"name": v, "cpp": v, "get": (lambda u, v=v: u[v]), "cost": 1.0} for v in ctx.roles["ints"]]
    atoms += _array_agg_atoms(ctx)
    if not atoms:
        return
    atoms += _consts_atoms(ctx)
    by = {a["name"]: a for a in atoms}
    only_scalars = not (ctx.roles["arrays"] or ctx.roles["rows"] or ctx.roles["strs"] or ctx.roles["grids"]
                        or ctx.roles["mats"])
    found = _formula_search(ctx, ctx.units, exps, ctx.rnd_units[:20], atoms, 5,
                            min(2.0 if only_scalars else 1.0, ctx.time_left() * 0.25))
    for ex in found[:60]:
        if all(a.const for a in _ex_atoms_used(ex)):
            continue
        def run(u, ex=ex):
            return _ex_value(ex, {a["name"]: a["get"](u) for a in atoms if not a.get("const")})

        def cpp(G, ex=ex):
            pre, expr = _ex_cpp(ex, G, by)
            return (pre + "\n" if pre else "") + G.pr(expr, "bool" if ex.typ == "b" else ("float" if ex.typ == "f"
                                                                                       else "int"))
        uses_arr = any(a.desc_atom.startswith(("suma(", "max(", "min(", "|")) for a in _ex_atoms_used(ex))
        yield Cand("wzór:" + ex.text(), "wynik = " + ex.text(), "formula", 1.0 + ex.cost, ex.all_tags(), run, cpp,
                   cx=("n" if uses_arr else _formula_cx(ex)), work=lambda u: 10)


def _formula_cx(ex: Ex) -> str:
    ops = set()

    def walk(e):
        if e.op != "atom":
            ops.add(e.op)
            for a in e.args:
                walk(a)
    walk(ex)
    if ops & {"ndiv", "sdiv", "phi", "isprime", "spf", "lpf"}:
        return "sqrt(V)"
    if ops & {"fib", "fact", "pow", "binom"}:
        return "1"
    return "1"


def fam_rows_formula(ctx: Ctx):
    """Wynik dla KAŻDEGO wiersza (np. q zapytań „a b” → q odpowiedzi): wzór z pól wiersza i parametrów."""
    for R in ctx.roles["rows"]:
        exps_units = _unit_exps(ctx, lambda u, R=R: len(u[R.names[0]]))
        if exps_units is None:
            continue
        params = [v for v in ctx.roles["params"] if v not in R.names]
        pts, exps = [], []
        for u, e in zip(ctx.units, exps_units):
            for r in range(len(u[R.names[0]])):
                pts.append((u, r))
                exps.append([e[r]])
        if len(pts) < 1:
            continue
        rnd = [(u, r) for u in ctx.rnd_units for r in range(len(u.get(R.names[0]) or []))][:30]
        pts, exps = pts[:80], exps[:80]
        atoms = [{"name": f, "cpp": f"{f}[@I@]", "get": (lambda p, f=f: p[0][f][p[1]]), "cost": 1.0} for f in R.names]
        atoms += [{"name": v, "cpp": v, "get": (lambda p, v=v: p[0][v]), "cost": 1.2} for v in params]
        atoms += _consts_atoms(ctx)
        by = {a["name"]: a for a in atoms}
        found = _formula_search(ctx, pts, exps, rnd, atoms, 5, min(2.0, ctx.time_left() * 0.25))
        for ex in found[:40]:
            if all(a.const for a in _ex_atoms_used(ex)):
                continue
            def run(u, ex=ex, R=R):
                res = []
                for r in range(len(u[R.names[0]])):
                    vals = {a["name"]: a["get"]((u, r)) for a in atoms if not a.get("const")}
                    res.append(_ex_value(ex, vals))
                return res

            def cpp(G, ex=ex, R=R):
                i = G.f("i")
                pre, expr = _ex_cpp(ex, G, by, {"@I@": i})
                cnt = R.count.cpp() if R.count is not None else f"(int){R.names[0]}.size()"
                kind = "bool" if ex.typ == "b" else ("float" if ex.typ == "f" else "int")
                return f"for (int {i} = 0; {i} < {cnt}; {i}++) {{\n" + _indent(G.pr(expr, kind), "    ") + "\n}"
            yield Cand(f"wiersz:{R.name}:" + ex.text(), "dla każdego wiersza: " + ex.text(), "rows", 1.5 + ex.cost,
                       ex.all_tags() | {"query"}, run, cpp, cx="m", work=lambda u, R=R: 4 * len(u[R.names[0]]))


def fam_elem_formula(ctx: Ctx):
    """Wynik dla każdego elementu ciągu: wzór z a_i, numeru i, n i parametrów."""
    for a, _ln in ctx.roles["arrays"]:
        exps_units = _unit_exps(ctx, lambda u, a=a: len(u[a]))
        if exps_units is None:
            continue
        pts, exps = [], []
        for u, e in zip(ctx.units, exps_units):
            for i in range(len(u[a])):
                pts.append((u, i))
                exps.append([e[i]])
        if not pts:
            continue
        rnd = [(u, i) for u in ctx.rnd_units for i in range(len(u.get(a) or []))][:30]
        pts, exps = pts[:80], exps[:80]
        atoms = [{"name": f"{a}[i]", "cpp": f"{a}[@I@]", "get": (lambda p, a=a: p[0][a][p[1]]), "cost": 1.0},
                 {"name": "i", "cpp": "(@I@ + 1)", "get": (lambda p: p[1] + 1), "cost": 1.3, "tags": ("position",)},
                 {"name": "n", "cpp": f"(long long){a}.size()", "get": (lambda p, a=a: len(p[0][a])), "cost": 1.4}]
        atoms += [{"name": v, "cpp": v, "get": (lambda p, v=v: p[0][v]), "cost": 1.2} for v in ctx.roles["params"]]
        atoms += _consts_atoms(ctx)
        by = {x["name"]: x for x in atoms}
        found = _formula_search(ctx, pts, exps, rnd, atoms, 4, min(1.5, ctx.time_left() * 0.2))
        for ex in found[:30]:
            if not any(x.desc_atom == f"{a}[i]" for x in _ex_atoms_used(ex)) and ex.typ != "b":
                continue

            def run(u, ex=ex, a=a):
                return [_ex_value(ex, {x["name"]: x["get"]((u, i)) for x in atoms if not x.get("const")})
                        for i in range(len(u[a]))]

            def cpp(G, ex=ex, a=a):
                i = G.f("i")
                pre, expr = _ex_cpp(ex, G, by, {"@I@": i})
                if ex.typ == "b" or ctx.out.seq_col:
                    kind = "bool" if ex.typ == "b" else ("float" if ex.typ == "f" else "int")
                    return f"for (size_t {i} = 0; {i} < {a}.size(); {i}++) {{\n" + _indent(G.pr(expr, kind), "    ") + "\n}"
                w = G.f("wynik")
                return (f"vector<{'double' if ex.typ == 'f' else 'long long'}> {w};\n"
                        f"for (size_t {i} = 0; {i} < {a}.size(); {i}++) {w}.push_back({expr});\n" + G.seq(w))
            yield Cand(f"element:{a}:" + ex.text(), f"dla każdego elementu {a}: " + ex.text(), "elem", 1.5 + ex.cost,
                       ex.all_tags(), run, cpp, cx="n", work=lambda u, a=a: 4 * len(u[a]))


# ============================================================================================ rodziny: ciągi
_TEMPS = {"r": "wynik", "x": "x", "y": "y", "z": "z", "i": "i", "j": "j", "l": "l", "b": "b", "s": "suma", "f": "jest",
          "c": "cnt", "m": "m", "d": "d", "p": "p", "t": "t", "w": "w", "q": "q", "e": "e", "o": "o", "h": "h",
          "g": "g", "u": "u", "v": "v", "mask": "mask", "best": "najlepszy", "cur": "akt", "e2": "d2", "e3": "e2",
          "q2": "q2", "v2": "v2", "fx": "sx", "fy": "sy", "gx": "tx", "gy": "ty", "dr": "dr", "dc": "dc", "z": "odw",
          "k": "k", "a": "nx"}


def _mk(key, desc, fam, cost, tags, py, tpl, kind, none=None, cx="n", work=None, helpers=(), **fixed):
    """Kandydat z szablonu C++ liczącego «r» (kind: int/bool/float/str/seq) albo samodzielnie wypisującego (raw)."""
    def cpp(G, tpl=tpl, kind=kind, none=none, fixed=fixed, helpers=helpers):
        code0 = _t(tpl, **fixed)
        none0 = _t(none, **fixed) if none else ""
        temps = {}
        for k, base in _TEMPS.items():
            if "«" + k + "»" in code0 or "«" + k + "»" in none0:
                temps[k] = G.f(base)
        code = _t(code0, **temps)
        G.need(*helpers)
        if "«NONEW»" in code:
            code = code.replace("«NONEW»", G.o.cpp_word(G.o.none))
        if "«SEQ»" in code:
            code = code.replace("«SEQ»", _indent(G.seq(temps.get("r", "wynik")), "    "))
        if kind == "raw":
            return code
        r = temps.get("r", "wynik")
        if kind == "seq":
            return code + "\n" + G.seq(r)
        nc = _t(none0, **temps) if none else None
        return code + "\n" + G.pr(r, kind, nc)
    return Cand(key, desc, fam, cost, tags, py, cpp, cx=cx, work=work, helpers=helpers)


def _need_nonempty(a):
    if not a:
        raise Bad
    return a


def _lis(a, strict=True, dec=False):
    d = []
    for i, x in enumerate(a):
        best = 0
        for j in range(i):
            y = a[j]
            ok = (y > x if dec else y < x) if strict else (y >= x if dec else y <= x)
            if ok and d[j] > best:
                best = d[j]
        d.append(best + 1)
    return max(d) if d else 0


def _longest_run(a, rel):
    if not a:
        return 0
    best = cur = 1
    for i in range(1, len(a)):
        cur = cur + 1 if rel(a[i - 1], a[i]) else 1
        best = max(best, cur)
    return best


def _mode(a):
    c = Counter(_need_nonempty(a))
    m = max(c.values())
    return min(v for v, k in c.items() if k == m), m


# predykaty elementu: (klucz, opis, tagi, Python (x, K), C++ («x», «K»), potrzebne K, koszt, pomocnicze)
ELEM_PREDS = [
    ("even", "parzyste", ("even",), lambda x, K: x % 2 == 0, "«x» % 2 == 0", False, 1.0, ()),
    ("odd", "nieparzyste", ("odd",), lambda x, K: x % 2 != 0, "«x» % 2 != 0", False, 1.0, ()),
    ("pos", "dodatnie", (), lambda x, K: x > 0, "«x» > 0", False, 1.0, ()),
    ("neg", "ujemne", (), lambda x, K: x < 0, "«x» < 0", False, 1.0, ()),
    ("zero", "równe 0", ("equal",), lambda x, K: x == 0, "«x» == 0", False, 1.2, ()),
    ("prime", "pierwsze", ("prime",), lambda x, K: _isprime(x), "isprime(«x»)", False, 1.2, ("isprime",)),
    ("square", "kwadraty", ("square",), lambda x, K: _issq(x), "issq(«x»)", False, 1.4, ("issq",)),
    ("pal", "palindromy", ("palin",), lambda x, K: _ispal(x), "ispalnum(«x»)", False, 1.5, ("ispalnum",)),
    ("eqK", "równe «K»", ("equal", "occur"), lambda x, K: x == K, "«x» == «K»", True, 1.0, ()),
    ("neK", "różne od «K»", (), lambda x, K: x != K, "«x» != «K»", True, 1.3, ()),
    ("gtK", "> «K»", (), lambda x, K: x > K, "«x» > «K»", True, 1.0, ()),
    ("geK", "≥ «K»", ("atleast",), lambda x, K: x >= K, "«x» >= «K»", True, 1.0, ()),
    ("ltK", "< «K»", (), lambda x, K: x < K, "«x» < «K»", True, 1.0, ()),
    ("leK", "≤ «K»", ("atmost",), lambda x, K: x <= K, "«x» <= «K»", True, 1.0, ()),
    ("divK", "podzielne przez «K»", ("div",), lambda x, K: K != 0 and x % K == 0, "«K» != 0 && «x» % «K» == 0", True,
     1.0, ()),
    ("dividesK", "dzielniki «K»", ("divisors", "div"), lambda x, K: x != 0 and K % x == 0, "«x» != 0 && «K» % «x» == 0",
     True, 1.3, ()),
]


def _k_choices(ctx: Ctx, extra_consts=True) -> list[tuple[str, object, float]]:
    """Parametry K: zmienne niebędące rozmiarami (k, x…) oraz stałe z treści (droższe)."""
    out = [(v, (lambda u, v=v: u[v]), 0.0) for v in ctx.roles["params"]]
    if extra_consts:
        out += [(str(c), (lambda u, c=c: c), 1.0) for c in ctx.consts if c <= 10**6][:3]
    return out


def fam_array(ctx: Ctx):
    """Agregaty ciągu: suma, max, różne wartości, k-ty, mediana, serie, LIS… oraz licz/sumuj/szukaj z warunkiem."""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    W = lambda a: (lambda u: len(u[a]))  # noqa: E731
    W2 = lambda a: (lambda u: len(u[a]) ** 2)  # noqa: E731
    Ks = _k_choices(ctx)
    for a, _ln in ctx.roles["arrays"] + (ctx.roles["cols"] if len(ctx.roles["rows"]) == 1 else []):
        A = dict(a=a)
        g = lambda f, a=a: (lambda u: f(u[a]))  # noqa: E731
        base = [
            ("sum", "suma elementów", ("sum",), 1.0, lambda v: _ck(sum(v)),
             "long long «r» = 0;\nfor (long long «x» : «a») «r» += «x»;", "int"),
            ("max", "największy element", ("max",), 1.0, lambda v: max(_need_nonempty(v)),
             "long long «r» = *max_element(«a».begin(), «a».end());", "int"),
            ("min", "najmniejszy element", ("min",), 1.0, lambda v: min(_need_nonempty(v)),
             "long long «r» = *min_element(«a».begin(), «a».end());", "int"),
            ("distinct", "liczba różnych wartości", ("distinct", "count"), 1.2, lambda v: len(set(v)),
             "long long «r» = set<long long>(«a».begin(), «a».end()).size();", "int"),
            ("xor", "XOR wszystkich elementów", ("xor",), 1.4, lambda v: _ck(_xor_all(v)),
             "long long «r» = 0;\nfor (long long «x» : «a») «r» ^= «x»;", "int"),
            ("gcd", "NWD wszystkich elementów", ("gcd",), 1.3, lambda v: _gcd_all(v),
             "long long «r» = 0;\nfor (long long «x» : «a») «r» = gcd(«r», «x»);", "int"),
            ("lcm", "NWW wszystkich elementów", ("lcm",), 1.4, lambda v: _lcm_all(v),
             "long long «r» = 1;\nfor (long long «x» : «a») «r» = lcm(«r», «x»);", "int"),
            ("prod", "iloczyn elementów", ("product",), 1.4, lambda v: _prod_all(v),
             "long long «r» = 1;\nfor (long long «x» : «a») «r» *= «x»;", "int"),
            ("range", "max − min", ("diff", "max", "min"), 1.6, lambda v: _ck(max(_need_nonempty(v)) - min(v)),
             "long long «r» = *max_element(«a».begin(), «a».end()) - *min_element(«a».begin(), «a».end());", "int"),
            ("mean", "średnia arytmetyczna", ("avg",), 1.3, lambda v: _ck(sum(_need_nonempty(v))) / len(v),
             "long long «s» = 0;\nfor (long long «x» : «a») «s» += «x»;\ndouble «r» = (double)«s» / «a».size();",
             "float"),
            ("meanfloor", "średnia (część całkowita)", ("avg",), 1.6, lambda v: _cdiv(_ck(sum(_need_nonempty(v))), len(v)),
             "long long «s» = 0;\nfor (long long «x» : «a») «s» += «x»;\nlong long «r» = «s» / (long long)«a».size();",
             "int"),
            ("medlo", "mediana (dolna)", ("median",), 1.3, lambda v: sorted(_need_nonempty(v))[(len(v) - 1) // 2],
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nlong long «r» = «b»[(«b».size() - 1) / 2];",
             "int"),
            ("medhi", "mediana (górna)", ("median",), 1.4, lambda v: sorted(_need_nonempty(v))[len(v) // 2],
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nlong long «r» = «b»[«b».size() / 2];", "int"),
            ("medavg", "mediana (średnia dwóch środkowych)", ("median",), 1.5, _median_avg,
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nsize_t «m» = «b».size();\n"
             "double «r» = «m» % 2 ? (double)«b»[«m» / 2] : («b»[«m» / 2 - 1] + «b»[«m» / 2]) / 2.0;", "float"),
            ("argmax", "pozycja największego (pierwsza, od 1)", ("max", "position"), 1.4,
             lambda v: v.index(max(_need_nonempty(v))) + 1,
             "long long «r» = max_element(«a».begin(), «a».end()) - «a».begin() + 1;", "int"),
            ("argmaxl", "pozycja największego (ostatnia, od 1)", ("max", "position"), 1.6,
             lambda v: len(v) - v[::-1].index(max(_need_nonempty(v))),
             "long long «r» = 1;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) if («a»[«i»] >= «a»[«r» - 1]) «r» = «i» + 1;",
             "int"),
            ("argmin", "pozycja najmniejszego (pierwsza, od 1)", ("min", "position"), 1.4,
             lambda v: v.index(min(_need_nonempty(v))) + 1,
             "long long «r» = min_element(«a».begin(), «a».end()) - «a».begin() + 1;", "int"),
            ("argminl", "pozycja najmniejszego (ostatnia, od 1)", ("min", "position"), 1.6,
             lambda v: len(v) - v[::-1].index(min(_need_nonempty(v))),
             "long long «r» = 1;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) if («a»[«i»] <= «a»[«r» - 1]) «r» = «i» + 1;",
             "int"),
            ("mode", "najczęstsza wartość (najmniejsza przy remisie)", ("max", "count"), 1.5, lambda v: _mode(v)[0],
             "map<long long, long long> «c»;\nfor (long long «x» : «a») «c»[«x»]++;\nlong long «r» = 0, «best» = 0;\n"
             "for (auto [«x», «m»] : «c») if («m» > «best») { «best» = «m»; «r» = «x»; }", "int"),
            ("modecnt", "ile razy występuje najczęstsza wartość", ("max", "count"), 1.5, lambda v: _mode(v)[1],
             "map<long long, long long> «c»;\nfor (long long «x» : «a») «c»[«x»]++;\nlong long «r» = 0;\n"
             "for (auto [«x», «m»] : «c») «r» = max(«r», «m»);", "int"),
            ("cntmax", "ile elementów równych maksimum", ("max", "count"), 1.6, lambda v: v.count(max(_need_nonempty(v))),
             "long long «r» = count(«a».begin(), «a».end(), *max_element(«a».begin(), «a».end()));", "int"),
            ("cntmin", "ile elementów równych minimum", ("min", "count"), 1.6, lambda v: v.count(min(_need_nonempty(v))),
             "long long «r» = count(«a».begin(), «a».end(), *min_element(«a».begin(), «a».end()));", "int"),
            ("max2d", "druga największa (różna) wartość", ("max",), 1.6, lambda v: _nth_distinct(v, -2),
             "set<long long> «b»(«a».begin(), «a».end());\nlong long «r» = «b».size() >= 2 ? *next(«b».rbegin()) : 0;",
             "int", "«b».size() < 2"),
            ("max2", "drugi największy element (z powtórzeniami)", ("max",), 1.6, lambda v: _nth_sorted(v, -2),
             "vector<long long> «b» = «a»;\nsort(«b».rbegin(), «b».rend());\nlong long «r» = «b».size() >= 2 ? «b»[1] : 0;",
             "int", "«b».size() < 2"),
            ("min2d", "druga najmniejsza (różna) wartość", ("min",), 1.6, lambda v: _nth_distinct(v, 1),
             "set<long long> «b»(«a».begin(), «a».end());\nlong long «r» = «b».size() >= 2 ? *next(«b».begin()) : 0;",
             "int", "«b».size() < 2"),
            ("min2", "drugi najmniejszy element (z powtórzeniami)", ("min",), 1.6, lambda v: _nth_sorted(v, 1),
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nlong long «r» = «b».size() >= 2 ? «b»[1] : 0;",
             "int", "«b».size() < 2"),
            ("records", "ile elementów większych od wszystkich wcześniejszych", ("record", "max", "count"), 1.6,
             _records, "long long «r» = 0, «best» = LLONG_MIN;\nfor (long long «x» : «a») if («x» > «best») { «r»++; «best» = «x»; }",
             "int"),
            ("rises", "ile razy a[i] > a[i−1]", ("increasing", "adjacent", "count"), 1.6,
             lambda v: sum(1 for i in range(1, len(v)) if v[i] > v[i - 1]),
             "long long «r» = 0;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) if («a»[«i»] > «a»[«i» - 1]) «r»++;", "int"),
            ("falls", "ile razy a[i] < a[i−1]", ("adjacent", "count"), 1.6,
             lambda v: sum(1 for i in range(1, len(v)) if v[i] < v[i - 1]),
             "long long «r» = 0;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) if («a»[«i»] < «a»[«i» - 1]) «r»++;", "int"),
            ("adjeq", "ile par sąsiednich równych", ("adjacent", "equal", "count"), 1.6,
             lambda v: sum(1 for i in range(1, len(v)) if v[i] == v[i - 1]),
             "long long «r» = 0;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) if («a»[«i»] == «a»[«i» - 1]) «r»++;", "int"),
            ("runs", "liczba serii równych kolejnych", ("run", "count"), 1.6,
             lambda v: (1 + sum(1 for i in range(1, len(v)) if v[i] != v[i - 1])) if v else 0,
             "long long «r» = «a».empty() ? 0 : 1;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) if («a»[«i»] != «a»[«i» - 1]) «r»++;",
             "int"),
            ("runeq", "najdłuższa seria równych kolejnych", ("run", "max", "len"), 1.6,
             lambda v: _longest_run(v, lambda x, y: x == y), _RUN_TPL.replace("«REL»", "«a»[«i» - 1] == «a»[«i»]"), "int"),
            ("runinc", "najdłuższy rosnący spójny fragment", ("increasing", "subarray", "max", "len"), 1.6,
             lambda v: _longest_run(v, lambda x, y: x < y), _RUN_TPL.replace("«REL»", "«a»[«i» - 1] < «a»[«i»]"), "int"),
            ("runnd", "najdłuższy niemalejący spójny fragment", ("nondecreasing", "subarray", "max", "len"), 1.7,
             lambda v: _longest_run(v, lambda x, y: x <= y), _RUN_TPL.replace("«REL»", "«a»[«i» - 1] <= «a»[«i»]"), "int"),
            ("rundec", "najdłuższy malejący spójny fragment", ("decreasing", "subarray", "max", "len"), 1.7,
             lambda v: _longest_run(v, lambda x, y: x > y), _RUN_TPL.replace("«REL»", "«a»[«i» - 1] > «a»[«i»]"), "int"),
            ("lis", "najdłuższy podciąg rosnący", ("lis", "increasing", "subseq", "max", "len"), 1.6, lambda v: _lis(v),
             _LIS_TPL.replace("«REL»", "«a»[«j»] < «a»[«i»]"), "int"),
            ("lnds", "najdłuższy podciąg niemalejący", ("lis", "nondecreasing", "subseq", "max", "len"), 1.8,
             lambda v: _lis(v, strict=False), _LIS_TPL.replace("«REL»", "«a»[«j»] <= «a»[«i»]"), "int"),
            ("lds", "najdłuższy podciąg malejący", ("subseq", "decreasing", "max", "len"), 1.8, lambda v: _lis(v, dec=True),
             _LIS_TPL.replace("«REL»", "«a»[«j»] > «a»[«i»]"), "int"),
            ("sumadj", "suma |a[i] − a[i−1]|", ("sum", "diff", "adjacent"), 1.7,
             lambda v: _ck(sum(abs(v[i] - v[i - 1]) for i in range(1, len(v)))),
             "long long «r» = 0;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) «r» += llabs(«a»[«i»] - «a»[«i» - 1]);", "int"),
            ("maxadj", "największa |a[i] − a[i−1]|", ("max", "diff", "adjacent"), 1.7,
             lambda v: max(abs(v[i] - v[i - 1]) for i in range(1, len(v))) if len(v) > 1 else _raise(),
             "long long «r» = 0;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) «r» = max(«r», llabs(«a»[«i»] - «a»[«i» - 1]));",
             "int"),
            ("single", "ile wartości występuje dokładnie raz", ("distinct", "count"), 1.7,
             lambda v: sum(1 for k in Counter(v).values() if k == 1),
             "map<long long, long long> «c»;\nfor (long long «x» : «a») «c»[«x»]++;\nlong long «r» = 0;\n"
             "for (auto [«x», «m»] : «c») if («m» == 1) «r»++;", "int"),
            ("dups", "ile elementów to powtórzenia (n − liczba różnych)", ("distinct", "count"), 1.8,
             lambda v: len(v) - len(set(v)),
             "long long «r» = «a».size() - set<long long>(«a».begin(), «a».end()).size();", "int"),
            ("nminusmode", "ile elementów trzeba zmienić, by wszystkie były równe", ("equal", "min"), 1.8,
             lambda v: len(v) - _mode(v)[1],
             "map<long long, long long> «c»;\nlong long «best» = 0;\nfor (long long «x» : «a») «best» = max(«best», ++«c»[«x»]);\n"
             "long long «r» = (long long)«a».size() - «best»;", "int"),
            ("sumdist", "suma różnych wartości", ("sum", "distinct"), 1.7, lambda v: _ck(sum(set(v))),
             "set<long long> «b»(«a».begin(), «a».end());\nlong long «r» = 0;\nfor (long long «x» : «b») «r» += «x»;", "int"),
            ("maxpref", "największa suma prefiksu", ("max", "sum", "prefix"), 1.8, lambda v: _ck(max(itertools.accumulate(_need_nonempty(v)))),
             "long long «r» = LLONG_MIN, «s» = 0;\nfor (long long «x» : «a») { «s» += «x»; «r» = max(«r», «s»); }", "int"),
            ("minpref", "najmniejsza suma prefiksu", ("min", "sum", "prefix"), 1.8, lambda v: _ck(min(itertools.accumulate(_need_nonempty(v)))),
             "long long «r» = LLONG_MAX, «s» = 0;\nfor (long long «x» : «a») { «s» += «x»; «r» = min(«r», «s»); }", "int"),
            ("locmax", "ile lokalnych maksimów (większe od obu sąsiadów)", ("max", "count", "adjacent"), 1.9,
             lambda v: sum(1 for i in range(1, len(v) - 1) if v[i] > v[i - 1] and v[i] > v[i + 1]),
             "long long «r» = 0;\nfor (size_t «i» = 1; «i» + 1 < «a».size(); «i»++) if («a»[«i»] > «a»[«i» - 1] && «a»[«i»] > «a»[«i» + 1]) «r»++;",
             "int"),
            ("fixed", "ile i ma a[i] = i (od 1)", ("position", "equal", "count"), 1.9,
             lambda v: sum(1 for i, x in enumerate(v) if x == i + 1),
             "long long «r» = 0;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) if («a»[«i»] == (long long)«i» + 1) «r»++;", "int"),
            ("aboveavg", "ile elementów powyżej średniej", ("avg", "count"), 1.9,
             lambda v: sum(1 for x in v if x * len(v) > sum(v)),
             "long long «s» = 0, «r» = 0;\nfor (long long «x» : «a») «s» += «x»;\nfor (long long «x» : «a») if («x» * (long long)«a».size() > «s») «r»++;",
             "int"),
            ("sorted", "czy ciąg jest niemalejący", ("sort", "exists"), 1.5, lambda v: all(v[i] <= v[i + 1] for i in range(len(v) - 1)),
             "bool «r» = is_sorted(«a».begin(), «a».end());", "bool"),
            ("strinc", "czy ciąg jest rosnący", ("increasing", "exists"), 1.6, lambda v: all(v[i] < v[i + 1] for i in range(len(v) - 1)),
             "bool «r» = true;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) if («a»[«i»] <= «a»[«i» - 1]) «r» = false;", "bool"),
            ("isperm", "czy to permutacja liczb 1..n", ("perm", "exists"), 1.6, lambda v: sorted(v) == list(range(1, len(v) + 1)),
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nbool «r» = true;\n"
             "for (size_t «i» = 0; «i» < «b».size(); «i»++) if («b»[«i»] != (long long)«i» + 1) «r» = false;", "bool"),
            ("alldist", "czy wszystkie elementy są różne", ("distinct", "exists"), 1.5, lambda v: len(set(v)) == len(v),
             "bool «r» = set<long long>(«a».begin(), «a».end()).size() == «a».size();", "bool"),
            ("palseq", "czy ciąg jest palindromem", ("palin", "exists"), 1.6, lambda v: v == v[::-1],
             "bool «r» = equal(«a».begin(), «a».end(), «a».rbegin());", "bool"),
        ]
        for key, desc, tags, cost, f, tpl, kind, *none in base:
            heavy = key in ("lis", "lnds", "lds")
            yield _mk(f"arr:{key}:{a}", f"{desc} ({a})", "array", 2.0 + cost, set(tags), g(f), tpl, kind,
                      none[0] if none else None, cx="n^2" if heavy else "n log n", work=(W2(a) if heavy else W(a)), **A)
        # k-ty, suma k największych…
        for K, getK, kc in Ks:
            AK = dict(a=a, K=K)
            for key, desc, tags, cost, f, tpl in [
                ("kth", "k-ty najmniejszy", ("kth", "min"), 1.3, lambda v, k: _kth(sorted(v), k),
                 "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nlong long «r» = «b»[«K» - 1];"),
                ("kthl", "k-ty największy", ("kth", "max"), 1.3, lambda v, k: _kth(sorted(v, reverse=True), k),
                 "vector<long long> «b» = «a»;\nsort(«b».rbegin(), «b».rend());\nlong long «r» = «b»[«K» - 1];"),
                ("topk", "suma k największych", ("sum", "max", "kth"), 1.5, lambda v, k: _ck(sum(sorted(v, reverse=True)[:max(0, k)])),
                 "vector<long long> «b» = «a»;\nsort(«b».rbegin(), «b».rend());\nlong long «r» = 0;\n"
                 "for (long long «i» = 0; «i» < min(«K», (long long)«b».size()); «i»++) «r» += «b»[«i»];"),
                ("botk", "suma k najmniejszych", ("sum", "min", "kth"), 1.5, lambda v, k: _ck(sum(sorted(v)[:max(0, k)])),
                 "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\nlong long «r» = 0;\n"
                 "for (long long «i» = 0; «i» < min(«K», (long long)«b».size()); «i»++) «r» += «b»[«i»];"),
            ]:
                yield _mk(f"arr:{key}:{a}:{K}", f"{desc} ({a}, k = {K})", "array", 2.0 + cost + kc, set(tags),
                          (lambda u, f=f, a=a, getK=getK: f(u[a], getK(u))), tpl, "int", cx="n log n", work=W(a), **AK)
        # z warunkiem na element
        for pk, pdesc, ptags, pf, pc, needK, pcost, ph in ELEM_PREDS:
            for K, getK, kc in (Ks if needK else [(None, (lambda u: None), 0.0)]):
                P = _t(pc, K=K or "0")
                d = _t(pdesc, K=K or "")
                F = dict(a=a, P=P)
                for ok, odesc, otags, ocost, of, otpl, okind, onone in _PRED_OBJS:
                    yield _mk(f"arr:{ok}:{pk}:{a}:{K}", _t(odesc, d=d) + f" ({a})", "array", 2.2 + pcost + ocost + kc,
                              set(ptags) | set(otags), (lambda u, of=of, pf=pf, a=a, getK=getK: of(u[a], pf, getK(u))),
                              otpl, okind, onone, cx="n", work=W(a), helpers=ph, **F)


_RUN_TPL = ("long long «r» = «a».empty() ? 0 : 1, «c» = 1;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) {\n"
            "    «c» = («REL») ? «c» + 1 : 1;\n    «r» = max(«r», «c»);\n}")
_LIS_TPL = ("vector<long long> «d»(«a».size(), 1);\nlong long «r» = 0;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) {\n"
            "    for (size_t «j» = 0; «j» < «i»; «j»++) if («REL») «d»[«i»] = max(«d»[«i»], «d»[«j»] + 1);\n"
            "    «r» = max(«r», «d»[«i»]);\n}")

# cele dla „elementów spełniających warunek P”: (klucz, opis, tagi, koszt, Python, C++, rodzaj, warunek braku)
_PRED_OBJS = [
    ("cnt", "ile elementów «d»", ("count",), 0.0, lambda v, p, K: sum(1 for x in v if p(x, K)),
     "long long «r» = 0;\nfor (long long «x» : «a») if («P») «r»++;", "int", None),
    ("sum", "suma elementów «d»", ("sum",), 0.3, lambda v, p, K: _ck(sum(x for x in v if p(x, K))),
     "long long «r» = 0;\nfor (long long «x» : «a») if («P») «r» += «x»;", "int", None),
    ("max", "największy element «d»", ("max",), 0.4, lambda v, p, K: max((x for x in v if p(x, K)), default=NONE),
     "long long «r» = LLONG_MIN;\nbool «f» = false;\nfor (long long «x» : «a») if («P») { «r» = max(«r», «x»); «f» = true; }",
     "int", "!«f»"),
    ("min", "najmniejszy element «d»", ("min",), 0.4, lambda v, p, K: min((x for x in v if p(x, K)), default=NONE),
     "long long «r» = LLONG_MAX;\nbool «f» = false;\nfor (long long «x» : «a») if («P») { «r» = min(«r», «x»); «f» = true; }",
     "int", "!«f»"),
    ("any", "czy jest element «d»", ("exists",), 0.3, lambda v, p, K: any(p(x, K) for x in v),
     "bool «r» = false;\nfor (long long «x» : «a») if («P») «r» = true;", "bool", None),
    ("all", "czy wszystkie elementy są «d»", ("exists",), 0.4, lambda v, p, K: all(p(x, K) for x in v),
     "bool «r» = true;\nfor (long long «x» : «a») if (!(«P»)) «r» = false;", "bool", None),
    ("first", "pozycja pierwszego elementu «d» (od 1)", ("position",), 0.5,
     lambda v, p, K: next((i + 1 for i, x in enumerate(v) if p(x, K)), NONE),
     "long long «r» = -1;\nfor (size_t «i» = 0; «i» < «a».size() && «r» == -1; «i»++) {\n    long long «x» = «a»[«i»];\n"
     "    if («P») «r» = «i» + 1;\n}", "int", "«r» == -1"),
    ("last", "pozycja ostatniego elementu «d» (od 1)", ("position",), 0.6,
     lambda v, p, K: next((len(v) - i for i, x in enumerate(reversed(v)) if p(x, K)), NONE),
     "long long «r» = -1;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) {\n    long long «x» = «a»[«i»];\n"
     "    if («P») «r» = «i» + 1;\n}", "int", "«r» == -1"),
]


def _raise():
    raise Bad


def _xor_all(v):
    r = 0
    for x in v:
        r ^= x
    return r


def _gcd_all(v):
    r = 0
    for x in v:
        r = math.gcd(r, x)
    return r


def _lcm_all(v):
    r = 1
    for x in v:
        r = _lcm(r, x)
    return r


def _prod_all(v):
    r = 1
    for x in v:
        r = _ck(r * x)
    return r


def _median_avg(v):
    b = sorted(_need_nonempty(v))
    m = len(b)
    return float(b[m // 2]) if m % 2 else (b[m // 2 - 1] + b[m // 2]) / 2.0


def _nth_distinct(v, k):
    b = sorted(set(v))
    if len(b) < 2:
        return NONE
    return b[k]


def _nth_sorted(v, k):
    b = sorted(v)
    if len(b) < 2:
        return NONE
    return b[k]


def _records(v):
    r, best = 0, None
    for x in v:
        if best is None or x > best:
            r, best = r + 1, x
    return r


def _kth(b, k):
    if not isinstance(k, int) or k < 1 or k > len(b):
        raise Bad
    return b[k - 1]


def _none_int(ctx: Ctx):
    return int(ctx.out.none) if re.fullmatch(r"-?\d+", ctx.out.none or "") else None


def fam_seq(ctx: Ctx):
    """Ciąg wynikowy: posortowany, bez powtórzeń, odwrócony, sumy prefiksowe, rangi, następny większy, okna…"""
    if not any(len(s.exp) >= 2 for s in ctx.evs):
        return
    NI = _none_int(ctx)
    Ks = _k_choices(ctx)
    W = lambda a: (lambda u: len(u[a]))  # noqa: E731
    W2 = lambda a: (lambda u: len(u[a]) ** 2)  # noqa: E731
    for a, _ln in ctx.roles["arrays"] + (ctx.roles["cols"] if len(ctx.roles["rows"]) == 1 else []):
        A = dict(a=a)
        base = [
            ("sort", "posortowany rosnąco", ("sort", "increasing"), 0.8, lambda v: sorted(v),
             "vector<long long> «r» = «a»;\nsort(«r».begin(), «r».end());", False),
            ("sortd", "posortowany malejąco", ("sort", "decreasing"), 1.0, lambda v: sorted(v, reverse=True),
             "vector<long long> «r» = «a»;\nsort(«r».rbegin(), «r».rend());", False),
            ("uniq", "różne wartości rosnąco", ("sort", "distinct"), 1.2, lambda v: sorted(set(v)),
             "vector<long long> «r» = «a»;\nsort(«r».begin(), «r».end());\n«r».erase(unique(«r».begin(), «r».end()), «r».end());",
             False),
            ("uniqd", "różne wartości malejąco", ("sort", "distinct"), 1.4, lambda v: sorted(set(v), reverse=True),
             "vector<long long> «r» = «a»;\nsort(«r».rbegin(), «r».rend());\n«r».erase(unique(«r».begin(), «r».end()), «r».end());",
             False),
            ("rev", "odwrócony", ("reverse",), 1.0, lambda v: v[::-1], "vector<long long> «r»(«a».rbegin(), «a».rend());",
             False),
            ("pref", "sumy prefiksowe", ("sum", "prefix"), 1.2, lambda v: [_ck(x) for x in itertools.accumulate(v)],
             "vector<long long> «r»;\nlong long «s» = 0;\nfor (long long «x» : «a») { «s» += «x»; «r».push_back(«s»); }", False),
            ("suf", "sumy sufiksowe", ("sum",), 1.6, lambda v: [_ck(x) for x in itertools.accumulate(v[::-1])][::-1],
             "vector<long long> «r»(«a».size());\nlong long «s» = 0;\n"
             "for (size_t «i» = «a».size(); «i»-- > 0; ) { «s» += «a»[«i»]; «r»[«i»] = «s»; }", False),
            ("runmax", "maksimum prefiksu dla każdej pozycji", ("max", "prefix"), 1.5,
             lambda v: list(itertools.accumulate(v, max)),
             "vector<long long> «r»;\nfor (long long «x» : «a») «r».push_back(«r».empty() ? «x» : max(«r».back(), «x»));", False),
            ("runmin", "minimum prefiksu dla każdej pozycji", ("min", "prefix"), 1.5,
             lambda v: list(itertools.accumulate(v, min)),
             "vector<long long> «r»;\nfor (long long «x» : «a») «r».push_back(«r».empty() ? «x» : min(«r».back(), «x»));", False),
            ("dense", "numer wartości wśród różnych (od najmniejszej)", ("sort", "position"), 1.7,
             lambda v: [sorted(set(v)).index(x) + 1 for x in v],
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\n«b».erase(unique(«b».begin(), «b».end()), «b».end());\n"
             "vector<long long> «r»;\nfor (long long «x» : «a») «r».push_back(lower_bound(«b».begin(), «b».end(), «x») - «b».begin() + 1);",
             False),
            ("rank", "miejsce w rankingu rosnąco (1 + ile mniejszych)", ("sort", "position"), 1.7,
             lambda v: [1 + sum(1 for y in v if y < x) for x in v],
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\n"
             "vector<long long> «r»;\nfor (long long «x» : «a») «r».push_back(lower_bound(«b».begin(), «b».end(), «x») - «b».begin() + 1);",
             False),
            ("rankd", "miejsce w rankingu (1 + ile większych)", ("sort", "position"), 1.7,
             lambda v: [1 + sum(1 for y in v if y > x) for x in v],
             "vector<long long> «b» = «a»;\nsort(«b».begin(), «b».end());\n"
             "vector<long long> «r»;\nfor (long long «x» : «a») «r».push_back(«b».end() - upper_bound(«b».begin(), «b».end(), «x») + 1);",
             False),
            ("smlbef", "ile mniejszych przed każdym", ("count",), 1.9,
             lambda v: [sum(1 for j in range(i) if v[j] < v[i]) for i in range(len(v))], _CNT_TPL.replace("«J»", "«j» < «i»")
             .replace("«REL»", "«a»[«j»] < «a»[«i»]"), True),
            ("grtbef", "ile większych przed każdym", ("count", "inversion"), 1.9,
             lambda v: [sum(1 for j in range(i) if v[j] > v[i]) for i in range(len(v))], _CNT_TPL.replace("«J»", "«j» < «i»")
             .replace("«REL»", "«a»[«j»] > «a»[«i»]"), True),
            ("smlaft", "ile mniejszych za każdym", ("count", "inversion"), 1.9,
             lambda v: [sum(1 for j in range(i + 1, len(v)) if v[j] < v[i]) for i in range(len(v))],
             _CNT_TPL.replace("«J»", "«j» > «i»").replace("«REL»", "«a»[«j»] < «a»[«i»]"), True),
            ("grtaft", "ile większych za każdym", ("count",), 1.9,
             lambda v: [sum(1 for j in range(i + 1, len(v)) if v[j] > v[i]) for i in range(len(v))],
             _CNT_TPL.replace("«J»", "«j» > «i»").replace("«REL»", "«a»[«j»] > «a»[«i»]"), True),
            ("argsort", "numery elementów w kolejności rosnących wartości", ("sort", "position"), 1.8,
             lambda v: [i + 1 for i in sorted(range(len(v)), key=lambda i: (v[i], i))],
             "vector<long long> «r»(«a».size());\niota(«r».begin(), «r».end(), 1);\n"
             "stable_sort(«r».begin(), «r».end(), [&](long long «x», long long «y») { return «a»[«x» - 1] < «a»[«y» - 1]; });",
             False),
            ("argsortd", "numery elementów w kolejności malejących wartości", ("sort", "position"), 1.9,
             lambda v: [i + 1 for i in sorted(range(len(v)), key=lambda i: (-v[i], i))],
             "vector<long long> «r»(«a».size());\niota(«r».begin(), «r».end(), 1);\n"
             "stable_sort(«r».begin(), «r».end(), [&](long long «x», long long «y») { return «a»[«x» - 1] > «a»[«y» - 1]; });",
             False),
            ("diffs", "różnice sąsiednich a[i] − a[i−1]", ("diff", "adjacent"), 1.6,
             lambda v: [_ck(v[i] - v[i - 1]) for i in range(1, len(v))],
             "vector<long long> «r»;\nfor (size_t «i» = 1; «i» < «a».size(); «i»++) «r».push_back(«a»[«i»] - «a»[«i» - 1]);", False),
            ("firstocc", "różne wartości w kolejności pierwszego wystąpienia", ("distinct",), 1.7,
             lambda v: list(dict.fromkeys(v)),
             "vector<long long> «r»;\nset<long long> «b»;\nfor (long long «x» : «a») if («b».insert(«x»).second) «r».push_back(«x»);",
             False),
            ("occ", "ile razy występuje każdy element", ("count",), 1.8, lambda v: [v.count(x) for x in v],
             "map<long long, long long> «c»;\nfor (long long «x» : «a») «c»[«x»]++;\nvector<long long> «r»;\n"
             "for (long long «x» : «a») «r».push_back(«c»[«x»]);", False),
            ("repeated", "wartości występujące więcej niż raz (rosnąco)", ("distinct", "count"), 1.9,
             lambda v: sorted(x for x, k in Counter(v).items() if k > 1),
             "map<long long, long long> «c»;\nfor (long long «x» : «a») «c»[«x»]++;\nvector<long long> «r»;\n"
             "for (auto [«x», «m»] : «c») if («m» > 1) «r».push_back(«x»);", False),
            ("cumdist", "liczba różnych wartości w każdym prefiksie", ("distinct", "prefix"), 1.9,
             lambda v: [len(set(v[:i + 1])) for i in range(len(v))],
             "vector<long long> «r»;\nset<long long> «b»;\nfor (long long «x» : «a») { «b».insert(«x»); «r».push_back(«b».size()); }",
             False),
        ]
        if NI is not None:
            base += [
                ("nge", "następny większy element (albo brak)", ("max",), 1.9, lambda v, NI=NI: [_next_by(v, i, lambda x, y: y > x, NI) for i in range(len(v))],
                 _NEXT_TPL.replace("«REL»", "«a»[«j»] > «a»[«i»]").replace("«NI»", str(NI)).replace("«FROM»", "«i» + 1").replace("«STEP»", "«j»++").replace("«COND»", "«j» < «a».size()"), True),
                ("nse", "następny mniejszy element (albo brak)", ("min",), 2.0, lambda v, NI=NI: [_next_by(v, i, lambda x, y: y < x, NI) for i in range(len(v))],
                 _NEXT_TPL.replace("«REL»", "«a»[«j»] < «a»[«i»]").replace("«NI»", str(NI)).replace("«FROM»", "«i» + 1").replace("«STEP»", "«j»++").replace("«COND»", "«j» < «a».size()"), True),
                ("pge", "poprzedni większy element (albo brak)", ("max",), 2.0, lambda v, NI=NI: [_prev_by(v, i, lambda x, y: y > x, NI) for i in range(len(v))],
                 _PREV_TPL.replace("«REL»", "«a»[«j» - 1] > «a»[«i»]").replace("«NI»", str(NI)), True),
                ("pse", "poprzedni mniejszy element (albo brak)", ("min",), 2.0, lambda v, NI=NI: [_prev_by(v, i, lambda x, y: y < x, NI) for i in range(len(v))],
                 _PREV_TPL.replace("«REL»", "«a»[«j» - 1] < «a»[«i»]").replace("«NI»", str(NI)), True),
            ]
        for key, desc, tags, cost, f, tpl, quad in base:
            yield _mk(f"seq:{key}:{a}", f"{desc} ({a})", "seq", 2.0 + cost, set(tags),
                      (lambda u, f=f, a=a: f(u[a])), tpl, "seq", cx="n^2" if quad else "n log n",
                      work=W2(a) if quad else W(a), **A)
        for K, getK, kc in Ks:
            AK = dict(a=a, K=K)
            for key, desc, tags, cost, f, tpl in [
                ("rotl", "przesunięcie cykliczne w lewo o k", ("reverse",), 1.5, _rotl,
                 "vector<long long> «r»(«a».size());\nlong long «m» = «a».size();\n"
                 "for (long long «i» = 0; «i» < «m»; «i»++) «r»[«i»] = «a»[((«i» + «K») % «m» + «m») % «m»];"),
                ("rotr", "przesunięcie cykliczne w prawo o k", ("reverse",), 1.5, lambda v, k: _rotl(v, -k),
                 "vector<long long> «r»(«a».size());\nlong long «m» = «a».size();\n"
                 "for (long long «i» = 0; «i» < «m»; «i»++) «r»[«i»] = «a»[((«i» - «K») % «m» + «m») % «m»];"),
                ("winsum", "sumy okien długości k", ("sum", "window", "subarray"), 1.4, lambda v, k: _windows(v, k, sum),
                 _WIN_TPL.replace("«AGG»", "«s» += «a»[«j»]").replace("«INIT»", "0")),
                ("winmax", "maksima okien długości k", ("max", "window", "subarray"), 1.5, lambda v, k: _windows(v, k, max),
                 _WIN_TPL.replace("«AGG»", "«s» = max(«s», «a»[«j»])").replace("«INIT»", "LLONG_MIN")),
                ("winmin", "minima okien długości k", ("min", "window", "subarray"), 1.5, lambda v, k: _windows(v, k, min),
                 _WIN_TPL.replace("«AGG»", "«s» = min(«s», «a»[«j»])").replace("«INIT»", "LLONG_MAX")),
            ]:
                yield _mk(f"seq:{key}:{a}:{K}", f"{desc} ({a}, k = {K})", "seq", 2.0 + cost + kc, set(tags),
                          (lambda u, f=f, a=a, getK=getK: f(u[a], getK(u))), tpl, "seq", cx="n·k", work=W2(a), **AK)
        for pk, pdesc, ptags, pf, pc, needK, pcost, ph in ELEM_PREDS:
            for K, getK, kc in (Ks if needK else [(None, (lambda u: None), 0.0)]):
                P = _t(pc, K=K or "0")
                d = _t(pdesc, K=K or "")
                yield _mk(f"seq:filter:{pk}:{a}:{K}", f"elementy {d} ({a})", "seq", 3.0 + pcost + kc, set(ptags),
                          (lambda u, pf=pf, a=a, getK=getK: [x for x in u[a] if pf(x, getK(u))]),
                          "vector<long long> «r»;\nfor (long long «x» : «a») if («P») «r».push_back(«x»);", "seq",
                          cx="n", work=W(a), helpers=ph, a=a, P=P)
                yield _mk(f"seq:idx:{pk}:{a}:{K}", f"numery (od 1) elementów {d} ({a})", "seq", 3.3 + pcost + kc,
                          set(ptags) | {"position"},
                          (lambda u, pf=pf, a=a, getK=getK: [i + 1 for i, x in enumerate(u[a]) if pf(x, getK(u))]),
                          "vector<long long> «r»;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) {\n    long long «x» = «a»[«i»];\n"
                          "    if («P») «r».push_back(«i» + 1);\n}", "seq", cx="n", work=W(a), helpers=ph, a=a, P=P)


_CNT_TPL = ("vector<long long> «r»(«a».size(), 0);\nfor (size_t «i» = 0; «i» < «a».size(); «i»++)\n"
            "    for (size_t «j» = 0; «j» < «a».size(); «j»++)\n        if («J» && «REL») «r»[«i»]++;")
_NEXT_TPL = ("vector<long long> «r»(«a».size(), «NI»);\nfor (size_t «i» = 0; «i» < «a».size(); «i»++)\n"
             "    for (size_t «j» = «FROM»; «COND»; «STEP»)\n        if («REL») { «r»[«i»] = «a»[«j»]; break; }")
_PREV_TPL = ("vector<long long> «r»(«a».size(), «NI»);\nfor (size_t «i» = 0; «i» < «a».size(); «i»++)\n"
             "    for (size_t «j» = «i»; «j» > 0; «j»--)\n        if («REL») { «r»[«i»] = «a»[«j» - 1]; break; }")
_WIN_TPL = ("vector<long long> «r»;\nfor (long long «i» = 0; «i» + «K» <= (long long)«a».size(); «i»++) {\n"
            "    long long «s» = «INIT»;\n    for (long long «j» = «i»; «j» < «i» + «K»; «j»++) «AGG»;\n    «r».push_back(«s»);\n}")


def _next_by(v, i, rel, NI):
    for j in range(i + 1, len(v)):
        if rel(v[i], v[j]):
            return v[j]
    return NI


def _prev_by(v, i, rel, NI):
    for j in range(i - 1, -1, -1):
        if rel(v[i], v[j]):
            return v[j]
    return NI


def _rotl(v, k):
    if not v:
        return []
    k %= len(v)
    return v[k:] + v[:k]


def _windows(v, k, f):
    if not isinstance(k, int) or k < 1:
        raise Bad
    return [_ck(f(v[i:i + k])) for i in range(len(v) - k + 1)]


# ---------------------------------------------------------------------------------------- pary, trójki
PAIR_PREDS = [
    ("sumK", "a[i] + a[j] = «K»", ("sum", "equal"), lambda x, y, K: x + y == K, "«x» + «y» == «K»", True, 1.0),
    ("sumleK", "a[i] + a[j] ≤ «K»", ("sum", "atmost"), lambda x, y, K: x + y <= K, "«x» + «y» <= «K»", True, 1.1),
    ("sumltK", "a[i] + a[j] < «K»", ("sum",), lambda x, y, K: x + y < K, "«x» + «y» < «K»", True, 1.2),
    ("sumgeK", "a[i] + a[j] ≥ «K»", ("sum", "atleast"), lambda x, y, K: x + y >= K, "«x» + «y» >= «K»", True, 1.1),
    ("sumgtK", "a[i] + a[j] > «K»", ("sum",), lambda x, y, K: x + y > K, "«x» + «y» > «K»", True, 1.2),
    ("inv", "a[i] > a[j]", ("inversion",), lambda x, y, K: x > y, "«x» > «y»", False, 0.8),
    ("asc", "a[i] < a[j]", ("increasing",), lambda x, y, K: x < y, "«x» < «y»", False, 0.9),
    ("eq", "a[i] = a[j]", ("equal",), lambda x, y, K: x == y, "«x» == «y»", False, 0.8),
    ("ne", "a[i] ≠ a[j]", ("distinct",), lambda x, y, K: x != y, "«x» != «y»", False, 1.0),
    ("diffK", "|a[i] − a[j]| = «K»", ("diff", "equal"), lambda x, y, K: abs(x - y) == K, "llabs(«x» - «y») == «K»", True, 1.1),
    ("diffleK", "|a[i] − a[j]| ≤ «K»", ("diff", "atmost"), lambda x, y, K: abs(x - y) <= K, "llabs(«x» - «y») <= «K»", True, 1.2),
    ("diffgeK", "|a[i] − a[j]| ≥ «K»", ("diff", "atleast"), lambda x, y, K: abs(x - y) >= K, "llabs(«x» - «y») >= «K»", True, 1.2),
    ("sumdivK", "a[i] + a[j] podzielne przez «K»", ("sum", "div"), lambda x, y, K: K != 0 and (x + y) % K == 0,
     "«K» != 0 && («x» + «y») % «K» == 0", True, 1.2),
    ("evensum", "a[i] + a[j] parzyste", ("sum", "even"), lambda x, y, K: (x + y) % 2 == 0, "(«x» + «y») % 2 == 0", False, 1.2),
    ("proddivK", "a[i]·a[j] podzielne przez «K»", ("product", "div"), lambda x, y, K: K != 0 and (x * y) % K == 0,
     "«K» != 0 && («x» * «y») % «K» == 0", True, 1.3),
    ("prodK", "a[i]·a[j] = «K»", ("product", "equal"), lambda x, y, K: x * y == K, "«x» * «y» == «K»", True, 1.2),
    ("prodleK", "a[i]·a[j] ≤ «K»", ("product",), lambda x, y, K: x * y <= K, "«x» * «y» <= «K»", True, 1.3),
    ("coprime", "NWD(a[i], a[j]) = 1", ("gcd", "coprime"), lambda x, y, K: math.gcd(x, y) == 1, "gcd(«x», «y») == 1", False, 1.2),
    ("ncoprime", "NWD(a[i], a[j]) > 1", ("gcd",), lambda x, y, K: math.gcd(x, y) > 1, "gcd(«x», «y») > 1", False, 1.3),
    ("divides", "jedna liczba dzieli drugą", ("div",), lambda x, y, K: (y != 0 and x % y == 0) or (x != 0 and y % x == 0),
     "(«y» != 0 && «x» % «y» == 0) || («x» != 0 && «y» % «x» == 0)", False, 1.3),
    ("xorK", "a[i] xor a[j] = «K»", ("xor",), lambda x, y, K: (x ^ y) == K, "(«x» ^ «y») == «K»", True, 1.3),
    ("sumprime", "a[i] + a[j] jest pierwsza", ("prime", "sum"), lambda x, y, K: _isprime(x + y), "isprime(«x» + «y»)",
     False, 1.4),
]
PAIR_FUNCS = [
    ("sum", "a[i] + a[j]", ("sum",), lambda x, y: x + y, "«x» + «y»", 0.0),
    ("prod", "a[i]·a[j]", ("product",), lambda x, y: x * y, "«x» * «y»", 0.2),
    ("adiff", "|a[i] − a[j]|", ("diff",), lambda x, y: abs(x - y), "llabs(«x» - «y»)", 0.2),
    ("later", "a[j] − a[i] (j > i)", ("diff",), lambda x, y: y - x, "«y» - «x»", 0.4),
    ("xor", "a[i] xor a[j]", ("xor",), lambda x, y: x ^ y, "«x» ^ «y»", 0.5),
    ("gcd", "NWD(a[i], a[j])", ("gcd",), lambda x, y: math.gcd(x, y), "gcd(«x», «y»)", 0.5),
]
_PAIR_LOOP = ("for (size_t «i» = 0; «i» < «a».size(); «i»++)\n    for (size_t «j» = «i» + 1; «j» < «a».size(); «j»++) {\n"
              "        long long «x» = «a»[«i»], «y» = «a»[«j»];\n«BODY»\n    }")


def _pairs_iter(v):
    n = len(v)
    for i in range(n):
        x = v[i]
        for j in range(i + 1, n):
            yield x, v[j]


def fam_pairs(ctx: Ctx):
    """Wszystkie pary i < j: ile/czy istnieje para z warunkiem, największa/najmniejsza wartość funkcji pary."""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    Ks = _k_choices(ctx)
    for a, _ln in ctx.roles["arrays"] + (ctx.roles["cols"] if len(ctx.roles["rows"]) == 1 else []):
        W = lambda u, a=a: len(u[a]) ** 2 // 2 + 1  # noqa: E731
        for pk, pdesc, ptags, pf, pc, needK, pcost in PAIR_PREDS:
            for K, getK, kc in (Ks if needK else [(None, (lambda u: None), 0.0)]):
                P = _t(pc, K=K or "0")
                d = _t(pdesc, K=K or "")
                hp = ("isprime",) if "isprime" in pc else ()
                body_cnt = "        if («P») «r»++;"
                yield _mk(f"pairs:cnt:{pk}:{a}:{K}", f"liczba par i < j: {d}", "pairs", 3.0 + pcost + kc,
                          set(ptags) | {"pairs", "count"},
                          (lambda u, pf=pf, a=a, getK=getK: (lambda K_: sum(1 for x, y in _pairs_iter(u[a]) if pf(x, y, K_)))(getK(u))),
                          "long long «r» = 0;\n" + _PAIR_LOOP.replace("«BODY»", body_cnt), "int", cx="n^2", work=W,
                          helpers=hp, a=a, P=P)
                yield _mk(f"pairs:any:{pk}:{a}:{K}", f"czy istnieje para i < j: {d}", "pairs", 3.3 + pcost + kc,
                          set(ptags) | {"pairs", "exists"},
                          (lambda u, pf=pf, a=a, getK=getK: (lambda K_: any(pf(x, y, K_) for x, y in _pairs_iter(u[a])))(getK(u))),
                          "bool «r» = false;\n" + _PAIR_LOOP.replace("«BODY»", "        if («P») «r» = true;"), "bool",
                          cx="n^2", work=W, helpers=hp, a=a, P=P)
        for fk, fdesc, ftags, ff, fc, fcost in PAIR_FUNCS:
            for obj, odesc, otag, agg, init, upd in (("max", "największa", "max", max, "LLONG_MIN", "max"),
                                                    ("min", "najmniejsza", "min", min, "LLONG_MAX", "min")):
                yield _mk(f"pairs:{obj}:{fk}:{a}", f"{odesc} wartość {fdesc} po parach i < j", "pairs", 3.0 + fcost,
                          set(ftags) | {"pairs", otag},
                          (lambda u, ff=ff, a=a, agg=agg: _ck(agg((ff(x, y) for x, y in _pairs_iter(u[a])), default=NONE)
                                                              if len(u[a]) >= 2 else NONE)),
                          f"long long «r» = {init};\n" + _PAIR_LOOP.replace("«BODY»", f"        «r» = {upd}(«r», «F»);"),
                          "int", "«a».size() < 2", cx="n^2", work=W, a=a, F=fc)
            for K, getK, kc in Ks:
                if fk not in ("sum", "prod", "adiff"):
                    continue
                yield _mk(f"pairs:maxle:{fk}:{a}:{K}", f"największa wartość {fdesc} ≤ {K} po parach i < j", "pairs",
                          3.6 + fcost + kc, set(ftags) | {"pairs", "max"},
                          (lambda u, ff=ff, a=a, getK=getK: (lambda K_: max((ff(x, y) for x, y in _pairs_iter(u[a])
                                                                             if ff(x, y) <= K_), default=NONE))(getK(u))),
                          "long long «r» = LLONG_MIN;\n" + _PAIR_LOOP.replace("«BODY»", "        if ((«F») <= «K») «r» = max(«r», «F»);"),
                          "int", "«r» == LLONG_MIN", cx="n^2", work=W, a=a, F=fc, K=K)
                yield _mk(f"pairs:closest:{fk}:{a}:{K}", f"najmniejsza |{fdesc} − {K}| po parach i < j", "pairs",
                          3.8 + fcost + kc, set(ftags) | {"pairs", "min", "diff"},
                          (lambda u, ff=ff, a=a, getK=getK: (lambda K_: min((abs(ff(x, y) - K_) for x, y in _pairs_iter(u[a])),
                                                                            default=NONE))(getK(u))),
                          "long long «r» = LLONG_MAX;\n" + _PAIR_LOOP.replace("«BODY»", "        «r» = min(«r», llabs((«F») - «K»));"),
                          "int", "«r» == LLONG_MAX", cx="n^2", work=W, a=a, F=fc, K=K)
        # trójki
        W3 = lambda u, a=a: len(u[a]) ** 3 // 6 + 1  # noqa: E731
        trip = [
            ("tri", "liczba trójek tworzących trójkąt", ("triangle", "triples", "count"), 1.0,
             lambda v, K: sum(1 for t in itertools.combinations(v, 3) if _tri_ok(t)), "if («TRI») «r»++;", "int", "0", None),
            ("trianyc", "czy jakieś trzy tworzą trójkąt", ("triangle", "triples", "exists"), 1.2,
             lambda v, K: any(_tri_ok(t) for t in itertools.combinations(v, 3)), "if («TRI») «r» = true;", "bool", "false", None),
            ("triper", "największy obwód trójkąta", ("triangle", "triples", "max"), 1.4,
             lambda v, K: max((sum(t) for t in itertools.combinations(v, 3) if _tri_ok(t)), default=NONE),
             "if («TRI») «r» = max(«r», «x» + «y» + «z»);", "int", "LLONG_MIN", "«r» == LLONG_MIN"),
            ("incr", "liczba trójek i < j < l z a[i] < a[j] < a[l]", ("increasing", "triples", "count"), 1.4,
             lambda v, K: sum(1 for x, y, z in itertools.combinations(v, 3) if x < y < z), "if («x» < «y» && «y» < «z») «r»++;",
             "int", "0", None),
        ]
        for key, desc, tags, cost, f, body, kind, init, none in trip:
            yield _mk(f"trip:{key}:{a}", desc, "triples", 3.5 + cost, set(tags),
                      (lambda u, f=f, a=a: f(u[a], None)),
                      _TRIP_TPL.replace("«BODY»", body).replace("«INIT»", init).replace("«TY»", "bool" if kind == "bool" else "long long"),
                      kind, none, cx="n^3", work=W3, a=a, TRI=_TRI_C)
        for K, getK, kc in Ks:
            for key, desc, tags, cost, f, body in [
                ("sumK", f"liczba trójek o sumie {K}", ("triples", "sum", "count", "equal"), 1.0,
                 lambda v, k: sum(1 for t in itertools.combinations(v, 3) if sum(t) == k), "if («x» + «y» + «z» == «K») «r»++;"),
                ("sumleK", f"liczba trójek o sumie ≤ {K}", ("triples", "sum", "count"), 1.2,
                 lambda v, k: sum(1 for t in itertools.combinations(v, 3) if sum(t) <= k), "if («x» + «y» + «z» <= «K») «r»++;"),
            ]:
                yield _mk(f"trip:{key}:{a}:{K}", desc, "triples", 3.5 + cost + kc, set(tags),
                          (lambda u, f=f, a=a, getK=getK: f(u[a], getK(u))),
                          _TRIP_TPL.replace("«BODY»", body).replace("«INIT»", "0").replace("«TY»", "long long"), "int",
                          cx="n^3", work=W3, a=a, K=K)


_TRI_C = "max({«x», «y», «z»}) * 2 < «x» + «y» + «z»"
_TRIP_TPL = ("«TY» «r» = «INIT»;\nfor (size_t «i» = 0; «i» < «a».size(); «i»++)\n"
             "    for (size_t «j» = «i» + 1; «j» < «a».size(); «j»++)\n        for (size_t «l» = «j» + 1; «l» < «a».size(); «l»++) {\n"
             "            long long «x» = «a»[«i»], «y» = «a»[«j»], «z» = «a»[«l»];\n            «BODY»\n        }")


def _tri_ok(t):
    return max(t) * 2 < sum(t)


# ---------------------------------------------------------------------------------------- spójne fragmenty
def _cache(u: dict, key: str, fn):
    k = "#c:" + key
    v = u.get(k)
    if v is None:
        v = fn()
        u[k] = v
    return v


def _sub_vals(v: list, g: str) -> list[tuple[int, int, int]]:
    """(początek, długość, wartość agregatu) dla wszystkich spójnych fragmentów."""
    n = len(v)
    out = []
    for i in range(n):
        s = 0
        hi, lo = None, None
        seen = set()
        for j in range(i, n):
            x = v[j]
            if g == "sum":
                s += x
                val = s
            elif g == "xor":
                s ^= x
                val = s
            elif g == "max":
                hi = x if hi is None or x > hi else hi
                val = hi
            elif g == "min":
                lo = x if lo is None or x < lo else lo
                val = lo
            elif g == "len":
                val = j - i + 1
            elif g == "distinct":
                seen.add(x)
                val = len(seen)
            else:  # range
                hi = x if hi is None or x > hi else hi
                lo = x if lo is None or x < lo else lo
                val = hi - lo
            out.append((i, j - i + 1, val))
    return out


SUB_AGGS = [
    ("sum", "suma", ("sum",), "long long «s» = 0;", "«s» += «a»[«j»];", "«s»", 0.0),
    ("max", "maksimum", ("max",), "long long «s» = LLONG_MIN;", "«s» = max(«s», «a»[«j»]);", "«s»", 0.4),
    ("min", "minimum", ("min",), "long long «s» = LLONG_MAX;", "«s» = min(«s», «a»[«j»]);", "«s»", 0.4),
    ("len", "długość", ("len",), "", "", "(long long)(«j» - «i» + 1)", 0.3),
    ("xor", "xor", ("xor",), "long long «s» = 0;", "«s» ^= «a»[«j»];", "«s»", 0.6),
    ("distinct", "liczba różnych wartości", ("distinct",), "set<long long> «b»;", "«b».insert(«a»[«j»]);",
     "(long long)«b».size()", 0.6),
    ("range", "max − min", ("diff", "max", "min"), "long long «h» = LLONG_MIN, «l» = LLONG_MAX;",
     "«h» = max(«h», «a»[«j»]);\n        «l» = min(«l», «a»[«j»]);", "(«h» - «l»)", 0.7),
]
SUB_CMPS = [("eq", "=", "==", lambda x, k: x == k, 0.0, ("equal",)), ("le", "≤", "<=", lambda x, k: x <= k, 0.1, ("atmost",)),
            ("lt", "<", "<", lambda x, k: x < k, 0.3, ()), ("ge", "≥", ">=", lambda x, k: x >= k, 0.1, ("atleast",)),
            ("gt", ">", ">", lambda x, k: x > k, 0.3, ())]
SUB_OBJS = [
    ("cnt", "liczba fragmentów, w których", ("count",), "long long «r» = 0;", "«r»++;", "int", None, 0.0,
     lambda xs: sum(1 for _ in xs)),
    ("long", "najdłuższy fragment, w którym", ("max", "len"), "long long «r» = 0;",
     "«r» = max(«r», (long long)(«j» - «i» + 1));", "int", None, 0.2, lambda xs: max((L for L in xs), default=0)),
    ("short", "najkrótszy fragment, w którym", ("min", "len"), "long long «r» = LLONG_MAX;",
     "«r» = min(«r», (long long)(«j» - «i» + 1));", "int", "«r» == LLONG_MAX", 0.3,
     lambda xs: min((L for L in xs), default=NONE)),
    ("any", "czy istnieje fragment, w którym", ("exists",), "bool «r» = false;", "«r» = true;", "bool", None, 0.4,
     lambda xs: any(True for _ in xs)),
]
_SUB_TPL = ("«INIT»\nfor (size_t «i» = 0; «i» < «a».size(); «i»++) {\n    «GI»\n"
            "    for (size_t «j» = «i»; «j» < «a».size(); «j»++) {\n        «GS»\n        if («COND») «UPD»\n    }\n}")


def fam_subarrays(ctx: Ctx):
    """Wszystkie spójne fragmenty [i, j]: agregat (suma, max, min, długość, xor, różne, max−min) porównany z k →
    ile / najdłuższy / najkrótszy / czy istnieje; oraz największa/najmniejsza suma fragmentu."""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    Ks = _k_choices(ctx)
    for a, _ln in ctx.roles["arrays"] + (ctx.roles["cols"] if len(ctx.roles["rows"]) == 1 else []):
        W = lambda u, a=a: len(u[a]) ** 2 // 2 + 1  # noqa: E731
        for gk, gdesc, gtags, ginit, gstep, gval, gcost in SUB_AGGS:
            for K, getK, kc in Ks:
                for ck, cdesc, cop, cf, ccost, ctags in SUB_CMPS:
                    if gk == "len" and ck in ("eq",):
                        continue
                    for ok, odesc, otags, oinit, oupd, okind, onone, ocost, of in SUB_OBJS:
                        if gk == "len" and ok in ("long", "short"):
                            continue

                        def run(u, a=a, gk=gk, getK=getK, cf=cf, of=of):
                            k = getK(u)
                            vals = _cache(u, f"sub:{a}:{gk}", lambda: _sub_vals(u[a], gk))
                            return of(L for _i, L, v in vals if cf(v, k))
                        tpl = _SUB_TPL.replace("«INIT»", oinit).replace("«GI»", ginit).replace("«GS»", gstep) \
                            .replace("«COND»", f"{gval} {cop} «K»").replace("«UPD»", oupd)
                        yield _mk(f"sub:{ok}:{gk}{ck}:{a}:{K}", f"{odesc} {gdesc} {cdesc} {K}", "subarray",
                                  3.2 + gcost + ccost + ocost + kc, set(gtags) | set(otags) | set(ctags) | {"subarray"}, run, tpl,
                                  okind, onone, cx="n^2", work=W, a=a, K=K)
        for gk, gdesc, gtags, ginit, gstep, gval, gcost in SUB_AGGS:
            if gk not in ("sum", "xor"):
                continue
            for obj, odesc, otag, agg, init in (("max", "największa", "max", max, "LLONG_MIN"),
                                               ("min", "najmniejsza", "min", min, "LLONG_MAX")):
                def run(u, a=a, gk=gk, agg=agg):
                    vals = _cache(u, f"sub:{a}:{gk}", lambda: _sub_vals(u[a], gk))
                    return agg((v for _i, _L, v in vals), default=NONE)
                tpl = _SUB_TPL.replace("«INIT»", f"long long «r» = {init};").replace("«GI»", ginit).replace("«GS»", gstep) \
                    .replace("«COND»", "true").replace("«UPD»", f"«r» = {obj}(«r», {gval});")
                yield _mk(f"sub:{obj}:{gk}:{a}", f"{odesc} {gdesc} spójnego fragmentu", "subarray", 3.0 + gcost,
                          set(gtags) | {otag, "subarray"}, run, tpl, "int", "«a».empty()", cx="n^2", work=W, a=a)
        # fragmenty o sumie 0 / z różnymi elementami / suma podzielna przez K / okna długości K
        extra = [
            ("zero", "liczba fragmentów o sumie 0", ("sum", "count", "subarray"), 1.0, "sum",
             lambda L, v, k: v == 0, "long long «r» = 0;", "«s» == 0", "«r»++;", "int", None, lambda xs: sum(1 for _ in xs)),
            ("zerolong", "najdłuższy fragment o sumie 0", ("sum", "max", "len", "subarray"), 1.2, "sum",
             lambda L, v, k: v == 0, "long long «r» = 0;", "«s» == 0", "«r» = max(«r», (long long)(«j» - «i» + 1));", "int",
             None, lambda xs: max(xs, default=0)),
            ("alldist", "liczba fragmentów bez powtórzeń", ("distinct", "count", "subarray"), 1.2, "distinct",
             lambda L, v, k: v == L, "long long «r» = 0;", "(long long)«b».size() == (long long)(«j» - «i» + 1)", "«r»++;",
             "int", None, lambda xs: sum(1 for _ in xs)),
            ("alldistlong", "najdłuższy fragment bez powtórzeń", ("distinct", "max", "len", "subarray"), 1.2, "distinct",
             lambda L, v, k: v == L, "long long «r» = 0;", "(long long)«b».size() == (long long)(«j» - «i» + 1)",
             "«r» = max(«r», (long long)(«j» - «i» + 1));", "int", None, lambda xs: max(xs, default=0)),
        ]
        for key, desc, tags, cost, gk, cond, init, ccond, upd, kind, none, of in extra:
            g = next(x for x in SUB_AGGS if x[0] == gk)

            def run(u, a=a, gk=gk, cond=cond, of=of):
                vals = _cache(u, f"sub:{a}:{gk}", lambda: _sub_vals(u[a], gk))
                return of(L for _i, L, v in vals if cond(L, v, None))
            tpl = _SUB_TPL.replace("«INIT»", init).replace("«GI»", g[3]).replace("«GS»", g[4]).replace("«COND»", ccond) \
                .replace("«UPD»", upd)
            yield _mk(f"sub:{key}:{a}", desc, "subarray", 3.2 + cost, set(tags), run, tpl, kind, none, cx="n^2", work=W, a=a)
        for K, getK, kc in Ks:
            def run_div(u, a=a, getK=getK):
                k = getK(u)
                if not k:
                    raise Bad
                vals = _cache(u, f"sub:{a}:sum", lambda: _sub_vals(u[a], "sum"))
                return sum(1 for _i, _L, v in vals if v % k == 0)
            tpl = _SUB_TPL.replace("«INIT»", "long long «r» = 0;").replace("«GI»", "long long «s» = 0;") \
                .replace("«GS»", "«s» += «a»[«j»];").replace("«COND»", "«s» % «K» == 0").replace("«UPD»", "«r»++;")
            yield _mk(f"sub:cntdiv:{a}:{K}", f"liczba fragmentów o sumie podzielnej przez {K}", "subarray", 4.2 + kc,
                      {"sum", "div", "count", "subarray"}, run_div, tpl, "int", cx="n^2", work=W, a=a, K=K)
            for obj, odesc, agg, init in (("max", "największa", max, "LLONG_MIN"), ("min", "najmniejsza", min, "LLONG_MAX")):
                def run_w(u, a=a, getK=getK, agg=agg):
                    k = getK(u)
                    vals = _cache(u, f"sub:{a}:sum", lambda: _sub_vals(u[a], "sum"))
                    return agg((v for _i, L, v in vals if L == k), default=NONE)
                tpl = _SUB_TPL.replace("«INIT»", f"long long «r» = {init};").replace("«GI»", "long long «s» = 0;") \
                    .replace("«GS»", "«s» += «a»[«j»];").replace("«COND»", "(long long)(«j» - «i» + 1) == «K»") \
                    .replace("«UPD»", f"«r» = {obj}(«r», «s»);")
                yield _mk(f"sub:win{obj}:{a}:{K}", f"{odesc} suma {K} kolejnych elementów", "subarray", 3.4 + kc,
                          {"sum", obj, "window", "subarray"}, run_w, tpl, "int", f"«r» == {init}", cx="n^2", work=W, a=a, K=K)


# ---------------------------------------------------------------------------------------- podzbiory
def _subset_data(v: list):
    n = len(v)
    if n > 18:
        raise Bad
    N = 1 << n
    sums = [0] * N
    xors = [0] * N
    cnts = [0] * N
    for m in range(1, N):
        low = m & -m
        b = low.bit_length() - 1
        p = m ^ low
        sums[m] = sums[p] + v[b]
        xors[m] = xors[p] ^ v[b]
        cnts[m] = cnts[p] + 1
    return sums, xors, cnts


_SUBSET_TPL = ("«INIT»\nint «m» = «a».size();\nfor (long long «mask» = 0; «mask» < (1LL << «m»); «mask»++) {\n"
               "    long long «s» = 0, «c» = 0, «x» = 0;\n"
               "    for (int «b» = 0; «b» < «m»; «b»++) if («mask» >> «b» & 1) { «s» += «a»[«b»]; «c»++; «x» ^= «a»[«b»]; }\n"
               "    «UPD»\n}")


def fam_subsets(ctx: Ctx):
    """Wszystkie podzbiory (2^n): ile/czy o sumie k, największa suma ≤ k, najmniej elementów o sumie ≥ k, podział…"""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    Ks = _k_choices(ctx)
    for a, _ln in ctx.roles["arrays"] + (ctx.roles["cols"] if len(ctx.roles["rows"]) == 1 else []):
        W = lambda u, a=a: (1 << len(u[a])) * 2 if len(u[a]) <= 18 else 10**12  # noqa: E731

        def D(u, a=a):
            return _cache(u, f"subset:{a}", lambda: _subset_data(u[a]))
        noK = [
            ("part", "podział na dwie grupy: najmniejsza różnica sum", ("subset", "diff", "min", "sum"), 0.8,
             lambda u, a=a: min(abs(sum(u[a]) - 2 * s) for s in D(u)[0]),
             "long long «t» = 0, «r» = LLONG_MAX;\nfor (long long «e» : «a») «t» += «e»;", "«r» = min(«r», llabs(«t» - 2 * «s»));",
             "int", None),
            ("halves", "czy da się podzielić na dwie grupy o równej sumie", ("subset", "equal", "sum", "exists"), 1.0,
             lambda u, a=a: any(2 * s == sum(u[a]) for s in D(u)[0]),
             "long long «t» = 0;\nbool «r» = false;\nfor (long long «e» : «a») «t» += «e»;", "if (2 * «s» == «t») «r» = true;",
             "bool", None),
            ("distsums", "ile różnych sum niepustych podzbiorów", ("subset", "sum", "distinct", "count"), 1.0,
             lambda u, a=a: len(set(D(u)[0][1:])), "set<long long> «o»;", "if («c» > 0) «o».insert(«s»);", "int", None),
            ("maxxor", "największy xor podzbioru", ("subset", "xor", "max"), 1.2, lambda u, a=a: max(D(u)[1]),
             "long long «r» = 0;", "«r» = max(«r», «x»);", "int", None),
            ("nonadj", "największa suma elementów, z których żadne dwa nie sąsiadują", ("subset", "adjacent", "max", "sum"),
             1.2, lambda u, a=a: max(s for m, s in enumerate(D(u)[0]) if m & (m >> 1) == 0),
             "long long «r» = 0;", "if (!(«mask» & («mask» >> 1))) «r» = max(«r», «s»);", "int", None),
        ]
        for key, desc, tags, cost, f, init, upd, kind, none in noK:
            tpl = _SUBSET_TPL.replace("«INIT»", init).replace("«UPD»", upd)
            if key == "distsums":
                tpl += "\nlong long «r» = «o».size();"
            yield _mk(f"subset:{key}:{a}", desc, "subset", 3.5 + cost, set(tags), f, tpl, kind, none, cx="2^n·n", work=W, a=a)
        for K, getK, kc in Ks:
            withK = [
                ("cnt", f"ile niepustych podzbiorów ma sumę {K}", ("subset", "sum", "count", "equal"), 0.6,
                 lambda d, k: sum(1 for m, s in enumerate(d[0]) if m and s == k), "long long «r» = 0;",
                 "if («c» > 0 && «s» == «K») «r»++;", "int", None),
                ("cnt0", f"ile podzbiorów (także pustych) ma sumę {K}", ("subset", "sum", "count", "equal"), 0.9,
                 lambda d, k: sum(1 for s in d[0] if s == k), "long long «r» = 0;", "if («s» == «K») «r»++;", "int", None),
                ("any", f"czy jakiś niepusty podzbiór ma sumę {K}", ("subset", "sum", "exists", "equal"), 0.6,
                 lambda d, k: any(m and s == k for m, s in enumerate(d[0])), "bool «r» = false;",
                 "if («c» > 0 && «s» == «K») «r» = true;", "bool", None),
                ("maxle", f"największa suma podzbioru ≤ {K}", ("subset", "sum", "max"), 0.7,
                 lambda d, k: max((s for s in d[0] if s <= k), default=NONE), "long long «r» = LLONG_MIN;",
                 "if («s» <= «K») «r» = max(«r», «s»);", "int", "«r» == LLONG_MIN"),
                ("minge", f"najmniejsza suma podzbioru ≥ {K}", ("subset", "sum", "min"), 0.8,
                 lambda d, k: min((s for s in d[0] if s >= k), default=NONE), "long long «r» = LLONG_MAX;",
                 "if («s» >= «K») «r» = min(«r», «s»);", "int", "«r» == LLONG_MAX"),
                ("cntle", f"ile niepustych podzbiorów ma sumę ≤ {K}", ("subset", "sum", "count"), 0.9,
                 lambda d, k: sum(1 for m, s in enumerate(d[0]) if m and s <= k), "long long «r» = 0;",
                 "if («c» > 0 && «s» <= «K») «r»++;", "int", None),
                ("minsizege", f"najmniej elementów o sumie ≥ {K}", ("subset", "sum", "min", "count"), 0.9,
                 lambda d, k: min((c for s, c in zip(d[0], d[2]) if s >= k), default=NONE), "long long «r» = LLONG_MAX;",
                 "if («s» >= «K») «r» = min(«r», «c»);", "int", "«r» == LLONG_MAX"),
                ("minsizeeq", f"najmniej elementów o sumie dokładnie {K}", ("subset", "sum", "min", "count", "equal"), 0.9,
                 lambda d, k: min((c for s, c in zip(d[0], d[2]) if s == k), default=NONE), "long long «r» = LLONG_MAX;",
                 "if («s» == «K») «r» = min(«r», «c»);", "int", "«r» == LLONG_MAX"),
                ("maxsizele", f"najwięcej elementów o sumie ≤ {K}", ("subset", "sum", "max", "count"), 0.9,
                 lambda d, k: max((c for s, c in zip(d[0], d[2]) if s <= k), default=NONE), "long long «r» = LLONG_MIN;",
                 "if («s» <= «K») «r» = max(«r», «c»);", "int", "«r» == LLONG_MIN"),
                ("cntdiv", f"ile niepustych podzbiorów ma sumę podzielną przez {K}", ("subset", "sum", "div", "count"), 1.1,
                 lambda d, k: sum(1 for m, s in enumerate(d[0]) if m and k and s % k == 0) if k else _raise(),
                 "long long «r» = 0;", "if («c» > 0 && «s» % «K» == 0) «r»++;", "int", None),
                ("xorK", f"ile niepustych podzbiorów ma xor = {K}", ("subset", "xor", "count"), 1.2,
                 lambda d, k: sum(1 for m, x in enumerate(d[1]) if m and x == k), "long long «r» = 0;",
                 "if («c» > 0 && «x» == «K») «r»++;", "int", None),
            ]
            for key, desc, tags, cost, f, init, upd, kind, none in withK:
                yield _mk(f"subset:{key}:{a}:{K}", desc, "subset", 3.5 + cost + kc, set(tags),
                          (lambda u, f=f, D=D, getK=getK: f(D(u), getK(u))),
                          _SUBSET_TPL.replace("«INIT»", init).replace("«UPD»", upd), kind, none, cx="2^n·n", work=W,
                          a=a, K=K)


# ---------------------------------------------------------------------------------------- zapytania (naiwnie)
RANGE_AGGS = [
    ("sum", "suma", ("sum",), lambda w: _ck(sum(w)), "long long «s» = 0;", "«s» += «a»[«i»];", "«s»"),
    ("min", "minimum", ("min",), lambda w: min(_need_nonempty(w)), "long long «s» = LLONG_MAX;", "«s» = min(«s», «a»[«i»]);", "«s»"),
    ("max", "maksimum", ("max",), lambda w: max(_need_nonempty(w)), "long long «s» = LLONG_MIN;", "«s» = max(«s», «a»[«i»]);", "«s»"),
    ("xor", "xor", ("xor",), _xor_all, "long long «s» = 0;", "«s» ^= «a»[«i»];", "«s»"),
    ("gcd", "NWD", ("gcd",), _gcd_all, "long long «s» = 0;", "«s» = gcd(«s», «a»[«i»]);", "«s»"),
    ("distinct", "liczba różnych", ("distinct",), lambda w: len(set(w)), "set<long long> «b»;", "«b».insert(«a»[«i»]);",
     "(long long)«b».size()"),
    ("range", "max − min", ("diff",), lambda w: max(_need_nonempty(w)) - min(w),
     "long long «h» = LLONG_MIN, «l» = LLONG_MAX;", "{ «h» = max(«h», «a»[«i»]); «l» = min(«l», «a»[«i»]); }", "(«h» - «l»)"),
]
VALUE_QUERIES = [
    ("eq", "ile elementów = x", ("count", "equal", "occur"), lambda v, x: sum(1 for y in v if y == x), "long long «s» = 0;",
     "if («e» == «x») «s»++;", "int", None),
    ("lt", "ile elementów < x", ("count",), lambda v, x: sum(1 for y in v if y < x), "long long «s» = 0;",
     "if («e» < «x») «s»++;", "int", None),
    ("le", "ile elementów ≤ x", ("count",), lambda v, x: sum(1 for y in v if y <= x), "long long «s» = 0;",
     "if («e» <= «x») «s»++;", "int", None),
    ("gt", "ile elementów > x", ("count",), lambda v, x: sum(1 for y in v if y > x), "long long «s» = 0;",
     "if («e» > «x») «s»++;", "int", None),
    ("ge", "ile elementów ≥ x", ("count",), lambda v, x: sum(1 for y in v if y >= x), "long long «s» = 0;",
     "if («e» >= «x») «s»++;", "int", None),
    ("in", "czy x występuje", ("exists",), lambda v, x: x in v, "bool «s» = false;", "if («e» == «x») «s» = true;", "bool", None),
    ("first", "pierwsza pozycja x (od 1)", ("position",), lambda v, x: next((i + 1 for i, y in enumerate(v) if y == x), NONE),
     "long long «s» = -1;", "if («e» == «x» && «s» == -1) «s» = «p» + 1;", "int", "«s» == -1"),
    ("last", "ostatnia pozycja x (od 1)", ("position",),
     lambda v, x: next((len(v) - i for i, y in enumerate(reversed(v)) if y == x), NONE), "long long «s» = -1;",
     "if («e» == «x») «s» = «p» + 1;", "int", "«s» == -1"),
    ("ceil", "najmniejszy element ≥ x", ("min",), lambda v, x: min((y for y in v if y >= x), default=NONE),
     "long long «s» = LLONG_MAX;", "if («e» >= «x») «s» = min(«s», «e»);", "int", "«s» == LLONG_MAX"),
    ("floor", "największy element ≤ x", ("max",), lambda v, x: max((y for y in v if y <= x), default=NONE),
     "long long «s» = LLONG_MIN;", "if («e» <= «x») «s» = max(«s», «e»);", "int", "«s» == LLONG_MIN"),
    ("sumle", "suma elementów ≤ x", ("sum",), lambda v, x: _ck(sum(y for y in v if y <= x)), "long long «s» = 0;",
     "if («e» <= «x») «s» += «e»;", "int", None),
    ("closest", "najmniejsza |a[i] − x|", ("diff", "min"), lambda v, x: min((abs(y - x) for y in v), default=NONE),
     "long long «s» = LLONG_MAX;", "«s» = min(«s», llabs(«e» - «x»));", "int", "«s» == LLONG_MAX"),
    ("mult", "ile elementów podzielnych przez x", ("div", "count"), lambda v, x: sum(1 for y in v if x and y % x == 0) if x else _raise(),
     "long long «s» = 0;", "if («e» % «x» == 0) «s»++;", "int", None),
    ("divs", "ile elementów dzieli x", ("divisors", "count"), lambda v, x: sum(1 for y in v if y and x % y == 0),
     "long long «s» = 0;", "if («e» != 0 && «x» % «e» == 0) «s»++;", "int", None),
]


def fam_queries(ctx: Ctx):
    """Zapytania liczone naiwnie: (l, r) → agregat a[l..r]; x → ile/czy/gdzie x w ciągu (każde od zera)."""
    arrays = ctx.roles["arrays"]
    if not arrays:
        return
    for a, _ln in arrays:
        # (l, r) w wierszach
        for R in ctx.roles["rows"]:
            if len(R.names) != 2:
                continue
            if _unit_exps(ctx, lambda u, R=R: len(u[R.names[0]])) is None:
                continue
            L_, H_ = R.names
            cnt = R.count.cpp() if R.count is not None else f"(int){L_}.size()"
            for base in (1, 0):
                for gk, gdesc, gtags, gf, ginit, gstep, gval in RANGE_AGGS:
                    def run(u, a=a, L_=L_, H_=H_, base=base, gf=gf):
                        v = u[a]
                        out = []
                        for l, r in zip(u[L_], u[H_]):
                            l, r = l - base, r - base
                            if l < 0 or r >= len(v) or l > r:
                                raise Bad
                            out.append(gf(v[l:r + 1]))
                        return out
                    off = f" - {base}" if base else ""
                    tpl = (f"for (int «q» = 0; «q» < {cnt}; «q»++) {{\n    long long «l» = {L_}[«q»]{off}, «h» = {H_}[«q»]{off};\n"
                           f"    {ginit.replace('«h»', '«o»').replace('«l»', '«d»')}\n"
                           f"    for (long long «i» = «l»; «i» <= «h»; «i»++) {gstep.replace('«h»', '«o»').replace('«l»', '«d»')}\n"
                           f"    «PRINT»\n}}")
                    gv = gval.replace("«h»", "«o»").replace("«l»", "«d»")
                    yield _q_cand(f"query:{gk}:{base}:{a}:{R.name}",
                                  f"dla każdego zapytania ({L_}, {H_}): {gdesc} {a}[{L_}..{H_}] (numeracja od {base})",
                                  3.0 + (0.3 if base == 0 else 0) + (0.5 if gk not in ("sum", "min", "max") else 0),
                                  set(gtags) | {"query", "subarray"}, run, tpl, gv, "int", None,
                                  lambda u, a=a, R=R: len(u[a]) * len(u[R.names[0]]) + 1, a=a)
        # pojedyncze x: drugi ciąg / wiersze z jedną liczbą
        for b, _lb in arrays:
            if b == a:
                continue
            if _unit_exps(ctx, lambda u, b=b: len(u[b])) is None:
                continue
            order = [x[0] for x in arrays]
            swapped = order.index(b) < order.index(a)
            for key, desc, tags, f, init, step, kind, none in VALUE_QUERIES:
                def run(u, a=a, b=b, f=f):
                    return [f(u[a], x) for x in u[b]]
                tpl = (f"for (long long «x» : {b}) {{\n    {init}\n"
                       f"    for (size_t «p» = 0; «p» < {a}.size(); «p»++) {{\n        long long «e» = {a}[«p»];\n"
                       f"        {step}\n    }}\n    «PRINT»\n}}")
                yield _q_cand(f"qx:{key}:{a}:{b}", f"dla każdego x z {b}: {desc} w {a}", 3.0 + (1.0 if swapped else 0),
                              set(tags) | {"query"}, run, tpl, "«s»", kind, none,
                              lambda u, a=a, b=b: len(u[a]) * len(u[b]) + 1, a=a)


def _q_cand(key, desc, cost, tags, run, tpl, val, kind, none, work, **fixed):
    def cpp(G, tpl=tpl, val=val, kind=kind, none=none, fixed=fixed):
        code0 = _t(tpl, **fixed)
        temps = {k: G.f(base) for k, base in _TEMPS.items() if "«" + k + "»" in code0 or "«" + k + "»" in val
                 or (none and "«" + k + "»" in none)}
        pr = G.pr(_t(val, **temps), kind, _t(none, **temps) if none else None)
        return _t(code0, **temps).replace("«PRINT»", pr.replace("\n", "\n    "))
    return Cand(key, desc, "query", cost, tags, run, cpp, cx="n·q", work=work)


# ---------------------------------------------------------------------------------------- przegląd x ∈ [od, do]
SCAN_PREDS = [
    ("prime", "pierwsze", ("prime",), lambda x, N, K: _isprime(x), "isprime(«x»)", ("isprime",), False, 0.8),
    ("square", "kwadraty", ("square",), lambda x, N, K: _issq(x), "issq(«x»)", ("issq",), False, 1.0),
    ("pal", "palindromy", ("palin",), lambda x, N, K: _ispal(x), "ispalnum(«x»)", ("ispalnum",), False, 1.0),
    ("divN", "dzielniki N", ("divisors", "div"), lambda x, N, K: x != 0 and N % x == 0, "«x» != 0 && «N» % «x» == 0", (),
     False, 0.8),
    ("coprime", "względnie pierwsze z N", ("coprime", "gcd"), lambda x, N, K: math.gcd(x, N) == 1, "gcd(«x», «N») == 1", (),
     False, 1.0),
    ("perfect", "doskonałe", ("divisors",), lambda x, N, K: x > 1 and x <= 20_000 and _sdiv(x) == 2 * x
     if x <= 20_000 else _raise(), "«x» > 1 && sdiv(«x») == 2 * «x»", ("sdiv",), False, 1.4),
    ("divK", "podzielne przez K", ("div",), lambda x, N, K: K != 0 and x % K == 0, "«K» != 0 && «x» % «K» == 0", (), True, 0.8),
    ("digsumK", "o sumie cyfr K", ("digits", "sum"), lambda x, N, K: _digsum(x) == K, "digsum(«x») == «K»", ("digsum",), True, 1.0),
    ("digsumdivK", "o sumie cyfr podzielnej przez K", ("digits", "div"), lambda x, N, K: K != 0 and _digsum(x) % K == 0,
     "«K» != 0 && digsum(«x») % «K» == 0", ("digsum",), True, 1.3),
]


def fam_scan(ctx: Ctx):
    """Przegląd kolejnych liczb: ile/suma/lista x ∈ [1..N] (albo [a..b]) spełniających warunek; następna taka liczba."""
    ints = ctx.roles["ints"]
    if not ints or ctx.roles["arrays"] or ctx.roles["rows"] or ctx.roles["strs"] or len(ints) > 3:
        return
    ranges = [(f"1..{v}", (lambda u, v=v: (1, u[v])), "1", v, v) for v in ints]
    if len(ints) >= 2:
        ranges.append((f"{ints[0]}..{ints[1]}", (lambda u: (u[ints[0]], u[ints[1]])), ints[0], ints[1], ints[1]))
    single = _unit_exps(ctx, lambda u: 1) is not None
    for rdesc, rng, lo, hi, Nv in ranges:
        others = [v for v in ints if v not in (lo, hi)]
        Ks = [(v, (lambda u, v=v: u[v]), 0.0) for v in others] + [(str(c), (lambda u, c=c: c), 1.0) for c in ctx.consts
                                                                    if c <= 10**6][:2]
        for pk, pdesc, ptags, pf, pc, ph, needK, pcost in SCAN_PREDS:
            mult = {"perfect": 60, "digsumK": 6, "digsumdivK": 6, "pal": 4, "coprime": 3}.get(pk, 2)
            W = lambda u, rng=rng, mult=mult: max(1, rng(u)[1] - rng(u)[0] + 1) * mult  # noqa: E731
            for K, getK, kc in (Ks if needK else [(None, (lambda u: None), 0.0)]):
                P = _t(pc, N=Nv, K=K or "0")
                d = _t(pdesc, K=K or "", N=Nv)

                def xs(u, rng=rng, pf=pf, getK=getK, Nv=Nv):
                    l_, h_ = rng(u)
                    if h_ - l_ > 2_000_000:
                        raise Bad
                    k = getK(u)
                    N = u[Nv]
                    return [x for x in range(l_, h_ + 1) if pf(x, N, k)]
                loop = f"for (long long «x» = {lo}; «x» <= {hi}; «x»++) if («P») "
                if single:
                    yield _mk(f"scan:cnt:{pk}:{rdesc}:{K}", f"ile liczb z [{rdesc.replace('..', ', ')}] to {d}", "scan",
                              2.5 + pcost + kc, set(ptags) | {"count"}, (lambda u, xs=xs: len(xs(u))),
                              "long long «r» = 0;\n" + loop + "«r»++;", "int", cx="V", work=W, helpers=ph, P=P)
                    yield _mk(f"scan:sum:{pk}:{rdesc}:{K}", f"suma liczb z [{rdesc.replace('..', ', ')}], które są {d}", "scan",
                              2.8 + pcost + kc, set(ptags) | {"sum"}, (lambda u, xs=xs: _ck(sum(xs(u)))),
                              "long long «r» = 0;\n" + loop + "«r» += «x»;", "int", cx="V", work=W, helpers=ph, P=P)
                yield _mk(f"scan:list:{pk}:{rdesc}:{K}", f"wszystkie liczby z [{rdesc.replace('..', ', ')}], które są {d}",
                          "scan", 2.8 + pcost + kc, set(ptags), xs,
                          "vector<long long> «r»;\n" + loop + "«r».push_back(«x»);", "seq", cx="V", work=W, helpers=ph, P=P)
    # następna / poprzednia liczba z własnością, k-ta liczba z własnością, pierwsze k takich liczb
    single = _unit_exps(ctx, lambda u: 1) is not None
    for v in ints:
        for pk, pdesc, ptags, pf, pc, ph, needK, pcost in SCAN_PREDS:
            if needK or pk in ("divN", "coprime"):
                continue
            P = _t(pc)

            def nxt(u, v=v, pf=pf):
                x = u[v]
                if x > 10**9:
                    raise Bad
                for _ in range(3000):
                    if pf(x, 0, None):
                        return x
                    x += 1
                raise Bad

            def kth(u, v=v, pf=pf):
                k, x = u[v], 0
                if k < 1 or k > 20_000:
                    raise Bad
                while True:
                    x += 1
                    if pf(x, 0, None):
                        k -= 1
                        if k == 0:
                            return x
                    if x > 300_000:
                        raise Bad

            def firstk(u, v=v, pf=pf):
                k, x, res = u[v], 0, []
                if k < 0 or k > 20_000:
                    raise Bad
                while len(res) < k:
                    x += 1
                    if pf(x, 0, None):
                        res.append(x)
                    if x > 300_000:
                        raise Bad
                return res
            if any(len(sm.exp) >= 2 for sm in ctx.evs):
                yield _mk(f"scan:firstk:{pk}:{v}", f"pierwsze {v} liczb, które są {pdesc}", "scan", 3.0 + pcost,
                          set(ptags), firstk,
                          f"vector<long long> «r»;\nfor (long long «x» = 1; (long long)«r».size() < {v}; «x»++) if («P») «r».push_back(«x»);",
                          "seq", cx="V log V", work=lambda u, v=v: min(10**9, max(10, u[v]) * 15), helpers=ph, P=P)
            if not single:
                continue
            yield _mk(f"scan:next:{pk}:{v}", f"najmniejsza liczba ≥ {v}, która jest {pdesc}", "scan", 3.0 + pcost,
                      set(ptags) | {"min"}, nxt, f"long long «r» = {v};\nwhile (!(«P»)) «r»++;".replace("«x»", "«r»"),
                      "int", cx="V", work=lambda u: 3000, helpers=ph, P=P.replace("«x»", "«r»"))
            yield _mk(f"scan:kth:{pk}:{v}", f"{v}-ta liczba, która jest {pdesc}", "scan", 3.2 + pcost,
                      set(ptags) | {"kth"}, kth,
                      f"long long «c» = 0, «r» = 0;\nwhile («c» < {v}) {{\n    «r»++;\n    if («P») «c»++;\n}}",
                      "int", cx="V log V", work=lambda u, v=v: min(10**9, max(10, u[v]) * 15), helpers=ph, P=P.replace("«x»", "«r»"))


# ---------------------------------------------------------------------------------------- napisy
def _pal_subs(s):
    n = len(s)
    return [(i, j) for i in range(n) for j in range(i + 1, n + 1) if s[i:j] == s[i:j][::-1]]


def _occ(s, t):
    if not t:
        raise Bad
    return sum(1 for i in range(len(s) - len(t) + 1) if s[i:i + len(t)] == t)


def _is_subseq(t, s):
    it = iter(s)
    return all(c in it for c in t)


def _lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _edit(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _lcsub(a, b):
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def _brackets(s):
    st = []
    pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
    for c in s:
        if c in "([{<":
            st.append(c)
        elif c in pairs:
            if not st or st[-1] != pairs[c]:
                return False
            st.pop()
    return not st


def _depth(s):
    d = best = 0
    for c in s:
        if c in "([{":
            d += 1
            best = max(best, d)
        elif c in ")]}":
            d -= 1
    return best


_VOW = "aeiouyAEIOUY"
_BR_C = ("bool «r» = true;\nstring «b»;\nfor (char «c» : «s») {\n    if (string(\"([{<\").find(«c») != string::npos) «b» += «c»;\n"
         "    else if (string(\")]}>\").find(«c») != string::npos) {\n        char «o» = «c» == ')' ? '(' : «c» == ']' ? '[' : «c» == '}' ? '{' : '<';\n"
         "        if («b».empty() || «b».back() != «o») { «r» = false; break; }\n        «b».pop_back();\n    }\n}\n"
         "if (!«b».empty()) «r» = false;")


def fam_strings(ctx: Ctx):
    """Napisy: długość, znaki, palindromy (także podsłowa), różne podsłowa, wystąpienia wzorca, podciąg, LCS…"""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    strs = ctx.roles["strs"]
    chars = sorted({c for u in ctx.units for s_ in strs for c in u[s_]})[:12] if strs else []
    Ks = _k_choices(ctx, extra_consts=True)
    for s in strs:
        W = lambda u, s=s: len(u[s]) + 1  # noqa: E731
        W2 = lambda u, s=s: len(u[s]) ** 2 + 1  # noqa: E731
        W3 = lambda u, s=s: len(u[s]) ** 3 // 4 + 1  # noqa: E731
        S = dict(s=s)
        one = [
            ("len", "długość napisu", ("len",), 1.0, lambda v: len(v), "long long «r» = «s».size();", "int", W),
            ("distinct", "liczba różnych znaków", ("distinct", "char", "count"), 1.3, lambda v: len(set(v)),
             "long long «r» = set<char>(«s».begin(), «s».end()).size();", "int", W),
            ("vowels", "liczba samogłosek", ("vowel", "count"), 1.3, lambda v: sum(1 for c in v if c in _VOW),
             "long long «r» = 0;\nfor (char «c» : «s») if (string(\"aeiouyAEIOUY\").find(«c») != string::npos) «r»++;", "int", W),
            ("cons", "liczba spółgłosek", ("count", "char"), 1.6, lambda v: sum(1 for c in v if c.isalpha() and c not in _VOW),
             "long long «r» = 0;\nfor (char «c» : «s») if (isalpha((unsigned char)«c») && string(\"aeiouyAEIOUY\").find(«c») == string::npos) «r»++;",
             "int", W),
            ("upper", "liczba wielkich liter", ("upper", "count"), 1.6, lambda v: sum(1 for c in v if "A" <= c <= "Z"),
             "long long «r» = 0;\nfor (char «c» : «s») if («c» >= 'A' && «c» <= 'Z') «r»++;", "int", W),
            ("lower", "liczba małych liter", ("lower", "count"), 1.6, lambda v: sum(1 for c in v if "a" <= c <= "z"),
             "long long «r» = 0;\nfor (char «c» : «s») if («c» >= 'a' && «c» <= 'z') «r»++;", "int", W),
            ("digits", "liczba cyfr", ("digits", "count"), 1.6, lambda v: sum(1 for c in v if "0" <= c <= "9"),
             "long long «r» = 0;\nfor (char «c» : «s») if («c» >= '0' && «c» <= '9') «r»++;", "int", W),
            ("digsum", "suma cyfr", ("digits", "sum"), 1.3, lambda v: sum(int(c) for c in v if "0" <= c <= "9"),
             "long long «r» = 0;\nfor (char «c» : «s») if («c» >= '0' && «c» <= '9') «r» += «c» - '0';", "int", W),
            ("ispal", "czy napis jest palindromem", ("palin", "exists"), 1.0, lambda v: v == v[::-1],
             "bool «r» = «s» == string(«s».rbegin(), «s».rend());", "bool", W),
            ("palsubs", "ile podsłów jest palindromami", ("palin", "substr", "count"), 1.4, lambda v: len(_pal_subs(v)),
             "long long «r» = 0;\nfor (size_t «i» = 0; «i» < «s».size(); «i»++)\n    for (size_t «j» = «i»; «j» < «s».size(); «j»++) {\n"
             "        string «t» = «s».substr(«i», «j» - «i» + 1);\n        if («t» == string(«t».rbegin(), «t».rend())) «r»++;\n    }",
             "int", W3),
            ("longpal", "długość najdłuższego podsłowa-palindromu", ("palin", "substr", "max", "len"), 1.4,
             lambda v: max((j - i for i, j in _pal_subs(v)), default=0),
             "long long «r» = 0;\nfor (size_t «i» = 0; «i» < «s».size(); «i»++)\n    for (size_t «j» = «i»; «j» < «s».size(); «j»++) {\n"
             "        string «t» = «s».substr(«i», «j» - «i» + 1);\n        if («t» == string(«t».rbegin(), «t».rend())) «r» = max(«r», (long long)«t».size());\n    }",
             "int", W3),
            ("distsubs", "liczba różnych (niepustych) podsłów", ("distinct", "substr", "count"), 1.4,
             lambda v: len({v[i:j] for i in range(len(v)) for j in range(i + 1, len(v) + 1)}),
             "set<string> «b»;\nfor (size_t «i» = 0; «i» < «s».size(); «i»++)\n    for (size_t «j» = «i» + 1; «j» <= «s».size(); «j»++) «b».insert(«s».substr(«i», «j» - «i»));\n"
             "long long «r» = «b».size();", "int", W3),
            ("run", "najdłuższy blok jednakowych znaków", ("run", "max", "len"), 1.5,
             lambda v: _longest_run(list(v), lambda x, y: x == y),
             "long long «r» = «s».empty() ? 0 : 1, «c» = 1;\nfor (size_t «i» = 1; «i» < «s».size(); «i»++) {\n"
             "    «c» = «s»[«i»] == «s»[«i» - 1] ? «c» + 1 : 1;\n    «r» = max(«r», «c»);\n}", "int", W),
            ("runs", "liczba bloków jednakowych znaków", ("run", "count"), 1.6,
             lambda v: (1 + sum(1 for i in range(1, len(v)) if v[i] != v[i - 1])) if v else 0,
             "long long «r» = «s».empty() ? 0 : 1;\nfor (size_t «i» = 1; «i» < «s».size(); «i»++) if («s»[«i»] != «s»[«i» - 1]) «r»++;",
             "int", W),
            ("rev", "napis od tyłu", ("reverse",), 1.0, lambda v: v[::-1], "string «r»(«s».rbegin(), «s».rend());", "str", W),
            ("toupper", "napis wielkimi literami", ("upper",), 1.2, lambda v: "".join(c.upper() if "a" <= c <= "z" else c for c in v),
             "string «r» = «s»;\nfor (char &«c» : «r») if («c» >= 'a' && «c» <= 'z') «c» = «c» - 'a' + 'A';", "str", W),
            ("tolower", "napis małymi literami", ("lower",), 1.2, lambda v: "".join(c.lower() if "A" <= c <= "Z" else c for c in v),
             "string «r» = «s»;\nfor (char &«c» : «r») if («c» >= 'A' && «c» <= 'Z') «c» = «c» - 'A' + 'a';", "str", W),
            ("swapcase", "zamiana wielkości liter", ("upper", "lower"), 1.5,
             lambda v: "".join(c.upper() if "a" <= c <= "z" else c.lower() if "A" <= c <= "Z" else c for c in v),
             "string «r» = «s»;\nfor (char &«c» : «r»)\n    if («c» >= 'a' && «c» <= 'z') «c» = «c» - 'a' + 'A';\n"
             "    else if («c» >= 'A' && «c» <= 'Z') «c» = «c» - 'A' + 'a';", "str", W),
            ("sortch", "znaki posortowane", ("sort", "char"), 1.4, lambda v: "".join(sorted(v)),
             "string «r» = «s»;\nsort(«r».begin(), «r».end());", "str", W),
            ("modech", "najczęstszy znak", ("max", "char", "count"), 1.5,
             lambda v: min(c for c in set(v) if v.count(c) == max(v.count(x) for x in set(v))) if v else _raise(),
             "map<char, long long> «b»;\nfor (char «c» : «s») «b»[«c»]++;\nchar «r» = 0;\nlong long «m» = 0;\n"
             "for (auto [«c», «o»] : «b») if («o» > «m») { «m» = «o»; «r» = «c»; }", "str", W),
            ("modecnt", "ile razy występuje najczęstszy znak", ("max", "char", "count"), 1.6,
             lambda v: max(Counter(v).values()) if v else _raise(),
             "map<char, long long> «b»;\nlong long «r» = 0;\nfor (char «c» : «s») «r» = max(«r», ++«b»[«c»]);", "int", W),
            ("brackets", "czy nawiasy są poprawnie sparowane", ("exists",), 1.4, _brackets, _BR_C, "bool", W),
            ("depth", "największa głębokość nawiasów", ("max", "depth"), 1.6, _depth,
             "long long «r» = 0, «d» = 0;\nfor (char «c» : «s») {\n    if («c» == '(' || «c» == '[' || «c» == '{') «r» = max(«r», ++«d»);\n"
             "    else if («c» == ')' || «c» == ']' || «c» == '}') «d»--;\n}", "int", W),
        ]
        for key, desc, tags, cost, f, tpl, kind, wk in one:
            yield _mk(f"str:{key}:{s}", f"{desc} ({s})", "string", 2.0 + cost, set(tags), (lambda u, f=f, s=s: f(u[s])),
                      tpl, kind, cx=("n^3" if wk is W3 else "n"), work=wk, **S)
        for ch in chars:
            yield _mk(f"str:cnt:{s}:{ch}", f"ile razy w {s} występuje „{ch}”", "string", 3.0, {"count", "char", "occur"},
                      (lambda u, s=s, ch=ch: u[s].count(ch)), "long long «r» = count(«s».begin(), «s».end(), «C»);", "int",
                      cx="n", work=W, s=s, C=_cpp_char(ch))
        for K, getK, kc in Ks:
            yield _mk(f"str:mod:{s}:{K}", f"liczba {s} (zapisana cyframi) modulo {K}", "string", 3.2 + kc, {"mod", "div", "digits"},
                      (lambda u, s=s, getK=getK: _strmod(u[s], getK(u))),
                      "long long «r» = 0;\nfor (char «c» : «s») «r» = («r» * 10 + («c» - '0')) % «K»;", "int", cx="n", work=W, s=s, K=K)
            yield _mk(f"str:divby:{s}:{K}", f"czy liczba {s} jest podzielna przez {K}", "string", 3.3 + kc, {"div", "digits", "exists"},
                      (lambda u, s=s, getK=getK: _strmod(u[s], getK(u)) == 0),
                      "long long «m» = 0;\nfor (char «c» : «s») «m» = («m» * 10 + («c» - '0')) % «K»;\nbool «r» = «m» == 0;", "bool",
                      cx="n", work=W, s=s, K=K)
    # dwa napisy
    for i1, s in enumerate(strs):
        for t in strs[i1 + 1:]:
            W2 = lambda u, s=s, t=t: len(u[s]) * len(u[t]) + 1  # noqa: E731
            two = [
                ("occ", f"ile razy {t} występuje w {s}", ("count", "substr", "occur"), 1.0, lambda a, b: _occ(a, b),
                 "long long «r» = 0;\nfor (size_t «i» = 0; «i» + «t».size() <= «s».size(); «i»++) if («s».compare(«i», «t».size(), «t») == 0) «r»++;",
                 "int"),
                ("occr", f"ile razy {s} występuje w {t}", ("count", "substr"), 1.4, lambda a, b: _occ(b, a),
                 "long long «r» = 0;\nfor (size_t «i» = 0; «i» + «s».size() <= «t».size(); «i»++) if («t».compare(«i», «s».size(), «s») == 0) «r»++;",
                 "int"),
                ("sub", f"czy {t} jest podsłowem {s}", ("substr", "exists"), 1.2, lambda a, b: b in a,
                 "bool «r» = «s».find(«t») != string::npos;", "bool"),
                ("subr", f"czy {s} jest podsłowem {t}", ("substr", "exists"), 1.4, lambda a, b: a in b,
                 "bool «r» = «t».find(«s») != string::npos;", "bool"),
                ("subseq", f"czy {t} jest podciągiem {s}", ("subseq", "exists"), 1.2, lambda a, b: _is_subseq(b, a),
                 "size_t «j» = 0;\nfor (char «c» : «s») if («j» < «t».size() && «t»[«j»] == «c») «j»++;\nbool «r» = «j» == «t».size();", "bool"),
                ("subseqr", f"czy {s} jest podciągiem {t}", ("subseq", "exists"), 1.4, lambda a, b: _is_subseq(a, b),
                 "size_t «j» = 0;\nfor (char «c» : «t») if («j» < «s».size() && «s»[«j»] == «c») «j»++;\nbool «r» = «j» == «s».size();", "bool"),
                ("lcs", "najdłuższy wspólny podciąg", ("lcs", "subseq", "max", "len"), 1.0, _lcs,
                 "vector<vector<long long>> «d»(«s».size() + 1, vector<long long>(«t».size() + 1, 0));\n"
                 "for (size_t «i» = 1; «i» <= «s».size(); «i»++)\n    for (size_t «j» = 1; «j» <= «t».size(); «j»++)\n"
                 "        «d»[«i»][«j»] = «s»[«i» - 1] == «t»[«j» - 1] ? «d»[«i» - 1][«j» - 1] + 1 : max(«d»[«i» - 1][«j»], «d»[«i»][«j» - 1]);\n"
                 "long long «r» = «d»[«s».size()][«t».size()];", "int"),
                ("edit", "odległość edycyjna", ("edit", "min"), 1.1, _edit,
                 "vector<vector<long long>> «d»(«s».size() + 1, vector<long long>(«t».size() + 1, 0));\n"
                 "for (size_t «i» = 0; «i» <= «s».size(); «i»++) «d»[«i»][0] = «i»;\nfor (size_t «j» = 0; «j» <= «t».size(); «j»++) «d»[0][«j»] = «j»;\n"
                 "for (size_t «i» = 1; «i» <= «s».size(); «i»++)\n    for (size_t «j» = 1; «j» <= «t».size(); «j»++)\n"
                 "        «d»[«i»][«j»] = min({«d»[«i» - 1][«j»] + 1, «d»[«i»][«j» - 1] + 1, «d»[«i» - 1][«j» - 1] + («s»[«i» - 1] != «t»[«j» - 1])});\n"
                 "long long «r» = «d»[«s».size()][«t».size()];", "int"),
                ("anagram", "czy napisy są anagramami", ("anagram", "exists"), 1.0, lambda a, b: sorted(a) == sorted(b),
                 "string «b» = «s», «c» = «t»;\nsort(«b».begin(), «b».end());\nsort(«c».begin(), «c».end());\nbool «r» = «b» == «c»;", "bool"),
                ("anadel", "ile liter usunąć, by były anagramami", ("anagram", "min"), 1.5,
                 lambda a, b: sum(abs(a.count(c) - b.count(c)) for c in set(a) | set(b)),
                 "map<char, long long> «b»;\nfor (char «c» : «s») «b»[«c»]++;\nfor (char «c» : «t») «b»[«c»]--;\n"
                 "long long «r» = 0;\nfor (auto [«c», «o»] : «b») «r» += llabs(«o»);", "int"),
                ("common", "liczba wspólnych różnych znaków", ("char", "count", "distinct"), 1.6,
                 lambda a, b: len(set(a) & set(b)),
                 "set<char> «b»(«s».begin(), «s».end()), «c»(«t».begin(), «t».end());\nlong long «r» = 0;\n"
                 "for (char «o» : «b») if («c».count(«o»)) «r»++;", "int"),
                ("hamming", "liczba pozycji, na których napisy się różnią", ("diff", "count"), 1.4,
                 lambda a, b: sum(x != y for x, y in zip(a, b)) if len(a) == len(b) else _raise(),
                 "long long «r» = 0;\nfor (size_t «i» = 0; «i» < «s».size(); «i»++) if («s»[«i»] != «t»[«i»]) «r»++;", "int"),
                ("lcsub", "najdłuższe wspólne podsłowo", ("substr", "max", "len"), 1.4, _lcsub,
                 "long long «r» = 0;\nfor (size_t «i» = 0; «i» < «s».size(); «i»++)\n    for (size_t «j» = 0; «j» < «t».size(); «j»++) {\n"
                 "        size_t «o» = 0;\n        while («i» + «o» < «s».size() && «j» + «o» < «t».size() && «s»[«i» + «o»] == «t»[«j» + «o»]) «o»++;\n"
                 "        «r» = max(«r», (long long)«o»);\n    }", "int"),
                ("eq", "czy napisy są równe", ("equal", "exists"), 1.3, lambda a, b: a == b, "bool «r» = «s» == «t»;", "bool"),
                ("concat", "sklejenie napisów", ("sum",), 1.6, lambda a, b: a + b, "string «r» = «s» + «t»;", "str"),
                ("lexmin", "mniejszy leksykograficznie napis", ("min",), 1.6, lambda a, b: min(a, b), "string «r» = min(«s», «t»);", "str"),
            ]
            for key, desc, tags, cost, f, tpl, kind in two:
                yield _mk(f"str2:{key}:{s}:{t}", desc, "string", 2.0 + cost, set(tags),
                          (lambda u, f=f, s=s, t=t: f(u[s], u[t])), tpl, kind, cx="n·m", work=W2, s=s, t=t)


def _cpp_char(ch: str) -> str:
    return "'" + {"'": "\\'", "\\": "\\\\"}.get(ch, ch) + "'"


def _strmod(v: str, k):
    if not k or k < 1 or not re.fullmatch(r"\d+", v or ""):
        raise Bad
    r = 0
    for c in v:
        r = (r * 10 + int(c)) % k
    return r


# ---------------------------------------------------------------------------------------- grafy
def _graph(u, R, nv, directed):
    n = u[nv]
    if not isinstance(n, int) or n < 1 or n > 300_000:
        raise Bad
    A, B = u[R.names[0]], u[R.names[1]]
    Wt = u[R.names[2]] if len(R.names) >= 3 else None
    adj = [[] for _ in range(n + 1)]
    for i in range(len(A)):
        x, y = A[i], B[i]
        if not (1 <= x <= n and 1 <= y <= n):
            raise Bad
        w = Wt[i] if Wt is not None else 1
        adj[x].append((y, w))
        if not directed:
            adj[y].append((x, w))
    return n, adj


def _bfs(n, adj, s):
    d = [-1] * (n + 1)
    if not (1 <= s <= n):
        raise Bad
    d[s] = 0
    q = deque([s])
    while q:
        v = q.popleft()
        for w_, _c in adj[v]:
            if d[w_] == -1:
                d[w_] = d[v] + 1
                q.append(w_)
    return d


def _dijkstra(n, adj, s):
    import heapq
    if not (1 <= s <= n):
        raise Bad
    d = [-1] * (n + 1)
    d[s] = 0
    pq = [(0, s)]
    while pq:
        e, v = heapq.heappop(pq)
        if e != d[v]:
            continue
        for w_, c in adj[v]:
            if c < 0:
                raise Bad
            if d[w_] == -1 or d[v] + c < d[w_]:
                d[w_] = d[v] + c
                heapq.heappush(pq, (d[w_], w_))
    return d


def _comps(n, adj):
    seen = [False] * (n + 1)
    sizes = []
    for s in range(1, n + 1):
        if seen[s]:
            continue
        seen[s] = True
        st, c = [s], 0
        while st:
            v = st.pop()
            c += 1
            for w_, _ in adj[v]:
                if not seen[w_]:
                    seen[w_] = True
                    st.append(w_)
        sizes.append(c)
    return sizes


def _cycle_dir(n, adj):
    col = [0] * (n + 1)
    for s in range(1, n + 1):
        if col[s]:
            continue
        st = [(s, 0)]
        col[s] = 1
        while st:
            v, i = st[-1]
            if i < len(adj[v]):
                st[-1] = (v, i + 1)
                w_ = adj[v][i][0]
                if col[w_] == 1:
                    return True
                if col[w_] == 0:
                    col[w_] = 1
                    st.append((w_, 0))
            else:
                col[v] = 2
                st.pop()
    return False


def _cycle_undir(n, adj, m):
    # cykl ⇔ w jakiejś składowej krawędzi ≥ wierzchołków (pętle i krawędzie wielokrotne też są cyklami)
    sizes = _comps(n, adj)
    return m > n - len(sizes)


def _bipartite(n, adj):
    col = [-1] * (n + 1)
    for s in range(1, n + 1):
        if col[s] != -1:
            continue
        col[s] = 0
        q = deque([s])
        while q:
            v = q.popleft()
            for w_, _ in adj[v]:
                if col[w_] == -1:
                    col[w_] = col[v] ^ 1
                    q.append(w_)
                elif col[w_] == col[v]:
                    return False
    return True


def _tree_info(n, adj):
    """BFS z korzenia 1: rodzic, głębokość, kolejność; dzieci rosnąco."""
    par = [0] * (n + 1)
    dep = [-1] * (n + 1)
    dep[1] = 0
    order = [1]
    q = deque([1])
    while q:
        v = q.popleft()
        for w_, _ in sorted(adj[v]):
            if dep[w_] == -1:
                dep[w_] = dep[v] + 1
                par[w_] = v
                order.append(w_)
                q.append(w_)
    if len(order) != n:
        raise Bad
    return par, dep, order


def _tree_orders(n, adj):
    par, dep, order = _tree_info(n, adj)
    ch = [[] for _ in range(n + 1)]
    for v in range(2, n + 1):
        ch[par[v]].append(v)
    pre, post = [], []
    st = [(1, 0)]
    while st:
        v, i = st.pop()
        if i == 0:
            pre.append(v)
        kids = sorted(ch[v])
        if i < len(kids):
            st.append((v, i + 1))
            st.append((kids[i], 0))
        else:
            post.append(v)
    return pre, post


def _subtree(n, adj):
    par, dep, order = _tree_info(n, adj)
    sz = [1] * (n + 1)
    for v in reversed(order[1:]):
        sz[par[v]] += sz[v]
    return sz[1:]


def _mst(n, adj, edges):
    p = list(range(n + 1))

    def f(x):
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x
    tot, used = 0, 0
    for w_, x, y in sorted(edges):
        a, b = f(x), f(y)
        if a != b:
            p[a] = b
            tot += w_
            used += 1
    return tot if used == n - 1 else NONE


def _topo(n, adj):
    import heapq
    indeg = [0] * (n + 1)
    for v in range(1, n + 1):
        for w_, _ in adj[v]:
            indeg[w_] += 1
    pq = [v for v in range(1, n + 1) if indeg[v] == 0]
    heapq.heapify(pq)
    out = []
    while pq:
        v = heapq.heappop(pq)
        out.append(v)
        for w_, _ in adj[v]:
            indeg[w_] -= 1
            if indeg[w_] == 0:
                heapq.heappush(pq, w_)
    return out if len(out) == n else NONE


_G_BUILD = ("vector<vector<pair<int, long long>>> «g»(«N» + 1);\nfor (size_t «i» = 0; «i» < «A».size(); «i»++) {\n"
            "    «g»[«A»[«i»]].push_back({(int)«B»[«i»], «WT»});\n«UND»}")
_G_UND = "    «g»[«B»[«i»]].push_back({(int)«A»[«i»], «WT»});\n"
_G_BFS = ("vector<long long> «d»(«N» + 1, -1);\ndeque<int> «q»;\n«d»[«S»] = 0;\n«q».push_back(«S»);\nwhile (!«q».empty()) {\n"
          "    int «v» = «q».front();\n    «q».pop_front();\n    for (auto [«u», «w»] : «g»[«v»])\n"
          "        if («d»[«u»] == -1) { «d»[«u»] = «d»[«v»] + 1; «q».push_back(«u»); }\n}")
_G_DIJ = ("vector<long long> «d»(«N» + 1, -1);\npriority_queue<pair<long long, int>, vector<pair<long long, int>>, greater<>> «q»;\n"
          "«d»[«S»] = 0;\n«q».push({0, «S»});\nwhile (!«q».empty()) {\n    auto [«e», «v»] = «q».top();\n    «q».pop();\n"
          "    if («e» != «d»[«v»]) continue;\n    for (auto [«u», «w»] : «g»[«v»])\n"
          "        if («d»[«u»] == -1 || «d»[«v»] + «w» < «d»[«u»]) { «d»[«u»] = «d»[«v»] + «w»; «q».push({«d»[«u»], «u»}); }\n}")
_G_COMPS = ("vector<int> «o»(«N» + 1, 0);\nvector<long long> «b»;   // rozmiary spójnych składowych\n"
            "for (int «s» = 1; «s» <= «N»; «s»++) {\n    if («o»[«s»]) continue;\n    «o»[«s»] = 1;\n    vector<int> «t» = {«s»};\n"
            "    long long «c» = 0;\n    while (!«t».empty()) {\n        int «v» = «t».back();\n        «t».pop_back();\n        «c»++;\n"
            "        for (auto [«u», «w»] : «g»[«v»]) if (!«o»[«u»]) { «o»[«u»] = 1; «t».push_back(«u»); }\n    }\n    «b».push_back(«c»);\n}")
_G_TREE = ("vector<int> «p»(«N» + 1, 0), «l» = {1};\nvector<long long> «d»(«N» + 1, -1);\n«d»[1] = 0;\n"
           "for (size_t «i» = 0; «i» < «l».size(); «i»++) {   // BFS od korzenia 1\n    int «v» = «l»[«i»];\n"
           "    sort(«g»[«v»].begin(), «g»[«v»].end());\n    for (auto [«u», «w»] : «g»[«v»])\n"
           "        if («d»[«u»] == -1) { «d»[«u»] = «d»[«v»] + 1; «p»[«u»] = «v»; «l».push_back(«u»); }\n}")


def _graph_vertex_var(ctx: Ctx, R: Item) -> str | None:
    for v in ctx.roles["ints"]:
        if R.count is not None and R.count.kind == "var" and R.count.var == v and R.count.add == 0:
            continue
        ok = bool(ctx.units)
        for u in ctx.units:
            n = u.get(v)
            if not isinstance(n, int) or n < 1:
                ok = False
                break
            for f in R.names[:2]:
                if any(not (1 <= x <= n) for x in u[f]):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return v
    return None


def fam_graph(ctx: Ctx):
    for c in _fam_graph(ctx):
        if ":True:" in c.key or c.key.startswith(("g:cycle:d", "g:topo")):
            c.tags |= {"directed"}
        elif c.key.startswith(("g:", "t:")):
            c.tags |= {"undirected"}
        yield c


def _fam_graph(ctx: Ctx):
    """Grafy z listy krawędzi: składowe, BFS/Dijkstra (1 → n, s → t, do wszystkich), cykle, dwudzielność, stopnie,
    drzewa (średnica, wysokość, głębokości, poddrzewa, rodzice, preorder/postorder), MST, sortowanie topologiczne."""
    NI = _none_int(ctx)
    single = _unit_exps(ctx, lambda u: 1) is not None
    multi = any(len(s.exp) >= 2 for s in ctx.evs)
    for R in ctx.roles["rows"]:
        if len(R.names) not in (2, 3):
            continue
        nv = _graph_vertex_var(ctx, R)
        if nv is None:
            continue
        weighted = len(R.names) == 3
        A, B = R.names[0], R.names[1]
        WT = f"{R.names[2]}[«i»]" if weighted else "1"
        is_tree = all(len(u[A]) == u[nv] - 1 for u in ctx.units)
        others = [v for v in ctx.roles["ints"] if v != nv and not (R.count is not None and R.count.var == v)]
        Wk = lambda u: (len(u[A]) + (u[nv] if isinstance(u.get(nv), int) else 0)) * 3 + 1  # noqa: E731
        for directed in (False, True):
            dtag = "skierowany" if directed else "nieskierowany"
            fx = dict(N=nv, A=A, B=B, WT=WT)
            build = _G_BUILD.replace("«UND»", "" if directed else _G_UND)
            gc = 0.6 if directed else 0.0

            def G(u, directed=directed):
                return _cache(u, f"g:{R.name}:{directed}", lambda: _graph(u, R, nv, directed))
            pairs = [("1", nv, (lambda u: 1), (lambda u: u[nv]), 0.0)]
            pairs += [(p, q, (lambda u, p=p: u[p]), (lambda u, q=q: u[q]), 0.0) for p in others for q in others if p != q][:4]
            for wtd in ((True,) if weighted else (False,)):
                algo = _G_DIJ if wtd else _G_BFS
                dist = _dijkstra if wtd else _bfs
                atag = {"dist", "min"}
                if single:
                    for sn, tn, gs, gt, pc in pairs:
                        def run(u, G=G, gs=gs, gt=gt, dist=dist):
                            n, adj = G(u)
                            t = gt(u)
                            if not (1 <= t <= n):
                                raise Bad
                            d = dist(n, adj, gs(u))[t]
                            return d if d != -1 else NONE
                        yield _mk(f"g:dist:{sn}:{tn}:{directed}:{R.name}",
                                  f"najkrótsza droga z {sn} do {tn} (graf {dtag}{', wagi' if wtd else ''})", "graph",
                                  2.5 + gc + pc, atag, run, build + "\n" + algo.replace("«S»", sn) + f"\nlong long «r» = «d»[{tn}];",
                                  "int", "«r» == -1", cx="m log n" if wtd else "n + m", work=Wk, **fx)
                    if True:
                        def run_ecc(u, G=G, dist=dist):
                            n, adj = G(u)
                            return max(dist(n, adj, 1))
                        yield _mk(f"g:ecc:{directed}:{R.name}", f"najdalszy wierzchołek od 1 (graf {dtag})", "graph",
                                  3.5 + gc, {"dist", "max"}, run_ecc,
                                  build + "\n" + algo.replace("«S»", "1") + "\nlong long «r» = *max_element(«d».begin(), «d».end());",
                                  "int", cx="m log n" if wtd else "n + m", work=Wk, **fx)
                if multi and NI is not None:
                    def run_all(u, G=G, dist=dist):
                        n, adj = G(u)
                        return [x if x != -1 else NI for x in dist(n, adj, 1)[1:]]
                    yield _mk(f"g:distall:{directed}:{R.name}", f"odległości od 1 do każdego wierzchołka (graf {dtag})", "graph",
                              2.5 + gc, {"dist"}, run_all,
                              build + "\n" + algo.replace("«S»", "1") +
                              f"\nvector<long long> «r»;\nfor (int «v» = 1; «v» <= «N»; «v»++) «r».push_back(«d»[«v»] == -1 ? {NI} : «d»[«v»]);",
                              "seq", cx="m log n" if wtd else "n + m", work=Wk, **fx)
            if single:
                def run_reach(u, G=G):
                    n, adj = G(u)
                    return sum(1 for x in _bfs(n, adj, 1)[1:] if x != -1)
                yield _mk(f"g:reach:{directed}:{R.name}", f"ile wierzchołków osiągalnych z 1 (graf {dtag})", "graph", 3.0 + gc,
                          {"reach", "count"}, run_reach,
                          build + "\n" + _G_BFS.replace("«S»", "1") + "\nlong long «r» = 0;\nfor (int «v» = 1; «v» <= «N»; «v»++) if («d»[«v»] != -1) «r»++;",
                          "int", cx="n + m", work=Wk, **fx)
                if directed:
                    def run_cyc(u, G=G):
                        n, adj = G(u)
                        return _cycle_dir(n, adj)
                    yield _mk(f"g:cycle:d:{R.name}", "czy graf skierowany ma cykl", "graph", 3.0, {"cycle", "exists"}, run_cyc,
                              build + "\n" + _CYC_DIR, "bool", cx="n + m", work=Wk, **fx)
                else:
                    comps = [
                        ("ncomp", "liczba spójnych składowych", ("comp", "count", "connected"), 2.4, lambda s: len(s),
                         "long long «r» = «b».size();", "int"),
                        ("big", "rozmiar największej spójnej składowej", ("comp", "max", "connected"), 2.8, lambda s: max(s),
                         "long long «r» = *max_element(«b».begin(), «b».end());", "int"),
                        ("conn", "czy graf jest spójny", ("connected", "exists"), 2.6, lambda s: len(s) == 1,
                         "bool «r» = «b».size() == 1;", "bool"),
                        ("need", "ile krawędzi dodać, by graf był spójny", ("connected", "comp", "min"), 3.0, lambda s: len(s) - 1,
                         "long long «r» = (long long)«b».size() - 1;", "int"),
                        ("iso", "liczba wierzchołków bez sąsiadów", ("count", "degree"), 3.4, lambda s: sum(1 for x in s if x == 1),
                         "long long «r» = count(«b».begin(), «b».end(), 1LL);", "int"),
                    ]
                    for key, desc, tags, cost, f, tail, kind in comps:
                        def run_c(u, G=G, f=f):
                            n, adj = G(u)
                            return f(_comps(n, adj))
                        yield _mk(f"g:{key}:{R.name}", desc, "graph", cost, set(tags), run_c, build + "\n" + _G_COMPS + "\n" + tail,
                                  kind, cx="n + m", work=Wk, **fx)

                    def run_cu(u, G=G):
                        n, adj = G(u)
                        return _cycle_undir(n, adj, len(u[A]))
                    yield _mk(f"g:cycle:u:{R.name}", "czy graf ma cykl", "graph", 3.0, {"cycle", "exists"}, run_cu,
                              build + "\n" + _G_COMPS + f"\nbool «r» = (long long){A}.size() > «N» - (long long)«b».size();",
                              "bool", cx="n + m", work=Wk, **fx)

                    def run_bip(u, G=G):
                        n, adj = G(u)
                        return _bipartite(n, adj)
                    yield _mk(f"g:bip:{R.name}", "czy graf jest dwudzielny", "graph", 3.2, {"bipartite", "exists"}, run_bip,
                              build + "\n" + _BIP, "bool", cx="n + m", work=Wk, **fx)
                    if weighted:
                        def run_mst(u, G=G):
                            n, adj = G(u)
                            return _mst(n, adj, list(zip(u[R.names[2]], u[A], u[B])))
                        yield _mk(f"g:mst:{R.name}", "waga minimalnego drzewa rozpinającego", "graph", 3.0, {"mst", "min", "sum"},
                                  run_mst, _MST, "int", "«c» != «N» - 1", cx="m log m", work=Wk, **fx)
            if multi and not directed:
                def run_deg(u, G=G):
                    n, adj = G(u)
                    return [len(adj[v]) for v in range(1, n + 1)]
                yield _mk(f"g:deg:{R.name}", "stopień każdego wierzchołka", "graph", 3.0, {"degree"}, run_deg,
                          build + "\nvector<long long> «r»;\nfor (int «v» = 1; «v» <= «N»; «v»++) «r».push_back(«g»[«v»].size());",
                          "seq", cx="n + m", work=Wk, **fx)
            if multi and directed and not weighted:
                def run_topo(u, G=G):
                    n, adj = G(u)
                    return _topo(n, adj)
                yield _mk(f"g:topo:{R.name}", "najmniejsze leksykograficznie sortowanie topologiczne", "graph", 3.0,
                          {"topo", "sort"}, run_topo, build + "\n" + _TOPO, "raw", cx="m log n", work=Wk, **fx)
            # drzewa (korzeń 1)
            if is_tree and not directed:
                tree_single = [
                    ("diam", "średnica drzewa (liczba krawędzi)" if not weighted else "średnica drzewa (suma wag)",
                     ("diameter", "tree", "max", "dist"), 2.6, lambda n, adj, wtd=weighted: _diam(n, adj, wtd),
                     (_G_DIJ if weighted else _G_BFS).replace("«S»", "1") + "\nint «x» = max_element(«d».begin(), «d».end()) - «d».begin();\n"
                     + (_G_DIJ if weighted else _G_BFS).replace("«S»", "«x»").replace("«d»", "«e2»").replace("«q»", "«q2»")
                     .replace("«v»", "«v2»").replace("«e»", "«e3»") + "\nlong long «r» = *max_element(«e2».begin(), «e2».end());", "int"),
                    ("height", "wysokość drzewa (najgłębszy wierzchołek, korzeń 1)", ("depth", "tree", "max"), 2.8,
                     lambda n, adj: max(_tree_info(n, adj)[1]), _G_TREE + "\nlong long «r» = *max_element(«d».begin(), «d».end());", "int"),
                    ("leaves", "liczba liści (wierzchołków stopnia 1)", ("leaf", "tree", "count"), 3.0,
                     lambda n, adj: sum(1 for v in range(1, n + 1) if len(adj[v]) == 1),
                     "long long «r» = 0;\nfor (int «v» = 1; «v» <= «N»; «v»++) if («g»[«v»].size() == 1) «r»++;", "int"),
                ]
                if single:
                    for key, desc, tags, cost, f, tail, kind in tree_single:
                        def run_t(u, G=G, f=f):
                            n, adj = G(u)
                            return f(n, adj)
                        yield _mk(f"t:{key}:{R.name}", desc, "tree", cost, set(tags), run_t, build + "\n" + tail, kind,
                                  cx="n", work=Wk, **fx)
                if multi:
                    tree_seq = [
                        ("depths", "głębokość każdego wierzchołka (korzeń 1)", ("depth", "tree", "dist"), 2.6,
                         lambda n, adj: _tree_info(n, adj)[1][1:],
                         _G_TREE + "\nvector<long long> «r»(«d».begin() + 1, «d».end());"),
                        ("parents", "rodzic każdego wierzchołka 2..n (korzeń 1)", ("tree",), 2.8,
                         lambda n, adj: _tree_info(n, adj)[0][2:],
                         _G_TREE + "\nvector<long long> «r»(«p».begin() + 2, «p».end());"),
                        ("sub", "rozmiar poddrzewa każdego wierzchołka (korzeń 1)", ("tree", "count"), 2.8, _subtree,
                         _G_TREE + "\nvector<long long> «r»(«N» + 1, 1);\nfor (size_t «i» = «l».size(); «i»-- > 1; ) «r»[«p»[«l»[«i»]]] += «r»[«l»[«i»]];\n"
                         "«r».erase(«r».begin());"),
                        ("pre", "kolejność preorder (korzeń 1, dzieci rosnąco)", ("tree", "sort"), 3.0,
                         lambda n, adj: _tree_orders(n, adj)[0], _G_TREE + "\n" + _PREPOST.replace("«PRE»", "    «r».push_back(«v»);\n").replace("«POST»", "")),
                        ("post", "kolejność postorder (korzeń 1, dzieci rosnąco)", ("tree", "sort"), 3.0,
                         lambda n, adj: _tree_orders(n, adj)[1], _G_TREE + "\n" + _PREPOST.replace("«PRE»", "").replace("«POST»", "    «r».push_back(«v»);\n")),
                    ]
                    for key, desc, tags, cost, f, tail in tree_seq:
                        def run_ts(u, G=G, f=f):
                            n, adj = G(u)
                            return f(n, adj)
                        yield _mk(f"t:{key}:{R.name}", desc, "tree", cost, set(tags), run_ts, build + "\n" + tail, "seq",
                                  cx="n log n", work=Wk, **fx)


def _diam(n, adj, wtd):
    dist = _dijkstra if wtd else _bfs
    d = dist(n, adj, 1)
    x = d.index(max(d))
    return max(dist(n, adj, x))


_CYC_DIR = ("vector<int> «o»(«N» + 1, 0);   // 0 = nieodwiedzony, 1 = na stosie, 2 = przetworzony\nbool «r» = false;\n"
            "function<void(int)> «h» = [&](int «v») {\n    «o»[«v»] = 1;\n    for (auto [«u», «w»] : «g»[«v»]) {\n"
            "        if («o»[«u»] == 1) «r» = true;\n        else if («o»[«u»] == 0) «h»(«u»);\n    }\n    «o»[«v»] = 2;\n};\n"
            "for (int «v» = 1; «v» <= «N»; «v»++) if («o»[«v»] == 0) «h»(«v»);")
_BIP = ("vector<int> «o»(«N» + 1, -1);\nbool «r» = true;\nfor (int «s» = 1; «s» <= «N»; «s»++) {\n    if («o»[«s»] != -1) continue;\n"
        "    «o»[«s»] = 0;\n    deque<int> «q» = {«s»};\n    while (!«q».empty()) {\n        int «v» = «q».front();\n        «q».pop_front();\n"
        "        for (auto [«u», «w»] : «g»[«v»]) {\n            if («o»[«u»] == -1) { «o»[«u»] = «o»[«v»] ^ 1; «q».push_back(«u»); }\n"
        "            else if («o»[«u»] == «o»[«v»]) «r» = false;\n        }\n    }\n}")
_MST = ("vector<array<long long, 3>> «e»;\nfor (size_t «i» = 0; «i» < «A».size(); «i»++) «e».push_back({«WT», «A»[«i»], «B»[«i»]});\n"
        "sort(«e».begin(), «e».end());\nvector<int> «p»(«N» + 1);\niota(«p».begin(), «p».end(), 0);\n"
        "function<int(int)> «h» = [&](int «x») { return «p»[«x»] == «x» ? «x» : «p»[«x»] = «h»(«p»[«x»]); };\n"
        "long long «r» = 0, «c» = 0;\nfor (auto [«w», «x», «y»] : «e») {\n    int «u» = «h»(«x»), «v» = «h»(«y»);\n"
        "    if («u» != «v») { «p»[«u»] = «v»; «r» += «w»; «c»++; }\n}")
_TOPO = ("vector<int> «o»(«N» + 1, 0);   // stopnie wejściowe\nfor (int «v» = 1; «v» <= «N»; «v»++) for (auto [«u», «w»] : «g»[«v»]) «o»[«u»]++;\n"
         "priority_queue<int, vector<int>, greater<int>> «q»;\nfor (int «v» = 1; «v» <= «N»; «v»++) if («o»[«v»] == 0) «q».push(«v»);\n"
         "vector<long long> «r»;\nwhile (!«q».empty()) {\n    int «v» = «q».top();\n    «q».pop();\n    «r».push_back(«v»);\n"
         "    for (auto [«u», «w»] : «g»[«v»]) if (--«o»[«u»] == 0) «q».push(«u»);\n}\n"
         "if ((long long)«r».size() != «N») cout << «NONEW» << \"\\n\";\nelse {\n«SEQ»\n}")
_PREPOST = ("vector<vector<int>> «c»(«N» + 1);\nfor (int «v» = 2; «v» <= «N»; «v»++) «c»[«p»[«v»]].push_back(«v»);\n"
            "vector<long long> «r»;\nfunction<void(int)> «h» = [&](int «v») {\n«PRE»    for (int «u» : «c»[«v»]) «h»(«u»);\n«POST»};\n«h»(1);")


# ---------------------------------------------------------------------------------------- plansze
_DR = (-1, 1, 0, 0, -1, -1, 1, 1)
_DC = (0, 0, -1, 1, -1, 1, -1, 1)


def _grid_ok(g):
    if not g or any(len(r) != len(g[0]) for r in g):
        raise Bad
    return len(g), len(g[0])


def _grid_comps(g, ch, dirs):
    R, C = _grid_ok(g)
    seen = [[False] * C for _ in range(R)]
    sizes = []
    for i in range(R):
        for j in range(C):
            if g[i][j] != ch or seen[i][j]:
                continue
            seen[i][j] = True
            st, c = [(i, j)], 0
            while st:
                x, y = st.pop()
                c += 1
                for k in range(dirs):
                    a, b = x + _DR[k], y + _DC[k]
                    if 0 <= a < R and 0 <= b < C and not seen[a][b] and g[a][b] == ch:
                        seen[a][b] = True
                        st.append((a, b))
            sizes.append(c)
    return sizes


def _grid_bfs(g, src, wall, dirs):
    R, C = _grid_ok(g)
    d = [[-1] * C for _ in range(R)]
    q = deque()
    for (x, y) in src:
        if g[x][y] != wall:
            d[x][y] = 0
            q.append((x, y))
    while q:
        x, y = q.popleft()
        for k in range(dirs):
            a, b = x + _DR[k], y + _DC[k]
            if 0 <= a < R and 0 <= b < C and d[a][b] == -1 and g[a][b] != wall:
                d[a][b] = d[x][y] + 1
                q.append((a, b))
    return d


def _find(g, ch):
    out = [(i, j) for i, r in enumerate(g) for j, c in enumerate(r) if c == ch]
    if len(out) != 1:
        raise Bad
    return out[0]


_GR_DIRS = "const int «dr»[8] = {-1, 1, 0, 0, -1, -1, 1, 1}, «dc»[8] = {0, 0, -1, 1, -1, 1, -1, 1};"
_GR_BFS = ("int «h» = «P».size(), «o» = «P»[0].size();\n" + _GR_DIRS + "\n"
           "vector<vector<long long>> «d»(«h», vector<long long>(«o», -1));\ndeque<pair<int, int>> «q»;\n«SRC»\n"
           "while (!«q».empty()) {\n    auto [«x», «y»] = «q».front();\n    «q».pop_front();\n    for (int «k» = 0; «k» < «DIRS»; «k»++) {\n"
           "        int «a» = «x» + «dr»[«k»], «b» = «y» + «dc»[«k»];\n"
           "        if («a» >= 0 && «a» < «h» && «b» >= 0 && «b» < «o» && «d»[«a»][«b»] == -1 && «P»[«a»][«b»] != «WALL») {\n"
           "            «d»[«a»][«b»] = «d»[«x»][«y»] + 1;\n            «q».push_back({«a», «b»});\n        }\n    }\n}")
_GR_COMPS = ("int «h» = «P».size(), «o» = «P»[0].size();\n" + _GR_DIRS + "\n"
             "vector<vector<int>> «z»(«h», vector<int>(«o», 0));\nvector<long long> «t»;   // rozmiary obszarów\n"
             "for (int «i» = 0; «i» < «h»; «i»++)\n    for (int «j» = 0; «j» < «o»; «j»++) {\n"
             "        if («P»[«i»][«j»] != «CH» || «z»[«i»][«j»]) continue;\n        «z»[«i»][«j»] = 1;\n"
             "        vector<pair<int, int>> «s» = {{«i», «j»}};\n        long long «c» = 0;\n        while (!«s».empty()) {\n"
             "            auto [«x», «y»] = «s».back();\n            «s».pop_back();\n            «c»++;\n"
             "            for (int «k» = 0; «k» < «DIRS»; «k»++) {\n                int «a» = «x» + «dr»[«k»], «b» = «y» + «dc»[«k»];\n"
             "                if («a» >= 0 && «a» < «h» && «b» >= 0 && «b» < «o» && !«z»[«a»][«b»] && «P»[«a»][«b»] == «CH») {\n"
             "                    «z»[«a»][«b»] = 1;\n                    «s».push_back({«a», «b»});\n                }\n            }\n        }\n"
             "        «t».push_back(«c»);\n    }")
_FIND = ("int «fx» = 0, «fy» = 0;\nfor (int «i» = 0; «i» < (int)«P».size(); «i»++)\n    for (int «j» = 0; «j» < (int)«P»[«i»].size(); «j»++)\n"
         "        if («P»[«i»][«j»] == «CH») { «fx» = «i»; «fy» = «j»; }")


def fam_grid(ctx: Ctx):
    for c in _fam_grid(ctx):
        if c.key.endswith(":8"):
            c.tags |= {"diag"}
        yield c


def _fam_grid(ctx: Ctx):
    """Plansze: ile pól danego znaku, obszary (4/8 sąsiadów), BFS między polami-literami i z rogu do rogu."""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    for it in ctx.roles["grids"]:
        P = it.name
        grids = [u[P] for u in ctx.units]
        if not grids or any(not g or any(len(r) != len(g[0]) for r in g) for g in grids):
            continue
        chars = sorted({c for g in grids for r in g for c in r})
        if len(chars) > 8:
            continue
        uniq = [c for c in chars if all(sum(r.count(c) for r in g) == 1 for g in grids)]
        sec = getattr(ctx, "plan_text", "")
        named = [c for c in uniq if c.isalpha() and re.search(r"(?<![A-Za-z0-9])[„\"'‘(]?" + re.escape(c) + r"[”\"'’)]?(?![A-Za-z0-9])", sec)]
        walls = [c for c in chars if c not in uniq]
        W = lambda u, P=P: len(u[P]) * (len(u[P][0]) if u[P] else 0) * 4 + 1  # noqa: E731
        for ch in chars:
            CH = _cpp_char(ch)
            yield _mk(f"grid:cnt:{P}:{ch}", f"ile pól „{ch}”", "grid", 3.0, {"count", "char"},
                      (lambda u, P=P, ch=ch: sum(r.count(ch) for r in u[P])),
                      "long long «r» = 0;\nfor (auto &«x» : «P») «r» += count(«x».begin(), «x».end(), «CH»);", "int",
                      cx="n·m", work=W, P=P, CH=CH)
            for dirs in (4, 8):
                dc = 0.0 if dirs == 4 else 0.6
                yield _mk(f"grid:comps:{P}:{ch}:{dirs}", f"liczba obszarów z pól „{ch}” ({dirs} sąsiadów)", "grid", 2.6 + dc,
                          {"comp", "count"}, (lambda u, P=P, ch=ch, dirs=dirs: len(_grid_comps(u[P], ch, dirs))),
                          _GR_COMPS + "\nlong long «r» = «t».size();", "int", cx="n·m", work=W, P=P, CH=CH, DIRS=dirs)
                yield _mk(f"grid:big:{P}:{ch}:{dirs}", f"największy obszar z pól „{ch}” ({dirs} sąsiadów)", "grid", 2.9 + dc,
                          {"comp", "max"}, (lambda u, P=P, ch=ch, dirs=dirs: max(_grid_comps(u[P], ch, dirs), default=0)),
                          _GR_COMPS + "\nlong long «r» = «t».empty() ? 0 : *max_element(«t».begin(), «t».end());", "int",
                          cx="n·m", work=W, P=P, CH=CH, DIRS=dirs)
        for wall in walls:
            WALL = _cpp_char(wall)
            for dirs in (4, 8):
                dc = 0.0 if dirs == 4 else 0.6
                for s_ in uniq:
                    for t_ in uniq:
                        if s_ == t_:
                            continue

                        def run(u, P=P, s_=s_, t_=t_, wall=wall, dirs=dirs):
                            g = u[P]
                            d = _grid_bfs(g, [_find(g, s_)], wall, dirs)
                            x, y = _find(g, t_)
                            return d[x][y] if d[x][y] != -1 else NONE
                        src = (_FIND.replace("«CH»", _cpp_char(s_)) + "\n" +
                               _FIND.replace("«CH»", _cpp_char(t_)).replace("«fx»", "«gx»").replace("«fy»", "«gy»") +
                               "\n«d»[«fx»][«fy»] = 0;\n«q».push_back({«fx», «fy»});")
                        yield _mk(f"grid:bfs:{P}:{s_}{t_}:{wall}:{dirs}",
                                  f"najmniej kroków z „{s_}” do „{t_}” omijając „{wall}” ({dirs} kierunków)", "grid", 2.6 + dc,
                                  {"dist", "min", "path"}, run, _GR_BFS.replace("«SRC»", src) + "\nlong long «r» = «d»[«gx»][«gy»];",
                                  "int", "«r» == -1", cx="n·m", work=W, P=P, WALL=WALL, DIRS=dirs)
                    if True:
                        def run_fl(u, P=P, s_=s_, wall=wall, dirs=dirs):
                            g = u[P]
                            d = _grid_bfs(g, [_find(g, s_)], wall, dirs)
                            return sum(1 for r in d for x in r if x != -1)
                        src = _FIND.replace("«CH»", _cpp_char(s_)) + "\n«d»[«fx»][«fy»] = 0;\n«q».push_back({«fx», «fy»});"
                        yield _mk(f"grid:flood:{P}:{s_}:{wall}:{dirs}", f"ile pól osiągalnych z „{s_}” omijając „{wall}”",
                                  "grid", 3.0 + dc, {"reach", "count"}, run_fl,
                                  _GR_BFS.replace("«SRC»", src) + "\nlong long «r» = 0;\nfor (auto &«x» : «d») for (long long «y» : «x») if («y» != -1) «r»++;",
                                  "int", cx="n·m", work=W, P=P, WALL=WALL, DIRS=dirs)

                def run_c(u, P=P, wall=wall, dirs=dirs):
                    g = u[P]
                    R, C = _grid_ok(g)
                    if g[0][0] == wall:
                        return NONE
                    d = _grid_bfs(g, [(0, 0)], wall, dirs)
                    return d[R - 1][C - 1] if d[R - 1][C - 1] != -1 else NONE
                src = "if («P»[0][0] != «WALL») { «d»[0][0] = 0; «q».push_back({0, 0}); }"
                yield _mk(f"grid:corner:{P}:{wall}:{dirs}", f"najmniej kroków z lewego górnego do prawego dolnego rogu omijając „{wall}”",
                          "grid", 2.8 + dc + (1.5 if len(named) >= 2 else 0.0), {"dist", "min", "path"}, run_c,
                          _GR_BFS.replace("«SRC»", src) + "\nlong long «r» = «d»[«h» - 1][«o» - 1];", "int", "«r» == -1",
                          cx="n·m", work=W, P=P, WALL=WALL, DIRS=dirs)


# ---------------------------------------------------------------------------------------- macierze
def fam_matrix(ctx: Ctx):
    """Macierze liczb: suma, max/min, sumy wierszy i kolumn, przekątne, najlepsza ścieżka w prawo/w dół."""
    single = _unit_exps(ctx, lambda u: 1) is not None
    for it in ctx.roles["mats"]:
        M = it.name
        W = lambda u, M=M: len(u[M]) * (len(u[M][0]) if u[M] else 0) + 1  # noqa: E731
        sc = [
            ("sum", "suma wszystkich liczb", ("sum",), 2.5, lambda m: _ck(sum(map(sum, m))),
             "long long «r» = 0;\nfor (auto &«x» : «M») for (long long «y» : «x») «r» += «y»;"),
            ("max", "największa liczba", ("max",), 2.6, lambda m: max(max(r) for r in m if r),
             "long long «r» = LLONG_MIN;\nfor (auto &«x» : «M») for (long long «y» : «x») «r» = max(«r», «y»);"),
            ("min", "najmniejsza liczba", ("min",), 2.6, lambda m: min(min(r) for r in m if r),
             "long long «r» = LLONG_MAX;\nfor (auto &«x» : «M») for (long long «y» : «x») «r» = min(«r», «y»);"),
            ("maxrow", "największa suma wiersza", ("max", "sum"), 3.0, lambda m: max(sum(r) for r in m),
             "long long «r» = LLONG_MIN;\nfor (auto &«x» : «M») «r» = max(«r», accumulate(«x».begin(), «x».end(), 0LL));"),
            ("maxcol", "największa suma kolumny", ("max", "sum"), 3.1, lambda m: max(sum(c) for c in zip(*m)),
             "long long «r» = LLONG_MIN;\nfor (size_t «j» = 0; «j» < «M»[0].size(); «j»++) {\n    long long «s» = 0;\n"
             "    for (auto &«x» : «M») «s» += «x»[«j»];\n    «r» = max(«r», «s»);\n}"),
            ("diag", "suma na przekątnej", ("sum",), 3.0, lambda m: _ck(sum(m[i][i] for i in range(min(len(m), len(m[0]))))),
             "long long «r» = 0;\nfor (size_t «i» = 0; «i» < «M».size() && «i» < «M»[0].size(); «i»++) «r» += «M»[«i»][«i»];"),
            ("path", "największa suma ścieżki z lewego górnego rogu w prawo/w dół", ("max", "sum", "path"), 3.0,
             lambda m: _mpath(m, max), _MPATH.replace("«OP»", "max")),
            ("pathmin", "najmniejsza suma ścieżki z lewego górnego rogu w prawo/w dół", ("min", "sum", "path"), 3.0,
             lambda m: _mpath(m, min), _MPATH.replace("«OP»", "min")),
        ]
        if single:
            for key, desc, tags, cost, f, tpl in sc:
                yield _mk(f"mat:{key}:{M}", desc, "matrix", cost, set(tags), (lambda u, f=f, M=M: f(u[M])), tpl, "int",
                          cx="n·m", work=W, M=M)
        if any(len(s.exp) >= 2 for s in ctx.evs):
            yield _mk(f"mat:rows:{M}", "suma każdego wiersza", "matrix", 2.8, {"sum"}, (lambda u, M=M: [_ck(sum(r)) for r in u[M]]),
                      "vector<long long> «r»;\nfor (auto &«x» : «M») «r».push_back(accumulate(«x».begin(), «x».end(), 0LL));",
                      "seq", cx="n·m", work=W, M=M)
            yield _mk(f"mat:cols:{M}", "suma każdej kolumny", "matrix", 2.9, {"sum"},
                      (lambda u, M=M: [_ck(sum(c)) for c in zip(*u[M])]),
                      "vector<long long> «r»(«M».empty() ? 0 : «M»[0].size(), 0);\nfor (auto &«x» : «M») for (size_t «j» = 0; «j» < «x».size(); «j»++) «r»[«j»] += «x»[«j»];",
                      "seq", cx="n·m", work=W, M=M)


_MPATH = ("size_t «h» = «M».size(), «o» = «M»[0].size();\nvector<vector<long long>> «d»(«h», vector<long long>(«o», 0));\n"
          "for (size_t «i» = 0; «i» < «h»; «i»++)\n    for (size_t «j» = 0; «j» < «o»; «j»++) {\n"
          "        if («i» == 0 && «j» == 0) «d»[«i»][«j»] = «M»[0][0];\n        else if («i» == 0) «d»[«i»][«j»] = «d»[«i»][«j» - 1] + «M»[«i»][«j»];\n"
          "        else if («j» == 0) «d»[«i»][«j»] = «d»[«i» - 1][«j»] + «M»[«i»][«j»];\n"
          "        else «d»[«i»][«j»] = «OP»(«d»[«i» - 1][«j»], «d»[«i»][«j» - 1]) + «M»[«i»][«j»];\n    }\nlong long «r» = «d»[«h» - 1][«o» - 1];")


def _mpath(m, op):
    R, C = len(m), len(m[0]) if m else 0
    if not R or not C:
        raise Bad
    d = [[0] * C for _ in range(R)]
    for i in range(R):
        for j in range(C):
            if i == 0 and j == 0:
                d[i][j] = m[0][0]
            elif i == 0:
                d[i][j] = d[i][j - 1] + m[i][j]
            elif j == 0:
                d[i][j] = d[i - 1][j] + m[i][j]
            else:
                d[i][j] = op(d[i - 1][j], d[i][j - 1]) + m[i][j]
    return _ck(d[-1][-1])


FAMILIES = [fam_formula, fam_rows_formula, fam_elem_formula, fam_array, fam_seq, fam_pairs, fam_subarrays, fam_subsets,
            fam_queries, fam_scan, fam_strings, fam_graph, fam_grid, fam_matrix, lambda ctx: fam_rows2(ctx),
            lambda ctx: fam_recurrence(ctx)]


# ============================================================================================ losowe wejścia
def _plan_write(plan: Plan, env: dict) -> str:
    lines: list[str] = []

    def items(its, e):
        for it in its:
            k = it.kind
            if k == "scal":
                lines.append(" ".join(str(e[n]) for n in it.names))
            elif k == "str":
                lines.append(str(e[it.name]))
            elif k == "arr":
                lines.append(" ".join(str(x) for x in e[it.name]))
            elif k == "rows":
                for i in range(len(e[it.names[0]])):
                    lines.append(" ".join(str(e[f][i]) for f in it.names))
                if it.until is not None:
                    lines.append(str(it.until))
            elif k == "mat":
                for r in e[it.name]:
                    lines.append(" ".join(str(x) for x in r))
            elif k == "grid":
                lines.extend(e[it.name])
            elif k == "ops":
                for key, args in e[it.name]:
                    lines.append(" ".join([key] + [str(x) for x in args]))
            elif k == "mt":
                lines.append(str(len(e["#tests"])))
                for t in e["#tests"]:
                    items(it.items, t)
    items(plan.items, env)
    return "\n".join(lines) + "\n"


def _len_users(plan: Plan) -> Counter:
    c: Counter = Counter()
    for it in plan.top():
        for L in (it.length, it.count):
            if L is not None and L.kind == "var":
                c[L.var] += 1
    return c


def _mutate(plan: Plan, env: dict, rng: random.Random) -> dict | None:
    """Mała losowa odmiana przykładu: krótsze/przetasowane ciągi, inne parametry — zgodna z planem wczytywania."""
    users = _len_users(plan)
    e = {k: (list(v) if isinstance(v, list) else v) for k, v in env.items() if not k.startswith("#c:")}
    units = [dict(t) for t in env["#tests"]] if plan.mt else [e]
    if plan.mt:
        e["#tests"] = units
    for u in units:
        for k in list(u):
            if k.startswith("#c:"):
                del u[k]
        for it in plan.top():
            if it.kind == "arr" and it.typ == "i" and u.get(it.name):
                v = list(u[it.name])
                lo, hi = min(v), max(v)
                if it.length is not None and it.length.kind == "var" and users[it.length.var] == 1 and it.length.mul == 1:
                    keep = rng.randint(max(1, -it.length.add + 1 if it.length.add < 0 else 1), min(len(v), 7))
                    v = rng.sample(v, keep)
                    u[it.length.var] = keep - it.length.add
                for i in range(len(v)):
                    if rng.random() < 0.4:
                        v[i] = rng.randint(lo, max(lo, min(hi, lo + 12)))
                rng.shuffle(v)
                u[it.name] = v
            elif it.kind == "rows" and it.count is not None and it.count.kind == "var" and users[it.count.var] == 1 \
                    and it.count.mul == 1 and u.get(it.names[0]):
                m = len(u[it.names[0]])
                keep = sorted(rng.sample(range(m), rng.randint(0, min(m, 6))))
                for f in it.names:
                    u[f] = [u[f][i] for i in keep]
                u[it.count.var] = len(keep) - it.count.add
            elif it.kind == "str" and u.get(it.name):
                sv = u[it.name]
                L = rng.randint(1, min(len(sv), 8))
                u[it.name] = "".join(rng.choice(sv) for _ in range(L)) if not sv.isdigit() else str(rng.randint(1, 10**L))
            elif it.kind == "scal":
                for nm, ty in zip(it.names, it.types):
                    if ty == "i" and users[nm] == 0 and isinstance(u.get(nm), int) and rng.random() < 0.7:
                        x = u[nm]
                        u[nm] = rng.randint(max(0, x // 2) if x >= 0 else x * 2, max(3, x + 3) if x >= 0 else 0)
    return e


def random_inputs(plan: Plan, parsed: dict, text: str, sample_envs: list[tuple[str, dict]], n: int = 24,
                  budget: float = 1.5) -> list[tuple[str, dict]]:
    """Małe losowe wejścia: generator `inputgen` (format z przykładów i treści) + odmiany przykładów."""
    t0 = time.time()
    out: list[tuple[str, dict]] = []
    seen: set = set()

    def add(txt):
        if txt in seen or len(txt) > 4000:
            return
        env = plan.read(txt)
        if env is None:
            return
        seen.add(txt)
        out.append((txt, env))
    try:
        from . import inputgen as IG
        spec = IG.infer([s for s, _ in sample_envs], text or None, time_budget=min(1.0, budget / 2))
        rng = random.Random(20251)
        strat = ["random", "min", "random", "extreme", "equal", "random", "sorted", "random_tree", "random", "reversed",
                 "connected", "random"]
        for k in range(n * 3):
            if len(out) >= n or time.time() - t0 > budget:
                break
            try:
                add(IG.generate(spec, strat[k % len(strat)], "tiny" if k % 3 != 2 else "small", rng))
            except Exception:  # noqa: BLE001 — generator bywa kapryśny dla nietypowych formatów
                continue
    except Exception:  # noqa: BLE001
        pass
    if len(out) < n // 2 and sample_envs:
        rng = random.Random(7)
        for k in range(n * 4):
            if len(out) >= n or time.time() - t0 > budget + 1.0:
                break
            _src, env = sample_envs[k % len(sample_envs)]
            try:
                m = _mutate(plan, env, rng)
                if m is not None:
                    add(_plan_write(plan, m))
            except Exception:  # noqa: BLE001 — odmiana nie wyszła, próbujemy dalej
                continue
    out.sort(key=lambda x: len(x[0]))
    return out


# ============================================================================================ złożoność
def _cx_ops(cx: str, ctx: Ctx, parsed: dict) -> float | None:
    """Szacunkowa liczba operacji dla największych danych z treści (None = nie wiadomo)."""
    sb = ST.size_bounds(parsed.get("constraints") or {})

    def bound(nm):
        b = sb.get(nm) or sb.get(re.sub(r"[_\d]+$", "", nm)) or {}
        mx = b.get("max")
        return mx if isinstance(mx, (int, float)) and mx > 0 else None
    sizes = [bound(v) for v in ctx.roles["sizes"]]
    sizes = [x for x in sizes if x]
    for g in ctx.roles["grids"] + ctx.roles["mats"]:
        if g.count is not None and g.count.kind == "var":
            b = bound(g.count.var)
            if b:
                sizes.append(b)
    for s_ in ctx.roles["strs"]:
        for k, v in sb.items():
            if k.strip("|") == s_ and isinstance(v.get("max"), (int, float)):
                sizes.append(v["max"])
    N = max(sizes) if sizes else None
    vals = [bound(v) for v in ctx.roles["ints"]]
    vals = [x for x in vals if x]
    V = max(vals) if vals else None
    T_ = 1
    if ctx.mt and ctx.plan.mt is not None:
        T_ = bound(ctx.plan.mt.var) or 1
    lg = lambda x: max(1.0, math.log2(x))  # noqa: E731
    if cx == "1":
        return T_
    if cx in ("sqrt(V)",):
        return None if V is None else T_ * math.sqrt(V)
    if cx == "V":
        return None if V is None else T_ * V
    if cx == "V log V":
        return None if V is None else T_ * V * lg(V) * 2
    if N is None:
        return None
    table = {"q log q": N * lg(N), "n": N, "n log n": N * lg(N), "m": N, "m log n": N * lg(N), "m log m": N * lg(N), "n + m": N,
             "n^2": N * N, "n·k": N * N, "n·q": N * N, "n·m": N * N, "n^3": N ** 3, "2^n·n": (2.0 ** min(N, 200)) * N}
    v = table.get(cx)
    return None if v is None else T_ * v


# ============================================================================================ C++ i weryfikacja
def assemble(ctx: Ctx, c: Cand, title: str | None = None, note: list[str] | None = None) -> str:
    G = Gen(ctx)
    body = c.cpp(G)
    main = ctx.plan.cpp_reader(body=body, fresh=G.f)
    helpers = [CPP_HELPERS[h] for h in _HELPER_ORDER if h in G.helpers]
    head = []
    if title:
        head.append(f"// Zadanie: {title}")
    head.append(f"// Program wygenerowany przez syntezę OI Forge (bez AI): {c.desc}")
    for ln in note or []:
        head.append("// " + ln)
    return ("\n".join(head) + "\n#include <bits/stdc++.h>\nusing namespace std;\n\n"
            + ("\n\n".join(helpers) + "\n\n" if helpers else "")
            + "int main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n" + main + "\n    return 0;\n}\n")


def verify_cpp(ctx: Ctx, c: Cand, code: str, samples_raw: list[dict], timeout: float = 2.0, job=None) -> dict:
    """Kompilacja, przykłady (duże: TLE dozwolone dla brute) i zgodność z interpreterem na losowych wejściach."""
    from . import runner
    import os
    prog = runner.prepare(code, lang="cpp")
    if not prog.ok:
        return {"ok": False, "why": "compile", "error": (prog.compile.stderr if prog.compile else prog.error or "")[:2000]}

    def alive():
        nonlocal prog
        if prog.cmd and not os.path.exists(prog.cmd[0]):      # binarka wypadła z cache — skompiluj ponownie
            prog = runner.prepare(code, lang="cpp")
        return prog
    res = []
    slow = 0
    for s in samples_raw:
        big = len(s["input"]) > BIG_INPUT
        rr = runner.run(alive().cmd, s["input"], timeout=timeout * (2 if big else 1))
        if rr.verdict == "TLE":
            slow += 1
            res.append({"name": s.get("name"), "verdict": "TLE", "ok": None, "source": s.get("source")})
            continue
        ok = rr.verdict == "OK" and ctx.out.same(_toks(rr.stdout), _toks(s["output"]))
        res.append({"name": s.get("name"), "verdict": "OK" if ok else (rr.verdict if rr.verdict != "OK" else "WA"),
                    "ok": ok, "got": rr.stdout[:300], "expected": s["output"][:300], "source": s.get("source")})
        if not ok:
            return {"ok": False, "why": "sample", "results": res}
    import concurrent.futures as cf
    todo = [(txt, sig) for (txt, _env), sig in zip(ctx.rnd, c.sig or ()) if sig is not None][:CROSS_TESTS]

    alive()

    def one(item):
        txt, sig = item
        rr = runner.run(prog.cmd, txt, timeout=timeout * 2)
        if rr.verdict == "TLE":                      # obciążony komputer — jedna powtórka z dłuższym limitem
            rr = runner.run(prog.cmd, txt, timeout=timeout * 6)
        ok = rr.verdict == "OK" and ctx.out.same(_toks(rr.stdout), list(sig))
        return ok, txt, sig, (rr.stdout[:200] if rr.verdict == "OK" else rr.verdict)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        outs = list(ex.map(one, todo))
    for ok, txt, sig, got in outs:
        if not ok:
            return {"ok": False, "why": "translate", "input": txt, "python": " ".join(sig)[:200], "cpp": got}
    return {"ok": True, "results": res, "slow": slow, "cross": len(outs), "passed": sum(1 for r in res if r["ok"]),
            "total": len(res)}


# ============================================================================================ główna funkcja
MARGIN = 1.5
CROSS_TESTS = 20
SCORE_FLOOR = -6.5      # dziwaczne programy (dużo pojęć spoza treści) nie są pokazywane


FORMULA_BITS = 19.0     # wzory: ok. 40 tys. wyrażeń ≈ 2^15 — przykłady muszą nieść wyraźnie więcej informacji


def _evidence_bits(ctx: Ctx) -> float:
    """Ile informacji niosą wyjścia przykładów (w bitach, z grubsza): mała liczba ≈ 2–4 bity, TAK/NIE = 1 bit."""
    bits = 0.0
    for s in ctx.samples:
        if s.env is None:
            continue
        for t in s.exp:
            if _is_num(t):
                b = math.log2(abs(int(t)) + 2) + 1
            elif _is_float(t):
                b = 3.3 * len(t.replace("-", "").replace(".", ""))
            elif t in (ctx.out.yes, ctx.out.no):
                b = 1.0
            else:
                b = 4.0
            bits += min(24.0, b)
    return bits


def _trivial(ctx: Ctx, c: Cand) -> bool:
    checked = [s for s in ctx.samples if s.env is not None]
    toks = [t for s in checked for t in s.exp]
    if len(checked) <= 1 and len(toks) <= 1 and all(len(t) <= 3 for t in toks):
        return True
    # wzory są wyliczane z ogromnej przestrzeni — dwa małe wyniki („2”, „3”) łatwo trafić przypadkiem
    return c.fam in ("formula", "rows", "elem") and _evidence_bits(ctx) < FORMULA_BITS


def _search(parsed: dict, statement_text: str, words: dict | None, budget: float, prog, t0: float):
    """Plan wczytywania, przykłady, losowe wejścia i wszystkie kandydaty pasujące do przykładów (bez kompilacji)."""
    samples_raw = [s for s in parsed.get("samples") or [] if s.get("input") is not None and s.get("output")]
    base = {"status": "none", "code": None, "time": 0.0, "candidates": 0, "matches": 0, "competitors": [],
            "reasons": [], "message": ""}
    if not samples_raw:
        base.update(status="no_samples", message="Treść nie ma przykładów — nie mam na czym sprawdzić programu.")
        return base, None, [], samples_raw
    plan = build_plan(parsed.get("input_format") or [], samples_raw)
    if plan is None:
        base.update(status="no_plan", message="Nie rozpoznałem formatu wejścia na tyle, żeby czytać przykłady.")
        base["time"] = round(time.time() - t0, 2)
        return base, None, [], samples_raw
    out = OutSpec(samples_raw, words)
    samples = []
    for s in samples_raw:
        big = len(s["input"]) > BIG_INPUT
        env = None if big else plan.read(s["input"])
        if env is None and not big:
            base.update(status="no_plan", message="Plan wczytywania nie pasuje do przykładu.")
            return base, None, [], samples_raw
        samples.append(Sample(s.get("name"), env, _toks(s["output"]), s.get("source"), big, len(s["input"]), s["output"]))
    samples.sort(key=lambda x: (x.env is None, x.size))
    prog(0.05, "Synteza: losuję małe testy do porównań…")
    sample_envs = [(s["input"], plan.read(s["input"])) for s in samples_raw if len(s["input"]) <= BIG_INPUT]
    rnd = random_inputs(plan, parsed, statement_text, [x for x in sample_envs if x[1] is not None], budget=1.2)
    deadline = t0 + budget
    ctx = Ctx(plan, out, samples, rnd, keyword_hits(parsed, statement_text), statement_consts(parsed), deadline)
    ctx.plan_text = " ".join((parsed.get("sections") or {}).get(k, "") for k in ("tresc", "wejscie", "wyjscie")) \
        or statement_text
    prog(0.15, "Synteza: przeszukuję programy (wzory, pary, fragmenty, podzbiory, grafy…)")
    matches: list[Cand] = []
    n_c = 0
    seen_keys = set()
    for fi, fam in enumerate(FAMILIES):
        if time.time() > deadline:
            break
        try:
            for c in fam(ctx):
                if time.time() > deadline:
                    break
                n_c += 1
                if c.key in seen_keys:
                    continue
                seen_keys.add(c.key)
                if check_cand(ctx, c):
                    score_cand(ctx, c)
                    if c.score >= SCORE_FLOOR:
                        matches.append(c)
        except (Bad, KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, RecursionError):
            continue
        prog(0.15 + 0.5 * (fi + 1) / len(FAMILIES), f"Synteza: sprawdzono {n_c} programów, pasuje {len(matches)}")
    base.update(candidates=n_c, matches=len(matches), random_tests=len(rnd))
    return base, ctx, matches, samples_raw


def synthesize(parsed: dict, statement_text: str = "", *, words: dict | None = None, job=None, budget: float = 6.0,
               want: str = "brute", f0: float = 0.0, f1: float = 1.0, timeout: float = 2.0) -> dict:
    """Szuka programu pasującego do wszystkich przykładów. Wynik:
    {status: "verified" | "weak" | "none" | "no_plan" | "no_samples", code, desc, family, cx, fast, competitors,
     distinguishing, checked, cross, reasons, message, time}."""
    t0 = time.time()

    def prog(f, msg):
        if job is not None:
            job.progress(f0 + (f1 - f0) * f, msg)
    base, ctx, matches, samples_raw = _search(parsed, statement_text, words, budget, prog, t0)
    if ctx is None:
        base["time"] = round(time.time() - t0, 2)
        return base
    rnd, n_c, deadline = ctx.rnd, base["candidates"], ctx.deadline
    if not matches:
        base.update(status="none", message=f"Sprawdziłem {n_c} programów z języka syntezy — żaden nie daje wyników z "
                                           "przykładów.")
        base["time"] = round(time.time() - t0, 2)
        return base
    matches.sort(key=lambda c: (-c.score, c.cost))
    top = matches[:50]
    for c in top:
        c.sig = signature(ctx, c)
    clusters: list[list[Cand]] = []
    for c in top:
        for cl in clusters:
            if _differs(cl[0].sig, c.sig) is None:
                cl.append(c)
                break
        else:
            clusters.append([c])
    prog(0.7, "Synteza: tłumaczę najlepszy program na C++ i sprawdzam…")
    chosen = None
    tried = 0
    fails = []
    for ci, cl in enumerate(clusters[:4]):
        for c in cl[:3]:
            if tried and time.time() > deadline + 8:   # najlepszego kandydata sprawdzamy zawsze (wolny komputer)
                break
            tried += 1
            fast_ops = _cx_ops(c.cx, ctx, parsed)
            note = [f"Złożoność: O({c.cx.replace('^2', '²').replace('^3', '³')})" +
                    (f" — dla największych danych ok. {ST.fmt_value(int(fast_ops))} operacji" if fast_ops else "")]
            code = assemble(ctx, c, parsed.get("title"), note)
            v = verify_cpp(ctx, c, code, samples_raw, timeout=timeout)
            if v["ok"]:
                chosen = (ci, c, code, v, fast_ops)
                break
            fails.append({"desc": c.desc, "why": v.get("why")})
        if chosen:
            break
    if not chosen:
        base.update(status="none", message="Znalazłem programy pasujące do przykładów w interpreterze, ale żaden nie "
                                           "przeszedł weryfikacji w C++.", fails=fails)
        base["time"] = round(time.time() - t0, 2)
        return base
    ci, best, code, v, ops = chosen
    rest = [cl for i, cl in enumerate(clusters) if i != ci]
    competitors = []
    for cl in rest:
        r = cl[0]
        if r.score < best.score - MARGIN or r.nspec < best.nspec:
            continue
        i = _differs(best.sig, r.sig)
        comp = {"desc": r.desc, "family": r.fam, "score": r.score, "code": None}
        if i is not None:
            comp["input"] = ctx.rnd[i][0]
            comp["output"] = " ".join(r.sig[i])[:200]
            comp["best_output"] = " ".join(best.sig[i])[:200]
        try:
            comp["code"] = assemble(ctx, r, parsed.get("title"), ["UWAGA: to jeden z kilku programów zgodnych z przykładami."])
        except Exception:  # noqa: BLE001
            comp["code"] = None
        competitors.append(comp)
        if len(competitors) >= 3:
            break
    reasons = []
    if competitors:
        reasons.append("ambiguous")
    if _trivial(ctx, best):
        reasons.append("trivial")
    if not best.aligned:
        reasons.append("unaligned")
    if not rnd or all(x is None for x in (best.sig or ())):
        reasons.append("unchecked")
    fast = ops is not None and ops <= 2e8
    if "unaligned" in reasons and ({"trivial", "ambiguous"} & set(reasons)):
        # ani słowa treści, ani przykłady nie wskazują tego programu — to byłaby zgadywanka, nie pokazujemy jej
        base.update(status="none", reasons=reasons, guess=best.desc,
                    message=f"Do przykładów pasuje {len(matches)} programów z języka syntezy, ale żaden nie ma związku ze "
                            "słowami treści, a przykłady ich nie rozstrzygają — nie pokazuję zgadywanki.")
        base["time"] = round(time.time() - t0, 2)
        return base
    status = "weak" if reasons else "verified"
    msg = _message(best, v, reasons, competitors, len(rnd))
    base.update(status=status, code=code, desc=best.desc, family=best.fam, cx=best.cx, ops=ops, fast=fast,
                score=best.score, aligned=best.aligned, competitors=competitors, reasons=reasons, message=msg,
                passed=v["passed"], total=v["total"], slow=v["slow"], cross=v["cross"], results=v["results"],
                tags=sorted(best.tags & ctx.hits), others_same=[c.desc for c in clusters[ci][1:4]])
    base["time"] = round(time.time() - t0, 2)
    return base


def _message(best: Cand, v: dict, reasons: list, competitors: list, nrnd: int) -> str:
    head = f"Synteza: „{best.desc}” — przykłady {v['passed']}/{v['total']}"
    if v.get("slow"):
        head += f" (na {v['slow']} za wolny — to normalne dla brute)"
    head += f", zgodny z interpreterem na {v['cross']} losowych testach."
    if not reasons:
        return head
    why = []
    if "ambiguous" in reasons:
        why.append(f"{len(competitors)} {'inny program też pasuje' if len(competitors) == 1 else 'inne programy też pasują'}"
                   " do wszystkich przykładów, ale daje inne wyniki na losowym teście — przykłady nie rozstrzygają")
    if "trivial" in reasons:
        why.append("jedyny przykład ma bardzo krótki wynik — zgodność mogła wyjść przypadkiem")
    if "unaligned" in reasons:
        why.append("ten program nie ma związku ze słowami treści")
    if "unchecked" in reasons:
        why.append("nie udało się go porównać na losowych testach")
    return head + " NIEPEWNE: " + "; ".join(why) + "."


def reader_program(parsed: dict, title: str | None = None) -> str | None:
    """Kompletny program, który TYLKO wczytuje dane wg formatu z treści (bez udawanego rozwiązania)."""
    samples = [s for s in parsed.get("samples") or [] if s.get("input") is not None]
    fmt = parsed.get("input_format") or []
    plan = build_plan(fmt, samples) if samples else None
    if plan is not None:
        ops_info = _ops_info(parsed) if any(it.kind == "ops" for it in plan.top()) else {}
        main = plan.cpp_reader(body="// (tu są już wczytane dane jednego zestawu)" if plan.mt else None, ops_info=ops_info)
        return _reader_wrap(main, title, parsed)
    # Format z treści nie zgadza się z przykładami (np. rozmiar n odczytany jako napis albo niezadeklarowany).
    # 1) format wywnioskowany z samych przykładów (inputgen — ten sam, z którego testerka generuje testy),
    # 2) format z treści, 3) kształt przykładów. Każdy wariant musi się skompilować.
    tries = []
    raw = [s["input"] for s in samples if not s.get("big") and str(s.get("input") or "").strip()]
    if raw:
        try:
            from . import inputgen as IG
            text = "\n".join([parsed.get("title") or ""] + [str(v) for v in (parsed.get("sections") or {}).values()])
            spec = IG.infer(raw, text, time_budget=2.0)
            ops = _ops_info(parsed) if any(it.get("kind") == "queries" for it in _walk_spec(spec.get("items") or [])) else {}
            tries.append(IG.cpp_reader(spec, ops_info=ops))
        except Exception:  # noqa: BLE001 — niepasujące przykłady, przekroczony czas itp.
            pass
    if fmt or samples:
        tries.append((ST.cpp_reader(fmt, samples=samples) or {}).get("code"))
    if fmt and samples:
        tries.append((ST.cpp_reader([], samples=samples) or {}).get("code"))
    for main in tries:
        if main and main.strip():
            code = _reader_wrap(main, title, parsed)
            if _compiles(code):
                return code
    return None


def _walk_spec(items: list[dict]):
    for it in items:
        yield it
        yield from _walk_spec(it.get("items") or [])


def _ops_info(parsed: dict) -> dict:
    """Nazwy argumentów i znaczenie operacji z treści („Q x — wypisanie stanu lampy”)."""
    try:
        from ..tools import taskexplain as TE
        return {q["tag"]: q for q in TE.structure(parsed).get("queries") or [] if q.get("tag")}
    except Exception:  # noqa: BLE001 — bez opisu też da się wczytać
        return {}


def _reader_wrap(main: str, title: str | None, parsed: dict) -> str:
    head = (f"// Zadanie: {title or parsed.get('title') or '(bez tytułu)'}\n"
            "// Gotowe wczytywanie danych wygenerowane z formatu wejścia (to NIE jest rozwiązanie).\n")
    return (head + "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n"
            "    cin.tie(nullptr);\n" + main + "\n    return 0;\n}\n")


def _compiles(code: str) -> bool:
    from . import runner
    try:
        return bool(runner.prepare(code, lang="cpp").ok)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------------------- wiersze „x y”
def _iv_union(xs, ys, closed):
    tot, cl, cr = 0, None, None
    for l, r in sorted(zip(xs, ys)):
        if r < l:
            raise Bad
        r = r + 1 if closed else r
        if cr is None or l > cr:
            if cr is not None:
                tot += cr - cl
            cl, cr = l, r
        else:
            cr = max(cr, r)
    return tot + (cr - cl if cr is not None else 0)


def _iv_overlap(xs, ys):
    ev = sorted([(l, 0) for l in xs] + [(r, 1) for r in ys])
    best = cur = 0
    for _p, t in ev:
        cur += 1 if t == 0 else -1
        best = max(best, cur)
    return best


def _iv_disjoint(xs, ys, strict):
    cnt, last = 0, None
    for l, r in sorted(zip(xs, ys), key=lambda t: (t[1], t[0])):
        if last is None or (l > last if strict else l >= last):
            cnt += 1
            last = r
    return cnt


_RL = "for (size_t «i» = 0; «i» < «X».size(); «i»++) "
_IV_UNION = ("vector<pair<long long, long long>> «b»;\n" + _RL + "«b».push_back({«X»[«i»], «Y»[«i»] + «CL»});\n"
             "sort(«b».begin(), «b».end());\nlong long «r» = 0, «l» = 0, «h» = LLONG_MIN;\nfor (auto [«x», «y»] : «b») {\n"
             "    if («h» == LLONG_MIN || «x» > «h») {\n        if («h» != LLONG_MIN) «r» += «h» - «l»;\n        «l» = «x»;\n"
             "        «h» = «y»;\n    } else «h» = max(«h», «y»);\n}\nif («h» != LLONG_MIN) «r» += «h» - «l»;")
_IV_OVER = ("vector<pair<long long, int>> «b»;\n" + _RL + "{ «b».push_back({«X»[«i»], 0}); «b».push_back({«Y»[«i»], 1}); }\n"
            "sort(«b».begin(), «b».end());\nlong long «r» = 0, «c» = 0;\n"
            "for (auto [«x», «t»] : «b») { «c» += «t» == 0 ? 1 : -1; «r» = max(«r», «c»); }")
_IV_DISJ = ("vector<pair<long long, long long>> «b»;   // (koniec, początek)\n" + _RL + "«b».push_back({«Y»[«i»], «X»[«i»]});\n"
            "sort(«b».begin(), «b».end());\nlong long «r» = 0, «l» = 0;\nbool «f» = false;\n"
            "for (auto [«y», «x»] : «b») if (!«f» || «x» «CMP» «l») { «r»++; «l» = «y»; «f» = true; }")
_KNAP = ("int «m» = «X».size();\nlong long «r» = 0;\nfor (long long «mask» = 0; «mask» < (1LL << «m»); «mask»++) {\n"
         "    long long «s» = 0, «t» = 0;\n    for (int «b» = 0; «b» < «m»; «b»++) if («mask» >> «b» & 1) { «s» += «X»[«b»]; «t» += «Y»[«b»]; }\n"
         "    if («s» <= «K») «r» = max(«r», «t»);\n}")


def _knap(ws, vs, cap):
    if len(ws) > 18:
        raise Bad
    best = 0
    for m in range(1 << len(ws)):
        s = t = 0
        for b in range(len(ws)):
            if m >> b & 1:
                s += ws[b]
                t += vs[b]
        if s <= cap:
            best = max(best, t)
    return best


def fam_rows2(ctx: Ctx):
    """Wiersze „x y”: agregaty po wierszach, przedziały [x, y] (suma, największe pokrycie, najwięcej rozłącznych)
    i plecak „waga wartość” z pojemnością (wszystkie podzbiory)."""
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    for R in ctx.roles["rows"]:
        if len(R.names) != 2:
            continue
        X, Y = R.names
        W = lambda u, X=X: len(u[X]) * 3 + 1  # noqa: E731
        z = lambda f, X=X, Y=Y: (lambda u: f(u[X], u[Y]))  # noqa: E731
        items = [
            ("sumprod", f"suma {X}·{Y} po wierszach", ("sum", "product"), 2.6,
             lambda a, b: _ck(sum(x * y for x, y in zip(a, b))), "long long «r» = 0;\n" + _RL + "«r» += «X»[«i»] * «Y»[«i»];"),
            ("maxprod", f"największe {X}·{Y}", ("max", "product"), 2.8,
             lambda a, b: max(x * y for x, y in zip(_need_nonempty(a), b)),
             "long long «r» = LLONG_MIN;\n" + _RL + "«r» = max(«r», «X»[«i»] * «Y»[«i»]);"),
            ("maxsum", f"największe {X} + {Y}", ("max", "sum"), 2.8, lambda a, b: max(x + y for x, y in zip(_need_nonempty(a), b)),
             "long long «r» = LLONG_MIN;\n" + _RL + "«r» = max(«r», «X»[«i»] + «Y»[«i»]);"),
            ("maxdiff", f"największe {Y} − {X}", ("max", "diff"), 2.9, lambda a, b: max(y - x for x, y in zip(_need_nonempty(a), b)),
             "long long «r» = LLONG_MIN;\n" + _RL + "«r» = max(«r», «Y»[«i»] - «X»[«i»]);"),
            ("sumdiff", f"suma ({Y} − {X})", ("sum", "len"), 2.9, lambda a, b: _ck(sum(y - x for x, y in zip(a, b))),
             "long long «r» = 0;\n" + _RL + "«r» += «Y»[«i»] - «X»[«i»];"),
            ("cntlt", f"w ilu wierszach {X} < {Y}", ("count",), 3.0, lambda a, b: sum(1 for x, y in zip(a, b) if x < y),
             "long long «r» = 0;\n" + _RL + "if («X»[«i»] < «Y»[«i»]) «r»++;"),
            ("cnteq", f"w ilu wierszach {X} = {Y}", ("count", "equal"), 3.0, lambda a, b: sum(1 for x, y in zip(a, b) if x == y),
             "long long «r» = 0;\n" + _RL + "if («X»[«i»] == «Y»[«i»]) «r»++;"),
            ("cntgt", f"w ilu wierszach {X} > {Y}", ("count",), 3.0, lambda a, b: sum(1 for x, y in zip(a, b) if x > y),
             "long long «r» = 0;\n" + _RL + "if («X»[«i»] > «Y»[«i»]) «r»++;"),
            ("summax", f"suma max({X}, {Y})", ("sum", "max"), 3.1, lambda a, b: _ck(sum(max(x, y) for x, y in zip(a, b))),
             "long long «r» = 0;\n" + _RL + "«r» += max(«X»[«i»], «Y»[«i»]);"),
            ("summin", f"suma min({X}, {Y})", ("sum", "min"), 3.1, lambda a, b: _ck(sum(min(x, y) for x, y in zip(a, b))),
             "long long «r» = 0;\n" + _RL + "«r» += min(«X»[«i»], «Y»[«i»]);"),
            ("union", f"łączna długość przedziałów [{X}, {Y}) (bez liczenia dwa razy)", ("subarray", "sum", "len"), 3.0,
             lambda a, b: _iv_union(a, b, False), _IV_UNION.replace("«CL»", "0")),
            ("unionc", f"ile liczb całkowitych należy do któregoś z przedziałów [{X}, {Y}]", ("subarray", "count"), 3.2,
             lambda a, b: _iv_union(a, b, True), _IV_UNION.replace("«CL»", "1")),
            ("over", f"największa liczba przedziałów [{X}, {Y}] zawierających wspólny punkt", ("subarray", "max"), 3.0,
             _iv_overlap, _IV_OVER),
            ("disj", f"najwięcej rozłącznych przedziałów [{X}, {Y}]", ("subarray", "max", "count"), 3.0,
             lambda a, b: _iv_disjoint(a, b, True), _IV_DISJ.replace("«CMP»", ">")),
            ("disjh", f"najwięcej nienachodzących przedziałów [{X}, {Y}) (mogą się stykać)", ("subarray", "max", "count"), 3.2,
             lambda a, b: _iv_disjoint(a, b, False), _IV_DISJ.replace("«CMP»", ">=")),
        ]
        for key, desc, tags, cost, f, tpl in items:
            yield _mk(f"rows2:{key}:{R.name}", desc, "rows", cost, set(tags), z(f), tpl, "int", cx="n log n", work=W, X=X, Y=Y)
        for K, getK, kc in _k_choices(ctx, extra_consts=False):
            for wf, vf, d in ((X, Y, f"{X} = waga, {Y} = wartość"), (Y, X, f"{Y} = waga, {X} = wartość")):
                yield _mk(f"rows2:knap:{R.name}:{wf}:{K}", f"plecak: największa suma wartości przy wadze ≤ {K} ({d})", "subset",
                          3.0 + kc + (0.3 if wf == Y else 0), {"subset", "sum", "max", "atmost"},
                          (lambda u, wf=wf, vf=vf, getK=getK: _knap(u[wf], u[vf], getK(u))), _KNAP, "int", cx="2^n·n",
                          work=lambda u, X=X: (1 << min(len(u[X]), 30)) * len(u[X]) + 1, X=wf, Y=vf, K=K)


# ---------------------------------------------------------------------------------------- ciągi rekurencyjne
def _mat_mul(P, Q, M):
    n = len(P)
    return [[sum(P[i][t] * Q[t][j] for t in range(n)) % M for j in range(n)] for i in range(n)]


def _recur(n, c, f0, d, M):
    """f(i) = c[0]·f(i−1) + … + c[k−1]·f(i−k) + d (mod M), f(0..k−1) = f0; potęgowanie macierzy (jak w C++)."""
    k = len(c)
    if n < 0:
        raise Bad
    if n < k:
        return f0[n] % M
    S = k + 1
    A = [[0] * S for _ in range(S)]
    for j in range(k):
        A[0][j] = c[j] % M
    A[0][k] = d % M
    for i in range(1, k):
        A[i][i - 1] = 1
    A[k][k] = 1
    Rm = [[int(i == j) for j in range(S)] for i in range(S)]
    e = n - (k - 1)
    while e > 0:
        if e & 1:
            Rm = _mat_mul(Rm, A, M)
        A = _mat_mul(A, A, M)
        e >>= 1
    res = sum(Rm[0][j] * (f0[k - 1 - j] % M) for j in range(k)) + Rm[0][k]
    return res % M


def _recur_exact(n, c, f0, d):
    k = len(c)
    if n < 0 or n > 400:
        raise Bad
    f = list(f0)
    while len(f) <= n:
        f.append(_ck(sum(c[j] * f[-1 - j] for j in range(k)) + d))
    return f[n]


CPP_HELPERS["recur"] = (
    "long long recur(long long n, vector<long long> c, vector<long long> f0, long long d, long long M) {\n"
    "    // f(i) = c[0]·f(i−1) + … + c[k−1]·f(i−k) + d (mod M), f(0..k−1) = f0 — szybkie potęgowanie macierzy\n"
    "    int k = c.size(), S = k + 1;\n    if (n < k) return f0[n] % M;\n"
    "    vector<vector<long long>> A(S, vector<long long>(S, 0)), R(S, vector<long long>(S, 0));\n"
    "    for (int j = 0; j < k; j++) A[0][j] = c[j] % M;\n    A[0][k] = d % M;\n"
    "    for (int i = 1; i < k; i++) A[i][i - 1] = 1;\n    A[k][k] = 1;\n    for (int i = 0; i < S; i++) R[i][i] = 1;\n"
    "    auto mul = [&](const vector<vector<long long>> &P, const vector<vector<long long>> &Q) {\n"
    "        vector<vector<long long>> T(S, vector<long long>(S, 0));\n"
    "        for (int i = 0; i < S; i++) for (int t = 0; t < S; t++) for (int j = 0; j < S; j++)\n"
    "            T[i][j] = (T[i][j] + (__int128)P[i][t] * Q[t][j]) % M;\n        return T;\n    };\n"
    "    for (long long e = n - (k - 1); e > 0; e >>= 1) {\n        if (e & 1) R = mul(R, A);\n        A = mul(A, A);\n    }\n"
    "    long long res = R[0][k] % M;\n    for (int j = 0; j < k; j++) res = (res + (__int128)R[0][j] * (f0[k - 1 - j] % M)) % M;\n"
    "    return res;\n}")
CPP_HELPERS["recur_exact"] = (
    "long long recur_exact(long long n, vector<long long> c, vector<long long> f, long long d) {\n"
    "    // f(i) = c[0]·f(i−1) + … + c[k−1]·f(i−k) + d, f(0..k−1) podane\n    int k = c.size();\n"
    "    while ((long long)f.size() <= n) {\n        long long x = d;\n        for (int j = 0; j < k; j++) x += c[j] * f[f.size() - 1 - j];\n"
    "        f.push_back(x);\n    }\n    return f[n];\n}")
_HELPER_ORDER += ["recur", "recur_exact"]
TAGS["recurrence"] = (r"rekurencj|rekurencyjn|zdefiniowan\w* (?:nast[eę]puj[aą]co|wzorem)|okre[sś]lon\w* (?:nast[eę]puj[aą]co|wzorem)"
                      r"|\b[a-z]_?\{?n\}?\s*=|\b[a-z]\s*\(\s*n\s*\)\s*=|wyraz\w* ci[aą]gu|liczb\w* \w+owe?j?\b.{0,40}(?:defini|okre[sś]l)")
_TAG_RX["recurrence"] = re.compile(TAGS["recurrence"])


def fam_recurrence(ctx: Ctx):
    """n → f(n) dla ciągu liniowo-rekurencyjnego rzędu 1–3 (małe współczynniki i wyrazy początkowe), także modulo
    stała z treści. Potęgowanie macierzy, więc działa i dla n ≈ 10^18."""
    ints = ctx.roles["ints"]
    if len(ints) != 1 or ctx.roles["arrays"] or ctx.roles["rows"] or ctx.roles["strs"]:
        return
    if _unit_exps(ctx, lambda u: 1) is None:
        return
    v = ints[0]
    mods = [c for c in ctx.consts if c > 1000][:2] + [None]
    specs = []
    for c1 in (1, 2, 3):
        for d in (0, 1, 2):
            if c1 == 1 and d == 0:
                continue
            for f0 in (0, 1, 2):
                specs.append(((c1,), (f0,), d))
    for c1 in range(4):
        for c2 in range(4):
            if c2 == 0:
                continue
            for d in (0, 1):
                for a0 in range(3):
                    for a1 in range(3):
                        specs.append(((c1, c2), (a0, a1), d))
    for c1 in range(3):
        for c2 in range(3):
            for c3 in (1, 2):
                for init in itertools.product((0, 1), repeat=3):
                    specs.append(((c1, c2, c3), init, 0))
    for M in mods:
        for c, f0, d in specs:
            for base in (0, 1):
                def run(u, c=c, f0=f0, d=d, M=M, base=base):
                    n = u[v] - base
                    return _recur(n, c, f0, d, M) if M else _recur_exact(n, c, f0, d)
                terms = " + ".join((f"{x}·f(i−{j + 1})" if x != 1 else f"f(i−{j + 1})") for j, x in enumerate(c) if x)
                desc = (f"f(i) = {terms}{f' + {d}' if d else ''}, f(0..{len(c) - 1}) = {', '.join(map(str, f0))}; "
                        f"wynik f({v}{' − 1' if base else ''}){f' mod {M}' if M else ''}")
                cv = "{" + ", ".join(map(str, c)) + "}"
                fv = "{" + ", ".join(map(str, f0)) + "}"
                arg = f"{v} - 1" if base else v
                tpl = (f"long long «r» = recur({arg}, {cv}, {fv}, {d}, {M}LL);" if M else
                       f"long long «r» = recur_exact({arg}, {cv}, {fv}, {d});")
                tags = {"recurrence"} | ({"mod"} if M else set()) | ({"fib"} if c == (1, 1) and d == 0 else set())
                cost = 2.4 + 0.6 * len(c) + 0.15 * (sum(c) + d) + 0.1 * sum(f0) + 0.2 * base + (0.0 if M else 0.3)
                yield _mk(f"rec:{c}:{f0}:{d}:{M}:{base}", desc, "recurrence", cost, tags, run, tpl, "int",
                          cx="1" if M else "n", work=lambda u: 60, helpers=("recur" if M else "recur_exact",))


# ---------------------------------------------------------------------------------------- symulacja struktur danych
# akcje: (nazwa, arność, drukuje?, opis, wzorzec słowa-klucza operacji)
_DS_ACTIONS = {
    "push": (1, False, "wstaw x", r"push|dodaj|wstaw|insert|add|wloz|po[lł][oó][zż]|in$"),
    "erase1": (1, False, "usuń jedno x", r"usun|erase|remove|del|wyjmij"),
    "has": (1, True, "czy x jest", r"czy|jest|find|szukaj|has|exist"),
    "cnt": (1, True, "ile razy x", r"ile|count|licz"),
    "pop": (0, False, "usuń szczyt", r"pop|usun|zdejmij|wyjmij|obs[lł]u[zż]|out$"),
    "popp": (0, True, "wypisz i usuń szczyt", r"pop|usun|zdejmij|wyjmij|obs[lł]u[zż]|out$"),
    "top": (0, True, "wypisz szczyt", r"top|front|szczyt|pierwsz|podejrzyj|peek|gora|przod"),
    "size": (0, True, "wypisz rozmiar", r"size|ile|rozmiar|liczb"),
    "empty": (0, True, "czy pusta", r"empty|pust"),
    "clear": (0, False, "wyczyść", r"clear|czy[sś][cć]|wyczy|reset"),
    "min": (0, True, "wypisz minimum", r"min"),
    "max": (0, True, "wypisz maksimum", r"max"),
}
_DS_KINDS = {
    "stack": ("stos", ("push",), ("pop", "popp", "top", "size", "empty", "clear"), ("stack",)),
    "queue": ("kolejka", ("push",), ("pop", "popp", "top", "size", "empty", "clear"), ("queue",)),
    "pqmax": ("kopiec (największy na szczycie)", ("push",), ("pop", "popp", "top", "size", "empty", "clear"), ("heap", "max")),
    "pqmin": ("kopiec (najmniejszy na szczycie)", ("push",), ("pop", "popp", "top", "size", "empty", "clear"), ("heap", "min")),
    "mset": ("multizbiór", ("push", "erase1", "has", "cnt"), ("min", "max", "size", "empty", "clear"), ("set",)),
    "set": ("zbiór", ("push", "erase1", "has"), ("min", "max", "size", "empty", "clear"), ("set", "distinct")),
}
TAGS.update({"stack": r"\bstos", "queue": r"kolejk(?!\w* priorytet)", "heap": r"kopiec|kolejk\w* priorytet",
             "set": r"\bzbi[oó]r|zbior\w*|multizbi"})
for _k in ("stack", "queue", "heap", "set"):
    _TAG_RX[_k] = re.compile(TAGS[_k])
CONCEPTS |= {"stack", "queue", "heap", "set"}


def _ds_run(kind, acts, ops):
    """Symulacja w Pythonie dokładnie jak w C++ (stack/queue/priority_queue/multiset/set)."""
    import heapq
    out = []
    S: list = []
    dq: deque = deque()
    ms: Counter = Counter()

    def size():
        return len(dq) if kind == "queue" else (sum(ms.values()) if kind in ("mset", "set") else len(S))

    def top():
        if kind == "stack":
            return S[-1]
        if kind == "queue":
            return dq[0]
        if kind == "pqmax":
            return -S[0][0] if isinstance(S[0][0], int) and S[0][1] == 0 else S[0][2]
        return S[0][2] if kind == "pqmin" else None

    for key, args in ops:
        a = acts[key]
        if a == "push":
            x = args[0]
            if kind == "stack":
                S.append(x)
            elif kind == "queue":
                dq.append(x)
            elif kind in ("pqmax", "pqmin"):
                if kind == "pqmax":
                    heapq.heappush(S, (-x, 0, x) if isinstance(x, int) else (_Rev(x), 1, x))
                else:
                    heapq.heappush(S, (x, 0, x))
            elif kind == "mset":
                ms[args[0]] += 1
            else:
                ms[args[0]] = 1
        elif a == "erase1":
            if ms.get(args[0]):
                ms[args[0]] -= 1
                if not ms[args[0]]:
                    del ms[args[0]]
        elif a == "has":
            out.append(bool(ms.get(args[0])))
        elif a == "cnt":
            out.append(ms.get(args[0], 0))
        elif a in ("pop", "popp", "top"):
            if size() == 0:
                if a != "pop":
                    out.append(NONE)
                continue
            if a != "pop":
                out.append(top())
            if a != "top":
                if kind == "stack":
                    S.pop()
                elif kind == "queue":
                    dq.popleft()
                else:
                    heapq.heappop(S)
        elif a == "size":
            out.append(size())
        elif a == "empty":
            out.append(size() == 0)
        elif a == "clear":
            S.clear()
            dq.clear()
            ms.clear()
        elif a in ("min", "max"):
            keys = [k for k, v in ms.items() if v > 0]
            out.append(NONE if not keys else (min(keys) if a == "min" else max(keys)))
    return out


class _Rev:
    """Odwrócony porządek napisów (kopiec „największy na szczycie” dla napisów)."""
    __slots__ = ("s",)

    def __init__(self, s):
        self.s = s

    def __lt__(self, o):
        return self.s > o.s

    def __eq__(self, o):
        return self.s == o.s


def fam_ds(ctx: Ctx):
    """Operacje na strukturze („push x”, „pop”, „ile”…): każde słowo-operacja dostaje akcję stosu, kolejki, kopca
    albo (multi)zbioru; wynik = to, co wypisują operacje. Kandydat musi odtworzyć CAŁE wyjście przykładów."""
    ops_items = ctx.roles["ops"]
    if len(ops_items) != 1 or ctx.mt:
        return
    it = ops_items[0]
    keys = sorted(it.ops)
    if len(keys) > 5 or any(len(it.ops[k]) > 1 for k in keys):
        return
    types = {it.ops[k][0] for k in keys if it.ops[k]}
    if len(types) > 1:
        return
    T_ = {"i": "long long", "s": "string", "f": "double"}.get(next(iter(types)) if types else "i", "long long")
    for kind, (kdesc, argacts, noargacts, ktags) in _DS_KINDS.items():
        choices = [argacts if it.ops[k] else noargacts for k in keys]
        for combo in itertools.product(*choices):
            if "push" not in combo:
                continue
            acts = dict(zip(keys, combo))
            if not any(_DS_ACTIONS[a][1] for a in combo):
                continue
            bonus = sum(0.5 for k, a in acts.items() if re.search(_DS_ACTIONS[a][3], T.strip_accents(k).lower()))
            desc = f"{kdesc}: " + ", ".join(f"„{k}” = {_DS_ACTIONS[a][2]}" for k, a in acts.items())
            yield _ds_cand(ctx, it, kind, acts, T_, desc, 3.0 + 0.4 * len(keys) - bonus, set(ktags))


def _ds_cand(ctx, it, kind, acts, T_, desc, cost, tags):
    name = it.name

    def run(u, acts=acts, kind=kind):
        return _ds_run(kind, acts, u[name])

    def cpp(G, acts=acts, kind=kind):
        S, op, x, qi = G.f("S"), G.f("op"), G.f("x"), G.f("qi")
        decl = {"stack": f"stack<{T_}> {S};", "queue": f"queue<{T_}> {S};", "pqmax": f"priority_queue<{T_}> {S};",
                "pqmin": f"priority_queue<{T_}, vector<{T_}>, greater<{T_}>> {S};", "mset": f"multiset<{T_}> {S};",
                "set": f"set<{T_}> {S};"}[kind]
        topx = {"stack": f"{S}.top()", "queue": f"{S}.front()", "pqmax": f"{S}.top()", "pqmin": f"{S}.top()"}.get(kind)
        lines = [decl, f"for (int {qi} = 0; {qi} < {it.count.cpp()}; {qi}++) {{", f"    string {op};", f"    cin >> {op};"]
        first = True
        for key, a in acts.items():
            lines.append(f"    {'if' if first else 'else if'} ({op} == {G.o.cpp_word(key)}) {{")
            first = False
            body = []
            if _DS_ACTIONS[a][0] == 1:
                body.append(f"{T_} {x};")
                body.append(f"cin >> {x};")
            if a == "push":
                body.append(f"{S}.{'insert' if kind in ('mset', 'set') else 'push'}({x});")
            elif a == "erase1":
                body.append(f"auto it_ = {S}.find({x});")
                body.append(f"if (it_ != {S}.end()) {S}.erase(it_);")
            elif a == "has":
                body.append(G.pr(f"{S}.count({x}) > 0", "bool"))
            elif a == "cnt":
                body.append(G.pr(f"(long long){S}.count({x})", "int"))
            elif a in ("pop", "popp", "top"):
                if a == "pop":
                    body.append(f"if (!{S}.empty()) {S}.pop();")
                else:
                    body.append(f"if ({S}.empty()) cout << {G.o.cpp_word(G.o.none)} << \"\\n\";")
                    body.append("else {")
                    body.append(f"    cout << {topx} << \"\\n\";")
                    if a == "popp":
                        body.append(f"    {S}.pop();")
                    body.append("}")
            elif a == "size":
                body.append(G.pr(f"(long long){S}.size()", "int"))
            elif a == "empty":
                body.append(G.pr(f"{S}.empty()", "bool"))
            elif a == "clear":
                body.append(f"{S} = decltype({S})();")
            elif a in ("min", "max"):
                body.append(f"if ({S}.empty()) cout << {G.o.cpp_word(G.o.none)} << \"\\n\";")
                body.append(f"else cout << {'*' + S + '.begin()' if a == 'min' else '*' + S + '.rbegin()'} << \"\\n\";")
            lines += ["        " + ln for b in body for ln in b.split("\n")]
            lines.append("    }")
        lines.append("}")
        return "\n".join(lines)
    return Cand(f"ds:{kind}:" + ",".join(f"{k}={a}" for k, a in acts.items()), desc, "simulation", cost,
                tags | {"query"}, run, cpp, cx="q log q", work=lambda u: len(u[name]) * 4 + 1)


FAMILIES.append(lambda ctx: fam_ds(ctx))


# ============================================================================================ kontrola cudzego kodu
CHALLENGE_MIN_SCORE = -3.5
CHALLENGE_MARGIN = 1.0


def challenge(parsed: dict, statement_text: str, code: str, *, words: dict | None = None, budget: float = 2.5,
              timeout: float = 2.0, lang: str | None = None) -> dict:
    """Ochrona przed przeuczeniem dla rozwiązań spoza syntezy (szablony, kod z podobnych zadań): czy inny
    WIARYGODNY program (zgodny ze słowami treści i ze wszystkimi przykładami) daje inne wyniki niż `code` na losowych
    małych testach? Wynik: {checked, tests, competitors[{desc, input, output, best_output, code}], agree[desc]}."""
    from . import runner
    import concurrent.futures as cf
    t0 = time.time()
    res = {"checked": False, "tests": 0, "competitors": [], "agree": []}
    try:
        base, ctx, matches, _raw = _search(parsed, statement_text, words, budget, lambda f, m: None, t0)
    except Exception:  # noqa: BLE001 — kontrola nigdy nie może wywrócić narzędzia
        return res
    if ctx is None or not ctx.rnd:
        return res
    plaus = [c for c in sorted(matches, key=lambda c: (-c.score, c.cost)) if c.aligned and c.score >= CHALLENGE_MIN_SCORE][:30]
    res["checked"] = True
    if not plaus:
        return res
    import os
    prog = runner.prepare(code, lang=lang or runner.detect_lang(code))
    if prog.ok and prog.lang == "cpp" and prog.cmd and not os.path.exists(prog.cmd[0]):
        prog = runner.prepare(code, lang="cpp")
    if not prog.ok:
        res["checked"] = False
        return res

    def one(txt):
        rr = runner.run(prog.cmd, txt, timeout=timeout * 2)
        return _toks(rr.stdout) if rr.verdict == "OK" and rr.stdout.strip() else None
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        outs = list(ex.map(one, [t for t, _ in ctx.rnd]))
    res["tests"] = sum(1 for o in outs if o is not None)
    judged = []
    for c in plaus:
        c.sig = signature(ctx, c)
        diff, overlap = None, False
        for i, (o, sg) in enumerate(zip(outs, c.sig)):
            if o is None or sg is None:
                continue
            if [ctx.out.none] in (o, list(sg)):
                continue                  # „brak odpowiedzi” na zdegenerowanym wejściu (np. n = 1) to słaby dowód różnicy
            overlap = True
            if not ctx.out.same(o, list(sg)):
                diff = i
                break
        if overlap:
            judged.append((c, diff))
    agree = [c for c, d in judged if d is None]
    res["agree"] = [c.desc for c in agree[:5]]
    floor = (max(c.score for c in agree) - CHALLENGE_MARGIN) if agree else CHALLENGE_MIN_SCORE
    need = max((c.nspec for c in agree), default=1)
    seen_sigs = []
    for c, diff in judged:
        if diff is None or c.score < floor or c.nspec < need or any(_differs(c.sig, s2) is None for s2 in seen_sigs):
            continue
        seen_sigs.append(c.sig)
        comp = {"desc": c.desc, "family": c.fam, "score": c.score, "input": ctx.rnd[diff][0],
                "output": " ".join(c.sig[diff])[:200], "best_output": " ".join(outs[diff])[:200], "code": None}
        try:
            comp["code"] = assemble(ctx, c, parsed.get("title"), ["UWAGA: to inny program zgodny z przykładami."])
        except Exception:  # noqa: BLE001
            pass
        res["competitors"].append(comp)
        if len(res["competitors"]) >= 3:
            break
    res["time"] = round(time.time() - t0, 2)
    return res

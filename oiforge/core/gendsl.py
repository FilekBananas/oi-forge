"""Mini-język generatorów testów (DSL) — interpreter + eksport do samodzielnego C++ i Pythona.

Uczeń nie musi pisać generatora w C++:

    tests 1000          # ile testów (oddzielonych pustą linią — format Testerki)
    n = int(1, 10)
    a = array(n, int(-5, 5))
    print n
    print a

Gramatyka (skrót):
    program   := { instrukcja (NL | ';') }
    instrukcja:= 'tests' wyr | 'seed' wyr                           (tylko na najwyższym poziomie, raz)
               | NAZWA ('=' | '+=' | '-=' | '*=' | '//=' | '%=') wyr
               | 'print' [wyr {',' wyr}] ['sep' '=' wyr] ['end' '=' wyr]
               | 'repeat' wyr blok | 'for' NAZWA 'in' wyr ['..' wyr] blok | 'while' wyr blok
               | 'if' wyr blok {('elif' | 'else' 'if') wyr blok} ['else' blok]
               | 'break' | 'continue' | 'assert' wyr [',' wyr]
    blok      := '{' { instrukcja } '}'
    wyr       := or ; or := and {('or'|'||') and} ; and := not {('and'|'&&') not} ; not := ('not'|'!') not | por
    por       := arytm {('=='|'!='|'<'|'<='|'>'|'>='|'in') arytm}       (łańcuchy jak w Pythonie: 1 <= x <= 5)
    arytm     := skł {('+'|'-') skł} ; skł := unarny {('*'|'/'|'//'|'%') unarny} ; unarny := ('-'|'+') unarny | potęga
    potęga    := postfiks ['**' unarny] ; postfiks := atom {'[' wyr ']'}
    atom      := LICZBA | NAPIS | NAZWA | NAZWA '(' argumenty ')' | '(' wyr [',' wyr ...] ')' | '[' lista ']' | true | false
Komentarze: `#` do końca linii. Liczby: 100, 1e5 (= 100000), 2e5, 100_000, 1.5. Napisy: "…" lub '…'.
Wbudowane zmienne: testno (numer testu od 1), ntests (liczba testów), size (rozmiar; domyślnie 10, ustawiany
przez benchmark i argv[3] eksportu).

Semantyka: liczby całkowite 64-bitowe (przepełnienie = błąd), `/` daje liczbę rzeczywistą, `//` i `%` jak w Pythonie
(zaokrąglenie w dół). Liczby rzeczywiste pamiętają liczbę cyfr po przecinku (float(1, 2, 3) → „1.234”).
Wartości są niemutowalne (brak `a[i] = …`; jest `put(a, i, v)`), więc eksport C++ ma tę samą semantykę.

Bezpieczeństwo: to interpreter (NIE eval Pythona) z limitem kroków, czasu i rozmiaru wyjścia.

Eksport: `export(source, "cpp" | "python")` — samodzielny program: `./gen <seed> [liczba_testów] [size]`.
Losowanie w eksporcie to MT19937 z inicjalizacją jak w `random.seed` Pythona i te same algorytmy co w
`oiforge.core.randstruct` → ten sam seed daje IDENTYCZNE testy jak podgląd w OI Forge.

Publiczne API:
    parse(source) -> Program            (rzuca DslError z linią i kolumną)
    check(source) -> {ok, errors, warnings, info}
    run(source, seed=None, tests=None, vars=None, preview=5, keep_output=True, ...) -> dict
    generate(source, seed=None, tests=None, vars=None) -> str          (całe wyjście)
    export(source, lang="cpp") -> str
    reference() -> [{name, sig, desc, example, cat}]   ;   FUNCTIONS, KEYWORDS
"""
from __future__ import annotations

import ast as _pyast
import copy
import difflib
import inspect
import math
import re
import time
from pathlib import Path

from .randstruct import (MAX_N, Rng, StructError, distinct_ints, expand_alphabet, palindrome, periodic_string,
                         permutation, random_bipartite, random_dag, random_forest, random_graph, random_grid,
                         random_intervals, random_points, random_queries, random_string, random_tree, sequence,
                         thue_morse, tree_parents)

INT_MIN, INT_MAX = -(1 << 63), (1 << 63) - 1
DEFAULT_SIZE = 10
MAX_STR = 10_000_000
PREVIEW_DEFAULT = 5


# ============================================================================================== błędy
class DslError(Exception):
    """Błąd składni albo wykonania z pozycją (linia/kolumna od 1)."""

    def __init__(self, message: str, line: int | None = None, col: int | None = None, *, test: int | None = None,
                 severity: str = "error"):
        self.message = message
        self.line = line
        self.col = col
        self.test = test
        self.severity = severity
        super().__init__(self.text())

    def text(self) -> str:
        pre = ""
        if self.test:
            pre += f"Test {self.test} — "
        if self.line:
            pre += f"linia {self.line}" + (f", kolumna {self.col}" if self.col else "") + ": "
        return (pre[:1].upper() + pre[1:] if pre else "") + self.message

    def to_dict(self) -> dict:
        return {"line": self.line, "col": self.col, "message": self.message, "severity": self.severity,
                "test": self.test, "text": self.text()}


def _suggest(name: str, options) -> str:
    m = difflib.get_close_matches(name, list(options), n=1, cutoff=0.6)
    return f" Czy chodziło o „{m[0]}”?" if m else ""


# ============================================================================================== lekser
KEYWORDS = ("tests", "seed", "print", "repeat", "for", "in", "if", "elif", "else", "while", "break", "continue",
            "assert", "and", "or", "not", "true", "false")
_KW = set(KEYWORDS)
_TOKEN_RE = re.compile(r"""
 (?P<ws>[ \t\r\f\v]+)
|(?P<comment>\#[^\n]*)
|(?P<nl>\n)
|(?P<float>\d[\d_]*\.\d[\d_]*(?:[eE][+-]?\d+)?|\d[\d_]*[eE]-\d+)
|(?P<int>\d[\d_]*(?:[eE]\+?\d+)?)
|(?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
|(?P<name>[A-Za-z_][A-Za-z0-9_]*)
|(?P<op>\*\*|//=|//|\.\.|==|!=|<=|>=|\+=|-=|\*=|%=|&&|\|\||[-+*/%<>=()\[\]{},;!])
""", re.X)
_ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'", "r": "\r", "0": "\0"}


class Tok:
    __slots__ = ("kind", "value", "line", "col", "text")

    def __init__(self, kind, value, line, col, text):
        self.kind, self.value, self.line, self.col, self.text = kind, value, line, col, text

    def __repr__(self):
        return f"Tok({self.kind},{self.text!r}@{self.line}:{self.col})"


def _unescape(body: str, line: int, col: int) -> str:
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            e = body[i + 1]
            if e not in _ESC:
                raise DslError(f"Nieznana sekwencja „\\{e}” w napisie (dozwolone: \\n \\t \\\\ \\\" \\').", line, col + i + 1)
            out.append(_ESC[e])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def tokenize(src: str) -> list[Tok]:
    src = src.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    toks: list[Tok] = []
    line, lstart, pos, depth = 1, 0, 0, 0
    n = len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        col = pos - lstart + 1
        if not m:
            ch = src[pos]
            if ch in "\"'":
                raise DslError("Niezamknięty napis — brakuje cudzysłowu na końcu (napis musi być w jednej linii).", line, col)
            if ch == ":":
                raise DslError("Nieoczekiwany „:” — bloki zapisuj w klamrach: if x > 0 { ... }, repeat n { ... }.", line, col)
            if ch == ".":
                raise DslError("Nieoczekiwana kropka. Zakres pisz jako 1..n (dwie kropki), liczbę jako 1.5.", line, col)
            if ch in "&|":
                raise DslError(f"Nieoczekiwany znak „{ch}” — użyj „and”/„or” (albo „&&”/„||”).", line, col)
            raise DslError(f"Nieoczekiwany znak „{ch}”.", line, col)
        kind = m.lastgroup
        text = m.group(kind)
        pos = m.end()
        if kind == "ws" or kind == "comment":
            continue
        if kind == "nl":
            if depth == 0:
                toks.append(Tok("nl", None, line, col, "\\n"))
            line += 1
            lstart = pos
            continue
        if kind == "int":
            t = text.replace("_", "").lower()
            if "e" in t:
                mant, ex = t.split("e")
                val = int(mant) * 10 ** int(ex.lstrip("+"))
            else:
                val = int(t)
            if val > INT_MAX + 1:
                raise DslError(f"Liczba {text} nie mieści się w 64 bitach (maks. 9223372036854775807).", line, col)
            toks.append(Tok("num", val, line, col, text))
        elif kind == "float":
            t = text.replace("_", "")
            digits = len(t.split(".")[1]) if "." in t and "e" not in t.lower() else 6
            toks.append(Tok("float", (float(t), max(0, min(12, digits))), line, col, text))
        elif kind == "str":
            toks.append(Tok("str", _unescape(text[1:-1], line, col), line, col, text))
        elif kind == "name":
            toks.append(Tok("kw" if text in _KW else "name", text, line, col, text))
        else:
            if text in ("(", "["):
                depth += 1
            elif text in (")", "]"):
                depth = max(0, depth - 1)
            toks.append(Tok("op", text, line, col, text))
    toks.append(Tok("nl", None, line, pos - lstart + 1, "\\n"))
    toks.append(Tok("eof", None, line, pos - lstart + 1, "koniec"))
    return toks


# ============================================================================================== AST
class Node:
    __slots__ = ("line", "col")


class Num(Node):
    __slots__ = ("value", "digits")

    def __init__(self, value, digits=None, line=0, col=0):
        self.value, self.digits, self.line, self.col = value, digits, line, col


class Str(Node):
    __slots__ = ("value",)

    def __init__(self, value, line=0, col=0):
        self.value, self.line, self.col = value, line, col


class Name(Node):
    __slots__ = ("id",)

    def __init__(self, id_, line=0, col=0):
        self.id, self.line, self.col = id_, line, col


class ListLit(Node):
    __slots__ = ("items",)

    def __init__(self, items, line=0, col=0):
        self.items, self.line, self.col = items, line, col


class BinOp(Node):
    __slots__ = ("op", "left", "right")

    def __init__(self, op, left, right, line=0, col=0):
        self.op, self.left, self.right, self.line, self.col = op, left, right, line, col


class Unary(Node):
    __slots__ = ("op", "operand")

    def __init__(self, op, operand, line=0, col=0):
        self.op, self.operand, self.line, self.col = op, operand, line, col


class Compare(Node):
    __slots__ = ("ops", "operands")

    def __init__(self, ops, operands, line=0, col=0):
        self.ops, self.operands, self.line, self.col = ops, operands, line, col


class BoolOp(Node):
    __slots__ = ("op", "left", "right")

    def __init__(self, op, left, right, line=0, col=0):
        self.op, self.left, self.right, self.line, self.col = op, left, right, line, col


class Not(Node):
    __slots__ = ("operand",)

    def __init__(self, operand, line=0, col=0):
        self.operand, self.line, self.col = operand, line, col


class Call(Node):
    __slots__ = ("name", "args", "kwargs", "bound", "varargs", "varkw", "last_explicit")

    def __init__(self, name, args, kwargs, line=0, col=0):
        self.name, self.args, self.kwargs, self.line, self.col = name, args, kwargs, line, col
        self.bound = None       # lista węzłów (z domyślnymi) dla zwykłych funkcji
        self.varargs = None     # dla funkcji zmiennoargumentowych (choice, min, max)
        self.varkw = None
        self.last_explicit = -1  # indeks ostatniego jawnie podanego parametru (eksport pomija resztę)


class Index(Node):
    __slots__ = ("obj", "index")

    def __init__(self, obj, index, line=0, col=0):
        self.obj, self.index, self.line, self.col = obj, index, line, col


class Assign(Node):
    __slots__ = ("name", "op", "expr")

    def __init__(self, name, op, expr, line=0, col=0):
        self.name, self.op, self.expr, self.line, self.col = name, op, expr, line, col


class Print(Node):
    __slots__ = ("items", "sep", "end")

    def __init__(self, items, sep, end, line=0, col=0):
        self.items, self.sep, self.end, self.line, self.col = items, sep, end, line, col


class Repeat(Node):
    __slots__ = ("count", "body")

    def __init__(self, count, body, line=0, col=0):
        self.count, self.body, self.line, self.col = count, body, line, col


class For(Node):
    __slots__ = ("var", "start", "end", "iterable", "body")

    def __init__(self, var, start, end, iterable, body, line=0, col=0):
        self.var, self.start, self.end, self.iterable, self.body = var, start, end, iterable, body
        self.line, self.col = line, col


class While(Node):
    __slots__ = ("cond", "body")

    def __init__(self, cond, body, line=0, col=0):
        self.cond, self.body, self.line, self.col = cond, body, line, col


class If(Node):
    __slots__ = ("branches", "orelse")

    def __init__(self, branches, orelse, line=0, col=0):
        self.branches, self.orelse, self.line, self.col = branches, orelse, line, col


class Break(Node):
    __slots__ = ()

    def __init__(self, line=0, col=0):
        self.line, self.col = line, col


class Continue(Node):
    __slots__ = ()

    def __init__(self, line=0, col=0):
        self.line, self.col = line, col


class Assert(Node):
    __slots__ = ("cond", "msg")

    def __init__(self, cond, msg, line=0, col=0):
        self.cond, self.msg, self.line, self.col = cond, msg, line, col


class Program:
    def __init__(self, body, tests=None, seed=None, source=""):
        self.body = body
        self.tests = tests          # węzeł wyrażenia albo None (=1)
        self.seed = seed            # int albo None
        self.source = source
        self.assigned: set = set()
        self.used_names: set = set()
        self.calls: set = set()
        self.warnings: list = []


# ============================================================================================== parser
_CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")
_AUG = ("+=", "-=", "*=", "//=", "%=")


class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks
        self.i = 0
        self.loop = 0

    # --- pomocnicze
    @property
    def cur(self) -> Tok:
        return self.t[self.i]

    def peek(self, k=1) -> Tok:
        return self.t[min(self.i + k, len(self.t) - 1)]

    def is_op(self, *ops) -> bool:
        c = self.cur
        return c.kind == "op" and c.value in ops

    def is_kw(self, *kws) -> bool:
        c = self.cur
        return c.kind == "kw" and c.value in kws

    def next(self) -> Tok:
        tok = self.t[self.i]
        if self.i < len(self.t) - 1:
            self.i += 1
        return tok

    def _desc(self, tok: Tok) -> str:
        if tok.kind == "eof":
            return "koniec programu"
        if tok.kind == "nl":
            return "koniec linii"
        return f"„{tok.text}”"

    def err(self, msg, tok=None):
        tok = tok or self.cur
        raise DslError(msg, tok.line, tok.col)

    def expect_op(self, op, what=None):
        if not self.is_op(op):
            self.err(f"Oczekiwano „{op}”{(' ' + what) if what else ''}, a jest {self._desc(self.cur)}.")
        return self.next()

    def skip_nl(self):
        while self.cur.kind == "nl" or self.is_op(";"):
            self.next()

    # --- program
    def program(self, source) -> Program:
        prog = Program([], source=source)
        self.skip_nl()
        while self.cur.kind != "eof":
            if self.is_op("}"):
                self.err("Nadmiarowa klamra „}” — nie zamyka żadnego bloku.")
            if self.is_kw("tests", "seed"):
                tok = self.next()
                e = self.expr()
                if tok.value == "tests":
                    if prog.tests is not None:
                        raise DslError("Instrukcja „tests” może wystąpić tylko raz.", tok.line, tok.col)
                    prog.tests = e
                else:
                    if prog.seed is not None:
                        raise DslError("Instrukcja „seed” może wystąpić tylko raz.", tok.line, tok.col)
                    prog.seed = _const_int(e, "seed")
                self.end_stmt()
            else:
                prog.body.append(self.statement())
            self.skip_nl()
        return prog

    def end_stmt(self):
        if self.cur.kind in ("nl", "eof") or self.is_op(";", "}"):
            if self.is_op(";"):
                self.next()
            return
        tok = self.cur
        hint = ""
        if tok.kind == "op" and tok.value == "=":
            hint = " (czy chodziło o „==” w warunku?)"
        elif tok.kind == "op" and tok.value == "{":
            hint = " — blok { } może być tylko po if/else/repeat/for/while."
        self.err(f"Nieoczekiwany {self._desc(tok)} — oczekiwano końca instrukcji (nowej linii albo „;”){hint}.")

    def block(self, after: str) -> list:
        if not self.is_op("{"):
            tok = self.cur
            hint = " (bloki zapisuj w klamrach, nie wcięciami)" if tok.kind == "nl" else ""
            self.err(f"Oczekiwano „{{” po {after}{hint}, a jest {self._desc(tok)}.")
        open_tok = self.next()
        body = []
        self.skip_nl()
        while not self.is_op("}"):
            if self.cur.kind == "eof":
                raise DslError(f"Brakuje „}}” zamykającej blok otwarty w linii {open_tok.line}.", open_tok.line,
                               open_tok.col)
            if self.is_kw("tests", "seed"):
                self.err(f"„{self.cur.value}” może wystąpić tylko na najwyższym poziomie programu (poza blokami).")
            body.append(self.statement())
            self.skip_nl()
        self.next()
        return body

    def statement(self):
        tok = self.cur
        if tok.kind == "kw":
            kw = tok.value
            if kw == "print":
                return self.print_stmt()
            if kw == "repeat":
                self.next()
                cnt = self.expr()
                self.loop += 1
                body = self.block("repeat")
                self.loop -= 1
                return Repeat(cnt, body, tok.line, tok.col)
            if kw == "for":
                return self.for_stmt()
            if kw == "while":
                self.next()
                cond = self.expr()
                self.loop += 1
                body = self.block("warunku while")
                self.loop -= 1
                return While(cond, body, tok.line, tok.col)
            if kw == "if":
                return self.if_stmt()
            if kw in ("break", "continue"):
                self.next()
                if not self.loop:
                    raise DslError(f"„{kw}” poza pętlą (repeat/for/while).", tok.line, tok.col)
                self.end_stmt()
                return Break(tok.line, tok.col) if kw == "break" else Continue(tok.line, tok.col)
            if kw == "assert":
                self.next()
                cond = self.expr()
                msg = None
                if self.is_op(","):
                    self.next()
                    msg = self.expr()
                self.end_stmt()
                return Assert(cond, msg, tok.line, tok.col)
            if kw in ("else", "elif"):
                self.err(f"„{kw}” bez poprzedzającego „if”.")
            self.err(f"Słowo „{kw}” nie może zaczynać instrukcji.")
        if tok.kind == "name":
            nx = self.peek()
            if nx.kind == "op" and (nx.value == "=" or nx.value in _AUG):
                self.next()
                op = self.next().value
                if self.is_op("="):
                    self.err("Podwójne „=” — przypisanie to jedno „=”, porównanie „==” (tylko w warunku).")
                e = self.expr()
                self.end_stmt()
                return Assign(tok.value, op, e, tok.line, tok.col)
            if nx.kind == "op" and nx.value == "[":
                save = self.i
                self.next()
                depth = 0
                while True:
                    c = self.cur
                    if c.kind in ("nl", "eof"):
                        break
                    if c.kind == "op" and c.value == "[":
                        depth += 1
                    elif c.kind == "op" and c.value == "]":
                        depth -= 1
                        if depth == 0:
                            self.next()
                            break
                    self.next()
                if self.is_op("=") or (self.cur.kind == "op" and self.cur.value in _AUG):
                    raise DslError("Wartości są niemutowalne — zamiast a[i] = x użyj a = put(a, i, x).", tok.line, tok.col)
                self.i = save
            if tok.value in ("int", "long", "double", "auto", "vector", "string") and nx.kind == "name":
                self.err(f"Tu nie deklaruje się typów — napisz po prostu „{nx.value} = …”.")
            if tok.value in ("def", "function", "fn", "func"):
                self.err("Ten mini-język nie ma własnych funkcji — użyj pętli i wbudowanych funkcji.")
        start = self.cur
        e = self.expr()
        if isinstance(e, Compare) and e.ops == ["=="] and isinstance(e.operands[0], Name):
            raise DslError("Porównanie „==” nic nie robi — przypisanie zapisuje się pojedynczym „=”.", start.line, start.col)
        if isinstance(e, Call):
            raise DslError(f"Wynik {e.name}(…) nie jest używany — przypisz go (x = {e.name}(…)) albo wypisz (print {e.name}(…)).",
                           start.line, start.col)
        raise DslError("To wyrażenie nic nie robi — przypisz je do zmiennej (x = …) albo wypisz (print …).",
                       start.line, start.col)

    def print_stmt(self):
        tok = self.next()
        items, sep, end = [], None, None
        while True:
            c = self.cur
            if c.kind in ("nl", "eof") or (c.kind == "op" and c.value in (";", "}")):
                break
            if c.kind == "name" and c.value in ("sep", "end") and self.peek().kind == "op" and self.peek().value == "=":
                self.next()
                self.next()
                v = self.expr()
                if c.value == "sep":
                    sep = v
                else:
                    end = v
                if self.is_op(","):
                    self.next()
                continue
            items.append(self.expr())
            if self.is_op(","):
                self.next()
                continue
            c = self.cur
            if not (c.kind in ("nl", "eof") or (c.kind == "op" and c.value in (";", "}"))
                    or (c.kind == "name" and c.value in ("sep", "end"))):
                self.err(f"Oczekiwano „,” między wartościami w print, a jest {self._desc(c)}.")
        self.end_stmt()
        return Print(items, sep, end, tok.line, tok.col)

    def for_stmt(self):
        tok = self.next()
        if self.is_op("("):
            self.err("Pętla w tym języku: for i in 1..n { … } (bez nawiasów jak w C++).")
        if self.cur.kind != "name":
            self.err(f"Po „for” oczekiwano nazwy zmiennej, a jest {self._desc(self.cur)}.")
        var = self.next().value
        if not self.is_kw("in"):
            self.err("Oczekiwano „in”: for i in 1..n { … }.")
        self.next()
        first = self.expr()
        start = end = it = None
        if self.is_op(".."):
            self.next()
            start, end = first, self.expr()
        else:
            it = first
        self.loop += 1
        body = self.block("nagłówku for")
        self.loop -= 1
        return For(var, start, end, it, body, tok.line, tok.col)

    def if_stmt(self):
        tok = self.next()
        branches = [(self.expr(), self.block("warunku if"))]
        orelse = None
        while True:
            save = self.i
            while self.cur.kind == "nl":
                self.next()
            if self.is_kw("elif"):
                self.next()
                branches.append((self.expr(), self.block("warunku elif")))
                continue
            if self.is_kw("else"):
                self.next()
                if self.is_kw("if"):
                    self.next()
                    branches.append((self.expr(), self.block("warunku else if")))
                    continue
                orelse = self.block("else")
                break
            self.i = save
            break
        return If(branches, orelse, tok.line, tok.col)

    # --- wyrażenia
    def expr(self):
        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.is_kw("or") or self.is_op("||"):
            tok = self.next()
            left = BoolOp("or", left, self.and_expr(), tok.line, tok.col)
        return left

    def and_expr(self):
        left = self.not_expr()
        while self.is_kw("and") or self.is_op("&&"):
            tok = self.next()
            left = BoolOp("and", left, self.not_expr(), tok.line, tok.col)
        return left

    def not_expr(self):
        if self.is_kw("not") or self.is_op("!"):
            tok = self.next()
            return Not(self.not_expr(), tok.line, tok.col)
        return self.comparison()

    def comparison(self):
        first = self.arith()
        ops, operands = [], [first]
        while (self.cur.kind == "op" and self.cur.value in _CMP_OPS) or self.is_kw("in") or \
                (self.is_kw("not") and self.peek().kind == "kw" and self.peek().value == "in"):
            if self.is_kw("not"):
                self.next()
                self.next()
                ops.append("not in")
            else:
                ops.append(self.next().value)
            operands.append(self.arith())
        if not ops:
            return first
        if self.is_op("="):
            self.err("W wyrażeniu nie można przypisywać — do porównania użyj „==”.")
        return Compare(ops, operands, first.line, first.col)

    def arith(self):
        left = self.term()
        while self.is_op("+", "-"):
            tok = self.next()
            left = BinOp(tok.value, left, self.term(), tok.line, tok.col)
        return left

    def term(self):
        left = self.unary()
        while self.is_op("*", "/", "//", "%"):
            tok = self.next()
            left = BinOp(tok.value, left, self.unary(), tok.line, tok.col)
        return left

    def unary(self):
        if self.is_op("-", "+"):
            tok = self.next()
            operand = self.unary()
            if tok.value == "+":
                return operand
            if isinstance(operand, Num):
                if operand.digits is None and -operand.value < INT_MIN:
                    raise DslError("Liczba poza zakresem 64 bitów.", tok.line, tok.col)
                return Num(-operand.value, operand.digits, tok.line, tok.col)
            return Unary("-", operand, tok.line, tok.col)
        return self.power()

    def power(self):
        base = self.postfix()
        if self.is_op("**"):
            tok = self.next()
            return BinOp("**", base, self.unary(), tok.line, tok.col)
        return base

    def postfix(self):
        e = self.atom()
        while self.is_op("["):
            tok = self.next()
            idx = self.expr()
            self.expect_op("]", "zamykające indeks")
            e = Index(e, idx, tok.line, tok.col)
        if self.is_op("(") and not isinstance(e, Name):
            self.err("Wywoływać można tylko funkcje wbudowane po nazwie, np. int(1, 10).")
        return e

    def atom(self):
        tok = self.cur
        if tok.kind == "num":
            self.next()
            if tok.value > INT_MAX:
                raise DslError("Liczba poza zakresem 64 bitów (maks. 9223372036854775807).", tok.line, tok.col)
            return Num(tok.value, None, tok.line, tok.col)
        if tok.kind == "float":
            self.next()
            return Num(tok.value[0], tok.value[1], tok.line, tok.col)
        if tok.kind == "str":
            self.next()
            return Str(tok.value, tok.line, tok.col)
        if tok.kind == "kw" and tok.value in ("true", "false"):
            self.next()
            return Num(1 if tok.value == "true" else 0, None, tok.line, tok.col)
        if tok.kind == "name":
            self.next()
            if self.is_op("("):
                return self.call(tok)
            return Name(tok.value, tok.line, tok.col)
        if tok.kind == "op" and tok.value == "(":
            self.next()
            if self.is_op(")"):
                self.err("Puste nawiasy () — wpisz wyrażenie.")
            first = self.expr()
            if self.is_op(","):
                items = [first]
                while self.is_op(","):
                    self.next()
                    if self.is_op(")"):
                        break
                    items.append(self.expr())
                self.expect_op(")", "zamykające nawias")
                return ListLit(items, tok.line, tok.col)
            if not self.is_op(")") and (self.cur.line > tok.line or self.cur.kind == "eof"):
                raise DslError(f"Brakuje „)” — nawias otwarty w linii {tok.line} nie został zamknięty.", tok.line, tok.col)
            self.expect_op(")", "zamykające nawias")
            return first
        if tok.kind == "op" and tok.value == "[":
            self.next()
            items = []
            while not self.is_op("]"):
                items.append(self.expr())
                if self.is_op(","):
                    self.next()
                    continue
                if not self.is_op("]"):
                    if self.cur.line > tok.line or self.cur.kind == "eof":
                        raise DslError(f"Brakuje „]” — lista otwarta w linii {tok.line} nie została zamknięta.",
                                       tok.line, tok.col)
                    self.err(f"Oczekiwano „,” albo „]” w liście, a jest {self._desc(self.cur)}.")
            self.next()
            return ListLit(items, tok.line, tok.col)
        if tok.kind == "kw":
            self.err(f"Słowo kluczowe „{tok.value}” nie może być użyte jako wartość.")
        if tok.kind in ("nl", "eof"):
            self.err("Niedokończone wyrażenie — czegoś brakuje na końcu linii.")
        self.err(f"Nieoczekiwany {self._desc(tok)} w wyrażeniu.")

    def call(self, name_tok):
        self.next()   # (
        args, kwargs = [], {}
        while not self.is_op(")"):
            c = self.cur
            if c.kind == "name" and self.peek().kind == "op" and self.peek().value == "=":
                self.next()
                self.next()
                if c.value in kwargs:
                    raise DslError(f"Parametr „{c.value}” podany dwa razy.", c.line, c.col)
                kwargs[c.value] = self.expr()
                kwargs[c.value].line = kwargs[c.value].line or c.line
            else:
                if kwargs:
                    self.err("Argumenty pozycyjne muszą być przed nazwanymi (np. graph(n, m, directed=1)).")
                args.append(self.expr())
            if self.is_op(","):
                self.next()
                continue
            if not self.is_op(")"):
                c = self.cur
                if c.line > name_tok.line or c.kind == "eof":
                    raise DslError(f"Brakuje „)” zamykającego wywołanie {name_tok.value}(…) — nawias otwarty w linii "
                                   f"{name_tok.line} nie został zamknięty.", name_tok.line, name_tok.col)
                self.err(f"Oczekiwano „,” albo „)” w wywołaniu {name_tok.value}(…), a jest {self._desc(c)}.")
        self.next()
        return Call(name_tok.value, args, kwargs, name_tok.line, name_tok.col)


def _const_int(e, what):
    if isinstance(e, Num) and e.digits is None:
        return e.value
    raise DslError(f"„{what}” wymaga stałej liczby całkowitej (np. {what} 42).", e.line, e.col)


# ============================================================================================== wbudowane funkcje
FUNCTIONS = (
    "int", "float", "chance", "choice", "char", "string", "palindrome", "periodic", "antihash",
    "array", "matrix", "distinct", "perm", "seq", "range",
    "sorted", "reverse", "shuffle", "unique", "len", "sum", "min", "max", "count", "put", "flat", "rows", "lines",
    "join", "str", "abs", "floor", "ceil", "round", "sqrt", "gcd", "lcm",
    "tree", "parents", "forest", "graph", "dag", "bipartite", "grid", "pairs", "interval", "points", "queries",
)
# funkcje losujące (kolejność ich wywołań ma znaczenie; reszta jest „czysta”)
RANDOM_FUNCS = {"int", "float", "chance", "choice", "char", "string", "palindrome", "periodic", "array", "matrix",
                "distinct", "perm", "seq", "shuffle", "tree", "parents", "forest", "graph", "dag", "bipartite",
                "grid", "pairs", "interval", "points", "queries"}
LAZY_PARAMS = {"gen"}
PREDEFINED = ("testno", "ntests", "size")

REFERENCE = [
    # --- instrukcje
    {"cat": "Instrukcje", "name": "tests", "sig": "tests N", "desc": "Liczba testów (domyślnie 1). Testy są oddzielane PUSTĄ LINIĄ — format Testerki. Stała albo wyrażenie z `size`.", "example": "tests 1000"},
    {"cat": "Instrukcje", "name": "seed", "sig": "seed S", "desc": "Domyślne ziarno losowania — te same testy przy każdym uruchomieniu (eksport bierze ziarno z argv[1]).", "example": "seed 42"},
    {"cat": "Instrukcje", "name": "=", "sig": "x = wyrażenie", "desc": "Przypisanie. Są też `+=`, `-=`, `*=`, `//=`, `%=`. Zmienne nie mają typów.", "example": "n = int(1, 10)\nm = n * (n - 1) // 2"},
    {"cat": "Instrukcje", "name": "print", "sig": "print a, b, … sep=\" \" end=\"\\n\"", "desc": "Wypisuje wartości w jednej linii (listy są rozwijane). Lista list, drzewo, graf, siatka — jeden element w linii. Pusta lista nic nie wypisuje (np. drzewo o 1 wierzchołku). `sep` — separator, `end` — zakończenie linii.", "example": "print n, m\nprint a sep=\",\"\nprint g"},
    {"cat": "Instrukcje", "name": "repeat", "sig": "repeat k { … }", "desc": "Powtarza blok k razy.", "example": "repeat q {\n    print int(1, n), int(1, n)\n}"},
    {"cat": "Instrukcje", "name": "for", "sig": "for i in a..b { … }", "desc": "Pętla po liczbach od a do b WŁĄCZNIE; `for x in lista { … }` — po elementach listy (albo znakach napisu).", "example": "for i in 1..n {\n    print i, int(1, i)\n}"},
    {"cat": "Instrukcje", "name": "if", "sig": "if w { … } elif w { … } else { … }", "desc": "Instrukcja warunkowa (`else if` też działa).", "example": "if chance(0.5) {\n    print \"TAK\"\n} else {\n    print \"NIE\"\n}"},
    {"cat": "Instrukcje", "name": "while", "sig": "while w { … }", "desc": "Pętla z warunkiem; `break` i `continue` działają jak w C++.", "example": "x = 1\nwhile x < n {\n    x = x * 2\n}"},
    {"cat": "Instrukcje", "name": "assert", "sig": "assert w, \"komunikat\"", "desc": "Przerywa generowanie z błędem, jeśli warunek nie jest spełniony (kontrola poprawności generatora).", "example": "assert m <= n * (n - 1) // 2, \"za dużo krawędzi\""},
    {"cat": "Instrukcje", "name": "testno", "sig": "testno, ntests, size", "desc": "Wbudowane zmienne: numer bieżącego testu (od 1), liczba testów, rozmiar `size` (benchmark / argv[3] eksportu; domyślnie 10). `true` = 1, `false` = 0.", "example": "n = min(testno, 100)    # rosnące rozmiary"},
    {"cat": "Instrukcje", "name": "operatory", "sig": "+ - * / // % **  == != < <= > >=  and or not  in  a[i]", "desc": "`/` daje liczbę rzeczywistą, `//` dzielenie całkowite w dół, `**` potęga. Liczby całkowite są 64-bitowe. `+` łączy też napisy i listy, `*` powiela. `a[-1]` — ostatni element.", "example": "m = n * (n - 1) // 2\nok = 1 <= x and x <= n"},
    # --- liczby
    {"cat": "Liczby", "name": "int", "sig": "int(lo, hi)", "desc": "Losowa liczba całkowita z przedziału [lo, hi] (włącznie).", "example": "n = int(1, 2e5)"},
    {"cat": "Liczby", "name": "float", "sig": "float(lo, hi, digits=2)", "desc": "Losowa liczba rzeczywista z [lo, hi] z `digits` cyframi po przecinku (tak też jest wypisywana).", "example": "x = float(-1, 1, 3)"},
    {"cat": "Liczby", "name": "chance", "sig": "chance(p)", "desc": "1 z prawdopodobieństwem p, w przeciwnym razie 0.", "example": "if chance(0.1) { print -1 }"},
    {"cat": "Liczby", "name": "choice", "sig": "choice(a, b, …, weights=(…))", "desc": "Losowy z podanych argumentów albo losowy element listy/znak napisu. `weights` — wagi (liczby).", "example": "op = choice(\"ADD\", \"DEL\", weights=(3, 1))"},
    # --- napisy
    {"cat": "Napisy", "name": "char", "sig": "char(alphabet=\"a-z\")", "desc": "Losowy znak. Alfabet: zakresy „a-z0-9” albo nazwy lower, upper, letters, digits, alnum, binary, dna, hex.", "example": "c = char(\"ab\")"},
    {"cat": "Napisy", "name": "string", "sig": "string(n, alphabet=\"a-z\")", "desc": "Losowy napis długości n.", "example": "s = string(int(1, 10), \"abc\")"},
    {"cat": "Napisy", "name": "palindrome", "sig": "palindrome(n, alphabet=\"a-z\")", "desc": "Losowy palindrom długości n.", "example": "print palindrome(7, \"ab\")"},
    {"cat": "Napisy", "name": "periodic", "sig": "periodic(n, period, alphabet=\"a-z\")", "desc": "Napis długości n z losowym wzorcem długości `period` powtórzonym wielokrotnie.", "example": "print periodic(12, 3)"},
    {"cat": "Napisy", "name": "antihash", "sig": "antihash(n, alphabet=\"ab\")", "desc": "Słowo Thue–Morse’a — łamie haszowanie modulo 2^64 (dla n ≥ 2048).", "example": "print antihash(4096)"},
    {"cat": "Napisy", "name": "join", "sig": "join(lista, sep=\"\")", "desc": "Skleja elementy listy w napis.", "example": "s = join(array(5, char(\"01\")))"},
    {"cat": "Napisy", "name": "str", "sig": "str(x)", "desc": "Zamienia wartość na napis (tak, jak wypisałby ją print).", "example": "s = str(n) + \"!\""},
    # --- tablice
    {"cat": "Tablice", "name": "array", "sig": "array(n, gen)", "desc": "Lista n wartości; `gen` jest obliczane OSOBNO dla każdego elementu.", "example": "a = array(n, int(-1e9, 1e9))"},
    {"cat": "Tablice", "name": "matrix", "sig": "matrix(r, c, gen)", "desc": "Macierz r×c (wypisywana wierszami).", "example": "print matrix(3, 4, int(0, 9))"},
    {"cat": "Tablice", "name": "distinct", "sig": "distinct(n, lo, hi)", "desc": "n RÓŻNYCH liczb z [lo, hi] w losowej kolejności.", "example": "a = distinct(n, 1, 1e9)"},
    {"cat": "Tablice", "name": "perm", "sig": "perm(n, base=1)", "desc": "Losowa permutacja liczb 1..n (base=0 → 0..n−1).", "example": "p = perm(n)"},
    {"cat": "Tablice", "name": "seq", "sig": "seq(n, lo, hi, kind=\"uniform\")", "desc": "Ciąg o rozkładzie: uniform, sorted, desc, nearly (prawie posortowany), repeats (dużo powtórzeń), equal, zigzag, distinct.", "example": "a = seq(n, 1, 100, \"nearly\")"},
    {"cat": "Tablice", "name": "range", "sig": "range(lo, hi)", "desc": "Lista lo, lo+1, …, hi (włącznie).", "example": "a = shuffle(range(1, n))"},
    # --- listy
    {"cat": "Listy", "name": "sorted", "sig": "sorted(x, desc=0)", "desc": "Posortowana kopia (desc=1 — malejąco).", "example": "a = sorted(array(n, int(1, 100)))"},
    {"cat": "Listy", "name": "reverse", "sig": "reverse(x)", "desc": "Odwrócona kopia listy albo napisu.", "example": "b = reverse(a)"},
    {"cat": "Listy", "name": "shuffle", "sig": "shuffle(x)", "desc": "Losowo przetasowana kopia.", "example": "a = shuffle(a)"},
    {"cat": "Listy", "name": "unique", "sig": "unique(x)", "desc": "Kopia bez powtórzeń (zostaje pierwsze wystąpienie).", "example": "u = unique(a)"},
    {"cat": "Listy", "name": "len", "sig": "len(x)", "desc": "Długość listy albo napisu.", "example": "print len(a)"},
    {"cat": "Listy", "name": "sum", "sig": "sum(x)", "desc": "Suma elementów listy.", "example": "print sum(a)"},
    {"cat": "Listy", "name": "min", "sig": "min(a, b, …) / min(lista)", "desc": "Najmniejsza wartość (max — największa).", "example": "n = min(testno, 50)"},
    {"cat": "Listy", "name": "max", "sig": "max(a, b, …) / max(lista)", "desc": "Największa wartość.", "example": "print max(a)"},
    {"cat": "Listy", "name": "count", "sig": "count(x, v)", "desc": "Ile razy v występuje w liście (albo podnapis w napisie).", "example": "k = count(a, 0)"},
    {"cat": "Listy", "name": "put", "sig": "put(x, i, v)", "desc": "Kopia listy z elementem i zamienionym na v (indeks od 0, ujemny od końca).", "example": "a = put(a, 0, 1e9)"},
    {"cat": "Listy", "name": "flat", "sig": "flat(x)", "desc": "Spłaszcza listę list o jeden poziom.", "example": "print flat(tree(5))"},
    {"cat": "Listy", "name": "rows", "sig": "rows(x)  /  lines(x)", "desc": "Lista wypisywana po jednym elemencie w linii.", "example": "print rows(array(3, string(4)))"},
    # --- matematyka
    {"cat": "Matematyka", "name": "abs", "sig": "abs(x)", "desc": "Wartość bezwzględna.", "example": "d = abs(a - b)"},
    {"cat": "Matematyka", "name": "floor", "sig": "floor(x) / ceil(x) / round(x)", "desc": "Zaokrąglenia do liczby całkowitej (round: do najbliższej, połówki do parzystej).", "example": "k = floor(sqrt(n))"},
    {"cat": "Matematyka", "name": "ceil", "sig": "ceil(x)", "desc": "Zaokrąglenie w górę.", "example": "k = ceil(n / 3)"},
    {"cat": "Matematyka", "name": "round", "sig": "round(x)", "desc": "Zaokrąglenie do najbliższej liczby całkowitej.", "example": "k = round(n / 3)"},
    {"cat": "Matematyka", "name": "sqrt", "sig": "sqrt(x)", "desc": "Pierwiastek (liczba rzeczywista).", "example": "k = floor(sqrt(n))"},
    {"cat": "Matematyka", "name": "gcd", "sig": "gcd(a, b) / lcm(a, b)", "desc": "NWD i NWW.", "example": "g = gcd(a, b)"},
    {"cat": "Matematyka", "name": "lcm", "sig": "lcm(a, b)", "desc": "Najmniejsza wspólna wielokrotność.", "example": "l = lcm(a, b)"},
    # --- drzewa
    {"cat": "Drzewa", "name": "tree", "sig": "tree(n, shape=\"random\", weights=0, base=1, shuffle=1)", "desc": "Losowe drzewo: n−1 krawędzi „u v” (z wagą „u v w”, gdy weights=(lo, hi)). Kształty: random (Prüfer), parent (płytkie), line (ścieżka — głęboka rekurencja!), star, binary, caterpillar, deep.", "example": "print n\nprint tree(n, \"line\")"},
    {"cat": "Drzewa", "name": "parents", "sig": "parents(n, shape=\"random\", base=1, shuffle=1)", "desc": "Drzewo jako tablica rodziców p2 … pn (pi < i, korzeń 1) — jedna linia.", "example": "print n\nprint parents(n)"},
    {"cat": "Drzewa", "name": "forest", "sig": "forest(n, k, weights=0, base=1)", "desc": "Las k drzew (n−k krawędzi, dokładnie k spójnych składowych).", "example": "print forest(n, 3)"},
    # --- grafy
    {"cat": "Grafy", "name": "graph", "sig": "graph(n, m, connected=1, directed=0, weights=0, loops=0, multi=0, base=1)", "desc": "Losowy graf z m krawędziami. Domyślnie spójny, nieskierowany, bez pętli i multikrawędzi. weights=(1, 100) — wagi.", "example": "print n, m\nprint graph(n, m, weights=(1, 1e9))"},
    {"cat": "Grafy", "name": "dag", "sig": "dag(n, m, connected=0, weights=0, base=1)", "desc": "Skierowany graf acykliczny (krawędzie zgodne z losowym porządkiem topologicznym).", "example": "print dag(n, m)"},
    {"cat": "Grafy", "name": "bipartite", "sig": "bipartite(n1, n2, m, weights=0, base=1, shift=0)", "desc": "Graf dwudzielny: lewa strona 1..n1, prawa 1..n2 (shift=1 → n1+1..n1+n2).", "example": "print bipartite(5, 4, 8)"},
    # --- siatki
    {"cat": "Siatki", "name": "grid", "sig": "grid(rows, cols, alphabet=\"#.\", p=-1, path=0)", "desc": "Plansza znaków (wiersz w linii). p — prawdopodobieństwo pierwszego znaku (np. ściany „#”), path=1 — gwarantowana ścieżka z lewego górnego do prawego dolnego rogu z ostatniego znaku.", "example": "print r, c\nprint grid(r, c, \"#.\", p=0.3, path=1)"},
    # --- przedziały
    {"cat": "Przedziały i zapytania", "name": "pairs", "sig": "pairs(k, lo, hi, distinct=0)", "desc": "k przedziałów „l r” (lo ≤ l ≤ r ≤ hi), każdy w osobnej linii; distinct=1 → l < r.", "example": "print pairs(q, 1, n)"},
    {"cat": "Przedziały i zapytania", "name": "interval", "sig": "interval(lo, hi)", "desc": "Jeden przedział [l, r] (lista dwóch liczb).", "example": "print 2, interval(1, n)"},
    {"cat": "Przedziały i zapytania", "name": "points", "sig": "points(k, lo, hi, distinct=1)", "desc": "k punktów „x y” (domyślnie różnych).", "example": "print points(n, -1e9, 1e9)"},
    {"cat": "Przedziały i zapytania", "name": "queries", "sig": "queries(q, n, kinds=\"range point_value\", lo=1, hi=1e9)", "desc": "q zapytań „typ argumenty”; typ = numer rodzaju (od 1). Rodzaje: point (i), range (l r), value (x), point_value (i x), range_value (l r x), pair (u v), none.", "example": "print queries(q, n, \"point_value range\")"},
]


def reference() -> list[dict]:
    return [dict(r) for r in REFERENCE]


# ============================================================================================== RUNTIME-BEGIN
# Ta sekcja (do RUNTIME-END) jest kopiowana do eksportu Pythona (z drzewem zależności z randstruct).
# Każda funkcja ma odpowiednik w CPP_RUNTIME — zmiany wprowadzaj w obu miejscach.
class GenError(Exception):
    """Błąd wykonania generatora (komunikat po polsku)."""


def _tname(x):
    t = type(x)
    if t is int or t is bool:
        return "liczba całkowita"
    if isinstance(x, float):
        return "liczba rzeczywista"
    if t is str:
        return "napis"
    if isinstance(x, list):
        return "lista"
    return "brak wartości" if x is None else t.__name__


def _isnum(x):
    t = type(x)
    return t is int or t is DFloat or t is float or t is bool


def _digits(x):
    t = type(x)
    if t is DFloat:
        return x.d
    if t is float:
        return 6
    return -1


def _mkf(v, a, b):
    if type(v) is complex:
        raise GenError("Potęga liczby ujemnej z ułamkowym wykładnikiem nie jest liczbą rzeczywistą.")
    d = max(_digits(a), _digits(b))
    return DFloat(v, d if d >= 0 else 6)


class DFloat(float):
    """Liczba rzeczywista pamiętająca liczbę cyfr po przecinku (do wypisywania)."""
    __slots__ = ("d",)

    def __new__(cls, v, d=6):
        o = float.__new__(cls, v)
        o.d = d
        return o

    def __add__(self, o):
        return _mkf(float(self) + float(o), self, o) if _isnum(o) else NotImplemented

    def __radd__(self, o):
        return _mkf(float(o) + float(self), o, self) if _isnum(o) else NotImplemented

    def __sub__(self, o):
        return _mkf(float(self) - float(o), self, o) if _isnum(o) else NotImplemented

    def __rsub__(self, o):
        return _mkf(float(o) - float(self), o, self) if _isnum(o) else NotImplemented

    def __mul__(self, o):
        return _mkf(float(self) * float(o), self, o) if _isnum(o) else NotImplemented

    def __rmul__(self, o):
        return _mkf(float(o) * float(self), o, self) if _isnum(o) else NotImplemented

    def __truediv__(self, o):
        return _mkf(float(self) / float(o), self, o) if _isnum(o) else NotImplemented

    def __rtruediv__(self, o):
        return _mkf(float(o) / float(self), o, self) if _isnum(o) else NotImplemented

    def __floordiv__(self, o):
        return _mkf(float(self) // float(o), self, o) if _isnum(o) else NotImplemented

    def __rfloordiv__(self, o):
        return _mkf(float(o) // float(self), o, self) if _isnum(o) else NotImplemented

    def __mod__(self, o):
        return _mkf(float(self) % float(o), self, o) if _isnum(o) else NotImplemented

    def __rmod__(self, o):
        return _mkf(float(o) % float(self), o, self) if _isnum(o) else NotImplemented

    def __pow__(self, o):
        return _mkf(float(self) ** float(o), self, o) if _isnum(o) else NotImplemented

    def __rpow__(self, o):
        return _mkf(float(o) ** float(self), o, self) if _isnum(o) else NotImplemented

    def __neg__(self):
        return DFloat(-float(self), self.d)

    def __pos__(self):
        return self

    def __abs__(self):
        return DFloat(abs(float(self)), self.d)

    def __repr__(self):
        return "%.*f" % (self.d, self)

    __str__ = __repr__


class Rows(list):
    """Lista wypisywana po jednym elemencie w linii (krawędzie, siatki, zapytania)."""
    __slots__ = ()


def _hkey(x):
    if isinstance(x, list):
        return tuple([_hkey(e) for e in x])
    return x


class Gen:
    """Środowisko wykonania generatora: losowanie (Rng), wyjście, limity i wbudowane funkcje DSL."""

    def __init__(self, seed=0, max_steps=0, max_output=0, time_limit=0.0):
        self.R = Rng(seed)
        self.buf = []
        self.total = 0
        self.steps = 0
        self.max_steps = max_steps or (1 << 62)
        self.max_output = max_output or (1 << 62)
        self.deadline = time.perf_counter() + time_limit if time_limit else 0.0
        self._tick = 1 << 14

    # ------------------------------------------------------------------ limity
    def cost(self, k=1):
        self.steps += k
        if self.steps > self.max_steps:
            raise GenError(f"Przekroczono limit {self.max_steps} kroków — generator robi za dużo albo ma "
                           "nieskończoną pętlę. Zmniejsz rozmiary lub liczbę testów.")
        if self.deadline and self.steps >= self._tick:
            self._tick = self.steps + (1 << 14)
            if time.perf_counter() > self.deadline:
                raise GenError("Przekroczono limit czasu generowania — zmniejsz rozmiary lub liczbę testów.")

    # ------------------------------------------------------------------ typy
    def _int(self, x, what):
        t = type(x)
        if t is int:
            return x
        if t is bool:
            return int(x)
        hint = " (wynik „/” jest rzeczywisty — użyj „//”)" if isinstance(x, float) else ""
        raise GenError(f"{what}: oczekiwano liczby całkowitej, a jest {_tname(x)}{hint}.")

    def _num(self, x, what):
        if _isnum(x):
            return x
        raise GenError(f"{what}: oczekiwano liczby, a jest {_tname(x)}.")

    def _str(self, x, what):
        if type(x) is str:
            return x
        raise GenError(f"{what}: oczekiwano napisu, a jest {_tname(x)}.")

    def _list(self, x, what):
        if isinstance(x, list):
            return x
        raise GenError(f"{what}: oczekiwano listy, a jest {_tname(x)}.")

    def _size(self, n, what):
        n = self._int(n, what)
        if n < 0:
            raise GenError(f"{what}: rozmiar nie może być ujemny (jest {n}).")
        if n > MAX_N:
            raise GenError(f"{what}: rozmiar {n} jest za duży (limit {MAX_N}).")
        return n

    def _chk(self, v):
        if type(v) is int and not (-(1 << 63) <= v < (1 << 63)):
            raise GenError("Przepełnienie: wynik nie mieści się w 64 bitach (long long).")
        return v

    def _flag(self, x):
        return bool(x)

    # ------------------------------------------------------------------ wyjście
    def fmt(self, x, sep=" "):
        t = type(x)
        if t is int:
            return str(x)
        if t is str:
            return x
        if t is DFloat:
            return "%.*f" % (x.d, x)
        if t is float:
            return "%.6f" % x
        if t is bool:
            return "1" if x else "0"
        if t is Rows or t is list:
            if t is Rows or (x and all([type(e) is list or type(e) is Rows for e in x])):
                return "\n".join([self.fmt(e, sep) for e in x])
            return sep.join([self.fmt(e, sep) for e in x])
        raise GenError("Nie można wypisać wartości (zmienna bez wartości?).")

    def print(self, items, sep=" ", end="\n"):
        sep = self._str(sep, "print … sep=")
        end = self._str(end, "print … end=")
        parts = [self.fmt(x, sep) for x in items if not (isinstance(x, list) and not x)]
        if items and not parts:
            return
        s = sep.join(parts) + end
        self.total += len(s)
        if self.total > self.max_output:
            raise GenError(f"Wyjście przekroczyło limit {self.max_output // 1000000} MB — zmniejsz testy.")
        self.cost(1 + len(s) // 32)
        self.buf.append(s)

    def end_test(self):
        s = "".join(self.buf)
        self.buf.clear()
        if s and not s.endswith("\n"):
            s += "\n"
            self.total += 1
        return s

    # ------------------------------------------------------------------ pętle i pomocnicze (eksport)
    def times(self, k):
        k = self._int(k, "repeat")
        if k < 0:
            raise GenError(f"repeat: liczba powtórzeń nie może być ujemna (jest {k}).")
        return range(k)

    def span(self, a, b):
        return range(self._int(a, "for … in a..b"), self._int(b, "for … in a..b") + 1)

    def items(self, x):
        if type(x) is str:
            return list(x)
        return list(self._list(x, "for … in"))

    def fail(self, msg=None):
        raise GenError("Asercja nie jest spełniona" + (": " + self.fmt(msg) if msg is not None else "."))

    def div(self, a, b):
        self._num(a, "/")
        self._num(b, "/")
        if b == 0:
            raise GenError("Dzielenie przez zero.")
        d = max(_digits(a), _digits(b))
        return DFloat(a / b, d if d >= 0 else 6)

    # ------------------------------------------------------------------ liczby
    def int(self, lo, hi):
        lo = self._int(lo, "int(lo, hi)")
        hi = self._int(hi, "int(lo, hi)")
        if lo > hi:
            raise GenError(f"int({lo}, {hi}): dolna granica jest większa od górnej.")
        if hi - lo >= 1 << 63:
            raise GenError("int(lo, hi): zakres jest za szeroki (najwyżej 2^63 liczb).")
        return self.R.randint(lo, hi)

    def float(self, lo, hi, digits=2):
        lo = self._num(lo, "float(lo, hi)")
        hi = self._num(hi, "float(lo, hi)")
        digits = self._int(digits, "float(…, digits)")
        if digits < 0 or digits > 12:
            raise GenError("float(…, digits): liczba cyfr musi być z zakresu 0..12.")
        scale = 10 ** digits
        if abs(float(lo)) * scale > 9007199254740992.0 or abs(float(hi)) * scale > 9007199254740992.0:
            raise GenError("float(lo, hi, digits): za duży zakres przy tylu cyfrach po przecinku.")
        a = math.ceil(float(lo) * scale - 1e-9)
        b = math.floor(float(hi) * scale + 1e-9)
        if a > b:
            raise GenError(f"float({lo}, {hi}, {digits}): pusty przedział.")
        return DFloat(self.R.randint(a, b) / scale, digits)

    def chance(self, p):
        p = self._num(p, "chance(p)")
        return 1 if self.R.random() < float(p) else 0

    def choice(self, *items, weights=0):
        if len(items) == 1 and isinstance(items[0], list):
            items = list(items[0])
        elif len(items) == 1 and type(items[0]) is str:
            items = list(items[0])
        if not items:
            raise GenError("choice(): brak elementów do wyboru.")
        if not (type(weights) is int and weights == 0):
            w = self._list(weights, "choice(…, weights=)")
            if len(w) != len(items):
                raise GenError(f"choice(): {len(items)} elementów, a {len(w)} wag.")
            allint = True
            for x in w:
                self._num(x, "choice(…, weights=)")
                if x < 0:
                    raise GenError("choice(): wagi nie mogą być ujemne.")
                if type(x) is not int:
                    allint = False
            if allint:
                tot = 0
                for x in w:
                    tot += x
                if tot <= 0:
                    raise GenError("choice(): suma wag musi być dodatnia.")
                r = self.R.below(tot)
                acc = 0
                for k in range(len(w)):
                    acc += w[k]
                    if r < acc:
                        return items[k]
            tot = 0.0
            for x in w:
                tot += float(x)
            if tot <= 0:
                raise GenError("choice(): suma wag musi być dodatnia.")
            r = self.R.random() * tot
            acc = 0.0
            for k in range(len(w)):
                acc += float(w[k])
                if r < acc:
                    return items[k]
            return items[-1]
        return items[self.R.below(len(items))]

    # ------------------------------------------------------------------ napisy
    def char(self, alphabet="a-z"):
        a = expand_alphabet(self._str(alphabet, "char(alphabet)"))
        return a[self.R.below(len(a))]

    def string(self, n, alphabet="a-z"):
        n = self._size(n, "string(n)")
        self.cost(n // 8 + 1)
        return random_string(n, self._str(alphabet, "string(…, alphabet)"), self.R)

    def palindrome(self, n, alphabet="a-z"):
        n = self._size(n, "palindrome(n)")
        self.cost(n // 8 + 1)
        return palindrome(n, self._str(alphabet, "palindrome(…, alphabet)"), self.R)

    def periodic(self, n, period, alphabet="a-z"):
        n = self._size(n, "periodic(n)")
        period = self._int(period, "periodic(…, period)")
        if period < 1:
            raise GenError("periodic(): okres musi być ≥ 1.")
        self.cost(n // 8 + 1)
        return periodic_string(n, period, self._str(alphabet, "periodic(…, alphabet)"), self.R)

    def antihash(self, n, alphabet="ab"):
        n = self._size(n, "antihash(n)")
        self.cost(n // 8 + 1)
        return thue_morse(n, self._str(alphabet, "antihash(…, alphabet)"))

    def join(self, x, sep=""):
        sep = self._str(sep, "join(…, sep)")
        if type(x) is str:
            return sep.join(list(x))
        x = self._list(x, "join(x)")
        self.cost(len(x) // 8 + 1)
        return sep.join([self.fmt(e, " ") for e in x])

    def str(self, x):
        return self.fmt(x, " ")

    # ------------------------------------------------------------------ tablice
    def array(self, n, gen):
        n = self._size(n, "array(n, …)")
        self.cost(n)
        return [gen() for _ in range(n)]

    def matrix(self, rows, cols, gen):
        rows = self._size(rows, "matrix(r, …)")
        cols = self._size(cols, "matrix(…, c, …)")
        if rows * cols > MAX_N:
            raise GenError("matrix(): za duża macierz.")
        self.cost(rows * cols + rows)
        return Rows([[gen() for _ in range(cols)] for _ in range(rows)])

    def distinct(self, n, lo, hi):
        n = self._size(n, "distinct(n, …)")
        lo = self._int(lo, "distinct(…, lo, hi)")
        hi = self._int(hi, "distinct(…, lo, hi)")
        self.cost(n + 1)
        return distinct_ints(n, lo, hi, self.R)

    def perm(self, n, base=1):
        n = self._size(n, "perm(n)")
        self.cost(n + 1)
        return permutation(n, self.R, base=self._int(base, "perm(…, base)"))

    def seq(self, n, lo, hi, kind="uniform"):
        n = self._size(n, "seq(n, …)")
        lo = self._int(lo, "seq(…, lo, hi)")
        hi = self._int(hi, "seq(…, lo, hi)")
        self.cost(n + 1)
        return sequence(n, lo, hi, self._str(kind, "seq(…, kind)"), self.R)

    def range(self, lo, hi):
        lo = self._int(lo, "range(lo, hi)")
        hi = self._int(hi, "range(lo, hi)")
        if hi - lo + 1 > MAX_N:
            raise GenError("range(): za długi zakres.")
        self.cost(max(0, hi - lo + 1) + 1)
        return list(range(lo, hi + 1))

    # ------------------------------------------------------------------ listy
    def sorted(self, x, desc=0):
        x = self._list(x, "sorted(x)")
        self.cost(len(x) + 1)
        try:
            r = sorted(x, reverse=bool(desc))
        except TypeError:
            raise GenError("sorted(): lista zawiera wartości, których nie da się porównać (np. liczby i napisy).") from None
        return Rows(r) if type(x) is Rows else r

    def reverse(self, x):
        if type(x) is str:
            return x[::-1]
        x = self._list(x, "reverse(x)")
        self.cost(len(x) // 4 + 1)
        return Rows(x[::-1]) if type(x) is Rows else x[::-1]

    def shuffle(self, x):
        if type(x) is str:
            c = list(x)
            self.R.shuffle(c)
            return "".join(c)
        x = self._list(x, "shuffle(x)")
        self.cost(len(x) + 1)
        c = list(x)
        self.R.shuffle(c)
        return Rows(c) if type(x) is Rows else c

    def unique(self, x):
        if type(x) is str:
            return "".join(self.unique(list(x)))
        x = self._list(x, "unique(x)")
        self.cost(len(x) + 1)
        seen = set()
        out = []
        for e in x:
            k = _hkey(e)
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
        return Rows(out) if type(x) is Rows else out

    def len(self, x):
        if type(x) is str or isinstance(x, list):
            return len(x)
        raise GenError(f"len(x): oczekiwano listy albo napisu, a jest {_tname(x)}.")

    def sum(self, x):
        x = self._list(x, "sum(x)")
        self.cost(len(x) // 4 + 1)
        acc = 0
        for e in x:
            self._num(e, "sum(x)")
            acc = self._chk(acc + e)
        return acc

    def _minmax(self, items, what, sign):
        if len(items) == 1 and isinstance(items[0], list):
            items = items[0]
        if not items:
            raise GenError(f"{what}(): pusta lista.")
        self.cost(len(items) // 4 + 1)
        best = items[0]
        try:
            for e in items[1:]:
                if (e < best) if sign < 0 else (e > best):
                    best = e
        except TypeError:
            raise GenError(f"{what}(): nie można porównać {_tname(best)} i {_tname(e)}.") from None
        return best

    def min(self, *items):
        return self._minmax(items, "min", -1)

    def max(self, *items):
        return self._minmax(items, "max", 1)

    def count(self, x, v):
        if type(x) is str:
            v = self._str(v, "count(napis, v)")
            if not v:
                raise GenError("count(): szukany napis nie może być pusty.")
            return x.count(v)
        x = self._list(x, "count(x, v)")
        self.cost(len(x) // 4 + 1)
        c = 0
        for e in x:
            if e == v:
                c += 1
        return c

    def put(self, x, i, v):
        i = self._int(i, "put(x, i, v)")
        if type(x) is str:
            n = len(x)
            if i < 0:
                i += n
            if not 0 <= i < n:
                raise GenError(f"put(): indeks {i} poza napisem długości {n}.")
            return x[:i] + self._str(v, "put(napis, i, v)") + x[i + 1:]
        x = self._list(x, "put(x, i, v)")
        n = len(x)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise GenError(f"put(): indeks {i} poza listą długości {n}.")
        self.cost(n // 8 + 1)
        c = list(x)
        c[i] = v
        return Rows(c) if type(x) is Rows else c

    def flat(self, x):
        x = self._list(x, "flat(x)")
        out = []
        for e in x:
            if isinstance(e, list):
                out.extend(e)
            else:
                out.append(e)
        self.cost(len(out) // 4 + 1)
        return out

    def rows(self, x):
        if type(x) is str:
            return Rows(list(x))
        return Rows(self._list(x, "rows(x)"))

    def lines(self, x):
        return self.rows(x)

    # ------------------------------------------------------------------ matematyka
    def abs(self, x):
        self._num(x, "abs(x)")
        return self._chk(abs(x)) if type(x) is int else abs(x)

    def floor(self, x):
        self._num(x, "floor(x)")
        return x if type(x) is int else self._chk(math.floor(x))

    def ceil(self, x):
        self._num(x, "ceil(x)")
        return x if type(x) is int else self._chk(math.ceil(x))

    def round(self, x):
        self._num(x, "round(x)")
        return x if type(x) is int else self._chk(round(float(x)))

    def sqrt(self, x):
        self._num(x, "sqrt(x)")
        if x < 0:
            raise GenError("sqrt(x): pierwiastek z liczby ujemnej.")
        return DFloat(math.sqrt(float(x)), 6)

    def gcd(self, a, b):
        return math.gcd(self._int(a, "gcd(a, b)"), self._int(b, "gcd(a, b)"))

    def lcm(self, a, b):
        return self._chk(math.lcm(self._int(a, "lcm(a, b)"), self._int(b, "lcm(a, b)")))

    # ------------------------------------------------------------------ drzewa i grafy
    def _rows(self, edges):
        return Rows([list(e) for e in edges])

    def tree(self, n, shape="random", weights=0, base=1, shuffle=1):
        n = self._size(n, "tree(n)")
        self.cost(2 * n + 1)
        return self._rows(random_tree(n, self._str(shape, "tree(…, shape)"), self.R, base=self._int(base, "base"),
                                      shuffle=self._flag(shuffle), weights=weights))

    def parents(self, n, shape="random", base=1, shuffle=1):
        n = self._size(n, "parents(n)")
        base = self._int(base, "base")
        self.cost(2 * n + 1)
        par = tree_parents(n, self._str(shape, "parents(…, shape)"), self.R, shuffle=self._flag(shuffle))
        return [p + base for p in par[1:]]

    def forest(self, n, k, weights=0, base=1):
        n = self._size(n, "forest(n, k)")
        self.cost(2 * n + 1)
        return self._rows(random_forest(n, self._int(k, "forest(n, k)"), self.R, base=self._int(base, "base"),
                                        weights=weights))

    def graph(self, n, m, connected=1, directed=0, weights=0, loops=0, multi=0, base=1):
        n = self._size(n, "graph(n, m)")
        m = self._size(m, "graph(n, m)")
        self.cost(2 * (n + m) + 1)
        return self._rows(random_graph(n, m, self.R, connected=self._flag(connected), directed=self._flag(directed),
                                       loops=self._flag(loops), multi=self._flag(multi), base=self._int(base, "base"),
                                       weights=weights))

    def dag(self, n, m, connected=0, weights=0, base=1):
        n = self._size(n, "dag(n, m)")
        m = self._size(m, "dag(n, m)")
        self.cost(2 * (n + m) + 1)
        return self._rows(random_dag(n, m, self.R, connected=self._flag(connected), base=self._int(base, "base"),
                                     weights=weights))

    def bipartite(self, n1, n2, m, weights=0, base=1, shift=0):
        n1 = self._size(n1, "bipartite(n1, …)")
        n2 = self._size(n2, "bipartite(…, n2, …)")
        m = self._size(m, "bipartite(…, m)")
        self.cost(2 * m + 1)
        return self._rows(random_bipartite(n1, n2, m, self.R, base=self._int(base, "base"), shift=self._flag(shift),
                                           weights=weights))

    def grid(self, rows, cols, alphabet="#.", p=-1, path=0):
        rows = self._size(rows, "grid(rows, …)")
        cols = self._size(cols, "grid(…, cols)")
        p = self._num(p, "grid(…, p)")
        self.cost(rows * cols + 1)
        return Rows(random_grid(rows, cols, self.R, alphabet=self._str(alphabet, "grid(…, alphabet)"),
                                p=None if p < 0 else float(p), path=self._flag(path)))

    def pairs(self, k, lo, hi, distinct=0):
        k = self._size(k, "pairs(k, …)")
        self.cost(k + 1)
        return self._rows(random_intervals(k, self._int(lo, "pairs(…, lo, hi)"), self._int(hi, "pairs(…, lo, hi)"),
                                           self.R, distinct=self._flag(distinct)))

    def interval(self, lo, hi):
        return list(random_intervals(1, self._int(lo, "interval(lo, hi)"), self._int(hi, "interval(lo, hi)"),
                                     self.R)[0])

    def points(self, k, lo, hi, distinct=1):
        k = self._size(k, "points(k, …)")
        self.cost(k + 1)
        return self._rows(random_points(k, self._int(lo, "points(…, lo, hi)"), self._int(hi, "points(…, lo, hi)"),
                                        self.R, distinct=self._flag(distinct)))

    def queries(self, q, n, kinds="range point_value", lo=1, hi=1000000000):
        q = self._size(q, "queries(q, …)")
        n = self._int(n, "queries(…, n)")
        if isinstance(kinds, list):
            kinds = " ".join([self._str(k, "queries(…, kinds)") for k in kinds])
        self.cost(q + 1)
        return self._rows(random_queries(q, n, self._str(kinds, "queries(…, kinds)"), self.R,
                                         lo=self._int(lo, "queries(…, lo)"), hi=self._int(hi, "queries(…, hi)")))
# ============================================================================================== RUNTIME-END


def _signature(name):
    """(parametry, domyślne, variadic, kwonly) funkcji DSL na podstawie metody Gen."""
    sig = inspect.signature(getattr(Gen, name))
    params, defaults, var, kwonly = [], {}, False, {}
    for p in list(sig.parameters.values())[1:]:
        if p.kind is p.VAR_POSITIONAL:
            var = True
        elif p.kind is p.KEYWORD_ONLY:
            kwonly[p.name] = p.default
        else:
            params.append(p.name)
            if p.default is not p.empty:
                defaults[p.name] = p.default
    return params, defaults, var, kwonly


SIGS = {f: _signature(f) for f in FUNCTIONS}


def _default_node(v, line, col):
    if isinstance(v, str):
        return Str(v, line, col)
    return Num(v, None, line, col)


# ============================================================================================== analiza
def _walk_expr(e, fn):
    fn(e)
    t = type(e)
    if t is BinOp or t is BoolOp:
        _walk_expr(e.left, fn)
        _walk_expr(e.right, fn)
    elif t is Unary or t is Not:
        _walk_expr(e.operand, fn)
    elif t is Compare:
        for o in e.operands:
            _walk_expr(o, fn)
    elif t is ListLit:
        for o in e.items:
            _walk_expr(o, fn)
    elif t is Index:
        _walk_expr(e.obj, fn)
        _walk_expr(e.index, fn)
    elif t is Call:
        for a in e.args:
            _walk_expr(a, fn)
        for a in e.kwargs.values():
            _walk_expr(a, fn)


def is_pure(e) -> bool:
    """Czy wyrażenie nie losuje (kolejność obliczania nie ma wtedy znaczenia)."""
    found = []
    _walk_expr(e, lambda x: found.append(1) if type(x) is Call and x.name in RANDOM_FUNCS else None)
    return not found


def _stmt_exprs(s):
    t = type(s)
    if t is Assign:
        return [s.expr]
    if t is Print:
        return list(s.items) + [x for x in (s.sep, s.end) if x is not None]
    if t is Repeat:
        return [s.count]
    if t is For:
        return [x for x in (s.start, s.end, s.iterable) if x is not None]
    if t is While:
        return [s.cond]
    if t is If:
        return [c for c, _ in s.branches]
    if t is Assert:
        return [x for x in (s.cond, s.msg) if x is not None]
    return []


def _stmt_blocks(s):
    t = type(s)
    if t in (Repeat, For, While):
        return [s.body]
    if t is If:
        return [b for _, b in s.branches] + ([s.orelse] if s.orelse is not None else [])
    return []


def walk_statements(body, fn):
    for s in body:
        fn(s)
        for b in _stmt_blocks(s):
            walk_statements(b, fn)


def _bind(call: Call):
    name = call.name
    if name not in SIGS:
        raise DslError(f"Nieznana funkcja „{name}”.{_suggest(name, FUNCTIONS)}", call.line, call.col)
    params, defaults, var, kwonly = SIGS[name]
    sig_txt = next((r["sig"] for r in REFERENCE if r["name"] == name), name + "(…)")
    if var:
        if not call.args:
            raise DslError(f"{name}() potrzebuje co najmniej jednego argumentu: {sig_txt}.", call.line, call.col)
        for k in call.kwargs:
            if k not in kwonly:
                raise DslError(f"Nieznany parametr „{k}” funkcji {name}().{_suggest(k, kwonly)}", call.line, call.col)
        call.varargs = list(call.args)
        call.varkw = dict(call.kwargs)
        return
    if len(call.args) > len(params):
        raise DslError(f"Za dużo argumentów: {name}() przyjmuje najwyżej {len(params)} — {sig_txt}.", call.line, call.col)
    bound = {}
    for p, a in zip(params, call.args):
        bound[p] = a
    for k, a in call.kwargs.items():
        if k not in params:
            avail = ", ".join(p for p in params)
            raise DslError(f"Nieznany parametr „{k}” funkcji {name}() (dostępne: {avail}).{_suggest(k, params)}",
                           a.line or call.line, a.col or call.col)
        if k in bound:
            raise DslError(f"Parametr „{k}” funkcji {name}() podany dwa razy.", call.line, call.col)
        bound[k] = a
    out = []
    for p in params:
        if p in bound:
            out.append(bound[p])
        elif p in defaults:
            out.append(_default_node(defaults[p], call.line, call.col))
        else:
            hint = ""
            if name == "range" and len(call.args) == 1:
                hint = " W pętli użyj for i in 1..n { … }."
            elif name == "int" and len(call.args) == 1:
                hint = " Liczbę z zakresu losuje int(lo, hi); część całkowitą daje floor(x)."
            raise DslError(f"Brakuje argumentu „{p}” w {name}(): {sig_txt}.{hint}", call.line, call.col)
    call.bound = out
    explicit = [i for i, p in enumerate(params) if p in bound]
    call.last_explicit = max(explicit) if explicit else -1


def analyze(prog: Program) -> Program:
    """Wiązanie argumentów, nieznane nazwy, ostrzeżenia. Rzuca DslError przy pierwszym błędzie."""
    assigned = set(PREDEFINED)
    walk_statements(prog.body, lambda s: assigned.add(s.name) if type(s) is Assign else
                    (assigned.add(s.var) if type(s) is For else None))
    prog.assigned = assigned
    errors: list[DslError] = []
    used = set()
    calls = set()

    def on_expr(e):
        t = type(e)
        if t is Name:
            used.add(e.id)
            if e.id not in assigned:
                extra = ""
                if e.id in FUNCTIONS:
                    extra = f" „{e.id}” to funkcja — dopisz nawiasy, np. {e.id}(…)."
                elif e.id in ("line", "star", "binary", "caterpillar", "random", "deep", "parent", "sorted",
                              "uniform", "nearly", "desc", "repeats", "zigzag", "equal"):
                    extra = f" Nazwę rodzaju podaj w cudzysłowie: \"{e.id}\"."
                elif e.id in ("n", "m", "k", "q", "t"):
                    extra = f" Najpierw przypisz wartość, np. {e.id} = int(1, 10)."
                errors.append(DslError(f"Nieznana zmienna „{e.id}”.{extra or _suggest(e.id, assigned | set(FUNCTIONS))}",
                                       e.line, e.col))
        elif t is Call:
            calls.add(e.name)
            try:
                _bind(e)
            except DslError as ex:
                errors.append(ex)

    def on_stmt(s):
        for e in _stmt_exprs(s):
            _walk_expr(e, on_expr)
        if type(s) is Print:
            for x in (s.sep, s.end):
                if x is not None and not is_pure(x):
                    errors.append(DslError("sep= i end= nie mogą losować.", x.line, x.col))

    walk_statements(prog.body, on_stmt)
    if prog.tests is not None:
        def tests_names(e):
            if type(e) is Name and e.id != "size":
                errors.append(DslError("Liczba testów może zależeć tylko od stałych i zmiennej size.", e.line, e.col))
            elif type(e) is Call:
                try:
                    _bind(e)
                except DslError as ex:
                    errors.append(ex)
        _walk_expr(prog.tests, tests_names)
        if not is_pure(prog.tests):
            errors.append(DslError("Liczba testów nie może być losowa — wpisz stałą (np. tests 100).",
                                   prog.tests.line, prog.tests.col))
    if errors:
        errors.sort(key=lambda e: (e.line or 0, e.col or 0))
        err = errors[0]
        err.all = errors
        raise err
    prog.used_names = used
    prog.calls = calls
    # ostrzeżenia
    warns = []
    has_print = []
    walk_statements(prog.body, lambda s: has_print.append(s) if type(s) is Print else None)
    if not has_print:
        warns.append({"line": None, "message": "Program nic nie wypisuje — dodaj instrukcję print."})
    for s in has_print:
        if not s.items and (s.end is None or (type(s.end) is Str and "\n" in s.end.value)):
            warns.append({"line": s.line, "message": "Pusty print wypisze pustą linię — Testerka potraktuje ją jak "
                                                     "separator i podzieli test na dwa."})
        if type(s.sep) is Str and "\n" in s.sep.value:
            pass
    prog.warnings = warns
    return prog


_PARSE_CACHE: dict = {}


def parse(source: str) -> Program:
    """Parsuje i analizuje program DSL. Rzuca DslError (z .all = lista błędów)."""
    source = source or ""
    key = source
    hit = _PARSE_CACHE.get(key)
    if hit is not None:
        return hit
    toks = tokenize(source)
    prog = Parser(toks).program(source)
    analyze(prog)
    if len(_PARSE_CACHE) > 64:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[key] = prog
    return prog


def check(source: str) -> dict:
    """Sprawdzenie składni i analiza (bez uruchamiania)."""
    try:
        prog = parse(source)
    except DslError as e:
        errs = getattr(e, "all", None) or [e]
        return {"ok": True, "valid": False, "errors": [x.to_dict() for x in errs[:20]], "warnings": [], "info": {}}
    tests = None
    if prog.tests is not None:
        try:
            tests = _eval_tests(prog, DEFAULT_SIZE)
        except DslError:
            tests = None
    info = {"tests": tests if prog.tests is not None else 1, "tests_from_size": prog.tests is not None and "size" in _names_in(prog.tests),
            "seed": prog.seed, "uses_size": "size" in prog.used_names, "uses_testno": "testno" in prog.used_names,
            "variables": sorted(prog.assigned - set(PREDEFINED)), "functions": sorted(prog.calls)}
    return {"ok": True, "valid": True, "errors": [], "warnings": prog.warnings, "info": info}


def _names_in(e) -> set:
    out = set()
    _walk_expr(e, lambda x: out.add(x.id) if type(x) is Name else None)
    return out


# ============================================================================================== interpreter
class _Break(Exception):
    pass


class _Continue(Exception):
    pass


_RANGE_ERR = "Przepełnienie: wynik nie mieści się w 64 bitach (long long)."


def _arith(G, sym, a, b):
    ta, tb = type(a), type(b)
    if ta is int and tb is int:
        if sym == "+":
            r = a + b
        elif sym == "-":
            r = a - b
        elif sym == "*":
            r = a * b
        elif sym == "/":
            return G.div(a, b)
        elif sym == "//":
            if b == 0:
                raise GenError("Dzielenie całkowite przez zero.")
            r = a // b
        elif sym == "%":
            if b == 0:
                raise GenError("Reszta z dzielenia przez zero.")
            r = a % b
        else:   # **
            if b < 0:
                if a == 0:
                    raise GenError("0 do potęgi ujemnej.")
                return DFloat(a ** b, 6)
            if abs(a) > 1 and b > 64:
                raise GenError(_RANGE_ERR)
            r = a ** b
        if not (INT_MIN <= r <= INT_MAX):
            raise GenError(_RANGE_ERR)
        return r
    if _isnum(a) and _isnum(b):
        if sym == "/":
            return G.div(a, b)
        if sym in ("//", "%") and b == 0:
            raise GenError("Dzielenie przez zero.")
        try:
            if sym == "+":
                return a + b
            if sym == "-":
                return a - b
            if sym == "*":
                return a * b
            if sym == "//":
                return a // b
            if sym == "%":
                return a % b
            if float(a) == 0 and b < 0:
                raise GenError("0 do potęgi ujemnej.")
            return a ** b
        except OverflowError:
            raise GenError("Wynik jest za duży (przepełnienie liczby rzeczywistej).") from None
    if sym == "+":
        if ta is str and tb is str:
            if len(a) + len(b) > MAX_STR:
                raise GenError("Napis jest za długi.")
            G.cost((len(a) + len(b)) // 64 + 1)
            return a + b
        if isinstance(a, list) and isinstance(b, list):
            if len(a) + len(b) > MAX_N:
                raise GenError("Lista jest za długa.")
            G.cost((len(a) + len(b)) // 8 + 1)
            return list(a) + list(b)
    if sym == "*":
        seq_, k = (a, b) if tb is int and (ta is str or isinstance(a, list)) else \
            ((b, a) if ta is int and (tb is str or isinstance(b, list)) else (None, None))
        if seq_ is not None:
            if len(seq_) * max(k, 0) > (MAX_STR if type(seq_) is str else MAX_N):
                raise GenError("Wynik powielania jest za długi.")
            G.cost(len(seq_) * max(k, 0) // 8 + 1)
            return seq_ * k if type(seq_) is str else list(seq_) * k
    names = {"+": "dodać", "-": "odjąć", "*": "pomnożyć", "/": "podzielić", "//": "podzielić", "%": "policzyć reszty",
             "**": "potęgować"}
    raise GenError(f"Nie można {names.get(sym, 'wykonać ' + sym)}: {_tname(a)} {sym} {_tname(b)}.")


def _compare(sym, a, b):
    if sym == "==":
        return a == b
    if sym == "!=":
        return a != b
    if sym in ("in", "not in"):
        if isinstance(b, list):
            r = any(e == a for e in b)
        elif type(b) is str:
            if type(a) is not str:
                raise GenError(f"„in” z napisem wymaga napisu po lewej stronie, a jest {_tname(a)}.")
            r = a in b
        else:
            raise GenError(f"„in” wymaga listy albo napisu po prawej stronie, a jest {_tname(b)}.")
        return r if sym == "in" else not r
    ok = (_isnum(a) and _isnum(b)) or (type(a) is str and type(b) is str) or (isinstance(a, list) and isinstance(b, list))
    if not ok:
        raise GenError(f"Nie można porównać: {_tname(a)} {sym} {_tname(b)}.")
    try:
        if sym == "<":
            return a < b
        if sym == "<=":
            return a <= b
        if sym == ">":
            return a > b
        return a >= b
    except TypeError:
        raise GenError("Nie można porównać list o niezgodnych elementach.") from None


def _index(a, i):
    if type(i) is not int:
        raise GenError(f"Indeks musi być liczbą całkowitą, a jest {_tname(i)}.")
    if type(a) is str or isinstance(a, list):
        n = len(a)
        k = i + n if i < 0 else i
        if not 0 <= k < n:
            raise GenError(f"Indeks {i} poza zakresem (długość {n}; indeksy od 0 do {n - 1}).")
        return a[k]
    raise GenError(f"Indeksować można listę albo napis, a nie {_tname(a)}.")


def _wrap(fn, node, what=None):
    """Zamienia błędy wykonania na DslError z pozycją węzła."""
    line, col = node.line, node.col

    def f(env):
        try:
            return fn(env)
        except (DslError, _Break, _Continue):
            raise
        except (GenError, StructError) as e:
            raise DslError(str(e), line, col) from None
        except RecursionError:
            raise DslError("Za głęboko zagnieżdżone wyrażenie.", line, col) from None
        except (TypeError, ValueError, OverflowError, ZeroDivisionError, IndexError, MemoryError) as e:
            raise DslError(f"Błąd{(' w ' + what) if what else ''}: {e}", line, col) from None
    return f


class Interpreter:
    """Kompiluje AST do domknięć Pythona (szybciej niż zwykłe przechodzenie drzewa)."""

    def __init__(self, G: Gen):
        self.G = G

    # --- wyrażenia
    def expr(self, e):
        G = self.G
        t = type(e)
        if t is Num:
            v = DFloat(e.value, e.digits) if e.digits is not None else e.value
            return lambda env: v
        if t is Str:
            s = e.value
            return lambda env: s
        if t is Name:
            name, line, col = e.id, e.line, e.col

            def get(env):
                try:
                    return env[name]
                except KeyError:
                    raise DslError(f"Zmienna „{name}” nie ma jeszcze wartości (użyta przed przypisaniem).", line, col) from None
            return get
        if t is ListLit:
            fs = [self.expr(x) for x in e.items]
            return lambda env: [f(env) for f in fs]
        if t is BinOp:
            fa, fb, sym = self.expr(e.left), self.expr(e.right), e.op
            return _wrap(lambda env: _arith(G, sym, fa(env), fb(env)), e)
        if t is Unary:
            fo = self.expr(e.operand)

            def neg(env):
                v = fo(env)
                if type(v) is int:
                    if v == INT_MIN:
                        raise GenError(_RANGE_ERR)
                    return -v
                if _isnum(v):
                    return -v
                raise GenError(f"Nie można zmienić znaku: {_tname(v)}.")
            return _wrap(neg, e)
        if t is Not:
            fo = self.expr(e.operand)
            return lambda env: 0 if fo(env) else 1
        if t is BoolOp:
            fa, fb = self.expr(e.left), self.expr(e.right)
            if e.op == "and":
                return lambda env: (1 if fb(env) else 0) if fa(env) else 0
            return lambda env: 1 if fa(env) else (1 if fb(env) else 0)
        if t is Compare:
            fs = [self.expr(x) for x in e.operands]
            ops = list(e.ops)
            if len(ops) == 1:
                fa, fb, sym = fs[0], fs[1], ops[0]
                return _wrap(lambda env: 1 if _compare(sym, fa(env), fb(env)) else 0, e)

            def chain(env):
                a = fs[0](env)
                for k, sym in enumerate(ops):
                    b = fs[k + 1](env)
                    if not _compare(sym, a, b):
                        return 0
                    a = b
                return 1
            return _wrap(chain, e)
        if t is Index:
            fa, fi = self.expr(e.obj), self.expr(e.index)
            return _wrap(lambda env: _index(fa(env), fi(env)), e)
        if t is Call:
            return self.call(e)
        raise DslError("Nieobsługiwane wyrażenie.", getattr(e, "line", None), getattr(e, "col", None))

    def call(self, e: Call):
        G = self.G
        fn = getattr(G, e.name)
        if e.varargs is not None:
            fs = [self.expr(a) for a in e.varargs]
            kws = {k: self.expr(v) for k, v in e.varkw.items()}
            if kws:
                return _wrap(lambda env: fn(*[f(env) for f in fs], **{k: f(env) for k, f in kws.items()}), e, e.name + "()")
            return _wrap(lambda env: fn(*[f(env) for f in fs]), e, e.name + "()")
        params = SIGS[e.name][0]
        if e.name in ("array", "matrix"):
            fast = self._fast_gen(e.bound[-1])
            if fast is not None:
                return self._fast_array(e, fast)
        fs = []
        for p, a in zip(params, e.bound):
            if p in LAZY_PARAMS:
                g = self.expr(a)
                fs.append(lambda env, g=g: (lambda: g(env)))
            else:
                fs.append(self.expr(a))
        if len(fs) == 1:
            f0 = fs[0]
            return _wrap(lambda env: fn(f0(env)), e, e.name + "()")
        if len(fs) == 2:
            f0, f1 = fs
            return _wrap(lambda env: fn(f0(env), f1(env)), e, e.name + "()")
        return _wrap(lambda env: fn(*[f(env) for f in fs]), e, e.name + "()")

    def _fast_gen(self, g):
        """Szybka ścieżka array(n, int(lo, hi)) itp.: argumenty generatora liczone raz (gdy są „czyste”)."""
        if type(g) is not Call or g.name not in RANDOM_FUNCS or g.varkw:
            return None
        if g.name in ("array", "matrix"):
            return None
        args = g.varargs if g.varargs is not None else g.bound
        if not all(is_pure(a) for a in args):
            return None
        return g

    def _fast_array(self, e: Call, g: Call):
        G = self.G
        is_matrix = e.name == "matrix"
        size_fs = [self.expr(a) for a in e.bound[:-1]]
        arg_fs = [self.expr(a) for a in (g.varargs if g.varargs is not None else g.bound)]
        inner = getattr(G, g.name)
        gline, gcol = g.line, g.col
        is_int = g.name == "int"

        def run(env):
            sizes = [f(env) for f in size_fs]
            try:
                args = [f(env) for f in arg_fs]
            except DslError:
                raise
            if is_matrix:
                r = G._size(sizes[0], "matrix(r, …)")
                c = G._size(sizes[1], "matrix(…, c, …)")
                if r * c > MAX_N:
                    raise GenError("matrix(): za duża macierz.")
                total, shape = r * c, (r, c)
                G.cost(r * c + r)
            else:
                total = G._size(sizes[0], "array(n, …)")
                G.cost(total)
            try:
                if is_int and total:
                    lo = G._int(args[0], "int(lo, hi)")
                    hi = G._int(args[1], "int(lo, hi)")
                    if lo > hi:
                        raise GenError(f"int({lo}, {hi}): dolna granica jest większa od górnej.")
                    if hi - lo >= 1 << 63:
                        raise GenError("int(lo, hi): zakres jest za szeroki (najwyżej 2^63 liczb).")
                    below, w = G.R.below, hi - lo + 1
                    vals = [lo + below(w) for _ in range(total)]
                else:
                    vals = [inner(*args) for _ in range(total)]
            except (GenError, StructError) as ex:
                raise DslError(str(ex), gline, gcol) from None
            if is_matrix:
                r, c = shape
                return Rows([vals[i * c:(i + 1) * c] for i in range(r)])
            return vals
        return _wrap(run, e, e.name + "()")

    # --- instrukcje
    def block(self, body):
        fs = [self.stmt(s) for s in body]
        if not fs:
            return lambda env: None
        if len(fs) == 1:
            return fs[0]

        def run(env):
            for f in fs:
                f(env)
        return run

    def stmt(self, s):
        G = self.G
        cost = G.cost
        t = type(s)
        if t is Assign:
            fe, name, op = self.expr(s.expr), s.name, s.op
            if op == "=":
                def assign(env):
                    cost(1)
                    env[name] = fe(env)
                return assign
            sym = op[:-1]
            line, col = s.line, s.col

            def aug(env):
                cost(1)
                try:
                    old = env[name]
                except KeyError:
                    raise DslError(f"Zmienna „{name}” nie ma jeszcze wartości — „{op}” wymaga wcześniejszego przypisania.",
                                   line, col) from None
                env[name] = _arith(G, sym, old, fe(env))
            return _wrap(aug, s)
        if t is Print:
            fs = [self.expr(x) for x in s.items]
            fsep = self.expr(s.sep) if s.sep is not None else None
            fend = self.expr(s.end) if s.end is not None else None
            if fsep is None and fend is None:
                return _wrap(lambda env: G.print([f(env) for f in fs]), s, "print")
            return _wrap(lambda env: G.print([f(env) for f in fs], fsep(env) if fsep else " ",
                                             fend(env) if fend else "\n"), s, "print")
        if t is Repeat:
            fc, body = self.expr(s.count), self.block(s.body)
            line, col = s.line, s.col

            def rep(env):
                k = fc(env)
                if type(k) is not int:
                    raise DslError(f"repeat: liczba powtórzeń musi być liczbą całkowitą (jest {_tname(k)}).", line, col)
                for _ in range(k):
                    cost(1)
                    try:
                        body(env)
                    except _Break:
                        break
                    except _Continue:
                        continue
            return rep
        if t is For:
            body, var, line, col = self.block(s.body), s.var, s.line, s.col
            if s.iterable is None:
                fa, fb = self.expr(s.start), self.expr(s.end)

                def loop_range(env):
                    a = fa(env)
                    b = fb(env)
                    if type(a) is not int or type(b) is not int:
                        raise DslError("for i in a..b: granice muszą być liczbami całkowitymi.", line, col)
                    for i in range(a, b + 1):
                        cost(1)
                        env[var] = i
                        try:
                            body(env)
                        except _Break:
                            break
                        except _Continue:
                            continue
                return loop_range
            fi = self.expr(s.iterable)

            def loop_items(env):
                it = fi(env)
                if type(it) is str:
                    it = list(it)
                elif not isinstance(it, list):
                    raise DslError(f"for x in …: oczekiwano listy albo napisu, a jest {_tname(it)} "
                                   "(zakres liczb zapisz jako 1..n).", line, col)
                for x in list(it):
                    cost(1)
                    env[var] = x
                    try:
                        body(env)
                    except _Break:
                        break
                    except _Continue:
                        continue
            return loop_items
        if t is While:
            fc, body = self.expr(s.cond), self.block(s.body)

            def wh(env):
                while True:
                    cost(1)
                    if not fc(env):
                        break
                    try:
                        body(env)
                    except _Break:
                        break
                    except _Continue:
                        continue
            return wh
        if t is If:
            brs = [(self.expr(c), self.block(b)) for c, b in s.branches]
            orelse = self.block(s.orelse) if s.orelse is not None else None

            def iff(env):
                cost(1)
                for c, b in brs:
                    if c(env):
                        b(env)
                        return
                if orelse is not None:
                    orelse(env)
            return iff
        if t is Break:
            def brk(env):
                raise _Break()
            return brk
        if t is Continue:
            def cnt(env):
                raise _Continue()
            return cnt
        if t is Assert:
            fc = self.expr(s.cond)
            fm = self.expr(s.msg) if s.msg is not None else None
            return _wrap(lambda env: None if fc(env) else G.fail(fm(env) if fm else None), s, "assert")
        raise DslError("Nieobsługiwana instrukcja.", s.line, s.col)


def _eval_tests(prog: Program, size) -> int:
    if prog.tests is None:
        return 1
    G = Gen(0, max_steps=10_000)
    f = Interpreter(G).expr(prog.tests)
    v = f({"size": size, "testno": 0, "ntests": 0})
    if type(v) is not int:
        raise DslError("Liczba testów musi być liczbą całkowitą.", prog.tests.line, prog.tests.col)
    if v < 0:
        raise DslError("Liczba testów nie może być ujemna.", prog.tests.line, prog.tests.col)
    return v


def run(source: str, *, seed=None, tests=None, vars=None, preview: int = PREVIEW_DEFAULT, keep_output: bool = True,
        max_steps: int = 50_000_000, max_output: int = 64_000_000, time_limit: float = 60.0,
        max_tests: int = 200_000, first_test_only: bool = False) -> dict:
    """Uruchamia program DSL. Zwraca {output, tests, tests_count, preview, stats, warnings, seed}.
    Rzuca DslError (z linią/kolumną i numerem testu)."""
    prog = parse(source)
    vars = dict(vars or {})
    size = vars.pop("size", DEFAULT_SIZE)
    if type(size) is not int:
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise DslError("Zmienna size musi być liczbą całkowitą.") from None
    if seed is None or seed == "":
        seed = prog.seed if prog.seed is not None else 1
    try:
        seed = abs(int(seed)) % (1 << 64)
    except (TypeError, ValueError):
        raise DslError("Ziarno (seed) musi być liczbą całkowitą.") from None
    declared = _eval_tests(prog, size)
    T = declared
    if tests is not None and tests != "":
        try:
            T = int(tests)
        except (TypeError, ValueError):
            raise DslError("Liczba testów musi być liczbą całkowitą.") from None
    if first_test_only:
        T = min(T, 1) or 1
    T = max(0, min(T, max_tests))
    G = Gen(seed, max_steps=max_steps, max_output=max_output, time_limit=time_limit)
    body = Interpreter(G).block(prog.body)
    t0 = time.perf_counter()
    texts: list[str] = []
    stats = {"bytes": 0, "lines": 0, "max_test_bytes": 0, "max_test_lines": 0, "empty_tests": 0, "blank_inside": 0}
    warnings = []
    base_env = {"ntests": T, "size": size}
    for k, v in vars.items():
        if isinstance(k, str) and re.fullmatch(r"[A-Za-z_]\w*", k) and isinstance(v, (int, str)):
            base_env[k] = v
    first_blank = None
    for t in range(1, T + 1):
        env = dict(base_env)
        env["testno"] = t
        try:
            body(env)
        except DslError as e:
            e.test = t
            e.args = (e.text(),)
            e.partial = texts[:preview]
            raise
        except (_Break, _Continue):
            pass
        except RecursionError:
            raise DslError("Za głęboka rekurencja w interpreterze.", test=t) from None
        txt = G.end_test()
        nlines = txt.count("\n")
        b = len(txt)
        stats["bytes"] += b + (1 if t > 1 else 0)
        stats["lines"] += nlines
        stats["max_test_bytes"] = max(stats["max_test_bytes"], b)
        stats["max_test_lines"] = max(stats["max_test_lines"], nlines)
        if not txt:
            stats["empty_tests"] += 1
        elif txt.startswith("\n") or "\n\n" in txt:
            stats["blank_inside"] += 1
            if first_blank is None:
                first_blank = t
        if keep_output or len(texts) < preview:
            texts.append(txt)
    elapsed = time.perf_counter() - t0
    if stats["empty_tests"]:
        warnings.append(f"{stats['empty_tests']} test(ów) jest pustych — sprawdź warunki przy print.")
    if first_blank is not None:
        warnings.append(f"Test {first_blank} zawiera pustą linię — Testerka podzieli go na kilka testów "
                        "(pusta linia = separator). Unikaj pustych list i pustego print.")
    warnings += [w["message"] for w in prog.warnings if w["message"] not in warnings]
    output = "\n".join(texts) if keep_output else None
    stats.update({"tests": T, "time_ms": round(elapsed * 1000, 1), "steps": G.steps,
                  "avg_test_bytes": round(stats["bytes"] / T, 1) if T else 0, "seed": seed, "size": size})
    return {"output": output, "tests": texts if keep_output else None, "tests_count": T, "declared_tests": declared,
            "preview": texts[:preview], "stats": stats, "warnings": warnings, "seed": seed}


def generate(source: str, seed=None, tests=None, vars=None, **kw) -> str:
    """Całe wyjście generatora (testy oddzielone pustą linią)."""
    return run(source, seed=seed, tests=tests, vars=vars, preview=0, **kw)["output"]


# ============================================================================================== eksport: wspólne
_PREC = {"or": 1, "and": 2, "not": 3, "cmp": 4, "+": 6, "-": 6, "*": 7, "/": 7, "//": 7, "%": 7, "neg": 8, "**": 9,
         "post": 10, "atom": 11}


def _runtime_source() -> str:
    src = Path(__file__).read_text(encoding="utf-8")
    a = re.search(r"^# =+ RUNTIME-BEGIN$", src, re.M).start()
    b = re.search(r"^# =+ RUNTIME-END$", src, re.M).start()
    return src[a:b]


def _strip_docstrings(node):
    for n in _pyast.walk(node):
        if isinstance(n, (_pyast.FunctionDef, _pyast.ClassDef)) and n.body and isinstance(n.body[0], _pyast.Expr) \
                and isinstance(getattr(n.body[0], "value", None), _pyast.Constant) and isinstance(n.body[0].value.value, str):
            n.body = n.body[1:] or [_pyast.Pass()]
    return node


def _names_used(node) -> set:
    out = set()
    for n in _pyast.walk(node):
        if isinstance(n, _pyast.Name):
            out.add(n.id)
        elif isinstance(n, _pyast.Attribute) and isinstance(n.value, _pyast.Name) and n.value.id == "self":
            out.add("self." + n.attr)
    return out


def _python_runtime(used_funcs: set) -> str:
    """Kod środowiska dla eksportu Pythona: potrzebne metody Gen + zależności z randstruct (drzewo zależności)."""
    rt = _pyast.parse(_runtime_source())
    rs = _pyast.parse(Path(__file__).with_name("randstruct.py").read_text(encoding="utf-8"))
    tops = {}
    order = []
    for mod in (rs, rt):
        for n in mod.body:
            names = []
            if isinstance(n, (_pyast.FunctionDef, _pyast.ClassDef)):
                names = [n.name]
            elif isinstance(n, _pyast.Assign):
                names = [t.id for t in n.targets if isinstance(t, _pyast.Name)]
            for nm in names:
                if nm == "__all__":
                    continue
                tops[nm] = n
                order.append((nm, n))
    gen_cls = tops["Gen"]
    methods = {m.name: m for m in gen_cls.body if isinstance(m, _pyast.FunctionDef)}
    need_m = {"__init__", "cost", "print", "end_test", "fmt", "times", "span", "items", "fail", "div",
              "_int", "_num", "_str", "_list", "_size", "_chk", "_flag"} | set(used_funcs)
    stack = list(need_m)
    while stack:
        m = stack.pop()
        if m not in methods:
            continue
        for nm in _names_used(methods[m]):
            if nm.startswith("self.") and nm[5:] in methods and nm[5:] not in need_m:
                need_m.add(nm[5:])
                stack.append(nm[5:])
    new_cls = copy.copy(gen_cls)
    new_cls.body = [m for m in gen_cls.body if isinstance(m, _pyast.FunctionDef) and m.name in need_m]
    need = {"Gen", "GenError", "DFloat", "Rows", "StructError", "Rng"}
    roots = set()
    for m in new_cls.body:
        roots |= {x for x in _names_used(m) if not x.startswith("self.")}
    stack = list(roots | need)
    seen = set()
    while stack:
        nm = stack.pop()
        if nm in seen or nm not in tops:
            continue
        seen.add(nm)
        node = new_cls if nm == "Gen" else tops[nm]
        for x in _names_used(node):
            if not x.startswith("self.") and x in tops and x not in seen:
                stack.append(x)
    parts = []
    emitted = set()
    for nm, node in order:
        if nm not in seen or id(node) in emitted:
            continue
        emitted.add(id(node))
        node = new_cls if nm == "Gen" else node
        parts.append(_pyast.unparse(_strip_docstrings(_pyast.parse(_pyast.unparse(node)))))
    return "\n\n\n".join(parts)


def _sanitize_names(prog: Program, reserved: set) -> dict:
    mp = {}
    for nm in sorted(prog.assigned):
        new = nm
        while new in reserved:
            new += "_"
        mp[nm] = new
    return mp


# ============================================================================================== eksport: Python
_PY_RESERVED = {"False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
                "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is",
                "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
                "G", "sys", "math", "time", "random", "test", "main", "match", "case", "type", "_", "int", "float",
                "str", "list", "len", "range", "print", "DFloat", "Gen", "GenError", "StructError", "Rows", "Rng",
                "bool", "abs", "min", "max", "sum", "copy", "re"}


class _PyGen:
    def __init__(self, prog: Program):
        self.prog = prog
        self.names = _sanitize_names(prog, _PY_RESERVED)
        self.lines: list[str] = []
        self.tmp = 0

    def n(self, name):
        return self.names.get(name, name)

    def ex(self, e, ctx="value") -> tuple[str, int]:
        t = type(e)
        if t is Num:
            if e.digits is not None:
                return f"DFloat({e.value!r}, {e.digits})", 11
            return (str(e.value), 11) if e.value >= 0 else (f"({e.value})", 11)
        if t is Str:
            return repr(e.value), 11
        if t is Name:
            return self.n(e.id), 11
        if t is ListLit:
            return "[" + ", ".join(self.v(x) for x in e.items) + "]", 11
        if t is BinOp:
            if e.op == "/":
                return f"G.div({self.v(e.left)}, {self.v(e.right)})", 10
            p = _PREC[e.op]
            if e.op == "**":
                a = self.par(e.left, p + 1)
                b = self.par(e.right, p)
            else:
                a = self.par(e.left, p)
                b = self.par(e.right, p + 1)
            return f"{a} {e.op} {b}", p
        if t is Unary:
            return "-" + self.par(e.operand, 8), 8
        if t is Not:
            inner = self.par(e.operand, 3, ctx="cond")
            return (f"not {inner}", 3) if ctx == "cond" else (f"(0 if {inner} else 1)", 11)
        if t is BoolOp:
            p = _PREC[e.op]
            s = f"{self.par(e.left, p, ctx='cond')} {e.op} {self.par(e.right, p + 1, ctx='cond')}"
            return (s, p) if ctx == "cond" else (f"(1 if {s} else 0)", 11)
        if t is Compare:
            parts = [self.par(e.operands[0], 6)]
            for op, o in zip(e.ops, e.operands[1:]):
                parts.append(op)
                parts.append(self.par(o, 6))
            s = " ".join(parts)
            return (s, 4) if ctx == "cond" else (f"int({s})", 11)
        if t is Index:
            return f"{self.par(e.obj, 10)}[{self.v(e.index)}]", 10
        if t is Call:
            return self.call(e), 10
        raise DslError("Nieobsługiwane wyrażenie w eksporcie.", e.line, e.col)

    def v(self, e):
        return self.ex(e)[0]

    def par(self, e, need, ctx="value"):
        s, p = self.ex(e, ctx)
        return s if p >= need else f"({s})"

    def cond(self, e):
        return self.ex(e, "cond")[0]

    def call(self, e: Call):
        if e.varargs is not None:
            args = [self.v(a) for a in e.varargs] + [f"{k}={self.v(v)}" for k, v in e.varkw.items()]
            return f"G.{e.name}({', '.join(args)})"
        params, defaults, _, _ = SIGS[e.name]
        out = []
        for i, (p, a) in enumerate(zip(params, e.bound)):
            if i > e.last_explicit and p in defaults:
                break
            code = self.v(a)
            out.append(f"lambda: {code}" if p in LAZY_PARAMS else code)
        return f"G.{e.name}({', '.join(out)})"

    def emit(self, line, ind):
        self.lines.append("    " * ind + line)

    def block(self, body, ind):
        if not body:
            self.emit("pass", ind)
        for s in body:
            self.stmt(s, ind)

    def stmt(self, s, ind):
        t = type(s)
        if t is Assign:
            nm = self.n(s.name)
            if s.op == "=":
                self.emit(f"{nm} = {self.v(s.expr)}", ind)
            else:
                sym = s.op[:-1]
                rhs = BinOp(sym, Name(s.name, s.line, s.col), s.expr, s.line, s.col)
                self.emit(f"{nm} = {self.v(rhs)}", ind)
        elif t is Print:
            args = ["[" + ", ".join(self.v(x) for x in s.items) + "]"]
            if s.sep is not None:
                args.append(f"sep={self.v(s.sep)}")
            if s.end is not None:
                args.append(f"end={self.v(s.end)}")
            self.emit(f"G.print({', '.join(args)})", ind)
        elif t is Repeat:
            self.tmp += 1
            self.emit(f"for _r{self.tmp} in G.times({self.v(s.count)}):", ind)
            self.block(s.body, ind + 1)
        elif t is For:
            nm = self.n(s.var)
            if s.iterable is None:
                self.emit(f"for {nm} in G.span({self.v(s.start)}, {self.v(s.end)}):", ind)
            else:
                self.emit(f"for {nm} in G.items({self.v(s.iterable)}):", ind)
            self.block(s.body, ind + 1)
        elif t is While:
            self.emit(f"while {self.cond(s.cond)}:", ind)
            self.block(s.body, ind + 1)
        elif t is If:
            for k, (c, b) in enumerate(s.branches):
                self.emit(f"{'if' if k == 0 else 'elif'} {self.cond(c)}:", ind)
                self.block(b, ind + 1)
            if s.orelse is not None:
                self.emit("else:", ind)
                self.block(s.orelse, ind + 1)
        elif t is Break:
            self.emit("break", ind)
        elif t is Continue:
            self.emit("continue", ind)
        elif t is Assert:
            self.emit(f"if not ({self.cond(s.cond)}):", ind)
            self.emit(f"G.fail({self.v(s.msg) if s.msg is not None else ''})", ind + 1)


def _source_comment(src: str, prefix: str) -> str:
    lines = (src or "").replace("\r\n", "\n").rstrip("\n").split("\n")
    return "\n".join(prefix + ln if ln.strip() else prefix.rstrip() for ln in lines)


def export_python(source: str) -> str:
    prog = parse(source)
    g = _PyGen(prog)
    g.block(prog.body, 1)
    tests_expr = g.v(prog.tests) if prog.tests is not None else "1"
    seed = prog.seed if prog.seed is not None else 1
    runtime = _python_runtime(prog.calls)
    head = f'''#!/usr/bin/env python3
# Generator testów wyeksportowany z OI Forge (mini-język generatorów).
# Uruchomienie:  python3 gen.py <seed> [liczba_testów] [size]
# Wypisuje testy oddzielone PUSTĄ LINIĄ (format Testerki OI Forge). Ten sam seed = te same testy
# co w podglądzie OI Forge (to samo losowanie: MT19937 + identyczne algorytmy).
#
# Program źródłowy:
{_source_comment(source, "#   ")}
import math
import random
import sys
import time


def test(G, testno, ntests, size):
'''
    body = "\n".join(g.lines)
    main = f'''

def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else {seed}
    size = int(sys.argv[3]) if len(sys.argv) > 3 else {DEFAULT_SIZE}
    ntests = int(sys.argv[2]) if len(sys.argv) > 2 else {tests_expr}
    G = Gen(seed)
    out = sys.stdout
    try:
        for t in range(1, ntests + 1):
            test(G, t, ntests, size)
            text = G.end_test()
            out.write(("\\n" if t > 1 else "") + text)
    except (GenError, StructError) as e:
        out.flush()
        sys.stderr.write("Błąd generatora: " + str(e) + "\\n")
        sys.exit(1)
    out.flush()


# ----------------------------------------------------------------------------- środowisko (nie edytuj)
'''
    return head + body + main + runtime + '\n\n\nif __name__ == "__main__":\n    main()\n'


# ============================================================================================== eksport: C++
_CPP_RESERVED = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor", "bool", "break", "case", "catch", "char",
    "char8_t", "char16_t", "char32_t", "class", "compl", "concept", "const", "consteval", "constexpr", "constinit",
    "const_cast", "continue", "co_await", "co_return", "co_yield", "decltype", "default", "delete", "do", "double",
    "dynamic_cast", "else", "enum", "explicit", "export", "extern", "false", "float", "for", "friend", "goto", "if",
    "inline", "int", "long", "mutable", "namespace", "new", "noexcept", "not", "not_eq", "nullptr", "operator", "or",
    "or_eq", "private", "protected", "public", "register", "reinterpret_cast", "requires", "return", "short",
    "signed", "sizeof", "static", "static_assert", "static_cast", "struct", "switch", "template", "this",
    "thread_local", "throw", "true", "try", "typedef", "typeid", "typename", "union", "unsigned", "using", "virtual",
    "void", "volatile", "wchar_t", "while", "xor", "xor_eq", "main", "test", "V", "L", "F", "T", "R", "OUT", "oig",
    "std", "fail", "Print", "Idx", "In", "Div", "FDiv", "Mod", "Pw", "Neg", "Times", "ToInt", "Items", "Fail",
    "assert", "errno", "stdin", "stdout", "stderr", "NULL", "EOF", "FILE",
}
_CPP_FN = {f: f[0].upper() + f[1:] for f in FUNCTIONS}
_CPP_RESERVED |= set(_CPP_FN.values())
# grupy kodu C++ (drzewo zależności) potrzebne dla funkcji
_CPP_GROUP = {
    **{f: "lib" for f in ("float", "chance", "choice", "array", "matrix", "range", "sorted", "reverse", "shuffle",
                          "unique", "len", "sum", "min", "max", "count", "put", "flat", "rows", "lines", "join", "str",
                          "abs", "floor", "ceil", "round", "sqrt", "gcd", "lcm")},
    "char": "str", "string": "str", "palindrome": "str", "periodic": "str", "antihash": "str",
    "distinct": "seq", "perm": "seq", "seq": "seq",
    "tree": "tree", "parents": "tree", "forest": "tree",
    "graph": "graph", "dag": "graph", "bipartite": "graph",
    "grid": "grid", "pairs": "misc", "interval": "misc", "points": "misc", "queries": "misc",
}
_CPP_DEPS = {"lib": [], "struct": [], "str": ["struct"], "seq": ["struct"], "tree": ["seq", "struct"],
             "graph": ["tree"], "grid": ["str", "struct"], "misc": ["struct"]}


def _cpp_str(s: str) -> str:
    out = ['"']
    for ch in s:
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
        elif ch == "\0":
            out.append('\\0""')
        elif ch == "?":
            out.append("\\?")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _cpp_lit(v) -> str:
    if isinstance(v, str):
        return f"V({_cpp_str(v)})"
    if isinstance(v, int):
        return f"V({v}LL)" if not (-(1 << 31) < v < (1 << 31)) else f"V({v})"
    raise ValueError(v)


class _CppGen:
    def __init__(self, prog: Program):
        self.prog = prog
        self.names = _sanitize_names(prog, _CPP_RESERVED)
        self.lines: list[str] = []
        self.tmp = 0

    def n(self, name):
        return self.names.get(name, name)

    def lam(self, decls: list[tuple[str, str]], ret: str) -> str:
        body = " ".join(f"V {nm} = {code};" for nm, code in decls)
        return f"[&]() -> V {{ {body} return {ret}; }}()"

    def num(self, e: Num) -> str:
        if e.digits is not None:
            return f"V::F({e.value!r}, {e.digits})"
        return _cpp_lit(e.value)

    def v(self, e) -> str:
        """Wyrażenie typu V."""
        t = type(e)
        if t is Num:
            return self.num(e)
        if t is Str:
            return f"V({_cpp_str(e.value)})"
        if t is Name:
            return self.n(e.id)
        if t is ListLit:
            return "V::L({" + ", ".join(self.v(x) for x in e.items) + "})"
        if t is BinOp:
            a, b = self.v(e.left), self.v(e.right)
            fn = {"+": "Add", "-": "Sub", "*": "Mul", "/": "Div", "//": "FDiv", "%": "Mod", "**": "Pw"}[e.op]
            if not is_pure(e.left) and not is_pure(e.right):
                return self.lam([("_a", a), ("_b", b)], f"{fn}(_a, _b)")
            return f"{fn}({a}, {b})"
        if t is Unary:
            return f"Neg({self.v(e.operand)})"
        if t in (Not, BoolOp, Compare):
            return f"V({self.c(e)})"
        if t is Index:
            a, i = self.v(e.obj), self.v(e.index)
            if not is_pure(e.obj) and not is_pure(e.index):
                return self.lam([("_a", a), ("_i", i)], "Idx(_a, _i)")
            return f"Idx({a}, {i})"
        if t is Call:
            return self.call(e)
        raise DslError("Nieobsługiwane wyrażenie w eksporcie.", e.line, e.col)

    def c(self, e) -> str:
        """Wyrażenie typu bool (warunek)."""
        t = type(e)
        if t is Not:
            return f"!({self.c(e.operand)})"
        if t is BoolOp:
            op = "&&" if e.op == "and" else "||"
            return f"({self.c(e.left)} {op} {self.c(e.right)})"
        if t is Compare:
            def one(op, a, b):
                if op == "in":
                    return f"In({a}, {b})"
                if op == "not in":
                    return f"!In({a}, {b})"
                return f"({a} {op} {b})"
            if len(e.ops) == 1:
                a, b = self.v(e.operands[0]), self.v(e.operands[1])
                if not is_pure(e.operands[0]) and not is_pure(e.operands[1]):
                    return "[&]() -> bool { V _a = " + a + "; V _b = " + b + "; return " + one(e.ops[0], "_a", "_b") + "; }()"
                return one(e.ops[0], a, b)
            parts = [f"V _o0 = {self.v(e.operands[0])};"]
            for k, op in enumerate(e.ops):
                parts.append(f"V _o{k + 1} = {self.v(e.operands[k + 1])};")
                parts.append(f"if (!{one(op, f'_o{k}', f'_o{k + 1}')}) return false;")
            return "[&]() -> bool { " + " ".join(parts) + " return true; }()"
        return f"T({self.v(e)})"

    def call(self, e: Call) -> str:
        fn = _CPP_FN[e.name]
        if e.varargs is not None:
            items = "{" + ", ".join(self.v(a) for a in e.varargs) + "}"
            args = [items] + [self.v(v) for v in e.varkw.values()]
            return f"{fn}({', '.join(args)})"
        params, defaults, _, _ = SIGS[e.name]
        out, impure = [], []
        for i, (p, a) in enumerate(zip(params, e.bound)):
            if i > e.last_explicit and p in defaults:
                break
            if p in LAZY_PARAMS:
                out.append(f"[&]() -> V {{ return {self.v(a)}; }}")
            else:
                code = self.v(a)
                if not is_pure(a):
                    impure.append(i)
                out.append(code)
        if len(impure) >= 2:
            decls = []
            for k, i in enumerate(impure):
                decls.append((f"_p{k}", out[i]))
                out[i] = f"_p{k}"
            return self.lam(decls, f"{fn}({', '.join(out)})")
        return f"{fn}({', '.join(out)})"

    def emit(self, line, ind):
        self.lines.append("    " * ind + line)

    def block(self, body, ind):
        for s in body:
            self.stmt(s, ind)

    def stmt(self, s, ind):
        t = type(s)
        if t is Assign:
            nm = self.n(s.name)
            if s.op == "=":
                self.emit(f"{nm} = {self.v(s.expr)};", ind)
            else:
                rhs = BinOp(s.op[:-1], Name(s.name, s.line, s.col), s.expr, s.line, s.col)
                self.emit(f"{nm} = {self.v(rhs)};", ind)
        elif t is Print:
            args = ["{" + ", ".join(self.v(x) for x in s.items) + "}"]
            if s.sep is not None or s.end is not None:
                args.append(self.v(s.sep) if s.sep is not None else 'V(" ")')
            if s.end is not None:
                args.append(self.v(s.end))
            self.emit(f"Print({', '.join(args)});", ind)
        elif t is Repeat:
            self.tmp += 1
            k = self.tmp
            self.emit(f"for (long long _r{k} = Times({self.v(s.count)}); _r{k} > 0; --_r{k}) {{", ind)
            self.block(s.body, ind + 1)
            self.emit("}", ind)
        elif t is For:
            self.tmp += 1
            k = self.tmp
            nm = self.n(s.var)
            if s.iterable is None:
                self.emit(f"for (long long _i{k} = ToInt({self.v(s.start)}), _e{k} = ToInt({self.v(s.end)}); "
                          f"_i{k} <= _e{k}; ++_i{k}) {{", ind)
                self.emit(f"{nm} = V(_i{k});", ind + 1)
            else:
                self.emit(f"for (const V& _x{k} : Items({self.v(s.iterable)})) {{", ind)
                self.emit(f"{nm} = _x{k};", ind + 1)
            self.block(s.body, ind + 1)
            self.emit("}", ind)
        elif t is While:
            self.emit(f"while ({self.c(s.cond)}) {{", ind)
            self.block(s.body, ind + 1)
            self.emit("}", ind)
        elif t is If:
            for k, (c, b) in enumerate(s.branches):
                self.emit(f"{'if' if k == 0 else '} else if'} ({self.c(c)}) {{", ind)
                self.block(b, ind + 1)
            if s.orelse is not None:
                self.emit("} else {", ind)
                self.block(s.orelse, ind + 1)
            self.emit("}", ind)
        elif t is Break:
            self.emit("break;", ind)
        elif t is Continue:
            self.emit("continue;", ind)
        elif t is Assert:
            self.emit(f"if (!({self.c(s.cond)})) Fail({self.v(s.msg) if s.msg is not None else 'V()'});", ind)


def _cpp_runtime(groups: set) -> str:
    parts = []
    chunks = re.split(r"^//@@(\w+)\s*$", CPP_RUNTIME, flags=re.M)
    # chunks: [przed, nazwa1, kod1, nazwa2, kod2, ...]
    need = set(groups)
    stack = list(need)
    while stack:
        g = stack.pop()
        for d in _CPP_DEPS.get(g, []):
            if d not in need:
                need.add(d)
                stack.append(d)
    for i in range(1, len(chunks), 2):
        name, code = chunks[i], chunks[i + 1]
        if name in ("core", "end") or name in need:
            parts.append(code.strip("\n"))
    return "\n\n".join(parts)


def export_cpp(source: str) -> str:
    prog = parse(source)
    g = _CppGen(prog)
    g.block(prog.body, 1)
    groups = {_CPP_GROUP[f] for f in prog.calls if f in _CPP_GROUP}
    runtime = _cpp_runtime(groups)
    user_vars = [g.n(v) for v in sorted(prog.assigned - set(PREDEFINED))]
    decl = f"    V {', '.join(user_vars)};\n" if user_vars else ""
    tests_expr = f"ToInt({g.v(prog.tests)})" if prog.tests is not None else "1"
    seed = prog.seed if prog.seed is not None else 1
    body = "\n".join(g.lines)
    return f'''// Generator testów wyeksportowany z OI Forge (mini-język generatorów).
// Kompilacja:   g++ -O2 -std=c++17 gen.cpp -o gen
// Uruchomienie: ./gen <seed> [liczba_testów] [size]
// Wypisuje testy oddzielone PUSTĄ LINIĄ (format Testerki OI Forge). Losowanie: MT19937 inicjalizowany jak
// random.seed w Pythonie + te same algorytmy co w OI Forge, więc ten sam seed daje IDENTYCZNE testy jak podgląd.
//
// Program źródłowy:
{_source_comment(source, "//   ")}

{runtime}

using namespace oig;

// ------------------------------------------------------------------------------ Twój generator (jeden test)
void test(V testno, V ntests, V size) {{
    (void)testno; (void)ntests; (void)size;
{decl}{body}
}}

int main(int argc, char** argv) {{
    unsigned long long seed = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : {seed}ULL;
    V size = argc > 3 ? V(std::atoll(argv[3])) : V({DEFAULT_SIZE});
    long long ntests = argc > 2 ? std::atoll(argv[2]) : {tests_expr};
    R.seed(seed);
    for (long long t = 1; t <= ntests; ++t) {{
        if (t > 1) Emit("\\n");
        BeginTest();
        test(V(t), V(ntests), size);
        EndTest();
    }}
    FlushOut();
    return 0;
}}
'''


def export(source: str, lang: str = "cpp") -> str:
    """Samodzielny generator w C++ albo Pythonie (seed z argv[1], liczba testów z argv[2], size z argv[3])."""
    lang = (lang or "cpp").lower()
    if lang in ("py", "python", "python3"):
        return export_python(source)
    if lang in ("cpp", "c++", "cxx"):
        return export_cpp(source)
    raise DslError(f"Nieznany język eksportu „{lang}” (dostępne: cpp, python).")


# ============================================================================================== środowisko C++
CPP_RUNTIME = r"""
//@@core
#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <set>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

// ------------------------------------------------------------------------------ środowisko (nie edytuj)
namespace oig {

inline std::string OUT;
inline bool HAS = false;
inline char LAST = '\n';

inline void FlushOut() {
    if (!OUT.empty()) { std::fwrite(OUT.data(), 1, OUT.size(), stdout); OUT.clear(); }
    std::fflush(stdout);
}
[[noreturn]] inline void fail(const std::string& msg) {
    FlushOut();
    std::fprintf(stderr, "Błąd generatora: %s\n", msg.c_str());
    std::exit(1);
}
inline void Emit(const std::string& s) {
    if (s.empty()) return;
    OUT += s;
    HAS = true;
    LAST = s.back();
    if (OUT.size() > (1u << 16)) { std::fwrite(OUT.data(), 1, OUT.size(), stdout); OUT.clear(); }
}
inline void BeginTest() { HAS = false; LAST = '\n'; }
inline void EndTest() { if (HAS && LAST != '\n') Emit("\n"); }

// MT19937 z inicjalizacją identyczną jak random.seed(int) w Pythonie (init_by_array)
struct MT {
    uint32_t mt[624];
    int mti = 625;
    void init_genrand(uint32_t s) {
        mt[0] = s;
        for (mti = 1; mti < 624; mti++) mt[mti] = 1812433253U * (mt[mti - 1] ^ (mt[mti - 1] >> 30)) + (uint32_t)mti;
    }
    void init_by_array(const uint32_t* key, int len) {
        init_genrand(19650218U);
        int i = 1, j = 0;
        for (int k = (624 > len ? 624 : len); k; k--) {
            mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525U)) + key[j] + (uint32_t)j;
            i++; j++;
            if (i >= 624) { mt[0] = mt[623]; i = 1; }
            if (j >= len) j = 0;
        }
        for (int k = 623; k; k--) {
            mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941U)) - (uint32_t)i;
            i++;
            if (i >= 624) { mt[0] = mt[623]; i = 1; }
        }
        mt[0] = 0x80000000U;
        mti = 624;
    }
    void seed(unsigned long long s) {
        uint32_t key[2] = {(uint32_t)(s & 0xffffffffULL), (uint32_t)(s >> 32)};
        init_by_array(key, (s >> 32) ? 2 : 1);
    }
    uint32_t next() {
        static const uint32_t mag01[2] = {0x0U, 0x9908b0dfU};
        uint32_t y;
        if (mti >= 624) {
            int kk;
            for (kk = 0; kk < 624 - 397; kk++) {
                y = (mt[kk] & 0x80000000U) | (mt[kk + 1] & 0x7fffffffU);
                mt[kk] = mt[kk + 397] ^ (y >> 1) ^ mag01[y & 1U];
            }
            for (; kk < 623; kk++) {
                y = (mt[kk] & 0x80000000U) | (mt[kk + 1] & 0x7fffffffU);
                mt[kk] = mt[kk + (397 - 624)] ^ (y >> 1) ^ mag01[y & 1U];
            }
            y = (mt[623] & 0x80000000U) | (mt[0] & 0x7fffffffU);
            mt[623] = mt[396] ^ (y >> 1) ^ mag01[y & 1U];
            mti = 0;
        }
        y = mt[mti++];
        y ^= (y >> 11);
        y ^= (y << 7) & 0x9d2c5680U;
        y ^= (y << 15) & 0xefc60000U;
        y ^= (y >> 18);
        return y;
    }
};

// Losowanie jak oiforge.core.randstruct.Rng (getrandbits + odrzucanie)
struct Rng {
    MT mt;
    void seed(unsigned long long s) { mt.seed(s); }
    unsigned long long bits(int k) {
        if (k <= 0) return 0;
        if (k <= 32) return mt.next() >> (32 - k);
        unsigned long long lo = mt.next();
        unsigned long long hi = mt.next() >> (64 - k);
        return lo | (hi << 32);
    }
    unsigned long long below(unsigned long long n) {
        if (n == 0) fail("Losowanie z pustego zakresu.");
        int k = 64 - __builtin_clzll(n);
        unsigned long long r = bits(k);
        while (r >= n) r = bits(k);
        return r;
    }
    long long randint(long long a, long long b) {
        if (a > b) fail("Pusty przedział [" + std::to_string(a) + ", " + std::to_string(b) + "].");
        return (long long)((unsigned long long)a + below((unsigned long long)b - (unsigned long long)a + 1ULL));
    }
    double random() {
        uint32_t a = mt.next() >> 5, b = mt.next() >> 6;
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
    }
    template <class X> void shuffle(std::vector<X>& x) {
        for (long long i = (long long)x.size() - 1; i > 0; --i) {
            long long j = (long long)below((unsigned long long)i + 1);
            std::swap(x[i], x[j]);
        }
    }
};
inline Rng R;
const long long MAX_N = 10000000;

// Wartość dynamiczna (jak w mini-języku): liczba całkowita, rzeczywista (z liczbą cyfr), napis, lista
struct V {
    int t = -1;              // -1 brak, 0 int, 1 float, 2 napis, 3 lista, 4 lista wierszy
    int d = 6;
    long long i = 0;
    double f = 0;
    std::string s;
    std::vector<V> a;
    V() {}
    V(int x) : t(0), i(x) {}
    V(long long x) : t(0), i(x) {}
    V(const char* x) : t(2), s(x) {}
    V(const std::string& x) : t(2), s(x) {}
    static V F(double x, int dg) { V v; v.t = 1; v.f = x; v.d = dg; return v; }
    static V L(std::vector<V> xs, bool rows = false) { V v; v.t = rows ? 4 : 3; v.a = std::move(xs); return v; }
    bool num() const { return t == 0 || t == 1; }
    bool lst() const { return t == 3 || t == 4; }
    double dv() const { return t == 0 ? (double)i : f; }
};

inline std::string tn(const V& x) {
    switch (x.t) {
        case 0: return "liczba całkowita";
        case 1: return "liczba rzeczywista";
        case 2: return "napis";
        case 3: case 4: return "lista";
    }
    return "brak wartości";
}
inline long long I(const V& x, const char* what) {
    if (x.t == 0) return x.i;
    fail(std::string(what) + ": oczekiwano liczby całkowitej, a jest " + tn(x) + (x.t == 1 ? " (wynik „/” jest rzeczywisty — użyj „//”)" : "") + ".");
}
inline const V& Num(const V& x, const char* what) {
    if (x.num()) return x;
    fail(std::string(what) + ": oczekiwano liczby, a jest " + tn(x) + ".");
}
inline const std::string& S(const V& x, const char* what) {
    if (x.t == 2) return x.s;
    fail(std::string(what) + ": oczekiwano napisu, a jest " + tn(x) + ".");
}
inline const std::vector<V>& Lst(const V& x, const char* what) {
    if (x.lst()) return x.a;
    fail(std::string(what) + ": oczekiwano listy, a jest " + tn(x) + ".");
}
inline long long Size(const V& x, const char* what) {
    long long n = I(x, what);
    if (n < 0) fail(std::string(what) + ": rozmiar nie może być ujemny (jest " + std::to_string(n) + ").");
    if (n > MAX_N) fail(std::string(what) + ": rozmiar " + std::to_string(n) + " jest za duży.");
    return n;
}
inline bool T(const V& x) {
    switch (x.t) {
        case 0: return x.i != 0;
        case 1: return x.f != 0;
        case 2: return !x.s.empty();
        case 3: case 4: return !x.a.empty();
    }
    fail("Użyto zmiennej bez wartości.");
}
inline bool T(bool b) { return b; }
inline long long ToInt(const V& x) { return I(x, "for … in a..b"); }
inline int dg(const V& a, const V& b) {
    int d = -1;
    if (a.t == 1) d = a.d;
    if (b.t == 1 && b.d > d) d = b.d;
    return d < 0 ? 6 : d;
}
[[noreturn]] inline void opfail(const char* verb, const char* sym, const V& a, const V& b) {
    fail(std::string("Nie można ") + verb + ": " + tn(a) + " " + sym + " " + tn(b) + ".");
}
const char* OVF = "Przepełnienie: wynik nie mieści się w 64 bitach (long long).";

inline V Add(const V& a, const V& b) {
    if (a.t == 0 && b.t == 0) { long long r; if (__builtin_add_overflow(a.i, b.i, &r)) fail(OVF); return V(r); }
    if (a.num() && b.num()) return V::F(a.dv() + b.dv(), dg(a, b));
    if (a.t == 2 && b.t == 2) return V(a.s + b.s);
    if (a.lst() && b.lst()) { std::vector<V> r = a.a; r.insert(r.end(), b.a.begin(), b.a.end()); return V::L(std::move(r)); }
    opfail("dodać", "+", a, b);
}
inline V Sub(const V& a, const V& b) {
    if (a.t == 0 && b.t == 0) { long long r; if (__builtin_sub_overflow(a.i, b.i, &r)) fail(OVF); return V(r); }
    if (a.num() && b.num()) return V::F(a.dv() - b.dv(), dg(a, b));
    opfail("odjąć", "-", a, b);
}
inline V Rep(const V& x, long long k) {
    if (k < 0) k = 0;
    if (x.t == 2) { std::string r; r.reserve(x.s.size() * k); for (long long j = 0; j < k; ++j) r += x.s; return V(r); }
    std::vector<V> r; r.reserve(x.a.size() * k);
    for (long long j = 0; j < k; ++j) r.insert(r.end(), x.a.begin(), x.a.end());
    return V::L(std::move(r));
}
inline V Mul(const V& a, const V& b) {
    if (a.t == 0 && b.t == 0) { long long r; if (__builtin_mul_overflow(a.i, b.i, &r)) fail(OVF); return V(r); }
    if (a.num() && b.num()) return V::F(a.dv() * b.dv(), dg(a, b));
    if ((a.t == 2 || a.lst()) && b.t == 0) return Rep(a, b.i);
    if (a.t == 0 && (b.t == 2 || b.lst())) return Rep(b, a.i);
    opfail("pomnożyć", "*", a, b);
}
inline V Div(const V& a, const V& b) {
    if (!a.num()) fail("/: oczekiwano liczby, a jest " + tn(a) + ".");
    if (!b.num()) fail("/: oczekiwano liczby, a jest " + tn(b) + ".");
    if (b.dv() == 0) fail("Dzielenie przez zero.");
    return V::F(a.dv() / b.dv(), dg(a, b));
}
inline void fdivmod(double vx, double wx, double& fd, double& md) {
    double mod = std::fmod(vx, wx);
    double div = (vx - mod) / wx;
    if (mod) { if ((wx < 0) != (mod < 0)) { mod += wx; div -= 1.0; } }
    else mod = std::copysign(0.0, wx);
    if (div) { fd = std::floor(div); if (div - fd > 0.5) fd += 1.0; }
    else fd = std::copysign(0.0, vx / wx);
    md = mod;
}
inline V FDiv(const V& a, const V& b) {
    if (a.t == 0 && b.t == 0) {
        if (b.i == 0) fail("Dzielenie całkowite przez zero.");
        if (a.i == LLONG_MIN && b.i == -1) fail(OVF);
        long long q = a.i / b.i;
        if ((a.i % b.i != 0) && ((a.i < 0) != (b.i < 0))) --q;
        return V(q);
    }
    if (a.num() && b.num()) {
        if (b.dv() == 0) fail("Dzielenie przez zero.");
        double fd, md; fdivmod(a.dv(), b.dv(), fd, md); return V::F(fd, dg(a, b));
    }
    opfail("podzielić", "//", a, b);
}
inline V Mod(const V& a, const V& b) {
    if (a.t == 0 && b.t == 0) {
        if (b.i == 0) fail("Reszta z dzielenia przez zero.");
        if (b.i == -1) return V(0);
        long long r = a.i % b.i;
        if (r != 0 && ((r < 0) != (b.i < 0))) r += b.i;
        return V(r);
    }
    if (a.num() && b.num()) {
        if (b.dv() == 0) fail("Dzielenie przez zero.");
        double vx = a.dv(), wx = b.dv(), mod = std::fmod(vx, wx);
        if (mod) { if ((wx < 0) != (mod < 0)) mod += wx; } else mod = std::copysign(0.0, wx);
        return V::F(mod, dg(a, b));
    }
    opfail("policzyć reszty", "%", a, b);
}
inline V Pw(const V& a, const V& b) {
    if (a.t == 0 && b.t == 0) {
        long long x = a.i, e = b.i;
        if (e < 0) { if (x == 0) fail("0 do potęgi ujemnej."); return V::F(std::pow((double)x, (double)e), 6); }
        if (x == 0) return V(e == 0 ? 1 : 0);
        if (x == 1) return V(1);
        if (x == -1) return V(e % 2 ? -1 : 1);
        if (e > 64) fail(OVF);
        long long r = 1;
        for (long long k = 0; k < e; ++k) if (__builtin_mul_overflow(r, x, &r)) fail(OVF);
        return V(r);
    }
    if (a.num() && b.num()) {
        double x = a.dv(), y = b.dv();
        if (x == 0 && y < 0) fail("0 do potęgi ujemnej.");
        if (x < 0 && y != std::floor(y)) fail("Potęga liczby ujemnej z ułamkowym wykładnikiem nie jest liczbą rzeczywistą.");
        double r = (y == 0) ? 1.0 : std::pow(x, y);
        if (std::isinf(r)) fail("Wynik jest za duży (przepełnienie liczby rzeczywistej).");
        return V::F(r, dg(a, b));
    }
    opfail("potęgować", "**", a, b);
}
inline V Neg(const V& a) {
    if (a.t == 0) { if (a.i == LLONG_MIN) fail(OVF); return V(-a.i); }
    if (a.t == 1) return V::F(-a.f, a.d);
    fail("Nie można zmienić znaku: " + tn(a) + ".");
}
inline bool Eq(const V& a, const V& b) {
    if (a.num() && b.num()) return (a.t == 0 && b.t == 0) ? a.i == b.i : a.dv() == b.dv();
    if (a.t == 2 && b.t == 2) return a.s == b.s;
    if (a.lst() && b.lst()) {
        if (a.a.size() != b.a.size()) return false;
        for (size_t k = 0; k < a.a.size(); ++k) if (!Eq(a.a[k], b.a[k])) return false;
        return true;
    }
    if (a.t == -1 || b.t == -1) fail("Użyto zmiennej bez wartości.");
    return false;
}
inline int Cmp(const V& a, const V& b) {
    if (a.num() && b.num()) {
        if (a.t == 0 && b.t == 0) return a.i < b.i ? -1 : (a.i > b.i ? 1 : 0);
        double x = a.dv(), y = b.dv();
        return x < y ? -1 : (x > y ? 1 : 0);
    }
    if (a.t == 2 && b.t == 2) { int c = a.s.compare(b.s); return c < 0 ? -1 : (c > 0 ? 1 : 0); }
    if (a.lst() && b.lst()) {
        size_t n = std::min(a.a.size(), b.a.size());
        for (size_t k = 0; k < n; ++k) if (!Eq(a.a[k], b.a[k])) return Cmp(a.a[k], b.a[k]);
        return a.a.size() < b.a.size() ? -1 : (a.a.size() > b.a.size() ? 1 : 0);
    }
    fail("Nie można porównać: " + tn(a) + " i " + tn(b) + ".");
}
inline bool operator==(const V& a, const V& b) { return Eq(a, b); }
inline bool operator!=(const V& a, const V& b) { return !Eq(a, b); }
inline bool operator<(const V& a, const V& b) { return Cmp(a, b) < 0; }
inline bool operator<=(const V& a, const V& b) { return Cmp(a, b) <= 0; }
inline bool operator>(const V& a, const V& b) { return Cmp(a, b) > 0; }
inline bool operator>=(const V& a, const V& b) { return Cmp(a, b) >= 0; }
inline int Cat(const V& x) { return x.num() ? 0 : (x.t == 2 ? 1 : 2); }
struct KeyLess {   // porządek do zbiorów (jak klucze haszujące w Pythonie: 1 == 1.0)
    bool operator()(const V& a, const V& b) const {
        int ca = Cat(a), cb = Cat(b);
        if (ca != cb) return ca < cb;
        if (ca < 2) return Cmp(a, b) < 0;
        size_t n = std::min(a.a.size(), b.a.size());
        for (size_t k = 0; k < n; ++k) {
            if ((*this)(a.a[k], b.a[k])) return true;
            if ((*this)(b.a[k], a.a[k])) return false;
        }
        return a.a.size() < b.a.size();
    }
};
inline bool In(const V& x, const V& c) {
    if (c.lst()) { for (const V& e : c.a) if (Eq(e, x)) return true; return false; }
    if (c.t == 2) {
        if (x.t != 2) fail("„in” z napisem wymaga napisu po lewej stronie, a jest " + tn(x) + ".");
        return c.s.find(x.s) != std::string::npos;
    }
    fail("„in” wymaga listy albo napisu po prawej stronie, a jest " + tn(c) + ".");
}
inline V Idx(const V& a, const V& iv) {
    if (iv.t != 0) fail("Indeks musi być liczbą całkowitą, a jest " + tn(iv) + ".");
    long long n;
    if (a.t == 2) n = (long long)a.s.size(); else if (a.lst()) n = (long long)a.a.size();
    else fail("Indeksować można listę albo napis, a nie " + tn(a) + ".");
    long long k = iv.i < 0 ? iv.i + n : iv.i;
    if (k < 0 || k >= n) fail("Indeks " + std::to_string(iv.i) + " poza zakresem (długość " + std::to_string(n) + ").");
    if (a.t == 2) return V(std::string(1, a.s[k]));
    return a.a[k];
}
inline long long Times(const V& k) {
    long long x = I(k, "repeat");
    if (x < 0) fail("repeat: liczba powtórzeń nie może być ujemna.");
    return x;
}
inline std::vector<V> Items(const V& x) {
    if (x.t == 2) { std::vector<V> r; for (char c : x.s) r.push_back(V(std::string(1, c))); return r; }
    return Lst(x, "for … in");
}

// ---- wypisywanie
inline bool Rowsy(const V& x) {
    if (x.t == 4) return true;
    if (x.t != 3 || x.a.empty()) return false;
    for (const V& e : x.a) if (!e.lst()) return false;
    return true;
}
inline std::string Fmt(const V& x, const std::string& sep = " ") {
    switch (x.t) {
        case 0: return std::to_string(x.i);
        case 1: { char buf[400]; std::snprintf(buf, sizeof buf, "%.*f", x.d, x.f); return buf; }
        case 2: return x.s;
        case 3: case 4: {
            bool r = Rowsy(x);
            std::string out;
            for (size_t k = 0; k < x.a.size(); ++k) { if (k) out += r ? "\n" : sep; out += Fmt(x.a[k], sep); }
            return out;
        }
    }
    fail("Nie można wypisać wartości (zmienna bez wartości?).");
}
inline void Print(const std::vector<V>& items, const V& sep = V(" "), const V& end = V("\n")) {
    const std::string& sp = S(sep, "print … sep=");
    const std::string& en = S(end, "print … end=");
    std::string s;
    size_t parts = 0;
    for (size_t k = 0; k < items.size(); ++k) {
        if (items[k].lst() && items[k].a.empty()) continue;   // pusta lista nic nie wypisuje
        if (parts++) s += sp;
        s += Fmt(items[k], sp);
    }
    if (!items.empty() && parts == 0) return;
    s += en;
    Emit(s);
}
[[noreturn]] inline void Fail(const V& msg) {
    fail(std::string("Asercja nie jest spełniona") + (msg.t == -1 ? "." : ": " + Fmt(msg)));
}

// ---- liczby
inline V Int(const V& lo_, const V& hi_) {
    long long lo = I(lo_, "int(lo, hi)"), hi = I(hi_, "int(lo, hi)");
    if (lo > hi) fail("int(" + std::to_string(lo) + ", " + std::to_string(hi) + "): dolna granica jest większa od górnej.");
    if ((unsigned long long)hi - (unsigned long long)lo >= (1ULL << 63)) fail("int(lo, hi): zakres jest za szeroki (najwyżej 2^63 liczb).");
    return V(R.randint(lo, hi));
}
//@@lib
inline V Float(const V& lo_, const V& hi_, const V& digits_ = V(2)) {
    double lo = Num(lo_, "float(lo, hi)").dv(), hi = Num(hi_, "float(lo, hi)").dv();
    long long d = I(digits_, "float(…, digits)");
    if (d < 0 || d > 12) fail("float(…, digits): liczba cyfr musi być z zakresu 0..12.");
    double scale = 1; for (long long k = 0; k < d; ++k) scale *= 10;
    if (std::fabs(lo) * scale > 9007199254740992.0 || std::fabs(hi) * scale > 9007199254740992.0)
        fail("float(lo, hi, digits): za duży zakres przy tylu cyfrach po przecinku.");
    long long a = (long long)std::ceil(lo * scale - 1e-9), b = (long long)std::floor(hi * scale + 1e-9);
    if (a > b) fail("float(lo, hi, digits): pusty przedział.");
    return V::F((double)R.randint(a, b) / scale, (int)d);
}
inline V Chance(const V& p) { double x = Num(p, "chance(p)").dv(); return V(R.random() < x ? 1 : 0); }
inline V Choice(std::vector<V> items, const V& weights = V(0)) {
    if (items.size() == 1 && items[0].lst()) { std::vector<V> c = items[0].a; items = c; }
    else if (items.size() == 1 && items[0].t == 2) { std::vector<V> c; for (char ch : items[0].s) c.push_back(V(std::string(1, ch))); items = c; }
    if (items.empty()) fail("choice(): brak elementów do wyboru.");
    if (!(weights.t == 0 && weights.i == 0)) {
        const std::vector<V>& w = Lst(weights, "choice(…, weights=)");
        if (w.size() != items.size()) fail("choice(): " + std::to_string(items.size()) + " elementów, a " + std::to_string(w.size()) + " wag.");
        bool allint = true;
        for (const V& x : w) { Num(x, "choice(…, weights=)"); if (x.dv() < 0) fail("choice(): wagi nie mogą być ujemne."); if (x.t != 0) allint = false; }
        if (allint) {
            long long tot = 0; for (const V& x : w) tot += x.i;
            if (tot <= 0) fail("choice(): suma wag musi być dodatnia.");
            long long r = (long long)R.below((unsigned long long)tot), acc = 0;
            for (size_t k = 0; k < w.size(); ++k) { acc += w[k].i; if (r < acc) return items[k]; }
        }
        double tot = 0.0; for (const V& x : w) tot += x.dv();
        if (tot <= 0) fail("choice(): suma wag musi być dodatnia.");
        double r = R.random() * tot, acc = 0.0;
        for (size_t k = 0; k < w.size(); ++k) { acc += w[k].dv(); if (r < acc) return items[k]; }
        return items.back();
    }
    return items[R.below(items.size())];
}

// ---- listy i matematyka
inline V Array(const V& n_, const std::function<V()>& gen) {
    long long n = Size(n_, "array(n, …)");
    std::vector<V> r; r.reserve(n);
    for (long long k = 0; k < n; ++k) r.push_back(gen());
    return V::L(std::move(r));
}
inline V Matrix(const V& r_, const V& c_, const std::function<V()>& gen) {
    long long r = Size(r_, "matrix(r, …)"), c = Size(c_, "matrix(…, c, …)");
    if (r * c > MAX_N) fail("matrix(): za duża macierz.");
    std::vector<V> rows;
    for (long long i = 0; i < r; ++i) { std::vector<V> row; for (long long j = 0; j < c; ++j) row.push_back(gen()); rows.push_back(V::L(std::move(row))); }
    return V::L(std::move(rows), true);
}
inline V Range(const V& lo_, const V& hi_) {
    long long lo = I(lo_, "range(lo, hi)"), hi = I(hi_, "range(lo, hi)");
    if (hi - lo + 1 > MAX_N) fail("range(): za długi zakres.");
    std::vector<V> r; for (long long x = lo; x <= hi; ++x) r.push_back(V(x));
    return V::L(std::move(r));
}
inline V Keep(const V& like, std::vector<V> xs) { return V::L(std::move(xs), like.t == 4); }
inline V Sorted(const V& x, const V& desc = V(0)) {
    std::vector<V> r = Lst(x, "sorted(x)");
    if (T(desc)) std::stable_sort(r.begin(), r.end(), [](const V& a, const V& b) { return Cmp(b, a) < 0; });
    else std::stable_sort(r.begin(), r.end(), [](const V& a, const V& b) { return Cmp(a, b) < 0; });
    return Keep(x, std::move(r));
}
inline V Reverse(const V& x) {
    if (x.t == 2) return V(std::string(x.s.rbegin(), x.s.rend()));
    std::vector<V> r = Lst(x, "reverse(x)"); std::reverse(r.begin(), r.end()); return Keep(x, std::move(r));
}
inline V Shuffle(const V& x) {
    if (x.t == 2) { std::vector<char> c(x.s.begin(), x.s.end()); R.shuffle(c); return V(std::string(c.begin(), c.end())); }
    std::vector<V> r = Lst(x, "shuffle(x)"); R.shuffle(r); return Keep(x, std::move(r));
}
inline V Unique(const V& x) {
    if (x.t == 2) { std::string out; for (char c : x.s) if (out.find(c) == std::string::npos) out += c; return V(out); }
    std::set<V, KeyLess> seen; std::vector<V> out;
    for (const V& e : Lst(x, "unique(x)")) if (seen.insert(e).second) out.push_back(e);
    return Keep(x, std::move(out));
}
inline V Len(const V& x) {
    if (x.t == 2) return V((long long)x.s.size());
    if (x.lst()) return V((long long)x.a.size());
    fail("len(x): oczekiwano listy albo napisu, a jest " + tn(x) + ".");
}
inline V Sum(const V& x) {
    V acc(0);
    for (const V& e : Lst(x, "sum(x)")) { Num(e, "sum(x)"); acc = Add(acc, e); }
    return acc;
}
inline V MinMax(std::vector<V> items, const char* what, int sign) {
    if (items.size() == 1 && items[0].lst()) { std::vector<V> c = items[0].a; items = c; }
    if (items.empty()) fail(std::string(what) + "(): pusta lista.");
    V best = items[0];
    for (size_t k = 1; k < items.size(); ++k) if (sign < 0 ? Cmp(items[k], best) < 0 : Cmp(items[k], best) > 0) best = items[k];
    return best;
}
inline V Min(std::vector<V> items) { return MinMax(std::move(items), "min", -1); }
inline V Max(std::vector<V> items) { return MinMax(std::move(items), "max", 1); }
inline V Count(const V& x, const V& v) {
    if (x.t == 2) {
        const std::string& p = S(v, "count(napis, v)");
        if (p.empty()) fail("count(): szukany napis nie może być pusty.");
        long long c = 0; size_t pos = 0;
        while ((pos = x.s.find(p, pos)) != std::string::npos) { ++c; pos += p.size(); }
        return V(c);
    }
    long long c = 0; for (const V& e : Lst(x, "count(x, v)")) if (Eq(e, v)) ++c;
    return V(c);
}
inline V Put(const V& x, const V& i_, const V& v) {
    long long i = I(i_, "put(x, i, v)");
    if (x.t == 2) {
        long long n = (long long)x.s.size(); if (i < 0) i += n;
        if (i < 0 || i >= n) fail("put(): indeks poza napisem.");
        return V(x.s.substr(0, i) + S(v, "put(napis, i, v)") + x.s.substr(i + 1));
    }
    std::vector<V> c = Lst(x, "put(x, i, v)");
    long long n = (long long)c.size(); if (i < 0) i += n;
    if (i < 0 || i >= n) fail("put(): indeks poza listą.");
    c[i] = v; return Keep(x, std::move(c));
}
inline V Flat(const V& x) {
    std::vector<V> out;
    for (const V& e : Lst(x, "flat(x)")) { if (e.lst()) out.insert(out.end(), e.a.begin(), e.a.end()); else out.push_back(e); }
    return V::L(std::move(out));
}
inline V Rows(const V& x) {
    if (x.t == 2) { std::vector<V> r; for (char c : x.s) r.push_back(V(std::string(1, c))); return V::L(std::move(r), true); }
    return V::L(Lst(x, "rows(x)"), true);
}
inline V Lines(const V& x) { return Rows(x); }
inline V Join(const V& x, const V& sep = V("")) {
    const std::string& sp = S(sep, "join(…, sep)");
    std::string out;
    if (x.t == 2) { for (size_t k = 0; k < x.s.size(); ++k) { if (k) out += sp; out += x.s[k]; } return V(out); }
    const std::vector<V>& a = Lst(x, "join(x)");
    for (size_t k = 0; k < a.size(); ++k) { if (k) out += sp; out += Fmt(a[k], " "); }
    return V(out);
}
inline V Str(const V& x) { return V(Fmt(x, " ")); }
inline long long Chk(double f) {
    if (!(f >= -9223372036854775808.0 && f < 9223372036854775808.0)) fail(OVF);
    return (long long)f;
}
inline V Abs(const V& x) {
    Num(x, "abs(x)");
    if (x.t == 0) { if (x.i == LLONG_MIN) fail(OVF); return V(x.i < 0 ? -x.i : x.i); }
    return V::F(std::fabs(x.f), x.d);
}
inline V Floor(const V& x) { Num(x, "floor(x)"); return x.t == 0 ? x : V(Chk(std::floor(x.f))); }
inline V Ceil(const V& x) { Num(x, "ceil(x)"); return x.t == 0 ? x : V(Chk(std::ceil(x.f))); }
inline V Round(const V& x) { Num(x, "round(x)"); return x.t == 0 ? x : V(Chk(std::nearbyint(x.f))); }
inline V Sqrt(const V& x) { Num(x, "sqrt(x)"); if (x.dv() < 0) fail("sqrt(x): pierwiastek z liczby ujemnej."); return V::F(std::sqrt(x.dv()), 6); }
inline long long gcdll(long long a, long long b) { if (a < 0) a = -a; if (b < 0) b = -b; while (b) { long long t = a % b; a = b; b = t; } return a; }
inline V Gcd(const V& a, const V& b) { return V(gcdll(I(a, "gcd(a, b)"), I(b, "gcd(a, b)"))); }
inline V Lcm(const V& a_, const V& b_) {
    long long a = I(a_, "lcm(a, b)"), b = I(b_, "lcm(a, b)");
    if (a == 0 || b == 0) return V(0);
    long long g = gcdll(a, b), r;
    if (__builtin_mul_overflow(a / g, b, &r)) fail(OVF);
    if (r == LLONG_MIN) fail(OVF);
    return V(r < 0 ? -r : r);
}

//@@struct
// ---- wspólne dla struktur
typedef std::pair<long long, long long> E;
inline std::string lower_ascii(std::string s) { for (char& c : s) if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a'); return s; }
inline std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\n\r"), b = s.find_last_not_of(" \t\n\r");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}
inline bool Flag(const V& x) { return T(x); }
inline bool WeightRange(const V& w, long long& lo, long long& hi) {
    if (w.num() && w.dv() == 0) return false;
    if (w.lst()) {
        if (w.a.size() != 2) fail("Wagi podaj jako parę (min, max), np. weights=(1, 100).");
        if (w.a[0].t != 0 || w.a[1].t != 0) fail("Wagi muszą być liczbami całkowitymi.");
        lo = w.a[0].i; hi = w.a[1].i;
    } else {
        if (w.t != 0) fail("Wagi muszą być liczbami całkowitymi.");
        lo = 1; hi = w.i;
    }
    if (lo > hi) fail("Pusty przedział wag [" + std::to_string(lo) + ", " + std::to_string(hi) + "].");
    return true;
}
inline V Finish(const std::vector<E>& edges, long long base, const V& weights) {
    long long lo = 0, hi = 0;
    bool w = WeightRange(weights, lo, hi);
    std::vector<V> out; out.reserve(edges.size());
    for (const E& e : edges) {
        std::vector<V> row = {V(e.first + base), V(e.second + base)};
        if (w) row.push_back(V(R.randint(lo, hi)));
        out.push_back(V::L(std::move(row)));
    }
    return V::L(std::move(out), true);
}
inline void CheckN(long long n, const char* name, long long lo) {
    if (n < lo) fail(std::string(name) + " = " + std::to_string(n) + " — musi być co najmniej " + std::to_string(lo) + ".");
    if (n > MAX_N) fail(std::string(name) + " = " + std::to_string(n) + " — za dużo.");
}
inline std::vector<long long> DistinctInts(long long n, long long lo, long long hi) {
    long long size = hi - lo + 1;
    if (n > size) fail("Przedział [" + std::to_string(lo) + ", " + std::to_string(hi) + "] ma tylko " + std::to_string(size < 0 ? 0 : size) + " liczb, a potrzeba " + std::to_string(n) + " różnych.");
    std::vector<long long> out;
    if (n == 0) return out;
    if (size <= 4 * n) {
        std::vector<long long> pool; for (long long x = lo; x <= hi; ++x) pool.push_back(x);
        for (long long i = 0; i < n; ++i) { long long j = i + (long long)R.below((unsigned long long)(size - i)); std::swap(pool[i], pool[j]); }
        pool.resize(n); return pool;
    }
    std::unordered_set<long long> seen;
    while ((long long)out.size() < n) {
        long long x = lo + (long long)R.below((unsigned long long)size);
        if (seen.count(x)) continue;
        seen.insert(x); out.push_back(x);
    }
    return out;
}
inline V IntList(const std::vector<long long>& xs) {
    std::vector<V> r; r.reserve(xs.size()); for (long long x : xs) r.push_back(V(x)); return V::L(std::move(r));
}

//@@str
inline int ccls(char c) { if (c >= 'a' && c <= 'z') return 1; if (c >= 'A' && c <= 'Z') return 2; if (c >= '0' && c <= '9') return 3; return 0; }
inline std::string Expand(const std::string& spec) {
    if (spec == "lower") return "abcdefghijklmnopqrstuvwxyz";
    if (spec == "upper") return "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    if (spec == "letters") return "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    if (spec == "digits") return "0123456789";
    if (spec == "alnum") return "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    if (spec == "binary") return "01";
    if (spec == "dna") return "ACGT";
    if (spec == "hex") return "0123456789abcdef";
    std::string out; size_t i = 0;
    while (i < spec.size()) {
        char c = spec[i];
        if (i + 2 < spec.size() && spec[i + 1] == '-' && ccls(c) && ccls(c) == ccls(spec[i + 2]) && c <= spec[i + 2]) {
            for (int k = c; k <= spec[i + 2]; ++k) out += (char)k;
            i += 3;
        } else { out += c; ++i; }
    }
    if (out.empty()) fail("Pusty alfabet.");
    return out;
}
inline std::string RandomString(long long n, const std::string& alphabet) {
    CheckN(n, "długość", 0);
    std::string a = Expand(alphabet), s;
    s.reserve(n);
    for (long long k = 0; k < n; ++k) s += a[R.below(a.size())];
    return s;
}
inline V Char(const V& alphabet = V("a-z")) { std::string a = Expand(S(alphabet, "char(alphabet)")); return V(std::string(1, a[R.below(a.size())])); }
inline V String(const V& n, const V& alphabet = V("a-z")) { return V(RandomString(Size(n, "string(n)"), S(alphabet, "string(…, alphabet)"))); }
inline V Palindrome(const V& n_, const V& alphabet = V("a-z")) {
    long long n = Size(n_, "palindrome(n)");
    std::string half = RandomString((n + 1) / 2, S(alphabet, "palindrome(…, alphabet)"));
    std::string tail = half.substr(0, n / 2); std::reverse(tail.begin(), tail.end());
    return V(half + tail);
}
inline V Periodic(const V& n_, const V& period_, const V& alphabet = V("a-z")) {
    long long n = Size(n_, "periodic(n)"), period = I(period_, "periodic(…, period)");
    if (period < 1) fail("periodic(): okres musi być ≥ 1.");
    long long p = std::min(period, n);
    if (p == 0) return V("");
    std::string base = RandomString(p, S(alphabet, "periodic(…, alphabet)")), s;
    for (long long k = 0; k < n; ++k) s += base[k % p];
    return V(s);
}
inline V Antihash(const V& n_, const V& alphabet = V("ab")) {
    long long n = Size(n_, "antihash(n)");
    std::string a = Expand(S(alphabet, "antihash(…, alphabet)"));
    if (a.size() < 2) fail("Thue–Morse potrzebuje alfabetu z co najmniej 2 znakami.");
    std::string s; s.reserve(n);
    for (long long i = 0; i < n; ++i) s += (__builtin_popcountll((unsigned long long)i) & 1) ? a[1] : a[0];
    return V(s);
}

//@@seq
inline V Distinct(const V& n, const V& lo, const V& hi) {
    return IntList(DistinctInts(Size(n, "distinct(n, …)"), I(lo, "distinct(…, lo, hi)"), I(hi, "distinct(…, lo, hi)")));
}
inline V Perm(const V& n_, const V& base_ = V(1)) {
    long long n = Size(n_, "perm(n)"), base = I(base_, "perm(…, base)");
    std::vector<long long> p; for (long long k = 0; k < n; ++k) p.push_back(base + k);
    R.shuffle(p); return IntList(p);
}
inline std::string SeqKind(const std::string& name) {
    std::string s = lower_ascii(trim(name));
    const char* canon[] = {"uniform", "sorted", "desc", "nearly", "repeats", "equal", "zigzag", "distinct"};
    for (const char* c : canon) if (s == c) return s;
    const char* al[][2] = {{"random", "uniform"}, {"losowy", "uniform"}, {"jednostajny", "uniform"}, {"asc", "sorted"},
        {"rosnacy", "sorted"}, {"rosnący", "sorted"}, {"malejacy", "desc"}, {"malejący", "desc"}, {"reversed", "desc"},
        {"prawie", "nearly"}, {"nearly_sorted", "nearly"}, {"almost", "nearly"}, {"powtorzenia", "repeats"},
        {"powtórzenia", "repeats"}, {"few", "repeats"}, {"rowne", "equal"}, {"równe", "equal"}, {"same", "equal"},
        {"pila", "zigzag"}, {"piła", "zigzag"}, {"alternating", "zigzag"}, {"rozne", "distinct"}, {"różne", "distinct"},
        {"unique", "distinct"}};
    for (auto& p : al) if (s == p[0]) return p[1];
    fail("Nieznany rodzaj ciągu „" + name + "”. Dostępne: uniform, sorted, desc, nearly, repeats, equal, zigzag, distinct.");
}
inline V Seq(const V& n_, const V& lo_, const V& hi_, const V& kind_ = V("uniform")) {
    long long n = Size(n_, "seq(n, …)"), lo = I(lo_, "seq(…, lo, hi)"), hi = I(hi_, "seq(…, lo, hi)");
    std::string kind = SeqKind(S(kind_, "seq(…, kind)"));
    if (lo > hi) fail("Pusty przedział [" + std::to_string(lo) + ", " + std::to_string(hi) + "].");
    if (kind == "distinct") return IntList(DistinctInts(n, lo, hi));
    std::vector<long long> a;
    if (kind == "equal") { if (n == 0) return V::L({}); long long v = R.randint(lo, hi); a.assign(n, v); return IntList(a); }
    if (kind == "repeats") {
        long long k = (long long)std::sqrt((double)n); while (k * k > n) --k; while ((k + 1) * (k + 1) <= n) ++k;
        if (k < 1) k = 1;
        std::vector<long long> pool; for (long long j = 0; j < k; ++j) pool.push_back(R.randint(lo, hi));
        for (long long j = 0; j < n; ++j) a.push_back(pool[R.below(k)]);
        return IntList(a);
    }
    for (long long j = 0; j < n; ++j) a.push_back(R.randint(lo, hi));
    if (kind == "uniform") return IntList(a);
    std::sort(a.begin(), a.end());
    if (kind == "desc") std::reverse(a.begin(), a.end());
    else if (kind == "nearly") {
        if (n >= 2) for (long long s = 0, sw = std::max(1LL, n / 20); s < sw; ++s) {
            long long i = (long long)R.below(n), j = (long long)R.below(n); std::swap(a[i], a[j]);
        }
    } else if (kind == "zigzag") {
        std::vector<long long> out; long long i = 0, j = n - 1;
        while (i <= j) { out.push_back(a[i]); if (i != j) out.push_back(a[j]); ++i; --j; }
        a = out;
    }
    return IntList(a);
}

//@@tree
inline std::string TreeShape(const std::string& name) {
    std::string s = lower_ascii(trim(name));
    const char* canon[] = {"random", "parent", "line", "star", "binary", "caterpillar", "deep"};
    for (const char* c : canon) if (s == c) return s;
    const char* al[][2] = {{"losowe", "random"}, {"losowy", "random"}, {"prufer", "random"}, {"uniform", "random"},
        {"rodzic", "parent"}, {"random_parent", "parent"}, {"shallow", "parent"}, {"plytkie", "parent"}, {"płytkie", "parent"},
        {"path", "line"}, {"sciezka", "line"}, {"ścieżka", "line"}, {"lancuch", "line"}, {"łańcuch", "line"}, {"chain", "line"},
        {"gwiazda", "star"}, {"binarne", "binary"}, {"bin", "binary"}, {"heap", "binary"}, {"gasienica", "caterpillar"},
        {"gąsienica", "caterpillar"}, {"glebokie", "deep"}, {"głębokie", "deep"}};
    for (auto& p : al) if (s == p[0]) return p[1];
    fail("Nieznany kształt drzewa „" + name + "”. Dostępne: random, parent, line, star, binary, caterpillar, deep.");
}
inline std::vector<E> Prufer(long long n) {
    std::vector<E> edges;
    if (n <= 1) return edges;
    if (n == 2) { edges.push_back(E(0, 1)); return edges; }
    std::vector<long long> code(n - 2), degree(n, 1);
    for (long long k = 0; k < n - 2; ++k) code[k] = (long long)R.below(n);
    for (long long x : code) degree[x]++;
    long long ptr = 0;
    while (degree[ptr] != 1) ++ptr;
    long long leaf = ptr;
    for (long long x : code) {
        edges.push_back(E(x, leaf));
        degree[leaf]--; degree[x]--;
        if (degree[x] == 1 && x < ptr) leaf = x;
        else { ++ptr; while (degree[ptr] != 1) ++ptr; leaf = ptr; }
    }
    edges.push_back(E(n - 1, leaf));
    return edges;
}
inline std::vector<E> TreeRaw(long long n, const std::string& shape) {
    if (shape == "random") return Prufer(n);
    std::vector<E> e;
    if (shape == "parent") { for (long long i = 1; i < n; ++i) e.push_back(E((long long)R.below(i), i)); }
    else if (shape == "line") { for (long long i = 1; i < n; ++i) e.push_back(E(i - 1, i)); }
    else if (shape == "star") { for (long long i = 1; i < n; ++i) e.push_back(E(0, i)); }
    else if (shape == "binary") { for (long long i = 1; i < n; ++i) e.push_back(E((i - 1) / 2, i)); }
    else if (shape == "caterpillar") {
        long long s = std::max(1LL, (n + 1) / 2);
        for (long long i = 1; i < n; ++i) e.push_back(i < s ? E(i - 1, i) : E((long long)R.below(s), i));
    } else if (shape == "deep") { for (long long i = 1; i < n; ++i) e.push_back(E(i - 1 - (long long)R.below(std::min(i, 3LL)), i)); }
    return e;
}
inline std::vector<E> Relabel(long long n, const std::vector<E>& edges, bool flip) {
    std::vector<long long> perm(n); for (long long k = 0; k < n; ++k) perm[k] = k;
    R.shuffle(perm);
    std::vector<E> e; e.reserve(edges.size());
    for (const E& x : edges) e.push_back(E(perm[x.first], perm[x.second]));
    R.shuffle(e);
    if (flip) for (E& x : e) if (R.bits(1)) std::swap(x.first, x.second);
    return e;
}
inline V Tree(const V& n_, const V& shape_ = V("random"), const V& weights = V(0), const V& base_ = V(1), const V& shuffle_ = V(1)) {
    long long n = Size(n_, "tree(n)");
    std::string shape = S(shape_, "tree(…, shape)");
    long long base = I(base_, "base");
    bool sh = Flag(shuffle_);
    CheckN(n, "n", 1);
    shape = TreeShape(shape);
    std::vector<E> e = TreeRaw(n, shape);
    if (sh) e = Relabel(n, e, true);
    return Finish(e, base, weights);
}
inline V Parents(const V& n_, const V& shape_ = V("random"), const V& base_ = V(1), const V& shuffle_ = V(1)) {
    long long n = Size(n_, "parents(n)"), base = I(base_, "base");
    std::string shape = S(shape_, "parents(…, shape)");
    bool sh = Flag(shuffle_);
    CheckN(n, "n", 1);
    shape = TreeShape(shape);
    std::vector<E> edges = TreeRaw(n, shape);
    std::vector<long long> par(n, -1);
    if (shape != "random" && !sh) {
        for (const E& e : edges) par[e.second] = e.first;
    } else {
        std::vector<std::vector<long long>> adj(n);
        for (const E& e : edges) { adj[e.first].push_back(e.second); adj[e.second].push_back(e.first); }
        std::vector<char> seen(n, 0); seen[0] = 1;
        std::vector<long long> p0(n, -1), order;
        if (sh) {
            std::vector<long long> avail = {0};
            while (!avail.empty()) {
                long long j = (long long)R.below(avail.size());
                long long v = avail[j]; avail[j] = avail.back(); avail.pop_back();
                order.push_back(v);
                for (long long w : adj[v]) if (!seen[w]) { seen[w] = 1; p0[w] = v; avail.push_back(w); }
            }
        } else {
            order.push_back(0);
            for (size_t h = 0; h < order.size(); ++h) {
                long long v = order[h];
                for (long long w : adj[v]) if (!seen[w]) { seen[w] = 1; p0[w] = v; order.push_back(w); }
            }
        }
        std::vector<long long> lab(n, 0);
        for (size_t k = 0; k < order.size(); ++k) lab[order[k]] = (long long)k;
        for (long long v = 0; v < n; ++v) if (p0[v] >= 0) par[lab[v]] = lab[p0[v]];
    }
    std::vector<V> out; for (long long k = 1; k < n; ++k) out.push_back(V(par[k] + base));
    return V::L(std::move(out));
}
inline V Forest(const V& n_, const V& k_, const V& weights = V(0), const V& base_ = V(1)) {
    long long n = Size(n_, "forest(n, k)"), k = I(k_, "forest(n, k)"), base = I(base_, "base");
    CheckN(n, "n", 1); CheckN(k, "k", 1);
    if (k > n) fail("Las: liczba drzew k = " + std::to_string(k) + " większa niż liczba wierzchołków n = " + std::to_string(n) + ".");
    std::vector<long long> order(n); for (long long j = 0; j < n; ++j) order[j] = j;
    R.shuffle(order);
    std::vector<long long> cuts;
    if (k > 1) { cuts = DistinctInts(k - 1, 1, n - 1); std::sort(cuts.begin(), cuts.end()); }
    std::vector<long long> bounds = {0}; bounds.insert(bounds.end(), cuts.begin(), cuts.end()); bounds.push_back(n);
    std::vector<E> edges;
    for (long long g = 0; g < k; ++g) {
        long long a = bounds[g], b = bounds[g + 1];
        for (const E& e : Prufer(b - a)) edges.push_back(E(order[a + e.first], order[a + e.second]));
    }
    R.shuffle(edges);
    for (E& x : edges) if (R.bits(1)) std::swap(x.first, x.second);
    return Finish(edges, base, weights);
}

//@@graph
inline long long MaxEdges(long long n, bool directed, bool loops) {
    long long m = directed ? n * (n - 1) : n * (n - 1) / 2;
    return m + (loops ? n : 0);
}
inline void SamplePairs(long long n, long long k, std::unordered_set<long long>& used, bool directed, bool loops, std::vector<E>& edges) {
    if (k <= 0) return;
    long long avail = MaxEdges(n, directed, loops) - (long long)used.size();
    if (2 * k > avail) {
        std::vector<E> cand;
        for (long long u = 0; u < n; ++u)
            for (long long v = directed ? 0 : u; v < n; ++v) {
                if (u == v && !loops) continue;
                if (used.count(u * n + v)) continue;
                cand.push_back(E(u, v));
            }
        for (long long i = 0; i < k; ++i) {
            long long j = i + (long long)R.below((unsigned long long)((long long)cand.size() - i));
            std::swap(cand[i], cand[j]);
            edges.push_back(cand[i]);
        }
        return;
    }
    while (k > 0) {
        long long u = (long long)R.below(n), v = (long long)R.below(n);
        if (u == v && !loops) continue;
        long long key = (directed || u <= v) ? u * n + v : v * n + u;
        if (used.count(key)) continue;
        used.insert(key); edges.push_back(E(u, v)); --k;
    }
}
inline V Graph(const V& n_, const V& m_, const V& connected_ = V(1), const V& directed_ = V(0), const V& weights = V(0),
               const V& loops_ = V(0), const V& multi_ = V(0), const V& base_ = V(1)) {
    long long n = Size(n_, "graph(n, m)"), m = Size(m_, "graph(n, m)");
    bool connected = Flag(connected_), directed = Flag(directed_), loops = Flag(loops_), multi = Flag(multi_);
    long long base = I(base_, "base");
    CheckN(n, "n", 1); CheckN(m, "m", 0);
    if (connected && m < n - 1) fail("Graf spójny o n = " + std::to_string(n) + " wierzchołkach potrzebuje co najmniej n−1 = " + std::to_string(n - 1) + " krawędzi (jest m = " + std::to_string(m) + ").");
    long long mx = MaxEdges(n, directed, loops);
    if (!multi && m > mx) fail("Za dużo krawędzi: graf o n = " + std::to_string(n) + " wierzchołkach ma ich najwyżej " + std::to_string(mx) + " (jest m = " + std::to_string(m) + "). Zmniejsz m albo dodaj multi=1.");
    if (multi && m > 0 && n == 1 && !loops) fail("Graf z jednym wierzchołkiem bez pętli nie może mieć krawędzi.");
    std::vector<E> edges;
    std::unordered_set<long long> used;
    if (connected && n > 1) {
        for (E e : Prufer(n)) {
            if (directed && R.bits(1)) std::swap(e.first, e.second);
            edges.push_back(e);
            long long u = e.first, v = e.second;
            used.insert((directed || u <= v) ? u * n + v : v * n + u);
        }
    }
    long long k = m - (long long)edges.size();
    if (multi) {
        for (long long j = 0; j < k; ++j) {
            long long u, v;
            while (true) { u = (long long)R.below(n); v = (long long)R.below(n); if (u != v || loops) break; }
            edges.push_back(E(u, v));
        }
    } else SamplePairs(n, k, used, directed, loops, edges);
    edges = Relabel(n, edges, !directed);
    return Finish(edges, base, weights);
}
inline V Dag(const V& n_, const V& m_, const V& connected_ = V(0), const V& weights = V(0), const V& base_ = V(1)) {
    long long n = Size(n_, "dag(n, m)"), m = Size(m_, "dag(n, m)");
    bool connected = Flag(connected_);
    long long base = I(base_, "base");
    CheckN(n, "n", 1); CheckN(m, "m", 0);
    long long mx = n * (n - 1) / 2;
    if (m > mx) fail("DAG o n = " + std::to_string(n) + " wierzchołkach ma najwyżej n(n−1)/2 = " + std::to_string(mx) + " krawędzi (jest m = " + std::to_string(m) + ").");
    if (connected && m < n - 1) fail("Spójny DAG o n = " + std::to_string(n) + " wierzchołkach potrzebuje co najmniej " + std::to_string(n - 1) + " krawędzi.");
    std::vector<long long> order(n); for (long long j = 0; j < n; ++j) order[j] = j;
    R.shuffle(order);
    std::vector<E> pe;
    std::unordered_set<long long> used;
    if (connected) for (long long j = 1; j < n; ++j) { long long i = (long long)R.below(j); pe.push_back(E(i, j)); used.insert(i * n + j); }
    SamplePairs(n, m - (long long)pe.size(), used, false, false, pe);
    std::vector<E> edges;
    for (const E& e : pe) edges.push_back(E(order[std::min(e.first, e.second)], order[std::max(e.first, e.second)]));
    R.shuffle(edges);
    return Finish(edges, base, weights);
}
inline V Bipartite(const V& n1_, const V& n2_, const V& m_, const V& weights = V(0), const V& base_ = V(1), const V& shift_ = V(0)) {
    long long n1 = Size(n1_, "bipartite(n1, …)"), n2 = Size(n2_, "bipartite(…, n2, …)"), m = Size(m_, "bipartite(…, m)");
    long long base = I(base_, "base");
    bool shift = Flag(shift_);
    CheckN(n1, "n1", 1); CheckN(n2, "n2", 1); CheckN(m, "m", 0);
    long long mx = n1 * n2;
    if (m > mx) fail("Graf dwudzielny ma najwyżej " + std::to_string(mx) + " krawędzi (jest m = " + std::to_string(m) + ").");
    std::vector<E> edges;
    if (2 * m > mx) {
        std::vector<E> cand; for (long long u = 0; u < n1; ++u) for (long long v = 0; v < n2; ++v) cand.push_back(E(u, v));
        for (long long i = 0; i < m; ++i) { long long j = i + (long long)R.below((unsigned long long)((long long)cand.size() - i)); std::swap(cand[i], cand[j]); edges.push_back(cand[i]); }
    } else {
        std::unordered_set<long long> used;
        while ((long long)edges.size() < m) {
            long long u = (long long)R.below(n1), v = (long long)R.below(n2);
            if (used.count(u * n2 + v)) continue;
            used.insert(u * n2 + v); edges.push_back(E(u, v));
        }
    }
    long long off = shift ? n1 : 0, lo = 0, hi = 0;
    bool w = WeightRange(weights, lo, hi);
    std::vector<V> out;
    for (const E& e : edges) {
        std::vector<V> row = {V(e.first + base), V(e.second + base + off)};
        if (w) row.push_back(V(R.randint(lo, hi)));
        out.push_back(V::L(std::move(row)));
    }
    return V::L(std::move(out), true);
}

//@@grid
inline V Grid(const V& rows_, const V& cols_, const V& alphabet_ = V("#."), const V& p_ = V(-1), const V& path_ = V(0)) {
    long long rows = Size(rows_, "grid(rows, …)"), cols = Size(cols_, "grid(…, cols)");
    double p = Num(p_, "grid(…, p)").dv();
    std::string alphabet = S(alphabet_, "grid(…, alphabet)");
    bool path = Flag(path_);
    CheckN(rows, "rows", 1); CheckN(cols, "cols", 1);
    if (rows * cols > MAX_N) fail("Siatka jest za duża.");
    std::string a = Expand(alphabet);
    size_t L = a.size();
    bool use_p = p >= 0 && L >= 2;
    std::vector<std::string> g(rows, std::string(cols, ' '));
    for (long long i = 0; i < rows; ++i)
        for (long long j = 0; j < cols; ++j) {
            if (use_p) {
                if (R.random() < p) g[i][j] = a[0];
                else if (L == 2) g[i][j] = a[1];
                else g[i][j] = a[1 + R.below(L - 1)];
            } else g[i][j] = a[R.below(L)];
        }
    if (path) {
        char fr = a[L - 1];
        long long i = 0, j = 0;
        g[0][0] = fr;
        while (i != rows - 1 || j != cols - 1) {
            if (i == rows - 1) ++j;
            else if (j == cols - 1) ++i;
            else if (R.bits(1)) ++i;
            else ++j;
            g[i][j] = fr;
        }
    }
    std::vector<V> out; for (auto& r : g) out.push_back(V(r));
    return V::L(std::move(out), true);
}

//@@misc
inline std::vector<E> Intervals(long long k, long long lo, long long hi, bool distinct) {
    if (lo > hi || (distinct && lo == hi)) fail("Za mały zakres [" + std::to_string(lo) + ", " + std::to_string(hi) + "] na przedziały.");
    std::vector<E> out;
    for (long long j = 0; j < k; ++j) {
        long long a = R.randint(lo, hi), b = R.randint(lo, hi);
        if (distinct) while (b == a) b = R.randint(lo, hi);
        out.push_back(a <= b ? E(a, b) : E(b, a));
    }
    return out;
}
inline V Pairs(const V& k_, const V& lo, const V& hi, const V& distinct = V(0)) {
    long long k = Size(k_, "pairs(k, …)");
    std::vector<E> e = Intervals(k, I(lo, "pairs(…, lo, hi)"), I(hi, "pairs(…, lo, hi)"), Flag(distinct));
    std::vector<V> out; for (const E& x : e) out.push_back(V::L({V(x.first), V(x.second)}));
    return V::L(std::move(out), true);
}
inline V Interval(const V& lo, const V& hi) {
    E x = Intervals(1, I(lo, "interval(lo, hi)"), I(hi, "interval(lo, hi)"), false)[0];
    return V::L({V(x.first), V(x.second)});
}
inline V Points(const V& k_, const V& lo_, const V& hi_, const V& distinct_ = V(1)) {
    long long k = Size(k_, "points(k, …)"), lo = I(lo_, "points(…, lo, hi)"), hi = I(hi_, "points(…, lo, hi)");
    bool distinct = Flag(distinct_);
    if (lo > hi) fail("Pusty przedział.");
    double side = (double)(hi - lo + 1);
    if (distinct && (double)k > side * side) fail("Nie ma tylu różnych punktów w tym zakresie.");
    std::set<E> seen; std::vector<V> out;
    while ((long long)out.size() < k) {
        long long x = R.randint(lo, hi), y = R.randint(lo, hi);
        if (distinct) { if (seen.count(E(x, y))) continue; seen.insert(E(x, y)); }
        out.push_back(V::L({V(x), V(y)}));
    }
    return V::L(std::move(out), true);
}
inline std::string QueryKind(const std::string& name) {
    std::string s = lower_ascii(trim(name));
    const char* canon[] = {"point", "range", "value", "point_value", "range_value", "pair", "none"};
    for (const char* c : canon) if (s == c) return s;
    const char* al[][2] = {{"punkt", "point"}, {"pozycja", "point"}, {"przedzial", "range"}, {"przedział", "range"},
        {"wartosc", "value"}, {"wartość", "value"}, {"para", "pair"}, {"brak", "none"}, {"pusty", "none"}, {"empty", "none"},
        {"set", "point_value"}, {"update", "point_value"}, {"add", "range_value"}, {"sum", "range"}, {"query", "range"}};
    for (auto& p : al) if (s == p[0]) return p[1];
    fail("Nieznany rodzaj zapytania „" + name + "”. Dostępne: point, range, value, point_value, range_value, pair, none.");
}
inline V Queries(const V& q_, const V& n_, const V& kinds_ = V("range point_value"), const V& lo_ = V(1), const V& hi_ = V(1000000000)) {
    long long q = Size(q_, "queries(q, …)"), n = I(n_, "queries(…, n)");
    std::string ks;
    if (kinds_.lst()) { for (size_t j = 0; j < kinds_.a.size(); ++j) { if (j) ks += " "; ks += S(kinds_.a[j], "queries(…, kinds)"); } }
    else ks = S(kinds_, "queries(…, kinds)");
    long long lo = I(lo_, "queries(…, lo)"), hi = I(hi_, "queries(…, hi)");
    CheckN(q, "q", 0); CheckN(n, "n", 1);
    std::vector<std::string> kinds; std::string cur;
    for (char c : ks) { if (c == ' ' || c == ',' || c == '\t' || c == '\n') { if (!cur.empty()) kinds.push_back(QueryKind(cur)); cur.clear(); } else cur += c; }
    if (!cur.empty()) kinds.push_back(QueryKind(cur));
    if (kinds.empty()) fail("Podaj co najmniej jeden rodzaj zapytania.");
    for (auto& k : kinds) if (k == "pair" && n < 2) fail("Zapytania „pair” wymagają n ≥ 2.");
    std::vector<V> out;
    for (long long j = 0; j < q; ++j) {
        long long t = (long long)R.below(kinds.size());
        const std::string& kd = kinds[t];
        std::vector<V> row = {V(t + 1)};
        if (kd == "point") row.push_back(V(R.randint(1, n)));
        else if (kd == "range" || kd == "range_value") {
            long long a = R.randint(1, n), b = R.randint(1, n);
            if (a <= b) { row.push_back(V(a)); row.push_back(V(b)); } else { row.push_back(V(b)); row.push_back(V(a)); }
            if (kd == "range_value") row.push_back(V(R.randint(lo, hi)));
        } else if (kd == "value") row.push_back(V(R.randint(lo, hi)));
        else if (kd == "point_value") { long long i = R.randint(1, n); long long x = R.randint(lo, hi); row.push_back(V(i)); row.push_back(V(x)); }
        else if (kd == "pair") { long long u = R.randint(1, n), v = R.randint(1, n - 1); if (v >= u) ++v; row.push_back(V(u)); row.push_back(V(v)); }
        out.push_back(V::L(std::move(row)));
    }
    return V::L(std::move(out), true);
}

//@@end
}  // namespace oig
"""

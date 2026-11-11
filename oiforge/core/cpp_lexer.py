"""Lekki lekser C++ (tokeny z linią/kolumną) i pomocnicze analizy strukturalne.

Nie jest pełnym parserem C++, ale poprawnie obsługuje komentarze, literały (także raw strings), preprocesor,
operatory wieloznakowe i pozwala znaleźć funkcje, bloki i pary nawiasów — wystarcza do lintów, analiz i formatowania.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORDS = frozenset("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t char32_t class compl
concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype default delete
do double dynamic_cast else enum explicit export extern false float for friend goto if inline int long mutable
namespace new noexcept not not_eq nullptr operator or or_eq private protected public register reinterpret_cast
requires return short signed sizeof static static_assert static_cast struct switch template this thread_local throw
true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())

TYPE_KEYWORDS = frozenset("auto bool char double float int long short signed unsigned void wchar_t size_t".split())

_OPERATORS = sorted("""
>>= <<= <=> ->* ... :: -> ++ -- << >> <= >= == != && || += -= *= /= %= &= |= ^= ## .* + - * / % ^ & | ~ ! = < > ? : . #
""".split(), key=len, reverse=True)
_PUNCT = set("{}()[];,")


@dataclass
class Token:
    kind: str      # kw, id, num, str, char, op, punct, pp, comment
    text: str
    line: int      # 1-based
    col: int       # 1-based
    pos: int       # offset w źródle

    @property
    def end(self) -> int:
        return self.pos + len(self.text)

    def __repr__(self) -> str:
        return f"Token({self.kind},{self.text!r},{self.line}:{self.col})"


_NUM_RE = re.compile(r"""
    (?:0[xX][0-9a-fA-F']+(?:\.[0-9a-fA-F']*)?(?:[pP][+-]?\d+)?
     |0[bB][01']+
     |(?:\d[\d']*\.?[\d']*|\.\d[\d']*)(?:[eE][+-]?\d+)?)
    [uUlLfFzZ]*
""", re.X)
_ID_RE = re.compile(r"[^\W\d][\w$]*|\$[\w$]*")          # także identyfikatory z literami spoza ASCII (np. `żółw`)
_OP_RE = re.compile("|".join(re.escape(o) for o in _OPERATORS))
_RAW_RE = re.compile(r'(?:u8|u|U|L)?R"([^()\\\s]{0,16})\(')


def tokenize(code: str, keep_comments: bool = True) -> list[Token]:
    """Dzieli kod na tokeny. Nigdy nie rzuca wyjątku — nieznane znaki stają się tokenami `op`."""
    toks: list[Token] = []
    i, n = 0, len(code)
    line, line_start = 1, 0
    at_line_start = True

    def add(kind, start, end):
        toks.append(Token(kind, code[start:end], line, start - line_start + 1, start))

    while i < n:
        c = code[i]
        if c == "\n":
            line += 1
            line_start = i + 1
            at_line_start = True
            i += 1
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        # preprocesor (z kontynuacjami linii)
        if c == "#" and at_line_start:
            j = i
            while j < n:
                if code[j] == "\n":
                    if code[j - 1] == "\\" or (code[j - 1] == "\r" and j >= 2 and code[j - 2] == "\\"):
                        j += 1
                        continue
                    break
                if code.startswith("//", j):
                    k = code.find("\n", j)
                    j = n if k < 0 else k
                    break
                if code.startswith("/*", j):
                    k = code.find("*/", j + 2)
                    if k < 0:
                        j = n
                        break
                    j = k + 2
                    continue
                j += 1
            start_line, start_ls = line, line_start
            toks.append(Token("pp", code[i:j].rstrip(), start_line, i - start_ls + 1, i))
            line += code.count("\n", i, j)
            if code.count("\n", i, j):
                line_start = code.rfind("\n", i, j) + 1
            i = j
            continue
        at_line_start = False
        # komentarze
        if code.startswith("//", i):
            j = code.find("\n", i)
            j = n if j < 0 else j
            if keep_comments:
                add("comment", i, j)
            i = j
            continue
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            j = n if j < 0 else j + 2
            if keep_comments:
                add("comment", i, j)
            line += code.count("\n", i, j)
            if code.count("\n", i, j):
                line_start = code.rfind("\n", i, j) + 1
            i = j
            continue
        # raw string
        m = _RAW_RE.match(code, i)
        if m:
            delim = ")" + m.group(1) + '"'
            j = code.find(delim, m.end())
            j = n if j < 0 else j + len(delim)
            add("str", i, j)
            line += code.count("\n", i, j)
            if code.count("\n", i, j):
                line_start = code.rfind("\n", i, j) + 1
            i = j
            continue
        # literały znakowe/napisowe (z prefiksami u8/u/U/L)
        pm = re.match(r'(?:u8|u|U|L)?(["\'])', code[i:i + 3])
        if pm and (i == 0 or not (code[i - 1].isalnum() or code[i - 1] == "_") or pm.start() == 0):
            q = pm.group(1)
            j = i + pm.end()
            while j < n and code[j] != q and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            j = min(n, j + 1)
            add("str" if q == '"' else "char", i, j)
            i = j
            continue
        if c.isdigit() or (c == "." and i + 1 < n and code[i + 1].isdigit()):
            m = _NUM_RE.match(code, i)
            j = m.end() if m and m.end() > i else i + 1
            add("num", i, j)
            i = j
            continue
        if c.isalpha() or c == "_" or c == "$":
            m = _ID_RE.match(code, i)
            j = m.end() if m else i + 1
            word = code[i:j]
            add("kw" if word in KEYWORDS else "id", i, j)
            i = j
            continue
        if c in _PUNCT:
            add("punct", i, i + 1)
            i += 1
            continue
        m = _OP_RE.match(code, i)
        j = m.end() if m else i + 1
        add("op", i, j)
        i = j
    return toks


def significant(tokens: list[Token]) -> list[Token]:
    """Tokeny bez komentarzy i dyrektyw preprocesora."""
    return [t for t in tokens if t.kind not in ("comment", "pp")]


def strip_comments(code: str) -> str:
    """Zastępuje komentarze spacjami (zachowuje nowe linie i pozycje znaków)."""
    out = list(code)
    for t in tokenize(code):
        if t.kind == "comment":
            for k in range(t.pos, t.end):
                if out[k] != "\n":
                    out[k] = " "
    return "".join(out)


def strip_strings(code: str) -> str:
    """Zastępuje zawartość literałów spacjami (np. do szukania wzorców regexem bez fałszywych trafień)."""
    out = list(code)
    for t in tokenize(code):
        if t.kind in ("str", "char", "comment"):
            for k in range(t.pos + 1, t.end - 1):
                if out[k] != "\n":
                    out[k] = " "
    return "".join(out)


_PAIRS = {"(": ")", "[": "]", "{": "}"}


def match_brackets(tokens: list[Token]) -> dict[int, int]:
    """Mapa indeks_otwarcia -> indeks_zamknięcia (i odwrotnie) dla (), [], {} (indeksy w liście tokenów)."""
    res: dict[int, int] = {}
    stack: list[tuple[str, int]] = []
    for idx, t in enumerate(tokens):
        if t.kind != "punct":
            continue
        if t.text in _PAIRS:
            stack.append((t.text, idx))
        elif t.text in ")]}":
            # zdejmij aż do pasującego (tolerancja na błędny kod)
            for k in range(len(stack) - 1, -1, -1):
                if _PAIRS[stack[k][0]] == t.text:
                    o = stack[k][1]
                    res[o] = idx
                    res[idx] = o
                    del stack[k:]
                    break
    return res


_CONTROL = {"if", "for", "while", "switch", "catch", "return", "sizeof", "decltype", "alignof", "static_assert",
            "do", "else", "new", "delete", "throw", "case", "using", "typedef", "noexcept", "requires"}


def find_functions(code: str, tokens: list[Token] | None = None) -> list[dict]:
    """Definicje funkcji (także metod w struct/class): {name, ret, params, line, end_line, body_start, body_end,
    owner, is_method}. body_start/body_end = pozycje znaków nawiasów klamrowych."""
    toks = significant(tokens if tokens is not None else tokenize(code))
    pairs = match_brackets(toks)
    res = []
    owners: list[tuple[int, str]] = []   # (indeks zamykającej klamry, nazwa struct/class/namespace)
    depth_fn_end = -1                    # wewnątrz ciała funkcji — pomijamy (lambdy itp.)
    i = 0
    while i < len(toks):
        t = toks[i]
        while owners and i > owners[-1][0]:
            owners.pop()
        if i <= depth_fn_end:
            i += 1
            continue
        prev_txt = toks[i - 1].text if i > 0 else ""
        if (t.kind == "kw" and t.text in ("struct", "class", "namespace", "union") and i + 1 < len(toks)
                and prev_txt not in ("<", ",", "(", "enum")):
            # znajdź '{' tej definicji (przed ';')
            j = i + 1
            name = toks[j].text if toks[j].kind == "id" else "(anonimowa)"
            while j < len(toks) and not (toks[j].kind == "punct" and toks[j].text in "{;"):
                j += 1
            if j < len(toks) and toks[j].text == "{" and j in pairs:
                owners.append((pairs[j], name))
                i = j + 1
                continue
        if t.kind in ("id", "kw") and t.text not in _CONTROL and i + 1 < len(toks) and toks[i + 1].text == "(":
            if t.kind == "kw" and t.text != "operator":
                i += 1
                continue
            close = pairs.get(i + 1)
            if close is None:
                i += 1
                continue
            j = close + 1
            # kwalifikatory po liście parametrów: const, noexcept, override, final, -> typ, = default
            while j < len(toks) and (toks[j].text in ("const", "noexcept", "override", "final", "mutable", "&", "&&")
                                     or toks[j].kind == "id" and toks[j].text in ("override", "final")):
                if toks[j].text == "noexcept" and j + 1 < len(toks) and toks[j + 1].text == "(":
                    j = pairs.get(j + 1, j) + 1
                    continue
                j += 1
            if j < len(toks) and toks[j].text == "->":
                while j < len(toks) and toks[j].text not in ("{", ";"):
                    j += 1
            if j < len(toks) and toks[j].text == ":" and owners:  # lista inicjalizacyjna konstruktora
                j += 1
                while j < len(toks) and toks[j].text != ";":
                    tj = toks[j]
                    if tj.text == "(" and j in pairs:
                        j = pairs[j] + 1
                        continue
                    if tj.text == "{":
                        if toks[j - 1].kind == "id" and j in pairs:   # inicjalizacja klamrowa a{5}
                            j = pairs[j] + 1
                            continue
                        break                                         # ciało konstruktora
                    j += 1
            if j < len(toks) and toks[j].text == "{" and j in pairs:
                # typ zwracany: tokeny wstecz do ; } { lub początku/specyfikatora dostępu
                k = i - 1
                ret_toks = []
                while k >= 0 and toks[k].text not in (";", "}", "{", ":") and not (toks[k].kind == "kw" and toks[k].text in ("public", "private", "protected")):
                    ret_toks.append(toks[k].text)
                    k -= 1
                ret = " ".join(reversed(ret_toks)).replace(" :: ", "::").strip()
                name = t.text
                if ret.endswith("::") or (ret_toks and ret_toks[0] == "::"):
                    # Klasa::metoda
                    pass
                is_lambda_like = not ret and not owners
                if not is_lambda_like or name == "main":
                    body_open, body_close = j, pairs[j]
                    params = code[toks[i + 1].pos + 1: toks[close].pos].strip()
                    res.append({"name": name, "ret": ret or ("" if owners else "int"), "params": params,
                                "line": t.line, "end_line": toks[body_close].line,
                                "body_start": toks[body_open].pos, "body_end": toks[body_close].pos,
                                "owner": owners[-1][1] if owners else None, "is_method": bool(owners)})
                    depth_fn_end = body_close
                    i = body_close + 1
                    continue
        i += 1
    return res


def line_of(code: str, pos: int) -> int:
    return code.count("\n", 0, pos) + 1


def includes(code: str) -> list[str]:
    return re.findall(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", code, re.M)


def has_main(code: str) -> bool:
    return any(f["name"] == "main" and not f["is_method"] for f in find_functions(code))

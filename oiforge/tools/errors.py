"""Tłumacz błędów C++ (GCC / Clang / linker) i błędów wykonania na zrozumiały polski.

Bez AI: ponad 100 wzorców (data/error_patterns.json) + reguły kontekstowe (podpowiedzi nagłówków, literówek,
typów w max/min, polskich cudzysłowów...). Upraszcza spam szablonów (`std::__cxx11::basic_string<char>` → `string`),
grupuje błędy kaskadowe (pierwszy błąd = główna przyczyna) i pokazuje fragment kodu z zaznaczoną kolumną.

API:
    translate(stderr, source=None) -> list[{line, col, severity, title, explanation, fix, original, snippet, ...}]
    explain_runtime(run_result_dict, source=None) -> {kind, title, explanation, causes, fix, severity, line, ...}
HTTP:
    POST /api/errors/translate {stderr, source}  -> {items, summary}
    POST /api/errors/runtime {run, source}        -> {explanation}
    GET  /api/errors/examples                     -> {examples}
    GET  /api/errors/patterns                     -> {patterns: [...]}  (katalog wzorców)
"""
from __future__ import annotations

import json
import re
import threading

from oiforge import config
from oiforge.server_api import ApiError

try:
    from oiforge.core import text as _text
except ImportError:  # pragma: no cover
    _text = None

PATTERNS_FILE = config.DATA_DIR / "error_patterns.json"
_lock = threading.Lock()
_cache: dict = {"sig": None, "patterns": [], "compiled": []}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DIAG_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\n]*?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning|note|remark|Error|Warning):\s*(?P<msg>.*)$")
_TOOL_RE = re.compile(r"^(?P<file>cc1plus|cc1|collect2(?:\.exe)?|clang(?:\+\+)?(?:-\d+)?|ld(?:\.lld)?|g\+\+(?:-\d+)?|c\+\+"
                      r"|lld|ld64(?:\.lld)?|[\w\-./\\]*?(?:ld|g\+\+|gcc|clang\+\+)(?:\.exe)?):\s*"
                      r"(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.*)$")
_CTX_RE = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[^:\n]+?):(?:(?P<line>\d+):(?P<col>\d+):)?\s+"
                     r"(?P<what>required from(?: here)?|required by substitution|in (?:constexpr )?expansion of|"
                     r"instantiated from(?: here)?|In instantiation of|In (?:member |static member )?function|"
                     r"In constructor|In destructor|In lambda function|At global scope|In function template)(?P<rest>.*)$")
_INCL_RE = re.compile(r"^(?:In file included from|\s+from )")
_GCC_ECHO_RE = re.compile(r"^\s*(?P<n>\d+)?\s*\|(?P<code>.*)$")
_CARET_RE = re.compile(r"^\s*[\^~ ]*\^[\^~ ]*$|^\s*~+\s*$")
_GEN_RE = re.compile(r"^\s*\d+\s+(?:errors?|warnings?)(?:\s+and\s+\d+\s+(?:errors?|warnings?))?\s+generated\.?\s*$")
_LD_UNDEF_RE = re.compile(r"undefined reference to [`'‘](?P<sym>[^'’]+)['’]")
_MAC_UNDEF_RE = re.compile(r'^\s*"(?P<sym>[^"]+)", referenced from:')
_PY_FILE_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<fn>.*))?$')
_PY_ERR_RE = re.compile(r"^(?P<exc>[A-Z]\w*(?:Error|Exception|Exit|Interrupt|Warning)|KeyboardInterrupt|StopIteration)(?::\s*(?P<msg>.*))?$")

USER_EXT = (".cpp", ".cc", ".cxx", ".c++", ".cp", ".c", ".py", ".ino", ".C")

# ------------------------------------------------------------------------------------------------- dane
HEADER_FOR = {
    "vector": "<vector>", "string": "<string>", "map": "<map>", "set": "<set>", "multiset": "<set>",
    "multimap": "<map>", "unordered_map": "<unordered_map>", "unordered_set": "<unordered_set>",
    "queue": "<queue>", "priority_queue": "<queue>", "deque": "<deque>", "stack": "<stack>", "list": "<list>",
    "pair": "<utility>", "make_pair": "<utility>", "swap": "<utility>", "tuple": "<tuple>", "tie": "<tuple>",
    "sort": "<algorithm>", "stable_sort": "<algorithm>", "reverse": "<algorithm>", "max": "<algorithm>",
    "min": "<algorithm>", "lower_bound": "<algorithm>", "upper_bound": "<algorithm>", "binary_search": "<algorithm>",
    "unique": "<algorithm>", "fill": "<algorithm>", "count": "<algorithm>", "find": "<algorithm>",
    "max_element": "<algorithm>", "min_element": "<algorithm>", "next_permutation": "<algorithm>",
    "nth_element": "<algorithm>", "accumulate": "<numeric>", "iota": "<numeric>", "gcd": "<numeric>",
    "lcm": "<numeric>", "partial_sum": "<numeric>", "cin": "<iostream>", "cout": "<iostream>", "cerr": "<iostream>",
    "endl": "<iostream>", "getline": "<string>", "setprecision": "<iomanip>", "setw": "<iomanip>",
    "setfill": "<iomanip>", "fixed": "<iostream>", "printf": "<cstdio>", "scanf": "<cstdio>", "puts": "<cstdio>",
    "getchar": "<cstdio>", "putchar": "<cstdio>", "memset": "<cstring>", "strlen": "<cstring>",
    "strcmp": "<cstring>", "memcpy": "<cstring>", "sqrt": "<cmath>", "pow": "<cmath>", "abs": "<cstdlib>/<cmath>",
    "fabs": "<cmath>", "floor": "<cmath>", "ceil": "<cmath>", "log": "<cmath>", "log2": "<cmath>", "round": "<cmath>",
    "INT_MAX": "<climits>", "INT_MIN": "<climits>", "LLONG_MAX": "<climits>", "LLONG_MIN": "<climits>",
    "numeric_limits": "<limits>", "bitset": "<bitset>", "stringstream": "<sstream>", "istringstream": "<sstream>",
    "ostringstream": "<sstream>", "function": "<functional>", "greater": "<functional>", "less": "<functional>",
    "rand": "<cstdlib>", "srand": "<cstdlib>", "exit": "<cstdlib>", "atoi": "<cstdlib>", "time": "<ctime>",
    "mt19937": "<random>", "uniform_int_distribution": "<random>", "chrono": "<chrono>", "array": "<array>",
    "assert": "<cassert>", "isdigit": "<cctype>", "isalpha": "<cctype>", "tolower": "<cctype>",
    "toupper": "<cctype>", "int64_t": "<cstdint>", "uint64_t": "<cstdint>", "complex": "<complex>",
    "to_string": "<string>", "stoi": "<string>", "stoll": "<string>", "iota_view": "<ranges>", "ranges": "<ranges>",
    "optional": "<optional>", "variant": "<variant>", "__gcd": "<algorithm>", "size_t": "<cstddef>",
}
STD_NAMES = set(HEADER_FOR) - {"printf", "scanf", "puts", "getchar", "putchar", "memset", "strlen", "strcmp",
                                "memcpy", "INT_MAX", "INT_MIN", "LLONG_MAX", "LLONG_MIN", "rand", "srand", "exit",
                                "atoi", "time", "assert", "isdigit", "isalpha", "tolower", "toupper", "int64_t",
                                "uint64_t", "__gcd", "size_t", "sqrt", "pow", "fabs", "floor", "ceil", "log", "log2",
                                "round"}
CHAR_NAMES = {
    "\u201e": "polski cudzysłów otwierający „", "\u201d": "cudzysłów zamykający ”", "\u201c": "cudzysłów “",
    "\u2018": "apostrof ‘", "\u2019": "apostrof ’", "\u2013": "półpauza –", "\u2014": "pauza —",
    "\u00a0": "twarda spacja (NBSP)", "\u2212": "znak minus − (nie zwykły '-')", "\u00d7": "znak mnożenia ×",
    "\u2264": "≤", "\u2265": "≥", "\u2260": "≠", "\u00ab": "«", "\u00bb": "»", "\u2026": "wielokropek …",
    "\u200b": "niewidoczna spacja zerowej szerokości", "\ufeff": "BOM (niewidoczny znak na początku pliku)",
}
OCTAL_LEAD = {"342": "znak Unicode (zwykle cudzysłów „ ” ‘ ’, półpauza – albo minus −) skopiowany z PDF/Worda",
              "302": "twarda spacja albo znak ° / ×", "303": "litera z ogonkiem (ą, ę, ó…) poza napisem",
              "305": "litera z ogonkiem (ł, ś, ź, ż…) poza napisem", "304": "litera z ogonkiem (ą, ć, ę…) poza napisem",
              "357": "BOM albo znak specjalny z edytora", "240": "druga część twardej spacji",
              "200": "część znaku Unicode", "236": "część cudzysłowu", "235": "część cudzysłowu", "223": "część półpauzy",
              "224": "część pauzy", "234": "część cudzysłowu", "230": "część apostrofu", "231": "część apostrofu"}

_TEMPLATE_DEFAULT_ARGS = ("std::allocator<", "allocator<", "std::char_traits<", "char_traits<", "std::hash<", "hash<",
                          "std::equal_to<", "equal_to<", "std::default_delete<", "default_delete<")


# ------------------------------------------------------------------------------------------------- upraszczanie typów
def _split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur or out:
        out.append("".join(cur).strip())
    return out


def _simplify_templates(s: str, depth: int = 0) -> str:
    if depth > 12 or "<" not in s:
        return s
    out = []
    i, n = 0, len(s)
    while i < n:
        mt = re.match(r"[A-Za-z_][\w:]*<", s[i:])
        if mt and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_")) and not mt.group(0).startswith("operator"):
            name = mt.group(0)[:-1]
            j = i + len(mt.group(0))
            lvl = 1
            k = j
            while k < n and lvl:
                if s[k] == "<":
                    lvl += 1
                elif s[k] == ">":
                    lvl -= 1
                k += 1
            if lvl:
                out.append(s[i:])
                break
            inner = s[j:k - 1]
            args = [_simplify_templates(a, depth + 1) for a in _split_top(inner)]
            base = name.split("::")[-1]
            keep = []
            for idx, a in enumerate(args):
                if idx > 0 and a.startswith(_TEMPLATE_DEFAULT_ARGS):
                    continue
                if idx > 0 and base in ("set", "map", "multiset", "multimap", "priority_queue") and \
                        re.fullmatch(r"(std::)?less<[^<>]*(<.*>)?[^<>]*>", a) and base != "priority_queue":
                    continue
                if idx == 1 and base == "priority_queue" and a.startswith(("std::vector<", "vector<")):
                    if idx + 1 < len(args) and not re.fullmatch(r"(std::)?less<.*>", args[idx + 1]):
                        keep.append(a)
                    elif idx + 1 >= len(args):
                        continue
                    else:
                        continue
                    continue
                if idx == 2 and base == "priority_queue" and re.fullmatch(r"(std::)?less<.*>", a):
                    continue
                keep.append(a)
            if base == "basic_string" and keep and keep[0] == "char":
                out.append("string")
            elif base in ("basic_ostream", "basic_istream", "basic_iostream", "basic_ifstream", "basic_ofstream",
                          "basic_stringstream", "basic_istringstream", "basic_ostringstream") and keep == ["char"]:
                out.append(base[len("basic_"):])
            else:
                out.append(f"{name}<{', '.join(keep)}>")
            i = k
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def simplify_types(s: str) -> str:
    """Upraszcza spam szablonów STL w komunikacie kompilatora."""
    if not s:
        return s
    s = s.replace("std::__cxx11::", "std::").replace("std::__1::", "std::").replace("std::__debug::", "std::")
    s = s.replace("std::debug::", "std::").replace("std::_V2::", "std::")
    s = re.sub(r"\[abi:[^\]]*\]", "", s)
    s = re.sub(r"\b__gnu_cxx::__normal_iterator<([^,<>]+?)\*, ((?:std::)?vector<[^<>]*(?:<[^<>]*>)?[^<>]*>)\s*>",
               r"\2::iterator", s)
    s = re.sub(r"\b__gnu_cxx::__normal_iterator<const ([^,<>]+?)\*, ((?:std::)?vector<[^<>]*(?:<[^<>]*>)?[^<>]*>)\s*>",
               r"\2::const_iterator", s)
    s = _simplify_templates(s)
    s = s.replace("std::string", "string")
    s = re.sub(r"\bstd::(vector|pair|map|set|multiset|multimap|unordered_map|unordered_set|deque|queue|stack|"
               r"priority_queue|list|array|tuple|bitset|less|greater|ostream|istream|iostream|ifstream|ofstream|"
               r"stringstream|istringstream|ostringstream|function|complex|optional)\b", r"\1", s)
    s = re.sub(r"\bstd::_Rb_tree_(const_)?iterator<([^<>]*(?:<[^<>]*>)?)>", r"iterator (set/map)<\2>", s)
    s = re.sub(r"\bstd::_Rb_tree_(const_)?iterator<", "iterator (set/map)<", s)
    s = re.sub(r"\blong long unsigned int\b", "unsigned long long", s)
    s = re.sub(r"\blong unsigned int\b", "unsigned long", s)
    s = re.sub(r"\blong long int\b", "long long", s)
    s = re.sub(r"\blong int\b", "long", s)
    s = re.sub(r"\bshort int\b", "short", s)
    s = re.sub(r"\bstd::size_t\b", "size_t", s)
    s = re.sub(r"\s+>", ">", s)
    # {aka 'X'} — usuń, gdy po uproszczeniu to to samo
    def aka(mt):
        return ""
    s = re.sub(r"\s*\{aka [‘'][^’']*[’']\}", aka, s)
    s = re.sub(r"\s*\(aka [‘'][^’']*[’']\)", "", s)
    return s


def _q(s: str) -> str:
    """Zamienia cudzysłowy kompilatora ‘x’ / 'x' na `x`."""
    return re.sub(r"[‘'`]([^’'`\n]{1,200})[’']", r"`\1`", s)


# ------------------------------------------------------------------------------------------------- wzorce
def load_patterns() -> list[dict]:
    try:
        sig = PATTERNS_FILE.stat().st_mtime_ns
    except OSError:
        sig = None
    with _lock:
        if _cache["sig"] == sig and _cache["compiled"]:
            return _cache["compiled"]
        compiled = []
        try:
            data = json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
            pats = data.get("patterns", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError):
            pats = []
        for p in pats:
            rx = []
            for m in p.get("match", []):
                try:
                    rx.append(re.compile(m))
                except re.error:
                    continue
            if rx:
                compiled.append(dict(p, _rx=rx))
        _cache.update(sig=sig, patterns=pats, compiled=compiled)
        return compiled


def _kb_ids() -> set | None:
    try:
        from oiforge.core import kb
        return {a["id"] for a in kb.articles()}
    except Exception:  # noqa: BLE001
        return None


def _match_pattern(msg: str):
    for p in load_patterns():
        for rx in p["_rx"]:
            mt = rx.search(msg)
            if mt:
                return p, {k: (v or "") for k, v in mt.groupdict().items()}
    return None, {}


def _fmt(tpl: str, groups: dict) -> str:
    if not tpl:
        return ""

    def sub(mt):
        k = mt.group(1)
        v = groups.get(k)
        return simplify_types(v) if v is not None else mt.group(0)
    return re.sub(r"\{(\w+)\}", sub, tpl)


# ------------------------------------------------------------------------------------------------- parsowanie stderr
def _is_user_file(f: str) -> bool:
    f = (f or "").strip()
    if not f:
        return False
    low = f.lower().replace("\\", "/")
    if any(x in low for x in ("/include/", "/c++/", "/usr/lib", "/bits/", "cellar/gcc", ".sdk/", "/mingw", "/lib/gcc",
                               "/libexec/", "/opt/homebrew/opt/llvm", "/usr/local/opt")):
        return low.endswith(USER_EXT) and "/include/" not in low
    if low.endswith((".h", ".hpp", ".hh", ".tcc", ".inc")):
        return False
    if "." not in low.rsplit("/", 1)[-1]:
        return low in ("main",)
    return low.endswith(USER_EXT) or low in ("<stdin>", "<source>")


class _Diag:
    __slots__ = ("file", "line", "col", "sev", "msg", "notes", "ctx", "echo", "caret", "user_loc", "extra", "tool")

    def __init__(self, file, line, col, sev, msg, ctx=None, tool=False):
        self.file, self.line, self.col, self.sev, self.msg = file, line, col, sev, msg
        self.notes: list = []
        self.ctx: list = list(ctx or [])
        self.echo: str | None = None
        self.caret: str | None = None
        self.user_loc = None
        self.extra: list = []
        self.tool = tool


def parse_diagnostics(stderr: str) -> list[_Diag]:
    """Dzieli surowe stderr na diagnostyki (błąd/ostrzeżenie + notatki + kontekst)."""
    text = _ANSI_RE.sub("", stderr or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    diags: list[_Diag] = []
    pending: list = []
    last = None                 # ostatnia diagnostyka lub notatka, do której doklejamy echo kodu
    mac_undef: list[str] = []
    in_mac_undef = False
    py_frames: list = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if _GEN_RE.match(line):
            continue
        m = _DIAG_RE.match(line)
        if m and not line.lstrip().startswith("|"):
            sev = m.group("sev").lower()
            if sev == "remark":
                continue
            loc = (m.group("file"), int(m.group("line")), int(m.group("col") or 0))
            msg = m.group("msg").strip()
            if sev == "note":
                if diags:
                    note = {"file": loc[0], "line": loc[1], "col": loc[2], "msg": msg}
                    diags[-1].notes.append(note)
                    last = note
                continue
            d = _Diag(loc[0], loc[1], loc[2], "error" if "error" in sev else "warning", msg, pending)
            pending = []
            diags.append(d)
            last = d
            in_mac_undef = False
            continue
        m = _TOOL_RE.match(line)
        if m:
            sev = m.group("sev").lower()
            msg = m.group("msg").strip()
            if sev == "note" and diags:
                diags[-1].notes.append({"file": "", "line": 0, "col": 0, "msg": msg})
                continue
            d = _Diag(m.group("file"), 0, 0, "error" if "error" in sev else "warning", msg, pending, tool=True)
            pending = []
            diags.append(d)
            last = d
            continue
        if _INCL_RE.match(line):
            pending.append({"kind": "include", "text": line.strip()})
            continue
        m = _CTX_RE.match(line)
        if m:
            pending.append({"kind": "ctx", "file": m.group("file"), "line": int(m.group("line") or 0),
                            "col": int(m.group("col") or 0), "what": m.group("what"), "text": line.strip()})
            continue
        if "Undefined symbols for architecture" in line:
            in_mac_undef = True
            d = _Diag("ld", 0, 0, "error", "Undefined symbols", pending, tool=True)
            pending = []
            diags.append(d)
            last = d
            continue
        mu = _MAC_UNDEF_RE.match(line)
        if mu and in_mac_undef:
            mac_undef.append(mu.group("sym"))
            diags[-1].extra.append(mu.group("sym"))
            continue
        if in_mac_undef and re.match(r"^\s+(_\w+|\S+) in \S+", line):
            continue
        if re.match(r"^ld: symbol\(s\) not found", line) or re.match(r"^ld: ", line):
            if diags and diags[-1].msg == "Undefined symbols":
                continue
            d = _Diag("ld", 0, 0, "error", line[4:].strip(), pending, tool=True)
            pending = []
            diags.append(d)
            continue
        ml = _LD_UNDEF_RE.search(line)
        if ml:
            loc = re.match(r"^(?P<file>[^:\n]+?):(?:\(\.text[^)]*\)|(?P<line>\d+))", line)
            d = _Diag(loc.group("file") if loc else "ld", int(loc.group("line")) if loc and loc.group("line") else 0, 0,
                      "error", f"undefined reference to '{ml.group('sym')}'", pending, tool=True)
            pending = []
            diags.append(d)
            continue
        if re.search(r"multiple definition of [`'‘]", line) or re.search(r"duplicate symbol '", line):
            d = _Diag("ld", 0, 0, "error", line.split(": ", 1)[-1].strip() if ": " in line else line.strip(), pending,
                      tool=True)
            pending = []
            diags.append(d)
            continue
        if re.match(r"^\s*(?:/usr/bin/)?ld[^:]*: .*: in function", line) or re.match(r"^[^:]+\.o: in function", line):
            continue
        mp = _PY_FILE_RE.match(line)
        if mp:
            py_frames.append((mp.group("file"), int(mp.group("line"))))
            continue
        me = _PY_ERR_RE.match(line.strip())
        if me and (py_frames or "Traceback" in text or "Error" in me.group("exc")):
            f, ln = (py_frames[-1] if py_frames else ("main.py", 0))
            d = _Diag(f, ln, 0, "error", line.strip(), pending)
            pending = []
            diags.append(d)
            last = d
            continue
        if line.startswith("Traceback (most recent call last)"):
            continue
        me = _GCC_ECHO_RE.match(line)
        if me and last is not None:
            target = last if isinstance(last, _Diag) else None
            if me.group("n"):
                if target is not None and target.echo is None:
                    target.echo = me.group("code")
            elif target is not None and target.caret is None and "^" in me.group("code"):
                target.caret = me.group("code")
            continue
        if _CARET_RE.match(line) and isinstance(last, _Diag):
            if last.caret is None:
                last.caret = line
            continue
        bullet = line.strip()
        if bullet.startswith(("•", "*", "+")) and diags:
            diags[-1].notes.append({"file": "", "line": 0, "col": 0, "msg": bullet.lstrip("•*+ ").strip()})
            continue
        if isinstance(last, _Diag) and last.echo is None and not line.startswith(" " * 0 + "In ") and diags and \
                not _DIAG_RE.match(line) and len(line) < 400 and not line.lstrip().startswith(("required", "In ")):
            # clang: echo kodu bez '|'
            last.echo = line
            continue
    # lokalizacja w pliku użytkownika
    for d in diags:
        if _is_user_file(d.file) and d.line:
            d.user_loc = (d.file, d.line, d.col)
            continue
        cand = None
        for c in d.ctx:
            if c.get("kind") == "ctx" and c.get("line") and _is_user_file(c.get("file", "")):
                cand = (c["file"], c["line"], c["col"])
        if cand is None:
            for nt in d.notes:
                if nt.get("line") and _is_user_file(nt.get("file", "")):
                    cand = (nt["file"], nt["line"], nt["col"])
                    break
        d.user_loc = cand
    return diags


# ------------------------------------------------------------------------------------------------- tłumaczenie
def _identifiers(source: str) -> set[str]:
    try:
        from oiforge.core import cpp_lexer
        return {t.text for t in cpp_lexer.tokenize(source) if t.kind == "id"}
    except Exception:  # noqa: BLE001
        return set(re.findall(r"[A-Za-z_]\w*", source or ""))


def _similar(name: str, cands, limit=3) -> list[str]:
    out = []
    for c in cands:
        if c == name or len(c) < 2:
            continue
        if _text is not None:
            d = _text.levenshtein(name.lower(), c.lower())
        else:
            d = abs(len(c) - len(name)) + sum(1 for a, b in zip(name, c) if a != b)
        thr = 1 if len(name) <= 4 else (2 if len(name) <= 8 else 3)
        if d <= thr:
            out.append((d, c))
    out.sort()
    return [c for _, c in out[:limit]]


def _snippet(source: str | None, line: int, col: int, echo: str | None = None, length: int = 1) -> dict | None:
    if line <= 0:
        return None
    if source:
        lines = source.replace("\r\n", "\n").split("\n")
        if line > len(lines):
            return None
        a, b = max(1, line - 2), min(len(lines), line + 1)
        rows = [{"n": k, "text": lines[k - 1], "focus": k == line} for k in range(a, b + 1)]
        focus = lines[line - 1]
    elif echo is not None:
        rows = [{"n": line, "text": echo.rstrip("\n")[:400], "focus": True}]
        focus = echo
    else:
        return None
    col = max(1, col or 1)
    if col - 1 < len(focus):
        mt = re.match(r"[A-Za-z_]\w*|\d+|\S", focus[col - 1:])
        length = max(length, len(mt.group(0)) if mt else 1)
    pad = "".join(ch if ch == "\t" else " " for ch in focus[:col - 1])
    text = "\n".join(f"{r['n']:>4} | {r['text']}" for r in rows if r["n"] <= line)
    text += f"\n     | {pad}^" + "~" * max(0, length - 1)
    after = [r for r in rows if r["n"] > line]
    if after:
        text += "\n" + "\n".join(f"{r['n']:>4} | {r['text']}" for r in after)
    return {"lines": rows, "line": line, "col": col, "len": length, "text": text}


def _first_user_line_text(source, line):
    if not source or line <= 0:
        return ""
    ls = source.replace("\r\n", "\n").split("\n")
    return ls[line - 1] if line <= len(ls) else ""


def _enrich(pid: str, groups: dict, d: _Diag, source: str | None, item: dict):
    """Inteligentne dopowiedzenia zależne od kontekstu (kod źródłowy, typy w komunikacie)."""
    msg = d.msg
    if pid in ("undeclared", "undeclared-type", "not-member-std"):
        name = groups.get("name", "")
        base = name.split("::")[-1]
        hints = []
        has_std = bool(source and re.search(r"using\s+namespace\s+std\s*;", source))
        has_bits = bool(source and "bits/stdc++.h" in source)
        std_name = base in HEADER_FOR
        if std_name:
            item["fix"] = ""
            if base in STD_NAMES and not has_std:
                hints.append(f"`{base}` należy do przestrzeni nazw std: pisz `std::{base}` albo dodaj na górze "
                             f"`using namespace std;`.")
            if not has_bits:
                hints.append(f"Upewnij się, że jest `#include {HEADER_FOR[base]}` (albo `#include <bits/stdc++.h>`).")
            if not hints:
                hints.append(f"`{base}` pochodzi z biblioteki standardowej — sprawdź `#include {HEADER_FOR[base]}` i `std::`.")
        if source:
            ids = _identifiers(source)
            sim = _similar(base, ids)
            if sim and not std_name:
                hints.insert(0, "Czy chodziło o " + ", ".join(f"`{s}`" for s in sim) + "? (literówka / wielkość liter)")
            # zadeklarowana gdzie indziej (inny zasięg)?
            if base in ids and d.user_loc and not std_name:
                ln = d.user_loc[1]
                decl_lines = [i + 1 for i, l in enumerate(source.split("\n"))
                              if re.search(rf"\b(int|long|double|char|bool|auto|string|ll|float|unsigned)\b[^;=]*\b{re.escape(base)}\b", l)]
                if decl_lines:
                    hints.insert(0, f"`{base}` jest zadeklarowane w linii {decl_lines[0]}, ale w innym zasięgu (np. "
                                    f"wewnątrz pętli/bloku `{{}}` albo innej funkcji) — poza tym blokiem nie istnieje. "
                                    f"Przenieś deklarację wyżej.")
        sug = groups.get("sug")
        if sug and not (std_name and sug == "std::" + base):
            hints.insert(0, f"Kompilator podpowiada: `{sug}`.")
        if base == "main":
            hints.append("Funkcja main musi nazywać się dokładnie `main` (małymi literami).")
        if hints:
            item["fix"] = (item["fix"] + " " if item["fix"] else "") + " ".join(hints)
        if base and ("\\U" in base or any(ord(ch) > 127 for ch in base)):
            item["title"] = "Niedozwolony znak w kodzie (cudzysłów/znak spoza ASCII)"
            item["fix"] = "Zamień polskie/typograficzne cudzysłowy „ ” na proste `\"`, a półpauzę – na zwykły minus `-`."
    if pid == "stray":
        ch = groups.get("ch") or ""
        code = groups.get("code") or ""
        hexv = groups.get("hex") or ""
        if hexv:
            try:
                ch = chr(int(hexv, 16))
            except ValueError:
                pass
        what = CHAR_NAMES.get(ch) or (OCTAL_LEAD.get(code) if code else None) or (f"znak `{ch}`" if ch else "znak spoza ASCII")
        line_txt = _first_user_line_text(source, d.user_loc[1] if d.user_loc else 0)
        bad = sorted({c for c in line_txt if ord(c) > 127})
        if bad:
            names = ", ".join(CHAR_NAMES.get(c, f"`{c}` (U+{ord(c):04X})") for c in bad)
            item["explanation"] += f" W tej linii są znaki spoza ASCII: {names}."
        else:
            item["explanation"] += f" Najpewniej to {what}."
        if any(c in line_txt for c in "„”“"):
            item["fix"] = "Zamień polskie/typograficzne cudzysłowy „ ” na zwykłe proste `\"`. " + item["fix"]
    if pid in ("no-matching-maxmin",):
        args = groups.get("args", "")
        types = [simplify_types(a.strip().rstrip("&").replace("const ", "")) for a in _split_top(args)] if args else []
        notes = " ".join(n.get("msg", "") for n in d.notes)
        mt = re.search(r"deduced conflicting types for parameter '[^']+' \('([^']+)' vs\. '([^']+)'\)", notes)
        if mt:
            types = [simplify_types(mt.group(1)), simplify_types(mt.group(2))]
        if len(types) == 2 and types[0] != types[1]:
            wide = next((t for t in types if "long long" in t), None) or \
                next((t for t in types if "double" in t), None) or types[0]
            item["explanation"] = (f"`{groups.get('fn', 'max')}` dostał argumenty RÓŻNYCH typów: `{types[0]}` i `{types[1]}`. "
                                   f"std::max/std::min to szablon `max(T a, T b)` — oba argumenty muszą mieć ten sam "
                                   f"typ T, a kompilator nie zgaduje, który wybrać.")
            item["fix"] = (f"Podaj typ jawnie: `{groups.get('fn', 'max').split('::')[-1]}<{wide}>(a, b)` albo rzutuj "
                           f"jeden argument: `max(a, ({wide})b)`; jeszcze lepiej trzymaj obie wartości w tym samym "
                           f"typie (np. obie long long).")
    if pid == "no-match-shl" or pid == "no-match-shr":
        tp = simplify_types(groups.get("type", ""))
        if re.match(r"(std::)?(vector|set|map|multiset|unordered_map|unordered_set|deque|list|array)\b", tp):
            item["fix"] = (f"Strumień nie umie wypisać/wczytać całego kontenera `{tp}` — zrób to w pętli: "
                           f"`for (auto x : v) cout << x << ' ';` (dla wczytania: `for (auto& x : v) cin >> x;`).")
        elif tp.startswith("pair"):
            item["fix"] = "Wypisz pola osobno: `cout << p.first << ' ' << p.second;`."
        elif "void" in tp:
            item["fix"] = "Wypisujesz wynik funkcji typu void — taka funkcja nic nie zwraca. Zmień jej typ albo wypisuj wewnątrz."
    if pid == "unordered-hash":
        item["fix"] = ("unordered_map/unordered_set nie mają hasza dla pair/tuple/struktur. Użyj `map<pair<int,int>, int>` "
                       "(O(log n)) albo napisz własny hash: `struct H { size_t operator()(pair<int,int> p) const { return "
                       "p.first * 1000003LL ^ p.second; } }; unordered_map<pair<int,int>, int, H> m;`.")
    if pid == "expected-semicolon" and source and d.user_loc:
        ln = d.user_loc[1]
        ls = source.replace("\r\n", "\n").split("\n")
        col = d.user_loc[2]
        cur = ls[ln - 1] if 1 <= ln <= len(ls) else ""
        prefix = cur[:max(0, col - 1)]
        if not prefix.strip() and ln > 1:
            j = ln - 1
            while j >= 1 and not ls[j - 1].strip():
                j -= 1
            if j >= 1 and not ls[j - 1].rstrip().endswith((";", "{", "}", ",", ":")):
                item["fix"] = f"Brakuje średnika na końcu linii {j}: `{ls[j - 1].strip()}` ← tu dopisz `;`."
                item["line_hint"] = j
    if pid == "ambiguous-ref":
        name = groups.get("name", "")
        item["fix"] = (f"Zmień nazwę swojej zmiennej `{name}` (np. `{name}_`, `my{name.capitalize()}`, `cnt`) — koliduje z "
                       f"`std::{name}` przez `using namespace std;`. Alternatywnie usuń `using namespace std;` i pisz `std::`.")
    if pid == "file-not-found":
        hdr = groups.get("hdr", "")
        if "bits/stdc++" in hdr:
            item["fix"] = ("`<bits/stdc++.h>` istnieje tylko w GCC (libstdc++). Na macOS z clangiem użyj GCC "
                           "(`brew install gcc`, kompilator g++-14/15/16) albo dołącz nagłówki osobno (iostream, vector, "
                           "algorithm…). OI Forge dla clanga dodaje własny zamiennik — kompiluj przez OI Forge.")
        elif hdr.lower() in ("conio.h", "windows.h", "dos.h"):
            item["fix"] = f"`{hdr}` jest tylko na Windows — usuń go (getch/system(\"pause\") nie są potrzebne na OI)."


def _generic(d: _Diag) -> tuple[str, str, str]:
    m = d.msg.lower()
    if d.sev == "warning":
        return ("Ostrzeżenie kompilatora", "Kompilator uważa ten fragment za podejrzany (kod się skompiluje, ale może "
                "działać inaczej, niż zamierzasz).", "Przeczytaj oryginalny komunikat i popraw wskazany fragment.")
    if "expected" in m:
        return ("Błąd składni", "Kompilator spodziewał się innego symbolu w tym miejscu — zwykle brakuje średnika, "
                "nawiasu albo klamry (często w linii POWYŻEJ wskazanej).", "Sprawdź nawiasy i średniki w tej i "
                "poprzedniej linii.")
    return ("Błąd kompilacji", "Kompilator nie może przetłumaczyć tego fragmentu.", "Przeczytaj oryginalny komunikat "
            "(poniżej) — najważniejszy jest zawsze PIERWSZY błąd.")


def translate(stderr: str, source: str | None = None, max_items: int = 40) -> list[dict]:
    """Tłumaczy stderr kompilatora na listę czytelnych zgłoszeń (pierwszy błąd = główna przyczyna)."""
    diags = parse_diagnostics(stderr or "")
    items: list[dict] = []
    seen_undecl: dict[str, dict] = {}
    first_error_line = None
    syntax_error_seen = None
    last_user_loc = None
    seen_msgs: dict = {}
    for d in diags:
        if d.user_loc is None and not d.tool and d.file and not _is_user_file(d.file) and last_user_loc:
            d.user_loc = last_user_loc          # kolejny błąd z tego samego szablonu
        if d.user_loc:
            last_user_loc = d.user_loc
        p, groups = _match_pattern(d.msg)
        if p is None or p.get("id") == "template-deduction":
            # spróbuj dopasować po notatkach (clang: szczegóły w note; GCC 16: zagnieżdżone „• error: …”)
            for nt in d.notes:
                nm = nt.get("msg", "")
                nested = nm.startswith("error:")
                p2, g2 = _match_pattern(nm[6:].strip() if nested else nm)
                if p2 is not None and (p2.get("from_note") or nested) and p2.get("id") != "template-deduction":
                    p, groups = p2, g2
                    break
        if p is not None:
            title = _fmt(p.get("title", ""), groups)
            expl = _fmt(p.get("explanation", ""), groups)
            fix = _fmt(p.get("fix", ""), groups)
            pid = p.get("id", "")
            kb = p.get("kb")
            ids = _kb_ids()
            if kb and ids is not None and kb not in ids:
                kb = None
            level = p.get("level") or ("error" if d.sev == "error" else "warning")
        else:
            title, expl, fix = _generic(d)
            pid, kb = "", None
            level = "error" if d.sev == "error" else "warning"
        loc = d.user_loc
        line, col = (loc[1], loc[2]) if loc else (d.line if _is_user_file(d.file) else 0, d.col if _is_user_file(d.file) else 0)
        item = {
            "line": line, "col": col, "severity": d.sev, "level": level, "title": _q(title), "explanation": _q(expl),
            "fix": _q(fix), "original": simplify_types(d.msg), "raw": d.msg, "pattern": pid or None, "kb": kb,
            "file": d.file if not d.tool else d.file, "in_header": bool(d.file and not _is_user_file(d.file) and not d.tool),
            "primary": False, "cascade": False, "count": 1, "notes": [], "snippet": None,
            "flag": (re.search(r"\[(-W[\w=+-]+)\]", d.msg).group(1) if re.search(r"\[(-W[\w=+-]+)\]", d.msg) else None),
        }
        if d.extra:
            item["symbols"] = [simplify_types(s) for s in d.extra]
            if pid in ("undefined-reference",) and d.extra:
                item["explanation"] += " Brakujące: " + ", ".join(f"`{simplify_types(s)}`" for s in d.extra[:5]) + "."
        notes = []
        cands = 0
        for nt in d.notes:
            msg = nt.get("msg", "")
            if re.match(r"(candidate|there (?:are|is) \d+ candidates?|candidate function|candidate template)", msg):
                cands += 1
                if cands > 2:
                    continue
            if re.match(r"(?:in instantiation|required from|in expansion of)", msg):
                continue
            notes.append(simplify_types(msg)[:300])
        if cands > 2:
            notes.append(f"… i jeszcze {cands - 2} kandydat(ów) — pominięte.")
        item["notes"] = notes[:6]
        if d.ctx and item["in_header"]:
            item["explanation"] += (" (Błąd jest zgłaszany wewnątrz biblioteki standardowej, ale przyczyna leży w Twojej "
                                    "linii wskazanej jako „required from here”.)")
        try:
            _enrich(pid, groups, d, source, item)
        except Exception:  # noqa: BLE001
            pass
        if item.get("line_hint"):
            pass
        snip = _snippet(source, item["line"], item["col"], d.echo)
        item["snippet"] = snip
        # duplikaty (spam szablonów: ten sam komunikat z kilku miejsc biblioteki)
        dkey = (pid or "", item["original"], item["line"] if not item["in_header"] else -1)
        if dkey in seen_msgs and d.sev == "error":
            seen_msgs[dkey]["count"] += 1
            continue
        seen_msgs[dkey] = item
        # kaskady
        if pid == "stray":
            prev = next((it for it in items if it.get("pattern") == "stray" and it["line"] == item["line"]), None)
            if prev is not None:
                prev["count"] += 1
                continue
        if pid in ("undeclared", "undeclared-type") and groups.get("name"):
            key = groups["name"]
            if key in seen_undecl:
                seen_undecl[key]["count"] += 1
                seen_undecl[key].setdefault("lines", []).append(item["line"])
                continue
            seen_undecl[key] = item
            item["lines"] = [item["line"]]
        if d.sev == "error":
            if first_error_line is None:
                first_error_line = item["line"]
                item["primary"] = True
                if pid.startswith("expected") or pid in ("stray", "missing-terminating"):
                    syntax_error_seen = item["line"]
            else:
                if syntax_error_seen is not None and item["line"] and abs(item["line"] - syntax_error_seen) <= 3:
                    item["cascade"] = True
                    item["cascade_of"] = syntax_error_seen
                elif any(it["line"] == item["line"] and it["severity"] == "error" for it in items):
                    item["cascade"] = True
                elif pid in ("ld-failed",):
                    item["cascade"] = True
        items.append(item)
        if len(items) >= max_items:
            break
    root = next((i for i in items if i["primary"]), None)
    for i in items:
        if i.get("cascade_of") and root is not None and root["line"] and i["severity"] == "error" and i is not root:
            i["fix"] = (f"Najpierw popraw główny błąd z linii {root['line']} — ten jest najpewniej jego skutkiem "
                        f"i zniknie razem z nim.")
    # gdy są tylko błędy linkera/narzędzia — pierwszy niech będzie główny
    if items and not any(i["primary"] for i in items):
        errs = [i for i in items if i["severity"] == "error" and i.get("pattern") != "ld-failed"]
        if errs:
            errs[0]["primary"] = True
    # „ld returned 1 exit status” jest zawsze skutkiem — ukryj, gdy jest inny błąd linkera
    if any(i.get("pattern") in ("undefined-reference", "undefined-main", "multiple-definition") for i in items):
        for i in items:
            if i.get("pattern") == "ld-failed":
                i["cascade"] = True
    order = sorted(range(len(items)), key=lambda k: (not items[k]["primary"], items[k]["severity"] != "error",
                                                    items[k]["cascade"], k))
    return [items[k] for k in order]


def summarize(items: list[dict]) -> dict:
    errs = [i for i in items if i["severity"] == "error"]
    warns = [i for i in items if i["severity"] == "warning"]
    root = next((i for i in items if i["primary"]), errs[0] if errs else None)
    return {"errors": len(errs), "warnings": len(warns), "cascades": sum(1 for i in items if i["cascade"]),
            "root": {"line": root["line"], "title": root["title"]} if root else None,
            "headline": (f"Główna przyczyna: {root['title']} (linia {root['line']})" if root and root["line"] else
                         (f"Główna przyczyna: {root['title']}" if root else
                          ("Brak błędów — tylko ostrzeżenia." if warns else "Brak błędów.")))}


# ------------------------------------------------------------------------------------------------- błędy wykonania
_SIG_INFO = {
    "SIGSEGV": ("Naruszenie ochrony pamięci (Segmentation fault)",
                "Program odwołał się do pamięci, do której nie ma dostępu, i system go zabił.",
                ["wyjście poza tablicę/vector (indeks ujemny albo ≥ rozmiar)", "zbyt głęboka rekurencja (przepełnienie stosu)",
                 "duża tablica lokalna w funkcji (np. int a[10000000] w main)", "dereferencja wskaźnika nullptr / end() iteratora",
                 "odczyt z pustego vectora/kolejki (v[0], q.front(), s.top())"],
                "Uruchom Sprawdzarkę z sanitizerami — wskaże dokładną linię. Duże tablice przenieś przed main."),
    "SIGFPE": ("Błąd arytmetyczny (SIGFPE)", "Program wykonał niedozwoloną operację arytmetyczną na liczbach całkowitych.",
               ["dzielenie przez zero (a / 0)", "modulo przez zero (a % 0)", "INT_MIN / -1 (przepełnienie dzielenia)"],
               "Sprawdź każde `/` i `%` — czy dzielnik może być 0 (np. pusty zbiór, n = 0, b = 0 w gcd)."),
    "SIGABRT": ("Program przerwany (abort)", "Program sam się zakończył przez abort() — zwykle po nieudanej asercji albo "
                "nieobsłużonym wyjątku.",
                ["nieobsłużony wyjątek (np. vector::at poza zakresem, stoi na złym napisie, bad_alloc)",
                 "nieudane assert(...)", "wykryte uszkodzenie sterty (double free, zapis poza tablicą na stercie)",
                 "tryb debug biblioteki (_GLIBCXX_DEBUG / hardening) wykrył błąd"],
                "Przeczytaj stderr — zwykle jest tam nazwa wyjątku albo treść asercji."),
    "SIGBUS": ("Błąd szyny (SIGBUS)", "Nieprawidłowy dostęp do pamięci (np. niewyrównany adres albo przepełnienie stosu "
               "na macOS).", ["zbyt głęboka rekurencja / ogromne tablice lokalne (macOS zgłasza to jako SIGBUS)",
                              "zły wskaźnik"], "Przenieś duże tablice poza funkcje; sprawdź głębokość rekurencji."),
    "SIGILL": ("Niedozwolona instrukcja (SIGILL)", "Procesor trafił na instrukcję, której nie może wykonać.",
               ["funkcja zwracająca wartość dochodzi do końca bez return (clang wstawia wtedy pułapkę ud2/brk)",
                "kod skompilowany z -march=native na innym procesorze", "wykryte UB z -fsanitize-trap"],
               "Sprawdź, czy każda funkcja nie-void kończy się return."),
    "SIGTRAP": ("Pułapka (SIGTRAP)", "Program zatrzymał się na instrukcji pułapki.",
                ["asercja biblioteki w trybie hardening (np. indeks poza vectorem)", "__builtin_trap() / brak return"],
                "Uruchom z sanitizerami, żeby zobaczyć linię."),
    "SIGKILL": ("Program zabity (SIGKILL)", "System natychmiast zakończył program.",
                ["przekroczony limit czasu (zabity przez sprawdzarkę)", "przekroczony limit pamięci (OOM killer)"],
                "Zmniejsz złożoność lub zużycie pamięci."),
    "SIGXCPU": ("Przekroczony limit czasu procesora", "Program przekroczył limit czasu CPU.",
                ["zbyt wolny algorytm", "nieskończona pętla"], "Popraw złożoność."),
    "SIGPIPE": ("Przerwany potok (SIGPIPE)", "Program pisał do zamkniętego wyjścia.", ["wypisywanie po zamknięciu odbiorcy"],
                "Zwykle niegroźne przy testach interaktywnych."),
}
_WIN = {0xC0000005: "SIGSEGV", 0xC00000FD: "STACK", 0xC0000094: "SIGFPE", 0xC000008E: "SIGFPE", 0xC0000095: "SIGFPE",
        0xC0000409: "SIGABRT", 0xC0000374: "HEAP", 0xC0000017: "MLE", 0xC000001D: "SIGILL", 0xC0000096: "SIGILL",
        0xC000008C: "SIGSEGV", 0xC0000135: "DLL", 0xC0000139: "DLL", 0xC000007B: "DLL", 0x80000003: "SIGTRAP",
        0xC0000420: "SIGABRT", 0xC000013A: "CTRLC", 3: "SIGABRT"}


def _win_code(code) -> int | None:
    if code is None:
        return None
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    u = c & 0xFFFFFFFF
    if u in _WIN and (u > 0xFFFF or config.IS_WINDOWS):
        return u
    return None


def _line_from_text(txt: str) -> int | None:
    for rx in (r"main\.cpp:(\d+)", r"\.cpp:(\d+)(?::\d+)?", r'File "[^"]*", line (\d+)', r"line (\d+)"):
        mt = re.search(rx, txt or "")
        if mt:
            return int(mt.group(1))
    return None


def explain_runtime(run: dict | None, source: str | None = None) -> dict:
    """Wyjaśnienie werdyktu/sygnału/kodu wyjścia programu (RunResult.to_dict())."""
    run = run or {}
    verdict = (run.get("verdict") or "").upper()
    sig = run.get("signal") or ""
    code = run.get("exit_code")
    stderr = run.get("stderr") or ""
    out = {"kind": "ok", "title": "Program zakończył się poprawnie", "explanation": "", "causes": [], "fix": "",
           "severity": "info", "line": None, "details": {}}
    if run.get("error"):
        return dict(out, kind="launch", severity="error", title="Nie udało się uruchomić programu",
                    explanation=str(run["error"]), fix="Sprawdź, czy kompilacja się udała i czy plik wykonywalny istnieje.")
    # wyjątki / asercje / tryby debug w stderr
    sp = _stderr_specials(stderr)
    if verdict == "TLE" or run.get("timed_out"):
        t = run.get("time") or run.get("wall")
        return dict(out, kind="tle", severity="error", title="Przekroczony limit czasu (TLE)",
                    explanation=f"Program nie skończył się w limicie czasu" + (f" ({t:.2f} s)." if isinstance(t, (int, float)) else ".")
                    + " Albo algorytm jest za wolny dla tego rozmiaru danych, albo program wpadł w nieskończoną pętlę "
                      "(albo czeka na dane, których nie ma).",
                    causes=["złożoność za duża (np. O(n²) dla n = 10⁵–10⁶)", "nieskończona pętla (zły warunek, brak i++)",
                            "czytanie wejścia w pętli bez sprawdzenia końca (while(true) cin >> x)",
                            "wolne wejście/wyjście (brak sync_with_stdio(false), endl w pętli)",
                            "pętla po unsigned w dół (i >= 0 zawsze prawdziwe)"],
                    fix="Uruchom na małym teście — jeśli też się wiesza, to pętla; jeśli nie, policz złożoność "
                        "(Pomiar złożoności) i przyspiesz I/O.")
    if verdict == "MLE":
        mem = run.get("mem_kb")
        return dict(out, kind="mle", severity="error", title="Przekroczony limit pamięci (MLE)",
                    explanation="Program zużył więcej pamięci, niż pozwala limit" + (f" ({mem / 1024:.0f} MB)." if mem else "."),
                    causes=["za duże tablice (np. long long a[10⁸] = 800 MB)", "vector rośnie bez końca (push_back w pętli bez warunku)",
                            "map/set z milionami elementów (≈ 40–80 B na element)", "głęboka rekurencja z dużymi ramkami",
                            "kopiowanie kontenerów przez wartość w rekurencji"],
                    fix="Policz pamięć: int = 4 B, long long = 8 B; tablica 10⁷ intów = 40 MB.")
    if sp:
        out.update(sp)
        out["severity"] = "error"
        return out
    winsig = _win_code(code)
    name = sig or ""
    if winsig and not name:
        name = {"STACK": "STACK", "HEAP": "HEAP", "DLL": "DLL", "MLE": "MLE", "CTRLC": "CTRLC"}.get(_WIN[winsig], _WIN[winsig])
    low = stderr.lower()
    if "stack-overflow" in low or "stack overflow" in low or name in ("STACK",) or \
            (name in ("SIGSEGV", "SIGBUS") and re.search(r"(#\d+ .* in (\w+).*\n){20,}", stderr)):
        return dict(out, kind="stack-overflow", severity="error", title="Przepełnienie stosu (zbyt głęboka rekurencja)",
                    explanation="Stos wywołań się skończył — każde wywołanie funkcji zajmuje miejsce na stosie, a "
                                "rekurencja była za głęboka (albo nieskończona)." +
                                (f" Kod Windows 0x{winsig:08X} = STATUS_STACK_OVERFLOW." if winsig else ""),
                    causes=["brak/zły warunek stopu w rekurencji", "DFS po ścieżce długości 10⁵–10⁶ (domyślny stos 1–8 MB)",
                            "duże tablice lokalne w funkcji rekurencyjnej"],
                    fix="Sprawdź przypadek bazowy; dla głębokich DFS przepisz na iteracyjny (własny stos) albo przenieś "
                        "duże tablice poza funkcję. Na OI stos = limit pamięci, ale w wielu środowiskach jest mały.",
                    line=_line_from_text(stderr))
    if name in ("HEAP",):
        return dict(out, kind="heap", severity="error", title="Uszkodzona sterta (0xC0000374)",
                    explanation="Program zapisał poza zaalokowanym obszarem pamięci (np. poza vectorem) albo zwolnił "
                                "pamięć dwa razy.", causes=["zapis poza vector/new[]", "double delete"],
                    fix="Uruchom z sanitizerami (Sprawdzarka) — pokaże dokładne miejsce.")
    if name == "DLL":
        return dict(out, kind="dll", severity="error", title="Brak biblioteki DLL (Windows)",
                    explanation="Program nie może się uruchomić, bo nie znalazł bibliotek MinGW (np. libstdc++-6.dll, "
                                "libgcc_s_seh-1.dll).", causes=["kompilator MinGW nie jest w PATH"],
                    fix="Dodaj katalog bin MinGW do PATH albo kompiluj z `-static`.")
    if name == "CTRLC":
        return dict(out, kind="interrupted", severity="warning", title="Program przerwany (Ctrl+C)",
                    explanation="Program został przerwany z zewnątrz.", fix="")
    if name in _SIG_INFO or (winsig and _WIN.get(winsig) in _SIG_INFO):
        key = name if name in _SIG_INFO else _WIN[winsig]
        title, expl, causes, fix = _SIG_INFO[key]
        if winsig:
            expl += f" (kod Windows 0x{winsig:08X})"
        if key == "SIGKILL" and run.get("mem_kb") and run.get("mem_kb", 0) > 1024 * 1024:
            title = "Program zabity — prawdopodobnie brak pamięci"
        return dict(out, kind=key.lower(), severity="error", title=title, explanation=expl, causes=causes, fix=fix,
                    line=_line_from_text(stderr))
    if verdict == "RE" or (code not in (0, None)):
        c = code if code is not None else "?"
        expl = f"Program zakończył się kodem {c} (różnym od 0). Sprawdzarka traktuje to jako błąd wykonania (RE)."
        causes = ["`return 1;` (albo inny niezerowy) w main", "exit(1) / exit(-1) w kodzie"]
        if isinstance(code, int) and code in (134, 6, -6):
            return explain_runtime(dict(run, signal="SIGABRT"), source)
        if isinstance(code, int) and code in (139, 11, -11):
            return explain_runtime(dict(run, signal="SIGSEGV"), source)
        if isinstance(code, int) and code in (136, 8, -8):
            return explain_runtime(dict(run, signal="SIGFPE"), source)
        if isinstance(code, int) and code in (137, -9):
            return explain_runtime(dict(run, signal="SIGKILL"), source)
        if "traceback" in low:
            return _python_error(stderr, out)
        return dict(out, kind="exit-code", severity="error", title=f"Niezerowy kod wyjścia ({c})", explanation=expl,
                    causes=causes, fix="main powinien kończyć się `return 0;`.")
    if "traceback" in low:
        return _python_error(stderr, out)
    if stderr.strip() and ("runtime error" in low or "addresssanitizer" in low):
        return dict(out, kind="ub", severity="error", title="Wykryto niezdefiniowane zachowanie (UB)",
                    explanation="Program zakończył się, ale sanitizer zgłosił błąd w trakcie działania.",
                    fix="Zobacz szczegóły w Sprawdzarce.", line=_line_from_text(stderr))
    return out


def _stderr_specials(stderr: str) -> dict | None:
    s = stderr or ""
    mt = re.search(r"terminate called after throwing an instance of '([^']+)'(?:\s*\n\s*what\(\):\s*(.*))?", s) or \
        re.search(r"terminating (?:due to|with) uncaught exception of type ([\w:<>]+)(?::\s*(.*))?", s)
    if mt:
        exc = simplify_types(mt.group(1))
        what = (mt.group(2) or "").strip()
        info = _exception_info(exc, what)
        return dict(kind="exception", title=info[0], explanation=info[1], causes=info[2], fix=info[3],
                    line=_line_from_text(s), details={"exception": exc, "what": what})
    mt = re.search(r"Assertion [`'‘]?(.+?)['’]? failed", s) or re.search(r"Assertion failed: \((.+?)\), function", s)
    if mt and "Hardening" not in s and "libc++" not in s[:mt.start()][-80:]:
        ln = re.search(r"line (\d+)", s) or re.search(r":(\d+): .*Assertion", s)
        return dict(kind="assert", title="Nie powiodła się asercja assert", explanation=f"Warunek `{mt.group(1)}` był fałszywy — "
                    "program sam się zatrzymał (abort).", causes=["założenie w kodzie nie było prawdziwe dla tych danych"],
                    fix="Sprawdź, dla jakich danych warunek nie zachodzi.", line=int(ln.group(1)) if ln else None)
    mt = re.search(r"libc\+\+ Hardening assertion (.+?) failed: (.+)", s) or re.search(r"assertion (.+?) failed: (.+)", s)
    if mt:
        what = mt.group(2).strip()
        return dict(kind="hardening", title="Biblioteka wykryła błąd: " + _pl_lib_msg(what),
                    explanation=f"Tryb sprawdzania biblioteki (libc++ hardening) zatrzymał program: `{mt.group(1)}` "
                                f"— {_pl_lib_msg(what)}.", causes=[], fix="Popraw indeks/warunek wskazany w ramce stosu.",
                    line=_line_from_text(s))
    mt = re.search(r"Error: (attempt to [^\n]+(?:\n[^\n]+)?)", s)
    if mt or "_GLIBCXX_DEBUG" in s or "/debug/" in s and "Error:" in s:
        what = re.sub(r"\s+", " ", mt.group(1)) if mt else "błąd kontenera"
        return dict(kind="glibcxx-debug", title="Tryb debug STL wykrył błąd: " + _pl_lib_msg(what),
                    explanation=f"libstdc++ w trybie _GLIBCXX_DEBUG: {_pl_lib_msg(what)}.", causes=[],
                    fix="Popraw indeks/iterator.", line=_line_from_text(s))
    if re.search(r"Floating point exception", s):
        return dict(kind="sigfpe", title=_SIG_INFO["SIGFPE"][0], explanation=_SIG_INFO["SIGFPE"][1],
                    causes=_SIG_INFO["SIGFPE"][2], fix=_SIG_INFO["SIGFPE"][3], line=None)
    if re.search(r"double free|free\(\): invalid|corrupted (size|double-linked)|malloc\(\): ", s):
        return dict(kind="heap", title="Uszkodzona pamięć sterty",
                    explanation="Biblioteka wykryła uszkodzenie sterty (zapis poza zaalokowaną pamięcią albo podwójne "
                                "zwolnienie).", causes=["zapis poza vector/tablicę new[]", "delete dwa razy"],
                    fix="Uruchom z sanitizerami — wskażą miejsce.", line=None)
    return None


def _pl_lib_msg(what: str) -> str:
    w = what.strip()
    mt = re.search(r"out-of-bounds index (\d+), but\s+container only holds (\d+) elements", w)
    if mt:
        return f"indeks {mt.group(1)} poza kontenerem o rozmiarze {mt.group(2)}"
    rules = [
        (r"vector\[\] index out of bounds", "indeks vector[] poza zakresem"),
        (r"string index out of bounds", "indeks string poza zakresem"),
        (r"front\(\) called on an empty", "front() na pustym kontenerze"),
        (r"back\(\) called on an empty", "back() na pustym kontenerze"),
        (r"pop_back\(\) called on an empty", "pop_back() na pustym kontenerze"),
        (r"(?:top|pop)\(\) called on an empty|empty (?:priority_)?queue|empty stack", "top()/pop() na pustej kolejce/stosie"),
        (r"comparator does not induce a strict weak ordering|strict[- ]weak[- ]ordering",
         "komparator nie jest ścisłym porządkiem (używasz <= lub >=?) — sort wyszedłby poza tablicę"),
        (r"attempt to dereference a past-the-end iterator", "dereferencja iteratora end()"),
        (r"attempt to increment a past-the-end iterator", "iterator przesunięty za end()"),
        (r"attempt to decrement a dereferenceable \(start-of-sequence\) iterator", "iterator cofnięty przed begin()"),
        (r"singular iterator", "użycie nieważnego (unieważnionego) iteratora"),
        (r"not sorted|not partitioned", "lower_bound/binary_search na nieposortowanych danych"),
        (r"attempt to access an element in an empty container", "dostęp do elementu pustego kontenera"),
        (r"erase.*(?:past-the-end|end\(\))", "erase(end())"),
        (r"cannot (?:create|allocate) .* larger than max_size", "rozmiar ujemny lub gigantyczny"),
    ]
    for rx, pl in rules:
        if re.search(rx, w, re.I):
            return pl
    return w[:160]


def _exception_info(exc: str, what: str):
    e = exc.split("::")[-1]
    if e == "out_of_range":
        mt = re.search(r"__n \(which is (\d+)\) >= this->size\(\) \(which is (\d+)\)", what)
        mt2 = re.search(r"__pos \(which is (\d+)\) > this->size\(\) \(which is (\d+)\)", what)
        if mt:
            d = f"Indeks {mt.group(1)}, a rozmiar to {mt.group(2)} (poprawne indeksy 0…{int(mt.group(2)) - 1})."
        elif mt2:
            d = f"Pozycja {mt2.group(1)} w substr/erase, a długość napisu to {mt2.group(2)}."
        else:
            d = f"Komunikat: {what}" if what else ""
        return ("Wyjątek out_of_range — indeks poza zakresem",
                "Metoda .at() / substr() / stoi dostała argument spoza zakresu i rzuciła wyjątek, którego nikt nie złapał. " + d,
                ["v.at(i) z i ≥ v.size()", "s.substr(pos) z pos > s.size()", "stoi na liczbie poza zakresem int",
                 "map.at(k) dla nieistniejącego klucza"],
                "Sprawdź indeksy (pętle do size() - 1) i istnienie klucza (`m.count(k)`).")
    if e == "bad_alloc":
        return ("Brak pamięci (bad_alloc)", "Nie udało się zaalokować pamięci — program prosił o za dużo.",
                ["vector o ogromnym/ujemnym rozmiarze (np. vector<int> v(n) z niezainicjalizowanym n)",
                 "push_back w nieskończonej pętli", "za duże tablice dynamiczne"],
                "Sprawdź rozmiary kontenerów i czy n jest wczytane przed utworzeniem vectora.")
    if e == "length_error":
        return ("Wyjątek length_error — nieprawidłowy rozmiar",
                "Próba utworzenia lub zmiany rozmiaru kontenera na wartość większą niż max_size() — zwykle rozmiar jest ujemny "
                "(np. -1 zamieniony na ogromną liczbę bez znaku).",
                ["vector<int> v(n - k) dla n < k", "resize(-1)", "string(n, c) z ujemnym n", "reserve(śmieci)"],
                "Sprawdź wartość rozmiaru tuż przed utworzeniem kontenera.")
    if e == "invalid_argument":
        return ("Wyjątek invalid_argument (np. stoi)", f"Funkcja konwersji dostała napis, który nie jest liczbą"
                + (f" ({what})." if what else "."), ["stoi(\"\") albo stoi(\"abc\")", "wczytanie pustej linii przez getline"],
                "Sprawdź, co faktycznie wczytujesz (np. getline po cin >> zostawia pustą linię).")
    if e in ("logic_error",) and "null" in what.lower():
        return ("Wyjątek logic_error — string z nullptr", "Utworzono std::string z pustego wskaźnika (np. string s = 0).",
                ["string s = NULL / 0"], "Nie inicjalizuj stringa zerem.")
    if e == "bad_function_call":
        return ("Wyjątek bad_function_call", "Wywołano pusty std::function.", ["function<...> f; f(...) bez przypisania"],
                "Przypisz funkcję przed wywołaniem.")
    return (f"Nieobsłużony wyjątek {exc}", f"Program rzucił wyjątek {exc}" + (f": {what}" if what else "") +
            " i nikt go nie złapał — działanie zostało przerwane (abort).", [], "Złap wyjątek albo usuń jego przyczynę.")


_PY_ERRORS = {
    "IndexError": ("Indeks poza listą (IndexError)", "Odwołanie do elementu listy/napisu, który nie istnieje.",
                   "Sprawdź zakresy pętli: indeksy idą od 0 do len(a) - 1."),
    "KeyError": ("Brak klucza w słowniku (KeyError)", "Słownik nie ma takiego klucza.",
                 "Użyj `d.get(k, 0)` albo `collections.defaultdict`."),
    "ZeroDivisionError": ("Dzielenie przez zero", "Dzielnik był równy 0.", "Sprawdź, czy dzielnik może być zerem."),
    "ValueError": ("Zła wartość (ValueError)", "Funkcja dostała argument złego rodzaju — np. int('') albo int('3.5').",
                   "Sprawdź format wejścia (split(), puste linie)."),
    "RecursionError": ("Za głęboka rekurencja (RecursionError)", "Python ma limit rekurencji ~1000.",
                       "`sys.setrecursionlimit(10**6)` i/lub przepisz rekurencję na pętlę (stos)."),
    "EOFError": ("Koniec danych (EOFError)", "input() nie miał już czego przeczytać.",
                 "Wczytujesz więcej linii, niż jest w danych — sprawdź format wejścia."),
    "NameError": ("Nieznana nazwa (NameError)", "Używasz zmiennej/funkcji, której nie zdefiniowano.",
                  "Sprawdź pisownię i czy zmienna jest zdefiniowana przed użyciem."),
    "TypeError": ("Zły typ (TypeError)", "Operacja na wartości niewłaściwego typu (np. str + int).",
                  "Zamień typy (int(x), str(x)) przed działaniem."),
    "MemoryError": ("Brak pamięci (MemoryError)", "Program zużył za dużo pamięci.", "Zmniejsz rozmiar struktur."),
    "AttributeError": ("Brak atrybutu (AttributeError)", "Obiekt nie ma takiej metody/pola.", "Sprawdź typ obiektu."),
    "SyntaxError": ("Błąd składni Pythona", "Kod nie jest poprawnym Pythonem.", "Sprawdź nawiasy, dwukropki i wcięcia."),
    "IndentationError": ("Złe wcięcia", "Wcięcia nie pasują do struktury kodu.", "Używaj konsekwentnie 4 spacji."),
    "OverflowError": ("Przepełnienie (OverflowError)", "Wynik za duży dla float.", "Licz na int albo modulo."),
}


def _python_error(stderr: str, base: dict) -> dict:
    lines = [l for l in stderr.strip().split("\n") if l.strip()]
    last = lines[-1].strip() if lines else ""
    mt = _PY_ERR_RE.match(last)
    exc = mt.group("exc") if mt else "Error"
    msg = (mt.group("msg") or "") if mt else last
    ln = None
    for l in lines:
        mp = _PY_FILE_RE.match(l)
        if mp:
            ln = int(mp.group("line"))
    title, expl, fix = _PY_ERRORS.get(exc, (f"Wyjątek Pythona {exc}", "Program Pythona zakończył się wyjątkiem.",
                                            "Przeczytaj ostatnią linię komunikatu."))
    return dict(base, kind="python", severity="error", title=title, explanation=expl + (f" Komunikat: {msg}" if msg else ""),
                fix=fix, line=ln, details={"exception": exc, "message": msg})


# ------------------------------------------------------------------------------------------------- przykłady i HTTP
EXAMPLES = [
    {"id": "undecl", "title": "Nieznana nazwa (brak using namespace std)",
     "source": "#include <iostream>\nint main() {\n    int n;\n    cin >> n;\n    cout << n * 2 << endl;\n}\n",
     "stderr": "main.cpp: In function 'int main()':\nmain.cpp:4:5: error: 'cin' was not declared in this scope; did you mean 'std::cin'?\n    4 |     cin >> n;\n      |     ^~~\n      |     std::cin\nmain.cpp:5:5: error: 'cout' was not declared in this scope; did you mean 'std::cout'?\n    5 |     cout << n * 2 << endl;\n      |     ^~~~\nmain.cpp:5:22: error: 'endl' was not declared in this scope; did you mean 'std::endl'?\n    5 |     cout << n * 2 << endl;\n      |                      ^~~~\n"},
    {"id": "semi", "title": "Brak średnika",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n    int a = 5\n    int b = 7;\n    cout << a + b;\n}\n",
     "stderr": "main.cpp: In function 'int main()':\nmain.cpp:5:5: error: expected ',' or ';' before 'int'\n    5 |     int b = 7;\n      |     ^~~\nmain.cpp:6:17: error: 'b' was not declared in this scope\n    6 |     cout << a + b;\n      |                 ^\n"},
    {"id": "maxll", "title": "max(long long, int)",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n    long long best = 0;\n    int x = 5;\n    best = max(best, x);\n    cout << best;\n}\n",
     "stderr": "main.cpp: In function 'int main()':\nmain.cpp:6:15: error: no matching function for call to 'max(long long int&, int&)'\n    6 |     best = max(best, x);\n      |            ~~~^~~~~~~~~\nIn file included from /usr/include/c++/13/algorithm:60,\n                 from /usr/include/x86_64-linux-gnu/c++/13/bits/stdc++.h:51,\n                 from main.cpp:1:\n/usr/include/c++/13/bits/stl_algobase.h:257:5: note: candidate: 'template<class _Tp> constexpr const _Tp& std::max(const _Tp&, const _Tp&)'\n  257 |     max(const _Tp& __a, const _Tp& __b)\n      |     ^~~\n/usr/include/c++/13/bits/stl_algobase.h:257:5: note:   template argument deduction/substitution failed:\nmain.cpp:6:15: note:   deduced conflicting types for parameter 'const _Tp' ('long long int' and 'int')\n    6 |     best = max(best, x);\n      |            ~~~^~~~~~~~~\n"},
    {"id": "stray", "title": "Polskie cudzysłowy z PDF-a",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n    cout << \u201eTAK\u201d << '\\n';\n}\n",
     "stderr": "main.cpp:4:13: error: stray '\\342' in program\n    4 |     cout << <U+201E>TAK<U+201D> << '\\n';\n      |             ^~~~~~~~\nmain.cpp:4:14: error: stray '\\200' in program\nmain.cpp:4:15: error: stray '\\236' in program\nmain.cpp: In function 'int main()':\nmain.cpp:4:16: error: 'TAK' was not declared in this scope\n"},
    {"id": "sortstruct", "title": "sort na strukturze bez operator<",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nstruct P { int x, y; };\nint main() {\n    vector<P> v(3);\n    sort(v.begin(), v.end());\n}\n",
     "stderr": "In file included from /usr/include/c++/13/bits/stl_algobase.h:71,\n                 from /usr/include/c++/13/algorithm:60,\n                 from main.cpp:1:\n/usr/include/c++/13/bits/predefined_ops.h: In instantiation of 'constexpr bool __gnu_cxx::__ops::_Iter_less_iter::operator()(_Iterator1, _Iterator2) const [with _Iterator1 = __gnu_cxx::__normal_iterator<P*, std::vector<P, std::allocator<P> > >; _Iterator2 = __gnu_cxx::__normal_iterator<P*, std::vector<P, std::allocator<P> > >]':\n/usr/include/c++/13/bits/stl_algo.h:1810:14:   required from 'constexpr void std::__insertion_sort(_RandomAccessIterator, _RandomAccessIterator, _Compare)'\nmain.cpp:6:9:   required from here\n    6 |     sort(v.begin(), v.end());\n      |     ~~~~^~~~~~~~~~~~~~~~~~~~\n/usr/include/c++/13/bits/predefined_ops.h:45:23: error: no match for 'operator<' (operand types are 'P' and 'P')\n   45 |       { return *__it1 < *__it2; }\n      |                ~~~~~~~^~~~~~~~\n"},
    {"id": "coutvec", "title": "cout << vector",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n    vector<int> v = {1, 2, 3};\n    cout << v << endl;\n}\n",
     "stderr": "main.cpp: In function 'int main()':\nmain.cpp:5:10: error: no match for 'operator<<' (operand types are 'std::ostream' {aka 'std::basic_ostream<char>'} and 'std::vector<int>')\n    5 |     cout << v << endl;\n      |     ~~~~ ^~ ~\n      |     |       |\n      |     |       std::vector<int>\n      |     std::ostream {aka std::basic_ostream<char>}\n"},
    {"id": "ambig", "title": "count + using namespace std",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint count = 0;\nint main() {\n    count++;\n    cout << count;\n}\n",
     "stderr": "main.cpp: In function 'int main()':\nmain.cpp:5:5: error: reference to 'count' is ambiguous\n    5 |     count++;\n      |     ^~~~~\nmain.cpp:3:5: note: candidates are: 'int count'\n    3 | int count = 0;\n      |     ^~~~~\n"},
    {"id": "undefref", "title": "undefined reference (brak definicji)",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint licz(int x);\nint main() {\n    cout << licz(5);\n}\n",
     "stderr": "/usr/bin/ld: /tmp/ccQw3kVd.o: in function `main':\nmain.cpp:(.text.startup+0x13): undefined reference to `licz(int)'\ncollect2: error: ld returned 1 exit status\n"},
    {"id": "retnv", "title": "Brak return (ostrzeżenie)",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint szukaj(vector<int>& v, int x) {\n    for (int i = 0; i < (int)v.size(); i++)\n        if (v[i] == x) return i;\n}\nint main() { vector<int> v = {1, 2}; cout << szukaj(v, 3); }\n",
     "stderr": "main.cpp: In function 'int szukaj(std::vector<int>&, int)':\nmain.cpp:6:1: warning: control reaches end of non-void function [-Wreturn-type]\n    6 | }\n      | ^\n"},
    {"id": "clangmap", "title": "clang: const map i operator[]",
     "source": "#include <bits/stdc++.h>\nusing namespace std;\nint f(const map<int,int>& m) { return m[1]; }\nint main() {}\n",
     "stderr": "main.cpp:3:39: error: no viable overloaded operator[] for type 'const map<int, int>'\n    3 | int f(const map<int,int>& m) { return m[1]; }\n      |                                       ~^~\n/Library/Developer/CommandLineTools/usr/include/c++/v1/map:1145:17: note: candidate function not viable: 'this' argument has type 'const map<int, int>', but method is not marked const\n1 error generated.\n"},
    {"id": "mainint", "title": "#define int long long",
     "source": "#include <bits/stdc++.h>\n#define int long long\nusing namespace std;\nint main() {\n    int n; cin >> n;\n}\n",
     "stderr": "cc1plus: error: '::main' must return 'int'\n"},
    {"id": "runtime", "title": "Błąd wykonania: vector::_M_range_check",
     "source": "", "stderr": "terminate called after throwing an instance of 'std::out_of_range'\n  what():  vector::_M_range_check: __n (which is 5) >= this->size() (which is 3)\n", "runtime": True},
]


def handle_translate(body: dict) -> dict:
    stderr = body.get("stderr") or body.get("text") or ""
    if not str(stderr).strip():
        raise ApiError("Wklej komunikat kompilatora (stderr).")
    source = body.get("source") or body.get("code") or None
    items = translate(str(stderr), source)
    runtime = None
    if not items:
        rt = explain_runtime({"stderr": stderr, "verdict": "RE" if re.search(r"terminate|Assertion|Traceback|Segmentation|"
                                                                             r"Floating|Sanitizer|abort", stderr, re.I) else ""},
                             source)
        if rt.get("kind") not in ("ok",):
            runtime = rt
    return {"items": items, "summary": summarize(items), "runtime": runtime}


def handle_runtime(body: dict) -> dict:
    run = body.get("run") or body
    return {"explanation": explain_runtime(run, body.get("source"))}


def handle_examples(_q: dict) -> dict:
    return {"examples": EXAMPLES}


def handle_patterns(_q: dict) -> dict:
    return {"patterns": [{k: v for k, v in p.items() if not k.startswith("_")} for p in load_patterns()]}


ROUTES = {
    "POST /api/errors/translate": handle_translate,
    "POST /api/errors/runtime": handle_runtime,
    "GET /api/errors/examples": handle_examples,
    "GET /api/errors/patterns": handle_patterns,
}

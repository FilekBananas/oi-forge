"""Backend wizualnego debuggera oparty na GDB/MI (Linux, Windows/MinGW; na macOS gdb zwykle nie działa).

Zawiera:
- parser rekordów GDB/MI (`parse_mi_line`) — rekordy wyniku (^done, ^error, ^running), asynchroniczne (*stopped,
  =breakpoint-modified), strumienie (~ @ &) i znak zachęty `(gdb)`;
- parser wartości wypisywanych przez gdb (`parse_gdb_value`) — także z pretty printerami libstdc++
  ("std::vector of length 3, capacity 4 = {1, 2, 3}", "std::map with 2 elements = {[1] = 2, [3] = 4}",
  "<repeats 10 times>", "\"abc\"", "97 'a'", "@0x7ffe...: {...}");
- konwersję na wspólny format węzłów debuggera (`to_node`);
- pętlę śledzenia (`trace`) z wymiennym transportem (`GdbMI` = prawdziwy proces gdb, w testach atrapa).

Strategia jak w backendzie lldb: breakpointy na początkach ciągów wierszy tablicy linii pliku użytkownika,
wyłączanie trafionego breakpointu i włączanie poprzedniego (Continue bez przechodzenia krokowego nad pułapką),
jednorazowy breakpoint na adresie powrotu przy ostatniej lokalizacji funkcji (powroty z rekurencji).
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time

try:
    from ._dbg_lldb_driver import explain_crash, pretty_type, short_func
except ImportError:  # uruchomienie poza pakietem
    from _dbg_lldb_driver import explain_crash, pretty_type, short_func  # type: ignore


# ============================================================================================ parser MI
class MIParseError(ValueError):
    pass


_ESC = {"n": 10, "t": 9, "r": 13, "f": 12, "v": 11, "a": 7, "b": 8, "e": 27, '"': 34, "\\": 92, "'": 39, "?": 63}


def _parse_cstring(s: str, i: int) -> tuple[str, int]:
    """Literał C (MI): s[i] == '"'. Zwraca (tekst, indeks za cudzysłowem). Ósemkowe \\ooo = bajty UTF-8."""
    if s[i] != '"':
        raise MIParseError(f"oczekiwano '\"' na pozycji {i}")
    i += 1
    out = bytearray()
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            return out.decode("utf-8", "replace"), i + 1
        if c == "\\" and i + 1 < n:
            d = s[i + 1]
            if d in "01234567":
                j = i + 1
                while j < n and j < i + 4 and s[j] in "01234567":
                    j += 1
                out.append(int(s[i + 1:j], 8) & 0xFF)
                i = j
                continue
            if d == "x":
                j = i + 2
                while j < n and j < i + 4 and s[j] in "0123456789abcdefABCDEF":
                    j += 1
                out.append(int(s[i + 2:j] or "0", 16) & 0xFF)
                i = j
                continue
            out.append(_ESC.get(d, ord(d) if ord(d) < 128 else 63))
            i += 2
            continue
        out.extend(c.encode("utf-8"))
        i += 1
    raise MIParseError("niezakończony literał")


_IDENT = re.compile(r"[A-Za-z_][\w\-]*")


def _parse_value(s: str, i: int):
    c = s[i] if i < len(s) else ""
    if c == '"':
        return _parse_cstring(s, i)
    if c == "{":
        return _parse_tuple(s, i)
    if c == "[":
        return _parse_list(s, i)
    raise MIParseError(f"nieoczekiwany znak {c!r} na pozycji {i}")


def _parse_result(s: str, i: int):
    m = _IDENT.match(s, i)
    if not m or m.end() >= len(s) or s[m.end()] != "=":
        raise MIParseError(f"oczekiwano zmienna= na pozycji {i}")
    val, j = _parse_value(s, m.end() + 1)
    return m.group(0), val, j


def _add(d: dict, k: str, v):
    if k in d:
        if isinstance(d[k], list) and getattr(d[k], "_multi", False):
            d[k].append(v)
        else:
            lst = _Multi([d[k], v])
            d[k] = lst
    else:
        d[k] = v


class _Multi(list):
    _multi = True


def _parse_tuple(s: str, i: int):
    assert s[i] == "{"
    i += 1
    d: dict = {}
    if i < len(s) and s[i] == "}":
        return d, i + 1
    while True:
        k, v, i = _parse_result(s, i)
        _add(d, k, v)
        if i < len(s) and s[i] == ",":
            i += 1
            continue
        if i < len(s) and s[i] == "}":
            return d, i + 1
        raise MIParseError(f"oczekiwano ',' lub '}}' na pozycji {i}")


def _parse_list(s: str, i: int):
    assert s[i] == "["
    i += 1
    out: list = []
    if i < len(s) and s[i] == "]":
        return out, i + 1
    while True:
        if s[i] in '"{[':
            v, i = _parse_value(s, i)
            out.append(v)
        else:
            _k, v, i = _parse_result(s, i)      # lista wyników: frame={...},frame={...} -> wartości
            out.append(v)
        if i < len(s) and s[i] == ",":
            i += 1
            continue
        if i < len(s) and s[i] == "]":
            return out, i + 1
        raise MIParseError(f"oczekiwano ',' lub ']' na pozycji {i}")


def parse_results(s: str, i: int = 0) -> dict:
    """Lista wyników po klasie rekordu: a="1",b={...}. Toleruje rozszerzenie gdb `bkpt={...},{...}`
    (dodatkowe lokalizacje bez nazwy — dołączane do poprzedniego klucza jako lista)."""
    d: dict = {}
    last = None
    n = len(s)
    while i < n:
        if s[i] == ",":
            i += 1
            continue
        if s[i] in "{[\"" and last is not None:
            v, i = _parse_value(s, i)
            _add(d, last, v)
            continue
        k, v, i = _parse_result(s, i)
        _add(d, k, v)
        last = k
    return d


_REC = re.compile(r"^(\d*)([\^*+=])([a-zA-Z][\w\-]*)")


def parse_mi_line(line: str) -> dict | None:
    """Jedna linia wyjścia GDB/MI -> {kind, token, cls, data} lub {kind, text}. Puste/nieznane -> None."""
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    if line.strip() in ("(gdb)", "(gdb) "):
        return {"kind": "prompt"}
    c = line[0]
    if c in "~@&":
        try:
            text, _ = _parse_cstring(line, 1)
        except MIParseError:
            text = line[1:]
        return {"kind": {"~": "console", "@": "target", "&": "log"}[c], "text": text}
    m = _REC.match(line)
    if not m:
        return {"kind": "raw", "text": line}
    token = int(m.group(1)) if m.group(1) else None
    kind = {"^": "result", "*": "exec", "+": "status", "=": "notify"}[m.group(2)]
    rest = line[m.end():]
    try:
        data = parse_results(rest[1:]) if rest.startswith(",") else {}
    except (MIParseError, IndexError) as e:
        data = {"_parse_error": str(e), "_raw": rest}
    return {"kind": kind, "token": token, "cls": m.group(3), "data": data}


# ============================================================================================ wartości gdb
class GV:
    """Sparsowana wartość z gdb. kind: scalar | string | seq | struct | map | error."""
    __slots__ = ("kind", "text", "items", "fields", "header", "more")

    def __init__(self, kind, text=None, items=None, fields=None, header=None, more=False):
        self.kind = kind
        self.text = text
        self.items = items
        self.fields = fields
        self.header = header
        self.more = more

    def __repr__(self):  # pragma: no cover - pomocnicze
        if self.kind in ("scalar", "string", "error"):
            return f"GV({self.kind}:{self.text!r})"
        if self.kind == "seq":
            return f"GV(seq{self.items!r}{'…' if self.more else ''})"
        return f"GV({self.kind}:{self.fields or self.items!r})"


_REPEATS = re.compile(r"\s*<repeats (\d+) times>")
_CHAR_LIT = re.compile(r"^(-?\d+) ('(?:\\.|\\\d{1,3}|[^'])*')$")


class _VP:
    def __init__(self, s: str, max_expand: int = 4096):
        self.s = s
        self.i = 0
        self.max_expand = max_expand

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\n":
            self.i += 1

    def peek(self, k=1):
        return self.s[self.i:self.i + k]

    def value(self) -> GV:
        self.ws()
        s = self.s
        if self.peek(3) == "@0x":                         # referencja: @0x7ffe...: wartość
            j = s.find(": ", self.i)
            if j > 0:
                self.i = j + 2
                return self.value()
        c = self.peek()
        if c == "{":
            return self.brace()
        if c == '"':
            return self.string()
        if c == "<":
            j = self.match_angle(self.i)
            txt = s[self.i:j]
            self.i = j
            kind = "error" if txt.startswith(("<error", "<optimized", "<incomplete", "<synthetic", "<unavailable")) else "scalar"
            return GV(kind, text=txt)
        if c == "(":                                       # (int *) 0x601040 <tab>
            j = self.match_paren(self.i)
            self.i = j
            self.ws()
            v = self.value()
            return v
        # nagłówek pretty printera: "std::vector of length 3, capacity 4 = {...}" lub "std::map with 0 elements"
        hdr = self.header()
        if hdr is not None:
            self.ws()
            if self.peek(2) == "= ":
                self.i += 2
                v = self.value()
                v.header = hdr
                return v
            v = GV("seq", items=[], header=hdr)
            return v
        tok = self.token()
        self.ws()
        if self.peek() == '"' and tok.startswith("0x"):     # char* : 0x4006f4 "hello"
            return self.string()
        if self.peek() == "<" and tok.startswith("0x") and not self.s.startswith("<repeats", self.i):
            j = self.match_angle(self.i)                     # wskaźnik na funkcję/symbol: 0x401136 <f(int)>
            self.i = j
        m = _CHAR_LIT.match(tok)
        if m:
            return GV("scalar", text=m.group(2))
        tok = re.sub(r"^(0x[0-9a-fA-F]+)\s+<[^<>]*(?:<[^<>]*>[^<>]*)*>$", r"\1", tok)
        return GV("scalar", text=tok)

    def header(self):
        s = self.s
        m = re.compile(r"(std::[\w:<>, ]*?(?: of length \d+(?:, capacity \d+)?| with \d+ elements?| containing| wrapping: )"
                       r"|std::[\w:]+(?:<[^=]*?>)?(?= = \{))").match(s, self.i)
        if not m:
            return None
        txt = m.group(0)
        self.i = m.end()
        if txt.endswith("wrapping: "):                       # std::queue wrapping: std::deque with 2 elements = {...}
            inner = self.header()
            return txt + (inner or "")
        return txt.strip()

    def token(self) -> str:
        s = self.s
        j = self.i
        depth = 0
        while j < len(s):
            c = s[j]
            if c in "([<" and not s.startswith("<repeats", j):
                depth += 1
            elif c in ")]>" and depth:
                depth -= 1
            elif depth == 0 and c in ",}":
                break
            elif depth == 0 and c == '"':
                break
            elif depth == 0 and s.startswith(" <repeats", j):
                break
            elif c == "'":
                k = j + 1
                while k < len(s) and s[k] != "'":
                    k += 2 if s[k] == "\\" else 1
                j = k
            j += 1
        tok = s[self.i:j].strip()
        self.i = j
        return tok

    def match_angle(self, i):
        depth = 0
        s = self.s
        while i < len(s):
            if s[i] == "<":
                depth += 1
            elif s[i] == ">":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return len(s)

    def match_paren(self, i):
        depth = 0
        s = self.s
        while i < len(s):
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return len(s)

    def string(self) -> GV:
        """Ciąg literałów: "abc", 'x' <repeats 20 times>, "def"... (gdb dzieli długie powtórzenia)."""
        parts = []
        more = False
        while True:
            self.ws()
            c = self.peek()
            if c == '"':
                txt, j = _parse_cstring(self.s, self.i)
                parts.append(txt)
                self.i = j
                if self.peek(3) == "...":
                    self.i += 3
                    more = True
            elif c == "'":
                j = self.i + 1
                while j < len(self.s) and self.s[j] != "'":
                    j += 2 if self.s[j] == "\\" else 1
                lit = self.s[self.i + 1:j]
                self.i = j + 1
                ch = _unescape_char(lit)
                m = _REPEATS.match(self.s, self.i)
                cnt = 1
                if m:
                    cnt = int(m.group(1))
                    self.i = m.end()
                parts.append(ch * min(cnt, self.max_expand))
            else:
                break
            save = self.i
            self.ws()
            if self.peek(2) == ", " and self.s[self.i + 2:self.i + 3] in ('"', "'"):
                self.i += 2
                continue
            self.i = save
            break
        txt = "".join(parts)
        if "\x00" in txt:
            txt = txt.split("\x00", 1)[0]
        return GV("string", text=txt, more=more)

    def brace(self) -> GV:
        s = self.s
        assert s[self.i] == "{"
        self.i += 1
        self.ws()
        if self.peek() == "}":
            self.i += 1
            return GV("seq", items=[])
        # rodzaj: [klucz] = wartość -> map ; nazwa = wartość -> struct ; inaczej lista wartości
        mode = None
        items: list = []
        fields: list = []
        more = False
        while True:
            self.ws()
            if self.peek(3) == "...":
                self.i += 3
                more = True
                self.ws()
            if self.peek() == "}":
                self.i += 1
                break
            if mode is None:
                if self.peek() == "[":
                    mode = "map"
                elif re.match(r"(?:static )?(?:<[^=]*?>|[A-Za-z_]\w*) = ", s[self.i:]):
                    mode = "struct"
                else:
                    mode = "seq"
            if mode == "map":
                j = self.match_bracket(self.i)
                key_txt = s[self.i + 1:j - 1]
                self.i = j
                self.ws()
                if self.peek(2) == "= ":
                    self.i += 2
                key = _VP(key_txt).value() if key_txt else GV("scalar", text="")
                val = self.value()
                items.append((key, val))
            elif mode == "struct":
                m = re.compile(r"(?:static )?(<[^=]*?>|[A-Za-z_]\w*) = ").match(s, self.i)
                if not m:
                    mode = "seq"
                    continue
                self.i = m.end()
                val = self.value()
                name = m.group(1)
                if not m.group(0).startswith("static "):
                    fields.append((name, val))
            else:
                val = self.value()
                rm = _REPEATS.match(s, self.i)
                if rm:
                    self.i = rm.end()
                    cnt = int(rm.group(1))
                    items.extend([val] * max(1, min(cnt, self.max_expand - len(items))))
                else:
                    items.append(val)
            self.ws()
            if self.peek(3) == "...":
                self.i += 3
                more = True
                self.ws()
            if self.peek() == ",":
                self.i += 1
                continue
            if self.peek() == "}":
                self.i += 1
                break
            if self.i >= len(s):
                break
            self.i += 1                                    # tolerancja na nieznaną składnię
        if mode == "map":
            return GV("map", items=items, more=more)
        if mode == "struct":
            return GV("struct", fields=fields, more=more)
        return GV("seq", items=items, more=more)

    def match_bracket(self, i):
        depth = 0
        s = self.s
        while i < len(s):
            if s[i] == "[":
                depth += 1
            elif s[i] == "]":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif s[i] == '"':
                _, i = _parse_cstring(s, i)
                continue
            i += 1
        return len(s)


def _unescape_char(lit: str) -> str:
    if not lit.startswith("\\"):
        return lit
    d = lit[1:]
    if d and d[0] in "01234567":
        return chr(int(d, 8))
    return chr(_ESC.get(d[:1], 63))


def parse_gdb_value(s: str) -> GV:
    """Tekst wartości z gdb -> GV. Nigdy nie rzuca wyjątku (w razie problemu zwraca skalar z oryginałem)."""
    if s is None:
        return GV("error", text="<brak>")
    try:
        return _VP(s).value()
    except Exception:  # noqa: BLE001
        return GV("scalar", text=s)


# ============================================================================================ -> węzły
_STD = r"(?:const )?std::(?:__\w+::)?"
_RX = {
    "string": re.compile(_STD + r"(?:basic_string|string|basic_string_view)\b"),
    "vecbool": re.compile(_STD + r"vector<bool"),
    "seq": re.compile(_STD + r"(vector|deque|list|forward_list|array|valarray|span)<"),
    "adaptor": re.compile(_STD + r"(queue|stack|priority_queue)<"),
    "map": re.compile(_STD + r"(?:multi)?map<|" + _STD + r"unordered_(?:multi)?map<"),
    "set": re.compile(_STD + r"(?:multi)?set<|" + _STD + r"unordered_(?:multi)?set<"),
    "pair": re.compile(_STD + r"pair<"),
    "tuple": re.compile(_STD + r"tuple<"),
    "bitset": re.compile(_STD + r"bitset<(\d+)>"),
}
_CARRAY = re.compile(r"^(.*?)\s*((?:\[\d+\])+)\s*$")
_SIZE = re.compile(r"(?:of length|with) (\d+)")


def _elem(gv: GV, depth: int = 1):
    """Element kontenera: skalar -> napis, w innym przypadku węzeł bez typu."""
    if gv.kind == "scalar":
        return gv.text
    if gv.kind == "error":
        return "?"
    if gv.kind == "string":
        return gv.text
    n = to_node(gv, "", depth)
    n.pop("type", None)
    return n


def _is_scalarish(gv: GV) -> bool:
    return gv.kind in ("scalar", "error")


def to_node(gv: GV, type_name: str = "", depth: int = 0, max_items: int = 256, max_rows: int = 48,
            max_cols: int = 48, max_map: int = 100) -> dict:
    """GV + nazwa typu z gdb -> węzeł {type, kind, value|items|rows|fields, size?, truncated?}."""
    t = (type_name or "").strip()
    if t.endswith("&"):
        t = t[:-1].strip()
    tname = pretty_type(t) if t else ""
    hdr = gv.header or ""
    size = None
    m = _SIZE.search(hdr)
    if m:
        size = int(m.group(1))
    if gv.kind == "error":
        return {"type": tname, "kind": "scalar", "value": gv.text or "?"}
    if gv.kind == "string":
        d = {"type": "string" if _RX["string"].match(t) else (tname or "string"), "kind": "string", "value": gv.text or ""}
        if gv.more:
            d["value"] += "…"
        return d
    if gv.kind == "scalar":
        return {"type": tname, "kind": "scalar", "value": gv.text or ""}
    bm = _RX["bitset"].match(t)
    if bm and gv.kind in ("map", "seq"):
        nbits = int(bm.group(1))
        bits = ["0"] * min(nbits, max_items)
        for k, v in (gv.items or []) if gv.kind == "map" else []:
            try:
                idx = int(k.text)
                if idx < len(bits):
                    bits[idx] = "1" if (v.text or "0") not in ("0", "false") else "0"
            except (TypeError, ValueError):
                pass
        d = {"type": tname, "kind": "array", "items": bits, "size": nbits}
        if nbits > len(bits):
            d["truncated"] = True
        return d
    is_map_type = bool(_RX["map"].match(t)) or ("map with" in hdr)
    if gv.kind == "map":
        entries = gv.items or []
        if _RX["tuple"].match(t) or "tuple" in hdr:
            return {"type": tname, "kind": "pair", "items": [_elem(v, depth + 1) for _k, v in entries]}
        keys_seq = all(k.kind == "scalar" and k.text == str(i) for i, (k, _v) in enumerate(entries))
        if not is_map_type and keys_seq:
            # np. std::list w starszych printerach: {[0] = 1, [1] = 2}
            gv = GV("seq", items=[v for _k, v in entries], header=gv.header, more=gv.more)
        else:
            n = size if size is not None else len(entries)
            items = [[_elem(k, depth + 1), _elem(v, depth + 1)] for k, v in entries[:max_map]]
            d = {"type": tname, "kind": "map", "items": items, "size": n}
            if n > len(items) or gv.more:
                d["truncated"] = True
            return d
    if gv.kind == "struct":
        fields = gv.fields or []
        names = [f for f, _ in fields]
        if _RX["pair"].match(t) or names == ["first", "second"]:
            fd = dict(fields)
            return {"type": tname, "kind": "pair", "items": [_elem(fd.get("first", GV("scalar", "?")), depth + 1),
                                                            _elem(fd.get("second", GV("scalar", "?")), depth + 1)]}
        if len(fields) == 1 and fields[0][0] in ("_M_elems", "__elems_", "c"):
            return to_node(fields[0][1], type_name, depth, max_items, max_rows, max_cols, max_map)
        if any(f in ("_M_impl", "_M_t", "_M_h") for f in names):
            return {"type": tname, "kind": "scalar", "value": "(brak pretty printerów gdb — włącz je, by zobaczyć elementy)"}
        out = {}
        for f, v in fields[:24]:
            if f.startswith("<"):                         # klasa bazowa: spłaszcz pola
                if v.kind == "struct":
                    for f2, v2 in (v.fields or [])[:24]:
                        out[f2] = _elem(v2, depth + 1)
                continue
            out[f] = _elem(v, depth + 1)
        return {"type": tname, "kind": "struct", "fields": out}
    # seq
    items = gv.items or []
    is_set = bool(_RX["set"].match(t)) or " set " in f" {hdr} " or hdr.startswith("std::set") or "unordered_set" in hdr
    ca = _CARRAY.match(t)
    dims = [int(x) for x in re.findall(r"\[(\d+)\]", ca.group(2))] if ca else []
    elem_t = ca.group(1).strip() if ca else ""
    two_d = (len(dims) >= 2) or (items and all(x.kind == "seq" and all(_is_scalarish(y) for y in (x.items or [])) for x in items[:max_rows])
                                  and any(x.items for x in items[:max_rows]))
    if two_d and items and depth == 0:
        rows = []
        maxc = 0
        trunc = gv.more or len(items) > max_rows
        cells = 0
        for r in items[:max_rows]:
            vals = [(y.text if y.kind != "error" else "?") for y in (r.items or [])[:max_cols]]
            if cells + len(vals) > 1024:
                trunc = True
                break
            rows.append(vals)
            cells += len(vals)
            maxc = max(maxc, len(r.items or []))
            if len(r.items or []) > max_cols or r.more:
                trunc = True
        nrows = dims[0] if dims else (size if size is not None else len(items))
        ncols = dims[1] if len(dims) >= 2 else maxc
        if elem_t in ("char", "signed char", "unsigned char") and rows:
            pass
        d = {"type": tname, "kind": "matrix", "rows": rows, "size": [nrows, ncols]}
        if trunc or nrows > len(rows):
            d["truncated"] = True
        return d
    m_items = items[:max_items if not is_set else max_map]
    out_items = [_elem(x, depth + 1) for x in m_items]
    if dims:
        n = dims[0]
        kind = "array"
    else:
        n = size if size is not None else len(items)
        kind = "set" if is_set else "vector"
    d = {"type": tname, "kind": kind, "items": out_items, "size": n}
    if n > len(out_items) or gv.more:
        d["truncated"] = True
    adm = _RX["adaptor"].match(t)
    if adm:
        d["note"] = {"stack": "szczyt = ostatni", "queue": "przód = pierwszy",
                     "priority_queue": "kopiec (szczyt = [0])"}[adm.group(1)]
    return d


def is_simple_type(t: str) -> bool:
    """Czy gdb poda wartość w --simple-values (skalar, wskaźnik, enum) — tzn. nie tablica/struktura."""
    t = (t or "").strip()
    if not t or "[" in t:
        return False
    if t.endswith("*"):
        return True
    base = re.sub(r"\b(const|volatile|unsigned|signed|long|short)\b", " ", t).strip()
    return base in ("", "int", "char", "bool", "double", "float", "__int128", "size_t", "wchar_t",
                    "int64_t", "uint64_t", "int32_t", "uint32_t", "ll", "ull", "ld")


# ============================================================================================ transport
class GdbMI:
    """Proces gdb w trybie MI2: wysyłanie poleceń z tokenami, odbiór wyników i rekordów asynchronicznych."""

    def __init__(self, gdb_path: str, cwd: str | None = None):
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000
        else:
            kw["start_new_session"] = True
        self.proc = subprocess.Popen([gdb_path, "--interpreter=mi2", "--nx", "-q"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, **kw)
        self.q: queue.Queue = queue.Queue()
        self.async_q: queue.Queue = queue.Queue()
        self.token = 100
        self.console: list[str] = []
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self):
        for raw in iter(self.proc.stdout.readline, b""):
            rec = parse_mi_line(raw.decode("utf-8", "replace"))
            if rec is None:
                continue
            if rec["kind"] == "exec":
                self.async_q.put(rec)
            elif rec["kind"] == "result":
                self.q.put(rec)
            elif rec["kind"] in ("console", "target", "log"):
                self.console.append(rec["text"])
                if len(self.console) > 2000:
                    del self.console[:1000]
        self.q.put({"kind": "eof"})
        self.async_q.put({"kind": "eof"})

    def cmd(self, command: str, timeout: float = 15.0) -> dict:
        self.token += 1
        tok = self.token
        try:
            self.proc.stdin.write(f"{tok}{command}\n".encode("utf-8"))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return {"kind": "eof", "cls": "error", "data": {"msg": "gdb zakończył działanie"}}
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return {"kind": "timeout", "cls": "error", "data": {"msg": f"gdb nie odpowiedział: {command}"}}
            try:
                rec = self.q.get(timeout=left)
            except queue.Empty:
                continue
            if rec.get("kind") == "eof":
                return {"kind": "eof", "cls": "error", "data": {"msg": "gdb zakończył działanie"}}
            if rec.get("token") == tok:
                return rec

    def wait_stopped(self, timeout: float) -> dict | None:
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                rec = self.async_q.get(timeout=left)
            except queue.Empty:
                return None
            if rec.get("kind") == "eof":
                return {"kind": "eof", "cls": "stopped", "data": {"reason": "gdb-exited"}}
            if rec.get("cls") == "stopped":
                return rec

    def interrupt(self):
        self.cmd("-exec-interrupt", timeout=3)

    def close(self):
        try:
            self.cmd("-gdb-exit", timeout=2)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.kill()
        except OSError:
            pass


def mi_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ============================================================================================ śledzenie
def _ok(rec: dict) -> bool:
    return rec.get("cls") in ("done", "running", "connected")


def _err(rec: dict) -> str:
    return (rec.get("data") or {}).get("msg", "") if isinstance(rec.get("data"), dict) else ""


def _same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.basename(a.replace("\\", "/")) == os.path.basename(b.replace("\\", "/"))


def _int(x, default=0) -> int:
    try:
        return int(str(x), 0)
    except (TypeError, ValueError):
        return default


SIGNALS_PL = {
    "SIGSEGV": "EXC_BAD_ACCESS/SIGSEGV", "SIGABRT": "SIGABRT abort", "SIGFPE": "SIGFPE", "SIGILL": "SIGILL",
    "SIGBUS": "SIGBUS",
}


class GdbTracer:
    def __init__(self, mi, cfg: dict, emit):
        self.mi = mi
        self.cfg = cfg
        self.emit = emit
        self.src = cfg["source"]
        self.user_lines = int(cfg.get("user_lines") or 10 ** 9)
        self.max_steps = int(cfg.get("max_steps") or 3000)
        self.budget = float(cfg.get("time_budget") or 25.0)
        self.max_items = int(cfg.get("max_items") or 256)
        self.watch = set(cfg.get("watch") or [])
        self.decl = cfg.get("decl_lines") or {}         # {funkcja: {zmienna: linia deklaracji}}
        self.global_names = list(cfg.get("globals") or [])
        self.t0 = time.time()
        self.steps = 0
        self.bps: dict[int, dict] = {}                  # numer -> {line, addr, func, exit}
        self.prev_bp: int | None = None
        self.ret_bp: int | None = None
        self.pending_ret: dict | None = None
        self.last_stop = None
        self.stack: list[dict] = []
        self.prev_depth = None
        self.types: dict = {}                           # (func, line) -> {nazwa: (typ, arg)}
        self.gtypes: dict = {}
        self.seen: dict = {}
        self.out_pos = 0

    def elapsed(self):
        return time.time() - self.t0

    def c(self, command, timeout=15.0):
        return self.mi.cmd(command, timeout)

    # --- przygotowanie
    def setup(self):
        for s in ("-gdb-set pagination off", "-gdb-set confirm off", "-gdb-set width 0", "-gdb-set height 0",
                  f"-gdb-set print elements {self.max_items}", "-gdb-set print repeats 10",
                  "-gdb-set print pretty off", "-gdb-set print frame-arguments scalars",
                  "-gdb-set breakpoint pending off", "-enable-pretty-printing"):
            self.c(s)
        r = self.c(f"-file-exec-and-symbols {mi_quote(self.cfg['exe'])}", timeout=60)
        if not _ok(r):
            raise GdbError("gdb nie wczytał programu: " + _err(r))
        r = self.c(f"-symbol-list-lines {mi_quote(self.src)}", timeout=30)
        if not _ok(r):
            # ścieżka w informacjach debugowych inna niż podana — weź plik z funkcją main
            rf = self.c("-file-list-exec-source-file")
            alt = (rf.get("data") or {}).get("fullname") or (rf.get("data") or {}).get("file")
            if _ok(rf) and alt:
                self.src = alt
                r = self.c(f"-symbol-list-lines {mi_quote(self.src)}", timeout=30)
        rows = []
        if _ok(r):
            for e in (r["data"].get("lines") or []):
                if isinstance(e, dict):
                    rows.append((_int(e.get("pc")), _int(e.get("line"))))
        if not rows:
            raise GdbError("gdb nie znalazł informacji o liniach (czy program skompilowano z -g?). " + _err(r))
        rows.sort()
        starts = []
        prev_line = None
        for pc, line in rows:
            if line != prev_line and 0 < line <= self.user_lines:
                starts.append((pc, line))
            prev_line = line
        # funkcje i przesunięcia (wejście do funkcji = prolog -> breakpoint liniowy, który gdb przesuwa za prolog)
        lo = min(p for p, _ in rows)
        hi = max(p for p, _ in rows) + 1
        info = {}
        r = self.c(f"-data-disassemble -s {lo:#x} -e {hi:#x} -- 0", timeout=30)
        if _ok(r):
            for ins in (r["data"].get("asm_insns") or []):
                if isinstance(ins, dict):
                    info[_int(ins.get("address"))] = (ins.get("func-name") or "?", _int(ins.get("offset"), -1))
        last_by_func: dict = {}
        seen_lines_entry = set()
        for pc, line in starts:
            fn, off = info.get(pc, ("?", -1))
            if off == 0:
                if (fn, line) in seen_lines_entry:
                    continue
                seen_lines_entry.add((fn, line))
                r = self.c(f"-break-insert {mi_quote(f'{self.src}:{line}')}")
            else:
                r = self.c(f"-break-insert *{pc:#x}")
            if not _ok(r):
                continue
            bk = r["data"].get("bkpt")
            if isinstance(bk, list):
                bk = bk[0]
            if not isinstance(bk, dict):
                continue
            num = _int(bk.get("number"))
            self.bps[num] = {"line": line, "addr": pc, "func": short_func(fn), "exit": False}
            if fn not in last_by_func or pc > last_by_func[fn][0]:
                last_by_func[fn] = (pc, num)
        for _pc, num in last_by_func.values():
            self.bps[num]["exit"] = True
        if not self.bps:
            raise GdbError("Nie udało się ustawić breakpointów w gdb.")
        self.c("-gdb-set breakpoint always-inserted on")

    def run(self):
        cfg = self.cfg
        args = f"< {mi_quote(cfg['input_path'])[1:-1]!s}"
        r = self.c(f"-exec-arguments < {_shell_path(cfg['input_path'])} > {_shell_path(cfg['output_path'])} "
                   f"2> {_shell_path(cfg['stderr_path'])}")
        del args, r
        lines = sorted(set(b["line"] for b in self.bps.values()))
        self.emit({"t": "info", "lines": lines, "globals": self.global_names, "backend": "gdb"})
        r = self.c("-exec-run", timeout=30)
        if not _ok(r):
            raise GdbError("Nie udało się uruchomić programu w gdb: " + _err(r))
        reason, message, exit_code = None, None, None
        while True:
            rec = self.mi.wait_stopped(max(0.5, self.budget - self.elapsed() + 1))
            if rec is None:
                reason = "timeout"
                message = (f"Przekroczono budżet czasu ({self.budget:.0f} s) — program działa zbyt długo bez przejścia "
                           "przez nową linię (nieskończona pętla?).")
                self.mi.interrupt()
                rec2 = self.mi.wait_stopped(3)
                if rec2 is not None:
                    self.report_here(message, "timeout")
                break
            d = rec.get("data") or {}
            why = d.get("reason", "")
            if why in ("exited-normally", "exited", "gdb-exited"):
                reason = "exit"
                exit_code = _int(d.get("exit-code", "0"), 0) if why != "exited" else int(str(d.get("exit-code", "0")), 8)
                if why == "gdb-exited":
                    reason, message = "crash", "gdb zakończył działanie nieoczekiwanie."
                break
            if why == "exited-signalled":
                reason = "crash"
                message = explain_crash(f"{d.get('signal-name', '')} {d.get('signal-meaning', '')}")
                break
            if why == "signal-received":
                sig = d.get("signal-name", "")
                reason = "crash"
                message = explain_crash(f"{SIGNALS_PL.get(sig, sig)} ({d.get('signal-meaning', '')})")
                self.report_here(message, "crash")
                break
            if why == "breakpoint-hit":
                self.on_breakpoint(d)
            if self.steps >= self.max_steps:
                reason = "max_steps"
                message = f"Osiągnięto limit {self.max_steps} kroków — dalsza część programu nie została prześledzona."
                break
            if self.elapsed() > self.budget:
                reason = "timeout"
                message = f"Przekroczono budżet czasu ({self.budget:.0f} s) po {self.steps} krokach."
                break
            r = self.c("-exec-continue")
            if not _ok(r):
                reason, message = "crash", "gdb: " + _err(r)
                break
        out = self.read_out()
        if out:
            self.emit({"t": "out", "out": out})
        try:
            with open(cfg["stderr_path"], "rb") as f:
                err_txt = f.read(20000).decode("utf-8", "replace")
        except OSError:
            err_txt = ""
        self.emit({"t": "end", "reason": reason, "message": message, "exit_code": exit_code, "steps": self.steps,
                   "elapsed": round(self.elapsed(), 3), "stderr": err_txt})

    def read_out(self) -> str:
        try:
            with open(self.cfg["output_path"], "rb") as f:
                f.seek(self.out_pos)
                data = f.read(1 << 20)
        except OSError:
            return ""
        self.out_pos += len(data)
        return data.decode("utf-8", "replace")

    def on_breakpoint(self, d: dict):
        num = _int(d.get("bkptno"), -1)
        fr = d.get("frame") or {}
        is_ret = False
        info = self.bps.get(num)
        if self.ret_bp is not None and num == self.ret_bp:
            is_ret = True
            self.ret_bp = None
            if self.prev_bp is not None:
                self.c(f"-break-enable {self.prev_bp}")
                self.prev_bp = None
        elif info is not None:
            if self.prev_bp is not None and self.prev_bp != num:
                self.c(f"-break-enable {self.prev_bp}")
            self.c(f"-break-disable {num}")
            self.prev_bp = num
            if info["exit"]:
                self.arm_return(info)
        else:
            return
        line = _int(fr.get("line"), info["line"] if info else 0)
        if not _same_file(fr.get("fullname") or fr.get("file") or "", self.src) or not (0 < line <= self.user_lines):
            return
        fname = short_func(fr.get("func") or (info or {}).get("func") or "?")
        depth_r = self.c("-stack-info-depth 4000")
        total = _int((depth_r.get("data") or {}).get("depth"), 1)
        fid = f"{total}:{fname}"
        key = (fid, line)
        if key == self.last_stop and not is_ret:
            return
        self.last_stop = key
        step = self.collect(fname, line, total, fid)
        if is_ret and self.pending_ret:
            step["ret"] = self.pending_ret
        if is_ret or self.ret_bp is None:
            self.pending_ret = None
        self.emit(step)

    def arm_return(self, info):
        r = self.c("-stack-list-frames 1 1")
        frames = (r.get("data") or {}).get("stack") or []
        if not frames or not isinstance(frames[0], dict):
            return
        f1 = frames[0]
        if not _same_file(f1.get("fullname") or f1.get("file") or "", self.src):
            return
        addr = f1.get("addr")
        if not addr:
            return
        if self.ret_bp is not None:
            self.c(f"-break-delete {self.ret_bp}")
        r = self.c(f"-break-insert -t *{addr}")
        bk = (r.get("data") or {}).get("bkpt")
        if isinstance(bk, list):
            bk = bk[0]
        if isinstance(bk, dict):
            self.ret_bp = _int(bk.get("number"))
            self.pending_ret = {"from": info["func"]}

    def user_stack(self) -> list[dict]:
        r = self.c("-stack-list-frames 0 400")
        out = []
        for f in (r.get("data") or {}).get("stack") or []:
            if isinstance(f, dict) and _same_file(f.get("fullname") or f.get("file") or "", self.src):
                out.append({"func": short_func(f.get("func") or "?"), "line": _int(f.get("line")),
                            "level": _int(f.get("level"))})
        return out

    def collect(self, fname: str, line: int, total_depth: int, fid: str, frame_level: int = 0) -> dict:
        self.steps += 1
        # stos: odśwież przy zmianie głębokości lub funkcji
        if self.prev_depth != total_depth or not self.stack or self.stack[0]["func"] != fname:
            us = self.user_stack()
            n = len(us)
            stack = []
            for i, f in enumerate(us[:40]):
                sid = f"{total_depth - f['level']}:{f['func']}"
                old = next((e for e in self.stack if e["id"] == sid), None)
                stack.append({"func": f["func"], "line": f["line"], "id": sid,
                              "args": old["args"] if old else ""})
            self.stack = stack or [{"func": fname, "line": line, "id": fid, "args": ""}]
            self.user_depth = max(0, n - 1)
            if fid not in set(e["id"] for e in self.stack[1:]):
                self.seen.pop(fid, None)
        else:
            self.stack[0]["line"] = line
        self.prev_depth = total_depth
        vars_ = {}
        r = self.c(f"-stack-list-variables --thread 1 --frame {frame_level} --simple-values")
        vlist = (r.get("data") or {}).get("variables") or []
        seen = self.seen.setdefault(fid, set())
        decl = self.decl.get(fname) or {}
        complex_vars = []
        arg_parts = []
        for v in vlist:
            if not isinstance(v, dict):
                continue
            nm = v.get("name") or ""
            if not nm or nm.startswith("__") or nm == "this" or (self.watch and nm not in self.watch) or nm in vars_:
                continue
            is_arg = v.get("arg") == "1"
            if not is_arg:
                dl = decl.get(nm)
                if dl is not None and (dl > line or (dl == line and nm not in seen)):
                    continue
                seen.add(nm)
            typ = v.get("type") or ""
            if "value" in v:
                node = to_node(parse_gdb_value(v["value"]), typ)
            else:
                complex_vars.append((nm, typ, is_arg))
                node = None
            if node is not None:
                node["scope"] = "arg" if is_arg else "local"
                vars_[nm] = node
                if is_arg:
                    arg_parts.append(f"{nm}={node.get('value')}")
        for nm, typ, is_arg in complex_vars:
            r = self.c(f"-data-evaluate-expression --thread 1 --frame {frame_level} {mi_quote(nm)}")
            if not _ok(r):
                continue
            node = to_node(parse_gdb_value((r.get("data") or {}).get("value", "")), typ, max_items=self.max_items)
            node["scope"] = "arg" if is_arg else "local"
            vars_[nm] = node
            if is_arg:
                arg_parts.append(f"{nm}=…")
        if self.stack:
            self.stack[0]["args"] = ", ".join(arg_parts[:6])
        for nm in self.global_names:
            if self.watch and nm not in self.watch:
                continue
            typ = self.gtypes.get(nm)
            if typ is None:
                rr = self.c(f"-var-create - * {mi_quote(nm)}")
                typ = (rr.get("data") or {}).get("type", "") if _ok(rr) else ""
                if _ok(rr) and (rr.get("data") or {}).get("name"):
                    self.c(f"-var-delete {(rr.get('data') or {}).get('name')}")
                self.gtypes[nm] = typ
            r = self.c(f"-data-evaluate-expression {mi_quote(nm)}")
            if not _ok(r):
                continue
            node = to_node(parse_gdb_value((r.get("data") or {}).get("value", "")), typ, max_items=self.max_items)
            node["scope"] = "global"
            vars_["::" + nm if nm in vars_ else nm] = node
        step = {"t": "step", "line": line, "func": fname, "depth": getattr(self, "user_depth", 0),
                "stack": [dict(e) for e in self.stack], "vars": vars_, "frame": fid}
        out = self.read_out()
        if out:
            step["out"] = out
        return step

    def report_here(self, message: str, kind: str):
        us = self.user_stack()
        if not us:
            return
        top = us[0]
        self.c(f"-stack-select-frame {top['level']}")
        depth_r = self.c("-stack-info-depth 4000")
        total = _int((depth_r.get("data") or {}).get("depth"), 1)
        fid = f"{total - top['level']}:{top['func']}"
        step = self.collect(top["func"], top["line"], total, fid, frame_level=top["level"])
        step[kind] = message
        self.emit(step)


class GdbError(Exception):
    pass


def _shell_path(p: str) -> str:
    """Ścieżka do przekierowania w argumentach programu (gdb uruchamia przez powłokę na POSIX)."""
    if os.name == "nt":
        return '"' + p + '"'
    return "'" + p.replace("'", "'\\''") + "'"


def trace(cfg: dict, emit, mi=None) -> None:
    """Śledzi program pod gdb. `emit(dict)` dostaje zdarzenia w formacie drivera lldb."""
    own = mi is None
    if own:
        mi = GdbMI(cfg["gdb"], cwd=cfg.get("cwd"))
    tr = GdbTracer(mi, cfg, emit)
    try:
        tr.setup()
        tr.run()
    except GdbError as e:
        emit({"t": "error", "message": str(e)})
    finally:
        if own:
            mi.close()

"""Driver wizualnego debuggera oparty na module `lldb` — uruchamiany jako OSOBNY proces.

Moduł `lldb` jest zbudowany dla Pythona systemowego (na macOS: /usr/bin/python3 = 3.9, ścieżka z `lldb -P`),
a nie dla Pythona serwera, dlatego ten plik jest samodzielnym skryptem (bez importów z `oiforge`) zgodnym
z Pythonem 3.6+. Komunikacja: konfiguracja w pliku JSON (argv[1]), wynik jako linie JSON na stdout:

    {"t": "info", ...}            po uruchomieniu (linie z breakpointami, pid, czasy)
    {"t": "step", ...}            jeden krok (linia, funkcja, stos, zmienne, nowe wyjście programu)
    {"t": "end", "reason": ...}   koniec: exit | crash | max_steps | timeout | cancelled
    {"t": "error", "message": .}  błąd krytyczny (np. brak uprawnień do debugowania)

Szybkość: zamiast StepInto (wpada w kod biblioteczny) stawiamy breakpointy na każdej linii pliku użytkownika
(jedno `BreakpointCreateBySourceRegex`) i robimy `Continue`. Trafioną lokalizację wyłączamy, a poprzednią
włączamy — dzięki temu `Continue` nie musi przechodzić krokowo nad pułapką (jedno przejście przez jądro na krok
zamiast dwóch). Żeby nie zgubić powrotów z rekurencji (ta sama lokalizacja trafiona dwa razy z rzędu w innych
ramkach), przy ostatniej lokalizacji funkcji stawiamy jednorazowy breakpoint na adresie powrotu.
"""
import json
import os
import re
import signal
import struct
import sys
import time

CFG = {}
OUT = sys.stdout
_emit_buf = []


def emit(obj, flush=False):
    _emit_buf.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    if flush or len(_emit_buf) >= 16:
        flush_out()


def flush_out():
    if _emit_buf:
        try:
            OUT.write("\n".join(_emit_buf) + "\n")
            OUT.flush()
        except (BrokenPipeError, OSError):
            pass
        del _emit_buf[:]


# ------------------------------------------------------------------------------------------- nazwy typów
_STD = r"std::(?:__1::|__cxx11::|__debug::|__[a-z0-9_]+::)?"
RX_STRING = re.compile(r"^(?:const )?" + _STD + r"basic_string<")
RX_STRING_VIEW = re.compile(r"^(?:const )?" + _STD + r"basic_string_view<")
RX_VECBOOL = re.compile(r"^(?:const )?" + _STD + r"vector<bool[,>]")
RX_SEQ = re.compile(r"^(?:const )?" + _STD + r"(vector|deque|list|forward_list|array|valarray|span)<")
RX_ADAPTOR = re.compile(r"^(?:const )?" + _STD + r"(queue|stack|priority_queue)<")
RX_MAP = re.compile(r"^(?:const )?" + _STD + r"(map|multimap|unordered_map|unordered_multimap)<")
RX_SET = re.compile(r"^(?:const )?" + _STD + r"(set|multiset|unordered_set|unordered_multiset)<")
RX_PAIR = re.compile(r"^(?:const )?" + _STD + r"pair<")
RX_TUPLE = re.compile(r"^(?:const )?" + _STD + r"tuple<")
RX_BITSET = re.compile(r"^(?:const )?" + _STD + r"bitset<")
RX_OPAQUE = re.compile(r"(iterator|__wrap_iter|__tree_const_iterator|__hash_iterator|function<|shared_ptr<|unique_ptr<|"
                       r"__gnu_pbds|basic_ostream|basic_istream|ios_base|mutex|thread|chrono|random|mersenne|"
                       r"uniform_int|lambda|\$_\d|__bit_reference|optional<|variant<|reference_wrapper)")
CONTIGUOUS = ("vector", "array", "valarray", "span")


def pretty_type(name):
    """Skraca nazwę typu: std::__1::vector<int, std::__1::allocator<int> > -> vector<int>."""
    if not name:
        return ""
    s = name
    s = re.sub(r"std::__(?:1|cxx11|debug)::", "std::", s)
    s = re.sub(r"std::basic_string<char, std::char_traits<char>, std::allocator<char> ?>", "string", s)
    s = re.sub(r"std::basic_string<char>", "string", s)
    for _ in range(4):
        s2 = re.sub(r", std::allocator<[^<>]*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>[^<>]*)*> ?>", ">", s)
        s2 = re.sub(r", std::less<[^<>]*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>[^<>]*)*> ?(?=[,>])", "", s2)
        s2 = re.sub(r", std::hash<[^<>]*(?:<[^<>]*>[^<>]*)*> ?, std::equal_to<[^<>]*(?:<[^<>]*>[^<>]*)*> ?(?=[,>])", "", s2)
        if s2 == s:
            break
        s = s2
    s = s.replace("std::", "")
    s = re.sub(r"\s+>", ">", s)
    s = s.replace("> >", ">>")
    return s


# ------------------------------------------------------------------------------------------- wartości
class Reader:
    """Zamienia SBValue na węzeł JSON: {type, kind, value | items | rows | fields, size?, truncated?}."""

    def __init__(self, lldb, target, process, max_items=256, max_rows=48, max_cols=48, max_cells=1024,
                 max_map=100, max_fields=24):
        self.lldb = lldb
        self.target = target
        self.process = process
        self.max_items = max_items
        self.max_rows = max_rows
        self.max_cols = max_cols
        self.max_cells = max_cells
        self.max_map = max_map
        self.max_fields = max_fields
        self.plans = {}
        self.tname_cache = {}
        self.elem_cache = {}
        self.err = lldb.SBError()
        self.basic_cache = {}
        self.vec_ok = {}
        self.blob = None
        self.blob_lo = self.blob_hi = 0
        self.little = process.GetByteOrder() != lldb.eByteOrderBig
        self.ptr_size = process.GetAddressByteSize() or 8

    # --- typy podstawowe: format struct dla szybkiego odczytu pamięci
    def basic_fmt(self, t):
        """(fmt, size, kind) dla typów wbudowanych (int, double, char, bool), inaczej None."""
        key = t.GetName()
        if key in self.basic_cache:
            return self.basic_cache[key]
        lldb = self.lldb
        res = None
        ct = t.GetCanonicalType()
        if ct.GetTypeClass() == lldb.eTypeClassBuiltin:
            bt = ct.GetBasicType()
            size = ct.GetByteSize()
            if bt == lldb.eBasicTypeBool:
                res = ("B", size, "bool") if size == 1 else None
            elif bt in (lldb.eBasicTypeChar, lldb.eBasicTypeSignedChar, lldb.eBasicTypeUnsignedChar):
                res = ("b" if bt != lldb.eBasicTypeUnsignedChar else "B", 1, "char")
            elif bt in (lldb.eBasicTypeFloat, lldb.eBasicTypeDouble):
                res = ("f", 4, "float32") if size == 4 else ("d", 8, "float")
            elif bt == lldb.eBasicTypeLongDouble:
                res = None
            else:
                signed = bool(ct.GetTypeFlags() & lldb.eTypeIsSigned)
                f = {1: "b", 2: "h", 4: "i", 8: "q"}.get(size)
                if f:
                    res = (f if signed else f.upper(), size, "int")
        self.basic_cache[key] = res
        return res

    def fmt_basic(self, x, kind):
        if kind == "bool":
            return "true" if x else "false"
        if kind == "char":
            c = x & 0xFF
            if 32 <= c < 127:
                return "'" + chr(c) + "'" if chr(c) not in "'\\" else "'\\" + chr(c) + "'"
            return {0: "'\\0'", 10: "'\\n'", 9: "'\\t'", 13: "'\\r'"}.get(c, str(x))
        if kind == "float":
            if x != x:
                return "nan"
            if x in (float("inf"), float("-inf")):
                return "inf" if x > 0 else "-inf"
            r = repr(float(x))
            if r.endswith(".0"):
                r = r[:-2]
            return r
        if kind == "float32":
            if x != x:
                return "nan"
            return "%.7g" % x
        return str(x)

    # --- pamięć: najpierw z bufora ramki (jeden odczyt SP..CFA na krok), potem bezpośrednio z procesu
    def set_blob(self, lo, data):
        self.blob = data
        self.blob_lo = lo
        self.blob_hi = lo + (len(data) if data else 0)

    def mem(self, addr, size):
        if size <= 0:
            return b""
        if self.blob is not None and self.blob_lo <= addr and addr + size <= self.blob_hi:
            o = addr - self.blob_lo
            return self.blob[o:o + size]
        data = self.process.ReadMemory(addr, size, self.err)
        if not self.err.Success() or data is None or len(data) < size:
            return None
        return data

    def unpack(self, data, count, bf):
        vals = struct.unpack(("<" if self.little else ">") + bf[0] * count, data[:count * bf[1]])
        k = bf[2]
        if k == "int":
            return [str(x) for x in vals]
        return [self.fmt_basic(x, k) for x in vals]

    def read_basic_array(self, addr, count, bf):
        if count <= 0:
            return []
        data = self.mem(addr, count * bf[1])
        if data is None:
            return None
        return self.unpack(data, count, bf)

    # --- szybka ścieżka dla zbuforowanych zmiennych
    def fast_info(self, v, p):
        """Dla skalarów, tablic C i std::vector typów prostych: adres + format (odczyt bez API wartości)."""
        lldb = self.lldb
        k = p[0]
        try:
            addr = v.GetLoadAddress()
        except Exception:  # noqa: BLE001
            return None
        if addr == lldb.LLDB_INVALID_ADDRESS or addr == 0:
            return None
        if k == "scalar" and p[1]:
            return {"k": "s", "addr": addr, "bf": p[1]}
        if k == "carray" and p[3]:
            return {"k": "a", "addr": addr, "bf": p[3], "count": p[1]}
        if k == "seq" and p[1] == "vector" and p[2]:
            et = v.GetType().GetCanonicalType()
            if et.IsReferenceType():
                et = et.GetDereferencedType().GetCanonicalType()
            et = et.GetTemplateArgumentType(0)
            if not et or not et.IsValid():
                return None
            bf = self.basic_fmt(et)
            if bf:
                return {"k": "vb", "addr": addr, "bf": bf}
            ep = self.plan(et)
            if ep[0] == "seq" and ep[1] == "vector" and ep[2]:
                it = et.GetCanonicalType().GetTemplateArgumentType(0)
                ibf = self.basic_fmt(it) if it and it.IsValid() else None
                if ibf:
                    return {"k": "vv", "addr": addr, "bf": ibf}
        return None

    def vec_hdr(self, addr):
        ps = self.ptr_size
        data = self.mem(addr, 3 * ps)
        if data is None:
            return None
        return struct.unpack(("<" if self.little else ">") + ("Q" if ps == 8 else "I") * 3, data)

    def render_cached(self, inf):
        v, tname, p, fast = inf
        if fast is not None:
            k = fast["k"]
            try:
                if k == "s":
                    bf = fast["bf"]
                    data = self.mem(fast["addr"], bf[1])
                    if data is not None:
                        return {"type": tname, "kind": "scalar", "value": self.unpack(data, 1, bf)[0]}
                elif k == "a":
                    count = fast["count"]
                    m = min(count, self.max_items)
                    items = self.read_basic_array(fast["addr"], m, fast["bf"])
                    if items is not None:
                        d = {"type": tname, "kind": "array", "items": items, "size": count}
                        if count > m:
                            d["truncated"] = True
                        return d
                elif k in ("vb", "vv"):
                    ok = self.vec_ok.get(tname)
                    if ok is not False:
                        r = self.vec_fast(fast, tname)
                        if ok is None:
                            ref = self.render(v, tname, p)
                            if r is not None and r == ref and (ref.get("size") or 0) > 0:
                                self.vec_ok[tname] = True
                            elif r is not None and r != ref:
                                self.vec_ok[tname] = False
                            return ref
                        if r is not None:
                            return r
            except Exception:  # noqa: BLE001
                pass
        return self.render(v, tname, p)

    def vec_fast(self, fast, tname):
        hdr = self.vec_hdr(fast["addr"])
        if hdr is None:
            return None
        b, e, c = hdr
        bf = fast["bf"]
        if fast["k"] == "vb":
            esz = bf[1]
            if not (b <= e <= c) or (e - b) % esz:
                return None
            n = (e - b) // esz
            if n > 1 << 28:
                return None
            m = min(n, self.max_items)
            items = self.read_basic_array(b, m, bf) if m else []
            if items is None:
                return None
            d = {"type": tname, "kind": "vector", "items": items, "size": n}
            if n > m:
                d["truncated"] = True
            return d
        # vector<vector<T>> -> macierz
        ps = self.ptr_size
        vsz = 3 * ps
        if not (b <= e <= c) or (e - b) % vsz:
            return None
        n = (e - b) // vsz
        if n > 1 << 24:
            return None
        r = min(n, self.max_rows)
        rows = []
        maxc = 0
        trunc = n > r
        cells = 0
        if r:
            data = self.mem(b, r * vsz)
            if data is None:
                return None
            hdrs = struct.unpack(("<" if self.little else ">") + ("Q" if ps == 8 else "I") * (3 * r), data)
            esz = bf[1]
            for i in range(r):
                rb, re_, rc = hdrs[3 * i], hdrs[3 * i + 1], hdrs[3 * i + 2]
                if not (rb <= re_ <= rc) or (re_ - rb) % esz:
                    return None
                cn = (re_ - rb) // esz
                cc = min(cn, self.max_cols)
                if cells + cc > self.max_cells:
                    trunc = True
                    break
                vals = self.read_basic_array(rb, cc, bf) if cc else []
                if vals is None:
                    return None
                if cn > cc:
                    trunc = True
                maxc = max(maxc, cn)
                cells += cc
                rows.append(vals)
        d = {"type": tname, "kind": "matrix", "rows": rows, "size": [n, maxc]}
        if trunc:
            d["truncated"] = True
        return d

    # --- plan dla typu (cache po nazwie)
    def plan(self, t):
        name = t.GetName()
        p = self.plans.get(name)
        if p is not None:
            return p
        lldb = self.lldb
        ct = t.GetCanonicalType()
        if ct.IsReferenceType():
            ct = ct.GetDereferencedType().GetCanonicalType()
        cname = ct.GetName() or ""
        tc = ct.GetTypeClass()
        if tc == lldb.eTypeClassBuiltin or tc == lldb.eTypeClassEnumeration:
            p = ("scalar", self.basic_fmt(ct))
        elif tc == lldb.eTypeClassPointer or tc == lldb.eTypeClassMemberPointer:
            pt = ct.GetPointeeType().GetCanonicalType()
            bf = self.basic_fmt(pt) if pt.IsValid() else None
            if bf and bf[2] == "char":
                p = ("cstr",)
            else:
                p = ("pointer",)
        elif tc == lldb.eTypeClassArray:
            et = ct.GetArrayElementType()
            esz = et.GetByteSize() or 1
            count = (ct.GetByteSize() // esz) if esz else 0
            bf = self.basic_fmt(et)
            ect = et.GetCanonicalType()
            if bf and bf[2] == "char":
                p = ("chararray", count)
            elif ect.GetTypeClass() == lldb.eTypeClassArray:
                p = ("carray2d", count, et)
            else:
                p = ("carray", count, et, bf)
        elif RX_STRING.match(cname) or RX_STRING_VIEW.match(cname):
            p = ("string",)
        elif RX_VECBOOL.match(cname):
            p = ("seq", "vector", False)
        elif RX_SEQ.match(cname):
            kind = RX_SEQ.match(cname).group(1)
            p = ("seq", kind, kind in CONTIGUOUS)
        elif RX_ADAPTOR.match(cname):
            p = ("adaptor", RX_ADAPTOR.match(cname).group(1))
        elif RX_MAP.match(cname):
            p = ("map",)
        elif RX_SET.match(cname):
            p = ("set",)
        elif RX_PAIR.match(cname):
            p = ("pair",)
        elif RX_TUPLE.match(cname):
            p = ("tuple",)
        elif RX_BITSET.match(cname):
            p = ("bitset",)
        elif RX_OPAQUE.search(cname) or cname.startswith("(lambda") or "lambda" in cname:
            p = ("opaque",)
        elif tc in (lldb.eTypeClassStruct, lldb.eTypeClassClass, lldb.eTypeClassUnion):
            p = ("struct",)
        else:
            p = ("opaque",)
        self.plans[name] = p
        return p

    # --- konwersja
    def info(self, v, fast=False):
        """(wartość po dereferencji, ładna nazwa typu, plan, szybka ścieżka) — liczone raz dla zbuforowanej SBValue."""
        t = v.GetType()
        if t.IsReferenceType():
            v = v.Dereference()
            t = v.GetType()
        tn = t.GetName() or ""
        tname = self.tname_cache.get(tn)
        if tname is None:
            tname = self.tname_cache[tn] = pretty_type(v.GetDisplayTypeName() or tn)
        p = self.plan(t)
        return (v, tname, p, self.fast_info(v, p) if fast else None)

    def node(self, v, depth=0):
        """Pełny węzeł (dict) dla wartości v."""
        v, tname, p, _ = self.info(v)
        return self.render(v, tname, p, depth)

    def render(self, v, tname, p, depth=0):
        k = p[0]
        if k == "scalar":
            return {"type": tname, "kind": "scalar", "value": self.scalar_text(v)}
        if k == "pointer":
            return {"type": tname, "kind": "scalar", "value": self.pointer_text(v)}
        if k == "cstr":
            s = v.GetSummary()
            if s and s.startswith('"'):
                return {"type": tname, "kind": "string", "value": unquote(s)}
            return {"type": tname, "kind": "scalar", "value": self.pointer_text(v)}
        if k == "string":
            s = v.GetSummary() or ""
            return {"type": "string" if "string" in tname else tname, "kind": "string", "value": unquote(s)}
        if k == "chararray":
            s = v.GetSummary() or ""
            return {"type": tname, "kind": "string", "value": unquote(s), "size": p[1]}
        if k == "carray":
            return self.carray(v, tname, p, depth)
        if k == "carray2d":
            return self.carray2d(v, tname, p)
        if k == "seq":
            return self.seq(v, tname, p, depth)
        if k == "adaptor":
            inner = v.GetChildMemberWithName("c")
            if not inner.IsValid() or inner.GetNumChildren() == 0 and not inner.MightHaveChildren():
                inner = v
            n = self.seq(inner, tname, ("seq", "deque", False), depth)
            n["type"] = tname
            if p[1] == "stack":
                n["note"] = "szczyt = ostatni"
            elif p[1] == "queue":
                n["note"] = "przód = pierwszy"
            elif p[1] == "priority_queue":
                n["note"] = "kopiec (szczyt = [0])"
            return n
        if k == "map":
            return self.mapnode(v, tname, depth)
        if k == "set":
            return self.setnode(v, tname, depth)
        if k == "pair":
            a = v.GetChildMemberWithName("first")
            b = v.GetChildMemberWithName("second")
            if not a.IsValid():
                a, b = v.GetChildAtIndex(0), v.GetChildAtIndex(1)
            return {"type": tname, "kind": "pair", "items": [self.elem(a, depth + 1), self.elem(b, depth + 1)]}
        if k == "tuple":
            n = min(v.GetNumChildren(), 12)
            return {"type": tname, "kind": "pair", "items": [self.elem(v.GetChildAtIndex(i), depth + 1) for i in range(n)]}
        if k == "bitset":
            n = v.GetNumChildren()
            m = min(n, self.max_items)
            items = []
            for i in range(m):
                c = v.GetChildAtIndex(i)
                val = c.GetValue() or "0"
                items.append("1" if val in ("true", "1") else "0")
            d = {"type": tname, "kind": "array", "items": items, "size": n}
            if n > m:
                d["truncated"] = True
            return d
        if k == "struct":
            return self.struct(v, tname, depth)
        s = v.GetSummary() or v.GetValue() or ""
        return {"type": tname, "kind": "scalar", "value": unquote(s) if s.startswith('"') else (s or "…")}

    def elem(self, v, depth):
        """Element kontenera: dla skalarów zwięzły napis, w pozostałych przypadkach węzeł."""
        t = v.GetType()
        if t.IsReferenceType():
            v = v.Dereference()
            t = v.GetType()
        p = self.plan(t)
        if p[0] == "scalar":
            return self.scalar_text(v)
        if p[0] == "pointer":
            return self.pointer_text(v)
        if p[0] in ("string", "chararray"):
            return unquote(v.GetSummary() or "")
        if depth > 3:
            s = v.GetSummary()
            return {"kind": "scalar", "type": pretty_type(t.GetName()), "value": s or "…"}
        n = self.node(v, depth)
        n.pop("type", None) if depth > 1 else None
        return n

    def scalar_text(self, v):
        s = v.GetValue()
        if s is None:
            s = v.GetSummary() or "?"
        return s

    def pointer_text(self, v):
        x = v.GetValueAsUnsigned(0)
        if x == 0:
            return "nullptr"
        return hex(x)

    def carray(self, v, tname, p, depth):
        count, et, bf = p[1], p[2], p[3]
        m = min(count, self.max_items)
        items = None
        if bf:
            addr = v.GetLoadAddress()
            if addr != self.lldb.LLDB_INVALID_ADDRESS:
                items = self.read_basic_array(addr, m, bf)
        if items is None:
            items = [self.elem(v.GetChildAtIndex(i), depth + 1) for i in range(m)]
        d = {"type": tname, "kind": "array", "items": items, "size": count}
        if count > m:
            d["truncated"] = True
        return d

    def carray2d(self, v, tname, p):
        rows_n = p[1]
        inner_t = p[2]
        ict = inner_t.GetCanonicalType()
        et = ict.GetArrayElementType()
        esz = et.GetByteSize() or 1
        cols_n = (ict.GetByteSize() // esz) if esz else 0
        bf = self.basic_fmt(et)
        r = min(rows_n, self.max_rows)
        c = min(cols_n, self.max_cols)
        if r * c > self.max_cells:
            r = max(1, self.max_cells // max(1, c))
        rows = []
        addr = v.GetLoadAddress()
        if bf and addr != self.lldb.LLDB_INVALID_ADDRESS and cols_n:
            flat = self.read_basic_array(addr, r * cols_n, bf) if r * cols_n <= 65536 else None
            if flat is not None:
                rows = [flat[i * cols_n: i * cols_n + c] for i in range(r)]
        if not rows:
            for i in range(r):
                row = v.GetChildAtIndex(i)
                rows.append([self.elem(row.GetChildAtIndex(j), 3) for j in range(c)])
        d = {"type": tname, "kind": "matrix", "rows": rows, "size": [rows_n, cols_n]}
        if bf and bf[2] == "char":
            d["elem"] = "char"
        if rows_n > r or cols_n > c:
            d["truncated"] = True
        return d

    def num_children(self, v, cap):
        try:
            return v.GetNumChildren(cap + 1)
        except TypeError:
            return v.GetNumChildren()

    def seq(self, v, tname, p, depth):
        kind, contiguous = p[1], p[2]
        n = self.num_children(v, 1 << 30)
        if n < 0 or n > 1 << 30:
            n = 0
        m = min(n, self.max_items)
        items = None
        first = v.GetChildAtIndex(0) if m else None
        if first is not None and contiguous and first.IsValid():
            ec = self.elem_cache.get(tname)
            if ec is None:
                et = first.GetType()
                bf = self.basic_fmt(et)
                if bf:
                    ec = ("basic", bf)
                elif self.plan(et)[0] == "seq":
                    ec = ("seq",)
                else:
                    ec = ("other",)
                self.elem_cache[tname] = ec
            if ec[0] == "basic":
                addr = first.GetLoadAddress()
                if addr != self.lldb.LLDB_INVALID_ADDRESS:
                    items = self.read_basic_array(addr, m, ec[1])
            elif ec[0] == "seq" and depth == 0:
                return self.vec2d(v, tname, n)
        if items is None:
            items = [self.elem(v.GetChildAtIndex(i), depth + 1) for i in range(m)]
        d = {"type": tname, "kind": "vector", "items": items, "size": n}
        if n > m:
            d["truncated"] = True
        return d

    def vec2d(self, v, tname, n):
        r = min(n, self.max_rows)
        rows = []
        maxc = 0
        trunc = n > r
        cells = 0
        for i in range(r):
            row = v.GetChildAtIndex(i)
            cn = self.num_children(row, 1 << 30)
            if cn < 0 or cn > 1 << 30:
                cn = 0
            c = min(cn, self.max_cols)
            if cells + c > self.max_cells:
                trunc = True
                break
            vals = None
            if c:
                f = row.GetChildAtIndex(0)
                bf = self.basic_fmt(f.GetType())
                if bf:
                    addr = f.GetLoadAddress()
                    if addr != self.lldb.LLDB_INVALID_ADDRESS:
                        vals = self.read_basic_array(addr, c, bf)
            if vals is None:
                vals = [self.elem(row.GetChildAtIndex(j), 3) for j in range(c)]
            if cn > c:
                trunc = True
            maxc = max(maxc, cn)
            cells += c
            rows.append(vals)
        d = {"type": tname, "kind": "matrix", "rows": rows, "size": [n, maxc]}
        if trunc:
            d["truncated"] = True
        return d

    def mapnode(self, v, tname, depth):
        n = self.num_children(v, 1 << 30)
        if n < 0 or n > 1 << 30:
            n = 0
        m = min(n, self.max_map)
        items = []
        for i in range(m):
            c = v.GetChildAtIndex(i)
            k = c.GetChildMemberWithName("first")
            val = c.GetChildMemberWithName("second")
            if not k.IsValid():
                k, val = c.GetChildAtIndex(0), c.GetChildAtIndex(1)
            if not k.IsValid():
                continue
            items.append([self.elem(k, depth + 1), self.elem(val, depth + 1)])
        d = {"type": tname, "kind": "map", "items": items, "size": n}
        if n > m:
            d["truncated"] = True
        return d

    def setnode(self, v, tname, depth):
        n = self.num_children(v, 1 << 30)
        if n < 0 or n > 1 << 30:
            n = 0
        m = min(n, self.max_map)
        items = [self.elem(v.GetChildAtIndex(i), depth + 1) for i in range(m)]
        d = {"type": tname, "kind": "set", "items": items, "size": n}
        if n > m:
            d["truncated"] = True
        return d

    def struct(self, v, tname, depth):
        if depth > 2:
            return {"type": tname, "kind": "scalar", "value": v.GetSummary() or "{…}"}
        n = v.GetNumChildren()
        fields = {}
        for i in range(min(n, self.max_fields)):
            c = v.GetChildAtIndex(i)
            nm = c.GetName() or ("#%d" % i)
            if nm.startswith("__") or nm.startswith("_M_"):
                continue
            fields[nm] = self.elem(c, depth + 1)
        d = {"type": tname, "kind": "struct", "fields": fields}
        if n > self.max_fields:
            d["truncated"] = True
        return d


def arg_repr(name, node):
    """Krótki opis argumentu do stosu wywołań: n=5, s="ab", v=[3 el.]."""
    if not isinstance(node, dict):
        return "%s=%s" % (name, node)
    k = node.get("kind")
    if k == "scalar":
        return "%s=%s" % (name, node.get("value"))
    if k == "string":
        s = node.get("value") or ""
        return '%s="%s"' % (name, s[:12] + ("…" if len(s) > 12 else ""))
    if "size" in node and isinstance(node["size"], int):
        return "%s=[%d el.]" % (name, node["size"])
    return "%s=…" % name


def unquote(s):
    if s and len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if s and s.startswith('"') and s.endswith('"...'):
        return s[1:-4] + "…"
    return s or ""


# ------------------------------------------------------------------------------------------- driver
LIB_FUNCS = re.compile(r"^(__cxx_global_var_init|_GLOBAL__sub_I|__cxx_global_array_dtor|__dtor_|_GLOBAL__D)")


class BpInfo(object):
    __slots__ = ("bp", "line", "addr", "func", "exit")

    def __init__(self, bp, line, addr, func):
        self.bp = bp
        self.line = line
        self.addr = addr
        self.func = func
        self.exit = False


class Tracer:
    def __init__(self, lldb, cfg):
        self.lldb = lldb
        self.cfg = cfg
        self.src = cfg["source"]
        self.src_base = os.path.basename(self.src)
        self.user_lines = int(cfg.get("user_lines") or 10 ** 9)
        self.max_steps = int(cfg.get("max_steps") or 3000)
        self.budget = float(cfg.get("time_budget") or 25.0)
        self.watch = set(cfg.get("watch") or [])
        self.t0 = time.time()
        self.steps = 0
        self.out_pos = 0
        self.out_f = None
        self.seen = {}          # (cfa, func) -> set((nazwa, linia deklaracji)) już zadeklarowanych zmiennych
        self.args_cache = {}    # (cfa, func) -> args string
        self.vcache = {}        # (cfa, func) -> {(nazwa, linia dekl.): info z Reader.info}
        self.meta = {}          # (func, linia) -> [(nazwa, linia dekl., czy argument)]
        self.gcache = {}        # nazwa globalnej -> info
        self.stack = []         # pełny stos ramek użytkownika (od góry): dict(func, line, id, args, key)
        self.last_stop = None
        self.prev_loc = None
        self.ret_bp = None
        self.pending_ret = None
        self.globals = []
        self.proc = None
        self.target = None
        self.dbg = None
        self.reader = None

    def elapsed(self):
        return time.time() - self.t0

    # --- przygotowanie
    def setup(self):
        lldb = self.lldb
        cfg = self.cfg
        lldb.SBDebugger.Initialize()
        dbg = self.dbg = lldb.SBDebugger.Create(False)
        ci = dbg.GetCommandInterpreter()

        def cmd(c):
            r = lldb.SBCommandReturnObject()
            ci.HandleCommand(c, r)
            return r
        self.cmd = cmd
        dsym = cfg.get("dsym")
        if dsym:
            cmd("settings set symbols.enable-external-lookup false")
        cmd("settings set target.move-to-nearest-code false")
        cmd("settings set target.process.thread.step-avoid-libraries true")
        cmd("settings set target.load-script-from-symbol-file false")
        dbg.SetAsync(True)
        err = lldb.SBError()
        target = self.target = dbg.CreateTarget(cfg["exe"], None, None, False, err)
        if not target or not target.IsValid():
            raise DriverError("Nie udało się wczytać programu do debuggera: %s" % (err.GetCString() or "?"))
        if dsym:
            r = cmd('target symbols add "%s"' % dsym)
            if not r.Succeeded():
                cmd("settings set symbols.enable-external-lookup true")
        self.resolve_source()
        self.bps = {}                    # id breakpointu -> BpInfo
        func_last = {}
        lines = set()
        addrs = self.statement_addresses()
        if not addrs:
            bp = target.BreakpointCreateBySourceRegex(".", lldb.SBFileSpec(self.src))
            for i in range(bp.GetNumLocations() if bp.IsValid() else 0):
                a = bp.GetLocationAtIndex(i).GetAddress()
                line = a.GetLineEntry().GetLine()
                if 0 < line <= self.user_lines:
                    addrs.append((a.GetFileAddress(), line))
            if bp.IsValid():
                target.BreakpointDelete(bp.GetID())
        for fa, line in addrs:
            sa = target.ResolveFileAddress(fa)
            fn = sa.GetFunction()
            fname = fn.GetName() if fn.IsValid() else ""
            if LIB_FUNCS.match(fname or ""):
                continue
            bp = target.BreakpointCreateBySBAddress(sa)
            if not bp.IsValid():
                continue
            self.bps[bp.GetID()] = BpInfo(bp, line, fa, short_func(fname or "?"))
            lines.add(line)
            if fn.IsValid():
                key = fn.GetStartAddress().GetFileAddress()
                if key not in func_last or fa > func_last[key][0]:
                    func_last[key] = (fa, bp.GetID())
        for _fa, bid in func_last.values():
            self.bps[bid].exit = True
        self.lines = sorted(lines)
        if not self.lines:
            raise DriverError("Debugger nie znalazł informacji o liniach programu (brak symboli -g?).")
        # globalne: z parsowania źródła + wszystkie zadeklarowane w pliku użytkownika
        names = list(cfg.get("globals") or [])
        found = target.FindGlobalVariables(".", 2000, lldb.eMatchTypeRegex)
        for i in range(found.GetSize()):
            g = found.GetValueAtIndex(i)
            d = g.GetDeclaration()
            if d.GetFileSpec().GetFilename() == self.src_base and 0 < d.GetLine() <= self.user_lines:
                nm = (g.GetName() or "").lstrip(":")
                if nm and "::" not in nm and nm not in names:
                    names.append(nm)
        self.global_names = names

    def resolve_source(self):
        """Ścieżka pliku w informacjach debugowych może różnić się od podanej (np. binarka z cache skompilowana
        z innej lokalizacji) — wtedy bierzemy plik jednostki kompilacji zawierającej `main`."""
        mod = self.target.GetModuleAtIndex(0)
        names = set()
        for ci in range(mod.GetNumCompileUnits()):
            fs = mod.GetCompileUnitAtIndex(ci).GetFileSpec()
            if fs.GetFilename():
                names.add(fs.GetFilename())
        if self.src_base in names:
            return
        scl = self.target.FindFunctions("main")
        for i in range(scl.GetSize()):
            cu = scl.GetContextAtIndex(i).GetCompileUnit()
            fs = cu.GetFileSpec() if cu.IsValid() else None
            if fs is not None and fs.GetFilename():
                self.src = os.path.join(fs.GetDirectory() or "", fs.GetFilename())
                self.src_base = fs.GetFilename()
                return

    def line_rows(self):
        """Wiersze tablicy linii (adres, plik, linia) w kolejności adresów — z `image dump line-table`
        (szybko, jedno polecenie), a w razie problemu przez SB API."""
        rows = []
        r = self.cmd('image dump line-table "%s"' % self.src_base)
        text = r.GetOutput() or "" if r.Succeeded() else ""
        rx = re.compile(r"^\s*(0x[0-9a-fA-F]+): (.*?)(?::(\d+))?(?::(\d+))?\s*$")
        for ln in text.splitlines():
            m = rx.match(ln)
            if not m:
                continue
            path = m.group(2)
            line = int(m.group(3)) if m.group(3) else 0
            rows.append((int(m.group(1), 16), os.path.basename(path), line))
        if rows:
            return rows
        lldb = self.lldb
        for mi in range(self.target.GetNumModules()):
            mod = self.target.GetModuleAtIndex(mi)
            for ci in range(mod.GetNumCompileUnits()):
                cu = mod.GetCompileUnitAtIndex(ci)
                for li in range(cu.GetNumLineEntries()):
                    le = cu.GetLineEntryAtIndex(li)
                    rows.append((le.GetStartAddress().GetFileAddress(), le.GetFileSpec().GetFilename() or "", le.GetLine()))
        return rows

    def statement_addresses(self):
        """Początki ciągów wierszy tej samej linii pliku użytkownika (po prologu funkcji). Dzięki temu np. pętla
        `for` ma osobne zatrzymanie na inkrementacji, a skok wstecz do warunku (środek ciągu) nie zatrzymuje."""
        rows = self.line_rows()
        rows.sort(key=lambda r: r[0])
        starts = []
        prev = None
        for addr, fname, line in rows:
            mine = fname == self.src_base and 0 < line <= self.user_lines
            if mine and not (prev is not None and prev[1] == fname and prev[2] == line):
                starts.append((addr, line))
            prev = (addr, fname, line)
        out = []
        seen = set()
        funcs_with = set()
        funcs_all = {}
        for addr, line in starts:
            if addr in seen:
                continue
            seen.add(addr)
            sa = self.target.ResolveFileAddress(addr)
            fn = sa.GetFunction()
            if fn.IsValid():
                fstart = fn.GetStartAddress().GetFileAddress()
                funcs_all.setdefault(fstart, (fn, line))
                if addr < fstart + fn.GetPrologueByteSize():
                    continue
                funcs_with.add(fstart)
            out.append((addr, line))
        # funkcje jednolinijkowe: brak początku ciągu po prologu — weź adres tuż za prologiem
        for fstart, (fn, line) in funcs_all.items():
            if fstart not in funcs_with:
                a = fstart + fn.GetPrologueByteSize()
                le = self.target.ResolveFileAddress(a).GetLineEntry()
                if le.GetFileSpec().GetFilename() == self.src_base and 0 < le.GetLine() <= self.user_lines:
                    out.append((a, le.GetLine()))
        out.sort()
        return out

    def launch(self):
        lldb = self.lldb
        cfg = self.cfg
        li = lldb.SBLaunchInfo([])
        li.SetWorkingDirectory(cfg["cwd"])
        li.AddOpenFileAction(0, cfg["input_path"], True, False)
        li.AddOpenFileAction(1, cfg["output_path"], False, True)
        li.AddOpenFileAction(2, cfg["stderr_path"], False, True)
        env = ["OIFORGE_DEBUG=1"]
        li.SetEnvironmentEntries(env, True)
        err = lldb.SBError()
        t = time.time()
        proc = self.proc = self.target.Launch(li, err)
        if not err.Success() or not proc or not proc.IsValid():
            raise DriverError(explain_launch_error(err.GetCString() or ""))
        self.listener = self.dbg.GetListener()
        st = self.wait_stop(max(5.0, self.budget))
        emit({"t": "info", "pid": proc.GetProcessID(), "lines": self.lines, "launch_s": round(time.time() - t, 3),
              "setup_s": round(t - self.t0, 3), "globals": self.global_names}, flush=True)
        return st

    def wait_stop(self, timeout):
        lldb = self.lldb
        ev = lldb.SBEvent()
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            if not self.listener.WaitForEvent(max(1, int(min(left, 1.0) + 0.999)), ev):
                if time.time() >= deadline:
                    return None
                continue
            if not lldb.SBProcess.EventIsProcessEvent(ev):
                continue
            st = lldb.SBProcess.GetStateFromEvent(ev)
            if st == lldb.eStateStopped and lldb.SBProcess.GetRestartedFromEvent(ev):
                continue
            if st in (lldb.eStateStopped, lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached):
                return st

    # --- odczyt stdout programu przyrostowo
    def read_out(self):
        try:
            if self.out_f is None:
                self.out_f = open(self.cfg["output_path"], "rb")
            self.out_f.seek(self.out_pos)
            data = self.out_f.read(1 << 20)
        except OSError:
            return ""
        if not data:
            return ""
        self.out_pos += len(data)
        return data.decode("utf-8", "replace")

    # --- główna pętla
    def run(self):
        lldb = self.lldb
        st = self.launch()
        reason = None
        message = None
        exit_code = None
        while True:
            if st is None:
                reason = "timeout"
                message = "Przekroczono budżet czasu (%.0f s) — program działa zbyt długo bez przejścia przez nową linię " \
                          "(nieskończona pętla?)." % self.budget
                self.interrupt_and_report(message)
                break
            if st == lldb.eStateExited:
                reason = "exit"
                exit_code = self.proc.GetExitStatus()
                break
            if st in (lldb.eStateCrashed, lldb.eStateDetached):
                reason = "crash"
                message = "Program zakończył się awaryjnie."
                break
            thread = self.proc.GetSelectedThread()
            sr = thread.GetStopReason()
            if sr in (lldb.eStopReasonException, lldb.eStopReasonSignal) or (
                    sr not in (lldb.eStopReasonBreakpoint, lldb.eStopReasonNone, lldb.eStopReasonTrace,
                               lldb.eStopReasonPlanComplete, lldb.eStopReasonInvalid)):
                desc = thread.GetStopDescription(256) or "błąd"
                if sr == lldb.eStopReasonSignal and thread.GetStopReasonDataAtIndex(0) in (signal.SIGTRAP,):
                    pass
                else:
                    reason = "crash"
                    message = explain_crash(desc)
                    self.report_crash(thread, message)
                    break
            if sr == lldb.eStopReasonBreakpoint:
                self.on_breakpoint(thread)
            if self.steps >= self.max_steps:
                reason = "max_steps"
                message = "Osiągnięto limit %d kroków — dalsza część programu nie została prześledzona." % self.max_steps
                break
            if self.elapsed() > self.budget:
                reason = "timeout"
                message = "Przekroczono budżet czasu (%.0f s) po %d krokach." % (self.budget, self.steps)
                break
            self.proc.Continue()
            st = self.wait_stop(max(0.5, self.budget - self.elapsed() + 1.0))
        out = self.read_out()
        if out:
            emit({"t": "out", "out": out})
        try:
            err_txt = open(self.cfg["stderr_path"], "rb").read(20000).decode("utf-8", "replace")
        except OSError:
            err_txt = ""
        emit({"t": "end", "reason": reason, "message": message, "exit_code": exit_code, "steps": self.steps,
              "elapsed": round(self.elapsed(), 3), "stderr": err_txt}, flush=True)

    def interrupt_and_report(self, message):
        lldb = self.lldb
        try:
            self.proc.Stop()
            st = self.wait_stop(3.0)
            if st == lldb.eStateStopped:
                th = self.proc.GetSelectedThread()
                self.report_crash(th, message, kind="timeout")
        except Exception:  # noqa: BLE001
            pass

    def first_user_frame(self, thread):
        n = min(thread.GetNumFrames(), 256)
        for i in range(n):
            fr = thread.GetFrameAtIndex(i)
            le = fr.GetLineEntry()
            fs = le.GetFileSpec()
            if fs.GetFilename() == self.src_base and 0 < le.GetLine() <= self.user_lines:
                return fr
        return None

    def report_crash(self, thread, message, kind="crash"):
        fr = self.first_user_frame(thread)
        if fr is None:
            return
        line = fr.GetLineEntry().GetLine()
        step = self.collect(thread, fr, line, short_func(fr.GetFunctionName() or "?"), fr.GetCFA())
        step[kind] = message
        emit(step, flush=True)

    def on_breakpoint(self, thread):
        bp_id = thread.GetStopReasonDataAtIndex(0)
        is_ret = False
        info = self.bps.get(bp_id)
        if self.ret_bp is not None and bp_id == self.ret_bp.GetID():
            self.target.BreakpointDelete(bp_id)
            self.ret_bp = None
            is_ret = True
            if self.prev_loc is not None:
                self.prev_loc.SetEnabled(True)
                self.prev_loc = None
        elif info is not None:
            # wyłącz trafioną lokalizację, włącz poprzednią — Continue bez przechodzenia krokowego nad pułapką
            if self.prev_loc is not None and self.prev_loc.GetID() != bp_id:
                self.prev_loc.SetEnabled(True)
            info.bp.SetEnabled(False)
            self.prev_loc = info.bp
            if info.exit:
                self.arm_return(thread)
        else:
            return
        fr = thread.GetFrameAtIndex(0)
        if info is not None:
            line, fname = info.line, info.func
        else:
            le = fr.GetLineEntry()
            line = le.GetLine()
            if le.GetFileSpec().GetFilename() != self.src_base or line <= 0 or line > self.user_lines:
                fr = self.first_user_frame(thread)
                if fr is None:
                    return
                line = fr.GetLineEntry().GetLine()
            fname = fr.GetFunctionName() or ""
            if LIB_FUNCS.match(fname):
                return
            fname = short_func(fname)
        cfa = fr.GetCFA()
        key = (cfa, line)
        if key == self.last_stop and not is_ret:
            return                       # ta sama linia w tej samej ramce — scalamy (jak `next` w gdb)
        self.last_stop = key
        step = self.collect(thread, fr, line, fname, cfa)
        if is_ret and self.pending_ret:
            ret = {"from": self.pending_ret["from"]}
            try:
                val = self.ret_value(thread.GetFrameAtIndex(0), self.pending_ret.get("rtype"))
            except Exception:  # noqa: BLE001
                val = None
            if val is not None:
                ret["value"] = val
            step["ret"] = ret
        if is_ret or not self.ret_bp:
            self.pending_ret = None
        emit(step)

    def arm_return(self, thread):
        """Ostatnia lokalizacja funkcji: jednorazowy breakpoint na adresie powrotu w kodzie użytkownika."""
        fr0 = thread.GetFrameAtIndex(0)
        fr1 = thread.GetFrameAtIndex(1)
        if not fr1.IsValid():
            return
        le = fr1.GetLineEntry()
        if le.GetFileSpec().GetFilename() != self.src_base:
            return
        pc = fr1.GetPC()
        if self.ret_bp is not None:
            self.target.BreakpointDelete(self.ret_bp.GetID())
        self.ret_bp = self.target.BreakpointCreateByAddress(pc)
        self.ret_bp.SetOneShot(True)
        fn = fr0.GetFunction()
        rtype = fn.GetType().GetFunctionReturnType() if fn.IsValid() else None
        self.pending_ret = {"from": short_func(fr0.GetFunctionName() or "?"), "rtype": rtype}

    def ret_value(self, fr, rtype):
        if rtype is None or not rtype.IsValid():
            return None
        bf = self.reader.basic_fmt(rtype)
        if not bf or bf[2] in ("float", "float32"):
            return None
        arch = self.target.GetTriple() or ""
        regname = "x0" if ("arm64" in arch or "aarch64" in arch) else "rax"
        r = fr.FindRegister(regname)
        if not r.IsValid():
            return None
        x = r.GetValueAsUnsigned(0)
        size = bf[1]
        x &= (1 << (8 * size)) - 1
        if bf[0].islower() and x >= 1 << (8 * size - 1):
            x -= 1 << (8 * size)
        return self.reader.fmt_basic(x, bf[2])

    # --- stos wywołań (przyrostowo: ramki poniżej znanej ramki się nie zmieniają)
    def update_stack(self, thread, fr, fname, line, cfa):
        prev = self.stack
        pos = {}
        for idx, e in enumerate(prev):
            pos.setdefault(e["key"], idx)
        top_key = (cfa, fname)
        if top_key in pos:
            p = pos[top_key]
            top = dict(prev[p])
            top["line"] = line
            new = [top] + prev[p + 1:]
            return new, (p == 0)
        walked = [{"func": fname, "line": line, "id": "%x" % cfa, "key": top_key, "args": None}]
        i = fr.GetFrameID() + 1
        limit = i + 4000
        while i < limit:
            f = thread.GetFrameAtIndex(i)
            i += 1
            if not f.IsValid():
                break
            le = f.GetLineEntry()
            if le.GetFileSpec().GetFilename() != self.src_base:
                continue
            fc = f.GetCFA()
            fn = short_func(f.GetFunctionName() or "?")
            k = (fc, fn)
            if k in pos:
                e = dict(prev[pos[k]])
                e["line"] = le.GetLine()
                return walked + [e] + prev[pos[k] + 1:], False
            args = self.args_cache.get(k)
            if args is None:
                args = self.args_cache[k] = self.args_text(f)
            walked.append({"func": fn, "line": le.GetLine(), "id": "%x" % fc, "key": k, "args": args})
            if fn == "main":
                break
        return walked, False

    # --- zbieranie stanu
    def collect(self, thread, fr, line, fname, cfa):
        lldb = self.lldb
        if self.reader is None:
            self.reader = Reader(lldb, self.target, self.proc, max_items=int(self.cfg.get("max_items") or 256))
        self.steps += 1
        stack, same_top = self.update_stack(thread, fr, fname, line, cfa)
        top_key = (cfa, fname)
        live = set(e["key"] for e in stack)
        if not same_top and top_key not in set(e["key"] for e in self.stack):
            # nowa aktywacja ramki (wywołanie) — zapomnij zbuforowane wartości o tym samym kluczu
            self.vcache.pop(top_key, None)
            self.seen.pop(top_key, None)
        for k in list(self.vcache):
            if k not in live:
                del self.vcache[k]
                self.seen.pop(k, None)
                self.args_cache.pop(k, None)
        self.stack = stack
        # zmienne lokalne i argumenty: metadane per (funkcja, linia), SBValue per aktywacja ramki
        mkey = (fname, line)
        meta = self.meta.get(mkey)
        fresh = None
        if meta is None:
            fresh = {}
            meta = []
            vl = fr.GetVariables(True, True, False, True)
            best = {}
            for i in range(vl.GetSize()):
                v = vl.GetValueAtIndex(i)
                nm = v.GetName()
                if not nm or nm.startswith("__") or nm == "this":
                    continue
                is_arg = v.GetValueType() == lldb.eValueTypeVariableArgument
                dl = v.GetDeclaration().GetLine()
                if not is_arg and dl > line:
                    continue
                if nm in best and best[nm][1] >= dl:
                    continue
                best[nm] = (v, dl, is_arg)
            for nm, (v, dl, is_arg) in best.items():
                meta.append((nm, dl, is_arg))
                fresh[(nm, dl)] = v
            meta.sort(key=lambda e: (not e[2], e[1], e[0]))
            self.meta[mkey] = meta
        cache = self.vcache.setdefault(top_key, {})
        seen = self.seen.setdefault(top_key, set())
        # jeden odczyt pamięci całej ramki (SP..CFA): skalary i tablice lokalne czytamy potem z bufora
        blob = None
        sp = fr.GetSP()
        if 0 < cfa - sp <= (1 << 18):
            err = lldb.SBError()
            blob = self.proc.ReadMemory(sp, cfa - sp, err)
            if not err.Success():
                blob = None
        self.reader.set_blob(sp, blob)
        vars_ = {}
        arg_parts = []
        for nm, dl, is_arg in meta:
            if self.watch and nm not in self.watch:
                continue
            if not is_arg:
                if dl == line and (nm, dl) not in seen:
                    continue
                seen.add((nm, dl))
            inf = cache.get((nm, dl))
            if inf is None:
                v = fresh.get((nm, dl)) if fresh else None
                if v is None:
                    v = fr.FindVariable(nm)
                if not v.IsValid():
                    continue
                inf = cache[(nm, dl)] = self.reader.info(v, True)
            try:
                node = self.reader.render_cached(inf)
            except Exception as e:  # noqa: BLE001
                node = {"kind": "scalar", "type": inf[1], "value": "<błąd odczytu: %s>" % e}
            if "lambda" in node.get("type", ""):
                continue
            node["scope"] = "arg" if is_arg else "local"
            vars_[nm] = node
            if is_arg and len(arg_parts) < 6:
                arg_parts.append(arg_repr(nm, node))
        if self.watch:
            arg_parts = []
            for nm, dl, is_arg in meta:
                if is_arg and len(arg_parts) < 6:
                    arg_parts.append("%s=…" % nm)
        self.reader.set_blob(0, None)
        args = ", ".join(arg_parts)
        self.args_cache[top_key] = args
        stack[0]["args"] = args
        for nm in self.global_names:
            if self.watch and nm not in self.watch:
                continue
            inf = self.gcache.get(nm)
            if inf is None:
                g = self.target.FindFirstGlobalVariable(nm)
                if not g.IsValid():
                    self.gcache[nm] = False
                    continue
                inf = self.gcache[nm] = self.reader.info(g, True)
            if inf is False:
                continue
            try:
                node = self.reader.render_cached(inf)
            except Exception as e:  # noqa: BLE001
                node = {"kind": "scalar", "type": inf[1], "value": "<błąd odczytu: %s>" % e}
            node["scope"] = "global"
            vars_["::" + nm if nm in vars_ else nm] = node
        out_stack = [{"func": e["func"], "line": e["line"], "id": e["id"], "args": e["args"] or ""} for e in stack[:40]]
        step = {"t": "step", "line": line, "func": fname, "depth": max(0, len(stack) - 1), "stack": out_stack,
                "vars": vars_, "frame": "%x" % cfa}
        out = self.read_out()
        if out:
            step["out"] = out
        return step

    def args_text(self, f):
        parts = []
        vl = f.GetVariables(True, False, False, True)
        for i in range(min(vl.GetSize(), 6)):
            v = vl.GetValueAtIndex(i)
            try:
                parts.append(arg_repr(v.GetName(), self.reader.node(v, 2)))
            except Exception:  # noqa: BLE001
                parts.append("%s=?" % v.GetName())
        return ", ".join(parts)

    def finish(self):
        try:
            if self.proc is not None and self.proc.IsValid():
                st = self.proc.GetState()
                if st not in (self.lldb.eStateExited, self.lldb.eStateDetached, self.lldb.eStateInvalid):
                    self.proc.Kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.out_f:
                self.out_f.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.lldb.SBDebugger.Destroy(self.dbg)
        except Exception:  # noqa: BLE001
            pass


class DriverError(Exception):
    pass


def short_func(name):
    """main(), fact(int) -> fact, std::__1::sort<...> -> sort, Foo::bar(int) const -> Foo::bar."""
    s = name or "?"
    depth = 0
    out = []
    for ch in s:
        if ch == "(" and depth == 0:
            break
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    r = "".join(out).strip()
    r = re.sub(r"^.*\s", "", r)
    r = r.replace("std::__1::", "").replace("std::", "")
    return r or s


def explain_launch_error(msg):
    m = msg or ""
    low = m.lower()
    if "not allowed" in low or "permission" in low or "attach failed" in low or "operation not permitted" in low:
        return ("System nie pozwolił debuggerowi przejąć procesu (uprawnienia debugowania). Na macOS uruchom w Terminalu "
                "`sudo DevToolsSecurity -enable` i dodaj użytkownika do grupy deweloperów: "
                "`sudo dseditgroup -o edit -a $(whoami) -t user _developer`, potem uruchom OI Forge ponownie. "
                "Szczegóły: " + m)
    if "developer mode" in low:
        return "Włącz tryb deweloperski: `sudo DevToolsSecurity -enable`. Szczegóły: " + m
    return "Nie udało się uruchomić programu pod debuggerem: " + (m or "nieznany błąd")


def explain_crash(desc):
    d = desc or ""
    low = d.lower()
    if "exc_bad_access" in low or "sigsegv" in low:
        hint = "odwołanie do niedozwolonej pamięci — wyjście poza tablicę, nullptr albo zbyt głęboka rekurencja"
    elif "sigabrt" in low or "abort" in low:
        hint = "program przerwał się sam (abort) — np. nieobsłużony wyjątek (out_of_range przy .at()), assert"
    elif "exc_arithmetic" in low or "sigfpe" in low:
        hint = "błąd arytmetyczny — dzielenie (lub modulo) przez zero"
    elif "exc_bad_instruction" in low or "sigill" in low:
        hint = "niedozwolona instrukcja — często funkcja bez `return`, albo wykryte niezdefiniowane zachowanie"
    elif "sigbus" in low:
        hint = "błąd szyny — niewyrównany lub niepoprawny adres pamięci"
    else:
        hint = "błąd wykonania"
    return "Program zatrzymał się z błędem: %s (%s)." % (hint, d)


def main():
    global CFG
    if len(sys.argv) < 2:
        print(json.dumps({"t": "error", "message": "Brak pliku konfiguracji."}))
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        CFG = json.load(f)
    p = CFG.get("lldb_path")
    if p and p not in sys.path:
        sys.path.insert(0, p)
    try:
        import lldb  # noqa: F401
    except Exception as e:  # noqa: BLE001
        emit({"t": "error", "message": "Nie można zaimportować modułu lldb w tym interpreterze Pythona: %s" % e}, flush=True)
        return 3
    tr = Tracer(lldb, CFG)

    def on_term(signum, frame):
        tr.finish()
        emit({"t": "end", "reason": "cancelled", "message": "Przerwano.", "steps": tr.steps}, flush=True)
        os._exit(1)
    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        tr.setup()
        tr.run()
    except DriverError as e:
        emit({"t": "error", "message": str(e)}, flush=True)
    except Exception as e:  # noqa: BLE001
        import traceback
        emit({"t": "error", "message": "Błąd debuggera: %s: %s" % (e.__class__.__name__, e),
              "trace": traceback.format_exc()}, flush=True)
    finally:
        tr.finish()
        flush_out()
    return 0


if __name__ == "__main__":
    sys.exit(main())

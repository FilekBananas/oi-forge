"""Output Difference Viewer — porównywanie wyjść programów (tokeny / linie / znaki).

Python API (używane przez testerkę i auto-testerkę):
    first_difference(a, b, mode="tokens", ignore_whitespace=True, float_eps=None) -> dict | None
    compare(a, b, mode="tokens", ignore_whitespace=True, float_eps=None, context=3, max_rows=3000) -> dict
    tokens_equal(x, y, float_eps) -> bool
HTTP:
    POST /api/diff {a, b, mode: tokens|lines|chars, ignore_whitespace, float_eps, context}
Konwencja: `a` = wyjście sprawdzane („Twoje”), `b` = wyjście wzorcowe („oczekiwane”).
"""
from __future__ import annotations

import math
import re

try:
    from oiforge.server_api import ApiError
except ImportError:  # pragma: no cover - użycie poza serwerem
    class ApiError(Exception):
        def __init__(self, message, status=400, **extra):
            super().__init__(message)
            self.message, self.status, self.extra = message, status, extra

MODES = ("tokens", "lines", "chars")
MAX_MYERS_D = 1000          # maksymalna „odległość edycyjna” liczona dokładnie (dalej: diff przybliżony)
MAX_INLINE_TOKENS = 400     # podświetlanie w linii: LCS po tokenach do tej długości
_NUM_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TOKEN_RE = re.compile(r"\S+")


# ---------------------------------------------------------------------------------------------- pomocnicze
def normalize_newlines(s: str | None) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _parse_float(t: str) -> float | None:
    if not _NUM_RE.fullmatch(t):
        if t.lower() in ("nan", "inf", "-inf", "+inf", "infinity", "-infinity"):
            try:
                return float(t)
            except ValueError:
                return None
        return None
    try:
        return float(t)
    except ValueError:
        return None


def tokens_equal(x: str, y: str, float_eps: float | None = None) -> bool:
    """Równość tokenów; przy float_eps liczby porównywane z tolerancją bezwzględną LUB względną."""
    if x == y:
        return True
    if float_eps is None:
        return False
    fx, fy = _parse_float(x), _parse_float(y)
    if fx is None or fy is None:
        return False
    if math.isnan(fx) or math.isnan(fy):
        return math.isnan(fx) and math.isnan(fy)
    if math.isinf(fx) or math.isinf(fy):
        return fx == fy
    diff = abs(fx - fy)
    return diff <= float_eps or diff <= float_eps * max(abs(fx), abs(fy))


def tokens_with_pos(s: str) -> list[tuple[str, int, int]]:
    """Lista (token, linia 1.., kolumna 1..)."""
    out = []
    for ln_no, line in enumerate(normalize_newlines(s).split("\n"), 1):
        for m in _TOKEN_RE.finditer(line):
            out.append((m.group(0), ln_no, m.start() + 1))
    return out


def _lines(s: str, ignore_whitespace: bool) -> list[str]:
    lines = normalize_newlines(s).split("\n")
    if ignore_whitespace:
        lines = [ln.rstrip() for ln in lines]
        while lines and not lines[-1]:
            lines.pop()
    elif lines and lines[-1] == "":
        lines.pop()         # bez ignorowania białych znaków usuwamy tylko jeden końcowy "\n"
    return lines


def _line_key(line: str, ignore_whitespace: bool):
    return tuple(line.split()) if ignore_whitespace else line


def _lines_equal(x: str, y: str, ignore_whitespace: bool, float_eps: float | None) -> bool:
    if not ignore_whitespace:
        return x == y
    tx, ty = x.split(), y.split()
    if len(tx) != len(ty):
        return False
    return all(tokens_equal(p, q, float_eps) for p, q in zip(tx, ty))


def _show(tok: str | None, limit: int = 60) -> str:
    if tok is None:
        return "(koniec)"
    return tok if len(tok) <= limit else tok[:limit] + "…"


def _char_name(c: str | None) -> str:
    if c is None:
        return "koniec tekstu"
    return {" ": "spacja", "\n": "nowa linia", "\t": "tabulator", "\r": "powrót karetki"}.get(c, f"„{c}”")


def _first_diff_col(x: str, y: str) -> int:
    n = min(len(x), len(y))
    for i in range(n):
        if x[i] != y[i]:
            return i + 1
    return n + 1


# ---------------------------------------------------------------------------------------------- pierwsza różnica
def first_difference(a: str, b: str, mode: str = "tokens", ignore_whitespace: bool = True,
                     float_eps: float | None = None, context: int = 3) -> dict | None:
    """Pierwsza różnica między `a` (sprawdzane) i `b` (wzorcowe). None, gdy równe w danym trybie."""
    mode = mode if mode in MODES else "tokens"
    if mode == "tokens":
        return _first_diff_tokens(a, b, float_eps, context)
    if mode == "lines":
        return _first_diff_lines(a, b, ignore_whitespace, float_eps)
    return _first_diff_chars(a, b, ignore_whitespace)


def _token_pos(s: str, idx: int) -> tuple[int, int] | None:
    """(linia, kolumna) tokenu nr idx (0..) — bez wyznaczania pozycji wszystkich tokenów."""
    count = 0
    for ln_no, line in enumerate(normalize_newlines(s).split("\n"), 1):
        k = len(line.split())
        if count + k > idx:
            for t, m in enumerate(_TOKEN_RE.finditer(line)):
                if count + t == idx:
                    return ln_no, m.start() + 1
        count += k
    return None


def _first_diff_tokens(a: str, b: str, float_eps: float | None, context: int) -> dict | None:
    ta, tb = a.split(), b.split()
    n = min(len(ta), len(tb))
    idx = None
    if float_eps is None:
        if ta[:n] != tb[:n]:
            lo, hi = 0, n          # wyszukiwanie binarne po prefiksach (porównania list w C)
            while hi - lo > 64:
                mid = (lo + hi) // 2
                if ta[lo:mid] == tb[lo:mid]:
                    lo = mid
                else:
                    hi = mid
            for i in range(lo, hi):
                if ta[i] != tb[i]:
                    idx = i
                    break
    else:
        for i in range(n):
            if not tokens_equal(ta[i], tb[i], float_eps):
                idx = i
                break
    if idx is None:
        if len(ta) == len(tb):
            return None
        idx = n
    pa = _token_pos(a, idx) if idx < len(ta) else None
    pb = _token_pos(b, idx) if idx < len(tb) else None
    tok_a = (ta[idx], *pa) if pa else None
    tok_b = (tb[idx], *pb) if pb else None
    last_a = _token_pos(a, len(ta) - 1) if (ta and not tok_a) else None
    last_b = _token_pos(b, len(tb) - 1) if (tb and not tok_b) else None
    if tok_a and tok_b:
        kind = "token"
        msg = (f"Pierwsza różnica: token nr {idx + 1} (linia {tok_a[1]}, znak {tok_a[2]}): "
               f"jest „{_show(tok_a[0])}”, oczekiwano „{_show(tok_b[0])}”.")
        fa, fb = _parse_float(tok_a[0]), _parse_float(tok_b[0])
        if fa is not None and fb is not None and float_eps is None and fa != fb and \
                abs(fa - fb) <= 1e-6 * max(1.0, abs(fb)) and ("." in tok_a[0] or "." in tok_b[0]):
            msg += " Liczby różnią się bardzo nieznacznie — może potrzebne porównanie z tolerancją (float)."
        elif tok_a[0].lower() == tok_b[0].lower():
            msg += " Różnica tylko w wielkości liter."
    elif tok_b:
        kind = "missing"
        msg = (f"Wyjście jest za krótkie: po {idx} tokenach brakuje „{_show(tok_b[0])}” "
               f"(oczekiwano łącznie {len(tb)} tokenów, jest {len(ta)}).")
    else:
        kind = "extra"
        msg = (f"Wyjście jest za długie: po {idx} poprawnych tokenach jest nadmiarowe „{_show(tok_a[0])}” "
               f"(oczekiwano {len(tb)} tokenów, jest {len(ta)}).")

    def line_text(s, ln):
        if not ln:
            return ""
        parts = normalize_newlines(s).split("\n", ln)
        return parts[ln - 1] if ln - 1 < len(parts) else ""
    lo, hi = max(0, idx - context), idx + context + 1
    return {
        "mode": "tokens", "kind": kind, "index": idx, "token_no": idx + 1,
        "line_a": tok_a[1] if tok_a else (last_a[0] if last_a else 1),
        "col_a": tok_a[2] if tok_a else None,
        "line_b": tok_b[1] if tok_b else (last_b[0] if last_b else 1),
        "col_b": tok_b[2] if tok_b else None,
        "line": tok_a[1] if tok_a else (tok_b[1] if tok_b else 1),
        "col": tok_a[2] if tok_a else (tok_b[2] if tok_b else 1),
        "token_a": tok_a[0] if tok_a else None,
        "token_b": tok_b[0] if tok_b else None,
        "context_a": ta[lo:hi], "context_b": tb[lo:hi], "context_start": lo,
        "line_text_a": line_text(a, tok_a[1]) if tok_a else "",
        "line_text_b": line_text(b, tok_b[1]) if tok_b else "",
        "tokens_a": len(ta), "tokens_b": len(tb),
        "message": msg,
    }


def _first_diff_lines(a: str, b: str, ignore_whitespace: bool, float_eps: float | None) -> dict | None:
    la, lb = _lines(a, ignore_whitespace), _lines(b, ignore_whitespace)
    n = min(len(la), len(lb))
    idx = None
    for i in range(n):
        if not _lines_equal(la[i], lb[i], ignore_whitespace, float_eps):
            idx = i
            break
    if idx is None:
        if len(la) == len(lb):
            return None
        idx = n
    xa = la[idx] if idx < len(la) else None
    xb = lb[idx] if idx < len(lb) else None
    if xa is not None and xb is not None:
        kind = "line"
        col = _first_diff_col(xa, xb)
        msg = f"Pierwsza różnica w linii {idx + 1}, znak {col}: „{_show(xa, 80)}” zamiast „{_show(xb, 80)}”."
    elif xb is not None:
        kind, col = "missing", 1
        msg = f"Brakuje linii {idx + 1} i dalszych (oczekiwano {len(lb)} linii, jest {len(la)})."
    else:
        kind, col = "extra", 1
        msg = f"Nadmiarowa linia {idx + 1} (oczekiwano {len(lb)} linii, jest {len(la)})."
    return {"mode": "lines", "kind": kind, "index": idx, "line": idx + 1, "col": col,
            "line_a": idx + 1, "line_b": idx + 1, "col_a": col, "col_b": col,
            "text_a": xa, "text_b": xb, "token_a": xa, "token_b": xb,
            "lines_a": len(la), "lines_b": len(lb), "message": msg}


def _first_diff_chars(a: str, b: str, ignore_whitespace: bool) -> dict | None:
    sa, sb = normalize_newlines(a), normalize_newlines(b)
    if ignore_whitespace:
        sa = "\n".join(_lines(sa, True))
        sb = "\n".join(_lines(sb, True))
    if sa == sb:
        return None
    n = min(len(sa), len(sb))
    i = 0
    while i < n and sa[i] == sb[i]:
        i += 1
    line = sa.count("\n", 0, i) + 1
    col = i - (sa.rfind("\n", 0, i) + 1) + 1
    ca = sa[i] if i < len(sa) else None
    cb = sb[i] if i < len(sb) else None
    kind = "char" if ca is not None and cb is not None else ("missing" if ca is None else "extra")
    msg = f"Pierwsza różnica na znaku {i + 1} (linia {line}, kolumna {col}): {_char_name(ca)} zamiast {_char_name(cb)}."
    return {"mode": "chars", "kind": kind, "index": i, "offset": i, "line": line, "col": col,
            "line_a": line, "col_a": col, "line_b": sb.count("\n", 0, i) + 1, "col_b": col,
            "char_a": ca, "char_b": cb, "token_a": ca, "token_b": cb,
            "context_a": sa[max(0, i - 20): i + 20], "context_b": sb[max(0, i - 20): i + 20], "message": msg}


# ---------------------------------------------------------------------------------------------- Myers
def myers(a: list, b: list, eq=None, max_d: int = MAX_MYERS_D) -> list[tuple[str, int, int]] | None:
    """Minimalny skrypt edycji (Myers O((N+M)D)). Zwraca listę ("eq"|"del"|"ins", i, j) albo None,
    gdy odległość przekracza max_d. Indeksy: i w `a`, j w `b` (dla ins i = pozycja w a, dla del j = pozycja w b)."""
    n, m = len(a), len(b)
    if eq is None:
        def eq(x, y):
            return x == y
    # wspólny prefiks / sufiks
    pre = 0
    while pre < n and pre < m and eq(a[pre], b[pre]):
        pre += 1
    suf = 0
    while suf < n - pre and suf < m - pre and eq(a[n - 1 - suf], b[m - 1 - suf]):
        suf += 1
    A, B = a[pre:n - suf], b[pre:m - suf]
    N, M = len(A), len(B)
    ops: list[tuple[str, int, int]] = [("eq", i, i) for i in range(pre)]
    if N == 0 or M == 0:
        ops += [("del", pre + i, pre) for i in range(N)]
        ops += [("ins", pre + N, pre + j) for j in range(M)]
    else:
        mid = _myers_core(A, B, eq, max_d)
        if mid is None:
            return None
        ops += [(op, i + pre, j + pre) for op, i, j in mid]
    ops += [("eq", n - suf + k, m - suf + k) for k in range(suf)]
    return ops


def _myers_core(A, B, eq, max_d):
    N, M = len(A), len(B)
    MAX = N + M
    lim = min(MAX, max_d)
    off = lim + 1
    v = [0] * (2 * lim + 3)
    trace = []
    found_d = None
    for d in range(lim + 1):
        trace.append(v[off - d - 1: off + d + 2])          # tylko potrzebny fragment (k ∈ [-d-1, d+1])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[off + k - 1] < v[off + k + 1]):
                x = v[off + k + 1]
            else:
                x = v[off + k - 1] + 1
            y = x - k
            while x < N and y < M and eq(A[x], B[y]):
                x += 1
                y += 1
            v[off + k] = x
            if x >= N and y >= M:
                found_d = d
                break
        if found_d is not None:
            break
    if found_d is None:
        return None
    # odtwarzanie ścieżki
    ops = []
    x, y = N, M
    for d in range(found_d, -1, -1):
        vd = trace[d]          # vd[idx] odpowiada k = idx - d - 1

        def V(k, _vd=vd, _d=d):
            return _vd[k + _d + 1]
        k = x - y
        if d == 0:
            while x > 0 and y > 0:
                ops.append(("eq", x - 1, y - 1))
                x -= 1
                y -= 1
            break
        if k == -d or (k != d and V(k - 1) < V(k + 1)):
            pk = k + 1
        else:
            pk = k - 1
        px = V(pk)
        py = px - pk
        while x > px and y > py:
            ops.append(("eq", x - 1, y - 1))
            x -= 1
            y -= 1
        if x == px:
            ops.append(("ins", x, y - 1))
        else:
            ops.append(("del", x - 1, y))
        x, y = px, py
    ops.reverse()
    return ops


def _approx_ops(n: int, m: int) -> list[tuple[str, int, int]]:
    """Przybliżony „diff” pozycyjny dla bardzo różnych tekstów."""
    ops = []
    for i in range(min(n, m)):
        ops.append(("sub", i, i))
    for i in range(m, n):
        ops.append(("del", i, m))
    for j in range(n, m):
        ops.append(("ins", n, j))
    return ops


# ---------------------------------------------------------------------------------------------- podświetlanie w linii
def inline_segments(x: str, y: str, float_eps: float | None = None) -> tuple[list, list]:
    """Segmenty [tekst, zmieniony?] dla linii x (sprawdzanej) i y (wzorcowej) — po tokenach z zachowaniem spacji."""
    px = re.findall(r"\s+|\S+", x)
    py = re.findall(r"\s+|\S+", y)
    wx = [p for p in px if not p.isspace()]
    wy = [p for p in py if not p.isspace()]
    if len(wx) > MAX_INLINE_TOKENS or len(wy) > MAX_INLINE_TOKENS:
        # prefiks/sufiks znakowy
        i = 0
        while i < min(len(x), len(y)) and x[i] == y[i]:
            i += 1
        j = 0
        while j < min(len(x), len(y)) - i and x[len(x) - 1 - j] == y[len(y) - 1 - j]:
            j += 1
        return ([[x[:i], False], [x[i:len(x) - j], True], [x[len(x) - j:], False]],
                [[y[:i], False], [y[i:len(y) - j], True], [y[len(y) - j:], False]])
    ops = myers(wx, wy, eq=lambda p, q: tokens_equal(p, q, float_eps), max_d=MAX_INLINE_TOKENS * 2) or []
    chg_x = set(i for op, i, _ in ops if op == "del")
    chg_y = set(j for op, _, j in ops if op == "ins")

    def build(parts, changed):
        segs, wi = [], 0
        for p in parts:
            if p.isspace():
                flag = False
            else:
                flag = wi in changed
                wi += 1
            if segs and segs[-1][1] == flag:
                segs[-1][0] += p
            else:
                segs.append([p, flag])
        return segs
    return build(px, chg_x), build(py, chg_y)


# ---------------------------------------------------------------------------------------------- pełne porównanie
def compare(a: str, b: str, mode: str = "tokens", ignore_whitespace: bool = True, float_eps: float | None = None,
            context: int = 3, max_rows: int = 3000, inline: bool = True) -> dict:
    """Pełny wynik: równość, pierwsza różnica, wiersze diffu (obok siebie) i statystyki."""
    mode = mode if mode in MODES else "tokens"
    a, b = normalize_newlines(a), normalize_newlines(b)
    eps = float_eps if (float_eps is not None and float_eps > 0) else None
    first = first_difference(a, b, mode, ignore_whitespace, eps)
    iw = ignore_whitespace or mode == "tokens"
    la, lb = _lines(a, iw), _lines(b, iw)

    def same(x, y):
        return x == y or (iw and _lines_equal(x, y, iw, eps))
    n, m = len(la), len(lb)
    pre = _common_prefix(la, lb)
    while pre < n and pre < m and same(la[pre], lb[pre]):
        pre += 1
    suf = 0
    while suf < n - pre and suf < m - pre and same(la[n - 1 - suf], lb[m - 1 - suf]):
        suf += 1
    A, B = la[pre:n - suf], lb[pre:m - suf]
    if eps is None:
        keys: dict = {}
        ka = [keys.setdefault(_line_key(x, iw), len(keys)) for x in A]
        kb = [keys.setdefault(_line_key(x, iw), len(keys)) for x in B]
        mid = myers(ka, kb, max_d=_dlimit(len(A) + len(B)))
    else:
        mid = myers(A, B, eq=lambda x, y: _lines_equal(x, y, iw, eps), max_d=_dlimit(len(A) + len(B)))
    approx = mid is None
    if approx:
        mid = _approx_ops(len(A), len(B))
    rows_out, truncated, counts = _rows_collapsed(_Ops(pre, suf, n, m, mid), la, lb, iw, eps, context, max_rows)
    if inline:
        for r in rows_out:
            if r["type"] == "chg":
                r["seg_a"], r["seg_b"] = inline_segments(r["a"], r["b"], eps)
    ta, tb = a.split(), b.split()
    nmin = min(len(ta), len(tb))
    if eps is None:
        diff_tok = sum(1 for x, y in zip(ta, tb) if x != y) + abs(len(ta) - len(tb))
    else:
        diff_tok = sum(1 for i in range(nmin) if not tokens_equal(ta[i], tb[i], eps)) + abs(len(ta) - len(tb))
    stats = {
        "lines_a": len(la), "lines_b": len(lb), "tokens_a": len(ta), "tokens_b": len(tb),
        "diff_lines": counts["chg"] + counts["add"] + counts["del"],
        "changed_lines": counts["chg"], "added_lines": counts["add"], "deleted_lines": counts["del"],
        "diff_tokens": diff_tok,
        "token_count_differs": len(ta) != len(tb),
        "missing_at_end": max(0, len(tb) - len(ta)),
        "extra_at_end": max(0, len(ta) - len(tb)),
        "approximate": approx,
    }
    equal = first is None
    hints = _hints(a, b, ta, tb, equal, eps, stats, first)
    return {"equal": equal, "mode": mode, "first": first, "rows": rows_out, "truncated": truncated,
            "stats": stats, "hints": hints, "summary": _summary(equal, first, stats)}


def _common_prefix(x: list, y: list) -> int:
    """Długość wspólnego prefiksu (dokładna równość) — wyszukiwanie binarne na porównaniach list (w C)."""
    n = min(len(x), len(y))
    if x[:n] == y[:n]:
        return n
    lo, hi = 0, n
    while hi - lo > 32:
        mid = (lo + hi) // 2
        if x[lo:mid] == y[lo:mid]:
            lo = mid
        else:
            hi = mid
    while lo < hi and x[lo] == y[lo]:
        lo += 1
    return lo


def _dlimit(total: int) -> int:
    # koszt Myersa ~ D² (pętla po przekątnych) + (N+M)·D — ograniczamy, żeby odpowiedź była szybka
    return max(50, min(MAX_MYERS_D, 20_000_000 // max(1, total)))


class _Ops:
    """Skrypt edycji: wspólny prefiks + środek (Myers) + wspólny sufiks — bez materializacji wszystkich linii."""

    def __init__(self, pre, suf, n, m, mid):
        self.pre, self.suf, self.n, self.m, self.mid = pre, suf, n, m, mid

    def runs(self):
        """Zdarzenia: ("eqrun", i, j, długość) albo (op, i, j)."""
        if self.pre:
            yield ("eqrun", 0, 0, self.pre)
        for op, i, j in self.mid:
            yield (op, i + self.pre, j + self.pre)
        if self.suf:
            yield ("eqrun", self.n - self.suf, self.m - self.suf, self.suf)


def _rows_collapsed(ops, la, lb, iw, eps, context, max_rows):
    """Wiersze obok siebie; długie fragmenty równe zwinięte do {"type": "skip"} (zostaje `context` linii)."""
    ctx = 3 if context is None else max(0, context)
    events = []
    pend_del, pend_ins = [], []
    eq_run = None

    def close_eq():
        nonlocal eq_run
        if eq_run:
            events.append(("eqrun", eq_run[0], eq_run[1], eq_run[2]))
            eq_run = None

    def flush():
        k = min(len(pend_del), len(pend_ins))
        for t in range(k):
            events.append(("chg", pend_del[t], pend_ins[t]))
        for i in pend_del[k:]:
            events.append(("del", i, None))
        for j in pend_ins[k:]:
            events.append(("add", None, j))
        pend_del.clear()
        pend_ins.clear()
    for ev in ops.runs():
        op = ev[0]
        if op in ("eq", "eqrun"):
            if pend_del or pend_ins:
                flush()
            i, j = ev[1], ev[2]
            L = ev[3] if op == "eqrun" else 1
            if eq_run and eq_run[0] + eq_run[2] == i and eq_run[1] + eq_run[2] == j:
                eq_run[2] += L
            else:
                close_eq()
                eq_run = [i, j, L]
        elif op == "sub":
            i, j = ev[1], ev[2]
            if pend_del or pend_ins:
                flush()
            if la[i] == lb[j] or _lines_equal(la[i], lb[j], iw, eps):
                if eq_run and eq_run[0] + eq_run[2] == i and eq_run[1] + eq_run[2] == j:
                    eq_run[2] += 1
                else:
                    close_eq()
                    eq_run = [i, j, 1]
            else:
                close_eq()
                events.append(("chg", i, j))
        else:
            close_eq()
            (pend_del.append(ev[1]) if op == "del" else pend_ins.append(ev[2]))
    flush()
    close_eq()
    counts = {"chg": 0, "add": 0, "del": 0}
    for e in events:
        if e[0] in counts:
            counts[e[0]] += 1
    any_change = any(e[0] != "eqrun" for e in events)
    out = []

    def eq_row(i, j):
        return {"type": "eq", "a_no": i + 1, "b_no": j + 1, "a": la[i], "b": lb[j]}
    for idx, e in enumerate(events):
        if len(out) > max_rows:
            break
        if e[0] == "eqrun":
            _, i, j, L = e
            if not any_change:
                keep_head, keep_tail = min(L, 2 * ctx + 20), 0
            else:
                keep_head = ctx if idx > 0 else 0
                keep_tail = ctx if idx < len(events) - 1 else 0
                if keep_head + keep_tail >= L:
                    keep_head, keep_tail = L, 0
            for t in range(keep_head):
                out.append(eq_row(i + t, j + t))
            skipped = L - keep_head - keep_tail
            if skipped > 0:
                out.append({"type": "skip", "count": skipped})
            for t in range(L - keep_tail, L):
                out.append(eq_row(i + t, j + t))
        elif e[0] == "chg":
            out.append({"type": "chg", "a_no": e[1] + 1, "b_no": e[2] + 1, "a": la[e[1]], "b": lb[e[2]]})
        elif e[0] == "del":
            out.append({"type": "del", "a_no": e[1] + 1, "b_no": None, "a": la[e[1]], "b": None})
        else:
            out.append({"type": "add", "a_no": None, "b_no": e[2] + 1, "a": None, "b": lb[e[2]]})
    truncated = False
    if len(out) > max_rows:
        out = out[:max_rows] + [{"type": "skip", "count": None, "truncated": True}]
        truncated = True
    return out, truncated, counts


def _hints(a, b, ta, tb, equal, eps, stats, first=None) -> list[str]:
    hints = []
    if equal:
        if a != b:
            if " ".join(ta) == " ".join(tb) and normalize_newlines(a).split("\n") != normalize_newlines(b).split("\n"):
                hints.append("Treść jest zgodna, różnią się tylko białe znaki (spacje / końce linii) — "
                             "sprawdzarka OI to akceptuje.")
        return hints
    ft = first or {}
    case_cand = ft.get("token_a") is not None and ft.get("token_b") is not None and \
        str(ft["token_a"]).lower() == str(ft["token_b"]).lower()
    if case_cand and len(ta) == len(tb) and [x.lower() for x in ta] == [y.lower() for y in tb]:
        hints.append("Różnica tylko w wielkości liter (np. „Tak” zamiast „TAK”) — popraw dokładny napis z treści.")
    num_cand = ft.get("token_a") is not None and ft.get("token_b") is not None and \
        tokens_equal(str(ft["token_a"]), str(ft["token_b"]), 1e-6)
    if eps is None and num_cand and len(ta) == len(tb) and all(tokens_equal(x, y, 1e-6) for x, y in zip(ta, tb)):
        hints.append("Liczby różnią się mniej niż o 1e-6 — jeśli zadanie dopuszcza błąd, porównuj z tolerancją. "
                     "W przeciwnym razie ustaw precyzję wypisywania (np. `cout << fixed << setprecision(9)`).")
    if stats["missing_at_end"] and ta == tb[:len(ta)]:
        hints.append(f"Wyjście urywa się — brakuje {stats['missing_at_end']} tokenów na końcu. "
                     "Czy program kończy się za wcześnie (return, wyjątek, za mała pętla)?")
    if stats["extra_at_end"] and tb == ta[:len(tb)]:
        hints.append(f"Na końcu jest {stats['extra_at_end']} nadmiarowych tokenów — może zostały komunikaty "
                     "debugowe albo pętla wypisuje o jeden element za dużo.")
    if not ta and tb:
        hints.append("Program nic nie wypisał. Sprawdź, czy nie czyta z pliku / czy nie zakończył się błędem.")
    if ta and tb and len(ta) == len(tb) and stats["diff_tokens"] == len(ta) and len(ta) > 3:
        hints.append("Wszystkie tokeny są różne — może zła kolejność wypisywania albo zupełnie inny wynik.")
    if ta and tb and len(ta) == len(tb) and sorted(ta) == sorted(tb) and ta != tb:
        hints.append("Te same wartości, ale w innej kolejności — jeśli zadanie dopuszcza dowolną kolejność, "
                     "użyj checkera „dowolna permutacja”.")
    return hints


def _summary(equal: bool, first: dict | None, stats: dict) -> str:
    if equal:
        return "Wyjścia są identyczne."
    if not first:
        return "Wyjścia się różnią."
    return first["message"]


# ---------------------------------------------------------------------------------------------- HTTP
def _float_or_none(v):
    if v in (None, "", False):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError("Tolerancja (float_eps) musi być liczbą, np. 1e-6.")
    return f if f > 0 else None


def handle_diff(body: dict) -> dict:
    a = body.get("a")
    b = body.get("b")
    if a is None or b is None:
        raise ApiError("Podaj oba teksty do porównania (a i b).")
    if not isinstance(a, str) or not isinstance(b, str):
        raise ApiError("Teksty muszą być napisami.")
    if len(a) + len(b) > 40_000_000:
        raise ApiError("Za duże teksty do porównania (limit ~40 MB łącznie).")
    mode = body.get("mode") or "tokens"
    if mode not in MODES:
        raise ApiError("Nieznany tryb porównania (tokens, lines, chars).")
    iw = body.get("ignore_whitespace")
    iw = True if iw is None else bool(iw)
    ctx = body.get("context")
    ctx = 3 if ctx is None else int(ctx)
    res = compare(a, b, mode=mode, ignore_whitespace=iw, float_eps=_float_or_none(body.get("float_eps")),
                  context=ctx, max_rows=int(body.get("max_rows") or 3000))
    res["ok"] = True
    return res


ROUTES = {
    "POST /api/diff": handle_diff,
}

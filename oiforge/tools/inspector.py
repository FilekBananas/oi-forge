"""Inspektor wejścia/wyjścia: rozpoznaje strukturę testu (tablice, macierze, siatki, grafy, drzewa, przedziały,
napisy, multitesty) i przygotowuje dane do czytelnych wizualizacji.

Silnik: linie wejścia dzielone na tokeny; nagłówki (1–4 liczby całkowite, np. `n m`, `n q`, `t`) uruchamiają
szablony sekcji (tablica n liczb, n×m macierz, m krawędzi, n−1 krawędzi drzewa, q zapytań…), a programowanie
dynamiczne po liniach wybiera najlepsze wyjaśnienie całego wejścia. Do tego interpretacje „całościowe” (lista liczb,
siatka bez nagłówka…). Każdy kandydat: {id, label, confidence, summary, parts:[{kind, label, ...dane}]}.

HTTP: POST /api/inspector/detect {text, hint?, output?, base?} -> {candidates, best, output_overlay, stats}
"""
from __future__ import annotations

import re

from oiforge.server_api import ApiError

MAX_LINES = 20000
MAX_TEXT = 4_000_000
LIMITS = {"array": 5000, "matrix_rows": 200, "matrix_cols": 200, "graph_nodes": 2000, "graph_edges": 6000,
          "intervals": 2000, "strings": 2000}

_INT = re.compile(r"^[+-]?\d+$")
_NUM = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

KIND_LABELS = {
    "array": "Tablica", "matrix": "Macierz", "grid": "Siatka znaków", "graph": "Graf", "tree": "Drzewo",
    "intervals": "Przedziały / zapytania", "points": "Pary liczb", "queries": "Zapytania", "strings": "Napisy",
    "values": "Parametry", "numbers": "Liczby", "text": "Tekst", "multitest": "Wiele testów", "table": "Tabela",
}


# ============================================================================================ model linii
class Lines:
    def __init__(self, text: str):
        raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while raw and not raw[-1].strip():
            raw.pop()
        while raw and not raw[0].strip():
            raw.pop(0)
        self.truncated = len(raw) > MAX_LINES
        raw = raw[:MAX_LINES]
        self.raw = raw
        self.toks = [ln.split() for ln in raw]
        self.n = len(raw)
        self.isint = [all(_INT.match(t) for t in tk) and bool(tk) for tk in self.toks]
        self.isnum = [all(_NUM.match(t) for t in tk) and bool(tk) for tk in self.toks]

    def ints(self, i):
        return [int(t) for t in self.toks[i]]

    def nums(self, i):
        out = []
        for t in self.toks[i]:
            out.append(int(t) if _INT.match(t) else float(t))
        return out

    def width(self, i):
        return len(self.toks[i])


def _num(t: str):
    return int(t) if _INT.match(t) else float(t)


# ============================================================================================ części (parts)
def part_values(names: list[str], vals: list, line: int) -> dict:
    return {"kind": "values", "label": "Parametry", "names": names, "values": vals, "lines": [line, line]}


def part_array(vals: list, l0: int, l1: int, label: str = "Tablica") -> dict:
    lim = LIMITS["array"]
    nums = [v for v in vals if isinstance(v, (int, float))]
    d = {"kind": "array", "label": label, "values": vals[:lim], "n": len(vals), "lines": [l0, l1],
         "truncated": len(vals) > lim}
    if nums:
        d["min"] = min(nums)
        d["max"] = max(nums)
        d["sum"] = sum(nums)
        d["sorted"] = all(nums[i] <= nums[i + 1] for i in range(len(nums) - 1))
    return d


def part_matrix(rows: list[list], l0: int, l1: int, label: str = "Macierz") -> dict:
    r = rows[:LIMITS["matrix_rows"]]
    r = [row[:LIMITS["matrix_cols"]] for row in r]
    flat = [x for row in rows for x in row if isinstance(x, (int, float))]
    d = {"kind": "matrix", "label": label, "rows": r, "n": len(rows), "m": max((len(x) for x in rows), default=0),
         "lines": [l0, l1], "truncated": len(rows) > len(r) or any(len(a) > LIMITS["matrix_cols"] for a in rows)}
    if flat:
        d["min"] = min(flat)
        d["max"] = max(flat)
    return d


def part_grid(rows: list[str], l0: int, l1: int) -> dict:
    counts: dict = {}
    for row in rows:
        for ch in row:
            counts[ch] = counts.get(ch, 0) + 1
    r = rows[:LIMITS["matrix_rows"]]
    return {"kind": "grid", "label": "Siatka", "rows": [x[:LIMITS["matrix_cols"]] for x in r], "n": len(rows),
            "m": max((len(x) for x in rows), default=0), "legend": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "lines": [l0, l1], "truncated": len(rows) > len(r) or any(len(x) > LIMITS["matrix_cols"] for x in rows)}


def _base_for(edges: list, n: int) -> int | None:
    """0 lub 1 — indeksowanie wierzchołków; None gdy krawędzie nie pasują do n."""
    if not edges:
        return 1
    lo = min(min(e[0], e[1]) for e in edges)
    hi = max(max(e[0], e[1]) for e in edges)
    if lo >= 1 and hi <= n:
        return 1
    if lo >= 0 and hi <= n - 1:
        return 0
    return None


def _is_tree(n: int, edges: list, base: int) -> bool:
    if len(edges) != n - 1:
        return False
    parent = list(range(n + 1))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        a, b = find(e[0] - base), find(e[1] - base)
        if a == b:
            return False
        parent[a] = b
    return True


def part_graph(n: int, edges: list, l0: int, l1: int, weighted: bool, base: int, kind: str = "graph") -> dict:
    deg = [0] * (n + base + 1)
    for e in edges:
        if 0 <= e[0] < len(deg):
            deg[e[0]] += 1
        if 0 <= e[1] < len(deg):
            deg[e[1]] += 1
    loops = sum(1 for e in edges if e[0] == e[1])
    multi = len(edges) - len({(min(e[0], e[1]), max(e[0], e[1])) for e in edges})
    d = {"kind": kind, "label": "Drzewo" if kind == "tree" else ("Graf ważony" if weighted else "Graf"), "n": n,
         "m": len(edges), "edges": edges[:LIMITS["graph_edges"]], "weighted": weighted, "base": base,
         "directed": False, "degrees": deg[base:base + n][:LIMITS["graph_nodes"]], "lines": [l0, l1],
         "loops": loops, "multi_edges": multi,
         "truncated": n > LIMITS["graph_nodes"] or len(edges) > LIMITS["graph_edges"]}
    if kind == "tree":
        d["root"] = base
    return d


def part_intervals(items: list, l0: int, l1: int, kind: str = "intervals") -> dict:
    lim = LIMITS["intervals"]
    d = {"kind": kind, "label": KIND_LABELS.get(kind, "Pary"), "items": items[:lim], "q": len(items),
         "lines": [l0, l1], "truncated": len(items) > lim}
    flat = [x for it in items for x in it if isinstance(x, (int, float))]
    if flat:
        d["min"] = min(flat)
        d["max"] = max(flat)
    return d


def part_strings(strs: list[str], l0: int, l1: int) -> dict:
    lim = LIMITS["strings"]
    return {"kind": "strings", "label": "Napisy", "items": strs[:lim], "q": len(strs), "lines": [l0, l1],
            "truncated": len(strs) > lim, "lengths": [len(s) for s in strs[:lim]]}


def part_text(lines: list[str], l0: int, l1: int) -> dict:
    return {"kind": "text", "label": "Tekst", "lines_text": lines[:200], "lines": [l0, l1]}


# ============================================================================================ szablony
def _grid_row(tok: list[str]) -> bool:
    if len(tok) != 1:
        return False
    s = tok[0]
    return len(s) >= 2 and not _NUM.match(s) or (len(s) >= 2 and set(s) <= set("01"))


HEADER_COST = 2.0


def _sc(lines: int, q: float, bonus: float = 0.0) -> float:
    """Wynik szablonu: jakość × liczba wyjaśnionych linii + premia za wiarygodność − koszt nagłówka."""
    return lines * q + bonus - HEADER_COST


def _templates(L: Lines, i: int) -> list[tuple[str, list[dict], int, float]]:
    """Szablony sterowane nagłówkiem w linii i. Zwraca [(nazwa, części, następna linia, wynik)]."""
    out = []
    if not L.isint[i] or not (1 <= L.width(i) <= 4):
        return out
    H = L.ints(i)
    N = L.n
    nm = ["n", "m", "q", "k"][: len(H)]
    head = part_values(nm, H, i)
    n = H[0]
    j = i + 1
    # --- jedna liczba
    if len(H) == 1 and 1 <= n <= 10 ** 7:
        if j < N and L.isnum[j] and L.width(j) == n:
            out.append(("array", [head, part_array(L.nums(j), j, j)], j + 1, _sc(1, 1.5, 2.2 + 0.15 * min(n, 20))))
        if n >= 2 and j + n <= N and all(L.isnum[k] and L.width(k) == 1 for k in range(j, j + n)):
            out.append(("array_v", [head, part_array([L.nums(k)[0] for k in range(j, j + n)], j, j + n - 1)], j + n,
                        _sc(n, 1.0, 1.0)))
        if n >= 2 and j + n - 1 <= N and all(L.isint[k] and L.width(k) in (2, 3) for k in range(j, j + n - 1)):
            edges = [L.ints(k) for k in range(j, j + n - 1)]
            w = all(len(e) == 3 for e in edges)
            if all(len(e) == len(edges[0]) for e in edges):
                base = _base_for(edges, n)
                if base is not None and _is_tree(n, edges, base):
                    out.append(("tree", [head, part_graph(n, edges, j, j + n - 2, w, base, "tree")], j + n - 1,
                                _sc(n - 1, 1.4, 3.0)))
        if n >= 2 and j < N and L.isint[j] and L.width(j) in (n - 1, n):
            ps = L.ints(j)
            edges = None
            base = 1
            if len(ps) == n - 1 and all(1 <= p <= n for p in ps):
                edges = [[p, k + 2] for k, p in enumerate(ps)]
            elif len(ps) == n - 1 and all(0 <= p <= n - 1 for p in ps):
                edges = [[p, k + 1] for k, p in enumerate(ps)]
                base = 0
            elif len(ps) == n and sum(1 for p in ps if p in (0, -1)) == 1 and all(-1 <= p <= n for p in ps):
                edges = [[p, k + 1] for k, p in enumerate(ps) if p > 0]
            if edges is not None and _is_tree(n, edges, base):
                pg = part_graph(n, edges, j, j, False, base, "tree")
                pg["parents"] = ps
                pg["label"] = "Drzewo (tablica rodziców)"
                pg["root"] = next((k + base for k in range(n) if not any(e[1] == k + base for e in edges)), base)
                pg["directed"] = True
                exact = 1.0 if len(ps) == n - 1 else 0.0
                out.append(("parents", [head, pg], j + 1, _sc(1, 1.5, 2.0 + exact + 0.1 * min(n, 20))))
        for w_ in (2, 3):
            if n >= 1 and j + n <= N and all(L.isnum[k] and L.width(k) == w_ for k in range(j, j + n)):
                items = [L.nums(k) for k in range(j, j + n)]
                kind = "intervals" if w_ == 2 and all(a <= b for a, b in items) else ("points" if w_ == 2 else "table")
                out.append((kind, [head, part_intervals(items, j, j + n - 1, kind)], j + n,
                            _sc(n, 1.0, 1.4 if kind == "intervals" else 0.6)))
        if j + n <= N and n >= 2 and all(L.isint[k] for k in range(j, j + n)):
            widths = {L.width(k) for k in range(j, j + n)}
            firsts = {L.toks[k][0] for k in range(j, j + n)}
            if len(widths) > 1 and len(firsts) <= 4 and min(widths) >= 2:
                items = [L.ints(k) for k in range(j, j + n)]
                out.append(("queries", [head, part_intervals(items, j, j + n - 1, "queries")], j + n, _sc(n, 1.0, 0.8)))
        if j + n <= N and all(not L.isnum[k] for k in range(j, j + n)):
            strs = [L.raw[k].strip() for k in range(j, j + n)]
            if n >= 2 and all(len(L.toks[k]) == 1 for k in range(j, j + n)) and len({len(x) for x in strs}) == 1 \
                    and len(strs[0]) == n:
                out.append(("grid", [head, part_grid(strs, j, j + n - 1)], j + n, _sc(n, 1.3, 2.0)))
            out.append(("strings", [head, part_strings(strs, j, j + n - 1)], j + n, _sc(n, 1.0, 0.5)))
        if n >= 2 and j + n <= N and all(L.isnum[k] and L.width(k) == n for k in range(j, j + n)):
            out.append(("matrix", [head, part_matrix([L.nums(k) for k in range(j, j + n)], j, j + n - 1)], j + n,
                        _sc(n, 1.2, 0.3 + 0.3 * min(n, 5))))
    # --- dwie (lub więcej) liczby: n m ...
    if len(H) >= 2:
        n, m = H[0], H[1]
        if n >= 1 and m >= 1 and j + n <= N and all(L.isnum[k] and L.width(k) == m for k in range(j, j + n)):
            out.append(("matrix", [head, part_matrix([L.nums(k) for k in range(j, j + n)], j, j + n - 1)], j + n,
                        _sc(n, 1.2, 1.0 + (0.5 if m > 1 else 0))))
        if n >= 1 and m >= 1 and j + n <= N and all(_grid_row(L.toks[k]) and len(L.toks[k][0]) == m for k in range(j, j + n)):
            out.append(("grid", [head, part_grid([L.toks[k][0] for k in range(j, j + n)], j, j + n - 1)], j + n,
                        _sc(n, 1.3, 2.5)))
        if n >= 1 and m >= 1 and j + m <= N and all(L.isint[k] and L.width(k) in (2, 3) for k in range(j, j + m)):
            edges = [L.ints(k) for k in range(j, j + m)]
            if all(len(e) == len(edges[0]) for e in edges):
                base = _base_for(edges, n)
                if base is not None:
                    w = len(edges[0]) == 3
                    kind = "tree" if m == n - 1 and _is_tree(n, edges, base) else "graph"
                    bonus = 2.0 + (2.0 if (w and any(e[2] > n or e[2] < 0 for e in edges)) else 0.0)
                    bonus += 1.5 if kind == "tree" else 0.0
                    out.append(("graph", [head, part_graph(n, edges, j, j + m - 1, w, base, kind)], j + m,
                                _sc(m, 1.3, bonus)))
        if n >= 1 and j < N and L.isnum[j] and L.width(j) == n:
            arr = part_array(L.nums(j), j, j)
            if j + 1 < N and L.isnum[j + 1] and L.width(j + 1) == m:
                out.append(("arrays", [head, arr, part_array(L.nums(j + 1), j + 1, j + 1, "Tablica B")], j + 2,
                            _sc(2, 1.5, 3.0)))
            k = j + 1
            if m >= 1 and k + m <= N and all(L.isint[x] and 1 <= L.width(x) <= 4 for x in range(k, k + m)):
                items = [L.ints(x) for x in range(k, k + m)]
                widths = {len(x) for x in items}
                kind = "intervals" if widths == {2} and all(a <= b for a, b in items) else (
                    "queries" if len(widths) > 1 or widths == {3} else ("points" if widths == {2} else "table"))
                out.append(("array+q", [head, arr, part_intervals(items, k, k + m - 1, kind)], k + m,
                            _sc(1 + m, 1.25, 3.0)))
            out.append(("array", [head, arr], j + 1, _sc(1, 1.5, 1.8)))
        if n >= 1 and j + n <= N and all(L.isnum[k] and L.width(k) == 2 for k in range(j, j + n)):
            items = [L.nums(k) for k in range(j, j + n)]
            kind = "intervals" if all(a <= b for a, b in items) else "points"
            out.append((kind, [head, part_intervals(items, j, j + n - 1, kind)], j + n, _sc(n, 1.0, 0.3)))
        if len(H) >= 3:
            q = H[2]
            for name, parts, nxt, sc in list(out):
                if name in ("graph", "matrix", "grid") and q >= 1 and nxt + q <= N and \
                        all(L.isint[x] for x in range(nxt, nxt + q)):
                    items = [L.ints(x) for x in range(nxt, nxt + q)]
                    out.append((name + "+q", parts + [part_intervals(items, nxt, nxt + q - 1, "queries")], nxt + q,
                                sc + q * 1.1 + 1.5))
    return out


def _fallback(L: Lines, i: int) -> tuple[list[dict], int, float]:
    if L.isnum[i]:
        vals = L.nums(i)
        if len(vals) <= 4:
            return [part_values([f"x{k + 1}" for k in range(len(vals))], vals, i)], i + 1, -1.0
        return [part_array(vals, i, i, "Liczby")], i + 1, -0.8
    if len(L.toks[i]) == 0:
        return [], i + 1, -0.1
    return [part_strings([L.raw[i].strip()], i, i)], i + 1, -1.0


def _best_layout(L: Lines, start: int = 0) -> list:
    """DP od końca: best[i] = (wynik, części, nazwa) — najlepsze wyjaśnienie linii i..N-1."""
    N = L.n
    best: list = [None] * (N + 1)
    best[N] = (0.0, [], "")
    for i in range(N - 1, start - 1, -1):
        parts, nxt, sc = _fallback(L, i)
        cand = (sc + best[nxt][0], parts + best[nxt][1], "line")
        for name, tparts, tnext, tsc in _templates(L, i):
            if tnext <= N and best[tnext] is not None:
                tot = tsc + best[tnext][0]
                if tot > cand[0]:
                    cand = (tot, tparts + best[tnext][1], name)
        best[i] = cand
    return best


# ============================================================================================ multitest
def _multitest(L: Lines) -> dict | None:
    if L.n < 2 or not L.isint[0] or L.width(0) != 1:
        return None
    t = L.ints(0)[0]
    if not (1 <= t <= 10000) or t > L.n - 1:
        return None
    i = 1
    blocks = []
    sig = None
    for _ in range(t):
        if i >= L.n:
            return None
        opts = [o for o in _templates(L, i)]
        chosen = None
        if opts:
            opts.sort(key=lambda o: -o[3])
            if sig is not None:
                same = [o for o in opts if o[0] == sig]
                chosen = same[0] if same else None
            if chosen is None and sig is None:
                chosen = opts[0]
        if chosen is None:
            # blok jednoliniowy (np. "a b")
            if sig not in (None, "line"):
                return None
            parts, nxt, _ = _fallback(L, i)
            chosen = ("line", parts, nxt, 1.0)
        if sig is None:
            sig = chosen[0]
        elif chosen[0] != sig:
            return None
        blocks.append({"parts": chosen[1], "lines": [i, chosen[2] - 1]})
        i = chosen[2]
    if i != L.n:
        return None
    label = KIND_LABELS.get(sig.split("+")[0].replace("_v", ""), "blok") if sig != "line" else "linia"
    return {"kind": "multitest", "label": f"Wiele testów (t = {t})", "t": t, "block_kind": sig,
            "blocks": blocks[:200], "truncated": len(blocks) > 200, "lines": [0, L.n - 1],
            "summary": f"{t} testów, każdy: {label}"}


# ============================================================================================ kandydaci
def _describe(parts: list[dict]) -> str:
    bits = []
    for p in parts:
        k = p["kind"]
        if k == "values":
            bits.append(", ".join(f"{a}={b}" for a, b in zip(p["names"], p["values"])))
        elif k == "array":
            bits.append(f"tablica {p['n']} el. (min {p.get('min')}, max {p.get('max')})")
        elif k == "matrix":
            bits.append(f"macierz {p['n']}×{p['m']}")
        elif k == "grid":
            bits.append(f"siatka {p['n']}×{p['m']} ({len(p['legend'])} rodz. znaków)")
        elif k in ("graph", "tree"):
            extra = ", ważony" if p.get("weighted") else ""
            bits.append(f"{'drzewo' if k == 'tree' else 'graf'} n={p['n']}, m={p['m']}{extra}, indeksy od {p['base']}")
        elif k in ("intervals", "points", "queries", "table"):
            bits.append(f"{KIND_LABELS[k].lower()}: {p['q']}")
        elif k == "strings":
            bits.append(f"napisy: {p['q']}")
        elif k == "multitest":
            bits.append(p.get("summary", "multitest"))
        elif k == "text":
            bits.append("tekst")
    return "; ".join(bits)


def _main_kind(parts: list[dict]) -> str:
    order = ["multitest", "graph", "tree", "grid", "matrix", "intervals", "queries", "points", "table", "array",
             "strings", "values", "text"]
    kinds = [p["kind"] for p in parts]
    for k in order:
        if k in kinds:
            return k
    return "text"


def _confidence(score: float, nlines: int, parts: list[dict]) -> float:
    explained = sum(p["lines"][1] - p["lines"][0] + 1 for p in parts
                    if p["kind"] not in ("text",) and not (p["kind"] == "values" and p["names"][0].startswith("x"))
                    and not (p["kind"] == "strings" and p["q"] == 1 and p["lines"][0] == p["lines"][1]
                             and p.get("label") == "Napisy" and nlines > 1))
    cover = min(1.0, explained / max(1, nlines))
    strength = 1 - 2 ** (-max(0.0, score + 1.5) / 2.0)
    return round(max(0.05, min(0.99, 0.25 + 0.74 * cover * strength)), 3)


def detect(text: str, hint: str | None = None, output: str | None = None) -> dict:
    L = Lines(text or "")
    cands: list[dict] = []
    if L.n == 0:
        return {"candidates": [], "best": None, "stats": {"lines": 0, "tokens": 0}}
    best = _best_layout(L)
    seen_sig = set()

    def add(cid, parts, conf, label=None):
        sig = tuple((p["kind"], tuple(p["lines"])) for p in parts)
        if not parts:
            return
        if sig in seen_sig:
            for c in cands:
                if c["_sig"] == sig and conf > c["confidence"]:
                    c.update(id=cid, confidence=conf, label=label or c["label"])
            return
        seen_sig.add(sig)
        mk = _main_kind(parts)
        cands.append({"id": cid, "kind": mk, "label": label or KIND_LABELS.get(mk, mk), "confidence": conf,
                      "summary": _describe(parts), "parts": parts, "_sig": sig})

    # 1) najlepsze układy zaczynające się od każdego szablonu w linii 0 (alternatywy interpretacji)
    opts = _templates(L, 0)
    fb_parts, fb_next, fb_sc = _fallback(L, 0)
    layouts = [(fb_sc + best[fb_next][0], fb_parts + best[fb_next][1], "line")]
    for name, tparts, tnext, tsc in opts:
        layouts.append((tsc + best[tnext][0], tparts + best[tnext][1], name))
    layouts.sort(key=lambda x: -x[0])
    top_sc = max(layouts[0][0], 1e-9)
    top_conf = _confidence(layouts[0][0], L.n, layouts[0][1])
    for k, (sc, parts, name) in enumerate(layouts[:8]):
        rel = max(0.0, sc) / top_sc if top_sc > 0 else 0.0
        conf = top_conf if k == 0 else min(_confidence(sc, L.n, parts), round(top_conf * rel ** 1.5, 3))
        add(f"layout{k}", parts, conf)
    # 2) multitest
    mt = _multitest(L)
    if mt:
        bk = mt["block_kind"]
        conf = 0.985 if bk != "line" and mt["t"] >= 2 else (0.9 if bk != "line" else 0.45)
        add("multitest", [part_values(["t"], [mt["t"]], 0), mt], conf, mt["label"])
    # 3) interpretacje całościowe
    all_num = all(L.isnum[i] or not L.toks[i] for i in range(L.n))
    flat = [_num(t) for tk in L.toks for t in tk] if all_num else []
    if all_num and flat:
        if len(flat) == 1:
            add("number", [part_values(["x"], flat, 0)], 0.9, "Liczba")
        else:
            add("numbers", [part_array(flat, 0, L.n - 1, "Wszystkie liczby")], 0.3, "Lista liczb")
        widths = {L.width(i) for i in range(L.n) if L.toks[i]}
        if L.n >= 2 and len(widths) == 1 and min(widths) >= 2:
            add("matrix_nohdr", [part_matrix([L.nums(i) for i in range(L.n)], 0, L.n - 1)], 0.55, "Macierz (bez nagłówka)")
    rows = [tk[0] for tk in L.toks if tk]
    if L.n >= 2 and all(_grid_row(tk) for tk in L.toks if tk) and len({len(r) for r in rows}) == 1:
        add("grid_nohdr", [part_grid(rows, 0, L.n - 1)], 0.8, "Siatka (bez nagłówka)")
    if not all_num:
        add("strings_all", [part_strings([ln.strip() for ln in L.raw], 0, L.n - 1)], 0.25, "Linie tekstu")
    add("text", [part_text(L.raw, 0, L.n - 1)], 0.05, "Surowy tekst")
    # podpowiedź użytkownika: podnieś kandydatów danego rodzaju
    if hint and hint != "auto":
        for c in cands:
            if c["kind"] == hint or any(p["kind"] == hint for p in c["parts"]):
                c["_boost"] = 1
                c["confidence"] = round(min(0.99, 0.6 + 0.4 * c["confidence"]), 3)
    cands.sort(key=lambda c: (-c.get("_boost", 0), -c["confidence"]))
    for c in cands:
        c.pop("_boost", None)
    for c in cands:
        c.pop("_sig", None)
    res = {"candidates": cands[:12], "best": cands[0]["id"] if cands else None,
           "stats": {"lines": L.n, "tokens": sum(len(t) for t in L.toks), "truncated": L.truncated}}
    if output is not None and output.strip():
        res["output_overlay"] = {c["id"]: overlay(c, output) for c in cands[:12]}
        res["output_parsed"] = parse_output(output)
    return res


# ============================================================================================ wyjście
def parse_output(text: str) -> dict:
    L = Lines(text)
    toks = [t for tk in L.toks for t in tk]
    ints = all(_INT.match(t) for t in toks) and bool(toks)
    yesno = all(t.upper() in ("TAK", "NIE", "YES", "NO", "NIEMOZLIWE", "NIEMOŻLIWE", "IMPOSSIBLE") for t in toks) and bool(toks)
    return {"lines": L.n, "tokens": len(toks), "all_int": ints, "yes_no": yesno,
            "values": [_num(t) if _NUM.match(t) else t for t in toks[:5000]],
            "line_values": [[_num(t) if _NUM.match(t) else t for t in tk] for tk in L.toks[:5000]]}


def overlay(cand: dict, output: str) -> dict | None:
    """Jak nałożyć wyjście na wejście: ścieżka na grafie, etykiety wierzchołków, odpowiedzi na zapytania…"""
    po = parse_output(output)
    vals = po["values"]
    lines = po["line_values"]
    if not vals:
        return None
    parts = cand.get("parts") or []
    gp = next((p for p in parts if p["kind"] in ("graph", "tree")), None)
    qp = next((p for p in parts if p["kind"] in ("intervals", "queries", "points", "table")), None)
    ap = next((p for p in parts if p["kind"] == "array"), None)
    mt = next((p for p in parts if p["kind"] == "multitest"), None)
    if mt and len(lines) == mt["t"]:
        return {"kind": "per_test", "values": lines, "label": "Odpowiedź dla każdego testu"}
    if qp and len(lines) == qp["q"]:
        return {"kind": "answers", "values": [ln[0] if len(ln) == 1 else ln for ln in lines],
                "label": "Odpowiedzi na zapytania"}
    if gp and po["all_int"]:
        n, base = gp["n"], gp["base"]
        ints = [int(v) for v in vals]
        edges = {(min(e[0], e[1]), max(e[0], e[1])) for e in gp["edges"]}
        directed_edges = {(e[0], e[1]) for e in gp["edges"]}

        def is_path(seq):
            return len(seq) >= 2 and all(base <= v < base + n for v in seq) and all(
                (min(a, b), max(a, b)) in edges or (a, b) in directed_edges for a, b in zip(seq, seq[1:]))
        for seq in (ints, ints[1:] if len(ints) > 1 and ints[0] == len(ints) - 1 else None,
                    ints[2:] if len(ints) > 2 and ints[1] == len(ints) - 2 else None):
            if seq and is_path(seq):
                return {"kind": "path", "vertices": seq, "label": f"Ścieżka z wyjścia ({len(seq)} wierzchołków)"}
        if len(ints) == n:
            return {"kind": "node_labels", "values": ints, "label": "Wartości wierzchołków z wyjścia"}
        if len(ints) == n - 1 and base == 1:
            return {"kind": "node_labels", "values": [None] + ints, "offset": 2, "label": "Wartości wierzchołków 2..n"}
        if all(base <= v < base + n for v in ints) and len(set(ints)) == len(ints) and len(ints) >= 2:
            return {"kind": "node_set", "vertices": ints, "label": "Wierzchołki z wyjścia"}
    if ap and len(vals) == ap["n"]:
        return {"kind": "array", "values": vals, "label": "Tablica z wyjścia (porównaj z wejściem)"}
    if ap and po["all_int"] and all(0 <= int(v) <= ap["n"] for v in vals) and len(vals) <= ap["n"]:
        return {"kind": "indices", "values": [int(v) for v in vals], "label": "Indeksy w tablicy (z wyjścia)"}
    if len(vals) == 1:
        return {"kind": "value", "values": vals, "label": "Wynik"}
    return {"kind": "list", "values": vals[:2000], "label": "Wyjście"}


# ============================================================================================ HTTP
def handle_detect(body: dict) -> dict:
    text = body.get("text")
    if text is None:
        text = body.get("input") or ""
    if not isinstance(text, str):
        raise ApiError("Pole `text` musi być napisem.")
    if len(text) > MAX_TEXT:
        raise ApiError("Za duże wejście (limit 4 MB) — wklej mniejszy test.")
    if not text.strip():
        raise ApiError("Wklej wejście testu, żeby je zinterpretować.")
    return detect(text, body.get("hint"), body.get("output"))


ROUTES = {
    "POST /api/inspector/detect": handle_detect,
}

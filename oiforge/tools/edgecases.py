"""(l) Testy brzegowe: z wnioskowanego formatu wejścia + ograniczeń generuje krótkie, podchwytliwe testy
(konkretne wejścia!) z wyjaśnieniem, co łapią, oraz generator C++ testu maksymalnego / małych testów losowych.

Opcjonalnie uruchamia kod ucznia (i brute) na tych testach: POST /api/edgecases {statement, code?, brute?}."""
from __future__ import annotations

import random
import re

from ..core import runner
from ..core import statement as ST
from ..core import text as T
from ..server_api import ApiError

MAX_SHOW = 40          # maks. liczba elementów w teście „krótkim”
DEFAULT_HI = 10**9


# ============================================================================================ model wejścia
class Model:
    """Opis wejścia przydatny do generowania: zakresy zmiennych, relacje, alfabety z przykładów."""

    def __init__(self, parsed: dict):
        self.p = parsed
        self.fmt = parsed.get("input_format") or []
        self.cons = parsed.get("constraints") or {}
        self.sb = ST.size_bounds(self.cons)
        self.rel = parsed.get("relations") or []
        sec = parsed.get("sections") or {}
        self.low = T.strip_accents(" ".join(sec.get(k, "") for k in ("tresc", "wejscie", "uwagi"))).lower()
        self.sizes = set(ST.size_variables(self.fmt, self.cons))
        for it in self.fmt:
            for key in ("length", "repeat"):
                v = it.get(key)
                if v and re.fullmatch(r"[A-Za-z]\w*", str(v)):
                    self.sizes.add(str(v))
        self.samples = [s for s in parsed.get("samples") or [] if s.get("input")]
        self.neq = any(r["type"] == "neq" for r in self.rel) or bool(re.search(r"\ba ?!?≠ ?b\b|rozn\w+ (wierzcholk|miast)",
                                                                                 self.low))
        self.multi_ok = bool(re.search(r"moze (istniec|byc) (\w+ )?(wiele|kilka) (drog|krawedzi|polaczen)|wielokrotn", self.low))
        self.no_multi = bool(re.search(r"(kazda|zadna) (para|krawedz)\w* (\w+ ){0,4}(co najwyzej raz|nie powtarza|"
                                       r"jednokrotnie)|krawedzie (sa )?(parami )?rozne|nie ma (dwoch|wielokrotnych)", self.low))
        self.tree = bool(re.search(r"\bdrzew(?!\w* przedzial)", self.low)) or any(
            str(it.get("repeat", "")).replace(" ", "") == "n-1" for it in self.fmt)
        self.connected = bool(re.search(r"graf (jest )?spojn|spojny graf|da sie (dojsc|dojechac) z kazdego", self.low)) \
            and not re.search(r"nie(koniecznie)? (jest )?spojn", self.low)
        self.distinct_vals = bool(re.search(r"parami rozn|wszystkie (liczby|wartosci|elementy) (sa )?rozn|rozne liczby",
                                            self.low))
        self.sorted_input = bool(re.search(r"posortowan\w* (rosnaco|niemalejaco)|w kolejnosci (rosnacej|niemalejacej)",
                                           self.low))
        self.permutation = bool(re.search(r"permutacj\w* (liczb )?(1|od 1)|jest permutacj", self.low))
        self.alphabet = self._alphabet()
        self.ties = bool(re.search(r"remis|kilka (\w+ )?(rownych|takich samych|najlepszych)|jesli (jest )?(kilka|wiele)",
                                   self.low))

    # --- zakresy
    def bounds(self, var: str, default_lo: int = 1, default_hi: int = DEFAULT_HI) -> tuple:
        b = ST.base_var(var)
        c = self.sb.get(b) or self.cons.get(var) or {}
        if not c and b not in self.sizes:
            c = self.cons.get("_values") or {}
        lo, hi = c.get("min"), c.get("max")
        if hi is None:
            for r in self.rel:
                if r["type"] == "le" and ST.base_var(r["a"]) == b:
                    hi = ("var", ST.base_var(r["b"]))
                    break
        if lo is None:
            lo = 0 if (b in ("m", "q", "k") and False) else default_lo
        if hi is None:
            hi = default_hi
        return lo, hi

    def resolve(self, v, env, fallback):
        if isinstance(v, tuple) and v[0] == "var":
            return env.get(v[1], fallback)
        return v

    def _alphabet(self) -> dict:
        """Znaki występujące w napisach/planszach z przykładów (np. 'ox', '.#PW')."""
        out = {}
        for idx, it in enumerate(self.fmt):
            if it.get("kind") not in ("string", "grid", "lines"):
                continue
            chars = set()
            for s in self.samples:
                ok = ST.check_format(self.fmt, s["input"])
                if not ok["ok"]:
                    continue
                lines = [ln for ln in s["input"].split("\n") if ln.strip()]
                for ln in lines:
                    toks = ln.split()
                    if len(toks) == 1 and not re.fullmatch(r"-?\d+", toks[0]):
                        chars.update(toks[0])
            if not chars:
                if re.search(r"mal\w+ liter", self.low):
                    chars = set("abcdefghijklmnopqrstuvwxyz")
                elif re.search(r"duz\w+ liter|wielk\w+ liter", self.low):
                    chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                elif re.search(r"zer\w* i jedyn|binarn|0 i 1", self.low):
                    chars = set("01")
                else:
                    chars = set("abc")
            out[idx] = "".join(sorted(chars))
        return out

    def size_max(self, var: str, default: int = 10**5) -> int:
        lo, hi = self.bounds(var, 1, default)
        return hi if isinstance(hi, int) else default


# ============================================================================================ renderowanie testu
def _render(model: Model, sc: dict) -> str | None:
    """Buduje wejście dla scenariusza sc (słownik parametrów). Zwraca tekst albo None, gdy się nie da."""
    rnd = random.Random(sc.get("seed", 1))
    fmt = model.fmt
    if not fmt:
        return None
    per_test = [x for x in fmt if x.get("per_test")]
    top = [x for x in fmt if not x.get("per_test")]
    out_lines: list[str] = []
    if top and top[0]["kind"] == "multitest":
        t = sc.get("tests", 1)
        out_lines.append(str(t))
        for i in range(t):
            sub = dict(sc)
            sub["seed"] = sc.get("seed", 1) * 31 + i
            if "sub_scenarios" in sc:
                sub.update(sc["sub_scenarios"][i % len(sc["sub_scenarios"])])
            body = _render_items(model, per_test, sub, random.Random(sub["seed"]))
            if body is None:
                return None
            out_lines += body
        return "\n".join(out_lines) + "\n"
    body = _render_items(model, top, sc, rnd)
    if body is None:
        return None
    return "\n".join(body) + "\n"


def _plan_sizes(model: Model, items: list[dict], sc: dict, rnd) -> dict:
    env: dict = {}
    n_target = sc.get("n")
    for it in items:
        if it["kind"] != "scalars":
            continue
        for v in it.get("vars") or []:
            b = ST.base_var(v)
            lo, hi = model.bounds(v, 1, DEFAULT_HI)
            lo_r = model.resolve(lo, env, 1)
            hi_r = model.resolve(hi, env, 10)
            if not isinstance(lo_r, (int, float)):
                lo_r = 1
            if not isinstance(hi_r, (int, float)):
                hi_r = max(lo_r, 10)
            lo_r, hi_r = int(lo_r), int(hi_r)
            if b in model.sizes:
                if b in sc.get("sizes", {}):
                    val = sc["sizes"][b]
                elif n_target == "min":
                    val = lo_r
                elif isinstance(n_target, int):
                    val = n_target
                else:
                    val = min(hi_r, 5)
                val = max(lo_r, min(hi_r, val))
            else:
                mode = sc.get("values", "small")
                if b in sc.get("sizes", {}):
                    val = sc["sizes"][b]
                elif mode == "max":
                    val = hi_r
                elif mode == "min":
                    val = lo_r
                else:
                    val = rnd.randint(lo_r, max(lo_r, min(hi_r, lo_r + 9)))
                val = max(lo_r, min(hi_r, val))
            env[b] = val
    return env


def _value(model: Model, var: str, env: dict, mode: str, rnd, i: int = 0, n: int = 1):
    lo, hi = model.bounds(var, 1, DEFAULT_HI)
    lo = model.resolve(lo, env, 1)
    hi = model.resolve(hi, env, 10)
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        lo, hi = 1, 10
    if hi < lo:
        hi = lo
    if mode == "max":
        return hi
    if mode == "min":
        return lo
    if mode == "zero":
        return 0 if lo <= 0 <= hi else lo
    if mode == "equal":
        return min(hi, max(lo, 7))
    if mode == "neg":
        return rnd.randint(lo, min(hi, -1)) if lo < 0 else lo
    if mode == "alt":
        return lo if i % 2 == 0 else hi
    if mode == "sorted":
        span = max(1, min(hi - lo, 10 * n))
        return lo + (i * span) // max(1, n - 1) if n > 1 else lo
    if mode == "reverse":
        span = max(1, min(hi - lo, 10 * n))
        return lo + ((n - 1 - i) * span) // max(1, n - 1) if n > 1 else lo
    small_hi = min(hi, lo + 9) if hi - lo > 9 else hi
    return rnd.randint(lo, max(lo, small_hi))


def _render_items(model: Model, items: list[dict], sc: dict, rnd) -> list[str] | None:
    env = _plan_sizes(model, items, sc, rnd)
    # krawędzie / wiersze planujemy przed wypisaniem pierwszej linii (m zależy od kształtu grafu)
    body: list[str] = []
    graph_rows = None
    for it in items:
        if it["kind"] == "rows" and len(it.get("vars") or []) in (2, 3) and not it.get("length"):
            rep = str(it.get("repeat") or "")
            a, b = it["vars"][0], it["vars"][1]
            lo_a, hi_a = model.bounds(a)
            linked = isinstance(hi_a, tuple) or ST.base_var(a) in ("a", "b", "u", "v", "x", "y") and "n" in env
            if linked:
                graph_rows = it
                nvar = hi_a[1] if isinstance(hi_a, tuple) else "n"
                n = env.get(nvar, 5)
                edges = _graph_edges(model, sc.get("graph", "random"), n, rnd, rep, env)
                if edges is None:
                    return None
                repv = re.sub(r"[-+].*$", "", rep.replace(" ", ""))
                if repv in env and rep.replace(" ", "") == repv:
                    # m = liczba krawędzi — sprawdź ograniczenia
                    lo_m, hi_m = model.bounds(repv, 0, 10**6)
                    hi_m = model.resolve(hi_m, env, 10**6)
                    lo_m = model.resolve(lo_m, env, 0)
                    if isinstance(lo_m, int) and len(edges) < lo_m:
                        return None
                    if isinstance(hi_m, int) and len(edges) > hi_m:
                        edges = edges[:hi_m]
                    env[repv] = len(edges)
                elif rep.replace(" ", "") == f"{nvar}-1" and len(edges) != n - 1:
                    return None
                it["_edges"] = edges
            break
    for it in items:
        k = it["kind"]
        vs = it.get("vars") or []
        if k == "scalars":
            body.append(" ".join(str(env.get(ST.base_var(v), _value(model, v, env, "small", rnd))) for v in vs))
        elif k == "array":
            L = ST._eval_len(it.get("length"), env)
            if L is None:
                L = sc.get("n") if isinstance(sc.get("n"), int) else 5
            mode = sc.get("array", sc.get("values", "small"))
            v = vs[0] if vs else "a"
            body.append(" ".join(str(x) for x in _array(model, v, L, env, mode, rnd)))
        elif k == "string":
            idx = model.fmt.index(it) if it in model.fmt else 0
            body.append(_string(model, it, idx, env, sc.get("string", "random"), rnd,
                                n=sc.get("n") if isinstance(sc.get("n"), int) else (1 if sc.get("n") == "min" else None)))
        elif k in ("rows", "lines", "grid", "queries"):
            rep = it.get("repeat")
            R = ST._eval_len(rep, env)
            if it is graph_rows and "_edges" in it:
                edges = it.pop("_edges")
                w = len(vs)
                for e in edges:
                    if w == 3:
                        wv = _value(model, vs[2], env, sc.get("weights", "small"), rnd)
                        body.append(f"{e[0]} {e[1]} {wv}")
                    else:
                        body.append(f"{e[0]} {e[1]}")
                continue
            if R is None:
                if str(rep or "").startswith("until:"):
                    R = 2
                elif k == "queries" and model.samples:
                    return None
                else:
                    return None
            if k == "rows":
                L = ST._eval_len(it.get("length"), env) if it.get("length") else None
                mode = sc.get("array", sc.get("values", "small"))
                for r in range(R):
                    if L is not None:
                        body.append(" ".join(str(x) for x in _array(model, vs[0] if vs else "a", L, env, mode, rnd)))
                    else:
                        body.append(" ".join(str(_value(model, v, env, mode, rnd, r, R)) for v in vs) if vs else "1")
            elif k in ("grid", "lines"):
                idx = model.fmt.index(it) if it in model.fmt else 0
                L = ST._eval_len(it.get("length"), env) or env.get("m") or 3
                gmode = sc.get("grid", "random")
                if k == "grid" and gmode != "nospecial":
                    rows = grid_full(model, idx, R, L, {"open": "free", "single": "free", "one": "free"}.get(gmode, gmode),
                                     rnd)
                    if rows is None:
                        return None
                    body += rows
                else:
                    for r in range(R):
                        body.append(_grid_row(model, idx, L, r, R, gmode, rnd))
            else:
                ql = _sample_queries(model)
                if not ql:
                    return None
                for r in range(R):
                    body.append(_query_line(ql, env, rnd, sc.get("queries", "random"), r))
            if str(rep or "").startswith("until:"):
                body.append(str(rep).split(":", 1)[1])
        elif k == "multitest":
            continue
    return body


def _array(model: Model, var: str, L: int, env: dict, mode: str, rnd) -> list:
    if model.permutation:
        arr = list(range(1, L + 1))
        if mode == "reverse":
            arr.reverse()
        elif mode not in ("sorted",):
            rnd.shuffle(arr)
        return arr
    vals = [_value(model, var, env, mode if mode != "random" else "small", rnd, i, L) for i in range(L)]
    if mode == "sorted" or model.sorted_input:
        vals.sort()
    elif mode == "reverse":
        vals.sort(reverse=True)
    if model.distinct_vals and len(set(vals)) < len(vals):
        lo, hi = model.bounds(var)
        lo = model.resolve(lo, env, 1)
        base = int(lo) if isinstance(lo, int) else 1
        vals = [base + i for i in range(L)]
        if mode == "reverse":
            vals.reverse()
        elif mode not in ("sorted",) and not model.sorted_input:
            rnd.shuffle(vals)
    return vals


def _graph_edges(model: Model, shape: str, n: int, rnd, rep: str, env: dict) -> list | None:
    rep = rep.replace(" ", "")
    tree_like = model.tree or rep.endswith("-1")
    if shape == "path":
        e = [(i, i + 1) for i in range(1, n)]
    elif shape == "star":
        e = [(1, i) for i in range(2, n + 1)]
    elif shape == "empty":
        if tree_like:
            return None
        e = []
    elif shape == "disconnected":
        if tree_like or model.connected or n < 4:
            return None
        h = n // 2
        e = [(i, i + 1) for i in range(1, h)] + [(i, i + 1) for i in range(h + 1, n)]
    elif shape == "loop":
        if model.neq or tree_like:
            return None
        e = [(1, 2), (2, 2), (2, 3)] if n >= 3 else [(1, 1)]
    elif shape == "multi":
        if tree_like or model.no_multi:
            return None
        e = [(1, 2), (1, 2), (2, 1)] + ([(2, 3)] if n >= 3 else [])
    elif shape == "cycle":
        if tree_like or n < 3:
            return None
        e = [(i, i % n + 1) for i in range(1, n + 1)]
    else:
        if tree_like:
            e = [(rnd.randint(1, i - 1), i) for i in range(2, n + 1)]
        else:
            e = [(rnd.randint(1, i - 1), i) for i in range(2, n + 1)]
            e += [(rnd.randint(1, n), rnd.randint(1, n)) for _ in range(2)]
            if model.neq:
                e = [x for x in e if x[0] != x[1]]
    return e


def _string(model: Model, it: dict, idx: int, env: dict, mode: str, rnd, n: int | None = None) -> str:
    alpha = model.alphabet.get(idx) or "abc"
    L = ST._eval_len(it.get("length"), env) if it.get("length") else None
    if L is None:
        L = {"one": 1, "same": 6, "palindrome": 7, "alt": 8}.get(mode, n or 6)
        lc = model.cons.get(f"|{(it.get('vars') or ['s'])[0]}|") or {}
        if isinstance(lc.get("max"), int):
            L = max(1, min(L, lc["max"]))
    if mode == "one" or L <= 1:
        return alpha[0]
    if mode == "same":
        return alpha[0] * L
    if mode == "palindrome":
        h = "".join(rnd.choice(alpha) for _ in range((L + 1) // 2))
        return h + h[::-1][L % 2:]
    if mode == "alt":
        a, b = alpha[0], alpha[-1]
        return "".join(a if i % 2 == 0 else b for i in range(L))
    return "".join(rnd.choice(alpha) for _ in range(L))


def grid_roles(model: Model, idx: int) -> dict:
    """Role znaków planszy z przykładów: wolne pole, ściana, znaki specjalne (start/meta — po jednym na planszę)."""
    alpha = model.alphabet.get(idx) or ".#"
    cnt: dict = {}
    once_everywhere = None
    present_everywhere = None
    for smp in model.samples:
        c = ST.check_format(model.fmt, smp["input"])
        if not c.get("ok") or idx not in c.get("spans", {}):
            continue
        a, b = c["spans"][idx]
        rows = [ln.strip() for ln in smp["input"].split("\n") if ln.strip()][a:b]
        local: dict = {}
        for row in rows:
            for ch in row:
                local[ch] = local.get(ch, 0) + 1
                cnt[ch] = cnt.get(ch, 0) + 1
        once = {ch for ch, k in local.items() if k == 1 and ch not in ".#"}
        once_everywhere = once if once_everywhere is None else (once_everywhere & once)
        present = {ch for ch in local if ch.isalpha()}
        present_everywhere = present if present_everywhere is None else (present_everywhere & present)
    specials = sorted(once_everywhere or [], key=lambda ch: model.low.find(ch.lower()) if ch.isalpha() else 99)
    free = "." if "." in alpha else (max(cnt, key=cnt.get) if cnt else alpha[0])
    wall = next((ch for ch in "#XxO*@" if ch in alpha and ch != free and ch not in specials), None)
    if wall is None:
        others = sorted((k, ch) for ch, k in cnt.items() if ch not in specials and ch != free)
        wall = others[0][1] if others else ("#" if free != "#" else "X")
    corners = bool(re.search(r"lew\w* gorn|praw\w* doln|\brogu\b|narozn", model.low))
    # litery obecne na każdej planszy z przykładów (np. „P” = płonące drzewo) — muszą wystąpić choć raz
    required = sorted(ch for ch in (present_everywhere or set()) if ch not in specials and ch not in (free, wall))
    if wall in (present_everywhere or set()) and wall.isalpha() and not specials:
        required.append(wall)
    return {"free": free, "wall": wall, "specials": specials[:2], "corners": corners, "alpha": alpha,
            "required": required[:2]}


def grid_full(model: Model, idx: int, R: int, L: int, mode: str, rnd) -> list[str] | None:
    """Cała plansza R × L dla scenariusza brzegowego (start/meta zawsze dokładnie raz)."""
    ro = grid_roles(model, idx)
    free, wall, sp = ro["free"], ro["wall"], ro["specials"]
    if R < 1 or L < 1:
        return None
    g = [[free] * L for _ in range(R)]
    start, end = (0, 0), (R - 1, L - 1)
    if mode == "walled":                    # start otoczony ścianami — meta nieosiągalna
        if R * L < 4:
            return None
        g = [[free] * L for _ in range(R)]
        for (r, c) in ((0, 1), (1, 0), (1, 1)):
            if r < R and c < L:
                g[r][c] = wall
    elif mode == "all_walls":               # same ściany poza startem (i metą)
        g = [[wall] * L for _ in range(R)]
    elif mode == "snake":                   # wąski korytarz-wąż: najdłuższa możliwa ścieżka
        g = [[free] * L for _ in range(R)]
        for r in range(1, R, 2):
            g[r] = [wall] * L
            g[r][L - 1 if (r // 2) % 2 == 0 else 0] = free
    elif mode == "checker":                 # szachownica: 4 kierunki ≠ 8 kierunków
        g = [[(free if (r + c) % 2 else wall) for c in range(L)] for r in range(R)]
    elif mode == "random":
        g = [[(wall if rnd.random() < 0.3 else free) for _ in range(L)] for _ in range(R)]
    if sp:
        if len(sp) >= 2 and R * L < 2:
            return None
        g[start[0]][start[1]] = sp[0]
        if len(sp) >= 2:
            g[end[0]][end[1]] = sp[1]
    elif ro["corners"] and mode != "checker":
        g[start[0]][start[1]] = free
        g[end[0]][end[1]] = free
    for k, ch in enumerate(ro.get("required") or []):
        if not any(ch in row for row in g):
            pos = [start, end][k] if k < 2 else None
            if pos is None or (k == 1 and R * L < 2):
                return None
            g[pos[0]][pos[1]] = ch
    return ["".join(row) for row in g]


def _grid_row(model: Model, idx: int, L: int, r: int, R: int, mode: str, rnd) -> str:
    alpha = model.alphabet.get(idx) or ".#"
    common = [c for c in alpha if c in ".#ox01_-"] or list(alpha)
    free = "." if "." in alpha else common[0]
    special = [c for c in alpha if c not in ".#"]
    row = [free] * L if mode in ("free", "one") else [rnd.choice(common) for _ in range(L)]
    if special and mode != "nospecial":
        if r == 0:
            row[0] = special[0]
        if len(special) > 1 and r == R - 1:
            row[-1] = special[1]
    return "".join(row)


def _sample_queries(model: Model) -> list[list[str]]:
    """Kształty zapytań z przykładów: [['Q', int], ['S', int, int], ...] — tylko z wierszy zapytań."""
    shapes = []
    qidx = [i for i, it in enumerate(model.fmt) if it.get("kind") == "queries"]
    for s in model.samples:
        lines = [ln for ln in s["input"].split("\n") if ln.strip()]
        c = ST.check_format(model.fmt, s["input"])
        if qidx and qidx[0] in c.get("spans", {}):
            a, b = c["spans"][qidx[0]]
            if b <= a:
                b = len(lines)
            lines = lines[a:b]
        for ln in lines:
            toks = ln.split()
            if toks and not re.fullmatch(r"-?\d+", toks[0]):
                shp = [toks[0]] + ["#" if re.fullmatch(r"-?\d+", t) else t for t in toks[1:]]
                if shp not in shapes:
                    shapes.append(shp)
    return shapes


def _numeric_query_shapes(model: Model) -> list[list[str]]:
    """Zapytania zapisane samymi liczbami („1 l r”, „2 x”): kształt = pierwsza liczba (rodzaj) + liczba argumentów.
    Gdy pierwszych liczb jest dużo różnych, to nie są rodzaje — wtedy kształt to same argumenty („#”)."""
    qidx = [i for i, it in enumerate(model.fmt) if it.get("kind") == "queries"]
    rows: list[list[str]] = []
    for s in model.samples:
        lines = [ln for ln in s["input"].split("\n") if ln.strip()]
        c = ST.check_format(model.fmt, s["input"])
        if not (qidx and qidx[0] in c.get("spans", {})):
            continue
        a, b = c["spans"][qidx[0]]
        if b <= a:
            b = len(lines)
        rows += [ln.split() for ln in lines[a:b] if ln.split()]
    if not rows or not all(re.fullmatch(r"-?\d+", t) for r in rows for t in r):
        return []
    firsts = {r[0] for r in rows}
    shapes: list[list[str]] = []
    for r in rows:
        shp = ([r[0]] if len(firsts) <= 4 else ["#"]) + ["#"] * (len(r) - 1)
        if shp not in shapes:
            shapes.append(shp)
    return shapes[:6]


def _shape_generator(model: Model, small: bool) -> list[str]:
    """Generator dla nierozpoznanego formatu: powiela kształt pierwszego przykładu (te same wiersze i liczba
    wartości), liczby losuje z zakresów widzianych na tej samej pozycji w przykładach, słowa bierze z przykładów."""
    samples = [[ln.split() for ln in s["input"].split("\n") if ln.strip()] for s in model.samples]
    samples = [x for x in samples if x]
    if not samples:
        return ["// brak przykładów i nierozpoznany format — generator wypisuje jedną liczbę z [1, 10]",
                "cout << R(1, 10) << \"\\n\";"]
    base = samples[0]
    same = [x for x in samples if len(x) == len(base) and all(len(a) == len(b) for a, b in zip(x, base))]
    out = ["// format nierozpoznany z treści — generator powiela kształt pierwszego przykładu"
           f" (liczba wierszy: {len(base)}), losując liczby z zakresów widzianych w przykładach"]
    for li, row in enumerate(base):
        parts = []
        for j, tok in enumerate(row):
            col = [x[li][j] for x in same]
            if all(re.fullmatch(r"-?\d{1,17}", t) for t in col):
                vals = [int(t) for t in col]
                lo, hi = min(vals), max(vals)
                if lo == hi:            # jeden przykład: lekko poszerz zakres wokół widzianej wartości
                    lo, hi = (lo if lo <= 1 else max(1, lo // 2)), hi + max(1, abs(hi) // 2)
                if small:
                    hi = min(hi, lo + 10)
                parts.append(f"R({lo}, {hi})")
            elif len(set(col)) == 1:
                parts.append(f"\"{_cpp_str(tok)}\"")
            else:
                opts = sorted(set(col))
                arr = "{" + ", ".join(f"\"{_cpp_str(t)}\"" for t in opts) + "}"
                parts.append(f"vector<string>{arr}[R(0, {len(opts) - 1})]")
        out.append("cout << " + " << ' ' << ".join(parts) + " << \"\\n\";")
    return out


def _query_line(shapes, env, rnd, mode, r) -> str:
    shp = shapes[r % len(shapes)] if mode == "cycle" else rnd.choice(shapes)
    n = env.get("n") or 5
    nums = [rnd.randint(1, max(1, n)) for _ in shp[1:] if _ == "#"]
    if len(nums) == 2:
        nums.sort()
    it = iter(nums)
    return " ".join([shp[0]] + [str(next(it)) if t == "#" else t for t in shp[1:]])


# ============================================================================================ bardzo duże liczby
BIG_TEST = 100_000          # test dłuższy niż tyle znaków jest „duży” — UI pokazuje tylko podgląd
MAX_BIG_DIGITS = 2_000_001  # dłuższych liczb nie wypisujemy jako testów (od tego jest generator)


def bignum_vars(model: Model) -> list[tuple[str, dict]]:
    """Zmienne skalarne opisane jako „liczba do 10^K” (K > 18) — w programie to napisy cyfr."""
    out = []
    for it in model.fmt:
        if it.get("kind") != "scalars":
            continue
        for v in it.get("vars") or []:
            c = model.cons.get(v) or model.sb.get(ST.base_var(v)) or {}
            if c.get("bignum") and c.get("max_digits"):
                out.append((v, c))
    return out


def _big_max(c: dict) -> str | None:
    if c.get("max_nines"):
        return "9" * c["max_nines"] if c["max_nines"] <= MAX_BIG_DIGITS else None
    lead, k = c.get("max_lead"), c.get("max_pow10")
    if isinstance(lead, str) and isinstance(k, int) and len(lead) + k <= MAX_BIG_DIGITS:
        return lead + "0" * k
    if isinstance(c.get("max"), int):
        return str(c["max"])
    return None


def _big_ok(x: str, c: dict) -> bool:
    """Czy napis cyfr jest poprawną wartością zmiennej (bez zer wiodących, w zakresie)?"""
    if not x.isdigit() or (len(x) > 1 and x[0] == "0"):
        return False
    lo = c.get("min")
    if isinstance(lo, int) and lo > 0 and x == "0":
        return False
    D = c.get("max_digits") or 0
    if len(x) > D:
        return False
    top = _big_max(c)
    return not (top is not None and len(x) == len(top) and x > top)


def _bignum_tests(model: Model, limit: int) -> list[dict]:
    """Testy brzegowe dla wejścia z „liczbą jako napisem”: 1, 9, 10, 10^k, 9·10^k, same dziewiątki, cyfry 0/1,
    2^64, maksymalna długość… Każdy test jest poprawnym wejściem (bez zer wiodących, w zakresie z treści)."""
    bigs = bignum_vars(model)
    v0, c0 = bigs[0]
    D = int(c0["max_digits"])
    top = _big_max(c0)
    lo = 0 if c0.get("min") == 0 else 1
    rnd = random.Random(11)
    k = max(1, min(6, D - 2))
    mid = max(2, min(25, D - 1))
    bin_len = max(2, min(30, D - 1))
    cands = [
        (str(lo), f"Najmniejsza wartość ({v0} = {lo})",
         "Najmniejsze możliwe wejście — sprawdza przypadek „już gotowe / 0 ruchów” i pętle, które nic nie robią.", ["min"]),
        ("9", f"Jedna cyfra: {v0} = 9", "Największa liczba jednocyfrowa — kolejne +1 zmienia długość zapisu (9 → 10).",
         ["min"]),
        ("10", f"{v0} = 10", "Najmniejsza liczba dwucyfrowa: zero na końcu, przeniesienie przy odejmowaniu.", ["min"]),
        ("1" + "0" * k, f"Potęga dziesiątki: 10^{k}", "Jedynka i same zera — przy obrotach/usuwaniu cyfr znikają zera "
         "wiodące, łatwo pomylić długość liczby.", ["zero"]),
        ("9" + "0" * k, f"9·10^{k}: dziewiątka i same zera", "Wiodąca dziewiątka i zera — przeniesienie tuż przed "
         "zmianą długości liczby.", ["zero"]),
        ("9" * mid, f"Same dziewiątki ({mid} cyfr)", "Dodanie 1 przenosi się przez wszystkie cyfry i wydłuża liczbę; "
         "takiej liczby nie zmieścisz w long long.", ["overflow"]),
        ("5" + "0" * (mid - 1), f"Jedna cyfra i same zera po niej ({mid} cyfr)",
         "Same zera po pierwszej cyfrze — sprawdza obsługę zer wewnątrz i na końcu zapisu.", ["zero"]),
        ("1" + "".join(rnd.choice("01") for _ in range(bin_len - 1)), f"Tylko cyfry 0 i 1 ({bin_len} cyfr)",
         "Zapis złożony wyłącznie z zer i jedynek (częste podzadanie) — dużo zer w środku.", ["equal"]),
        ("18446744073709551616", "2^64 — tuż za zakresem unsigned long long",
         "20 cyfr: wczytanie do long long / unsigned long long się przepełni — liczbę trzeba trzymać jako napis.",
         ["overflow"]),
        (rnd.choice("123456789") + "".join(rnd.choice("0123456789") for _ in range(min(999, D - 1))),
         f"Losowa długa liczba ({min(1000, D)} cyfr)", "Średnio długie wejście — sprawdza, czy rozwiązanie działa na "
         "cyfrach (napis), a nie na typie liczbowym.", ["max"]),
    ]
    if top is not None:
        cands.append((top, f"Maksymalna wartość ({ST.fmt_big(c0)}, {len(top)} cyfr)",
                      "Największe dozwolone wejście — maksymalna długość zapisu; sprawdza czas działania i pamięć "
                      "(wczytuj jednym `cin >> s`, nie znak po znaku z `endl`).", ["max"]))
    if D - 1 >= 2 and D - 1 <= MAX_BIG_DIGITS:
        cands.append(("9" * (D - 1), f"Same dziewiątki, maksymalna długość ({D - 1} cyfr)",
                      "Najdłuższa liczba z samych dziewiątek — +1 przenosi się przez całą liczbę.", ["max", "overflow"]))
    others = [(it, v) for it in model.fmt for v in (it.get("vars") or []) if v != v0]
    out: list[dict] = []
    seen = set()
    for val, title, why, tags in cands:
        if val in seen or not _big_ok(val, c0):
            continue
        seen.add(val)
        rows = []
        for it in model.fmt:
            vals = []
            for v in it.get("vars") or []:
                if v == v0:
                    vals.append(val)
                    continue
                c = model.cons.get(v) or model.sb.get(ST.base_var(v)) or {}
                if c.get("bignum"):
                    vals.append("1")
                    continue
                a, b = model.bounds(v)
                a = a if isinstance(a, int) else 1
                b = b if isinstance(b, int) else a + 10
                vals.append(str(max(a, min(b, 3))))
            rows.append(" ".join(vals))
        inp = "\n".join(rows) + "\n"
        t = {"id": f"t{len(out) + 1}", "title": title, "why": why, "input": inp, "tags": ["bignum"] + tags}
        if len(inp) > BIG_TEST:
            t.update(big=True, input_size=len(inp), input_preview=inp[:60] + f"… ({len(inp) - 1} znaków)")
        out.append(t)
        if len(out) >= limit:
            break
    if others and out:
        out[0]["why"] += " Pozostałe liczby mają małe wartości."
    return out


# ============================================================================================ scenariusze
def edge_tests(parsed: dict, limit: int = 18) -> list[dict]:
    model = Model(parsed)
    fmt = model.fmt
    if not fmt:
        return []
    if bignum_vars(model) and all(it.get("kind") == "scalars" and not it.get("per_test") for it in fmt):
        return _bignum_tests(model, limit)
    kinds = {it["kind"] for it in fmt}
    has_graph = any(it["kind"] == "rows" and len(it.get("vars") or []) in (2, 3) and not it.get("length") for it in fmt)
    has_array = "array" in kinds or any(it["kind"] == "rows" and (it.get("length") or len(it.get("vars") or []) == 1)
                                        for it in fmt)
    has_string = "string" in kinds
    sb = model.sb
    neg_ok = any((v.get("min") is not None and v["min"] < 0) for v in model.cons.values())
    zero_ok = any(v.get("min") == 0 for k, v in model.cons.items() if ST.base_var(k) not in model.sizes)
    big_vals = [v.get("max") for k, v in model.cons.items() if isinstance(v.get("max"), int)
                and ST.base_var(k) not in model.sizes]
    vmax = max(big_vals) if big_vals else None
    nmain, nmax, _ = ST.main_size(fmt, model.cons)
    scenarios: list[tuple[dict, str, str, list]] = []

    def add(sc, title, why, tags):
        scenarios.append((sc, title, why, tags))

    nmin = None
    if nmain:
        lo, _hi = model.bounds(nmain)
        nmin = lo if isinstance(lo, int) else 1
    add({"n": "min", "values": "min", "graph": "path", "string": "one", "grid": "one", "tests": 1},
        f"Minimalny rozmiar ({nmain} = {nmin})" if nmain else "Minimalne dane",
        "Najmniejsze możliwe wejście — łapie błędy „o jeden”, puste pętle, dzielenie przez zero i założenie, "
        "że są co najmniej 2 elementy.", ["min"])
    if nmain and nmin is not None and nmin <= 1:
        add({"n": 2, "values": "small", "graph": "path", "string": "random", "tests": 1},
            f"{nmain} = 2", "Dwa elementy: najmniejszy przypadek, w którym jest jakaś relacja między elementami.", ["min"])
    if vmax and vmax >= 10**6:
        add({"n": 4, "values": "max", "array": "max", "weights": "max", "graph": "path", "tests": 1},
            "Wartości maksymalne", f"Kilka elementów, każdy równy {ST.fmt_value(vmax)} — suma/iloczyn przekracza int. "
            "Łapie przepełnienie (brak long long).", ["overflow"])
    if has_array:
        add({"n": 5, "array": "equal", "values": "small", "tests": 1}, "Wszystkie elementy równe",
            "Same jednakowe wartości — łapie złe warunki ostre/nieostre (< vs ≤), remisy i dzielenie przez różnicę.",
            ["equal", "ties"])
        if not model.sorted_input:
            add({"n": 6, "array": "sorted", "tests": 1}, "Ciąg posortowany rosnąco",
                "Dane już posortowane — najgorszy przypadek naiwnego quicksorta, łapie błędy w warunkach brzegowych "
                "pętli i wyszukiwania.", ["sorted"])
            add({"n": 6, "array": "reverse", "tests": 1}, "Ciąg posortowany malejąco",
                "Odwrotna kolejność — maksymalna liczba inwersji, najgorszy przypadek wielu zachłannych podejść.",
                ["sorted"])
        if zero_ok:
            add({"n": 5, "array": "zero", "values": "zero", "tests": 1}, "Same zera",
                "Zera bywają traktowane jak „brak wartości” (np. inicjalizacja maksimum zerem, dzielenie, iloczyn).",
                ["zero"])
        if neg_ok:
            add({"n": 5, "array": "neg", "values": "neg", "tests": 1}, "Liczby ujemne",
                "Wszystkie wartości ujemne — łapie maksimum zainicjalizowane zerem i modulo z ujemnych liczb.",
                ["negative"])
            add({"n": 6, "array": "alt", "tests": 1}, "Naprzemiennie minimum i maksimum",
                "Skrajne wartości obok siebie — największe możliwe różnice, łapie przepełnienie przy odejmowaniu.",
                ["overflow", "negative"])
    if has_graph:
        add({"n": 6, "graph": "path", "tests": 1}, "Graf-ścieżka 1–2–…–n",
            "Najdłuższa możliwa ścieżka: największa głębokość rekurencji i największe odległości.", ["graph", "path"])
        add({"n": 6, "graph": "star", "tests": 1}, "Gwiazda (wszyscy połączeni z 1)",
            "Jeden wierzchołek o ogromnym stopniu — łapie rozwiązania kwadratowe względem stopnia.", ["graph", "star"])
        add({"n": 6, "graph": "disconnected", "tests": 1}, "Graf niespójny",
            "Dwie osobne części — część wierzchołków jest nieosiągalna (−1 / „nieskończoność”).", ["graph"])
        add({"n": 3, "graph": "empty", "tests": 1}, "Brak krawędzi (m = 0)",
            "Pusty zbiór krawędzi — pętle po krawędziach nic nie robią, wszystko jest osobno.", ["graph", "zero"])
        add({"n": 4, "graph": "loop", "tests": 1}, "Pętla własna (a = b)",
            "Krawędź z wierzchołka do niego samego — dozwolona, bo treść tego nie zabrania; łapie podwójne liczenie.",
            ["graph"])
        add({"n": 4, "graph": "multi", "tests": 1}, "Krawędzie wielokrotne",
            "Ta sama para połączona kilka razy — łapie macierz sąsiedztwa zamiast listy i złe liczenie stopni.", ["graph"])
        add({"n": 5, "graph": "cycle", "tests": 1}, "Cykl",
            "Graf z cyklem — łapie nieskończone pętle w DFS bez tablicy odwiedzonych.", ["graph"])
    if has_string:
        add({"string": "one", "n": "min", "tests": 1}, "Napis jednoznakowy",
            "Napis długości 1 — łapie dostęp do s[1] i pętle od i = 1.", ["string", "min"])
        add({"string": "same", "n": 4, "tests": 1}, "Wszystkie znaki jednakowe",
            "Same takie same litery — każdy fragment jest palindromem, wiele równych wystąpień.", ["string", "equal"])
        add({"string": "alt", "n": 4, "tests": 1}, "Znaki naprzemienne",
            "Naprzemienne litery (abab…) — łapie błędy w porównaniach sąsiadów i okresach.", ["string"])
        add({"string": "palindrome", "n": 4, "tests": 1}, "Palindrom", "Napis symetryczny.", ["string"])
    if "grid" in kinds:
        gi = next(i for i, it in enumerate(fmt) if it["kind"] == "grid")
        git = fmt[gi]
        ro = grid_roles(model, gi)
        dims = [str(git.get("repeat") or ""), str(git.get("length") or "")]
        dims = [d for d in dims if re.fullmatch(r"[A-Za-z]\w*", d)]

        def gsz(r, c):
            out = {}
            if dims:
                out[dims[0]] = r
                if len(dims) > 1 and dims[1] != dims[0]:
                    out[dims[1]] = c
            return out
        two = len(ro["specials"]) >= 2
        add({"sizes": gsz(1, 2 if two else 1), "grid": "single", "tests": 1},
            "Plansza 1×2: start tuż obok mety" if two else "Plansza 1×1 (start = meta)",
            "Najmniejsza możliwa plansza — wynik 0 albo 1 ruch; łapie BFS, który nie sprawdza celu przy starcie, "
            "i pętle po sąsiadach wychodzące poza planszę.", ["grid", "min"])
        path_task = bool(ro["specials"] or ro["corners"])
        if path_task:
            add({"sizes": gsz(3, 3), "grid": "walled", "tests": 1}, "Start otoczony ścianami — brak drogi",
                "Z pola startowego nie da się nigdzie wyjść, więc cel jest nieosiągalny: poprawny wynik to „brak "
                "drogi” (np. −1). Łapie programy, które wypisują 0 albo nieskończoność.", ["grid", "unreachable"])
        add({"sizes": gsz(3, 4), "grid": "all_walls", "tests": 1},
            "Cała plansza to ściany (poza startem/metą)" if path_task else f"Cała plansza ze znaków „{ro['wall']}”",
            "Same przeszkody — BFS kończy się od razu, nic nie jest osiągalne." if path_task else
            "Jednolita plansza — jeden wielki obszar (albo żadnego); łapie liczniki zainicjalizowane źle i "
            "rekurencję zbyt głęboką dla DFS.", ["grid", "zero"])
        add({"sizes": gsz(5, 5), "grid": "snake", "tests": 1}, "Wąski korytarz (wąż)",
            "Jedna kręta ścieżka przez całą planszę — najdłuższa możliwa odległość; łapie DFS zamiast BFS i błędy "
            "przy kierunkach ruchu.", ["grid", "path"])
        add({"sizes": gsz(4, 4), "grid": "checker", "tests": 1}, "Szachownica",
            "Pola stykające się tylko rogami — przy 4 kierunkach są osobno, przy 8 łączą się. Łapie złą liczbę "
            "kierunków.", ["grid"])
        add({"sizes": gsz(4, 5), "grid": "open", "tests": 1}, "Plansza bez przeszkód",
            "Wszystkie pola wolne — maksymalnie dużo stanów w kolejce BFS.", ["grid"])
    if "multitest" in kinds:
        add({"tests": 3, "n": 3, "sub_scenarios": [{"n": "min", "values": "min"}, {"n": 5, "values": "max"},
                                                   {"n": 2, "array": "equal"}]},
            "Kilka zestawów danych z rzędu",
            "Różne zestawy w jednym wejściu — łapie brak czyszczenia tablic/zmiennych między testami.", ["multitest"])
    if model.ties and has_array:
        add({"n": 4, "array": "equal", "tests": 1}, "Remis", "Kilka równie dobrych kandydatów — sprawdź, który wybierasz.",
            ["ties"])
    if nmax and nmax <= 20 and has_array:
        add({"n": nmax, "values": "small", "tests": 1}, f"Maksymalne n = {nmax} (małe)",
            "Przy małym limicie możesz dać pełny test maksymalny ręcznie.", ["max"])
    out = []
    seen = set()
    for sc, title, why, tags in scenarios:
        try:
            txt = _render(model, dict(sc, seed=len(out) + 7))
        except Exception:  # noqa: BLE001
            txt = None
        if not txt or txt in seen:
            continue
        chk = ST.check_format(model.fmt, txt)
        if not chk["ok"] and chk["reason"] not in ("nieznana liczba powtórzeń",):
            continue
        if sum(len(ln.split()) for ln in txt.split("\n")) > MAX_SHOW * 4:
            continue
        seen.add(txt)
        if title.startswith("Minimalny rozmiar") or re.fullmatch(r"\w+ = 2", title):
            env = {k: v for k, v in chk.get("env", {}).items() if "#" not in k and k in model.sizes}
            if env:
                desc = ", ".join(f"{k} = {v}" for k, v in env.items())
                title = ("Minimalny rozmiar" if title.startswith("Minimalny") else "Mały rozmiar") + f" ({desc})"
        out.append({"id": f"t{len(out) + 1}", "title": title, "why": why, "input": txt, "tags": tags})
        if len(out) >= limit:
            break
    # zapytania: przykład z jednym zapytaniem
    q_items = [it for it in fmt if it["kind"] == "queries"]
    if q_items and model.samples:
        one = _sample_with_one_query(model, q_items[0])
        if one and one not in seen:
            out.append({"id": f"t{len(out) + 1}", "title": "Tylko jedno zapytanie",
                        "why": "Przykład skrócony do jednego zapytania — łapie błędy inicjalizacji przed pierwszym "
                               "zapytaniem.", "input": one, "tags": ["queries", "min"]})
    return out


def _sample_with_one_query(model: Model, qit: dict) -> str | None:
    s = model.samples[0]["input"]
    lines = [ln for ln in s.split("\n") if ln.strip()]
    rep = str(qit.get("repeat") or "")
    if not re.fullmatch(r"[A-Za-z]", rep):
        return None
    first = model.fmt[0]
    if first["kind"] != "scalars" or rep not in [ST.base_var(v) for v in first["vars"]]:
        return None
    pos = [ST.base_var(v) for v in first["vars"]].index(rep)
    chk = ST.check_format(model.fmt, s)
    if not chk["ok"]:
        return None
    toks = lines[0].split()
    q = int(toks[pos]) if re.fullmatch(r"\d+", toks[pos]) else None
    if not q:
        return None
    head = len(lines) - q
    if head < 1:
        return None
    # zachowaj pierwsze zapytanie, które ma wynik (jeśli są różne typy, weź pierwsze)
    toks[pos] = "1"
    return "\n".join([" ".join(toks)] + lines[1:head] + [lines[head]]) + "\n"


# ============================================================================================ generator C++
def generator_cpp(parsed: dict, mode: str = "max") -> str:
    """Program C++ wypisujący test: mode='max' (maksymalny) albo 'small' (mały losowy, do testerki).
    Ziarno losowości z argv[1] (zgodnie z testerką)."""
    model = Model(parsed)
    fmt = model.fmt
    L: list[str] = []
    w = L.append
    w("#include <bits/stdc++.h>")
    w("using namespace std;")
    w("// Generator testów wygenerowany przez OI Forge na podstawie treści zadania.")
    w("// Użycie: ./gen <ziarno>  — wypisuje test na standardowe wyjście.")
    w("mt19937_64 rng;")
    w("long long R(long long a, long long b) { if (b < a) b = a; return uniform_int_distribution<long long>(a, b)(rng); }")
    w("")
    w("int main(int argc, char** argv) {")
    w("    rng.seed(argc > 1 ? atoll(argv[1]) : 12345);")
    if not fmt:
        for ln in _shape_generator(model, mode == "small"):
            w("    " + ln)
        w("    return 0;")
        w("}")
        return "\n".join(L) + "\n"
    declared: set[str] = set()
    small = mode == "small"

    def lim(var, default_hi):
        lo, hi = model.bounds(var, 1, default_hi)
        lo_s = _cpp_bound(lo, declared, "1")
        hi_s = _cpp_bound(hi, declared, str(default_hi))
        return lo_s, hi_s

    def size_expr(var):
        lo, hi = lim(var, 100000)
        if small:
            return f"R({lo}, min<long long>({hi}, 8))"
        return f"{hi}"

    per_test = [x for x in fmt if x.get("per_test")]
    top = [x for x in fmt if not x.get("per_test")]
    ind = "    "
    if top and top[0]["kind"] == "multitest":
        tv = ST.cpp_name(top[0]["vars"][0] if top[0]["vars"] else "t")
        lo, hi = lim(top[0]["vars"][0] if top[0]["vars"] else "t", 10)
        w(f"    long long {tv} = {('R(1, min<long long>(' + hi + ', 5))') if small else '1'};"
          f"  // liczba zestawów (dla testu max: 1 duży zestaw)")
        w(f"    cout << {tv} << \"\\n\";")
        w(f"    for (long long tc = 0; tc < {tv}; tc++) {{")
        ind = "        "
        items = per_test
    else:
        items = top
    graph = None
    for it in items:
        if it["kind"] == "rows" and len(it.get("vars") or []) in (2, 3) and not it.get("length"):
            graph = it
            break
    for it in items:
        k = it["kind"]
        vs = it.get("vars") or []
        if k == "scalars":
            names = []
            for v in vs:
                nm = ST.cpp_name(v)
                b = ST.base_var(v)
                cb = model.cons.get(v) or model.sb.get(b) or {}
                if cb.get("bignum") and cb.get("max_digits"):
                    D = int(cb["max_digits"])
                    Lx = "R(1, 6)" if small else str(max(1, D - 1))
                    w(f"{ind}// {v} ≤ {ST.fmt_big(cb)}: liczba do {D} cyfr — generujemy ją jako napis "
                      f"({'krótka' if small else f'{max(1, D - 1)} losowych cyfr, czyli zawsze < 10^{D - 1}'})")
                    w(f"{ind}string {nm}; {{ long long L = {Lx}; {nm}.assign(L, '0'); {nm}[0] = '1' + R(0, 8); "
                      f"for (long long i = 1; i < L; i++) {nm}[i] = '0' + R(0, 9); }}")
                    declared.add(nm)
                    names.append(nm)
                    continue
                if b in model.sizes:
                    if graph is not None and str(graph.get("repeat", "")).replace(" ", "") == b:
                        # liczba krawędzi
                        lo, hi = lim(v, 100000)
                        if model.tree:
                            expr = None
                        elif small:
                            expr = f"R({lo}, min<long long>({hi}, 10))"
                        else:
                            expr = hi
                    else:
                        expr = size_expr(v)
                else:
                    lo, hi = lim(v, 10**9)
                    expr = f"R({lo}, {'min<long long>(' + hi + ', 10)' if small else hi})"
                if expr is None:
                    rep_n = [x for x in vs if ST.base_var(x) != b]
                    expr = f"{ST.cpp_name(rep_n[0]) if rep_n else 'n'} - 1"
                w(f"{ind}long long {nm} = {expr};")
                declared.add(nm)
                names.append(nm)
            if names:
                w(f"{ind}cout << " + " << ' ' << ".join(names) + " << \"\\n\";")
        elif k == "array":
            v = ST.cpp_name(vs[0] if vs else "a")
            Lx = _cpp_len(it.get("length"), declared) or ("8" if small else "100000")
            lo, hi = lim(vs[0] if vs else "a", 10**9)
            if small:
                hi = f"min<long long>({hi}, {lo} + 10)"
            if model.permutation:
                w(f"{ind}{{ vector<long long> p({Lx}); iota(p.begin(), p.end(), 1); shuffle(p.begin(), p.end(), rng);")
                w(f"{ind}  for (long long i = 0; i < {Lx}; i++) cout << p[i] << \" \\n\"[i + 1 == {Lx}]; }}")
            else:
                sort_line = " sort(arr.begin(), arr.end());" if model.sorted_input else ""
                w(f"{ind}{{ vector<long long> arr({Lx}); for (auto &x : arr) x = R({lo}, {hi});{sort_line}")
                w(f"{ind}  for (long long i = 0; i < (long long)arr.size(); i++) cout << arr[i] << "
                  f"(i + 1 == (long long)arr.size() ? '\\n' : ' '); }}")
        elif k == "string":
            idx = fmt.index(it) if it in fmt else 0
            alpha = model.alphabet.get(idx) or "abc"
            Lx = _cpp_len(it.get("length"), declared)
            if not Lx:
                c = model.cons.get(f"|{vs[0]}|") if vs else None
                c = c or model.cons.get("_values") or {}
                hi = c.get("max") if isinstance(c.get("max"), int) and c.get("max") <= 10**7 else 100000
                Lx = ("R(1, 10)" if small else str(hi))
            w(f"{ind}{{ string al = \"{_cpp_str(alpha)}\"; long long L = {Lx}; string s(L, al[0]);")
            w(f"{ind}  for (auto &c : s) c = al[R(0, (long long)al.size() - 1)]; cout << s << \"\\n\"; }}")
        elif k in ("rows", "grid", "lines", "queries"):
            rep = it.get("repeat")
            Rx = _cpp_len(rep, declared)
            if it is graph:
                a_hi = model.bounds(vs[0])[1]
                nvar = a_hi[1] if isinstance(a_hi, tuple) else "n"
                nn = ST.cpp_name(nvar) if ST.cpp_name(nvar) in declared else (next(iter(declared)) if declared else "5")
                if model.tree or str(rep).replace(" ", "").endswith("-1"):
                    w(f"{ind}// drzewo: losowy ojciec dla każdego wierzchołka 2..n, potem przenumerowanie")
                    w(f"{ind}{{ vector<long long> perm({nn} + 1); iota(perm.begin(), perm.end(), 0); "
                      f"shuffle(perm.begin() + 1, perm.end(), rng);")
                    w(f"{ind}  for (long long i = 2; i <= {nn}; i++) {{ long long p = R(max(1LL, i - 10), i - 1); "
                      f"cout << perm[p] << ' ' << perm[i]" + (f" << ' ' << R({lim(vs[2], 10**9)[0]}, {lim(vs[2], 10**9)[1]})"
                                                             if len(vs) == 3 else "") + " << \"\\n\"; } }")
                else:
                    loop = "a == b" if model.neq else "false"
                    wexpr = ""
                    if len(vs) == 3:
                        lo3, hi3 = lim(vs[2], 10**9)
                        wexpr = f" << ' ' << R({lo3}, {('min<long long>(' + hi3 + ', 10)') if small else hi3})"
                    w(f"{ind}for (long long e = 0; e < {Rx or 'm'}; e++) {{")
                    w(f"{ind}    long long a = R(1, {nn}), b = R(1, {nn});")
                    w(f"{ind}    if ({loop}) {{ if ({nn} == 1) {{ e--; continue; }} b = a % {nn} + 1; }}")
                    w(f"{ind}    cout << a << ' ' << b{wexpr} << \"\\n\";")
                    w(f"{ind}}}")
                continue
            if Rx is None:
                Rx = "5" if small else "100000"
                w(f"{ind}// UWAGA: nie wiem, ile jest wierszy — przyjmuję {Rx}")
            if k == "rows":
                Lx = _cpp_len(it.get("length"), declared) if it.get("length") else None
                if Lx:
                    lo, hi = lim(vs[0] if vs else "a", 10**9)
                    w(f"{ind}for (long long r = 0; r < {Rx}; r++) for (long long c = 0; c < {Lx}; c++) "
                      f"cout << R({lo}, {('min<long long>(' + hi + ', 9)') if small else hi}) << (c + 1 == {Lx} ? '\\n' : ' ');")
                else:
                    parts = []
                    for v in vs or ["x"]:
                        lo, hi = lim(v, 10**9)
                        parts.append(f"R({lo}, {('min<long long>(' + hi + ', 10)') if small else hi})")
                    w(f"{ind}for (long long r = 0; r < {Rx}; r++) cout << " + " << ' ' << ".join(parts) + " << \"\\n\";")
            elif k in ("grid", "lines"):
                idx = fmt.index(it) if it in fmt else 0
                alpha = model.alphabet.get(idx) or ".#"
                Lx = _cpp_len(it.get("length"), declared) or ("m" if "m" in declared else ("4" if small else "1000"))
                if k == "grid":
                    ro = grid_roles(model, idx)
                    sp = ro["specials"]
                    wall_p = "0" if not small else "25"
                    w(f"{ind}{{ // plansza: wolne „{_cpp_str(ro['free'])}”, ściany „{_cpp_str(ro['wall'])}”"
                      + (f", start „{_cpp_str(sp[0])}” w lewym górnym rogu" if sp else "")
                      + (f", meta „{_cpp_str(sp[1])}” w prawym dolnym" if len(sp) > 1 else "")
                      + (" (test maksymalny: bez ścian — najwięcej pracy dla BFS)" if not small else ""))
                    w(f"{ind}  long long R_ = {Rx}, C_ = {Lx}; vector<string> g(R_, string(C_, '{_cpp_str(ro['free'])}'));")
                    w(f"{ind}  for (auto &row : g) for (auto &c : row) if (R(1, 100) <= {wall_p}) c = '{_cpp_str(ro['wall'])}';")
                    if sp:
                        w(f"{ind}  g[0][0] = '{_cpp_str(sp[0])}';")
                        if len(sp) > 1:
                            w(f"{ind}  if (R_ * C_ > 1) g[R_ - 1][C_ - 1] = '{_cpp_str(sp[1])}';")
                    elif ro["corners"]:
                        w(f"{ind}  g[0][0] = g[R_ - 1][C_ - 1] = '{_cpp_str(ro['free'])}';")
                    w(f"{ind}  for (auto &row : g) cout << row << \"\\n\"; }}")
                    if str(rep or "").startswith("until:"):
                        w(f"{ind}cout << \"{str(rep).split(':', 1)[1]}\\n\";")
                    continue
                w(f"{ind}{{ string al = \"{_cpp_str(alpha)}\";  // znaki z przykładów — popraw, jeśli np. start/meta "
                  f"muszą wystąpić dokładnie raz")
                w(f"{ind}  for (long long r = 0; r < {Rx}; r++) {{ string row({Lx}, al[0]); for (auto &c : row) "
                  f"c = al[R(0, (long long)al.size() - 1)]; cout << row << \"\\n\"; }} }}")
            else:
                shapes = _sample_queries(model)
                if not shapes:
                    raw = _numeric_query_shapes(model)
                    nn = "n" if "n" in declared else "10"
                    if raw:
                        w(f"{ind}// zapytania liczbowe podpatrzone w przykładach (pierwsza liczba = rodzaj): "
                          + ", ".join(" ".join(r) for r in raw))
                        w(f"{ind}for (long long r = 0; r < {Rx}; r++) {{")
                        w(f"{ind}    int typ = R(0, {len(raw) - 1});")
                        for si, shp in enumerate(raw):
                            parts = [f"\"{_cpp_str(t)}\"" if t != "#" else f"R(1, {nn})" for t in shp]
                            w(f"{ind}    if (typ == {si}) cout << " + " << ' ' << ".join(parts) + " << \"\\n\";")
                        w(f"{ind}}}")
                    else:
                        w(f"{ind}// kształtu zapytań nie widać w przykładach — każde zapytanie to jedna liczba z [1, {nn}]")
                        w(f"{ind}for (long long r = 0; r < {Rx}; r++) cout << R(1, {nn}) << \"\\n\";")
                else:
                    nn = "n" if "n" in declared else "10"
                    w(f"{ind}// typy zapytań podpatrzone w przykładach: " + ", ".join(" ".join(s) for s in shapes))
                    w(f"{ind}for (long long r = 0; r < {Rx}; r++) {{")
                    w(f"{ind}    int typ = R(0, {len(shapes) - 1});")
                    for si, shp in enumerate(shapes):
                        nums = sum(1 for t in shp[1:] if t == "#")
                        w(f"{ind}    if (typ == {si}) {{")
                        if nums == 2:
                            w(f"{ind}        long long x = R(1, {nn}), y = R(1, {nn}); if (x > y) swap(x, y);")
                            vals = iter(["x", "y"])
                        else:
                            w(f"{ind}        " + " ".join(f"long long v{j} = R(1, {nn});" for j in range(nums)))
                            vals = iter([f"v{j}" for j in range(nums)])
                        parts = [f"\"{_cpp_str(shp[0])}\""] + [next(vals) if t == "#" else f"\"{_cpp_str(t)}\""
                                                                for t in shp[1:]]
                        w(f"{ind}        cout << " + " << ' ' << ".join(parts) + " << \"\\n\";")
                        w(f"{ind}    }}")
                    w(f"{ind}}}")
            if str(rep or "").startswith("until:"):
                w(f"{ind}cout << \"{str(rep).split(':', 1)[1]}\\n\";")
    if top and top[0]["kind"] == "multitest":
        w("    }")
    w("    return 0;")
    w("}")
    return "\n".join(L) + "\n"


def _cpp_bound(v, declared: set, default: str) -> str:
    if isinstance(v, tuple) and v[0] == "var":
        nm = ST.cpp_name(v[1])
        return nm if nm in declared else default
    if isinstance(v, float):
        v = int(v)
    if isinstance(v, int):
        if abs(v) > 9 * 10**18:
            return "LLONG_MAX" if v > 0 else "LLONG_MIN"
        return f"{v}LL" if abs(v) > 2**31 - 1 else str(v)
    return default


def _cpp_len(expr, declared: set) -> str | None:
    if expr is None:
        return None
    e = str(expr).replace(" ", "")
    if re.fullmatch(r"\d+", e):
        return e
    m = re.fullmatch(r"([A-Za-z]\w*)([-+*]\d+)?", e)
    if m:
        nm = ST.cpp_name(m.group(1))
        if nm in declared:
            return f"({nm}{m.group(2) or ''})"
    return None


def _cpp_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\"", "\\\"")


# ============================================================================================ uruchamianie kodu
def run_on_tests(tests: list[dict], code: str, brute: str | None = None, timeout: float = 2.0) -> dict:
    prog = runner.prepare(code)
    res = {"compile": prog.compile.to_dict() if prog.compile else None, "ok": prog.ok, "results": []}
    bprog = runner.prepare(brute) if brute and brute.strip() else None
    if bprog is not None:
        res["brute_compile"] = bprog.compile.to_dict() if bprog.compile else None
    if not prog.ok:
        return res
    for t in tests:
        rr = runner.run(prog.cmd, t["input"], timeout=timeout)
        item = {"id": t["id"], "verdict": rr.verdict, "time": rr.time, "stdout": rr.stdout[:4000],
                "stderr": rr.stderr[:2000]}
        if bprog is not None and bprog.ok and len(t["input"]) <= BIG_TEST:     # brute na milionie cyfr nie zdąży
            br = runner.run(bprog.cmd, t["input"], timeout=timeout * 3)
            item["expected"] = br.stdout[:4000]
            if rr.verdict == "OK" and br.verdict == "OK":
                item["verdict"] = "OK" if runner.outputs_equal(rr.stdout, br.stdout) else "WA"
        res["results"].append(item)
    return res


# ============================================================================================ HTTP
def build(statement: str) -> dict:
    p = ST.parse(statement)
    tests = edge_tests(p)
    gen_max = generator_cpp(p, "max")
    gen_small = generator_cpp(p, "small")
    notes = []
    if not p.get("input_format"):
        notes.append("Nie rozpoznałem formatu wejścia — testy są przybliżone, a generator powiela kształt "
                     "pierwszego przykładu (sprawdź, czy to wystarcza).")
    elif any(it.get("kind") == "queries" for it in p.get("input_format") or []) and \
            not _sample_queries(Model(p)) and not _numeric_query_shapes(Model(p)):
        notes.append("Nie rozpoznałem kształtu zapytań — generator wypisuje zapytania jako pojedyncze liczby.")
    elif not p.get("format_check", {}).get("ok"):
        notes.append("Format wejścia nie zgadza się w 100% z przykładami — sprawdź testy przed użyciem.")
    if not p.get("constraints"):
        notes.append("Nie znalazłem ograniczeń — wartości skrajne mogą być nieprawdziwe.")
    return {"title": p.get("title"), "tests": tests, "generator_max": gen_max, "generator_small": gen_small,
            "input_format": p.get("input_format"), "constraints": p.get("constraints"),
            "samples": p.get("samples"), "notes": notes + (p.get("warnings") or [])}


def handle_edgecases(body: dict) -> dict:
    st = (body.get("statement") or body.get("text") or "").strip()
    if not st:
        raise ApiError("Wklej treść zadania (albo upuść PDF).")
    res = build(st)
    code = body.get("code") or ""
    if code.strip():
        res["run"] = run_on_tests(res["tests"] + [{"id": f"s{i + 1}", "input": s["input"]}
                                                  for i, s in enumerate(res["samples"] or [])],
                                  code, body.get("brute"), float(body.get("timeout") or 2.0))
    return res


def handle_check_generator(body: dict) -> dict:
    """Kompiluje generator i uruchamia go raz (podgląd początku testu)."""
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Brak kodu generatora.")
    prog = runner.prepare(code)
    if not prog.ok:
        return {"compile": prog.compile.to_dict() if prog.compile else None, "ok_run": False}
    rr = runner.run(prog.cmd, "", timeout=10.0, args=[str(body.get("seed") or 1)])
    return {"compile": prog.compile.to_dict(), "ok_run": rr.verdict == "OK", "verdict": rr.verdict,
            "preview": rr.stdout[:3000], "size": len(rr.stdout), "time": rr.time}


ROUTES = {
    "POST /api/edgecases": handle_edgecases,
    "POST /api/edgecases/generator": handle_check_generator,
}

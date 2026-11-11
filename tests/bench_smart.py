"""Zestaw sprawdzający „mądre” narzędzia do treści zadań (auto-solver, doradca).

Syntetyczne treści w `tests/data/statements/*.txt` (napisane ręcznie, w stylu OI/kółek) + niezależne rozwiązania
wzorcowe w Pythonie (`ref`) i generatory małych losowych testów (`gen`) — tutaj.

Uruchomienie pomiaru:  python3 -m tests.bench_smart [--only nazwa,nazwa] [--no-solver] [--json plik]
Mierzy:
  * auto-solver: status „solved” ORAZ zgodność najlepszego kodu z `ref` na 20 losowych małych testach,
  * doradca: czy wybrana zmienna rozmiaru i złożoność są sensowne (`size`, `cx`).
"""
from __future__ import annotations

import heapq
import json
import math
import os
import random
import sys
import time
from collections import deque
from functools import lru_cache
from pathlib import Path

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")
ROOT = Path(__file__).resolve().parent.parent
STATEMENTS_DIR = ROOT / "tests" / "data" / "statements"
MOD = 10**9 + 7


def _ints(inp: str) -> list[int]:
    return [int(x) for x in inp.split()]


def _lines(inp: str) -> list[str]:
    return [ln.rstrip("\r") for ln in inp.split("\n")]


# ============================================================================================ ciągi
def r_pary_suma(inp):
    t = _ints(inp)
    n, k, a = t[0], t[1], t[2:2 + t[0]]
    return str(sum(1 for i in range(n) for j in range(i + 1, n) if a[i] + a[j] == k))


def g_pary_suma(r):
    n, k = r.randint(1, 8), r.randint(2, 12)
    return f"{n} {k}\n" + " ".join(str(r.randint(1, 10)) for _ in range(n)) + "\n"


def r_wystapienia(inp):
    t = _ints(inp)
    n = t[0]
    a, x = t[1:1 + n], t[1 + n]
    return str(a.count(x))


def g_wystapienia(r):
    n = r.randint(1, 8)
    return f"{n}\n" + " ".join(str(r.randint(1, 5)) for _ in range(n)) + f"\n{r.randint(1, 6)}\n"


def r_nwd_wielu(inp):
    t = _ints(inp)
    g = 0
    for x in t[1:1 + t[0]]:
        g = math.gcd(g, x)
    return str(g)


def g_nwd_wielu(r):
    n = r.randint(1, 6)
    m = r.choice([1, 2, 3, 6, 12])
    return f"{n}\n" + " ".join(str(m * r.randint(1, 20)) for _ in range(n)) + "\n"


def r_kadane(inp):
    t = _ints(inp)
    a = t[1:1 + t[0]]
    return str(max(sum(a[i:j + 1]) for i in range(len(a)) for j in range(i, len(a))))


def g_kadane(r):
    n = r.randint(1, 8)
    return f"{n}\n" + " ".join(str(r.randint(-10, 10)) for _ in range(n)) + "\n"


def r_gasienica(inp):
    t = _ints(inp)
    n, s, a = t[0], t[1], t[2:2 + t[0]]
    best = 0
    for i in range(n):
        for j in range(i, n):
            if sum(a[i:j + 1]) <= s:
                best = max(best, j - i + 1)
    return str(best)


def g_gasienica(r):
    n = r.randint(1, 8)
    return f"{n} {r.randint(1, 20)}\n" + " ".join(str(r.randint(1, 9)) for _ in range(n)) + "\n"


def r_lis(inp):
    t = _ints(inp)
    a = t[1:1 + t[0]]
    d = []
    for i, x in enumerate(a):
        d.append(1 + max([d[j] for j in range(i) if a[j] < x] or [0]))
    return str(max(d))


def g_lis(r):
    n = r.randint(1, 8)
    return f"{n}\n" + " ".join(str(r.randint(1, 10)) for _ in range(n)) + "\n"


# ============================================================================================ zapytania
def r_sumy_przedzialow(inp):
    t = _ints(inp)
    n, q = t[0], t[1]
    a = t[2:2 + n]
    out = []
    pos = 2 + n
    for _ in range(q):
        l, rr = t[pos], t[pos + 1]
        pos += 2
        out.append(str(sum(a[l - 1:rr])))
    return "\n".join(out)


def g_sumy_przedzialow(r):
    n, q = r.randint(1, 7), r.randint(1, 5)
    s = f"{n} {q}\n" + " ".join(str(r.randint(-5, 9)) for _ in range(n)) + "\n"
    for _ in range(q):
        a, b = sorted((r.randint(1, n), r.randint(1, n)))
        s += f"{a} {b}\n"
    return s


def r_sumy_zero(inp):
    t = _ints(inp)
    n = t[0]
    a = t[1:1 + n]
    q = t[1 + n]
    pos = 2 + n
    out = []
    for _ in range(q):
        l, rr = t[pos], t[pos + 1]
        pos += 2
        out.append(str(sum(a[l:rr + 1])))
    return "\n".join(out)


def g_sumy_zero(r):
    n, q = r.randint(1, 7), r.randint(1, 5)
    s = f"{n}\n" + " ".join(str(r.randint(0, 9)) for _ in range(n)) + f"\n{q}\n"
    for _ in range(q):
        a, b = sorted((r.randint(0, n - 1), r.randint(0, n - 1)))
        s += f"{a} {b}\n"
    return s


def r_egzamin(inp):
    t = _ints(inp)
    n = t[0]
    a = t[1:1 + n]
    q = t[1 + n]
    return "\n".join(str(sum(1 for x in a if x >= y)) for y in t[2 + n:2 + n + q])


def g_egzamin(r):
    n, q = r.randint(1, 7), r.randint(1, 5)
    return (f"{n}\n" + " ".join(str(r.randint(0, 10)) for _ in range(n)) + f"\n{q}\n"
            + "\n".join(str(r.randint(0, 11)) for _ in range(q)) + "\n")


# ============================================================================================ binsearch po wyniku
def r_deski(inp):
    t = _ints(inp)
    n, k, a = t[0], t[1], t[2:2 + t[0]]
    best = 0
    for L in range(1, max(a) + 1):
        if sum(x // L for x in a) >= k:
            best = L
    return str(best)


def g_deski(r):
    n = r.randint(1, 5)
    return f"{n} {r.randint(1, 12)}\n" + " ".join(str(r.randint(1, 15)) for _ in range(n)) + "\n"


def r_drwal(inp):
    t = _ints(inp)
    n, m, h = t[0], t[1], t[2:2 + t[0]]
    best = 0
    for H in range(0, max(h) + 1):
        if sum(max(0, x - H) for x in h) >= m:
            best = H
    return str(best)


def g_drwal(r):
    n = r.randint(1, 6)
    h = [r.randint(1, 15) for _ in range(n)]
    return f"{n} {r.randint(1, sum(h))}\n" + " ".join(map(str, h)) + "\n"


def r_rozklad_pracy(inp):
    t = _ints(inp)
    n, k, a = t[0], t[1], t[2:2 + t[0]]

    @lru_cache(maxsize=None)
    def f(i, parts):
        if i == n:
            return 0
        if parts == 0:
            return 10**18
        best = 10**18
        s = 0
        for j in range(i, n):
            s += a[j]
            best = min(best, max(s, f(j + 1, parts - 1)))
        return best
    return str(f(0, k))


def g_rozklad_pracy(r):
    n = r.randint(1, 7)
    return f"{n} {r.randint(1, n)}\n" + " ".join(str(r.randint(1, 9)) for _ in range(n)) + "\n"


def r_maszyny(inp):
    t = _ints(inp)
    n, k, a = t[0], t[1], t[2:2 + t[0]]
    T = 0
    while sum(T // x for x in a) < k:
        T += 1
    return str(T)


def g_maszyny(r):
    n = r.randint(1, 5)
    return f"{n} {r.randint(1, 15)}\n" + " ".join(str(r.randint(1, 6)) for _ in range(n)) + "\n"


# ============================================================================================ grafy
def _edges(inp, w):
    t = _ints(inp)
    n, m = t[0], t[1]
    e = []
    for i in range(m):
        e.append(tuple(t[2 + i * w: 2 + (i + 1) * w]))
    return n, m, e


def r_kurier(inp):
    n, m, e = _edges(inp, 3)
    g = [[] for _ in range(n + 1)]
    for a, b, c in e:
        g[a].append((b, c))
        g[b].append((a, c))
    d = [None] * (n + 1)
    pq = [(0, 1)]
    while pq:
        dv, v = heapq.heappop(pq)
        if d[v] is not None:
            continue
        d[v] = dv
        for u, c in g[v]:
            if d[u] is None:
                heapq.heappush(pq, (dv + c, u))
    return str(d[n] if d[n] is not None else -1)


def _rand_graph(r, n, m, weighted=False, wmax=9, loops=False):
    out = []
    for _ in range(m):
        a, b = r.randint(1, n), r.randint(1, n)
        while not loops and a == b and n > 1:
            a, b = r.randint(1, n), r.randint(1, n)
        if not loops and a == b:
            continue
        out.append((a, b, r.randint(1, wmax)) if weighted else (a, b))
    return out


def g_kurier(r):
    n = r.randint(2, 6)
    e = _rand_graph(r, n, r.randint(0, 8), True)
    return f"{n} {len(e)}\n" + "".join(" ".join(map(str, x)) + "\n" for x in e)


def _dsu_count(n, e):
    p = list(range(n + 1))

    def f(x):
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x
    c = n
    for a, b in e:
        ra, rb = f(a), f(b)
        if ra != rb:
            p[ra] = rb
            c -= 1
    return c


def r_grupy_znajomych(inp):
    n, m, e = _edges(inp, 2)
    return str(_dsu_count(n, e))


def g_grupy_znajomych(r):
    n = r.randint(1, 8)
    e = _rand_graph(r, n, r.randint(0, 7))
    return f"{n} {len(e)}\n" + "".join(f"{a} {b}\n" for a, b in e)


def r_siec(inp):
    n, m, e = _edges(inp, 3)
    p = list(range(n + 1))

    def f(x):
        while p[x] != x:
            x = p[x]
        return x
    tot = 0
    for a, b, c in sorted(e, key=lambda x: x[2]):
        ra, rb = f(a), f(b)
        if ra != rb:
            p[ra] = rb
            tot += c
    return str(tot)


def g_siec(r):
    n = r.randint(2, 6)
    e = [(r.randint(1, i - 1), i, r.randint(1, 20)) for i in range(2, n + 1)]
    e += _rand_graph(r, n, r.randint(0, 5), True, 20)
    r.shuffle(e)
    return f"{n} {len(e)}\n" + "".join(" ".join(map(str, x)) + "\n" for x in e)


def r_kolejnosc(inp):
    n, m, e = _edges(inp, 2)
    g = [[] for _ in range(n + 1)]
    deg = [0] * (n + 1)
    for a, b in e:
        g[a].append(b)
        deg[b] += 1
    pq = [v for v in range(1, n + 1) if deg[v] == 0]
    heapq.heapify(pq)
    out = []
    while pq:
        v = heapq.heappop(pq)
        out.append(v)
        for u in g[v]:
            deg[u] -= 1
            if deg[u] == 0:
                heapq.heappush(pq, u)
    return " ".join(map(str, out)) if len(out) == n else "NIE"


def g_kolejnosc(r):
    n = r.randint(1, 6)
    perm = list(range(1, n + 1))
    r.shuffle(perm)
    e = []
    for _ in range(r.randint(0, 6)):
        i, j = r.randint(0, n - 1), r.randint(0, n - 1)
        if i == j:
            continue
        if r.random() < 0.85 and i > j:
            i, j = j, i
        e.append((perm[i], perm[j]))
    return f"{n} {len(e)}\n" + "".join(f"{a} {b}\n" for a, b in e)


def r_przesiadki(inp):
    t = _ints(inp)
    n, m, s, tt = t[:4]
    g = [[] for _ in range(n + 1)]
    for i in range(m):
        a, b = t[4 + 2 * i], t[5 + 2 * i]
        g[a].append(b)
        g[b].append(a)
    d = [-1] * (n + 1)
    d[s] = 0
    q = deque([s])
    while q:
        v = q.popleft()
        for u in g[v]:
            if d[u] < 0:
                d[u] = d[v] + 1
                q.append(u)
    return str(d[tt])


def g_przesiadki(r):
    n = r.randint(1, 7)
    e = _rand_graph(r, n, r.randint(0, 8))
    return f"{n} {len(e)} {r.randint(1, n)} {r.randint(1, n)}\n" + "".join(f"{a} {b}\n" for a, b in e)


def r_drzewo(inp):
    t = _ints(inp)
    n = t[0]
    g = [[] for _ in range(n + 1)]
    for i in range(n - 1):
        a, b = t[1 + 2 * i], t[2 + 2 * i]
        g[a].append(b)
        g[b].append(a)
    d = [-1] * (n + 1)
    d[1] = 0
    q = deque([1])
    while q:
        v = q.popleft()
        for u in g[v]:
            if d[u] < 0:
                d[u] = d[v] + 1
                q.append(u)
    return str(max(d[1:]))


def g_drzewo(r):
    n = r.randint(1, 8)
    e = [(r.randint(1, i - 1), i) for i in range(2, n + 1)]
    e = [(b, a) if r.random() < 0.5 else (a, b) for a, b in e]
    r.shuffle(e)
    return f"{n}\n" + "".join(f"{a} {b}\n" for a, b in e)


# ============================================================================================ plansze
def _read_grid(inp):
    ls = [x for x in _lines(inp) if x.strip()]
    n, m = map(int, ls[0].split())
    return n, m, [ls[1 + i].strip() for i in range(n)]


def _bfs_grid(g, n, m, starts, passable, dirs=4):
    dd = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if dirs == 8 else [])
    d = [[-1] * m for _ in range(n)]
    q = deque()
    for (a, b) in starts:
        d[a][b] = 0
        q.append((a, b))
    while q:
        a, b = q.popleft()
        for da, db in dd:
            x, y = a + da, b + db
            if 0 <= x < n and 0 <= y < m and d[x][y] < 0 and passable(g[x][y]):
                d[x][y] = d[a][b] + 1
                q.append((x, y))
    return d


def r_labirynt(inp):
    n, m, g = _read_grid(inp)
    d = _bfs_grid(g, n, m, [(0, 0)], lambda c: c == ".")
    return str(d[n - 1][m - 1])


def _rand_grid(r, n, m, p_wall=0.3, wall="#", free="."):
    return [[wall if r.random() < p_wall else free for _ in range(m)] for _ in range(n)]


def g_labirynt(r):
    n, m = r.randint(1, 5), r.randint(1, 5)
    g = _rand_grid(r, n, m)
    g[0][0] = g[n - 1][m - 1] = "."
    return f"{n} {m}\n" + "".join("".join(row) + "\n" for row in g)


def r_mysz(inp):
    n, m, g = _read_grid(inp)
    s = next((i, j) for i in range(n) for j in range(m) if g[i][j] == "M")
    t = next((i, j) for i in range(n) for j in range(m) if g[i][j] == "S")
    d = _bfs_grid(g, n, m, [s], lambda c: c != "X")
    v = d[t[0]][t[1]]
    return str(v) if v >= 0 else "NIE"


def g_mysz(r):
    while True:
        n, m = r.randint(1, 5), r.randint(1, 5)
        if n * m >= 2:
            break
    g = _rand_grid(r, n, m, 0.3, "X")
    cells = [(i, j) for i in range(n) for j in range(m)]
    a, b = r.sample(cells, 2)
    g[a[0]][a[1]] = "M"
    g[b[0]][b[1]] = "S"
    return f"{n} {m}\n" + "".join("".join(row) + "\n" for row in g)


def _components(g, n, m, ch, dirs):
    seen = [[False] * m for _ in range(n)]
    dd = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if dirs == 8 else [])
    c = 0
    for i in range(n):
        for j in range(m):
            if g[i][j] == ch and not seen[i][j]:
                c += 1
                st = [(i, j)]
                seen[i][j] = True
                while st:
                    a, b = st.pop()
                    for da, db in dd:
                        x, y = a + da, b + db
                        if 0 <= x < n and 0 <= y < m and not seen[x][y] and g[x][y] == ch:
                            seen[x][y] = True
                            st.append((x, y))
    return c


def r_wyspy(inp):
    n, m, g = _read_grid(inp)
    return str(_components(g, n, m, "#", 4))


def g_wyspy(r):
    n, m = r.randint(1, 6), r.randint(1, 6)
    g = _rand_grid(r, n, m, 0.45)
    return f"{n} {m}\n" + "".join("".join(row) + "\n" for row in g)


def r_plamy(inp):
    n, m, g = _read_grid(inp)
    return str(_components(g, n, m, "*", 8))


def g_plamy(r):
    n, m = r.randint(1, 6), r.randint(1, 6)
    g = _rand_grid(r, n, m, 0.4, "*")
    return f"{n} {m}\n" + "".join("".join(row) + "\n" for row in g)


def r_pozar(inp):
    n, m, g = _read_grid(inp)
    starts = [(i, j) for i in range(n) for j in range(m) if g[i][j] == "P"]
    d = _bfs_grid(g, n, m, starts, lambda c: c == "D")
    best = 0
    for i in range(n):
        for j in range(m):
            if g[i][j] == "D":
                if d[i][j] < 0:
                    return "-1"
                best = max(best, d[i][j])
    return str(best)


def g_pozar(r):
    n, m = r.randint(1, 5), r.randint(1, 5)
    g = [[r.choice("DDD..") for _ in range(m)] for _ in range(n)]
    for _ in range(r.randint(1, 2)):
        g[r.randint(0, n - 1)][r.randint(0, m - 1)] = "P"
    return f"{n} {m}\n" + "".join("".join(row) + "\n" for row in g)


# ============================================================================================ napisy
def r_palindrom(inp):
    s = inp.split()[0]
    return "TAK" if s == s[::-1] else "NIE"


def g_palindrom(r):
    L = r.randint(1, 7)
    h = "".join(r.choice("ab") for _ in range(L))
    return (h + h[::-1][L % 2:] if r.random() < 0.5 else h) + "\n"


def r_anagramy(inp):
    a, b = inp.split()[:2]
    return "TAK" if sorted(a) == sorted(b) else "NIE"


def g_anagramy(r):
    a = "".join(r.choice("abc") for _ in range(r.randint(1, 6)))
    if r.random() < 0.5:
        b = list(a)
        r.shuffle(b)
        b = "".join(b)
    else:
        b = "".join(r.choice("abc") for _ in range(r.randint(1, 6)))
    return f"{a}\n{b}\n"


def r_lcs(inp):
    a, b = inp.split()[:2]
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = d[i - 1][j - 1] + 1 if a[i - 1] == b[j - 1] else max(d[i - 1][j], d[i][j - 1])
    return str(d[-1][-1])


def g_lcs(r):
    return ("".join(r.choice("abc") for _ in range(r.randint(1, 7))) + "\n"
            + "".join(r.choice("abc") for _ in range(r.randint(1, 7))) + "\n")


def r_nawiasy(inp):
    s = inp.split()[0] if inp.split() else ""
    st = []
    pair = {")": "(", "]": "[", "}": "{"}
    for c in s:
        if c in "([{":
            st.append(c)
        elif not st or st.pop() != pair[c]:
            return "NIE"
    return "TAK" if not st else "NIE"


def g_nawiasy(r):
    if r.random() < 0.5:
        s = ""
        st = []
        for _ in range(r.randint(1, 8)):
            if st and r.random() < 0.5:
                s += {"(": ")", "[": "]", "{": "}"}[st.pop()]
            else:
                c = r.choice("([{")
                st.append(c)
                s += c
        while st:
            s += {"(": ")", "[": "]", "{": "}"}[st.pop()]
        return s + "\n"
    return "".join(r.choice("()[]{}") for _ in range(r.randint(1, 8))) + "\n"


def r_onp(inp):
    st = []
    for tok in inp.split():
        if tok in "+-*" and len(tok) == 1:
            b, a = st.pop(), st.pop()
            st.append(a + b if tok == "+" else a - b if tok == "-" else a * b)
        else:
            st.append(int(tok))
    return str(st[-1])


def g_onp(r):
    def build(d):
        if d == 0 or r.random() < 0.3:
            return [str(r.randint(0, 9))]
        return build(d - 1) + build(d - 1) + [r.choice("+-*")]
    return " ".join(build(3)) + "\n"


def r_ranking(inp):
    t = inp.split()
    n = int(t[0])
    rows = [(t[1 + 2 * i], int(t[2 + 2 * i])) for i in range(n)]
    rows.sort(key=lambda x: (-x[1], x[0]))
    return "\n".join(x[0] for x in rows)


def g_ranking(r):
    n = r.randint(1, 6)
    names = r.sample(["Ala", "Ola", "Ewa", "Jan", "Piotr", "Kasia", "Zenek", "Adam", "Basia"], n)
    return f"{n}\n" + "".join(f"{nm} {r.randint(0, 5)}\n" for nm in names)


# ============================================================================================ symulacja
def r_kolejka(inp):
    ls = [x.split() for x in _lines(inp) if x.strip()]
    q = int(ls[0][0])
    dq = deque()
    out = []
    for op in ls[1:1 + q]:
        if op[0] == "PRZYJDZ":
            dq.append(op[1])
        elif dq:
            out.append(dq.popleft())
        else:
            out.append("PUSTO")
    return "\n".join(out)


def g_kolejka(r):
    q = r.randint(1, 8)
    ops = [f"PRZYJDZ {r.randint(1, 50)}" if r.random() < 0.55 else "OBSLUZ" for _ in range(q)]
    return f"{q}\n" + "\n".join(ops) + "\n"


def r_stos(inp):
    ls = [x.split() for x in _lines(inp) if x.strip()]
    q = int(ls[0][0])
    st = []
    out = []
    for op in ls[1:1 + q]:
        if op[0] == "PUSH":
            st.append(op[1])
        elif op[0] == "POP":
            if st:
                st.pop()
        else:
            out.append(st[-1] if st else "PUSTY")
    return "\n".join(out)


def g_stos(r):
    q = r.randint(1, 9)
    ops = [r.choice([f"PUSH {r.randint(1, 30)}", "POP", "TOP", "TOP"]) for _ in range(q)]
    return f"{q}\n" + "\n".join(ops) + "\n"


# ============================================================================================ DP
def r_plecak(inp):
    t = _ints(inp)
    n, W = t[0], t[1]
    best = 0
    items = [(t[2 + 2 * i], t[3 + 2 * i]) for i in range(n)]
    for mask in range(1 << n):
        w = sum(items[i][0] for i in range(n) if mask >> i & 1)
        if w <= W:
            best = max(best, sum(items[i][1] for i in range(n) if mask >> i & 1))
    return str(best)


def g_plecak(r):
    n = r.randint(1, 6)
    return f"{n} {r.randint(1, 15)}\n" + "".join(f"{r.randint(1, 8)} {r.randint(1, 20)}\n" for _ in range(n))


def r_reszta(inp):
    t = _ints(inp)
    n, K, c = t[0], t[1], t[2:2 + t[0]]
    INF = 10**9
    d = [0] + [INF] * K
    for x in range(1, K + 1):
        for v in c:
            if v <= x and d[x - v] + 1 < d[x]:
                d[x] = d[x - v] + 1
    return str(d[K] if d[K] < INF else -1)


def g_reszta(r):
    n = r.randint(1, 4)
    return f"{n} {r.randint(1, 25)}\n" + " ".join(str(x) for x in r.sample(range(2, 12), n)) + "\n"


def r_schody(inp):
    n = _ints(inp)[0]
    a, b = 1, 1
    for _ in range(n - 1):
        a, b = b, (a + b) % MOD
    return str(b)


def g_schody(r):
    return f"{r.randint(1, 60)}\n"


# ============================================================================================ matematyka
def r_pierwsze_przedzial(inp):
    a, b = _ints(inp)[:2]
    return str(sum(1 for x in range(a, b + 1) if x >= 2 and all(x % d for d in range(2, int(x ** 0.5) + 1))))


def g_pierwsze_przedzial(r):
    a = r.randint(1, 60)
    return f"{a} {r.randint(a, 90)}\n"


def r_fibonacci(inp):
    n = _ints(inp)[0]

    def mul(A, B):
        return [[(A[0][0] * B[0][0] + A[0][1] * B[1][0]) % MOD, (A[0][0] * B[0][1] + A[0][1] * B[1][1]) % MOD],
                [(A[1][0] * B[0][0] + A[1][1] * B[1][0]) % MOD, (A[1][0] * B[0][1] + A[1][1] * B[1][1]) % MOD]]
    R, M = [[1, 0], [0, 1]], [[1, 1], [1, 0]]
    e = n
    while e:
        if e & 1:
            R = mul(R, M)
        M = mul(M, M)
        e >>= 1
    return str(R[0][1])


def g_fibonacci(r):
    return f"{r.choice([r.randint(0, 30), r.randint(0, 10**18)])}\n"


def gs_fibonacci(r):
    return f"{r.randint(0, 40)}\n"


def r_dzielniki(inp):
    n = _ints(inp)[0]
    c = 0
    d = 1
    while d * d <= n:
        if n % d == 0:
            c += 1 if d * d == n else 2
        d += 1
    return str(c)


def g_dzielniki(r):
    return f"{r.choice([r.randint(1, 100), r.randint(1, 10**6), 720720])}\n"


def gs_dzielniki(r):
    return f"{r.randint(1, 2000)}\n"


def r_potega(inp):
    a, b, m = _ints(inp)[:3]
    return str(pow(a, b, m))


def g_potega(r):
    return f"{r.randint(1, 10**18)} {r.randint(1, 10**18)} {r.randint(2, 10**9)}\n"


def gs_potega(r):
    return f"{r.randint(1, 10**9)} {r.randint(1, 60)} {r.randint(2, 10**9)}\n"


# ============================================================================================ rejestr
# size: zmienne rozmiaru, które doradca może uznać za główne; cx: akceptowalne złożoności (z COMPLEXITY_TABLE)
NLOGN = ["O(n log n)", "O(n)"]
TASKS: dict[str, dict] = {
    "pary_suma": dict(ref=r_pary_suma, gen=g_pary_suma, size={"n"}, cx=NLOGN, kind="ciągi"),
    "wystapienia": dict(ref=r_wystapienia, gen=g_wystapienia, size={"n"}, cx=NLOGN, kind="ciągi"),
    "nwd_wielu": dict(ref=r_nwd_wielu, gen=g_nwd_wielu, size={"n"}, cx=NLOGN, kind="matematyka"),
    "kadane": dict(ref=r_kadane, gen=g_kadane, size={"n"}, cx=NLOGN, kind="ciągi"),
    "gasienica": dict(ref=r_gasienica, gen=g_gasienica, size={"n"}, cx=NLOGN, kind="dwa wskaźniki"),
    "lis": dict(ref=r_lis, gen=g_lis, size={"n"}, cx=NLOGN, kind="DP"),
    "sumy_przedzialow": dict(ref=r_sumy_przedzialow, gen=g_sumy_przedzialow, size={"n", "q"}, cx=NLOGN,
                             kind="zapytania"),
    "sumy_zero": dict(ref=r_sumy_zero, gen=g_sumy_zero, size={"n", "q"}, cx=NLOGN, kind="zapytania"),
    "egzamin": dict(ref=r_egzamin, gen=g_egzamin, size={"n", "q"}, cx=NLOGN, kind="zapytania"),
    "deski": dict(ref=r_deski, gen=g_deski, size={"n"}, cx=NLOGN, kind="binsearch"),
    "drwal": dict(ref=r_drwal, gen=g_drwal, size={"n"}, cx=NLOGN, kind="binsearch"),
    "rozklad_pracy": dict(ref=r_rozklad_pracy, gen=g_rozklad_pracy, size={"n"}, cx=NLOGN, kind="binsearch"),
    "maszyny": dict(ref=r_maszyny, gen=g_maszyny, size={"n"}, cx=NLOGN, kind="binsearch"),
    "kurier": dict(ref=r_kurier, gen=g_kurier, size={"n", "m"}, cx=NLOGN, kind="grafy"),
    "grupy_znajomych": dict(ref=r_grupy_znajomych, gen=g_grupy_znajomych, size={"n", "m"}, cx=NLOGN, kind="grafy"),
    "siec": dict(ref=r_siec, gen=g_siec, size={"n", "m"}, cx=NLOGN, kind="grafy"),
    "kolejnosc": dict(ref=r_kolejnosc, gen=g_kolejnosc, size={"n", "m"}, cx=NLOGN, kind="grafy"),
    "przesiadki": dict(ref=r_przesiadki, gen=g_przesiadki, size={"n", "m"}, cx=NLOGN, kind="grafy"),
    "drzewo": dict(ref=r_drzewo, gen=g_drzewo, size={"n"}, cx=NLOGN, kind="grafy"),
    "labirynt": dict(ref=r_labirynt, gen=g_labirynt, size={"n", "m", "n·m"}, cx=NLOGN + ["O(n²)", "O(n·m)"], kind="siatki"),
    "mysz": dict(ref=r_mysz, gen=g_mysz, size={"n", "m", "n·m"}, cx=NLOGN + ["O(n²)", "O(n·m)"], kind="siatki"),
    "wyspy": dict(ref=r_wyspy, gen=g_wyspy, size={"n", "m", "n·m"}, cx=NLOGN + ["O(n²)", "O(n·m)"], kind="siatki"),
    "plamy": dict(ref=r_plamy, gen=g_plamy, size={"n", "m", "n·m"}, cx=NLOGN + ["O(n²)", "O(n·m)"], kind="siatki"),
    "pozar": dict(ref=r_pozar, gen=g_pozar, size={"n", "m", "n·m"}, cx=NLOGN + ["O(n²)", "O(n·m)"], kind="siatki"),
    "palindrom": dict(ref=r_palindrom, gen=g_palindrom, size={"|s|", "s", "n"}, cx=NLOGN, kind="napisy"),
    "anagramy": dict(ref=r_anagramy, gen=g_anagramy, size={"|s|", "s", "|a|", "a", "n"}, cx=NLOGN, kind="napisy"),
    "lcs": dict(ref=r_lcs, gen=g_lcs, size={"|s|", "s", "|a|", "a", "n", "|t|", "t"}, cx=["O(n²)"], kind="DP"),
    "nawiasy": dict(ref=r_nawiasy, gen=g_nawiasy, size={"|s|", "s", "n"}, cx=NLOGN, kind="napisy"),
    "onp": dict(ref=r_onp, gen=g_onp, size=None, cx=None, kind="symulacja"),
    "ranking": dict(ref=r_ranking, gen=g_ranking, size={"n"}, cx=NLOGN, kind="sortowanie"),
    "kolejka": dict(ref=r_kolejka, gen=g_kolejka, size={"q"}, cx=NLOGN, kind="symulacja"),
    "stos": dict(ref=r_stos, gen=g_stos, size={"q", "n"}, cx=NLOGN, kind="symulacja"),
    "plecak": dict(ref=r_plecak, gen=g_plecak, size={"n", "W", "n·W"}, cx=["O(n²)", "O(n³)"], kind="DP"),
    "reszta": dict(ref=r_reszta, gen=g_reszta, size={"n", "k", "K", "n·k"}, cx=["O(n³)", "O(n²)", "O(n log n)", "O(n)"],
                   kind="DP"),
    "schody": dict(ref=r_schody, gen=g_schody, size={"n"}, cx=NLOGN, kind="DP"),
    "pierwsze_przedzial": dict(ref=r_pierwsze_przedzial, gen=g_pierwsze_przedzial, size={"b", "a"}, cx=NLOGN,
                               kind="matematyka"),
    "fibonacci": dict(ref=r_fibonacci, gen=g_fibonacci, gen_small=gs_fibonacci, size={"n"}, cx=["O(log n) / O(1)"], kind="matematyka"),
    "dzielniki": dict(ref=r_dzielniki, gen=g_dzielniki, gen_small=gs_dzielniki, size={"n"}, cx=["O(√n)"], kind="matematyka"),
    "potega": dict(ref=r_potega, gen=g_potega, gen_small=gs_potega, size={"b", "a"}, cx=["O(log n) / O(1)"], kind="matematyka"),
}


def statement(name: str) -> str:
    return (STATEMENTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


# ============================================================================================ pomiar
def _agree(code: str, ref, gen, tests: int = 20, seed: int = 1, lang: str | None = None) -> tuple[int, int, str]:
    """Ile z `tests` losowych małych testów kod rozwiązuje zgodnie z `ref`. Zwraca (ok, total, pierwsza różnica)."""
    from oiforge.core import runner
    prog = runner.prepare(code, lang=lang)
    if not prog.ok:
        return 0, tests, "błąd kompilacji"
    rnd = random.Random(seed)
    ok = 0
    first = ""
    for _ in range(tests):
        inp = gen(rnd)
        exp = ref(inp)
        rr = runner.run(prog.cmd, inp, timeout=3.0)
        good = rr.verdict == "OK" and runner.outputs_equal(rr.stdout, exp)
        ok += good
        if not good and not first:
            first = f"wejście {inp!r}: oczekiwano {exp!r}, otrzymano {rr.stdout[:80]!r} ({rr.verdict})"
    return ok, tests, first


def check_samples_against_refs() -> list[str]:
    """Kontrola danych: przykłady w treściach zgadzają się z rozwiązaniami wzorcowymi."""
    from oiforge.core import statement as ST
    bad = []
    for name, t in TASKS.items():
        p = ST.parse(statement(name))
        if not p["samples"]:
            bad.append(f"{name}: brak przykładów")
        for s in p["samples"]:
            if t["ref"](s["input"]).split() != s["output"].split():
                bad.append(f"{name}: przykład {s['name']} — ref daje {t['ref'](s['input'])!r}, treść {s['output']!r}")
    return bad


def measure(names=None, do_solver=True, do_brute=False, do_suggest=True, verbose=True) -> dict:
    # do_brute: zostawione dla zgodności wywołań — auto-brute został usunięty
    from oiforge.core import statement as ST
    from oiforge.tools import autosolver as AS
    from oiforge.tools import suggest as SG
    rows = {}
    for name in names or TASKS:
        t = TASKS[name]
        text = statement(name)
        row: dict = {"kind": t["kind"]}
        t0 = time.time()
        if do_suggest:
            p = ST.parse(text)
            cx = SG.complexity_info(p)
            var_ok = t["size"] is None or cx.get("var") in t["size"]
            cx_ok = t["cx"] is None or cx.get("complexity") in t["cx"]
            row["suggest"] = {"var": cx.get("var"), "max": cx.get("max_fmt"), "complexity": cx.get("complexity"),
                              "ok": bool(var_ok and cx_ok)}
        if do_solver:
            r = AS.solve(text, use_user_code=False)
            sol = {"status": r["status"], "id": (r.get("best") or {}).get("id")}
            if r["status"] == "solved":
                ok, tot, diff = _agree(r["best"]["code"], t["ref"], t["gen"])
                sol.update(agree=f"{ok}/{tot}", ok=ok == tot, diff=diff)
            else:
                sol["ok"] = False
            row["solver"] = sol
        row["time"] = round(time.time() - t0, 1)
        rows[name] = row
        if verbose:
            print(f"{name:20s} " + " | ".join(
                f"{k}: " + (("✔ " if v.get("ok") else "✘ ") + json.dumps({kk: vv for kk, vv in v.items() if kk not in ("ok", "diff")},
                                                                            ensure_ascii=False))
                for k, v in row.items() if isinstance(v, dict)) + f" | {row['time']} s", flush=True)
    summary = {}
    for key in ("solver", "suggest"):
        vals = [r[key]["ok"] for r in rows.values() if key in r]
        if vals:
            summary[key] = f"{sum(vals)}/{len(vals)} ({round(100 * sum(vals) / len(vals))}%)"
    brute_correct = [r["brute"].get("correct") for r in rows.values() if "brute" in r and r["brute"]["ok"]]
    if brute_correct:
        summary["brute_correct_on_random"] = f"{sum(bool(x) for x in brute_correct)}/{len(brute_correct)}"
    return {"rows": rows, "summary": summary}


if __name__ == "__main__":
    args = sys.argv[1:]
    only = None
    if "--only" in args:
        only = args[args.index("--only") + 1].split(",")
    bad = check_samples_against_refs()
    if bad:
        print("UWAGA — dane niespójne:\n" + "\n".join(bad))
    res = measure(only, do_solver="--no-solver" not in args, do_brute="--no-brute" not in args,
                  do_suggest="--no-suggest" not in args)
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    if "--json" in args:
        Path(args[args.index("--json") + 1]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

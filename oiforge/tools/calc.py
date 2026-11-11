"""Kalkulator OI (agent J): arytmetyka modularna, teoria liczb, bity, systemy liczbowe,
kalkulator limitów złożoności i kalkulator pamięci deklaracji C++.

Python API (czyste funkcje, bez HTTP — używane też w testach):
    evaluate(expr, names=None, cpp=False) -> int          bezpieczne wyrażenia: 2e5+5, 10^9+7, 1<<20, N*2
    is_prime(n) -> bool                                    deterministyczny Miller–Rabin dla n < 3.3·10^24
    factorize(n, deadline) -> (dict p->e, nierozłożone)   Pollard–Brent + Miller–Rabin
    ext_gcd(a, b) -> (g, x, y)                             a·x + b·y = g
    crt([(a, m), ...]) -> (x, M) | None                    uogólnione CRT (moduły nie muszą być względnie pierwsze)
    ncr_mod(n, k, m) -> (wynik, metoda, dokładnie|None)    silnie / Fermat / Lucas / Lucas + CRT
    fib_mod(n, m), fact_mod(n, m)
    bits_info(v, typ), double_info(s), convert_base(s, from, to)
    limits_table(n, q, m, time_limit, ops_per_sec), memory_report(decls, limit_mb, vars_text)
HTTP: POST /api/calc/<op> (lista: GET /api/calc/ops). Każde obliczenie ma kooperacyjny limit czasu.
"""
from __future__ import annotations

import ast
import math
import random
import re
import struct
import time
from decimal import Decimal, InvalidOperation

try:
    from oiforge.server_api import ApiError
except ImportError:  # pragma: no cover - użycie poza serwerem
    class ApiError(Exception):
        def __init__(self, message, status=400, **extra):
            super().__init__(message)
            self.message, self.status, self.extra = message, status, extra

TIME_LIMIT = 3.0                 # s na jedno żądanie
MAX_INPUT_CHARS = 20000
MAX_DIGITS = 4000                # Python domyślnie nie zamienia na tekst liczb > 4300 cyfr
MAX_BITS = int(MAX_DIGITS * 3.3219280948873626)
LOG10_2 = math.log10(2)


# ============================================================================================ limit czasu
class CalcTimeout(Exception):
    pass


class Deadline:
    """Kooperacyjny limit czasu — pętle wołają .check()."""
    __slots__ = ("end", "n", "seconds")

    def __init__(self, seconds: float | None = TIME_LIMIT):
        self.seconds = seconds
        self.end = None if seconds is None else time.perf_counter() + seconds
        self.n = 0

    def check(self, every: int = 1) -> None:
        if self.end is None:
            return
        self.n += 1
        if self.n % every == 0 and time.perf_counter() > self.end:
            raise CalcTimeout()

    def expired(self) -> bool:
        return self.end is not None and time.perf_counter() > self.end


NO_LIMIT = Deadline(None)


# ============================================================================================ wyrażenia
_SCI_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d*)?|\.\d+)[eE]([+-]?\d+)(?![\w.])")
_SUFFIX_RE = re.compile(r"\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)(?:[uU](?:ll|LL|l|L)?|(?:ll|LL|l|L)[uU]?)\b")
_FUNCS = {
    "min": min, "max": max, "abs": abs, "gcd": math.gcd, "lcm": math.lcm, "isqrt": math.isqrt,
    "sqrt": math.isqrt, "log2": lambda x: x.bit_length() - 1 if x > 0 else _bad("log2 z liczby ≤ 0"),
}


def _bad(msg: str):
    raise ValueError(msg)


def _prep_expr(s: str, cpp: bool = False) -> tuple[str, list[str]]:
    """Normalizuje zapis: 2·10^5, 1'000'000, 2e5, 1000000LL, 1 000 000 → poprawny Python."""
    notes: list[str] = []
    s = str(s).strip()
    for a, b in (("·", "*"), ("×", "*"), ("⋅", "*"), ("∗", "*"), ("−", "-"), ("–", "-"), ("÷", "/"), (" ", " ")):
        s = s.replace(a, b)
    s = re.sub(r"(?<=\d)'(?=\d)", "", s)                     # separator cyfr C++14
    s = re.sub(r"(?<=\d)_(?=\d)", "", s)
    s = re.sub(r"(?<=\d)[  ](?=\d{3}(?!\d))", "", s)     # 1 000 000
    s = re.sub(r"\btrue\b", "1", s)
    s = re.sub(r"\bfalse\b", "0", s)
    s = re.sub(r"\(\s*(?:long\s+long|long|int|unsigned(?:\s+long\s+long)?|ll|size_t|int64_t|uint64_t)\s*\)", "", s)  # rzutowania
    s = _SUFFIX_RE.sub(r"\1", s)

    def sci(m):
        try:
            d = Decimal(m.group(0))
        except InvalidOperation:
            raise ValueError(f"Niepoprawna liczba „{m.group(0)}”.")
        if abs(int(m.group(2))) > 5000:
            raise ValueError(f"Wykładnik w „{m.group(0)}” jest za duży.")
        if d != d.to_integral_value():
            raise ValueError(f"„{m.group(0)}” nie jest liczbą całkowitą.")
        return str(int(d))
    s = _SCI_RE.sub(sci, s)
    s = re.sub(r"(?<![.\w])0+(\d)", r"\1", s)                    # 007 → 7 (bez ósemkowych)
    if "^" in s:
        if cpp:
            notes.append("W C++ `^` to XOR, a nie potęga! `10^5` daje 15. Potęgę zapisz jako `1e5` albo `100000`.")
        else:
            s = s.replace("^", "**")
    return s, notes


def evaluate(expr, names: dict | None = None, cpp: bool = False, notes: list | None = None) -> int:
    """Bezpiecznie liczy wyrażenie całkowite. cpp=True: semantyka C++ (^ = XOR, / i % obcinają do zera)."""
    if isinstance(expr, bool):
        return int(expr)
    if isinstance(expr, int):
        return expr
    if isinstance(expr, float):
        if not math.isfinite(expr) or expr != int(expr):
            raise ValueError("Oczekiwano liczby całkowitej.")
        return int(expr)
    text = str(expr if expr is not None else "")
    if not text.strip():
        raise ValueError("Puste wyrażenie.")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("Za długie wyrażenie.")
    src, n2 = _prep_expr(text, cpp)
    if notes is not None:
        notes.extend(n2)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValueError(f"Nie rozumiem wyrażenia „{text.strip()[:60]}”. Przykłady: 2e5+5, 10^9+7, 1<<20, 2*N+1.")
    names = names or {}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return int(node.value)
            if isinstance(node.value, int):
                return node.value
            if isinstance(node.value, float) and node.value.is_integer():
                return int(node.value)
            raise ValueError("Dozwolone są tylko liczby całkowite.")
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise ValueError(f"Nieznana nazwa „{node.id}”.")
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return v
            if isinstance(node.op, ast.Invert):
                return ~v
            if isinstance(node.op, ast.Not):
                return int(not v)
            raise ValueError("Niedozwolony operator.")
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            op = node.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                if a.bit_length() + b.bit_length() > MAX_BITS * 4:
                    raise ValueError("Wynik mnożenia jest za duży.")
                return a * b
            if isinstance(op, (ast.Div, ast.FloorDiv)):
                if b == 0:
                    raise ValueError("Dzielenie przez zero.")
                if cpp or isinstance(op, ast.Div):
                    q = abs(a) // abs(b)
                    return q if (a >= 0) == (b >= 0) else -q
                return a // b
            if isinstance(op, ast.Mod):
                if b == 0:
                    raise ValueError("Modulo przez zero.")
                if cpp:
                    r = abs(a) % abs(b)
                    return r if a >= 0 else -r
                return a % b
            if isinstance(op, ast.Pow):
                if b < 0:
                    raise ValueError("Ujemny wykładnik nie daje liczby całkowitej.")
                if abs(a) > 1 and abs(a).bit_length() * b > MAX_BITS * 4:
                    raise ValueError("Potęga jest za duża (ponad ~16 000 cyfr). Użyj potęgowania modulo.")
                return a ** b
            if isinstance(op, ast.LShift):
                if b < 0 or b > MAX_BITS * 4:
                    raise ValueError("Niepoprawne przesunięcie bitowe.")
                return a << b
            if isinstance(op, ast.RShift):
                if b < 0:
                    raise ValueError("Ujemne przesunięcie bitowe.")
                return a >> b
            if isinstance(op, ast.BitAnd):
                return a & b
            if isinstance(op, ast.BitOr):
                return a | b
            if isinstance(op, ast.BitXor):
                return a ^ b
            raise ValueError("Niedozwolony operator.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            args = [ev(x) for x in node.args]
            if not args:
                raise ValueError(f"{node.func.id}() wymaga argumentów.")
            if node.func.id in ("isqrt", "sqrt") and args[0] < 0:
                raise ValueError("Pierwiastek z liczby ujemnej.")
            return int(_FUNCS[node.func.id](*args))
        raise ValueError(f"Nie rozumiem wyrażenia „{text.strip()[:60]}”.")
    return ev(tree)


def fmt_int(x: int) -> str:
    """Liczba jako tekst; olbrzymie liczby jako skrót (Python nie zamienia > 4300 cyfr)."""
    if x.bit_length() <= MAX_BITS:
        return str(x)
    digits = int(x.bit_length() * LOG10_2)
    shift = max(0, digits - 30)
    head = str(abs(x) // (10 ** shift))
    tail = str(abs(x) % 10 ** 20).rjust(20, "0")
    total = shift + len(head)
    return f"{'-' if x < 0 else ''}{head[:20]}…{tail} ({total} cyfr)"


def sci(x: int | float) -> str:
    """Zwięzły zapis naukowy: 3.5·10^6."""
    if isinstance(x, int):
        if abs(x) < 10 ** 6:
            return str(x)
        e = len(str(abs(x))) - 1 if x.bit_length() <= MAX_BITS else int(x.bit_length() * LOG10_2)
        m = abs(x) / 10 ** e if e < 300 else float(str(abs(x))[:6]) / 10 ** 5
        return f"{'-' if x < 0 else ''}{m:.3g}·10^{e}"
    if x == 0:
        return "0"
    e = math.floor(math.log10(abs(x)))
    if -3 <= e < 6:
        return str(round(x)) if abs(x) >= 100 else f"{x:.3g}"
    return f"{x / 10 ** e:.3g}·10^{e}"


# ============================================================================================ liczby pierwsze
def _small_primes(limit: int) -> list[int]:
    sieve = bytearray([1]) * (limit + 1)
    sieve[0:2] = b"\x00\x00"
    for p in range(2, int(limit ** 0.5) + 1):
        if sieve[p]:
            sieve[p * p::p] = bytearray(len(range(p * p, limit + 1, p)))
    return [i for i in range(limit + 1) if sieve[i]]


SMALL_PRIMES = _small_primes(10000)
_MR_BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)
MR_DETERMINISTIC_LIMIT = 3317044064679887385961981      # dla n < tej liczby bazy 2..41 dają wynik pewny


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in SMALL_PRIMES[:46]:          # liczby pierwsze < 200
        if n % p == 0:
            return n == p
    if n < 200 * 200:
        return True
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    bases = _MR_BASES
    if n >= MR_DETERMINISTIC_LIMIT:
        rng = random.Random(n & 0xFFFFFFFF)
        bases = _MR_BASES + tuple(rng.randrange(43, n - 1) for _ in range(24))
    for a in bases:
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def primality_kind(n: int) -> str:
    return "deterministyczny" if n < MR_DETERMINISTIC_LIMIT else "probabilistyczny (błąd < 4⁻³⁷)"


def _pollard_brent(n: int, dl: Deadline) -> int:
    if n % 2 == 0:
        return 2
    rng = random.Random(n * 2654435761 & 0xFFFFFFFFFFFF)
    while True:
        y, c, m = rng.randrange(1, n), rng.randrange(1, n), 128
        g = r = q = 1
        x = ys = y
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                ys = y
                for _ in range(min(m, r - k)):
                    y = (y * y + c) % n
                    q = q * abs(x - y) % n
                g = math.gcd(q, n)
                k += m
                dl.check()
            r <<= 1
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = math.gcd(abs(x - ys), n)
                dl.check(256)
        if g != n:
            return g


def factorize(n: int, dl: Deadline = NO_LIMIT) -> tuple[dict[int, int], list[int]]:
    """Rozkład |n| na czynniki: ({p: e}, lista nierozłożonych reszt — niepusta tylko przy przekroczeniu czasu)."""
    n = abs(n)
    fac: dict[int, int] = {}
    if n < 2:
        return fac, []
    for p in SMALL_PRIMES:
        if p * p > n:
            break
        if n % p == 0:
            e = 0
            while n % p == 0:
                n //= p
                e += 1
            fac[p] = e
    rest: list[int] = []
    stack = [n] if n > 1 else []
    while stack:
        m = stack.pop()
        if m == 1:
            continue
        if is_prime(m):
            fac[m] = fac.get(m, 0) + 1
            continue
        r = math.isqrt(m)
        if r * r == m:
            stack += [r, r]
            continue
        try:
            d = _pollard_brent(m, dl)
        except CalcTimeout:
            rest.append(m)
            rest.extend(x for x in stack if x > 1)
            break
        stack += [d, m // d]
    return dict(sorted(fac.items())), sorted(rest)


def divisor_count(fac: dict[int, int]) -> int:
    out = 1
    for e in fac.values():
        out *= e + 1
    return out


def divisor_sum(fac: dict[int, int]) -> int:
    out = 1
    for p, e in fac.items():
        out *= (p ** (e + 1) - 1) // (p - 1)
    return out


def euler_phi(fac: dict[int, int]) -> int:
    out = 1
    for p, e in fac.items():
        out *= (p - 1) * p ** (e - 1)
    return out


def divisors_list(fac: dict[int, int], limit: int = 5000) -> list[int] | None:
    if divisor_count(fac) > limit:
        return None
    ds = [1]
    for p, e in fac.items():
        ds = [d * p ** k for d in ds for k in range(e + 1)]
    return sorted(ds)


def next_prime(n: int, dl: Deadline = NO_LIMIT) -> int:
    c = max(2, n + 1)
    while not is_prime(c):
        c += 1
        dl.check(64)
    return c


def prev_prime(n: int, dl: Deadline = NO_LIMIT) -> int | None:
    c = n - 1
    while c >= 2:
        if is_prime(c):
            return c
        c -= 1
        dl.check(64)
    return None


def fmt_factorization(fac: dict[int, int], rest: list[int] | None = None) -> str:
    parts = [f"{p}^{e}" if e > 1 else str(p) for p, e in fac.items()]
    parts += [f"[{r}]" for r in (rest or [])]
    return " · ".join(parts) if parts else "1"


# ============================================================================================ NWD, CRT, modulo
def ext_gcd(a: int, b: int) -> tuple[int, int, int]:
    """(g, x, y): a·x + b·y = g = NWD(a, b) ≥ 0."""
    old_r, r, old_s, s, old_t, t = a, b, 1, 0, 0, 1
    while r:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
        old_t, t = t, old_t - q * t
    if old_r < 0:
        old_r, old_s, old_t = -old_r, -old_s, -old_t
    return old_r, old_s, old_t


def ext_gcd_steps(a: int, b: int, max_rows: int = 80) -> list[dict]:
    rows = []
    old_r, r, old_s, s, old_t, t = a, b, 1, 0, 0, 1
    rows.append({"r": str(old_r), "q": "", "x": str(old_s), "y": str(old_t)})
    while r:
        q = old_r // r
        rows.append({"r": str(r), "q": str(q), "x": str(s), "y": str(t)})
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
        old_t, t = t, old_t - q * t
        if len(rows) > max_rows:
            break
    rows.append({"r": "0", "q": "", "x": "", "y": ""})
    return rows


def mod_inverse(a: int, m: int) -> int | None:
    if m < 1:
        return None
    try:
        return pow(a, -1, m)
    except ValueError:
        return None


def crt(pairs) -> tuple[int, int] | None:
    """Uogólnione CRT: x ≡ a_i (mod m_i). Zwraca (x, M) z 0 ≤ x < M = NWW(m_i) albo None (sprzeczne)."""
    x, M = 0, 1
    for a, m in pairs:
        if m <= 0:
            raise ValueError("Moduły muszą być dodatnie.")
        a %= m
        g, p, _ = ext_gcd(M, m)
        if (a - x) % g:
            return None
        lcm = M // g * m
        t = ((a - x) // g * p) % (m // g)
        x = (x + M * t) % lcm
        M = lcm
    return x, M


# ============================================================================================ kombinatoryka
def _binom_prime_digit(n: int, k: int, p: int, fact: list[int] | None, dl: Deadline) -> int:
    """C(n, k) mod p dla 0 ≤ k ≤ n < p (p pierwsze)."""
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    if fact is not None:
        return fact[n] * pow(fact[k] * fact[n - k] % p, p - 2, p) % p
    if k > 3_000_000:
        raise ValueError("Za duże k dla tak dużego modułu (potrzeba > 3·10^6 mnożeń).")
    num = den = 1
    for i in range(k):
        num = num * (n - i) % p
        den = den * (i + 1) % p
        dl.check(8192)
    return num * pow(den, p - 2, p) % p


def lucas(n: int, k: int, p: int, dl: Deadline = NO_LIMIT) -> int:
    fact = None
    if p <= 1_000_000:
        fact = [1] * p
        for i in range(1, p):
            fact[i] = fact[i - 1] * i % p
            if i & 8191 == 0:
                dl.check()
    res = 1
    while n or k:
        ni, ki = n % p, k % p
        if ki > ni:
            return 0
        res = res * _binom_prime_digit(ni, ki, p, fact, dl) % p
        n //= p
        k //= p
    return res


def ncr_mod(n: int, k: int, m: int, dl: Deadline = NO_LIMIT) -> tuple[int, str, int | None]:
    """(C(n, k) mod m, opis metody, dokładna wartość C(n, k) albo None)."""
    if n < 0 or k < 0:
        raise ValueError("n i k muszą być nieujemne.")
    if m < 1:
        raise ValueError("Moduł musi być dodatni.")
    if k > n:
        return 0, "k > n, więc C(n, k) = 0", 0
    k = min(k, n - k)
    if n <= 200_000:
        exact = math.comb(n, k)
        return exact % m, "dokładnie (math.comb), potem modulo", exact
    if is_prime(m):
        if n < m:
            return _binom_prime_digit(n, k, m, None, dl), "iloczyn n·(n−1)·…·(n−k+1) / k! z odwrotnością z małego tw. Fermata", None
        return lucas(n, k, m, dl), f"twierdzenie Lucasa (cyfry n i k w systemie o podstawie {m})", None
    fac, rest = factorize(m, dl)
    if rest or any(e > 1 for e in fac.values()):
        raise ValueError("Dla n > 200 000 i modułu podzielnego przez kwadrat liczby pierwszej potrzebny jest uogólniony "
                         "Lucas (Granville) — nieobsługiwane. Spróbuj modułu pierwszego albo bezkwadratowego.")
    pairs = [(lucas(n, k, p, dl), p) for p in fac]
    x, _ = crt(pairs)
    return x, "Lucas dla każdego czynnika pierwszego modułu + chińskie twierdzenie o resztach (CRT)", None


def fib_mod(n: int, m: int | None = None) -> int:
    """F(n) (F(0)=0, F(1)=1) modulo m — szybkie podwajanie, O(log n)."""
    if n < 0:
        raise ValueError("n musi być nieujemne.")
    a, b = 0, 1
    for bit in bin(n)[2:]:
        if m:
            c = a * ((2 * b - a) % m) % m
            d = (a * a + b * b) % m
        else:
            c = a * (2 * b - a)
            d = a * a + b * b
        if bit == "1":
            a, b = d, (c + d) % m if m else c + d
        else:
            a, b = c, d
    return a % m if m else a


def fact_mod(n: int, m: int, dl: Deadline = NO_LIMIT) -> tuple[int, str]:
    if n < 0:
        raise ValueError("n musi być nieujemne.")
    if m < 1:
        raise ValueError("Moduł musi być dodatni.")
    if m == 1:
        return 0, "wszystko modulo 1 to 0"
    if n >= m:
        return 0, f"n ≥ m, więc m dzieli n! (czynnik {m} występuje w iloczynie)"
    if is_prime(m) and m - 1 - n < n:
        if m - 1 - n > 10_000_000:
            raise ValueError("Za dużo mnożeń (> 10^7).")
        prod = 1
        for i in range(n + 1, m):
            prod = prod * i % m
            dl.check(16384)
        return (m - 1) * pow(prod, m - 2, m) % m, "tw. Wilsona: (m−1)! ≡ −1, więc n! ≡ −1 / ((n+1)·…·(m−1))"
    if n > 10_000_000:
        raise ValueError("n > 10^7 — za dużo mnożeń w limicie czasu.")
    r = 1
    for i in range(2, n + 1):
        r = r * i % m
        dl.check(16384)
    return r, "iloczyn 1·2·…·n z modulo po każdym kroku"


# ============================================================================================ bity
INT_TYPES = {
    "int8": (8, True, "int8_t"), "uint8": (8, False, "uint8_t"),
    "int16": (16, True, "short"), "uint16": (16, False, "unsigned short"),
    "int32": (32, True, "int"), "uint32": (32, False, "unsigned int"),
    "int64": (64, True, "long long"), "uint64": (64, False, "unsigned long long"),
    "int128": (128, True, "__int128"), "uint128": (128, False, "unsigned __int128"),
}


def bits_info(v: int, typ: str = "int64") -> dict:
    if typ not in INT_TYPES:
        raise ValueError("Nieznany typ. Dostępne: " + ", ".join(INT_TYPES))
    w, signed, cname = INT_TYPES[typ]
    lo, hi = (-(1 << (w - 1)), (1 << (w - 1)) - 1) if signed else (0, (1 << w) - 1)
    mask = (1 << w) - 1
    u = v & mask
    sval = u - (1 << w) if signed and u >> (w - 1) else u

    def as_type(x):
        x &= mask
        return x - (1 << w) if signed and x >> (w - 1) else x

    pop = bin(u).count("1")
    ll = w > 32
    sfx = "ll" if ll else ""
    out = {
        "type": typ, "cname": cname, "width": w, "signed": signed, "min": str(lo), "max": str(hi),
        "input": str(v), "overflow": not (lo <= v <= hi), "value": str(sval), "unsigned": str(u),
        "bits": format(u, f"0{w}b"), "hex": "0x" + format(u, f"0{w // 4}X"), "oct": "0" + format(u, "o"),
        "popcount": pop, "parity": pop & 1, "is_pow2": u != 0 and u & (u - 1) == 0 and not (signed and sval < 0),
        "highest": (u.bit_length() - 1) if u else None,
        "lowest": ((u & -u).bit_length() - 1) if u else None,
        "clz": w - u.bit_length(), "ctz": ((u & -u).bit_length() - 1) if u else w,
        "ops": [
            {"expr": "x & -x", "desc": "najniższy zapalony bit (lowbit — Fenwick)", "value": str(as_type(u & -u))},
            {"expr": "x & (x - 1)", "desc": "zgaszenie najniższego zapalonego bitu", "value": str(as_type(u & (u - 1)))},
            {"expr": "x | (x + 1)", "desc": "zapalenie najniższego zgaszonego bitu", "value": str(as_type(u | (u + 1)))},
            {"expr": "~x", "desc": "negacja wszystkich bitów (= −x − 1 dla typów ze znakiem)", "value": str(as_type(~u))},
            {"expr": "-x", "desc": "liczba przeciwna w U2 (= ~x + 1)", "value": str(as_type(-u))},
            {"expr": "x << 1", "desc": "mnożenie przez 2" + (" (przepełnienie!)" if as_type(u << 1) != sval * 2 else ""), "value": str(as_type(u << 1))},
            {"expr": "x >> 1", "desc": "dzielenie przez 2 (dla ujemnych: zaokrąglenie w dół)" if signed else "dzielenie przez 2", "value": str(sval >> 1)},
            {"expr": "x ^ (x >> 1)", "desc": "kod Graya", "value": str(as_type(u ^ (u >> 1)))},
        ],
        "builtins": [
            {"expr": f"__builtin_popcount{sfx}(x)", "value": str(pop), "desc": "liczba zapalonych bitów"},
            {"expr": f"__builtin_clz{sfx}(x)", "value": str(w - u.bit_length()) if u else "UB dla x = 0", "desc": "zera wiodące"},
            {"expr": f"__builtin_ctz{sfx}(x)", "value": str((u & -u).bit_length() - 1) if u else "UB dla x = 0", "desc": "zera końcowe"},
            {"expr": "__lg(x)" if w <= 64 else "63 - __builtin_clzll(x)", "value": str(u.bit_length() - 1) if u else "UB dla x = 0", "desc": "indeks najwyższego bitu (⌊log₂ x⌋)"},
            {"expr": f"__builtin_parity{sfx}(x)", "value": str(pop & 1), "desc": "parzystość liczby bitów"},
        ] if w in (32, 64) else [],
    }
    if out["overflow"]:
        out["overflow_note"] = (f"{v} nie mieści się w typie {cname} (zakres {lo} … {hi}). Po rzutowaniu bity zostaną "
                                f"obcięte do {w} i wyjdzie {sval}.")
    if signed and sval < 0:
        a = -sval
        out["u2"] = {"abs_bits": format(a & mask, f"0{w}b"), "not_bits": format(~a & mask, f"0{w}b"), "result_bits": out["bits"],
                     "text": f"U2: zapisz |x| = {a}, zaneguj wszystkie bity, dodaj 1. Najstarszy bit (znak) = 1 oznacza wartość ujemną: "
                             f"−2^{w - 1} + {u - (1 << (w - 1))}."}
    return out


def eval_float(expr: str) -> float:
    """Wyrażenie zmiennoprzecinkowe (jak double w C++): 0.1+0.2, 1/3, 2**-10, 1e-9, pi."""
    src = str(expr).strip().replace("^", "**").replace("·", "*").replace("×", "*")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValueError(f"„{src[:40]}” nie jest liczbą ani wyrażeniem.")
    consts = {"pi": math.pi, "e": math.e, "inf": math.inf, "nan": math.nan}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in consts:
            return consts[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                if b == 0:
                    return math.copysign(math.inf, a) if a else math.nan
                return a / b
            if isinstance(node.op, ast.Pow):
                if abs(b) > 4096:
                    raise ValueError("Za duży wykładnik.")
                return a ** b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("sqrt", "exp", "log", "log2", "log10", "sin", "cos") \
                and len(node.args) == 1:
            return float(getattr(math, node.func.id)(ev(node.args[0])))
        raise ValueError(f"„{src[:40]}” nie jest liczbą ani prostym wyrażeniem (+ − · / ^).")
    try:
        return float(ev(tree))
    except OverflowError:
        return math.inf
    except (ValueError, ZeroDivisionError) as e:
        if isinstance(e, ValueError) and str(e):
            raise
        raise ValueError("Niepoprawne wyrażenie.")


def double_info(text: str) -> dict:
    s = str(text).strip().replace(",", ".").replace("−", "-")
    if not s:
        raise ValueError("Podaj liczbę, np. 0.1 albo 1e-9.")
    try:
        x = float.fromhex(s) if re.match(r"^[+-]?0[xX]", s) else float(s)
    except ValueError:
        x = eval_float(s)
    bits = struct.unpack(">Q", struct.pack(">d", x))[0]
    sign, exp, man = bits >> 63, (bits >> 52) & 0x7FF, bits & ((1 << 52) - 1)
    if exp == 0x7FF:
        kind = "NaN" if man else ("+∞" if not sign else "−∞")
    elif exp == 0:
        kind = "zero" if man == 0 else "podnormalna (subnormal)"
    else:
        kind = "znormalizowana"
    out = {"input": s, "value": repr(x), "bits": format(bits, "064b"), "sign": sign, "exponent_raw": exp,
           "mantissa_bits": format(man, "052b"), "kind": kind, "hex": "0x" + format(bits, "016X")}
    if math.isfinite(x):
        e = (exp - 1023) if exp else -1022
        lead = 1 if exp else 0
        out["exponent"] = e
        out["formula"] = f"(−1)^{sign} · {lead}.{format(man, '052b').rstrip('0') or '0'}₂ · 2^{e}"
        out["mantissa_value"] = repr(lead + man / 2 ** 52)
        exact = Decimal(x)
        ex = format(exact, "f")
        out["exact"] = ex if len(ex) <= 1200 else format(exact, ".60e")
        out["float_hex"] = x.hex()
        out["ulp"] = repr(math.ulp(x))
        out["next_up"] = repr(math.nextafter(x, math.inf))
        out["next_down"] = repr(math.nextafter(x, -math.inf))
        try:
            dec_in = Decimal(s)
            out["exactly_representable"] = dec_in == exact
            err = dec_in - exact
            out["error"] = format(err, ".3e") if err else "0"
        except InvalidOperation:
            out["exactly_representable"] = None
        out["safe_int"] = abs(x) <= 2 ** 53
        if abs(x) > 2 ** 53 and x == int(x):
            out["int_note"] = "Powyżej 2^53 ≈ 9·10^15 double nie przechowa każdej liczby całkowitej — używaj long long."
    try:
        f32 = struct.unpack(">f", struct.pack(">f", x))[0]
        b32 = struct.unpack(">I", struct.pack(">f", x))[0]
    except OverflowError:
        f32, b32 = (math.inf if x > 0 else -math.inf), (0x7F800000 if x > 0 else 0xFF800000)
    out["float32"] = {"value": repr(f32), "bits": format(b32, "032b"), "exact": format(Decimal(f32), "f")[:200] if math.isfinite(f32) else repr(f32),
                      "rel_error": (format(abs(f32 - x) / abs(x), ".2e") if math.isfinite(f32) and x and math.isfinite(x) else None)}
    return out


# ============================================================================================ systemy liczbowe
DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def to_base(n: int, b: int) -> str:
    if not 2 <= b <= 36:
        raise ValueError("Podstawa musi być z zakresu 2–36.")
    if n == 0:
        return "0"
    neg, n = n < 0, abs(n)
    if b == 10:
        return ("-" if neg else "") + fmt_int(n)
    if b in (2, 8, 16):
        s = format(n, {2: "b", 8: "o", 16: "X"}[b])
    else:
        out = []
        while n:
            n, r = divmod(n, b)
            out.append(DIGITS[r])
        s = "".join(reversed(out))
    return ("-" if neg else "") + s


def parse_base(text: str, base: int) -> int:
    s = re.sub(r"[\s_']", "", str(text)).strip()
    if not s:
        raise ValueError("Podaj liczbę.")
    if len(s) > MAX_INPUT_CHARS:
        raise ValueError("Za długa liczba.")
    neg = s.startswith("-")
    if s[0] in "+-":
        s = s[1:]
    low = s.lower()
    for pref, pb in (("0x", 16), ("0b", 2), ("0o", 8)):
        if low.startswith(pref) and (base in (0, pb)):
            s, base = s[2:], pb
            break
    if base == 0:
        base = 10
    if not 2 <= base <= 36:
        raise ValueError("Podstawa musi być z zakresu 2–36.")
    if base == 10 and len(s) > MAX_DIGITS:
        raise ValueError(f"Za długa liczba (maks. {MAX_DIGITS} cyfr dziesiętnych).")
    for ch in s:
        d = DIGITS.find(ch.upper())
        if d < 0 or d >= base:
            raise ValueError(f"Znak „{ch}” nie jest cyfrą w systemie o podstawie {base}"
                             + (f" (dozwolone: 0–{DIGITS[base - 1]})." if base > 1 else "."))
    v = int(s, base)
    return -v if neg else v


def convert_base(text: str, from_base: int, to_base_: int) -> dict:
    v = parse_base(text, from_base)
    out = {"value": fmt_int(v), "result": to_base(v, to_base_), "from": from_base or 10, "to": to_base_,
           "common": [{"base": b, "name": nm, "text": to_base(v, b)} for b, nm in
                      ((2, "dwójkowy"), (8, "ósemkowy"), (10, "dziesiętny"), (16, "szesnastkowy"), (36, "36-kowy"))],
           "bits": abs(v).bit_length(), "digits": len(to_base(abs(v), to_base_)) if abs(v).bit_length() <= MAX_BITS else None}
    steps = []
    if to_base_ != 10 and 0 < abs(v) < 10 ** 18:
        n = abs(v)
        while n and len(steps) < 70:
            q, r = divmod(n, to_base_)
            steps.append({"n": str(n), "q": str(q), "r": DIGITS[r]})
            n = q
    out["div_steps"] = steps
    fb = from_base or 10
    src = re.sub(r"[\s_'+-]", "", str(text))
    if src.lower().startswith(("0x", "0b", "0o")) and fb in (2, 8, 16):
        src = src[2:]
    if fb != 10 and 0 < len(src) <= 16:
        terms = []
        for i, ch in enumerate(src):
            p = len(src) - 1 - i
            d = DIGITS.find(ch.upper())
            terms.append({"digit": ch.upper(), "power": p, "value": str(d * fb ** p)})
        out["horner"] = terms
    return out


# ============================================================================================ limity złożoności
COMPLEXITIES = [
    ("O(1)", lambda n, L: 0.0, "wzór matematyczny, tablica wyników, prosty warunek"),
    ("O(log n)", lambda n, L: math.log10(L), "wyszukiwanie binarne, szybkie potęgowanie, NWD"),
    ("O(√n)", lambda n, L: 0.5 * math.log10(n), "rozkład na czynniki, dzielniki, test pierwszości naiwny, dekompozycja pierwiastkowa (pojedyncze zapytanie)"),
    ("O(n)", lambda n, L: math.log10(n), "jedno przejście, sumy prefiksowe, dwa wskaźniki, BFS/DFS, KMP, stos monotoniczny"),
    ("O(n log n)", lambda n, L: math.log10(n) + math.log10(L), "sortowanie, set/map, kolejka priorytetowa, Dijkstra, drzewo przedziałowe/Fenwick, wyszukiwanie binarne po wyniku"),
    ("O(n log² n)", lambda n, L: math.log10(n) + 2 * math.log10(L), "binsearch + drzewo przedziałowe, merge sort tree, CDQ, sumy w drzewie z sortowaniem"),
    ("O(n√n)", lambda n, L: 1.5 * math.log10(n), "algorytm Mo, dekompozycja pierwiastkowa, sqrt-heurystyki"),
    ("O(n²)", lambda n, L: 2 * math.log10(n), "dwie pętle, DP O(n²) (LCS, LIS naiwnie, przedziały), wszystkie pary"),
    ("O(n² log n)", lambda n, L: 2 * math.log10(n) + math.log10(L), "pary + sortowanie/binsearch, Dijkstra z każdego wierzchołka w gęstym grafie"),
    ("O(n³)", lambda n, L: 3 * math.log10(n), "Floyd–Warshall, mnożenie macierzy, DP przedziałowe O(n³)"),
    ("O(2^n)", lambda n, L: n * LOG10_2, "wszystkie podzbiory (maski bitowe), backtracking"),
    ("O(n·2^n)", lambda n, L: math.log10(n) + n * LOG10_2, "DP na maskach bitowych (komiwojażer), sumy po podzbiorach (SOS)"),
    ("O(n!)", lambda n, L: math.lgamma(n + 1) / math.log(10), "wszystkie permutacje (next_permutation)"),
]
SPEEDS = {"1e8": 1e8, "2e8": 2e8, "5e8": 5e8, "1e9": 1e9}


def _verdict(log_ops: float, log_budget: float) -> str:
    r = log_ops - log_budget
    if r <= math.log10(0.5):
        return "ok"
    if r <= math.log10(2.5):
        return "risky"
    return "tle"


def _max_n(fn, log_budget: float) -> int | None:
    """Największe n, dla którego log10(ops) ≤ budżet (wyszukiwanie binarne po n)."""
    def f(n):
        return fn(n, max(2.0, math.log2(n)))
    if f(1) > log_budget:
        return 0
    lo, hi = 1, 10 ** 18
    if f(hi) <= log_budget:
        return None                      # bez ograniczenia w praktyce
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if f(mid) <= log_budget:
            lo = mid
        else:
            hi = mid
    return lo


def limits_table(n: int, q: int = 0, m: int = 0, time_limit: float = 1.0, ops_per_sec: float = 2e8) -> dict:
    if n < 1:
        raise ValueError("n musi być ≥ 1.")
    if not (0.01 <= time_limit <= 120):
        raise ValueError("Limit czasu musi być z zakresu 0.01–120 s.")
    if not (1e6 <= ops_per_sec <= 1e11):
        raise ValueError("Niepoprawna szybkość sprawdzarki.")
    log_budget = math.log10(time_limit * ops_per_sec)
    L = max(2.0, math.log2(n))
    rows = []
    best = None
    for name, fn, hint in COMPLEXITIES:
        lo = fn(n, L)
        v = _verdict(lo, log_budget)
        mx = _max_n(fn, log_budget)
        rows.append({"name": name, "log_ops": round(lo, 4), "ops": sci(10 ** lo) if lo < 300 else f"10^{lo:.0f}",
                     "seconds": (10 ** lo / ops_per_sec) if lo < 300 else None, "ratio": round(lo - log_budget, 3),
                     "verdict": v, "max_n": None if mx is None else (sci(mx) if mx >= 10 ** 6 else str(mx)), "hint": hint})
        if v == "ok":
            best = name
    extra = []

    def add(name, lo, hint):
        extra.append({"name": name, "log_ops": round(lo, 4), "ops": sci(10 ** lo) if lo < 300 else f"10^{lo:.0f}",
                      "seconds": 10 ** lo / ops_per_sec if lo < 300 else None, "verdict": _verdict(lo, log_budget), "hint": hint})
    lg = math.log10
    if q and q > 0:
        add("O(n + q)", lg(n + q), "odpowiedzi w O(1) po preprocessingu (sumy prefiksowe, sparse table)")
        add("O((n + q) log n)", lg(n + q) + lg(L), "drzewo przedziałowe / Fenwick, binsearch na posortowanej tablicy")
        add("O((n + q) log² n)", lg(n + q) + 2 * lg(L), "binsearch po drzewie, merge sort tree")
        add("O(n + q√n)", lg(n + q * math.sqrt(n)), "dekompozycja pierwiastkowa")
        add("O((n + q)√n)", lg(n + q) + 0.5 * lg(n), "algorytm Mo (zapytania offline)")
        add("O(n·q)", lg(n) + lg(q), "każde zapytanie liczone naiwnie od nowa")
    if m and m > 0:
        add("O(n + m)", lg(n + m), "BFS, DFS, sortowanie topologiczne, SCC, mosty")
        add("O((n + m) log n)", lg(n + m) + lg(L), "Dijkstra z kolejką priorytetową, Kruskal (sortowanie krawędzi)")
        add("O(n·m)", lg(n) + lg(m), "Bellman–Ford, BFS z każdego wierzchołka")
        add("O(n³)", 3 * lg(n), "Floyd–Warshall")
    rec = None
    if best:
        rec = f"Przy n = {sci(n)} i limicie {time_limit:g} s najwolniejsza bezpieczna złożoność to {best}."
    else:
        rec = f"Nawet O(1) wygląda podejrzanie — sprawdź limit czasu i szybkość sprawdzarki."
    return {"n": str(n), "q": str(q), "m": str(m), "budget": sci(10 ** log_budget), "log_budget": round(log_budget, 4),
            "rows": rows, "extra": extra, "best": best, "recommendation": rec}


# ============================================================================================ kalkulator pamięci
_TYPE_SIZES = {
    "bool": (1, 1), "char": (1, 1), "signed char": (1, 1), "unsigned char": (1, 1), "int8_t": (1, 1), "uint8_t": (1, 1),
    "char8_t": (1, 1), "byte": (1, 1), "short": (2, 2), "unsigned short": (2, 2), "int16_t": (2, 2), "uint16_t": (2, 2),
    "char16_t": (2, 2), "int": (4, 4), "unsigned int": (4, 4), "int32_t": (4, 4), "uint32_t": (4, 4), "float": (4, 4),
    "char32_t": (4, 4), "wchar_t": (4, 4), "long": (8, 8), "unsigned long": (8, 8), "long long": (8, 8),
    "unsigned long long": (8, 8), "int64_t": (8, 8), "uint64_t": (8, 8), "size_t": (8, 8), "ssize_t": (8, 8),
    "ptrdiff_t": (8, 8), "intptr_t": (8, 8), "uintptr_t": (8, 8), "double": (8, 8), "long double": (16, 16),
    "__int128": (16, 16), "unsigned __int128": (16, 16), "__int128_t": (16, 16), "__uint128_t": (16, 16),
    "__float128": (16, 16), "int_fast32_t": (8, 8), "int_least32_t": (4, 4), "int_fast64_t": (8, 8),
}
_QUALIFIERS = {"static", "const", "constexpr", "inline", "volatile", "mutable", "register", "thread_local", "extern",
               "constinit", "typename", "struct", "class", "union", "enum", "alignas"}
_CPP_KEYWORDS = {"return", "if", "else", "for", "while", "do", "switch", "case", "break", "continue", "goto", "using",
                 "namespace", "template", "typedef", "throw", "try", "catch", "delete", "new", "operator", "sizeof",
                 "public", "private", "protected", "friend", "default", "assert", "cin", "cout", "cerr", "printf",
                 "scanf", "puts", "freopen", "memset", "fill", "sort", "this", "static_assert", "co_return"}
_INT_WORDS = {"unsigned", "signed", "short", "long", "int", "char", "double", "float", "bool", "__int128"}
_NODE_CONTAINERS = {"set", "multiset", "map", "multimap"}
_HASH_CONTAINERS = {"unordered_set", "unordered_multiset", "unordered_map", "unordered_multimap", "gp_hash_table", "cc_hash_table"}


class TInfo:
    __slots__ = ("size", "align", "name", "kind", "elem", "per_elem", "note")

    def __init__(self, size, align, name, kind="plain", elem=None, per_elem=None, note=None):
        self.size, self.align, self.name, self.kind = size, align, name, kind
        self.elem, self.per_elem, self.note = elem, per_elem, note


def _align(x: int, a: int) -> int:
    return (x + a - 1) // a * a


def _split_top(s: str, sep: str = ",") -> list[str]:
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _split_top_paren(s: str, sep: str = ",") -> list[str]:
    """Jak _split_top, ale bez traktowania < > jako nawiasów (wyrażenia typu 1<<20)."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _layout(fields: list[TInfo]) -> tuple[int, int]:
    off, al = 0, 1
    for f in fields:
        off = _align(off, f.align) + f.size
        al = max(al, f.align)
    return _align(max(off, 1), al), al


class _MemCtx:
    def __init__(self, names: dict):
        self.names = dict(names)          # stałe/zmienne o znanej wartości
        self.aliases: dict[str, str] = {}
        self.structs: dict[str, TInfo] = {}
        self.define_int_ll = False
        self.notes: list[str] = []

    def norm_type(self, t: str) -> str:
        t = re.sub(r"\bstd\s*::\s*", "", t)
        t = re.sub(r"__gnu_pbds\s*::\s*", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        t = re.sub(r"\s*([<>,*&])\s*", r"\1", t)
        words = [w for w in t.split(" ") if w not in _QUALIFIERS]
        t = " ".join(words)
        return t

    def info(self, t: str, depth: int = 0) -> TInfo | None:
        if depth > 12:
            return None
        t = self.norm_type(t)
        if not t:
            return None
        if t in self.aliases:
            return self.info(self.aliases[t], depth + 1)
        if t.endswith("*") or t.endswith("&"):
            return TInfo(8, 8, t)
        m = re.fullmatch(r"([A-Za-z_]\w*)<(.*)>", t)
        if m:
            name, args = m.group(1), _split_top(m.group(2))
            return self._template(name, args, t, depth)
        words = t.split(" ")
        if all(w in _INT_WORDS for w in words):
            if self.define_int_ll:
                words = [w2 for w in words for w2 in (["long", "long"] if w == "int" else [w])]
            return self._builtin(words, t)
        if t in _TYPE_SIZES:
            s, a = _TYPE_SIZES[t]
            return TInfo(s, a, t)
        if t in ("string", "basic_string<char>"):
            return TInfo(32, 8, "string", kind="string")
        if t in self.structs:
            return self.structs[t]
        if t in ("ll", "LL"):
            return TInfo(8, 8, "long long")
        if t in ("ull", "ULL"):
            return TInfo(8, 8, "unsigned long long")
        if t in ("ld",):
            return TInfo(16, 16, "long double")
        if t in ("pii",):
            return TInfo(8, 4, "pair<int,int>")
        if t in ("pll",):
            return TInfo(16, 8, "pair<long long,long long>")
        if t in ("vi",):
            return self.info("vector<int>", depth + 1)
        if t in ("vll", "vl"):
            return self.info("vector<long long>", depth + 1)
        if t in ("vvi",):
            return self.info("vector<vector<int>>", depth + 1)
        return None

    def _builtin(self, words: list[str], t: str) -> TInfo | None:
        n_long = words.count("long")
        uns = "unsigned" in words
        pre = "unsigned " if uns else ""
        if "double" in words:
            return TInfo(16, 16, "long double") if n_long else TInfo(8, 8, "double")
        if "float" in words:
            return TInfo(4, 4, "float")
        if "bool" in words:
            return TInfo(1, 1, "bool")
        if "__int128" in words:
            return TInfo(16, 16, pre + "__int128")
        if "char" in words:
            return TInfo(1, 1, pre + "char")
        if "short" in words:
            return TInfo(2, 2, pre + "short")
        if n_long >= 2:
            return TInfo(8, 8, pre + "long long")
        if n_long == 1:
            return TInfo(8, 8, pre + "long", note="long ma 8 B na Linuksie/macOS, ale tylko 4 B na Windowsie — bezpieczniej long long.")
        return TInfo(4, 4, pre + "int" if uns else "int")

    def _template(self, name: str, args: list[str], full: str, depth: int) -> TInfo | None:
        sub = [self.info(a, depth + 1) for a in args]
        if name == "vector":
            el = sub[0] if sub else None
            if el is None:
                return None
            if el.name == "bool":
                return TInfo(40, 8, "vector<bool>", kind="vector", elem=el, note="vector<bool> pakuje bity: 1 bit na element.")
            return TInfo(24, 8, f"vector<{el.name}>", kind="vector", elem=el)
        if name == "array" and len(args) == 2 and sub[0]:
            k = evaluate(args[1], self.names, cpp=True)
            return TInfo(max(1, k * sub[0].size), sub[0].align, f"array<{sub[0].name},{k}>")
        if name in ("pair", "tuple"):
            if any(x is None for x in sub):
                return None
            s, a = _layout(sub)
            return TInfo(s, a, f"{name}<{','.join(x.name for x in sub)}>")
        if name == "bitset" and args:
            k = evaluate(args[0], self.names, cpp=True)
            return TInfo(max(1, (k + 63) // 64) * 8, 8, f"bitset<{k}>")
        if name == "complex" and sub and sub[0]:
            return TInfo(2 * sub[0].size, sub[0].align, f"complex<{sub[0].name}>")
        if name in _NODE_CONTAINERS:
            val = [x for x in sub[:2] if x is not None]
            vs, va = _layout(val) if val else (8, 8)
            per = 32 + _align(vs, 8)
            return TInfo(48, 8, full, kind="node", per_elem=per)
        if name in _HASH_CONTAINERS:
            val = [x for x in sub[:2] if x is not None]
            vs, va = _layout(val) if val else (8, 8)
            per = 8 + _align(vs, 8) + 8 + 8
            return TInfo(56, 8, full, kind="node", per_elem=per)
        if name in ("deque", "queue", "stack"):
            el = sub[0] if sub else None
            return TInfo(80, 8, full, kind="deque", elem=el, per_elem=el.size if el else None)
        if name == "priority_queue":
            el = sub[0] if sub else None
            return TInfo(32, 8, full, kind="deque", elem=el, per_elem=el.size if el else None)
        if name == "list":
            el = sub[0] if sub else None
            return TInfo(24, 8, full, kind="node", per_elem=16 + _align(el.size if el else 8, 8))
        if name in ("unique_ptr",):
            return TInfo(8, 8, full)
        if name in ("shared_ptr", "function"):
            return TInfo(16 if name == "shared_ptr" else 32, 8, full)
        if name == "optional" and sub and sub[0]:
            s, a = _layout([sub[0], TInfo(1, 1, "bool")])
            return TInfo(s, a, full)
        if name in ("tree", "basic_string"):
            return TInfo(48, 8, full, kind="node", per_elem=48)
        return None


_STR_RE = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')


def _strip_comments(code: str) -> str:
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            j = code.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", code[i:j]))
            i = j
        elif c in "\"'":
            m = _STR_RE.match(code, i)
            if m:
                s = m.group(0)
                out.append(s[0] + "_" * (len(s) - 2) + s[-1] if len(s) >= 2 else s)
                i = m.end()
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _match_brace(code: str, i: int) -> int:
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(code) - 1


def _classify_brace(head: str, in_func: bool) -> tuple[str, str | None]:
    h = head.strip()
    m = re.match(r"^(?:template\s*<.*>\s*)?(?:typedef\s+)?(struct|class|union)\b\s*([A-Za-z_]\w*)?[^()=]*$", h, re.S)
    if m:
        return "struct", m.group(2)
    if re.match(r"^(namespace\b|extern\s*\"_*\")", h):
        return "ns", None
    if re.match(r"^enum\b", h):
        return "skip", None
    if not h:
        return ("block", None) if in_func else ("skip", None)
    if re.search(r"(\)|\belse|\bdo|\btry|\bconst|\bnoexcept|\boverride|\bfinal|->\s*[\w:<>,\s*&]+)\s*$", h):
        if "=" in h and not re.match(r"^(if|for|while|switch|else)\b", h) and re.search(r"=\s*\[", h):
            return "skip", None             # lambda: auto f = [&](...) {...};
        return ("block" if in_func else "func"), None
    return "init", None                      # inicjalizacja klamrowa: int a[3] = {..}, vector<int> v{..}


def _scan(code: str) -> list[tuple[str, int, str]]:
    """Dzieli kod na instrukcje: (tekst, numer_linii, zakres) — zakres: global | local | struct:<nazwa> | endstruct:<nazwa>.
    Deklaracje tuż po definicji struktury (`struct A {...} t[N];`) dostają nazwę typu na początku."""
    out: list[tuple[str, int, str]] = []
    stack: list[tuple[str, str | None, bool]] = []
    start = i = paren = 0
    n = len(code)
    pending: tuple[str, bool] | None = None
    anon = 0

    def cur_scope() -> str:
        for kind, name, _ in reversed(stack):
            if kind == "ns":
                continue
            if kind == "struct":
                return "struct:" + (name or "")
            return "local"
        return "global"

    def line_of(pos: int) -> int:
        while pos < n and code[pos].isspace():
            pos += 1
        return code.count("\n", 0, pos) + 1

    while i < n:
        c = code[i]
        if c == "(":
            paren += 1
        elif c == ")":
            paren = max(0, paren - 1)
        elif c == "{" and paren == 0:
            head = code[start:i]
            in_func = any(k in ("func", "block") for k, _, _ in stack)
            kind, name = _classify_brace(head, in_func)
            if kind in ("init", "skip"):
                i = _match_brace(code, i) + 1
                continue
            is_td = False
            if kind == "struct":
                if not name:
                    anon += 1
                    name = f"__anon{anon}"
                is_td = bool(re.match(r"^\s*(?:template\s*<.*>\s*)?typedef\b", head, re.S))
            pending = None
            stack.append((kind, name, is_td))
            start = i + 1
        elif c == "}" and paren == 0:
            pending = None
            if stack:
                kind, name, is_td = stack.pop()
                if kind == "struct":
                    out.append(("", line_of(i), "endstruct:" + (name or "")))
                    pending = (name or "", is_td)
            start = i + 1
        elif c == ";" and paren == 0:
            stmt = code[start:i]
            if pending is not None:
                nm, td = pending
                pending = None
                if stmt.strip():
                    stmt = (f"typedef {nm} " if td else f"{nm} ") + stmt.strip()
            if stmt.strip():
                out.append((stmt, line_of(start), cur_scope()))
            start = i + 1
        i += 1
    if code[start:].strip() and not stack:
        out.append((code[start:], line_of(start), "global"))
    return out


_DECL_HEAD = re.compile(
    r"^\s*((?:(?:static|const|constexpr|inline|volatile|mutable|register|thread_local|extern|constinit|struct|class|alignas\s*\([^)]*\))\s+)*)"
    r"((?:(?:unsigned|signed|short|long|int|char|double|float|bool|__int128)\b\s*)+|(?:std\s*::\s*|__gnu_pbds\s*::\s*)?[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*)")


def _read_type(s: str) -> tuple[str, str, str] | None:
    """Rozbija instrukcję na (kwalifikatory, typ, reszta). Typ może mieć szablon <...> i * / &."""
    m = _DECL_HEAD.match(s)
    if not m:
        return None
    quals, typ = m.group(1) or "", m.group(2)
    pos = m.end()
    rest = s[pos:]
    if rest.lstrip().startswith("<"):
        j = pos + (len(rest) - len(rest.lstrip()))
        depth = 0
        k = j
        while k < len(s):
            if s[k] == "<":
                depth += 1
            elif s[k] == ">":
                depth -= 1
                if depth == 0:
                    break
            elif s[k] in ";{}":
                return None
            k += 1
        if depth != 0:
            return None
        typ = typ + s[j:k + 1]
        pos = k + 1
        rest = s[pos:]
        # zagnieżdżony typ: vector<int>::iterator — pomijamy
        if rest.lstrip().startswith("::"):
            return None
    while True:
        r2 = rest.lstrip()
        if r2[:1] in ("*", "&"):
            typ += r2[0]
            rest = r2[1:]
        elif re.match(r"(const|volatile)\b", r2):
            rest = r2[re.match(r"(const|volatile)\b", r2).end():]
        else:
            break
    return quals, typ.strip(), rest


def _parse_declarators(rest: str) -> list[dict] | None:
    out = []
    for part in _split_top_paren(rest):
        m = re.match(r"^\s*([*&]*)\s*([A-Za-z_]\w*)\s*((?:\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]\s*)*)(.*)$", part, re.S)
        if not m:
            return None
        ptr, name, dims_s, tail = m.groups()
        if name in _CPP_KEYWORDS:
            return None
        dims = re.findall(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]", dims_s or "")
        tail = tail.strip()
        ctor = None
        init = None
        if tail.startswith("("):
            if not tail.endswith(")"):
                return None
            ctor = tail[1:-1]
        elif tail.startswith("{"):
            init = tail
        elif tail.startswith("="):
            init = tail[1:].strip()
        elif tail.startswith(":"):     # pole bitowe w strukturze
            init = None
        elif tail:
            return None
        out.append({"ptr": ptr, "name": name, "dims": dims, "ctor": ctor, "init": init})
    return out


_TYPEISH = {"int", "long", "short", "char", "bool", "double", "float", "void", "auto", "unsigned", "signed", "string",
            "vector", "pair", "array", "set", "map", "const", "ll", "ull", "ld", "pii", "pll", "size_t", "__int128",
            "int64_t", "uint64_t", "int32_t", "uint32_t", "deque", "queue", "stack", "bitset", "tuple", "unordered_map",
            "unordered_set", "multiset", "multimap", "priority_queue", "function", "struct", "volatile", "std"}


def _looks_like_prototype(ctor: str, ctx: "_MemCtx") -> bool:
    """`int f(int a, int b);` albo `T x();` to deklaracje funkcji, a nie obiekty z konstruktorem."""
    c = ctor.strip()
    if not c:
        return True
    m = re.match(r"([A-Za-z_]\w*)", c)
    if not m:
        return False
    w = m.group(1)
    return w in _TYPEISH or w in ctx.aliases or w in ctx.structs


def _count_init(init: str) -> int | None:
    s = init.strip()
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        return len(_split_top_paren(inner)) if inner else 0
    if s.startswith('"'):
        return len(s) - 1
    return None


def _fmt_bytes(b: int | float) -> str:
    if b < 1024:
        return f"{int(b)} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.2f} MB"
    return f"{b / 1024 ** 3:.2f} GB"


def _safe_info(ctx: "_MemCtx", t: str) -> TInfo | None:
    try:
        return ctx.info(t)
    except ValueError:
        return None


def _vector_heap(ctx: _MemCtx, ti: TInfo, args: list[str], warn: list[str]) -> int | None:
    """Pamięć na stercie dla vector<T> v(args...)."""
    if not args:
        return 0
    try:
        cnt = evaluate(args[0], ctx.names, cpp=True)
    except ValueError:
        return None
    if cnt < 0:
        raise ValueError("Ujemny rozmiar wektora.")
    el = ti.elem
    if el is None:
        return None
    if el.name == "bool":
        return (cnt + 63) // 64 * 8
    inner = 0
    if el.kind == "vector" and len(args) >= 2:
        mm = re.match(r"^\s*(?:std\s*::\s*)?vector\s*<(.*)>\s*\((.*)\)\s*$", args[1], re.S)
        if mm:
            inner_ti = ctx.info(f"vector<{mm.group(1)}>")
            if inner_ti:
                ih = _vector_heap(ctx, inner_ti, _split_top_paren(mm.group(2)), warn)
                if ih is None:
                    return None
                inner = ih
                ic = evaluate(_split_top_paren(mm.group(2))[0], ctx.names, cpp=True) if mm.group(2).strip() else 0
                if cnt >= 1000 and ic <= 8:
                    warn.append(f"Wektor wektorów z małymi wewnętrznymi wektorami: każdy wewnętrzny to dodatkowe 24 B "
                                f"nagłówka + osobna alokacja. Rozważ zwykłą tablicę 2D albo spłaszczenie do jednego wektora.")
    elif el.kind == "string" and len(args) >= 2:
        mm = re.match(r"^\s*(?:std\s*::\s*)?string\s*\((.*)\)\s*$", args[1], re.S)
        if mm:
            try:
                ln = evaluate(_split_top_paren(mm.group(1))[0], ctx.names, cpp=True)
                inner = ln + 1 if ln > 15 else 0
            except ValueError:
                return None
    return cnt * (el.size + inner)


def memory_report(decls: str, limit_mb: float = 256, vars_text: str = "") -> dict:
    if not str(decls or "").strip():
        raise ValueError("Wklej deklaracje, np. int a[1000000]; long long dp[5005][5005];")
    if len(decls) > 200_000:
        raise ValueError("Za długi kod (maks. 200 000 znaków).")
    names: dict[str, int] = {}
    for part in re.split(r"[,;\n]+", vars_text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            if re.fullmatch(r"[A-Za-z_]\w*", k):
                try:
                    names[k] = evaluate(v, names)
                except ValueError as e:
                    raise ValueError(f"Zmienna {k}: {e}")
    ctx = _MemCtx(names)
    code = str(decls).replace("\r\n", "\n").replace("\r", "\n")
    consts = []
    global_notes: list[dict] = []
    # --- preprocesor
    lines = code.split("\n")
    for idx, ln in enumerate(lines):
        st = ln.strip()
        if not st.startswith("#"):
            continue
        m = re.match(r"#\s*define\s+([A-Za-z_]\w*)\s+(.+?)\s*(?://.*)?$", st)
        if m:
            nm, val = m.group(1), m.group(2).strip()
            if nm == "int" and re.fullmatch(r"long\s+long", val):
                ctx.define_int_ll = True
                global_notes.append({"tone": "warn", "line": idx + 1, "text": "`#define int long long` — każdy `int` ma teraz 8 B, więc tablice `int` zajmują 2× więcej pamięci."})
            elif _safe_info(ctx, val) is not None and not re.fullmatch(r"[\d\s+\-*/()<>^xXa-fA-F'eE.]+", val):
                ctx.aliases[nm] = val
            else:
                try:
                    nts: list[str] = []
                    ctx.names[nm] = evaluate(val, ctx.names, cpp=True, notes=nts)
                    consts.append({"name": nm, "value": str(ctx.names[nm]), "line": idx + 1, "expr": val})
                    for t in nts:
                        global_notes.append({"tone": "danger", "line": idx + 1, "text": f"{nm}: {t}"})
                except ValueError:
                    pass
        lines[idx] = " " * len(ln)
    code = _strip_comments("\n".join(lines))
    items = []
    by_name: dict[str, dict] = {}
    struct_fields: dict[str, list[TInfo]] = {}

    for stmt, line, sc in _scan(code):
        s = stmt.strip()
        if sc.startswith("endstruct:"):
            nm = sc[len("endstruct:"):]
            flds = struct_fields.pop(nm, [])
            size, al = _layout(flds) if flds else (1, 1)
            ctx.structs[nm] = TInfo(size, al, nm, note=f"struct {nm}: {size} B (z wyrównaniem do {al} B)")
            continue
        if not s:
            continue
        # aliasy typów
        m = re.match(r"^typedef\s+(.+?)\s+([A-Za-z_]\w*)$", s, re.S)
        if m:
            ctx.aliases[m.group(2)] = m.group(1)
            continue
        m = re.match(r"^using\s+([A-Za-z_]\w*)\s*=\s*(.+)$", s, re.S)
        if m:
            ctx.aliases[m.group(1)] = m.group(2)
            continue
        if re.match(r"^(using|return|template|friend|public|private|protected|#)\b", s):
            continue
        storage_scope = "global"
        struct_owner = None
        if sc.startswith("struct:"):
            struct_owner = sc[len("struct:"):]
        elif sc == "local":
            storage_scope = "local"
        m = re.match(r"^([A-Za-z_]\w*)\s*\.\s*(resize|assign|reserve)\s*\((.*)\)$", s, re.S)
        rt = None if m else _read_type(s)
        if not rt:
            # wywołania resize/assign na znanych wektorach
            if m and m.group(1) in by_name and by_name[m.group(1)].get("_ti") is not None:
                it = by_name[m.group(1)]
                w: list[str] = []
                try:
                    hp = _vector_heap(ctx, it["_ti"], _split_top_paren(m.group(3)), w)
                except ValueError:
                    hp = None
                if hp is not None and not it["dims"]:
                    it["heap"] = max(it["heap"], hp)
                    it["notes"].append(f"Linia {line}: {m.group(1)}.{m.group(2)}(…) → {_fmt_bytes(hp)} na stercie.")
            continue
        quals, typ, rest = rt
        is_static = "static" in quals
        is_const = "const" in quals or "constexpr" in quals
        if typ.split("::")[-1].strip() in _CPP_KEYWORDS or typ in ("return", "delete", "new", "goto", "case"):
            continue
        decls_ = _parse_declarators(rest)
        if not decls_:
            continue
        ti = None
        try:
            ti = ctx.info(typ)
        except ValueError:
            ti = None
        for d in decls_:
            nm = d["name"]
            if d["ctor"] is not None and _looks_like_prototype(d["ctor"], ctx):
                continue
            # stałe / znane zmienne skalarne
            if (not d["dims"] and d["init"] is not None and ti is not None and ti.kind == "plain" and ti.size <= 16
                    and (is_const or (storage_scope == "global" and struct_owner is None))):
                try:
                    nts = []
                    val = evaluate(d["init"], ctx.names, cpp=True, notes=nts)
                    ctx.names[nm] = val
                    if is_const or storage_scope == "global":
                        consts.append({"name": nm, "value": str(val), "line": line, "expr": d["init"], "const": is_const})
                    for t in nts:
                        global_notes.append({"tone": "danger", "line": line, "text": f"{nm}: {t}"})
                except ValueError:
                    pass
            if struct_owner is not None:
                if is_static:
                    continue
                if ti is None:
                    global_notes.append({"tone": "warn", "line": line, "text": f"Nieznany typ pola „{typ}” w strukturze — pominięto w rozmiarze."})
                    continue
                cnt = 1
                ok = True
                for dim in d["dims"]:
                    try:
                        cnt *= evaluate(dim, ctx.names, cpp=True)
                    except ValueError:
                        ok = False
                if ok and not d["ptr"]:
                    struct_fields.setdefault(struct_owner, []).append(TInfo(ti.size * cnt, ti.align, ti.name))
                elif d["ptr"]:
                    struct_fields.setdefault(struct_owner, []).append(TInfo(8, 8, "*"))
                continue
            elem = TInfo(8, 8, typ + d["ptr"]) if d["ptr"] else ti
            interesting = bool(d["dims"]) or (elem is not None and (elem.kind != "plain" or elem.size >= 64))
            if not interesting:
                continue
            warns: list[str] = []
            notes: list[str] = []
            item = {"line": line, "name": nm, "text": re.sub(r"\s+", " ", s)[:160], "type": elem.name if elem else typ,
                    "dims": [], "count": None, "elem_bytes": elem.size if elem else None, "bytes": 0, "heap": 0,
                    "storage": "global" if (storage_scope == "global" or is_static) else "stack", "unknown": False,
                    "warnings": warns, "notes": notes}
            if elem is None:
                item["unknown"] = True
                warns.append(f"Nieznany typ „{typ}” — nie umiem policzyć rozmiaru (zdefiniuj go wyżej albo użyj typów wbudowanych).")
                items.append(item)
                continue
            cnt = 1
            vla = False
            dims_out = []
            for k, dim in enumerate(d["dims"]):
                if not dim.strip():
                    c = _count_init(d["init"] or "") if k == 0 else None
                    if c is None:
                        item["unknown"] = True
                        warns.append("Tablica bez rozmiaru — nie umiem go ustalić.")
                        break
                    dims_out.append(str(c))
                    cnt *= c
                    continue
                nts = []
                try:
                    v = evaluate(dim, ctx.names, cpp=True, notes=nts)
                except ValueError as e:
                    unknown = re.findall(r"„(\w+)”", str(e))
                    item["unknown"] = True
                    vla = True
                    warns.append(f"Nie znam wartości {('`' + unknown[0] + '`') if unknown else 'wymiaru'} w `[{dim.strip()}]` — "
                                 f"podaj ją w polu „Zmienne” (np. {unknown[0] if unknown else 'n'} = 200000).")
                    break
                for t in nts:
                    warns.append(t)
                if v <= 0:
                    warns.append(f"Wymiar [{dim.strip()}] = {v} — rozmiar tablicy musi być dodatni.")
                dims_out.append(str(v))
                cnt *= max(v, 0)
            item["dims"] = dims_out
            if item["unknown"]:
                if vla and storage_scope == "local" and not is_static:
                    warns.append("Tablica o zmiennej długości (VLA) na stosie — to rozszerzenie GCC, nie standard C++. Lepiej `vector`.")
                items.append(item)
                by_name[nm] = item
                continue
            item["count"] = cnt
            item["bytes"] = cnt * elem.size
            if elem.note:
                notes.append(elem.note)
            # sterta: wektory z rozmiarem w konstruktorze
            if elem.kind == "vector":
                item["_ti"] = elem
                if d["ctor"] is not None and not d["dims"]:
                    try:
                        hp = _vector_heap(ctx, elem, _split_top_paren(d["ctor"]), warns)
                    except ValueError as e:
                        hp = None
                        warns.append(str(e))
                    if hp is None:
                        item["unknown"] = True
                        warns.append("Nie znam rozmiaru wektora — podaj wartości zmiennych w polu „Zmienne”.")
                    else:
                        item["heap"] = hp
                        if elem.elem and elem.elem.kind == "vector" and len(_split_top_paren(d["ctor"])) < 2:
                            notes.append("Wewnętrzne wektory są puste — ich elementy dojdą osobno.")
                elif d["init"] and d["init"].strip().startswith("{") and not d["dims"]:
                    c = _count_init(d["init"])
                    if c and elem.elem:
                        item["heap"] = c * elem.elem.size
                elif d["dims"]:
                    notes.append(f"Liczę tylko {cnt} pustych wektorów (po 24 B). Elementy dodane przez push_back to dodatkowo "
                                 f"{elem.elem.size if elem.elem else '?'} B na element (+ zapas pojemności do 2×).")
                else:
                    notes.append("Pusty wektor (24 B). Elementy dodane przez push_back to dodatkowo "
                                 f"{elem.elem.size if elem.elem else '?'} B na element (+ zapas pojemności do 2×).")
                if item["heap"] and not d["dims"] and "push_back" not in notes:
                    pass
            elif elem.kind == "node":
                notes.append(f"Rozmiar zależy od liczby elementów: ~{elem.per_elem} B na element (węzeł + wskaźniki)."
                             if elem.per_elem else "Rozmiar zależy od liczby elementów.")
            elif elem.kind == "deque":
                notes.append(f"Rozmiar zależy od liczby elementów: ~{elem.per_elem or '?'} B na element (+ bloki pamięci).")
            elif elem.kind == "string":
                if d["ctor"] is not None:
                    try:
                        ln = evaluate(_split_top_paren(d["ctor"])[0], ctx.names, cpp=True)
                        item["heap"] = (ln + 1) * cnt if ln > 15 else 0
                    except ValueError:
                        pass
                notes.append("string: 32 B nagłówka; tekst dłuższy niż 15 znaków leży na stercie.")
            if elem.name == "bool" and d["dims"] and cnt >= 10 ** 6:
                notes.append(f"Wskazówka: bitset<{cnt}> zajmie {_fmt_bytes((cnt + 63) // 64 * 8)} zamiast {_fmt_bytes(cnt)}.")
            if elem.name == "long double":
                notes.append("long double: 16 B na x86-64 Linux (jak na OI), 8 B na Windows/MSVC i Macach z ARM.")
            if item["storage"] == "stack":
                if item["bytes"] >= 8 * 1024 ** 2:
                    warns.append(f"Tablica lokalna {_fmt_bytes(item['bytes'])} na STOSIE — domyślny stos ma często 8 MB (Windows: 1 MB). "
                                 f"Przenieś ją przed main() (globalnie) albo użyj vector.")
                elif item["bytes"] >= 1024 ** 2:
                    warns.append(f"Duża tablica lokalna ({_fmt_bytes(item['bytes'])}) na stosie — bezpieczniej globalnie albo jako vector.")
                if d["dims"] and d["init"] is None:
                    notes.append("Tablica lokalna NIE jest zerowana (zawiera śmieci) — użyj `= {}` albo zmiennej globalnej.")
            items.append(item)
            by_name[nm] = item

    limit_b = float(limit_mb) * 1024 ** 2
    total_static = sum(i["bytes"] for i in items if i["storage"] == "global")
    total_stack = sum(i["bytes"] for i in items if i["storage"] == "stack")
    total_heap = sum(i["heap"] for i in items)
    total = total_static + total_stack + total_heap
    for it in items:
        it.pop("_ti", None)
        b = it["bytes"] + it["heap"]
        it["total"] = b
        it["human"] = _fmt_bytes(b)
        it["mb"] = round(b / 1024 ** 2, 3)
        it["share"] = round(b / limit_b, 4) if limit_b else 0
    pct = total / limit_b if limit_b else 0
    verdict = "ok" if pct <= 0.8 else ("risky" if pct <= 1.0 else "mle")
    if verdict == "mle":
        global_notes.insert(0, {"tone": "danger", "line": None, "text": f"Suma {_fmt_bytes(total)} przekracza limit {limit_mb:g} MB — MLE (albo błąd kompilacji/linkowania przy ogromnych tablicach)."})
    elif verdict == "risky":
        global_notes.insert(0, {"tone": "warn", "line": None, "text": f"Zajętość {pct * 100:.0f}% limitu — doliczy się jeszcze stos rekurencji, bufory wejścia i zawartość kontenerów."})
    if any(i["unknown"] for i in items):
        global_notes.append({"tone": "info", "line": None, "text": "Część deklaracji ma nieznany rozmiar — suma jest zaniżona."})
    return {"items": items, "consts": consts, "notes": global_notes, "total": total, "total_human": _fmt_bytes(total),
            "total_mb": round(total / 1024 ** 2, 3), "static": total_static, "stack": total_stack, "heap": total_heap,
            "static_human": _fmt_bytes(total_static), "stack_human": _fmt_bytes(total_stack), "heap_human": _fmt_bytes(total_heap),
            "limit_mb": limit_mb, "percent": round(pct * 100, 2), "verdict": verdict,
            "aliases": {k: v for k, v in ctx.aliases.items()}, "structs": {k: v.size for k, v in ctx.structs.items()}}


# ============================================================================================ HTTP
def _num(body: dict, key: str, label: str | None = None, required: bool = True, default=None,
         lo: int | None = None, hi: int | None = None) -> int | None:
    raw = body.get(key)
    label = label or key
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise ApiError(f"Podaj {label}.")
        return default
    try:
        v = evaluate(raw)
    except ValueError as e:
        raise ApiError(f"{label}: {e}")
    if lo is not None and v < lo:
        raise ApiError(f"{label} musi być ≥ {lo}.")
    if hi is not None and v > hi:
        raise ApiError(f"{label} musi być ≤ {fmt_int(hi)}.")
    return v


def _guard(fn):
    def wrapped(body):
        body = body if isinstance(body, dict) else {}
        for v in body.values():
            if isinstance(v, str) and len(v) > 200_000:
                raise ApiError("Za długie dane wejściowe.")
        dl = Deadline(TIME_LIMIT)
        t0 = time.perf_counter()
        try:
            res = fn(body, dl)
        except CalcTimeout:
            raise ApiError(f"Obliczenia przekroczyły limit czasu ({TIME_LIMIT:g} s). Spróbuj mniejszych liczb.", 408)
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            raise ApiError(str(e) or "Niepoprawne dane.")
        res.setdefault("ok", True)
        res["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return res
    wrapped.__name__ = fn.__name__
    return wrapped


def _s(x: int | None) -> str | None:
    return None if x is None else fmt_int(x)


@_guard
def handle_eval(b, dl):
    v = _num(b, "expr", "wyrażenie")
    return {"value": fmt_int(v), "sci": sci(v), "bits": v.bit_length(), "hex": to_base(v, 16) if v.bit_length() <= 4096 else None,
            "digits": len(str(abs(v))) if v.bit_length() <= MAX_BITS else None,
            "fits": {"int": -2 ** 31 <= v < 2 ** 31, "long long": -2 ** 63 <= v < 2 ** 63, "unsigned long long": 0 <= v < 2 ** 64,
                     "__int128": -2 ** 127 <= v < 2 ** 127}}


@_guard
def handle_modular(b, dl):
    m = _num(b, "m", "moduł m", lo=1)
    a = _num(b, "a", "a")
    bb = _num(b, "b", "b", required=False)
    an = a % m
    out = {"m": fmt_int(m), "a": fmt_int(an), "m_prime": is_prime(m) if m.bit_length() <= 4096 else None,
           "a_raw_negative": a < 0}
    inv_a = mod_inverse(a, m)
    out["inv_a"] = _s(inv_a)
    out["gcd_a"] = fmt_int(math.gcd(a, m))
    if bb is not None:
        bn = bb % m
        out["b"] = fmt_int(bn)
        out["sum"] = fmt_int((a + bb) % m)
        out["diff"] = fmt_int((a - bb) % m)
        out["prod"] = fmt_int(a * bb % m)
        inv_b = mod_inverse(bb, m)
        out["inv_b"] = _s(inv_b)
        out["gcd_b"] = fmt_int(math.gcd(bb, m))
        out["quot"] = _s(a * inv_b % m if inv_b is not None else None)
        if bb >= 0:
            out["pow"] = fmt_int(pow(a, bb, m))
        elif inv_a is not None:
            out["pow"] = fmt_int(pow(inv_a, -bb, m))
        else:
            out["pow"] = None
    hints = []
    if (m - 1) ** 2 >= 2 ** 63:
        hints.append("(m−1)² nie mieści się w long long — iloczyn licz przez `__int128` albo mnożenie „po bitach”.")
    elif (m - 1) ** 2 >= 2 ** 31:
        hints.append("(m−1)² nie mieści się w int — przed mnożeniem rzutuj na long long: `1LL * a * b % m`.")
    if a < 0:
        hints.append("W C++ `a % m` dla ujemnego a jest ujemne — normalizuj: `((a % m) + m) % m`.")
    if out.get("m_prime") is False:
        hints.append("m nie jest pierwsze — odwrotność istnieje tylko dla liczb względnie pierwszych z m (licz rozszerzonym Euklidesem, nie Fermatem).")
    out["hints"] = hints
    return out


@_guard
def handle_pow(b, dl):
    a = _num(b, "a", "podstawa a")
    e = _num(b, "e", "wykładnik e")
    m = _num(b, "m", "moduł m", required=False, lo=1)
    if m is None:
        if e < 0:
            raise ApiError("Ujemny wykładnik bez modułu nie daje liczby całkowitej.")
        if abs(a) > 1 and abs(a).bit_length() * e > MAX_BITS * 4:
            raise ApiError("Wynik byłby gigantyczny — podaj moduł m.")
        return {"value": fmt_int(a ** e), "steps": []}
    if e < 0:
        inv = mod_inverse(a, m)
        if inv is None:
            raise ApiError("Ujemny wykładnik wymaga odwrotności a, a NWD(a, m) ≠ 1.")
        a, e = inv, -e
    steps = []
    if e.bit_length() <= 64:
        base, ee, res = a % m, e, 1 % m
        while ee:
            bit = ee & 1
            if bit:
                res = res * base % m
            steps.append({"bit": bit, "base": fmt_int(base), "res": fmt_int(res)})
            base = base * base % m
            ee >>= 1
    return {"value": fmt_int(pow(a, e, m)), "steps": steps}


@_guard
def handle_inverse(b, dl):
    a = _num(b, "a", "a")
    m = _num(b, "m", "moduł m", lo=1)
    inv = mod_inverse(a, m)
    g = math.gcd(a, m)
    if inv is None:
        raise ApiError(f"Odwrotność nie istnieje: NWD({fmt_int(a)}, {fmt_int(m)}) = {fmt_int(g)} ≠ 1.")
    method = "małe tw. Fermata: a^(m−2) mod m" if is_prime(m) else "rozszerzony algorytm Euklidesa"
    return {"value": fmt_int(inv), "method": method, "check": fmt_int(a * inv % m)}


@_guard
def handle_ncr(b, dl):
    n = _num(b, "n", "n", lo=0)
    k = _num(b, "k", "k", lo=0)
    p = _num(b, "p", "moduł", required=False, lo=1)
    if p is None:
        if n > 200_000:
            raise ApiError("Bez modułu liczę dokładnie tylko dla n ≤ 200 000.")
        exact = math.comb(n, k) if k <= n else 0
        return {"value": fmt_int(exact), "method": "dokładnie (math.comb)", "exact": fmt_int(exact), "digits": len(str(exact)) if exact.bit_length() <= MAX_BITS else None}
    v, method, exact = ncr_mod(n, k, p, dl)
    out = {"value": fmt_int(v), "method": method}
    if exact is not None:
        out["exact"] = fmt_int(exact)
        out["digits"] = len(str(exact)) if exact.bit_length() <= MAX_BITS else int(exact.bit_length() * LOG10_2) + 1
    if p and n >= p and is_prime(p) and n <= 200_000:
        out["note"] = "Uwaga: n ≥ p, więc liczenie przez silnie i odwrotności modulo p dałoby błąd (dzielenie przez 0 mod p) — potrzebny Lucas."
    return out


def _factor_payload(n: int, dl: Deadline, list_limit: int = 5000) -> dict:
    fac, rest = factorize(n, dl)
    an = abs(n)
    out = {"n": fmt_int(n), "factors": [{"p": fmt_int(p), "e": e} for p, e in fac.items()],
           "pretty": ("-" if n < 0 else "") + fmt_factorization(fac, rest), "incomplete": [fmt_int(r) for r in rest],
           "is_prime": is_prime(an) if an.bit_length() <= 4096 else None, "certainty": primality_kind(an)}
    if not rest and an > 0:
        out["divisor_count"] = fmt_int(divisor_count(fac))
        out["divisor_sum"] = fmt_int(divisor_sum(fac))
        out["phi"] = fmt_int(euler_phi(fac))
        ds = divisors_list(fac, list_limit)
        out["divisors"] = [fmt_int(d) for d in ds] if ds is not None else None
        out["divisors_truncated"] = ds is None
        out["squarefree"] = all(e == 1 for e in fac.values())
        out["perfect_square"] = all(e % 2 == 0 for e in fac.values())
    return out


@_guard
def handle_factorize(b, dl):
    n = _num(b, "n", "n")
    if n == 0:
        raise ApiError("0 nie ma rozkładu na czynniki pierwsze (dzieli się przez wszystko).")
    if abs(n).bit_length() > 400:
        raise ApiError("Za duża liczba do faktoryzacji (maks. ~120 cyfr).")
    return _factor_payload(n, dl)


@_guard
def handle_prime(b, dl):
    n = _num(b, "n", "n")
    out = {"n": fmt_int(n), "is_prime": is_prime(n), "certainty": primality_kind(abs(n))}
    if abs(n).bit_length() <= 2048:
        out["next"] = fmt_int(next_prime(n, dl))
        pp = prev_prime(n, dl) if n > 2 else None
        out["prev"] = _s(pp)
        if not out["is_prime"] and n > 1 and n.bit_length() <= 400:
            fac, rest = factorize(n, dl)
            out["smallest_factor"] = fmt_int(min(list(fac) + rest)) if (fac or rest) else None
    return out


@_guard
def handle_divisors(b, dl):
    n = _num(b, "n", "n", lo=1)
    if n.bit_length() > 400:
        raise ApiError("Za duża liczba (maks. ~120 cyfr).")
    lim = _num(b, "limit", "limit", required=False, default=5000, lo=1, hi=200000)
    return _factor_payload(n, dl, lim)


@_guard
def handle_phi(b, dl):
    n = _num(b, "n", "n", lo=1)
    if n.bit_length() > 400:
        raise ApiError("Za duża liczba (maks. ~120 cyfr).")
    fac, rest = factorize(n, dl)
    if rest:
        raise ApiError("Nie udało się rozłożyć liczby w limicie czasu.")
    return {"n": fmt_int(n), "phi": fmt_int(euler_phi(fac)), "pretty": fmt_factorization(fac),
            "formula": "φ(n) = n · Π (1 − 1/p) = " + " · ".join(f"({p}−1)·{p}^{e - 1}" if e > 1 else f"({p}−1)" for p, e in fac.items())}


@_guard
def handle_gcd(b, dl):
    nums = b.get("numbers")
    if isinstance(nums, str):
        nums = [x for x in re.split(r"[\s,;]+", nums) if x]
    if isinstance(nums, list) and nums:
        vals = [evaluate(x) for x in nums][:1000]
        if len(vals) < 2:
            raise ApiError("Podaj co najmniej dwie liczby.")
    else:
        vals = [_num(b, "a", "a"), _num(b, "b", "b")]
    g = 0
    l_ = 1
    for v in vals:
        g = math.gcd(g, v)
        l_ = math.lcm(l_, v) if v else 0
    a, bb = vals[0], vals[1]
    g2, x, y = ext_gcd(a, bb)
    out = {"gcd": fmt_int(g), "lcm": fmt_int(l_), "a": fmt_int(a), "b": fmt_int(bb), "x": fmt_int(x), "y": fmt_int(y),
           "check": f"{fmt_int(a)}·({fmt_int(x)}) + {fmt_int(bb)}·({fmt_int(y)}) = {fmt_int(a * x + bb * y)}",
           "steps": ext_gcd_steps(a, bb) if max(abs(a), abs(bb)).bit_length() <= 256 else [], "count": len(vals)}
    if g2 and bb:
        step = abs(bb // g2)
        x0 = x % step if step else x
        out["x_min_pos"] = fmt_int(x0)
        out["general"] = f"x = {fmt_int(x)} + k·{fmt_int(bb // g2)},  y = {fmt_int(y)} − k·{fmt_int(a // g2)}  (k ∈ ℤ)"
    out["lcm_overflow_ll"] = l_ >= 2 ** 63
    return out


@_guard
def handle_crt(b, dl):
    raw = b.get("congruences") or b.get("pairs") or []
    if not isinstance(raw, list) or not raw:
        raise ApiError("Podaj układ kongruencji: x ≡ a (mod m).")
    if len(raw) > 64:
        raise ApiError("Maksymalnie 64 kongruencje.")
    pairs = []
    for i, r in enumerate(raw):
        if isinstance(r, dict):
            a, m = r.get("a"), r.get("m")
        elif isinstance(r, (list, tuple)) and len(r) == 2:
            a, m = r
        else:
            raise ApiError(f"Kongruencja {i + 1}: oczekiwano pary (a, m).")
        try:
            av, mv = evaluate(a), evaluate(m)
        except ValueError as e:
            raise ApiError(f"Kongruencja {i + 1}: {e}")
        if mv < 1:
            raise ApiError(f"Kongruencja {i + 1}: moduł musi być ≥ 1.")
        pairs.append((av, mv))
    res = crt(pairs)
    coprime = all(math.gcd(pairs[i][1], pairs[j][1]) == 1 for i in range(len(pairs)) for j in range(i + 1, len(pairs)))
    if res is None:
        bad = None
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                g = math.gcd(pairs[i][1], pairs[j][1])
                if (pairs[i][0] - pairs[j][0]) % g:
                    bad = (i, j, g)
                    break
            if bad:
                break
        msg = "Układ jest sprzeczny — brak rozwiązań."
        if bad:
            i, j, g = bad
            msg += (f" Kongruencje {i + 1} i {j + 1}: NWD({fmt_int(pairs[i][1])}, {fmt_int(pairs[j][1])}) = {fmt_int(g)}, "
                    f"ale {fmt_int(pairs[i][0])} ≢ {fmt_int(pairs[j][0])} (mod {fmt_int(g)}).")
        return {"solvable": False, "message": msg, "coprime": coprime}
    x, M = res
    return {"solvable": True, "x": fmt_int(x), "M": fmt_int(M), "coprime": coprime,
            "general": f"x ≡ {fmt_int(x)} (mod {fmt_int(M)})",
            "checks": [{"a": fmt_int(a % m), "m": fmt_int(m), "ok": x % m == a % m} for a, m in pairs]}


@_guard
def handle_base(b, dl):
    fb = _num(b, "from", "podstawa źródłowa", required=False, default=10, lo=0, hi=36)
    tb = _num(b, "to", "podstawa docelowa", required=False, default=2, lo=2, hi=36)
    if fb == 1:
        raise ApiError("Podstawa musi być z zakresu 2–36.")
    return convert_base(str(b.get("value", "")), fb, tb)


@_guard
def handle_bits(b, dl):
    typ = str(b.get("type") or "int64")
    raw = b.get("value")
    if str(b.get("format")) == "bin" and isinstance(raw, str):
        bits = re.sub(r"[\s_']", "", raw)
        if not re.fullmatch(r"[01]{1,128}", bits):
            raise ApiError("Zapis binarny może zawierać tylko 0 i 1.")
        v = int(bits, 2)
        w, signed, _ = INT_TYPES.get(typ, (64, True, ""))
        if signed and len(bits) == w and bits[0] == "1":
            v -= 1 << w
    else:
        v = _num(b, "value", "liczba")
    if abs(v).bit_length() > 1024:
        raise ApiError("Za duża liczba.")
    return bits_info(v, typ)


@_guard
def handle_double(b, dl):
    return double_info(str(b.get("value", "")))


@_guard
def handle_sequence(b, dl):
    kind = str(b.get("kind") or "fibonacci")
    n = _num(b, "n", "n", lo=0)
    m = _num(b, "m", "moduł m", required=False, lo=1)
    if kind in ("fibonacci", "fib"):
        if m is None:
            if n > 19000:
                raise ApiError("Bez modułu liczę F(n) dokładnie do n = 19 000 (dalej podaj m).")
            v = fib_mod(n)
            return {"value": fmt_int(v), "digits": len(str(v)), "method": "szybkie podwajanie: F(2k) = F(k)·(2F(k+1) − F(k)), F(2k+1) = F(k)² + F(k+1)²"}
        return {"value": fmt_int(fib_mod(n, m)), "method": "szybkie podwajanie modulo m, O(log n)"}
    if kind in ("factorial", "fact"):
        if m is None:
            if n > 1400:
                raise ApiError("Bez modułu liczę n! dokładnie do n = 1400 (dalej podaj m).")
            v = math.factorial(n)
            return {"value": fmt_int(v), "digits": len(str(v)), "method": "dokładnie"}
        v, method = fact_mod(n, m, dl)
        return {"value": fmt_int(v), "method": method}
    raise ApiError("Nieznany ciąg — dostępne: fibonacci, factorial.")


def _strip_constraint(raw: str) -> str:
    """„1 ≤ n ≤ 2·10^5” → „2·10^5”; „n = 10^6” → „10^6”."""
    s = raw.replace("<=", "≤").replace("⩽", "≤")
    if "≤" in s:
        s = s.split("≤")[-1]
    elif re.match(r"^\s*[A-Za-z]\w*\s*=", s):
        s = s.split("=", 1)[1]
    elif re.match(r"^\s*[A-Za-z]\w*\s*<(?!<)", s):
        s = re.split(r"<(?!<)", s, 1)[1]
    return s.strip()


def _constraint_num(b: dict, key: str, label: str, required: bool = True, default=None) -> int | None:
    raw = b.get(key)
    if isinstance(raw, str):
        raw = _strip_constraint(raw)
        if not raw:
            raw = None
    if raw is None:
        if required:
            raise ApiError(f"Podaj {label}.")
        return default
    try:
        v = evaluate(raw)
    except ValueError as e:
        raise ApiError(f"{label}: {e}")
    if v < 0:
        raise ApiError(f"{label} nie może być ujemne.")
    return v


@_guard
def handle_limits(b, dl):
    n = _constraint_num(b, "n", "n")
    if n < 1:
        raise ApiError("n musi być ≥ 1.")
    if n.bit_length() > 1000:
        raise ApiError("n jest absurdalnie duże.")
    q = _constraint_num(b, "q", "q", required=False, default=0)
    m = _constraint_num(b, "m", "m", required=False, default=0)
    try:
        tl = float(re.sub(r"[^\d.e+-]", "", str(b.get("time_limit") or 1).replace(",", ".")) or 1)
        sp = b.get("ops_per_sec") or 2e8
        sp = SPEEDS.get(str(sp)) or float(evaluate(sp) if isinstance(sp, str) else sp)
    except (ValueError, TypeError):
        raise ApiError("Niepoprawny limit czasu albo szybkość.")
    return limits_table(n, q or 0, m or 0, tl, sp)


@_guard
def handle_memory(b, dl):
    try:
        lim = float(str(b.get("limit_mb") or 256).replace(",", "."))
    except ValueError:
        raise ApiError("Niepoprawny limit pamięci.")
    if not (1 <= lim <= 1 << 20):
        raise ApiError("Limit pamięci musi być z zakresu 1 MB – 1 TB.")
    return memory_report(str(b.get("decls") or b.get("code") or ""), lim, str(b.get("vars") or ""))


OPS = {
    "eval": (handle_eval, "Wartość wyrażenia całkowitego {expr}"),
    "modular": (handle_modular, "a±b, a·b, a/b, a^b, odwrotności mod m {a, b, m}"),
    "pow": (handle_pow, "a^e mod m z krokami szybkiego potęgowania {a, e, m?}"),
    "inverse": (handle_inverse, "Odwrotność a mod m {a, m}"),
    "ncr": (handle_ncr, "C(n, k) mod p {n, k, p?}"),
    "factorize": (handle_factorize, "Rozkład na czynniki (Pollard rho) {n}"),
    "prime": (handle_prime, "Test pierwszości, sąsiednie liczby pierwsze {n}"),
    "divisors": (handle_divisors, "Dzielniki: liczba, suma, lista {n, limit?}"),
    "phi": (handle_phi, "Funkcja φ Eulera {n}"),
    "gcd": (handle_gcd, "NWD, NWW, rozszerzony Euklides {a, b} | {numbers}"),
    "crt": (handle_crt, "Chińskie twierdzenie o resztach {congruences: [[a, m], ...]}"),
    "base": (handle_base, "Zamiana systemów 2–36 {value, from, to}"),
    "bits": (handle_bits, "Bity liczby {value, type: int32|uint32|int64|uint64|...}"),
    "double": (handle_double, "Reprezentacja IEEE 754 {value}"),
    "sequence": (handle_sequence, "n! / F(n) mod m {kind: factorial|fibonacci, n, m?}"),
    "limits": (handle_limits, "Tabela złożoności dla ograniczeń {n, q?, m?, time_limit, ops_per_sec}"),
    "memory": (handle_memory, "Pamięć deklaracji C++ {decls, limit_mb, vars?}"),
}


def handle_ops(_):
    return {"ops": [{"op": k, "path": f"/api/calc/{k}", "desc": d} for k, (_, d) in OPS.items()]}


def handle_unknown(_, op):
    raise ApiError(f"Nieznana operacja kalkulatora „{op}”. Dostępne: {', '.join(OPS)}.", 404)


ROUTES = {f"POST /api/calc/{k}": fn for k, (fn, _) in OPS.items()}
ROUTES["GET /api/calc/ops"] = handle_ops
ROUTES["POST /api/calc/<op>"] = handle_unknown

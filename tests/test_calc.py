"""Testy kalkulatora OI (oiforge/tools/calc.py): wyrażenia, Miller–Rabin, Pollard rho, CRT, nCr (Lucas),
bity, double, systemy liczbowe, tabela limitów, parser pamięci + test HTTP na porcie 8812.

Uruchom: python3 -m unittest tests.test_calc -v
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge.tools import calc as C  # noqa: E402

PORT = 8812


def naive_prime(n: int) -> bool:
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True


def product(fac: dict[int, int]) -> int:
    out = 1
    for p, e in fac.items():
        out *= p ** e
    return out


class TestExpressions(unittest.TestCase):
    def test_olympiad_notation(self):
        cases = {"2e5+5": 200005, "10^9+7": 10 ** 9 + 7, "1<<20": 1 << 20, "2·10^5": 200000, "1'000'000": 10 ** 6,
                 "1000000LL": 10 ** 6, "0x1F": 31, "1.5e3": 1500, "1.05e3": 1050, "1 000 000": 10 ** 6, "(1<<20)+5": (1 << 20) + 5,
                 "max(3, 7) * 2": 14, "-7 % 3": 2, "007": 7, "2^61-1": 2 ** 61 - 1}
        for expr, want in cases.items():
            with self.subTest(expr=expr):
                self.assertEqual(C.evaluate(expr), want)

    def test_cpp_semantics(self):
        notes = []
        self.assertEqual(C.evaluate("10^5", cpp=True, notes=notes), 15)
        self.assertTrue(any("XOR" in n for n in notes))
        self.assertEqual(C.evaluate("-7 / 2", cpp=True), -3)
        self.assertEqual(C.evaluate("-7 % 3", cpp=True), -1)
        self.assertEqual(C.evaluate("N * 2 + 1", {"N": 100}), 201)
        self.assertEqual(C.evaluate("MAXVAL + full", {"MAXVAL": 5, "full": 1}), 6)   # sufiksy nie psują nazw

    def test_errors(self):
        for bad in ("", "abc", "1.5", "2**100000000", "import os", "__import__('os')", "1/0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                C.evaluate(bad)


class TestPrimes(unittest.TestCase):
    def test_small_against_naive(self):
        for n in range(-5, 5000):
            self.assertEqual(C.is_prime(n), naive_prime(n), n)

    def test_strong_pseudoprimes_and_carmichael(self):
        pseudo = [561, 1105, 1729, 2047, 41041, 825265, 3215031751, 2152302898747, 3474749660383, 341550071728321,
                  3825123056546413051, 318665857834031151167461, 3317044064679887385961981]
        for n in pseudo:
            self.assertFalse(C.is_prime(n), n)

    def test_big_primes(self):
        for p in (2 ** 61 - 1, 10 ** 9 + 7, 998244353, 10 ** 18 + 9, 2 ** 89 - 1, 2 ** 127 - 1, 1000000000000000003):
            self.assertTrue(C.is_prime(p), p)
        self.assertFalse(C.is_prime((2 ** 61 - 1) * (10 ** 9 + 7)))

    def test_next_prev(self):
        self.assertEqual(C.next_prime(10 ** 9), 10 ** 9 + 7)
        self.assertEqual(C.prev_prime(10 ** 9 + 7), 999999937)
        self.assertEqual(C.next_prime(1), 2)
        self.assertIsNone(C.prev_prime(2))


class TestFactorization(unittest.TestCase):
    def check(self, n):
        fac, rest = C.factorize(n, C.Deadline(20))
        self.assertEqual(rest, [], n)
        self.assertEqual(product(fac), n)
        for p in fac:
            self.assertTrue(C.is_prime(p), p)
        return fac

    def test_known(self):
        self.assertEqual(self.check(999999999999999999), {3: 4, 7: 1, 11: 1, 13: 1, 19: 1, 37: 1, 52579: 1, 333667: 1})
        self.assertEqual(self.check(10 ** 18), {2: 18, 5: 18})
        self.assertEqual(self.check(999999937 * 999999929), {999999929: 1, 999999937: 1})
        self.assertEqual(self.check((10 ** 9 + 7) ** 2), {10 ** 9 + 7: 2})
        self.assertEqual(self.check(2 ** 61 - 1), {2 ** 61 - 1: 1})
        self.assertEqual(self.check(4611686014132420609), {2147483647: 2})
        self.assertEqual(self.check(897612484786617600), {2: 8, 3: 4, 5: 2, 7: 2, 11: 1, 13: 1, 17: 1, 19: 1, 23: 1, 29: 1, 31: 1, 37: 1})

    def test_random_up_to_1e18(self):
        rng = random.Random(7)
        t0 = time.perf_counter()
        for _ in range(60):
            self.check(rng.randrange(2, 10 ** 18))
        for _ in range(15):     # półpierwsze z czynnikami ~10^9 — najtrudniejszy przypadek dla rho
            p = C.next_prime(rng.randrange(10 ** 8, 10 ** 9))
            q = C.next_prime(rng.randrange(10 ** 8, 10 ** 9))
            self.assertEqual(self.check(p * q), dict(sorted({p: 1, q: 1}.items())) if p != q else {p: 2})
        self.assertLess(time.perf_counter() - t0, 60)

    def test_divisor_functions(self):
        fac = self.check(720)
        self.assertEqual(C.divisor_count(fac), 30)
        self.assertEqual(C.divisor_sum(fac), sum(d for d in range(1, 721) if 720 % d == 0))
        self.assertEqual(C.euler_phi(fac), sum(1 for k in range(1, 721) if math.gcd(k, 720) == 1))
        self.assertEqual(C.divisors_list(fac), [d for d in range(1, 721) if 720 % d == 0])
        big = self.check(897612484786617600)
        self.assertEqual(C.divisor_count(big), 103680)
        self.assertIsNone(C.divisors_list(big, 5000))

    def test_timeout_returns_partial(self):
        p, q = 2 ** 89 - 1, 2 ** 107 - 1
        fac, rest = C.factorize(12 * p * q, C.Deadline(0.05))
        self.assertEqual(fac.get(2), 2)
        self.assertEqual(fac.get(3), 1)
        self.assertEqual(rest, [p * q])


class TestGcdCrt(unittest.TestCase):
    def test_ext_gcd(self):
        rng = random.Random(1)
        for _ in range(500):
            a, b = rng.randrange(-10 ** 12, 10 ** 12), rng.randrange(-10 ** 12, 10 ** 12)
            g, x, y = C.ext_gcd(a, b)
            self.assertEqual(g, math.gcd(a, b))
            self.assertEqual(a * x + b * y, g)
        self.assertEqual(C.ext_gcd(240, 46), (2, -9, 47))

    def test_crt_coprime(self):
        self.assertEqual(C.crt([(2, 3), (3, 5), (2, 7)]), (23, 105))

    def test_crt_general(self):
        self.assertEqual(C.crt([(1, 4), (3, 6)]), (9, 12))
        self.assertIsNone(C.crt([(1, 4), (2, 6)]))
        rng = random.Random(3)
        for _ in range(300):
            x = rng.randrange(0, 10 ** 15)
            mods = [rng.randrange(1, 10 ** 5) for _ in range(rng.randrange(1, 5))]
            res = C.crt([(x % m, m) for m in mods])
            self.assertIsNotNone(res)
            r, M = res
            self.assertEqual(M, math.lcm(*mods))
            self.assertEqual(r, x % M)

    def test_inverse(self):
        self.assertEqual(C.mod_inverse(3, 7), 5)
        self.assertIsNone(C.mod_inverse(6, 9))
        self.assertEqual(3 * C.mod_inverse(3, 10 ** 9 + 7) % (10 ** 9 + 7), 1)


class TestCombinatorics(unittest.TestCase):
    def test_exact_range(self):
        for n, k, m in [(10, 3, 1000), (1000, 500, 10 ** 9 + 7), (200000, 77777, 998244353), (5, 7, 13), (50, 25, 1)]:
            v, _, _ = C.ncr_mod(n, k, m)
            self.assertEqual(v, math.comb(n, k) % m)

    def test_lucas_against_comb(self):
        rng = random.Random(5)
        for p in (2, 3, 5, 7, 13, 101, 1009):
            for _ in range(40):
                n = rng.randrange(0, 3000)
                k = rng.randrange(0, n + 1)
                self.assertEqual(C.lucas(n, k, p), math.comb(n, k) % p, (n, k, p))

    def test_big_n(self):
        p = 10 ** 9 + 7
        v, method, _ = C.ncr_mod(10 ** 6, 3, p)
        self.assertEqual(v, math.comb(10 ** 6, 3) % p)
        self.assertIn("Fermata", method)
        v, method, _ = C.ncr_mod(10 ** 18, 10 ** 9, 13)
        self.assertIn("Lucas", method)
        # Lucas dla 13: porównanie z ręcznym iloczynem po cyfrach
        n, k, want = 10 ** 18, 10 ** 9, 1
        while n or k:
            want = want * math.comb(n % 13, k % 13) % 13
            n //= 13
            k //= 13
        self.assertEqual(v, want)
        # moduł bezkwadratowy złożony: Lucas + CRT
        v, method, _ = C.ncr_mod(10 ** 6, 12345, 2 * 3 * 5 * 7 * 11 * 13)
        self.assertEqual(v, math.comb(10 ** 6, 12345) % 30030)
        self.assertIn("CRT", method.upper())
        with self.assertRaises(ValueError):
            C.ncr_mod(10 ** 6, 5, 2 ** 10)

    def test_fib_fact(self):
        fibs = [0, 1]
        for _ in range(300):
            fibs.append(fibs[-1] + fibs[-2])
        for n in range(300):
            self.assertEqual(C.fib_mod(n), fibs[n])
            self.assertEqual(C.fib_mod(n, 1000003), fibs[n] % 1000003)
        self.assertEqual(C.fib_mod(10 ** 18, 10 ** 9 + 7), 209783453)
        for n in range(0, 60):
            self.assertEqual(C.fact_mod(n, 1000003)[0], math.factorial(n) % 1000003)
        self.assertEqual(C.fact_mod(10 ** 9 + 6, 10 ** 9 + 7)[0], 10 ** 9 + 6)     # tw. Wilsona
        self.assertEqual(C.fact_mod(10 ** 6, 999983)[0], 0)
        direct = 1
        for i in range(2, 999980):
            direct = direct * i % 999983
        v, method = C.fact_mod(999979, 999983)
        self.assertEqual(v, direct)
        self.assertIn("Wilsona", method)


class TestBitsDoubleBases(unittest.TestCase):
    def test_bits(self):
        b = C.bits_info(-5, "int32")
        self.assertEqual(b["bits"], "1" * 29 + "011")
        self.assertEqual(b["hex"], "0xFFFFFFFB")
        self.assertEqual(b["popcount"], 31)
        self.assertFalse(b["overflow"])
        self.assertIn("u2", b)
        b = C.bits_info(12, "uint64")
        self.assertEqual(b["highest"], 3)
        self.assertEqual(b["lowest"], 2)
        ops = {o["expr"]: o["value"] for o in b["ops"]}
        self.assertEqual(ops["x & -x"], "4")
        self.assertEqual(ops["x & (x - 1)"], "8")
        b = C.bits_info(2 ** 31, "int32")
        self.assertTrue(b["overflow"])
        self.assertEqual(b["value"], str(-2 ** 31))
        self.assertEqual(C.bits_info(-1, "uint32")["value"], str(2 ** 32 - 1))

    def test_double(self):
        d = C.double_info("0.1")
        self.assertFalse(d["exactly_representable"])
        self.assertTrue(d["exact"].startswith("0.1000000000000000055511151231257827"))
        self.assertEqual(d["exponent"], -4)
        self.assertEqual(C.double_info("0.5")["exactly_representable"], True)
        self.assertEqual(C.double_info("0.1+0.2")["value"], "0.30000000000000004")
        self.assertEqual(C.double_info("-0.0")["sign"], 1)
        self.assertEqual(C.double_info("1e-310")["kind"], "podnormalna (subnormal)")
        self.assertEqual(C.double_info("inf")["kind"], "+∞")

    def test_bases(self):
        self.assertEqual(C.convert_base("255", 10, 16)["result"], "FF")
        self.assertEqual(C.convert_base("0xff", 0, 2)["result"], "11111111")
        self.assertEqual(C.convert_base("-1010", 2, 10)["result"], "-10")
        self.assertEqual(C.convert_base("ZZ", 36, 10)["result"], "1295")
        with self.assertRaises(ValueError):
            C.convert_base("129", 8, 10)
        rng = random.Random(9)
        for _ in range(200):
            v, b = rng.randrange(-10 ** 30, 10 ** 30), rng.randrange(2, 37)
            self.assertEqual(int(C.to_base(v, b), b), v)


class TestLimits(unittest.TestCase):
    def test_table(self):
        r = C.limits_table(200000, 100000, 0, 1.0, 2e8)
        verdict = {row["name"]: row["verdict"] for row in r["rows"]}
        self.assertEqual(verdict["O(n log n)"], "ok")
        self.assertEqual(verdict["O(n²)"], "tle")
        self.assertEqual(verdict["O(2^n)"], "tle")
        self.assertEqual(len(r["rows"]), 13)
        self.assertTrue(any(e["name"] == "O((n + q) log n)" for e in r["extra"]))
        small = {row["name"]: row["verdict"] for row in C.limits_table(20, time_limit=1.0)["rows"]}
        self.assertEqual(small["O(2^n)"], "ok")
        self.assertEqual(small["O(n!)"], "tle")
        self.assertEqual(C.limits_table(5000)["best"], "O(n²)")

    def test_constraint_strings(self):
        r = C.handle_limits({"n": "1 ≤ n ≤ 2·10^5", "q": "q ≤ 10^5", "time_limit": "2 s"})
        self.assertEqual(r["n"], "200000")
        self.assertEqual(r["q"], "100000")
        self.assertEqual(C.handle_limits({"n": "1<<20"})["n"], str(1 << 20))


class TestMemory(unittest.TestCase):
    def test_brief_example(self):
        r = C.memory_report("int a[1000000]; long long dp[5005][5005]; vector<int> g[200005]; bitset<100000> b;", 256)
        by = {i["name"]: i for i in r["items"]}
        self.assertEqual(by["a"]["bytes"], 4_000_000)
        self.assertEqual(by["dp"]["bytes"], 5005 * 5005 * 8)
        self.assertEqual(by["g"]["bytes"], 200005 * 24)
        self.assertEqual(by["b"]["bytes"], (100000 + 63) // 64 * 8)
        self.assertEqual(r["total"], 4_000_000 + 5005 * 5005 * 8 + 200005 * 24 + 12504)
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(C.memory_report("long long dp[5005][5005];", 128)["verdict"], "mle")

    def test_constants_and_expressions(self):
        code = """
        #define MAXN 100005
        const int N = 2e5 + 5, LOG = 18;
        constexpr int M = 1 << 20;
        int a[N], b[2 * N + 1];
        long long up[LOG][N];
        char s[MAXN];
        int bad[10^5];
        """
        r = C.memory_report(code, 256)
        by = {i["name"]: i for i in r["items"]}
        self.assertEqual(by["a"]["count"], 200005)
        self.assertEqual(by["b"]["count"], 400011)
        self.assertEqual(by["up"]["bytes"], 18 * 200005 * 8)
        self.assertEqual(by["s"]["bytes"], 100005)
        self.assertEqual(by["bad"]["count"], 15)
        self.assertTrue(any("XOR" in w for w in by["bad"]["warnings"]))
        self.assertEqual({c["name"] for c in r["consts"]} >= {"N", "LOG", "M", "MAXN"}, True)

    def test_types_structs_aliases(self):
        code = """
        typedef long long ll;
        using pii = pair<int, int>;
        struct Node { int l, r; ll sum; } t[400000];
        struct E { char c; double w; int x; };
        E e[1000];
        pii p[10];
        pair<long long, int> q[10];
        array<int, 3> ar[100];
        long double ld[10];
        vector<bool> vb(800);
        unsigned long long u[4];
        """
        by = {i["name"]: i for i in C.memory_report(code, 256)["items"]}
        self.assertEqual(by["t"]["elem_bytes"], 16)
        self.assertEqual(by["e"]["elem_bytes"], 24)          # wyrównanie: 1 + 7 + 8 + 4 + 4
        self.assertEqual(by["p"]["bytes"], 80)
        self.assertEqual(by["q"]["bytes"], 160)
        self.assertEqual(by["ar"]["bytes"], 1200)
        self.assertEqual(by["ld"]["bytes"], 160)
        self.assertEqual(by["vb"]["heap"], 104)
        self.assertEqual(by["u"]["bytes"], 32)

    def test_define_int_long_long(self):
        r = C.memory_report("#define int long long\nint a[1000];", 256)
        self.assertEqual(r["items"][0]["bytes"], 8000)
        self.assertTrue(any("define int long long" in n["text"] for n in r["notes"]))

    def test_local_arrays_and_vectors(self):
        code = """
        int f(int x);
        int main() {
            int n; cin >> n;
            int small[100];
            long long big[2000000];
            int vla[n];
            vector<int> v(n);
            vector<vector<int>> dp(n + 1, vector<int>(n + 1));
            static int st[1 << 20];
            auto g = [&](int x) { int inner[50]; return x; };
            for (int i = 0; i < n; i++) { int tmp[4]; }
        }
        """
        r = C.memory_report(code, 256, "n = 1000")
        by = {i["name"]: i for i in r["items"]}
        self.assertNotIn("f", by)
        self.assertNotIn("inner", by)
        self.assertEqual(by["big"]["storage"], "stack")
        self.assertTrue(any("STOSIE" in w for w in by["big"]["warnings"]))
        self.assertTrue(any("NIE jest zerowana" in n for n in by["small"]["notes"]))
        self.assertEqual(by["vla"]["count"], 1000)
        self.assertEqual(by["v"]["heap"], 4000)
        self.assertEqual(by["dp"]["heap"], 1001 * (24 + 1001 * 4))
        self.assertEqual(by["st"]["storage"], "global")
        self.assertIn("tmp", by)
        r2 = C.memory_report("int main(){ int n; cin >> n; int vla[n]; vector<int> v(n); }", 256)
        by2 = {i["name"]: i for i in r2["items"]}
        self.assertTrue(by2["vla"]["unknown"])
        self.assertTrue(any("VLA" in w for w in by2["vla"]["warnings"]))
        self.assertTrue(by2["v"]["unknown"])

    def test_resize(self):
        r = C.memory_report("const int N = 1000;\nvector<long long> d;\nint main(){ d.resize(N + 1); }", 256)
        self.assertEqual(r["items"][0]["heap"], 1001 * 8)

    def test_containers_notes(self):
        r = C.memory_report("map<int, int> mp; set<long long> s; string t; deque<int> dq;", 256)
        by = {i["name"]: i for i in r["items"]}
        self.assertTrue(any("na element" in n for n in by["mp"]["notes"]))
        self.assertEqual(by["t"]["bytes"], 32)

    def test_empty_input(self):
        with self.assertRaises(ValueError):
            C.memory_report("   ", 256)


class TestHandlers(unittest.TestCase):
    def test_validation_messages_are_polish(self):
        with self.assertRaises(C.ApiError) as cm:
            C.handle_modular({"a": "5", "m": "0"})
        self.assertIn("≥ 1", cm.exception.message)
        with self.assertRaises(C.ApiError) as cm:
            C.handle_inverse({"a": "6", "m": "9"})
        self.assertIn("nie istnieje", cm.exception.message)
        with self.assertRaises(C.ApiError):
            C.handle_factorize({"n": "0"})
        with self.assertRaises(C.ApiError):
            C.handle_eval({"expr": "os.system('x')"})

    def test_modular_handler(self):
        r = C.handle_modular({"a": "-3", "b": "4", "m": "1e9+7"})
        m = 10 ** 9 + 7
        self.assertEqual(r["sum"], "1")
        self.assertEqual(r["diff"], str((-7) % m))
        self.assertEqual(r["pow"], "81")
        self.assertEqual(int(r["quot"]) * 4 % m, (-3) % m)
        self.assertTrue(r["hints"])

    def test_crt_handler(self):
        r = C.handle_crt({"congruences": [["2", "3"], ["3", "5"], ["2", "7"]]})
        self.assertEqual((r["x"], r["M"]), ("23", "105"))
        r = C.handle_crt({"congruences": [{"a": 1, "m": 4}, {"a": 2, "m": 6}]})
        self.assertFalse(r["solvable"])
        self.assertIn("sprzeczny", r["message"])

    def test_gcd_handler(self):
        r = C.handle_gcd({"a": "240", "b": "46"})
        self.assertEqual((r["gcd"], r["lcm"], r["x"], r["y"]), ("2", "5520", "-9", "47"))
        self.assertTrue(r["steps"])
        self.assertEqual(C.handle_gcd({"numbers": "12 18 30"})["gcd"], "6")

    def test_factor_handler(self):
        r = C.handle_factorize({"n": "10^18"})
        self.assertEqual(r["pretty"], "2^18 · 5^18")
        self.assertEqual(r["divisor_count"], "361")
        r = C.handle_factorize({"n": "2^61-1"})
        self.assertTrue(r["is_prime"])


@unittest.skipIf(os.environ.get("OIFORGE_SKIP_HTTP"), "pominięto test HTTP")
class TestHTTP(unittest.TestCase):
    proc = None

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, OIFORGE_NO_WARMUP="1")
        cls.proc = subprocess.Popen([sys.executable, "-m", "oiforge", "--port", str(PORT), "--no-browser"], cwd=str(ROOT),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=3) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.4)
        cls.tearDownClass()
        raise unittest.SkipTest("Serwer nie wystartował na porcie 8812.")

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
            cls.proc = None

    def post(self, path, body, header=True):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"X-OIForge": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_endpoints(self):
        st, r = self.post("/api/calc/factorize", {"n": "999999999999999999"})
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertIn("52579", r["pretty"])
        st, r = self.post("/api/calc/modular", {"a": "2", "b": "10", "m": "1000"})
        self.assertEqual(r["pow"], "24")
        st, r = self.post("/api/calc/memory", {"decls": "int a[1000000];", "limit_mb": 256})
        self.assertEqual(r["total"], 4_000_000)
        st, r = self.post("/api/calc/limits", {"n": "2e5", "q": "1e5", "time_limit": 1})
        self.assertEqual(len(r["rows"]), 13)
        st, r = self.post("/api/calc/ncr", {"n": "10", "k": "3", "p": "1000"})
        self.assertEqual(r["value"], "120")
        st, r = self.post("/api/calc/bits", {"value": "-1", "type": "int32"})
        self.assertEqual(r["popcount"], 32)

    def test_errors(self):
        st, r = self.post("/api/calc/inverse", {"a": "2", "m": "4"})
        self.assertEqual(st, 400)
        self.assertFalse(r["ok"])
        self.assertIn("nie istnieje", r["error"])
        st, r = self.post("/api/calc/nieistnieje", {})
        self.assertEqual(st, 404)
        st, r = self.post("/api/calc/eval", {"expr": "1+1"}, header=False)
        self.assertEqual(st, 403)
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/calc/ops", timeout=10) as resp:
            ops = json.loads(resp.read())["ops"]
        self.assertGreaterEqual(len(ops), 15)


if __name__ == "__main__":
    unittest.main()

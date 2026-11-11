"""Testy tłumacza Python → C++ (oiforge.tools.py2cpp + pakiet oiforge.tools._py2cpp).

1. Zgodność: każdy program z tests/py2cpp_cases/*.py jest tłumaczony, kompilowany (runner.compile_cpp)
   i uruchamiany na wejściach z pliku .io — wyjście C++ musi być IDENTYCZNE z wyjściem Pythona
   (zapisanym w .io i dodatkowo sprawdzanym na żywo). Na końcu wypisywany jest % zgodnych.
2. Testy jednostkowe konkretnych tłumaczeń (heapq, bisect, // i %, opcje, ostrzeżenia, mapa linii…).
3. Odporność: konstrukcje nieobsługiwane → komentarz z oryginałem (bez „TODO”) + ostrzeżenie, nigdy wyjątek.

Uruchomienie: python3 -m unittest tests.test_py2cpp -v
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oiforge import config  # noqa: E402
from oiforge.core import runner  # noqa: E402
from oiforge.server_api import ApiError  # noqa: E402
from oiforge.tools import py2cpp as tool  # noqa: E402
from oiforge.tools._py2cpp import translate  # noqa: E402
from oiforge.tools._py2cpp.examples import EXAMPLES  # noqa: E402

CASES_DIR = ROOT / "tests" / "py2cpp_cases"
HAS_CXX = config.CXX is not None


def parse_io(text: str) -> list[tuple[str, str]]:
    """Plik .io: bloki `=== in` / `=== out` (wejście i oczekiwane wyjście = wyjście Pythona)."""
    pairs, cur, mode = [], None, None
    for line in text.split("\n"):
        if line.startswith("=== in"):
            cur = {"in": [], "out": []}
            pairs.append(cur)
            mode = "in"
            continue
        if line.startswith("=== out"):
            mode = "out"
            continue
        if cur is not None:
            cur[mode].append(line)
    return [("\n".join(p["in"]).rstrip("\n") + "\n", "\n".join(p["out"]).rstrip("\n") + "\n") for p in pairs]


def norm(s: str) -> str:
    return runner.normalize_output(s)


class _Suite:
    """Wspólny stan: tłumaczenia i kompilacje wszystkich przypadków (równolegle, z cache binarek)."""
    loaded = False
    cases: dict = {}
    results: dict = {}

    @classmethod
    def load(cls):
        if cls.loaded:
            return
        cls.loaded = True
        for py in sorted(CASES_DIR.glob("*.py")):
            io = py.with_suffix(".io")
            cls.cases[py.stem] = (py.read_text(encoding="utf-8"), parse_io(io.read_text(encoding="utf-8")) if io.exists() else [])
        for ex in EXAMPLES:
            cls.cases["przyklad_" + ex["id"]] = (ex["code"], [(ex["input"], None)])
        if not HAS_CXX:
            return

        def work(item):
            name, (code, pairs) = item
            tr = translate(code)
            cr = runner.compile_cpp(tr["cpp"]) if tr.get("ok") else None
            return name, tr, cr
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            for name, tr, cr in ex.map(work, cls.cases.items()):
                cls.results[name] = (tr, cr)


@unittest.skipUnless(HAS_CXX, "Brak kompilatora C++ — testy zgodności pominięte.")
class TestEquivalence(unittest.TestCase):
    """Tłumaczenie każdego programu kompiluje się i daje identyczne wyjście jak Python."""
    passed: set = set()
    total = 0

    @classmethod
    def setUpClass(cls):
        _Suite.load()
        cls.total = len(_Suite.cases)

    @classmethod
    def tearDownClass(cls):
        n = cls.total
        k = len(cls.passed)
        if n:
            print(f"\n[py2cpp] zgodnych programów: {k}/{n} ({100 * k / n:.1f}%)", file=sys.stderr)

    def check(self, name: str):
        code, pairs = _Suite.cases[name]
        tr, cr = _Suite.results[name]
        self.assertTrue(tr.get("ok"), f"{name}: tłumaczenie nie powiodło się: {tr.get('error')}")
        errors = [w for w in tr["warnings"] if w["severity"] == "error"]
        self.assertEqual(errors, [], f"{name}: fragmenty nieprzetłumaczone: {errors}")
        self.assertTrue(cr is not None and cr.ok, f"{name}: C++ się nie kompiluje:\n{cr.stderr if cr else ''}\n{tr['cpp']}")
        self.assertTrue(pairs, f"{name}: brak testów wejścia")
        prog_py = runner.prepare(code, lang="python")
        for i, (inp, expected) in enumerate(pairs):
            rp = runner.run_program(prog_py, inp, timeout=20)
            self.assertEqual(rp.verdict, "OK", f"{name} test {i}: Python zakończył się {rp.verdict}: {rp.stderr[-500:]}")
            if expected is not None:
                self.assertEqual(norm(rp.stdout), norm(expected), f"{name} test {i}: plik .io nieaktualny")
            rc = runner.run([cr.exe], inp, timeout=10)
            self.assertEqual(rc.verdict, "OK", f"{name} test {i}: C++ zakończył się {rc.verdict}: {rc.stderr[-500:]}")
            self.assertEqual(norm(rc.stdout), norm(rp.stdout), f"{name} test {i}: różne wyjścia\nWEJŚCIE:\n{inp}")
            self.assertTrue(runner.outputs_equal(rc.stdout, rp.stdout))
        type(self).passed.add(name)


def _make_case_test(name):
    def test(self):
        self.check(name)
    test.__name__ = f"test_{name}"
    test.__doc__ = f"Zgodność Python ↔ C++: {name}"
    return test


for _py in sorted(CASES_DIR.glob("*.py")):
    setattr(TestEquivalence, f"test_{_py.stem}", _make_case_test(_py.stem))
for _ex in EXAMPLES:
    setattr(TestEquivalence, f"test_przyklad_{_ex['id']}", _make_case_test("przyklad_" + _ex["id"]))


class TestCasesPresent(unittest.TestCase):
    def test_at_least_40_programs(self):
        progs = list(CASES_DIR.glob("*.py"))
        self.assertGreaterEqual(len(progs), 40)
        for p in progs:
            self.assertTrue(p.with_suffix(".io").exists(), f"brak {p.with_suffix('.io').name}")
            pairs = parse_io(p.with_suffix(".io").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(pairs), 1, p.name)


def cpp_of(code: str, **opts) -> dict:
    r = translate(code, opts)
    assert r["ok"], r.get("error")
    return r


def main_body(cpp: str) -> str:
    return cpp.split("int main() {", 1)[1]


class TestTranslation(unittest.TestCase):
    """Konkretne reguły tłumaczenia (bez kompilacji)."""

    def test_heapq_becomes_min_priority_queue(self):
        r = cpp_of("import heapq\nh = []\nheapq.heappush(h, 5)\nheapq.heappush(h, 1)\nprint(heapq.heappop(h), h[0], len(h))\n")
        self.assertIn("priority_queue<long long, vector<long long>, greater<long long>> h;", r["cpp"])
        self.assertIn("h.push(5);", r["cpp"])
        self.assertIn("h.top()", r["cpp"])

    def test_heapq_tuple_pop_unpack(self):
        r = cpp_of("import heapq\npq = [(0, 1)]\nwhile pq:\n    d, u = heapq.heappop(pq)\n    print(d, u)\n")
        self.assertIn("auto [d, u] = pq.top();", r["cpp"])
        self.assertIn("pq.pop();", r["cpp"])
        self.assertIn("while (!pq.empty())", r["cpp"])

    def test_bisect(self):
        r = cpp_of("from bisect import bisect_left, bisect_right, insort\na = [1, 2, 4]\n"
                   "print(bisect_left(a, 2), bisect_right(a, 2))\ninsort(a, 3)\n")
        self.assertIn("lower_bound(a.begin(), a.end(), 2) - a.begin()", r["cpp"])
        self.assertIn("upper_bound(a.begin(), a.end(), 2) - a.begin()", r["cpp"])
        self.assertIn("a.insert(upper_bound(a.begin(), a.end(), 3), 3);", r["cpp"])

    def test_floordiv_mod_only_when_sign_unknown(self):
        r = cpp_of("for i in range(10):\n    print(i // 3, i % 3)\n")
        self.assertNotIn("floordiv(", r["cpp"])
        self.assertNotIn("pymod(", r["cpp"])
        self.assertIn("i / 3", r["cpp"])
        r = cpp_of("a = int(input())\nprint(a // 2, a % 2)\n")
        self.assertIn("floordiv(a, 2)", main_body(r["cpp"]))
        self.assertIn("pymod(a, 2)", main_body(r["cpp"]))
        r = cpp_of("a = int(input())\nif a % 2 == 0:\n    print('parzysta')\n")
        self.assertNotIn("pymod(", r["cpp"])

    def test_mod_by_positive_constant_gives_nonneg(self):
        r = cpp_of("M = 10**9 + 7\nx = int(input()) % M\nprint(x * 3 % M)\n")
        body = main_body(r["cpp"])
        self.assertIn("pymod(", body)
        self.assertIn("x * 3 % M", body)
        self.assertIn("const long long M = 1000000007;", r["cpp"])

    def test_int_option(self):
        r = cpp_of("n = int(input())\na = list(map(int, input().split()))\nprint(sum(a) * n)\n", int_type="int")
        body = main_body(r["cpp"])
        self.assertIn("int n;", body)
        self.assertIn("vector<int> a = readInts();", body)
        self.assertNotIn("long long n", body)

    def test_unordered_option(self):
        code = "d = {}\nfor w in input().split():\n    d[w] = d.get(w, 0) + 1\nprint(len(d))\n"
        self.assertIn("map<string, long long> d;", cpp_of(code)["cpp"])
        self.assertIn("unordered_map<string, long long> d;", cpp_of(code, use_unordered=True)["cpp"])
        # para nie ma std::hash → zostaje map
        r = cpp_of("d = {}\nd[(1, 2)] = 3\nprint(d[(1, 2)])\n", use_unordered=True)
        self.assertIn("map<pair<long long, long long>, long long> d;", r["cpp"])

    def test_fast_io_option(self):
        self.assertIn("ios::sync_with_stdio(false);", cpp_of("print(1)\n")["cpp"])
        self.assertNotIn("sync_with_stdio", cpp_of("print(1)\n", fast_io=False)["cpp"])

    def test_header_and_newline(self):
        cpp = cpp_of("print('ala')\n")["cpp"]
        self.assertIn("#include <bits/stdc++.h>", cpp)
        self.assertIn("using namespace std;", cpp)
        self.assertIn('cout << "ala\\n";', cpp)
        self.assertNotIn("endl", cpp)

    def test_line_map(self):
        code = "n = int(input())\ns = 0\nfor i in range(n):\n    s += i\nprint(s)\n"
        r = cpp_of(code)
        lines = r["cpp"].split("\n")
        py_lines = len(code.split("\n"))
        self.assertTrue(r["line_map"])
        mapped = {}
        for c, p in r["line_map"]:
            self.assertTrue(1 <= c <= len(lines))
            self.assertTrue(1 <= p <= py_lines)
            mapped.setdefault(p, []).append(lines[c - 1])
        self.assertTrue(any("s += i;" in t for t in mapped[4]))
        self.assertTrue(any("for (" in t for t in mapped[3]))
        self.assertTrue(any("cout" in t for t in mapped[5]))

    def test_types_and_containers(self):
        r = cpp_of("n = 3\ng = [[] for _ in range(n)]\ng[0].append((1, 2.5))\ndp = [[0] * 4 for _ in range(n)]\n"
                   "s = set()\ns.add('xy')\nc = set()\nc.add('z')\nprint(g, dp, s, c)\n")
        cpp = r["cpp"]
        self.assertIn("vector<vector<pair<long long, double>>> g(n);", cpp)
        self.assertIn("vector<vector<long long>> dp(n, vector<long long>(4, 0));", cpp)
        self.assertIn("set<string> s;", cpp)
        self.assertIn("set<char> c;", cpp)          # jednoznakowy napis → char

    def test_char_vs_string(self):
        r = cpp_of("s = input()\ncnt = 0\nfor c in s:\n    if c == 'a':\n        cnt += 1\nprint(cnt, s[0] == 'b')\n")
        self.assertIn("for (char c : s)", r["cpp"])
        self.assertIn("c == 'a'", r["cpp"])
        self.assertIn("s[0] == 'b'", r["cpp"])

    def test_token_reading(self):
        body = main_body(cpp_of("n, m = map(int, input().split())\nx = int(input())\nprint(n + m + x)\n")["cpp"])
        self.assertIn("long long n, m;", body)
        self.assertIn("cin >> n >> m;", body)
        self.assertIn("cin >> x;", body)
        self.assertNotIn("skipLine", body)
        # mieszanie odczytu tokenów i całych linii → reszta linii pomijana
        body = main_body(cpp_of("n = int(input())\ns = input()\nprint(n, s)\n")["cpp"])
        self.assertIn("skipLine();", body)

    def test_fstring_formatting(self):
        cpp = cpp_of("x = 2.0 / 3\nn = 7\nprint(f'{x:.2f} {n:>5} {n:05d}')\n")["cpp"]
        self.assertIn("fixed << setprecision(2) << x", cpp)
        self.assertIn('fmt(n, ">5")', cpp)
        self.assertIn('fmt(n, "05d")', cpp)

    def test_sort_key_is_stable(self):
        cpp = cpp_of("a = [(1, 'b'), (0, 'a')]\na.sort(key=lambda p: -p[0])\nb = sorted(a, reverse=True)\nprint(a, b)\n")["cpp"]
        self.assertIn("stable_sort(a.begin(), a.end()", cpp)
        self.assertIn("sort(b.begin(), b.end(), greater<>());", cpp)

    def test_swap_and_tuple_assign(self):
        cpp = cpp_of("a, b = 1, 2\na, b = b, a\na, b = b, a + b\nprint(a, b)\n")["cpp"]
        self.assertIn("swap(a, b);", cpp)
        self.assertIn("tie(a, b) = make_tuple(b, a + b);", cpp)

    def test_chained_comparison(self):
        cpp = cpp_of("a, b, c = 1, 2, 3\nprint(a < b < c)\n")["cpp"]
        self.assertIn("a < b && b < c", cpp)

    def test_negative_step_range(self):
        cpp = cpp_of("n = 5\nfor i in range(n - 1, -1, -1):\n    print(i)\nfor j in reversed(range(n)):\n    print(j)\n")["cpp"]
        self.assertIn("for (long long i = n - 1; i > -1; i--)", cpp)
        self.assertIn("for (long long j = n - 1; j >= 0; j--)", cpp)

    def test_recursion_limit_comment(self):
        r = cpp_of("import sys\nsys.setrecursionlimit(10**6)\ndef f(n):\n    return 0 if n == 0 else f(n - 1)\nprint(f(10))\n")
        self.assertIn("// sys.setrecursionlimit", r["cpp"])
        self.assertTrue(any(w["code"] == "recursion" for w in r["warnings"]))
        self.assertIn("long long f(long long n)", r["cpp"])

    def test_overflow_warning(self):
        r = cpp_of("n = int(input())\nprint(2 ** n, 3 ** n)\nf = 1\nfor i in range(1, n + 1):\n    f *= i\nprint(f)\n")
        codes = [w["code"] for w in r["warnings"]]
        self.assertIn("overflow", codes)
        self.assertIn("1LL << n", r["cpp"])
        r = cpp_of("print(2 ** 70)\n")
        self.assertTrue(any(w["code"] == "big-int" for w in r["warnings"]))

    def test_inf_sentinel(self):
        r = cpp_of("INF = float('inf')\nd = [INF] * 3\nd[0] = 0\nprint(d[0], d[1] == INF)\n")
        self.assertIn("const long long INF = LLONG_MAX / 4;", r["cpp"])
        self.assertIn("vector<long long> d(3, INF);", r["cpp"])
        r = cpp_of("x = float('inf')\ny = 2.5\nprint(min(x, y))\n")
        self.assertIn("INFINITY", r["cpp"])

    def test_dict_order_warning(self):
        r = cpp_of("d = {'b': 1, 'a': 2}\nfor k, v in d.items():\n    print(k, v)\n")
        self.assertTrue(any(w["code"] == "dict-order" for w in r["warnings"]))
        self.assertIn("for (auto [k, v] : d)", r["cpp"].replace("const auto& [k, v]", "auto [k, v]"))

    def test_keywords_renamed(self):
        r = cpp_of("new = 5\ndefault = [1, 2]\ncount = 0\nfor x in default:\n    count += x\nprint(new, count)\n")
        cpp = r["cpp"]
        self.assertIn("new_", cpp)
        self.assertIn("default_", cpp)
        self.assertNotRegex(cpp, r"\blong long new\b")

    def test_main_renamed_to_solve(self):
        r = cpp_of("def main():\n    print(42)\n\nif __name__ == '__main__':\n    main()\n")
        self.assertIn("void solve() {", r["cpp"])
        self.assertIn("    solve();", r["cpp"])

    def test_class_to_struct(self):
        r = cpp_of("class P:\n    def __init__(self, x):\n        self.x = x\n    def get(self):\n        return self.x * 2\n"
                   "p = P(3)\nprint(p.get())\n")
        self.assertIn("struct P {", r["cpp"])
        self.assertIn("long long x = 0;", r["cpp"])
        self.assertIn("long long get_() {", r["cpp"])      # `get` zasłoniłoby std::get → zmiana nazwy
        self.assertIn("P p(3);", r["cpp"])

    def test_web_split_for_type_change(self):
        r = cpp_of("n = input()\nn = int(n)\nprint(n + 1)\n")
        body = main_body(r["cpp"])
        self.assertIn("string n", body)
        self.assertIn("long long n_int = stoll(n);", body)

    def test_loop_variables_reuse(self):
        body = main_body(cpp_of("for i in range(3):\n    print(i)\nfor i in range(2):\n    print(i * 2)\n")["cpp"])
        self.assertEqual(body.count("for (long long i = 0;"), 2)

    def test_lru_cache_memo(self):
        r = cpp_of("from functools import lru_cache\n@lru_cache(None)\ndef f(n):\n    return 1 if n < 2 else f(n - 1) + f(n - 2)\nprint(f(30))\n")
        self.assertIn("map<long long, long long> memo_f;", r["cpp"])
        self.assertIn("return memo_f[key] =", r["cpp"])

    def test_nested_function_lambda(self):
        r = cpp_of("def solve():\n    cnt = 0\n    def go(k):\n        nonlocal cnt\n        cnt += 1\n        if k > 0:\n"
                   "            go(k - 1)\n    go(3)\n    print(cnt)\nsolve()\n")
        self.assertIn("function<void(long long)> go = [&](long long k)", r["cpp"])


class TestRobustness(unittest.TestCase):
    """Nieobsługiwane konstrukcje: komentarz z oryginałem + ostrzeżenie, nigdy wyjątek."""

    WEIRD = [
        "def gen(n):\n    for i in range(n):\n        yield i\nprint(sum(gen(5)))\n",
        "match int(input()):\n    case 1:\n        print('a')\n    case _:\n        print('b')\n",
        "class A:\n    pass\nclass B(A):\n    pass\nprint(B())\n",
        "def f(*args):\n    return len(args)\nprint(f(1, 2, 3))\n",
        "import os\nprint(os.getcwd())\n",
        "x = [1, 2, 3]\nx[1:2] = [9, 9]\nprint(x)\n",
        "async def f():\n    await g()\n",
        "print(eval('1+2'), exec('x=1'))\n",
        "with open('plik.txt') as f:\n    print(f.read())\n",
        "a = {1: 2} | {3: 4}\nprint(a)\n",
        "lambda: (yield)\n",
        "x = [i for i in range(3)]\ny = {*x, *x}\nprint(y)\n",
        "print(*zip(*[[1, 2], [3, 4]]))\n",
        "def f(a, /, b, *, c=1, **kw):\n    return a\nprint(f(1, 2, c=3, d=4))\n",
        "global x\nx: int\nprint(__name__, __file__)\n",
        "import numpy as np\nprint(np.array([1, 2]).sum())\n",
        "",
        "   \n\n",
        "x = 1\n" * 300 + "print(x)\n",
    ]

    def test_never_raises(self):
        for code in self.WEIRD:
            with self.subTest(code=code[:40]):
                r = translate(code)
                self.assertIn("cpp", r)
                self.assertIn("warnings", r)
                self.assertIsInstance(r["warnings"], list)

    def test_unsupported_gives_todo_and_warning(self):
        r = translate("def gen(n):\n    yield n\nimport os\nprint(os.getcwd())\n")
        self.assertTrue(r["ok"])
        self.assertNotIn("TODO", r["cpp"])
        self.assertIn("nie przetłumaczone", r["cpp"])
        self.assertTrue(any(w["severity"] == "error" for w in r["warnings"]))
        for w in r["warnings"]:
            self.assertIn(w["severity"], ("error", "warn", "info"))
            self.assertTrue(w["message"])

    def test_syntax_error(self):
        r = translate("def f(:\n    pass\n")
        self.assertFalse(r["ok"])
        self.assertEqual(r["warnings"][0]["code"], "syntax")
        self.assertIn("składni", r["warnings"][0]["message"])

    def test_polish_messages(self):
        r = translate("try:\n    x = int(input())\nexcept ValueError:\n    x = 0\nprint(x)\n")
        msgs = " ".join(w["message"] for w in r["warnings"])
        self.assertRegex(msgs, r"[ąęółśżźćń]")


class TestRoutes(unittest.TestCase):
    def test_routes_registered(self):
        for k in ("POST /api/py2cpp/translate", "POST /api/py2cpp/verify", "POST /api/py2cpp/verify_start",
                  "GET /api/py2cpp/examples"):
            self.assertIn(k, tool.ROUTES)

    def test_translate_handler(self):
        r = tool.handle_translate({"code": "print(1 + 2)\n", "options": {"int_type": "int", "use_unordered": True,
                                                                         "fast_io": False}})
        self.assertTrue(r["ok"])
        self.assertIn("cout << 1 + 2", r["cpp"])
        self.assertEqual(r["options"]["int_type"], "int")
        for key in ("cpp", "warnings", "line_map"):
            self.assertIn(key, r)
        with self.assertRaises(ApiError):
            tool.handle_translate({"code": "   "})

    def test_examples(self):
        r = tool.handle_examples({})
        self.assertGreaterEqual(len(r["examples"]), 8)
        for ex in r["examples"]:
            for k in ("id", "title", "description", "code", "input"):
                self.assertIn(k, ex)
            tr = translate(ex["code"])
            self.assertTrue(tr["ok"], ex["id"])
            self.assertFalse([w for w in tr["warnings"] if w["severity"] == "error"], ex["id"])

    @unittest.skipUnless(HAS_CXX, "Brak kompilatora C++")
    def test_verify_handler(self):
        code = "a, b = map(int, input().split())\nprint(a // b, a % b)\n"
        r = tool.handle_verify({"code": code, "inputs": ["-7 2\n", "7 -2\n", "9 3\n"]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["compile"]["ok"])
        self.assertTrue(r["equal"], r)
        self.assertEqual(len(r["tests"]), 3)
        for t in r["tests"]:
            self.assertTrue(t["equal"])
            self.assertEqual(t["python"]["stdout"].split(), t["cpp"]["stdout"].split())
        self.assertIn("speedup", r)
        self.assertIsNotNone(r["python_time"])

    @unittest.skipUnless(HAS_CXX, "Brak kompilatora C++")
    def test_verify_detects_difference(self):
        # dict w Pythonie zachowuje kolejność wstawiania, map w C++ jest posortowany → różnica wykryta
        code = "d = {}\nfor w in input().split():\n    d[w] = 1\nprint(*d)\n"
        r = tool.handle_verify({"code": code, "input": "zeta alfa\n"})
        self.assertFalse(r["equal"])
        self.assertIsNotNone(r["tests"][0]["first_diff"])


class RepairTest(unittest.TestCase):
    def test_empty_range_uses_indexed_list(self):
        r = translate("tab = list(map(int, input().split()))\nfor i in range():\n    print(tab[i], end=' ')\n")
        self.assertTrue(r["ok"])
        self.assertNotIn("TODO", r["cpp"])
        self.assertIn("tab.size()", r["cpp"])
        self.assertTrue(any(w["code"] == "range-empty" and w["line"] == 2 for w in r["warnings"]))


if __name__ == "__main__":
    unittest.main()

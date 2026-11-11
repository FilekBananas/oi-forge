"""Testy porównania rozwiązań (oiforge.tools.benchmark)."""
import math
import unittest

from oiforge import config
from oiforge.core import jobs
from oiforge.server_api import ApiError
from oiforge.tools import benchmark as B

PY_SLOW = """import sys
d = sys.stdin.read().split()
n = int(d[0]); a = list(map(int, d[1:1 + n]))
c = 0
for i in range(n):
    for j in range(i + 1, n):
        if a[i] > a[j]:
            c += 1
print(c)
"""
PY_FAST = """import sys
d = sys.stdin.read().split()
n = int(d[0]); a = list(map(int, d[1:1 + n]))
# zliczanie inwersji przez sortowanie przez scalanie
def go(x):
    if len(x) < 2:
        return x, 0
    m = len(x) // 2
    l, cl = go(x[:m]); r, cr = go(x[m:])
    out = []; i = j = 0; c = cl + cr
    while i < len(l) and j < len(r):
        if r[j] < l[i]:
            out.append(r[j]); j += 1; c += len(l) - i
        else:
            out.append(l[i]); i += 1
    out += l[i:]; out += r[j:]
    return out, c
print(go(a)[1])
"""
GEN = "n = size\nprint n\nprint array(n, int(1, 1000))\n"


class TestSizes(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(B.parse_sizes("1e3, 1e4, 1e5, 2e5"), [1000, 10000, 100000, 200000])
        self.assertEqual(B.parse_sizes("10^5 2·10^5 100k 1M"), [100000, 200000, 1000000])
        self.assertEqual(B.parse_sizes([500, "1e3", 500]), [500, 1000])
        self.assertEqual(B.parse_sizes("2*10^5; 1,5e5"), [150000, 200000])
        self.assertEqual(B.parse_sizes("3e3,1e3"), [1000, 3000])
        for bad in ("", "abc", "0", "1e12"):
            with self.assertRaises(ApiError):
                B.parse_sizes(bad)

    def test_format(self):
        self.assertEqual([B.fmt_n(x) for x in (999, 1000, 100000, 200000, 250000, 1500)],
                         ["999", "10^3", "10^5", "2·10^5", "2,5·10^5", "1500"])
        self.assertEqual(B.fmt_time(0.0123), "12 ms")
        self.assertEqual(B.fmt_time(1.5), "1,50 s")


class TestComplexity(unittest.TestCase):
    def test_classes(self):
        ns = [1000, 10000, 100000, 1000000]
        cases = {"O(n log n)": lambda n: 2e-8 * n * math.log2(n) + 0.001,
                 "O(n²)": lambda n: 1e-9 * n * n + 0.001,
                 "O(n)": lambda n: 3e-7 * n + 0.002}
        for label, f in cases.items():
            r = B.estimate_complexity([(n, f(n)) for n in ns])
            self.assertEqual(r["label"], label)
        r = B.estimate_complexity([(n, 1e-9 * n * n + 0.001) for n in ns])
        self.assertAlmostEqual(r["slope"], 2.0, delta=0.15)
        self.assertEqual(r["confidence"], "high")
        self.assertEqual(len(r["fit"]), 4)

    def test_too_little_data(self):
        self.assertIsNone(B.estimate_complexity([(1000, 0.1)])["label"])
        r = B.estimate_complexity([(1000, 0.001), (2000, 0.001)])
        self.assertEqual(r["confidence"], "low")


class TestVerdict(unittest.TestCase):
    def row(self, n, ta, tb, va="OK", vb="OK", same=True):
        return {"n": n, "a": {"verdict": va, "time": ta}, "b": {"verdict": vb, "time": tb}, "same": same}

    def test_b_faster_from_threshold(self):
        rows = [self.row(1000, 0.002, 0.002), self.row(10000, 0.01, 0.004), self.row(100000, 0.5, 0.15),
                self.row(200000, 2.0, 0.59)]
        v = B.make_verdict(rows, True, 5)
        self.assertIn("B jest ~3,4× szybsze dla n ≥ 10^4", v["headline"])

    def test_similar_and_mismatch_and_tle(self):
        rows = [self.row(1000, 0.010, 0.011), self.row(10000, 0.10, 0.095, same=False)]
        v = B.make_verdict(rows, True, 5)
        self.assertIn("podobnie", v["headline"])
        self.assertTrue(any(m["tone"] == "danger" and "RÓŻNIĄ" in m["text"] for m in v["messages"]))
        rows = [self.row(1000, 0.01, 0.01), self.row(10000, None, 0.02, va="TLE")]
        v = B.make_verdict(rows, True, 2)
        self.assertIn("A nie mieści się w limicie od n = 10^4", v["headline"])
        self.assertTrue(any("limit czasu" in m["text"] for m in v["messages"]))

    def test_single(self):
        rows = [{"n": 100, "a": {"verdict": "OK", "time": 0.01}}, {"n": 1000, "a": {"verdict": "OK", "time": 0.1}}]
        self.assertIn("10^3", B.make_verdict(rows, False, 5)["headline"])


class TestJob(unittest.TestCase):
    def test_python_pair(self):
        body = {"a": PY_SLOW, "b": PY_FAST, "lang_a": "python", "lang_b": "python", "generator": GEN,
                "sizes": "100, 400, 1600", "repeats": 1, "timeout": 20}
        cfg = B._config(body)
        self.assertEqual(cfg["gen_kind"], "dsl")
        job, res = jobs.run_sync(B._work, cfg)
        self.assertEqual(res["status"], "done")
        self.assertEqual([r["n"] for r in res["rows"]], [100, 400, 1600])
        self.assertTrue(all(r["same"] for r in res["rows"]))
        self.assertTrue(all(r["a"]["verdict"] == "OK" and r["b"]["verdict"] == "OK" for r in res["rows"]))
        self.assertGreater(res["rows"][-1]["a"]["time"], res["rows"][-1]["b"]["time"])
        self.assertTrue(res["verdict"]["headline"])
        self.assertEqual({s["name"] for s in res["series"] if not s.get("dashed")}, {"A", "B"})
        self.assertTrue(any(e["type"] == "size" for e in job.events))

    def test_mismatch_detected(self):
        wrong = PY_FAST.replace("print(go(a)[1])", "print(go(a)[1] + (1 if n > 150 else 0))")
        cfg = B._config({"a": PY_SLOW, "b": wrong, "lang_a": "python", "lang_b": "python", "generator": GEN,
                         "sizes": [50, 200], "repeats": 1})
        _, res = jobs.run_sync(B._work, cfg)
        self.assertEqual([r["same"] for r in res["rows"]], [True, False])
        self.assertIn("diff", res["rows"][1])
        self.assertTrue(any("RÓŻNIĄ" in m["text"] for m in res["verdict"]["messages"]))

    def test_tle_skips_larger(self):
        loop = "import sys\nn = int(sys.stdin.read().split()[0])\nwhile n > 300:\n    pass\nprint(n)\n"
        cfg = B._config({"a": loop, "lang_a": "python", "generator": "print size", "sizes": [10, 1000, 2000],
                         "repeats": 1, "timeout": 0.5})
        _, res = jobs.run_sync(B._work, cfg)
        vs = [r["a"]["verdict"] for r in res["rows"]]
        self.assertEqual(vs[0], "OK")
        self.assertEqual(vs[1], "TLE")
        self.assertEqual(len(res["rows"]), 2)        # obie (jedyne) rozwiązania padły → koniec

    @unittest.skipUnless(config.CXX, "brak kompilatora C++")
    def test_cpp_with_code_generator(self):
        a = "#include <bits/stdc++.h>\nusing namespace std;\nint main(){long long n,s=0,x;cin>>n;for(int i=0;i<n;i++){cin>>x;s+=x;}cout<<s<<endl;}\n"
        gen = "import sys\nn = int(sys.argv[1])\nprint(n)\nprint(' '.join(str(i % 7) for i in range(n)))\n"
        cfg = B._config({"a": a, "b": a.replace("endl", "'\\n'"), "generator": gen, "gen_lang": "python",
                         "sizes": "1e3, 1e4", "repeats": 2})
        self.assertEqual(cfg["gen_kind"], "code")
        _, res = jobs.run_sync(B._work, cfg)
        self.assertEqual(res["status"], "done", res)
        self.assertTrue(all(r["same"] for r in res["rows"]))
        self.assertEqual(res["langs"], {"a": "cpp", "b": "cpp"})

    def test_compile_error_reported(self):
        cfg = B._config({"a": "#include <cstdio>\nint main( { }", "generator": "print size", "sizes": [10]})
        job, res = jobs.run_sync(B._work, cfg)
        if not config.CXX:
            self.skipTest("brak kompilatora")
        self.assertEqual(res["status"], "compile_error")
        self.assertTrue(any(e["type"] == "compile_error" for e in job.events))

    def test_bad_config(self):
        with self.assertRaises(ApiError):
            B._config({"a": "", "generator": "print 1"})
        with self.assertRaises(ApiError):
            B._config({"a": "print(1)", "generator": "n = size\nprint m"})


class TestTemplates(unittest.TestCase):
    def test_templates_have_valid_generators(self):
        t = B.handle_templates({})["templates"]
        self.assertGreaterEqual(len(t), 3)
        for x in t:
            B.parse_sizes(x["sizes"])
            self.assertEqual(B._detect_gen_kind(x["generator"], x["gen_kind"]), "dsl")
            self.assertTrue(x["a"] and x["b"])


if __name__ == "__main__":
    unittest.main()

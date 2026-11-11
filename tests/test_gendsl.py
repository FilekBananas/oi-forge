"""Testy mini-języka generatorów (oiforge.core.gendsl) i modułu HTTP oiforge.tools.generators."""
import concurrent.futures as cf
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from oiforge import config
from oiforge.core import gendsl as G
from oiforge.core import randstruct as rs
from oiforge.core import runner

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = json.loads((ROOT / "data" / "gendsl_examples.json").read_text(encoding="utf-8"))["examples"]

FULL = r'''tests 4
seed 123
n = int(4, 8) + testno
m = n - 1 + int(0, 3)
print n, m
print tree(n)
print tree(n, "line", weights=(1, 9), shuffle=0)
print tree(n, "caterpillar", base=0)
print parents(n)
print graph(n, m)
print graph(n, n, directed=1, weights=5, connected=0)
print graph(3, 5, multi=1, loops=1, connected=0)
print dag(n, m, connected=1)
print bipartite(3, 4, 5, shift=1)
print forest(n, 2)
print grid(3, 5, "#.", p=0.3, path=1)
x = float(-1, 1, 3)
print x, x * 2 + 1, n / 3, 7 // 2, -7 // 2, -7 % 3, 2 ** 10, sqrt(n), 2 ** -2, 7.5 // 2, -7.5 % 2
print choice(1, 2, 3), choice("abc"), choice("A", "B", weights=(3, 1)), choice(1, 2, weights=(0.5, 1.5))
print char("a-z0-9"), string(6, "ab"), palindrome(5), periodic(7, 3), antihash(8)
a = seq(10, 1, 5, "nearly")
print seq(9, 1, 100, "zigzag"), seq(6, 1, 9, "repeats"), seq(5, 1, 9, "desc"), distinct(5, 1, 6), perm(5)
print sorted(a, desc=1) sep=","
print reverse(a), shuffle(a), unique(a), len(a), sum(a), min(a), max(3, 9, 2), count(a, 3)
print put(a, -1, 99), flat(tree(3)), range(3, 6), join(["a", 1, 2.5], "-"), str(3) + "x"
print abs(-5), floor(2.7), ceil(2.1), round(2.5), round(3.5), gcd(12, 18), lcm(4, 6)
print pairs(3, 1, n), interval(1, 10), points(3, 0, 5)
print queries(4, n, "point range_value pair none", 1, 100)
print matrix(2, 3, int(0, 9))
if chance(0.5) and n > 3 { print "A" } elif not (n < 2) { print "B" } else { print "C" }
k = 0
for i in 1..n {
    if i % 2 == 0 { continue }
    k += i
    if k > 10 { break }
}
for c in "xyz" { print c end="" }
print k, 1 <= k <= 100, "ab" in "cabd", 3 in a
w = 0
while w < 3 { w += 1 }
repeat 2 { print int(1, 6) + int(1, 6), array(2, choice(7, 8)) }
assert n >= 3, "n za małe"
'''


def split_tests(text):
    return [t.strip("\n") for t in text.split("\n\n") if t.strip()]


def run_lines(src, **kw):
    return G.run(src, **kw)["output"]


class TestSyntaxErrors(unittest.TestCase):
    def err(self, src):
        with self.assertRaises(G.DslError) as cm:
            G.parse(src)
        return cm.exception

    def test_positions(self):
        e = self.err("n = int(1, 10)\nprint arary(n, 1)")
        self.assertEqual((e.line, e.col), (2, 7))
        self.assertIn("array", e.message)                       # podpowiedź
        e = self.err("n = int(1, 10)\n\nx = graph(n, n, conected=1)")
        self.assertEqual(e.line, 3)
        self.assertIn("connected", e.message)
        e = self.err("print m")
        self.assertEqual((e.line, e.col), (1, 7))
        self.assertIn("Nieznana zmienna", e.message)
        e = self.err("repeat 5 print 1")
        self.assertEqual(e.line, 1)
        self.assertIn("{", e.message)
        e = self.err("n = int(1, 10\nprint n")
        self.assertEqual(e.line, 1)
        self.assertIn(")", e.message)
        e = self.err('a = "abc')
        self.assertIn("Niezamknięty napis", e.message)
        e = self.err("x = 1\n  y = x @ 2")
        self.assertEqual((e.line, e.col), (2, 9))

    def test_messages(self):
        self.assertIn("poza pętlą", self.err("break").message)
        self.assertIn("int(lo, hi)", self.err("n = int(5)").message)
        self.assertIn("klamrach", self.err("if 1:\n print 1").message)
        self.assertIn("=", self.err("n == 5").message)
        self.assertIn("put", self.err("a = [1, 2]\na[0] = 5").message)
        self.assertIn("tylko raz", self.err("tests 5\ntests 6").message)
        self.assertIn("najwyższym poziomie", self.err("repeat 2 {\n tests 5\n}").message)
        self.assertIn("for i in 1..n", self.err("for (i = 0; i < n; i++) { }").message)
        self.assertIn("cudzysłowie", self.err('print tree(5, line)').message)
        self.assertIn("losowa", self.err("tests int(1, 5)").message)

    def test_check_endpoint_shape(self):
        r = G.check("n = int(1, 10)\nprint n m")
        self.assertFalse(r["valid"])
        self.assertEqual(r["errors"][0]["line"], 2)
        r = G.check("tests 7\nseed 3\nn = size\nprint n")
        self.assertTrue(r["valid"])
        self.assertEqual(r["info"]["tests"], 7)
        self.assertEqual(r["info"]["seed"], 3)
        self.assertTrue(r["info"]["uses_size"])
        r = G.check("n = 5")
        self.assertTrue(r["valid"])
        self.assertTrue(any("nic nie wypisuje" in w["message"] for w in r["warnings"]))


class TestSemantics(unittest.TestCase):
    def test_spec_example(self):
        src = "tests 1000\nn = int(1, 10)\na = array(n, int(-5, 5))\nprint n\nprint a\n"
        res = G.run(src, seed=5)
        tests = split_tests(res["output"])
        self.assertEqual(len(tests), 1000)
        self.assertEqual(res["tests_count"], 1000)
        for t in tests:
            lines = t.split("\n")
            n = int(lines[0])
            a = list(map(int, lines[1].split()))
            self.assertTrue(1 <= n <= 10 and len(a) == n and all(-5 <= x <= 5 for x in a))
        # format Testerki: dokładnie jedna pusta linia między testami, na końcu newline
        self.assertNotIn("\n\n\n", res["output"])
        self.assertTrue(res["output"].endswith("\n"))
        try:
            from oiforge.tools.tester import split_tests as tester_split
        except Exception:  # noqa: BLE001
            tester_split = None
        if tester_split:
            self.assertEqual(len(tester_split(res["output"])[0]), 1000)

    def test_determinism_and_seed(self):
        src = "tests 20\nprint array(5, int(1, 1e9))"
        self.assertEqual(run_lines(src, seed=1), run_lines(src, seed=1))
        self.assertNotEqual(run_lines(src, seed=1), run_lines(src, seed=2))
        self.assertEqual(run_lines("seed 77\n" + src), run_lines(src, seed=77))
        self.assertEqual(run_lines("seed 77\n" + src, seed=5), run_lines(src, seed=5))   # seed z żądania wygrywa
        # pierwsze testy nie zależą od liczby testów (podgląd = początek pełnego wyjścia)
        self.assertTrue(run_lines(src, seed=9).startswith(run_lines(src, seed=9, tests=3)))

    def test_testno_size_ntests(self):
        out = run_lines("tests 3\nprint testno, ntests, size", vars={"size": 42})
        self.assertEqual(out, "1 3 42\n\n2 3 42\n\n3 3 42\n")
        self.assertEqual(run_lines("tests size // 5\nprint 1", vars={"size": 15}).count("1"), 3)

    def test_arithmetic(self):
        out = run_lines("print 7 // 2, -7 // 2, -7 % 3, 7 % -3, 2 ** 10, 10 / 4, 1e5, 2e5 + 1, 100_000, 3 - -2")
        self.assertEqual(out, "3 -4 2 -2 1024 2.500000 100000 200001 100000 5\n")
        out = run_lines("x = 1.50\nprint x, x * 2, x + 1, round(2.5), round(3.5), floor(-0.5), ceil(-0.5), abs(-3)")
        self.assertEqual(out, "1.50 3.00 2.50 2 4 -1 0 3\n")
        self.assertEqual(run_lines('print "ab" + "c", "xy" * 3, [1, 2] + [3], len("abc"), "abc"[-1]'),
                         "abc xyxyxy 1 2 3 3 c\n")
        self.assertEqual(run_lines("print 1 < 2, 2 < 1, 1 <= 1 <= 2, not 0, 1 and 0, 0 or 5, 3 in [1, 3]"),
                         "1 0 1 1 0 1 1\n")
        for bad, msg in (("print 2 ** 63", "64"), ("print 9223372036854775807 + 1", "64"), ("print 1 // 0", "zero"),
                         ('print "a" + 1', "Nie można"), ("print [1, 2][5]", "poza zakresem"),
                         ("print int(1, 10 / 2)", "//"), ("x = sqrt(-1)\nprint x", "ujemnej")):
            with self.assertRaises(G.DslError, msg=bad) as cm:
                G.run(bad)
            self.assertIn(msg, cm.exception.message, bad)

    def test_print_options_and_empty_lists(self):
        self.assertEqual(run_lines('print [1, 2, 3] sep=","'), "1,2,3\n")
        self.assertEqual(run_lines('print 1 end=""\nprint 2'), "12\n")
        self.assertEqual(run_lines("print 5\nprint tree(1)\nprint []"), "5\n")           # puste listy nic nie wypisują
        self.assertEqual(run_lines("print [[1, 2], [3, 4]]"), "1 2\n3 4\n")
        self.assertEqual(run_lines('print rows(["ab", "cd"])'), "ab\ncd\n")
        self.assertEqual(run_lines('print 2, interval(5, 5)'), "2 5 5\n")
        r = G.run('tests 2\nprint 1\nprint ""\nprint 2')
        self.assertTrue(any("pustą linię" in w for w in r["warnings"]))

    def test_control_flow(self):
        src = "s = 0\nfor i in 1..10 {\n  if i == 3 { continue }\n  if i > 6 { break }\n  s += i\n}\nprint s"
        self.assertEqual(run_lines(src), "18\n")
        self.assertEqual(run_lines("x = 0\nwhile x < 100 { x = x * 2 + 1 }\nprint x"), "127\n")
        self.assertEqual(run_lines("c = 0\nrepeat 4 { repeat 3 { c += 1 } }\nprint c"), "12\n")
        self.assertEqual(run_lines('for ch in "abc" { print ch end="." }\nprint ""'), "a.b.c.\n")
        self.assertEqual(run_lines("if 0 { print 1 } else if 0 { print 2 } else { print 3 }"), "3\n")
        self.assertEqual(run_lines("if 0 {\n print 1\n}\nelse {\n print 2\n}"), "2\n")

    def test_runtime_error_position_and_test(self):
        with self.assertRaises(G.DslError) as cm:
            G.run("tests 5\nn = 3 + testno\nif testno == 4 {\n  a = distinct(n, 1, 5)\n}\nprint n")
        e = cm.exception
        self.assertEqual((e.line, e.test), (4, 4))
        self.assertIn("różnych", e.message)
        with self.assertRaises(G.DslError) as cm:
            G.run('n = 3\nassert n > 5, "za małe n"\nprint n')
        self.assertIn("za małe n", cm.exception.message)

    def test_limits(self):
        with self.assertRaises(G.DslError) as cm:
            G.run("x = 0\nwhile 1 { x += 1 }", max_steps=100_000)
        self.assertIn("limit", cm.exception.message)
        with self.assertRaises(G.DslError) as cm:
            G.run("tests 100\nprint string(10000, \"a\")", max_output=50_000)
        self.assertIn("limit", cm.exception.message)
        with self.assertRaises(G.DslError):
            G.run("s = \"ab\"\nrepeat 40 { s = s + s }\nprint len(s)")


class TestStructures(unittest.TestCase):
    def test_tree_output(self):
        for shape in ("random", "line", "star", "binary", "caterpillar", "deep", "parent"):
            out = run_lines(f'tests 50\nn = int(1, 30)\nprint n\nprint tree(n, "{shape}")', seed=4)
            for t in split_tests(out):
                lines = t.split("\n")
                n = int(lines[0])
                edges = [tuple(map(int, ln.split())) for ln in lines[1:]]
                self.assertEqual(len(edges), n - 1)
                self.assertTrue(rs.is_tree(n, edges), (shape, t))

    def test_graph_dag_perm_distinct_grid(self):
        out = run_lines("tests 40\nn = int(1, 12)\nm = int(n - 1, n * (n - 1) // 2)\nprint n, m\nprint graph(n, m)", seed=2)
        for t in split_tests(out):
            lines = t.split("\n")
            n, m = map(int, lines[0].split())
            edges = [tuple(map(int, ln.split())) for ln in lines[1:]]
            self.assertEqual(len(edges), m)
            self.assertTrue(rs.is_connected(n, edges))
        out = run_lines("tests 40\nn = int(1, 10)\nm = int(0, n * (n - 1) // 2)\nprint n, m\nprint dag(n, m)", seed=3)
        for t in split_tests(out):
            lines = t.split("\n")
            n, m = map(int, lines[0].split())
            edges = [tuple(map(int, ln.split())) for ln in lines[1:]]
            self.assertEqual(len(edges), m)
            self.assertFalse(rs.has_cycle(n, edges, directed=True))
        out = run_lines("tests 30\nn = int(1, 20)\nprint perm(n)\nprint distinct(n, 1, 25)", seed=5)
        for t in split_tests(out):
            p, d = t.split("\n")
            p, d = list(map(int, p.split())), list(map(int, d.split()))
            self.assertTrue(rs.is_permutation(p))
            self.assertEqual(len(set(d)), len(d))
        out = run_lines('print grid(4, 7, "#.", p=0.5, path=1)', seed=1).rstrip("\n").split("\n")
        self.assertEqual(len(out), 4)
        self.assertTrue(all(len(r) == 7 for r in out))
        self.assertEqual((out[0][0], out[-1][-1]), (".", "."))

    def test_parents_and_queries(self):
        out = run_lines("n = 30\nprint parents(n)", seed=1).split()
        self.assertEqual(len(out), 29)
        self.assertTrue(all(1 <= int(p) < i for i, p in enumerate(out, start=2)))
        out = run_lines('print queries(50, 10, "point_value range")', seed=1).strip().split("\n")
        self.assertEqual(len(out), 50)
        for ln in out:
            v = list(map(int, ln.split()))
            self.assertIn(v[0], (1, 2))
            if v[0] == 2:
                self.assertTrue(1 <= v[1] <= v[2] <= 10)


class TestExamples(unittest.TestCase):
    def test_examples_count_and_categories(self):
        self.assertGreaterEqual(len(EXAMPLES), 15)
        cats = {e["category"] for e in EXAMPLES}
        for c in ("Tablice", "Grafy", "Drzewa", "Siatki", "Napisy", "Zapytania", "Multitest"):
            self.assertIn(c, cats)
        self.assertEqual(len({e["id"] for e in EXAMPLES}), len(EXAMPLES))

    def test_examples_run_cleanly(self):
        for e in EXAMPLES:
            res = G.run(e["source"], seed=11)
            self.assertGreater(res["tests_count"], 0, e["id"])
            self.assertEqual(len(split_tests(res["output"])), res["tests_count"], e["id"])
            self.assertFalse([w for w in res["warnings"] if "pustą linię" in w], e["id"])

    def test_examples_export(self):
        for e in EXAMPLES:
            self.assertIn("int main", G.export(e["source"], "cpp"))
            self.assertIn("def main", G.export(e["source"], "python"))


def _run_py(code: str, args):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "gen.py"
        p.write_text(code, encoding="utf-8")
        r = subprocess.run([sys.executable, str(p), *map(str, args)], capture_output=True, text=True, timeout=120,
                           encoding="utf-8")
        return r


class TestExportPython(unittest.TestCase):
    def test_identical_to_interpreter(self):
        for src, seed in ((FULL, 123), (FULL, 9)):
            r = _run_py(G.export(src, "python"), [seed])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, G.run(src, seed=seed)["output"])

    def test_examples_identical(self):
        for e in EXAMPLES:
            r = _run_py(G.export(e["source"], "python"), [5, 7])
            self.assertEqual(r.returncode, 0, (e["id"], r.stderr))
            self.assertEqual(r.stdout, G.run(e["source"], seed=5, tests=7)["output"], e["id"])

    def test_args_and_size(self):
        src = "tests 4\nprint testno, ntests, size, int(1, 1000)"
        r = _run_py(G.export(src, "python"), [3, 2, 50])
        self.assertEqual(r.stdout, G.run(src, seed=3, tests=2, vars={"size": 50})["output"])
        r = _run_py(G.export("n = 2\nassert n > 5, \"źle\"\nprint n", "python"), [1])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("źle", r.stderr)

    def test_reserved_names(self):
        src = "int = 5\nlambda = int + 1\nG = [1, 2]\nprint int, lambda, G, int(1, 1)"
        r = _run_py(G.export(src, "python"), [1])
        self.assertEqual(r.stdout, G.run(src)["output"])


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestExportCpp(unittest.TestCase):
    def _build(self, src):
        cr = runner.compile_cpp(G.export(src, "cpp"), mode="release", std="c++17")
        self.assertTrue(cr.ok, cr.stderr[:3000])
        return cr.exe

    def test_full_program_identical(self):
        exe = self._build(FULL)
        for seed in (123, 4):
            rr = runner.run([exe], "", timeout=30, args=[seed])
            self.assertEqual(rr.verdict, "OK", rr.stderr)
            self.assertEqual(rr.stdout, G.run(FULL, seed=seed)["output"])
        rr = runner.run([exe], "", timeout=30, args=[7, 2])
        self.assertEqual(rr.stdout, G.run(FULL, seed=7, tests=2)["output"])

    def test_examples_compile_and_match(self):
        picked = [e for e in EXAMPLES if e["id"] in ("array-basic", "tree-shapes", "graph-dijkstra", "grid-maze",
                                                     "strings-palindromes", "queries-segment-tree", "graph-dsu",
                                                     "numbers-yes-no", "array-distributions")]
        self.assertGreaterEqual(len(picked), 8)
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            exes = list(ex.map(lambda e: runner.compile_cpp(G.export(e["source"], "cpp"), std="c++17"), picked))
        for e, cr in zip(picked, exes):
            self.assertTrue(cr.ok, (e["id"], cr.stderr[:2000]))
            rr = runner.run([cr.exe], "", timeout=30, args=[21, 30])
            self.assertEqual(rr.verdict, "OK", (e["id"], rr.stderr))
            self.assertEqual(rr.stdout, G.run(e["source"], seed=21, tests=30)["output"], e["id"])

    def test_runtime_error_and_size(self):
        exe = self._build("n = size\nassert n < 100, \"za duże\"\nprint n, distinct(n, 1, n)")
        rr = runner.run([exe], "", timeout=10, args=[1, 1, 50])
        self.assertEqual(rr.stdout, G.run("n = size\nassert n < 100, \"za duże\"\nprint n, distinct(n, 1, n)",
                                          seed=1, vars={"size": 50})["output"])
        rr = runner.run([exe], "", timeout=10, args=[1, 1, 500])
        self.assertNotEqual(rr.exit_code, 0)
        self.assertIn("za duże", rr.stderr)


class TestHandlers(unittest.TestCase):
    def test_run_check_export_struct(self):
        from oiforge.server_api import ApiError
        from oiforge.tools import generators as T
        r = T.handle_run({"source": "tests 10\nprint int(1, 5)", "seed": 3, "preview": 4})
        self.assertEqual(r["tests_count"], 10)
        self.assertEqual(len(r["preview"]), 4)
        self.assertTrue(r["output"].startswith(r["preview"][0]))
        r = T.handle_run({"source": "tests 10\nprint int(1, 5)", "tests": 2, "include_output": False})
        self.assertIsNone(r["output"])
        self.assertEqual(r["tests_count"], 2)
        with self.assertRaises(ApiError) as cm:
            T.handle_run({"source": "print x"})
        self.assertEqual(cm.exception.extra["errors"][0]["line"], 1)
        c = T.handle_check({"source": "n = int(1,\nprint n"})
        self.assertFalse(c["valid"])
        ex = T.handle_export({"source": "print 1", "lang": "python"})
        self.assertEqual(ex["filename"], "gen.py")
        s = T.handle_struct({"kind": "tree", "n": 10, "seed": 1})
        self.assertTrue(rs.is_tree(10, s["edges"]))
        s = T.handle_struct({"kind": "graph", "n": 6, "m": 9, "directed": True})
        self.assertEqual(len(s["edges"]), 9)
        s = T.handle_struct({"kind": "grid_graph", "rows": 3, "cols": 3})
        self.assertEqual(s["n"], 9)
        self.assertGreaterEqual(len(T.handle_examples({})["examples"]), 15)
        ref = T.handle_reference({})
        names = {f["name"] for f in ref["functions"]}
        for f in ("int", "array", "tree", "graph", "dag", "grid", "perm", "distinct", "queries"):
            self.assertIn(f, names)


if __name__ == "__main__":
    unittest.main()

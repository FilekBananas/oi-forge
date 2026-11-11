"""Testy generatora testów brzegowych (oiforge.tools.edgecases)."""
import glob
import random
import unittest

from oiforge import config
from oiforge.core import runner
from oiforge.core import statement as ST
from oiforge.tools import edgecases as EC
from tests.test_autosolver import PAIRS
from tests.test_statement import PDF_GRAPH, PLAIN_ARRAY

STRING_TASK = """Palindromy
Sprawdź, czy słowo jest palindromem.
Wejście
W pierwszym wierszu znajduje się słowo s złożone z małych liter alfabetu angielskiego (1 ≤ |s| ≤ 100 000).
Wyjście
TAK albo NIE.
Przykład
Wejście:
kajak
Wyjście:
TAK
"""

TREE_TASK = """Drzewo
Dane jest drzewo o n wierzchołkach. Policz rozmiar poddrzewa każdego wierzchołka (korzeń to 1).
Wejście
W pierwszym wierszu liczba n (1 ≤ n ≤ 200 000). W kolejnych n − 1 wierszach po dwie liczby a, b (1 ≤ a, b ≤ n) — krawędzie drzewa.
Wyjście
n liczb.
Przykład
Wejście:
3
1 2
1 3
Wyjście:
3
1
1
"""

STATEMENTS = sorted(glob.glob(str(config.PROGRAMS_DIR / "*" / "*" / "statements" / "*.txt")))


class TestEdgeTests(unittest.TestCase):
    def check_all_valid(self, text):
        p = ST.parse(text)
        tests = EC.edge_tests(p)
        self.assertTrue(tests)
        for t in tests:
            c = ST.check_format(p["input_format"], t["input"])
            self.assertTrue(c["ok"] or c["reason"] == "nieznana liczba powtórzeń", (t["title"], t["input"], c))
            self.assertTrue(t["why"])
        return p, tests

    def test_graph(self):
        p, tests = self.check_all_valid(PDF_GRAPH)
        titles = " | ".join(t["title"] for t in tests)
        self.assertIn("ścieżka", titles)
        self.assertIn("Gwiazda", titles)
        self.assertIn("niespójny", titles)
        self.assertIn("m = 0", titles)
        self.assertNotIn("Pętla własna", titles)          # treść mówi a ≠ b
        first = tests[0]["input"].split("\n")[0].split()
        self.assertEqual(first, ["1", "0"])                # n minimalne, m = 0

    def test_array_overflow_and_patterns(self):
        p, tests = self.check_all_valid(PLAIN_ARRAY)
        tags = {g for t in tests for g in t["tags"]}
        self.assertIn("overflow", tags)
        self.assertIn("negative", tags)
        over = next(t for t in tests if "overflow" in t["tags"])
        self.assertIn("1000000000", over["input"])
        eq = next(t for t in tests if t["title"].startswith("Wszystkie elementy równe"))
        vals = eq["input"].split("\n")[1].split()
        self.assertEqual(len(set(vals)), 1)

    def test_string_and_tree(self):
        _, tests = self.check_all_valid(STRING_TASK)
        one = next(t for t in tests if t["title"] in ("Napis jednoznakowy", "Minimalny rozmiar (s = 1)")
                   or t["title"].startswith("Minimalny"))
        self.assertEqual(len(one["input"].strip()), 1)
        p, tests = self.check_all_valid(TREE_TASK)
        titles = [t["title"] for t in tests]
        self.assertTrue(any("ścieżka" in t for t in titles))
        self.assertFalse(any("niespójny" in t for t in titles))    # drzewo jest zawsze spójne

    def test_pairs_k_scalar(self):
        self.check_all_valid(PAIRS)


class TestGridEdgeTests(unittest.TestCase):
    """Plansze: brak drogi, start = meta, wąż, same ściany, szachownica; start/meta dokładnie raz."""

    def _edge(self, name):
        from tests import bench_smart as B
        p = ST.parse(B.statement(name))
        return p, EC.edge_tests(p)

    def test_corner_maze(self):
        from tests import bench_smart as B
        p, tests = self._edge("labirynt")
        titles = " | ".join(t["title"] for t in tests)
        for want in ("brak drogi", "Wąski korytarz", "ściany", "Szachownica", "bez przeszkód"):
            self.assertIn(want, titles)
        self.assertGreaterEqual(len(tests), 6)
        walled = next(t for t in tests if "brak drogi" in t["title"])
        self.assertEqual(B.TASKS["labirynt"]["ref"](walled["input"]), "-1")
        one = next(t for t in tests if t["input"].startswith("1 1\n"))
        self.assertEqual(B.TASKS["labirynt"]["ref"](one["input"]), "0")          # start = meta
        snake = next(t for t in tests if "korytarz" in t["title"])
        self.assertEqual(B.TASKS["labirynt"]["ref"](snake["input"]), "16")       # wąż 5×5: 5+4+… pól ścieżki
        for t in tests:
            c = ST.check_format(p["input_format"], t["input"])
            self.assertTrue(c["ok"], (t["title"], t["input"]))

    def test_letters_maze_has_start_and_goal_once(self):
        from tests import bench_smart as B
        p, tests = self._edge("mysz")
        self.assertTrue(any("brak drogi" in t["title"] for t in tests))
        self.assertTrue(any("1×2" in t["title"] for t in tests))
        for t in tests:
            grid = "".join(t["input"].split("\n")[1:])
            self.assertEqual((grid.count("M"), grid.count("S")), (1, 1), (t["title"], t["input"]))
        walled = next(t for t in tests if "brak drogi" in t["title"])
        self.assertEqual(B.TASKS["mysz"]["ref"](walled["input"]), "NIE")

    def test_regions_checkerboard(self):
        from tests import bench_smart as B
        _, tests = self._edge("plamy")
        chk = next(t for t in tests if t["title"] == "Szachownica")
        self.assertEqual(B.TASKS["plamy"]["ref"](chk["input"]), "1")     # 8 kierunków: jedna plama
        _, tests = self._edge("wyspy")
        chk = next(t for t in tests if t["title"] == "Szachownica")
        self.assertEqual(B.TASKS["wyspy"]["ref"](chk["input"]), "8")     # 4 kierunki: osobne wyspy
        self.assertFalse(any("brak drogi" in t["title"] for t in tests))  # liczenie wysp to nie szukanie drogi

    @unittest.skipUnless(config.CXX, "brak kompilatora C++")
    def test_grid_generators(self):
        from tests import bench_smart as B
        p = ST.parse(B.statement("mysz"))
        for mode, seed in (("small", 1), ("small", 2), ("max", 3)):
            prog = runner.prepare(EC.generator_cpp(p, mode))
            self.assertTrue(prog.ok, prog.compile.stderr if prog.compile else prog.error)
            out = runner.run(prog.cmd, "", timeout=10, args=[str(seed)]).stdout
            c = ST.check_format(p["input_format"], out)
            self.assertTrue(c["ok"], (mode, out[:200], c))
            grid = "".join(out.split("\n")[1:])
            self.assertEqual((grid.count("M"), grid.count("S")), (1, 1), mode)
        n, m = map(int, out.split("\n")[0].split())
        self.assertEqual((n, m), (500, 500))                          # test maksymalny


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestGenerators(unittest.TestCase):
    def run_gen(self, text, mode, seed=3):
        p = ST.parse(text)
        code = EC.generator_cpp(p, mode)
        prog = runner.prepare(code)
        self.assertTrue(prog.ok, prog.compile.stderr if prog.compile else prog.error)
        rr = runner.run(prog.cmd, "", timeout=10, args=[str(seed)])
        self.assertEqual(rr.verdict, "OK", rr.stderr)
        return p, rr.stdout

    def test_small_generators_produce_valid_input(self):
        for text in (PDF_GRAPH, PLAIN_ARRAY, STRING_TASK, TREE_TASK, PAIRS):
            for seed in (1, 2, 3):
                p, out = self.run_gen(text, "small", seed)
                c = ST.check_format(p["input_format"], out)
                self.assertTrue(c["ok"], (p["title"], out[:200], c))

    def test_max_generator_sizes(self):
        p, out = self.run_gen(PLAIN_ARRAY, "max")
        lines = out.split("\n")
        self.assertEqual(lines[0], "1000000")
        self.assertGreater(len(lines[1].split()), 3 * 10**5)     # runner obcina wyjście do ~4 MB
        p, out = self.run_gen(TREE_TASK, "max")
        self.assertEqual(len([ln for ln in out.split("\n") if ln.strip()]), 200000)

    def test_run_user_code_on_tests(self):
        code = "#include <bits/stdc++.h>\nint main(){long long n,s=0;std::cin>>n;for(long long i=0;i<n;i++){long long x;std::cin>>x;s+=x;}std::cout<<s<<\"\\n\";}"
        r = EC.handle_edgecases({"statement": PLAIN_ARRAY, "code": code})
        self.assertTrue(r["run"]["ok"])
        self.assertTrue(all(x["verdict"] == "OK" for x in r["run"]["results"]))


@unittest.skipUnless(len(STATEMENTS) >= 50 and config.CXX, "brak prawdziwych treści albo kompilatora")
class TestRealStatements(unittest.TestCase):
    def test_edge_tests_valid_on_real_statements(self):
        ok = total = 0
        for f in STATEMENTS:
            with open(f, encoding="utf-8", errors="replace") as fh:
                p = ST.parse(fh.read())
            if not p["format_check"]["ok"]:
                continue
            for t in EC.edge_tests(p):
                total += 1
                c = ST.check_format(p["input_format"], t["input"])
                ok += c["ok"] or c["reason"] == "nieznana liczba powtórzeń"
        self.assertGreater(total, 300)
        self.assertGreaterEqual(ok / total, 0.97)

    def test_some_generators_compile(self):
        rnd = random.Random(5)
        files = rnd.sample(STATEMENTS, 12)
        bad = []
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                p = ST.parse(fh.read())
            prog = runner.prepare(EC.generator_cpp(p, "small"))
            if not prog.ok:
                bad.append(f)
        self.assertEqual(bad, [])



class TestBigNumberEdges(unittest.TestCase):
    def test_bignum_edges_are_valid(self):
        from tests.test_statement import BIG_DIGITS
        tests = EC.edge_tests(ST.parse(BIG_DIGITS))
        vals = [t["input"].strip() for t in tests]
        for v in vals:
            self.assertTrue(v.isdigit() and (v == "0" or v[0] != "0"), v[:30])
            self.assertTrue(len(v) <= 200000 or v == "1" + "0" * 200000, len(v))
        for want in ("1", "9", "10", "1000000", "9000000", "18446744073709551616"):
            self.assertIn(want, vals)
        self.assertTrue(any(set(v) == {"9"} and len(v) > 19 for v in vals))          # same dziewiątki
        self.assertTrue(any(set(v) <= {"0", "1"} and len(v) >= 10 for v in vals))    # tylko 0 i 1
        big = [t for t in tests if t.get("big")]
        self.assertTrue(big and all(len(t["input_preview"]) < 200 for t in big))     # maksymalna długość
        self.assertIn("bignum", tests[0]["tags"])

    def test_bignum_generator_compiles(self):
        from tests.test_statement import BIG_DIGITS
        code = EC.generator_cpp(ST.parse(BIG_DIGITS), "small")
        self.assertIn("string n", code)
        if config.CXX:
            prog = runner.prepare(code)
            self.assertTrue(prog.ok, prog.compile.stderr if prog.compile else prog.error)
            out = runner.run(prog.cmd, "", args=["5"]).stdout.strip()
            self.assertTrue(out.isdigit() and out[0] != "0")


if __name__ == "__main__":
    unittest.main()

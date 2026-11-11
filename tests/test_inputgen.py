"""Testy wnioskowania formatu wejścia, generowania testów i shrinkingu (oiforge.core.inputgen)."""
import glob
import os
import random
import unittest
from pathlib import Path

from oiforge.core import inputgen as ig

ROOT = Path(__file__).resolve().parent.parent
STATEMENTS = sorted(glob.glob(str(ROOT / "user_data/programs/*/*/statements/*.txt")) +
                    glob.glob(str(ROOT / "user_data/programs/*/*/statements/*.html")))


def kinds(spec):
    return [it["kind"] for it in ig._iter_items(spec["items"])]


class SamplesAndHintsTest(unittest.TestCase):
    def test_normalize_pdf_blank_lines(self):
        self.assertEqual(ig.normalize_sample("2 5\r\n\r\n3 4\n\n1 3\n\n"), "2 5\n\n3 4\n\n1 3")
        self.assertEqual(ig._tok_lines("2 5\n\n3 4\n\n1 3"), [["2", "5"], ["3", "4"], ["1", "3"]])

    def test_extract_pdf_samples(self):
        text = ("Zadanie\n\nWejście\n\nW pierwszym wierszu n.\n\nPrzykłady\n\nWejście dla testu abc0a:\n\n3\n\n1 2 3\n\n"
                "Wyjście dla testu abc0a:\n\n6\n\nWejście dla testu abc0b:\n\n1\n\n5\n\nWyjście dla testu abc0b:\n\n5\n\n"
                "Zadanie\n\n© 2025 XIV LO\n")
        s = ig.extract_samples(text)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]["input"], "3\n1 2 3")
        self.assertEqual(s[0]["output"], "6")
        self.assertEqual(s[1]["name"], "abc0b")

    def test_extract_html_samples(self):
        text = "<h2>Przykład</h2><p>Dla danych wejściowych:</p><pre>4\n1 2\n</pre><p>poprawną odpowiedzią jest:</p><pre>3\n</pre>"
        s = ig.extract_samples(text)
        self.assertEqual(s, [{"input": "4\n1 2", "output": "3", "name": "przykład 1"}])

    def test_extract_side_by_side(self):
        text = ("Wejście\n\nW pierwszym wierszu n (1 ≤ n ≤ 100), w kolejnych n wierszach pary.\n\nWyjście\n\n"
                "Jedna liczba.\n\nPrzykład\n\nWejście Wyjście\n\n3\n\n1 2\n\n3 4\n\n5 6\n\n21\n\n1/1 Zadanie\n")
        s = ig.extract_samples(text)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["input"], "3\n1 2\n3 4\n5 6")
        self.assertEqual(s[0]["output"], "21")

    def test_constraints_from_pdf_text(self):
        text = ("Wejście\n\nW pierwszej linii dwie liczby całkowite: n, m (1 ≤ n ≤ 100 000, 0 ≤ m ≤ 1 000 000).\n\n"
                "W kolejnych m liniach pary a, b (1 ≤ a, b ≤ n). Wartości nie przekroczą 10\n9\n.\n\nWyjście\n")
        h = ig.statement_hints(text)
        c = h["constraints"]
        self.assertEqual(c["n"]["min"], 1)
        self.assertEqual(c["n"]["max"], 100000)
        self.assertEqual(c["m"]["max"], 1000000)
        self.assertEqual(c["a"]["max"], "n")
        self.assertIn("m", h["count_mentions"])
        self.assertEqual(h["global"].get("max"), 10 ** 9)

    def test_weird_le_symbols(self):
        h = ig.statement_hints("Wejście\nDwie liczby n i q (1 6 n, q 6 5 000) oraz ciąg (1 ¬ ai ¬ 10\n9\n).\nWyjście\n")
        self.assertEqual(h["constraints"]["n"]["max"], 5000)
        self.assertEqual(h["constraints"]["q"]["max"], 5000)
        self.assertEqual(h["constraints"]["ai"]["max"], 10 ** 9)

    def test_eval_expr_safe(self):
        env = {"n": 5}
        self.assertEqual(ig.eval_expr("n*(n-1)//2", env), 10)
        self.assertEqual(ig.eval_expr("10**9", env), 10 ** 9)
        self.assertEqual(ig.eval_expr("10^5", env), 10 ** 5)
        self.assertEqual(ig.eval_expr("min(n, 3)", env), 3)
        for bad in ("__import__('os')", "n.__class__", "open('x')", "[1,2]"):
            with self.assertRaises(ig.FormatError):
                ig.eval_expr(bad, env)


class InferTest(unittest.TestCase):
    def check_roundtrip(self, spec, strategies=None, sizes=("tiny", "small", "medium")):
        rng = random.Random(3)
        for st in strategies or ig.STRATEGY_IDS:
            for sz in sizes:
                t = ig.generate(spec, st, sz, rng)
                ok, msg = ig.validate(spec, t)
                self.assertTrue(ok, f"{st}/{sz}: {msg}\n{t[:300]}")

    def test_array(self):
        spec = ig.infer(["5\n1 2 3 4 5\n", "3\n7 7 1\n"])
        self.assertEqual(kinds(spec), ["line", "array"])
        self.assertEqual(spec["items"][1]["len"], "n")
        self.check_roundtrip(spec)

    def test_graph_with_statement(self):
        st = ("Wejście\nW pierwszej linii dwie liczby całkowite: n, m (1 ≤ n ≤ 100 000, 0 ≤ m ≤ 1 000 000), liczba "
              "wierzchołków i krawędzi grafu. W kolejnych m liniach krawędzie a, b (1 ≤ a, b ≤ n).\nWyjście\n")
        spec = ig.infer(["7 6\n1 2\n2 3\n3 4\n4 1\n3 5\n5 7\n"], st)
        g = spec["items"][1]
        self.assertEqual(g["kind"], "graph")
        self.assertEqual((g["n"], g["count"]), ("n", "m"))
        self.assertEqual(spec["vars"]["n"]["max"], 100000)
        self.check_roundtrip(spec)

    def test_tree(self):
        spec = ig.infer(["5\n1 2\n2 3\n2 4\n4 5\n", "3\n1 2\n1 3\n"])
        g = spec["items"][1]
        self.assertEqual(g["kind"], "graph")
        self.assertTrue(g["tree"])
        self.assertEqual(g["count"], "n-1")
        for st in ("path", "star", "random_tree", "random", "max"):
            t = ig.generate(spec, st, "medium", seed=1)
            p = ig.parse(spec, t)
            n = p.env["n"]
            edges = [(r[0] - 1, r[1] - 1) for r in p.nodes[1]["rows"]]
            self.assertEqual(len(edges), n - 1)
            self.assertTrue(ig._connected(n, edges), st)

    def test_multitest(self):
        spec = ig.infer(["3\n2\n1 2\n3\n1 2 3\n1\n5\n", "2\n4\n4 3 2 1\n2\n5 5\n"])
        self.assertEqual(spec["items"][1]["kind"], "block")
        self.assertEqual(spec["items"][1]["count"], "t")
        self.assertEqual(kinds(spec), ["line", "block", "line", "array"])
        self.check_roundtrip(spec)

    def test_queries_with_type(self):
        spec = ig.infer(["6\npush 3\npush 5\npop\npush 1\npop\npop\n"])
        q = spec["items"][1]
        self.assertEqual(q["kind"], "queries")
        self.assertEqual(sorted(t["tag"] for t in q["types"]), ["pop", "push"])
        self.check_roundtrip(spec)

    def test_array_then_queries(self):
        spec = ig.infer(["4 3\n2 3 2 1\nczytaj 2 4\nzamien 2 4\nczytaj 1 3\n"])
        self.assertEqual(kinds(spec), ["line", "array", "queries"])
        q = spec["items"][2]
        self.assertEqual(q["count"], "q")
        self.assertTrue(all(f["max"] == "n" for t in q["types"] for f in t["fields"]))
        self.check_roundtrip(spec)

    def test_ranges_ordered_pairs(self):
        spec = ig.infer(["5 3\n1 5 2 4 3\n1 3\n2 5\n1 1\n"])
        rep = spec["items"][2]
        self.assertEqual(rep["kind"], "repeat")
        self.assertEqual(rep.get("pairs"), [[0, 1, "le"]])
        self.check_roundtrip(spec)

    def test_grid_and_unique_chars(self):
        spec = ig.infer(["4 3\nP#W.\n.##.\n....\n", "3 1\nP#W\n"])
        g = spec["items"][1]
        self.assertEqual(g["kind"], "grid")
        self.assertEqual(g.get("unique"), "PW")
        self.check_roundtrip(spec)

    def test_matrix(self):
        spec = ig.infer(["2 4\n1 2 3 4\n5 6 7 8\n", "3 5\n1 0 0 0 0\n0 1 0 0 0\n0 0 1 0 0\n"])
        self.assertEqual(spec["items"][1]["kind"], "matrix")
        self.check_roundtrip(spec)

    def test_string_len(self):
        spec = ig.infer(["6\nabacab\n", "3\nxyz\n"])
        self.assertEqual(spec["items"][1]["kind"], "string")
        self.assertEqual(spec["items"][1]["len"], "n")
        self.check_roundtrip(spec)

    def test_sentinel(self):
        spec = ig.infer(["3 4\n1 2\n-1\n", "5 5\n-1\n"])
        self.assertEqual(spec["items"][-1]["count"].get("until"), "-1")
        self.check_roundtrip(spec, ["random", "min", "max"])

    def test_lists(self):
        spec = ig.infer(["3\n2 5 7\n0\n3 1 2 3\n"])
        self.assertEqual(spec["items"][1]["kind"], "lists")
        self.check_roundtrip(spec, ["random", "min", "max"])

    def test_permutation_and_parents(self):
        spec = ig.infer(["6\n3 1 2 6 5 4\n", "4\n2 4 1 3\n"])
        self.assertEqual(spec["items"][1].get("order"), "permutation")
        self.check_roundtrip(spec)
        spec = ig.infer(["6\n1 1 2 3 3\n", "4\n1 2 2\n"])
        self.assertEqual(spec["items"][1]["kind"], "parents")
        self.check_roundtrip(spec)

    def test_describe_polish(self):
        spec = ig.infer(["5\n1 2\n2 3\n2 4\n4 5\n"])
        text = "\n".join(ig.describe(spec))
        self.assertIn("drzewo", text)
        self.assertIn("krawędzi", text)


class GenerateTest(unittest.TestCase):
    SPEC = {"version": 1, "vars": {"n": {"min": 1, "max": 100000, "count": True},
                                   "m": {"min": 0, "max": 200000, "count": True}},
            "items": [{"kind": "line", "fields": [{"type": "int", "var": "n"}, {"type": "int", "var": "m"}]},
                      {"kind": "graph", "n": "n", "count": "m", "base": 1, "directed": False, "tree": False,
                       "connected": True, "simple": True, "weight": {"type": "int", "min": 1, "max": 10 ** 9}}]}

    def test_strategies_valid(self):
        for st in ig.STRATEGY_IDS:
            for sz in ("tiny", "small", "medium", 500):
                t = ig.generate(self.SPEC, st, sz, seed=hash((st, str(sz))) % 1000)
                ok, msg = ig.validate(self.SPEC, t)
                self.assertTrue(ok, f"{st}/{sz}: {msg}")

    def test_size_caps(self):
        t = ig.generate(self.SPEC, "max", "small", seed=1)
        n = int(t.split()[0])
        self.assertLessEqual(n, 10)
        t = ig.generate(self.SPEC, "max", "max", seed=1)
        self.assertEqual(int(t.split()[0]), 100000)

    def test_min_strategy(self):
        t = ig.generate(self.SPEC, "min", "small", seed=1)
        n, m = map(int, t.split()[:2])
        self.assertEqual(n, 1)
        self.assertEqual(m, 0)

    def test_values_strategies_on_array(self):
        spec = {"vars": {"n": {"min": 1, "max": 50, "count": True}},
                "items": [{"kind": "line", "fields": [{"type": "int", "var": "n"}]},
                          {"kind": "array", "len": "n", "elem": {"type": "int", "min": -5, "max": 5}}]}
        arr = lambda t: list(map(int, t.split("\n")[1].split()))  # noqa: E731
        a = arr(ig.generate(spec, "sorted", "small", seed=2))
        self.assertEqual(a, sorted(a))
        a = arr(ig.generate(spec, "reversed", "small", seed=2))
        self.assertEqual(a, sorted(a, reverse=True))
        a = arr(ig.generate(spec, "equal", "small", seed=2))
        self.assertEqual(len(set(a)), 1)
        a = arr(ig.generate(spec, "alternating", "small", seed=2))
        self.assertTrue(all(x in (-5, 5) for x in a))

    def test_distinct_huge_range(self):
        spec = {"vars": {"n": {"min": 5, "max": 5, "count": True}},
                "items": [{"kind": "line", "fields": [{"type": "int", "var": "n"}]},
                          {"kind": "array", "len": "n", "order": "distinct",
                           "elem": {"type": "int", "min": 0, "max": 2 ** 64 - 1}}]}
        t = ig.generate(spec, "random_full", "small", seed=1)
        self.assertTrue(ig.validate(spec, t)[0])


class ShrinkTest(unittest.TestCase):
    def test_shrink_array(self):
        spec = ig.infer(["5\n1 2 3 4 5\n", "3\n7 7 1\n"],
                        "Wejście\nliczba n (1 ≤ n ≤ 1000), potem n liczb a_i (1 ≤ a_i ≤ 10^9).\nWyjście\n")
        inp = "12\n209 2097 20976 209764 5 6 7 8 9 10 11 99\n"
        res = ig.shrink(spec, inp, lambda t: int(t.split()[0]) >= 9)
        self.assertEqual(res["input"], "9\n1 1 1 1 1 1 1 1 1\n")
        self.assertTrue(res["improved"])

    def test_shrink_keeps_specific_value(self):
        spec = ig.infer(["5\n1 2 3 4 5\n", "3\n7 7 1\n"])
        inp = "8\n3 9 2 7 7 4 1 8\n"
        res = ig.shrink(spec, inp, lambda t: "7" in t.split()[1:])
        self.assertEqual(res["input"], "1\n7\n")

    def test_shrink_tree_stays_tree(self):
        spec = ig.infer(["5\n1 2\n2 3\n2 4\n4 5\n", "3\n1 2\n1 3\n"])
        edges = [(i, i + 1) for i in range(1, 29)] + [(15, 30)]          # ścieżka 1..29 + liść przy 15
        random.Random(5).shuffle(edges)
        big = "30\n" + "\n".join(f"{a} {b}" for a, b in edges) + "\n"

        def pred(text):
            p = ig.parse(spec, text, strict=False)
            deg = {}
            for a, b in p.nodes[1]["rows"]:
                deg[a] = deg.get(a, 0) + 1
                deg[b] = deg.get(b, 0) + 1
            return max(deg.values() or [0]) >= 3          # „błąd”: wierzchołek stopnia ≥ 3
        self.assertTrue(pred(big))
        res = ig.shrink(spec, big, pred)
        p = ig.parse(spec, res["input"], strict=False)
        self.assertEqual(p.env["n"], 4)            # gwiazda z 3 liśćmi
        edges = [(a - 1, b - 1) for a, b in p.nodes[1]["rows"]]
        self.assertTrue(ig._connected(p.env["n"], edges))

    def test_shrink_multitest(self):
        spec = ig.infer(["3\n2\n1 2\n3\n1 2 3\n1\n5\n", "2\n4\n4 3 2 1\n2\n5 5\n"])
        inp = "4\n2\n1 2\n3\n4 5 6\n1\n9\n2\n3 4\n"
        res = ig.shrink(spec, inp, lambda t: "9" in t.split())
        self.assertEqual(res["input"], "1\n1\n9\n")

    def test_shrink_invalid_input(self):
        spec = ig.infer(["3\n1 2 3\n"])
        res = ig.shrink(spec, "abc\n", lambda t: True)
        self.assertFalse(res["improved"])
        self.assertIn("error", res)


@unittest.skipUnless(STATEMENTS, "brak prywatnych treści zadań ucznia (user_data/programs)")
class RealStatementsTest(unittest.TestCase):
    """Wnioskowanie formatu na PRAWDZIWYCH przykładach z treści zadań ucznia."""

    def test_real_statements(self):
        total = with_samples = parsed = gen_ok = structured = 0
        failures = []
        for f in STATEMENTS:
            total += 1
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            samples = ig.extract_samples(text)
            if not samples:
                continue
            with_samples += 1
            spec = ig.infer([s["input"] for s in samples], text)
            if all(ig.validate(spec, s["input"])[0] for s in samples):
                parsed += 1
            else:
                failures.append(os.path.basename(f))
            if any(it["kind"] != "line" for it in spec["items"]):
                structured += 1
            rng = random.Random(1)
            ok = True
            for st in ("random", "min", "max", "path", "equal"):
                t = ig.generate(spec, st, "small", rng)
                if not ig.validate(spec, t)[0]:
                    ok = False
            gen_ok += ok
        print(f"\n[inputgen] treści: {total}, z przykładami: {with_samples}, format pasuje do wszystkich przykładów: "
              f"{parsed} ({100 * parsed / max(1, with_samples):.1f}%), struktura rozpoznana (nie tylko linie): "
              f"{structured}, poprawne generowanie: {gen_ok} ({100 * gen_ok / max(1, with_samples):.1f}%)"
              + (f"; niepasujące: {', '.join(failures[:10])}" if failures else ""))
        self.assertGreaterEqual(parsed / max(1, with_samples), 0.95)
        self.assertGreaterEqual(gen_ok / max(1, with_samples), 0.93)
        self.assertGreaterEqual(with_samples / max(1, total), 0.9)



class TestBigint(unittest.TestCase):
    """Liczba do 10^K cyfr (K > 18): generowana jako napis cyfr, nigdy jako int."""

    def spec(self):
        from tests.test_statement import BIG_DIGITS
        samples = ig.extract_samples(BIG_DIGITS)
        return samples, ig.infer([s["input"] for s in samples], BIG_DIGITS)

    def test_infer_from_statement(self):
        samples, sp = self.spec()
        self.assertEqual([s.get("source") for s in samples], [None, "ocen", "ocen"])   # testy „ocen” też
        f = sp["items"][0]["fields"][0]
        self.assertEqual((f["type"], f["max_len"], f["min"]), ("bigint", 200001, 1))
        self.assertTrue(all(sp["sample_parse"]))                  # także przykład z 150 001 cyframi
        self.assertIn("cyfr", " ".join(ig.describe(sp)))

    def test_generate_valid_everywhere(self):
        _, sp = self.spec()
        for st in ig.STRATEGY_IDS:
            for size in ("tiny", "small", "medium", "large"):
                t = ig.generate(sp, st, size, seed=7)
                ok, msg = ig.validate(sp, t)
                self.assertTrue(ok, (st, size, msg, t[:40]))
                self.assertNotEqual(t.strip()[0], "0")
                self.assertLessEqual(len(t.strip()), 3000)      # testy losowe mają rozsądną długość
        mx = ig.generate(sp, "max", "max", seed=1).strip()
        self.assertEqual(mx, "1" + "0" * 200000)               # jeden test maksymalny: dokładnie 10^K

    def test_validate_rejects_bad_numbers(self):
        _, sp = self.spec()
        self.assertFalse(ig.validate(sp, "0123\n")[0])          # zero wiodące
        self.assertFalse(ig.validate(sp, "0\n")[0])             # n ≥ 1
        self.assertFalse(ig.validate(sp, "2" + "0" * 200000 + "\n")[0])   # > 10^200000
        self.assertTrue(ig.validate(sp, "9" * 200000 + "\n")[0])



class CppReaderTest(unittest.TestCase):
    """„Gotowe wczytywanie danych” ze specyfikacji: kompiluje się i czyta przykłady do końca, bez błędu."""

    CASES = {
        "array": ["5\n1 2 3 4 5\n", "3\n7 7 1\n"],
        "grid": ["3 4\n#..#\n....\n##.#\n", "2 2\n.#\n#.\n"],
        "graph": ["4 3\n1 2\n2 3\n3 4\n", "3 2\n1 2\n1 3\n"],
        "queries": ["3 5\nADD 4\nGET\nADD 7\nSUM 1 2\nGET\n", "1 3\nGET\nADD 1\nSUM 1 1\n"],
        "multitest": ["2\n3\n1 2 3\n2\n5 6\n", "1\n1\n9\n"],
    }

    @unittest.skipUnless(__import__("oiforge.config", fromlist=["CXX"]).CXX, "brak kompilatora C++")
    def test_readers_compile_and_read_samples(self):
        from oiforge.core import runner
        for name, samples in self.CASES.items():
            with self.subTest(name):
                spec = ig.infer(samples, time_budget=3.0)
                main = ig.cpp_reader(spec)
                self.assertTrue(main, spec)
                self.assertNotIn("TODO", main)
                code = ("#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n" + main +
                        '\n    string rest;\n    if (cin >> rest) return 3;   // coś zostało nieprzeczytane\n    return 0;\n}\n')
                prog = runner.prepare(code, lang="cpp")
                self.assertTrue(prog.ok, code)
                for inp in samples:
                    self.assertEqual(runner.run(prog.cmd, inp, timeout=5).verdict, "OK", (code, inp))

    def test_unsupported_expression_gives_none(self):
        spec = {"items": [{"kind": "line", "fields": [{"type": "int", "var": "n"}]},
                          {"kind": "array", "len": "2**n", "elem": {"type": "int"}}]}
        self.assertIsNone(ig.cpp_reader(spec))


if __name__ == "__main__":
    unittest.main()

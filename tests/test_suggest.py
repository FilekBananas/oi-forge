"""Testy doradcy algorytmów (oiforge.tools.suggest) i objaśniacza treści (oiforge.tools.taskexplain)."""
import json
import unittest
from collections import Counter

from oiforge import config
from oiforge.tools import suggest as SG
from oiforge.tools import taskexplain as TE
from tests.test_statement import PDF_GRAPH, PLAIN_ARRAY

HAVE_DATA = (config.PROGRAMS_DIR / "index.json").exists() and \
    len(list(config.PROGRAMS_DIR.glob("*/*/statements/*.txt"))) >= 50

WEIGHTED = """Podróż
Bajtazar chce dojechać z miasta 1 do miasta n najkrótszą drogą. Drogi mają długości (wagi).
Wejście
W pierwszym wierszu znajdują się liczby n, m (2 ≤ n ≤ 100 000, 1 ≤ m ≤ 200 000). W kolejnych m wierszach znajdują się
trzy liczby a, b, w (1 ≤ a, b ≤ n, 1 ≤ w ≤ 10^9) — droga z a do b o długości w.
Wyjście
Wypisz długość najkrótszej drogi z 1 do n.
Przykład
Wejście:
3 3
1 2 4
2 3 1
1 3 7
Wyjście:
5
"""

WAYS_MOD = """Schodki
Na ile sposobów można wejść na n-ty schodek, jeśli w jednym kroku pokonujemy 1 lub 2 schodki? Wynik podaj modulo 10^9 + 7.
Wejście
Jedna liczba całkowita n (1 ≤ n ≤ 10^6).
Wyjście
Liczba sposobów modulo 10^9+7.
Przykład
Wejście:
4
Wyjście:
5
"""

BINSEARCH = """Drukarki
Mamy n drukarek; i-ta drukuje jedną kartkę w t_i sekund. Jaki jest minimalny czas T taki, że da się wydrukować k kartek?
Wejście
W pierwszym wierszu liczby n, k (1 ≤ n ≤ 200 000, 1 ≤ k ≤ 10^9). W drugim wierszu n liczb t_1, ..., t_n (1 ≤ t_i ≤ 10^9).
Wyjście
Najmniejszy czas.
Przykład
Wejście:
2 5
1 2
Wyjście:
4
"""

SEGTREE = """Lampki
Dany jest ciąg n liczb. Należy obsłużyć q zapytań: zmiana wartości na pozycji oraz zapytanie o sumę na przedziale od a do b.
Wejście
W pierwszym wierszu liczby n, q (1 ≤ n, q ≤ 500 000). W drugim wierszu n liczb a_i (0 ≤ a_i ≤ 10^9).
W kolejnych q wierszach zapytania.
Wyjście
Odpowiedzi na zapytania o sumę.
"""


class TestSignals(unittest.TestCase):
    def top(self, text, k=3):
        r = SG.analyze(text, with_code=False)
        return [a["id"] for a in r["algorithms"][:k]], r

    def test_signals_file(self):
        data = SG.load_signals()
        self.assertGreaterEqual(len(data["signals"]), 60)
        ids = set(data["algorithms"])
        for s in data["signals"]:
            for a in s["algos"]:
                self.assertIn(a, ids, f"nieznany algorytm {a} w sygnale {s['id']}")
        for _pat, lab in data["round_rules"]:
            self.assertIn(lab, ids)
        self.assertEqual(SG._round_label("Dzień 5 - Dijkstra"), "dijkstra")
        self.assertEqual(SG._round_label("Drzewa przedziałowe"), "segment_tree")
        self.assertEqual(SG._round_label("Dynamiki na drzewach"), "tree")
        self.assertEqual(SG._round_label("Problem plecakowy"), "dp")
        self.assertIsNone(SG._round_label("Kontest - dobijanie"))
        self.assertIsNone(SG._round_label("Warsztaty 3"))

    def test_graph_bfs(self):
        ids, r = self.top(PDF_GRAPH)
        self.assertIn("bfs", ids)
        self.assertEqual(r["complexity"]["complexity"], "O(n log n)")
        self.assertTrue(any(x["selected"] for x in r["complexity"]["table"]))

    def test_weighted_dijkstra(self):
        ids, _ = self.top(WEIGHTED)
        self.assertEqual(ids[0], "dijkstra")

    def test_modulo_ways_dp(self):
        ids, _ = self.top(WAYS_MOD)
        self.assertIn("dp", ids)

    def test_binsearch_answer(self):
        ids, _ = self.top(BINSEARCH)
        self.assertEqual(ids[0], "binary_search")

    def test_segment_tree(self):
        ids, _ = self.top(SEGTREE)
        self.assertIn("segment_tree", ids[:2])

    def test_reasons_and_kb(self):
        _, r = self.top(WEIGHTED)
        a = r["algorithms"][0]
        self.assertTrue(a["reasons"])
        self.assertTrue(all("reason" in x for x in a["reasons"]))
        self.assertGreater(a["confidence"], 0)
        self.assertIsInstance(a["kb"], list)

    def test_small_n_priors(self):
        r = SG.analyze("Zadanie\nWejście\nW pierwszym wierszu liczba n (1 ≤ n ≤ 18). W drugim wierszu n liczb a_i "
                       "(1 ≤ a_i ≤ 100).\nWyjście\nWypisz wynik.", with_code=False)
        ids = [a["id"] for a in r["algorithms"][:4]]
        self.assertTrue({"bitmask_dp", "backtracking"} & set(ids), ids)


PAIRS_K = """Pary o zadanej sumie
Bajtek dostał ciąg liczb oraz liczbę k. Na ile sposobów można wybrać z ciągu dwa elementy (na różnych pozycjach), których
suma wynosi dokładnie k?
Wejście
W pierwszym wierszu znajdują się dwie liczby całkowite n i k (1 ≤ n ≤ 100 000, 1 ≤ k ≤ 10^9). W drugim wierszu znajduje
się n liczb całkowitych a_1, a_2, ..., a_n (1 ≤ a_i ≤ 10^9).
Wyjście
Liczba par indeksów (i, j), takich że i < j oraz a_i + a_j = k.
Przykład
Wejście:
6 7
1 6 3 4 2 5
Wyjście:
3
"""


class TestComplexityRegressions(unittest.TestCase):
    """Rozmiar danych to LICZNOŚĆ (n, q, |s|, pola planszy), nie wartość (k, a_i, w)."""

    def bench(self, name):
        from tests import bench_smart as B
        return B.statement(name)

    def test_pairs_k_is_not_size(self):
        cx = SG.complexity_info(SG.ST.parse(PAIRS_K))
        self.assertEqual(cx["var"], "n")
        self.assertEqual(cx["complexity"], "O(n log n)")
        self.assertNotIn("k", [s["var"] for s in cx["sizes"]])
        ids = [a["id"] for a in SG.analyze(PAIRS_K, with_code=False)["algorithms"][:3]]
        self.assertNotEqual(ids[0], "dp", ids)
        self.assertTrue({"two_pointers", "sorting", "stl"} & set(ids), ids)

    def test_value_m_is_not_size(self):
        cx = SG.complexity_info(SG.ST.parse(self.bench("drwal")))      # „n m”, m = ilość drewna ≤ 2·10^9
        self.assertEqual((cx["var"], cx["complexity"]), ("n", "O(n log n)"))

    def test_several_sizes(self):
        cx = SG.complexity_info(SG.ST.parse(self.bench("sumy_przedzialow")))
        self.assertEqual({s["var"] for s in cx["sizes"]}, {"n", "q"})
        self.assertIn("n + q", cx["note"])
        self.assertTrue(all(s.get("complexity") for s in cx["sizes"]))

    def test_grid_cells(self):
        cx = SG.complexity_info(SG.ST.parse(self.bench("labirynt")))
        self.assertEqual(cx["var"], "n·m")
        self.assertEqual(cx["max"], 10**6)
        self.assertEqual(cx["complexity"], "O(n·m)")
        ids = [a["id"] for a in SG.analyze(self.bench("labirynt"), with_code=False)["algorithms"][:2]]
        self.assertEqual(ids[0], "bfs", ids)

    def test_value_dimension_note(self):
        cx = SG.complexity_info(SG.ST.parse(self.bench("reszta")))
        self.assertEqual(cx["var"], "n")
        self.assertIn("DP po K", cx["note"])

    def test_binsearch_answer_classics(self):
        for name in ("deski", "drwal", "maszyny", "rozklad_pracy"):
            ids = [a["id"] for a in SG.analyze(self.bench(name), with_code=False)["algorithms"][:1]]
            self.assertEqual(ids, ["binary_search"], name)

    def test_bench_advisor_rate(self):
        from tests import bench_smart as B
        res = B.measure(do_solver=False, do_brute=False, verbose=False)
        ok = sum(r["suggest"]["ok"] for r in res["rows"].values())
        self.assertGreaterEqual(ok / len(res["rows"]), 0.9, res["summary"])


class TestModels(unittest.TestCase):
    def test_naive_bayes_and_centroid(self):
        docs = ["graf wierzchołki krawędzie najkrótsza ścieżka bfs", "graf krawędź wierzchołek odległość",
                "suma przedział zapytanie zmiana drzewo przedziałowe", "przedział zapytania suma zmiany wartości",
                "napis litery palindrom słowo", "słowo litery napis odwrócić"]
        y = ["graph", "graph", "seg", "seg", "str", "str"]
        X = [SG.featurize(d) for d in docs]
        for model in (SG.NaiveBayes(), SG.Centroid()):
            model.fit(X, y)
            p = model.predict_proba(SG.featurize("najkrótsza ścieżka w grafie z wierzchołkami"))
            self.assertEqual(max(p, key=p.get), "graph")
            self.assertAlmostEqual(sum(p.values()), 1.0, places=6)
            p = model.predict_proba(SG.featurize("czy słowo jest palindromem"))
            self.assertEqual(max(p, key=p.get), "str")
        # serializacja
        nb = SG.NaiveBayes().fit(X, y)
        nb2 = SG.NaiveBayes.from_json(json.loads(json.dumps(nb.to_json())))
        x = SG.featurize("drzewo przedziałowe zapytania")
        self.assertEqual(max(nb.predict_proba(x), key=nb.predict_proba(x).get), max(nb2.predict_proba(x), key=nb2.predict_proba(x).get))


class TestTaskExplain(unittest.TestCase):
    def test_explain_graph(self):
        r = TE.explain(PDF_GRAPH)
        self.assertEqual(r["title"], "Odległości")
        self.assertTrue(r["summary"])
        self.assertTrue(r["goal"])
        names = {v["name"] for v in r["variables"]}
        self.assertTrue({"n", "m"} <= names)
        n = next(v for v in r["variables"] if v["name"] == "n")
        self.assertEqual(n["max_fmt"], "10^5")
        self.assertIn("wierzchołków", n["meaning"])
        pit = {p["id"] for p in r["pitfalls"]}
        self.assertIn("no-loops", pit)
        self.assertIn("zero-size", pit)
        self.assertEqual(len(r["subtasks"]), 2)
        self.assertTrue(any(g["term"].startswith("graf") or g["term"].startswith("wierzchołek") for g in r["glossary"]))
        self.assertEqual([f["kind"] for f in r["input"]["format"]], ["scalars", "rows"])

    def test_pitfalls_long_long_and_modulo(self):
        r = TE.explain(PLAIN_ARRAY)
        pit = {p["id"] for p in r["pitfalls"]}
        self.assertIn("sum-overflow", pit)          # 10^6 · 10^9 > int
        self.assertIn("negative", pit)
        r = TE.explain(WAYS_MOD)
        self.assertIn("modulo", {p["id"] for p in r["pitfalls"]})


@unittest.skipUnless(HAVE_DATA, "brak danych ucznia w user_data/programs")
class TestOnStudentData(unittest.TestCase):
    def test_corpus_and_labels(self):
        corpus = SG.user_corpus()
        self.assertGreaterEqual(len(corpus), 150)
        labels = Counter(x["label"] for x in corpus if x["label"])
        self.assertGreaterEqual(sum(labels.values()), 120)
        self.assertGreaterEqual(len(labels), 10)

    def test_classifier_cross_validation(self):
        r = SG.evaluate(folds=5)
        self.assertGreaterEqual(r["models"]["blend"]["top3"], 0.6)
        self.assertGreaterEqual(r["models"]["blend"]["top1"], 0.4)
        self.assertGreater(r["models"]["blend"]["top1"], r["models"]["nb"]["top1"] - 0.01)

    def test_similar_finds_same_task(self):
        corpus = SG.user_corpus()
        d = next(x for x in corpus if x["parsed"]["samples"])
        sim = SG.similar_tasks(d["text"], k=3, with_code=False)
        self.assertTrue(sim)
        self.assertEqual(sim[0]["key"], d["key"])
        self.assertTrue(sim[0]["same_task"])
        self.assertIn("rozwiązanie", sim[0]["message"])



class TestBigNumberComplexity(unittest.TestCase):
    def test_digits_are_the_size(self):
        from tests.test_statement import BIG_DIGITS
        from oiforge.core import statement as ST
        cx = SG.complexity_info(ST.parse(BIG_DIGITS))
        self.assertEqual(cx["var"], "d (cyfry n)")
        self.assertEqual(cx["max"], 200001)
        self.assertTrue(cx["complexity"].startswith("O(d"), cx["complexity"])
        self.assertIn("napis", cx["hint"])
        sub = {s["id"]: s for s in cx["subtasks"]}
        self.assertEqual(sub[2]["max"], 1001)                    # n ≤ 10^1000 → 1001 cyfr
        self.assertIn("d", sub[2]["complexity"])

    def test_bignum_pitfall_in_explainer(self):
        from tests.test_statement import BIG_DIGITS
        r = TE.explain(BIG_DIGITS)
        self.assertIn("bignum", [p["id"] for p in r["pitfalls"]])
        var = next(v for v in r["variables"] if v["name"] == "n")
        self.assertEqual(var["max_fmt"], "10^200000")


if __name__ == "__main__":
    unittest.main()

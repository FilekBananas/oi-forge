"""Testy rdzenia wyszukiwania (oiforge.core.search) i małej sieci neuronowej (oiforge.core.nn).

Uruchom: python3 -m unittest tests.test_search -v
Testy są niezależne od zawartości bazy wiedzy (własne mini-korpusy), poza testem synonimów z data/synonyms.json.
"""
from __future__ import annotations

import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from oiforge.core import search as S
from oiforge.core import text as T
from oiforge.core.nn import HashedMLP, text_features, typo_variants, word_dropout


def _corpus() -> S.SearchIndex:
    idx = S.SearchIndex({"title": 3.0, "aliases": 2.5, "body": 1.0})
    docs = {
        "set": ("std::set — zbiór uporządkowany", "set, zbiór, sety", "Zbiór unikalnych elementów w kolejności rosnącej. "
                                                                      "insert, erase i lower_bound w O(log n)."),
        "vector": ("std::vector — tablica dynamiczna", "vector, wektor", "push_back dodaje element na koniec, erase usuwa."),
        "dijkstra": ("Algorytm Dijkstry", "dijkstra, najkrótsza ścieżka", "Najkrótsze ścieżki w grafie z nieujemnymi wagami, "
                                                                          "kolejka priorytetowa, O((n + m) log n)."),
        "binsearch": ("Wyszukiwanie binarne", "binsearch, binary search", "Dzielenie przedziału na pół; lower_bound."),
        "segtree": ("Drzewo przedziałowe", "segment tree, przedziałówka", "Zapytania o sumę na przedziale w O(log n)."),
        "precision": ("Wypisywanie liczb rzeczywistych", "setprecision, fixed", "cout << fixed << setprecision(2) — "
                                                                                 "dwa miejsca po przecinku."),
        "eof": ("Wczytywanie do końca pliku", "eof, while cin", "while (cin >> x) czyta, dopóki są dane."),
    }
    for did, (title, al, body) in docs.items():
        idx.add(did, {"title": title, "aliases": al, "body": body}, payload={"id": did})
    return idx.build()


class TestRoot(unittest.TestCase):
    def test_polish_inflection(self):
        cases = {"seta": "set", "seci": "set", "vectorz": "vector", "mapi": "map", "kolejc": "kolejk",
                 "przedzialow": "przedzial", "stringi": "string", "kodzi": "kod"}
        for t, want in cases.items():
            self.assertEqual(S.root(t), want, t)

    def test_short_and_special_terms_untouched(self):
        for t in ("bfs", "dp", "c++", "10", "lower_bound", "set"):
            self.assertEqual(S.root(t), t)

    def test_forms_share_root(self):
        forms = ["set", "seta", "secie", "setu", "sety"]
        roots = {S.root(T.stem(T.normalize(f))) for f in forms}
        self.assertEqual(roots, {"set"})


class TestExpandQuery(unittest.TestCase):
    def test_original_terms_weight_one(self):
        ex = dict(S.expand_query("drzewo przedziałowe"))
        self.assertEqual(ex.get("drzew"), 1.0)

    def test_synonyms_from_file(self):
        ex = dict(S.expand_query("segtree"))
        self.assertEqual(ex.get(T.stem("segtree")), 1.0)
        self.assertTrue(any(t.startswith("przedzial") for t in ex), ex)
        self.assertTrue(all(w <= 1.0 for w in ex.values()))

    def test_inflected_phrase_matches_group(self):
        ex = dict(S.expand_query("w secie"))
        self.assertIn("set", ex)            # „secie” → grupa „set”

    def test_multiword_synonym_weight_is_split(self):
        ex = dict(S.expand_query("binsearch"))
        # „wyszukiwanie binarne” (2 słowa) dostaje mniejszą wagę na słowo niż pojedynczy synonim
        self.assertLess(ex.get("binar", 0.6), 0.6)

    def test_synonyms_file_valid(self):
        data = json.loads((S.SYN_PATH).read_text(encoding="utf-8"))
        groups = data["groups"]
        self.assertGreaterEqual(len(groups), 250)
        for g in groups:
            self.assertIsInstance(g, list)
            self.assertGreaterEqual(len(g), 2, g)
            self.assertTrue(all(isinstance(x, str) and x.strip() for x in g), g)


class TestSearchIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = _corpus()

    def top(self, q, **kw):
        r = self.idx.search(q, **kw)
        return r[0]["id"] if r else None

    def test_exact(self):
        self.assertEqual(self.top("dijkstra"), "dijkstra")
        self.assertEqual(self.top("zbiór uporządkowany"), "set")

    def test_typo_tolerance(self):
        self.assertEqual(self.top("dikstra"), "dijkstra")
        self.assertEqual(self.top("binserch"), "binsearch")

    def test_synonym(self):
        self.assertEqual(self.top("segtree"), "segtree")
        self.assertEqual(self.top("miejsca po przecinku"), "precision")

    def test_inflection(self):
        self.assertEqual(self.top("w secie"), "set")
        self.assertEqual(self.top("z vectora"), "vector")

    def test_prefix(self):
        self.assertEqual(self.top("przedział"), "segtree")

    def test_filters_and_boost(self):
        r = self.idx.search("log n", filters=lambda did, p: did != "set")
        self.assertNotIn("set", [x["id"] for x in r])
        r = self.idx.search("log n", boost=lambda did, p: 100.0 if did == "segtree" else 1.0)
        self.assertEqual(r[0]["id"], "segtree")

    def test_result_shape_and_cover(self):
        r = self.idx.search("dijkstra kolejka")
        self.assertTrue(r)
        for k in ("id", "score", "payload", "matched", "cover"):
            self.assertIn(k, r[0])
        self.assertEqual(r[0]["cover"], 1.0)

    def test_candidates_and_extra_terms(self):
        r = self.idx.search("log n", candidates=["dijkstra"])
        self.assertEqual([x["id"] for x in r], ["dijkstra"])
        r = self.idx.search("zzzz", extra_terms=[("dijkstr", 0.5), ("dijkstra", 0.5)])
        self.assertTrue(r)

    def test_empty_query(self):
        self.assertEqual(self.idx.search(""), [])
        self.assertEqual(self.idx.search("   ???   "), [])

    def test_suggest(self):
        self.assertIn("dijkstr", " ".join(self.idx.suggest("dijk")))

    def test_correct(self):
        c = self.idx.correct("dijsktra kolejka")
        self.assertTrue(c["changed"])
        self.assertIn("dijkstra", c["text"].lower())
        self.assertFalse(self.idx.correct("dijkstra")["changed"])
        # znane słowa (także w odmianie) nie są „poprawiane”
        self.assertFalse(self.idx.correct("w secie")["changed"])

    def test_correct_keeps_polish_letters(self):
        c = self.idx.correct("uporzadkowany zbiur")
        self.assertIn("zbiór", c["text"])

    def test_stats(self):
        self.assertTrue(self.idx.has_term("dijkstra"))
        self.assertGreater(self.idx.idf("dijkstra"), self.idx.idf("log"))
        self.assertEqual(self.idx.df("dijkstra"), 1)
        self.assertIn("dijkstra", self.idx.vocabulary())

    def test_speed(self):
        idx = S.SearchIndex({"title": 2.0, "body": 1.0})
        rng = random.Random(1)
        words = [f"slowo{i}" for i in range(3000)]
        for i in range(3000):
            idx.add(str(i), {"title": " ".join(rng.choices(words, k=4)), "body": " ".join(rng.choices(words, k=40))})
        idx.build()
        t = time.perf_counter()
        for _ in range(20):
            idx.search("slowo1 slowo2 slowo3 slowo4")
        self.assertLess((time.perf_counter() - t) / 20, 0.5)


class TestRankingSignals(unittest.TestCase):
    """Sygnały dodane dla trafności asystenta: ograniczone sumowanie synonimów, waga pojęć, znane słowa."""

    def test_synonym_pileup_is_limited(self):
        idx = S.SearchIndex({"title": 2.0, "body": 1.0})
        # dokument A: tylko synonimy słowa „policzyć” (zliczanie, count, histogram, częstość) + „mapa”
        idx.add("A", {"title": "zliczanie count histogram częstość w mapie", "body": "zliczanie wystąpień count mapa"})
        # dokument B: rzadkie, kluczowe słowo pytania („wysp”) + „mapie”
        idx.add("B", {"title": "liczba wysp na mapie", "body": "flood fill"})
        for i in range(30):
            idx.add(f"x{i}", {"title": f"inny temat {i}", "body": "mapa tablica liczba"})
        idx.build()
        r = idx.search("jak policzyć ile jest wysp na mapie")
        self.assertEqual(r[0]["id"], "B", r[:3])
        self.assertIn("covered", r[0])

    def test_query_term_weights_and_known_terms(self):
        idx = _corpus()
        w = idx.query_term_weights("dijkstra kolejka zzzqqq")
        self.assertGreater(w[T.stem("dijkstra")], 0.0)
        self.assertEqual(w[T.stem("zzzqqq")], 0.0)
        solid, allq = idx.known_terms("dijkstra makaronowy")
        self.assertIn(T.stem("dijkstra"), solid)
        self.assertNotIn(T.stem("makaronowy"), solid)
        self.assertEqual(len(allq), 2)

    def test_transposition_is_one_typo(self):
        self.assertTrue(S._transposed("binrane", "binarne"))
        self.assertFalse(S._transposed("binarne", "binarne"))
        self.assertFalse(S._transposed("abcd", "abdx"))
        idx = _corpus()
        self.assertIn("dijkstra", idx.correct("dikjstra")["text"].lower())

    def test_single_letters_do_not_count_for_cover(self):
        self.assertNotIn("n", S._cover_terms("vector 2d n na m"))
        self.assertEqual(S._cover_terms("n"), {"n"})


class TestHighlight(unittest.TestCase):
    def test_bold_and_accents(self):
        out = S.highlight("Zbiór unikalnych elementów, wstawianie w O(log n).", ["zbior", "wstawian"])
        self.assertIn("**Zbiór**", out)
        self.assertIn("**wstawianie**", out)

    def test_window(self):
        txt = "a " * 300 + "dijkstra " + "b " * 300
        out = S.highlight(txt, ["dijkstra"], width=80)
        self.assertIn("**dijkstra**", out)
        self.assertTrue(out.startswith("…"))
        self.assertLessEqual(len(out), 120)

    def test_empty(self):
        self.assertEqual(S.highlight("", ["x"]), "")


class TestTermGraph(unittest.TestCase):
    def test_npmi_neighbors(self):
        ctxs = []
        for _ in range(6):
            ctxs.append(["zaokragl", "setprecision", "przecink"])
            ctxs.append(["dijkstra", "kolejk", "priorytet"])
        ctxs += [["inne", "slowo"], ["jeszcze", "inne"], ["cos", "tam"]] * 3
        g = S.TermGraph(max_df_ratio=0.5).fit(ctxs)
        rel = [t for t, _ in g.related("zaokragl")]
        self.assertIn("setprecision", rel)
        self.assertNotIn("dijkstra", rel)
        ex = dict(g.expand(["zaokragl"], weight=0.3))
        self.assertTrue(0 < ex["setprecision"] <= 0.3)

    def test_tiny_input(self):
        self.assertEqual(S.TermGraph().fit([["a", "b"]]).related("a"), [])


class TestTrigramHelpers(unittest.TestCase):
    def test_jaccard(self):
        a, b = S.trigram_set("jak użyć seta"), S.trigram_set("jak uzyc seta")
        self.assertAlmostEqual(S.jaccard(a, b), 1.0)
        self.assertEqual(S.jaccard(frozenset(), a), 0.0)


class TestNN(unittest.TestCase):
    DATA = [
        ("set cpp", "set"), ("jak używać seta", "set"), ("zbiór uporządkowany", "set"), ("set lower_bound", "set"),
        ("usuwanie z seta", "set"), ("set erase", "set"),
        ("vector push_back", "vector"), ("tablica dynamiczna", "vector"), ("jak usunąć z vectora", "vector"),
        ("vector rozmiar", "vector"), ("wektor c++", "vector"),
        ("dijkstra kod", "dijkstra"), ("najkrótsza ścieżka w grafie z wagami", "dijkstra"), ("algorytm dijkstry", "dijkstra"),
        ("dijkstra kolejka priorytetowa", "dijkstra"),
        ("wczytywanie do końca pliku", "eof"), ("while cin", "eof"), ("nieznana liczba danych", "eof"),
        ("jak wczytać wszystkie liczby", "eof"),
    ]

    def test_features_deterministic(self):
        m1, m2 = HashedMLP(["a"]), HashedMLP(["a"])
        self.assertEqual(m1.featurize("dijkstra kod"), m2.featurize("dijkstra kod"))
        f = text_features("Jak użyć seta?")
        self.assertIn("u:set", f)            # „seta” → rdzeń „set”
        self.assertTrue(any(x.startswith("c:") for x in f))

    def test_learns_and_generalizes_to_typos(self):
        m = HashedMLP(sorted({y for _, y in self.DATA}), hidden=32, seed=3)
        st = m.fit(self.DATA, epochs=60, lr=0.3)
        self.assertTrue(st["done"])
        self.assertGreaterEqual(m.evaluate(self.DATA), 0.95)
        self.assertEqual(m.predict("dikstra", 1)[0][0], "dijkstra")
        self.assertEqual(m.predict("jak uzyc setu", 1)[0][0], "set")
        p = m.predict_proba("dijkstra kod")
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)

    def test_sampled_softmax_many_labels(self):
        rng = random.Random(5)
        data = []
        for c in range(60):
            base = f"temat{c} slowo{c} rzecz{c}"
            for _ in range(4):
                ws = base.split()
                rng.shuffle(ws)
                data.append((" ".join(ws), f"L{c}"))
        m = HashedMLP(hidden=32, seed=1)
        m.fit(data, epochs=25, lr=0.3, negatives=8)
        self.assertGreaterEqual(m.evaluate(data), 0.9)

    def test_resumable_and_time_budget(self):
        m = HashedMLP(sorted({y for _, y in self.DATA}), hidden=16)
        st1 = m.fit(self.DATA, epochs=200, max_seconds=0.05)
        self.assertFalse(st1["done"])
        st2 = m.fit(self.DATA, epochs=200)
        self.assertTrue(st2["done"])
        self.assertGreater(st2["step"], st1["step"])

    def test_save_load_roundtrip(self):
        m = HashedMLP(sorted({y for _, y in self.DATA}), hidden=16, seed=2)
        m.fit(self.DATA, epochs=30, lr=0.3)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nn_test.json"
            m.save(p, key="abc")
            self.assertIsNone(HashedMLP.load(p, key="inny"))
            m2 = HashedMLP.load(p, key="abc")
            self.assertIsNotNone(m2)
            a, b = m.predict("dijkstra kod", 2), m2.predict("dijkstra kod", 2)
            self.assertEqual(a[0][0], b[0][0])
            self.assertAlmostEqual(a[0][1], b[0][1], places=2)      # float16 w pliku
            self.assertIsNone(HashedMLP.load(Path(d) / "brak.json"))

    def test_warm_start(self):
        m = HashedMLP(["set", "vector"], hidden=16)
        m.fit([x for x in self.DATA if x[1] in ("set", "vector")], epochs=30)
        m2 = HashedMLP(["set", "vector", "eof"], hidden=16)
        self.assertEqual(m2.warm_start(m), 2)
        self.assertEqual(m2.predict("set cpp", 1)[0][0], "set")

    def test_unknown_text(self):
        m = HashedMLP(["a", "b"], hidden=8)
        m.fit([("alfa", "a"), ("beta", "b")], epochs=5)
        self.assertEqual(m.predict_proba("zzzzqqq"), {})
        self.assertEqual(m.known_fraction(""), 0.0)

    def test_augmentation(self):
        rng = random.Random(1)
        v = typo_variants("wyszukiwanie binarne", rng, 3)
        self.assertEqual(len(v), 3)
        self.assertTrue(all(x != "wyszukiwanie binarne" for x in v))
        self.assertIsNone(word_dropout("dwa słowa", rng))


if __name__ == "__main__":
    unittest.main()

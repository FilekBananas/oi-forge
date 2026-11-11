"""Testy asystenta (oiforge.tools.assistant) i dokumentacji (oiforge.tools.docs).

Uruchom: python3 -m unittest tests.test_assistant -v

Zawiera test regresji jakości: ~70 realistycznych pytań ucznia z akceptowalnymi artykułami. Baza wiedzy rośnie,
więc pytania, których oczekiwanych artykułów jeszcze nie ma, są pomijane. Wynik (np. 68/70) jest wypisywany.
Ustaw OIFORGE_TEST_NN=1, żeby przed testem regresji poczekać na wytrenowanie sieci neuronowej.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge.core import kb  # noqa: E402
from oiforge.tools import assistant as A  # noqa: E402
from oiforge.tools import docs as D  # noqa: E402
from tests import assistant_cases as AC  # noqa: E402

# (pytanie, akceptowalne id artykułów dla top-1 [, oczekiwana intencja])
REGRESSION = [
    ("set cpp", ["stl-set"]),
    ("jak użyć seta", ["stl-set"]),
    ("jak zaokrąglić do 2 miejsc po przecinku", ["num-rounding", "io-precision"]),
    ("zaokrąglenie w górę dzielenia", ["num-ceil-div"]),
    ("binserch", ["tech-binary-search", "stl-binary-search"]),
    ("dijkstra kod", ["graph-dijkstra"], "code"),
    ("sort malejąco", ["stl-sort"]),
    ("vector usuń element", ["stl-vector", "stl-remove-erase"]),
    ("long long czy int", ["num-int-types"]),
    ("multiset vs set", ["stl-multiset", "stl-set"], "compare"),
    ("co to lazy propagation", ["ds-lazy-propagation"], "define"),
    ("jak wczytać do końca pliku", ["io-read-until-eof"]),
    ("segfault co to", ["pit-out-of-bounds", "pit-stack-overflow", "pit-segfault", "cpp-stack-overflow"]),
    ("kolejka priorytetowa min", ["stl-priority-queue", "ds-heap"]),
    ("jak posortować pary po drugim elemencie", ["stl-sort", "pit-comparator"]),
    ("zliczanie wystąpień w mapie", ["stl-map", "stl-unordered-map"]),
    ("unordered_map anty hash", ["stl-unordered-map", "ds-custom-hash"]),
    ("getline po cin nie działa", ["io-getline"]),
    ("szybkie wczytywanie cin", ["io-fast-io", "io-custom-fast-read"]),
    ("wypisz liczbę z zerami wiodącymi", ["io-setw-setfill"]),
    ("printf long long", ["io-printf-formats"]),
    ("przepełnienie inta", ["num-overflow", "pit-int-overflow"]),
    ("sumy prefiksowe", ["ds-prefix-sums"]),
    ("suma w prostokącie", ["ds-prefix-sums-2d"]),
    ("minimum na przedziale w O(1)", ["ds-sparse-table"]),
    ("najbliższy większy element po prawej", ["ds-monotonic-stack"]),
    ("minimum w oknie przesuwnym", ["ds-monotonic-queue", "tech-sliding-window"]),
    ("bfs najkrótsza ścieżka", ["graph-bfs"]),
    ("bfs na planszy", ["graph-bfs-grid"]),
    ("czy graf jest dwudzielny", ["graph-bipartite"]),
    ("wykrywanie cyklu w grafie skierowanym", ["graph-cycle-detection", "graph-toposort"]),
    ("plecak 0/1", ["dp-knapsack-01"]),
    ("minimalna liczba monet do wydania reszty", ["dp-coin-change"]),
    ("najdłuższy rosnący podciąg nlogn", ["dp-lis"]),
    ("lista sąsiedztwa", ["graph-representation"]),
    ("jak wczytać graf", ["graph-input"]),
    ("pierwiastek z long long dokładnie", ["num-sqrt-pow"]),
    ("__int128 wypisywanie", ["num-int128"]),
    ("string na int", ["num-conversions", "stl-string", "stl-string-methods"]),
    ("lower_bound w secie", ["stl-set"]),
    ("usuwanie z vectora w pętli iterator", ["pit-iterator-erase", "stl-iterators", "stl-vector", "stl-remove-erase"]),
    ("structured bindings", ["stl-tuple"]),
    ("find union", ["graph-dsu"]),
    ("drzewo przedziałowe", ["ds-segment-tree"]),
    ("fenwick", ["ds-fenwick"]),
    ("nwd", ["math-gcd-lcm", "stl-gcd-lcm"]),
    ("sito eratostenesa", ["math-sieve"]),
    ("szybkie potęgowanie modulo", ["math-fast-pow"]),
    ("odwrotność modulo", ["math-mod-inverse"]),
    ("otoczka wypukła", ["geo-convex-hull"]),
    ("kmp wyszukiwanie wzorca", ["str-kmp"]),
    ("lca binary lifting", ["tree-lca"]),
    ("średnica drzewa", ["tree-diameter"]),
    ("sortowanie topologiczne", ["graph-toposort"]),
    ("floyd warshall", ["graph-floyd-warshall"]),
    ("mst kruskal", ["graph-mst-kruskal"]),
    ("dwa wskaźniki", ["tech-two-pointers"]),
    ("binsearch po wyniku", ["tech-binary-search-answer"]),
    ("gra nim", ["game-nim"]),
    ("pole wielokąta", ["geo-polygon-area"]),
    ("wektor usun element", ["stl-vector", "stl-remove-erase"]),
    ("dikstra", ["graph-dijkstra"]),
    ("kolejka priorytetowa", ["stl-priority-queue", "ds-heap"]),
    ("usuń duplikaty z vectora", ["stl-unique"]),
    ("next_permutation", ["stl-permutations"]),
    ("memset -1", ["stl-memset"]),
    ("k-ty najmniejszy element", ["stl-nth-element", "ds-ordered-set"]),
    ("hashowanie stringów", ["str-hashing", "ds-string-hashing"]),
    ("dp na drzewie", ["tree-dp", "dp-tree"]),
    ("przepełnienie stosu przy rekurencji", ["pit-stack-overflow", "cpp-stack-overflow", "graph-dfs"]),
    ("wiele testów czyszczenie zmiennych między testami", ["pit-multitest-reset", "cpp-global-vs-local"]),
    ("freopen", ["io-freopen"]),
    ("złożoność dijkstry", ["graph-dijkstra"], "complexity"),
    ("bfs vs dfs", ["graph-bfs", "graph-dfs"], "compare"),
]

# Trudniejsze, potoczne sformułowania (bez nazw tematów, z literówkami i bez ogonków) — miara „rozumienia”.
REGRESSION_HARD = [
    ("jak zrobic zeby set sortowal malejaco", ["stl-set"]),
    ("wywala mi sie program jak mam duza tablice w funkcji", ["pit-stack-overflow", "cpp-stack-overflow", "cpp-global-vs-local"]),
    ("najszybsza droga w grafie z wagami", ["graph-dijkstra"]),
    ("jak policzyc liczby pierwsze do miliona", ["math-sieve"]),
    ("kolejka z ktorej zawsze wyciagam najmniejszy element", ["stl-priority-queue", "ds-heap"]),
    ("struktura do sumy na przedziale i zmiany elementu", ["ds-fenwick", "ds-segment-tree", "ds-segment-tree-iterative"]),
    ("czy dwa wierzcholki sa w tej samej skladowej", ["graph-dsu", "graph-connected-components"]),
    ("wypisz double z dokladnoscia do 3 miejsc", ["io-precision", "num-rounding"]),
    ("liczba wieksza niz long long", ["num-int128", "math-bignum", "num-bigint-strings"]),
    ("jak odwrocic napis", ["stl-reverse-rotate", "stl-string-methods", "stl-string", "str-basic-reverse"]),
    ("a do potegi b modulo", ["math-fast-pow"]),
    ("najdluzszy wspolny podciag dwoch napisow", ["dp-lcs"]),
    ("minimalna liczba operacji zeby zamienic slowo w drugie", ["dp-edit-distance"]),
    ("palindromy w czasie liniowym", ["str-manacher", "str-palindromes"]),
    ("szukanie wzorca w tekscie szybko", ["str-kmp", "str-z-function", "str-hashing", "ds-string-hashing"]),
    ("czy punkt lezy w srodku wielokata", ["geo-point-in-polygon"]),
    ("minimalne drzewo spinajace", ["graph-mst-kruskal", "graph-mst-prim"]),
    ("k-ty przodek wierzcholka w drzewie", ["tree-lca", "tree-lca-euler"]),
    ("kto wygra gre w zabieranie kamieni", ["game-nim", "game-sprague-grundy", "game-graph-games", "dp-games"]),
    ("losowe liczby w c++", ["num-random", "stl-shuffle-random"]),
    ("liczby oddzielone przecinkami na wejsciu", ["io-read-separated"]),
    ("cin nie wczytuje spacji w napisie", ["io-getline"]),
    ("program za wolno wczytuje milion liczb", ["io-fast-io", "io-custom-fast-read"]),
    ("unordered map dostaje tle na specjalnych testach", ["stl-unordered-map", "ds-custom-hash"]),
    ("sortowanie wierzcholkow tak zeby krawedzie szly w prawo", ["graph-toposort"]),
    ("ile jest sposobow wybrania k elementow z n modulo", ["math-binomial", "math-combinatorics"]),
    ("najwiekszy wspolny dzielnik dwoch liczb", ["math-gcd-lcm", "stl-gcd-lcm"]),
    ("minimum na kazdym oknie dlugosci k", ["ds-monotonic-queue", "tech-sliding-window"]),
    ("dodaj x na przedziale i odpowiedz o sume", ["ds-lazy-propagation", "ds-fenwick-range", "ds-difference-array"]),
    ("sprawdz czy da sie pokolorowac graf dwoma kolorami", ["graph-bipartite"]),
]


def run_regression(verbose: bool = False, items=None) -> tuple[int, int, list]:
    ids = {a["id"] for a in kb.articles()}
    ok, total, fails = 0, 0, []
    for item in items if items is not None else REGRESSION:
        q, exp = item[0], item[1]
        want_intent = item[2] if len(item) > 2 else None
        if not ids & set(exp):
            continue
        total += 1
        r = A.ask(q)
        got = (r.get("topic") or {}).get("id")
        good = got in exp and (want_intent is None or r["intent"]["id"] == want_intent)
        ok += good
        if not good:
            fails.append((q, got, r["intent"]["id"], exp))
        if verbose:
            print(f"  {'OK ' if good else 'ŹLE'} {q!r:48} → {got} [{r['intent']['id']}, pewność {r.get('confidence')}]")
    return ok, total, fails


class TestIntent(unittest.TestCase):
    def test_basic_intents(self):
        cases = {
            "co to lazy propagation": "define", "segfault co to": "define", "dijkstra kod": "code",
            "złożoność dijkstry": "complexity", "multiset vs set": "compare", "jak użyć seta": "howto",
            "różnica między bfs a dfs": "compare",
        }
        for q, want in cases.items():
            self.assertEqual(A.detect_intent(q)["id"], want, q)

    def test_compiler_output(self):
        err = "a.cpp: In function 'int main()':\na.cpp:5:5: error: 'x' was not declared in this scope\n    5 |     x = 3;"
        self.assertEqual(A.detect_intent(err)["id"], "compiler")

    def test_code_paste(self):
        code = "#include <bits/stdc++.h>\nusing namespace std;\nint main(){\n int n; cin >> n;\n cout << n;\n}"
        self.assertEqual(A.detect_intent(code)["id"], "code_paste")

    def test_task(self):
        t = "Mam zadanie: dane jest n liczb (1 ≤ n ≤ 200000), wypisz liczbę par o sumie k. Jaki algorytm?"
        self.assertEqual(A.detect_intent(t)["id"], "task")

    def test_split_compare(self):
        self.assertEqual(A.split_compare("multiset vs set"), ("multiset", "set"))
        self.assertEqual(A.split_compare("różnica między bfs a dfs"), ("bfs", "dfs"))
        self.assertEqual(A.split_compare("czym się różni map od unordered_map"), ("map", "unordered_map"))
        self.assertIsNone(A.split_compare("jak sprawdzić czy element jest w secie bardzo szybko proszę"))

    def test_constraint_advice(self):
        adv = A.constraint_advice("1 ≤ n ≤ 200 000")
        self.assertIsNotNone(adv)
        self.assertIn("log", adv["complexity"])
        self.assertIn("2", A.constraint_advice("n ≤ 20")["complexity"])


class TestAsk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        A.engine().state(wait=True)
        # Sieć z pamięci podręcznej wczytuje się w ~1 s; świeży trening może trwać dłużej — wtedy testy idą bez
        # niej (ranking działa też bez sieci). OIFORGE_TEST_NN=1 wymusza czekanie na pełny trening.
        A.engine().wait_nn(timeout=900 if os.environ.get("OIFORGE_TEST_NN") else 20)

    def test_regression(self):
        ok, total, fails = run_regression(verbose="-v" in sys.argv or bool(os.environ.get("OIFORGE_TEST_VERBOSE")))
        print(f"\n[regresja asystenta] {ok}/{total} pytań z poprawnym artykułem top-1", file=sys.stderr)
        for f in fails:
            print(f"   ✗ {f[0]!r} → {f[1]} ({f[2]}), oczekiwano {f[3]}", file=sys.stderr)
        if total:
            self.assertGreaterEqual(ok / total, 0.9, fails)

    def test_regression_hard(self):
        ok, total, fails = run_regression(items=REGRESSION_HARD)
        print(f"\n[regresja asystenta — trudne, potoczne] {ok}/{total}", file=sys.stderr)
        for f in fails:
            print(f"   ✗ {f[0]!r} → {f[1]} ({f[2]}), oczekiwano {f[3]}", file=sys.stderr)
        if total:
            self.assertGreaterEqual(ok / total, 0.7, fails)

    def test_answer_shape(self):
        r = A.ask("jak użyć seta")
        for k in ("blocks", "sources", "suggestions", "confidence", "topic", "intent", "see_also", "actions"):
            self.assertIn(k, r)
        self.assertTrue(r["blocks"])
        self.assertGreaterEqual(len(r["suggestions"]), 3)
        self.assertLessEqual(len(r["suggestions"]), 5)
        self.assertTrue(any(b["type"] == "code" for b in r["blocks"]))

    def test_speed(self):
        A.ask("rozgrzewka")
        t = time.perf_counter()
        for q in ("set cpp", "dijkstra kod", "jak wczytać do końca pliku", "multiset vs set"):
            A.ask(q)
        per = (time.perf_counter() - t) / 4
        # na nieobciążonej maszynie ~10–40 ms; limit z zapasem na wolne CI
        self.assertLess(per, 3.0)

    def test_compare_table(self):
        if not (kb.get("stl-set") and kb.get("stl-multiset")):
            self.skipTest("brak artykułów set/multiset")
        r = A.ask("multiset vs set")
        self.assertEqual(r["intent"]["id"], "compare")
        tables = [b for b in r["blocks"] if b["type"] == "table"]
        self.assertTrue(tables)
        self.assertEqual(len(tables[0]["headers"]), 3)

    def test_followup_context(self):
        if not kb.get("stl-set"):
            self.skipTest("brak stl-set")
        r = A.ask("a jak usunąć?", [{"q": "jak użyć seta", "topic": "stl-set"}])
        self.assertEqual(r["topic"]["id"], "stl-set")
        self.assertIsNotNone(r["context"])
        if kb.get("stl-vector"):
            r2 = A.ask("a z vectora?", [{"q": "jak usunąć element z seta", "topic": "stl-set"}])
            self.assertIn(r2["topic"]["id"], ("stl-vector", "stl-remove-erase"))

    def test_low_confidence(self):
        r = A.ask("jak ugotować makaron z sosem pomidorowym")
        self.assertEqual(r["confidence_label"], "niska")
        self.assertTrue(r["alternatives"])

    def test_did_you_mean(self):
        if not kb.get("graph-dijkstra"):
            self.skipTest("brak graph-dijkstra")
        r = A.ask("dijsktra kood")
        self.assertEqual(r["topic"]["id"], "graph-dijkstra")

    def test_compiler_answer(self):
        err = "a.cpp: In function 'int main()':\na.cpp:5:5: error: 'x' was not declared in this scope"
        r = A.ask(err)
        self.assertEqual(r["intent"]["id"], "compiler")
        self.assertTrue(any(a["view"] == "errors" for a in r["actions"]))

    def test_code_answer(self):
        code = "#include <bits/stdc++.h>\nusing namespace std;\nint main(){\n priority_queue<pair<int,int>> pq;\n" \
               " vector<long long> dist(10, 1e18);\n dist[0]=0; pq.push({0,0});\n}"
        r = A.ask(code)
        self.assertEqual(r["intent"]["id"], "code_paste")
        self.assertTrue(any(a["view"] == "checker" for a in r["actions"]))

    def test_task_answer(self):
        r = A.ask("Mam zadanie: n ≤ 100000 miast i m dróg z długościami, znajdź najkrótszą drogę z 1 do n. Jaki algorytm?")
        self.assertEqual(r["intent"]["id"], "task")
        self.assertTrue(any(a["view"] == "suggest" for a in r["actions"]))

    def test_empty_question(self):
        from oiforge.server_api import ApiError
        with self.assertRaises(ApiError):
            A.handle_ask({"q": "   "})


def run_big(cases) -> dict:
    """Pomiar na dużym zestawie (tests/assistant_cases.py): top-1, top-3, kod w odpowiedzi, czasy."""
    ids = {a["id"] for a in kb.articles()}
    t1 = t3 = tot = code_ok = code_tot = 0
    fails, times = [], []
    for q, exp, *rest in cases:
        opt = rest[0] if rest else {}
        if not ids & set(exp):
            continue
        tot += 1
        t = time.perf_counter()
        r = A.ask(q)
        times.append(time.perf_counter() - t)
        got = (r.get("topic") or {}).get("id")
        ranked = r.get("ranking") or [got]
        g1 = got in exp and (not opt.get("intent") or r["intent"]["id"] == opt["intent"])
        t1 += g1
        t3 += any(x in exp for x in ranked[:3])
        if opt.get("code"):
            code_tot += 1
            code_ok += any(b.get("type") == "code" for b in r["blocks"])
        if not g1:
            fails.append((q, got, ranked[:3], exp))
    times.sort()
    return {"top1": t1 / max(1, tot), "top3": t3 / max(1, tot), "total": tot, "fails": fails,
            "code": code_ok / max(1, code_tot), "median_s": times[len(times) // 2] if times else 0.0,
            "p95_s": times[int(0.95 * (len(times) - 1))] if times else 0.0}


class TestBigRegression(unittest.TestCase):
    """≥ 150 realistycznych pytań (czynność + obiekt, potocznie, bez ogonków, literówki, „co to”, „X vs Y”…)."""

    @classmethod
    def setUpClass(cls):
        A.engine().state(wait=True)
        A.engine().wait_nn(timeout=900 if os.environ.get("OIFORGE_TEST_NN") else 20)
        A.ask("rozgrzewka")
        cls.res = run_big(AC.CASES)

    def test_size(self):
        self.assertGreaterEqual(len(AC.CASES), 150)

    def test_top1_top3(self):
        r = self.res
        print(f"\n[duża regresja] top-1 {r['top1']:.1%}, top-3 {r['top3']:.1%} z {r['total']} pytań; "
              f"kod {r['code']:.0%}; mediana {1000 * r['median_s']:.0f} ms, p95 {1000 * r['p95_s']:.0f} ms",
              file=sys.stderr)
        for f in r["fails"]:
            print(f"   ✗ {f[0]!r} → {f[1]} {f[2]}, oczekiwano {f[3]}", file=sys.stderr)
        self.assertGreaterEqual(r["top1"], 0.85, r["fails"])
        self.assertGreaterEqual(r["top3"], 0.95, r["fails"])

    def test_code_in_howto_answers(self):
        self.assertGreaterEqual(self.res["code"], 0.95)

    def test_speed(self):
        # odpowiedź (bez dociąganych osobno „Twoich rozwiązań”) ma przychodzić w < 300 ms
        self.assertLess(self.res["median_s"], 0.3)

    def test_heldout(self):
        r = run_big(AC.HELDOUT)
        print(f"\n[zestaw kontrolny] top-1 {r['top1']:.1%}, top-3 {r['top3']:.1%} z {r['total']}", file=sys.stderr)
        for f in r["fails"]:
            print(f"   ✗ {f[0]!r} → {f[1]} {f[2]}, oczekiwano {f[3]}", file=sys.stderr)
        self.assertGreaterEqual(r["top3"], 0.85, r["fails"])


class TestActionObject(unittest.TestCase):
    """„Jak zrobić X z Y”: czynność ma większą wagę niż obiekt."""

    def test_detection(self):
        ao = A.action_object("jak posortować vector par po drugim elemencie")
        self.assertEqual((ao["action"], ao["kind"]), ("sort", "algo"))
        self.assertIn("pair", ao["objects"])
        self.assertGreater(ao["factors"]["stl-sort"], 1.0)
        self.assertLess(ao["factors"]["stl-pair"], 1.0)
        # set ma własny porządek i własne metody — artykuł o obiekcie wygrywa
        ao = A.action_object("jak zrobic zeby set sortowal malejaco")
        self.assertGreater(ao["factors"].get("stl-set", 1.0), 1.0)
        self.assertNotIn("stl-sort", ao["factors"])
        ao = A.action_object("jak usunąć element z seta")
        self.assertEqual(ao["action"], "remove")
        self.assertGreater(ao["factors"].get("stl-set", 1.0), 1.0)
        self.assertEqual(A.action_object("usuwanie duplikatow z wektora")["action"], "dedupe")
        self.assertEqual(A.action_object("dijkstra z kolejka priorytetowa")["residual"], ["dijkstra"])

    def test_reported_bug_sort_pairs_by_second(self):
        A.engine().state(wait=True)
        for q in ("jak posortować vector par po drugim elemencie", "jak posortowac pary po second",
                  "sortowanie vectora par po drugim elemencie"):
            r = A.ask(q)
            self.assertIn(r["topic"]["id"], ("stl-sort", "pit-comparator"), q)
            code = "\n".join(b["code"] for b in r["blocks"] if b["type"] == "code")
            self.assertIn(".second", code, q)

    def test_action_beats_object_more(self):
        A.engine().state(wait=True)
        cases = {"jak odwrocic vector": "stl-reverse-rotate", "usuwanie duplikatow z wektora": "stl-unique",
                 "sortowanie vectora malejaco": ("stl-sort", "pit-comparator"), "jak usunąć element z seta": "stl-set",
                 "jak dodac element do seta": "stl-set"}
        for q, want in cases.items():
            got = A.ask(q)["topic"]["id"]
            self.assertIn(got, want if isinstance(want, tuple) else (want,), q)


class TestUnknown(unittest.TestCase):
    def test_honest_dont_know(self):
        A.engine().state(wait=True)
        for q in AC.UNKNOWN:
            r = A.ask(q)
            self.assertEqual(r["confidence_label"], "niska", q)
            self.assertTrue(r["alternatives"], q)
            self.assertIsNone(r["topic"], q)
            self.assertIn("Nie wiem", r["blocks"][0]["md"], q)

    def test_known_question_is_answered(self):
        A.engine().state(wait=True)
        for q in ("popcount", "stos c++", "wyszukiwanie binrane"):
            self.assertIsNotNone(A.ask(q)["topic"], q)

    def test_programs_deferred(self):
        r = A.ask("dijkstra kod")
        self.assertEqual(r["programs"], [])
        self.assertTrue(r.get("programs_query"))
        self.assertIn("programs", A.handle_programs({"q": "dijkstra"}))


class TestSuggestGlobal(unittest.TestCase):
    def test_suggest(self):
        s = A.suggest("jak wczy")
        self.assertTrue(s)
        self.assertTrue(all("text" in x for x in s))

    def test_suggest_fuzzy_fallback(self):
        self.assertIsInstance(A.suggest("dijsktra"), list)

    def test_global(self):
        r = A.global_search("set")
        self.assertTrue(r["results"])
        types = {x["type"] for x in r["results"]}
        self.assertIn("article", types)
        for x in r["results"]:
            for k in ("type", "title", "view", "params", "snippet", "score"):
                self.assertIn(k, x)

    def test_examples_status(self):
        self.assertTrue(A.examples(6))
        st = A.status()
        self.assertGreater(st["units"], 0)


class TestDocs(unittest.TestCase):
    def test_tree(self):
        t = D.handle_tree({})
        self.assertTrue(t["categories"])
        total = sum(c["count"] for c in t["categories"])
        self.assertEqual(total, len(kb.articles()))

    def test_article(self):
        aid = kb.articles()[0]["id"]
        r = D.handle_article({"id": aid})
        self.assertEqual(r["article"]["id"], aid)
        self.assertTrue(r["toc"])
        self.assertIn("neighbors", r)

    def test_article_missing(self):
        from oiforge.server_api import ApiError
        with self.assertRaises(ApiError):
            D.handle_article({"id": "nie-ma-takiego"})

    def test_search(self):
        if not kb.get("stl-set"):
            self.skipTest("brak stl-set")
        r = D.handle_search({"q": "zbiór uporządkowany"})
        self.assertTrue(r["results"])
        self.assertIn("stl-set", [x["id"] for x in r["results"][:3]])


if __name__ == "__main__":
    unittest.main()

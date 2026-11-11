"""Testy objaśniacza treści (oiforge.tools.taskexplain): „W skrócie”, typ zadania, struktura, zmienne, przykład
rozpisany, pułapki i złożoność. Wszystkie treści poniżej są własne i krótkie (nie pochodzą z danych ucznia)."""
import json
import unittest

from oiforge import config
from oiforge.tools import taskexplain as TE
from tests.test_statement import SIX_AS_LE

TREE_STORY = """Sady

Bajtocja słynie z najpiękniejszych sadów na świecie. Pewnego dnia ogrodnik Bajtazar postanowił zebrać owoce.
Dane jest drzewo o n wierzchołkach, w każdym wierzchołku i rośnie a_i jabłek. Ogrodnik zaczyna w wierzchołku 1
i schodzi w dół drzewa, aż dojdzie do liścia. Policz, ile najwięcej jabłek może zebrać.

Wejście
W pierwszym wierszu znajduje się jedna liczba całkowita n (1 ≤ n ≤ 300 000), oznaczająca liczbę wierzchołków.
W drugim wierszu znajduje się n liczb całkowitych a_1, ..., a_n (0 ≤ a_i ≤ 10^9), oznaczających liczby jabłek.
W kolejnych n - 1 wierszach znajdują się po dwie liczby u i v (1 ≤ u, v ≤ n) — krawędzie drzewa.

Wyjście
Wypisz jedną liczbę całkowitą — największą liczbę jabłek, jaką może zebrać ogrodnik.

Przykład
Wejście:
4
5 1 7 2
1 2
1 3
3 4
Wyjście:
14
"""

QUERIES = """Lampki

Dany jest napis z liter o (zapalona) i x (zgaszona) opisujący n lampek. Należy obsłużyć q zapytań.

Wejście
W pierwszym wierszu znajdują się dwie liczby całkowite n i q (1 ≤ n, q ≤ 500 000), oznaczające odpowiednio liczbę lampek
i liczbę zapytań. W drugim wierszu znajduje się napis długości n. W kolejnych q wierszach znajdują się zapytania.
Zapytanie typu P zawiera dwie liczby a i b (1 ≤ a ≤ b ≤ n), oznaczające przedział, na którym przełączamy lampki.
Zapytanie typu C zawiera liczbę x (1 ≤ x ≤ n), oznaczającą numer lampki, o którą pytamy.

Wyjście
Dla każdego zapytania typu C wypisz w osobnym wierszu ON albo OFF.

Przykład
Wejście:
4 4
oxxo
C 2
P 1 3
C 2
C 4
Wyjście:
OFF
ON
ON
"""

VALUE_ONLY = """Dzielniki

Dana jest liczba n. Wypisz liczbę jej dzielników.

Wejście
W jedynym wierszu znajduje się liczba całkowita n (1 ≤ n ≤ 10^12).

Wyjście
Wypisz jedną liczbę — liczbę dzielników n.

Przykład
Wejście:
12
Wyjście:
6
"""

LOCK = """Kłódka

Na kłódce wyświetla się liczba n. Jednym ruchem możesz zwiększyć ją o 1 albo podwoić.

Wejście
Jedna liczba całkowita n (1 ≤ n ≤ 100 000) — liczba na kłódce.

Wyjście
Wypisz jedną liczbę — najmniejszą liczbę ruchów potrzebną, aby dojść od 1 do n.

Przykład
Wejście:
5
Wyjście:
3
"""

WEIGHTED_DAG = """Kanały

Sieć kanałów składa się z n śluz połączonych m jednokierunkowymi kanałami. Kanały nie tworzą cykli.

Wejście
W pierwszym wierszu znajdują się liczby n i m (2 ≤ n ≤ 100 000, 1 ≤ m ≤ 200 000), oznaczające liczbę śluz i kanałów.
W kolejnych m wierszach znajdują się trzy liczby a, b, w (1 ≤ a, b ≤ n, 1 ≤ w ≤ 10^9) — kanał z a do b o długości w.

Wyjście
Wypisz liczbę różnych dróg ze śluzy 1 do śluzy n modulo 10^9 + 9.

Przykład
Wejście:
3 3
1 2 4
2 3 1
1 3 7
Wyjście:
2
"""

MULTITEST = """Pary

Dla każdego zestawu danych sprawdź, czy w ciągu są dwie równe liczby.

Wejście
W pierwszym wierszu znajduje się liczba zestawów t (1 ≤ t ≤ 10 000). Każdy zestaw składa się z dwóch wierszy:
w pierwszym jest liczba n (1 ≤ n ≤ 200 000), a w drugim n liczb a_i (-10^9 ≤ a_i ≤ 10^9).
Suma n we wszystkich zestawach nie przekracza 200 000.

Wyjście
Dla każdego zestawu wypisz TAK albo NIE.

Przykład
Wejście:
2
3
1 2 1
2
5 6
Wyjście:
TAK
NIE
"""

GRID = """Labirynt

Dana jest plansza n × m złożona z pól wolnych (.) i ścian (#). Wyznacz najkrótszą drogę z lewego górnego do prawego
dolnego pola, poruszając się do sąsiednich pól.

Wejście
W pierwszym wierszu znajdują się liczby n i m (1 ≤ n, m ≤ 1000). W kolejnych n wierszach znajduje się po m znaków.

Wyjście
Wypisz długość najkrótszej drogi albo -1, jeśli nie da się dojść.

Przykład
Wejście:
2 3
..#
#..
Wyjście:
3
"""


class TestBriefAndSummary(unittest.TestCase):
    def test_tree_brief_and_story_filtered(self):
        r = TE.explain(TREE_STORY)
        b = r["brief"]["text"]
        self.assertIn("drzewo", b)
        self.assertIn("n ≤ 3·10^5", b)
        self.assertIn("a_i", b)
        self.assertIn("największą liczbę jabłek", b)
        # fabularne zdania nie otwierają streszczenia
        self.assertNotIn("słynie", r["summary"][0])
        self.assertNotIn("Pewnego dnia", r["summary"][0])
        self.assertEqual(r["task_type"]["id"], "optimize")

    def test_tree_structure_and_walkthrough(self):
        r = TE.explain(TREE_STORY)
        st = r["structure"]
        self.assertEqual(st["kind"], "tree")
        self.assertTrue(st["verified"])
        self.assertTrue(any("drzewo" in n for n in st["notes"]))
        w = r["walkthrough"][0]
        self.assertEqual(w["input"][0], "n = 4")
        self.assertEqual(w["input"][1], "a = [5, 1, 7, 2]")
        self.assertIn("1–2, 1–3, 3–4", w["input"][2])
        self.assertEqual(w["output"], "wynik = 14")
        hints = " ".join(h["text"] for h in r["complexity"]["hints"])
        self.assertIn("DP na drzewie", hints)
        self.assertNotIn("Dijkstra", hints)
        pit = {p["id"] for p in r["pitfalls"]}
        self.assertIn("sum-overflow", pit)          # 3·10^5 · 10^9 > int
        self.assertIn("deep-recursion", pit)


class TestQueries(unittest.TestCase):
    def test_query_types_and_prints(self):
        r = TE.explain(QUERIES)
        qs = {q["tag"]: q for q in r["structure"]["queries"]}
        self.assertEqual(set(qs), {"C", "P"})
        self.assertEqual(qs["P"]["params"], ["a", "b"])
        self.assertEqual(qs["C"]["params"], ["x"])
        self.assertTrue(qs["C"]["prints"])
        self.assertFalse(qs["P"]["prints"])
        self.assertIn("przedział", qs["P"]["meaning"])
        self.assertEqual(r["task_type"]["id"], "queries")
        self.assertIn("Operacje:", r["brief"]["text"])
        self.assertIn("odpowiedź na każde C", r["brief"]["text"])

    def test_query_walkthrough_maps_answers(self):
        r = TE.explain(QUERIES)
        w = r["walkthrough"][0]
        line = next(x for x in w["input"] if x.startswith("zapytania"))
        self.assertIn("C 2 → OFF", line)
        self.assertIn("P 1 3,", line)
        self.assertIn("C 4 → ON", line)
        self.assertIsNone(w["output"])               # wyjście zużyte przez zapytania

    def test_naive_cost_and_hint(self):
        r = TE.explain(QUERIES)
        cx = r["complexity"]
        self.assertIn("za wolno", cx["note"])
        self.assertTrue(any("różnicach" in h["text"] or "Fenwick" in h["text"] for h in cx["hints"]))

    def test_operations_with_letters_no_bogus_vars(self):
        r = TE.explain(SIX_AS_LE)
        names = [v["name"] for v in r["variables"]]
        self.assertNotIn("c", names)
        self.assertNotIn("s", names)
        n = next(v for v in r["variables"] if v["name"] == "n")
        self.assertEqual(n["max_fmt"], "5000")
        q = next(v for v in r["variables"] if v["name"] == "q")
        self.assertEqual(q["max_fmt"], "5000")
        self.assertNotIn("√", r["complexity"]["complexity"])     # wcześniej „6” zamiast ≤ dawało n ≤ 10^9 → O(√n)
        self.assertIn("2,5·10^7", r["complexity"]["note"])       # q·n naiwnie mieści się w budżecie
        ops = r["structure"]["queries"][0]
        self.assertEqual(ops["params"], ["a", "b", "R/M"])
        for v in r["variables"]:
            self.assertLessEqual(len(v["meaning"]), 85)


class TestValuesVsSizes(unittest.TestCase):
    def test_value_only_uses_sqrt(self):
        r = TE.explain(VALUE_ONLY)
        cx = r["complexity"]
        self.assertEqual(cx["basis"], "wartość")
        self.assertEqual(cx["complexity"], "O(√n)")
        n = next(v for v in r["variables"] if v["name"] == "n")
        self.assertEqual(n["kind"], "value")
        self.assertEqual(r["structure"]["kind"], "values")

    def test_lock_value_not_sorting(self):
        r = TE.explain(LOCK)
        cx = r["complexity"]
        self.assertEqual(cx["basis"], "wartość")
        self.assertEqual(cx["complexity"], "O(n)")
        self.assertNotIn("sortowanie", json.dumps(cx, ensure_ascii=False))
        self.assertIn("wartość", cx["note"])
        self.assertNotIn("fast-io", {p["id"] for p in r["pitfalls"]})


class TestGraphsGridsTests(unittest.TestCase):
    def test_weighted_dag(self):
        r = TE.explain(WEIGHTED_DAG)
        st = r["structure"]
        self.assertEqual(st["kind"], "graph")
        self.assertTrue(st["flags"]["directed"])
        self.assertTrue(st["flags"]["weighted"])
        self.assertIn("skierowany", st["label"])
        self.assertEqual(r["task_type"]["id"], "count")
        self.assertEqual(r["task_type"]["modulo"], "10^9+9")
        mod = next(p for p in r["pitfalls"] if p["id"] == "modulo")
        self.assertIn("nie 10^9+7", mod["text"])
        self.assertTrue(any("topologiczne" in h["text"] for h in r["complexity"]["hints"]))
        self.assertIn("2→3", " ".join(r["walkthrough"][0]["input"]))

    def test_multitest_with_sum(self):
        r = TE.explain(MULTITEST)
        self.assertTrue(r["structure"]["flags"]["multitest"])
        self.assertEqual(r["task_type"]["id"], "decision")
        self.assertEqual(r["task_type"]["words"][:2], ["TAK", "NIE"])
        mt = next(p for p in r["pitfalls"] if p["id"] == "multitest")
        self.assertIn("200 000", mt["text"])
        self.assertIn("negative", {p["id"] for p in r["pitfalls"]})
        w = r["walkthrough"][0]["input"]
        self.assertTrue(w[0].startswith("t = 2"))
        self.assertIn("zestaw 1", w[1])

    def test_grid_bfs_and_no_solution(self):
        r = TE.explain(GRID)
        self.assertEqual(r["structure"]["kind"], "grid")
        self.assertTrue(any("BFS" in h["text"] for h in r["complexity"]["hints"]))
        self.assertIn("no-solution", {p["id"] for p in r["pitfalls"]})
        self.assertIn("plansza", r["brief"]["text"])

    def test_backward_compatible_fields(self):
        r = TE.explain(TREE_STORY)
        for k in ("title", "summary", "given", "goal", "input", "output", "variables", "samples", "pitfalls",
                  "subtasks", "complexity", "glossary", "keywords", "warnings", "sections", "limits", "meta"):
            self.assertIn(k, r)
        for k in ("var", "max", "max_fmt", "complexity", "hint", "note", "sizes"):
            self.assertIn(k, r["complexity"])
        self.assertTrue(all("text" in f for f in r["input"]["format"]))


HAVE_DATA = len(list(config.PROGRAMS_DIR.glob("*/*/statements/*.txt"))) >= 50


@unittest.skipUnless(HAVE_DATA, "brak danych ucznia w user_data/programs")
class TestOnStudentStatements(unittest.TestCase):
    def test_corpus_runs_and_has_brief(self):
        files = sorted(config.PROGRAMS_DIR.glob("*/*/statements/*.txt"))[:60]
        ok = brief = 0
        for f in files:
            try:
                r = TE.explain(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — treść bez sekcji to ApiError
                continue
            ok += 1
            brief += bool(r["brief"]["text"])
            for w in r["walkthrough"]:
                self.assertTrue(w["input"])
        self.assertGreater(ok, 40)
        self.assertGreaterEqual(brief, ok - 2)


if __name__ == "__main__":
    unittest.main()

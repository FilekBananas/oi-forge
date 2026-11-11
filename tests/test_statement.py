"""Testy parsera treści zadań (oiforge.core.statement).

Część testów działa na syntetycznych treściach (artefakty PDF, HTML, wklejony tekst), część na prawdziwych treściach
ucznia z user_data/programs — te są pomijane, gdy danych brak."""
import glob
import os
import unittest

from oiforge import config
from oiforge.core import runner
from oiforge.core import statement as ST

STATEMENTS = sorted(glob.glob(str(config.PROGRAMS_DIR / "*" / "*" / "statements" / "*.txt")) +
                    glob.glob(str(config.PROGRAMS_DIR / "*" / "*" / "statements" / "*.html")))

# Tekst „jak z PDF”: linie oddzielone pustą linią, potęgi w osobnej linii, przenoszone wyrazy.
PDF_GRAPH = """Odległości

Kółko testowe – podstawy Kod zadania: odl

BFS Limit pamięci: 64 MiB

Dany jest graf nieskierowany. Policz odległość każdego wierzchołka od wierzchołka nr 1, repre-

zentowaną liczbą krawędzi.

Wejście

W pierwszej linii wejścia znajdują się dwie liczby całkowite: n, m (1 ≤ n ≤ 100 000, 0 ≤ m ≤ 2 · 10
5
),

oznaczające odpowiednio liczbę wierzchołków i liczbę krawędzi. W kolejnych m liniach opisane są krawędzie.

Opis jednej krawędzi składa się z dwóch liczb całkowitych: a, b (1 ≤ a, b ≤ n, a̸ = b).

Wyjście

Na wyjście należy wypisać n linii: odległość i-tego wierzchołka albo −1, jeśli nie da się do niego dojść.

Przykład

Wejście dla testu odl0:

4 3

1 2

2 3

1 3

Wyjście dla testu odl0:

0

1

1

-1

Ocenianie

Podzadanie Ograniczenia Limit czasu Liczba punktów

1 n, m ≤ 10 0, 5 s 30

2 brak dodatkowych założeń 1 s 70

Odległości

© 2026 Szkoła

1/1
"""

PLAIN_ARRAY = """Suma ciągu
Dla danego ciągu liczb oblicz sumę jego elementów.
Wejście
W pierwszym wierszu znajduje się jedna liczba całkowita n (1 <= n <= 10^6). W drugim wierszu znajduje się n liczb
całkowitych a_1, a_2, ..., a_n (|a_i| <= 10^9).
Wyjście
Wypisz jedną liczbę — sumę elementów ciągu.
Przykład
Dla danych wejściowych:
3
1 2 3
Poprawnym wynikiem jest:
6
"""

HTML_STMT = """<html><body><nav>menu</nav><section class="main-content col"><h1>Kwadraty</h1>
<p>Dla każdej liczby wypisz jej kwadrat.</p>
<h2>Wejście</h2><p>W pierwszym wierszu znajduje się liczba zestawów danych t (1 &le; t &le; 10<sup>5</sup>).
W każdym z kolejnych t wierszy znajduje się jedna liczba całkowita x<sub>i</sub> (0 &lt;= x<sub>i</sub> &lt;= 10<sup>9</sup>).</p>
<h2>Wyjście</h2><p>Dla każdego zestawu wypisz x<sup>2</sup>.</p>
<h2>Przykład</h2><p>Dla danych wejściowych:</p><pre>2
3
10</pre><p>poprawnym wynikiem jest:</p><pre>9
100</pre>
</section></div></body></html>"""

SIDE_BY_SIDE = """Dostępna pamięć: 32MB

Plansza

Masz daną planszę liczb rozmiaru 3 × n.

Wejście

W pierwszym wierszu wejścia znajduje się jedna liczba całkowita n (1 ¬ n ¬ 200 000). W n kolejnych wierszach

znajdują się po trzy liczby całkowite x, y, z.

Wyjście

Na wyjście wypisz maksymalną sumę.

Przykład

Wejście Wyjście

2

1 2 3

4 5 6

15

1/1 Plansza
"""

MOJIBAKE = """Lampy

Wej±cie

W pierwszym wierszu wej±cia znajduj¡ si¦ dwie liczby n i m (1 ≤ n, m ≤ 10
6
), oznaczaj¡ce liczb¦ lamp oraz liczb¦ zapyta«.

Wyj±cie

Dla ka»dego zapytania wypisz odpowied¹.

Przykªad

Wej±cie dla testu lam0:

2 1

Wyj±cie dla testu lam0:

7
"""


class TestNumbers(unittest.TestCase):
    def test_parse_value(self):
        self.assertEqual(ST.parse_value("1 000 000"), 10**6)
        self.assertEqual(ST.parse_value("2·10^5"), 200000)
        self.assertEqual(ST.parse_value("2 · 10^5"), 200000)
        self.assertEqual(ST.parse_value("10^9+7"), 10**9 + 7)
        self.assertEqual(ST.parse_value("10^9 + 7"), 10**9 + 7)
        self.assertEqual(ST.parse_value("5*10^5"), 500000)
        self.assertEqual(ST.parse_value("1e9"), 10**9)
        self.assertEqual(ST.parse_value("-10^6"), -10**6)
        self.assertEqual(ST.parse_value("2^31-1"), 2**31 - 1)
        self.assertEqual(ST.parse_value("10^{18}"), 10**18)

    def test_fmt_value(self):
        self.assertEqual(ST.fmt_value(10**6), "10^6")
        self.assertEqual(ST.fmt_value(200000), "2·10^5")
        self.assertEqual(ST.fmt_value(10**9 + 7), "10^9+7")
        self.assertEqual(ST.fmt_value(500), "500")


class TestConstraints(unittest.TestCase):
    def cons(self, text):
        return ST.extract_constraints(text)[0]

    def test_chain_and_lists(self):
        c = self.cons("Dane są liczby n, m (1 ≤ n ≤ 200 000, 0 ≤ m ≤ 10^6).")
        self.assertEqual((c["n"]["min"], c["n"]["max"]), (1, 200000))
        self.assertEqual((c["m"]["min"], c["m"]["max"]), (0, 10**6))
        c = self.cons("Dwie liczby n, m (1 ≤ n, m ≤ 5 · 10^5).")
        self.assertEqual(c["n"]["max"], 500000)
        self.assertEqual(c["m"]["max"], 500000)

    def test_subscripts_and_abs(self):
        c = self.cons("Kolejne liczby a_i spełniają 1 ≤ a_i ≤ 10^9.")
        self.assertEqual(c["a_i"]["max"], 10**9)
        c = self.cons("Liczby ai (−10^9 ≤ ai ≤ 10^9).".replace("−", "-"))
        self.assertEqual(c["a_i"]["min"], -10**9)
        c = self.cons("Każda liczba spełnia |x_i| <= 1000.")
        self.assertEqual((c["x_i"]["min"], c["x_i"]["max"]), (-1000, 1000))

    def test_strict_and_relations(self):
        c, rel = ST.extract_constraints("Liczba k (0 < k < 100). Wierzchołki a, b (1 ≤ a, b ≤ n, a ≠ b).")
        self.assertEqual((c["k"]["min"], c["k"]["max"]), (1, 99))
        self.assertTrue(any(r["type"] == "neq" for r in rel))
        self.assertTrue(any(r["type"] == "le" and r["b"] == "n" for r in rel))

    def test_words(self):
        c = self.cons("Liczby na planszy co do modułu nie będą większe niż 10^9.")
        self.assertEqual(c["_values"]["max"], 10**9)
        self.assertEqual(c["_values"]["min"], -10**9)


class TestParsePdf(unittest.TestCase):
    def setUp(self):
        self.p = ST.parse(PDF_GRAPH)

    def test_header(self):
        self.assertEqual(self.p["title"], "Odległości")
        self.assertEqual(self.p["meta"]["code"], "odl")
        self.assertEqual(self.p["memory_limit"], "64 MiB")
        self.assertEqual(self.p["meta"]["header_note"], "BFS")

    def test_sections_and_cleanup(self):
        s = self.p["sections"]
        self.assertIn("reprezentowaną", s["tresc"])          # przeniesienie wyrazu sklejone
        self.assertIn("2·10^5", s["wejscie"].replace(" ", ""))  # potęga z osobnej linii
        self.assertNotIn("©", " ".join(s.values()))
        self.assertIn("wyjscie", s)

    def test_constraints_and_format(self):
        c = self.p["constraints"]
        self.assertEqual(c["m"]["max"], 200000)
        self.assertEqual(c["n"]["max"], 100000)
        fmt = self.p["input_format"]
        self.assertEqual(fmt[0]["kind"], "scalars")
        self.assertEqual(fmt[0]["vars"], ["n", "m"])
        self.assertEqual(fmt[1]["kind"], "rows")
        self.assertEqual(fmt[1]["repeat"], "m")
        self.assertEqual(fmt[1]["vars"], ["a", "b"])
        self.assertTrue(self.p["format_check"]["ok"])

    def test_samples_and_subtasks(self):
        sm = self.p["samples"]
        self.assertEqual(len(sm), 1)
        self.assertEqual(sm[0]["name"], "odl0")
        self.assertEqual(sm[0]["input"], "4 3\n1 2\n2 3\n1 3\n")
        self.assertEqual(sm[0]["output"], "0\n1\n1\n-1\n")
        st = self.p["subtasks"]
        self.assertEqual([x["points"] for x in st], [30, 70])
        self.assertEqual(st[0]["time_limit"], 0.5)
        self.assertEqual(st[0]["constraints"]["n"]["max"], 10)
        self.assertEqual(self.p["time_limit"], 1.0)


class TestParseOther(unittest.TestCase):
    def test_plain_text(self):
        p = ST.parse(PLAIN_ARRAY)
        self.assertEqual(p["title"], "Suma ciągu")
        self.assertEqual(p["samples"][0]["output"].strip(), "6")
        fmt = p["input_format"]
        self.assertEqual([f["kind"] for f in fmt], ["scalars", "array"])
        self.assertEqual(fmt[1]["length"], "n")
        self.assertEqual(p["constraints"]["a_i"]["max"], 10**9)
        self.assertTrue(p["format_check"]["ok"])

    def test_html(self):
        p = ST.parse(HTML_STMT)
        self.assertEqual(p["title"], "Kwadraty")
        self.assertEqual(p["meta"]["source"], "html")
        self.assertNotIn("menu", p["sections"].get("tresc", ""))
        self.assertEqual(p["constraints"]["t"]["max"], 10**5)
        self.assertEqual(p["samples"][0]["input"], "2\n3\n10\n")
        self.assertEqual(p["samples"][0]["output"], "9\n100\n")
        self.assertEqual(p["input_format"][0]["kind"], "multitest")
        self.assertTrue(p["format_check"]["ok"])

    def test_side_by_side_samples(self):
        p = ST.parse(SIDE_BY_SIDE)
        self.assertEqual(p["title"], "Plansza")
        s = p["samples"][0]
        self.assertEqual(s["input"], "2\n1 2 3\n4 5 6\n")
        self.assertEqual(s["output"].strip(), "15")
        self.assertEqual(p["constraints"]["n"]["max"], 200000)   # „¬” w PDF to „≤”

    def test_mojibake(self):
        p = ST.parse(MOJIBAKE)
        self.assertIn("wejscie", p["sections"])
        self.assertIn("zapytań", p["sections"]["wejscie"])
        self.assertEqual(p["constraints"]["m"]["max"], 10**6)
        self.assertEqual(p["samples"][0]["output"].strip(), "7")

    def test_complexity_table(self):
        self.assertEqual(ST.complexity_for(10)["complexity"], "O(n!)")
        self.assertEqual(ST.complexity_for(20)["complexity"], "O(2^n · n)")
        self.assertEqual(ST.complexity_for(500)["complexity"], "O(n³)")
        self.assertEqual(ST.complexity_for(5000)["complexity"], "O(n²)")
        self.assertEqual(ST.complexity_for(10**6)["complexity"], "O(n log n)")
        self.assertIn("log", ST.complexity_for(10**18)["complexity"])

    @unittest.skipUnless(config.CXX, "brak kompilatora C++")
    def test_cpp_reader_compiles_and_reads(self):
        p = ST.parse(PDF_GRAPH)
        rd = ST.cpp_reader(p["input_format"])
        code = ("#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n" + rd["code"] +
                "\n    cout << n << ' ' << m << ' ' << wiersze.size() << \"\\n\";\n}\n")
        r = runner.run_source(code, p["samples"][0]["input"])
        self.assertTrue(r["ok"], r["compile"])
        self.assertEqual(r["run"]["stdout"].strip(), "4 3 3")


@unittest.skipUnless(len(STATEMENTS) >= 50, "brak prawdziwych treści w user_data/programs")
class TestSizesAndShapes(unittest.TestCase):
    """Regresje: rozmiar danych = liczność (n, q, |s|, wiersze planszy), nie wartość (k, a_i); kształty wejścia."""

    def test_size_is_count_not_value(self):
        t = ("Pary\nPolicz pary i < j, dla których a_i + a_j = k.\nWejście\nW pierwszym wierszu liczby n i k "
             "(1 ≤ n ≤ 100 000, 1 ≤ k ≤ 10^9). W drugim wierszu n liczb a_i (1 ≤ a_i ≤ 10^9).\nWyjście\nLiczba par.\n"
             "Przykład\nWejście:\n4 5\n1 4 2 3\nWyjście:\n2\n")
        p = ST.parse(t)
        self.assertEqual(ST.size_variables(p["input_format"], p["constraints"]), ["n"])
        var, mx, _ = ST.main_size(p["input_format"], p["constraints"])
        self.assertEqual((var, mx), ("n", 100000))
        self.assertEqual(ST.complexity_for(mx)["complexity"], "O(n log n)")

    def test_queries_and_grid_sizes(self):
        t = ("Sumy\nWejście\nW pierwszym wierszu liczby n i q (1 ≤ n, q ≤ 500 000). W drugim wierszu n liczb a_i "
             "(|a_i| ≤ 10^9). W każdym z kolejnych q wierszy dwie liczby l, r (1 ≤ l ≤ r ≤ n).\nWyjście\nSumy.\n"
             "Przykład\nWejście:\n3 1\n1 2 3\n1 2\nWyjście:\n3\n")
        p = ST.parse(t)
        self.assertEqual(set(ST.size_variables(p["input_format"], p["constraints"])), {"n", "q"})
        g = ("Labirynt\nWejście\nW pierwszym wierszu liczby n i m (1 ≤ n, m ≤ 1000). Każdy z kolejnych n wierszy "
             "zawiera m znaków „.” lub „#”.\nWyjście\nLiczba ruchów.\nPrzykład\nWejście:\n2 3\n..#\n#..\nWyjście:\n3\n")
        p = ST.parse(g)
        self.assertEqual([it["kind"] for it in p["input_format"]], ["scalars", "grid"])
        self.assertEqual(ST.main_size(p["input_format"], p["constraints"], product=True)[:2], ("n·m", 10**6))

    def test_pure_numbers_use_largest(self):
        p = ST.parse("Potęga\nWejście\nTrzy liczby a, b, m (1 ≤ a, b ≤ 10^18, 2 ≤ m ≤ 10^9).\nWyjście\na^b mod m.\n"
                     "Przykład\nWejście:\n2 10 1000\nWyjście:\n24\n")
        var, mx, _ = ST.main_size(p["input_format"], p["constraints"])
        self.assertEqual(mx, 10**18)

    def test_zero_indexed_array_length(self):
        t = ("Regały\nWejście\nPierwszy wiersz zawiera liczbę n (1 ≤ n ≤ 1000). Drugi wiersz zawiera n liczb "
             "a_0, a_1, ..., a_{n-1}.\nWyjście\nSuma.\nPrzykład\nWejście:\n3\n1 2 3\nWyjście:\n6\n")
        p = ST.parse(t)
        self.assertTrue(p["format_check"]["ok"])
        arr = next(it for it in p["input_format"] if it["kind"] == "array")
        self.assertEqual(arr["length"], "n")

    def test_array_without_samples(self):
        p = ST.parse("Zadanie\nWejście\nW pierwszym wierszu liczba n (1 ≤ n ≤ 18). W drugim wierszu n liczb a_i "
                     "(1 ≤ a_i ≤ 100).\nWyjście\nWynik.")
        self.assertEqual([(it["kind"], it.get("length")) for it in p["input_format"]], [("scalars", None), ("array", "n")])

    def test_command_rows_are_queries(self):
        t = ("Stos\nWejście\nW pierwszym wierszu liczba operacji q (1 ≤ q ≤ 10^6). Każdy z kolejnych q wierszy zawiera "
             "jedną operację: PUSH x (1 ≤ x ≤ 10^9), POP albo TOP.\nWyjście\nWyniki TOP.\nPrzykład\nWejście:\n3\n"
             "PUSH 5\nTOP\nPOP\nWyjście:\n5\n")
        p = ST.parse(t)
        self.assertEqual([it["kind"] for it in p["input_format"]], ["scalars", "queries"])

    def test_rows_with_name_and_number(self):
        t = ("Ranking\nWejście\nW pierwszym wierszu liczba n (1 ≤ n ≤ 1000). W każdym z kolejnych n wierszy znajduje "
             "się imię zawodnika (napis) oraz liczba całkowita p (0 ≤ p ≤ 100).\nWyjście\nImiona.\nPrzykład\nWejście:\n"
             "2\nOla 5\nJan 7\nWyjście:\nJan\nOla\n")
        p = ST.parse(t)
        rows = p["input_format"][1]
        self.assertEqual(rows["kind"], "rows")
        self.assertEqual(rows["types"][0], "string")
        self.assertEqual(rows["vars"][1], "p")

    def test_string_length_bounds(self):
        p = ST.parse("Wspólny podciąg\nWejście\nW pierwszym wierszu słowo a, w drugim słowo b. Oba słowa mają długość "
                     "od 1 do 5000.\nWyjście\nDługość.\nPrzykład\nWejście:\nabc\nacb\nWyjście:\n2\n")
        self.assertEqual(p["constraints"].get("|a|", {}).get("max"), 5000)
        self.assertEqual(ST.main_size(p["input_format"], p["constraints"])[1], 5000)


class TestBenchStatements(unittest.TestCase):
    """Zestaw sprawdzający (tests/data/statements): treści się parsują, przykłady zgadzają się z wzorcówkami."""

    def test_bench_samples_match_references(self):
        from tests import bench_smart as B
        self.assertGreaterEqual(len(B.TASKS), 30)
        self.assertEqual(B.check_samples_against_refs(), [])
        ok = sum(ST.parse(B.statement(n))["format_check"]["ok"] for n in B.TASKS)
        self.assertGreaterEqual(ok, len(B.TASKS) - 1)


@unittest.skipUnless(len(STATEMENTS) >= 50, "brak zaimportowanych treści zadań (user_data/programs)")
class TestRealStatements(unittest.TestCase):
    """Skuteczność na prawdziwych treściach ucznia (PDF→txt i HTML z SIO2)."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = []
        for f in STATEMENTS:
            with open(f, encoding="utf-8", errors="replace") as fh:
                cls.parsed.append((os.path.basename(f), ST.parse(fh.read())))

    def rate(self, pred):
        return sum(1 for _, p in self.parsed if pred(p)) / len(self.parsed)

    def test_sections(self):
        self.assertGreaterEqual(self.rate(lambda p: bool(p["sections"].get("wejscie"))), 0.95)
        self.assertGreaterEqual(self.rate(lambda p: bool(p["sections"].get("wyjscie"))), 0.95)

    def test_samples(self):
        self.assertGreaterEqual(self.rate(lambda p: bool(p["samples"])), 0.95)

    def test_constraints(self):
        self.assertGreaterEqual(self.rate(lambda p: bool(p["constraints"])), 0.80)

    def test_format_verified_on_samples(self):
        self.assertGreaterEqual(self.rate(lambda p: p["format_check"]["ok"]), 0.92)

    def test_titles_and_memory(self):
        self.assertGreaterEqual(self.rate(lambda p: bool(p["title"])), 0.97)
        self.assertGreaterEqual(self.rate(lambda p: bool(p["memory_limit"])), 0.9)


# ============================================================================================ testy „ocen”, duże liczby
# Własne, krótkie treści w stylu współczesnych zadań OI (nie kopie treści z OI).
BIG_DIGITS = """Zadanie: CYF
Cyfrowy licznik
Bajtek ma licznik, który pokazuje liczbę n. Policz, ile cyfr różnych od zera ma zapis dziesiętny liczby n.
Wejście
W pierwszym i jedynym wierszu wejścia znajduje się jedna liczba całkowita n (1 ≤ n ≤ 10^200000).
Wyjście
Wypisz jedną liczbę — liczbę niezerowych cyfr n.
Przykład
Dla danych wejściowych:
305
poprawnym wynikiem jest:
2
Wyjaśnienie do przykładu: niezerowe są cyfry 3 i 5.
Testy przykładowe: Test 0 to test z przykładu powyżej. Poza tym:
1ocen: n = 1 000 000; odpowiedź to 1;
2ocen: n = 9 · 10^150000; odpowiedź to 1;
3ocen: n = 10^5, losowe cyfry; odpowiedź to 5.
Ocenianie
Podzadanie Ograniczenia Punkty
1 n ≤ 10^18 40
2 n ≤ 10^1000 30
3 brak dodatkowych ograniczeń 30
"""

TWO_VARS_OCEN = """Przedział
Dane są liczby a i b. Wypisz, ile jest liczb całkowitych x takich, że a ≤ x ≤ b.
Wejście
W pierwszym wierszu znajdują się dwie liczby całkowite a, b (1 ≤ a ≤ b ≤ 10^18).
Wyjście
Jedna liczba.
Przykład
Dla danych wejściowych:
3 7
poprawnym wynikiem jest:
5
Testy przykładowe: Test 0a to test z przykładu powyżej. Poza tym:
1ocen: b = 10, a = 1. Odpowiedź 10.
2ocen: a = b = 10^18. Odpowiedź to 1.
3ocen: a = 2, b = 10^9; przykładowa odpowiedź to 999999999.
0d: a = 5, b = 6, odpowiedź: 2.
4ocen: a = 7, b = 1 000 000, a wynik to
999994.
"""

GRID_OCEN = """Pola
Policz wolne pola planszy.
Wejście
W pierwszym wierszu znajduje się liczba całkowita n (1 ≤ n ≤ 1000). Każdy z kolejnych n wierszy zawiera n znaków . lub #.
Wyjście
Jedna liczba.
Przykład
Dla danych wejściowych:
2
.#
..
poprawnym wynikiem jest:
3
Testy przykładowe: Test 0 to test z przykładu powyżej. Poza tym:
1ocen: n = 1000, wszystkie pola wolne; odpowiedź to 1000000.
"""

LAYOUT_TWO_COLUMNS = """Podwojenie
Wypisz podwojoną sumę liczb.
Wejście
W pierwszym wierszu znajduje się liczba całkowita n (1 ≤ n ≤ 1000). W drugim wierszu znajduje się n liczb
całkowitych a_i (1 ≤ a_i ≤ 1000).
Wyjście
Jedna liczba.
Przykład
Dla danych wejściowych:                         poprawnym wynikiem jest:
3                                               12
1 2 3
Olimpiada Informatyczna (oi.edu.pl) 1/2
Podwojenie (v. 1.0)
Wyjaśnienie do przykładu: 2 · (1 + 2 + 3) = 12.
"""


class TestOcenAndBigNumbers(unittest.TestCase):
    def test_bignum_constraint(self):
        p = ST.parse(BIG_DIGITS)
        c = p["constraints"]["n"]
        self.assertTrue(c["bignum"])
        self.assertIsNone(c["max"])
        self.assertEqual((c["max_digits"], c["max_lead"], c["max_pow10"]), (200001, "1", 200000))
        self.assertEqual(ST.fmt_big(c), "10^200000")
        self.assertEqual(p["input_format"][0]["types"], ["bignum"])
        rd = ST.cpp_reader(p["input_format"])["code"]
        self.assertIn("string n;", rd)
        sub = {s["id"]: s["constraints"].get("n", {}) for s in p["subtasks"]}
        self.assertFalse(sub[1].get("bignum"))                 # n ≤ 10^18 mieści się w long long
        self.assertEqual(sub[2].get("max_digits"), 1001)       # n ≤ 10^1000 — 1001 cyfr

    def test_ocen_samples_scalar(self):
        p = ST.parse(BIG_DIGITS)
        names = [s["name"] for s in p["samples"]]
        self.assertEqual(names, ["przykład 1", "1ocen", "2ocen"])          # 3ocen jest losowy — pominięty
        o1, o2 = p["samples"][1], p["samples"][2]
        self.assertEqual((o1["input"], o1["output"], o1["source"]), ("1000000\n", "1\n", "ocen"))
        self.assertEqual(o2["input"], "9" + "0" * 150000 + "\n")             # napis cyfr, bez inta
        self.assertTrue(o2["big"])
        self.assertLess(len(o2["input_preview"]), 200)
        self.assertNotIn("ocen", p["samples"][0].get("explanation", ""))    # wyjaśnienie bez listy testów
        self.assertIn("testy_ocen", p["sections"])
        self.assertTrue(p["format_check"]["ok"])

    def test_ocen_two_vars_and_variants(self):
        p = ST.parse(TWO_VARS_OCEN)
        got = {s["name"]: (s["input"], s["output"]) for s in p["samples"] if s.get("source") == "ocen"}
        self.assertEqual(got["1ocen"], ("1 10\n", "10\n"))                   # kolejność z formatu, nie z opisu
        self.assertEqual(got["2ocen"], (f"{10**18} {10**18}\n", "1\n"))     # „a = b = 10^18”
        self.assertEqual(got["0d"], ("5 6\n", "2\n"))                        # etykieta bez „ocen” po nagłówku
        self.assertEqual(got["4ocen"], ("7 1000000\n", "999994\n"))          # odpowiedź w następnej linii
        self.assertNotIn("3ocen", got)                                     # „przykładowa odpowiedź” — checker

    def test_ocen_skipped_when_input_not_scalar(self):
        p = ST.parse(GRID_OCEN)
        self.assertFalse([s for s in p["samples"] if s.get("source") == "ocen"])

    def test_ocen_lost_exponent_rejected(self):
        txt = BIG_DIGITS.replace("9 · 10^150000", "9 · 10150 000")         # wykładnik zgubiony przy kopiowaniu
        p = ST.parse(txt)
        self.assertNotIn("2ocen", [s["name"] for s in p["samples"]])

    def test_two_column_layout_and_footer(self):
        p = ST.parse(LAYOUT_TWO_COLUMNS)
        self.assertEqual(len(p["samples"]), 1)
        s = p["samples"][0]
        self.assertEqual((s["input"], s["output"]), ("3\n1 2 3\n", "12\n"))
        self.assertFalse(s.get("uncertain"))
        self.assertIn("przyklad", p["sections"])
        self.assertNotIn("Olimpiada", " ".join(p["sections"].values()))
        self.assertNotIn("(v. 1.0)", " ".join(p["sections"].values()))

    def test_two_columns_collapsed(self):
        # ten sam przykład skopiowany bez układu kolumn: „3 12” / „1 2 3” — wynik to ostatnie słowo pierwszej linii
        txt = LAYOUT_TWO_COLUMNS.replace("Dla danych wejściowych:                         poprawnym wynikiem jest:",
                                         "Dla danych wejściowych: poprawnym wynikiem jest:")
        txt = txt.replace("3                                               12", "3 12")
        s = ST.parse(txt)["samples"][0]
        self.assertEqual((s["input"], s["output"]), ("3\n1 2 3\n", "12\n"))

    def test_value_text_and_big_number(self):
        self.assertEqual(ST.value_text("1 000 000"), "1000000")
        self.assertEqual(ST.value_text("10^9"), "1000000000")
        self.assertEqual(ST.value_text("9 · 10^20"), "9" + "0" * 20)
        self.assertEqual(len(ST.value_text("10^999999")), 1000000)
        self.assertIsNone(ST.big_number("10^18"))                          # mieści się w long long
        self.assertEqual(ST.big_number("10^19")["digits"], 20)
        self.assertEqual(ST.big_number("10^30 - 1"), {"lead": "9", "pow10": 0, "nines": 30, "digits": 30})



# „≤” zapisane przez font PDF jako „6” (albo znak sterujący 0x14) — treści własne, krótkie
SIX_AS_LE = """Sortowanie kawałków

Dany jest ciąg liczb i lista operacji sortujących jego fragmenty.

Wejście

W pierwszym wierszu znajdują się dwie liczby całkowite n i q (1 6 n, q 6 5 000), oznaczające długość ciągu i liczbę operacji.

W drugim wierszu znajduje się n liczb całkowitych.

W kolejnych q wierszach znajdują się opisy operacji: dwie liczby a i b (1 6 a 6 b 6 n) oraz litera R lub M.

Wyjście

Wypisz ciąg po wszystkich operacjach.

Przykład

Wejście:

6 2

0 8 2 0 1 6

1 6 R

2 4 M

Wyjście:

0 8 6 2 1 0

Ocenianie

Podzadanie Ograniczenia Punkty

1 n, q 6 1 000 40

2 brak dodatkowych założeń 60
"""


class TestLeqEncodedAsSix(unittest.TestCase):
    def test_six_is_leq_in_text_only(self):
        p = ST.parse(SIX_AS_LE)
        c = p["constraints"]
        self.assertEqual((c["n"]["min"], c["n"]["max"]), (1, 5000))
        self.assertEqual((c["q"]["min"], c["q"]["max"]), (1, 5000))
        self.assertEqual(c["a"]["min"], 1)
        # przykład nietknięty: „1 6 R” to operacja, „0 8 2 0 1 6” to dane
        self.assertIn("1 6 R", p["samples"][0]["input"])
        self.assertIn("0 8 2 0 1 6", p["samples"][0]["input"])
        self.assertEqual(p["subtasks"][0]["conditions"], "n, q ≤ 1 000")
        self.assertTrue(any("„6”" in w for w in p["warnings"]))

    def test_real_six_untouched(self):
        txt = ("Zadanie\n\nWejście\nW pierwszym wierszu liczba n (1 ≤ n ≤ 1000).\n\nWyjście\nJedna liczba.\n\n"
               "Ocenianie\nPodzadanie Ograniczenia Limit czasu Punkty\n1 n ≤ 100 6 s 30\n2 brak dodatkowych założeń 6 s 70\n")
        p = ST.parse(txt)
        self.assertEqual(p["constraints"]["n"]["max"], 1000)
        self.assertEqual(p["subtasks"][0]["conditions"], "n ≤ 100")
        self.assertEqual(p["subtasks"][0]["time_limit"], 6.0)
        self.assertFalse(any("„6”" in w for w in p["warnings"]))

    def test_double_chain_in_table_when_real_leq_exists(self):
        txt = ("Zadanie\n\nWejście\nW pierwszym wierszu liczba n (1 ≤ n ≤ 10^6).\n\nWyjście\nJedna liczba.\n\n"
               "Ocenianie\nPodzadanie Ograniczenia Limit czasu Liczba punktów\n1 1 6 n 6 20 2 s 10\n2 1 6 n 6 2000 2 s 90\n")
        p = ST.parse(txt)
        self.assertEqual([s["conditions"] for s in p["subtasks"]], ["1 ≤ n ≤ 20", "1 ≤ n ≤ 2000"])
        self.assertEqual(p["subtasks"][1]["constraints"]["n"]["max"], 2000)

    def test_control_char_leq_and_cdot(self):
        txt = ("Zadanie\n\nWejście\nW pierwszym wierszu liczba n (1 \x14 n \x14 1 000 000). W drugim n liczb c_i "
               "(1 \x14 c_i \x14 10^9).\n\nWyjście\nKoszt k \x01 c.\n")
        p = ST.parse(txt)
        self.assertEqual(p["constraints"]["n"]["max"], 10**6)
        self.assertEqual(p["constraints"]["c_i"]["max"], 10**9)
        self.assertIn("·", p["sections"]["wyjscie"])

if __name__ == "__main__":
    unittest.main()

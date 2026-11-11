"""Testy syntezy programów (oiforge.core.synth): plan wczytywania (Python = C++), wyliczanie kandydatów, ochrona
przed przeuczeniem (kilka programów pasuje → „weak”), brak TODO w czymkolwiek, co generujemy.
Treści zadań w tym pliku są napisane specjalnie do testów."""
import json
import os
import random
import unittest

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge import config
from oiforge.core import runner
from oiforge.core import statement as ST
from oiforge.core import synth as SY


def _pairs_stmt():
    samples = [("6 8\n1 7 3 5 4 4\n", None), ("5 10\n5 5 5 5 5\n", None), ("4 3\n1 2 1 2\n", None)]
    out = []
    for inp, _ in samples:
        t = list(map(int, inp.split()))
        n, k, a = t[0], t[1], t[2:]
        out.append((inp, str(sum(1 for i in range(n) for j in range(i + 1, n) if a[i] + a[j] == k))))
    body = "\n".join(f"Wejście:\n{i}Wyjście:\n{o}\n" for i, o in out)
    return ("Paczki cukierków\nBajtek ma n paczek, w i-tej jest a_i cukierków. Chce dać koledze dwie różne paczki tak, "
            "żeby razem było w nich dokładnie k cukierków. Na ile sposobów może wybrać parę paczek?\n"
            "Wejście\nW pierwszym wierszu dwie liczby całkowite n i k (1 ≤ n ≤ 1000, 1 ≤ k ≤ 10^9). W drugim wierszu n liczb "
            "całkowitych a_i (1 ≤ a_i ≤ 10^9).\nWyjście\nJedna liczba — liczba par.\nPrzykład\n" + body)


def _bfs_ref(inp):
    t = list(map(int, inp.split()))
    n, m = t[0], t[1]
    adj = [[] for _ in range(n + 1)]
    for i in range(m):
        a, b = t[2 + 2 * i], t[3 + 2 * i]
        adj[a].append(b)
        adj[b].append(a)
    d = [-1] * (n + 1)
    d[1] = 0
    q = [1]
    for v in q:
        for w in adj[v]:
            if d[w] == -1:
                d[w] = d[v] + 1
                q.append(w)
    return str(d[n])


GRAPH_INPUTS = ["5 5\n1 2\n2 3\n3 5\n1 4\n4 5\n", "4 2\n1 2\n3 4\n", "6 7\n1 2\n2 3\n3 4\n4 6\n1 5\n5 6\n2 5\n"]
GRAPH = ("Wycieczka rowerowa\nW Bajtocji jest n skrzyżowań połączonych m ścieżkami rowerowymi (dwukierunkowymi). Bajtek "
         "mieszka przy skrzyżowaniu 1, a jego babcia przy skrzyżowaniu n. Ile najmniej ścieżek musi przejechać, żeby "
         "dotrzeć do babci? Jeśli się nie da, wypisz -1.\nWejście\nW pierwszym wierszu liczby n i m (2 ≤ n ≤ 10^5, "
         "0 ≤ m ≤ 2·10^5). W każdym z kolejnych m wierszy dwie liczby a, b (1 ≤ a, b ≤ n) — ścieżka między a i b.\n"
         "Wyjście\nJedna liczba — najmniejsza liczba ścieżek albo -1.\nPrzykład\n"
         + "\n".join(f"Wejście:\n{i}Wyjście:\n{_bfs_ref(i)}\n" for i in GRAPH_INPUTS))


def _rec(n, M=10**9 + 7):
    a, b = 1, 1                      # f(0) = 1, f(1) = 1, f(i) = f(i−1) + 2·f(i−2)
    for _ in range(n):
        a, b = b, (b + 2 * a) % M
    return a


RECUR = ("Kolonia bakterii\nW chwili 0 i w chwili 1 w probówce jest jedna bakteria. Później w każdej chwili i liczba bakterii "
         "jest określona wzorem f(i) = f(i − 1) + 2 · f(i − 2). Podaj f(n). Wynik może być ogromny, więc wypisz resztę z "
         "dzielenia go przez 10^9 + 7.\nWejście\nJedna liczba całkowita n (0 ≤ n ≤ 10^18).\nWyjście\nJedna liczba — "
         "f(n) modulo 10^9 + 7.\nPrzykład\n"
         + "\n".join(f"Wejście:\n{n}\nWyjście:\n{_rec(n)}\n" for n in (5, 40, 1000)))

# treść mówi o parach i sumie, ale nie mówi, jak suma ma się do k; w obu przykładach „suma = k” i „suma ≥ k” dają
# ten sam wynik — przykłady nie rozstrzygają, więc to NIE może być sukces
AMBIG = ("Pary Bajtka\nBajtek liczy pary indeksów i < j, których suma a_i + a_j jest związana z liczbą k tajemniczą "
         "regułą. Ile par policzy?\nWejście\nW pierwszym wierszu liczby n i k (1 ≤ n ≤ 100, 1 ≤ k ≤ 1000). W drugim "
         "wierszu n liczb całkowitych a_i (1 ≤ a_i ≤ 1000).\nWyjście\nJedna liczba.\nPrzykład\nWejście:\n3 10\n5 5 1\n"
         "Wyjście:\n1\nWejście:\n4 7\n3 4 1 2\nWyjście:\n1\n")

# ciąg rosnący: posortowanie, maksimum prefiksu i „wypisz to samo” dają to samo, a treść nic nie mówi — zgadywanka
NO_EVIDENCE = ("Liczby Bajtka\nBajtek zapisał n liczb. Wypisz n liczb zgodnie z zasadą Bajtka.\nWejście\nW pierwszym "
               "wierszu liczba n (1 ≤ n ≤ 100). W drugim wierszu n liczb całkowitych a_i (1 ≤ a_i ≤ 1000).\nWyjście\n"
               "W jednym wierszu n liczb.\nPrzykład\nWejście:\n4\n2 5 7 9\nWyjście:\n2 5 7 9\n")

STRANGE = ("Zagadka\nWypisz liczbę, którą zna tylko autor.\nWejście\nW pierwszym wierszu liczba n (1 ≤ n ≤ 100). W drugim "
           "wierszu n liczb a_i.\nWyjście\nJedna liczba.\nPrzykład\nWejście:\n3\n5 1 4\nWyjście:\n987654\n")

QUERIES = ("Półka z książkami\nNa półce stoją książki. Wykonaj q operacji.\nWejście\nW pierwszym wierszu liczba q "
           "(1 ≤ q ≤ 10^5). W każdym z kolejnych q wierszy jest jedna operacja: DODAJ x, USUN albo ILE.\nWyjście\n"
           "Dla każdej operacji ILE wypisz liczbę książek.\nPrzykład\nWejście:\n4\nDODAJ 5\nILE\nUSUN\nILE\nWyjście:\n1\n0\n")


def _wrap_reader(code: str, body: str = "") -> str:
    return "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n" + code + "\n" + body + "\nreturn 0;\n}\n"


class TestPlan(unittest.TestCase):
    def test_plan_reads_samples_like_the_statement(self):
        p = ST.parse(_pairs_stmt())
        plan = SY.build_plan(p["input_format"], p["samples"])
        self.assertIsNotNone(plan)
        env = plan.read(p["samples"][0]["input"])
        self.assertEqual(env["n"], 6)
        self.assertEqual(env["k"], 8)
        self.assertEqual(env["a"], [1, 7, 3, 5, 4, 4])
        self.assertIsNone(plan.read("6 8\n1 2 3\n"))                  # za mało liczb — plan nie pasuje

    def test_write_is_inverse_of_read(self):
        p = ST.parse(GRAPH)
        plan = SY.build_plan(p["input_format"], p["samples"])
        for s in p["samples"]:
            env = plan.read(s["input"])
            self.assertEqual(plan.read(SY._plan_write(plan, env)), env)

    @unittest.skipUnless(config.CXX, "brak kompilatora C++")
    def test_cpp_reader_reads_the_same_values(self):
        p = ST.parse(GRAPH)
        plan = SY.build_plan(p["input_format"], p["samples"])
        body = 'long long s = 0; for (size_t i = 0; i < a.size(); i++) s += a[i] * 7 + b[i];\ncout << n << " " << m << " " << s << "\\n";'
        code = _wrap_reader(plan.cpp_reader(body=body))
        prog = runner.prepare(code)
        self.assertTrue(prog.ok, prog.compile.stderr if prog.compile else prog.error)
        for s in p["samples"]:
            env = plan.read(s["input"])
            exp = f"{env['n']} {env['m']} {sum(x * 7 + y for x, y in zip(env['a'], env['b']))}"
            self.assertEqual(runner.run(prog.cmd, s["input"]).stdout.split(), exp.split())


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestSynthesis(unittest.TestCase):
    def _no_todo(self, r):
        self.assertNotIn("TODO", json.dumps(r, ensure_ascii=False))

    def test_pairs_found_and_correct(self):
        text = _pairs_stmt()
        r = SY.synthesize(ST.parse(text), text)
        self.assertEqual(r["status"], "verified", r.get("message"))
        self.assertEqual(r["family"], "pairs")
        self._no_todo(r)
        prog = runner.prepare(r["code"])
        rnd = random.Random(5)
        for _ in range(15):                                          # porównanie z niezależnym wzorcem
            n, k = rnd.randint(1, 8), rnd.randint(2, 12)
            a = [rnd.randint(1, 10) for _ in range(n)]
            exp = sum(1 for i in range(n) for j in range(i + 1, n) if a[i] + a[j] == k)
            got = runner.run(prog.cmd, f"{n} {k}\n{' '.join(map(str, a))}\n").stdout.split()
            self.assertEqual(got, [str(exp)])

    def test_graph_bfs(self):
        r = SY.synthesize(ST.parse(GRAPH), GRAPH)
        self.assertEqual(r["status"], "verified", r.get("message"))
        self.assertEqual(r["family"], "graph")
        prog = runner.prepare(r["code"])
        rnd = random.Random(2)
        for _ in range(12):
            n = rnd.randint(2, 7)
            edges = [(rnd.randint(1, n), rnd.randint(1, n)) for _ in range(rnd.randint(0, 8))]
            inp = f"{n} {len(edges)}\n" + "".join(f"{a} {b}\n" for a, b in edges)
            self.assertEqual(runner.run(prog.cmd, inp).stdout.split(), [_bfs_ref(inp)])

    def test_recurrence_with_modulo_and_huge_n(self):
        r = SY.synthesize(ST.parse(RECUR), RECUR)
        self.assertEqual(r["status"], "verified", r.get("message"))
        self.assertEqual(r["family"], "recurrence")
        self.assertTrue(r["fast"])                                    # potęgowanie macierzy — mieści się w limitach
        prog = runner.prepare(r["code"])
        self.assertEqual(runner.run(prog.cmd, "77\n").stdout.split(), [str(_rec(77))])

    def test_ambiguous_samples_are_not_a_success(self):
        r = SY.synthesize(ST.parse(AMBIG), AMBIG)
        self.assertEqual(r["status"], "weak", r.get("message"))
        self.assertIn("ambiguous", r["reasons"])
        self.assertTrue(r["competitors"])
        c = r["competitors"][0]
        self.assertTrue(c.get("input"))                               # wejście, na którym programy się różnią
        self.assertNotEqual(c.get("output"), c.get("best_output"))
        self.assertIn("NIEPEWNE", r["message"])
        self._no_todo(r)

    def test_guess_without_any_evidence_is_not_shown(self):
        r = SY.synthesize(ST.parse(NO_EVIDENCE), NO_EVIDENCE)
        self.assertEqual(r["status"], "none", r.get("desc"))
        self.assertIsNone(r["code"])
        self.assertIn("zgadywanki", r["message"])

    def test_nothing_found_is_honest(self):
        p = ST.parse(STRANGE)
        r = SY.synthesize(p, STRANGE)
        self.assertEqual(r["status"], "none", r.get("desc"))
        self.assertIsNone(r["code"])
        reader = SY.reader_program(p)
        self.assertIn("cin >>", reader)
        self.assertNotIn("TODO", reader)
        self.assertTrue(runner.prepare(reader).ok)


LAMPS = """Lampki
Bajtek ma n lampek w rzędzie, każda jest zapalona albo zgaszona. Wykonuje m poleceń.
Wejście
W pierwszym wierszu wejścia znajdują się dwie liczby n i m (1 ≤ n, m ≤ 100 000), oznaczające liczbę lampek oraz liczbę poleceń. W drugim wierszu znajduje się napis s długości n złożony z liter o (zapalona) i x (zgaszona). W kolejnych m wierszach podane są polecenia. Polecenie zmiany przedziału poprzedzone jest literą S, a pytanie o stan lampki literą Q. Polecenie typu Q zawiera też liczbę x (1 ≤ x ≤ n), oznaczającą numer lampki. Polecenie typu S zawiera też dwie liczby a i b (1 ≤ a ≤ b ≤ n), oznaczające przedział, w którym każda lampka zmienia stan.
Wyjście
Dla każdego polecenia Q jeden wiersz: 1, jeśli lampka jest zapalona, albo 0.
Przykład
Wejście:
6 4
ooxxox
Q 3
S 2 4
Q 3
Q 2
Wyjście:
0
1
0
"""


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestReaderWithoutTodo(unittest.TestCase):
    def test_queries_reader_from_samples(self):
        p = ST.parse(QUERIES)
        rd = ST.cpp_reader(p["input_format"], samples=p["samples"])
        self.assertNotIn("TODO", rd["code"])
        self.assertIn('"DODAJ"', rd["code"])
        self.assertTrue(runner.prepare(_wrap_reader(rd["code"])).ok)

    def test_reader_program_reads_queries_with_statement_names(self):
        # zapytania po napisie: napis „ooxxox” to nie operacja; nazwy argumentów (x, a, b) z treści
        p = ST.parse(LAMPS)
        for code in (SY.reader_program(p), _wrap_reader(ST.cpp_reader(p["input_format"], samples=p["samples"])["code"])):
            self.assertIn('"Q"', code)
            self.assertIn('"S"', code)
            self.assertNotIn('"ooxxox"', code)
            self.assertNotIn("TODO", code)
            prog = runner.prepare(code)
            self.assertTrue(prog.ok, code)
            self.assertEqual(runner.run(prog.cmd, p["samples"][0]["input"], timeout=5).verdict, "OK")
        reader = SY.reader_program(p)
        self.assertIn("cin >> a >> b;", reader)
        self.assertIn("qi < m;", reader)
        self.assertRegex(reader, r"long long x;\s*cin >> x;")

    def test_unknown_format_reader(self):
        rd = ST.cpp_reader([], samples=[{"input": "3 ala\n1 2 3\n"}])
        self.assertNotIn("TODO", rd["code"])
        self.assertTrue(runner.prepare(_wrap_reader(rd["code"])).ok)
        rd = ST.cpp_reader([])
        self.assertNotIn("TODO", rd["code"])
        self.assertTrue(runner.prepare(_wrap_reader(rd["code"])).ok)

    def test_multitest_reader(self):
        fmt = [{"kind": "multitest", "vars": ["t"], "types": ["int"]},
               {"kind": "scalars", "vars": ["n"], "types": ["int"], "per_test": True}]
        rd = ST.cpp_reader(fmt)
        self.assertNotIn("TODO", rd["code"])
        self.assertTrue(runner.prepare(_wrap_reader(rd["code"])).ok)


if __name__ == "__main__":
    unittest.main()

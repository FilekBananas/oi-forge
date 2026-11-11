"""Testy auto-solvera (oiforge.tools.autosolver).

Kompilacje są cache'owane w user_data/cache/bin, więc ponowne uruchomienie jest szybkie."""
import concurrent.futures as cf
import json
import os
import unittest

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")      # rozgrzewka w tle tylko w serwerze, nie w testach

from oiforge import config
from oiforge.core import jobs
from oiforge.core import statement as ST
from oiforge.tools import autosolver as AS
from tests.test_statement import PDF_GRAPH, PLAIN_ARRAY

DEFAULT_PH = dict(AS.DEFAULT_PH)

INVERSIONS = """Inwersje
Policz liczbę inwersji w ciągu, czyli par indeksów i < j, dla których a_i > a_j.
Wejście
W pierwszym wierszu jedna liczba całkowita n (1 ≤ n ≤ 200 000). W drugim wierszu n liczb całkowitych a_i (1 ≤ a_i ≤ 10^9).
Wyjście
Jedna liczba — liczba inwersji.
Przykład
Wejście:
5
2 4 1 3 5
Wyjście:
3
Wejście:
3
3 2 1
Wyjście:
3
"""

NONSENSE = """Smoki i rycerze
Rycerz Bajtazar walczy ze smokami w kolejności podanej na wejściu; po każdej walce zyskuje doświadczenie zależne od
poziomu smoka i aktualnego humoru księżniczki opisanego tajemniczą formułą z dodatku do regulaminu.
Wejście
W pierwszym wierszu liczba n (1 ≤ n ≤ 1000). W drugim wierszu n liczb s_i (1 ≤ s_i ≤ 1000).
Wyjście
Jedna liczba.
Przykład
Wejście:
3
5 1 4
Wyjście:
4242
"""

# historyjka bez żadnego związku z matematyką — każdy szablon może pasować tylko „kształtem” (√50 = 7,07 → 7)
UNRELATED = """Wieża z klocków
Jaś ustawia klocki w rzędzie i bawi się nimi według tajemniczej zasady, którą zna tylko jego starsza siostra.
Wejście
W jedynym wierszu wejścia znajduje się jedna liczba całkowita n (1 ≤ n ≤ 10^12).
Wyjście
Wypisz jedną liczbę całkowitą.
Przykład
Dla danych wejściowych:
50
poprawnym wynikiem jest:
7
"""

UNRELATED_OCEN = UNRELATED + """Testy przykładowe: Test 0 to test z przykładu powyżej. Poza tym:
1ocen: n = 100; odpowiedź to 3.
"""

PAIRS = """Pary
Dany jest ciąg n liczb i liczba k. Ile jest par indeksów i < j takich, że a_i + a_j = k?
Wejście
W pierwszym wierszu liczby n i k (1 ≤ n ≤ 200 000, 1 ≤ k ≤ 10^9). W drugim wierszu n liczb a_i (1 ≤ a_i ≤ 10^9).
Wyjście
Liczba par.
Przykład
Wejście:
5 6
1 5 3 3 2
Wyjście:
2
"""


class TestTemplates(unittest.TestCase):
    def test_library_size_and_ids(self):
        tpls = AS.load_templates()
        bases = [t for t in tpls if not t.get("variant_of")]
        self.assertGreaterEqual(len(bases), 40)
        ids = [t["id"] for t in tpls]
        self.assertEqual(len(ids), len(set(ids)))
        for t in bases:
            self.assertTrue(t["example"], t["id"])
            self.assertTrue(t["shapes"], t["id"])

    @unittest.skipUnless(config.CXX, "brak kompilatora C++")
    def test_every_template_passes_its_example(self):
        tpls = [t for t in AS.load_templates() if t.get("example")]

        def check(t):
            ex = t["example"]
            s = [{"input": AS._fill(ex["input"], DEFAULT_PH), "output": AS._fill(ex["output"], DEFAULT_PH)}]
            r = AS.run_on_samples(AS.render(t, DEFAULT_PH), s, timeout=5)
            msgs = []
            if r["passed"] != 1:
                msgs.append(f"{t['id']}: {r.get('compile_error') or r['results']}")
            if t.get("brute"):
                rb = AS.run_on_samples(AS.render(t, DEFAULT_PH, brute=True), s, timeout=5)
                if rb["passed"] != 1:
                    msgs.append(f"{t['id']} (brute): {rb.get('compile_error') or rb['results']}")
            return msgs

        with cf.ThreadPoolExecutor(6) as ex:
            bad = [m for ms in ex.map(check, tpls) for m in ms]
        self.assertEqual(bad, [])

    @unittest.skipUnless(config.CXX, "brak kompilatora C++")
    def test_variants_compile(self):
        from oiforge.core import runner
        vs = [t for t in AS.load_templates() if t.get("variant_of")]
        self.assertGreaterEqual(len(vs), 20)
        with cf.ThreadPoolExecutor(6) as ex:
            res = list(ex.map(lambda t: (t["id"], runner.prepare(AS.render(t, DEFAULT_PH)).ok), vs))
            # własne brute wariantów (wariant nie dziedziczy brute bazowego szablonu)
            resb = list(ex.map(lambda t: (t["id"] + " (brute)", runner.prepare(AS.render(t, DEFAULT_PH, brute=True)).ok),
                               [t for t in vs if t.get("brute")]))
        self.assertEqual([i for i, ok in res + resb if not ok], [])
        base = {t["id"]: t for t in AS.load_templates() if not t.get("variant_of")}
        for t in vs:
            if t.get("brute"):
                self.assertNotEqual(t["brute"], base[t["variant_of"]].get("brute"), t["id"])


class TestFeatures(unittest.TestCase):
    def test_shape_tags(self):
        self.assertIn("G", AS.shape_tags(ST.parse(PDF_GRAPH)["input_format"]))
        self.assertIn("NA", AS.shape_tags(ST.parse(PLAIN_ARRAY)["input_format"]))
        self.assertIn("NKA", AS.shape_tags(ST.parse(PAIRS)["input_format"]))

    def test_placeholders(self):
        p = ST.parse("Zadanie\nWyjście\nWypisz TAK albo NIE. Jeśli rozwiązanie nie istnieje, wypisz BRAK. Wynik podaj "
                     "modulo 998244353 z dokładnością do trzech miejsc po przecinku.\nPrzykład\nWejście:\n1\nWyjście:\nTAK\n")
        ph = AS.placeholders(p)
        self.assertEqual((ph["YES"], ph["NO"]), ("TAK", "NIE"))
        self.assertEqual(ph["NONE"], "BRAK")
        self.assertEqual(ph["MOD"], "998244353")
        self.assertEqual(ph["PREC"], "3")

    def test_render_multitest_wrapper(self):
        t = next(x for x in AS.load_templates() if x["id"] == "sum-array")
        code = AS.render(t, DEFAULT_PH, multitest=True)
        self.assertIn("while (T_--) solve();", code)


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestSolve(unittest.TestCase):
    def test_solves_array_sum(self):
        r = AS.solve(PLAIN_ARRAY, use_user_code=False)
        self.assertEqual(r["status"], "solved", r.get("message"))
        self.assertEqual(r["best"]["id"], "sum-array")
        self.assertIn("1/1", r["message"])

    def test_solves_bfs_graph(self):
        r = AS.solve(PDF_GRAPH, use_user_code=False)
        self.assertEqual(r["status"], "solved")
        self.assertEqual(r["best"]["id"], "bfs-dist-all")
        self.assertIn("Odległości", r["best"]["code"])

    def test_solves_inversions_and_pairs(self):
        r = AS.solve(INVERSIONS, use_user_code=False)
        self.assertEqual(r["best"]["id"] if r["best"] else None, "inversions")
        r = AS.solve(PAIRS, use_user_code=False)
        self.assertEqual(r["status"], "solved")
        self.assertEqual(r["best"]["id"], "pairs-sum-k")

    def test_honest_failure_with_reader(self):
        r = AS.solve(NONSENSE, use_user_code=False)
        self.assertEqual(r["status"], "unsolved")
        self.assertIn("Nie znalazłem", r["message"])
        self.assertIsNone(r["best"])
        self.assertIn("cin >>", r["reader"])           # gotowe wczytywanie wygenerowane z formatu…
        self.assertIn("to NIE jest rozwiązanie", r["reader"])
        self.assertNotIn("TODO", json.dumps(r, ensure_ascii=False))     # …ale nigdy TODO ani udawany wynik
        self.assertNotIn("wynik = 0", r["reader"])

    def test_job_api(self):
        job, res = jobs.run_sync(lambda j: AS.solve(PLAIN_ARRAY, j, use_user_code=False), title="test")
        self.assertEqual(res["status"], "solved")
        self.assertTrue(any(e.get("type") == "candidate" for e in job.events))
        json.dumps(res)


class TestShapesAndPlaceholders(unittest.TestCase):
    def bench(self, name):
        from tests import bench_smart as B
        return ST.parse(B.statement(name))

    def test_new_shape_tags(self):
        self.assertIn("GST", AS.shape_tags(self.bench("przesiadki")["input_format"]))
        self.assertIn("NRS2", AS.shape_tags(self.bench("ranking")["input_format"]))
        self.assertIn("NQ", AS.shape_tags(self.bench("stos")["input_format"]))
        self.assertIn("GRID", AS.shape_tags(self.bench("mysz")["input_format"]))
        self.assertNotIn("SK", AS.shape_tags(self.bench("palindrom")["input_format"]))

    def test_grid_placeholders(self):
        ph = AS.placeholders(self.bench("mysz"))             # „Mysz (litera M) … sera (litera S)”, ściany X
        self.assertEqual((ph["START"], ph["END"], ph["WALL"]), ("M", "S", "X"))
        ph = AS.placeholders(self.bench("pozar"))            # źródła P, ogień idzie po D
        self.assertEqual((ph["SRC"], ph["FUEL"]), ("P", "D"))
        ph = AS.placeholders(self.bench("plamy"))
        self.assertEqual(ph["LAND"], "*")

    def test_none_word(self):
        self.assertEqual(AS.placeholders(self.bench("kolejka"))["NONE"], "PUSTO")
        self.assertEqual(AS.placeholders(self.bench("stos"))["NONE"], "PUSTY")
        self.assertEqual(AS.placeholders(self.bench("mysz"))["NONE"], "NIE")
        self.assertEqual(AS.placeholders(self.bench("labirynt"))["NONE"], "-1")


class TestWarmup(unittest.TestCase):
    def test_sources_cover_templates(self):
        srcs = AS.warmup_sources()
        bases = [t for t in AS.load_templates() if not t.get("variant_of")]
        self.assertGreaterEqual(len(srcs), len(bases))
        self.assertTrue(all("int main()" in x for x in srcs))

    def test_disabled_by_env(self):
        self.assertEqual(os.environ.get("OIFORGE_NO_WARMUP"), "1")
        self.assertFalse(AS.start_warmup())
        st = AS.warmup_status()
        self.assertIn("done", st)
        self.assertIn("total", st)


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestBenchSolve(unittest.TestCase):
    """Zestaw sprawdzający: auto-solver ma rozwiązywać klasyki i zgadzać się z wzorcówką w Pythonie."""
    NAMES = ["labirynt", "mysz", "wyspy", "plamy", "pozar", "sumy_zero", "deski", "rozklad_pracy", "przesiadki",
             "kolejka", "stos", "ranking", "pierwsze_przedzial", "kurier", "egzamin", "wystapienia"]

    def test_bench_subset_solved_and_correct(self):
        from tests import bench_smart as B
        bad = []
        for name in self.NAMES:
            t = B.TASKS[name]
            r = AS.solve(B.statement(name), use_user_code=False)
            if r["status"] != "solved":
                bad.append(f"{name}: {r['status']}")
                continue
            ok, tot, diff = B._agree(r["best"]["code"], t["ref"], t["gen"], tests=10)
            if ok != tot:
                bad.append(f"{name}: {r['best']['id']} {ok}/{tot} {diff}")
        self.assertEqual(bad, [])

    def test_maze_corners_minus_one(self):
        from tests import bench_smart as B
        from oiforge.core import runner
        r = AS.solve(B.statement("labirynt"), use_user_code=False)
        self.assertEqual(r["best"]["id"], "grid-bfs-corners")
        prog = runner.prepare(r["best"]["code"])
        self.assertEqual(runner.run(prog.cmd, "3 3\n.#.\n##.\n...\n").stdout.strip(), "-1")   # start zamurowany
        self.assertEqual(runner.run(prog.cmd, "1 1\n.\n").stdout.strip(), "0")                 # start = meta

    def test_progress_messages(self):
        from tests import bench_smart as B
        job, res = jobs.run_sync(lambda j: AS.solve(B.statement("deski"), j, use_user_code=False), title="test")
        self.assertEqual(res["status"], "solved")
        kinds = {e.get("type") for e in job.events}
        self.assertIn("compiled", kinds)
        self.assertIn("candidate", kinds)


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestWeakEvidence(unittest.TestCase):
    def test_shape_only_match_is_weak(self):
        r = AS.solve(UNRELATED, use_user_code=False)
        self.assertEqual(r["status"], "weak", r.get("message"))
        self.assertFalse(r["ok"])
        self.assertIn("shape", r["weak_reasons"])
        self.assertIn("sprawdź testerką", r["message"])
        self.assertTrue(r["best"]["weak"])
        self.assertLessEqual(r["best"]["confidence"], 0.3)
        self.assertIn("SŁABY DOWÓD", r["best"]["code"])
        json.dumps(r)

    def test_ocen_sample_kills_coincidence(self):
        r = AS.solve(UNRELATED_OCEN, use_user_code=False)
        self.assertEqual(len(r["samples"]), 2)
        self.assertNotIn(r["status"], ("solved", "weak"))

    def test_weak_rules(self):
        ok = lambda e: {"ok": True, "expected": e}           # noqa: E731
        self.assertEqual(AS.weak_evidence({"sig_hit": False, "results": [ok("7"), ok("123456")]}), ["shape"])
        self.assertEqual(AS.weak_evidence({"sig_hit": True, "sig_strong": False, "results": [ok("7\n")]}), ["trivial"])
        self.assertEqual(AS.weak_evidence({"sig_hit": True, "sig_strong": True, "results": [ok("7\n")]}), [])
        self.assertEqual(AS.weak_evidence({"sig_hit": True, "sig_strong": False, "results": [ok("7"), ok("8")]}), [])
        self.assertEqual(AS.weak_evidence({"sig_hit": True, "sig_strong": False, "results": [ok("1 2 3 4")]}), [])


if __name__ == "__main__":
    unittest.main()

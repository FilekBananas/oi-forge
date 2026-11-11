"""Testy dziennika błędów i postępów (oiforge.tools.journal).

Uruchom: python3 -m unittest tests.test_journal -v

Sprawdza odporność log_event (złe dane, równoległość, brak dostępu do pliku), statystyki błędów,
postępy na syntetycznym index.json i — jeśli są — na prawdziwych danych ucznia (skipUnless).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="oiforge-journal-")
os.environ.setdefault("OIFORGE_USER_DIR", _TMP)
os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge import config  # noqa: E402
from oiforge.server_api import ApiError  # noqa: E402
from oiforge.tools import journal as J  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_INDEX = ROOT / "user_data" / "programs" / "index.json"
HAS_REAL = REAL_INDEX.is_file()
_saved = {}


def setUpModule():  # noqa: N802
    _saved["store"] = config.STORE_DIR
    _saved["programs"] = config.PROGRAMS_DIR
    (Path(_TMP) / "store").mkdir(parents=True, exist_ok=True)
    config.STORE_DIR = Path(_TMP) / "store"
    config.PROGRAMS_DIR = Path(_TMP) / "programs"


def tearDownModule():  # noqa: N802
    config.STORE_DIR = _saved["store"]
    config.PROGRAMS_DIR = _saved["programs"]
    J.set_path(None)
    shutil.rmtree(_TMP, ignore_errors=True)


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_TMP))
        self.path = self.dir / "journal.jsonl"
        J.set_path(self.path)

    def lines(self):
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]


# =============================================================================================== log_event
class TestLogEvent(JournalCase):
    def test_basic_record(self):
        J.log_event("run", {"verdict": "OK"})
        J.log("kb_view", id="stl-set")
        recs = self.lines()
        self.assertEqual([r["kind"] for r in recs], ["run", "kb_view"])
        self.assertEqual(recs[0]["data"], {"verdict": "OK"})
        self.assertRegex(recs[0]["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertAlmostEqual(recs[0]["t"], time.time(), delta=60)

    def test_never_raises_on_bad_input(self):
        class Weird:
            def __str__(self):
                raise RuntimeError("nie da się wypisać")

        bad_inputs = [
            (None, None), ("", {}), (123, {"x": 1}), ("Run Event!", "napis zamiast dict"),
            ("run", {"verdict": float("nan"), "big": 10 ** 40, "set": {1, 2}, "obj": object()}),
            ("assistant_q", {"q": "ą" * 100000}), ("run", {"deep": {"a": {"b": {"c": {"d": {"e": 1}}}}}}),
            ("run", {"w": Weird()}), ("run", {i: i for i in range(1000)}), ("run", [1, 2, 3]),
        ]
        for kind, data in bad_inputs:
            J.log_event(kind, data)          # nie może rzucić
        recs = self.lines()
        self.assertEqual(len(recs), len(bad_inputs))
        self.assertEqual(recs[0]["kind"], "other")
        self.assertEqual(recs[3]["kind"], "run_event")
        self.assertLessEqual(len(recs[5]["data"]["q"]), J.MAX_STR)
        self.assertIsNone(recs[4]["data"]["verdict"])
        self.assertLessEqual(len(recs[8]["data"]), J.MAX_ITEMS)

    def test_unwritable_path_is_silent(self):
        J.set_path(self.dir)                 # katalog zamiast pliku — open() się nie uda
        J.log_event("run", {"verdict": "OK"})
        self.assertEqual(J.events(), [])
        J.set_path(self.dir / "nowy" / "pod" / "journal.jsonl")   # brakujące katalogi tworzone w locie
        J.log_event("run", {"verdict": "OK"})
        self.assertEqual(len(J.events()), 1)

    def test_parallel_writes(self):
        n_threads, per = 8, 250

        def worker(k):
            for i in range(per):
                J.log_event("tester_result", {"ok": i % 2 == 0, "tests": i, "verdict": "OK", "thread": k})

        ths = [threading.Thread(target=worker, args=(k,)) for k in range(n_threads)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        recs = self.lines()                   # każda linia to poprawny JSON
        self.assertEqual(len(recs), n_threads * per)
        self.assertEqual(len(J.events("tester_result")), n_threads * per)

    def test_fast(self):
        t0 = time.perf_counter()
        for i in range(500):
            J.log_event("run", {"verdict": "OK", "i": i})
        per_call = (time.perf_counter() - t0) / 500
        self.assertLess(per_call, 0.02, f"log_event za wolny: {per_call * 1000:.2f} ms")

    def test_incremental_read_and_partial_line(self):
        J.log_event("run", {"verdict": "OK"})
        self.assertEqual(len(J.events()), 1)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"t": 1, "kind": "run", "da')        # niedokończona linia (zapis w toku)
        self.assertEqual(len(J.events()), 1)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('ta": {}}\n' + "to nie jest json\n")
        J.log_event("kb_view", {"id": "stl-map"})
        evs = J.events()
        self.assertEqual([e["kind"] for e in evs], ["run", "run", "kb_view"])
        self.assertEqual(J.kb_read_ids(), {"stl-map": 1})

    def test_rotation(self):
        old = J.MAX_BYTES
        J.MAX_BYTES = 2000
        try:
            for i in range(100):
                J.log_event("run", {"verdict": "OK", "i": i, "pad": "x" * 50})
        finally:
            J.MAX_BYTES = old
        self.assertTrue((self.dir / J.OLD_NAME).exists())
        evs = J.events()
        self.assertGreater(len(evs), 10)
        self.assertEqual(evs[-1]["data"]["i"], 99)


# =============================================================================================== statystyki
class TestStats(JournalCase):
    def test_ranking_trend_and_counts(self):
        for _ in range(5):
            J.log_event("checker_issue", {"rule": "int-overflow-mul", "severity": "warning", "title": "Mnożenie intów", "kb": "num-overflow"})
        for _ in range(2):
            J.log_event("checker_issue", {"rule": "endl-in-loop", "severity": "perf"})
        for _ in range(3):
            J.log_event("compile_error", {"title": "Nie zadeklarowano zmiennej"})
        for v in ("OK", "OK", "RE", "TLE"):
            J.log_event("run", {"verdict": v})
        J.log_event("tester_result", {"ok": True, "tests": 1000, "verdict": "OK"})
        J.log_event("tester_result", {"ok": False, "tests": 37, "verdict": "WA"})
        J.log_event("kb_view", {"id": "stl-set"})
        J.log_event("kb_view", {"id": "stl-set"})
        J.log_event("assistant_q", {"q": "jak działa lower_bound"})
        s = J.stats(30)
        top = s["top_errors"]
        self.assertEqual(top[0]["title"], "Mnożenie intów")
        self.assertEqual(top[0]["count"], 5)
        self.assertEqual(top[0]["rank"], 1)
        self.assertEqual(top[0]["kb"], "num-overflow")
        self.assertEqual(top[0]["trend"], "up")
        self.assertEqual(top[1]["source"], "compile")
        sources = {e["source"] for e in top}
        self.assertTrue({"checker", "compile", "runtime", "tester"} <= sources)
        self.assertEqual(s["runs"]["total"], 4)
        self.assertEqual(s["runs"]["ok"], 2)
        self.assertEqual(s["tester"]["tests"], 1037)
        self.assertEqual(s["tester"]["failed"], 1)
        self.assertEqual(s["counts"]["compile_error"], 3)
        self.assertEqual(s["kb_top"][0], {"id": "stl-set", "title": s["kb_top"][0]["title"], "count": 2})
        self.assertNotEqual(s["kb_top"][0]["title"], "stl-set")      # tytuł z bazy wiedzy
        self.assertEqual(s["questions"][0]["q"], "jak działa lower_bound")
        self.assertEqual(len(s["trend"]), 31)
        today = s["trend"][-1]
        self.assertEqual(today["runs"], 4)
        self.assertEqual(today["errors"], 7 + 3 + 2 + 1)            # checker + compile + RE/TLE + tester WA

    def test_trend_down_and_window(self):
        now = time.time()
        old = [{"t": now - 10 * 86400, "ts": "x", "kind": "checker_issue", "data": {"rule": "r1"}}] * 4
        recent = [{"t": now - 3600, "ts": "x", "kind": "checker_issue", "data": {"rule": "r1"}}]
        ancient = [{"t": now - 100 * 86400, "ts": "x", "kind": "run", "data": {"verdict": "OK"}}]
        with open(self.path, "w", encoding="utf-8") as f:
            for r in ancient + old + recent:
                f.write(json.dumps(r) + "\n")
        s = J.stats(30, now=now)
        self.assertEqual(s["top_errors"][0]["count"], 5)
        self.assertEqual(s["top_errors"][0]["trend"], "down")
        self.assertEqual(s["runs"]["total"], 0)          # poza oknem 30 dni
        self.assertEqual(s["counts_all"]["run"], 1)

    def test_empty(self):
        s = J.stats(7)
        self.assertEqual(s["total_events"], 0)
        self.assertEqual(s["top_errors"], [])
        self.assertEqual(len(s["trend"]), 8)

    def test_http_handlers(self):
        with self.assertRaises(ApiError):
            J.handle_log({})
        self.assertTrue(J.handle_log({"kind": "kb_view", "data": {"id": "stl-map"}})["ok"])
        self.assertEqual(J.handle_events({"kind": "kb_view"})["events"][0]["data"]["id"], "stl-map")
        self.assertIn("top_errors", J.handle_stats({"days": "abc"}))


# =============================================================================================== postępy
def _write_index(path: Path, rows):
    recs = []
    for i, (short, tm, rnd) in enumerate(rows):
        recs.append({"source": "sio2.test", "contest_id": "k1", "contest_name": "Kółko", "problem_name": short.upper(),
                     "problem_short": short, "submission_id": str(1000 + i), "time": tm, "score": 100, "round": rnd,
                     "path": f"sio2.test/k1/{short}__{1000 + i}.cpp"})
    path.write_text(json.dumps({"fetched_at": None, "programs": recs}), encoding="utf-8")


class TestProgress(JournalCase):
    def test_synthetic_index(self):
        today = date(2026, 11, 9)
        idx = self.dir / "index.json"
        d = lambda k: (today - timedelta(days=k)).isoformat() + " 18:00:00"  # noqa: E731
        _write_index(idx, [("abc", d(0), "Sumy prefiksowe"), ("abc", d(0), "Sumy prefiksowe"),   # dwa zgłoszenia tego samego
                           ("bin", d(1), "Wyszukiwanie binarne"), ("dij", d(2), "Dzień 5 - Dijkstra"),
                           ("old", d(30), "Warsztaty 1")])
        probs = J.solved_problems(idx)
        self.assertEqual(len(probs), 4)
        abc = next(p for p in probs if p["problem_short"] == "abc")
        self.assertEqual(abc["submissions"], 2)
        self.assertEqual(abc["id"], "sio2.test:1000")
        p = J.progress(idx, today=today)
        self.assertEqual(p["totals"]["submissions"], 5)
        self.assertEqual(p["totals"]["problems"], 4)
        self.assertEqual(sum(p["heatmap"].values()), 5)
        self.assertEqual(p["streak"]["current"], 3)
        self.assertTrue(p["streak"]["active_today"])
        self.assertEqual(p["cumulative"][-1]["total"], 4)
        self.assertIn("Warsztaty 1", p["unmatched_rounds"])
        tids = {t["id"]: t for t in p["topics"]}
        self.assertEqual(tids["prefix-sums"]["solved"], 1)
        self.assertEqual(tids["dijkstra"]["solved"], 1)
        self.assertTrue(p["strongest"])
        self.assertTrue(p["weakest"])
        self.assertEqual(p["recent"][0]["problem_short"], "abc")

    def test_streaks(self):
        t = date(2026, 11, 9)
        days = {t - timedelta(days=k) for k in (1, 2, 3, 10, 11)}
        s = J._streaks(days, t)
        self.assertEqual((s["current"], s["longest"], s["active_today"]), (3, 3, False))
        s = J._streaks(days, t + timedelta(days=2))
        self.assertEqual(s["current"], 0)
        self.assertEqual(J._streaks(set(), t)["current"], 0)

    def test_missing_or_broken_index(self):
        self.assertEqual(J.solved_problems(self.dir / "nie-ma.json"), [])
        bad = self.dir / "bad.json"
        bad.write_text("{to nie json", encoding="utf-8")
        p = J.progress(bad)
        self.assertFalse(p["has_data"])
        self.assertEqual(p["totals"]["problems"], 0)

    @unittest.skipUnless(HAS_REAL, "brak prawdziwych danych ucznia")
    def test_real_student_data(self):
        recs = json.loads(REAL_INDEX.read_text(encoding="utf-8"))["programs"]
        p = J.progress(REAL_INDEX)
        self.assertTrue(p["has_data"])
        self.assertEqual(p["totals"]["submissions"], sum(1 for r in recs if (r.get("score") or 100) >= 100))
        self.assertGreaterEqual(p["totals"]["problems"], 200)
        self.assertLessEqual(p["totals"]["problems"], p["totals"]["submissions"])
        self.assertEqual(sum(p["heatmap"].values()), p["totals"]["submissions"])
        self.assertGreaterEqual(p["streak"]["longest"], 2)
        self.assertGreaterEqual(p["totals"]["active_days"], 20)
        self.assertGreater(len(p["strongest"]), 3)
        self.assertTrue(p["weakest"])
        matched = sum(t["solved"] for t in p["topics"])
        self.assertGreater(matched, 150)
        names = {t["id"] for t in p["strongest"]}
        self.assertTrue(names & {"graphs-basic", "stl", "dp-basic", "greedy", "contest-practice", "math-basic"})
        self.assertEqual(p["cumulative"][-1]["total"], p["totals"]["problems"])
        self.assertTrue(all(":" in r["id"] for r in p["recent"]))           # „<źródło>:<id zgłoszenia>”


if __name__ == "__main__":
    unittest.main()

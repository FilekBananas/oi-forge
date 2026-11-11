"""Testy modułu Trening (oiforge.tools.training): SM-2, fiszki, tryb zawodów, ścieżka nauki, zadanie dnia.

Uruchom: python3 -m unittest tests.test_training -v

Stan (store, dziennik) trafia do katalogu tymczasowego. Testy na prawdziwych danych ucznia
(user_data/programs/index.json) są pomijane, gdy pliku brak.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="oiforge-training-")
os.environ.setdefault("OIFORGE_USER_DIR", _TMP)
os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge import config  # noqa: E402
from oiforge.core import kb, store  # noqa: E402
from oiforge.server_api import ApiError  # noqa: E402
from oiforge.tools import journal  # noqa: E402
from oiforge.tools import training as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_INDEX = ROOT / "user_data" / "programs" / "index.json"
HAS_REAL = REAL_INDEX.is_file()
_saved = {}


def setUpModule():  # noqa: N802
    # Gdyby config był już zaimportowany wcześniej (inne testy), przekieruj zapisy do katalogu tymczasowego.
    _saved["store"] = config.STORE_DIR
    _saved["programs"] = config.PROGRAMS_DIR
    store_dir = Path(_TMP) / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    config.STORE_DIR = store_dir
    config.PROGRAMS_DIR = Path(_TMP) / "programs"
    journal.set_path(Path(_TMP) / "journal-test.jsonl")


def tearDownModule():  # noqa: N802
    config.STORE_DIR = _saved["store"]
    config.PROGRAMS_DIR = _saved["programs"]
    journal.set_path(None)
    T._clock = __import__("time").time
    shutil.rmtree(_TMP, ignore_errors=True)


class FakeClock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


T0 = datetime(2026, 11, 9, 9, 0, 0).timestamp()


# =============================================================================================== SM-2
class TestSM2(unittest.TestCase):
    def test_ease_formula(self):
        # EF' = EF + (0.1 − (5−q)(0.08 + (5−q)·0.02)), q = 1, 3, 4, 5 dla ocen 1..4
        expected = {1: 1.96, 2: 2.36, 3: 2.5, 4: 2.6}
        for g, ef in expected.items():
            self.assertAlmostEqual(T.sm2(None, g, T0)["ef"], ef, places=4, msg=f"ocena {g}")

    def test_ease_has_floor(self):
        st = None
        for _ in range(10):
            st = T.sm2(st, 1, T0)
        self.assertAlmostEqual(st["ef"], T.MIN_EF)

    def test_good_sequence_is_classic_sm2(self):
        st, at, seq = None, T0, []
        for _ in range(4):
            st = T.sm2(st, 3, at)
            seq.append(st["interval"])
            at = st["due"] + 60
        self.assertEqual(seq[:3], [1, 6, 15])          # 1, 6, 6·2.5
        self.assertEqual(seq[3], round(15 * 2.5))

    def test_fail_resets_and_relearns_in_10_minutes(self):
        st = T.sm2(None, 3, T0)
        st = T.sm2(st, 3, st["due"] + 10)
        self.assertEqual(st["reps"], 2)
        at = st["due"] + 10
        failed = T.sm2(st, 1, at)
        self.assertEqual(failed["reps"], 0)
        self.assertEqual(failed["interval"], 0)
        self.assertAlmostEqual(failed["due"], at + T.RELEARN_S, places=2)
        self.assertEqual(failed["lapses"], 1)
        self.assertLess(failed["ef"], st["ef"])

    def test_better_grade_never_shorter(self):
        for prev in (None, T.sm2(None, 3, T0), T.sm2(T.sm2(None, 3, T0), 3, T0 + 2 * 86400)):
            ivs = [T.sm2(prev, g, T0 + 10 * 86400)["interval"] for g in (1, 2, 3, 4)]
            self.assertEqual(ivs, sorted(ivs), ivs)
            self.assertLess(ivs[0], ivs[1])

    def test_due_is_local_midnight(self):
        st = T.sm2(None, 3, T0)
        d = datetime.fromtimestamp(st["due"])
        self.assertEqual((d.hour, d.minute, d.second), (0, 0, 0))
        self.assertEqual(d.date(), date(2026, 11, 10))

    def test_invalid_grade(self):
        with self.assertRaises(ValueError):
            T.sm2(None, 0, T0)
        with self.assertRaises(ValueError):
            T.sm2(None, 5, T0)

    def test_labels_and_preview(self):
        self.assertEqual(T.interval_label(T.sm2(None, 1, T0), T0), "10 min")
        self.assertEqual(T.interval_label(T.sm2(None, 3, T0), T0), "jutro")
        st = T.sm2(None, 3, T0)
        st2 = T.sm2(st, 3, st["due"] + 5)
        self.assertEqual(T.interval_label(st2, st["due"] + 5), "za 6 dni")
        pv = T.preview(None, T0)
        self.assertEqual(set(pv), {"1", "2", "3", "4"})
        self.assertEqual(pv["4"]["label"], "Łatwe")
        self.assertNotEqual(pv["1"]["next"], pv["4"]["next"])


# =============================================================================================== fiszki
class TestFlashcardData(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "data" / "flashcards.json").read_text(encoding="utf-8"))

    def test_at_least_150_hand_cards(self):
        self.assertGreaterEqual(len(self.data["cards"]), 150)

    def test_ids_decks_and_content(self):
        decks = {d["id"] for d in self.data["decks"]}
        ids = [c["id"] for c in self.data["cards"]]
        self.assertEqual(len(ids), len(set(ids)), "duplikaty id")
        per = {}
        for c in self.data["cards"]:
            self.assertIn(c["deck"], decks, c["id"])
            self.assertTrue(c["q"].strip() and c["a"].strip(), c["id"])
            self.assertEqual(c["a"].count("```") % 2, 0, f"niezamknięty blok kodu w {c['id']}")
            self.assertEqual(c["q"].count("```") % 2, 0, f"niezamknięty blok kodu w {c['id']}")
            per[c["deck"]] = per.get(c["deck"], 0) + 1
        for d in decks:
            self.assertGreaterEqual(per.get(d, 0), 10, f"za mało kart w talii {d}")

    def test_kb_links_exist(self):
        ids = {a["id"] for a in kb.articles()}
        refs = [c["kb"] for c in self.data["cards"] if c.get("kb")]
        ok = sum(1 for r in refs if r in ids)
        self.assertGreaterEqual(ok / max(1, len(refs)), 0.9, "ponad 10% kart wskazuje nieistniejące artykuły")

    def test_loader_has_no_errors(self):
        self.assertEqual(T._load_hand()["errors"], [])

    def test_auto_cards_from_kb(self):
        auto = T._auto_cards()
        self.assertGreater(len(auto["cards"]), 500)
        kinds = {c["kind"] for c in auto["cards"]}
        self.assertTrue({"complexity", "function", "qa"} <= kinds)
        ids = [c["id"] for c in T.all_cards()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(c["deck"].startswith("kb-") and c["kb"] for c in auto["cards"]))


class TestFlashcardFlow(unittest.TestCase):
    def setUp(self):
        store.delete("flashcards_state")
        self.clock = FakeClock(T0)
        T._clock = self.clock

    def tearDown(self):
        T._clock = __import__("time").time

    def test_new_card_limit_and_review_persistence(self):
        q = T.due_cards(limit=100)
        self.assertEqual(len(q["cards"]), T.DEFAULT_NEW_PER_DAY)
        self.assertTrue(all(c["is_new"] for c in q["cards"]))
        self.assertTrue(all(not c["deck"].startswith("kb-") for c in q["cards"]), "nowe karty z bazy tylko na życzenie")
        cid = q["cards"][0]["id"]
        r = T.review(cid, 3)
        self.assertEqual(r["next"], "jutro")
        self.assertEqual(store.get("flashcards_state")["cards"][cid]["reps"], 1)
        q2 = T.due_cards(limit=100)
        self.assertNotIn(cid, [c["id"] for c in q2["cards"]])
        self.assertEqual(q2["counts"]["new_left_today"], T.DEFAULT_NEW_PER_DAY - 1)
        self.clock.advance(86400)        # następnego dnia karta jest do powtórki
        q3 = T.due_cards(limit=100)
        self.assertEqual(q3["cards"][0]["id"], cid)
        self.assertFalse(q3["cards"][0]["is_new"])

    def test_failed_card_returns_after_relearn(self):
        cid = T.due_cards(limit=1)["cards"][0]["id"]
        T.review(cid, 1)
        self.assertNotIn(cid, [c["id"] for c in T.due_cards(limit=100)["cards"]])
        self.clock.advance(T.RELEARN_S + 1)
        self.assertIn(cid, [c["id"] for c in T.due_cards(limit=100)["cards"]])

    def test_deck_and_topic_filters(self):
        q = T.due_cards(deck="kb-grafy", limit=5)
        self.assertTrue(q["cards"])
        self.assertTrue(all(c["deck"] == "kb-grafy" for c in q["cards"]))
        t = T.due_cards(topic="dijkstra", limit=50, extra_new=50)
        kbs = T._topic_kb("dijkstra")
        self.assertTrue(t["cards"])
        self.assertTrue(all(c["kb"] in kbs for c in t["cards"]))
        with self.assertRaises(ApiError):
            T.due_cards(deck="nie-ma-takiej")

    def test_review_validation(self):
        with self.assertRaises(ApiError):
            T.review("nie-ma-takiej-karty", 3)
        with self.assertRaises(ApiError):
            T.review("stl-001", 7)

    def test_decks_summary_and_streak(self):
        for i in range(3):
            cid = next(c["id"] for c in T.due_cards(limit=100)["cards"] if c["is_new"])
            T.review(cid, 3)
            self.clock.advance(86400)
        s = T.decks_summary()
        self.assertEqual(s["stats"]["streak"]["current"], 3)
        self.assertEqual(s["stats"]["reviews_total"], 3)
        self.assertGreaterEqual(s["totals"]["hand_cards"], 150)
        self.assertEqual(s["totals"]["learned"], 3)
        self.assertTrue(any(d["auto"] for d in s["decks"]))


# =============================================================================================== zawody
class TestContest(unittest.TestCase):
    def setUp(self):
        store.delete("contest_current")
        store.delete("contest_history")
        self.clock = FakeClock(T0)
        T._clock = self.clock

    def tearDown(self):
        T._clock = __import__("time").time

    def test_start_defaults_and_validation(self):
        st = T.contest_start({})
        s = st["session"]
        self.assertEqual(s["duration_min"], 300)
        self.assertEqual(len(s["tasks"]), 3)
        self.assertEqual([t["short"] for t in s["tasks"]], ["A", "B", "C"])
        with self.assertRaises(ApiError) as cm:
            T.contest_start({})
        self.assertEqual(cm.exception.status, 409)
        T.contest_stop({"discard": True})
        with self.assertRaises(ApiError):
            T.contest_start({"duration_min": 2})
        with self.assertRaises(ApiError):
            T.contest_start({"tasks": [{"name": f"Z{i}"} for i in range(13)]})

    def test_state_survives_restart_and_time_flows(self):
        T.contest_start({"duration_min": 120, "tasks": [{"name": "Kurier", "short": "kur"}, {"name": "Lampy"}]})
        self.clock.advance(600)
        # „restart”: nowy odczyt wyłącznie ze store
        raw = json.loads((config.STORE_DIR / "contest_current.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["tasks"][0]["short"], "kur")
        st = T.contest_state()
        self.assertTrue(st["active"])
        self.assertAlmostEqual(st["session"]["elapsed"], 600, delta=0.5)
        self.assertAlmostEqual(st["session"]["remaining"], 120 * 60 - 600, delta=0.5)

    def test_pause_excludes_time(self):
        T.contest_start({"duration_min": 60})
        self.clock.advance(100)
        st = T.contest_update({"action": "pause"})
        self.assertTrue(st["session"]["paused"])
        self.assertEqual(st["session"]["mode"], "training")
        self.clock.advance(500)
        self.assertAlmostEqual(T.contest_state()["session"]["elapsed"], 100, delta=0.5)
        T.contest_update({"action": "resume"})
        self.clock.advance(30)
        s = T.contest_state()["session"]
        self.assertFalse(s["paused"])
        self.assertAlmostEqual(s["elapsed"], 130, delta=0.5)
        self.assertAlmostEqual(s["paused_total"], 500, delta=0.5)

    def test_status_focus_notes_checklist_and_summary(self):
        T.contest_start({"duration_min": 300, "tasks": [{"name": "A"}, {"name": "B"}, {"name": "C"}]})
        T.contest_update({"task_id": "t1", "status": "reading"})
        self.clock.advance(1200)
        T.contest_update({"task_id": "t2", "status": "brute"})       # fokus przechodzi na B
        self.clock.advance(600)
        T.contest_update({"task_id": "t2", "status": "full"})        # B rozwiązane → brak fokusu
        self.clock.advance(300)
        st = T.contest_update({"task_id": "t1", "notes": "**pomysł:** binsearch po wyniku",
                               "checklist": {"ll": True, "sample": True, "nieznane": True}, "points": "40"})
        s = st["session"]
        self.assertIsNone(s["focus"])
        self.assertAlmostEqual(s["task_times"]["t1"], 1200, delta=1)
        self.assertAlmostEqual(s["task_times"]["t2"], 600, delta=1)
        t1 = s["tasks"][0]
        self.assertEqual(t1["checklist"], {"ll": True, "sample": True})
        self.assertEqual(t1["points"], 40)
        self.assertIn("binsearch", t1["notes"])
        self.assertEqual(s["tasks"][1]["solved_at"], 1800)
        with self.assertRaises(ApiError):
            T.contest_update({"task_id": "t1", "status": "zrobione"})
        with self.assertRaises(ApiError):
            T.contest_update({"task_id": "t9", "status": "full"})
        out = T.contest_stop({})
        sm = out["summary"]
        self.assertFalse(out["active"])
        self.assertEqual(sm["solved_full"], 1)
        self.assertEqual(sm["points_total"], 140)
        self.assertAlmostEqual(sm["elapsed"], 2100, delta=1)
        self.assertTrue(sm["advice"])
        self.assertTrue(any(a.startswith("Nie przeczytano: C") for a in sm["advice"]), sm["advice"])
        hist = T.handle_contest_history({})["history"]
        self.assertEqual(hist[0]["id"], sm["id"])
        self.assertIsNone(store.get("contest_current"))
        self.assertTrue(any(e["kind"] == "status" for e in hist[0]["events"]))

    def test_reminders_schedule_and_ack(self):
        st = T.contest_start({"duration_min": 300})
        rem = {r["id"]: r["at"] for r in st["session"]["reminders"]}
        self.assertEqual(rem["hour1"], 3600)
        self.assertEqual(rem["hour4"], 4 * 3600)
        self.assertEqual(rem["final30"], 300 * 60 - 1800)
        self.assertEqual(rem["end"], 300 * 60)
        self.clock.advance(3601)
        s = T.contest_state()["session"]
        due = [r for r in s["reminders"] if r["due"] and not r["fired"]]
        self.assertEqual([r["id"] for r in due], ["hour1"])
        s = T.contest_update({"action": "ack", "reminder": "hour1"})["session"]
        self.assertIn("hour1", s["fired"])
        self.assertTrue(any(e["kind"] == "reminder" for e in s["events"]))
        # krótka sesja: bez przypomnień godzinnych kolidujących z końcowymi
        T.contest_stop({"discard": True})
        st = T.contest_start({"duration_min": 60})
        ids = [r["id"] for r in st["session"]["reminders"]]
        self.assertIn("final30", ids)
        self.assertNotIn("hour1", ids)

    def test_add_remove_task_and_history_delete(self):
        T.contest_start({"duration_min": 30, "tasks": [{"name": "X"}]})
        s = T.contest_update({"action": "add_task", "name": "Nowe", "short": "N"})["session"]
        self.assertEqual(len(s["tasks"]), 2)
        s = T.contest_update({"action": "remove_task", "task_id": s["tasks"][1]["id"]})["session"]
        self.assertEqual(len(s["tasks"]), 1)
        with self.assertRaises(ApiError):
            T.contest_update({"action": "remove_task", "task_id": "t1"})
        out = T.contest_stop({})
        r = T.handle_contest_history_delete({"id": out["summary"]["id"]})
        self.assertEqual(r["history_count"], 0)


# =============================================================================================== ścieżka nauki
class TestRoadmap(unittest.TestCase):
    def setUp(self):
        store.delete("flashcards_state")

    def test_structure_and_kb_links(self):
        raw = json.loads((ROOT / "data" / "roadmap.json").read_text(encoding="utf-8"))
        self.assertEqual([s["title"] for s in raw["stages"]], ["Podstawy", "I etap OI", "II etap OI", "III etap OI"])
        ids = {a["id"] for a in kb.articles()}
        tids = []
        for s in raw["stages"]:
            self.assertTrue(s["criteria"])
            for t in s["topics"]:
                tids.append(t["id"])
                existing = [k for k in t["kb"] if k in ids]
                self.assertTrue(existing, f"temat {t['id']} nie ma żadnego istniejącego artykułu")
        self.assertEqual(len(tids), len(set(tids)))
        self.assertEqual(T.roadmap_data()["missing_kb"], [])

    def test_round_matching(self):
        cases = {
            "Dzień 7 - Toposort i dynamiki na grafach": ["toposort"],
            "Drzewa przedziałowe": ["segtree"],
            "Dzień 3 - Drzewa przedziałowe": ["segtree"],
            "Sortowanie topologiczne": ["toposort"],
            "Sortowanie": ["sorting"],
            "Dzień 1 - Binsearch po wyniku": ["binsearch-answer"],
            "Wyszukiwanie binarne po wyniku": ["binsearch-answer"],
            "Wyszukiwanie binarne na 12.02.2026": ["binsearch"],
            "Find & Union": ["dsu-mst"],
            "Dynamiki na drzewach": ["tree-dp"],
            "Gąsienica": ["two-pointers"],
            "Dzień KOPCA - mojej ulubionej struktury": ["heap-ds"],
            "T[][]": ["arrays"],
            "Warsztaty 1": [],
            "": [],
            None: [],
        }
        for rn, exp in cases.items():
            self.assertEqual(T.topics_for_round(rn), exp, rn)
        self.assertEqual(set(T.topics_for_round("Dynamiki i zachłany")), {"dp-basic", "greedy"})
        self.assertEqual(set(T.topics_for_round("Rekurencja i teoria liczb")), {"recursion", "math-basic"})

    def test_roadmap_without_data(self):
        r = T.roadmap(probs=[])
        self.assertEqual(len(r["stages"]), 4)
        self.assertTrue(r["stages"][0]["unlocked"])
        self.assertFalse(r["stages"][3]["unlocked"])
        self.assertEqual(r["overall"]["solved_matched"], 0)
        self.assertIsNotNone(r["next"])
        for st in r["stages"]:
            for t in st["topics"]:
                self.assertTrue(0 <= t["progress"] <= 1)
                self.assertTrue(t["criteria"])

    def test_reading_counts_towards_progress(self):
        before = {t["id"]: t for st in T.roadmap(probs=[])["stages"] for t in st["topics"]}["pitfalls"]
        for k in ("pit-int-overflow", "pit-out-of-bounds", "pit-uninitialized"):
            journal.log_event("kb_view", {"id": k})
        after = {t["id"]: t for st in T.roadmap(probs=[])["stages"] for t in st["topics"]}["pitfalls"]
        self.assertGreater(after["progress"], before["progress"])
        self.assertEqual(after["read"], 3)
        self.assertTrue(next(c for c in after["criteria"] if c["id"] == "read")["done"])

    @unittest.skipUnless(HAS_REAL, "brak prawdziwych danych ucznia")
    def test_roadmap_with_real_data(self):
        probs = journal.solved_problems(REAL_INDEX)
        r = T.roadmap(probs)
        self.assertGreater(r["overall"]["solved_matched"], 150)
        rows = {t["id"]: t for st in r["stages"] for t in st["topics"]}
        for tid in ("graphs-basic", "dp-basic", "stl", "sorting", "dijkstra", "segtree", "toposort"):
            self.assertGreater(rows[tid]["solved"], 0, tid)
        self.assertTrue(r["stages"][1]["unlocked"])
        self.assertTrue(r["stages"][2]["unlocked"])


# =============================================================================================== zadanie dnia
class TestDaily(unittest.TestCase):
    def test_shape_and_determinism_without_data(self):
        d1 = T.daily(date(2026, 11, 9), probs=[])
        d2 = T.daily(date(2026, 11, 9), probs=[])
        self.assertEqual(d1["topic"], d2["topic"])
        self.assertIsNone(d1["task"])
        self.assertIn("due", d1["flashcards"])
        for k in ("title", "kb", "reason"):
            self.assertIn(k, d1["topic"])

    def test_synthetic_task_is_old(self):
        today = date(2026, 11, 9)
        probs = []
        for i, days_ago in enumerate((1, 3, 100, 200)):
            dt = datetime.combine(today - timedelta(days=days_ago), datetime.min.time())
            probs.append({"key": f"k{i}", "id": f"sio2:{i}", "last_id": f"sio2:{i}", "problem_name": f"Z{i}",
                          "problem_short": f"z{i}", "contest_name": "C", "round": "Sumy prefiksowe", "url": None,
                          "first": dt, "last": dt, "first_time": str(dt), "last_time": str(dt), "submissions": 1})
        d = T.daily(today, probs=probs)
        self.assertIn(d["task"]["id"], {"sio2:2", "sio2:3"})
        self.assertGreaterEqual(d["task"]["days_ago"], 45)
        self.assertTrue(d["task"]["reason"])

    def test_cached_for_the_whole_day(self):
        store.delete("training_daily")
        first = T.handle_daily({})
        journal.log_event("kb_view", {"id": (first.get("topic") or {}).get("kb") or "stl-set"})   # postęp się zmienia…
        again = T.handle_daily({})
        self.assertTrue(again.get("cached"))
        self.assertEqual(first["topic"], again["topic"])                                      # …ale temat dnia nie
        self.assertEqual(store.get("training_daily")["date"], date.today().isoformat())
        with self.assertRaises(ApiError):
            T.handle_daily({"date": "wczoraj"})

    @unittest.skipUnless(HAS_REAL, "brak prawdziwych danych ucznia")
    def test_real_data_deterministic_and_valid_id(self):
        probs = journal.solved_problems(REAL_INDEX)
        days = [date(2026, 11, 9) + timedelta(days=i) for i in range(6)]
        res = [T.daily(d, probs=probs) for d in days]
        again = T.daily(days[0], probs=probs)
        self.assertEqual(res[0]["task"]["id"], again["task"]["id"])
        self.assertEqual(res[0]["topic"]["id"], again["topic"]["id"])
        self.assertGreater(len({r["task"]["id"] for r in res}), 1, "zadanie dnia powinno się zmieniać")
        recs = json.loads(REAL_INDEX.read_text(encoding="utf-8"))["programs"]
        ids = {f"{r.get('source') or 'local'}:{r.get('submission_id') or r.get('path')}" for r in recs}
        for r in res:
            self.assertIn(r["task"]["id"], ids)
            self.assertRegex(r["task"]["id"], r"^[\w.\-]+:\w+")
            self.assertTrue(r["task"]["problem_name"])
            self.assertTrue(r["topic"]["kb"])


if __name__ == "__main__":
    unittest.main()

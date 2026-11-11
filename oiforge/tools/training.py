"""Trening (OI Forge): tryb zawodów OI, fiszki z powtórkami SM-2, ścieżka nauki i zadanie dnia.

Bez AI — reguły, statystyka i algorytm SM-2. Stan trwały w `core.store`:
  contest_current  — trwająca sesja zawodów (przetrwa odświeżenie strony i restart aplikacji)
  contest_history  — zakończone sesje (podsumowania + oś zdarzeń), najnowsze pierwsze
  flashcards_state — {"cards": {id: {ef, reps, interval, due, ...}}, "days": {dzień: {...}}, "extra": {dzień: n}}

HTTP:
  POST /api/contest/start {duration_min=300, tasks:[{name, short}], title?, mode?: contest|training, force?}
  GET  /api/contest/state · POST /api/contest/update {...} · POST /api/contest/stop {discard?}
  GET  /api/contest/history · POST /api/contest/history/delete {id}
  GET  /api/flashcards/decks · GET /api/flashcards/due?deck=&topic=&limit=&extra_new= · POST /api/flashcards/review {card_id, grade}
  GET  /api/roadmap · GET /api/training/daily
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta

from .. import config
from ..core import kb, store
from ..server_api import ApiError
from . import journal

ROADMAP_FILE = config.DATA_DIR / "roadmap.json"
FLASHCARDS_FILE = config.DATA_DIR / "flashcards.json"

_clock = time.time            # podmieniane w testach


def now() -> float:
    return _clock()


def _today(ts: float | None = None) -> date:
    return datetime.fromtimestamp(now() if ts is None else ts).date()


def _midnight(d: date) -> float:
    return datetime(d.year, d.month, d.day).timestamp()


def _seed(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:12], 16)


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(s).lower()).strip()


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n == 1:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def _fmt_dur(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min" if m else f"{sec} s"


# =============================================================================================================
#                                              TRYB ZAWODÓW
# =============================================================================================================
STATUSES = [
    {"id": "unread", "label": "Nieczytane", "tone": "neutral", "icon": "eye"},
    {"id": "reading", "label": "Czytam", "tone": "info", "icon": "book-open"},
    {"id": "brute", "label": "Brute", "tone": "warn", "icon": "hammer"},
    {"id": "partial", "label": "Częściowe", "tone": "accent", "icon": "gauge"},
    {"id": "full", "label": "Pełne", "tone": "success", "icon": "circle-check"},
]
STATUS_LABEL = {s["id"]: s["label"] for s in STATUSES}
ACTIVE_STATUSES = {"reading", "brute", "partial"}

CHECKLIST = [
    {"id": "ll", "label": "long long wszędzie, gdzie wynik, suma albo iloczyn może przekroczyć ~2·10⁹", "kb": "num-overflow"},
    {"id": "arrays", "label": "Tablice dość duże: maksymalne n z treści + zapas (indeksy od 1?)", "kb": "pit-out-of-bounds"},
    {"id": "fastio", "label": "Szybkie wejście/wyjście: sync_with_stdio(false), cin.tie(0), '\\n' zamiast endl", "kb": "io-fast-io"},
    {"id": "debug", "label": "Usunięte wypisywanie debugów (cerr, dodatkowe cout, pauzy)", "kb": "oi-debugging"},
    {"id": "sample", "label": "Sprawdzony przykład z treści — dokładnie ten format wyjścia", "kb": "oi-reading-statement"},
    {"id": "n1", "label": "Przypadki brzegowe: n = 1, wartości minimalne i maksymalne", "kb": "pit-off-by-one"},
    {"id": "memory", "label": "Pamięć policzona: rozmiar tablic × typ mieści się w limicie", "kb": "oi-memory"},
]
CHECK_IDS = {c["id"] for c in CHECKLIST}

_contest_lock = threading.RLock()
HISTORY_MAX = 60
EVENTS_MAX = 400


def _elapsed(s: dict, at: float | None = None) -> float:
    """Czas aktywny sesji w sekundach (bez pauz)."""
    at = s.get("finished_at") or (now() if at is None else at)
    total = at - s["started_at"]
    for p in s.get("pauses") or []:
        st, en = p[0], p[1] if p[1] is not None else at
        total -= max(0.0, min(en, at) - st)
    return max(0.0, total)


def _is_paused(s: dict) -> bool:
    ps = s.get("pauses") or []
    return bool(ps) and ps[-1][1] is None


def _reminders(s: dict) -> list[dict]:
    """Harmonogram przypomnień (w sekundach czasu aktywnego)."""
    dur = int(s["duration_min"]) * 60
    out = []
    finals = []
    if dur > 35 * 60:
        finals.append({"id": "final30", "at": dur - 1800, "kind": "final",
                       "text": "Zostało 30 minut — wyślij wszystko, co masz, i sprawdź long longi!"})
    if dur > 20 * 60:
        finals.append({"id": "final10", "at": dur - 600, "kind": "final",
                       "text": "Zostało 10 minut — nie zaczynaj nic nowego. Ostatnie wysyłki i sprawdzenie checklisty."})
    k = 1
    while k * 3600 < dur:
        at = k * 3600
        if all(abs(at - f["at"]) >= 600 for f in finals):
            h = f"{k} {_plural(k, 'godzina', 'godziny', 'godzin')}"
            out.append({"id": f"hour{k}", "at": at, "kind": "backup",
                        "text": f"Minęła {h} — zrób backup i wyślij to, co masz (nawet brute za częściowe punkty)."})
        k += 1
    out += finals
    out.append({"id": "end", "at": dur, "kind": "end", "text": "Koniec czasu! Zakończ sesję i zobacz podsumowanie."})
    out.sort(key=lambda r: r["at"])
    return out


def _task_times(s: dict, at_elapsed: float | None = None) -> dict[str, float]:
    """Czas spędzony nad każdym zadaniem — z dziennika „fokusu” (w czasie aktywnym)."""
    end = _elapsed(s) if at_elapsed is None else at_elapsed
    log = s.get("focus_log") or []
    out = {t["id"]: 0.0 for t in s.get("tasks", [])}
    for i, (e, tid) in enumerate(log):
        nxt = log[i + 1][0] if i + 1 < len(log) else end
        if tid and tid in out:
            out[tid] += max(0.0, nxt - e)
    return out


def _event(s: dict, kind: str, text: str, task: str | None = None) -> None:
    ev = {"t": round(now(), 3), "e": round(_elapsed(s), 1), "kind": kind, "text": text}
    if task:
        ev["task"] = task
    s.setdefault("events", []).append(ev)
    if len(s["events"]) > EVENTS_MAX:
        s["events"] = s["events"][:5] + s["events"][-(EVENTS_MAX - 5):]


def _set_focus(s: dict, tid: str | None) -> None:
    if s.get("focus") == tid:
        return
    s["focus"] = tid
    s.setdefault("focus_log", []).append([round(_elapsed(s), 1), tid])


def _task(s: dict, tid) -> dict:
    for t in s.get("tasks", []):
        if t["id"] == tid:
            return t
    raise ApiError("Nie ma takiego zadania w sesji.", 404)


def _clean_task(raw, i: int) -> dict:
    if isinstance(raw, str):
        raw = {"name": raw}
    raw = raw if isinstance(raw, dict) else {}
    name = str(raw.get("name") or "").strip()[:80]
    short = re.sub(r"\s+", "", str(raw.get("short") or ""))[:12]
    letter = chr(ord("A") + i) if i < 26 else str(i + 1)
    return {"id": f"t{i + 1}", "name": name or f"Zadanie {letter}", "short": short or letter, "status": "unread",
            "notes": "", "points": None, "checklist": {}, "status_log": [[0.0, "unread"]], "solved_at": None}


def _public(s: dict | None) -> dict | None:
    if not s:
        return None
    el = _elapsed(s)
    dur = int(s["duration_min"]) * 60
    out = {k: v for k, v in s.items() if k not in ("focus_log",)}
    fired = set(s.get("fired") or [])
    out.update(elapsed=round(el, 1), remaining=round(dur - el, 1), duration_s=dur, paused=_is_paused(s),
               over=el >= dur, progress=round(min(1.0, el / dur), 4) if dur else 1.0,
               paused_total=round(sum(((p[1] or now()) - p[0]) for p in s.get("pauses") or []), 1),
               task_times={k: round(v, 1) for k, v in _task_times(s, el).items()},
               reminders=[dict(r, fired=r["id"] in fired, due=el >= r["at"]) for r in _reminders(s)])
    return out


def _brief(h: dict) -> dict:
    keys = ("id", "title", "mode", "started_at", "finished_at", "duration_min", "elapsed", "paused_total",
            "solved_full", "solved_partial", "points_total", "tasks_count", "aborted")
    return {k: h.get(k) for k in keys}


def contest_state() -> dict:
    with _contest_lock:
        s = store.get("contest_current")
        hist = store.get("contest_history", []) or []
    return {"active": bool(s), "session": _public(s), "now": now(), "statuses": STATUSES, "checklist": CHECKLIST,
            "history": [_brief(h) for h in hist[:20]], "history_count": len(hist)}


def contest_start(body: dict) -> dict:
    try:
        dur = int(float(body.get("duration_min") or 300))
    except (TypeError, ValueError):
        raise ApiError("Czas trwania musi być liczbą minut.")
    if not 5 <= dur <= 720:
        raise ApiError("Czas trwania: od 5 do 720 minut.")
    tasks_raw = body.get("tasks") or []
    if not isinstance(tasks_raw, list):
        raise ApiError("Pole tasks musi być listą zadań.")
    tasks_raw = [t for t in tasks_raw if (isinstance(t, dict) and (str(t.get("name") or "").strip() or str(t.get("short") or "").strip()))
                 or (isinstance(t, str) and t.strip())]
    if len(tasks_raw) > 12:
        raise ApiError("Maksymalnie 12 zadań w jednej sesji.")
    if not tasks_raw:
        tasks_raw = [{} for _ in range(3)]
    mode = body.get("mode") if body.get("mode") in ("contest", "training") else "contest"
    with _contest_lock:
        cur = store.get("contest_current")
        if cur and not body.get("force"):
            raise ApiError("Trwa już sesja zawodów — zakończ ją, zanim zaczniesz nową.", 409)
        if cur:
            _finish(cur, aborted=True)
        t0 = now()
        s = {"id": "c" + datetime.fromtimestamp(t0).strftime("%Y%m%d-%H%M%S"),
             "title": (str(body.get("title") or "").strip()[:80] or f"Sesja {datetime.fromtimestamp(t0).strftime('%d.%m.%Y %H:%M')}"),
             "mode": mode, "duration_min": dur, "started_at": t0, "pauses": [], "finished_at": None,
             "tasks": [_clean_task(t, i) for i, t in enumerate(tasks_raw)], "focus": None, "focus_log": [[0.0, None]],
             "events": [], "fired": []}
        _event(s, "start", f"Start: {s['title']} ({_fmt_dur(dur * 60)}, {len(s['tasks'])} "
                           f"{_plural(len(s['tasks']), 'zadanie', 'zadania', 'zadań')}).")
        store.set("contest_current", s)
    journal.log_event("contest_start", {"duration_min": dur, "tasks": len(s["tasks"]), "mode": mode})
    return contest_state()


def contest_update(body: dict) -> dict:
    with _contest_lock:
        s = store.get("contest_current")
        if not s:
            raise ApiError("Nie ma trwającej sesji zawodów.", 404)
        action = body.get("action")
        changed = False
        if action in ("pause", "resume", "toggle_pause"):
            paused = _is_paused(s)
            want = (not paused) if action == "toggle_pause" else (action == "pause")
            if want and not paused:
                s.setdefault("pauses", []).append([now(), None])
                if s.get("mode") == "contest":
                    s["mode"] = "training"
                    _event(s, "mode", "Pauza zmienia sesję w trening (na prawdziwych zawodach nie ma pauz).")
                _event(s, "pause", "Pauza.")
                changed = True
            elif not want and paused:
                s["pauses"][-1][1] = now()
                _event(s, "resume", f"Wznowiono po {_fmt_dur(s['pauses'][-1][1] - s['pauses'][-1][0])} pauzy.")
                changed = True
        ack = body.get("ack") if body.get("ack") is not None else (body.get("reminder") if action == "ack" else None)
        if ack is not None:
            ids = ack if isinstance(ack, list) else [ack]
            known = {r["id"]: r for r in _reminders(s)}
            for rid in ids:
                rid = str(rid)
                if rid in known and rid not in s.setdefault("fired", []):
                    s["fired"].append(rid)
                    _event(s, "reminder", known[rid]["text"])
                    changed = True
        if action == "note":
            text = str(body.get("text") or "").strip()[:500]
            if not text:
                raise ApiError("Notatka jest pusta.")
            _event(s, "note", text, body.get("task_id"))
            changed = True
        if action == "add_task":
            if len(s["tasks"]) >= 12:
                raise ApiError("Maksymalnie 12 zadań w jednej sesji.")
            t = _clean_task({"name": body.get("name"), "short": body.get("short")}, len(s["tasks"]))
            used = {x["id"] for x in s["tasks"]}
            n = len(s["tasks"]) + 1
            while f"t{n}" in used:
                n += 1
            t["id"] = f"t{n}"
            t["status_log"] = [[round(_elapsed(s), 1), "unread"]]
            s["tasks"].append(t)
            _event(s, "task", f"Dodano zadanie {t['short']}: {t['name']}.", t["id"])
            changed = True
        if action == "remove_task":
            t = _task(s, body.get("task_id"))
            if len(s["tasks"]) <= 1:
                raise ApiError("Sesja musi mieć co najmniej jedno zadanie.")
            s["tasks"] = [x for x in s["tasks"] if x["id"] != t["id"]]
            if s.get("focus") == t["id"]:
                _set_focus(s, None)
            _event(s, "task", f"Usunięto zadanie {t['short']}.")
            changed = True
        if action == "extend":
            try:
                mins = int(body.get("minutes") or 0)
            except (TypeError, ValueError):
                mins = 0
            if mins:
                s["duration_min"] = max(5, min(720, int(s["duration_min"]) + mins))
                _event(s, "extend", f"Zmieniono czas sesji na {_fmt_dur(s['duration_min'] * 60)}.")
                changed = True
        if action == "rename":
            title = str(body.get("title") or "").strip()[:80]
            if title:
                s["title"] = title
                changed = True
        if "focus" in body and "task_id" not in body:
            fid = body.get("focus")
            if fid is not None:
                _task(s, fid)
            _set_focus(s, fid)
            changed = True
        tid = body.get("task_id")
        if tid is not None and action not in ("remove_task", "note"):
            t = _task(s, tid)
            label = f"{t['short']}"
            if "status" in body:
                st = body.get("status")
                if st not in STATUS_LABEL:
                    raise ApiError("Nieznany status zadania.")
                if st != t["status"]:
                    el = round(_elapsed(s), 1)
                    _event(s, "status", f"{label}: {STATUS_LABEL[t['status']].lower()} → {STATUS_LABEL[st].lower()}.", t["id"])
                    t["status"] = st
                    t.setdefault("status_log", []).append([el, st])
                    if st == "full" and t.get("solved_at") is None:
                        t["solved_at"] = el
                    if st == "full" and t.get("points") is None:
                        t["points"] = 100
                    if st in ACTIVE_STATUSES:
                        _set_focus(s, t["id"])
                    elif s.get("focus") == t["id"]:
                        _set_focus(s, None)
                    changed = True
            if "notes" in body:
                t["notes"] = str(body.get("notes") or "")[:20000]
                changed = True
            if isinstance(body.get("checklist"), dict):
                cl = t.setdefault("checklist", {})
                for k, v in body["checklist"].items():
                    if k in CHECK_IDS:
                        cl[k] = bool(v)
                changed = True
            if "points" in body:
                p = body.get("points")
                try:
                    t["points"] = None if p in (None, "") else max(0, min(100, int(float(p))))
                except (TypeError, ValueError):
                    raise ApiError("Punkty muszą być liczbą 0–100.")
                changed = True
            if "name" in body and str(body.get("name") or "").strip():
                t["name"] = str(body["name"]).strip()[:80]
                changed = True
            if "short" in body and str(body.get("short") or "").strip():
                t["short"] = re.sub(r"\s+", "", str(body["short"]))[:12]
                changed = True
            if "focus" in body:
                _set_focus(s, t["id"] if body.get("focus") else (None if s.get("focus") == t["id"] else s.get("focus")))
                changed = True
        if changed:
            store.set("contest_current", s)
    return contest_state()


def _advice(summary: dict) -> list[str]:
    tips = []
    tasks = summary["tasks"]
    el = summary["elapsed"] or 1
    full = [t for t in tasks if t["status"] == "full"]
    unread = [t for t in tasks if t["status"] == "unread"]
    if tasks and len(full) == len(tasks):
        tips.append("Komplet pełnych rozwiązań — świetna robota! Spróbuj następnym razem trudniejszego zestawu albo krótszego czasu.")
    if unread:
        names = ", ".join(t["short"] for t in unread)
        tips.append(f"Nie przeczytano: {names}. Na zawodach przeczytaj wszystkie treści w pierwszych 20–30 minutach — "
                    "łatwe punkty bywają w zadaniu, którego się nie spodziewasz.")
    for t in tasks:
        if t["status"] != "full" and t["time_s"] > 0.45 * el and el > 1800:
            tips.append(f"Na zadanie {t['short']} poszło {round(100 * t['time_s'] / el)}% czasu bez pełnego rozwiązania. "
                        "Gdy przez ~60–90 minut nie ma postępu, wyślij brute za podzadania i przejdź dalej.")
            break
    if any(t["status"] in ("brute", "partial") for t in tasks):
        tips.append("Brute force i podzadania dały punkty częściowe — to dobra strategia. Sprawdź w historii, czy dało się je wysłać wcześniej.")
    sent = [t for t in tasks if t["status"] in ("full", "partial", "brute")]
    low_check = [t for t in sent if sum(1 for v in (t.get("checklist") or {}).values() if v) < 3]
    if sent and len(low_check) * 2 >= len(sent):
        tips.append("Przed wysłaniem przejdź checklistę (long long, rozmiary tablic, przykład, n = 1) — "
                    "to najczęstsze źródła utraconych punktów.")
    if summary["duration_min"] * 60 * 0.5 > el and not summary.get("aborted"):
        tips.append("Sesja zakończona przed połową czasu — jeśli to trening, zapisz w notatkach, co sprawiło trudność.")
    if summary.get("paused_total", 0) > 600:
        tips.append("W sesji były pauzy. Raz na jakiś czas zrób pełną symulację bez pauz — kondycja na 5 godzin też się liczy.")
    if not tips:
        tips.append("Dobra sesja. Przejrzyj notatki do zadań i dopisz do dziennika, czego się nauczyłeś.")
    return tips


def _finish(s: dict, aborted: bool = False) -> dict:
    if _is_paused(s):
        s["pauses"][-1][1] = now()
    s["finished_at"] = now()
    _event(s, "stop", "Sesja przerwana." if aborted else "Koniec sesji.")
    el = _elapsed(s)
    times = _task_times(s, el)
    tasks = []
    for t in s["tasks"]:
        pts = t.get("points")
        tasks.append({"id": t["id"], "name": t["name"], "short": t["short"], "status": t["status"],
                      "status_label": STATUS_LABEL.get(t["status"], t["status"]), "time_s": round(times.get(t["id"], 0.0), 1),
                      "solved_at": t.get("solved_at"), "points": pts, "notes": t.get("notes", ""),
                      "checklist": t.get("checklist", {}), "status_log": t.get("status_log", [])})
    summary = {
        "id": s["id"], "title": s["title"], "mode": s.get("mode"), "started_at": s["started_at"], "finished_at": s["finished_at"],
        "duration_min": s["duration_min"], "elapsed": round(el, 1),
        "paused_total": round(sum((p[1] - p[0]) for p in s.get("pauses") or []), 1), "pauses": len(s.get("pauses") or []),
        "tasks": tasks, "tasks_count": len(tasks),
        "solved_full": sum(1 for t in tasks if t["status"] == "full"),
        "solved_partial": sum(1 for t in tasks if t["status"] in ("partial", "brute")),
        "points_total": sum(int(t["points"] or 0) for t in tasks),
        "unassigned_s": round(max(0.0, el - sum(times.values())), 1),
        "events": s.get("events", [])[-EVENTS_MAX:], "aborted": aborted,
    }
    summary["advice"] = _advice(summary)
    hist = store.get("contest_history", []) or []
    hist = [h for h in hist if h.get("id") != summary["id"]]
    hist.insert(0, summary)
    store.set("contest_history", hist[:HISTORY_MAX])
    store.delete("contest_current")
    return summary


def contest_stop(body: dict) -> dict:
    with _contest_lock:
        s = store.get("contest_current")
        if not s:
            raise ApiError("Nie ma trwającej sesji zawodów.", 404)
        if body.get("discard"):
            store.delete("contest_current")
            return {"ok": True, "discarded": True, "summary": None, **contest_state()}
        summary = _finish(s)
    journal.log_event("contest_stop", {"elapsed": summary["elapsed"], "full": summary["solved_full"],
                                       "tasks": summary["tasks_count"], "points": summary["points_total"]})
    return {"ok": True, "summary": summary, **contest_state()}


def handle_contest_history(q: dict) -> dict:
    hist = store.get("contest_history", []) or []
    hid = q.get("id")
    if hid:
        for h in hist:
            if h.get("id") == hid:
                return {"session": h}
        raise ApiError("Nie ma takiej sesji w historii.", 404)
    return {"history": hist}


def handle_contest_history_delete(body: dict) -> dict:
    hid = body.get("id")
    with _contest_lock:
        hist = store.get("contest_history", []) or []
        new = [h for h in hist if h.get("id") != hid]
        if len(new) == len(hist):
            raise ApiError("Nie ma takiej sesji w historii.", 404)
        store.set("contest_history", new)
    return {"history": [_brief(h) for h in new[:20]], "history_count": len(new)}


# =============================================================================================================
#                                              FISZKI (SM-2)
# =============================================================================================================
GRADES = {1: {"label": "Nie wiem", "q": 1}, 2: {"label": "Trudne", "q": 3}, 3: {"label": "Dobrze", "q": 4}, 4: {"label": "Łatwe", "q": 5}}
RELEARN_S = 600               # karta „Nie wiem” wraca po 10 minutach
DEFAULT_NEW_PER_DAY = 15
MIN_EF = 1.3

_fc_lock = threading.RLock()
_hand_cache: dict = {"sig": None, "decks": [], "cards": [], "errors": []}
_auto_cache: dict = {"sig": None, "decks": [], "cards": []}


def sm2(state: dict | None, grade: int, at: float | None = None) -> dict:
    """Jeden krok SM-2. grade: 1 Nie wiem · 2 Trudne · 3 Dobrze · 4 Łatwe (→ jakość q = 1, 3, 4, 5).

    Łatwość (SM-2): EF' = EF + (0.1 − (5−q)(0.08 + (5−q)·0.02)), minimum 1.3 — aktualizowana po każdej ocenie.
    q < 3: powtórki od nowa (reps = 0), karta wraca za 10 minut.
    q ≥ 3: interwały SM-2: 1 dzień, 6 dni, potem I·EF — z dwiema poprawkami jak w Anki, żeby przyciski
    różniły się terminem: „Łatwe” dostaje premię ×1,3 (pierwszy interwał 3 dni), „Trudne” rośnie wolniej
    (drugi interwał 3 dni, dalej ×1,2). Interwał zawsze rośnie o co najmniej 1 dzień. Termin = północ (dziś + interwał).
    """
    if grade not in GRADES:
        raise ValueError("grade musi być 1–4")
    at = now() if at is None else at
    st = dict(state or {})
    ef = float(st.get("ef", 2.5))
    reps = int(st.get("reps", 0))
    interval = float(st.get("interval", 0))
    q = GRADES[grade]["q"]
    ef = max(MIN_EF, round(ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)), 4))
    if q < 3:
        st["lapses"] = int(st.get("lapses", 0)) + (1 if reps else 0)
        reps = 0
        interval = 0
        due = at + RELEARN_S
    else:
        reps += 1
        if reps == 1:
            new = 3 if q == 5 else 1
        elif reps == 2:
            new = {3: 3, 4: 6, 5: 8}[q]
        else:
            factor = 1.2 if q == 3 else ef * (1.3 if q == 5 else 1.0)
            new = round(interval * factor)
        interval = int(max(interval + 1, new, 1))
        due = _midnight(_today(at) + timedelta(days=interval))
    st.update(ef=ef, reps=reps, interval=interval, due=round(due, 3), last=round(at, 3), last_grade=grade,
              reviews=int(st.get("reviews", 0)) + 1)
    st.setdefault("first", round(at, 3))
    return st


def interval_label(st: dict, at: float | None = None) -> str:
    at = now() if at is None else at
    secs = st["due"] - at
    if secs < 3600:
        return f"{max(1, round(secs / 60))} min"
    days = (datetime.fromtimestamp(st["due"]).date() - _today(at)).days
    if days <= 0:
        return "dziś"
    if days == 1:
        return "jutro"
    if days < 30:
        return f"za {days} dni"
    if days < 365:
        m = max(1, round(days / 30))
        return f"za ~{m} mies."
    y = max(1, round(days / 365))
    return f"za ~{y} {_plural(y, 'rok', 'lata', 'lat')}"


def preview(state: dict | None, at: float | None = None) -> dict:
    at = now() if at is None else at
    out = {}
    for g, meta in GRADES.items():
        st = sm2(state, g, at)
        out[str(g)] = {"label": meta["label"], "next": interval_label(st, at), "interval": st["interval"]}
    return out


_kbm_cache: dict = {"sig": None, "map": {}}
_ksig: dict = {"t": -1e9, "sig": None}
SIG_TTL = 2.0


def _kb_sig():
    """Sygnatura bazy wiedzy sprawdzana najwyżej co 2 s (każde sprawdzenie to stat wszystkich plików)."""
    t = time.monotonic()
    if t - _ksig["t"] > SIG_TTL:
        try:
            _ksig["sig"] = kb.signature()
        except Exception:  # noqa: BLE001
            _ksig["sig"] = None
        _ksig["t"] = t
    return _ksig["sig"]


def _kb_map() -> dict[str, dict]:
    """id → artykuł (bez stat-ów przy każdym kb.get)."""
    sig = _kb_sig()
    if sig is None or sig != _kbm_cache["sig"]:
        _kbm_cache.update(sig=sig, map={a["id"]: a for a in kb.articles()})
    return _kbm_cache["map"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(s)).strip("-") or "inne"


def _h8(s: str) -> str:
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:8]


def _load_hand() -> dict:
    try:
        stt = FLASHCARDS_FILE.stat()
        sig = (stt.st_mtime_ns, stt.st_size)
    except OSError:
        sig = None
    if sig == _hand_cache["sig"]:
        return _hand_cache
    decks, cards, errors = [], [], []
    try:
        data = json.loads(FLASHCARDS_FILE.read_text(encoding="utf-8")) if sig else {}
    except (OSError, ValueError) as e:
        data, errors = {}, [f"flashcards.json: {e}"]
    deck_ids = set()
    for d in data.get("decks", []) if isinstance(data, dict) else []:
        if isinstance(d, dict) and d.get("id"):
            decks.append({"id": d["id"], "title": d.get("title") or d["id"], "description": d.get("description", ""),
                          "icon": d.get("icon") or "layers", "auto": False})
            deck_ids.add(d["id"])
    seen = set()
    for i, c in enumerate(data.get("cards", []) if isinstance(data, dict) else []):
        if not isinstance(c, dict) or not c.get("id") or not c.get("q") or not c.get("a"):
            errors.append(f"karta #{i}: brak id/q/a")
            continue
        if c["id"] in seen:
            errors.append(f"duplikat id {c['id']}")
            continue
        if c.get("deck") not in deck_ids:
            errors.append(f"{c['id']}: nieznana talia {c.get('deck')}")
            continue
        seen.add(c["id"])
        cards.append({"id": c["id"], "deck": c["deck"], "q": c["q"], "a": c["a"], "kb": c.get("kb"),
                      "tags": c.get("tags") or [], "source": "hand", "order": i})
    _hand_cache.update(sig=sig, decks=decks, cards=cards, errors=errors)
    return _hand_cache


def _q_text(q: str) -> str:
    q = str(q or "").strip()
    if not q:
        return q
    q = q[0].upper() + q[1:]
    if not q.endswith(("?", ".", "!", ":")):
        q += "?"
    return q


def _auto_cards() -> dict:
    """Karty generowane z bazy wiedzy: złożoność, funkcje (sygnatura → opis) i pytania QA."""
    sig = _kb_sig()
    if sig is not None and sig == _auto_cache["sig"]:
        return _auto_cache
    decks: dict[str, dict] = {}
    cards = []
    for a in kb.articles():
        cat = a.get("category") or "Inne"
        did = "kb-" + _slug(cat)
        if did not in decks:
            decks[did] = {"id": did, "title": cat, "description": f"Karty z bazy wiedzy: {cat}.", "icon": "library",
                          "auto": True, "category": cat}
        aid, title, lvl = a["id"], a.get("title", a["id"]), a.get("level") or 1
        base = {"deck": did, "kb": aid, "source": "kb", "level": lvl, "article": title}
        if a.get("complexity"):
            cards.append(dict(base, id=f"kb:{aid}:cx", kind="complexity",
                              q=f"**{title}**\n\nJaka jest złożoność najważniejszych operacji?",
                              a=f"{a['complexity']}" + (f"\n\n{a['summary']}" if a.get("summary") else "")))
        for f in (a.get("functions") or [])[:8]:
            if not isinstance(f, dict) or not f.get("sig") or not f.get("desc"):
                continue
            ans = str(f["desc"]).strip()
            ans = ans[0].upper() + ans[1:] if ans else ans
            if f.get("complexity"):
                ans += f"\n\n**Złożoność:** {f['complexity']}"
            if f.get("returns"):
                ans += f"\n\n**Zwraca:** `{f['returns']}`"
            if f.get("example"):
                ans += f"\n\n```cpp\n{f['example']}\n```"
            q = f"Co robi `{f['sig']}`" + (" i ile to kosztuje?" if f.get("complexity") else "?")
            cards.append(dict(base, id=f"kb:{aid}:fn:{_h8(f['sig'])}", kind="function", q=q, a=ans))
        for qa in (a.get("qa") or [])[:6]:
            if not isinstance(qa, dict) or not qa.get("a"):
                continue
            qs = qa.get("q")
            q0 = qs[0] if isinstance(qs, list) and qs else qs
            if not q0:
                continue
            ans = str(qa["a"])
            if qa.get("code"):
                ans += f"\n\n```cpp\n{qa['code']}\n```"
            cards.append(dict(base, id=f"kb:{aid}:qa:{_h8(q0)}", kind="qa", q=_q_text(q0), a=ans))
    order = {c: i for i, c in enumerate(["C++ język", "STL kontenery", "STL algorytmy", "Wejście/wyjście", "Liczby i typy",
                                          "Pułapki", "OI praktyka", "Techniki", "Struktury danych", "Grafy", "Drzewa",
                                          "Programowanie dynamiczne", "Teksty", "Matematyka", "Geometria", "Teoria gier",
                                          "Narzędzia"])}
    dl = sorted(decks.values(), key=lambda d: (order.get(d["category"], 99), d["title"]))
    cards.sort(key=lambda c: (c["level"],))  # stabilnie: łatwiejsze artykuły najpierw
    for i, c in enumerate(cards):
        c["order"] = i
    _auto_cache.update(sig=sig, decks=dl, cards=cards)
    return _auto_cache


def all_decks() -> list[dict]:
    return list(_load_hand()["decks"]) + list(_auto_cards()["decks"])


def all_cards() -> list[dict]:
    hand = _load_hand()["cards"]
    ids = {c["id"] for c in hand}
    return list(hand) + [c for c in _auto_cards()["cards"] if c["id"] not in ids]


def _cards_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in all_cards()}


def _fc_state() -> dict:
    st = store.get("flashcards_state", {}) or {}
    st.setdefault("cards", {})
    st.setdefault("days", {})
    st.setdefault("extra", {})
    return st


def _new_per_day() -> int:
    try:
        v = int((store.settings().get("flashcards") or {}).get("new_per_day", DEFAULT_NEW_PER_DAY))
    except (TypeError, ValueError, AttributeError):
        v = DEFAULT_NEW_PER_DAY
    return max(0, min(v, 200))


def _new_left(st: dict, at: float) -> int:
    day = _today(at).isoformat()
    done = int((st["days"].get(day) or {}).get("new", 0))
    extra = int(st["extra"].get(day, 0))
    return max(0, _new_per_day() + extra - done)


def _topic_kb(topic_id: str) -> set[str]:
    for s in roadmap_data()["stages"]:
        for t in s["topics"]:
            if t["id"] == topic_id:
                return set(t["kb"])
    raise ApiError("Nie ma takiego tematu na ścieżce.", 404)


def _card_out(c: dict, cs: dict | None, at: float) -> dict:
    out = {k: c.get(k) for k in ("id", "deck", "q", "a", "kb", "tags", "source", "kind", "article")}
    out["is_new"] = cs is None
    out["state"] = {k: cs.get(k) for k in ("ef", "reps", "interval", "due", "last", "lapses", "reviews")} if cs else None
    out["preview"] = preview(cs, at)
    if c.get("kb"):
        a = _kb_map().get(c["kb"])
        out["kb_title"] = a.get("title") if a else None
        if not a:
            out["kb"] = None
    return out


def due_cards(deck: str | None = None, topic: str | None = None, limit: int = 30, extra_new: int = 0,
              at: float | None = None) -> dict:
    at = now() if at is None else at
    hand_decks = {d["id"] for d in _load_hand()["decks"]}
    cards = all_cards()
    if deck:
        known = {d["id"] for d in all_decks()}
        if deck not in known:
            raise ApiError("Nie ma takiej talii.", 404)
        cards = [c for c in cards if c["deck"] == deck]
    if topic:
        kbs = _topic_kb(topic)
        cards = [c for c in cards if c.get("kb") in kbs]
    with _fc_lock:
        st = _fc_state()
    states = st["cards"]
    reviews = sorted((c for c in cards if c["id"] in states and states[c["id"]].get("due", 0) <= at),
                     key=lambda c: states[c["id"]]["due"])
    new_pool = [c for c in cards if c["id"] not in states]
    if not deck and not topic:
        new_pool = [c for c in new_pool if c["deck"] in hand_decks]   # nowe z talii z bazy — tylko na życzenie
    new_pool.sort(key=lambda c: (0 if c["deck"] in hand_decks else 1, c["order"]))
    new_left = _new_left(st, at) + max(0, int(extra_new or 0))
    new = new_pool[:new_left]
    limit = max(1, min(int(limit or 30), 200))
    queue = reviews[:limit]
    queue += new[:max(0, limit - len(queue))]
    learning_soon = sum(1 for c in cards if c["id"] in states and at < states[c["id"]].get("due", 0) <= at + RELEARN_S)
    return {"cards": [_card_out(c, states.get(c["id"]), at) for c in queue],
            "counts": {"due": len(reviews), "new": min(len(new_pool), new_left), "new_available": len(new_pool),
                       "new_left_today": new_left, "total": len(cards), "learning_soon": learning_soon},
            "deck": deck, "topic": topic}


def _streak_days(days: dict, today: date) -> dict:
    active = {date.fromisoformat(d) for d, v in days.items() if (v or {}).get("reviews")}
    if not active:
        return {"current": 0, "longest": 0, "active_today": False}
    ds = sorted(active)
    longest = run = 1
    for a, b in zip(ds, ds[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        longest = max(longest, run)
    cur = 0
    d = today if today in active else today - timedelta(days=1)
    while d in active:
        cur += 1
        d -= timedelta(days=1)
    return {"current": cur, "longest": longest, "active_today": today in active}


def decks_summary(at: float | None = None) -> dict:
    at = now() if at is None else at
    with _fc_lock:
        st = _fc_state()
    states = st["cards"]
    hand_decks = {d["id"] for d in _load_hand()["decks"]}
    per: dict[str, dict] = {}
    for c in all_cards():
        d = per.setdefault(c["deck"], {"total": 0, "new": 0, "due": 0, "learned": 0, "mature": 0})
        d["total"] += 1
        cs = states.get(c["id"])
        if cs is None:
            d["new"] += 1
            continue
        if cs.get("due", 0) <= at:
            d["due"] += 1
        if cs.get("reps", 0) >= 1:
            d["learned"] += 1
        if cs.get("interval", 0) >= 21:
            d["mature"] += 1
    decks = []
    for d in all_decks():
        decks.append(dict(d, **per.get(d["id"], {"total": 0, "new": 0, "due": 0, "learned": 0, "mature": 0})))
    today = _today(at)
    day = today.isoformat()
    last30 = [(today - timedelta(days=i)).isoformat() for i in range(30)]
    rev30 = sum(int((st["days"].get(k) or {}).get("reviews", 0)) for k in last30)
    ok30 = sum(int((st["days"].get(k) or {}).get("correct", 0)) for k in last30)
    new_left = _new_left(st, at)
    due_reviews = sum(d["due"] for d in decks)
    new_hand = sum(d["new"] for d in decks if d["id"] in hand_decks)
    return {
        "decks": decks,
        "totals": {"cards": sum(d["total"] for d in decks), "hand_cards": sum(d["total"] for d in decks if not d["auto"]),
                   "auto_cards": sum(d["total"] for d in decks if d["auto"]), "due": due_reviews,
                   "new_today": min(new_left, new_hand), "new_left_today": new_left,
                   "due_today": due_reviews + min(new_left, new_hand),
                   "learned": sum(d["learned"] for d in decks), "mature": sum(d["mature"] for d in decks)},
        "stats": {"streak": _streak_days(st["days"], today), "today": st["days"].get(day) or {"reviews": 0, "correct": 0, "new": 0},
                  "reviews_total": sum(int((v or {}).get("reviews", 0)) for v in st["days"].values()),
                  "retention_30d": round(ok30 / rev30, 3) if rev30 else None, "reviews_30d": rev30,
                  "history": [{"date": k, "reviews": int((st["days"].get(k) or {}).get("reviews", 0))} for k in reversed(last30)]},
        "grades": [{"grade": g, "label": m["label"]} for g, m in GRADES.items()],
        "new_per_day": _new_per_day(),
        "errors": _load_hand()["errors"][:20],
    }


def review(card_id: str, grade, at: float | None = None) -> dict:
    at = now() if at is None else at
    try:
        grade = int(grade)
    except (TypeError, ValueError):
        raise ApiError("Ocena musi być liczbą 1–4.")
    if grade not in GRADES:
        raise ApiError("Ocena musi być liczbą 1–4 (Nie wiem / Trudne / Dobrze / Łatwe).")
    card = _cards_by_id().get(str(card_id or ""))
    if not card:
        raise ApiError("Nie ma takiej fiszki.", 404)
    with _fc_lock:
        st = _fc_state()
        prev = st["cards"].get(card["id"])
        new_st = sm2(prev, grade, at)
        st["cards"][card["id"]] = new_st
        day = _today(at).isoformat()
        dd = st["days"].setdefault(day, {"reviews": 0, "correct": 0, "new": 0})
        dd["reviews"] = int(dd.get("reviews", 0)) + 1
        dd["correct"] = int(dd.get("correct", 0)) + (1 if grade >= 2 else 0)
        if prev is None:
            dd["new"] = int(dd.get("new", 0)) + 1
        if len(st["days"]) > 800:
            for k in sorted(st["days"])[:-800]:
                st["days"].pop(k, None)
        store.set("flashcards_state", st)
        streak = _streak_days(st["days"], _today(at))
    journal.log_event("flashcard_review", {"deck": card["deck"], "grade": grade, "kb": card.get("kb")})
    return {"card_id": card["id"], "state": new_st, "next": interval_label(new_st, at), "preview": preview(new_st, at),
            "today": dd, "streak": streak}


def handle_fc_decks(_q: dict) -> dict:
    return decks_summary()


def handle_fc_due(q: dict) -> dict:
    def num(k, d):
        try:
            return int(q.get(k) or d)
        except (TypeError, ValueError):
            return d
    return due_cards(q.get("deck") or None, q.get("topic") or None, num("limit", 30), num("extra_new", 0))


def handle_fc_review(body: dict) -> dict:
    return review(body.get("card_id"), body.get("grade"))


def handle_fc_reset(body: dict) -> dict:
    """Usuwa postęp jednej karty albo całej talii (nie dotyka statystyk dni)."""
    cid, deck = body.get("card_id"), body.get("deck")
    if not cid and not deck:
        raise ApiError("Podaj card_id albo deck.")
    with _fc_lock:
        st = _fc_state()
        if cid:
            removed = 1 if st["cards"].pop(str(cid), None) is not None else 0
        else:
            ids = {c["id"] for c in all_cards() if c["deck"] == deck}
            removed = sum(1 for i in list(st["cards"]) if i in ids and st["cards"].pop(i, None) is not None)
        store.set("flashcards_state", st)
    return {"removed": removed}


def card_learning_by_kb() -> dict[str, dict]:
    """{kb_id: {"total": n, "learned": k}} — do postępu na ścieżce nauki."""
    st = _fc_state()["cards"]
    out: dict[str, dict] = {}
    for c in all_cards():
        k = c.get("kb")
        if not k:
            continue
        d = out.setdefault(k, {"total": 0, "learned": 0})
        d["total"] += 1
        cs = st.get(c["id"])
        if cs and cs.get("reps", 0) >= 1:
            d["learned"] += 1
    return out


# =============================================================================================================
#                                              ŚCIEŻKA NAUKI
# =============================================================================================================
_rm_cache: dict = {"sig": None, "data": None}
_rm_lock = threading.Lock()


def roadmap_data() -> dict:
    """data/roadmap.json z odfiltrowanymi nieistniejącymi artykułami i skompilowanymi wzorcami rund."""
    t_now = time.monotonic()
    if _rm_cache["data"] is not None and t_now - _rm_cache.get("checked", -1e9) < SIG_TTL:
        return _rm_cache["data"]
    try:
        stt = ROADMAP_FILE.stat()
        fsig = (str(ROADMAP_FILE), stt.st_mtime_ns, stt.st_size)
    except OSError:
        fsig = None
    sig = (fsig, _kb_sig())
    with _rm_lock:
        _rm_cache["checked"] = t_now
        if _rm_cache["sig"] == sig and _rm_cache["data"] is not None:
            return _rm_cache["data"]
        try:
            raw = json.loads(ROADMAP_FILE.read_text(encoding="utf-8")) if fsig else {}
        except (OSError, ValueError):
            raw = {}
        stages, order, missing = [], 0, []
        kbm = _kb_map()
        for si, s in enumerate(raw.get("stages") or []):
            topics = []
            for t in s.get("topics") or []:
                kbs = [k for k in t.get("kb") or [] if k in kbm]
                missing += [k for k in t.get("kb") or [] if k not in kbm]
                pats = []
                for m in t.get("match") or []:
                    m = _norm(m)
                    if m:
                        pats.append((m, re.compile(r"(?<![a-z0-9])" + re.escape(m))))
                has_tasks = t.get("tasks", True) is not False and bool(pats or t.get("algo"))
                topics.append({"id": t["id"], "title": t.get("title", t["id"]), "icon": t.get("icon") or "circle-check",
                               "description": t.get("description", ""), "kb": kbs, "patterns": pats,
                               "algo": list(t.get("algo") or []), "has_tasks": has_tasks,
                               "target": int(t.get("target") or 0) if has_tasks else 0,
                               "stage": s["id"], "stage_index": si, "order": order})
                order += 1
            stages.append({"id": s["id"], "title": s.get("title", s["id"]), "subtitle": s.get("subtitle", ""),
                           "icon": s.get("icon") or "flag", "description": s.get("description", ""),
                           "criteria": s.get("criteria") or [], "topics": topics})
        data = {"stages": stages, "weights": raw.get("weights") or {"tasks": 0.6, "read": 0.25, "cards": 0.15},
                "weights_no_tasks": raw.get("weights_no_tasks") or {"read": 0.6, "cards": 0.4},
                "read_target": int(raw.get("read_target") or 3), "cards_target": int(raw.get("cards_target") or 10),
                "unlock_threshold": float(raw.get("unlock_threshold") or 0.45), "missing_kb": sorted(set(missing)),
                "topics": {t["id"]: t for s in stages for t in s["topics"]}, "round_cache": {}}
        _rm_cache.update(sig=sig, data=data)
        return data


def topics_for_round(round_name: str | None) -> list[str]:
    """Tematy ścieżki pasujące do nazwy rundy („Dzień 7 - Toposort i dynamiki na grafach” → [toposort]).

    Wszystkie dopasowania od początku słowa; dopasowanie zawarte w dłuższym dopasowaniu INNEGO tematu jest
    odrzucane („drzew” w „drzewa przedziałowe”, „sortowan” w „sortowanie topologiczne”)."""
    txt = _norm(round_name or "")
    if not txt:
        return []
    rm = roadmap_data()
    cached = rm["round_cache"].get(txt)
    if cached is not None:
        return list(cached)
    hits = []
    for t in rm["topics"].values():
        for m, rx in t["patterns"]:
            for mm in rx.finditer(txt):
                hits.append((mm.start(), mm.start() + len(m), t["id"], t["order"]))
    keep = []
    for a in hits:
        contained = any(b[2] != a[2] and b[0] <= a[0] and a[1] <= b[1] and (b[1] - b[0]) > (a[1] - a[0]) for b in hits)
        if not contained:
            keep.append(a)
    seen, out = set(), []
    for a in sorted(keep, key=lambda x: (x[3], x[0])):
        if a[2] not in seen:
            seen.add(a[2])
            out.append(a[2])
    if len(rm["round_cache"]) < 5000:
        rm["round_cache"][txt] = tuple(out)
    return out


def _program_tags() -> dict[str, list[str]]:
    """Identyfikatory algorytmów z modułu „Moje programy” (tylko gdy jego indeks jest już zbudowany — nie blokuje)."""
    mod = sys.modules.get("oiforge.tools.programs")
    if not mod or getattr(mod, "_engine", None) is None:
        return {}
    lock = getattr(mod, "_engine_lock", None)
    if lock is not None and lock.locked():
        return {}
    try:
        return {p["id"]: list(p.get("tag_ids") or []) for p in mod.list_programs()}
    except Exception:  # noqa: BLE001
        return {}


def topic_assignments(probs: list[dict] | None = None) -> dict:
    """Przypisuje unikalne zadania ucznia do tematów: najpierw po nazwie rundy, potem (dla rund ogólnych,
    np. „Warsztaty 2”) po rozpoznanych algorytmach z modułu „Moje programy”."""
    probs = journal.solved_problems() if probs is None else probs
    rm = roadmap_data()
    algo_map: dict[str, list[str]] = {}
    for t in rm["topics"].values():
        for a in t["algo"]:
            algo_map.setdefault(a, []).append(t["id"])
    tags = None
    by_topic: dict[str, list[dict]] = {t: [] for t in rm["topics"]}
    unmatched = 0
    for p in probs:
        tids = topics_for_round(p.get("round"))
        via = "round"
        if not tids:
            if tags is None:
                tags = _program_tags()
            tl = tags.get(p["id"]) or tags.get(p.get("last_id")) or []
            tids = list(dict.fromkeys(t for a in tl for t in algo_map.get(a, [])))[:2]
            via = "algo"
        if not tids:
            unmatched += 1
        for tid in tids:
            by_topic[tid].append(dict(p, via=via))
    return {"by_topic": by_topic, "unmatched": unmatched, "tags_used": bool(tags)}


def _topic_row(t: dict, solved: list[dict], read: dict, cards: dict, rm: dict) -> dict:
    kbm = _kb_map()
    read_ids = [k for k in t["kb"] if read.get(k)]
    read_target = max(1, min(len(t["kb"]), rm["read_target"])) if t["kb"] else 1
    r_ratio = min(1.0, len(read_ids) / read_target) if t["kb"] else 0.0
    c_total = sum((cards.get(k) or {}).get("total", 0) for k in t["kb"])
    c_learned = sum((cards.get(k) or {}).get("learned", 0) for k in t["kb"])
    c_target = max(1, min(c_total, rm["cards_target"])) if c_total else 1
    c_ratio = min(1.0, c_learned / c_target) if c_total else 0.0
    n_solved = len(solved)
    if t["has_tasks"]:
        tr = min(1.0, n_solved / t["target"]) if t["target"] else (1.0 if n_solved else 0.0)
        w = rm["weights"]
        prog = w.get("tasks", 0.6) * tr + w.get("read", 0.25) * r_ratio + w.get("cards", 0.15) * c_ratio
    else:
        tr = None
        w = rm["weights_no_tasks"]
        prog = w.get("read", 0.6) * r_ratio + w.get("cards", 0.4) * c_ratio
    prog = round(min(1.0, prog), 4)
    status = "done" if prog >= 0.85 else ("progress" if prog >= 0.35 else ("started" if prog > 0 else "new"))
    crit = []
    if t["has_tasks"]:
        crit.append({"id": "tasks", "label": f"Rozwiąż {t['target']} {_plural(t['target'], 'zadanie', 'zadania', 'zadań')} z tematu",
                     "value": min(n_solved, t["target"]), "target": t["target"], "done": n_solved >= t["target"]})
    if t["kb"]:
        crit.append({"id": "read", "label": f"Przeczytaj {read_target} {_plural(read_target, 'artykuł', 'artykuły', 'artykułów')}",
                     "value": min(len(read_ids), read_target), "target": read_target, "done": len(read_ids) >= read_target})
    if c_total:
        crit.append({"id": "cards", "label": f"Opanuj {c_target} {_plural(c_target, 'fiszkę', 'fiszki', 'fiszek')}",
                     "value": min(c_learned, c_target), "target": c_target, "done": c_learned >= c_target})
    return {"id": t["id"], "title": t["title"], "icon": t["icon"], "description": t["description"], "stage": t["stage"],
            "order": t["order"], "has_tasks": t["has_tasks"], "target": t["target"], "solved": n_solved,
            "ratio": round(tr, 4) if tr is not None else round(prog, 4), "progress": prog, "status": status,
            "parts": {"tasks": tr, "read": round(r_ratio, 4), "cards": round(c_ratio, 4)},
            "read": len(read_ids), "read_target": read_target, "cards_total": c_total, "cards_learned": c_learned,
            "cards_target": c_target, "criteria": crit,
            "kb": [{"id": k, "title": (kbm.get(k) or {}).get("title", k), "level": (kbm.get(k) or {}).get("level"),
                    "read": bool(read.get(k))} for k in t["kb"]],
            "tasks": [{"id": p["id"], "problem_name": p["problem_name"], "problem_short": p.get("problem_short"),
                       "contest_name": p.get("contest_name"), "round": p.get("round"), "time": p.get("last_time"),
                       "first_time": p.get("first_time"), "via": p.get("via")}
                      for p in sorted(solved, key=lambda x: x.get("last_time") or "", reverse=True)]}


def roadmap(probs: list[dict] | None = None) -> dict:
    rm = roadmap_data()
    assign = topic_assignments(probs)
    read = journal.kb_read_ids()
    try:
        cards = card_learning_by_kb()
    except Exception:  # noqa: BLE001
        cards = {}
    stages_out, prev_prog, prev_done = [], None, True
    for si, s in enumerate(rm["stages"]):
        rows = [_topic_row(t, assign["by_topic"].get(t["id"], []), read, cards, rm) for t in s["topics"]]
        prog = round(sum(r["progress"] for r in rows) / len(rows), 4) if rows else 0.0
        has_evidence = any(r["solved"] or r["read"] or r["cards_learned"] for r in rows)
        unlocked = si == 0 or (prev_prog is not None and prev_prog >= rm["unlock_threshold"]) or has_evidence
        stages_out.append({"id": s["id"], "title": s["title"], "subtitle": s["subtitle"], "icon": s["icon"],
                           "description": s["description"], "criteria": s["criteria"], "progress": prog,
                           "unlocked": unlocked, "done": prog >= 0.85, "topics": rows,
                           "done_topics": sum(1 for r in rows if r["status"] == "done"), "solved": sum(r["solved"] for r in rows)})
        prev_prog = prog
    for st in stages_out:
        for r in st["topics"]:
            r["unlocked"] = st["unlocked"]
    all_rows = [r for st in stages_out for r in st["topics"]]
    current = next((st for st in stages_out if st["unlocked"] and not st["done"]), stages_out[-1] if stages_out else None)
    nxt = None
    if current:
        cands = [r for r in current["topics"] if r["status"] != "done"]
        if cands:
            nxt = min(cands, key=lambda r: (r["progress"], r["order"]))
    overall = round(sum(r["progress"] for r in all_rows) / len(all_rows), 4) if all_rows else 0.0
    return {"stages": stages_out,
            "overall": {"progress": overall, "done_topics": sum(1 for r in all_rows if r["status"] == "done"),
                        "total_topics": len(all_rows), "current_stage": current["id"] if current else None,
                        "current_stage_title": current["title"] if current else None,
                        "solved_matched": sum(r["solved"] for r in all_rows), "unmatched_tasks": assign["unmatched"],
                        "tags_used": assign["tags_used"], "read_articles": len(read)},
            "next": {"id": nxt["id"], "title": nxt["title"], "stage": nxt["stage"]} if nxt else None,
            "missing_kb": rm["missing_kb"]}


def topic_progress_rows(by_topic: dict) -> list[dict]:
    """Dla dziennika: wiersze tematów (z odblokowaniem etapów) na podstawie gotowego przypisania zadań."""
    rm = roadmap_data()
    read = journal.kb_read_ids()
    try:
        cards = card_learning_by_kb()
    except Exception:  # noqa: BLE001
        cards = {}
    out, prev_prog = [], None
    for si, s in enumerate(rm["stages"]):
        rows = [_topic_row(t, by_topic.get(t["id"], []), read, cards, rm) for t in s["topics"]]
        prog = sum(r["progress"] for r in rows) / len(rows) if rows else 0.0
        unlocked = si == 0 or (prev_prog is not None and prev_prog >= rm["unlock_threshold"]) or \
            any(r["solved"] or r["read"] for r in rows)
        for r in rows:
            r["unlocked"] = unlocked
            r["stage_title"] = s["title"]
            unread = [k["id"] for k in r["kb"] if not k["read"]]
            r["kb_first"] = (unread or [k["id"] for k in r["kb"]] or [None])[0]
            r.pop("kb", None)
            r["tasks"] = r["tasks"][:8]
        out += rows
        prev_prog = prog
    return out


def handle_roadmap(_q: dict) -> dict:
    return roadmap()


# =============================================================================================================
#                                              ZADANIE DNIA
# =============================================================================================================
def _ago(days: int) -> str:
    if days < 14:
        return f"{days} {_plural(days, 'dzień', 'dni', 'dni')} temu"
    if days < 60:
        w = days // 7
        return f"{w} {_plural(w, 'tydzień', 'tygodnie', 'tygodni')} temu"
    m = days // 30
    if m < 12:
        return f"{m} {_plural(m, 'miesiąc', 'miesiące', 'miesięcy')} temu"
    y = days // 365
    return f"ponad {y} {_plural(y, 'rok', 'lata', 'lat')} temu"


def daily(day: date | None = None, probs: list[dict] | None = None) -> dict:
    """Deterministyczna porcja treningu na dany dzień: najsłabszy temat, stare zadanie do powtórki, fiszki."""
    day = day or _today()
    probs = journal.solved_problems() if probs is None else probs
    rng = random.Random(_seed("oiforge-daily", day.isoformat()))
    rmap = roadmap(probs)
    out: dict = {"date": day.isoformat(), "topic": None, "task": None, "flashcards": {"due": 0}}

    # --- temat dnia: najsłabszy z odblokowanych etapów (losowo spośród 3 najsłabszych — codziennie inny)
    rows = [r for st in rmap["stages"] if st["unlocked"] for r in st["topics"] if r["status"] != "done"]
    if rows:
        rows.sort(key=lambda r: (round(r["progress"], 2), r["order"]))
        pick = rows[:3][rng.randrange(min(3, len(rows)))]
        unread = [k for k in pick["kb"] if not k["read"]]
        if unread:
            art = unread[0]
        elif pick["kb"]:
            art = pick["kb"][rng.randrange(len(pick["kb"]))]
        else:
            art = None
        stage = next(st for st in rmap["stages"] if st["id"] == pick["stage"])
        bits = []
        if pick["has_tasks"]:
            bits.append(f"{pick['solved']}/{pick['target']} zadań")
        bits.append(f"{pick['read']}/{pick['read_target']} przeczytanych artykułów")
        if pick["cards_total"]:
            bits.append(f"{pick['cards_learned']}/{pick['cards_target']} fiszek")
        out["topic"] = {"id": pick["id"], "title": pick["title"], "kb": art["id"] if art else None,
                        "kb_title": art["title"] if art else None, "stage": stage["title"],
                        "progress": pick["progress"],
                        "reason": f"Najsłabszy temat na Twojej ścieżce ({stage['title']}): " + ", ".join(bits) + "."}

    # --- zadanie do ponownego rozwiązania: dawno rozwiązane, najlepiej ze słabszego tematu
    pool = []
    weak = {r["id"]: r["progress"] for st in rmap["stages"] for r in st["topics"]}
    for p in probs:
        last = p.get("last")
        if not isinstance(last, datetime):
            continue
        age = (day - last.date()).days
        if age < 0:
            continue
        pool.append((p, age))
    for min_age in (45, 21, 7, 0):
        cand = [(p, a) for p, a in pool if a >= min_age]
        if cand:
            break
    else:
        cand = []
    if cand:
        weights = []
        for p, a in cand:
            tids = topics_for_round(p.get("round"))
            wk = min((weak.get(t, 1.0) for t in tids), default=0.8)
            weights.append((1.0 + min(a, 365) / 60.0) * (1.6 - wk))
        p, a = rng.choices(cand, weights=weights, k=1)[0]
        tids = topics_for_round(p.get("round"))
        tname = next((r["title"] for st in rmap["stages"] for r in st["topics"] if tids and r["id"] == tids[0]), None)
        reason = f"Rozwiązane {_ago(a)}" + (f" (temat: {tname})" if tname else (f" (runda: {p['round']})" if p.get("round") else "")) + \
            ". Napisz je od zera bez zaglądania do starego kodu — potem porównaj."
        out["task"] = {"id": p["id"], "problem_name": p["problem_name"], "problem_short": p.get("problem_short"),
                       "contest_name": p.get("contest_name"), "round": p.get("round"), "solved_at": p.get("last_time"),
                       "days_ago": a, "url": p.get("url"), "topic": tids[0] if tids else None, "reason": reason}

    # --- fiszki
    try:
        ds = decks_summary()
        out["flashcards"] = {"due": ds["totals"]["due_today"], "reviews": ds["totals"]["due"], "new": ds["totals"]["new_today"],
                             "streak": ds["stats"]["streak"]["current"]}
    except Exception:  # noqa: BLE001
        out["flashcards"] = {"due": 0}
    out["overall"] = rmap["overall"]
    return out


def daily_cached() -> dict:
    """Zadanie dnia na dziś — temat i zadanie są zapamiętywane w store, żeby nie zmieniały się w ciągu dnia
    (np. po przeczytaniu artykułu). Liczba fiszek jest zawsze aktualna."""
    today = _today().isoformat()
    cached = store.get("training_daily")
    if isinstance(cached, dict) and cached.get("date") == today and (cached.get("topic") or cached.get("task")):
        out = dict(cached)
        try:
            ds = decks_summary()
            out["flashcards"] = {"due": ds["totals"]["due_today"], "reviews": ds["totals"]["due"],
                                 "new": ds["totals"]["new_today"], "streak": ds["stats"]["streak"]["current"]}
        except Exception:  # noqa: BLE001
            pass
        out["cached"] = True
        return out
    out = daily()
    try:
        store.set("training_daily", {k: out.get(k) for k in ("date", "topic", "task", "overall")})
    except (OSError, ValueError):
        pass
    return out


def handle_daily(q: dict) -> dict:
    if q.get("date"):
        try:
            d = date.fromisoformat(str(q["date"])[:10])
        except ValueError:
            raise ApiError("Niepoprawna data (RRRR-MM-DD).")
        return daily(d)
    return daily_cached()


ROUTES = {
    "POST /api/contest/start": contest_start,
    "GET /api/contest/state": lambda _q: contest_state(),
    "POST /api/contest/update": contest_update,
    "POST /api/contest/stop": contest_stop,
    "GET /api/contest/history": handle_contest_history,
    "POST /api/contest/history/delete": handle_contest_history_delete,
    "GET /api/flashcards/decks": handle_fc_decks,
    "GET /api/flashcards/due": handle_fc_due,
    "POST /api/flashcards/review": handle_fc_review,
    "POST /api/flashcards/reset": handle_fc_reset,
    "GET /api/roadmap": handle_roadmap,
    "GET /api/training/daily": handle_daily,
}

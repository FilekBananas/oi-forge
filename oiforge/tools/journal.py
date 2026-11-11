"""Dziennik błędów i postępów (OI Forge).

Bez AI — czysta statystyka na zdarzeniach zapisywanych lokalnie (JSONL) i na danych ucznia
(`user_data/programs/index.json`).

Python API dla innych modułów (wywołuj w try/except ImportError):
    log_event(kind: str, data: dict) -> None     # szybkie, bezpieczne wątkowo, NIGDY nie rzuca wyjątku
    events(kinds=None, since=None) -> list[dict] # [{"t": epoch, "ts": "YYYY-MM-DDTHH:MM:SS", "kind", "data"}]
    stats(days=30) -> dict                       # ranking błędów, trendy, liczniki
    progress(index_path=None) -> dict            # rozwiązane zadania, tematy, heatmapa, serie
    solved_problems(index_path=None) -> list     # unikalne zadania ucznia (pierwsze/ostatnie rozwiązanie)
    kb_read_ids() -> dict[str, int]              # przeczytane artykuły (z kb_view) → liczba wejść

Rodzaje zdarzeń (kind): checker_issue {rule, severity[, title, kb]}, tester_result {ok, tests, verdict},
compile_error {title}, run {verdict}, kb_view {id}, assistant_q {q}; dowolne inne `[a-z0-9_]` też są przyjmowane.

HTTP: POST /api/journal/log {kind, data} · GET /api/journal/stats?days=30 · GET /api/journal/progress ·
      GET /api/journal/events?kind=&limit=
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config
from ..server_api import ApiError

FILE_NAME = "journal.jsonl"
OLD_NAME = "journal.1.jsonl"
MAX_BYTES = 16 * 1024 * 1024        # po przekroczeniu: rotacja do journal.1.jsonl (zostaje jedna stara kopia)
MAX_STR = 400
MAX_ITEMS = 30
KNOWN_KINDS = ("checker_issue", "tester_result", "compile_error", "run", "kb_view", "assistant_q")
_KIND_RE = re.compile(r"[^a-z0-9_]+")

_write_lock = threading.Lock()
_read_lock = threading.Lock()
_path_override: Path | None = None
_cache: dict = {"key": None, "offset": 0, "events": [], "old_sig": None, "old_events": []}


# ============================================================================================ zapis
def journal_path() -> Path:
    return Path(_path_override) if _path_override else Path(config.USER_DIR) / FILE_NAME


def set_path(path) -> None:
    """Zmienia plik dziennika (testy). None = domyślny w `config.USER_DIR`."""
    global _path_override
    _path_override = Path(path) if path else None
    with _read_lock:
        _cache.update(key=None, offset=0, events=[], old_sig=None, old_events=[])


def _clean(v, depth: int = 0):
    """Sprowadza dane do bezpiecznego, małego JSON-a (obcina długie napisy i listy)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if -(2 ** 63) <= v < 2 ** 63 else str(v)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return v[:MAX_STR]
    if depth >= 3:
        return str(v)[:MAX_STR]
    if isinstance(v, dict):
        out = {}
        for i, (k, x) in enumerate(v.items()):
            if i >= MAX_ITEMS:
                break
            out[str(k)[:40]] = _clean(x, depth + 1)
        return out
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_clean(x, depth + 1) for x in list(v)[:MAX_ITEMS]]
    try:
        return str(v)[:MAX_STR]
    except Exception:  # noqa: BLE001
        return None


def _norm_kind(kind) -> str:
    try:
        k = _KIND_RE.sub("_", str(kind or "").strip().lower()).strip("_")[:32]
    except Exception:  # noqa: BLE001
        k = ""
    return k or "other"


def log_event(kind: str, data: dict | None = None) -> None:
    """Dopisuje zdarzenie do dziennika. Szybkie (jedno dopisanie linii), bezpieczne wątkowo, nigdy nie rzuca."""
    try:
        now = time.time()
        if not isinstance(data, dict):
            data = {} if data is None else {"value": data}
        rec = {"t": round(now, 3), "ts": datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S"),
               "kind": _norm_kind(kind), "data": _clean(data)}
        line = json.dumps(rec, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
        p = journal_path()
        with _write_lock:
            try:
                if p.stat().st_size > MAX_BYTES:
                    os.replace(p, p.with_name(OLD_NAME))
            except OSError:
                pass
            try:
                f = open(p, "a", encoding="utf-8", newline="\n")
            except FileNotFoundError:
                p.parent.mkdir(parents=True, exist_ok=True)
                f = open(p, "a", encoding="utf-8", newline="\n")
            with f:
                f.write(line)
    except Exception:  # noqa: BLE001 - dziennik nigdy nie może przeszkodzić innym modułom
        pass


def log(kind: str, **data) -> None:
    """Skrót: journal.log("run", verdict="OK")."""
    log_event(kind, data)


# ============================================================================================ odczyt
def _parse_lines(raw: bytes) -> list[dict]:
    out = []
    for ln in raw.split(b"\n"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict) and isinstance(rec.get("t"), (int, float)) and rec.get("kind"):
            if not isinstance(rec.get("data"), dict):
                rec["data"] = {}
            out.append(rec)
    return out


def _read_all() -> list[dict]:
    """Wszystkie zdarzenia (stara kopia + bieżący plik). Czyta przyrostowo — tylko nowe bajty."""
    p = journal_path()
    old = p.with_name(OLD_NAME)
    with _read_lock:
        try:
            st = old.stat()
            osig = (st.st_size, st.st_mtime_ns)
        except OSError:
            osig = None
        if osig != _cache["old_sig"]:
            try:
                _cache["old_events"] = _parse_lines(old.read_bytes()) if osig else []
            except OSError:
                _cache["old_events"] = []
            _cache["old_sig"] = osig
        try:
            st = p.stat()
            size = st.st_size
            ino = (str(p), getattr(st, "st_ino", 0), getattr(st, "st_ctime_ns", 0) if os.name == "nt" else 0)
        except OSError:
            _cache.update(key=None, offset=0, events=[])
            return list(_cache["old_events"])
        if _cache["key"] != ino or size < _cache["offset"]:
            _cache.update(key=ino, offset=0, events=[])
        if size > _cache["offset"]:
            try:
                with open(p, "rb") as f:
                    f.seek(_cache["offset"])
                    raw = f.read(size - _cache["offset"])
            except OSError:
                raw = b""
            cut = raw.rfind(b"\n")
            if cut >= 0:                      # niepełną ostatnią linię doczytamy następnym razem
                _cache["events"].extend(_parse_lines(raw[:cut + 1]))
                _cache["offset"] += cut + 1
        return _cache["old_events"] + _cache["events"]


def events(kinds=None, since: float | None = None, limit: int | None = None) -> list[dict]:
    if isinstance(kinds, str):
        kinds = {kinds}
    kinds = set(kinds) if kinds else None
    out = [e for e in _read_all() if (kinds is None or e["kind"] in kinds) and (since is None or e["t"] >= since)]
    if limit:
        out = out[-int(limit):]
    return out


def kb_read_ids() -> dict[str, int]:
    """Artykuły bazy wiedzy otwarte przez ucznia (zdarzenia kb_view) → liczba wejść."""
    c: Counter = Counter()
    for e in events("kb_view"):
        aid = e["data"].get("id") or e["data"].get("article")
        if isinstance(aid, str) and aid:
            c[aid] += 1
    return dict(c)


# ============================================================================================ pomocnicze
def _day(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(s).lower()).strip()


def _kb_title(aid: str | None) -> str | None:
    if not aid:
        return None
    try:
        from . import training  # noqa: PLC0415
        a = training._kb_map().get(aid)
        return a.get("title") if a else None
    except Exception:  # noqa: BLE001
        try:
            from ..core import kb  # noqa: PLC0415
            a = kb.get(aid)
            return a.get("title") if a else None
        except Exception:  # noqa: BLE001
            return None


def _rules_meta() -> dict:
    """Katalog reguł Sprawdzarki (tytuł, waga, linki do bazy) — jeśli moduł istnieje."""
    try:
        from . import _checker_rules as R  # noqa: PLC0415
        return getattr(R, "RULES", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


_kb_guess_cache: dict[str, str | None] = {}
GUESS_MIN_SCORE = 14.0
GUESS_CATEGORIES = {"Pułapki", "C++ język", "Liczby i typy", "Wejście/wyjście", "OI praktyka", "Narzędzia", "STL kontenery",
                    "STL algorytmy"}


def _kb_guess(text: str) -> str | None:
    """Najlepiej pasujący artykuł bazy wiedzy do tytułu błędu (BM25 z rdzenia), z cache."""
    key = _norm(text)[:120]
    if not key:
        return None
    if key in _kb_guess_cache:
        return _kb_guess_cache[key]
    aid = None
    try:
        srv = sys.modules.get("oiforge.server")
        if srv is not None and hasattr(srv, "kb_index"):
            res = srv.kb_index().search(text, k=4)
            top = res[0].get("score", 0) if res else 0
            if top >= GUESS_MIN_SCORE:
                # wśród prawie równie dobrych wyników wybierz artykuł o błędach/języku, a nie o algorytmie
                close = [r for r in res if r.get("score", 0) >= 0.85 * top]
                pref = [r for r in close if (r.get("payload") or {}).get("category") in GUESS_CATEGORIES]
                aid = (pref or ([res[0]] if len(close) == 1 else [None]))[0]
                aid = aid["id"] if aid else None
    except Exception:  # noqa: BLE001
        aid = None
    if len(_kb_guess_cache) > 2000:
        _kb_guess_cache.clear()
    _kb_guess_cache[key] = aid
    return aid


SOURCE_LABEL = {"checker": "Sprawdzarka", "compile": "Kompilator", "runtime": "Wykonanie", "tester": "Testerka"}
VERDICT_TEXT = {
    "RE": ("Błąd wykonania (RE)", "pit-out-of-bounds"),
    "TLE": ("Przekroczony limit czasu (TLE)", "oi-complexity-limits"),
    "MLE": ("Przekroczony limit pamięci (MLE)", "oi-memory"),
    "WA": ("Zła odpowiedź (WA)", "oi-stress-test"),
}


def _trend(recent: int, prev: int) -> str:
    if recent > prev:
        return "up"
    if recent < prev:
        return "down"
    return "flat"


# ============================================================================================ statystyki dziennika
def stats(days: int = 30, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    days = max(1, min(int(days or 30), 3650))
    evs = _read_all()
    since = now - days * 86400
    wk1, wk2 = now - 7 * 86400, now - 14 * 86400
    rules = None

    by_kind_all: Counter = Counter(e["kind"] for e in evs)
    win = [e for e in evs if e["t"] >= since]
    by_kind: Counter = Counter(e["kind"] for e in win)

    # --- ranking błędów (z całego okna)
    errs: dict[tuple, dict] = {}

    def add_err(key, source, title, e, severity=None, kb_id=None):
        it = errs.get(key)
        if it is None:
            it = errs[key] = {"key": ":".join(map(str, key)), "source": source, "source_label": SOURCE_LABEL.get(source, source),
                              "title": title, "count": 0, "recent": 0, "prev": 0, "severity": severity, "kb": kb_id,
                              "first": e["t"], "last": e["t"]}
        it["count"] += 1
        it["last"] = max(it["last"], e["t"])
        it["first"] = min(it["first"], e["t"])
        if e["t"] >= wk1:
            it["recent"] += 1
        elif e["t"] >= wk2:
            it["prev"] += 1
        if kb_id and not it["kb"]:
            it["kb"] = kb_id
        if severity and not it["severity"]:
            it["severity"] = severity

    verdicts: Counter = Counter()
    tester = {"sessions": 0, "ok": 0, "failed": 0, "tests": 0, "verdicts": Counter()}
    daily: dict[str, Counter] = defaultdict(Counter)
    kb_views: Counter = Counter()
    questions = []
    for e in win:
        d, k = e["data"], e["kind"]
        day = _day(e["t"])
        daily[day]["events"] += 1
        if k == "checker_issue":
            rid = str(d.get("rule") or d.get("id") or "?")
            if rules is None:
                rules = _rules_meta()
            meta = rules.get(rid)
            title = d.get("title") or (getattr(meta, "title", None)) or rid
            kb_id = d.get("kb") or ((getattr(meta, "kb", None) or [None])[0] if meta else None)
            sev = d.get("severity") or getattr(meta, "severity", None)
            add_err(("checker", rid), "checker", title, e, sev, kb_id)
            daily[day]["errors"] += 1
            daily[day]["checker"] += 1
        elif k == "compile_error":
            title = str(d.get("title") or "Błąd kompilacji").strip()[:160]
            add_err(("compile", _norm(title)[:80]), "compile", title, e, "error", d.get("kb"))
            daily[day]["errors"] += 1
            daily[day]["compile"] += 1
        elif k == "run":
            v = str(d.get("verdict") or "?").upper()
            verdicts[v] += 1
            daily[day]["runs"] += 1
            if v in VERDICT_TEXT and v != "WA":
                t, kb_id = VERDICT_TEXT[v]
                add_err(("runtime", v), "runtime", t, e, "error", kb_id)
                daily[day]["errors"] += 1
        elif k == "tester_result":
            tester["sessions"] += 1
            ok = bool(d.get("ok"))
            tester["ok" if ok else "failed"] += 1
            try:
                n = int(d.get("tests") or 0)
            except (TypeError, ValueError):
                n = 0
            tester["tests"] += max(0, n)
            daily[day]["tests"] += max(0, n)
            daily[day]["tester"] += 1
            v = str(d.get("verdict") or ("OK" if ok else "WA")).upper()
            tester["verdicts"][v] += 1
            if not ok:
                t, kb_id = VERDICT_TEXT.get(v, (f"Testerka: {v}", "oi-stress-test"))
                if v == "WA":
                    t = "Zła odpowiedź na teście losowym (WA)"
                add_err(("tester", v), "tester", t, e, "error", kb_id)
                daily[day]["errors"] += 1
        elif k == "kb_view":
            aid = d.get("id")
            if isinstance(aid, str) and aid:
                kb_views[aid] += 1
                daily[day]["kb"] += 1
        elif k == "assistant_q":
            q = str(d.get("q") or "").strip()
            if q:
                questions.append({"q": q[:200], "t": e["t"], "ts": e.get("ts")})
                daily[day]["questions"] += 1

    ranking = sorted(errs.values(), key=lambda x: (-x["count"], -x["last"]))
    for i, it in enumerate(ranking[:40], 1):
        it["rank"] = i
        it["trend"] = _trend(it["recent"], it["prev"])
        if not it["kb"] and it["source"] in ("compile", "checker"):
            it["kb"] = _kb_guess(it["title"])
        it["kb_title"] = _kb_title(it["kb"])
        if it["kb"] and not it["kb_title"]:
            it["kb"] = None
    ranking = ranking[:40]

    # --- seria dzienna (dla wykresów)
    end = date.fromtimestamp(now)
    start = end - timedelta(days=days)   # dni kalendarzowe — odejmowanie sekund gubiło dzień po zmianie czasu
    series = []
    d0 = start
    while d0 <= end:
        key = d0.isoformat()
        c = daily.get(key, Counter())
        series.append({"date": key, "runs": c["runs"], "tests": c["tests"], "errors": c["errors"], "checker": c["checker"],
                       "compile": c["compile"], "kb": c["kb"], "questions": c["questions"], "events": c["events"]})
        d0 += timedelta(days=1)

    # --- porównanie tydzień do tygodnia
    def cnt(lo, hi, kinds):
        return sum(1 for e in evs if lo <= e["t"] < hi and e["kind"] in kinds)
    err_kinds = {"checker_issue", "compile_error"}
    week = {"errors_now": cnt(wk1, now + 1, err_kinds), "errors_prev": cnt(wk2, wk1, err_kinds),
            "runs_now": cnt(wk1, now + 1, {"run"}), "runs_prev": cnt(wk2, wk1, {"run"})}

    activity = Counter(_day(e["t"]) for e in evs)
    kb_top = [{"id": a, "title": _kb_title(a) or a, "count": n} for a, n in kb_views.most_common(15)]
    questions.sort(key=lambda q: -q["t"])
    q_terms = Counter()
    for q in questions:
        for w in re.findall(r"[a-z0-9+#_]{3,}", _norm(q["q"])):
            q_terms[w] += 1

    run_total = sum(verdicts.values())
    return {
        "days": days,
        "since": _day(since),
        "total_events": len(evs),
        "window_events": len(win),
        "first_event": evs[0]["ts"] if evs else None,
        "counts": {k: by_kind.get(k, 0) for k in KNOWN_KINDS} | {k: v for k, v in by_kind.items() if k not in KNOWN_KINDS},
        "counts_all": dict(by_kind_all),
        "runs": {"total": run_total, "ok": verdicts.get("OK", 0),
                 "ok_rate": round(verdicts.get("OK", 0) / run_total, 3) if run_total else None,
                 "verdicts": [{"label": v, "value": n} for v, n in verdicts.most_common()]},
        "tester": {"sessions": tester["sessions"], "ok": tester["ok"], "failed": tester["failed"], "tests": tester["tests"],
                   "verdicts": [{"label": v, "value": n} for v, n in tester["verdicts"].most_common()]},
        "compile_errors": by_kind.get("compile_error", 0),
        "checker_issues": by_kind.get("checker_issue", 0),
        "top_errors": ranking,
        "week": week,
        "trend": series,
        "kb_top": kb_top,
        "kb_unique": len(kb_views),
        "questions": questions[:20],
        "question_terms": [{"label": w, "value": n} for w, n in q_terms.most_common(12)],
        "activity": dict(activity),
    }


# ============================================================================================ postępy ucznia (programy)
def _program_id(rec: dict) -> str:
    """Zgodne z `oiforge.tools.programs.program_id`."""
    mod = sys.modules.get("oiforge.tools.programs")
    fn = getattr(mod, "program_id", None) if mod else None
    if fn:
        try:
            return fn(rec)
        except Exception:  # noqa: BLE001
            pass
    return f"{rec.get('source') or 'local'}:{rec.get('submission_id') or rec.get('path')}"


def programs_index_path() -> Path:
    return Path(config.PROGRAMS_DIR) / "index.json"


_prog_cache: dict = {"sig": None, "records": []}
_prog_lock = threading.Lock()


def load_programs(index_path=None) -> list[dict]:
    """Rekordy z index.json (z cache po mtime). Brak/uszkodzony plik → []."""
    p = Path(index_path) if index_path else programs_index_path()
    try:
        st = p.stat()
        sig = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    with _prog_lock:
        if _prog_cache["sig"] != sig:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                data = {}
            recs = data.get("programs") if isinstance(data, dict) else data
            recs = [r for r in (recs or []) if isinstance(r, dict)]
            _prog_cache.update(sig=sig, records=recs)
        return list(_prog_cache["records"])


def _parse_time(s) -> datetime | None:
    s = str(s or "").strip().replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def _accepted(rec: dict) -> bool:
    sc = rec.get("score")
    if sc is None:
        return True
    try:
        return float(sc) >= 100
    except (TypeError, ValueError):
        return True


def solved_problems(index_path=None) -> list[dict]:
    """Unikalne zadania (source, konkurs, skrót) z datą pierwszego i ostatniego rozwiązania."""
    by_key: dict[tuple, dict] = {}
    for r in load_programs(index_path):
        if not _accepted(r):
            continue
        dt = _parse_time(r.get("time"))
        key = (str(r.get("source") or ""), str(r.get("contest_id") or ""),
               str(r.get("problem_short") or r.get("problem_name") or r.get("path") or "").lower())
        it = by_key.get(key)
        pid = _program_id(r)
        if it is None:
            by_key[key] = {"key": "|".join(key), "id": pid, "first_id": pid, "last_id": pid,
                           "problem_name": r.get("problem_name") or r.get("problem_short") or "?",
                           "problem_short": r.get("problem_short") or "", "contest_id": r.get("contest_id"),
                           "contest_name": r.get("contest_name") or r.get("contest_id"), "round": r.get("round"),
                           "url": r.get("url"), "first": dt, "last": dt, "submissions": 1, "lines": r.get("lines")}
            continue
        it["submissions"] += 1
        if dt and (it["first"] is None or dt < it["first"]):
            it["first"], it["first_id"], it["id"] = dt, pid, pid
            it["round"] = r.get("round") or it["round"]
        if dt and (it["last"] is None or dt > it["last"]):
            it["last"], it["last_id"] = dt, pid
        if not it["round"] and r.get("round"):
            it["round"] = r.get("round")
    out = list(by_key.values())
    out.sort(key=lambda x: (x["first"] or datetime.min))
    for it in out:
        it["first_time"] = it["first"].strftime("%Y-%m-%d %H:%M:%S") if it["first"] else None
        it["last_time"] = it["last"].strftime("%Y-%m-%d %H:%M:%S") if it["last"] else None
    return out


def _streaks(days_set: set[date], today: date) -> dict:
    """Seria dni (bieżąca kończy się dziś albo wczoraj) i seria tygodni ISO."""
    if not days_set:
        return {"current": 0, "longest": 0, "current_weeks": 0, "longest_weeks": 0, "active_today": False}
    ds = sorted(days_set)
    longest = run = 1
    for a, b in zip(ds, ds[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        longest = max(longest, run)
    cur = 0
    d = today if today in days_set else today - timedelta(days=1)
    while d in days_set:
        cur += 1
        d -= timedelta(days=1)

    weeks = sorted({(x - timedelta(days=x.weekday())) for x in ds})
    wl = wrun = 1
    for a, b in zip(weeks, weeks[1:]):
        wrun = wrun + 1 if (b - a).days == 7 else 1
        wl = max(wl, wrun)
    wset = set(weeks)
    this_monday = today - timedelta(days=today.weekday())
    m = this_monday if this_monday in wset else this_monday - timedelta(days=7)
    wcur = 0
    while m in wset:
        wcur += 1
        m -= timedelta(days=7)
    return {"current": cur, "longest": longest, "current_weeks": wcur, "longest_weeks": wl,
            "active_today": today in days_set}


def progress(index_path=None, today: date | None = None) -> dict:
    """Postępy z rozwiązań ucznia: oś czasu, heatmapa, serie, tematy (dopasowane do ścieżki nauki)."""
    today = today or date.today()
    recs = [r for r in load_programs(index_path) if _accepted(r)]
    probs = solved_problems(index_path)
    heat: Counter = Counter()
    months: Counter = Counter()
    weekday: Counter = Counter()
    for r in recs:
        dt = _parse_time(r.get("time"))
        if dt:
            heat[dt.date().isoformat()] += 1
            months[dt.strftime("%Y-%m")] += 1
            weekday[dt.weekday()] += 1
    first_days: Counter = Counter(p["first"].date().isoformat() for p in probs if p["first"])
    cumulative, total = [], 0
    for d in sorted(first_days):
        total += first_days[d]
        cumulative.append({"date": d, "count": first_days[d], "total": total})
    if cumulative and cumulative[-1]["date"] < today.isoformat():
        cumulative.append({"date": today.isoformat(), "count": 0, "total": total})
    days_set = {date.fromisoformat(d) for d in heat}
    streak = _streaks(days_set, today)

    # --- miesiące (ciągła oś)
    month_list = []
    if months:
        y, m = map(int, min(months).split("-"))
        y2, m2 = today.year, today.month
        while (y, m) <= (y2, m2):
            k = f"{y:04d}-{m:02d}"
            month_list.append({"month": k, "value": months.get(k, 0)})
            m += 1
            if m > 12:
                y, m = y + 1, 1

    # --- tematy (rundy → tematy ścieżki nauki)
    rounds: dict[str, dict] = {}
    for p in probs:
        rn = (p.get("round") or "").strip() or "(bez rundy)"
        it = rounds.setdefault(rn, {"round": rn, "count": 0, "topics": []})
        it["count"] += 1
    topic_rows, matched_rounds, strongest, weakest, by_topic = [], set(), [], [], {}
    try:
        from . import training  # noqa: PLC0415 - leniwy import (unikamy cyklu)
        tmap = training.topic_assignments(probs)
        by_topic = tmap["by_topic"]
        for rn, it in rounds.items():
            it["topics"] = training.topics_for_round(rn)
            if it["topics"]:
                matched_rounds.add(rn)
        topic_rows = training.topic_progress_rows(by_topic)
    except Exception as e:  # noqa: BLE001 - ścieżka nauki jest opcjonalna
        topic_rows = []
        sys.stderr.write(f"[journal] tematy: {e}\n")
    solved_topics = [t for t in topic_rows if t["solved"] > 0]
    strongest = sorted(solved_topics, key=lambda t: (-t["solved"], -t["ratio"], t["order"]))[:6]
    weakest = sorted((t for t in topic_rows if t.get("unlocked") and t["ratio"] < 1 and t.get("has_tasks")),
                     key=lambda t: (t["ratio"], t["order"]))[:6]

    recent = sorted(probs, key=lambda p: p["last"] or datetime.min, reverse=True)[:12]
    contests = Counter(p.get("contest_name") or "?" for p in probs)
    wd = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
    first = min((p["first"] for p in probs if p["first"]), default=None)
    last = max((p["last"] for p in probs if p["last"]), default=None)
    weeks_span = max(1, ((today - first.date()).days // 7) + 1) if first else 1

    def light(p):
        return {"id": p["id"], "last_id": p["last_id"], "problem_name": p["problem_name"], "problem_short": p["problem_short"],
                "contest_name": p["contest_name"], "round": p["round"], "time": p["last_time"], "first_time": p["first_time"],
                "url": p["url"], "submissions": p["submissions"]}

    return {
        "has_data": bool(recs),
        "totals": {"submissions": len(recs), "problems": len(probs), "contests": len(contests),
                   "active_days": len(days_set), "topics_with_tasks": len(solved_topics),
                   "per_week": round(len(probs) / weeks_span, 2)},
        "first": first.strftime("%Y-%m-%d") if first else None,
        "last": last.strftime("%Y-%m-%d") if last else None,
        "streak": streak,
        "heatmap": dict(heat),
        "cumulative": cumulative,
        "months": month_list,
        "by_weekday": [{"label": wd[i], "value": weekday.get(i, 0)} for i in range(7)],
        "contests": [{"label": c, "value": n} for c, n in contests.most_common()],
        "rounds": sorted(rounds.values(), key=lambda x: (-x["count"], x["round"])),
        "unmatched_rounds": sorted(rn for rn in rounds if rn not in matched_rounds),
        "topics": topic_rows,
        "strongest": strongest,
        "weakest": weakest,
        "recent": [light(p) for p in recent],
    }


# ============================================================================================ HTTP
def handle_log(body: dict) -> dict:
    kind = body.get("kind")
    if not kind or not isinstance(kind, str):
        raise ApiError("Podaj rodzaj zdarzenia (kind).")
    data = body.get("data")
    if data is None:
        data = {k: v for k, v in body.items() if k not in ("kind", "data")}
    log_event(kind, data if isinstance(data, dict) else {"value": data})
    return {"ok": True}


def handle_stats(q: dict) -> dict:
    try:
        days = int(q.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return {"ok": True, **stats(days)}


def handle_progress(_q: dict) -> dict:
    return {"ok": True, **progress()}


def handle_events(q: dict) -> dict:
    try:
        limit = max(1, min(int(q.get("limit") or 100), 2000))
    except (TypeError, ValueError):
        limit = 100
    kind = q.get("kind") or None
    evs = events({k.strip() for k in kind.split(",")} if kind else None, limit=limit)
    return {"ok": True, "events": list(reversed(evs))}


ROUTES = {
    "POST /api/journal/log": handle_log,
    "GET /api/journal/stats": handle_stats,
    "GET /api/journal/progress": handle_progress,
    "GET /api/journal/events": handle_events,
}

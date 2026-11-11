"""Synchronizacja „Moich programów” z SIO2 (dowolna instancja oioioi: Szkopuł, szkolne serwery SIO2, …).

Pobiera WSZYSTKIE zgłoszenia za 100 pkt (w konkursach ACM: zaakceptowane) ze wszystkich konkursów użytkownika,
rundy z list zadań (= tematy!) i treści zadań (PDF/HTML). Przyrostowo: znane zgłoszenia są pomijane.
Format zapisu = `user_data/programs/index.json` (moduł `programs`).

Logowanie: (a) login + hasło (Django CSRF: GET /login/ → csrftoken + csrfmiddlewaretoken, POST z Referer),
(b) wklejone ciasteczko `sessionid`. Hasło NIGDY nie jest zapisywane; opcjonalnie zapamiętujemy tylko cookie sesji.

Awaryjnie (gdy Python nie łączy się z serwerem): skrypt do konsoli przeglądarki/zakładka → plik JSON → import,
albo import ZIP/folderu z plikami .cpp/.py (z heurystyką nazwy zadania z nazwy pliku).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import http.cookiejar
import io
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from .. import config
from ..core import jobs, store
from ..server_api import ApiError
from . import _sio2_parse as P
from . import programs as PR

SZKOPUL_BASE = "https://szkopul.edu.pl"
DEFAULT_BASE = SZKOPUL_BASE          # gdy nie ma jeszcze żadnych programów ani ustawionego adresu
UA = f"OI Forge/{config.VERSION} (lokalne studio OI ucznia; synchronizacja wlasnych zgloszen)"  # tylko ASCII
SESSION_KEY = "sio2_session"
STATE_NAME = ".sync_state.json"
MAX_RESPONSE = 25 * 1024 * 1024
CODE_EXT = {".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp", ".cp": "cpp", ".c": "c", ".py": "python",
            ".java": "java", ".pas": "pascal", ".rs": "rust", ".go": "go", ".kt": "kotlin", ".hs": "haskell"}
SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", ".idea", ".vscode", "venv", ".venv", "build",
             "dist", "__MACOSX", ".DS_Store", "bin", "obj", "target"}
_running: dict = {"job": None}
_run_lock = threading.Lock()


class SyncError(Exception):
    pass


class Cancelled(Exception):
    pass


# ============================================================================================ klient HTTP
def default_base(root=None) -> str:
    """Adres SIO2 dla tego użytkownika: zapisany w ustawieniach, inaczej serwer, z którego ma już najwięcej
    programów, a gdy nie ma żadnych — Szkopuł. Nic nie jest wpisane na sztywno pod konkretną osobę."""
    try:
        s = (store.settings().get("sio2", {}) or {}).get("base_url") or ""
    except Exception:  # noqa: BLE001
        s = ""
    if s.strip():
        return s.strip()
    try:
        counts: dict[str, int] = {}
        for r in PR.load_index(Path(root or PR.programs_dir()))["programs"]:
            src = str(r.get("source") or "")
            if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(_\d+)?", src):
                counts[src] = counts.get(src, 0) + 1
        if counts:
            return "https://" + max(counts, key=counts.get).replace("_", ":")
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_BASE


def normalize_base(url: str | None) -> str:
    u = (url or "").strip() or default_base()
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ApiError("Niepoprawny adres SIO2 (np. https://szkopul.edu.pl albo adres szkolnego serwera SIO2).")
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


def source_of(base: str) -> str:
    return urllib.parse.urlparse(base).netloc.replace(":", "_")


class Resp:
    def __init__(self, status: int, url: str, headers: dict, body: bytes):
        self.status, self.url, self.headers, self.body = status, url, headers, body

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    @property
    def text(self) -> str:
        m = re.search(r"charset=([\w\-]+)", self.headers.get("content-type") or "", re.I)
        enc = m.group(1) if m else "utf-8"
        try:
            return self.body.decode(enc, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class Sio2Client:
    """Grzeczny klient oioioi: User-Agent „OI Forge”, ciasteczka, opóźnienia między żądaniami, ponawianie."""

    def __init__(self, base_url: str, delay: float = 0.35, timeout: float = 25.0, retries: int = 3,
                 cancelled=None, log=None):
        self.base = normalize_base(base_url)
        self.host = urllib.parse.urlparse(self.base).hostname or ""
        self.delay = max(0.0, float(delay))
        self.timeout = timeout
        self.retries = retries
        self.cancelled = cancelled or (lambda: False)
        self.log = log or (lambda msg, level="info": None)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._last = 0.0
        self.requests = 0

    def abs(self, path: str) -> str:
        if re.match(r"^https?://", path or "", re.I):
            return path
        return urllib.parse.urljoin(self.base + "/", (path or "/").lstrip("/"))

    def _sleep(self, sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            if self.cancelled():
                raise Cancelled()
            time.sleep(min(0.1, max(0.0, end - time.time())))

    def _throttle(self) -> None:
        wait = self._last + self.delay - time.time()
        if wait > 0:
            self._sleep(wait)
        self._last = time.time()

    def request(self, path: str, data: dict | bytes | None = None, headers: dict | None = None) -> Resp:
        url = self.abs(path)
        hdrs = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
                "Accept-Language": "pl,en;q=0.7"}
        hdrs.update(headers or {})
        body = urllib.parse.urlencode(data).encode("utf-8") if isinstance(data, dict) else data
        if body is not None:
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        attempt = 0
        while True:
            if self.cancelled():
                raise Cancelled()
            self._throttle()
            self.requests += 1
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if body is not None else "GET")
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    content = r.read(MAX_RESPONSE + 1)
                    return Resp(r.status, r.geturl(), {k.lower(): v for k, v in r.headers.items()}, content)
            except urllib.error.HTTPError as e:
                try:
                    content = e.read()
                except Exception:  # noqa: BLE001
                    content = b""
                finally:
                    try:
                        e.close()
                    except Exception:  # noqa: BLE001
                        pass
                hd = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    attempt += 1
                    ra = hd.get("retry-after", "")
                    wait = min(30.0, float(ra)) if ra.isdigit() else 1.5 * attempt
                    self.log(f"Serwer odpowiedział {e.code} — ponawiam za {wait:.0f} s…", "warn")
                    self._sleep(wait)
                    continue
                return Resp(e.code, e.geturl() or url, hd, content)
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError,
                    http.client.HTTPException) as e:
                if attempt < self.retries:
                    attempt += 1
                    self.log(f"Problem z połączeniem ({_reason(e)}) — próba {attempt + 1}/{self.retries + 1}…",
                             "warn")
                    self._sleep(1.5 * attempt)
                    continue
                raise SyncError(f"Nie mogę połączyć się z {self.host}: {_reason(e)}. Sprawdź internet albo użyj "
                                f"skryptu do konsoli przeglądarki (zakładka „Synchronizacja”).") from e

    def get_html(self, path: str) -> tuple[Resp, str]:
        r = self.request(path)
        return r, r.text

    # ---------------------------------------------------------------- ciasteczka i logowanie
    def cookie(self, name: str) -> str | None:
        for c in self.jar:
            if c.name == name:
                return c.value
        return None

    def set_cookie(self, name: str, value: str) -> None:
        ck = http.cookiejar.Cookie(0, name, value, None, False, self.host, False, False, "/", True, False, None, True,
                                   None, None, {}, False)
        self.jar.set_cookie(ck)

    def set_session(self, cookie_text: str) -> None:
        """`abc123` albo `sessionid=abc123; csrftoken=…` (np. skopiowane z narzędzi deweloperskich)."""
        t = (cookie_text or "").strip().strip('"').strip("'")
        if not t:
            raise ApiError("Puste ciasteczko sesji.")
        if t.lower().startswith("cookie:"):
            t = t[7:].strip()
        if "=" in t:
            for part in t.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if re.fullmatch(r"[\w\-]+", k) and v:
                        self.set_cookie(k, v)
            if not self.cookie("sessionid"):
                raise ApiError("W wklejonym tekście nie ma ciasteczka sessionid.")
        else:
            if not re.fullmatch(r"[\w\-.:]+", t):
                raise ApiError("Ciasteczko sessionid ma niepoprawne znaki.")
            self.set_cookie("sessionid", t)

    def login(self, username: str, password: str) -> str | None:
        r, html = self.get_html("/login/")
        if r.status >= 400:
            raise SyncError(f"Strona logowania zwróciła błąd HTTP {r.status}.")
        if P.is_logged_in(html):
            return P.username(html)
        form = P.parse_login_form(html)
        if not form:
            raise SyncError("Nie znalazłem formularza logowania na /login/ — czy to na pewno serwer oioioi (SIO2)?")
        fields = dict(form["fields"])
        fields["csrfmiddlewaretoken"] = form["csrf"] or self.cookie("csrftoken") or ""
        if not fields["csrfmiddlewaretoken"]:
            raise SyncError("Brak tokenu CSRF na stronie logowania.")
        fields[form["user_field"]] = username
        fields[form["pass_field"]] = password
        fields.setdefault("next", "")
        action = urllib.parse.urljoin(r.url, form["action"] or r.url)
        r2 = self.request(action, data=fields, headers={"Referer": r.url})
        fields.clear()                                    # hasło nie zostaje w pamięci dłużej niż trzeba
        html2 = r2.text
        if r2.status == 403:
            raise SyncError("SIO2 odrzuciło logowanie (403 — ochrona CSRF). Spróbuj wkleić ciasteczko sessionid.")
        if P.is_logged_in(html2):
            return P.username(html2)
        err = P.login_error(html2)
        raise SyncError("Nieprawidłowy login lub hasło." + (f" Komunikat SIO2: {err}" if err else ""))


def _reason(e: Exception) -> str:
    r = getattr(e, "reason", None) or e
    if isinstance(r, (socket.timeout, TimeoutError)):
        return "przekroczono czas oczekiwania"
    return str(r)[:200] or e.__class__.__name__


# ============================================================================================ stan synchronizacji
def _state_path(root: Path) -> Path:
    return Path(root) / STATE_NAME


def load_state(root: Path) -> dict:
    try:
        d = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def save_state(root: Path, st: dict) -> None:
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _int_score(v):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(float(v), 2)


def _lang_of(filename: str | None, code: str) -> tuple[str, str]:
    ext = Path(filename or "").suffix.lower()
    if ext in CODE_EXT:
        return CODE_EXT[ext], ext
    if re.search(r"^\s*#\s*include|\bint\s+main\s*\(", code, re.M):
        return "cpp", ".cpp"
    if re.search(r"^\s*(def |import |from \w+ import|print\()", code, re.M):
        return "python", ".py"
    return "cpp", ext if ext else ".cpp"


def _decode_code(raw: bytes) -> str:
    for enc in ("utf-8", "cp1250", "iso-8859-2"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ============================================================================================ synchronizacja
def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def sync(job, opts: dict) -> dict:
    """Funkcja zadania w tle (jobs.submit). Zwraca podsumowanie."""
    t0 = time.time()
    root = Path(opts.get("_root") or PR.programs_dir())
    client = Sio2Client(opts.get("base_url") or store.settings().get("sio2", {}).get("base_url") or DEFAULT_BASE,
                        delay=float(opts.get("delay") if opts.get("delay") is not None else 0.35),
                        timeout=float(opts.get("timeout") or 25), cancelled=lambda: job.cancelled,
                        log=lambda m, level="info": job.log(m, level))
    base, source = client.base, source_of(client.base)
    try:
        return _sync(job, opts, client, root, base, source, t0)
    except Cancelled:
        job.log("Przerwano synchronizację (to, co już pobrane, zostało zapisane).", "warn")
        return {"cancelled": True, "duration": round(time.time() - t0, 1)}
    finally:
        opts.pop("password", None)


def _sync(job, opts, client: Sio2Client, root: Path, base: str, source: str, t0: float) -> dict:
    job.progress(0.01, f"Łączenie z {client.host}…")
    user = None
    username, password = (opts.get("username") or "").strip(), opts.get("password") or ""
    saved = store.get(SESSION_KEY, {}) or {}
    if username and password:
        job.log(f"Logowanie jako {username}…")
        user = client.login(username, password)
        password = ""
        opts["password"] = ""
    elif (opts.get("sessionid") or "").strip():
        client.set_session(opts["sessionid"])
        job.log("Używam wklejonego ciasteczka sesji.")
    elif saved.get("cookie") and saved.get("base_url") == base:
        client.set_session(saved["cookie"])
        job.log("Używam zapamiętanej sesji.")
    else:
        raise SyncError("Podaj login i hasło albo wklej ciasteczko sessionid (albo użyj skryptu do konsoli).")
    r, html = client.get_html("/contest/")
    if r.status >= 400 or not P.is_logged_in(html):
        if saved.get("cookie") and not (username or opts.get("sessionid")):
            store.delete(SESSION_KEY)
        raise SyncError("Sesja jest nieważna albo wygasła — zaloguj się ponownie (login i hasło lub nowe ciasteczko).")
    user = user or P.username(html)
    job.log(f"Zalogowano{f' jako {user}' if user else ''}.", "success")
    if opts.get("remember"):
        sid = client.cookie("sessionid")
        if sid:
            store.set(SESSION_KEY, {"base_url": base, "cookie": f"sessionid={sid}", "username": user or username,
                                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
            job.log("Zapamiętano sesję (tylko ciasteczko, bez hasła).")
    if opts.get("save_settings", True):
        try:
            store.update_settings({"sio2": {"base_url": base}})
        except Exception:  # noqa: BLE001
            pass
    # ---- lista konkursów (z paginacją)
    contests = P.parse_contests(html)
    pag = P.parse_pagination(html)
    page = 1
    while pag.get("next") and page < 30:
        page = pag["next"]
        r, h2 = client.get_html(f"/contest/?page={page}")
        if r.status >= 400:
            break
        known_ids = {c["id"] for c in contests}
        contests += [c for c in P.parse_contests(h2) if c["id"] not in known_ids]
        pag = P.parse_pagination(h2)
    only = _as_list(opts.get("contests"))
    if only:
        contests = [c for c in contests if c["id"] in only or c["name"] in only]
        for cid in only:
            if cid not in {c["id"] for c in contests} and re.fullmatch(r"[\w\-.]+", cid):
                contests.append({"id": cid, "name": cid, "url": f"/c/{cid}/"})
    if not contests:
        job.log("Nie widzę konkursów na /contest/ — sprawdzę samo archiwum zadań.", "warn")
    else:
        job.log(f"Konkursy: {len(contests)}.")
    job.push({"type": "contests", "items": contests})
    # ---- stan i znane zgłoszenia
    data = PR.load_index(root, strict=True)
    known = {str(r.get("submission_id")) for r in data["programs"] if r.get("source") == source}
    by_sid = {str(r.get("submission_id")): r for r in data["programs"] if r.get("source") == source}
    state = load_state(root)
    sstate = state.setdefault(source, {})
    full = bool(opts.get("full"))
    want_statements = opts.get("statements", True) is not False
    totals = {"new": 0, "accepted": 0, "submissions": 0, "statements": 0, "pdf": 0, "errors": []}
    problems_all = PR.load_problems(root, source)
    nC = len(contests)
    for ci, c in enumerate(contests):
        if job.cancelled:
            raise Cancelled()
        cid, cname = c["id"], c["name"]
        frac0 = 0.04 + 0.95 * ci / nC
        span = 0.95 / nC
        job.progress(frac0, f"[{ci + 1}/{nC}] {cname}: zgłoszenia…")
        cst = sstate.get(cid, {})
        rows: list[dict] = []
        seen = set()
        page = 1
        max_sid = int(cst.get("max_sid") or 0)
        while True:
            path = f"/c/{urllib.parse.quote(cid)}/submissions/" + (f"?page={page}" if page > 1 else "")
            r, h = client.get_html(path)
            if r.status in (403, 404):
                job.log(f"{cname}: brak dostępu do zgłoszeń (HTTP {r.status}) — pomijam.", "warn")
                break
            if r.status >= 400:
                totals["errors"].append(f"{cname}: HTTP {r.status}")
                job.log(f"{cname}: błąd HTTP {r.status} przy liście zgłoszeń.", "warn")
                break
            if "/login" in urllib.parse.urlparse(r.url).path and not P.is_logged_in(h):
                raise SyncError("Sesja wygasła w trakcie synchronizacji — zaloguj się ponownie.")
            parsed = P.parse_submissions(h, base)
            if not parsed["has_table"]:
                break
            page_rows = [x for x in parsed["rows"] if x["submission_id"] not in seen]
            for x in page_rows:
                seen.add(x["submission_id"])
            rows += page_rows
            nxt = parsed["next_page"]
            if not full and cst.get("complete") and max_sid and page_rows and \
                    min(int(x["submission_id"]) for x in page_rows) <= max_sid:
                break               # starsze strony już przejrzane przy poprzedniej synchronizacji
            if not nxt or nxt <= page or page >= 400:
                break
            page = nxt
            job.progress(frac0 + span * 0.2, f"[{ci + 1}/{nC}] {cname}: zgłoszenia, strona {page}…")
        accepted = [x for x in rows if P.is_accepted(x)]
        new = [x for x in accepted if x["submission_id"] not in known]
        totals["submissions"] += len(rows)
        totals["accepted"] += len(accepted)
        job.push({"type": "contest", "id": cid, "name": cname, "index": ci, "total": nC, "submissions": len(rows),
                  "accepted": len(accepted), "new": len(new)})
        if rows:
            job.log(f"{cname}: {len(rows)} zgłoszeń, {len(accepted)} za 100 pkt, nowych: {len(new)}.")
        all_sids = [int(x["submission_id"]) for x in rows] + [max_sid]
        has_programs = bool(accepted) or any(rr.get("contest_id") == cid for rr in by_sid.values())
        if not has_programs:
            sstate[cid] = {"complete": True, "max_sid": max(all_sids), "last_sync": datetime.now().isoformat(
                timespec="seconds"), "name": cname}
            continue
        # ---- lista zadań i rundy (tematy)
        job.progress(frac0 + span * 0.3, f"[{ci + 1}/{nC}] {cname}: lista zadań i rundy…")
        probs = _fetch_problems(client, cid)
        if probs:
            problems_all[cid] = {"problems": [{k: p.get(k) for k in ("short", "name", "round", "cells", "url")}
                                              for p in probs]}
            PR.save_problems(root, source, problems_all)
        pmap = {}
        for p in probs:
            pmap.setdefault(str(p["short"]).lower(), p)
            pmap.setdefault(str(p.get("url_short") or "").lower(), p)
        # ---- pobieranie kodów
        recs = []
        for k, row in enumerate(new):
            job.progress(frac0 + span * (0.35 + 0.45 * k / max(1, len(new))),
                         f"[{ci + 1}/{nC}] {cname}: pobieram {k + 1}/{len(new)} — {row['problem_name']}")
            try:
                rec = _download(client, root, source, base, cid, cname, row, pmap)
            except Cancelled:
                raise
            except SyncError as e:
                totals["errors"].append(f"{cname} / {row['submission_id']}: {e}")
                job.log(f"Nie pobrano zgłoszenia {row['submission_id']}: {e}", "warn")
                continue
            if rec:
                recs.append(rec)
                known.add(rec["submission_id"])
                job.push({"type": "program", "contest_id": cid, "contest_name": cname,
                          "problem_name": rec["problem_name"], "problem_short": rec["problem_short"],
                          "submission_id": rec["submission_id"], "time": rec["time"], "round": rec.get("round"),
                          "path": rec["path"]})
        # uzupełnij rundy w starych rekordach (tylko dodajemy brakujące pola)
        fixes = []
        for sid_, old in by_sid.items():
            if old.get("contest_id") == cid and not old.get("round"):
                p = pmap.get(str(old.get("problem_short") or "").lower())
                if p and p.get("round"):
                    fixes.append({"source": source, "submission_id": sid_, "round": P.clean_round(p["round"])})
        if recs or fixes:
            res = PR.merge_programs(recs + fixes, root)
            totals["new"] += res["added"]
        # ---- treści zadań
        if want_statements:
            shorts = {}
            for x in accepted:
                shorts.setdefault(str(x["problem_short"]).lower(), x["problem_short"])
            for rr in by_sid.values():
                if rr.get("contest_id") == cid:
                    shorts.setdefault(str(rr.get("problem_short") or "").lower(), rr.get("problem_short"))
            todo = [s for s in shorts if s and not any(PR.find_statement(root, source, cid, s).values())]
            for k, s in enumerate(todo):
                job.progress(frac0 + span * (0.8 + 0.2 * k / max(1, len(todo))),
                             f"[{ci + 1}/{nC}] {cname}: treść {k + 1}/{len(todo)} ({s})")
                p = pmap.get(s)
                try:
                    kind = _fetch_statement(client, root, source, cid, s, p)
                except Cancelled:
                    raise
                except SyncError as e:
                    job.log(f"Treść {s}: {e}", "warn")
                    continue
                if kind:
                    totals["statements"] += 1
                    totals["pdf"] += kind == "pdf"
                    job.push({"type": "statement", "contest_id": cid, "short": s, "kind": kind})
        sstate[cid] = {"complete": True, "max_sid": max(all_sids), "name": cname,
                       "last_sync": datetime.now().isoformat(timespec="seconds")}
        state[source] = sstate
        save_state(root, state)
    # ---- archiwum zadań (problemset, np. Szkopuł): zgłoszenia spoza konkursów z ogólnej listy /submissions/
    if opts.get("problemset", True) is not False and not only:
        _sync_problemset(job, client, root, base, source, known, totals, sstate, full)
        state[source] = sstate
        save_state(root, state)
    if not contests and not totals["submissions"]:
        raise SyncError("Nie znalazłem ani konkursów, ani zgłoszeń w archiwum zadań — sprawdź adres i logowanie.")
    state.setdefault("_last", {})[source] = {"at": datetime.now().isoformat(timespec="seconds"), "user": user,
                                            "new": totals["new"], "contests": nC}
    save_state(root, state)
    PR.invalidate()
    pending = len(pending_statements(root))
    summary = {"contests": nC, "submissions": totals["submissions"], "accepted": totals["accepted"],
               "new_programs": totals["new"], "total": len(PR.load_index(root)["programs"]),
               "statements_new": totals["statements"], "pdf_new": totals["pdf"], "pending_statements": pending,
               "user": user, "errors": totals["errors"][:50], "requests": client.requests,
               "duration": round(time.time() - t0, 1), "base_url": base}
    job.log(f"Gotowe: {totals['new']} nowych programów, {totals['statements']} nowych treści"
            f"{f', {pending} PDF do ekstrakcji tekstu' if pending else ''}.", "success")
    return summary


ARCHIVE_ID = "archiwum"
ARCHIVE_NAME = "Archiwum zadań"


def _sync_problemset(job, client: Sio2Client, root: Path, base: str, source: str, known: set, totals: dict,
                     sstate: dict, full: bool) -> None:
    """Zgłoszenia z archiwum zadań (poza konkursami) — oioioi ma ogólną listę „Wszystkie zgłoszenia” pod
    /submissions/ (ta sama tabela co w konkursie; linki /s/<id>/ bez /c/<konkurs>/). Na Szkopule to główne
    miejsce, gdzie trafiają rozwiązania zadań z archiwum."""
    job.progress(0.985, f"{ARCHIVE_NAME}: zgłoszenia poza konkursami…")
    cst = sstate.get("_" + ARCHIVE_ID, {})
    max_sid = int(cst.get("max_sid") or 0)
    rows, seen, page = [], set(), 1
    while True:
        if job.cancelled:
            raise Cancelled()
        r, h = client.get_html("/submissions/" + (f"?page={page}" if page > 1 else ""))
        if r.status >= 400:
            if r.status not in (403, 404):
                job.log(f"{ARCHIVE_NAME}: HTTP {r.status} przy liście zgłoszeń — pomijam.", "warn")
            break
        parsed = P.parse_submissions(h, base)
        if not parsed["has_table"]:
            break
        page_rows = [x for x in parsed["rows"] if x["submission_id"] not in seen]
        for x in page_rows:
            seen.add(x["submission_id"])
        rows += page_rows
        nxt = parsed["next_page"]
        if not full and cst.get("complete") and max_sid and page_rows and \
                min(int(x["submission_id"]) for x in page_rows) <= max_sid:
            break
        if not page_rows or not nxt or nxt <= page or page >= 400:
            break
        page = nxt
    archive = [x for x in rows if x.get("contest_id") == ""]
    accepted = [x for x in archive if P.is_accepted(x)]
    new = [x for x in accepted if x["submission_id"] not in known]
    totals["submissions"] += len(archive)
    totals["accepted"] += len(accepted)
    if archive:
        job.log(f"{ARCHIVE_NAME}: {len(archive)} zgłoszeń, {len(accepted)} za 100 pkt, nowych: {len(new)}.")
    recs = []
    for k, row in enumerate(new):
        if job.cancelled:
            raise Cancelled()
        job.progress(0.985 + 0.014 * k / max(1, len(new)), f"{ARCHIVE_NAME}: pobieram {k + 1}/{len(new)} — {row['problem_name']}")
        try:
            rec = _download(client, root, source, base, ARCHIVE_ID, ARCHIVE_NAME, row, {}, archive=True)
        except Cancelled:
            raise
        except SyncError as e:
            totals["errors"].append(f"{ARCHIVE_NAME} / {row['submission_id']}: {e}")
            job.log(f"Nie pobrano zgłoszenia {row['submission_id']}: {e}", "warn")
            continue
        if rec:
            recs.append(rec)
            known.add(rec["submission_id"])
            job.push({"type": "program", "contest_id": ARCHIVE_ID, "contest_name": ARCHIVE_NAME,
                      "problem_name": rec["problem_name"], "problem_short": rec["problem_short"],
                      "submission_id": rec["submission_id"], "time": rec["time"], "path": rec["path"]})
    if recs:
        totals["new"] += PR.merge_programs(recs, root)["added"]
    sids = [int(x["submission_id"]) for x in rows] + [max_sid]
    sstate["_" + ARCHIVE_ID] = {"complete": True, "max_sid": max(sids), "name": ARCHIVE_NAME,
                                "last_sync": datetime.now().isoformat(timespec="seconds")}


def _fetch_problems(client: Sio2Client, cid: str) -> list[dict]:
    out, page, seen = [], 1, set()
    while True:
        path = f"/c/{urllib.parse.quote(cid)}/p/" + (f"?page={page}" if page > 1 else "")
        r, h = client.get_html(path)
        if r.status >= 400:
            break
        parsed = P.parse_problems(h, client.base)
        for p in parsed["problems"]:
            key = str(p["short"]).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        nxt = parsed["next_page"]
        if not nxt or nxt <= page or page >= 50:
            break
        page = nxt
    return out


def _download(client: Sio2Client, root: Path, source: str, base: str, cid: str, cname: str, row: dict,
              pmap: dict, archive: bool = False) -> dict | None:
    sid = row["submission_id"]
    prefix = "" if archive else f"/c/{urllib.parse.quote(cid)}"      # archiwum: /s/<id>/… bez konkursu
    r = client.request(f"{prefix}/s/{sid}/download/")
    fname = P.filename_from_content_disposition(r.headers.get("content-disposition"))
    code = None
    if r.status == 200 and not (r.content_type == "text/html" and b"<html" in r.body[:2000].lower()):
        code = _decode_code(r.body)
    else:
        r2, h2 = client.get_html(f"{prefix}/s/{sid}/source/")
        if r2.status == 200:
            code = P.extract_source_from_page(h2) or None
    if code is None:
        raise SyncError(f"HTTP {r.status} przy pobieraniu kodu")
    lang, ext = _lang_of(fname, code)
    short = row.get("problem_short") or (Path(fname).stem if fname else "zad")
    rel = f"{PR.safe_part(source)}/{PR.safe_part(cid)}/{PR.safe_part(short)}__{sid}{ext}"
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(code.encode("utf-8"))
    p = pmap.get(str(short).lower())
    acc_score = row.get("score")
    rec = {"source": source, "contest_id": cid, "contest_name": cname,
           "problem_name": row.get("problem_name") or (p or {}).get("name") or short, "problem_short": short,
           "submission_id": str(sid), "time": row.get("time") or "",
           "score": _int_score(acc_score) if acc_score is not None else 100,
           "status": row.get("status") or "", "lang": lang, "path": rel, "url": f"{base}{prefix}/s/{sid}/",
           "lines": len(code.splitlines())}
    if p and p.get("round"):
        rec["round"] = P.clean_round(p["round"])
    if acc_score is None:
        rec["verdict"] = "accepted"
    if fname:
        rec["filename"] = fname
    return rec


def _fetch_statement(client: Sio2Client, root: Path, source: str, cid: str, short: str, p: dict | None) -> str | None:
    url = (p or {}).get("url") or f"/c/{urllib.parse.quote(cid)}/p/{urllib.parse.quote(short)}/"
    r = client.request(url)
    if r.status >= 400:
        return None
    d = PR.statement_dir(root, source, cid)
    d.mkdir(parents=True, exist_ok=True)
    name = PR.safe_part(short)
    if r.content_type == "application/pdf" or r.body[:5] == b"%PDF-":
        (d / f"{name}.pdf").write_bytes(r.body)
        return "pdf"
    if r.content_type in ("text/html", "application/xhtml+xml", ""):
        html = r.text
        doc = P.parse_html(html)
        root_el = P.statement_root(doc)
        # treść w PDF osadzona/podlinkowana na stronie zadania
        for el in root_el.find_all(pred=lambda n: n.tag in ("a", "iframe", "embed", "object")):
            href = el.get("href") or el.get("src") or el.get("data") or ""
            if re.search(r"\.pdf(\?|$)", href, re.I) or (el.tag != "a" and href):
                r2 = client.request(urllib.parse.urljoin(r.url, href))
                if r2.status == 200 and (r2.content_type == "application/pdf" or r2.body[:5] == b"%PDF-"):
                    (d / f"{name}.pdf").write_bytes(r2.body)
                    return "pdf"
        text = P.html_to_text(doc)
        if len(text.strip()) < 40:
            return None
        (d / f"{name}.html").write_text(html, encoding="utf-8")
        return "html"
    return None


# ============================================================================================ treści PDF → tekst
def pending_statements(root=None) -> list[dict]:
    """PDF-y treści bez pliku .txt (tekst wyciąga przeglądarka przez pdf.js i odsyła)."""
    root = Path(root or PR.programs_dir())
    data = PR.load_index(root)
    out, seen = [], set()
    for rec in data["programs"]:
        src, cid, short = rec.get("source"), rec.get("contest_id"), rec.get("problem_short")
        if not (src and cid and short):
            continue
        st = PR.find_statement(root, src, cid, str(short))
        if st["pdf"] and not st["txt"] and not st["html"]:
            key = f"{PR.safe_part(src)}/{PR.safe_part(cid)}/{st['pdf'].stem}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "source": src, "contest_id": cid, "short": st["pdf"].stem,
                        "problem_name": rec.get("problem_name"), "contest_name": rec.get("contest_name"),
                        "size": st["pdf"].stat().st_size})
    return out


def save_statement_text(key: str, text: str, root=None) -> bool:
    root = Path(root or PR.programs_dir())
    parts = str(key or "").split("/")
    if len(parts) != 3 or not all(re.fullmatch(r"[\w.\-]+", x) and x not in (".", "..") for x in parts):
        raise ApiError("Niepoprawny klucz treści.")
    d = PR.statement_dir(root, parts[0], parts[1])
    pdf = d / f"{parts[2]}.pdf"
    if not pdf.is_file() or not PR.inside(root, pdf):
        raise ApiError(f"Nie ma PDF-a dla {key}.", 404)
    txt = (text or "").replace("\r\n", "\n").replace("\x00", "")
    if not txt.strip():
        return False
    if len(txt) > 2_000_000:
        txt = txt[:2_000_000]
    tmp = d / f"{parts[2]}.txt.tmp"
    tmp.write_text(txt, encoding="utf-8")
    os.replace(tmp, d / f"{parts[2]}.txt")
    PR.invalidate()
    return True


# ============================================================================================ importy
def _existing_hashes(root: Path) -> set[str]:
    hs = set()
    for rec in PR.load_index(root)["programs"]:
        p = root / str(rec.get("path") or "")
        if rec.get("path") and p.is_file():
            try:
                hs.add(_code_hash(p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return hs


def _code_hash(code: str) -> str:
    norm = "\n".join(l.rstrip() for l in code.replace("\r\n", "\n").strip().split("\n"))
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()


def _known_problems(root: Path) -> dict[str, list[dict]]:
    """skrót (małe litery) → [{source, contest_id, short, name, round}] ze wszystkich problems.json."""
    out: dict[str, list[dict]] = {}
    try:
        srcs = [x for x in root.iterdir() if x.is_dir() and (x / "problems.json").is_file()]
    except OSError:
        srcs = []
    for sd in srcs:
        for cid, v in PR.load_problems(root, sd.name).items():
            for p in (v or {}).get("problems", []) or []:
                if isinstance(p, dict) and p.get("short"):
                    out.setdefault(str(p["short"]).lower(), []).append(
                        {"source": sd.name, "contest_id": cid, "short": p["short"], "name": p.get("name"),
                         "round": P.clean_round(p.get("round"))})
    return out


_GENERIC_DIRS = re.compile(r"^(src|kod|kody|code|codes|programy|programs|rozwiazania|rozwiązania|solutions|sol|"
                           r"zadania|tasks|oi|cpp|c\+\+|projekty|projects|desktop|pulpit|documents|dokumenty|"
                           r"downloads|pobrane|home|users|untitled|nowy folder|new folder|\d+)$", re.I)


def guess_problem(relpath: str, code: str, known: dict | None = None) -> dict:
    """Heurystyka: nazwa/skrót zadania i temat z nazwy pliku, folderów i komentarza w kodzie."""
    pth = Path(relpath.replace("\\", "/"))
    stem = pth.stem
    out = {"short": None, "name": None, "round": None, "submission_id": None, "match": None}
    m = re.match(r"^([A-Za-z0-9]+)__(\d+)$", stem)          # format eksportu OI Forge / SIO2: skrót__id
    if m:
        out["short"], out["submission_id"] = m.group(1), m.group(2)
    else:
        s = stem.strip()
        s = re.sub(r"(?i)^(zadanie|zad|task|problem|sol|rozw|rozwiazanie|solution)[\s_\-.]*", "", s) or s
        for _ in range(3):
            s2 = re.sub(r"(?i)[\s_\-.]+(brut|brute|bruteforce|wzor|wzorc|wzorcowka|wzorcówka|sol|solution|rozw|ok|ac|"
                        r"final|fin|good|dobre|v\d+|wersja\d*|\d{1,3}|100|test|gen|copy|kopia)$", "", s)
            if s2 == s:
                break
            s = s2
        out["short"] = s or stem
    mm = re.search(r"(?im)^\s*(?://+|/\*+|\*|#)\s*(?:zadanie|zad\.?|task|problem)\s*[:\-–]?\s*([^\n*]{2,60}?)\s*(?:\*/)?\s*$",
                   code[:3000])
    if mm:
        out["name"] = mm.group(1).strip(" .:-–")
    cand = (known or {}).get(str(out["short"]).lower()) or []
    if cand:
        best = cand[0]
        if out["name"]:
            for c in cand:
                if PR.norm(c.get("name") or "") == PR.norm(out["name"]):
                    best = c
                    break
        out["match"] = best
        out["name"] = out["name"] or best.get("name")
        out["round"] = best.get("round")
    if not out["name"]:
        nm = re.sub(r"[_\-.]+", " ", str(out["short"])).strip()
        nm = re.sub(r"(?<=[a-ząćęłńóśźż])(?=[A-Z])", " ", nm)
        out["name"] = nm[:1].upper() + nm[1:] if nm else stem
    # folder jako temat (np. „Dijkstra/zad.cpp”, „dp/plecak.cpp”)
    for part in reversed(pth.parent.parts):
        if not part or _GENERIC_DIRS.match(part.strip()):
            continue
        if PR.concepts_in_topic(part) and not out["round"]:
            out["round"] = part
            break
        if not out["round"] and not out.get("folder"):
            out["folder"] = part
    return out


def _mtime_str(ts: float | None) -> str:
    try:
        return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def import_files(items: list[tuple[str, bytes, float | None]], batch: str, root=None) -> dict:
    """items: [(ścieżka względna, zawartość, mtime)] → rekordy źródła „import”. Zwraca podsumowanie."""
    root = Path(root or PR.programs_dir())
    slug = PR.safe_part(re.sub(r"\.(zip|json)$", "", batch or "import", flags=re.I)).lower()[:60] or "import"
    known = _known_problems(root)
    hashes = _existing_hashes(root)
    recs, skipped, dup = [], [], 0
    for rel, raw, mt in items:
        ext = Path(rel).suffix.lower()
        if ext not in CODE_EXT:
            continue
        if len(raw) > 1_000_000:
            skipped.append(f"{rel}: za duży plik")
            continue
        code = _decode_code(raw)
        if not code.strip():
            continue
        h = _code_hash(code)
        if h in hashes:
            dup += 1
            continue
        hashes.add(h)
        g = guess_problem(rel, code, known)
        lang = CODE_EXT[ext]
        safe_rel = "/".join(PR.safe_part(x) for x in Path(rel.replace("\\", "/")).parts if x not in ("", ".", ".."))
        path = f"import/{slug}/{safe_rel}"
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(code.encode("utf-8"))
        rec = {"source": "import", "contest_id": f"import-{slug}", "contest_name": f"Import: {batch or slug}",
               "problem_name": g["name"], "problem_short": g["short"],
               "submission_id": g["submission_id"] and f"{g['submission_id']}" or "h" + h[:12],
               "time": _mtime_str(mt), "score": None, "status": "Import", "lang": lang, "path": path, "url": "",
               "lines": len(code.splitlines()), "origin": rel}
        if g.get("round"):
            rec["round"] = g["round"]
        elif g.get("folder"):
            rec["folder"] = g["folder"]
        if g.get("match"):
            m = g["match"]
            rec["statement_ref"] = {"source": m["source"], "contest_id": m["contest_id"], "short": m["short"]}
        recs.append(rec)
    res = PR.merge_programs(recs, root) if recs else {"added": 0, "updated": 0,
                                                      "total": len(PR.load_index(root)["programs"])}
    return {"added": res["added"], "duplicates": dup, "skipped": skipped[:30], "total": res["total"],
            "matched": sum(1 for r in recs if r.get("statement_ref")),
            "programs": [{"problem_name": r["problem_name"], "problem_short": r["problem_short"], "origin": r["origin"],
                          "round": r.get("round"), "matched": bool(r.get("statement_ref"))} for r in recs[:200]]}


def import_zip(raw: bytes, name: str = "import.zip", root=None) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise ApiError("To nie jest poprawny plik ZIP.") from e
    items, total = [], 0
    exports = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            fn = info.filename.replace("\\", "/")
            parts = fn.split("/")
            if any(p in SKIP_DIRS or p.startswith(".") for p in parts[:-1]) or parts[-1].startswith("._"):
                continue
            if info.file_size > 40_000_000:
                continue
            total += info.file_size
            if total > 300_000_000:
                raise ApiError("Archiwum jest za duże (limit 300 MB po rozpakowaniu).")
            ext = Path(fn).suffix.lower()
            if ext == ".json" and info.file_size < 200_000_000:
                try:
                    obj = json.loads(zf.read(info).decode("utf-8"))
                    if isinstance(obj, dict) and obj.get("format") == "oiforge-sio2":
                        exports.append(obj)
                        continue
                except (ValueError, UnicodeDecodeError):
                    pass
            if ext not in CODE_EXT:
                continue
            try:
                ts = datetime(*info.date_time).timestamp()
            except (ValueError, OverflowError):
                ts = None
            items.append((fn, zf.read(info), ts))
    out = import_files(items, name, root) if items else {"added": 0, "duplicates": 0, "skipped": [], "total": None,
                                                          "programs": []}
    for obj in exports:
        r = import_export(obj, root)
        out["added"] += r["added"]
        out["export"] = r
    if out.get("total") is None:
        out["total"] = len(PR.load_index(root)["programs"])
    out["files"] = len(items)
    return out


def import_folder(path: str, root=None) -> dict:
    p = Path(os.path.expanduser(str(path or "").strip().strip('"'))).resolve()
    if not p.is_dir():
        raise ApiError("Nie ma takiego folderu.")
    root_dir = Path(root or PR.programs_dir()).resolve()
    if p == root_dir or root_dir in p.parents:
        raise ApiError("To jest folder samego OI Forge — wybierz folder z Twoimi programami.")
    items, total, n = [], 0, 0
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                       and not d.startswith("cmake-build")]
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext not in CODE_EXT or fn.startswith("."):
                continue
            fp = Path(dirpath) / fn
            try:
                st = fp.stat()
                if st.st_size > 1_000_000:
                    continue
                total += st.st_size
                n += 1
                if n > 5000 or total > 80_000_000:
                    raise ApiError("Za dużo plików w folderze (limit 5000 plików / 80 MB). Wybierz mniejszy folder.")
                items.append((str(fp.relative_to(p)), fp.read_bytes(), st.st_mtime))
            except OSError:
                continue
    res = import_files(items, p.name, root)
    res["files"] = len(items)
    res["folder"] = str(p)
    return res


def import_export(obj: dict, root=None) -> dict:
    """Plik JSON ze skryptu konsoli ({format: "oiforge-sio2", base_url, programs, problems, statements})."""
    root = Path(root or PR.programs_dir())
    if not isinstance(obj, dict):
        raise ApiError("Niepoprawny plik JSON.")
    progs = obj.get("programs")
    if not isinstance(progs, list):
        raise ApiError("W pliku nie ma listy programów („programs”). Czy to plik ze skryptu OI Forge?")
    base = normalize_base(obj.get("base_url") or DEFAULT_BASE)
    source = PR.safe_part(obj.get("source") or source_of(base))
    data = PR.load_index(root, strict=True)
    known = {str(r.get("submission_id")) for r in data["programs"] if r.get("source") == source}
    problems_all = PR.load_problems(root, source)
    pmaps: dict[str, dict] = {}
    for cid, plist in (obj.get("problems") or {}).items():
        if not re.fullmatch(r"[\w\-.]+", str(cid)) or not isinstance(plist, list):
            continue
        clean = []
        for p in plist:
            if isinstance(p, dict) and p.get("short"):
                clean.append({"short": str(p["short"]), "name": str(p.get("name") or p["short"]),
                              "round": p.get("round"), "cells": p.get("cells") or [], "url": p.get("url")})
        if clean:
            problems_all[cid] = {"problems": clean}
            pmaps[cid] = {c["short"].lower(): c for c in clean}
    if pmaps:
        PR.save_problems(root, source, problems_all)
    recs, skipped = [], 0
    for p in progs:
        if not isinstance(p, dict):
            continue
        cid, sid = str(p.get("contest_id") or ""), str(p.get("submission_id") or "")
        code = p.get("code")
        if not (re.fullmatch(r"[\w\-.]+", cid) and re.fullmatch(r"\w+", sid)) or not isinstance(code, str):
            skipped += 1
            continue
        if sid in known:
            continue
        if len(code) > 2_000_000:
            skipped += 1
            continue
        short = str(p.get("problem_short") or "zad")
        lang, ext = _lang_of(p.get("filename"), code)
        rel = f"{source}/{PR.safe_part(cid)}/{PR.safe_part(short)}__{PR.safe_part(sid)}{ext}"
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(code.encode("utf-8"))
        pm = (pmaps.get(cid) or {}).get(short.lower()) or {}
        sc = p.get("score")
        rec = {"source": source, "contest_id": cid, "contest_name": str(p.get("contest_name") or cid),
               "problem_name": str(p.get("problem_name") or pm.get("name") or short), "problem_short": short,
               "submission_id": sid, "time": str(p.get("time") or ""),
               "score": _int_score(sc) if isinstance(sc, (int, float)) else 100,
               "status": str(p.get("status") or ""), "lang": lang, "path": rel,
               "url": f"{base}/c/{cid}/s/{sid}/", "lines": len(code.splitlines())}
        rnd = p.get("round") or pm.get("round")
        if rnd:
            rec["round"] = P.clean_round(rnd)
        if p.get("filename"):
            rec["filename"] = str(p["filename"])[:200]
        recs.append(rec)
        known.add(sid)
    # rundy dla starych rekordów
    fixes = []
    for r in data["programs"]:
        if r.get("source") == source and not r.get("round"):
            pm = (pmaps.get(str(r.get("contest_id"))) or {}).get(str(r.get("problem_short") or "").lower())
            if pm and pm.get("round"):
                fixes.append({"source": source, "submission_id": str(r.get("submission_id")),
                              "round": P.clean_round(pm["round"])})
    res = PR.merge_programs(recs + fixes, root) if (recs or fixes) else {"added": 0, "total": len(data["programs"])}
    st_new = 0
    for cid, items in (obj.get("statements") or {}).items():
        if not re.fullmatch(r"[\w\-.]+", str(cid)) or not isinstance(items, dict):
            continue
        d = PR.statement_dir(root, source, cid)
        for short, st in items.items():
            if not isinstance(st, dict) or not re.fullmatch(r"[\w\-.]+", str(short)):
                continue
            name = PR.safe_part(short)
            if any(PR.find_statement(root, source, cid, name).values()):
                continue
            d.mkdir(parents=True, exist_ok=True)
            typ = st.get("type")
            try:
                if typ == "pdf" and st.get("b64"):
                    raw = base64.b64decode(st["b64"], validate=False)
                    if raw[:5] == b"%PDF-" and len(raw) < 40_000_000:
                        (d / f"{name}.pdf").write_bytes(raw)
                        st_new += 1
                elif typ == "html" and st.get("html"):
                    html = str(st["html"])
                    if "<html" not in html[:500].lower():
                        html = f'<html><body><section class="main-content">{html}</section></body></html>'
                    (d / f"{name}.html").write_text(html, encoding="utf-8")
                    st_new += 1
                elif typ == "text" and st.get("text"):
                    (d / f"{name}.txt").write_text(str(st["text"]), encoding="utf-8")
                    st_new += 1
            except (binascii.Error, ValueError, OSError):
                continue
    PR.invalidate()
    return {"added": res.get("added", 0), "skipped": skipped, "statements_new": st_new, "source": source,
            "total": res.get("total"), "pending_statements": len(pending_statements(root))}


# ============================================================================================ skrypt do konsoli
CONSOLE_JS = r"""(async () => {
  // OI Forge — eksport Twoich zaakceptowanych programów (100 pkt) z SIO2. Działa tylko na stronie SIO2,
  // na której jesteś zalogowany. Nic nie wysyła na zewnątrz: na końcu zapisuje plik JSON w „Pobranych”.
  const KNOWN = new Set(__KNOWN__);          // zgłoszenia, które OI Forge już ma (pomijane)
  const HAVE_ST = new Set(__HAVE_ST__);      // treści, które OI Forge już ma ("konkurs/skrót")
  const WITH_PDF = true, DELAY = 300;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:440px;background:#111827;' +
    'color:#e5e7eb;font:13px/1.45 system-ui,sans-serif;padding:12px 14px;border-radius:10px;' +
    'box-shadow:0 10px 30px rgba(0,0,0,.45);white-space:pre-wrap';
  document.body.appendChild(box);
  const say = (t) => { box.textContent = 'OI Forge: ' + t; console.log('[OI Forge]', t); };
  const parse = (html) => new DOMParser().parseFromString(html, 'text/html');
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
  const norm = (s) => (s || '').toLowerCase().replace(/ł/g, 'l').normalize('NFD').replace(/[̀-ͯ]/g, '').trim();
  async function get(url) {
    for (let a = 0; a < 4; a++) {
      try {
        await sleep(DELAY);
        const r = await fetch(url, { credentials: 'same-origin' });
        if (r.status === 429 || r.status >= 500) { await sleep(2000 * (a + 1)); continue; }
        return r;
      } catch (e) { await sleep(1500 * (a + 1)); }
    }
    throw new Error('Nie udało się pobrać ' + url);
  }
  function b64(buf) {
    const bytes = new Uint8Array(buf); let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  const loggedIn = (d) => !!d.querySelector('[data-post-url$="/logout/"], a[href$="/logout/"], form[action$="/logout/"]');
  const out = { format: 'oiforge-sio2', version: 1, base_url: location.origin, exported_at: new Date().toISOString(),
    user: txt(document.querySelector('#username')), contests: [], programs: [], problems: {}, statements: {} };
  try {
    say('pobieram listę konkursów…');
    const cdoc = parse(await (await get('/contest/')).text());
    if (!loggedIn(cdoc)) { say('Nie jesteś zalogowany na tej stronie SIO2. Zaloguj się i uruchom skrypt ponownie.'); return; }
    const contests = new Map();
    cdoc.querySelectorAll('a[href]').forEach((a) => {
      const m = (a.getAttribute('href') || '').match(/^(?:https?:\/\/[^/]+)?\/c\/([^/?#]+)\/?$/);
      if (m && !contests.has(m[1])) contests.set(m[1], txt(a) || m[1]);
    });
    let ci = 0;
    for (const [cid, cname] of contests) {
      ci++;
      out.contests.push({ id: cid, name: cname });
      const rows = [];
      for (let page = 1; page <= 400; page++) {
        say(`[${ci}/${contests.size}] ${cname}: zgłoszenia, strona ${page}…`);
        const r = await get(`/c/${cid}/submissions/` + (page > 1 ? `?page=${page}` : ''));
        if (!r.ok) break;
        const d = parse(await r.text());
        const table = [...d.querySelectorAll('table.submission')].find((t) => !t.classList.contains('table-report'));
        if (!table) break;
        const heads = [...table.querySelectorAll('thead th')].map((th) => norm(txt(th)));
        const col = (names) => heads.findIndex((h) => names.includes(h));
        const iTime = col(['submission time', 'czas zgloszenia']), iProb = col(['problem', 'zadanie']);
        const iStat = col(['status']), iScore = col(['score', 'wynik']);
        table.querySelectorAll('tbody tr').forEach((tr) => {
          let sid = ((tr.id || '').match(/^report(\d+)row$/) || [])[1];
          const link = tr.querySelector('a[href*="/s/"]');
          if (!sid && link) sid = ((link.getAttribute('href') || '').match(/\/s\/(\d+)\//) || [])[1];
          if (!sid) return;
          const cell = (suf, idx) => tr.querySelector(`[id="submission${sid}-${suf}"]`) || (idx >= 0 ? tr.children[idx] : null);
          const pt = txt(cell('problem-instance', iProb));
          const pm = pt.match(/^(.*\S)\s*\(([^()]+)\)$/);
          const scoreTxt = txt(cell('score', iScore));
          rows.push({ sid, time: txt(cell('link', iTime)), problem_name: pm ? pm[1] : pt, problem_short: pm ? pm[2] : '',
            status: txt(cell('status', iStat)), score: scoreTxt === '' ? null : parseFloat(scoreTxt.replace(',', '.')),
            hasScore: iScore >= 0 });
        });
        if (!d.querySelector(`ul.pagination a[href="?page=${page + 1}"]`)) break;
      }
      const ok = new Set(['ok', 'accepted', 'zaakceptowano', 'zaakceptowane', 'ac']);
      const acc = rows.filter((x) => (x.score !== null && !isNaN(x.score) ? x.score >= 99.999 : ok.has(norm(x.status))));
      if (!acc.length) continue;
      say(`[${ci}/${contests.size}] ${cname}: lista zadań i rundy…`);
      const probs = [];
      for (let page = 1; page <= 50; page++) {
        const r = await get(`/c/${cid}/p/` + (page > 1 ? `?page=${page}` : ''));
        if (!r.ok) break;
        const d = parse(await r.text());
        let round = null;
        d.querySelectorAll('table tr').forEach((tr) => {
          const a = [...tr.querySelectorAll('a[href]')].find((x) => new RegExp(`/c/${cid}/p/[^/]+/(\\d+/)?$`).test(x.getAttribute('href')));
          if (!a) {
            const st = tr.querySelector('strong');
            if (st && (tr.classList.contains('problemlist-subheader') || tr.children.length === 1)) {
              const em = tr.querySelector('em');
              round = (txt(st) + (em && txt(em) ? ' ' + txt(em) : '')).trim();
            }
            return;
          }
          const first = tr.querySelector('td');
          const short = (first && !first.querySelector('a') && txt(first)) || tr.id || a.getAttribute('href').split('/p/')[1].split('/')[0];
          probs.push({ short, name: txt(a), round, url: a.getAttribute('href'), cells: [...tr.children].map(txt) });
        });
        if (!d.querySelector(`ul.pagination a[href="?page=${page + 1}"]`)) break;
      }
      out.problems[cid] = probs;
      const pmap = new Map(probs.map((p) => [p.short.toLowerCase(), p]));
      let k = 0;
      for (const x of acc) {
        k++;
        if (KNOWN.has(x.sid)) continue;
        say(`[${ci}/${contests.size}] ${cname}: kod ${k}/${acc.length} (${x.problem_name})`);
        const r = await get(`/c/${cid}/s/${x.sid}/download/`);
        if (!r.ok) continue;
        const cd = r.headers.get('content-disposition') || '';
        const fn = ((cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i) || [])[1]) || '';
        const p = pmap.get((x.problem_short || '').toLowerCase());
        out.programs.push({ contest_id: cid, contest_name: cname, problem_name: x.problem_name, problem_short: x.problem_short,
          submission_id: x.sid, time: x.time, score: x.score !== null && !isNaN(x.score) ? x.score : 100, status: x.status,
          filename: decodeURIComponent(fn), round: p ? p.round : null, code: await r.text() });
      }
      const shorts = [...new Set(acc.map((x) => (x.problem_short || '').toLowerCase()).filter(Boolean))];
      out.statements[cid] = {};
      let s = 0;
      for (const sh of shorts) {
        s++;
        if (HAVE_ST.has(`${cid}/${sh}`)) continue;
        const p = pmap.get(sh);
        say(`[${ci}/${contests.size}] ${cname}: treść ${s}/${shorts.length} (${sh})`);
        const r = await get(p ? p.url : `/c/${cid}/p/${sh}/`);
        if (!r.ok) continue;
        const ct = (r.headers.get('content-type') || '').toLowerCase();
        if (ct.includes('pdf')) { if (WITH_PDF) out.statements[cid][sh] = { type: 'pdf', b64: b64(await r.arrayBuffer()) }; }
        else if (ct.includes('html')) {
          const d = parse(await r.text());
          const main = d.querySelector('section.main-content') || d.querySelector('article') || d.body;
          main.querySelectorAll('script,style,form,nav,.modal').forEach((e) => e.remove());
          out.statements[cid][sh] = { type: 'html', html: main.innerHTML };
        }
      }
    }
    const blob = new Blob([JSON.stringify(out)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `oiforge-sio2-${location.hostname}-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    say(`Gotowe! Nowych programów: ${out.programs.length}. Plik ${a.download} jest w „Pobranych” — zaimportuj go w OI Forge (Moje programy → Synchronizacja → Importuj JSON).`);
  } catch (e) {
    say('Błąd: ' + (e && e.message ? e.message : e) + ' — to, co pobrane, możesz zapisać ponownym uruchomieniem.');
    console.error(e);
  }
})();"""


def console_script(base_url: str | None = None, root=None) -> dict:
    root = Path(root or PR.programs_dir())
    base = normalize_base(base_url or default_base(root))
    source = source_of(base)
    data = PR.load_index(root)
    known = sorted({str(r.get("submission_id")) for r in data["programs"] if r.get("source") == source})
    have = set()
    for r in data["programs"]:
        if r.get("source") == source and r.get("contest_id") and r.get("problem_short"):
            st = PR.find_statement(root, source, r["contest_id"], str(r["problem_short"]))
            if any(st.values()):
                have.add(f"{r['contest_id']}/{str(r['problem_short']).lower()}")
    js = CONSOLE_JS.replace("__KNOWN__", json.dumps(known)).replace("__HAVE_ST__", json.dumps(sorted(have)))
    bookmarklet = "javascript:" + urllib.parse.quote(re.sub(r"^\s*//[^\n]*\n", "", js, flags=re.M), safe="")
    return {
        "script": js, "bookmarklet": bookmarklet, "base_url": base, "known": len(known),
        "instructions": [
            f"Otwórz {base} w przeglądarce i zaloguj się na swoje konto.",
            "Otwórz konsolę deweloperską: F12 albo Ctrl+Shift+J (Windows/Linux), Cmd+Option+J (macOS, Chrome/Edge), "
            "Cmd+Option+C (Safari — najpierw włącz menu „Programowanie”).",
            "Przejdź do zakładki „Console”. Jeśli przeglądarka blokuje wklejanie, wpisz allow pasting i naciśnij Enter.",
            "Wklej skrypt (przycisk „Kopiuj skrypt”) i naciśnij Enter. W prawym dolnym rogu strony zobaczysz postęp.",
            "Na końcu przeglądarka zapisze plik oiforge-sio2-….json w „Pobranych”.",
            "Wróć tutaj i kliknij „Importuj JSON”, wskazując ten plik. Treści w PDF zostaną od razu zamienione na tekst.",
        ],
    }


# ============================================================================================ HTTP
def handle_sync(b: dict) -> dict:
    with _run_lock:
        jid = _running.get("job")
        j = jobs.get(jid) if jid else None
        if j and j.status == "running":
            return {"job": jid, "already_running": True}
        opts = {k: b.get(k) for k in ("base_url", "username", "password", "sessionid", "remember", "contests",
                                      "delay", "statements", "full")}
        opts["base_url"] = normalize_base(opts.get("base_url") or store.settings().get("sio2", {}).get("base_url"))
        if opts.get("delay") is not None:
            try:
                opts["delay"] = max(0.1, min(5.0, float(opts["delay"])))
            except (TypeError, ValueError):
                opts["delay"] = 0.35
        jid = jobs.submit(sync, opts, title="Synchronizacja z SIO2")
        _running["job"] = jid
        b.pop("password", None)
        return {"job": jid}


def handle_status(_q: dict) -> dict:
    root = PR.programs_dir()
    base = default_base(root)
    saved = store.get(SESSION_KEY, {}) or {}
    st = load_state(root)
    data = PR.load_index(root)
    jid = _running.get("job")
    j = jobs.get(jid) if jid else None
    by_source: dict[str, int] = {}
    for r in data["programs"]:
        by_source[r.get("source") or "?"] = by_source.get(r.get("source") or "?", 0) + 1
    return {"base_url": base, "szkopul": SZKOPUL_BASE,
            "saved_session": {"username": saved.get("username"), "saved_at": saved.get("saved_at"),
                              "base_url": saved.get("base_url")} if saved.get("cookie") else None,
            "last_sync": (st.get("_last") or {}).get(source_of(normalize_base(base))),
            "running_job": jid if j and j.status == "running" else None,
            "programs": len(data["programs"]), "by_source": by_source, "fetched_at": data.get("fetched_at"),
            "pending_statements": len(pending_statements(root)), "root": str(root)}


def handle_forget(_b: dict) -> dict:
    store.delete(SESSION_KEY)
    return {"forgotten": True}


def handle_pending(_q: dict) -> dict:
    items = pending_statements()
    return {"items": items, "count": len(items)}


def handle_statement_text(b: dict) -> dict:
    items = b.get("items")
    if not isinstance(items, list):
        items = [{"key": b.get("key"), "text": b.get("text")}]
    saved, errors = 0, []
    for it in items[:500]:
        if not isinstance(it, dict):
            continue
        try:
            saved += bool(save_statement_text(it.get("key"), it.get("text") or ""))
        except ApiError as e:
            errors.append(f"{it.get('key')}: {e.message}")
    return {"saved": saved, "errors": errors, "pending": len(pending_statements())}


def handle_import_json(b: dict) -> dict:
    obj = b
    if isinstance(b.get("json"), str):
        try:
            obj = json.loads(b["json"])
        except json.JSONDecodeError as e:
            raise ApiError(f"Plik nie jest poprawnym JSON-em: {e}") from e
    elif isinstance(b.get("data"), (dict, list)):
        obj = b["data"]
    if isinstance(obj, dict) and obj.get("format") != "oiforge-sio2" and isinstance(obj.get("programs"), list) and \
            not any(isinstance(p, dict) and "code" in p for p in obj["programs"][:20]):
        raise ApiError("Ten JSON nie zawiera kodów programów. Użyj pliku ze skryptu OI Forge do konsoli.")
    return import_export(obj)


def handle_import_zip(b: dict) -> dict:
    data = b.get("b64") or b.get("data") or ""
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError) as e:
        raise ApiError("Niepoprawne dane pliku (base64).") from e
    if not raw:
        raise ApiError("Pusty plik.")
    return import_zip(raw, str(b.get("name") or "import.zip"))


def handle_import_folder(b: dict) -> dict:
    return import_folder(b.get("path") or "")


def handle_script(q: dict) -> dict:
    return console_script(q.get("base_url"))


ROUTES = {
    "POST /api/programs/sync": handle_sync,
    "GET /api/programs/sync/status": handle_status,
    "POST /api/programs/sync/forget": handle_forget,
    "GET /api/programs/sync/script": handle_script,
    "GET /api/programs/statements/pending": handle_pending,
    "POST /api/programs/statements/text": handle_statement_text,
    "POST /api/programs/import/json": handle_import_json,
    "POST /api/programs/import/zip": handle_import_zip,
    "POST /api/programs/import/folder": handle_import_folder,
}

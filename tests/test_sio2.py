"""Testy synchronizacji z SIO2: parsery (zapisane strony + syntetyczne), symulowany serwer oioioi
(logowanie z CSRF, paginacja, pobieranie, rundy, treści), przyrostowość, importy (ZIP/folder/JSON), skrypt do konsoli.

Uruchom: python3 -m unittest tests.test_sio2 -v
"""
from __future__ import annotations

import base64
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OIFORGE_NO_WARMUP", "1")
os.environ.setdefault("OIFORGE_ALGODETECT", "off")      # deterministycznie: bez liczenia algodetect w tle

from oiforge.core import jobs  # noqa: E402
from oiforge.tools import _sio2_parse as P  # noqa: E402
from oiforge.tools import programs as PR  # noqa: E402
from oiforge.tools import sio2 as S  # noqa: E402



# ============================================================================================ parsery syntetyczne
PL_SUBMISSIONS = """<html><body><header><a href="#" data-post-url="/logout/">Wyloguj</a></header>
<table class="table submission"><thead><tr><th class="submission__margin"></th><th>Czas zgłoszenia</th><th>Zadanie</th>
<th>Rodzaj</th><th>Status</th><th>Wynik</th><th>Szczegóły</th></tr></thead><tbody>
<tr><td></td><td><a href="/c/k/s/77/">2026-01-02 10:00:00</a></td><td>Wieże (wie)</td><td>Normalne</td>
<td>Wstępne testy: OK</td><td>100</td><td><a href="/c/k/s/77/">Szczegóły</a></td></tr>
<tr><td></td><td><a href="/c/k/s/76/">2026-01-01 10:00:00</a></td><td>Wieże (wie)</td><td>Normalne</td>
<td>Wstępne testy: OK</td><td>7 / 10</td><td><a href="/c/k/s/76/">Szczegóły</a></td></tr>
</tbody></table>
<ul class="pagination"><li class="page-item active"><span class="page-link">1</span></li></ul></body></html>"""

ACM_SUBMISSIONS = """<table class="table submission"><thead><tr><th>Submission time</th><th>Problem</th><th>Status</th>
</tr></thead><tbody><tr><td><a href="/c/acm/s/9/">2026-03-01 12:00:00</a></td><td>Lody (lo)</td><td>Accepted</td></tr>
<tr><td><a href="/c/acm/s/8/">2026-03-01 11:00:00</a></td><td>Lody (lo)</td><td>Wrong answer</td></tr>
<tr><td><a href="/c/acm/s/7/">2026-03-01 10:00:00</a></td><td>Kredki (has)</td><td>OK</td></tr></tbody></table>"""


class TestParsersSynthetic(unittest.TestCase):
    def test_polish_headers(self):
        p = P.parse_submissions(PL_SUBMISSIONS)
        self.assertEqual([r["submission_id"] for r in p["rows"]], ["77", "76"])
        self.assertEqual(p["rows"][0]["problem_name"], "Wieże")
        self.assertEqual(p["rows"][0]["problem_short"], "wie")
        self.assertEqual(p["rows"][0]["score"], 100)
        self.assertEqual(p["rows"][1]["score"], 70)          # „7 / 10” → 70 pkt
        self.assertEqual([P.is_accepted(r) for r in p["rows"]], [True, False])

    def test_acm_without_scores(self):
        p = P.parse_submissions(ACM_SUBMISSIONS)
        self.assertEqual(len(p["rows"]), 3)
        self.assertFalse(p["rows"][0]["has_score_column"])
        self.assertEqual([P.is_accepted(r) for r in p["rows"]], [True, False, True])

    def test_helpers(self):
        self.assertEqual(P.split_problem_cell("  Odległości wierzchołków (bfs) "), ("Odległości wierzchołków", "bfs"))
        self.assertEqual(P.split_problem_cell("Zadanie (K,N)-Skoczek [B] (kns)"), ("Zadanie (K,N)-Skoczek [B]", "kns"))
        self.assertEqual(P.clean_round("Dzień 5 - Dijkstra (22 August 2026, 09:00 - 14:00)"), "Dzień 5 - Dijkstra")
        self.assertEqual(P.clean_round("Find & Union"), "Find & Union")
        self.assertEqual(P.filename_from_content_disposition('attachment; filename="alc.cpp"'), "alc.cpp")
        self.assertEqual(P.filename_from_content_disposition("attachment; filename*=UTF-8''%C5%BCar.cpp"), "żar.cpp")
        self.assertIsNone(P.parse_score(""))
        self.assertEqual(P.parse_score(" 100.00 "), 100.0)

    def test_html_to_text(self):
        html = """<html><body><nav>MENU</nav><section class="main-content"><h1>Apteka</h1><p>Jaś stoi
        w kolejce.</p><h2>Wejście</h2><p>Liczba <i>n</i>.</p><pre>4
5 2 4 3
</pre><footer>stopka</footer></section></body></html>"""
        t = P.html_to_text(html)
        self.assertIn("Apteka", t)
        self.assertIn("Jaś stoi w kolejce.", t)
        self.assertIn("Wejście", t)
        self.assertIn("4\n5 2 4 3", t)
        self.assertNotIn("MENU", t)
        self.assertNotIn("stopka", t)


# ============================================================================================ symulowany oioioi
CODE_A_NEW = """#include <bits/stdc++.h>
using namespace std;
int main(){ long long n; cin >> n; long long l = 0, r = 2e9;
  while (l < r) { long long mid = (l + r) / 2; if (mid * mid >= n) r = mid; else l = mid + 1; }
  cout << l << "\\n"; }
"""
CODE_A_OLD = "#include <bits/stdc++.h>\nint main(){ return 0; }\n"
CODE_B = "#include <bits/stdc++.h>\nusing namespace std;\nint main(){ vector<int> v(5); sort(v.begin(), v.end()); }\n"
CODE_DRZ = "n = int(input())\nprint(n * 2)\n"


class FakeOioioi:
    """Minimalna atrapa serwera oioioi: logowanie Django (CSRF cookie + token + Referer), sesja, paginacja."""

    def __init__(self):
        self.user, self.password = "uczen", "tajne123"
        self.csrf_cookie = "csrfCOOKIE123"
        self.csrf_form = "csrfFORMabc"
        self.sessions: set[str] = set()
        self.log: list[tuple[str, str, dict]] = []
        self.login_posts: list[dict] = []
        self.subs = {
            "kurs-a": [  # od najnowszych
                ("1005", "2026-02-03 10:00:00", "Pierwiastek (aaa)", "Initial tests: OK", "100", CODE_A_NEW),
                ("1004", "2026-02-02 10:00:00", "Bajtek (bbb)", "Initial tests: OK", "40", None),
                ("1003", "2026-02-01 10:00:00", "Bajtek (bbb)", "Initial tests: OK", "100", CODE_B),
                ("1002", "2026-01-05 10:00:00", "Pierwiastek (aaa)", "Initial tests: OK", "100", CODE_A_OLD),
                ("1001", "2026-01-04 10:00:00", "Ciągi (ccc)", "Compilation failed", "", None),
            ],
            "kurs-acm": [
                ("2002", "2026-03-01 12:00:00", "Drzewo (drz)", "OK", None, CODE_DRZ),
                ("2001", "2026-03-01 11:00:00", "Drzewo (drz)", "Zła odpowiedź", None, None),
            ],
            "pusty": [],
        }
        # archiwum zadań (problemset, jak na Szkopule): (sid, czas, zadanie, status, wynik, kod, klucz zadania)
        self.archive: list[tuple] = []
        self.page_size = 3
        self.base = ""

    # ---------------------------------------------------------------- strony
    def layout(self, body: str, logged: bool) -> str:
        user = ('<strong class="username" id="username">Uczeń Testowy</strong>'
                '<a class="dropdown-item" href="#" data-post-url="/logout/">Log out</a>') if logged else \
            ('<form id="navbar-login" action="/login/" method="post"><input type="hidden" '
             f'name="csrfmiddlewaretoken" value="{self.csrf_form}"><input type="text" name="username">'
             '<input type="password" name="password"></form>')
        return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>SIO2</title></head><body>'
                f'<header id="oioioi-navbar">{user}</header><section class="col-lg-9 main-content">{body}</section>'
                f'<footer>Powered by OIOIOI</footer></body></html>')

    def login_page(self, error: bool = False) -> str:
        err = '<ul class="errorlist nonfield"><li>Wprowadź poprawne dane logowania.</li></ul>' if error else ""
        return self.layout(f'<h1>Zaloguj się</h1>{err}<form method="post"><input type="hidden" '
                           f'name="csrfmiddlewaretoken" value="{self.csrf_form}"><input type="text" name="username">'
                           '<input type="password" name="password"><input type="hidden" name="next" value="">'
                           '<button type="submit">Zaloguj</button></form>', False)

    def contests_page(self) -> str:
        rows = "".join(f'<tr><td>{c}</td><td><a class="fill-clickable" href="/c/{c}/">Kurs {c.upper()}</a></td></tr>'
                       for c in self.subs)
        return self.layout(f"<h1>Select contest</h1><table class='table'><thead><tr><th>ID</th><th>Name</th></tr>"
                           f"</thead><tbody>{rows}</tbody></table>", True)

    def submissions_page(self, cid: str, page: int) -> str:
        subs = self.subs[cid]
        if not subs:
            return self.layout('<h1>My submissions</h1><div class="text-center">You have not submitted anything yet.'
                               '</div>', True)
        chunk = subs[(page - 1) * self.page_size: page * self.page_size]
        acm = cid == "kurs-acm"
        if acm:
            head = "<th></th><th>Czas zgłoszenia</th><th>Zadanie</th><th>Rodzaj</th><th>Status</th><th>Szczegóły</th>"
        else:
            head = "<th></th><th>Submission time</th><th>Problem</th><th>Kind</th><th>Status</th><th>Score</th>" \
                   "<th>Details</th>"
        body = ""
        for sid, tm, prob, status, score, _code in chunk:
            sc = "" if acm else f'<td id="submission{sid}-score">{score}</td>'
            body += (f'<tr id="report{sid}row"><td id="submission{sid}-margin"></td>'
                     f'<td id="submission{sid}-link"><a href="/c/{cid}/s/{sid}/">{tm}</a></td>'
                     f'<td id="submission{sid}-problem-instance">{prob}</td><td id="submission{sid}-kind">Normal</td>'
                     f'<td id="submission{sid}-status">{status}</td>{sc}'
                     f'<td><a href="/c/{cid}/s/{sid}/">Details</a></td></tr>'
                     f'<tr class="padded-collapsing-content"><td colspan="100">No reports</td></tr>')
        pages = (len(subs) + self.page_size - 1) // self.page_size
        pag = "".join(f'<li class="page-item active"><span class="page-link">{p}</span></li>' if p == page else
                      f'<li class="page-item"><a class="page-link" href="?page={p}">{p}</a></li>'
                      for p in range(1, pages + 1))
        return self.layout(f'<table class="table table-sm submission"><thead><tr>{head}</tr></thead><tbody>{body}'
                           f'</tbody></table><ul class="justify-content-center pagination">{pag}</ul>', True)

    def all_submissions_page(self, page: int) -> str:
        """/submissions/ — wszystkie zgłoszenia użytkownika: z konkursów (/c/<id>/s/<sid>/) i z archiwum (/s/<sid>/)."""
        items = [(sid, tm, prob, st, sc, f"/c/{cid}/s/{sid}/", None)
                 for cid, subs in self.subs.items() for sid, tm, prob, st, sc, _c in subs]
        items += [(sid, tm, prob, st, sc, f"/s/{sid}/", key) for sid, tm, prob, st, sc, _c, key in self.archive]
        items.sort(key=lambda x: -int(x[0]))
        if not items:
            return self.layout('<h1>All submissions</h1><div class="text-center">You have not submitted anything yet.'
                               '</div>', True)
        chunk = items[(page - 1) * self.page_size: page * self.page_size]
        body = ""
        for sid, tm, prob, status, score, link, key in chunk:
            pcell = f'<a href="/problemset/problem/{key}/site/">{prob}</a>' if key else prob
            body += (f'<tr id="report{sid}row"><td id="submission{sid}-margin"></td>'
                     f'<td id="submission{sid}-link"><a href="{link}">{tm}</a></td>'
                     f'<td id="submission{sid}-problem-instance">{pcell}</td><td id="submission{sid}-kind">Normal</td>'
                     f'<td id="submission{sid}-status">{status}</td><td id="submission{sid}-score">{score or ""}</td>'
                     f'<td><a href="{link}">Details</a></td></tr>')
        pages = (len(items) + self.page_size - 1) // self.page_size
        pag = "".join(f'<li class="page-item active"><span class="page-link">{p}</span></li>' if p == page else
                      f'<li class="page-item"><a class="page-link" href="?page={p}">{p}</a></li>'
                      for p in range(1, pages + 1))
        head = "<th></th><th>Submission time</th><th>Problem</th><th>Kind</th><th>Status</th><th>Score</th><th>Details</th>"
        return self.layout(f'<h1>All submissions</h1><table class="table table-sm submission"><thead><tr>{head}</tr>'
                           f'</thead><tbody>{body}</tbody></table><ul class="pagination">{pag}</ul>', True)

    def problems_page(self, cid: str, page: int) -> str:
        if cid == "kurs-a":
            data = {1: ("Wyszukiwanie binarne (1 October 2025, 12:00 - )", [("aaa", "Pierwiastek"), ("bbb", "Bajtek")]),
                    2: ("Dijkstra (8 October 2025, 12:00 - )", [("ccc", "Ciągi")])}
            pages = 2
        else:
            data = {1: (None, [("drz", "Drzewo")])}
            pages = 1
        rnd, items = data[page]
        rows = ""
        if rnd:
            name, em = rnd.split(" (", 1)
            rows += (f'<tr class="problemlist-subheader"><td colspan=5><strong>{name}</strong><em> ({em} </em></td>'
                     f'</tr>')
        for short, name in items:
            rows += (f'<tr id="{short}"><td>{short}</td><td><a href="/c/{cid}/p/{short}/">{name}</a></td>'
                     f'<td class="text-end">10 of 10</td><td><a href="/c/{cid}/submit/1/">Submit</a></td></tr>')
        pag = "".join(f'<li class="page-item active"><span class="page-link">{p}</span></li>' if p == page else
                      f'<li class="page-item"><a class="page-link" href="?page={p}">{p}</a></li>'
                      for p in range(1, pages + 1))
        return self.layout(f'<ul class="pagination">{pag}</ul><table class="table table-striped"><thead><tr><th></th>'
                           f'<th>Name</th><th>Tries left</th><th></th></tr></thead><tbody>{rows}</tbody></table>', True)


def make_handler(fake: FakeOioioi):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # cicho
            pass

        def _cookies(self) -> dict:
            out = {}
            for part in (self.headers.get("Cookie") or "").split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    out[k] = v
            return out

        def _logged(self) -> bool:
            return self._cookies().get("sessionid") in fake.sessions

        def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, loc, headers=None):
            h = {"Location": loc}
            h.update(headers or {})
            self._send(302, b"", headers=h)

        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            page = int(q.get("page", ["1"])[0])
            fake.log.append(("GET", self.path, dict(self.headers)))
            path = u.path
            if path == "/login/":
                return self._send(200, fake.login_page(), headers={
                    "Set-Cookie": f"csrftoken={fake.csrf_cookie}; Path=/; SameSite=Lax"})
            if not self._logged():
                return self._redirect(f"/login/?next={urllib.parse.quote(path)}")
            if path == "/":
                return self._send(200, fake.layout("<h1>Witaj</h1>", True))
            if path == "/contest/":
                return self._send(200, fake.contests_page())
            if path == "/submissions/":
                if fake.archive is None:
                    return self._send(404, "404")
                return self._send(200, fake.all_submissions_page(page))
            m = re.match(r"^/s/(\d+)/download/$", path)
            if m:
                for sid, _tm, prob, _st, _sc, code, _key in fake.archive or []:
                    if sid == m.group(1) and code is not None:
                        short = re.search(r"\((\w+)\)", prob).group(1)
                        return self._send(200, code.encode("utf-8"), "application/octet-stream",
                                          {"Content-Disposition": f'attachment; filename="{short}.cpp"'})
                return self._send(404, "nie ma")
            m = re.match(r"^/c/([\w-]+)/submissions/$", path)
            if m:
                return self._send(200, fake.submissions_page(m.group(1), page))
            m = re.match(r"^/c/([\w-]+)/s/(\d+)/download/$", path)
            if m:
                cid, sid = m.groups()
                for s in fake.subs.get(cid, []):
                    if s[0] == sid and s[5] is not None:
                        ext = ".py" if s[5].startswith("n =") else ".cpp"
                        short = re.search(r"\((\w+)\)", s[2]).group(1)
                        return self._send(200, s[5].encode("utf-8"), "application/octet-stream",
                                          {"Content-Disposition": f'attachment; filename="{short}{ext}"'})
                return self._send(404, "nie ma")
            m = re.match(r"^/c/([\w-]+)/p/$", path)
            if m:
                return self._send(200, fake.problems_page(m.group(1), page))
            m = re.match(r"^/c/([\w-]+)/p/(\w+)/$", path)
            if m:
                cid, short = m.groups()
                if short == "aaa":
                    return self._send(200, b"%PDF-1.4\n% fake pdf\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n",
                                      "application/pdf")
                if short in ("bbb", "drz"):
                    return self._send(200, fake.layout(
                        f"<h1>Zadanie {short}</h1><p>Bajtek ma ciąg liczb i chce go posortować rosnąco. Pomóż mu.</p>"
                        "<h2>Wejście</h2><p>W pierwszym wierszu liczba n.</p><h2>Wyjście</h2><p>Posortowany ciąg.</p>",
                        True))
                return self._send(404, "brak")
            return self._send(404, "404")

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            body = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
            fake.log.append(("POST", self.path, dict(self.headers)))
            if self.path.startswith("/login/"):
                form = {k: v[0] for k, v in body.items()}
                fake.login_posts.append({k: ("***" if k == "password" else v) for k, v in form.items()})
                ref = self.headers.get("Referer") or ""
                if self._cookies().get("csrftoken") != fake.csrf_cookie or \
                        form.get("csrfmiddlewaretoken") != fake.csrf_form or not ref.startswith(fake.base):
                    return self._send(403, "CSRF verification failed")
                if form.get("username") == fake.user and form.get("password") == fake.password:
                    sid = f"sess{len(fake.sessions) + 1}xyz"
                    fake.sessions.add(sid)
                    return self._redirect(form.get("next") or "/", {"Set-Cookie": f"sessionid={sid}; Path=/; HttpOnly"})
                return self._send(200, fake.login_page(error=True))
            return self._send(404, "404")
    return H


class FakeServerMixin:
    @classmethod
    def start_fake(cls):
        cls.fake = FakeOioioi()
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.fake))
        cls.httpd.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.fake.base = cls.base
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def stop_fake(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()


class TestSyncWithFakeServer(FakeServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_fake()

    @classmethod
    def tearDownClass(cls):
        cls.stop_fake()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oiforge-sio2-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def opts(self, **kw):
        o = {"base_url": self.base, "username": "uczen", "password": "tajne123", "delay": 0, "_root": str(self.tmp),
             "save_settings": False, "remember": False}
        o.update(kw)
        return o

    def downloads(self):
        return [p for m, p, _ in self.fake.log if m == "GET" and p.endswith("/download/")]

    def test_full_sync_then_incremental(self):
        self.fake.log.clear()
        job, res = jobs.run_sync(S.sync, self.opts(), title="test")
        self.assertEqual(res["new_programs"], 4, res)
        self.assertEqual(res["contests"], 3)
        # --- logowanie: CSRF cookie + token z formularza + Referer + User-Agent
        post = self.fake.login_posts[-1]
        self.assertEqual(post["csrfmiddlewaretoken"], self.fake.csrf_form)
        self.assertEqual(post["password"], "***")
        self.assertIn("next", post)
        uas = {h.get("User-Agent", "") for _, _, h in self.fake.log}
        self.assertTrue(all(ua.startswith("OI Forge") for ua in uas), uas)
        # --- index.json w tym samym formacie co prawdziwy
        data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
        progs = {p["submission_id"]: p for p in data["programs"]}
        self.assertEqual(set(progs), {"1005", "1003", "1002", "2002"})
        a = progs["1005"]
        for key in ("source", "contest_id", "contest_name", "problem_name", "problem_short", "submission_id", "time",
                    "score", "status", "lang", "path", "url", "lines", "round"):
            self.assertIn(key, a)
        self.assertEqual(a["source"], S.source_of(self.base))
        self.assertEqual((a["problem_name"], a["problem_short"], a["score"]), ("Pierwiastek", "aaa", 100))
        self.assertEqual(a["round"], "Wyszukiwanie binarne")
        self.assertEqual(a["url"], f"{self.base}/c/kurs-a/s/1005/")
        self.assertEqual((self.tmp / a["path"]).read_text(encoding="utf-8"), CODE_A_NEW)
        self.assertTrue(a["path"].endswith("kurs-a/aaa__1005.cpp"))
        self.assertEqual(progs["2002"]["lang"], "python")              # ACM: status OK, plik .py
        self.assertEqual(progs["2002"]["score"], 100)
        self.assertNotIn("1004", progs)                                 # 40 pkt — pomijamy
        # --- rundy (problems.json) i treści: PDF → do ekstrakcji, HTML → tekst w Pythonie
        probs = json.loads((self.tmp / S.source_of(self.base) / "problems.json").read_text(encoding="utf-8"))
        self.assertEqual([p["short"] for p in probs["kurs-a"]["problems"]], ["aaa", "bbb", "ccc"])
        self.assertEqual(probs["kurs-a"]["problems"][2]["round"], "Dijkstra (8 October 2025, 12:00 - )")
        sd = PR.statement_dir(self.tmp, S.source_of(self.base), "kurs-a")
        self.assertTrue((sd / "aaa.pdf").is_file())
        self.assertTrue((sd / "bbb.html").is_file())
        pend = S.pending_statements(self.tmp)
        self.assertEqual([p["short"] for p in pend], ["aaa"])
        self.assertTrue(S.save_statement_text(pend[0]["key"], "Pierwiastek\nOblicz sufit pierwiastka.", self.tmp))
        self.assertEqual(S.pending_statements(self.tmp), [])
        # --- zdarzenia na żywo
        types = Counter(e.get("type") for e in job.events)
        self.assertEqual(types["program"], 4)
        self.assertGreaterEqual(types["contest"], 3)
        # --- druga synchronizacja: przyrostowo, bez pobierania i bez strony 2 zgłoszeń
        self.fake.log.clear()
        _, res2 = jobs.run_sync(S.sync, self.opts(), title="test2")
        self.assertEqual(res2["new_programs"], 0)
        self.assertEqual(self.downloads(), [])
        self.assertNotIn("/c/kurs-a/submissions/?page=2", [p for _, p, _ in self.fake.log])
        # --- nowe zgłoszenie → pobieramy tylko je
        self.fake.subs["kurs-a"].insert(0, ("1006", "2026-02-04 10:00:00", "Ciągi (ccc)", "Initial tests: OK",
                                            "100", "// nowy\nint main(){}\n"))
        try:
            self.fake.log.clear()
            _, res3 = jobs.run_sync(S.sync, self.opts(), title="test3")
            self.assertEqual(res3["new_programs"], 1)
            self.assertEqual(self.downloads(), ["/c/kurs-a/s/1006/download/"])
            data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
            new = [p for p in data["programs"] if p["submission_id"] == "1006"][0]
            self.assertEqual(new["round"], "Dijkstra")
        finally:
            self.fake.subs["kurs-a"].pop(0)
        # --- wyszukiwarka na zsynchronizowanych danych
        PR.set_root(self.tmp)
        try:
            res = PR.search("binserch", k=3)
            self.assertTrue(res)
            self.assertEqual(res[0]["problem_short"], "aaa")
        finally:
            PR.set_root(None)

    def test_wrong_password(self):
        with self.assertRaises(RuntimeError) as cm:
            jobs.run_sync(S.sync, self.opts(password="zle"), title="zle")
        self.assertIn("Nieprawidłowy login lub hasło", str(cm.exception))
        self.assertFalse((self.tmp / "index.json").exists())

    def test_session_cookie(self):
        self.fake.sessions.add("wklejonasesja123")
        o = self.opts(username="", password="", sessionid="sessionid=wklejonasesja123; csrftoken=x",
                      contests="kurs-acm")
        _, res = jobs.run_sync(S.sync, o, title="cookie")
        self.assertEqual(res["new_programs"], 1)
        self.assertEqual(res["contests"], 1)
        with self.assertRaises(RuntimeError) as cm:
            jobs.run_sync(S.sync, self.opts(username="", password="", sessionid="nieznana"), title="zla sesja")
        self.assertIn("Sesja", str(cm.exception))

    def test_no_credentials(self):
        with self.assertRaises(RuntimeError) as cm:
            jobs.run_sync(S.sync, self.opts(username="", password="", base_url="http://127.0.0.1:9"), title="brak")
        self.assertIn("login", str(cm.exception).lower())


class TestArchiveSync(FakeServerMixin, unittest.TestCase):
    """Szkopuł i inne serwery SIO2: rozwiązania z archiwum zadań (poza konkursami) z ogólnej listy /submissions/."""

    @classmethod
    def setUpClass(cls):
        cls.start_fake()

    @classmethod
    def tearDownClass(cls):
        cls.stop_fake()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oiforge-arch-"))
        self.saved_subs = self.fake.subs
        self.fake.archive = [
            ("3002", "2026-04-02 10:00:00", "Kolorowanie (kol)", "OK", "100", "// archiwum\nint main(){}\n", "Ab3x"),
            ("3001", "2026-04-01 10:00:00", "Kolorowanie (kol)", "Wrong answer", "0", None, "Ab3x"),
        ]

    def tearDown(self):
        self.fake.subs = self.saved_subs
        self.fake.archive = []
        shutil.rmtree(self.tmp, ignore_errors=True)

    def opts(self, **kw):
        o = {"base_url": self.base, "username": "uczen", "password": "tajne123", "delay": 0, "_root": str(self.tmp),
             "save_settings": False, "remember": False}
        o.update(kw)
        return o

    def test_archive_programs_are_imported(self):
        _, res = jobs.run_sync(S.sync, self.opts(), title="archiwum")
        self.assertEqual(res["new_programs"], 5, res)        # 4 z konkursów + 1 z archiwum (bez duplikatów)
        data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
        arch = [p for p in data["programs"] if p["contest_id"] == S.ARCHIVE_ID]
        self.assertEqual([p["submission_id"] for p in arch], ["3002"])
        a = arch[0]
        self.assertEqual((a["contest_name"], a["problem_short"], a["problem_name"]), (S.ARCHIVE_NAME, "kol", "Kolorowanie"))
        self.assertEqual(a["url"], f"{self.base}/s/3002/")
        self.assertEqual((self.tmp / a["path"]).read_text(encoding="utf-8"), "// archiwum\nint main(){}\n")
        # druga synchronizacja: nic nowego
        self.fake.log.clear()
        _, res2 = jobs.run_sync(S.sync, self.opts(), title="archiwum2")
        self.assertEqual(res2["new_programs"], 0)
        self.assertFalse([q for m, q, _ in self.fake.log if q.endswith("/download/")])

    def test_only_archive_without_contests(self):
        self.fake.subs = {}                                   # konto bez konkursów — tylko archiwum (typowe na Szkopule)
        _, res = jobs.run_sync(S.sync, self.opts(), title="tylko-archiwum")
        self.assertEqual(res["new_programs"], 1, res)

    def test_server_without_global_list(self):
        self.fake.archive = None                              # starsze SIO2 bez /submissions/ — konkursy nadal działają
        _, res = jobs.run_sync(S.sync, self.opts(), title="bez-listy")
        self.assertEqual(res["new_programs"], 4, res)


class TestClient(unittest.TestCase):
    def test_normalize_base(self):
        self.assertEqual(S.normalize_base("sio2.example.pl/"), "https://sio2.example.pl")
        self.assertEqual(S.normalize_base("https://Szkopul.edu.pl/c/x/"), "https://szkopul.edu.pl")
        self.assertEqual(S.source_of("http://127.0.0.1:8000"), "127.0.0.1_8000")

    def test_set_session_variants(self):
        c = S.Sio2Client("https://sio2.example.pl")
        c.set_session("abc123def")
        self.assertEqual(c.cookie("sessionid"), "abc123def")
        c2 = S.Sio2Client("https://sio2.example.pl")
        c2.set_session("Cookie: csrftoken=t1; sessionid=zz9; lang=pl")
        self.assertEqual((c2.cookie("sessionid"), c2.cookie("csrftoken")), ("zz9", "t1"))

    def test_unreachable_is_friendly(self):
        c = S.Sio2Client("http://127.0.0.1:9", delay=0, timeout=1, retries=0)
        with self.assertRaises(S.SyncError) as cm:
            c.request("/contest/")
        self.assertIn("skryptu do konsoli", str(cm.exception))


# ============================================================================================ importy
class TestImports(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oiforge-imp-"))
        src = self.tmp / "sio2.test" / "kurs"
        (src / "statements").mkdir(parents=True)
        (self.tmp / "sio2.test" / "problems.json").write_text(json.dumps({"kurs": {"problems": [
            {"short": "alc", "name": "Alchemia", "round": "Rekurencja i teoria liczb (1 December 2025, 15:00 - )"}]}}),
            encoding="utf-8")
        (src / "statements" / "alc.txt").write_text("Alchemia\nAlicja miesza roztwory. NWD.", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zip_import_with_heuristics(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("moje/Dijkstra/zad_lot_brut.cpp", "// Zadanie: Lot samolotem\n#include <bits/stdc++.h>\n"
                                                          "int main(){priority_queue<int> q;}\n")
            z.writestr("moje/kolko/alc.cpp", "#include <bits/stdc++.h>\nint main(){}\n")
            z.writestr("moje/kolko/alc_kopia.cpp", "#include <bits/stdc++.h>\nint main(){}\n")   # duplikat treści
            z.writestr("moje/dp/plecak.py", "print(1)\n")
            z.writestr("__MACOSX/moje/._alc.cpp", "xx")
            z.writestr("moje/notatki.txt", "nie kod")
        res = S.import_zip(buf.getvalue(), "Moje rozwiązania.zip", root=self.tmp)
        self.assertEqual(res["added"], 3, res)
        self.assertEqual(res["duplicates"], 1)
        data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
        by = {p["problem_short"]: p for p in data["programs"]}
        self.assertEqual(by["lot"]["problem_name"], "Lot samolotem")
        self.assertEqual(by["lot"]["round"], "Dijkstra")
        self.assertEqual(by["alc"]["problem_name"], "Alchemia")             # dopasowane do problems.json
        self.assertEqual(by["alc"]["statement_ref"]["contest_id"], "kurs")
        self.assertEqual(by["plecak"]["lang"], "python")
        self.assertEqual(by["plecak"]["round"], "dp")
        self.assertTrue(all(p["source"] == "import" for p in data["programs"]))
        self.assertTrue((self.tmp / by["lot"]["path"]).is_file())
        # treść dopasowanego zadania widoczna w wyszukiwarce
        PR.set_root(self.tmp)
        try:
            g = PR.get(PR.program_id(by["alc"]))
            self.assertIn("Alicja", g["statement"])
            r = PR.search("dijkstra", k=3)
            self.assertEqual(r[0]["problem_short"], "lot")
        finally:
            PR.set_root(None)
        # ponowny import = same duplikaty
        res2 = S.import_zip(buf.getvalue(), "Moje rozwiązania.zip", root=self.tmp)
        self.assertEqual(res2["added"], 0)

    def test_folder_import(self):
        src = Path(tempfile.mkdtemp(prefix="oiforge-src-"))
        try:
            (src / "grafy").mkdir()
            (src / "grafy" / "bfs.cpp").write_text("#include <queue>\nint main(){}\n", encoding="utf-8")
            (src / ".git").mkdir()
            (src / ".git" / "x.cpp").write_text("int main(){}", encoding="utf-8")
            res = S.import_folder(str(src), root=self.tmp)
            self.assertEqual(res["added"], 1)
            data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(data["programs"][0]["round"], "grafy")
            with self.assertRaises(Exception):
                S.import_folder(str(src / "nie-ma"), root=self.tmp)
        finally:
            shutil.rmtree(src, ignore_errors=True)

    def test_json_export_import(self):
        pdf = b"%PDF-1.4\n%%EOF\n"
        obj = {"format": "oiforge-sio2", "version": 1, "base_url": "https://sio2.test",
               "programs": [{"contest_id": "kurs", "contest_name": "Kurs", "problem_name": "Alchemia",
                             "problem_short": "alc", "submission_id": "555", "time": "2026-01-01 10:00:00",
                             "score": 100, "status": "Initial tests: OK", "filename": "alc.cpp",
                             "round": "Rekurencja i teoria liczb (1 December 2025, 15:00 - )",
                             "code": "#include <bits/stdc++.h>\nint main(){ std::cout << std::__gcd(4, 6); }\n"},
                            {"contest_id": "kurs", "problem_short": "zzz", "submission_id": "556", "code": "int main(){}",
                             "problem_name": "Zet"},
                            {"contest_id": "../zly", "submission_id": "1", "code": "x"}],
               "problems": {"kurs": [{"short": "zzz", "name": "Zet", "round": "Grafy (2 October 2025, 10:00 - )"}]},
               "statements": {"kurs": {"zzz": {"type": "pdf", "b64": base64.b64encode(pdf).decode()},
                                       "alc": {"type": "html", "html": "<h1>X</h1>"}}}}
        res = S.import_export(obj, root=self.tmp)
        self.assertEqual(res["added"], 2)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["source"], "sio2.test")
        data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
        by = {p["submission_id"]: p for p in data["programs"]}
        self.assertEqual(by["555"]["round"], "Rekurencja i teoria liczb")
        self.assertEqual(by["556"]["round"], "Grafy")
        self.assertEqual(by["555"]["url"], "https://sio2.test/c/kurs/s/555/")
        self.assertTrue((PR.statement_dir(self.tmp, "sio2.test", "kurs") / "zzz.pdf").is_file())
        self.assertEqual(res["pending_statements"], 1)
        self.assertEqual(S.import_export(obj, root=self.tmp)["added"], 0)        # przyrostowo


class TestConsoleScript(unittest.TestCase):
    def test_script(self):
        tmp = Path(tempfile.mkdtemp(prefix="oiforge-js-"))
        try:
            (tmp / "index.json").write_text(json.dumps({"programs": [
                {"source": "sio2.example.pl", "contest_id": "k", "submission_id": "123", "problem_short": "a",
                 "path": "x.cpp"}]}), encoding="utf-8")
            cs = S.console_script("https://sio2.example.pl", root=tmp)
            self.assertIn('new Set(["123"])', cs["script"])
            self.assertIn("oiforge-sio2", cs["script"])
            self.assertTrue(cs["bookmarklet"].startswith("javascript:"))
            self.assertEqual(len(cs["instructions"]), 6)
            node = shutil.which("node")
            if node:
                f = tmp / "s.js"
                f.write_text(cs["script"], encoding="utf-8")
                r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

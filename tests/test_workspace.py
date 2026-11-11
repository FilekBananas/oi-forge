"""Testy edytora projektów (oiforge.tools.workspace).

Uruchom: python3 -m unittest tests.test_workspace -v

Wszystko działa na TYMCZASOWYM katalogu (OIFORGE_USER_DIR + workspace.set_root) — prawdziwy user_data
nie jest dotykany. Sprawdza bezpieczeństwo ścieżek (`..`, ścieżki absolutne, dowiązania), nazwy Windows,
create/save/rename/delete→kosz→restore, konflikt zapisu, new_task, testy (OK/WA/TLE/CE), szukanie i HTTP.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="oiforge-ws-")
os.environ.setdefault("OIFORGE_USER_DIR", _TMP)
os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge import config  # noqa: E402
from oiforge.server_api import ApiError  # noqa: E402
from oiforge.tools import workspace as W  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HAS_CXX = bool(config.CXX)
PORT = 8813

GOOD = """#include <bits/stdc++.h>
using namespace std;
int main(){ long long n, s = 0, x; cin >> n; while (n--) { cin >> x; s += x; } cout << s << "\\n"; }
"""
BAD = """#include <bits/stdc++.h>
using namespace std;
int main(){ int n, s = 0, x; cin >> n; while (n--) { cin >> x; s += x; } cout << s + 1 << "\\n"; }
"""
SLOW = """#include <bits/stdc++.h>
int main(){ volatile unsigned long long x = 0; while (true) x++; }
"""
NOT_CPP = "int main( { return 0 }"


def tearDownModule():  # noqa: N802
    W.set_root(None)
    shutil.rmtree(_TMP, ignore_errors=True)


class WsCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_TMP, prefix="ws-")) / "workspace"
        W.set_root(self.dir)

    def tearDown(self):
        W.set_root(None)
        shutil.rmtree(self.dir.parent, ignore_errors=True)

    def write(self, rel: str, text: str) -> Path:
        p = self.dir.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


# =============================================================================================== ścieżki
class TestPathSafety(WsCase):
    def test_parent_escape_rejected(self):
        W.ensure_root()
        for bad in ["../x.cpp", "a/../../x.cpp", "..", "a/b/../../../etc/passwd", "..\\..\\x.cpp", "a\\..\\..\\x"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ApiError):
                    W.resolve(bad)
                with self.assertRaises(ApiError):
                    W.create(bad, "file")
                with self.assertRaises(ApiError):
                    W.save_file(bad, "x")

    def test_absolute_rejected(self):
        W.ensure_root()
        for bad in ["/etc/passwd", "C:\\Windows\\win.ini", "c:/x.txt", "~/x", "//server/share/x", "\\\\server\\x"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ApiError):
                    W.read_file(bad)
                with self.assertRaises(ApiError):
                    W.save_file(bad, "x")

    def test_trash_not_accessible_directly(self):
        W.ensure_root()
        with self.assertRaises(ApiError):
            W.save_file(".trash/x.cpp", "x")
        with self.assertRaises(ApiError):
            W.create(".trash", "dir")

    @unittest.skipIf(os.name == "nt", "dowiązania symboliczne wymagają uprawnień na Windows")
    def test_symlink_outside_rejected(self):
        W.ensure_root()
        outside = Path(tempfile.mkdtemp(dir=_TMP, prefix="outside-"))
        (outside / "secret.txt").write_text("tajne", encoding="utf-8")
        os.symlink(outside, self.dir / "link")
        os.symlink(outside / "secret.txt", self.dir / "plik.txt")
        with self.assertRaises(ApiError):
            W.read_file("link/secret.txt")
        with self.assertRaises(ApiError):
            W.read_file("plik.txt")
        with self.assertRaises(ApiError):
            W.save_file("link/nowy.txt", "x")
        self.assertFalse((outside / "nowy.txt").exists())
        names = [n["name"] for n in W.tree()["tree"]]
        self.assertNotIn("link", names)
        self.assertNotIn("plik.txt", names)
        hits = W.search("tajne")["results"]
        self.assertEqual(hits, [])

    def test_windows_names_sanitized(self):
        self.assertEqual(W.sanitize_name("CON"), "CON_")
        self.assertEqual(W.sanitize_name("con.cpp"), "con_.cpp")
        self.assertEqual(W.sanitize_name("NUL.tar.gz"), "NUL_.tar.gz")
        self.assertEqual(W.sanitize_name("com1.txt"), "com1_.txt")
        self.assertEqual(W.sanitize_name("LPT9"), "LPT9_")
        self.assertEqual(W.sanitize_name("console.cpp"), "console.cpp")
        self.assertEqual(W.sanitize_name('a<b>c:d"e|f?g*.cpp'), "a_b_c_d_e_f_g_.cpp")
        self.assertEqual(W.sanitize_name("plik. . "), "plik")
        self.assertEqual(W.sanitize_name("  zadanie 1  "), "zadanie 1")
        self.assertEqual(W.sanitize_name("Zażółć gęślą jaźń.cpp"), "Zażółć gęślą jaźń.cpp")
        for bad in ["", "   ", ".", "..", " . ", ".trash"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ApiError):
                    W.sanitize_name(bad)
        self.assertLessEqual(len(W.sanitize_name("x" * 400 + ".cpp")), 150)
        self.assertTrue(W.sanitize_name("x" * 400 + ".cpp").endswith(".cpp"))

    def test_create_sanitizes(self):
        r = W.create("Nowy folder/CON.cpp", "file")
        self.assertEqual(r["path"], "Nowy folder/CON_.cpp")
        r = W.create("a?b.py", "file")
        self.assertEqual(r["path"], "a_b.py")
        self.assertTrue((self.dir / "a_b.py").is_file())


# =============================================================================================== operacje
class TestFileOps(WsCase):
    def test_first_run_example(self):
        t = W.tree()
        names = [n["name"] for n in t["tree"]]
        self.assertIn(W.EXAMPLE_NAME, names)
        ex = next(n for n in t["tree"] if n["name"] == W.EXAMPLE_NAME)
        kinds = [(c["name"], c["type"]) for c in ex["children"]]
        self.assertEqual(kinds[0], ("testy", "dir"))                 # najpierw foldery
        self.assertIn(("rozw.cpp", "file"), kinds)
        self.assertNotIn(".oiforge-workspace.json", json.dumps(t))
        # usunięty przykład NIE wraca
        W.delete(W.EXAMPLE_NAME)
        self.assertNotIn(W.EXAMPLE_NAME, [n["name"] for n in W.tree()["tree"]])

    def test_tree_sorting_natural(self):
        W.ensure_root()
        for n in ["10.in", "2.in", "1.in", "B.cpp", "a.cpp"]:
            self.write(f"p/{n}", "x")
        (self.dir / "p" / "zz").mkdir()
        (self.dir / "p" / "aa").mkdir()
        p = next(n for n in W.tree()["tree"] if n["name"] == "p")
        self.assertEqual([c["name"] for c in p["children"]], ["aa", "zz", "1.in", "2.in", "10.in", "a.cpp", "B.cpp"])

    def test_create_save_read(self):
        r = W.create("proj/main.cpp", "file")
        self.assertIn("int main", W.read_file("proj/main.cpp")["content"])
        with self.assertRaises(ApiError):
            W.create("proj/main.cpp", "file")
        r2 = W.create("proj/main.cpp", "file", unique=True)
        self.assertEqual(r2["path"], "proj/main (2).cpp")
        f = W.read_file(r["path"])
        s = W.save_file(r["path"], "a\r\nb\r\n", f["mtime"])
        self.assertTrue(s.get("saved"))
        self.assertEqual((self.dir / "proj" / "main.cpp").read_bytes(), b"a\nb\n")
        self.assertEqual(W.read_file("proj/main.cpp")["content"], "a\nb\n")
        W.create("proj/sub", "dir")
        self.assertTrue((self.dir / "proj" / "sub").is_dir())
        W.create("proj/pusty.txt", "file", template="empty")
        self.assertEqual((self.dir / "proj" / "pusty.txt").read_text(encoding="utf-8"), "")
        W.create("proj/z.cpp", "file", content="// mój kod\n")
        self.assertEqual(W.read_file("proj/z.cpp")["content"], "// mój kod\n")

    def test_utf8_and_binary(self):
        W.ensure_root()
        self.write("t.md", "Zażółć gęślą jaźń ✓\n")
        self.assertEqual(W.read_file("t.md")["content"], "Zażółć gęślą jaźń ✓\n")
        (self.dir / "b.bin").write_bytes(b"\x00\x01\x02\xff")
        self.assertTrue(W.read_file("b.bin")["binary"])

    def test_size_limit(self):
        W.ensure_root()
        with self.assertRaises(ApiError):
            W.save_file("duzy.txt", "x" * (W.MAX_FILE_BYTES + 1))
        self.assertFalse((self.dir / "duzy.txt").exists())

    def test_save_conflict(self):
        W.create("k.cpp", "file", content="v1\n")
        f = W.read_file("k.cpp")
        time.sleep(0.01)
        p = self.dir / "k.cpp"
        p.write_text("zmiana z zewnątrz\n", encoding="utf-8")
        os.utime(p, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))      # na pewno inny mtime
        r = W.save_file("k.cpp", "v2\n", f["mtime"])
        self.assertTrue(r.get("conflict"))
        self.assertEqual(r["disk_content"], "zmiana z zewnątrz\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "zmiana z zewnątrz\n")    # nic nie nadpisano
        r = W.save_file("k.cpp", "v2\n", f["mtime"], force=True)
        self.assertTrue(r.get("saved"))
        self.assertEqual(p.read_text(encoding="utf-8"), "v2\n")
        # ta sama treść na dysku = brak konfliktu mimo innego mtime
        r = W.save_file("k.cpp", "v2\n", 1)
        self.assertFalse(r.get("conflict"))
        # plik usunięty z zewnątrz
        cur = W.read_file("k.cpp")["mtime"]
        p.unlink()
        r = W.save_file("k.cpp", "v3\n", cur)
        self.assertTrue(r.get("conflict") and r.get("deleted"))
        self.assertTrue(W.save_file("k.cpp", "v3\n", cur, force=True).get("saved"))

    def test_atomic_no_tmp_left(self):
        W.create("a/x.cpp", "file", content="1")
        for i in range(5):
            W.save_file("a/x.cpp", f"{i}\n")
        self.assertEqual(sorted(c.name for c in (self.dir / "a").iterdir()), ["x.cpp"])

    def test_rename(self):
        W.create("p/a.cpp", "file", content="A")
        r = W.rename("p/a.cpp", new_name="b.cpp")
        self.assertEqual(r["path"], "p/b.cpp")
        self.assertFalse((self.dir / "p" / "a.cpp").exists())
        r = W.rename("p/b.cpp", new_path="q/c.cpp")
        self.assertEqual((self.dir / "q" / "c.cpp").read_text(encoding="utf-8"), "A")
        W.create("q/d.cpp", "file", content="D")
        with self.assertRaises(ApiError):
            W.rename("q/c.cpp", new_name="d.cpp")
        with self.assertRaises(ApiError):
            W.rename("q", new_path="q/w/srodku")
        with self.assertRaises(ApiError):
            W.rename("q/c.cpp", new_path="../c.cpp")
        r = W.rename("q/c.cpp", new_name="C.cpp")                  # zmiana samej wielkości liter
        self.assertEqual(r["path"], "q/C.cpp")
        self.assertIn("C.cpp", [c.name for c in (self.dir / "q").iterdir()])
        r = W.rename("q/C.cpp", new_name="PRN.cpp")
        self.assertEqual(r["path"], "q/PRN_.cpp")

    def test_delete_trash_restore(self):
        W.create("p/a.cpp", "file", content="kod A")
        W.create("p/sub/b.txt", "file", content="B")
        d = W.delete("p/a.cpp")
        self.assertFalse((self.dir / "p" / "a.cpp").exists())
        items = W.trash_list()["items"]
        self.assertEqual(items[0]["path"], "p/a.cpp")
        self.assertNotIn(".trash", json.dumps(W.tree()))
        # w tym czasie powstał nowy plik o tej samej nazwie → przywrócony dostaje dopisek
        W.create("p/a.cpp", "file", content="nowy")
        r = W.restore(d["trash_id"])
        self.assertEqual(r["path"], "p/a (przywrócony).cpp")
        self.assertEqual((self.dir / "p" / "a (przywrócony).cpp").read_text(encoding="utf-8"), "kod A")
        self.assertEqual(W.trash_list()["items"], [])
        with self.assertRaises(ApiError):
            W.restore(d["trash_id"])
        # folder
        d2 = W.delete("p/sub")
        self.assertFalse((self.dir / "p" / "sub").exists())
        r = W.restore(d2["trash_id"])
        self.assertEqual(r["path"], "p/sub")
        self.assertEqual((self.dir / "p" / "sub" / "b.txt").read_text(encoding="utf-8"), "B")
        with self.assertRaises(ApiError):
            W.restore("../../etc")
        W.delete("p")
        self.assertEqual(W.empty_trash()["removed"], 1)

    def test_duplicate(self):
        W.create("p/a.cpp", "file", content="A")
        self.assertEqual(W.duplicate("p/a.cpp")["path"], "p/a (kopia).cpp")
        self.assertEqual(W.duplicate("p/a.cpp")["path"], "p/a (kopia 2).cpp")
        r = W.duplicate("p")
        self.assertEqual(r["path"], "p (kopia)")
        self.assertTrue((self.dir / "p (kopia)" / "a (kopia).cpp").is_file())


# =============================================================================================== zadania
class TestTasks(WsCase):
    STATEMENT = """Suma

Dana jest liczba n i n liczb. Wypisz ich sumę.

Wejście
W pierwszym wierszu liczba n (1 ≤ n ≤ 1000).

Wyjście
Suma liczb.

Przykład
Dla danych wejściowych:
3
1 2 3
poprawnym wynikiem jest:
6
"""

    def test_new_task_files(self):
        r = W.new_task("Moje zadanie: test?")
        self.assertEqual(r["path"], "Moje zadanie_ test_")
        base = self.dir / r["path"]
        for f in ["rozw.cpp", "brute.cpp", "gen.py", "testy/1.in", "testy/1.out", "tresc.md", "notatki.md"]:
            self.assertTrue((base / f).is_file(), f)
        self.assertEqual(r["open"], "Moje zadanie_ test_/rozw.cpp")
        gen = (base / "gen.py").read_text(encoding="utf-8")
        self.assertIn("sys.argv[1]", gen)
        compile(gen, "gen.py", "exec")
        out = subprocess.run([sys.executable, str(base / "gen.py"), "7", "5"], capture_output=True, text=True, timeout=30)
        self.assertEqual(len([t for t in out.stdout.strip().split("\n\n") if t.strip()]), 5)
        r2 = W.new_task("Moje zadanie: test?")
        self.assertEqual(r2["path"], "Moje zadanie_ test_ (2)")

    def test_new_task_with_samples(self):
        r = W.new_task("", self.STATEMENT)
        base = self.dir / r["path"]
        self.assertIn("Suma", (base / "tresc.md").read_text(encoding="utf-8"))
        if r["samples"]:
            self.assertEqual((base / "testy/1.in").read_text(encoding="utf-8").split(), ["3", "1", "2", "3"])
            self.assertEqual((base / "testy/1.out").read_text(encoding="utf-8").split(), ["6"])

    def test_new_project(self):
        r = W.new_project("Projekt / próbny")
        self.assertEqual(r["path"], "Projekt _ próbny")
        self.assertEqual(list((self.dir / r["path"]).iterdir()), [])

    def test_task_context_and_tests(self):
        r = W.new_task("Zad")
        ctx = W.task_context("Zad/rozw.cpp", with_content=True)
        self.assertEqual(ctx["solution"]["path"], "Zad/rozw.cpp")
        self.assertEqual(ctx["brute"]["path"], "Zad/brute.cpp")
        self.assertEqual(ctx["generator"]["path"], "Zad/gen.py")
        self.assertEqual(ctx["statement"]["path"], "Zad/tresc.md")
        self.assertIn("int main", ctx["solution"]["content"])
        self.assertEqual(ctx["tests_dir"], "Zad/testy")
        s = W.save_test("Zad", "5\n1 1 1 1 1\n", "5\n")
        self.assertEqual(s["number"], 2)
        self.assertEqual(s["in"], "Zad/testy/2.in")
        s = W.save_test("Zad/rozw.cpp", "1\n7", None)
        self.assertEqual(s["number"], 3)
        self.assertIsNone(s["out"])
        info = W.tests_info("Zad/rozw.cpp")
        self.assertEqual([t["name"] for t in info["tests"]], ["1", "2", "3"])
        self.assertEqual(info["tests"][1]["in_preview"], "5\n1 1 1 1 1\n")
        # folder bez testów → save_test tworzy testy/
        W.new_project("Pusty")
        W.create("Pusty/a.cpp", "file")
        s = W.save_test("Pusty", "1\n", "1\n")
        self.assertEqual(s["in"], "Pusty/testy/1.in")

    def test_import_program_errors(self):
        with self.assertRaises(ApiError):
            W.import_program("")
        with self.assertRaises(ApiError):
            W.import_program("nie-ma-takiego-programu-xyz")


# =============================================================================================== uruchamianie testów
@unittest.skipUnless(HAS_CXX, "brak kompilatora C++")
class TestRunTests(WsCase):
    def setUp(self):
        super().setUp()
        W.ensure_root()
        self.write("z/testy/1.in", "3\n1 2 3\n")
        self.write("z/testy/1.out", "6\n")
        self.write("z/testy/2.in", "2\n2000000000 2000000000\n")
        self.write("z/testy/2.out", "4000000000\n")
        self.write("z/testy/3.in", "1\n5\n")                 # bez .out — tylko uruchomienie

    def test_good_solution_ok(self):
        self.write("z/rozw.cpp", GOOD)
        r = W.run_tests_sync("z/rozw.cpp")
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["passed"], 3)
        self.assertEqual(r["unchecked"], 1)
        self.assertTrue(r["all_ok"])
        self.assertEqual([t["verdict"] for t in r["tests"]], ["OK", "OK", "OK"])

    def test_bad_solution_wa(self):
        self.write("z/rozw.cpp", BAD)
        r = W.run_tests_sync("z/rozw.cpp")
        v = [t["verdict"] for t in r["tests"]]
        self.assertEqual(v[:2], ["WA", "WA"])
        self.assertFalse(r["all_ok"])
        d = r["tests"][0]["diff"]
        self.assertEqual(d["line"], 1)
        self.assertEqual(d["expected"], "6")
        self.assertEqual(d["got"], "7")

    def test_folder_and_tests_in_subdir(self):
        self.write("z/rozw.cpp", GOOD)
        r = W.run_tests_sync("z")                              # folder → rozw.cpp
        self.assertTrue(r["all_ok"])
        self.write("z/inne/alt.cpp", GOOD)                       # testy szukane wyżej
        r = W.run_tests_sync("z/inne/alt.cpp", tests=["1"])
        self.assertEqual(r["total"], 1)

    def test_compile_error_and_tle(self):
        self.write("z/rozw.cpp", NOT_CPP)
        r = W.run_tests_sync("z/rozw.cpp")
        self.assertEqual(r["status"], "CE")
        self.write("z/wolne.cpp", SLOW)
        r = W.run_tests_sync("z/wolne.cpp", timeout=0.3, tests=["1"])
        self.assertEqual(r["tests"][0]["verdict"], "TLE")

    def test_python_solution(self):
        self.write("z/rozw.py", "n = int(input())\nprint(sum(map(int, input().split())))\n")
        r = W.run_tests_sync("z/rozw.py")
        self.assertTrue(r["all_ok"], r)

    def test_no_tests_dir(self):
        W.new_project("bez")
        self.write("bez/a.cpp", GOOD)
        with self.assertRaises(ApiError):
            W.run_tests_sync("bez/a.cpp")
        self.write("bez/notatka.md", "x")
        with self.assertRaises(ApiError):
            W.run_tests_sync("bez/notatka.md")


# =============================================================================================== szukanie
class TestSearch(WsCase):
    def test_search(self):
        W.ensure_root()
        self.write("p/a.cpp", "int main() {\n    // TODO: dijkstra\n}\n")
        self.write("p/dijkstra.md", "notatki")
        r = W.search("Dijkstra")
        kinds = {(x["path"], x["kind"], x["line"]) for x in r["results"]}
        self.assertIn(("p/a.cpp", "text", 2), kinds)
        self.assertIn(("p/dijkstra.md", "name", None), kinds)
        self.assertEqual(W.search("x")["results"], [])          # za krótkie
        W.delete("p/a.cpp")
        self.assertNotIn("p/a.cpp", [x["path"] for x in W.search("dijkstra")["results"]])


# =============================================================================================== HTTP
def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0          # nikt nie nasłuchuje


@unittest.skipUnless(_port_free(PORT), f"port {PORT} zajęty")
class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.udir = Path(tempfile.mkdtemp(dir=_TMP, prefix="http-"))
        env = dict(os.environ, OIFORGE_USER_DIR=str(cls.udir), OIFORGE_NO_WARMUP="1")
        cls.proc = subprocess.Popen([sys.executable, "-m", "oiforge", "--port", str(PORT), "--no-browser", "--no-warmup"],
                                    cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.25)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("serwer nie wystartował")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        shutil.rmtree(cls.udir, ignore_errors=True)

    def req(self, method, path, body=None, headers=True):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        if headers:
            h["X-OIForge"] = "1"
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_flow(self):
        st, r = self.req("GET", "/api/ws/tree")
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertIn(W.EXAMPLE_NAME, [n["name"] for n in r["tree"]])
        self.assertTrue((self.udir / "workspace").is_dir())
        st, r = self.req("POST", "/api/ws/create", {"path": "h/a.cpp", "kind": "file"}, headers=False)
        self.assertEqual(st, 403)
        st, r = self.req("POST", "/api/ws/save", {"path": "../uciekam.txt", "content": "x"})
        self.assertEqual(st, 403)
        self.assertFalse(r["ok"])
        self.assertFalse((self.udir / "uciekam.txt").exists())
        st, r = self.req("GET", "/api/ws/file?path=%2Fetc%2Fpasswd")
        self.assertEqual(st, 403)
        st, r = self.req("POST", "/api/ws/new_task", {"name": "HTTP zadanie"})
        self.assertTrue(r["ok"])
        st, f = self.req("GET", "/api/ws/file?path=" + urllib.request.quote(r["open"]))
        self.assertIn("int main", f["content"])
        st, s = self.req("POST", "/api/ws/save", {"path": r["open"], "content": GOOD, "mtime": f["mtime"]})
        self.assertTrue(s.get("saved"), s)
        st, s2 = self.req("POST", "/api/ws/save", {"path": r["open"], "content": BAD, "mtime": f["mtime"]})
        self.assertTrue(s2.get("conflict"), s2)
        st, d = self.req("POST", "/api/ws/delete", {"path": "HTTP zadanie/notatki.md"})
        st, t = self.req("GET", "/api/ws/trash")
        self.assertEqual(t["items"][0]["id"], d["trash_id"])
        st, rr = self.req("POST", "/api/ws/restore", {"trash_id": d["trash_id"]})
        self.assertEqual(rr["path"], "HTTP zadanie/notatki.md")
        st, q = self.req("GET", "/api/ws/search?q=" + urllib.request.quote("cin >> n"))
        self.assertTrue(any(x["path"] == r["open"] for x in q["results"]))

    @unittest.skipUnless(HAS_CXX, "brak kompilatora C++")
    def test_run_tests_job(self):
        ex = W.EXAMPLE_NAME
        self.req("GET", "/api/ws/tree")
        st, r = self.req("POST", "/api/ws/run_tests", {"path": f"{ex}/rozw.cpp"})
        self.assertTrue(r["ok"], r)
        job = r["job"]
        snap = {}
        for _ in range(240):
            st, snap = self.req("GET", f"/api/jobs/{job}")
            if snap["status"] != "running":
                break
            time.sleep(0.25)
        self.assertEqual(snap["status"], "done", snap)
        self.assertTrue(snap["result"]["all_ok"], snap["result"])
        self.assertEqual(snap["result"]["total"], 3)
        tests = [e for e in snap["events"] if e["type"] == "test"]
        self.assertEqual(len(tests), 3)


if __name__ == "__main__":
    unittest.main()

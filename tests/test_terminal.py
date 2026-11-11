"""Tryb terminala (oiforge/tools/terminal.py) i strażnik pamięci po „Uruchom” (checker.guard_run)."""
import os
import time
import unittest

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge import config  # noqa: E402
from oiforge.tools import checker, terminal  # noqa: E402

ECHO_SUM = r"""#include <bits/stdc++.h>
using namespace std;
int main() { int n; cin >> n; long long s = 0; for (int i = 0; i < n; i++) { int x; cin >> x; s += x; cout << s << endl; } }
"""

OOB = r"""#include <bits/stdc++.h>
using namespace std;
vector<int> v;
int main() { int x; cin >> x; for (int i = 0; i < x; i++) { int a; cin >> a; v.push_back(a); }
    for (int i = 0; i < x + 1000; i++) cout << v[i] << '\n'; }
"""


def _collect(sid, until_done=True, rounds=20):
    since, rev, buf, x = 0, -1, {}, None
    for _ in range(rounds):
        x = terminal.handle_read({"id": sid, "since": since, "rev": rev})
        for i, c in enumerate(x["chunks"]):
            buf[x["start"] + i] = c
        since, rev = x["next"], x["rev"]
        if x["done"] or not until_done:
            break
    return [buf[k] for k in sorted(buf)], x


class TerminalTest(unittest.TestCase):
    def test_interactive_cpp(self):
        r = terminal.handle_start({"code": ECHO_SUM})
        self.assertTrue(r["started"], r)
        sid = r["id"]
        terminal.handle_write({"id": sid, "data": "2\n5\n"})
        time.sleep(0.3)
        chunks, x = _collect(sid, until_done=False)
        out = "".join(c["t"] for c in chunks if c["k"] == "out")
        self.assertIn("5", out)                      # odpowiedź przed podaniem reszty danych
        self.assertFalse(x["done"])
        terminal.handle_write({"id": sid, "data": "7\n"})
        chunks, x = _collect(sid)
        self.assertTrue(x["done"])
        self.assertEqual(x["exit_code"], 0)
        self.assertEqual("".join(c["t"] for c in chunks if c["k"] == "out").split(), ["5", "12"])

    def test_eof_and_kill(self):
        r = terminal.handle_start({"code": "import sys\nprint(len(sys.stdin.read().split()))", "lang": "python"})
        terminal.handle_write({"id": r["id"], "data": "1 2 3\n"})
        terminal.handle_write({"id": r["id"], "data": "", "eof": True})
        chunks, x = _collect(r["id"])
        self.assertEqual("".join(c["t"] for c in chunks if c["k"] == "out").strip(), "3")
        r2 = terminal.handle_start({"code": "int main(){ while (true) {} }"})
        self.assertTrue(terminal.handle_kill({"id": r2["id"]})["killed"])

    def test_compile_error(self):
        r = terminal.handle_start({"code": "int main() { return x; }"})
        self.assertFalse(r["started"])
        self.assertFalse(r["compile"]["ok"])


@unittest.skipUnless(config.capabilities().get("sanitizers") or config.GCC, "brak sanitizerów i trybu debug STL")
class GuardTest(unittest.TestCase):
    def test_out_of_bounds_found_with_line(self):
        g = checker.guard_run(OOB, "3 1 2 3")
        self.assertTrue(g["checked"])
        self.assertTrue(g["issues"], g)
        self.assertEqual(g["issues"][0]["line"], 5)

    def test_clean_program(self):
        g = checker.guard_run(OOB.replace("x + 1000", "x"), "3 1 2 3")
        self.assertTrue(g["checked"])
        self.assertEqual(g["issues"], [])


if __name__ == "__main__":
    unittest.main()

"""Testy C++ Playground (oiforge.tools.playground).

Uruchom: python3 -m unittest tests.test_playground -v
Testy uruchamiające kod pomijają się, gdy brak kompilatora.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oiforge import config  # noqa: E402
from oiforge.core import runner  # noqa: E402
from oiforge.server_api import ApiError  # noqa: E402
from oiforge.tools import playground as P  # noqa: E402

HAS_CXX = bool(config.CXX)


def main_body(program: str) -> str:
    return program[program.index("int main() {"):]


def globals_part(program: str) -> str:
    start = program.find('#line 1 "main.cpp"')
    head = program[:program.index("int main() {")]
    return head[start:] if start >= 0 else head


class TestWrap(unittest.TestCase):
    def test_statements_go_to_main_definitions_outside(self):
        w = P.wrap("int sq(int x) { return x * x; }\nstruct P { int a; };\ncout << sq(4) << '\\n';\n")
        self.assertFalse(w["has_main"])
        g, m = globals_part(w["program"]), main_body(w["program"])
        self.assertIn("int sq(int x)", g)
        self.assertIn("struct P", g)
        self.assertIn("cout << sq(4)", m)
        self.assertNotIn("int sq", m)
        self.assertIn("#include <bits/stdc++.h>", w["program"])
        self.assertIn("using namespace std;", w["program"])
        self.assertTrue(w["notes"][0].startswith("Brak funkcji main"))

    def test_existing_main_untouched(self):
        code = "#include <iostream>\nint main() { std::cout << 1; }\n"
        w = P.wrap(code)
        self.assertTrue(w["has_main"])
        self.assertEqual(w["program"], code)

    def test_main_without_includes_gets_header(self):
        w = P.wrap("int main() { vector<int> v; cout << v.size(); }")
        self.assertTrue(w["program"].startswith("#include <bits/stdc++.h>\nusing namespace std;\n"))

    def test_repl_last_expression(self):
        w = P.wrap("vector<int> v = {3, 1, 2};\nsort(v.begin(), v.end());\nv")
        self.assertIn("std::cout << (v) << '\\n';", main_body(w["program"]))
        self.assertEqual(w["repl"], "v")
        self.assertTrue(any("REPL" in n for n in w["notes"]))

    def test_repl_question_mark_lines(self):
        w = P.wrap("int a = 7;\n? a / 2\n? a % 3; // reszta\n")
        m = main_body(w["program"])
        self.assertIn("std::cout << (a / 2) << '\\n';", m)
        self.assertIn("std::cout << (a % 3) << '\\n'; // reszta", m)
        self.assertEqual(w["repl_lines"], [2, 3])

    def test_ternary_on_continuation_line_is_not_repl(self):
        w = P.wrap("int x = 5;\nint y = x > 3\n    ? 1 : 2;\ny")
        self.assertIn("? 1 : 2;", w["program"])
        self.assertEqual(w["repl_lines"], [])

    def test_missing_semicolon_before_last_expression(self):
        w = P.wrap("int x = 3\nx * 2")
        m = main_body(w["program"])
        self.assertIn("int x = 3;", w["program"])
        self.assertIn("std::cout << (x * 2) << '\\n';", m)

    def test_declarations_after_statement_stay_local(self):
        w = P.wrap("int n;\ncin >> n;\nvector<int> a(n);\na.size()")
        g, m = globals_part(w["program"]), main_body(w["program"])
        self.assertIn("int n;", g)
        self.assertIn("vector<int> a(n);", m)
        self.assertNotIn("vector<int> a(n);", g)

    def test_capturing_lambda_stays_local(self):
        w = P.wrap("int k = 2;\nauto f = [&](int x) { return x * k; };\nf(5)")
        self.assertIn("auto f = [&]", main_body(w["program"]))
        self.assertIn("int k = 2;", globals_part(w["program"]))

    def test_templates_using_typedef_and_pp_are_global(self):
        code = ("#define MOD 1000000007\ntypedef long long ll;\nusing pii = pair<int, int>;\n"
                "template <class T> T mx(T a, T b) { return a > b ? a : b; }\nnamespace g { int z = 1; }\n"
                "mx<ll>(3, MOD)")
        w = P.wrap(code)
        g = globals_part(w["program"])
        for s in ("typedef long long ll;", "using pii", "template <class T>", "namespace g"):
            self.assertIn(s, g)
        self.assertIn("#define MOD", w["program"][:w["program"].index("oipg")])

    def test_std_qualified_code_gets_no_using(self):
        w = P.wrap("std::vector<int> v{1, 2};\nstd::cout << v.size();")
        self.assertNotIn("using namespace std;", w["program"])

    def test_line_directives_point_to_user_lines(self):
        w = P.wrap("int a = 1;\n\nint b = 2;\ncout << a + b;\n")
        self.assertIn('#line 4 "main.cpp"\ncout << a + b;', w["program"])

    def test_user_dbg_and_printers_not_duplicated(self):
        code = ("#define dbg(x) cerr << #x << x\n"
                "ostream& operator<<(ostream& os, const vector<int>& v) { return os << v.size(); }\n"
                "vector<int> v{1};\ncout << v;")
        w = P.wrap(code)
        self.assertNotIn("dbg_impl", w["program"])
        self.assertNotIn("namespace oipg {\ninline void put", w["program"])

    def test_python_detected(self):
        w = P.wrap("for i in range(3):\n    print(i)\n")
        self.assertEqual(w["lang"], "python")

    def test_empty_code(self):
        with self.assertRaises(ApiError):
            P.wrap("   ")
        with self.assertRaises(ApiError):
            P.handle_wrap({"code": ""})

    def test_pretty_view_is_clean(self):
        w = P.wrap("vector<int> v{3,1};\nsort(v.begin(),v.end());\nv")
        self.assertNotIn("#line", w["pretty"])
        self.assertIn("int main() {\n    sort(v.begin(), v.end());", w["pretty"])
        self.assertIn("[nagłówek OI Forge Playground", w["pretty"])

    def test_polish_plurals(self):
        self.assertEqual(P._pl(1, "a", "b", "c"), "a")
        self.assertEqual(P._pl(3, "a", "b", "c"), "b")
        self.assertEqual(P._pl(5, "a", "b", "c"), "c")
        self.assertEqual(P._pl(12, "a", "b", "c"), "c")
        self.assertEqual(P._pl(22, "a", "b", "c"), "b")


@unittest.skipUnless(HAS_CXX, "brak kompilatora C++")
class TestRun(unittest.TestCase):
    def run_code(self, code, inp=""):
        return P.handle_run({"code": code, "input": inp})

    def test_containers_print_and_repl(self):
        r = self.run_code("vector<int> v = {3, 1, 2};\nsort(v.begin(), v.end());\nmap<string, int> m{{\"a\", 1}};\n"
                          "cout << v << ' ' << m << ' ' << pair<int, char>{1, 'x'} << '\\n';\n"
                          "set<int> s(v.begin(), v.end());\ns")
        self.assertTrue(r["success"], r.get("compile"))
        self.assertEqual(r["run"]["stdout"], "[1, 2, 3] {\"a\": 1} (1, 'x')\n{1, 2, 3}\n")

    def test_dbg_goes_to_stderr_with_user_line(self):
        r = self.run_code("int a = 4;\nvector<int> v{1, 2};\ndbg(a, v, a * 2);")
        self.assertTrue(r["success"], r.get("compile"))
        self.assertEqual(r["run"]["stdout"], "")
        self.assertIn("[linia 3] a = 4, v = [1, 2], a * 2 = 8", r["run"]["stderr"])

    def test_int128_and_queues(self):
        r = self.run_code("__int128 x = (__int128)1 << 100;\ncout << x << '\\n';\n"
                          "priority_queue<int, vector<int>, greater<int>> pq;\nfor (int y : {5, 1, 3}) pq.push(y);\n"
                          "cout << pq << '\\n';\ntuple<int, string> t{7, \"ab\"};\nt")
        self.assertTrue(r["success"], r.get("compile"))
        self.assertEqual(r["run"]["stdout"], "1267650600228229401496703205376\n[top: 1, 3, 5]\n(7, \"ab\")\n")

    def test_input_is_passed(self):
        r = self.run_code("int n; cin >> n;\nlong long s = 0;\nfor (int i = 0; i < n; i++) { int x; cin >> x; s += x; }\ns",
                          "3\n10 20 30\n")
        self.assertTrue(r["success"], r.get("compile"))
        self.assertEqual(r["run"]["stdout"].strip(), "60")

    def test_compile_error_points_to_fragment_line(self):
        r = self.run_code("int a = 1;\nint b = 2;\nint c = a +* ;\ncout << c;")
        self.assertFalse(r["success"])
        self.assertFalse(r["compile"]["ok"])
        self.assertIn("main.cpp:3:", r["compile"]["stderr"])

    def test_full_program_runs(self):
        r = self.run_code("#include <bits/stdc++.h>\nint main() { std::cout << 6 * 7; }")
        self.assertTrue(r["success"])
        self.assertEqual(r["run"]["stdout"], "42")


class TestExamples(unittest.TestCase):
    def test_examples_file_shape(self):
        data = json.loads(P.EXAMPLES_FILE.read_text(encoding="utf-8"))
        ex = data["examples"]
        self.assertGreaterEqual(len(ex), 25)
        ids = [e["id"] for e in ex]
        self.assertEqual(len(ids), len(set(ids)))
        for e in ex:
            for k in ("id", "title", "description", "code", "category"):
                self.assertTrue(e.get(k), (e.get("id"), k))
        self.assertEqual(len(P.handle_examples({})["examples"]), len(ex))

    @unittest.skipUnless(HAS_CXX, "brak kompilatora C++")
    def test_every_example_compiles_and_runs(self):
        ex = P.load_examples()

        def work(e):
            w = P.wrap(e["code"])
            res = runner.run_source(w["program"], e.get("input", ""), lang="cpp", timeout=5.0)
            return e["id"], res

        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(work, ex))
        for eid, res in results:
            self.assertTrue(res["compile"]["ok"], f"{eid}: {res['compile']['stderr'][:800]}")
            self.assertEqual(res["run"]["verdict"], "OK", f"{eid}: {res['run']}")
            self.assertTrue(res["run"]["stdout"] or res["run"]["stderr"], eid)


if __name__ == "__main__":
    unittest.main()

"""Testy tłumacza błędów C++ (oiforge.tools.errors) i wzorców data/error_patterns.json."""
from __future__ import annotations

import json
import re
import unittest

from oiforge import config
from oiforge.core import runner
from oiforge.server_api import ApiError
from oiforge.tools import errors as E

H = "#include <bits/stdc++.h>\nusing namespace std;\n"


def pats(stderr, source=None):
    return [i.get("pattern") for i in E.translate(stderr, source)]


class TestPatterns(unittest.TestCase):
    def test_file_is_valid(self):
        data = json.loads(E.PATTERNS_FILE.read_text(encoding="utf-8"))
        ps = data["patterns"]
        self.assertGreaterEqual(len(ps), 80)
        ids = [p["id"] for p in ps]
        self.assertEqual(len(ids), len(set(ids)), "zduplikowane id wzorców")
        for p in ps:
            for k in ("id", "match", "title", "explanation", "fix"):
                self.assertIn(k, p)
            for m in p["match"]:
                re.compile(m)
            self.assertTrue(re.search(r"[ąęłóśżźćńĄĘŁÓŚŻŹĆŃ]", p["explanation"] + p["title"] + p["fix"]) or p["id"].startswith("py-"),
                            f"{p['id']}: tekst nie wygląda na polski")

    def test_required_messages_are_covered(self):
        must = [
            "'x' was not declared in this scope", "use of undeclared identifier 'x'", "expected ';' before 'int'",
            "no matching function for call to 'foo(int)'", "no match for 'operator<<' (operand types are 'std::ostream' and 'std::vector<int>')",
            "invalid conversion from 'int' to 'int*'", "cannot convert 'std::string' to 'int' in initialization",
            "lvalue required as left operand of assignment", "redefinition of 'int f()'",
            "control reaches end of non-void function [-Wreturn-type]",
            "comparison of integer expressions of different signedness: 'int' and 'std::vector<int>::size_type'",
            "array subscript 10 is above array bounds of 'int [10]'", "narrowing conversion of 'x' from 'long long int' to 'int'",
            "template argument deduction/substitution failed:", "'int A::x' is private within this context",
            "undefined reference to `f(int)'", "multiple definition of `f()'", "expected primary-expression before ';' token",
            "expected unqualified-id before '}' token", "stray '\\342' in program", "ld: symbol(s) not found for architecture arm64",
            "missing template arguments before 'v'", "invalid use of incomplete type 'struct A'",
            "passing 'const A' as 'this' argument discards qualifiers [-fpermissive]", "no member named 'push' in 'std::vector<int>'",
            "'class std::vector<int>' has no member named 'push'", "call of overloaded 'abs(unsigned int)' is ambiguous",
            "unused variable 'a' [-Wunused-variable]", "'s' may be used uninitialized [-Wmaybe-uninitialized]",
            "reference to 'count' is ambiguous", "'int y1' redeclared as different kind of entity",
            "'::main' must return 'int'", "iteration 10 invokes undefined behavior [-Waggressive-loop-optimizations]",
            "format '%d' expects argument of type 'int', but argument 2 has type 'long long int' [-Wformat=]",
            "suggest parentheses around assignment used as truth value [-Wparentheses]", "jump to case label",
            "variable 'x' is uninitialized when used here", "non-void function does not return a value",
            "implicit conversion loses integer precision: 'long long' to 'int'", "comparison of integers of different signs: 'int' and 'size_t'",
            "declaration of 'n' shadows a global declaration [-Wshadow]", "left shift count >= width of type",
            "comparison of unsigned expression in '>= 0' is always true [-Wtype-limits]",
            "invalid operands of types 'const char [2]' and 'const char [2]' to binary 'operator+'",
            "request for member 'size' in 'v', which is of non-class type 'std::vector<int>()'",
            "too many arguments to function 'int f(int)'", "assignment of read-only variable 'x'",
            "bits/stdc++.h: No such file or directory", "'bits/stdc++.h' file not found",
            "infinite recursion detected [-Winfinite-recursion]", "NameError: name 'x' is not defined",
        ]
        missing = []
        for msg in must:
            p, _ = E._match_pattern(msg)
            if p is None:
                missing.append(msg)
        self.assertFalse(missing, "\n".join(missing))

    def test_patterns_endpoint(self):
        r = E.handle_patterns({})
        self.assertGreaterEqual(len(r["patterns"]), 80)
        self.assertNotIn("_rx", r["patterns"][0])


class TestSimplify(unittest.TestCase):
    def test_strings_and_vectors(self):
        self.assertEqual(E.simplify_types("std::__cxx11::basic_string<char>"), "string")
        self.assertEqual(E.simplify_types("std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"), "string")
        self.assertEqual(E.simplify_types("std::vector<int, std::allocator<int> >"), "vector<int>")
        self.assertEqual(E.simplify_types("std::__1::vector<long long, std::__1::allocator<long long>>"), "vector<long long>")
        self.assertEqual(E.simplify_types("std::map<int, int, std::less<int>, std::allocator<std::pair<const int, int> > >"),
                         "map<int, int>")
        self.assertEqual(E.simplify_types("std::vector<std::vector<int, std::allocator<int> >, std::allocator<std::vector<int, std::allocator<int> > > >"),
                         "vector<vector<int>>")
        self.assertEqual(E.simplify_types("std::basic_ostream<char>"), "ostream")
        self.assertEqual(E.simplify_types("long long int"), "long long")
        self.assertIn("vector<P>::iterator",
                      E.simplify_types("__gnu_cxx::__normal_iterator<P*, std::vector<P, std::allocator<P> > >"))
        self.assertEqual(E.simplify_types("std::set<int, std::greater<int>, std::allocator<int> >"), "set<int, greater<int>>")
        self.assertNotIn("abi", E.simplify_types("std::__1::vector<int>::operator[][abi:ne180100](unsigned long)"))

    def test_operator_not_mangled(self):
        s = E.simplify_types("no match for 'operator<<' (operand types are 'std::ostream' and 'std::vector<int>')")
        self.assertIn("operator<<", s)
        self.assertIn("vector<int>", s)


class TestTranslate(unittest.TestCase):
    def test_examples(self):
        expected = {"undecl": "undeclared", "semi": "expected-semicolon", "maxll": "no-matching-maxmin", "stray": "stray",
                    "sortstruct": "no-match-lt", "coutvec": "no-match-shl", "ambig": "ambiguous-ref",
                    "undefref": "undefined-reference", "retnv": "control-reaches-end", "clangmap": "no-match-index",
                    "mainint": "main-return-int"}
        for ex in E.EXAMPLES:
            if ex["id"] not in expected:
                continue
            items = E.translate(ex["stderr"], ex["source"] or None)
            self.assertTrue(items, ex["id"])
            self.assertEqual(items[0]["pattern"], expected[ex["id"]], ex["id"])
            for it in items:
                for k in ("line", "col", "severity", "title", "explanation", "fix", "original", "snippet"):
                    self.assertIn(k, it)

    def test_primary_and_cascade(self):
        ex = next(e for e in E.EXAMPLES if e["id"] == "semi")
        items = E.translate(ex["stderr"], ex["source"])
        self.assertTrue(items[0]["primary"])
        self.assertEqual(items[0]["line"], 5)
        self.assertIn("linii 4", items[0]["fix"])            # brakujący średnik na końcu linii 4
        self.assertTrue(any(i["cascade"] for i in items[1:]))
        s = E.summarize(items)
        self.assertEqual(s["errors"], 2)
        self.assertIn("Brakuje średnika", s["headline"])

    def test_snippet_marks_column(self):
        src = "int main() {\n    int x = y + 1;\n}\n"
        err = "main.cpp: In function 'int main()':\nmain.cpp:2:13: error: 'y' was not declared in this scope\n"
        it = E.translate(err, src)[0]
        self.assertEqual(it["line"], 2)
        self.assertEqual(it["col"], 13)
        sn = it["snippet"]
        caret_line = [l for l in sn["text"].split("\n") if "^" in l][0]
        code_line = [l for l in sn["text"].split("\n") if "int x = y" in l][0]
        self.assertEqual(caret_line.index("^"), code_line.index("y + 1"))

    def test_header_error_mapped_to_user_line(self):
        ex = next(e for e in E.EXAMPLES if e["id"] == "sortstruct")
        it = E.translate(ex["stderr"], ex["source"])[0]
        self.assertEqual(it["line"], 6)
        self.assertTrue(it["in_header"])

    def test_typo_suggestion(self):
        src = H + "int main() {\n    int wynik = 0;\n    cout << wynk;\n}\n"
        err = "main.cpp:5:13: error: 'wynk' was not declared in this scope; did you mean 'wynik'?\n"
        it = E.translate(err, src)[0]
        self.assertIn("wynik", it["fix"])

    def test_missing_std(self):
        src = "#include <vector>\nint main() { vector<int> v; }\n"
        err = "main.cpp:2:14: error: 'vector' was not declared in this scope\n"
        it = E.translate(err, src)[0]
        self.assertIn("std::vector", it["fix"])

    def test_undeclared_merged(self):
        err = ("main.cpp:3:5: error: 'x' was not declared in this scope\nmain.cpp:4:5: error: 'x' was not declared in this scope\n"
               "main.cpp:5:5: error: 'x' was not declared in this scope\n")
        items = E.translate(err, None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["count"], 3)

    def test_mac_linker(self):
        err = ('Undefined symbols for architecture arm64:\n  "licz(int)", referenced from:\n      _main in main.o\n'
               "ld: symbol(s) not found for architecture arm64\nclang: error: linker command failed with exit code 1 (use -v to see invocation)\n")
        items = E.translate(err, None)
        self.assertEqual(items[0]["pattern"], "undefined-reference")
        self.assertIn("licz(int)", items[0]["explanation"])

    def test_windows_path(self):
        err = "C:\\Users\\Jan\\a.cpp:7:3: error: 'x' was not declared in this scope\n"
        it = E.translate(err, None)[0]
        self.assertEqual(it["line"], 7)
        self.assertEqual(it["pattern"], "undeclared")

    def test_clang_style(self):
        err = ("main.cpp:4:5: error: use of undeclared identifier 'cout'\n    4 |     cout << 1;\n      |     ^\n"
               "1 error generated.\n")
        items = E.translate(err, None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["pattern"], "undeclared")
        self.assertIsNotNone(items[0]["snippet"])

    def test_handler(self):
        with self.assertRaises(ApiError):
            E.handle_translate({"stderr": "  "})
        r = E.handle_translate({"stderr": "main.cpp:1:1: error: expected ';' before 'int'", "source": "int a\nint b;"})
        self.assertTrue(r["items"])
        self.assertIn("summary", r)
        r = E.handle_translate({"stderr": "terminate called after throwing an instance of 'std::bad_alloc'\n  what():  std::bad_alloc\n"})
        self.assertEqual(r["runtime"]["kind"], "exception")
        self.assertIn("examples", E.handle_examples({}))


class TestRuntime(unittest.TestCase):
    def test_signals(self):
        self.assertEqual(E.explain_runtime({"verdict": "RE", "signal": "SIGSEGV", "exit_code": -11})["kind"], "sigsegv")
        self.assertEqual(E.explain_runtime({"verdict": "RE", "signal": "SIGFPE", "exit_code": -8})["kind"], "sigfpe")
        self.assertEqual(E.explain_runtime({"verdict": "RE", "signal": "SIGABRT", "exit_code": -6})["kind"], "sigabrt")
        self.assertEqual(E.explain_runtime({"verdict": "RE", "signal": "SIGBUS"})["kind"], "sigbus")
        self.assertEqual(E.explain_runtime({"verdict": "TLE", "timed_out": True, "time": 2.0})["kind"], "tle")
        self.assertEqual(E.explain_runtime({"verdict": "MLE", "mem_kb": 300000})["kind"], "mle")
        self.assertEqual(E.explain_runtime({"verdict": "RE", "exit_code": 139})["kind"], "sigsegv")
        self.assertEqual(E.explain_runtime({"verdict": "RE", "exit_code": 1})["kind"], "exit-code")
        self.assertEqual(E.explain_runtime({"verdict": "OK", "exit_code": 0})["kind"], "ok")

    def test_windows_codes(self):
        for code, kind in ((3221225477, "sigsegv"), (-1073741819, "sigsegv"), (3221225725, "stack-overflow"),
                           (-1073741571, "stack-overflow"), (3221225620, "sigfpe"), (3221225781, "dll")):
            r = E.explain_runtime({"verdict": "RE", "exit_code": code})
            self.assertEqual(r["kind"], kind, code)

    def test_stack_overflow_asan(self):
        r = E.explain_runtime({"verdict": "RE", "stderr": "==1==ERROR: AddressSanitizer: stack-overflow on address 0x1\n"
                                                         "    #0 0x1 in dfs(int) main.cpp:5\n"})
        self.assertEqual(r["kind"], "stack-overflow")
        self.assertEqual(r["line"], 5)

    def test_exceptions(self):
        r = E.explain_runtime({"verdict": "RE", "signal": "SIGABRT", "stderr":
                               "terminate called after throwing an instance of 'std::out_of_range'\n"
                               "  what():  vector::_M_range_check: __n (which is 5) >= this->size() (which is 3)\n"})
        self.assertEqual(r["kind"], "exception")
        self.assertIn("Indeks 5", r["explanation"])
        r = E.explain_runtime({"verdict": "RE", "stderr": "libc++abi: terminating due to uncaught exception of type std::length_error: vector\n"})
        self.assertEqual(r["kind"], "exception")
        self.assertIn("length_error", r["title"])
        r = E.explain_runtime({"verdict": "RE", "stderr": "a.out: main.cpp:7: int main(): Assertion `x > 0' failed.\n"})
        self.assertEqual(r["kind"], "assert")

    def test_glibcxx_debug(self):
        r = E.explain_runtime({"verdict": "RE", "stderr": "Error: attempt to subscript container with out-of-bounds index 5, but \n"
                                                         "container only holds 3 elements.\n"})
        self.assertEqual(r["kind"], "glibcxx-debug")
        self.assertIn("indeks 5", r["title"])

    def test_python(self):
        tb = ('Traceback (most recent call last):\n  File "main.py", line 3, in <module>\n    print(a[5])\n'
              "IndexError: list index out of range\n")
        r = E.explain_runtime({"verdict": "RE", "exit_code": 1, "stderr": tb})
        self.assertEqual(r["kind"], "python")
        self.assertEqual(r["line"], 3)


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestRealCompiler(unittest.TestCase):
    """Prawdziwe komunikaty kompilatora (GCC/Clang dostępny na maszynie) → właściwe wzorce."""

    CASES = [
        ("int main(){ x = 5; }", "undeclared"),
        (H + "int main(){ int a = 5\n int b; }", "expected-semicolon"),
        (H + "int main(){ long long a = 1; int b = 2; cout << max(a, b); }", "no-matching-maxmin"),
        (H + "int main(){ vector<int> v; cout << v; }", "no-match-shl"),
        (H + "int count = 0;\nint main(){ count++; }", "ambiguous-ref"),
        (H + "int y1;\nint main(){ y1 = 2; }", "redeclared-kind"),
        (H + "struct P{int x;};\nint main(){ vector<P> v(3); sort(v.begin(), v.end()); }", ("no-match-lt", "no-match-less-call")),
        ("int main(){ int a = 1, b = 2; if (a + b = 3) {} }", "lvalue-required"),
    ]

    def test_compile_messages(self):
        bad = []
        for src, want in self.CASES:
            cr = runner.compile_cpp(src, mode="syntax")
            self.assertFalse(cr.ok, src)
            items = E.translate(cr.stderr, src)
            got = [i["pattern"] for i in items]
            wants = want if isinstance(want, tuple) else (want,)
            if not any(w in got[:2] for w in wants):
                bad.append(f"{want}: {got} :: {cr.stderr[:300]}")
        self.assertFalse(bad, "\n".join(bad))


if __name__ == "__main__":
    unittest.main()

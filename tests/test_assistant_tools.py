"""Testy narzędzi asystenta: piaskownica (uruchamianie kodu z czatu), komendy (/uruchom, /clear…), „nie rozumiem”."""
import os
import unittest

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

from oiforge import config
from oiforge.tools import _assistant_tools as AT
from oiforge.tools import assistant as A


class TestMessageParsing(unittest.TestCase):
    def test_inline_snippet_split_from_question(self):
        text, code, stdin = AT.split_message("co wypisze cout << 'a' + 1;")
        self.assertEqual((text, code, stdin), ("co wypisze", "cout << 'a' + 1;", ""))

    def test_input_line_and_fences(self):
        text, code, stdin = AT.split_message("int n; cin >> n;\ncout << n * 2;\nwejście: 21")
        self.assertIn("cin >> n", code)
        self.assertEqual(stdin.strip(), "21")
        text, code, stdin = AT.split_message("sprawdź to\n```cpp\nint main(){}\n```\n```input\n5\n```")
        self.assertEqual((text, code.strip(), stdin.strip()), ("sprawdź to", "int main(){}", "5"))

    def test_language_detection(self):
        self.assertEqual(AT.detect_lang("print(7 // 2)"), "python")
        self.assertEqual(AT.detect_lang("cout << 1;"), "cpp")

    def test_wrap_snippet_keeps_functions_global(self):
        src, wrapped = AT.wrap_cpp("int sq(int x) {\n    return x * x;\n}\ncout << sq(7);")
        self.assertTrue(wrapped)
        self.assertLess(src.index("int sq"), src.index("int main"))
        self.assertIn("#include <bits/stdc++.h>", src)
        src2, wrapped2 = AT.wrap_cpp("int main(){ cout << 1; }")
        self.assertTrue(wrapped2)
        self.assertTrue(src2.startswith("#include"))

    def test_commands_and_clarify(self):
        self.assertEqual(AT.parse_command("/uruchom cout << 1;"), ("uruchom", "cout << 1;"))
        self.assertEqual(AT.parse_command("/run x")[0], "uruchom")
        self.assertEqual(AT.parse_command("/wyczyść")[0], "clear")
        self.assertEqual(AT.parse_command("/nieistnieje")[0], "?")
        self.assertIsNone(AT.parse_command("jak użyć seta"))
        self.assertEqual(AT.clarify_mode("nie rozumiem"), "simple")
        self.assertEqual(AT.clarify_mode("Pokaż przykład"), "example")
        self.assertEqual(AT.clarify_mode("krok po kroku"), "steps")
        self.assertIsNone(AT.clarify_mode("jak posortować vector malejąco"))


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestSandboxRun(unittest.TestCase):
    def test_char_arithmetic_prints_ascii_code(self):
        r = AT.run_code("cout << 'a' + 1;")
        self.assertEqual((r["stage"], r["verdict"], r["stdout"].strip()), ("run", "OK", "98"))
        self.assertTrue(r["wrapped"])

    def test_stdin_and_python(self):
        r = AT.run_code("int a, b; cin >> a >> b; cout << a + b;", "2 3\n")
        self.assertEqual(r["stdout"].strip(), "5")
        r = AT.run_code("print(7 // 2, -7 % 3)")
        self.assertEqual(r["stdout"].strip(), "3 2")

    def test_compile_error_is_translated(self):
        r = AT.run_code("cout << x;")
        self.assertEqual((r["stage"], r["verdict"]), ("compile", "CE"))
        self.assertTrue(r["errors"] or r["raw"])

    def test_infinite_loop_times_out(self):
        r = AT.run_code("while (true) {}", timeout=0.5)
        self.assertEqual(r["verdict"], "TLE")

    @unittest.skipUnless(AT.sys.platform == "darwin", "piaskownica sandbox-exec tylko na macOS")
    def test_sandbox_blocks_writes_outside(self):
        target = os.path.join(os.path.expanduser("~"), "oif_sandbox_test_should_not_exist.txt")
        r = AT.run_code(f'FILE* f = fopen("{target}", "w"); cout << (f ? "zapisano" : "zablokowane");')
        self.assertEqual(r["stdout"].strip(), "zablokowane")
        self.assertFalse(os.path.exists(target))


@unittest.skipUnless(config.CXX, "brak kompilatora C++")
class TestAssistantWithTools(unittest.TestCase):
    def test_question_with_code_runs_it_and_explains(self):
        r = A.ask("co wypisze cout << 'a' + 1;")
        self.assertEqual(r["intent"]["id"], "run")
        run = next(b for b in r["blocks"] if b["type"] == "run")
        self.assertEqual(run["stdout"].strip(), "98")
        self.assertTrue(any(b["type"] == "notice" and "ASCII" in b.get("md", "") for b in r["blocks"]))

    def test_plain_question_is_not_run(self):
        r = A.ask("jak działa cout << endl")
        self.assertNotIn("run", [b["type"] for b in r["blocks"]])

    def test_clear_and_help(self):
        self.assertTrue(A.ask("/clear").get("clear"))
        r = A.ask("/pomoc")
        self.assertTrue(any(b["type"] == "table" for b in r["blocks"]))

    def test_dont_understand_uses_previous_topic(self):
        first = A.ask("jak posortować malejąco")
        hist = [{"q": "jak posortować malejąco", "topic": first["topic"]["id"], "uid": first.get("used_uid"), "clarify": 0}]
        r = A.ask("nie rozumiem", hist)
        self.assertEqual(r["intent"]["id"], "clarify")
        self.assertEqual(r["topic"]["id"], first["topic"]["id"])
        self.assertEqual(r["clarify"], 1)
        hist.append({"q": "nie rozumiem", "topic": r["topic"]["id"], "clarify": 1, "examples": r.get("examples", 0)})
        r2 = A.ask("nadal nie rozumiem", hist)
        self.assertEqual(r2["clarify"], 2)

    def test_dont_understand_without_context_asks_for_details(self):
        r = A.ask("nie rozumiem", [])
        self.assertTrue(r["blocks"])

    def test_full_tools_available(self):
        if not AT.FULL:
            self.skipTest("tryb podstawowy")
        r = A.ask("/sprawdz int a[5];\nfor (int i = 0; i <= 5; i++) a[i] = i;\ncout << a[0];")
        self.assertTrue(any(b["type"] == "errors" for b in r["blocks"]))
        r = A.ask("/zlozonosc for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) c++;")
        self.assertTrue(any(b["type"] == "complexity" and "n²" in b.get("text", "") for b in r["blocks"]))

    def test_basic_mode_hides_full_tools(self):
        old = AT.FULL
        AT.FULL = False
        try:
            self.assertEqual(AT.parse_command("/sprawdz x"), ("full-only", "sprawdz"))
            self.assertNotIn("sprawdz", [c["name"] for c in AT.available_commands()])
        finally:
            AT.FULL = old


if __name__ == "__main__":
    unittest.main()

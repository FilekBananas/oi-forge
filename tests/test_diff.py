"""Testy Output Difference Viewer (oiforge.tools.diff)."""
import random
import unittest

from oiforge.server_api import ApiError
from oiforge.tools import diff as D


def lcs_len(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    return dp[0][0]


class FirstDifferenceTest(unittest.TestCase):
    def test_equal_ignores_whitespace_and_crlf(self):
        self.assertIsNone(D.first_difference("1 2\r\n3\n\n", "1  2 3"))

    def test_token_difference_position(self):
        f = D.first_difference("1 2 3\n4 5\n", "1 2 3\n4 6\n")
        self.assertEqual(f["kind"], "token")
        self.assertEqual((f["index"], f["line"], f["col"]), (4, 2, 3))
        self.assertEqual((f["token_a"], f["token_b"]), ("5", "6"))
        self.assertIn("linia 2", f["message"])
        self.assertEqual(f["context_a"][-1], "5")

    def test_missing_and_extra(self):
        f = D.first_difference("1 2", "1 2 3")
        self.assertEqual(f["kind"], "missing")
        self.assertEqual(f["token_b"], "3")
        self.assertIsNone(f["token_a"])
        f = D.first_difference("1 2 3 4", "1 2 3")
        self.assertEqual(f["kind"], "extra")
        self.assertEqual(f["token_a"], "4")

    def test_float_eps(self):
        self.assertIsNone(D.first_difference("0.3333333", "0.33333334", float_eps=1e-6))
        self.assertIsNotNone(D.first_difference("0.3333", "0.3334", float_eps=1e-6))
        self.assertIsNone(D.first_difference("1000000.5", "1000000.4", float_eps=1e-6))   # względna
        self.assertIsNotNone(D.first_difference("abc", "abd", float_eps=1e-3))

    def test_lines_mode(self):
        f = D.first_difference("a b\nc d\n", "a b\nc  e\n", mode="lines")
        self.assertEqual((f["line"], f["kind"]), (2, "line"))
        self.assertIsNone(D.first_difference("a  b \n", "a b\n", mode="lines"))
        self.assertIsNotNone(D.first_difference("a  b\n", "a b\n", mode="lines", ignore_whitespace=False))

    def test_chars_mode(self):
        f = D.first_difference("ab c", "ab  c", mode="chars", ignore_whitespace=False)
        self.assertEqual((f["line"], f["col"], f["char_a"], f["char_b"]), (1, 4, "c", " "))
        f = D.first_difference("x\ny", "x\nz", mode="chars")
        self.assertEqual((f["line"], f["col"]), (2, 1))

    def test_case_only_hint(self):
        r = D.compare("Tak\n", "TAK\n")
        self.assertFalse(r["equal"])
        self.assertTrue(any("wielkości liter" in h for h in r["hints"]))


class CompareTest(unittest.TestCase):
    def test_rows_and_stats(self):
        a = "\n".join(str(i) for i in range(1, 11)) + "\n"
        b = a.replace("5\n", "50\n") + "11\n"
        r = D.compare(a, b)
        self.assertFalse(r["equal"])
        types = [x["type"] for x in r["rows"]]
        self.assertIn("chg", types)
        self.assertIn("add", types)
        st = r["stats"]
        self.assertEqual(st["missing_at_end"], 1)
        self.assertEqual(st["changed_lines"], 1)
        self.assertTrue(st["token_count_differs"])
        chg = next(x for x in r["rows"] if x["type"] == "chg")
        self.assertEqual(chg["a"], "5")
        self.assertTrue(any(seg[1] for seg in chg["seg_a"]))

    def test_equal_whitespace_hint(self):
        r = D.compare("1 2\n3", "1\n2 3\n")
        self.assertTrue(r["equal"])
        self.assertTrue(r["hints"])

    def test_skip_collapse(self):
        a = "\n".join(map(str, range(1000)))
        b = a.replace("\n500\n", "\n5000\n")
        r = D.compare(a, b, context=2)
        self.assertLess(len(r["rows"]), 20)
        self.assertEqual(sum(1 for x in r["rows"] if x["type"] == "chg"), 1)
        self.assertTrue(any(x["type"] == "skip" for x in r["rows"]))

    def test_large_input_fast(self):
        a = "\n".join(str(random.randint(0, 9)) for _ in range(200_000))
        b = a[:-1] + ("0" if a[-1] != "0" else "1")
        r = D.compare(a, b)
        self.assertFalse(r["equal"])
        self.assertEqual(r["first"]["line"], 200_000)

    def test_myers_random_minimal(self):
        rnd = random.Random(1)
        for _ in range(200):
            a = [rnd.randint(0, 3) for _ in range(rnd.randint(0, 25))]
            b = [rnd.randint(0, 3) for _ in range(rnd.randint(0, 25))]
            ops = D.myers(a, b)
            # skrypt odtwarza b z a
            out = []
            eq = 0
            for op, i, j in ops:
                if op == "eq":
                    self.assertEqual(a[i], b[j])
                    out.append(a[i])
                    eq += 1
                elif op == "ins":
                    out.append(b[j])
            self.assertEqual(out, b)
            self.assertEqual(sum(1 for o in ops if o[0] in ("eq", "del")), len(a))
            self.assertEqual(eq, lcs_len(a, b))           # minimalność (Myers = najdłuższy wspólny podciąg)

    def test_myers_limit_fallback(self):
        a = list(range(5000))
        b = list(range(5000, 10000))
        self.assertIsNone(D.myers(a, b, max_d=100))
        r = D.compare("\n".join(map(str, a)), "\n".join(map(str, b)))
        self.assertTrue(r["stats"]["approximate"])
        self.assertTrue(r["truncated"] or len(r["rows"]) <= 3001)


class HttpHandlerTest(unittest.TestCase):
    def test_handle_diff(self):
        r = D.handle_diff({"a": "1 2", "b": "1 3", "mode": "tokens"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["first"]["token_no"], 2)
        r = D.handle_diff({"a": "1.0000001", "b": "1", "float_eps": "1e-6"})
        self.assertTrue(r["equal"])

    def test_errors(self):
        with self.assertRaises(ApiError):
            D.handle_diff({"a": "x"})
        with self.assertRaises(ApiError):
            D.handle_diff({"a": "x", "b": "y", "mode": "bits"})
        with self.assertRaises(ApiError):
            D.handle_diff({"a": "x", "b": "y", "float_eps": "abc"})


if __name__ == "__main__":
    unittest.main()

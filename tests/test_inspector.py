"""Testy inspektora wejścia/wyjścia: rozpoznawanie struktury testów i nakładanie wyjścia."""
from __future__ import annotations

import unittest

from oiforge.server_api import ApiError
from oiforge.tools import inspector as I


def top(text, **kw):
    r = I.detect(text, **kw)
    return r["candidates"][0], r


def part(cand, kind):
    return next(p for p in cand["parts"] if p["kind"] == kind)


class DetectTest(unittest.TestCase):
    def test_array_with_count(self):
        c, _ = top("5\n3 1 4 1 5\n")
        self.assertEqual(c["kind"], "array")
        a = part(c, "array")
        self.assertEqual(a["values"], [3, 1, 4, 1, 5])
        self.assertEqual((a["min"], a["max"], a["n"]), (1, 5, 5))
        self.assertGreater(c["confidence"], 0.7)

    def test_vertical_array(self):
        c, _ = top("4\n10\n20\n30\n40\n")
        self.assertEqual(part(c, "array")["values"], [10, 20, 30, 40])

    def test_single_number(self):
        c, _ = top("42\n")
        self.assertEqual(c["kind"], "values")
        self.assertGreater(c["confidence"], 0.8)

    def test_numbers_without_header(self):
        r = I.detect("1 2 3 4 5 6 7\n")
        kinds = [c["kind"] for c in r["candidates"]]
        self.assertIn("array", kinds)

    def test_matrix_nm(self):
        c, _ = top("2 3\n1 2 3\n4 5 6\n")
        self.assertEqual(c["kind"], "matrix")
        m = part(c, "matrix")
        self.assertEqual((m["n"], m["m"], m["rows"][1], m["max"]), (2, 3, [4, 5, 6], 6))

    def test_square_matrix(self):
        c, _ = top("3\n1 2 3\n4 5 6\n7 8 9\n")
        self.assertEqual(c["kind"], "matrix")

    def test_grid_with_header(self):
        c, _ = top("3 4\n#..#\n.##.\n#..#\n")
        self.assertEqual(c["kind"], "grid")
        g = part(c, "grid")
        self.assertEqual(g["legend"], {".": 6, "#": 6})
        self.assertEqual(g["rows"][1], ".##.")

    def test_grid_without_header(self):
        c, _ = top("S.#\n..#\n#.E\n")
        self.assertEqual(c["kind"], "grid")
        self.assertEqual(set(part(c, "grid")["legend"]), {"S", ".", "#", "E"})

    def test_graph_unweighted(self):
        c, _ = top("4 5\n1 2\n2 3\n3 4\n4 1\n1 3\n")
        g = part(c, "graph")
        self.assertEqual((c["kind"], g["n"], g["m"], g["base"], g["weighted"]), ("graph", 4, 5, 1, False))
        self.assertEqual(g["degrees"], [3, 2, 3, 2])

    def test_graph_weighted(self):
        c, _ = top("3 3\n1 2 5\n2 3 7\n1 3 10\n")
        g = part(c, "graph")
        self.assertTrue(g["weighted"])
        self.assertEqual(g["edges"][2], [1, 3, 10])

    def test_graph_zero_indexed_tree(self):
        c, _ = top("4 3\n0 1\n1 2\n2 3\n")
        g = part(c, "tree")
        self.assertEqual(g["base"], 0)

    def test_tree_edges(self):
        c, _ = top("5\n1 2\n1 3\n3 4\n3 5\n")
        self.assertEqual(c["kind"], "tree")
        self.assertEqual(part(c, "tree")["m"], 4)

    def test_tree_parents(self):
        c, _ = top("5\n1 1 2 2\n")
        t = part(c, "tree")
        self.assertEqual(t["parents"], [1, 1, 2, 2])
        self.assertEqual(sorted(map(tuple, t["edges"])), [(1, 2), (1, 3), (2, 4), (2, 5)])
        self.assertEqual(t["root"], 1)

    def test_intervals(self):
        c, _ = top("3\n1 4\n2 6\n8 10\n")
        p = part(c, "intervals")
        self.assertEqual(p["items"], [[1, 4], [2, 6], [8, 10]])

    def test_array_then_queries(self):
        c, _ = top("5 3\n1 2 3 4 5\n1 3\n2 4\n1 5\n")
        self.assertEqual(part(c, "array")["values"], [1, 2, 3, 4, 5])
        self.assertEqual(part(c, "intervals")["q"], 3)

    def test_typed_queries(self):
        c, _ = top("5 4\n1 2 3 4 5\n1 1 3\n2 2\n1 2 5\n2 4\n")
        self.assertEqual(c["kind"], "queries")
        self.assertEqual(part(c, "queries")["items"][1], [2, 2])

    def test_two_arrays(self):
        c, _ = top("3 4\n1 2 3\n4 5 6 7\n")
        arrs = [p for p in c["parts"] if p["kind"] == "array"]
        self.assertEqual([a["n"] for a in arrs], [3, 4])

    def test_multitest(self):
        c, _ = top("2\n3\n1 2 3\n2\n5 6\n")
        self.assertEqual(c["kind"], "multitest")
        mt = part(c, "multitest")
        self.assertEqual(mt["t"], 2)
        self.assertEqual(len(mt["blocks"]), 2)
        self.assertEqual(mt["blocks"][1]["parts"][1]["values"], [5, 6])

    def test_multitest_graphs(self):
        c, _ = top("2\n3 2\n1 2\n2 3\n2 1\n1 2\n")
        self.assertEqual(c["kind"], "multitest")
        self.assertIn(part(c, "multitest")["blocks"][0]["parts"][1]["kind"], ("graph", "tree"))

    def test_strings(self):
        c, _ = top("3\nabc\nkajak\nxyz\n")
        self.assertEqual(c["kind"], "strings")
        self.assertEqual(part(c, "strings")["lengths"], [3, 5, 3])

    def test_graph_with_queries(self):
        c, _ = top("4 3 2\n1 2\n2 3\n3 4\n1 4\n2 3\n")
        self.assertEqual(part(c, "tree")["n"], 4)
        self.assertEqual(part(c, "queries")["q"], 2)

    def test_hint_and_crlf(self):
        c, r = top("2 2\r\n1 2\r\n2 1\r\n")          # niejednoznaczne: dwie tablice / graf / macierz
        self.assertIn(c["kind"], ("array", "graph", "matrix"))
        self.assertEqual(top("2 2\r\n1 2\r\n2 1\r\n", hint="graph")[0]["kind"], "graph")
        c2, _ = top("2 2\r\n1 2\r\n2 1\r\n", hint="matrix")
        self.assertEqual(c2["kind"], "matrix")
        self.assertIn([2, 1], part(c2, "matrix")["rows"])

    def test_empty_and_errors(self):
        self.assertEqual(I.detect("")["candidates"], [])
        with self.assertRaises(ApiError):
            I.handle_detect({"text": "   "})
        with self.assertRaises(ApiError):
            I.handle_detect({"text": 5})


class OverlayTest(unittest.TestCase):
    def test_path_on_graph(self):
        c, r = top("4 5\n1 2\n2 3\n3 4\n4 1\n1 3\n", output="1 3 4\n")
        ov = r["output_overlay"][c["id"]]
        self.assertEqual((ov["kind"], ov["vertices"]), ("path", [1, 3, 4]))

    def test_path_with_count(self):
        c, r = top("4 3\n1 2\n2 3\n3 4\n", output="4\n1 2 3 4\n")
        self.assertEqual(r["output_overlay"][c["id"]]["vertices"], [1, 2, 3, 4])

    def test_node_labels(self):
        c, r = top("4 3\n1 2\n2 3\n3 4\n", output="0 1 2 3\n")
        ov = r["output_overlay"][c["id"]]
        self.assertEqual((ov["kind"], ov["values"]), ("node_labels", [0, 1, 2, 3]))
        c2, r2 = top("4 3\n1 2\n2 3\n3 4\n", output="0 5 9 12\n")
        self.assertEqual(r2["output_overlay"][c2["id"]]["kind"], "node_labels")

    def test_query_answers(self):
        c, r = top("5 3\n1 2 3 4 5\n1 3\n2 4\n1 5\n", output="6\n9\n15\n")
        ov = r["output_overlay"][c["id"]]
        self.assertEqual((ov["kind"], ov["values"]), ("answers", [6, 9, 15]))

    def test_array_output(self):
        c, r = top("5\n3 1 4 1 5\n", output="1 1 3 4 5\n")
        self.assertEqual(r["output_overlay"][c["id"]]["kind"], "array")

    def test_multitest_answers(self):
        c, r = top("2\n3\n1 2 3\n2\n5 6\n", output="TAK\nNIE\n")
        self.assertEqual(r["output_overlay"][c["id"]]["kind"], "per_test")
        self.assertTrue(r["output_parsed"]["yes_no"])


if __name__ == "__main__":
    unittest.main()

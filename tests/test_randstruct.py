"""Testy biblioteki losowych struktur (oiforge.core.randstruct)."""
import math
import random
import unittest
from collections import deque

from oiforge.core import randstruct as rs


def _bfs_reach(rows, cols, g, free):
    if g[0][0] != free or g[rows - 1][cols - 1] != free:
        return False
    seen = {(0, 0)}
    dq = deque([(0, 0)])
    while dq:
        i, j = dq.popleft()
        if (i, j) == (rows - 1, cols - 1):
            return True
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < rows and 0 <= b < cols and (a, b) not in seen and g[a][b] == free:
                seen.add((a, b))
                dq.append((a, b))
    return False


class TestRng(unittest.TestCase):
    def test_deterministic(self):
        a, b = rs.Rng(42), rs.Rng(42)
        self.assertEqual([a.randint(1, 10 ** 9) for _ in range(50)], [b.randint(1, 10 ** 9) for _ in range(50)])
        c = rs.Rng(43)
        self.assertNotEqual([rs.Rng(42).randint(1, 10 ** 9) for _ in range(5)], [c.randint(1, 10 ** 9) for _ in range(5)])

    def test_ranges(self):
        r = rs.Rng(1)
        for _ in range(2000):
            x = r.randint(-3, 3)
            self.assertTrue(-3 <= x <= 3)
            self.assertTrue(0 <= r.below(7) < 7)
            self.assertTrue(0.0 <= r.random() < 1.0)
        self.assertEqual({r.randint(0, 2) for _ in range(300)}, {0, 1, 2})
        big = r.randint(-(1 << 62), 1 << 62)
        self.assertTrue(-(1 << 62) <= big <= 1 << 62)
        with self.assertRaises(rs.StructError):
            r.randint(5, 4)

    def test_python_compatible_seed(self):
        # getrandbits ze zwykłego random.Random — gwarancja zgodności z eksportem (MT19937 + init_by_array)
        self.assertEqual(rs.Rng(7).bits(32), random.Random(7).getrandbits(32))

    def test_shuffle_is_permutation(self):
        x = list(range(100))
        rs.Rng(3).shuffle(x)
        self.assertEqual(sorted(x), list(range(100)))
        self.assertNotEqual(x, list(range(100)))


class TestTrees(unittest.TestCase):
    def test_all_shapes_are_trees(self):
        for shape in rs.TREE_SHAPES:
            for n in (1, 2, 3, 5, 17, 100):
                for base in (0, 1):
                    for seed in range(3):
                        e = rs.random_tree(n, shape, seed, base=base)
                        self.assertEqual(len(e), n - 1, (shape, n))
                        self.assertTrue(rs.is_tree(n, e, base), (shape, n, seed, e))
                        for u, v in e:
                            self.assertTrue(base <= u < n + base and base <= v < n + base)

    def test_shapes_have_their_form(self):
        n = 50
        deg = lambda e: [sum(1 for x in e if v in x[:2]) for v in range(1, n + 1)]  # noqa: E731
        self.assertEqual(max(deg(rs.random_tree(n, "line", 1))), 2)
        self.assertEqual(max(deg(rs.random_tree(n, "star", 1))), n - 1)
        self.assertLessEqual(max(deg(rs.random_tree(n, "binary", 1))), 3)
        self.assertEqual(rs.random_tree(4, "line", 1, shuffle=False), [(1, 2), (2, 3), (3, 4)])
        self.assertEqual(rs.tree_shape("ścieżka"), "line")
        with self.assertRaises(rs.StructError):
            rs.tree_shape("kwadrat")

    def test_weights(self):
        e = rs.random_tree(30, "random", 5, weights=(10, 20))
        self.assertTrue(all(len(x) == 3 and 10 <= x[2] <= 20 for x in e))
        self.assertTrue(rs.is_tree(30, e))

    def test_parents(self):
        for shape in rs.TREE_SHAPES:
            for shuffle in (True, False):
                for n in (1, 2, 9, 60):
                    par = rs.tree_parents(n, shape, 11, shuffle=shuffle)
                    self.assertEqual(par[0], -1)
                    self.assertTrue(all(0 <= par[i] < i for i in range(1, n)), (shape, shuffle, par))
        self.assertEqual(rs.tree_parents(5, "line", 1, shuffle=False), [-1, 0, 1, 2, 3])

    def test_forest(self):
        for n, k in ((1, 1), (10, 1), (10, 3), (10, 10), (40, 7)):
            e = rs.random_forest(n, k, 2)
            self.assertEqual(len(e), n - k)
            self.assertEqual(rs.components(n, e), k)
        with self.assertRaises(rs.StructError):
            rs.random_forest(3, 4, 1)


class TestGraphs(unittest.TestCase):
    def _simple(self, e, directed):
        keys = [(u, v) if directed else (min(u, v), max(u, v)) for u, v, *_ in e]
        return len(keys) == len(set(keys)) and all(u != v for u, v in keys)

    def test_connected_graph(self):
        for n, m in ((1, 0), (2, 1), (5, 4), (5, 10), (30, 29), (30, 100), (30, 435), (200, 400)):
            for seed in range(3):
                e = rs.random_graph(n, m, seed)
                self.assertEqual(len(e), m)
                self.assertTrue(rs.is_connected(n, e), (n, m, seed))
                self.assertTrue(self._simple(e, False))

    def test_directed_and_disconnected(self):
        e = rs.random_graph(10, 90, 1, directed=True)                      # pełny skierowany
        self.assertEqual(len(e), 90)
        self.assertTrue(self._simple(e, True))
        e = rs.random_graph(20, 5, 2, connected=False)
        self.assertEqual(len(e), 5)
        self.assertGreater(rs.components(20, e), 1)

    def test_loops_and_multi(self):
        e = rs.random_graph(3, 6, 1, loops=True, connected=False)          # 3 krawędzie + 3 pętle = wszystko
        self.assertEqual(len(set((min(u, v), max(u, v)) for u, v in e)), 6)
        e = rs.random_graph(2, 10, 1, multi=True, connected=False)
        self.assertEqual(len(e), 10)
        self.assertTrue(all(u != v for u, v in e))

    def test_errors(self):
        with self.assertRaises(rs.StructError):
            rs.random_graph(5, 3, 1)                                       # spójny potrzebuje ≥ 4
        with self.assertRaises(rs.StructError):
            rs.random_graph(4, 7, 1)                                       # maks. 6
        with self.assertRaises(rs.StructError):
            rs.random_dag(4, 7, 1)

    def test_dag_acyclic(self):
        for n, m in ((1, 0), (5, 10), (20, 50), (30, 400), (8, 7)):
            for seed in range(3):
                e = rs.random_dag(n, m, seed)
                self.assertEqual(len(e), m)
                self.assertFalse(rs.has_cycle(n, e, directed=True), (n, m, seed))
                self.assertTrue(self._simple(e, True))
        e = rs.random_dag(15, 14, 4, connected=True)
        self.assertTrue(rs.is_connected(15, e))

    def test_bipartite(self):
        e = rs.random_bipartite(4, 6, 20, 1)
        self.assertEqual(len(set(e)), 20)
        self.assertTrue(all(1 <= u <= 4 and 1 <= v <= 6 for u, v in e))
        e = rs.random_bipartite(3, 3, 9, 2, shift=True)
        self.assertTrue(all(1 <= u <= 3 and 4 <= v <= 6 for u, v in e))
        self.assertTrue(rs.is_bipartite(6, e))

    def test_grid_graph(self):
        e = rs.grid_graph(3, 4)
        self.assertEqual(len(e), 3 * 3 + 2 * 4)
        self.assertTrue(rs.is_connected(12, e))
        self.assertTrue(rs.is_bipartite(12, e))


class TestGridsSequencesStrings(unittest.TestCase):
    def test_grid(self):
        g = rs.random_grid(6, 9, 1, alphabet="#.", p=0.4)
        self.assertEqual(len(g), 6)
        self.assertTrue(all(len(r) == 9 and set(r) <= {"#", "."} for r in g))
        for seed in range(20):
            g = rs.random_grid(7, 8, seed, alphabet="#.", p=0.7, path=True)
            self.assertTrue(_bfs_reach(7, 8, g, "."), g)
        g = rs.random_grid(3, 3, 1, alphabet="a-c")
        self.assertTrue(set("".join(g)) <= set("abc"))

    def test_permutation_distinct(self):
        for n in (0, 1, 10, 1000):
            self.assertTrue(rs.is_permutation(rs.permutation(n, n), 1))
        self.assertEqual(sorted(rs.permutation(5, 1, base=0)), [0, 1, 2, 3, 4])
        for n, lo, hi in ((5, 1, 5), (10, 1, 12), (50, 1, 10 ** 12), (0, 3, 2)):
            d = rs.distinct_ints(n, lo, hi, 7)
            self.assertEqual(len(d), n)
            self.assertEqual(len(set(d)), n)
            self.assertTrue(all(lo <= x <= hi for x in d))
        with self.assertRaises(rs.StructError):
            rs.distinct_ints(6, 1, 5, 1)

    def test_sequences(self):
        n = 200
        a = rs.sequence(n, 1, 1000, "sorted", 1)
        self.assertEqual(a, sorted(a))
        a = rs.sequence(n, 1, 1000, "desc", 1)
        self.assertEqual(a, sorted(a, reverse=True))
        a = rs.sequence(n, 1, 10 ** 9, "distinct", 1)
        self.assertEqual(len(set(a)), n)
        self.assertEqual(len(set(rs.sequence(n, 1, 9, "equal", 1))), 1)
        self.assertLessEqual(len(set(rs.sequence(n, 1, 10 ** 9, "repeats", 1))), math.isqrt(n))
        a = rs.sequence(n, 1, 10 ** 9, "nearly", 1)
        inv = sum(1 for i in range(n - 1) if a[i] > a[i + 1])
        self.assertLess(inv, n // 5)
        z = rs.sequence(7, 1, 100, "zigzag", 3)
        self.assertTrue(z[0] <= z[2] and z[1] >= z[3])
        self.assertEqual(rs.sequence_kind("prawie"), "nearly")

    def test_strings(self):
        self.assertEqual(rs.expand_alphabet("a-e"), "abcde")
        self.assertEqual(rs.expand_alphabet("a-c0-2_"), "abc012_")
        self.assertEqual(rs.expand_alphabet("+-*"), "+-*")
        self.assertEqual(rs.expand_alphabet("dna"), "ACGT")
        s = rs.random_string(50, "xy", 1)
        self.assertTrue(len(s) == 50 and set(s) <= {"x", "y"})
        for n in range(0, 12):
            p = rs.palindrome(n, "abc", n)
            self.assertEqual(p, p[::-1])
            self.assertEqual(len(p), n)
        s = rs.periodic_string(20, 3, "a-z", 2)
        self.assertTrue(all(s[i] == s[i + 3] for i in range(17)))
        self.assertEqual(rs.thue_morse(8), "abbabaab")

    def test_intervals_points_queries(self):
        iv = rs.random_intervals(100, 1, 10, 1)
        self.assertTrue(all(1 <= l <= r <= 10 for l, r in iv))
        iv = rs.random_intervals(100, 1, 3, 1, distinct=True)
        self.assertTrue(all(l < r for l, r in iv))
        pts = rs.random_points(9, 0, 2, 1)
        self.assertEqual(len(set(pts)), 9)
        with self.assertRaises(rs.StructError):
            rs.random_points(10, 0, 2, 1)
        qs = rs.random_queries(200, 10, "point range_value pair none", 4, lo=5, hi=6)
        for q in qs:
            t = q[0]
            self.assertIn(t, (1, 2, 3, 4))
            if t == 1:
                self.assertEqual(len(q), 2)
            elif t == 2:
                self.assertTrue(len(q) == 4 and q[1] <= q[2] and 5 <= q[3] <= 6)
            elif t == 3:
                self.assertTrue(len(q) == 3 and q[1] != q[2])
            else:
                self.assertEqual(len(q), 1)

    def test_determinism_everywhere(self):
        f = lambda s: (rs.random_tree(20, "random", s), rs.random_graph(15, 30, s), rs.random_dag(10, 20, s),  # noqa: E731
                       rs.random_grid(4, 5, s, p=0.3), rs.sequence(10, 1, 9, "nearly", s), rs.random_string(9, "ab", s))
        self.assertEqual(f(99), f(99))
        self.assertNotEqual(f(99), f(100))


if __name__ == "__main__":
    unittest.main()

"""Testy optymalizatora (oiforge.tools.optimizer): reguły, bezpieczeństwo, diff, kompilacja, benchmark."""
import json
import shutil
import unittest
from pathlib import Path

from oiforge.tools import optimizer as op

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ROOT / "user_data" / "programs" / "index.json"
HAVE_CXX = bool(shutil.which("g++-16") or shutil.which("g++") or shutil.which("clang++"))
H = "#include <bits/stdc++.h>\nusing namespace std;\n"

BASIC = H + """int suma(vector<int> v) {
    int s = 0;
    for (int x : v) s += x;
    return s;
}
int main() {
    int n;
    cin >> n;
    vector<int> a;
    for (int i = 0; i < n; i++) {
        int x;
        cin >> x;
        a.push_back(x);
    }
    string s = "";
    for (int i = 0; i < n; i++) s = s + 'a';
    double d = 1.5;
    cout << pow(d, 2) << endl;
    for (int i = 0; i < n; i++) cout << a[i] << endl;
    cout << suma(a) << ' ' << s.size() << endl;
}
"""


def rules(res):
    return {c["rule"]: c for c in res["changes"]}


class TestRules(unittest.TestCase):
    def test_safe_rules_applied(self):
        r = op.optimize(BASIC)
        rs = rules(r)
        for rid in ("fast_io", "endl", "const_ref", "string_concat", "pow_square", "reserve"):
            self.assertIn(rid, rs, rid)
            self.assertTrue(rs[rid]["safe"], rid)
            self.assertTrue(rs[rid]["applied"], rid)
        code = r["optimized_code"]
        self.assertIn("ios_base::sync_with_stdio(false);", code)
        self.assertIn("cin.tie(nullptr);", code)
        self.assertNotIn("endl", code)
        self.assertIn("int suma(const vector<int>& v)", code)
        self.assertIn("s += 'a'", code)
        self.assertIn("d * d", code)
        self.assertIn("a.reserve(n);", code)
        self.assertTrue(r["changed"])

    def test_unsafe_not_applied_by_default(self):
        r = op.optimize(BASIC)
        rs = rules(r)
        self.assertIn("pragma", rs)
        self.assertFalse(rs["pragma"]["safe"])
        self.assertFalse(rs["pragma"]["applied"])
        self.assertNotIn("#pragma", r["optimized_code"])

    def test_apply_list(self):
        r = op.optimize(BASIC, ["pragma"])
        self.assertTrue(r["optimized_code"].startswith("#pragma GCC optimize"))
        self.assertIn("endl", r["optimized_code"])       # tylko wskazana reguła
        self.assertEqual(r["applied"], ["pragma"])

    def test_interactive_keeps_endl(self):
        code = H + """int main(){ int lo = 1, hi = 1000000;
  while (lo <= hi) { int mid = (lo + hi) / 2; cout << mid << endl; string odp; cin >> odp;
    if (odp == "OK") break; else if (odp == "MNIEJSZA") hi = mid - 1; else lo = mid + 1; } }"""
        r = op.optimize(code)
        self.assertTrue(r["interactive"])
        self.assertIn("endl", r["optimized_code"])
        fio = rules(r).get("fast_io")
        if fio:
            self.assertFalse(fio["applied"])

    def test_mixed_io_not_auto(self):
        code = H + "int main(){ int n; scanf(\"%d\", &n); for (int i = 0; i < n; i++) { int x; cin >> x; printf(\"%d\\n\", x); } }"
        r = op.optimize(code)
        fio = rules(r)["fast_io"]
        self.assertFalse(fio["safe"])
        self.assertFalse(fio["applied"])

    def test_modified_param_not_const(self):
        code = H + "void f(vector<int> v) { v.push_back(1); cout << v.size(); }\nint main(){ vector<int> a(5); f(a); }"
        r = op.optimize(code)
        rs = rules(r)
        self.assertNotIn("const_ref", rs)
        self.assertIn("pass_by_ref", rs)
        self.assertFalse(rs["pass_by_ref"]["applied"])

    def test_set_lower_bound(self):
        code = H + "int main(){ set<int> s = {1, 5, 9}; int q; cin >> q; while (q--) { int x; cin >> x; auto it = lower_bound(s.begin(), s.end(), x); cout << *it << '\\n'; } }"
        r = op.optimize(code)
        self.assertIn("s.lower_bound(x)", r["optimized_code"])

    def test_range_for_ref(self):
        code = H + "int main(){ vector<string> w(3, \"ab\"); int t = 0; for (auto x : w) t += x.size(); cout << t; }"
        r = op.optimize(code)
        self.assertIn("for (const auto& x : w)", r["optimized_code"])

    def test_strlen_hoist(self):
        code = H + "char s[100005];\nint main(){ scanf(\"%s\", s); int c = 0;\n  for (int i = 0; i < strlen(s); i++) if (s[i] == 'a') c++;\n  printf(\"%d\", c); }"
        r = op.optimize(code)
        self.assertIn("const int len_s = strlen(s);", r["optimized_code"])
        self.assertIn("i < len_s", r["optimized_code"])

    def test_string_concat_trap(self):
        # s = s + a + b dla znaków: a + b to int — NIE wolno zamienić na s += a + b
        code = H + "int main(){ string s; char a = 'x', b = 'y'; s = s + a + b; cout << s; }"
        r = op.optimize(code)
        self.assertNotIn("string_concat", rules(r))

    def test_suggestions(self):
        code = H + """vector<int> g[200005]; bool vis[200005];
void dfs(int v) { vis[v] = true; for (int u : g[v]) if (!vis[u]) dfs(u); }
int main(){ int n, m; cin >> n >> m; map<int,int> cnt; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); cnt[a]++; }
  dfs(1); vector<vector<int>> t(n, vector<int>(n)); int sum = 0; for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) sum += t[i][j] * t[j][i];
  cout << sum << ' ' << cnt.size(); }"""
        rs = rules(op.optimize(code))
        for rid in ("iterative_dfs", "unordered_map", "flat_2d", "long_long"):
            self.assertIn(rid, rs, rid)
            self.assertFalse(rs[rid]["applied"], rid)
        self.assertIn("```cpp", rs["iterative_dfs"]["suggestion_md"])

    def test_long_long_product_fix(self):
        code = H + "int main(){ int a, b; cin >> a >> b; long long c = a * b; cout << c; }"
        r = op.optimize(code)
        self.assertIn("long long c = 1LL * a * b;", r["optimized_code"])

    def test_diff(self):
        r = op.optimize(BASIC)
        self.assertTrue(r["diff"].startswith("--- oryginał.cpp"))
        kinds = {row["type"] for row in r["diff_rows"]}
        self.assertTrue(kinds & {"chg", "add"})
        self.assertTrue(all("a_text" in row and "b_text" in row for row in r["diff_rows"]))

    def test_idempotent(self):
        once = op.optimize(BASIC)["optimized_code"]
        twice = op.optimize(once)
        self.assertEqual(twice["optimized_code"], once)

    def test_robust(self):
        for code in ["", "}}}", "int main(", "x" * 2000, "int main(){}"]:
            self.assertIn("optimized_code", op.optimize(code))

    def test_rules_endpoint(self):
        rs = op.handle_rules({})["rules"]
        self.assertGreaterEqual(len(rs), 12)


class TestAlgorithmic(unittest.TestCase):
    """Poprawki algorytmiczne: ranking (najważniejsza na górze), zysk złożoności, diff, przypadki negatywne."""

    def test_bench_top_suggestion(self):
        from tests import bench_cx_opt as B
        self.assertGreaterEqual(len(B.OPT_CASES), 15)
        bad = []
        for c in B.OPT_CASES:
            ch = op.optimize(c["code"])["changes"]
            top = ch[0]["rule"] if ch else None
            if top not in c["top"]:
                bad.append(f"{c['name']}: oczekiwano {c['top'][0]}, jest {top}")
            target = next((x for x in ch if x["rule"] in c["top"]), None)
            if c["rewrite"] and not (target and target["has_edit"]):
                bad.append(f"{c['name']}: brak automatycznego przepisania")
            if c["gain"] and not (target and c["gain"] in (target["gain"] or "").replace("O(", "").replace(")", "")):
                bad.append(f"{c['name']}: zysk {target and target['gain']}, oczekiwano … → O({c['gain']})")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_prefix_sums_rewrite_shape(self):
        from tests import bench_cx_opt as B
        r = op.optimize(B.OPT_CASES[0]["code"])
        ch = r["changes"][0]
        self.assertEqual(ch["rule"], "prefix_sums")
        self.assertEqual(ch["category"], "algorytm")
        self.assertEqual(ch["gain"], "O(n·q) → O(n + q)")
        self.assertTrue(ch["applied"] and ch["safe"])
        self.assertTrue(any(row["type"] != "eq" for row in ch["patch"]))
        code = r["optimized_code"]
        self.assertIn("vector<long long> pref_a(a.size() + 1, 0);", code)
        self.assertIn("if (l <= r) s += pref_a[r + 1] - pref_a[l];", code)
        impacts = [c["impact"] for c in r["changes"]]
        self.assertEqual(impacts, sorted(impacts, reverse=True))

    def test_prefix_sums_not_when_array_changes(self):
        code = H + """int main(){ int n, q; cin >> n >> q; vector<long long> a(n); for (auto &x : a) cin >> x;
  while (q--) { int t, l, r; cin >> t >> l >> r; if (t == 1) { a[l] = r; continue; }
    long long s = 0; for (int i = l; i <= r; i++) s += a[i]; cout << s << '\\n'; } }"""
        ch = rules(op.optimize(code)).get("prefix_sums")
        self.assertIsNotNone(ch)
        self.assertFalse(ch["has_edit"])
        self.assertIn("Fenwick", ch["suggestion_md"])

    def test_prefix_sums_not_for_doubles(self):
        code = H + """int main(){ int n, q; cin >> n >> q; vector<double> a(n); for (auto &x : a) cin >> x;
  while (q--) { int l, r; cin >> l >> r; double s = 0; for (int i = l; i <= r; i++) s += a[i]; cout << s << '\\n'; } }"""
        self.assertNotIn("prefix_sums", rules(op.optimize(code)))

    def test_hoist_sort_not_when_used_after_loop(self):
        code = H + """int main(){ int n, q; cin >> n >> q; vector<int> a(n); for (auto &x : a) cin >> x;
  while (q--) { int x; cin >> x; sort(a.begin(), a.end()); cout << a[0] + x << '\\n'; }
  cout << a[0] << '\\n'; }"""
        self.assertNotIn("hoist_sort", rules(op.optimize(code)))

    def test_memoize_not_impure(self):
        code = H + """long long calls = 0;
long long f(int n) { calls++; if (n < 2) return n; return f(n - 1) + f(n - 2); }
int main(){ int n; cin >> n; cout << f(n) << ' ' << calls << '\\n'; }"""
        self.assertNotIn("memoize", rules(op.optimize(code)))

    def test_deque_not_when_passed_to_function(self):
        code = H + """void pokaz(vector<int>& v) { for (int x : v) cout << x << ' '; }
int main(){ int n; cin >> n; vector<int> v; for (int i = 0; i < n; i++) { int x; cin >> x; v.insert(v.begin(), x); } pokaz(v); }"""
        ch = rules(op.optimize(code)).get("erase_begin")
        self.assertIsNotNone(ch)
        self.assertFalse(ch["has_edit"])

    def test_reserve_skipped_for_deque(self):
        code = H + """int main(){ int n; cin >> n; vector<int> v; for (int i = 0; i < n; i++) { int t; cin >> t;
  if (t > 0) v.push_back(t); else if (!v.empty()) v.erase(v.begin()); } cout << v.size() << '\\n'; }"""
        r = op.optimize(code)
        self.assertIn("deque<int> v;", r["optimized_code"])
        self.assertNotIn("reserve", r["optimized_code"])


@unittest.skipUnless(HAVE_CXX, "brak kompilatora C++")
class TestRewritesRun(unittest.TestCase):
    """Przepisany kod MUSI się kompilować i dawać ten sam wynik (losowe małe wejścia)."""

    def test_rewrites_same_output(self):
        from tests import bench_cx_opt as B
        rows = B.eval_optimizer(op, compile_check=True, tests=8)
        bad = []
        for r in rows:
            if r["rewrite_expected"] and not (r.get("rewritten") and r.get("compiles") and r.get("same_output")):
                bad.append(f"{r['name']}: przepisano={r.get('rewritten')} kompiluje={r.get('compiles')} "
                           f"wynik={r.get('same_output')}")
            if "all_safe_compiles" in r and not (r["all_safe_compiles"] and r["all_safe_same"] is not False):
                bad.append(f"{r['name']}: wszystkie bezpieczne zmiany naraz psują program")
        self.assertEqual(bad, [], "\n".join(bad))


@unittest.skipUnless(HAVE_CXX, "brak kompilatora C++")
class TestCompileAndBenchmark(unittest.TestCase):
    def test_optimized_compiles_and_same_output(self):
        from oiforge.core import runner
        r = op.optimize(BASIC)
        inp = "5\n3 1 4 1 5\n"
        a = runner.run_source(BASIC, inp)
        b = runner.run_source(r["optimized_code"], inp)
        self.assertTrue(a["ok"] and b["ok"], b.get("compile"))
        self.assertTrue(runner.outputs_equal(a["run"]["stdout"], b["run"]["stdout"]))

    def test_benchmark_job(self):
        from oiforge.core import jobs
        r = op.optimize(BASIC)
        _, res = jobs.run_sync(op._bench_work, {"original": BASIC, "optimized": r["optimized_code"],
                                                 "input": "100000\n" + " ".join(["7"] * 100000) + "\n", "repeats": 2})
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["same_output"])
        self.assertIsNotNone(res["speedup"])
        self.assertGreater(res["speedup"], 1.0)


@unittest.skipUnless(PROGRAMS.exists() and HAVE_CXX, "brak programów ucznia albo kompilatora")
class TestStudentPrograms(unittest.TestCase):
    def test_sample_compiles(self):
        from oiforge.core import runner
        progs = json.loads(PROGRAMS.read_text(encoding="utf-8"))["programs"]
        base = PROGRAMS.parent
        checked = 0
        for p in progs[::12]:
            code = (base / p["path"]).read_text(encoding="utf-8", errors="replace")
            res = op.optimize(code)
            if not res["changed"]:
                continue
            cr = runner.compile_cpp(res["optimized_code"], mode="syntax")
            self.assertTrue(cr.ok, f"{p['path']}: {cr.stderr[-500:]}")
            checked += 1
        self.assertGreater(checked, 5)


if __name__ == "__main__":
    unittest.main()

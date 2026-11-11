"""Testy Testerki uniwersalnej (oiforge.tools.tester)."""
import os
import time
import unittest

from oiforge import config
from oiforge.core import jobs, runner
from oiforge.server_api import ApiError
from oiforge.tools import tester as T

os.environ.setdefault("OIFORGE_NO_JOURNAL", "1")      # testy nie zaśmiecają dziennika ucznia
HAVE_CXX = bool(config.CXX)

SUM_OK = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;long long s=0;for(int i=0;i<n;i++){long long x;cin>>x;s+=x;}cout<<s<<"\n";}
"""
SUM_BRUTE = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;vector<long long>a(n);for(auto&x:a)cin>>x;long long s=0;for(auto x:a)s+=x;cout<<s<<endl;}
"""
# celowo błędne: gubi siódemki, a dla n >= 5 i a[1] > a[4] dodaje 1
SUM_BAD = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;vector<long long>a(n);for(auto&x:a)cin>>x;long long s=0;
for(int i=0;i<n;i++) if(a[i]!=7) s+=a[i];
cout<<s<<"\n";}
"""
SUM_BAD2 = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;vector<long long>a(n);for(auto&x:a)cin>>x;long long s=0;for(auto x:a)s+=x;
if(n>=5 && a[1]>a[4]) s++; cout<<s<<"\n";}
"""
GEN_BIG = r"""#include <bits/stdc++.h>
using namespace std;
int main(int argc,char**argv){mt19937 r(atoi(argv[1]));int T=atoi(argv[2]);
for(int t=0;t<T;t++){ if(t) cout<<"\n"; int n=6+r()%5; cout<<n<<"\n";
 for(int i=0;i<n;i++) cout<<1+r()%1000<<" \n"[i==n-1]; }}
"""
GEN_PY = """import random, sys
random.seed(int(sys.argv[1]))
T = int(sys.argv[2])
print("\\n\\n".join(f"{n}\\n" + " ".join(str(random.randint(1, 9)) for _ in range(n))
                     for n in (random.randint(1, 5) for _ in range(T))))
"""
GEN_SEED = r"""#include <bits/stdc++.h>
using namespace std;
int main(int argc,char**argv){mt19937 r(atoi(argv[1])); int n=1+r()%5; cout<<n<<"\n";
for(int i=0;i<n;i++) cout<<1+r()%9<<" "; cout<<"\n";}
"""


def run(body):
    cfg = T._config_from(body)
    if cfg["checker"]:
        cfg["checker_source"] = T._checker_source(cfg["checker"])
    return jobs.run_sync(T._work, cfg)


class SplitTest(unittest.TestCase):
    def test_blank_lines_variants(self):
        text = "1\n5\n\n2\n1 2\r\n\r\n\n   \n3\n1 2 3\n\t\n"
        tests, w = T.split_tests(text)
        self.assertEqual(tests, ["1\n5\n", "2\n1 2\n", "3\n1 2 3\n"])
        self.assertEqual(w, [])

    def test_custom_separator(self):
        text = "2\n\n1 2\n---\n1\n\n5\n---\n"
        tests, _ = T.split_tests(text, "---")
        self.assertEqual(tests, ["2\n\n1 2\n", "1\n\n5\n"])

    def test_inner_blank_line_warning(self):
        text = "\n\n".join(f"{k}\n\n" + " ".join(["1"] * k) for k in [3, 4, 3, 5, 3, 4, 5, 3, 4])
        tests, w = T.split_tests(text)
        self.assertTrue(w, "powinno ostrzec o pustej linii w środku testu")
        self.assertIn("separator", " ".join(w))

    def test_compare_modes(self):
        self.assertIsNone(T.compare_outputs("1 2\n", "1\n2"))
        self.assertIsNotNone(T.compare_outputs("1 2\n", "1\n2", "exact"))
        self.assertIsNone(T.compare_outputs("0.5000001", "0.5", "float", 1e-6))
        self.assertIsNotNone(T.compare_outputs("0.51", "0.5", "float", 1e-6))

    def test_templates(self):
        r = T.handle_templates({})
        ids = {c["id"] for c in r["checkers"]}
        self.assertTrue({"float", "permutation", "set", "yesno"} <= ids)
        self.assertTrue(all(c["name"] and c["description"] for c in r["checkers"]))
        self.assertTrue(any(g["lang"] == "python" for g in r["generators"]))

    def test_config_validation(self):
        with self.assertRaises(ApiError):
            T._config_from({"solution": "", "brute": "x", "generator": "y"})
        with self.assertRaises(ApiError):
            T._config_from({"solution": "x", "brute": "", "generator": "y"})
        with self.assertRaises(ApiError):
            T._config_from({"solution": "x", "brute": "y", "generator": "z", "compare": "magic"})
        with self.assertRaises(ApiError):
            T._checker_source("nie_ma_takiego")


@unittest.skipUnless(HAVE_CXX, "brak kompilatora C++")
class CheckerTemplatesTest(unittest.TestCase):
    def check(self, cid, out, ans, ok):
        prog = runner.prepare(T._checker_source(cid), lang="cpp")
        self.assertTrue(prog.ok, prog.compile.stderr if prog.compile else prog.error)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            c = T.Checker(prog, d)
            res, msg, err = c.check("", out, ans)
            self.assertEqual(res, ok, f"{cid}: {msg}")
            self.assertIsNone(err)

    def test_float(self):
        self.check("float", "0.3333333\n", "0.33333334\n", True)
        self.check("float", "0.33\n", "0.34\n", False)

    def test_permutation(self):
        self.check("permutation", "3 1 2", "1 2 3", True)
        self.check("permutation", "3 1 1", "1 2 3", False)

    def test_set(self):
        self.check("set", "3 1 1 2", "1 2 3", True)
        self.check("set", "3 1", "1 2 3", False)

    def test_yesno(self):
        self.check("yesno", "tak\nNie", "TAK\nNIE", True)
        self.check("yesno", "TAK", "NIE", False)

    def test_lines_any_order(self):
        self.check("lines_any_order", "1 2\n3 4\n", "3  4\n1 2\n", True)


@unittest.skipUnless(HAVE_CXX, "brak kompilatora C++")
class TesterJobTest(unittest.TestCase):
    def test_correct_solution_1000_ok_and_fast(self):
        t0 = time.perf_counter()
        job, res = run({"solution": SUM_OK, "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP, "seed": 1,
                        "tests_per_run": 1000})
        dt = time.perf_counter() - t0
        self.assertEqual(res["status"], "OK", res.get("message"))
        self.assertEqual(res["stats"]["counts"]["OK"], 1000)
        self.assertEqual(res["stats"]["done"], 1000)
        types = [e["type"] for e in job.events]
        self.assertEqual(types.count("test"), T.MAX_EVENT_TESTS)
        self.assertIn("stats", types)
        self.assertGreater(res["stats"]["sol_max"], 0)
        # wydajność: < ~15 s na niezaładowanej maszynie; przy dużym obciążeniu (inne procesy) skalujemy próg
        try:
            load = os.getloadavg()[0] / (os.cpu_count() or 1)
        except (OSError, AttributeError):
            load = 1.0
        limit = 15.0 * max(1.0, load)
        print(f"\n[tester] 1000 testów sumy (C++): {dt:.1f} s, obciążenie {load:.1f}× rdzeni → limit {limit:.0f} s")
        self.assertLess(dt, limit)

    def test_buggy_solution_finds_counterexample(self):
        job, res = run({"solution": SUM_BAD, "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP, "seed": 7})
        self.assertEqual(res["status"], "WA")
        first = res["first_failure"]
        self.assertIn("7", first["input"].split())
        self.assertIsNotNone(first["diff"])
        self.assertFalse(first["diff"]["equal"])
        mini = res["minimized"]
        self.assertIsNotNone(mini)
        self.assertLessEqual(mini["size"], first["size"])
        self.assertEqual(mini["input"], "1\n7\n")          # najmniejszy możliwy błędny test z generatora
        self.assertIn("failure", [e["type"] for e in job.events])

    def test_minimize_and_shrink(self):
        job, res = run({"solution": SUM_BAD2, "brute": SUM_BRUTE, "generator": GEN_BIG, "seed": 5,
                        "tests_per_run": 300})
        self.assertEqual(res["status"], "WA")
        self.assertLessEqual(res["minimized"]["size"], res["first_failure"]["size"])
        sh = res["shrunk"]
        self.assertIsNotNone(sh, res.get("shrink_info"))
        self.assertLess(sh["size"], res["minimized"]["size"])
        n = int(sh["input"].split()[0])
        self.assertEqual(n, 5)                             # błąd wymaga n ≥ 5
        self.assertEqual(sh["verdict"], "WA")

    def test_collect_all(self):
        job, res = run({"solution": SUM_BAD, "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP, "seed": 3,
                        "stop_on_first": False, "tests_per_run": 300, "shrink": False})
        self.assertEqual(res["stats"]["done"], 300)
        self.assertGreater(res["failures"], 1)
        self.assertEqual(res["stats"]["counts"]["WA"], res["failures"])

    def test_python_generator_multiple_runs(self):
        job, res = run({"solution": SUM_OK, "brute": SUM_BRUTE, "generator": GEN_PY, "langs": {"generator": "python"},
                        "runs": 3, "tests_per_run": 50, "seed": 10})
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["stats"]["done"], 150)
        gens = [e for e in job.events if e["type"] == "generator"]
        self.assertEqual([g["seed"] for g in gens], [10, 11, 12])

    def test_seed_mode(self):
        job, res = run({"solution": SUM_BAD, "brute": SUM_BRUTE, "generator": GEN_SEED, "mode": "seed",
                        "tests": 300, "seed": 1, "shrink": False})
        self.assertEqual(res["status"], "WA")
        self.assertIn("7", res["first_failure"]["input"].split())

    def test_checker_template(self):
        perm_sol = r"""#include <bits/stdc++.h>
using namespace std; int main(){int n;cin>>n;vector<int>a(n);for(auto&x:a)cin>>x;sort(a.rbegin(),a.rend());
for(int x:a)cout<<x<<" ";cout<<"\n";}"""
        perm_brute = r"""#include <bits/stdc++.h>
using namespace std; int main(){int n;cin>>n;vector<int>a(n);for(auto&x:a)cin>>x;sort(a.begin(),a.end());
for(int x:a)cout<<x<<" ";cout<<"\n";}"""
        job, res = run({"solution": perm_sol, "brute": perm_brute, "generator": T.GEN_TEMPLATE_CPP,
                        "tests_per_run": 60, "checker": "permutation", "seed": 2})
        self.assertEqual(res["status"], "OK", res.get("message"))
        job, res = run({"solution": perm_sol, "brute": perm_brute, "generator": T.GEN_TEMPLATE_CPP,
                        "tests_per_run": 60, "seed": 2, "shrink": False})
        self.assertEqual(res["status"], "WA")

    def test_runtime_error_and_tle(self):
        re_sol = r"""#include <bits/stdc++.h>
using namespace std; int main(){int n;cin>>n; if(n==3){ vector<int> v; return v.at(5); } long long s=0;
for(int i=0;i<n;i++){long long x;cin>>x;s+=x;} cout<<s<<"\n";}"""
        job, res = run({"solution": re_sol, "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP, "seed": 1,
                        "tests_per_run": 100, "shrink": False})
        self.assertEqual(res["status"], "RE")
        self.assertEqual(res["minimized"]["input"].split()[0], "3")
        tle_sol = r"""#include <bits/stdc++.h>
using namespace std; int main(){int n;cin>>n; if(n==8){ volatile long long z=0; while(true) z++; }
long long s=0; for(int i=0;i<n;i++){long long x;cin>>x;s+=x;} cout<<s<<"\n";}"""
        job, res = run({"solution": tle_sol, "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP, "seed": 1,
                        "tests_per_run": 100, "timeout": 0.5, "shrink": False})
        self.assertEqual(res["status"], "TLE")

    def test_slow_brute_hint(self):
        slow = r"""#include <bits/stdc++.h>
using namespace std; int main(){ volatile long long z=0; for(long long i=0;i<600000000LL;i++) z+=i;
int n;cin>>n;long long s=0;for(int i=0;i<n;i++){long long x;cin>>x;s+=x;}cout<<s<<"\n";}"""
        job, res = run({"solution": SUM_OK, "brute": slow, "generator": T.GEN_TEMPLATE_CPP, "seed": 1,
                        "tests_per_run": 10, "timeout": 0.1, "brute_timeout": 20})
        self.assertIn("brute_slow", res["hints"])
        self.assertTrue(any(e["type"] == "hint" and e.get("code") == "brute_slow" for e in job.events))

    def test_compile_error(self):
        job, res = run({"solution": "int main( { return 0; }", "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP})
        self.assertEqual(res["status"], "CE")
        ev = [e for e in job.events if e["type"] == "compile_error"]
        self.assertEqual(ev[0]["which"], "solution")
        self.assertIn("error", ev[0]["stderr"])

    def test_generator_error(self):
        job, res = run({"solution": SUM_OK, "brute": SUM_BRUTE,
                        "generator": "#include <cstdlib>\nint main(){ abort(); }"})
        self.assertEqual(res["status"], "ERROR")
        self.assertTrue(any(e["type"] == "generator_error" for e in job.events))

    def test_cancel(self):
        cfg = T._config_from({"solution": SUM_OK, "brute": SUM_BRUTE, "generator": T.GEN_TEMPLATE_CPP,
                              "tests_per_run": 5000})
        job = jobs.Job("t")
        job.cancelled = True
        res = T._work(job, cfg)
        self.assertEqual(res["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()

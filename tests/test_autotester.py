"""Testy Auto-testerki (oiforge.tools.autotester)."""
import json
import os
import unittest

from oiforge import config
from oiforge.core import jobs
from oiforge.server_api import ApiError
from oiforge.tools import autotester as A

os.environ.setdefault("OIFORGE_NO_JOURNAL", "1")      # testy nie zaśmiecają dziennika ucznia
HAVE_CXX = bool(config.CXX)
HAVE_SAN = bool(config.capabilities().get("sanitizers"))

STATEMENT = ("Suma\n\nWejście\n\nW pierwszym wierszu wejścia znajduje się liczba całkowita n (1 ≤ n ≤ 100 000). "
             "W drugim wierszu n liczb całkowitych a_i (1 ≤ a_i ≤ 10\n9\n).\n\nWyjście\n\nJedna liczba — suma.\n\n"
             "Przykład\n\nWejście dla testu sum0:\n\n5\n\n1 2 3 4 5\n\nWyjście dla testu sum0:\n\n15\n\n"
             "Wejście dla testu sum1:\n\n3\n\n7 7 1\n\nWyjście dla testu sum1:\n\n15\n")
GOOD = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;long long s=0;for(int i=0;i<n;i++){long long x;cin>>x;s+=x;}cout<<s<<"\n";}
"""
OVERFLOW = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;int s=0;for(int i=0;i<n;i++){int x;cin>>x;s+=x;}cout<<s<<"\n";}
"""
OOB = r"""#include <bits/stdc++.h>
using namespace std;
int a[8];
int main(){int n;cin>>n;long long s=0;for(int i=0;i<n;i++){cin>>a[i];s+=a[i];}cout<<s<<"\n";}
"""
WRONG_SAMPLE = r"""#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;long long s=1;for(int i=0;i<n;i++){long long x;cin>>x;s+=x;}cout<<s<<"\n";}
"""


def run_job(spec, solution, brute="", samples=None, count=80, **kw):
    cfg = {"solution": solution, "brute": brute, "spec": spec, "strategies": A.suggest_strategies(spec),
           "count": count, "timeout": 3.0, "brute_timeout": 15.0, "samples": A._norm_samples(samples or []),
           "langs": {}, "sanitize": True, "max_size": "medium", "seed": 11, "stop_on_first": True,
           "compare": "tokens", "float_eps": None, "workers": 8, "shrink": True}
    cfg.update(kw)
    return jobs.run_sync(A._work, cfg)


class InferEndpointTest(unittest.TestCase):
    def test_infer_from_samples_and_statement(self):
        r = A.handle_infer({"samples": [], "statement": STATEMENT})
        self.assertTrue(r["extracted"])
        self.assertEqual(len(r["samples"]), 2)
        self.assertEqual(r["samples"][0]["output"], "15")
        spec = r["spec"]
        self.assertEqual([it["kind"] for it in spec["items"]], ["line", "array"])
        self.assertEqual(spec["vars"]["n"]["max"], 100000)
        self.assertEqual(spec["items"][1]["elem"]["max"], 10 ** 9)
        self.assertTrue(r["description"])
        self.assertTrue(all(e["valid"] for e in r["examples"]))
        self.assertIn("sorted", r["suggested_strategies"])

    def test_infer_errors(self):
        with self.assertRaises(ApiError):
            A.handle_infer({"samples": []})

    def test_extract(self):
        r = A.handle_extract({"statement": STATEMENT})
        self.assertEqual(r["count"], 2)

    def test_generate_and_validate(self):
        spec = A.handle_infer({"samples": ["5\n1 2 3 4 5", "3\n7 7 1"]})["spec"]
        g = A.handle_generate({"spec": json.dumps(spec), "strategy": "max", "size": "small", "seed": 1})
        self.assertTrue(g["valid"], g["error"])
        v = A.handle_validate({"spec": spec, "samples": ["2\n1 2", "2\n1 2 3"]})
        self.assertEqual([x["valid"] for x in v["results"]], [True, False])
        with self.assertRaises(ApiError):
            A.handle_generate({"spec": "{nie json", "strategy": "random"})
        with self.assertRaises(ApiError):
            A.handle_generate({"spec": {"foo": 1}})

    def test_strategies_list(self):
        r = A.handle_strategies({})
        self.assertGreaterEqual(len(r["strategies"]), 12)
        self.assertEqual([s["id"] for s in r["sizes"]][:2], ["tiny", "small"])

    def test_explain_runtime(self):
        self.assertIn("tablicy", A.explain_runtime("runtime error: index 8 out of bounds for type 'int[8]'"))
        self.assertIn("long long", A.explain_runtime("runtime error: signed integer overflow: 2147483647 + 1"))
        self.assertIn("rekurencja", A.explain_runtime("ERROR: AddressSanitizer: stack-overflow on address"))
        self.assertEqual(A.clean_paths("/x/user_data/cache/src/" + "a" * 24 + ".cpp:3:1"), "main.cpp:3:1")


@unittest.skipUnless(HAVE_CXX, "brak kompilatora C++")
class AutoTesterJobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inf = A.handle_infer({"samples": [], "statement": STATEMENT})
        cls.spec = cls.inf["spec"]

    def test_good_solution_passes(self):
        job, res = run_job(self.spec, GOOD, samples=self.inf["samples"], count=60)
        self.assertEqual(res["status"], "OK", res["message"])
        self.assertEqual(res["counts"]["OK"], 60)
        self.assertTrue(all(s["verdict"] == "OK" for s in res["samples"]))

    def test_overflow_found_with_brute(self):
        job, res = run_job(self.spec, OVERFLOW, brute=GOOD, count=200)
        self.assertEqual(res["status"], "WA")
        self.assertIsNotNone(res["failure"])
        # kontrprzykład po shrinkingu nie jest większy i nadal pokazuje błąd
        sh = res["shrunk"] or res["failure"]
        self.assertEqual(sh["verdict"], "WA")
        self.assertLessEqual(sh["size"], res["failure"]["size"])

    @unittest.skipUnless(HAVE_SAN, "brak sanitizerów na tej platformie")
    def test_out_of_bounds_crash_test(self):
        job, res = run_job(self.spec, OOB, count=120)
        self.assertIn(res["status"], ("UB", "RE"))
        sh = res["shrunk"]
        self.assertIsNotNone(sh, res.get("shrink_info"))
        n = int(sh["input"].split()[0])
        self.assertEqual(n, 9)                  # a[8] — pierwszy błędny rozmiar to n = 9
        self.assertTrue(sh.get("explanation"))
        self.assertTrue(any(e["type"] == "compiled" and e.get("mode") == "sanitize" for e in job.events))

    def test_sample_mismatch(self):
        job, res = run_job(self.spec, WRONG_SAMPLE, samples=self.inf["samples"], count=20, sanitize=False)
        self.assertEqual(res["status"], "SAMPLE_WA")
        self.assertEqual(res["samples"][0]["verdict"], "WA")
        self.assertIn("diff", res["samples"][0])

    def test_start_validation(self):
        with self.assertRaises(ApiError):
            A.handle_start({"solution": "", "spec": self.spec})
        with self.assertRaises(ApiError):
            A.handle_start({"solution": GOOD, "spec": {"items": [{"kind": "array", "len": "zz"}]}})



class TestOcenSamples(unittest.TestCase):
    def test_infer_uses_ocen_and_bigint(self):
        from tests.test_statement import BIG_DIGITS
        r = A.handle_infer({"statement": BIG_DIGITS})
        self.assertTrue(r["extracted"])
        self.assertEqual([s.get("source") for s in r["samples"]], [None, "ocen", "ocen"])
        self.assertEqual(r["spec"]["items"][0]["fields"][0]["type"], "bigint")
        self.assertTrue(all(r["sample_parse"]))
        for ex in r["examples"]:
            self.assertTrue(ex["valid"], ex)


if __name__ == "__main__":
    unittest.main()

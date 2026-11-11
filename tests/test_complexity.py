"""Testy analizy złożoności (oiforge.tools.complexity): statyka, ograniczenia, pamięć, dopasowanie modeli, pomiar."""
import json
import math
import random
import shutil
import unittest
from pathlib import Path

from oiforge.tools import complexity as cx

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ROOT / "user_data" / "programs" / "index.json"
H = "#include <bits/stdc++.h>\nusing namespace std;\n"

CONS = {"n": 200000, "m": 200000, "q": 200000, "k": 100}

# (nazwa, kod, oczekiwane wyrażenie O(...))
CASES = [
    ("liniowa", H + "int main(){int n;cin>>n; long long s=0; for(int i=0;i<n;i++){int x;cin>>x;s+=x;} cout<<s;}", "n"),
    ("kwadratowa", H + "int a[200005]; int main(){int n;cin>>n; long long s=0; for(int i=0;i<n;i++) for(int j=0;j<n;j++) s+=a[i]*a[j]; cout<<s;}", "n²"),
    ("zależna j>i", H + "int a[200005]; int main(){int n;cin>>n; long long s=0; for(int i=0;i<n;i++) for(int j=i+1;j<n;j++) s+=a[i]^a[j];}", "n²"),
    ("zależna j<i", H + "int main(){int n;cin>>n; long long s=0; for(int i=0;i<n;i++) for(int j=0;j<i;j++) s++; cout<<s;}", "n²"),
    ("sześcienna (Floyd)", H + "long long d[505][505]; int main(){int n;cin>>n; for(int k=0;k<n;k++) for(int i=0;i<n;i++) for(int j=0;j<n;j++) d[i][j]=min(d[i][j],d[i][k]+d[k][j]);}", "n³"),
    ("harmoniczna j+=i", H + "int d[200005]; int main(){int n;cin>>n; for(int i=1;i<=n;i++) for(int j=i;j<=n;j+=i) d[j]++;}", "n log n"),
    ("sito", H + "bool c[1000005]; int main(){int n;cin>>n; for(int i=2;i*i<=n;i++) if(!c[i]) for(int j=i*i;j<=n;j+=i) c[j]=1;}", "n log log n"),
    ("pierwiastek i*i<=n", H + "int main(){long long n;cin>>n; int cnt=0; for(long long i=1;i*i<=n;i++) if(n%i==0) cnt++; cout<<cnt;}", "√n"),
    ("logarytm i*=2", H + "int main(){int n;cin>>n; int c=0; for(int i=1;i<n;i*=2) c++; cout<<c;}", "log n"),
    ("logarytm x/=2", H + "int main(){long long x;cin>>x; int c=0; while(x>0){x/=2;c++;} cout<<c;}", "log x"),
    ("binsearch w pętli zapytań", H + "int main(){int n,q;cin>>n>>q; vector<int>a(n); for(auto&x:a)cin>>x; sort(a.begin(),a.end()); while(q--){int x;cin>>x; int l=0,r=n-1; while(l<r){int m=(l+r)/2; if(a[m]<x) l=m+1; else r=m;} cout<<l<<'\\n';}}", "n log n + q log n"),
    ("dwa wskaźniki", H + "long long a[200005]; int main(){int n; long long K;cin>>n>>K; for(int i=0;i<n;i++)cin>>a[i]; int j=0; long long s=0; int best=0; for(int i=0;i<n;i++){ while(j<n && s+a[j]<=K){s+=a[j];j++;} best=max(best,j-i); s-=a[i]; } cout<<best;}", "n"),
    ("stos monotoniczny", H + "int a[200005]; int main(){int n;cin>>n; for(int i=0;i<n;i++)cin>>a[i]; stack<int> st; for(int i=0;i<n;i++){ while(!st.empty() && a[st.top()]<=a[i]) st.pop(); st.push(i);} }", "n"),
    ("memo DP", H + "long long dp[5005][105]; int n,k; long long f(int i,int j){ if(i==n) return 0; if(dp[i][j]!=-1) return dp[i][j]; long long r=0; for(int t=0;t<k;t++) r=max(r,f(i+1,t)+t); return dp[i][j]=r;} int main(){cin>>n>>k; memset(dp,-1,sizeof dp); cout<<f(0,0);}", "n·k²"),
    ("merge sort", H + "vector<int> a; void ms(int l,int r){ if(l>=r) return; int m=(l+r)/2; ms(l,m); ms(m+1,r); vector<int> t; int i=l,j=m+1; while(i<=m&&j<=r){ if(a[i]<=a[j]) t.push_back(a[i++]); else t.push_back(a[j++]); } while(i<=m) t.push_back(a[i++]); while(j<=r) t.push_back(a[j++]); for(int k=l;k<=r;k++) a[k]=t[k-l]; } int main(){int n;cin>>n; a.resize(n); for(auto&x:a)cin>>x; ms(0,n-1);}", "n log n"),
    ("set w pętli", H + "int main(){int n;cin>>n; set<int> s; for(int i=0;i<n;i++){int x;cin>>x; s.insert(x);} cout<<s.size();}", "n log n"),
    ("sort", H + "int main(){int n;cin>>n; vector<long long> v(n); for(auto&x:v)cin>>x; sort(v.begin(),v.end()); cout<<v[0];}", "n log n"),
    ("BFS", H + "vector<int> g[200005]; int d[200005]; int main(){int n,m;cin>>n>>m; for(int i=0;i<m;i++){int a,b;cin>>a>>b;g[a].push_back(b);g[b].push_back(a);} fill(d,d+n+1,-1); queue<int>q; q.push(1); d[1]=0; while(!q.empty()){int v=q.front();q.pop(); for(int u:g[v]) if(d[u]==-1){d[u]=d[v]+1;q.push(u);}} }", "n + m"),
    ("DFS rekurencyjny", H + "vector<int> g[200005]; bool vis[200005]; void dfs(int v){ vis[v]=1; for(int u:g[v]) if(!vis[u]) dfs(u);} int main(){int n,m;cin>>n>>m; for(int i=0;i<m;i++){int a,b;cin>>a>>b;g[a].push_back(b);g[b].push_back(a);} int c=0; for(int i=1;i<=n;i++) if(!vis[i]){c++;dfs(i);} cout<<c;}", "n + m"),
    ("DSU", H + "int p[200005]; int f(int x){ return p[x]==x?x:p[x]=f(p[x]); } int main(){int n,m;cin>>n>>m; for(int i=1;i<=n;i++)p[i]=i; for(int i=0;i<m;i++){int a,b;cin>>a>>b; p[f(a)]=f(b);} }", "m log n + n"),
    ("drzewo przedziałowe (rekurencja)", H + "int t[800005]; int query(int v,int l,int r,int ql,int qr){ if(qr<l||r<ql) return 0; if(ql<=l&&r<=qr) return t[v]; int m=(l+r)/2; return query(2*v,l,m,ql,qr)+query(2*v+1,m+1,r,ql,qr);} int main(){int n,q;cin>>n>>q; while(q--){int l,r;cin>>l>>r; cout<<query(1,0,n-1,l,r)<<'\\n';}}", "q log n"),
    ("Fibonacci bez memo", H + "long long fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2);} int main(){int n;cin>>n;cout<<fib(n);}", "1.62^n"),
    ("zapytania × n", H + "int a[200005]; int main(){int n,q;cin>>n>>q; for(int i=0;i<n;i++)cin>>a[i]; while(q--){int x;cin>>x; int c=0; for(int i=0;i<n;i++) if(a[i]==x) c++; cout<<c<<'\\n';}}", "n·q"),
    ("szybkie potęgowanie w pętli", H + "long long pw(long long a,long long b){ long long r=1; while(b>0){ if(b&1) r=r*a%1000000007; a=a*a%1000000007; b>>=1;} return r;} int main(){int n;cin>>n; for(int i=0;i<n;i++){long long a,b;cin>>a>>b; cout<<pw(a,b)<<'\\n';}}", "n log b"),
    ("plecak", H + "long long dp[100005]; int main(){int n,W;cin>>n>>W; for(int i=0;i<n;i++){int w,v;cin>>w>>v; for(int j=W;j>=w;j--) dp[j]=max(dp[j],dp[j-w]+v);} cout<<dp[W];}", "n·W"),
    ("BFS ucznia (vector jako kolejka)", H + "vector<int> kolejka; vector<int> odw; vector<vector<int>> s; int main(){int n,m;cin>>n>>m; for(int i=0;i<n;i++){vector<int> d; s.push_back(d); odw.push_back(1e9);} for(int i=0;i<m;i++){int x,y;cin>>x>>y; s[x-1].push_back(y-1); s[y-1].push_back(x-1);} kolejka.push_back(0); odw[0]=0; int akt=0; while(kolejka.size()>akt){ for(int i=0;i<s[kolejka[akt]].size();i++){ if(odw[s[kolejka[akt]][i]]>odw[kolejka[akt]]+1){ kolejka.push_back(s[kolejka[akt]][i]); odw[s[kolejka[akt]][i]]=odw[kolejka[akt]]+1; } } akt++; } }", "n + m"),
    ("bąbelkowe (flaga)", H + "int b[1005]; int main(){int a;cin>>a; for(int i=0;i<a;i++)cin>>b[i]; bool c=true; while(c){ c=false; for(int i=0;i<a-1;i++){ if(b[i]>b[i+1]){ swap(b[i],b[i+1]); c=true; } } } }", "a²"),
]


class TestStaticComplexity(unittest.TestCase):
    def test_cases(self):
        self.assertGreaterEqual(len(CASES), 20)
        failures = []
        for name, code, expected in CASES:
            r = cx.analyze(code, CONS)
            if r["expr"] != expected:
                failures.append(f"{name}: oczekiwano O({expected}), jest {r['complexity']}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_dependent_loop_pattern(self):
        r = cx.analyze(CASES[2][1], CONS)
        inner = r["loops"][0]["children"][0]["children"][0]
        self.assertEqual(inner["pattern"], "dependent")

    def test_two_pointers_amortized_flag(self):
        r = cx.analyze(CASES[11][1], CONS)
        flat = []

        def walk(ns):
            for nd in ns:
                flat.append(nd)
                walk(nd.get("children", []))
        walk(r["loops"])
        self.assertTrue(any(nd.get("amortized") and nd.get("pattern") == "two_pointers" for nd in flat))

    def test_recursion_master_theorem_text(self):
        r = cx.analyze(CASES[14][1], CONS)
        ms = next(n for n in r["loops"] if n.get("name") == "ms")
        self.assertEqual(ms["recursion"]["type"], "dc")
        self.assertIn("przypadek 2", ms["recursion"]["text"])

    def test_verdicts(self):
        quad = CASES[1][1]
        self.assertEqual(cx.analyze(quad, {"n": 200000}, time_limit=1)["time"]["verdict"], "tle")
        self.assertEqual(cx.analyze(quad, {"n": 1000}, time_limit=1)["time"]["verdict"], "ok")
        self.assertEqual(cx.analyze(quad, {"n": 12000}, time_limit=1)["time"]["verdict"], "warn")
        self.assertEqual(cx.analyze(quad, {}, time_limit=1)["time"]["verdict"], "unknown")

    def test_time_limit_scaling(self):
        quad = CASES[1][1]
        r1 = cx.analyze(quad, {"n": 15000}, time_limit=0.5)
        r2 = cx.analyze(quad, {"n": 15000}, time_limit=5)
        self.assertNotEqual(r1["time"]["verdict"], "ok")
        self.assertEqual(r2["time"]["verdict"], "ok")

    def test_hotspots(self):
        code = H + "int a[200005];\nint main(){\n  int n,q; cin>>n>>q;\n  for(int i=0;i<n;i++) cin>>a[i];\n  while(q--){\n    int x; cin>>x; int c=0;\n    for(int i=0;i<n;i++)\n      if(a[i]==x) c++;\n    cout<<c<<'\\n';\n  }\n}"
        r = cx.analyze(code, CONS)
        self.assertTrue(r["hotspots"])
        top = r["hotspots"][0]
        self.assertIn("if(a[i]==x)", top["code"].replace(" ", ""))
        self.assertGreater(top["share"], 0.3)

    def test_stl_costs_and_warnings(self):
        code = H + "int main(){int n,q;cin>>n>>q; set<int> s; for(int i=0;i<n;i++){int x;cin>>x;s.insert(x);} while(q--){int x;cin>>x; auto it=lower_bound(s.begin(),s.end(),x); cout<<*it<<'\\n';}}"
        r = cx.analyze(code, CONS)
        self.assertEqual(r["expr"], "n·q + n log n")
        self.assertTrue(any("lower_bound" in w["message"] for w in r["warnings"]))
        code2 = H + "int main(){int n;cin>>n; string s; for(int i=0;i<n;i++){ s = s + 'a'; } cout<<s.size();}"
        r2 = cx.analyze(code2, CONS)
        self.assertEqual(r2["expr"], "n²")
        self.assertTrue(any("+=" in w["message"] for w in r2["warnings"]))

    def test_memory(self):
        code = H + "int a[1000000]; long long b[1000][1000]; int main(){ vector<int> v(5000000); }"
        r = cx.analyze(code, {"n": 10})
        names = {it["name"]: it for it in r["memory"]["items"]}
        self.assertAlmostEqual(names["a"]["mb"], 3.81, delta=0.05)
        self.assertAlmostEqual(names["b"]["mb"], 7.63, delta=0.05)
        self.assertAlmostEqual(names["v"]["mb"], 19.07, delta=0.1)
        r2 = cx.analyze(H + "int a[100000000]; int main(){}", {"n": 1}, memory_limit=128)
        self.assertEqual(r2["memory"]["verdict"], "mle")

    def test_fragment_without_main(self):
        r = cx.analyze("for (int i = 0; i < n; i++)\n  for (int j = 0; j < m; j++)\n    s += t[i][j];", {"n": 1000, "m": 1000})
        self.assertEqual(r["expr"], "n·m")
        self.assertEqual(r["loops"][0]["children"][0]["line"], 1)

    def test_robust_on_garbage(self):
        for code in ["", "}}}", "int main(", "while(", "for(;;){}", "int f(int x){return f(x);}", "x" * 3000]:
            r = cx.analyze(code, CONS)
            self.assertIn("complexity", r)

    def test_limits_table(self):
        rows = cx.limits_table(1.0, "n log n")
        byc = {r["class"]: r for r in rows}
        self.assertTrue(byc["O(n log n)"]["yours"])
        self.assertGreater(byc["O(n)"]["max_n"], 1e8)
        self.assertTrue(4000 < byc["O(n²)"]["max_n"] < 30000)
        self.assertTrue(20 <= byc["O(2ⁿ)"]["max_n"] <= 30)


class TestBenchSuite(unittest.TestCase):
    """Zestaw pomiarowy z tests/bench_cx_opt.py (typowe wzorce OI) + zestaw kontrolny."""

    def test_main_suite(self):
        from tests import bench_cx_opt as B
        self.assertGreaterEqual(len(B.CX_CASES), 30)
        rows = B.eval_complexity(cx)
        bad = [f"{r['name']}: oczekiwano O({r['expect']}), jest O({r['got']})" for r in rows if not r["ok"]]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_control_suite(self):
        from tests import bench_cx_opt as B
        rows = B.eval_complexity(cx, B.CX_CONTROL)
        bad = [f"{r['name']}: oczekiwano O({r['expect']}), jest O({r['got']})" for r in rows if not r["ok"]]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_norm_expr(self):
        from tests import bench_cx_opt as B
        self.assertTrue(B.expr_ok("O(q·n log n)", ["n·q log n"]))
        self.assertTrue(B.expr_ok("m log m + n", ["n + m log m"]))
        self.assertTrue(B.expr_ok("n log n + m log n", ["(n + m) log n"]))
        self.assertFalse(B.expr_ok("n²", ["n"]))


class TestQueryValues(unittest.TestCase):
    """Wartości wczytywane w pętli zapytań (l, r, a, b) nie mogą stać się wymiarami złożoności."""

    SLOW = H + """int main(){
    int n, q; cin >> n >> q;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    while (q--) {
        int l, r; cin >> l >> r;
        long long s = 0;
        for (int i = l; i <= r; i++) s += a[i];
        cout << s << endl;
    }
}"""

    def test_range_loop_bounded_by_array(self):
        r = cx.analyze(self.SLOW, {"n": 100000, "q": 100000})
        self.assertEqual(r["expr"], "n·q")
        self.assertNotIn("r", {s["name"] for s in r["symbols"]})
        self.assertEqual(r["time"]["verdict"], "tle")

    def test_derivation_points_to_lines(self):
        r = cx.analyze(self.SLOW, {"n": 100000, "q": 100000})
        self.assertTrue(r["derivation"])
        top = r["derivation"][0]
        self.assertEqual(top["line"], 10)
        self.assertEqual([s["line"] for s in top["loops"]], [7, 10])
        self.assertEqual(top["loops"][1]["pattern"], "query_range")
        self.assertIn("linia 7", r["derivation_md"])
        self.assertIn("przedział", r["derivation_md"])

    def test_dsu_queries(self):
        code = H + """int p[200005], r_[200005];
int find(int x){ return p[x]==x ? x : p[x]=find(p[x]); }
void unite(int a,int b){ a=find(a); b=find(b); if(a==b) return; if(r_[a]<r_[b]) swap(a,b); p[b]=a; if(r_[a]==r_[b]) r_[a]++; }
int main(){ int n,q; cin>>n>>q; for(int i=1;i<=n;i++) p[i]=i;
  while(q--){ int t,a,b; cin>>t>>a>>b; if(t==1) unite(a,b); else cout<<(find(a)==find(b)?"TAK":"NIE")<<"\\n"; } }"""
        r = cx.analyze(code, {"n": 100000, "q": 100000})
        self.assertEqual(r["expr"], "n + q·α(n)")
        self.assertEqual(r["time"]["verdict"], "ok")
        self.assertFalse({"a", "b"} & {s["name"] for s in r["symbols"]})

    def test_verdicts_for_limits(self):
        nlogn = H + "int main(){int n;cin>>n; vector<int> v(n); for(auto&x:v)cin>>x; sort(v.begin(),v.end()); cout<<v[0];}"
        self.assertEqual(cx.analyze(nlogn, {"n": 100000}, time_limit=1)["time"]["verdict"], "ok")
        self.assertEqual(cx.analyze(self.SLOW, {"n": 1000, "q": 1000}, time_limit=1)["time"]["verdict"], "ok")

    def test_value_constraints_not_used_as_sizes(self):
        # m nie ma ograniczenia w treści — nie może dostać wartości z a_i ≤ 10^9
        code = H + "int main(){int n,m;cin>>n>>m; for(int i=0;i<m;i++){int x;cin>>x;} for(int i=0;i<n;i++){int x;cin>>x;}}"
        r = cx.analyze(code, statement="Dane: n (1 ≤ n ≤ 1000), liczby a_i (1 ≤ a_i ≤ 10^9).")
        vals = {s["name"]: s["value"] for s in r["symbols"]}
        self.assertLessEqual(vals.get("m", 0), 1000)

    def test_product_constraint(self):
        code = H + "int main(){int n,k;cin>>n>>k; long long s=0; for(int i=0;i<n;i++) for(int j=0;j<k;j++) s+=i^j; cout<<s;}"
        r1 = cx.analyze(code, {"n": 1e6, "k": 1e6})
        r2 = cx.analyze(code, {"n": 1e6, "k": 1e6, "n·k": 1e7})
        self.assertEqual(r1["time"]["verdict"], "tle")
        self.assertEqual(r2["time"]["verdict"], "ok")

    def test_shadowed_test_loop(self):
        code = H + """int solve(){ int n; cin>>n; long long s=0; for(int i=0;i<n;i++){ long long x; cin>>x; s+=x; } return s%7; }
int main(){ int n; cin>>n; for(int i=0;i<n;i++) cout<<solve()<<"\\n"; }"""
        r = cx.analyze(code, {"n": 200000})
        self.assertIn("t", {s["name"] for s in r["symbols"]})

    def test_exit_branch_runs_once(self):
        code = H + """int main(){ int n; cin>>n; vector<int> a(n); for(auto&x:a) cin>>x;
  for(int i=0;i<n;i++){ if(a[i]<0){ auto it=find(a.begin(),a.end(),-a[i]); cout<<(it-a.begin()); return 0; } } }"""
        self.assertEqual(cx.analyze(code, CONS)["expr"], "n")


class TestConstraints(unittest.TestCase):
    def test_parse_statement_text(self):
        txt = ("W pierwszym wierszu wejścia znajdują się dwie liczby n, q (1 ¬ n, q ¬ 500 000). W kolejnej linii "
               "ciąg n liczb ai (0 ¬ ai ¬ 10\n9\n). Limit czasu: 2 s. Dostępna pamięć: 128MB")
        r = cx.parse_constraints_text(txt)
        self.assertEqual(r["constraints"]["n"], 500000)
        self.assertEqual(r["constraints"]["q"], 500000)
        self.assertEqual(r["constraints"]["max(a)"], 1e9)
        self.assertEqual(r["time_limit"], 2.0)
        self.assertEqual(r["memory_limit"], 128)

    def test_parse_variants(self):
        self.assertEqual(cx.parse_constraints_text("1 ≤ k ≤ n ≤ 10^6")["constraints"], {"k": 1e6, "n": 1e6})
        self.assertEqual(cx.parse_constraints_text("$1 \\le n \\le 2 \\cdot 10^5$")["constraints"]["n"], 2e5)
        self.assertEqual(cx.parse_constraints_text("2 ≤ n ≤ 100 000, 1 ≤ m ≤ 200 000")["constraints"],
                         {"n": 100000, "m": 200000})

    def test_to_number(self):
        for s, v in [("2e5", 2e5), ("200 000", 2e5), ("2·10^5", 2e5), ("10^9", 1e9), (5, 5), ({"max": 7}, 7)]:
            self.assertEqual(cx._to_number(s), v)

    def test_statement_used_by_analyze(self):
        r = cx.analyze(CASES[1][1], statement="Pierwszy wiersz zawiera liczbę n (1 ≤ n ≤ 1000).")
        self.assertEqual(r["constraints"]["n"], 1000)
        self.assertEqual(r["constraints_source"], "statement")
        self.assertEqual(r["time"]["verdict"], "ok")


class TestEmpirical(unittest.TestCase):
    def _pts(self, f, noise=0.03):
        rng = random.Random(1)
        return [[n, f(n) * (1 + rng.uniform(-noise, noise)) + 0.002] for n in [1000 * 2 ** i for i in range(9)]]

    def test_fit_linear(self):
        self.assertEqual(cx.fit_models(self._pts(lambda n: 2e-8 * n))["best"], "n")

    def test_fit_nlogn(self):
        self.assertEqual(cx.fit_models(self._pts(lambda n: 5e-9 * n * math.log2(n)))["best"], "n log n")

    def test_fit_quadratic(self):
        res = cx.fit_models(self._pts(lambda n: 1e-10 * n * n))
        self.assertEqual(res["best"], "n²")
        self.assertAlmostEqual(res["slope"], 2.0, delta=0.25)

    def test_template_rendering(self):
        rng = random.Random(3)
        out = cx.render_template("{n} {q}\n{arr:n:1:100}\n{lines:q:[rand:1:n] [rand:1:n]}", 5, rng)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "5 5")
        self.assertEqual(len(lines[1].split()), 5)
        self.assertEqual(len(lines), 7)
        self.assertEqual(len(cx.render_template("n\na_1..a_n", 4, rng).split()), 5)

    def test_infer_template(self):
        tpl = cx.infer_template(CASES[17][1])
        self.assertIn("{n}", tpl)
        inp = cx._gen_input(tpl, 10, random.Random(1))
        self.assertEqual(len(inp.strip().split("\n")), 11)

    @unittest.skipUnless(shutil.which("g++-16") or shutil.which("g++") or shutil.which("clang++"), "brak kompilatora")
    def test_measure_job(self):
        from oiforge.core import jobs
        code = H + "int main(){ios::sync_with_stdio(false);cin.tie(nullptr);int n;cin>>n; vector<int> a(n); for(auto&x:a)cin>>x; long long s=0; for(int i=0;i<n;i++) for(int j=0;j<n;j++) s=s*31+(a[j]^i); cout<<s;}"
        job, res = jobs.run_sync(cx._measure_work, {"code": code, "input_template": "{n}\n{arr:n:1:1000}",
                                                     "sizes": [1000, 2000, 4000, 8000, 16000], "time_limit": 3})
        self.assertTrue(res["ok"], res)
        self.assertGreaterEqual(len(res["points"]), 4)
        self.assertIn(res["fit"]["best"], ("n²", "n² log n", "n log² n", "n√n"))
        self.assertTrue(res["series"][0]["points"])


@unittest.skipUnless(PROGRAMS.exists(), "brak prywatnych programów ucznia")
class TestRealPrograms(unittest.TestCase):
    def test_all_programs_analyze(self):
        progs = json.loads(PROGRAMS.read_text(encoding="utf-8"))["programs"]
        base = PROGRAMS.parent
        for p in progs:
            code = (base / p["path"]).read_text(encoding="utf-8", errors="replace")
            r = cx.analyze(code, CONS)
            self.assertTrue(r["complexity"].startswith("O("), p["path"])


if __name__ == "__main__":
    unittest.main()

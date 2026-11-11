"""Testy Sprawdzarki kodu: każda reguła statyczna ma przypadek pozytywny (błąd wykryty) i negatywny
(poprawny kod — brak fałszywego alarmu). Dodatkowo: fałszywe alarmy na prawdziwych rozwiązaniach ucznia
(user_data/programs, pomijane gdy brak) i kilka testów analizy dynamicznej (sanitizery, różnice wariantów)."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("OIFORGE_NO_JOURNAL", "1")      # testy nie zaśmiecają dziennika ucznia

from oiforge import config  # noqa: E402
from oiforge.tools import _checker_rules as R  # noqa: E402
from oiforge.tools import checker  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = config.PROGRAMS_DIR / "index.json"

H = "#include <bits/stdc++.h>\nusing namespace std;\n"


def rules_of(code: str, severity: str | None = None) -> set[str]:
    res = R.analyze(code)
    assert not res["internal_errors"], res["internal_errors"]
    return {i["rule"] for i in res["issues"] if severity is None or i["severity"] == severity}


# (reguła, kod z błędem, poprawny kod)
CASES = [
    ("overflow-int-mul",
     H + "int main(){ int a, b; cin >> a >> b; long long x = a * b; cout << x; }",
     H + "int main(){ int a, b; cin >> a >> b; long long x = 1LL * a * b; cout << x; }"),
    ("overflow-mod-mul",
     H + "const int MOD = 1e9 + 7;\nint main(){ int a, b; cin >> a >> b; int c = a * b % MOD; cout << c; }",
     H + "const int MOD = 1e9 + 7;\nint main(){ int a, b; cin >> a >> b; int c = 1LL * a * b % MOD; cout << c; }"),
    ("overflow-cast-late",
     H + "int main(){ int a, b; cin >> a >> b; long long x = (long long)(a * b); cout << x; }",
     H + "int main(){ int a, b; cin >> a >> b; long long x = (long long)a * b; cout << x; }"),
    ("overflow-int-sum",
     H + "int main(){ int a, b; cin >> a >> b; long long s = a + b; cout << s; }",
     H + "int main(){ long long a, b; cin >> a >> b; long long s = a + b; cout << s; }"),
    ("shift-overflow",
     H + "int main(){ long long m = 1 << 40; cout << m; }",
     H + "int main(){ long long m = 1LL << 40; cout << m; }"),
    ("shift-int-wide",
     H + "int main(){ int k; cin >> k; long long m = 1 << k; cout << m; }",
     H + "int main(){ int k; cin >> k; long long m = 1LL << k; cout << m; }"),
    ("accumulate-int-init",
     H + "int main(){ vector<long long> v(5, 1e12); long long s = accumulate(v.begin(), v.end(), 0); cout << s; }",
     H + "int main(){ vector<long long> v(5, 1e12); long long s = accumulate(v.begin(), v.end(), 0LL); cout << s; }"),
    ("int-max-plus",
     H + "int main(){ int x = INT_MAX + 1; cout << x; }",
     H + "int main(){ long long x = (long long)INT_MAX + 1; cout << x; }"),
    ("inf-overflow",
     H + "int d[105][105];\nint main(){ int n; cin >> n; for(int i=0;i<n;i++) for(int j=0;j<n;j++) d[i][j] = INT_MAX;\n"
         "for(int k=0;k<n;k++) for(int i=0;i<n;i++) for(int j=0;j<n;j++) d[i][j] = min(d[i][j], d[i][k] + d[k][j]);\n"
         "cout << d[0][0]; }",
     H + "int d[105][105];\nint main(){ int n; cin >> n; for(int i=0;i<n;i++) for(int j=0;j<n;j++) d[i][j] = 1e9;\n"
         "for(int k=0;k<n;k++) for(int i=0;i<n;i++) for(int j=0;j<n;j++) d[i][j] = min(d[i][j], d[i][k] + d[k][j]);\n"
         "cout << d[0][0]; }"),
    ("int-literal-overflow",
     H + "const int INF = 1e18;\nint main(){ cout << INF; }",
     H + "const long long INF = 1e18;\nint main(){ cout << INF; }"),
    ("const-int-overflow",
     H + "int main(){ long long x = 1000000 * 1000000; cout << x; }",
     H + "int main(){ long long x = 1000000LL * 1000000; cout << x; }"),
    ("inf-0x3f-ll",
     H + "const long long INF = 0x3f3f3f3f;\nint main(){ long long d = INF; cout << d; }",
     H + "const long long INF = 0x3f3f3f3f3f3f3f3fLL;\nint main(){ long long d = INF; cout << d; }"),
    ("inf-memset-mismatch",
     H + "long long d[100];\nint main(){ memset(d, 0x3f, sizeof d); if (d[5] == 0x3f3f3f3f) cout << \"INF\"; }",
     H + "int d[100];\nint main(){ memset(d, 0x3f, sizeof d); if (d[5] == 0x3f3f3f3f) cout << \"INF\"; }"),
    ("narrowing-ll-to-int",
     H + "int main(){ long long a, b; cin >> a >> b; int r = a * b; cout << r; }",
     H + "int main(){ long long a, b; cin >> a >> b; long long r = a * b; cout << r; }"),
    ("size-minus-one",
     H + "int main(){ vector<int> v; int x; while (cin >> x) v.push_back(x);\n"
         "for (int i = 0; i < v.size() - 1; i++) cout << v[i + 1] - v[i] << '\\n'; }",
     H + "int main(){ vector<int> v; int x; while (cin >> x) v.push_back(x);\n"
         "for (int i = 0; i + 1 < (int)v.size(); i++) cout << v[i + 1] - v[i] << '\\n'; }"),
    ("signed-unsigned-loop",
     H + "int main(){ vector<int> v(5); for (int i = 0; i < v.size(); i++) cout << v[i]; }",
     H + "int main(){ vector<int> v(5); for (int i = 0; i < (int)v.size(); i++) cout << v[i]; }"),
    ("unsigned-reverse-loop",
     H + "int main(){ vector<int> v(5); for (size_t i = v.size() - 1; i >= 0; i--) cout << v[i]; }",
     H + "int main(){ vector<int> v(5); for (int i = (int)v.size() - 1; i >= 0; i--) cout << v[i]; }"),
    ("assign-in-condition",
     H + "int main(){ int x; cin >> x; if (x = 5) cout << \"piec\"; }",
     H + "int main(){ int x; cin >> x; if (x == 5) cout << \"piec\"; }"),
    ("uninit-local",
     H + "int main(){ int n, s; cin >> n; for (int i = 0; i < n; i++) { int x; cin >> x; s += x; } cout << s; }",
     H + "int main(){ int n, s = 0; cin >> n; for (int i = 0; i < n; i++) { int x; cin >> x; s += x; } cout << s; }"),
    ("uninit-local-array",
     H + "int main(){ string s; cin >> s; int cnt[26]; for (char c : s) cnt[c - 'a']++; cout << cnt[0]; }",
     H + "int main(){ string s; cin >> s; int cnt[26] = {}; for (char c : s) cnt[c - 'a']++; cout << cnt[0]; }"),
    ("missing-return",
     H + "int f(int x) { x *= 2; }\nint main(){ cout << f(3); }",
     H + "int f(int x) { x *= 2; return x; }\nint main(){ cout << f(3); }"),
    ("missing-return-path",
     H + "int znajdz(vector<int>& v, int x) { for (int i = 0; i < (int)v.size(); i++) if (v[i] == x) return i; }\n"
         "int main(){ vector<int> v = {1, 2}; cout << znajdz(v, 2); }",
     H + "int znajdz(vector<int>& v, int x) { for (int i = 0; i < (int)v.size(); i++) if (v[i] == x) return i; return -1; }\n"
         "int main(){ vector<int> v = {1, 2}; cout << znajdz(v, 2); }"),
    ("comparator-non-strict",
     H + "bool cmp(int a, int b) { return a <= b; }\nint main(){ vector<int> v = {3, 1, 2}; sort(v.begin(), v.end(), cmp); }",
     H + "bool cmp(int a, int b) { return a < b; }\nint main(){ vector<int> v = {3, 1, 2}; sort(v.begin(), v.end(), cmp); }"),
    ("chained-comparison",
     H + "int main(){ int a, b, c; cin >> a >> b >> c; if (a < b < c) cout << \"rosnaco\"; }",
     H + "int main(){ int a, b, c; cin >> a >> b >> c; if (a < b && b < c) cout << \"rosnaco\"; }"),
    ("bitwise-precedence",
     H + "int main(){ int x; cin >> x; if (x & 1 == 0) cout << \"parzysta\"; }",
     H + "int main(){ int x; cin >> x; if ((x & 1) == 0) cout << \"parzysta\"; }"),
    ("xor-as-power",
     H + "int main(){ int MAXV = 10^9; cout << MAXV; }",
     H + "int main(){ int MAXV = 1e9; cout << MAXV; }"),
    ("logical-const",
     H + "int main(){ char c; cin >> c; if (c == 'a' || 'e') cout << \"samogloska\"; }",
     H + "int main(){ char c; cin >> c; if (c == 'a' || c == 'e') cout << \"samogloska\"; }"),
    ("comma-in-index",
     H + "int a[10][10];\nint main(){ int i = 1, j = 2; a[i, j] = 5; cout << a[1][2]; }",
     H + "int a[10][10];\nint main(){ int i = 1, j = 2; a[i][j] = 5; cout << a[1][2]; }"),
    ("empty-body",
     H + "int main(){ int x; cin >> x; if (x > 0); { cout << \"dodatnia\"; } }",
     H + "int main(){ int x; cin >> x; if (x > 0) { cout << \"dodatnia\"; } }"),
    ("switch-fallthrough",
     H + "int main(){ int k; cin >> k; switch (k) { case 1: cout << \"a\"; case 2: cout << \"b\"; break; } }",
     H + "int main(){ int k; cin >> k; switch (k) { case 1: cout << \"a\"; break; case 2: cout << \"b\"; break; } }"),
    ("recursion-no-base",
     H + "long long silnia(int n) { return n * silnia(n - 1); }\nint main(){ cout << silnia(5); }",
     H + "long long silnia(int n) { if (n <= 1) return 1; return n * silnia(n - 1); }\nint main(){ cout << silnia(5); }"),
    ("float-equality",
     H + "int main(){ double a = 0.1, b = 0.2; if (a + b == 0.3) cout << \"rowne\"; }",
     H + "int main(){ double a = 0.1, b = 0.2; if (fabs(a + b - 0.3) < 1e-9) cout << \"rowne\"; }"),
    ("int-division-double",
     H + "int main(){ int suma, n; cin >> suma >> n; double sr = suma / n; cout << sr; }",
     H + "int main(){ int suma, n; cin >> suma >> n; double sr = (double)suma / n; cout << sr; }"),
    ("int-div-literal",
     H + "int main(){ double r; cin >> r; double pole = 1/2 * r * r; cout << pole; }",
     H + "int main(){ double r; cin >> r; double pole = 1.0/2 * r * r; cout << pole; }"),
    ("negative-modulo",
     H + "const int MOD = 1e9 + 7;\nint main(){ long long a, b; cin >> a >> b; long long r = (a - b) % MOD; cout << r; }",
     H + "const int MOD = 1e9 + 7;\nint main(){ long long a, b; cin >> a >> b; long long r = ((a - b) % MOD + MOD) % MOD; cout << r; }"),
    ("index-out-of-range",
     H + "int main(){ int n; cin >> n; vector<int> a(n); for (int i = 1; i <= n; i++) cin >> a[i]; cout << a[1]; }",
     H + "int main(){ int n; cin >> n; vector<int> a(n + 1); for (int i = 1; i <= n; i++) cin >> a[i]; cout << a[1]; }"),
    ("index-negative",
     H + "int main(){ int n; cin >> n; vector<int> a(n); for (int &x : a) cin >> x;\n"
         "int c = 0; for (int i = 0; i < n; i++) c += a[i] > a[i - 1]; cout << c; }",
     H + "int main(){ int n; cin >> n; vector<int> a(n); for (int &x : a) cin >> x;\n"
         "int c = 0; for (int i = 1; i < n; i++) c += a[i] > a[i - 1]; cout << c; }"),
    ("vector-no-size-index",
     H + "int main(){ int n; cin >> n; vector<int> v; for (int i = 0; i < n; i++) cin >> v[i]; cout << v[0]; }",
     H + "int main(){ int n; cin >> n; vector<int> v(n); for (int i = 0; i < n; i++) cin >> v[i]; cout << v[0]; }"),
    ("size-before-read",
     H + "int main(){ int n; vector<int> v(n); cin >> n; for (int i = 0; i < n; i++) cin >> v[i]; cout << v[0]; }",
     H + "int main(){ int n; cin >> n; vector<int> v(n); for (int i = 0; i < n; i++) cin >> v[i]; cout << v[0]; }"),
    ("large-local-array",
     H + "int main(){ int a[5000000]; a[0] = 1; cout << a[0]; }",
     H + "int a[5000000];\nint main(){ a[0] = 1; cout << a[0]; }"),
    ("vla",
     H + "int main(){ int n; cin >> n; int a[n]; for (int i = 0; i < n; i++) cin >> a[i]; cout << a[0]; }",
     H + "int main(){ int n; cin >> n; vector<int> a(n); for (int i = 0; i < n; i++) cin >> a[i]; cout << a[0]; }"),
    ("sizeof-array-param",
     H + "void wyczysc(int t[]) { memset(t, 0, sizeof(t)); }\nint main(){ int a[100]; wyczysc(a); cout << a[5]; }",
     H + "void wyczysc(int t[], int n) { memset(t, 0, n * sizeof(int)); }\nint main(){ int a[100]; wyczysc(a, 100); cout << a[5]; }"),
    ("memset-value",
     H + "int dp[1000];\nint main(){ memset(dp, 1, sizeof dp); cout << dp[0]; }",
     H + "int dp[1000];\nint main(){ memset(dp, -1, sizeof dp); cout << dp[0]; }"),
    ("memset-size",
     H + "int dp[1000];\nint main(){ int n; cin >> n; memset(dp, 0, n); cout << dp[0]; }",
     H + "int dp[1000];\nint main(){ int n; cin >> n; memset(dp, 0, n * sizeof(int)); cout << dp[0]; }"),
    ("memset-vector",
     H + "int main(){ vector<int> v(10); memset(&v, 0, sizeof v); cout << v.size(); }",
     H + "int main(){ vector<int> v(10); fill(v.begin(), v.end(), 0); cout << v.size(); }"),
    ("endl-in-loop",
     H + "int main(){ int n; cin >> n; for (int i = 0; i < n; i++) cout << i << endl; }",
     H + "int main(){ int n; cin >> n; for (int i = 0; i < n; i++) cout << i << '\\n'; }"),
    ("no-fast-io",
     H + "int main(){ int n; cin >> n; long long s = 0; for (int i = 0; i < n; i++) { int x; cin >> x; s += x; } cout << s; }",
     H + "int main(){ ios::sync_with_stdio(false); cin.tie(nullptr); int n; cin >> n; long long s = 0;\n"
         "for (int i = 0; i < n; i++) { int x; cin >> x; s += x; } cout << s; }"),
    ("mixed-io-after-sync",
     H + "int main(){ ios::sync_with_stdio(false); int n; cin >> n; int x; scanf(\"%d\", &x); cout << n + x; }",
     H + "int main(){ ios::sync_with_stdio(false); int n; cin >> n; int x; cin >> x; cout << n + x; }"),
    ("scanf-format",
     H + "int main(){ long long n; scanf(\"%d\", &n); printf(\"%lld\", n); }",
     H + "int main(){ long long n; scanf(\"%lld\", &n); printf(\"%lld\", n); }"),
    ("printf-format",
     H + "int main(){ long long n = 1e12; printf(\"%d\\n\", n); }",
     H + "int main(){ long long n = 1e12; printf(\"%lld\\n\", n); }"),
    ("cin-eof-loop",
     H + "int main(){ int x; long long s = 0; while (!cin.eof()) { cin >> x; s += x; } cout << s; }",
     H + "int main(){ int x; long long s = 0; while (cin >> x) { s += x; } cout << s; }"),
    ("getline-after-cin",
     H + "int main(){ int n; cin >> n; string s; getline(cin, s); cout << s.size(); }",
     H + "int main(){ int n; cin >> n; cin.ignore(); string s; getline(cin, s); cout << s.size(); }"),
    ("freopen-left",
     H + "int main(){ freopen(\"in.txt\", \"r\", stdin); int n; cin >> n; cout << n; }",
     H + "int main(){\n#ifdef LOCAL\n freopen(\"in.txt\", \"r\", stdin);\n#endif\n int n; cin >> n; cout << n; }"),
    ("double-print-precision",
     H + "int main(){ double x; cin >> x; double y = x / 3; cout << y; }",
     H + "int main(){ double x; cin >> x; double y = x / 3; cout << fixed << setprecision(6) << y; }"),
    ("erase-in-loop",
     H + "int main(){ set<int> s = {1, 2, 3, 4};\n"
         "for (auto it = s.begin(); it != s.end(); it++) if (*it % 2 == 0) s.erase(it); cout << s.size(); }",
     H + "int main(){ set<int> s = {1, 2, 3, 4};\n"
         "for (auto it = s.begin(); it != s.end(); ) { if (*it % 2 == 0) it = s.erase(it); else ++it; } cout << s.size(); }"),
    ("modify-in-range-for",
     H + "int main(){ vector<int> v = {1, 2, 3}; for (int x : v) if (x % 2) v.push_back(x * 2); cout << v.size(); }",
     H + "int main(){ vector<int> v = {1, 2, 3}; vector<int> dod; for (int x : v) if (x % 2) dod.push_back(x * 2);\n"
         "for (int x : dod) v.push_back(x); cout << v.size(); }"),
    ("erase-index-skip",
     H + "int main(){ vector<int> v = {2, 2, 3}; for (int i = 0; i < v.size(); i++) if (v[i] == 2) v.erase(v.begin() + i);\n"
         "cout << v.size(); }",
     H + "int main(){ vector<int> v = {2, 2, 3}; for (int i = 0; i < v.size(); i++) if (v[i] == 2) { v.erase(v.begin() + i); i--; }\n"
         "cout << v.size(); }"),
    ("map-index-check",
     H + "int main(){ map<int,int> m; int x; cin >> x; if (m[x]) cout << \"jest\"; }",
     H + "int main(){ map<int,int> m; int x; cin >> x; if (m.count(x)) cout << \"jest\"; }"),
    ("strlen-in-loop",
     H + "char s[1000005];\nint main(){ scanf(\"%s\", s); int c = 0; for (int i = 0; i < strlen(s); i++) c += s[i] == 'a';\n"
         "printf(\"%d\", c); }",
     H + "char s[1000005];\nint main(){ scanf(\"%s\", s); int c = 0, len = strlen(s); for (int i = 0; i < len; i++) c += s[i] == 'a';\n"
         "printf(\"%d\", c); }"),
    ("pass-by-value",
     H + "int suma(vector<int> v, int i) { if (i == (int)v.size()) return 0; return v[i] + suma(v, i + 1); }\n"
         "int main(){ vector<int> v(1000, 1); cout << suma(v, 0); }",
     H + "int suma(const vector<int>& v, int i) { if (i == (int)v.size()) return 0; return v[i] + suma(v, i + 1); }\n"
         "int main(){ vector<int> v(1000, 1); cout << suma(v, 0); }"),
    ("string-concat-loop",
     H + "int main(){ int n; cin >> n; string s; for (int i = 0; i < n; i++) s = s + 'a'; cout << s.size(); }",
     H + "int main(){ int n; cin >> n; string s; for (int i = 0; i < n; i++) s += 'a'; cout << s.size(); }"),
    ("multitest-no-clear",
     H + "vector<int> g[200005];\nint main(){ int t; cin >> t; while (t--) { int n, m; cin >> n >> m;\n"
         "for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); } cout << g[1].size() << '\\n'; } }",
     H + "vector<int> g[200005];\nint main(){ int t; cin >> t; while (t--) { int n, m; cin >> n >> m;\n"
         "for (int i = 0; i <= n; i++) g[i].clear();\n"
         "for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); } cout << g[1].size() << '\\n'; } }"),
    ("define-int-main",
     "#include <bits/stdc++.h>\n#define int long long\nusing namespace std;\nint main(){ int n; cin >> n; cout << n; }",
     "#include <bits/stdc++.h>\n#define int long long\nusing namespace std;\nsigned main(){ int n; cin >> n; cout << n; }"),
    ("std-name-collision",
     H + "int count = 0;\nint main(){ count++; cout << count; }",
     H + "int cnt = 0;\nint main(){ cnt++; cout << cnt; }"),
    ("shadow-loop-var",
     H + "int main(){ int n = 3; for (int i = 0; i < n; i++) for (int i = 0; i < n; i++) cout << i; }",
     H + "int main(){ int n = 3; for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) cout << i + j; }"),
    ("shadow-global",
     H + "int n;\nint licz() { return n * 2; }\nint main(){ int n; cin >> n; cout << licz(); }",
     H + "int n;\nint licz() { return n * 2; }\nint main(){ cin >> n; cout << licz(); }"),
    ("unused-variable",
     H + "int main(){ int n, nieuzywana; cin >> n; cout << n; }",
     H + "int main(){ int n; cin >> n; cout << n; }"),
    ("main-no-return",
     H + "int main(){ cout << 1; }",
     H + "int main(){ cout << 1; return 0; }"),
    ("long-32bit-windows",
     H + "int main(){ long x = 5; cout << x; }",
     H + "int main(){ long long x = 5; cout << x; }"),
    ("system-pause",
     H + "int main(){ cout << 1; system(\"pause\"); }",
     H + "int main(){ cout << 1; }"),
    ("non-portable-header",
     "#include <iostream>\n#include <conio.h>\nint main(){ std::cout << 1; getch(); }",
     "#include <iostream>\nint main(){ std::cout << 1; }"),
    ("rand-usage",
     H + "int main(){ srand(time(0)); cout << rand() % 100; }",
     H + "int main(){ mt19937 rng(123); cout << uniform_int_distribution<int>(0, 99)(rng); }"),
    ("sort-range-1indexed",
     H + "int a[100005];\nint main(){ int n; cin >> n; for (int i = 1; i <= n; i++) cin >> a[i]; sort(a, a + n); cout << a[1]; }",
     H + "int a[100005];\nint main(){ int n; cin >> n; for (int i = 1; i <= n; i++) cin >> a[i]; sort(a + 1, a + n + 1); cout << a[1]; }"),
    ("pow-int",
     H + "int main(){ int k; cin >> k; long long p = (long long)pow(10, k); cout << p % 7; }",
     H + "int main(){ int k; cin >> k; long long p = 1; for (int i = 0; i < k; i++) p *= 10; cout << p % 7; }"),
    ("sqrt-precision",
     H + "int main(){ long long n; cin >> n; long long r = (long long)sqrt(n); cout << r; }",
     H + "int main(){ long long n; cin >> n; long long r = sqrtl(n); while (r * r > n) r--; while ((r + 1) * (r + 1) <= n) r++; cout << r; }"),
    ("abs-ll",
     "#include <cstdlib>\n#include <cstdio>\nint main(){ long long x; scanf(\"%lld\", &x); printf(\"%lld\", (long long)abs(x)); }",
     "#include <cstdlib>\n#include <cstdio>\nint main(){ long long x; scanf(\"%lld\", &x); printf(\"%lld\", llabs(x)); }"),
    ("shadow-local",
     H + "int main(){ int x = 5; { int x = 7; cout << x; } cout << x; }",
     H + "int main(){ int x = 5; { int y = 7; cout << y; } cout << x; }"),
]


class TestRules(unittest.TestCase):
    def test_rule_count(self):
        self.assertGreaterEqual(len(R.RULES), 40)
        cat = checker.handle_rules({})
        self.assertEqual(cat["count"], len(R.RULES))

    def test_cases_cover_rules(self):
        covered = {c[0] for c in CASES}
        self.assertGreaterEqual(len(covered), 40)
        missing = covered - set(R.RULES)
        self.assertFalse(missing, f"przypadki dla nieistniejących reguł: {missing}")

    def test_positive_and_negative(self):
        failures = []
        for rid, bad, good in CASES:
            got_bad = rules_of(bad)
            got_good = rules_of(good)
            if rid not in got_bad:
                failures.append(f"{rid}: NIE wykryto w kodzie z błędem (wykryto: {sorted(got_bad)})")
            if rid in got_good:
                failures.append(f"{rid}: FAŁSZYWY ALARM w poprawnym kodzie")
        self.assertFalse(failures, "\n" + "\n".join(failures))

    def test_correct_programs_have_no_errors(self):
        for rid, bad, good in CASES:
            errs = rules_of(good, "error")
            self.assertFalse(errs & {"uninit-local", "index-out-of-range", "comparator-non-strict", "missing-return"},
                             f"poprawny kod dla {rid} dostał błędy {errs}")

    def test_issue_shape(self):
        res = R.analyze(CASES[0][1])
        self.assertTrue(res["issues"])
        it = res["issues"][0]
        for k in ("line", "col", "severity", "rule", "title", "message", "fix", "kb", "confidence"):
            self.assertIn(k, it)
        self.assertIn(it["severity"], ("error", "warning", "perf", "style", "info"))
        self.assertIn("stats", res)
        self.assertIn("health", res["stats"])
        # wiadomości po polsku
        self.assertTrue(any(ch in it["message"] for ch in "ąęłóśżźćń"), it["message"])

    def test_issue_line_points_at_bug(self):
        code = H + "int main(){\n    int n;\n    cin >> n;\n    vector<int> a(n);\n    for (int i = 0; i <= n; i++)\n        a[i] = i;\n}\n"
        res = R.analyze(code)
        hits = [i for i in res["issues"] if i["rule"] == "index-out-of-range"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 8)

    def test_kb_links_exist(self):
        from oiforge.core import kb
        ids = {a["id"] for a in kb.articles()}
        res = checker.handle_analyze({"code": CASES[0][1]})
        for it in res["issues"]:
            if it["kb"]:
                self.assertIn(it["kb"], ids)

    def test_macros_and_aliases(self):
        code = ("#include <bits/stdc++.h>\n#define ll long long\n#define FOR(i,a,b) for (int i = (a); i < (b); i++)\n"
                "using namespace std;\ntypedef pair<int,int> pii;\nint main(){ int n; cin >> n; vector<int> v(n);\n"
                "FOR(i, 0, n + 1) v[i] = i; ll s = 0; FOR(i, 0, n) s += v[i]; cout << s; }")
        got = rules_of(code)
        self.assertIn("index-out-of-range", got)

    def test_weird_code_does_not_crash(self):
        weird = ["", "int main(", "}}}{{{", "#define X(a) a##a\nint main(){ X(1); }", "template<class T> struct A { T x; };",
                 "int main(){ auto f = [&](auto self, int x) -> int { return x ? self(self, x - 1) : 0; }; }",
                 "struct P { int x, y; bool operator<(const P& o) const { return x < o.x || (x == o.x && y < o.y); } };",
                 "int main(){ int a[3] = {1, 2, 3}; for (auto [x, y] : map<int,int>{}) {} }",
                 "x" * 10000, "int main(){" + "(" * 300 + "1" + ")" * 300 + ";}"]
        for w in weird:
            res = R.analyze(w)
            self.assertIn("issues", res)

    def test_lambda_comparator(self):
        got = rules_of(H + "int main(){ vector<int> v = {3,1,2}; sort(v.begin(), v.end(), [](int a, int b){ return a >= b; }); }")
        self.assertIn("comparator-non-strict", got)
        got = rules_of(H + "struct E { int u, v, w; };\nint main(){ vector<E> e(3);\n"
                       "sort(e.begin(), e.end(), [](const E& a, const E& b){ if (a.w != b.w) return a.w < b.w; return a.u < b.u; }); }")
        self.assertNotIn("comparator-non-strict", got)

    def test_python_code_is_not_analyzed_as_cpp(self):
        res = checker.handle_analyze({"code": "n = int(input())\nprint(n * 2)\n"})
        self.assertEqual(res["issues"], [])
        self.assertEqual(res["stats"]["language"], "python")

    def test_handle_analyze_requires_code(self):
        from oiforge.server_api import ApiError
        with self.assertRaises(ApiError):
            checker.handle_analyze({"code": "   "})

    def test_routes(self):
        self.assertIn("POST /api/checker/analyze", checker.ROUTES)
        self.assertIn("POST /api/checker/run", checker.ROUTES)
        self.assertIn("GET /api/checker/rules", checker.ROUTES)

    def test_sanitizer_parser(self):
        ub = ("main.cpp:9:21: runtime error: signed integer overflow: 2147483647 + 3 cannot be represented in type 'int'\n"
              "    #0 0x00010424cb00 in main main.cpp:9\n")
        items = checker.parse_sanitizer(ub, "a\n" * 20)
        self.assertEqual(items[0]["rule"], "ub-overflow")
        self.assertEqual(items[0]["line"], 9)
        asan = ("==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x6020000000fc at pc 0x1 bp 0x2 sp 0x3\n"
                "WRITE of size 4 at 0x6020000000fc thread T0\n    #0 0x000102958d08 in main main.cpp:10\n"
                "    #1 0x00018ac07dfc in start+0x1b4c (dyld:arm64e+0x1fdfc)\n\n"
                "0x6020000000fc is located 0 bytes after 12-byte region [0x6020000000f0,0x6020000000fc)\n")
        items = checker.parse_sanitizer(asan, "a\n" * 20)
        self.assertEqual(items[0]["rule"], "asan-heap-overflow")
        self.assertEqual(items[0]["line"], 10)
        self.assertIn("indeksie ≈ 3", items[0]["message"])
        st = ("==1==ERROR: AddressSanitizer: stack-overflow on address 0x1 (pc 0x2 bp 0x3 sp 0x4 T0)\n"
              "    #0 0x000100e08704 in f(int) main.cpp:3\n    #1 0x000100e08718 in f(int) main.cpp:3\n")
        items = checker.parse_sanitizer(st, "")
        self.assertEqual(items[0]["rule"], "asan-stack-exhausted")
        self.assertEqual(items[0]["line"], 3)


@unittest.skipUnless(PROGRAMS.exists(), "brak user_data/programs (dane prywatne)")
class TestUserCorpus(unittest.TestCase):
    """238 zaakceptowanych (100 pkt) rozwiązań ucznia — zgłoszenia „error” to prawie na pewno fałszywe alarmy."""

    def test_false_alarms_minimal(self):
        idx = json.loads(PROGRAMS.read_text(encoding="utf-8"))["programs"]
        errors_total = 0
        files_with_errors = []
        for p in idx:
            path = config.PROGRAMS_DIR / p["path"]
            if not path.exists():
                continue
            code = path.read_text(encoding="utf-8", errors="replace")
            res = R.analyze(code)
            self.assertFalse(res["internal_errors"], f"{p['path']}: {res['internal_errors']}")
            errs = [i for i in res["issues"] if i["severity"] == "error"]
            if errs:
                files_with_errors.append((p["path"], [(e["rule"], e["line"]) for e in errs]))
            errors_total += len(errs)
        # jedyny znany przypadek to PRAWDZIWY błąd (zapis tab[n] przy tablicy rozmiaru n, który przypadkiem przeszedł)
        self.assertLessEqual(len(files_with_errors), 2, files_with_errors)


def _have_cxx() -> bool:
    return bool(config.CXX)


@unittest.skipUnless(_have_cxx(), "brak kompilatora C++")
class TestDynamic(unittest.TestCase):
    """Analiza dynamiczna — kompiluje i uruchamia (wolniejsze)."""

    def test_vector_out_of_bounds_and_uninit(self):
        code = (H + "int main() {\n    int n;\n    cin >> n;\n    vector<int> v(n);\n    for (int i = 0; i < n; i++) cin >> v[i];\n"
                "    int s;\n    for (int i = 0; i < n; i++) s += v[i];\n    cout << s << ' ' << v[n] << '\\n';\n}\n")
        res = checker.run_dynamic(code, ["3\n1 2 3\n"], timeout=2.0)
        rules = {i["rule"] for i in res["issues"]}
        self.assertTrue(res["compile"]["ok"])
        self.assertIn("uninit-local", rules)
        dyn = [i for i in res["issues"] if i["source"] in ("sanitizer", "differential")]
        self.assertTrue(dyn, f"brak zgłoszeń dynamicznych: {rules}")
        if config.capabilities().get("sanitizers"):
            oob = [i for i in res["issues"] if i["rule"] in ("stl-hardening", "asan-container-overflow",
                                                              "asan-heap-overflow", "stl-debug", "stl-assert")]
            self.assertTrue(oob, rules)
            self.assertTrue(any(i["line"] == 10 for i in oob), [(i["rule"], i["line"]) for i in oob])
        self.assertIn("health", res["summary"])

    def test_compile_error_is_translated(self):
        res = checker.run_dynamic("int main() { int a = 5\n int b; }", [""], timeout=1.0, variants=["release"])
        self.assertFalse(res["compile"]["ok"])
        self.assertTrue(any(i["rule"] == "compile-error" for i in res["issues"]))
        self.assertEqual(res["summary"]["health"]["label"], "Błąd kompilacji")

    def test_infinite_loop_tle(self):
        code = H + "int main(){ int n; cin >> n; long long s = 0; for (int i = 0; i < n; i += 0) s++; cout << s; }"
        res = checker.run_dynamic(code, ["5"], timeout=0.5, variants=["release"])
        self.assertTrue(any(i["rule"] in ("tle-infinite", "tle-slow") for i in res["issues"]),
                        [i["rule"] for i in res["issues"]])

    def test_job_api(self):
        from oiforge.core import jobs
        r = checker.handle_run({"code": H + "int main(){ int a, b; cin >> a >> b; cout << a + b; }",
                                "inputs": [{"input": "2 3", "expected": "5"}], "variants": ["release"]})
        self.assertIn("job", r)
        import time
        for _ in range(600):
            snap = jobs.get(r["job"]).snapshot()
            if snap["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(snap["status"], "done", snap.get("error"))
        self.assertTrue(snap["result"]["inputs"][0].get("expected_ok"))


if __name__ == "__main__":
    unittest.main()

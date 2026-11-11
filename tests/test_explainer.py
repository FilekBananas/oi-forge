"""Testy objaśniacza kodu (oiforge.tools.explainer)."""
import json
import re
import unittest
from pathlib import Path

from oiforge.tools import explainer as ex

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ROOT / "user_data" / "programs" / "index.json"
H = "#include <bits/stdc++.h>\nusing namespace std;\n"

DIJKSTRA = H + """vector<pair<int,int>> g[200005];
long long dist[200005];
int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, m;
    cin >> n >> m;
    for (int i = 0; i < m; i++) {
        int a, b, w;
        cin >> a >> b >> w;
        g[a].push_back({b, w});
    }
    for (int i = 1; i <= n; i++) dist[i] = LLONG_MAX;
    dist[1] = 0;
    priority_queue<pair<long long,int>, vector<pair<long long,int>>, greater<>> pq;
    pq.push({0, 1});
    while (!pq.empty()) {
        auto [d, v] = pq.top();
        pq.pop();
        if (d != dist[v]) continue;
        for (auto [u, w] : g[v]) {
            if (dist[v] + w < dist[u]) {
                dist[u] = dist[v] + w;
                pq.push({dist[u], u});
            }
        }
    }
    cout << dist[n] << '\\n';
}
"""

ROLES = H + """int main() {
    int n;
    cin >> n;
    long long suma = 0;
    int ile = 0, mx = INT_MIN;
    bool znaleziono = false;
    for (int i = 0; i < n; i++) {
        int x;
        cin >> x;
        suma += x;
        if (x % 2 == 0) ile++;
        mx = max(mx, x);
        if (x == 7) znaleziono = true;
    }
    cout << suma << ' ' << ile << ' ' << mx << ' ' << znaleziono << endl;
}
"""

RECURSIVE = H + """long long silnia(int n) {
    if (n <= 1) return 1;
    return n * silnia(n - 1);
}
int policz(const vector<int>& v, int k) {
    int c = 0;
    for (int x : v) if (x > k) c++;
    return c;
}
int main() {
    int n;
    cin >> n;
    cout << silnia(n) << endl;
}
"""


# ---------------------------------------------------------------- programy szkolne (objaśnienie „jak nauczyciel”)
PROGRAMS_T = {}
P = PROGRAMS_T
P["sum"] = H + """int main() {
    int n;
    cin >> n;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    long long s = 0;
    for (int i = 0; i < n; i++) s += a[i];
    cout << s << "\\n";
}
"""
P["max"] = H + """int main() {
    int n;
    cin >> n;
    int mx = INT_MIN;
    for (int i = 0; i < n; i++) {
        int x;
        cin >> x;
        mx = max(mx, x);
    }
    cout << mx << endl;
}
"""
P["min_if"] = H + """int main() {
    int n;
    cin >> n;
    int a[1000];
    for (int i = 0; i < n; i++) cin >> a[i];
    int mn = a[0];
    for (int i = 1; i < n; i++)
        if (a[i] < mn) mn = a[i];
    cout << mn << '\\n';
}
"""
P["count_even"] = H + """int main() {
    int n;
    cin >> n;
    int ile = 0;
    for (int i = 0; i < n; i++) {
        int x;
        cin >> x;
        if (x % 2 == 0) ile++;
    }
    cout << ile << '\\n';
}
"""
P["pairs"] = H + """int main() {
    int n, k;
    cin >> n >> k;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    long long cnt = 0;
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            if (a[i] + a[j] == k) cnt++;
    cout << cnt << '\\n';
}
"""
P["maxsub3"] = H + """int main() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    long long best = LLONG_MIN;
    for (int i = 0; i < n; i++) {
        for (int j = i; j < n; j++) {
            long long s = 0;
            for (int k = i; k <= j; k++) s += a[k];
            best = max(best, s);
        }
    }
    cout << best << endl;
}
"""
P["maxsub2"] = H + """int main() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    long long best = a[0];
    for (int i = 0; i < n; i++) {
        long long s = 0;
        for (int j = i; j < n; j++) {
            s += a[j];
            if (s > best) best = s;
        }
    }
    cout << best << '\\n';
}
"""
P["kadane"] = H + """int main() {
    int n;
    cin >> n;
    vector<long long> a(n);
    for (auto &x : a) cin >> x;
    long long cur = 0, best = LLONG_MIN;
    for (int i = 0; i < n; i++) {
        cur = max(a[i], cur + a[i]);
        best = max(best, cur);
    }
    cout << best << '\\n';
}
"""
P["longest_run"] = H + """int main() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    int cur = 1, best = 1;
    for (int i = 1; i < n; i++) {
        if (a[i] > a[i - 1]) cur++;
        else cur = 1;
        best = max(best, cur);
    }
    cout << best << '\\n';
}
"""
P["sort_second"] = H + """int main() {
    int n;
    cin >> n;
    vector<pair<int, int>> v(n);
    for (auto &p : v) cin >> p.first >> p.second;
    sort(v.begin(), v.end(), [](const pair<int,int>& a, const pair<int,int>& b) {
        return a.second < b.second;
    });
    for (auto &p : v) cout << p.first << ' ' << p.second << '\\n';
}
"""
P["sort_desc"] = H + """int main() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int &x : a) cin >> x;
    sort(a.begin(), a.end(), greater<int>());
    for (int x : a) cout << x << ' ';
    cout << '\\n';
}
"""
P["binsearch"] = H + """int main() {
    int n, q;
    cin >> n >> q;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    sort(a.begin(), a.end());
    while (q--) {
        int x;
        cin >> x;
        int lo = 0, hi = n - 1, ans = -1;
        while (lo <= hi) {
            int mid = (lo + hi) / 2;
            if (a[mid] >= x) { ans = mid; hi = mid - 1; }
            else lo = mid + 1;
        }
        cout << (ans == -1 ? n : ans) << '\\n';
    }
}
"""
P["lower_bound"] = H + """int main() {
    int n, q;
    cin >> n >> q;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    sort(a.begin(), a.end());
    while (q--) {
        int x;
        cin >> x;
        cout << lower_bound(a.begin(), a.end(), x) - a.begin() << '\\n';
    }
}
"""
P["bfs"] = H + """int main() {
    int n, m;
    cin >> n >> m;
    vector<vector<int>> g(n + 1);
    for (int i = 0; i < m; i++) {
        int a, b;
        cin >> a >> b;
        g[a].push_back(b);
        g[b].push_back(a);
    }
    vector<int> dist(n + 1, -1);
    queue<int> q;
    dist[1] = 0;
    q.push(1);
    while (!q.empty()) {
        int v = q.front();
        q.pop();
        for (int u : g[v]) {
            if (dist[u] == -1) {
                dist[u] = dist[v] + 1;
                q.push(u);
            }
        }
    }
    for (int i = 1; i <= n; i++) cout << dist[i] << ' ';
    cout << '\\n';
}
"""
P["dfs_components"] = H + """vector<int> g[100005];
bool vis[100005];
void dfs(int v) {
    vis[v] = true;
    for (int u : g[v])
        if (!vis[u]) dfs(u);
}
int main() {
    int n, m;
    cin >> n >> m;
    for (int i = 0; i < m; i++) {
        int a, b;
        cin >> a >> b;
        g[a].push_back(b);
        g[b].push_back(a);
    }
    int comps = 0;
    for (int v = 1; v <= n; v++)
        if (!vis[v]) {
            comps++;
            dfs(v);
        }
    cout << comps << '\\n';
}
"""
P["dijkstra"] = H + """int main() {
    int n, m;
    cin >> n >> m;
    vector<vector<pair<int,int>>> g(n + 1);
    for (int i = 0; i < m; i++) {
        int a, b, w;
        cin >> a >> b >> w;
        g[a].push_back({b, w});
        g[b].push_back({a, w});
    }
    vector<long long> dist(n + 1, LLONG_MAX);
    priority_queue<pair<long long,int>, vector<pair<long long,int>>, greater<>> pq;
    dist[1] = 0;
    pq.push({0, 1});
    while (!pq.empty()) {
        auto [d, v] = pq.top();
        pq.pop();
        if (d > dist[v]) continue;
        for (auto [u, w] : g[v])
            if (dist[v] + w < dist[u]) {
                dist[u] = dist[v] + w;
                pq.push({dist[u], u});
            }
    }
    cout << dist[n] << '\\n';
}
"""
P["dsu"] = H + """int p[200005], sz[200005];
int find(int x) { return p[x] == x ? x : p[x] = find(p[x]); }
void unite(int a, int b) {
    a = find(a); b = find(b);
    if (a == b) return;
    if (sz[a] < sz[b]) swap(a, b);
    p[b] = a;
    sz[a] += sz[b];
}
int main() {
    int n, q;
    cin >> n >> q;
    for (int i = 1; i <= n; i++) { p[i] = i; sz[i] = 1; }
    while (q--) {
        int t, a, b;
        cin >> t >> a >> b;
        if (t == 1) unite(a, b);
        else cout << (find(a) == find(b) ? "TAK" : "NIE") << '\\n';
    }
}
"""
P["sieve"] = H + """int main() {
    int n;
    cin >> n;
    vector<bool> pierwsza(n + 1, true);
    pierwsza[0] = pierwsza[1] = false;
    for (int i = 2; (long long)i * i <= n; i++)
        if (pierwsza[i])
            for (int j = i * i; j <= n; j += i) pierwsza[j] = false;
    int ile = 0;
    for (int i = 2; i <= n; i++) if (pierwsza[i]) ile++;
    cout << ile << '\\n';
}
"""
P["gcd"] = H + """int nwd(int a, int b) {
    while (b != 0) {
        int r = a % b;
        a = b;
        b = r;
    }
    return a;
}
int main() {
    int a, b;
    cin >> a >> b;
    cout << nwd(a, b) << '\\n';
}
"""
P["fastpow"] = H + """const long long MOD = 1000000007;
long long potega(long long a, long long b) {
    long long r = 1;
    a %= MOD;
    while (b > 0) {
        if (b & 1) r = r * a % MOD;
        a = a * a % MOD;
        b >>= 1;
    }
    return r;
}
int main() {
    long long a, b;
    cin >> a >> b;
    cout << potega(a, b) << '\\n';
}
"""
P["naivepow"] = H + """int main() {
    long long a, b;
    cin >> a >> b;
    const long long M = 1000000007;
    long long r = 1;
    for (long long i = 0; i < b; i++) r = r * a % M;
    cout << r << '\\n';
}
"""
P["knapsack"] = H + """int main() {
    int n, W;
    cin >> n >> W;
    vector<int> w(n), v(n);
    for (int i = 0; i < n; i++) cin >> w[i] >> v[i];
    vector<long long> dp(W + 1, 0);
    for (int i = 0; i < n; i++)
        for (int j = W; j >= w[i]; j--)
            dp[j] = max(dp[j], dp[j - w[i]] + v[i]);
    cout << dp[W] << '\\n';
}
"""
P["lis_n2"] = H + """int main() {
    int n;
    cin >> n;
    vector<int> a(n), dp(n, 1);
    for (int i = 0; i < n; i++) cin >> a[i];
    int best = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < i; j++)
            if (a[j] < a[i]) dp[i] = max(dp[i], dp[j] + 1);
        best = max(best, dp[i]);
    }
    cout << best << '\\n';
}
"""
P["prefix"] = H + """int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    vector<long long> pref(n + 1, 0);
    for (int i = 1; i <= n; i++) {
        long long x;
        cin >> x;
        pref[i] = pref[i - 1] + x;
    }
    while (q--) {
        int l, r;
        cin >> l >> r;
        cout << pref[r] - pref[l - 1] << '\\n';
    }
}
"""
P["naive_queries"] = H + """int main() {
    int n, q;
    cin >> n >> q;
    vector<long long> a(n + 1);
    for (int i = 1; i <= n; i++) cin >> a[i];
    for (int t = 0; t < q; t++) {
        int l, r;
        cin >> l >> r;
        long long s = 0;
        for (int i = l; i <= r; i++) s += a[i];
        cout << s << '\\n';
    }
}
"""
P["two_pointers"] = H + """int main() {
    int n;
    long long S;
    cin >> n >> S;
    vector<long long> a(n);
    for (auto &x : a) cin >> x;
    int best = n + 1, l = 0;
    long long sum = 0;
    for (int r = 0; r < n; r++) {
        sum += a[r];
        while (sum >= S) {
            best = min(best, r - l + 1);
            sum -= a[l];
            l++;
        }
    }
    cout << (best == n + 1 ? 0 : best) << '\\n';
}
"""
P["brackets"] = H + """int main() {
    string s;
    cin >> s;
    stack<char> st;
    bool ok = true;
    for (char c : s) {
        if (c == '(') st.push(c);
        else {
            if (st.empty()) { ok = false; break; }
            st.pop();
        }
    }
    if (!st.empty()) ok = false;
    cout << (ok ? "TAK" : "NIE") << '\\n';
}
"""
P["fib_rec"] = H + """long long fib(int n) {
    if (n <= 1) return n;
    return fib(n - 1) + fib(n - 2);
}
int main() {
    int n;
    cin >> n;
    cout << fib(n) << '\\n';
}
"""
P["palindrome"] = H + """int main() {
    string s;
    cin >> s;
    int n = s.size();
    bool pal = true;
    for (int i = 0; i < n / 2; i++)
        if (s[i] != s[n - 1 - i]) pal = false;
    cout << (pal ? "TAK" : "NIE") << '\\n';
}
"""
P["letters"] = H + """int main() {
    string s;
    cin >> s;
    int cnt[26] = {0};
    for (char c : s) cnt[c - 'a']++;
    for (int i = 0; i < 26; i++)
        if (cnt[i] > 0) cout << char('a' + i) << ' ' << cnt[i] << '\\n';
}
"""
P["bubble"] = H + """int main() {
    int n;
    cin >> n;
    int a[1005];
    for (int i = 0; i < n; i++) cin >> a[i];
    for (int i = 0; i < n; i++)
        for (int j = 0; j + 1 < n - i; j++)
            if (a[j] > a[j + 1]) swap(a[j], a[j + 1]);
    for (int i = 0; i < n; i++) cout << a[i] << ' ';
    cout << '\\n';
}
"""
P["prime_check"] = H + """int main() {
    long long n;
    cin >> n;
    bool pierwsza = n >= 2;
    for (long long d = 2; d * d <= n; d++)
        if (n % d == 0) { pierwsza = false; break; }
    cout << (pierwsza ? "TAK" : "NIE") << '\\n';
}
"""
P["multitest_sum"] = H + """int main() {
    int t;
    cin >> t;
    while (t--) {
        int n;
        cin >> n;
        long long s = 0;
        for (int i = 0; i < n; i++) {
            long long x;
            cin >> x;
            s += x;
        }
        cout << s << '\\n';
    }
}
"""
P["grid_bfs"] = H + """int main() {
    int n, m;
    cin >> n >> m;
    vector<string> g(n);
    for (auto &row : g) cin >> row;
    vector<vector<int>> d(n, vector<int>(m, -1));
    int dx[] = {1, -1, 0, 0}, dy[] = {0, 0, 1, -1};
    queue<pair<int,int>> q;
    d[0][0] = 0;
    q.push({0, 0});
    while (!q.empty()) {
        auto [x, y] = q.front();
        q.pop();
        for (int k = 0; k < 4; k++) {
            int nx = x + dx[k], ny = y + dy[k];
            if (nx < 0 || ny < 0 || nx >= n || ny >= m || g[nx][ny] == '#' || d[nx][ny] != -1) continue;
            d[nx][ny] = d[x][y] + 1;
            q.push({nx, ny});
        }
    }
    cout << d[n - 1][m - 1] << '\\n';
}
"""
P["avg"] = H + """int main() {
    int n;
    cin >> n;
    double suma = 0;
    for (int i = 0; i < n; i++) {
        double x;
        cin >> x;
        suma += x;
    }
    cout << fixed << setprecision(2) << suma / n << '\\n';
}
"""

# (program, słowa kluczowe, które MUSZĄ być w overview_md — bez rozróżniania wielkości liter)
TEACHER_EXPECT = {
    "sum": ["wczytuje", "`n`", "tablicy `a`", "sumę elementów", "akumulator", "O(n)"],
    "max": ["największą z wczytanych liczb", "maksimum", "`mx`"],
    "min_if": ["najmniejszy element", "`mn`", "minimum"],
    "count_even": ["ile elementów spełnia warunek", "x % 2 == 0", "licznik `ile`"],
    "pairs": ["liczbę par", "a[i] + a[j] == k", "O(n²)", "dwoma wskaźnikami", "unordered_map"],
    "maxsub3": ["największą sumę spójnego fragmentu", "dla każdego początku `i`", "`j` ≥ `i`", "a[i..j]", "O(n³)",
                "trzy zagnieżdżone pętle", "Kadane", "prefiksowych"],
    "maxsub2": ["największą sumę spójnego fragmentu", "O(n²)", "Kadane"],
    "kadane": ["największą sumę spójnego fragmentu", "Kadane", "O(n)"],
    "longest_run": ["najdłuższego ściśle rosnącego spójnego fragmentu", "`cur`", "`best`"],
    "sort_second": ["sortuje `v` rosnąco według drugiego elementu pary", "O(n log n)", "posortowane"],
    "sort_desc": ["sortuje `a` malejąco", "O(n log n)"],
    "binsearch": ["szuka binarnie", "a[mid] >= x", "zapytania", "log n"],
    "lower_bound": ["lower_bound", "mniejszych od `x`", "log n"],
    "bfs": ["najkrótsze odległości", "BFS", "krawędzi grafu", "O(n + m)"],
    "dfs_components": ["liczbę spójnych składowych", "DFS", "O(n + m)"],
    "dijkstra": ["najkrótsze odległości", "Dijkstr", "kopca", "z wagami"],
    "dsu": ["w tym samym zbiorze", "Find & Union", "α(n)"],
    "sieve": ["ile jest liczb pierwszych", "Sito Eratostenesa", "n log log n"],
    "gcd": ["największy wspólny dzielnik", "Euklidesa", "O(log min(a, b))"],
    "fastpow": ["szybkim potęgowaniem", "O(log b)"],
    "naivepow": ["do potęgi `b` modulo `M`", "O(b)", "Szybkie potęgowanie", "O(log b)"],
    "knapsack": ["problem plecakowy 0/1", "dp[j]", "O(n·W)", "w dół"],
    "lis_n2": ["najdłuższego rosnącego podciągu", "O(n²)", "O(n log n)", "lower_bound"],
    "prefix": ["sum prefiksowych", "sumę na przedziale", "O(1)"],
    "naive_queries": ["sumę fragmentu", "a[l..r]", "Sumy prefiksowe"],
    "two_pointers": ["najkrótszego spójnego fragmentu", "sum >= S", "dwoma wskaźnikami", "O(n)"],
    "brackets": ["nawiasy są poprawnie sparowane", "stos"],
    "fib_rec": ["Fibonacciego", "rekurencyjnie", "1.62^n", "memoizacja", "O(n)"],
    "palindrome": ["palindromem", "od obu końców"],
    "letters": ["ile razy występuje każda litera", "c − 'a'"],
    "bubble": ["bąbelkow", "O(n²)", "sort(a, a + n)"],
    "prime_check": ["liczbą pierwszą", "√n"],
    "multitest_sum": ["sumę wczytanych liczb", "`t`", "testów"],
    "grid_bfs": ["najkrótsze odległości", "planszy", "BFS", "O(n·m)"],
    "avg": ["średnią arytmetyczną", "suma / n"],
}


class TestExplainer(unittest.TestCase):
    def test_structure(self):
        r = ex.explain(DIJKSTRA)
        for k in ("overview_md", "sections", "line_notes", "algorithms", "complexity"):
            self.assertIn(k, r)
        ids = [s["id"] for s in r["sections"]]
        for sid in ("input", "algorithm", "output", "complexity"):
            self.assertIn(sid, ids)
        for s in r["sections"]:
            self.assertTrue(s["title"] and s["md"])

    def test_dijkstra_explained(self):
        r = ex.explain(DIJKSTRA)
        self.assertIn("Dijkstr", r["overview_md"])
        alg = next(s for s in r["sections"] if s["id"] == "algorithm")["md"]
        self.assertIn("Dlaczego to działa", alg)
        self.assertIn("nieujemn", alg)
        inp = next(s for s in r["sections"] if s["id"] == "input")["md"]
        self.assertIn("`n` i `m`", inp)
        self.assertIn("krawędzie grafu", inp)
        st = next(s for s in r["sections"] if s["id"] == "structures")["md"]
        self.assertIn("NAJMNIEJSZY", st)          # greater<> → min-kopiec
        self.assertIn("odległości", st)

    def test_line_notes(self):
        r = ex.explain(DIJKSTRA)
        notes = {n["line"]: n["note"] for n in r["line_notes"]}
        self.assertIn(10, notes)                  # for po m krawędziach
        self.assertIn("m obrotów", notes[10])
        self.assertTrue(any("kopiec" in n or "kopca" in n for n in notes.values()))
        self.assertTrue(any("relaksacja" in n for n in notes.values()))
        self.assertTrue(all(isinstance(n["line"], int) and n["line"] > 0 for n in r["line_notes"]))

    def test_variable_roles(self):
        r = ex.explain(ROLES)
        roles = {v["name"]: v["role"] for v in r["variables"]}
        self.assertEqual(roles.get("suma"), "sum")
        self.assertEqual(roles.get("ile"), "counter")
        self.assertEqual(roles.get("mx"), "max")
        self.assertEqual(roles.get("znaleziono"), "flag")
        self.assertEqual(roles.get("n"), "input")

    def test_functions(self):
        r = ex.explain(RECURSIVE)
        fs = {f["name"]: f for f in r["functions"]}
        self.assertTrue(fs["silnia"]["recursive"])
        self.assertFalse(fs["policz"]["recursive"])
        md = next(s for s in r["sections"] if s["id"] == "functions")["md"]
        self.assertIn("rekurencyjna", md)
        self.assertIn("Warunek bazowy", md)
        self.assertIn("stałą referencję", md)     # const vector<int>&
        self.assertIn("zwraca liczbę całkowitą", md)

    def test_stl_dictionary_size(self):
        self.assertGreaterEqual(len(ex.STL), 150)
        for k, v in ex.STL.items():
            self.assertTrue(v and isinstance(v, str), k)
        self.assertIn("O(log n)", ex.STL["set.insert"])

    def test_algorithm_templates_cover_all_ids(self):
        from oiforge.core import algodetect
        self.assertEqual(set(ex.ALGO_TEXT), set(algodetect.IDS))
        for aid, t in ex.ALGO_TEXT.items():
            self.assertEqual(len(t), 4, aid)

    def test_fragment_and_garbage(self):
        r = ex.explain("for (int i = 0; i < n; i++) s += a[i];")
        self.assertTrue(r["overview_md"])
        self.assertEqual(r["line_notes"][0]["line"], 1)
        for code in ["}}}", "int main(", "x" * 1000, "int main(){}"]:
            self.assertIn("sections", ex.explain(code))

    def test_polish_text_quality(self):
        r = ex.explain(DIJKSTRA)
        text = r["overview_md"] + " ".join(s["md"] for s in r["sections"])
        self.assertNotRegex(text, r"\bNone\b|\bnull\b|\{[a-z_]+\}")
        self.assertRegex(text, r"[ąęłńóśźż]")

    def test_tips(self):
        slow = H + "int main(){ int n; cin >> n; for (int i = 0; i < n; i++) { int x; cin >> x; cout << x << endl; } }"
        r = ex.explain(slow)
        text = " ".join(s["md"] for s in r["sections"])
        self.assertIn("sync_with_stdio", text)
        self.assertIn("endl", text)


class TestTeacherOverview(unittest.TestCase):
    """Overview „jak nauczyciel”: co wczytuje, CO LICZY (z nazwami zmiennych), jak, złożoność i skąd, sugestia."""

    def test_programs(self):
        self.assertGreaterEqual(len(PROGRAMS_T), 25)
        bad = []
        for name, code in PROGRAMS_T.items():
            r = ex.explain(code)
            ov = r["overview_md"]
            self.assertTrue(ov.startswith("Program"), name)
            low = ov.lower()
            missing = [k for k in TEACHER_EXPECT.get(name, []) if k.lower() not in low]
            if missing:
                bad.append((name, missing, ov[:400]))
        self.assertFalse(bad, "\n".join(f"{n}: brak {m}\n   {o}" for n, m, o in bad))

    def test_structure_of_summary(self):
        r = ex.explain(PROGRAMS_T["maxsub3"])
        s = r["summary"]
        for k in ("reads", "computes", "outputs", "complexity", "complexity_why", "improvements"):
            self.assertIn(k, s)
        self.assertEqual(s["complexity"], "O(n³)")
        self.assertTrue(s["improvements"])
        self.assertIn("best", [c["var"] for c in s["computes"]])

    def test_overview_sections_order(self):
        ov = ex.explain(PROGRAMS_T["maxsub3"])["overview_md"]
        i_what, i_how, i_out = ov.find("liczy"), ov.find("**Jak to robi:**"), ov.find("**Wypisuje:**")
        i_cx, i_imp = ov.find("**Złożoność:**"), ov.find("**Można lepiej:**")
        self.assertTrue(0 < i_what < i_how < i_out < i_cx < i_imp, ov)

    def test_no_placeholders(self):
        for name, code in PROGRAMS_T.items():
            ov = ex.explain(code)["overview_md"]
            self.assertNotRegex(ov, r"\bNone\b|\{[a-z_]+\}|``", name)


@unittest.skipUnless(PROGRAMS.exists(), "brak prywatnych programów ucznia")
class TestRealPrograms(unittest.TestCase):
    def test_all_student_programs(self):
        progs = json.loads(PROGRAMS.read_text(encoding="utf-8"))["programs"]
        base = PROGRAMS.parent
        for p in progs[::3]:
            code = (base / p["path"]).read_text(encoding="utf-8", errors="replace")
            r = ex.explain(code)
            self.assertTrue(r["overview_md"].startswith("Program"), p["path"])
            self.assertTrue(r["sections"])


if __name__ == "__main__":
    unittest.main()

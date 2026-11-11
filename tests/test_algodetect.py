"""Testy rozpoznawania algorytmów (oiforge.core.algodetect).

1. Podręcznikowe implementacje każdego z 54 identyfikatorów (CONTRACT 3.5) — musi rozpoznać właściwy algorytm.
2. Brak fałszywych alarmów dla „zaawansowanych” algorytmów na prostych programach.
3. Kalibracja na prawdziwych programach ucznia (pomijana, gdy brak user_data/programs).
"""
import json
import unittest
from pathlib import Path

from oiforge.core import algodetect

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ROOT / "user_data" / "programs" / "index.json"

H = "#include <bits/stdc++.h>\nusing namespace std;\n"

SNIPPETS = {
    "binary_search": H + """
int main(){ int n, q; cin >> n >> q; vector<long long> a(n); for (auto &x : a) cin >> x;
  while (q--) { long long x; cin >> x; int lo = 0, hi = n - 1, ans = -1;
    while (lo <= hi) { int mid = lo + (hi - lo) / 2; if (a[mid] >= x) { ans = mid; hi = mid - 1; } else lo = mid + 1; }
    cout << ans << '\\n'; } }""",
    "two_pointers": H + """
int main(){ int n; long long s; cin >> n >> s; vector<long long> a(n); for (auto &x : a) cin >> x;
  int j = 0; long long sum = 0; int best = 0;
  for (int i = 0; i < n; i++) { while (j < n && sum + a[j] <= s) { sum += a[j]; j++; } best = max(best, j - i); sum -= a[i]; }
  cout << best; }""",
    "prefix_sums": H + """
long long p[200005];
int main(){ int n, q; cin >> n >> q; for (int i = 1; i <= n; i++) { long long x; cin >> x; p[i] = p[i - 1] + x; }
  while (q--) { int l, r; cin >> l >> r; cout << p[r] - p[l - 1] << '\\n'; } }""",
    "sorting": H + """
int main(){ int n; cin >> n; vector<int> a(n); for (auto &x : a) cin >> x; sort(a.begin(), a.end());
  for (int x : a) cout << x << ' '; }""",
    "greedy": H + """
// zachłannie: wybieramy przedziały kończące się najwcześniej
int main(){ int n; cin >> n; vector<pair<int,int>> v(n); for (auto &p : v) cin >> p.second >> p.first;
  sort(v.begin(), v.end()); int last = -1, cnt = 0;
  for (int i = 0; i < n; i++) { if (v[i].second >= last) { cnt++; last = v[i].first; } } cout << cnt; }""",
    "dp": H + """
int dp[1005][1005];
int main(){ string a, b; cin >> a >> b; int n = a.size(), m = b.size();
  for (int i = 1; i <= n; i++) for (int j = 1; j <= m; j++)
    if (a[i-1] == b[j-1]) dp[i][j] = dp[i-1][j-1] + 1; else dp[i][j] = max(dp[i-1][j], dp[i][j-1]);
  cout << dp[n][m]; }""",
    "bitmask_dp": H + """
int d[20][20]; int dp[1 << 18][18];
int main(){ int n; cin >> n; for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) cin >> d[i][j];
  for (int m = 0; m < (1 << n); m++) for (int i = 0; i < n; i++) dp[m][i] = 1e9; dp[1][0] = 0;
  for (int mask = 0; mask < (1 << n); mask++) for (int i = 0; i < n; i++) if (mask & (1 << i))
    for (int j = 0; j < n; j++) if (!(mask & (1 << j))) dp[mask | (1 << j)][j] = min(dp[mask | (1 << j)][j], dp[mask][i] + d[i][j]);
  cout << dp[(1 << n) - 1][n - 1]; }""",
    "bfs": H + """
vector<int> g[200005]; int dist[200005];
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  fill(dist, dist + n + 1, -1); queue<int> q; q.push(1); dist[1] = 0;
  while (!q.empty()) { int v = q.front(); q.pop(); for (int u : g[v]) if (dist[u] == -1) { dist[u] = dist[v] + 1; q.push(u); } }
  for (int i = 1; i <= n; i++) cout << dist[i] << '\\n'; }""",
    "dfs": H + """
vector<int> g[200005]; bool vis[200005];
void dfs(int v) { vis[v] = true; for (int u : g[v]) if (!vis[u]) dfs(u); }
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  int comps = 0; for (int i = 1; i <= n; i++) if (!vis[i]) { comps++; dfs(i); } cout << comps; }""",
    "dijkstra": H + """
vector<pair<int,int>> g[200005]; long long dist[200005];
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b, w; cin >> a >> b >> w; g[a].push_back({b, w}); }
  for (int i = 1; i <= n; i++) dist[i] = LLONG_MAX; dist[1] = 0;
  priority_queue<pair<long long,int>, vector<pair<long long,int>>, greater<>> pq; pq.push({0, 1});
  while (!pq.empty()) { auto [d, v] = pq.top(); pq.pop(); if (d != dist[v]) continue;
    for (auto [u, w] : g[v]) if (dist[v] + w < dist[u]) { dist[u] = dist[v] + w; pq.push({dist[u], u}); } }
  cout << dist[n]; }""",
    "bellman_ford": H + """
int main(){ int n, m; cin >> n >> m; vector<tuple<int,int,int>> edges(m); for (auto &[a, b, w] : edges) cin >> a >> b >> w;
  vector<long long> d(n + 1, 1e18); d[1] = 0;
  for (int it = 0; it < n - 1; it++) for (auto [a, b, w] : edges) if (d[a] < 1e18 && d[a] + w < d[b]) d[b] = d[a] + w;
  cout << d[n]; }""",
    "floyd": H + """
long long d[505][505];
int main(){ int n; cin >> n; for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) cin >> d[i][j];
  for (int k = 0; k < n; k++) for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) d[i][j] = min(d[i][j], d[i][k] + d[k][j]);
  cout << d[0][n-1]; }""",
    "topo_sort": H + """
vector<int> g[200005]; int indeg[200005];
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); indeg[b]++; }
  queue<int> q; for (int i = 1; i <= n; i++) if (indeg[i] == 0) q.push(i); vector<int> order;
  while (!q.empty()) { int v = q.front(); q.pop(); order.push_back(v); for (int u : g[v]) if (--indeg[u] == 0) q.push(u); }
  for (int v : order) cout << v << ' '; }""",
    "dsu": H + """
int p[200005], sz[200005];
int find(int x) { return p[x] == x ? x : p[x] = find(p[x]); }
void unite(int a, int b) { a = find(a); b = find(b); if (a == b) return; if (sz[a] < sz[b]) swap(a, b); p[b] = a; sz[a] += sz[b]; }
int main(){ int n, q; cin >> n >> q; for (int i = 1; i <= n; i++) { p[i] = i; sz[i] = 1; }
  while (q--) { int t, a, b; cin >> t >> a >> b; if (t == 1) unite(a, b); else cout << (find(a) == find(b) ? "TAK" : "NIE") << '\\n'; } }""",
    "mst": H + """
int p[200005];
int find(int x) { return p[x] == x ? x : p[x] = find(p[x]); }
int main(){ int n, m; cin >> n >> m; vector<tuple<int,int,int>> e(m); for (auto &[w, a, b] : e) cin >> a >> b >> w;
  sort(e.begin(), e.end()); for (int i = 1; i <= n; i++) p[i] = i; long long total = 0;
  for (auto [w, a, b] : e) if (find(a) != find(b)) { p[find(a)] = find(b); total += w; }
  cout << total; }""",
    "lca": H + """
const int LOG = 18; vector<int> g[200005]; int up[200005][LOG], dep[200005];
void dfs(int v, int p) { up[v][0] = p; for (int k = 1; k < LOG; k++) up[v][k] = up[up[v][k-1]][k-1];
  for (int u : g[v]) if (u != p) { dep[u] = dep[v] + 1; dfs(u, v); } }
int lca(int a, int b) { if (dep[a] < dep[b]) swap(a, b);
  for (int k = LOG - 1; k >= 0; k--) if (dep[a] - (1 << k) >= dep[b]) a = up[a][k];
  if (a == b) return a; for (int k = LOG - 1; k >= 0; k--) if (up[a][k] != up[b][k]) { a = up[a][k]; b = up[b][k]; } return up[a][0]; }
int main(){ int n, q; cin >> n >> q; for (int i = 0; i < n - 1; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  dfs(1, 1); while (q--) { int a, b; cin >> a >> b; cout << lca(a, b) << '\\n'; } }""",
    "segment_tree": H + """
const int N = 1 << 18; long long t[2 * N];
void upd(int v, int tl, int tr, int pos, long long val) { if (tl == tr) { t[v] = val; return; } int tm = (tl + tr) / 2;
  if (pos <= tm) upd(2 * v, tl, tm, pos, val); else upd(2 * v + 1, tm + 1, tr, pos, val); t[v] = t[2 * v] + t[2 * v + 1]; }
long long query(int v, int tl, int tr, int l, int r) { if (r < tl || tr < l) return 0; if (l <= tl && tr <= r) return t[v];
  int tm = (tl + tr) / 2; return query(2 * v, tl, tm, l, r) + query(2 * v + 1, tm + 1, tr, l, r); }
int main(){ int n, q; cin >> n >> q; while (q--) { int t_, a, b; cin >> t_ >> a >> b; if (t_ == 1) upd(1, 0, n - 1, a, b); else cout << query(1, 0, n - 1, a, b) << '\\n'; } }""",
    "lazy_segment_tree": H + """
const int N = 1 << 18; long long t[2 * N], lazy[2 * N];
void push(int v, int tl, int tr) { if (lazy[v]) { int tm = (tl + tr) / 2; t[2*v] += lazy[v] * (tm - tl + 1); t[2*v+1] += lazy[v] * (tr - tm);
  lazy[2 * v] += lazy[v]; lazy[2 * v + 1] += lazy[v]; lazy[v] = 0; } }
void add(int v, int tl, int tr, int l, int r, long long x) { if (r < tl || tr < l) return; if (l <= tl && tr <= r) { t[v] += x * (tr - tl + 1); lazy[v] += x; return; }
  push(v, tl, tr); int tm = (tl + tr) / 2; add(2 * v, tl, tm, l, r, x); add(2 * v + 1, tm + 1, tr, l, r, x); t[v] = t[2 * v] + t[2 * v + 1]; }
int main(){ int n, q; cin >> n >> q; while (q--) { int l, r; long long x; cin >> l >> r >> x; add(1, 0, n - 1, l, r, x); } cout << t[1]; }""",
    "fenwick": H + """
long long bit[200005]; int n;
void add(int i, long long x) { for (; i <= n; i += i & -i) bit[i] += x; }
long long sum(int i) { long long s = 0; for (; i > 0; i -= i & -i) s += bit[i]; return s; }
int main(){ int q; cin >> n >> q; while (q--) { int t, a; long long b; cin >> t >> a >> b; if (t == 1) add(a, b); else cout << sum(b) - sum(a - 1) << '\\n'; } }""",
    "sparse_table": H + """
int st[18][200005], lg[200005];
int main(){ int n, q; cin >> n >> q; for (int i = 0; i < n; i++) cin >> st[0][i];
  for (int i = 2; i <= n; i++) lg[i] = lg[i / 2] + 1;
  for (int k = 1; k < 18; k++) for (int i = 0; i + (1 << k) <= n; i++) st[k][i] = min(st[k-1][i], st[k-1][i + (1 << (k-1))]);
  while (q--) { int l, r; cin >> l >> r; int k = lg[r - l + 1]; cout << min(st[k][l], st[k][r - (1 << k) + 1]) << '\\n'; } }""",
    "kmp": H + """
int main(){ string s, t; cin >> s >> t; string w = t + "#" + s; int n = w.size(); vector<int> pi(n, 0);
  for (int i = 1; i < n; i++) { int k = pi[i - 1]; while (k > 0 && w[i] != w[k]) k = pi[k - 1]; if (w[i] == w[k]) k++; pi[i] = k; }
  int cnt = 0; for (int i = 0; i < n; i++) if (pi[i] == (int)t.size()) cnt++; cout << cnt; }""",
    "z_function": H + """
int main(){ string s; cin >> s; int n = s.size(); vector<int> z(n, 0);
  for (int i = 1, l = 0, r = 0; i < n; i++) { if (i <= r) z[i] = min(r - i + 1, z[i - l]);
    while (i + z[i] < n && s[z[i]] == s[i + z[i]]) z[i]++; if (i + z[i] - 1 > r) { l = i; r = i + z[i] - 1; } }
  for (int x : z) cout << x << ' '; }""",
    "hashing": H + """
const long long MOD = 1000000007, B = 131; long long h[200005], pw[200005];
long long get(int l, int r) { return ((h[r] - h[l - 1] * pw[r - l + 1]) % MOD + MOD) % MOD; }
int main(){ string s; cin >> s; int n = s.size(); pw[0] = 1;
  for (int i = 1; i <= n; i++) { h[i] = (h[i - 1] * B + s[i - 1]) % MOD; pw[i] = pw[i - 1] * B % MOD; }
  int q; cin >> q; while (q--) { int a, b, c, d; cin >> a >> b >> c >> d; cout << (get(a, b) == get(c, d) ? "TAK" : "NIE") << '\\n'; } }""",
    "trie": H + """
int nxt[1000005][26], cnt[1000005], nodes = 1;
void insert(const string &s) { int v = 0; for (char ch : s) { int c = ch - 'a'; if (!nxt[v][c]) nxt[v][c] = nodes++; v = nxt[v][c]; } cnt[v]++; }
int main(){ int n; cin >> n; for (int i = 0; i < n; i++) { string s; cin >> s; insert(s); } cout << nodes; }""",
    "manacher": H + """
int main(){ string s; cin >> s; int n = s.size(); vector<int> d1(n);
  for (int i = 0, l = 0, r = -1; i < n; i++) { int k = (i > r) ? 1 : min(d1[l + r - i], r - i + 1);
    while (i - k >= 0 && i + k < n && s[i - k] == s[i + k]) k++; d1[i] = k--; if (i + k > r) { l = i - k; r = i + k; } }
  cout << *max_element(d1.begin(), d1.end()); }""",
    "sieve": H + """
bool composite[1000001];
int main(){ int n; cin >> n; for (int i = 2; i * i <= n; i++) if (!composite[i]) for (int j = i * i; j <= n; j += i) composite[j] = true;
  for (int i = 2; i <= n; i++) if (!composite[i]) cout << i << '\\n'; }""",
    "fast_pow": H + """
const long long MOD = 1e9 + 7;
long long power(long long a, long long b) { long long r = 1; a %= MOD; while (b > 0) { if (b & 1) r = r * a % MOD; a = a * a % MOD; b >>= 1; } return r; }
int main(){ long long a, b; cin >> a >> b; cout << power(a, b); }""",
    "mod_inverse": H + """
const long long MOD = 1e9 + 7;
long long power(long long a, long long b) { long long r = 1; while (b) { if (b & 1) r = r * a % MOD; a = a * a % MOD; b >>= 1; } return r; }
long long inv(long long a) { return power(a, MOD - 2); }
int main(){ long long a, b; cin >> a >> b; cout << a * inv(b) % MOD; }""",
    "gcd": H + """
long long nwd(long long a, long long b) { return b == 0 ? a : nwd(b, a % b); }
int main(){ int n; cin >> n; long long g = 0; for (int i = 0; i < n; i++) { long long x; cin >> x; g = nwd(g, x); } cout << g; }""",
    "matrix_pow": H + """
typedef vector<vector<long long>> M; const long long MOD = 1e9 + 7;
M mul(const M &a, const M &b) { int n = a.size(); M c(n, vector<long long>(n, 0));
  for (int i = 0; i < n; i++) for (int k = 0; k < n; k++) for (int j = 0; j < n; j++) c[i][j] = (c[i][j] + a[i][k] * b[k][j]) % MOD; return c; }
int main(){ long long n; cin >> n; M r = {{1, 0}, {0, 1}}, a = {{1, 1}, {1, 0}};
  while (n > 0) { if (n & 1) r = mul(r, a); a = mul(a, a); n >>= 1; } cout << r[0][1]; }""",
    "convex_hull": H + """
typedef long long ll; struct P { ll x, y; };
ll cross(P o, P a, P b) { return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x); }
int main(){ int n; cin >> n; vector<P> p(n); for (auto &q : p) cin >> q.x >> q.y;
  sort(p.begin(), p.end(), [](P a, P b) { return a.x < b.x || (a.x == b.x && a.y < b.y); }); vector<P> h;
  for (int i = 0; i < n; i++) { while (h.size() >= 2 && cross(h[h.size()-2], h.back(), p[i]) <= 0) h.pop_back(); h.push_back(p[i]); }
  cout << h.size(); }""",
    "geometry": H + """
struct Punkt { double x, y; };
double odl(Punkt a, Punkt b) { return sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)); }
int main(){ Punkt a, b, c; cin >> a.x >> a.y >> b.x >> b.y >> c.x >> c.y;
  double cr = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  cout << fixed << setprecision(6) << odl(a, b) + odl(b, c) + odl(c, a) << ' ' << fabs(cr) / 2; }""",
    "monotonic_stack": H + """
int main(){ int n; cin >> n; vector<int> a(n), res(n, -1); for (auto &x : a) cin >> x; stack<int> st;
  for (int i = 0; i < n; i++) { while (!st.empty() && a[st.top()] < a[i]) { res[st.top()] = i; st.pop(); } st.push(i); }
  for (int x : res) cout << x << ' '; }""",
    "sliding_window": H + """
int main(){ int n, k; cin >> n >> k; vector<int> a(n); for (auto &x : a) cin >> x; deque<int> dq;
  for (int i = 0; i < n; i++) { while (!dq.empty() && a[dq.back()] >= a[i]) dq.pop_back(); dq.push_back(i);
    if (dq.front() <= i - k) dq.pop_front(); if (i >= k - 1) cout << a[dq.front()] << ' '; } }""",
    "recursion": H + """
long long silnia(int n) { if (n <= 1) return 1; return n * silnia(n - 1); }
int main(){ int n; cin >> n; cout << silnia(n); }""",
    "backtracking": H + """
int n; vector<int> perm; bool used[10];
void gen() { if ((int)perm.size() == n) { for (int x : perm) cout << x << ' '; cout << '\\n'; return; }
  for (int i = 1; i <= n; i++) if (!used[i]) { used[i] = true; perm.push_back(i); gen(); perm.pop_back(); used[i] = false; } }
int main(){ cin >> n; gen(); }""",
    "bitset": H + """
bitset<100001> can;
int main(){ int n; cin >> n; can[0] = 1; for (int i = 0; i < n; i++) { int w; cin >> w; can |= can << w; } cout << can.count(); }""",
    "scc": H + """
vector<int> g[100005]; int tin[100005], low[100005], comp[100005], timer_ = 0, ncomp = 0; bool onstack[100005]; stack<int> st;
void tarjan(int v) { tin[v] = low[v] = ++timer_; st.push(v); onstack[v] = true;
  for (int u : g[v]) { if (!tin[u]) { tarjan(u); low[v] = min(low[v], low[u]); } else if (onstack[u]) low[v] = min(low[v], tin[u]); }
  if (low[v] == tin[v]) { while (true) { int u = st.top(); st.pop(); onstack[u] = false; comp[u] = ncomp; if (u == v) break; } ncomp++; } }
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); }
  for (int i = 1; i <= n; i++) if (!tin[i]) tarjan(i); cout << ncomp; }""",
    "bridges": H + """
vector<int> g[100005]; int tin[100005], low[100005], timer_ = 0; vector<pair<int,int>> br;
void dfs(int v, int p) { tin[v] = low[v] = ++timer_;
  for (int u : g[v]) { if (u == p) continue; if (tin[u]) low[v] = min(low[v], tin[u]);
    else { dfs(u, v); low[v] = min(low[v], low[u]); if (low[u] > tin[v]) br.push_back({v, u}); } } }
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  for (int i = 1; i <= n; i++) if (!tin[i]) dfs(i, 0); cout << br.size(); }""",
    "bipartite": H + """
vector<int> g[100005]; int color[100005];
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  bool ok = true; for (int s = 1; s <= n; s++) if (!color[s]) { queue<int> q; q.push(s); color[s] = 1;
    while (!q.empty()) { int v = q.front(); q.pop(); for (int u : g[v]) { if (!color[u]) { color[u] = 3 - color[v]; q.push(u); } else if (color[u] == color[v]) ok = false; } } }
  cout << (ok ? "TAK" : "NIE"); }""",
    "flow": H + """
int cap[505][505], par[505]; int n;
int bfs(int s, int t) { fill(par, par + n + 1, -1); par[s] = s; queue<pair<int,int>> q; q.push({s, INT_MAX});
  while (!q.empty()) { auto [v, f] = q.front(); q.pop(); for (int u = 1; u <= n; u++) if (par[u] == -1 && cap[v][u] > 0) { par[u] = v; int nf = min(f, cap[v][u]); if (u == t) return nf; q.push({u, nf}); } }
  return 0; }
int main(){ int m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b, c; cin >> a >> b >> c; cap[a][b] += c; }
  int flow = 0, f; while ((f = bfs(1, n))) { flow += f; int v = n; while (v != 1) { int p = par[v]; cap[p][v] -= f; cap[v][p] += f; v = p; } }
  cout << flow; }""",
    "matching": H + """
vector<int> g[505]; int mt[505]; bool used[505];
bool try_kuhn(int v) { if (used[v]) return false; used[v] = true; for (int to : g[v]) if (mt[to] == -1 || try_kuhn(mt[to])) { mt[to] = v; return true; } return false; }
int main(){ int n, m, k; cin >> n >> m >> k; for (int i = 0; i < k; i++) { int a, b; cin >> a >> b; g[a].push_back(b); }
  fill(mt, mt + 505, -1); int res = 0; for (int v = 1; v <= n; v++) { fill(used, used + 505, false); if (try_kuhn(v)) res++; } cout << res; }""",
    "mo": H + """
int BLOCK; int cnt[1000005], a[200005], distinct_ = 0;
struct Q { int l, r, id; };
void add(int i) { if (cnt[a[i]]++ == 0) distinct_++; } void rem(int i) { if (--cnt[a[i]] == 0) distinct_--; }
int main(){ int n, q; cin >> n >> q; for (int i = 0; i < n; i++) cin >> a[i]; BLOCK = sqrt(n) + 1; vector<Q> qs(q);
  for (int i = 0; i < q; i++) { cin >> qs[i].l >> qs[i].r; qs[i].id = i; }
  sort(qs.begin(), qs.end(), [](Q x, Q y) { if (x.l / BLOCK != y.l / BLOCK) return x.l / BLOCK < y.l / BLOCK; return x.r < y.r; });
  vector<int> ans(q); int cl = 0, cr = -1;
  for (auto &qq : qs) { while (cr < qq.r) add(++cr); while (cr > qq.r) rem(cr--); while (cl < qq.l) rem(cl++); while (cl > qq.l) add(--cl); ans[qq.id] = distinct_; }
  for (int x : ans) cout << x << '\\n'; }""",
    "sqrt_decomp": H + """
int a[200005]; long long blk[1000]; int B;
int main(){ int n, q; cin >> n >> q; B = sqrt(n) + 1; for (int i = 0; i < n; i++) { cin >> a[i]; blk[i / B] += a[i]; }
  while (q--) { int l, r; cin >> l >> r; long long s = 0; for (int i = l; i <= r; ) { if (i % B == 0 && i + B - 1 <= r) { s += blk[i / B]; i += B; } else { s += a[i]; i++; } } cout << s << '\\n'; } }""",
    "coordinate_compression": H + """
int main(){ int n; cin >> n; vector<int> a(n); for (auto &x : a) cin >> x; vector<int> v = a;
  sort(v.begin(), v.end()); v.erase(unique(v.begin(), v.end()), v.end());
  for (int i = 0; i < n; i++) a[i] = lower_bound(v.begin(), v.end(), a[i]) - v.begin();
  for (int x : a) cout << x << ' '; }""",
    "meet_in_middle": H + """
int main(){ int n; long long s; cin >> n >> s; vector<long long> a(n); for (auto &x : a) cin >> x; int h = n / 2;
  vector<long long> L, R; for (int m = 0; m < (1 << h); m++) { long long t = 0; for (int i = 0; i < h; i++) if (m & (1 << i)) t += a[i]; L.push_back(t); }
  for (int m = 0; m < (1 << (n - h)); m++) { long long t = 0; for (int i = 0; i < n - h; i++) if (m & (1 << i)) t += a[h + i]; R.push_back(t); }
  sort(R.begin(), R.end()); long long cnt = 0; for (long long x : L) cnt += upper_bound(R.begin(), R.end(), s - x) - lower_bound(R.begin(), R.end(), s - x);
  cout << cnt; }""",
    "simulation": H + """
int dx[4] = {0, 1, 0, -1}, dy[4] = {1, 0, -1, 0};
int main(){ int n, k; cin >> n >> k; int x = 0, y = 0, dir = 0; string ruchy; cin >> ruchy;
  for (char c : ruchy) { if (c == 'P') dir = (dir + 1) % 4; else if (c == 'L') dir = (dir + 3) % 4; else { x += dx[dir]; y += dy[dir]; } }
  cout << x << ' ' << y; }""",
    "math": H + """
int main(){ long long n; cin >> n; bool prime = n >= 2;
  for (long long i = 2; i * i <= n; i++) { if (n % i == 0) { prime = false; break; } }
  cout << (prime ? "TAK" : "NIE"); }""",
    "combinatorics": H + """
const long long MOD = 1e9 + 7; long long C[2005][2005];
int main(){ for (int i = 0; i < 2005; i++) { C[i][0] = 1; for (int j = 1; j <= i; j++) C[i][j] = (C[i-1][j-1] + C[i-1][j]) % MOD; }
  int q; cin >> q; while (q--) { int n, k; cin >> n >> k; cout << C[n][k] << '\\n'; } }""",
    "string_processing": H + """
int main(){ string s; getline(cin, s); int cnt[26] = {0};
  for (int i = 0; i < (int)s.size(); i++) if (isalpha(s[i])) cnt[tolower(s[i]) - 'a']++;
  for (int c = 0; c < 26; c++) if (cnt[c]) cout << char('a' + c) << ' ' << cnt[c] << '\\n'; }""",
    "graph": H + """
vector<int> g[200005];
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  for (int v = 1; v <= n; v++) { cout << g[v].size(); for (int u : g[v]) cout << ' ' << u; cout << '\\n'; } }""",
    "tree": H + """
vector<int> g[200005]; int sz[200005];
void dfs(int v, int p) { sz[v] = 1; for (int u : g[v]) { if (u == p) continue; dfs(u, v); sz[v] += sz[u]; } }
int main(){ int n; cin >> n; for (int i = 0; i < n - 1; i++) { int a, b; cin >> a >> b; g[a].push_back(b); g[b].push_back(a); }
  dfs(1, 0); for (int i = 1; i <= n; i++) cout << sz[i] << ' '; }""",
    "grid": H + """
char plansza[1005][1005]; int dist_[1005][1005]; int dx[4] = {1, -1, 0, 0}, dy[4] = {0, 0, 1, -1};
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < n; i++) for (int j = 0; j < m; j++) cin >> plansza[i][j];
  memset(dist_, -1, sizeof dist_); queue<pair<int,int>> q; q.push({0, 0}); dist_[0][0] = 0;
  while (!q.empty()) { auto [x, y] = q.front(); q.pop(); for (int d = 0; d < 4; d++) { int nx = x + dx[d], ny = y + dy[d];
    if (nx >= 0 && nx < n && ny >= 0 && ny < m && plansza[nx][ny] != '#' && dist_[nx][ny] == -1) { dist_[nx][ny] = dist_[x][y] + 1; q.push({nx, ny}); } } }
  cout << dist_[n-1][m-1]; }""",
}

# Algorytmy, których obecność w danym fragmencie byłaby jawnym błędem (fałszywy alarm).
NOT_EXPECTED = {
    "sorting": {"dijkstra", "segment_tree", "dsu", "kmp", "sieve"},
    "prefix_sums": {"dp", "dijkstra", "segment_tree", "fenwick"},
    "bfs": {"dijkstra", "dp", "topo_sort"},
    "dfs": {"bfs", "dijkstra"},
    "dijkstra": {"bfs", "dp", "topo_sort"},
    "dsu": {"dp", "segment_tree"},
    "fenwick": {"segment_tree", "dp"},
    "sieve": {"dp", "two_pointers"},
    "kmp": {"dijkstra", "dp", "sorting"},
    "fast_pow": {"binary_search", "dp"},
    "recursion": {"dfs", "dp", "segment_tree"},
    "math": {"sieve", "dp", "binary_search"},
    "string_processing": {"kmp", "hashing", "dp"},
    "graph": {"bfs", "dfs", "dijkstra"},
    "two_pointers": {"binary_search", "dp"},
    "binary_search": {"two_pointers", "dp"},
}

SIMPLE_PROGRAMS = [
    H + "int main(){ int a, b; cin >> a >> b; cout << a + b; }",
    H + "int main(){ int n; cin >> n; long long s = 0; for (int i = 0; i < n; i++) { int x; cin >> x; s += x; } cout << s; }",
    H + "int main(){ int n; cin >> n; for (int i = 1; i <= n; i++) { if (i % 15 == 0) cout << \"FizzBuzz\\n\"; "
        "else if (i % 3 == 0) cout << \"Fizz\\n\"; else cout << i << '\\n'; } }",
    H + "int main(){ int n; cin >> n; vector<int> a(n); int mx = INT_MIN; for (auto &x : a) { cin >> x; mx = max(mx, x); } cout << mx; }",
]
ADVANCED = {"dijkstra", "bellman_ford", "floyd", "topo_sort", "dsu", "mst", "lca", "segment_tree", "lazy_segment_tree",
            "fenwick", "sparse_table", "kmp", "z_function", "hashing", "trie", "manacher", "sieve", "matrix_pow",
            "convex_hull", "scc", "bridges", "bipartite", "flow", "matching", "mo", "sqrt_decomp", "meet_in_middle",
            "bitmask_dp", "dp", "bfs", "dfs", "binary_search", "two_pointers"}


class TestSnippets(unittest.TestCase):
    def test_every_id_has_a_snippet(self):
        self.assertEqual(set(SNIPPETS), set(algodetect.IDS))

    def test_detects_each_algorithm(self):
        failures = []
        for aid, code in SNIPPETS.items():
            res = {r["id"]: r for r in algodetect.detect(code)}
            if aid not in res:
                failures.append(f"{aid}: nie wykryto (wykryto {sorted(res)})")
                continue
            r = res[aid]
            self.assertTrue(r["name"] and r["evidence"], aid)
            self.assertTrue(all(isinstance(x, int) for x in r["lines"]), aid)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_main_algorithm_is_confident(self):
        weak = []
        for aid, code in SNIPPETS.items():
            res = {r["id"]: r["confidence"] for r in algodetect.detect(code)}
            if res.get(aid, 0) < 0.5 and aid not in ("greedy", "simulation", "recursion", "math"):
                weak.append(f"{aid}: {res.get(aid)}")
        self.assertEqual(weak, [], "\n".join(weak))

    def test_no_obvious_false_positives(self):
        bad = []
        for aid, forbidden in NOT_EXPECTED.items():
            got = {r["id"] for r in algodetect.detect(SNIPPETS[aid])}
            if got & forbidden:
                bad.append(f"{aid}: {sorted(got & forbidden)}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_simple_programs_have_no_advanced_algorithms(self):
        for code in SIMPLE_PROGRAMS:
            got = {r["id"] for r in algodetect.detect(code)}
            self.assertFalse(got & ADVANCED, f"{got & ADVANCED} w prostym programie")

    def test_variants(self):
        knap = H + """
int dp[100005];
int main(){ int n, W; cin >> n >> W; for (int i = 0; i < n; i++) { int w, v; cin >> w >> v;
  for (int j = W; j >= w; j--) dp[j] = max(dp[j], dp[j - w] + v); } cout << dp[W]; }"""
        r = {x["id"]: x for x in algodetect.detect(knap)}
        self.assertIn("dp", r)
        self.assertIn("plecak", r["dp"].get("variant", ""))
        bs = H + """
bool ok(long long x) { return x * x <= 1000000; }
int main(){ long long lo = 0, hi = 2e9; while (lo < hi) { long long mid = (lo + hi + 1) / 2; if (ok(mid)) lo = mid; else hi = mid - 1; } cout << lo; }"""
        r = {x["id"]: x for x in algodetect.detect(bs)}
        self.assertEqual(r["binary_search"].get("variant"), "po wyniku")

    def test_polish_names_and_student_style(self):
        # styl ucznia: vector jako kolejka + indeks, polskie nazwy — to nadal BFS
        code = H + """
vector<int> kolejka; vector<int> odw; vector<vector<int>> s;
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < n; i++) { vector<int> d; s.push_back(d); odw.push_back(1e9); }
  for (int i = 0; i < m; i++) { int x, y; cin >> x >> y; s[x-1].push_back(y-1); s[y-1].push_back(x-1); }
  kolejka.push_back(0); odw[0] = 0; int akt = 0;
  while (kolejka.size() > akt) { for (int i = 0; i < s[kolejka[akt]].size(); i++) {
      if (odw[s[kolejka[akt]][i]] > odw[kolejka[akt]] + 1) { kolejka.push_back(s[kolejka[akt]][i]); odw[s[kolejka[akt]][i]] = odw[kolejka[akt]] + 1; } }
    akt++; }
  for (int i = 0; i < n; i++) cout << odw[i] << '\\n'; }"""
        got = {r["id"] for r in algodetect.detect(code)}
        self.assertIn("bfs", got)
        self.assertIn("graph", got)
        self.assertNotIn("dp", got)
        self.assertNotIn("dijkstra", got)

    def test_robust_on_garbage(self):
        for code in ["", "   ", "int main(", "}}}{{{", "#include <x>\n" * 3, "for(;;)", "a" * 5000,
                     "int f(int x){ return f(x); }"]:
            self.assertIsInstance(algodetect.detect(code), list)

    def test_result_shape(self):
        r = algodetect.detect(SNIPPETS["dijkstra"])[0]
        for k in ("id", "name", "confidence", "lines", "evidence"):
            self.assertIn(k, r)
        self.assertTrue(0 < r["confidence"] <= 1)
        self.assertIn(r["id"], algodetect.NAMES)


@unittest.skipUnless(PROGRAMS.exists() and algodetect.HAVE_CALIBRATION,
                     "brak programów albo tabel kalibracji autora (dane prywatne)")
class TestCalibration(unittest.TestCase):
    def test_precision_recall_on_student_programs(self):
        res = algodetect.evaluate()
        per = res["per_algorithm"]
        self.assertGreater(res["programs"], 100)
        # główne tematy kółek: wysokie precision i recall po weryfikacji etykiet
        for aid in ("dijkstra", "segment_tree", "dsu", "sorting", "tree", "graph", "topo_sort", "binary_search"):
            if aid in per and per[aid]["support"] >= 3:
                self.assertGreaterEqual(per[aid]["recall"], 0.8, aid)
                self.assertGreaterEqual(per[aid]["precision"] or 0, 0.8, aid)
        total_fp = sum(v["fp"] for v in per.values())
        total_det = sum(v["fp"] + v["tp"] for v in per.values())
        self.assertLess(total_fp / max(total_det, 1), 0.1)


if __name__ == "__main__":
    unittest.main()

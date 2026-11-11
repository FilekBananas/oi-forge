"""Zestaw pomiarowy dla analizy złożoności (`tools.complexity`) i optymalizatora (`tools.optimizer`).

Programy są napisane ręcznie (typowe wzorce z OI / kółek), NIE pochodzą z prywatnych programów ucznia.

Uruchomienie pomiaru:
    python3 -m tests.bench_cx_opt                 # tabela trafności (złożoność + optymalizator, z kompilacją przepisań)
    python3 -m tests.bench_cx_opt --no-compile    # bez kompilowania przepisanego kodu
    python3 -m tests.bench_cx_opt --pkg KATALOG   # pomiar innej wersji pakietu `oiforge` (np. kopii sprzed zmian)
    python3 -m tests.bench_cx_opt --json plik.json

Złożoność: `expect` = lista akceptowanych wyrażeń O(...) (porównanie odporne na kolejność czynników i składników).
Optymalizator: `top` = reguła, która powinna być na górze listy; `rewrite` = czy oczekujemy automatycznego przepisania;
`gen(rng)` = generator małych losowych wejść do sprawdzenia, że przepisany kod daje ten sam wynik.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("OIFORGE_NO_WARMUP", "1")

H = "#include <bits/stdc++.h>\nusing namespace std;\n"
CONS = {"n": 200000, "m": 200000, "q": 200000}


# ============================================================================================ złożoność
def _cx(name, code, expect, cons=None, tag=""):
    return {"name": name, "code": H + code.strip("\n") + "\n", "expect": expect if isinstance(expect, list) else [expect],
            "cons": cons or CONS, "tag": tag}


CX_CASES = [
    _cx("DSU (kompresja + ranga), q zapytań", """
int p[200005], r_[200005];
int find(int x){ return p[x]==x ? x : p[x]=find(p[x]); }
void unite(int a,int b){ a=find(a); b=find(b); if(a==b) return; if(r_[a]<r_[b]) swap(a,b); p[b]=a; if(r_[a]==r_[b]) r_[a]++; }
int main(){ int n,q; cin>>n>>q; for(int i=1;i<=n;i++) p[i]=i;
  while(q--){ int t,a,b; cin>>t>>a>>b; if(t==1) unite(a,b); else cout<<(find(a)==find(b)?"TAK":"NIE")<<"\\n"; } }
""", ["n + q·α(n)", "n + q log n"], tag="dsu"),
    _cx("DSU tylko z kompresją, m krawędzi", """
int p[200005];
int f(int x){ if(p[x]==x) return x; return p[x]=f(p[x]); }
int main(){ int n,m; cin>>n>>m; for(int i=1;i<=n;i++) p[i]=i; int comp=n;
  for(int i=0;i<m;i++){ int a,b; cin>>a>>b; a=f(a); b=f(b); if(a!=b){ p[a]=b; comp--; } }
  cout<<comp<<"\\n"; }
""", ["n + m log n", "n + m·α(n)"], tag="dsu"),
    _cx("BFS po planszy n×m", """
int main(){ int n,m; cin>>n>>m; vector<string> g(n); for(auto&s:g) cin>>s;
  vector<vector<int>> d(n, vector<int>(m,-1)); queue<pair<int,int>> q; d[0][0]=0; q.push({0,0});
  int dx[]={1,-1,0,0}, dy[]={0,0,1,-1};
  while(!q.empty()){ auto [x,y]=q.front(); q.pop();
    for(int k=0;k<4;k++){ int nx=x+dx[k], ny=y+dy[k];
      if(nx<0||ny<0||nx>=n||ny>=m||g[nx][ny]=='#'||d[nx][ny]!=-1) continue;
      d[nx][ny]=d[x][y]+1; q.push({nx,ny}); } }
  cout<<d[n-1][m-1]<<"\\n"; }
""", ["n·m"], {"n": 2000, "m": 2000}, tag="bfs"),
    _cx("BFS po planszy (tablica odwiedzin bool, pary w kolejce)", """
char g[1005][1005]; bool vis[1005][1005];
int main(){ int n,m; cin>>n>>m; for(int i=0;i<n;i++) cin>>g[i];
  queue<pair<int,int>> q; q.push({0,0}); vis[0][0]=true; int cnt=0;
  while(!q.empty()){ int x=q.front().first, y=q.front().second; q.pop(); cnt++;
    const int dx[4]={1,-1,0,0}, dy[4]={0,0,1,-1};
    for(int k=0;k<4;k++){ int nx=x+dx[k], ny=y+dy[k];
      if(nx>=0&&ny>=0&&nx<n&&ny<m&&!vis[nx][ny]&&g[nx][ny]=='.'){ vis[nx][ny]=true; q.push({nx,ny}); } } }
  cout<<cnt<<"\\n"; }
""", ["n·m"], {"n": 1000, "m": 1000}, tag="bfs"),
    _cx("NWD iteracyjnie", """
long long nwd(long long a, long long b){ while(b){ long long t=a%b; a=b; b=t; } return a; }
int main(){ long long a,b; cin>>a>>b; cout<<nwd(a,b)<<"\\n"; }
""", ["log min(a, b)", "log b", "log a", "log V"], {"a": 1e18, "b": 1e18}, tag="gcd"),
    _cx("NWD rekurencyjnie w pętli n", """
long long nwd(long long a, long long b){ return b==0 ? a : nwd(b, a%b); }
int main(){ int n; cin>>n; long long g=0; for(int i=0;i<n;i++){ long long x; cin>>x; g=nwd(g,x);} cout<<g<<"\\n"; }
""", ["n log V", "n + log V", "n log x"], tag="gcd"),
    _cx("sumy przedziałów liczone pętlą (wolne)", """
int main(){
    int n, q; cin >> n >> q;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    while (q--) {
        int l, r; cin >> l >> r;
        long long s = 0;
        for (int i = l; i <= r; i++) s += a[i];
        cout << s << endl;
    }
}
""", ["n·q"], tag="queries"),
    _cx("sumy prefiksowe + while(q--) ze stałym wnętrzem", """
long long p[200005];
int main(){ int n,q; cin>>n>>q; for(int i=1;i<=n;i++){ long long x; cin>>x; p[i]=p[i-1]+x; }
  while(q--){ int l,r; cin>>l>>r; cout<<p[r]-p[l-1]<<"\\n"; } }
""", ["n + q"], tag="queries"),
    _cx("set: wstawianie wartości z zapytań", """
int main(){ int q; cin>>q; set<long long> s; while(q--){ long long x; cin>>x; s.insert(x); auto it=s.lower_bound(x); cout<<*it<<"\\n"; } }
""", ["q log q"], tag="stl"),
    _cx("binsearch while(l<r) w pętli zapytań", """
int main(){ int n,q; cin>>n>>q; vector<int> a(n); for(auto&x:a) cin>>x; sort(a.begin(),a.end());
  while(q--){ int x; cin>>x; int l=0, r=n; while(l<r){ int mid=(l+r)/2; if(a[mid]<x) l=mid+1; else r=mid; } cout<<l<<"\\n"; } }
""", ["n log n + q log n"], tag="binsearch"),
    _cx("binsearch po wyniku (hi = 1e18) z liniowym sprawdzeniem", """
int n; long long k; long long a[200005];
bool ok(long long t){ long long c=0; for(int i=0;i<n;i++){ c+=t/a[i]; if(c>=k) return true; } return c>=k; }
int main(){ cin>>n>>k; for(int i=0;i<n;i++) cin>>a[i];
  long long lo=0, hi=1000000000000000000LL;
  while(lo<hi){ long long mid=lo+(hi-lo)/2; if(ok(mid)) hi=mid; else lo=mid+1; }
  cout<<lo<<"\\n"; }
""", ["n log C"], tag="binsearch"),
    _cx("binsearch rekurencyjny", """
int a[200005];
int bs(int l, int r, int x){ if(l>r) return -1; int m=(l+r)/2; if(a[m]==x) return m; if(a[m]<x) return bs(m+1,r,x); return bs(l,m-1,x); }
int main(){ int n,x; cin>>n>>x; for(int i=0;i<n;i++) cin>>a[i]; cout<<bs(0,n-1,x)<<"\\n"; }
""", ["n"], tag="binsearch"),
    _cx("połowienie while(n>0) n/=2", """
int main(){ long long n; cin>>n; int c=0; while(n>0){ n/=2; c++; } cout<<c<<"\\n"; }
""", ["log n"], {"n": 1e18}, tag="log"),
    _cx("podwajanie for(i=1;i<n;i*=2) w pętli n", """
int main(){ int n; cin>>n; long long s=0; for(int j=0;j<n;j++) for(int i=1;i<n;i*=2) s+=i^j; cout<<s<<"\\n"; }
""", ["n log n"], tag="log"),
    _cx("dwa wskaźniki: for + while z niecofanym j", """
long long a[200005];
int main(){ int n; long long K; cin>>n>>K; for(int i=0;i<n;i++) cin>>a[i];
  int j=0; long long s=0; int best=0;
  for(int i=0;i<n;i++){ while(j<n && s+a[j]<=K){ s+=a[j]; j++; } best=max(best,j-i); if(j>i) s-=a[i]; else j=i+1; }
  cout<<best<<"\\n"; }
""", ["n"], tag="two_pointers"),
    _cx("dwa wskaźniki z dwóch końców (posortowana tablica)", """
int main(){ int n; long long x; cin>>n>>x; vector<long long> a(n); for(auto&v:a) cin>>v; sort(a.begin(),a.end());
  int j=n-1; long long cnt=0;
  for(int i=0;i<n;i++){ while(j>=0 && a[i]+a[j]>x) j--; if(j>i) cnt+=j-i; }
  cout<<cnt<<"\\n"; }
""", ["n log n"], tag="two_pointers"),
    _cx("sito Eratostenesa", """
bool z[10000005];
int main(){ int n; cin>>n; int c=0; for(int i=2;i<=n;i++){ if(z[i]) continue; c++; for(long long j=1LL*i*i;j<=n;j+=i) z[j]=1; } cout<<c<<"\\n"; }
""", ["n log log n"], {"n": 10000000}, tag="sieve"),
    _cx("pętla harmoniczna j+=i", """
int d[1000005];
int main(){ int n; cin>>n; for(int i=1;i<=n;i++) for(int j=i;j<=n;j+=i) d[j]++; long long s=0; for(int i=1;i<=n;i++) s+=d[i]; cout<<s<<"\\n"; }
""", ["n log n"], {"n": 1000000}, tag="harmonic"),
    _cx("Dijkstra z priority_queue", """
vector<pair<int,int>> g[200005]; long long dist[200005];
int main(){ int n,m; cin>>n>>m; for(int i=0;i<m;i++){ int a,b,w; cin>>a>>b>>w; g[a].push_back({b,w}); g[b].push_back({a,w}); }
  for(int i=1;i<=n;i++) dist[i]=LLONG_MAX; dist[1]=0;
  priority_queue<pair<long long,int>, vector<pair<long long,int>>, greater<>> pq; pq.push({0,1});
  while(!pq.empty()){ auto [d,v]=pq.top(); pq.pop(); if(d>dist[v]) continue;
    for(auto [u,w]: g[v]) if(dist[v]+w<dist[u]){ dist[u]=dist[v]+w; pq.push({dist[u],u}); } }
  for(int i=1;i<=n;i++) cout<<dist[i]<<"\\n"; }
""", ["n log n + m log n", "n + m log n", "n + m log m", "n log n + m log m", "(n + m) log n"], tag="graph"),
    _cx("DFS rekurencyjny na liście sąsiedztwa", """
vector<int> g[200005]; bool vis[200005];
void dfs(int v){ vis[v]=true; for(int u: g[v]) if(!vis[u]) dfs(u); }
int main(){ int n,m; cin>>n>>m; for(int i=0;i<m;i++){ int a,b; cin>>a>>b; g[a].push_back(b); g[b].push_back(a); }
  int c=0; for(int v=1;v<=n;v++) if(!vis[v]){ c++; dfs(v); } cout<<c<<"\\n"; }
""", ["n + m"], tag="graph"),
    _cx("BFS na liście sąsiedztwa (vector<vector<int>>)", """
int main(){ int n,m; cin>>n>>m; vector<vector<int>> g(n+1); for(int i=0;i<m;i++){ int a,b; cin>>a>>b; g[a].push_back(b); g[b].push_back(a); }
  vector<int> d(n+1,-1); queue<int> q; q.push(1); d[1]=0;
  while(!q.empty()){ int v=q.front(); q.pop(); for(int u: g[v]) if(d[u]<0){ d[u]=d[v]+1; q.push(u); } }
  for(int v=1;v<=n;v++) cout<<d[v]<<" "; }
""", ["n + m"], tag="graph"),
    _cx("sortowanie topologiczne (Kahn)", """
vector<int> g[200005]; int indeg[200005];
int main(){ int n,m; cin>>n>>m; for(int i=0;i<m;i++){ int a,b; cin>>a>>b; g[a].push_back(b); indeg[b]++; }
  queue<int> q; for(int v=1;v<=n;v++) if(!indeg[v]) q.push(v); vector<int> order;
  while(!q.empty()){ int v=q.front(); q.pop(); order.push_back(v); for(int u: g[v]) if(--indeg[u]==0) q.push(u); }
  for(int v: order) cout<<v<<" "; }
""", ["n + m"], tag="graph"),
    _cx("Kruskal: sort krawędzi + DSU", """
int p[200005];
int fnd(int x){ return p[x]==x?x:p[x]=fnd(p[x]); }
int main(){ int n,m; cin>>n>>m; vector<array<int,3>> e(m); for(auto& [w,a,b]: e) cin>>a>>b>>w;
  sort(e.begin(), e.end()); for(int i=1;i<=n;i++) p[i]=i; long long res=0;
  for(auto [w,a,b]: e){ a=fnd(a); b=fnd(b); if(a!=b){ p[a]=b; res+=w; } }
  cout<<res<<"\\n"; }
""", ["n + m log m", "m log m + n", "n + m log n"], tag="graph"),
    _cx("memo DP (plecak top-down)", """
int n, W; int w[105], v[105]; long long memo[105][100005]; bool done_[105][100005];
long long go(int i, int c){ if(i==n) return 0; if(done_[i][c]) return memo[i][c]; done_[i][c]=true;
  long long r=go(i+1,c); if(w[i]<=c) r=max(r, go(i+1,c-w[i])+v[i]); return memo[i][c]=r; }
int main(){ cin>>n>>W; for(int i=0;i<n;i++) cin>>w[i]>>v[i]; cout<<go(0,W)<<"\\n"; }
""", ["n·W"], {"n": 100, "W": 100000}, tag="dp"),
    _cx("memo DP z pętlą przejść (dp[i][j] = max po t<k)", """
long long dp[5005][105]; int n,k;
long long f(int i,int j){ if(i==n) return 0; if(dp[i][j]!=-1) return dp[i][j]; long long r=0; for(int t=0;t<k;t++) r=max(r,f(i+1,t)+t*j); return dp[i][j]=r; }
int main(){ cin>>n>>k; memset(dp,-1,sizeof dp); cout<<f(0,0)<<"\\n"; }
""", ["n·k²"], {"n": 5000, "k": 100}, tag="dp"),
    _cx("LCS — DP po dwóch napisach", """
int dp[5005][5005];
int main(){ string s,t; cin>>s>>t; int n=s.size(), m=t.size();
  for(int i=1;i<=n;i++) for(int j=1;j<=m;j++) dp[i][j] = s[i-1]==t[j-1] ? dp[i-1][j-1]+1 : max(dp[i-1][j], dp[i][j-1]);
  cout<<dp[n][m]<<"\\n"; }
""", ["|s|·|t|", "n·m"], {"|s|": 5000, "|t|": 5000}, tag="dp"),
    _cx("plecak 0/1 bottom-up", """
long long dp[100005];
int main(){ int n,W; cin>>n>>W; for(int i=0;i<n;i++){ int w,v; cin>>w>>v; for(int j=W;j>=w;j--) dp[j]=max(dp[j],dp[j-w]+v); } cout<<dp[W]<<"\\n"; }
""", ["n·W"], {"n": 100, "W": 100000}, tag="dp"),
    _cx("DP po maskach bitowych", """
int c[20][20]; int dp[1<<20];
int main(){ int n; cin>>n; for(int i=0;i<n;i++) for(int j=0;j<n;j++) cin>>c[i][j];
  for(int mask=1;mask<(1<<n);mask++){ int i=__builtin_popcount(mask)-1; dp[mask]=INT_MAX;
    for(int j=0;j<n;j++) if(mask>>j&1) dp[mask]=min(dp[mask], dp[mask^(1<<j)]+c[i][j]); }
  cout<<dp[(1<<n)-1]<<"\\n"; }
""", ["2^n · n", "2^n·n"], {"n": 20}, tag="dp"),
    _cx("map w pętli (zliczanie)", """
int main(){ int n; cin>>n; map<int,int> cnt; for(int i=0;i<n;i++){ int x; cin>>x; cnt[x]++; } int best=0; for(auto [k,v]: cnt) best=max(best,v); cout<<best<<"\\n"; }
""", ["n log n"], tag="stl"),
    _cx("unordered_set w pętli", """
int main(){ int n; cin>>n; unordered_set<long long> s; for(int i=0;i<n;i++){ long long x; cin>>x; s.insert(x); } cout<<s.size()<<"\\n"; }
""", ["n"], tag="stl"),
    _cx("sort w pętli zapytań (kopiowanie i sortowanie)", """
int main(){ int n,q; cin>>n>>q; vector<int> a(n); for(auto&x:a) cin>>x;
  while(q--){ int k; cin>>k; vector<int> b=a; sort(b.begin(), b.end()); cout<<b[k-1]<<"\\n"; } }
""", ["n·q log n"], tag="stl"),
    _cx("sortowanie przez scalanie", """
vector<int> a, t;
void ms(int l,int r){ if(r-l<=1) return; int m=(l+r)/2; ms(l,m); ms(m,r); int i=l,j=m,k=l;
  while(i<m||j<r){ if(j>=r||(i<m&&a[i]<=a[j])) t[k++]=a[i++]; else t[k++]=a[j++]; } for(int x=l;x<r;x++) a[x]=t[x]; }
int main(){ int n; cin>>n; a.resize(n); t.resize(n); for(auto&x:a) cin>>x; ms(0,n); for(int x: a) cout<<x<<" "; }
""", ["n log n"], tag="dc"),
    _cx("dziel i zwyciężaj z pracą O(1)", """
int a[200005];
int mx(int l,int r){ if(l==r) return a[l]; int m=(l+r)/2; return max(mx(l,m), mx(m+1,r)); }
int main(){ int n; cin>>n; for(int i=0;i<n;i++) cin>>a[i]; cout<<mx(0,n-1)<<"\\n"; }
""", ["n"], tag="dc"),
    _cx("drzewo przedziałowe rekurencyjne, q zapytań", """
int n; long long t[800005];
void upd(int v,int l,int r,int p,long long x){ if(l==r){ t[v]=x; return; } int m=(l+r)/2; if(p<=m) upd(2*v,l,m,p,x); else upd(2*v+1,m+1,r,p,x); t[v]=t[2*v]+t[2*v+1]; }
long long qry(int v,int l,int r,int ql,int qr){ if(qr<l||r<ql) return 0; if(ql<=l&&r<=qr) return t[v]; int m=(l+r)/2; return qry(2*v,l,m,ql,qr)+qry(2*v+1,m+1,r,ql,qr); }
int main(){ int q; cin>>n>>q; while(q--){ int ty,a,b; cin>>ty>>a>>b; if(ty==1) upd(1,0,n-1,a,b); else cout<<qry(1,0,n-1,a,b)<<"\\n"; } }
""", ["q log n"], tag="ds"),
    _cx("drzewo Fenwicka", """
int n; long long f[200005];
void add(int i,long long x){ for(;i<=n;i+=i&-i) f[i]+=x; }
long long sum(int i){ long long s=0; for(;i>0;i-=i&-i) s+=f[i]; return s; }
int main(){ int q; cin>>n>>q; for(int i=1;i<=n;i++){ long long x; cin>>x; add(i,x); }
  while(q--){ int t,a,b; cin>>t>>a>>b; if(t==1) add(a,b); else cout<<sum(b)-sum(a-1)<<"\\n"; } }
""", ["n log n + q log n"], tag="ds"),
    _cx("stos monotoniczny", """
int a[200005], L[200005];
int main(){ int n; cin>>n; for(int i=0;i<n;i++) cin>>a[i]; stack<int> st;
  for(int i=0;i<n;i++){ while(!st.empty() && a[st.top()]>=a[i]) st.pop(); L[i]= st.empty()? -1 : st.top(); st.push(i); }
  for(int i=0;i<n;i++) cout<<L[i]<<" "; }
""", ["n"], tag="amort"),
    _cx("okno przesuwne: minimum w deque", """
int a[200005];
int main(){ int n,k; cin>>n>>k; for(int i=0;i<n;i++) cin>>a[i]; deque<int> dq;
  for(int i=0;i<n;i++){ while(!dq.empty() && a[dq.back()]>=a[i]) dq.pop_back(); dq.push_back(i);
    if(dq.front()<=i-k) dq.pop_front(); if(i>=k-1) cout<<a[dq.front()]<<" "; } }
""", ["n"], tag="amort"),
    _cx("funkcja prefiksowa (KMP)", """
int main(){ string s; cin>>s; int n=s.size(); vector<int> pi(n,0);
  for(int i=1;i<n;i++){ int k=pi[i-1]; while(k>0 && s[i]!=s[k]) k=pi[k-1]; if(s[i]==s[k]) k++; pi[i]=k; }
  for(int x: pi) cout<<x<<" "; }
""", ["|s|"], {"|s|": 1000000}, tag="amort"),
    _cx("Floyd–Warshall", """
long long d[505][505];
int main(){ int n; cin>>n; for(int i=0;i<n;i++) for(int j=0;j<n;j++) cin>>d[i][j];
  for(int k=0;k<n;k++) for(int i=0;i<n;i++) for(int j=0;j<n;j++) d[i][j]=min(d[i][j],d[i][k]+d[k][j]);
  cout<<d[0][n-1]<<"\\n"; }
""", ["n³"], {"n": 500}, tag="nested"),
    _cx("sortowanie bąbelkowe", """
int a[5005];
int main(){ int n; cin>>n; for(int i=0;i<n;i++) cin>>a[i];
  for(int i=0;i<n;i++) for(int j=0;j+1<n-i;j++) if(a[j]>a[j+1]) swap(a[j],a[j+1]);
  for(int i=0;i<n;i++) cout<<a[i]<<" "; }
""", ["n²"], {"n": 5000}, tag="nested"),
    _cx("wczytywanie planszy n×m i suma", """
int main(){ int n,m; cin>>n>>m; long long s=0; for(int i=0;i<n;i++) for(int j=0;j<m;j++){ int x; cin>>x; s+=x; } cout<<s<<"\\n"; }
""", ["n·m"], {"n": 1000, "m": 1000}, tag="nested"),
    _cx("szybkie potęgowanie w pętli zapytań", """
const long long MOD=1000000007;
long long pw(long long a,long long b){ long long r=1; a%=MOD; while(b>0){ if(b&1) r=r*a%MOD; a=a*a%MOD; b>>=1; } return r; }
int main(){ int q; cin>>q; while(q--){ long long a,b; cin>>a>>b; cout<<pw(a,b)<<"\\n"; } }
""", ["q log b", "q log V"], {"q": 200000, "b": 1e18}, tag="log"),
    _cx("permutacje next_permutation", """
int main(){ int n; cin>>n; vector<int> p(n); iota(p.begin(),p.end(),0); long long best=0;
  do { long long s=0; for(int i=0;i<n;i++) s+=1LL*(i+1)*p[i]; best=max(best,s); } while(next_permutation(p.begin(),p.end()));
  cout<<best<<"\\n"; }
""", ["n! · n", "n!·n"], {"n": 10}, tag="exp"),
    _cx("podzbiory rekurencyjnie (2^n)", """
int n; long long a[25]; long long K; int cnt=0;
void rec(int i,long long s){ if(i==n){ if(s==K) cnt++; return; } rec(i+1,s); rec(i+1,s+a[i]); }
int main(){ cin>>n>>K; for(int i=0;i<n;i++) cin>>a[i]; rec(0,0); cout<<cnt<<"\\n"; }
""", ["2^n"], {"n": 20}, tag="exp"),
    _cx("Fibonacci bez spamiętywania", """
long long fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); }
int main(){ int n; cin>>n; cout<<fib(n)<<"\\n"; }
""", ["1.62^n", "1.618^n"], {"n": 40}, tag="exp"),
    _cx("pierwiastkowo: dzielniki liczby", """
int main(){ long long n; cin>>n; int c=0; for(long long d=1; d*d<=n; d++) if(n%d==0) c+= (d*d==n)?1:2; cout<<c<<"\\n"; }
""", ["√n"], {"n": 1e12}, tag="sqrt"),
    _cx("przegląd napisu i licznik liter", """
int main(){ string s; cin>>s; int c[26]={0}; for(int i=0;i<(int)s.size();i++) c[s[i]-'a']++; int best=0; for(int i=0;i<26;i++) best=max(best,c[i]); cout<<best<<"\\n"; }
""", ["|s|"], {"|s|": 1000000}, tag="string"),
    _cx("wiele zestawów testowych (t) z sortowaniem", """
int main(){ int t; cin>>t; while(t--){ int n; cin>>n; vector<int> a(n); for(auto&x:a) cin>>x; sort(a.begin(),a.end()); cout<<a[n/2]<<"\\n"; } }
""", ["t·n log n", "n log n"], {"n": 200000, "t": 1}, tag="tests"),
    _cx("liniowe szukanie w pętli zapytań", """
int a[200005];
int main(){ int n,q; cin>>n>>q; for(int i=0;i<n;i++) cin>>a[i];
  while(q--){ int x; cin>>x; bool f=false; for(int i=0;i<n;i++) if(a[i]==x){ f=true; break; } cout<<(f?"TAK":"NIE")<<"\\n"; } }
""", ["n·q"], tag="queries"),
]


# Zestaw kontrolny: warianty w innym stylu, dopisane po poprawkach analizatora (bez dostrajania pod nie) — mierzy,
# czy reguły uogólniają się poza przykłady, na których je pisano.
CX_CONTROL = [
    _cx("[k] suma przedziału w funkcji wołanej w zapytaniach", """
int n; long long a[200005];
long long sum(int l, int r){ long long s=0; for(int i=l;i<=r;i++) s+=a[i]; return s; }
int main(){ int q; cin>>n>>q; for(int i=0;i<n;i++) cin>>a[i]; while(q--){ int l,r; cin>>l>>r; cout<<sum(l,r)<<"\\n"; } }
""", ["n·q"], tag="kontrola"),
    _cx("[k] BFS po planszy h×w (memset, array<int,2>)", """
char g[1005][1005]; int dist_[1005][1005];
int main(){ int h,w; cin>>h>>w; for(int i=0;i<h;i++) cin>>g[i]; memset(dist_,-1,sizeof dist_);
  queue<array<int,2>> q; q.push({0,0}); dist_[0][0]=0; int dr[]={-1,1,0,0}, dc[]={0,0,-1,1};
  while(!q.empty()){ auto cur=q.front(); q.pop(); int r=cur[0], c=cur[1];
    for(int d=0; d<4; d++){ int nr=r+dr[d], nc=c+dc[d];
      if(nr<0||nr>=h||nc<0||nc>=w) continue; if(g[nr][nc]=='#'||dist_[nr][nc]!=-1) continue;
      dist_[nr][nc]=dist_[r][c]+1; q.push({nr,nc}); } }
  cout<<dist_[h-1][w-1]<<"\\n"; }
""", ["h·w"], {"h": 1000, "w": 1000}, tag="kontrola"),
    _cx("[k] __gcd w pętli", """
int main(){ int n; cin>>n; long long g=0; for(int i=0;i<n;i++){ long long x; cin>>x; g=__gcd(g,x); } cout<<g<<"\\n"; }
""", ["n log V", "n + log V"], tag="kontrola"),
    _cx("[k] DSU iteracyjny find + łączenie wg rozmiaru", """
int par[200005], sz[200005];
int fnd(int x){ while(par[x]!=x) x=par[x]; return x; }
int main(){ int n,q; cin>>n>>q; for(int i=1;i<=n;i++){ par[i]=i; sz[i]=1; }
  while(q--){ int a,b; cin>>a>>b; a=fnd(a); b=fnd(b); if(a==b) continue; if(sz[a]<sz[b]) swap(a,b); par[b]=a; sz[a]+=sz[b]; } }
""", ["n + q log n"], tag="kontrola"),
    _cx("[k] Dijkstra na secie", """
vector<pair<int,int>> adj[100005]; long long d[100005];
int main(){ int n,m; cin>>n>>m; for(int i=0;i<m;i++){ int a,b,c; cin>>a>>b>>c; adj[a].push_back({b,c}); }
  for(int i=1;i<=n;i++) d[i]=1e18; d[1]=0; set<pair<long long,int>> s; s.insert({0,1});
  while(!s.empty()){ int v=s.begin()->second; s.erase(s.begin());
    for(auto [u,w]: adj[v]) if(d[v]+w<d[u]){ s.erase({d[u],u}); d[u]=d[v]+w; s.insert({d[u],u}); } }
  cout<<d[n]<<"\\n"; }
""", ["n log n + m log n", "(n + m) log n", "n + m log n"], tag="kontrola"),
    _cx("[k] binsearch lo<=hi z >>1 w zapytaniach", """
int main(){ int n,q; cin>>n>>q; vector<long long> a(n); for(auto&x:a) cin>>x; sort(a.begin(),a.end());
  while(q--){ long long x; cin>>x; int lo=0, hi=n-1, ans=-1;
    while(lo<=hi){ int mid=(lo+hi)>>1; if(a[mid]>=x){ ans=mid; hi=mid-1; } else lo=mid+1; }
    cout<<ans<<"\\n"; } }
""", ["n log n + q log n"], tag="kontrola"),
    _cx("[k] i <<= 1", """
int main(){ long long n; cin>>n; int c=0; for(long long i=1;i<=n;i<<=1) c++; cout<<c<<"\\n"; }
""", ["log n"], {"n": 1e18}, tag="kontrola"),
    _cx("[k] suma cyfr n liczb", """
int main(){ int n; cin>>n; long long tot=0; for(int i=0;i<n;i++){ long long x; cin>>x; while(x>0){ tot+=x%10; x/=10; } } cout<<tot<<"\\n"; }
""", ["n log x", "n log V"], tag="kontrola"),
    _cx("[k] gąsienica while(r<n) z wewnętrznym while", """
int a[200005];
int main(){ int n; long long k; cin>>n>>k; for(int i=0;i<n;i++) cin>>a[i];
  int l=0, r=0; long long sum=0; int best=0;
  while(r<n){ sum+=a[r]; r++; while(sum>k){ sum-=a[l]; l++; } best=max(best,r-l); }
  cout<<best<<"\\n"; }
""", ["n"], tag="kontrola"),
    _cx("[k] sito od 2*i", """
bool comp[5000005];
int main(){ int n; cin>>n; int cnt=0; for(int i=2;i<=n;i++){ if(!comp[i]){ cnt++; for(int j=2*i;j<=n;j+=i) comp[j]=true; } } cout<<cnt<<"\\n"; }
""", ["n log log n"], {"n": 5000000}, tag="kontrola"),
    _cx("[k] suma dzielników (harmonicznie)", """
long long sd[1000005];
int main(){ int n; cin>>n; for(int d=1; d<=n; d++) for(int k=d; k<=n; k+=d) sd[k]+=d; cout<<sd[n]<<"\\n"; }
""", ["n log n"], {"n": 1000000}, tag="kontrola"),
    _cx("[k] memo: liczba ścieżek w siatce (vector<vector>)", """
int n,m; vector<vector<long long>> dp; vector<string> g;
long long go(int i,int j){ if(i>=n||j>=m||g[i][j]=='#') return 0; if(i==n-1&&j==m-1) return 1;
  if(dp[i][j]!=-1) return dp[i][j]; return dp[i][j]=(go(i+1,j)+go(i,j+1))%1000000007; }
int main(){ cin>>n>>m; g.resize(n); for(auto&s:g) cin>>s; dp.assign(n, vector<long long>(m,-1)); cout<<go(0,0)<<"\\n"; }
""", ["n·m"], {"n": 2000, "m": 2000}, tag="kontrola"),
    _cx("[k] LIS z lower_bound", """
int main(){ int n; cin>>n; vector<int> t; for(int i=0;i<n;i++){ int x; cin>>x; auto it=lower_bound(t.begin(),t.end(),x); if(it==t.end()) t.push_back(x); else *it=x; } cout<<t.size()<<"\\n"; }
""", ["n log n"], tag="kontrola"),
    _cx("[k] LIS kwadratowo", """
int a[5005], f[5005];
int main(){ int n; cin>>n; for(int i=0;i<n;i++) cin>>a[i]; int best=0;
  for(int i=0;i<n;i++){ f[i]=1; for(int j=0;j<i;j++) if(a[j]<a[i]) f[i]=max(f[i],f[j]+1); best=max(best,f[i]); }
  cout<<best<<"\\n"; }
""", ["n²"], {"n": 5000}, tag="kontrola"),
    _cx("[k] okno z multisetem", """
int a[200005];
int main(){ int n,k; cin>>n>>k; for(int i=0;i<n;i++) cin>>a[i]; multiset<int> w;
  for(int i=0;i<n;i++){ w.insert(a[i]); if(i>=k) w.erase(w.find(a[i-k])); if(i>=k-1) cout<<*w.rbegin()<<" "; } }
""", ["n log n", "n log k"], tag="kontrola"),
    _cx("[k] palindromy: rozszerzanie od środka", """
int main(){ string s; cin>>s; int n=s.size(); long long cnt=0;
  for(int c=0;c<n;c++){ int l=c, r=c; while(l>=0 && r<n && s[l]==s[r]){ cnt++; l--; r++; } }
  cout<<cnt<<"\\n"; }
""", ["|s|²", "n²"], {"|s|": 5000}, tag="kontrola"),
    _cx("[k] kopiec: n wstawień i k zdjęć", """
int main(){ int n,k; cin>>n>>k; priority_queue<long long> pq; for(int i=0;i<n;i++){ long long x; cin>>x; pq.push(x); }
  long long s=0; for(int i=0;i<k && !pq.empty();i++){ s+=pq.top(); pq.pop(); } cout<<s<<"\\n"; }
""", ["n log n + k log n"], tag="kontrola"),
    _cx("[k] potęgowanie macierzy 2×2", """
typedef array<array<long long,2>,2> M; const long long MOD=1000000007;
M mul(M a, M b){ M c{}; for(int i=0;i<2;i++) for(int j=0;j<2;j++) for(int k=0;k<2;k++) c[i][j]=(c[i][j]+a[i][k]*b[k][j])%MOD; return c; }
int main(){ long long k; cin>>k; M r{}; r[0][0]=r[1][1]=1; M A{}; A[0][0]=A[0][1]=A[1][0]=1;
  while(k){ if(k&1) r=mul(r,A); A=mul(A,A); k>>=1; } cout<<r[0][1]<<"\\n"; }
""", ["log k"], {"k": 1e18}, tag="kontrola"),
]


def _norm_term(t: str) -> str:
    t = t.strip().replace(" · ", "·").replace("(", "").replace(")", "")
    m = re.search(r"\blog", t)
    poly, logs = (t[:m.start()], t[m.start():]) if m else (t, "")
    pf = sorted(x.strip() for x in re.split(r"·|\s+", poly) if x.strip())
    return "·".join(pf) + ("|" + re.sub(r"\s+", " ", logs.strip()) if logs else "")


def norm_expr(e: str) -> str:
    e = (e or "").strip()
    if e.startswith("O(") and e.endswith(")"):
        e = e[2:-1]
    m = re.fullmatch(r"\(([^()]*)\)\s*·?\s*(log [^+]+|α\(n\))", e)
    if m:  # (n + m) log n  →  n log n + m log n
        e = " + ".join(f"{x.strip()} {m.group(2)}" if m.group(2).startswith("log") else f"{x.strip()}·{m.group(2)}"
                       for x in m.group(1).split("+"))
    parts = [p for p in re.split(r"\s\+\s", e) if p.strip()]
    return " + ".join(sorted(_norm_term(p) for p in parts))


def expr_ok(got: str, accepted: list) -> bool:
    g = norm_expr(got)
    return any(g == norm_expr(a) for a in accepted)


def eval_complexity(cx, cases=None) -> list[dict]:
    rows = []
    for c in (CX_CASES if cases is None else cases):
        t0 = time.perf_counter()
        try:
            r = cx.analyze(c["code"], c["cons"])
            got = r["expr"]
            err = None
        except Exception as e:  # noqa: BLE001
            got, err = "", f"{type(e).__name__}: {e}"
        rows.append({"name": c["name"], "tag": c["tag"], "expect": c["expect"][0], "got": got,
                     "ok": err is None and expr_ok(got, c["expect"]), "error": err,
                     "time": round(time.perf_counter() - t0, 3)})
    return rows


# ============================================================================================ optymalizator
def _rand_arr(rng, n, lo=1, hi=100):
    return " ".join(str(rng.randint(lo, hi)) for _ in range(n))


def g_range_sum(rng):
    n, q = rng.randint(1, 30), rng.randint(1, 30)
    qs = []
    for _ in range(q):
        l = rng.randint(0, n - 1)
        r = rng.randint(l, n - 1)
        qs.append(f"{l} {r}")
    return f"{n} {q}\n{_rand_arr(rng, n, -50, 1000)}\n" + "\n".join(qs) + "\n"


def g_range_sum1(rng):
    n, q = rng.randint(1, 30), rng.randint(1, 30)
    qs = []
    for _ in range(q):
        l = rng.randint(1, n)
        r = rng.randint(l, n)
        qs.append(f"{l} {r}")
    return f"{n} {q}\n{_rand_arr(rng, n, 0, 10**9)}\n" + "\n".join(qs) + "\n"


def g_queries_x(rng):
    n, q = rng.randint(1, 30), rng.randint(1, 30)
    return f"{n} {q}\n{_rand_arr(rng, n, 1, 20)}\n" + "\n".join(str(rng.randint(1, 25)) for _ in range(q)) + "\n"


def g_queue_ops(rng):
    q = rng.randint(1, 40)
    out, size = [], 0
    for _ in range(q):
        if size and rng.random() < 0.4:
            out.append("2")
            size -= 1
        else:
            out.append(f"1 {rng.randint(1, 100)}")
            size += 1
    return f"{q}\n" + "\n".join(out) + "\n"


def g_small_n(rng):
    return f"{rng.randint(0, 25)}\n"


def g_binom(rng):
    n = rng.randint(0, 22)
    return f"{n} {rng.randint(0, n)}\n"


def g_n(rng):
    n = rng.randint(1, 50)
    return f"{n}\n"


def g_n_arr(rng):
    n = rng.randint(1, 40)
    return f"{n}\n{_rand_arr(rng, n, 1, 1000)}\n"


def g_grid_sum(rng):
    n, m = rng.randint(1, 8), rng.randint(1, 8)
    rows = "\n".join(_rand_arr(rng, m, 0, 9) for _ in range(n))
    q = rng.randint(1, 15)
    qs = []
    for _ in range(q):
        a, b = sorted(rng.randint(1, n) for _ in range(2))
        c, d = sorted(rng.randint(1, m) for _ in range(2))
        qs.append(f"{a} {c} {b} {d}")
    return f"{n} {m} {q}\n{rows}\n" + "\n".join(qs) + "\n"


def g_tc_pow(rng):
    q = rng.randint(1, 20)
    return f"{q}\n" + "\n".join(f"{rng.randint(0, 10)} {rng.randint(0, 25)}" for _ in range(q)) + "\n"


def _op(name, code, top, rewrite=False, gen=None, gain=None, also=None):
    return {"name": name, "code": H + code.strip("\n") + "\n", "top": top if isinstance(top, list) else [top],
            "rewrite": rewrite, "gen": gen, "gain": gain, "also": also or []}


OPT_CASES = [
    _op("suma przedziału w pętli zapytań (vector, while(q--))", """
int main(){
    int n, q; cin >> n >> q;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    while (q--) {
        int l, r; cin >> l >> r;
        long long s = 0;
        for (int i = l; i <= r; i++) s += a[i];
        cout << s << endl;
    }
}
""", "prefix_sums", True, g_range_sum, gain="n + q"),
    _op("suma przedziału, tablica globalna, zapytania 1-indeksowane", """
int a[200005];
int main(){
    int n, q;
    scanf("%d %d", &n, &q);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    for (int t = 0; t < q; t++) {
        int l, r;
        scanf("%d %d", &l, &r);
        long long sum = 0;
        for (int i = l - 1; i < r; i++) {
            sum = sum + a[i];
        }
        printf("%lld\\n", sum);
    }
}
""", "prefix_sums", True, g_range_sum1, gain="n + q"),
    _op("suma przedziału, a[1..n], cin, i<=r", """
long long a[100005];
int main(){
    ios::sync_with_stdio(false); cin.tie(nullptr);
    int n, q; cin >> n >> q;
    for (int i = 1; i <= n; i++) cin >> a[i];
    while (q--) {
        int l, r; cin >> l >> r;
        long long w = 0;
        for (int j = l; j <= r; ++j)
            w += a[j];
        cout << w << '\\n';
    }
}
""", "prefix_sums", True, g_range_sum1, gain="n + q"),
    _op("maksimum na przedziale w zapytaniach", """
int a[200005];
int main(){
    int n, q; cin >> n >> q;
    for (int i = 0; i < n; i++) cin >> a[i];
    while (q--) {
        int l, r; cin >> l >> r;
        int mx = a[l];
        for (int i = l; i <= r; i++) mx = max(mx, a[i]);
        cout << mx << "\\n";
    }
}
""", "sparse_table"),
    _op("minimum na przedziale w zapytaniach (if)", """
int main(){
    int n, q; cin >> n >> q;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    for (int k = 0; k < q; k++) {
        int l, r; cin >> l >> r;
        int best = INT_MAX;
        for (int i = l; i <= r; i++) if (a[i] < best) best = a[i];
        cout << best << "\\n";
    }
}
""", "sparse_table"),
    _op("liniowe szukanie elementu w pętli zapytań", """
int a[200005];
int main(){
    int n, q; cin >> n >> q;
    for (int i = 0; i < n; i++) cin >> a[i];
    while (q--) {
        int x; cin >> x;
        bool found = false;
        for (int i = 0; i < n; i++) if (a[i] == x) { found = true; break; }
        cout << (found ? "TAK" : "NIE") << "\\n";
    }
}
""", "linear_search"),
    _op("find() na vectorze w pętli", """
int main(){
    int n, q; cin >> n >> q;
    vector<int> v(n);
    for (auto &x : v) cin >> x;
    int hits = 0;
    for (int i = 0; i < q; i++) {
        int x; cin >> x;
        if (find(v.begin(), v.end(), x) != v.end()) hits++;
    }
    cout << hits << "\\n";
}
""", "linear_search"),
    _op("count() na vectorze w pętli zapytań", """
int main(){
    int n, q; cin >> n >> q;
    vector<int> v(n);
    for (auto &x : v) cin >> x;
    while (q--) { int x; cin >> x; cout << count(v.begin(), v.end(), x) << "\\n"; }
}
""", "linear_search"),
    _op("kolejka na vectorze: erase(begin()) w pętli", """
int main(){
    int q; cin >> q;
    vector<int> v;
    long long s = 0;
    while (q--) {
        int t; cin >> t;
        if (t == 1) { int x; cin >> x; v.push_back(x); }
        else if (!v.empty()) { s += v[0]; v.erase(v.begin()); }
    }
    cout << s << " " << v.size() << "\\n";
}
""", "erase_begin", True, g_queue_ops, gain="q"),
    _op("insert(begin()) w pętli", """
int main(){
    int n; cin >> n;
    vector<int> v;
    for (int i = 0; i < n; i++) { int x; cin >> x; v.insert(v.begin(), x); }
    for (int x : v) cout << x << " ";
    cout << "\\n";
}
""", "erase_begin", True, g_n_arr, gain="n"),
    _op("s = s + c w pętli", """
int main(){
    int n; cin >> n;
    string s;
    for (int i = 0; i < n; i++) s = s + char('a' + i % 26);
    cout << s.size() << " " << s.substr(0, 5) << "\\n";
}
""", "string_concat", True, g_n),
    _op("Fibonacci bez spamiętywania", """
long long fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}
int main(){ int n; cin >> n; cout << fib(n) << "\\n"; }
""", "memoize", True, g_small_n, gain="n"),
    _op("dwumian Newtona rekurencyjnie (nakładające się podproblemy)", """
long long C(int n, int k) {
    if (k == 0 || k == n) return 1;
    return C(n - 1, k - 1) + C(n - 1, k);
}
int main(){ int n, k; cin >> n >> k; cout << C(n, k) << "\\n"; }
""", "memoize", True, g_binom),
    _op("pow() na liczbach całkowitych modulo", """
const long long MOD = 1000000007;
int main(){
    int q; cin >> q;
    while (q--) {
        long long a, b; cin >> a >> b;
        long long r = (long long)pow(a, b) % MOD;
        cout << r << "\\n";
    }
}
""", "int_pow"),
    _op("map z małymi kluczami (zliczanie liter)", """
int main(){
    string s; cin >> s;
    map<char, int> cnt;
    for (char c : s) cnt[c]++;
    int best = 0;
    for (auto [c, k] : cnt) best = max(best, k);
    cout << best << "\\n";
}
""", "small_map"),
    _op("endl w pętli przy dużym wyjściu (algorytm OK)", """
int main(){
    ios::sync_with_stdio(false); cin.tie(nullptr);
    int n; cin >> n;
    for (int i = 1; i <= n; i++) cout << 1LL * i * i << endl;
}
""", "endl", True, g_n),
    _op("vector przez wartość do funkcji rekurencyjnej", """
long long go(vector<int> a, int i) {
    if (i == (int)a.size()) return 0;
    return a[i] + go(a, i + 1);
}
int main(){
    ios::sync_with_stdio(false); cin.tie(nullptr);
    int n; cin >> n;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    cout << go(a, 0) << "\\n";
}
""", "const_ref", True, g_n_arr, gain="n"),
    _op("sort w pętli zapytań na niezmienianej tablicy", """
int main(){
    int n, q; cin >> n >> q;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    while (q--) {
        int x; cin >> x;
        sort(a.begin(), a.end());
        cout << upper_bound(a.begin(), a.end(), x) - a.begin() << "\\n";
    }
}
""", "hoist_sort", True, g_queries_x, gain="n log n + q log n"),
    _op("pary o sumie k — dwie pętle", """
int a[200005];
int main(){
    int n; long long k; cin >> n >> k;
    for (int i = 0; i < n; i++) cin >> a[i];
    long long cnt = 0;
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            if (a[i] + a[j] == k) cnt++;
    cout << cnt << "\\n";
}
""", "pair_sum"),
    _op("std::lower_bound na secie", """
int main(){
    int n, q; cin >> n >> q;
    set<int> s;
    for (int i = 0; i < n; i++) { int x; cin >> x; s.insert(x); }
    while (q--) {
        int x; cin >> x;
        auto it = lower_bound(s.begin(), s.end(), x);
        cout << (it == s.end() ? -1 : *it) << "\\n";
    }
}
""", "set_lower_bound", True, g_queries_x),
    _op("suma przedziału + endl: algorytm ważniejszy niż I/O", """
int main(){
    int n, q; cin >> n >> q;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    for (int i = 0; i < q; i++) {
        int l, r; cin >> l >> r;
        long long s = 0;
        for (int j = l; j <= r; j++) { s += a[j]; }
        cout << s << endl;
    }
}
""", "prefix_sums", True, g_range_sum, gain="n + q"),
    _op("sumy 2D w zapytaniach (prostokąty)", """
int t[505][505];
int main(){
    int n, m, q; cin >> n >> m >> q;
    for (int i = 1; i <= n; i++) for (int j = 1; j <= m; j++) cin >> t[i][j];
    while (q--) {
        int a, b, c, d; cin >> a >> b >> c >> d;
        long long s = 0;
        for (int i = a; i <= c; i++) for (int j = b; j <= d; j++) s += t[i][j];
        cout << s << "\\n";
    }
}
""", "prefix_sums_2d"),
]

ALGO_RULES = {"prefix_sums", "prefix_sums_2d", "sparse_table", "linear_search", "erase_begin", "memoize", "hoist_sort",
              "pair_sum", "string_concat", "set_lower_bound", "const_ref", "int_pow", "hoist_strlen"}


def eval_optimizer(op, compile_check: bool = True, tests: int = 12) -> list[dict]:
    runner = None
    if compile_check:
        try:
            from oiforge.core import runner as _r
            runner = _r
        except Exception:  # noqa: BLE001
            runner = None
    rows = []
    for c in OPT_CASES:
        row = {"name": c["name"], "expect": c["top"][0], "rewrite_expected": c["rewrite"]}
        try:
            res = op.optimize(c["code"])
        except Exception as e:  # noqa: BLE001
            row.update(top=None, ok=False, error=f"{type(e).__name__}: {e}")
            rows.append(row)
            continue
        ch = res["changes"]
        row["top"] = ch[0]["rule"] if ch else None
        row["rules"] = [x["rule"] for x in ch]
        row["ok"] = row["top"] in c["top"]
        row["in_list"] = any(x["rule"] in c["top"] for x in ch)
        target = next((x for x in ch if x["rule"] in c["top"]), None)
        row["gain"] = (target or {}).get("gain")
        row["rewritten"] = bool(target and target.get("has_edit"))
        if c["rewrite"] and runner is not None and target is not None and target.get("has_edit"):
            new = op.optimize(c["code"], [target["rule"]])["optimized_code"]
            row["compiles"], row["same_output"] = _check_same(runner, c["code"], new, c["gen"], tests)
        if runner is not None and res.get("changed") and c["gen"] is not None:
            # wszystkie bezpieczne zmiany naraz (domyślne działanie optymalizatora) — konflikty między regułami
            row["all_safe_compiles"], row["all_safe_same"] = _check_same(runner, c["code"], res["optimized_code"],
                                                                         c["gen"], tests)
        rows.append(row)
    return rows


def _check_same(runner, orig: str, new: str, gen, tests: int):
    a = runner.prepare(orig, mode="release")
    b = runner.prepare(new, mode="release")
    if not b.ok:
        return False, False
    if not a.ok or gen is None:
        return True, None
    rng = random.Random(2024)
    for _ in range(tests):
        inp = gen(rng)
        ra = runner.run(a.cmd, inp, timeout=5)
        rb = runner.run(b.cmd, inp, timeout=5)
        if ra.verdict != "OK":
            continue
        if rb.verdict != "OK" or not runner.outputs_equal(ra.stdout, rb.stdout):
            return True, False
    return True, True


# ============================================================================================ CLI
def _load(pkg: str | None):
    if pkg:
        sys.path.insert(0, str(Path(pkg).resolve()))
        for k in [k for k in sys.modules if k == "oiforge" or k.startswith("oiforge.")]:
            del sys.modules[k]
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from oiforge.tools import complexity as cx
    from oiforge.tools import optimizer as op
    return cx, op


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pkg", help="katalog z pakietem oiforge (domyślnie bieżący projekt)")
    ap.add_argument("--no-compile", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--only", choices=["cx", "opt"])
    a = ap.parse_args(argv)
    cx, op = _load(a.pkg)
    out = {}
    if a.only != "opt":
        rows = eval_complexity(cx)
        out["complexity"] = rows
        ok = sum(r["ok"] for r in rows)
        print(f"\n== Złożoność: {ok}/{len(rows)} trafnych ({100 * ok / len(rows):.0f}%)")
        for r in rows:
            print(f"  {'✔' if r['ok'] else '✘'} {r['name'][:58]:58s} oczek. O({r['expect']})  jest O({r['got']})"
                  + (f"  [{r['error']}]" if r.get("error") else ""))
        rows = eval_complexity(cx, CX_CONTROL)
        out["complexity_control"] = rows
        ok = sum(r["ok"] for r in rows)
        print(f"\n== Złożoność — zestaw kontrolny: {ok}/{len(rows)} trafnych ({100 * ok / len(rows):.0f}%)")
        for r in rows:
            print(f"  {'✔' if r['ok'] else '✘'} {r['name'][:58]:58s} oczek. O({r['expect']})  jest O({r['got']})"
                  + (f"  [{r['error']}]" if r.get("error") else ""))
    if a.only != "cx":
        rows = eval_optimizer(op, compile_check=not a.no_compile)
        out["optimizer"] = rows
        ok = sum(r["ok"] for r in rows)
        inl = sum(r.get("in_list", False) for r in rows)
        rw = [r for r in rows if r["rewrite_expected"]]
        rw_ok = sum(1 for r in rw if r.get("rewritten") and r.get("compiles") and r.get("same_output") is not False)
        sf = [r for r in rows if "all_safe_compiles" in r]
        sf_ok = sum(1 for r in sf if r["all_safe_compiles"] and r["all_safe_same"] is not False)
        print(f"\n== Optymalizator: na górze listy {ok}/{len(rows)}, na liście {inl}/{len(rows)}, "
              f"przepisania kompilują się i dają ten sam wynik: {rw_ok}/{len(rw)}, "
              f"wszystkie bezpieczne zmiany naraz: {sf_ok}/{len(sf)}")
        for r in rows:
            extra = ""
            if r["rewrite_expected"]:
                extra = f" przepisano={r.get('rewritten')} kompiluje={r.get('compiles')} wynik={r.get('same_output')}"
            print(f"  {'✔' if r['ok'] else '✘'} {r['name'][:58]:58s} oczek. {r['expect']:15s} jest {str(r.get('top')):15s}"
                  f"{extra}" + (f" zysk: {r['gain']}" if r.get("gain") else ""))
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

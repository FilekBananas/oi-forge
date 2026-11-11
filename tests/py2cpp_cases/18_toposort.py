from collections import deque

n, m = map(int, input().split())
g = [[] for _ in range(n + 1)]
indeg = [0] * (n + 1)
for _ in range(m):
    a, b = map(int, input().split())
    g[a].append(b)
    indeg[b] += 1
q = deque(v for v in range(1, n + 1) if indeg[v] == 0)
order = []
while q:
    u = q.popleft()
    order.append(u)
    for v in g[u]:
        indeg[v] -= 1
        if indeg[v] == 0:
            q.append(v)
if len(order) < n:
    print("CYKL")
else:
    print(*order)

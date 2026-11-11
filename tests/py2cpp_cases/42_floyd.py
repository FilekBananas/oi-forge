INF = 10**18
n, m = map(int, input().split())
d = [[INF] * n for _ in range(n)]
for i in range(n):
    d[i][i] = 0
for _ in range(m):
    a, b, w = map(int, input().split())
    a -= 1
    b -= 1
    d[a][b] = min(d[a][b], w)
for k in range(n):
    for i in range(n):
        for j in range(n):
            if d[i][k] + d[k][j] < d[i][j]:
                d[i][j] = d[i][k] + d[k][j]
for row in d:
    print(' '.join(str(x) if x < INF else 'X' for x in row))

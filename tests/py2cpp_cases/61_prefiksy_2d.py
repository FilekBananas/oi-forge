n, m = map(int, input().split())
g = [list(map(int, input().split())) for _ in range(n)]
P = [[0] * (m + 1) for _ in range(n + 1)]
for i in range(n):
    for j in range(m):
        P[i + 1][j + 1] = g[i][j] + P[i][j + 1] + P[i + 1][j] - P[i][j]
q = int(input())
for _ in range(q):
    r1, c1, r2, c2 = map(int, input().split())
    print(P[r2][c2] - P[r1 - 1][c2] - P[r2][c1 - 1] + P[r1 - 1][c1 - 1])

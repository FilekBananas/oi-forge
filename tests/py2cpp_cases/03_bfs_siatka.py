from collections import deque

n, m = map(int, input().split())
grid = [input() for _ in range(n)]
sr = sc = tr = tc = 0
for i in range(n):
    for j in range(m):
        if grid[i][j] == 'S':
            sr, sc = i, j
        elif grid[i][j] == 'T':
            tr, tc = i, j
dist = [[-1] * m for _ in range(n)]
dist[sr][sc] = 0
q = deque()
q.append((sr, sc))
while q:
    r, c = q.popleft()
    for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        nr, nc = r + dr, c + dc
        if 0 <= nr < n and 0 <= nc < m and grid[nr][nc] != '#' and dist[nr][nc] == -1:
            dist[nr][nc] = dist[r][c] + 1
            q.append((nr, nc))
print(dist[tr][tc])

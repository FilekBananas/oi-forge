n, m = map(int, input().split())
grid = [list(input()) for _ in range(n)]
for i in range(n):
    for j in range(m):
        if grid[i][j] == '.' and (i + j) % 2 == 0:
            grid[i][j] = '+'
for row in grid:
    print(''.join(row))
cols = [''.join(col) for col in zip(*grid)]
print(cols)
cnt = sum(row.count('#') for row in grid)
print(cnt)

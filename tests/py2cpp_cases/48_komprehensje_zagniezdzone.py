n, m = map(int, input().split())
grid = [list(map(int, input().split())) for _ in range(n)]
t = [[grid[i][j] for i in range(n)] for j in range(m)]
for row in t:
    print(*row)
flat = [x for row in grid for x in row if x % 2 == 1]
print(flat)
sums = [sum(row) for row in grid]
print(sums, max(max(row) for row in grid))

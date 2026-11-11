n, s = map(int, input().split())
coins = list(map(int, input().split()))
INF = float('inf')
dp = [INF] * (s + 1)
dp[0] = 0
ways = [0] * (s + 1)
ways[0] = 1
for c in coins:
    for v in range(c, s + 1):
        if dp[v - c] + 1 < dp[v]:
            dp[v] = dp[v - c] + 1
        ways[v] = (ways[v] + ways[v - c]) % 1000000007
print(dp[s] if dp[s] != INF else -1, ways[s])

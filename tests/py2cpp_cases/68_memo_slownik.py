import sys
sys.setrecursionlimit(10000)
memo = {}

def ways(n, k=2):
    if n < 0:
        return 0
    if n == 0:
        return 1
    key = (n, k)
    if key in memo:
        return memo[key]
    res = (ways(n - 1, k) + ways(n - k, k=k)) % 1000000007
    memo[key] = res
    return res

n = int(input())
print(ways(n), ways(n, 3), ways(n, k=1))

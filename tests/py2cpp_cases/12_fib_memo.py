from functools import lru_cache
import sys
sys.setrecursionlimit(10000)

MOD = 10**9 + 7

@lru_cache(maxsize=None)
def fib(n):
    if n < 2:
        return n
    return (fib(n - 1) + fib(n - 2)) % MOD

@lru_cache(None)
def paths(r, c):
    if r == 0 or c == 0:
        return 1
    return (paths(r - 1, c) + paths(r, c - 1)) % MOD

n = int(input())
print(fib(n))
print(paths(n % 50, n % 37))

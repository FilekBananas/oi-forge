def solve():
    n = int(input())
    a = list(map(int, input().split()))
    calls = 0

    def go(i, s):
        nonlocal calls
        calls += 1
        if i == n:
            return 1 if s == 0 else 0
        return go(i + 1, s + a[i]) + go(i + 1, s)

    total = go(0, 0)
    print(total - 1, calls)

solve()

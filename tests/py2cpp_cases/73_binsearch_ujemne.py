def first_true(lo, hi, pred):
    while lo < hi:
        mid = (lo + hi) // 2
        if pred(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo

t = int(input())
for _ in range(t):
    x = int(input())
    print(first_true(-10**9, 10**9, lambda v: v * 3 >= x))

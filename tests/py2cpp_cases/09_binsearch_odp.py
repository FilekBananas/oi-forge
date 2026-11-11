def ok(d, a, k):
    cnt = 1
    last = a[0]
    for x in a[1:]:
        if x - last >= d:
            cnt += 1
            last = x
    return cnt >= k

n, k = map(int, input().split())
a = sorted(map(int, input().split()))
lo, hi = 0, a[-1] - a[0] + 1
while hi - lo > 1:
    mid = (lo + hi) // 2
    if ok(mid, a, k):
        lo = mid
    else:
        hi = mid
print(lo)

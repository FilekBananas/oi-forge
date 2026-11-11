def clamp(x, lo=0, hi=10):
    return max(lo, min(hi, x))

def find_first(a, target):
    for i, x in enumerate(a):
        if x == target:
            return i
    return None

def stats(a):
    return min(a), max(a), sum(a)

a = list(map(int, input().split()))
print([clamp(x) for x in a], clamp(a[0], hi=2), clamp(a[0], -100, 100))
idx = find_first(a, 3)
print(idx, find_first(a, 99))
lo, hi, tot = stats(a)
print(lo, hi, tot)

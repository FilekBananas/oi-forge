from bisect import bisect_left, bisect_right

n = int(input())
a = list(map(int, input().split()))
tails = []
for x in a:
    i = bisect_left(tails, x)
    if i == len(tails):
        tails.append(x)
    else:
        tails[i] = x
print(len(tails))
s = sorted(a)
q = int(input())
for _ in range(q):
    lo, hi = map(int, input().split())
    print(bisect_right(s, hi) - bisect_left(s, lo))

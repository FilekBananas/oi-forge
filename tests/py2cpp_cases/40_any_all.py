a = list(map(int, input().split()))
print(any(x > 10 for x in a), all(x > 0 for x in a))
print(any(a), all(a))
print(sum(1 for x in a if x % 2 == 0))
print(sum(x for x in a if x > 0))
evens = [x for x in a if x % 2 == 0]
print(len(evens), evens)
sq = {x * x for x in a}
print(sorted(sq))
d = {x: x * 2 for x in a}
print(d[a[0]])

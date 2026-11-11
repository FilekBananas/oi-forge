a = list(map(int, input().split()))
found = None
for i, x in enumerate(a):
    if x < 0:
        found = i
        break
print(found)
flags = [x > 0 for x in a]
print(flags, any(flags), all(flags))
pairs = [(x, x * x) for x in a[:2]]
print(pairs)
print(tuple(a[:3]), {1: 'a'}, (5,))
print(True and False, not True, 3 > 2)

n = int(input())
a = set(map(int, input().split()))
b = set(map(int, input().split()))
print(len(a), len(b))
print(sorted(a & b))
print(sorted(a | b))
print(sorted(a - b))
seen = set()
dup = 0
for x in [1, 2, 3, 2, 1, 4]:
    if x in seen:
        dup += 1
    seen.add(x)
print(dup, 4 in seen, 7 not in seen)

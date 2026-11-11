n = int(input())
people = []
for _ in range(n):
    name, age, score = input().split()
    people.append((name, int(age), float(score)))
people.sort(key=lambda p: (-p[2], p[1], p[0]))
for name, age, score in people:
    print(name, age, score)
oldest = max(people, key=lambda p: p[1])
print(oldest[0])
by_name = sorted(people, key=lambda p: p[0])
print([p[0] for p in by_name])

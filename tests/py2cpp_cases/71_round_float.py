import math
xs = list(map(float, input().split()))
for x in xs:
    print(round(x, 2), round(x), math.floor(x), math.ceil(x), math.sqrt(abs(x)))
mean = sum(xs) / len(xs)
var = sum((x - mean) ** 2 for x in xs) / len(xs)
print(f"{mean:.3f} {var:.3f}")
print(abs(mean - 2.0) < 1e-9)

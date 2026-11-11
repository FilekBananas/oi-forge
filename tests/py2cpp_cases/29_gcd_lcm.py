import math
from functools import reduce

n = int(input())
a = list(map(int, input().split()))
g = 0
for x in a:
    g = math.gcd(g, x)
l = reduce(lambda x, y: x * y // math.gcd(x, y), a)
print(g, l)
print(math.isqrt(n * 1000), math.sqrt(16), math.floor(7 / 2), math.ceil(7 / 2))

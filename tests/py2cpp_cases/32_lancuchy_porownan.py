a, b, c = map(int, input().split())
print(a < b < c, a <= b <= c, a == b == c)
if 0 <= a < 10:
    print("cyfra")
x, y = a, b
x, y = y, x
print(x, y)
x, y = y, x + y
print(x, y)
a, b, c = c, a, b
print(a, b, c)
p = q = 5
print(p + q)

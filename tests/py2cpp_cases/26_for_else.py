def first_divisor(n):
    for d in range(2, n):
        if d * d > n:
            break
        if n % d == 0:
            return d
    return n

n = int(input())
for x in range(2, n + 1):
    for d in range(2, x):
        if x % d == 0:
            print(x, "=", d, "*", x // d)
            break
    else:
        print(x, "pierwsza")
k = n
while k > 1:
    if k % 7 == 0:
        print("podzielne przez 7:", k)
        break
    k -= 1
else:
    print("brak wielokrotności 7")
print(first_divisor(91), first_divisor(97))

n = int(input())
steps = 0
x = n
while x != 1 and steps < 1000:
    if x % 2 == 0:
        x //= 2
    else:
        x = 3 * x + 1
    steps += 1
print(steps)
digits = []
y = n
while y > 0:
    digits.append(y % 10)
    y //= 10
print(digits[::-1], sum(digits))
k = 0
while k * k <= n:
    k += 1
print(k - 1)

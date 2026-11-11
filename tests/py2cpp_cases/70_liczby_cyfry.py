def digit_sum(x):
    x = abs(x)
    s = 0
    while x:
        s += x % 10
        x //= 10
    return s

for v in map(int, input().split()):
    r = str(v)[::-1]
    print(digit_sum(v), r, v % 10, v // 10, -v % 7, int(str(abs(v))[::-1]))

n = int(input())
for i in range(1, n + 1):
    print(' ' * (n - i) + '*' * (2 * i - 1))
print('-' * 10)
print('ab' * 3, [0, 1] * 2)
s = "Ala ma kota"
print(s.split(' '), s.lower().count('a'), s.title(), s.swapcase())
print(s.center(15, '.'), s.ljust(13, '_') + '|', s.zfill(14))
print("%s|%5s|%-5s|" % ('x', 'ab', 'cd'))

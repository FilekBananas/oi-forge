a = list(map(int, input().split()))
a.insert(0, 100)
a.append(-1)
print(a)
a.remove(100)
print(a.index(-1), a.count(3))
a.reverse()
print(a)
last = a.pop()
print(last, a)
a.extend([7, 8])
print(a, len(a))
b = a[:]
b[0] = 99
print(a[0] != b[0])
print(a[1:3], a[:2], a[-3:], a[::2])
del a[0]
print(a)

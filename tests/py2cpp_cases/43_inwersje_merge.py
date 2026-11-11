import sys
sys.setrecursionlimit(100000)

def sort_count(a):
    if len(a) <= 1:
        return a, 0
    mid = len(a) // 2
    left, x = sort_count(a[:mid])
    right, y = sort_count(a[mid:])
    merged = []
    i = j = 0
    inv = x + y
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
            inv += len(left) - i
    merged += left[i:]
    merged += right[j:]
    return merged, inv

n = int(input())
a = list(map(int, input().split()))
s, inv = sort_count(a)
print(inv)
print(*s)

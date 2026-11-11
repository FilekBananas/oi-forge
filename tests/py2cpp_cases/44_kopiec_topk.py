import heapq

n, k = map(int, input().split())
a = list(map(int, input().split()))
h = []
for x in a:
    heapq.heappush(h, -x)
top = []
for _ in range(k):
    top.append(-heapq.heappop(h))
print(*top)
mn = a[:]
heapq.heapify(mn)
print(heapq.heappop(mn), heapq.heappop(mn), mn[0])
print(heapq.nlargest(2, a), heapq.nsmallest(2, a))

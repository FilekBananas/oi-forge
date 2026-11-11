class Event:
    def __init__(self, t, kind, name):
        self.t = t
        self.kind = kind
        self.name = name

    def __lt__(self, other):
        if self.t != other.t:
            return self.t < other.t
        return self.kind < other.kind

n = int(input())
evs = []
for _ in range(n):
    t, k, name = input().split()
    evs.append(Event(int(t), int(k), name))
evs.sort()
for e in evs:
    print(e.t, e.kind, e.name)

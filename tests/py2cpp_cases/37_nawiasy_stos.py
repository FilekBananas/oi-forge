t = int(input())
pairs = {')': '(', ']': '[', '}': '{'}
for _ in range(t):
    s = input()
    stack = []
    ok = True
    for ch in s:
        if ch in '([{':
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                ok = False
                break
            stack.pop()
    if stack:
        ok = False
    print("TAK" if ok else "NIE")

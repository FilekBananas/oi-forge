k = int(input())
s = input()
res = []
for ch in s:
    if 'a' <= ch <= 'z':
        res.append(chr((ord(ch) - ord('a') + k) % 26 + ord('a')))
    elif ch.isupper():
        res.append(chr((ord(ch) - ord('A') + k) % 26 + ord('A')))
    else:
        res.append(ch)
print(''.join(res))
print(ord('a'), chr(66), [ord(c) for c in "AB"])

// name: TAK/NIE bez wielkości liter
// description: Porównuje tokeny bez rozróżniania wielkości liter (TAK = tak = Tak, YES = yes).
// Użycie: checker wejście wyjście_programu wyjście_wzorcowe  →  kod 0 = OK, 1 = WA
#include <bits/stdc++.h>
using namespace std;

static string lower(string s) {
    for (char& c : s) c = (char)tolower((unsigned char)c);
    return s;
}

int main(int argc, char** argv) {
    if (argc < 4) { printf("Użycie: %s in out ans\n", argv[0]); return 2; }
    ifstream out(argv[2]), ans(argv[3]);
    string a, b;
    long long idx = 0;
    while (true) {
        bool ha = static_cast<bool>(out >> a), hb = static_cast<bool>(ans >> b);
        if (!ha && !hb) break;
        ++idx;
        if (!ha) { printf("Za krótkie wyjście: brakuje tokenu nr %lld (\"%s\").\n", idx, b.c_str()); return 1; }
        if (!hb) { printf("Za długie wyjście: nadmiarowy token nr %lld (\"%s\").\n", idx, a.c_str()); return 1; }
        if (lower(a) != lower(b)) { printf("Token nr %lld: \"%s\" zamiast \"%s\".\n", idx, a.c_str(), b.c_str()); return 1; }
    }
    printf("OK (%lld tokenów).\n", idx);
    return 0;
}

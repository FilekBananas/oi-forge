// name: Liczby rzeczywiste z tolerancją
// description: Porównuje tokeny; liczby uznaje za równe, gdy różnią się o ≤ eps bezwzględnie lub względnie (domyślnie 1e-6, można podać jako 4. argument).
// Użycie: checker wejście wyjście_programu wyjście_wzorcowe [eps]  →  kod 0 = OK, 1 = WA
#include <bits/stdc++.h>
using namespace std;

static bool isNumber(const string& s, long double& v) {
    if (s.empty()) return false;
    char* end = nullptr;
    v = strtold(s.c_str(), &end);
    return end && *end == '\0';
}

int main(int argc, char** argv) {
    if (argc < 4) { printf("Użycie: %s in out ans [eps]\n", argv[0]); return 2; }
    long double eps = argc >= 5 ? strtold(argv[4], nullptr) : 1e-6L;
    ifstream out(argv[2]), ans(argv[3]);
    string a, b;
    long long idx = 0;
    while (true) {
        bool ha = static_cast<bool>(out >> a), hb = static_cast<bool>(ans >> b);
        if (!ha && !hb) break;
        ++idx;
        if (!ha) { printf("Za mało liczb: brakuje tokenu nr %lld (oczekiwano \"%s\").\n", idx, b.c_str()); return 1; }
        if (!hb) { printf("Za dużo liczb: nadmiarowy token nr %lld (\"%s\").\n", idx, a.c_str()); return 1; }
        if (a == b) continue;
        long double x, y;
        if (isNumber(a, x) && isNumber(b, y)) {
            long double d = fabsl(x - y);
            if (d <= eps || d <= eps * max(fabsl(x), fabsl(y))) continue;
            printf("Token nr %lld: %s zamiast %s (różnica %.3Lg > %.3Lg).\n", idx, a.c_str(), b.c_str(), d, eps);
            return 1;
        }
        printf("Token nr %lld: \"%s\" zamiast \"%s\".\n", idx, a.c_str(), b.c_str());
        return 1;
    }
    printf("OK (%lld tokenów).\n", idx);
    return 0;
}

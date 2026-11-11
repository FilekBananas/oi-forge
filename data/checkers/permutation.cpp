// name: Dowolna kolejność (permutacja)
// description: Wyjście jest poprawne, jeśli zawiera dokładnie te same tokeny co wzorcowe (z powtórzeniami), w dowolnej kolejności.
// Użycie: checker wejście wyjście_programu wyjście_wzorcowe  →  kod 0 = OK, 1 = WA
#include <bits/stdc++.h>
using namespace std;

int main(int argc, char** argv) {
    if (argc < 4) { printf("Użycie: %s in out ans\n", argv[0]); return 2; }
    ifstream out(argv[2]), ans(argv[3]);
    vector<string> a, b;
    string t;
    while (out >> t) a.push_back(t);
    while (ans >> t) b.push_back(t);
    if (a.size() != b.size()) {
        printf("Liczba tokenów: %zu, oczekiwano %zu.\n", a.size(), b.size());
        return 1;
    }
    sort(a.begin(), a.end());
    sort(b.begin(), b.end());
    for (size_t i = 0; i < a.size(); ++i) {
        if (a[i] != b[i]) {
            printf("Różne wartości (po posortowaniu): \"%s\" zamiast \"%s\".\n", a[i].c_str(), b[i].c_str());
            return 1;
        }
    }
    printf("OK (te same %zu tokenów).\n", a.size());
    return 0;
}

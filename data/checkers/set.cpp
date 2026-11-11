// name: Porównanie zbiorów
// description: Porównuje ZBIORY tokenów — kolejność i powtórzenia nie mają znaczenia.
// Użycie: checker wejście wyjście_programu wyjście_wzorcowe  →  kod 0 = OK, 1 = WA
#include <bits/stdc++.h>
using namespace std;

int main(int argc, char** argv) {
    if (argc < 4) { printf("Użycie: %s in out ans\n", argv[0]); return 2; }
    ifstream out(argv[2]), ans(argv[3]);
    set<string> a, b;
    string t;
    while (out >> t) a.insert(t);
    while (ans >> t) b.insert(t);
    for (const string& x : b) if (!a.count(x)) { printf("Brakuje elementu \"%s\".\n", x.c_str()); return 1; }
    for (const string& x : a) if (!b.count(x)) { printf("Nadmiarowy element \"%s\".\n", x.c_str()); return 1; }
    printf("OK (zbiór %zu elementów).\n", a.size());
    return 0;
}

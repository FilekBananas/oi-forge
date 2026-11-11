// name: Linie w dowolnej kolejności
// description: Każda linia wyjścia (po usunięciu nadmiarowych spacji) musi wystąpić we wzorcowym tyle samo razy — kolejność linii dowolna.
// Użycie: checker wejście wyjście_programu wyjście_wzorcowe  →  kod 0 = OK, 1 = WA
#include <bits/stdc++.h>
using namespace std;

static vector<string> readLines(const char* path) {
    ifstream f(path);
    vector<string> res;
    string line;
    while (getline(f, line)) {
        stringstream ss(line);
        string tok, norm;
        while (ss >> tok) { if (!norm.empty()) norm += ' '; norm += tok; }
        if (!norm.empty()) res.push_back(norm);
    }
    return res;
}

int main(int argc, char** argv) {
    if (argc < 4) { printf("Użycie: %s in out ans\n", argv[0]); return 2; }
    vector<string> a = readLines(argv[2]), b = readLines(argv[3]);
    if (a.size() != b.size()) { printf("Liczba niepustych linii: %zu, oczekiwano %zu.\n", a.size(), b.size()); return 1; }
    sort(a.begin(), a.end());
    sort(b.begin(), b.end());
    for (size_t i = 0; i < a.size(); ++i)
        if (a[i] != b[i]) { printf("Linia \"%s\" nie pasuje (oczekiwano \"%s\").\n", a[i].c_str(), b[i].c_str()); return 1; }
    printf("OK (%zu linii).\n", a.size());
    return 0;
}

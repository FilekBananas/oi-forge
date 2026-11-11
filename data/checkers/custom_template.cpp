// name: Własny checker (szablon)
// description: Szablon do napisania własnej weryfikacji — np. gdy zadanie ma wiele poprawnych odpowiedzi. Wczytuje wejście, wyjście programu i wzorcowe.
// Użycie: checker wejście wyjście_programu wyjście_wzorcowe  →  kod 0 = OK, 1 = WA (komunikat wypisz na stdout)
#include <bits/stdc++.h>
using namespace std;

int main(int argc, char** argv) {
    if (argc < 4) { printf("Użycie: %s in out ans\n", argv[0]); return 2; }
    ifstream in(argv[1]), out(argv[2]), ans(argv[3]);

    // 1) Wczytaj wejście testu (tak jak w zadaniu).
    long long n;
    in >> n;

    // 2) Wczytaj odpowiedź wzorcową (np. optymalny wynik) i odpowiedź programu.
    long long best, got;
    if (!(ans >> best)) { printf("Brak odpowiedzi wzorcowej.\n"); return 2; }
    if (!(out >> got)) { printf("Program nic nie wypisał.\n"); return 1; }

    // 3) Sprawdź poprawność. Przykład: wynik musi być równy optymalnemu.
    //    W zadaniach z wieloma poprawnymi odpowiedziami sprawdź tu np. czy wypisany ciąg/ścieżka spełnia warunki.
    if (got != best) { printf("Wynik %lld, a optymalny to %lld.\n", got, best); return 1; }

    string extra;
    if (out >> extra) { printf("Nadmiarowe dane na wyjściu: \"%s\"…\n", extra.c_str()); return 1; }
    printf("OK\n");
    return 0;
}

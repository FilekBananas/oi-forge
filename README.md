# ⚒ OI Forge

**Projekt i program do przygotowań do Olimpiady Informatycznej — BEZ AI.**

OI Forge to projekt: program, który uruchamiasz u siebie na komputerze. Nie jest to aplikacja w chmurze, usługa ani
konto do założenia. W jednym oknie masz edytor z uruchamianiem i testami, asystenta z ogromną bazą wiedzy, testerkę,
wizualny debugger, sprawdzarkę kodu, tłumacza Python → C++, analizę złożoności, wyszukiwarkę Twoich własnych rozwiązań
i ponad 30 innych narzędzi.

---

## BEZ AI

**W OI Forge nie ma żadnej sztucznej inteligencji.** Żadnego modelu językowego (LLM), żadnego „czatu z AI”, żadnej
chmury i żadnego generowania tekstu.

- **Odpowiedzi asystenta są napisane, nie wygenerowane.** Każda pochodzi z ręcznie przygotowanej bazy wiedzy:
  370 artykułów, 3326 pytań z odpowiedziami i 1726 opisanych funkcji. Każdy fragment kodu w bazie kompiluje się.
- **Bez internetu.** Program łączy się z siecią tylko wtedy, gdy sam każesz mu zaimportować swoje programy ze Szkopułu
  albo z SIO2. Żadnych kont, reklam ani telemetrii.
- **Bez karty graficznej i bez mielenia procesora.** Nie ma modelu AI, który liczyłby odpowiedzi — szukanie w bazie trwa
  milisekundy. Procesor pracuje naprawdę tylko wtedy, gdy sam coś uruchamiasz (kompilacja, testy, debugger), oraz raz
  przy pierwszym starcie, kiedy program w tle, z niskim priorytetem, przygotowuje indeksy i kompiluje szablony.
- **Jak to działa, bez marketingu:** wyszukiwanie BM25 z synonimami i poprawianiem literówek, reguły i wzorce, analiza
  kodu na tokenach, prawdziwe kompilatory, sanitizery i debugger, a w auto-solverze — przeglądanie małych programów
  i sprawdzanie ich na przykładach z treści. Kolejność wyników poprawiają proste statystyki liczone lokalnie: mała sieć
  dopasowująca słowa (kilkaset tysięcy liczb, uczona w kilka sekund na samej bazie wiedzy) i klasyfikator Naive Bayes.
  To zwykła matematyka na Twoim komputerze, nie model AI.

---

## Twoje programy, nie moje

W repozytorium **nie ma niczyich rozwiązań** — ani moich, ani nikogo innego. Nie ma też żadnych danych „dotrenowanych”
pod konkretną osobę. Po pobraniu OI Forge jest czysty, a swoje programy importujesz sam, na jeden z dwóch sposobów
(**Moje programy → Synchronizacja i import**):

1. **Z serwera SIO2 albo ze Szkopułu**
   - **Szkopuł** — wybierasz „Szkopuł” i logujesz się. Program pobiera Twoje rozwiązania za 100 pkt z konkursów
     i z archiwum zadań.
   - **Dowolna strona działająca na SIO2** (np. serwer Twojej szkoły albo kółka) — wybierasz „Inna strona SIO2”,
     wpisujesz jej adres i logujesz się.

   Pobierają się wszystkie rozwiązania za 100 pkt, z tematami rund i treściami zadań; kolejne importy dociągają tylko
   nowe. Hasło nie jest nigdzie zapisywane (na życzenie tylko ciasteczko sesji). Jeśli serwer nie wpuszcza programu,
   jest skrypt do konsoli przeglądarki, który robi to samo z poziomu zalogowanej przeglądarki.
2. **Z folderu z programami** — wskazujesz folder ze swoimi rozwiązaniami (albo upuszczasz `.zip`). Nazwy zadań
   i tematy program zgaduje z nazw plików i folderów (np. `Dijkstra/lot.cpp`) oraz komentarzy `// Zadanie: …`.

**Potem OI Forge dostraja się do Ciebie** — lokalnie i bez AI: wyszukiwarka przeszukuje Twoje programy, edytor
podpowiada nazwy z Twojego kodu, styl formatowania „moja” uczy się z Twoich programów, doradca algorytmów uczy się na
Twoich zadaniach (tematy rund to gotowe etykiety), dziennik liczy Twoje postępy, a asystent przy odpowiedziach pokazuje
Twoje rozwiązania na dany temat. Wszystko zostaje w `user_data/` na Twoim komputerze i nigdzie nie jest wysyłane.

---

## Szybki start

| System | Jak uruchomić |
|---|---|
| **macOS** | Dwuklik `start.command` (albo w terminalu: `./start.command`) |
| **Windows** | Dwuklik `start-windows.bat` |
| **Linux** | `./start-linux.sh` |
| każdy | `python3 -m oiforge` (opcje: `--port 8777`, `--browser`, `--no-browser`, `--no-warmup`) |

Program otwiera się w osobnym oknie przeglądarki (Chrome/Edge bez paska adresu) pod adresem `http://127.0.0.1:8777`.
Zamykasz go, wciskając `Ctrl+C` w terminalu.

### Wymagania
- **Python 3.9+** — bez żadnych dodatkowych paczek.
- **Kompilator C++**:
  - macOS: `brew install gcc` (zalecane, jak na OI) albo `xcode-select --install` (clang),
  - Linux: `sudo apt install g++ gdb`,
  - Windows: [MSYS2](https://www.msys2.org) → `pacman -S mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-gdb`
    (dodaj `C:\msys64\ucrt64\bin` do PATH) albo WinLibs.
- Opcjonalnie: **clang + lldb** (sanitizery i debugger na macOS — z Xcode Command Line Tools), **gdb** (debugger
  na Linuksie i Windows), **Chrome/Edge/Chromium** (osobne okno; bez nich otworzy się zwykła przeglądarka).

Na stronie **Ustawienia → System** zobaczysz, co zostało wykryte na Twoim komputerze.

---

## Jak to wygląda

OI Forge jest do rozwiązywania zadań. Otwiera się od razu w **edytorze projektów**: drzewo plików, karty, „Uruchom”,
wejście i wyjście, podpowiedzi z bieżącego kodu. Wejście podajesz na dwa sposoby: **Dane z góry** (całe wejście przed
uruchomieniem) albo **Terminal** (program czeka, aż wpiszesz dane, jak w konsoli). Po każdym „Uruchom” program jest też
sprawdzany w tle sanitizerami — wyjście poza tablicę albo vector pokazuje się od razu, z numerem linii.

| | |
|---|---|
| **Kod** | edytor — najważniejsze narzędzia są w nim pod przyciskiem „Narzędzia” |
| **Zadanie** | wklejasz treść raz: o co chodzi, jaki algorytm, testy brzegowe, auto-solver |
| **Testy** | rozwiązanie kontra brute na tysiącach testów, generatory, porównanie wyjść |
| **Szukaj** | ściągawka: STL, algorytmy, snippety, asystent, Twoje programy |
| **Programy** | Twoje zaimportowane rozwiązania |
| **Narzędzia** | wszystko na jednej stronie (także trening: symulacja etapu, fiszki, ścieżka, dziennik) |

**★ = działa pewnie.** Gwiazdkę mają narzędzia sprawdzone na przykładach ze znaną odpowiedzią i na prawdziwych
programach olimpijskich (lista z uzasadnieniem: `web/js/quality.js`). Bez gwiazdki są trzy narzędzia, które
zgadują — auto-solver (ok. 1 na 4 prawdziwe zadania), doradca algorytmów (54% trafień, 77% w pierwszej trójce)
i testy brzegowe (97% testów poprawnych). Działają, ale ich wynik trzeba sprawdzić.

## Narzędzia

**Asystent**
- **Asystent** — pytasz jak czatbota („set cpp”, „jak zaokrąglić do 2 miejsc”, „binserch”, „dijkstra kod”,
  „multiset vs set”), a dostajesz odpowiedź z kodem, złożonością, pułapkami i powiązanymi tematami. Pamięta kontekst
  rozmowy („a jak usunąć?”), poprawia literówki i pokazuje Twoje własne rozwiązania na ten temat.
  - **Uruchamia kod w piaskownicy**: napisz „co wypisze cout << 'a' + 1;” albo wklej program (dane w linii `wejście: …`),
    a asystent go skompiluje, uruchomi (bez sieci, zapis plików tylko w katalogu tymczasowym, limit czasu i pamięci),
    pokaże wynik i wyjaśni niespodzianki (np. że `'a' + 1` to kod ASCII 98). Każdy przykład w odpowiedzi ma przycisk ▶.
  - **Komendy** (wpisz `/`): `/uruchom`, `/sprawdz`, `/zlozonosc`, `/wyjasnij`, `/formatuj`, `/optymalizuj`, `/py2cpp`,
    `/blad`, `/zadanie`, `/algorytm`, `/testy`, `/rozwiaz`, `/programy`, `/docs`, `/pomoc` i `/clear` (czyści rozmowę
    i kontekst). Asystent korzysta tu ze wszystkich narzędzi OI Forge.
  - **„Nie rozumiem”** — wraca do poprzedniego tematu i tłumaczy prościej: analogia, jedno zdanie, kroki, najprostszy
    przykład uruchomiony w piaskownicy; za drugim razem inne przykłady i prostsze tematy na start. „Pokaż przykład”
    i „krok po kroku” też działają.
  - **Personalizacja bez AI** — pamięta (lokalnie, w `user_data/store`), o co pytasz i czego nie rozumiałeś: przy
    powrocie do trudnego tematu zaczyna od najprostszego wyjaśnienia, ostrzega przed tematami dużo powyżej Twojego
    poziomu i — jeśli piszesz głównie w Pythonie — dokłada przykłady w Pythonie.
- **Dokumentacja** — lokalna baza wiedzy: 370 artykułów, 3326 pytań z odpowiedziami i 1726 opisanych funkcji.
  C++ od podstaw (pętle, warunki, funkcje, tablice, operatory, rzutowanie), znaki i kody ASCII, STL, I/O, liczby,
  pułapki, praktyka OI (także zadania interaktywne i Python na zawodach), grafy, drzewa, struktury danych, techniki,
  DP, teksty, matematyka (z FFT/NTT, eliminacją Gaussa, bazą XOR, Möbiusem), geometria i gry. Do tego pełna
  dokumentacja: `<algorithm>`, `<numeric>`, `<ranges>`, `<cmath>`, `<cstring>`, `<iomanip>`, `<sstream>`, `<chrono>`,
  `<bit>`, `<climits>` i wszystkie metody kontenerów (vector, string, set, map, unordered, deque/list, adaptery, bitset).
- **Snippety** — wpisz `seg`, `dij`, `bs`, `lca`, `dinic`, `kmp`… w dowolnym edytorze i wciśnij `Tab`
  (58 przetestowanych implementacji plus Twoje własne).

**Kod**
- **C++ Playground** — fragment kodu bez `main`; szkielet programu dopisuje się sam, a wypisywanie wyrażeń działa jak w REPL.
- **Brudnopis** — wiele kart roboczych C++/Python bez tworzenia plików.
- **Sprawdzarka kodu** — 77 reguł statycznych (overflow, `=` w `if`, komparator `<=`, `size()-1`, multitest bez
  czyszczenia…) oraz uruchomienie z AddressSanitizerem/UBSan i porównaniem wariantów kompilacji, które wykrywa UB
  i niezainicjalizowane zmienne.
- **Tłumacz błędów C++** — 132 wzorce komunikatów GCC/clang/linkera po polsku, bez spamu szablonów.
- **Wizualny debugger** — program linia po linii: zmienne, tablice ze wskaźnikami `i`, `l`, `r`, `mid`,
  macierze jako mapa ciepła, stos rekurencji, przewijanie w przód i w tył.
- **Objaśniacz kodu**, **Optymalizator** (szybkie I/O, `endl`, `const &`… z benchmarkiem) i
  **Analiza złożoności** (statyczna i empiryczna, z werdyktem dla limitów zadania).
- **Upiększacz kodu / formatter** — 6 stylów, w tym „moja”, którego program uczy się z Twoich zaimportowanych programów. Edytor formatuje kod skrótem `Ctrl+Shift+F`.
- **Tłumacz Python → C++** — inferencja typów, `heapq`, `bisect`, f-stringi, `//` i `%` z semantyką Pythona,
  sprawdzanie równoważności wyjść.

**Testowanie**
- **Testerka** — Twoje rozwiązanie kontra brute. Generator wypisuje N testów oddzielonych pustą linią, testy idą
  równolegle, a przy błędzie testerka znajduje najmniejszy kontrprzykład.
- **Auto-testerka** — rozpoznaje format wejścia z przykładów i sama generuje testy oraz shrinking.
- **Porównanie wyjść** — pierwsza różnica (linia, znak, token) i widok obok siebie.
- **Generator testów (DSL)** — `n = int(1, 10)`, `a = array(n, int(-5, 5))`, `tree(n)`, `graph(n, m)`…,
  z eksportem do C++/Pythona.
- **Edytor grafów** — rysujesz myszą, a obok powstaje wejście.
- **Porównanie rozwiązań** (benchmark z wykresem) i **Inspektor wejścia/wyjścia**: tablice, siatki, grafy i drzewa narysowane z testu.

**Zadania** (wklejasz treść albo PDF)
- **Objaśniacz treści**, **Doradca algorytmów** (dopuszczalna złożoność z ograniczeń + klasyfikator uczony na Twoich
  zadaniach), **Testy brzegowe** i **Auto-solver** (szablony, Twoje programy i synteza programów — wszystko
  sprawdzane na przykładach z treści i na losowych testach; gdy nic pewnego nie pasuje, dostajesz gotowe wczytywanie
  danych w formacie z treści, nigdy kod z TODO). Wynik auto-solvera możesz od razu „Otworzyć w edytorze”.

**Moje programy**
- Twoje rozwiązania (z Szkopułu, dowolnej strony SIO2 albo z folderu) i wyszukiwarka, która rozumie zapytania typu
  „binserch”, „dikstra”, „drzewo przedziałowe”, `lower_bound` czy „najkrótsza ścieżka”: szuka po temacie rundy, kodzie
  i treści zadania.

**Trening**
- **Tryb zawodów** (5 h, statusy zadań, notatki, przypomnienia, checklista), **Dziennik postępów**, **Fiszki**
  (SM-2, ok. 2900 kart), **Ścieżka nauki** (Podstawy → I → II → III etap) i zadanie dnia na stronie głównej.

**Narzędzia**
- **Wizualizacje algorytmów** (27 animacji: sortowania, binsearch, BFS/DFS w labiryncie, Dijkstra, DSU, drzewo
  przedziałowe, Fenwick, KMP, plecak, LCS…), **Kalkulatory** (modulo, nCr, rozkład na czynniki do 10¹⁸, bity,
  systemy liczbowe, limity → złożoność, pamięć tablic), **Ustawienia**.

Paleta komend: `Ctrl+K` / `⌘K`. Edytor: `Ctrl+Enter` uruchamia, `Tab` rozwija snippet, `Ctrl+Shift+F` formatuje.

---

## Gdzie są dane
- `user_data/` — Twoje zaimportowane programy (`programs/`), ustawienia, dziennik, projekty w edytorze. To dane prywatne:
  nie trafiają do repozytorium (są w `.gitignore`) i program nigdzie ich nie wysyła.
- Cache (skompilowane programy, indeksy, modele wyszukiwania) jest poza projektem: macOS `~/Library/Caches/OIForge`,
  Linux `~/.cache/oiforge`, Windows `%LOCALAPPDATA%\OIForge\Cache`. Odbudowuje się sam, ma limit ok. 1,5 GB i sam
  usuwa najstarsze pliki; wyczyścisz go też w Ustawieniach.

---

## Licencja

OI Forge jest udostępniony na **licencji OI Forge License 1.0** (plik [`LICENSE`](LICENSE), po polsku i po angielsku).
Kod źródłowy jest jawny (*source-available*), ale **to nie jest licencja open source**:

- **wolno** za darmo: pobrać, uruchamiać, czytać i zmieniać kod na własny, prywatny użytek osoby fizycznej oraz
  przekazywać innym osobom prywatnym wierne, niezmienione kopie (z licencją);
- **nie wolno** bez pisemnej zgody autora: używać go w firmie, szkole, na uczelni ani w żadnej innej organizacji,
  używać komercyjnie, sprzedawać, publikować zmienionych wersji, udostępniać jako usługi online ani używać bazy wiedzy
  i szablonów do trenowania innych narzędzi czy modeli.

Organizacje mogą poprosić o osobną licencję. Komponenty zewnętrzne (CodeMirror, pdf.js, fonty, ikony) mają własne
licencje — zobacz [`THIRD-PARTY.md`](THIRD-PARTY.md).

Copyright © 2026 Filip Biskupski.

---

## Dla programisty
- Architektura i kontrakty modułów: [`docs/CONTRACT.md`](docs/CONTRACT.md). Backend to czysty stdlib Pythona
  (`oiforge/`), frontend to vanilla JS + CodeMirror 5 (`web/`), a baza wiedzy leży w `data/kb/*.json`.
- Testy: `python3 -m unittest discover -s tests -t .` (testy, które potrzebują zaimportowanych programów, same się
  pomijają, gdy ich nie ma).
- Walidacja bazy wiedzy (kompiluje każdy fragment kodu): `python3 scripts/validate_kb.py`.
- Pełna kontrola jakości: `python3 scripts/check_all.py` (testy, baza wiedzy, składnia JS, start serwera i widoki).

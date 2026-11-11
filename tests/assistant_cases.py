"""Duży zestaw regresji jakości asystenta: realistyczne pytania ucznia + akceptowalne artykuły.

Format: (pytanie, [akceptowalne id artykułów], opcje) — opcje (słownik, opcjonalny):
- "intent": oczekiwana intencja (code / define / compare / complexity / howto),
- "code": True — odpowiedź MUSI zawierać blok kodu (pytania „jak…”, „kod…”).

Pytania są pisane jak przez ucznia: potocznie, często bez ogonków, z literówkami, krótkie i długie.
Używane przez tests/test_assistant.py (progi top-1 / top-3) i do ręcznego pomiaru (`python3 -m tests.assistant_cases`).
"""
from __future__ import annotations

C = {"code": True}

CASES = [
    # ---------------------------------------------------------------- STL: czynność + obiekt
    ("jak posortować vector par po drugim elemencie", ["stl-sort", "pit-comparator"], C),
    ("jak posortowac pary po second malejaco", ["stl-sort", "pit-comparator"], C),
    ("sortowanie vectora malejaco", ["stl-sort", "pit-comparator"], C),
    ("jak posortowac tablice struktur po jednym polu", ["stl-sort", "pit-comparator", "cpp-struct",
                                                        "cpp-operator-overloading"], C),
    ("jak posortować stringa alfabetycznie", ["stl-sort", "stl-string", "stl-string-methods"], C),
    ("sortowanie wektora stringow po dlugosci", ["stl-sort", "pit-comparator", "str-basic-lex-compare"], C),
    ("jak posortowac tablice od 1 do n", ["stl-sort"], C),
    ("jak posortowac mape po wartosciach", ["stl-map", "stl-sort"], C),
    ("jak usunac element z vectora", ["stl-vector", "stl-remove-erase"], C),
    ("jak usunac wszystkie zera z vectora", ["stl-remove-erase", "stl-vector"], C),
    ("mam vector intow i chce usunac z niego wszystkie liczby parzyste jak to zrobic najszybciej",
     ["stl-remove-erase", "pit-iterator-erase", "stl-vector"], C),
    ("usuwanie duplikatow z wektora", ["stl-unique"], C),
    ("jak usunąć element z seta", ["stl-set"], C),
    ("jak usunac jeden element z multiseta", ["stl-multiset"], C),
    ("usuwanie klucza z mapy", ["stl-map"], C),
    ("jak usunac znak ze stringa", ["stl-string-methods", "stl-string", "str-basic-string-ops", "stl-remove-erase"], C),
    ("jak wstawic element na poczatek vectora", ["stl-vector", "stl-deque"], C),
    ("wstawianie do mapy", ["stl-map", "pit-map-bracket"], C),
    ("jak dodac element do seta", ["stl-set"], C),
    ("jak sprawdzic czy element jest w secie", ["stl-set"], C),
    ("jak sprawdzic czy klucz jest w mapie", ["stl-map", "pit-map-bracket"], C),
    ("jak znalezc element w vectorze", ["stl-count-find", "stl-vector"], C),
    ("szukanie podciagu w stringu", ["stl-string-methods", "stl-string", "str-basic-string-ops"], C),
    ("jak odwrocic vector", ["stl-reverse-rotate"], C),
    ("jak odwrocic stringa", ["str-basic-reverse", "stl-reverse-rotate"], C),
    ("jak policzyc ile razy wystepuje liczba w vectorze", ["stl-count-find", "stl-binary-search"], C),
    ("zliczanie liter w napisie", ["str-basic-count-letters"], C),
    ("jak zliczyc wystapienia slow w tekscie", ["stl-map", "pit-map-bracket", "stl-unordered-map",
                                                "str-basic-split"], C),
    ("tablica dwuwymiarowa vector", ["stl-vector-2d"], C),
    ("jak zrobic vector 2d o wymiarach n na m", ["stl-vector-2d"], C),
    ("suma elementow vectora", ["stl-accumulate"], C),
    ("max element w vectorze", ["stl-min-max"], C),
    ("ostatni element seta", ["stl-set", "stl-iterators"], C),
    ("najmniejszy element w multisecie", ["stl-multiset", "stl-set"]),
    ("lower_bound na mapie", ["stl-map"]),
    ("kolejka priorytetowa min", ["stl-priority-queue", "ds-heap"]),
    ("priority queue z parami", ["stl-priority-queue", "stl-pair"]),
    ("stos c++", ["stl-stack"]),
    ("deque push_front", ["stl-deque"]),
    ("bitset count", ["stl-bitset"]),
    ("wszystkie permutacje next_permutation", ["stl-permutations"], C),
    ("jak wypełnić tablicę zerami", ["stl-memset", "stl-iota-fill"], C),
    ("jak zamienic dwie zmienne", ["stl-swap"]),
    ("przetasowac vector losowo", ["stl-shuffle-random", "num-random"]),
    ("maksymalna wartosc inta", ["stl-numeric-limits", "num-int-types", "num-inf-constants"]),
    ("popcount", ["stl-builtin-bits"]),
    ("polacz dwa posortowane vectory", ["stl-merge"]),
    ("czesc wspolna dwoch zbiorow", ["stl-set-operations"]),
    # ---------------------------------------------------------------- wejście / wyjście
    ("jak wczytac linie ze spacjami", ["io-getline"], C),
    ("getline nie dziala po cin", ["io-getline"]),
    ("szybkie wczytywanie danych", ["io-fast-io", "io-custom-fast-read"]),
    ("jak wczytac nieznana liczbe liczb", ["io-read-until-eof"], C),
    ("wczytywanie planszy z kropkami i krzyzykami", ["io-read-grid"]),
    ("jak wczytac liczby oddzielone przecinkami", ["io-read-separated"]),
    ("wypisywanie z dokładnością do 6 miejsc", ["io-precision", "num-rounding"]),
    ("jak wypisac liczbe z zerami na poczatku", ["io-setw-setfill"]),
    ("printf long long format", ["io-printf-formats"]),
    ("jak wypisac elementy oddzielone spacja bez spacji na koncu", ["io-print-separators"]),
    ("jak wypisac TAK lub NIE", ["io-print-bool"]),
    ("wypisz liczbe binarnie", ["io-hex-bin", "math-bases", "stl-bitset"]),
    ("freopen plik wejsciowy", ["io-freopen", "tool-redirect-io"]),
    ("endl czy \\n", ["pit-endl-tle", "io-fast-io"]),
    # ---------------------------------------------------------------- zaokrąglanie, typy, overflow
    ("jak zaokraglic liczbe do calkowitej", ["num-rounding"]),
    ("zaokraglanie w gore przy dzieleniu calkowitym", ["num-ceil-div", "num-rounding"], C),
    ("ceil z dzielenia intow", ["num-ceil-div", "num-rounding"]),
    ("zaokrąglenie do 2 miejsc po przecinku", ["num-rounding", "io-precision"], C),
    ("dlaczego 0.1+0.2 nie jest rowne 0.3", ["num-floating", "pit-double-equal"]),
    ("porownywanie doubli", ["pit-double-equal", "num-floating"]),
    ("int czy long long", ["num-int-types"]),
    ("przepelnienie przy mnozeniu dwoch intow", ["pit-int-overflow", "num-overflow"]),
    ("liczba wieksza niz 10^18", ["num-int128", "math-bignum", "num-bigint-strings"]),
    ("petla do v.size()-1 dla pustego vectora", ["pit-unsigned-size"]),
    ("abs dla long long", ["pit-abs-long-long"]),
    ("1 << 40 nie dziala", ["pit-bit-ops"]),
    ("konwersja string na int", ["num-conversions"], C),
    ("int na string", ["num-conversions", "stl-string"], C),
    ("pierwiastek z duzej liczby dokladnie", ["num-sqrt-pow"]),
    ("losowe liczby mt19937", ["num-random", "stl-shuffle-random"]),
    ("log2 liczby calkowitej", ["num-log2", "stl-builtin-bits"]),
    ("jaka wartosc nieskonczonosci wybrac", ["num-inf-constants"]),
    # ---------------------------------------------------------------- modulo
    ("modulo z liczby ujemnej", ["num-negative-mod"]),
    ("dlaczego -7 % 3 daje -1", ["num-negative-mod"]),
    ("dzielenie modulo", ["math-mod-inverse", "num-mod-arithmetic"]),
    ("mnozenie modulo 10^9+7 przepelnienie", ["num-mod-arithmetic", "pit-int-overflow"]),
    ("a do potegi b modulo m", ["math-fast-pow"], C),
    ("odwrotnosc modularna", ["math-mod-inverse"]),
    # ---------------------------------------------------------------- pułapki
    ("niezainicjowana zmienna losowe wyniki", ["pit-uninitialized"]),
    ("if (x = 5) zawsze prawda", ["pit-assign-vs-compare"]),
    ("wyjscie poza tablice", ["pit-out-of-bounds"]),
    ("segfault przy duzej tablicy w main", ["pit-stack-overflow", "cpp-stack-overflow", "cpp-global-vs-local"]),
    ("memset 1 nie dziala", ["pit-memset", "stl-memset"]),
    ("vector<bool> dziwnie sie zachowuje", ["pit-vector-bool"]),
    ("dzielenie intow daje 0", ["pit-integer-division"]),
    ("zmienna y1 nie kompiluje", ["pit-name-collisions", "cpp-bits-stdcpp"]),
    ("blad o jeden w petli", ["pit-off-by-one"]),
    ("wiele testow czyszczenie tablic", ["pit-multitest-reset"]),
    ("map[] dodaje elementy", ["pit-map-bracket", "stl-map"]),
    ("sort sie wywala z komparatorem <=", ["pit-comparator", "stl-sort"]),
    ("crash po erase w petli po secie", ["pit-iterator-erase"]),
    # ---------------------------------------------------------------- błędy kompilacji (opisane słowami)
    ("no matching function for call to max", ["pit-abs-long-long", "stl-min-max"]),
    ("reference to next is ambiguous", ["pit-name-collisions"]),
    ("jak czytac bledy kompilatora", ["oi-debugging", "tool-compile-terminal", "oi-compile-flags"]),
    ("unordered_map z para jako kluczem nie kompiluje", ["stl-unordered-map", "ds-custom-hash"]),
    # ---------------------------------------------------------------- grafy
    ("bfs kod", ["graph-bfs"], {"intent": "code", "code": True}),
    ("dfs rekurencyjnie", ["graph-dfs"]),
    ("najkrotsza sciezka w grafie bez wag", ["graph-bfs"]),
    ("najkrotsza droga w grafie z wagami", ["graph-dijkstra"]),
    ("dijkstra z kolejka priorytetowa", ["graph-dijkstra", "stl-priority-queue"]),
    ("ujemne wagi najkrotsza sciezka", ["graph-bellman-ford", "graph-spfa"]),
    ("najkrotsze sciezki miedzy wszystkimi parami", ["graph-floyd-warshall"]),
    ("spojne skladowe grafu", ["graph-connected-components", "graph-dsu"]),
    ("minimalne drzewo rozpinajace kruskal", ["graph-mst-kruskal"]),
    ("toposort kahn", ["graph-toposort"]),
    ("silnie spojne skladowe", ["graph-scc"]),
    ("mosty w grafie", ["graph-bridges-articulation"]),
    ("bfs 0-1", ["graph-01-bfs"]),
    ("maksymalny przeplyw", ["graph-max-flow"]),
    ("skojarzenie w grafie dwudzielnym", ["graph-bipartite-matching"]),
    ("cykl eulera", ["graph-euler"]),
    ("macierz sasiedztwa czy lista sasiedztwa", ["graph-representation"]),
    ("jak wczytac graf z krawedziami", ["graph-input", "graph-representation"], C),
    ("napisz bfs na siatce", ["graph-bfs-grid"], {"intent": "code", "code": True}),
    # ---------------------------------------------------------------- drzewa
    ("lca kod", ["tree-lca", "tree-lca-euler"], {"intent": "code", "code": True}),
    ("srednica drzewa", ["tree-diameter"]),
    ("rozmiary poddrzew", ["tree-basics", "tree-dp", "tree-dfs-order"]),
    ("drzewo z tablicy ojcow", ["tree-from-parents"]),
    ("centroid drzewa", ["tree-centroid"]),
    ("heavy light decomposition", ["tree-hld"]),
    ("odleglosc miedzy dwoma wierzcholkami w drzewie", ["tree-distance", "tree-lca"]),
    # ---------------------------------------------------------------- DP
    ("co to jest programowanie dynamiczne", ["dp-intro"], {"intent": "define"}),
    ("plecak kod", ["dp-knapsack-01", "dp-knapsack-unbounded"], {"intent": "code", "code": True}),
    ("na ile sposobow mozna wejsc po schodach", ["dp-stairs-fibonacci"]),
    ("najdluzszy rosnacy podciag", ["dp-lis"]),
    ("lcs dwoch slow", ["dp-lcs"]),
    ("odleglosc edycyjna", ["dp-edit-distance"]),
    ("dp na maskach bitowych", ["dp-bitmask"]),
    ("liczba sciezek w siatce", ["dp-grid-paths"]),
    ("dp po cyfrach", ["dp-digit"]),
    ("wydawanie reszty najmniejsza liczba monet", ["dp-coin-change"]),
    # ---------------------------------------------------------------- teksty
    ("kmp kod", ["str-kmp"], {"intent": "code", "code": True}),
    ("funkcja z", ["str-z-function"]),
    ("sprawdz czy slowo jest palindromem", ["str-basic-palindrome", "str-palindromes"], C),
    ("najdluzszy palindrom w napisie", ["str-manacher", "str-palindromes"]),
    ("podzial napisu na slowa", ["str-basic-split"], C),
    ("szyfr cezara", ["str-basic-caesar"]),
    ("hashowanie napisow", ["str-hashing", "ds-string-hashing"]),
    ("tablica sufiksowa", ["str-suffix-array"]),
    ("zamiana wielkich liter na male", ["str-basic-char-ops", "stl-string-methods"], C),
    ("drzewo trie", ["ds-trie", "str-trie"]),
    # ---------------------------------------------------------------- matematyka
    ("sito eratostenesa kod", ["math-sieve"], {"intent": "code", "code": True}),
    ("jak sprawdzic czy liczba jest pierwsza", ["math-primality", "math-sieve"], C),
    ("rozklad na czynniki pierwsze", ["math-factorization"]),
    ("wszystkie dzielniki liczby", ["math-divisors"]),
    ("nwd i nww", ["math-gcd-lcm", "stl-gcd-lcm"]),
    ("symbol newtona modulo", ["math-binomial"]),
    ("funkcja eulera phi", ["math-euler-phi"]),
    ("chinskie twierdzenie o resztach", ["math-crt"]),
    ("zamiana liczby z dziesietnego na binarny", ["math-bases", "io-hex-bin"]),
    ("rozszerzony algorytm euklidesa", ["math-extended-euclid"]),
    ("fibonacci szybko potegowanie macierzy", ["math-matrix-pow"]),
    # ---------------------------------------------------------------- struktury danych
    ("drzewo przedzialowe kod", ["ds-segment-tree", "ds-segment-tree-iterative"], {"intent": "code", "code": True}),
    ("drzewo fenwicka", ["ds-fenwick"]),
    ("sumy prefiksowe 2d", ["ds-prefix-sums-2d"]),
    ("tablica roznicowa", ["ds-difference-array"]),
    ("sparse table rmq", ["ds-sparse-table"]),
    ("find union kod", ["graph-dsu"], {"intent": "code", "code": True}),
    ("stos monotoniczny", ["ds-monotonic-stack"]),
    ("ordered set k-ty element", ["ds-ordered-set", "cpp-pbds-ordered-set"]),
    # ---------------------------------------------------------------- techniki
    ("wyszukiwanie binarne po wyniku", ["tech-binary-search-answer"]),
    ("binsearch na liczbach rzeczywistych", ["tech-binary-search-real"]),
    ("gasienica dwa wskazniki", ["tech-two-pointers"]),
    ("zamiatanie", ["tech-sweep-line"]),
    ("skalowanie wspolrzednych", ["tech-coordinate-compression"]),
    ("meet in the middle", ["tech-meet-in-the-middle"]),
    ("kiedy dziala algorytm zachlanny", ["tech-greedy"]),
    ("generowanie wszystkich podzbiorow", ["tech-bitmask-brute-force", "tech-backtracking"], C),
    # ---------------------------------------------------------------- „co to jest X”
    ("co to jest kopiec", ["ds-heap", "stl-priority-queue"], {"intent": "define"}),
    ("co to jest graf dwudzielny", ["graph-bipartite"], {"intent": "define"}),
    ("co to lambda w c++", ["cpp-lambdas"], {"intent": "define"}),
    ("co to jest referencja", ["cpp-references"], {"intent": "define"}),
    ("co to jest iterator", ["stl-iterators"], {"intent": "define"}),
    ("czym jest struct", ["cpp-struct"], {"intent": "define"}),
    # ---------------------------------------------------------------- „X vs Y”
    ("map vs unordered_map", ["stl-map", "stl-unordered-map"], {"intent": "compare"}),
    ("roznica miedzy set a unordered_set", ["stl-set", "stl-unordered-set"], {"intent": "compare"}),
    ("dijkstra vs bellman ford", ["graph-dijkstra", "graph-bellman-ford"], {"intent": "compare"}),
    ("fenwick czy drzewo przedzialowe", ["ds-fenwick", "ds-segment-tree"]),
    ("kruskal vs prim", ["graph-mst-kruskal", "graph-mst-prim"], {"intent": "compare"}),
    ("lower_bound vs upper_bound", ["stl-binary-search"]),
    # ---------------------------------------------------------------- „złożoność X”
    ("zlozonosc sortowania", ["stl-sort"], {"intent": "complexity"}),
    ("złożoność bfs", ["graph-bfs"], {"intent": "complexity"}),
    ("zlozonosc operacji na secie", ["stl-set"], {"intent": "complexity"}),
    ("zlozonosc unordered_map", ["stl-unordered-map"], {"intent": "complexity"}),
    ("jaka zlozonosc dla n do 10^5", ["oi-complexity-limits"]),
    # ---------------------------------------------------------------- „kod X”
    ("kod dijkstry", ["graph-dijkstra"], {"intent": "code", "code": True}),
    ("szablon programu na oi", ["oi-template"], C),
    ("pokaz kod szybkiego potegowania", ["math-fast-pow"], {"intent": "code", "code": True}),
    # ---------------------------------------------------------------- literówki
    ("dijsktra", ["graph-dijkstra"]),
    ("sortowanei malejaco", ["stl-sort", "pit-comparator"]),
    ("wyszukiwanie binrane", ["tech-binary-search", "stl-binary-search"]),
    ("unordred map", ["stl-unordered-map"]),
    ("fenwik", ["ds-fenwick"]),
    ("kolejka priorytetowo", ["stl-priority-queue", "ds-heap"]),
    ("zaokraglenei w gore", ["num-ceil-div", "num-rounding"]),
    ("drzewo przedzialowe z lazy propagacja", ["ds-lazy-propagation"]),
    # ---------------------------------------------------------------- OI praktyka / narzędzia
    ("ile operacji na sekunde", ["oi-complexity-limits"]),
    ("jak napisac generator testow i brute", ["oi-brute-generator", "oi-stress-test"]),
    ("jak porownac wyjscie dwoch programow", ["tool-diff-files", "oi-stress-test"]),
    ("jak zmierzyc czas dzialania programu", ["tool-measure-time"]),
    ("ile pamieci zajmuje tablica intow 10^7", ["oi-memory", "num-int-types"]),
    # ---------------------------------------------------------------- potoczne, bez nazw tematów (trudne)
    ("mam pary liczb i chce je ustawic wedlug drugiej liczby", ["stl-sort", "pit-comparator"], C),
    ("jak zrobic zeby sort sortowal od najwiekszego", ["stl-sort", "pit-comparator"], C),
    ("chce posortowac punkty najpierw po x a potem po y", ["stl-sort", "pit-comparator", "stl-tuple",
                                                          "geo-point-vector"], C),
    ("jak wyrzucic powtarzajace sie liczby z tablicy", ["stl-unique"], C),
    ("jak sprawdzic czy w vectorze jest jakas liczba", ["stl-count-find", "stl-vector"], C),
    ("jak wziac najwiekszy element z seta", ["stl-set", "stl-iterators"], C),
    ("struktura ktora trzyma liczby posortowane i pozwala szybko usuwac", ["stl-set", "stl-multiset"]),
    ("jak policzyc ile razy wystapila kazda liczba", ["stl-map", "pit-map-bracket", "stl-unordered-map",
                                                     "stl-count-find"], C),
    ("jak odwrocic kolejnosc elementow w tablicy", ["stl-reverse-rotate"], C),
    ("przesunac tablice cyklicznie o k miejsc", ["stl-reverse-rotate"], C),
    ("jak wczytac caly wiersz tekstu razem ze spacjami", ["io-getline"], C),
    ("program bardzo wolno wczytuje dane co zrobic", ["io-fast-io", "io-custom-fast-read"]),
    ("nie wiem ile bedzie liczb na wejsciu jak je wczytac", ["io-read-until-eof"], C),
    ("jak wypisac wynik z dwoma miejscami po kropce", ["io-precision", "num-rounding"], C),
    ("wynik wychodzi ujemny a powinien byc dodatni przy duzych liczbach", ["pit-int-overflow", "num-overflow"]),
    ("reszta z dzielenia wychodzi ujemna", ["num-negative-mod"]),
    ("jak podzielic dwie liczby i zaokraglic w gore bez double", ["num-ceil-div", "num-rounding"], C),
    ("program sie wywala gdy robie duza tablice w funkcji", ["pit-stack-overflow", "cpp-stack-overflow",
                                                             "cpp-global-vs-local"]),
    ("dostaje rozne wyniki przy kazdym uruchomieniu", ["pit-uninitialized", "pit-out-of-bounds"]),
    ("jak znalezc najkrotsza droge w labiryncie", ["graph-bfs-grid", "graph-bfs"], C),
    ("jak sprawdzic czy z jednego miasta da sie dojechac do drugiego", ["graph-connected-components", "graph-dsu",
                                                                        "graph-bfs", "graph-dfs"]),
    ("jak policzyc ile jest wysp na mapie", ["graph-bfs-grid", "graph-connected-components", "graph-dfs"], C),
    ("kolejnosc wykonywania zadan gdy jedne zaleza od drugich", ["graph-toposort"]),
    ("jak znalezc cykl w grafie", ["graph-cycle-detection"]),
    ("najtansze polaczenie wszystkich miast drogami", ["graph-mst-kruskal", "graph-mst-prim"]),
    ("suma na przedziale wiele zapytan bez zmian w tablicy", ["ds-prefix-sums"], C),
    ("dodawanie wartosci na przedzialach a na koncu wypisanie tablicy", ["ds-difference-array"]),
    ("minimum na przedziale ze zmianami elementow", ["ds-segment-tree", "ds-segment-tree-iterative"]),
    ("dla kazdego elementu najblizszy mniejszy po lewej", ["ds-monotonic-stack"]),
    ("jak szybko sprawdzac czy dwa fragmenty napisu sa rowne", ["str-hashing", "ds-string-hashing"]),
    ("ile razy wzorzec wystepuje w tekscie", ["str-kmp", "str-z-function", "str-hashing"]),
    ("wybrac przedmioty o sumie wag nie wiekszej niz W i najwiekszej wartosci", ["dp-knapsack-01"]),
    ("ile jest sposobow zeby rozmienic kwote monetami", ["dp-coin-change"]),
    ("jak policzyc n po k dla duzych liczb modulo", ["math-binomial"]),
    ("liczby pierwsze do 10^7", ["math-sieve"]),
    ("najmniejsza wspolna wielokrotnosc", ["math-gcd-lcm", "stl-gcd-lcm"]),
    ("jak szukac odpowiedzi binarnie gdy warunek jest monotoniczny", ["tech-binary-search-answer",
                                                                     "tech-binary-search"]),
    ("najkrotszy fragment tablicy o sumie co najmniej s", ["tech-two-pointers", "tech-sliding-window"]),
    ("sprawdzanie wszystkich podzbiorow dla n do 20", ["tech-bitmask-brute-force", "dp-bitmask",
                                                       "oi-complexity-limits"]),
    ("wartosci do 10^9 a chce ich uzyc jako indeksy tablicy", ["tech-coordinate-compression"]),
    ("rekurencja z zapamietywaniem wynikow", ["dp-intro", "dp-stairs-fibonacci"]),
    ("jak napisac lambde do sorta", ["stl-sort", "cpp-lambdas"], C),
    ("jak przekazac vector do funkcji zeby sie nie kopiowal", ["cpp-references"], C),
    ("co zrobic zeby zmienna byla widoczna w kazdej funkcji", ["cpp-global-vs-local"]),
    ("co oznacza auto w c++", ["cpp-auto-range-for"]),
    ("petla po wszystkich elementach mapy", ["stl-map", "cpp-auto-range-for"], C),
    ("kto wygra gre w zabieranie zapalek", ["game-nim", "game-sprague-grundy", "dp-games", "game-graph-games"]),
    ("czy dwa odcinki sie przecinaja", ["geo-segment-intersection"]),
    ("czy punkt jest po lewej czy po prawej stronie prostej", ["geo-orientation", "geo-cross-dot"]),
    ("pole wielokata ze wspolrzednych wierzcholkow", ["geo-polygon-area"]),
    ("wierzcholek ktorego usuniecie rozspojnia graf", ["graph-bridges-articulation"]),
    ("jak skompilowac program w terminalu", ["tool-compile-terminal"]),
    ("jakich flag kompilacji uzywac", ["oi-compile-flags", "tool-compile-terminal"]),
    ("jak zamienic cyfre zapisana jako char na int", ["num-conversions", "str-basic-char-ops"], C),
    ("jak sprawdzic czy znak jest litera", ["str-basic-char-ops"], C),
    ("suma cyfr liczby", ["math-bases", "num-log2"], C),
    ("jak sprawdzic czy liczba jest potega dwojki", ["tech-bit-tricks", "stl-builtin-bits", "pit-bit-ops"]),
    ("xor na przedziale", ["tech-prefix-xor"]),
    ("dwie liczby w tablicy o sumie k", ["tech-two-sum-hashmap", "tech-two-pointers"]),
    ("losowa liczba z przedzialu od a do b", ["num-random", "stl-shuffle-random"], C),
]

# Zestaw KONTROLNY (held-out): sformułowania pisane po zakończeniu strojenia rankingu i bazy wiedzy — nie dopisywano
# pod nie wariantów QA. Mierzy, czy poprawki uogólniają się na nowe pytania (wynik raportowany osobno).
HELDOUT = [
    ("jak ułożyć liczby w vectorze od największej do najmniejszej", ["stl-sort", "pit-comparator"]),
    ("sortowanie tablicy par wedlug pierwszego a potem drugiego", ["stl-sort", "pit-comparator", "stl-pair", "stl-tuple"]),
    ("posortuj stringi wedlug dlugosci", ["stl-sort", "str-basic-lex-compare", "pit-comparator"]),
    ("jak wywalic z wektora wszystkie liczby ujemne", ["stl-remove-erase", "pit-iterator-erase", "stl-vector"]),
    ("jak zostawic tylko rozne liczby w tablicy", ["stl-unique", "stl-set"]),
    ("jak odwrocic tablice int", ["stl-reverse-rotate"]),
    ("jak sprawdzic czy w secie jest liczba 5", ["stl-set"]),
    ("dodawanie elementu na koniec wektora", ["stl-vector"]),
    ("usun klucz z mapy w c++", ["stl-map"]),
    ("jak znalezc najwiekszy element tablicy", ["stl-min-max"]),
    ("jak zsumowac wszystkie elementy wektora", ["stl-accumulate"]),
    ("ile razy liczba 7 wystepuje w wektorze", ["stl-count-find", "stl-binary-search"]),
    ("jak przechowywac pary liczb w secie", ["stl-set", "stl-pair"]),
    ("macierz n na m w c++ vector", ["stl-vector-2d"]),
    ("jak wczytac cala linijke", ["io-getline"]),
    ("wczytywanie az do konca danych", ["io-read-until-eof"]),
    ("wypisz liczbe z 3 miejscami po przecinku", ["io-precision", "num-rounding"]),
    ("cin za wolny przy milionie liczb", ["io-fast-io", "io-custom-fast-read"]),
    ("jak zaokraglic w dol do calkowitej", ["num-rounding", "num-ceil-div"]),
    ("wynik mnozenia nie miesci sie w int", ["pit-int-overflow", "num-overflow", "num-int-types"]),
    ("jak liczyc wszystko modulo 1000000007", ["num-mod-arithmetic"]),
    ("reszta z dzielenia ujemnej liczby jest ujemna", ["num-negative-mod"]),
    ("porownanie liczb zmiennoprzecinkowych z epsilonem", ["pit-double-equal", "num-floating"]),
    ("tablica 10^6 w funkcji main crash", ["pit-stack-overflow", "cpp-stack-overflow", "cpp-global-vs-local"]),
    ("kod bfs na grafie", ["graph-bfs"]),
    ("dfs iteracyjnie ze stosem", ["graph-dfs"]),
    ("najkrotsza sciezka z wagami dodatnimi", ["graph-dijkstra"]),
    ("czy w grafie skierowanym jest cykl", ["graph-cycle-detection", "graph-toposort"]),
    ("lista sasiedztwa z wagami", ["graph-representation", "graph-input"]),
    ("polacz dwa zbiory i sprawdz czy elementy sa razem", ["graph-dsu"]),
    ("najnizszy wspolny przodek w drzewie", ["tree-lca", "tree-lca-euler"]),
    ("glebokosc kazdego wierzcholka drzewa", ["tree-basics", "tree-dfs-order", "tree-distance"]),
    ("plecak z nieograniczona liczba przedmiotow", ["dp-knapsack-unbounded"]),
    ("najdluzszy wspolny podciag dwoch napisow kod", ["dp-lcs"]),
    ("ile jest sciezek z lewego gornego rogu do prawego dolnego", ["dp-grid-paths"]),
    ("liczba sposobow wejscia na n-ty schodek", ["dp-stairs-fibonacci"]),
    ("wyszukiwanie wzorca w tekscie kmp", ["str-kmp"]),
    ("czy napis czyta sie tak samo od tylu", ["str-basic-palindrome", "str-palindromes"]),
    ("zamien wszystkie litery na wielkie", ["str-basic-char-ops", "stl-string-methods"]),
    ("policz samogloski w napisie", ["str-basic-count-letters", "str-basic-char-ops"]),
    ("liczby pierwsze sito kod", ["math-sieve"]),
    ("dzielniki liczby do pierwiastka", ["math-divisors"]),
    ("nww dwoch liczb", ["math-gcd-lcm", "stl-gcd-lcm"]),
    ("dwumian newtona modulo p", ["math-binomial"]),
    ("suma na przedziale z aktualizacja punktu", ["ds-fenwick", "ds-segment-tree", "ds-segment-tree-iterative"]),
    ("minimum na przedziale bez zmian szybko", ["ds-sparse-table"]),
    ("dodaj na przedziale i zapytaj o sume na przedziale", ["ds-lazy-propagation", "ds-fenwick-range"]),
    ("najblizszy wiekszy element z prawej strony", ["ds-monotonic-stack"]),
    ("binarne wyszukiwanie odpowiedzi kiedy uzyc", ["tech-binary-search-answer"]),
    ("okno o stalej dlugosci k suma", ["tech-sliding-window", "ds-monotonic-queue", "ds-prefix-sums"]),
    ("co to jest zachlanny algorytm", ["tech-greedy"]),
    ("co to jest drzewo fenwicka", ["ds-fenwick"]),
    ("co to jest dsu", ["graph-dsu"]),
    ("co to jest unordered_map", ["stl-unordered-map"]),
    ("set vs vector", ["stl-set", "stl-vector"]),
    ("bfs czy dijkstra", ["graph-bfs", "graph-dijkstra"]),
    ("zlozonosc dijkstry z kopcem", ["graph-dijkstra"]),
    ("zlozonosc sortowania przez scalanie", ["stl-sort", "tech-divide-and-conquer", "stl-merge"]),
    ("jak napisac brute force do sprawdzania", ["oi-brute-generator", "oi-stress-test"]),
    ("jak debugowac segfault", ["oi-debugging", "pit-out-of-bounds", "tool-gdb-lldb"]),
]

# Pytania spoza bazy wiedzy — asystent ma szczerze powiedzieć „nie wiem” (niska pewność + propozycje).
UNKNOWN = [
    "jak ugotować makaron z sosem pomidorowym",
    "kto wygrał mundial w 2018",
    "przepis na pierogi ruskie",
    "jaka jest stolica australii",
    "asdfgh qwerty zxcv",
    "ile lat ma papież",
]


def _measure(verbose: bool = True, cases=None):  # pragma: no cover — narzędzie ręczne
    import os
    import time
    os.environ.setdefault("OIFORGE_NO_WARMUP", "1")
    from oiforge.core import kb
    from oiforge.tools import assistant as A
    A.engine().state(wait=True)
    A.engine().wait_nn(timeout=float(os.environ.get("NNWAIT", "60")))
    ids = {a["id"] for a in kb.articles()}
    t1 = t3 = tot = code_ok = code_tot = 0
    times = []
    for q, exp, *rest in (cases if cases is not None else CASES):
        opt = rest[0] if rest else {}
        missing = [x for x in exp if x not in ids]
        if len(missing) == len(exp):
            print("BRAK ARTYKUŁÓW", q, exp)
            continue
        tot += 1
        t = time.perf_counter()
        r = A.ask(q)
        times.append(1000 * (time.perf_counter() - t))
        got = (r.get("topic") or {}).get("id")
        ranked = r.get("ranking") or ([got] + [x["id"] for x in r.get("see_also") or []])  # (stara wersja bez „ranking”)
        g1 = got in exp and (not opt.get("intent") or r["intent"]["id"] == opt["intent"])
        g3 = any(x in exp for x in ranked[:3])
        t1 += g1
        t3 += g3
        if opt.get("code"):
            code_tot += 1
            has = any(b.get("type") == "code" for b in r["blocks"])
            code_ok += has
            if not has and verbose:
                print(f"  BEZ KODU  {q!r} → {got}")
        if verbose and not g1:
            print(f"  {'~' if g3 else '✗'} {q!r:60} → {got} [{r['intent']['id']}] {ranked[:3]} oczek. {exp}")
    unk = 0
    for q in UNKNOWN:
        r = A.ask(q)
        unk += r.get("confidence_label") == "niska" and bool(r.get("alternatives"))
    times.sort()
    print(f"top-1 {t1}/{tot} = {100 * t1 / tot:.1f}%  top-3 {t3}/{tot} = {100 * t3 / tot:.1f}%  "
          f"kod {code_ok}/{code_tot}  nie-wiem {unk}/{len(UNKNOWN)}  "
          f"czas med {times[len(times) // 2]:.0f} ms, max {times[-1]:.0f} ms")
    return t1, t3, tot


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    _measure(cases=HELDOUT if "--heldout" in _sys.argv else None)

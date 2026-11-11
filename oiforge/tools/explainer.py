"""(h) Objaśniacz kodu — tłumaczy program C++ po polsku jak nauczyciel (bez AI: analiza tokenów i struktury).

POST /api/explain {code} → {overview_md, sections:[{title, md}], line_notes:[{line, note}], algorithms, complexity,
                             variables:[...], functions:[...], io:{reads, writes}}

Co robi: opisuje, co program wczytuje (kolejność cin/scanf + pętle), jakie ma struktury danych, jaki algorytm
(szablony wyjaśnień dla każdego id z algodetect: co to jest, dlaczego działa, złożoność), rolę zmiennych
(licznik, akumulator, flaga, maksimum, indeks…), każdą funkcję (parametry, wynik, rekurencja), adnotuje kluczowe
linie (pętle, warunki, wywołania STL — słownik ~150 konstrukcji) i opisuje wyjście.
"""
from __future__ import annotations

import re

from oiforge.server_api import ApiError
from oiforge.tools._cppstruct import Code, normalize_expr

try:
    from oiforge.core import algodetect
except Exception:  # noqa: BLE001
    algodetect = None

# ============================================================================================== słownik STL
# klucz: "nazwa" (funkcja wolna) albo "rodzaj.metoda" (rodzaj z _cppstruct: vector, string, set, multiset, map,
# multimap, umap, uset, pq, queue, stack, deque, bitset) albo ".metoda" (ogólnie)
STL = {
    # --- algorytmy
    "sort": "sortuje zakres rosnąco (z komparatorem — według podanego porządku) — O(n log n)",
    "stable_sort": "sortuje stabilnie (równe elementy zachowują kolejność) — O(n log n)",
    "partial_sort": "ustawia k najmniejszych elementów na początku, posortowane — O(n log k)",
    "nth_element": "ustawia na pozycji k element, który byłby tam po posortowaniu — średnio O(n)",
    "reverse": "odwraca kolejność elementów — O(n)",
    "unique": "usuwa kolejne duplikaty (zwraca nowy koniec; zwykle z erase) — O(n), zakres musi być posortowany",
    "lower_bound": "binarnie szuka pierwszego elementu ≥ x w posortowanym zakresie — O(log n)",
    "upper_bound": "binarnie szuka pierwszego elementu > x w posortowanym zakresie — O(log n)",
    "binary_search": "sprawdza, czy x jest w posortowanym zakresie — O(log n)",
    "equal_range": "zakres elementów równych x w posortowanym ciągu — O(log n)",
    "partition_point": "binarnie szuka miejsca, gdzie predykat zmienia się z true na false — O(log n)",
    "max_element": "iterator na największy element — O(n) (wartość: *max_element(...))",
    "min_element": "iterator na najmniejszy element — O(n)",
    "minmax_element": "naraz minimum i maksimum zakresu — O(n)",
    "accumulate": "sumuje zakres od wartości początkowej (uwaga: typ wyniku = typ tej wartości, 0LL dla long long) — O(n)",
    "partial_sum": "zapisuje sumy prefiksowe zakresu — O(n)",
    "adjacent_difference": "różnice sąsiednich elementów (odwrotność sum prefiksowych) — O(n)",
    "iota": "wypełnia kolejnymi liczbami x, x+1, x+2, … — O(n)",
    "fill": "wypełnia zakres jedną wartością — O(n)",
    "fill_n": "wypełnia n elementów jedną wartością — O(n)",
    "memset": "ustawia bajty pamięci (bezpiecznie tylko dla 0 i -1) — O(rozmiar tablicy)",
    "memcpy": "kopiuje bajty pamięci — O(rozmiar)",
    "copy": "kopiuje zakres w inne miejsce — O(n)",
    "count": "liczy elementy równe x — O(n)",
    "count_if": "liczy elementy spełniające warunek — O(n)",
    "find": "szuka pierwszego wystąpienia x (liniowo) — O(n)",
    "find_if": "szuka pierwszego elementu spełniającego warunek — O(n)",
    "next_permutation": "przechodzi do następnej permutacji leksykograficznie (false, gdy była ostatnia) — O(n)",
    "prev_permutation": "przechodzi do poprzedniej permutacji — O(n)",
    "rotate": "obraca zakres (przesuwa cyklicznie) — O(n)",
    "shuffle": "losowo miesza zakres — O(n)",
    "random_shuffle": "losowo miesza zakres (przestarzałe, lepiej shuffle) — O(n)",
    "all_of": "czy wszystkie elementy spełniają warunek — O(n)",
    "any_of": "czy jakikolwiek element spełnia warunek — O(n)",
    "none_of": "czy żaden element nie spełnia warunku — O(n)",
    "merge": "scala dwa posortowane zakresy — O(n + m)",
    "inplace_merge": "scala dwie posortowane połówki w miejscu — O(n)",
    "set_union": "suma dwóch posortowanych zakresów — O(n + m)",
    "set_intersection": "część wspólna dwóch posortowanych zakresów — O(n + m)",
    "set_difference": "różnica dwóch posortowanych zakresów — O(n + m)",
    "is_sorted": "sprawdza, czy zakres jest posortowany — O(n)",
    "transform": "przekształca każdy element funkcją — O(n)",
    "for_each": "wywołuje funkcję dla każdego elementu — O(n)",
    "reduce": "redukuje zakres (np. suma) — O(n)",
    "inner_product": "iloczyn skalarny dwóch zakresów — O(n)",
    "distance": "odległość między iteratorami (dla setu/mapy liniowo!)",
    "advance": "przesuwa iterator o k pozycji",
    "next": "iterator o krok dalej (nie zmienia oryginału)",
    "prev": "iterator o krok wcześniej",
    "iter_swap": "zamienia wartości wskazywane przez dwa iteratory",
    "swap": "zamienia wartości dwóch zmiennych (dla kontenerów w O(1))",
    "max": "większa z wartości (oba argumenty muszą mieć ten sam typ — np. max(a, 0LL))",
    "min": "mniejsza z wartości (ten sam typ argumentów)",
    "clamp": "przycina wartość do przedziału [lo, hi]",
    "abs": "wartość bezwzględna", "llabs": "wartość bezwzględna long long", "fabs": "wartość bezwzględna liczby rzeczywistej",
    "sqrt": "pierwiastek kwadratowy (double — uwaga na precyzję dla dużych liczb)",
    "sqrtl": "pierwiastek w long double (dokładniejszy)", "cbrt": "pierwiastek sześcienny",
    "pow": "potęga w liczbach rzeczywistych (double!) — dla liczb całkowitych lepiej mnożyć lub szybkie potęgowanie",
    "powl": "potęga w long double", "log": "logarytm naturalny", "log2": "logarytm o podstawie 2",
    "log10": "logarytm dziesiętny", "exp": "e do potęgi x", "ceil": "zaokrąglenie w górę", "floor": "zaokrąglenie w dół",
    "round": "zaokrąglenie do najbliższej liczby całkowitej", "hypot": "długość przeciwprostokątnej √(x² + y²)",
    "atan2": "kąt wektora (x, y) w radianach", "sin": "sinus (radiany)", "cos": "cosinus (radiany)",
    "__gcd": "NWD dwóch liczb (algorytm Euklidesa) — O(log)", "gcd": "NWD dwóch liczb (C++17) — O(log)",
    "lcm": "NWW dwóch liczb (C++17)",
    "__builtin_popcount": "liczba zapalonych bitów (int)", "__builtin_popcountll": "liczba zapalonych bitów (long long)",
    "__builtin_clz": "liczba zer wiodących", "__builtin_ctz": "liczba zer na końcu (najmłodszy zapalony bit)",
    "__lg": "podłoga z log₂ x (numer najstarszego bitu)", "popcount": "liczba zapalonych bitów (C++20)",
    "to_string": "zamienia liczbę na napis", "stoi": "zamienia napis na int", "stoll": "zamienia napis na long long",
    "stod": "zamienia napis na double", "atoi": "zamienia napis C na int",
    "getline": "wczytuje całą linię (ze spacjami) do napisu",
    "printf": "wypisuje sformatowany tekst (styl C)", "scanf": "wczytuje sformatowane dane (styl C)",
    "puts": "wypisuje napis i znak nowej linii", "putchar": "wypisuje jeden znak", "getchar": "wczytuje jeden znak",
    "make_pair": "tworzy parę (a, b)", "make_tuple": "tworzy krotkę", "tie": "rozpakowuje krotkę/parę do zmiennych",
    "get": "element krotki o danym numerze", "isalpha": "czy znak jest literą", "isdigit": "czy znak jest cyfrą",
    "isupper": "czy znak jest wielką literą", "islower": "czy znak jest małą literą", "isspace": "czy znak jest białym znakiem",
    "tolower": "zamienia literę na małą", "toupper": "zamienia literę na wielką",
    "setprecision": "liczba cyfr przy wypisywaniu liczb rzeczywistych (z fixed — po przecinku)",
    "exit": "kończy program natychmiast", "assert": "przerywa program, gdy warunek jest fałszywy (debugowanie)",
    "rand": "liczba pseudolosowa (słaba — lepiej mt19937)", "srand": "ustawia ziarno rand()",
    "sync_with_stdio": "wyłącza synchronizację cin/cout z printf/scanf — szybsze wczytywanie i wypisywanie",
    "tie": "odwiązuje cin od cout — cout nie jest opróżniane przed każdym wczytaniem (szybciej)",
    # --- vector
    "vector.push_back": "dokłada element na koniec — zamortyzowane O(1)",
    "vector.emplace_back": "tworzy element na końcu (bez kopiowania) — zamortyzowane O(1)",
    "vector.pop_back": "usuwa ostatni element — O(1)", "vector.back": "ostatni element — O(1)",
    "vector.front": "pierwszy element — O(1)", "vector.size": "liczba elementów (typ bez znaku!)",
    "vector.empty": "czy wektor jest pusty", "vector.clear": "usuwa wszystkie elementy",
    "vector.resize": "zmienia rozmiar (nowe elementy = 0 albo podana wartość)",
    "vector.assign": "ustawia n kopii wartości (czyści i wypełnia od nowa) — O(n)",
    "vector.insert": "wstawia element w środek — O(n) (przesuwa resztę!)",
    "vector.erase": "usuwa element/zakres — O(n) (przesuwa resztę!)", "vector.reserve": "rezerwuje pamięć z góry",
    "vector.begin": "iterator na początek", "vector.end": "iterator za ostatni element",
    "vector.rbegin": "iterator od końca (odwrotny)", "vector.rend": "koniec iteracji odwrotnej",
    "vector.at": "element z kontrolą zakresu (wyjątek przy złym indeksie)",
    # --- string
    "string.substr": "podnapis od pozycji p o długości k — O(k)",
    "string.find": "pozycja pierwszego wystąpienia (string::npos, gdy brak) — O(n·m)",
    "string.rfind": "pozycja ostatniego wystąpienia", "string.length": "długość napisu",
    "string.size": "długość napisu", "string.c_str": "napis w stylu C (dla printf)",
    "string.append": "dopisuje na końcu", "string.insert": "wstawia w środek — O(n)", "string.erase": "usuwa fragment — O(n)",
    "string.replace": "zastępuje fragment", "string.compare": "porównuje leksykograficznie",
    "string.push_back": "dopisuje znak na końcu — O(1)", "string.pop_back": "usuwa ostatni znak",
    "string.back": "ostatni znak", "string.front": "pierwszy znak", "string.empty": "czy napis jest pusty",
    "string.find_first_of": "pierwsze wystąpienie któregoś ze znaków", "string.clear": "czyści napis",
    "string.begin": "iterator na początek napisu", "string.end": "iterator za koniec napisu",
    # --- set / multiset
    "set.insert": "wstawia element (duplikat jest ignorowany) — O(log n)",
    "set.erase": "usuwa element — O(log n)", "set.find": "szuka elementu (end(), gdy brak) — O(log n)",
    "set.count": "0 albo 1 — czy element jest w zbiorze — O(log n)",
    "set.lower_bound": "najmniejszy element ≥ x — O(log n)", "set.upper_bound": "najmniejszy element > x — O(log n)",
    "set.begin": "najmniejszy element (iterator)", "set.rbegin": "największy element (iterator)",
    "set.size": "liczba elementów", "set.empty": "czy zbiór jest pusty", "set.emplace": "wstawia element — O(log n)",
    "set.contains": "czy element jest w zbiorze (C++20) — O(log n)", "set.clear": "czyści zbiór",
    "multiset.insert": "wstawia element (duplikaty dozwolone) — O(log n)",
    "multiset.erase": "erase(x) usuwa WSZYSTKIE kopie x; erase(find(x)) — tylko jedną — O(log n)",
    "multiset.count": "liczba kopii x — O(log n + k)", "multiset.find": "iterator na jedną z kopii x — O(log n)",
    "multiset.lower_bound": "najmniejszy element ≥ x — O(log n)", "multiset.upper_bound": "najmniejszy element > x — O(log n)",
    "multiset.begin": "najmniejszy element", "multiset.rbegin": "największy element",
    # --- map
    "map.insert": "wstawia parę klucz–wartość — O(log n)", "map.erase": "usuwa klucz — O(log n)",
    "map.find": "szuka klucza — O(log n)", "map.count": "czy klucz istnieje (0/1) — O(log n)",
    "map.lower_bound": "pierwszy klucz ≥ x — O(log n)", "map.upper_bound": "pierwszy klucz > x — O(log n)",
    "map.contains": "czy klucz istnieje (C++20)", "map.at": "wartość dla klucza (wyjątek, gdy brak)",
    "map.emplace": "wstawia parę — O(log n)", "map.size": "liczba kluczy", "map.begin": "najmniejszy klucz",
    "map.rbegin": "największy klucz", "map.clear": "czyści mapę", "map.empty": "czy mapa jest pusta",
    # --- unordered
    "umap.insert": "wstawia parę — średnio O(1)", "umap.erase": "usuwa klucz — średnio O(1)",
    "umap.find": "szuka klucza — średnio O(1)", "umap.count": "czy klucz istnieje — średnio O(1)",
    "umap.at": "wartość dla klucza", "umap.size": "liczba kluczy", "umap.reserve": "rezerwuje kubełki (mniej przehaszowań)",
    "uset.insert": "wstawia element — średnio O(1)", "uset.count": "czy element jest w zbiorze — średnio O(1)",
    "uset.find": "szuka elementu — średnio O(1)", "uset.erase": "usuwa element — średnio O(1)",
    # --- kopiec / kolejka / stos / deque
    "pq.push": "wrzuca element na kopiec — O(log n)", "pq.emplace": "wrzuca element na kopiec — O(log n)",
    "pq.pop": "zdejmuje element z góry kopca — O(log n)",
    "pq.top": "element na górze kopca (domyślnie NAJWIĘKSZY) — O(1)", "pq.size": "liczba elementów w kopcu",
    "pq.empty": "czy kopiec jest pusty",
    "queue.push": "dokłada element na koniec kolejki — O(1)", "queue.pop": "usuwa element z początku kolejki — O(1)",
    "queue.front": "element z początku kolejki (najstarszy)", "queue.back": "element z końca kolejki",
    "queue.empty": "czy kolejka jest pusta", "queue.size": "liczba elementów w kolejce",
    "stack.push": "kładzie element na stos — O(1)", "stack.pop": "zdejmuje element ze stosu — O(1)",
    "stack.top": "element na szczycie stosu (ostatnio położony)", "stack.empty": "czy stos jest pusty",
    "stack.size": "liczba elementów na stosie",
    "deque.push_front": "dokłada na początek — O(1)", "deque.push_back": "dokłada na koniec — O(1)",
    "deque.pop_front": "usuwa z początku — O(1)", "deque.pop_back": "usuwa z końca — O(1)",
    "deque.front": "pierwszy element", "deque.back": "ostatni element", "deque.empty": "czy kolejka jest pusta",
    # --- bitset
    "bitset.count": "liczba zapalonych bitów — O(n/64)", "bitset.set": "zapala bit(y)", "bitset.reset": "gasi bit(y)",
    "bitset.flip": "odwraca bit(y)", "bitset.test": "czy bit jest zapalony", "bitset.any": "czy jakiś bit jest zapalony",
    "bitset.none": "czy żaden bit nie jest zapalony", "bitset.to_string": "bity jako napis 0/1",
    # --- ogólne metody
    ".size": "liczba elementów", ".empty": "czy kontener jest pusty", ".clear": "czyści kontener",
    ".begin": "iterator na początek", ".end": "iterator za koniec", ".push_back": "dokłada element na koniec",
    ".insert": "wstawia element", ".erase": "usuwa element", ".find": "szuka elementu", ".count": "liczy wystąpienia",
}

# ============================================================================================== szablony algorytmów
ALGO_TEXT = {
    "binary_search": ("Wyszukiwanie binarne",
                      "Zamiast sprawdzać wszystkie możliwości po kolei, program utrzymuje przedział, w którym na pewno "
                      "jest odpowiedź, i za każdym razem sprawdza jego środek. Zależnie od wyniku odrzuca połowę "
                      "przedziału.",
                      "Działa, bo sprawdzana własność jest **monotoniczna**: jeśli coś zachodzi dla x, to zachodzi "
                      "też dla wszystkich większych (albo mniejszych) wartości — więc po sprawdzeniu środka wiemy, "
                      "w której połowie leży odpowiedź.",
                      "O(log zakresu) kroków — dla zakresu 10⁹ to tylko ok. 30 sprawdzeń."),
    "two_pointers": ("Dwa wskaźniki (gąsienica)",
                     "Program trzyma dwa indeksy — początek i koniec „okna” — i przesuwa je tylko do przodu: "
                     "koniec rozszerza okno, początek je zwęża.",
                     "Kluczowa obserwacja: gdy okno [l, r] przestaje spełniać warunek, to żadne większe okno z tym "
                     "samym l też go nie spełni, więc l można bezpiecznie przesunąć. Żaden wskaźnik się nie cofa.",
                     "O(n) łącznie, mimo pętli w pętli — każdy wskaźnik przejdzie tablicę najwyżej raz."),
    "prefix_sums": ("Sumy prefiksowe",
                    "Program raz liczy tablicę sum początkowych fragmentów: p[i] = a[1] + … + a[i]. Dzięki niej "
                    "suma dowolnego przedziału [l, r] to po prostu p[r] − p[l−1].",
                    "Suma przedziału to suma do r minus suma do l−1 — wspólny początek się odejmuje.",
                    "O(n) na przygotowanie, potem O(1) na każde zapytanie (zamiast O(n))."),
    "sorting": ("Sortowanie",
                "Dane są ustawiane w kolejności rosnącej (lub według własnego kryterium), co upraszcza dalszą część: "
                "równe wartości stoją obok siebie, a najmniejsze/największe są na końcach.",
                "Wiele zadań staje się łatwych dopiero na posortowanych danych — można wtedy przejść raz po kolei "
                "albo szukać binarnie.",
                "std::sort działa w O(n log n); proste sortowania (bąbelkowe, przez wybieranie) — O(n²)."),
    "greedy": ("Algorytm zachłanny",
               "W każdym kroku program wybiera to, co w tej chwili wygląda najlepiej (np. przedział, który kończy się "
               "najwcześniej, albo najtańszy element), i nigdy nie zmienia decyzji.",
               "Taki wybór jest poprawny, gdy da się pokazać, że lokalnie najlepszy ruch można zawsze rozszerzyć do "
               "optymalnego rozwiązania (argument „wymiany”). Bez tego dowodu zachłan łatwo daje zły wynik!",
               "Zwykle O(n log n) — dominuje sortowanie lub kopiec."),
    "dp": ("Programowanie dynamiczne",
           "Program rozbija problem na mniejsze podproblemy (stany), zapisuje ich wyniki w tablicy i buduje "
           "odpowiedź z wcześniej policzonych wartości, zamiast liczyć je wiele razy.",
           "Wynik dla stanu zależy tylko od stanów „mniejszych”, które są już policzone (kolejność pętli tego "
           "pilnuje), a każdy podproblem jest liczony dokładnie raz.",
           "Liczba stanów × koszt jednego przejścia."),
    "bitmask_dp": ("DP na maskach bitowych",
                   "Podzbiór elementów jest zapisany jako liczba (maska): i-ty bit = 1 oznacza, że element i jest "
                   "wybrany. Tablica dp[maska] trzyma najlepszy wynik dla danego podzbioru.",
                   "Przejście dodaje jeden element do podzbioru (ustawia bit), więc maski rosną — kolejność "
                   "od 0 do 2ⁿ−1 zapewnia, że mniejsze podzbiory są gotowe wcześniej.",
                   "O(2ⁿ · n) albo O(2ⁿ · n²) — tylko dla małych n (do ok. 20)."),
    "bfs": ("BFS — przeszukiwanie wszerz",
            "Program odwiedza wierzchołki warstwami: najpierw start, potem jego sąsiadów, potem sąsiadów sąsiadów… "
            "Kolejka (FIFO) pilnuje tej kolejności.",
            "Wierzchołki wychodzą z kolejki w kolejności rosnącej odległości, więc pierwszy raz, gdy dotrzemy do "
            "wierzchołka, robimy to najkrótszą drogą (gdy każda krawędź ma tę samą długość).",
            "O(n + m) — każdy wierzchołek i każda krawędź są przeglądane raz."),
    "dfs": ("DFS — przeszukiwanie w głąb",
            "Program idzie z wierzchołka tak głęboko, jak się da, wchodząc rekurencyjnie do nieodwiedzonych "
            "sąsiadów, a potem się wycofuje.",
            "Tablica odwiedzonych gwarantuje, że każdy wierzchołek zostanie odwiedzony dokładnie raz — z każdego "
            "dojdziemy do całej jego spójnej składowej.",
            "O(n + m). Uwaga na głęboką rekurencję (np. ścieżka 10⁶ wierzchołków) — może zabraknąć stosu."),
    "dijkstra": ("Algorytm Dijkstry",
                 "Program liczy najkrótsze drogi od startu w grafie z nieujemnymi wagami. Z kopca zawsze bierze "
                 "wierzchołek o najmniejszej znanej odległości i „relaksuje” krawędzie: jeśli przez niego da się "
                 "dojść do sąsiada krócej, poprawia odległość sąsiada.",
                 "Wierzchołek zdjęty z kopca ma już ostateczną odległość — każda inna droga do niego musiałaby "
                 "przejść przez wierzchołek dalszy, a wagi są nieujemne, więc nie może być krótsza.",
                 "O((n + m) log n) z kopcem."),
    "bellman_ford": ("Algorytm Bellmana-Forda",
                     "Program n−1 razy przegląda wszystkie krawędzie i relaksuje je.",
                     "Najkrótsza ścieżka ma najwyżej n−1 krawędzi, a po k rundach znamy wszystkie najkrótsze "
                     "ścieżki z ≤ k krawędzi. Działa też dla ujemnych wag; poprawa w n-tej rundzie = ujemny cykl.",
                     "O(n · m)."),
    "floyd": ("Algorytm Floyda-Warshalla",
              "Program liczy najkrótsze drogi między wszystkimi parami wierzchołków: dla każdego k sprawdza, czy "
              "droga i → k → j jest krótsza niż obecna i → j.",
              "Po przetworzeniu k znamy najkrótsze drogi, które jako pośrednich używają tylko wierzchołków 1…k — "
              "dlatego pętla po k musi być zewnętrzna.",
              "O(n³) — do ok. n = 500."),
    "topo_sort": ("Sortowanie topologiczne",
                  "Program ustawia wierzchołki grafu skierowanego tak, by każda krawędź prowadziła „w prawo”. Liczy "
                  "stopnie wejściowe i bierze do kolejki wierzchołki, do których nic już nie wchodzi.",
                  "Wierzchołek o stopniu 0 nie musi czekać na nikogo; po jego usunięciu kolejne wierzchołki tracą "
                  "zależności. Jeśli nie da się wybrać wszystkich, w grafie jest cykl.",
                  "O(n + m)."),
    "dsu": ("Find & Union (DSU)",
            "Program utrzymuje podział elementów na rozłączne zbiory. Każdy zbiór ma reprezentanta; find(x) szuka "
            "reprezentanta, a union łączy dwa zbiory, podpinając jednego reprezentanta pod drugiego.",
            "Dwa elementy są w jednym zbiorze ⇔ mają tego samego reprezentanta. Kompresja ścieżki (podpinanie "
            "odwiedzonych węzłów bezpośrednio pod korzeń) i łączenie według rozmiaru utrzymują drzewa bardzo płaskie.",
            "Prawie O(1) na operację (zamortyzowane O(α(n)))."),
    "mst": ("Minimalne drzewo rozpinające",
            "Program wybiera krawędzie łączące wszystkie wierzchołki o najmniejszej łącznej wadze. Kruskal: "
            "krawędzie od najlżejszej, bierzemy te, które nie tworzą cyklu (sprawdza to DSU). Prim: drzewo rośnie "
            "z jednego wierzchołka, zawsze o najtańszą krawędź wychodzącą.",
            "Własność cięcia: najlżejsza krawędź przechodząca przez dowolny podział wierzchołków na dwie części "
            "należy do pewnego MST.",
            "Kruskal O(m log m), Prim O(m log n) albo O(n²)."),
    "lca": ("LCA — najniższy wspólny przodek",
            "Program dla każdego wierzchołka pamięta przodków o 1, 2, 4, 8, … poziomów wyżej (binary lifting). "
            "Najpierw wyrównuje głębokości, potem skacze oboma wierzchołkami w górę, dopóki przodkowie się różnią.",
            "Każdą odległość da się zapisać jako sumę potęg dwójki, więc wystarczy log n skoków.",
            "O(n log n) przygotowania, O(log n) na zapytanie."),
    "segment_tree": ("Drzewo przedziałowe",
                     "Program buduje pełne drzewo binarne nad tablicą: liście to elementy, a każdy węzeł trzyma "
                     "wynik (np. sumę, minimum, xor) dla swojego przedziału. Węzeł v ma synów 2v i 2v+1.",
                     "Każdy przedział rozkłada się na O(log n) węzłów drzewa, a zmiana elementu dotyczy tylko węzłów "
                     "na ścieżce od liścia do korzenia.",
                     "O(log n) na zapytanie i aktualizację, O(n) pamięci (2·N albo 4·N)."),
    "lazy_segment_tree": ("Drzewo przedziałowe z propagacją",
                          "Oprócz wartości węzły trzymają „odroczoną zmianę” (lazy), która dotyczy całego poddrzewa. "
                          "Spychamy ją do synów dopiero wtedy, gdy musimy do nich zejść.",
                          "Dzięki temu zmiana na przedziale dotyka tylko O(log n) węzłów — tak samo jak zapytanie.",
                          "O(log n) na operację, także dla zmian na przedziałach."),
    "fenwick": ("Drzewo Fenwicka (BIT)",
                "Program trzyma tablicę, w której komórka i odpowiada za przedział długości „najniższy zapalony bit "
                "liczby i”. Sumy prefiksowe i aktualizacje chodzą skokami i ± (i & −i).",
                "Każdy prefiks rozkłada się na O(log n) takich przedziałów — tyle, ile zapalonych bitów.",
                "O(log n) na operację, bardzo mała stała."),
    "sparse_table": ("Sparse table",
                     "Program liczy minimum (lub inną operację idempotentną) dla wszystkich przedziałów długości "
                     "2^k. Zapytanie o [l, r] to dwa nakładające się przedziały długości 2^k.",
                     "Dla minimum nakładanie się nie szkodzi (min(x, x) = x).",
                     "O(n log n) przygotowania, O(1) na zapytanie."),
    "kmp": ("KMP — funkcja prefiksowa",
            "Dla każdej pozycji program liczy długość najdłuższego właściwego prefiksu, który jest jednocześnie "
            "sufiksem. Przy niedopasowaniu cofa się po tej tablicy zamiast zaczynać od zera.",
            "Wartość funkcji prefiksowej rośnie o najwyżej 1 na krok, więc łączna liczba cofnięć jest liniowa.",
            "O(n + m)."),
    "z_function": ("Funkcja Z",
                   "Dla każdej pozycji i program liczy, jak długi prefiks napisu zaczyna się w i. Wykorzystuje "
                   "„okno” [l, r] już znalezionego dopasowania, żeby nie porównywać od nowa.",
                   "Wewnątrz okna wartości z są znane z wcześniejszych pozycji; prawa granica r tylko rośnie.",
                   "O(n)."),
    "hashing": ("Haszowanie napisów",
                "Program zamienia każdy prefiks napisu na liczbę (wielomian modulo duża liczba pierwsza). Hasz "
                "dowolnego podsłowa wylicza się z dwóch haszy prefiksów.",
                "Równe napisy mają równe hasze; różne — prawie na pewno różne (kolizja ma małe prawdopodobieństwo).",
                "O(n) przygotowania, O(1) na porównanie podsłów."),
    "trie": ("Drzewo trie",
             "Słowa są zapisywane w drzewie, gdzie każda krawędź to litera, a ścieżka od korzenia to prefiks.",
             "Wspólne prefiksy są przechowywane raz, a szukanie słowa to przejście po jego literach.",
             "O(długość słowa) na operację."),
    "manacher": ("Algorytm Manachera",
                 "Dla każdego środka program liczy promień najdłuższego palindromu, korzystając z odbicia względem "
                 "środka najdalej sięgającego znanego palindromu.",
                 "Wewnątrz znanego palindromu sytuacja po obu stronach środka jest symetryczna; granica r tylko rośnie.",
                 "O(n)."),
    "sieve": ("Sito Eratostenesa",
              "Program przechodzi po liczbach od 2 i każdą nieskreśloną (pierwszą) wykorzystuje do skreślenia jej "
              "wielokrotności.",
              "Liczba złożona ma dzielnik pierwszy ≤ √n, więc zostanie skreślona; liczba pierwsza nie ma mniejszych "
              "dzielników — nikt jej nie skreśli.",
              "O(n log log n)."),
    "fast_pow": ("Szybkie potęgowanie",
                 "Program liczy aᵇ, patrząc na bity wykładnika: podstawa jest podnoszona do kwadratu (a, a², a⁴, …), "
                 "a do wyniku mnożone są te potęgi, którym odpowiada zapalony bit b.",
                 "Każde b to suma potęg dwójki, więc aᵇ to iloczyn odpowiednich a^(2^k).",
                 "O(log b) mnożeń zamiast b."),
    "mod_inverse": ("Odwrotność modularna",
                    "Program dzieli modulo p, mnożąc przez odwrotność: a⁻¹ ≡ a^(p−2) (mod p).",
                    "Małe twierdzenie Fermata: dla pierwszego p i a niepodzielnego przez p, a^(p−1) ≡ 1 (mod p).",
                    "O(log p) na odwrotność."),
    "gcd": ("NWD — algorytm Euklidesa",
            "Program liczy największy wspólny dzielnik, zastępując parę (a, b) parą (b, a mod b), aż druga liczba "
            "będzie zerem.",
            "Wspólne dzielniki a i b są takie same jak b i a mod b, a liczby szybko maleją.",
            "O(log min(a, b))."),
    "matrix_pow": ("Potęgowanie macierzy",
                   "Rekurencję liniową (np. Fibonacciego) program zapisuje jako mnożenie przez macierz i podnosi "
                   "macierz do potęgi szybkim potęgowaniem.",
                   "Mnożenie macierzy jest łączne, więc działa ta sama sztuczka co dla liczb.",
                   "O(k³ log n) dla macierzy k×k."),
    "convex_hull": ("Otoczka wypukła",
                    "Program sortuje punkty i buduje otoczkę, usuwając ostatni punkt ze stosu, gdy trzy ostatnie "
                    "punkty tworzą „zły” skręt (sprawdzany iloczynem wektorowym).",
                    "Na otoczce wypukłej idąc w jedną stronę zawsze skręcamy w tę samą stronę.",
                    "O(n log n)."),
    "geometry": ("Geometria obliczeniowa",
                 "Program operuje na punktach i wektorach: liczy odległości, pola i orientację punktów (iloczyn "
                 "wektorowy mówi, czy skręcamy w lewo, czy w prawo).",
                 "Iloczyn wektorowy (b−a)×(c−a) to podwojone pole trójkąta ze znakiem — w liczbach całkowitych, bez "
                 "błędów zaokrągleń.",
                 "Zwykle O(1) na operację geometryczną."),
    "monotonic_stack": ("Stos monotoniczny",
                        "Program trzyma na stosie indeksy w kolejności monotonicznej. Przed włożeniem nowego "
                        "elementu zdejmuje te, które są od niego gorsze — dla nich nowy element jest odpowiedzią "
                        "(np. „najbliższy większy”).",
                        "Zdjęty element nigdy już nie będzie potrzebny, bo nowy jest bliżej i nie gorszy.",
                        "O(n) łącznie — każdy element raz wchodzi i raz wychodzi."),
    "sliding_window": ("Okno przesuwne",
                       "Program utrzymuje informację (sumę, minimum…) o oknie kolejnych elementów i przy przesunięciu "
                       "tylko dodaje nowy element i usuwa ten, który wypadł.",
                       "Sąsiednie okna różnią się tylko dwoma elementami.",
                       "O(n)."),
    "recursion": ("Rekurencja",
                  "Funkcja wywołuje samą siebie dla mniejszego problemu, aż dojdzie do przypadku bazowego, który zna "
                  "odpowiedź od razu.",
                  "Jeśli przypadek bazowy jest poprawny, a krok poprawnie składa wynik z mniejszych wyników, to "
                  "całość jest poprawna (indukcja).",
                  "Zależy od liczby wywołań — patrz analiza złożoności."),
    "backtracking": ("Przeszukiwanie z nawrotami",
                     "Program buduje rozwiązanie krok po kroku, próbując każdej możliwości; po powrocie z rekurencji "
                     "cofa swój wybór (np. odznacza element), żeby spróbować następnego.",
                     "Przegląda wszystkie możliwości, więc na pewno znajdzie odpowiedź — ale tylko dla małych danych.",
                     "Wykładniczo: n! dla permutacji, 2ⁿ dla podzbiorów."),
    "bitset": ("Bitset",
               "Program trzyma wiele wartości logicznych w jednej strukturze, gdzie operacje (|, &, <<) działają na "
               "64 bitach naraz.",
               "To ta sama logika co na tablicy bool, tylko 64× szybciej i 8× mniej pamięci.",
               "O(n/64) na operację na całym bitsecie."),
    "scc": ("Silnie spójne składowe",
            "Program dzieli graf skierowany na grupy wierzchołków, w których z każdego da się dojść do każdego.",
            "Tarjan: wartość low mówi, jak wysoko na stosie DFS da się wrócić; gdy low[v] = czas wejścia v, "
            "v jest „korzeniem” składowej.",
            "O(n + m)."),
    "bridges": ("Mosty i punkty artykulacji",
                "Program szuka krawędzi (wierzchołków), po usunięciu których graf się rozspójnia, porównując czasy "
                "wejścia DFS z wartością low.",
                "Krawędź v–u jest mostem, gdy z poddrzewa u nie da się wrócić do v ani wyżej (low[u] > tin[v]).",
                "O(n + m)."),
    "bipartite": ("Graf dwudzielny",
                  "Program koloruje wierzchołki na dwa kolory tak, by sąsiedzi mieli różne kolory.",
                  "Graf jest dwudzielny ⇔ nie ma cyklu nieparzystej długości — wtedy konflikt kolorów nigdy nie "
                  "wystąpi.",
                  "O(n + m)."),
    "flow": ("Maksymalny przepływ",
             "Program szuka ścieżek powiększających w sieci residualnej i przepycha nimi przepływ, aktualizując "
             "pojemności krawędzi i krawędzi odwrotnych.",
             "Przepływ jest maksymalny ⇔ nie ma ścieżki powiększającej (twierdzenie o maksymalnym przepływie i "
             "minimalnym przekroju).",
             "Edmonds-Karp O(n·m²), Dinic O(n²·m)."),
    "matching": ("Skojarzenia w grafie dwudzielnym",
                 "Dla każdego wierzchołka lewej strony program szuka ścieżki powiększającej, która pozwala dołożyć "
                 "go do skojarzenia, przepinając wcześniejsze pary.",
                 "Skojarzenie jest największe ⇔ nie ma ścieżki powiększającej (twierdzenie Berge’a).",
                 "O(n · m) (algorytm Kuhna)."),
    "mo": ("Algorytm Mo",
           "Zapytania o przedziały są sortowane po blokach długości √n, a program przesuwa końce aktualnego "
           "przedziału, dodając i usuwając pojedyncze elementy.",
           "Przy takim porządku końce łącznie przesuwają się O((n + q)√n) razy.",
           "O((n + q)·√n)."),
    "sqrt_decomp": ("Dekompozycja pierwiastkowa",
                    "Tablica jest podzielona na bloki długości ~√n; dla każdego bloku program pamięta wynik zbiorczy. "
                    "Zapytanie to kilka pełnych bloków + pojedyncze elementy na brzegach.",
                    "Pełnych bloków jest najwyżej √n, a brzegowych elementów najwyżej 2√n.",
                    "O(√n) na operację."),
    "coordinate_compression": ("Skalowanie (kompresja współrzędnych)",
                               "Duże wartości (np. do 10⁹) są zamieniane na ich pozycje w posortowanej liście "
                               "różnych wartości: 0, 1, 2, …",
                               "Zachowany jest porządek wartości, a numery są małe — mogą indeksować tablicę lub "
                               "drzewo przedziałowe.",
                               "O(n log n)."),
    "meet_in_middle": ("Meet in the middle",
                       "Program dzieli zbiór na dwie połowy, przegląda wszystkie podzbiory każdej połowy osobno "
                       "i łączy wyniki (sortowanie + wyszukiwanie binarne).",
                       "Zamiast 2ⁿ możliwości mamy 2·2^(n/2).",
                       "O(2^(n/2) · n)."),
    "simulation": ("Symulacja",
                   "Program krok po kroku odtwarza to, co dzieje się w zadaniu (ruchy, zmiany stanu), dokładnie "
                   "według reguł z treści.",
                   "Poprawność wynika wprost z wiernego odwzorowania reguł — tu najczęstsze błędy to przypadki "
                   "brzegowe.",
                   "Liczba kroków symulacji × koszt kroku."),
    "math": ("Matematyka / teoria liczb",
             "Program korzysta z własności liczb: podzielności, dzielników do √n, cyfr, arytmetyki modulo.",
             "Np. jeśli n = a·b i a ≤ b, to a ≤ √n — dlatego dzielników wystarczy szukać do pierwiastka.",
             "Często O(√n) lub O(log n)."),
    "combinatorics": ("Kombinatoryka",
                      "Program liczy liczbę sposobów: silnie, symbole Newtona (trójkąt Pascala), zwykle modulo duża "
                      "liczba pierwsza.",
                      "C(n, k) = C(n−1, k−1) + C(n−1, k): albo bierzemy n-ty element, albo nie.",
                      "O(n²) dla trójkąta Pascala albo O(n) z silniami."),
    "string_processing": ("Przetwarzanie napisów",
                          "Program przechodzi po znakach napisu i analizuje je: porównuje, zlicza, zamienia "
                          "(litery mają kody ASCII, np. 'a' = 97, więc c − 'a' to numer litery).",
                          "Operacje na pojedynczych znakach to zwykła arytmetyka na ich kodach.",
                          "Zwykle O(długość napisu)."),
    "graph": ("Graf",
              "Program zapamiętuje graf: dla każdego wierzchołka listę sąsiadów (lista sąsiedztwa) albo tablicę "
              "„czy jest krawędź” (macierz sąsiedztwa).",
              "Lista sąsiedztwa zajmuje O(n + m) pamięci i pozwala szybko przejrzeć sąsiadów wierzchołka.",
              "Budowa: O(n + m)."),
    "tree": ("Drzewo",
             "Dane tworzą drzewo: spójny graf bez cykli (n wierzchołków, n−1 krawędzi). Program przechodzi je od "
             "korzenia, pamiętając rodzica, i liczy coś dla poddrzew.",
             "W drzewie do każdego wierzchołka prowadzi dokładnie jedna ścieżka od korzenia, więc wystarczy nie "
             "wracać do rodzica, żeby nie zapętlić się.",
             "O(n)."),
    "grid": ("Plansza (siatka)",
             "Program traktuje planszę jak graf: pola to wierzchołki, a sąsiednie pola (góra/dół/lewo/prawo) są "
             "połączone. Tablice dx/dy opisują możliwe ruchy.",
             "Sprawdzanie granic planszy przed wejściem na pole chroni przed wyjściem poza tablicę.",
             "O(n · m) dla przeglądu całej planszy."),
}

ROLE_TEXT = {
    "loop": "zmienna sterująca pętli", "counter": "licznik (zwiększany o 1)", "sum": "akumulator — suma",
    "product": "akumulator — iloczyn", "max": "bieżące maksimum", "min": "bieżące minimum", "flag": "flaga (tak/nie)",
    "input": "dane z wejścia", "const": "stała", "result": "wynik wypisywany na końcu", "pointer": "wskaźnik (indeks)",
    "mid": "środek przedziału", "bound": "granica przedziału wyszukiwania",
    "temp": "zmienna pomocnicza", "answer": "odpowiedź", "index": "indeks / pozycja",
}

_TYPE_PL = {
    "int": "liczba całkowita", "long long": "duża liczba całkowita (long long)", "ll": "duża liczba całkowita (long long)",
    "unsigned long long": "duża liczba bez znaku", "double": "liczba rzeczywista", "long double": "liczba rzeczywista",
    "float": "liczba rzeczywista", "char": "znak", "bool": "wartość logiczna", "string": "napis", "short": "mała liczba",
}


def _type_pl(t: str) -> str:
    t = re.sub(r"\b(const|static|std::|unsigned)\b", "", t or "").strip()
    t = re.sub(r"\s+", " ", t)
    return _TYPE_PL.get(t, t or "?")


_TYPE_ACC = {
    "int": "liczbę całkowitą", "long long": "liczbę całkowitą (long long)", "ll": "liczbę całkowitą (long long)",
    "unsigned long long": "liczbę bez znaku", "double": "liczbę rzeczywistą", "long double": "liczbę rzeczywistą",
    "float": "liczbę rzeczywistą", "char": "znak", "bool": "wartość logiczną (true/false)", "string": "napis",
    "short": "liczbę całkowitą",
}


def _type_acc(t: str) -> str:
    t2 = re.sub(r"\b(const|static|std::|unsigned|inline)\b", "", t or "").strip()
    t2 = re.sub(r"\s+", " ", t2)
    if t2 in _TYPE_ACC:
        return _TYPE_ACC[t2]
    m = re.match(r"(vector|pair|set|map|tuple)\b", t2)
    if m:
        return {"vector": "wektor", "pair": "parę", "set": "zbiór", "map": "mapę", "tuple": "krotkę"}[m.group(1)] + f" ({t2})"
    return t2


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _join_pl(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " i " + items[-1]


def _code(s: str) -> str:
    s = (s or "").replace("`", "'")
    return f"`{s}`"


# ============================================================================================== analizator
class Explainer:
    def __init__(self, code: str):
        self.raw = code.replace("\r\n", "\n")
        self.offset = 0
        c = Code(self.raw)
        if not c.functions and c.n:
            c = Code("int main() {\n" + self.raw + "\n}")
            self.offset = 1
        self.c = c
        # kod bez zawartości literałów napisowych: szybkie i bezpieczne wyrażenia regularne nawet dla programów
        # z ogromnymi tablicami stałych (np. 300 liczb Fibonacciego jako napisy)
        self.nostr = " ".join('""' if t.kind == "str" else t.text for t in c.toks)
        self.flat = normalize_expr(self.nostr)
        try:
            self.algos = algodetect.detect(self.raw) if algodetect else []
        except Exception:  # noqa: BLE001
            self.algos = []
        self.algo_ids = {a["id"] for a in self.algos}
        self.notes: dict[int, list] = {}
        self.read_vars = set()
        for io in c.io:
            if io.kind == "read":
                for t in io.targets:
                    m = re.match(r"\s*&?\s*([A-Za-z_]\w*)", t)
                    if m:
                        self.read_vars.add(m.group(1))

    def L(self, line: int) -> int:
        return line - self.offset

    def note(self, line: int, text: str, prio: int = 1):
        ln = self.L(line)
        if ln <= 0:
            return
        self.notes.setdefault(ln, []).append((prio, text))

    # ------------------------------------------------------------------ pomocnicze
    def loop_count_text(self, L) -> str:
        """Czytelna liczba obrotów pętli: 'n', 'n − 1', 'm', 'q', …"""
        c = self.c
        if L.kind == "range":
            return f"wszystkie elementy {c.text(*L.range_expr)}"
        if L.kind == "for" and L.cond and L.init and L.var:
            cond = normalize_expr(c.text(*L.cond))
            init = normalize_expr(c.text(*L.init))
            v = re.escape(L.var)
            ms = re.search(rf"{v}=(.+)$", init)
            start = ms.group(1) if ms else "0"
            m = re.fullmatch(rf"{v}(<=|<|!=)(.+)", cond.split("&&")[0])
            if m:
                op, b = m.group(1), m.group(2)
                b_disp = c.text(L.cond[0], L.cond[1]).split("&&")[0]
                b_disp = re.sub(rf"^\s*{v}\s*(<=|<|!=)\s*", "", b_disp).strip()
                if start == "0" and op in ("<", "!="):
                    return b_disp
                if start == "1" and op == "<=":
                    return b_disp
                if start == "0" and op == "<=":
                    return f"{b_disp} + 1"
                if start == "1" and op == "<":
                    return f"{b_disp} − 1"
                return f"od {start} do {b_disp}"
            m = re.fullmatch(rf"{v}(>|>=)(.+)", cond)
            if m:
                return f"od {start} w dół do {m.group(2)}"
        if L.kind == "while" and L.cond:
            ct = normalize_expr(c.text(*L.cond))
            m = re.fullmatch(r"(\w+)--(>0)?|--(\w+)", ct)
            if m:
                return m.group(1) or m.group(3)
        return ""

    def loop_desc(self, L, k: int, info=None) -> str:
        c = self.c
        if L.kind == "range":
            rng = c.text(*L.range_expr)
            return f"Pętla po elementach {_code(rng)} — zmienna {_code(L.var or '?')} przyjmuje kolejne wartości"
        if L.kind == "for":
            cnt = self.loop_count_text(L)
            if L.step and re.search(r"--|-=", c.text(*L.step)):
                return f"Pętla po {_code(L.var or '?')} w dół ({cnt})"
            if L.step and re.search(r"\*=|<<=", c.text(*L.step)):
                return f"Pętla po {_code(L.var or '?')} mnożonym w każdym kroku — logarytmiczna liczba obrotów"
            if L.step and re.search(rf"\+=\s*\w", c.text(*L.step)) and not re.search(r"\+=\s*1\b", c.text(*L.step)):
                return f"Pętla po {_code(L.var or '?')} skokami {_code(c.text(*L.step))}"
            if cnt.startswith("od "):
                return f"Pętla po {_code(L.var or '?')} {cnt}"
            if cnt:
                return f"Pętla po {_code(L.var or '?')} — {cnt} {('obrotów' if not cnt.isdigit() else _plural(int(cnt), 'obrót', 'obroty', 'obrotów'))}"
            return f"Pętla for po {_code(L.var or '?')}"
        if L.kind in ("while", "do"):
            ct = normalize_expr(c.text(*L.cond)) if L.cond else ""
            m = re.fullmatch(r"(\w+)--(>0)?|--(\w+)", ct)
            if m:
                v = m.group(1) or m.group(3)
                return f"Pętla wykona się {_code(v)} razy (tyle, ile zestawów/zapytań)"
            m = re.search(r"!(\w+)\.empty\(\)|(\w+)\.size\(\)", ct)
            if m:
                q = m.group(1) or m.group(2)
                return f"Pętla działa, dopóki {_code(q)} nie jest pusta"
            if re.fullmatch(r"true|1|1==1", ct):
                return "Pętla „nieskończona” — kończy ją break albo return w środku"
            if re.match(r"(std::)?cin>>", ct):
                return "Pętla czyta dane aż do końca wejścia"
            return f"Pętla while — powtarza, dopóki {_code(c.text(*L.cond))}"
        return "Pętla"

    # ------------------------------------------------------------------ wejście
    def input_section(self) -> tuple[str, list[str]]:
        c = self.c
        reads = [io for io in c.io if io.kind == "read"]
        if not reads:
            return "Program nie wczytuje danych z wejścia.", []
        parts = []
        summary = []
        handled_loops = set()
        main = c.func_by_name.get("main")
        # literały porównywane z wczytanymi zmiennymi (typy zapytań)
        lits: dict[str, list] = {}
        for i in range(1, c.n - 1):
            if c.texts[i] == "==" and c.toks[i + 1].kind in ("str", "char") and c.toks[i - 1].kind == "id" \
                    and len(c.texts[i + 1]) <= 24:
                lits.setdefault(c.texts[i - 1], []).append(c.texts[i + 1])
        for io in reads:
            ls = c.loops_containing(io.idx)
            fn = c.enclosing_func(io.idx)
            where = ""
            if fn and fn.name != "main":
                where = f" (w funkcji {_code(fn.name)}())"
            vals = self.describe_targets(io.targets, io.idx) if io.targets else ["znak"]
            if not ls:
                parts.append(f"- linia {self.L(io.line)}: {_join_pl(vals)}{where}")
                summary.append(_join_pl([t for t in io.targets]))
                continue
            top = ls[0]
            if top in handled_loops and len(ls) == 1:
                continue
            L = c.loops[ls[-1]]
            cnt = self.loop_count_text(L)
            usage = self.read_usage(io, L)
            rep = f"{cnt} razy" if cnt else "w pętli"
            if len(ls) >= 2:
                outer = c.loops[ls[0]]
                oc = self.loop_count_text(outer)
                rep = f"{oc} × {cnt}" if oc and cnt else rep
            kinds = []
            for t in io.targets:
                v = re.match(r"\s*([A-Za-z_]\w*)", t)
                if v and v.group(1) in lits:
                    kinds.append(f"{_code(v.group(1))} rozpoznaje typ zapytania ({', '.join(sorted(set(lits[v.group(1)]))[:4])})")
            line = f"- linia {self.L(io.line)}: {rep}: {_join_pl(vals)}{where}"
            if usage:
                line += f" — {usage}"
            if kinds:
                line += "; " + "; ".join(kinds)
            parts.append(line)
            summary.append(f"{rep}: {', '.join(io.targets)}")
        fast = any(cl.name == "sync_with_stdio" for cl in c.calls)
        md = "Dane są wczytywane w tej kolejności:\n\n" + "\n".join(parts)
        if fast:
            md += "\n\nNa początku `ios::sync_with_stdio(false)` i `cin.tie(nullptr)` przyspieszają wczytywanie — " \
                  "przy dużych danych to często różnica między TLE a OK."
        elif len(reads) >= 2 and any(c.loops_containing(io.idx) for io in reads) and any(io.api == "cin" for io in reads):
            md += "\n\n💡 Program czyta dużo danych przez `cin` bez `ios::sync_with_stdio(false); cin.tie(nullptr);` — " \
                  "dodanie tych dwóch linii może wyraźnie przyspieszyć program."
        return md, summary

    _NUM_WORDS = {1: "jedną", 2: "dwie", 3: "trzy", 4: "cztery"}
    _ADJ = {"int": "całkowite", "long long": "całkowite (long long)", "ll": "całkowite (long long)",
            "unsigned long long": "całkowite bez znaku", "double": "rzeczywiste", "long double": "rzeczywiste",
            "float": "rzeczywiste", "short": "całkowite"}

    def describe_targets(self, targets: list, at: int) -> list[str]:
        """Grupuje kolejne skalary tego samego typu: „dwie liczby całkowite: `n` i `m`”."""
        c = self.c
        out = []
        group = []
        gtype = None

        def flush():
            nonlocal group, gtype
            if not group:
                return
            if len(group) == 1:
                out.append(self.describe_target(group[0], at))
            else:
                n = len(group)
                adj = self._ADJ.get(gtype, "")
                cnt = self._NUM_WORDS.get(n, str(n))
                noun = "liczby" if n <= 4 else "liczb"
                out.append(f"{cnt} {noun} {adj}: {_join_pl([_code(g) for g in group])}".replace("  ", " "))
            group = []
            gtype = None
        for t in targets:
            tt = normalize_expr(t)
            d = c.decl_of(tt, at) if re.fullmatch(r"[A-Za-z_]\w*", tt) else None
            ty = re.sub(r"\b(const|static|std::)\b", "", d.type).strip() if d is not None and d.kind == "scalar" else None
            if ty and ty in self._ADJ:
                if gtype is not None and ty != gtype:
                    flush()
                gtype = ty
                group.append(tt)
            else:
                flush()
                out.append(self.describe_target(t, at))
        flush()
        return out

    def describe_target(self, t: str, at: int) -> str:
        c = self.c
        tt = normalize_expr(t)
        m = re.match(r"([A-Za-z_]\w*)", tt)
        if not m:
            return _code(t)
        name = m.group(1)
        d = c.decl_of(name, at)
        if tt == name:
            if d is None:
                return _code(name)
            if d.kind == "string":
                return f"napis {_code(name)}"
            if d.kind == "scalar":
                return f"{_type_pl(d.type)} {_code(name)}" if "char" not in d.type else f"znak {_code(name)}"
            return _code(name)
        if re.fullmatch(rf"{re.escape(name)}\[\w+(-1|\+1)?\]", tt):
            return f"element tablicy {_code(name)}"
        if re.fullmatch(rf"{re.escape(name)}\[\w+\]\[\w+\]", tt):
            return f"pole planszy/tablicy {_code(name)}"
        if re.fullmatch(rf"{re.escape(name)}(\[\w+\])?\.(first|second|x|y)", tt):
            return f"pole {_code(t)}"
        return _code(t)

    def read_usage(self, io, L) -> str:
        """Co dzieje się z wczytanymi wartościami w tej samej pętli."""
        c = self.c
        names = [re.match(r"\s*([A-Za-z_]\w*)", t).group(1) for t in io.targets if re.match(r"\s*([A-Za-z_]\w*)", t)]
        body = normalize_expr(c.text(*L.body))
        uses = []
        for cl in c.calls:
            if not (L.body[0] <= cl.idx <= L.body[1]) or not cl.receiver:
                continue
            argt = normalize_expr(c.text(*cl.args[0])) if cl.args else ""
            if cl.name in ("push_back", "emplace_back") and cl.receiver_text != cl.receiver:
                idx = normalize_expr(cl.receiver_text)
                if any(n in idx for n in names) and (any(n in argt for n in names) or argt):
                    w = " z wagą" if len(names) >= 3 or "pair" in (c.decl_of(cl.receiver) or type("x", (), {"type": ""})).type else ""
                    one = "−1" in idx or "-1" in idx
                    uses.append(f"to krawędzie grafu{w} dopisywane do listy sąsiedztwa {_code(cl.receiver)}"
                                + (" (wierzchołki numerowane od 1, w programie od 0)" if one else ""))
                    break
            if cl.name in ("push_back", "emplace_back", "push", "insert", "emplace") and cl.receiver_text == cl.receiver \
                    and any(n in argt for n in names):
                k = c.decl_of(cl.receiver)
                kind = {"vector": "wektora", "set": "zbioru", "multiset": "zbioru", "pq": "kopca", "queue": "kolejki",
                        "map": "mapy", "stack": "stosu", "deque": "kolejki", "uset": "zbioru", "string": "napisu"}.get(
                    k.kind if k else "", "kontenera")
                uses.append(f"trafiają do {kind} {_code(cl.receiver)}")
                break
        for n in names:
            if re.search(rf"(\w+)\[{re.escape(n)}\](\+\+|\+=1)", body):
                arr = re.search(rf"(\w+)\[{re.escape(n)}\](\+\+|\+=1)", body).group(1)
                uses.append(f"liczone w {_code(arr)} (ile razy wystąpiła każda wartość)")
                break
            if re.search(rf"(\w+)\+={re.escape(n)}\b", body):
                acc = re.search(rf"(\w+)\+={re.escape(n)}\b", body).group(1)
                uses.append(f"dodawane do sumy {_code(acc)}")
                break
            if re.search(rf"(\w+)\[\w+\]=\w+\[\w+-1\]\+{re.escape(n)}\b", body):
                uses.append("od razu budowane są sumy prefiksowe")
                break
        return _join_pl(uses[:2])

    # ------------------------------------------------------------------ wyjście
    def output_section(self) -> str:
        c = self.c
        writes = [io for io in c.io if io.kind == "write" and io.api in ("cout", "printf", "puts", "putchar")]
        if not writes:
            return "Program nic nie wypisuje (albo używa innego sposobu wypisywania)."
        items = []
        for io in writes[:12]:
            vals = [t for t in io.targets if t not in ("endl", "std::endl", '"\\n"', "'\\n'", '" "', "' '")]
            if not vals:
                continue
            ls = c.loops_containing(io.idx)
            ctx = " (w pętli — dla każdego elementu/zapytania)" if ls else ""
            cond = ""
            j = io.idx - 1
            while j > 0 and c.texts[j] not in (";", "{", "}"):
                j -= 1
            seg = c.text(j + 1, io.idx - 1)
            if seg.strip().startswith("if"):
                cond = f" gdy {_code(seg.strip()[2:].strip())}"
            elif seg.strip() == "else":
                cond = " w przeciwnym razie"
            fn = c.enclosing_func(io.idx)
            fpart = f" w {_code(fn.name)}()" if fn and fn.name != "main" else ""
            shown = ", ".join(_code(v) for v in vals[:4])
            items.append(f"- linia {self.L(io.line)}{fpart}: {shown}{cond}{ctx}")
        endl = sum(1 for io in writes for t in io.targets if t in ("endl", "std::endl"))
        md = "Program wypisuje:\n\n" + "\n".join(items)
        if endl and any(c.loops_containing(io.idx) for io in writes):
            md += "\n\n💡 `endl` w pętli opróżnia bufor przy każdej linii — przy dużym wyjściu użyj `'\\n'`."
        return md

    # ------------------------------------------------------------------ struktury danych
    def structures_section(self) -> str:
        c = self.c
        out = []
        seen = set()
        for d in c.decls:
            if d.is_param or d.kind in ("scalar", "pointer", "other", "pair", "tuple") or d.name in seen:
                continue
            if d.scope not in ("global", "main") and d.kind not in ("pq", "queue", "stack", "set", "map", "umap"):
                continue
            seen.add(d.name)
            desc = self.describe_container(d)
            if desc:
                out.append(f"- {_code(d.name)} — {desc} (linia {self.L(d.line)})")
        if not out:
            return ""
        return "\n".join(out[:14])

    def describe_container(self, d) -> str:
        c = self.c
        name = d.name
        role = self.array_role(name)
        k = d.kind
        if k == "array":
            dims = " × ".join(d.dims)
            elem = c.classify_type(d.elem)[0]
            if elem in ("vector", "set", "deque"):
                base = f"tablica {d.dims[0]} list ({d.elem})"
                if self._is_adj(name):
                    base = f"lista sąsiedztwa grafu: dla każdego wierzchołka lista sąsiadów" + (
                        " (pary sąsiad–waga)" if "pair" in d.elem else "")
            else:
                base = f"tablica {_type_pl(d.elem)} o wymiarach {dims}" if len(d.dims) > 1 else \
                    f"tablica {_type_pl(d.elem)} rozmiaru {dims}"
        elif k == "vector":
            if d.nested >= 2:
                if self._is_adj(name):
                    base = "lista sąsiedztwa grafu (vector wektorów)" + (" z parami sąsiad–waga" if "pair" in d.elem else "")
                else:
                    base = "tablica dwuwymiarowa (vector wektorów)"
            else:
                size = f" rozmiaru {d.ctor[0]}" if d.ctor else ""
                base = f"wektor ({_type_pl(d.elem)}){size}"
        elif k == "string":
            base = "napis"
        elif k == "pq":
            mn = "greater" in d.type
            neg = bool(re.search(r"\*\s*-1|\*\s*\(-1\)|push\(\s*\{?\s*-|make_pair\(-", self.nostr))
            base = "kolejka priorytetowa (kopiec) — " + ("na górze zawsze NAJMNIEJSZY element (greater<>)" if mn else
                                                         "na górze zawsze NAJWIĘKSZY element")
            if neg and not mn:
                base += "; odległości są wrzucane z minusem, żeby działała jak kopiec minimalny"
        elif k == "queue":
            base = "kolejka FIFO — elementy wychodzą w kolejności wrzucenia"
        elif k == "stack":
            base = "stos — zdejmujemy ostatnio położony element"
        elif k == "deque":
            base = "kolejka dwustronna (dodawanie/usuwanie z obu końców w O(1))"
        elif k == "set":
            base = "zbiór uporządkowany bez powtórzeń (wstawianie, usuwanie, szukanie w O(log n))"
        elif k == "multiset":
            base = "zbiór uporządkowany z powtórzeniami (multiset)"
        elif k in ("map", "multimap"):
            base = "mapa (słownik) klucz → wartość, posortowana po kluczach, operacje w O(log n)"
        elif k == "umap":
            base = "tablica haszująca klucz → wartość (średnio O(1) na operację)"
        elif k == "uset":
            base = "zbiór haszujący (średnio O(1) na operację)"
        elif k == "bitset":
            base = f"bitset ({d.elem} bitów) — szybkie operacje na wielu bitach naraz"
        else:
            base = d.type
        if role:
            base += f"; rola: **{role}**"
        return base

    def _relax_targets(self) -> set:
        if not hasattr(self, "_rt"):
            self._rt = set()
            if algodetect is not None:
                try:
                    ctx = algodetect.Ctx(self.raw if not self.offset else "int main() {\n" + self.raw + "\n}")
                    self._rt = {r["target"] for r in algodetect._relaxations(ctx) if r["plus"]}
                except Exception:  # noqa: BLE001
                    pass
        return self._rt

    def _is_adj(self, name: str) -> bool:
        return any(cl.receiver == name and cl.receiver_text != name and cl.name in ("push_back", "emplace_back")
                   for cl in self.c.calls)

    def array_role(self, name: str) -> str:
        c = self.c
        flat = self.flat
        en = re.escape(name)
        if name in self._relax_targets():
            return "odległości od startu (poprawiane relaksacją)"
        if re.search(rf"{en}\[[^\]]+\]={en}\[[^\]]+-1\]\+", flat) or re.search(rf"{en}\.push_back\({en}\.back\(\)\+", flat):
            return "sumy prefiksowe"
        if re.search(rf"{en}\[(\w+)\]=\w+\({en}\[\1\]\)", flat):
            return "rodzic w DSU (reprezentant zbioru)"
        if re.search(rf"{en}\[(\w+)\]\+={en}\[(\w+)\];", flat) and "dsu" in self.algo_ids:
            return "rozmiary zbiorów (łączenie według rozmiaru)"
        if re.search(rf"if\(\w+\[[^\]]+\]>\w+(\[[^\]]+\])?(\.first|\.second)?\+[^)]*\){{?{en}\[", flat) or \
                re.search(rf"if\({en}\[[^\]]+\]>[^)]*\+[^)]*\)", flat):
            return "odległości od startu"
        if re.search(rf"{en}\[\w+\]=(true|1);", flat) and re.search(rf"if\(!{en}\[|{en}\[\w+\]==(0|false)", flat):
            return "odwiedzone wierzchołki/pola"
        if re.search(rf"{en}\[[^\]]+\](\+\+|\+=1);", flat) and re.search(rf"{en}\[[^\]]+\]==0", flat):
            return "stopnie wejściowe (ile krawędzi wchodzi)"
        if re.search(rf"{en}\[[^\]]+\]=(max|min)\(", flat):
            return "tablica stanów programowania dynamicznego"
        if re.search(rf"{en}\[\w+\[\w+\]\](\+\+|\+=1)|{en}\[\w+\](\+\+|\+=1)", flat):
            return "liczniki wystąpień"
        if name.lower() in ("dp", "memo", "f"):
            return "tablica stanów programowania dynamicznego"
        return ""

    # ------------------------------------------------------------------ zmienne
    def variable_roles(self) -> list[dict]:
        c = self.c
        res = []
        seen = set()
        loop_vars = {L.var for L in c.loops if L.var and L.kind in ("for", "range")}
        printed = set()
        for io in c.io:
            if io.kind == "write":
                for t in io.targets:
                    m = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*", t)
                    if m:
                        printed.add(m.group(1))
        for d in c.decls:
            if d.kind not in ("scalar",) or d.is_param or (d.name, d.scope) in seen:
                continue
            seen.add((d.name, d.scope))
            name = d.name
            asg = [a for a in c.assigns if a.target == name and a.target_text == name and a.func == (
                None if d.scope == "global" else d.scope)]
            role = None
            detail = ""
            if d.is_const or name in c.consts and not asg[1:]:
                role = "const"
                v = c.consts.get(name)
                if v is not None:
                    detail = f"= {int(v) if float(v).is_integer() else v}"
            elif name in loop_vars and all(a.op in ("=", "++", "--", "+=", "-=") for a in asg) and \
                    any(L.var == name for L in c.loops if L.kind == "for" and L.init and d.idx >= L.init[0] - 1
                        and d.idx <= (L.init[1] if L.init else 0)):
                role = "loop"
            else:
                ops = [a.op for a in asg]
                rhs_texts = [normalize_expr(c.text(*a.rhs)) if a.rhs else "" for a in asg]
                non_init = [(o, r) for o, r in zip(ops, rhs_texts)][1:] if ops and ops[0] == "=" else list(zip(ops, rhs_texts))
                if any(re.fullmatch(rf"max\({re.escape(name)},.+\)|max\(.+,{re.escape(name)}\)", r) for _, r in non_init):
                    role = "max"
                elif any(re.fullmatch(rf"min\({re.escape(name)},.+\)|min\(.+,{re.escape(name)}\)", r) for _, r in non_init):
                    role = "min"
                elif any(re.fullmatch(r"\(?\w+\+\w+\)?(/2|>>1)|\w+\+\(\w+-\w+\)/2|\(\w+\+\w+\+1\)/2", r) for _, r in non_init + list(zip(ops[:1], rhs_texts[:1]))):
                    role = "mid"
                elif d.type.strip() == "bool" or (non_init and all(r in ("0", "1", "true", "false") for o, r in non_init if o == "=")
                                                   and any(r in ("true", "false") for _, r in non_init)):
                    role = "flag"
                elif non_init and all(o in ("++", "--") or (o in ("+=", "-=") and r == "1") for o, r in non_init) and \
                        re.search(rf"\w+\[{re.escape(name)}([+-]1)?\]", self.flat):
                    role = "pointer"
                elif non_init and all(o in ("++",) or (o == "+=" and r == "1") for o, r in non_init):
                    role = "counter"
                elif any(o == "+=" for o, _ in non_init):
                    role = "sum"
                elif any(o == "*=" for o, _ in non_init):
                    role = "product"
                elif name in self.read_vars and not non_init:
                    role = "input"
                elif name in self.read_vars:
                    role = "input"
                elif non_init and all(o in ("++", "--", "+=", "-=") for o, _ in non_init) and re.search(
                        rf"\w+\[{re.escape(name)}\]", self.flat):
                    role = "pointer"
                elif name in printed:
                    role = "result"
                elif non_init and any(re.fullmatch(r"\w+\[\w+\]", r) for _, r in non_init) and len(non_init) <= 2:
                    role = "temp"
            if role is None:
                continue
            if role == "loop":
                continue  # pętle opisujemy przy pętlach — tabela byłaby za długa
            if name in printed and role in ("sum", "counter", "max", "min"):
                detail = (detail + " — wypisywana jako wynik").strip()
            res.append({"name": name, "type": d.type, "role": role, "role_text": ROLE_TEXT[role], "line": self.L(d.line),
                        "scope": d.scope, "detail": detail})
        return res[:30]

    # ------------------------------------------------------------------ funkcje
    NAME_VERBS = [
        (r"^(znajdz|find|fin|szukaj|search|serch|root|lider)", "szuka"),
        (r"^(dodaj|add|update|upd|zmien|zmiana|modify|set|ustaw)", "zmienia/aktualizuje"),
        (r"^(zczytaj|query|get|sum|suma|ile|czytaj|odczyt|count)", "odpowiada na zapytanie"),
        (r"^(spr|check|ok|czy|is|valid|can|moze)", "sprawdza warunek"),
        (r"^(dfs|bfs|odwiedz|visit|przejdz)", "przechodzi graf"),
        (r"^(polacz|union|unite|merge|scal|join|lacz)", "łączy"),
        (r"^(sortuj|sort)", "sortuje"), (r"^(wypisz|print|pisz|out)", "wypisuje"),
        (r"^(wczytaj|read|load|input)", "wczytuje dane"), (r"^(oblicz|calc|compute|solve|licz|policz)", "liczy wynik"),
        (r"^(pot|power|potega|binpow|fastpow)", "liczy potęgę"), (r"^(nwd|gcd)", "liczy NWD"),
        (r"^(build|buduj|zbuduj|init|inicjuj)", "buduje/inicjuje strukturę"), (r"^(cross|iloczyn)", "liczy iloczyn wektorowy"),
        (r"^(odl|dist)", "liczy odległość"), (r"^(pole|area)", "liczy pole"),
    ]

    def functions_section(self, comp: dict | None) -> tuple[str, list[dict]]:
        c = self.c
        recinfo = {}
        if comp:
            for node in comp.get("loops", []):
                if node.get("kind") == "function":
                    recinfo[node["name"]] = node
        out = []
        data = []
        for f in c.functions:
            if f.name == "main":
                continue
            params = []
            for p in f.params:
                mode = "przez stałą referencję (bez kopiowania, tylko do odczytu)" if p.is_ref and p.is_const else (
                    "przez referencję — zmiany są widoczne na zewnątrz" if p.is_ref else (
                        "przez wartość — KOPIA całego kontenera!" if p.kind in ("vector", "string", "set", "map", "umap",
                                                                                 "deque", "multiset") else "przez wartość"))
                params.append(f"{_code(p.name)} ({_type_pl(p.type)}, {mode})")
            ret = (f.ret or "").replace("static", "").replace("inline", "").strip()
            ret_txt = "nic nie zwraca (void)" if ret == "void" else f"zwraca {_type_acc(ret)}"
            body = normalize_expr(c.text(f.open_idx, f.close_idx))
            verb = None
            for rx, v in self.NAME_VERBS:
                if re.search(rx, f.name.lower()):
                    verb = v
                    break
            # algorytmy z dowodami w liniach tej funkcji
            algs = [a for a in self.algos if any(f.line <= ln <= f.end_line for ln in a["lines"]) and
                    a["id"] not in ("recursion", "graph", "tree", "grid", "math", "string_processing")]
            what = ""
            if algs:
                what = f"Realizuje: **{algs[0]['name']}**" + (f" ({algs[0]['evidence'][0]})" if algs[0]["evidence"] else "")
            elif verb:
                what = f"Sądząc po nazwie i treści — {verb}."
            rec = ""
            if f.self_calls:
                ri = recinfo.get(f.name, {}).get("recursion") or {}
                rec = f"Funkcja jest **rekurencyjna** ({len(f.self_calls)} " + _plural(len(f.self_calls), "wywołanie", "wywołania",
                                                                                          "wywołań") + " samej siebie)."
                if ri.get("text"):
                    rec += f" {ri['text']}"
                base = re.search(r"\{if\(([^)]*)\)\{?return", body)
                if base:
                    rec += f" Warunek bazowy: {_code(base.group(1))}."
            pc = recinfo.get(f.name, {}).get("per_call")
            cost = f"Koszt jednego wywołania: O({pc})." if pc else ""
            ot = recinfo.get(f.name, {}).get("one_time")
            if ot:
                cost = f"Łączny koszt wszystkich wywołań: O({ot}) (każdy stan liczony raz)."
            md = f"**{f.name}**(" + ", ".join(p.name for p in f.params) + f") — linie {self.L(f.line)}–{self.L(f.end_line)}, {ret_txt}.\n"
            if params:
                md += "\nParametry: " + "; ".join(params) + "."
            for part in (what, rec, cost):
                if part:
                    md += "\n\n" + part
            out.append(md)
            data.append({"name": f.name, "line": self.L(f.line), "end_line": self.L(f.end_line), "returns": ret,
                         "params": [{"name": p.name, "type": p.type, "ref": p.is_ref, "const": p.is_const} for p in f.params],
                         "recursive": bool(f.self_calls), "summary": what or (verb or "")})
            self.note(f.line, f"Funkcja {f.name}(): {ret_txt}" + (" — rekurencyjna" if f.self_calls else ""), 3)
        return "\n\n".join(out), data

    # ------------------------------------------------------------------ adnotacje linii
    def annotate_lines(self, comp: dict | None):
        c = self.c
        loop_notes = {}
        if comp:
            def walk(nodes):
                for nd in nodes:
                    if nd.get("kind") != "function" and nd.get("note"):
                        loop_notes[nd["line"]] = nd
                    walk(nd.get("children", []))
            walk(comp.get("loops", []))
        for k, L in enumerate(c.loops):
            d = self.loop_desc(L, k)
            extra = ""
            nd = loop_notes.get(self.L(L.line))
            if nd and nd.get("pattern") in ("two_pointers", "monotonic_stack", "harmonic", "sieve", "binary_search",
                                             "dependent", "adjacency", "bfs", "pq_graph", "sqrt", "log", "until_stable"):
                extra = f" · {nd['note']}"
            self.note(L.line, d + extra, 2)
        # wywołania STL
        for cl in c.calls:
            key = None
            if cl.is_method and cl.receiver:
                d = c.decl_of(cl.receiver, cl.idx)
                kind = d.kind if d else None
                if d is not None and cl.receiver_text != cl.receiver and d.kind in ("vector", "array"):
                    kind = c.classify_type(d.elem if d.kind == "vector" else d.elem)[0]
                    if d.kind == "array":
                        kind = c.classify_type(d.type)[0]
                for kk in (f"{kind}.{cl.name}", f".{cl.name}"):
                    if kk in STL:
                        key = kk
                        break
                if cl.name == "tie" and cl.receiver in ("cin", "cout"):
                    key = "tie"
                    if cl.receiver == "cout":
                        self.note(cl.line, "`cout.tie(0)` niczego nie zmienia (cout nie jest z niczym związany) — "
                                           "wystarczy `cin.tie(0)`", 1)
                        continue
                if cl.name in ("size", "empty", "begin", "end", "length", "rbegin", "rend", "first", "second"):
                    continue
                if key:
                    self.note(cl.line, f"{_code(cl.receiver_text + '.' + cl.name + '()')}: {STL[key]}", 1)
            elif cl.name in STL and cl.name not in self.c.func_by_name:
                if cl.name in ("max", "min", "abs") and len(self.notes.get(self.L(cl.line), [])) > 0:
                    continue
                self.note(cl.line, f"{_code(cl.name + '(…)')}: {STL[cl.name]}", 1)
            elif cl.name in self.c.func_by_name and not cl.is_method:
                f = self.c.func_by_name[cl.name]
                caller = c.enclosing_func(cl.idx)
                if caller is not None and caller.name == cl.name:
                    self.note(cl.line, f"wywołanie rekurencyjne {cl.name}(…) — mniejszy podproblem", 2)
                else:
                    self.note(cl.line, f"wywołanie własnej funkcji {cl.name}(…)", 0)
        # wejście/wyjście
        for io in c.io:
            if io.kind == "read":
                self.note(io.line, "wczytuje " + _join_pl([_code(t) for t in io.targets]) if io.targets else "wczytuje dane", 1)
            elif io.targets:
                vals = [t for t in io.targets if t not in ("endl", "std::endl", '"\\n"', "'\\n'", '" "', "' '")]
                if vals:
                    self.note(io.line, "wypisuje " + _join_pl([_code(v) for v in vals[:3]]), 1)
        # charakterystyczne instrukcje
        for a in c.assigns:
            if not a.rhs or a.op != "=":
                continue
            r = normalize_expr(c.text(*a.rhs))
            lhs = normalize_expr(a.target_text)
            if re.fullmatch(rf"\w+\({re.escape(lhs)}\)", r) and a.indexed:
                self.note(a.line, "kompresja ścieżki: element zostaje podpięty bezpośrednio pod reprezentanta", 3)
            elif re.fullmatch(rf"(max|min)\({re.escape(lhs)},.+\)", r) and a.indexed and "+" in r:
                self.note(a.line, f"przejście/relaksacja: {_code(lhs)} poprawiane, jeśli znaleziono lepszą wartość", 3)
            elif re.fullmatch(r"\(?\w+\+\w+(\+1)?\)?(/2|>>1)|\w+\+\(\w+-\w+\)/2", r):
                self.note(a.line, f"{_code(a.target)} = środek przedziału", 3)
        for i in range(c.n - 1):
            if c.texts[i] != "if" or c.texts[i + 1] != "(" or (i + 1) not in c.pairs:
                continue
            j = c.pairs[i + 1]
            if j + 1 >= c.n or c.texts[j + 1] != "return":
                continue
            line = c.toks[i].line
            f = None
            for fn in c.functions:
                if fn.line <= line <= fn.end_line:
                    f = fn
            if f and f.self_calls:
                self.note(line, "warunek bazowy rekurencji — tu rekurencja się zatrzymuje", 3)
        # relaksacje (Dijkstra/BFS)
        if algodetect is not None:
            try:
                ctx = algodetect.Ctx(self.raw if not self.offset else "int main() {\n" + self.raw + "\n}")
                for r in algodetect._relaxations(ctx):
                    self.note(r["line"], f"relaksacja: jeśli przez bieżący wierzchołek dojdziemy do {_code(r['lhs'])} "
                                         f"taniej, poprawiamy wartość", 3)
            except Exception:  # noqa: BLE001
                pass

    def line_notes(self) -> list[dict]:
        out = []
        for ln in sorted(self.notes):
            items = sorted(self.notes[ln], key=lambda x: -x[0])
            texts = []
            for _, t in items:
                if t not in texts:
                    texts.append(t)
            out.append({"line": ln, "note": " · ".join(texts[:2])})
        return out

    # ------------------------------------------------------------------ algorytmy
    def algorithms_section(self) -> str:
        if not self.algos:
            return ("Nie rozpoznałem żadnej klasycznej techniki — program wygląda na bezpośrednią implementację treści "
                    "zadania (pętle, warunki, obliczenia).")
        main_algos = [a for a in self.algos if a["confidence"] >= 0.5][:3] or self.algos[:1]
        parts = []
        for a in main_algos:
            t = ALGO_TEXT.get(a["id"])
            if not t:
                continue
            title, what, why, cx = t
            lines = ", ".join(str(self.L(x)) for x in a["lines"][:5] if self.L(x) > 0)
            ev = "; ".join(a["evidence"][:3])
            md = f"### {title}" + (f" ({a['variant']})" if a.get("variant") else "") + \
                 f"\n\n{what}\n\n**Dlaczego to działa?** {why}\n\n**Złożoność:** {cx}"
            if ev:
                md += f"\n\n*Po czym to widać:* {ev}" + (f" (linie {lines})." if lines else ".")
            parts.append(md)
        rest = [a for a in self.algos if a not in main_algos and a["confidence"] >= 0.4]
        if rest:
            parts.append("Dodatkowo w kodzie widać: " + ", ".join(f"{a['name']} ({round(100 * a['confidence'])}%)"
                                                                   for a in rest[:5]) + ".")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ całość
    def run(self) -> dict:
        comp = None
        try:
            from oiforge.tools import complexity as cx
            comp = cx.analyze(self.raw)
        except Exception:  # noqa: BLE001
            comp = None
        sections = []
        inp_md, inp_summary = self.input_section()
        sections.append({"id": "input", "title": "Co program wczytuje", "md": inp_md})
        st = self.structures_section()
        if st:
            sections.append({"id": "structures", "title": "Struktury danych", "md": st})
        sections.append({"id": "algorithm", "title": "Algorytm i dlaczego działa", "md": self.algorithms_section()})
        fmd, fdata = self.functions_section(comp)
        if fmd:
            sections.append({"id": "functions", "title": "Funkcje", "md": fmd})
        roles = self.variable_roles()
        if roles:
            rows = "\n".join(f"| `{r['name']}` | {_type_pl(r['type'])} | {r['role_text']}"
                             f"{(' ' + r['detail']) if r['detail'] else ''} | {r['line']} |" for r in roles)
            sections.append({"id": "variables", "title": "Rola zmiennych",
                             "md": "| zmienna | typ | rola | linia |\n|---|---|---|---|\n" + rows})
            for r in roles:
                if r["role"] in ("counter", "sum", "max", "min", "flag", "mid", "pointer"):
                    self.note(r["line"] + self.offset, f"{_code(r['name'])}: {r['role_text']}", 0)
        sections.append({"id": "output", "title": "Co program wypisuje", "md": self.output_section()})
        if comp:
            cmd = f"Szacowana złożoność czasowa: **{comp['complexity']}**."
            if comp.get("hotspots"):
                h = comp["hotspots"][0]
                cmd += f" Najwięcej pracy wykonuje linia {h['line']} ({_code(h['code'][:60])})."
            if comp.get("memory", {}).get("items"):
                cmd += f" Pamięć: ok. {comp['memory']['total_mb']} MB (przy ograniczeniach domyślnych)."
            for w in comp.get("warnings", [])[:3]:
                cmd += f"\n\n⚠️ Linia {w['line']}: {w['message']}"
            sections.append({"id": "complexity", "title": "Złożoność", "md": cmd})
        self.annotate_lines(comp)
        overview = self.overview(inp_summary, comp)
        teach = {}
        try:
            tmd, teach = Teacher(self, comp).overview()
            if tmd:
                overview = tmd
        except Exception:  # noqa: BLE001 — w razie problemu zostaje prostszy opis
            teach = {}
        # sekcja „Algorytm”: gdy nie ma nazwanej techniki, opisz wprost, co i jak program liczy
        if teach.get("computes"):
            for sec in sections:
                if sec["id"] != "algorithm":
                    continue
                items = "\n".join(f"- {c['what']}" + (f" — {c['how']}" if c.get("how") else "")
                                  for c in teach["computes"][:4])
                if not self.algos:
                    sec["md"] = "Program nie używa żadnej „nazwanej” techniki — liczy wynik wprost:\n\n" + items
                else:
                    sec["md"] = "**Co dokładnie jest liczone:**\n\n" + items + "\n\n" + sec["md"]
                if teach.get("improvements"):
                    sec["md"] += "\n\n**Można lepiej:** " + teach["improvements"][0]
        return {"overview_md": overview, "summary": teach, "sections": sections, "line_notes": self.line_notes(),
                "algorithms": self.algos, "functions": fdata, "variables": roles,
                "complexity": {"complexity": comp["complexity"], "hotspots": comp["hotspots"][:5],
                               "warnings": comp["warnings"][:5]} if comp else None}

    def overview(self, inp_summary: list[str], comp: dict | None) -> str:
        c = self.c
        ids = [a["id"] for a in self.algos if a["confidence"] >= 0.5]
        names = [a["name"] for a in self.algos if a["confidence"] >= 0.5 and a["id"] not in (
            "graph", "recursion", "tree", "grid")][:2]
        what = []
        reads = [io for io in c.io if io.kind == "read"]
        if "graph" in ids or "tree" in ids:
            kind = "drzewo" if "tree" in ids else "graf"
            weighted = "dijkstra" in ids or any(re.search(r"pair", d.type) for d in c.decls if self._is_adj(d.name))
            what.append(f"wczytuje {kind}{' z wagami krawędzi' if weighted else ''}")
        elif "grid" in ids:
            what.append("wczytuje planszę")
        elif reads:
            loops = [io for io in reads if c.loops_containing(io.idx)]
            if loops:
                what.append("wczytuje ciąg danych" + (" i zapytania" if len({c.loops_containing(io.idx)[0] for io in loops}) > 1 else ""))
            else:
                what.append("wczytuje kilka liczb")
        if names:
            what.append("korzysta z techniki: " + _join_pl([f"**{n}**" for n in names]))
        writes = [io for io in c.io if io.kind == "write"]
        if writes:
            in_loop = any(c.loops_containing(io.idx) for io in writes)
            what.append("wypisuje " + ("odpowiedzi w pętli" if in_loop else "wynik"))
        s = "Program " + _join_pl(what) + "." if what else "Program nie ma wejścia ani wyjścia — to fragment kodu."
        if comp:
            s += f" Złożoność czasowa: **{comp['complexity']}**."
        nf = len([f for f in c.functions if f.name != "main"])
        if nf:
            s += f" Oprócz main ma {nf} " + _plural(nf, "własną funkcję", "własne funkcje", "własnych funkcji") + "."
        return s


# ============================================================================================== nauczyciel
# „Co program LICZY i jak” — rozpoznawanie typowych obliczeń (suma, maksimum, licznik, pary, sumy fragmentów,
# najdłuższy fragment, sortowanie, wyszukiwanie, odległości w grafie, DP…) z faktów o przypisaniach, pętlach
# i warunkach. Wynik to zdania jak u nauczyciela + złożoność z wyjaśnieniem SKĄD + sugestia ulepszenia.

_LIT_RX = re.compile(r"-?\d+(\.\d+)?(e\d+)?[lLuU]*|true|false|'.'|\w*_(MAX|MIN)|-?1e\d+|INF|inf|-INF|-inf|"
                     r"-?\(?\w*_(MAX|MIN)\)?|nullptr|NULL")


_SANE_TARGET = re.compile(r"&?[A-Za-z_]\w*(\[[^\[\]]{1,20}\])*(\.\w+)*")


def _unparen(s: str) -> str:
    """Zdejmuje zewnętrzne nawiasy tylko wtedy, gdy obejmują całe wyrażenie: `(i < n)` → `i < n`."""
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        for i, ch in enumerate(s):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i < len(s) - 1:
                return s
        s = s[1:-1].strip()
    return s


def _cmp_flip(op: str) -> str:
    return {"<": ">", ">": "<", "<=": ">=", ">=": "<="}.get(op, op)


def _pretty(expr: str) -> str:
    """`a[i]+a[j]==k` → `a[i] + a[j] == k` (dla czytelności zdań)."""
    s = re.sub(r"\s+", " ", expr or "").strip()
    return s


class Teacher:
    """Opis programu „jak nauczyciel”: co wczytuje, CO OBLICZA, jakim sposobem, co wypisuje, złożoność i SKĄD,
    co można poprawić. Działa na faktach z `_cppstruct.Code` (pętle, przypisania, wywołania, wejście/wyjście)."""

    def __init__(self, ex: "Explainer", comp: dict | None):
        self.ex = ex
        self.c = ex.c
        self.comp = comp
        self.algo = {a["id"]: a for a in ex.algos}
        self.ifs = self._find_ifs()
        self.facts = self._scalar_facts()
        self.by_var: dict[str, list] = {}
        for f in self.facts:
            self.by_var.setdefault(f["var"], []).append(f)
        # kod „spłaszczony” bez literałów napisowych (wielkie tablice stałych nie spowalniają wyrażeń regularnych)
        self.flat = normalize_expr(" ".join('""' if t.kind == "str" else t.text for t in self.c.toks))
        self.steps: list[dict] = []        # {what, how, var, line, key}
        self.improve: list[str] = []
        self.used_algos: set[str] = set()

    # ------------------------------------------------------------------ podstawy
    def _find_ifs(self) -> list[dict]:
        c = self.c
        out = []
        for i, t in enumerate(c.texts):
            if t != "if":
                continue
            k = i + 1
            if k < c.n and c.texts[k] == "constexpr":
                k += 1
            if k >= c.n or c.texts[k] != "(" or k not in c.pairs:
                continue
            j = c.pairs[k]
            b0 = j + 1
            if b0 >= c.n:
                continue
            b1 = c.stmt_end(b0)
            els = None
            if b1 + 1 < c.n and c.texts[b1 + 1] == "else":
                els = (b1 + 2, c.stmt_end(b1 + 2))
            out.append({"idx": i, "cond": c.text(k + 1, j - 1), "body": (b0, b1), "else": els})
        return out

    def conds_at(self, idx: int, lo: int = -1) -> list[tuple[str, bool]]:
        """Warunki if (od zewnętrznego) obejmujące token idx; (tekst, True) w gałęzi if, (tekst, False) w else."""
        res = []
        for f in self.ifs:
            if f["idx"] < lo:
                continue
            if f["body"][0] <= idx <= f["body"][1]:
                res.append((f["cond"], True))
            elif f["else"] and f["else"][0] <= idx <= f["else"][1]:
                res.append((f["cond"], False))
        return res

    def loop_info(self, k: int) -> dict:
        c = self.c
        L = c.loops[k]
        v = L.var or ""
        d = {"k": k, "kind": L.kind, "var": v, "line": L.line, "start": None, "bound": None, "op": None,
             "down": False, "dep": set(), "phrase": "", "count": self.ex.loop_count_text(L)}
        outer = {c.loops[j].var for j in c.loops_containing(L.idx) if j != k and c.loops[j].var}
        if L.kind == "for" and v and L.init and L.cond:
            init = c.text(*L.init)
            cond = c.text(*L.cond).split("&&")[0].strip()
            step = c.text(*L.step) if L.step else ""
            ms = re.search(rf"\b{re.escape(v)}\s*=\s*(.+)$", init)
            start = ms.group(1).strip() if ms else "?"
            m = re.match(rf"^{re.escape(v)}\s*(<=|<|!=|>=|>)\s*(.+?)\s*$", _unparen(cond))
            sq = re.match(rf"^(\(\s*long\s+long\s*\)\s*)?{re.escape(v)}\s*\*\s*{re.escape(v)}\s*(<=|<)\s*(.+)$", cond)
            down = bool(re.search(r"--|-=", step))
            d.update(start=start, down=down)
            if sq:
                d.update(op=sq.group(2), bound=sq.group(3).strip(), sqrt=True)
                d["phrase"] = f"dla `{v}` od {start}, dopóki `{v}·{v} ≤ {d['bound']}` (czyli do √{d['bound']})"
            elif m:
                op, bound = m.group(1), m.group(2).strip()
                d.update(op=op, bound=bound)
                stepm = re.search(rf"{re.escape(v)}\s*\+=\s*(\w+)", step)
                if down:
                    d["phrase"] = f"dla `{v}` od {start} w dół do {bound}" + ("" if op in (">=", "<=") else " (bez niej)")
                elif stepm and stepm.group(1) != "1":
                    d["phrase"] = f"dla `{v}` = {start}, {start} + {stepm.group(1)}, … (skokami co `{stepm.group(1)}`) do {bound}"
                else:
                    end = bound if op == "<=" else f"{bound} − 1"
                    if re.fullmatch(r"\w+\s*-\s*1|\w+\s*\+\s*1", bound) and op == "<":
                        end = f"{bound} − 1"
                    d["phrase"] = f"dla każdego `{v}` od {start} do {end}"
            else:
                d["phrase"] = f"w pętli po `{v}` ({_pretty(cond)})"
            d["dep"] = {x for x in outer if re.search(rf"\b{re.escape(x)}\b", f"{start} {d['bound'] or ''}")}
        elif L.kind == "range":
            rng = c.text(*L.range_expr) if L.range_expr else "?"
            dd = c.decl_of(re.match(r"\w+", rng).group(0), L.idx) if re.match(r"\w+", rng) else None
            if dd is not None and dd.kind == "string":
                d["phrase"] = f"dla każdego znaku `{v}` napisu `{rng}`"
            elif re.fullmatch(r"\w+\[\w+\]", rng.replace(" ", "")) and self.ex._is_adj(rng.split("[")[0].strip()):
                d["phrase"] = f"dla każdego sąsiada `{v}` z listy `{rng}`"
            else:
                d["phrase"] = f"dla każdego elementu `{v}` z `{rng}`"
            d["range"] = rng
        elif L.kind in ("while", "do") and L.cond:
            ct = c.text(*L.cond)
            cn = normalize_expr(ct)
            mm = re.fullmatch(r"(\w+)--(>0)?|--(\w+)", cn)
            if mm:
                x = mm.group(1) or mm.group(3)
                noun = {"t": "testów", "z": "zestawów", "q": "zapytań", "m": "operacji"}.get(x.lower(), "powtórzeń")
                d["phrase"] = f"dla każdego z `{x}` {noun}"
                d["countdown"] = x
            elif re.fullmatch(r"!(\w+)\.empty\(\)", cn):
                q = re.fullmatch(r"!(\w+)\.empty\(\)", cn).group(1)
                d["phrase"] = f"dopóki `{q}` nie jest pusta"
            elif re.fullmatch(r"(\w+)<=?(\w+)", cn) and self.algo.get("binary_search"):
                d["phrase"] = f"dopóki przedział [`{re.fullmatch(r'(\w+)<=?(\w+)', cn).group(1)}`, " \
                              f"`{re.fullmatch(r'(\w+)<=?(\w+)', cn).group(2)}`] jest niepusty"
            elif re.match(r"(std::)?cin>>", cn):
                d["phrase"] = "aż do końca wejścia"
            else:
                d["phrase"] = f"dopóki `{_pretty(ct)}`"
        return d

    def _scalar_facts(self) -> list[dict]:
        c = self.c
        out = []
        heads = [L.head for L in c.loops if L.kind in ("for", "range") and L.head]
        for a in c.assigns:
            name = a.target
            if not name or a.indexed or normalize_expr(a.target_text) != name:
                continue
            if any(h[0] <= a.idx <= h[1] for h in heads):
                continue
            d = c.decl_of(name, a.idx)
            if d is not None and d.kind != "scalar":
                continue
            rt = c.text(*a.rhs) if a.rhs else ""
            rn = normalize_expr(rt)
            f = {"var": name, "idx": a.idx, "line": a.line, "op": a.op, "rhs": rt, "rn": rn, "func": a.func,
                 "loops": c.loops_containing(a.idx), "kind": None, "expr": None, "args": []}
            en = re.escape(name)
            if a.op == "+=":
                f["kind"], f["expr"] = ("inc", None) if rn == "1" else ("sum", rt)
            elif a.op == "-=":
                f["kind"], f["expr"] = ("dec", None) if rn == "1" else ("sub", rt)
            elif a.op == "++":
                f["kind"] = "inc"
            elif a.op == "--":
                f["kind"] = "dec"
            elif a.op == "*=":
                f["kind"], f["expr"] = "prod", rt
            elif a.op == "^=":
                f["kind"], f["expr"] = "xor", rt
            elif a.op == "=" and a.rhs:
                cl = next((x for x in c.calls if x.idx == a.rhs[0] and x.name in ("max", "min")
                           and x.close_idx == a.rhs[1]), None)
                if cl is not None:
                    args = [c.text(*r) for r in cl.args]
                    others = [x for x in args if normalize_expr(x) != name]
                    f["args"] = args
                    if len(others) < len(args):
                        f["kind"], f["expr"] = cl.name, (others[0] if others else "")
                    else:
                        f["kind"], f["expr"] = cl.name + "_of", None
                elif re.fullmatch(rf"{en}\+1|1\+{en}", rn):
                    f["kind"] = "inc"
                elif re.fullmatch(rf"\(?{en}\*(.+?)\)?%(.+)", rn):
                    mm = re.fullmatch(rf"\(?{en}\*(.+?)\)?%(.+)", rn)
                    f["kind"], f["expr"], f["mod"] = "prodmod", mm.group(1), mm.group(2)
                elif rn.startswith(name + "+"):
                    f["kind"], f["expr"] = "sum", rt.split("+", 1)[1].strip()
                elif rn.startswith(name + "*"):
                    f["kind"], f["expr"] = "prod", rt.split("*", 1)[1].strip()
                elif _LIT_RX.fullmatch(rn):
                    f["kind"], f["expr"] = "reset", rt
                else:
                    f["kind"], f["expr"] = "set", rt
                    conds = self.conds_at(a.idx)
                    if conds and conds[-1][1]:
                        cn = normalize_expr(conds[-1][0])
                        e = re.escape(rn)
                        if re.fullmatch(rf"{e}>=?{en}|{en}<=?{e}", cn):
                            f["kind"] = "max"
                        elif re.fullmatch(rf"{e}<=?{en}|{en}>=?{e}", cn):
                            f["kind"] = "min"
            if f["kind"]:
                out.append(f)
        return out

    def facts_of(self, var: str, *kinds) -> list[dict]:
        return [f for f in self.by_var.get(var, []) if not kinds or f["kind"] in kinds]

    def reset_loop(self, var: str) -> int | None:
        """Pętla, w której zmienna jest zerowana na nowo (np. `long long s = 0;` w pętli po i)."""
        rs = [f for f in self.facts_of(var, "reset") if f["loops"]]
        return rs[-1]["loops"][-1] if rs else None

    def read_in(self, k: int) -> set[str]:
        c = self.c
        L = c.loops[k]
        out = set()
        for io in c.io:
            if io.kind == "read" and L.body[0] <= io.idx <= L.body[1]:
                for t in io.targets:
                    m = re.match(r"\s*&?\s*([A-Za-z_]\w*)\s*$", t)
                    if m:
                        out.add(m.group(1))
        return out

    def _elem(self, expr: str, k: int | None) -> tuple[str, str] | None:
        """Czy wyrażenie to „kolejny element”: `a[i]` (i = zmienna pętli) albo wczytana zmienna. → (rodzaj, nazwa)."""
        e = normalize_expr(expr)
        m = re.fullmatch(r"(\w+)\[(\w+)\]", e)
        if m:
            return ("arr", m.group(1))
        if re.fullmatch(r"\w+", e) and k is not None and e in self.read_in(k):
            return ("read", e)
        if re.fullmatch(r"\w+", e) and k is not None and self.c.loops[k].kind == "range" and self.c.loops[k].var == e:
            rng = self.c.text(*self.c.loops[k].range_expr) if self.c.loops[k].range_expr else e
            return ("arr", rng)
        return None

    # ------------------------------------------------------------------ rozpoznawanie obliczeń
    def analyze(self) -> None:
        for fn in (self._r_kadane, self._r_fragment_sums, self._r_longest, self._r_pairs, self._r_knapsack,
                   self._r_lis, self._r_fib, self._r_letters, self._r_bubble, self._r_prodmod, self._r_sort,
                   self._r_binsearch, self._r_twoptr, self._r_minmax, self._r_sum, self._r_count, self._r_flag,
                   self._r_algos, self._r_manual_sort, self._r_generic_algos, self._r_containers):
            try:
                fn()
            except Exception:  # noqa: BLE001 — pojedyncza heurystyka nie może wywalić objaśnienia
                pass

    def _step(self, key, what, how="", var=None, line=None, improve=None, noun=None):
        if any(s["key"] == key for s in self.steps):
            return
        self.steps.append({"key": key, "what": what, "how": how, "var": var, "line": line, "noun": noun})
        if improve and improve not in self.improve:
            self.improve.append(improve)

    def _r_kadane(self):
        for f in self.facts:
            if f["kind"] != "max_of" or len(f["args"]) != 2:
                continue
            cur = f["var"]
            a0, a1 = (normalize_expr(x) for x in f["args"])
            if a1.startswith(cur + "+") and a1[len(cur) + 1:] == a0 or a0.startswith(cur + "+") and a0[len(cur) + 1:] == a1:
                el = a0 if not a0.startswith(cur + "+") else a1
                best = next((g for g in self.facts if g["kind"] == "max" and normalize_expr(g["expr"] or "") == cur), None)
                arr = re.match(r"(\w+)", el).group(1)
                self._step("kadane", f"**największą sumę spójnego fragmentu** tablicy `{arr}`",
                           f"algorytmem Kadane'a: `{cur}` to najlepsza suma fragmentu kończącego się na bieżącej "
                           f"pozycji — albo zaczynamy nowy fragment od `{el}`, albo przedłużamy poprzedni "
                           f"(`{cur} + {el}`)" + (f"; `{best['var']}` pamięta najlepszy wynik" if best else ""),
                           var=best["var"] if best else cur, line=f["line"])
                self.used_vars = getattr(self, "used_vars", set()) | {cur} | ({best["var"]} if best else set())

    def _r_fragment_sums(self):
        c = self.c
        for f in self.facts:
            if f["kind"] not in ("max", "min") or not re.fullmatch(r"\w+", normalize_expr(f["expr"] or "")):
                continue
            s = normalize_expr(f["expr"])
            sums = [g for g in self.facts_of(s, "sum") if g["loops"]]
            if not sums:
                continue
            g = sums[0]
            m = re.fullmatch(r"(\w+)\[(\w+)\]", normalize_expr(g["expr"] or ""))
            if not m:
                continue
            arr, kv = m.group(1), m.group(2)
            kl = next((j for j in reversed(g["loops"]) if c.loops[j].var == kv), None)
            rl = self.reset_loop(s)
            if kl is None or rl is None:
                continue
            li = self.loop_info(kl)
            best = f["var"]
            word = "największą" if f["kind"] == "max" else "najmniejszą"
            if li["start"] and li["dep"] and li["bound"] and re.fullmatch(r"\w+", li["bound"]) and \
                    li["bound"] in {c.loops[j].var for j in g["loops"]}:
                i, j = li["start"], li["bound"]
                how = (f"dla każdego początku `{i}` i każdego końca `{j}` ≥ `{i}` osobną pętlą po `{kv}` sumuje "
                       f"`{arr}[{i}..{j}]` w zmiennej `{s}` (akumulator zerowany dla każdego fragmentu), a `{best}` "
                       f"pamięta {'największą' if f['kind'] == 'max' else 'najmniejszą'} z tych sum")
                imp = (f"Sumę `{arr}[{i}..{j}]` można liczyć przyrostowo — `{s} += {arr}[{j}]` przy przesuwaniu `{j}` — "
                       f"albo z sum prefiksowych (`p[{j}+1] − p[{i}]`), co usuwa trzecią pętlę: O(n²). "
                       f"Algorytm Kadane'a daje O(n): `cur = max({arr}[i], cur + {arr}[i]); best = max(best, cur);`."
                       if f["kind"] == "max" else
                       f"Trzecią pętlę usuniesz sumami prefiksowymi albo liczeniem `{s}` przyrostowo — O(n²).")
            else:
                i = next((c.loops[j].var for j in g["loops"] if j == rl), "i")
                how = (f"dla każdego początku `{i}` przesuwa koniec `{kv}` w prawo i dokłada `{arr}[{kv}]` do sumy "
                       f"`{s}` (zerowanej dla każdego początku), więc `{s}` = suma `{arr}[{i}..{kv}]`; `{best}` "
                       f"pamięta {'największą' if f['kind'] == 'max' else 'najmniejszą'} z nich")
                imp = (f"Algorytm Kadane'a liczy to samo w O(n) jedną pętlą: `cur = max({arr}[i], cur + {arr}[i]); "
                       f"best = max(best, cur);`." if f["kind"] == "max" else "")
            self._step("fragsum", f"**{word} sumę spójnego fragmentu** tablicy `{arr}`", how, var=best,
                       line=f["line"], improve=imp or None)
            self.used_vars = getattr(self, "used_vars", set()) | {s, best}

    def _r_longest(self):
        for f in self.facts:
            if f["kind"] != "max" or not re.fullmatch(r"\w+", normalize_expr(f["expr"] or "")):
                continue
            cur = normalize_expr(f["expr"])
            incs = [g for g in self.facts_of(cur, "inc") if g["loops"]]
            resets = [g for g in self.facts_of(cur, "reset") if g["loops"]]
            if not incs or not resets:
                continue
            conds = self.conds_at(incs[0]["idx"])
            cond = conds[-1][0] if conds else ""
            cn = normalize_expr(cond)
            kind = ""
            m = re.fullmatch(r"(\w+)\[(\w+)\](>|<|>=|<=|==|!=)\1\[\2-1\]", cn)
            if m:
                kind = {">": "ściśle rosnącego", "<": "ściśle malejącego", ">=": "niemalejącego", "<=": "nierosnącego",
                        "==": "o równych elementach", "!=": "bez dwóch równych sąsiadów"}[m.group(3)]
                arr = m.group(1)
                what = f"**długość najdłuższego {kind} spójnego fragmentu** tablicy `{arr}`"
            else:
                what = f"**długość najdłuższego spójnego fragmentu**, w którym zachodzi `{_pretty(cond)}`" if cond else \
                    "**długość najdłuższej serii**"
            how = (f"`{cur}` to długość bieżącej serii: rośnie o 1, gdy `{_pretty(cond)}`, a w przeciwnym razie zaczyna "
                   f"się od nowa (`{cur} = {resets[-1]['expr']}`); `{f['var']}` pamięta najdłuższą serię")
            self._step("longest", what, how, var=f["var"], line=f["line"])
            self.used_vars = getattr(self, "used_vars", set()) | {cur, f["var"]}

    def _r_pairs(self):
        c = self.c
        for f in self.facts:
            if f["kind"] != "inc" or len(f["loops"]) < 2:
                continue
            li_o, li_i = self.loop_info(f["loops"][-2]), self.loop_info(f["loops"][-1])
            if not (li_o["var"] and li_i["var"] and li_i["start"]):
                continue
            st = normalize_expr(li_i["start"])
            if st not in (f"{li_o['var']}+1",) and not (st == li_o["var"] and False):
                continue
            conds = self.conds_at(f["idx"], lo=c.loops[f["loops"][-2]].idx)
            cond = conds[-1][0] if conds else ""
            i, j = li_o["var"], li_i["var"]
            what = f"**liczbę par** (`{i}`, `{j}`), `{i}` < `{j}`" + (f", dla których `{_pretty(cond)}`" if cond else "")
            how = f"sprawdza każdą parę: {li_o['phrase']} i {li_i['phrase']}" + \
                  (f" — gdy `{_pretty(cond)}`, zwiększa licznik `{f['var']}`" if cond else f", zwiększając `{f['var']}`")
            imp = None
            cn = normalize_expr(cond)
            if re.fullmatch(r"(\w+)\[\w+\]\+\1\[\w+\]==\w+", cn) or re.fullmatch(r"\w+==(\w+)\[\w+\]\+\1\[\w+\]", cn):
                imp = ("Zamiast sprawdzać wszystkie pary (O(n²)): posortuj tablicę i idź dwoma wskaźnikami z obu końców "
                       "(O(n log n)) albo zliczaj w `unordered_map`, ile razy wystąpiło `k − a[j]` (O(n)).")
            elif cond:
                imp = "Dla n do ~10⁴ to wystarczy; dla n ~ 10⁵ szukaj sortowania, dwóch wskaźników albo struktury danych, " \
                      "żeby nie sprawdzać każdej pary."
            self._step("pairs", what, how, var=f["var"], line=f["line"], improve=imp)

    def _r_binsearch(self):
        c = self.c
        if "binary_search" not in self.algo:
            return
        for k, L in enumerate(c.loops):
            if L.kind != "while" or not L.cond:
                continue
            cn = normalize_expr(c.text(*L.cond))
            m = re.fullmatch(r"(\w+)<=?(\w+)", cn)
            if not m:
                continue
            lo, hi = m.groups()
            for f in self.ifs:
                if not (L.body[0] <= f["idx"] <= L.body[1]):
                    continue
                body = normalize_expr(c.text(*f["body"]))
                ma = re.search(r"(\w+)=(\w+);", body)
                if not ma or ma.group(1) in (lo, hi):
                    continue
                ans, mid = ma.groups()
                smallest = re.search(rf"{re.escape(hi)}={re.escape(mid)}-1", body)
                cond = _pretty(f["cond"])
                q = any(c.loops[j].kind in ("while", "for") and j != k for j in c.loops_containing(L.idx))
                what = (("dla każdego zapytania " if q else "") +
                        f"**szuka binarnie {'najmniejszego' if smallest else 'największego'} `{mid}`, dla którego "
                        f"`{cond}`** (wynik w `{ans}`)")
                how = (f"trzyma przedział [`{lo}`, `{hi}`], sprawdza środek `{mid}` i odrzuca połowę, w której odpowiedzi "
                       f"na pewno nie ma — działa, bo warunek jest monotoniczny (tablica jest posortowana)")
                self._step("binsearch", what, how, var=ans, line=L.line,
                           noun=f"znaleziony binarnie {'najmniejszy' if smallest else 'największy'} `{mid}`, dla którego "
                                f"`{cond}`")
                self.used_vars = getattr(self, "used_vars", set()) | {ans, lo, hi, mid}
                self.used_algos.add("binary_search")
                return

    def _r_twoptr(self):
        if "two_pointers" not in self.algo:
            return
        mm = next((f for f in self.facts if f["kind"] in ("min", "max") and
                   re.search(r"-\s*\w+\s*\+\s*1", f["expr"] or "")), None)
        sums = [f["var"] for f in self.facts if f["kind"] in ("sum", "sub") and f["loops"]]
        if mm:
            word = "najkrótszego" if mm["kind"] == "min" else "najdłuższego"
            loops = [L for L in self.c.loops if L.kind == "while" and L.cond]
            cond = _pretty(self.c.text(*loops[0].cond)) if loops else ""
            self._step("twoptr", f"**długość {word} spójnego fragmentu**" + (f", dla którego `{cond}`" if cond else "")
                       + f" (wynik w `{mm['var']}`)",
                       "dwoma wskaźnikami (gąsienicą): prawy koniec dokłada kolejny element do okna, a lewy je skraca, "
                       "dopóki warunek dalej zachodzi — każdy wskaźnik przechodzi tablicę tylko raz", var=mm["var"])
            self.used_vars = getattr(self, "used_vars", set()) | {mm["var"]} | set(sums)
            self.used_algos.add("two_pointers")

    def _r_minmax(self):
        for f in self.facts:
            if f["kind"] not in ("max", "min") or not f["loops"]:
                continue
            e = self._elem(f["expr"] or "", f["loops"][-1])
            if not e:
                continue
            if self._covered(f["var"]):
                continue
            word = "największy" if f["kind"] == "max" else "najmniejszy"
            conds = [x for x in self.conds_at(f["idx"], lo=self.c.loops[f["loops"][-1]].idx)
                     if not re.search(rf"\b{re.escape(f['var'])}\b", x[0])]
            extra = f" spośród tych, dla których `{_pretty(conds[-1][0])}`" if conds else ""
            if e[0] == "arr":
                what = f"**{word} element** `{e[1]}`{extra}"
            else:
                what = f"**{'największą' if f['kind'] == 'max' else 'najmniejszą'} z wczytanych liczb**{extra}"
            how = f"przechodzi po elementach ({self.loop_info(f['loops'][-1])['phrase']}) i trzyma w `{f['var']}` " \
                  f"bieżące {'maksimum' if f['kind'] == 'max' else 'minimum'}"
            self._step(f"minmax:{f['var']}", what, how, var=f["var"], line=f["line"])

    def _covered(self, var: str) -> bool:
        return any(s.get("var") == var for s in self.steps) or var in getattr(self, "used_vars", set())

    def _r_sum(self):
        c = self.c
        for var, fs in self.by_var.items():
            sums = [f for f in fs if f["kind"] == "sum" and f["loops"]]
            if not sums or self._covered(var):
                continue
            f = sums[0]
            k = f["loops"][-1]
            e = self._elem(f["expr"] or "", k)
            rl = self.reset_loop(var)
            li = self.loop_info(k)
            conds = [x for x in self.conds_at(f["idx"], lo=c.loops[k].idx)]
            extra = f" tych, dla których `{_pretty(conds[-1][0])}`" if conds and conds[-1][1] else ""
            per = ""
            if rl is not None and rl != k:
                pl = self.loop_info(rl)
                per = f" ({pl['phrase']} osobno)"
            # zapytania o sumę przedziału liczone pętlą
            if e and e[0] == "arr" and li["start"] and li["bound"] and rl is not None and \
                    re.fullmatch(r"\w+", li["start"]) and not li["start"].isdigit() and li["start"] in self.read_in(rl):
                arr = e[1]
                self._step("rangesum", f"dla każdego zapytania **sumę fragmentu** `{arr}[{li['start']}..{li['bound']}]`",
                           f"pętlą {li['phrase']} dodaje elementy do `{var}`", var=var, line=f["line"],
                           improve=f"Każde zapytanie kosztuje tu O(długość przedziału). Sumy prefiksowe "
                                   f"(`p[i] = p[i−1] + {arr}[i]`) dają każdą sumę w O(1): `p[{li['bound']}] − "
                                   f"p[{li['start']}−1]`.")
                continue
            if e and e[0] == "arr":
                what = f"**sumę{extra or ''} elementów** `{e[1]}`" if not extra else f"**sumę elementów** `{e[1]}`{extra.replace(' tych', ', ale tylko tych')}"
            elif e and e[0] == "read":
                what = "**sumę wczytanych liczb**" + (f" — tylko{extra}" if extra else "")
            else:
                what = f"**sumę wartości** `{_pretty(f['expr'] or '')}`" + (f" dla{extra}" if extra else "")
            avg = any(re.fullmatch(rf"\(?(double|float|long double)?\)?{re.escape(var)}/\w+", normalize_expr(t))
                      for io in c.io if io.kind == "write" for t in io.targets)
            if avg:
                what = f"**średnią arytmetyczną** wczytanych liczb (suma `{var}` dzielona przez ich liczbę)"
            self._step(f"sum:{var}", what + per, f"{li['phrase']} dodaje kolejną wartość do akumulatora `{var}`",
                       var=var, line=f["line"])

    def _r_count(self):
        c = self.c
        for var, fs in self.by_var.items():
            incs = [f for f in fs if f["kind"] == "inc" and f["loops"]]
            if not incs or self._covered(var):
                continue
            f = incs[0]
            if self.c.loops[f["loops"][-1]].var == var:
                continue
            k = f["loops"][-1]
            conds = [x for x in self.conds_at(f["idx"], lo=c.loops[k].idx) if x[1]]
            cond = conds[-1][0] if conds else ""
            cn = normalize_expr(cond)
            li = self.loop_info(k)
            if "sieve" in self.algo and re.fullmatch(r"!?\w+\[\w+\]", cn):
                what = "**ile jest liczb pierwszych**" + (f" do `{li['bound']}`" if li["bound"] else "")
                how = f"po zbudowaniu sita przechodzi {li['phrase']} i liczy w `{var}` liczby niewykreślone"
            elif re.fullmatch(r"!(\w+)\[(\w+)\]", cn) and any(cl.name in c.func_by_name for cl in c.calls
                                                              if c.loops[k].body[0] <= cl.idx <= c.loops[k].body[1]):
                what = "**liczbę spójnych składowych** grafu"
                how = (f"{li['phrase']}: jeśli wierzchołek nie był jeszcze odwiedzony, zaczyna z niego przeszukiwanie "
                       f"(które odwiedza całą jego składową) i zwiększa `{var}`")
            elif cond:
                e = None
                what = f"**ile razy zachodzi warunek** `{_pretty(cond)}`"
                rd = self.read_in(k)
                if any(re.search(rf"\b{re.escape(x)}\b", cn) for x in rd) or re.search(r"\w+\[\w+\]", cn):
                    what = f"**ile elementów spełnia warunek** `{_pretty(cond)}`"
                how = f"{li['phrase']} sprawdza warunek i zwiększa licznik `{var}`"
            else:
                continue
            self._step(f"count:{var}", what, how, var=var, line=f["line"])

    def _r_flag(self):
        c = self.c
        for var, fs in self.by_var.items():
            if self._covered(var):
                continue
            d = c.decl_of(var, fs[0]["idx"])
            sets = [f for f in fs if f["kind"] == "reset" and normalize_expr(f["expr"] or "") in ("true", "false", "0", "1")]
            inloop = [f for f in sets if f["loops"]]
            if not inloop or (d is not None and d.type.strip() != "bool" and not
                              all(normalize_expr(f["expr"]) in ("true", "false") for f in sets)):
                continue
            f = inloop[0]
            conds = self.conds_at(f["idx"], lo=c.loops[f["loops"][-1]].idx)
            cond = normalize_expr(conds[-1][0]) if conds else ""
            m = re.fullmatch(r"(\w+)\[(\w+)\]!=\1\[(\w+)-1-\2\]|(\w+)\[(\w+)\]!=\4\[(\w+)-\5-1\]", cond)
            if m:
                s = m.group(1) or m.group(4)
                self._step(f"flag:{var}", f"**czy napis `{s}` jest palindromem**",
                           f"porównuje znaki parami od obu końców (`{s}[i]` z `{s}[n−1−i]`); pierwsza różnica ustawia "
                           f"`{var}` na fałsz", var=var, line=f["line"])
                continue
            m = re.fullmatch(r"(\w+)%(\w+)==0", cond)
            if m and self.loop_info(f["loops"][-1]).get("sqrt"):
                self._step(f"flag:{var}", f"**czy `{m.group(1)}` jest liczbą pierwszą**",
                           f"sprawdza dzielniki `{m.group(2)}` tylko do √{m.group(1)} — jeśli liczba ma dzielnik, "
                           f"to ma też dzielnik ≤ √{m.group(1)}", var=var, line=f["line"])
                continue
            if any(d2.kind == "stack" for d2 in c.decls) and re.search(r"empty", cond):
                self._step(f"flag:{var}", "**czy nawiasy są poprawnie sparowane**",
                           "nawias otwierający trafia na stos, zamykający zdejmuje ostatni otwarty; błąd, gdy stos jest "
                           f"pusty przy zamykaniu albo niepusty na końcu (flaga `{var}`)", var=var, line=f["line"])
                continue
            val = normalize_expr(f["expr"])
            pos = val in ("true", "1")
            if cond:
                what = f"**czy {'istnieje element, dla którego' if pos else 'żaden element nie łamie warunku —'} " \
                       f"`{_pretty(conds[-1][0])}`**" if pos else f"**czy warunek `{_pretty(conds[-1][0])}` nigdy nie zachodzi**"
                self._step(f"flag:{var}", what, f"flaga `{var}` zmienia się na {'prawdę' if pos else 'fałsz'}, gdy "
                                                f"warunek zajdzie", var=var, line=f["line"])

    def _r_prodmod(self):
        for f in self.facts:
            if f["kind"] != "prodmod" or not f["loops"]:
                continue
            L = self.c.loops[f["loops"][-1]]
            body = normalize_expr(self.c.text(L.idx, L.body[1]))
            if re.search(r">>=|/=2|>>1|&1", body):
                continue                     # to szybkie potęgowanie (wykładnik dzielony przez 2)
            li = self.loop_info(f["loops"][-1])
            cnt = li["count"] or li["bound"] or "k"
            self._step("prodmod", f"**`{f['expr']}` do potęgi `{cnt}` modulo `{f['mod']}`**",
                       f"mnoży `{f['var']}` przez `{f['expr']}` {cnt} razy, za każdym razem biorąc resztę z dzielenia przez "
                       f"`{f['mod']}` (żeby liczby się nie przepełniały)", var=f["var"], line=f["line"],
                       improve=f"Szybkie potęgowanie liczy to w O(log {cnt}) zamiast O({cnt}): podnosi podstawę do kwadratu "
                               f"i mnoży wynik tylko dla zapalonych bitów wykładnika.")

    def _r_sort(self):
        c = self.c
        for cl in c.calls:
            if cl.name not in ("sort", "stable_sort") or cl.is_method or len(cl.args) < 2:
                continue
            a0 = c.text(*cl.args[0])
            m = re.match(r"\s*([\w\[\]\s.]+?)\s*\.\s*begin\s*\(\s*\)|\s*(\w+)\s*(\+\s*\w+)?\s*$", a0)
            cont = (m.group(1) or m.group(2)) if m else a0
            order = "rosnąco"
            if len(cl.args) >= 3:
                cmpt = c.text(*cl.args[2])
                cmpn = normalize_expr(cmpt)
                if "greater" in cmpn:
                    order = "malejąco"
                else:
                    body = cmpn
                    if re.fullmatch(r"\w+", cmpn) and cmpn in c.func_by_name:
                        fn = c.func_by_name[cmpn]
                        body = normalize_expr(c.text(fn.open_idx, fn.close_idx))
                    r = re.search(r"return(.+?);", body)
                    expr = r.group(1) if r else ""
                    mm = re.fullmatch(r"(\w+)\.(\w+)(<|>)(\w+)\.(\w+)", expr)
                    mm2 = re.fullmatch(r"(\w+)\.(size|length)\(\)(<|>)(\w+)\.(size|length)\(\)", expr)
                    if mm and mm.group(2) == mm.group(5):
                        fld = mm.group(2)
                        fname = {"first": "pierwszego elementu pary (`first`)", "second": "drugiego elementu pary (`second`)"}.get(
                            fld, f"pola `{fld}`")
                        order = f"{'rosnąco' if mm.group(3) == '<' else 'malejąco'} według {fname}"
                    elif mm2:
                        order = f"{'rosnąco' if mm2.group(3) == '<' else 'malejąco'} według długości"
                    elif re.fullmatch(r"(\w+)>(\w+)", expr):
                        order = "malejąco"
                    elif expr:
                        order = f"według własnego porządku (komparator: `{_pretty(r.group(1)) if r else cmpt[:60]}`)"
                    else:
                        order = "według własnego komparatora"
            fully = re.search(r"\.end\s*\(\s*\)|\+\s*\w+", c.text(*cl.args[1]))
            self._step(f"sort:{cont}:{cl.line}", f"**sortuje `{cont}` {order}**",
                       f"najpierw sortuje `{cont}` funkcją `{cl.name}` (O(n log n))"
                       + ("" if fully else " — tylko wskazany fragment"), var=cont, line=cl.line)
            self.used_algos.add("sorting")

    def _r_bubble(self):
        c = self.c
        for cl in c.calls:
            if cl.name != "swap" or len(cl.args) != 2 or len(c.loops_containing(cl.idx)) < 2:
                continue
            x, y = (normalize_expr(c.text(*r)) for r in cl.args)
            m = re.fullmatch(r"(\w+)\[(\w+)\]", x)
            if not m or y != f"{m.group(1)}[{m.group(2)}+1]":
                continue
            conds = self.conds_at(cl.idx)
            cn = normalize_expr(conds[-1][0]) if conds else ""
            order = "malejąco" if re.search(r"<", cn) else "rosnąco"
            self._step("bubble", f"**sortuje tablicę `{m.group(1)}` {order} metodą bąbelkową**",
                       "porównuje sąsiednie elementy i zamienia je, gdy stoją w złej kolejności; po każdym przebiegu "
                       "największy element „wypływa” na koniec", var=m.group(1), line=cl.line,
                       improve="Sortowanie bąbelkowe to O(n²). `sort(a, a + n)` (O(n log n)) robi to samo dużo szybciej.")
            self.used_algos.add("sorting")

    def _r_letters(self):
        c = self.c
        flat = self.flat
        m = re.search(r"(\w+)\[(\w+)-'a'\](\+\+|\+=1)", flat) or re.search(r"(\w+)\[(\w+)-'A'\](\+\+|\+=1)", flat)
        if m:
            self._step("letters", f"**ile razy występuje każda litera** (licznik `{m.group(1)}[litera − 'a']`)",
                       f"dla każdego znaku zwiększa licznik jego litery — `{m.group(2)} − 'a'` zamienia literę na numer 0–25",
                       var=m.group(1))

    def _r_knapsack(self):
        c = self.c
        flat = self.flat
        m = re.search(r"(\w+)\[(\w+)\]=max\(\1\[\2\],\1\[\2-(\w+)\[(\w+)\]\]\+(\w+)\[\4\]\)", flat)
        if m:
            dp, j, w, i, v = m.groups()
            down = any(L.var == j and L.step and re.search(r"--|-=", c.text(*L.step)) for L in c.loops)
            what = (f"**największą łączną wartość przedmiotów o łącznej wadze ≤ pojemności** — problem plecakowy "
                    f"{'0/1 (każdy przedmiot najwyżej raz)' if down else 'bez ograniczeń (przedmioty można powtarzać)'}")
            how = (f"`{dp}[{j}]` = najlepsza wartość przy wadze co najwyżej `{j}`; dla każdego przedmiotu `{i}` "
                   f"sprawdza, czy opłaca się go dołożyć: `{dp}[{j}] = max({dp}[{j}], {dp}[{j} − {w}[{i}]] + {v}[{i}])`"
                   + (f" — pętla po `{j}` idzie w dół, żeby przedmiot nie został wzięty dwa razy" if down else ""))
            self._step("knapsack", what, how, var=dp, noun="największą wartość plecaka")
            self.used_algos.add("dp")
            return
        # wydawanie reszty: najmniej monet / liczba sposobów / osiągalne sumy
        m = re.search(r"(\w+)\[(\w+)\]=min\(\1\[\2\],\1\[\2-([\w\[\]]+)\]\+1\)", flat)
        if m:
            dp, j, coin = m.groups()
            self._step("coins", f"**najmniejszą liczbę monet (przedmiotów), które dają sumę** — `{dp}[{j}]` dla każdej "
                                f"kwoty `{j}`",
                       f"dla każdej kwoty `{j}` sprawdza, czy opłaca się użyć monety `{coin}`: "
                       f"`{dp}[{j}] = min({dp}[{j}], {dp}[{j} − {coin}] + 1)` (start: `{dp}[0] = 0`, reszta „nieskończoność”)",
                       var=dp, noun="najmniejszą liczbę monet")
            self.used_algos.add("dp")
            return
        m = re.search(r"(\w+)\[(\w+)\]\+=\1\[\2-([\w\[\]]+)\]", flat)
        if m:
            dp, j, coin = m.groups()
            self._step("ways", f"**na ile sposobów można uzyskać każdą sumę** `{j}` (tablica `{dp}`)",
                       f"`{dp}[{j}] += {dp}[{j} − {coin}]` — każdy sposób na `{j} − {coin}` po dołożeniu `{coin}` daje "
                       f"sposób na `{j}` (start: `{dp}[0] = 1`)", var=dp, noun="liczbę sposobów")
            self.used_algos.add("dp")
            return
        m = re.search(r"(\w+)\[(\w+)\]\|=\1\[\2-([\w\[\]]+)\]|if\((\w+)\[(\w+)-([\w\[\]]+)\]\)\4\[\5\]=(true|1)", flat)
        if m:
            dp = m.group(1) or m.group(4)
            self._step("subsetsum", f"**które sumy da się uzyskać** z podanych liczb (tablica `{dp}`)",
                       "suma jest osiągalna, jeśli osiągalna była suma mniejsza o bieżącą liczbę", var=dp,
                       noun="osiągalne sumy")
            self.used_algos.add("dp")

    def _r_lis(self):
        flat = self.flat
        m = re.search(r"if\((\w+)\[(\w+)\]<\1\[(\w+)\]\)(\w+)\[\3\]=max\(\4\[\3\],\4\[\2\]\+1\)", flat)
        if m:
            a, j, i, dp = m.groups()
            bv = next((f["var"] for f in self.facts if f["kind"] == "max" and
                       re.fullmatch(rf"{re.escape(dp)}\[\w+\]", normalize_expr(f["expr"] or ""))), None)
            if bv:
                self.used_vars = getattr(self, "used_vars", set()) | {bv}
            self._step("lis", f"**długość najdłuższego rosnącego podciągu** tablicy `{a}`",
                       f"`{dp}[{i}]` = długość najdłuższego rosnącego podciągu kończącego się na `{a}[{i}]`; sprawdza "
                       f"wszystkie wcześniejsze `{j}` < `{i}` z `{a}[{j}] < {a}[{i}]` i przedłuża najlepszy z nich",
                       var=bv or dp, improve="LIS da się policzyć w O(n log n): trzymaj tablicę „najmniejszych końców” podciągów "
                                       "każdej długości i wstawiaj elementy przez `lower_bound`.")
            self.used_algos.add("dp")

    def _r_fib(self):
        c = self.c
        for f in c.functions:
            body = normalize_expr(c.text(f.open_idx, f.close_idx))
            nm = re.escape(f.name)
            if re.search(rf"{nm}\((\w+)-1\)\+{nm}\(\1-2\)", body):
                memo = re.search(r"\w+\[\w+\]!=-1|\w+\[\w+\]=" + nm, body) or re.search(r"if\(\w+\[\w+\]\)return", body)
                self._step("fib", f"**n-ty wyraz ciągu Fibonacciego** (funkcja `{f.name}`)",
                           f"rekurencyjnie: `{f.name}(n) = {f.name}(n−1) + {f.name}(n−2)`, z warunkiem bazowym dla małych n",
                           var=f.name, line=f.line,
                           improve=None if memo else
                           f"Bez zapamiętywania `{f.name}` liczy te same wartości wiele razy — czas rośnie wykładniczo "
                           f"(ok. 1,62ⁿ). Zapamiętuj wyniki w tablicy (memoizacja) albo licz pętlą od dołu: O(n).")
                self.used_algos.add("recursion")

    def _r_algos(self):
        c = self.c
        A = self.algo
        start = None
        m = re.search(r"(\w+)\[(\w+)\]=0;", self.flat)
        if m:
            start = m.group(2)
        if "dijkstra" in A:
            dist = next(iter(self.ex._relax_targets()), "dist")
            self._step("dijkstra", f"**najkrótsze odległości** od wierzchołka `{start or 'startowego'}` w grafie z wagami "
                                   f"(tablica `{dist}`)",
                       "algorytmem Dijkstry: z kopca bierze wierzchołek o najmniejszej znanej odległości i poprawia "
                       "(relaksuje) odległości jego sąsiadów", var=dist)
            self.used_algos.add("dijkstra")
        elif "bfs" in A:
            dist = next(iter(self.ex._relax_targets()), None)
            if dist is None:
                mm = re.search(r"(\w+)(\[\w+\])+=\1(\[\w+\])+\+1", self.flat)
                dist = mm.group(1) if mm else "dist"
            grid = "grid" in A
            self._step("bfs", f"**najkrótsze odległości** (liczbę kroków) od "
                              f"{'pola startowego na planszy' if grid else 'wierzchołka `' + (start or 'startowego') + '`'} "
                              f"(tablica `{dist}`)",
                       "przeszukiwaniem wszerz (BFS): kolejka odwiedza najpierw pola/wierzchołki w odległości 1, potem 2… "
                       "— pierwsze dotarcie to najkrótsza droga", var=dist)
            self.used_algos.add("bfs")
        if "dsu" in A:
            self._step("dsu", "**odpowiada, czy dwa elementy są w tym samym zbiorze**, łącząc zbiory po drodze",
                       "strukturą Find & Union: `find` zwraca reprezentanta zbioru (z kompresją ścieżki), łączenie "
                       "podpina jednego reprezentanta pod drugiego", var=None)
            self.used_algos.add("dsu")
        if "sieve" in A and not any(s["key"].startswith("count") for s in self.steps):
            self._step("sieve", "**które liczby są pierwsze** (sito Eratostenesa)",
                       "każda liczba pierwsza wykreśla swoje wielokrotności, zaczynając od kwadratu", var=None)
        if "sieve" in A:
            self.used_algos.add("sieve")
        if "gcd" in A or any(re.match(r"(nwd|gcd)$", f.name, re.I) for f in c.functions):
            fn = next((f.name for f in c.functions if re.match(r"(nwd|gcd)", f.name, re.I)), "gcd")
            self._step("gcd", "**największy wspólny dzielnik (NWD)** dwóch liczb",
                       f"algorytmem Euklidesa w funkcji `{fn}`: para (a, b) zamienia się w (b, a mod b), aż b = 0", var=fn)
            self.used_algos.add("gcd")
        if "fast_pow" in A:
            fn = next((f.name for f in c.functions if any(f.line <= ln <= f.end_line for ln in A["fast_pow"]["lines"])), None)
            self._step("fastpow", "**potęgę aᵇ modulo M** szybkim potęgowaniem" + (f" (funkcja `{fn}`)" if fn else ""),
                       "patrzy na bity wykładnika: podstawa podnoszona do kwadratu, a wynik mnożony, gdy bit = 1",
                       var=fn)
            self.used_algos.add("fast_pow")
        if "binary_search" in A and not any(s["key"].startswith("sort") and False for s in self.steps):
            lb = any(cl.name in ("lower_bound", "upper_bound") for cl in c.calls)
            self._step("binsearch", "dla każdego zapytania **szuka binarnie** w posortowanej tablicy "
                                    "(pozycję pierwszego elementu ≥ x, czyli ile elementów jest mniejszych)"
                       if lb or "lower" in c.code else "**szuka binarnie** odpowiedzi w posortowanym zakresie",
                       ("dla każdego zapytania wywołuje `lower_bound`, które w posortowanej tablicy znajduje pierwszy "
                        "element ≥ x w O(log n) (dzieli przedział na pół)" if lb else
                        "pętlą: sprawdza środek przedziału i odrzuca połowę, w której odpowiedzi na pewno nie ma"),
                       var=None)
            self.used_algos.add("binary_search")
        if "prefix_sums" in A and not any(s["key"] == "rangesum" for s in self.steps):
            self._step("prefix", "**sumy na przedziałach** z sum prefiksowych",
                       "najpierw liczy `pref[i] = pref[i−1] + a[i]`, a suma `[l, r]` to `pref[r] − pref[l−1]` — O(1) na "
                       "zapytanie", var=None)
            self.used_algos.add("prefix_sums")
        if "two_pointers" in A:
            mm = next((f for f in self.facts if f["kind"] == "min" and re.search(r"-\s*\w+\s*\+\s*1", f["expr"] or "")), None)
            if mm:
                self._step("twoptr", f"**długość najkrótszego spójnego fragmentu** spełniającego warunek okna "
                                     f"(wynik w `{mm['var']}`)",
                           "dwoma wskaźnikami: prawy koniec rozszerza okno, a lewy je skraca, dopóki warunek dalej "
                           "zachodzi — każdy wskaźnik przechodzi tablicę raz", var=mm["var"])
                self.used_vars = getattr(self, "used_vars", set()) | {mm["var"]}
            else:
                self._step("twoptr", "**przegląd okien** dwoma wskaźnikami",
                           "lewy i prawy koniec przesuwają się tylko do przodu — każdy przechodzi tablicę raz", var=None)
            self.used_algos.add("two_pointers")
        if "dfs" in A and not any(s["key"].startswith("count") for s in self.steps):
            self._step("dfs", "**odwiedza graf w głąb** (DFS)", "rekurencyjnie wchodzi do nieodwiedzonych sąsiadów",
                       var=None)
        if "dfs" in A:
            self.used_algos.add("dfs")
        if "dp" in A and "dp" not in self.used_algos:
            ev = (A["dp"].get("evidence") or [""])[0]
            self._step("dp", "**wynik programowaniem dynamicznym**",
                       "wypełnia tablicę stanów, licząc każdy stan z wcześniej policzonych" + (f" ({ev})" if ev else ""),
                       var=None)
            self.used_algos.add("dp")
        if "monotonic_stack" in A:
            self._step("monostack", "**najbliższy większy/mniejszy element** stosem monotonicznym",
                       "przed włożeniem elementu zdejmuje ze stosu elementy gorsze od niego", var=None)
            self.used_algos.add("monotonic_stack")
        if "topo_sort" in A:
            self._step("topo", "**kolejność topologiczną** wierzchołków", "algorytmem Kahna: kolejka wierzchołków "
                                                                          "o stopniu wejściowym 0", var=None)
            self.used_algos.add("topo_sort")
        if "mst" in A:
            self._step("mst", "**minimalne drzewo rozpinające**", "krawędzie od najlżejszej, bez tworzenia cyklu",
                       var=None)
            self.used_algos.add("mst")
        if "floyd" in A:
            self._step("floyd", "**najkrótsze odległości między wszystkimi parami wierzchołków**",
                       "algorytmem Floyda-Warshalla (pętla po wierzchołku pośrednim k na zewnątrz)", var=None)
            self.used_algos.add("floyd")
        if any(d.kind == "queue" for d in c.decls) and not ({"bfs", "topo_sort", "dijkstra"} & set(A)) and \
                not any(s["key"] in ("bfs",) for s in self.steps):
            q = next(d.name for d in c.decls if d.kind == "queue")
            self._step("queue", f"**symuluje kolejkę** `{q}` (pierwszy przyszedł — pierwszy obsłużony)",
                       "elementy dochodzą na koniec (`push`) i są obsługiwane od początku (`front`/`pop`)", var=q)

    def _r_manual_sort(self):
        c = self.c
        if "sorting" not in self.algo or any(st["key"].startswith(("sort", "bubble")) for st in self.steps):
            return
        if any(cl.name in ("sort", "stable_sort") for cl in c.calls):
            return
        sw = next((cl for cl in c.calls if cl.name == "swap" and len(c.loops_containing(cl.idx)) >= 1), None)
        arr = None
        if sw is not None and sw.args:
            m = re.match(r"(\w+)\[", normalize_expr(c.text(*sw.args[0])))
            arr = m.group(1) if m else None
        selection = bool(re.search(r"if\((\w+)\[(\w+)\]<\1\[(\w+)\]\)\3=\2", self.flat))
        kind = "przez wybieranie (w każdym kroku szuka najmniejszego elementu z reszty i stawia go na miejscu)" \
            if selection else "przez porównania i zamiany sąsiednich/wybranych elementów"
        self._step("msort", f"**sortuje tablicę{(' `' + arr + '`') if arr else ''} ręcznie** — sortowanie {kind}",
                   "dwie zagnieżdżone pętle porównują elementy i zamieniają te, które stoją w złej kolejności",
                   var=arr, improve="Proste sortowania kosztują O(n²); `sort(a, a + n)` z <algorithm> robi to w "
                                    "O(n log n).")
        self.used_algos.add("sorting")

    _GENERIC_SKIP = {"graph", "tree", "grid", "math", "string_processing", "greedy", "recursion", "simulation"}

    def _r_generic_algos(self):
        for a in self.ex.algos:
            if a["confidence"] < 0.6 or a["id"] in self.used_algos or a["id"] in self._GENERIC_SKIP:
                continue
            t = ALGO_TEXT.get(a["id"])
            if not t or any(st["key"] == f"algo:{a['id']}" for st in self.steps):
                continue
            first = re.split(r"(?<=[.!?])\s", t[1])[0].rstrip(".")
            first = re.sub(r"^Program\s+", "", first)
            self._step(f"algo:{a['id']}", f"wynik techniką **{t[0]}**", first, var=None)
            self.used_algos.add(a["id"])

    def _r_containers(self):
        """Gdy nic innego nie rozpoznano: opis pracy na kontenerach STL (zbiór, mapa, kopiec, stos, kolejka)."""
        if self.steps:
            return
        c = self.c
        ops: dict[str, set] = {}
        for cl in c.calls:
            if cl.is_method and cl.receiver:
                ops.setdefault(cl.receiver, set()).add(cl.name)
        flat = self.flat
        for d in c.decls:
            if d.kind not in ("set", "multiset", "map", "umap", "uset", "pq", "stack", "queue", "deque") or d.is_param:
                continue
            o = ops.get(d.name, set())
            nm = d.name
            if d.kind in ("map", "umap") and re.search(rf"{re.escape(nm)}\[[^\]]+\](\+\+|\+=1)", flat):
                self._step(f"cont:{nm}", f"**ile razy występuje każda wartość** (licznik w mapie `{nm}`)",
                           f"`{nm}[x]++` — nowy klucz startuje od 0, więc wystarczy zwiększać licznik", var=nm)
            elif d.kind in ("set", "multiset", "uset"):
                verbs = [v for k, v in (("insert", "dodaje"), ("erase", "usuwa"), ("count", "sprawdza obecność"),
                                         ("find", "wyszukuje"), ("lower_bound", "szuka najbliższego ≥"))
                         if k in o]
                self._step(f"cont:{nm}", f"**utrzymuje zbiór `{nm}`** ({'z powtórzeniami' if d.kind == 'multiset' else 'bez powtórzeń'}"
                                         f"{', posortowany' if d.kind != 'uset' else ''})",
                           (_join_pl(verbs) + " elementy" if verbs else "trzyma elementy") +
                           (" — każda operacja O(log n)" if d.kind != "uset" else " — średnio O(1)"), var=nm)
            elif d.kind == "pq":
                mn = "greater" in d.type
                self._step(f"cont:{nm}", f"**zawsze bierze {'najmniejszy' if mn else 'największy'} element** z kopca `{nm}`",
                           "`push` wkłada element, `top`/`pop` zdejmują szczyt — O(log n) na operację", var=nm)
            elif d.kind == "stack":
                self._step(f"cont:{nm}", f"**przetwarza dane stosem** `{nm}` (ostatni włożony — pierwszy zdjęty)",
                           "`push` kładzie element na szczyt, `top`/`pop` biorą ostatnio położony", var=nm)
            elif d.kind in ("map",):
                self._step(f"cont:{nm}", f"**przechowuje pary klucz → wartość** w mapie `{nm}` (posortowanej po kluczach)",
                           "dostęp i zmiana wartości dla klucza w O(log n)", var=nm)
            if len(self.steps) >= 2:
                break

    # ------------------------------------------------------------------ zdania
    def reads_phrase(self) -> str:
        c = self.c
        reads = [io for io in c.io if io.kind == "read"]
        if not reads:
            return ""
        parts: list[str] = []
        seen: set = set()
        for io in reads:
            ls = c.loops_containing(io.idx)
            names = [normalize_expr(t) for t in io.targets]
            if not ls:
                tg = [t for t in io.targets if _SANE_TARGET.fullmatch(normalize_expr(t))]
                vals = self.ex.describe_targets(tg, io.idx) if tg else []
                if vals:
                    parts.append(_join_pl(vals).replace("duża liczba całkowita (long long)", "liczbę całkowitą (long long)")
                                 .replace("liczba całkowita", "liczbę całkowitą")
                                 .replace("liczba rzeczywista", "liczbę rzeczywistą").replace("wartość logiczna",
                                                                                               "wartość logiczną"))
                continue
            names = [n for n in names if _SANE_TARGET.fullmatch(n)]
            if not names:
                continue
            k = ls[-1]
            key = (ls[0], tuple(sorted(names)))
            if key in seen or ls[0] in {x[0] for x in seen if len(ls) > 1}:
                continue
            seen.add(key)
            li = self.loop_info(k)
            cnt = li["count"] or ""
            if c.loops[k].kind == "range":
                rng = li.get("range", "")
                dd = c.decl_of(re.match(r"\w+", rng).group(0), io.idx) if re.match(r"\w+", rng) else None
                cnt = dd.ctor[0] if dd is not None and dd.ctor else "wszystkie"
            if li.get("countdown"):
                cnt = li["countdown"]
            cnt_txt = f"`{cnt}`" if cnt and re.fullmatch(r"[\w+\- ]+", cnt) else (cnt or "kolejno")
            arr = {re.match(r"(\w+)\[", n).group(1) for n in names if re.match(r"\w+\[", n)}
            usage = self.ex.read_usage(io, c.loops[k])
            if any("krawędzie grafu" in u for u in [usage]):
                parts.append(f"{cnt_txt} krawędzi grafu (" + ", ".join(f"`{n}`" for n in names) + ")")
            elif len(names) == 2 and all(re.search(r"\.(first|second)$", n) for n in names):
                base = names[0].split(".")[0]
                parts.append(f"{cnt_txt} par liczb do `{li.get('range', base)}`")
            elif arr and len(arr) == 1 and len(names) == 1:
                a = next(iter(arr))
                dd = c.decl_of(a, io.idx)
                if dd is not None and (dd.kind == "array" and len(dd.dims) > 1 or getattr(dd, "nested", 0) >= 2):
                    parts.append(f"tablicę `{a}` ({cnt_txt} pól)")
                else:
                    parts.append(f"{cnt_txt} liczb do tablicy `{a}`")
            elif c.loops[k].kind == "range" and len(names) == 1 and names[0] == c.loops[k].var:
                dd = c.decl_of(li.get("range", ""), io.idx)
                what = "wierszy planszy" if dd is not None and "string" in dd.elem else "liczb do"
                parts.append(f"{cnt_txt} {what} `{li.get('range', '')}`")
            elif li.get("countdown") or (cnt and re.fullmatch(r"q|t|z", cnt)):
                noun = "zapytań (każde: " if cnt.lower() in ("q", "m") else "testów (w każdym: "
                parts.append(f"{cnt_txt} {noun}" + ", ".join(f"`{n}`" for n in names) + ")")
            elif len(names) >= 2:
                parts.append(f"{cnt_txt} razy po " + _join_pl([f"`{n}`" for n in names]))
            else:
                parts.append(f"{cnt_txt} liczb (kolejno do `{names[0]}`)" if names else f"{cnt_txt} wartości")
        parts = [p for p in parts if p]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return parts[0] + ", a potem " + _join_pl(parts[1:])

    def outputs_phrase(self) -> str:
        c = self.c
        writes = [io for io in c.io if io.kind == "write" and io.api in ("cout", "printf", "puts", "putchar")]
        if not writes:
            return ""
        meaning = {}
        for s in self.steps:
            if s.get("var") and (s.get("noun") or not re.match(r"\**(dla każdego|sortuje|szuka|odpowiada|symuluje|"
                                                               r"odwiedza|przegląd)", s["what"])):
                meaning.setdefault(s["var"], re.sub(r"\*\*", "", s.get("noun") or s["what"]))
        items = []
        for io in writes:
            vals = [t for t in io.targets if t not in ("endl", "std::endl", '"\\n"', "'\\n'", '" "', "' '")
                    and not re.fullmatch(r"(fixed|setprecision\(.*\)|\"[^\"]*\"|'[^']*')", t.strip())]
            ls = c.loops_containing(io.idx)
            loop_src = None
            if ls:
                L0 = c.loops[ls[-1]]
                if L0.kind == "range" and L0.range_expr:
                    loop_src = normalize_expr(c.text(*L0.range_expr))
            for v in vals[:3]:
                vn = normalize_expr(v)
                base = re.match(r"\(?(\w+)", vn).group(1) if re.match(r"\(?(\w+)", vn) else vn
                sorted_src = next((st["var"] for st in self.steps if st["key"].startswith(("sort", "bubble"))
                                   and st.get("var") in (loop_src, base)), None)
                mpre = re.fullmatch(r"(\w+)\[(\w+)\]-\1\[(\w+)-1\]", vn)
                if sorted_src:
                    items.append(f"posortowane elementy `{sorted_src}`")
                elif mpre:
                    items.append(f"`{_pretty(v)}` — sumę na przedziale [`{mpre.group(3)}`, `{mpre.group(2)}`] "
                                 f"(różnica sum prefiksowych)")
                elif re.fullmatch(r"(lower|upper)_bound\((\w+)\.begin\(\),\2\.end\(\),(\w+)\)-\2\.begin\(\)", vn):
                    mm = re.fullmatch(r"(lower|upper)_bound\((\w+)\.begin\(\),\2\.end\(\),(\w+)\)-\2\.begin\(\)", vn)
                    rel = "mniejszych od" if mm.group(1) == "lower" else "nie większych od"
                    items.append(f"liczbę elementów `{mm.group(2)}` {rel} `{mm.group(3)}` (pozycję z `{mm.group(1)}_bound`)")
                elif base in meaning and (vn == base or re.fullmatch(rf"{re.escape(base)}(\[[^\]]+\])+|{re.escape(base)}\(.*\)", vn)):
                    items.append(f"`{_pretty(v)}` — {meaning[base]}")
                elif base in meaning and "?" in vn:
                    items.append(f"`{_pretty(v)}` — {meaning[base]} (albo wartość specjalną, gdy odpowiedzi brak)")
                elif base in meaning and re.fullmatch(rf"{re.escape(base)}[/*+-]\w+", vn):
                    items.append(f"`{_pretty(v)}` — {meaning[base]}")
                elif re.search(r"\?\s*\"(TAK|NIE|YES|NO|Tak|Nie|Yes|No|tak|nie)", v):
                    items.append(f"odpowiedź TAK/NIE (`{_pretty(v)[:50]}`)")
                else:
                    items.append(f"`{_pretty(v)[:50]}`")
            if len(items) >= 3:
                break
        seen, out = set(), []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        loop = any(c.loops_containing(io.idx) for io in writes)
        s = _join_pl(out[:3])
        return s + (" (w pętli — dla każdego elementu/zapytania)" if loop and s else "")

    def complexity_why(self) -> str:
        comp = self.comp
        if not comp:
            return ""
        nodes = []

        def walk(ns, chain):
            for nd in ns:
                if nd.get("kind") == "function":
                    walk(nd.get("children", []), [])
                    continue
                ch = chain + [nd]
                nodes.append(ch)
                walk(nd.get("children", []), ch)
        walk(comp.get("loops", []), [])
        hot = [h.get("line") for h in (comp.get("hotspots") or [])[:3] if h.get("line")]

        step_lines = {self.ex.L(st["line"]) for st in self.steps if st.get("line")}

        def key(ch):
            nd = ch[-1]
            lo, hi = nd.get("line", 0), nd.get("end_line", nd.get("line", 0))
            ins = any(lo <= hl <= hi for hl in hot)
            core = any(lo <= sl <= hi for sl in step_lines)
            return ((nd.get("total_ops") or 0), core, ins, len(ch))
        best = max(nodes, key=key, default=None)
        parts = []
        override = self.complexity_override()
        if override:
            parts.append(override[1])
        elif best:
            descr = []
            for nd in best:
                v = nd.get("var") or "?"
                cnt = nd.get("count") or "?"
                pat = nd.get("pattern")
                hdr = (nd.get("header") or "").strip()
                if pat == "dependent":
                    descr.append(f"po `{v}` (do {cnt} obrotów, zależnie od zewnętrznej pętli)")
                elif pat == "binary_search":
                    descr.append(f"wyszukiwanie binarne po `{v}` ({cnt} kroków)")
                elif nd.get("kind") in ("while", "do") or pat in ("countdown",):
                    descr.append(f"`{hdr}` ({cnt} razy)")
                else:
                    descr.append(f"po `{v}` ({cnt} obrotów)")
            if len(best) == 1:
                parts.append("jedna pętla " + descr[0])
            else:
                word = {2: "dwie", 3: "trzy", 4: "cztery"}.get(len(best), str(len(best)))
                parts.append(f"{word} zagnieżdżone pętle: " + ", w niej ".join(descr))
            tot = best[-1].get("total")
            if tot and len(best) > 1:
                parts[-1] += f" — razem rzędu {tot} kroków"
                if any(nd.get("pattern") == "dependent" for nd in best):
                    parts[-1] += " (pętle zależne robią mniej obrotów, ale rząd wielkości ten sam)"
        for call in (comp.get("calls") or [])[:3]:
            if override:
                break
            w = call.get("what") or ""
            if w.startswith("sort"):
                parts.append("sortowanie `sort` — O(n log n)")
            elif w.startswith(("lower_bound", "upper_bound", "binary_search")):
                parts.append(f"`{w.split(':')[0]}` w każdym obrocie — O(log n)")
        for nd in comp.get("loops", []):
            if override:
                break
            if nd.get("kind") == "function" and (nd.get("recursion") or {}).get("text"):
                parts.append(f"funkcja `{nd.get('name')}`: {nd['recursion']['text'].split('. ')[0]}")
        keys = {st["key"] for st in self.steps}
        if "dijkstra" in keys:
            parts.append("każde włożenie i zdjęcie z kopca kosztuje O(log m)")
        elif "bfs" in keys:
            parts.append("każdy wierzchołek trafia do kolejki raz, a każda krawędź jest oglądana raz (lub dwa razy)")
        seen, out = set(), []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return "; ".join(out[:3])

    def complexity_override(self) -> tuple[str, str] | None:
        """Algorytmy, których koszt nie wynika z prostego liczenia pętli (amortyzacja, logarytmiczne malenie)."""
        keys = {s["key"] for s in self.steps}
        comp = (self.comp or {}).get("complexity", "")
        if "dsu" in keys:
            return ("O((n + q)·α(n))", "każde `find`/łączenie kosztuje prawie O(1) (zamortyzowane O(α(n)) dzięki kompresji "
                                       "ścieżki i łączeniu według rozmiaru), więc całość to praktycznie O(n + q)")
        if "bfs" in keys and "grid" in self.algo and not re.search(r"·|\*|m", comp):
            return ("O(n·m)", "każde pole planszy trafia do kolejki najwyżej raz, a z każdego sprawdzamy 4 sąsiadów — "
                              "razem O(liczba pól) = O(n·m)")
        if "gcd" in keys and comp in ("O(1)", "O(log n)", ""):
            return ("O(log min(a, b))", "w algorytmie Euklidesa liczby maleją co najmniej o połowę co dwa kroki, więc pętla "
                                        "wykona się O(log min(a, b)) razy")
        return None

    # ------------------------------------------------------------------ całość
    def overview(self) -> tuple[str, dict]:
        self.analyze()
        c = self.c
        reads = self.reads_phrase()
        outs = self.outputs_phrase()
        steps = self.steps[:4]
        s = "Program "
        bits = []
        if reads:
            bits.append(f"wczytuje {reads}")
        if steps:
            whats = [st["what"] for st in steps]
            verb = "liczy" if not whats[0].startswith(_VERB_STEPS) else ""
            first = f"{verb} {whats[0]}".strip()
            bits.append(first)
        s += ", następnie ".join(bits[:2]) if bits else ""
        if len(steps) > 1:
            more = [st["what"] for st in steps[1:3]]
            s += "; oprócz tego " + _join_pl([("liczy " + w) if not w.startswith(_VERB_STEPS) else w for w in more])
        if s == "Program ":
            return "", {}
        s = s.rstrip() + "."
        hows = [st for st in steps if st.get("how")]
        md = s
        if hows:
            md += "\n\n**Jak to robi:** " + " ".join(_cap1(h["how"]) + "." for h in hows[:3])
        names = [a["name"] for a in self.ex.algos if a["confidence"] >= 0.5
                 and a["id"] not in ("graph", "tree", "grid", "recursion", "string_processing", "math", "greedy")][:2]
        if names:
            md += " Technika: " + _join_pl([f"**{n}**" for n in names]) + "."
        if outs:
            md += f"\n\n**Wypisuje:** {outs}."
        comp_txt = None
        if self.comp:
            why = self.complexity_why()
            ov = self.complexity_override()
            comp_txt = ov[0] if ov else self.comp["complexity"]
            md += f"\n\n**Złożoność:** **{comp_txt}**" + (f" — {why}." if why else ".")
        if self.improve:
            md += "\n\n**Można lepiej:** " + self.improve[0]
        data = {"reads": reads, "computes": [{"what": st["what"], "how": st.get("how", ""), "var": st.get("var"),
                                              "line": self.ex.L(st["line"]) if st.get("line") else None}
                                             for st in steps],
                "outputs": outs, "complexity": comp_txt,
                "complexity_why": self.complexity_why() if self.comp else "", "improvements": self.improve[:3]}
        return md, data


_VERB_STEPS = ("dla każdego", "**sortuje", "**symuluje", "**szuka", "**odwiedza", "**odpowiada", "**przegląd",
               "**utrzymuje", "**zawsze bierze", "**przetwarza", "**przechowuje")


def _cap1(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s



def explain(code: str) -> dict:
    return Explainer(code).run()


def handle_explain(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod do objaśnienia.")
    if len(code) > 300_000:
        raise ApiError("Kod jest za długi (limit 300 kB).")
    return explain(code)


def handle_stl(body: dict) -> dict:
    q = (body.get("q") or "").strip().lower()
    items = [{"key": k, "desc": v} for k, v in STL.items() if not q or q in k.lower()]
    return {"items": items, "count": len(STL)}


ROUTES = {
    "POST /api/explain": handle_explain,
    "GET /api/explain/stl": handle_stl,
}

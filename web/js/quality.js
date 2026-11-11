// Gwiazdka ★ = narzędzie sprawdzone na konkretnych przykładach ze znaną odpowiedzią (i na prawdziwych programach olimpijskich z SIO2)
// — działa pewnie. Bez gwiazdki = działa, ale to heurystyka: wynik traktuj jako podpowiedź i sprawdzaj.
// Ostatni sprawdzian: 2026-11-10 (skrypt weryfikacji + pomiary na 238 prawdziwych programach / 216 treściach zadań z SIO2).

export const STARRED = {
  // kod
  workspace: 'Uruchamianie, wejście/wyjście, testy i podpowiedzi z Twojego kodu — sprawdzone.',
  scratchpad: 'Kompiluje i uruchamia C++/Pythona — sprawdzone.',
  playground: 'Fragment bez main zamienia w program i uruchamia — sprawdzone.',
  checker: 'Znalazł wszystkie wstawione błędy; sanitizery wskazują linię wyjścia poza vector.',
  errors: 'Tłumaczy błędy g++/clang z właściwą linią — sprawdzone.',
  complexity: 'Trafna na 67/67 programach testowych (DSU, BFS po planszy, NWD, zapytania…).',
  optimizer: 'Najważniejsza poprawka na górze w 22/22 przypadkach; przepisany kod daje ten sam wynik.',
  explain: 'Rozpoznaje, co liczy program, w 197 z 238 prawdziwych programów olimpijskich.',
  beautifier: '0 zepsutych programów na 238 prawdziwych programach olimpijskich i całej bazie wiedzy, w 5 stylach.',
  py2cpp: 'Tłumaczenie kompiluje się i daje identyczny wynik (z // i % jak w Pythonie).',
  debugger: 'Krok po kroku z wartościami zmiennych i tablic — sprawdzone.',
  // testy
  tester: 'Znajduje kontrprzykład (np. overflow) i go zmniejsza — sprawdzone.',
  autotester: 'Format wejścia rozpoznany w 214/214 prawdziwych treściach zadań; łapie overflow testem maksymalnym.',
  diff: 'Wskazuje pierwszą różnicę (linia, token) — sprawdzone.',
  benchmark: 'Mierzy czasy dwóch rozwiązań dla rosnących n — sprawdzone.',
  gendsl: 'Generuje testy z opisu — sprawdzone.',
  grapheditor: 'Rysuje i eksportuje grafy/drzewa w formacie wejścia — sprawdzone.',
  inspector: 'Rozpoznaje drzewa, grafy, tablice i plansze we wejściu — sprawdzone.',
  // zadanie
  taskexplain: 'Format wejścia z treści zgodny z przykładami w 210/216 prawdziwych zadań z SIO2.',
  // szukanie
  search: '„binserch” i inne literówki trafiają w dobry artykuł — sprawdzone.',
  assistant: 'Dobry artykuł na 1. miejscu w 87–99% pytań testowych.',
  docs: '370 artykułów, 3300+ pytań i pełna dokumentacja STL i nagłówków; każdy kod z bazy kompiluje się.',
  snippets: 'Gotowe implementacje (seg, dij, bs…) — kompilują się.',
  programs: 'Wyszukiwarka Twoich programów (z Szkopułu, SIO2 albo folderu) — sprawdzone.',
  // inne
  calculators: 'Wyniki dokładne (np. C(n, k), modulo) — sprawdzone.',
  algoviz: 'Animacje krok po kroku — sprawdzone.',
  contest: 'Zegar i statusy zadań — działa.',
  flashcards: 'Powtórki z odstępami — działa.',
  journal: 'Statystyki z Twoich zgłoszeń — działa.',
  roadmap: 'Mapa tematów z postępem — działa.',
};

// Działa, ale wynik jest zgadywany — sprawdzaj go (zmierzone na 216 prawdziwych zadaniach z SIO2).
export const HINT_ONLY = {
  autosolver: 'Rozwiązuje ok. 1 na 4 prawdziwe zadania z SIO2; czasem podaje kod, który przechodzi tylko przykłady. Zawsze testuj.',
  suggest: 'Trafia algorytm w ok. 54% prawdziwych zadań (77% w pierwszej trójce); po imporcie uczy się na Twoich programach. Szacowana złożoność z limitów jest pewniejsza.',
  edgecases: '97% testów jest poprawnych, ale czasem test łamie warunek z treści (np. graf niespójny, gdy treść gwarantuje spójność).',
};

export const isStarred = (id) => Object.prototype.hasOwnProperty.call(STARRED, id);
export const qualityNote = (id) => STARRED[id] || HINT_ONLY[id] || '';
export const STAR = '★';

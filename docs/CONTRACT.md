# OI Forge — kontrakt architektury (OBOWIĄZKOWA lektura dla każdego agenta)

OI Forge to lokalne studio do zadań olimpijskich (OI) dla ucznia piszącego w C++ (czasem w Pythonie).
**Zero LLM / zero AI w chmurze.** Wolno: reguły, BM25/TF-IDF, haszowanie, n-gramy, Levenshtein, małe sieci
(softmax/MLP na haszowanych cechach trenowane lokalnie), analiza tokenów/AST, uruchamianie kompilatorów.
Ma działać offline. Cały interfejs i wszystkie teksty dla użytkownika **po polsku** (poprawna polszczyzna, z ogonkami).

Katalog główny projektu: dalej ROOT (folder z `oiforge/`, `web/`, `data/`).

## 1. Zasady pracy (ważne)
- **Python: tylko biblioteka standardowa** (Python 3.14). Bez numpy/flask/requests.
- **Edytujesz TYLKO pliki, które należą do Ciebie** (lista w zadaniu). Pliki rdzenia (`oiforge/core/*` poza Twoimi,
  `oiforge/server.py`, `web/js/{app,ui,api,editor,icons,markdown}.js`, `web/css/app.css`) są tylko do odczytu.
  Jeśli czegoś w rdzeniu brakuje — obejdź to lokalnie w swoim module i opisz potrzebę w raporcie końcowym.
- Testy: `tests/test_<twój_moduł>.py` w `unittest`, uruchamiane `python3 -m unittest tests.test_<moduł> -v` z ROOT.
- JS sprawdzaj: `node --check web/js/views/<id>.js` (w `web/js/package.json` jest `"type": "module"`).
- Serwer do testów HTTP uruchamiaj na WŁASNYM porcie: `python3 -m oiforge --port 88NN --no-browser` (NN w zadaniu).
  Zawsze go zabij po teście. NIE używaj narzędzi przeglądarki (Playwright/Chrome) — UI zweryfikuje integrator.
- Pliki tymczasowe: `user_data/tmp/` albo katalog tmp systemu (moduł `tempfile`).
- Kompilatory: `g++-16` (Homebrew GCC, ma `bits/stdc++.h`, domyślny), `clang++` (Apple, do sanitizerów i lldb;
  dla clanga dodajemy `-I ROOT/include`, gdzie jest shim `bits/stdc++.h`). Zawsze używaj `oiforge.core.runner`.
- Jakość ponad ilość, ale projekt ma być DUŻY i działać „mądrze”. Każdy moduł ma działać na prawdziwych przykładach.
- Na końcu raport: co zrobione, pliki, jak przetestowane, znane ograniczenia, czego potrzebujesz od rdzenia.

## 1a. Wieloplatformowość (macOS, Linux, Windows) — WYMAGANE
Aplikacja ma działać na macOS, Linuxie i Windowsie. Rozwijamy na macOS, ale kod musi być przenośny:
- Ścieżki tylko przez `pathlib`/`os.path`; pliki zawsze z `encoding="utf-8"`; normalizuj `\r\n` → `\n` przy porównaniach.
- Procesy: `subprocess` z listą argumentów (nigdy `shell=True`); Python uruchamiaj przez `sys.executable`;
  binarki mają sufiks `.exe` na Windows (`runner` to obsługuje — nie składaj ścieżek binarek sam).
- Moduły tylko-POSIX (`resource`, `os.wait4`, `signal.SIGKILL`, `fcntl`) importuj/używaj warunkowo (`os.name == "posix"`).
- Narzędzie zewnętrzne niedostępne na danej platformie (sanitizery na MinGW, lldb na Windows, gdb na macOS) =
  łagodna degradacja: czytelny komunikat po polsku + działający fallback, nigdy wyjątek.
  `oiforge.config` udostępnia: `IS_WINDOWS, IS_MAC, IS_LINUX, CXX (g++), CLANG, GDB, LLDB, PYTHON` oraz `capabilities()`.
- Nie zakładaj bash/zsh, `/tmp`, `open`, `xdg-open` — do temp używaj `tempfile`, do otwierania przeglądarki `webbrowser`.

## 1b. Dane użytkownika (po imporcie — prywatne, NIGDY w repozytorium)
- `user_data/programs/index.json` — zaakceptowane (100 pkt) rozwiązania użytkownika, zaimportowane z Szkopułu, dowolnej
  strony SIO2 albo z folderu. Rekord: `{source, contest_id, contest_name, problem_name, problem_short, submission_id,
  time, score, status, lang, path, url, lines, round}`; `path` względem `user_data/programs/`; `source` = host serwera
  (np. `szkopul.edu.pl`) albo nazwa importu; rozwiązania z archiwum zadań (poza konkursami) mają `contest_id = "archiwum"`.
  `round` = nazwa rundy — na kółkach to zwykle TEMAT („Wyszukiwanie binarne”, „Dijkstra”…) → darmowe etykiety algorytmów.
- `user_data/programs/<source>/<contest>/statements/<short>.txt` (tekst z PDF) lub `.html` — treści zadań pobrane przy
  imporcie; `problems.json` w tym katalogu = listy zadań z rundami.
- Na tych danych OI Forge **dostraja się do użytkownika** (lokalnie, bez AI): wyszukiwarka programów, podpowiedzi
  w edytorze, doradca algorytmów, dziennik. Modele i indeksy są w cache i odbudowują się same po imporcie.
- To dane prywatne: NIE kopiuj ich do `data/` ani `tests/`; testy, które z nich korzystają, mają się pomijać (skipUnless),
  gdy plików brak. Testy z konkretnymi danymi autora są w `tests/*_private.py` — eksport publiczny je pomija.
- `web/vendor/pdfjs/pdf.min.mjs` + `pdf.worker.min.mjs` (pdf.js 4.10, legacy) — ekstrakcja tekstu z PDF w przeglądarce.

## 2. Układ katalogów
```
oiforge/__main__.py        start serwera (python3 -m oiforge [--port N] [--no-browser])
oiforge/server.py          HTTP, routing, statyki, auto-discovery narzędzi, /api/views
oiforge/config.py          ścieżki i wykryte kompilatory
oiforge/core/              biblioteki współdzielone (runner, jobs, store, text, cpp_lexer, search, kb, ...)
oiforge/tools/<nazwa>.py   moduły funkcji — każdy eksportuje ROUTES
data/kb/*.json             baza wiedzy („nasz internet”)
data/snippets/*.json       biblioteka snippetów
data/*.json                inne dane (synonyms.json, error_patterns.json, tasks/*.json ...)
include/bits/stdc++.h      shim dla clanga
web/index.html, web/css/app.css, web/js/*.js       powłoka UI (rdzeń)
web/js/views/<id>.js       widoki (auto-ładowane), web/css/views/<id>.css (opcjonalny CSS widoku, auto-ładowany)
user_data/                 dane runtime (store, cache, tmp, programs) — nie commitować
tests/                     testy unittest
```

## 3. Backend

### 3.1 Moduł narzędzia (`oiforge/tools/<nazwa>.py`)
```python
from oiforge.server_api import ApiError          # wyjątek -> {"ok": false, "error": msg} ze statusem
from oiforge.core import runner, jobs, store

def handle_analyze(body: dict) -> dict:          # body = JSON z żądania (POST) albo query params (GET)
    code = body.get("code", "")
    if not code.strip():
        raise ApiError("Wklej kod do analizy.")
    return {"ok": True, "issues": [...]}

ROUTES = {
    "POST /api/checker/analyze": handle_analyze,   # metoda + ścieżka; sama ścieżka = POST
    "GET /api/checker/rules": handle_rules,
}
```
- Serwer przy starcie importuje każdy `oiforge/tools/*.py` (bez `_` na początku) i rejestruje `ROUTES`.
  Błąd importu jednego modułu NIE wywala serwera (jest logowany i widoczny w `GET /api/health`).
- Odpowiedź zawsze JSON. Jeśli handler zwróci dict bez `ok`, serwer doda `"ok": true`.
- Nieobsłużony wyjątek → status 500, `{"ok": false, "error": "...", "trace": "..."}`.

### 3.2 Zadania w tle (`oiforge.core.jobs`)
Dla operacji > ~1 s (testerka, pomiar złożoności, debugger, scraper):
```python
def handle_start(body):
    return {"ok": True, "job": jobs.submit(_work, body, title="Testerka")}

def _work(job, body):
    for i in range(n):
        if job.cancelled: break
        job.progress(i / n, f"Test {i+1}/{n}")      # 0..1 + komunikat
        job.push({"type": "test", "index": i, "verdict": "OK"})   # zdarzenia przyrostowe
    return {"summary": ...}                          # wynik końcowy (JSON-owalny)
```
- `GET /api/jobs/<id>?since=K` → `{ok, id, title, status: "running"|"done"|"error"|"cancelled", progress, message,
  events: [...od K], next: K2, result?, error?}`; `POST /api/jobs/<id>/cancel`.

### 3.3 Kompilacja i uruchamianie (`oiforge.core.runner`)
```python
runner.detect_lang(source) -> "cpp" | "python"
runner.compile_cpp(source, mode="release", std="c++20", extra_flags=None) -> CompileResult
    # mode: "release" (g++-16 -O2), "sanitize" (clang++ ASan+UBSan -g -O1, fallback g++),
    #       "debug" (clang++ -g -O0 pod lldb, fallback g++-16 -g -O0), "fast" (g++-16 -O2 -march=native)
    # CompileResult: ok, exe (ścieżka), stderr, cached, compiler, cmd(list), time; .to_dict()
runner.prepare(source, lang=None, mode="release") -> Program
    # Program: ok, lang, cmd (lista do exec), compile (CompileResult|None), error (str); .to_dict()
runner.run(cmd, input_text="", timeout=2.0, args=None, cwd=None, env=None, mem_limit_mb=None) -> RunResult
    # RunResult: stdout, stderr, exit_code, time (s), mem_kb, timed_out, signal(str|None),
    #            verdict: "OK"|"TLE"|"RE"|"MLE"; .to_dict()
runner.run_source(source, input_text="", lang=None, mode="release", timeout=2.0) -> dict
    # skrót: {ok, compile: {...}, run: {...}} ; ok=False przy błędzie kompilacji
```
Binarki są cache’owane po SHA1(źródło+flagi) w `config.CACHE_DIR/bin/` (systemowy katalog cache: macOS `~/Library/Caches/OIForge`,
Linux `~/.cache/oiforge`, Windows `%LOCALAPPDATA%\OIForge\Cache`; nadpisanie: `OIFORGE_CACHE_DIR`) — wielokrotna kompilacja
tego samego kodu jest darmowa. Źródło leży w `.../src/<klucz>/main.cpp`, więc komunikaty i `__FILE__` pokazują `main.cpp`.
Zawsze używaj `config.CACHE_DIR`, nigdy `user_data/cache` na sztywno. `bits/stdc++.h` = nasz shim z `include/` (także dla GCC 16,
który nie dołącza `<cassert>`).
Wyjście jest obcinane do ~4 MB. Stos: binarki release/fast mają 512 MB stosu (`-Wl,-stack_size`).

**Tryb terminala** (`oiforge/tools/terminal.py`): program działa na żywo i czeka na dane wpisywane w konsoli edytora.
`POST /api/term/start {code, lang, replace?}` → `{started, id, compile}`; `POST /api/term/write {id, data, eof?}`;
`GET /api/term/read?id&since&rev` → `{start, chunks:[{k:"out"|"err"|"in", t}], next, rev, done, exit_code, verdict, time}`
(long-poll ≤ 0,8 s; fragment `start` klient podmienia, bo ostatni mógł urosnąć); `POST /api/term/kill {id}`.
macOS/Linux: pseudoterminal bez echa (bufory jak w prawdziwej konsoli), Windows: potoki.

**Strażnik pamięci** (`POST /api/checker/guard {code, input}` → `{checked, issues:[{rule,line,title,message,fix,code}]}`):
po zwykłym „Uruchom” edytor puszcza ten sam program z sanitizerami (+ tryb debug STL) na tym samym wejściu i pokazuje
tylko PEWNE błędy pamięci/UB z linią (wyjście poza vector/tablicę itp.), których wersja -O2 nie zgłasza.

**„Otwórz w edytorze”** (obok każdego „Wyślij do testerki”): `ctx.navigate('workspace', params)`, gdzie
`{statement, code?, target?: 'brute'|'gen', tests?: [{input, output?}]}` tworzy nowe zadanie (tresc.md + testy z
przykładów), kod zapisuje do `rozw.cpp` / `brute.cpp` / `gen.cpp|gen.py`, a `tests` dopisuje do `testy/`;
`{code, name}` bez treści → plik w `Szkice/`; `{program_id}` → import z „Moich programów”; `{path, line}` → otwarcie pliku.
Panel narzędzi przy pustej `tresc.md` (sam szablon) pokazuje pole do wklejenia treści: zapis do `tresc.md` i od razu
ponowne uruchomienie wybranego narzędzia.

### 3.4 Pozostałe biblioteki rdzenia
- `oiforge.core.store`: `get(key, default=None)`, `set(key, value)`, `settings() -> dict`, `update_settings(patch) -> dict`.
  Klucz = `[a-z0-9_.-]+`, plik `user_data/store/<key>.json`. HTTP: `GET /api/store/get?key=`, `POST /api/store/set {key,value}`,
  `GET /api/settings`, `POST /api/settings {patch}`.
- `oiforge.core.text`: `normalize(s)` (małe litery, bez ogonków, zachowuje `c++`, `#`), `tokenize(s)` (słowa + rozbijanie
  camelCase/snake_case), `stem(tok)` (lekki stemmer PL/EN), `terms(s)` (stemy bez stopwords), `char_ngrams(s, n=3)`,
  `levenshtein(a,b)`, `similarity(a,b)` (0..1), `STOPWORDS`.
- `oiforge.core.cpp_lexer`: `tokenize(code) -> [Token(kind, text, line, col, pos)]`, kind ∈ `kw, id, num, str, char, op,
  punct, pp, comment`; `strip_comments(code)` (zachowuje pozycje/linie), `match_brackets(tokens) -> {i: j}`,
  `find_functions(code) -> [{name, ret, params, line, end_line, body_start, body_end}]`, `significant(tokens)` (bez komentarzy).
- `oiforge.core.kb`: `articles() -> list[dict]`, `get(id)`, `snippets() -> list[dict]` (domyślne + użytkownika), `reload()`.
- `oiforge.core.search`: `SearchIndex(fields={"title":3.0,"body":1.0,...})`, `.add(doc_id, {field: text}, payload=None)`,
  `.build()`, `.search(query, k=10, filters=None) -> [{id, score, payload, matched: [terms], highlights}]`;
  `expand_query(q) -> list[(term, weight)]` (synonimy z `data/synonyms.json` + fuzzy). Agent A rozwija ten moduł,
  ale publiczne API musi zostać zgodne.

### 3.5 Interfejsy współdzielone między agentami (stałe sygnatury)
Jeśli korzystasz z modułu innego agenta, importuj w `try/except ImportError` i miej awaryjny fallback.
- `oiforge.core.algodetect.detect(code: str) -> list[{"id","name","confidence","lines","evidence"}]` — agent B2.
  `id` z listy: binary_search, two_pointers, prefix_sums, sorting, greedy, dp, bitmask_dp, bfs, dfs, dijkstra,
  bellman_ford, floyd, topo_sort, dsu, mst, lca, segment_tree, lazy_segment_tree, fenwick, sparse_table, kmp, z_function,
  hashing, trie, manacher, sieve, fast_pow, mod_inverse, gcd, matrix_pow, convex_hull, geometry, monotonic_stack,
  sliding_window, recursion, backtracking, bitset, scc, bridges, bipartite, flow, matching, mo, sqrt_decomp,
  coordinate_compression, meet_in_middle, simulation, math, combinatorics, string_processing, graph, tree, grid.
- `oiforge.core.statement.parse(text: str) -> {"title","sections":{"tresc","wejscie","wyjscie","przyklad","ocenianie"...},
  "input_format": [...], "constraints": {"n": {"min":1,"max":200000}, ...}, "samples": [{"input","output"}],
  "subtasks": [{"points", "conditions"}], "time_limit", "memory_limit"}` — agent E2.
  Przykłady z opisów testów „ocen” („1ocen: n = 10^6; odpowiedź to 1”) mają `source: "ocen"`; przykład dłuższy niż
  100 tys. znaków ma `big: true` i `input_preview` (pokazuj podgląd, a uruchamiaj na pełnym `input`). Zmienna
  „n ≤ 10^K” z K > 18 ma w `constraints` `bignum: true, max_digits: K+1` (`max` = None) i typ `"bignum"` w formacie —
  w programie to napis cyfr; NIGDY nie zamieniaj takiej wartości na int (`ST.value_text`/`ST.fmt_big` pomagają).
  Tekst sekcji (nigdy przykłady) jest czyszczony z „≤” zapisanego przez PDF jako „6” („1 6 n, q 6 5 000”) lub znak 0x14
  (0x15 = ≥, 0x01 = ·) — wtedy w `warnings` jest komunikat o tej poprawce.
- Objaśniacz treści — agent E2. HTTP: `POST /api/taskexplain {statement}`; Python: `oiforge.tools.taskexplain.explain(text)`.
  Pola dotychczasowe bez zmian (`title, summary, given, goal, input{text,format,verified}, output, variables, samples,
  pitfalls, subtasks, complexity{var,max,max_fmt,complexity,hint,note,sizes}, glossary, keywords, warnings, sections`), nowe:
  `brief {text, given, goal, goal_verb, goal_source, answer}` („Dane: … Policz: … — jedna liczba.”),
  `task_type {id: optimize|count|decision|construct|simulate|queries|interactive|compute, label, tags, labels,
  consequence, words, modulo}`, `structure {kind: tree|graph|grid|strings|array|pairs|values|queries|unknown, label,
  flags{queries,multitest,tree,graph,directed,weighted,rooted,grid}, notes, items[{line,kind,role,text,vars,repeat,length}],
  queries[{tag,params,meaning,param_meaning,prints,count}], query_count, verified}`,
  `walkthrough [{name, input: [str], output, explanation}]` (tylko gdy przykład przechodzi format — nigdy zgadywane),
  `complexity.{budget, budget_text, hints[{text, why}], basis: "rozmiar"|"wartość"}`, a w `variables[]` dodatkowo
  `kind: size|value|array|param|string`, `symbolic_max`, `auto_name`.
- `oiforge.core.statement.cpp_reader(fmt, indent="    ", samples=None) -> {code, vars, arrays, rows, notes}` — kod C++
  wczytujący dane. NIGDY nie zawiera „TODO”: przy zapytaniach słowa-operacje i ich argumenty są brane z `samples`
  (bez przykładów: cały wiersz do `stringstream`), przy nierozpoznanym formacie — kształt wierszy z przykładów albo
  wczytanie wszystkich wartości do `vector<string>` (z uwagą w `notes`).
- Synteza programów (zero AI) — `oiforge.core.synth.synthesize(parsed, statement_text="", *, words=None, job=None,
  budget=6.0, want="brute", f0=0, f1=1, timeout=2.0) -> {status, code, desc, family, cx, ops, fast, reasons,
  competitors[{desc, family, score, code, input, output, best_output}], message, passed, total, slow, cross,
  candidates, matches, random_tests, time}`. Plan wczytywania (`build_plan`) czyta przykłady w Pythonie dokładnie tak
  jak wygenerowany C++; kandydaci (wzory, agregaty, pary/trójki, spójne fragmenty, podzbiory, zapytania naiwnie,
  przegląd x ∈ [1..N], ciągi rekurencyjne, napisy, grafy/drzewa, plansze, macierze, wiersze „x y”) są interpretowani
  na WSZYSTKICH przykładach (także „ocen”); zwycięzca jest kompilowany, sprawdzany na przykładach i porównywany z
  interpreterem na losowych testach. `status`: `verified` (jednoznaczny, zgodny ze słowami treści, nie-trywialny
  dowód), `weak` (`reasons` ⊂ {ambiguous, trivial, unaligned, unchecked}; przy `ambiguous` w `competitors` są programy
  zgodne z przykładami i wejście, na którym się różnią), `none`, `no_plan`, `no_samples`. `fast` = szacunek złożoności
  (`cx`) mieści się w limitach z treści. `reader_program(parsed) -> str | None` — kompletny program, który TYLKO
  wczytuje dane (do pokazania jako „Gotowe wczytywanie danych”); wiersze zapytań czyta w pętli z rozgałęzieniem na typ
  operacji, a nazwy argumentów i opis bierze z `taskexplain.structure(parsed)["queries"]` (np. `Q x`, `S a b`).
  Gdy format z treści nie zgadza się z przykładami, kolejno: `inputgen.cpp_reader(inputgen.infer(przykłady, treść))`
  (format wywnioskowany z przykładów), `statement.cpp_reader(fmt)`, kształt przykładów — pierwszy, który się kompiluje.
  `oiforge.core.inputgen.cpp_reader(spec, indent="    ", ops_info=None) -> str | None` — wnętrze `main` wczytujące
  dane wg specyfikacji inputgen (None, gdy czegoś nie da się zapisać, np. wyrażenie `2**n`). `challenge(parsed, text, code, *, words=None) ->
  {checked, tests, agree, competitors}` — ochrona przed przeuczeniem dla kodu spoza syntezy (szablony, podobne zadania):
  wiarygodny program syntezy zgodny z przykładami, który daje inne wyniki niż `code` na losowych testach, trafia do
  `competitors` (auto-solver zmienia wtedy `solved` na `weak`).
- Auto-solver (`POST /api/autosolver/solve`, job) — `status`: `solved` | `brute` (zweryfikowany brute z syntezy, za
  wolny na limity — „szybkiego rozwiązania nie znalazłem”) | `weak` | `unsolved` | `no_samples`; `best.kind` ∈
  {template, user, synth}; przy braku rozwiązania `reader` (gotowe wczytywanie) + `hints` (tekst), opcjonalnie
  `competitors` i `synth` (podsumowanie syntezy). Pole `sketch` (szkic z TODO) zostało usunięte. Własny program ucznia
  dla tego samego zadania (`best.same_task`, podobieństwo treści ≥ 0,9) to wzorzec: `solved`, pewność 0,97, bez
  `challenge` i bez ostrzeżeń o krótkim przykładzie.
- `oiforge.tools.errors.translate(stderr: str, source: str | None = None) -> list[{"line","col","severity",
  "title","explanation","fix","original"}]` — agent B1. HTTP: `POST /api/errors/translate {stderr, source}`.
- Formatter — agent C. HTTP: `POST /api/format {code, style?} -> {ok, code, changed}`; Python:
  `oiforge.tools.formatter.format_code(code, style=None) -> str`.

## 4. Frontend

### 4.1 Widok (`web/js/views/<id>.js`)
```js
import { h, icon, button, card, tabs, field, input, select, toast, markdown, outputPanel } from '../ui.js';
import { api } from '../api.js';
import { createEditor } from '../editor.js';

export default {
  id: 'checker',                 // = nazwa pliku
  title: 'Sprawdzarka kodu',
  icon: 'bug',                   // nazwa ikony Lucide z web/js/icons.js (patrz ICON_NAMES)
  group: 'Kod',                  // 'Asystent' | 'Kod' | 'Testowanie' | 'Zadania' | 'Moje programy' | 'Narzędzia'
  order: 10,                     // kolejność w grupie
  description: 'Statyczna analiza + sanitizery: znajduje overflow, wyjście poza tablicę, UB…',
  keywords: ['debug', 'błędy', 'sanitizer'],   // do palety komend
  render(root, ctx) {            // root = pusty <div class="view">; ctx = {navigate(id, params), params, settings}
    root.append(viewHeader(this), ...);
  },
  onShow(params) {},             // opcjonalnie: przy każdym ponownym wejściu (params z navigate)
};
```
- Powłoka ładuje listę widoków z `GET /api/views` (skan katalogu), robi dynamiczny `import()` każdego,
  buduje sidebar wg `group/order`. Widok renderuje się raz (leniwie przy pierwszym wejściu) i jest cache’owany.
- Nawigacja: `ctx.navigate('assistant', {q: 'set cpp'})` — hash `#/assistant?q=set%20cpp`.
- **Narzędzia asystenta** (`oiforge/tools/_assistant_tools.py`, bez AI): piaskownica `run_code(code, stdin, lang)`
  (fragment C++ owijany w `#include` + `main`, definicje funkcji przed main; macOS `sandbox-exec`: bez sieci, zapis tylko
  w katalogu tymczasowym; Linux `bwrap`, jeśli jest; zawsze limit czasu 2 s, 512 MB i 64 KB wyjścia), komendy `/…`
  (`parse_command`, `available_commands`), naturalne prośby przy kodzie („co wypisze…”, „sprawdź ten kod”),
  `clarify_mode` dla „nie rozumiem / pokaż przykład / krok po kroku”, ręczne analogie `ANALOGIES`, profil ucznia
  (`store` klucz `assistant_profile`; czytany i zapisywany TYLKO dla pytań z okna czatu, nie w testach). Tryb
  `OIFORGE_ASSISTANT_TOOLS=all|basic` (OI Forge Mini: `basic` — tylko uruchamianie, programy, dokumentacja).
  HTTP: `POST /api/assistant/run {code, input?, lang?}` → `{run}`, `GET /api/assistant/commands`. Odpowiedź `ask`
  może mieć bloki `run`, `tip`, pola `clear`, `clarify`, `examples`, `used_uid`; historia od klienta:
  `[{q, topic, uid, clarify, examples}]`. Akcje są filtrowane w UI przez `ctx.hasView(view)`.
- Przekazywanie kodu między narzędziami: `ctx.navigate('checker', {code})` (params przechowywane w pamięci, nie w hashu).

### 4.2 UI kit (`web/js/ui.js`) — używaj ZAMIAST ręcznego DOM/CSS, żeby całość była spójna
```js
h(tag, attrs?, ...children)      // attrs: class, style(obj|str), on<event>, dataset, html, value, ... ; children: Node|string|array|null
icon(name, size=16)              // <svg>
viewHeader({title, icon, description}, ...actions)   // nagłówek widoku (duży tytuł + opis + przyciski po prawej)
button(label, {icon, variant:'primary'|'secondary'|'ghost'|'danger'|'success', size:'sm'|'md'|'lg', onClick, title, disabled})
iconButton(iconName, {title, onClick, variant})
card({title, icon, subtitle, actions:[], body: Node|[], class, padded=true})
tabs(items:[{id,label,icon,badge,render:()=>Node}], {active, onChange}) -> {el, select(id)}
split(leftNode, rightNode, {ratio=0.5, min=240, direction:'h'|'v', storageKey}) -> el   // przeciągany podział
field(label, control, hint?)     // etykieta + kontrolka
input({value, placeholder, type, onInput, onEnter, mono, size})  -> <input>
textarea({value, placeholder, rows, mono, onInput}) -> <textarea>
select(options:[{value,label}]|string[], {value, onChange}) -> <select>
checkbox(label, {checked, onChange}) -> <label>   // .input = <input>
segmented(options, {value, onChange}) -> el       // przełącznik pigułkowy
badge(text, tone:'neutral'|'accent'|'success'|'warn'|'danger'|'info')
verdictBadge(verdict)            // OK/WA/TLE/RE/MLE/CE ładnie pokolorowane
toast(message, tone='info', {timeout})
modal({title, body, actions:[button...], width}) -> {close()}
spinner(size) ; progressBar() -> {el, set(fraction, label)}
emptyState({icon, title, text, action})
table(columns:[{key,label,align,render?:(row)=>Node}], rows, {onRowClick, compact})
kbd('Ctrl+K')
markdown(mdString) -> Node       // obsługuje ```cpp kod```, `inline`, **bold**, *ital*, listy, tabele |a|b|, > cytaty, nagłówki
codeBlock(code, lang='cpp', {copy=true, lineNumbers=false, highlightLines=[]}) -> Node   // statyczne podświetlenie
copyButton(getText) ; fileDrop({accept, onFiles, label}) -> el
outputPanel() -> {el, showRun(runResultDict), showCompile(compileDict), showText(text), clear(), setBusy(bool)}
    // ujednolicony panel wyników: werdykt, czas, pamięć, stdout, stderr, błędy kompilacji (z tłumaczeniem przez /api/errors/translate)
stat({label, value, tone}) ; kv([[k, v], ...]) ; section(title, ...children)
```
Klasy CSS dostępne globalnie (z `app.css`): `.stack` (pion z odstępami), `.row` (poziom), `.grow`, `.grid-2`, `.grid-3`,
`.muted`, `.mono`, `.small`, `.scroll`, `.pad`, `.gap-s|m|l`, `.panel`, `.toolbar`, `.chip`, `.hl` (podświetlenie),
`.diff-add/.diff-del/.diff-eq`, `.tone-success|warn|danger|info|accent` (kolor tekstu).
Kolory przez zmienne CSS: `--bg, --bg-2, --panel, --panel-2, --border, --text, --text-2, --muted, --accent, --accent-2,
--success, --warn, --danger, --info, --radius, --mono` (działają w motywie ciemnym i jasnym — nie wpisuj kolorów na sztywno).

### 4.3 API (`web/js/api.js`)
```js
await api.post('/api/checker/analyze', {code})      // zwraca JSON; przy ok:false rzuca Error(error) (i pokazuje toast, chyba że {quiet:true})
await api.get('/api/kb/article', {id})
const ctl = api.job('/api/tester/start', body, {onEvent(ev){}, onProgress(p, msg){}});   // ctl.promise -> result, ctl.cancel()
await store.get(key, def) ; await store.set(key, value) ; settings() / updateSettings(patch)
```

### 4.4 Edytor (`web/js/editor.js`)
```js
const ed = createEditor(parentEl, {value, lang:'cpp'|'python'|'text', readOnly, height:'420px'|'auto'|'100%',
                                    placeholder, storageKey, onChange, onRun, lineNumbers=true, minimal=false});
ed.getValue(); ed.setValue(s); ed.setLang(l); ed.focus(); ed.format(); ed.cm (CodeMirror)
ed.markLines([{line, type:'error'|'warn'|'info'|'hint', message}]); ed.clearMarks(); ed.highlightLine(n, cls); ed.scrollToLine(n)
ed.addGutter(name); ed.setGutterMarker(line, name, el); ed.clearGutter(name); ed.onGutterClick((line, gutter, ev) => …)
```
Edytor ma wbudowane: snippety na Tab (`seg`+Tab → drzewo przedziałowe), Ctrl+Enter = onRun, Ctrl+Shift+F = formatowanie, Ctrl/⌘+F = pasek „Znajdź i zamień” (`web/js/findbar.js`, `ed.find(replace)`),
Shift+Tab = cofnięcie wcięcia zaznaczonych linii,
Ctrl+/ komentarz, dopasowanie nawiasów, autozamykanie, wyszukiwanie, zwijanie bloków.
Podpowiedzi biorą TYLKO bieżący plik (zmienne widoczne w danym miejscu z typem, funkcje zdefiniowane/użyte, typy, makra)
+ słowa C++ — nie nazwy z innych programów ucznia. Kolejność heurystyczna: zasięg i bliskość, po `cin >>` niewczytane zmienne
(i `a[i]` w pętli), po `a.` metody wg typu (vector/set/map/string/…, pola struktur), po `sort(` → `a.begin(), a.end()`,
w `vector<` same typy. Wstawki: `cout`/`cin` + Tab → `cout << ;`, `vector` → `vector<> ;`, `for` → pętla z granicą `n`,
`if`/`while` → `( ) { }`; Tab i `>`/`;` przeskakują do kolejnego pola.
Trzy osobne zestawy: C++ (powyżej), Python (słowa kluczowe i funkcje wbudowane, metody wg typu z przypisania — list/str/
dict/set/deque, zawartość modułów `math.`/`sys.`/`heapq.`…, `import ` → moduły; wstawki `for` → `for i in range(n):`,
`def`/`if`/`while` z dwukropkiem i wcięciem, `print` → `print()`), tekst (.txt/.md/testy: tylko słowa z tego pliku, od 3. litery).

### 4.5 Inne helpery frontendu (rdzeń)
- `web/js/pdf.js`: `await extractPdfText(fileOrArrayBuffer) -> string` (pdf.js z vendor, łączy linie jak w
  `user_data/.../statements/*.txt`). Używaj w widokach przyjmujących treść zadania (upload PDF).
- `web/js/charts.js`: `lineChart(series:[{name, points:[[x,y]...]}], {xLabel, yLabel, logX, logY, height}) -> SVG Node`,
  `barChart(items:[{label, value, tone}], {height, horizontal}) -> Node` — proste wykresy SVG w stylu aplikacji.
- `ctx.navigate(id, params)` przekazuje kod między narzędziami, np. `ctx.navigate('checker', {code})`,
  `ctx.navigate('tester', {solution, brute, generator})`, `ctx.navigate('playground', {code})`,
  `ctx.navigate('assistant', {q})`, `ctx.navigate('docs', {id})` — każdy widok w `onShow(params)` obsługuje swoje klucze.

### 4.6 Identyfikatory widoków (kto jest właścicielem)
rdzeń: `home`, `snippets`, `scratchpad`, `settings` · A: `assistant`, `docs` · B1: `checker`, `errors` ·
B2: `complexity`, `explain`, `optimizer` · C: `beautifier`, `playground` · D: `py2cpp` · E1: `tester`, `diff`, `autotester` ·
E2: `taskexplain`, `suggest`, `edgecases`, `autosolver` · F: `programs` · G: `debugger`, `inspector` ·
H: `contest`, `journal`, `flashcards`, `roadmap` · I: `gendsl`, `grapheditor`, `benchmark` · J: `algoviz`, `calculators`.
Grupy w sidebarze: Asystent (assistant, docs, snippets) · Kod (playground, scratchpad, checker, errors, debugger, explain,
optimizer, beautifier, py2cpp, complexity) · Testowanie (tester, autotester, diff, gendsl, grapheditor, benchmark, inspector) ·
Zadania (taskexplain, suggest, edgecases, autosolver) · Moje programy (programs) · Trening (contest, journal,
flashcards, roadmap) · Narzędzia (algoviz, calculators, settings).

### 4.7 Testowanie HTTP curl-em
Każdy POST wymaga nagłówka `X-OIForge: 1` (ochrona przed CSRF), np.:
`curl -s -H 'X-OIForge: 1' -H 'Content-Type: application/json' -X POST http://127.0.0.1:88NN/api/x/y -d '{"code": "..."}'`.

## 5. Baza wiedzy — schemat (`data/kb/<plik>.json`)
```json
{ "chunk": "stl-containers", "articles": [ {
  "id": "stl-set",
  "title": "std::set — zbiór uporządkowany",
  "category": "STL kontenery",
  "tags": ["set", "zbiór", "drzewo czerwono-czarne", "unikalne"],
  "aliases": ["set", "std::set", "set cpp", "zbiór c++", "jak używać set", "sety"],
  "summary": "Jedno–dwa zdania po polsku: czym to jest i kiedy używać.",
  "complexity": "insert/erase/find/lower_bound: O(log n); iteracja: O(n)",
  "level": 1,
  "body": [
    {"type": "text", "md": "Akapit w markdown..."},
    {"type": "code", "lang": "cpp", "title": "Podstawy", "code": "#include <bits/stdc++.h>\nusing namespace std;\nint main(){...}"},
    {"type": "table", "headers": ["Operacja", "Złożoność", "Opis"], "rows": [["s.insert(x)", "O(log n)", "..."]]},
    {"type": "tip", "md": "Wskazówka"}, {"type": "warn", "md": "Ostrzeżenie"}
  ],
  "functions": [ {"sig": "s.lower_bound(x)", "desc": "iterator na pierwszy element ≥ x", "complexity": "O(log n)",
                  "returns": "iterator", "example": "auto it = s.lower_bound(5);"} ],
  "pitfalls": ["`std::lower_bound(s.begin(), s.end(), x)` na secie działa w O(n) — używaj `s.lower_bound(x)`."],
  "qa": [ {"q": ["jak usunąć jeden element z multiset", "multiset erase tylko jeden", "usuwanie jednej kopii z multiseta"],
           "a": "Markdown: `ms.erase(ms.find(x))` usuwa jedną kopię; `ms.erase(x)` usuwa WSZYSTKIE.",
           "code": "multiset<int> ms = {1, 2, 2, 3};\nms.erase(ms.find(2)); // zostaje {1, 2, 3}"} ],
  "related": ["stl-multiset", "stl-map"]
} ] }
```
Kategorie (dokładnie te nazwy): "C++ język", "STL kontenery", "STL algorytmy", "Wejście/wyjście", "Liczby i typy",
"Pułapki", "OI praktyka", "Grafy", "Drzewa", "Struktury danych", "Techniki", "Programowanie dynamiczne", "Teksty",
"Matematyka", "Geometria", "Teoria gier", "Narzędzia".
- `id`: kebab-case, globalnie unikalne, z prefiksem domeny (`stl-`, `cpp-`, `io-`, `num-`, `pit-`, `oi-`, `graph-`,
  `tree-`, `ds-`, `tech-`, `dp-`, `str-`, `math-`, `geo-`, `game-`).
- `qa` = serce chatbota: KONKRETNE pytania, które uczeń realnie wpisze (różne sformułowania, potocznie, z literówkami,
  PL i EN), i zwięzła odpowiedź z kodem. Min. 5 QA na artykuł, przy popularnych tematach 10+.
- Kod: kompletne programy (z `int main`) kompilują się `g++-16 -std=c++20`; fragmenty bez main też muszą być poprawnym C++
  (walidator owija fragmenty w szablon z `#include <bits/stdc++.h>`/`using namespace std;` i `int main(){ ... }`,
  więc fragment = instrukcje albo definicje funkcji/struktur — oznacz definicje globalne polem `"scope": "global"` w bloku code
  lub w QA `"code_scope": "global"`).
- Walidacja: `python3 scripts/validate_kb.py [plik.json ...]` — schemat, unikalność id, `related`, kompilacja kodu.
  Plik ma przejść bez błędów. Pisz w kilku plikach po ~8–12 artykułów (łatwiej edytować i walidować).

## 6. Snippety (`data/snippets/*.json`)
```json
{ "snippets": [ { "trigger": "seg", "name": "Drzewo przedziałowe (suma, punkt-przedział)", "tags": ["segment tree", "drzewo przedziałowe"],
  "lang": "cpp", "scope": "global", "description": "...", "complexity": "O(log n) na operację",
  "code": "struct SegTree {\n    ...\n};", "usage": "SegTree st(n); st.update(i, v); st.query(l, r);",
  "kb": "ds-segment-tree" } ] }
```
`code` może zawierać `${1:nazwa}` (placeholdery tabstop) — opcjonalnie. Kod snippetu kompiluje się po wklejeniu
(scope global = definicje przed main; scope local = instrukcje w main).

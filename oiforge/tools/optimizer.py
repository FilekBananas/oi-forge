"""(i) Optymalizator kodu C++ — reguły (bez AI) przyspieszające program bez zmiany wyniku.

Najpierw szuka poprawek ALGORYTMICZNYCH (sumy prefiksowe zamiast sumowania przedziału w każdym zapytaniu, sparse table,
zbiór/sortowanie zamiast liniowego szukania, deque zamiast erase(begin()), spamiętywanie rekurencji, sort przed pętlą…),
potem stałych (szybkie I/O, endl, const &, reserve…). Lista jest posortowana według wpływu (`impact`). Gdy wzorzec jest
jednoznaczny, reguła ma gotowe przepisanie kodu (`has_edit`); w przeciwnym razie konkretną wskazówkę ze szkicem kodu.

POST /api/optimize {code, apply?: [rule_ids], verify?: bool}
    → {optimized_code, changes:[{rule, title, why, line, lines, safe, applied, has_edit, speedup_hint, suggestion_md,
       before, after, gain, impact, category, patch}], diff (unified), diff_rows (do widoku obok siebie),
       applied:[rule_ids], compile?, complexity?: {before, after}}
    Bez `apply` stosowane są tylko reguły bezpieczne; z `apply` — dokładnie wskazane (także ryzykowne, jeśli mają edycję).
    `gain` = zysk złożoności (np. „O(n·q) → O(n + q)”), `patch` = wiersze diffu tylko tej zmiany.
POST /api/optimize/benchmark {original, optimized, input? | input_template?, size?, repeats?}   (job)
    → uruchamia oba programy, porównuje wyjścia, liczy przyspieszenie.
GET  /api/optimize/rules → lista reguł.
"""
from __future__ import annotations

import difflib
import re

from oiforge.server_api import ApiError
from oiforge.tools._cppstruct import Code, normalize_expr

try:
    from oiforge.core import jobs, runner
except Exception:  # noqa: BLE001
    jobs = runner = None

RULES = {
    "prefix_sums": ("Sumy prefiksowe zamiast sumowania przedziału w każdym zapytaniu", True,
                    "każde zapytanie w O(1) zamiast O(długość przedziału)"),
    "prefix_sums_2d": ("Sumy prefiksowe 2D dla prostokątów", False, "zapytanie o prostokąt w O(1)"),
    "sparse_table": ("Sparse table dla min/max na przedziale", False, "zapytanie w O(1) po O(n log n) przygotowania"),
    "linear_search": ("Zbiór / sortowanie zamiast liniowego szukania w pętli", False,
                      "wyszukiwanie w O(1) lub O(log n) zamiast O(n)"),
    "pair_sum": ("Pary o danej sumie: tablica haszująca / dwa wskaźniki zamiast dwóch pętli", False,
                 "O(n²) → O(n) lub O(n log n)"),
    "memoize": ("Spamiętywanie (memo) w rekurencji z powtarzającymi się wywołaniami", True,
                "wykładniczo → liczba różnych stanów"),
    "hoist_sort": ("Sortowanie raz przed pętlą zamiast w każdym obrocie", True, "O(q·n log n) → O(n log n)"),
    "int_pow": ("pow() na liczbach całkowitych → szybkie potęgowanie / przesunięcie bitowe", False,
                "dokładny wynik (bez double) i O(log b)"),
    "small_map": ("Mapa z małymi kluczami → zwykła tablica", False, "O(1) i bez narzutu drzewa"),
    "fast_io": ("Szybkie wejście/wyjście", True, "do 3–5× szybsze wczytywanie dużych danych"),
    "endl": ("endl → '\\n'", True, "endl opróżnia bufor przy każdej linii; '\\n' jest wielokrotnie szybsze przy dużym wyjściu"),
    "const_ref": ("Przekazywanie kontenerów przez const &", True, "brak kopiowania przy każdym wywołaniu (O(n) → O(1))"),
    "pass_by_ref": ("Kontener modyfikowany w funkcji przekazywany przez wartość", False,
                    "kopia przy każdym wywołaniu — rozważ referencję"),
    "pow_square": ("pow(x, 2) → x * x", True, "mnożenie zamiast funkcji pow (double) — szybciej i dokładniej"),
    "string_concat": ("s = s + x → s += x", True, "brak kopiowania całego napisu (O(n²) → O(n))"),
    "reserve": ("reserve() przed wypełnianiem wektora", True, "mniej realokacji pamięci (zwykle kilka–kilkanaście %)"),
    "set_lower_bound": ("std::lower_bound na secie → s.lower_bound(x)", True, "O(n) → O(log n) na zapytanie"),
    "range_for_ref": ("for (auto x : …) → for (const auto& x : …)", True, "bez kopiowania elementu w każdym obrocie"),
    "hoist_strlen": ("strlen() poza warunkiem pętli", True, "strlen liczy się w O(n) przy każdym obrocie → O(n²)"),
    "sqrt_cond": ("i <= sqrt(n) → 1LL * i * i <= n", False, "bez liczenia pierwiastka w każdym obrocie i bez błędów zaokrągleń"),
    "unordered_map": ("map → unordered_map z własnym haszem", False, "średnio O(1) zamiast O(log n), ale bez porządku"),
    "pragma": ("#pragma GCC optimize", False, "czasem 10–30% szybciej, czasem nic — opcjonalne"),
    "iterative_dfs": ("Rekurencyjny DFS → iteracyjny", False, "brak ryzyka przepełnienia stosu przy głębokim grafie"),
    "flat_2d": ("vector<vector<T>> → płaska tablica", False, "lepsza lokalność pamięci, mniej alokacji"),
    "long_long": ("int → long long tam, gdzie grozi przepełnienie", False, "poprawność: wynik nie zmieści się w int"),
    "int_ll_define": ("#define int long long", False, "2× więcej pamięci dla tablic int — używaj long long tylko tam, gdzie trzeba"),
    "map_access": ("m[x] do sprawdzania istnienia → count/find", False, "operator[] tworzy nowe wpisy — mapa puchnie"),
    "erase_begin": ("vector.erase(begin()) / insert(begin()) w pętli → deque", True,
                    "O(n) → O(1) na każde usunięcie/wstawienie na początku"),
    "copy_in_loop": ("Kopia kontenera w pętli", False, "każda kopia to O(n)"),
    "memset_loop": ("memset całej tablicy w pętli", False, "czyść tylko używaną część (fill(a, a + n + 1, 0))"),
}

CUSTOM_HASH = """struct custom_hash {
    // splitmix64 — odporny na złośliwe testy anty-hash
    static unsigned long long splitmix64(unsigned long long x) {
        x += 0x9e3779b97f4a7c15ULL;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
        return x ^ (x >> 31);
    }
    size_t operator()(unsigned long long x) const {
        static const unsigned long long FIXED_RANDOM = chrono::steady_clock::now().time_since_epoch().count();
        return splitmix64(x + FIXED_RANDOM);
    }
};
"""

MUTATING = {"push_back", "pop_back", "emplace_back", "insert", "erase", "clear", "resize", "assign", "emplace",
            "push", "pop", "push_front", "pop_front", "swap", "reserve", "shrink_to_fit", "append", "replace",
            "emplace_front", "extract", "merge", "splice", "remove", "unique", "reverse", "sort"}
FREE_MUTATING = {"sort", "stable_sort", "reverse", "fill", "iota", "swap", "next_permutation", "prev_permutation",
                 "shuffle", "random_shuffle", "nth_element", "partial_sort", "rotate", "unique", "memset", "partial_sum",
                 "getline", "transform", "copy"}


# wpływ reguły na czas (do kolejności na liście): algorytm > duża stała > drobiazgi
IMPACT = {
    "prefix_sums": 100, "memoize": 99, "prefix_sums_2d": 98, "sparse_table": 96, "hoist_sort": 94,
    "linear_search": 92, "set_lower_bound": 91, "erase_begin": 90, "pair_sum": 88, "string_concat": 87,
    "hoist_strlen": 86, "const_ref": 40, "copy_in_loop": 70, "memset_loop": 66, "int_pow": 62, "endl": 30,
    "fast_io": 50, "long_long": 45, "pass_by_ref": 35, "unordered_map": 34, "small_map": 36, "map_access": 30,
    "range_for_ref": 25, "pow_square": 20, "iterative_dfs": 22, "sqrt_cond": 18, "reserve": 15, "flat_2d": 12,
    "int_ll_define": 10, "pragma": 5,
}
CATEGORY = {r: "algorytm" for r in ("prefix_sums", "memoize", "prefix_sums_2d", "sparse_table", "hoist_sort",
                                    "linear_search", "set_lower_bound", "erase_begin", "pair_sum", "string_concat",
                                    "hoist_strlen", "copy_in_loop", "memset_loop")}
CATEGORY.update({"long_long": "poprawność", "int_ll_define": "pamięć", "iterative_dfs": "stos", "map_access": "pamięć"})


class Change:
    def __init__(self, rule: str, line: int, why: str, edits: list | None = None, suggestion_md: str = "",
                 safe: bool | None = None, lines: list | None = None, before: str = "", after: str = "",
                 gain: str = "", impact: int | None = None):
        title, default_safe, hint = RULES[rule]
        self.rule = rule
        self.title = title
        self.why = why
        self.line = line
        self.lines = lines or [line]
        self.edits = edits or []
        self.safe = default_safe if safe is None else safe
        self.speedup_hint = hint
        self.suggestion_md = suggestion_md
        self.before = before
        self.after = after
        self.applied = False
        self.gain = gain
        self.impact = IMPACT.get(rule, 20) if impact is None else impact
        self.category = CATEGORY.get(rule, "stała")
        self.patch: list = []
        self.complexity: dict | None = None

    def to_dict(self) -> dict:
        return {"rule": self.rule, "title": self.title, "why": self.why, "line": self.line, "lines": self.lines,
                "safe": self.safe, "applied": self.applied, "has_edit": bool(self.edits),
                "speedup_hint": self.speedup_hint, "suggestion_md": self.suggestion_md, "before": self.before,
                "after": self.after, "gain": self.gain, "impact": self.impact, "category": self.category,
                "patch": self.patch, "complexity": self.complexity}


class Optimizer:
    def __init__(self, code: str):
        self.src = code.replace("\r\n", "\n")
        self.c = Code(self.src)
        self.changes: list[Change] = []
        self.lines = self.src.split("\n")
        self.interactive = self._interactive()
        self.mixed_io = bool(re.search(r"\b(scanf|printf|puts|getchar|putchar|gets|fgets)\s*\(", self.src)) and \
            bool(re.search(r"\b(cin|cout)\b", self.src))
        self._an_cache = None
        self._names_used: set = set()
        self._deque_vars: set = set()

    # ------------------------------------------------------------------ pomocnicze
    def pos_line(self, pos: int) -> int:
        return self.src.count("\n", 0, pos) + 1

    def tok_pos(self, i: int) -> int:
        return self.c.toks[i].pos

    def tok_end(self, i: int) -> int:
        return self.c.toks[i].end

    def indent_of_line(self, line: int) -> str:
        if 1 <= line <= len(self.lines):
            m = re.match(r"[ \t]*", self.lines[line - 1])
            return m.group(0) if m else ""
        return ""

    def line_start_pos(self, line: int) -> int:
        return sum(len(l) + 1 for l in self.lines[:line - 1])

    def insert_before_stmt(self, tok_idx: int, stmt: str) -> tuple:
        """Edycja wstawiająca instrukcję przed tokenem: w osobnej linii, jeśli token zaczyna linię, inaczej w tej samej."""
        t = self.c.toks[tok_idx]
        ls = self.line_start_pos(t.line)
        before = self.src[ls:t.pos]
        if before.strip() == "":
            return (ls, ls, f"{before}{stmt}\n")
        return (t.pos, t.pos, f"{stmt} ")

    def _interactive(self) -> bool:
        c = self.c
        if re.search(r"fflush\s*\(|cout\s*\.\s*flush|<<\s*flush\b", self.src):
            return True
        for L in c.loops:
            reads = [io for io in c.io if io.kind == "read" and L.body[0] <= io.idx <= L.body[1]]
            writes = [io for io in c.io if io.kind == "write" and L.body[0] <= io.idx <= L.body[1]]
            if reads and writes and min(w.idx for w in writes) < max(r.idx for r in reads) and \
                    any(t in ("endl", "std::endl") for w in writes for t in w.targets) and \
                    min(w.idx for w in writes) < min(r.idx for r in reads):
                return True
        return False

    # ------------------------------------------------------------------ reguły
    def r_fast_io(self):
        c = self.c
        uses_cin = any(io.api == "cin" for io in c.io)
        uses_cout = any(io.api == "cout" for io in c.io)
        if not (uses_cin or uses_cout):
            return
        has_sync = any(cl.name == "sync_with_stdio" for cl in c.calls)
        has_tie = any(cl.name == "tie" and cl.receiver == "cin" for cl in c.calls)
        main = c.func_by_name.get("main")
        if main is None or (has_sync and has_tie):
            return
        many_reads = any(c.loops_containing(io.idx) for io in c.io if io.kind == "read") or \
            any(c.loops_containing(io.idx) for io in c.io if io.kind == "write")
        if not many_reads:
            return
        safe = not (self.mixed_io or self.interactive)
        why = "Program wczytuje/wypisuje dużo danych przez cin/cout bez wyłączenia synchronizacji ze stdio."
        if self.mixed_io:
            why += " UWAGA: program miesza cin/cout ze scanf/printf — po sync_with_stdio(false) kolejność wyjścia " \
                   "może się pomieszać! Najpierw ujednolić wejście/wyjście."
        if self.interactive:
            why += " UWAGA: program wygląda na interaktywny (wypisuje i od razu czyta odpowiedź) — cin.tie(nullptr) " \
                   "wymaga ręcznego opróżniania bufora (cout.flush())."
        # wstaw po '{' main
        open_pos = self.tok_end(main.open_idx)
        next_line = self.pos_line(open_pos) + 1
        ind = self.indent_of_line(next_line) or "    "
        if not ind.strip() == "" or ind == "":
            ind = "    "
        ins = ""
        if not has_sync:
            ins += f"\n{ind}ios_base::sync_with_stdio(false);"
        if not has_tie:
            ins += f"\n{ind}cin.tie(nullptr);"
        self.changes.append(Change("fast_io", main.line, why, [(open_pos, open_pos, ins)], safe=safe,
                                   before="int main() {", after="int main() {" + ins.replace("\n", " ")))

    def r_endl(self):
        c = self.c
        if self.interactive:
            uses = [io for io in c.io if io.kind == "write" and any(t in ("endl", "std::endl") for t in io.targets)]
            if uses:
                self.changes.append(Change("endl", uses[0].line, "Program jest interaktywny — endl jest tu potrzebny "
                                                                 "(opróżnia bufor po każdej odpowiedzi). Nie zmieniaj.",
                                           safe=False, lines=[u.line for u in uses]))
            return
        edits = []
        lines = []
        for i, t in enumerate(c.toks):
            if t.text != "endl" or t.kind != "id":
                continue
            # tylko w łańcuchu << (cout/cerr)
            if i == 0 or c.texts[i - 1] not in ("<<", "::"):
                continue
            start = i
            if c.texts[i - 1] == "::" and i >= 2 and c.texts[i - 2] == "std":
                start = i - 2
            edits.append((self.tok_pos(start), self.tok_end(i), "'\\n'"))
            lines.append(t.line)
        if not edits:
            return
        in_loop = any(c.innermost_loop(i) is not None for i, t in enumerate(c.toks) if t.text == "endl")
        why = f"{len(edits)}× endl" + (" (także w pętli)" if in_loop else "") + \
              " — każde endl wymusza opróżnienie bufora wyjścia. Znak '\\n' daje ten sam wynik bez flush."
        self.changes.append(Change("endl", lines[0], why, edits, lines=sorted(set(lines)),
                                   before="cout << x << endl;", after="cout << x << '\\n';"))

    def _param_ranges(self, f):
        c = self.c
        j = f.open_idx - 1
        while j > 0 and c.texts[j] != ")":
            j -= 1
        if j not in c.pairs:
            return []
        po = c.pairs[j]
        if j - po <= 1:
            return []
        return c.split_top(po + 1, j - 1, (",",))

    def _modified_in(self, name: str, a: int, b: int) -> bool:
        c = self.c
        for asg in c.assigns_in(a, b, name):
            return True
        for cl in c.calls:
            if not (a <= cl.idx <= b):
                continue
            if cl.receiver == name and cl.name in MUTATING:
                return True
            if not cl.is_method and cl.args:
                argtexts = [normalize_expr(c.text(x, y)) for x, y in cl.args]
                if cl.name in FREE_MUTATING and any(re.match(rf"&?{re.escape(name)}\b", t) for t in argtexts):
                    return True
                g = c.func_by_name.get(cl.name)
                if g is not None:
                    for k, t in enumerate(argtexts):
                        if t == name and k < len(g.params) and g.params[k].is_ref and not g.params[k].is_const:
                            return True
        txt = normalize_expr(c.text(a, b))
        en = re.escape(name)
        if re.search(rf"&{en}\b|for\(auto&\w+:{en}\)|for\(auto&\[[^\]]*\]:{en}\)|\b{en}=|cin>>{en}\b|>>{en}\[", txt):
            return True
        return False

    def r_const_ref(self):
        c = self.c
        for f in c.functions:
            if f.name == "main":
                continue
            ranges = self._param_ranges(f)
            for p in f.params:
                if p.is_ref or p.is_ptr or p.kind not in ("vector", "string", "set", "multiset", "map", "multimap", "umap",
                                                          "uset", "deque", "pq", "queue", "stack", "struct"):
                    continue
                rng = next(((a, b) for a, b in ranges if a <= p.idx <= b), None)
                if rng is None:
                    continue
                a, b = rng
                modified = self._modified_in(p.name, f.open_idx, f.close_idx)
                rec = bool(f.self_calls)
                tstart = self.tok_pos(a)
                tend = self.tok_pos(p.idx)
                type_src = self.src[tstart:tend].rstrip()
                if modified:
                    why = (f"Parametr {p.name} ({type_src}) jest kopiowany przy każdym wywołaniu {f.name}()" +
                           (" — a funkcja jest rekurencyjna, więc kopii jest bardzo dużo" if rec else "") +
                           ". Funkcja go modyfikuje, więc zamiana na referencję ZMIENI działanie (zmiany będą widoczne "
                           "u wołającego) — zrób to tylko, jeśli tak ma być.")
                    self.changes.append(Change("pass_by_ref", p.line, why,
                                               [(tstart, tend, f"{type_src}& ")], safe=False,
                                               before=f"{type_src} {p.name}", after=f"{type_src}& {p.name}"))
                    continue
                if type_src.startswith("const "):
                    new = f"{type_src}& "
                else:
                    new = f"const {type_src}& "
                why = (f"Parametr {p.name} ({type_src}) jest kopiowany przy każdym wywołaniu {f.name}()" +
                       (" (funkcja rekurencyjna — kopia na każdym poziomie!)" if rec else "") +
                       ". Funkcja go nie zmienia, więc const & daje ten sam wynik bez kopiowania.")
                self.changes.append(Change("const_ref", p.line, why, [(tstart, tend, new)],
                                           before=f"{type_src} {p.name}", after=f"{new}{p.name}"))

    def _simple_expr(self, a: int, b: int) -> bool:
        c = self.c
        for i in range(a, b + 1):
            t = c.texts[i]
            if t in ("++", "--", "=", "+=", "-=", "*=", "/=", ",") or (c.toks[i].kind == "id" and i + 1 <= b and
                                                                       c.texts[i + 1] == "("):
                return False
        return True

    def r_pow(self):
        c = self.c
        for cl in c.calls:
            if cl.name not in ("pow", "powl") or cl.is_method or len(cl.args) != 2:
                continue
            ex = normalize_expr(c.text(*cl.args[1]))
            if ex not in ("2", "3", "2.0", "3.0"):
                continue
            a, b = cl.args[0]
            if not self._simple_expr(a, b):
                continue
            base_src = self.src[self.tok_pos(a):self.tok_end(b)]
            simple = all(c.toks[i].kind in ("id", "num") or c.texts[i] in ("[", "]", ".", "->", "::")
                         for i in range(a, b + 1))
            base = base_src if simple else f"({base_src})"
            k = 2 if ex.startswith("2") else 3
            ids = c.ids_in(a, b)
            d = c.decl_of(ids[0], cl.idx) if ids else None
            floating = d is not None and re.search(r"double|float", d.type or d.elem or "")
            start = self.tok_pos(cl.idx - 2) if cl.std and cl.idx >= 2 else self.tok_pos(cl.idx)
            end = self.tok_end(cl.close_idx)
            if floating:
                rep = " * ".join([base] * k)
                self.changes.append(Change("pow_square", cl.line, f"pow({base_src}, {k}) na liczbach rzeczywistych — "
                                                                  f"mnożenie jest szybsze i dokładniejsze.",
                                           [(start, end, rep)], before=self.src[start:end], after=rep))
            else:
                rep = "1LL * " + " * ".join([base] * k)
                self.changes.append(Change("pow_square", cl.line,
                                           f"pow({base_src}, {k}) liczy w double (błędy zaokrągleń dla dużych liczb!). "
                                           f"Dla liczb całkowitych lepiej {rep} — ale typ wyniku zmieni się z double na "
                                           f"long long, sprawdź kontekst (np. wypisywanie).",
                                           [(start, end, rep)], safe=False, before=self.src[start:end], after=rep))

    def r_string_concat(self):
        c = self.c
        for asg in c.assigns:
            if asg.op != "=" or not asg.rhs or asg.indexed:
                continue
            d = c.decl_of(asg.target, asg.idx)
            if d is None or d.kind != "string":
                continue
            ra, rb = asg.rhs
            if c.texts[ra] != asg.target or ra + 1 > rb or c.texts[ra + 1] != "+":
                continue
            rest = (ra + 2, rb)
            if rest[0] > rest[1]:
                continue
            # tylko jeden składnik (s + a + b dla znaków to pułapka: a + b to int!)
            if c.find_top(rest[0], rest[1], ("+", "-", "?")):
                continue
            x_src = self.src[self.tok_pos(rest[0]):self.tok_end(rest[1])]
            start = self.tok_pos(asg.idx)
            end = self.tok_end(rb)
            new = f"{asg.target} += {x_src}"
            in_loop = c.innermost_loop(asg.idx) is not None
            fn = c.enclosing_func(asg.idx)
            in_loop = in_loop or (fn is not None and fn.name != "main")
            self.changes.append(Change("string_concat", asg.line,
                                       f"{asg.target} = {asg.target} + {x_src} tworzy nową kopię całego napisu" +
                                       (" w każdym obrocie pętli — razem O(n²)" if in_loop else "") +
                                       f". {asg.target} += {x_src} dopisuje w miejscu.",
                                       [(start, end, new)], before=self.src[start:end], after=new))

    def r_reserve(self):
        c = self.c
        done = set()
        for cl in c.calls:
            if cl.name not in ("push_back", "emplace_back") or not cl.receiver or cl.receiver_text != cl.receiver:
                continue
            v = cl.receiver
            if v in done or v in self._deque_vars:
                continue                      # deque (po regule erase_begin) nie ma reserve()
            d = c.decl_of(v, cl.idx)
            if d is None or d.kind != "vector" or d.ctor or d.init or d.is_param:
                continue
            if any(x.receiver == v and x.name in ("reserve", "resize", "assign") for x in c.calls):
                continue
            pushes = [x for x in c.calls if x.receiver == v and x.receiver_text == v and x.name in ("push_back", "emplace_back")]
            if len(pushes) != 1:
                continue
            k = c.innermost_loop(cl.idx)
            if k is None or c.loops[k].parent is not None:
                continue
            L = c.loops[k]
            if L.kind != "for" or not L.cond or not L.init or not L.var:
                continue
            m = re.fullmatch(rf"{re.escape(L.var)}(<|<=)([A-Za-z_]\w*)([+-]\d+)?", normalize_expr(c.text(*L.cond)))
            if not m:
                continue
            bound = m.group(2)
            if bound in c.consts:
                continue
            init = normalize_expr(c.text(*L.init))
            if not re.search(rf"{re.escape(L.var)}=(0|1)$", init):
                continue
            # bound musi być znane przed pętlą (wczytane lub przypisane), deklaracja wektora też przed pętlą
            known = any(io.kind == "read" and io.idx < L.idx and any(re.match(rf"\s*{re.escape(bound)}\b", t)
                                                                      for t in io.targets) for io in c.io)
            known = known or any(a.target == bound and a.idx < L.idx and a.op == "=" for a in c.assigns)
            if not known or d.idx > L.idx:
                continue
            f_loop = c.func_name_at(L.idx)
            if d.scope != "global" and d.scope != f_loop:
                continue
            if c.enclosing_func(L.idx) is None:
                continue
            line = L.line
            extra = ""
            if m.group(1) == "<=":
                extra = " + 1"
            stmt = f"{v}.reserve({bound}{extra});"
            done.add(v)
            self.changes.append(Change("reserve", line, f"Wektor {v} jest wypełniany push_back w pętli o {bound} obrotach — "
                                                        f"reserve rezerwuje pamięć raz, zamiast kilkukrotnie przenosić "
                                                        f"elementy.", [self.insert_before_stmt(L.idx, stmt)], before="",
                                       after=stmt))

    def r_set_lower_bound(self):
        c = self.c
        for cl in c.calls:
            if cl.name not in ("lower_bound", "upper_bound") or cl.is_method or len(cl.args) != 3:
                continue
            t0 = normalize_expr(c.text(*cl.args[0]))
            t1 = normalize_expr(c.text(*cl.args[1]))
            m0 = re.fullmatch(r"(\w+)\.begin\(\)", t0)
            m1 = re.fullmatch(r"(\w+)\.end\(\)", t1)
            if not (m0 and m1 and m0.group(1) == m1.group(1)):
                continue
            s = m0.group(1)
            d = c.decl_of(s, cl.idx)
            if d is None or d.kind not in ("set", "multiset", "map", "multimap"):
                continue
            x_src = self.src[self.tok_pos(cl.args[2][0]):self.tok_end(cl.args[2][1])]
            start = self.tok_pos(cl.idx - 2) if cl.std and cl.idx >= 2 else self.tok_pos(cl.idx)
            end = self.tok_end(cl.close_idx)
            new = f"{s}.{cl.name}({x_src})"
            self.changes.append(Change("set_lower_bound", cl.line,
                                       f"std::{cl.name} na {d.kind} nie może skakać po elementach — przechodzi je po kolei "
                                       f"(O(n)). Metoda {s}.{cl.name}(x) korzysta z drzewa (O(log n)).",
                                       [(start, end, new)], before=self.src[start:end], after=new))

    def r_range_for(self):
        c = self.c
        for L in c.loops:
            if L.kind != "range":
                continue
            o, cl_ = L.head
            colon = c.find_top(o + 1, cl_ - 1, (":",))
            if not colon:
                continue
            decl_a, decl_b = o + 1, colon[0] - 1
            dtxt = normalize_expr(c.text(decl_a, decl_b))
            if not re.fullmatch(r"auto\w+", dtxt.replace(" ", "")) and not re.fullmatch(r"auto [A-Za-z_]\w*", c.text(decl_a, decl_b)):
                continue
            var = L.var
            ra, rb = L.range_expr
            ids = c.ids_in(ra, rb)
            if not ids:
                continue
            d = c.decl_of(ids[0], L.idx)
            if d is None:
                continue
            elem = d.elem if d.kind == "vector" and "[" not in c.text(ra, rb) else (
                c.classify_type(d.elem)[1] if d.kind == "vector" and d.nested >= 2 else
                (d.elem if d.kind == "array" else ""))
            if d.kind in ("map", "multimap", "umap"):
                elem = "pair<" + d.elem + ">"
            heavy = re.search(r"vector|string|set|map|deque|pair<[^>]*(vector|string)", elem or "")
            if not heavy:
                continue
            if self._modified_in(var, L.body[0], L.body[1]):
                continue
            start, end = self.tok_pos(decl_a), self.tok_end(decl_b)
            new = f"const auto& {var}"
            self.changes.append(Change("range_for_ref", L.line,
                                       f"Pętla kopiuje każdy element ({elem}) do zmiennej {var}. Element nie jest "
                                       f"modyfikowany, więc const auto& wystarczy.",
                                       [(start, end, new)], before=self.src[start:end], after=new))

    def r_strlen(self):
        c = self.c
        used = set(c.texts)
        for L in c.loops:
            if L.kind != "for" or not L.cond:
                continue
            for i in range(L.cond[0], L.cond[1] + 1):
                if c.texts[i] != "strlen" or i + 1 > L.cond[1] or c.texts[i + 1] != "(":
                    continue
                close = c.pairs.get(i + 1)
                if close is None:
                    continue
                arg = normalize_expr(c.text(i + 2, close - 1))
                if not re.fullmatch(r"\w+", arg) or self._modified_in(arg, L.body[0], L.body[1]):
                    continue
                name = f"len_{arg}"
                k = 2
                while name in used:
                    name = f"len_{arg}{k}"
                    k += 1
                used.add(name)
                line = L.line
                edits = [self.insert_before_stmt(L.idx, f"const int {name} = strlen({arg});"),
                         (self.tok_pos(i), self.tok_end(close), name)]
                self.changes.append(Change("hoist_strlen", line, f"strlen({arg}) w warunku pętli liczy długość od nowa "
                                                                 f"w każdym obrocie (O(n) za każdym razem).", edits,
                                           before=f"i < strlen({arg})", after=f"i < {name}"))

    def r_sqrt_cond(self):
        c = self.c
        for L in c.loops:
            if L.kind != "for" or not L.cond or not L.var:
                continue
            ct = normalize_expr(c.text(*L.cond))
            m = re.fullmatch(rf"{re.escape(L.var)}<=\(?(int|longlong)?\)?(sqrtl?)\((\w+)\)", ct)
            if not m:
                continue
            n = m.group(3)
            if self._modified_in(n, L.body[0], L.body[1]):
                why_extra = f" UWAGA: {n} zmienia się w pętli — nowa wersja też to uwzględni (warunek liczony co obrót)."
            else:
                why_extra = ""
            start, end = self.tok_pos(L.cond[0]), self.tok_end(L.cond[1])
            new = f"1LL * {L.var} * {L.var} <= {n}"
            self.changes.append(Change("sqrt_cond", L.line, f"sqrt({n}) w warunku liczy pierwiastek w każdym obrocie "
                                                            f"(i może się pomylić o 1 przez zaokrąglenia double)."
                                                            + why_extra, [(start, end, new)],
                                       before=self.src[start:end], after=new))

    def r_unordered(self):
        c = self.c
        for d in c.decls:
            if d.kind not in ("map",) or d.is_param:
                continue
            key = (d.elem.split(",")[0] if d.elem else "").strip()
            if not re.fullmatch(r"(long long|int|long|unsigned long long|unsigned|ll|int64_t|long long int)", key):
                continue
            nm = re.escape(d.name)
            flat = normalize_expr(self.src)
            ordered = re.search(rf"{nm}\.(begin|rbegin|end|lower_bound|upper_bound|equal_range|rend)\(|"
                                rf"for\([^;)]*:{nm}\)|prev\(|next\(", flat)
            if ordered:
                continue
            # edycja: map< → unordered_map<…, custom_hash>
            idx = d.idx
            j = idx - 1
            while j >= 0 and c.texts[j] != "map":
                j -= 1
            if j < 0:
                continue
            gt = c._skip_template(j + 1)
            if gt is None:
                continue
            start = self.tok_pos(j - 2) if j >= 2 and c.texts[j - 1] == "::" else self.tok_pos(j)
            inner = self.src[self.tok_end(j + 1):self.tok_pos(gt)]
            new = f"unordered_map<{inner}, custom_hash>"
            edits = [(start, self.tok_end(gt), new)]
            # dodaj definicję haszu po ostatnim #include / using namespace
            m = None
            for mm in re.finditer(r"^\s*(#include[^\n]*|using\s+namespace\s+std\s*;)[^\n]*$", self.src, re.M):
                m = mm
            ins_pos = m.end() + 1 if m else 0
            if "custom_hash" not in self.src:
                edits.append((ins_pos, ins_pos, "\n" + CUSTOM_HASH))
            self.changes.append(Change("unordered_map", d.line,
                                       f"Mapa {d.name} nie jest przeglądana w kolejności kluczy — tablica haszująca "
                                       f"daje średnio O(1). Własny hasz (splitmix64) chroni przed testami anty-hash. "
                                       f"Ryzyko: przy bardzo wielu elementach unordered_map bywa wolniejsza przez "
                                       f"alokacje; zmierz benchmarkiem.", edits, safe=False,
                                       before=f"map<{inner}>", after=new))

    def r_pragma(self):
        if "#pragma GCC optimize" in self.src:
            return
        new = '#pragma GCC optimize("O3,unroll-loops")\n'
        self.changes.append(Change("pragma", 1, "Wymusza agresywniejsze optymalizacje kompilatora. Opcjonalne: czasem "
                                                "przyspiesza pętle o 10–30%, czasem nic nie daje. Nie dodawaj "
                                                "#pragma GCC target(\"avx2\") — na starszych sprawdzarkach może "
                                                "skończyć się błędem wykonania.", [(0, 0, new)], safe=False,
                                   before="", after=new.strip()))

    def r_iterative_dfs(self):
        c = self.c
        for f in c.functions:
            if not f.self_calls or not f.params:
                continue
            adj = None
            for cl in f.self_calls:
                k = c.innermost_loop(cl.idx)
                if k is None:
                    continue
                L = c.loops[k]
                txt = c.text(*L.range_expr) if L.kind == "range" else (c.text(*L.cond) if L.cond else "")
                m = re.search(r"(\w+)\[", txt)
                if m:
                    d = c.decl_of(m.group(1))
                    if d is not None and (d.nested >= 2 or d.kind == "array"):
                        adj = m.group(1)
            if not adj:
                continue
            body = normalize_expr(c.text(f.open_idx, f.close_idx))
            vm = re.search(r"(\w+)\[" + re.escape(f.params[0].name) + r"\]=(true|1)", body)
            vis = vm.group(1) if vm else "vis"
            v = f.params[0].name
            code = (f"void {f.name}_iter(int start) {{\n"
                    f"    vector<int> st = {{start}};\n"
                    f"    {vis}[start] = true;\n"
                    f"    while (!st.empty()) {{\n"
                    f"        int {v} = st.back(); st.pop_back();\n"
                    f"        for (int u : {adj}[{v}]) {{\n"
                    f"            if (!{vis}[u]) {{ {vis}[u] = true; st.push_back(u); }}\n"
                    f"        }}\n"
                    f"    }}\n}}")
            md = (f"Rekurencja w {f.name}() może zejść tak głęboko, jak długa jest najdłuższa ścieżka (do n "
                  f"poziomów). Przy n ~ 10⁶ zwykły stos (np. 8 MB na Linuksie, 1 MB na Windows) może się przepełnić. "
                  f"Iteracyjna wersja z jawnym stosem (kolejność odwiedzin może być inna — to ważne, jeśli liczysz "
                  f"czasy wejścia/wyjścia):\n\n```cpp\n{code}\n```")
            self.changes.append(Change("iterative_dfs", f.line, f"Głęboka rekurencja w {f.name}() — ryzyko przepełnienia "
                                                                f"stosu.", suggestion_md=md))

    def r_flat_2d(self):
        c = self.c
        for d in c.decls:
            if d.kind != "vector" or d.nested < 2 or len(d.ctor) < 2 or "vector" not in d.ctor[1]:
                continue
            inner = c.classify_type(d.elem)[1]
            m = re.search(r"\(([^,()]+)", d.ctor[1])
            mdim = m.group(1).strip() if m else "M"
            md = (f"`{d.name}` to tablica {d.ctor[0]} × {mdim} jako `vector<vector<{inner}>>` — każdy wiersz to osobna "
                  f"alokacja. Szybciej (lepsza pamięć podręczna) jest jedną tablicą:\n\n```cpp\n"
                  f"vector<{inner}> {d.name}({d.ctor[0]} * ({mdim}));\n// zamiast {d.name}[i][j]: {d.name}[i * ({mdim}) + j]\n```\n"
                  f"albo statyczną tablicą `{inner} {d.name}[MAXN][MAXM];`, jeśli rozmiary są znane z góry.")
            self.changes.append(Change("flat_2d", d.line, f"Dwuwymiarowy vector {d.name} — wolniejszy dostęp i dużo "
                                                          f"alokacji.", suggestion_md=md))

    def r_long_long(self):
        c = self.c
        for a in c.assigns:
            if a.op not in ("+=", "*=", "=") or a.indexed or not a.rhs:
                continue
            d = c.decl_of(a.target, a.idx)
            if d is None or d.kind != "scalar" or not re.fullmatch(r"(int|signed|unsigned|unsigned int|short)", d.type.strip()):
                continue
            if c.int_is_ll:
                continue
            rhs = normalize_expr(c.text(*a.rhs))
            risky = False
            why = ""
            if a.op == "*=" and c.innermost_loop(a.idx) is not None:
                risky, why = True, f"{a.target} jest mnożone w pętli"
            elif a.op == "+=" and c.innermost_loop(a.idx) is not None and ("*" in rhs or len(c.loops_containing(a.idx)) >= 2):
                risky, why = True, f"do {a.target} są dodawane w pętli iloczyny/wiele składników"
            elif a.op == "+=" and c.innermost_loop(a.idx) is not None and re.search(r"\w+\[\w+\]", rhs):
                arr = re.match(r"(\w+)\[", rhs).group(1) if re.match(r"(\w+)\[", rhs) else ""
                read_arr = any(io.kind == "read" and any(re.match(rf"\s*{re.escape(arr)}\[", t) for t in io.targets)
                               for io in c.io)
                if read_arr:
                    risky, why = True, f"{a.target} sumuje wczytane liczby w pętli (n · max wartość może przekroczyć 2·10⁹)"
            if not risky:
                continue
            used_printf = re.search(rf"printf\([^;]*\b{re.escape(a.target)}\b", self.src)
            # edycja: deklaracja 'int x' → 'long long x' (tylko pojedyncza deklaracja)
            edits = []
            j = d.idx - 1
            in_minmax = re.search(rf"\b(min|max)\([^;]*\b{re.escape(a.target)}\b", self.src)
            if j >= 0 and c.texts[j] == "int" and (j == 0 or c.texts[j - 1] in (";", "{", "}", "(", ")")) and \
                    (d.idx + 1 < c.n and c.texts[d.idx + 1] in (";", "=")) and not in_minmax:
                edits = [(self.tok_pos(j), self.tok_end(j), "long long")]
            md = (f"Wartość `{a.target}` może przekroczyć zakres int (≈ 2,1·10⁹): {why}. Jeśli ograniczenia na to "
                  f"pozwalają, zmień typ na `long long`" + (" (i format %d → %lld w printf)" if used_printf else "") + ".")
            self.changes.append(Change("long_long", a.line, f"Możliwe przepełnienie int: {why}.", edits, safe=False,
                                       suggestion_md=md, before=f"int {a.target}", after=f"long long {a.target}"))
            break
        # long long x = a * b  (a, b int)
        for a in c.assigns:
            if a.op != "=" or not a.rhs:
                continue
            d = c.decl_of(a.target, a.idx)
            if d is None or "long long" not in c.resolve_type(d.type if d.kind == "scalar" else d.elem or ""):
                continue
            r = normalize_expr(c.text(*a.rhs))
            m = re.fullmatch(r"(\w+)\*(\w+)", r)
            if not m:
                continue
            ds = [c.decl_of(x, a.idx) for x in (m.group(1), m.group(2))]
            if all(x is not None and x.kind == "scalar" and x.type.strip() == "int" for x in ds) and not c.int_is_ll:
                pos = self.tok_pos(a.rhs[0])
                self.changes.append(Change("long_long", a.line,
                                           f"{m.group(1)} * {m.group(2)} liczy się w int i przepełnia, ZANIM wynik trafi do "
                                           f"long long. Rozwiązanie: 1LL * {m.group(1)} * {m.group(2)}.",
                                           [(pos, pos, "1LL * ")], safe=True, before=r, after="1LL * " + r))
                self.changes[-1].speedup_hint = "poprawność (bez przepełnienia), szybkość bez zmian"
        if c.int_is_ll:
            big = [d for d in c.decls if d.kind == "array" and d.dims and "int" in d.elem and
                   (c._eval_const_text(d.dims[0]) or 0) >= 10 ** 6]
            if big:
                md = (f"`#define int long long` podwaja pamięć tablic int (np. `{big[0].name}`: "
                      f"{big[0].dims[0]} elementów × 8 B zamiast 4 B) i spowalnia operacje na pamięci. Lepiej używać "
                      f"`long long` tylko dla zmiennych, które naprawdę przekraczają 2·10⁹.")
                self.changes.append(Change("int_ll_define", 1, "Wszystkie int są long long — więcej pamięci.",
                                           suggestion_md=md))

    def r_map_access(self):
        c = self.c
        for m in re.finditer(r"\bif\s*\(\s*!?\s*(\w+)\s*\[([^\]]+)\]\s*(==\s*0|!=\s*0|>\s*0)?\s*\)", self.src):
            name = m.group(1)
            d = c.decl_of(name)
            if d is None or d.kind not in ("map", "umap"):
                continue
            line = self.pos_line(m.start())
            md = (f"`{name}[{m.group(2)}]` dla nieistniejącego klucza TWORZY wpis z wartością 0 — mapa rośnie przy każdym "
                  f"sprawdzeniu. Do sprawdzania istnienia użyj `{name}.count({m.group(2)})` albo "
                  f"`{name}.find({m.group(2)}) != {name}.end()`.")
            self.changes.append(Change("map_access", line, f"{name}[…] użyte tylko do sprawdzenia.", suggestion_md=md))
            break

    def r_copy_in_loop(self):
        c = self.c
        for d in c.decls:
            if d.kind not in ("vector", "string", "set", "map") or d.is_param or not d.init:
                continue
            if c.innermost_loop(d.idx) is None:
                continue
            src = normalize_expr(d.init)
            d2 = c.decl_of(src, d.idx) if re.fullmatch(r"\w+", src) else None
            if d2 is None or d2.kind != d.kind:
                continue
            md = (f"`{d.type} {d.name} = {src};` w pętli kopiuje cały kontener w każdym obrocie. Jeśli tylko czytasz — "
                  f"użyj `const auto& {d.name} = {src};`; jeśli modyfikujesz — może da się pracować na oryginale.")
            self.changes.append(Change("copy_in_loop", d.line, f"Kopia {src} w pętli.", suggestion_md=md))

    def r_memset_loop(self):
        c = self.c
        for cl in c.calls:
            if cl.name != "memset" or c.innermost_loop(cl.idx) is None or not cl.args:
                continue
            ids = c.ids_in(*cl.args[0])
            d = c.decl_of(ids[0]) if ids else None
            size = c._eval_const_text(d.dims[0]) if d is not None and d.dims else None
            if size and size >= 10 ** 5:
                md = (f"`memset({ids[0]}, …)` czyści całą tablicę ({d.dims[0]} elementów) w każdym obrocie pętli (np. "
                      f"dla każdego zestawu testów). Czyść tylko używaną część: `fill({ids[0]}, {ids[0]} + n + 1, 0);`.")
                self.changes.append(Change("memset_loop", cl.line, "Czyszczenie całej dużej tablicy w pętli.",
                                           suggestion_md=md))

    # ================================================================== reguły algorytmiczne
    # ------------------------------------------------------------------ pomocnicze
    def _an(self):
        """Analizator złożoności (krotności pętli, rozmiary tablic) — liczony leniwie, raz na optymalizację."""
        if self._an_cache is None:
            try:
                from oiforge.tools import complexity as cxm
                an = cxm.Analyzer(self.src)
                an.run()
                self._an_cache = an
            except Exception:  # noqa: BLE001
                self._an_cache = False
        return self._an_cache or None

    def _fix(self, cost):
        an = self._an()
        if an is None or cost is None:
            return cost
        lo = {v for v in cost.vars() if v.startswith("§")}
        return cost.subst({v: an._param_fallback(v) for v in lo}) if lo else cost

    def _mult_cost(self, idx: int):
        an = self._an()
        if an is None:
            return None
        try:
            return self._fix(an.mult_at(idx))
        except Exception:  # noqa: BLE001
            return None

    def _loop_mult(self, k: int):
        an = self._an()
        if an is None:
            return None
        try:
            return self._fix(an.mult(k))
        except Exception:  # noqa: BLE001
            return None

    def _size_cost(self, name: str, at: int):
        an = self._an()
        if an is None:
            return None
        try:
            return self._fix(an._domain(an.csize(name, at)))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _o(cost, default: str = "?") -> str:
        return f"O({cost.expr()})" if cost is not None else default

    def _unique(self, base: str) -> str:
        used = set(self.c.texts) | self._names_used
        name = base
        k = 2
        while name in used:
            name = f"{base}{k}"
            k += 1
        self._names_used.add(name)
        return name

    def _is_integral(self, type_text: str) -> bool:
        t = self.c.resolve_type(type_text or "").strip()
        t = re.sub(r"\b(const|static|constexpr|volatile|signed)\b", "", t).strip()
        t = re.sub(r"\s+", " ", t)
        return bool(re.fullmatch(r"(unsigned )?(long long( int)?|long( int)?|int|short|char)|unsigned|"
                                 r"u?int(8|16|32|64)_t|size_t", t))

    def _elem_type(self, d) -> str | None:
        """Typ elementu jednowymiarowej tablicy/vectora liczb całkowitych albo None."""
        if d is None:
            return None
        if d.kind == "array" and len(d.dims) == 1:
            et = d.elem or d.type
        elif d.kind == "vector" and d.nested == 1:
            et = d.elem
        else:
            return None
        return et if self._is_integral(et) else None

    def _body_stmts(self, L) -> list:
        c = self.c
        a, b = L.body
        if a <= b and c.texts[a] == "{":
            return c.block_statements(a + 1, b - 1) if b - 1 >= a + 1 else []
        return [(a, b)]

    def _for_parts(self, L):
        """(zakres LO, zakres HI, inclusive, zmienna zadeklarowana w for?) dla for (int i = LO; i <= HI; i++)."""
        c = self.c
        if L.kind != "for" or not L.var or not L.init or not L.cond or not L.step:
            return None
        v = L.var
        st = normalize_expr(c.text(*L.step))
        if st not in (f"{v}++", f"++{v}", f"{v}+=1"):
            return None
        eq = c.find_top(L.init[0], L.init[1], ("=",))
        if not eq or eq[0] + 1 > L.init[1] or c.find_top(L.init[0], L.init[1], (",",)):
            return None
        declared = c.texts[L.init[0]] != v
        ops = c.find_top(L.cond[0], L.cond[1], ("<=", "<"))
        if len(ops) != 1 or c.find_top(L.cond[0], L.cond[1], ("&&", "||")):
            return None
        o = ops[0]
        if normalize_expr(c.text(L.cond[0], o - 1)) != v or o + 1 > L.cond[1]:
            return None
        return (eq[0] + 1, L.init[1]), (o + 1, L.cond[1]), c.texts[o] == "<=", declared

    def _src_of(self, rng) -> str:
        return self.src[self.tok_pos(rng[0]):self.tok_end(rng[1])].strip()

    @staticmethod
    def _paren(t: str) -> str:
        return t if re.fullmatch(r"[\w.]+(\s*[-+]\s*[\w.]+)*", t) else f"({t})"

    @staticmethod
    def _plus1(t: str) -> str:
        m = re.fullmatch(r"(.*\S)\s*-\s*1", t)
        if m and re.fullmatch(r"[\w.]+(\s*[-+]\s*[\w.]+)*", m.group(1)):
            return m.group(1)
        return f"{Optimizer._paren(t)} + 1"

    def _ids_unmodified(self, rng, a: int, b: int) -> bool:
        return not any(self._modified_in(x, a, b) for x in set(self.c.ids_in(*rng)))

    def _outer_insert_loop(self, k: int, arr: str):
        """Najbardziej zewnętrzna pętla nad pętlą k, w której tablica arr nie jest zmieniana (tam wstawimy budowę)."""
        c = self.c
        q = c.loops[k].parent
        if q is None:
            return None
        Q = c.loops[q]
        if self._modified_in(arr, Q.idx, Q.body[1]):
            return None
        while Q.parent is not None:
            P = c.loops[Q.parent]
            if self._modified_in(arr, P.idx, P.body[1]):
                break
            q = Q.parent
            Q = P
        return q

    def _replace_stmt(self, a: int, b: int, text: str) -> tuple:
        return (self.tok_pos(a), self.tok_end(b), text)

    # ------------------------------------------------------------------ sumy prefiksowe
    def _range_sum(self, k: int):
        """for (i = LO; i <= HI; i++) ACC += A[i];  →  (ACC, A, LO, HI, incl, declared)."""
        c = self.c
        L = c.loops[k]
        fp = self._for_parts(L)
        if not fp:
            return None
        lo, hi, incl, declared = fp
        st = self._body_stmts(L)
        if len(st) != 1:
            return None
        txt = normalize_expr(c.text(*st[0]))
        v = re.escape(L.var)
        m = re.fullmatch(rf"(\w+)\+=(\w+)\[{v}\];?", txt) or re.fullmatch(rf"(\w+)=\1\+(\w+)\[{v}\];?", txt)
        if not m:
            m2 = re.fullmatch(rf"(\w+)=(\w+)\[{v}\]\+(\w+);?", txt)
            if not (m2 and m2.group(1) == m2.group(3)):
                return None
            acc, arr = m2.group(1), m2.group(2)
        else:
            acc, arr = m.group(1), m.group(2)
        if acc == arr or acc == L.var:
            return None
        return acc, arr, lo, hi, incl, declared

    def r_prefix_sums(self):
        c = self.c
        done = {}
        for k, L in enumerate(c.loops):
            if L.parent is None:
                continue
            m = self._range_sum(k)
            if not m:
                continue
            acc, arr, lo, hi, incl, declared = m
            d = c.decl_of(arr, L.idx)
            et = self._elem_type(d)
            if et is None or acc in c.ids_in(*lo) + c.ids_in(*hi):
                continue
            q = self._outer_insert_loop(k, arr)
            Q = c.loops[L.parent]
            before = self._loop_mult(k)
            qcnt = self._loop_mult(L.parent)
            nsz = self._size_cost(arr, L.idx)
            after = (qcnt + nsz) if qcnt is not None and nsz is not None else None
            gain = f"{self._o(before)} → {self._o(after)}" if before is not None and after is not None else \
                "O(długość przedziału) → O(1) na zapytanie"
            lo_s, hi_s = self._src_of(lo), self._src_of(hi)
            ok = q is not None and declared and self._ids_unmodified(lo, L.body[0], L.body[1]) and \
                self._ids_unmodified(hi, L.body[0], L.body[1])
            is_global_arr = d.scope == "global" or d.kind == "vector"
            nested_c_array = d.kind == "array" and q is not None and c.loops[q].parent is not None
            pdecl = c.decl_of(acc, L.idx)
            unsigned = "unsigned" in (c.resolve_type(et) + " " + c.resolve_type(pdecl.type if pdecl else ""))
            ptype = "unsigned long long" if unsigned else "long long"
            pname = done.get((arr, q)) or f"pref_{arr}"
            sketch = (f"```cpp\n// raz, przed pętlą zapytań:\nvector<{ptype}> {pname}(N + 1, 0);   "
                      f"// {pname}[i + 1] = {arr}[0] + … + {arr}[i]\n"
                      f"for (int i = 0; i < N; i++) {pname}[i + 1] = {pname}[i] + {arr}[i];\n"
                      f"// w zapytaniu zamiast pętli:\n{acc} += {pname}[{self._plus1(hi_s) if incl else hi_s}]"
                      f" - {pname}[{lo_s}];\n```")
            why = (f"Pętla w linii {L.line} sumuje {arr}[{lo_s}..{hi_s}] od nowa w każdym obrocie pętli z linii "
                   f"{Q.line} — to {self._o(before, 'O(n·q)')} operacji. Tablica {arr} się tam nie zmienia, więc "
                   f"wystarczy raz policzyć sumy prefiksowe i każdą sumę przedziału brać jako różnicę dwóch wyrazów.")
            if not ok or not is_global_arr or nested_c_array:
                reason = ("granice przedziału zmieniają się w pętli" if q is not None and declared else
                          "tablica jest zmieniana między zapytaniami" if q is None else
                          "zmienna pętli jest używana po pętli")
                if not is_global_arr:
                    reason = "to lokalna tablica C (niezainicjowane elementy) — przepisz ręcznie"
                elif nested_c_array:
                    reason = "budowa po całej tablicy C w każdym teście byłaby za droga — zbuduj tylko do n"
                if q is None:
                    md = (why + f"\n\n**Uwaga:** {arr} zmienia się między zapytaniami ({reason}) — sumy prefiksowe "
                                f"trzeba by przebudowywać. Jeśli zmiany to pojedyncze elementy, użyj drzewa "
                                f"Fenwicka (O(log n) na zmianę i na zapytanie).")
                else:
                    md = why + f"\n\nNie przepisuję automatycznie ({reason}). Szkic:\n\n" + sketch
                self.changes.append(Change("prefix_sums", L.line, why.split(" — ")[0] + ".", suggestion_md=md,
                                           safe=False, gain=gain, before=c.line_text(L.line).strip()[:120]))
                continue
            edits = []
            if pname == f"pref_{arr}" and (arr, q) not in done:
                pname = self._unique(f"pref_{arr}")
                done[(arr, q)] = pname
                j = self._unique("i_")
                size = f"{arr}.size()" if d.kind == "vector" else d.dims[0].strip()
                jt = "size_t" if d.kind == "vector" else "int"
                ind = self.indent_of_line(c.loops[q].line)
                build = (f"vector<{ptype}> {pname}({size} + 1, 0);  // sumy prefiksowe: {pname}[i + 1] = "
                         f"{arr}[0] + … + {arr}[i]\n{ind}for ({jt} {j} = 0; {j} < {size}; {j}++) "
                         f"{pname}[{j} + 1] = {pname}[{j}] + {arr}[{j}];")
                edits.append(self.insert_before_stmt(c.loops[q].idx, build))
            new = (f"if ({lo_s} <= {hi_s}) {acc} += {pname}[{self._plus1(hi_s)}] - {pname}[{lo_s}];" if incl else
                   f"if ({lo_s} < {hi_s}) {acc} += {pname}[{hi_s}] - {pname}[{lo_s}];")
            edits.append(self._replace_stmt(L.idx, L.body[1], new))
            ch = Change("prefix_sums", L.line, why, edits, gain=gain, lines=[L.line, c.loops[q].line],
                        before=self.src[self.tok_pos(L.idx):self.tok_end(L.body[1])].strip()[:160], after=new,
                        suggestion_md=why + "\n\nWarunek `if` zachowuje działanie dla pustego przedziału.")
            self.changes.append(ch)

    def r_prefix_sums_2d(self):
        c = self.c
        for k, L in enumerate(c.loops):
            if L.parent is None or len(L.children) != 1:
                continue
            I = c.loops[L.children[0]]
            if I.kind != "for" or not I.var or not L.var:
                continue
            st = self._body_stmts(I)
            if len(st) != 1:
                continue
            txt = normalize_expr(c.text(*st[0]))
            m = re.fullmatch(rf"(\w+)\+=(\w+)\[{re.escape(L.var)}\]\[{re.escape(I.var)}\];?", txt)
            if not m:
                continue
            acc, arr = m.group(1), m.group(2)
            if self._modified_in(arr, c.loops[L.parent].idx, c.loops[L.parent].body[1]):
                continue
            before = self._mult_cost(I.body[0])
            qcnt = self._loop_mult(L.parent)
            gain = f"{self._o(before)} → O(n·m + {qcnt.expr() if qcnt is not None else 'q'})"
            md = (f"Pętle w liniach {L.line}–{I.line} sumują prostokąt tablicy `{arr}` w każdym zapytaniu. "
                  f"Sumy prefiksowe 2D policzone raz dają każdy prostokąt w O(1):\n\n```cpp\n"
                  f"// P[i][j] = suma {arr}[1..i][1..j]  (indeksy od 1)\n"
                  f"static long long P[N + 1][M + 1];\n"
                  f"for (int i = 1; i <= n; i++)\n    for (int j = 1; j <= m; j++)\n"
                  f"        P[i][j] = {arr}[i][j] + P[i - 1][j] + P[i][j - 1] - P[i - 1][j - 1];\n"
                  f"// prostokąt (a, b) – (c, d):\n"
                  f"{acc} = P[c][d] - P[a - 1][d] - P[c][b - 1] + P[a - 1][b - 1];\n```")
            self.changes.append(Change("prefix_sums_2d", L.line, f"Suma prostokąta liczona dwiema pętlami w każdym "
                                                                 f"zapytaniu — {self._o(before)}.", suggestion_md=md,
                                       gain=gain))

    # ------------------------------------------------------------------ min/max na przedziale → sparse table
    def r_sparse_table(self):
        c = self.c
        for k, L in enumerate(c.loops):
            if L.parent is None:
                continue
            fp = self._for_parts(L)
            if not fp:
                continue
            lo, hi, incl, _ = fp
            st = self._body_stmts(L)
            if len(st) != 1:
                continue
            txt = normalize_expr(c.text(*st[0]))
            v = re.escape(L.var)
            m = re.fullmatch(rf"(\w+)=(max|min)\(\1,(\w+)\[{v}\]\);?", txt) or \
                re.fullmatch(rf"(\w+)=(max|min)\((\w+)\[{v}\],\1\);?", txt)
            if m:
                acc, op, arr = m.group(1), m.group(2), m.group(3)
            else:
                m = re.fullmatch(rf"if\((\w+)\[{v}\]([<>])(\w+)\)(\w+)=\1\[{v}\];?", txt)
                if not (m and m.group(3) == m.group(4)):
                    continue
                acc, arr, op = m.group(3), m.group(1), ("max" if m.group(2) == ">" else "min")
            if self._modified_in(arr, c.loops[L.parent].idx, c.loops[L.parent].body[1]):
                continue
            d = c.decl_of(arr, L.idx)
            et = (d.elem or d.type) if d is not None and d.kind in ("array", "vector") else "int"
            et = c.resolve_type(et) if et else "int"
            before = self._loop_mult(k)
            qcnt = self._loop_mult(L.parent)
            nsz = self._size_cost(arr, L.idx)
            gain = "O(n·q) → O(n log n + q)"
            try:
                from oiforge.tools._cppstruct import Cost
                if before is not None and qcnt is not None and nsz is not None:
                    gain = f"{self._o(before)} → {self._o(qcnt + nsz * Cost.log(nsz.dominant() and list(nsz.dominant().f)[0] or 'n'))}"
            except Exception:  # noqa: BLE001
                pass
            lo_s, hi_s = self._src_of(lo), self._src_of(hi)
            r_s = hi_s if incl else f"{self._paren(hi_s)} - 1"
            md = (f"Pętla w linii {L.line} szuka {op}imum {arr}[{lo_s}..{hi_s}] w każdym zapytaniu. Tablica się nie "
                  f"zmienia, więc **sparse table** (tablica rzadka) odpowiada w O(1): `sp[j][i]` = {op} z "
                  f"przedziału [i, i + 2^j). Dowolny przedział pokrywają dwa (zachodzące na siebie) kawałki o "
                  f"długości 2^k.\n\n```cpp\n// raz, przed pętlą zapytań (N = rozmiar {arr}):\n"
                  f"int LOG = 1; while ((1 << LOG) <= N) LOG++;\n"
                  f"vector<vector<{et}>> sp(LOG, vector<{et}>(N));\n"
                  f"for (int i = 0; i < N; i++) sp[0][i] = {arr}[i];\n"
                  f"for (int j = 1; j < LOG; j++)\n"
                  f"    for (int i = 0; i + (1 << j) <= N; i++)\n"
                  f"        sp[j][i] = {op}(sp[j - 1][i], sp[j - 1][i + (1 << (j - 1))]);\n"
                  f"auto query = [&](int l, int r) {{   // {op} z [l, r], l <= r\n"
                  f"    int k = __lg(r - l + 1);\n"
                  f"    return {op}(sp[k][l], sp[k][r - (1 << k) + 1]);\n}};\n"
                  f"// w zapytaniu zamiast pętli:\n{acc} = {op}({acc}, query({lo_s}, {r_s}));\n```\n"
                  f"Jeśli {arr} zmienia się między zapytaniami — użyj drzewa przedziałowego (O(log n)).")
            self.changes.append(Change("sparse_table", L.line, f"{op.capitalize()} na przedziale liczone pętlą w każdym "
                                                               f"zapytaniu ({self._o(before)}).", suggestion_md=md,
                                       gain=gain, lines=[L.line, c.loops[L.parent].line]))

    # ------------------------------------------------------------------ liniowe szukanie w pętli
    def r_linear_search(self):
        c = self.c
        seen = set()
        for k, L in enumerate(c.loops):
            if L.parent is None or L.kind not in ("for", "range"):
                continue
            body = normalize_expr(c.text(*L.body))
            v = re.escape(L.var or "#")
            arr = None
            if L.kind == "for":
                m = re.search(rf"if\((\w+)\[{v}\]==(\w+)\)|if\((\w+)==(\w+)\[{v}\]\)", body)
                if m:
                    arr, x = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            else:
                m = re.search(rf"if\({v}==(\w+)\)|if\((\w+)=={v}\)", body)
                ids = c.ids_in(*L.range_expr)
                if m and ids:
                    arr, x = ids[0], (m.group(1) or m.group(2))
            if not arr or x == L.var or arr in seen:
                continue
            P = c.loops[L.parent]
            if self._modified_in(arr, P.idx, P.body[1]) or L.var in (x,):
                continue
            seen.add(arr)
            before = self._loop_mult(k)
            qcnt = self._loop_mult(L.parent)
            nsz = self._size_cost(arr, L.idx)
            gain = "O(n·q) → O(n + q)"
            if before is not None and qcnt is not None and nsz is not None:
                gain = f"{self._o(before)} → {self._o(qcnt + nsz)} (zbiór haszujący) lub O(n log n + q log n)"
            self.changes.append(self._search_change(L.line, arr, x, gain, f"Pętla w linii {L.line} przegląda całe "
                                                                          f"{arr}, żeby znaleźć {x} — w każdym obrocie "
                                                                          f"pętli z linii {P.line}.", "find"))
        for cl in c.calls:
            if cl.name not in ("find", "count") or cl.is_method or len(cl.args) != 3 or cl.name in c.func_by_name:
                continue
            k = c.innermost_loop(cl.idx)
            if k is None:
                continue
            t0 = normalize_expr(c.text(*cl.args[0]))
            t1 = normalize_expr(c.text(*cl.args[1]))
            m0 = re.fullmatch(r"(\w+)\.begin\(\)", t0)
            m1 = re.fullmatch(r"(\w+)\.end\(\)", t1)
            if not (m0 and m1 and m0.group(1) == m1.group(1)):
                continue
            arr = m0.group(1)
            d = c.decl_of(arr, cl.idx)
            if d is None or d.kind not in ("vector", "deque", "string") or arr in seen:
                continue
            L = c.loops[k]
            if self._modified_in(arr, L.idx, L.body[1]):
                continue
            seen.add(arr)
            x = self.src[self.tok_pos(cl.args[2][0]):self.tok_end(cl.args[2][1])]
            before = self._mult_cost(cl.idx)
            nsz = self._size_cost(arr, cl.idx)
            qcnt = self._loop_mult(k)
            gain = "O(n·q) → O(n + q)"
            if before is not None and nsz is not None and qcnt is not None:
                gain = f"{self._o(before * nsz)} → {self._o(qcnt + nsz)}"
            self.changes.append(self._search_change(cl.line, arr, x, gain, f"std::{cl.name}({arr}.begin(), {arr}.end(), "
                                                                           f"{x}) w pętli przegląda cały {arr} — O(n) "
                                                                           f"za każdym razem.", cl.name))

    def _search_change(self, line: int, arr: str, x: str, gain: str, why: str, what: str):
        cnt = what == "count"
        md = (why + " Tablica się w tej pętli nie zmienia, więc przygotuj ją raz:\n\n"
              + ("**Liczenie wystąpień** — mapa licznika (O(1) średnio na zapytanie):\n\n```cpp\n"
                 f"unordered_map<long long, int> ile;   // raz, przed pętlą\nfor (auto v : {arr}) ile[v]++;\n"
                 f"// w pętli:\nint wynik = ile.count({x}) ? ile[{x}] : 0;\n```\n"
                 "albo posortuj i licz przez `equal_range` (O(log n)):\n\n```cpp\n"
                 f"vector<long long> s({arr}.begin(), {arr}.end()); sort(s.begin(), s.end());   // raz\n"
                 f"auto [lo, hi] = equal_range(s.begin(), s.end(), {x});\nint wynik = hi - lo;\n```"
                 if cnt else
                 "**Czy element występuje?** — zbiór haszujący (O(1) średnio):\n\n```cpp\n"
                 f"unordered_set<long long> jest({arr}.begin(), {arr}.end());   // raz, przed pętlą\n"
                 f"// w pętli:\nbool found = jest.count({x});\n```\n"
                 "albo posortowana kopia i wyszukiwanie binarne (O(log n), bez ryzyka testów anty-hash):\n\n```cpp\n"
                 f"vector<long long> s({arr}.begin(), {arr}.end()); sort(s.begin(), s.end());   // raz\n"
                 f"bool found = binary_search(s.begin(), s.end(), {x});\n```\n"
                 "Jeśli potrzebujesz POZYCJI elementu, trzymaj `unordered_map<wartość, indeks>`."))
        return Change("linear_search", line, why, suggestion_md=md, gain=gain)

    # ------------------------------------------------------------------ pary o sumie k
    def r_pair_sum(self):
        c = self.c
        for k, L in enumerate(c.loops):
            if len(L.children) != 1 or not L.var:
                continue
            I = c.loops[L.children[0]]
            if I.kind != "for" or not I.var or not I.init:
                continue
            init = normalize_expr(c.text(*I.init))
            if not re.search(rf"={re.escape(L.var)}\+1$|={re.escape(L.var)}$|=0$", init):
                continue
            body = normalize_expr(c.text(*I.body))
            a_, b_ = re.escape(L.var), re.escape(I.var)
            m = re.search(rf"if\((\w+)\[{a_}\]\+(\w+)\[{b_}\]==(\w+)\)", body) or \
                re.search(rf"if\((\w+)\[{b_}\]\+(\w+)\[{a_}\]==(\w+)\)", body)
            if not m or m.group(1) != m.group(2):
                continue
            arr, K = m.group(1), m.group(3)
            before = self._mult_cost(I.body[0])
            nsz = self._size_cost(arr, L.idx)
            gain = f"{self._o(before, 'O(n²)')} → {self._o(nsz, 'O(n)')}"
            md = (f"Dwie pętle (linie {L.line}–{I.line}) sprawdzają wszystkie pary — {self._o(before, 'O(n²)')}. "
                  f"Dla każdego elementu wystarczy wiedzieć, ile razy wcześniej wystąpiło `{K} - x`:\n\n```cpp\n"
                  f"unordered_map<long long, long long> ile;\nlong long par = 0;\n"
                  f"for (int i = 0; i < n; i++) {{\n    par += ile[{K} - {arr}[i]];   // pary (j, i), j < i\n"
                  f"    ile[{arr}[i]]++;\n}}\n```\nAlbo posortuj i użyj dwóch wskaźników (O(n log n)).")
            self.changes.append(Change("pair_sum", L.line, f"Sprawdzanie wszystkich par w poszukiwaniu sumy {K}.",
                                       suggestion_md=md, gain=gain, lines=[L.line, I.line]))

    # ------------------------------------------------------------------ erase(begin()) / insert(begin()) → deque
    def r_erase_begin(self):
        c = self.c
        cands = {}
        for cl in c.calls:
            if cl.name not in ("erase", "insert") or not cl.receiver or cl.receiver_text != cl.receiver or not cl.args:
                continue
            if c.innermost_loop(cl.idx) is None:
                continue
            d = c.decl_of(cl.receiver, cl.idx)
            t = normalize_expr(c.text(*cl.args[0]))
            if d is None or d.kind not in ("vector", "string") or \
                    not re.fullmatch(rf"{re.escape(cl.receiver)}\.begin\(\)", t):
                continue
            if cl.name == "erase" and len(cl.args) != 1 or cl.name == "insert" and len(cl.args) != 2:
                continue
            cands.setdefault(cl.receiver, []).append(cl)
        for v, calls in cands.items():
            d = c.decl_of(v, calls[0].idx)
            before = self._mult_cost(calls[0].idx)
            vs = self._size_cost(v, calls[0].idx)
            gain = f"{self._o(before * vs if before is not None and vs is not None else None, 'O(n²)')} → " \
                   f"{self._o(before, 'O(n)')}"
            what = f"{v}.erase({v}.begin())" if calls[0].name == "erase" else f"{v}.insert({v}.begin(), …)"
            why = (f"{what} w pętli przesuwa wszystkie elementy wektora — O(n) za każdym razem. deque robi to samo "
                   f"w O(1) (pop_front / push_front) i ma te same metody co vector.")
            edits = self._deque_edits(v, d, calls) if d.kind == "vector" else None
            if edits:
                self._deque_vars.add(v)
            md = (why + f"\n\n```cpp\ndeque<…> {v};\n{v}.pop_front();      // zamiast {v}.erase({v}.begin())\n"
                        f"{v}.push_front(x);   // zamiast {v}.insert({v}.begin(), x)\n```\n"
                        f"Inna opcja: zamiast usuwać, trzymaj indeks początku (`int head = 0; … {v}[head++]`).")
            if edits:
                self.changes.append(Change("erase_begin", calls[0].line, why, edits, suggestion_md=md, gain=gain,
                                           lines=sorted({d.line} | {cl.line for cl in calls}),
                                           before=f"vector … {v}; {v}.{calls[0].name}({v}.begin(){', x' if calls[0].name == 'insert' else ''})",
                                           after=f"deque … {v}; {v}.{'pop_front()' if calls[0].name == 'erase' else 'push_front(x)'}"))
            else:
                self.changes.append(Change("erase_begin", calls[0].line, why, suggestion_md=md, gain=gain, safe=False))

    def _deque_edits(self, v: str, d, calls) -> list | None:
        """Zamiana vector → deque, gdy wektor jest używany tylko metodami, które deque też ma."""
        c = self.c
        if d is None or d.is_param:
            return None
        if d.nested != 1 and not (d.nested == 2 and c.classify_type(d.elem)[0] == "string"):
            return None
        ok_methods = {"push_back", "emplace_back", "pop_back", "front", "back", "size", "empty", "clear", "begin",
                      "end", "rbegin", "rend", "erase", "insert", "resize", "assign", "at", "swap"}
        for i, t in enumerate(c.texts):
            if t != v or c.toks[i].kind != "id" or i == d.idx:
                continue
            if i > 0 and c.texts[i - 1] in (".", "->", "::"):
                continue
            nxt = c.texts[i + 1] if i + 1 < c.n else ""
            if nxt in (".", "->"):
                meth = c.texts[i + 2] if i + 2 < c.n else ""
                if meth not in ok_methods:
                    return None
                continue
            if nxt == "[":
                continue
            prev = c.texts[i - 1] if i > 0 else ""
            if prev == ":" and c.innermost_loop(i) is not None:      # for (auto x : v)
                continue
            if prev in ("<<",):                                       # cout << v?  — nie ma takiego operatora
                return None
            return None                                               # przekazanie do funkcji, przypisanie itp.
        edits = []
        for cl in calls:
            res_used = c.texts[cl.idx - 3] if cl.idx >= 3 else ""
            if res_used in ("=", "return", "(", ","):
                return None
            if cl.name == "erase":
                edits.append((self.tok_pos(cl.idx), self.tok_end(cl.close_idx), "pop_front()"))
            else:
                x = self.src[self.tok_pos(cl.args[1][0]):self.tok_end(cl.args[1][1])]
                edits.append((self.tok_pos(cl.idx), self.tok_end(cl.close_idx), f"push_front({x})"))
        j = d.idx - 1
        while j >= 0 and c.texts[j] != "vector":
            j -= 1
        if j < 0 or d.idx - j > 12:
            return None
        edits.append((self.tok_pos(j), self.tok_end(j), "deque"))
        if not re.search(r"#\s*include\s*<(bits/stdc\+\+\.h|deque)>", self.src):
            edits.append((0, 0, "#include <deque>\n"))
        return edits

    # ------------------------------------------------------------------ spamiętywanie
    def r_memoize(self):
        c = self.c
        an = self._an()
        for f in c.functions:
            if len(f.self_calls) < 2 or f.name == "main" or not f.params or len(f.params) > 3:
                continue
            fi = an.func_info.get(f.name) if an else None
            rtype = (fi.recursion or {}).get("type") if fi and fi.recursion else None
            if rtype not in ("exponential", "backtracking"):
                continue
            body = normalize_expr(c.text(f.open_idx, f.close_idx))
            ret = c.resolve_type(f.ret or "").strip()
            if not ret or ret == "void" or not self._is_integral(ret) and ret not in ("double", "long double", "bool"):
                continue
            if any(p.is_ref or p.is_ptr or p.kind != "scalar" for p in f.params):
                continue
            if any(io.func == f.name for io in c.io) or re.search(r"\bstatic\b|rand\(|cin|cout|printf|scanf", body):
                continue
            # czysta funkcja: przypisania tylko do lokalnych zmiennych i parametrów
            local = {p.name for p in f.params} | {d.name for d in c.decls if d.scope == f.name}
            if any(a.target not in local for a in c.assigns if f.open_idx < a.idx < f.close_idx):
                continue
            if any(cl.name in c.func_by_name and cl.name != f.name for cl in f.calls):
                continue
            rets = [i for i in range(f.open_idx, f.close_idx) if c.texts[i] == "return"]
            if not rets:
                continue
            memo = self._unique(f"memo_{f.name}")
            key = self._unique("key_")
            it = self._unique("it_")
            ptypes = [re.sub(r"\bconst\b", "", self.src[self.tok_pos(self._param_type_start(f, p)):self.tok_pos(p.idx)]).strip()
                      for p in f.params]
            if len(f.params) == 1:
                ktype, kexpr = ptypes[0], f.params[0].name
            else:
                ktype = "tuple<" + ", ".join(ptypes) + ">"
                kexpr = "make_tuple(" + ", ".join(p.name for p in f.params) + ")"
            rtext = (f.ret or ret).strip()
            edits = []
            ls = self.line_start_pos(f.line)
            edits.append((ls, ls, f"map<{ktype}, {rtext}> {memo};   // spamiętane wyniki {f.name}()\n"))
            ind = self.indent_of_line(f.line + 1) if f.line + 1 <= len(self.lines) and self.lines[f.line].strip() \
                else "    "
            if not ind.strip() == "" or ind == "":
                ind = "    "
            open_end = self.tok_end(f.open_idx)
            edits.append((open_end, open_end, f"\n{ind}auto {key} = {kexpr};\n{ind}auto {it} = {memo}.find({key});\n"
                                              f"{ind}if ({it} != {memo}.end()) return {it}->second;"))
            for r in rets:
                e = c.stmt_end(r)
                if c.texts[e] != ";" or e <= r + 1:
                    edits = None
                    break
                expr = self.src[self.tok_pos(r + 1):self.tok_end(e - 1)]
                edits.append((self.tok_pos(r + 1), self.tok_end(e - 1), f"{memo}[{key}] = ({expr})"))
            if not edits:
                continue
            before = fi.per_call.expr().replace("§N", "n").replace("§", "") if fi else "wykładniczo"
            gain = f"O({before}) → O(liczba różnych argumentów)"
            if len(f.params) == 1:
                gain = f"O({before}) → O(n log n)"
            why = (f"{f.name}() woła samą siebie {len(f.self_calls)} razy i te same argumenty liczy wielokrotnie "
                   f"(drzewo wywołań rośnie wykładniczo: {before}). Funkcja nie ma efektów ubocznych, więc wynik dla "
                   f"tych samych argumentów można zapamiętać.")
            md = why + (f"\n\nPrzepisanie dodaje mapę `{memo}` (klucz = argumenty) i zapisuje wynik przy każdym "
                        f"`return`. Szybciej działa zwykła tablica `memo[N]` z wartością -1 jako „nie policzone”, "
                        f"jeśli znasz zakres argumentów.")
            self.changes.append(Change("memoize", f.line, why, edits, suggestion_md=md, gain=gain,
                                       before=f"{rtext} {f.name}(…) {{ … return …; }}",
                                       after=f"if ({memo} ma wynik) return; … return {memo}[{key}] = …;"))

    def _param_type_start(self, f, p) -> int:
        c = self.c
        j = p.idx - 1
        while j > 0 and c.texts[j] not in ("(", ","):
            j -= 1
        return j + 1

    # ------------------------------------------------------------------ sort w pętli
    def r_hoist_sort(self):
        c = self.c
        for cl in c.calls:
            if cl.name not in ("sort", "stable_sort") or cl.is_method or len(cl.args) not in (2, 3):
                continue
            k = c.innermost_loop(cl.idx)
            if k is None:
                continue
            t0 = normalize_expr(c.text(*cl.args[0]))
            t1 = normalize_expr(c.text(*cl.args[1]))
            m0 = re.fullmatch(r"(\w+)\.begin\(\)", t0)
            m1 = re.fullmatch(r"(\w+)\.end\(\)", t1)
            if m0 and m1 and m0.group(1) == m1.group(1):
                v = m0.group(1)
            else:
                m0 = re.fullmatch(r"(\w+)(\+\w+)?", t0)
                m1 = re.fullmatch(r"(\w+)\+(\w+)", t1)
                if not (m0 and m1 and m0.group(1) == m1.group(1)):
                    continue
                v = m0.group(1)
                if self._modified_in(m1.group(2), c.loops[k].idx, c.loops[k].body[1]):
                    continue
            L = c.loops[k]
            st = self._body_stmts(L)
            # sort musi być bezpośrednią instrukcją ciała pętli (nie w if) i pierwszym użyciem v w obrocie
            sidx = next((a for a, b in st if a <= cl.idx <= b), None)
            if sidx is None or not (c.texts[sidx] == "sort" or (c.texts[sidx] == "std" and c.texts[sidx + 2] == "sort")):
                continue
            if any(v in c.ids_in(a, b) for a, b in st if b < sidx):
                continue
            # poza sortowaniem v nie może się zmieniać w pętli
            if any(a.target == v for a in c.assigns_in(L.body[0], L.body[1])):
                continue
            if any(x.receiver == v and x.name in MUTATING for x in c.calls if L.body[0] <= x.idx <= L.body[1]):
                continue
            if any(x is not cl and not x.is_method and x.name in FREE_MUTATING and x.args and
                   v in c.ids_in(*x.args[0]) for x in c.calls if L.body[0] <= x.idx <= L.body[1]):
                continue
            if re.search(rf">>{re.escape(v)}\b", normalize_expr(c.text(*L.body))):
                continue
            # po pętli v nie może być użyte (pętla mogłaby nie wykonać się ani razu)
            f = c.enclosing_func(cl.idx)
            end = f.close_idx if f else c.n - 1
            if any(c.texts[i] == v for i in range(L.body[1] + 1, end)):
                continue
            d = c.decl_of(v, cl.idx)
            if d is not None and d.scope == "global" and any(
                    g.name != (f.name if f else None) and v in c.texts[g.open_idx:g.close_idx] for g in c.functions):
                continue
            e = c.stmt_end(sidx)
            stmt = self.src[self.tok_pos(sidx):self.tok_end(e)]
            ls = self.line_start_pos(c.toks[sidx].line)
            line_txt = self.lines[c.toks[sidx].line - 1]
            if line_txt.strip() == stmt.strip():
                rem = (ls, ls + len(line_txt) + 1, "")
            else:
                rem = (self.tok_pos(sidx), self.tok_end(e), "")
            edits = [rem, self.insert_before_stmt(L.idx, stmt.strip())]
            before = self._mult_cost(cl.idx)
            vs = self._size_cost(v, cl.idx)
            gain = "O(q·n log n) → O(n log n)"
            try:
                from oiforge.tools._cppstruct import Cost
                if before is not None and vs is not None:
                    lg = Cost.log(list(vs.dominant().f)[0]) if vs.dominant() and vs.dominant().f else Cost.const(1)
                    gain = f"{self._o(before * vs * lg)} → {self._o(vs * lg)} (samo sortowanie)"
            except Exception:  # noqa: BLE001
                pass
            why = (f"sort({v}) w linii {cl.line} wykonuje się w każdym obrocie pętli z linii {L.line}, a {v} się w niej "
                   f"nie zmienia — wystarczy posortować raz, przed pętlą.")
            self.changes.append(Change("hoist_sort", cl.line, why, edits, gain=gain, lines=[cl.line, L.line],
                                       before=stmt.strip(), after=f"{stmt.strip()}  // przed pętlą (linia {L.line})"))

    # ------------------------------------------------------------------ pow na liczbach całkowitych
    def r_int_pow(self):
        c = self.c
        for cl in c.calls:
            if cl.name not in ("pow", "powl") or cl.is_method or len(cl.args) != 2:
                continue
            ex = normalize_expr(c.text(*cl.args[1]))
            if ex in ("2", "3", "2.0", "3.0", "0.5"):
                continue                        # pow(x, 2) obsługuje reguła pow_square
            ids = c.ids_in(*cl.args[0]) + c.ids_in(*cl.args[1])
            decls = [c.decl_of(x, cl.idx) for x in ids]
            if any(d is not None and re.search(r"double|float", d.type or "") for d in decls):
                continue
            base = normalize_expr(c.text(*cl.args[0]))
            ctx = normalize_expr(c.text(max(0, cl.idx - 6), min(c.n - 1, cl.close_idx + 3)))
            integer_ctx = bool(re.search(r"\((long ?long|int|ll)\)pow|pow\([^;]*\)%|=(int|longlong|ll)\(?pow",
                                         ctx.replace("longlong", "long long"))) or \
                any(d is not None and d.kind == "scalar" and self._is_integral(d.type) for d in decls)
            if not integer_ctx:
                continue
            edits = []
            start = self.tok_pos(cl.idx - 2) if cl.std and cl.idx >= 2 else self.tok_pos(cl.idx)
            end = self.tok_end(cl.close_idx)
            e_src = self.src[self.tok_pos(cl.args[1][0]):self.tok_end(cl.args[1][1])]
            if base == "2":
                edits = [(start, end, f"(1LL << ({e_src}))")]
            mod_used = re.search(r"%\s*(\w+)", self.src[end:end + 40])
            mod = mod_used.group(1) if mod_used else "MOD"
            md = (f"`pow` liczy na liczbach zmiennoprzecinkowych (double): dla wyników > 2^53 gubi cyfry, a modulo "
                  f"po fakcie nie ma sensu (wynik już się przepełnił). Szybkie potęgowanie liczy dokładnie i w "
                  f"O(log b):\n\n```cpp\nlong long power(long long a, long long b, long long mod) {{\n"
                  f"    long long r = 1 % mod;\n    a %= mod;\n    while (b > 0) {{\n        if (b & 1) r = r * a % mod;\n"
                  f"        a = a * a % mod;\n        b >>= 1;\n    }}\n    return r;\n}}\n// zamiast pow(…) % {mod}:\n"
                  f"long long r = power({self.src[self.tok_pos(cl.args[0][0]):self.tok_end(cl.args[0][1])]}, {e_src}, "
                  f"{mod});\n```" + ("\n\nDla potęgi dwójki: `1LL << k` (dokładnie, bez double)." if base == "2" else ""))
            self.changes.append(Change("int_pow", cl.line, f"pow() na liczbach całkowitych w linii {cl.line} — "
                                                           f"wynik w double, możliwa utrata dokładności.", edits,
                                       suggestion_md=md, safe=False, gain="dokładny wynik, O(log b)",
                                       before=self.src[start:end], after=edits[0][2] if edits else
                                       f"power(…, {e_src}, {mod})"))

    # ------------------------------------------------------------------ mapa o małych kluczach
    def r_small_map(self):
        c = self.c
        for d in c.decls:
            if d.kind not in ("map", "umap") or d.is_param:
                continue
            key = (d.elem.split(",")[0] if d.elem else "").strip()
            kt = c.resolve_type(key).strip()
            if kt not in ("char", "bool", "unsigned char", "signed char", "short"):
                continue
            val = d.elem.split(",", 1)[1].strip() if "," in d.elem else "int"
            size = {"char": 256, "unsigned char": 256, "signed char": 256, "bool": 2, "short": 65536}[kt]
            md = (f"`{d.name}` ma klucze typu `{kt}` — jest ich najwyżej {size}. Zwykła tablica jest szybsza (O(1), "
                  f"bez alokacji węzłów drzewa):\n\n```cpp\n{val} {d.name}[{size}] = {{}};   // zamiast {d.type}\n"
                  f"{d.name}[(unsigned char)c]++;\n```\n" + ("Dla samych małych liter: `int cnt[26]` i `cnt[c - 'a']`."
                                                             if kt == "char" else ""))
            self.changes.append(Change("small_map", d.line, f"Mapa {d.name} z kluczami {kt} — wystarczy tablica.",
                                       suggestion_md=md))

    # ------------------------------------------------------------------ całość
    def analyze(self) -> list[Change]:
        for fn in (self.r_prefix_sums, self.r_prefix_sums_2d, self.r_sparse_table, self.r_linear_search,
                   self.r_pair_sum, self.r_memoize, self.r_hoist_sort, self.r_erase_begin, self.r_int_pow,
                   self.r_small_map,
                   self.r_fast_io, self.r_endl, self.r_const_ref, self.r_pow, self.r_string_concat, self.r_reserve,
                   self.r_set_lower_bound, self.r_range_for, self.r_strlen, self.r_sqrt_cond, self.r_unordered,
                   self.r_long_long, self.r_map_access, self.r_copy_in_loop, self.r_memset_loop,
                   self.r_iterative_dfs, self.r_flat_2d, self.r_pragma):
            try:
                fn()
            except Exception:  # noqa: BLE001 — jedna reguła nie może zepsuć całości
                pass
        self._tune_impact()
        self.changes.sort(key=lambda ch: (-ch.impact, ch.line))
        self._finish()
        return self.changes

    def _tune_impact(self):
        """Wpływ zależny od kontekstu: endl/s = s + x w pętli, kopia w funkcji rekurencyjnej, …"""
        c = self.c
        for ch in self.changes:
            in_loop = any(c.innermost_loop(i) is not None for i, t in enumerate(c.toks) if t.line in ch.lines)
            if ch.rule == "endl":
                ch.impact = 55 if in_loop else 20
            elif ch.rule == "string_concat":
                ch.impact = 87 if in_loop else 25
            elif ch.rule == "set_lower_bound":
                ch.impact = 91 if in_loop else 40
            elif ch.rule in ("const_ref", "pass_by_ref"):
                f = next((g for g in c.functions if g.line <= ch.line <= g.end_line), None)
                if f is not None and f.self_calls:
                    ch.impact = 84 if ch.rule == "const_ref" else 60
                    ch.category = "algorytm"
                    if not ch.gain:
                        ch.gain = "kopia O(n) przy każdym wywołaniu → O(1)"
            elif ch.rule == "fast_io" and not ch.safe:
                ch.impact = 30

    def _finish(self):
        """Zysk złożoności dla przepisań algorytmicznych (analiza kodu przed/po) i diff każdej zmiany osobno."""
        before = None
        done = 0
        for ch in self.changes:
            if not ch.edits:
                continue
            out = self.src
            for s_, e_, txt in sorted(ch.edits, key=lambda e: (e[0], e[1]), reverse=True):
                out = out[:s_] + txt + out[e_:]
            rows = diff_rows(self.src, out)
            show = [False] * len(rows)
            for i, r in enumerate(rows):
                if r["type"] != "eq":
                    for j in range(max(0, i - 1), min(len(rows), i + 2)):
                        show[j] = True
            ch.patch = [r for i, r in enumerate(rows) if show[i]][:40]
            if ch.category == "algorytm" and ch.rule in ("prefix_sums", "memoize", "hoist_sort", "erase_begin",
                                                         "set_lower_bound", "string_concat") and done < 6:
                done += 1
                try:
                    from oiforge.tools import complexity as cxm
                    if before is None:
                        before = cxm.analyze(self.src)["complexity"]
                    after = cxm.analyze(out)["complexity"]
                    if after != before:
                        ch.gain = f"{before} → {after}"
                        ch.complexity = {"before": before, "after": after}
                except Exception:  # noqa: BLE001
                    pass

    def apply(self, rule_ids: list | None) -> str:
        chosen = []
        for ch in self.changes:
            if not ch.edits:
                continue
            if rule_ids is None:
                ok = ch.safe
            else:
                ok = ch.rule in rule_ids or f"{ch.rule}:{ch.line}" in rule_ids
            if ok:
                chosen.append(ch)
        edits = []
        for ch in chosen:
            for e in ch.edits:
                edits.append((e[0], e[1], e[2], ch))
        edits.sort(key=lambda e: (e[0], e[1]))
        # odrzuć nakładające się edycje (zachowaj wcześniejszą)
        accepted = []
        last_end = -1
        for e in edits:
            if e[0] < last_end or (accepted and e[0] == accepted[-1][0] and e[1] > e[0] and accepted[-1][1] > accepted[-1][0]):
                continue
            accepted.append(e)
            last_end = max(last_end, e[1])
        out = self.src
        for s, e, txt, ch in sorted(accepted, key=lambda e: (e[0], e[1]), reverse=True):
            out = out[:s] + txt + out[e:]
            ch.applied = True
        return out


def diff_rows(a: str, b: str) -> list[dict]:
    al, bl = a.split("\n"), b.split("\n")
    rows = []
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"type": "eq", "a": i1 + k + 1, "b": j1 + k + 1, "a_text": al[i1 + k], "b_text": bl[j1 + k]})
        elif tag == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                ra = i1 + k if i1 + k < i2 else None
                rb = j1 + k if j1 + k < j2 else None
                rows.append({"type": "chg" if ra is not None and rb is not None else ("del" if ra is not None else "add"),
                             "a": ra + 1 if ra is not None else None, "b": rb + 1 if rb is not None else None,
                             "a_text": al[ra] if ra is not None else "", "b_text": bl[rb] if rb is not None else ""})
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append({"type": "del", "a": k + 1, "b": None, "a_text": al[k], "b_text": ""})
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append({"type": "add", "a": None, "b": k + 1, "a_text": "", "b_text": bl[k]})
    return rows


def optimize(code: str, apply: list | None = None) -> dict:
    opt = Optimizer(code)
    changes = opt.analyze()
    new_code = opt.apply(apply)
    diff = "".join(difflib.unified_diff(opt.src.splitlines(True), new_code.splitlines(True), "oryginał.cpp",
                                        "zoptymalizowany.cpp", n=2))
    return {"optimized_code": new_code, "changes": [ch.to_dict() for ch in changes], "diff": diff,
            "diff_rows": diff_rows(opt.src, new_code), "applied": sorted({ch.rule for ch in changes if ch.applied}),
            "interactive": opt.interactive, "mixed_io": opt.mixed_io, "changed": new_code != opt.src}


def handle_optimize(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod do optymalizacji.")
    if len(code) > 300_000:
        raise ApiError("Kod jest za długi (limit 300 kB).")
    apply = body.get("apply")
    if apply is not None and not isinstance(apply, list):
        raise ApiError("Pole apply musi być listą identyfikatorów reguł.")
    res = optimize(code, apply)
    if body.get("verify") and runner is not None and res["changed"]:
        cr = runner.compile_cpp(res["optimized_code"], mode="syntax")
        res["compile"] = {"ok": cr.ok, "stderr": cr.stderr[-4000:], "errors": cr.errors, "warnings": cr.warnings}
    return res


def handle_rules(_body: dict) -> dict:
    return {"rules": [{"id": k, "title": v[0], "safe": v[1], "speedup_hint": v[2]} for k, v in RULES.items()]}


# ============================================================================================== benchmark
def _bench_work(job, body: dict):
    if runner is None:
        raise RuntimeError("Brak modułu runner.")
    orig = body.get("original") or ""
    opt = body.get("optimized") or ""
    repeats = max(1, min(int(body.get("repeats") or 3), 10))
    timeout = float(body.get("timeout") or 10.0)
    inp = body.get("input")
    src = "user"
    if not inp:
        try:
            from oiforge.tools import complexity as cx
            import random
            tpl = body.get("input_template") or cx.infer_template(orig) or "{n}\n{arr:n:1:1000000000}"
            size = int(cx._to_number(body.get("size")) or 200000)
            inp = cx._gen_input(tpl, size, random.Random(int(body.get("seed") or 7)))
            src = "generated"
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Nie udało się wygenerować wejścia: {e}"}
    job.progress(0.05, "Kompilacja oryginału…")
    pa = runner.prepare(orig, mode="release")
    if not pa.ok:
        return {"ok": False, "error": "Oryginał się nie kompiluje.", "compile": pa.compile.to_dict() if pa.compile else None}
    job.progress(0.2, "Kompilacja wersji zoptymalizowanej…")
    pb = runner.prepare(opt, mode="release")
    if not pb.ok:
        return {"ok": False, "error": "Wersja zoptymalizowana się nie kompiluje.",
                "compile": pb.compile.to_dict() if pb.compile else None}
    ta, tb = [], []
    ra = rb = None
    for r in range(repeats):
        if job.cancelled:
            break
        job.progress(0.3 + 0.65 * r / repeats, f"Przebieg {r + 1}/{repeats}…")
        ra = runner.run(pa.cmd, inp, timeout=timeout)
        rb = runner.run(pb.cmd, inp, timeout=timeout)
        job.push({"type": "run", "index": r, "orig": {"time": ra.time, "verdict": ra.verdict},
                  "opt": {"time": rb.time, "verdict": rb.verdict}})
        if ra.verdict == "OK":
            ta.append(ra.time)
        if rb.verdict == "OK":
            tb.append(rb.time)
        if ra.verdict != "OK" or rb.verdict != "OK":
            break
    same = bool(ra and rb and runner.outputs_equal(ra.stdout, rb.stdout))
    best_a = min(ta) if ta else None
    best_b = min(tb) if tb else None
    speedup = round(best_a / best_b, 2) if best_a and best_b and best_b > 0 else None
    return {"ok": True, "same_output": same, "input_source": src, "input_bytes": len(inp),
            "original": {"time": best_a, "verdict": ra.verdict if ra else None, "mem_kb": ra.mem_kb if ra else None,
                         "stderr": (ra.stderr or "")[:1000] if ra else ""},
            "optimized": {"time": best_b, "verdict": rb.verdict if rb else None, "mem_kb": rb.mem_kb if rb else None,
                          "stderr": (rb.stderr or "")[:1000] if rb else ""},
            "speedup": speedup, "times": {"original": ta, "optimized": tb},
            "output_preview": {"original": (ra.stdout if ra else "")[:400], "optimized": (rb.stdout if rb else "")[:400]}}


def handle_benchmark(body: dict) -> dict:
    if not (body.get("original") or "").strip() or not (body.get("optimized") or "").strip():
        raise ApiError("Potrzebny jest kod oryginalny i zoptymalizowany.")
    if jobs is None:
        raise ApiError("Brak modułu zadań w tle.", 500)
    return {"job": jobs.submit(_bench_work, body, title="Benchmark optymalizacji")}


ROUTES = {
    "POST /api/optimize": handle_optimize,
    "POST /api/optimize/benchmark": handle_benchmark,
    "GET /api/optimize/rules": handle_rules,
}

"""Silnik formatera C++ oparty na tokenach (`oiforge.core.cpp_lexer`) — bez clang-format, bez AI.

Zasada działania (w uproszczeniu jak clang-format):
1. Lekser daje tokeny z pozycjami (także komentarze i dyrektywy preprocesora).
2. Klasyfikator nadaje tokenom role: `<`/`>` szablonowe vs porównania, `>>` zamykające szablony vs przesunięcie,
   minus unarny vs binarny, `*`/`&` deklaratora vs mnożenie/AND, `<<` strumienia vs przesunięcie, rodzaje `:` i `{`.
3. Parser (rekurencyjny, tolerancyjny na błędny kod) dzieli kod na „linie logiczne” i nadaje im wcięcia:
   instrukcje, bloki, ciała funkcji/lambd, if/else/for/while/do/switch/case, struct/class/enum, namespace, szablony.
4. Emiter składa wynik: spacje między tokenami wg stylu, zachowuje komentarze na miejscu, zachowuje świadome
   łamania linii wewnątrz wyrażeń (z wyrównaniem do nawiasu), opcjonalnie łamie zbyt długie linie.

Gwarancje (sprawdzane w `format_source`): sekwencja tokenów bez białych znaków jest identyczna przed i po,
a formatowanie jest idempotentne. Dyrektywy preprocesora, raw stringi i treść komentarzy nie są modyfikowane
(poza usunięciem spacji na końcach linii komentarza blokowego).
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..core import cpp_lexer

# ============================================================================================ styl
STYLE_SCHEMA: list[dict] = [
    {"key": "indent_width", "type": "int", "min": 1, "max": 8, "group": "Wcięcia", "label": "Szerokość wcięcia"},
    {"key": "use_tabs", "type": "bool", "group": "Wcięcia", "label": "Wcięcia tabulatorami"},
    {"key": "indent_case", "type": "bool", "group": "Wcięcia", "label": "Wcinaj `case` wewnątrz `switch`"},
    {"key": "indent_namespace", "type": "bool", "group": "Wcięcia", "label": "Wcinaj zawartość `namespace`"},
    {"key": "access_offset", "type": "choice", "group": "Wcięcia", "label": "`public:` / `private:`",
     "choices": [["outdent", "Na poziomie struct"], ["half", "Pół wcięcia"], ["indent", "Jak pola"]]},
    {"key": "pp_left", "type": "bool", "group": "Wcięcia", "label": "Preprocesor (#…) przy lewej krawędzi"},

    {"key": "brace_style", "type": "choice", "group": "Klamry", "label": "Styl klamer",
     "choices": [["attach", "Przyklejone (attach)"], ["kr", "K&R (funkcje w nowej linii)"],
                 ["stroustrup", "Stroustrup"], ["allman", "Allman (każda w nowej linii)"],
                 ["gnu", "GNU-lite (klamry wcięte o pół)"]]},
    {"key": "else_newline", "type": "bool", "group": "Klamry", "label": "`else` / `catch` w nowej linii po `}`"},
    {"key": "short_if", "type": "choice", "group": "Klamry", "label": "Krótki if/for/while bez klamer",
     "choices": [["never", "Zawsze w dwóch liniach"], ["keep", "Zostaw jak jest"], ["always", "W jednej linii, gdy się mieści"]]},
    {"key": "short_blocks", "type": "choice", "group": "Klamry", "label": "Krótkie bloki { … } (1 instrukcja)",
     "choices": [["never", "Zawsze rozwijaj"], ["keep", "Zostaw jak jest"], ["always", "W jednej linii, gdy się mieści"]]},

    {"key": "space_after_keyword", "type": "bool", "group": "Spacje", "label": "Spacja po if/for/while: `if (x)`"},
    {"key": "space_before_brace", "type": "bool", "group": "Spacje", "label": "Spacja przed `{`: `) {`"},
    {"key": "space_assign", "type": "bool", "group": "Spacje", "label": "Wokół `=`, `+=`, `-=`…"},
    {"key": "space_arith", "type": "bool", "group": "Spacje", "label": "Wokół `+ - * / %`, bitowych i przesunięć"},
    {"key": "space_compare", "type": "bool", "group": "Spacje", "label": "Wokół `== != < > <= >=`"},
    {"key": "space_logical", "type": "bool", "group": "Spacje", "label": "Wokół `&&` i `||`"},
    {"key": "space_ternary", "type": "bool", "group": "Spacje", "label": "Wokół `?` i `:` (operator trójargumentowy)"},
    {"key": "space_stream", "type": "bool", "group": "Spacje", "label": "Wokół `<<` / `>>` strumieni (`cout << x`)"},
    {"key": "space_after_comma", "type": "bool", "group": "Spacje", "label": "Po przecinku"},
    {"key": "space_for_semicolon", "type": "bool", "group": "Spacje", "label": "Po `;` w nagłówku `for`"},
    {"key": "space_range_colon", "type": "bool", "group": "Spacje", "label": "Wokół `:` w pętli zakresowej"},
    {"key": "space_after_template", "type": "bool", "group": "Spacje", "label": "`template <…>` (spacja po template)"},
    {"key": "space_inside_parens", "type": "bool", "group": "Spacje", "label": "Wewnątrz nawiasów: `( x )`"},
    {"key": "space_inside_braces", "type": "bool", "group": "Spacje", "label": "Wewnątrz inicjalizacji: `{ 1, 2 }`"},
    {"key": "pointer_align", "type": "choice", "group": "Spacje", "label": "Wyrównanie `*` i `&`",
     "choices": [["left", "Do typu: int* p"], ["right", "Do nazwy: int *p"]]},
    {"key": "trailing_comment_spaces", "type": "int", "min": 1, "max": 4, "group": "Spacje",
     "label": "Spacje przed komentarzem na końcu linii"},

    {"key": "max_empty_lines", "type": "int", "min": 0, "max": 4, "group": "Linie", "label": "Maks. pustych linii z rzędu"},
    {"key": "keep_block_start_blank", "type": "bool", "group": "Linie", "label": "Zostaw pustą linię na początku bloku"},
    {"key": "separate_definitions", "type": "bool", "group": "Linie", "label": "Pusta linia między funkcjami/strukturami"},
    {"key": "max_line_length", "type": "int", "min": 0, "max": 240, "group": "Linie",
     "label": "Łam linie dłuższe niż (0 = nie łam)"},
]
STYLE_KEYS = [s["key"] for s in STYLE_SCHEMA]

BASE_STYLE: dict = {
    "indent_width": 4, "use_tabs": False, "indent_case": True, "indent_namespace": False, "access_offset": "outdent",
    "pp_left": True, "brace_style": "attach", "else_newline": False, "short_if": "keep", "short_blocks": "keep",
    "space_after_keyword": True, "space_before_brace": True, "space_assign": True, "space_arith": True,
    "space_compare": True, "space_logical": True, "space_ternary": True, "space_stream": True,
    "space_after_comma": True, "space_for_semicolon": True, "space_range_colon": True, "space_after_template": False,
    "space_inside_parens": False, "space_inside_braces": False, "pointer_align": "left", "trailing_comment_spaces": 1,
    "max_empty_lines": 1, "keep_block_start_blank": False, "separate_definitions": False, "max_line_length": 0,
}

PRESETS: dict[str, dict] = {
    "moja": {"label": "Moja", "description": "Twój styl (domyślnie czytelny styl OI; „Naucz się” dopasuje go do Twoich programów).",
             "options": {}},
    "google": {"label": "Google", "description": "Google C++ Style: wcięcie 2, klamry przyklejone, `int* p`, 2 spacje przed komentarzem.",
               "options": {"indent_width": 2, "access_offset": "half", "space_after_template": True,
                           "trailing_comment_spaces": 2, "short_if": "keep", "short_blocks": "keep",
                           "max_line_length": 100}},
    "llvm": {"label": "LLVM", "description": "Styl LLVM/clang-format: wcięcie 2, `case` bez wcięcia, `int *p`.",
             "options": {"indent_width": 2, "indent_case": False, "pointer_align": "right", "space_after_template": True,
                         "short_if": "never", "short_blocks": "keep", "max_line_length": 100}},
    "allman": {"label": "Allman", "description": "Każda klamra w osobnej linii, wcięcie 4.",
               "options": {"brace_style": "allman", "else_newline": True, "short_if": "never", "short_blocks": "never"}},
    "kr": {"label": "K&R", "description": "Kernighan & Ritchie: klamra funkcji w nowej linii, reszta przyklejona, `int *p`.",
           "options": {"brace_style": "kr", "indent_case": False, "pointer_align": "right", "short_if": "never",
                       "short_blocks": "never"}},
    "compact": {"label": "Kompaktowy CP", "description": "Zwięźle jak na zawodach: `for(int i=0;i<n;i++)`, krótkie if-y w jednej linii.",
                "options": {"space_after_keyword": False, "space_assign": False, "space_arith": False,
                            "space_compare": False, "space_for_semicolon": False, "space_range_colon": False,
                            "short_if": "always", "short_blocks": "always"}},
}
PRESET_ORDER = ["moja", "google", "llvm", "allman", "kr", "compact"]


def normalize_options(opts: dict | None) -> dict:
    """Uzupełnia brakujące klucze, przycina zakresy, odrzuca nieznane klucze/wartości."""
    out = dict(BASE_STYLE)
    for spec in STYLE_SCHEMA:
        k = spec["key"]
        if not opts or k not in opts or opts[k] is None:
            continue
        v = opts[k]
        try:
            if spec["type"] == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "tak", "yes", "on")
            elif spec["type"] == "int":
                v = max(spec["min"], min(spec["max"], int(v)))
            elif spec["type"] == "choice":
                if v not in [c[0] for c in spec["choices"]]:
                    continue
        except (TypeError, ValueError):
            continue
        out[k] = v
    return out


# ============================================================================================ leksyka
_NONASCII_RE = re.compile(r"[^\x00-\x7f]")


_TOK_CACHE: dict[str, list] = {}


def tokenize(code: str) -> list:
    """`cpp_lexer.tokenize`, ale odporne na litery spoza ASCII w identyfikatorach (lekser rdzenia się na nich
    wywraca). Litery zamieniamy na `Q` (ta sama długość → te same pozycje) i przywracamy oryginalny tekst.
    Wynik jest cache'owany (tokeny traktujemy jako niezmienne)."""
    hit = _TOK_CACHE.get(code)
    if hit is not None:
        return hit
    toks = _tokenize(code)
    if len(_TOK_CACHE) > 48:
        _TOK_CACHE.clear()
    _TOK_CACHE[code] = toks
    return toks


def _tokenize(code: str) -> list:
    if _NONASCII_RE.search(code):
        safe = _NONASCII_RE.sub(lambda m: "Q" if m.group().isalpha() else m.group(), code)
        toks = cpp_lexer.tokenize(safe)
        for t in toks:
            t.text = code[t.pos:t.pos + len(t.text)]
        return toks
    return cpp_lexer.tokenize(code)


_INC_RE = re.compile(r'^#[ \t]*include[ \t]*([<"][^>"\n]*[>"])[ \t]*(.*)$')


def norm_pp(text: str) -> str:
    """Normalizuje białe znaki w `#include<x>` → `#include <x>`; inne dyrektywy zostają nietknięte."""
    if "\n" in text:
        return text
    m = _INC_RE.match(text)
    if not m:
        return text
    rest = m.group(2)
    return "#include " + m.group(1) + ((" " + rest) if rest else "")


def signature(code: str) -> list[tuple[str, str]]:
    """Sekwencja tokenów istotnych semantycznie (bez komentarzy i białych znaków; w `#include` spacje nieistotne)."""
    return [(t.kind, norm_pp(t.text) if t.kind == "pp" else t.text)
            for t in tokenize(code.replace("\r\n", "\n")) if t.kind != "comment"]


def comment_signature(code: str) -> list[str]:
    """Treść komentarzy (bez spacji na końcach linii) — formatter ich nie zmienia."""
    return ["\n".join(ln.rstrip() for ln in t.text.split("\n"))
            for t in tokenize(code.replace("\r\n", "\n")) if t.kind == "comment"]


@lru_cache(maxsize=8192)
def _glue_ok(a: str, b: str) -> bool:
    """Czy sklejenie `a`+`b` bez spacji daje dokładnie te same dwa tokeny?"""
    try:
        toks = cpp_lexer.tokenize(a + b)
    except Exception:  # noqa: BLE001
        return False
    return len(toks) == 2 and toks[0].text == a and toks[1].text == b and toks[1].kind != "comment"


# ============================================================================================ słowniki
CTRL_KW = {"if", "for", "while", "switch", "catch"}
TYPE_KW = {"int", "long", "short", "char", "bool", "float", "double", "void", "unsigned", "signed", "auto",
           "wchar_t", "char8_t", "char16_t", "char32_t"}
QUAL_KW = {"const", "volatile", "constexpr", "static", "inline", "mutable", "extern", "thread_local", "register",
           "virtual", "explicit", "friend", "typename", "consteval", "constinit", "struct", "class", "enum", "union"}
VALUE_KW = {"this", "true", "false", "nullptr"}
CALL_KW = {"sizeof", "alignof", "decltype", "typeid", "noexcept", "static_assert", "alignas", "explicit",
           "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast", "requires"} | TYPE_KW
KWPAREN = {"return", "case", "throw", "new", "delete", "and", "or", "not", "xor", "bitand", "bitor", "compl",
           "co_return", "co_await", "co_yield", "else", "do", "and_eq", "or_eq", "xor_eq", "not_eq", "using", "goto"}
WORD_OPS = {"and", "or", "not", "xor", "bitand", "bitor", "compl", "and_eq", "or_eq", "xor_eq", "not_eq"}
CASTS = {"static_cast", "dynamic_cast", "reinterpret_cast", "const_cast"}
KNOWN_TEMPLATES = set("""
vector array deque list forward_list set multiset map multimap unordered_set unordered_multiset unordered_map
unordered_multimap stack queue priority_queue pair tuple bitset basic_string function optional variant span valarray
complex greater less greater_equal less_equal equal_to not_equal_to plus minus multiplies hash numeric_limits
make_pair make_tuple get tie unique_ptr shared_ptr weak_ptr make_unique make_shared enable_if enable_if_t is_same
is_same_v conditional conditional_t decay decay_t remove_reference common_type iterator_traits initializer_list tree
gp_hash_table cc_hash_table uniform_int_distribution uniform_real_distribution normal_distribution
bernoulli_distribution discrete_distribution duration duration_cast time_point atomic lock_guard
istream_iterator ostream_iterator back_insert_iterator reverse_iterator max min declval forward move_iterator
array_view basic_ostream basic_istream char_traits allocator integral_constant is_integral is_integral_v
is_floating_point invoke_result_t result_of any_cast holds_alternative get_if visit monostate ratio bit_cast
popcount countl_zero countr_zero accumulate iota fill_n mdspan ranges views
""".split())
KNOWN_TYPES = set("""
string wstring string_view size_t ssize_t ptrdiff_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t
uint64_t intmax_t uintmax_t __int128 __int128_t __uint128_t mt19937 mt19937_64 istream ostream iostream
stringstream istringstream ostringstream ifstream ofstream fstream FILE ll ull ld lld pii pll vi vll vvi vb
vpii vpll Node Edge iterator const_iterator nullptr_t byte
""".split())
STREAM_NAMES = {"cout", "cin", "cerr", "clog", "wcout", "wcin", "wcerr", "os", "out", "is", "in", "ss", "oss",
                "iss", "fin", "fout", "stream", "strm", "buf", "file", "inp", "output", "input", "o", "str", "sout",
                "sin"}
LOOP_MACROS = {"FOR", "REP", "rep", "forn", "fore", "F0R", "FOR0", "FORd", "ROF", "R0F", "trav", "each", "loop",
               "F", "FORE", "REPP", "per", "repi", "rrep", "Rep", "For", "ForEach", "forr", "fori", "forj", "rep1",
               "FOR1", "fr", "ff", "forl", "forx", "FOREACH"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
ARITH_OPS = {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"}
COMPARE_OPS = {"==", "!=", "<", ">", "<=", ">=", "<=>"}
LOGICAL_OPS = {"&&", "||"}
BLOCKISH = {"block", "func", "class", "ns", "enum", "lambda", "extern", "ctrl", "case"}
_WORD_KINDS = ("id", "kw", "num", "str", "char")
_EXPR_START_KW = {"this", "true", "false", "nullptr", "sizeof", "new", "delete", "static_cast", "dynamic_cast",
                  "reinterpret_cast", "const_cast", "alignof", "decltype", "typeid", "noexcept", "not", "compl"}


# ============================================================================================ formatter
class _Fmt:
    def __init__(self, code: str, opts: dict):
        self.code = code
        self.o = opts
        self.iw = int(opts["indent_width"])
        self.A = tokenize(code)
        A = self.A
        # nowe linie w przerwie przed tokenem
        self.nlb = []
        prev_end = 0
        for t in A:
            self.nlb.append(code.count("\n", prev_end, t.pos))
            prev_end = t.pos + len(t.text)
        self.trail_nl = code.count("\n", prev_end)
        self.S = [k for k, t in enumerate(A) if t.kind != "comment"]      # indeksy tokenów istotnych w A
        self.a2s = {k: p for p, k in enumerate(self.S)}
        self.T = [A[k] for k in self.S]
        n = self.n = len(self.T)
        self.pairs = cpp_lexer.match_brackets(self.T)
        self.role = [""] * n
        self.cat = [""] * n          # kategoria operatora binarnego
        self.start = [None] * n      # (anchor, col, cont)
        self.join = [False] * n
        self.soft = [None] * n       # miękki początek linii (po template<...>)
        self.fallback = [None] * n   # (anchor, col) gdy wymuszona nowa linia przed tokenem „doklejonym”
        self.bkind = [None] * n      # rodzaj klamry
        self.inner = {}              # zamykająca klamra bloku -> (anchor, col) zawartości
        self.ppcol = {}              # dyrektywa -> (anchor, col)
        self.tmpl_pops = [0] * n
        self.lambda_body = set()
        self.nobreak = set()         # tokeny, przed którymi nie wolno łamać długiej linii
        self.regions = []            # kandydaci do sklejenia w jedną linię
        self.unbraced = []           # (nagłówek, pierwszy, ostatni) ciała if/for/while bez klamer
        self.defs = []               # (pierwszy, ostatni) definicje najwyższego poziomu
        self.anchor = -1
        self.inline_depth = 0
        self.block_level = 0
        self.toplevel = []           # (pierwszy, ostatni) instrukcje/dyrektywy najwyższego poziomu
        self.steps = 0
        self.types = set(KNOWN_TYPES)
        self.templates = set(KNOWN_TEMPLATES)
        self.loop_macros = set(LOOP_MACROS)
        self._collect_names()
        self._classify()

    # ------------------------------------------------------------------ pomocnicze
    def tx(self, p: int) -> str:
        return self.T[p].text if 0 <= p < self.n else ""

    def kd(self, p: int) -> str:
        return self.T[p].kind if 0 <= p < self.n else ""

    def is_p(self, p: int, s: str) -> bool:
        return 0 <= p < self.n and self.T[p].kind == "punct" and self.T[p].text == s

    def is_op(self, p: int, s: str) -> bool:
        return 0 <= p < self.n and self.T[p].kind == "op" and self.T[p].text == s

    # ------------------------------------------------------------------ nazwy typów / szablonów / makr
    def _collect_names(self):
        T, n = self.T, self.n
        for p, t in enumerate(T):
            if t.kind == "pp":
                m = re.match(r"#\s*define\s+(\w+)\s*\(([^)]*)\)\s*(for|while|if)\b", t.text)
                if m:
                    self.loop_macros.add(m.group(1))
                m = re.match(r"#\s*define\s+(\w+)\s+(?:long\s+long|long\s+double|unsigned\s+long\s+long|int|"
                             r"double|pair\s*<|vector\s*<|std::)", t.text)
                if m:
                    self.types.add(m.group(1))
                continue
            if t.kind == "kw" and t.text in ("struct", "class", "union", "enum") and p + 1 < n:
                q = p + 1
                if self.tx(q) in ("class", "struct"):
                    q += 1
                if self.kd(q) == "id":
                    self.types.add(T[q].text)
            elif t.kind == "kw" and t.text == "typedef":
                q = p + 1
                depth = 0
                last_id = None
                while q < n and not (depth == 0 and self.tx(q) == ";"):
                    if self.tx(q) in ("(", "[", "{"):
                        depth += 1
                    elif self.tx(q) in (")", "]", "}"):
                        depth -= 1
                    elif T[q].kind == "id" and depth == 0:
                        last_id = T[q].text
                    q += 1
                if last_id:
                    self.types.add(last_id)
            elif t.kind == "kw" and t.text == "using" and self.kd(p + 1) == "id" and self.is_op(p + 2, "="):
                self.types.add(T[p + 1].text)
            elif t.kind == "kw" and t.text == "template" and self.is_op(p + 1, "<"):
                # parametry szablonu to typy; nazwa po nagłówku to szablon
                q, depth = p + 2, 1
                while q < n and depth > 0:
                    x = self.tx(q)
                    if x == "<":
                        depth += 1
                    elif x == ">":
                        depth -= 1
                    elif x == ">>":
                        depth -= 2
                    elif x in (";", "{", "}"):
                        break
                    elif self.kd(q) == "id" and self.tx(q - 1) in ("class", "typename", "..."):
                        self.types.add(self.tx(q))
                    q += 1
                if depth <= 0 and q < n:
                    r = q
                    while r < n and r < q + 40 and self.tx(r) not in (";", "{", "("):
                        if self.tx(r) in ("struct", "class", "union", "using") and self.kd(r + 1) == "id":
                            self.templates.add(self.tx(r + 1))
                            self.types.add(self.tx(r + 1))
                            break
                        r += 1
                    else:
                        if r < n and self.tx(r) == "(" and self.kd(r - 1) == "id":
                            self.templates.add(self.tx(r - 1))

    # ------------------------------------------------------------------ klasyfikacja tokenów
    def _try_template(self, i: int):
        """Jeśli `<` pod indeksem i otwiera listę argumentów szablonu, zwraca (indeks_zamknięcia, zagnieżdżone<)."""
        T, n = self.T, self.n
        prev = self.tx(i - 1)
        pk = self.kd(i - 1)
        if prev == "operator" or (i >= 2 and self.tx(i - 2) == "operator"):
            return None
        strong = prev == "template" or prev in CASTS or prev in self.templates
        if not strong and pk != "id":
            return None
        depth = 1
        j = i + 1
        nested = []
        has_type = False
        bad = False
        while j < n:
            t = T[j]
            x = t.text
            if t.kind == "pp":
                return None
            if t.kind == "punct":
                if x in "([":
                    c = self.pairs.get(j)
                    if c is None or c < j:
                        return None
                    j = c + 1
                    continue
                if x in ";{})]":
                    return None
                j += 1
                continue
            if t.kind == "op":
                if x == "<":
                    if self.kd(j - 1) == "id" or self.tx(j - 1) in self.templates:
                        nested.append(j)
                        depth += 1
                        j += 1
                        continue
                    return None
                if x == ">":
                    depth -= 1
                    if depth == 0:
                        break
                    j += 1
                    continue
                if x == ">>":
                    depth -= 2
                    if depth <= 0:
                        if depth < 0:
                            return None
                        break
                    j += 1
                    continue
                if x in ("&&", "||", "==", "!=", "?", ">=", "<=", ">>=", "<<=", "+=", "-=", "!", "<=>", "=="):
                    return None
                if x in ("<<", "|", "^", "%", "/") and not strong:
                    return None
                if x not in ("::", ",", "*", "&", "...", "+", "-", "=", ".", "~", ":"):
                    bad = True
                j += 1
                continue
            if t.kind == "kw":
                if x in ("return", "and", "or", "not", "new", "delete", "throw", "case"):
                    return None
                if x in TYPE_KW or x in ("const", "typename", "class", "struct", "unsigned", "long", "decltype",
                                          "volatile", "auto"):
                    has_type = True
            elif t.kind == "id":
                if x in self.types or x in self.templates:
                    has_type = True
            elif t.kind in ("str", "char"):
                bad = True
            j += 1
        else:
            return None
        if j >= n:
            return None
        if strong:
            return j, nested
        if bad:
            return None
        nx = self.tx(j + 1)
        nk = self.kd(j + 1)
        ok_next = (nx in ("(", ")", "{", "::", ">", ">>", ",", ";", "&", "*", "&&", "...", "[", "}", "=", "]", ".")
                   or nk in ("id", "kw") or j + 1 >= n)
        if ok_next and (has_type or nested or nx == "::" or (nx == "(" and prev[:1].isupper())):
            return j, nested
        return None

    def _classify(self):
        T, n, pairs = self.T, self.n, self.pairs
        role = self.role
        # --- szablony
        p = 0
        while p < n:
            if T[p].kind == "op" and T[p].text == "<" and not role[p]:
                r = self._try_template(p)
                if r:
                    close, nested = r
                    role[p] = "tmpl_open"
                    for q in nested:
                        role[q] = "tmpl_open"
                    # przypisz zamknięcia: przejdź jeszcze raz licząc głębokość
                    depth = 0
                    q = p
                    while q <= close:
                        x = T[q].text
                        if role[q] == "tmpl_open":
                            depth += 1
                        elif T[q].kind == "punct" and x in "([" and pairs.get(q, -1) > q:
                            q = pairs[q] + 1
                            continue
                        elif x == ">" and T[q].kind == "op" and depth > 0:
                            role[q] = "tmpl_close"
                            self.tmpl_pops[q] = 1
                            depth -= 1
                        elif x == ">>" and depth > 0:
                            role[q] = "tmpl_close"
                            self.tmpl_pops[q] = min(2, depth)
                            depth -= 2
                        q += 1
            p += 1
        # --- operator name tokens: operator<, operator(), operator[] ...
        for p in range(n):
            if T[p].kind == "kw" and T[p].text == "operator":
                q = p + 1
                if self.is_p(q, "(") and self.is_p(q + 1, ")"):
                    role[q] = role[q + 1] = "opname"
                elif self.is_p(q, "[") and self.is_p(q + 1, "]"):
                    role[q] = role[q + 1] = "opname"
                elif T[q].kind == "op" if q < n else False:
                    role[q] = "opname"
                    if self.tx(q + 1) in ("[",) and self.tx(q + 2) == "]":
                        role[q + 1] = role[q + 2] = "opname"
                elif self.kd(q) == "kw" and self.tx(q) in ("new", "delete"):
                    role[q] = "opname_kw"
                    if self.is_p(q + 1, "[") and self.is_p(q + 2, "]"):
                        role[q + 1] = role[q + 2] = "opname"
                elif self.kd(q) == "str" and self.kd(q + 1) == "id":
                    role[q] = "opname"
                    role[q + 1] = "opname"
        # --- operatory trójargumentowe ?: (stos per głębokość nawiasów)
        qstack: list[list[int]] = [[]]
        for p in range(n):
            t = T[p]
            if t.kind == "punct":
                if t.text in "([{":
                    qstack.append([])
                elif t.text in ")]}":
                    if len(qstack) > 1:
                        qstack.pop()
                    else:
                        qstack[0] = []
                elif t.text == ";":
                    qstack[-1] = []
            elif t.kind == "op":
                if t.text == "?":
                    qstack[-1].append(p)
                    role[p] = "ternary"
                elif t.text == ":" and qstack[-1]:
                    qstack[-1].pop()
                    role[p] = "ternary"
            elif t.kind == "pp":
                pass
        # --- lambdy: [capt] <tpl>? (params)? spec* {   oraz atrybuty [[...]]
        for p in range(n):
            if not self.is_p(p, "[") or role[p]:
                continue
            if self.is_p(p + 1, "[") or self.is_p(p - 1, "["):
                role[p] = "attr"
                continue
            prev, pk = self.tx(p - 1), self.kd(p - 1)
            if prev == "auto" or (prev in ("&", "&&") and self.tx(p - 2) == "auto"):
                role[p] = "sbind"
                continue
            if p == 0 or pk == "pp" or prev in ("=", "(", ",", "{", "return", ";", "}", "?", ":", "&&", "||", "!",
                                                  "<<", ">>", "+", "-", "*", "/", "[", "<", ">", "==", "!=",
                                                  "throw", "co_return", "co_yield", "..."):
                if pk in ("id", "num", "str", "char") or (prev in (")", "]") and self.kd(p - 1) == "punct"):
                    role[p] = "subscript"
                    continue
                if prev in ("<", ">") and role[p - 1] in ("tmpl_open", "tmpl_close"):
                    role[p] = "subscript"
                    continue
                c = pairs.get(p)
                if c is None or c < p:
                    role[p] = "subscript"
                    continue
                q = c + 1
                if self.is_op(q, "<") and role[q] == "tmpl_open":
                    depth = 0
                    while q < n:
                        if role[q] == "tmpl_open":
                            depth += 1
                        elif role[q] == "tmpl_close":
                            depth -= self.tmpl_pops[q]
                            if depth <= 0:
                                q += 1
                                break
                        q += 1
                if self.is_p(q, "("):
                    pc = pairs.get(q)
                    if pc is None:
                        role[p] = "subscript"
                        continue
                    role[q] = "lparams"
                    q = pc + 1
                guard = 0
                while q < n and guard < 60:
                    guard += 1
                    x = self.tx(q)
                    if x in ("mutable", "constexpr", "consteval", "static"):
                        q += 1
                    elif x == "noexcept":
                        q += 1
                        if self.is_p(q, "("):
                            q = pairs.get(q, q) + 1
                    elif self.is_p(q, "[") and self.is_p(q + 1, "["):
                        q = pairs.get(q, q) + 1
                    elif x == "->":
                        role[q] = "trailing"
                        q += 1
                        while q < n and not self.is_p(q, "{") and self.tx(q) not in (";", ")", ","):
                            if self.is_p(q, "(") or self.is_p(q, "["):
                                q = pairs.get(q, q)
                            q += 1
                    else:
                        break
                if self.is_p(q, "{"):
                    role[p] = "lambda"
                    self.lambda_body.add(q)
                else:
                    role[p] = "lambda" if self.is_p(c + 1, "(") else "subscript"
            else:
                role[p] = "subscript"
        # --- nawiasy okrągłe: rola po poprzedniku + rzutowania C
        for p in range(n):
            if not self.is_p(p, "(") or role[p]:
                continue
            prev, pk = self.tx(p - 1), self.kd(p - 1)
            if pk == "kw" and prev in CTRL_KW:
                role[p] = "ctrl"
            elif pk == "kw" and prev == "constexpr" and self.tx(p - 2) == "if":
                role[p] = "ctrl"
            elif pk == "kw" and prev in KWPAREN:
                role[p] = "kwparen"
                c = pairs.get(p)
                if c and c > p + 1 and self._is_cast(p, c):
                    role[c] = "cast_close"
            elif pk == "kw" and (prev in CALL_KW or prev in VALUE_KW or prev == "operator"):
                role[p] = "call"
            elif pk == "id" or (pk == "punct" and prev in (")", "]")) or role[p - 1] in ("tmpl_close", "opname") \
                    or role[p - 1] == "opname_kw":
                role[p] = "call"
            else:
                role[p] = "group"
                c = pairs.get(p)
                if c and c > p + 1 and self._is_cast(p, c):
                    role[c] = "cast_close"
        # --- -> : trailing return vs member
        for p in range(n):
            if T[p].kind == "op" and T[p].text == "->" and not role[p]:
                if self.is_p(p - 1, ")") and (self.kd(p + 1) == "kw" and self.tx(p + 1) in TYPE_KW | {"decltype", "const", "typename"}
                                              or (self.tx(p + 1) in self.types and self.kd(p + 2) in ("punct",) and self.tx(p + 2) in ("{", ";"))):
                    role[p] = "trailing"
                else:
                    role[p] = "member"
        # --- operatory jedno-/dwuargumentowe, wskaźniki/referencje, strumienie
        for p in range(n):
            t = T[p]
            if t.kind != "op" or role[p]:
                continue
            x = t.text
            if x in ("++", "--"):
                prev, pk = self.tx(p - 1), self.kd(p - 1)
                if pk in ("id", "num") or (pk == "punct" and prev in (")", "]")) or (pk == "kw" and prev in VALUE_KW):
                    role[p] = "postfix"
                else:
                    role[p] = "prefix"
                continue
            if x in ("*", "&", "&&"):
                r = self._ptr_or_op(p)
                role[p] = r
                if r == "bin":
                    self.cat[p] = "logical" if x == "&&" else "arith"
                continue
            if x in ("-", "+", "!", "~"):
                if x in ("!", "~") or not self._binary_ctx(p):
                    role[p] = "unary"
                else:
                    role[p] = "bin"
                    self.cat[p] = "arith"
                continue
            if x in ("<<", ">>"):
                role[p] = "bin"
                self.cat[p] = "stream" if self._is_stream(p) else "arith"
                continue
            if x in ASSIGN_OPS:
                if x == "=" and (self._in_capture(p)):
                    role[p] = "capture"
                    continue
                role[p] = "bin"
                self.cat[p] = "assign"
                continue
            if x in ARITH_OPS:
                role[p] = "bin"
                self.cat[p] = "arith"
                continue
            if x in COMPARE_OPS:
                role[p] = "bin"
                self.cat[p] = "compare"
                continue
            if x in LOGICAL_OPS:
                role[p] = "bin"
                self.cat[p] = "logical"
                continue
            if x == "::":
                role[p] = "scope"
            elif x in (".", ".*", "->*"):
                role[p] = "member"
            elif x == "...":
                role[p] = "ellipsis"
            elif x == ":":
                role[p] = "colon"
            elif x == "?":
                role[p] = "ternary"
            else:
                role[p] = "other"
        for p in range(n):
            if self.kd(p) == "kw" and self.tx(p) in WORD_OPS:
                self.cat[p] = "word"

    def _in_capture(self, p: int) -> bool:
        q = p - 1
        while q >= 0 and q > p - 30:
            if self.is_p(q, "["):
                return self.role[q] == "lambda"
            if self.tx(q) in (";", "{", "}", "(", ")", "]"):
                return False
            q -= 1
        return False

    def _is_cast(self, o: int, c: int) -> bool:
        """( typ ) wyrażenie — rzutowanie w stylu C?"""
        has_type = False
        for q in range(o + 1, c):
            t = self.T[q]
            x = t.text
            if t.kind == "kw":
                if x in TYPE_KW or x in ("const", "volatile", "unsigned", "signed", "struct", "typename"):
                    has_type = True
                    continue
                return False
            if t.kind == "id":
                if x in self.types or x in self.templates or (self.tx(q - 1) == "::" and self.tx(q - 2) == "std"):
                    has_type = True
                    continue
                if x == "std" and self.tx(q + 1) == "::":
                    continue
                return False
            if t.kind == "op" and x in ("*", "&", "&&", "::", "<", ">", ">>", ","):
                continue
            if t.kind == "num" and self.role[o + 1] == "":
                continue
            return False
        if not has_type:
            return False
        nx = self.T[c + 1] if c + 1 < self.n else None
        if nx is None:
            return False
        if nx.kind in ("id", "num", "str", "char"):
            return True
        if nx.kind == "kw" and (nx.text in _EXPR_START_KW):
            return True
        if nx.kind == "punct" and nx.text == "(":
            return True
        if nx.kind == "op" and nx.text in ("-", "!", "~", "*", "&", "++", "--", "::"):
            # (int)-x : niejednoznaczne z (a)-x, ale zawartość jest typem
            return True
        return False

    def _binary_ctx(self, p: int) -> bool:
        """Czy poprzedni token kończy wyrażenie (→ operator dwuargumentowy)?"""
        if p == 0:
            return False
        t = self.T[p - 1]
        r = self.role[p - 1]
        if t.kind in ("id", "num", "str", "char"):
            return True
        if t.kind == "kw":
            return t.text in VALUE_KW
        if t.kind == "punct":
            if t.text == ")":
                return r != "cast_close"
            if t.text == "]":
                return True
            if t.text == "}":
                return self.bkind[p - 1] == "init" or (self.pairs.get(p - 1, -1) >= 0 and
                                                       self._brace_is_init_guess(self.pairs[p - 1]))
            return False
        if t.kind == "op":
            if r == "postfix":
                return True
            if r == "tmpl_close":
                return True
            return False
        return False

    def _brace_is_init_guess(self, o: int) -> bool:
        prev = self.tx(o - 1)
        return prev in ("=", "(", ",", "return", "{") or self.kd(o - 1) == "id" or self.role[o - 1] == "tmpl_close"

    def _ptr_or_op(self, p: int) -> str:
        """`*`/`&`/`&&`: 'ptr' (deklarator), 'unary' (dereferencja/adres) albo 'bin'."""
        T = self.T
        x = T[p].text
        prev, pk, pr = self.tx(p - 1), self.kd(p - 1), self.role[p - 1] if p > 0 else ""
        nx, nk = self.tx(p + 1), self.kd(p + 1)
        if pk == "kw" and (prev in TYPE_KW or prev in ("const", "volatile", "typename")):
            return "ptr"
        if pk == "op" and prev in ("*", "&", "&&") and pr == "ptr":
            return "ptr"
        if pr == "tmpl_close":
            # vector<int>& v  /  a<b> & c (rzadkie)
            if nk in ("id", "kw") or nx in (")", ",", ">", "*", "&", "&&", "[", "...", "="):
                return "ptr"
            return "bin"
        if pk == "id":
            known = prev in self.types or prev in self.templates
            if known:
                if nk in ("id",) or nx in (")", ",", ">", "*", "&", "&&", "...", "const", "[") or (nk == "kw" and nx in ("const", "operator")):
                    # wyklucz `a * b` gdy a to typ z KNOWN_TYPES użyty jako zmienna? (rzadkie)
                    return "ptr"
            # T* x w liście parametrów szablonu / deklaracjach: id id
            if nx in (")", ",", ">") and self.tx(p - 2) in ("(", ",", "<", "const", "::") and self.kd(p - 2) != "id":
                if self.tx(p - 2) == "::" or prev[:1].isupper():
                    return "ptr"
            if nk == "id" and x != "&&" and self._decl_start(p - 1, stmt_only=True) and \
                    self.tx(p + 2) in ("=", ";", ",", "[", "{"):
                return "ptr"
            if nk == "id" and x != "&&" and prev[:1].isupper() and self._decl_start(p - 1) and \
                    self.tx(p + 2) in ("=", ";", ",", ")", "[", "{"):
                return "ptr"
            if x == "&&" and nk in ("id",) and self._decl_start(p - 1) and self.tx(p + 2) in ("=", ")", ",", ":"):
                if prev in self.types:
                    return "ptr"
        if pk == "punct" and prev == ")" and x in ("&", "&&") and nx in ("{", ";", "const", "noexcept", "override", "="):
            return "ptr"   # kwalifikator referencyjny metody
        if self._binary_ctx(p):
            return "bin"
        return "unary"

    def _decl_start(self, q: int, stmt_only: bool = False) -> bool:
        """Czy identyfikator pod q (typ) stoi na początku deklaracji (po ; { } ( , lub specyfikatorach)?"""
        r = q - 1
        # typ kwalifikowany a::b::T
        while r >= 1 and self.tx(r) == "::" and self.kd(r - 1) == "id":
            r -= 2
        if r < 0:
            return True
        x = self.tx(r)
        if self.kd(r) == "pp":
            return True
        if x in ((";", "{", "}") if stmt_only else (";", "{", "}", "(", ",")) and self.kd(r) == "punct":
            return True
        if self.kd(r) == "kw" and x in ("const", "static", "constexpr", "inline", "volatile", "mutable", "extern",
                                         "typename", "struct", "class", "register", "thread_local", "friend",
                                         "virtual", "public", "private", "protected"):
            return True
        if x == ":" and self.role[r] in ("colon", "label"):
            return True
        if x == "<" and self.role[r] == "tmpl_open":
            return True
        return False

    def _is_stream(self, p: int) -> bool:
        """`<<`/`>>` w łańcuchu strumienia (cout << …, cin >> …) czy przesunięcie bitowe?"""
        T = self.T
        depth = 0
        q = p - 1
        steps = 0
        while q >= 0 and steps < 400:
            steps += 1
            t = T[q]
            if t.kind == "pp":
                break
            if t.kind == "punct":
                if t.text in ")]":
                    o = self.pairs.get(q)
                    if o is None or o > q:
                        break
                    q = o - 1
                    continue
                if t.text in "([{;,}":
                    break
            if t.kind == "op":
                if t.text in ("<<", ">>") and self.cat[q]:
                    return self.cat[q] == "stream"
                if t.kind == "op" and (t.text in ASSIGN_OPS or t.text in ("?", ":", "&&", "||", "return")):
                    break
            if t.kind == "kw" and t.text in ("return", "case"):
                break
            if t.kind == "id" and t.text in STREAM_NAMES and self.tx(q - 1) not in (".", "->"):
                return True
            q -= 1
        # prawa strona: endl / literał napisowy → strumień
        nx = self.T[p + 1] if p + 1 < self.n else None
        if nx is not None and (nx.kind == "str" or nx.text in ("endl", "flush", "setprecision", "fixed", "setw")):
            return True
        if nx is not None and nx.text == "std" and self.tx(p + 3) in ("endl", "flush", "setprecision", "fixed", "setw"):
            return True
        return False

    # ================================================================== parser struktury
    def set_start(self, p: int, col: int, cont: int | None = None):
        if p >= self.n or self.inline_depth:
            return
        self.start[p] = (self.anchor, col, self.iw if cont is None else cont)

    def set_join(self, p: int, fb_col: int | None = None):
        if p >= self.n:
            return
        self.join[p] = True
        if fb_col is not None:
            self.fallback[p] = (self.anchor, fb_col)

    def parse(self):
        self.i = 0
        guard = 0
        while self.i < self.n:
            before = self.i
            self.block_items(0, "file", None)
            if self.i < self.n and self.i == before:
                # osierocona klamra zamykająca na najwyższym poziomie
                self.set_start(self.i, 0)
                self.i += 1
            guard += 1
            if guard > self.n + 5:
                break

    def block_items(self, col: int, ctx: str, closer):
        T = self.T
        iw = self.iw
        stmt_col = col
        if ctx == "switch":
            label_col = col if self.o["indent_case"] else max(0, col - iw)
            stmt_col = label_col + iw
        while self.i < self.n:
            p = self.i
            t = T[p]
            if t.kind == "punct" and t.text == "}":
                if closer is not None and p == closer:
                    return
                if ctx == "file" and closer is None:
                    self.set_start(p, col)
                    self.i += 1
                    continue
                return
            if t.kind == "pp":
                self.pp(p, col)
                self.i += 1
                if self.block_level == 0:
                    self.toplevel.append((p, p))
                continue
            self.steps += 1
            if self.steps > 4 * self.n + 100:
                raise RuntimeError("parser nie robi postępu")
            if ctx == "switch" and t.kind == "kw" and t.text in ("case", "default") and self._label_colon(p) is not None:
                self.case_label(label_col, stmt_col)
                continue
            if ctx == "class" and t.kind == "kw" and t.text in ("public", "private", "protected") and self.is_op(p + 1, ":"):
                off = self.o["access_offset"]
                acol = col - iw if off == "outdent" else (col - max(1, iw // 2) if off == "half" else col)
                self.set_start(p, max(0, acol))
                self.role[p + 1] = "label"
                self.i = p + 2
                continue
            self.statement(stmt_col, ctx)
            if self.i == p:
                self.i += 1
            if self.block_level == 0:
                self.toplevel.append((p, self.i - 1))

    def pp(self, p: int, col: int):
        self.ppcol[p] = (self.anchor, col)

    def _label_colon(self, p: int):
        """Indeks `:` kończącego etykietę case/default (albo None)."""
        T = self.T
        if T[p].text == "default":
            return p + 1 if self.is_op(p + 1, ":") else None
        q = p + 1
        depth = 0
        while q < self.n and q < p + 200:
            t = T[q]
            if t.kind == "pp":
                return None
            if t.kind == "punct":
                if t.text in "([{":
                    depth += 1
                elif t.text in ")]}":
                    depth -= 1
                    if depth < 0:
                        return None
                elif t.text == ";":
                    return None
            elif t.kind == "op" and t.text == ":" and depth == 0 and self.role[q] != "ternary":
                return q
            q += 1
        return None

    def case_label(self, label_col: int, stmt_col: int):
        p = self.i
        c = self._label_colon(p)
        self.set_start(p, label_col)
        self.role[c] = "label"
        self.i = c + 1
        if self.i >= self.n:
            return
        first_stmt = self.i
        if self.is_p(self.i, "{"):
            self.block(self.i, label_col, "case")
            first_stmt = None
        # instrukcje w tej samej linii co etykieta → region "case"
        stmts = []
        while self.i < self.n:
            q = self.i
            t = self.T[q]
            if t.kind == "punct" and t.text == "}":
                break
            if t.kind == "kw" and t.text in ("case", "default") and self._label_colon(q) is not None:
                break
            if t.kind == "pp":
                self.pp(q, stmt_col)
                self.i += 1
                continue
            self.statement(stmt_col, "block")
            if self.i == q:
                self.i += 1
            stmts.append((q, self.i - 1))
        if first_stmt is not None and stmts:
            same = [s for s in stmts if self.T[s[0]].line == self.T[c].line]
            if same and same[0][0] == first_stmt:
                self.regions.append({"kind": "case", "head": p, "first": same[0][0], "last": same[-1][1],
                                     "stmts": same})

    def statement(self, col: int, ctx: str):
        p = self.i
        t = self.T[p]
        if t.kind == "pp":
            self.pp(p, col)
            self.i += 1
            return
        self.set_start(p, col)
        self.statement_body(col, ctx)

    def statement_body(self, col: int, ctx: str):
        T = self.T
        p = self.i
        if p >= self.n:
            return
        t = T[p]
        x = t.text
        if t.kind == "punct":
            if x == "{":
                self.block(p, col, "block", head=None)
                return
            if x == ";":
                self.i += 1
                return
        if t.kind == "kw":
            if x == "if":
                return self.if_stmt(col, ctx)
            if x in ("for", "while", "switch"):
                return self.loop_stmt(col, ctx)
            if x == "do":
                return self.do_stmt(col, ctx)
            if x == "else":
                self.i += 1
                if self.tx(self.i) == "if":
                    self.set_join(self.i)
                    return self.if_stmt(col, ctx)
                return self.body(col, ctx, p)
            if x == "try":
                return self.try_stmt(col, ctx)
            if x in ("case", "default") and self._label_colon(p) is not None:
                c = self._label_colon(p)
                self.role[c] = "label"
                self.i = c + 1
                return
            if x in ("public", "private", "protected") and self.is_op(p + 1, ":"):
                self.role[p + 1] = "label"
                self.i = p + 2
                return
            if x == "namespace" or (x == "inline" and self.tx(p + 1) == "namespace"):
                q = p + 1
                while q < self.n and q < p + 30 and not self.is_p(q, "{") and self.tx(q) not in (";", "="):
                    q += 1
                if self.is_p(q, "{"):
                    self.i = q
                    d = [p, None]
                    self.defs.append(d)
                    self.block(q, col, "ns", head=p)
                    d[1] = self.i - 1
                    return
            if x == "extern" and self.kd(p + 1) == "str" and self.is_p(p + 2, "{"):
                self.i = p + 2
                self.block(p + 2, col, "extern", head=p)
                return
            if x == "template" and self.is_op(p + 1, "<") and self.role[p + 1] == "tmpl_open":
                q = p + 1
                depth = 0
                while q < self.n:
                    if self.role[q] == "tmpl_open":
                        depth += 1
                    elif self.role[q] == "tmpl_close":
                        depth -= self.tmpl_pops[q]
                        if depth <= 0:
                            break
                    q += 1
                self.i = q + 1
                if self.i < self.n:
                    if T[self.i].kind == "pp":
                        return
                    if not self.inline_depth:
                        self.soft[self.i] = (self.anchor, col)
                    self.statement_body(col, ctx)
                return
        if t.kind == "id":
            if self.is_op(p + 1, ":") and self.role[p + 1] != "ternary" and ctx in ("block", "switch") \
                    and not self.is_op(p + 2, ":"):
                self.role[p + 1] = "label"
                self.i = p + 2
                return
            if self.is_p(p + 1, "(") and ctx in ("block", "switch", "file"):
                c = self.pairs.get(p + 1)
                if c is not None and c > p and c + 1 < self.n:
                    nt = T[c + 1]
                    starts_stmt = (nt.kind == "punct" and nt.text == "{") or \
                        (nt.kind in ("id", "kw") and nt.text not in WORD_OPS and nt.text not in
                         ("const", "noexcept", "override", "final", "throw", "mutable", "volatile", "try")) or \
                        (nt.kind == "op" and nt.text in ("++", "--") and self.kd(c + 2) == "id")
                    if starts_stmt and (ctx != "file" or (x in self.loop_macros and not self.kd(p - 1) == "id")):
                        return self.loop_stmt(col, ctx, macro=True)
        self.simple(col, ctx)

    # --- if / else
    def if_stmt(self, col: int, ctx: str):
        p = self.i            # 'if'
        q = p + 1
        if self.tx(q) == "constexpr":
            q += 1
        if self.tx(q) == "!" and self.tx(q + 1) == "consteval":
            q += 2
        elif self.tx(q) == "consteval":
            q += 1
        if self.is_p(q, "("):
            self.role[q] = "ctrl"
            self.head_parens(q)
        else:
            self.i = q
        braced = self.body(col, ctx, p)
        # else
        j = self.i
        if j < self.n and self.T[j].kind == "kw" and self.T[j].text == "else":
            cuddle = braced and not self.o["else_newline"] and self.o["brace_style"] not in ("allman", "gnu", "stroustrup")
            if cuddle:
                self.set_join(j, col)
            else:
                self.set_start(j, col)
            self.i = j + 1
            if self.tx(self.i) == "if":
                self.set_join(self.i)
                self.if_stmt(col, ctx)
            else:
                self.body(col, ctx, j)

    def head_parens(self, o: int):
        """Przetwarza ( ... ) nagłówka (if/while/for/switch/catch/makro) — z lambdami w środku."""
        c = self.pairs.get(o)
        self.i = o + 1
        if c is None:
            return
        self.scan_range(c)
        self.i = c + 1

    def scan_range(self, end: int):
        """Skanuje tokeny do `end` (wyłącznie): klasyfikuje klamry (lambdy, inicjalizacje)."""
        T = self.T
        while self.i < end:
            p = self.i
            t = T[p]
            if t.kind == "pp":
                self.pp(p, self._cur_col_guess())
                self.i += 1
                continue
            if t.kind == "punct" and t.text == "{":
                if p in self.lambda_body:
                    self.lambda_block(p)
                    continue
                self.bkind[p] = "init"
                c = self.pairs.get(p)
                if c is not None and c > p:
                    self.bkind[c] = "init"
                self.i += 1
                continue
            self.i += 1

    def _cur_col_guess(self) -> int:
        return 0

    def body(self, col: int, ctx: str, head: int) -> bool:
        """Ciało instrukcji sterującej. Zwraca True, gdy było w klamrach."""
        T = self.T
        iw = self.iw
        while self.i < self.n and T[self.i].kind == "pp":
            self.pp(self.i, col + iw)
            self.i += 1
        p = self.i
        if p >= self.n:
            return False
        t = T[p]
        if t.kind == "punct" and t.text == "{":
            self.block(p, col, "ctrl", head=head)
            return True
        if t.kind == "punct" and t.text == ";":
            self.set_join(p)
            self.i += 1
            return False
        if t.kind == "punct" and t.text == "}":
            return False
        self.set_start(p, col + iw)
        self.statement_body(col + iw, ctx if ctx in ("block", "switch") else "block")
        last = self.i - 1
        if last >= p:
            self.unbraced.append((head, p, last))
        simple = T[p].kind != "kw" or T[p].text not in ("if", "for", "while", "do", "switch", "try")
        if simple:
            for q in range(p + 1, last + 1):
                if self.start[q] is not None or self.kd(q) == "pp":
                    simple = False
                    break
        if simple and last >= p:
            self.regions.append({"kind": "if", "head": head, "first": p, "last": last})
        return False

    def loop_stmt(self, col: int, ctx: str, macro: bool = False):
        p = self.i
        kw = self.tx(p)
        q = p + 1
        if self.is_p(q, "("):
            if not macro:
                self.role[q] = "ctrl"
            c = self.pairs.get(q)
            if kw == "for" and c is not None:
                self._for_header(q, c)
            self.head_parens(q)
        else:
            self.i = q
        if kw == "while" and self.is_p(self.i, ";") and not macro:
            self.set_join(self.i)
            self.i += 1
            return
        if kw == "switch":
            while self.i < self.n and self.T[self.i].kind == "pp":
                self.pp(self.i, col)
                self.i += 1
            if self.is_p(self.i, "{"):
                self.block(self.i, col, "ctrl", head=p, inner_ctx="switch")
                return
        self.body(col, ctx, p)

    def _for_header(self, o: int, c: int):
        depth = 0
        semis = 0
        q = o + 1
        colon = None
        while q < c:
            t = self.T[q]
            if t.kind == "punct":
                if t.text in "([{":
                    depth += 1
                elif t.text in ")]}":
                    depth -= 1
                elif t.text == ";" and depth == 0:
                    self.role[q] = "for_semi"
                    semis += 1
            elif t.kind == "op" and t.text == ":" and depth == 0 and self.role[q] != "ternary" and colon is None:
                colon = q
            q += 1
        if colon is not None and semis == 0:
            self.role[colon] = "range"

    def do_stmt(self, col: int, ctx: str):
        p = self.i
        self.i = p + 1
        braced = self.body(col, ctx, p)
        j = self.i
        if j < self.n and self.tx(j) == "while":
            if braced:
                self.set_join(j, col)
            else:
                self.set_start(j, col)
            q = j + 1
            if self.is_p(q, "("):
                self.role[q] = "ctrl"
                self.head_parens(q)
            else:
                self.i = q
            if self.is_p(self.i, ";"):
                self.i += 1

    def try_stmt(self, col: int, ctx: str):
        p = self.i
        self.i = p + 1
        if self.is_p(self.i, "{"):
            self.block(self.i, col, "ctrl", head=p)
        while self.i < self.n and self.tx(self.i) == "catch":
            j = self.i
            cuddle = not self.o["else_newline"] and self.o["brace_style"] not in ("allman", "gnu", "stroustrup")
            if cuddle:
                self.set_join(j, col)
            else:
                self.set_start(j, col)
            q = j + 1
            if self.is_p(q, "("):
                self.role[q] = "ctrl"
                self.head_parens(q)
            else:
                self.i = q
            if self.is_p(self.i, "{"):
                self.block(self.i, col, "ctrl", head=j)
            else:
                break

    # --- bloki
    def block(self, p: int, col: int, what: str, head=None, inner_ctx=None):
        """Blok w klamrach. what: block (instrukcja), ctrl, func, class, enum, ns, extern, case."""
        o = self.o
        iw = self.iw
        bs = o["brace_style"]
        if what == "block" and head is None:
            attach = None          # klamra jest sama początkiem instrukcji
        elif bs == "attach":
            attach = True
        elif bs in ("kr", "stroustrup"):
            attach = what != "func"
        else:
            attach = False
        brace_col = col
        inner_col = col + iw
        if bs == "gnu" and what in ("ctrl", "case", "block") and head is not None:
            off = iw if iw < 4 else iw // 2
            brace_col = col + off
            inner_col = col + max(iw, off + 2)
        if what in ("ns", "extern") and not o["indent_namespace"]:
            inner_col = col
        self.bkind[p] = what
        if attach is True:
            self.set_join(p, brace_col)
        elif attach is False:
            self.set_start(p, brace_col)
        close = self.pairs.get(p)
        if close is not None and close < p:
            close = None
        if close is not None:
            self.bkind[close] = what
            self.inner[close] = (self.anchor, inner_col)
        self.i = p + 1
        self.block_level += 1
        if what == "enum":
            self.enum_body(inner_col, close)
        else:
            ctx = inner_ctx or {"class": "class", "ns": "file", "extern": "file"}.get(what, "block")
            self.block_items(inner_col, ctx, close)
        self.block_level -= 1
        if close is None:
            return
        self.i = close
        self.set_start(close, brace_col)
        self.i = close + 1
        # region: krótki blok
        content = list(range(p + 1, close))
        if what not in ("ns", "extern") and head is not None:
            simple = True
            if content:
                for q in content[1:]:
                    if self.start[q] is not None or self.kd(q) == "pp" or self.soft[q] is not None:
                        simple = False
                        break
                if self.kd(content[0]) == "pp" or (self.kd(content[0]) == "kw" and self.tx(content[0]) in
                                                   ("if", "for", "while", "do", "switch", "try", "case", "default",
                                                    "public", "private", "protected")):
                    simple = False
            if simple:
                self.regions.append({"kind": "block", "head": head, "open": p, "first": p, "last": close,
                                     "what": what})

    def enum_body(self, col: int, close):
        """Wyliczenia: zawartość jako jedna „instrukcja” bez dodatkowego wcięcia kontynuacji."""
        if close is None:
            close = self.n
        if self.i < close:
            p = self.i
            if self.kd(p) == "pp":
                self.pp(p, col)
            else:
                self.set_start(p, col, cont=0)
            while self.i < close:
                q = self.i
                if self.kd(q) == "pp":
                    self.pp(q, col)
                elif self.is_p(q, "{"):
                    self.bkind[q] = "init"
                    c = self.pairs.get(q)
                    if c is not None and c > q:
                        self.bkind[c] = "init"
                self.i += 1

    def lambda_block(self, p: int):
        """Ciało lambdy: w jednej linii, jeśli tak było w oryginale; inaczej blok wcięty względem linii z `{`."""
        close = self.pairs.get(p)
        self.bkind[p] = "lambda"
        if close is None or close < p:
            self.i = p + 1
            return
        self.bkind[close] = "lambda"
        single = self.T[p].line == self.T[close].line and not self._has_comment_or_pp(p, close)
        if single or self.inline_depth:
            for q in range(p, close + 1):
                self.nobreak.add(q)
            self.inline_depth += 1
            self.block_level += 1
            self.i = p + 1
            self.block_items(self.iw, "block", close)
            self.block_level -= 1
            self.inline_depth -= 1
            self.i = close + 1
            return
        self.set_join(p, None)
        old = self.anchor
        self.anchor = p
        self.inner[close] = (p, self.iw)
        self.i = p + 1
        self.block_level += 1
        self.block_items(self.iw, "block", close)
        self.block_level -= 1
        self.set_start(close, 0)
        self.anchor = old
        self.i = close + 1
        # tokeny po } doklejone
        if close + 1 < self.n and self.kd(close + 1) != "pp":
            self.set_join(close + 1)

    def _has_comment_or_pp(self, p: int, q: int) -> bool:
        a, b = self.S[p], self.S[q]
        for k in range(a + 1, b):
            if self.A[k].kind in ("comment", "pp"):
                return True
        return False

    # --- instrukcja/deklaracja ogólna
    def simple(self, col: int, ctx: str):
        T = self.T
        n = self.n
        stack: list[int] = []
        ctor = False
        first = self.i
        while self.i < n:
            p = self.i
            t = T[p]
            x = t.text
            k = t.kind
            if k == "pp":
                self.pp(p, col)
                self.i += 1
                continue
            if k == "punct":
                if x == ";":
                    if not stack:
                        self.i += 1
                        return
                    self.i += 1
                    continue
                if x in "([":
                    stack.append(p)
                    self.i += 1
                    continue
                if x in ")]":
                    if stack:
                        stack.pop()
                    self.i += 1
                    continue
                if x == "{":
                    if p in self.lambda_body:
                        self.lambda_block(p)
                        continue
                    if not stack and self._is_body_brace(p, first, ctor, ctx):
                        what = "func" if ctx in ("file", "class") else "ctrl"
                        d = [first, None]
                        if ctx in ("file",) and not self.inline_depth:
                            self.defs.append(d)
                        self.block(p, col, what, head=first)
                        d[1] = self.i - 1
                        if self.is_p(self.i, ";"):
                            self.set_join(self.i)
                            self.i += 1
                        return
                    self.bkind[p] = "init"
                    c = self.pairs.get(p)
                    if c is not None and c > p:
                        self.bkind[c] = "init"
                    stack.append(p)
                    self.i += 1
                    continue
                if x == "}":
                    if stack and self.tx(stack[-1]) == "{":
                        stack.pop()
                        self.i += 1
                        continue
                    return
            if k == "kw" and x in ("struct", "class", "union", "enum") and not stack:
                b = self._class_head(p)
                if b is not None:
                    for q in range(p + 1, b):
                        if self.is_op(q, ":") and self.role[q] in ("colon", ""):
                            self.role[q] = "colon"
                    d = [first, None]
                    if ctx == "file" and not self.inline_depth and x != "enum":
                        self.defs.append(d)
                    self.i = b
                    self.block(b, col, "enum" if x == "enum" else "class", head=first)
                    d[1] = self.i - 1
                    if self.i < n and not self.is_p(self.i, ";") and self.kd(self.i) != "pp":
                        self.set_join(self.i)
                    continue
            if k == "op" and x == ":" and not stack and self.role[p] in ("colon",) and self.is_p(p - 1, ")"):
                ctor = True
            self.i += 1

    def _is_body_brace(self, p: int, first: int, ctor: bool, ctx: str) -> bool:
        prev = self.tx(p - 1)
        pk = self.kd(p - 1)
        if ctor:
            return prev in (")", "}") and pk == "punct" or prev in ("const", "noexcept")
        if pk == "punct" and prev == ")":
            o = self.pairs.get(p - 1)
            if o is not None and self.role[o] == "group" and self.tx(o - 1) in ("=", "return"):
                return False
            # `= T(x) {` raczej nie występuje; ( ... ) { → ciało funkcji / makro-pętli
            for q in range(first, p):
                if self.is_op(q, "=") and self.role[q] == "bin" and self._depth_at(first, q) == 0:
                    return False
            return True
        if pk == "kw" and prev in ("const", "noexcept", "override", "final", "mutable", "volatile", "try"):
            return True
        if pk == "id" and prev in ("override", "final"):
            return True
        if prev in ("&", "&&") and self.role[p - 1] == "ptr" and self.is_p(p - 2, ")"):
            return True
        # trailing return: ) -> T {
        q = p - 1
        while q > first and q > p - 30:
            if self.role[q] == "trailing":
                return True
            if self.tx(q) in (";", "{", "}", "=") and self.kd(q) == "punct":
                break
            q -= 1
        return False

    def _depth_at(self, a: int, b: int) -> int:
        d = 0
        for q in range(a, b):
            if self.kd(q) == "punct":
                if self.tx(q) in "([{":
                    d += 1
                elif self.tx(q) in ")]}":
                    d -= 1
        return d

    def _class_head(self, p: int):
        """Indeks `{` definicji struct/class/union/enum zaczynającej się na p albo None."""
        T, n = self.T, self.n
        q = p + 1
        if T[p].text == "enum" and self.tx(q) in ("class", "struct"):
            q += 1
        seen_name = False
        base = False
        while q < n and q < p + 200:
            t = T[q]
            x = t.text
            if t.kind == "pp":
                return None
            if t.kind == "punct":
                if x == "{":
                    return q
                if x == "[" and self.is_p(q + 1, "["):
                    c = self.pairs.get(q)
                    if c is None:
                        return None
                    q = c + 1
                    continue
                if x == "(" and self.tx(q - 1) in ("alignas", "__attribute__", "__declspec"):
                    c = self.pairs.get(q)
                    if c is None:
                        return None
                    q = c + 1
                    continue
                if x == "," and base:
                    q += 1
                    continue
                return None
            if t.kind == "op":
                if self.role[q] == "tmpl_open":
                    depth = 0
                    while q < n:
                        if self.role[q] == "tmpl_open":
                            depth += 1
                        elif self.role[q] == "tmpl_close":
                            depth -= self.tmpl_pops[q]
                            if depth <= 0:
                                break
                        q += 1
                    q += 1
                    continue
                if x == "::":
                    q += 1
                    continue
                if x == ":" and not base:
                    base = True
                    q += 1
                    continue
                return None
            if t.kind == "id":
                if seen_name and not base and x not in ("final",):
                    return None
                seen_name = True
                q += 1
                continue
            if t.kind == "kw":
                if x in ("final", "public", "private", "protected", "virtual", "alignas", "__attribute__") or \
                        (base and (x in TYPE_KW or x in ("unsigned", "signed", "long", "short"))):
                    q += 1
                    continue
                return None
            return None
        return None

    # ================================================================== spacje
    def word(self, p: int) -> bool:
        k = self.kd(p)
        return k in _WORD_KINDS

    def pref_after(self, a: int, b: int) -> int:
        """Preferencja spacji PO tokenie a (gdy następny to b): 1 tak, -1 nie, 0 obojętnie."""
        o = self.o
        t = self.T[a]
        x = t.text
        r = self.role[a]
        if t.kind == "punct":
            if x == ",":
                return 1 if o["space_after_comma"] else -1
            if x == ";":
                if r == "for_semi":
                    return 1 if o["space_for_semicolon"] else -1
                return 1
            if x == "(":
                if r == "opname":
                    return -1
                return 1 if o["space_inside_parens"] else -1
            if x == "[":
                return -1
            if x == "{":
                if self.bkind[a] == "init" or self.bkind[a] is None:
                    return 1 if o["space_inside_braces"] else -1
                return 1
            if x == ")":
                if r == "cast_close":
                    return -1
                if r == "opname":
                    return -1
                return 0
            if x == "]":
                if r == "opname":
                    return -1
                return 0
            if x == "}":
                return 0
            return 0
        if t.kind == "op":
            if r == "tmpl_open":
                return -1
            if r == "tmpl_close":
                return 1 if self.kd(b) in ("id", "kw") else 0
            if r in ("unary", "prefix", "scope", "member", "opname", "capture"):
                return -1
            if r == "ptr":
                if o["pointer_align"] == "left":
                    return 1
                return -1
            if r == "postfix":
                return 0
            if r == "bin":
                return 1 if self._cat_on(a) else -1
            if r == "ternary":
                return 1 if o["space_ternary"] else -1
            if r == "range":
                return 1 if o["space_range_colon"] else -1
            if r == "label":
                return 1
            if r == "colon":
                return 1
            if r == "trailing":
                return 1
            if r == "ellipsis":
                return 1 if self.kd(b) in ("id", "kw") else -1
            return 0
        if t.kind == "kw":
            if x in VALUE_KW:
                return 0
            if x == "operator":
                return -1 if self.role[b] in ("opname",) else 1
            if x == "template":
                return 0
            return 1
        return 0

    def pref_before(self, a: int, b: int) -> int:
        """Preferencja spacji PRZED tokenem b (gdy poprzedni to a)."""
        o = self.o
        t = self.T[b]
        x = t.text
        r = self.role[b]
        if t.kind == "punct":
            if x in (";", ","):
                return -1
            if x == "(":
                if r == "ctrl":
                    return 1 if o["space_after_keyword"] else -1
                if r in ("call", "lparams", "opname"):
                    return -1
                if r == "kwparen":
                    return 1
                return 0
            if x == ")":
                if self.is_p(a, "(") or r == "opname":
                    return -1
                return 1 if o["space_inside_parens"] else -1
            if x == "[":
                if r in ("subscript", "opname"):
                    return -1
                return 0
            if x == "]":
                return -1
            if x == "{":
                bk = self.bkind[b]
                if bk == "init" or bk is None:
                    if self.is_p(a, "{") or self.is_p(a, "(") or self.is_p(a, "["):
                        return 0
                    if self.kd(a) == "id" or self.role[a] == "tmpl_close" or self.tx(a) in (")", "]"):
                        return -1
                    return 0
                return 1 if o["space_before_brace"] else -1
            if x == "}":
                bk = self.bkind[b]
                if self.is_p(a, "{"):
                    return -1
                if bk == "init" or bk is None:
                    return 1 if o["space_inside_braces"] else -1
                return 1
            return 0
        if t.kind == "op":
            if r == "tmpl_open":
                if self.tx(a) == "template":
                    return 1 if o["space_after_template"] else -1
                return -1
            if r == "tmpl_close":
                return -1
            if r in ("postfix", "member", "opname", "capture"):
                return -1
            if r == "scope":
                if self.kd(a) == "id" or self.role[a] in ("tmpl_close",):
                    return -1
                return 0
            if r == "ptr":
                if self.role[a] == "ptr":
                    return -1
                if o["pointer_align"] == "left":
                    return -1
                return 1
            if r == "bin":
                return 1 if self._cat_on(b) else -1
            if r == "ternary":
                return 1 if o["space_ternary"] else -1
            if r == "range":
                return 1 if o["space_range_colon"] else -1
            if r == "label":
                return -1
            if r == "colon":
                return 1
            if r == "trailing":
                return 1
            if r == "ellipsis":
                return -1
            return 0
        return 0

    def _cat_on(self, p: int) -> bool:
        c = self.cat[p]
        o = self.o
        if c == "assign":
            return o["space_assign"]
        if c == "arith":
            return o["space_arith"]
        if c == "compare":
            return o["space_compare"]
        if c == "logical":
            return o["space_logical"]
        if c == "stream":
            return o["space_stream"]
        return True

    def space(self, a: int, b: int) -> str:
        """Odstęp między kolejnymi tokenami istotnymi a, b w tej samej linii."""
        if a < 0:
            return ""
        ta, tb = self.T[a], self.T[b]
        pa = self.pref_after(a, b)
        pb = self.pref_before(a, b)
        if ta.kind in ("num", "str", "char") and tb.kind == "id" and self.S[b] == self.S[a] + 1 \
                and ta.pos + len(ta.text) == tb.pos:
            sp = ""                                             # literał użytkownika: 10_km, "abc"s
        elif ta.kind == "str" and tb.kind == "str":
            sp = " "
        elif pa < 0 or pb < 0:
            sp = ""
        elif pa > 0 or pb > 0:
            sp = " "
        elif (self.word(a) or (ta.kind == "punct" and ta.text in (")", "]", "}"))) and self.word(b):
            sp = " "
        elif ta.kind == "kw" and ta.text in WORD_OPS or tb.kind == "kw" and tb.text in WORD_OPS:
            sp = " "
        else:
            sp = ""
        if sp == "" and not _glue_ok(ta.text, tb.text):
            sp = " "
        return sp

    # ================================================================== emisja
    def emit(self) -> str:
        A, T, S, o = self.A, self.T, self.S, self.o
        iw = self.iw
        n = self.n
        maxe = int(o["max_empty_lines"])
        limit = int(o["max_line_length"] or 0)
        tcs = int(o["trailing_comment_spaces"])
        self.buf: list[str] = []
        self.col = 0
        self.line_indent = 0
        self.line_has = False
        self.indent_of = [0] * n            # wcięcie linii, w której wyemitowano token
        stack: list[list] = []              # [open_p, kind, line_indent, content_col|None, block_depth]
        block_depth = 0
        ctx_stack: list[tuple] = []
        stmt_col, stmt_cont = 0, iw
        need_nl = False
        prev_sig = -1
        prev_k = -1                          # ostatni wyemitowany token (indeks w A)
        merge_until = -1
        consumed = set()
        region_at = {}
        for rg in self.regions:
            region_at.setdefault(rg["first"], []).append(rg)
        ensure_blank = self._ensure_blank_marks()

        def base(anchor):
            return 0 if anchor is None or anchor < 0 else self.indent_of[anchor]

        def resolve(st):
            return base(st[0]) + st[1]

        def indent_str(c):
            if o["use_tabs"]:
                return "\t" * (c // iw) + " " * (c % iw)
            return " " * c

        def newline(c, blanks=0):
            if self.buf:
                # usuń spacje końcowe
                while self.buf and self.buf[-1] == " ":
                    self.buf.pop()
                self.buf.append("\n" * (1 + max(0, blanks)))
            self.buf.append(indent_str(c))
            self.col = c
            self.line_indent = c
            self.line_has = False

        def put(text, sp=""):
            if sp and self.line_has:
                self.buf.append(sp)
                self.col += len(sp)
            if "\n" in text:
                self.buf.append(text)
                self.col = len(text) - text.rfind("\n") - 1
            else:
                self.buf.append(text)
                self.col += len(text)
            self.line_has = True

        def cont_col(p):
            for e in reversed(stack):
                if e[4] != block_depth:
                    break
                if p < n and self.pairs.get(p) == e[0] and self.is_closer(p):
                    return e[2]
                if e[3] is not None:
                    return e[3]
                return e[2] + iw
            return stmt_col + stmt_cont

        def blank_count(k, p):
            b = max(0, self.nlb[k] - 1)
            b = min(b, maxe)
            if prev_sig >= 0 and self.bkind[prev_sig] in BLOCKISH and self.is_p(prev_sig, "{") and \
                    prev_k == S[prev_sig] and not o["keep_block_start_blank"]:
                b = 0
            if p is not None and self.is_p(p, "}") and self.bkind[p] in BLOCKISH:
                b = 0
            if k in ensure_blank and maxe >= 1:
                b = max(b, 1)
            return b

        def region_width(rg, sp_first):
            """Szerokość, gdyby region był w jednej linii (od bieżącej kolumny)."""
            w = self.col + len(sp_first)
            prev = None
            for q in range(rg["first"], rg["last"] + 1):
                if prev is not None:
                    w += len(self.space(prev, q))
                w += len(T[q].text)
                prev = q
            if rg["last"] + 1 < n and self.tx(rg["last"] + 1) in (";", ","):
                w += 1
            return w

        def try_merge(p, k):
            """Decyzja o sklejeniu regionu zaczynającego się na tokenie p. Zwraca ostatni token regionu albo -1."""
            for rg in region_at.get(p, []):
                kind = rg["kind"]
                mode = o["short_blocks"] if kind == "block" else o["short_if"]
                if kind == "block" and rg.get("what") == "enum" and mode == "never":
                    mode = "keep"
                if mode == "never":
                    continue
                if self._region_has_comment(rg):
                    continue
                first, last = rg["first"], rg["last"]
                if mode == "keep":
                    if kind == "block":
                        if any(self.nlb[S[q]] for q in range(first + 1, last + 1)):
                            continue
                    elif any(self.nlb[S[q]] for q in range(first, last + 1)):
                        continue
                    lim = limit or 10 ** 6
                else:
                    if kind == "block":
                        if any(self.nlb[S[q]] for q in range(first + 2, last)):
                            continue
                    elif any(self.nlb[S[q]] for q in range(first + 1, last + 1)):
                        continue
                    lim = limit or 100
                sp = self.space(prev_sig, first) if prev_sig >= 0 else ""
                if kind == "block" and sp == "" and self.is_p(first, "{") and self.line_has:
                    sp = " " if o["space_before_brace"] else ""
                if kind in ("if", "case") and sp == "":
                    sp = " "
                if region_width(rg, sp) > lim:
                    continue
                return last
            return -1

        for k, tok in enumerate(A):
            if tok.kind == "comment":
                text = "\n".join(ln.rstrip() for ln in tok.text.split("\n"))
                nxt = self._next_sig(k)
                if prev_k >= 0 and self.nlb[k] == 0 and self.line_has:
                    # komentarz na końcu linii / w środku wyrażenia
                    if text.startswith("//"):
                        sp = " " * tcs
                    else:
                        sp = " " if tok.pos > A[prev_k].pos + len(A[prev_k].text) else ""
                    put(text, sp)
                else:
                    # komentarz w osobnej linii
                    c = self._comment_col(nxt, base, resolve, cont_col)
                    if prev_k < 0:
                        self.buf.append(indent_str(c))
                        self.col = c
                        self.line_indent = c
                    else:
                        b = min(max(0, self.nlb[k] - 1), maxe)
                        if prev_sig >= 0 and prev_k == S[prev_sig] and self.is_p(prev_sig, "{") and \
                                self.bkind[prev_sig] in BLOCKISH and not o["keep_block_start_blank"]:
                            b = 0
                        if k in ensure_blank and maxe >= 1:
                            b = max(b, 1)
                        newline(c, b)
                    put(text)
                    if nxt is not None and self.start[nxt] is not None and merge_until < nxt:
                        consumed.add(nxt)
                if text.startswith("//"):
                    need_nl = True
                elif nxt is not None and self.nlb[S[nxt]] > 0:
                    need_nl = True
                prev_k = k
                continue

            p = self.a2s[k]
            t = T[p]
            st = self.start[p]
            in_merge = p <= merge_until
            if in_merge:
                st = None
            is_pp = t.kind == "pp"
            nl = False
            c = 0
            blanks = 0
            sp = ""
            if prev_k < 0:
                c = 0 if is_pp and o["pp_left"] else (resolve(st) if st else 0)
                self.buf.append(indent_str(c))
                self.col = c
                self.line_indent = c
            elif is_pp:
                nl = True
                pc = self.ppcol.get(p, (-1, 0))
                c = 0 if o["pp_left"] else base(pc[0]) + pc[1]
                blanks = min(max(0, self.nlb[k] - 1), maxe)
                if k in ensure_blank and maxe >= 1:
                    blanks = max(blanks, 1)
            elif need_nl:
                nl = True
                if st is not None:
                    c = resolve(st)
                    blanks = blank_count(k, p)
                elif self.soft[p] is not None and not in_merge:
                    c = resolve(self.soft[p])
                    blanks = blank_count(k, p)
                elif self.fallback[p] is not None:
                    c = resolve(self.fallback[p])
                else:
                    c = cont_col(p)
                    blanks = min(max(0, self.nlb[k] - 1), maxe)
            elif p in consumed and st is not None:
                if self.nlb[k] > 0:
                    nl = True
                    c = resolve(st)
                    blanks = blank_count(k, p)
                else:
                    sp = " "
            elif st is not None:
                nl = True
                c = resolve(st)
                blanks = blank_count(k, p)
            elif self.soft[p] is not None and self.nlb[k] > 0 and not in_merge:
                nl = True
                c = resolve(self.soft[p])
                blanks = blank_count(k, p)
            elif self.join[p] and t.kind == "kw" and t.text in ("else", "catch") and prev_sig == merge_until \
                    and self.fallback[p] is not None and not in_merge:
                # po bloku sklejonym w jedną linię `else` idzie do nowej linii: if (a) { x; }⏎else { y; }
                nl = True
                c = resolve(self.fallback[p])
            elif self.join[p] or in_merge or (t.kind == "punct" and t.text in ";,"):
                sp = self.space(prev_sig, p) if prev_k == S[prev_sig] else self._gap_after_comment(prev_k, k)
            elif self.nlb[k] > 0:
                nl = True
                c = cont_col(p)
                blanks = min(max(0, self.nlb[k] - 1), maxe)
            else:
                sp = self.space(prev_sig, p) if prev_sig >= 0 and prev_k == S[prev_sig] else \
                    self._gap_after_comment(prev_k, k)

            # decyzja o sklejeniu regionu (krótkie if/bloki/case)
            if not in_merge and p in region_at and prev_k >= 0 and not need_nl:
                last = try_merge(p, k)
                if last >= 0:
                    merge_until = last
                    in_merge = True
                    nl = False
                    sp = self.space(prev_sig, p) if prev_k == S[prev_sig] else " "
                    if sp == "" and (self.is_p(p, "{") and o["space_before_brace"] or self.region_kind(p) in ("if", "case")):
                        sp = " "

            # nawiasy: zawartość w tej samej linii co nawias otwierający?
            if stack and stack[-1][3] is None and stack[-1][5] == prev_sig and prev_sig >= 0:
                if not nl:
                    stack[-1][3] = self.col + len(sp)
                else:
                    stack[-1][3] = None
                stack[-1][5] = -2

            if nl:
                newline(c, blanks)
                sp = ""
            # łamanie zbyt długich linii (zachłannie, w miejscu po przecinku / przed << / po && ||)
            elif limit and not in_merge and self.line_has and prev_sig >= 0 and \
                    self._can_break_before(p, prev_sig, k) and \
                    self.col + self._chunk_width(p, sp) > limit:
                cc = cont_col(p)
                if cc + len(t.text) < self.col:
                    newline(cc, 0)
                    sp = ""
            put(norm_pp(t.text) if is_pp else t.text, sp)
            self.indent_of[p] = self.line_indent
            if nl and st is not None and not (self.is_p(p, "}") and self.bkind[p] in BLOCKISH):
                stmt_col, stmt_cont = c, st[2]
            elif nl and self.soft[p] is not None:
                stmt_col, stmt_cont = c, iw
            need_nl = is_pp
            # stos nawiasów
            if t.kind == "punct" and t.text in "([" or (t.kind == "punct" and t.text == "{" and
                                                       self.bkind[p] in ("init", None, "enum")) or \
                    self.role[p] == "tmpl_open":
                stack.append([p, t.text, self.line_indent, None, block_depth, p])
            elif self.is_closer(p):
                pops = self.tmpl_pops[p] if self.role[p] == "tmpl_close" else 1
                for _ in range(pops):
                    for idx in range(len(stack) - 1, -1, -1):
                        if stack[idx][4] != block_depth:
                            break
                        if (self.role[p] == "tmpl_close" and self.role[stack[idx][0]] == "tmpl_open") or \
                                (self.role[p] != "tmpl_close" and self.pairs.get(p) == stack[idx][0]):
                            del stack[idx:]
                            break
            elif t.kind == "punct" and t.text == "{":
                ctx_stack.append((stmt_col, stmt_cont))
                block_depth += 1
            elif t.kind == "punct" and t.text == "}" and self.bkind[p] not in ("init", None, "enum"):
                if ctx_stack:
                    stmt_col, stmt_cont = ctx_stack.pop()
                while stack and stack[-1][4] >= block_depth:
                    stack.pop()
                block_depth = max(0, block_depth - 1)
            prev_sig = p
            prev_k = k
        out = "".join(self.buf).rstrip("\n ")
        return out + "\n" if out.strip() else ""

    def _gap_after_comment(self, prev_k: int, k: int) -> str:
        """Odstęp po komentarzu blokowym w środku linii — jak w oryginale (0 albo 1 spacja)."""
        a = self.A[prev_k]
        return " " if self.A[k].pos > a.pos + len(a.text) else ""

    def region_kind(self, p):
        for rg in self.regions:
            if rg["first"] == p:
                return rg["kind"]
        return None

    def is_closer(self, p: int) -> bool:
        t = self.T[p]
        if t.kind == "punct" and t.text in ")]":
            return True
        if t.kind == "punct" and t.text == "}" and self.bkind[p] in ("init", None, "enum"):
            return True
        return self.role[p] == "tmpl_close"

    def _can_break_before(self, p: int, prev: int, k: int) -> bool:
        if self.join[p] or self.kd(p) == "pp" or p in self.nobreak:
            return False
        if self.A[k - 1].kind == "comment" if k > 0 else False:
            return False
        tp, tq = self.T[prev], self.T[p]
        if tp.kind == "punct" and tp.text == ",":
            return True
        if tq.kind == "op" and self.cat[p] == "stream":
            return True
        if tp.kind == "op" and self.cat[prev] == "logical" and self.role[prev] == "bin":
            return True
        if tp.kind == "kw" and tp.text in ("and", "or"):
            return True
        return False

    def _chunk_width(self, p: int, sp: str) -> int:
        """Szerokość fragmentu od tokenu p do następnego miejsca, w którym wolno złamać linię."""
        T, S, A = self.T, self.S, self.A
        w = len(sp) + len(T[p].text.split("\n")[0])
        q = p + 1
        while q < self.n:
            k = S[q]
            if A[k - 1].kind == "comment" or self.nlb[k] > 0 or self.start[q] is not None or T[q].kind == "pp" \
                    or self.soft[q] is not None:
                break
            if self._can_break_before(q, q - 1, k):
                break
            w += len(self.space(q - 1, q)) + len(T[q].text.split("\n")[0])
            if "\n" in T[q].text:
                break
            q += 1
        return w

    def _region_has_comment(self, rg) -> bool:
        a = self.S[rg["first"] - 1] if rg["first"] > 0 else 0
        b = self.S[rg["last"]]
        for k in range(a + 1, b):
            if self.A[k].kind in ("comment", "pp"):
                return True
        return False

    def _next_sig(self, k: int):
        for j in range(k + 1, len(self.A)):
            if self.A[j].kind != "comment":
                return self.a2s[j]
        return None

    def _comment_col(self, nxt, base, resolve, cont_col) -> int:
        if nxt is None:
            # komentarz na końcu pliku: poziom ostatniej instrukcji najwyższego poziomu
            return 0
        if self.kd(nxt) == "pp":
            pc = self.ppcol.get(nxt, (-1, 0))
            return 0 if self.o["pp_left"] and pc[1] == 0 else base(pc[0]) + pc[1]
        if self.is_p(nxt, "}") and nxt in self.inner:
            a, c = self.inner[nxt]
            return base(a) + c
        st = self.start[nxt]
        if st is not None:
            return resolve(st)
        if self.soft[nxt] is not None:
            return resolve(self.soft[nxt])
        if self.fallback[nxt] is not None:
            return resolve(self.fallback[nxt])
        return cont_col(nxt)

    def _ensure_blank_marks(self) -> set:
        if not self.o["separate_definitions"]:
            return set()
        A, S = self.A, self.S
        marks = set()
        for first, last in self.defs:
            if last is None:
                continue
            k = S[first]
            # komentarze bezpośrednio nad definicją należą do niej
            j = k
            while j - 1 >= 0 and A[j - 1].kind == "comment" and self.nlb[j] == 1 and self.nlb[j - 1] >= 1:
                j -= 1
            if j > 0:
                marks.add(j)
            # pierwszy token w nowej linii po definicji
            e = S[last] + 1
            while e < len(A) and self.nlb[e] == 0:
                e += 1
            if e < len(A):
                marks.add(e)
        return marks


# ============================================================================================ API
class FormatError(Exception):
    pass


def format_unchecked(code: str, opts: dict) -> str:
    """Formatuje bez weryfikacji (do testów i diagnostyki)."""
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    if not code.strip():
        return ""
    f = _Fmt(code, opts)
    f.parse()
    return f.emit()


def format_source(code: str, opts: dict | None = None, verify: bool = True) -> tuple[str, list[str]]:
    """Formatuje kod. Zwraca (wynik, ostrzeżenia). Przy jakimkolwiek naruszeniu gwarancji zwraca oryginał."""
    o = normalize_options(opts)
    src = code.replace("\r\n", "\n").replace("\r", "\n")
    try:
        out = format_unchecked(src, o)
    except RecursionError:
        return code, ["Kod jest zbyt głęboko zagnieżdżony — pozostawiono bez zmian."]
    except Exception as e:  # noqa: BLE001
        return code, [f"Formatter nie poradził sobie z tym kodem ({e.__class__.__name__}) — pozostawiono bez zmian."]
    if verify:
        if signature(out) != signature(src):
            return code, ["Formatowanie zmieniłoby tokeny (wewnętrzny błąd heurystyki) — pozostawiono bez zmian."]
        if comment_signature(out) != comment_signature(src):
            return code, ["Formatowanie zmieniłoby komentarze — pozostawiono bez zmian."]
    return out, []

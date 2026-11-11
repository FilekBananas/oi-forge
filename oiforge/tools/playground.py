"""C++ Playground: fragment kodu bez `main` → automatyczny szkielet programu i uruchomienie.

- definicje najwyższego poziomu (funkcje, struct/class/enum, szablony, using/typedef, namespace, #include/#define)
  trafiają przed `main`, a instrukcje — do `main()`;
- deklaracje zmiennych przed pierwszą instrukcją są globalne (mogą ich używać funkcje), późniejsze zostają w `main`;
- tryb REPL: ostatnie wyrażenie bez średnika albo linia zaczynająca się od `?` jest wypisywane;
- `dbg(a, b, …)` wypisuje nazwy i wartości (na stderr), `cout << kontener` działa dla vector/set/map/pair/tuple/…;
- dyrektywy `#line` sprawiają, że błędy kompilacji wskazują linie Twojego fragmentu.

HTTP: POST /api/playground/wrap {code} -> {program, notes, ...}; POST /api/playground/run {code, input} ->
      {program, notes, compile, run, success}; GET /api/playground/examples -> {examples}.
"""
from __future__ import annotations

import json
import re

from .. import config
from ..core import runner, store
from ..server_api import ApiError
from . import _fmt_engine as E

EXAMPLES_FILE = config.DATA_DIR / "playground_examples.json"
MAX_CODE = 400_000
USER_FILE = "main.cpp"
PRELUDE_FILE = "playground_naglowek.h"
SKELETON_FILE = "playground_szkielet.cpp"

PRELUDE = r"""// ===== OI Forge Playground: wypisywanie kontenerów, dbg(...), __int128 =====
#ifdef __SIZEOF_INT128__
inline std::ostream& operator<<(std::ostream& os, unsigned __int128 v) {
    if (v == 0) return os << '0';
    std::string s;
    while (v) { s += char('0' + int(v % 10)); v /= 10; }
    std::reverse(s.begin(), s.end());
    return os << s;
}
inline std::ostream& operator<<(std::ostream& os, __int128 v) {
    if (v < 0) { os << '-'; return os << (unsigned __int128)(-(v + 1)) + 1; }
    return os << (unsigned __int128)v;
}
inline std::istream& operator>>(std::istream& is, __int128& v) {
    std::string s; is >> s; v = 0; bool neg = !s.empty() && s[0] == '-';
    for (size_t i = neg; i < s.size(); i++) v = v * 10 + (s[i] - '0');
    if (neg) v = -v;
    return is;
}
#endif
template <class A, class B> std::ostream& operator<<(std::ostream& os, const std::pair<A, B>& p);
template <class... T> std::ostream& operator<<(std::ostream& os, const std::tuple<T...>& t);
template <class T, class Al> std::ostream& operator<<(std::ostream& os, const std::vector<T, Al>& v);
template <class T, std::size_t N> std::ostream& operator<<(std::ostream& os, const std::array<T, N>& v);
template <class T, class Al> std::ostream& operator<<(std::ostream& os, const std::deque<T, Al>& v);
template <class T, class Al> std::ostream& operator<<(std::ostream& os, const std::list<T, Al>& v);
template <class T, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::set<T, C, Al>& v);
template <class T, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::multiset<T, C, Al>& v);
template <class T, class H, class Q, class Al> std::ostream& operator<<(std::ostream& os, const std::unordered_set<T, H, Q, Al>& v);
template <class T, class H, class Q, class Al> std::ostream& operator<<(std::ostream& os, const std::unordered_multiset<T, H, Q, Al>& v);
template <class K, class V, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::map<K, V, C, Al>& m);
template <class K, class V, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::multimap<K, V, C, Al>& m);
template <class K, class V, class H, class Q, class Al> std::ostream& operator<<(std::ostream& os, const std::unordered_map<K, V, H, Q, Al>& m);
template <class T, class Co> std::ostream& operator<<(std::ostream& os, std::stack<T, Co> s);
template <class T, class Co> std::ostream& operator<<(std::ostream& os, std::queue<T, Co> q);
template <class T, class Co, class Cm> std::ostream& operator<<(std::ostream& os, std::priority_queue<T, Co, Cm> q);
namespace oipg {
inline void put(std::ostream& os, const std::string& s) { os << '"' << s << '"'; }
inline void put(std::ostream& os, const char* s) { os << '"' << s << '"'; }
inline void put(std::ostream& os, char c) { os << '\'' << c << '\''; }
inline void put(std::ostream& os, bool b) { os << (b ? "true" : "false"); }
template <class T> void put(std::ostream& os, const T& x) { os << x; }
template <class It> std::ostream& seq(std::ostream& os, It b, It e, const char* l, const char* r) {
    os << l;
    for (It it = b; it != e; ++it) { if (it != b) os << ", "; put(os, *it); }
    return os << r;
}
template <class It> std::ostream& kv(std::ostream& os, It b, It e) {
    os << '{';
    for (It it = b; it != e; ++it) { if (it != b) os << ", "; put(os, it->first); os << ": "; put(os, it->second); }
    return os << '}';
}
}
template <class A, class B> std::ostream& operator<<(std::ostream& os, const std::pair<A, B>& p) {
    os << '('; oipg::put(os, p.first); os << ", "; oipg::put(os, p.second); return os << ')';
}
template <class... T> std::ostream& operator<<(std::ostream& os, const std::tuple<T...>& t) {
    os << '(';
    std::apply([&os](const auto&... xs) { std::size_t i = 0; ((os << (i++ ? ", " : ""), oipg::put(os, xs)), ...); }, t);
    return os << ')';
}
template <class T, class Al> std::ostream& operator<<(std::ostream& os, const std::vector<T, Al>& v) {
    os << '[';
    for (std::size_t i = 0; i < v.size(); i++) { if (i) os << ", "; const T& x = v[i]; oipg::put(os, x); }
    return os << ']';
}
template <class T, std::size_t N> std::ostream& operator<<(std::ostream& os, const std::array<T, N>& v) { return oipg::seq(os, v.begin(), v.end(), "[", "]"); }
template <class T, class Al> std::ostream& operator<<(std::ostream& os, const std::deque<T, Al>& v) { return oipg::seq(os, v.begin(), v.end(), "[", "]"); }
template <class T, class Al> std::ostream& operator<<(std::ostream& os, const std::list<T, Al>& v) { return oipg::seq(os, v.begin(), v.end(), "[", "]"); }
template <class T, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::set<T, C, Al>& v) { return oipg::seq(os, v.begin(), v.end(), "{", "}"); }
template <class T, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::multiset<T, C, Al>& v) { return oipg::seq(os, v.begin(), v.end(), "{", "}"); }
template <class T, class H, class Q, class Al> std::ostream& operator<<(std::ostream& os, const std::unordered_set<T, H, Q, Al>& v) { return oipg::seq(os, v.begin(), v.end(), "{", "}"); }
template <class T, class H, class Q, class Al> std::ostream& operator<<(std::ostream& os, const std::unordered_multiset<T, H, Q, Al>& v) { return oipg::seq(os, v.begin(), v.end(), "{", "}"); }
template <class K, class V, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::map<K, V, C, Al>& m) { return oipg::kv(os, m.begin(), m.end()); }
template <class K, class V, class C, class Al> std::ostream& operator<<(std::ostream& os, const std::multimap<K, V, C, Al>& m) { return oipg::kv(os, m.begin(), m.end()); }
template <class K, class V, class H, class Q, class Al> std::ostream& operator<<(std::ostream& os, const std::unordered_map<K, V, H, Q, Al>& m) { return oipg::kv(os, m.begin(), m.end()); }
template <class T, class Co> std::ostream& operator<<(std::ostream& os, std::stack<T, Co> s) {
    os << "[szczyt: ";
    for (bool f = true; !s.empty(); s.pop(), f = false) { if (!f) os << ", "; oipg::put(os, s.top()); }
    return os << ']';
}
template <class T, class Co> std::ostream& operator<<(std::ostream& os, std::queue<T, Co> q) {
    os << "[przód: ";
    for (bool f = true; !q.empty(); q.pop(), f = false) { if (!f) os << ", "; oipg::put(os, q.front()); }
    return os << ']';
}
template <class T, class Co, class Cm> std::ostream& operator<<(std::ostream& os, std::priority_queue<T, Co, Cm> q) {
    os << "[top: ";
    for (bool f = true; !q.empty(); q.pop(), f = false) { if (!f) os << ", "; oipg::put(os, q.top()); }
    return os << ']';
}
"""

DBG = r"""namespace oipg {
inline std::vector<std::string> split_names(const char* s) {
    std::vector<std::string> out(1);
    int depth = 0; char q = 0;
    for (const char* p = s; *p; ++p) {
        char c = *p;
        if (q) { out.back() += c; if (c == '\\' && p[1]) out.back() += *++p; else if (c == q) q = 0; continue; }
        if (c == '"' || c == '\'') q = c;
        else if (c == '(' || c == '[' || c == '{') depth++;
        else if (c == ')' || c == ']' || c == '}') depth--;
        else if (c == ',' && depth == 0) { out.emplace_back(); continue; }
        out.back() += c;
    }
    for (auto& x : out) { size_t a = x.find_first_not_of(' '), b = x.find_last_not_of(' '); x = a == std::string::npos ? x : x.substr(a, b - a + 1); }
    return out;
}
template <class... T> void dbg_impl(int line, const char* names, const T&... vals) {
    std::vector<std::string> n = split_names(names);
    std::size_t i = 0;
    std::cerr << "[linia " << line << "] ";
    ((std::cerr << (i ? ", " : "") << (i < n.size() ? n[i] : std::string("?")) << " = ", put(std::cerr, vals), ++i), ...);
    std::cerr << std::endl;
}
}
#define dbg(...) oipg::dbg_impl(__LINE__, #__VA_ARGS__, __VA_ARGS__)
"""

_STD_NAMES = {"cout", "cin", "cerr", "endl", "vector", "string", "map", "set", "multiset", "multimap", "pair",
              "make_pair", "tuple", "make_tuple", "queue", "priority_queue", "stack", "deque", "list", "array", "bitset",
              "sort", "reverse", "unique", "lower_bound", "upper_bound", "binary_search", "min", "max", "swap",
              "min_element", "max_element", "accumulate", "fill", "iota", "next_permutation", "greater", "less", "gcd",
              "lcm", "to_string", "stoi", "stoll", "getline", "unordered_map", "unordered_set", "numeric_limits",
              "function", "mt19937", "mt19937_64", "uniform_int_distribution", "random_device", "chrono", "count",
              "find", "iota", "setprecision", "fixed", "abs", "tie", "get", "ios", "ios_base", "shuffle", "partial_sum",
              "nth_element", "stable_sort", "all_of", "any_of", "none_of", "string_view", "optional", "accumulate",
              "popcount", "__lg", "transform", "distance", "prev", "next", "move", "begin", "end"}
_TYPEISH_KW = E.TYPE_KW | {"const", "constexpr", "static", "unsigned", "signed", "long", "short", "volatile",
                           "thread_local", "inline", "struct", "class", "enum", "typename", "mutable", "register"}


# ============================================================================================ analiza fragmentu
def _repl_question_lines(code: str) -> tuple[str, list[int]]:
    """Linie `? wyrażenie` → `std::cout << (wyrażenie) << '\\n';` (liczba linii zachowana)."""
    toks = E.tokenize(code)
    lines = code.split("\n")
    hits = []
    prev_sig = None
    for t in toks:
        if t.kind == "op" and t.text == "?" and lines[t.line - 1][:t.col - 1].strip() == "":
            rest = lines[t.line - 1][t.col:]
            cut = _comment_cut(rest)
            rest = rest if cut is None else rest[:cut]
            no_colon = re.search(r"(?<!:):(?!:)", re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", rest)) is None
            if prev_sig is None or prev_sig.text in (";", "{", "}") or prev_sig.kind == "pp" or \
                    (prev_sig.line - 1) in hits or (no_colon and prev_sig.line < t.line):
                hits.append(t.line - 1)
        if t.kind != "comment":
            prev_sig = t
    for ln in hits:
        s = lines[ln]
        m = re.match(r"^(\s*)\?\s*(.*?)\s*$", s)
        if not m:
            continue
        expr = m.group(2)
        cm = ""
        # komentarz na końcu linii
        cut = _comment_cut(expr)
        if cut is not None:
            expr, cm = expr[:cut].rstrip(), " " + expr[cut:]
        expr = expr.rstrip(";").rstrip()
        if not expr:
            lines[ln] = m.group(1) + cm.strip()
            continue
        lines[ln] = f"{m.group(1)}std::cout << ({expr}) << '\\n';{cm}"
    return "\n".join(lines), [h + 1 for h in hits]


def _comment_cut(s: str):
    q = None
    i = 0
    while i < len(s):
        c = s[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif s.startswith("//", i) or s.startswith("/*", i):
            return i
        i += 1
    return None


def _items(code: str):
    """Elementy najwyższego poziomu: [{kind, first, last, text_start, text_end, line, terminated}]."""
    f = E._Fmt(code, dict(E.BASE_STYLE))
    f.parse()
    T = f.T
    items = []
    defs = {d[0]: d for d in f.defs if d[1] is not None}
    tops = getattr(f, "toplevel", [])
    for first, last in tops:
        if last < first:
            continue
        t0 = T[first]
        kind = _classify_item(f, first, last, defs)
        tl = T[last]
        end = tl.pos + len(tl.text)
        k = f.S[last] + 1
        if k < len(f.A) and f.A[k].kind == "comment" and f.nlb[k] == 0 and f.A[k].text.startswith("//"):
            end = f.A[k].pos + len(f.A[k].text)          # komentarz na końcu linii jedzie z instrukcją
        core_end = tl.pos + len(tl.text)
        items.append({"kind": kind, "first": first, "last": last, "start": t0.pos, "end": end,
                      "core": code[t0.pos:core_end], "trail": code[core_end:end],
                      "line": t0.line, "terminated": tl.text in (";", "}") or tl.kind == "pp",
                      "text": code[t0.pos:end]})
    return f, items


def _classify_item(f, first: int, last: int, defs: dict) -> str:
    T = f.T
    t = T[first]
    if t.kind == "pp":
        return "pp"
    if first in defs:
        return "def"
    x = t.text
    if t.kind == "kw" and x in ("template", "using", "typedef", "namespace", "struct", "class", "union", "enum",
                                "extern", "static_assert"):
        if x == "using" and f.tx(first + 1) == "namespace":
            return "using_ns"
        if x in ("struct", "class", "union", "enum") and not any(f.is_p(q, "{") for q in range(first, last + 1)):
            return "decl" if _looks_decl(f, first, last) else "def"
        return "def"
    if t.kind == "kw" and x in ("if", "for", "while", "do", "switch", "return", "break", "continue", "try", "throw",
                                "goto", "case", "default", "else", "delete"):
        return "stmt"
    if f.is_p(first, "{"):
        return "stmt"
    if _looks_decl(f, first, last):
        # prototyp funkcji: typ nazwa(param z typem) ;
        return "decl"
    return "stmt"


def _looks_decl(f, first: int, last: int) -> bool:
    """Czy instrukcja to deklaracja zmiennej (typ [szablon] [*&] nazwa ...)?"""
    T = f.T
    q = first
    saw_type = False
    while q <= last:
        t = T[q]
        if t.kind == "kw" and t.text in _TYPEISH_KW:
            saw_type = True
            q += 1
            continue
        if t.kind == "kw" and t.text in ("operator",):
            return True
        if t.kind == "id":
            # kwalifikowane nazwy typów std::vector<int>
            r = q
            while f.tx(r + 1) == "::" and f.kd(r + 2) == "id":
                r += 2
            if f.role[r + 1] == "tmpl_open" if r + 1 <= last else False:
                depth = 0
                s = r + 1
                while s <= last:
                    if f.role[s] == "tmpl_open":
                        depth += 1
                    elif f.role[s] == "tmpl_close":
                        depth -= f.tmpl_pops[s]
                        if depth <= 0:
                            break
                    s += 1
                r = s
                if f.tx(r + 1) == "::" and f.kd(r + 2) == "id":
                    r += 2
            nxt = r + 1
            while nxt <= last and f.role[nxt] == "ptr":
                nxt += 1
            if saw_type and r == q and f.kd(q) == "id" and f.tx(q + 1) in ("=", ";", ",", "[", "(", "{", ":"):
                return True     # int x = ..., long long a[N]
            if nxt <= last and (f.kd(nxt) == "id" or f.role[nxt] == "sbind") and \
                    f.tx(nxt + 1) in ("=", ";", ",", "[", "(", "{", ":", ")"):
                return True     # vector<int> v(n);  Node* root = ...;  T x;
            if nxt <= last and f.is_p(nxt, "[") and f.role[nxt] == "sbind":
                return True
            if saw_type and f.is_p(r + 1, "["):
                return True
            return False
        if f.role[q] == "sbind":
            return saw_type
        return False
    return False


def _uses_unqualified_std(f) -> bool:
    T = f.T
    for p, t in enumerate(T):
        if t.kind == "id" and t.text in _STD_NAMES and f.tx(p - 1) not in ("::", ".", "->"):
            return True
    return False


def _user_defines_dbg(code: str, f) -> bool:
    if re.search(r"^\s*#\s*define\s+dbg\b", code, re.M):
        return True
    T = f.T
    for p, t in enumerate(T):
        if t.kind == "id" and t.text == "dbg" and p > 0 and (T[p - 1].kind == "kw" and T[p - 1].text in
                                                              E.TYPE_KW | {"auto"} or T[p - 1].text in (">",)):
            return True
    return False


def _user_defines_container_print(f) -> bool:
    T = f.T
    for p, t in enumerate(T):
        if t.kind == "kw" and t.text == "operator" and f.tx(p + 1) == "<<":
            window = " ".join(T[q].text for q in range(p, min(len(T), p + 25)))
            if re.search(r"\b(vector|pair|set|map|deque|tuple|array)\s*<", window):
                return True
    return False


def wrap(code: str) -> dict:
    """Zamienia fragment w pełny program. Zwraca {program, notes, lang, has_main, repl_lines, stats}."""
    src = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    notes: list[str] = []
    if not src.strip():
        raise ApiError("Wpisz fragment kodu do uruchomienia.")
    if runner.detect_lang(src) == "python" and not re.search(r"#\s*include|;\s*$", src, re.M):
        return {"program": src, "lang": "python", "notes": ["Wykryto Pythona — uruchamiam skrypt bez zmian."],
                "has_main": True, "repl_lines": [], "stats": {}}
    src2, qlines = _repl_question_lines(src)
    for ln in qlines:
        notes.append(f"Linia {ln}: `? …` — wartość wyrażenia zostanie wypisana.")
    try:
        from ..core import cpp_lexer
        has_main = cpp_lexer.has_main(src2)
    except Exception:  # noqa: BLE001
        has_main = bool(re.search(r"\bint\s+main\s*\(", src2))
    try:
        f, items = _items(src2)
    except Exception:  # noqa: BLE001
        f, items = None, []
    if has_main or f is None:
        prog = src2
        head = []
        if not re.search(r"#\s*include", src2):
            head.append("#include <bits/stdc++.h>")
            notes.append("Dodano #include <bits/stdc++.h> (brakowało nagłówków).")
            if f is not None and _uses_unqualified_std(f) and "using namespace std" not in src2:
                head.append("using namespace std;")
                notes.append("Dodano using namespace std;.")
        if head:
            prog = "\n".join(head) + f'\n#line 1 "{USER_FILE}"\n' + src2
        notes.insert(0, "Kod ma funkcję main — uruchamiam go jako zwykły program." if has_main else
                     "Nie udało się przeanalizować fragmentu — uruchamiam bez zmian.")
        return {"program": prog if prog.endswith("\n") else prog + "\n", "lang": "cpp", "notes": notes,
                "has_main": has_main, "repl_lines": qlines, "stats": {}}

    # --- podział: prolog preprocesora, definicje, deklaracje, instrukcje
    leading_pp = []
    k = 0
    while k < len(items) and items[k]["kind"] == "pp":
        leading_pp.append(items[k])
        k += 1
    rest = items[k:]
    seen_stmt = False
    globals_, body = [], []
    late_decls = 0
    lambda_local = 0
    for it in rest:
        kind = it["kind"]
        if kind in ("pp", "def", "using_ns"):
            globals_.append(it)
            continue
        if kind == "decl":
            has_capture_lambda = any(f.role[q] == "lambda" and f.tx(q + 1) in ("&", "=")
                                     for q in range(it["first"], it["last"] + 1))
            if not seen_stmt and not has_capture_lambda and it["terminated"]:
                globals_.append(it)
                continue
            if has_capture_lambda:
                lambda_local += 1
            elif seen_stmt:
                late_decls += 1
            body.append(it)
            continue
        seen_stmt = True
        body.append(it)
    # REPL: ostatni element bez średnika (jeśli obejmuje kilka linii, a poprzednia linia nie ma średnika — rozdziel)
    repl_expr = None
    if body and not body[-1]["terminated"]:
        split = _split_unterminated(f, src2, body[-1])
        if split is not None:
            head_it, tail_it = split
            body[-1:] = [head_it, tail_it]
            notes.append(f"Linia {head_it['end_line']}: dopisano brakujący średnik.")
    if body and not body[-1]["terminated"]:
        last = body[-1]
        if last["kind"] == "decl":
            last["text"] = last["core"] + ";" + last["trail"]
            last["patched"] = True
            notes.append(f"Linia {last['line']}: dopisano brakujący średnik.")
        else:
            repl_expr = body.pop()
    elif globals_ and not globals_[-1]["terminated"] and globals_[-1]["kind"] == "decl":
        globals_[-1]["text"] = globals_[-1]["core"] + ";" + globals_[-1]["trail"]
        globals_[-1]["patched"] = True

    user_has_bits = any(re.search(r"#\s*include\s*<bits/stdc\+\+\.h>", it["text"]) for it in items if it["kind"] == "pp")
    uses_std = _uses_unqualified_std(f)
    has_using = "using namespace std" in src2
    out = ["// Program wygenerowany przez OI Forge Playground z Twojego fragmentu.",
           "// Dyrektywy #line sprawiają, że błędy kompilacji wskazują linie Twojego fragmentu (main.cpp)."]

    def mark(line):
        out.append(f'#line {line} "{USER_FILE}"')

    for it in leading_pp:
        mark(it["line"])
        out.append(it["text"])
    if not user_has_bits:
        out.append("#include <bits/stdc++.h>")
        notes.append("Dodano #include <bits/stdc++.h>.")
    if uses_std and not has_using:
        out.append("using namespace std;")
        notes.append("Dodano using namespace std; (fragment używa nazw z std bez przedrostka).")
    printers = not _user_defines_container_print(f)
    want_dbg = not _user_defines_dbg(src2, f)
    if printers or want_dbg:
        out.append(f'#line 1 "{PRELUDE_FILE}"')
    if printers:
        out.append(PRELUDE.rstrip())
    if want_dbg:
        out.append(DBG.rstrip())
    for ch in _chunks(src2, globals_):
        mark(ch["line"])
        out.append(_indent_first(src2, ch))
    out.append(f'#line 1 "{SKELETON_FILE}"')
    out.append("int main() {")
    for ch in _chunks(src2, body):
        mark(ch["line"])
        out.append(_indent_first(src2, ch))
    if repl_expr is not None:
        expr = repl_expr["core"].strip()
        mark(repl_expr["line"])
        out.append(f"    std::cout << ({expr}) << '\\n';")
        short = expr if len(expr) <= 60 else expr[:57] + "…"
        notes.append(f"Ostatnie wyrażenie `{short}` (bez średnika) zostanie wypisane — tryb REPL.")
    out.append(f'#line 3 "{SKELETON_FILE}"')
    out.append("    return 0;")
    out.append("}")
    n_def = sum(1 for it in globals_ if it["kind"] in ("def", "using_ns"))
    n_var = sum(1 for it in globals_ if it["kind"] == "decl")
    before = []
    if n_def:
        before.append(f"{n_def} {_pl(n_def, 'definicja', 'definicje', 'definicji')}")
    if n_var:
        before.append(f"{n_var} {_pl(n_var, 'zmienna globalna', 'zmienne globalne', 'zmiennych globalnych')}")
    nb = len(body)
    notes.insert(0, "Brak funkcji main — dodano szablon programu: "
                    + (("przed main " + " i ".join(before) + ", ") if before else "")
                    + f"w main() {nb} {_pl(nb, 'instrukcja', 'instrukcje', 'instrukcji')}"
                    + (" + wypisanie ostatniego wyrażenia." if repl_expr is not None else "."))
    if late_decls:
        notes.append("Deklaracje po pierwszej instrukcji zostały w main (mogą zależeć od wcześniejszych instrukcji).")
    if lambda_local:
        notes.append("Lambdy z przechwytywaniem [&]/[=] zostały w main (poza funkcją nie wolno przechwytywać).")
    if not body and repl_expr is None:
        notes.append("Brak instrukcji do wykonania — dopisz wywołanie funkcji albo wyrażenie bez średnika, "
                     "którego wartość chcesz zobaczyć.")
    if printers:
        notes.append("Możesz wypisać kontener jednym `cout << v` (vector, set, map, pair, tuple, deque, queue…).")
    if want_dbg:
        notes.append("`dbg(a, b, …)` wypisuje nazwy i wartości zmiennych na stderr (z numerem linii).")
    program = "\n".join(out) + "\n"
    return {"program": program, "pretty": _pretty(program), "lang": "cpp", "notes": notes, "has_main": False,
            "repl_lines": qlines, "repl": repl_expr["core"].strip() if repl_expr else None,
            "stats": {"globals": n_def, "statements": len(body), "pp": len(leading_pp) +
                      sum(1 for it in globals_ if it["kind"] == "pp")}}


_EXPR_END = ("id", "num", "str", "char")


def _split_unterminated(f, code: str, it: dict):
    """`int x = 3⏎x` → (`int x = 3;`, `x`): dzieli niezakończony element w ostatnim sensownym łamaniu linii."""
    T = f.T
    first, last = it["first"], it["last"]
    for j in range(last, first, -1):
        if f.nlb[f.S[j]] == 0:
            continue
        a, b = T[j - 1], T[j]
        a_ends = a.kind in _EXPR_END or (a.kind == "punct" and a.text in (")", "]", "}")) or \
            (a.kind == "kw" and a.text in ("true", "false", "nullptr", "this")) or f.role[j - 1] in ("postfix", "tmpl_close")
        b_starts = b.kind in _EXPR_END or (b.kind == "punct" and b.text in ("(", "{")) or \
            (b.kind == "kw" and b.text in ("true", "false", "nullptr", "this", "sizeof", "new")) or \
            (b.kind == "op" and f.role[j] in ("unary", "prefix") and b.text != "&")
        if not (a_ends and b_starts):
            continue
        if any(f.is_p(q, "{") and f.bkind[q] in E.BLOCKISH for q in range(first, last + 1)):
            return None
        head = dict(it)
        hcore = code[it["start"]:a.pos + len(a.text)]
        head.update(last=j - 1, end=a.pos + len(a.text), core=hcore, trail="", text=hcore + ";",
                    terminated=True, patched=True, end_line=a.line)
        head["kind"] = "decl" if _looks_decl(f, first, j - 1) else "stmt"
        tail = dict(it)
        tcore = code[b.pos:it["start"] + len(it["core"])]
        tail.update(first=j, start=b.pos, line=b.line, core=tcore, text=tcore + it["trail"], kind="stmt",
                    terminated=False)
        return head, tail
    return None


def _pretty(program: str) -> str:
    """Wersja do podglądu: bez dyrektyw #line, z nagłówkiem zwiniętym do komentarza, sformatowana."""
    text = re.sub(r'^#line \d+ "[^"]*"\n', "", program, flags=re.M)
    text = re.sub(r"^// Dyrektywy #line.*\n", "", text, flags=re.M)
    a = text.find(PRELUDE.split("\n", 1)[0])
    if a >= 0:
        b_marker = DBG.rstrip().rsplit("\n", 1)[-1]
        b = text.find(b_marker)
        end = b + len(b_marker) if b >= 0 else text.find("\n", text.find(PRELUDE.rstrip().rsplit("\n", 1)[-1], a)) + 1
        if end > a:
            text = (text[:a] + "// [nagłówek OI Forge Playground: operator<< dla kontenerów, __int128 i dbg(...)]"
                    + text[end:])
    try:
        from .formatter import format_code
        return format_code(text)
    except Exception:  # noqa: BLE001
        return text


def _chunks(code: str, items: list[dict]) -> list[dict]:
    """Łączy kolejne elementy, między którymi w oryginale są tylko białe znaki/komentarze."""
    out: list[dict] = []
    for it in items:
        if out and it["kind"] != "pp" and out[-1]["kind"] != "pp" and not out[-1].get("patched") \
                and not it.get("patched"):
            gap = code[out[-1]["end"]:it["start"]]
            if not gap.strip() or all(t.kind == "comment" for t in E.tokenize(gap)):
                prev = out[-1]
                prev["text"] = prev["text"] + gap + it["text"]
                prev["end"] = it["end"]
                continue
        out.append(dict(it))
    return out


def _pl(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _indent_first(code: str, it: dict, min_indent: int = 0) -> str:
    """Tekst elementu z oryginalnym wcięciem pierwszej linii (żeby #line i kolumny błędów się zgadzały)."""
    ls = code.rfind("\n", 0, it["start"]) + 1
    lead = code[ls:it["start"]]
    if lead.strip():
        lead = " " * len(lead)
    text = it["text"]
    if min_indent and len(lead.expandtabs(4)) < min_indent and "\n" not in text:
        lead = " " * min_indent
    return lead + text


# ============================================================================================ HTTP
def _code(body: dict) -> str:
    code = body.get("code")
    if code is None or not str(code).strip():
        raise ApiError("Wpisz fragment kodu do uruchomienia.")
    code = str(code)
    if len(code) > MAX_CODE:
        raise ApiError("Fragment jest za duży (limit 400 kB).")
    return code


def handle_wrap(body: dict) -> dict:
    return wrap(_code(body))


def handle_run(body: dict) -> dict:
    w = wrap(_code(body))
    try:
        s = store.settings()
    except Exception:  # noqa: BLE001
        s = {}
    timeout = body.get("timeout") or s.get("time_limit") or 2.0
    try:
        timeout = max(0.1, min(float(timeout), 30.0))
    except (TypeError, ValueError):
        timeout = 2.0
    mem = body.get("memory_limit") or s.get("memory_limit")
    mode = body.get("mode") or "release"
    std = body.get("std") or s.get("cpp_std") or "c++20"
    res = runner.run_source(w["program"], str(body.get("input") or ""), lang=w["lang"], mode=mode, timeout=timeout,
                            std=std, mem_limit_mb=mem)
    out = dict(w)
    out.update({"compile": res.get("compile"), "run": res.get("run"),
                "success": bool(res.get("run")) and res["run"].get("verdict") == "OK"})
    return out


def load_examples() -> list[dict]:
    try:
        data = json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("examples", data) if isinstance(data, dict) else data
    return [e for e in items if isinstance(e, dict) and e.get("code")]


def handle_examples(_q: dict) -> dict:
    return {"examples": load_examples()}


ROUTES = {
    "POST /api/playground/wrap": handle_wrap,
    "POST /api/playground/run": handle_run,
    "GET /api/playground/examples": handle_examples,
}

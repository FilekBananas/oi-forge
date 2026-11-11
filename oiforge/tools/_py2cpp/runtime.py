"""Biblioteka pomocnicza C++ emitowana do tłumaczenia — tylko te funkcje, których program używa.

Każdy helper ma kod (z `INT_T` zamienianym na wybrany typ całkowity) i listę zależności.
Kolejność w `ORDER` jest topologiczna (szablony muszą widzieć wcześniej zadeklarowane przeciążenia).
"""
from __future__ import annotations

H: dict[str, tuple[str, tuple[str, ...]]] = {}


def _h(name: str, code: str, deps: tuple[str, ...] = ()) -> None:
    H[name] = (code.strip("\n"), deps)


_h("floordiv", r"""
// a // b z Pythona: zaokrągla w dół także dla ujemnych (-7 // 2 == -4, w C++ -7 / 2 == -3)
long long floordiv(long long a, long long b) {
    long long q = a / b;
    if ((a % b != 0) && ((a < 0) != (b < 0))) q--;
    return q;
}""")

_h("pymod", r"""
// a % b z Pythona: wynik ma znak dzielnika (-7 % 3 == 2, w C++ -7 % 3 == -1)
long long pymod(long long a, long long b) {
    long long r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) r += b;
    return r;
}""")

_h("fpymod", r"""
// a % b dla liczb rzeczywistych z semantyką Pythona
double fpymod(double a, double b) {
    double r = fmod(a, b);
    if (r != 0 && ((r < 0) != (b < 0))) r += b;
    return r;
}""")

_h("ipow", r"""
// a ** e dla liczb całkowitych (szybkie potęgowanie). Uwaga: wynik musi zmieścić się w 64 bitach!
long long ipow(long long a, long long e) {
    long long r = 1;
    while (e > 0) {
        if (e & 1) r *= a;
        e >>= 1;
        if (e) a *= a;
    }
    return r;
}""")

_h("powmod", r"""
// pow(a, e, m) z Pythona: a^e mod m w O(log e), iloczyny w __int128
long long powmod(long long a, long long e, long long m) {
    a %= m;
    if (a < 0) a += m;
    long long r = 1 % m;
    while (e > 0) {
        if (e & 1) r = (__int128)r * a % m;
        a = (__int128)a * a % m;
        e >>= 1;
    }
    return r;
}""")

_h("input", r"""
// input() z Pythona: cała linia bez '\n'; na końcu wejścia wyjątek (jak EOFError)
string input() {
    string s;
    if (!getline(cin, s)) throw runtime_error("EOFError");
    if (!s.empty() && s.back() == '\r') s.pop_back();
    return s;
}""")

_h("skipLine", r"""
// Po `cin >> ...` pomija resztę linii — w Pythonie input() zawsze zjada całą linię
void skipLine() {
    cin.ignore(numeric_limits<streamsize>::max(), '\n');
}""")

_h("readline", r"""
// sys.stdin.readline(): linia RAZEM z '\n'; na końcu wejścia pusty napis
string readline() {
    string s;
    if (!getline(cin, s)) return "";
    if (!s.empty() && s.back() == '\r') s.pop_back();
    if (!cin.eof()) s += '\n';
    return s;
}""")

_h("readLines", r"""
// sys.stdin.readlines() / list(sys.stdin): wszystkie linie (z '\n')
vector<string> readLines() {
    vector<string> lines;
    for (string s; !(s = readline()).empty();) lines.push_back(s);
    return lines;
}""", ("readline",))

_h("readAll", r"""
// sys.stdin.read(): całe pozostałe wejście jako jeden napis
string readAll() {
    return string(istreambuf_iterator<char>(cin), istreambuf_iterator<char>());
}""")

_h("readAllTokens", r"""
// sys.stdin.read().split(): wszystkie tokeny wejścia
vector<string> readAllTokens() {
    vector<string> tokens;
    for (string t; cin >> t;) tokens.push_back(t);
    return tokens;
}""")

_h("nextToken", r"""
// next(it) dla it = iter(sys.stdin.read().split()): kolejny token wejścia
string nextToken() {
    string t;
    if (!(cin >> t)) throw runtime_error("StopIteration");
    return t;
}""")

_h("nextInt", r"""
// int(next(it)) dla iteratora po tokenach wejścia
long long nextInt() {
    long long x;
    if (!(cin >> x)) throw runtime_error("StopIteration");
    return x;
}""")

_h("readInts", r"""
// list(map(int, input().split())): wszystkie liczby całkowite z jednej linii
vector<INT_T> readInts() {
    vector<INT_T> v;
    string s = input();
    const char* p = s.c_str();
    char* end;
    while (true) {
        long long x = strtoll(p, &end, 10);
        if (end == p) break;
        v.push_back(x);
        p = end;
    }
    return v;
}""", ("input",))

_h("readFloats", r"""
// list(map(float, input().split())): liczby rzeczywiste z jednej linii
vector<double> readFloats() {
    vector<double> v;
    string s = input();
    const char* p = s.c_str();
    char* end;
    while (true) {
        double x = strtod(p, &end);
        if (end == p) break;
        v.push_back(x);
        p = end;
    }
    return v;
}""", ("input",))

_h("split", r"""
// str.split() bez argumentu: dzieli po białych znakach, pomija puste kawałki
vector<string> split(const string& s) {
    vector<string> out;
    size_t i = 0, n = s.size();
    while (i < n) {
        while (i < n && isspace((unsigned char)s[i])) i++;
        size_t j = i;
        while (j < n && !isspace((unsigned char)s[j])) j++;
        if (j > i) out.push_back(s.substr(i, j - i));
        i = j;
    }
    return out;
}""")

_h("splitSep", r"""
// str.split(sep): dzieli po separatorze (puste kawałki zostają, jak w Pythonie)
vector<string> split(const string& s, const string& sep) {
    vector<string> out;
    size_t start = 0, pos;
    while ((pos = s.find(sep, start)) != string::npos) {
        out.push_back(s.substr(start, pos - start));
        start = pos + sep.size();
    }
    out.push_back(s.substr(start));
    return out;
}""")

_h("strip", r"""
// str.strip(): usuwa białe znaki (albo podane znaki) z obu końców
string strip(const string& s, const string& chars = " \t\n\r\v\f") {
    size_t b = s.find_first_not_of(chars);
    if (b == string::npos) return "";
    size_t e = s.find_last_not_of(chars);
    return s.substr(b, e - b + 1);
}""")

_h("lstrip", r"""
// str.lstrip(): usuwa białe znaki z lewej
string lstrip(const string& s, const string& chars = " \t\n\r\v\f") {
    size_t b = s.find_first_not_of(chars);
    return b == string::npos ? "" : s.substr(b);
}""")

_h("rstrip", r"""
// str.rstrip(): usuwa białe znaki z prawej
string rstrip(const string& s, const string& chars = " \t\n\r\v\f") {
    size_t e = s.find_last_not_of(chars);
    return e == string::npos ? "" : s.substr(0, e + 1);
}""")

_h("upper", r"""
// str.upper()
string upper(string s) {
    for (char& c : s) c = toupper((unsigned char)c);
    return s;
}""")

_h("lower", r"""
// str.lower()
string lower(string s) {
    for (char& c : s) c = tolower((unsigned char)c);
    return s;
}""")

_h("swapcase", r"""
// str.swapcase()
string swapcase(string s) {
    for (char& c : s) c = isupper((unsigned char)c) ? tolower((unsigned char)c) : toupper((unsigned char)c);
    return s;
}""")

_h("title", r"""
// str.title(): pierwsza litera każdego słowa wielka
string title(string s) {
    bool prev = false;
    for (char& c : s) {
        bool alpha = isalpha((unsigned char)c);
        c = alpha ? (prev ? tolower((unsigned char)c) : toupper((unsigned char)c)) : c;
        prev = alpha;
    }
    return s;
}""")

_h("capitalize", r"""
// str.capitalize(): pierwsza litera wielka, reszta małe
string capitalize(string s) {
    for (size_t i = 0; i < s.size(); i++) s[i] = i ? tolower((unsigned char)s[i]) : toupper((unsigned char)s[i]);
    return s;
}""")

_h("replaceAll", r"""
// str.replace(a, b): zamienia wszystkie wystąpienia
string replaceAll(string s, const string& from, const string& to, long long limit = -1) {
    if (from.empty()) return s;
    size_t pos = 0;
    while (limit != 0 && (pos = s.find(from, pos)) != string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
        if (limit > 0) limit--;
    }
    return s;
}""")

_h("countSub", r"""
// str.count(t): liczba nienakładających się wystąpień
long long countSub(const string& s, const string& t) {
    if (t.empty()) return (long long)s.size() + 1;
    long long cnt = 0;
    for (size_t pos = s.find(t); pos != string::npos; pos = s.find(t, pos + t.size())) cnt++;
    return cnt;
}""")

_h("strPred", r"""
// str.isdigit() / isalpha() / ...: niepusty napis, w którym każdy znak spełnia warunek
bool allChars(const string& s, int (*pred)(int)) {
    if (s.empty()) return false;
    for (unsigned char c : s)
        if (!pred(c)) return false;
    return true;
}""")

_h("caseCheck", r"""
// str.isupper() / islower(): jest litera i wszystkie litery mają daną wielkość
bool caseCheck(const string& s, bool up) {
    bool any = false;
    for (unsigned char c : s) {
        if (isalpha(c)) {
            any = true;
            if (up ? islower(c) : isupper(c)) return false;
        }
    }
    return any;
}""")

_h("repeatStr", r"""
// s * n dla napisów
string repeatStr(const string& s, long long n) {
    string out;
    for (long long i = 0; i < n; i++) out += s;
    return out;
}""")

_h("repeatVec", r"""
// lista * n
template<class T> vector<T> repeatVec(const vector<T>& v, long long n) {
    vector<T> out;
    for (long long i = 0; i < n; i++) out.insert(out.end(), v.begin(), v.end());
    return out;
}""")

_h("concat", r"""
// a + b dla list
template<class T> vector<T> concat(vector<T> a, const vector<T>& b) {
    a.insert(a.end(), b.begin(), b.end());
    return a;
}""")

_h("pad", r"""
// str.ljust / rjust / center / zfill
string ljust(const string& s, long long w, char f = ' ') { return (long long)s.size() >= w ? s : s + string(w - s.size(), f); }
string rjust(const string& s, long long w, char f = ' ') { return (long long)s.size() >= w ? s : string(w - s.size(), f) + s; }
string center(const string& s, long long w, char f = ' ') {
    if ((long long)s.size() >= w) return s;
    long long total = w - s.size(), left = total / 2 + (total & w & 1);
    return string(left, f) + s + string(total - left, f);
}
string zfill(const string& s, long long w) {
    if ((long long)s.size() >= w) return s;
    size_t sign = (!s.empty() && (s[0] == '-' || s[0] == '+')) ? 1 : 0;
    return s.substr(0, sign) + string(w - s.size(), '0') + s.substr(sign);
}""")

_h("strcore", r"""
// str(x) jak w Pythonie (liczby rzeczywiste: najkrótszy zapis, np. 0.1 + 0.2 -> 0.30000000000000004)
string str(const string& s) { return s; }
string str(const char* s) { return s; }
string str(char c) { return string(1, c); }
string str(bool b) { return b ? "True" : "False"; }
template<class T> requires is_integral_v<T> string str(T x) { return to_string(x); }
string str(double x) {
    if (std::isnan(x)) return "nan";
    if (std::isinf(x)) return x > 0 ? "inf" : "-inf";
    if (x == 0) return std::signbit(x) ? "-0.0" : "0.0";
    char buf[64];
#if defined(__cpp_lib_to_chars)
    string s(buf, to_chars(buf, buf + sizeof buf, x, chars_format::scientific).ptr);
#else
    for (int p = 1; p <= 17; p++) {
        snprintf(buf, sizeof buf, "%.*e", p - 1, x);
        if (strtod(buf, nullptr) == x) break;
    }
    string s = buf;
#endif
    bool neg = s[0] == '-';
    if (neg) s.erase(0, 1);
    size_t e = s.find('e');
    int ex = stoi(s.substr(e + 1));
    string digits;
    for (char c : s.substr(0, e))
        if (c != '.') digits += c;
    while (digits.size() > 1 && digits.back() == '0') digits.pop_back();
    int n = digits.size();
    string out;
    if (ex < -4 || ex >= 16) {
        out = digits.substr(0, 1) + (n > 1 ? "." + digits.substr(1) : "");
        char eb[16];
        snprintf(eb, sizeof eb, "e%c%02d", ex < 0 ? '-' : '+', ex < 0 ? -ex : ex);
        out += eb;
    } else if (ex < 0) {
        out = "0." + string(-ex - 1, '0') + digits;
    } else if (ex + 1 >= n) {
        out = digits + string(ex + 1 - n, '0') + ".0";
    } else {
        out = digits.substr(0, ex + 1) + "." + digits.substr(ex + 1);
    }
    return neg ? "-" + out : out;
}""")

_h("reprcore", r"""
// repr(x) / str(kontener) jak w Pythonie: [1, 2], (1, 'a'), {1: 2}, {1, 2}
string repr(const string& s) {
    char q = (s.find('\'') != string::npos && s.find('"') == string::npos) ? '"' : '\'';
    string out(1, q);
    for (unsigned char c : s) {
        if (c == '\\') out += "\\\\";
        else if (c == (unsigned char)q) { out += '\\'; out += q; }
        else if (c == '\n') out += "\\n";
        else if (c == '\t') out += "\\t";
        else if (c == '\r') out += "\\r";
        else if (c < 32 || c == 127) { char b[8]; snprintf(b, sizeof b, "\\x%02x", c); out += b; }
        else out += (char)c;
    }
    return out + q;
}
string repr(char c) { return repr(string(1, c)); }
string repr(const char* s) { return repr(string(s)); }
template<class T> string repr(const T& x);
template<class A, class B> string repr(const pair<A, B>& p);
template<class... T> string repr(const tuple<T...>& t);
template<class T> string repr(const vector<T>& v);
template<class T> string repr(const deque<T>& v);
template<class T, class C> string repr(const set<T, C>& s);
template<class T> string repr(const unordered_set<T>& s);
template<class K, class V, class C> string repr(const map<K, V, C>& m);
template<class K, class V> string repr(const unordered_map<K, V>& m);
template<class T> string repr(const optional<T>& o);
template<class T> string repr(const T& x) { return str(x); }
template<class C> string reprItems(const C& c, const string& open, const string& close) {
    string out = open;
    bool first = true;
    for (const auto& x : c) {
        if (!first) out += ", ";
        out += repr(x);
        first = false;
    }
    return out + close;
}
template<class C> string reprMap(const C& m) {
    string out = "{";
    bool first = true;
    for (const auto& [k, v] : m) {
        if (!first) out += ", ";
        out += repr(k) + ": " + repr(v);
        first = false;
    }
    return out + "}";
}
template<class A, class B> string repr(const pair<A, B>& p) { return "(" + repr(p.first) + ", " + repr(p.second) + ")"; }
template<class... T> string repr(const tuple<T...>& t) {
    string out = "(";
    bool first = true;
    apply([&](const auto&... xs) { ((out += (first ? "" : ", ") + repr(xs), first = false), ...); }, t);
    return out + (sizeof...(T) == 1 ? ",)" : ")");
}
template<class T> string repr(const vector<T>& v) { return reprItems(v, "[", "]"); }
template<class T> string repr(const deque<T>& v) { return reprItems(v, "deque([", "])"); }
template<class T, class C> string repr(const set<T, C>& s) { return s.empty() ? "set()" : reprItems(s, "{", "}"); }
template<class T> string repr(const unordered_set<T>& s) { return s.empty() ? "set()" : reprItems(s, "{", "}"); }
template<class K, class V, class C> string repr(const map<K, V, C>& m) { return reprMap(m); }
template<class K, class V> string repr(const unordered_map<K, V>& m) { return reprMap(m); }
template<class T> string repr(const optional<T>& o) { return o ? repr(*o) : "None"; }
template<class A, class B> string str(const pair<A, B>& p) { return repr(p); }
template<class... T> string str(const tuple<T...>& t) { return repr(t); }
template<class T> string str(const vector<T>& v) { return repr(v); }
template<class T> string str(const deque<T>& v) { return repr(v); }
template<class T, class C> string str(const set<T, C>& s) { return repr(s); }
template<class T> string str(const unordered_set<T>& s) { return repr(s); }
template<class K, class V, class C> string str(const map<K, V, C>& m) { return repr(m); }
template<class K, class V> string str(const unordered_map<K, V>& m) { return repr(m); }
template<class T> string str(const optional<T>& o) { return o ? str(*o) : "None"; }
template<class T> string reprAsTuple(const vector<T>& v) { return v.size() == 1 ? "(" + repr(v[0]) + ",)" : reprItems(v, "(", ")"); }""", ("strcore",))

_h("join", r"""
// sep.join(elementy): skleja elementy (zamienione na napisy) separatorem
template<class C> string join(const C& items, const string& sep) {
    string out;
    bool first = true;
    for (const auto& x : items) {
        if (!first) out += sep;
        out += str(x);
        first = false;
    }
    return out;
}""", ("strcore",))

_h("fmt", r"""
// format(x, spec) / f"{x:spec}" — mini-język formatowania Pythona: [[fill]align][sign][0][width][,][.prec][type]
struct FmtSpec { char fill = ' ', align = 0, sign = '-', group = 0, type = 0; bool alt = false, zero = false, fillSet = false; int width = 0, prec = -1; };
FmtSpec parseSpec(const string& s) {
    FmtSpec f;
    size_t i = 0, n = s.size();
    auto isAlign = [](char c) { return c == '<' || c == '>' || c == '^' || c == '='; };
    if (n >= 2 && isAlign(s[1])) { f.fill = s[0]; f.align = s[1]; f.fillSet = true; i = 2; }
    else if (n >= 1 && isAlign(s[0])) { f.align = s[0]; i = 1; }
    if (i < n && (s[i] == '+' || s[i] == '-' || s[i] == ' ')) f.sign = s[i++];
    if (i < n && s[i] == '#') { f.alt = true; i++; }
    if (i < n && s[i] == '0') { f.zero = true; i++; }
    while (i < n && isdigit((unsigned char)s[i])) f.width = f.width * 10 + (s[i++] - '0');
    if (i < n && (s[i] == ',' || s[i] == '_')) f.group = s[i++];
    if (i < n && s[i] == '.') { i++; f.prec = 0; while (i < n && isdigit((unsigned char)s[i])) f.prec = f.prec * 10 + (s[i++] - '0'); }
    if (i < n) f.type = s[i];
    return f;
}
string fmtPad(const string& sign, const string& body, const FmtSpec& f, bool numeric) {
    char align = f.align, fill = f.fill;
    if (f.zero && !f.fillSet) fill = '0';
    if (!align) align = (f.zero && numeric) ? '=' : (numeric ? '>' : '<');
    long long len = sign.size() + body.size();
    if (len >= f.width) return sign + body;
    long long pad = f.width - len;
    if (align == '<') return sign + body + string(pad, fill);
    if (align == '>') return string(pad, fill) + sign + body;
    if (align == '=') return sign + string(pad, fill) + body;
    return string(pad / 2, fill) + sign + body + string(pad - pad / 2, fill);
}
string fmtGroup(string digits, char g) {
    if (!g) return digits;
    size_t dot = digits.find_first_of(".eE%");
    string head = digits.substr(0, dot), tail = dot == string::npos ? "" : digits.substr(dot);
    string out;
    for (size_t i = 0; i < head.size(); i++) {
        if (i && (head.size() - i) % 3 == 0) out += g;
        out += head[i];
    }
    return out + tail;
}
string fmt(double x, const string& spec);
string fmt(long long x, const string& spec) {
    FmtSpec f = parseSpec(spec);
    char t = f.type;
    if (t == 'e' || t == 'E' || t == 'f' || t == 'F' || t == 'g' || t == 'G' || t == '%') return fmt((double)x, spec);
    if (t == 'c') return fmtPad("", string(1, (char)x), f, false);
    string sign = x < 0 ? "-" : (f.sign == '+' ? "+" : (f.sign == ' ' ? " " : ""));
    unsigned long long u = x < 0 ? 0ULL - (unsigned long long)x : (unsigned long long)x;
    int base = (t == 'x' || t == 'X') ? 16 : t == 'o' ? 8 : t == 'b' ? 2 : 10;
    string body;
    do { body += "0123456789abcdef"[u % base]; u /= base; } while (u);
    reverse(body.begin(), body.end());
    if (t == 'X') for (char& c : body) c = toupper((unsigned char)c);
    if (base == 10) body = fmtGroup(body, f.group);
    if (f.alt && base != 10) sign += string("0") + (t == 'x' ? 'x' : t == 'X' ? 'X' : t == 'o' ? 'o' : 'b');
    return fmtPad(sign, body, f, true);
}
string fmt(int x, const string& spec) { return fmt((long long)x, spec); }
string fmt(bool b, const string& spec) { return spec.empty() ? str(b) : fmt((long long)b, spec); }
string fmt(double x, const string& spec) {
    FmtSpec f = parseSpec(spec);
    char t = f.type;
    string sign = std::signbit(x) && !std::isnan(x) ? "-" : (f.sign == '+' ? "+" : (f.sign == ' ' ? " " : ""));
    double a = fabs(x);
    string body;
    char buf[512];
    if (std::isinf(a) || std::isnan(a)) {
        body = std::isinf(a) ? "inf" : "nan";
        if (t == 'F' || t == 'E' || t == 'G') for (char& c : body) c = toupper((unsigned char)c);
        if (t == '%') body += "%";
    } else if (t == 'f' || t == 'F' || t == 'e' || t == 'E' || t == '%') {
        int p = f.prec < 0 ? 6 : f.prec;
        double v = t == '%' ? a * 100 : a;
        snprintf(buf, sizeof buf, (string("%") + (f.alt ? "#" : "") + ".*" + (t == '%' ? 'f' : t)).c_str(), p, v);
        body = buf;
        if (t == '%') body += "%";
    } else if (t == 'g' || t == 'G' || (t == 0 && f.prec >= 0)) {
        int p = f.prec < 0 ? 6 : (f.prec == 0 ? 1 : f.prec);
        snprintf(buf, sizeof buf, "%.*e", p - 1, a);
        int ex = atoi(strchr(buf, 'e') + 1);
        if (ex < -4 || ex >= (t == 0 ? p - 1 : p)) {
            string s = buf;
            size_t e = s.find('e');
            string m = s.substr(0, e);
            if (!f.alt && m.find('.') != string::npos) { while (m.back() == '0') m.pop_back(); if (m.back() == '.') m.pop_back(); }
            char eb[16];
            snprintf(eb, sizeof eb, "e%c%02d", ex < 0 ? '-' : '+', ex < 0 ? -ex : ex);
            body = m + eb;
        } else {
            snprintf(buf, sizeof buf, "%.*f", max(0, p - 1 - ex), a);
            body = buf;
            if (!f.alt && body.find('.') != string::npos) { while (body.back() == '0') body.pop_back(); if (body.back() == '.') body.pop_back(); }
            if (t == 0 && body.find('.') == string::npos) body += ".0";
        }
        if (t == 'G') for (char& c : body) c = toupper((unsigned char)c);
    } else {
        body = str(a);
    }
    body = fmtGroup(body, f.group);
    return fmtPad(sign, body, f, true);
}
string fmt(const string& s, const string& spec) {
    FmtSpec f = parseSpec(spec);
    return fmtPad("", f.prec >= 0 ? s.substr(0, f.prec) : s, f, false);
}
string fmt(const char* s, const string& spec) { return fmt(string(s), spec); }
string fmt(char c, const string& spec) { return fmt(string(1, c), spec); }""", ("strcore",))

_h("noIdx", r"""
// brak granicy w wycinku (a[i:] / a[::-1])
const long long NO_IDX = LLONG_MIN;""")

_h("slice", r"""
// a[start:stop:step] z Pythona (ujemne indeksy liczone od końca, granice przycinane)
template<class C> C pySlice(const C& v, long long start, long long stop = NO_IDX, long long step = 1) {
    long long n = v.size();
    C out;
    if (step > 0) {
        start = start == NO_IDX ? 0 : (start < 0 ? max(0LL, start + n) : min(start, n));
        stop = stop == NO_IDX ? n : (stop < 0 ? max(0LL, stop + n) : min(stop, n));
        if (step == 1) return start < stop ? C(v.begin() + start, v.begin() + stop) : C();
        for (long long i = start; i < stop; i += step) out.push_back(v[i]);
    } else {
        start = start == NO_IDX ? n - 1 : (start < 0 ? max(-1LL, start + n) : min(start, n - 1));
        stop = stop == NO_IDX ? -1 : (stop < 0 ? max(-1LL, stop + n) : min(stop, n - 1));
        for (long long i = start; i > stop; i += step) out.push_back(v[i]);
    }
    return out;
}""", ("noIdx",))

_h("sorted", r"""
// sorted(x): posortowana kopia (jako wektor)
template<class C> auto sorted(const C& c) {
    vector<typename C::value_type> v(c.begin(), c.end());
    sort(v.begin(), v.end());
    return v;
}""")

_h("popBack", r"""
// x = a.pop(): zdejmuje i zwraca ostatni element
template<class C> auto popBack(C& c) {
    auto x = c.back();
    c.pop_back();
    return x;
}""")

_h("popFront", r"""
// x = q.popleft(): zdejmuje i zwraca pierwszy element
template<class C> auto popFront(C& c) {
    auto x = c.front();
    c.pop_front();
    return x;
}""")

_h("popAt", r"""
// x = a.pop(i): usuwa i zwraca element o indeksie i (O(n))
template<class T> T popAt(vector<T>& v, long long i) {
    if (i < 0) i += v.size();
    T x = v[i];
    v.erase(v.begin() + i);
    return x;
}""")

_h("pqPop", r"""
// heappop(h) dla priority_queue: zdejmuje i zwraca najmniejszy element
template<class Q> auto pqPop(Q& q) {
    auto x = q.top();
    q.pop();
    return x;
}""")

_h("vecHeap", r"""
// heapq na zwykłym wektorze — kopiec MINIMALNY jak w Pythonie
template<class T> void heappush(vector<T>& h, type_identity_t<T> x) {
    h.push_back(x);
    push_heap(h.begin(), h.end(), greater<T>());
}
template<class T> T heappop(vector<T>& h) {
    pop_heap(h.begin(), h.end(), greater<T>());
    T x = h.back();
    h.pop_back();
    return x;
}
template<class T> void heapify(vector<T>& h) {
    make_heap(h.begin(), h.end(), greater<T>());
}""")

_h("dget", r"""
// d.get(k, domyślna): wartość albo domyślna, bez wstawiania klucza
template<class M, class K> typename M::mapped_type dget(const M& m, const K& k, const typename M::mapped_type& def = {}) {
    auto it = m.find(k);
    return it == m.end() ? def : it->second;
}""")

_h("setops", r"""
// operacje na zbiorach: a | b, a & b, a - b, a ^ b
template<class S> S setUnion(S a, const S& b) { a.insert(b.begin(), b.end()); return a; }
template<class S> S setInter(const S& a, const S& b) { S r; for (const auto& x : a) if (b.count(x)) r.insert(x); return r; }
template<class S> S setDiff(const S& a, const S& b) { S r; for (const auto& x : a) if (!b.count(x)) r.insert(x); return r; }
template<class S> S setSymDiff(const S& a, const S& b) {
    S r;
    for (const auto& x : a) if (!b.count(x)) r.insert(x);
    for (const auto& x : b) if (!a.count(x)) r.insert(x);
    return r;
}""")

_h("isSubset", r"""
// a <= b dla zbiorów (issubset)
template<class S> bool isSubset(const S& a, const S& b) {
    for (const auto& x : a) if (!b.count(x)) return false;
    return true;
}""")

_h("mostCommon", r"""
// Counter.most_common(k): pary (element, liczność) malejąco po liczności
template<class M> vector<pair<typename M::key_type, long long>> mostCommon(const M& m, long long k = -1) {
    vector<pair<typename M::key_type, long long>> v(m.begin(), m.end());
    stable_sort(v.begin(), v.end(), [](const auto& a, const auto& b) { return a.second > b.second; });
    if (k >= 0 && k < (long long)v.size()) v.resize(k);
    return v;
}""")

_h("keys", r"""
// list(d) / list(d.keys()): klucze słownika jako wektor
template<class M> vector<typename M::key_type> keys(const M& m) {
    vector<typename M::key_type> v;
    for (const auto& [k, _] : m) v.push_back(k);
    return v;
}""")

_h("values", r"""
// list(d.values()): wartości słownika jako wektor
template<class M> vector<typename M::mapped_type> values(const M& m) {
    vector<typename M::mapped_type> v;
    for (const auto& [_, x] : m) v.push_back(x);
    return v;
}""")

_h("rangeVec", r"""
// list(range(a, b, step))
vector<INT_T> rangeVec(long long a, long long b, long long step = 1) {
    vector<INT_T> v;
    for (long long i = a; step > 0 ? i < b : i > b; i += step) v.push_back(i);
    return v;
}""")

_h("isqrt", r"""
// math.isqrt(n): podłoga z pierwiastka (dokładnie, bez błędów double)
long long isqrt(long long n) {
    long long r = sqrtl((long double)n);
    while (r * r > n) r--;
    while ((r + 1) * (r + 1) <= n) r++;
    return r;
}""")

_h("comb", r"""
// math.comb(n, k): symbol Newtona (iloczyny w __int128)
long long comb(long long n, long long k) {
    if (k < 0 || k > n) return 0;
    k = min(k, n - k);
    __int128 r = 1;
    for (long long i = 1; i <= k; i++) r = r * (n - k + i) / i;
    return (long long)r;
}""")

_h("perm", r"""
// math.perm(n, k) = n! / (n-k)!
long long perm(long long n, long long k) {
    if (k < 0 || k > n) return 0;
    long long r = 1;
    for (long long i = 0; i < k; i++) r *= n - i;
    return r;
}""")

_h("factorial", r"""
// math.factorial(n). Uwaga: 21! nie mieści się w 64 bitach!
long long factorial(long long n) {
    long long r = 1;
    for (long long i = 2; i <= n; i++) r *= i;
    return r;
}""")

_h("pyround", r"""
// round(x, n) — zaokrąglenie do n miejsc (połówki do parzystej, jak w Pythonie)
double pyround(double x, long long n) {
    double p = pow(10.0, (double)n);
    return nearbyint(x * p) / p;
}""")

_h("toBase", r"""
// bin(x) / hex(x) / oct(x): zapis z przedrostkiem 0b / 0x / 0o
string toBase(long long x, int base) {
    unsigned long long u = x < 0 ? 0ULL - (unsigned long long)x : (unsigned long long)x;
    string s;
    do { s += "0123456789abcdef"[u % base]; u /= base; } while (u);
    s += base == 2 ? "b0" : base == 16 ? "x0" : "o0";
    if (x < 0) s += '-';
    reverse(s.begin(), s.end());
    return s;
}""")

_h("permutations", r"""
// itertools.permutations(a, r): w kolejności jak w Pythonie
template<class T> vector<vector<T>> permutations(const vector<T>& a, long long r = -1) {
    long long n = a.size();
    if (r < 0) r = n;
    vector<vector<T>> out;
    if (r > n) return out;
    vector<T> cur;
    vector<bool> used(n, false);
    auto rec = [&](auto&& self) -> void {
        if ((long long)cur.size() == r) { out.push_back(cur); return; }
        for (long long i = 0; i < n; i++) {
            if (used[i]) continue;
            used[i] = true;
            cur.push_back(a[i]);
            self(self);
            cur.pop_back();
            used[i] = false;
        }
    };
    rec(rec);
    return out;
}""")

_h("combinations", r"""
// itertools.combinations(a, r): podzbiory r-elementowe w kolejności jak w Pythonie
template<class T> vector<vector<T>> combinations(const vector<T>& a, long long r) {
    long long n = a.size();
    vector<vector<T>> out;
    vector<T> cur;
    auto rec = [&](auto&& self, long long start) -> void {
        if ((long long)cur.size() == r) { out.push_back(cur); return; }
        for (long long i = start; i < n; i++) {
            cur.push_back(a[i]);
            self(self, i + 1);
            cur.pop_back();
        }
    };
    rec(rec, 0);
    return out;
}""")

_h("product", r"""
// itertools.product(a, repeat=k): iloczyn kartezjański
template<class T> vector<vector<T>> product(const vector<vector<T>>& pools) {
    vector<vector<T>> out = {{}};
    for (const auto& pool : pools) {
        vector<vector<T>> next;
        for (const auto& prefix : out)
            for (const auto& x : pool) {
                auto p = prefix;
                p.push_back(x);
                next.push_back(p);
            }
        out = next;
    }
    return out;
}""")

_h("prefixSums", r"""
// itertools.accumulate(a): sumy prefiksowe
template<class T> vector<T> prefixSums(const vector<T>& a) {
    vector<T> out;
    T acc{};
    for (size_t i = 0; i < a.size(); i++) {
        acc = i ? acc + a[i] : a[i];
        out.push_back(acc);
    }
    return out;
}""")

_h("transpose", r"""
// zip(*macierz): transpozycja (wiersze krótsze niż pierwszy obcinają wynik, jak w Pythonie)
template<class C> auto transpose(const vector<C>& m) {
    using T = typename C::value_type;
    vector<vector<T>> out;
    if (m.empty()) return out;
    size_t w = m[0].size();
    for (const auto& row : m) w = min(w, (size_t)row.size());
    out.assign(w, vector<T>());
    for (const auto& row : m)
        for (size_t j = 0; j < w; j++) out[j].push_back(row[j]);
    return out;
}""")

_h("sumOf", r"""
// sum(x) dla dowolnego kontenera
template<class C> auto sumOf(const C& c) {
    typename C::value_type s{};
    for (const auto& x : c) s += x;
    return s;
}""")

ORDER = ["floordiv", "pymod", "fpymod", "ipow", "powmod", "isqrt", "comb", "perm", "factorial", "pyround",
         "toBase", "input", "skipLine", "readline", "readLines", "readAll", "readAllTokens", "nextToken", "nextInt",
         "readInts", "readFloats", "split", "splitSep", "strip", "lstrip", "rstrip", "upper", "lower", "swapcase",
         "title", "capitalize", "replaceAll", "countSub", "strPred", "caseCheck", "repeatStr", "repeatVec", "concat",
         "pad", "strcore", "reprcore", "join", "fmt", "noIdx", "slice", "sorted", "popBack", "popFront", "popAt",
         "pqPop", "vecHeap", "dget", "setops", "isSubset", "mostCommon", "keys", "values", "rangeVec",
         "permutations", "combinations", "product", "prefixSums", "transpose", "sumOf"]
assert set(ORDER) == set(H), set(H) ^ set(ORDER)

# nazwy funkcji wprowadzanych przez helpery — identyfikatory użytkownika o tych nazwach są zmieniane
HELPER_NAMES = {"floordiv", "pymod", "fpymod", "ipow", "powmod", "isqrt", "comb", "perm", "factorial", "pyround",
                "toBase", "input", "skipLine", "readline", "readLines", "readAll", "readAllTokens", "nextToken",
                "nextInt", "readInts", "readFloats", "split", "strip", "lstrip", "rstrip", "upper", "lower",
                "swapcase", "title", "capitalize", "replaceAll", "countSub", "allChars", "caseCheck", "repeatStr",
                "repeatVec", "concat", "ljust", "rjust", "center", "zfill", "str", "repr", "reprItems", "reprMap", "reprAsTuple",
                "join", "fmt", "FmtSpec", "parseSpec", "fmtPad", "fmtGroup", "NO_IDX", "pySlice", "sorted", "popBack",
                "popFront", "popAt", "pqPop", "heappush", "heappop", "heapify", "dget", "setUnion", "setInter",
                "setDiff", "setSymDiff", "isSubset", "mostCommon", "keys", "values", "rangeVec", "permutations",
                "combinations", "product", "prefixSums", "sumOf", "transpose"}


def render(used: set[str], int_type: str) -> str:
    """Kod helperów (z zależnościami) w poprawnej kolejności."""
    need: set[str] = set()

    def add(n: str) -> None:
        if n in need:
            return
        need.add(n)
        for d in H[n][1]:
            add(d)

    for n in used:
        if n in H:
            add(n)
    parts = [H[n][0].replace("INT_T", int_type) for n in ORDER if n in need]
    return "\n\n".join(parts)

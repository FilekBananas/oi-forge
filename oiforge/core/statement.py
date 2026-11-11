"""Parser treści zadań olimpijskich (PL): tekst z PDF, HTML (sio2/oioioi) albo wklejony tekst.

`parse(text)` zwraca:
    {title, sections: {tresc, wejscie, wyjscie, przyklad, ocenianie, uwagi, ...},
     input_format: [{line, kind, vars, types, length?, repeat?, desc}],
     constraints: {var: {min, max, raw}}, relations: [...],
     samples: [{name, input, output, explanation?, uncertain?}],
     subtasks: [{id, points, conditions, constraints, time_limit}],
     time_limit, memory_limit, keywords, meta, warnings, format_check}

Odporność na typowe artefakty PDF: łamane wyrazy („repre-\\nzentującego”), potęgi w osobnej linii
(„10\\n4” = 10^4), zepsute kodowanie fontów OT4 („wªa±nie” = „właśnie”), akcenty LaTeX-a jako osobne znaki
(„˙ze” = „że”), „¬”/„⩽” zamiast „≤”, liczby z separatorami tysięcy („1 000 000”), „2·10^5”, „10^9+7”.
Bez zależności zewnętrznych.
"""
from __future__ import annotations

import html as _html
import math
import re
import unicodedata
from collections import Counter
from html.parser import HTMLParser

from . import text as T

__all__ = ["parse", "clean", "html_to_text", "parse_value", "extract_constraints", "infer_input_format",
           "check_format", "cpp_reader", "complexity_for", "COMPLEXITY_TABLE", "size_bounds", "fmt_value"]

INT32_MAX = 2**31 - 1
_LOST_POW = {104, 105, 106, 107, 108, 109, 1012, 1015, 1016, 1018}

# ============================================================================================ HTML → tekst
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5",
          "h6", "blockquote", "dl", "dt", "dd", "hr", "center", "figure", "figcaption", "thead", "tbody"}
_SKIP = {"script", "style", "nav", "header", "footer", "noscript", "head", "title", "button", "form", "select",
         "textarea", "svg", "iframe"}


class _Html2Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0
        self.pre_buf: list[str] = []
        self.pres: list[str] = []
        self.title = None
        self._h1 = None
        self.in_main = 0           # głębokość w <section class=main-content>
        self.has_main = False
        self._stack: list[str] = []
        self.sup = 0
        self.sub = 0
        self.texmath = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in _SKIP:
            self.skip += 1
            return
        if tag == "section" and "main-content" in cls:
            self.has_main = True
            self.in_main += 1
            self._stack.append("main")
            return
        if tag == "section":
            self._stack.append("section")
        if self.skip:
            return
        if tag == "pre":
            self.pre += 1
            self.pre_buf = []
            return
        if self.pre:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n")
            if tag == "h1":
                self._h1 = []
        elif tag == "li":
            self.out.append("\n• ")
        elif tag in _BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag == "sup":
            self.out.append("^")
            self.sup += 1
        elif tag == "sub":
            self.out.append("_")
            self.sub += 1
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self.out.append(" " + alt + " ")
            elif "texmath" in cls or self.texmath or "tex" in (a.get("src") or ""):
                self.out.append(" ⟦wzór⟧ ")
        elif tag == "span" and "texmath" in cls:
            self.texmath += 1
            self._stack.append("texmath")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "section":
            if self._stack:
                top = self._stack.pop()
                if top == "main":
                    self.in_main = max(0, self.in_main - 1)
            return
        if self.skip:
            return
        if tag == "pre":
            if self.pre:
                self.pre -= 1
                txt = "".join(self.pre_buf)
                self.pres.append(txt)
                self.out.append(f"\n⟦PRE:{len(self.pres) - 1}⟧\n")
            return
        if self.pre:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if tag == "h1" and self._h1 is not None:
                t = " ".join("".join(self._h1).split())
                if t and not self.title:
                    self.title = t
                self._h1 = None
            self.out.append("\n\n")
        elif tag in _BLOCK:
            self.out.append("\n")
        elif tag == "sup":
            self.sup = max(0, self.sup - 1)
            self.out.append(" " if False else "")
        elif tag == "sub":
            self.sub = max(0, self.sub - 1)
        elif tag == "span" and self._stack and self._stack[-1] == "texmath":
            self._stack.pop()
            self.texmath = max(0, self.texmath - 1)

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            self.pre_buf.append(data)
            return
        if self._h1 is not None:
            self._h1.append(data)
        if self.sup or self.sub:
            data = data.strip()
            if len(data) > 1 and not re.fullmatch(r"-?\d+", data):
                data = "{" + data + "}"
        else:
            data = re.sub(r"\s+", " ", data)
        self.out.append(data)


def html_to_text(src: str) -> tuple[str, list[str], str | None]:
    """(tekst, bloki <pre>, tytuł z <h1>). Gdy strona ma <section class=main-content>, bierze tylko ją."""
    m = re.search(r"<section[^>]*class=\"[^\"]*main-content[^\"]*\"[^>]*>(.*?)</section>\s*</div>", src, re.S | re.I)
    body = m.group(0) if m else src
    p = _Html2Text()
    try:
        p.feed(body)
        p.close()
    except Exception:  # noqa: BLE001 — uszkodzony HTML: tniemy tagi regexem
        txt = re.sub(r"<[^>]+>", "\n", body)
        return _html.unescape(txt), [], None
    txt = "".join(p.out)
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n[ \t]+", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    title = p.title
    if title:
        title = re.sub(r"\s*-\s*Problem statement.*$", "", title).strip()
    return txt.strip(), p.pres, title


def looks_like_html(s: str) -> bool:
    head = s[:4000].lower()
    return bool(re.search(r"<(html|body|p|div|h[1-6]|pre|section|br|span|table)[\s>/]", head)) and "</" in head


# ============================================================================================ znaki
_OT4 = {"¡": "ą", "¢": "ć", "¦": "ę", "ª": "ł", "«": "ń", "±": "ś", "¹": "ź", "»": "ż",
        "\x81": "Ą", "\x82": "Ć", "\x86": "Ę", "\x8a": "Ł", "\x8b": "Ń", "\x91": "Ś", "\x99": "Ź", "\x9b": "Ż",
        "\x15": "–", "\x7f": ""}
_ACCENTS = {("˙", "z"): "ż", ("˙", "Z"): "Ż", ("´", "s"): "ś", ("´", "S"): "Ś", ("´", "c"): "ć", ("´", "C"): "Ć",
            ("´", "n"): "ń", ("´", "N"): "Ń", ("´", "o"): "ó", ("´", "O"): "Ó", ("´", "z"): "ź", ("´", "Z"): "Ź"}
_SUPS = str.maketrans({"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8",
                       "⁹": "9", "⁻": "-"})
_SUBS = str.maketrans({"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7", "₈": "8",
                       "₉": "9"})


def _fix_mojibake(s: str) -> tuple[str, bool]:
    bad = len(re.findall(r"[A-Za-z][ª±¦¡«»¢¹]|[ª±¦¡«»¢¹][a-z]", s))
    good = len(re.findall(r"[ąćęłńśźżĄĆĘŁŃŚŹŻ]", s))
    if bad >= 3 and bad > good:
        return "".join(_OT4.get(c, c) for c in s), True
    return s, False


def _normalize_chars(s: str) -> tuple[str, bool]:
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    s, moj = _fix_mojibake(s)
    # akcenty LaTeX-a jako osobne znaki: „˙ze” → „że”, „niesko´nczono´s´c” → „nieskończoność”
    s = re.sub(r"([˙´])\s?([A-Za-z])", lambda m: _ACCENTS.get((m.group(1), m.group(2)), m.group(0)), s)
    s = re.sub(r"([aeAE])˛", lambda m: {"a": "ą", "e": "ę", "A": "Ą", "E": "Ę"}[m.group(1)], s)
    s = re.sub(r"̸\s*=", "≠", s)                      # „a̸ = b” → „a ≠ b”
    s = s.replace("̸", "")
    for a, b in (("¬", "≤"), ("⩽", "≤"), ("⩾", "≥"), ("≦", "≤"), ("≧", "≥"), ("−", "-"), ("‐", "-"), ("‑", "-"),
                 ("∗", "*"), ("〈", "["), ("⟨", "["), ("〉", "]"), ("⟩", "]"), ("ﬁ", "fi"), ("ﬂ", "fl"),
                 (" ", " "), (" ", " "), (" ", " "), (" ", " "), ("​", ""),
                 ("…", "..."), ("′", "'"), ("﻿", "")):
        s = s.replace(a, b)
    # znaki sterujące z fontów LaTeX-a (cmsy/cmex) bez mapowania Unicode: 0x14 = ≤, 0x15 = ≥, 0x01 = ·,
    # 0x02/0x03 = duże nawiasy [ ] (w zepsutym kodowaniu OT4 0x15 to „–” — wtedy zamienił je już _fix_mojibake)
    if not moj:
        s = s.replace("\x14", "≤").replace("\x15", "≥")
    s = s.replace("\x01", "·")
    if "\x02" in s and "\x03" in s:
        s = s.replace("\x02", "[").replace("\x03", "]")
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+", lambda m: "^" + m.group(0).translate(_SUPS), s) if not moj else s
    s = re.sub(r"[₀₁₂₃₄₅₆₇₈₉]+", lambda m: "_" + m.group(0).translate(_SUBS), s)
    # matematyczne litery kursywą (𝑛, 𝑚) → zwykłe
    s = re.sub(r"[\U0001D400-\U0001D7FF]", lambda m: unicodedata.normalize("NFKC", m.group(0)), s)
    return s, moj


# ============================================================================================ liczby
_GROUPED = r"\d{1,3}(?:[ ]\d{3})+(?![\d,.]\d)"
_PLAIN = r"\d+(?:\.\d+)?(?:[eE]\d+)?"
_POW = r"(?:\s*\^\s*(?:\{\s*-?\d+\s*\}|\(\s*-?\d+\s*\)|-?\d+))"
_ATOM = rf"(?:{_GROUPED}|{_PLAIN}){_POW}?"
VALUE_RE = re.compile(rf"(?:{_ATOM})(?:\s*[·*×x]\s*(?:{_ATOM}))?(?:\s*[+-]\s*\d+(?![\d^.,]|\s*[·*×]))?")


def parse_value(s: str):
    """'2·10^5' → 200000, '1 000 000' → 1000000, '10^9+7' → 1000000007, '1e9' → 10**9, '0,5' → 0.5."""
    if s is None:
        return None
    s = str(s).strip().replace("{", "").replace("}", "").replace("(", "").replace(")", "")
    neg = False
    if s.startswith("-"):
        neg, s = True, s[1:].strip()
    s = re.sub(r"^(\d),(\d+)$", r"\1.\2", s)
    m = re.fullmatch(r"(.+?)\s*([+-])\s*(\d+)", s)
    add = 0
    if m and ("^" in m.group(1) or "·" in m.group(1) or "*" in m.group(1)):
        s, add = m.group(1), int(m.group(3)) * (1 if m.group(2) == "+" else -1)
    parts = re.split(r"\s*[·*×x]\s*", s)
    val = 1
    for p in parts:
        p = p.strip()
        if not p:
            return None
        mm = re.fullmatch(r"([\d ]+(?:\.\d+)?(?:[eE]\d+)?)\s*(?:\^\s*(-?\d+))?", p)
        if not mm:
            return None
        base_s = mm.group(1).replace(" ", "")
        try:
            base = float(base_s) if ("." in base_s or "e" in base_s.lower()) else int(base_s)
        except ValueError:
            return None
        if mm.group(2) is not None:
            e = int(mm.group(2))
            if e > 40:
                return None
            base = base ** e
        val *= base
    val += add
    if isinstance(val, float) and val.is_integer() and abs(val) < 1e19:
        val = int(val)
    return -val if neg else val


def fmt_value(v) -> str:
    """Ładny zapis liczby: 1000000 → '10^6', 200000 → '2·10^5', 1000000007 → '10^9+7'."""
    if v is None:
        return "?"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:g}"
    v = int(v)
    neg = v < 0
    a = abs(v)
    out = None
    if a >= 10000:
        for add in (0, 7, 9, 3, -1, 1):
            b = a - add
            if b <= 0:
                continue
            e = int(round(math.log10(b)))
            if e >= 4 and 10 ** e == b:
                out = f"10^{e}" + (f"+{add}" if add > 0 else (f"{add}" if add < 0 else ""))
                break
            e = len(str(b)) - 1
            lead, rest = divmod(b, 10 ** e)
            if rest == 0 and e >= 4 and add == 0:
                out = f"{lead}·10^{e}"
                break
        if out is None and a in (2**31 - 1, 2**32, 2**63 - 1, 2**31, 2**63, 2**64 - 1):
            out = {2**31 - 1: "2^31-1", 2**32: "2^32", 2**63 - 1: "2^63-1", 2**31: "2^31", 2**63: "2^63",
                   2**64 - 1: "2^64-1"}[a]
    if out is None:
        out = f"{a:,}".replace(",", " ") if a >= 10000 else str(a)
    return ("-" if neg else "") + out


# ============================================================================================ bardzo duże liczby
# „1 ≤ n ≤ 10^1000000” — liczba ma do miliona cyfr, więc w programie jest NAPISEM. Takich wartości nie zamieniamy
# na int (Python 3.11+ nie wypisze inta dłuższego niż ~4300 cyfr, a JSON i tak by tego nie zniósł) — trzymamy opis
# {lead, pow10, digits} i budujemy napis cyfr tylko wtedy, gdy jest naprawdę potrzebny.
LL_MAX = 2**63 - 1
ULL_MAX = 2**64 - 1
_BIG_RE = re.compile(r"(?:(\d{1,3}(?:[ ]\d{3})*|\d+)\s*[·*×x]\s*)?10\s*\^\s*[{(]?\s*(\d+)\s*[})]?(?:\s*([+-])\s*1)?")


def big_number(s) -> dict | None:
    """'10^1000000' → {'lead': '1', 'pow10': 1000000, 'digits': 1000001}; '9 · 10^999999' → lead '9';
    '10^30 - 1' → {'nines': 30, 'digits': 30}; '10^20 + 1' → {'plus_one': True}. Liczby mieszczące się w long long
    (≤ 2^63−1) → None."""
    if s is None:
        return None
    t = str(s).strip()
    m = _BIG_RE.fullmatch(t)
    if m:
        lead = (m.group(1) or "1").replace(" ", "").lstrip("0") or "0"
        k = int(m.group(2))
        if lead == "0":
            return None
        digits = len(lead) + k
        if digits <= 18 or (digits == 19 and int(lead + "0" * k) <= LL_MAX):
            return None
        if m.group(3) == "-":
            if lead != "1":
                return None
            return {"lead": "9", "pow10": 0, "nines": k, "digits": k}
        info = {"lead": lead, "pow10": k, "digits": digits}
        if m.group(3) == "+":
            info["plus_one"] = True
        return info
    v = parse_value(t)
    if isinstance(v, int) and v > ULL_MAX:          # 2^63…2^64−1 mieści się jeszcze w unsigned long long
        d = str(v)
        stripped = d.rstrip("0")
        return {"lead": stripped, "pow10": len(d) - len(stripped), "digits": len(d)}
    return None


def big_digits(info: dict) -> str:
    """Napis cyfr liczby opisanej przez big_number()."""
    if info.get("nines"):
        return "9" * info["nines"]
    s = info["lead"] + "0" * info["pow10"]
    if info.get("plus_one"):
        s = s[:-1] + "1" if s.endswith("0") else s
    return s


def value_text(expr) -> str | None:
    """Dokładny zapis dziesiętny wyrażenia z treści ('1 000 000' → '1000000', '9 · 10^999999' → '9000…0',
    '10^9' → '1000000000'). None, gdy to nie jest liczba całkowita."""
    if expr is None:
        return None
    t = str(expr).strip().rstrip(".")
    neg = t.startswith("-")
    if neg:
        t = t[1:].strip()
    info = big_number(t)
    if info is not None:
        return ("-" if neg else "") + big_digits(info)
    v = parse_value(t)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not isinstance(v, int):
        return None
    if abs(v) >= 10**4000:
        return None
    return str(-v if neg else v)


def fmt_big(c: dict) -> str:
    """Ładny zapis górnej granicy „liczby jako napisu” z ograniczeń: '10^1000000', '9·10^999999', '10^30−1'."""
    if not c:
        return "?"
    if c.get("max_nines"):
        return f"10^{c['max_nines']}−1"
    lead, k = c.get("max_lead"), c.get("max_pow10")
    if lead is not None and k is not None:
        if k == 0:
            return lead if len(lead) <= 24 else f"{lead[:6]}… ({len(lead)} cyfr)"
        return (f"10^{k}" if lead == "1" else f"{lead}·10^{k}") + ("+1" if c.get("max_plus_one") else "")
    if isinstance(c.get("max"), int):
        return fmt_value(c["max"])
    return f"{c.get('max_digits', '?')} cyfr"


def _mark_big(d: dict, info: dict) -> None:
    d["bignum"] = True
    d["max_digits"] = max(d.get("max_digits") or 0, info["digits"])
    if info.get("nines"):
        d["max_nines"] = info["nines"]
    else:
        d["max_lead"], d["max_pow10"] = info["lead"], info["pow10"]
        if info.get("plus_one"):
            d["max_plus_one"] = True


# ============================================================================================ tekst → linie
_NOISE_RE = re.compile(
    r"^(©.*|.*\bsio2\.[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b.*|szablon tre[sś]ci zadania.*|stworzonego przez .{2,60}|"
    r"warunkami licencji.*|\d+\s*/\s*(?:\d+|\?+)(\s+.{0,60})?|strona \d+( z \d+)?|page \d+( of \d+)?|"
    r"koło informatyczne\b.{0,80}|to zadanie pochodzi z.*|"
    # stopki szablonów treści z serwerów SIO2 (adres serwera, „koło informatyczne …”, autor szablonu) i PDF-ów Olimpiady: „Olimpiada Informatyczna (oi.edu.pl) 1/2”, „… finansowana jest ze środków …”,
    # „„Organizacja i przeprowadzenie olimpiad …”.”, „Bitada (v. 1.0)”, „v. 1.2 1/2 Najmniejsza wspólna …”
    r"olimpiada informatyczna \(oi\.edu\.pl\).*|olimpiada informatyczna finansowana .*|"
    r"[„\"]?organizacja i przeprowadzenie olimpiad .*|.{1,80}\(v\.\s?\d+(?:\.\d+){0,2}\)|"
    r"v\.\s?\d+(?:\.\d+){0,2}\s+\d+\s*/\s*\d+(?:\s+.{0,80})?)$", re.I)


def _logical_lines(raw: str) -> tuple[list[str], str]:
    """Dzieli tekst na logiczne linie. Tekst z PDF (jak w statements/*.txt) ma linie oddzielone pustą linią,
    a pojedyncze \\n wewnątrz to potęgi/indeksy/ułamki — sklejamy je."""
    lines = raw.split("\n")
    nonempty = sum(1 for ln in lines if ln.strip())
    empty = len(lines) - nonempty
    doubled = nonempty > 6 and empty >= 0.6 * nonempty and "\n\n" in raw
    if doubled:
        blocks = re.split(r"\n[ \t]*\n", raw)
        out = [_join_inner(b) for b in blocks]
        return [ln for ln in out if ln is not None], "pdf"
    return [ln.rstrip() for ln in lines], "text"


_EXP_BASE = re.compile(r"(?:^|[\s(\[≤<≥>=,·*×+-]|\.\.)(10|2|[2-9]\s*[·*×]\s*10|10\s*[·*×]\s*10)$")


def _join_inner(block: str) -> str:
    parts = [p.strip() for p in block.split("\n")]
    parts = [p for p in parts if p != ""]
    if not parts:
        return ""
    out = parts[0]
    i = 1
    while i < len(parts):
        p = parts[i]
        if re.fullmatch(r"-?\d{1,2}|[a-z]|\d{1,2}\s*[+-]\s*\d|\{?-?\d{1,2}\}?", p) and _EXP_BASE.search(out):
            out = out + "^" + p.strip("{}")
            if i + 1 < len(parts):
                nxt = parts[i + 1]
                sep = "" if re.match(r"^[).,;:\]]", nxt) else " "
                out = out + sep + nxt
                i += 2
                continue
            i += 1
            continue
        if out.endswith("-") and re.search(r"[a-ząćęłńóśźż]-$", out) and re.match(r"^[a-ząćęłńóśźż]", p):
            out = out[:-1] + p
        elif re.match(r"^[).,;:\]]", p) or out.endswith(("(", "[")):
            out += p
        elif p == "'" or p.startswith("'"):
            out += p
        else:
            out += " " + p
        i += 1
    return out


def _fix_exponents_text(lines: list[str]) -> list[str]:
    """Dla zwykłego tekstu (pojedyncze \\n): „... ≤ 10” + „6” + „)” → „... ≤ 10^6)”."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if (i + 1 < len(lines) and re.fullmatch(r"\s*-?\d{1,2}\s*", lines[i + 1] or "")
                and _EXP_BASE.search(ln.rstrip()) and re.search(r"[≤<≥>]|liczb|nie przekr|wi[eę]ksz|mniejsz|zakres",
                                                                 ln)):
            ln = ln.rstrip() + "^" + lines[i + 1].strip()
            i += 2
            if i < len(lines) and re.match(r"^\s*[).,;:\]]", lines[i] or ""):
                ln += lines[i].strip()
                i += 1
            out.append(ln)
            continue
        out.append(ln)
        i += 1
    return out


def _dehyphenate(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        if out and re.search(r"[a-ząćęłńóśźż]{2}-$", out[-1]) and re.match(r"^[a-ząćęłńóśźż]{2}", ln.strip()):
            out[-1] = out[-1][:-1] + ln.strip()
        else:
            out.append(ln)
    return out


# „≤” zapisane przez PDF jako cyfra „6” (font LaTeX-a bez mapowania Unicode): „(1 6 n, q 6 5 000)” = 1 ≤ n, q ≤ 5000,
# „1 6 a 6 b 6 n”. Naprawiamy TYLKO tekst sekcji (nigdy przykładów) i tylko łańcuchy liczba/zmienna „6” zmienna/liczba.
_SIX_VAR = r"[A-Za-z](?:_?[A-Za-z0-9]|_\{[^}\s]{1,6}\})?"
_SIX_NUM = r"\d{1,3}(?: \d{3})+(?!\d)|\d+(?:\^\d+)?"
_SIX_RE = re.compile(r"(?<![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.,^'|])(?P<l>" + _SIX_VAR + r"|" + _SIX_NUM + r")\s6\s(?=(?P<r>"
                     + _SIX_VAR + r"|" + _SIX_NUM + r")(?P<after>[^\wąćęłńóśźż]|$))")
_SIX_DOUBLE = re.compile(r"(?:^|[\s(,])(?:" + _SIX_NUM + r"|" + _SIX_VAR + r")\s6\s" + _SIX_VAR + r"\s6\s(?:"
                         + _SIX_NUM + r"|" + _SIX_VAR + r")(?=[\s,;.)]|$)")
_SIX_STOP = {"ma", "to", "do", "na", "od", "po", "za", "we", "ze", "co", "go", "je", "mu", "nr", "w", "z", "o", "u"}


def _six_operand(tok: str) -> str:
    return "num" if tok[0].isdigit() else "var"


def _fix_six_leq(sections: dict[str, list[str]]) -> tuple[dict[str, list[str]], int]:
    """„1 6 n 6 20” → „1 ≤ n ≤ 20”. Gdy dokument ma prawdziwe „≤”, poprawiamy tylko pewne łańcuchy „a 6 x 6 b”
    (np. tabela podzadań). Wiersze wyglądające jak dane („1 6 R”) zostawiamy, chyba że to taki podwójny łańcuch."""
    has_le = any(("≤" in ln or "<=" in ln) for v in sections.values() for ln in v)
    fixed = 0

    def fix_line(ln: str) -> str:
        nonlocal fixed
        if " 6 " not in ln:
            return ln
        double = bool(_SIX_DOUBLE.search(ln))
        data_like = bool(re.fullmatch(r"[\d\s\-A-Za-z]*", ln)) and not re.search(r"[A-Za-z]{3}", ln)
        if (has_le or data_like) and not double:
            return ln

        def rep(m: re.Match) -> str:
            nonlocal fixed
            lt, rt = _six_operand(m.group("l")), _six_operand(m.group("r"))
            if lt == "num" and rt == "num":
                return m.group(0)
            if (lt == "var" and m.group("l").lower() in _SIX_STOP) or (rt == "var" and m.group("r").lower() in _SIX_STOP):
                return m.group(0)
            if rt == "var":
                # zmienna po prawej musi kończyć element łańcucha: „6 n,” „6 b 6” „6 n)” — nie „6 s 30” (sekundy)
                rest = ln[m.end() + len(m.group("r")):]
                if not re.match(r"\s*(?:[,;.)\]≤<]|6\s|$|(?:i|oraz|lub)\s)", rest):
                    return m.group(0)
            fixed += 1
            return m.group("l") + " ≤ "
        return _SIX_RE.sub(rep, ln)

    out = {k: [fix_line(ln) for ln in v] for k, v in sections.items()}
    return out, fixed


# ============================================================================================ sekcje
_SECTION_PATTERNS = [
    ("wejscie", r"wej[sś]cie|dane wej[sś]ciowe|format wej[sś]cia|opis wej[sś]cia|wej[sś]cie programu|input|"
                r"specyfikacja wej[sś]cia|format danych wej[sś]ciowych|dane"),
    ("wyjscie", r"wyj[sś]cie|dane wyj[sś]ciowe|format wyj[sś]cia|opis wyj[sś]cia|wyj[sś]cie programu|output|"
                r"specyfikacja wyj[sś]cia|format danych wyj[sś]ciowych|wynik"),
    ("przyklad", r"przyk[lł]ad(?:y|\(y\)|owe dane|owe testy)?(?:\s*\d+)?|example(?:s)?|sample(?:s)?(?: tests?)?"),
    ("ocenianie", r"ocenianie|podzadania|punktacja|scoring|subtasks?|ocenianie i podzadania"),
    ("uwagi", r"uwagi|uwaga|notatki|wskaz[oó]wk[ai]|notes?|komentarz"),
    ("ograniczenia", r"ograniczenia|limity|constraints|ograniczenia i limity"),
    ("zadanie", r"zadanie|tw[oó]je zadanie|polecenie|task"),
    ("wyjasnienie", r"wyja[sś]nienie(?: do przyk[lł]ad[uó]w?)?|obja[sś]nienie(?: przyk[lł]adu)?|explanation"),
]
_SECTION_RES = [(k, re.compile(rf"^\s*(?:\d+\.\s*)?(?:{p})\s*[:.]?\s*$", re.I)) for k, p in _SECTION_PATTERNS]
_INLINE_SEC = re.compile(r"^\s*(wej[sś]cie|wyj[sś]cie)\s*:\s+(?!$)(.+)$", re.I)
_SIDE_RE = re.compile(r"^\s*(wej[sś]cie|input|dla danych wej[sś]ciowych)\s*:?\s+(wyj[sś]cie|output|"
                      r"poprawn\w+\s+(?:wynik\w*|odpowied\w+)(?:\s+jest)?)\s*:?\s*$", re.I)
# „Testy przykładowe: … Poza tym:” / „Testy „ocen”:” i pozycje „1ocen: n = 1 000 000; odpowiedź to 1;”, „0b: n = 10, …”
_OCEN_HEAD = re.compile(r"^\s*(?:pozosta[lł]e\s+)?testy\s+(?:przyk[lł]adowe|[„\"'”]?ocen[”\"']?)(?![\wąćęłńóśźż])", re.I)
_OCEN_LABEL = re.compile(r"^\s*[-•]?\s*(\d{1,2}(?:ocen|[a-z]))\s*:\s*(.*)$", re.I)
_IN_RE = re.compile(r"^\s*(?:(?:wej[sś]cie|input|dane wej[sś]ciowe)(?:\s+(?:dla testu|testu|przyk[lł]adu)\s+(\S+?))?"
                    r"|przyk[lł]adowe (?:wej[sś]cie|dane(?: wej[sś]ciowe)?)(?:\s+(\S+?))?"
                    r"|(?:(?:natomiast|a|za[sś]|oraz|i)\s+)?dla (?:nast[eę]puj[aą]cych |takich |innych )?danych"
                    r"(?: wej[sś]ciowych)?(?: \(\w+\))?|sample input(?:\s+(\S+?))?|dane)\s*:?\s*$", re.I)
_IN_RE2 = re.compile(r"^\s*(?:wej[sś]cie|input)\s+dla\s+testu\s+(\S+?)\s*:?\s*$", re.I)
_OUT_RE = re.compile(r"^\s*(?:(?:wyj[sś]cie|output|dane wyj[sś]ciowe)(?:\s+(?:dla testu|testu|przyk[lł]adu)\s+(\S+?))?"
                     r"|przyk[lł]adowe wyj[sś]cie(?:\s+(\S+?))?|(?:to\s+|jedn[aą] z\s+|jednym z\s+|przyk[lł]adow[aąy]\s+)?"
                     r"poprawn(?:ym|ą|y|a|e|ych) (?:wynikiem|wynik[oó]w|odpowiedzi|"
                     r"odpowiedzi[aą]|wynik|odpowied[zź]|wyj[sś]cie)(?: jest| to| b[eę]dzie)?"
                     r"|wynik|odpowied[zź]|sample output(?:\s+(\S+?))?)\s*:?\s*$", re.I)
# tekst, który kończy blok przykładu (stopki, komentarze autorów, przypisy)
_SAMPLE_STOP = re.compile(r"^\s*(autor(?:zy)?(?: zadania)?\s*:|komentarz|wskaz[oó]wk|ciekawostk|uwaga\s*:|uwagi\s*:|"
                          r"zadanie (?:powsta|pochodzi)|fig\.\s*\d|rys(?:unek|\.)\s*\d*|bo\s*:|oij\.edu|olimpiada "
                          r"finansowana|\d+\.\s+obrazek|\d+/\?+|t[lł]umaczenie|źród[lł]o\s*:|zadanie z )", re.I)
_POLISH_PROSE = re.compile(r"\b(jest|s[aą]|si[eę]|że|dla|kt[oó]r\w+|oraz|przyk[lł]ad\w*|nale[zż]y|mo[zż]na|"
                           r"poniewa[zż]|wi[eę]c|natomiast|gdy|je[sś]li|b[eę]dzie|zatem|wynik\w*)\b", re.I)


def _is_prose(line: str) -> bool:
    s = line.strip()
    if _SAMPLE_STOP.match(s):
        return True
    words = re.findall(r"[A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}", s)
    if len(words) >= 5 and len(_POLISH_PROSE.findall(s)) >= 1 and re.search(r"[ąćęłńóśźż]", s):
        return True
    return len(words) >= 8 and len(_POLISH_PROSE.findall(s)) >= 2
_OUT_RE2 = re.compile(r"^\s*(?:wyj[sś]cie|output)\s+dla\s+testu\s+(\S+?)\s*:?\s*$", re.I)
_PRE_RE = re.compile(r"^⟦PRE:(\d+)⟧$")


def _section_of(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 60:
        return None
    for k, rx in _SECTION_RES:
        if rx.match(s):
            return k
    return None


def _strip_header(lines: list[str], html_title: str | None) -> tuple[list[str], dict]:
    """Usuwa nagłówek szablonu (tytuł, kod zadania, limity) i zwraca meta."""
    meta: dict = {}
    head_idx = [i for i, ln in enumerate(lines[:12]) if ln.strip()][:6]
    drop = set()
    title = html_title
    for pos, i in enumerate(head_idx):
        ln = lines[i].strip()
        mz = re.fullmatch(r"Zadanie\s*:\s*([A-Za-z0-9_]{1,12})", ln)
        if mz:
            meta["code"] = mz.group(1).lower()
            drop.add(i)
            if pos > 0:
                drop.add(head_idx[pos - 1])         # znacznik autora/serii nad „Zadanie: XXX”
            if pos + 1 < len(head_idx) and not title:
                title = lines[head_idx[pos + 1]].strip()
                drop.add(head_idx[pos + 1])
            continue
        m = re.search(r"Kod zadania:\s*(\S+)", ln)
        if m:
            meta["code"] = m.group(1)
            contest = ln[: m.start()].strip(" –-")
            if contest and pos == 0 and not title:
                title = contest            # „Zachłanny wąż Kod zadania: waz” — tytuł w tej samej linii
            elif contest:
                meta["contest"] = contest
            drop.add(i)
            if pos > 0 and not title:
                title = lines[head_idx[pos - 1]].strip()
                drop.add(head_idx[pos - 1])
            continue
        m = re.search(r"(Limit pami[eę]ci|Dost[eę]pna pami[eę][cć]|Memory limit)\s*:?\s*(\d+(?:[.,]\d+)?)\s*([MKG]i?B)",
                      ln, re.I)
        if m:
            meta["memory_limit"] = f"{m.group(2)} {m.group(3)}"
            note = ln[: m.start()].strip(" –-")
            if note:
                meta["header_note"] = note
            drop.add(i)
            if "Dost" in m.group(1) and pos + 1 < len(head_idx) and not title:
                cand = lines[head_idx[pos + 1]].strip()
                if 0 < len(cand) <= 70 and not _section_of(cand):
                    title = cand
                    drop.add(head_idx[pos + 1])
            continue
        mt = re.search(r"Limit czasu\s*:?\s*(\d+(?:[.,]\d+)?)\s*s\b", ln)
        if mt and len(ln) < 80:
            meta["time_limit"] = _parse_time(mt.group(1))
            note = ln[: mt.start()].strip(" –-")
            if note:
                meta.setdefault("header_note", note)
            drop.add(i)
            continue
        if pos == 0 and re.search(r"(WIC|WWI|OI|Kółko|ko[lł]o|contest|Dzie[nń]|\d{2}\.\d{2}\.\d{4})", ln) and \
                len(head_idx) > 1 and re.search(r"(Dost[eę]pna pami|Limit pami)", lines[head_idx[1]]):
            meta["contest"] = ln
            drop.add(i)
    if not title:
        for i in head_idx:
            ln = lines[i].strip()
            if i not in drop and 0 < len(ln) <= 70 and not _section_of(ln) and not ln.endswith("."):
                title = ln
                drop.add(i)
                break
    body = [ln for i, ln in enumerate(lines) if i not in drop]
    if title:
        # tytuł powtarzany w stopce każdej strony
        tnorm = title.strip().lower()
        body = [ln for ln in body if ln.strip().lower() != tnorm]
    meta["title"] = title or ""
    return body, meta


# ============================================================================================ przykłady
def _split_samples(lines: list[str], pres: list[str]) -> tuple[list[dict], list[str], list[str]]:
    """Wyciąga przykłady z linii sekcji „Przykład” (i okolic). Zwraca (samples, linie_wyjaśnień, pozostałe)."""
    samples: list[dict] = []
    expl: list[str] = []
    rest: list[str] = []
    mode = None            # None | "in" | "out" | "side" | "expl"
    cur: dict | None = None
    buf: list[str] = []

    def flush():
        nonlocal buf, cur
        if mode == "in":
            if cur is None:
                cur = {"name": None, "input": "", "output": ""}
            cur["input"] = _join_sample(buf, pres)
        elif mode == "out":
            if cur is None:
                cur = {"name": None, "input": "", "output": ""}
            cur["output"] = _join_sample(buf, pres)
            samples.append(cur)
            cur = None
        elif mode == "side":
            samples.append({"name": None, "side_by_side": _sample_lines(buf, pres), "side_col": side_col,
                            "input": "", "output": ""})
        elif mode == "expl":
            expl.extend(buf)
        buf = []

    side_col = None
    for ln in lines:
        s = ln.strip()
        ms = _SIDE_RE.match(s)
        if ms:
            flush()
            mode = "side"
            # kolumna, w której zaczyna się wyjście (tekst z „pdftotext -layout”: „301      17”)
            side_col = (len(ln) - len(ln.lstrip())) + ms.start(2) if ms.start(2) - ms.end(1) >= 3 else None
            continue
        m_in = _IN_RE2.match(s) or _IN_RE.match(s)
        m_out = _OUT_RE2.match(s) or _OUT_RE.match(s)
        if m_in and not (mode == "in" and not buf and False):
            flush()
            if cur is not None and cur.get("input"):
                samples.append(cur)       # wejście bez wyjścia
            name = next((g for g in m_in.groups() if g), None) if m_in.groups() else None
            cur = {"name": name.rstrip(":") if name else None, "input": "", "output": ""}
            mode = "in"
            continue
        if m_out and mode in ("in", "side", None, "expl") and (mode == "in" or cur is not None or mode is None):
            flush()
            if cur is None:
                cur = {"name": None, "input": "", "output": ""}
            name = next((g for g in m_out.groups() if g), None) if m_out.groups() else None
            if name and not cur.get("name"):
                cur["name"] = name.rstrip(":")
            mode = "out"
            continue
        sec = _section_of(s)
        if sec == "wyjasnienie" or re.match(r"^\s*(wyja[sś]nienie|obja[sś]nienie)", s, re.I):
            flush()
            mode = "expl"
            m = re.match(r"^\s*(?:wyja[sś]nienie|obja[sś]nienie)[^:]*:\s*(.+)$", s, re.I)
            if m:
                buf.append(m.group(1))
            continue
        if sec == "przyklad":
            flush()
            mode = None
            continue
        if mode in ("in", "out", "side"):
            if s and _is_prose(s) and (buf or mode == "out"):
                flush()
                mode = "expl" if not _SAMPLE_STOP.match(s) or re.match(r"^\s*(komentarz|bo\s*:)", s, re.I) else "tail"
                if mode == "expl":
                    buf.append(s)
                continue
            buf.append(ln)
        elif mode == "tail":
            if s:
                rest.append(ln)
        elif mode == "expl":
            buf.append(ln)
        else:
            if s:
                rest.append(ln)
    flush()
    if cur is not None and cur.get("input"):
        samples.append(cur)
    # przypisz wyjaśnienia do ostatniego przykładu
    if expl and samples:
        samples[-1]["explanation"] = " ".join(x.strip() for x in expl if x.strip())
    return samples, expl, rest


def _sample_lines(buf: list[str], pres: list[str]) -> list[str]:
    out: list[str] = []
    for ln in buf:
        m = _PRE_RE.match(ln.strip())
        if m:
            out += pres[int(m.group(1))].replace("\r\n", "\n").split("\n")
        else:
            out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return [ln.rstrip() for ln in out]


def _join_sample(buf: list[str], pres: list[str]) -> str:
    lines = _sample_lines(buf, pres)
    # w tekście z PDF puste linie to tylko separatory; w <pre> zachowujemy wszystko
    if not any(_PRE_RE.match(b.strip()) for b in buf):
        lines = [ln for ln in lines if ln.strip()]
    txt = "\n".join(lines)
    return txt + ("\n" if txt else "")


# ============================================================================================ ograniczenia
_VAR = r"(?:[A-Za-z](?:_\{[A-Za-z0-9,+\- ]{1,8}\}|_[A-Za-z0-9]{1,3}|'|[0-9]{1,2}(?![0-9])|[ijk](?![A-Za-ząćęłńóśźż]))?)"
_TOKEN_RE = re.compile(
    r"(?P<ws>\s+)|(?P<num>" + VALUE_RE.pattern + r")|(?P<op>≤|≥|<=|>=|<|>|≠|!=|=)"
    r"|(?P<abs>\|\s*" + _VAR + r"\s*\|)|(?P<var>(?<![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_])" + _VAR +
    r"(?![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_]))"
    r"|(?P<comma>,)|(?P<conj>(?<![A-Za-ząćęłńóśźż])(?:i|oraz|and)(?![A-Za-ząćęłńóśźż]))|(?P<mul>[·*×])|(?P<plus>\+)"
    r"|(?P<minus>-)|(?P<other>.)", re.S)
_POLISH_SINGLE = {"i", "w", "z", "o", "a", "u"}


def norm_var(v: str) -> str:
    v = v.strip().replace(" ", "")
    v = re.sub(r"_\{([^}]*)\}", r"_\1", v)
    if re.fullmatch(r"[A-Za-z][ijk]", v):
        v = v[0] + "_" + v[1]
    elif re.fullmatch(r"[A-Za-z][0-9]{1,2}", v):
        v = v[0] + "_" + v[1:]
    return v


def base_var(v: str) -> str:
    return norm_var(v).split("_")[0].strip("|'")


def _tokens(s: str) -> list[tuple[str, str]]:
    out = []
    for m in _TOKEN_RE.finditer(s):
        k = m.lastgroup
        if k == "ws":
            continue
        out.append((k, m.group(0)))
    return out


def _operands(toks: list[tuple[str, str]]):
    """Grupuje tokeny w operandy: ("num", wartość, tekst) | ("vars", [..]) | ("abs", v) | ("expr", tekst) | op | sep."""
    out = []
    i = 0
    n = len(toks)
    while i < n:
        k, t = toks[i]
        if k == "minus" and i + 1 < n and toks[i + 1][0] == "num" and (not out or out[-1][0] in ("op", "sep")):
            v = parse_value(toks[i + 1][1])
            out.append(("num", -v if v is not None else None, "-" + toks[i + 1][1]))
            i += 2
            continue
        if k == "minus" and i + 3 < n and toks[i + 1][1] == "(" and toks[i + 2][0] == "num" and toks[i + 3][1] == ")":
            v = parse_value(toks[i + 2][1])
            out.append(("num", -v if v is not None else None, "-(" + toks[i + 2][1] + ")"))
            i += 4
            continue
        if k == "num":
            out.append(("num", parse_value(t), t))
        elif k == "op":
            out.append(("op", {"<=": "≤", ">=": "≥", "!=": "≠"}.get(t, t)))
        elif k == "abs":
            out.append(("abs", norm_var(t.strip("| "))))
        elif k == "var":
            # lista zmiennych: a, b, c  /  a i b  / n·m (iloczyn) / n+m (suma)
            names = [t]
            j = i + 1
            expr = None
            while j + 1 < n:
                kj, tj = toks[j]
                if kj in ("comma", "conj") and toks[j + 1][0] == "var":
                    names.append(toks[j + 1][1])
                    j += 2
                    continue
                if kj in ("mul", "plus") and toks[j + 1][0] == "var" and len(names) == 1:
                    expr = names[0] + ("·" if kj == "mul" else "+") + toks[j + 1][1]
                    j += 2
                    break
                break
            if expr:
                out.append(("expr", expr.replace(" ", "")))
            else:
                out.append(("vars", [norm_var(x) for x in names]))
            i = j
            continue
        elif k == "comma":
            out.append(("sep", ","))
        elif k == "other" and t in "()[];:":
            out.append(("sep", t))
        else:
            out.append(("sep", t))
        i += 1
    return out


def _chains(ops):
    """Wyszukuje łańcuchy OPERAND (OP OPERAND)+."""
    chains = []
    i = 0
    n = len(ops)
    while i < n:
        if ops[i][0] in ("num", "vars", "abs", "expr") and i + 2 < n and ops[i + 1][0] == "op" and \
                ops[i + 2][0] in ("num", "vars", "abs", "expr"):
            ch = [ops[i]]
            j = i + 1
            while j + 1 < n and ops[j][0] == "op" and ops[j + 1][0] in ("num", "vars", "abs", "expr"):
                nxt = ops[j + 1]
                # „1 ≤ a, b ≤ n, a ≠ b” — lista po prawej stronie, po której jest kolejny operator → rozetnij
                if nxt[0] == "vars" and len(nxt[1]) > 1 and len(ch) >= 2 and j + 2 < n and ops[j + 2][0] == "op":
                    ch += [ops[j], ("vars", nxt[1][:1])]
                    chains.append(ch)
                    ops = ops[:j + 1] + [("vars", nxt[1][1:])] + ops[j + 2:]
                    n = len(ops)
                    ch = None
                    i = j + 1
                    break
                ch += [ops[j], nxt]
                j += 2
            if ch is None:
                continue
            chains.append(ch)
            i = j
            continue
        i += 1
    return chains


def _apply_chain(ch, cons: dict, rel: list, raw: str, integer: bool = True):
    # zamień ≥ na ≤ odwracając łańcuch
    if any(c[0] == "op" and c[1] in ("≥", ">") for c in ch) and not any(c[0] == "op" and c[1] in ("≤", "<") for c in ch):
        ch = list(reversed(ch))
        ch = [("op", {"≥": "≤", ">": "<"}[c[1]]) if c[0] == "op" else c for c in ch]
    ops_ok = all(c[1] in ("≤", "<", "≠") for c in ch if c[0] == "op")
    if not ops_ok:
        return
    for idx in range(0, len(ch), 2):
        item = ch[idx]
        if item[0] == "num":
            continue
        names = item[1] if item[0] == "vars" else [item[1]]
        is_abs = item[0] == "abs"
        lo = hi = None
        hi_big = None              # górna granica to liczba za duża na long long („10^1000000”) → liczba jako napis
        lo_strict = hi_strict = False
        # najbliższe ograniczenia liczbowe z lewej / prawej
        if idx - 2 >= 0 and ch[idx - 1][0] == "op":
            op = ch[idx - 1][1]
            left = ch[idx - 2]
            if op == "≠":
                for nm in names:
                    if left[0] == "vars":
                        rel.append({"type": "neq", "a": left[1][-1], "b": nm, "raw": raw})
            elif left[0] == "num":
                lo, lo_strict = left[1], op == "<"
            elif left[0] == "vars":
                for nm in names:
                    rel.append({"type": "le", "a": left[1][-1], "b": nm, "strict": op == "<", "raw": raw})
        if idx + 2 < len(ch) and ch[idx + 1][0] == "op":
            op = ch[idx + 1][1]
            right = ch[idx + 2]
            if op == "≠":
                for nm in names:
                    if right[0] == "vars":
                        rel.append({"type": "neq", "a": nm, "b": right[1][0], "raw": raw})
                    elif right[0] == "num":
                        rel.append({"type": "neq_val", "a": nm, "value": right[1], "raw": raw})
            elif right[0] == "num":
                hi, hi_strict = right[1], op == "<"
                hi_big = big_number(right[2]) if op == "≤" else None
            elif right[0] == "vars":
                for nm in names:
                    rel.append({"type": "le", "a": nm, "b": right[1][0], "strict": op == "<", "raw": raw})
                # przenieś górne ograniczenie dalej w łańcuchu: k ≤ n ≤ 10^5
                for k2 in range(idx + 4, len(ch), 2):
                    if ch[k2][0] == "num" and ch[k2 - 1][0] == "op":
                        hi, hi_strict = ch[k2][1], ch[k2 - 1][1] == "<"
                        hi_big = big_number(ch[k2][2]) if not hi_strict else None
                        break
        if lo is None and hi is None and hi_big is None:
            continue
        for nm in names:
            if not nm or (nm in _POLISH_SINGLE and lo is None and hi is None):
                continue
            key = nm
            d = cons.setdefault(key, {"min": None, "max": None, "raw": raw})
            if is_abs:
                if hi is not None:
                    d["max"] = hi - (1 if hi_strict and integer else 0)
                    d["min"] = -d["max"]
                    d["abs"] = True
                    # |s| to też długość napisu — zapamiętaj pełny zakres
                    cons.setdefault(f"|{key}|", {"min": lo if lo is not None else 0, "max": d["max"], "raw": raw,
                                                 "length": True})
                continue
            if lo is not None and d.get("min") is None:
                d["min"] = (lo + 1) if (lo_strict and integer and isinstance(lo, int)) else lo
            if hi is not None and d.get("max") is None:
                d["max"] = (hi - 1) if (hi_strict and integer and isinstance(hi, int)) else hi
            if hi_big is not None and not d.get("bignum") and (d.get("max") is None or d.get("max") == hi):
                _mark_big(d, hi_big)
            if d.get("raw") is None:
                d["raw"] = raw


_WORD_BOUNDS = [
    (r"(?:nie|nigdy nie)\s+(?:b[eę]d[aą]\s+)?(?:przekracza(?:ją|j[aą]|ć)?|przekrocz[aąy]\w*|wi[eę]ksz\w*\s+(?:ni[zż]|od))"
     r"\s+(?:warto[sś]ci\s+)?(?P<v>" + VALUE_RE.pattern + ")", "max"),
    (r"co najwy[zż]ej\s+(?P<v>" + VALUE_RE.pattern + ")", "max"),
    (r"mniejsz\w+\s+(?:ni[zż]|od)\s+(?P<v>" + VALUE_RE.pattern + ")", "lt"),
    (r"(?:nie mniejsz\w+\s+(?:ni[zż]|od)|co najmniej)\s+(?P<v>" + VALUE_RE.pattern + ")", "min"),
]
_RANGE_RE = re.compile(r"z\s+(?:zakresu|przedzia[lł]u)\s*\[\s*(?P<a>-?\s*" + VALUE_RE.pattern + r")\s*[,;]\s*(?P<b>-?\s*"
                       + VALUE_RE.pattern + r")\s*\]|z\s+zakresu\s+od\s+(?P<c>-?\s*" + VALUE_RE.pattern +
                       r")\s+do\s+(?P<d>-?\s*" + VALUE_RE.pattern + r")", re.I)
_VARS_IN_SENT = re.compile(r"(?<![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_|])(" + _VAR + r")(?![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_(])")


def _sentences(txt: str) -> list[str]:
    txt = re.sub(r"\s+", " ", txt or "").strip()
    if not txt:
        return []
    parts = re.split(r"(?<=[.;!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ•(]|W\s|Na\s)|\s+(?=•)"
                     # brak kropki (HTML/wklejka): „... szerokość wąwozu W kolejnych N liniach ...”
                     r"|(?<=[a-ząćęłńóśźż)])\s+(?=W (?:kolejnych|następnych|każdym|drugi\w*|trzeci\w*|i-t\w*) )"
                     r"|(?<=[a-ząćęłńóśźż)])\s+(?=(?:Kolejne|Następne|Każdy z|Drugi|Trzeci) \w+ (?:wiersz|lini))", txt)
    return [p.strip() for p in parts if p.strip()]


def _last_var_before(sent: str, pos: int, known: set[str]) -> str | None:
    prev = sent[:pos]
    cands = [norm_var(m.group(1)) for m in _VARS_IN_SENT.finditer(prev)]
    cands = [c for c in cands if c not in _POLISH_SINGLE or c in known]
    return cands[-1] if cands else None


def extract_constraints(text: str, known_vars: set[str] | None = None) -> tuple[dict, list]:
    """Ograniczenia zmiennych z tekstu: {var: {min, max, raw}}, relacje [{type, a, b}]."""
    cons: dict = {}
    rel: list = []
    known = set(known_vars or ())
    for sent in _sentences(text):
        s = sent.replace("<=", "≤").replace(">=", "≥")
        if re.search(r"[≤<≥>≠]", s):
            # tniemy na klauzule po nawiasach/średnikach, ale łańcuch zostaje w całości
            for clause in re.split(r"[;()]|\s-\s|\s–\s", s):
                if not re.search(r"[≤<≥>≠]", clause):
                    continue
                toks = _tokens(clause)
                ops = _operands(toks)
                for ch in _chains(ops):
                    _apply_chain(ch, cons, rel, clause.strip())
        # słowne: „nie przekracza 10^9”, „z zakresu [1, 10^6]”
        low = s.lower()
        for m in _RANGE_RE.finditer(s):
            a = parse_value((m.group("a") or m.group("c") or "").replace(" ", "") if (m.group("a") or m.group("c"))
                            else None)
            b = parse_value((m.group("b") or m.group("d") or "").strip())
            a = parse_value((m.group("a") or m.group("c")).strip()) if (m.group("a") or m.group("c")) else None
            v = _last_var_before(s, m.start(), known) or "_values"
            if a is not None and b is not None:
                d = cons.setdefault(v, {"min": None, "max": None, "raw": s[max(0, m.start() - 60):m.end()]})
                d["min"] = a if d["min"] is None else d["min"]
                d["max"] = b if d["max"] is None else d["max"]
        for pat, kind in _WORD_BOUNDS:
            for m in re.finditer(pat, low, re.I):
                val = parse_value(m.group("v"))
                if val is None:
                    info = big_number(m.group("v")) if kind == "max" else None
                    if info is not None:
                        v = _last_var_before(s, m.start(), known) or "_values"
                        d = cons.setdefault(v, {"min": None, "max": None, "raw": s.strip()[:200]})
                        if not d.get("bignum") and d.get("max") is None:
                            _mark_big(d, info)
                    continue
                orig_pos = m.start()
                v = _last_var_before(s, orig_pos, known) or "_values"
                d = cons.setdefault(v, {"min": None, "max": None, "raw": s.strip()[:200]})
                if "modu" in low[max(0, orig_pos - 40):orig_pos] or "bezwzgl" in low[max(0, orig_pos - 60):orig_pos]:
                    if d["max"] is None:
                        d["max"] = val
                        d["min"] = -val
                        d["abs"] = True
                    continue
                if kind == "max" and d["max"] is None:
                    d["max"] = val
                elif kind == "lt" and d["max"] is None:
                    d["max"] = val - 1 if isinstance(val, int) else val
                elif kind == "min" and d["min"] is None:
                    d["min"] = val
        if re.search(r"\b(dodatni|naturaln)", low):
            for m in re.finditer(r"(?:dodatni\w*|naturaln\w*)\s+(?:liczb\w*\s+)?(?:ca[lł]kowit\w*\s+)?(" + _VAR + r")\b",
                                 s):
                v = norm_var(m.group(1))
                if v not in _POLISH_SINGLE:
                    d = cons.setdefault(v, {"min": None, "max": None, "raw": s[:200]})
                    if d["min"] is None:
                        d["min"] = 1
    # odfiltruj śmieci: zmienne-słowa
    cons = {k: v for k, v in cons.items() if k and (v.get("min") is not None or v.get("max") is not None
                                                    or v.get("bignum"))}
    for v in cons.values():
        if not v.get("bignum") and isinstance(v.get("max"), int) and v["max"] > ULL_MAX:
            _mark_big(v, big_number(str(v["max"])))      # „n ≤ 10^30” — też liczba jako napis
    return cons, rel


def size_bounds(cons: dict) -> dict:
    """Ograniczenia zmiennych bazowych: a_i → a."""
    out = {}
    for k, v in cons.items():
        b = base_var(k) if not k.startswith("_") else k
        if "·" in k or "+" in k:
            b = k
        cur = out.get(b)
        if cur is None:
            out[b] = dict(v)
        else:
            if cur.get("max") is None:
                cur["max"] = v.get("max")
            if cur.get("min") is None:
                cur["min"] = v.get("min")
    return out


# ============================================================================================ podzadania
def _parse_time(s: str):
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


_SUB_ROW = re.compile(r"^\s*(\d{1,2})\s+(.+?)\s+(?:(\d+(?:\s?[.,]\s?\d+)?)\s*(?:s|sek\w*)\s+)?(\d{1,3})\s*%?\s*$")


def _parse_subtasks(lines: list[str], prose: str) -> tuple[list[dict], list[float]]:
    subs: list[dict] = []
    times: list[float] = []
    in_table = False
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^(podzadanie|nr|grupa|subtask)\b", s, re.I):
            in_table = True
            continue
        if _OCEN_HEAD.match(s) or _OCEN_LABEL.match(s):
            in_table = False
            continue
        m = _SUB_ROW.match(s)
        if m and (in_table or re.search(r"[≤<]|brak", s)):
            cond = m.group(2).strip()
            t = _parse_time(m.group(3)) if m.group(3) else None
            if t is not None:
                times.append(t)
            subs.append({"id": int(m.group(1)), "points": int(m.group(4)), "conditions": cond, "time_limit": t})
        elif subs and in_table and not re.match(r"^\d", s) and len(s) < 120:
            subs[-1]["conditions"] += " " + s
    if not subs:
        # prozą: „W testach wartych 30% punktów zachodzi n ≤ 1000.”
        for sent in _sentences(prose):
            m = re.search(r"(?:test\w*|przypadk\w*)[^.]{0,40}?(?:wart\w*(?:\s+[lł][aą]cznie)?|stanowi[aą]cych)?\s*"
                          r"(?:co najmniej\s+|przynajmniej\s+)?(\d{1,3})\s*(%|punkt\w*|pkt)", sent, re.I) or \
                re.search(r"(?:dla|w)\s+(?:co najmniej\s+|przynajmniej\s+)?(\d{1,3})\s*(%)\s+(?:test|przypadk|punkt)",
                          sent, re.I)
            if m and re.search(r"[≤<]|zachodzi|spe[lł]ni", sent):
                cond = re.split(r"(?:zachodzi(?: warunek| dodatkowy warunek)?|spe[lł]nion\w*(?: jest)?(?: warunek)?)\s*:?",
                                sent, maxsplit=1)
                c = cond[1] if len(cond) > 1 else sent
                subs.append({"id": len(subs) + 1, "points": int(m.group(1)), "percent": m.group(2) == "%",
                             "conditions": c.strip(" .:"), "time_limit": None})
    for st in subs:
        c, r = extract_constraints(st["conditions"])
        st["constraints"] = c
    return subs, times


# ============================================================================================ format wejścia
_ORD = {"pierwsz": 1, "drug": 2, "trzec": 3, "czwart": 4, "piąt": 5, "piat": 5, "szóst": 6, "szost": 6,
        "jedyn": 1, "ostatni": -1}
_COUNT_WORDS = {"jedn": 1, "jeden": 1, "dwie": 2, "dwóch": 2, "dwoch": 2, "dwa": 2, "trzy": 3, "trzech": 3,
                "cztery": 4, "czterech": 4, "pięć": 5, "pieć": 5, "piec": 5, "pięciu": 5, "sześć": 6, "szesc": 6}
_LINE_W = r"(?:wierszu|wierszach|wiersz|wiersze|wierszy|linii|liniach|linijce|linijkach|linia|linie|linijka|lini)"
_REPEAT_RE = re.compile(
    r"(?:(?:w\s+)?(?:każdym\s+z\s+|kazdym\s+z\s+|i-t\w+\s+z\s+|i\s*[-–]\s*t\w+\s+z\s+|ka[zż]d\w+\s+z\s+)?"
    r"(?:(?<![A-Za-ząćęłńóśźż])(?P<a>[A-Za-z](?:\s*[-+]\s*\d)?|\d+|dw[oó]ch|trzech|czterech|pi[eę]ciu)\s+)?"
    r"(?:kolejn\w+|nast[eę]pn\w+|dalsz\w+|pozosta[lł]\w+)\s+"
    r"(?:(?<![A-Za-ząćęłńóśźż])(?P<b>[A-Za-z](?:\s*[-+]\s*\d)?|\d+|[A-Za-z]\s*·\s*\d|dw[oó]ch|trzech|czterech|"
    r"pi[eę]ciu)\s+)?" + _LINE_W + r")", re.I)
_REPEAT_RE2 = re.compile(r"(?:kolejn\w+|nast[eę]pn\w+)\s+(?P<b>[A-Za-z](?:\s*[-+]\s*\d)?)\s+(?:" + _LINE_W +
                         r"|wierszy)", re.I)
_REPEAT_RE3 = re.compile(r"(?:^|\s)(?<![A-Za-ząćęłńóśźż])(?P<b>[A-Za-z](?:\s*[-+]\s*\d)?)\s+(?:kolejn\w+\s+|nast[eę]pn\w+\s+)?"
                         r"(?:wierszy|linii|linijek|wiersze|linie)\b(?!\s+wyj)", re.I)


def _ordinal(sent: str) -> int | None:
    low = sent.lower()
    m = re.search(r"(?:w|we)\s+(pierwsz|drug|trzec|czwart|pi[aą]t|sz[oó]st|jedyn|ostatni)\w*\s+(?:i\s+jedyn\w+\s+)?"
                  r"(?:" + _LINE_W + r"|z\s+nich|z\s+tych\s+" + _LINE_W + r")", low)
    if not m:
        m = re.search(r"(pierwsz|drug|trzec|czwart|pi[aą]t|sz[oó]st|jedyn|ostatni)\w*\s+(?:i\s+jedyn\w+\s+)?"
                      r"(?:wiersz|lini|linijk)\w*", low)
    if m:
        k = m.group(1).replace("ą", "a").replace("ó", "o")
        for key, val in _ORD.items():
            if k.startswith(key.replace("ą", "a").replace("ó", "o")):
                return val
    if re.search(r"(?:^|\s)(?:na|w)\s+(?:standardowym\s+)?wej[sś]ciu\s+(?:znajduj|jest|podan|b[eę]d)", low) or \
            re.search(r"^(?:wej[sś]cie|dane)\s+(?:zawiera|sk[lł]ada)", low) or \
            re.search(r"^(?:jedyny|pierwszy) wiersz", low) or \
            re.search(r"^w\s+(?:dw[oó]ch|trzech|czterech)\s+(?:pierwszych\s+|kolejnych\s+)?(?:wierszach|liniach)", low):
        return 1
    if re.search(r"^(?:w\s+)?(?:kolejn|nast[eę]pn)\w*\s+(?:wierszu|linii|linijce|wiersz|linia|linijka)\b", low) or \
            re.search(r"^(?:w\s+)?(?:kolejn|nast[eę]pn)(?:ej|ym|y|a)\s+(?:wierszu|linii|linijce|wiersz|linia|linijka)", low) \
            or re.search(r"^(?:następnie|potem|dalej|po nich|po tym)\b.{0,40}(?:liczb|napis|s[lł]ow|warto|ci[aą]g)", low):
        return 0      # „następny wiersz” — numer ustalany względem poprzedniego
    return None


_TYPE_WORDS = [
    (r"liczb\w*\s+rzeczywist|liczb\w*\s+zmiennoprzecink|u[lł]amk\w+ dziesi[eę]tn", "double"),
    (r"napis|s[lł]ow[oa]\b|s[lł]owo|ci[aą]g\w*\s+(?:ma[lł]ych\s+|du[zż]ych\s+)?(?:liter|znak)|tekst|wyraz|"
     r"ci[aą]g\s+zer\s+i\s+jedynek|string|ci[aą]g\w* sk[lł]adaj[aą]c\w* si[eę] z (?:liter|znak|ma[lł]ych)", "string"),
    (r"liczb", "int"),
]
_MODIFIERS = (r"(?:\s+(?:ca[lł]kowit\w*|naturaln\w*|rzeczywist\w*|dodatni\w*|nieujemn\w*|ma[lł]ych|du[zż]ych|"
              r"liter\w*|znak\w*|binarn\w*|oddzielon\w*(?:\s+(?:pojedynczymi\s+)?(?:spacj|odst[eę]p)\w*)?|"
              r"pojedyncz\w*|odst[eę]p\w*|spacj\w*|rozdzielon\w*|kolejn\w*|parami\s+r[oó][zż]n\w*|"
              r"r[oó][zż]n\w*|wej[sś]ciow\w*))*")


def _sentence_vars(sent: str) -> list[str]:
    """Nazwy zmiennych w zdaniu opisu wejścia (bez słów jednoliterowych)."""
    s = re.sub(r"\([^)]*[≤<≥>][^)]*\)", " ", sent)          # usuń nawiasy z ograniczeniami
    s = re.sub(r"-?\d+\s*≤[^,;)]*", " ", s)
    names: list[str] = []
    for m in re.finditer(r"(?<![A-Za-ząćęłńóśźż])(?:liczb\w*|warto[sś]\w*|napis\w*|s[lł]ow\w*|ci[aą]g\w*|"
                         r"znak\w*|wyraz\w*|tekst\w*|parametr\w*)(?!\w)" + _MODIFIERS + r"\s*[:,]?\s*"
                         r"(?P<list>" + _VAR + r"(?:\s*(?:,|\bi\b|\boraz\b)\s*" + _VAR +
                         r")*(?:\s*,?\s*(?:\.\.\.|…|\.\s\.\s\.|·\s·\s·)\s*,?\s*" + _VAR +
                         r")?)(?![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_(])", s):
        raw = m.group("list")
        tail = s[m.end():m.end() + 20]
        if raw.strip() in _POLISH_SINGLE and re.match(r"\s+(?!i\b|oraz\b)[a-ząćęłńóśźż]{2,}", tail):
            continue            # „liczb z przedziału” — „z” to przyimek, nie zmienna
        for part in re.split(r"\s*(?:,|\bi\b|\boraz\b|\.\.\.|…|\.\s\.\s\.|·\s·\s·)\s*", raw):
            part = part.strip(" .")
            if not part or not re.fullmatch(_VAR, part):
                continue
            names.append(norm_var(part))
        if names:
            break
    if not names:
        # „... 3 liczby oddzielone spacjami: n – ilość latarni, y – koszt ..., z – fundusze”
        for m in re.finditer(r"(?:^|[:,;]\s*|\s)(" + _VAR + r")\s*[–-]\s+(?:ilo[sś][cć]|liczb|koszt|warto|d[lł]ugo|"
                             r"szeroko|wysoko|numer|[a-ząćęłńóśźż]{3,})", s):
            v = norm_var(m.group(1))
            if v not in _POLISH_SINGLE or v in ("a", "w", "z"):
                names.append(v)
    if not names:
        # zmienne z ograniczeń w tym zdaniu: „liczba całkowita (1 ≤ n ≤ 10^6)”
        c, _ = extract_constraints(sent)
        names = [k for k in c if not k.startswith("_")][:4]
    out: list[str] = []
    for nm in names:
        if nm in _POLISH_SINGLE and nm not in ("a", "w", "z", "u"):
            continue
        if nm not in out:
            out.append(nm)
    return out


def _collapse_array_names(names: list[str]) -> tuple[list[str], str | None]:
    """[a_1, a_2, a_n] → (['a'], 'n');  [a_1, an] → (['a'], 'n')."""
    if len(names) >= 2:
        fixed = list(names)
        b0 = base_var(fixed[0])
        if "_" in fixed[0]:
            # „an” / „am” / „ak” na końcu listy a1, a2, ... to a_n
            last = fixed[-1]
            if re.fullmatch(re.escape(b0) + r"[a-zA-Z]", last):
                fixed[-1] = b0 + "_" + last[-1]
        bases = {base_var(x) for x in fixed}
        subs = [x.split("_", 1)[1] if "_" in x else "" for x in fixed]
        if len(bases) == 1 and all(subs):
            last = subs[-1]
            ln = last if re.fullmatch(r"[A-Za-z](?:[-+]\d)?", last) else None
            if ln and subs[0] == "0":
                # indeksy od zera: a_0, a_1, ..., a_{n-1} → n elementów; a_0 … a_n → n + 1
                m = re.fullmatch(r"([A-Za-z])-1", ln)
                ln = m.group(1) if m else (f"{ln}+1" if re.fullmatch(r"[A-Za-z]", ln) else ln)
            return [bases.pop()], ln
    if len(names) == 1 and "_" in names[0]:
        b, sub = names[0].split("_", 1)
        if sub in ("i", "j", "k"):
            return [b], None
    return names, None


def _count_in(sent: str) -> tuple[int | str | None, str | None]:
    """Ile liczb opisuje zdanie: („dwie liczby” → 2), („3 liczby” → 3), („n liczb” → 'n')."""
    low = sent.lower()
    m = re.search(r"(?<![A-Za-ząćęłńóśźż])(jedn\w*|dwie|dwóch|dwoch|dwa|trzy|trzech|cztery|czterech|pi[eę][cć]|"
                  r"pi[eę]ciu|sze[sś][cć])\s+(?:\w+\s+){0,2}?(?:liczb|warto|napis|s[lł]ow|znak|parametr)", low)
    if m:
        w = m.group(1)
        for key, val in _COUNT_WORDS.items():
            if w.startswith(key[:4]) or w == key:
                return val, None
        return 1, None
    m = re.search(r"\bpo\s+([a-zA-Z])\s+(?:[\wąćęłńóśźż]+\s+)?(?:liczb|warto|znak|element)", sent)
    if m and m.group(1).lower() not in ("i", "w", "z", "o", "a", "u"):
        return m.group(1), "array"
    m = re.search(r"(?<![\w.,])(?<![-+·*]\s)(?<![-+·*])([1-9])\s+(?:\w+\s+){0,1}?(?:liczb|warto[sś]ci|napis)", low)
    if m:
        return int(m.group(1)), None
    for m in re.finditer(r"(?<![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ_])(?=([a-zA-Z](?:\s*[-+·*]\s*\d)?)\s+(?:[\wąćęłńóśźż]+\s+){0,3}?"
                         r"(?:liczb|warto[sś]ci|znak|s[lł][oó]w|napis|element|wyraz|ma[lł]ych liter|liter|cyfr))", sent):
        # „W drugim wierszu n liczb” — pierwsze dopasowanie to „W …”, szukamy dalej
        if m.group(1).lower() not in ("i", "w", "z", "o", "a", "u"):
            return m.group(1).replace(" ", ""), "array"
    m = re.search(r"(?:ci[aą]g|tablic\w*|permutacj\w*)\s+(?:\w+\s+){0,1}?([a-zA-Z](?:\s*[-+]\s*\d)?)\s+"
                  r"(?:liczb|element|warto)", sent)
    if m and m.group(1).lower() not in ("i", "w", "z", "o", "u"):
        return m.group(1).replace(" ", ""), "array"
    m = re.search(r"(?:ci[aą]g|permutacj\w*|tablic\w*)\s+(?:d[lł]ugo[sś]ci\s+)?([a-zA-Z])\b", sent)
    if m and m.group(1).lower() not in ("i", "w", "z", "o", "u", "a"):
        return m.group(1), "array"
    return None, None


def infer_input_format(wejscie: str, samples: list[dict] | None = None, cons: dict | None = None) -> list[dict]:
    """Opis wejścia → lista elementów formatu (zdania → linie/powtórzenia/zmienne)."""
    items: list[dict] = []
    sents = _sentences(wejscie)
    line_no = 0
    multitest = None
    for idx, sent in enumerate(sents):
        low = sent.lower()
        if re.match(r"^(możesz założyć|mozesz zalozyc|zakładamy|zak[lł]adamy|wiadomo|gwarantujemy|uwaga|"
                    r"liczby w (?:ka[zż]dym|drugim|trzecim|tym|jednym)|w ka[zż]dym wierszu liczby|"
                    r"liczby (?:w|s[aą]) (?:wierszach )?(?:pooddzielan|oddzielon|rozdzielon))", low):
            continue
        ordn = _ordinal(sent)
        rep = None
        mrep = _REPEAT_RE.search(sent) or _REPEAT_RE2.search(sent) or _REPEAT_RE3.search(sent)
        if mrep:
            g = mrep.groupdict()
            rep = (g.get("a") or g.get("b") or "").replace(" ", "") or None
            if rep and rep.lower()[:3] in ("dwo", "dwó", "trz", "czt", "pie", "pię"):
                rep = {"dwo": "2", "dwó": "2", "trz": "3", "czt": "4", "pie": "5", "pię": "5"}[rep.lower()[:3]]
            if rep and rep.lower() in ("i", "w", "z", "o", "a", "u") and not re.fullmatch(r"[A-Z]", rep):
                rep = None
            if rep is None and re.search(r"(?:kolejn|nast[eę]pn|pozosta[lł])(?:ych|e)\s+(?:wierszach|liniach|linijkach|"
                                         r"wierszy|wiersze|linie|linii)", low):
                rep = "?"
            if rep is None:
                mrep = None
        if multitest is None and ordn == 1 and idx < 2 and re.search(
                r"(?:liczb\w*|ilo[sś][cć])\s+(?:\w+\s+)?(?:zestaw|test|przypadk|zapyta[nń] testow)|zestaw\w* danych|"
                r"liczba test", low):
            vs = _sentence_vars(sent) or ["t"]
            multitest = vs[0]
            items.append({"line": "1", "kind": "multitest", "vars": [vs[0]], "types": ["int"], "desc": sent})
            line_no = 0
            continue
        if ordn == 0:
            ordn = line_no + 1
        is_line_sent = ordn is not None or rep is not None
        if not is_line_sent:
            # „Każdy test składa się z dwóch linii.” — sam opis bloku
            if multitest and re.search(r"(?:ka[zż]d\w+|jeden)\s+(?:test|zestaw)\w*\s+sk[lł]ada", low):
                line_no = 0
                continue
            # zdanie doprecyzowujące poprzedni element: „Opis jednej krawędzi składa się z dwóch liczb a, b”
            if items and re.search(r"sk[lł]ada si[eę]|opis\w* (?:jednej|jednego|ka[zż]d)|ka[zż]d\w+ z nich|oznaczaj|"
                                   r"zawiera", low):
                vs = _sentence_vars(sent)
                cnt, _ = _count_in(sent)
                last = items[-1]
                if vs and last["kind"] in ("rows", "queries") and (not last["vars"] or last["vars"] == ["?"]):
                    last["vars"] = vs
                    last["types"] = ["int"] * len(vs)
                    last["kind"] = "rows"
                    last["desc"] += " " + sent
                elif last["kind"] in ("rows", "queries") and not last["vars"] and \
                        re.search(r"\bznak(?:ów|ow)\b", low) and not re.search(r"liczb", low):
                    # „W kolejnych n liniach … Opis jednego wiersza składa się z n znaków” — plansza
                    last.update(kind="grid", types=["string"], vars=["plansza"])
                    if isinstance(cnt, str):
                        last["length"] = cnt
                    last["desc"] += " " + sent
                elif cnt and isinstance(cnt, int) and last["kind"] == "rows" and not last["vars"]:
                    last["vars"] = [f"x{i + 1}" for i in range(cnt)]
                    last["types"] = ["int"] * cnt
                else:
                    last["desc"] += " " + sent
            continue
        vs = _sentence_vars(sent)
        cnt, cnt_kind = _count_in(sent)
        typ = "int"
        for pat, t in _TYPE_WORDS:
            if re.search(pat, low):
                typ = t
                break
        if rep is not None and multitest and rep == multitest:
            # „W każdym z kolejnych t wierszy …” przy t zestawach = jeden wiersz na zestaw
            vs2, ln = _collapse_array_names(vs)
            if typ == "string" and len(vs2) <= 1:
                items.append({"line": "2", "kind": "string", "vars": vs2[:1] or ["s"], "types": ["string"], "desc": sent,
                              "per_test": True})
            else:
                if not vs2 and isinstance(cnt, int):
                    vs2 = [f"x{i + 1}" for i in range(cnt)] if cnt > 1 else ["x"]
                items.append({"line": "2", "kind": "scalars", "vars": vs2 or ["x"], "types": [typ] * max(1, len(vs2)),
                              "desc": sent, "per_test": True})
            continue
        if rep is not None:
            start = (line_no + 1) if ordn is None else ordn
            vs2, ln = _collapse_array_names(vs)
            kind = "rows"
            row_len = None
            if typ == "int" and not vs2 and re.search(r"\bznak(?:ów|ow)\b", low) and not re.search(r"liczb", low):
                typ = "string"          # „Każdy z kolejnych n wierszy zawiera m znaków” — plansza
            if re.search(r"zapyta|operacj|polece|instrukcj|zdarze|komend|rozkaz", low) and (
                    not vs2 or re.search(r"(?<![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ])[A-ZĄĆĘŁŃÓŚŹŻ_]{2,}(?![A-Za-z])", sent)):
                kind = "queries"        # „PUSH x”, „OBSLUZ” — wiersze z nazwą operacji
            elif typ == "string" and len(vs2) <= 1 and not re.search(r"liczb", low):
                kind = "grid" if re.search(r"plansz|map\w|siatk|krat|labirynt|wiersz\w* plansz|znak|pol[ae]", low) \
                    else "lines"
            elif cnt_kind == "array" and isinstance(cnt, str) and cnt != rep:
                row_len = cnt         # „W każdym z kolejnych n wierszy znajduje się m liczb” — macierz
                vs2 = vs2[:1] or ["a"]
            elif cnt_kind == "array" and cnt == rep and len(vs2) <= 1 and not re.search(r"\bpar\w*\b|tr[oó]jk", low) \
                    and re.search(_LINE_W + r"\w*\b.{0,60}?(?<![A-Za-z])" + re.escape(str(rep)) +
                                  r"\s+(?:[\wąćęłńóśźż]+\s+){0,2}?liczb", sent):
                row_len = cnt         # „W i-tej z kolejnych n linii znajduje się n liczb” — macierz n × n
                vs2 = vs2[:1] or ["a"]
            elif re.search(r"po jednej liczbie|jedn[aą] liczb[eę]", low) and len(vs2) <= 1:
                vs2 = vs2[:1] or ["a"]
            elif not vs2 and isinstance(cnt, int):
                vs2 = [f"x{i + 1}" for i in range(cnt)]
            mt = re.search(r"(?:liczb\w*|wiersz\w*|zero|warto[sś][cć])\s+(-?\d+)\s+(?:ko[nń]czy|oznacza koniec|zamyka)", low) \
                or re.search(r"(-?\d+)\s+(?:ko[nń]czy|oznacza koniec)", " ".join(sents[idx:idx + 3]).lower())
            if rep == "?" and mt:
                rep = "until:" + mt.group(1)
            elif rep == "?" and re.search(r"do ko[nń]ca (?:wej[sś]cia|pliku|danych)", " ".join(sents[idx:]).lower()):
                rep = "EOF"
            it = {"line": f"{start}..", "kind": kind, "repeat": rep, "vars": vs2,
                  "types": ([typ if kind == "rows" else "string"] * max(1, len(vs2))) if vs2 else [], "desc": sent}
            if row_len:
                it["length"] = row_len
            if multitest:
                it["per_test"] = True
            items.append(it)
            line_no = start
            continue
        if ordn == -1:
            ordn = line_no + 1
        line_no = ordn
        vs2, ln = _collapse_array_names(vs)
        base = {"line": str(ordn), "desc": sent}
        if multitest:
            base["per_test"] = True
        if cnt_kind == "array" or ln or (len(vs) >= 2 and vs2 != vs):
            length = ln or (cnt if isinstance(cnt, str) else None)
            arr_v = vs2[:1]
            if arr_v and length is not None and arr_v[0] == str(length):
                arr_v = []              # „n liczb całkowitych” — n to długość, nie nazwa ciągu
            if not arr_v:
                mname = re.search(r"[–-]\s*([A-Za-z])\s*[.;]?\s*$", sent)     # „ciąg n liczb całkowitych – b.”
                if mname and mname.group(1) not in _POLISH_SINGLE | {str(length)}:
                    arr_v = [mname.group(1)]
            if not arr_v:
                taken = {v for x in items for v in (x.get("vars") or [])}
                arr_v = [next((c for c in ("a", "b", "c", "d") if c not in taken), "a")]
            if typ == "string" and not re.search(r"liczb", low):
                items.append(dict(base, kind="string", vars=arr_v or ["s"], types=["string"], length=length))
            else:
                items.append(dict(base, kind="array", vars=arr_v or ["a"], length=length, types=[typ]))
            continue
        if typ == "string" and (not vs or len(vs) <= 2) and not re.search(r"liczb\w*\s+(?:ca[lł]k|natur)", low):
            if isinstance(cnt, int) and cnt > 1 and len(vs) < cnt:
                vs = (vs + ["s", "t", "u", "v"])[:cnt]
            for i, v in enumerate(vs or ["s"]):
                items.append(dict(base, kind="string", vars=[v], types=["string"], line=str(ordn + i)))
            line_no = ordn + max(0, len(vs or ["s"]) - 1)
            continue
        if not vs and isinstance(cnt, int):
            vs = [f"x{i + 1}" for i in range(cnt)] if cnt > 1 else ["x"]
        if not vs and re.search(r"(?:zawiera|znajduje si[eę]|jest|podan\w*)\s+(?:\w+\s+)?(?:liczb\w*|sta[lł]\w*|"
                                r"warto[sś]\w*|parametr\w*)", low):
            vs = ["x"]
        if not vs:
            continue
        if isinstance(cnt, int) and cnt > len(vs):
            pool = [x for x in ("n", "m", "k", "q", "x", "y") if x not in vs]
            while len(vs) < cnt and pool:
                vs.append(pool.pop(0))
        items.append(dict(base, kind="scalars", vars=vs, types=[typ] * len(vs),
                          count=cnt if isinstance(cnt, int) else None))
    _assign_types(items, cons or {})
    if samples:
        items = _reconcile_with_samples(items, samples, [k for k in (cons or {}) if re.fullmatch(r"[A-Za-z]", k)])
    return items


def _assign_types(items: list[dict], cons: dict) -> None:
    sb = size_bounds(cons)
    for it in items:
        types = []
        for i, v in enumerate(it.get("vars") or []):
            t = (it.get("types") or ["int"] * 10)[i] if i < len(it.get("types") or []) else "int"
            b = sb.get(base_var(v)) or {}
            mx = b.get("max")
            mn = b.get("min")
            if t in ("int", "long long") and b.get("bignum") and "_" not in norm_var(v):
                t = "bignum"            # liczba z tysiącami cyfr — w programie to napis
            if t == "int" and ((isinstance(mx, (int, float)) and abs(mx) > INT32_MAX) or
                               (isinstance(mn, (int, float)) and abs(mn) > INT32_MAX)):
                t = "long long"
            if t == "int" and (isinstance(mx, float) and not float(mx).is_integer()):
                t = "double"
            types.append(t)
        it["types"] = types


# ------------------------------------------------------------------------------------------- dopasowanie do przykładów
def _tok_lines(inp: str) -> list[list[str]]:
    return [ln.split() for ln in inp.replace("\r\n", "\n").split("\n") if ln.strip()]


def _is_int(t: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+", t))


def _type_ok(tok: str, typ: str | None) -> bool:
    if typ in ("int", "long long", "bignum"):
        return _is_int(tok)
    if typ == "double":
        return bool(re.fullmatch(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?", tok))
    return True


def _eval_len(expr, env: dict):
    if expr is None:
        return None
    if isinstance(expr, int):
        return expr
    e = str(expr).replace(" ", "")
    if re.fullmatch(r"\d+", e):
        return int(e)
    m = re.fullmatch(r"([A-Za-z]\w*)(?:([-+*])(\d+))?", e)
    if m and m.group(1) in env:
        v = env[m.group(1)]
        if m.group(2) == "-":
            v -= int(m.group(3))
        elif m.group(2) == "+":
            v += int(m.group(3))
        elif m.group(2) == "*":
            v *= int(m.group(3))
        return v
    m = re.fullmatch(r"([A-Za-z])·(\d+)", e)
    if m and m.group(1) in env:
        return env[m.group(1)] * int(m.group(2))
    return None


def check_format(fmt: list[dict], inp: str) -> dict:
    """Symuluje czytanie przykładu wg formatu. {ok, consumed_lines, env, reason}."""
    lines = _tok_lines(inp)
    li = 0
    env: dict = {}
    spans: dict[int, list] = {}          # indeks elementu formatu → [pierwsza linia, za ostatnią] (0-based)
    pos_of = {id(x): i for i, x in enumerate(fmt)}

    def run_items(items) -> tuple[bool, str]:
        nonlocal li
        for it in items:
            k = it["kind"]
            spans.setdefault(pos_of.get(id(it), -1), [li, li])
            ok_, why_ = _one(it, k)
            spans[pos_of.get(id(it), -1)][1] = li
            if not ok_ or why_:
                return ok_, why_
        return True, ""

    def _one(it, k) -> tuple[bool, str]:
        nonlocal li
        if True:
            if k in ("scalars",):
                if li >= len(lines):
                    return False, "za mało linii"
                toks = lines[li]
                need = len(it["vars"]) or len(toks)
                if len(toks) < need:
                    return False, f"linia {li + 1}: oczekiwano {need} wartości"
                types = it.get("types") or []
                for j, (v, t) in enumerate(zip(it["vars"], toks)):
                    if not _type_ok(t, types[j] if j < len(types) else None):
                        return False, f"linia {li + 1}: „{t}” to nie jest {types[j]}"
                    if _is_int(t):
                        # liczba z milionem cyfr: Python 3.11+ nie zamieni jej na int — wystarczy „bardzo dużo”
                        env[base_var(v)] = int(t) if len(t) <= 4000 else 10**4000
                if len(toks) != need:
                    return False, f"linia {li + 1}: {len(toks)} wartości zamiast {need}"
                li += 1
            elif k in ("array", "string"):
                if li >= len(lines):
                    return False, "za mało linii"
                L = _eval_len(it.get("length"), env)
                if k == "string":
                    li += 1
                    return True, ""
                if L is None:
                    typ = (it.get("types") or [None])[0]
                    if not all(_type_ok(t, typ) for t in lines[li]):
                        return False, f"linia {li + 1}: wartości nie są typu {typ}"
                    env[base_var(it["vars"][0]) + "#len"] = len(lines[li])
                    li += 1
                    return True, ""
                got = 0
                typ = (it.get("types") or [None])[0]
                while got < L and li < len(lines):
                    if not all(_type_ok(t, typ) for t in lines[li]):
                        return False, f"linia {li + 1}: wartości nie są typu {typ}"
                    got += len(lines[li])
                    li += 1
                if got != L:
                    return False, f"tablica {it['vars'][0]}: {got} wartości zamiast {L}"
            elif k in ("rows", "queries", "lines", "grid"):
                rp = str(it.get("repeat") or "")
                if rp.startswith("until:"):
                    term = rp.split(":", 1)[1]
                    while li < len(lines) and lines[li] != [term]:
                        li += 1
                    if li >= len(lines):
                        return False, f"brak wiersza kończącego {term}"
                    li += 1
                    return True, ""
                if rp == "EOF":
                    li = len(lines)
                    return True, ""
                R = _eval_len(it.get("repeat"), env)
                if R is None:
                    return True, "nieznana liczba powtórzeń"     # do końca
                if li + R > len(lines):
                    return False, f"za mało wierszy ({len(lines) - li} zamiast {R})"
                if k == "rows" and it.get("length"):
                    w = _eval_len(it["length"], env)
                    if w is not None:
                        for r in range(R):
                            if len(lines[li + r]) != w:
                                return False, f"wiersz {li + r + 1}: {len(lines[li + r])} wartości zamiast {w}"
                elif k == "rows" and it.get("vars"):
                    w = len(it["vars"])
                    types = it.get("types") or []
                    for r in range(R):
                        if len(lines[li + r]) != w:
                            return False, f"wiersz {li + r + 1}: {len(lines[li + r])} wartości zamiast {w}"
                        for j, t in enumerate(lines[li + r]):
                            if not _type_ok(t, types[j] if j < len(types) else None):
                                return False, f"wiersz {li + r + 1}: „{t}” to nie jest {types[j]}"
                li += R
            elif k == "multitest":
                if li >= len(lines) or not _is_int(lines[li][0]):
                    return False, "brak liczby testów"
                tcount = int(lines[li][0])
                li += 1
                sub = [x for x in fmt if x.get("per_test")]
                for _ in range(tcount):
                    ok, why = run_items(sub)
                    if not ok:
                        return False, why
                return True, ""
        return True, ""

    top = [x for x in fmt if not x.get("per_test")]
    ok, why = run_items(top)
    if ok and why == "" and li != len(lines):
        return {"ok": False, "consumed_lines": li, "env": env, "spans": spans,
                "reason": f"zostało {len(lines) - li} nieprzeczytanych linii"}
    return {"ok": ok, "consumed_lines": li, "env": env, "reason": why, "spans": spans}


def _reconcile_with_samples(items: list[dict], samples: list[dict], cons_names: list[str] | None = None) -> list[dict]:
    real = [s for s in samples if s.get("input")]
    if not real:
        return items
    if items and all(check_format(items, s["input"])["ok"] for s in real):
        for it in items:
            it.setdefault("source", "text")
        return items
    # spróbuj naprawić: uzupełnij nazwy/liczby z przykładów
    fixed = [dict(it) for it in items]
    lines = _tok_lines(real[0]["input"])
    if fixed and fixed[0]["kind"] == "scalars" and lines:
        n0 = len(lines[0])
        if len(fixed[0]["vars"]) != n0 and n0 <= 6:
            vs = fixed[0]["vars"][:n0]
            pool = [x for x in ("n", "m", "k", "q", "x", "y") if x not in vs]
            while len(vs) < n0:
                vs.append(pool.pop(0) if pool else f"x{len(vs) + 1}")
            fixed[0]["vars"] = vs
            fixed[0]["types"] = (fixed[0]["types"] + ["int"] * n0)[:n0]
    for it in fixed:
        if it["kind"] == "rows" and it.get("repeat") == "?":
            it["repeat"] = None
    fixed = _fix_rows_from_samples(fixed, real)
    if fixed and all(check_format(fixed, s["input"])["ok"] for s in real):
        for it in fixed:
            it.setdefault("source", "text+samples")
        return fixed
    guess = _guess_from_samples(real, items, cons_names)
    if guess:
        return guess
    for it in items:
        it.setdefault("source", "text")
        it["unverified"] = True
    return items


def _fix_rows_from_samples(items: list[dict], samples: list[dict]) -> list[dict]:
    """Wiersze o złej szerokości/typach („imię zawodnika oraz liczba p” → [napis, liczba]) — szerokość i typy kolumn
    bierzemy z przykładu, w którym czytanie się wyłożyło (nazwy liczbowych kolumn zostają z treści)."""
    items = [dict(it) for it in items]
    for _ in range(3):
        bad = None
        for s in samples:
            c = check_format(items, s["input"])
            if not c["ok"]:
                bad = (c, s)
                break
        if bad is None:
            return items
        c, s = bad
        idxs = [k for k in c.get("spans", {}) if k >= 0]
        if not idxs:
            return items
        idx = max(idxs)
        it = items[idx]
        if it.get("kind") != "rows" or it.get("length"):
            return items
        a = c["spans"][idx][0]
        R = _eval_len(it.get("repeat"), c["env"])
        lines = _tok_lines(s["input"])[a:a + (R if isinstance(R, int) and R > 0 else 1)]
        if not lines or len({len(ln) for ln in lines}) != 1:
            return items
        w = len(lines[0])
        if not 1 <= w <= 6:
            return items
        cols = [[ln[j] for ln in lines] for j in range(w)]
        types = ["int" if all(_is_int(t) for t in col) else
                 ("double" if all(_type_ok(t, "double") for t in col) else "string") for col in cols]
        old = list(it.get("vars") or [])
        old_types = list(it.get("types") or [])
        if w == 1 and types == ["string"]:
            # jeden napis w wierszu — to plansza (równe długości) albo lista słów
            same = len({len(ln[0]) for ln in lines}) == 1
            it.update(kind="grid" if same and re.search(r"plansz|map\w|siatk|krat|labirynt|pol[aey]|znak",
                                                         (it.get("desc") or "").lower()) else ("grid" if same and
                                                                                              len(lines) > 1 else "lines"),
                      vars=old[:1] if old and not _is_int(old[0]) else (["plansza"] if same else ["s"]),
                      types=["string"])
            continue
        envv = c.get("env") or {}
        mat = next((v for v in ([str(it.get("repeat") or "")] + [k for k in envv if "#" not in k])
                    if envv.get(v) == w and re.fullmatch(r"[A-Za-z]\w*", v)
                    and re.search(r"(?<![A-Za-z])" + re.escape(v) + r"\s+(?:[\wąćęłńóśźż]+\s+){0,2}?liczb",
                                  it.get("desc") or "")), None)
        if mat and all(t == "int" for t in types):
            it.update(vars=(old[:1] if old else ["a"]), types=["int"], length=mat)
            continue
        if len(old) == w and [("string" if t == "string" else "num") for t in old_types] == \
                [("string" if t == "string" else "num") for t in types]:
            return items
        used = {v for x in items if x is not it for v in (x.get("vars") or [])}
        n_num = sum(1 for t in types if t != "string")
        cand: list[str] = []
        for k in extract_constraints(it.get("desc") or "")[0]:
            b = base_var(k)
            if not k.startswith("_") and b not in used and b not in cand:
                cand.append(b)
        if len(cand) == n_num:
            num_names = cand            # „po dwie liczby 1 ≤ a, b ≤ n” → a, b
        elif len(old) == w:
            num_names = [v for v in old if not re.fullmatch(r"x\d*", v)]
        else:
            num_names = []
        names = []
        for t in types:
            if t != "string" and num_names:
                names.append(num_names.pop(0))
                continue
            pool = ("s", "t", "u", "w") if t == "string" else ("a", "b", "c", "d", "e", "x", "y", "z")
            nm = next((p for p in pool if p not in used and p not in names), f"k{len(names) + 1}")
            names.append(nm)
        it["vars"] = names
        it["types"] = [("long long" if (t == "int" and i < len(old_types) and old_types[i] == "long long") else t)
                       for i, t in enumerate(types)]
    return items


def _guess_from_samples(samples: list[dict], text_items: list[dict],
                        cons_names: list[str] | None = None) -> list[dict] | None:
    """Hipotezy kształtu wejścia sprawdzane na wszystkich przykładach."""
    first_vars = text_items[0]["vars"] if text_items and text_items[0]["kind"] == "scalars" else []
    lines0 = _tok_lines(samples[0]["input"])
    if not lines0:
        return None
    c0 = len(lines0[0])
    if not first_vars and cons_names and len(cons_names) == c0 and len(lines0) == 1:
        first_vars = list(cons_names)     # „Trzy liczby a, b, m (…)” bez numeru wiersza — nazwy z ograniczeń

    def names(k):
        vs = list(first_vars[:k])
        pool = [x for x in ("n", "m", "k", "q", "x", "y", "z") if x not in vs]
        i = 1
        while len(vs) < k:
            vs.append(pool.pop(0) if pool else f"x{i}")
            i += 1
        return vs

    hyps: list[list[dict]] = []
    hv = names(c0)
    all_int0 = all(_is_int(t) for t in lines0[0])
    arr_name = next((it["vars"][0] for it in text_items if it["kind"] == "array" and it.get("vars")), "a")
    row_vars = next((it["vars"] for it in text_items if it["kind"] == "rows" and it.get("vars")), None)
    hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0}])
    if all_int0:
        for vi, v in enumerate(hv):
            # n, potem n liczb (w jednej lub wielu liniach)
            hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                         {"line": "2", "kind": "array", "vars": [arr_name], "length": v, "types": ["int"]}])
        for vi, v in enumerate(hv):
            for w in (1, 2, 3, 4):
                rv = row_vars if row_vars and len(row_vars) == w else [["a"], ["a", "b"], ["a", "b", "c"],
                                                                       ["a", "b", "c", "d"]][w - 1]
                hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                             {"line": "2..", "kind": "rows", "repeat": v, "vars": rv, "types": ["int"] * w}])
                hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                             {"line": "2..", "kind": "rows", "repeat": f"{v}-1", "vars": rv, "types": ["int"] * w}])
            hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                         {"line": "2..", "kind": "grid", "repeat": v, "vars": ["plansza"], "types": ["string"]}])
            hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                         {"line": "2..", "kind": "lines", "repeat": v, "vars": ["s"], "types": ["string"]}])
        if c0 >= 2:
            for v1 in hv:
                for v2 in hv:
                    if v1 == v2:
                        continue
                    hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                                 {"line": "2", "kind": "array", "vars": [arr_name], "length": v1, "types": ["int"]},
                                 {"line": "3..", "kind": "queries", "repeat": v2, "vars": [], "types": []}])
        for v in hv:
            hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                         {"line": "2", "kind": "string", "vars": ["s"], "types": ["string"]},
                         {"line": "3..", "kind": "queries", "repeat": v, "vars": [], "types": []}])
            hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                         {"line": "2..", "kind": "queries", "repeat": v, "vars": [], "types": []}])
        hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * c0},
                     {"line": "2", "kind": "string", "vars": ["s"], "types": ["string"]}])
        if c0 == 1:
            # n, a[n], m, b[m]
            hyps.append([{"line": "1", "kind": "scalars", "vars": ["n"], "types": ["int"]},
                         {"line": "2", "kind": "array", "vars": ["a"], "length": "n", "types": ["int"]},
                         {"line": "3", "kind": "scalars", "vars": ["m"], "types": ["int"]},
                         {"line": "4", "kind": "array", "vars": ["b"], "length": "m", "types": ["int"]}])
            hyps.append([{"line": "1", "kind": "scalars", "vars": ["n"], "types": ["int"]},
                         {"line": "2", "kind": "array", "vars": ["a"], "length": "n", "types": ["int"]},
                         {"line": "3", "kind": "array", "vars": ["b"], "length": "n", "types": ["int"]}])
            hyps.append([{"line": "1", "kind": "scalars", "vars": ["n"], "types": ["int"]},
                         {"line": "2", "kind": "array", "vars": ["a"], "length": "n", "types": ["int"]},
                         {"line": "3", "kind": "scalars", "vars": ["q"], "types": ["int"]},
                         {"line": "4..", "kind": "queries", "repeat": "q", "vars": [], "types": []}])
            for w in (1, 2, 3):
                hyps.append([{"line": "1", "kind": "multitest", "vars": ["t"], "types": ["int"]},
                             {"line": "2", "kind": "scalars", "vars": ["n"], "types": ["int"], "per_test": True},
                             {"line": "3", "kind": "array", "vars": ["a"], "length": "n", "types": ["int"],
                              "per_test": True}] if w == 1 else
                            [{"line": "1", "kind": "multitest", "vars": ["t"], "types": ["int"]},
                             {"line": "2", "kind": "scalars", "vars": ["a", "b", "c"][:w], "types": ["int"] * w,
                              "per_test": True}])
            hyps.append([{"line": "1", "kind": "multitest", "vars": ["t"], "types": ["int"]},
                         {"line": "2", "kind": "string", "vars": ["s"], "types": ["string"], "per_test": True}])
        if c0 == 2:
            hyps.append([{"line": "1", "kind": "scalars", "vars": hv, "types": ["int"] * 2},
                         {"line": "2..", "kind": "rows", "repeat": hv[0], "vars": ["a"], "length": hv[1],
                          "types": ["int"]}])
    else:
        hyps.append([{"line": "1", "kind": "string", "vars": ["s"], "types": ["string"]}])
        hyps.append([{"line": "1", "kind": "string", "vars": ["s"], "types": ["string"]},
                     {"line": "2", "kind": "string", "vars": ["t"], "types": ["string"]}])
    for h in hyps:
        if all(check_format(h, s["input"])["ok"] for s in samples):
            for it in h:
                it["source"] = "samples"
                it["desc"] = _desc_for(it)
            # nazwy zmiennych z tekstu, gdy rodzaj i liczba się zgadzają
            rename: dict[str, str] = {}
            used = list(text_items)
            for it in h:
                for ti in used:
                    if ti["kind"] == it["kind"] and len(ti.get("vars") or []) == len(it.get("vars") or []) and ti["vars"]:
                        for a, b in zip(it["vars"], ti["vars"]):
                            if a != b and not re.fullmatch(r"x\d*", b):
                                rename[a] = base_var(b) if it["kind"] in ("array", "rows") else b
                        used.remove(ti)
                        break
            if rename:
                for it in h:
                    it["vars"] = [rename.get(v, v) for v in it["vars"]]
                    for key in ("length", "repeat"):
                        if it.get(key) in rename:
                            it[key] = rename[it[key]]
            # dociągnij opisy z tekstu
            for it in h:
                for ti in text_items:
                    if ti["kind"] == it["kind"] and ti.get("desc"):
                        it["desc"] = ti["desc"]
                        break
            return h
    return None


def _desc_for(it: dict) -> str:
    k = it["kind"]
    if k == "scalars":
        return "Liczby: " + ", ".join(it["vars"])
    if k == "array":
        return f"Ciąg {it['vars'][0]} długości {it.get('length') or '?'}"
    if k == "rows":
        return f"{it.get('repeat') or '?'} wierszy: " + ", ".join(it.get("vars") or [])
    if k == "grid":
        return f"Plansza: {it.get('repeat') or '?'} wierszy znaków"
    if k == "queries":
        return f"{it.get('repeat') or '?'} zapytań (po jednym w wierszu)"
    if k == "string":
        return f"Napis {it['vars'][0]}"
    return ""


# ============================================================================================ C++: wczytywanie
_CPP_KEYWORDS = {"int", "long", "char", "double", "float", "short", "auto", "new", "delete", "do", "if", "for",
                 "while", "case", "this", "true", "false", "and", "or", "not", "xor", "std", "main", "max", "min",
                 "abs", "map", "set", "pair", "string", "vector", "queue", "stack", "swap", "sort", "y0", "y1",
                 "j0", "j1", "jn", "yn", "signed", "unsigned", "const", "static", "union", "struct", "class"}


def cpp_name(v: str, used: set[str] | None = None) -> str:
    b = base_var(v) or "x"
    b = re.sub(r"[^A-Za-z0-9_]", "", b) or "x"
    if b in _CPP_KEYWORDS or b[0].isdigit():
        b = b + "_"
    return b


def _query_ops_from_samples(samples: list[dict] | None, skip: int = 0) -> dict[str, list[str]] | None:
    """Operacje w wierszach zapytań z przykładów: słowo-klucz → typy argumentów („PUSH 5”, „POP”). None = nie wiadomo.
    `skip` — tyle początkowych wierszy (niepustych) to dane przed zapytaniami (np. napis „xoxoo” to nie operacja)."""
    ops: dict[str, list[list[str]]] = {}
    for s in samples or []:
        for ln in [x for x in (s.get("input") or "").replace("\r\n", "\n").split("\n") if x.strip()][skip:]:
            toks = ln.split()
            if toks and re.fullmatch(r"[A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ_]\w*", toks[0]):
                ops.setdefault(toks[0], []).append(toks[1:])
    if not ops or len(ops) > 8:
        return None
    out = {}
    for key, argss in ops.items():
        if len({len(a) for a in argss}) != 1:
            return None
        w = len(argss[0])
        out[key] = ["long long" if all(_is_int(a[j]) for a in argss) else "string" for j in range(w)]
    return out


def _lines_before(items: list[dict], it: dict) -> int:
    """Ile wierszy wejścia jest przed elementem `it` — z numerów wierszy formatu („1”, „2”, „3..”); 0 = nie wiadomo."""
    m = re.match(r"\s*(\d+)", str(it.get("line") or ""))
    prev = items[:items.index(it)] if it in items else []
    if not m or not all(re.fullmatch(r"\s*\d+\s*", str(x.get("line") or "")) for x in prev):
        return 0
    return max(0, int(m.group(1)) - 1)


def _reader_from_samples(samples: list[dict] | None, indent: str) -> list[str]:
    """Wczytywanie, gdy formatu nie rozpoznano: jeśli wszystkie przykłady mają ten sam kształt (liczba wartości w każdym
    wierszu), czytamy wiersz po wierszu do nazwanych zmiennych; inaczej — wszystkie wartości po kolei do wektora."""
    shapes = []
    for s in samples or []:
        rows = [ln.split() for ln in (s.get("input") or "").replace("\r\n", "\n").split("\n") if ln.strip()]
        shapes.append(rows)
    out = []
    if shapes and all(len(r) == len(shapes[0]) for r in shapes) and len(shapes[0]) <= 6 and \
            all(len(a) == len(b) for r in shapes for a, b in zip(r, shapes[0])) and sum(map(len, shapes[0])) <= 12:
        out.append(f"{indent}// format nierozpoznany — kształt wejścia wzięty z przykładów")
        k = 0
        for li, row in enumerate(shapes[0]):
            names = []
            for j in range(len(row)):
                k += 1
                col = [r[li][j] for r in shapes]
                t = "long long" if all(_is_int(x) for x in col) else ("double" if all(_type_ok(x, "double") for x in col)
                                                                    else "string")
                names.append((f"x{k}", t))
            for t in dict.fromkeys(t for _, t in names):
                out.append(f"{indent}{t} {', '.join(n for n, tt in names if tt == t)};")
            out.append(f"{indent}cin >> {' >> '.join(n for n, _ in names)};   // wiersz {li + 1}")
        return out
    out.append(f"{indent}// format nierozpoznany — wczytuję wszystkie wartości po kolei (jako napisy; liczby: stoll(w))")
    out.append(f"{indent}vector<string> wartosci;")
    out.append(f"{indent}for (string w; cin >> w; ) wartosci.push_back(w);")
    return out


def cpp_reader(fmt: list[dict], indent: str = "    ", samples: list[dict] | None = None) -> dict:
    """Generuje kod C++ wczytujący dane wg formatu. Zwraca {code, vars: {nazwa: typ}, arrays, rows, notes}.
    `samples` (opcjonalnie) pomaga przy zapytaniach („PUSH x” / „POP”) i nierozpoznanym formacie."""
    lines: list[str] = []
    decl: dict[str, str] = {}
    arrays: list[str] = []
    rows: list[dict] = []
    notes: list[str] = []
    per_test = [x for x in fmt if x.get("per_test")]
    top = [x for x in fmt if not x.get("per_test")]

    def ctype(t):
        return {"int": "long long", "long long": "long long", "double": "double", "string": "string",
                "bignum": "string"}.get(t, "long long")

    def emit(items, ind):
        for it in items:
            k = it["kind"]
            vs = [cpp_name(v) for v in (it.get("vars") or [])]
            ts = it.get("types") or []
            if k == "scalars":
                if not vs:
                    continue
                groups: dict[str, list[str]] = {}
                for i, v in enumerate(vs):
                    t = ctype(ts[i] if i < len(ts) else "int")
                    if v in decl:
                        continue
                    decl[v] = t
                    groups.setdefault(t, []).append(v)
                for t, names in groups.items():
                    lines.append(f"{ind}{t} {', '.join(names)};")
                big = [v for i, v in enumerate(vs) if i < len(ts) and ts[i] == "bignum"]
                if big:
                    lines.append(f"{ind}// {', '.join(big)}: liczba za duża na long long — wczytujemy ją jako napis cyfr")
                lines.append(f"{ind}cin >> {' >> '.join(vs)};")
            elif k == "multitest":
                v = vs[0] if vs else "t"
                decl[v] = "int"
                lines.append(f"{ind}int {v};")
                lines.append(f"{ind}cin >> {v};")
                lines.append(f"{ind}while ({v}--) {{")
                emit(per_test, ind + "    ")
                lines.append(f"{ind}    // (tu są już wczytane dane jednego zestawu)")
                lines.append(f"{ind}}}")
            elif k == "array":
                v = vs[0] if vs else "a"
                L = cpp_name(it["length"]) if it.get("length") and re.fullmatch(r"[A-Za-z]\w*", str(it["length"])) \
                    else (str(it.get("length")).replace(" ", "") if it.get("length") else None)
                t = ctype(ts[0] if ts else "int")
                if v in decl:
                    v = v + "_arr"
                if L:
                    lines.append(f"{ind}vector<{t}> {v}({L});")
                    lines.append(f"{ind}for (auto &x : {v}) cin >> x;")
                else:
                    notes.append(f"Nie wiem, ile elementów ma {v} — czytam do końca wiersza.")
                    lines.append(f"{ind}vector<{t}> {v};")
                    lines.append(f"{ind}{{ string line; getline(cin >> ws, line); stringstream ss(line); {t} x; "
                                 f"while (ss >> x) {v}.push_back(x); }}")
                decl[v] = f"vector<{t}>"
                arrays.append(v)
            elif k == "string":
                v = vs[0] if vs else "s"
                if v in decl:
                    v = v + "_str"
                lines.append(f"{ind}string {v};")
                lines.append(f"{ind}cin >> {v};")
                decl[v] = "string"
            elif k in ("rows", "grid", "lines", "queries"):
                R = it.get("repeat")
                Rc = None
                if R and R != "?":
                    m = re.fullmatch(r"([A-Za-z]\w*)\s*([-+])\s*(\d+)", str(R).replace(" ", ""))
                    if m:
                        Rc = f"{cpp_name(m.group(1))} {m.group(2)} {m.group(3)}"
                    elif re.fullmatch(r"[A-Za-z]\w*", str(R)):
                        Rc = cpp_name(R)
                    elif re.fullmatch(r"\d+", str(R)):
                        Rc = str(R)
                if k == "grid":
                    g = vs[0] if vs else "plansza"
                    lines.append(f"{ind}vector<string> {g}({Rc or 0});")
                    lines.append(f"{ind}for (auto &row : {g}) cin >> row;")
                    decl[g] = "vector<string>"
                    arrays.append(g)
                    continue
                if k == "lines":
                    g = (vs[0] if vs else "s") + "s"
                    lines.append(f"{ind}vector<string> {g}({Rc or 0});")
                    lines.append(f"{ind}for (auto &w : {g}) cin >> w;")
                    decl[g] = "vector<string>"
                    arrays.append(g)
                    continue
                if k == "queries" or not vs:
                    ops = _query_ops_from_samples(samples, _lines_before(items, it))
                    lines.append(f"{ind}for (int qi = 0; qi < {Rc or 'q'}; qi++) {{")
                    if ops:
                        lines.append(f"{ind}    string typ;")
                        lines.append(f"{ind}    cin >> typ;")
                        for oi, (key, args) in enumerate(ops.items()):
                            kw = "if" if oi == 0 else "} else if"
                            lines.append(f"{ind}    {kw} (typ == \"{key}\") {{")
                            if args:
                                anames = [f"arg{j + 1}" for j in range(len(args))]
                                for t in dict.fromkeys(args):
                                    lines.append(f"{ind}        {t} " + ", ".join(n for n, tt in zip(anames, args) if tt == t) + ";")
                                lines.append(f"{ind}        cin >> {' >> '.join(anames)};   // operacja {key} "
                                             + " ".join(anames))
                            else:
                                lines.append(f"{ind}        // operacja {key} (bez argumentów)")
                        lines.append(f"{ind}    }}")
                    else:
                        lines.append(f"{ind}    string linia;")
                        lines.append(f"{ind}    getline(cin >> ws, linia);   // cały wiersz zapytania")
                        lines.append(f"{ind}    stringstream ss(linia);")
                        lines.append(f"{ind}    string typ;")
                        lines.append(f"{ind}    ss >> typ;   // rodzaj zapytania; jego argumenty czytaj dalej z ss")
                    lines.append(f"{ind}}}")
                    continue
                cnt = Rc or "m"
                fields = []
                for i, v in enumerate(vs):
                    t = ctype(ts[i] if i < len(ts) else "int")
                    nm = v
                    if nm in decl:
                        nm = nm + "_"
                    fields.append((nm, t))
                arr_names = [f"{nm}s" if len(fields) > 1 else (nm if nm not in decl else nm + "s") for nm, _ in fields]
                if len(fields) == 1:
                    nm, t = fields[0]
                    an = nm if nm not in decl else nm + "s"
                    lines.append(f"{ind}vector<{t}> {an}({cnt});")
                    lines.append(f"{ind}for (auto &x : {an}) cin >> x;")
                    decl[an] = f"vector<{t}>"
                    arrays.append(an)
                    rows.append({"name": an, "fields": [nm], "count": cnt})
                else:
                    st = "struct Wiersz { " + " ".join(f"{t} {nm};" for nm, t in fields) + " };"
                    rn = "wiersze" if not any(r["name"] == "wiersze" for r in rows) else f"wiersze{len(rows) + 1}"
                    lines.append(f"{ind}// {st}")
                    lines.append(f"{ind}vector<array<long long, {len(fields)}>> {rn}({cnt});")
                    lines.append(f"{ind}for (auto &w : {rn}) for (auto &x : w) cin >> x;   // "
                                 + ", ".join(f"w[{i}] = {nm}" for i, (nm, _) in enumerate(fields)))
                    decl[rn] = f"vector<array<long long, {len(fields)}>>"
                    arrays.append(rn)
                    rows.append({"name": rn, "fields": [nm for nm, _ in fields], "count": cnt})
                    del arr_names

    emit(top, indent)
    if not lines:
        lines += _reader_from_samples(samples, indent)
        notes.append("Nie rozpoznałem formatu wejścia z treści — wczytywanie zgadnięte z przykładów.")
    return {"code": "\n".join(lines), "vars": decl, "arrays": arrays, "rows": rows, "notes": notes}


# ============================================================================================ złożoność
COMPLEXITY_TABLE = [
    {"max": 10, "label": "n ≤ 10", "complexity": "O(n!)", "hint": "wszystkie permutacje, pełne przeszukiwanie",
     "ops": "≈ 3,6 mln"},
    {"max": 20, "label": "n ≤ 20", "complexity": "O(2^n · n)", "hint": "wszystkie podzbiory, DP na maskach bitowych",
     "ops": "≈ 2·10^7"},
    {"max": 500, "label": "n ≤ 500", "complexity": "O(n³)", "hint": "trzy pętle, Floyd–Warshall, DP przedziałowe",
     "ops": "≈ 1,25·10^8"},
    {"max": 5000, "label": "n ≤ 5000", "complexity": "O(n²)", "hint": "dwie pętle, DP kwadratowe",
     "ops": "≈ 2,5·10^7"},
    {"max": 10**6, "label": "n ≤ 10^6", "complexity": "O(n log n)",
     "hint": "sortowanie, wyszukiwanie binarne, drzewo przedziałowe, kopiec, Dijkstra", "ops": "≈ 2·10^7"},
    {"max": 10**8, "label": "n ≤ 10^8", "complexity": "O(n)", "hint": "jedno przejście, sito, sumy prefiksowe",
     "ops": "≈ 10^8"},
    {"max": 10**12, "label": "n ≤ 10^12", "complexity": "O(√n)", "hint": "dzielniki do √n, rozkład na czynniki",
     "ops": "≈ 10^6"},
    {"max": 10**18, "label": "n ≤ 10^18", "complexity": "O(log n) / O(1)",
     "hint": "matematyka, szybkie potęgowanie, wyszukiwanie binarne po wyniku", "ops": "≈ 60"},
]


def complexity_for(nmax) -> dict:
    """Dopuszczalna złożoność dla maksymalnego rozmiaru danych."""
    if nmax is None:
        return {"index": None, "complexity": None, "hint": "Nie znalazłem ograniczeń — zgaduję O(n log n)."}
    for i, row in enumerate(COMPLEXITY_TABLE):
        if nmax <= row["max"]:
            return dict(row, index=i)
    return dict(COMPLEXITY_TABLE[-1], index=len(COMPLEXITY_TABLE) - 1)


_SIZE_NAMES = ("n", "m", "q", "k", "N", "M", "Q", "K", "z", "t", "l", "L", "h", "w", "W", "H", "d", "r", "c", "s")


def _structural_sizes(fmt: list[dict], cons: dict) -> list[str]:
    """Zmienne, które wejście wprost używa jako LICZNOŚCI: długość ciągu/napisu/wiersza planszy, liczba wierszy
    (krawędzi, zapytań, wierszy planszy), długość napisu |s|."""
    sb = size_bounds(cons)
    sizes: list[str] = []
    for it in fmt:
        for key in ("repeat", "length"):
            v = it.get(key)
            if v and re.fullmatch(r"[A-Za-z]", str(v)[:1]):
                b = re.sub(r"[-+*·].*$", "", str(v))
                if b in sb and sb[b].get("max") is not None and b not in sizes:
                    sizes.append(b)
        if it.get("kind") in ("string", "lines") and not it.get("length"):
            for v in it.get("vars") or []:
                c = cons.get(f"|{v}|")
                if c and c.get("max") is not None and base_var(v) in sb and base_var(v) not in sizes:
                    sizes.append(base_var(v))
    return sizes


def size_variables(fmt: list[dict], cons: dict) -> list[str]:
    """Zmienne opisujące ROZMIAR danych (liczność: długości tablic/napisów, liczby wierszy, krawędzi, zapytań),
    a nie wartości (a_i, k jako szukana suma, wagi w, koszty c). Najpierw rozmiary „strukturalne” z formatu wejścia;
    zmienne o nazwach rozmiarów (n, m, k…) z pierwszego wiersza dokładamy tylko wtedy, gdy formatu nie da się użyć."""
    sb = size_bounds(cons)
    sizes = _structural_sizes(fmt, cons)
    if sizes:
        # q / Q to prawie zawsze liczba zapytań (nawet gdy zapytań nie umiemy rozpisać na wiersze)
        for it in fmt[:3]:
            if it.get("kind") in ("scalars", "multitest"):
                for v in it.get("vars") or []:
                    b = base_var(v)
                    if b in ("q", "Q") and b in sb and sb[b].get("max") is not None and b not in sizes:
                        sizes.append(b)
        return sizes
    if fmt and all(it.get("kind") == "scalars" for it in fmt):
        # same liczby na wejściu (a b m, n) — o złożoności decyduje największa z nich (pętla/logarytm po wartości);
        # zmienne z indeksem (a_i) to elementy ciągu, nie rozmiar
        for it in fmt:
            for v in it.get("vars") or []:
                if "_" in norm_var(v):
                    continue
                b = base_var(v)
                if b in sb and isinstance(sb[b].get("max"), (int, float)) and b not in sizes:
                    sizes.append(b)
        if sizes:
            return sizes
    # zmienne z pierwszego wiersza o typowych nazwach rozmiarów (n, m, q, k…)
    for it in fmt[:2]:
        if it.get("kind") in ("scalars", "multitest"):
            for v in it.get("vars") or []:
                b = base_var(v)
                if b in _SIZE_NAMES[:12] and b in sb and sb[b].get("max") is not None and b not in sizes:
                    sizes.append(b)
    if not sizes:
        for nm in _SIZE_NAMES:
            if nm in sb and sb[nm].get("max") is not None and nm not in sizes:
                sizes.append(nm)
                if len(sizes) >= 2:
                    break
    if not sizes:
        cand = [(k, v.get("max")) for k, v in sb.items() if isinstance(v.get("max"), (int, float)) and not k.startswith("_")]
        if cand:
            sizes.append(max(cand, key=lambda kv: kv[1])[0])
    return sizes


def grid_dims(fmt: list[dict]) -> tuple[str, str] | None:
    """(wiersze, kolumny) planszy, gdy wejście ma planszę n × m opisaną zmiennymi."""
    for it in fmt:
        if it.get("kind") in ("grid", "lines") or (it.get("kind") == "rows" and it.get("length")):
            r, c = str(it.get("repeat") or ""), str(it.get("length") or "")
            if re.fullmatch(r"[A-Za-z]\w*", r) and re.fullmatch(r"[A-Za-z]\w*", c) and r != c:
                return r, c
            if re.fullmatch(r"[A-Za-z]\w*", r) and r == c:
                return r, c
    return None


def main_size(fmt: list[dict], cons: dict, product: bool = False) -> tuple[str | None, float | None, list]:
    """(zmienna, maksimum, [(zmienna, max)…]) — główny rozmiar danych. Z `product=True` plansza n × m daje
    pseudo-zmienną „n·m” (liczba pól), bo to od niej zależy BFS/DFS po planszy."""
    sb = size_bounds(cons)
    sv = size_variables(fmt, cons)
    vals = [(v, sb[v]["max"]) for v in sv if v in sb and isinstance(sb[v].get("max"), (int, float))]
    if not vals:
        return None, None, []
    if product:
        gd = grid_dims(fmt)
        if gd:
            r, c = gd
            mr, mc = (sb.get(r) or {}).get("max"), (sb.get(c) or {}).get("max")
            if isinstance(mr, (int, float)) and isinstance(mc, (int, float)):
                return (f"{r}·{c}" if r != c else f"{r}²"), mr * mc, vals
    v, mx = max(vals, key=lambda kv: kv[1])
    # „n ≤ 10^18” dla zadania z jedną liczbą to też „rozmiar”
    return v, mx, vals


# ============================================================================================ słowa kluczowe
_KEY_BOOST = {"graf", "drzew", "wierzchol", "krawedz", "sciezk", "najkrotsz", "przedzial", "zapytan", "modulo",
              "permutacj", "podciag", "podslow", "palindrom", "sum", "minimaln", "maksymaln", "spojn", "cykl",
              "plansz", "siatk", "napis", "slow", "liczb", "pierwsz", "dzielnik", "nwd", "sortowan", "sposob",
              "koszt", "wag", "odleglosc", "kolejk", "stos", "zbior", "punkt", "prostokat", "przeciec"}


def keywords(text: str, k: int = 14) -> list[str]:
    cnt = Counter(t for t in T.terms(text) if len(t) >= 3 and not t.isdigit())
    scored = sorted(cnt.items(), key=lambda kv: -(kv[1] * (2.0 if any(kv[0].startswith(b) for b in _KEY_BOOST)
                                                            else 1.0)))
    return [w for w, _ in scored[:k]]


# ============================================================================================ główna funkcja
def clean(text: str) -> str:
    """Oczyszczony tekst (bez nagłówków szablonu, z naprawionymi potęgami) — do wyświetlenia/wyszukiwania."""
    p = parse(text)
    return "\n\n".join(v for v in p["sections"].values() if v)


def _join_prose(lines: list[str]) -> str:
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if out and out[-1] != "\n":
                out.append("\n")
            continue
        if s.startswith(("•", "-", "*")) or re.match(r"^\d+[.)]\s", s):
            out.append("\n" + s)
        else:
            out.append(s)
    txt = ""
    for piece in out:
        if piece == "\n":
            txt += "\n"
        elif piece.startswith("\n"):
            txt += piece
        else:
            txt += ("" if (not txt or txt.endswith("\n")) else " ") + piece
    return re.sub(r"[ \t]+", " ", txt).strip()


def parse(text: str) -> dict:
    """Pełne parsowanie treści zadania (patrz docstring modułu)."""
    src = text or ""
    warnings: list[str] = []
    source = "text"
    pres: list[str] = []
    html_title = None
    if looks_like_html(src):
        source = "html"
        src, pres, html_title = html_to_text(src)
        # <pre> z własnymi znacznikami „Wejście/Wyjście” w środku — rozwiń do zwykłych linii
        for k, pre in enumerate(pres):
            plines = [x.strip() for x in pre.split("\n")]
            if any(_IN_RE.match(x) or _OUT_RE.match(x) or _SIDE_RE.match(x) for x in plines if x):
                src = src.replace(f"⟦PRE:{k}⟧", "\n" + pre.strip("\n") + "\n")
        if "⟦wzór⟧" in src:
            warnings.append("Część wzorów w treści HTML to obrazki — ograniczenia mogą być niepełne.")
    src, moj = _normalize_chars(src)
    if moj:
        warnings.append("Naprawiono zepsute kodowanie polskich znaków z PDF.")
    lines, mode = _logical_lines(src)
    if mode == "pdf" and source == "text":
        source = "pdf"
    nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
    if nonempty and re.fullmatch(r"\d", lines[nonempty[-1]].strip()) and \
            re.match(r"(koło informatyczne|dost[eę]pna pami)", lines[nonempty[0]].strip(), re.I):
        lines = lines[:nonempty[-1]]          # numer strony na końcu szablonu „koło informatyczne”
    lines = [ln for ln in lines if not _NOISE_RE.match(ln.strip())]
    lines, meta = _strip_header(lines, html_title)
    meta["source"] = source
    if re.search(r"\n\s*Interakcja\s*\n|interaktywn|komunikacja (twojego )?programu", src, re.I):
        meta["interactive"] = True
    title = meta.pop("title", "")
    # --- podział na sekcje
    sections: dict[str, list[str]] = {"tresc": []}
    order: list[str] = ["tresc"]
    cur = "tresc"
    sample_lines: list[str] = []
    for ln in lines:
        s = ln.strip()
        mo = _OCEN_LABEL.match(s) if cur != "testy_ocen" else None
        if _OCEN_HEAD.match(s) or (mo and "ocen" in mo.group(1).lower()):
            # „Testy przykładowe: … Poza tym:” / „1ocen: …” — osobna sekcja (nie wyjaśnienie przykładu)
            cur = "testy_ocen"
            sections.setdefault(cur, [])
            if cur not in order:
                order.append(cur)
            sections[cur].append(ln)
            continue
        sec = _section_of(s)
        if sec is None:
            mi = _INLINE_SEC.match(s)
            if mi and not re.match(r"^\s*(?:dla testu|testu)", mi.group(2), re.I) and cur in ("tresc", "wejscie", "zadanie") \
                    and len(mi.group(2)) > 25:
                sec = "wejscie" if mi.group(1).lower().startswith("wej") else "wyjscie"
                sections.setdefault(sec, [])
                if sec not in order:
                    order.append(sec)
                cur = sec
                sections[sec].append(mi.group(2))
                continue
        if sec == "zadanie":
            sec = "tresc" if cur == "tresc" else "uwagi"
        if sec == "wyjasnienie":
            if cur == "przyklad":
                sample_lines.append(ln)
                continue
            sec = "przyklad"
            sample_lines.append(ln)
        # nagłówek „Wejście” wewnątrz przykładów to znacznik przykładu, nie sekcja
        if sec in ("wejscie", "wyjscie") and cur == "przyklad":
            sample_lines.append(ln)
            continue
        if sec == "wejscie" and "wejscie" in sections and "wyjscie" in sections and cur in ("wyjscie",):
            # druga „Wejście” po sekcji Wyjście — to przykład bez nagłówka
            cur = "przyklad"
            sections.setdefault("przyklad", [])
            sample_lines.append(ln)
            continue
        if sec:
            cur = sec
            sections.setdefault(sec, [])
            if sec not in order:
                order.append(sec)
            continue
        if _SIDE_RE.match(s) or _IN_RE2.match(s) or (cur != "przyklad" and _IN_RE.match(s) and
                                                     re.match(r"^\s*(dla danych|przyk[lł]adowe)", s, re.I)):
            cur = "przyklad"
            sections.setdefault("przyklad", [])
            if "przyklad" not in order:
                order.append("przyklad")
            sample_lines.append(ln)
            continue
        if cur == "przyklad":
            sample_lines.append(ln)
        else:
            sections[cur].append(ln)
    # brak nagłówków „Wejście” — spróbuj wyciąć opis wejścia z treści po zdaniu „W pierwszym wierszu...”
    if "wejscie" not in sections:
        body = sections["tresc"]
        for i, ln in enumerate(body):
            if re.match(r"^\s*(W pierwsz\w+ (wierszu|linii)|Pierwsz\w+ (wiersz|linia)|Na (standardowym )?wej[sś]ciu)", ln):
                sections["wejscie"] = body[i:]
                sections["tresc"] = body[:i]
                order.insert(1, "wejscie")
                break
    samples, expl, rest = _split_samples(sample_lines, pres)
    if rest:
        sections.setdefault("uwagi", [])
        if "uwagi" not in order:
            order.append("uwagi")
        sections["uwagi"] = rest + sections["uwagi"]
    # --- tekst sekcji
    prose_lines = {k: (_fix_exponents_text(v) if mode == "text" else v) for k, v in sections.items() if k != "przyklad"}
    prose_lines = {k: _dehyphenate(v) for k, v in prose_lines.items()}
    prose_lines, six_fixed = _fix_six_leq(prose_lines)
    if six_fixed:
        warnings.append("W tekście z PDF znak „≤” był zapisany jako „6” (np. „1 6 n”) — poprawiłem; sprawdź ograniczenia.")
    sec_text = {k: _join_prose(v) for k, v in prose_lines.items()}
    sec_text["przyklad"] = "\n\n".join(
        f"Wejście:\n{s['input']}Wyjście:\n{s['output']}" for s in samples if s.get("input") or s.get("output"))
    if expl:
        sec_text["wyjasnienie"] = _join_prose(expl)
    ordered = {k: sec_text.get(k, "") for k in order if k in sec_text}
    for k in sec_text:
        ordered.setdefault(k, sec_text[k])
    sections_out = {k: v for k, v in ordered.items() if v}
    # --- ograniczenia (najpierw z wejścia, potem z reszty)
    cons: dict = {}
    rel: list = []
    for key in ("wejscie", "ograniczenia", "tresc", "uwagi", "wyjscie"):
        c, r = extract_constraints(sec_text.get(key, ""))
        for k, v in c.items():
            if k not in cons:
                cons[k] = v
            else:
                for f in ("min", "max"):
                    if cons[k].get(f) is None and v.get(f) is not None:
                        cons[k][f] = v[f]
        rel += r
    # --- zgubione indeksy górne z PDF („≤ 109” zamiast „≤ 10^9”), gdy w całym tekście nie ma żadnej potęgi
    if source in ("pdf", "text") and "^" not in src:
        for k, v in cons.items():
            for f in ("max", "min"):
                val = v.get(f)
                if isinstance(val, int) and abs(val) in _LOST_POW:
                    e = int(str(abs(val))[2:])
                    v[f] = (10 ** e) * (1 if val > 0 else -1)
                    v["guessed_power"] = True
        if any(v.get("guessed_power") for v in cons.values()):
            warnings.append("Potęgi w tekście z PDF mogły się zgubić (np. „109” → 10^9) — sprawdź ograniczenia.")
    # --- podzadania i limity
    subtasks, times = _parse_subtasks(prose_lines.get("ocenianie", []), sec_text.get("ocenianie", ""))
    time_limit = meta.pop("time_limit", None)
    m = None if time_limit else re.search(r"limit czasu\s*:?\s*(\d+(?:[.,]\d+)?)\s*(?:s|sek)", src, re.I)
    if m:
        time_limit = _parse_time(m.group(1))
    elif times and not time_limit:
        time_limit = max(times)
    memory_limit = meta.pop("memory_limit", None)
    if not memory_limit:
        m = re.search(r"(?:limit pami[eę]ci|pami[eę][cć])\s*:?\s*(\d+)\s*([MG]i?B)", src, re.I)
        if m:
            memory_limit = f"{m.group(1)} {m.group(2)}"
    # --- przykłady obok siebie: podziel wg formatu
    fmt = infer_input_format(sec_text.get("wejscie", ""), None, cons)
    for s in samples:
        if "side_by_side" in s:
            inp, out, unc = _split_side(s.pop("side_by_side"), fmt, s.pop("side_col", None))
            s["input"], s["output"] = inp, out
            if unc:
                s["uncertain"] = True
    samples = [s for s in samples if s.get("input") or s.get("output")]
    for i, s in enumerate(samples):
        if not s.get("name"):
            s["name"] = f"przykład {i + 1}"
    if samples and not sections_out.get("przyklad"):
        # przykład w dwóch kolumnach dostał wejście/wyjście dopiero teraz — uzupełnij tekst sekcji (w dobrym miejscu)
        sec_text["przyklad"] = "\n\n".join(f"Wejście:\n{s['input']}Wyjście:\n{s['output']}" for s in samples)
        sections_out = {k: sec_text[k] for k in list(dict.fromkeys(order + list(sections_out))) if sec_text.get(k)}
    fmt = infer_input_format(sec_text.get("wejscie", ""), samples, cons) if samples else fmt
    if fmt and samples:
        samples = _split_merged(samples, fmt)
        _infer_string_lengths(fmt, samples)
    for it in fmt:
        if it.get("kind") in ("string", "lines", "grid"):
            for v in it.get("vars") or []:
                if cons.get(v, {}).get("abs") and f"|{v}|" in cons:
                    cons.pop(v)           # „1 ≤ |s| ≤ 10^5” dla napisu to długość, nie wartość bezwzględna
            if it.get("length") and it["length"] in (it.get("vars") or []):
                it.pop("length")
    _string_length_bounds(fmt, cons, sec_text.get("wejscie", ""))
    checks = [check_format(fmt, s["input"]) for s in samples if s.get("input")] if fmt else []
    fmt_ok = bool(checks) and all(c["ok"] for c in checks)
    if fmt_ok:
        for it in fmt:
            it.pop("unverified", None)
        # testy „ocen” opisane słownie → dodatkowe przykłady (tylko gdy format jest potwierdzony przykładami)
        if sections.get("testy_ocen"):
            samples = samples + ocen_samples(sections["testy_ocen"], fmt, cons, samples)
    if not sections_out.get("wejscie"):
        warnings.append("Nie znalazłem sekcji „Wejście” — format wejścia zgaduję z przykładów.")
    if not samples:
        warnings.append("Nie znalazłem przykładów w treści.")
    _resolve_symbolic_bounds(cons, rel)
    full_text = " ".join(sections_out.get(k, "") for k in ("tresc", "wejscie", "wyjscie", "uwagi"))
    return {
        "title": title,
        "sections": sections_out,
        "input_format": fmt,
        "constraints": cons,
        "relations": rel,
        "samples": samples,
        "subtasks": subtasks,
        "time_limit": time_limit,
        "memory_limit": memory_limit,
        "keywords": keywords(full_text),
        "meta": meta,
        "warnings": warnings,
        "format_check": {"ok": fmt_ok, "details": [c["reason"] for c in checks]},
    }


_LEN_RE = re.compile(
    r"d[lł]ugo[sś]\w*\s+(?:[\wąćęłńóśźż]+\s+){0,3}?(?:od\s+(?P<lo>" + VALUE_RE.pattern + r")\s+do\s+(?P<hi>"
    + VALUE_RE.pattern + r")|nie\s+(?:przekracza\w*|(?:jest\s+)?wi[eę]ksz\w*\s+(?:ni[zż]|od))\s+(?P<hi2>"
    + VALUE_RE.pattern + r")|co\s+najwy[zż]ej\s+(?P<hi3>" + VALUE_RE.pattern + r")|(?:≤|<=)\s*(?P<hi4>"
    + VALUE_RE.pattern + r"))", re.I)


def _string_length_bounds(fmt: list[dict], cons: dict, wejscie: str) -> None:
    """„Oba słowa … ich długości nie przekraczają 100 000”, „napis o długości od 1 do 10^6” → |s| ≤ … dla napisów,
    które nie mają jeszcze ograniczenia długości."""
    strs = [v for it in fmt if it.get("kind") in ("string", "lines") and not it.get("length")
            for v in (it.get("vars") or [])]
    strs = [v for v in strs if f"|{v}|" not in cons]
    if not strs:
        return
    for it in [x for x in fmt if x.get("kind") in ("string", "lines")] + [{"desc": wejscie, "vars": strs}]:
        m = _LEN_RE.search(it.get("desc") or "")
        if not m:
            continue
        hi = parse_value(next(g for g in (m.group("hi"), m.group("hi2"), m.group("hi3"), m.group("hi4")) if g))
        lo = parse_value(m.group("lo")) if m.group("lo") else 1
        if not isinstance(hi, (int, float)):
            continue
        for v in it.get("vars") or []:
            if v in strs and f"|{v}|" not in cons:
                cons[f"|{v}|"] = {"min": lo, "max": hi, "raw": m.group(0), "length": True}


def _resolve_symbolic_bounds(cons: dict, rel: list) -> None:
    """„1 ≤ a, b ≤ n” → max(a) = max(n) (z dopiskiem max_ref), także łańcuchowo (a ≤ b ≤ n)."""
    for _ in range(3):
        changed = False
        for r in rel or []:
            if not isinstance(r, dict) or r.get("type") not in ("le", "lt"):
                continue
            a, b = r.get("a"), r.get("b")
            ca, cb = cons.get(a), cons.get(b)
            if not isinstance(ca, dict) or not isinstance(cb, dict):
                continue
            if ca.get("max") is None and isinstance(cb.get("max"), (int, float)):
                ca["max"] = cb["max"] - (1 if r.get("strict") and isinstance(cb["max"], int) else 0)
                ca["max_ref"] = b
                changed = True
        if not changed:
            break


def _infer_string_lengths(fmt: list[dict], samples: list[dict]) -> None:
    """Napis bez podanej długości: jeśli we wszystkich przykładach jego długość = wartość którejś zmiennej (np. n),
    zapisz to (przydatne dla generatorów)."""
    for i, it in enumerate(fmt):
        if it.get("kind") not in ("string", "grid", "lines") or it.get("length"):
            continue
        cands = None
        for smp in samples:
            c = check_format(fmt, smp.get("input") or "")
            if not c["ok"] or i not in c["spans"]:
                return
            a, b = c["spans"][i]
            lines = _tok_lines(smp["input"])[a:b]
            if not lines:
                return
            L = len(lines[0][0]) if lines[0] else 0
            if any(len(ln[0]) != L for ln in lines if ln):
                cands = set()
                break
            ok = {k for k, v in c["env"].items() if isinstance(v, int) and v == L and "#" not in k}
            cands = ok if cands is None else cands & ok
        if cands:
            pref = [x for x in ("n", "m", "k", "l", "w", "L") if x in cands]
            it["length"] = pref[0] if pref else sorted(cands)[0]


def _split_merged(samples: list[dict], fmt: list[dict]) -> list[dict]:
    """Dwa przykłady sklejone w jeden blok (PDF w dwóch kolumnach): wejście = k pełnych zestawów, wyjście = k linii."""
    out: list[dict] = []
    for s in samples:
        inp = s.get("input") or ""
        c = check_format(fmt, inp)
        if c["ok"] or not c["reason"].startswith("zostało") or any(x.get("kind") == "multitest" for x in fmt):
            out.append(s)
            continue
        lines = [ln for ln in inp.split("\n") if ln.strip()]
        chunks = []
        pos = 0
        while pos < len(lines) and len(chunks) < 8:
            c2 = check_format(fmt, "\n".join(lines[pos:]) + "\n")
            used = c2["consumed_lines"]
            if used <= 0 or (not c2["ok"] and not c2["reason"].startswith("zostało")):
                break
            chunks.append(lines[pos:pos + used])
            pos += used
        olines = [ln for ln in (s.get("output") or "").split("\n") if ln.strip()]
        if pos == len(lines) and len(chunks) >= 2 and olines and len(olines) % len(chunks) == 0:
            per = len(olines) // len(chunks)
            for i, ch in enumerate(chunks):
                out.append({"name": f"{s.get('name') or 'przykład'} ({i + 1})", "input": "\n".join(ch) + "\n",
                            "output": "\n".join(olines[i * per:(i + 1) * per]) + "\n", "split_from_merged": True})
        else:
            out.append(s)
    return out


def _split_columns(ls: list[str], col: int) -> tuple[str, str] | None:
    """Podział linii z układem kolumnowym (wejście | wyjście) wg kolumny, w której zaczyna się wyjście."""
    left: list[str] = []
    right: list[str] = []
    for ln in ls:
        if not ln[: max(0, col - 2)].strip():                # tylko prawa kolumna
            right.append(ln.strip())
            continue
        if len(ln.rstrip()) <= col - 2:                      # tylko lewa kolumna
            left.append(ln.strip())
            continue
        best = None
        for m in re.finditer(r"\s{2,}", ln):
            if m.end() < len(ln) and abs(m.end() - col) <= 8 and (best is None or abs(m.end() - col) < abs(best.end() - col)):
                best = m
        if best is None:
            return None
        left.append(ln[:best.start()].strip())
        right.append(ln[best.end():].strip())
    left = [x for x in left if x]
    right = [x for x in right if x]
    if not left or not right:
        return None
    return "\n".join(left) + "\n", "\n".join(right) + "\n"


def _split_side(lines: list[str], fmt: list[dict], col: int | None = None) -> tuple[str, str, bool]:
    """Przykład w dwóch kolumnach („Wejście Wyjście”, „Dla danych wejściowych: … poprawnym wynikiem jest:”):
    przy zachowanym układzie kolumn dzielimy wg kolumny, inaczej wybieramy miejsce podziału zgodne z formatem."""
    ls = [ln for ln in lines if ln.strip()]
    if not ls:
        return "", "", True
    if col:
        res = _split_columns(ls, col)
        if res and (not fmt or check_format(fmt, res[0])["ok"]):
            return res[0], res[1], False
    if len(ls) == 1:
        parts = ls[0].split()
        return " ".join(parts[:-1]) + "\n", parts[-1] + "\n", True
    if fmt:
        for cut in range(1, len(ls)):
            inp = "\n".join(ls[:cut]) + "\n"
            c = check_format(fmt, inp)
            if c["ok"] and c["reason"] == "":
                return inp, "\n".join(ls[cut:]) + "\n", False
        # kolumny sklejone spacją („3 6” / „1 2 3”): wyjście to ostatnie słowa pierwszych j linii
        for j in range(1, min(len(ls), 8) + 1):
            head = [ln.split() for ln in ls[:j]]
            if any(len(t) < 2 for t in head):
                break
            inp = "\n".join([" ".join(t[:-1]) for t in head] + ls[j:]) + "\n"
            c = check_format(fmt, inp)
            if c["ok"] and c["reason"] == "":
                return inp, "\n".join(t[-1] for t in head) + "\n", True
    return "\n".join(ls[:-1]) + "\n", ls[-1] + "\n", True


# ============================================================================================ testy „ocen”
# Współczesne treści OI wypisują dodatkowe testy przykładowe słownie: „1ocen: n = 1 000 000; odpowiedź to 1;”,
# „3ocen: n = 9 · 10^999999; odpowiedź to 3.”, „0b: n = 10, odpowiedź: NIE.”. Gdy wejście składa się z samych
# liczb (wiersze zmiennych skalarnych) i wszystkie zmienne są podane, budujemy z nich dodatkowe przykłady.
BIG_SAMPLE = 100_000            # przykład dłuższy niż tyle znaków jest „duży” (np. liczba z milionem cyfr)
_ANSWER_RE = re.compile(r"(?<![\wąćęłńóśźż])(?:(?:to\s+)?(?:poprawn\w+\s+)?(?:odpowied[zź]\w*|wynik\w*))"
                        r"\s*(?:to|jest|wynosi|brzmi|[:=–-])?\s*:?\s*", re.I)
_WORD_ANSWER_RE = re.compile(r"[„\"']?([A-ZĄĆĘŁŃÓŚŹŻ][A-ZĄĆĘŁŃÓŚŹŻ_]+|-1)[”\"']?(?=[\s.,;:)]|$)")
_ASSIGN_RE = re.compile(r"(?<![\wąćęłńóśźż=≤≥<>])((?:" + _VAR + r"\s*=\s*)+)(-?\s*" + VALUE_RE.pattern + r")")


def _ocen_entries(lines: list[str]) -> list[tuple[str, str]]:
    """[(etykieta, opis)] z sekcji testów „ocen” (opis może być zawinięty na kilka linii)."""
    out: list[list[str]] = []
    cur = None
    armed = False
    for ln in lines:
        s = ln.strip()
        if _OCEN_HEAD.match(s):
            armed = True
            continue
        m = _OCEN_LABEL.match(s)
        if m and ("ocen" in m.group(1).lower() or armed):
            cur = [m.group(1), m.group(2).strip()]
            out.append(cur)
            continue
        if cur is None:
            continue
        if not s or _section_of(s) or re.match(r"^(podzadani|ocenianie|grupa)\b", s, re.I):
            cur = None
            continue
        ma = _ANSWER_RE.search(cur[1])
        if (not ma or not cur[1][ma.end():].strip()) and len(cur[1]) < 600:
            cur[1] += " " + s                     # zawinięty opis — doklejamy, dopóki nie ma odpowiedzi
    return [(a, b) for a, b in out]


def _ocen_answer(t: str) -> str | None:
    t = t.strip()
    mw = _WORD_ANSWER_RE.match(t)
    if mw and not re.match(r"-?\d", t[mw.end():].lstrip(" ,")):
        return mw.group(1)
    m = re.match(r"-?\s*(?:" + VALUE_RE.pattern + r")", t)
    if not m:
        return None
    rest = t[m.end():]
    if re.match(r"\s*[,;]?\s*-?\d", rest) or re.match(r"\s*(?:,\s*)?(?:\.\s?\.\s?\.|…)", rest):
        return None                               # lista liczb („0, 0, 0, 1”) albo „…” — nie umiemy porównać
    if re.match(r"\s*[a-ząćęłńóśźż]", rest) and not re.match(r"\s*(?:i|oraz|a|bo|gdy|poniewa)\b", rest):
        return None                               # „odpowiedź to 5 sposobów …” — niejednoznaczne
    return value_text(m.group(0).replace(" ", "") if "^" in m.group(0) else m.group(0))


def _ocen_sample(label: str, text: str, fmt: list[dict], cons: dict) -> dict | None:
    low = text.lower()
    if re.search(r"losow|przyk[lł]adow\w*\s+(?:odpowied|wynik)|jedn\w*\s+z\s+(?:mo[zż]liwych|poprawnych)", low):
        return None
    ma = _ANSWER_RE.search(text)
    if not ma:
        return None
    ans = _ocen_answer(text[ma.end():])
    if ans is None:
        return None
    pre = text[:ma.start()]
    if re.search(r"\.\s?\.\s?\.|…", pre):
        return None
    assign: dict[str, str] = {}
    for m in _ASSIGN_RE.finditer(pre):
        raw = m.group(2)
        if re.search(r"[·*×x]", raw) and "^" not in raw:
            return None                           # „9 · 10999 999” — wykładnik zgubiony przy kopiowaniu z PDF
        val = value_text(raw)
        if val is None:
            return None
        for nm in re.findall(_VAR, m.group(1)):
            key = norm_var(nm)
            if key in assign and assign[key] != val:
                return None
            assign[key] = val
    if not assign:
        return None
    known = {norm_var(v) for it in fmt for v in it.get("vars") or []}
    if any(k not in known and base_var(k) not in known for k in assign):
        return None                               # treść mówi o zmiennej spoza wejścia — format jest niepełny
    rows = []
    for it in fmt:
        vals = []
        types = it.get("types") or []
        for i, v in enumerate(it.get("vars") or []):
            key = norm_var(v)
            val = assign.get(key, assign.get(base_var(v)))
            t = types[i] if i < len(types) else "int"
            if val is None or t not in ("int", "long long", "bignum") or not re.fullmatch(r"-?\d+", val):
                return None
            c = cons.get(key) or cons.get(base_var(v)) or {}
            if len(val) <= 19:
                x = int(val)
                if isinstance(c.get("min"), (int, float)) and x < c["min"]:
                    return None
                if isinstance(c.get("max"), (int, float)) and x > c["max"] and not c.get("bignum"):
                    return None
            elif not c.get("bignum") or len(val.lstrip("-")) > (c.get("max_digits") or 0):
                return None
            vals.append(val)
        rows.append(" ".join(vals))
    inp = "\n".join(rows) + "\n"
    if len(inp) <= BIG_SAMPLE and not check_format(fmt, inp)["ok"]:
        return None
    out = {"name": label, "input": inp, "output": ans + "\n", "source": "ocen",
           "explanation": re.sub(r"\s+", " ", text).strip()[:240]}
    if len(inp) > BIG_SAMPLE:
        out["big"] = True
        out["input_size"] = len(inp)
        out["input_preview"] = inp[:48] + f"… ({len(inp) - 1} znaków)"
    return out


def ocen_samples(lines: list[str], fmt: list[dict], cons: dict, samples: list[dict]) -> list[dict]:
    """Dodatkowe przykłady z opisów testów „ocen” — tylko dla wejścia z samych liczb (wiersze zmiennych)."""
    if not fmt or any(it.get("kind") != "scalars" or it.get("per_test") for it in fmt):
        return []
    seen = {(s.get("input") or "").strip() for s in samples}
    out = []
    for label, text in _ocen_entries(lines):
        smp = _ocen_sample(label, text, fmt, cons)
        if smp and smp["input"].strip() not in seen:
            seen.add(smp["input"].strip())
            out.append(smp)
    return out

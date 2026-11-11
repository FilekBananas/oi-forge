"""(W) Objaśniacz treści zadania — pod zawody: „W skrócie” (dane → cel → postać odpowiedzi), typ zadania
z praktyczną konsekwencją, rozpoznana struktura danych (drzewo / graf / plansza / napisy / zapytania / wiele testów),
tabela zmiennych (rozmiar czy wartość), przykład rozpisany na zmienne, „Na co uważać”, budżet operacji, podzadania
i słowniczek. Streszczenie wybiera zdania formalne (zmienne, liczby, polecenie), a nie fabułę.
Wyłącznie reguły, wyrażenia regularne i proste statystyki (bez LLM)."""
from __future__ import annotations

import math
import re
from collections import Counter

from ..core import statement as ST
from ..core import text as T
from ..server_api import ApiError

try:
    from . import suggest as SG
except ImportError:  # pragma: no cover
    SG = None

INT_MAX = 2**31 - 1
LL_MAX = 2**63 - 1
OPS_PER_SEC = 10**8            # prostych operacji na sekundę (reguła kciuka na sprawdzarkach OI)
_PL = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"

_GOAL_RE = re.compile(r"\b(oblicz(?:y[cć]|cie)?\b|wyznacz(?:y[cć]|cie)?\b|znajd[zź](?:cie)?\b|znale[zź][cć]|"
                      r"policz(?:y[cć]|cie)?\b|podaj\b|poda[cć]\b|wypisz\b|wypisa[cć]|sprawd[zź]\b|sprawdzi[cć]|"
                      r"ustal\b|ustali[cć]|zlicz\b|zliczy[cć]|okre[sś]l\b|okre[sś]li[cć]|odpowied[zź]\b|"
                      r"odpowiedzie[cć]|pom[oó][zż]\b|napisz program|twoim zadaniem|twoje zadanie|"
                      r"zadanie polega|nale[zż]y|chcia[lł]\w*by|zastanawia si[eę]|jak[aiey]? (?:jest )?(?:najmniejsz|"
                      r"najwi[eę]ksz|minimaln|maksymaln)|ile (?:co najmniej|co najwy[zż]ej|jest|razy|najmniej|"
                      r"najwi[eę]cej|minimalnie|maksymalnie|wynosi|b[eę]dzie))", re.I)
# „twarde” polecenie (do oceny formalności zdania): bez fabularnych „chciałby”, „zastanawia się”
_TASK_RE = re.compile(r"\b(oblicz\w*|wyznacz\w*|znajd[zź]\w*|znale[zź][cć]|policz\w*|podaj\b|wypisz\w*|wypisa[cć]|"
                      r"sprawd[zź]\w*|zlicz\w*|okre[sś]l\w*|napisz program|twoim zadaniem|zadanie polega|"
                      r"zaimplementuj|opracuj program|ile\b)", re.I)
_GIVEN_RE = re.compile(r"\b(dan[yaeo]\w*|mamy|ma\b|posiada\w*|sk[lł]ada si[eę]|znajduj[eą] si[eę]|jest\b|s[aą]\b|"
                       r"istniej\w*|zawiera\w*|otrzymuje\w*|dysponuje)", re.I)
_OPT_RE = re.compile(r"\b(minimaln\w*|maksymaln\w*|najmniej\w*|najwi[eę]c\w*|najkr[oó]tsz\w*|najd[lł]u[zż]sz\w*|"
                     r"najmniejsz\w*|najwi[eę]ksz\w*|najta[nń]sz\w*|najdro[zż]sz\w*|optymaln\w*|"
                     r"jak najmniej\w*|jak najwi[eę]\w*|mo[zż]liwie najmniej\w*|mo[zż]liwie najwi[eę]\w*)", re.I)
_DEF_RE = re.compile(r"\b(dan[yaeo]\b|dane s[aą]|mamy\b|sk[lł]ada si[eę]|ci[aą]g\w*|tablic\w*|graf\w*|drzew\w*|"
                     r"wierzcho[lł]k\w*|kraw[eę]d\w*|napis\w*|s[lł]ow[oa]\w*|plansz\w*|przedzia[lł]\w*|zapyta\w*|"
                     r"operacj\w*|permutacj\w*|punkt\w*|odcin\w*|macierz\w*|sum[aęy]\b|iloczyn\w*|podci[aą]g\w*|"
                     r"podzbi[oó]r\w*|zbi[oó]r\w*|modulo|reszt\w* z dzielenia|liczb\w* (?:ca[lł]kowit|naturaln|pierwsz)\w*|"
                     r"pol[ea]\b|kolumn\w*|wiersz\w*|prostok[aą]t\w*|parami r[oó][zż]n\w*|indeks\w*)", re.I)
_STORY_RE = re.compile(r"\b(pewnego (?:dnia|razu|wieczoru|ranka)|dawno|kiedy[sś]|postanowi[lł]\w*|lubi\b|lubi[aą]|"
                       r"uwielbia\w*|marz[yąe]\w*|niestety|na szcz[eę][sś]cie|okaza[lł]o si[eę]|w histori\w*|legend\w*|"
                       r"kr[oó]l\w*|ksi[eę][zż]\w*|smok\w*|wakacj\w*|[sś]wi[eę]t[aoy]\b|ferie|ch[lł]op\w*|dziewczyn\w*|"
                       r"mam[aąy]\b|tat[aę]\b|siostr\w*|brat\w*|koleg\w*|przyjaci\w*|jak wiadomo|ostatnio|od niedawna|"
                       r"s[lł]ynn\w*|dumn\w*|smutn\w*|zmartwi\w*|zdenerwowa\w*|niecierpliw\w*|ulubion\w*|"
                       r"najd[lł]u[zż]sz\w* rzek\w*|na [sś]wiecie|pi[eę]kn\w*|wspania[lł]\w*)\b", re.I)
_VAR_TOK = re.compile(r"(?<![\w" + _PL + r"'^])([A-Za-z](?:_\{[^}\s]{1,6}\}|_[A-Za-z0-9]{1,3}|[0-9]{1,2}|[ijk])?)"
                      r"(?![\w" + _PL + r"(])")
_SENT_SPLIT_TAIL = re.compile(r"\s*:\s*$")


def _low(s: str) -> str:
    return T.strip_accents(s or "").lower()


def _sentences(txt: str) -> list[str]:
    return [s for s in ST._sentences(txt) if len(s) > 3]


def _clip(s: str, n: int = 90) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:–- ") + "…"


def _fmt(v) -> str:
    return ST.fmt_value(v) if v is not None else "?"


# ============================================================================================ zmienne z treści
def _known_vars(p: dict) -> set[str]:
    out: set[str] = set()
    for k in p.get("constraints") or {}:
        if not k.startswith("_") and "|" not in k and "·" not in k and "+" not in k:
            out.add(k)
            out.add(ST.base_var(k))
    for it in p.get("input_format") or []:
        for v in it.get("vars") or []:
            if v != "plansza":
                out.add(v)
                out.add(ST.base_var(v))
    return out


def _var_tokens(sent: str, known: set[str]) -> list[str]:
    """Zmienne (znane z formatu/ograniczeń) występujące w zdaniu jako osobne symbole."""
    out = []
    for m in _VAR_TOK.finditer(sent):
        v = ST.norm_var(m.group(1))
        b = ST.base_var(v)
        if v not in known and b not in known:
            continue
        if b in ST._POLISH_SINGLE and not _math_ctx(sent, m.start(), m.end()):
            continue            # „a”, „i”, „w”, „z” to zwykle słowa — liczymy tylko w kontekście matematycznym
        out.append(b)
    return out


def _math_ctx(text: str, a: int, b: int) -> bool:
    """Czy symbol text[a:b] stoi w kontekście wzoru (sąsiednie niebiałe znaki to ≤, =, nawiasy, cyfry…)."""
    left = text[:a].rstrip()[-1:]
    right = text[b:].lstrip()[:1]
    if re.match(r"\s*(?:,|\bi\b|\boraz\b)\s*[A-Za-z](?:_?[a-z0-9])?(?![\w" + _PL + r"])", text[b:]) and \
            re.search(r"(?:liczb\w*|warto[sś]\w*|ca[lł]kowit\w*|zmienn\w*|parametr\w*)\s*:?\s*(?:[A-Za-z](?:_?[a-z0-9])?\s*,\s*)*$",
                      text[:a]):
        return True         # „liczby całkowite u i v”, „liczb a, b i c”
    if re.search(r"(?:liczb\w*|warto[sś]\w*|ca[lł]kowit\w*)\s*:?\s*(?:[A-Za-z](?:_?[a-z0-9])?\s*(?:,|\bi\b|\boraz\b)\s*)+$",
                 text[:a]):
        return True         # ostatni element listy: „u i v”
    return bool(re.match(r"[≤≥<>=≠(\[,+\-·*/|:]", left or "x") and re.match(r"[≤≥<>=≠)\],+\-–—·*/|_.;:]|$", right or "")) or \
        bool(re.match(r"[≤≥<>=≠]", right or "x")) or bool(re.match(r"[≤≥<>=≠]", left or "x"))


def _proper_names(text: str) -> set[str]:
    """Rdzenie imion / nazw własnych z fabuły: słowa z wielkiej litery w środku zdania („Bajtazar”, „Nahla”, „Nil”)
    oraz wszystko, co zaczyna się od „Bajt”/„Bit” (Bajtocja, Bitek)."""
    stems: set[str] = set()
    for sent in _sentences(text):
        words = re.findall(r"[A-Za-z" + _PL + r"][\w" + _PL + r"-]*", sent)
        for i, w in enumerate(words):
            lw = w.lower()
            if lw.startswith(("bajt", "bit")) and len(w) >= 4 and not re.fullmatch(r"bit(?:y|ów|owe|owy|em|ach)?", lw):
                stems.add(lw[:5])
            if i == 0 or len(w) < 3 or w.isupper() or not w[0].isupper():
                continue
            if re.fullmatch(r"[A-Z][a-z]?\d*", w):
                continue
            stems.add(lw[:5])
    return stems


def _story_hits(sent: str, names: set[str]) -> int:
    words = re.findall(r"[A-Za-z" + _PL + r"][\w" + _PL + r"-]*", sent)
    return sum(1 for w in words if len(w) >= 3 and w.lower()[:5] in names and (w[0].isupper() or
                                                                            w.lower().startswith(("bajt", "bit"))))


def _formality(sent: str, known: set[str], names: set[str]) -> float:
    """> 0: zdanie „formalne” (zmienne, liczby, polecenie, pojęcia); < 0: fabuła (imiona, „pewnego dnia”)."""
    low = sent.lower()
    f = 0.0
    nv = len(_var_tokens(sent, known))
    f += min(nv, 3) * 0.7
    if re.search(r"\d", sent):
        f += 0.4
    if re.search(r"[≤≥<>=≠]|\[\s*\w+\s*[,;]", sent):
        f += 0.5
    if _TASK_RE.search(sent):
        f += 1.3
    elif _GOAL_RE.search(sent):
        f += 0.5
    if _DEF_RE.search(low):
        f += 0.6
    if _OPT_RE.search(sent):
        f += 0.4
    if sent.rstrip().endswith("?"):
        f += 0.3
    story = _story_hits(sent, names)
    f -= min(story, 2) * 0.6
    if _STORY_RE.search(low):
        f -= 0.7
    if "!" in sent:
        f -= 0.3
    words = len(sent.split())
    if words > 50:
        f -= 0.6
    if words < 4:
        f -= 0.8
    return f


def is_formal(sent: str, known: set[str]) -> bool:
    """Zdanie zawiera zmienną, liczbę/wzór albo „twarde” polecenie (miara jakości streszczenia)."""
    return bool(_var_tokens(sent, known) or re.search(r"\d|[≤≥<>=]", sent) or _TASK_RE.search(sent))


# --------------------------------------------------------------------- przeróbka polecenia na tryb rozkazujący
_V3 = {"obliczy": "Oblicz", "policzy": "Policz", "wyznaczy": "Wyznacz", "znajdzie": "Znajdź", "sprawdzi": "Sprawdź",
       "wypisze": "Wypisz", "poda": "Podaj", "odpowie": "Odpowiedz", "zasymuluje": "Zasymuluj", "stwierdzi": "Stwierdź",
       "określi": "Określ", "ustali": "Ustal", "zliczy": "Zlicz", "rozwiąże": "Rozwiąż", "posortuje": "Posortuj",
       "obsłuży": "Obsłuż", "przetworzy": "Przetwórz", "wyliczy": "Wylicz", "rozstrzygnie": "Rozstrzygnij",
       "zamieni": "Zamień", "wyświetli": "Wypisz", "przekształci": "Przekształć", "zweryfikuje": "Sprawdź"}
_VINF = {"obliczyć": "Oblicz", "policzyć": "Policz", "wyznaczyć": "Wyznacz", "znaleźć": "Znajdź",
         "sprawdzić": "Sprawdź", "wypisać": "Wypisz", "podać": "Podaj", "odpowiedzieć": "Odpowiedz",
         "stwierdzić": "Stwierdź", "określić": "Określ", "ustalić": "Ustal", "zliczyć": "Zlicz", "wybrać": "Wybierz",
         "uporządkować": "Uporządkuj", "rozplanować": "Rozplanuj", "zasymulować": "Zasymuluj", "wyliczyć": "Wylicz",
         "rozwiązać": "Rozwiąż", "posortować": "Posortuj", "obsłużyć": "Obsłuż", "dowiedzieć się": "Ustal",
         "przewidzieć": "Przewidź", "zaplanować": "Zaplanuj", "ułożyć": "Ułóż", "przygotować": "Przygotuj",
         "wymyślić": "Wymyśl", "zbudować": "Zbuduj", "odtworzyć": "Odtwórz", "rozdzielić": "Rozdziel"}
_IMPER = ("Oblicz", "Policz", "Wyznacz", "Znajdź", "Sprawdź", "Wypisz", "Podaj", "Odpowiedz", "Ustal", "Zlicz",
          "Określ", "Zasymuluj", "Stwierdź", "Wybierz", "Zaimplementuj")


def _imperative(sent: str) -> str | None:
    """„Pomóż mu i napisz program, który policzy X” → „Policz X”; „Twoim zadaniem jest wyznaczenie X” → „Wyznaczenie X”.
    None, gdy zdanie nie jest poleceniem."""
    s = sent.strip()
    m = re.search(r"\b(?:napisz|opracuj|zaimplementuj|stw[oó]rz)\s+program\w*,?\s+(?:kt[oó]ry|kt[oó]ra)\s+(?:(?:dla|na podstawie)\s+"
                  r"[^,]{1,60}?\s+)?(?:wczyta\s+[^,]{1,80}?,?\s+(?:i|a nast[eę]pnie|nast[eę]pnie)\s+)?"
                  r"(?P<v>[a-ząćęłńóśźż]+)\b(?P<rest>.*)$", s, re.I)
    if m and m.group("v").lower() in _V3:
        return _V3[m.group("v").lower()] + m.group("rest").rstrip(" .")
    m = re.search(r"\b(?:pom[oó][zż]\w*|pom[oó]c|prosi\w*|b[lł]aga\w*|popro\w*)\b[^.:]*?(?:[:,]\s*|\s+i\s+|\s+)"
                  r"(?P<v>" + "|".join(sorted(_VINF, key=len, reverse=True)) + r")\b(?P<rest>.*)$", s, re.I)
    if m:
        return _VINF[m.group("v").lower()] + m.group("rest").rstrip(" .")
    m = re.search(r"\b(?:" + "|".join(i.lower() for i in _IMPER) + r")\b.*$", s, re.I)
    if m and (m.start() == 0 or re.search(r"(?:[:,;–-]|\bi|\bto)\s*$", s[:m.start()]) or
              re.match(r"dla\s+(?:ka[zż]d\w+|danego|danej|podanego|podanej)\b[^,.]{0,50}$", s[:m.start()], re.I)):
        t = m.group(0).rstrip(" .")
        t = t[0].upper() + t[1:]
        mc = re.match(r"(?:je[sś]li|je[zż]eli|gdy)\s+(.+?),?\s+to\s*$", s[:m.start()], re.I)
        if mc:
            t = re.split(r"\.\s", t)[0] + f"§gdy {_clip(mc.group(1), 70)}"
        return t
    m = re.search(r"\b(?:twoim zadaniem|twoje zadanie|zadanie)\s+(?:jest|polega na|b[eę]dzie)\s+(?P<rest>.+)$", s, re.I)
    if m:
        r = m.group("rest").rstrip(" .")
        return r[0].upper() + r[1:]
    m = re.search(r"\b(?:zastanawia(?:j[aą])?\s+si[eę]|(?:chcemy|chce|chcia[lł]\w*(?:by)?|musi|nale[zż]y)\s+(?:si[eę]\s+)?"
                  r"(?:wiedzie[cć]|dowiedzie[cć](?:\s+si[eę])?|ustali[cć]|pozna[cć]))"
                  r",?\s+(?P<rest>(?:ile|jak|czy|kt[oó]r|jaki|jaka|jakie|gdzie|do jakich|do kt[oó]rych|na ile)\b.+)$", s, re.I)
    if m:
        r = m.group("rest").rstrip(" .")
        verb = "Sprawdź" if r.lower().startswith("czy") else ("Policz" if r.lower().startswith("ile") else "Ustal")
        return f"{verb}, {r}"
    return None


# ============================================================================================ streszczenie
def summarize(parsed: dict, k: int = 3) -> list[str]:
    """Streszczenie ekstrakcyjne: najpierw zdania formalne (definicje z zmiennymi, polecenie), fabuła tylko gdy nic
    innego nie ma. Zachowuje kolejność z treści."""
    sec = parsed["sections"]
    tresc = sec.get("tresc", "")
    sents = [s for s in _sentences(tresc) if not (_SENT_SPLIT_TAIL.search(s) and len(s.split()) < 7)]
    nobullet = [s for s in sents if not s.lstrip().startswith("•")]
    if len(nobullet) >= 1:
        sents = nobullet
    out_first = next((s for s in _sentences(sec.get("wyjscie", "")) if len(s.split()) >= 4), None)
    if not sents:
        return [out_first] if out_first else []
    known = _known_vars(parsed)
    names = _proper_names(tresc)
    all_terms = Counter(t for t in T.terms(tresc + " " + sec.get("wyjscie", "")))
    n = len(sents)
    df: Counter = Counter()
    sterms = []
    for s in sents:
        ts = set(T.terms(s))
        sterms.append(ts)
        df.update(ts)
    scored = []
    for i, (s, ts) in enumerate(zip(sents, sterms)):
        cent = (sum(all_terms[t] * math.log(1 + n / df[t]) for t in ts) / (len(ts) ** 0.6)) if ts else 0.0
        f = _formality(s, known, names)
        sc = f + cent / 25.0
        if _GOAL_RE.search(s):
            sc += 0.6
        scored.append((sc, f, i, s))
    formal = [x for x in scored if x[1] > 0.3]
    pool = formal if formal else [x for x in scored if x[1] > -0.2] or scored
    best = sorted(pool, key=lambda x: -x[0])[:k if formal else max(1, k - 1)]
    out = []
    for _, _, _, s in sorted(best, key=lambda x: x[2]):
        imp = _imperative(s)
        if imp and len(imp) >= 12 and re.match(r"(pom[oó][zż]|.*\b(?:prosi|b[lł]aga|popro\w+)\b)", s, re.I):
            s = imp.replace("§", " — ") + "."
        out.append(s)
    if out_first and out and not is_formal(out[0], known) and out_first not in out:
        # w treści (prawie) sama fabuła — najkonkretniejsze jest zdanie z sekcji Wyjście; dajemy je na początek
        out = [out_first] + out[:k - 1]
    return out


def given_and_goal(parsed: dict) -> tuple[list[str], list[str]]:
    sec = parsed["sections"]
    tresc = sec.get("tresc", "")
    sents = _sentences(tresc)
    known = _known_vars(parsed)
    names = _proper_names(tresc)
    goal = [s for s in sents if _GOAL_RE.search(s) and _formality(s, known, names) > -0.2]
    out_s = _sentences(sec.get("wyjscie", ""))
    if out_s:
        goal.append(out_s[0])
    given = [s for s in sents if s not in goal and _formality(s, known, names) > 0.3 and
             (_GIVEN_RE.search(s) or _DEF_RE.search(s) or _var_tokens(s, known))][:4]
    if not given:
        given = [s for s in sents if s not in goal and _formality(s, known, names) > 0][:2]
    seen, g2 = set(), []
    for s in goal:
        if s not in seen:
            seen.add(s)
            g2.append(s)
    return given, g2[-4:]


# ============================================================================================ cel zadania
_NOM2ACC_NOUNS = {"liczba": "liczbę", "suma": "sumę", "kwota": "kwotę", "reszta": "resztę", "cena": "cenę",
                  "waga": "wagę", "droga": "drogę", "ścieżka": "ścieżkę", "różnica": "różnicę", "temperatura":
                  "temperaturę", "pozycja": "pozycję", "odpowiedź": "odpowiedź", "mediana": "medianę",
                  "długość": "długość", "wartość": "wartość", "odległość": "odległość", "ilość": "ilość",
                  "wysokość": "wysokość", "głębokość": "głębokość", "trasa": "trasę", "lista": "listę",
                  "permutacja": "permutację", "kolejność": "kolejność", "minimalna": "minimalną"}


def _to_acc(phrase: str) -> str:
    """„minimalna kwota potrzebna…” → „minimalną kwotę potrzebną…” (tylko początek frazy, rzeczowniki z listy)."""
    words = phrase.split(" ")
    noun_at = next((i for i, w in enumerate(words[:4]) if w.lower() in _NOM2ACC_NOUNS and w.lower().endswith("a")), None)
    if noun_at is None:
        return phrase
    for i in range(noun_at):
        w = words[i]
        if re.fullmatch(r"[\w" + _PL + r"-]*[^\Wąę]a", w, re.I) and len(w) > 2:
            words[i] = w[:-1] + "ą"
    words[noun_at] = _NOM2ACC_NOUNS[words[noun_at].lower()]
    if noun_at + 1 < len(words) and re.fullmatch(r"[a-ząćęłńóśźż]+(?:n|t|w|z)a", words[noun_at + 1]):
        words[noun_at + 1] = words[noun_at + 1][:-1] + "ą"       # „kwota potrzebna” → „kwotę potrzebną”
    return " ".join(words)


_OUT_PHRASE = [
    # „jedną liczbę całkowitą – największy możliwy połów”, „liczbę – maksymalną temperaturę”
    re.compile(r"(?:jedn\w+|dok[lł]adnie jedn\w+|pojedyncz\w+)?\s*(?:nieujemn\w+\s+|dodatni\w+\s+)?"
               r"(?:liczb[aeęy]\w*|s[lł]ow[oa]\w*|napis\w*|warto[sś][cć])(?:\s+ca[lł]kowit\w+|\s+rzeczywist\w+|"
               r"\s+naturaln\w+)?\s*(?:[–—:]|\s-\s)\s*(?P<x>[^.;]{3,160})", re.I),
    re.compile(r"\b(?:r[oó]wn\w+|oznaczaj[aą]c\w+|b[eę]d[aą]c\w+|okre[sś]laj[aą]c\w+|reprezentuj[aą]c\w+|"
               r"przedstawiaj[aą]c\w+)\s+(?:odpowiednio\s+)?(?P<x>[^.;]{3,160})", re.I),
    re.compile(r"\b(?:wypisz|wypisa[cć]|poda[cć]|podaj|wypisuje|zwr[oó]ci[cć]|zwraca[cć]?|wy[sś]wietli[cć])\s+(?:na\s+\w+\s+wyj[sś]\w+\s+)?(?P<x>(?!jedn|dok[lł]adnie|"
               r"\w+\s+wiersz|n\s|w\s|na\s+(?:standard|wyj)|po\s|odpowied|odpowiedni)[^.;]{3,160})", re.I),
    re.compile(r"\b(?:powin\w+\s+(?:si[eę]\s+)?(?:znale[zźsś][cć]\s+si[eę]|znajdowa[cć]\s+si[eę]|zawiera[cć]|by[cć]|"
               r"(?<=si[eę] )znale[zźsś][cć]|(?<=si[eę] )znajdowa[cć]))[\s,]+(?:(?:ka[zż]d\w+|po\s+jedn\w+)\s+w\s+(?:oddzieln|osobn)\w+\s+"
               r"(?:linii|wierszu),\s*)?"
               r"(?P<x>(?!jedn|dok[lł]adnie|odpowied)[^.;]{3,160})", re.I),
]
_CUT_RE = re.compile(r",\s+a\s+(?:je[sś]li|je[zż]eli|gdy)\s|,\s+(?:a\s+)?w\s+(?:drugim|trzecim|kolejn\w+|nast[eę]pn\w+)\s|,?\s+(?:lub|albo)\s+(?:-1|jedno\s+s[lł]owo|s[lł]owo|napis|liczb\w+\s+-1|[„\"']?[A-Z]{2,})|"
                     r",?\s+je[zż]eli\s|,?\s+je[sś]li\s|,?\s+gdy\s|,?\s+w przypadku|;|\s+\(|,\s+(?:gdzie|przy czym)\s")


def _clean_goal(x: str) -> str:
    x = re.sub(r"\s+", " ", x).strip(" ,.–-:")
    md = re.search(r"\s[–—]\s", x)
    if md and len(x[:md.start()].split()) >= 3:
        x = x[:md.start()]
    m = _CUT_RE.search(x)
    if m and m.start() > 8:
        x = x[:m.start()]
    x = re.sub(r"^(?:jedn\w+\s+)?(?:liczb\w+\s+)?(?:ca[lł]kowit\w+\s+)?(?:[–—-]\s*)", "", x).strip()
    words = x.split()
    if len(words) > 16:
        x = " ".join(words[:16]) + "…"
    return x.strip(" ,")


_DAT = {"liczbie": "liczbę", "sumie": "sumę", "kwocie": "kwotę", "różnicy": "różnicę", "długości": "długość",
        "wartości": "wartość", "odległości": "odległość", "wysokości": "wysokość", "ilości": "ilość", "cenie": "cenę",
        "medianie": "medianę", "reszcie": "resztę", "liczbom": "liczby"}


def _dat2acc(x: str) -> str:
    """„minimalnej liczbie szuflad” (po „równą”) → „minimalną liczbę szuflad”."""
    w = x.split(" ")
    noun = next((i for i, t in enumerate(w[:4]) if t.lower() in _DAT), None)
    if noun is None:
        return x
    for i in range(noun):
        if w[i].endswith("ej") and len(w[i]) > 3:
            w[i] = w[i][:-2] + "ą"
        elif w[i].endswith("emu") and len(w[i]) > 4:
            w[i] = w[i][:-3] + "y"
    w[noun] = _DAT[w[noun].lower()]
    return " ".join(w)


def _goal_from_output(wyj: str, strong_only: bool = False) -> str | None:
    for sent in _sentences(wyj)[:3]:
        mg = re.search(r"\bgdzie\s+([A-Za-z](?:_?[a-z0-9])?)\s+(?:oznacza|to|jest)\s+([^.;]{5,140})", sent)
        if mg:
            return _clean_goal(mg.group(2)) + f" (dla każdego {ST.norm_var(mg.group(1))})"
        md = re.search(r"\bTAK\b[”\"']?,?\s+(?:je[sś]li|je[zż]eli|gdy)\s+([^,.;]{5,140})", sent) or \
            re.search(r"w zale[zż]no[sś]ci od tego,?\s+czy\s+([^.;]{5,140})", sent)
        if md:
            return "czy " + _clean_goal(md.group(1))
        mz = re.search(r"\bczyli\s+([^.;]{5,140})", sent)
        if mz and not re.match(r"\s*(?:w|po|na)\s", mz.group(1)):
            return _clean_goal(mz.group(1))
        ms = re.search(r"([A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]\w+)\s+powinn\w+\s+(?:by[cć]\s+)?(?:posortowan\w+|wypisan\w+ w kolejno[sś]ci)\s*"
                       r"([^.;]{0,60})", sent)
        if ms:
            return f"posortowane {ms.group(1).lower()}" + (f" ({_clean_goal(ms.group(2))})" if ms.group(2).strip() else "")
        low = sent.lower()
        if re.search(r"\b(TAK|NIE)\b", sent) and not re.search(r"liczb\w*\s*[–—-]", sent):
            continue
        sent = re.sub(r"\s*\((?:[^()]{0,80})\)", "", sent)          # „(oznaczająca numer wierzchołka)” — dopowiedzenia
        for ri, rx in enumerate(_OUT_PHRASE):
            if strong_only and ri >= 2:
                break
            m = rx.search(sent)
            if not m:
                continue
            if re.search(r"w\s+(?:drugim|trzecim|czwartym|kolejnych|nast[eę]pnych)\s+wierszu[^.]*$", sent[:m.start()], re.I):
                continue
            x = _clean_goal(m.group("x"))
            if re.match(r"-?\d", x):
                continue            # „wypisać -1 jeśli …” — to przypadek specjalny, nie cel
            if ri == 1 and re.search(r"\br[oó]wn\w*\s+$", sent[:m.start("x")]):
                x = _dat2acc(x)
            xl = x.lower()
            if len(x) < 4 or re.match(r"(?:oddzielon|rozdzielon|w\s+(?:jednym|osobn|kolejn)|po\s+jedn|w\s+i-t|"
                                       r"odpowiedni|w\s+kolejno|liczb\w*\s+ca[lł]kowit|"
                                       r"spacj|dok[lł]adnie|na\s+standard)", xl):
                continue
            if "wiersz" in low and re.match(r"^\w+\s+wiersz", xl):
                continue
            return x
    return None


def goal_phrase(p: dict, task: dict | None = None) -> dict:
    """{'verb', 'what', 'source'} — co trzeba policzyć, najkrócej. Najpierw precyzyjne Wyjście („– największy
    możliwy połów”), potem polecenie z treści („Twoim zadaniem jest…”, „Napisz program, który policzy…”)."""
    sec = p["sections"]
    x = _goal_from_output(sec.get("wyjscie", ""), strong_only=True)
    weak = None if x else _goal_from_output(sec.get("wyjscie", ""))
    tid = (task or {}).get("id")
    verb = {"decision": "Sprawdź", "construct": "Wypisz", "simulate": "Wypisz", "queries": "Odpowiadaj",
            "count": "Policz", "optimize": "Policz", "interactive": "Ustal"}.get(tid or "", "Policz")
    if tid == "queries" and not re.search(r"zapyta|pytani|dla ka[zż]d|operacj\w* typu", _low(sec.get("wyjscie", ""))):
        verb = "Wypisz"
    listing = r"(?:numery|elementy|indeksy|liczby|wszystkie|kolejne|n\s|ci[aą]g|list\w*|napis|s[lł]owo|permutacj|dzielniki|posortowan)"
    if x and x.lower().startswith("czy "):
        return {"verb": "Sprawdź", "what": x, "source": "wyjście"}
    if x:
        v1 = "Wypisz" if verb == "Policz" and re.match(listing, x.lower()) else verb
        return {"verb": v1, "what": x, "source": "wyjście"}
    tresc = sec.get("tresc", "")
    known = _known_vars(p)
    names = _proper_names(tresc)
    cands = []
    for i, s in enumerate(_sentences(tresc)):
        imp = _imperative(s)
        if imp:
            cands.append((_formality(s, known, names) + (0.5 if "?" in s else 0) + i * 0.01, imp))
    for s in _sentences(tresc):
        if s.rstrip().endswith("?") and re.match(r"(?:ile|jak|czy|kt[oó]r|jaki|jaka|jakie|na ile)\b", s, re.I):
            cands.append((_formality(s, known, set()) + 0.4, s.rstrip("?")))
    if weak and (not cands or max(c[0] for c in cands) < 1.0):
        v2 = "Wypisz" if verb == "Policz" and re.match(listing, weak.lower()) else verb
        return {"verb": v2, "what": weak, "source": "wyjście"}
    if cands:
        imp = max(cands, key=lambda c: c[0])[1]
        imp = re.sub(r"\s+", " ", imp).strip()
        imp, _, cond = imp.partition("§")
        m = re.match(r"(\w+)[,:]?\s+(.*)$", imp)
        if m and m.group(1) in _IMPER + tuple(_VINF.values()):
            return {"verb": m.group(1), "what": _clean_goal(m.group(2)) + (f" ({cond})" if cond else ""), "source": "treść"}
        mq = re.match(r"(na ile|ile|czy|jak\w*|kt[oó]r\w*)\b(.*)$", imp, re.I)
        if mq:
            v = "Sprawdź" if mq.group(1).lower() == "czy" else ("Policz" if "ile" in mq.group(1).lower() else "Ustal")
            return {"verb": v, "what": _clean_goal(mq.group(1).lower() + mq.group(2)), "source": "treść"}
        return {"verb": None, "what": _clean_goal(imp), "source": "treść"}
    return {"verb": None, "what": None, "source": None}


# ============================================================================================ typ zadania
_ANSWER_WORDS = ("TAK", "NIE", "BRAK", "PRAWDA", "FAŁSZ", "YES", "NO", "ON", "OFF", "IMPOSSIBLE", "NIEMOŻLIWE")


def answer_words(wyj: str) -> list[str]:
    """Dokładne słowa do wypisania („TAK”, „NIE”, „BRAK”, „-1”…) — w kolejności z treści."""
    out: list[str] = []
    for m in re.finditer(r"(?<![\w" + _PL + r"])[„\"'‘]?([A-ZĄĆĘŁŃÓŚŹŻ]{2,}|-1)[”\"'’]?(?![\w" + _PL + r"])", wyj or ""):
        w = m.group(1)
        quoted = m.group(0)[:1] in "„\"'‘"
        near = re.search(r"(?:wypisa\w*|wypisz|s[lł]ow\w*|napis\w*|odpowied\w*|wynikiem|samo)\s+(?:\w+\s+){0,2}$",
                         (wyj or "")[max(0, m.start() - 40):m.start()], re.I)
        ok_word = w in _ANSWER_WORDS or (w != "-1" and len(w) >= 3 and w.isalpha() and bool(quoted or near)
                                         and w not in ("ASCII", "PESEL", "DFS", "BFS", "LCA", "NWD", "NWW", "XOR"))
        ok_m1 = w == "-1" and bool(re.search(r"(?:wypisa\w*|wypisz|liczb\w+|wynik\w*|odpowied\w*)\s+-1\b|"
                                             r"-1\s*,?\s*(?:je[sś]li|gdy|je[zż]eli)", wyj))
        if (ok_word or ok_m1) and w not in out:
            out.append(w)
    return out[:6]


def task_type(p: dict, struct: dict) -> dict:
    """Optymalizacja / zliczanie / decyzja / konstrukcja / symulacja / zapytania / interaktywne — z konsekwencją."""
    sec = p["sections"]
    wyj = sec.get("wyjscie", "")
    tresc = sec.get("tresc", "")
    full = " ".join(sec.get(k, "") for k in ("tresc", "wejscie", "wyjscie"))
    low, outl = _low(full), _low(wyj)
    words = answer_words(wyj if wyj.strip() else tresc)
    tags: list[str] = []
    mod = re.search(r"modulo\s+(\d[\d ^+·*]*\d|\d)|reszt\w* z dzielenia\s+(?:\w+\s+){0,6}?przez\s+(\d[\d ^+·*]*\d|\d)",
                    full, re.I)
    if (p.get("meta") or {}).get("interactive") or re.search(r"interakc|interaktywn", low):
        tags.append("interactive")
    if struct.get("flags", {}).get("queries"):
        tags.append("queries")
    yes_no = [w for w in words if w in ("TAK", "NIE", "PRAWDA", "FAŁSZ", "YES", "NO")]
    goal_txt = _low(" ".join(given_and_goal(p)[1]))
    if len(yes_no) >= 2 or (yes_no and re.search(r"\bczy\b", goal_txt)):
        tags.append("decision")
    opt = bool(_OPT_RE.search(re.sub(r"\b[a-z]-?t\w*\s+(?:co do wielkosci\s+)?(?:najmniejsz|najwieksz)\w*|co najmniej|"
                                     r"co najwyzej|przynajmniej|nie mniej", " ", goal_txt + " " + outl)))
    strong_count = re.search(r"na ile (?:roznych )?sposob|liczb\w* (?:\w+ ){0,2}(?:sposob|par|trojek|podciag|podzbior|"
                             r"mozliwosci|roznych|ciagow|slow|sciezek|drzew|permutacji|wystapien|kompozycji)|"
                             r"ile (?:jest )?(?:roznych|par|trojek|sposobow)|zlicz", goal_txt + " " + outl)
    if strong_count or mod:
        tags.append("count")
    if opt:
        tags.append("optimize")
    if re.search(r"dowoln\w* (?:z nich|poprawn|rozwiaz|odpowiedz|ustawien|kolejnosc|sposrod|takich|takie|taki|taka|jedn)", outl) \
            or re.search(r"(?:kilka|wiele|wiecej niz jedn\w*) (?:\w+ )?(?:poprawnych |mozliwych |prawidlowych )?"
                         r"(?:odpowiedzi|rozwiazan|wynikow|ustawien)|wypisz dowoln|mozna wypisac dowoln|"
                         r"dowoln\w* (?:z nich|poprawn\w* (?:rozwiazan|odpowiedz|rozmieszczen|ustawien))", low):
        tags.append("construct")
    if re.search(r"zasymul|symulacj|po wykonaniu wszystkich|wykonuje (?:kolejne|nastepujace) operacj|stan po", low):
        tags.append("simulate")
    order = ["interactive", "queries", "decision", "construct", "optimize", "count", "simulate"]
    if strong_count or mod:
        order = ["interactive", "queries", "decision", "construct", "count", "optimize", "simulate"]
    tags = [t for t in order if t in tags]
    prim = tags[0] if tags else "compute"
    if prim == "queries" and "decision" in tags:
        pass
    labels = {"interactive": "Interaktywne", "queries": "Zapytania / operacje", "decision": "Decyzja (tak/nie)",
              "construct": "Dowolna poprawna odpowiedź", "count": "Zliczanie", "optimize": "Optymalizacja", "simulate": "Symulacja",
              "compute": "Oblicz wynik"}
    cons: list[str] = []
    if "interactive" in tags:
        cons.append("Po każdym pytaniu `cout << flush` (albo endl); nie czytaj całego wejścia na raz.")
    if "queries" in tags:
        q = struct.get("query_count")
        cons.append(f"Liczy się koszt JEDNEGO zapytania × liczba zapytań{(' (' + q + ')') if q else ''}. "
                    "Zapytania znane z góry → można je wczytać wszystkie i odpowiadać offline (posortowane).")
    if "decision" in tags:
        w = [x for x in words if x in ("TAK", "NIE", "PRAWDA", "FAŁSZ", "YES", "NO")]
        cons.append("Wypisz dokładnie " + " / ".join(f"„{x}”" for x in w) + " — wielkość liter ma znaczenie.")
    if "construct" in tags:
        cons.append("Poprawnych odpowiedzi może być kilka — Twoja może różnić się od przykładu i nadal być dobra "
                    "(porównuj sprawdzaczką, nie diffem).")
    if "count" in tags:
        combi = re.search(r"sposob|par\b|par |trojek|podciag|podzbior|permutacj|ciagow|drzew|sciezek|kompozycj|mozliwosci",
                          goal_txt + " " + outl)
        cons.append(f"Wynik modulo {_mod_text(mod)} — bierz modulo po każdym + i ×." if mod else
                    ("Liczba możliwości rośnie szybko — sprawdź, czy wynik mieści się w long long." if combi else
                     "Policz dokładnie to, co definiuje treść (uważaj na przypadki brzegowe: 0 wystąpień, powtórzenia)."))
    if "optimize" in tags and "construct" not in tags:
        cons.append("Wypisujesz wartość optimum — samego rozwiązania nie trzeba (chyba że treść każe).")
    if "simulate" in tags:
        cons.append("Wykonaj operacje dokładnie wg opisu; sprawdź, czy naiwna symulacja zmieści się w limicie.")
    if prim == "compute":
        cons.append("Policz i wypisz wynik w podanym formacie.")
    return {"id": prim, "label": labels[prim], "tags": tags, "labels": [labels[t] for t in tags] or [labels[prim]],
            "consequence": " ".join(cons), "words": words, "modulo": _mod_text(mod) if mod else None}


def _mod_text(m) -> str | None:
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").strip()
    v = ST.parse_value(raw.replace("*", "·"))
    return ST.fmt_value(v) if isinstance(v, int) else raw


# ============================================================================================ struktura danych
_GRAPH_CUE = re.compile(r"graf\w*|wierzcho[lł]k\w*|kraw[eę]d\w*|drog[aię]\w*|dr[oó]g\b|po[lł][aą]cze\w*|po[lł][aą]czon\w*|"
                        r"tunel\w*|most\w*|korytarz\w*|przej[sś][cć]\w*|lot\w*\b|szlak\w*|kana[lł]\w*|[sś]cie[zż]k\w*|"
                        r"miast\w*|skrzy[zż]owa\w*|stacj\w*|bunkr\w*|lotnisk\w*|odnog\w*|rozwidle\w*|ulic\w*|linie? kolej",
                        re.I)
_TREE_CUE = re.compile(r"\bdrzew(?!\w* przedzia)\w*|\bkorze[nń]\w*|\bkorzeni\w*|poddrzew\w*|\bli[sś][cć]\w*|"
                       r"hierarchi\w*|rodzic\w*|przodk\w*|potomk\w*|przełożon\w*|kierownik\w*|ojc\w*|syn\w*\b", re.I)
_PARENT_CUE = re.compile(r"rodzic\w*|ojc\w*|ojciec|przełożon\w*|przelozon\w*|kierownik\w*|szef\w*|\bnad\s+(?:nim|pokojem|"
                         r"wierzcho[lł]kiem)|bezpo[sś]redni\w* przodk\w*|poprzednik\w*", re.I)
_QUERY_CUE = re.compile(r"zapyta\w*|pyta[nń]\w*|pytani\w*|operacj\w*|polece[nń]\w*|zdarze[nń]\w*|naci[sś]ni[eę]\w*|"
                        r"rozkaz\w*|instrukcj\w*|komend\w*|pr[oó][sś]b\w*|zmian\w* (?:stanu|warto)", re.I)
_TEST_CUE = re.compile(r"\btest\w*|zestaw\w* danych|przypadk\w* testow\w*|zestaw\w*", re.I)
_DIRECTED_CUE = re.compile(r"skierowan\w*|jednokierunkow\w*|\bz\s+(?:\w+\s+)?[a-z](?:_?i)?\s+do\s+(?:\w+\s+)?[a-z](?:_?i)?\b|"
                           r"prowadz\w*\s+z\b|da si[eę] przej[sś][cć] z\b|od\s+[a-z](?:_?i)?\s+do\s+[a-z](?:_?i)?\s+"
                           r"(?:ale|lecz)\s+nie|w jedn\w+ stron\w*|nie tworz\w* cykl|acykliczn", re.I)
_UNDIRECTED_CUE = re.compile(r"dwukierunkow\w*|nieskierowan\w*|w obie strony", re.I)
_WEIGHT_CUE = re.compile(r"wag[aię]\w*|d[lł]ugo[sś]\w*|koszt\w*|czas\w*|odleg[lł]\w*|przepustowo\w*|op[lł]at\w*|"
                         r"wysoko[sś]\w*|cen[aęy]\b|\bmetr\w*|minut\w*|sekund\w*|godzin\w*|kilometr\w*|\bkm\b", re.I)


def _check(fmt: list[dict], inp: str) -> dict | None:
    """check_format; gdy nie przechodzi tylko ostatni element — wiersze zapytań ze znacznikami („S 1”, „Z 2 10”), których
    parser nie umie opisać jednym szablonem — sprawdzamy go sami (liczba wierszy = powtórzenia, każdy zaczyna się znacznikiem)."""
    if not fmt:
        return None
    res = ST.check_format(fmt, inp)
    if res.get("ok") and not res.get("reason"):
        return res
    last = fmt[-1]
    if last.get("kind") not in ("rows", "queries", "grid", "lines") or any(x.get("per_test") or x.get("kind") == "multitest"
                                                                           for x in fmt):
        return None
    head = ST.check_format(fmt[:-1], inp) if len(fmt) > 1 else {"ok": False, "reason": "zostało", "consumed_lines": 0,
                                                                  "spans": {}, "env": {}}
    if head.get("ok") or not str(head.get("reason") or "").startswith("zostało"):
        return None
    lines = ST._tok_lines(inp)
    start = head.get("consumed_lines") or 0
    rest = lines[start:]
    if not rest or _tags_of(rest) is None:
        return None
    want = ST._eval_len(last.get("repeat"), head.get("env") or {})
    if want is not None and want != len(rest):
        return None
    spans = dict(head.get("spans") or {})
    spans[len(fmt) - 1] = [start, len(lines)]
    return {"ok": True, "reason": "", "spans": spans, "env": head.get("env") or {}, "relaxed": True,
            "consumed_lines": len(lines)}


def _sample_spans(p: dict) -> tuple[list[list[str]] | None, dict | None]:
    """(linie tokenów pierwszego przykładu, wynik sprawdzenia) — gdy format jest potwierdzony przykładem."""
    fmt = display_format(p)          # nazwy poprawione wg treści („a” → „n”), więc „n wierszy” da się policzyć
    for s in p.get("samples") or []:
        if s.get("source") == "ocen" or s.get("big") or not s.get("input"):
            continue
        res = _check(fmt, s["input"])
        if res:
            return ST._tok_lines(s["input"]), res
        return None, None
    return None, None


def _is_num(t: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", t))


def _tags_of(lines: list[list[str]]) -> list[str] | None:
    """Znaczniki zapytań z linii przykładu („Q 1”, „S 1 5”, „+ 7”, „push 3”, „pop”) albo None."""
    if not lines:
        return None
    firsts = [ln[0] for ln in lines if ln]
    if not firsts or any(_is_num(t) for t in firsts):
        return None
    distinct = list(dict.fromkeys(firsts))
    if len(distinct) > 8 or (len(distinct) == len(firsts) and len(firsts) > 2):
        return None
    if not any(len(ln) > 1 for ln in lines) and len(distinct) < 2:
        return None
    if any(len(t) > 12 for t in distinct):
        return None
    return distinct


def _tree_check(rows: list[list[str]], nverts: int) -> bool:
    """Czy krawędzie z przykładu tworzą drzewo na wierzchołkach 1..n (albo 0..n−1)."""
    try:
        edges = [(int(r[0]), int(r[1])) for r in rows]
    except (ValueError, IndexError):
        return False
    if len(edges) != nverts - 1:
        return False
    lo = min([a for e in edges for a in e] or [1])
    base = 0 if lo == 0 else 1
    par = list(range(nverts + 1))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a, b in edges:
        a, b = a - base, b - base
        if not (0 <= a < nverts and 0 <= b < nverts):
            return False
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        par[ra] = rb
    return True


def _item_names(it: dict, desc: str, wej: str | None = None) -> list[str]:
    """Nazwy zmiennych elementu formatu, które naprawdę występują w opisie (reszta to nazwy nadane przez parser).
    Opis elementu zgadniętego z przykładów jest sztuczny („n wierszy: a, b”) — wtedy szukamy w tekście sekcji Wejście."""
    out = []
    src = (wej or "") if it.get("source") == "samples" else desc
    for v in it.get("vars") or []:
        b = ST.base_var(v)
        if v == "plansza":
            continue
        for m in re.finditer(r"(?<![\w" + _PL + r"])" + re.escape(b) + r"(?:_?(?:[0-9]{1,2}|[ijkmn]))?(?![\w" + _PL + r"])", src):
            if b in ST._POLISH_SINGLE and not _math_ctx(src, m.start(), m.end()):
                continue
            out.append(v)
            break
    return out


def _rename_map(p: dict) -> dict[str, str]:
    """Naprawa nazw w pierwszym wierszu, gdy parser je zgadł: „liczba n (…), a po niej liczba k” dało [n, a] → a ↦ k;
    format z przykładów [n] przy „liczba testów t” → n ↦ t. Tylko gdy liczba kandydatów z tekstu się zgadza."""
    fmt = p.get("input_format") or []
    cons = p.get("constraints") or {}
    if not fmt or fmt[0].get("kind") not in ("scalars", "multitest"):
        return {}
    first = fmt[0]
    vs = [ST.base_var(v) for v in first.get("vars") or []]
    wej = p["sections"].get("wejscie", "")
    known = {ST.base_var(k) for k in cons if not k.startswith("_") and "|" not in k}
    src = (first.get("desc") or "") if first.get("source") != "samples" else ((_sentences(wej)[:1] or [""])[0])
    if not src.strip() and first.get("source") == "samples":
        # brak sekcji Wejście — pierwsze zdanie treści, które wymienia tyle znanych zmiennych, ile jest w wierszu
        for sent in _sentences(p["sections"].get("tresc", "")):
            vs0 = [ST.base_var(ST.norm_var(m.group(1))) for m in _VAR_TOK.finditer(sent)]
            if len([v for v in dict.fromkeys(vs0) if v in known]) >= len(first.get("vars") or []):
                src = sent
                break
    cands: list[str] = []
    for m in _VAR_TOK.finditer(src):
        b = ST.base_var(ST.norm_var(m.group(1)))
        if b in known and b not in cands and (b not in ST._POLISH_SINGLE or _math_ctx(src, m.start(), m.end())):
            cands.append(b)
    if len(cands) != len(vs) or cands == vs:
        return {}
    used = {ST.base_var(v) for it in fmt[1:] for v in it.get("vars") or []}
    same = sum(1 for v, c in zip(vs, cands) if v == c)
    mp = {}
    if same * 2 < len(vs) and not any(c in used for c in cands if c not in vs):
        # nazwy z parsera w większości nie pasują do treści („e, n, m” przy „d, m, y”) — bierzemy nazwy z treści
        return {v: c for v, c in zip(vs, cands) if v != c}
    for v, c in zip(vs, cands):
        if v == c:
            continue
        if v in cands or c in vs or (c in used and first.get("source") != "samples"):
            return {}
        mp[v] = c
    return mp


def _rn(x, mp: dict) -> str | None:
    if x is None:
        return None
    return re.sub(r"[A-Za-z]\w*", lambda m: mp.get(m.group(0), m.group(0)), str(x))


def display_format(p: dict) -> list[dict]:
    """Format wejścia z poprawionymi nazwami (do wyświetlania; check_format działa na oryginale)."""
    if p.get("_te_dfmt") is not None:
        return [dict(x) for x in p["_te_dfmt"]]
    out = _display_format(p)
    p["_te_dfmt"] = out
    return [dict(x) for x in out]


def _display_format(p: dict) -> list[dict]:
    mp = _rename_map(p)
    cons = p.get("constraints") or {}
    cbase = {ST.base_var(k) for k in cons if not k.startswith("_") and "|" not in k}
    wej = p["sections"].get("wejscie", "")
    out = []
    for it in p.get("input_format") or []:
        d = dict(it)
        if mp:
            d["vars"] = [mp.get(ST.base_var(v), v) if ST.base_var(v) == v else v for v in it.get("vars") or []]
            for key in ("repeat", "length"):
                if it.get(key) is not None:
                    d[key] = _rn(it[key], mp)
        if it.get("kind") == "array" and len(it.get("vars") or []) == 1 and not _item_names(d, it.get("desc") or "", wej):
            # ciąg bez nazwy w formacie, ale opis ma „(0 ≤ ai ≤ 10^18)” — bierzemy tę nazwę
            subs = [ST.base_var(ST.norm_var(m.group(1))) for m in _VAR_TOK.finditer(it.get("desc") or "")
                    if re.fullmatch(r"[A-Za-z]_(?:i|1|\{?i\}?)", ST.norm_var(m.group(1)))]
            subs = [x for x in subs if x in cbase]
            ml = re.search(r"liczb\w*\s+(?:ca[lł]kowit\w+\s+)?([A-Za-z])\s+(?:oznaczaj|opisuj|b[eę]d[aą]c|okre[sś]laj|\()",
                           it.get("desc") or "")
            if not subs and ml and ml.group(1) in cbase and ml.group(1) not in ("i", "o", "u"):
                subs = [ml.group(1)]
            if subs:
                d["vars"] = [subs[0] + "_i"]
                d["desc"] = it.get("desc")
        elif it.get("kind") == "rows" and it.get("vars") and it.get("source") != "samples" and \
                not _item_names(d, it.get("desc") or "", wej):
            dp = [x for x in _desc_params(it.get("desc") or "") if "/" not in x]
            if len(dp) == len(it["vars"]):
                d["vars"] = dp          # „trzy liczby x, y, z”, a parser nazwał je a, b, c
        out.append(d)
    return out


def structure(p: dict) -> dict:
    """Rozpoznana struktura wejścia: drzewo / graf (skierowany? ważony?) / plansza / napisy / ciąg / liczby,
    + zapytania i wiele testów. Każdy element formatu dostaje rolę i opis po polsku."""
    fmt = display_format(p)
    mp = _rename_map(p)
    sec = p["sections"]
    wej, tresc, wyj = sec.get("wejscie", ""), sec.get("tresc", ""), sec.get("wyjscie", "")
    full = " ".join([tresc, wej, wyj])
    cons = p.get("constraints") or {}
    sb = ST.size_bounds(cons)
    mean = _meanings(p)
    lines, chk = _sample_spans(p)
    spans = (chk or {}).get("spans") or {}
    env = dict((chk or {}).get("env") or {})          # sprawdzane na formacie z poprawionymi nazwami
    first_scalars = next((it for it in fmt if it.get("kind") == "scalars"), None)
    vert_var = None
    for v in (first_scalars or {}).get("vars") or []:
        b = ST.base_var(v)
        if re.search(r"^(?:liczb\w* |ilo[sś]\w* )?(?:wierzcho|miast|rozwidl|pokoi|pokoj|bunkr|wysp|skrzy|stacj|w[eę]z[lł]|os[oó]b|"
                     r"uczni|kasjer|pracownik|lotnisk|budyn|dom)", _low(mean.get(b, ""))):
            vert_var = b
            break
    if vert_var is None and first_scalars and first_scalars.get("vars"):
        vert_var = ST.base_var(first_scalars["vars"][0])
    first_vals = {ST.base_var(v): env.get(ST.base_var(v)) for v in (first_scalars or {}).get("vars") or []}
    roles: list[dict] = []
    flags = {"queries": False, "multitest": False, "tree": False, "graph": False, "directed": False,
             "weighted": False, "rooted": False, "grid": False}
    notes: list[str] = []
    queries: list[dict] = []
    query_count = None
    query_word = "zapytań"
    directed = bool(_DIRECTED_CUE.search(full)) and not _UNDIRECTED_CUE.search(full)

    for idx, it in enumerate(fmt):
        k = it.get("kind")
        desc = it.get("desc") or ""
        dlow = _low(desc)
        rep = str(it.get("repeat") or "")
        rep_b = re.sub(r"[-+*·].*$", "", rep)
        rep_mean = _low(mean.get(rep_b, ""))
        span = spans.get(idx)
        ilines = lines[span[0]:span[1]] if (lines is not None and span) else None
        role = {"index": idx, "line": it.get("line"), "kind": k, "role": k, "vars": list(it.get("vars") or []),
                "repeat": it.get("repeat"), "length": it.get("length"), "names": _item_names(it, desc, wej)}
        if idx == 0 and mp:
            role["renamed"] = True
            role["names"] = list(role["vars"])
        if k == "multitest":
            role["role"] = "multitest"
            flags["multitest"] = True
            v = (it.get("vars") or ["t"])[0]
            role["text"] = f"{v} — liczba zestawów danych; dalej {v} razy opis zestawu"
            notes.append(f"Wiele zestawów danych ({v}) — czyść struktury między zestawami.")
        elif k == "scalars":
            role["role"] = "scalars"
            parts = []
            for v in it.get("vars") or []:
                m = mean.get(ST.base_var(v))
                parts.append(f"{v} ({_clip(m, 40)})" if m else v)
            role["text"] = ", ".join(parts)
        elif k == "array":
            L = it.get("length")
            v = (it.get("vars") or ["a"])[0]
            if (str(L).replace(" ", "").endswith("-1") and (_PARENT_CUE.search(desc) or
                                                            (_TREE_CUE.search(full) and _PARENT_CUE.search(full)))):
                role["role"] = "parents"
                flags["tree"] = flags["rooted"] = True
                role["text"] = f"rodzic każdego wierzchołka ({L} liczb) → drzewo ukorzenione"
                notes.append(f"{L} liczb „rodzic wierzchołka” → drzewo ukorzenione (zapis przez rodziców).")
            else:
                role["role"] = "array"
                nm = ST.base_var(v) if role["names"] else None
                what = _clip(_array_meaning(desc), 50)
                role["text"] = (f"ciąg {nm + '_1 … ' + nm + '_' + str(L) if nm and L else (nm or '')} "
                                f"({L or '?'} liczb{', ' + what if what else ''})").replace("  ", " ")
                if re.search(r"permutacj", _low(desc + " " + tresc)):
                    role["text"] += " — permutacja"
        elif k == "string":
            role["role"] = "string"
            v = (it.get("vars") or ["s"])[0]
            role["text"] = f"napis {v if role['names'] else ''}".strip() + (f" (długość {it['length']})" if it.get("length") else "")
        elif k in ("lines", "grid", "rows", "queries"):
            tags = _tags_of(ilines) if ilines is not None else None
            is_query = bool(tags) or k == "queries" or bool(_QUERY_CUE.search(dlow) or _QUERY_CUE.search(rep_mean)) \
                or (rep_b in ("q", "Q") and not _TEST_CUE.search(rep_mean))
            is_tests = bool(_TEST_CUE.search(rep_mean)) or bool(re.search(r"\bka[zż]d\w* (?:test|zestaw)", dlow))
            ncols = None
            if ilines:
                cnt = Counter(len(x) for x in ilines)
                ncols = cnt.most_common(1)[0][0]
            elif it.get("vars"):
                ncols = len(it["vars"])
            ints = all(_is_num(t) for x in ilines for t in x) if ilines else \
                (bool(it.get("types")) and all(t in ("int", "long long") for t in it.get("types") or []))
            if is_tests and not tags and k == "rows":
                role["role"] = "tests"
                flags["multitest"] = True
                role["text"] = f"{rep} zestawów danych, w każdym: " + (", ".join(role["names"] or it.get("vars") or []) or "dane")
                notes.append(f"{rep} niezależnych zestawów danych (po jednym w wierszu).")
            elif is_query and k != "grid" or (tags and _QUERY_CUE.search(full)):
                role["role"] = "queries"
                flags["queries"] = True
                query_count = rep or query_count
                query_word = "operacji" if re.search(r"operacj|polece|naci[sś]ni|zdarze", dlow + " " + rep_mean) else "zapytań"
                role["tags"] = tags or []
                queries = _query_types(p, ilines, tags, it, desc, idx)
                role["query_types"] = queries
                untagged = queries[0]["params"] if (queries and not tags) else role["names"]
                role["text"] = f"{_rep_text(rep)} zapytań/operacji" + (", typy: " + ", ".join(tags) if tags else
                                                                     (f" „{' '.join(untagged)}”" if untagged else ""))
            elif k == "grid" and ((ilines and any(len(x) > 1 for x in ilines)) or
                                  (not re.search(r"plansz|siatk|map[aęy]|pol[ae]\b|p[oó]l\b|kratk|labirynt|teren",
                                                 _low(desc + " " + tresc + " " + wej)))):
                role["role"] = "lines"
                role["text"] = f"{_rep_text(rep)} wierszy tekstu" + (" (ze spacjami → getline)" if ilines and
                                                                     any(len(x) > 1 for x in ilines) else "")
            elif k == "grid" or (k == "rows" and it.get("length")) or \
                    (k == "rows" and ints and ncols and ncols >= 2 and re.search(r"plansz|macierz|siatk|tablic\w* dwuwym|"
                                                                                  r"prostok[aą]tn\w* tablic", _low(full))
                     and not _GRAPH_CUE.search(dlow)):
                role["role"] = "grid"
                flags["grid"] = True
                alph = None
                if ilines and k == "grid":
                    chars = sorted({c for x in ilines for t in x for c in t})
                    if len(chars) <= 6:
                        alph = " ".join(f"„{c}”" for c in chars)
                cols = it.get("length") or (ncols if k == "rows" else None)
                kind_txt = "plansza znaków" if k == "grid" else "plansza liczb"
                role["text"] = f"{kind_txt}: {_rep_text(rep)} wierszy" + (f" × {cols}" if cols else "") + \
                    (f" (znaki {alph})" if alph else "")
            elif k == "lines":
                role["role"] = "lines"
                role["text"] = f"{_rep_text(rep)} napisów, po jednym w wierszu"
            elif k == "rows" and ints and ncols in (2, 3) and re.fullmatch(r"[A-Za-z]\w*\s*-\s*1", rep.replace(" ", "")) \
                    and (_GRAPH_CUE.search(dlow) or _TREE_CUE.search(_low(full)) or _GRAPH_CUE.search(_low(full))):
                nv = env.get(re.sub(r"\s*-\s*1$", "", rep).strip())
                verified = bool(ilines) and isinstance(nv, int) and _tree_check(ilines, nv)
                parent_form = bool(_PARENT_CUE.search(desc)) and not re.search(r"kraw[eę]d", dlow)
                if parent_form:
                    role["role"] = "parents"
                    flags["tree"] = flags["rooted"] = True
                    w = ncols == 3 or (ncols == 2 and _WEIGHT_CUE.search(dlow))
                    role["text"] = f"{rep} wierszy: rodzic (i waga) kolejnego wierzchołka → drzewo ukorzenione" if w \
                        else f"{rep} wierszy: rodzic kolejnego wierzchołka → drzewo ukorzenione"
                    flags["weighted"] = flags["weighted"] or bool(w)
                    notes.append(f"{rep} wierszy z rodzicem → drzewo ukorzenione w wierzchołku 1.")
                elif verified or ncols in (2, 3):
                    role["role"] = "tree_edges"
                    flags["tree"] = True
                    weighted = ncols == 3
                    flags["weighted"] = flags["weighted"] or weighted
                    nm = re.sub(r"\s*-\s*1$", "", rep).strip()
                    role["text"] = f"{rep} krawędzi „{' '.join(role['names'] or ['a', 'b'][:2])}”" + \
                        (" z wagą" if weighted else "")
                    notes.append(f"{nm}−1 krawędzi" + (" (spójne na przykładzie)" if verified else "") + " → drzewo.")
            elif k == "rows" and ints and ncols in (2, 3) and (_GRAPH_CUE.search(dlow) or
                                                               (_GRAPH_CUE.search(_low(tresc + " " + wej))
                                                                and not re.search(r"przedzia|odcin|punkt|wsp[oó][lł]rz",
                                                                                  dlow))) \
                    and _endpoints_var(ilines, vert_var, first_vals) is not False:
                vv = _endpoints_var(ilines, vert_var, first_vals)
                if isinstance(vv, str):
                    vert_var = vv
                role["role"] = "edges"
                flags["graph"] = True
                weighted = ncols == 3 and bool(_WEIGHT_CUE.search(dlow + " " + _low(tresc)))
                flags["weighted"] = flags["weighted"] or weighted
                flags["directed"] = directed
                role["text"] = f"{_rep_text(rep)} krawędzi „{' '.join((role['names'] or ['a', 'b', 'w'])[:ncols])}”" + \
                    (" z wagą" if weighted else "") + (" (skierowane)" if directed else "")
                multi = _multi_edges(ilines)
                if multi:
                    notes.append("W przykładzie ta sama para wierzchołków występuje kilka razy → krawędzie wielokrotne.")
            elif k == "rows":
                cols = role["names"] or it.get("vars") or []
                if ncols == 2 and re.search(r"przedzia|odcin|\[\s*[a-z]_?i?\s*,\s*[a-z]_?i?\s*\]|od\s+[a-z]\w*\s+do\s+[a-z]",
                                            dlow):
                    role["role"] = "intervals"
                    role["text"] = f"{_rep_text(rep)} przedziałów [{', '.join(cols[:2]) or 'a, b'}]"
                elif ncols in (2, 3) and re.search(r"punkt\w*|wsp[oó][lł]rz[eę]d\w*", dlow + " " + _low(tresc)):
                    role["role"] = "points"
                    role["text"] = f"{_rep_text(rep)} punktów ({', '.join(cols[:ncols]) or 'x, y'})"
                elif ncols == 1:
                    role["role"] = "column"
                    role["text"] = f"{_rep_text(rep)} liczb, po jednej w wierszu" + (f" ({cols[0]})" if cols else "")
                else:
                    role["role"] = "rows"
                    role["text"] = f"{_rep_text(rep)} wierszy po {ncols or len(cols) or '?'} wartości" + \
                        (f": {', '.join(cols)}" if cols else "")
        else:
            role["text"] = it.get("desc", "")
        roles.append(role)

    rl = [("scalars" if r["role"] == "rows" and re.fullmatch(r"\d{1,2}", str(r.get("repeat") or "")) else r["role"])
          for r in roles]
    if any(r in ("tree_edges", "parents") for r in rl):
        kind = "tree"
    elif "edges" in rl:
        kind = "graph"
    elif "grid" in rl:
        kind = "grid"
    elif any(r in ("string", "lines") for r in rl) and not any(r in ("array", "column", "rows") for r in rl):
        kind = "strings"
    elif any(r in ("array", "column", "rows", "intervals", "points") for r in rl):
        kind = "pairs" if (any(r in ("intervals", "points") for r in rl) and "array" not in rl) else "array"
    elif rl and "queries" in rl and all(r in ("scalars", "queries") for r in rl):
        kind = "queries"
    elif rl and all(r in ("scalars", "multitest", "tests", "queries") for r in rl):
        kind = "values"
    elif any(r == "string" for r in rl):
        kind = "strings"
    else:
        kind = "unknown"
    if kind == "tree" and re.search(r"korze[nń]\w*\s+(?:\w+\s+){0,3}?(?:w\s+)?(?:wierzcho[lł]k\w*\s+)?(?:numer\s+|nr\s+)?1\b|"
                                    r"ukorzenion\w*\s+w\s+(?:wierzcho[lł]k\w*\s+)?(?:numer\s+)?1\b|korzeniem\s+jest\s+"
                                    r"(?:wierzcho[lł]ek\s+)?(?:numer\s+)?1\b", _low(full)):
        flags["rooted"] = True
        notes.append("Korzeń: wierzchołek 1.")
    if kind == "graph" and not flags["directed"] and directed:
        flags["directed"] = True
    if kind == "graph":
        if re.search(r"nie tworz\w* cykl|acykliczn|\bdag\b", _low(full)):
            notes.append("Graf skierowany bez cykli (DAG) → porządek topologiczny.")
        if flags["directed"]:
            notes.append("Krawędzie skierowane: a → b nie oznacza b → a.")
    label = _struct_label(kind, flags, roles)
    return {"kind": kind, "label": label, "flags": flags, "items": roles, "notes": list(dict.fromkeys(notes)),
            "queries": queries, "query_count": query_count, "query_word": query_word, "vertex_var": vert_var if kind in ("tree", "graph") else None,
            "verified": bool(chk)}


def _endpoints_var(ilines, prefer, first_vals: dict):
    """Która zmienna z pierwszego wiersza ogranicza końce krawędzi (liczba wierzchołków)? True = nie da się
    sprawdzić, False = żadna nie pasuje, str = nazwa zmiennej."""
    if ilines is None:
        return True
    try:
        mx = max(max(int(r[0]), int(r[1])) for r in ilines)
        mn = min(min(int(r[0]), int(r[1])) for r in ilines)
    except (ValueError, IndexError):
        return False
    if mn < 0:
        return False
    order = ([prefer] if prefer in first_vals else []) + [v for v in first_vals if v != prefer]
    for v in order:
        val = first_vals.get(v)
        if isinstance(val, int) and mx <= val:
            return v
    return False if any(isinstance(x, int) for x in first_vals.values()) else True


def _multi_edges(ilines) -> bool:
    if not ilines:
        return False
    seen = set()
    for r in ilines:
        if len(r) < 2:
            continue
        e = tuple(sorted((r[0], r[1])))
        if e in seen:
            return True
        seen.add(e)
    return False


def _struct_label(kind: str, flags: dict, roles: list[dict]) -> str:
    if kind == "tree":
        lab = "drzewo" + (" ukorzenione" if flags.get("rooted") else "") + (" z wagami" if flags.get("weighted") else "")
    elif kind == "graph":
        lab = "graf " + ("skierowany" if flags.get("directed") else "nieskierowany") + \
            (", ważony" if flags.get("weighted") else "")
    elif kind == "grid":
        lab = "plansza (siatka)"
    elif kind == "strings":
        n = sum(1 for r in roles if r["role"] in ("string", "lines"))
        lab = "napisy" if n > 1 or any(r["role"] == "lines" for r in roles) else "napis"
    elif kind == "array":
        n = sum(1 for r in roles if r["role"] in ("array", "column"))
        lab = "ciągi liczb" if n > 1 else ("tabela wierszy" if any(r["role"] == "rows" for r in roles) and n == 0
                                           else "ciąg liczb")
    elif kind == "pairs":
        lab = "przedziały" if any(r["role"] == "intervals" for r in roles) else "punkty"
    elif kind == "values":
        lab = "kilka liczb (wartości)"
    elif kind == "queries":
        lab = "ciąg operacji / zapytań"
    else:
        lab = "nierozpoznana"
    if flags.get("queries") and kind != "queries":
        lab += " + zapytania"
    if flags.get("multitest"):
        lab = "wiele zestawów: " + lab
    return lab


# --------------------------------------------------------------------------------------------- typy zapytań
def _tag_rx(tag: str) -> str:
    e = re.escape(tag)
    return r"(?<![\w" + _PL + r"])" + e + r"(?![\w" + _PL + r"])"


def _bullets(text: str) -> list[str]:
    parts = re.split(r"\s*•\s*|\s+(?=\d\.\s)|\n+", text or "")
    return [x.strip() for x in parts if x and x.strip()]


def _desc_params(desc: str) -> list[str]:
    """Parametry wiersza z opisu: „dwóch liczb a i b (…) oraz litery R lub M” → [a, b, R/M]."""
    d = re.sub(r"\([^)]*\)", " ", desc or "")
    names: list[str] = []
    for m in re.finditer(r"(?:liczb\w*|warto[sś]\w*)(?:\s+ca[lł]kowit\w+)?\s*[:,]?\s+(" + ST._VAR + r"(?:\s*(?:,|\bi\b|\boraz\b)\s*"
                         + ST._VAR + r")*)(?![\w" + _PL + r"])", d):
        parts = [x.strip() for x in re.split(r"\s*(?:,|\bi\b|\boraz\b)\s*", m.group(1)) if x.strip()]
        for x in parts:
            if x == "i" or (x in ("a", "w", "z", "o", "u") and len(parts) == 1):
                continue
            v = ST.norm_var(x)
            if v not in names:
                names.append(v)
    m = re.search(r"(?:liter\w*|znak\w*)\s+[„\"'‘]?([A-Za-z])[”\"'’]?\s+(?:lub|albo)\s+[„\"'‘]?([A-Za-z])[”\"'’]?", d)
    if m:
        names.append(f"{m.group(1)}/{m.group(2)}")
    return names


def _query_types(p: dict, ilines, tags, it: dict, desc: str, idx: int = -1) -> list[dict]:
    """Typy zapytań: [{tag, params, meaning, prints, count}] — z przykładu (znaczniki, liczba parametrów)
    i z opisu w treści („„+ x” – dodaje x”, „Zapytanie typu Q zawiera liczbę x, oznaczającą …”)."""
    sec = p["sections"]
    text = " ".join(sec.get(k, "") for k in ("tresc", "wejscie"))
    wyj = sec.get("wyjscie", "")
    out: list[dict] = []
    if not tags:
        # zapytania bez znacznika: jeden typ, parametry z opisu („dwie liczby a i b … oraz litera R lub M”)
        names = _desc_params(desc) or _item_names(it, desc) or []
        mean = _clip(_array_meaning(desc), 70)
        n = Counter(len(x) for x in ilines).most_common(1)[0][0] if ilines else len(names)
        if ilines is not None or names:
            params = names[:n] if len(names) >= n else names + [f"x{i + 1}" for i in range(len(names), n or 0)]
            out.append({"tag": None, "params": params, "meaning": mean, "param_meaning": "", "prints": None,
                        "count": len(ilines) if ilines else None})
        return out
    counts = Counter(x[0] for x in ilines if x)
    nparams = {t: Counter(len(x) - 1 for x in ilines if x and x[0] == t).most_common(1)[0][0] for t in tags}
    bullets = _bullets(sec.get("tresc", "")) + _bullets(sec.get("wejscie", ""))
    sents = _sentences(text)
    for t in tags:
        params: list[str] = []
        meaning = ""
        rx = _tag_rx(t)
        single_word = len(t) == 1 and t.isalpha() and t.islower()
        # 1) punkt listy zaczynający się od znacznika: „„+ x” – dodaje x do zbioru”, „push x, która …”
        for b in bullets:
            m = re.match(r"(?:operacj\w*|zapytani\w*|polecen\w*)?\s*[„\"'‘]?" + re.escape(t) +
                         r"(?P<params>(?:\s+[A-Za-z](?:_?\w)?)*)\s*[”\"'’]?\s*(?:[–—:-]|,\s*(?:kt[oó]r\w+|co)\s+)\s*(?P<m>.+)$",
                         b, re.I if not single_word else 0)
            if m:
                params = m.group("params").split()
                meaning = m.group("m")
                break
        # 2) zdanie o typie: „Zapytanie typu Q zawiera też liczbę x (…), oznaczającą numer lampy”
        typ_rx = r"(?:typu|liter[aąy]|znak\w*|rodzaju|operacj\w*|zapytani\w*|polecen\w*|od)\s+[„\"'‘]?" + re.escape(t) + \
            r"[”\"'’]?(?![\w" + _PL + r"])"
        pmean = ""
        for s in sents:
            if not re.search(typ_rx, s):
                continue
            mp = re.search(typ_rx + r"[^.]*?\bzawiera\w*\s+(?:te[zż]\s+)?(?:jedn\w+\s+|dwie\s+|trzy\s+)?(?:liczb\w+\s+"
                           r"(?:ca[lł]kowit\w+\s+)?)?(?P<ps>[A-Za-z](?:_?\w)?(?:\s*(?:,|i|oraz)\s*[A-Za-z](?:_?\w)?)*)"
                           r"(?:\s*\([^)]*\))?\s*,?\s*(?:oznaczaj\w+\s+(?P<pm>[^.(]+))?", s)
            if mp and not params:
                params = [x for x in re.split(r"\s*(?:,|\bi\b|\boraz\b)\s*", mp.group("ps")) if x]
                if mp.group("pm"):
                    pmean = mp.group("pm")
            clauses = re.split(r",\s+a\s+|;\s*", s)
            clause = next((c for c in clauses if re.search(_tag_rx(t), c)), s)
            mm = re.search(r"(?P<m>[A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż][^,;]*?)\s+(?:poprzedzon\w+\s+(?:jest\s+)?)?(?:liter[aą]|znakiem)\s+"
                           + re.escape(t) + r"(?![\w" + _PL + r"])", clause)
            if mm and not meaning:
                meaning = mm.group("m")
        if not meaning and pmean:
            meaning, pmean = pmean, ""
        if not params and nparams.get(t):
            params = [f"x{i + 1}" for i in range(nparams[t])] if nparams[t] > 1 else ["x"]
        meaning = re.split(r"(?<=[a-ząćęłńóśźż)])\.\s", meaning.strip())[0]
        meaning = _clip(re.sub(r"^(?:oznacza,?\s+(?:[zż]e\s+)?|powoduje\s+|kt[oó]r\w+\s+powoduje\s+)", "",
                               meaning.strip(" ,.;")), 80)
        meaning = meaning[:1].lower() + meaning[1:] if meaning[:2].istitle() else meaning
        out.append({"tag": t, "params": params[:nparams.get(t, len(params))] if nparams.get(t) is not None else params,
                    "meaning": meaning, "param_meaning": _clip(pmean.strip(" ,.;"), 60), "prints": None,
                    "count": counts.get(t, 0)})
    # które zapytania coś wypisują: znaczniki w treści sekcji Wyjście, potwierdzone liczbą linii wyjścia przykładów
    wtags = set()
    for q in out:
        t = q["tag"]
        if len(t) == 1 and t.isalpha() and t.islower():
            if re.search(r"(?:typu|operacj\w*|zapyta\w*|polece\w*)\s+(?:[^\s.]{1,8}\s*(?:,|lub|oraz|i)\s*){0,4}[„\"'‘]?"
                         + re.escape(t) + r"[”\"'’]?(?![\w" + _PL + r"])", wyj):
                wtags.add(t)
        elif re.search(_tag_rx(t), wyj):
            wtags.add(t)
    samples = [s for s in p.get("samples") or [] if s.get("input") and s.get("source") != "ocen" and not s.get("big")]

    def fits(sel: set) -> bool:
        ok_any = False
        for s in samples:
            res = _check(display_format(p), s["input"])
            if not res:
                continue
            lns = ST._tok_lines(s["input"])
            span = (res.get("spans") or {}).get(idx)
            qlines = lns[span[0]:span[1]] if span else [x for x in lns if x and x[0] in counts]
            outl = [x for x in s.get("output", "").split("\n") if x.strip() != ""]
            if sum(1 for x in qlines if x and x[0] in sel) != len(outl):
                return False
            ok_any = True
        return ok_any
    sel = None
    if wtags and fits(wtags):
        sel = wtags
    else:
        import itertools
        found = [set(c) for r in range(1, len(out) + 1) for c in itertools.combinations([q["tag"] for q in out], r)
                 if fits(set(c))]
        if len(found) == 1:
            sel = found[0]
    if sel is not None:
        for q in out:
            q["prints"] = q["tag"] in sel
    return out


# ============================================================================================ przykład rozpisany
def _show_list(toks: list[str], k: int = 10) -> str:
    return ", ".join(toks[:k]) + (", …" if len(toks) > k else "")


def walkthrough(p: dict, struct: dict, limit: int = 2) -> list[dict]:
    """Przykłady rozpisane na zmienne wg formatu wejścia (tylko gdy przykład przechodzi check_format — nigdy nie
    pokazujemy zgadywanego przypisania)."""
    if not p.get("input_format"):
        return []
    dfmt = display_format(p)
    fmt = dfmt
    roles = {r["index"]: r for r in struct.get("items") or []}
    out = []
    samples = [s for s in p.get("samples") or [] if s.get("input") and s.get("source") != "ocen" and not s.get("big")]
    for si, s in enumerate(samples[:limit]):
        res = _check(fmt, s["input"])
        if not res:
            continue            # niepotwierdzone („nieznana liczba powtórzeń”) — nie zgadujemy przypisania
        lines = ST._tok_lines(s["input"])
        spans = res.get("spans") or {}
        rows: list[str] = []
        outl = [x for x in s.get("output", "").split("\n") if x.strip() != ""]
        out_used = False
        multi = any(it.get("kind") == "multitest" for it in fmt)
        if multi:
            # wiele zestawów: pokaż liczbę zestawów i pierwszy zestaw
            mt = next(i for i, it in enumerate(fmt) if it.get("kind") == "multitest")
            sp = spans.get(mt)
            if not sp:
                continue
            tcount = lines[sp[0]][0]
            rows.append(f"{(dfmt[mt].get('vars') or ['t'])[0]} = {tcount} (zestawy)")
            sub = [dict(x, per_test=False) for x in fmt if x.get("per_test")]
            rest = "\n".join(" ".join(x) for x in lines[sp[0] + 1:])
            r2 = ST.check_format(sub, rest)
            if not ((r2.get("ok") and not r2.get("reason")) or str(r2.get("reason") or "").startswith("zostało")):
                continue
            l2 = ST._tok_lines(rest)
            sub_roles = {}
            for j, x in enumerate(sub):
                orig = next((i for i, y in enumerate(fmt) if y.get("per_test") and y.get("line") == x.get("line")), None)
                sub_roles[j] = roles.get(orig, {"role": x.get("kind"), "vars": x.get("vars") or []})
            sp2 = r2.get("spans") or {}
            if not sp2:
                continue
            first = []
            for j, x in enumerate(sub):
                if j in sp2:
                    first += _render_item(x, sub_roles[j], l2[sp2[j][0]:sp2[j][1]], None)[0]
            if first:
                rows.append("zestaw 1: " + "; ".join(first))
        else:
            for i, it in enumerate(fmt):
                if i not in spans:
                    continue
                a, b = spans[i]
                rendered, used = _render_item(dfmt[i], roles.get(i, {}), lines[a:b], outl if not out_used else None)
                out_used = out_used or used
                rows += rendered
        if not rows:
            continue
        o = None
        if not out_used:
            if len(outl) == 1:
                o = f"wynik = {outl[0]}" if len(outl[0]) <= 60 else f"wynik: {outl[0][:60]}…"
            elif outl:
                o = f"wyjście ({len(outl)} wierszy): " + " | ".join(x[:30] for x in outl[:6]) + (" | …" if len(outl) > 6 else "")
        expl = s.get("explanation") or (p["sections"].get("wyjasnienie") if si == 0 else None)
        out.append({"name": s.get("name") or f"przykład {si + 1}", "input": rows, "output": o,
                    "explanation": _clip(expl, 400) if expl else None})
    return out


def _render_item(it: dict, role: dict, lns: list[list[str]], outl: list[str] | None) -> tuple[list[str], bool]:
    """Jeden element formatu → linie opisu; drugi element = czy zużyto wyjście (zapytania → odpowiedzi)."""
    k = it.get("kind")
    r = role.get("role", k)
    names = role.get("names") or []
    vs = it.get("vars") or []
    rep = str(it.get("repeat") or "")
    if rep.startswith("until:") and lns and lns[-1] == [rep.split(":", 1)[1]]:
        lns = lns[:-1]
    toks = [t for x in lns for t in x]
    if r in ("scalars",) or k == "scalars":
        if len(vs) == len(toks):
            parts = []
            for v, t in zip(vs, toks):
                if v in names or role.get("renamed"):
                    parts.append(f"{v} = {t}")
                else:
                    m = _clip(re.sub(r"^(?:liczb[eęa]|ilo[sś][cć])\s+", "liczba ", _array_meaning(it.get("desc") or "")), 40)
                    parts.append(f"{m} = {t}" if m and len(vs) == 1 else t)
            return [", ".join(parts)], False
        return [" ".join(toks)], False
    if r == "array":
        nm = ST.base_var(vs[0]) if (vs and names) else "ciąg"
        return [f"{nm} = [{_show_list(toks, 12)}]" + (f" ({len(toks)} liczb)" if len(toks) > 12 else "")], False
    if r == "parents":
        if k == "array":
            return [f"rodzice: {_show_list(toks, 12)}"], False
        return ["rodzic wierzchołka (i waga): " + _show_list([f"{x[0]}" + (f" ({x[1]})" if len(x) > 1 else "")
                                                              for x in lns], 8)], False
    if r == "string":
        t = " ".join(toks)
        nm = vs[0] if (vs and names) else "napis"
        return [f"{nm} = „{t[:50]}{'…' if len(t) > 50 else ''}”"], False
    if r in ("tree_edges", "edges"):
        arrow = "→" if role.get("directed") or (r == "edges" and "skierowane" in role.get("text", "")) else "–"
        es = [f"{x[0]}{arrow}{x[1]}" + (f" ({x[2]})" if len(x) > 2 else "") for x in lns]
        lab = "krawędzie drzewa" if r == "tree_edges" else "krawędzie"
        return [f"{lab} ({len(es)}): {_show_list(es, 8)}"], False
    if r == "grid":
        show = [" ".join(x) for x in lns[:3]]
        return [f"plansza {len(lns)} wierszy: " + " / ".join(z[:24] for z in show) + (" / …" if len(lns) > 3 else "")], False
    if r == "lines":
        return [f"napisy ({len(lns)}): " + _show_list([f"„{' '.join(x)[:20]}”" for x in lns], 4)], False
    if r == "queries":
        qt = {q["tag"]: q for q in role.get("query_types") or [] if q.get("tag")}
        tagged = bool(qt)
        printing = [x for x in lns if x and tagged and qt.get(x[0], {}).get("prints")]
        if outl is not None and tagged and all(q.get("prints") is not None for q in qt.values()) and len(printing) == len(outl):
            it_out = iter(outl)
            items = [" ".join(x) + (f" → {next(it_out)}" if qt.get(x[0], {}).get("prints") else "") for x in lns]
            return [f"zapytania ({len(lns)}): {_show_list(items, 8)}"], True
        if outl is not None and not tagged and len(outl) == len(lns):
            items = [("(" + ", ".join(x) + ")" if len(x) > 1 else x[0]) + f" → {o}" for x, o in zip(lns, outl)]
            return [f"zapytania ({len(lns)}): {_show_list(items, 8)}"], True
        items = [" ".join(x) for x in lns]
        return [f"zapytania ({len(lns)}): {_show_list(items, 8)}"], False
    if r in ("intervals", "points", "rows", "tests", "column"):
        if r == "column":
            nm = vs[0] if (vs and names) else None
            return [(f"{ST.base_var(nm)} = " if nm else "liczby: ") + f"[{_show_list(toks, 12)}]"], False
        cols = names if len(names) == (len(lns[0]) if lns else 0) else []
        head = f"({', '.join(cols)})" if cols else ""
        items = ["[" + ", ".join(x) + "]" if r == "intervals" else "(" + ", ".join(x) + ")" for x in lns]
        lab = {"intervals": "przedziały", "points": "punkty", "tests": "zestawy", "rows": "wiersze"}[r]
        if outl is not None and r == "tests" and len(outl) == len(lns):
            items = [f"{a} → {o}" for a, o in zip(items, outl)]
            return [f"{lab} {head} ({len(lns)}): {_show_list(items, 6)}".replace("  ", " ")], True
        return [f"{lab} {head} ({len(lns)}): {_show_list(items, 8)}".replace("  ", " ")], False
    return [" ".join(toks)[:80]], False


# ============================================================================================ zmienne
_VAR_PAT = r"(?<![A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_])" + ST._VAR


def _meanings(parsed: dict) -> dict[str, str]:
    """Znaczenie zmiennych z opisu wejścia („n, m … oznaczające odpowiednio liczbę X i liczbę Y”, „n – ilość latarni”,
    „gdzie c_i oznacza kwotę…”, „liczba kasjerek n”)."""
    cached = parsed.get("_te_meanings")
    if cached is not None:
        return cached
    out: dict[str, str] = {}
    txt = parsed["sections"].get("wejscie", "") + " " + parsed["sections"].get("tresc", "")
    stop = r"(?:a|i|oraz|w|po|a po|kt[oó]r\w+|gdzie|natomiast|za[sś]|[zż]e|jest|s[aą]|to|na|z|do|od|dla|pooddzielan\w*|" \
           r"oddzielon\w*|rozdzielon\w*|podan\w*|zapisan\w*)\b"
    for sent in _sentences(txt):
        clean = re.sub(r"\([^)]*\)", " ", sent)
        clean = re.sub(r"\s+", " ", clean)
        # 0) n, q – odpowiednio długość ciągu i ilość zapytań
        for m in re.finditer(r"(" + _VAR_PAT + r"(?:\s*(?:,|i|oraz)\s*" + _VAR_PAT + r")+)\s*[–-]\s+odpowiednio\s+([^.;()]{5,160})", clean):
            names = [ST.norm_var(x) for x in re.split(r"\s*(?:,|\bi\b|\boraz\b)\s*", m.group(1)) if x.strip()]
            parts = [x.strip() for x in re.split(r",\s*|\s+i\s+|\s+oraz\s+", m.group(2)) if x.strip()]
            if len(parts) >= len(names):
                for nm, d0 in zip(names, parts):
                    out.setdefault(ST.base_var(nm), d0)
        # 1) n – ilość latarni
        for m in re.finditer(r"(?:^|[\s,:;])(" + _VAR_PAT + r")\s*[–-]\s+(?!" + stop + r")([a-ząćęłńóśźż][^,;.()–]{3,60})", clean):
            v = ST.norm_var(m.group(1))
            out.setdefault(ST.base_var(v), m.group(2).strip())
        # 2) gdzie c_i oznacza kwotę …  /  liczba a_i oznacza kolor …
        for m in re.finditer(r"(" + _VAR_PAT + r")\s+oznacza(?:j[aą])?\s+(?:odpowiednio\s+)?([^,;.()]{3,80})", clean):
            v = ST.norm_var(m.group(1))
            if ST.base_var(v) not in ST._POLISH_SINGLE or ST.base_var(v) in ("a",):
                out.setdefault(ST.base_var(v), m.group(2).strip())
        # 3) n, m … oznaczające (odpowiednio) liczbę X i liczbę Y  (także „liczby 2 ≤ n ≤ 10^5 i 1 ≤ m ≤ 2·10^5, oznaczające…”)
        clean3 = re.sub(r"-?\d[\d ^·]*\s*[≤<]\s*(?=[A-Za-z])", "", clean)
        clean3 = re.sub(r"(?<=[A-Za-z0-9])\s*[≤<]\s*-?\d[\d^·]*(?: \d{3})*", "", clean3)
        m = re.search(r"(" + _VAR_PAT + r"(?:\s*(?:,|i|oraz)\s*" + _VAR_PAT + r")*)\s*,?\s*(?:\s\S+){0,3}?\s*"
                      r"(?:oznaczaj[aą]c\w*|opisuj[aą]c\w*|okre[sś]laj[aą]c\w*|b[eę]d[aą]c\w*|reprezentuj[aą]c\w*)\s+"
                      r"(?:odpowiednio\s+|kolejno\s+)?([^.;()]{3,160})", clean3)
        if m:
            names = [ST.norm_var(x) for x in re.split(r"\s*(?:,|\bi\b|\boraz\b)\s*", m.group(1)) if x.strip()]
            names = [n for n in names if n not in ST._POLISH_SINGLE or n in ("a", "w", "z")]
            desc = m.group(2).strip()
            parts = [x.strip() for x in re.split(r",\s*|\s+i\s+|\s+oraz\s+", desc) if x.strip()]
            if len(names) > 1 and len(parts) >= len(names):
                lead = re.match(r"(liczb\w+|ilo[sś][cć]\w*)\s", parts[0])
                for j, (nm, d) in enumerate(zip(names, parts)):
                    if j and lead and not re.match(r"(?:liczb|ilo[sś]|d[lł]ugo|numer|warto|koszt|wysoko|szeroko|rozmiar)", d):
                        d = lead.group(1) + " " + d        # „liczbę skrzyżowań i dróg” → m: „liczbę dróg”
                    out.setdefault(ST.base_var(nm), d)
            elif len(names) == 1:
                out.setdefault(ST.base_var(names[0]), desc[:80])
        # 4) liczba kasjerek n / długość ciągu n
        for m in re.finditer(r"((?!liczb\w* (?:ca[lł]kowit|naturaln|rzeczywist|dodatni))"
                             r"(?:liczb\w+|d[lł]ugo[sś][cći]\w*|ilo[sś][cći]\w*|warto[sś][cći]\w*|koszt\w*|"
                             r"wysoko[sś][cć]\w*|szeroko[sś][cć]\w*|numer\w*)\s+[a-ząćęłńóśźż]{3,}(?:\s+[a-ząćęłńóśźż]{3,})?)"
                             r"\s+(" + _VAR_PAT + r")(?![\wąćęłńóśźż])", clean):
            v = ST.base_var(ST.norm_var(m.group(2)))
            if v in ST._POLISH_SINGLE and re.match(r"\s+[a-ząćęłńóśźż]{2,}", clean[m.end():]):
                continue            # „liczbę ryb w rozwidleniach” — „w” to przyimek
            out.setdefault(v, m.group(1))
    # 5) wartość po ograniczeniu: „n (1 ≤ n ≤ 2 000), rozmiar kwadratowej polany”
    for sent in _sentences(parsed["sections"].get("wejscie", "")):
        for m in re.finditer(r"(" + _VAR_PAT + r")\s*\([^)]*[≤<][^)]*\)\s*[,–-]\s*(?!" + stop + r")"
                             r"([a-ząćęłńóśźż][^,;.()]{3,60})", sent):
            out.setdefault(ST.base_var(ST.norm_var(m.group(1))), m.group(2).strip())
    res = {}
    for k, v in out.items():
        v = re.sub(r"\s+", " ", v).strip(" ,.:")
        v = re.sub(r"^(?:oznaczaj[aą]c\w*|opisuj[aą]c\w*|okre[sś]laj[aą]c\w*|b[eę]d[aą]c\w*|reprezentuj[aą]c\w*)\s+"
                   r"(?:odpowiednio\s+|kolejno\s+)?", "", v)
        if v and len(v) >= 3:
            res[k] = _clip(v, 70)
    parsed["_te_meanings"] = res
    return res


def _array_meaning(desc: str) -> str:
    """Krótki opis elementów z opisu wiersza: „… liczb oznaczających smaczność kolejnych posiłków” → „smaczność
    kolejnych posiłków”."""
    if not desc:
        return ""
    d = re.sub(r"\([^)]*\)", " ", desc)
    m = re.search(r"(?:oznaczaj[aą]c\w*|opisuj[aą]c\w*|reprezentuj[aą]c\w*|okre[sś]laj[aą]c\w*|b[eę]d[aą]c\w*)\s+"
                  r"(?:odpowiednio\s+|kolejno\s+)?([^.;]{3,90})", d)
    if not m:
        m = re.search(r"\s[–—-]\s+([a-ząćęłńóśźż][^.;]{3,80})", d)
    if not m:
        m = re.search(r"gdzie\s+\S+\s+oznacza\s+([^.;]{3,80})", d)
    return re.sub(r"\s+", " ", m.group(1)).strip(" ,.") if m else ""


def _elem_range(desc: str) -> tuple:
    """Zakres elementów z opisu: „z zakresu [0, 10^9]”, „należących do przedziału [0; 10^9]”, „od 1 do 100”."""
    V = ST.VALUE_RE.pattern
    m = re.search(r"(?:z\s+(?:zakresu|przedzia[lł]u)|nale[zż][aą]c\w*\s+do\s+(?:zakresu|przedzia[lł]u))\s*[\[(]\s*(-?\s*" + V +
                  r")\s*[,;]\s*(-?\s*" + V + r")\s*[\])]", desc or "")
    if not m:
        m = re.search(r"z\s+zakresu\s+od\s+(-?\s*" + V + r")\s+do\s+(-?\s*" + V + r")", desc or "")
    if m:
        a, b = ST.parse_value(m.group(1).replace(" ", "") if "^" in m.group(1) else m.group(1)), ST.parse_value(m.group(2))
        if a is not None and b is not None:
            return a, b
    m = re.search(r"nie\s+(?:przekraczaj\w*|wi[eę]ksz\w*\s+(?:ni[zż]|od))\s+(" + V + r")", desc or "")
    if m:
        return None, ST.parse_value(m.group(1))
    return None, None


def _structural_sizes(p: dict) -> list[str]:
    fmt, cons = p.get("input_format") or [], p.get("constraints") or {}
    try:
        return ST._structural_sizes(fmt, cons)
    except Exception:  # noqa: BLE001
        return []


def _symbolic_max(name: str, rel: list, sizes: set, sb: dict) -> str | None:
    """„1 ≤ a ≤ b ≤ n” → górna granica a to (przez b) n — pokazujemy nazwę rozmiaru zamiast pustego pola."""
    seen = {name}
    cur = name
    for _ in range(4):
        nxt = [r["b"] for r in rel or [] if r.get("type") == "le" and ST.base_var(str(r.get("a"))) == cur]
        nxt = [ST.base_var(str(x)) for x in nxt if x]
        if not nxt:
            return None
        for x in nxt:
            if x in sizes or isinstance((sb.get(x) or {}).get("max"), (int, float)):
                return x
        cur = next((x for x in nxt if x not in seen), None)
        if cur is None:
            return None
        seen.add(cur)
    return None


def variables_table(parsed: dict, struct: dict | None = None) -> list[dict]:
    cons = parsed.get("constraints") or {}
    fmt = display_format(parsed)
    rel = parsed.get("relations") or []
    sb = ST.size_bounds(cons)
    mean = _meanings(parsed)
    struct = struct or structure(parsed)
    roles = {r["index"]: r for r in struct.get("items") or []}
    text = " ".join(parsed["sections"].get(k, "") for k in ("tresc", "wejscie", "wyjscie"))
    rows: list[dict] = []
    seen = set()
    sizes = set(_structural_sizes(parsed))
    for r in roles.values():
        for key in ("repeat", "length"):
            x = re.sub(r"\s*[-+*·].*$", "", str(r.get(key) or ""))
            if re.fullmatch(r"[A-Za-z]\w?", x):
                sizes.add(ST.base_var(x))
    for it in fmt[:3]:
        if it.get("kind") in ("scalars", "multitest"):
            for v in it.get("vars") or []:
                if ST.base_var(v) in ("q", "Q", "t", "T", "z") and ST.base_var(v) in sb:
                    sizes.add(ST.base_var(v))
    if struct.get("vertex_var"):
        sizes.add(struct["vertex_var"])
    qparams: dict[str, dict] = {}
    for q in struct.get("queries") or []:
        for prm in q.get("params") or []:
            if "/" not in prm and not re.fullmatch(r"x\d", prm):
                qparams.setdefault(ST.base_var(prm), q)

    def in_text(b: str) -> bool:
        return bool(re.search(r"(?<![\w" + _PL + r"])" + re.escape(b) + r"(?:_?(?:[0-9]{1,2}|[ijkmn])|_\{[^}]*\})?(?![\w" + _PL + r"(])",
                              text))

    # znaczenie z roli w strukturze, gdy treść go nie podaje wprost („m” = liczba krawędzi, bo tyle jest wierszy krawędzi)
    role_meaning: dict[str, str] = {}
    _RM = {"queries": f"liczba {struct.get('query_word') or 'zapytań'}", "edges": "liczba krawędzi",
           "tree_edges": "liczba wierzchołków (krawędzi jest o 1 mniej)", "tests": "liczba zestawów danych",
           "multitest": "liczba zestawów danych", "grid": "liczba wierszy planszy", "lines": "liczba napisów",
           "intervals": "liczba przedziałów", "points": "liczba punktów", "column": "liczba elementów",
           "rows": "liczba wierszy danych"}
    for r in roles.values():
        rp = re.sub(r"\s*[-+*·].*$", "", str(r.get("repeat") or ""))
        if rp and r["role"] in _RM:
            role_meaning.setdefault(rp, _RM[r["role"]])
        if r["role"] == "parents" and r.get("kind") == "rows":
            vs = [ST.base_var(v) for v in (r.get("names") or r.get("vars") or [])]
            if vs:
                role_meaning.setdefault(vs[0], "rodzic (numer wierzchołka)")
            if len(vs) > 1:
                role_meaning.setdefault(vs[1], "waga krawędzi do rodzica")
        if r["role"] in ("edges", "tree_edges"):
            vs = [ST.base_var(v) for v in (r.get("names") or r.get("vars") or [])]
            for v in vs[:2]:
                role_meaning.setdefault(v, "koniec krawędzi (numer wierzchołka)")
            if len(vs) > 2:
                role_meaning.setdefault(vs[2], "waga krawędzi")
        L = str(r.get("length") or "")
        if re.fullmatch(r"[A-Za-z]", L):
            role_meaning.setdefault(L, {"array": "długość ciągu", "string": "długość napisu",
                                        "grid": "liczba kolumn planszy"}.get(r["role"], "długość"))
        if r["role"] == "multitest":
            for v in r.get("vars") or []:
                role_meaning.setdefault(ST.base_var(v), _RM["multitest"])

    def add(name, src_item=None, role_hint=None, desc=""):
        b = ST.base_var(name) if not name.startswith("_") else name
        if b in seen:
            return
        auto = src_item is not None and name not in (roles.get(src_item.get("_i"), {}).get("names") or []) \
            and not name.startswith("_")
        if auto:
            # nazwa nadana przez parser (w opisie tego wiersza jej nie ma): ciąg bez nazwy → „(ciąg)”, reszta pomijana
            if role_hint in ("array", "column"):
                lo, hi = _elem_range(desc)
                if "(ciąg)" not in seen:
                    seen.add("(ciąg)")
                    rows.append({"name": "(ciąg)", "base": "(ciąg)", "meaning": _clip(_array_meaning(desc), 70),
                                 "min": lo, "max": hi, "min_fmt": ST.fmt_value(lo) if lo is not None else "",
                                 "max_fmt": ST.fmt_value(hi) if hi is not None else "",
                                 "type": "long long" if isinstance(hi, (int, float)) and abs(hi) > INT_MAX else "int",
                                 "role": "tablica", "kind": "array", "digits": None, "raw": "", "guessed": False,
                                 "auto_name": True})
                return
            if not (role_hint == "scalars" and b in cons):
                return          # nazwa wymyślona przez parser (np. wzory-obrazki w HTML) — opisuje ją sekcja Struktura
        if src_item is None and name in ("W", "Z", "O", "I", "A", "U", "N", "S") and \
                not re.search(r"(?<![\w" + _PL + r"])" + name + r"\s*[≤≥<>=,)]", text):
            return          # „W każdym z nich … nie większej niż 5000” — „W” to przyimek z początku zdania
        raw_c = str((cons.get(name) or {}).get("raw") or "")
        if src_item is None and name.startswith("_") and (cons.get(name) or {}).get("max") is None:
            return          # „co najmniej 25 lat przed …” — sama dolna granica bez kontekstu
        if src_item is None and name in ("i", "j") and re.search(r"[A-Za-z]_?\{?i,\s*j", text):
            return          # „c_{i,j}” — to indeksy, nie zmienne
        if name.startswith("_") and re.search(r"wypis|wyj[sś]|%|przypadk\w* testow|test\w* wart", raw_c, re.I):
            return          # ograniczenie rozmiaru WYJŚCIA albo opis podzadań („w 20% przypadków…”)
        if name.startswith("_") and re.search(r"[lł][aą]czn\w* d[lł]ugo[sś]|sum\w* d[lł]ugo[sś]", raw_c, re.I):
            c0 = cons.get(name) or {}
            seen.add(name)
            rows.append({"name": "Σ|s|", "base": name, "meaning": "łączna długość wszystkich napisów",
                         "min": c0.get("min"), "max": c0.get("max"), "min_fmt": ST.fmt_value(c0["min"]) if c0.get("min") is not None else "",
                         "max_fmt": ST.fmt_value(c0["max"]) if c0.get("max") is not None else "", "symbolic_max": None,
                         "type": "int", "role": "rozmiar", "kind": "size", "digits": None, "raw": raw_c, "guessed": False,
                         "auto_name": False})
            return
        seen.add(b)
        c = cons.get(name) or cons.get(b) or next((v for k, v in cons.items() if ST.base_var(k) == b), None) or {}
        mn, mx = c.get("min"), c.get("max")
        if role_hint in ("array", "column") and mn is None and mx is None:
            mn, mx = _elem_range(desc)
        sym = _symbolic_max(b, rel, sizes, sb) if mx is None and not c.get("bignum") else None
        t = "int"
        if any(isinstance(x, float) and not float(x).is_integer() for x in (mn, mx) if x is not None):
            t = "double"
        elif any(isinstance(x, (int, float)) and abs(x) > INT_MAX for x in (mn, mx) if x is not None):
            t = "long long"
        if src_item is not None and src_item.get("kind") in ("string", "grid", "lines"):
            t = "string"
        if c.get("bignum"):
            t = "string (liczba)"
        if b in sizes:
            role, kind = "rozmiar", "size"
        elif role_hint in ("array", "column"):
            role, kind = "tablica", "array"
        elif b in qparams or role_hint == "queries":
            role, kind = "parametr zapytania", "param"
        elif role_hint in ("array", "column", "rows", "parents", "tree_edges", "edges", "intervals", "points", "tests"):
            role, kind = ("tablica", "array") if role_hint in ("array", "column") else ("w wierszach", "array")
        elif t == "string":
            role, kind = "napis", "string"
        else:
            role, kind = "wartość", "value"
        meaning = mean.get(b) or role_meaning.get(b, "")
        if not meaning and role_hint in ("array", "column"):
            meaning = _clip(_array_meaning(desc), 70)
        if b in qparams and not meaning:
            q = qparams[b]
            meaning = q.get("param_meaning") or (f"parametr zapytania {q['tag']}" if q.get("tag") else "parametr zapytania")
        disp = name if not name.startswith("_") else "wartości"
        if kind == "array" and role_hint in ("array", "column") and "_" in disp and not re.search(r"_[ijk]$", disp):
            disp = b + "_i"
        rows.append({"name": disp, "base": b, "meaning": meaning,
                     "min": mn, "max": mx, "min_fmt": ST.fmt_value(mn) if mn is not None else "",
                     "max_fmt": ST.fmt_value(mx) if mx is not None else (ST.fmt_big(c) if c.get("bignum") else (sym or "")),
                     "symbolic_max": sym, "type": t, "role": role, "kind": kind, "digits": c.get("max_digits"),
                     "raw": c.get("raw", ""), "guessed": bool(c.get("guessed_power")), "auto_name": bool(auto)})

    for i, it in enumerate(fmt):
        it = dict(it, _i=i)
        r = roles.get(i, {})
        if r.get("role") == "queries":
            continue                    # parametry zapytań opisuje tabela zapytań (i ograniczenia poniżej)
        for v in it.get("vars") or []:
            if v == "plansza" or (re.fullmatch(r"x\d*", v) and v not in cons):
                continue
            add(v, it, r.get("role"), it.get("desc") or "")
    for k in cons:
        if "·" in k or "+" in k or k.startswith("|"):
            continue
        add(k, None, "queries" if ST.base_var(k) in qparams else None)
    for b in qparams:
        if b not in seen and in_text(b):
            add(b, None, "queries")
    return rows


def describe_format(parsed: dict, struct: dict | None = None) -> list[dict]:
    """Opis formatu wejścia linijka po linijce (po polsku) — z rolą rozpoznaną w strukturze."""
    roles = {r["index"]: r for r in (struct or {}).get("items") or []}
    out = []
    for i, it in enumerate(parsed.get("input_format") or []):
        k = it.get("kind")
        vs = it.get("vars") or []
        line = it.get("line", "?")
        rep = it.get("repeat")
        r = roles.get(i)
        if r and r.get("text") and r.get("role") not in ("scalars",):
            text = r["text"][0].upper() + r["text"][1:]
        elif k == "multitest":
            text = f"Liczba zestawów danych {vs[0] if vs else 't'}; potem tyle razy powtarza się opis poniżej."
        elif k == "scalars":
            text = ("Liczba " if len(vs) == 1 else "Liczby ") + ", ".join(vs)
        elif k == "array":
            text = f"Ciąg {vs[0] if vs else 'a'} — {it.get('length') or '?'} liczb (w jednym wierszu)"
        elif k == "string":
            text = f"Napis {vs[0] if vs else 's'}"
        elif k == "rows":
            rr = _rep_text(rep)
            if it.get("length"):
                text = f"{rr} wierszy, w każdym {it['length']} liczb (macierz/tablica 2D)"
            elif vs:
                text = f"{rr} wierszy, w każdym: " + ", ".join(vs)
            else:
                text = f"{rr} wierszy z danymi"
        elif k == "grid":
            text = f"Plansza: {_rep_text(rep)} wierszy znaków"
        elif k == "lines":
            text = f"{_rep_text(rep)} wierszy, w każdym napis"
        elif k == "queries":
            text = f"{_rep_text(rep)} zapytań/operacji, po jednym w wierszu"
        else:
            text = it.get("desc", "")
        out.append({"line": line, "kind": k, "vars": vs, "text": text, "desc": it.get("desc", ""),
                    "role": (r or {}).get("role", k), "per_test": bool(it.get("per_test")),
                    "source": it.get("source", "text"), "unverified": bool(it.get("unverified"))})
    return out


def _rep_text(rep) -> str:
    if rep is None or rep == "?" or rep == "":
        return "Kolejne"
    rep = str(rep)
    if rep.startswith("until:"):
        return f"Kolejne (aż do wiersza „{rep.split(':', 1)[1]}”)"
    if rep == "EOF":
        return "Wszystkie pozostałe (do końca wejścia)"
    return rep


# ============================================================================================ „W skrócie”
def _bound(name: str, sb: dict, fallback: str | None = None) -> str:
    if str(name).startswith("until:"):
        return f"(do wiersza „{str(name).split(':', 1)[1]}”)"
    if str(name) == "EOF":
        return "(do końca wejścia)"
    c = sb.get(ST.base_var(name)) or {}
    mx = c.get("max")
    if c.get("bignum"):
        return f"{name} ≤ {ST.fmt_big(c)}"
    if isinstance(mx, (int, float)):
        return f"{name} ≤ {ST.fmt_value(mx)}"
    return fallback or name


def given_phrase(p: dict, struct: dict) -> str | None:
    cons = p.get("constraints") or {}
    sb = ST.size_bounds(cons)
    items = struct.get("items") or []
    kind = struct.get("kind")
    flags = struct.get("flags") or {}
    mean = _meanings(p)
    parts: list[str] = []
    vv = struct.get("vertex_var") or "n"
    arrays = [r for r in items if r["role"] in ("array", "column")]
    if kind == "tree":
        s = f"drzewo o {_bound(vv, sb)} wierzchołkach"
        extra = []
        if flags.get("rooted"):
            extra.append("korzeń 1" if any("Korzeń: wierzchołek 1" in x for x in struct.get("notes") or []) else "ukorzenione")
        if flags.get("weighted"):
            extra.append("krawędzie z wagami")
        if extra:
            s += " (" + ", ".join(extra) + ")"
        for r in arrays:
            if str(r.get("length")) == vv:
                nm = ST.base_var(r["vars"][0]) if r.get("names") else None
                s += f", w każdym wierzchołku liczba{(' ' + nm + '_i') if nm else ''}"
                break
        parts.append(s)
    elif kind == "graph":
        e = next((r for r in items if r["role"] == "edges"), {})
        m = str(e.get("repeat") or "m")
        s = (f"graf {'skierowany' if flags.get('directed') else 'nieskierowany'}{', ważony' if flags.get('weighted') else ''}: "
             f"{_bound(vv, sb)} wierzchołków, {_bound(m, sb)} krawędzi")
        parts.append(s)
    elif kind == "grid":
        g = next((r for r in items if r["role"] == "grid"), {})
        rr, cc = str(g.get("repeat") or "n"), str(g.get("length") or "")
        dims = f"{_bound(rr, sb)}" + (f" × {_bound(cc, sb)}" if cc and cc != rr else (f" × {rr}" if cc == rr else ""))
        parts.append(("plansza znaków " if g.get("kind") == "grid" else "plansza liczb ") + dims)
    elif kind == "strings":
        strs = [r for r in items if r["role"] in ("string", "lines")]
        if len(strs) == 1 and strs[0]["role"] == "string":
            v = (strs[0].get("vars") or ["s"])[0]
            c = cons.get(f"|{v}|") or {}
            L = strs[0].get("length")
            parts.append(f"napis {v if strs[0].get('names') else ''}".strip() +
                         (f" o długości ≤ {ST.fmt_value(c['max'])}" if c.get("max") else
                          (f" długości {_bound(str(L), sb)}" if L else "")))
        elif all(r["role"] == "string" for r in strs):
            V = ST.VALUE_RE.pattern
            mlen = re.search(r"d[lł]ugo[sś]\w*[^.]{0,40}?(?:nie (?:wi[eę]ksz\w* ni[zż]|przekracza\w*)|co najwy[zż]ej|≤)\s*(" + V + r")",
                             p["sections"].get("wejscie", ""))
            lv = ST.parse_value(mlen.group(1)) if mlen else None
            parts.append((f"{len(strs)} napisy" if len(strs) < 5 else f"{len(strs)} napisów") +
                         (f" o długości ≤ {ST.fmt_value(lv)}" if isinstance(lv, (int, float)) else ""))
        else:
            ln = next((r for r in strs if r["role"] == "lines"), {})
            parts.append(f"{_bound(str(ln.get('repeat') or 'n'), sb)} napisów")
    elif kind in ("array", "pairs"):
        for r in items:
            if r["role"] in ("array", "column"):
                L = str(r.get("length") or r.get("repeat") or "")
                named = {ST.base_var(v) for x in items for v in (x.get("names") or [])} | \
                    {ST.base_var(k) for k in cons if not k.startswith("_")}
                if re.fullmatch(r"[A-Za-z]", L) and L not in named:
                    L = ""          # długość nazwana tylko przez parser — nie pokazujemy wymyślonej litery
                nm = ST.base_var(r["vars"][0]) if (r.get("vars") and r.get("names")) else None
                c = sb.get(nm) if nm else None
                if not c or c.get("max") is None:
                    lo, hi = _elem_range(next((it.get("desc") or "" for i, it in enumerate(p.get("input_format") or [])
                                                if i == r["index"]), ""))
                    c = {"max": hi} if hi is not None else None
                rng = f", wartości ≤ {ST.fmt_value(c['max'])}" if c and isinstance(c.get("max"), (int, float)) else ""
                parts.append(f"ciąg {_bound(L, sb) + ' ' if L else ''}liczb{(' ' + nm + '_i') if nm else ''}{rng}")
            elif r["role"] in ("intervals", "points", "rows"):
                lab = {"intervals": "przedziałów", "points": "punktów", "rows": "wierszy danych"}[r["role"]]
                parts.append(f"{_bound(str(r.get('repeat') or 'n'), sb)} {lab}")
    elif kind == "values":
        vs = []
        for r in items:
            if r["role"] in ("scalars", "tests", "rows"):
                for v in (r.get("names") or [])[:4]:
                    if _bound(v, sb) != v or r["role"] == "scalars":
                        vs.append(_bound(v, sb))
        if vs:
            parts.append(("liczby " if len(vs) > 1 else "liczba ") + ", ".join(dict.fromkeys(vs[:5])))
        else:
            nl = sum(1 if r["role"] == "scalars" else int(r["repeat"]) if re.fullmatch(r"\d+", str(r.get("repeat") or "")) else 0
                     for r in items)
            if nl:
                parts.append(f"kilka liczb ({nl} {'wiersz' if nl == 1 else 'wiersze' if nl < 5 else 'wierszy'})")
    if kind == "queries":
        reps = {re.sub(r"\s*[-+*·].*$", "", str(r.get("repeat") or "")) for r in items}
        for r in items:
            if r["role"] == "scalars":
                for v in r.get("vars") or []:
                    if ST.base_var(v) not in reps and isinstance((sb.get(ST.base_var(v)) or {}).get("max"), (int, float)):
                        m = mean.get(ST.base_var(v))
                        parts.append(_bound(v, sb) + (f" ({_clip(m, 30)})" if m else ""))
    if not parts and kind != "queries":
        return None
    s = "; ".join(parts[:3])
    if flags.get("queries"):
        q = struct.get("query_count") or "q"
        qt = [x for x in struct.get("queries") or [] if x.get("tag")]
        typy = "typy" if 2 <= len(qt) <= 4 else "typów"
        tl = f" ({len(qt)} {typy}: {', '.join(x['tag'] for x in qt)})" if len(qt) > 1 else ""
        qw = struct.get("query_word") or "zapytań"
        qb = f"{qw} {_bound(q, sb)}" if str(q).startswith(("until:", "EOF")) else f"{_bound(q, sb)} {qw}"
        s = ((s + "; " if s else "") + f"{qb}{tl}" if kind == "queries" else s + f"; do tego {qb}{tl}")
    if flags.get("multitest"):
        t = next((r for r in items if r["role"] in ("multitest", "tests")), {})
        tv = (t.get("vars") or ["t"])[0] if t.get("role") == "multitest" else str(t.get("repeat") or "t")
        s = f"{_bound(tv, sb)} zestawów danych; w każdym: " + s if t.get("role") == "multitest" else s
    return s


def _answer_shape(p: dict, task: dict) -> str | None:
    wyj = p["sections"].get("wyjscie", "")
    low = _low(wyj)
    words = task.get("words") or []
    yn = [w for w in ("TAK", "NIE", "PRAWDA", "FAŁSZ", "YES", "NO") if w in words]
    if len(yn) >= 2:
        tail = " (+ rozwiązanie)" if "construct" in (task.get("tags") or []) else ""
        return " / ".join(yn) + tail
    special = [w for w in words if w in ("NIE", "BRAK", "-1", "IMPOSSIBLE", "NIEMOŻLIWE")]
    if special and re.search(r"w przeciwny\w* (?:wypadku|razie|przypadku)", low):
        return f"„{special[0]}” albo pełna odpowiedź (patrz Wyjście)"
    if special and re.search(r"liczb", low) and not re.search(r"[a-z]\s+(?:wierszy|linii)", low):
        return f"jedna liczba albo „{special[0]}”"
    if re.search(r"liczb\w* rzeczywist|zmiennoprzecink|po przecinku|po kropce", low):
        m = re.search(r"(\d+)\s+(?:cyfr\w*|miejsc\w*)\s+po\s+(?:przecinku|kropce)", low)
        return "liczba rzeczywista" + (f" ({m.group(1)} miejsc po przecinku)" if m else "")
    if task.get("id") == "queries" and re.search(r"zapyta|pytani|operacj|polece", low):
        return "odpowiedź na zapytania, każda w osobnym wierszu"
    m = re.search(r"(?:wypisa\w*|wypisz|sklada\w* sie z|powin\w+ (?:sie )?(?:znalezc|znajdowac|zawierac)|wyjscie\w* zawiera\w*)\s+"
                  r"(?:dokladnie\s+)?([a-z](?:\s*[-+]\s*\d)?)\s+(?:wierszy|linii|lini)", low)
    if m:
        return f"{m.group(1)} wierszy"
    if re.search(r"(?:jedn\w*|pojedyncz\w*)\s+(?:nieujemn\w* |dodatni\w* )?(?:liczb|wartosc)", low) and len(_sentences(wyj)) <= 3:
        return "jedna liczba"
    if re.search(r"(?:jedn\w*|pojedyncz\w*)\s+(?:slow|napis)", low):
        return "jedno słowo"
    m = re.search(r"(?:wypisa\w*|wypisz|sklada\w* sie z|powin\w+ (?:sie )?(?:znalezc|znajdowac|zawierac))\s+(?:dokladnie\s+)?"
                  r"([a-z](?:\s*[-+]\s*\d)?)\s+(?:wierszy|linii|lini)", low)
    if m:
        return f"{m.group(1)} wierszy"
    m = re.search(r"(?:wypisa\w*|wypisz|znalezc sie|znajdowac sie|zawierac)\s+(?:dokladnie\s+)?([a-z])\s+(?:\w+\s+){0,4}?liczb", low)
    if m:
        return f"{m.group(1)} liczb" + (" w jednym wierszu" if re.search(r"oddzielon|spacj|odstep|jednym wierszu", low) else "")
    if re.search(r"w (?:pierwszym i )?jedyn\w+ wierszu", low) and re.search(r"liczb", low):
        return "liczby w jednym wierszu" if re.search(r"oddzielon|spacj|odstep", low) else "jeden wiersz"
    return None


def _short_meaning(m: str, n: int = 48) -> str:
    m = re.sub(r"^(?:pro[sś]b\w*|[zż][aą]dani\w*|polecen\w*|zapytani\w*|operacj\w*)\s+o\s+", "", (m or "").strip(), flags=re.I)
    return _clip(m, n)


def brief(p: dict, struct: dict, task: dict) -> dict:
    g = given_phrase(p, struct)
    gp = goal_phrase(p, task)
    shape = _answer_shape(p, task)
    what = gp.get("what")
    parts = []
    if g:
        parts.append(f"Dane: {g}.")
    qt = [q for q in struct.get("queries") or [] if q.get("tag")]
    ops = None
    if qt:
        items = []
        for q in qt[:5]:
            head = (q["tag"] + " " + " ".join(x for x in q.get("params") or [] if not re.fullmatch(r"x\d", x))).strip()
            m = _short_meaning(q.get("meaning"))
            items.append(f"{head}" + (f" — {m}" if m else ""))
        ops = "; ".join(items)
        parts.append(f"Operacje: {ops}.")
        prints = [q["tag"] for q in qt if q.get("prints")]
        words = [w for w in task.get("words") or [] if w not in ("-1",)]
        if prints:
            parts.append(f"Wypisz: odpowiedź na każde {', '.join(prints)}" + (f" ({'/'.join(words[:3])})" if words else "") +
                         ", każdą w osobnym wierszu.")
        elif shape:
            parts.append(f"Wynik: {shape}.")
    elif what:
        verb = gp.get("verb")
        if verb and gp.get("source") == "treść":
            sep = ", " if re.match(r"(?:czy|ile|jak|kt[oó]r|jaki|jaka|jakie|gdzie|kiedy|na ile)\b", what, re.I) else " "
            parts.append(f"{verb}{sep}{what}" + (f" — {shape}" if shape else "") + ".")
        elif verb:
            parts.append(f"{verb}: {what}" + (f" — {shape}" if shape else "") + ".")
        else:
            parts.append(f"Cel: {what}" + (f" — {shape}" if shape else "") + ".")
    elif shape:
        parts.append(f"Wynik: {shape}.")
    text = " ".join(parts).replace("..", ".").replace("?.", "?").replace("….", "…")
    return {"text": text, "given": g, "goal": what if not ops else ops, "goal_verb": gp.get("verb") if not ops else "Operacje",
            "goal_source": gp.get("source") if not ops else "zapytania", "answer": shape}


# ============================================================================================ złożoność / budżet
def _ops(v: float) -> str:
    if v >= 1e4:
        e = int(math.floor(math.log10(v)))
        lead = v / 10 ** e
        lead_s = f"{lead:.1f}".replace(".", ",").rstrip("0").rstrip(",")
        return ("" if lead_s == "1" else lead_s + "·") + f"10^{e}"
    return str(int(round(v)))


def complexity_block(p: dict, struct: dict, task: dict) -> dict:
    cx = SG.complexity_info(p) if SG is not None else {}
    out = {k: cx.get(k) for k in ("var", "max", "max_fmt", "complexity", "hint", "note", "sizes")} if cx else {}
    tl = p.get("time_limit")
    budget = (tl or 1.0) * OPS_PER_SEC
    out["budget"] = budget
    out["budget_text"] = (f"≈ {_ops(budget)} prostych operacji (limit {str(tl).replace('.', ',')} s)" if tl else
                          f"limit czasu nieznany — przyjmij ≈ {_ops(OPS_PER_SEC)} operacji na sekundę")
    cons = p.get("constraints") or {}
    sb = ST.size_bounds(cons)
    kind = struct.get("kind")
    # koszt naiwny dla zapytań: q × n
    naive = naive_ok = None
    if struct.get("flags", {}).get("queries"):
        q = struct.get("query_count")
        qb = re.sub(r"[-+*·].*$", "", str(q or ""))
        qmax = (sb.get(qb) or {}).get("max")
        nvar = next((x["var"] for x in (cx.get("sizes") or []) if x["var"] != qb and "·" not in x["var"]), None)
        nmax = (sb.get(nvar) or {}).get("max") if nvar else None
        if isinstance(qmax, (int, float)) and isinstance(nmax, (int, float)):
            naive = (qb, qmax, nvar, nmax)
            naive_ok = qmax * nmax <= budget
    hints = algo_hints(p, struct, task, naive_ok)
    out["hints"] = hints
    out["basis"] = "rozmiar"
    v0 = out.get("var")
    if isinstance(v0, str) and re.fullmatch(r"[A-Za-z]", v0) and v0 != "n" and out.get("complexity") in \
            ("O(n!)", "O(2^n · n)", "O(n³)", "O(n²)", "O(n log n)", "O(n)", "O(√n)", "O(log n) / O(1)"):
        out["complexity"] = re.sub(r"(?<![A-Za-z])n(?![A-Za-z])", v0, out["complexity"])
    # 1) wartości zamiast rozmiaru — pojedyncze liczby na wejściu
    test_vars = set()
    for r in struct.get("items") or []:
        if r["role"] == "multitest":
            test_vars |= {ST.base_var(v) for v in r.get("vars") or []}
        elif r["role"] == "tests":
            test_vars.add(re.sub(r"[-+*·].*$", "", str(r.get("repeat") or "")))
    role_sizes = {re.sub(r"\s*[-+*·].*$", "", str(r.get(key) or "")) for r in struct.get("items") or []
                  for key in ("repeat", "length")} - {""}
    if kind == "values" and role_sizes <= test_vars:
        tcount = max([(sb.get(v) or {}).get("max") or 0 for v in test_vars] or [0])
        if tcount:
            budget = budget / tcount
        vals = [(r["base"], r["max"]) for r in variables_table(p, struct) if isinstance(r.get("max"), (int, float))
                and r["kind"] in ("value", "size") and r["base"] not in test_vars]
        big = [(k, v) for k, v in cons.items() if v.get("bignum")]
        if vals and not big:
            v, V = max(vals, key=lambda kv: kv[1])
            out["basis"] = "wartość"
            out["var"], out["max"], out["max_fmt"] = v, V, ST.fmt_value(V)
            if re.search(r"\bcyfr", _low(" ".join(p["sections"].get(k, "") for k in ("tresc", "wejscie", "wyjscie")))) \
                    and V >= 10**5:
                out["complexity"] = f"O(log {v})"
                out["note"] = (f"{v} ≤ {ST.fmt_value(V)} to wartość — operacje na cyfrach: {v} ma najwyżej "
                               f"{len(str(int(V)))} cyfr.")
            elif V <= budget:
                out["complexity"] = f"O({v})"
                out["note"] = (f"{v} ≤ {ST.fmt_value(V)} to wartość, nie liczba danych — pętla po wszystkich wartościach do {v} "
                               f"(≈ {_ops(V)} kroków) mieści się w budżecie.")
            elif V <= 10**13:
                out["complexity"] = f"O(√{v})"
                out["note"] = (f"{v} ≤ {ST.fmt_value(V)} to wartość: pętla do {v} to za dużo, ale do √{v} ≈ {_ops(math.isqrt(int(V)))}"
                               " kroków — wystarczy (dzielniki, rozkład na czynniki).")
            else:
                out["complexity"] = f"O(log {v})"
                out["note"] = (f"{v} ≤ {ST.fmt_value(V)} to wartość — potrzebny wzór, szybkie potęgowanie albo wyszukiwanie "
                               f"binarne (≈ {int(math.log2(V)) + 1} kroków).")
            if tcount:
                out["note"] += f" Zestawów jest do {ST.fmt_value(tcount)} — to na jeden zestaw (budżet ≈ {_ops(budget)})."
            out["hint"] = "; ".join(h["text"] for h in hints[:2]) if hints else None
            return out
    # 2) graf: rozmiar to n + m (wierzchołki i krawędzie), nie „wartość n”
    if kind == "graph":
        e = next((r for r in struct.get("items") or [] if r["role"] == "edges"), {})
        mv = re.sub(r"\s*[-+*·].*$", "", str(e.get("repeat") or ""))
        nv = struct.get("vertex_var")
        nmx, mmx = (sb.get(nv) or {}).get("max"), (sb.get(mv) or {}).get("max")
        if isinstance(nmx, (int, float)) and isinstance(mmx, (int, float)) and nv and mv:
            tot = nmx + mmx
            lg = max(1, math.log2(max(nmx, 2)))
            out["note"] = (f"Graf: {nv} ≤ {ST.fmt_value(nmx)} wierzchołków, {mv} ≤ {ST.fmt_value(mmx)} krawędzi. BFS/DFS to "
                           f"O({nv} + {mv}) ≈ {_ops(tot)}, z kolejką priorytetową O(({nv} + {mv}) log {nv}) ≈ {_ops(tot * lg)}.")
            if tot >= 10**4:
                out["complexity"] = f"O(({nv} + {mv}) log {nv})" if tot * lg <= budget else f"O({nv} + {mv})"
    # 3) zapytania: koszt naiwny q × n
    if naive:
        qb, qmax, nvar, nmax = naive
        if True:
            naive = qmax * nmax
            lg = max(1, math.log2(max(nmax, 2)))
            if naive <= budget:
                out["note"] = (f"Naiwnie O({qb}·{nvar}) ≈ {_ops(naive)} — zmieści się (każde zapytanie w O({nvar})). "
                               + (f"O({nvar} log {nvar}) na zapytanie ≈ {_ops(naive * lg)} — "
                                  + ("też OK." if naive * lg <= budget else "na granicy.")))
                out["complexity"] = f"O({qb}·{nvar})"
            else:
                out["note"] = (f"Naiwnie O({qb}·{nvar}) ≈ {_ops(naive)} — za wolno. Potrzebujesz ~O(log {nvar}) na zapytanie: "
                               f"O(({nvar} + {qb}) log {nvar}) ≈ {_ops((nmax + qmax) * lg)}.")
                out["complexity"] = f"O(({nvar} + {qb}) log {nvar})"
    out["hint"] = "; ".join(h["text"] for h in hints[:2]) if hints else None
    return out


def algo_hints(p: dict, struct: dict, task: dict, naive_ok: bool | None = None) -> list[dict]:
    """Podpowiedzi wynikające ze STRUKTURY danych i celu (nie z samej tabeli limitów). Max 3."""
    sec = p["sections"]
    full = _low(" ".join(sec.get(k, "") for k in ("tresc", "wejscie", "wyjscie")))
    kind = struct.get("kind")
    flags = struct.get("flags") or {}
    out: list[dict] = []

    def add(text, why):
        if not any(h["text"] == text for h in out):
            out.append({"text": text, "why": why})
    q = flags.get("queries")
    if kind == "tree":
        if q and re.search(r"poddrzew", full):
            add("DFS z czasami wejścia/wyjścia — poddrzewo to przedział [tin, tout]", "drzewo + zapytania o poddrzewa")
        if re.search(r"najnizsz\w* wspoln\w* przodk|\blca\b|odleglosc\w* miedzy|sciezk\w* miedzy", full):
            add("LCA (binary lifting) — odległość u–v = głęb(u) + głęb(v) − 2·głęb(lca)", "drzewo + pary wierzchołków")
        if re.search(r"glebokos|odleglosc\w* od korzen|od pokoju numer 1|od wierzcholka (?:nr |numer )?1", full):
            add("Jeden DFS/BFS od korzenia liczy głębokości (sumy wag) wszystkich wierzchołków", "drzewo + odległość od korzenia")
        if task.get("id") in ("optimize", "count") or re.search(r"poddrzew", full):
            add("DP na drzewie: wynik dla poddrzewa z wyników dzieci (DFS post-order)", "drzewo + " +
                ("optymalizacja" if task.get("id") == "optimize" else "zliczanie" if task.get("id") == "count" else "poddrzewa"))
        add("DFS/BFS po liście sąsiedztwa; przy n ~ 10^6 uważaj na głęboką rekurencję", "drzewo")
    elif kind == "graph":
        w = flags.get("weighted")
        if re.search(r"najkrotsz|najmniej\w* (?:drog|krawedz|przesiad|ruch)|odleglosc", full):
            add("Dijkstra (wagi ≥ 0) z kolejką priorytetową" if w else "BFS — najkrótsze ścieżki w grafie bez wag",
                "graf " + ("ważony" if w else "bez wag") + " + najkrótsza droga")
        if re.search(r"nie tworz\w* cykl|acykliczn|\bdag\b", full) or (flags.get("directed") and task.get("id") == "count"):
            add("Sortowanie topologiczne + DP po kolejności (np. liczba ścieżek modulo)", "graf skierowany bez cykli")
        if re.search(r"spojn\w*|skladow\w*|osiagaln", full):
            add("Spójne składowe: DFS/BFS albo Find & Union (DSU)", "graf + spójność/osiągalność")
        if re.search(r"dwudziel|dwa kolory|pokoloruj", full):
            add("Kolorowanie BFS-em na 2 kolory (graf dwudzielny)", "graf + dwa kolory")
        if re.search(r"minimaln\w* drzew\w* rozpinaj|najtansz\w* sie[cć]|polacz\w* wszystk\w* .*najmniejsz", full):
            add("Minimalne drzewo rozpinające (Kruskal + DSU)", "graf ważony + połącz wszystko najtaniej")
        add("Lista sąsiedztwa (vector<vector<int>>), a nie macierz — przy dużym n macierz się nie zmieści", "graf")
    elif kind == "grid":
        if re.search(r"najkrotsz|najmniej\w* (?:ruch|krok)|odleglosc", full):
            add("BFS po polach planszy (4 sąsiadów)", "plansza + najkrótsza droga")
        if re.search(r"\(a \+ 1, b\)|w prawo|w dol|prawo lub w dol|\(i\+1|\(a, b \+ 1\)", full):
            add("DP po planszy wierszami (ruch tylko w prawo/w dół)", "plansza + ruchy w prawo/dół")
        if re.search(r"(?:najwi\w*|maksymaln\w*) (?:\w+ ){0,3}(?:kwadrat|prostokat)|(?:kwadrat|prostokat)\w* (?:\w+ ){0,3}"
                     r"(?:najwi|maksymaln)", full):
            add("DP „największy kwadrat kończący się w (i, j)” albo sumy prefiksowe 2D", "plansza + największy kwadrat/prostokąt")
        if re.search(r"sum\w* (?:\w+ ){0,4}(?:prostokat|fragment|podplansz)", full):
            add("Sumy prefiksowe 2D — suma prostokąta w O(1)", "plansza + sumy prostokątów")
        if re.search(r"(?:ile|liczb\w*|policz\w*) (?:\w+ ){0,3}(?:spojn\w*|obszar\w*|wysp\w*|jezior\w*|plam\w*|pokoj\w*)", full):
            add("Flood fill (BFS/DFS) liczy spójne obszary", "plansza + obszary")
        if not out and task.get("id") == "optimize":
            add("DP wiersz po wierszu (stan: pole/kolumna w poprzednim wierszu)", "plansza + optymalizacja")
    if q and naive_ok:
        add("Wystarczy symulacja wprost: każde zapytanie/operacja w O(n) mieści się w limicie", "q·n ≤ budżet operacji")
    if q and not naive_ok:
        upd = re.search(r"zmie[nń]\w*|zamien\w*|przelacz\w*|dodaj\w*|zwieksz\w*|ustaw\w*|aktualiz\w*|update", full)
        rng = re.search(r"przedzial\w*|od a do b|\[a, b\]|fragment\w*", full)
        upd = upd or re.search(r"posortow|sortuj|odwr[oó]c|przestaw", full)
        if rng and not upd and kind != "tree" and re.search(r"sum", full):
            add("Sumy prefiksowe: suma na przedziale w O(1) (bez zmian w trakcie)", "zapytania o przedziały, brak zmian")
        elif rng and upd and kind != "tree":
            if re.search(r"(?:dla|w) kazd\w* (?:lamp|elemen|pozycj)\w* z przedzialu|zmien\w* stan\w* wszystk\w*|"
                         r"na danym przedziale|dodaj\w* \w+ do wszystk", full):
                add("Zmiana na przedziale + pytanie o punkt → Fenwick/drzewo przedziałowe na różnicach "
                    "(albo lazy propagation)", "zapytania: aktualizacja przedziału, odczyt punktu")
            else:
                add("Drzewo przedziałowe / Fenwick: zmiana i zapytanie w O(log n)", "zapytania ze zmianami")
        if re.search(r"zbior\w*|najmniejsz\w* element\w* niemniejsz|lower_bound|kolejk\w* priorytet|kopiec|kopca", full):
            add("std::set / multiset / priority_queue — operacje w O(log n)", "operacje na zbiorze/kolejce")
    if kind == "pairs" and re.search(r"po[lł][aą]cz\w* (?:\w+ ){0,2}wszystk", full) and task.get("id") == "optimize":
        add("Minimalne drzewo rozpinające na punktach: Prim O(n²) (graf pełny) albo Kruskal", "punkty + połącz wszystkie najtaniej")
    if kind in ("array", "pairs") and not q:
        if re.search(r"inwersj", full):
            add("Liczba inwersji: merge sort albo Fenwick po wartościach — O(n log n)", "ciąg + inwersje")
        if re.search(r"k-?t\w* najmniejsz|k-?t\w* najwieksz|median", full):
            add("nth_element / sortowanie — k-ty element w O(n) / O(n log n)", "ciąg + k-ty element")
        if re.search(r"najdluzsz\w* (?:\w+ )?(?:rosnac|niemalejac)\w* podciag", full):
            add("LIS w O(n log n) (lower_bound na tablicy końców)", "ciąg + najdłuższy rosnący podciąg")
        if re.search(r"spojn\w* (?:fragment|podciag)|kolejn\w* element|podtablic|sasiedni", full) and \
                re.search(r"sum\w*", full):
            add("Sumy prefiksowe / dwa wskaźniki dla spójnych fragmentów", "ciąg + spójne fragmenty")
    if kind == "strings":
        if re.search(r"wspoln\w* podciag", full):
            add("DP LCS: O(|a|·|b|) — tablica (|a|+1)×(|b|+1)", "dwa napisy + wspólny podciąg")
        if re.search(r"palindrom", full):
            add("Palindromy: rozszerzanie od środka / Manacher / DP", "napis + palindromy")
        if re.search(r"wzorz\w*|wystapie\w*|wystepuj\w* jako", full):
            add("Wyszukiwanie wzorca: KMP / funkcja prefiksowa / hashowanie", "napis + wzorzec")
        if re.search(r"leksykograficzn\w* najmniejsz", full) and re.search(r"ulozen|kolejnosc|polaczen|sklej", full):
            add("Sortowanie z komparatorem a+b < b+a", "sklejanie napisów w najmniejsze słowo")
    if kind == "values":
        if re.search(r"dzielnik|czynnik\w* pierwsz|liczb\w* pierwsz|pierwsz\w* liczb", full):
            add("Dzielniki/czynniki: pętla do √n" if not re.search(r"sito|pierwsze n liczb|n liczb pierwszych|n pierwszych liczb|"
                                                                   r"kolejn\w* liczb\w* pierwsz|i-ta liczbe pierwsza", full)
                else "Sito Eratostenesa", "liczby + dzielniki/pierwsze")
        if re.search(r"nwd|najwieksz\w* wspoln\w* dzielnik", full):
            add("std::gcd (algorytm Euklidesa)", "NWD")
        if re.search(r"potega|poteg\w*|\^", full) and re.search(r"modulo|reszt", full):
            add("Szybkie potęgowanie modulo — O(log k)", "potęga modulo")
    if task.get("id") == "optimize" and re.search(r"minimaln\w* (?:czas|maksym|najwieksz)|najmniejsz\w* (?:takie|taki|taka|mozliw\w* "
                                                  r"(?:czas|maksym|wartosc))|maksymaln\w* minimaln", full):
        add("Wyszukiwanie binarne po wyniku (czy da się w czasie T?)", "min-max / „najmniejsze takie, że…”")
    return out[:3]


# ============================================================================================ pułapki
def pitfalls(parsed: dict, struct: dict | None = None, task: dict | None = None) -> list[dict]:
    sec = parsed["sections"]
    full = " ".join(sec.get(k, "") for k in ("tresc", "wejscie", "wyjscie", "uwagi", "ocenianie"))
    low = T.strip_accents(full).lower()
    outl = T.strip_accents(sec.get("wyjscie", "")).lower()
    cons = parsed.get("constraints") or {}
    fmt = parsed.get("input_format") or []
    sb = ST.size_bounds(cons)
    struct = struct or structure(parsed)
    task = task or task_type(parsed, struct)
    kind = struct.get("kind")
    out: list[dict] = []

    def add(pid, title, text, sev="warn", icon="triangle-alert"):
        if not any(p["id"] == pid for p in out):
            out.append({"id": pid, "title": title, "text": text, "severity": sev, "icon": icon})

    huge = [(k, v) for k, v in cons.items() if v.get("bignum") and v.get("max_digits")]
    if huge:
        k, v = huge[0]
        add("bignum", "Liczba jako napis",
            f"{k} ≤ {ST.fmt_big(v)} ma do {ST.fmt_value(v['max_digits'])} cyfr — nie zmieści się w żadnym typie "
            f"liczbowym (long long ma 19 cyfr, nawet __int128 tylko 39). Wczytaj {k} jako `string` i licz na cyfrach: "
            "długość zapisu, przeniesienia, porównania napisów. Rozmiarem danych jest liczba cyfr.", "danger", "binary")
    big = [(k, v) for k, v in cons.items() if any(isinstance(v.get(f), (int, float)) and abs(v[f]) > INT_MAX
                                                 for f in ("min", "max")) and not v.get("bignum")]
    if big:
        k, v = big[0]
        add("long-long", "Duże liczby → long long",
            f"{('Wartości' if k.startswith('_') else k)} sięga {ST.fmt_value(v.get('max'))} — to nie mieści się w int "
            f"(max ≈ 2,1·10^9). Wczytuj i licz na `long long`.", "danger", "binary")
        if any(isinstance(v.get("max"), (int, float)) and v["max"] > LL_MAX for _, v in big):
            add("int128", "Nawet long long może nie wystarczyć",
                "Liczby przekraczają 9,2·10^18 — rozważ `__int128`, `unsigned long long` albo arytmetykę na napisach.",
                "danger", "binary")
    # suma / iloczyn / licznik może przekroczyć int — liczone z ograniczeń
    nvar, nmax, _ = ST.main_size(fmt, cons)
    vrows = [r for r in variables_table(parsed, struct) if isinstance(r.get("max"), (int, float))]
    endpoints = set()
    for r in struct.get("items") or []:
        if r["role"] in ("edges", "tree_edges", "intervals", "queries"):
            endpoints |= {ST.base_var(v) for v in (r.get("vars") or [])[:2]}
        elif r["role"] == "parents":
            endpoints |= {ST.base_var(v) for v in (r.get("vars") or [])[:1]}
    vals = [(r["base"], r["max"]) for r in vrows if r["base"] != nvar and r["base"] not in endpoints and
            (r["kind"] == "array" or r["name"] == "wartości" or (r["kind"] == "value" and abs(r["max"]) >= 10**9))]
    low3 = _low(" ".join(sec.get(k, "") for k in ("tresc", "wejscie", "wyjscie")))
    # „suma n we wszystkich zestawach…” to ograniczenie rozmiaru, nie suma wartości
    low3 = re.sub(r"\bsum\w* (?:wartosci |dlugosci |rozmiarow )?(?:wszystkich )?(?:[a-z]|\|[a-z]\|)(?:_?i)?\b", " ", low3)
    if nmax and vals and not big:
        vn, vmax = max(vals, key=lambda kv: kv[1])
        total = nmax * vmax
        weighted = (struct.get("flags") or {}).get("weighted")
        if total > INT_MAX and (re.search(r"\bsum|\blaczn\w* (?:koszt|sum|dlugosc|czas|wag|smacznosc|liczb|wartosc|"
                                          r"odleglosc|kwot)|\bzebra|\bzbiera|uzbiera|zarobi|\bzysk|zlowi|\bpolow|\blup\w*\b|"
                                          r"w sumie|calkowit\w* (?:koszt|czas)|iloczyn|\bkoszt|wydac|zaplac",
                                          low3)
                                or (weighted and re.search(r"odleglosc|najkrotsz|dlugosc\w* (?:sciezk|drog)|glebokos", low3))):
            add("sum-overflow", "Suma może przekroczyć int",
                f"{nvar} ≤ {ST.fmt_value(nmax)} wartości po maks. {ST.fmt_value(vmax)} → suma do "
                f"~{ST.fmt_value(int(total))}. Trzymaj sumę w `long long`.", "danger", "sigma")
    prod_vals = [r["max"] for r in vrows if r["kind"] in ("array", "value", "param") and r["base"] not in endpoints
                 and abs(r["max"]) > 46340]
    if prod_vals and not big and re.search(r"iloczyn|pomnoz|mnoz|ulamk|\bnww\b|kwadrat\w* (?:liczb|odleglosc)", low3) and \
            not re.search(r"iloczyn\w*[^.]{0,40}cyfr", low3):
        X = max(abs(v) for v in prod_vals)
        add("product-overflow", "Iloczyn może przekroczyć int",
            f"Iloczyn dwóch liczb ≤ {ST.fmt_value(X)} sięga {ST.fmt_value(X * X)} — mnóż na `long long`"
            + (" (a nawet to może nie wystarczyć — `__int128`)." if X * X > LL_MAX else " (rzutuj PRZED mnożeniem: 1LL * a * b)."),
            "danger", "sigma")
    if nmax and not big and task.get("id") == "count" and not task.get("modulo"):
        pairs = nmax * (nmax - 1) // 2
        if pairs > INT_MAX and re.search(r"par\w*|trojk\w*|podciag\w*|podzbior\w*|sposob\w*|fragment\w*|przedzial\w*", low):
            add("count-overflow", "Wynik zliczania może nie zmieścić się w int",
                f"Przy {nvar} ≤ {ST.fmt_value(nmax)} samych par jest do ~{ST.fmt_value(pairs)} — licz w `long long`.",
                "danger", "sigma")
    mod = task.get("modulo")
    if mod or re.search(r"modulo|reszt\w* z dzielenia|10\^9 ?\+ ?7|1000000007|998244353", low):
        odd = mod and mod not in ("10^9+7",)
        add("modulo", f"Wynik modulo {mod}" if mod else "Wynik modulo",
            (f"Uwaga: moduł to {mod}" + (" — nie 10^9+7!" if odd else "") + ". " if mod else "") +
            "Bierz modulo po każdym dodawaniu i mnożeniu; mnożenie rób na `long long` ((a * b) % M). "
            "Przy odejmowaniu dodaj M: (a - b + M) % M.", "warn", "sigma")
    if any(it.get("kind") == "multitest" for it in fmt) or (struct.get("flags") or {}).get("multitest") or \
            re.search(r"zestaw\w* danych|liczb\w* testow", low):
        msum = re.search(r"sum\w* (?:wartosci |dlugosci )?(?:wszystkich )?([a-z])\w*[^.]{0,60}?(?:nie przekracza|≤|<=|co najwyzej)\s*"
                         r"([\d ^·*]+\d)", low)
        add("multitest", "Wiele zestawów danych w jednym wejściu",
            "Czyść tablice, wektory i liczniki przed każdym zestawem. " +
            (f"Suma {msum.group(1)} po wszystkich zestawach ≤ {msum.group(2).strip()} — licz złożoność od tej sumy i "
             "nie zeruj całych tablic maksymalnego rozmiaru w każdym teście (memset × liczba testów = TLE)."
             if msum else "Nie zeruj całych tablic maksymalnego rozmiaru dla każdego testu — sprawdź, czy suma rozmiarów "
             "jest ograniczona."), "warn", "layers")
    if re.search(r"(?:numer|liczb\w*)\s+0\b", low) and re.search(r"od 1 do", low):
        add("numbering", "Uwaga na numerację (0 i od 1)",
            "W treści pojawia się numer 0 obok numeracji od 1 — sprawdź dokładnie, kto ma jaki numer (tablica rozmiaru n + 1).",
            "warn", "list-checks")
    elif re.search(r"(numerowan|ponumerowan)\w* (\w+ )?(liczbami )?(kolejnymi )?(liczbami )?(naturalnymi )?od 1|"
                 r"od 1 do [a-z]\b|indeks\w* (numerujemy )?od 1|numerujemy od (1|jedynki)|numery od 1", low):
        add("one-indexed", "Indeksowanie od 1",
            "Numery w zadaniu zaczynają się od 1. Zrób tablicę rozmiaru n + 1 albo odejmij 1 przy wczytywaniu "
            "(i dodaj 1 przy wypisywaniu!).", "info", "list-checks")
    elif any(re.search(r"(?:^|[\s(,])1\s*≤\s*[^≤<]*≤\s*" + re.escape(sz) + r"\s*$", str(v.get("raw") or ""))
             for sz in (struct.get("vertex_var"), nvar) if sz for k, v in cons.items()
             if ST.base_var(k) in endpoints | {ST.base_var(x) for q in struct.get("queries") or [] for x in q.get("params") or []}):
        add("one-indexed", "Numery od 1 do n",
            "Ograniczenia typu 1 ≤ x ≤ n oznaczają numerację od 1 — tablica rozmiaru n + 1 albo odejmij 1 przy wczytywaniu.",
            "info", "list-checks")
    elif re.search(r"(numerowan|ponumerowan)\w* (\w+ )?(liczbami )?(kolejnymi )?(liczbami )?od 0|numer 0\b|od 0 do [a-z]",
                   low):
        add("zero-indexed", "Indeksowanie od 0", "Numery zaczynają się od 0 — nie przesuwaj ich o 1.", "info", "list-checks")
    raw_out = sec.get("wyjscie", "") + " " + sec.get("tresc", "")
    caps = set(answer_words(raw_out)) | {"-1", "0"}
    word = None
    for m in re.finditer(r"(?:je[zż]eli|jesli|gdy|w przypadku,? gdy)[^.]{0,90}?(?:nie (?:istnieje|da sie|mozna|ma)|niemozliw)"
                         r"[^.]{0,80}?(?:wypisz|wypisac|nalezy wypisac|wypisuje|powinien wypisac|wynikiem)\s+"
                         r"(?:jedno slowo |liczbe |slowo |napis |powinna byc liczba |powinno byc |jest |wtedy )?[„\"'‘]?"
                         r"([A-Za-z\-]?\w*|-1)", low):
        w = m.group(1)
        orig = re.search(r"(?<![\w" + _PL + r"])" + re.escape(w) + r"(?![\w" + _PL + r"])", raw_out, re.I)
        cand = orig.group(0) if orig else w
        if cand.upper() in {c.upper() for c in caps} or (cand.isupper() and len(cand) >= 2):
            word = cand
            break
    if word is None:
        m2 = re.search(r"(?<![\w-])(-1|BRAK|NIE|IMPOSSIBLE|NIEMOZLIWE|brak|impossible)\b[^.]{0,40}(?:jesli|gdy|jezeli)[^.]{0,60}"
                       r"(?:nie istnieje|niemozliw|nie da sie|nie mozna|nie ma)", T.strip_accents(sec.get("wyjscie", "")))
        if m2:
            word = m2.group(1)
    if word:
        shown = word.upper() if word.isalpha() else word
        add("no-solution", f"Przypadek bez rozwiązania → wypisz „{shown}”",
            f"Zadanie przewiduje sytuację, gdy odpowiedź nie istnieje. Upewnij się, że ją wykrywasz i wypisujesz "
            f"dokładnie „{shown}”.", "warn", "circle-stop")
    if re.search(r"\btak\b.{0,60}\bnie\b|\bnie\b.{0,60}\btak\b", outl) and re.search(r"\bTAK\b|\bNIE\b",
                                                                                  sec.get("wyjscie", "")):
        add("yes-no", "Odpowiedź TAK/NIE",
            "Wypisz dokładnie „TAK” albo „NIE” (wielkie litery, bez kropki i bez polskich znaków w innych miejscach).",
            "info", "circle-check")
    elif len([w for w in task.get("words") or [] if w not in ("-1",)]) >= 2 and "yes-no" not in {p["id"] for p in out}:
        ws = [w for w in task.get("words") or [] if w != "-1"]
        add("exact-words", "Dokładne słowa na wyjściu",
            "Wypisuj dokładnie: " + ", ".join(f"„{w}”" for w in ws) + " — wielkość liter i polskie znaki mają znaczenie.",
            "info", "circle-check")
    if re.search(r"(kilka|wiele|wiecej niz jed\w*) (\w+ )?(poprawnych |mozliwych |prawidlowych )?(odpowiedzi|rozwiazan|wynikow|"
                 r"sposobow)|dowoln\w* z nich|dowolne z nich|dowolna z nich", low):
        how = "dowolną" if re.search(r"dowoln\w* z nich|dowolne z nich|dowolna", low) else None
        if re.search(r"(najmniejsz|leksykograficznie najmniejsz|pierwsz)\w*", outl) and not how:
            how = "najmniejszą/pierwszą (sprawdź dokładnie!)"
        add("ties", "Wiele poprawnych odpowiedzi / remisy",
            f"Gdy kilka odpowiedzi jest równie dobrych, wypisz {how or 'tę, której wymaga treść'}. "
            "Przy porównywaniu z przykładem Twoja odpowiedź może się różnić i nadal być poprawna.", "info", "git-branch")
    elif re.search(r"(jesli|gdy|jezeli) (\w+ ){0,3}(kilka|wiele|wiecej)", low):
        add("ties", "Remisy",
            "Treść opisuje sytuację remisu (kilka równych kandydatów) — sprawdź, który wybrać.", "info", "git-branch")
    rs = [x for x in ({re.sub(r"\s*[-+*·].*$", "", str(r.get(k3) or "")) for r in struct.get("items") or []
                       for k3 in ("repeat", "length")} | ({struct.get("vertex_var")} if struct.get("vertex_var") else set()))
          if x and isinstance((sb.get(x) or {}).get("max"), (int, float))]
    smax = max([sb[x]["max"] for x in rs] or [0])
    mo = re.search(r"(?:wypisa\w*|wypisz|sklada\w* sie z|powin\w+ (?:sie )?(?:znalezc|znajdowac|zawierac))\s+(?:dokladnie\s+)?"
                   r"([a-z])\s+(?:wierszy|linii|lini|liczb)", outl)
    if mo and isinstance((sb.get(mo.group(1)) or {}).get("max"), (int, float)):
        smax = max(smax, sb[mo.group(1)]["max"])        # dużo WYJŚCIA (np. n ≤ 10^6 wierszy) też wymaga szybkiego I/O
    if smax >= 2 * 10**5:
        add("fast-io", "Dużo danych → szybkie wejście/wyjście",
            "Na początku main: `ios::sync_with_stdio(false); cin.tie(nullptr);` i wypisuj '\\n' zamiast endl.",
            "info", "zap")
    if any((v.get("min") is not None and v["min"] < 0) for k, v in cons.items()) or \
            re.search(r"co do modul\w*|wartos\w* bezwzgl\w*|liczb\w* (?:calkowit\w* )?(?:moga byc )?ujemn", low):
        add("negative", "Liczby mogą być ujemne",
            "W C++ (-7) % 3 == -1 i (-7) / 2 == -3 — przy modulo/dzieleniu ujemnych uważaj. Nie inicjuj maksimum zerem!",
            "warn", "triangle-alert")
    zero = [k for k, v in cons.items() if v.get("min") == 0 and ST.base_var(k) in ("m", "k", "q", "n")]
    if zero:
        add("zero-size", f"{zero[0]} może być równe 0",
            f"{zero[0]} = 0 jest dopuszczalne (np. graf bez krawędzi, brak zapytań) — sprawdź, czy program to obsłuży.",
            "info", "info")
    if re.search(r"rzeczywist|zmiennoprzecink|z dokladnoscia|miejsc\w* po przecinku|cyfr\w* po przecinku|po kropce", low):
        mm = re.search(r"(\d+)\s+(?:miejsc|cyfr)\w* po (przecinku|kropce)", low)
        add("float", "Liczby rzeczywiste",
            "Używaj `double` (lub `long double`), wypisuj `fixed << setprecision(" + (mm.group(1) if mm else "k") +
            ")`, a porównuj z tolerancją (|a − b| < 1e-9).", "warn", "calculator")
    if any(it.get("kind") == "string" for it in fmt) and re.search(r"spacj\w*|odstep\w*|bialych znak", low) and \
            re.search(r"(tekst|napis|zdani|artykul|ciag)\w* (\w+ ){0,4}(moze zawierac|zawiera\w*|ze|oraz) spacj|"
                      r"(liter|znakow)\w* (\w+ ){0,6}oraz spacji", low):
        add("getline", "Napis ze spacjami",
            "Tekst może zawierać spacje — czytaj `getline(cin, s)` (po `cin >> n` zrób `cin.ignore()`).", "warn", "file-text")
    rep_until = [it for it in fmt if str(it.get("repeat", "")).startswith("until:") or it.get("repeat") == "EOF"]
    if rep_until or re.search(r"(konczy|zakonczon\w*) (dane|wejscie)|do konca (pliku|wejscia)|konczy dane wejsciowe", low):
        add("eof", "Nie wiadomo ile danych",
            "Liczba wierszy nie jest podana — czytaj w pętli `while (cin >> x)` albo do wiersza kończącego.",
            "info", "refresh-cw")
    graphy = kind in ("graph", "tree")
    if kind == "graph" and not re.search(r"parami rozn|zadne dwa \w+ nie sa polaczone wiecej|co najwyzej jedn\w* (?:drog|krawedz)|"
                                         r"nie powtarzaj|bez multikrawedzi|unikaln", low):
        if re.search(r"moze (istniec|byc) (\w+ )?(wiele|kilka) (drog|krawedzi|polaczen)|krawedzi\w* wielokrotn|multikrawedz|"
                     r"moga sie powtarzac", low) or any("wielokrotne" in n for n in struct.get("notes") or []):
            add("multi-edges", "Krawędzie wielokrotne",
                "Między tą samą parą wierzchołków może być kilka krawędzi — nie zakładaj, że są unikalne.", "warn", "git-compare")
    elif re.search(r"moze (istniec|byc) (\w+ )?(wiele|kilka) (drog|krawedzi|polaczen)|krawedzi\w* wielokrotn|multikrawedz",
                   low):
        add("multi-edges", "Krawędzie wielokrotne",
            "Między tą samą parą wierzchołków może być kilka krawędzi — nie zakładaj, że są unikalne.", "info", "git-compare")
    if any(r["type"] == "neq" for r in parsed.get("relations") or []) or re.search(r"nie ma petel|bez petel", low):
        add("no-loops", "Brak pętli (a ≠ b)", "Krawędź nie łączy wierzchołka z nim samym — nie musisz tego obsługiwać.",
            "ok", "check")
    elif kind == "graph" and re.search(r"petl\w*|a\s*=\s*b\s+jest\s+(?:mozliw|dopuszcz)", low):
        add("self-loops", "Możliwe pętle (a = b)", "Krawędź może łączyć wierzchołek z nim samym — obsłuż to.", "warn", "refresh-cw")
    if re.search(r"nie(koniecznie)? (jest )?spojn|moze byc niespojn|moze sie (skladac|rozpadac)", low):
        add("disconnected", "Graf może być niespójny",
            "Nie wszystkie wierzchołki muszą być osiągalne — przejdź pętlą po wszystkich i obsłuż nieosiągalne.",
            "warn", "network")
    elif kind == "graph" and re.search(r"odleglos|sciezk|dojsc|dojech|dotrze|osiagal|najkrotsz|przejsc|trasa|drog\w* z",
                                       low) and not re.search(r"\bspojn\w*|da sie dojsc z kazdego|da sie dotrzec|"
                                                              r"polaczon\w* (?:ze soba )?(?:bezposrednio )?lub posrednio", low):
        add("disconnected", "Spójność nie jest zagwarantowana",
            "Treść nie mówi, że graf jest spójny — wierzchołki mogą być nieosiągalne (sprawdź, co wtedy wypisać).",
            "info", "network")
    vmax = (sb.get(struct.get("vertex_var") or "") or {}).get("max") or nmax
    if vmax and vmax >= 10**5 and graphy:
        add("deep-recursion", "Głęboka rekurencja",
            f"Ścieżka może mieć ~{ST.fmt_value(vmax)} wierzchołków — rekurencyjny DFS ma głębokość n. "
            "Na OI stos jest duży, ale lokalnie może się przepełnić — w razie problemów użyj DFS ze stosem.",
            "info", "layers")
    # wartość jako zakres indeksów (n ≤ 10^9 numerów, ale tylko q operacji) → nie zrobisz tablicy rozmiaru n
    sizes = set(_structural_sizes(parsed))
    for r in vrows:
        if r["kind"] == "value" and isinstance(r["max"], int) and r["max"] > 5 * 10**7 and r["base"] not in sizes and \
                any(ST.base_var(k2) != r["base"] and str(v2.get("raw") or "").replace(" ", "").endswith("≤" + r["base"])
                    for k2, v2 in cons.items()) and kind not in ("values",):
            add("big-index", f"{r['base']} ≤ {r['max_fmt']} — tablica rozmiaru {r['base']} się nie zmieści",
                f"Numery sięgają {r['max_fmt']}, ale danych jest mało — użyj map/set albo kompresji współrzędnych.",
                "warn", "database")
            break
    mem = parsed.get("memory_limit") or ""
    mm = re.match(r"(\d+)\s*([MG])", mem)
    if mm and smax:
        nmax = smax
        limit_mb = int(mm.group(1)) * (1024 if mm.group(2) == "G" else 1)
        arrays = max(1, sum(1 for r in struct.get("items") or [] if r["role"] in ("array", "column", "rows", "parents",
                                                                                   "tree_edges", "edges", "intervals",
                                                                                   "points")))
        need_int = nmax * 4 * arrays * (2 if graphy else 1) / 2**20
        if limit_mb <= 64 and (need_int * 2 > limit_mb * 0.5 or (limit_mb <= 32 and nmax >= 10**6)):
            add("memory", f"Mały limit pamięci ({mem})",
                f"{ST.fmt_value(nmax)} × {arrays} {'tablica' if arrays == 1 else 'tablic'} int to ≈ {need_int:.0f} MB "
                f"(long long: ≈ {need_int * 2:.0f} MB). Używaj int tam, gdzie się da, i nie trzymaj zbędnych kopii.",
                "warn", "database")
    tl = parsed.get("time_limit")
    if tl and tl <= 0.5:
        add("tight-time", f"Krótki limit czasu ({tl} s)",
            "Szybkie wejście, unikaj endl i niepotrzebnych map/setów.", "warn", "timer")
    if re.search(r"leksykograficzn", low):
        add("lexico", "Porządek leksykograficzny",
            "Porównuj jak w słowniku (std::string ma gotowe operator<).", "info", "list-tree")
    fmt_notes = []
    if re.search(r"oddzielon\w* (pojedyncz\w* )?(spacj|odstep)|rozdzielon\w* (pojedyncz\w* )?(spacj|odstep)", outl):
        fmt_notes.append("wartości rozdzielone spacjami")
    if re.search(r"kazd\w* w (osobnym|oddzielnym) wierszu|w osobnych wierszach|po jednej w (kazdym )?wierszu|"
                 r"w oddzielnej lini|w osobnej lini|w (i|kolejnym)-?t?\w* wierszu", outl):
        fmt_notes.append("każda odpowiedź w osobnym wierszu")
    if re.search(r"pust\w* (wiersz|linie|linia)|wiersz powinien byc pusty", outl):
        fmt_notes.append("pusty wiersz też trzeba wypisać — nie pomijaj go")
    if re.search(r"rosnac|malejac|niemalejac|w kolejnosci", outl):
        fmt_notes.append("zachowaj wymaganą kolejność (" + ", ".join(sorted(set(re.findall(r"(?:nie)?(?:rosnac|malejac)\w*", outl)))
                                                                     or ["wg treści"]) + ")")
    if re.search(r"dwukropek|\(:\)", outl):
        fmt_notes.append("dokładne znaki z treści (np. dwukropek po numerze)")
    if fmt_notes:
        add("output-format", "Format wyjścia", "Uważaj: " + "; ".join(dict.fromkeys(fmt_notes)) +
            ". Sprawdzarki OI zwykle ignorują nadmiarowe spacje na końcu linii.", "info", "align-left")
    if (parsed.get("meta") or {}).get("interactive") or re.search(r"interakc|interaktywn", low):
        add("interactive", "Zadanie interaktywne",
            "Po każdym pytaniu zrób `cout << flush` (albo endl) i czytaj odpowiedź sprawdzaczki.", "warn", "message-square")
    order = {"danger": 0, "warn": 1, "info": 2, "ok": 3}
    out.sort(key=lambda x: order.get(x["severity"], 2))
    return out


# ============================================================================================ słowniczek
GLOSSARY = [
    ("graf", r"\bgraf\w*", "Zbiór wierzchołków połączonych krawędziami.", "graf lista sąsiedztwa"),
    ("wierzchołek / krawędź", r"wierzcho[lł]k\w*|kraw[eę]d[zź]\w*",
     "Wierzchołek to „punkt” grafu (miasto, osoba), krawędź łączy dwa wierzchołki (droga, znajomość).", "reprezentacja grafu"),
    ("graf skierowany", r"skierowan\w*|jednokierunkow\w*", "Krawędź a → b można przejść tylko w jedną stronę.",
     "graf skierowany"),
    ("drzewo", r"\bdrzew(?!\w* przedzia)\w*", "Spójny graf bez cykli; ma dokładnie n − 1 krawędzi.", "drzewo dfs"),
    ("korzeń / liść", r"korze[nń]\w*|li[sś][cć]\w*", "Korzeń to wyróżniony wierzchołek drzewa, liść — wierzchołek bez dzieci.",
     "drzewo ukorzenione"),
    ("ścieżka", r"[sś]cie[zż]k\w*", "Ciąg wierzchołków, w którym kolejne są połączone krawędziami.", "najkrótsza ścieżka"),
    ("cykl", r"\bcykl\w*", "Ścieżka, która wraca do punktu startowego.", "wykrywanie cyklu"),
    ("spójność", r"sp[oó]jn\w*", "Graf jest spójny, gdy z każdego wierzchołka da się dojść do każdego innego.",
     "spójne składowe"),
    ("waga krawędzi", r"\bwag[aię]\w*", "Liczba przypisana krawędzi (długość, koszt, czas).", "dijkstra"),
    ("podciąg", r"podci[aą]g\w*", "Elementy wybrane z ciągu z zachowaniem kolejności (niekoniecznie sąsiednie).",
     "najdłuższy rosnący podciąg"),
    ("spójny fragment", r"sp[oó]jn\w* fragment\w*|podtablic\w*|sp[oó]jn\w* podci[aą]g\w*",
     "Kolejne, sąsiednie elementy ciągu: a[l], a[l+1], …, a[r].", "sumy prefiksowe"),
    ("podsłowo", r"pods[lł]ow\w*", "Spójny fragment napisu (kolejne litery).", "wyszukiwanie wzorca kmp"),
    ("permutacja", r"permutacj\w*", "Ciąg zawierający każdą z liczb 1…n dokładnie raz.", "permutacje next_permutation"),
    ("palindrom", r"palindrom\w*", "Napis czytany tak samo od przodu i od tyłu (np. „kajak”).", "palindrom"),
    ("anagram", r"anagram\w*", "Napis złożony z tych samych liter w innej kolejności.", "anagram"),
    ("prefiks / sufiks", r"prefiks\w*|sufiks\w*", "Prefiks to początek napisu (ciągu), sufiks — jego koniec.",
     "funkcja prefiksowa"),
    ("inwersja", r"inwersj\w*", "Para indeksów i < j, dla której a[i] > a[j].", "liczba inwersji"),
    ("NWD", r"\bNWD\b|najwi[eę]ksz\w* wsp[oó]ln\w* dzielnik\w*", "Największy wspólny dzielnik — std::gcd(a, b).", "nwd"),
    ("NWW", r"\bNWW\b|najmniejsz\w* wsp[oó]ln\w* wielokrotno[sś]\w*", "Najmniejsza wspólna wielokrotność: a / gcd(a,b) * b.",
     "nww"),
    ("liczba pierwsza", r"liczb\w* pierwsz\w*|przest[eę]pn\w*", "Liczba > 1 podzielna tylko przez 1 i samą siebie.",
     "sito eratostenesa"),
    ("dzielnik", r"dzielnik\w*", "d jest dzielnikiem n, gdy n % d == 0.", "dzielniki"),
    ("modulo", r"modulo|reszt\w* z dzielenia", "Reszta z dzielenia (operator %).", "arytmetyka modulo"),
    ("silnia", r"silni\w*", "n! = 1 · 2 · … · n — rośnie bardzo szybko.", "silnia"),
    ("dwumian Newtona", r"dwumian\w*|symbol\w* newtona|n po k", "Liczba sposobów wyboru k elementów z n.", "dwumian newtona"),
    ("przedział", r"przedzia[lł]\w*", "Zakres indeksów lub liczb [a, b] (zwykle włącznie z końcami).", "drzewo przedziałowe"),
    ("mediana", r"median\w*", "Środkowy element po posortowaniu.", "sortowanie mediana"),
    ("porządek leksykograficzny", r"leksykograficzn\w*", "Kolejność jak w słowniku.", "porównywanie napisów"),
    ("niemalejąco / nierosnąco", r"niemalej[aą]c\w*|nierosn[aą]c\w*",
     "Niemalejąco: każdy kolejny ≥ poprzedni (dopuszczalne równe).", "sortowanie"),
    ("macierz / tablica 2D", r"macierz\w*|tablic\w* dwuwymiarow\w*", "Tablica liczb w wierszach i kolumnach: T[i][j].",
     "vector dwuwymiarowy"),
    ("plansza / sąsiednie pola", r"plansz\w*|s[aą]siedni\w* pol\w*|siatk\w*",
     "Siatka pól; sąsiednie pola to zwykle te stykające się bokiem (4 kierunki).", "bfs na siatce"),
    ("kolejka", r"kolejk\w*", "Struktura FIFO: pierwszy wchodzi — pierwszy wychodzi (std::queue).", "kolejka"),
    ("stos", r"\bstos\w*", "Struktura LIFO: ostatni wchodzi — pierwszy wychodzi (std::stack).", "stos"),
    ("zbiór / multizbiór", r"multizbi[oó]r\w*|zbi[oó]r\w*|zbior\w*", "Zbiór bez powtórzeń (std::set); multizbiór z powtórzeniami.",
     "set"),
    ("XOR", r"\bxor\b|⊕|alternatyw\w* wykluczaj\w*", "Bitowa różnica symetryczna (operator ^): a ^ a = 0.", "operacje bitowe"),
    ("system dwójkowy", r"dw[oó]jkow\w*|binarn\w*|bit\w*", "Zapis liczby w podstawie 2.", "liczby binarne bitset"),
    ("ciąg Fibonacciego", r"fibonacci\w*", "F(0)=0, F(1)=1, F(n)=F(n−1)+F(n−2).", "fibonacci"),
    ("potęga", r"pot[eę]g\w*", "a^k — dla dużych k licz szybkim potęgowaniem.", "szybkie potęgowanie"),
    ("sortowanie topologiczne", r"topologiczn\w*", "Ustawienie wierzchołków DAG-u tak, by każda krawędź szła „do przodu”.",
     "sortowanie topologiczne"),
]


def glossary(parsed: dict) -> list[dict]:
    sec = parsed["sections"]
    full = " ".join(sec.get(k, "") for k in ("tresc", "wejscie", "wyjscie", "uwagi"))
    out = []
    for term, pat, definition, query in GLOSSARY:
        m = re.search(pat, full, re.I)
        if not m:
            continue
        links = []
        if SG is not None:
            try:
                idx = SG._kb_index()
                if idx is not None:
                    for r in idx.search(query, k=2):
                        if r["score"] >= 1.5:
                            links.append({"id": r["id"], "title": r["payload"]["title"]})
            except Exception:  # noqa: BLE001
                links = []
        out.append({"term": term, "definition": definition, "found": m.group(0), "kb": links[:2]})
        if len(out) >= 14:
            break
    return out


# ============================================================================================ wyjście
def describe_output(parsed: dict) -> dict:
    txt = parsed["sections"].get("wyjscie", "")
    sents = _sentences(txt)
    items = []
    low = T.strip_accents(txt).lower()
    if re.search(r"jedn\w* liczb\w*|liczb\w* calkowit\w*", low) and len(sents) <= 2:
        items.append("jedna liczba")
    if re.search(r"\bTAK\b|\bNIE\b", txt):
        items.append("TAK / NIE")
    words = [w for w in answer_words(txt) if w not in ("TAK", "NIE")]
    for w in words[:3]:
        items.append(f"„{w}”")
    if re.search(r"dla kazdego (zapytania|testu|zestawu)|w (\w+ )?kolejnych wierszach|kazd\w* w osobnym", low):
        items.append("jedna odpowiedź na wiersz")
    if re.search(r"w jednym wierszu|oddzielon\w* spacj", low):
        items.append("wartości w jednym wierszu, rozdzielone spacjami")
    mm = re.search(r"(\d+)\s+(?:miejsc|cyfr)\w* po (?:przecinku|kropce)", low)
    if mm:
        items.append(f"{mm.group(1)} cyfr po przecinku")
    return {"text": txt, "sentences": sents, "items": items}


# ============================================================================================ całość
def explain(statement: str) -> dict:
    p = ST.parse(statement)
    if not any(p["sections"].get(k) for k in ("tresc", "wejscie", "wyjscie")) and not p["samples"]:
        raise ApiError("Nie rozpoznałem treści zadania — wklej pełny tekst (z sekcjami Wejście/Wyjście).")
    struct = structure(p)
    task = task_type(p, struct)
    given, goal = given_and_goal(p)
    cx = complexity_block(p, struct, task) if SG is not None else {}
    sub = SG.complexity_info(p).get("subtasks") if SG is not None else None
    return {
        "title": p["title"],
        "meta": p.get("meta"),
        "limits": {"time": p.get("time_limit"), "memory": p.get("memory_limit")},
        "brief": brief(p, struct, task),
        "task_type": task,
        "structure": {k: struct[k] for k in ("kind", "label", "flags", "notes", "queries", "query_count", "verified")}
        | {"items": [{k: r.get(k) for k in ("line", "kind", "role", "text", "vars", "repeat", "length")}
                     for r in struct["items"]]},
        "summary": summarize(p),
        "given": given,
        "goal": goal,
        "input": {"text": p["sections"].get("wejscie", ""), "format": describe_format(p, struct),
                  "verified": p.get("format_check", {}).get("ok", False)},
        "output": describe_output(p),
        "variables": variables_table(p, struct),
        "samples": p["samples"],
        "walkthrough": walkthrough(p, struct),
        "pitfalls": pitfalls(p, struct, task),
        "subtasks": sub or [{"id": s.get("id"), "points": s.get("points"), "conditions": s.get("conditions")}
                            for s in p.get("subtasks") or []],
        "complexity": cx,
        "glossary": glossary(p),
        "keywords": p.get("keywords"),
        "warnings": p.get("warnings"),
        "sections": p["sections"],
    }


def handle_explain(body: dict) -> dict:
    st = (body.get("statement") or body.get("text") or "").strip()
    if not st:
        raise ApiError("Wklej treść zadania (albo upuść PDF).")
    if len(st) > 400_000:
        raise ApiError("Treść jest za długa (maks. 400 tys. znaków).")
    return explain(st)


def handle_parse(body: dict) -> dict:
    st = (body.get("statement") or body.get("text") or "").strip()
    if not st:
        raise ApiError("Wklej treść zadania.")
    p = ST.parse(st)
    p["reader_cpp"] = ST.cpp_reader(p["input_format"])["code"] if p["input_format"] else ""
    return {"parsed": p}


ROUTES = {
    "POST /api/taskexplain": handle_explain,
    "POST /api/statement/parse": handle_parse,
}

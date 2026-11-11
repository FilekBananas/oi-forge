"""Parsery stron oioioi (SIO2: Szkopuł, szkolne serwery SIO2, …) — tylko `html.parser` ze stdlib.

Funkcje są czyste (HTML → dane), więc testuje się je na zapisanych stronach. Obsługują interfejs po angielsku
i po polsku (nagłówki tabel „Submission time/Problem/Status/Score” albo „Czas zgłoszenia/Zadanie/Status/Wynik”).
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
# Elementy, które przeglądarka domyka niejawnie, gdy zaczyna się kolejny taki sam (uproszczenie HTML5).
_AUTOCLOSE = {  # tag: (co domyka, granice, za które nie wychodzimy)
    "p": ({"p"}, {"div", "section", "article", "td", "th", "li", "body", "blockquote", "form"}),
    "li": ({"li"}, {"ul", "ol", "menu"}),
    "tr": ({"tr", "td", "th"}, {"table", "tbody", "thead", "tfoot"}),
    "td": ({"td", "th"}, {"tr", "table"}),
    "th": ({"td", "th"}, {"tr", "table"}),
    "option": ({"option"}, {"select", "datalist"}),
    "dt": ({"dt", "dd"}, {"dl"}),
    "dd": ({"dt", "dd"}, {"dl"}),
    "tbody": ({"thead", "tbody", "tfoot", "tr", "td", "th"}, {"table"}),
    "thead": ({"thead", "tbody", "tfoot", "tr", "td", "th"}, {"table"}),
}
_BLOCK = frozenset("""address article aside blockquote body dd details div dl dt fieldset figcaption figure footer form h1 h2
h3 h4 h5 h6 header hr li main nav ol p pre section table tbody thead tfoot tr ul br""".split())
_SKIP_TEXT = frozenset("script style noscript template svg head".split())


# ---------------------------------------------------------------------------------------------- mini-DOM
class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []          # Node | str
        self.parent = parent

    # --- atrybuty
    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def has_class(self, cls: str) -> bool:
        return cls in self.classes

    # --- przeszukiwanie
    def iter(self):
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            for ch in reversed(n.children):
                if isinstance(ch, Node):
                    stack.append(ch)

    def find_all(self, tag: str | None = None, cls: str | None = None, id_re: str | None = None,
                 pred=None) -> list["Node"]:
        rx = re.compile(id_re) if id_re else None
        out = []
        for n in self.iter():
            if n is self:
                continue
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.classes:
                continue
            if rx and not rx.search(n.attrs.get("id") or ""):
                continue
            if pred and not pred(n):
                continue
            out.append(n)
        return out

    def find(self, tag: str | None = None, cls: str | None = None, id_re: str | None = None, pred=None):
        for n in self.find_all(tag, cls, id_re, pred):
            return n
        return None

    def ancestor(self, tag: str) -> "Node | None":
        p = self.parent
        while p is not None and p.tag != tag:
            p = p.parent
        return p

    # --- tekst
    def text(self, sep: str = " ") -> str:
        parts: list[str] = []
        _collect_text(self, parts)
        return re.sub(r"\s+", " ", sep.join(parts)).strip()

    def links(self) -> list[tuple[str, str]]:
        return [(a.get("href") or "", a.text()) for a in self.find_all("a") if a.get("href")]

    def __repr__(self) -> str:  # pragma: no cover - tylko do debugowania
        return f"<{self.tag} {self.attrs}>"


def _collect_text(node: Node, parts: list[str]) -> None:
    for ch in node.children:
        if isinstance(ch, str):
            parts.append(ch)
        elif ch.tag not in _SKIP_TEXT:
            _collect_text(ch, parts)


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        rule = _AUTOCLOSE.get(tag)
        if rule:
            closes, bounds = rule
            # domknij niejawnie np. poprzedni <td>, ale nie wychodź poza bieżący wiersz/tabelę/listę
            cut = None
            for i in range(len(self.stack) - 1, 0, -1):
                t = self.stack[i].tag
                if t in bounds:
                    break
                if t in closes:
                    cut = i
            if cut is not None:
                del self.stack[cut:]
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    tb = _TreeBuilder()
    try:
        tb.feed(html or "")
        tb.close()
    except Exception:  # noqa: BLE001 - parser nigdy nie może wywalić synchronizacji
        pass
    return tb.root


def _doc(html_or_node) -> Node:
    return html_or_node if isinstance(html_or_node, Node) else parse_html(html_or_node)


# ---------------------------------------------------------------------------------------------- pomocnicze
_C_LINK = re.compile(r"^(?:https?://[^/]+)?/c/([^/?#]+)/?$")
_SUB_LINK = re.compile(r"(?:/c/([^/]+))?/s/(\d+)/?")      # bez /c/<id>/ = zgłoszenie z archiwum zadań (problemset)
_PSET_LINK = re.compile(r"/problemset/problem/([^/?#]+)/?")
_PROB_LINK = re.compile(r"/c/([^/]+)/p/([^/?#]+)/?")
_PROB_PAGE = re.compile(r"/c/([^/]+)/p/([^/?#]+)/(?:\d+/?)?$")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def norm_label(s: str) -> str:
    s = (s or "").strip().lower()
    for a, b in zip("ąćęłńóśźż", "acelnoszz"):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s)


def split_problem_cell(text: str) -> tuple[str, str]:
    """„Nazwa zadania (skrót)” → (nazwa, skrót). Bez nawiasu: (tekst, "")."""
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"^(.*\S)\s*\(([^()]+)\)$", t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return t, ""


def parse_score(text: str) -> float | None:
    """„100”, „100.00”, „100 / 100”, „ 8 ” → liczba punktów; puste/„—” → None."""
    t = (text or "").strip()
    if not t:
        return None
    nums = _NUM.findall(t)
    if not nums:
        return None
    v = float(nums[0].replace(",", "."))
    if len(nums) >= 2 and "/" in t:            # „7 / 10” — przeskaluj do 100
        mx = float(nums[1].replace(",", "."))
        if mx > 0 and mx != 100:
            return round(100.0 * v / mx, 2)
    return v


ACCEPTED_STATUSES = {"ok", "accepted", "zaakceptowano", "zaakceptowane", "zaakceptowany", "ac", "poprawne",
                     "correct", "passed"}


def is_accepted(row: dict) -> bool:
    """Wynik 100 pkt; w konkursach ACM (bez punktów) — status OK/Accepted/Zaakceptowano."""
    sc = row.get("score")
    if sc is not None:
        return sc >= 99.999
    st = norm_label(row.get("status") or "")
    st = re.sub(r"[^a-z: ]", "", st).strip()
    if st in ACCEPTED_STATUSES:
        return True
    # klasa CSS np. "submission--OK" (bez „INI_”) też oznacza zaakceptowane
    css = row.get("status_class") or ""
    return bool(re.search(r"submission--OK\b", css)) and row.get("has_score_column") is False


def clean_round(raw: str | None) -> str | None:
    """„Sumy prefiksowe (6 October 2025, 12:00 - )” → „Sumy prefiksowe”."""
    if not raw:
        return None
    t = re.sub(r"\s+", " ", raw).strip()
    t2 = re.sub(r"\s*\((?=[^()]*\d)[^()]*\)\s*$", "", t)
    return t2 or t


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", header, re.I)
    if m:
        try:
            return unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8")
        except (LookupError, UnicodeDecodeError):
            return unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"((?:[^"\\]|\\.)*)"', header, re.I)
    if m:
        return m.group(1).replace('\\"', '"')
    m = re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    if m:
        return m.group(1).strip()
    return None


def page_param(url: str) -> int | None:
    try:
        q = parse_qs(urlparse(url).query)
        return int(q.get("page", [None])[0]) if q.get("page") else None
    except (ValueError, TypeError):
        return None


def parse_pagination(html_or_node) -> dict:
    """{current, last, next} na podstawie `ul.pagination` (linki `?page=N`)."""
    doc = _doc(html_or_node)
    cur, pages, nxt = 1, set(), None
    for ul in doc.find_all("ul", cls="pagination"):
        for li in ul.find_all("li"):
            a = li.find("a")
            if li.has_class("active"):
                try:
                    cur = int(li.text())
                except ValueError:
                    pass
            if a is not None:
                p = page_param(a.get("href") or "")
                if p:
                    pages.add(p)
    pages.add(cur)
    if any(p > cur for p in pages):
        nxt = min(p for p in pages if p > cur)
    return {"current": cur, "last": max(pages), "next": nxt}


# ---------------------------------------------------------------------------------------------- konkursy
def parse_contests(html_or_node) -> list[dict]:
    """Lista konkursów z /contest/ (tabela) i z menu wyboru konkursu: [{id, name, url}]."""
    doc = _doc(html_or_node)
    found: dict[str, dict] = {}
    order: list[str] = []

    def add(cid, name, prio):
        cid = unquote(cid)
        name = re.sub(r"\s+", " ", name or "").strip()
        if cid not in found:
            found[cid] = {"id": cid, "name": name or cid, "url": f"/c/{cid}/", "_p": prio}
            order.append(cid)
        elif name and (found[cid]["_p"] > prio or found[cid]["name"] == cid):
            found[cid].update(name=name, _p=prio)

    for table in doc.find_all("table"):
        for a in table.find_all("a"):
            m = _C_LINK.match(a.get("href") or "")
            if m:
                add(m.group(1), a.text(), 0)
    for a in doc.find_all("a"):
        m = _C_LINK.match(a.get("href") or "")
        if m:
            add(m.group(1), a.text(), 1 if a.has_class("dropdown-item") else 2)
    out = []
    for cid in order:
        d = found[cid]
        d.pop("_p", None)
        out.append(d)
    return out


def current_contest(html_or_node) -> dict | None:
    """Konkurs aktywny na stronie (nazwa z przełącznika konkursów)."""
    doc = _doc(html_or_node)
    el = doc.find("div", cls="contest-name")
    return {"name": el.text()} if el is not None else None


# ---------------------------------------------------------------------------------------------- zgłoszenia
_HEAD_MAP = [
    ("time", ("submission time", "czas zgloszenia", "czas zgłoszenia", "data zgloszenia", "submission date", "date",
              "time", "czas", "data")),
    ("problem", ("problem", "zadanie", "task")),
    ("status", ("status", "wynik testow", "result")),
    ("score", ("score", "wynik", "punkty", "points", "pkt")),
    ("kind", ("kind", "rodzaj", "typ")),
    ("details", ("details", "szczegoly", "szczegóły")),
]


def _head_key(label: str) -> str | None:
    lab = norm_label(label)
    if not lab:
        return None
    for key, names in _HEAD_MAP:
        for n in names:
            if lab == norm_label(n):
                return key
    for key, names in _HEAD_MAP:
        for n in names:
            if norm_label(n) in lab:
                return key
    return None


def _find_submission_table(doc: Node) -> Node | None:
    cands = [t for t in doc.find_all("table") if t.has_class("submission") and not t.has_class("table-report")]
    for t in cands:
        if t.find("thead") is not None or t.find("th") is not None:
            return t
    # brak klasy — szukaj tabeli z nagłówkami „Problem/Zadanie” i „Status”
    for t in doc.find_all("table"):
        heads = {_head_key(th.text()) for th in t.find_all("th")}
        if "problem" in heads and "status" in heads and t.get("id") != "submission-status-table":
            return t
    return cands[0] if cands else None


def parse_submissions(html_or_node, base_url: str = "") -> dict:
    """Strona „Moje zgłoszenia” (/c/<id>/submissions/?page=N).

    Zwraca {has_table, rows: [{submission_id, time, problem_name, problem_short, status, score, kind, url,
    status_class, has_score_column}], pagination, next_page}. Brak tabeli = brak zgłoszeń."""
    doc = _doc(html_or_node)
    table = _find_submission_table(doc)
    pag = parse_pagination(doc)
    if table is None:
        return {"has_table": False, "rows": [], "pagination": pag, "next_page": None}
    heads = []
    thead = table.find("thead")
    head_row = (thead.find("tr") if thead is not None else None) or table.find("tr")
    if head_row is not None:
        heads = [_head_key(th.text()) for th in head_row.children if isinstance(th, Node) and th.tag in ("th", "td")]
    has_score = "score" in heads
    rows = []
    seen = set()
    for tr in table.find_all("tr"):
        if tr is head_row or tr.ancestor("thead") is not None:
            continue
        tds = [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
        if not tds:
            continue
        sid = None
        m = re.match(r"report(\d+)row$", tr.get("id") or "")
        if m:
            sid = m.group(1)
        link_url = None
        link_contest = None
        for href, _ in tr.links():
            mm = _SUB_LINK.search(href)
            if mm and (not sid or mm.group(2) == sid):
                sid = sid or mm.group(2)
                link_url = href
                link_contest = mm.group(1) or ""
                break
        if not sid:
            continue
        rec = {"submission_id": sid, "time": "", "problem_name": "", "problem_short": "", "status": "",
               "score": None, "kind": "", "url": urljoin(base_url, link_url) if link_url else "",
               "status_class": "", "has_score_column": has_score,
               "contest_id": link_contest}       # "" = archiwum zadań (problemset), None = nieznany
        by_id = {}
        for td in tds:
            mi = re.match(r"submission\d+-([\w-]+)$", td.get("id") or "")
            if mi:
                by_id[mi.group(1)] = td
        cells: dict[str, Node] = {}
        if by_id:
            cells["time"] = by_id.get("link")
            cells["problem"] = by_id.get("problem-instance") or by_id.get("problem")
            cells["status"] = by_id.get("status")
            cells["score"] = by_id.get("score")
            cells["kind"] = by_id.get("kind")
        if heads and len(tds) == len(heads):
            for key, td in zip(heads, tds):
                if key and cells.get(key) is None:
                    cells[key] = td
        if cells.get("time") is not None:
            rec["time"] = cells["time"].text()
        if cells.get("problem") is not None:
            pnode = cells["problem"]
            name, short = split_problem_cell(pnode.text())
            pl = [h for h, _ in pnode.links() if _PROB_LINK.search(h)]
            if pl and not short:
                short = _PROB_LINK.search(pl[0]).group(2)
            ps = [h for h, _ in pnode.links() if _PSET_LINK.search(h)]
            if ps and not short:
                short = _PSET_LINK.search(ps[0]).group(1)
            rec["problem_name"], rec["problem_short"] = name, short
        if cells.get("status") is not None:
            rec["status"] = cells["status"].text()
            rec["status_class"] = cells["status"].get("class") or ""
        if cells.get("score") is not None:
            rec["score"] = parse_score(cells["score"].text())
            if rec["score"] is None:
                rec["has_score_column"] = has_score
        if cells.get("kind") is not None:
            rec["kind"] = cells["kind"].text()
        if sid in seen:
            continue
        seen.add(sid)
        rows.append(rec)
    return {"has_table": True, "rows": rows, "pagination": pag, "next_page": pag["next"]}


def parse_submission_detail(html_or_node) -> dict:
    """Szczegóły zgłoszenia (/c/<id>/s/<sid>/): zadanie, data, status, wynik, linki do kodu."""
    doc = _doc(html_or_node)
    out = {"problem_name": "", "problem_short": "", "problem_url": "", "time": "", "status": "", "score": None,
           "download_url": "", "source_url": "", "submission_id": ""}
    for href, _ in doc.links():
        if re.search(r"/s/\d+/download/?$", href) and not out["download_url"]:
            out["download_url"] = href
        if re.search(r"/s/\d+/source/?$", href) and not out["source_url"]:
            out["source_url"] = href
    h1 = doc.find("h1")
    if h1 is not None:
        m = re.search(r"(\d{3,})", h1.text())
        if m:
            out["submission_id"] = m.group(1)
    table = doc.find("table", id_re=r"^submission-status-table$") or doc.find("table")
    if table is not None:
        heads = [_head_key(th.text()) for th in table.find_all("th")]
        body_tr = None
        for tr in table.find_all("tr"):
            if tr.find("td") is not None:
                body_tr = tr
                break
        if body_tr is not None:
            tds = [c for c in body_tr.children if isinstance(c, Node) and c.tag == "td"]
            for key, td in zip(heads, tds):
                if key == "problem":
                    out["problem_name"], out["problem_short"] = split_problem_cell(td.text())
                    a = td.find("a")
                    if a is not None:
                        out["problem_url"] = a.get("href") or ""
                elif key == "time":
                    out["time"] = td.text()
                elif key == "status":
                    out["status"] = td.text()
                elif key == "score":
                    out["score"] = parse_score(td.text())
    return out


def extract_source_from_page(html_or_node) -> str:
    """Awaryjnie: kod z podglądu „Show code” (/s/<sid>/source/) — tekst z <pre>/<code>."""
    doc = _doc(html_or_node)
    best = ""
    for tag in ("pre", "code"):
        for n in doc.find_all(tag):
            parts: list[str] = []
            _collect_text(n, parts)
            t = "".join(parts)
            if len(t) > len(best):
                best = t
    if not best:
        # tabela z numerami linii (pygments „linenos table”)
        td = doc.find("td", cls="code")
        if td is not None:
            parts = []
            _collect_text(td, parts)
            best = "".join(parts)
    return unescape(best)


# ---------------------------------------------------------------------------------------------- zadania i rundy
def parse_problems(html_or_node, base_url: str = "") -> dict:
    """Lista zadań (/c/<id>/p/?page=N). Wiersze bez linku do zadania = nazwa rundy (temat!).

    Zwraca {problems: [{short, name, round, round_name, url, cells}], next_page}. `round` = surowy tekst
    („Sumy prefiksowe (6 October 2025, 12:00 - )” — jak w problems.json), `round_name` = sam temat."""
    doc = _doc(html_or_node)
    out = []
    current_round = None
    tables = [t for t in doc.find_all("table") if t.find("a", pred=lambda a: bool(_PROB_PAGE.search(a.get("href")
                                                                                                         or "")))]
    tables = [t for t in tables if not any(t2 is not t and t.ancestor("table") is t2 for t2 in tables)]
    for table in tables:
        for tr in table.find_all("tr"):
            if tr.ancestor("thead") is not None or tr.find("th") is not None and tr.find("td") is None:
                continue
            tds = [c for c in tr.children if isinstance(c, Node) and c.tag == "td"]
            if not tds:
                continue
            plinks = [(h, t) for h, t in tr.links() if _PROB_PAGE.search(h)]
            if not plinks:
                strong = tr.find("strong")
                em = tr.find("em")
                if strong is not None:
                    raw = strong.text()
                    if em is not None and em.text():
                        raw = f"{raw} {em.text()}"
                else:
                    raw = tr.text()
                raw = re.sub(r"\s+", " ", raw).strip()
                if raw and (tr.has_class("problemlist-subheader") or len(tds) == 1):
                    current_round = raw
                continue
            href, name = plinks[0]
            m = _PROB_LINK.search(href)
            short_from_url = unquote(m.group(2))
            short = tds[0].text() if tds and tds[0].find("a") is None and tds[0].text() else (tr.get("id") or "")
            short = short or short_from_url
            cells = [td.text() for td in tds]
            out.append({"short": short, "name": re.sub(r"\s+", " ", name).strip(), "round": current_round,
                        "round_name": clean_round(current_round), "url": urljoin(base_url, href),
                        "url_short": short_from_url, "cells": cells})
    pag = parse_pagination(doc)
    return {"problems": out, "next_page": pag["next"], "pagination": pag}


# ---------------------------------------------------------------------------------------------- logowanie
def parse_login_form(html_or_node) -> dict | None:
    """Formularz logowania Django: {action, fields (ukryte), user_field, pass_field, csrf}. None = brak."""
    doc = _doc(html_or_node)
    forms = [f for f in doc.find_all("form") if f.find("input", pred=lambda i: (i.get("type") or "").lower()
                                                     == "password") is not None]
    if not forms:
        return None
    # preferuj główny formularz (nie ten z menu nawigacji)
    forms.sort(key=lambda f: (f.get("id") == "navbar-login", 0 if f.find("input", pred=lambda i: i.get("name")
                                                                            == "next") is not None else 1))
    f = forms[0]
    fields, user_field, pass_field = {}, None, None
    for inp in f.find_all("input"):
        name = inp.get("name")
        typ = (inp.get("type") or "text").lower()
        if not name:
            continue
        if typ == "password" and not pass_field:
            pass_field = name
        elif typ in ("text", "email") and not user_field:
            user_field = name
        elif typ == "hidden":
            fields[name] = inp.get("value") or ""
        elif typ in ("checkbox",) and inp.get("checked") is not None:
            fields[name] = inp.get("value") or "on"
    return {"action": f.get("action") or "", "method": (f.get("method") or "post").lower(), "fields": fields,
            "user_field": user_field or "username", "pass_field": pass_field or "password",
            "csrf": fields.get("csrfmiddlewaretoken", "")}


def is_logged_in(html_or_node) -> bool:
    """Zalogowany = jest link/akcja wylogowania i nie ma formularza logowania w menu."""
    doc = _doc(html_or_node)
    for n in doc.iter():
        if n.tag in ("a", "form", "button"):
            for attr in ("href", "data-post-url", "action"):
                v = n.get(attr) or ""
                if re.search(r"/logout/?$", v):
                    return True
    return False


def login_error(html_or_node) -> str | None:
    doc = _doc(html_or_node)
    for cls in ("errorlist", "alert-danger", "invalid-feedback"):
        for n in doc.find_all(cls=cls):
            if n.get("id") == "issue-report-errors":
                continue
            t = n.text()
            if t:
                return t
    return None


def username(html_or_node) -> str | None:
    doc = _doc(html_or_node)
    el = doc.find(id_re=r"^username$")
    if el is not None:
        t = el.text()
        if t and norm_label(t) not in ("zaloguj sie", "log in", "login"):
            return t
    return None


# ---------------------------------------------------------------------------------------------- treści zadań
def statement_root(doc: Node) -> Node:
    for pred in (lambda n: n.tag == "section" and n.has_class("main-content"),
                 lambda n: n.get("id") in ("problem-statement", "problem_statement"),
                 lambda n: n.tag == "article", lambda n: n.tag == "main", lambda n: n.tag == "body"):
        el = doc.find(pred=pred)
        if el is not None:
            return el
    return doc


def html_to_text(html_or_node, root: Node | None = None) -> str:
    """Tekst treści zadania z HTML: akapity i nagłówki w osobnych liniach, <pre> bez zmian, bez menu/stopki."""
    doc = _doc(html_or_node)
    node = root or statement_root(doc)
    out: list[str] = []

    def walk(n: Node, in_pre: bool):
        for ch in n.children:
            if isinstance(ch, str):
                out.append(ch if in_pre else re.sub(r"\s+", " ", ch))
                continue
            t = ch.tag
            if t in _SKIP_TEXT or t in ("nav", "header", "footer", "form", "button") or ch.has_class("modal") \
                    or ch.has_class("d-print-none"):
                continue
            if t == "img":
                alt = ch.get("alt") or ""
                if alt:
                    out.append(alt)
                continue
            if t == "br":
                out.append("\n")
                continue
            block = t in _BLOCK
            if block:
                out.append("\n\n" if t in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table") else "\n")
            if t == "li":
                out.append("- ")
            if t in ("td", "th"):
                out.append(" ")
            walk(ch, in_pre or t == "pre")
            if block:
                out.append("\n\n" if t in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table") else "\n")

    walk(node, False)
    txt = "".join(out)
    lines = [ln.rstrip() if "\t" in ln or ln.startswith("  ") else ln.strip() for ln in txt.split("\n")]
    txt = "\n".join(lines)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return unescape(txt).strip() + "\n"


def statement_title(html_or_node) -> str | None:
    doc = _doc(html_or_node)
    root = statement_root(doc)
    h1 = root.find("h1")
    return h1.text() if h1 is not None else None

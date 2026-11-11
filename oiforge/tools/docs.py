"""Dokumentacja offline OI Forge — drzewo kategorii, czytnik artykułu (ze spisem treści) i wyszukiwarka.

Trasy:
- GET /api/docs/tree               → {categories: [{name, count, levels, articles: [{id, title, level, summary, qa_count, tags}]}], total}
- GET /api/docs/article?id=        → {article, toc: [{anchor, title, kind, depth}], related, neighbors: {prev, next}, stats}
- GET /api/docs/search?q=&k=       → {results: [{id, title, category, level, summary, snippet, anchor, matched, score}], did_you_mean}

Wyszukiwanie używa tego samego indeksu jednostek co asystent (oiforge.tools.assistant): trafienie w sekcję,
pytanie QA albo funkcję daje kotwicę (`anchor`), więc czytnik może przewinąć do właściwego miejsca.
"""
from __future__ import annotations

import re

from oiforge.core import kb
from oiforge.core import search as S
from oiforge.core import text as T
from oiforge.server_api import ApiError

CATEGORY_ORDER = ["C++ język", "STL kontenery", "STL algorytmy", "Wejście/wyjście", "Liczby i typy", "Pułapki",
                  "OI praktyka", "Grafy", "Drzewa", "Struktury danych", "Techniki", "Programowanie dynamiczne",
                  "Teksty", "Matematyka", "Geometria", "Teoria gier", "Narzędzia"]
CATEGORY_ICONS = {
    "C++ język": "code-xml", "STL kontenery": "blocks", "STL algorytmy": "wand-sparkles", "Wejście/wyjście": "square-terminal",
    "Liczby i typy": "binary", "Pułapki": "triangle-alert", "OI praktyka": "trophy", "Grafy": "network",
    "Drzewa": "git-branch", "Struktury danych": "layers", "Techniki": "zap", "Programowanie dynamiczne": "grid-3x3",
    "Teksty": "align-left", "Matematyka": "sigma", "Geometria": "target", "Teoria gier": "brain-circuit",
    "Narzędzia": "hammer",
}
LEVELS = {1: "podstawowy", 2: "średni", 3: "zaawansowany", 4: "ekspercki", 5: "ekspercki"}
_HEADING_RX = re.compile(r"^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$", re.M)


def _cat_key(name: str) -> tuple:
    return (CATEGORY_ORDER.index(name) if name in CATEGORY_ORDER else len(CATEGORY_ORDER), name)


def _meta(a: dict) -> dict:
    return {"id": a["id"], "title": a.get("title", a["id"]), "level": a.get("level"),
            "level_label": LEVELS.get(a.get("level"), ""), "summary": a.get("summary", ""),
            "qa_count": len(a.get("qa") or []), "tags": (a.get("tags") or [])[:6], "category": a.get("category", "Inne")}


def tree() -> dict:
    cats: dict[str, list[dict]] = {}
    for a in kb.articles():
        cats.setdefault(a.get("category") or "Inne", []).append(a)
    out = []
    for name in sorted(cats, key=_cat_key):
        arts = sorted(cats[name], key=lambda a: (a.get("level") or 9, a.get("title", "")))
        levels: dict[int, int] = {}
        for a in arts:
            levels[a.get("level") or 0] = levels.get(a.get("level") or 0, 0) + 1
        out.append({"name": name, "icon": CATEGORY_ICONS.get(name, "book-open"), "count": len(arts), "levels": levels,
                    "articles": [_meta(a) for a in arts]})
    return {"categories": out, "total": sum(c["count"] for c in out)}


def toc(a: dict) -> list[dict]:
    """Spis treści: nagłówki z markdownu, tytuły przykładów kodu, tabele i stałe sekcje artykułu."""
    items: list[dict] = []
    body = a.get("body") or []
    if body:
        items.append({"anchor": "b0", "title": "Wprowadzenie", "kind": "intro", "depth": 0})
    for i, b in enumerate(body):
        if not isinstance(b, dict):
            continue
        typ = b.get("type")
        if typ in ("text", "tip", "warn"):
            for j, m in enumerate(_HEADING_RX.finditer(b.get("md") or "")):
                items.append({"anchor": f"b{i}-h{j}", "title": _clean(m.group(2)), "kind": "heading",
                              "depth": max(0, len(m.group(1)) - 2)})
        elif typ == "code" and b.get("title"):
            items.append({"anchor": f"b{i}", "title": _clean(b["title"]), "kind": "code", "depth": 0})
        elif typ == "table" and b.get("title"):
            items.append({"anchor": f"b{i}", "title": _clean(b["title"]), "kind": "table", "depth": 0})
    if a.get("functions"):
        items.append({"anchor": "functions", "title": "Najważniejsze funkcje", "kind": "section", "depth": 0})
    if a.get("pitfalls"):
        items.append({"anchor": "pitfalls", "title": "Pułapki", "kind": "section", "depth": 0})
    if a.get("qa"):
        items.append({"anchor": "qa", "title": f"Pytania i odpowiedzi ({len(a['qa'])})", "kind": "section", "depth": 0})
    if a.get("related"):
        items.append({"anchor": "related", "title": "Powiązane tematy", "kind": "section", "depth": 0})
    return items


def _clean(s: str) -> str:
    return re.sub(r"[`*_]", "", s or "").strip()


def article(aid: str) -> dict:
    a = kb.get(aid or "")
    if not a:
        raise ApiError("Nie znaleziono artykułu.", 404)
    art = {k: v for k, v in a.items() if not k.startswith("_")}
    art["level_label"] = LEVELS.get(a.get("level"), "")
    related = []
    for rid in a.get("related") or []:
        r = kb.get(rid)
        if r:
            related.append(_meta(r))
    same = sorted((x for x in kb.articles() if x.get("category") == a.get("category")),
                  key=lambda x: (x.get("level") or 9, x.get("title", "")))
    idx = next((i for i, x in enumerate(same) if x["id"] == a["id"]), -1)
    nb = {"prev": _meta(same[idx - 1]) if idx > 0 else None,
          "next": _meta(same[idx + 1]) if 0 <= idx < len(same) - 1 else None}
    code_blocks = sum(1 for b in a.get("body") or [] if isinstance(b, dict) and b.get("type") == "code")
    words = sum(len(str(b.get("md") or "").split()) for b in a.get("body") or [] if isinstance(b, dict))
    stats = {"qa": len(a.get("qa") or []), "functions": len(a.get("functions") or []),
             "pitfalls": len(a.get("pitfalls") or []), "code": code_blocks,
             "reading_min": max(1, round((words + 30 * code_blocks) / 180))}
    return {"article": art, "toc": toc(a), "related": related, "neighbors": nb, "stats": stats,
            "category_icon": CATEGORY_ICONS.get(a.get("category"), "book-open")}


def search(q: str, k: int = 20) -> dict:
    q = (q or "").strip()
    if not q:
        return {"results": []}
    try:
        from oiforge.tools import assistant as _A
        st = _A.engine().state()
        corr = st.index.correct(q)
        query = corr["text"] if corr["changed"] else q
        res = _A.retrieve(st, query, intent="howto", k=80)
        qterms = T.terms(query)
        best_unit: dict[str, tuple] = {}
        for f, u, h in res["units"]:
            if u.get("aid") and u["aid"] not in best_unit:
                best_unit[u["aid"]] = (f, u, h)
        out = []
        for score, aid in res["articles"][:k]:
            a = st.articles.get(aid)
            if not a or aid not in best_unit:
                continue
            f, u, h = best_unit[aid]
            text_ = _A._plain(u.get("text") or "") if u["kind"] != "article" else a.get("summary", "")
            out.append(dict(_meta(a), snippet=S.highlight(text_ or a.get("summary", ""), h.get("matched") or qterms, 200),
                            anchor=u.get("anchor") or "", hit_kind=u["kind"],
                            hit_label=_A.KIND_LABELS.get(u["kind"], u["kind"]), hit_title=u["title"],
                            matched=h.get("matched") or [], score=round(score, 3)))
        return {"results": out,
                "did_you_mean": {"text": corr["text"], "fixes": corr["fixes"]} if corr["changed"] else None}
    except ImportError:
        # awaryjnie: bazowy indeks serwera
        from oiforge import server
        res = server.kb_index().search(q, k=k)
        return {"results": [dict(r["payload"], snippet=r["payload"].get("summary", ""), anchor="",
                                 matched=r["matched"], score=r["score"]) for r in res]}


# ============================================================================================ HTTP
def handle_tree(_q: dict) -> dict:
    return tree()


def handle_article(q: dict) -> dict:
    res = article(q.get("id") or "")
    try:  # „przeczytane artykuły” w ścieżce nauki (moduł journal, jeśli jest)
        from oiforge.tools import journal
        journal.log_event("kb_view", {"id": q.get("id")})
    except Exception:  # noqa: BLE001
        pass
    return res


def handle_search(q: dict) -> dict:
    try:
        k = max(1, min(60, int(q.get("k") or 20)))
    except ValueError:
        k = 20
    return search(q.get("q") or "", k=k)


ROUTES = {
    "GET /api/docs/tree": handle_tree,
    "GET /api/docs/article": handle_article,
    "GET /api/docs/search": handle_search,
}

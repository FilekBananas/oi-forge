"""Planowanie deklaracji zmiennych lokalnych w C++.

Python ma zasięg funkcyjny, C++ — blokowy. Dla każdej zmiennej (sieci def-use) szukamy najgłębszego bloku,
który obejmuje wszystkie jej użycia, i miejsca deklaracji:
- `init`  — pierwsze użycie to zwykłe przypisanie bezpośrednio w tym bloku → `T x = wartość;`
- `for`   — zmienna pętli używana tylko w pętli → `for (T x : …)` / `for (T i = 0; …)`
- `before`— deklaracja `T x;` przed instrukcją zawierającą pierwsze użycie.
Jeśli blokiem jest ciało pętli, a pierwsze użycie nie jest przypisaniem (wartość „przechodzi” między iteracjami),
deklaracja wędruje piętro wyżej.
"""
from __future__ import annotations

import ast


class Ref:
    __slots__ = ("order", "path", "kind", "node")

    def __init__(self, order, path, kind, node):
        self.order = order
        self.path = path       # krotka kroków (klucz_bloku, indeks_instrukcji)
        self.kind = kind       # simple | unpack | for | def | store | load
        self.node = node


class Decl:
    __slots__ = ("kind", "block", "idx", "loc", "stmt")

    def __init__(self, kind, block, idx, loc, stmt):
        self.kind = kind
        self.block = block
        self.idx = idx
        self.loc = loc
        self.stmt = stmt


ROOT = ("root",)


class UnitPlan:
    def __init__(self, emitter, unit_scope, body, params=()):
        self.em = emitter
        self.an = emitter.an
        self.scope = unit_scope
        self.body = body
        self.refs: dict = {}            # sym -> [Ref]
        self.order = 0
        self.block_stmt: dict = {}      # (klucz, idx) -> instrukcja
        self.block_owner: dict = {}     # klucz -> instrukcja-właściciel (For/While/If…)
        self.decl: dict = {}            # sym -> Decl
        self.init_here: dict[int, set] = {}
        self.before_here: dict[int, list] = {}
        self.for_here: dict[int, set] = {}
        self.params = list(params)
        self.alias: dict = {}
        self._walk_block(body, ROOT, ())
        for s in list(self.refs):
            self._plan(s)

    # ------------------------------------------------------------------ zbieranie odwołań
    def plannable(self, sym) -> bool:
        if sym is None:
            return False
        root = sym.root()
        if root.scope is not self.scope:
            return False
        if root.kind == "param" and sym.base is None:
            return False
        if root.is_global or root.declared_global:
            return False
        if root.kind in ("import", "builtin", "class"):
            return False
        if root.kind == "func" and self.scope.kind == "module":
            return False
        return True

    def _add(self, node, path, kind):
        sym = self.an.sym_of.get(id(node))
        if not self.plannable(sym):
            return
        self.order += 1
        self.refs.setdefault(sym, []).append(Ref(self.order, path, kind, node))

    def _walk_block(self, stmts, key, prefix):
        for idx, s in enumerate(stmts):
            path = prefix + ((key, idx),)
            self.block_stmt[(key, idx)] = s
            self._stmt(s, path)

    def _expr(self, node, path):
        if node is None:
            return
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                if isinstance(sub.ctx, ast.Load):
                    self._add(sub, path, "load")
                else:
                    self._add(sub, path, "store")
            elif isinstance(sub, ast.Lambda) and self.an.scope_of[id(sub)].named_lambda:
                pass

    def _targets(self, t, path, kind):
        if isinstance(t, ast.Name):
            self._add(t, path, kind)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._targets(e, path, "unpack" if kind in ("simple", "unpack") else kind)
        elif isinstance(t, ast.Starred):
            self._targets(t.value, path, kind)
        else:
            self._expr(t, path)

    def _stmt(self, s, path):
        E = self._expr
        if isinstance(s, ast.Assign):
            E(s.value, path)
            simple = len(s.targets) == 1
            for t in s.targets:
                self._targets(t, path, "simple" if simple or isinstance(t, ast.Name) else "store")
            return
        if isinstance(s, ast.AnnAssign):
            E(s.value, path)
            if s.value is not None:
                self._targets(s.target, path, "simple")
            return
        if isinstance(s, ast.AugAssign):
            E(s.value, path)
            if isinstance(s.target, ast.Name):
                self._add(s.target, path, "load")
            else:
                E(s.target, path)
            return
        if isinstance(s, ast.For):
            E(s.iter, path)
            body_key = (id(s), "body")
            self.block_owner[body_key] = s
            self._targets(s.target, path + ((body_key, -1),), "for")
            self._walk_block(s.body, body_key, path)
            if s.orelse:
                k = (id(s), "orelse")
                self.block_owner[k] = s
                self._walk_block(s.orelse, k, path)
            return
        if isinstance(s, ast.While):
            E(s.test, path)
            k = (id(s), "body")
            self.block_owner[k] = s
            self._walk_block(s.body, k, path)
            if s.orelse:
                k2 = (id(s), "orelse")
                self.block_owner[k2] = s
                self._walk_block(s.orelse, k2, path)
            return
        if isinstance(s, ast.If):
            E(s.test, path)
            for f in ("body", "orelse"):
                k = (id(s), f)
                self.block_owner[k] = s
                self._walk_block(getattr(s, f), k, path)
            return
        if isinstance(s, ast.Try) or (hasattr(ast, "TryStar") and isinstance(s, ast.TryStar)):
            for f in ("body", "orelse", "finalbody"):
                k = (id(s), f)
                self.block_owner[k] = s
                self._walk_block(getattr(s, f), k, path)
            for h in s.handlers:
                k = (id(h), "body")
                self.block_owner[k] = s
                if h.name:
                    pass
                self._walk_block(h.body, k, path)
            return
        if isinstance(s, ast.With):
            for it in s.items:
                E(it.context_expr, path)
                if it.optional_vars is not None:
                    self._targets(it.optional_vars, path, "store")
            k = (id(s), "body")
            self.block_owner[k] = s
            self._walk_block(s.body, k, path)
            return
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in s.decorator_list:
                E(d, path)
            # funkcja zagnieżdżona: jej nazwa jest definiowana tutaj, a zmienne z domknięcia
            # muszą być zadeklarowane wcześniej (lambda [&] łapie je w miejscu definicji)
            fsym = self.an.sym_of.get(id(s))
            if self.plannable(fsym):
                self.order += 1
                self.refs.setdefault(fsym, []).append(Ref(self.order, path, "def", s))
            for sub in ast.walk(s):
                if isinstance(sub, ast.Name):
                    sym = self.an.sym_of.get(id(sub))
                    if self.plannable(sym) and sym is not fsym:
                        self.order += 1
                        self.refs.setdefault(sym, []).append(Ref(self.order, path, "load", sub))
            return
        if isinstance(s, ast.ClassDef):
            return
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            E(s.subject, path)
            for i, c in enumerate(s.cases):
                k = (id(c), "body")
                self.block_owner[k] = s
                self._walk_block(c.body, k, path)
            return
        for ch in ast.iter_child_nodes(s):
            if isinstance(ch, ast.expr):
                E(ch, path)

    # ------------------------------------------------------------------ planowanie
    def _is_loop_body(self, key) -> bool:
        if key == ROOT:
            return False
        owner = self.block_owner.get(key)
        return isinstance(owner, (ast.For, ast.While)) and key[1] == "body"

    def _plan(self, sym) -> None:
        refs = sorted(self.refs[sym], key=lambda r: r.order)
        self.refs[sym] = refs
        # najdłuższy wspólny prefiks kluczy bloków
        depth = 0
        while True:
            keys = set()
            ok = True
            for r in refs:
                if len(r.path) <= depth:
                    ok = False
                    break
                keys.add(r.path[depth][0])
            if not ok or len(keys) != 1:
                break
            depth += 1
        j = depth - 1
        first = refs[0]
        while True:
            key = first.path[j][0]
            idx = first.path[j][1]
            direct = len(first.path) == j + 1
            if direct and first.kind in ("simple", "unpack", "def") and idx >= 0:
                self._set(sym, Decl("init", key, idx, first.path[:j + 1], self.block_stmt.get((key, idx))))
                return
            if direct and first.kind == "for" and idx == -1:
                owner = self.block_owner.get(key)
                self._set(sym, Decl("for", key, -1, first.path[:j + 1], owner))
                return
            if self._is_loop_body(key) and j > 0:
                j -= 1
                continue
            if idx < 0:
                if j > 0:
                    j -= 1
                    continue
                idx = 0
            self._set(sym, Decl("before", key, idx, first.path[:j] + ((key, idx),), self.block_stmt.get((key, idx))))
            return

    def _set(self, sym, d: Decl) -> None:
        old = self.decl.get(sym)
        if old is not None:
            self._unset(sym, old)
        self.decl[sym] = d
        if d.stmt is None:
            return
        if d.kind == "init":
            self.init_here.setdefault(id(d.stmt), set()).add(sym)
        elif d.kind == "for":
            self.for_here.setdefault(id(d.stmt), set()).add(sym)
        else:
            lst = self.before_here.setdefault(id(d.stmt), [])
            if sym not in lst:
                lst.append(sym)

    def _unset(self, sym, d: Decl) -> None:
        if d.stmt is None:
            return
        if d.kind == "init":
            self.init_here.get(id(d.stmt), set()).discard(sym)
        elif d.kind == "for":
            self.for_here.get(id(d.stmt), set()).discard(sym)
        else:
            lst = self.before_here.get(id(d.stmt), [])
            if sym in lst:
                lst.remove(sym)

    # ------------------------------------------------------------------ konflikty nazw
    @staticmethod
    def within(inner, outer) -> bool:
        n = len(outer)
        if len(inner) < n:
            return False
        if tuple(inner[:n - 1]) != tuple(outer[:n - 1]):
            return False
        if inner[n - 1][0] != outer[n - 1][0]:
            return False
        return inner[n - 1][1] >= outer[n - 1][1]

    def resolve_conflicts(self, ctype) -> list:
        """Zwraca listę (sym, nowa_nazwa) do przemianowania; scala sieci o tym samym typie."""
        renames = []
        groups: dict[str, list] = {}
        for s in self.decl:
            groups.setdefault(s.root().name, []).append(s)
        for p in self.params:
            if p.root().name in groups:
                groups[p.root().name].append(p)
        for name, syms in groups.items():
            if len(syms) < 2:
                continue
            changed = True
            guard = 0
            while changed and guard < 50:
                guard += 1
                changed = False
                for i in range(len(syms)):
                    for k in range(i + 1, len(syms)):
                        a, b = syms[i], syms[k]
                        la = self._loc(a)
                        lb = self._loc(b)
                        if not (self.within(lb, la) or self.within(la, lb)):
                            continue
                        if ctype(a) == ctype(b):
                            keep, drop = (a, b) if (a in self.params or a.base is None or
                                                    (b not in self.params and self._loc_key(a) <= self._loc_key(b))) \
                                else (b, a)
                            self._merge(keep, drop)
                            syms.remove(drop)
                        else:
                            keep, drop = (a, b) if self._nrefs(a) >= self._nrefs(b) or a in self.params else (b, a)
                            renames.append(drop)
                            syms.remove(drop)
                        changed = True
                        break
                    if changed:
                        break
        return renames

    def _nrefs(self, s) -> int:
        return len(self.refs.get(s, ())) + (100 if s in self.params else 0)

    def _loc(self, s):
        if s in self.params:
            return ((ROOT, -1),)
        return self.decl[s].loc

    def _loc_key(self, s):
        return [(0, i) for _, i in self._loc(s)]

    def _merge(self, keep, drop) -> None:
        self.alias[drop] = keep
        d = self.decl.pop(drop, None)
        if d is not None:
            self._unset(drop, d)
        if keep in self.params:
            self.refs.pop(drop, None)
            return
        self.refs[keep] = sorted(self.refs.get(keep, []) + self.refs.pop(drop, []), key=lambda r: r.order)
        self._plan(keep)

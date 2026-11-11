"""Analiza zasięgów (scope) i przepływu danych dla tłumacza Python → C++.

1. Budowa drzewa zasięgów zgodnie z regułami Pythona (moduł, funkcja, lambda, klasa, comprehension),
   rozwiązanie każdej nazwy (`ast.Name`, `ast.arg`) do obiektu `Symbol`.
2. Analiza osiągających definicji (reaching definitions) w każdej funkcji i w kodzie modułu:
   definicje połączone wspólnymi użyciami tworzą „sieci” (webs). Zmienna z kilkoma niezależnymi
   sieciami (np. `n = input(); n = int(n)` albo kilka pętli `for i in ...`) zostaje rozdzielona
   na osobne symbole — dzięki temu każdy może mieć własny typ C++ i własne miejsce deklaracji.
"""
from __future__ import annotations

import ast
import builtins

BUILTIN_NAMES = set(dir(builtins))

INLINE_SCOPES = ("comp", "lambda")   # zasięgi emitowane w miejscu (comprehension, lambda jako argument)


class Scope:
    def __init__(self, kind: str, node, parent: "Scope | None", name: str = ""):
        self.kind = kind            # module | function | lambda | class | comp
        self.node = node
        self.parent = parent
        self.name = name
        self.bound: dict[str, ast.AST] = {}
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()
        self.params: list[str] = []
        self.children: list[Scope] = []
        self.syms: dict[str, Symbol] = {}
        self.named_lambda = False   # lambda przypisana do nazwy → traktowana jak funkcja
        if parent:
            parent.children.append(self)

    def unit(self) -> "Scope":
        """Najbliższy zasięg emitowany jako osobna funkcja C++ (moduł/def/nazwana lambda)."""
        s = self
        while s.parent is not None and (s.kind == "comp" or (s.kind == "lambda" and not s.named_lambda)):
            s = s.parent
        return s

    def __repr__(self):
        return f"<Scope {self.kind} {self.name}>"


class Symbol:
    _ids = 0

    def __init__(self, name: str, scope: Scope, kind: str = "var"):
        Symbol._ids += 1
        self.id = Symbol._ids
        self.name = name            # nazwa w Pythonie
        self.scope = scope
        self.kind = kind            # var | param | func | class | import | builtin
        self.cpp = name             # nazwa w C++ (ustalana później)
        self.is_global = False      # zmienna modułu używana w funkcjach → globalna w C++
        self.captured = False       # używana w funkcji zagnieżdżonej (domknięcie)
        self.declared_global = False
        self.base: Symbol | None = None   # przy rozdzieleniu na sieci — symbol pierwotny
        self.node = None            # FunctionDef/ClassDef/alias dla func/class/import
        self.qual = ""              # dla import: pełna nazwa, np. "collections.deque"

    def root(self) -> "Symbol":
        return self.base or self

    def __repr__(self):
        return f"<Sym {self.name}#{self.id} {self.kind} in {self.scope.kind}>"


class Analysis:
    """Wynik analizy: zasięgi, mapowanie węzłów na symbole i zasięgi."""

    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.module = Scope("module", tree, None, "<module>")
        self.scope_of: dict[int, Scope] = {}        # id(węzeł tworzący zasięg) -> Scope
        self.sym_of: dict[int, Symbol] = {}         # id(Name/arg/FunctionDef/ClassDef/alias) -> Symbol
        self.parent: dict[int, ast.AST] = {}        # id(węzeł) -> rodzic
        self.node_scope: dict[int, Scope] = {}      # id(Name) -> zasięg, w którym występuje
        self.builtins: dict[str, Symbol] = {}
        self.functions: list[Scope] = []            # zasięgi funkcji (def + nazwane lambdy)
        self.classes: list[Scope] = []
        self.imports: dict[str, str] = {}           # lokalna nazwa -> kwalifikowana (np. "heapq.heappush")
        self.all_names: set[str] = set()
        self.splits: dict[Symbol, list[Symbol]] = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                self.parent[id(ch)] = node
            if isinstance(node, ast.Name):
                self.all_names.add(node.id)
            elif isinstance(node, ast.arg):
                self.all_names.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                self.all_names.add(node.name)
            elif isinstance(node, ast.Attribute):
                self.all_names.add(node.attr)
        self._collect(tree, self.module)
        self._resolve_all()
        self._compute_webs()

    # ------------------------------------------------------------------ pass 1: zasięgi i wiązania
    def _bind(self, scope: Scope, name: str, node) -> None:
        if scope.kind == "comp" and not isinstance(node, ast.Name):
            pass
        scope.bound.setdefault(name, node)

    def _named_lambda(self, node: ast.Lambda) -> bool:
        p = self.parent.get(id(node))
        return isinstance(p, ast.Assign) and len(p.targets) == 1 and isinstance(p.targets[0], ast.Name) and p.value is node

    def _collect(self, node, scope: Scope) -> None:
        C = self._collect
        if isinstance(node, ast.Module):
            for s in node.body:
                C(s, scope)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._bind(scope, node.name, node)
            for d in node.decorator_list:
                C(d, scope)
            for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
                C(d, scope)
            fs = Scope("function", node, scope, node.name)
            self.scope_of[id(node)] = fs
            self.functions.append(fs)
            self._bind_args(node.args, fs)
            for s in node.body:
                C(s, fs)
            return
        if isinstance(node, ast.Lambda):
            for d in node.args.defaults:
                C(d, scope)
            ls = Scope("lambda", node, scope, "<lambda>")
            ls.named_lambda = self._named_lambda(node)
            if ls.named_lambda:
                self.functions.append(ls)
            self.scope_of[id(node)] = ls
            self._bind_args(node.args, ls)
            C(node.body, ls)
            return
        if isinstance(node, ast.ClassDef):
            self._bind(scope, node.name, node)
            for b in node.bases + node.decorator_list:
                C(b, scope)
            cs = Scope("class", node, scope, node.name)
            self.scope_of[id(node)] = cs
            self.classes.append(cs)
            for s in node.body:
                C(s, cs)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            gens = node.generators
            C(gens[0].iter, scope)
            cs = Scope("comp", node, scope, "<comp>")
            self.scope_of[id(node)] = cs
            for i, g in enumerate(gens):
                if i > 0:
                    C(g.iter, cs)
                C(g.target, cs)
                for c in g.ifs:
                    C(c, cs)
            if isinstance(node, ast.DictComp):
                C(node.key, cs)
                C(node.value, cs)
            else:
                C(node.elt, cs)
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self._bind(scope, node.id, node)
            return
        if isinstance(node, ast.NamedExpr):
            C(node.value, scope)
            s = scope
            while s.kind == "comp" and s.parent:
                s = s.parent
            self._bind(s, node.target.id, node.target)
            self.node_scope[id(node.target)] = s
            return
        if isinstance(node, ast.Global):
            scope.global_names.update(node.names)
            return
        if isinstance(node, ast.Nonlocal):
            scope.nonlocal_names.update(node.names)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "*":
                    continue
                local = a.asname or a.name.split(".")[0]
                self._bind(scope, local, a)
                if isinstance(node, ast.ImportFrom):
                    self.imports[local] = f"{node.module or ''}.{a.name}"
                else:
                    self.imports[local] = a.name if a.asname else a.name.split(".")[0]
            return
        if isinstance(node, ast.ExceptHandler):
            if node.type:
                C(node.type, scope)
            if node.name:
                self._bind(scope, node.name, node)
            for s in node.body:
                C(s, scope)
            return
        for ch in ast.iter_child_nodes(node):
            C(ch, scope)

    def _bind_args(self, args: ast.arguments, scope: Scope) -> None:
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            self._bind(scope, a.arg, a)
            scope.params.append(a.arg)
        for a in (args.vararg, args.kwarg):
            if a:
                self._bind(scope, a.arg, a)
                scope.params.append(a.arg)

    # ------------------------------------------------------------------ pass 2: rozwiązywanie nazw
    def symbol(self, scope: Scope, name: str) -> Symbol:
        sym = scope.syms.get(name)
        if sym is None:
            node = scope.bound.get(name)
            kind = "var"
            if isinstance(node, ast.arg):
                kind = "param"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "func"
            elif isinstance(node, ast.ClassDef):
                kind = "class"
            elif isinstance(node, ast.alias):
                kind = "import"
            sym = Symbol(name, scope, kind)
            if kind in ("func", "class", "import"):
                sym.node = node
            if kind == "import":
                sym.qual = self.imports.get(name, name)
            scope.syms[name] = sym
        return sym

    def lookup(self, scope: Scope, name: str) -> Symbol:
        s = scope
        if s.kind == "class" and name in s.bound and name not in s.global_names:
            return self.symbol(s, name)
        if name in s.global_names:
            sym = self.symbol(self.module, name)
            sym.declared_global = True
            return sym
        if name in s.nonlocal_names:
            p = s.parent
            while p is not None:
                if p.kind in ("function", "lambda") and name in p.bound:
                    sym = self.symbol(p, name)
                    sym.captured = True
                    return sym
                p = p.parent
        if name in s.bound and s.kind != "class":
            return self.symbol(s, name)
        p = s.parent
        while p is not None:
            if p.kind == "class":
                p = p.parent
                continue
            if name in p.global_names:
                return self.symbol(self.module, name)
            if name in p.bound:
                return self.symbol(p, name)
            p = p.parent
        if name in self.module.bound:
            return self.symbol(self.module, name)
        b = self.builtins.get(name)
        if b is None:
            b = self.builtins[name] = Symbol(name, self.module, "builtin")
        return b

    def _resolve_all(self) -> None:
        self._resolve(self.tree, self.module)
        # zmienne modułu używane w funkcjach i domknięcia
        for fs in self.functions + self.classes:
            self._mark_crossing(fs)

    def _mark_crossing(self, unit: Scope) -> None:
        for node in ast.walk(unit.node):
            if isinstance(node, ast.Name):
                sym = self.sym_of.get(id(node))
                if not sym or sym.kind in ("builtin",):
                    continue
                if sym.scope is self.module and sym.kind in ("var", "import"):
                    if self.node_scope.get(id(node)) is not None and self.node_scope[id(node)].unit() is not self.module:
                        sym.is_global = True
                elif sym.scope.kind in ("function", "lambda"):
                    here = self.node_scope.get(id(node))
                    if here is not None and here.unit() is not sym.scope.unit():
                        sym.captured = True

    def _resolve(self, node, scope: Scope) -> None:
        R = self._resolve
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.sym_of[id(node)] = self.symbol(scope, node.name) if node.name not in scope.global_names \
                else self.lookup(scope, node.name)
            for d in node.decorator_list + node.args.defaults + [x for x in node.args.kw_defaults if x]:
                R(d, scope)
            fs = self.scope_of[id(node)]
            for a in self._all_args(node.args):
                self.sym_of[id(a)] = self.symbol(fs, a.arg)
            for s in node.body:
                R(s, fs)
            return
        if isinstance(node, ast.Lambda):
            for d in node.args.defaults:
                R(d, scope)
            ls = self.scope_of[id(node)]
            for a in self._all_args(node.args):
                self.sym_of[id(a)] = self.symbol(ls, a.arg)
            R(node.body, ls)
            return
        if isinstance(node, ast.ClassDef):
            self.sym_of[id(node)] = self.symbol(scope, node.name)
            for b in node.bases + node.decorator_list:
                R(b, scope)
            cs = self.scope_of[id(node)]
            for s in node.body:
                R(s, cs)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            gens = node.generators
            R(gens[0].iter, scope)
            cs = self.scope_of[id(node)]
            for i, g in enumerate(gens):
                if i > 0:
                    R(g.iter, cs)
                R(g.target, cs)
                for c in g.ifs:
                    R(c, cs)
            if isinstance(node, ast.DictComp):
                R(node.key, cs)
                R(node.value, cs)
            else:
                R(node.elt, cs)
            return
        if isinstance(node, ast.NamedExpr):
            R(node.value, scope)
            s = self.node_scope.get(id(node.target), scope)
            self.sym_of[id(node.target)] = self.lookup(s, node.target.id)
            self.node_scope[id(node.target)] = scope
            return
        if isinstance(node, ast.Name):
            self.sym_of[id(node)] = self.lookup(scope, node.id)
            self.node_scope[id(node)] = scope
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name != "*":
                    self.sym_of[id(a)] = self.lookup(scope, a.asname or a.name.split(".")[0])
            return
        if isinstance(node, ast.ExceptHandler):
            if node.type:
                R(node.type, scope)
            if node.name:
                self.sym_of[id(node)] = self.lookup(scope, node.name)
            for s in node.body:
                R(s, scope)
            return
        for ch in ast.iter_child_nodes(node):
            R(ch, scope)

    @staticmethod
    def _all_args(args: ast.arguments) -> list:
        out = args.posonlyargs + args.args + args.kwonlyargs
        for a in (args.vararg, args.kwarg):
            if a:
                out = out + [a]
        return out

    # ------------------------------------------------------------------ pass 3: sieci def-use
    def _compute_webs(self) -> None:
        units = [self.module] + [f for f in self.functions]
        for u in units:
            body = u.node.body if not isinstance(u.node, ast.Lambda) else []
            if not body:
                continue
            flow = _Flow(self, u)
            flow.run(body)
            flow.split()


class _Def:
    __slots__ = ("id", "sym", "node")

    def __init__(self, i, sym, node):
        self.id = i
        self.sym = sym
        self.node = node


class _Loop:
    def __init__(self):
        self.breaks: list = []
        self.conts: list = []


class _Flow:
    """Osiągające definicje na strukturalnym AST (if/while/for/break/continue/return/try)."""

    def __init__(self, an: Analysis, unit: Scope):
        self.an = an
        self.unit = unit
        self.defs: list[_Def] = []
        self.parent_uf: list[int] = []
        self.use_defs: dict[int, set] = {}      # id(Name Load) -> {def ids}
        self.def_of_node: dict[int, int] = {}   # id(węzeł definiujący) -> def id
        self.loops: list[_Loop] = []
        self.inline = {id(s.node) for s in self._inline_scopes(unit)}

    def _inline_scopes(self, unit: Scope):
        out = []
        stack = list(unit.children)
        while stack:
            s = stack.pop()
            if s.kind == "comp" or (s.kind == "lambda" and not s.named_lambda):
                out.append(s)
                stack.extend(s.children)
        return out

    # union-find
    def _find(self, x: int) -> int:
        while self.parent_uf[x] != x:
            self.parent_uf[x] = self.parent_uf[self.parent_uf[x]]
            x = self.parent_uf[x]
        return x

    def _union(self, a: int, b: int) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self.parent_uf[max(ra, rb)] = min(ra, rb)

    def _tracked(self, sym: Symbol | None) -> bool:
        return (sym is not None and sym.scope is self.unit and sym.kind in ("var", "param")
                and not sym.is_global and not sym.captured and not sym.declared_global)

    def _new_def(self, sym: Symbol, node) -> int:
        if id(node) in self.def_of_node:          # ten sam węzeł w kolejnej iteracji pętli → ta sama definicja
            return self.def_of_node[id(node)]
        i = len(self.defs)
        self.defs.append(_Def(i, sym, node))
        self.parent_uf.append(i)
        self.def_of_node[id(node)] = i
        return i

    @staticmethod
    def _merge(*states):
        live = [s for s in states if s is not None]
        if not live:
            return None
        out: dict = {}
        for s in live:
            for k, v in s.items():
                out[k] = out.get(k, frozenset()) | v
        return out

    def run(self, body: list) -> None:
        st: dict = {}
        if self.unit.kind in ("function", "lambda"):
            args = self.unit.node.args
            for a in Analysis._all_args(args):
                sym = self.an.sym_of.get(id(a))
                if self._tracked(sym):
                    st[sym] = frozenset({self._new_def(sym, a)})
        self.block(body, st)

    def block(self, stmts, st):
        for s in stmts:
            if st is None:
                st = {}
            st = self.stmt(s, st)
        return st

    # --- wyrażenia
    def use(self, node, st):
        sym = self.an.sym_of.get(id(node))
        if not self._tracked(sym) or st is None:
            return
        ds = st.get(sym, frozenset())
        self.use_defs.setdefault(id(node), set()).update(ds)
        ds = list(ds)
        for d in ds[1:]:
            self._union(ds[0], d)

    def expr(self, node, st):
        if node is None or st is None:
            return st
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self.use(node, st)
            return st
        if isinstance(node, ast.NamedExpr):
            st = self.expr(node.value, st)
            return self.store(node.target, st)
        if isinstance(node, ast.Lambda) and id(node) not in self.inline:
            return st
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.expr_context):
                continue
            st = self.expr(ch, st) if not isinstance(ch, (ast.comprehension, ast.arguments)) else self._comp(ch, st)
        return st

    def _comp(self, ch, st):
        if isinstance(ch, ast.comprehension):
            st = self.expr(ch.iter, st)
            for c in ch.ifs:
                st = self.expr(c, st)
            return st
        if isinstance(ch, ast.arguments):
            for d in ch.defaults:
                st = self.expr(d, st)
        return st

    def store(self, target, st):
        if st is None:
            return st
        if isinstance(target, ast.Name):
            sym = self.an.sym_of.get(id(target))
            if self._tracked(sym):
                st = dict(st)
                st[sym] = frozenset({self._new_def(sym, target)})
            return st
        if isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                st = self.store(e, st)
            return st
        if isinstance(target, ast.Starred):
            return self.store(target.value, st)
        return self.expr(target, st)     # a[i] = ..., obj.x = ... → użycia bazy

    # --- instrukcje
    def stmt(self, s, st):
        E = self.expr
        if isinstance(s, ast.Assign):
            st = E(s.value, st)
            for t in s.targets:
                st = self.store(t, st)
            return st
        if isinstance(s, ast.AnnAssign):
            st = E(s.value, st)
            return self.store(s.target, st) if s.value is not None else st
        if isinstance(s, ast.AugAssign):
            st = E(s.value, st)
            if isinstance(s.target, ast.Name):
                sym = self.an.sym_of.get(id(s.target))
                if self._tracked(sym):
                    old = st.get(sym, frozenset())
                    d = self._new_def(sym, s.target)
                    for o in old:
                        self._union(o, d)
                    self.use_defs.setdefault(id(s.target), set()).update(old)
                    st = dict(st)
                    st[sym] = frozenset({d})
                return st
            return E(s.target, st)
        if isinstance(s, ast.If):
            st = E(s.test, st)
            a = self.block(s.body, dict(st))
            b = self.block(s.orelse, dict(st))
            return self._merge(a, b)
        if isinstance(s, ast.While):
            infinite = isinstance(s.test, ast.Constant) and bool(s.test.value)
            loop = _Loop()
            self.loops.append(loop)
            head = st
            for _ in range(12):
                E(s.test, head)
                loop.conts = []
                end = self.block(s.body, dict(head))
                new = self._merge(head, end, *loop.conts)
                if new == head:
                    break
                head = new
            self.loops.pop()
            exits = [] if infinite else [head]
            if s.orelse and not infinite:
                exits = [self.block(s.orelse, dict(head))]
            return self._merge(*exits, *loop.breaks)
        if isinstance(s, ast.For):
            st = E(s.iter, st)
            loop = _Loop()
            self.loops.append(loop)
            head = st
            for _ in range(12):
                loop.conts = []
                start = self.store(s.target, dict(head))
                end = self.block(s.body, start)
                new = self._merge(head, end, *loop.conts)
                if new == head:
                    break
                head = new
            self.loops.pop()
            out = head
            if s.orelse:
                out = self.block(s.orelse, dict(head))
            return self._merge(out, *loop.breaks)
        if isinstance(s, ast.Break):
            if self.loops:
                self.loops[-1].breaks.append(st)
            return None
        if isinstance(s, ast.Continue):
            if self.loops:
                self.loops[-1].conts.append(st)
            return None
        if isinstance(s, (ast.Return, ast.Raise)):
            for ch in ast.iter_child_nodes(s):
                st = E(ch, st)
            return None
        if isinstance(s, ast.Try) or (hasattr(ast, "TryStar") and isinstance(s, ast.TryStar)):
            start = dict(st)
            end = self.block(s.body, dict(st))
            hin = self._merge(start, end)
            outs = []
            for h in s.handlers:
                hs = dict(hin) if hin is not None else {}
                outs.append(self.block(h.body, hs))
            if s.orelse:
                end = self.block(s.orelse, end)
            res = self._merge(end, *outs)
            if s.finalbody:
                res = self.block(s.finalbody, res if res is not None else dict(hin or {}))
            return res
        if isinstance(s, ast.With):
            for it in s.items:
                st = E(it.context_expr, st)
                if it.optional_vars is not None:
                    st = self.store(it.optional_vars, st)
            return self.block(s.body, st)
        if isinstance(s, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            for d in s.decorator_list:
                st = E(d, st)
            return st
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            st = E(s.subject, st)
            outs = [self.block(c.body, dict(st)) for c in s.cases]
            return self._merge(st, *outs)
        if isinstance(s, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.Pass)):
            return st
        for ch in ast.iter_child_nodes(s):
            st = E(ch, st)
        return st

    # --- podział na sieci
    def split(self) -> None:
        groups: dict[Symbol, dict[int, list[int]]] = {}
        for d in self.defs:
            groups.setdefault(d.sym, {}).setdefault(self._find(d.id), []).append(d.id)
        for sym, webs in groups.items():
            if len(webs) <= 1:
                continue
            roots = sorted(webs)
            new_syms = {}
            for k, r in enumerate(roots):
                if k == 0:
                    new_syms[r] = sym
                    continue
                ns = Symbol(sym.name, sym.scope, "var")
                ns.base = sym
                new_syms[r] = ns
            self.an.splits[sym] = list(new_syms.values())
            for d in self.defs:
                if d.sym is sym:
                    target = new_syms[self._find(d.id)]
                    self.an.sym_of[id(d.node)] = target
            for nid, ds in self.use_defs.items():
                if not ds:
                    continue
                d0 = next(iter(ds))
                if self.defs[d0].sym is sym:
                    self.an.sym_of[nid] = new_syms[self._find(d0)]

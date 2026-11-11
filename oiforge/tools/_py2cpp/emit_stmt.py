"""Emisja instrukcji C++ (mixin Emittera): przypisania, wczytywanie, print, pętle, warunki, funkcje zagnieżdżone."""
from __future__ import annotations

import ast

from .emit_call import CallMixin
from .emit_expr import (E, P_ADD, P_COND, P_EQ, P_MUL, P_POST, P_REL, P_SHIFT, P_UNARY, Unsupported, char_lit,
                        cstr_lit, par)
from .infer import qual_of_call, short
from .signs import const_int
from .types import BOT, CHAR, FLOAT, INT, STR, Ty, List, elem, is_intlike, is_scalar

AUG = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.BitAnd: "&=", ast.BitOr: "|=", ast.BitXor: "^=",
       ast.LShift: "<<=", ast.RShift: ">>="}
PURE_BOUND_FUNCS = {"len", "abs", "min", "max"}


class StmtMixin(CallMixin):
    # ------------------------------------------------------------------ bloki
    def body(self, stmts, main: bool = False, docstring_ok: bool = False) -> None:
        for i, s in enumerate(stmts):
            if docstring_ok and i == 0 and isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and \
                    isinstance(s.value.value, str):
                continue
            if main and isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if main and isinstance(s, ast.Assign) and isinstance(s.value, ast.Lambda) and id(s) in self.skip_stmts:
                continue
            self.stmt(s)

    def decls_before(self, s) -> None:
        if self.plan is None:
            return
        for sym in self.plan.before_here.get(id(s), []):
            if sym in self.consts or sym in self.readline_alias or sym in self.thread_targets:
                continue
            t = self.sym_type(sym)
            if t.k == "tokens":
                continue
            if t.k == "func":
                fi = self.inf.func_by_fid.get(t.extra)
                if fi is not None:
                    self.out(f"function<{self.fn_sig(fi)}> {self.cn(sym)};")
                    continue
            ct = self.sym_ct(sym)
            if ct == "auto":
                ct = self.int_t
            self.out(f"{ct} {self.cn(sym)}{self.default_init(sym)};")

    def stmt(self, s) -> None:
        self.py = getattr(s, "lineno", self.py)
        self.decls_before(s)
        if id(s) in self.skip_stmts:
            if isinstance(s, ast.Assign) and any(self.an.sym_of.get(id(t)) in self.readline_alias
                                                 for t in s.targets if isinstance(t, ast.Name)):
                self.out("// " + self.src(s) + "  → w C++ wystarcza szybkie cin/getline")
            return
        try:
            with self.capture() as box:
                self._stmt(s)
            self.replay(box)
        except Unsupported as u:
            self.todo_stmt(s, u.msg)
        except RecursionError:
            raise
        except Exception as ex:  # noqa: BLE001
            self.todo_stmt(s, f"błąd tłumacza: {ex.__class__.__name__}: {ex}")

    def todo_stmt(self, s, msg: str) -> None:
        self.notes = []
        self.warn("todo", f"Tej instrukcji nie umiem przetłumaczyć ({msg}) — w kodzie C++ została jako komentarz z oryginałem.",
                  s.lineno, "error")
        lo, hi = s.lineno, getattr(s, "end_lineno", s.lineno) or s.lineno
        src = self.src_lines[lo - 1:hi]
        self.out(f"// Python, linia {lo} — nie przetłumaczone ({msg}):", s.lineno)
        for ln in src[:8]:
            self.out("//   " + ln.rstrip(), s.lineno)

    def _stmt(self, s) -> None:
        if isinstance(s, ast.Expr):
            return self.expr_stmt(s)
        if isinstance(s, ast.Assign):
            return self.assign(s)
        if isinstance(s, ast.AnnAssign):
            if s.value is None:
                return
            return self.assign(ast.Assign(targets=[s.target], value=s.value, lineno=s.lineno), orig=s)
        if isinstance(s, ast.AugAssign):
            return self.augassign(s)
        if isinstance(s, ast.If):
            return self.if_stmt(s)
        if isinstance(s, ast.While):
            return self.while_stmt(s)
        if isinstance(s, ast.For):
            return self.for_stmt(s)
        if isinstance(s, ast.Return):
            return self.return_stmt(s)
        if isinstance(s, ast.Break):
            if self.loops and self.loops[-1].get("flag"):
                self.out(f"{self.loops[-1]['flag']} = true;")
            self.out("break;")
            return
        if isinstance(s, ast.Continue):
            self.out("continue;")
            return
        if isinstance(s, ast.Pass):
            return
        if isinstance(s, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self.nested_def(s)
        if isinstance(s, ast.ClassDef):
            raise Unsupported("klasa zdefiniowana wewnątrz funkcji")
        if isinstance(s, ast.Delete):
            return self.delete_stmt(s)
        if isinstance(s, ast.Assert):
            self.out(f"assert({self.cond(s.test).c});")
            return
        if isinstance(s, ast.Raise):
            msg = "wyjątek"
            if isinstance(s.exc, ast.Call) and s.exc.args and isinstance(s.exc.args[0], ast.Constant):
                msg = str(s.exc.args[0].value)
            self.warn("raise", "`raise` → `throw runtime_error(...)` (przerywa program, jeśli nie złapany).", s.lineno,
                      "info")
            self.out(f"throw runtime_error({cstr_lit(msg)});")
            return
        if isinstance(s, ast.Try):
            return self.try_stmt(s)
        if isinstance(s, ast.With):
            self.warn("todo", "`with` — tłumaczony jako zwykły blok (bez menedżera kontekstu).", s.lineno)
            self.out("{")
            with self.indent():
                self.body(s.body)
            self.out("}")
            return
        raise Unsupported(f"instrukcja {s.__class__.__name__}")

    # ------------------------------------------------------------------ wyrażenie jako instrukcja
    def expr_stmt(self, s) -> None:
        v = s.value
        if isinstance(v, ast.Constant):
            if isinstance(v.value, str):
                self.out("// " + v.value.strip().replace("\n", " ")[:100])
            return
        if isinstance(v, ast.Call):
            if self.call_stmt(v):
                return
        e = self.x(v)
        self.out(f"{e.c};")

    def call_stmt(self, c) -> bool:
        """Wywołania-instrukcje: print, append, sort, heappush… Zwraca True, jeśli obsłużone."""
        f = c.func
        q = qual_of_call(self.an, f)
        if q == "print":
            self.print_stmt(c)
            return True
        if q in ("sys.stdout.write", "sys.stderr.write"):
            stream = "cerr" if "stderr" in q else "cout"
            items = self.stream_items(c.args[0])
            self.out(f"{stream} << {self.render_items(items)};")
            return True
        if q in ("sys.setrecursionlimit", "setrecursionlimit"):
            self.recursion = True
            self.info("recursion", "sys.setrecursionlimit: w C++ nie ma limitu rekursji — głębokość ogranicza stos "
                      "(na OI zwykle równy limitowi pamięci; lokalnie OI Forge daje 512 MB stosu).", c.lineno)
            self.out(f"// {self.src(c)} — w C++ głębokość rekursji ogranicza tylko rozmiar stosu")
            return True
        if q in ("threading.stack_size",):
            self.out(f"// {self.src(c)} — niepotrzebne w C++")
            return True
        if q in ("sys.exit", "exit", "quit"):
            self.out(f"{self.x(c).c};")
            return True
        if isinstance(f, ast.Attribute) and f.attr in ("start", "join") and isinstance(f.value, ast.Name):
            s = self.S(f.value)
            if s is not None and s in self.thread_targets:
                if f.attr == "start":
                    self.out(f"{self.thread_targets[s]}();  // wątek z większym stosem → zwykłe wywołanie")
                return True
        if isinstance(f, ast.Attribute) and f.attr == "start" and isinstance(f.value, ast.Call) and \
                qual_of_call(self.an, f.value.func) == "threading.Thread":
            for kw in f.value.keywords:
                if kw.arg == "target":
                    self.out(f"{self.x(kw.value).c}();  // wątek z większym stosem → zwykłe wywołanie")
                    return True
        if q is not None and (short(q) in ("heappush", "heappop", "heapify") and
                              (q.startswith("heapq") or q in ("heappush", "heappop", "heapify"))):
            mode, h = self.heap_target(c.args[0])
            if mode == "pq":
                if short(q) == "heappop":
                    self.out(f"{h}.pop();")
                    return True
                if short(q) == "heapify":
                    self.out(f"// heapify({h}): priority_queue jest kopcem od początku")
                    return True
            return False
        if isinstance(f, ast.Attribute):
            rt = self.T(f.value)
            if rt.k in ("module", "builtin", "class", "file", "stdout", "stderr"):
                return False
            return self.method_stmt(f.value, rt, f.attr, c)
        return False

    def method_stmt(self, recv, rt: Ty, m: str, c) -> bool:
        a = c.args
        k = rt.k
        x = self.x
        if k not in ("list", "dict", "set", "deque", "bot"):
            return False
        r = par(x(recv), P_POST)
        if k in ("list", "bot", "deque"):
            et = elem(rt) if k != "bot" else BOT
            if m == "append":
                v = x(a[0], et, brace=True)
                if v.cstr and et.k == "str":
                    pass
                self.out(f"{r}.push_back({v.c});")
                return True
            if m == "appendleft" and k == "deque":
                self.out(f"{r}.push_front({x(a[0], et, brace=True).c});")
                return True
            if m == "pop" and not a:
                self.out(f"{r}.pop_back();")
                return True
            if m == "popleft" and k == "deque":
                self.out(f"{r}.pop_front();")
                return True
            if m == "pop" and a and k == "list":
                i = const_int(a[0])
                if i == 0:
                    self.info("pop0", "`a.pop(0)` działa w O(n) — dla kolejki użyj collections.deque.", c.lineno)
                if i == -1:
                    self.out(f"{r}.pop_back();")
                else:
                    self.out(f"{r}.erase({r}.begin() + {par(x(a[0]), P_ADD - 1)});")
                return True
            if m in ("extend", "extendleft"):
                src = a[0]
                st = self.T(src)
                if m == "extendleft":
                    v = self.fresh("x")
                    self.out(f"for (const auto& {v} : {self.iterable_code(src)}) {r}.push_front({v});")
                    return True
                if isinstance(src, (ast.List, ast.Tuple)):
                    for e in src.elts:
                        self.out(f"{r}.push_back({x(e, et, brace=True).c});")
                    return True
                if self.lval(src) and st.k in ("list", "str", "deque", "set"):
                    b, e = self.rng(src)
                    self.out(f"{r}.insert({r}.end(), {b}, {e});")
                    return True
                v = self.fresh("x")
                self.out(f"for (const auto& {v} : {self.iterable_code(src)}) {r}.push_back({v});")
                return True
            if m == "insert" and k == "list":
                self.out(f"{r}.insert({r}.begin() + {par(x(a[0]), P_ADD - 1)}, {x(a[1], et, brace=True).c});")
                return True
            if m == "remove":
                self.out(f"{r}.erase(find({r}.begin(), {r}.end(), {x(a[0], et).c}));")
                return True
            if m == "sort" and k == "list":
                self.sort_stmt(r, et, self.kw(c, "key"), self.kw(c, "reverse"))
                return True
            if m == "reverse":
                self.out(f"reverse({r}.begin(), {r}.end());")
                return True
            if m == "clear":
                self.out(f"{r}.clear();")
                return True
            if m == "rotate" and k == "deque":
                n = x(a[0]).c if a else "1"
                self.out(f"if (!{r}.empty()) rotate({r}.begin(), {r}.begin() + pymod(-({n}), (long long){r}.size()), "
                         f"{r}.end());")
                self.use("pymod")
                return True
            return False
        if k == "dict":
            kt, vt = rt.args
            if m == "update":
                src = a[0] if a else None
                if rt.extra == "counter" and src is not None and self.T(src).k != "dict":
                    self.fill_lines(r, src, rt, "counter")
                    return True
                if src is not None:
                    v = self.fresh("kv")
                    self.out(f"for (const auto& {v} : {self.x(src).c}) {r}[{v}.first] = {v}.second;")
                    return True
                return False
            if m == "pop":
                self.out(f"{r}.erase({x(a[0], kt).c});")
                return True
            if m == "clear":
                self.out(f"{r}.clear();")
                return True
            if m == "setdefault":
                if len(a) > 1:
                    self.out(f"{r}.try_emplace({x(a[0], kt).c}, {x(a[1], vt).c});")
                else:
                    self.out(f"{r}[{x(a[0], kt).c}];")
                return True
            if m == "subtract" and rt.extra == "counter":
                v = self.fresh("x")
                self.out(f"for (const auto& {v} : {self.iterable_code(a[0])}) {r}[{v}]--;")
                return True
            return False
        if k == "set":
            et = rt.args[0]
            if m == "add":
                self.out(f"{r}.insert({x(a[0], et, brace=True).c});")
                return True
            if m in ("remove", "discard"):
                self.out(f"{r}.erase({x(a[0], et).c});")
                return True
            if m == "update":
                for src in a:
                    if self.lval(src) and self.T(src).k in ("list", "set", "str", "deque"):
                        b, e = self.rng(src)
                        self.out(f"{r}.insert({b}, {e});")
                    else:
                        v = self.fresh("x")
                        self.out(f"for (const auto& {v} : {self.iterable_code(src)}) {r}.insert({v});")
                return True
            if m == "clear":
                self.out(f"{r}.clear();")
                return True
            if m == "difference_update":
                v = self.fresh("x")
                self.out(f"for (const auto& {v} : {self.iterable_code(a[0])}) {r}.erase({v});")
                return True
            if m == "intersection_update":
                self.use("setops")
                self.out(f"{r} = setInter({r}, {x(a[0], rt).c});")
                return True
            return False
        return False

    # ------------------------------------------------------------------ print
    def render_items(self, items) -> str:
        merged = []
        for it in items:
            if it[0] == "lit" and merged and merged[-1][0] == "lit":
                merged[-1] = ("lit", merged[-1][1] + it[1])
            else:
                merged.append(it)
        out = []
        for kind, v in merged:
            if kind == "lit":
                if v == "":
                    continue
                out.append(char_lit(v) if len(v) == 1 else cstr_lit(v))
            else:
                out.append(v)
        return " << ".join(out)

    def _sep_item(self, node, default):
        if node is None:
            return ("lit", default)
        if isinstance(node, ast.Constant) and node.value is None:
            return ("lit", default)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return ("lit", node.value)
        return ("code", par(self.x(node, STR), P_SHIFT - 1))

    def elem_stream(self, code: str, t: Ty) -> str:
        k = t.k
        if k in ("int", "str", "char"):
            return code
        if k == "bool":
            return f'({code} ? "True" : "False")'
        if k in ("float",) or (k == "inf" and not self.inf_as_int):
            self.use("strcore")
            return f"str({code})"
        if k in ("list", "tuple", "set", "dict", "deque", "opt"):
            self.use("reprcore")
            return f"str({code})"
        if k == "class":
            return f"{code}.toStr()"
        return code

    def print_stmt(self, c) -> None:
        stream = "cout"
        fnode = self.kw(c, "file")
        if fnode is not None:
            fq = qual_of_call(self.an, fnode)
            if fq == "sys.stderr":
                stream = "cerr"
            elif fq != "sys.stdout":
                raise Unsupported("print(file=...) do pliku")
        sep = self._sep_item(self.kw(c, "sep"), " ")
        end = self._sep_item(self.kw(c, "end"), "\n")
        args = c.args
        if not any(isinstance(a, ast.Starred) for a in args):
            items = []
            for i, a in enumerate(args):
                if i:
                    items.append(sep)
                items.extend(self.stream_items(a))
            items.append(end)
            r = self.render_items(items)
            if r:
                self.out(f"{stream} << {r};")
            return
        # z gwiazdką: print(*a), print(n, *a), print(*a, sep='\n')
        state = "none"          # none | some | flag
        flag = None
        pending: list = []

        def flush():
            nonlocal pending
            r = self.render_items(pending)
            if r:
                self.out(f"{stream} << {r};")
            pending = []

        sep_code = self.render_items([sep]) if sep[1] != "" else None
        for a in args:
            if not isinstance(a, ast.Starred):
                if state == "some":
                    pending.append(sep)
                elif state == "flag":
                    flush()
                    if sep_code:
                        self.out(f"if (!{flag}) {stream} << {sep_code};")
                pending.extend(self.stream_items(a))
                state = "some"
                continue
            flush()
            src = a.value
            st = self.T(src)
            et = elem(st)
            if isinstance(src, ast.Call) and self.q(src) == "range":
                cont = self.materialize(src).c
                et = INT
            elif st.k in ("list", "str", "deque") and (self.lval(src) or isinstance(src, ast.Name)):
                cont = self.x(src).c
            else:
                cont = self.iterable_code(src)
                if not cont.isidentifier():
                    tmp = self.fresh("tmp")
                    self.out(f"auto {tmp} = {cont};")
                    cont = tmp
            if state == "none" and st.k in ("list", "str", "deque") and cont.isidentifier():
                i = self.fresh("i")
                el = self.elem_stream(f"{cont}[{i}]", et)
                sep_s = sep[1] if sep[0] == "lit" else None
                if sep_s is not None:
                    self.out(f"for (size_t {i} = 0; {i} < {cont}.size(); {i}++) {stream} << ({i} ? "
                             f"{cstr_lit(sep_s)} : \"\") << {el};")
                else:
                    self.out(f"for (size_t {i} = 0; {i} < {cont}.size(); {i}++) {{ if ({i}) {stream} << "
                             f"{sep_code}; {stream} << {el}; }}")
                state = "flag"
                flag = f"{cont}.empty()"
                continue
            v = self.fresh("x")
            el = self.elem_stream(v, et)
            if state == "some":
                self.out(f"for (const auto& {v} : {cont}) {stream} << {sep_code + ' << ' if sep_code else ''}{el};")
            else:
                if flag is None or not flag.endswith("first_"):
                    fl = self.fresh("first_")
                    if flag is not None:
                        self.out(f"bool {fl} = {flag};")
                    else:
                        self.out(f"bool {fl} = true;")
                    flag = fl
                self.out(f"for (const auto& {v} : {cont}) {{ if (!{flag}) {stream} << {sep_code or chr(34) * 2}; "
                         f"{stream} << {el}; {flag} = false; }}")
                state = "flag"
        pending.append(end)
        flush()

    # ------------------------------------------------------------------ przypisania
    def target_syms(self, t):
        if isinstance(t, ast.Name):
            return [t]
        if isinstance(t, (ast.Tuple, ast.List)):
            out = []
            for e in t.elts:
                out.extend(self.target_syms(e.value if isinstance(e, ast.Starred) else e))
            return out
        return []

    def is_decl_here(self, s, name_node) -> bool:
        if self.plan is None:
            return False
        raw = self.an.sym_of.get(id(name_node))
        return raw in self.plan.init_here.get(id(s), set())

    def assign(self, s, orig=None) -> None:
        sid = id(orig or s)
        value = s.value
        targets = s.targets
        vt = self.T(value)
        if vt.k == "tokens":
            self.out(f"// {self.src(orig or s)}: kolejne tokeny czytane wprost z cin (nextToken())")
            return
        if len(targets) > 1 and (isinstance(value, ast.Constant) or (isinstance(value, ast.UnaryOp) and
                                                                     isinstance(value.operand, ast.Constant))):
            for t in targets:
                self._assign_one(orig or s, t, value)
            return
        if len(targets) > 1:
            # a = b = wartość
            first = targets[-1]
            self._assign_one(orig or s, first, value)
            src = ast.copy_location(ast.Name(id="__tmp__", ctx=ast.Load()), value)
            fe = self.x(first)
            for t in targets[:-1]:
                if isinstance(t, ast.Name):
                    sym = self.S(t)
                    if self.is_decl_here(orig or s, t):
                        self.out(f"{self.sym_ct(sym)} {self.cn(sym)} = {fe.c};")
                    else:
                        self.out(f"{self.cn(sym)} = {fe.c};")
                else:
                    self.out(f"{self.x(t).c} = {fe.c};")
            del src
            return
        t = targets[0]
        del sid
        self._assign_one(orig or s, t, value)

    def _assign_one(self, s, t, value) -> None:
        if isinstance(t, (ast.Tuple, ast.List)):
            return self.assign_unpack(s, t, value)
        if isinstance(t, ast.Name):
            return self.assign_name(s, t, value)
        if isinstance(t, ast.Subscript):
            return self.assign_subscript(s, t, value)
        if isinstance(t, ast.Attribute):
            fe = self.x(t)
            ft = self.T(t)
            v = self.x(value, ft if ft.k != "bot" else None, brace=True)
            self.out(f"{fe.c} = {v.c};")
            return
        if isinstance(t, ast.Starred):
            raise Unsupported("przypisanie do *x")
        raise Unsupported("cel przypisania")

    def read_tokens(self, targets_code: list[str], s, conv: str, src) -> None:
        if src is not None and src.args:
            self.out(f"cout << {self.x(src.args[0]).c} << flush;")
        self.out("cin >> " + " >> ".join(targets_code) + ";")
        if id(s) in self.io_token_needs_skip:
            self.use("skipLine")
            self.out("skipLine();")

    def assign_name(self, s, t: ast.Name, value) -> None:
        sym = self.S(t)
        name = self.cn(sym)
        tt = self.sym_type(sym)
        decl = self.is_decl_here(s, t)
        ct = self.sym_ct(sym)
        dprefix = f"{ct} " if decl else ""
        if sym in self.consts:
            return
        if self.fi is None and self.plan is not None and sym.root().is_global and isinstance(value, ast.Constant) \
                and self._first_global_store(s, sym) and s in self.tree.body:
            self.global_inits[sym] = self.x(value, tt).c
            return
        # odczyt jednego tokenu: n = int(input())
        sc = self.read_scalar_pattern(value)
        if sc is not None and not self.in_try:
            if decl:
                self.out(f"{ct} {name};")
            self.read_tokens([name], s, sc[0], sc[1])
            return
        # kopiec jako priority_queue
        if self.is_pure_heap(sym):
            ht = self.heap_type(tt)
            if isinstance(value, ast.List) and not value.elts:
                if decl:
                    self.note("heapq = kopiec MINIMALNY → greater<>")
                self.out(f"{ht} {name};" if decl else f"{name} = {ht}();")
                return
            if isinstance(value, ast.List) and not any(isinstance(e, ast.Starred) for e in value.elts):
                if decl:
                    self.note("heapq = kopiec MINIMALNY → greater<>")
                self.out(f"{ht} {name};" if decl else f"{name} = {ht}();")
                for e in value.elts:
                    self.out(f"{name}.push({self.x(e, elem(tt), brace=True).c});")
                self.info("heap", "heapq (kopiec minimalny) → priority_queue z greater<> (domyślnie w C++ jest "
                          "maksymalny!).", s.lineno)
                return
            b, e = self.range_pair(value)
            if decl:
                self.out(f"{ht} {name}({b}, {e});")
            else:
                self.out(f"{name} = {ht}({b}, {e});")
            self.info("heap", "heapq (kopiec minimalny) → priority_queue z greater<> (domyślnie w C++ jest "
                      "maksymalny!).", s.lineno)
            return
        if sym in self.iter_src:
            src = self.cn(self.resolve_alias(self.iter_src[sym]))
            self.out(f"size_t {name} = 0;  // iter({src}): indeks następnego elementu" if decl else f"{name} = 0;")
            return
        # nazwana lambda w funkcji
        if isinstance(value, ast.Lambda):
            return self.named_lambda(s, sym, value, decl)
        # x = q.popleft() / x = a.pop() / x = heappop(h)
        if self._pop_assign(s, [t], value, decl):
            return
        # komprehensje i wypełnienia
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return self.assign_comp(s, sym, name, value, decl, tt)
        if isinstance(value, ast.Call):
            q = self.q(value)
            if q == "sorted" and value.args:
                self.sorted_into(ct if decl else None, name, value)
                return
            if q in ("collections.Counter", "Counter") and value.args:
                self.out(f"{ct} {name};" if decl else f"{name}.clear();")
                self.fill_lines(name, value.args[0], tt, "counter")
                return
            if q in ("set", "frozenset") and value.args and not (self.lval(value.args[0]) or
                                                                 isinstance(value.args[0], (ast.List, ast.Tuple))):
                self.out(f"{ct} {name};" if decl else f"{name}.clear();")
                self.fill_lines(name, value.args[0], tt, "set")
                return
            if q == "dict" and value.args and not (self.T(value.args[0]).k == "dict"):
                self.out(f"{ct} {name};" if decl else f"{name}.clear();")
                self.fill_lines(name, value.args[0], tt, "dict_pairs")
                return
            if q == "list" and len(value.args) == 1 and isinstance(value.args[0], ast.Call) and \
                    self.q(value.args[0]) == "range" and len(value.args[0].args) <= 2 and tt.k == "list":
                ra = value.args[0].args
                lo = self.x(ra[0]).c if len(ra) == 2 else "0"
                hi = self.x(ra[-1])
                size = hi.c if len(ra) == 1 else f"{par(hi, P_ADD)} - {par(self.x(ra[0]), P_ADD - 1)}"
                if decl:
                    self.out(f"{ct} {name}({size});")
                else:
                    self.out(f"{name}.assign({size}, 0);")
                self.out(f"iota({name}.begin(), {name}.end(), {lo});")
                return
            if q in ("input",) or self.is_readline_alias(value.func) or q in ("sys.stdin.readline",):
                pass
        # puste kontenery: [] {} set() dict() defaultdict(...) deque() Counter()
        if self._is_empty_container(value) and tt.k in ("list", "dict", "set", "deque") and not decl and \
                self.fi is None and sym.root().is_global and s in self.tree.body and self._first_global_store(s, sym):
            return          # zmienna globalna jest już pusta
        if self._is_empty_container(value) and tt.k in ("list", "dict", "set", "deque"):
            self.out(f"{ct} {name};" if decl else f"{name}.clear();")
            return
        # [x] * n / [[0] * m for _ in range(n)]
        fill = self.fill_ctor(value, tt)
        if fill is not None:
            if decl:
                self.out(f"{ct} {name}({fill});")
            else:
                self.out(f"{name}.assign({_assign_args(fill)});")
            return
        # referencje: wiersz = grid[i] (mutowany) / const auto& dla dużych kontenerów tylko do odczytu
        if decl and tt.k in ("list", "dict", "set", "deque", "class") and self.lval(value) and \
                not isinstance(value, ast.Name) and self.single_def(sym):
            ref = self._ref_kind(sym, value)
            if ref:
                v = self.x(value, tt)
                self.out(f"{ref} {name} = {v.c};")
                return
        if decl and tt.k in ("list", "dict", "set", "deque", "class") and isinstance(value, ast.Name) and \
                sym in self.inf.mutated:
            src_sym = self.S(value)
            if src_sym is not None and self.single_def(sym):
                self.info("alias", f"`{t.id} = {value.id}`: w Pythonie to ta sama lista (alias) → referencja `&`.",
                          s.lineno)
                self.note("w Pythonie to ten sam obiekt (alias)")
                self.out(f"{ct}& {name} = {self.cn(src_sym)};")
                return
        n0 = len(self.fresh_log)
        with self.capture() as box:
            v = self.x(value, tt, brace=True)
        code = v.c
        if decl and tt.k == "opt" and isinstance(value, ast.Constant) and value.value is None:
            self.note("None → optional (pusty)")
        if decl and code in self.fresh_log[n0:] and self._adopt_temp(box, code, ct, name):
            return
        self.replay(box)
        if decl:
            self.decl_line(ct, name, code)
            return
        self.out(f"{name} = {code};")

    def _adopt_temp(self, box, tmp: str, ct: str, name: str) -> bool:
        """Zmienna pomocnicza wyniku (np. suma z generatora) przejmuje nazwę celu: bez `T x = tmp;`."""
        import re
        if not any(ln.text.startswith(f"{ct} {tmp} ") or ln.text.startswith(f"{ct} {tmp};") or ln.text.startswith(f"{ct} {tmp}{{") or
                   ln.text.startswith(f"{ct} {tmp}(") for ln in box):
            return False
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        if any(word.search(ln.text) for ln in box):
            return False
        pat = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|\b' + re.escape(tmp) + r"\b")
        for ln in box:
            ln.text = pat.sub(lambda m: m.group(1) if m.group(1) else name, ln.text)
        self.replay(box)
        return True

    def decl_line(self, ct: str, name: str, code: str) -> None:
        """`T x = T(args);` → `T x(args);`, `T x = T{...};` → `T x = {...};`."""
        if code.startswith(ct + "(") and code.endswith(")") and _balanced(code[len(ct):]):
            self.out(f"{ct} {name}{code[len(ct):]};")
        elif code.startswith(ct + "{") and code.endswith("}") and _balanced(code[len(ct):]):
            self.out(f"{ct} {name} = {code[len(ct):]};")
        elif code == "{}":
            self.out(f"{ct} {name};")
        else:
            self.out(f"{ct} {name} = {code};")

    def single_def(self, sym) -> bool:
        return self.store_count(sym) == 1

    def _ref_kind(self, sym, value) -> str | None:
        base = value
        while isinstance(base, (ast.Subscript, ast.Attribute)):
            base = base.value
        if not isinstance(base, ast.Name):
            return None
        bsym = self.S(base)
        if bsym is None:
            return None
        if sym in self.inf.mutated:
            if self.single_def(sym):
                self.info("alias", "Element kontenera modyfikowany przez zmienną → referencja `auto&` "
                          "(w Pythonie to ten sam obiekt).", getattr(value, "lineno", None))
                return "auto&"
            return None
        if bsym not in self.inf.mutated and bsym not in self.inf.rebound and self.single_def(sym):
            return "const auto&"
        return None

    def _first_global_store(self, s, sym) -> bool:
        first = None
        for r in self.members(sym):
            for n in self.raw_stores.get(r, []) + self.raw_loads.get(r, []):
                key = (n.lineno, n.col_offset)
                if first is None or key < first:
                    first = key
        return first is not None and first[0] == s.lineno

    def _is_empty_container(self, v) -> bool:
        if isinstance(v, (ast.List, ast.Dict, ast.Set)) and not getattr(v, "elts", getattr(v, "keys", None)):
            return True
        if isinstance(v, ast.Call) and not v.args and not v.keywords:
            return self.q(v) in ("list", "dict", "set", "collections.deque", "deque", "collections.Counter",
                                 "Counter", "collections.OrderedDict", "OrderedDict")
        if isinstance(v, ast.Call) and self.q(v) in ("collections.defaultdict", "defaultdict") and len(v.args) <= 1:
            self._check_factory(v.args[0] if v.args else None, v)
            return True
        return False

    def fill_ctor(self, v, t: Ty) -> str | None:
        """Argumenty konstruktora wypełniającego: [x]*n → "n, x"; [[0]*m for _ in range(n)] → "n, vector(m, 0)"."""
        if t.k != "list":
            return None
        et = t.args[0]
        if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Mult):
            L, R = v.left, v.right
            if isinstance(R, ast.List) and len(R.elts) == 1:
                L, R = R, L
            if isinstance(L, ast.List) and len(L.elts) == 1 and not isinstance(L.elts[0], ast.Starred) and \
                    self._side_effect_free(L.elts[0]):
                if self._is_empty_container(L.elts[0]):
                    return self.x(R).c
                inner = self.inner_value(L.elts[0], et)
                return f"{self.x(R).c}, {inner}"
        if isinstance(v, ast.ListComp) and len(v.generators) == 1 and not v.generators[0].ifs:
            g = v.generators[0]
            if self.q(g.iter) == "range" and len(g.iter.args) == 1 and isinstance(g.target, ast.Name):
                tsym = self.an.sym_of.get(id(g.target))
                uses = any(isinstance(n, ast.Name) and self.an.sym_of.get(id(n)) is tsym for n in ast.walk(v.elt))
                if not uses and self._side_effect_free(v.elt):
                    n = self.x(g.iter.args[0]).c
                    if self._is_empty_container(v.elt):
                        return n
                    return f"{n}, {self.inner_value(v.elt, et)}"
        return None

    def inner_value(self, node, et: Ty) -> str:
        """Wartość elementu w konstruktorze wypełniającym (zagnieżdżone listy → konstruktory)."""
        inner = self.fill_ctor(node, et)
        if inner is not None:
            return f"{self.ct(et)}({inner})"
        if self._is_empty_container(node):
            return f"{self.ct(et)}()"
        e = self.x(node, et)
        if e.cstr and et.k == "str":
            return f"string({e.c})"
        return e.c

    def _side_effect_free(self, node) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                q = self.q(sub)
                if q in ("list", "set", "dict", "collections.deque", "deque", "collections.defaultdict",
                         "defaultdict", "float", "int", "str", "tuple"):
                    continue
                return False
            if isinstance(sub, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                if self.fill_ctor(sub, self.T(sub)) is None:
                    return False
            if isinstance(sub, ast.NamedExpr):
                return False
        return True

    def assign_comp(self, s, sym, name, value, decl, tt) -> None:
        ct = self.sym_ct(sym)
        fill = self.fill_ctor(value, tt)
        if fill is not None:
            self.out(f"{ct} {name}({fill});" if decl else f"{name}.assign({_assign_args(fill)});")
            return
        # [int(input()) for _ in range(n)] → wektor czytany tokenami
        if isinstance(value, ast.ListComp) and self._comp_token_read(value) and not self.in_try and tt.k == "list":
            g = value.generators[0]
            n = self.x(g.iter.args[0] if len(g.iter.args) == 1 else g.iter.args[1]).c
            if len(g.iter.args) == 2:
                n = f"{n} - {par(self.x(g.iter.args[0]), P_ADD - 1)}"
            if decl:
                self.out(f"{ct} {name}({n});")
            else:
                self.out(f"{name}.assign({n}, {{}});")
            v = self.fresh("x")
            self.out(f"for (auto& {v} : {name}) cin >> {v};")
            if id(s) in self.io_token_needs_skip:
                self.use("skipLine")
                self.out("skipLine();")
            return
        kind = {ast.ListComp: "list", ast.SetComp: "set", ast.DictComp: "dict", ast.GeneratorExp: "list"}[type(value)]
        refs_self = any(isinstance(n, ast.Name) and self.S(n) is sym for n in ast.walk(value))
        fill_t = tt if tt.k in ("list", "set", "dict") else List(elem(tt))
        if decl:
            self.out(f"{ct} {name};")
            target = name
        elif refs_self:
            target = self.fresh(f"new_{name}")
            self.out(f"{ct} {target};")
        else:
            self.out(f"{name}.clear();")
            target = name
        self.replay(self.loop_fill(value, target, kind, fill_t))
        if target != name:
            self.out(f"{name} = move({target});")

    def _pop_assign(self, s, targets, value, decl_any) -> bool:
        """x = q.popleft() → x = q.front(); q.pop_front();  (+ wersje z rozpakowaniem krotki)."""
        if not isinstance(value, ast.Call):
            return False
        f = value.func
        front = pop = None
        if isinstance(f, ast.Attribute) and not value.args and isinstance(f.value, (ast.Name, ast.Attribute)):
            rt = self.T(f.value)
            r = par(self.x(f.value), P_POST)
            if f.attr == "popleft" and rt.k == "deque":
                front, pop = f"{r}.front()", f"{r}.pop_front();"
            elif f.attr == "pop" and rt.k in ("list", "deque"):
                front, pop = f"{r}.back()", f"{r}.pop_back();"
        q = self.q(value)
        if q and short(q) == "heappop" and value.args and isinstance(value.args[0], ast.Name) and \
                self.is_pure_heap(self.S(value.args[0])):
            h = self.cn(self.S(value.args[0]))
            front, pop = f"{h}.top()", f"{h}.pop();"
        if front is None:
            return False
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            t = targets[0]
            sym = self.S(t)
            if self.is_decl_here(s, t):
                self.out(f"{self.sym_ct(sym)} {self.cn(sym)} = {front};")
            else:
                self.out(f"{self.cn(sym)} = {front};")
            self.out(pop)
            return True
        tup = targets[0]
        names = tup.elts
        if all(isinstance(e, ast.Name) for e in names):
            if all(self.is_decl_here(s, e) for e in names):
                self.out(f"auto [{', '.join(self.cn(self.S(e)) for e in names)}] = {front};")
            else:
                self.declare_missing(s, names)
                self.out(f"tie({', '.join(self.cn(self.S(e)) for e in names)}) = {front};")
            self.out(pop)
            return True
        return False

    def declare_missing(self, s, names, init: bool = True) -> None:
        groups: list[tuple[str, list[str]]] = []
        for e in names:
            if isinstance(e, ast.Name) and self.is_decl_here(s, e):
                sym = self.S(e)
                ct = self.sym_ct(sym)
                name = self.cn(sym) + (self.default_init(sym) if init else "")
                if groups and groups[-1][0] == ct:
                    groups[-1][1].append(name)
                else:
                    groups.append((ct, [name]))
        for ct, ns in groups:
            self.out(f"{ct} {', '.join(ns)};")

    def assign_unpack(self, s, t, value) -> None:
        elts = t.elts
        # a, b = map(int, input().split())
        tp = self.read_tuple_pattern(value)
        if tp is not None and not self.in_try and all(isinstance(e, (ast.Name, ast.Subscript, ast.Attribute))
                                                      for e in elts):
            self.declare_missing(s, elts, init=False)
            self.read_tokens([self.x(e).c for e in elts], s, tp[0], tp[1])
            return
        if any(isinstance(e, ast.Starred) for e in elts):
            return self.assign_starred(s, t, value)
        # swap: a, b = b, a
        if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == 2 == len(elts) and \
                self.src(elts[0]) == self.src(value.elts[1]) and self.src(elts[1]) == self.src(value.elts[0]) and \
                all(self.simple(e) for e in elts):
            self.out(f"swap({self.x(elts[0]).c}, {self.x(elts[1]).c});")
            return
        if self._pop_assign(s, [t], value, False):
            return
        if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(elts) and \
                not any(isinstance(v, ast.Starred) for v in value.elts):
            safe = True
            assigned: set[str] = set()
            for e, v in zip(elts, value.elts):
                used = {n.id for n in ast.walk(v) if isinstance(n, ast.Name)}
                if used & assigned:
                    safe = False
                    break
                base = e
                while isinstance(base, (ast.Subscript, ast.Attribute)):
                    base = base.value
                if isinstance(base, ast.Name):
                    assigned.add(base.id)
            if safe:
                for e, v in zip(elts, value.elts):
                    self._assign_one(s, e, v)
                return
            self.declare_missing(s, elts)
            vals = []
            for e, v in zip(elts, value.elts):
                ev = self.x(v, self.T(e))
                vals.append(f"string({ev.c})" if ev.cstr else ev.c)
            self.note("przypisanie równoległe: najpierw liczone są wszystkie prawe strony")
            self.out(f"tie({', '.join(self.x(e).c for e in elts)}) = make_tuple({', '.join(vals)});")
            return
        vt = self.T(value)
        names_ok = all(isinstance(e, ast.Name) for e in elts)
        if vt.k == "tuple" and len(vt.args) == len(elts):
            v = self.x(value)
            if names_ok and all(self.is_decl_here(s, e) for e in elts):
                self.out(f"auto [{', '.join(self.cn(self.S(e)) for e in elts)}] = {v.c};")
                return
            if all(isinstance(e, (ast.Name, ast.Subscript, ast.Attribute)) for e in elts):
                self.declare_missing(s, elts)
                self.out(f"tie({', '.join(self.x(e).c for e in elts)}) = {v.c};")
                return
        # rozpakowanie listy / iteratora: a, b = lista
        v = self.x(value)
        tmp = v.c
        if not (isinstance(value, ast.Name)) and not (v.c.isidentifier() and v.c in self.fresh_log):
            tmp = self.fresh("t")
            self.out(f"auto {tmp} = {v.c};")
        for i, e in enumerate(elts):
            self._assign_from_code(s, e, f"{tmp}[{i}]", elem(vt))

    def _assign_from_code(self, s, e, code: str, et: Ty) -> None:
        if isinstance(e, ast.Name):
            sym = self.S(e)
            if self.is_decl_here(s, e):
                self.out(f"{self.sym_ct(sym)} {self.cn(sym)} = {code};")
            else:
                self.out(f"{self.cn(sym)} = {code};")
        elif isinstance(e, (ast.Tuple, ast.List)):
            tmp = self.fresh("t")
            self.out(f"auto {tmp} = {code};")
            for i, sub in enumerate(e.elts):
                self._assign_from_code(s, sub, f"{tmp}[{i}]", elem(et))
        else:
            self.out(f"{self.x(e).c} = {code};")

    def assign_starred(self, s, t, value) -> None:
        elts = t.elts
        si = next(i for i, e in enumerate(elts) if isinstance(e, ast.Starred))
        vt = self.T(value)
        tp = self.read_tuple_pattern(value)
        if tp is not None and tp[0] == "int":
            self.use("readInts")
            v = E("readInts()")
        else:
            v = self.x(value)
        tmp = self.fresh("t")
        self.out(f"auto {tmp} = {v.c};")
        n = len(elts)
        for i, e in enumerate(elts):
            if i < si:
                self._assign_from_code(s, e, f"{tmp}[{i}]", elem(vt))
            elif i == si:
                tail = n - si - 1
                end = f"{tmp}.end() - {tail}" if tail else f"{tmp}.end()"
                target = e.value
                sym = self.S(target)
                ct = self.sym_ct(sym)
                code = f"{ct}({tmp}.begin() + {si}, {end})"
                if self.is_decl_here(s, target):
                    self.out(f"{ct} {self.cn(sym)}({tmp}.begin() + {si}, {end});")
                else:
                    self.out(f"{self.cn(sym)} = {code};")
            else:
                self._assign_from_code(s, e, f"{tmp}[{tmp}.size() - {n - i}]", elem(vt))

    def assign_subscript(self, s, t, value) -> None:
        if isinstance(t.slice, ast.Slice):
            sl = t.slice
            bt = self.T(t.value)
            if sl.lower is None and sl.upper is None and sl.step is None:
                self.out(f"{self.x(t.value).c} = {self.x(value, bt).c};")
                return
            raise Unsupported("przypisanie do wycinka a[i:j] = …")
        sc = self.read_scalar_pattern(value)
        if sc is not None and not self.in_try:
            self.read_tokens([self.x(t).c], s, sc[0], sc[1])
            return
        tt = self.T(t)
        fill = self.fill_ctor(value, tt)
        target = self.x(t)
        if fill is not None:
            self.out(f"{target.c} = {self.ct(tt)}({fill});")
            return
        if self._is_empty_container(value) and tt.k in ("list", "dict", "set", "deque"):
            self.out(f"{target.c} = {{}};")
            return
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            v = self.comp_value(value, tt)
            self.out(f"{target.c} = move({v.c});")
            return
        v = self.x(value, tt, brace=True)
        self.out(f"{target.c} = {v.c};")

    def named_lambda(self, s, sym, lam, decl) -> None:
        fi = self.inf.funcs[id(lam)]
        saved = (self.fi, self.plan, self.ret_type)
        self.fi = fi
        self.plan = self.make_plan(fi.scope, [], fi.params)
        params, ret = self.lambda_sig(fi)
        self.ret_type = fi.ret
        rec = any(isinstance(n, ast.Name) and self.S(n) is sym for n in ast.walk(lam.body))
        body = self.x(lam.body, fi.ret if ret != "void" else None)
        self.fi, self.plan, self.ret_type = saved
        name = self.cn(sym)
        lam_code = f"[&]({params}) -> {ret} {{ return {body.c}; }}" if ret != "void" else \
            f"[&]({params}) {{ {body.c}; }}"
        if rec or not decl:
            sig = self.fn_sig(fi)
            if decl:
                self.out(f"function<{sig}> {name} = {lam_code};")
            else:
                self.out(f"{name} = {lam_code};")
        else:
            self.out(f"auto {name} = {lam_code};")

    def fn_sig(self, fi) -> str:
        ret = self.ret_ct(fi)
        parts = self.param_list(fi, with_defaults=False)
        types = []
        for p in parts.split(", ") if parts else []:
            types.append(p.rsplit(" ", 1)[0])
        return f"{ret}({', '.join(types)})"

    # ------------------------------------------------------------------ +=, -=, …
    def augassign(self, s) -> None:
        t = s.target
        v = s.value
        op = s.op
        tt = self.T(t)
        target = self.x(t)
        tc = target.c
        if tt.k == "list" and isinstance(op, ast.Add):
            if isinstance(v, (ast.List, ast.Tuple)):
                et = tt.args[0]
                for e in v.elts:
                    self.out(f"{par(target, P_POST)}.push_back({self.x(e, et, brace=True).c});")
                return
            if self.lval(v):
                b, e = self.rng(v)
                if self.src(v) == self.src(t):
                    tmp = self.fresh("copy")
                    self.out(f"auto {tmp} = {tc};")
                    b, e = f"{tmp}.begin()", f"{tmp}.end()"
                self.out(f"{par(target, P_POST)}.insert({par(target, P_POST)}.end(), {b}, {e});")
                return
            b, e = self.range_pair(v)
            self.out(f"{par(target, P_POST)}.insert({par(target, P_POST)}.end(), {b}, {e});")
            return
        if tt.k == "list" and isinstance(op, ast.Mult):
            self.use("repeatVec")
            self.out(f"{tc} = repeatVec({tc}, {self.x(v).c});")
            return
        if tt.k == "set":
            if isinstance(op, ast.BitOr):
                if self.lval(v):
                    b, e = self.rng(v)
                    self.out(f"{par(target, P_POST)}.insert({b}, {e});")
                else:
                    self.out(f"for (const auto& x_ : {self.iterable_code(v)}) {par(target, P_POST)}.insert(x_);")
                return
            if isinstance(op, ast.Sub):
                self.out(f"for (const auto& x_ : {self.iterable_code(v)}) {par(target, P_POST)}.erase(x_);")
                return
            self.use("setops")
            fn = {ast.BitAnd: "setInter", ast.BitXor: "setSymDiff"}.get(type(op))
            if fn:
                self.out(f"{tc} = {fn}({tc}, {self.x(v, tt).c});")
                return
        if tt.k == "str":
            if isinstance(op, ast.Add):
                vt = self.T(v)
                if isinstance(v, ast.JoinedStr):
                    ve = self.fstring_str(v)
                else:
                    ve = self.x(v, CHAR if vt.k == "char" else STR)
                self.out(f"{tc} += {ve.c};")
                return
            if isinstance(op, ast.Mult):
                self.use("repeatStr")
                self.out(f"{tc} = repeatStr({tc}, {self.x(v).c});")
                return
        if tt.k == "dict" and isinstance(op, ast.BitOr):
            self.out(f"for (const auto& [k_, v_] : {self.x(v).c}) {par(target, P_POST)}[k_] = v_;")
            return
        vt = self.T(v)
        if isinstance(op, ast.FloorDiv):
            if tt.k == "float" or vt.k == "float":
                self.out(f"{tc} = floor({tc} / {par(self.x(v), P_MUL - 1)});")
                return
            if self.signs.nonneg(t) and self.signs.nonneg(v):
                self.out(f"{tc} /= {self.x(v).c};")
            else:
                self.use("floordiv")
                self.out(f"{tc} = floordiv({tc}, {self.x(v).c});")
            return
        if isinstance(op, ast.Mod):
            if tt.k == "float" or vt.k == "float":
                self.use("fpymod")
                self.out(f"{tc} = fpymod({tc}, {self.x(v).c});")
                return
            if self.signs.nonneg(t) and self.signs.nonneg(v):
                self.out(f"{tc} %= {self.x(v).c};")
            else:
                self.use("pymod")
                self.out(f"{tc} = pymod({tc}, {self.x(v).c});")
            return
        if isinstance(op, ast.Pow):
            e = self.power(t, v, s)
            self.out(f"{tc} = {e.c};")
            return
        if isinstance(op, ast.Div):
            if tt.k == "float" and not is_intlike(self.T(t)):
                self.out(f"{tc} /= {self.x(v).c};")
            else:
                self.out(f"{tc} = (double){par(target, P_UNARY)} / {par(self.x(v), P_MUL - 1)};")
            return
        if isinstance(op, ast.Mult) and is_intlike(tt) and isinstance(t, ast.Name) and self.loops and \
                not self._var_has_mod(t):
            self.warn("overflow", f"`{t.id} *= …` w pętli bez modulo: iloczyn może przekroczyć 64 bity "
                      "(Python ma nieograniczone liczby całkowite).", s.lineno)
        sym = AUG.get(type(op))
        if sym is None:
            raise Unsupported("operator przypisania")
        ve = self.operand(v, tt if tt.k in ("float",) else None)
        if isinstance(op, ast.LShift) or isinstance(op, ast.RShift):
            pass
        self.out(f"{tc} {sym} {ve.c};")

    def _var_has_mod(self, name_node) -> bool:
        sym = self.S(name_node)
        return any(r in self.mod_syms for r in self.members(sym))

    # ------------------------------------------------------------------ if / while / for
    def if_stmt(self, s) -> None:
        from .emit import _is_main_guard
        if _is_main_guard(s):
            self.body([b for b in s.body if not isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))])
            return
        c = self.cond(s.test)
        self.out(f"if ({c.c}) {{")
        with self.indent():
            self.body(s.body)
        orelse = s.orelse
        while orelse:
            if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                e = orelse[0]
                self.py = e.lineno
                with self.capture() as box:
                    c2 = self.cond(e.test)
                needs_block = bool(box) or bool(self.plan and self.plan.before_here.get(id(e)))
                if needs_block:
                    self.out("} else {")
                    with self.indent():
                        self.stmt(e)
                    break
                self.out(f"}} else if ({c2.c}) {{")
                with self.indent():
                    self.body(e.body)
                orelse = e.orelse
                continue
            self.out("} else {")
            with self.indent():
                self.body(orelse)
            break
        self.out("}")

    def while_stmt(self, s) -> None:
        flag = None
        if s.orelse:
            flag = self.fresh("broke")
            self.note("while … else: `else` wykona się, gdy pętla nie skończy się przez break")
            self.out(f"bool {flag} = false;")
        infinite = isinstance(s.test, ast.Constant) and bool(s.test.value)
        with self.capture() as box:
            c = E("true") if infinite else self.cond(s.test)
        self.loops.append({"flag": flag})
        if box:
            self.out("while (true) {")
            with self.indent():
                self.replay(box)
                self.out(f"if (!{par(c, P_UNARY)}) break;")
                self.body(s.body)
            self.out("}")
        else:
            self.out(f"while ({c.c}) {{")
            with self.indent():
                self.body(s.body)
            self.out("}")
        self.loops.pop()
        if s.orelse:
            self.out(f"if (!{flag}) {{")
            with self.indent():
                self.body(s.orelse)
            self.out("}")

    def for_stmt(self, s) -> None:
        flag = None
        if s.orelse:
            flag = self.fresh("broke")
            self.note("for … else: `else` wykona się, gdy pętla nie skończy się przez break")
            self.out(f"bool {flag} = false;")
        self.loops.append({"flag": flag})
        try:
            self.for_over(s.iter, s.target, lambda: self.body(s.body), stmt_node=s)
        finally:
            self.loops.pop()
        if s.orelse:
            self.out(f"if (!{flag}) {{")
            with self.indent():
                self.body(s.orelse)
            self.out("}")

    # --- pomocnicze dla pętli
    def _stored_in(self, body_nodes, sym) -> bool:
        for st in body_nodes:
            for n in ast.walk(st):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and self.S(n) is sym:
                    return True
                if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and self.S(n.target) is sym:
                    return True
        return False

    def _target_mode(self, target, stmt_node, comp_scope) -> tuple[bool, bool]:
        """(deklaracja w nagłówku, zmienna modyfikowana w ciele)."""
        if target is None or comp_scope or stmt_node is None:
            return True, False
        names = self.target_syms(target)
        here = self.plan.for_here.get(id(stmt_node), set()) if self.plan else set()
        in_header = all(self.an.sym_of.get(id(n)) in here for n in names)
        modified = any(self._stored_in(stmt_node.body, self.S(n)) for n in names)
        return in_header, modified

    def _invariant(self, node, stmt_node) -> bool:
        if node is None or stmt_node is None:
            return True
        names = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                q = self.q(sub)
                if q not in PURE_BOUND_FUNCS:
                    return False
                if q == "len" and sub.args:
                    b = sub.args[0]
                    while isinstance(b, (ast.Subscript, ast.Attribute)):
                        b = b.value
                    if isinstance(b, ast.Name):
                        bsym = self.S(b)
                        for st in stmt_node.body:
                            for n in ast.walk(st):
                                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                                    bb = n.func.value
                                    while isinstance(bb, (ast.Subscript, ast.Attribute)):
                                        bb = bb.value
                                    if isinstance(bb, ast.Name) and self.S(bb) is bsym and n.func.attr in (
                                            "append", "pop", "extend", "insert", "remove", "clear", "popleft",
                                            "appendleft", "add", "discard"):
                                        return False
            if isinstance(sub, ast.Name):
                names.add(self.S(sub))
        for sym in names:
            if sym is not None and self._stored_in(stmt_node.body, sym):
                return False
        return True

    def bind_elem(self, target, code: str, et: Ty, declare: bool, ref_ok: bool = True) -> None:
        """Wiązanie elementu `code` z celem pętli (wewnątrz ciała)."""
        if isinstance(target, ast.Name):
            sym = self.S(target)
            name = self.cn(sym)
            if declare:
                t = self.sym_type(sym)
                if is_scalar(t):
                    self.out(f"{self.ct(t)} {name} = {code};")
                elif sym in self.inf.mutated and ref_ok:
                    self.out(f"auto& {name} = {code};")
                elif ref_ok and not self._rebound_local(sym):
                    self.out(f"const auto& {name} = {code};")
                else:
                    self.out(f"auto {name} = {code};")
            else:
                self.out(f"{name} = {code};")
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            elts = target.elts
            if et.k == "tuple" and all(isinstance(e, ast.Name) for e in elts) and declare:
                self.out(f"auto [{', '.join(self.cn(self.S(e)) for e in elts)}] = {code};")
                return
            if et.k == "tuple" and all(isinstance(e, ast.Name) for e in elts):
                self.out(f"tie({', '.join(self.cn(self.S(e)) for e in elts)}) = {code};")
                return
            tmp = code
            if not code.isidentifier():
                tmp = self.fresh("t")
                self.out(f"const auto& {tmp} = {code};")
            for i, e in enumerate(elts):
                if et.k == "tuple":
                    sub_t = et.args[i] if i < len(et.args) else BOT
                    acc = (f"{tmp}.first" if i == 0 else f"{tmp}.second") if len(et.args) == 2 else f"get<{i}>({tmp})"
                else:
                    sub_t = elem(et)
                    acc = f"{tmp}[{i}]"
                self.bind_elem(e, acc, sub_t, declare, ref_ok)
            return
        raise Unsupported("cel pętli")

    def _rebound_local(self, sym) -> bool:
        return self.store_count(sym) > 1

    def for_over(self, it, target, body_fn, comp_scope=False, var_name=None, var_type=None, stmt_node=None):
        """Generuje pętlę C++ po iterowalnym `it`; `body_fn()` emituje ciało (wcięte)."""
        decl, modified = self._target_mode(target, stmt_node, comp_scope)
        t_it = self.T(it)
        q = self.q(it) if isinstance(it, ast.Call) else None
        if target is None:
            target_name = var_name
        else:
            target_name = None

        def open_block(header, prelude=None):
            self.out(header + " {")
            with self.indent():
                if prelude:
                    prelude()
                body_fn()
            self.out("}")

        # --- range(...)
        if q == "range" or (q == "reversed" and it.args and self.q(it.args[0]) == "range"):
            rev = q == "reversed"
            call = it.args[0] if rev else it
            return self._range_loop(call, rev, target, target_name, decl, modified, stmt_node, open_block)
        # --- enumerate(...)
        if q == "enumerate" and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2 and it.args:
            return self._enumerate_loop(it, target, decl, modified, stmt_node, open_block)
        # --- zip(...)
        if q == "zip" and it.args and isinstance(it.args[0], ast.Starred):
            self.use("transpose")
            return self._each(f"transpose({self.x(it.args[0].value).c})", elem(t_it), target, target_name, var_type,
                              decl, modified, open_block, stmt_node)
        if q == "zip" and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == len(it.args) and it.args:
            return self._zip_loop(it, target, decl, stmt_node, open_block)
        # --- słownik: .items() / .keys() / .values() / sam słownik
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and \
                it.func.attr in ("items", "keys", "values") and self.T(it.func.value).k == "dict":
            d = self.x(it.func.value).c
            dt = self.T(it.func.value)
            self._warn_dict_order(it)
            if it.func.attr == "items":
                if isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2 and \
                        all(isinstance(e, ast.Name) for e in target.elts) and decl and not modified:
                    ks, vs = self.S(target.elts[0]), self.S(target.elts[1])
                    ref = "auto&" if vs in self.inf.mutated else "const auto&"
                    if self._stored_in(stmt_node.body if stmt_node else [], vs):
                        ref = "auto"
                    return open_block(f"for ({ref} [{self.cn(ks)}, {self.cn(vs)}] : {d})")
                kv = self.fresh("kv")
                return open_block(f"for (const auto& {kv} : {d})",
                                  lambda: self.bind_elem(target, kv, ast_tuple_t(dt), decl and not modified))
            view = "views::keys" if it.func.attr == "keys" else "views::values"
            et = dt.args[0] if it.func.attr == "keys" else dt.args[1]
            return self._each(f"{d} | {view}", et, target, target_name, var_type, decl, modified, open_block, stmt_node)
        if t_it.k == "dict" and not isinstance(it, (ast.DictComp,)):
            self._warn_dict_order(it)
            d = self.x(it).c
            return self._each(f"{d} | views::keys", t_it.args[0], target, target_name, var_type, decl, modified,
                              open_block, stmt_node)
        # --- linie z wejścia: for line in sys.stdin / open(0)
        if t_it.k == "file" or (q == "open"):
            self.use("readline")
            v = self.cn(self.S(target)) if isinstance(target, ast.Name) else self.fresh("line")
            if isinstance(target, ast.Name) and decl:
                return open_block(f"for (string {v}; !({v} = readline()).empty();)")
            lv = self.fresh("line")
            return open_block(f"for (string {lv}; !({lv} = readline()).empty();)",
                              lambda: self.bind_elem(target, lv, STR, decl))
        if t_it.k == "tokens":
            tv = self.fresh("tok")
            if isinstance(target, ast.Name) and decl:
                return open_block(f"for (string {self.cn(self.S(target))}; cin >> {self.cn(self.S(target))};)")
            return open_block(f"for (string {tv}; cin >> {tv};)", lambda: self.bind_elem(target, tv, STR, decl))
        # --- odwrócona lista: for x in reversed(a)
        if q == "reversed" and it.args and self.lval(it.args[0]) and self.T(it.args[0]).k in ("list", "str", "deque"):
            src = self.x(it.args[0]).c
            return self._each(f"{src} | views::reverse", elem(self.T(it.args[0])), target, target_name, var_type,
                              decl, modified, open_block, stmt_node)
        # --- lista literałów: for dx, dy in [(0, 1), (1, 0), ...]
        if isinstance(it, (ast.List, ast.Tuple)) and t_it.k in ("list", "tuple"):
            lt = t_it if t_it.k == "list" else List(elem(t_it))
            cont = self.x(it, lt).c
            if t_it.k == "tuple":
                cont = f"{self.ct(lt)}{{{', '.join(self.x(e, elem(lt), brace=True).c for e in it.elts)}}}"
            return self._each(cont, elem(lt), target, target_name, var_type, decl, modified, open_block, stmt_node)
        if q in ("map", "filter") and isinstance(target, ast.Name) and not modified:
            et = elem(t_it)

            def sink(v):
                self.bind_elem(target, v.c, et, decl)
                body_fn()
            return self.lazy_fill(it, sink)
        if t_it.k == "lazy" or isinstance(it, (ast.GeneratorExp, ast.ListComp)) or q in ("sorted", "map", "filter",
                                                                                       "zip", "enumerate"):
            if q == "sorted" and not self.kw(it, "key") and not self.kw(it, "reverse") and it.args and \
                    self.T(it.args[0]).k in ("list", "str", "set", "deque"):
                self.use("sorted")
                cont = f"sorted({self.x(it.args[0]).c})"
            else:
                cont = self.materialize(it).c
            return self._each(cont, elem(t_it), target, target_name, var_type, decl, modified, open_block, stmt_node)
        if t_it.k in ("list", "str", "set", "deque", "char", "bot"):
            e = self.x(it)
            cont = f"string({e.c})" if e.cstr else e.c
            if t_it.k == "char":
                cont = f"string(1, {e.c})"
            et = elem(t_it) if t_it.k != "bot" else (var_type or BOT)
            if isinstance(it, ast.Name) and self.is_pure_heap(self.S(it)):
                raise Unsupported("iteracja po kopcu")
            return self._each(cont, et, target, target_name, var_type, decl, modified, open_block, stmt_node)
        if t_it.k == "tuple":
            et = elem(t_it)
            cont = self.x(it).c
            return self._each(f"vector<{self.ct_elem(et)}>{{{', '.join(self._tuple_items(cont, t_it))}}}", et, target,
                              target_name, var_type, decl, modified, open_block, stmt_node)
        raise Unsupported(f"pętla po obiekcie typu {t_it!r}")

    def _tuple_items(self, code, t):
        if len(t.args) == 2:
            return [f"{code}.first", f"{code}.second"]
        return [f"get<{i}>({code})" for i in range(len(t.args))]

    def _warn_dict_order(self, node) -> None:
        self.warn("dict-order", "Iteracja po słowniku: Python zachowuje kolejność wstawiania, a `map` w C++ — "
                  "rosnącą kolejność kluczy. Jeśli kolejność wpływa na wynik, posortuj klucze w Pythonie.",
                  getattr(node, "lineno", None), "info" if not self.use_unordered else "warn")

    def _each(self, cont, et, target, target_name, var_type, decl, modified, open_block, stmt_node):
        """for (T x : cont) z odpowiednim wiązaniem celu."""
        if target is None:
            v = target_name
            vt = var_type if var_type is not None else et
            if is_scalar(vt) and vt.k != "bot":
                return open_block(f"for ({self.ct(vt)} {v} : {cont})")
            return open_block(f"for (const auto& {v} : {cont})")
        if isinstance(target, ast.Name):
            sym = self.S(target)
            name = self.cn(sym)
            t = self.sym_type(sym)
            if decl and not modified:
                if is_scalar(t) and t.k != "bot":
                    return open_block(f"for ({self.ct(t)} {name} : {cont})")
                if sym in self.inf.mutated:
                    return open_block(f"for (auto& {name} : {cont})")
                return open_block(f"for (const auto& {name} : {cont})")
            iv = self.fresh(f"{name}_")
            return open_block(f"for (const auto& {iv} : {cont})",
                              lambda: self.bind_elem(target, iv, et, decl))
        if isinstance(target, (ast.Tuple, ast.List)):
            elts = target.elts
            if et.k == "tuple" and all(isinstance(e, ast.Name) for e in elts) and decl and not modified and \
                    len(et.args) == len(elts):
                syms = [self.S(e) for e in elts]
                if all(is_scalar(a) for a in et.args):
                    ref = "auto"
                elif any(s in self.inf.mutated for s in syms):
                    ref = "auto&"
                else:
                    ref = "const auto&"
                return open_block(f"for ({ref} [{', '.join(self.cn(s) for s in syms)}] : {cont})")
            iv = self.fresh("item")
            return open_block(f"for (const auto& {iv} : {cont})",
                              lambda: self.bind_elem(target, iv, et, decl))
        raise Unsupported("cel pętli")

    def _range_loop(self, call, rev, target, target_name, decl, modified, stmt_node, open_block):
        a = call.args
        if not a or len(a) > 3:
            raise Unsupported("range z niepoprawną liczbą argumentów")
        if isinstance(target, ast.Name):
            sym = self.S(target)
            name = self.cn(sym)
            vt = self.sym_type(sym)
        elif target is None:
            sym = None
            name = target_name
            vt = INT
        else:
            raise Unsupported("rozpakowanie elementu range")
        ct = self.ct(vt) if vt.k in ("int", "float") else self.int_t
        if ct == "double":
            ct = self.int_t
        start_n = a[0] if len(a) >= 2 else None
        stop_n = a[1] if len(a) >= 2 else a[0]
        step_n = a[2] if len(a) == 3 else None
        stepv = const_int(step_n) if step_n is not None else 1
        step_pos = stepv is None and self.signs.nonneg(step_n)
        if stepv == 0:
            raise Unsupported("range z krokiem 0")
        start = self.x(start_n) if start_n is not None else E("0")
        nonneg_start = start_n is None or self.signs.nonneg(start_n)
        if stepv is not None and stepv > 0 and nonneg_start and isinstance(stop_n, ast.Call) and \
                self.q(stop_n) == "len" and not rev:
            stop = self.len_expr(stop_n.args[0], raw=True)
            stop_is_size = True
        else:
            stop = self.x(stop_n)
            stop_is_size = False
        var = name
        prelude = None
        if not decl or modified:
            var = self.fresh(f"{name}_")
            if not decl:
                prelude = lambda: self.out(f"{name} = {var};")  # noqa: E731
            else:
                prelude = lambda: self.out(f"{ct} {name} = {var};")  # noqa: E731
        inv = self._invariant(stop_n, stmt_node)
        extra = ""
        if not inv:
            endv = self.fresh(f"{var}_end")
            extra = f", {endv} = {stop.c}"
            stop = E(endv)
            stop_is_size = False
        if stepv is None:
            st = self.x(step_n)
            if not self.simple(step_n):
                stv = self.fresh("step")
                self.out(f"{ct} {stv} = {st.c};")
                st = E(stv)
        if rev:
            if stepv != 1:
                raise Unsupported("reversed(range(...)) z krokiem")
            last = f"{par(stop, P_ADD)} - 1"
            lo = start.c
            header = f"for ({ct} {var} = {last}{extra}; {var} >= {par(start, P_REL - 1)}; {var}--)"
            del lo
            return open_block(header, prelude)
        if step_pos:
            cond = f"{var} < {par(stop, P_REL - 1)}"
            inc = f"{var} += {st.c}"
        elif stepv is not None and stepv > 0:
            cond = f"{var} < {par(stop, P_REL - 1)}"
            inc = f"{var}++" if stepv == 1 else f"{var} += {stepv}"
        elif stepv is not None:
            cond = f"{var} > {par(stop, P_REL - 1)}"
            inc = f"{var}--" if stepv == -1 else f"{var} -= {-stepv}"
        else:
            cond = f"({st.c} > 0 ? {var} < {par(stop, P_REL - 1)} : {var} > {par(stop, P_REL - 1)})"
            inc = f"{var} += {st.c}"
        del stop_is_size
        header = f"for ({ct} {var} = {start.c}{extra}; {cond}; {inc})"
        return open_block(header, prelude)

    def _enumerate_loop(self, it, target, decl, modified, stmt_node, open_block):
        seq = it.args[0]
        start = it.args[1] if len(it.args) > 1 else self.kw(it, "start")
        st = self.T(seq)
        it_t, vt_node = target.elts
        if not isinstance(it_t, ast.Name):
            raise Unsupported("enumerate z rozpakowanym indeksem")
        isym = self.S(it_t)
        iname = self.cn(isym)
        ct = self.ct(self.sym_type(isym)) if self.sym_type(isym).k == "int" else self.int_t
        et = elem(st)
        sv = const_int(start) if start is not None else 0
        if st.k in ("list", "str", "deque") and self.lval(seq) and sv is not None and decl and not modified:
            sc = self.x(seq).c
            if sv == 0:
                header = f"for ({ct} {iname} = 0; {iname} < ({self.int_t}){sc}.size(); {iname}++)"
                return open_block(header, lambda: self.bind_elem(vt_node, f"{sc}[{iname}]", et, True))
            header = f"for ({ct} {iname} = {sv}; {iname} < ({self.int_t}){sc}.size() + {sv}; {iname}++)" if sv < 0 \
                else (f"for ({ct} {iname} = {sv}; {iname} <= ({self.int_t}){sc}.size(); {iname}++)" if sv == 1 else
                      f"for ({ct} {iname} = {sv}; {iname} < ({self.int_t}){sc}.size() + {sv}; {iname}++)")
            idx = f"{iname} - {sv}" if sv > 0 else f"{iname} + {-sv}"
            return open_block(header, lambda: self.bind_elem(vt_node, f"{sc}[{idx}]", et, True))
        cont = self.iterable_code(seq)
        if sv is not None:
            init = str(sv - 1)
        else:
            init = f"{par(self.x(start), P_ADD)} - 1"
        iv = iname if (decl and not modified) else self.fresh(f"{iname}_")
        ev = self.fresh("item")

        def prelude():
            self.out(f"{iv}++;")
            if iv != iname:
                if decl:
                    self.out(f"{ct} {iname} = {iv};")
                else:
                    self.out(f"{iname} = {iv};")
            self.bind_elem(vt_node, ev, et, decl)
        return open_block(f"for ({ct} {iv} = {init}; const auto& {ev} : {cont})", prelude)

    def _zip_loop(self, it, target, decl, stmt_node, open_block):
        args = it.args
        codes = []
        sizes = []
        for a in args:
            at = self.T(a)
            if isinstance(a, ast.Call) and self.q(a) == "range" and len(a.args) <= 2:
                lo = self.x(a.args[0]).c if len(a.args) == 2 else "0"
                hi = self.x(a.args[-1]).c
                codes.append(("range", lo))
                sizes.append(f"({hi}) - ({lo})" if lo != "0" else hi)
                continue
            if at.k in ("list", "str", "deque") and self.lval(a):
                c = self.x(a).c
                codes.append(("seq", c))
                sizes.append(f"(long long){c}.size()")
                continue
            m = self.materialize(a)
            c = m.c
            if not c.isidentifier():
                tmp = self.fresh("tmp")
                self.out(f"auto {tmp} = {c};")
                c = tmp
            codes.append(("seq", c))
            sizes.append(f"(long long){c}.size()")
        k = self.fresh("k")
        bound = sizes[0] if len(sizes) == 1 else (f"min({sizes[0]}, {sizes[1]})" if len(sizes) == 2 else
                                                 f"min({{{', '.join(sizes)}}})")
        bound = bound.replace("(long long)", f"({self.int_t})")

        def prelude():
            for (kind, c), e, a in zip(codes, target.elts, args):
                if kind == "range":
                    self.bind_elem(e, f"{c} + {k}" if c != "0" else k, INT, decl)
                else:
                    self.bind_elem(e, f"{c}[{k}]", elem(self.T(a)), decl)
        return open_block(f"for ({self.int_t} {k} = 0; {k} < {bound}; {k}++)", prelude)

    # ------------------------------------------------------------------ return / del / try / def
    def return_stmt(self, s) -> None:
        if self.fi is None:
            self.out("return 0;")
            return
        if s.value is None or (isinstance(s.value, ast.Constant) and s.value.value is None):
            r = self.fi.ret
            if r.k == "opt":
                self.out("return nullopt;")
            elif self.ret_ct(self.fi) in ("void", ""):
                self.out("return;")
            else:
                self.out("return {};")
            return
        want = self.fi.ret if self.fi.ret.k not in ("bot", "top") else None
        v = self.x(s.value, want, brace=True)
        if self.memo_name:
            if v.c.startswith("{"):
                v = E(f"{self.ret_ct(self.fi)}{v.c}")
            self.out(f"return {self.memo_name}[key] = {v.c};")
            return
        self.out(f"return {v.c};")

    def delete_stmt(self, s) -> None:
        for t in s.targets:
            if isinstance(t, ast.Subscript):
                bt = self.T(t.value)
                b = par(self.x(t.value), P_POST)
                if isinstance(t.slice, ast.Slice):
                    sl = t.slice
                    if sl.step is not None:
                        raise Unsupported("del a[i:j:k]")
                    lo = self.x(sl.lower).c if sl.lower is not None else "0"
                    hi = f"{b}.begin() + {par(self.x(sl.upper), P_ADD - 1)}" if sl.upper is not None else f"{b}.end()"
                    self.out(f"{b}.erase({b}.begin() + {lo}, {hi});")
                elif bt.k == "dict":
                    self.out(f"{b}.erase({self.x(t.slice, bt.args[0]).c});")
                else:
                    i = const_int(t.slice)
                    if i == -1:
                        self.out(f"{b}.pop_back();")
                    else:
                        self.out(f"{b}.erase({b}.begin() + {par(self.x(t.slice), P_ADD - 1)});")
            elif isinstance(t, ast.Name):
                self.out(f"// del {t.id} — w C++ zmienna zniknie na końcu bloku")
            else:
                raise Unsupported("del")

    def try_stmt(self, s) -> None:
        self.warn("try", "try/except → try/catch: C++ łapie tylko wyjątki rzucane jawnie (np. koniec wejścia w "
                  "input(), stoll). Dzielenie przez zero ani wyjście poza tablicę NIE rzucają wyjątku!", s.lineno)
        self.out("try {")
        self.in_try += 1
        try:
            with self.indent():
                self.body(s.body)
                if s.orelse:
                    self.body(s.orelse)
        finally:
            self.in_try -= 1
        for h in s.handlers[:1]:
            if h.name:
                self.out(f"}} catch (const exception& {h.name}) {{")
            else:
                self.out("} catch (...) {")
            with self.indent():
                self.body(h.body)
        if len(s.handlers) > 1:
            self.warn("try", "Kilka bloków except — przetłumaczono tylko pierwszy (catch (...)).", s.lineno)
        if not s.handlers:
            self.out("} catch (...) {")
            self.out("    throw;")
        self.out("}")
        if s.finalbody:
            self.body(s.finalbody)

    def nested_def(self, s) -> None:
        fi = self.inf.funcs[id(s)]
        sym = self.S(s)
        name = self.cn(sym)
        self.func_cname[fi.fid] = name
        decl = self.plan is not None and self.an.sym_of.get(id(s)) in self.plan.init_here.get(id(s), set())
        saved = (self.fi, self.plan, self.ret_type, self.memo_name, self.memo_key, self.loops)
        self.fi = fi
        self.loops = []
        ret = self.ret_ct(fi)
        params = self.param_list(fi)
        sig = self.fn_sig(fi)
        self.plan = self.make_plan(fi.scope, s.body, fi.params)
        self.ret_type = fi.ret
        self.memo_name = self.memo_key = None
        if fi.memo and ret != "void":
            pts = [self.ct(self.sym_type(p)) for p in fi.params]
            names = [self.cn(p) for p in fi.params]
            kt = pts[0] if len(pts) == 1 else f"tuple<{', '.join(pts)}>"
            key = names[0] if len(pts) == 1 else f"make_tuple({', '.join(names)})"
            mname = self.fresh(f"memo_{name}")
            saved_fi = self.fi
            self.fi = saved[0]
            self.out(f"map<{kt}, {ret}> {mname};  // @lru_cache")
            self.fi = saved_fi
            self.memo_name, self.memo_key = mname, key
        recursive = fi.recursive or any(isinstance(n, ast.Name) and self.S(n) is sym for b in s.body
                                        for n in ast.walk(b))
        if recursive or not decl:
            head = f"function<{sig}> {name} = " if decl else f"{name} = "
        else:
            head = f"auto {name} = "
        rt = f" -> {ret}" if ret not in ("auto",) else ""
        self.out(f"{head}[&]({params}){rt} {{", s.lineno)
        with self.indent():
            if self.memo_name:
                self.out(f"auto key = {self.memo_key};")
                self.out(f"if (auto it = {self.memo_name}.find(key); it != {self.memo_name}.end()) return it->second;")
            self.body(s.body, docstring_ok=True)
            if ret not in ("void", "auto") and not _always(s.body):
                self.out("return {};" if fi.ret.k != "opt" else "return nullopt;")
        self.out("};")
        self.fi, self.plan, self.ret_type, self.memo_name, self.memo_key, self.loops = saved


def ast_tuple_t(dt: Ty) -> Ty:
    from .types import Tuple
    return Tuple(dt.args[0], dt.args[1])


def _assign_args(fill: str) -> str:
    """vector::assign wymaga (liczba, wartość) — dla pustego elementu dopisuje `{}`."""
    depth = 0
    for ch in fill:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return fill
    return f"{fill}, {{}}"


def _balanced(s: str) -> bool:
    """Czy nawias otwierający na początku zamyka się dokładnie na końcu."""
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0 and i != len(s) - 1:
                return False
    return depth == 0


def _always(stmts) -> bool:
    from .emit import _always_returns
    return _always_returns(stmts)


__all__ = ["StmtMixin", "P_EQ", "P_COND", "FLOAT", "char_lit"]

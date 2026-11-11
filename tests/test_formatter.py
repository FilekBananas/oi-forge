"""Testy formatera/upiększacza C++ (oiforge.tools.formatter + _fmt_engine).

Uruchom: python3 -m unittest tests.test_formatter -v
Test korpusowy (238 programów ucznia + kod z data/kb) uruchamia się, gdy są dane w user_data/programs.
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oiforge.tools import _fmt_engine as E  # noqa: E402
from oiforge.tools import formatter as F  # noqa: E402

PROGRAMS = ROOT / "user_data" / "programs"
INDEX = PROGRAMS / "index.json"


def opts(**kw):
    o = dict(E.BASE_STYLE)
    o.update(kw)
    return E.normalize_options(o)


def preset(name, **kw):
    o = dict(E.BASE_STYLE)
    o.update(E.PRESETS[name]["options"])
    o.update(kw)
    return E.normalize_options(o)


def fmt(code, **kw):
    return E.format_unchecked(code, opts(**kw))


class Guarantees:
    """Mixin: każdy przypadek sprawdza zachowanie tokenów i idempotencję."""

    def check(self, code, o=None, **kw):
        o = o or opts(**kw)
        out = E.format_unchecked(code, o)
        self.assertEqual(E.signature(out), E.signature(code), f"tokeny się zmieniły:\n{out}")
        self.assertEqual(E.comment_signature(out), E.comment_signature(code), "komentarze się zmieniły")
        again = E.format_unchecked(out, o)
        self.assertEqual(again, out, f"brak idempotencji:\n{out}\n---\n{again}")
        return out


# ============================================================================================ przypadki brzegowe
class TestTemplatesAndShifts(Guarantees, unittest.TestCase):
    def test_nested_template_closing(self):
        out = self.check("vector<vector<int>>a;map<int,vector<pair<int,int>>> m;")
        self.assertIn("vector<vector<int>> a;", out)
        self.assertIn("map<int, vector<pair<int, int>>> m;", out)

    def test_old_style_space_between_closers_kept(self):
        out = self.check("vector<vector<int> > a;")
        self.assertIn("> >", out)          # sklejenie dałoby inny token `>>`

    def test_shift_vs_template(self):
        out = self.check("int x=a>>b; int y=1<<k; vector<int> v; x>>=1;")
        self.assertIn("x = a >> b;", out)
        self.assertIn("y = 1 << k;", out)
        self.assertIn("vector<int> v;", out)
        self.assertIn("x >>= 1;", out)

    def test_comparison_is_not_template(self):
        out = self.check("if(a<b&&c>d) x=1; for(int i=0;i<n;i++) s+=i; bool t=a<b;")
        self.assertIn("if (a < b && c > d)", out)
        self.assertIn("for (int i = 0; i < n; i++)", out)
        self.assertIn("bool t = a < b;", out)

    def test_stream_operators(self):
        out = self.check('cout<<x<<" "<<(1<<k)<<endl; cin>>n>>m;', preset("compact"))
        self.assertIn('cout << x << " " << (1<<k) << endl;', out)
        self.assertIn("cin >> n >> m;", out)

    def test_template_keyword_spacing(self):
        self.assertIn("template <typename T>", self.check("template<typename T>T mx(T a,T b){return a>b?a:b;}",
                                                          space_after_template=True))
        out = self.check("template<typename T>T mx(T a,T b){return a>b?a:b;}")
        self.assertIn("template<typename T> T mx(T a, T b) { return a > b ? a : b; }", out)

    def test_casts_and_template_calls(self):
        out = self.check("ll s=(ll)a*b; auto v=static_cast<long long>(x)*2; int m=max<int>(a,b); auto g=get<0>(t);")
        self.assertIn("ll s = (ll)a * b;", out)
        self.assertIn("static_cast<long long>(x) * 2", out)
        self.assertIn("max<int>(a, b)", out)
        self.assertIn("get<0>(t)", out)


class TestUnaryBinary(Guarantees, unittest.TestCase):
    def test_unary_minus(self):
        out = self.check("x=-y; z=a- -b; return -1; f(-x,-(a+b)); c=a*-1;")
        for s in ("x = -y;", "z = a - -b;", "return -1;", "f(-x, -(a + b));", "c = a * -1;"):
            self.assertIn(s, out)

    def test_pointer_vs_multiplication(self):
        out = self.check("int *p=&x; int a=b*c; int x2=(a+b)*c; Node* root=nullptr; cout<<*it; *p=5;"
                         "struct Node{int v;}; void f(int* a,const vector<int>&v);")
        for s in ("int* p = &x;", "int a = b * c;", "int x2 = (a + b) * c;", "Node* root = nullptr;",
                  "cout << *it;", "*p = 5;", "void f(int* a, const vector<int>& v);"):
            self.assertIn(s, out)

    def test_pointer_align_right(self):
        out = self.check("int* p=&x; const string& s=t; int a, *b; int** pp;", pointer_align="right")
        for s in ("int *p = &x;", "const string &s = t;", "int a, *b;", "int **pp;"):
            self.assertIn(s, out)

    def test_rvalue_ref_vs_and(self):
        out = self.check("void f(T&& x); auto&& y=g(); bool c=a&&b; int&& r=5;",
                         )
        self.assertIn("auto&& y = g();", out)
        self.assertIn("bool c = a && b;", out)
        self.assertIn("int&& r = 5;", out)

    def test_address_and_bitand(self):
        out = self.check('scanf("%d",&x); int m=a&b; auto lo=i&-i;')
        self.assertIn('scanf("%d", &x);', out)
        self.assertIn("int m = a & b;", out)
        self.assertIn("lo = i & -i;", out)

    def test_increments(self):
        out = self.check("i++;++j;a[i]++;x=y++ +1;--k;")
        for s in ("i++;", "++j;", "a[i]++;", "--k;"):
            self.assertIn(s, out)


class TestLayout(Guarantees, unittest.TestCase):
    CODE = ("int main(){int n;cin>>n;if(n>0){cout<<n;}else{cout<<0;}"
            "for(int i=0;i<n;i++){s+=i;}while(n--){}return 0;}")

    def test_attach(self):
        out = self.check(self.CODE, short_blocks="never")
        self.assertIn("int main() {\n    int n;\n    cin >> n;\n    if (n > 0) {\n        cout << n;\n    } else {", out)

    def test_allman(self):
        out = self.check(self.CODE, preset("allman"))
        self.assertIn("int main()\n{\n    int n;", out)
        self.assertIn("    if (n > 0)\n    {\n        cout << n;\n    }\n    else\n    {", out)

    def test_kr_functions_on_new_line(self):
        out = self.check(self.CODE, preset("kr"))
        self.assertIn("int main()\n{", out)
        self.assertIn("if (n > 0) {", out)

    def test_stroustrup_else(self):
        out = self.check(self.CODE, brace_style="stroustrup", short_blocks="never")
        self.assertIn("}\n    else {", out)

    def test_gnu_lite(self):
        out = self.check(self.CODE, brace_style="gnu", short_blocks="never", indent_width=2)
        self.assertIn("  if (n > 0)\n    {\n      cout << n;\n    }", out)

    def test_else_newline_option(self):
        out = self.check(self.CODE, else_newline=True, short_blocks="never")
        self.assertIn("    }\n    else {", out)

    def test_indent_width_and_tabs(self):
        self.assertIn("\n  int n;", self.check(self.CODE, indent_width=2))
        self.assertIn("\n\tint n;", self.check(self.CODE, use_tabs=True))

    def test_keyword_and_brace_spacing_off(self):
        out = self.check(self.CODE, space_after_keyword=False, space_before_brace=False, short_blocks="never")
        self.assertIn("if(n > 0){", out)
        self.assertIn("int main(){", out)

    def test_short_if(self):
        code = "if(x) return 1;\nif(y)\n    return 2;\n"
        self.assertEqual(self.check(code, short_if="keep"), "if (x) return 1;\nif (y)\n    return 2;\n")
        self.assertEqual(self.check(code, short_if="never"), "if (x)\n    return 1;\nif (y)\n    return 2;\n")
        self.assertEqual(self.check(code, short_if="always"), "if (x) return 1;\nif (y) return 2;\n")

    def test_short_blocks(self):
        code = "int f(){return 1;}\nint g(){\nreturn 2;\n}\n"
        self.assertEqual(self.check(code, short_blocks="keep"), "int f() { return 1; }\nint g() {\n    return 2;\n}\n")
        self.assertEqual(self.check(code, short_blocks="always"), "int f() { return 1; }\nint g() { return 2; }\n")
        self.assertIn("int f() {\n    return 1;\n}", self.check(code, short_blocks="never"))

    def test_for_forever_and_empty_blocks(self):
        out = self.check("for(;;){} while(x){} void f(){} struct E{};", short_blocks="keep")
        self.assertIn("for (;;) {}", out)
        self.assertIn("void f() {}", out)
        self.assertIn("struct E {};", out)

    def test_for_semicolon_spacing(self):
        self.assertIn("for(int i=0;i<n;i++)", self.check("for (int i = 0; i < n; i++) x++;", preset("compact")))
        self.assertIn("for (int i = 0;i < n;i++)", self.check("for(int i=0;i<n;i++) x++;", space_for_semicolon=False))

    def test_range_for_and_structured_bindings(self):
        out = self.check("for(auto&[a,b]:m) cout<<a; auto [x,y]=p; for(const auto &e:v){}")
        self.assertIn("for (auto& [a, b] : m) cout << a;", out)
        self.assertIn("auto [x, y] = p;", out)
        self.assertIn("for (const auto& e : v) {}", out)

    def test_switch_case_indent(self):
        code = "switch(x){case 1: a++; break;\ncase 2:{int t=1;break;}\ndefault: b++;}"
        out = self.check(code)
        self.assertIn("switch (x) {\n    case 1: a++; break;\n    case 2: {\n        int t = 1;\n        break;\n    }\n"
                      "    default: b++;\n}", out)
        out2 = self.check(code, indent_case=False, short_if="never")
        self.assertIn("switch (x) {\ncase 1:\n    a++;\n    break;", out2)

    def test_namespace_indent(self):
        code = "namespace geo{struct P{int x;};}"
        self.assertIn("namespace geo {\nstruct P {", self.check(code, short_blocks="never"))
        self.assertIn("namespace geo {\n    struct P {", self.check(code, indent_namespace=True, short_blocks="never"))

    def test_access_specifiers(self):
        code = "class A{public:A(int n):n(n){}\nprivate:int n;};"
        out = self.check(code)
        self.assertIn("class A {\npublic:\n    A(int n) : n(n) {}\nprivate:\n    int n;\n};", out)
        self.assertIn("\n  public:", self.check(code, access_offset="indent", indent_width=2))

    def test_max_empty_lines_and_block_edges(self):
        code = "int a;\n\n\n\n\nint b;\nvoid f() {\n\n    x();\n\n}\n"
        self.assertEqual(self.check(code, max_empty_lines=1), "int a;\n\nint b;\nvoid f() {\n    x();\n}\n")
        self.assertIn("int a;\n\n\nint b;", self.check(code, max_empty_lines=2))

    def test_separate_definitions(self):
        out = self.check("int a;\nint f(){return 1;}\nint g(){return 2;}\n// komentarz\nint h(){return 3;}",
                         separate_definitions=True)
        self.assertEqual(out, "int a;\n\nint f() { return 1; }\n\nint g() { return 2; }\n\n// komentarz\n"
                              "int h() { return 3; }\n")

    def test_preprocessor_left_and_indented(self):
        code = "int main(){\n  #ifdef LOCAL\n  x();\n  #endif\n}"
        self.assertIn("\n#ifdef LOCAL\n    x();\n#endif\n", self.check(code))
        self.assertIn("\n    #ifdef LOCAL\n", self.check(code, pp_left=False))

    def test_include_normalized(self):
        out = self.check("#include<bits/stdc++.h>\n#include  \"moj.h\"  // lokalny\nint x;")
        self.assertTrue(out.startswith('#include <bits/stdc++.h>\n#include "moj.h" // lokalny\n'))

    def test_brace_init(self):
        out = self.check("vector<int>v={1,2,3}; int a[]{4,5}; pair<int,int>p{1,2}; return {a,b};"
                         "struct S{int x=0;vector<int> v{1,2};};")
        for s in ("vector<int> v = {1, 2, 3};", "int a[]{4, 5};", "pair<int, int> p{1, 2};", "return {a, b};",
                  "int x = 0;", "vector<int> v{1, 2};"):
            self.assertIn(s, out)
        self.assertIn("v = { 1, 2, 3 };", self.check("vector<int>v={1,2,3};", space_inside_braces=True))

    def test_multiline_initializer_kept(self):
        code = "int d[4][2] = {\n{0,1},\n  {1,0},\n{0,-1},\n{-1,0}\n};"
        out = self.check(code)
        self.assertEqual(out, "int d[4][2] = {\n    {0, 1},\n    {1, 0},\n    {0, -1},\n    {-1, 0}\n};\n")

    def test_continuation_alignment(self):
        out = self.check("if(a>b&&\nb>c) x=f(aaa,\nbbb);")
        self.assertIn("if (a > b &&\n    b > c)", out)
        self.assertIn("x = f(aaa,\n", out)


class TestLambdasMacrosStrings(Guarantees, unittest.TestCase):
    def test_inline_lambda(self):
        out = self.check("sort(a.begin(),a.end(),[](int x,int y){return x>y;});auto f=[&](int v)->int{return v*2;};")
        self.assertIn("sort(a.begin(), a.end(), [](int x, int y) { return x > y; });", out)
        self.assertIn("auto f = [&](int v) -> int { return v * 2; };", out)

    def test_multiline_lambda(self):
        code = ("int main(){\nfunction<void(int,int)> dfs=[&](int v,int p){\nfor(int u:g[v]) if(u!=p) dfs(u,v);\n};\n"
                "sort(a,a+n,[&](int x,int y){\nreturn x<y;\n});\n}")
        out = self.check(code)
        # ciało for-a to złożona instrukcja (if) → for w dwóch liniach, krótki if zostaje w jednej
        self.assertIn("    function<void(int, int)> dfs = [&](int v, int p) {\n        for (int u : g[v])\n"
                      "            if (u != p) dfs(u, v);\n    };", out)
        self.assertIn("    sort(a, a + n, [&](int x, int y) {\n        return x < y;\n    });", out)

    def test_lambda_captures(self):
        out = self.check("auto a=[=](){return x;}; auto b=[&,x](){}; auto c=[this]{return v;};")
        self.assertIn("[=]() { return x; }", out)
        self.assertIn("[&, x]() {}", out)
        self.assertIn("[this] { return v; }", out)

    def test_multiline_macro_untouched(self):
        code = "#define SWAP(a,b) do{ \\\n   int t=a;a=b; \\\n  b=t; }while(0)\nint main(){SWAP(x,y);}"
        out = self.check(code)
        self.assertIn("#define SWAP(a,b) do{ \\\n   int t=a;a=b; \\\n  b=t; }while(0)\n", out)

    def test_loop_macro(self):
        out = self.check("#define FOR(i,n) for(int i=0;i<(n);i++)\nint main(){FOR(i,n)\ncin>>a[i];\nFOR(i,n){s+=a[i];}}",
                         short_blocks="never")
        self.assertIn("    FOR(i, n)\n        cin >> a[i];\n    FOR(i, n) {\n        s += a[i];\n    }", out)

    def test_raw_string_untouched(self):
        code = 'string s=R"x(  a  =  b\n   c )x";int y=1;'
        out = self.check(code)
        self.assertIn('R"x(  a  =  b\n   c )x"', out)

    def test_comments_in_expressions(self):
        code = "int x = a + /* srodek */ b; // koniec\nint y = f(1, // pierwszy\n2);\n/* blok\n   dwa */\nint z;"
        out = self.check(code)
        self.assertIn("int x = a + /* srodek */ b; // koniec", out)
        self.assertIn("int y = f(1, // pierwszy\n          2);", out)
        self.assertIn("/* blok\n   dwa */\nint z;", out)

    def test_comment_between_if_and_brace(self):
        out = self.check("if(x) // dlaczego\n{\ny();\n}")
        self.assertIn("if (x) // dlaczego\n{\n    y();\n}", out)

    def test_trailing_comment_spaces(self):
        self.assertIn("x++;  // c", self.check("x++; // c", trailing_comment_spaces=2))

    def test_operator_overloads(self):
        out = self.check("struct P{int x;bool operator<(const P&o)const{return x<o.x;}"
                         "P operator+(const P&o)const{return {x+o.x};}int operator()(int a){return a;}};"
                         "ostream&operator<<(ostream&os,const P&p){return os<<p.x;}")
        self.assertIn("bool operator<(const P& o) const { return x < o.x; }", out)
        self.assertIn("P operator+(const P& o) const { return {x + o.x}; }", out)
        self.assertIn("int operator()(int a) { return a; }", out)
        self.assertIn("ostream& operator<<(ostream& os, const P& p) { return os << p.x; }", out)

    def test_user_defined_literal_and_digit_separators(self):
        out = self.check('auto s="abc"s; long long n=1\'000\'000; auto t=10ms;')
        self.assertIn('auto s = "abc"s;', out)
        self.assertIn("n = 1'000'000;", out)
        self.assertIn("t = 10ms;", out)

    def test_non_ascii_identifiers(self):
        out = self.check("int żółw=5; cout<<żółw;")
        self.assertIn("int żółw = 5;", out)

    def test_robust_on_broken_code(self):
        for code in ("int main() { if (x) {", "}}} int a;", "void f( { ;", "a = (b + ;", "#if A\nif (x) {\n#else\n"
                     "if (y) {\n#endif\n}\n", "template<class T", "x ? : y", "case 1: default:"):
            out, warn = E.format_source(code, opts())
            self.assertEqual(E.signature(out), E.signature(code), code)

    def test_empty_and_crlf(self):
        self.assertEqual(fmt(""), "")
        self.assertEqual(fmt("   \n\n"), "")
        self.assertEqual(fmt("int a;\r\nint b;\r\n"), "int a;\nint b;\n")

    def test_long_line_breaking(self):
        code = ("int main(){cout<<aaaaaaaaaa<<' '<<bbbbbbbbbbbbb<<' '<<cccccccccccccc<<' '<<ddddddddddddd<<' '<<"
                "eeeeeeeeeeee<<'\\n'; f(aaaaaaaaaaaa, bbbbbbbbbbbbbb, cccccccccccccc, dddddddddddddd, eeeeeeeeeeeeeee, "
                "ffffffffffff);}")
        out = self.check(code, max_line_length=60)
        self.assertTrue(all(len(ln) <= 60 for ln in out.splitlines()), out)
        self.assertIn("\n        << ", out)

    def test_compact_preset(self):
        out = self.check("int x = a + b * c; if (x == 1) { y = 2; }", preset("compact"))
        self.assertIn("int x=a+b*c;", out)
        self.assertIn("if(x==1) { y=2; }", out)


# ============================================================================================ API stylu
class TestStyleApi(unittest.TestCase):
    def test_resolve_presets(self):
        self.assertEqual(F.resolve_style("google")["indent_width"], 2)
        self.assertEqual(F.resolve_style("Kompaktowy CP")["space_assign"], False)
        self.assertEqual(F.resolve_style("K&R")["brace_style"], "kr")
        s = F.resolve_style({"preset": "llvm", "indent_width": 3})
        self.assertEqual((s["indent_width"], s["pointer_align"]), (3, "right"))
        self.assertEqual(F.resolve_style({"preset": "allman", "options": {"use_tabs": True}})["use_tabs"], True)

    def test_format_code_uses_saved_style(self):
        fake = {"format_style": {"preset": "moja", "moja": {"indent_width": 3, "space_after_keyword": False}}}
        with mock.patch.object(F.store, "settings", return_value=fake):
            out = F.format_code("int main(){if(x){y();}}")
        self.assertIn("\n   if(x) {", out)
        with mock.patch.object(F.store, "settings", return_value={"format_style": {"preset": "google"}}):
            self.assertIn("\n  if (x) {", F.format_code("int main(){if(x){\ny();}}"))

    def test_format_code_never_breaks_tokens(self):
        bad = "int a = 1 /* niedomknięty"
        self.assertEqual(E.signature(F.format_code(bad, "moja")), E.signature(bad))

    def test_save_style_and_presets_handler(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(F.store.config, "STORE_DIR", Path(td)):
            F.handle_style({"style": {"preset": "allman", "indent_width": 2}})
            pres = F.handle_presets({})
            self.assertEqual(pres["active"], "moja")
            self.assertEqual(pres["current"]["brace_style"], "allman")
            self.assertEqual(pres["current"]["indent_width"], 2)
            self.assertEqual(len(pres["presets"]), 6)
            F.handle_style({"preset": "google"})
            self.assertEqual(F.handle_presets({})["active"], "google")
            r = F.handle_format({"code": "int main(){return 0;}", "diff": True})
            self.assertTrue(r["changed"])
            self.assertIn("int main() {", r["code"])
            self.assertTrue(any(d["type"] == "add" for d in r["diff"]))

    def test_learn_style_from_programs(self):
        progs = ["#include <cstdio>\nint main()\n{\n  int x = 0;\n  if(x > 0)\n  {\n    x++;\n  }\n  else\n  {\n"
                 "    x--;\n  }\n  for(int i = 0;i < 3;i++)\n  {\n    x += i;\n  }\n  return x;\n}\n"] * 3
        res = F.learn_style(progs)
        o = res["options"]
        self.assertEqual(o["indent_width"], 2)
        self.assertEqual(o["brace_style"], "allman")
        self.assertFalse(o["space_after_keyword"])
        self.assertFalse(o["space_for_semicolon"])
        self.assertTrue(o["space_compare"])
        self.assertEqual(res["programs"], 3)
        self.assertTrue(any(e["key"] == "brace_style" for e in res["evidence"]))

    def test_learn_handler_with_codes_without_saving(self):
        r = F.handle_learn({"codes": ["int main() {\n\tif (x) {\n\t\ty();\n\t}\n}\n"], "save": False})
        self.assertFalse(r["saved"])
        self.assertTrue(r["options"]["use_tabs"])

    @unittest.skipUnless(INDEX.exists(), "brak user_data/programs/index.json")
    def test_learn_on_student_programs(self):
        res = F.learn_style(F._user_programs())
        self.assertGreaterEqual(res["programs"], 200)
        self.assertEqual(res["options"]["indent_width"], 4)
        self.assertEqual(res["options"]["brace_style"], "attach")


# ============================================================================================ upiększacz
class TestBeautifier(unittest.TestCase):
    def test_includes_sorted_and_deduplicated(self):
        code = '#include <vector>\n#include<iostream>\n\n#include <vector>\n#include "a.h"\nint x;\n'
        new, n = F.t_includes(code, sort=True, bits=False)
        self.assertEqual(new, '#include <iostream>\n#include <vector>\n#include "a.h"\nint x;\n')
        self.assertGreater(n, 0)

    def test_bits_stdcpp(self):
        code = "#include <vector>\n#include <algorithm>\n#include <ext/pb_ds/assoc_container.hpp>\nint x;\n"
        new, _ = F.t_includes(code, sort=True, bits=True)
        self.assertEqual(new, "#include <bits/stdc++.h>\n#include <ext/pb_ds/assoc_container.hpp>\nint x;\n")
        new2, _ = F.t_includes("#include <bits/stdc++.h>\n\n#include <numeric>\nint y;\n", True, True)
        self.assertEqual(new2, "#include <bits/stdc++.h>\nint y;\n")

    def test_endl(self):
        new, n = F.t_endl('cout << x << endl; std::cout << "a" << std::endl; cout << endl;')
        self.assertEqual(new, "cout << x << '\\n'; std::cout << \"a\\n\"; cout << '\\n';")
        self.assertEqual(n, 3)

    def test_namespace_add_and_remove(self):
        new, _ = F.t_namespace_std("#include <iostream>\nint main(){std::cout<<std::max(1,2);}", "add")
        self.assertIn("#include <iostream>\nusing namespace std;", new)
        self.assertIn("cout<<max(1,2)", new)
        new2, _ = F.t_namespace_std("#include <iostream>\nusing namespace std;\nint main(){cout<<max(1,2);}", "remove")
        self.assertNotIn("using namespace", new2)
        self.assertIn("std::cout<<std::max(1,2)", new2)

    def test_namespace_remove_respects_user_names(self):
        new, _ = F.t_namespace_std("using namespace std;\nint count = 0;\nint main(){count++; sort(a,a+3);}", "remove")
        self.assertIn("count++", new)
        self.assertNotIn("std::count", new)
        self.assertIn("std::sort", new)

    def test_braces(self):
        code = "int main(){\nif(a) x++;\nelse if(b) y++;\nelse\n  z++;\nfor(;;) for(;;) w++;\n}\n"
        new, n = F.t_braces(code, multiline=False)
        self.assertEqual(n, 5)
        out = F.format_code(new, "moja")
        # po bloku sklejonym w jedną linię `else` zaczyna nową linię
        self.assertIn("    if (a) { x++; }\n    else if (b) { y++; }\n    else { z++; }\n", out)
        self.assertIn("    for (;;) {\n        for (;;) { w++; }\n    }", out)
        res = F.beautify(code, {"braces": True}, {"preset": "moja", "short_blocks": "keep"})
        self.assertIn("    if (a) {\n        x++;\n    } else if (b) {", res["code"])

    def test_dead_code(self):
        code = ("int main(){\n    // wczytujemy dane\n    //cout << x << endl;\n    int x; // O(n log n)\n"
                "    //for(int i=0;i<n;i++){\n    //    s += a[i];\n    //}\n    return 0; // koniec\n}\n")
        new, n = F.t_remove_dead_code(code)
        self.assertIn("// wczytujemy dane", new)
        self.assertIn("// O(n log n)", new)
        self.assertIn("// koniec", new)
        self.assertNotIn("cout << x", new)
        self.assertNotIn("//for", new)
        self.assertNotIn("//}", new)
        self.assertEqual(n, 4)

    def test_looks_like_code(self):
        for s in ("cout << x << endl;", "for(int i=0;i<n;i++)", "dp[i][j] = max(dp[i-1][j], dp[i][j-1]);",
                  "int x;", "x++;", "return 0;"):
            self.assertTrue(F.looks_like_code(s), s)
        for s in ("sortujemy po x", "O(n log n)", "TODO: popraw", "wynik: 5", "Ala ma kota.", "dfs od korzenia",
                  "zamiana miejscami (swap)", "przypadek n = 1 trzeba obsłużyć osobno"):
            self.assertFalse(F.looks_like_code(s), s)

    def test_banner_and_whitespace(self):
        code = ("/******\n\n   Online C++ Compiler.\n   Code, Compile, Run\n\n*******/\n\n#include <cstdio>  \n"
                "int main() {   \n\n\n\n  return 0;\n}\n\n\n")
        res = F.beautify(code, {"format": False})
        self.assertTrue(res["code"].startswith("#include <cstdio>\nint main() {\n\n  return 0;\n}\n"), res["code"])
        ids = [c["id"] for c in res["changes"]]
        self.assertIn("remove_banner", ids)
        self.assertIn("trailing_ws", ids)
        self.assertIn("blank_lines", ids)

    def test_beautify_handler_returns_diff(self):
        r = F.handle_beautify({"code": "#include<vector>\n#include<vector>\nint main(){return 0;}",
                               "transforms": {"endl": True}})
        self.assertEqual(r["code"], "#include <vector>\n\nint main() { return 0; }\n")
        self.assertTrue(r["changed"])
        self.assertTrue(any(d["type"] == "del" for d in r["diff"]))
        self.assertIn("stats", r)


# ============================================================================================ korpus
def _corpus() -> list[tuple[str, str]]:
    items = []
    if INDEX.exists():
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        for rec in data["programs"]:
            p = PROGRAMS / rec["path"]
            if p.exists():
                items.append(("prog:" + rec["path"], p.read_text(encoding="utf-8", errors="replace")))
    for f in sorted(glob.glob(str(ROOT / "data" / "kb" / "*.json"))):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except ValueError:
            continue
        for a in d.get("articles", []):
            for i, b in enumerate(a.get("body", [])):
                if isinstance(b, dict) and b.get("type") == "code" and b.get("code"):
                    items.append((f"kb:{a.get('id')}:b{i}", b["code"]))
            for i, q in enumerate(a.get("qa", [])):
                if isinstance(q, dict) and q.get("code"):
                    items.append((f"kb:{a.get('id')}:qa{i}", q["code"]))
            for i, fn in enumerate(a.get("functions", [])):
                if isinstance(fn, dict) and fn.get("example"):
                    items.append((f"kb:{a.get('id')}:fn{i}", fn["example"]))
    return items


def check_preset(name: str) -> dict:
    """Formatuje cały korpus jednym presetem; zwraca liczniki naruszeń (używane też w procesach potomnych)."""
    o = preset(name)
    stats = {"preset": name, "items": 0, "programs": 0, "kb": 0, "token": 0, "comment": 0, "idempotency": 0,
             "fallback": 0, "examples": []}
    for key, code in _corpus():
        stats["items"] += 1
        stats["programs" if key.startswith("prog:") else "kb"] += 1
        src = code.replace("\r\n", "\n")
        try:
            out = E.format_unchecked(src, o)
        except Exception as e:  # noqa: BLE001
            stats["fallback"] += 1
            stats["examples"].append(f"{key}: wyjątek {e!r}")
            continue
        if E.signature(out) != E.signature(src):
            stats["token"] += 1
            stats["examples"].append(f"{key}: tokeny")
            continue
        if E.comment_signature(out) != E.comment_signature(src):
            stats["comment"] += 1
            stats["examples"].append(f"{key}: komentarze")
        if E.format_unchecked(out, o) != out:
            stats["idempotency"] += 1
            stats["examples"].append(f"{key}: idempotencja")
    stats["examples"] = stats["examples"][:10]
    return stats


@unittest.skipUnless(INDEX.exists() or glob.glob(str(ROOT / "data" / "kb" / "*.json")), "brak korpusu")
class TestCorpus(unittest.TestCase):
    def test_all_presets_on_student_programs_and_kb(self):
        names = E.PRESET_ORDER
        workers = max(1, min(len(names), (os.cpu_count() or 2)))
        try:
            with cf.ProcessPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(check_preset, names))
        except (OSError, cf.process.BrokenProcessPool, PermissionError):
            results = [check_preset(n) for n in names]
        for r in results:
            print(f"\n  [{r['preset']:8}] {r['programs']} programów + {r['kb']} fragmentów KB: "
                  f"naruszenia tokenów={r['token']}, komentarzy={r['comment']}, idempotencji={r['idempotency']}, "
                  f"wyjątków={r['fallback']}", end="")
        print()
        for r in results:
            self.assertEqual(r["fallback"], 0, r["examples"])
            self.assertEqual(r["token"], 0, r["examples"])
            self.assertEqual(r["comment"], 0, r["examples"])
            self.assertEqual(r["idempotency"], 0, r["examples"])

    @unittest.skipUnless(INDEX.exists(), "brak user_data/programs/index.json")
    def test_sample_of_30_programs_compiles_after_formatting(self):
        from oiforge.core import runner
        from oiforge import config
        if not config.CXX:
            self.skipTest("brak kompilatora C++")
        progs = [c for k, c in _corpus() if k.startswith("prog:")]
        step = max(1, len(progs) // 30)
        sample = progs[::step][:30]
        names = E.PRESET_ORDER

        def work(i_code):
            # programy ucznia to zaakceptowane rozwiązania (kompilowały się na sio2) — sprawdzamy wynik formatowania
            i, code = i_code
            name = names[i % len(names)]
            out = F.format_code(code, name)
            cr = runner.compile_cpp(out, mode="syntax")
            return name, cr.ok, cr.stderr[:600]

        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(work, enumerate(sample)))
        ok_after = sum(1 for _, ok, _ in res if ok)
        print(f"\n  kompilacja próbki: {ok_after}/{len(sample)} sformatowanych programów kompiluje się (presety rotacyjnie)")
        self.assertEqual(len(sample), 30)
        for name, ok, err in res:
            self.assertTrue(ok, f"[{name}] po formatowaniu nie kompiluje się:\n{err}")


if __name__ == "__main__":
    unittest.main()

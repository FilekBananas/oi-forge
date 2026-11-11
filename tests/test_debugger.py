"""Testy wizualnego debuggera: backend lldb (na maszynie z lldb), parser GDB/MI + wartości gdb (próbki),
pętla śledzenia gdb na atrapie transportu MI, tracer Pythona, oznaczanie zmian, analiza źródła."""
from __future__ import annotations

import os
import time
import unittest

from oiforge.tools import _dbg_gdb as G
from oiforge.tools import debugger as D

HAS_LLDB = D.lldb_interpreter() is not None


def step_at(steps, line, func=None, nth=0):
    hits = [s for s in steps if s["line"] == line and (func is None or s["func"] == func)]
    return hits[nth] if len(hits) > nth else None


def val(step, name):
    return step["vars"][name]


# ============================================================================================ lldb
CPP_MIX = r"""#include <bits/stdc++.h>
using namespace std;
int glob = 7;
int main() {
    int n;
    cin >> n;
    vector<int> v(n);
    int arr[5] = {0, 0, 0, 0, 0};
    long long sum = 0;
    for (int i = 0; i < n; i++) {
        cin >> v[i];
        arr[i] = v[i] * 2;
        sum += v[i];
    }
    vector<vector<int>> m(3, vector<int>(4, 0));
    for (int i = 0; i < 3; i++) {
        for (int j = 0; j < 4; j++) m[i][j] = i * 10 + j;
    }
    string s = "ala";
    s += "kot";
    map<string, int> cnt;
    cnt["x"] = 1;
    cnt["ab"] = 5;
    set<int> st = {5, 1, 3};
    pair<int, int> p = {4, 2};
    glob++;
    cout << sum << "\n";
    return 0;
}
"""

CPP_REC = r"""#include <bits/stdc++.h>
using namespace std;
long long fact(int n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}
int main() {
    long long r = fact(5);
    cout << r << endl;
    return 0;
}
"""

CPP_LOOP = r"""#include <bits/stdc++.h>
using namespace std;
int main() {
    int n = 400;
    vector<int> a(n);
    long long s = 0;
    for (int i = 0; i < n; i++) {
        a[i] = (i * 7) % 13;
        s += a[i];
    }
    cout << s << "\n";
}
"""

CPP_CRASH = r"""#include <bits/stdc++.h>
using namespace std;
int main() {
    int *p = nullptr;
    int x = 3;
    *p = x;
    return 0;
}
"""


@unittest.skipUnless(HAS_LLDB, "brak lldb z modułem Pythona na tej maszynie")
class LldbTraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mix = D.run_trace({"code": CPP_MIX, "input": "3\n4 8 15\n"})

    def test_mix_basic(self):
        r = self.mix
        self.assertIsNone(r.get("error"), r.get("error"))
        self.assertEqual(r["backend"], "lldb")
        self.assertEqual(r["reason"], "exit")
        self.assertEqual(r["exit_code"], 0)
        self.assertEqual(r["stdout"].strip(), "27")
        self.assertFalse(r["truncated"])

    def test_vector_and_array_values(self):
        steps = self.mix["steps"]
        st = step_at(steps, 15)                       # po pętli wczytującej
        self.assertIsNotNone(st)
        v = val(st, "v")
        self.assertEqual(v["kind"], "vector")
        self.assertEqual(v["items"], ["4", "8", "15"])
        self.assertEqual(v["size"], 3)
        a = val(st, "arr")
        self.assertEqual(a["kind"], "array")
        self.assertEqual(a["items"], ["8", "16", "30", "0", "0"])
        self.assertEqual(val(st, "sum")["value"], "27")
        # zmiana pojedynczej komórki tablicy: w drugiej iteracji (i=1) po linii 12 zmienia się arr[1]
        st13 = [s for s in steps if s["line"] == 13]
        self.assertGreaterEqual(len(st13), 2)
        self.assertIn(1, st13[1]["vars"]["arr"].get("changed_idx", []))
        self.assertTrue(st13[1]["vars"]["arr"].get("changed"))

    def test_matrix(self):
        st = step_at(self.mix["steps"], 19)
        m = val(st, "m")
        self.assertEqual(m["kind"], "matrix")
        self.assertEqual(m["rows"][2], ["20", "21", "22", "23"])
        self.assertEqual(m["size"], [3, 4])

    def test_string_map_set_pair_global(self):
        steps = self.mix["steps"]
        st = step_at(steps, 27)
        self.assertEqual(val(st, "s")["kind"], "string")
        self.assertEqual(val(st, "s")["value"], "alakot")
        mp = val(st, "cnt")
        self.assertEqual(mp["kind"], "map")
        self.assertEqual(mp["items"], [["ab", "5"], ["x", "1"]])
        self.assertEqual(val(st, "st")["kind"], "set")
        self.assertEqual(val(st, "st")["items"], ["1", "3", "5"])
        self.assertEqual(val(st, "p")["kind"], "pair")
        self.assertEqual(val(st, "p")["items"], ["4", "2"])
        g_before = val(step_at(steps, 26), "glob")
        g_after = val(st, "glob")
        self.assertEqual(g_before["scope"], "global")
        self.assertEqual((g_before["value"], g_after["value"]), ("7", "8"))
        self.assertTrue(g_after.get("changed"))

    def test_stdout_incremental(self):
        steps = self.mix["steps"]
        last = steps[-1]
        self.assertEqual(last["out"], len("27\n"))
        before = step_at(steps, 27)
        self.assertEqual(before["out"], 0)
        after = step_at(steps, 28)
        self.assertEqual(after["stdout"], "27\n")

    def test_recursion(self):
        r = D.run_trace({"code": CPP_REC, "input": ""})
        self.assertIsNone(r.get("error"), r.get("error"))
        steps = r["steps"]
        depths = [s["depth"] for s in steps if s["func"] == "fact"]
        self.assertEqual(max(depths), 5)
        deepest = max((s for s in steps if s["func"] == "fact"), key=lambda s: s["depth"])
        self.assertEqual(val(deepest, "n")["value"], "1")
        self.assertEqual([e["func"] for e in deepest["stack"]], ["fact"] * 5 + ["main"])
        self.assertEqual(deepest["stack"][1]["args"], "n=2")
        rets = [s["ret"]["value"] for s in steps if s.get("ret") and "value" in s["ret"]]
        self.assertEqual(rets, ["1", "2", "6", "24", "120"])
        self.assertEqual(r["stdout"].strip(), "120")

    def test_performance_1000_steps(self):
        t0 = time.time()
        r = D.run_trace({"code": CPP_LOOP, "input": "", "max_steps": 1000})
        dt = time.time() - t0
        self.assertIsNone(r.get("error"), r.get("error"))
        self.assertEqual(len(r["steps"]), 1000)
        self.assertEqual(r["reason"], "max_steps")
        self.assertTrue(r["truncated"])
        load = os.getloadavg()[0] / max(1, os.cpu_count() or 1) if hasattr(os, "getloadavg") else 1.0
        limit = 15.0 * max(1.0, load)              # na przeciążonej maszynie skaluj limit obciążeniem
        print(f"\n[debugger] 1000 kroków lldb: {dt:.1f} s ({r['stats']['steps_per_s']} kroków/s w pętli), "
              f"obciążenie {load:.1f}×, limit {limit:.0f} s")
        self.assertLess(dt, limit)
        st = r["steps"][-1]
        self.assertEqual(val(st, "a")["size"], 400)
        self.assertEqual(len(val(st, "a")["items"]), 256)
        self.assertTrue(val(st, "a").get("truncated"))

    def test_crash_and_watch(self):
        r = D.run_trace({"code": CPP_CRASH, "input": "", "watch": "x"})
        self.assertEqual(r["reason"], "crash")
        last = r["steps"][-1]
        self.assertIn("crash", last)
        self.assertEqual(last["line"], 6)
        self.assertEqual(set(last["vars"]), {"x"})
        self.assertIn("niedozwolonej pamięci", last["crash"])

    def test_compile_error(self):
        r = D.run_trace({"code": "int main() { int x = ; }", "input": ""})
        self.assertIn("kompilacji", r["error"])
        self.assertEqual(r["steps"], [])


# ============================================================================================ GDB/MI parser
class MIParserTest(unittest.TestCase):
    def test_result_bkpt(self):
        r = G.parse_mi_line('12^done,bkpt={number="1",type="breakpoint",disp="keep",enabled="y",addr="0x0000000000401136",'
                            'func="main",file="main.cpp",fullname="/tmp/a/main.cpp",line="5",thread-groups=["i1"],times="0"}')
        self.assertEqual(r["kind"], "result")
        self.assertEqual(r["token"], 12)
        self.assertEqual(r["cls"], "done")
        self.assertEqual(r["data"]["bkpt"]["number"], "1")
        self.assertEqual(r["data"]["bkpt"]["thread-groups"], ["i1"])

    def test_multiple_locations(self):
        r = G.parse_mi_line('5^done,bkpt={number="2",type="breakpoint",addr="<MULTIPLE>"},{number="2.1",addr="0x1"},'
                            '{number="2.2",addr="0x2"}')
        bk = r["data"]["bkpt"]
        self.assertIsInstance(bk, list)
        self.assertEqual([b["number"] for b in bk], ["2", "2.1", "2.2"])

    def test_stopped_record(self):
        r = G.parse_mi_line('*stopped,reason="breakpoint-hit",disp="keep",bkptno="3",frame={addr="0x0000555555555189",'
                            'func="fact",args=[{name="n",value="4"}],file="main.cpp",fullname="/tmp/x/main.cpp",line="7",'
                            'arch="i386:x86-64"},thread-id="1",stopped-threads="all",core="2"')
        self.assertEqual(r["kind"], "exec")
        self.assertEqual(r["cls"], "stopped")
        self.assertEqual(r["data"]["frame"]["args"], [{"name": "n", "value": "4"}])
        self.assertEqual(r["data"]["bkptno"], "3")

    def test_stack_list_frames(self):
        r = G.parse_mi_line('^done,stack=[frame={level="0",addr="0x1",func="fact",file="main.cpp",line="5"},'
                            'frame={level="1",addr="0x2",func="main",file="main.cpp",line="12"}]')
        self.assertEqual([f["func"] for f in r["data"]["stack"]], ["fact", "main"])

    def test_variables_and_escapes(self):
        r = G.parse_mi_line(r'^done,variables=[{name="s",arg="1",type="std::string",value="\"za\305\274\303\263\305\202\303\263\\n\""},'
                            r'{name="v",type="std::vector<int, std::allocator<int> >"}]')
        vs = r["data"]["variables"]
        self.assertEqual(vs[0]["value"], '"zażółó\\n"')
        self.assertEqual(vs[0]["arg"], "1")
        self.assertNotIn("value", vs[1])

    def test_streams_prompt_notify(self):
        self.assertEqual(G.parse_mi_line('~"Breakpoint 1 at 0x401136\\n"'), {"kind": "console", "text": "Breakpoint 1 at 0x401136\n"})
        self.assertEqual(G.parse_mi_line("(gdb) ")["kind"], "prompt")
        n = G.parse_mi_line('=thread-group-added,id="i1"')
        self.assertEqual((n["kind"], n["cls"], n["data"]["id"]), ("notify", "thread-group-added", "i1"))
        self.assertEqual(G.parse_mi_line('^error,msg="No symbol \\"xx\\" in current context."')["data"]["msg"],
                         'No symbol "xx" in current context.')
        self.assertEqual(G.parse_mi_line("^running")["cls"], "running")
        self.assertIsNone(G.parse_mi_line(""))
        self.assertEqual(G.parse_mi_line('*stopped,reason="exited",exit-code="01"')["data"]["exit-code"], "01")


class GdbValueTest(unittest.TestCase):
    def node(self, s, t=""):
        return G.to_node(G.parse_gdb_value(s), t)

    def test_vector_pretty(self):
        n = self.node("std::vector of length 3, capacity 4 = {1, 2, 3}", "std::vector<int, std::allocator<int> >")
        self.assertEqual((n["kind"], n["items"], n["size"], n["type"]), ("vector", ["1", "2", "3"], 3, "vector<int>"))
        e = self.node("std::vector of length 0, capacity 0", "std::vector<int, std::allocator<int> >")
        self.assertEqual((e["kind"], e["items"], e["size"]), ("vector", [], 0))

    def test_vector_of_vectors_matrix(self):
        s = ("std::vector of length 2, capacity 2 = {std::vector of length 3, capacity 3 = {1, 2, 3}, "
             "std::vector of length 3, capacity 3 = {4, 5, 6}}")
        n = self.node(s, "std::vector<std::vector<int, std::allocator<int> >, std::allocator<std::vector<int, std::allocator<int> > > >")
        self.assertEqual(n["kind"], "matrix")
        self.assertEqual(n["rows"], [["1", "2", "3"], ["4", "5", "6"]])
        self.assertEqual(n["size"], [2, 3])

    def test_c_arrays_and_repeats(self):
        n = self.node("{5, 0 <repeats 15 times>, 7}", "int [17]")
        self.assertEqual(n["kind"], "array")
        self.assertEqual(len(n["items"]), 17)
        self.assertEqual(n["items"][:3], ["5", "0", "0"])
        self.assertEqual(n["items"][-1], "7")
        m = self.node("{{1, 2}, {3, 4}, {0, 0}}", "int [3][2]")
        self.assertEqual((m["kind"], m["rows"], m["size"]), ("matrix", [["1", "2"], ["3", "4"], ["0", "0"]], [3, 2]))
        big = self.node("{1, 2, 3...}", "int [100000]")
        self.assertTrue(big["truncated"])
        self.assertEqual(big["size"], 100000)

    def test_strings_and_chars(self):
        self.assertEqual(self.node('"abc"', "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"),
                         {"type": "string", "kind": "string", "value": "abc"})
        n = self.node('"ab", \'x\' <repeats 12 times>, "cd"', "std::string")
        self.assertEqual(n["value"], "ab" + "x" * 12 + "cd")
        self.assertEqual(self.node("97 'a'", "char")["value"], "'a'")
        self.assertEqual(self.node('"kot", \'\\000\' <repeats 96 times>', "char [100]")["value"], "kot")
        self.assertEqual(self.node('0x402004 "hello"', "const char *")["value"], "hello")

    def test_map_set_pair_struct(self):
        mp = self.node("std::map with 2 elements = {[1] = 10, [5] = 50}", "std::map<int, int, std::less<int>, std::allocator<std::pair<int const, int> > >")
        self.assertEqual((mp["kind"], mp["items"], mp["size"]), ("map", [["1", "10"], ["5", "50"]], 2))
        sm = self.node('std::map with 1 element = {["ala"] = 3}', "std::map<std::string, int>")
        self.assertEqual(sm["items"], [["ala", "3"]])
        st = self.node("std::set with 3 elements = {1, 3, 5}", "std::set<int, std::less<int>, std::allocator<int> >")
        self.assertEqual((st["kind"], st["items"]), ("set", ["1", "3", "5"]))
        um = self.node("std::unordered_map with 2 elements = {[3] = 4, [1] = 2}", "std::unordered_map<int, int>")
        self.assertEqual(um["kind"], "map")
        pr = self.node("{first = 4, second = 2}", "std::pair<int, int>")
        self.assertEqual((pr["kind"], pr["items"]), ("pair", ["4", "2"]))
        sv = self.node("{x = 1, y = -3, name = \"p\"}", "Point")
        self.assertEqual((sv["kind"], sv["fields"]), ("struct", {"x": "1", "y": "-3", "name": "p"}))
        vp = self.node("std::vector of length 2, capacity 2 = {{first = 1, second = 2}, {first = 3, second = 4}}",
                       "std::vector<std::pair<int, int>, std::allocator<std::pair<int, int> > >")
        self.assertEqual(vp["kind"], "vector")
        self.assertEqual(vp["items"][1], {"kind": "pair", "items": ["3", "4"]})

    def test_misc(self):
        self.assertEqual(self.node("<optimized out>", "int")["value"], "<optimized out>")
        ref = self.node("@0x7fffffffe3c0: std::vector of length 2, capacity 2 = {7, 8}", "std::vector<int, std::allocator<int> > &")
        self.assertEqual(ref["items"], ["7", "8"])
        q = self.node("std::queue wrapping: std::deque with 2 elements = {1, 2}", "std::queue<int, std::deque<int, std::allocator<int> > >")
        self.assertEqual((q["kind"], q["items"], q["note"]), ("vector", ["1", "2"], "przód = pierwszy"))
        tp = self.node("std::tuple containing = {[1] = 1, [2] = 2.5, [3] = 120 'x'}", "std::tuple<int, double, char>")
        self.assertEqual((tp["kind"], tp["items"]), ("pair", ["1", "2.5", "'x'"]))
        bs = self.node("std::bitset = {[0] = 1, [3] = 1}", "std::bitset<5>")
        self.assertEqual(bs["items"], ["1", "0", "0", "1", "0"])
        ptr = self.node("(int *) 0x4052a0 <tab+8>", "int *")
        self.assertEqual(ptr["value"], "0x4052a0")
        self.assertEqual(self.node("true", "bool")["value"], "true")
        self.assertEqual(self.node("-1.5", "double")["value"], "-1.5")
        raw = self.node("{_M_impl = {_M_start = 0x0, _M_finish = 0x0}}", "std::vector<int>")
        self.assertIn("pretty printerów", raw["value"])

    def test_is_simple_type(self):
        self.assertTrue(G.is_simple_type("long long"))
        self.assertTrue(G.is_simple_type("int *"))
        self.assertFalse(G.is_simple_type("int [10]"))
        self.assertFalse(G.is_simple_type("std::vector<int>"))


# ============================================================================================ gdb na atrapie
class FakeMI:
    """Atrapa transportu GDB/MI: odpowiedzi to prawdziwe linie MI (parsowane przez parse_mi_line)."""

    def __init__(self, src):
        self.src = src
        self.log = []
        self.stops = [
            f'*stopped,reason="breakpoint-hit",bkptno="1",frame={{addr="0x401126",func="main",file="main.cpp",fullname="{src}",line="3"}}',
            f'*stopped,reason="breakpoint-hit",bkptno="2",frame={{addr="0x40112e",func="main",file="main.cpp",fullname="{src}",line="4"}}',
            f'*stopped,reason="breakpoint-hit",bkptno="3",frame={{addr="0x401140",func="main",file="main.cpp",fullname="{src}",line="5"}}',
            '*stopped,reason="exited-normally"',
        ]
        self.x = 0

    def cmd(self, c, timeout=15.0):
        self.log.append(c)
        if c.startswith("-symbol-list-lines"):
            line = ('^done,lines=[{pc="0x401120",line="2"},{pc="0x401126",line="3"},{pc="0x40112e",line="4"},'
                    '{pc="0x401140",line="5"},{pc="0x401148",line="6"}]')
        elif c.startswith("-data-disassemble"):
            line = ('^done,asm_insns=[{address="0x401120",func-name="main",offset="0",inst="push %rbp"},'
                    '{address="0x401126",func-name="main",offset="6",inst="movl"},'
                    '{address="0x40112e",func-name="main",offset="14",inst="movl"},'
                    '{address="0x401140",func-name="main",offset="32",inst="add"},'
                    '{address="0x401148",func-name="main",offset="40",inst="ret"}]')
        elif c.startswith("-break-insert"):
            num = 1 + sum(1 for x in self.log[:-1] if x.startswith("-break-insert"))
            line = f'^done,bkpt={{number="{num}",type="breakpoint",addr="0x0"}}'
        elif c.startswith("-exec-run") or c.startswith("-exec-continue"):
            line = "^running"
        elif c.startswith("-stack-info-depth"):
            line = '^done,depth="1"'
        elif c.startswith("-stack-list-frames 1 1"):
            line = '^done,stack=[frame={level="1",addr="0x7ffff7dd",func="__libc_start_main",from="/lib/libc.so.6"}]'
        elif c.startswith("-stack-list-frames"):
            line = f'^done,stack=[frame={{level="0",addr="0x40112e",func="main",file="main.cpp",fullname="{self.src}",line="4"}}]'
        elif c.startswith("-stack-list-variables"):
            self.x += 1
            line = (f'^done,variables=[{{name="x",type="int",value="{self.x * 10}"}},'
                    f'{{name="v",type="std::vector<int, std::allocator<int> >"}}]')
        elif c.startswith("-data-evaluate-expression") and '"v"' in c:
            line = f'^done,value="std::vector of length 2, capacity 2 = {{{self.x}, 9}}"'
        elif c.startswith("-data-evaluate-expression") and '"g"' in c:
            line = '^done,value="42"'
        elif c.startswith("-var-create"):
            line = '^done,name="var1",numchild="0",value="42",type="int",has_more="0"'
        else:
            line = "^done"
        return G.parse_mi_line(line)

    def wait_stopped(self, timeout):
        return G.parse_mi_line(self.stops.pop(0)) if self.stops else None

    def interrupt(self):
        pass

    def close(self):
        pass


class GdbFlowTest(unittest.TestCase):
    def test_trace_with_fake_transport(self):
        import tempfile
        d = tempfile.mkdtemp()
        src = os.path.join(d, "main.cpp")
        for nm in ("in.txt", "out.txt", "err.txt"):
            open(os.path.join(d, nm), "w").close()
        cfg = {"exe": os.path.join(d, "prog"), "source": src, "user_lines": 7, "input_path": os.path.join(d, "in.txt"),
               "output_path": os.path.join(d, "out.txt"), "stderr_path": os.path.join(d, "err.txt"),
               "max_steps": 100, "time_budget": 10, "globals": ["g"], "decl_lines": {"main": {"x": 2, "v": 2}}}
        events = []
        mi = FakeMI(src)
        G.trace(cfg, events.append, mi=mi)
        steps = [e for e in events if e["t"] == "step"]
        end = [e for e in events if e["t"] == "end"][0]
        self.assertEqual([s["line"] for s in steps], [3, 4, 5])
        self.assertEqual(end["reason"], "exit")
        self.assertEqual(end["exit_code"], 0)
        self.assertEqual(steps[0]["vars"]["x"]["value"], "10")
        self.assertEqual(steps[1]["vars"]["v"]["items"], ["2", "9"])
        self.assertEqual(steps[1]["vars"]["g"], {"type": "int", "kind": "scalar", "value": "42", "scope": "global"})
        # wejście do funkcji (offset 0) -> breakpoint liniowy; pozostałe po adresie
        inserts = [c for c in mi.log if c.startswith("-break-insert")]
        self.assertIn('-break-insert "', inserts[0])
        self.assertTrue(any(c.startswith("-break-insert *0x40112e") for c in inserts))
        # przełączanie: wyłącz trafiony, włącz poprzedni
        toggles = [c for c in mi.log if c.startswith("-break-disable") or c.startswith("-break-enable")]
        self.assertEqual(toggles[:3], ["-break-disable 1", "-break-enable 1", "-break-disable 2"])
        self.assertTrue(any(c.startswith("-exec-arguments <") for c in mi.log))


# ============================================================================================ Python
PY_CODE = """import sys
def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)
n = int(input())
a = list(map(int, input().split()))
grid = [[0] * 3 for _ in range(2)]
d = {}
for i, x in enumerate(a):
    d[x] = d.get(x, 0) + 1
    grid[i % 2][i % 3] += x
print(fact(4))
s = set(a)
b = a[10]
"""


class PythonTracerTest(unittest.TestCase):
    def test_python_trace(self):
        r = D.run_trace({"code": PY_CODE, "input": "5\n3 1 4 1 5\n", "lang": "python"})
        self.assertEqual(r["backend"], "python")
        self.assertEqual(r["reason"], "crash")
        steps = r["steps"]
        st = step_at(steps, 13)
        self.assertEqual(val(st, "a")["kind"], "vector")
        self.assertEqual(val(st, "a")["items"], ["3", "1", "4", "1", "5"])
        self.assertEqual(val(st, "grid")["kind"], "matrix")
        self.assertEqual(val(st, "grid")["rows"], [["3", "5", "4"], ["1", "1", "0"]])
        self.assertEqual(val(st, "d")["kind"], "map")
        self.assertEqual(val(st, "d")["items"], [["3", "1"], ["1", "2"], ["4", "1"], ["5", "1"]])
        deepest = max((s for s in steps if s["func"] == "fact"), key=lambda s: s["depth"])
        self.assertEqual(deepest["depth"], 4)
        self.assertEqual(val(deepest, "n")["value"], "1")
        self.assertEqual(r["stdout"], "24\n")
        self.assertIn("IndexError", steps[-1]["crash"])
        self.assertEqual(steps[-1]["line"], 15)
        self.assertEqual(val(step_at(steps, 15), "s")["kind"], "set")

    def test_python_limits_and_syntax(self):
        r = D.run_trace({"code": "i = 0\nwhile True:\n    i += 1\n", "lang": "python", "max_steps": 50})
        self.assertEqual(len(r["steps"]), 50)
        self.assertEqual(r["reason"], "max_steps")
        self.assertTrue(r["truncated"])
        r = D.run_trace({"code": "def f(:\n  pass\n", "lang": "python"})
        self.assertIn("składni", r["error"])


# ============================================================================================ pomocnicze
class HelpersTest(unittest.TestCase):
    def test_mark_changes(self):
        steps = [
            {"line": 1, "frame": "A", "stack": [{"id": "A"}], "vars": {"i": {"kind": "scalar", "value": "0"},
                                                                    "a": {"kind": "array", "items": ["1", "2", "3"]}}},
            {"line": 2, "frame": "A", "stack": [{"id": "A"}], "vars": {"i": {"kind": "scalar", "value": "1"},
                                                                    "a": {"kind": "array", "items": ["1", "9", "3"]}}},
            {"line": 3, "frame": "B", "stack": [{"id": "B"}, {"id": "A"}], "vars": {"i": {"kind": "scalar", "value": "1"}}},
            {"line": 4, "frame": "A", "stack": [{"id": "A"}], "vars": {"i": {"kind": "scalar", "value": "1"},
                                                                    "m": {"kind": "matrix", "rows": [["0", "1"]]}}},
            {"line": 5, "frame": "A", "stack": [{"id": "A"}], "vars": {"i": {"kind": "scalar", "value": "1"},
                                                                    "m": {"kind": "matrix", "rows": [["5", "1"]]}}},
        ]
        D.mark_changes(steps)
        self.assertNotIn("changed", steps[0]["vars"]["i"])
        self.assertTrue(steps[1]["vars"]["i"]["changed"])
        self.assertEqual(steps[1]["vars"]["a"]["changed_idx"], [1])
        self.assertTrue(steps[2]["vars"]["i"].get("new"))          # nowa ramka B
        self.assertNotIn("changed", steps[3]["vars"]["i"])          # powrót do A: i bez zmian
        self.assertEqual(steps[4]["vars"]["m"]["changed_cells"], [[0, 0]])

    def test_global_names(self):
        code = ("#include <bits/stdc++.h>\nusing namespace std;\nconst int N = 1e5 + 5, M = 10;\nint a[N], b, c = 5;\n"
                "vector<vector<int>> g(N);\nlong long dp[105][105];\nint f(int x);\nmap<int,int> cnt;\n"
                "int dx[] = {1, 0, -1, 0};\nint f(int x) { int loc = x; return loc; }\nint main() { int z = 1; }\n")
        self.assertEqual(D.global_names(code), ["N", "M", "a", "b", "c", "g", "dp", "cnt", "dx"])

    def test_decl_lines_and_watch(self):
        dl = D.decl_lines(CPP_REC)
        self.assertEqual(dl["fact"], {})
        self.assertEqual(dl["main"], {"r": 8})
        self.assertEqual(D.parse_watch("i, j ;a  bad-name"), ["i", "j", "a"])
        self.assertEqual(D.parse_watch(["x", "1y"]), ["x"])

    def test_capabilities_and_examples(self):
        c = D.capabilities()
        self.assertIn(c["cpp"], ("lldb", "gdb", None))
        self.assertTrue(c["python"])
        ex = D.handle_examples({})["examples"]
        self.assertGreaterEqual(len(ex), 5)
        self.assertTrue(all(e["code"].strip() for e in ex))

    def test_handle_trace_validation(self):
        from oiforge.server_api import ApiError
        with self.assertRaises(ApiError):
            D.handle_trace({"code": "  "})


if __name__ == "__main__":
    unittest.main()

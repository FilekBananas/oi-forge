"""Model spersonalizowanego autouzupełniania (oiforge/tools/complete.py) — na własnych, krótkich plikach."""
import tempfile
import unittest
from pathlib import Path

from oiforge.tools import complete

SRC_A = """#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
#define FOR(i, n) for (int i = 0; i < (n); i++)
int znajdz(int x) { return x; }
int main() {
    int n; cin >> n;
    vector<int> a(n);            // konstruktor, nie wywołanie funkcji „a”
    FOR(i, n) cin >> a[i];
    sort(a.begin(), a.end());
    cout << max(znajdz(1), znajdz(2)) << "\\n";
    if (n > 0) cout << znajdz(n);
    return 0;
}
"""
SRC_B = """#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
int main() { ll x; cin >> x; cout << max(x, 0LL) << " " << max(1, 2); sort(&x, &x + 1); sort(&x, &x + 1); }
"""


class CompleteModelTest(unittest.TestCase):
    def test_funcs_types(self):
        with tempfile.TemporaryDirectory() as d:
            pa, pb = Path(d, "a.cpp"), Path(d, "b.cpp")
            pa.write_text(SRC_A, encoding="utf-8")
            pb.write_text(SRC_B, encoding="utf-8")
            m = complete.build([pa, pb])
        funcs = dict(m["funcs"])
        types = dict(m["types"])
        self.assertIn("znajdz", funcs)          # własna funkcja wywoływana 3 razy
        self.assertIn("max", funcs)
        self.assertIn("sort", funcs)
        self.assertNotIn("a", funcs)            # vector<int> a(n) to deklaracja
        self.assertNotIn("if", funcs)
        self.assertNotIn("int", funcs)
        self.assertIn("ll", types)              # typedef w dwóch plikach
        self.assertIn("lines", m)
        self.assertEqual(m["version"], complete.MODEL_VERSION)


if __name__ == "__main__":
    unittest.main()

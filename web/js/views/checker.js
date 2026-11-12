// (B1) Sprawdzarka kodu — „wysyłam kod, a on go debuguje”: analiza statyczna (60+ reguł) + uruchomienie z sanitizerami,
// porównaniem wariantów kompilacji (-O2/-O0, zmienne = wzorzec/zero, debug STL, mały stos) i werdyktem zdrowia kodu.
import {
  h, icon, button, viewHeader, split, textarea, badge, callout, markdown, toast, clear, emptyState, progressBar,
  checkbox, select, modal, copyButton, debounce, kv, plural, fmtTime, fmtMem, verdictBadge, iconButton, more } from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const SEV = {
  error: { label: 'Błąd', many: 'Błędy', icon: 'circle-alert', tone: 'danger', mark: 'error' },
  warning: { label: 'Ostrzeżenie', many: 'Ostrzeżenia', icon: 'triangle-alert', tone: 'warn', mark: 'warn' },
  perf: { label: 'Wydajność', many: 'Wydajność', icon: 'zap', tone: 'info', mark: 'info' },
  style: { label: 'Styl', many: 'Styl', icon: 'paintbrush', tone: 'neutral', mark: 'hint' },
  info: { label: 'Uwaga', many: 'Uwagi', icon: 'info', tone: 'neutral', mark: 'hint' },
};
const SEV_ORDER = ['error', 'warning', 'perf', 'style', 'info'];
const SOURCES = {
  static: ['Analiza statyczna', 'scan-search'],
  sanitizer: ['Sanitizer', 'microscope'],
  differential: ['Porównanie wariantów', 'git-compare'],
  runtime: ['Uruchomienie', 'play'],
  compiler: ['Kompilator', 'cpu'],
};
const VARIANT_LABEL = {
  release: '-O2', sanitize: 'Sanitizery', o0: '-O0', init_pattern: 'Śmieci', init_zero: 'Zera', stl_debug: 'Debug STL',
  small_stack: 'Stos 8 MB', python: 'Python',
};

const EXAMPLES = [
  {
    title: 'Suma, iloczyn i wyjście poza vector', icon: 'bug',
    desc: 'Niezainicjalizowana suma, mnożenie intów do long long i odczyt v[n].',
    code: `#include <bits/stdc++.h>
using namespace std;

int main() {
    int n;
    cin >> n;
    vector<int> v(n);
    for (int i = 0; i < n; i++) cin >> v[i];
    int suma;
    for (int i = 0; i < n; i++) suma += v[i];
    long long iloczyn = v[0] * v[1];
    cout << suma << ' ' << iloczyn << ' ' << v[n] << '\\n';
}
`,
    inputs: [{ input: '3\n100000 100000 5\n' }],
  },
  {
    title: 'Komparator z <=', icon: 'list-checks',
    desc: 'sort z komparatorem, który łamie ścisły porządek — crash przy wielu równych elementach.',
    code: `#include <bits/stdc++.h>
using namespace std;

struct Zawodnik { int punkty, czas; };

bool cmp(const Zawodnik& a, const Zawodnik& b) {
    if (a.punkty != b.punkty) return a.punkty > b.punkty;
    return a.czas <= b.czas;
}

int main() {
    int n;
    cin >> n;
    vector<Zawodnik> z(n);
    for (auto& x : z) cin >> x.punkty >> x.czas;
    sort(z.begin(), z.end(), cmp);
    cout << z[0].punkty << ' ' << z[0].czas << '\\n';
}
`,
    inputs: [{ input: '40\n' + Array.from({ length: 40 }, () => '5 7').join('\n') + '\n' }],
  },
  {
    title: 'Pętla w dół na size_t', icon: 'timer',
    desc: 'Warunek i >= 0 dla typu bez znaku jest zawsze prawdziwy — nieskończona pętla.',
    code: `#include <bits/stdc++.h>
using namespace std;

int main() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int& x : a) cin >> x;
    for (size_t i = a.size() - 1; i >= 0; i--)
        cout << a[i] << ' ';
    cout << '\\n';
}
`,
    inputs: [{ input: '3\n1 2 3\n' }],
  },
  {
    title: 'Multitest bez czyszczenia', icon: 'refresh-cw',
    desc: 'Globalna lista sąsiedztwa nie jest czyszczona między testami.',
    code: `#include <bits/stdc++.h>
using namespace std;

vector<int> g[200005];
bool odw[200005];

int dfs(int v) {
    odw[v] = true;
    int ile = 1;
    for (int u : g[v]) if (!odw[u]) ile += dfs(u);
    return ile;
}

int main() {
    int t;
    cin >> t;
    while (t--) {
        int n, m;
        cin >> n >> m;
        for (int i = 0; i < m; i++) {
            int a, b;
            cin >> a >> b;
            g[a].push_back(b);
            g[b].push_back(a);
        }
        cout << dfs(1) << '\\n';
    }
}
`,
    inputs: [{ input: '2\n3 2\n1 2\n2 3\n2 1\n1 2\n', expected: '3\n2\n' }],
  },
  {
    title: 'Modulo i przepełnienie', icon: 'sigma',
    desc: 'Mnożenie modulo w int i ujemne modulo przy odejmowaniu.',
    code: `#include <bits/stdc++.h>
using namespace std;

const int MOD = 1e9 + 7;

int main() {
    int n;
    cin >> n;
    int wynik = 1;
    for (int i = 1; i <= n; i++) {
        int x;
        cin >> x;
        wynik = wynik * x % MOD;
    }
    int roznica = (wynik - 5) % MOD;
    cout << wynik << ' ' << roznica << '\\n';
}
`,
    inputs: [{ input: '3\n1000000 1000000 7\n' }],
  },
  {
    title: 'Brak return i dzielenie przez zero', icon: 'circle-alert',
    desc: 'Funkcja, która nie zawsze zwraca wartość, i średnia z pustego zbioru.',
    code: `#include <bits/stdc++.h>
using namespace std;

int znajdz(const vector<int>& v, int x) {
    for (int i = 0; i < (int)v.size(); i++)
        if (v[i] == x) return i;
}

int main() {
    int n, x;
    cin >> n >> x;
    vector<int> v(n);
    long long s = 0;
    for (int& e : v) { cin >> e; s += e; }
    cout << znajdz(v, x) << ' ' << s / n << '\\n';
}
`,
    inputs: [{ input: '3 9\n1 2 3\n' }, { input: '0 1\n' }],
  },
];

export default {
  id: 'checker',
  title: 'Sprawdzarka kodu',
  icon: 'bug',
  group: 'Kod',
  order: 10,
  layout: 'full',
  description: 'Wklej rozwiązanie — znajdę przepełnienia, wyjścia poza tablicę, niezainicjalizowane zmienne, złe komparatory, '
    + 'pułapki I/O i nieskończone pętle. Analiza statyczna + uruchomienie z sanitizerami i porównaniem wariantów kompilacji.',
  keywords: ['debug', 'błędy', 'sanitizer', 'sprawdź kod', 'ub', 'overflow', 'przepełnienie', 'segfault', 'lint', 'analiza'],

  render(root, ctx) {
    const self = this;
    let result = null;            // {mode, issues, stats|summary, ...}
    let filter = local.get('checker:filter', 'all');
    let srcFilter = 'all';
    let job = null;
    let stale = false;
    let inputs = local.get('checker:inputs', null) || [{ input: '', expected: '' }];
    const live = local.get('checker:live', true);

    // ------------------------------------------------------------------ lewa kolumna: kod + wejścia
    const edHost = h('div', { class: 'ck-editor' });
    const inputsHost = h('div', { class: 'ck-inputs' });
    const timeoutSel = select([{ value: '1', label: 'limit 1 s' }, { value: '2', label: 'limit 2 s' }, { value: '5', label: 'limit 5 s' },
      { value: '10', label: 'limit 10 s' }], { value: local.get('checker:timeout', '2'), size: 'sm', onChange: (v) => local.set('checker:timeout', v) });
    const liveBox = checkbox('Analizuj na bieżąco', { checked: live, onChange: (v) => { local.set('checker:live', v); if (v) liveAnalyze(); } });
    const staticBtn = button('Tylko analiza statyczna', { icon: 'scan-search', variant: 'ghost', onClick: () => runStatic(), title: 'Szybka analiza bez uruchamiania programu' });
    const dynBtn = button('Sprawdź (z sanitizerami)', { icon: 'microscope', variant: 'primary', onClick: () => runDynamic(),
      title: 'Analiza statyczna + kilka wariantów kompilacji (ASan/UBSan, kontrola STL) uruchomionych na wejściach testowych (Ctrl+Enter)' });
    const cancelBtn = button('Anuluj', { icon: 'circle-stop', variant: 'danger', onClick: () => job && job.cancel() });
    cancelBtn.hidden = true;

    const left = h('div', { class: 'ck-col' },
      edHost,
      h('div', { class: 'ck-inputs-box' },
        h('div', { class: 'row between ck-inputs-head' },
          h('div', { class: 'section-title', style: 'margin:0' }, 'Wejścia testowe'),
          h('div', { class: 'row gap-s' }, timeoutSel,
            button('Dodaj test', { icon: 'plus', size: 'sm', variant: 'ghost', onClick: () => { inputs.push({ input: '', expected: '' }); saveInputs(); renderInputs(true); } }))),
        inputsHost),
      h('div', { class: 'ck-actions' }, liveBox, h('div', { class: 'grow' }), staticBtn, dynBtn, cancelBtn));

    // ------------------------------------------------------------------ prawa kolumna: wyniki
    const progressHost = h('div', { class: 'ck-progress' });
    const resultsHost = h('div', { class: 'ck-results' });
    const right = h('div', { class: 'ck-col ck-right' }, progressHost, resultsHost);

    root.append(
      viewHeader(this,
        button('Przykłady z błędami', { icon: 'library', onClick: () => gallery() }),
        button('Reguły', { icon: 'list-checks', variant: 'ghost', onClick: () => showRules() })),
      h('div', { class: 'ck-body' }, split(left, right, { ratio: 0.5, min: 320, storageKey: 'checker' })));

    const ed = createEditor(edHost, {
      value: '', lang: 'cpp', height: '100%', storageKey: 'checker', toolbar: true, title: 'Twój kod',
      placeholder: 'Wklej tu rozwiązanie w C++ (albo Pythonie)…', onRun: () => runDynamic(),
      onChange: () => {
        if (result && !(liveBox.checked && result.mode === 'static')) { stale = true; markStale(); }
        if (liveBox.checked) liveAnalyze();
      },
      actions: [button('Wyczyść', { icon: 'trash-2', size: 'sm', variant: 'ghost', onClick: () => { ed.setValue(''); ed.clearMarks(); result = null; renderResults(); } })],
    });
    this._ed = ed;
    renderInputs();
    renderResults();
    if (ed.getValue().trim()) setTimeout(() => runStatic({ quiet: true }), 150);

    // ------------------------------------------------------------------ wejścia
    function saveInputs() { local.set('checker:inputs', inputs); }
    function renderInputs(focusLast = false) {
      clear(inputsHost);
      inputs.forEach((it, i) => {
        const ta = textarea({ value: it.input || '', rows: 3, placeholder: i === 0 ? 'Dane wejściowe (stdin), np. przykład z treści…' : 'Kolejny test…',
          onInput: debounce((v) => { it.input = v; saveInputs(); }, 250) });
        ta.addEventListener('input', () => { it.input = ta.value; });
        const exp = textarea({ value: it.expected || '', rows: 2, placeholder: 'Oczekiwane wyjście (opcjonalnie)…',
          onInput: debounce((v) => { it.expected = v; saveInputs(); }, 250) });
        exp.addEventListener('input', () => { it.expected = exp.value; });
        const expWrap = h('div', { class: 'ck-exp', hidden: !(it.expected || it.showExp) }, exp);
        const row = h('div', { class: 'ck-input' },
          h('div', { class: 'ck-input-head' },
            h('span', { class: 'ck-input-name' }, icon('file-input', 13), `Test ${i + 1}`),
            h('div', { class: 'grow' }),
            iconButton('target', { title: 'Oczekiwane wyjście', onClick: () => { it.showExp = !it.showExp; expWrap.hidden = !expWrap.hidden; if (!expWrap.hidden) exp.focus(); } }),
            inputs.length > 1 ? iconButton('x', { title: 'Usuń test', onClick: () => { inputs.splice(i, 1); saveInputs(); renderInputs(); } }) : null),
          ta, expWrap);
        inputsHost.append(row);
        if (focusLast && i === inputs.length - 1) setTimeout(() => ta.focus(), 30);
      });
    }

    // ------------------------------------------------------------------ analiza statyczna
    const liveAnalyze = debounce(() => {
      if (job || !ed.getValue().trim()) return;
      if (result && result.mode === 'dynamic') return;   // nie nadpisuj wyników uruchomienia
      runStatic({ quiet: true });
    }, 900);

    async function runStatic({ quiet = false } = {}) {
      const code = ed.getValue();
      if (!code.trim()) { if (!quiet) toast('Wklej kod do sprawdzenia.', 'info'); return; }
      try {
        const r = await api.post('/api/checker/analyze', { code }, { quiet });
        result = { mode: 'static', issues: r.issues || [], stats: r.stats || {}, note: r.note };
        stale = false;
        renderResults();
      } catch (e) { if (!quiet) console.error(e); }
    }

    // ------------------------------------------------------------------ analiza dynamiczna
    async function runDynamic() {
      if (job) return;
      const code = ed.getValue();
      if (!code.trim()) { toast('Wklej kod do sprawdzenia.', 'info'); return; }
      const payload = { code, timeout: parseFloat(timeoutSel.value) || 2,
        inputs: inputs.filter((x) => (x.input || '').length || (x.expected || '').trim() || inputs.length === 1)
          .map((x) => ({ input: x.input || '', expected: (x.expected || '').trim() ? x.expected : null })) };
      const pb = progressBar({ label: 'Przygotowuję…' });
      const live_ = h('div', { class: 'ck-live' });
      clear(progressHost);
      progressHost.append(h('div', { class: 'ck-progress-box' }, pb.el, live_));
      const compileChips = {};
      dynBtn.hidden = true; cancelBtn.hidden = false; staticBtn.disabled = true;
      job = api.job('/api/checker/run', payload, {
        onProgress: (p, msg) => pb.set(p, msg),
        onEvent: (ev) => {
          if (ev.type === 'stage' && ev.stage === 'compile') {
            for (const v of ev.variants || []) {
              const chip = h('span', { class: 'chip ck-vchip pending', title: v }, h('span', { class: 'spinner' }), VARIANT_LABEL[v] || v);
              compileChips[v] = chip; live_.append(chip);
            }
          } else if (ev.type === 'compile') {
            const chip = compileChips[ev.variant];
            if (chip) {
              chip.classList.remove('pending');
              chip.classList.add(ev.ok ? 'ok' : 'bad');
              chip.querySelector('.spinner')?.replaceWith(icon(ev.ok ? 'check' : 'x', 13));
              chip.title = `${ev.variant}: ${ev.ok ? 'skompilowano' : 'nie skompilował się'}${ev.cached ? ' (z pamięci podręcznej)' : ` w ${ev.time} s`}`;
            }
          } else if (ev.type === 'issue' && ev.issue && ev.issue.severity === 'error') {
            live_.append(h('div', { class: 'ck-live-issue' }, icon('circle-alert', 13), ev.issue.title, ev.issue.line ? h('span', { class: 'muted' }, ` · linia ${ev.issue.line}`) : null));
          }
        },
      });
      try {
        const r = await job.promise;
        result = { mode: 'dynamic', ...r };
        stale = false;
        renderResults();
      } catch (e) {
        if (e.cancelled) toast('Analizę anulowano.', 'info');
      } finally {
        job = null;
        clear(progressHost);
        dynBtn.hidden = false; cancelBtn.hidden = true; staticBtn.disabled = false;
      }
    }

    // ------------------------------------------------------------------ wyniki
    function markStale() {
      const el = resultsHost.querySelector('.ck-stale');
      if (el) el.hidden = !stale;
    }

    function visibleIssues() {
      let list = result?.issues || [];
      if (filter !== 'all') list = list.filter((i) => i.severity === filter);
      if (srcFilter === 'static') list = list.filter((i) => i.source === 'static');
      else if (srcFilter === 'dynamic') list = list.filter((i) => i.source !== 'static');
      return list;
    }

    function renderResults() {
      clear(resultsHost);
      if (!result) {
        ed.clearMarks();
        resultsHost.append(intro());
        return;
      }
      const issues = result.issues || [];
      ed.markLines(issues.filter((i) => i.line > 0 && i.severity !== 'info').map((i) => ({ line: i.line, type: SEV[i.severity]?.mark || 'info', message: i.title })));
      resultsHost.append(h('div', { class: 'ck-stale callout warn', hidden: !stale }, icon('triangle-alert', 15),
        h('span', null, 'Kod zmienił się od ostatniej analizy — wyniki mogą być nieaktualne.'),
        button('Odśwież', { size: 'sm', icon: 'refresh-cw', variant: 'ghost', onClick: () => (result.mode === 'dynamic' ? runDynamic() : runStatic()) })));
      resultsHost.append(summaryCard());
      if (result.note) resultsHost.append(callout(result.note, 'info'));
      for (const n of result.notes || []) resultsHost.append(callout(n, 'info'));
      if (result.mode === 'dynamic' && (result.inputs || []).length) resultsHost.append(inputsCard());
      resultsHost.append(filterBar());
      const list = visibleIssues();
      const listEl = h('div', { class: 'ck-list' });
      if (!issues.length) {
        listEl.append(emptyState({ icon: 'circle-check', title: 'Nie znaleziono problemów',
          text: result.mode === 'static' ? 'Analiza statyczna nic nie znalazła. Kliknij „Sprawdź (z sanitizerami)” (Ctrl+Enter), żeby uruchomić program na testach — najlepiej brzegowych: max n, zera, liczby ujemne.'
            : 'Na podanych testach program zachował się poprawnie we wszystkich wariantach kompilacji.' }));
      } else if (!list.length) {
        listEl.append(h('div', { class: 'muted small pad' }, 'Brak zgłoszeń w tym filtrze.'));
      } else {
        let groupSrc = null;
        for (const it of list) {
          const g = it.source === 'static' ? 'static' : 'dynamic';
          if (result.mode === 'dynamic' && g !== groupSrc && srcFilter === 'all') {
            // nagłówek grupy tylko gdy zmienia się rodzaj źródła w obrębie tej samej ważności
          }
          groupSrc = g;
          listEl.append(issueCard(it));
        }
      }
      resultsHost.append(listEl);
    }

    function summaryCard() {
      const issues = result.issues || [];
      const by = {};
      for (const i of issues) by[i.severity] = (by[i.severity] || 0) + 1;
      const hl = (result.mode === 'dynamic' ? result.summary?.health : result.stats?.health) || { score: 100, label: '—', tone: 'info' };
      const score = Math.max(0, Math.min(100, hl.score ?? 0));
      const ring = h('div', { class: `ck-ring tone-${hl.tone || 'info'}`, title: 'Ocena zdrowia kodu (0–100)' },
        hl.sure ? h('div', { class: 'ck-ring-in' }, h('b', null, '✗'), h('span', null, 'błąd')) : h('div', { class: 'ck-ring-in' }, h('b', null, String(score)), h('span', null, '/100')));
      ring.style.setProperty('--p', String(score));
      const st = result.mode === 'dynamic' ? result.static || {} : result.stats || {};
      const verdictText = result.mode === 'dynamic' ? result.summary?.verdict
        : (by.error ? 'Analiza statyczna widzi prawdopodobne błędy. Potwierdź je, uruchamiając program z sanitizerami na testach.'
          : by.warning ? 'Brak pewnych błędów, ale są ryzykowne miejsca warte sprawdzenia.'
            : 'Analiza statyczna nie widzi błędów. Kliknij „Sprawdź (z sanitizerami)”, żeby sprawdzić zachowanie na danych.');
      const chips = SEV_ORDER.filter((s) => by[s]).map((s) => h('button', {
        class: `chip ck-sevchip ${filter === s ? 'active' : ''}`, type: 'button',
        onclick: () => { filter = filter === s ? 'all' : s; local.set('checker:filter', filter); renderResults(); },
      }, icon(SEV[s].icon, 13), `${by[s]} ${by[s] === 1 ? SEV[s].label.toLowerCase() : SEV[s].many.toLowerCase()}`));
      const variants = result.mode === 'dynamic' ? Object.entries(result.variants || {}).map(([k, v]) =>
        h('span', { class: `chip ck-vchip ${v.ok ? 'ok' : 'bad'}`, title: v.info || k }, icon(v.ok ? 'check' : 'x', 12), VARIANT_LABEL[k] || k)) : [];
      const meta = [
        ['Linie', st.lines ?? '—'],
        st.functions !== undefined ? ['Funkcje / pętle', `${st.functions} / ${st.loops}`] : null,
        result.mode === 'dynamic' ? ['Testy', String(result.summary?.inputs ?? 0)] : null,
        ['Czas analizy', result.mode === 'dynamic' ? fmtTime(result.summary?.time) : `${st.time_ms ?? '—'} ms`],
        st.rules ? ['Reguły', String(st.rules)] : null,
      ];
      return h('div', { class: 'card ck-summary' }, h('div', { class: 'card-body' },
        h('div', { class: 'ck-sum-top' }, ring,
          h('div', { class: 'grow stack gap-s' },
            h('div', { class: 'row gap-s' }, h('span', { class: `ck-health tone-${hl.tone || 'info'}` }, hl.label || '—'),
              badge(result.mode === 'dynamic' ? 'statyczna + uruchomienia' : 'analiza statyczna', result.mode === 'dynamic' ? 'accent' : 'neutral',
                result.mode === 'dynamic' ? 'microscope' : 'scan-search')),
            h('div', { class: 'ck-verdict' }, verdictText),
            chips.length ? h('div', { class: 'row gap-s wrap' }, chips) : null,
            variants.length ? h('div', { class: 'row gap-s wrap ck-variants' }, h('span', { class: 'muted small' }, 'Warianty:'), variants) : null)),
        h('div', { class: 'ck-sum-meta' }, kv(meta.filter(Boolean)))));
    }

    function filterBar() {
      const seg = (label, val) => h('button', { type: 'button', class: srcFilter === val ? 'active' : '', onclick: () => { srcFilter = val; renderResults(); } }, label);
      const n = visibleIssues().length;
      return h('div', { class: 'row between ck-filterbar' },
        h('div', { class: 'section-title', style: 'margin:0' }, `Problemy (${n})`),
        result.mode === 'dynamic' ? h('div', { class: 'segmented' }, seg('Wszystkie', 'all'), seg('Z uruchomień', 'dynamic'), seg('Statyczne', 'static')) :
          (filter !== 'all' ? button('Pokaż wszystkie', { size: 'sm', variant: 'ghost', icon: 'filter', onClick: () => { filter = 'all'; local.set('checker:filter', filter); renderResults(); } }) : null));
    }

    function issueCard(it) {
      const sev = SEV[it.severity] || SEV.info;
      const [srcLabel, srcIcon] = SOURCES[it.source] || ['?', 'info'];
      const conf = Math.round((it.confidence ?? 0.7) * 100);
      const goto = () => { if (it.line) { ed.scrollToLine(it.line); ed.highlightLine(it.line, 'cm-line-current', { scroll: false }); } };
      const card = h('div', { class: `ck-issue sev-${it.severity}`, tabindex: 0 },
        h('div', { class: 'ck-issue-head' },
          h('span', { class: `ck-sev tone-${sev.tone}` }, icon(sev.icon, 17)),
          h('div', { class: 'grow' },
            h('div', { class: 'ck-issue-title' }, it.title),
            h('div', { class: 'ck-issue-meta' },
              badge(sev.label, sev.tone === 'neutral' ? 'neutral' : sev.tone),
              h('span', { class: 'ck-src', title: 'Źródło zgłoszenia' }, icon(srcIcon, 12), srcLabel),
              h('span', { class: 'ck-conf', title: 'Pewność, że to prawdziwy problem' }, h('span', { class: 'ck-conf-bar' }, h('i', { style: { width: conf + '%' } })), `${conf}%`),
              (it.confirmed_by || []).length ? h('span', { class: 'ck-confirmed', title: 'Potwierdzone przez inne źródło' }, icon('circle-check', 12), 'potwierdzone') : null,
              (it.inputs || []).length && result.mode === 'dynamic' ? h('span', { class: 'muted small' }, `test ${it.inputs.map((x) => x + 1).join(', ')}`) : null)),
          it.line ? button(`linia ${it.line}`, { size: 'sm', variant: 'ghost', icon: 'arrow-right', onClick: goto, title: 'Pokaż w edytorze' }) : null),
        it.code ? h('pre', { class: 'ck-code', onclick: goto }, it.code) : null,
        h('div', { class: 'ck-msg md' }, markdown(it.message || '')),
        it.fix ? h('div', { class: 'ck-fix' }, h('span', { class: 'ck-fix-icon' }, icon('lightbulb', 14)), h('div', { class: 'md grow' }, markdown(it.fix))) : null,
        (it.stack || []).length ? h('div', { class: 'ck-stack small muted' }, icon('layers', 12), ' Stos wywołań: ', it.stack.join(' ← ')) : null,
        h('div', { class: 'ck-issue-foot' },
          it.kb ? button('Baza wiedzy', { size: 'sm', variant: 'ghost', icon: 'book-open', onClick: () => ctx.navigate('docs', { id: it.kb }) })
            : (it.kb_query ? button('Zapytaj asystenta', { size: 'sm', variant: 'ghost', icon: 'message-square', onClick: () => ctx.navigate('assistant', { q: it.kb_query }) }) : null),
          it.evidence ? h('details', { class: 'ck-evidence' }, h('summary', null, icon('terminal', 12), ' Surowy raport'), h('pre', null, it.evidence)) : null,
          h('div', { class: 'grow' }),
          h('span', { class: 'ck-rule mono', title: 'Identyfikator reguły' }, it.rule)));
      card.addEventListener('dblclick', goto);
      card.addEventListener('keydown', (e) => { if (e.key === 'Enter') goto(); });
      return card;
    }

    function inputsCard() {
      const rows = result.inputs || [];
      const variantNames = [...new Set(rows.flatMap((r) => Object.keys(r.variants || {})))];
      const tbl = h('table', { class: 'tbl compact ck-runs' },
        h('thead', null, h('tr', null, h('th', null, 'Test'), variantNames.map((v) => h('th', { title: result.variants?.[v]?.info || v }, VARIANT_LABEL[v] || v)), h('th', null, 'Zgodność'), h('th', null, ''))),
        h('tbody', null, rows.map((r) => h('tr', null,
          h('td', null, h('b', null, r.name || `Test ${r.index + 1}`), r.expected ? (r.expected_ok ? badge('wynik OK', 'success') : badge('inny wynik', 'warn')) : null),
          variantNames.map((v) => {
            const x = r.variants?.[v];
            if (!x) return h('td', { class: 'muted' }, '—');
            return h('td', { title: `${fmtTime(x.time)}${x.mem_kb ? ' · ' + fmtMem(x.mem_kb) : ''}${x.signal ? ' · ' + x.signal : ''}` }, verdictBadge(x.verdict));
          }),
          h('td', null, r.agree ? h('span', { class: 'tone-success' }, icon('check', 14), ' te same wyniki') : h('span', { class: 'tone-danger' }, icon('git-compare', 14), ' różne!')),
          h('td', null, iconButton('eye', { title: 'Szczegóły uruchomień', onClick: () => runDetails(r) }))))));
      return h('div', { class: 'card ck-runs-card' }, h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('play', 17)),
        h('div', { class: 'grow' }, h('div', { class: 'card-title' }, 'Uruchomienia'), h('div', { class: 'card-sub' }, 'Ten sam program w różnych wariantach kompilacji — różne wyniki oznaczają niezdefiniowane zachowanie.'))),
      h('div', { class: 'card-body flush' }, h('div', { class: 'table-wrap' }, tbl)));
    }

    function runDetails(r) {
      const body = h('div', { class: 'stack' },
        h('div', null, h('div', { class: 'out-label' }, 'Wejście'), h('pre', { class: 'out' }, r.input_preview || '(puste)')),
        Object.entries(r.variants || {}).map(([k, x]) => h('div', { class: 'ck-run-variant' },
          h('div', { class: 'row gap-s' }, verdictBadge(x.verdict), h('b', null, VARIANT_LABEL[k] || k),
            h('span', { class: 'muted small' }, result.variants?.[k]?.info || ''), h('div', { class: 'grow' }),
            h('span', { class: 'muted small' }, `${fmtTime(x.time)}${x.mem_kb ? ' · ' + fmtMem(x.mem_kb) : ''}${x.signal ? ' · ' + x.signal : ''}`)),
          h('pre', { class: 'out' }, x.stdout || h('span', { class: 'faint' }, '(puste wyjście)')),
          x.stderr && x.stderr.trim() ? h('details', null, h('summary', { class: 'muted small' }, 'stderr'), h('pre', { class: 'out err' }, x.stderr)) : null)));
      modal({ title: `${r.name || 'Test'} — szczegóły uruchomień`, icon: 'play', body, width: 860 });
    }

    function intro() {
      const tricks = [
        ['scan-search', 'Analiza statyczna', '60+ reguł: przepełnienia int, 1 << 40, accumulate z 0, = w warunku, komparator z <=, niezainicjalizowane zmienne, wyjście poza tablicę w pętli, VLA, endl w pętli, scanf("%d") dla long long…'],
        ['microscope', 'Sanitizery', 'AddressSanitizer + UBSan + kontrola zakresów STL: dokładna linia, indeks i rozmiar przy wyjściu poza vector.'],
        ['git-compare', '-O2 kontra -O0', 'Różne wyniki zależnie od optymalizacji = prawie na pewno niezdefiniowane zachowanie.'],
        ['shuffle', 'Śmieci kontra zera', 'Zmienne lokalne startujące od wzorca 0xAA… i od zera — różne wyniki zdradzają niezainicjalizowaną zmienną.'],
        ['timer', 'TLE i pętle', 'Odróżnia „za wolno” od nieskończonej pętli, wykrywa za głęboką rekurencję (stos 8 MB).'],
        ['languages', 'Ostrzeżenia kompilatora', '-Wall -Wextra -Wshadow -Wconversion… przetłumaczone na polski.'],
      ];
      return h('div', { class: 'ck-intro' },
        emptyState({ icon: 'bug', title: 'Wklej kod — błędy pokażą się tutaj', text: 'Ctrl+Enter = pełne sprawdzenie: analiza statyczna + uruchomienie z sanitizerami na testach.',
          action: button('Wczytaj przykład z błędami', { icon: 'library', onClick: () => gallery() }) }),
        // opisy sztuczek schowane — kto chce, rozwinie
        more('Co dokładnie sprawdzam?', [h('div', { class: 'ck-tricks' }, tricks.map(([ic, t, d]) => h('div', { class: 'ck-trick' }, h('div', { class: 'ck-trick-icon' }, icon(ic, 16)),
          h('div', null, h('div', { class: 'ck-trick-title' }, t), h('div', { class: 'muted small' }, d)))))], { key: 'checker-tricks' }));
    }

    // ------------------------------------------------------------------ przykłady i reguły
    function gallery() {
      const m = modal({
        title: 'Przykłady programów z błędami', icon: 'library', width: 900,
        body: h('div', { class: 'ck-gallery' }, EXAMPLES.map((ex) => h('button', {
          class: 'ck-ex', type: 'button',
          onclick: () => { load(ex); m.close(); },
        }, h('div', { class: 'row gap-s' }, icon(ex.icon, 16), h('b', null, ex.title)), h('div', { class: 'muted small' }, ex.desc),
        h('pre', { class: 'ck-ex-code' }, ex.code.split('\n').slice(3, 11).join('\n'))))),
      });
    }

    function load(ex) {
      ed.setValue(ex.code);
      inputs = (ex.inputs || [{ input: '' }]).map((x) => ({ input: x.input || '', expected: x.expected || '' }));
      saveInputs();
      renderInputs();
      result = null;
      runStatic({ quiet: true });
    }

    async function showRules() {
      let r;
      try { r = await api.get('/api/checker/rules'); } catch { return; }
      const byCat = {};
      for (const rule of r.rules || []) (byCat[rule.category_name] ||= []).push(rule);
      modal({
        title: `Reguły analizy statycznej (${r.count})`, icon: 'list-checks', width: 860,
        body: h('div', { class: 'ck-rules' }, Object.entries(byCat).map(([cat, list]) => h('div', { class: 'stack gap-s' },
          h('div', { class: 'section-title' }, cat),
          list.map((x) => h('div', { class: 'ck-rule-row' }, badge(SEV[x.severity]?.label || x.severity, SEV[x.severity]?.tone === 'neutral' ? 'neutral' : SEV[x.severity]?.tone),
            h('div', { class: 'grow' }, h('div', null, h('b', null, x.title)), h('div', { class: 'muted small' }, x.description)),
            h('span', { class: 'mono small faint' }, x.id)))))),
      });
    }

    self._load = (params) => {
      if (params.code !== undefined) {
        ed.setValue(String(params.code));
        result = null;
        stale = false;
        if (Array.isArray(params.inputs) && params.inputs.length) {
          inputs = params.inputs.map((x) => (typeof x === 'string' ? { input: x, expected: '' } : { input: x.input || '', expected: x.expected || x.output || '' }));
          saveInputs();
          renderInputs();
        } else if (typeof params.input === 'string') {
          inputs = [{ input: params.input, expected: '' }];
          saveInputs();
          renderInputs();
        }
        renderResults();
        if (params.run !== false) runDynamic(); else runStatic({ quiet: true });
      }
    };
  },

  onShow(params) {
    if (params && this._load) this._load(params);
  },
};

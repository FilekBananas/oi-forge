// Ustawienia aplikacji + informacje o systemie (kompilatory, debugger, moduły).
import { h, icon, button, viewHeader, card, tabs, field, input, select, segmented, checkbox, badge, kv, clear, toast, table, callout, codeBlock } from '../ui.js';
import { api, settings, updateSettings } from '../api.js';

const ACCENTS = [['violet', '#8b5cf6'], ['blue', '#3b82f6'], ['emerald', '#10b981'], ['amber', '#f59e0b'], ['rose', '#f43f5e'], ['cyan', '#06b6d4']];

export default {
  id: 'settings', title: 'Ustawienia', icon: 'settings', group: 'Narzędzia', order: 99,
  description: 'Wygląd, edytor, limity uruchamiania, SIO2 i informacje o systemie.',
  keywords: ['ustawienia', 'settings', 'motyw', 'kompilator', 'system'],
  async render(root) {
    const S = await settings(true);
    const save = async (patch, msg = 'Zapisano') => { await updateSettings(patch); toast(msg, 'success', { timeout: 1200 }); };
    const t = tabs([
      { id: 'look', label: 'Wygląd', icon: 'paintbrush', render: () => look(S, save) },
      { id: 'editor', label: 'Edytor', icon: 'file-code', render: () => editor(S, save) },
      { id: 'run', label: 'Uruchamianie', icon: 'play', render: () => run(S, save) },
      { id: 'account', label: 'Profil i SIO2', icon: 'graduation-cap', render: () => account(S, save) },
      { id: 'system', label: 'System', icon: 'cpu', render: () => system() },
    ]);
    root.append(viewHeader(this), t.el);
    this._tabs = t;
  },
  onShow(params) { if (params.tab && this._tabs) this._tabs.select(params.tab); },
};

function look(S, save) {
  const sw = h('div', { class: 'row gap-s' }, ACCENTS.map(([name, color]) => {
    const b = h('button', { title: name, style: `width:30px;height:30px;border-radius:50%;border:2px solid ${S.accent === name ? 'var(--text)' : 'transparent'};background:${color};cursor:pointer;box-shadow:0 0 0 3px var(--panel)`,
      onclick: async () => { S.accent = name; await save({ accent: name }); sw.querySelectorAll('button').forEach((x) => { x.style.borderColor = x.title === name ? 'var(--text)' : 'transparent'; }); } });
    return b;
  }));
  return h('div', { class: 'grid-2' },
    card({ title: 'Motyw', icon: 'sun', body: h('div', { class: 'stack' },
      field('Tryb', segmented([{ value: 'dark', label: 'Ciemny', icon: 'moon' }, { value: 'light', label: 'Jasny', icon: 'sun' }, { value: 'system', label: 'Systemowy', icon: 'cpu' }],
        { value: S.theme, onChange: (v) => save({ theme: v }) })),
      field('Kolor akcentu', sw),
      field('Gęstość', segmented([{ value: 'comfortable', label: 'Wygodna' }, { value: 'compact', label: 'Kompaktowa' }], { value: S.density, onChange: (v) => save({ density: v }) }))) }),
    card({ title: 'Podgląd', icon: 'eye', body: h('div', { class: 'stack' },
      h('div', { class: 'row' }, button('Główny', { variant: 'primary', icon: 'play' }), button('Zwykły'), button('Duch', { variant: 'ghost' }), badge('OK', 'success'), badge('TLE', 'warn'), badge('WA', 'danger')),
      codeBlock('for (int i = 0; i < n; i++) {\n    dp[i] = max(dp[i - 1], a[i]); // komentarz\n}', 'cpp')) }));
}

function editor(S, save) {
  const e = S.editor || {};
  return card({ title: 'Edytor kodu', icon: 'file-code', body: h('div', { class: 'stack', style: 'max-width:560px' },
    field('Rozmiar czcionki', select([12, 13, 14, 15, 16, 18].map((n) => ({ value: n, label: `${n} px` })), { value: e.font_size || 14, onChange: (v) => save({ editor: { font_size: +v } }) })),
    checkbox('Zawijanie długich linii', { checked: !!e.line_wrap, onChange: (v) => save({ editor: { line_wrap: v } }) }),
    checkbox('Snippety pod klawiszem Tab', { checked: e.snippets !== false, onChange: (v) => save({ editor: { snippets: v } }) }),
    callout('Skróty: `Ctrl/⌘+Enter` uruchom · `Ctrl/⌘+Shift+F` formatuj · `Ctrl/⌘+/` komentarz · `Ctrl+Spacja` podpowiedzi · `Ctrl/⌘+F` szukaj · `Tab` rozwiń snippet / następne pole.', 'info')) });
}

function run(S, save) {
  const tl = input({ value: S.time_limit, type: 'number', attrs: { step: '0.5', min: '0.1', max: '60' }, onChange: (v) => save({ time_limit: +v || 2 }) });
  const ml = input({ value: S.memory_limit, type: 'number', attrs: { step: '32', min: '16' }, onChange: (v) => save({ memory_limit: +v || 256 }) });
  const tests = input({ value: (S.tester && S.tester.tests) || 1000, type: 'number', onChange: (v) => save({ tester: { tests: +v || 1000 } }) });
  const cards = input({ value: (S.flashcards && S.flashcards.new_per_day) || 15, type: 'number', attrs: { min: '0', max: '200' }, onChange: (v) => save({ flashcards: { new_per_day: Math.max(0, +v || 0) } }) });
  return h('div', { class: 'grid-2' },
    card({ title: 'Limity domyślne', icon: 'timer', body: h('div', { class: 'stack' },
      field('Limit czasu (s)', tl, 'Używany przy uruchamianiu programów i w testerce.'),
      field('Limit pamięci (MB)', ml, 'Przekroczenie oznaczane jako MLE (pomiar maksymalnej pamięci procesu).'),
      field('Testów na uruchomienie generatora', tests),
      field('Nowych fiszek dziennie', cards, 'Ile nowych kart wprowadzać każdego dnia w Fiszkach (powtórki są niezależne).')) }),
    card({ title: 'Kompilacja', icon: 'cpu', body: h('div', { class: 'stack' },
      field('Standard C++', select(['c++17', 'c++20', 'c++23'], { value: S.cpp_std, onChange: (v) => save({ cpp_std: v }) })),
      callout('Tryb **release**: `g++ -O2` (jak na OI). Tryb **sanitize**: AddressSanitizer + UBSan (wykrywa wyjście poza tablicę i UB). Tryb **debug**: `-g -O0` dla debuggera. Nagłówek `bits/stdc++.h` jest prekompilowany, więc kompilacja trwa ~1 s.', 'info')) }));
}

function account(S, save) {
  const name = input({ value: (S.user && S.user.name) || '', placeholder: 'Twoje imię', onChange: (v) => save({ user: { name: v.trim() } }) });
  const base = input({ value: (S.sio2 && S.sio2.base_url) || '', placeholder: 'puste = Szkopuł albo serwer, z którego masz już programy', mono: true, onChange: (v) => save({ sio2: { base_url: v.trim() } }) });
  return h('div', { class: 'grid-2' },
    card({ title: 'Profil', icon: 'graduation-cap', body: field('Imię (do powitania)', name) }),
    card({ title: 'SIO2', icon: 'download', body: h('div', { class: 'stack' }, field('Adres strony SIO2 / Szkopułu', base, 'Np. https://szkopul.edu.pl albo adres serwera SIO2 Twojej szkoły.'),
      callout('Import Twoich rozwiązań (Szkopuł, dowolna strona SIO2 albo folder) uruchomisz w **Moje programy → Synchronizacja i import**. Hasło nie jest nigdzie zapisywane.', 'info')) }));
}

function system() {
  const box = h('div', { class: 'stack' }, h('div', { class: 'skeleton', style: 'height:200px' }));
  (async () => {
    const [{ capabilities: c }, hl] = await Promise.all([api.get('/api/capabilities'), api.get('/api/health')]);
    clear(box);
    const ok = (v) => (v ? badge('dostępne', 'success', 'check') : badge('brak', 'danger', 'x'));
    box.append(h('div', { class: 'grid-2' },
      card({ title: 'Narzędzia', icon: 'cpu', body: kv([
        ['Platforma', c.platform], ['Python', c.python],
        ['GCC', c.gcc ? h('span', null, ok(true), ' ', h('span', { class: 'mono small' }, c.gcc_version)) : ok(false)],
        ['Clang', c.clang ? h('span', null, ok(true), ' ', h('span', { class: 'mono small' }, c.clang_version)) : ok(false)],
        ['Sanitizery', ok(c.sanitizers)], ['Debugger', c.debugger ? badge(c.debugger, 'success', 'check') : ok(false)],
        ['Okno programu', c.browser ? badge('Chrome/Edge', 'success') : badge('przeglądarka domyślna', 'warn')],
      ]) }),
      card({ title: 'Program', icon: 'activity', body: kv([
        ['Wersja', hl.version], ['Moduły', String(hl.tools.length)], ['Trasy API', String(hl.routes)], ['Artykuły bazy wiedzy', String(hl.kb_articles)],
        ['Moduły z błędami', Object.keys(hl.tool_errors).length ? badge(String(Object.keys(hl.tool_errors).length), 'danger') : badge('0', 'success')],
      ]), actions: [button('Wyczyść cache', { size: 'sm', icon: 'trash-2', title: 'Skompilowane programy i prekompilowane nagłówki — odtworzą się same', onClick: async () => { const r = await api.post('/api/cache/clear', { what: 'all' }); toast(`Usunięto ${r.removed} elementów cache`, 'success'); } })] })));
    try {
      const u = (await api.get('/api/cache/usage', null, { quiet: true })).usage;
      box.append(card({ title: 'Pamięć podręczna (cache)', icon: 'database', body: kv([
        ['Katalog', h('span', { class: 'mono small' }, u.dir)],
        ['Skompilowane programy', `${u.bin_mb} MB (limit ${u.limit_mb} MB — najstarsze usuwane automatycznie)`],
        ['Prekompilowane nagłówki', `${u.pch_mb} MB (przyspieszają kompilację ~6×)`],
      ]) }));
    } catch { /* starsza wersja serwera */ }
    if (!c.cpp) box.append(callout('**Brak kompilatora C++.** Zainstaluj: macOS — `xcode-select --install` lub `brew install gcc`; Linux — `sudo apt install g++`; Windows — [MSYS2](https://www.msys2.org) (`pacman -S mingw-w64-ucrt-x86_64-gcc`) lub WinLibs, i dodaj `bin` do PATH.', 'danger'));
    if (Object.keys(hl.tool_errors).length) box.append(card({ title: 'Błędy modułów', icon: 'triangle-alert', body: table([{ key: 'm', label: 'Moduł' }, { key: 'e', label: 'Błąd' }], Object.entries(hl.tool_errors).map(([m, e]) => ({ m, e }))) }));
    box.append(card({ title: 'Moduły', icon: 'blocks', body: h('div', { class: 'row gap-s' }, hl.tools.map((m) => badge(m, 'accent'))) }));
  })().catch((e) => { clear(box); box.append(callout('Nie udało się pobrać informacji: ' + e.message, 'danger')); });
  return box;
}

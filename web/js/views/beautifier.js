// (j)+(O) Upiększacz kodu i formatter wg stylu użytkownika — bez clang-format, bez AI (silnik tokenowy w Pythonie).
import {
  h, icon, button, iconButton, viewHeader, split, tabs, field, input, select, checkbox, badge, toast, modal, table,
  callout, emptyState, debounce, clear, copyButton, plural, spinner,
} from '../ui.js';
import { api, local } from '../api.js';
import { createEditor } from '../editor.js';

const SAMPLE = `#include<vector>
#include <iostream>
#include<algorithm>
#include <vector>
using namespace std;
int suma(vector<int>&v){int s=0;for(int i=0;i<v.size();i++) s+=v[i];return s;}
int main()
{
    int n;cin>>n;
    vector<int> a(n);
    for(auto&x:a)cin>>x;
    //cout << "debug: " << n << endl;
    sort(a.begin(),a.end(),[](int x,int y){return x>y;});
    if(n>0) cout<<a[0]<<endl;
    else cout<<-1<<endl;   // brak elementów
    cout<<suma(a)<<endl;
}
`;

const GROUP_ICONS = { 'Wcięcia': 'align-left', 'Klamry': 'braces', 'Spacje': 'code-xml', 'Linie': 'list-tree' };

export default {
  id: 'beautifier',
  title: 'Upiększacz kodu',
  icon: 'wand-sparkles',
  group: 'Kod',
  order: 8,
  layout: 'full',
  description: 'Formatowanie jednym kliknięciem wg Twojego stylu (albo Google, LLVM, Allman, K&R, Kompaktowy CP) + porządki: #include, endl, martwy kod, klamry.',
  keywords: ['formatowanie', 'format', 'clang-format', 'styl', 'wcięcia', 'beautify', 'upiększ', 'include', 'endl', 'klamry', 'prettier'],

  render(root, ctx) {
    const self = this;
    const st = {
      presets: [], schema: [], transforms: [], active: 'moja', learned: null,
      preset: 'moja', options: {}, dirty: false,
      tr: local.get('beautifier:tr', null),
      result: null, seq: 0, onlyChanges: local.get('beautifier:onlyChanges', true),
    };
    this._st = st;

    // ------------------------------------------------------------------ szkielet
    const presetRow = h('div', { class: 'bf-presets' });
    const presetNote = h('div', { class: 'small muted bf-preset-note' });
    const groupsHost = h('div', { class: 'stack gap-s' });
    const trHost = h('div', { class: 'stack gap-s' });
    const learnedInfo = h('div', { class: 'small muted' });
    const status = h('div', { class: 'bf-status small muted' });
    const changesHost = h('div', { class: 'bf-changes row gap-s' });
    const diffHost = h('div', { class: 'bf-diff' });
    const warnHost = h('div');

    const styleTab = h('div', { class: 'stack gap-s' },
      h('div', { class: 'section-title' }, 'Preset'), presetRow, presetNote,
      groupsHost,
      h('div', { class: 'bf-actions stack gap-s' },
        button('Zapisz jako mój styl', { icon: 'save', variant: 'primary', onClick: saveMine, title: 'Ten styl będzie używany przez Ctrl+Shift+F we wszystkich edytorach' }),
        button('Naucz się mojego stylu z moich programów', { icon: 'brain-circuit', onClick: learn,
          title: 'Analizuje Twoje rozwiązania z „Moje programy” (wcięcia, klamry, spacje…) i proponuje styl „Moja”' }),
        learnedInfo));
    const trTab = h('div', { class: 'stack gap-s' },
      h('div', { class: 'small muted' }, 'Każde przekształcenie można włączyć osobno — wynik i różnice widzisz od razu po prawej.'),
      trHost);
    const sideTabs = tabs([
      { id: 'style', label: 'Styl', icon: 'paintbrush', render: () => styleTab },
      { id: 'tr', label: 'Upiększacz', icon: 'sparkles', render: () => trTab },
    ], { active: local.get('beautifier:side', 'style'), onChange: (id) => local.set('beautifier:side', id) });
    const aside = h('aside', { class: 'bf-aside' }, sideTabs.el);

    const inHost = h('div', { class: 'bf-pane' });
    const outHost = h('div', { class: 'bf-pane' });
    const diffWrap = h('div', { class: 'bf-pane bf-diff-pane' },
      h('div', { class: 'row between bf-diff-bar' },
        changesHost,
        checkbox('Tylko zmienione fragmenty', { checked: st.onlyChanges, onChange: (v) => { st.onlyChanges = v; local.set('beautifier:onlyChanges', v); renderDiff(); } })),
      warnHost, diffHost);
    const outTabs = tabs([
      { id: 'result', label: 'Wynik', icon: 'eye', render: () => outHost },
      { id: 'diff', label: 'Różnice', icon: 'git-compare', render: () => diffWrap },
    ], { active: 'result', onChange: (id) => { if (id === 'result') outEd?.refresh(); } });
    outTabs.el.classList.add('bf-out-tabs');

    const applyBtn = button('Zastosuj w edytorze', { icon: 'check', variant: 'primary', size: 'sm', onClick: applyResult,
      title: 'Wstaw wynik do edytora po lewej' });
    const backBtn = button('Wróć z wynikiem', { icon: 'arrow-right', size: 'sm', onClick: () => sendBack(),
      title: 'Przekaż upiększony kod z powrotem do narzędzia, z którego przyszedł' });
    backBtn.hidden = true;
    this._backBtn = backBtn;
    const rightCol = h('div', { class: 'bf-col' },
      h('div', { class: 'row between bf-col-head' }, h('div', { class: 'row gap-s' }, status),
        h('div', { class: 'row gap-s' }, copyButton(() => st.result?.code ?? '', { label: 'Kopiuj' }), applyBtn, backBtn)),
      outTabs.el);
    const leftCol = h('div', { class: 'bf-col' }, inHost);

    root.append(
      viewHeader(this,
        button('Przykład', { icon: 'file-code', variant: 'ghost', onClick: () => { inEd.setValue(SAMPLE); } }),
        button('Formatuj wg mojego stylu', { icon: 'align-left', onClick: quickFormatMine, title: 'To samo co Ctrl+Shift+F w każdym edytorze' })),
      h('div', { class: 'bf-body' }, aside, split(leftCol, rightCol, { ratio: 0.5, min: 300, storageKey: 'beautifier' })));

    const inEd = createEditor(inHost, {
      value: SAMPLE, lang: 'cpp', height: '100%', storageKey: 'beautifier-in', toolbar: true, title: 'Twój kod',
      placeholder: 'Wklej kod C++ do upiększenia…', onChange: () => schedule(),
      onRun: () => schedule.flush && schedule.flush(),
    });
    const outEd = createEditor(outHost, { value: '', lang: 'cpp', height: '100%', readOnly: true, toolbar: true, title: 'Po upiększeniu' });
    this._inEd = inEd;

    // ------------------------------------------------------------------ dane
    const schedule = debounce(() => refresh(), 350);
    this._schedule = schedule;
    init();

    async function init() {
      status.replaceChildren(spinner(14), h('span', null, 'Wczytuję style…'));
      try {
        const [p, t] = await Promise.all([api.get('/api/format/presets'), api.get('/api/beautify/options')]);
        st.presets = p.presets; st.schema = p.schema; st.active = p.active; st.learned = p.learned;
        st.transforms = t.transforms;
        const saved = local.get('beautifier:style', null);
        if (saved && saved.options && saved.preset) { st.preset = saved.preset; st.options = { ...p.current, ...saved.options }; st.dirty = !!saved.dirty; }
        else { st.preset = p.active; st.options = { ...p.current }; }
        if (!st.tr) st.tr = Object.fromEntries(st.transforms.map((x) => [x.id, x.default]));
        renderPresets(); renderGroups(); renderTransforms(); renderLearned();
        refresh();
      } catch (e) {
        status.replaceChildren(icon('circle-alert', 14), h('span', null, 'Nie udało się wczytać stylów: ' + e.message));
      }
    }

    function persistStyle() { local.set('beautifier:style', { preset: st.preset, options: st.options, dirty: st.dirty }); }

    function renderPresets() {
      clear(presetRow);
      for (const p of st.presets) {
        const on = p.id === st.preset;
        presetRow.append(h('button', {
          class: ['chip', on ? 'active' : ''].join(' '), type: 'button', title: p.description,
          onclick: () => choosePreset(p.id),
        }, p.id === 'moja' ? icon('star', 13) : null, p.label, on && st.dirty ? h('span', { class: 'bf-dot', title: 'zmieniony' }) : null,
        p.id === st.active ? h('span', { class: 'bf-active', title: 'Aktywny styl (Ctrl+Shift+F)' }, icon('check', 11)) : null));
      }
      const cur = st.presets.find((p) => p.id === st.preset);
      presetNote.replaceChildren(cur ? cur.description : '', st.dirty ? h('span', { class: 'tone-warn' }, ' · zmieniony ręcznie') : '');
    }

    function choosePreset(id) {
      const p = st.presets.find((x) => x.id === id);
      if (!p) return;
      st.preset = id; st.options = { ...p.options }; st.dirty = false;
      persistStyle(); renderPresets(); renderGroups(); schedule();
    }

    function renderGroups() {
      clear(groupsHost);
      const groups = [];
      for (const s of st.schema) {
        let g = groups.find((x) => x.name === s.group);
        if (!g) { g = { name: s.group, items: [] }; groups.push(g); }
        g.items.push(s);
      }
      for (const g of groups) {
        const body = h('div', { class: 'bf-opts' });
        for (const s of g.items) body.append(control(s));
        const det = h('details', { class: 'bf-group', open: local.get('beautifier:g:' + g.name, g.name !== 'Linie') },
          h('summary', null, icon(GROUP_ICONS[g.name] || 'settings', 14), h('span', null, g.name)), body);
        det.addEventListener('toggle', () => local.set('beautifier:g:' + g.name, det.open));
        groupsHost.append(det);
      }
    }

    function setOpt(key, value) {
      st.options = { ...st.options, [key]: value };
      st.dirty = true;
      persistStyle(); renderPresets(); schedule();
    }

    function label(s) {
      // `kod` w etykietach schematu → <code>
      const parts = String(s.label).split('`');
      return h('span', { class: 'bf-label' }, parts.map((t, i) => (i % 2 ? h('code', null, t) : t)));
    }

    function control(s) {
      const v = st.options[s.key];
      if (s.type === 'bool') {
        const cb = checkbox('', { checked: !!v, onChange: (val) => setOpt(s.key, val) });
        cb.append(label(s));
        cb.classList.add('bf-check');
        return cb;
      }
      if (s.type === 'int') {
        const inp = input({ value: String(v ?? ''), type: 'number', size: 'sm', attrs: { min: s.min, max: s.max, step: 1 },
          onInput: (val) => { const n = parseInt(val, 10); if (!Number.isNaN(n)) setOpt(s.key, Math.max(s.min, Math.min(s.max, n))); } });
        inp.classList.add('bf-num');
        return h('label', { class: 'bf-row' }, label(s), inp);
      }
      const sel = select(s.choices.map(([value, lab]) => ({ value, label: lab })), { value: v, size: 'sm', onChange: (val) => setOpt(s.key, val) });
      return h('div', { class: 'bf-row bf-row-choice' }, label(s), sel);
    }

    function renderTransforms() {
      clear(trHost);
      for (const t of st.transforms) {
        if (t.type === 'choice') {
          const sel = select(t.choices.map(([value, lab]) => ({ value, label: lab })), { value: st.tr[t.id] ?? t.default, size: 'sm',
            onChange: (val) => { st.tr[t.id] = val; local.set('beautifier:tr', st.tr); schedule(); } });
          trHost.append(h('div', { class: 'bf-tr' }, h('div', { class: 'row between nowrap' }, h('b', null, t.label), sel), h('div', { class: 'small muted' }, t.description)));
          continue;
        }
        const cb = checkbox(t.label, { checked: !!st.tr[t.id], onChange: (val) => { st.tr[t.id] = val; local.set('beautifier:tr', st.tr); schedule(); } });
        trHost.append(h('div', { class: 'bf-tr' }, cb, h('div', { class: 'small muted' }, t.description)));
      }
      trHost.append(h('div', { class: 'row gap-s mt-s' },
        button('Domyślne', { icon: 'rotate-ccw', size: 'sm', variant: 'ghost', onClick: () => { st.tr = Object.fromEntries(st.transforms.map((x) => [x.id, x.default])); local.set('beautifier:tr', st.tr); renderTransforms(); schedule(); } }),
        button('Tylko formatowanie', { icon: 'align-left', size: 'sm', variant: 'ghost', onClick: () => { st.tr = Object.fromEntries(st.transforms.map((x) => [x.id, x.id === 'format' ? true : (x.type === 'choice' ? 'off' : false)])); local.set('beautifier:tr', st.tr); renderTransforms(); schedule(); } })));
    }

    function renderLearned() {
      const l = st.learned;
      learnedInfo.replaceChildren(l && l.at
        ? h('span', null, icon('circle-check', 13), ` Styl „Moja” wyuczony ${l.at} z ${l.programs} ${plural(l.programs, 'programu', 'programów', 'programów')}.`)
        : 'Styl „Moja” nie był jeszcze uczony na Twoich programach.');
    }

    // ------------------------------------------------------------------ podgląd na żywo
    async function refresh() {
      const code = inEd.getValue();
      const my = ++st.seq;
      if (!code.trim()) {
        st.result = null; outEd.setValue('');
        status.replaceChildren('Wklej kod po lewej.'); clear(changesHost); clear(diffHost); clear(warnHost);
        return;
      }
      status.replaceChildren(spinner(13), h('span', null, 'Formatuję…'));
      try {
        const res = await api.post('/api/beautify', { code, transforms: st.tr, style: { preset: st.preset, ...st.options } }, { quiet: true });
        if (my !== st.seq) return;
        st.result = res;
        outEd.setValue(res.code, true);
        const s = res.stats || { added: 0, removed: 0 };
        status.replaceChildren(res.changed
          ? h('span', { class: 'row gap-s nowrap' }, icon('sparkles', 14), h('span', null, 'Zmiany:'), badge(`+${s.added}`, 'success'), badge(`−${s.removed}`, 'danger'))
          : h('span', { class: 'row gap-s nowrap tone-success' }, icon('circle-check', 14), 'Kod już jest w tym stylu'));
        outTabs.setBadge('diff', res.changed ? String(s.added + s.removed) : null, 'accent');
        renderChanges(res); renderDiff();
      } catch (e) {
        if (my !== st.seq) return;
        status.replaceChildren(h('span', { class: 'tone-danger row gap-s nowrap' }, icon('circle-alert', 14), e.message));
      }
    }

    function renderChanges(res) {
      clear(changesHost);
      if (!res.changes.length) { changesHost.append(h('span', { class: 'small muted' }, 'Brak zmian.')); }
      for (const c of res.changes) changesHost.append(h('span', { class: 'chip bf-chip', title: c.label }, c.label, badge(String(c.count), 'accent')));
      clear(warnHost);
      for (const w of res.warnings || []) warnHost.append(callout(w, 'warn'));
    }

    function renderDiff() {
      clear(diffHost);
      const res = st.result;
      if (!res) return;
      if (!res.changed) { diffHost.append(emptyState({ icon: 'circle-check', title: 'Bez zmian', text: 'Wynik jest identyczny z wejściem.' })); return; }
      let rows = res.diff;
      if (st.onlyChanges) {
        const keep = new Set();
        rows.forEach((d, i) => { if (d.type !== 'eq') for (let k = Math.max(0, i - 2); k <= Math.min(rows.length - 1, i + 2); k++) keep.add(k); });
        const out = []; let last = -1;
        [...keep].sort((a, b) => a - b).forEach((i) => { if (last >= 0 && i !== last + 1) out.push({ type: 'skip' }); out.push(rows[i]); last = i; });
        rows = out;
      }
      const frag = document.createDocumentFragment();
      for (const d of rows.slice(0, 4000)) {
        if (d.type === 'skip') { frag.append(h('div', { class: 'bf-dl bf-skip' }, h('span', { class: 'bf-ln' }, '⋯'), h('span', { class: 'bf-ln' }, ''), h('span', { class: 'bf-sign' }, ''), h('span', { class: 'bf-txt muted' }, ''))); continue; }
        const cls = d.type === 'add' ? 'diff-add' : d.type === 'del' ? 'diff-del' : 'diff-eq';
        frag.append(h('div', { class: `bf-dl ${cls}` },
          h('span', { class: 'bf-ln' }, d.a ?? ''), h('span', { class: 'bf-ln' }, d.b ?? ''),
          h('span', { class: 'bf-sign' }, d.type === 'add' ? '+' : d.type === 'del' ? '−' : ' '),
          h('span', { class: 'bf-txt' }, d.text === '' ? ' ' : d.text)));
      }
      diffHost.append(frag);
    }

    // ------------------------------------------------------------------ akcje
    function applyResult() {
      if (!st.result) return;
      if (st.result.code === inEd.getValue()) { toast('Kod już jest w tym stylu.', 'info', { timeout: 1500 }); return; }
      inEd.setValue(st.result.code, true);
      toast('Wstawiono wynik do edytora.', 'success', { timeout: 1500 });
    }

    function sendBack() {
      const ret = self._returnTo;
      if (!ret || !st.result) return;
      ctx.navigate(ret, { code: st.result.code });
    }

    async function quickFormatMine() {
      try {
        const r = await api.post('/api/format', { code: inEd.getValue() });
        if (r.code !== inEd.getValue()) { inEd.setValue(r.code, true); toast('Sformatowano wg Twojego aktywnego stylu.', 'success', { timeout: 1500 }); }
        else toast('Kod już jest w Twoim stylu.', 'info', { timeout: 1500 });
        (r.warnings || []).forEach((w) => toast(w, 'warn'));
      } catch { /* toast z api */ }
    }

    async function saveMine() {
      try {
        const r = await api.post('/api/format/style', { style: { preset: st.preset, ...st.options } });
        st.active = 'moja';
        const p = await api.get('/api/format/presets');
        st.presets = p.presets; st.learned = p.learned;
        st.preset = 'moja'; st.options = { ...r.style }; st.dirty = false;
        persistStyle(); renderPresets(); renderGroups(); renderLearned(); schedule();
        toast('Zapisano jako „Moja” — Ctrl+Shift+F w każdym edytorze użyje tego stylu.', 'success');
      } catch { /* toast z api */ }
    }

    function fmtVal(key, v) {
      const s = st.schema.find((x) => x.key === key);
      if (typeof v === 'boolean') return v ? 'tak' : 'nie';
      if (s && s.type === 'choice') { const c = s.choices.find((x) => x[0] === v); return c ? c[1] : String(v); }
      return String(v);
    }

    async function learn() {
      const box = h('div', { class: 'stack' }, h('div', { class: 'row gap-s' }, spinner(16), h('span', { class: 'muted' }, 'Analizuję Twoje programy…')));
      const m = modal({ title: 'Naucz się mojego stylu', icon: 'brain-circuit', width: 760, body: box });
      const ctl = api.job('/api/format/learn', { async: true, save: false }, {
        onProgress: (p, msg) => { if (box.firstChild?.lastChild) box.firstChild.lastChild.textContent = msg || 'Analizuję…'; },
      });
      let res;
      try { res = await ctl.promise; } catch (e) { clear(box); box.append(callout(e.message, 'danger')); return; }
      clear(box);
      box.append(
        callout(`Przeanalizowano **${res.programs}** ${plural(res.programs, 'program', 'programy', 'programów')}. Poniżej styl, który najlepiej je opisuje — każda decyzja z uzasadnieniem.`, 'tip'),
        table([
          { key: 'label', label: 'Opcja', render: (r) => label({ label: r.label }) },
          { key: 'value', label: 'Wartość', render: (r) => h('b', null, fmtVal(r.key, r.value)) },
          { key: 'detail', label: 'Na podstawie', render: (r) => h('span', { class: 'small muted' }, r.detail) },
        ], res.evidence, { compact: true, maxHeight: '46vh' }));
      const actions = h('div', { class: 'row gap-s', style: 'justify-content:flex-end' },
        button('Anuluj', { variant: 'ghost', onClick: () => m.close() }),
        button('Tylko podejrzyj', { icon: 'eye', onClick: () => { st.preset = 'moja'; st.options = { ...res.options }; st.dirty = true; persistStyle(); renderPresets(); renderGroups(); schedule(); m.close(); } }),
        button('Zapisz jako mój styl', { icon: 'save', variant: 'primary', onClick: async () => {
          await api.post('/api/format/style', { style: res.options });
          const p = await api.get('/api/format/presets');
          st.presets = p.presets; st.active = p.active; st.learned = { at: new Date().toLocaleString('pl-PL'), programs: res.programs };
          st.preset = 'moja'; st.options = { ...p.current }; st.dirty = false;
          persistStyle(); renderPresets(); renderGroups(); renderLearned(); schedule(); m.close();
          toast('Zapisano Twój styl jako „Moja”.', 'success');
        } }));
      box.append(actions);
    }
  },

  onShow(params) {
    if (!params) return;
    if (params.code !== undefined && this._inEd) {
      this._inEd.setValue(String(params.code));
      this._schedule && this._schedule();
    }
    this._returnTo = params.returnTo || params.from || null;
    if (this._backBtn) this._backBtn.hidden = !this._returnTo;
  },
};

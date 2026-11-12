// UI kit OI Forge — spójne komponenty dla wszystkich widoków. Patrz docs/CONTRACT.md 4.2.
import { ICONS } from './icons.js';
import { isStarred, qualityNote, HINT_ONLY } from './quality.js';
import { markdownToDom, escapeHtml } from './markdown.js';
import { api, _setToast } from './api.js';

export { escapeHtml };

// Natywne append/prepend/replaceChildren zamieniają tablice i null na tekst („[object HTMLSpanElement]”, „null”).
// W całej aplikacji chcemy semantyki jak w h(): tablice spłaszczone, null/undefined/false pominięte.
(function patchDomInsertion() {
  if (window.__oifDomPatched) return;
  window.__oifDomPatched = true;
  const fix = (orig) => function patched(...args) {
    let needs = false;
    for (const a of args) if (a === null || a === undefined || a === false || Array.isArray(a)) { needs = true; break; }
    return orig.apply(this, needs ? args.flat(Infinity).filter((a) => a !== null && a !== undefined && a !== false) : args);
  };
  for (const proto of [Element.prototype, DocumentFragment.prototype, Document.prototype]) {
    for (const m of ['append', 'prepend', 'replaceChildren']) if (typeof proto[m] === 'function') proto[m] = fix(proto[m]);
  }
})();

// ------------------------------------------------------------------------------------------- h()
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class' || k === 'className') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
      else if (k === 'style') { if (typeof v === 'string') el.style.cssText = v; else Object.assign(el.style, v); }
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value') el.value = v;
      else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'hidden' || k === 'readOnly' || k === 'multiple') el[k] = !!v;
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === true) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }

// ------------------------------------------------------------------------------------------- ikony
export function icon(name, size = 16, attrs = {}) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', attrs.strokeWidth || 2);
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon', `icon-${name}`);
  svg.innerHTML = ICONS[name] || ICONS['circle-alert'] || '';
  return svg;
}

// ------------------------------------------------------------------------------------------- przyciski
export function button(label, opts = {}) {
  const { icon: ic, variant = 'secondary', size = 'md', onClick, title, disabled, type = 'button', iconRight, attrs = {} } = opts;
  const cls = ['btn', variant === 'secondary' ? '' : variant, size !== 'md' ? size : '', !label ? 'icon' : ''].filter(Boolean);
  if (opts.class) cls.push(opts.class);
  const b = h('button', { class: cls.join(' '), type, title: title || (typeof label === 'string' ? undefined : undefined), disabled, ...attrs },
    ic ? icon(ic, size === 'sm' ? 14 : size === 'lg' ? 18 : 16) : null, label ? h('span', null, label) : null,
    iconRight ? icon(iconRight, 14) : null);
  if (title) b.title = title;
  if (onClick) {
    b.addEventListener('click', async (e) => {
      if (b.dataset.busy) return;
      const r = onClick(e);
      if (r && typeof r.then === 'function') {
        setBusy(b, true);
        try { await r; } catch (err) { if (!err?.cancelled) console.error(err); } finally { setBusy(b, false); }
      }
    });
  }
  return b;
}

export function setBusy(btn, busy) {
  if (!btn) return;
  if (busy) {
    btn.dataset.busy = '1';
    btn.disabled = true;
    const sp = h('span', { class: 'spinner' });
    const ic = btn.querySelector('svg.icon');
    if (ic) { ic.style.display = 'none'; }
    btn.prepend(sp);
  } else {
    delete btn.dataset.busy;
    btn.disabled = false;
    btn.querySelector('.spinner')?.remove();
    const ic = btn.querySelector('svg.icon');
    if (ic) ic.style.display = '';
  }
}

export function iconButton(iconName, opts = {}) {
  return button(null, { icon: iconName, variant: opts.variant || 'ghost', size: opts.size || 'sm', ...opts });
}

// ------------------------------------------------------------------------------------------- nagłówek widoku, karty
export function viewHeader(meta, ...actions) {
  // ★ = sprawdzone i działa pewnie; „podpowiedź” = heurystyka, wynik trzeba sprawdzić (web/js/quality.js)
  const q = isStarred(meta.id) ? h('span', { class: 'q-chip star', title: qualityNote(meta.id) }, '★ działa pewnie')
    : HINT_ONLY[meta.id] ? h('span', { class: 'q-chip hint', title: qualityNote(meta.id) }, 'podpowiedź — sprawdzaj wynik') : null;
  return h('div', { class: 'view-header' },
    meta.icon ? h('div', { class: 'vh-icon' }, icon(meta.icon, 22)) : null,
    h('div', { class: 'grow' }, h('h1', null, meta.title, q), meta.description ? h('p', { title: meta.description }, meta.description) : null),
    actions.length ? h('div', { class: 'vh-actions' }, actions) : null);
}

export function card({ title, icon: ic, subtitle, actions = [], body, class: cls, padded = true, attrs } = {}) {
  const head = (title || actions.length) ? h('div', { class: 'card-head' },
    ic ? h('div', { class: 'card-icon' }, icon(ic, 17)) : null,
    h('div', { class: 'grow' }, title ? h('div', { class: 'card-title' }, title) : null, subtitle ? h('div', { class: 'card-sub' }, subtitle) : null),
    actions.length ? h('div', { class: 'card-actions' }, actions) : null) : null;
  const bodyEl = h('div', { class: padded ? 'card-body' : 'card-body flush' }, body);
  const el = h('div', { class: ['card', cls].filter(Boolean).join(' '), ...(attrs || {}) }, head, bodyEl);
  el.body = bodyEl;
  return el;
}

export function section(title, ...children) {
  return h('div', { class: 'stack gap-s' }, h('div', { class: 'section-title' }, title), children);
}

// ------------------------------------------------------------------------------------------- zakładki
export function tabs(items, { active, onChange, class: cls } = {}) {
  const list = h('div', { class: 'tab-list', role: 'tablist' });
  const panels = h('div', { class: 'tab-panels' });
  const root = h('div', { class: ['tabs', cls].filter(Boolean).join(' ') }, list, panels);
  const map = new Map();
  let current = null;
  const select = (id) => {
    if (!map.has(id)) return;
    for (const [k, v] of map) {
      const on = k === id;
      v.btn.classList.toggle('active', on);
      v.btn.setAttribute('aria-selected', on);
      if (on && !v.rendered) { v.panel.append(v.item.render ? v.item.render() : v.item.content || ''); v.rendered = true; }
      v.panel.hidden = !on;
    }
    const changed = current !== id;
    const initial = current === null;
    current = id;
    if (changed) {
      root.dispatchEvent(new CustomEvent('tabchange', { detail: id }));
      // przy pierwszym wyborze (w trakcie budowania widoku) wywołaj onChange dopiero po zakończeniu bieżącego kodu
      if (onChange) { if (initial) queueMicrotask(() => { try { onChange(id); } catch (e) { console.error(e); } }); else onChange(id); }
      // CodeMirror w ukrytym panelu wymaga odświeżenia
      requestAnimationFrame(() => map.get(id)?.panel.querySelectorAll('.CodeMirror').forEach((c) => c.CodeMirror?.refresh()));
    }
  };
  for (const it of items) {
    const btn = h('button', { class: 'tab-btn', role: 'tab', type: 'button', onclick: () => select(it.id) },
      it.icon ? icon(it.icon, 15) : null, it.label, it.badge !== undefined && it.badge !== null ? badge(String(it.badge), it.badgeTone || 'neutral') : null);
    const panel = h('div', { class: 'tab-panel', role: 'tabpanel', hidden: true });
    map.set(it.id, { btn, panel, item: it, rendered: false });
    list.append(btn);
    panels.append(panel);
  }
  select(active && map.has(active) ? active : items[0]?.id);
  return {
    el: root, select, get active() { return current; },
    panel: (id) => map.get(id)?.panel,
    setBadge(id, text, tone) {
      const v = map.get(id); if (!v) return;
      v.btn.querySelector('.badge')?.remove();
      if (text !== null && text !== undefined && text !== '') v.btn.append(badge(String(text), tone || 'neutral'));
    },
  };
}

// ------------------------------------------------------------------------------------------- split
export function split(left, right, { ratio = 0.5, min = 240, direction = 'h', storageKey } = {}) {
  const vertical = direction === 'v';
  let r = ratio;
  if (storageKey) { try { const s = parseFloat(localStorage.getItem('oif:split:' + storageKey)); if (s > 0 && s < 1) r = s; } catch { /* */ } }
  const a = h('div', { class: 'pane' }, left);
  const b = h('div', { class: 'pane' }, right);
  const handle = h('div', { class: 'split-handle', role: 'separator' });
  const root = h('div', { class: vertical ? 'split v' : 'split' }, a, handle, b);
  const apply = () => { a.style.flex = `0 0 calc(${(r * 100).toFixed(3)}% - 5px)`; b.style.flex = '1 1 0'; };
  apply();
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add('drag');
    const rect = root.getBoundingClientRect();
    const move = (ev) => {
      const total = vertical ? rect.height : rect.width;
      const pos = vertical ? ev.clientY - rect.top : ev.clientX - rect.left;
      r = Math.min(Math.max(pos, min), total - min) / total;
      apply();
    };
    const up = () => {
      handle.classList.remove('drag');
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      if (storageKey) { try { localStorage.setItem('oif:split:' + storageKey, String(r)); } catch { /* */ } }
      root.querySelectorAll('.CodeMirror').forEach((c) => c.CodeMirror?.refresh());
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
  handle.addEventListener('dblclick', () => { r = ratio; apply(); root.querySelectorAll('.CodeMirror').forEach((c) => c.CodeMirror?.refresh()); });
  return root;
}

// ------------------------------------------------------------------------------------------- formularze
export function field(label, control, hint) {
  return h('div', { class: 'field' }, label ? h('label', null, label) : null, control, hint ? h('div', { class: 'hint' }, hint) : null);
}

export function input({ value = '', placeholder = '', type = 'text', onInput, onEnter, onChange, mono, size, attrs = {}, class: cls } = {}) {
  const el = h('input', { class: ['input', mono ? 'mono' : '', size || '', cls].filter(Boolean).join(' '), type, placeholder, value, spellcheck: 'false', ...attrs });
  if (onInput) el.addEventListener('input', () => onInput(el.value, el));
  if (onChange) el.addEventListener('change', () => onChange(el.value, el));
  if (onEnter) el.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); onEnter(el.value, el); } });
  return el;
}

export function textarea({ value = '', placeholder = '', rows = 5, mono = true, onInput, attrs = {}, class: cls } = {}) {
  const el = h('textarea', { class: ['textarea', mono ? 'mono' : '', cls].filter(Boolean).join(' '), placeholder, rows, spellcheck: 'false', ...attrs });
  el.value = value;
  if (onInput) el.addEventListener('input', () => onInput(el.value, el));
  if (mono) indentKeys(el);
  return el;
}

/** Tab / Shift+Tab w polu tekstowym jak w edytorze: zaznaczone linie dostają (tracą) jedno wcięcie (4 spacje),
 *  bez zaznaczenia Tab wstawia 4 spacje, a Shift+Tab cofa wcięcie bieżącej linii. Działa z Ctrl+Z. */
export function indentKeys(el) {
  const put = (text, a, b) => {
    el.setSelectionRange(a, b);
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch { ok = false; }
    if (!ok || el.value.slice(a, a + text.length) !== text) { el.setRangeText(text, a, b, 'end'); el.dispatchEvent(new Event('input', { bubbles: true })); }
  };
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab' || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    const v = el.value;
    const s = el.selectionStart, en = el.selectionEnd;
    const multi = v.slice(s, en).includes('\n');
    e.preventDefault();
    if (!e.shiftKey && !multi) { put('    ', s, en); return; }
    const ls = v.lastIndexOf('\n', s - 1) + 1;
    const lastPos = multi && v[en - 1] === '\n' ? en - 1 : en;      // zaznaczenie kończy się na początku linii → bez niej
    const nl = v.indexOf('\n', lastPos);
    const be = nl < 0 ? v.length : nl;
    let first = 0, total = 0;
    const lines = v.slice(ls, be).split('\n').map((ln, i) => {
      if (e.shiftKey) {
        const d = (/^(?: {1,4}|\t)/.exec(ln) || [''])[0].length;
        if (i === 0) first = -d;
        total -= d;
        return ln.slice(d);
      }
      if (!ln.trim()) return ln;
      if (i === 0) first = 4;
      total += 4;
      return '    ' + ln;
    });
    if (!total) return;
    put(lines.join('\n'), ls, be);
    const ns = Math.max(ls, s + first);
    el.setSelectionRange(ns, Math.max(ns, en + total));
  });
}

export function select(options, { value, onChange, size, attrs = {} } = {}) {
  const el = h('select', { class: ['select', size || ''].filter(Boolean).join(' '), ...attrs },
    options.map((o) => {
      const opt = typeof o === 'object' ? o : { value: o, label: o };
      return h('option', { value: opt.value, selected: String(opt.value) === String(value) }, opt.label ?? opt.value);
    }));
  if (value !== undefined) el.value = value;
  if (onChange) el.addEventListener('change', () => onChange(el.value, el));
  return el;
}

// Zwijana sekcja „Więcej opcji” — rzadziej używane ustawienia nie zaśmiecają widoku (stan pamiętany per klucz).
export function more(label, children, { key, open = false } = {}) {
  let isOpen = open;
  try { if (key) { const v = localStorage.getItem('oif:more:' + key); if (v !== null) isOpen = v === '1'; } } catch { /* brak localStorage */ }
  const body = h('div', { class: 'more-body' }, children);
  const el = h('div', { class: ['more', isOpen ? 'open' : ''].join(' ') });
  const head = h('button', { class: 'more-head', type: 'button', onclick: () => {
    const o = !el.classList.contains('open');
    el.classList.toggle('open', o);
    try { if (key) localStorage.setItem('oif:more:' + key, o ? '1' : '0'); } catch { /* ignoruj */ }
  } }, icon('chevron-right', 14), h('span', null, label));
  el.append(head, body);
  return el;
}

export function checkbox(label, { checked = false, onChange, title } = {}) {
  const inp = h('input', { type: 'checkbox', checked });
  const el = h('label', { class: 'checkbox', title }, inp, label);
  if (onChange) inp.addEventListener('change', () => onChange(inp.checked, inp));
  el.input = inp;
  Object.defineProperty(el, 'checked', { get: () => inp.checked, set: (v) => { inp.checked = !!v; } });
  return el;
}

export function segmented(options, { value, onChange } = {}) {
  const root = h('div', { class: 'segmented', role: 'radiogroup' });
  let cur = value ?? (typeof options[0] === 'object' ? options[0].value : options[0]);
  const btns = options.map((o) => {
    const opt = typeof o === 'object' ? o : { value: o, label: o };
    const b = h('button', { type: 'button', class: String(opt.value) === String(cur) ? 'active' : '', title: opt.title,
      onclick: () => { set(opt.value); onChange && onChange(opt.value); } }, opt.icon ? icon(opt.icon, 14) : null, opt.label);
    b.dataset.value = opt.value;
    return b;
  });
  function set(v) { cur = v; btns.forEach((b) => b.classList.toggle('active', b.dataset.value === String(v))); }
  root.append(...btns);
  root.setValue = set;
  Object.defineProperty(root, 'value', { get: () => cur, set });
  return root;
}

// ------------------------------------------------------------------------------------------- znaczniki
export function badge(text, tone = 'neutral', ic) {
  return h('span', { class: ['badge', tone !== 'neutral' ? tone : ''].filter(Boolean).join(' ') }, ic ? icon(ic, 12) : null, text);
}

const VERDICTS = {
  OK: ['success', 'OK', 'circle-check'], AC: ['success', 'OK', 'circle-check'], WA: ['danger', 'WA', 'x'], TLE: ['warn', 'TLE', 'timer'],
  RE: ['danger', 'RE', 'bug'], MLE: ['warn', 'MLE', 'database'], CE: ['danger', 'CE', 'triangle-alert'], RUN: ['info', '…', 'loader'],
  SKIP: ['neutral', 'POMINIĘTO', 'skip-forward'], ERR: ['danger', 'BŁĄD', 'circle-alert'],
};
export const VERDICT_NAMES = {
  OK: 'Poprawnie', WA: 'Zła odpowiedź', TLE: 'Przekroczony limit czasu', RE: 'Błąd wykonania', MLE: 'Przekroczony limit pamięci', CE: 'Błąd kompilacji',
};
export function verdictBadge(verdict, label) {
  const [tone, text, ic] = VERDICTS[verdict] || ['neutral', verdict || '?', 'info'];
  const b = badge(label || text, tone, ic);
  b.classList.add('verdict');
  b.title = VERDICT_NAMES[verdict] || verdict || '';
  return b;
}

export function kbd(text) { return h('kbd', null, text); }

export function spinner(size = 18) { return h('span', { class: 'spinner', style: { width: size + 'px', height: size + 'px' } }); }

export function progressBar({ label = '', indeterminate = false } = {}) {
  const fill = h('div', { class: ['progress-fill', indeterminate ? 'indeterminate' : ''].join(' ') });
  const lab = h('span', null, label);
  const pct = h('span', null, '');
  const el = h('div', { class: 'progress' }, h('div', { class: 'progress-label' }, lab, pct), h('div', { class: 'progress-track' }, fill));
  return {
    el,
    set(fraction, text) {
      fill.classList.remove('indeterminate');
      const f = Math.max(0, Math.min(1, fraction || 0));
      fill.style.width = (f * 100).toFixed(1) + '%';
      pct.textContent = Math.round(f * 100) + '%';
      if (text !== undefined) lab.textContent = text;
    },
    indeterminate(text) { fill.classList.add('indeterminate'); pct.textContent = ''; if (text !== undefined) lab.textContent = text; },
  };
}

export function emptyState({ icon: ic = 'sparkles', title, text, action } = {}) {
  return h('div', { class: 'empty' }, h('div', { class: 'e-icon' }, icon(ic, 24)),
    title ? h('div', { class: 'e-title' }, title) : null, text ? h('div', { class: 'e-text' }, text) : null, action || null);
}

export function stat({ label, value, sub, tone, icon: ic } = {}) {
  return h('div', { class: 'stat' }, h('div', { class: 'row gap-s s-label' }, ic ? icon(ic, 14) : null, label),
    h('div', { class: ['s-value', tone ? 'tone-' + tone : ''].join(' ') }, value ?? '—'), sub ? h('div', { class: 's-sub' }, sub) : null);
}

export function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).map(([k, v]) => [h('dt', null, k), h('dd', null, v ?? '—')]));
}

// ------------------------------------------------------------------------------------------- tabela
export function table(columns, rows, { onRowClick, compact, empty = 'Brak danych', maxHeight } = {}) {
  const thead = h('thead', null, h('tr', null, columns.map((c) => h('th', { class: c.align === 'right' ? 'num' : '', style: c.width ? { width: c.width } : null }, c.label))));
  const tbody = h('tbody', null);
  const fill = (rs) => {
    clear(tbody);
    if (!rs.length) { tbody.append(h('tr', null, h('td', { colspan: columns.length, class: 'muted center' }, empty))); return; }
    for (const r of rs) {
      const tr = h('tr', null, columns.map((c) => {
        const v = c.render ? c.render(r) : r[c.key];
        return h('td', { class: c.align === 'right' ? 'num' : c.class || '' }, v instanceof Node ? v : (v ?? ''));
      }));
      if (onRowClick) tr.addEventListener('click', () => onRowClick(r, tr));
      tbody.append(tr);
    }
  };
  fill(rows || []);
  const tbl = h('table', { class: ['tbl', onRowClick ? 'clickable' : '', compact ? 'compact' : ''].filter(Boolean).join(' ') }, thead, tbody);
  const wrap = h('div', { class: 'table-wrap', style: maxHeight ? { maxHeight } : null }, tbl);
  wrap.update = fill;
  return wrap;
}

// ------------------------------------------------------------------------------------------- toasty i modale
let toastRoot = null;
export function toast(message, tone = 'info', { timeout } = {}) {
  if (!toastRoot) { toastRoot = h('div', { id: 'toasts' }); document.body.append(toastRoot); }
  const ic = { success: 'circle-check', error: 'circle-alert', danger: 'circle-alert', warn: 'triangle-alert', info: 'info' }[tone] || 'info';
  const el = h('div', { class: `toast ${tone}`, role: 'status' }, h('div', { class: 't-icon' }, icon(ic, 17)), h('div', { class: 'grow' }, message));
  toastRoot.append(el);
  const t = timeout ?? (tone === 'error' ? 6500 : 3500);
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), 200); };
  el.addEventListener('click', close);
  setTimeout(close, t);
  return { close };
}
_setToast(toast);

export function modal({ title, body, actions = [], width, onClose, icon: ic } = {}) {
  const prevFocus = document.activeElement;
  const box = h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', style: width ? { '--mw': typeof width === 'number' ? width + 'px' : width } : null },
    title ? h('div', { class: 'modal-head' }, ic ? icon(ic, 18) : null, h('div', { class: 'grow' }, title),
      iconButton('x', { title: 'Zamknij (Esc)', onClick: () => close() })) : null,
    h('div', { class: 'modal-body' }, body),
    actions.length ? h('div', { class: 'modal-foot' }, actions) : null);
  const bd = h('div', { class: 'backdrop', onmousedown: (e) => { if (e.target === bd) close(); } }, box);
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  document.body.append(bd);
  setTimeout(() => box.querySelector('input, textarea, select, button.primary')?.focus(), 30);
  function close() {
    document.removeEventListener('keydown', onKey, true);
    bd.remove();
    onClose && onClose();
    prevFocus?.focus?.();
  }
  return { close, el: box };
}

export function confirmDialog(message, { title = 'Potwierdź', ok = 'OK', danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = modal({
      title, body: h('div', { class: 'md' }, message), width: 440,
      onClose: () => { if (!done) resolve(false); },
      actions: [button('Anuluj', { variant: 'ghost', onClick: () => { done = true; resolve(false); m.close(); } }),
        button(ok, { variant: danger ? 'danger' : 'primary', onClick: () => { done = true; resolve(true); m.close(); } })],
    });
  });
}

export function promptDialog(message, { title = 'Podaj wartość', value = '', placeholder = '' } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const inp = input({ value, placeholder, onEnter: () => { done = true; resolve(inp.value); m.close(); } });
    const m = modal({
      title, width: 440, body: h('div', { class: 'stack' }, message ? h('div', null, message) : null, inp),
      onClose: () => { if (!done) resolve(null); },
      actions: [button('Anuluj', { variant: 'ghost', onClick: () => m.close() }),
        button('OK', { variant: 'primary', onClick: () => { done = true; resolve(inp.value); m.close(); } })],
    });
  });
}

// ------------------------------------------------------------------------------------------- kod i markdown
const MODES = { cpp: 'text/x-c++src', 'c++': 'text/x-c++src', c: 'text/x-csrc', python: 'python', py: 'python', text: 'text/plain', txt: 'text/plain', bash: 'text/plain', sh: 'text/plain', json: 'text/plain', plain: 'text/plain' };
// Podświetlanie funkcji: tryb-nakładka na clike/python — nazwa przed „(” dostaje dodatkową klasę cm-fn
// (wywołania, definicje, metody .push_back(), print(…)). Deklaracje zmiennych z konstruktorem („vector<int> v(n)”,
// „ll x(0)”) zostają zmiennymi: po typie nazwa jest funkcją tylko wtedy, gdy w nawiasie stoją parametry („int l, …”).
const FN_STYLE = /^(variable|variable-2|def|property|builtin)$/;
const FN_PARAMS = /^\s*\(\s*(?:\)|$|(?:const\s+)?[A-Za-z_][\w:]*(?:\s*<[^()]*>)?(?:\s+[A-Za-z_]\w*)*[\s&*]+[A-Za-z_]\w*\s*(?:[,)=[]|$))/;
const FN_DECL_LIST = /^\s*(?:(?:const|static|unsigned|signed|long|short)\s+)*[A-Za-z_][\w:]*(?:\s*<[^;]*>)?[\s&*]+[A-Za-z_]\w*/;
const FN_WRAPPED = { 'text/x-c++src': 'text/x-oif-c++src', 'text/x-csrc': 'text/x-oif-csrc', python: 'text/x-oif-python' };
let fnModes = null;
function ensureFnModes() {
  if (fnModes !== null) return fnModes;
  const CM = window.CodeMirror;
  if (!CM || !CM.defineMode || !CM.modes.clike || !CM.modes.python) return false;
  CM.defineMode('oif-fn', (config, pc) => {
    const inner = CM.getMode(config, pc.inner);
    const cpp = pc.lang === 'cpp';
    const token = (stream, state) => {
      const style = inner.token(stream, state);
      const text = stream.current();
      const prev = state.__oifPrev;
      if (/\S/.test(text)) state.__oifPrev = { s: style || '', t: text };
      if (!style || !FN_STYLE.test(style) || !stream.match(/^\s*\(/, false)) return style;
      if (prev && (prev.t === 'class' || prev.t === 'struct')) return style;
      if (cpp && prev) {
        // po typie / nazwie typu / „>” szablonu to deklaracja: funkcja tylko z listą parametrów
        const before = stream.string.slice(0, stream.start);
        const declCtx = prev.s === 'type' || prev.s === 'variable-3'
          || (/^(variable|def|builtin)$/.test(prev.s) && /^[A-Za-z_]\w*$/.test(prev.t) && before.trimEnd().endsWith(prev.t))
          || (/^>+$/.test(prev.t) && /\w\s*<[^;()]*>\s*$/.test(before))
          || (prev.t === ',' && FN_DECL_LIST.test(before) && !/^\s*(return|else|case|throw|delete|new|goto)\b/.test(before)
            && (before.match(/\(/g) || []).length === (before.match(/\)/g) || []).length);   // „vector<int> a(n), b(n, 0)”
        if (declCtx && !FN_PARAMS.test(stream.string.slice(stream.pos))) return style;
      }
      return `${style} fn`;
    };
    return Object.assign({}, inner, { token });
  });
  CM.defineMIME('text/x-oif-c++src', { name: 'oif-fn', inner: 'text/x-c++src', lang: 'cpp' });
  CM.defineMIME('text/x-oif-csrc', { name: 'oif-fn', inner: 'text/x-csrc', lang: 'cpp' });
  CM.defineMIME('text/x-oif-python', { name: 'oif-fn', inner: 'python', lang: 'python' });
  fnModes = true;
  return true;
}
export function cmMode(lang) {
  const m = MODES[(lang || 'cpp').toLowerCase()] || 'text/plain';
  return FN_WRAPPED[m] && ensureFnModes() ? FN_WRAPPED[m] : m;
}

export function highlightInto(target, code, lang = 'cpp') {
  const CM = window.CodeMirror;
  if (CM && CM.runMode) {
    CM.runMode(code, cmMode(lang), (text, style) => {
      if (style) target.append(h('span', { class: style.split(' ').map((s) => 'cm-' + s).join(' ') }, text));
      else target.append(document.createTextNode(text));
    });
  } else target.textContent = code;
}

export function codeBlock(code, lang = 'cpp', { copy = true, lineNumbers = false, highlightLines = [], title, actions = [], maxHeight, head } = {}) {
  code = String(code ?? '').replace(/\n$/, '');
  const pre = h('pre', { class: 'cm-s-oiforge' });
  if (maxHeight) pre.style.setProperty('--cb-max', typeof maxHeight === 'number' ? maxHeight + 'px' : maxHeight);
  if (lineNumbers || highlightLines.length) {
    const hl = new Set(highlightLines);
    code.split('\n').forEach((ln, i) => {
      const lineEl = h('span', { class: hl.has(i + 1) ? 'hl-line' : '' });
      if (lineNumbers) lineEl.append(h('span', { class: 'ln' }, String(i + 1)));
      highlightInto(lineEl, ln, lang);
      pre.append(lineEl, '\n');
    });
  } else highlightInto(pre, code, lang);
  const showHead = head ?? (!!title || actions.length > 0 || lang !== 'text');
  const copyBtn = copy ? copyButton(() => code) : null;
  if (!showHead) {
    return h('div', { class: 'codeblock no-head' }, pre, copyBtn ? h('div', { class: 'cb-float' }, copyBtn) : null);
  }
  return h('div', { class: 'codeblock' },
    h('div', { class: 'cb-head' }, h('span', { class: 'cb-lang' }, title || ({ cpp: 'C++', python: 'Python', py: 'Python' }[lang] || lang)),
      h('div', { class: 'cb-actions' }, actions, copyBtn)), pre);
}

export function copyButton(getText, { label, size = 'sm' } = {}) {
  const b = button(label || null, {
    icon: 'copy', variant: 'ghost', size, title: 'Kopiuj',
    onClick: async () => {
      const text = typeof getText === 'function' ? getText() : String(getText ?? '');
      try { await navigator.clipboard.writeText(text); } catch {
        const ta = h('textarea', { style: 'position:fixed;opacity:0' }); ta.value = text; document.body.append(ta); ta.select();
        document.execCommand('copy'); ta.remove();
      }
      const ic = b.querySelector('svg');
      if (ic) { ic.replaceWith(icon('check', 14)); setTimeout(() => b.querySelector('svg')?.replaceWith(icon('copy', 14)), 1200); }
      toast('Skopiowano do schowka', 'success', { timeout: 1500 });
    },
  });
  return b;
}

export function markdown(md) {
  return markdownToDom(md, { codeBlock: (code, lang) => codeBlock(code, lang), icon });
}

export function callout(md, tone = 'info') {
  const ic = { tip: 'lightbulb', warn: 'triangle-alert', info: 'info', danger: 'circle-alert', success: 'circle-check' }[tone] || 'info';
  return h('div', { class: `callout ${tone === 'success' ? 'tip' : tone}` }, h('div', { class: 'c-icon' }, icon(ic, 17)),
    h('div', { class: 'grow' }, typeof md === 'string' ? markdown(md) : md));
}

// ------------------------------------------------------------------------------------------- pliki
export function fileDrop({ accept, onFiles, label = 'Upuść plik tutaj albo kliknij, aby wybrać', multiple = false } = {}) {
  const inp = h('input', { type: 'file', accept, multiple, style: 'display:none' });
  const el = h('div', { class: 'filedrop', tabindex: 0 }, icon('upload', 18), h('div', { class: 'mt-s' }, label), inp);
  const handle = (files) => { if (files && files.length && onFiles) onFiles(multiple ? [...files] : files[0]); };
  el.addEventListener('click', () => inp.click());
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') inp.click(); });
  inp.addEventListener('change', () => { handle(inp.files); inp.value = ''; });
  el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('over'); });
  el.addEventListener('dragleave', () => el.classList.remove('over'));
  el.addEventListener('drop', (e) => { e.preventDefault(); el.classList.remove('over'); handle(e.dataTransfer.files); });
  return el;
}

export function readFileText(file) {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsText(file); });
}

// ------------------------------------------------------------------------------------------- panel wyników
const SIGNALS = {
  SIGSEGV: 'Naruszenie ochrony pamięci — najczęściej wyjście poza tablicę, za głęboka rekurencja albo dereferencja złego wskaźnika.',
  SIGBUS: 'Błąd magistrali — zwykle odwołanie do złej pamięci (np. poza tablicą, przepełnienie stosu).',
  SIGFPE: 'Błąd arytmetyczny — prawie zawsze dzielenie (lub modulo) przez zero.',
  SIGABRT: 'Program przerwany — nieudana asercja, wyjątek C++ albo wykryte uszkodzenie pamięci.',
  SIGKILL: 'Program zabity — przekroczony limit czasu lub pamięci.',
  SIGILL: 'Niedozwolona instrukcja — np. brak `return` w funkcji zwracającej wartość (UB) albo uszkodzony stos.',
};
export function fmtTime(s) { if (s === null || s === undefined) return '—'; return s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`; }
export function fmtMem(kb) { if (!kb && kb !== 0) return '—'; return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`; }

export function outputPanel({ title } = {}) {
  const head = h('div', { class: 'output-head' });
  const body = h('div', { class: 'stack gap-s' });
  const el = h('div', { class: 'output' }, title ? h('div', { class: 'out-label' }, title) : null, head, body);
  let busy = false;
  const api_ = {
    el,
    clear() { clear(head); clear(body); },
    setBusy(b, text = 'Kompiluję i uruchamiam…') {
      busy = b;
      if (b) { clear(head); clear(body); head.append(spinner(16), h('span', { class: 'muted' }, text)); }
    },
    showText(text, tone) { api_.clear(); body.append(h('pre', { class: ['out', tone === 'danger' ? 'err' : ''].join(' ') }, text)); },
    async showCompile(comp, source) {
      clear(head); clear(body);
      head.append(verdictBadge('CE', 'Błąd kompilacji'), comp?.compiler ? h('span', { class: 'muted small' }, shortCompiler(comp.compiler)) : null);
      const stderr = comp?.stderr || '';
      let cards = null;
      try {
        const r = await api.post('/api/errors/translate', { stderr, source }, { quiet: true });
        const items = (r.errors || r.items || r.results || []).filter((x) => x.severity !== 'note');
        if (items.length) {
          cards = h('div', { class: 'stack gap-s' }, items.slice(0, 8).map((e) => h('div', { class: ['err-card', e.severity === 'warning' ? 'warning' : ''].join(' ') },
            h('div', { class: 'ec-title' }, icon(e.severity === 'warning' ? 'triangle-alert' : 'circle-alert', 15),
              h('span', { class: 'grow' }, e.title || 'Błąd'), e.line ? h('span', { class: 'ec-line', onclick: () => el.dispatchEvent(new CustomEvent('gotoline', { detail: e.line, bubbles: true })) }, `linia ${e.line}`) : null),
            e.explanation ? h('div', { class: 'ec-text' }, markdown(e.explanation)) : null,
            e.fix ? h('div', { class: 'ec-fix' }, markdown('**Jak naprawić:** ' + e.fix)) : null)));
        }
      } catch { /* moduł tłumacza może nie istnieć */ }
      if (cards) body.append(cards);
      body.append(h('details', { open: !cards }, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Oryginalny komunikat kompilatora'),
        h('pre', { class: 'out err mt-s' }, stderr || '(brak komunikatu)')));
    },
    showRun(run, { expected, compile } = {}) {
      clear(head); clear(body);
      if (!run) return;
      let verdict = run.verdict;
      if (verdict === 'OK' && expected !== undefined && expected !== null && expected.trim() !== '') {
        const eq = run.stdout.trim().split(/\s+/).join(' ') === expected.trim().split(/\s+/).join(' ');
        verdict = eq ? 'OK' : 'WA';
      }
      head.append(verdictBadge(verdict, verdict === 'OK' ? 'OK' : undefined),
        h('div', { class: 'output-meta' },
          h('span', null, 'czas ', h('b', null, fmtTime(run.time))),
          run.mem_kb ? h('span', null, 'pamięć ', h('b', null, fmtMem(run.mem_kb))) : null,
          run.exit_code !== 0 && run.exit_code !== null ? h('span', null, 'kod wyjścia ', h('b', null, String(run.exit_code))) : null,
          compile?.cached === false && compile?.time ? h('span', null, 'kompilacja ', h('b', null, fmtTime(compile.time))) : null));
      if (run.timed_out || run.signal || (run.verdict === 'RE')) {
        const fallback = run.timed_out
          ? callout('Program nie zakończył się w limicie czasu. Sprawdź złożoność albo nieskończoną pętlę (np. zły warunek w `while`).', 'warn')
          : callout(`**${run.signal || 'Błąd wykonania'}** — ${SIGNALS[(run.signal || '').split(' ')[0]] || 'program zakończył się błędem wykonania.'}`, 'danger');
        const slot = h('div', null, fallback);
        body.append(slot);
        // pełne wyjaśnienie z modułu tłumacza błędów (przyczyny + jak naprawić), jeśli jest dostępny
        api.post('/api/errors/runtime', { run }, { quiet: true }).then((r) => {
          const x = r && r.explanation;
          if (!x || !x.title) return;
          const md = [`**${x.title}**${x.line ? ` (linia ${x.line})` : ''} — ${x.explanation || ''}`,
            x.causes && x.causes.length ? '\n**Najczęstsze przyczyny:**\n' + x.causes.map((c) => `- ${c}`).join('\n') : '',
            x.fix ? `\n**Co zrobić:** ${x.fix}` : ''].join('\n');
          slot.replaceChildren(callout(md, run.timed_out ? 'warn' : 'danger'));
        }).catch(() => {});
      }
      body.append(h('div', { class: 'out-label' }, 'Wyjście', copyButton(() => run.stdout)),
        h('pre', { class: 'out' }, run.stdout || h('span', { class: 'faint' }, '(puste wyjście)')));
      if (run.truncated) body.append(h('div', { class: 'muted small' }, 'Wyjście zostało obcięte (za duże).'));
      if (run.stderr && run.stderr.trim()) body.append(h('div', { class: 'out-label' }, 'stderr'), h('pre', { class: 'out err' }, run.stderr));
      if (compile?.warnings) body.append(h('details', null, h('summary', { class: 'muted small', style: 'cursor:pointer' }, `Ostrzeżenia kompilatora (${compile.warnings})`), h('pre', { class: 'out mt-s' }, compile.stderr)));
    },
    /** Wynik z /api/run: {ok, compile, run} */
    showResult(res, opts = {}) {
      if (!res) return;
      if (!res.ok || (res.compile && !res.compile.ok)) return api_.showCompile(res.compile, opts.source);
      api_.showRun(res.run, { ...opts, compile: res.compile });
    },
    get busy() { return busy; },
  };
  return api_;
}

function shortCompiler(path) { return String(path).split(/[\\/]/).pop(); }

// ------------------------------------------------------------------------------------------- narzędzia
export function debounce(fn, ms = 300) {
  let t = null;
  const d = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  d.flush = (...args) => { clearTimeout(t); fn(...args); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function timeAgo(date) {
  const d = typeof date === 'string' ? new Date(date.replace(' ', 'T')) : date;
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'przed chwilą';
  if (s < 3600) return `${Math.floor(s / 60)} min temu`;
  if (s < 86400) return `${Math.floor(s / 3600)} godz. temu`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} dni temu`;
  return d.toLocaleDateString('pl-PL');
}

export function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  if (n === 1) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
}

// Wizualizacje algorytmów krok po kroku (agent J). Generatory kroków: ./_algoviz/*.js (czysty JS), renderery: ./_algoviz/render.js.
import { h, icon, button, iconButton, viewHeader, input, textarea, select, badge, codeBlock, clear, debounce, toast, kbd, escapeHtml } from '../ui.js';
import { local } from '../api.js';
import { ALGOS, CATEGORIES, byId, defaultsFor, codeFor } from './_algoviz/registry.js';
import { RENDERERS } from './_algoviz/render.js';
import { rng } from './_algoviz/core.js';
import { mazeText, scatterText, emptyText } from './_algoviz/graphs.js';

const SPEEDS = [1500, 1050, 760, 560, 410, 300, 210, 140, 85, 45];   // ms na krok (suwak 1…10)
const SPEED_NAMES = ['bardzo wolno', 'wolno', 'wolno', 'spokojnie', 'normalnie', 'normalnie', 'szybko', 'szybko', 'bardzo szybko', 'turbo'];
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l');

function fmtDesc(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return s;
}

export default {
  id: 'algoviz',
  title: 'Wizualizacje algorytmów',
  icon: 'workflow',
  group: 'Narzędzia',
  order: 10,
  description: 'Animacje krok po kroku z kodem C++ i opisem: sortowania, wyszukiwanie binarne, BFS/DFS (także labirynt rysowany myszą), Dijkstra, Find & Union, drzewa przedziałowe, Fenwick, KMP, plecak, LIS/LCS, sito i rekurencja.',
  keywords: ['wizualizacja', 'animacja', 'algorytm', 'krok po kroku', 'sortowanie', 'bfs', 'dfs', 'dijkstra', 'dsu', 'find union', 'drzewo przedziałowe', 'fenwick', 'kmp', 'plecak', 'lis', 'lcs', 'sito', 'rekurencja', 'hanoi', 'labirynt', 'visualizer'],

  render(root, ctx) {
    const self = this;
    root.classList.add('av-view');
    const P = { algo: null, vals: {}, res: null, idx: 0, playing: false, timer: 0, speed: clampInt(local.get('algoviz:speed', 5), 1, 10), renderer: null, view: null, code: null, lines: [], curLine: null };
    this._P = P;

    // ------------------------------------------------------------------ szkielet
    const searchInp = input({ placeholder: 'Szukaj algorytmu…', size: 'sm', onInput: () => renderList() });
    const listEl = h('div', { class: 'av-list' });
    const side = h('aside', { class: 'av-side' }, h('div', { class: 'av-search' }, icon('search', 14), searchInp), listEl);
    const mobileSel = select(CATEGORIES.flatMap((c) => ALGOS.filter((a) => a.cat === c.id).map((a) => ({ value: a.id, label: `${c.id}: ${a.name}` }))), { size: 'sm', onChange: (v) => pick(v) });
    mobileSel.classList.add('av-mobile-select');

    const titleEl = h('h2', { class: 'av-title' });
    const badgesEl = h('div', { class: 'row gap-s' });
    const aboutEl = h('p', { class: 'av-about' });
    const readBtn = button('Czytaj artykuł', { icon: 'book-open', size: 'sm', onClick: () => openArticle() });
    const inputsEl = h('div', { class: 'av-inputs' });
    const toolsEl = h('div', { class: 'av-tools' });
    const errorEl = h('div', { class: 'av-error', hidden: true });
    const head = h('div', { class: 'card av-headcard' },
      h('div', { class: 'av-headrow' }, h('div', { class: 'grow' }, h('div', { class: 'row gap-s av-titlerow' }, titleEl, badgesEl), aboutEl), readBtn),
      inputsEl, toolsEl, errorEl);

    const stageHost = h('div', { class: 'av-stage' });
    const stepNo = h('span', { class: 'av-stepno' });
    const lineBadge = h('span', { class: 'av-linebadge' });
    const descEl = h('div', { class: 'av-desc', 'aria-live': 'polite' });
    const varsEl = h('div', { class: 'av-vars' });
    const panelsEl = h('div', { class: 'av-panels' });

    const btnFirst = iconButton('skip-back', { title: 'Na początek (Home)', onClick: () => { pause(); show(0); } });
    const btnPrev = iconButton('chevron-left', { title: 'Krok wstecz (←)', onClick: () => { pause(); show(P.idx - 1); } });
    const btnPlay = h('button', { class: 'av-play', type: 'button', title: 'Odtwarzaj / pauza (spacja)', onclick: () => toggle() }, icon('play', 20));
    const btnNext = iconButton('chevron-right', { title: 'Krok naprzód (→)', onClick: () => { pause(); show(P.idx + 1); } });
    const btnLast = iconButton('skip-forward', { title: 'Na koniec (End)', onClick: () => { pause(); show(Infinity); } });
    const slider = h('input', { type: 'range', class: 'av-slider', min: 0, max: 0, value: 0, 'aria-label': 'Krok' });
    slider.addEventListener('input', () => { pause(); show(+slider.value, true); });
    const counter = h('span', { class: 'av-counter' });
    const speedLabel = h('span', { class: 'av-speedlabel' });
    const speed = h('input', { type: 'range', class: 'av-speed', min: 1, max: 10, value: P.speed, 'aria-label': 'Prędkość' });
    speed.addEventListener('input', () => { P.speed = +speed.value; local.set('algoviz:speed', P.speed); speedLabel.textContent = SPEED_NAMES[P.speed - 1]; if (P.playing) schedule(); });
    speedLabel.textContent = SPEED_NAMES[P.speed - 1];
    const controls = h('div', { class: 'av-controls' },
      h('div', { class: 'av-transport' }, btnFirst, btnPrev, btnPlay, btnNext, btnLast),
      h('div', { class: 'av-scrub' }, slider, counter),
      h('div', { class: 'av-speedbox', title: 'Prędkość odtwarzania' }, icon('gauge', 15), speed, speedLabel));
    const progress = h('div', { class: 'av-progress' }, h('div', { class: 'av-progress-fill' }));

    const stageCard = h('div', { class: 'card av-stagecard' },
      h('div', { class: 'av-stagebox' }, stageHost),
      progress,
      h('div', { class: 'av-stepbar' }, h('div', { class: 'row gap-s av-stepmeta' }, stepNo, lineBadge), descEl),
      varsEl, panelsEl, controls);

    const codeHost = h('div', { class: 'av-code' });
    const legendEl = h('div', { class: 'av-legend' });
    const codeCol = h('section', { class: 'av-codecol' },
      h('div', { class: 'card av-codecard' }, h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('code-xml', 17)), h('div', { class: 'grow' }, h('div', { class: 'card-title' }, 'Kod C++'), h('div', { class: 'card-sub' }, 'podświetlona linia = bieżący krok'))), codeHost),
      h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', { class: 'card-icon' }, icon('paintbrush', 17)), h('div', { class: 'card-title' }, 'Legenda')), h('div', { class: 'card-body' }, legendEl)),
      h('div', { class: 'card av-keys' }, h('div', { class: 'card-body' },
        h('div', { class: 'row gap-s' }, kbd('←'), kbd('→'), h('span', { class: 'muted small' }, 'krok'), kbd('Spacja'), h('span', { class: 'muted small' }, 'odtwarzaj'), kbd('Home'), kbd('End')))));

    root.append(
      viewHeader(this, mobileSel),
      h('div', { class: 'av-root' }, side, h('section', { class: 'av-main' }, head, stageCard), codeCol));

    // ------------------------------------------------------------------ lista algorytmów
    function renderList() {
      clear(listEl);
      const q = norm(searchInp.value.trim());
      for (const c of CATEGORIES) {
        const items = ALGOS.filter((a) => a.cat === c.id && (!q || norm(`${a.name} ${a.about} ${a.id} ${a.complexity}`).includes(q)));
        if (!items.length) continue;
        listEl.append(h('div', { class: 'av-cat' }, icon(c.icon, 13), c.id));
        for (const a of items) {
          listEl.append(h('button', { class: ['av-item-btn', P.algo && P.algo.id === a.id ? 'active' : ''].join(' '), type: 'button', title: a.about, onclick: () => pick(a.id) },
            h('span', { class: 'nm' }, a.name), h('span', { class: 'cx' }, a.complexity)));
        }
      }
      if (!listEl.childElementCount) listEl.append(h('div', { class: 'muted small pad' }, 'Nic nie pasuje.'));
    }

    // ------------------------------------------------------------------ wybór algorytmu i dane
    const applySoon = debounce(() => apply(), 500);
    let fields = {};
    function pick(id, extra = {}) {
      const algo = byId.get(id) || ALGOS[0];
      pause();
      P.algo = algo;
      local.set('algoviz:last', algo.id);
      mobileSel.value = algo.id;
      const saved = local.get('algoviz:vals:' + algo.id, null);
      P.vals = { ...defaultsFor(algo), ...(saved || {}), ...extra };
      titleEl.textContent = algo.name;
      clear(badgesEl);
      badgesEl.append(badge(algo.cat, 'accent'), badge(algo.complexity, 'info', 'timer'));
      aboutEl.textContent = algo.about;
      readBtn.hidden = !algo.kb;
      renderInputs();
      renderLegend();
      renderList();
      if (P.view !== algo.view || !P.renderer) {
        P.renderer?.destroy();
        clear(stageHost);
        stageHost.dataset.view = algo.view;
        P.renderer = RENDERERS[algo.view](stageHost, { onGridEdit: (text) => { P.vals.grid = text; apply({ keepPlaying: P.playing }); } });
        P.view = algo.view;
      }
      apply();
    }

    function renderInputs() {
      clear(inputsEl); clear(toolsEl);
      fields = {};
      const algo = P.algo;
      for (const inp of algo.inputs) {
        if (inp.kind === 'grid') continue;
        let ctl;
        if (inp.kind === 'area') {
          ctl = textarea({ value: P.vals[inp.key] ?? '', rows: inp.rows || 4, onInput: (v) => { P.vals[inp.key] = v; applySoon(); } });
          ctl.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); applySoon.flush(); } });
        } else if (inp.kind === 'select') {
          ctl = select(inp.options, { value: P.vals[inp.key], size: 'sm', onChange: (v) => { P.vals[inp.key] = v; apply(); } });
        } else {
          ctl = input({ value: P.vals[inp.key] ?? '', placeholder: inp.ph || '', mono: true, size: 'sm', onInput: (v) => { P.vals[inp.key] = v; applySoon(); }, onEnter: () => applySoon.flush() });
        }
        fields[inp.key] = ctl;
        inputsEl.append(h('label', { class: ['av-field', inp.kind === 'area' ? 'wide' : '', inp.kind === 'select' ? 'narrow' : ''].join(' ') },
          h('span', { class: 'lbl' }, inp.label), ctl, inp.hint ? h('span', { class: 'hint' }, inp.hint) : null));
      }
      const acts = h('div', { class: 'av-inputacts' },
        button('Losuj', { icon: 'shuffle', size: 'sm', onClick: () => randomize(), title: 'Losowe dane wejściowe' }),
        button('Domyślne', { icon: 'rotate-ccw', size: 'sm', variant: 'ghost', onClick: () => { P.vals = defaultsFor(P.algo); local.remove('algoviz:vals:' + P.algo.id); renderInputs(); apply(); } }));
      if (algo.gridTools) {
        const r = () => rng((Date.now() ^ (Math.random() * 1e9)) >>> 0);
        toolsEl.append(h('span', { class: 'muted small' }, icon('pencil', 13), ' Plansza:'),
          button('Labirynt', { icon: 'grid-3x3', size: 'sm', onClick: () => setGrid(mazeText(r(), 13, 25, 0.12)) }),
          button('Losowe ściany', { icon: 'shuffle', size: 'sm', onClick: () => setGrid(scatterText(r(), 13, 25, 0.27)) }),
          button('Pusta', { icon: 'square-function', size: 'sm', variant: 'ghost', onClick: () => setGrid(emptyText(13, 25)) }),
          h('span', { class: 'muted small grow right' }, 'Rysuj ściany myszą · przeciągnij S i T'));
        inputsEl.append(acts);
      } else inputsEl.append(acts);
    }
    function setGrid(text) { P.vals.grid = text; apply(); }

    function randomize() {
      const r = rng((Date.now() ^ (Math.random() * 1e9)) >>> 0);
      P.vals = { ...P.vals, ...P.algo.random(r) };
      for (const [k, ctl] of Object.entries(fields)) ctl.value = P.vals[k] ?? '';
      apply();
    }

    function renderLegend() {
      clear(legendEl);
      for (const [tone, label] of P.algo.legend || []) legendEl.append(h('span', { class: 'av-legitem' }, h('span', { class: `av-sw t-${tone}` }), label));
    }

    function apply({ keepPlaying = false } = {}) {
      const algo = P.algo;
      let res;
      try {
        res = algo.run(P.vals);
      } catch (e) {
        errorEl.hidden = false;
        clear(errorEl);
        errorEl.append(icon('triangle-alert', 15), h('span', null, e.message || String(e)));
        if (!(e && e.constructor && e.constructor.name === 'InputError')) console.error(e);
        return;
      }
      errorEl.hidden = true;
      local.set('algoviz:vals:' + algo.id, P.vals);
      const wasPlaying = keepPlaying && P.playing;
      pause();
      P.res = res;
      setCode(codeFor(algo, P.vals));
      slider.max = res.steps.length - 1;
      show(0, true);
      if (wasPlaying) play();
    }

    function setCode(code) {
      if (P.code === code) return;
      P.code = code;
      clear(codeHost);
      const cb = codeBlock(code.code, 'cpp', { lineNumbers: true, copy: true, title: 'C++', maxHeight: 560 });
      codeHost.append(cb);
      P.pre = cb.querySelector('pre');
      P.lines = [...P.pre.children].filter((x) => x.tagName === 'SPAN');
      P.curLine = null;
    }

    // ------------------------------------------------------------------ odtwarzanie
    const animDur = () => Math.round(Math.min(480, SPEEDS[P.speed - 1] * 0.82));
    function show(i, instant = false) {
      if (!P.res) return;
      const steps = P.res.steps;
      i = Math.max(0, Math.min(steps.length - 1, i === Infinity ? steps.length - 1 : i));
      const prev = P.idx;
      P.idx = i;
      const st = steps[i];
      const dur = instant || Math.abs(i - prev) > 1 ? Math.min(160, animDur()) : animDur();
      stageHost.style.setProperty('--av-dur', dur + 'ms');
      try { P.renderer.update(st, P.res.data, { dur }); } catch (e) { console.error(e); }
      stepNo.textContent = `Krok ${i + 1} / ${steps.length}`;
      lineBadge.textContent = st.line ? `linia ${st.line}` : '';
      lineBadge.hidden = !st.line;
      descEl.innerHTML = fmtDesc(st.desc);
      descEl.classList.remove('flash'); void descEl.offsetWidth; descEl.classList.add('flash');
      counter.textContent = `${i + 1}/${steps.length}`;
      slider.value = i;
      slider.style.setProperty('--fill', steps.length > 1 ? `${(100 * i) / (steps.length - 1)}%` : '100%');
      progress.firstChild.style.width = steps.length > 1 ? `${(100 * i) / (steps.length - 1)}%` : '100%';
      btnPrev.disabled = btnFirst.disabled = i === 0;
      btnNext.disabled = btnLast.disabled = i === steps.length - 1;
      // zmienne
      clear(varsEl);
      for (const [k, v] of Object.entries(st.vars || {})) {
        if (v === undefined || v === null) continue;
        varsEl.append(h('span', { class: ['av-var', k === 'wynik' ? 'result' : ''].join(' ') }, h('span', { class: 'k' }, k), h('span', { class: 'v' }, String(v))));
      }
      varsEl.hidden = !varsEl.childElementCount;
      // panele (kolejka, stos, PQ…)
      clear(panelsEl);
      for (const p of st.panels || []) {
        panelsEl.append(h('div', { class: 'av-panel' }, h('div', { class: 'av-ptitle' }, p.title),
          h('div', { class: 'av-pitems' }, p.items.length ? p.items.map((it) => h('span', { class: ['av-chip', it.t ? 't-' + it.t : ''].join(' ') }, it.v, it.sub ? h('small', null, it.sub) : null)) : h('span', { class: 'faint small' }, 'pusto'))));
      }
      panelsEl.hidden = !panelsEl.childElementCount;
      highlightLine(st.line);
    }
    function highlightLine(n) {
      if (P.curLine) P.curLine.classList.remove('hl-line', 'av-curline');
      const el = n ? P.lines[n - 1] : null;
      P.curLine = el;
      if (!el) return;
      el.classList.add('hl-line', 'av-curline');
      const pre = P.pre;
      const top = el.offsetTop, bottom = top + el.offsetHeight;
      if (top < pre.scrollTop + 24 || bottom > pre.scrollTop + pre.clientHeight - 24) pre.scrollTo({ top: Math.max(0, top - pre.clientHeight / 2), behavior: 'smooth' });
    }
    function schedule() {
      clearTimeout(P.timer);
      if (!P.playing) return;
      P.timer = setTimeout(() => {
        if (!P.playing) return;
        if (P.idx >= P.res.steps.length - 1) { pause(); return; }
        show(P.idx + 1);
        if (P.idx >= P.res.steps.length - 1) pause(); else schedule();
      }, SPEEDS[P.speed - 1]);
    }
    function play() {
      if (!P.res) return;
      if (P.idx >= P.res.steps.length - 1) show(0, true);
      P.playing = true;
      btnPlay.replaceChildren(icon('pause', 20));
      btnPlay.classList.add('playing');
      schedule();
    }
    function pause() {
      P.playing = false;
      clearTimeout(P.timer);
      btnPlay.replaceChildren(icon('play', 20));
      btnPlay.classList.remove('playing');
    }
    function toggle() { if (P.playing) pause(); else play(); }
    this._pause = pause;

    function openArticle() {
      const id = P.algo && P.algo.kb;
      if (!id) return;
      if (window.OIF && window.OIF.views && !window.OIF.views.has('docs')) { toast('Widok dokumentacji nie jest jeszcze dostępny.', 'warn'); return; }
      ctx.navigate('docs', { id });
    }

    // ------------------------------------------------------------------ klawiatura i rozmiar
    document.addEventListener('keydown', (e) => {
      if (root.hidden || !root.isConnected || e.ctrlKey || e.metaKey || e.altKey) return;
      if (document.querySelector('.backdrop')) return;
      const t = e.target;
      if (t && (t.closest?.('input, textarea, select, [contenteditable], .CodeMirror'))) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); pause(); show(P.idx + 1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); pause(); show(P.idx - 1); }
      else if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault();
        if (e.repeat) return;
        // spacja na przycisku wywołałaby też jego „click” przy puszczeniu klawisza → podwójne przełączenie
        if (t && t.tagName === 'BUTTON') t.addEventListener('keyup', (ev) => ev.preventDefault(), { once: true });
        toggle();
      }
      else if (e.key === 'Home') { e.preventDefault(); pause(); show(0); }
      else if (e.key === 'End') { e.preventDefault(); pause(); show(Infinity); }
    });
    if (typeof ResizeObserver !== 'undefined') {
      let lastW = 0;
      const onResize = debounce(() => {
        const w = stageHost.clientWidth;
        if (!w || Math.abs(w - lastW) < 8) return;
        lastW = w;
        P.renderer?.rebuild?.();
        P.renderer?.resize?.();
        show(P.idx, true);
      }, 140);
      new ResizeObserver(onResize).observe(stageHost);
    }

    renderList();
    const start = (ctx.params && ctx.params.algo && byId.has(ctx.params.algo)) ? ctx.params.algo : local.get('algoviz:last', 'bubble');
    pick(byId.has(start) ? start : 'bubble');
    self._pick = pick;
  },

  onShow(params) {
    if (params && params.algo && this._pick && byId.has(params.algo)) {
      const extra = {};
      const algo = byId.get(params.algo);
      for (const inp of algo.inputs) if (params[inp.key] !== undefined) extra[inp.key] = String(params[inp.key]);
      this._pick(params.algo, extra);
    }
  },
  onHide() { this._pause && this._pause(); },
};

function clampInt(v, a, b) { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.max(a, Math.min(b, n)) : a; }

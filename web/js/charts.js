// Proste wykresy SVG w stylu aplikacji (bez zależności). Kolory przez zmienne CSS (motyw jasny/ciemny).
const NS = 'http://www.w3.org/2000/svg';
const PALETTE = ['var(--accent-2)', 'var(--info)', 'var(--success)', 'var(--warn)', 'var(--danger)', 'var(--tok-pp)', 'var(--tok-builtin)'];

function el(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  for (const k of kids) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}

function niceNum(x) {
  const ax = Math.abs(x);
  if (ax === 0) return '0';
  if (ax >= 1e6 || ax < 1e-3) return x.toExponential(1).replace('e+', 'e');
  if (ax >= 1000) return (x / 1000).toFixed(ax >= 1e4 ? 0 : 1) + 'k';
  if (ax >= 10) return x.toFixed(0);
  if (ax >= 1) return x.toFixed(1).replace(/\.0$/, '');
  return x.toPrecision(2);
}

function ticks(min, max, n = 5, log = false) {
  if (log) {
    const out = [];
    for (let p = Math.floor(Math.log10(min)); p <= Math.ceil(Math.log10(max)); p++) out.push(10 ** p);
    return out.filter((t) => t >= min * 0.999 && t <= max * 1.001);
  }
  if (max === min) return [min];
  const step0 = (max - min) / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => (max - min) / s <= n) || step0;
  const out = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) out.push(+t.toFixed(10));
  return out;
}

/**
 * lineChart(series, opts) — series: [{name, points: [[x, y], ...], color?, dashed?}]
 * opts: {xLabel, yLabel, logX, logY, height, yFormat, xFormat}
 */
export function lineChart(series, { xLabel = '', yLabel = '', logX = false, logY = false, height = 260, width = 640, yFormat = niceNum, xFormat = niceNum } = {}) {
  const pad = { l: 54, r: 16, t: 14, b: 40 };
  const pts = series.flatMap((s) => s.points).filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]) && (!logX || p[0] > 0) && (!logY || p[1] > 0));
  const svg = el('svg', { class: 'chart', viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet', role: 'img' });
  if (!pts.length) {
    svg.append(el('text', { x: width / 2, y: height / 2, 'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': 13 }, 'Brak danych'));
    return svg;
  }
  let [x0, x1] = [Math.min(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[0]))];
  let [y0, y1] = [Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1]))];
  if (!logY) { y0 = Math.min(0, y0); y1 = y1 === y0 ? y0 + 1 : y1 * 1.08; }
  if (x1 === x0) x1 = x0 + 1;
  const fx = logX ? (x) => Math.log10(x) : (x) => x;
  const fy = logY ? (y) => Math.log10(y) : (y) => y;
  const sx = (x) => pad.l + ((fx(x) - fx(x0)) / (fx(x1) - fx(x0) || 1)) * (width - pad.l - pad.r);
  const sy = (y) => height - pad.b - ((fy(y) - fy(y0)) / (fy(y1) - fy(y0) || 1)) * (height - pad.t - pad.b);
  const grid = el('g', { class: 'grid' });
  const axis = el('g', { class: 'axis' });
  for (const t of ticks(y0, y1, 5, logY)) {
    grid.append(el('line', { x1: pad.l, x2: width - pad.r, y1: sy(t), y2: sy(t) }));
    axis.append(el('text', { x: pad.l - 8, y: sy(t) + 4, 'text-anchor': 'end' }, yFormat(t)));
  }
  for (const t of ticks(x0, x1, 6, logX)) {
    axis.append(el('text', { x: sx(t), y: height - pad.b + 18, 'text-anchor': 'middle' }, xFormat(t)));
  }
  axis.append(el('line', { x1: pad.l, x2: width - pad.r, y1: height - pad.b, y2: height - pad.b }));
  if (xLabel) axis.append(el('text', { x: (pad.l + width - pad.r) / 2, y: height - 4, 'text-anchor': 'middle' }, xLabel));
  if (yLabel) axis.append(el('text', { x: 12, y: pad.t + 8, 'text-anchor': 'start' }, yLabel));
  svg.append(grid, axis);
  const legend = el('g', { class: 'legend' });
  let lx = pad.l + 6;
  series.forEach((s, i) => {
    const color = s.color || PALETTE[i % PALETTE.length];
    const ps = s.points.filter((p) => Number.isFinite(p[1]) && (!logX || p[0] > 0) && (!logY || p[1] > 0)).sort((a, b) => a[0] - b[0]);
    if (!ps.length) return;
    const d = ps.map((p, k) => `${k ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(' ');
    const g = el('g', { class: 'series' }, el('path', { d, stroke: color, 'stroke-dasharray': s.dashed ? '6 5' : null }));
    if (!s.dashed) ps.forEach((p) => {
      const c = el('circle', { cx: sx(p[0]), cy: sy(p[1]), r: 3.2, fill: color, class: 'tooltip-dot' });
      c.append(el('title', {}, `${s.name}: ${xFormat(p[0])} → ${yFormat(p[1])}`));
      g.append(c);
    });
    svg.append(g);
    if (series.length > 1 || s.name) {
      legend.append(el('rect', { x: lx, y: pad.t, width: 10, height: 10, rx: 3, fill: color }), el('text', { x: lx + 15, y: pad.t + 9 }, s.name || ''));
      lx += 26 + (s.name || '').length * 6.6;
    }
  });
  svg.append(legend);
  return svg;
}

/** barChart(items, opts) — items: [{label, value, tone?, sub?}] ; opts: {height, horizontal, format, max} */
export function barChart(items, { height, horizontal = true, format = (v) => String(v), max } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'barchart';
  const m = max ?? Math.max(1, ...items.map((i) => i.value || 0));
  if (horizontal) {
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:7px';
    for (const it of items) {
      const row = document.createElement('div');
      row.style.cssText = 'display:grid;grid-template-columns:minmax(90px,32%) 1fr auto;align-items:center;gap:10px;font-size:12.5px';
      const lab = document.createElement('div'); lab.textContent = it.label; lab.title = it.label;
      lab.style.cssText = 'color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
      const track = document.createElement('div'); track.style.cssText = 'height:9px;border-radius:99px;background:var(--panel-3);overflow:hidden';
      const fill = document.createElement('div');
      const color = it.tone ? `var(--${it.tone})` : 'linear-gradient(90deg,var(--accent-3),var(--accent-2))';
      fill.style.cssText = `height:100%;width:0;border-radius:99px;background:${color};transition:width .5s cubic-bezier(.2,.8,.2,1)`;
      track.append(fill);
      const val = document.createElement('div'); val.textContent = format(it.value); val.style.cssText = 'color:var(--muted);font-variant-numeric:tabular-nums;min-width:28px;text-align:right';
      row.append(lab, track, val);
      wrap.append(row);
      requestAnimationFrame(() => { fill.style.width = `${(100 * (it.value || 0)) / m}%`; });
    }
    return wrap;
  }
  const H = height || 160;
  wrap.style.cssText = `display:flex;align-items:flex-end;gap:6px;height:${H}px;padding-top:6px`;
  for (const it of items) {
    const col = document.createElement('div');
    col.style.cssText = 'flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:0;height:100%;justify-content:flex-end';
    const bar = document.createElement('div');
    const color = it.tone ? `var(--${it.tone})` : 'linear-gradient(180deg,var(--accent-2),var(--accent-3))';
    bar.style.cssText = `width:100%;max-width:38px;height:0;border-radius:6px 6px 3px 3px;background:${color};transition:height .5s cubic-bezier(.2,.8,.2,1)`;
    bar.title = `${it.label}: ${format(it.value)}`;
    const lab = document.createElement('div'); lab.textContent = it.label; lab.style.cssText = 'font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%';
    col.append(bar, lab);
    wrap.append(col);
    requestAnimationFrame(() => { bar.style.height = `${Math.max(2, ((H - 24) * (it.value || 0)) / m)}px`; });
  }
  return wrap;
}

/** Mapa aktywności w stylu GitHuba: days = {"2026-01-05": count, ...} */
export function heatmap(days, { weeks = 53, cell = 11, gap = 3 } = {}) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const start = new Date(today); start.setDate(start.getDate() - (weeks * 7 - 1) - ((today.getDay() + 6) % 7 === 6 ? 0 : 0));
  const maxV = Math.max(1, ...Object.values(days));
  const W = weeks * (cell + gap), H = 7 * (cell + gap) + 16;
  const svg = el('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMinYMin meet' });
  for (let i = 0; i < weeks * 7; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    if (d > today) break;
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;  // data lokalna, nie UTC
    const v = days[key] || 0;
    const wk = Math.floor(i / 7), dow = (d.getDay() + 6) % 7;
    const op = v ? 0.25 + 0.75 * (v / maxV) : 1;
    const r = el('rect', { x: wk * (cell + gap), y: dow * (cell + gap), width: cell, height: cell, rx: 2.5,
      fill: v ? 'var(--accent)' : 'var(--panel-3)', 'fill-opacity': op });
    r.append(el('title', {}, `${key}: ${v}`));
    svg.append(r);
  }
  return svg;
}

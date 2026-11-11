// Lekki renderer markdown → DOM (bezpieczny: wszystko escapowane, bez innerHTML z danych użytkownika poza escapowanym).
// Obsługa: nagłówki, akapity, listy (zagnieżdżone wcięciem), ```kod```, `inline`, **bold**, *italic*, ~~skreślenie~~,
// [link](url), [[kb:id|tytuł]] / (kb:id) jako linki do dokumentacji, tabele |a|b|, > cytaty, --- linia, > [!TIP] callouty.

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function inline(text) {
  // 1) wydziel `kod`, żeby nie formatować w środku
  const codes = [];
  let s = String(text ?? '').replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = escapeHtml(s);
  s = s.replace(/\[\[kb:([a-z0-9-]+)(?:\|([^\]]+))?\]\]/g, (_, id, t) => `<a class="kb-link" href="#/docs" data-kb="${id}">${t || id}</a>`);
  s = s.replace(/\[([^\]]+)\]\((kb:)([a-z0-9-]+)\)/g, (_, t, _k, id) => `<a class="kb-link" href="#/docs" data-kb="${id}">${t}</a>`);
  s = s.replace(/\[([^\]]+)\]\((#\/[^)\s]*)\)/g, (_, t, u) => `<a href="${u}">${t}</a>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  s = s.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__([^_]+?)__/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${escapeHtml(codes[+i])}</code>`);
  return s;
}

function splitRow(line) {
  let t = line.trim();
  if (t.startsWith('|')) t = t.slice(1);
  if (t.endsWith('|')) t = t.slice(0, -1);
  // pozwól na \| wewnątrz komórek
  return t.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

/**
 * markdownToDom(md, {codeBlock}) — codeBlock(code, lang) → Node (wstrzykiwane z ui.js, żeby uniknąć cyklu importów).
 */
export function markdownToDom(md, opts = {}) {
  const root = document.createElement('div');
  root.className = 'md';
  const lines = String(md ?? '').replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  const html = [];
  const blocks = [];  // [placeholderIndex, node]

  const flushPara = (buf) => { if (buf.length) html.push(`<p>${inline(buf.join(' '))}</p>`); buf.length = 0; };
  const para = [];

  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(```|~~~)\s*([\w+#-]*)\s*$/);
    if (fence) {
      flushPara(para);
      const lang = fence[2] || 'cpp';
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) { buf.push(lines[i]); i++; }
      i++;
      const ph = `<div data-ph="${blocks.length}"></div>`;
      blocks.push([buf.join('\n'), lang]);
      html.push(ph);
      continue;
    }
    if (/^\s*$/.test(line)) { flushPara(para); i++; continue; }
    const hm = line.match(/^(#{1,4})\s+(.*)$/);
    if (hm) { flushPara(para); html.push(`<h${hm[1].length}>${inline(hm[2])}</h${hm[1].length}>`); i++; continue; }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { flushPara(para); html.push('<hr>'); i++; continue; }
    // tabela
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      flushPara(para);
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { rows.push(splitRow(lines[i])); i++; }
      html.push('<table><thead><tr>' + head.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + head.map((_, k) => `<td>${inline(r[k] ?? '')}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
      continue;
    }
    // cytat / callout
    if (/^\s*>/.test(line)) {
      flushPara(para);
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
      const m = buf[0] && buf[0].match(/^\[!(TIP|WARN|WARNING|INFO|NOTE|DANGER)\]\s*(.*)$/i);
      if (m) {
        const kind = { TIP: 'tip', WARN: 'warn', WARNING: 'warn', INFO: 'info', NOTE: 'info', DANGER: 'danger' }[m[1].toUpperCase()];
        const rest = [m[2], ...buf.slice(1)].filter(Boolean).join('\n');
        html.push(`<div class="callout ${kind}" data-callout="${kind}"><div class="c-body">${inline(rest).replace(/\n/g, '<br>')}</div></div>`);
      } else {
        html.push(`<blockquote>${buf.map((b) => inline(b)).join('<br>')}</blockquote>`);
      }
      continue;
    }
    // listy (z prostym zagnieżdżaniem)
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      flushPara(para);
      const stack = [];  // [{indent, type}]
      let out = '';
      while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && stack.length))) {
        const l = lines[i];
        const m = l.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (!m) { out = out.replace(/<\/li>$/, '') + ' ' + inline(l.trim()) + '</li>'; i++; continue; }
        const indent = m[1].length;
        const type = /\d/.test(m[2]) ? 'ol' : 'ul';
        while (stack.length && indent < stack[stack.length - 1].indent) { out += `</${stack.pop().type}>`; }
        if (!stack.length || indent > stack[stack.length - 1].indent) { stack.push({ indent, type }); out += `<${type}>`; }
        out += `<li>${inline(m[3])}</li>`;
        i++;
      }
      while (stack.length) out += `</${stack.pop().type}>`;
      html.push(out);
      continue;
    }
    para.push(line.trim());
    i++;
  }
  flushPara(para);
  root.innerHTML = html.join('\n');
  // bloki kodu
  root.querySelectorAll('[data-ph]').forEach((el) => {
    const [code, lang] = blocks[+el.dataset.ph];
    const node = opts.codeBlock ? opts.codeBlock(code, lang) : Object.assign(document.createElement('pre'), { textContent: code });
    el.replaceWith(node);
  });
  // ikony calloutów
  if (opts.icon) {
    root.querySelectorAll('.callout').forEach((c) => {
      const name = { tip: 'lightbulb', warn: 'triangle-alert', info: 'info', danger: 'circle-alert' }[c.dataset.callout] || 'info';
      const ic = document.createElement('div'); ic.className = 'c-icon'; ic.append(opts.icon(name, 17)); c.prepend(ic);
    });
  }
  // linki do KB
  root.querySelectorAll('a[data-kb]').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent('oif:navigate', { detail: { id: 'docs', params: { id: a.dataset.kb } } }));
  }));
  return root;
}

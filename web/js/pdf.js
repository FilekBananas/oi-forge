// Ekstrakcja tekstu z PDF w przeglądarce (pdf.js z web/vendor/pdfjs) — działa offline na każdym systemie.
let pdfjsPromise = null;

function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import('/vendor/pdfjs/pdf.min.mjs').then((lib) => {
      lib.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.min.mjs';
      return lib;
    });
  }
  return pdfjsPromise;
}

/**
 * extractPdfText(fileOrArrayBuffer) -> Promise<string>
 * Łączy fragmenty w linie po współrzędnej Y. Indeksy górne (wykładniki, np. 10^{1 000 000} w treściach OI)
 * zapisuje jako „10^1000000” w tej samej linii — inaczej ograniczenie „n ≤ 10^6” rozpadłoby się na „10” i „6”.
 * Akapity rozdziela jedną pustą linią (większy odstęp w pionie), a nie pustą linią po każdej linii PDF-a.
 */
export async function extractPdfText(src) {
  const lib = await loadPdfjs();
  let data = src;
  if (src instanceof Blob) data = await src.arrayBuffer();
  if (data instanceof ArrayBuffer) data = new Uint8Array(data);
  const doc = await lib.getDocument({ data, verbosity: 0 }).promise;
  const out = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const tc = await page.getTextContent();
    let line = '';
    let baseY = null;      // linia bazowa bieżącej linii
    let baseH = 0;         // wysokość czcionki bieżącej linii
    let prevLineY = null;  // linia bazowa poprzedniej linii (do wykrywania akapitów)
    let lineGap = null;    // typowy odstęp między liniami
    const flush = () => {
      if (line.trim()) {
        if (prevLineY !== null && baseY !== null) {
          const gap = Math.abs(prevLineY - baseY);
          if (lineGap === null || gap < lineGap) lineGap = gap;
          if (lineGap && gap > lineGap * 1.45) out.push('');
        }
        out.push(line);
        prevLineY = baseY;
      }
      line = '';
    };
    for (const it of tc.items) {
      const y = it.transform ? it.transform[5] : null;
      const hgt = it.height || (it.transform ? Math.abs(it.transform[3]) : 0) || 0;
      if (y !== null && baseY !== null && Math.abs(y - baseY) > 2) {
        const dy = y - baseY;
        const small = hgt > 0 && baseH > 0 && hgt < baseH * 0.9;
        if (small && Math.abs(dy) < baseH * 0.8 && it.str.trim() && line.trim()) {
          // indeks górny (wykładnik) albo dolny — doklejamy w tej samej linii
          const s = it.str.trim().replace(/[\s  ]+(?=\d)/g, '');
          line += (dy > 0 ? '^' : '_') + (/^[\w]+$/.test(s) ? s : `{${s}}`);
          if (it.hasEOL) flush();
          continue;
        }
        flush();
      }
      if (!line) { baseY = y; baseH = hgt || baseH; }
      else if (hgt > baseH) baseH = hgt;
      line += it.str;
      if (it.hasEOL) flush();
    }
    flush();
    out.push('');
  }
  return out.join('\n').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n');
}

/** Wczytuje plik zadania: PDF → tekst przez pdf.js, HTML/TXT → tekst. */
export async function readStatementFile(file) {
  const name = (file.name || '').toLowerCase();
  if (name.endsWith('.pdf') || file.type === 'application/pdf') return extractPdfText(file);
  const text = await file.text();
  if (name.endsWith('.html') || name.endsWith('.htm') || /<html|<body|<div/i.test(text.slice(0, 500))) {
    const doc = new DOMParser().parseFromString(text, 'text/html');
    doc.querySelectorAll('script, style, nav, header, footer').forEach((e) => e.remove());
    return (doc.body?.innerText || doc.body?.textContent || '').replace(/\n{3,}/g, '\n\n');
  }
  return text;
}

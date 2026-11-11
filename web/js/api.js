// Komunikacja z backendem OI Forge.
const HEADERS = { 'Content-Type': 'application/json', 'X-OIForge': '1' };
let toastFn = null;
export function _setToast(fn) { toastFn = fn; }

export class ApiError extends Error {
  constructor(message, data, status) { super(message); this.data = data; this.status = status; }
}

async function request(method, url, body, opts = {}) {
  let full = url;
  if (method === 'GET' && body && Object.keys(body).length) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(body)) if (v !== undefined && v !== null) qs.set(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
    full += (url.includes('?') ? '&' : '?') + qs.toString();
  }
  let res;
  try {
    res = await fetch(full, {
      method, headers: method === 'GET' ? { 'X-OIForge': '1' } : HEADERS,
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}), signal: opts.signal,
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    const err = new ApiError('Brak połączenia z serwerem OI Forge. Czy program nadal działa w terminalu?', null, 0);
    if (!opts.quiet && toastFn) toastFn(err.message, 'error');
    throw err;
  }
  let data;
  try { data = await res.json(); } catch { data = { ok: false, error: `Niepoprawna odpowiedź serwera (HTTP ${res.status})` }; }
  if (!res.ok || data.ok === false) {
    const msg = data.error || `Błąd HTTP ${res.status}`;
    const err = new ApiError(msg, data, res.status);
    if (!opts.quiet && toastFn) toastFn(msg, 'error');
    throw err;
  }
  return data;
}

export const api = {
  get: (url, params, opts) => request('GET', url, params, opts),
  post: (url, body, opts) => request('POST', url, body, opts),

  /**
   * Uruchamia zadanie w tle (endpoint zwraca {job}) i odpytuje /api/jobs/<id>.
   * Zwraca {promise, cancel(), id: Promise<string>}.
   */
  job(url, body, { onEvent, onProgress, onStart, interval = 250, quiet = false } = {}) {
    let cancelled = false;
    let jobId = null;
    let resolveId;
    const idPromise = new Promise((r) => { resolveId = r; });
    const promise = (async () => {
      const start = await request('POST', url, body, { quiet });
      jobId = start.job;
      resolveId(jobId);
      if (!jobId) return start;  // endpoint zwrócił wynik synchronicznie
      onStart && onStart(jobId);
      let since = 0;
      let delay = Math.min(120, interval);
      for (;;) {
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(interval, delay * 1.5);
        let snap;
        try {
          snap = await request('GET', `/api/jobs/${jobId}`, { since }, { quiet: true });
        } catch (e) {
          if (e.status === 0) continue;   // chwilowy brak połączenia
          throw e;
        }
        since = snap.next;
        if (onEvent) for (const ev of snap.events || []) { try { onEvent(ev); } catch (err) { console.error(err); } }
        if (onProgress) onProgress(snap.progress, snap.message, snap);
        if (snap.status === 'done') return snap.result;
        if (snap.status === 'cancelled') { const e = new ApiError('Anulowano.', snap); e.cancelled = true; throw e; }
        if (snap.status === 'error') {
          const e = new ApiError(snap.error || 'Zadanie zakończyło się błędem.', snap);
          if (!quiet && toastFn) toastFn(e.message, 'error');
          throw e;
        }
        if (cancelled) { /* czekamy aż backend potwierdzi */ }
      }
    })();
    return {
      promise,
      id: idPromise,
      async cancel() {
        cancelled = true;
        const id = jobId || await idPromise;
        if (id) await request('POST', `/api/jobs/${id}/cancel`, {}, { quiet: true }).catch(() => {});
      },
    };
  },
};

export const store = {
  async get(key, def = null) {
    try { const r = await api.get('/api/store/get', { key }, { quiet: true }); return r.value ?? def; } catch { return def; }
  },
  async set(key, value) { await api.post('/api/store/set', { key, value }, { quiet: true }); },
};

let settingsCache = null;
const settingsListeners = new Set();
export async function settings(force = false) {
  if (!settingsCache || force) {
    try { settingsCache = (await api.get('/api/settings', null, { quiet: true })).settings; } catch { settingsCache = settingsCache || {}; }
  }
  return settingsCache;
}
export async function updateSettings(patch) {
  const r = await api.post('/api/settings', { patch });
  settingsCache = r.settings;
  settingsListeners.forEach((fn) => { try { fn(settingsCache, patch); } catch (e) { console.error(e); } });
  return settingsCache;
}
export function onSettings(fn) { settingsListeners.add(fn); return () => settingsListeners.delete(fn); }

/** Lokalny cache (localStorage) z bezpieczną obsługą błędów. */
export const local = {
  get(key, def = null) { try { const v = localStorage.getItem('oif:' + key); return v === null ? def : JSON.parse(v); } catch { return def; } },
  set(key, value) { try { localStorage.setItem('oif:' + key, JSON.stringify(value)); } catch { /* pełny/zablokowany */ } },
  remove(key) { try { localStorage.removeItem('oif:' + key); } catch { /* */ } },
};

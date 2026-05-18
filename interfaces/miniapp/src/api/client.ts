// In-memory API client for the Telegram Mini App.
// Token is kept in memory only — never written to localStorage.
// Sessions are ephemeral: the mini app re-authenticates on every open.

const REQUEST_TIMEOUT_MS = 30_000;

// Service-worker runtime caches that hold tenant-scoped responses.  These
// MUST be cleared on login/logout so a different account never sees the
// previous tenant's cached fleet/alert data.  Names match the
// `runtimeCaching[].cacheName` values in vite.config.ts.
const TENANT_CACHE_NAMES = [
  'driver-scorecard',
  'st-fleet',
  'st-alerts-count',
  'api-get',
];

async function clearTenantCaches(): Promise<void> {
  if (typeof caches === 'undefined') return;
  try {
    const names = await caches.keys();
    await Promise.all(
      names
        .filter(n => TENANT_CACHE_NAMES.some(p => n.includes(p)))
        .map(n => caches.delete(n)),
    );
  } catch {
    /* best-effort — never block login on cache eviction */
  }
}

let _token: string | null = null;

// Timestamp (ms since epoch) of the last response that actually reached the
// server (any HTTP status).  Updated in apiFetch so OfflineBanner can show
// "cached data as of HH:MM" when the device goes offline.
let _lastFetchAt: number | null = null;

export function getLastFetchAt(): number | null {
  return _lastFetchAt;
}

export function setToken(token: string): void {
  if (_token !== token) {
    // Auth identity changed — drop runtime SW caches so the new
    // session never serves the previous account's data.
    void clearTenantCaches();
  }
  _token = token;
}

export function clearToken(): void {
  _token = null;
  void clearTenantCaches();
}

type ApiFetchOpts = Omit<RequestInit, 'body'> & {
  body?: BodyInit | Record<string, unknown> | null;
  /** Per-call override for the abort timeout.  Default is
   *  ``REQUEST_TIMEOUT_MS`` (30s) which fits most endpoints; AI
   *  chat replies can take longer when the model is reasoning over
   *  fleet data, so callers there should pass ``timeoutMs: 90_000``. */
  timeoutMs?: number;
};

export async function apiFetch(path: string, opts: ApiFetchOpts = {}): Promise<Response> {
  const headers: Record<string, string> = {};

  if (opts.headers) {
    Object.assign(headers, opts.headers as Record<string, string>);
  }

  if (_token) {
    headers['Authorization'] = `Bearer ${_token}`;
  }

  let body = opts.body;
  if (
    body &&
    typeof body === 'object' &&
    !(body instanceof FormData) &&
    !(body instanceof Blob) &&
    !(body instanceof ArrayBuffer) &&
    !(body instanceof URLSearchParams) &&
    typeof body !== 'string'
  ) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }

  const controller = new AbortController();
  const limitMs = opts.timeoutMs ?? REQUEST_TIMEOUT_MS;
  const timeout = setTimeout(() => controller.abort(), limitMs);

  // Strip the custom opt before forwarding to fetch() so it doesn't
  // end up as a stray header / option the browser doesn't recognise.
  const { timeoutMs: _drop, ...fetchOpts } = opts;
  void _drop;
  try {
    const res = await fetch(path, {
      ...fetchOpts,
      headers,
      body: body as BodyInit,
      signal: controller.signal,
    });
    _lastFetchAt = Date.now();
    return res;
  } finally {
    clearTimeout(timeout);
  }
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

/** UI-friendly classification of a failed apiJSON/apiFetch call.
 *
 * Pages should branch on this kind so the user gets the right next step:
 *   - 'auth'     → 401/403 → JWT expired → re-launch from the bot
 *   - 'server'   → 5xx → backend / Samsara hiccup → retry
 *   - 'network'  → no response (offline, DNS, TLS, AbortError) → check connection
 *   - 'unknown'  → other 4xx (route gone, validation) → report to admin
 */
export type ApiErrorKind = 'auth' | 'server' | 'network' | 'unknown';

export interface ClassifiedError {
  kind: ApiErrorKind;
  message: string;
  /** Original status when known (HTTP code) — pages may want it for telemetry. */
  status?: number;
}

export function classifyError(e: unknown): ClassifiedError {
  if (e instanceof ApiError) {
    if (e.status === 401 || e.status === 403) {
      return {
        kind: 'auth',
        status: e.status,
        message: 'Your session has expired. Reopen this app from the Telegram bot to continue.',
      };
    }
    if (e.status >= 500) {
      return {
        kind: 'server',
        status: e.status,
        message: 'The server is having trouble. Try again in a moment.',
      };
    }
    return { kind: 'unknown', status: e.status, message: e.message || 'Request failed.' };
  }
  // TypeError / AbortError / generic fetch failures → no response reached us.
  return {
    kind: 'network',
    message: 'No connection. Check your network and try again.',
  };
}

export async function apiJSON<T = unknown>(path: string, opts: ApiFetchOpts = {}): Promise<T> {
  const res = await apiFetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err?.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
        ? detail.map((d: { msg?: string }) => d.msg ?? String(d)).join('; ')
        : res.statusText;
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}

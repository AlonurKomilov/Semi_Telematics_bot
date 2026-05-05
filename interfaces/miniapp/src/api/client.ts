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
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(path, {
      ...opts,
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

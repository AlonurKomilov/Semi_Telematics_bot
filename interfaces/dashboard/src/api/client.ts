const API_BASE = '/api';
const REQUEST_TIMEOUT_MS = 30_000; // 30 seconds
const AI_REQUEST_TIMEOUT_MS = 90_000; // 90 seconds — Gemini agent round-trips can take 30-60s

export function getToken(): string | null {
  return localStorage.getItem('jwt');
}

export function setToken(token: string): void {
  localStorage.setItem('jwt', token);
}

export function clearToken(): void {
  localStorage.removeItem('jwt');
}

type ApiFetchOpts = Omit<RequestInit, 'body'> & {
  body?: BodyInit | Record<string, unknown> | null;
};

export async function apiFetch(path: string, opts: ApiFetchOpts = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (opts.headers) {
    const h = opts.headers as Record<string, string>;
    Object.assign(headers, h);
  }
  if (token) headers['Authorization'] = `Bearer ${token}`;
  let body = opts.body;
  if (body && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof Blob) && !(body instanceof ArrayBuffer) && !(body instanceof URLSearchParams) && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...opts, headers, body: body as BodyInit, signal: controller.signal });
    if (res.status === 401) {
      clearToken();
      window.location.href = '/dashboard/';
      throw new Error('Unauthorized');
    }
    return res;
  } finally {
    clearTimeout(timeout);
  }
}

export async function apiJSON<T = unknown>(path: string, opts: ApiFetchOpts = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const res = await apiFetch(path, opts, timeoutMs);
  if (!res.ok) {
    const err: { detail?: unknown } = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err.detail;
    const msg = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg || String(d)).join('; ')
      : res.statusText;
    throw new Error(msg);
  }
  return res.json();
}

/** apiJSON with 90-second timeout for AI endpoints. */
export async function apiJSONAI<T = unknown>(path: string, opts: ApiFetchOpts = {}): Promise<T> {
  return apiJSON<T>(path, opts, AI_REQUEST_TIMEOUT_MS);
}

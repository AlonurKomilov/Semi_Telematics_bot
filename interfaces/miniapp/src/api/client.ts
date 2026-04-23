// In-memory API client for the Telegram Mini App.
// Token is kept in memory only — never written to localStorage.
// Sessions are ephemeral: the mini app re-authenticates on every open.

const REQUEST_TIMEOUT_MS = 30_000;

let _token: string | null = null;

export function setToken(token: string): void {
  _token = token;
}

export function clearToken(): void {
  _token = null;
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
    return await fetch(path, {
      ...opts,
      headers,
      body: body as BodyInit,
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
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
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

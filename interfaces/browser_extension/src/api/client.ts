/**
 * The extension's API client — the dashboard's, with the token in
 * ``chrome.storage.local`` instead of localStorage.
 *
 * Extension storage is isolated per extension: no web page and no other
 * extension can read it.  That, plus the token being SCOPED server-side
 * to the live map (aud=extension), is the security story — the panel
 * holds a key to a truck list, not to the account.
 */
const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'https://api.4truck.us';
const TOKEN_KEY = 'jwt';
const REQUEST_TIMEOUT_MS = 30_000;

export async function getToken(): Promise<string | null> {
  const got = await chrome.storage.local.get(TOKEN_KEY);
  const v = got[TOKEN_KEY];
  return typeof v === 'string' && v ? v : null;
}
export async function setToken(token: string): Promise<void> {
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
}
export async function clearToken(): Promise<void> {
  await chrome.storage.local.remove(TOKEN_KEY);
}

/** ``exp`` in ms, or null when the token is not a readable JWT. */
export function tokenExpiryMs(token: string): number | null {
  try {
    const [, payload] = token.split('.');
    const json = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof json.exp === 'number' ? json.exp * 1000 : null;
  } catch {
    return null;
  }
}

type ApiFetchOpts = Omit<RequestInit, 'body'> & {
  body?: BodyInit | Record<string, unknown> | null;
};

export class UnauthorizedError extends Error {
  constructor() { super('Unauthorized'); this.name = 'UnauthorizedError'; }
}

export async function apiFetch(path: string, opts: ApiFetchOpts = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string> | undefined) };
  const token = await getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  let body = opts.body;
  if (body && typeof body === 'object' && !(body instanceof FormData) && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    // ``credentials: 'omit'``: the extension has host permission for the
    // API, so the browser WOULD attach the person's .4truck.us dashboard
    // cookie.  The panel must speak with its own scoped token only —
    // never fall through to the full session behind it.  Load-bearing
    // twice: it also makes /auth/logout's Set-Cookie (clearing the
    // dashboard cookie) inert for the panel — the response cookie is
    // discarded, so Disconnect here never signs the dashboard out.
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts, headers, body: body as BodyInit, signal: controller.signal, credentials: 'omit',
    });
    if (res.status === 401) {
      await clearToken();
      throw new UnauthorizedError();
    }
    return res;
  } finally {
    clearTimeout(timer);
  }
}

export async function apiJSON<T>(path: string, opts: ApiFetchOpts = {}): Promise<T> {
  const res = await apiFetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = (err as { detail?: unknown }).detail;
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

/** Refresh when the token is inside its last quarter of life. */
export async function refreshIfNeeded(): Promise<void> {
  const token = await getToken();
  if (!token) return;
  const exp = tokenExpiryMs(token);
  if (exp === null) return;
  const iatGuess = exp - 8 * 3600_000;                 // short-session default
  const remaining = exp - Date.now();
  if (remaining > (exp - iatGuess) / 4) return;
  try {
    const out = await apiJSON<{ access_token: string }>('/auth/refresh', { method: 'POST' });
    await setToken(out.access_token);
  } catch {
    /* the next 401 will send the user to login */
  }
}

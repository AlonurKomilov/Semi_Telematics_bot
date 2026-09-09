// Thin API client for the operator dashboard.
//
// Auth model: Bearer token in localStorage.  The browser scopes
// localStorage per origin, so system.4truck.us cannot read tokens set
// on dash.4truck.us and vice-versa — that's the isolation guarantee
// we want for an operator tool that holds cross-account powers.
//
// The token itself is identical to the one the customer dashboard
// uses (issued by the same /api/auth/telegram* endpoints).  What
// gates the operator API is the server-side ``require_system_owner``
// dep: it checks the JWT's telegram_id against the SYSTEM_OWNER_IDS
// env allowlist.  A leaked customer token cannot access /system/*
// because the customer's Telegram id isn't in that list.

const TOKEN_KEY = '4truck_system_token';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore — localStorage may be disabled in private mode */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export interface ApiOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// A 401 means the SERVER says this token is dead — expired, or its jti
// revoked when the operator signed in somewhere else.  It must be
// thrown away here, because nothing downstream can.
//
// ``App`` gates on ``!!getToken()`` — token PRESENCE, not validity — so
// a revoked token left in localStorage reads as "signed in" forever.
// The console then renders normally while every request 401s, and each
// of the thirteen pages draws its own "Log out and back in" banner. The
// only exit was the Logout button, which is a strange thing to ask of a
// person who is not, by the server's account, logged in.
//
// Clearing plus a hard navigation, rather than a router push: the
// reload unmounts pages whose requests are still in flight, so their
// stale banners cannot race the redirect. Inside the Telegram mini app
// Login then re-runs ``system-telegram-init`` on mount and the operator
// is back where they were, without touching anything.
//
// 403 deliberately does NOT clear: that is the server saying "this
// token is genuine, you are just not an operator" — a real answer to
// keep on screen, and clearing it would loop through login forever.
let recovering = false;

function recoverFromDeadToken(): void {
  // Guard the stampede: the accounts page alone fires four parallel
  // requests, and they 401 together.
  if (recovering) return;
  recovering = true;
  clearToken();
  try {
    if (!window.location.pathname.startsWith('/login')) {
      window.location.replace('/login');
    }
  } catch {
    /* non-browser context (tests, SSR) — clearing is enough */
  }
}

export async function apiJSON<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`/api${path}`, {
    method: opts.method ?? 'GET',
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data?.detail) detail = data.detail;
    } catch {
      /* not JSON */
    }
    if (res.status === 401) recoverFromDeadToken();
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

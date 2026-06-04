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
    // 401 / 403 are the "you're not allowed here" signals.  The router
    // listens for ApiError 401 specifically to bounce to /login.
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

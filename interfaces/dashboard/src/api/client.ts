const API_BASE = '/api';
const REQUEST_TIMEOUT_MS = 30_000; // 30 seconds
const AI_REQUEST_TIMEOUT_MS = 90_000; // 90 seconds — Gemini agent round-trips can take 30-60s

const TOKEN_KEY = 'jwt';

export function getToken(): string | null {
  // Persistent (localStorage) takes priority; fall back to session-only storage
  return localStorage.getItem(TOKEN_KEY) ?? sessionStorage.getItem(TOKEN_KEY);
}

/** Store the token. Pass persistent=true to survive browser close (30-day); false for session-only. */
export function setToken(token: string, persistent = true): void {
  if (persistent) {
    localStorage.setItem(TOKEN_KEY, token);
    sessionStorage.removeItem(TOKEN_KEY);
  } else {
    sessionStorage.setItem(TOKEN_KEY, token);
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
}

/** Returns true if the current token is stored persistently (Remember me was checked). */
export function isTokenPersistent(): boolean {
  return localStorage.getItem(TOKEN_KEY) !== null;
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
  // Chain caller's signal (if any) with our timeout signal so either can abort the request.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const externalSignal = opts.signal as AbortSignal | undefined;
  let externalAbortHandler: (() => void) | null = null;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else {
      externalAbortHandler = () => controller.abort();
      externalSignal.addEventListener('abort', externalAbortHandler);
    }
  }
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
    if (externalSignal && externalAbortHandler) {
      externalSignal.removeEventListener('abort', externalAbortHandler);
    }
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

/** apiJSON with 90-second timeout for heavy compute endpoints (scorecards, reports). */
export async function apiJSONSlow<T = unknown>(path: string, opts: ApiFetchOpts = {}): Promise<T> {
  return apiJSON<T>(path, opts, AI_REQUEST_TIMEOUT_MS);
}

/** SSE event types from /ai/chat/stream */
export type StreamEvent =
  | { type: 'tool'; name: string; label: string }
  | { type: 'done'; reply: string; suggestions: string[]; usage: Record<string, number> | null; tool_results: unknown[] }
  | { type: 'error'; message: string };

/**
 * Stream a chat message to /ai/chat/stream.
 * Calls onEvent for each SSE event.  Returns when the stream ends.
 * Pass signal to allow cancellation.
 */
export async function apiStreamChat(
  message: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}/ai/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message }),
    signal,
  });

  if (res.status === 401) {
    clearToken();
    window.location.href = '/dashboard/';
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText })) as { detail?: string };
    throw new Error(err.detail || `HTTP ${res.status}`);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)) as StreamEvent);
        } catch { /* skip malformed */ }
      }
    }
  }
}

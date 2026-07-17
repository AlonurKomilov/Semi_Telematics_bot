// API base — same-origin by default.  nginx subdomain blocks proxy
// `/api/*` to the FastAPI backend, so dash.4truck.us/api/v1/foo hits
// the API without cross-origin CORS.  Override at build time via
// VITE_API_BASE if a deployment puts the API on a different host.
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';
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

// Module-level mirror of RoleViewContext.activeView so apiFetch (a
// free function with no React context access) can attach the
// ``X-View-As`` header on every request.  RoleViewContext calls
// ``setActiveViewForApi`` whenever the derived activeView changes;
// during the first render before that effect fires, the value is
// ``''`` and the header is omitted (backend falls back to the JWT
// role, identical to pre-strict-binding behavior).
let _activeViewForApi = '';

export function setActiveViewForApi(view: string): void {
  _activeViewForApi = view || '';
}

export async function apiFetch(path: string, opts: ApiFetchOpts = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (opts.headers) {
    const h = opts.headers as Record<string, string>;
    Object.assign(headers, h);
  }
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (_activeViewForApi) headers['X-View-As'] = _activeViewForApi;
  let body = opts.body;
  if (body && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof Blob) && !(body instanceof ArrayBuffer) && !(body instanceof URLSearchParams) && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  // Chain caller's signal (if any) with our timeout signal so either can abort the request.
  // ``abort()`` is called with an explicit reason so the surfaced
  // DOMException carries a useful message instead of the browser's
  // default "signal is aborted without reason" — that wording leaked
  // straight into user-facing error banners and looked broken even
  // though it was just a vanilla timeout.
  const controller = new AbortController();
  let didTimeout = false;
  const timeout = setTimeout(() => {
    didTimeout = true;
    controller.abort(new DOMException(
      `Request to ${path} timed out after ${Math.round(timeoutMs / 1000)}s. ` +
      `The server may be busy — please try again.`,
      'TimeoutError',
    ));
  }, timeoutMs);
  const externalSignal = opts.signal as AbortSignal | undefined;
  let externalAbortHandler: (() => void) | null = null;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason);
    else {
      externalAbortHandler = () => controller.abort(externalSignal.reason);
      externalSignal.addEventListener('abort', externalAbortHandler);
    }
  }
  try {
    // ``credentials: 'include'`` opts into sending the cross-subdomain
    // ``auth_token`` cookie set by the backend on .4truck.us.  Without
    // this, the browser would withhold the cookie on /api/* fetches
    // (per the cross-site fetch default), and a user logged in via
    // cookie at apex would silently fail every API call.  The Bearer
    // header is still set when localStorage has a token, so the Mini
    // App and direct integrations keep working unchanged.
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers,
      body: body as BodyInit,
      signal: controller.signal,
      credentials: 'include',
    });
    if (res.status === 401) {
      // Clear stale localStorage but DON'T navigate from here.  The
      // 401 simply means "no valid session" — App.tsx is the canonical
      // place to decide what happens next (bounce to apex login on a
      // persona subdomain, render Login on apex).  Forcing a
      // ``location.href = '/'`` from here used to race with that
      // decision and produced an extra flash of the wrong route on
      // every cross-host auth handoff.
      clearToken();
      throw new Error('Unauthorized');
    }
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      // Gateway-level failure — the API is restarting (make restart) or
      // briefly unreachable.  Announce it so the MaintenanceOverlay can
      // show "updating…" instead of each page surfacing a raw error.
      window.dispatchEvent(new Event('4truck:maintenance'));
    }
    return res;
  } catch (e) {
    // Re-throw timeout errors with the explicit reason we set above
    // so callers see "Request timed out…" instead of the browser's
    // generic abort message.  External-signal aborts pass through
    // unchanged so React Query cancellations still look like aborts.
    if (didTimeout && e instanceof DOMException && e.name === 'AbortError') {
      throw new Error(
        `Request to ${path} timed out after ${Math.round(timeoutMs / 1000)}s. ` +
        `The server may be busy — please try again.`,
      );
    }
    throw e;
  } finally {
    clearTimeout(timeout);
    if (externalSignal && externalAbortHandler) {
      externalSignal.removeEventListener('abort', externalAbortHandler);
    }
  }
}

/**
 * Error thrown by ``apiJSON`` on non-2xx responses.  Carries the HTTP
 * ``status`` so callers can branch on 401 / 403 / 404 without parsing
 * the message (which is the FastAPI ``detail`` field, useful for
 * humans but not for control flow).
 *
 * Example: hide a permission-gated chip on 403 without blowing up the
 * whole page.
 *
 *   try {
 *     await apiJSON('/alerts/escalations');
 *   } catch (e) {
 *     if (e instanceof ApiError && e.status === 403) return null;
 *     throw e;
 *   }
 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function apiJSON<T = unknown>(path: string, opts: ApiFetchOpts = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const res = await apiFetch(path, opts, timeoutMs);
  if (!res.ok) {
    const err: { detail?: unknown } = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err.detail;
    const msg = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg || String(d)).join('; ')
      // FastAPI errors that carry a structured body ({message, error_code})
      // — surface the human message instead of falling back to statusText.
      : (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string')
        ? (detail as { message: string }).message
      : res.statusText;
    throw new ApiError(res.status, msg);
  }
  return res.json();
}

/** apiJSON with 90-second timeout for AI endpoints. */
export async function apiJSONAI<T = unknown>(path: string, opts: ApiFetchOpts = {}): Promise<T> {
  return apiJSON<T>(path, opts, AI_REQUEST_TIMEOUT_MS);
}

// ── AI write actions (copilot "hands") ───────────────────────────
/** Status of a proposed write action. */
export interface AIActionStatus {
  id: string;
  tool: string;
  summary: string;
  risk: string;
  status: 'pending' | 'executing' | 'consumed' | 'declined' | 'failed';
  result: Record<string, unknown> | null;
}

/** Approve a proposed write — the server re-authorizes + executes. */
export function aiApproveAction(proposalId: string): Promise<{ status: string; result: Record<string, unknown> }> {
  return apiJSONAI(`/ai/actions/${encodeURIComponent(proposalId)}/approve`, { method: 'POST' });
}

/** Reject a proposed write (no mutation, recorded). */
export function aiRejectAction(proposalId: string): Promise<{ ok: boolean }> {
  return apiJSON(`/ai/actions/${encodeURIComponent(proposalId)}/reject`, { method: 'POST' });
}

/** Current status of a proposal (so a refreshed card shows the truth). */
export function aiGetActionStatus(proposalId: string): Promise<AIActionStatus> {
  return apiJSON(`/ai/actions/${encodeURIComponent(proposalId)}`);
}

/** apiJSON with 90-second timeout for heavy compute endpoints (scorecards, reports). */
export async function apiJSONSlow<T = unknown>(path: string, opts: ApiFetchOpts = {}): Promise<T> {
  return apiJSON<T>(path, opts, AI_REQUEST_TIMEOUT_MS);
}

/** SSE event types from /ai/chat/stream */
export type StreamEvent =
  | { type: 'tool'; name: string; label: string }
  | { type: 'thinking'; text: string }   // live reasoning chunk (streaming models)
  | { type: 'delta'; text: string }      // live answer chunk (streaming models)
  | { type: 'done'; reply: string; suggestions: string[]; usage: Record<string, number> | null; tool_results: unknown[]; scope?: { restricted: boolean; vehicle_count?: number }; model_tier?: string; conversation_id?: number; reasoning?: string; process?: { type: 'thinking' | 'tool'; text?: string; name?: string; label?: string; args?: string; result?: string; elapsed_ms?: number }[]; artifacts?: { type: string; [k: string]: unknown }[] }
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
  opts?: {
    /** Continue this thread; omit/null for the latest one. */
    conversationId?: number | null;
    /** Force a fresh thread (the "New chat" button). */
    newConversation?: boolean;
    /** What the user is viewing — a PROMPT HINT for the model (the
     *  backend still authorizes tools/scope from the JWT, never this). */
    pageContext?: unknown;
    /** Device-held file text riding inline for THIS message. 'sheet' =
     *  importable spreadsheet text; 'text' = extracted document (PDF/TXT),
     *  read-only. There is no upload endpoint by design — the server
     *  parses these transiently and persists nothing until the user
     *  approves an import. */
    attachments?: { name: string; content: string; kind?: 'sheet' | 'text' | 'image' }[];
  },
): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  // Mirror apiFetch: carry the persona preview so owner/admin "View as <role>"
  // gates the streaming AI as that role too (the backend honours X-View-As).
  if (_activeViewForApi) headers['X-View-As'] = _activeViewForApi;

  const res = await fetch(`${API_BASE}/ai/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      message,
      conversation_id: opts?.conversationId ?? null,
      new_conversation: opts?.newConversation ?? false,
      page_context: opts?.pageContext ?? null,
      attachments: opts?.attachments?.length ? opts.attachments : null,
    }),
    signal,
    credentials: 'include',
  });

  if (res.status === 401) {
    // Same rationale as apiFetch — App.tsx handles auth-less state.
    clearToken();
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

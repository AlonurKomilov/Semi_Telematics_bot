import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { apiJSON, getToken, setToken, clearToken, isTokenPersistent } from '../api/client';
import { isSafeReturnTo, APEX_DOMAIN as SAFE_APEX_DOMAIN, EXPLICIT_SIGNOUT_KEY } from '../lib/safeReturnTo';
import i18n from '../i18n';
import type { User, TelegramLoginData, AuthResponse } from '../types';

/** Poll interval for checking token expiry. */
const CHECK_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

/** Refresh window for short (~8-hour) tokens. */
const SHORT_REFRESH_THRESHOLD_MS = 30 * 60 * 1000; // 30 minutes
/** Refresh window for long (~30-day) tokens. */
const LONG_REFRESH_THRESHOLD_MS  = 7 * 24 * 60 * 60 * 1000; // 7 days

function decodeJwtPayload(token: string): { exp?: number; remember?: boolean; jti?: string } | null {
  try {
    return JSON.parse(atob(token.split('.')[1]));
  } catch {
    return null;
  }
}

/** A token minted before the ``jti`` rollout has no ``jti`` claim and
 *  therefore no row in ``user_sessions``.  Treat it as "needs refresh"
 *  on the next page load so the API can backfill a session row via
 *  ``refresh_token`` — without this hop, an existing logged-in user
 *  sees an empty "Active sessions" panel until their long token
 *  naturally enters the refresh window (up to 23 days). */
function tokenLacksJti(token: string): boolean {
  const payload = decodeJwtPayload(token);
  return !!payload && !payload.jti;
}

function getTokenExpiry(token: string): number | null {
  const payload = decodeJwtPayload(token);
  return payload && typeof payload.exp === 'number' ? payload.exp * 1000 : null;
}

/** Threshold below which we proactively refresh.  Short-lived
 *  ("Remember me" off) tokens use a tight 30-minute window so we
 *  don't waste an entire 8-hour session on a stale token; long-lived
 *  tokens keep the original 7-day window. */
function refreshThresholdFor(token: string): number {
  const payload = decodeJwtPayload(token);
  return payload && payload.remember === false
    ? SHORT_REFRESH_THRESHOLD_MS
    : LONG_REFRESH_THRESHOLD_MS;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  loginWithTelegram: (tgData: TelegramLoginData, rememberMe?: boolean) => Promise<void>;
  loginWithEmail: (email: string, password: string, rememberMe?: boolean) => Promise<void>;
  registerWithEmail: (email: string, password: string, displayName: string, inviteCode: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Force re-fetch of /user/me — used by the Role Permissions admin
   * page after a save so the saving admin's own UI updates without
   * waiting for the next focus-triggered refresh. */
  refreshUser: () => Promise<void>;
}

// Role → preferred persona subdomain.  After a successful login we
// inspect this map and, if the user landed at apex or a different
// persona's host, redirect them to the host that matches their role.
// Owner/Admin/Driver all land on dash. (driver gets the special-cased
// DriverOverview component on the shared dashboard) while the three
// operational personas get their own branded entry point.  Override
// per-deployment via the VITE_APEX_DOMAIN env var; the default matches
// production at 4truck.us.
// Imported from ../lib/safeReturnTo so the apex constant and the
// ``return_to`` validator stay in lockstep across the two redirect
// sites (App.tsx and this file).
const APEX_DOMAIN = SAFE_APEX_DOMAIN;
const ROLE_TO_HOST: Record<string, string> = {
  owner: `dash.${APEX_DOMAIN}`,
  admin: `dash.${APEX_DOMAIN}`,
  fleet: `fleet.${APEX_DOMAIN}`,
  dispatcher: `dispatch.${APEX_DOMAIN}`,
  safety: `safety.${APEX_DOMAIN}`,
  hr: `hr.${APEX_DOMAIN}`,
  accounting: `accounting.${APEX_DOMAIN}`,
  recruiter: `recruiter.${APEX_DOMAIN}`,
  driver: `dash.${APEX_DOMAIN}`,
};

function redirectAfterLoginIfNeeded(role: string | undefined): boolean {
  if (!role) return false;
  const target = ROLE_TO_HOST[role];
  if (!target) return false;
  if (typeof window === 'undefined') return false;
  const currentHost = window.location.hostname.toLowerCase();
  // Already on the right host (or on a non-production preview that
  // doesn't match the mapping) — nothing to do.
  if (currentHost === target) return false;
  // Only redirect when we're on a 4truck.us host of some kind; preview
  // deploys and local dev (localhost, *.vercel.app, etc.) should stay
  // on their own host so the dev loop isn't broken.
  if (!currentHost.endsWith(APEX_DOMAIN)) return false;
  // Respect ?return_to=… if the user was bounced here from a specific
  // page — e.g. clicking a "Settings" deep link while logged out.
  // ``isSafeReturnTo`` parses the URL and matches the hostname against
  // the apex exactly (or via the ``.4truck.us`` suffix), rejecting
  // open-redirect payloads like ``https://4truck.us.attacker.com/``
  // that an earlier ``includes(APEX_DOMAIN)`` substring check would
  // have accepted.
  const params = new URLSearchParams(window.location.search);
  const returnTo = params.get('return_to');
  if (isSafeReturnTo(returnTo)) {
    window.location.href = returnTo!;
    return true;
  }
  window.location.href = `https://${target}/`;
  return true;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  /** Silently refresh the JWT if it's close to expiring.  The
   *  threshold is dynamic: short-lived ("Remember me" off) tokens
   *  refresh in the last 30 minutes, long-lived tokens in the last
   *  7 days.  Without this split, a freshly-issued 8-hour token
   *  would trigger an immediate refresh because 7 days > 8 hours. */
  const refreshTokenIfNeeded = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    const exp = getTokenExpiry(token);
    if (!exp) return;
    const remaining = exp - Date.now();
    // Two reasons to refresh:
    //   1. Token is close to expiring (normal proactive refresh).
    //   2. Token predates the ``jti`` rollout — one-time hop so the
    //      API can backfill a ``user_sessions`` row.  After this
    //      refresh the new token has a jti and this branch never
    //      fires for it again.
    const closeToExpiry = remaining <= refreshThresholdFor(token);
    const needsJtiBackfill = tokenLacksJti(token);
    if (!closeToExpiry && !needsJtiBackfill) return;
    try {
      const res = await apiJSON<AuthResponse>('/auth/refresh', { method: 'POST' });
      // Preserve the same storage (persistent vs session-only)
      setToken(res.access_token, isTokenPersistent());
    } catch {
      // Token expired or server error — force logout on next request
    }
  }, []);

  const fetchUser = useCallback(async () => {
    // ALWAYS probe /user/me — the browser may still hold a valid
    // ``.4truck.us`` cookie even when this host's localStorage is
    // empty (the cookie travels across all subdomains via the apex
    // login flow).  The previous ``if (!getToken()) return`` short-
    // circuit hid the cookie-only session and made the apex
    // /login page render the form for already-authenticated users.
    // If there's neither cookie nor token, /user/me returns 401 and
    // the catch below cleanly resolves to the unauthenticated state.
    if (getToken()) {
      // Silently refresh on every page load if a localStorage token
      // is in its last 7 days — cookie sessions don't need this; the
      // server refreshes the cookie on each /auth/refresh response.
      await refreshTokenIfNeeded();
    }
    try {
      const data = await apiJSON<User>('/user/me');
      setUser(data);
      // Sync the backend-stored language into i18next so an account
      // whose Telegram bot is set to Russian doesn't see the dashboard
      // in English on first login. Only applies the first time per
      // session; users can still switch via the top-bar selector
      // (which writes back to the same endpoint).
      //
      // ``i18n`` is statically imported at the top — same instance
      // ``main.tsx`` initialises at boot — so the previous dynamic
      // ``await import('../i18n')`` was a Vite chunking-warning
      // generator with no behavioural benefit.  The outer try/catch
      // still guards against ``changeLanguage`` failures (unknown
      // locale codes, detector errors).
      try {
        const lang = (data as { language?: string | null }).language;
        if (lang && typeof lang === 'string' && i18n.resolvedLanguage !== lang) {
          await i18n.changeLanguage(lang);
        }
      } catch {
        /* changeLanguage failed — selector still works */
      }
    } catch {
      clearToken();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, [refreshTokenIfNeeded]);

  // Set up periodic token refresh check
  useEffect(() => {
    refreshTimer.current = setInterval(refreshTokenIfNeeded, CHECK_INTERVAL_MS);
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [refreshTokenIfNeeded]);

  useEffect(() => { fetchUser(); }, [fetchUser]);

  // Re-fetch /user/me on window focus so permission changes propagate
  // within seconds.  Without this, an admin tightening a permission via
  // the Role Permissions page would take up to the JWT lifetime (~30
  // days) to affect already-logged-in dashboard tabs.  Focus is the
  // natural trigger — every time a user comes back to the tab we get a
  // fresh permission read.  Cost: one /user/me HTTP request per tab
  // focus event.
  useEffect(() => {
    // Refetch on focus regardless of localStorage state — cookie-only
    // sessions have no token to gate on, and the network cost is one
    // /user/me per focus event either way.
    const onFocus = () => { void fetchUser(); };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [fetchUser]);

  const loginWithTelegram = useCallback(async (tgData: TelegramLoginData, rememberMe = false) => {
    // The flag must reach the API so it can pick the right JWT TTL
    // (long vs short).  Client-side storage choice (localStorage vs
    // sessionStorage) is still controlled by ``setToken(..., persistent)``.
    const res = await apiJSON<AuthResponse>('/auth/telegram-login', {
      method: 'POST',
      body: { ...(tgData as unknown as Record<string, unknown>), remember_me: rememberMe },
    });
    setToken(res.access_token, rememberMe);
    if (redirectAfterLoginIfNeeded(res.user?.role)) return;
    await fetchUser();
  }, [fetchUser]);

  const loginWithEmail = useCallback(async (email: string, password: string, rememberMe = false) => {
    const res = await apiJSON<AuthResponse>('/auth/login', {
      method: 'POST',
      body: { email, password, remember_me: rememberMe },
    });
    setToken(res.access_token, rememberMe);
    if (redirectAfterLoginIfNeeded(res.user?.role)) return;
    await fetchUser();
  }, [fetchUser]);

  const registerWithEmail = useCallback(async (
    email: string, password: string, displayName: string, inviteCode: string
  ) => {
    // Registration → long-lived session by default (account owners
    // shouldn't be kicked out at end of shift).  Sent explicitly
    // because the backend defaults ``remember_me`` to False.
    const res = await apiJSON<AuthResponse>('/auth/register', {
      method: 'POST',
      body: {
        email, password,
        display_name: displayName,
        invite_code: inviteCode,
        remember_me: true,
      },
    });
    setToken(res.access_token, true);
    if (redirectAfterLoginIfNeeded(res.user?.role)) return;
    await fetchUser();
  }, [fetchUser]);

  const logout = useCallback(async () => {
    // Order matters here.  We deliberately do NOT call ``setUser(null)``
    // — that would re-render App.tsx, which sees ``!user`` on a
    // persona subdomain and fires its own bounce-to-apex with
    // ``?return_to=<current page>``.  That bounce races the
    // window.location.href below and frequently wins, leaving the
    // user on ``apex/login?return_to=https://fleet.4truck.us/``
    // instead of the clean ``apex/login`` we wanted.  After re-login,
    // ``return_to`` would override role-forward and send a Fleet user
    // back to fleet. (fine for that user) or — worse — bounce a
    // freshly-logged-in Safety user to whichever persona host they
    // logged out from.
    //
    // Avoiding setUser is NOT enough by itself: once clearToken()
    // drops the Bearer, any in-flight/polling request 401s, the 401
    // handler nulls the user, and App.tsx bounces anyway.  Two guards
    // close the remaining race:
    //   1. The EXPLICIT_SIGNOUT flag below — App.tsx's bounce sees it
    //      and goes to the clean apex /login (no return_to).
    //   2. ``keepalive: true`` on the logout POST — the racing
    //      ``location.replace`` would otherwise CANCEL this request,
    //      leaving the shared ``.4truck.us`` cookie alive server-side;
    //      the apex then sees a live session and re-forwards to
    //      return_to → dash rejects → bounce → … the login↔dash loop.
    try {
      sessionStorage.setItem(EXPLICIT_SIGNOUT_KEY, '1');
    } catch { /* private mode — the keepalive guard still applies */ }
    clearToken();
    try {
      await fetch('/api/auth/logout', {
        method: 'POST', credentials: 'include', keepalive: true,
      });
    } catch {
      /* server unreachable — cookie expires on its own TTL */
    }
    if (typeof window !== 'undefined') {
      const host = window.location.hostname.toLowerCase();
      if (host === APEX_DOMAIN) {
        // Already on apex — full reload re-evaluates auth (now cookie-
        // less), App.tsx will render the Login form.
        window.location.reload();
        return;
      }
      if (host.endsWith(`.${APEX_DOMAIN}`)) {
        window.location.href = `https://${APEX_DOMAIN}/login`;
      }
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, loginWithTelegram, loginWithEmail, registerWithEmail, logout, refreshUser: fetchUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}

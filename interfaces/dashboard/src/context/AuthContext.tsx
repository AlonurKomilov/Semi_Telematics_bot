import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { apiJSON, getToken, setToken, clearToken, isTokenPersistent } from '../api/client';
import type { User, TelegramLoginData, AuthResponse } from '../types';

/** Poll interval for checking token expiry. */
const CHECK_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

/** Refresh window for short (~8-hour) tokens. */
const SHORT_REFRESH_THRESHOLD_MS = 30 * 60 * 1000; // 30 minutes
/** Refresh window for long (~30-day) tokens. */
const LONG_REFRESH_THRESHOLD_MS  = 7 * 24 * 60 * 60 * 1000; // 7 days

function decodeJwtPayload(token: string): { exp?: number; remember?: boolean } | null {
  try {
    return JSON.parse(atob(token.split('.')[1]));
  } catch {
    return null;
  }
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
  logout: () => void;
  /** Force re-fetch of /user/me — used by the Role Permissions admin
   * page after a save so the saving admin's own UI updates without
   * waiting for the next focus-triggered refresh. */
  refreshUser: () => Promise<void>;
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
    if (remaining > refreshThresholdFor(token)) return;
    try {
      const res = await apiJSON<AuthResponse>('/auth/refresh', { method: 'POST' });
      // Preserve the same storage (persistent vs session-only)
      setToken(res.access_token, isTokenPersistent());
    } catch {
      // Token expired or server error — force logout on next request
    }
  }, []);

  const fetchUser = useCallback(async () => {
    if (!getToken()) { setLoading(false); return; }
    // Silently refresh on every page load if token is in its last 7 days
    await refreshTokenIfNeeded();
    try {
      const data = await apiJSON<User>('/user/me');
      setUser(data);
      // Sync the backend-stored language into i18next so an account
      // whose Telegram bot is set to Russian doesn't see the dashboard
      // in English on first login. Only applies the first time per
      // session; users can still switch via the top-bar selector
      // (which writes back to the same endpoint).
      try {
        const lang = (data as { language?: string | null }).language;
        if (lang && typeof lang === 'string') {
          const i18n = (await import('../i18n')).default;
          if (i18n.resolvedLanguage !== lang) {
            await i18n.changeLanguage(lang);
          }
        }
      } catch {
        /* i18n not yet loaded — selector still works */
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
    const onFocus = () => {
      if (getToken()) fetchUser();
    };
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
    await fetchUser();
  }, [fetchUser]);

  const loginWithEmail = useCallback(async (email: string, password: string, rememberMe = false) => {
    const res = await apiJSON<AuthResponse>('/auth/login', {
      method: 'POST',
      body: { email, password, remember_me: rememberMe },
    });
    setToken(res.access_token, rememberMe);
    await fetchUser();
  }, [fetchUser]);

  const registerWithEmail = useCallback(async (
    email: string, password: string, displayName: string, inviteCode: string
  ) => {
    // Registration → long-lived session by default (account owners
    // shouldn't be kicked out at end of shift; the backend also
    // defaults remember_me=True on /register).
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
    await fetchUser();
  }, [fetchUser]);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
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

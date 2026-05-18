import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { apiJSON, getToken, setToken, clearToken, isTokenPersistent } from '../api/client';
import type { User, TelegramLoginData, AuthResponse } from '../types';

/** Refresh the token when less than this many ms remain. */
const REFRESH_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000; // 7 days — refresh in last week of 30-day token
/** Poll interval for checking token expiry. */
const CHECK_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

function getTokenExpiry(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  loginWithTelegram: (tgData: TelegramLoginData, rememberMe?: boolean) => Promise<void>;
  loginWithEmail: (email: string, password: string, rememberMe?: boolean) => Promise<void>;
  registerWithEmail: (email: string, password: string, displayName: string, inviteCode: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  /** Silently refresh the JWT if it's close to expiring. */
  const refreshTokenIfNeeded = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    const exp = getTokenExpiry(token);
    if (!exp) return;
    const remaining = exp - Date.now();
    if (remaining > REFRESH_THRESHOLD_MS) return;
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

  const loginWithTelegram = useCallback(async (tgData: TelegramLoginData, rememberMe = false) => {
    const res = await apiJSON<AuthResponse>('/auth/telegram-login', {
      method: 'POST',
      body: tgData as unknown as Record<string, unknown>,
    });
    setToken(res.access_token, rememberMe);
    await fetchUser();
  }, [fetchUser]);

  const loginWithEmail = useCallback(async (email: string, password: string, rememberMe = false) => {
    const res = await apiJSON<AuthResponse>('/auth/login', {
      method: 'POST',
      body: { email, password },
    });
    setToken(res.access_token, rememberMe);
    await fetchUser();
  }, [fetchUser]);

  const registerWithEmail = useCallback(async (
    email: string, password: string, displayName: string, inviteCode: string
  ) => {
    const res = await apiJSON<AuthResponse>('/auth/register', {
      method: 'POST',
      body: { email, password, display_name: displayName, invite_code: inviteCode },
    });
    setToken(res.access_token);
    await fetchUser();
  }, [fetchUser]);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, loginWithTelegram, loginWithEmail, registerWithEmail, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}

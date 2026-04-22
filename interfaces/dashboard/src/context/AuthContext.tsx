import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { apiJSON, getToken, setToken, clearToken } from '../api/client';
import type { User, TelegramLoginData, AuthResponse } from '../types';

/** Refresh the token when less than this many ms remain. */
const REFRESH_THRESHOLD_MS = 60 * 60 * 1000; // 1 hour
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
  loginWithTelegram: (tgData: TelegramLoginData) => Promise<void>;
  loginWithEmail: (email: string, password: string) => Promise<void>;
  registerWithEmail: (email: string, password: string, displayName: string, inviteCode: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchUser = useCallback(async () => {
    if (!getToken()) { setLoading(false); return; }
    try {
      const data = await apiJSON<User>('/user/me');
      setUser(data);
    } catch {
      clearToken();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

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
      setToken(res.access_token);
    } catch {
      // Token expired or server error — force logout on next request
    }
  }, []);

  // Set up periodic token refresh check
  useEffect(() => {
    refreshTimer.current = setInterval(refreshTokenIfNeeded, CHECK_INTERVAL_MS);
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [refreshTokenIfNeeded]);

  useEffect(() => { fetchUser(); }, [fetchUser]);

  const loginWithTelegram = useCallback(async (tgData: TelegramLoginData) => {
    const res = await apiJSON<AuthResponse>('/auth/telegram-login', {
      method: 'POST',
      body: tgData as unknown as Record<string, unknown>,
    });
    setToken(res.access_token);
    await fetchUser();
  }, [fetchUser]);

  const loginWithEmail = useCallback(async (email: string, password: string) => {
    const res = await apiJSON<AuthResponse>('/auth/login', {
      method: 'POST',
      body: { email, password },
    });
    setToken(res.access_token);
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

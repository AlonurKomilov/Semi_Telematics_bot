import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { setToken } from '../api/client';

// Telegram Login Widget callback shape.  Mirrors the user dashboard's
// definition; kept inline so this app has no shared types with the
// customer-side code (intentional — operator dashboard is independent).
interface TelegramLoginData {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

// Minimal shape of the Telegram WebApp SDK we rely on.  Only the
// fields we touch — the SDK has many more.
interface TelegramWebApp {
  initData: string;
  ready: () => void;
  expand?: () => void;
}

declare global {
  interface Window {
    __onSystemTelegramAuth?: (user: TelegramLoginData) => Promise<void>;
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export default function Login() {
  const nav = useNavigate();
  const widgetRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string>('');
  const [verifying, setVerifying] = useState(false);
  // True while we're inside Telegram and attempting Mini App auth, so
  // we can hide the Login Widget (it's only for plain-browser use).
  const [inTelegram, setInTelegram] = useState(false);

  // ── Mini App auth (preferred) ──────────────────────────────
  // When opened from the system bot's Menu Button, the WebApp SDK
  // exposes signed initData.  Validate it server-side against the
  // system bot token — no Login Widget domain config involved.
  useEffect(() => {
    const wa = window.Telegram?.WebApp;
    const initData = wa?.initData || '';
    if (!wa || !initData) {
      return;  // not in Telegram → fall through to the widget effect
    }
    setInTelegram(true);
    try { wa.ready(); wa.expand?.(); } catch { /* SDK quirk — ignore */ }
    setVerifying(true);
    (async () => {
      try {
        const res = await fetch('/api/auth/system-telegram-init', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ init_data: initData }),
        });
        if (res.status === 403) {
          throw new Error('Your Telegram account is not on the operator allowlist (SYSTEM_OWNER_IDS).');
        }
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        if (!data?.access_token) throw new Error('No token returned');
        setToken(data.access_token);
        nav('/accounts');
      } catch (e) {
        setToken('');
        setError(e instanceof Error ? e.message : 'Mini App login failed');
      } finally {
        setVerifying(false);
      }
    })();
  }, [nav]);

  // ── Login Widget (plain-browser fallback) ──────────────────
  useEffect(() => {
    // Skip entirely when we're inside Telegram — the Mini App path
    // above handles auth, and the widget would just error on the
    // embedded WebView origin anyway.
    if (window.Telegram?.WebApp?.initData) return;

    // Telegram widget callback — sends the signed login payload to
    // the platform's existing /auth/telegram-login endpoint and then
    // verifies the resulting JWT can reach /system/stats.  If it
    // can't (403), the user isn't on the SYSTEM_OWNER_IDS allowlist
    // and we clear the token rather than leaving a useless session.
    window.__onSystemTelegramAuth = async (tgUser) => {
      setError('');
      setVerifying(true);
      try {
        // Operator-only endpoint: validates against the system bot
        // token AND checks the telegram_id is in SYSTEM_OWNER_IDS in
        // a single round-trip.  Customer dashboards keep using the
        // separate /auth/telegram-login endpoint.
        const res = await fetch('/api/auth/system-telegram-login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...tgUser, remember_me: true }),
        });
        if (res.status === 403) {
          throw new Error(
            'Your account is not on the operator allowlist (SYSTEM_OWNER_IDS).',
          );
        }
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        if (!data?.access_token) throw new Error('No token returned');
        setToken(data.access_token);
        nav('/accounts');
      } catch (e) {
        setToken('');  // clear any partial token
        setError(e instanceof Error ? e.message : 'Login failed');
      } finally {
        setVerifying(false);
      }
    };

    // Inject the Telegram widget for the SYSTEM bot.  The operator
    // console MUST NOT use /auth/config (that returns the customer-
    // facing login bot) — we use the dedicated /auth/system-config
    // endpoint that returns the operator bot's username.
    const el = widgetRef.current;
    if (!el) return;
    (async () => {
      let botUsername = '';
      try {
        const cfg = await fetch('/api/auth/system-config').then((r) => r.json());
        botUsername = cfg?.bot_username || '';
      } catch { /* fallback to no widget */ }
      if (!botUsername) {
        setError(
          'Could not fetch system bot config.  Confirm TELEGRAM_BOT_TOKEN is set ' +
          'and the API has restarted since.',
        );
        return;
      }
      el.innerHTML = '';
      const script = document.createElement('script');
      script.src = 'https://telegram.org/js/telegram-widget.js?22';
      script.async = true;
      script.setAttribute('data-telegram-login', botUsername);
      script.setAttribute('data-size', 'large');
      script.setAttribute('data-radius', '6');
      script.setAttribute('data-onauth', '__onSystemTelegramAuth(user)');
      script.setAttribute('data-request-access', 'write');
      el.appendChild(script);
    })();

    return () => {
      delete window.__onSystemTelegramAuth;
    };
  }, [nav]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950">
      <div className="bg-slate-900 border border-slate-800 rounded-lg px-8 py-10 w-full max-w-md">
        <h1 className="text-lg font-semibold tracking-wider text-slate-100 mb-1">
          4truck <span className="text-slate-500">/ system</span>
        </h1>
        <p className="text-xs text-slate-400 mb-6">
          Operator-only console.  Restricted to platform owners on the
          SYSTEM_OWNER_IDS allowlist; access is also IP-gated at the
          Cloudflare edge.
        </p>
        {inTelegram ? (
          // Inside Telegram → Mini App auth runs automatically; no
          // Login Widget button needed.
          <p className="text-sm text-slate-300 text-center min-h-[48px] flex items-center justify-center">
            Authenticating via Telegram…
          </p>
        ) : (
          <div ref={widgetRef} className="flex justify-center min-h-[48px]" />
        )}
        {verifying && !inTelegram && (
          <p className="text-xs text-slate-400 mt-4 text-center">Verifying operator access…</p>
        )}
        {error && (
          <p className="text-xs text-danger mt-4 bg-danger/10 border border-danger/40 rounded px-3 py-2">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

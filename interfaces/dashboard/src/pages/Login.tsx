import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';
import { setToken } from '../api/client';
import { apiJSON } from '../api/client';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { toneClasses, toneText } from '../lib/status';
import type { TelegramLoginData } from '../types';

type Mode = 'login' | 'register';

export default function Login() {
  const { t } = useTranslation();
  const { loginWithTelegram, loginWithEmail, registerWithEmail } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  // The login API returns a structured 403 when the account isn't
  // email-verified yet.  Flipping this flag swaps the generic
  // ``error`` block for a "verify your inbox" notice with a "resend"
  // button — better UX than a flat error toast.
  const [needsVerification, setNeedsVerification] = useState(false);
  // After a successful registration the API tells us to check the
  // inbox; we render a green callout instead of redirecting.
  const [registeredEmail, setRegisteredEmail] = useState('');
  const [botUsername, setBotUsername] = useState('4truckBot');
  const [botId, setBotId] = useState('');
  const [widgetKey, setWidgetKey] = useState(0);
  const [showDisconnect, setShowDisconnect] = useState(false);

  // Bot-login state
  const [botLoginLink, setBotLoginLink] = useState('');
  const [botLoginToken, setBotLoginToken] = useState('');
  const [botLoginStatus, setBotLoginStatus] = useState<'idle' | 'pending' | 'approved' | 'rejected' | 'expired'>('idle');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup bot-login polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Start bot-login flow
  const startBotLogin = useCallback(async () => {
    setError('');
    setBotLoginStatus('pending');
    try {
      const res = await fetch('/api/auth/bot-login/init', { method: 'POST' });
      if (!res.ok) throw new Error('Failed to start bot login');
      const data = await res.json();
      setBotLoginToken(data.token);
      setBotLoginLink(data.deep_link);

      // Open the bot link
      window.open(data.deep_link, '_blank');

      // Start polling
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const check = await fetch(`/api/auth/bot-login/check/${data.token}`);
          if (!check.ok) return;
          const result = await check.json();
          if (result.status === 'approved') {
            if (pollRef.current) clearInterval(pollRef.current);
            setBotLoginStatus('approved');
            setToken(result.access_token, rememberMe);
            window.location.reload();
          } else if (result.status === 'rejected') {
            if (pollRef.current) clearInterval(pollRef.current);
            setBotLoginStatus('rejected');
            setError(result.reason || 'Login rejected — you are not registered.');
          } else if (result.status === 'expired') {
            if (pollRef.current) clearInterval(pollRef.current);
            setBotLoginStatus('expired');
            setError('Login link expired. Please try again.');
          }
        } catch { /* ignore poll errors */ }
      }, 3000);
    } catch (err) {
      setBotLoginStatus('idle');
      setError(err instanceof Error ? err.message : 'Failed to start bot login');
    }
  }, []);

  const cancelBotLogin = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    setBotLoginStatus('idle');
    setBotLoginLink('');
    setBotLoginToken('');
    setError('');
  }, []);

  // Pre-fill invite code from EITHER:
  //   ?invite=XXXX  (legacy / link-channel deep-link)
  //   /signup/XXXX  (path-segment URL the new email-channel uses —
  //                  keeps the code out of Referer headers and
  //                  query-string-bearing CDN access logs)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    let code = params.get('invite');
    if (!code) {
      // Match a /signup/<code> path; the trailing segment is the
      // invite code, normalised to upper-case.  Backend get_invite
      // already upper-strips on read, but we normalise here too so
      // the preview-fetch URL stays canonical.
      const m = window.location.pathname.match(/^\/signup\/([A-Za-z0-9-]+)$/);
      if (m) code = m[1];
    }
    if (code) {
      setInviteCode(code);
      setMode('register');
    }
  }, []);

  // Invite preview — when an invite code is present, fetch the safe-
  // to-show metadata so the recipient sees "you're being invited to
  // ACME as Driver by Alice" BEFORE they submit (and consume) the
  // single-use code.  Closes the trust gap a phishing-aware recipient
  // would otherwise have to take on faith — they can't validate the
  // invite is legitimate without burning it.
  //
  // Uniform 404 from the backend covers missing/expired/used/revoked
  // — we treat any non-200 the same way: don't render the callout.
  const [invitePreview, setInvitePreview] = useState<{
    account_name: string;
    role_label: string;
    truck_num: string | null;
    expires_at: string;
    inviter_display_name: string;
  } | null>(null);
  useEffect(() => {
    if (!inviteCode) { setInvitePreview(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJSON<{
          account_name: string;
          role_label: string;
          truck_num: string | null;
          expires_at: string;
          inviter_display_name: string;
        }>(`/auth/invite-preview?code=${encodeURIComponent(inviteCode)}`);
        if (!cancelled) setInvitePreview(data);
      } catch {
        // 404 (uniform "not available") or any network error → hide
        // the callout; the form still works the same.
        if (!cancelled) setInvitePreview(null);
      }
    })();
    return () => { cancelled = true; };
  }, [inviteCode]);

  useEffect(() => {
    // Telegram Login Widget callback
    window.__onTelegramAuth = async (tgUser: TelegramLoginData) => {
      try {
        await loginWithTelegram(tgUser, rememberMe);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Telegram login failed');
      }
    };

    // Fetch bot username, then inject Telegram Login Widget
    const el = containerRef.current;
    if (!el) return;

    (async () => {
      let fetchedBot = botUsername;
      try {
        const res = await fetch('/api/auth/config');
        if (res.ok) {
          const data = await res.json();
          fetchedBot = data.bot_username || fetchedBot;
          setBotUsername(fetchedBot);
          if (data.bot_id) setBotId(data.bot_id);
        }
      } catch { /* use fallback */ }

      el.innerHTML = '';
      const script = document.createElement('script');
      script.src = 'https://telegram.org/js/telegram-widget.js?22';
      script.async = true;
      script.setAttribute('data-telegram-login', fetchedBot);
      script.setAttribute('data-size', 'large');
      script.setAttribute('data-radius', '8');
      script.setAttribute('data-onauth', '__onTelegramAuth(user)');
      script.setAttribute('data-request-access', 'write');
      el.appendChild(script);
    })();

    return () => { delete window.__onTelegramAuth; };
  }, [loginWithTelegram, rememberMe, widgetKey]);

  /** Guide the user to disconnect their Telegram Login Widget session.
   *
   *  The Telegram widget session cookie lives on oauth.telegram.org (not our
   *  domain), so we cannot clear it directly.  The user must disconnect from
   *  within the Telegram app, then refresh the widget here.
   */
  const handleRefreshWidget = () => {
    setShowDisconnect(false);
    setWidgetKey((k) => k + 1);
    setError('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setNeedsVerification(false);
    setRegisteredEmail('');
    setLoading(true);
    try {
      if (mode === 'login') {
        await loginWithEmail(email, password, rememberMe);
      } else {
        // Registration no longer auto-signs-in — the API returns the
        // verification-required envelope and we render the "check
        // your inbox" notice instead of redirecting.
        const result = await apiJSON<{
          status: string;
          verification_required?: boolean;
          email?: string;
          message?: string;
        }>('/auth/register', {
          method: 'POST',
          body: {
            email,
            password,
            display_name: displayName,
            invite_code: inviteCode,
          },
        });
        if (result?.verification_required) {
          setRegisteredEmail(result.email || email);
          // Drop into login mode so the user knows what to do next.
          setMode('login');
          // Fall through — don't throw.  The notice block on the
          // form will render via registeredEmail.
        } else {
          // Legacy / unexpected response shape — fall back to the
          // old auto-login path so existing callers keep working.
          await registerWithEmail(email, password, displayName, inviteCode);
        }
      }
    } catch (err) {
      // The login API responds with two structured detail shapes we
      // can render as more-actionable UI than a flat error string.
      const raw = err instanceof Error ? err.message : '';
      // ``apiJSON`` surfaces server detail messages directly — match
      // on the error_code substring rather than parsing JSON ourselves.
      if (raw.includes('email_not_verified')) {
        setNeedsVerification(true);
        setError(t('auth.error_email_not_verified'));
      } else {
        setError(raw || 'Authentication failed');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <div className="mb-8 text-center">
        {/* ``text-foreground`` so the brand reads on Light theme too —
            the inherited default was close to the page background. */}
        <h1 className="text-4xl font-bold mb-2 text-foreground">4truck</h1>
        <p className="text-muted-foreground">{t('auth.tagline')}</p>
      </div>

      <div className="bg-card rounded-xl p-8 shadow-lg border border-border w-full max-w-sm">
        {/* Tab switcher */}
        <div className="flex mb-6 border-b border-border">
          <button
            className={`flex-1 pb-2 text-sm font-medium transition-colors ${
              mode === 'login'
                ? 'text-primary border-b-2 border-primary'
                : 'text-muted-foreground hover:text-foreground/80'
            }`}
            onClick={() => { setMode('login'); setError(''); }}
          >
            Sign In
          </button>
          <button
            className={`flex-1 pb-2 text-sm font-medium transition-colors ${
              mode === 'register'
                ? 'text-primary border-b-2 border-primary'
                : 'text-muted-foreground hover:text-foreground/80'
            }`}
            onClick={() => { setMode('register'); setError(''); }}
          >
            Register
          </button>
        </div>

        {/* Email/password form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === 'register' && (
            <>
              <Input
                type="text"
                placeholder={t('auth.display_name')}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
              <Input
                type="text"
                placeholder={t('auth.invite_code')}
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                required
              />
              {/* Invite preview callout — only renders when the
                  /auth/invite-preview probe succeeds.  Gives the
                  recipient a trust anchor ("you're being invited to
                  ACME as Driver by Alice") BEFORE they submit and
                  burn the single-use code.  If they expected a
                  different account/role, they can stop here and ask
                  out-of-band without consuming the invite. */}
              {invitePreview && (
                <div className="rounded-md border border-primary/30 bg-primary/5 p-3 text-xs">
                  <p className="font-medium text-foreground">
                    {invitePreview.inviter_display_name} invited you to{' '}
                    <strong>{invitePreview.account_name}</strong> as a{' '}
                    <strong>{invitePreview.role_label}</strong>
                    {invitePreview.truck_num && (
                      <> — Truck #{invitePreview.truck_num}</>
                    )}
                  </p>
                  <p className="text-muted-foreground mt-1">
                    Not what you expected? Stop here and ask the
                    sender — submitting will consume the invite.
                  </p>
                </div>
              )}
            </>
          )}
          <Input
            type="email"
            placeholder={t('auth.email')}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder={t('auth.password')}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />

          {mode === 'login' && (
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-4 h-4 rounded border-border accent-primary cursor-pointer"
                />
                <span className="text-sm text-muted-foreground">{t('auth.remember_me')}</span>
              </label>
              <a
                href="/forgot-password"
                className="text-xs text-primary hover:underline"
              >
                {t('auth.forgot_password')}
              </a>
            </div>
          )}

          {error && (
            <p className="text-danger text-xs">{error}</p>
          )}

          {/* Surface the "verify your email" flow when the API tells us
              the account is unverified.  The user can click to send a
              fresh verification link without re-entering credentials. */}
          {needsVerification && (
            <UnverifiedEmailNotice
              email={email}
              onResent={() => setError(t('auth.verification_resent'))}
            />
          )}

          {/* Welcome message after successful registration — the API
              tells us the user must verify their email before signing
              in, so we replace the form-error with an actionable note. */}
          {registeredEmail && mode === 'register' && (
            <div className={`p-3 border rounded-md text-xs ${toneClasses('ok')}`}>
              {t('auth.register_check_inbox', { email: registeredEmail })}
            </div>
          )}

          <Button
            type="submit"
            disabled={loading}
            className="w-full"
          >
            {loading ? '...' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </Button>
        </form>

        {/* Divider */}
        <div className="flex items-center my-5">
          <div className="flex-1 border-t border-border" />
          <span className="px-3 text-xs text-muted-foreground">or</span>
          <div className="flex-1 border-t border-border" />
        </div>

        {/* Telegram widget */}
        <div ref={containerRef} className="flex justify-center" />

        {!showDisconnect ? (
          <div className="flex justify-center mt-2">
            <button
              type="button"
              onClick={() => setShowDisconnect(true)}
              className="text-xs text-muted-foreground hover:text-foreground/80 transition-colors underline underline-offset-2"
            >
              {t('login_tg.disconnect_session')}
            </button>
          </div>
        ) : (
          <div className="mt-3 p-3 bg-muted border border-border rounded-lg text-xs text-foreground/80 space-y-3">
            <p className="font-medium text-foreground/90">{t('login_tg.switch_account')}</p>

            <ol className="list-decimal list-inside space-y-1.5 text-muted-foreground [&_b]:text-foreground/80">
              <li dangerouslySetInnerHTML={{ __html: t('login_tg.step_open_telegram') }} />
              <li dangerouslySetInnerHTML={{ __html: t('login_tg.step_search_service') }} />
              <li dangerouslySetInnerHTML={{ __html: t('login_tg.step_find_message', { bot: botUsername }) }} />
              <li dangerouslySetInnerHTML={{ __html: t('login_tg.step_tap_disconnect') }} />
              <li dangerouslySetInnerHTML={{ __html: t('login_tg.step_refresh_widget') }} />
            </ol>

            <div className="flex gap-2 pt-1">
              <Button
                type="button"
                onClick={handleRefreshWidget}
                className="flex-1 text-xs h-8"
              >
                {t('login_tg.refresh_widget')}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowDisconnect(false)}
                className="flex-1 text-xs h-8"
              >
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        )}

        {/* Divider before bot login */}
        <div className="flex items-center my-5">
          <div className="flex-1 border-t border-border" />
          <span className="px-3 text-xs text-muted-foreground">{t('login_tg.or_separator')}</span>
          <div className="flex-1 border-t border-border" />
        </div>

        {/* Bot-login flow */}
        {botLoginStatus === 'idle' && (
          <button
            type="button"
            onClick={startBotLogin}
            className="w-full py-2.5 bg-muted hover:bg-muted/80 border border-border text-foreground text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            <span>🤖</span>
            <span>{t('login_tg.login_via_bot')}</span>
          </button>
        )}

        {botLoginStatus === 'pending' && (
          <div className="p-4 bg-muted border border-border rounded-lg space-y-3">
            <div className="flex items-center gap-2">
              <div className="animate-spin h-4 w-4 border-2 border-primary border-t-transparent rounded-full" />
              <span className="text-sm text-foreground/90">{t('login_tg.waiting_approval')}</span>
            </div>
            <p
              className="text-xs text-muted-foreground [&_b]:text-foreground/80"
              dangerouslySetInnerHTML={{
                __html: t('login_tg.bot_link_opened', {
                  // Parse the username out of the deep link the API
                  // just returned (``https://t.me/<bot>?start=...``)
                  // instead of hardcoding ``app_4truck_bot``.  That
                  // hardcode showed the system bot's name even after
                  // the backend correctly resolved the LOGIN bot.
                  bot: botLoginLink.match(/t\.me\/([^?/]+)/)?.[1] || '',
                }),
              }}
            />
            {botLoginLink && (
              <a
                href={botLoginLink}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-center text-xs text-primary hover:text-primary/80 underline underline-offset-2"
              >
                {t('login_tg.didnt_open')}
              </a>
            )}
            <button
              type="button"
              onClick={cancelBotLogin}
              className="w-full py-1.5 bg-muted hover:bg-muted/80 text-foreground/80 text-xs font-medium rounded transition-colors"
            >
              {t('common.cancel')}
            </button>
          </div>
        )}

        {(botLoginStatus === 'rejected' || botLoginStatus === 'expired') && (
          <div className="p-4 bg-muted border border-danger-bd rounded-lg space-y-3">
            <p className="text-sm text-danger">
              {botLoginStatus === 'rejected' ? '❌ Login was rejected' : '⏰ Login link expired'}
            </p>
            <button
              type="button"
              onClick={() => { cancelBotLogin(); }}
              className="w-full py-1.5 bg-muted hover:bg-muted/80 text-foreground/80 text-xs font-medium rounded transition-colors"
            >
              Try again
            </button>
          </div>
        )}

        {botLoginStatus === 'approved' && (
          <div className="p-4 bg-muted border border-ok-bd rounded-lg">
            <p className="text-sm text-ok flex items-center gap-2">
              <span>✅</span> Login approved — redirecting...
            </p>
          </div>
        )}

        <p className="text-xs text-muted-foreground mt-4 text-center">
          {mode === 'register'
            ? 'Ask your company admin for an invite code.'
            : 'Sign in with your email or Telegram account.'}
        </p>
      </div>
    </div>
  );
}


/**
 * Inline notice rendered when the login API tells us the user hasn't
 * redeemed their verification link yet.  Carries a "resend" button
 * that calls /auth/resend-verification.  The endpoint always returns
 * 200 regardless of whether the email is registered, so this is safe
 * to expose without leaking account existence to a probing attacker.
 */
function UnverifiedEmailNotice({
  email, onResent,
}: {
  email: string;
  onResent: () => void;
}) {
  const { t } = useTranslation();
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  const handleResend = async () => {
    if (sending || sent) return;
    setSending(true);
    try {
      await apiJSON('/auth/resend-verification', {
        method: 'POST',
        body: { email },
      });
      setSent(true);
      onResent();
    } catch {
      // Endpoint is intentionally always 200, but if a network
      // failure trips us mid-flight, fall back silently — the user
      // can click again.
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={`p-3 border rounded-md text-xs space-y-2 ${toneClasses('warn')}`}>
      <p>{t('auth.error_email_not_verified')}</p>
      <button
        type="button"
        onClick={handleResend}
        disabled={sending || sent}
        className={`underline hover:no-underline disabled:opacity-50 ${toneText('warn')}`}
      >
        {sent
          ? t('auth.verification_resent')
          : sending
            ? t('auth.verification_resending')
            : t('auth.verification_resend_button')}
      </button>
    </div>
  );
}

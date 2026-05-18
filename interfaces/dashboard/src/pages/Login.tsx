import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';
import { setToken } from '../api/client';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
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

  // Pre-fill invite code from URL ?invite=XXXX
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('invite');
    if (code) {
      setInviteCode(code);
      setMode('register');
    }
  }, []);

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
    setLoading(true);
    try {
      if (mode === 'login') {
        await loginWithEmail(email, password, rememberMe);
      } else {
        await registerWithEmail(email, password, displayName, inviteCode);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold mb-2">4truck</h1>
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
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className="w-4 h-4 rounded border-border accent-primary cursor-pointer"
              />
              <span className="text-sm text-muted-foreground">{t('auth.remember_me')}</span>
            </label>
          )}

          {error && (
            <p className="text-destructive text-xs">{error}</p>
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
              dangerouslySetInnerHTML={{ __html: t('login_tg.bot_link_opened', { bot: 'app_4truck_bot' }) }}
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
          <div className="p-4 bg-muted border border-destructive/30 rounded-lg space-y-3">
            <p className="text-sm text-red-600 dark:text-red-400">
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
          <div className="p-4 bg-muted border border-green-600/50 dark:border-green-800/50 rounded-lg">
            <p className="text-sm text-green-600 dark:text-green-400 flex items-center gap-2">
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

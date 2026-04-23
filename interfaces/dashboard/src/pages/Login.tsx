import { useEffect, useRef, useState, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { setToken } from '../api/client';
import type { TelegramLoginData } from '../types';

type Mode = 'login' | 'register';

export default function Login() {
  const { loginWithTelegram, loginWithEmail, registerWithEmail } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [inviteCode, setInviteCode] = useState('');
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
            setToken(result.access_token);
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
        await loginWithTelegram(tgUser);
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
  }, [loginWithTelegram, widgetKey]);

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
        await loginWithEmail(email, password);
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
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-950 px-4">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold mb-2">🚛 4truck</h1>
        <p className="text-gray-400">Fleet Management Dashboard</p>
      </div>

      <div className="bg-gray-900 rounded-xl p-8 shadow-lg border border-gray-800 w-full max-w-sm">
        {/* Tab switcher */}
        <div className="flex mb-6 border-b border-gray-700">
          <button
            className={`flex-1 pb-2 text-sm font-medium transition-colors ${
              mode === 'login'
                ? 'text-blue-400 border-b-2 border-blue-400'
                : 'text-gray-500 hover:text-gray-300'
            }`}
            onClick={() => { setMode('login'); setError(''); }}
          >
            Sign In
          </button>
          <button
            className={`flex-1 pb-2 text-sm font-medium transition-colors ${
              mode === 'register'
                ? 'text-blue-400 border-b-2 border-blue-400'
                : 'text-gray-500 hover:text-gray-300'
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
              <input
                type="text"
                placeholder="Display name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
              />
              <input
                type="text"
                placeholder="Invite code"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                required
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
              />
            </>
          )}
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />

          {error && (
            <p className="text-red-400 text-xs">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {loading ? '...' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>

        {/* Divider */}
        <div className="flex items-center my-5">
          <div className="flex-1 border-t border-gray-700" />
          <span className="px-3 text-xs text-gray-500">or</span>
          <div className="flex-1 border-t border-gray-700" />
        </div>

        {/* Telegram widget */}
        <div ref={containerRef} className="flex justify-center" />

        {!showDisconnect ? (
          <div className="flex justify-center mt-2">
            <button
              type="button"
              onClick={() => setShowDisconnect(true)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors underline underline-offset-2"
            >
              Disconnect Telegram session
            </button>
          </div>
        ) : (
          <div className="mt-3 p-3 bg-gray-800 border border-gray-700 rounded-lg text-xs text-gray-300 space-y-3">
            <p className="font-medium text-gray-200">Switch Telegram account:</p>

            <ol className="list-decimal list-inside space-y-1.5 text-gray-400">
              <li>Open your <b className="text-gray-300">Telegram</b> app on phone or desktop</li>
              <li>Go to <b className="text-gray-300">Telegram</b> service chat — tap the search icon 🔍 and type <b className="text-gray-300">"Telegram"</b>, then select the one with ✅ verified badge that says <b className="text-gray-300">"service notifications"</b></li>
              <li>Scroll to the message that says <b className="text-gray-300">"You have successfully logged in on 4truck.us via @{botUsername}"</b></li>
              <li>Tap the <b className="text-blue-400">"Disconnect"</b> button below that message</li>
              <li>Come back here and press <b className="text-gray-300">"Refresh widget"</b> below</li>
            </ol>

            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={handleRefreshWidget}
                className="flex-1 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium rounded transition-colors"
              >
                Refresh widget
              </button>
              <button
                type="button"
                onClick={() => setShowDisconnect(false)}
                className="flex-1 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium rounded transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Divider before bot login */}
        <div className="flex items-center my-5">
          <div className="flex-1 border-t border-gray-700" />
          <span className="px-3 text-xs text-gray-500">or</span>
          <div className="flex-1 border-t border-gray-700" />
        </div>

        {/* Bot-login flow */}
        {botLoginStatus === 'idle' && (
          <button
            type="button"
            onClick={startBotLogin}
            className="w-full py-2.5 bg-gray-800 hover:bg-gray-700 border border-gray-600 text-white text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            <span>🤖</span>
            <span>Login via Telegram Bot</span>
          </button>
        )}

        {botLoginStatus === 'pending' && (
          <div className="p-4 bg-gray-800 border border-gray-700 rounded-lg space-y-3">
            <div className="flex items-center gap-2">
              <div className="animate-spin h-4 w-4 border-2 border-blue-400 border-t-transparent rounded-full" />
              <span className="text-sm text-gray-200">Waiting for approval...</span>
            </div>
            <p className="text-xs text-gray-400">
              A link to <b className="text-gray-300">@app_4truck_bot</b> was opened.
              Tap <b className="text-gray-300">Start</b> in the bot to approve your login.
            </p>
            {botLoginLink && (
              <a
                href={botLoginLink}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-center text-xs text-blue-400 hover:text-blue-300 underline underline-offset-2"
              >
                Didn't open? Click here to open the bot
              </a>
            )}
            <button
              type="button"
              onClick={cancelBotLogin}
              className="w-full py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium rounded transition-colors"
            >
              Cancel
            </button>
          </div>
        )}

        {(botLoginStatus === 'rejected' || botLoginStatus === 'expired') && (
          <div className="p-4 bg-gray-800 border border-red-800/50 rounded-lg space-y-3">
            <p className="text-sm text-red-400">
              {botLoginStatus === 'rejected' ? '❌ Login was rejected' : '⏰ Login link expired'}
            </p>
            <button
              type="button"
              onClick={() => { cancelBotLogin(); }}
              className="w-full py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium rounded transition-colors"
            >
              Try again
            </button>
          </div>
        )}

        {botLoginStatus === 'approved' && (
          <div className="p-4 bg-gray-800 border border-green-800/50 rounded-lg">
            <p className="text-sm text-green-400 flex items-center gap-2">
              <span>✅</span> Login approved — redirecting...
            </p>
          </div>
        )}

        <p className="text-xs text-gray-500 mt-4 text-center">
          {mode === 'register'
            ? 'Ask your company admin for an invite code.'
            : 'Sign in with your email or Telegram account.'}
        </p>
      </div>
    </div>
  );
}

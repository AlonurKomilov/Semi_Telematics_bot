import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
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
      let botUsername = 'SemiTelematicsBot';
      try {
        const res = await fetch('/api/auth/config');
        if (res.ok) {
          const data = await res.json();
          botUsername = data.bot_username || botUsername;
        }
      } catch { /* use fallback */ }

      el.innerHTML = '';
      const script = document.createElement('script');
      script.src = 'https://telegram.org/js/telegram-widget.js?22';
      script.async = true;
      script.setAttribute('data-telegram-login', botUsername);
      script.setAttribute('data-size', 'large');
      script.setAttribute('data-radius', '8');
      script.setAttribute('data-onauth', '__onTelegramAuth(user)');
      script.setAttribute('data-request-access', 'write');
      el.appendChild(script);
    })();

    return () => { delete window.__onTelegramAuth; };
  }, [loginWithTelegram]);

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

        <p className="text-xs text-gray-500 mt-4 text-center">
          {mode === 'register'
            ? 'Ask your company admin for an invite code.'
            : 'Sign in with your email or Telegram account.'}
        </p>
      </div>
    </div>
  );
}

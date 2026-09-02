import { useState } from 'react';
import { apiJSON, setToken } from '../api/client';

/**
 * Email + password, straight to /auth/login with ``client: "extension"``.
 * The server answers with a token SCOPED to the live map and labels the
 * session "Browser extension" in Active Sessions — so it can be revoked
 * from the dashboard like any other device.
 */
export default function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      const out = await apiJSON<{ access_token: string }>('/auth/login', {
        method: 'POST',
        body: { email, password, remember_me: true, client: 'extension' },
      });
      await setToken(out.access_token);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} style={{ padding: 16, display: 'grid', gap: 10 }}>
      <h1 style={{ fontSize: 16, margin: 0 }}>Sign in to 4truck</h1>
      <p className="muted" style={{ margin: 0 }}>
        This panel can see your trucks&apos; positions and nothing else.
      </p>
      <input className="input" type="email" placeholder="Email" value={email}
             onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
      <input className="input" type="password" placeholder="Password" value={password}
             onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      {error && <p style={{ color: 'var(--danger)', margin: 0 }}>{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  );
}

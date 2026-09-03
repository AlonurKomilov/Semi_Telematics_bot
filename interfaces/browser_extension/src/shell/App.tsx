import { Suspense, useEffect, useState } from 'react';
import { apiJSON, clearToken, getToken, refreshIfNeeded, UnauthorizedError } from '../api/client';
import { FEATURES } from './registry';
import Connect from './Connect';
import Settings from './Settings';
import UserMenu, { type Me } from './UserMenu';

type Phase = 'loading' | 'login' | 'ready';
type View = 'feature' | 'settings';

/** GET /extension/me — an avatar's worth, by design nothing more. */
interface MeWire { display_name?: string | null; role?: string | null; account_name?: string | null }

export default function App() {
  const [phase, setPhase] = useState<Phase>('loading');
  const [view, setView] = useState<View>('feature');
  const [featureId] = useState(FEATURES[0].id);
  const [me, setMe] = useState<Me | null>(null);
  // Why the connect screen is showing again — a session disconnected
  // from the profile (or expired) looks different from a first run.
  const [disconnected, setDisconnected] = useState(false);

  useEffect(() => {
    (async () => {
      await refreshIfNeeded();
      setPhase((await getToken()) ? 'ready' : 'login');
    })();
    // A 401 anywhere returns the panel to the connect screen.
    const onUnauthorized = (e: PromiseRejectionEvent) => {
      if (e.reason instanceof UnauthorizedError) { e.preventDefault(); setPhase('login'); setMe(null); setDisconnected(true); }
    };
    window.addEventListener('unhandledrejection', onUnauthorized);
    // The token leaving storage is THE disconnect signal, whoever
    // caused it: "Disconnect this session" on the profile makes the
    // next request 401, the client drops the token, and this fires —
    // including for the quiet 5-second poll that swallows its errors.
    const onStorage = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area !== 'local' || !('jwt' in changes)) return;
      if (changes.jwt.newValue === undefined) { setPhase('login'); setMe(null); setDisconnected(true); }
    };
    chrome.storage.onChanged.addListener(onStorage);
    return () => {
      window.removeEventListener('unhandledrejection', onUnauthorized);
      chrome.storage.onChanged.removeListener(onStorage);
    };
  }, []);

  // Who is connected — for the avatar and its menu.  Best effort: the
  // panel works without it, the avatar just shows "4".  Not /user/me:
  // that answer is the whole account profile, and this token is a
  // live-map key — /extension/me returns three display strings.
  useEffect(() => {
    if (phase !== 'ready') return;
    let cancelled = false;
    apiJSON<MeWire>('/extension/me')
      .then((w) => { if (!cancelled) setMe({ name: w.display_name ?? null, role: w.role ?? null, account_name: w.account_name ?? null }); })
      .catch(() => { if (!cancelled) setMe(null); });
    return () => { cancelled = true; };
  }, [phase]);

  if (phase === 'loading') return <p className="muted" style={{ padding: 16 }}>Loading…</p>;
  if (phase === 'login') {
    return <Connect disconnected={disconnected} onDone={() => { setDisconnected(false); setPhase('ready'); }} />;
  }

  const feature = FEATURES.find((f) => f.id === featureId) ?? FEATURES[0];
  // Own choice, not a revocation: the connect screen reads as a first run.
  const disconnect = async () => { setDisconnected(false); await clearToken(); setMe(null); setView('feature'); setPhase('login'); };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header className="row" style={{ padding: '6px 12px', borderBottom: '1px solid var(--border)', justifyContent: 'space-between' }}>
        <strong>4truck · {view === 'settings' ? 'Settings' : feature.label}</strong>
        <UserMenu me={me} onSettings={() => setView('settings')} onDisconnect={() => void disconnect()} />
      </header>
      <main style={{ flex: 1, minHeight: 0, overflowY: view === 'settings' ? 'auto' : undefined }}>
        {view === 'settings' ? (
          <Settings onBack={() => setView('feature')} />
        ) : (
          <Suspense fallback={<p className="muted" style={{ padding: 16 }}>Loading…</p>}>
            <feature.Component />
          </Suspense>
        )}
      </main>
    </div>
  );
}

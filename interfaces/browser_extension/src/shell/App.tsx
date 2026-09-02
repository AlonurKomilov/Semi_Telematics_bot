import { Suspense, useEffect, useState } from 'react';
import { clearToken, getToken, refreshIfNeeded, UnauthorizedError } from '../api/client';
import { FEATURES } from './registry';
import Login from './Login';

type Phase = 'loading' | 'login' | 'ready';

export default function App() {
  const [phase, setPhase] = useState<Phase>('loading');
  const [featureId] = useState(FEATURES[0].id);

  useEffect(() => {
    (async () => {
      await refreshIfNeeded();
      setPhase((await getToken()) ? 'ready' : 'login');
    })();
    // A 401 anywhere returns the panel to the login screen.
    const onUnauthorized = (e: PromiseRejectionEvent) => {
      if (e.reason instanceof UnauthorizedError) { e.preventDefault(); setPhase('login'); }
    };
    window.addEventListener('unhandledrejection', onUnauthorized);
    return () => window.removeEventListener('unhandledrejection', onUnauthorized);
  }, []);

  if (phase === 'loading') return <p className="muted" style={{ padding: 16 }}>Loading…</p>;
  if (phase === 'login') return <Login onDone={() => setPhase('ready')} />;

  const feature = FEATURES.find((f) => f.id === featureId) ?? FEATURES[0];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header className="row" style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)', justifyContent: 'space-between' }}>
        <strong>4truck · {feature.label}</strong>
        <button className="btn" onClick={async () => { await clearToken(); setPhase('login'); }}>Sign out</button>
      </header>
      <main style={{ flex: 1, minHeight: 0 }}>
        <Suspense fallback={<p className="muted" style={{ padding: 16 }}>Loading…</p>}>
          <feature.Component />
        </Suspense>
      </main>
    </div>
  );
}

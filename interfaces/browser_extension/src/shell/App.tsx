import { Suspense, useEffect, useRef, useState } from 'react';
import { apiFetch, apiJSON, clearToken, getToken, refreshIfNeeded, UnauthorizedError } from '../api/client';
import { FEATURES } from './registry';
import Connect from './Connect';
import Settings from './Settings';
import UserMenu, { type Me } from './UserMenu';
import FeatureMenu from './FeatureMenu';
import { ACTIVE_FEATURE_KEY } from '../features/maps-overlay/bridge';
import { forgetInventory } from '../features/inventory/data';

type Phase = 'loading' | 'login' | 'ready';
type View = 'feature' | 'settings';

/** GET /extension/me — an avatar's worth, by design nothing more. */
interface MeWire {
  display_name?: string | null; role?: string | null; account_name?: string | null;
  /** Feature ids this person may open — the server's answer, in the
   *  panel's vocabulary.  Absent from an older API: then the panel shows
   *  what it has always shown rather than nothing. */
  features?: string[] | null;
}

/** The chosen feature, remembered like every other working preference
 *  here — somebody who works from Inventory works from it tomorrow.
 *  Declared with the overlay's contracts because it has two readers. */
const FEATURE_KEY = ACTIVE_FEATURE_KEY;

export default function App() {
  const [phase, setPhase] = useState<Phase>('loading');
  const [view, setView] = useState<View>('feature');
  const [featureId, setFeatureId] = useState(FEATURES[0].id);
  /** null = not answered yet (or an API without the field): fall back to
   *  the first feature, which is what this panel showed before there was
   *  a choice.  A failed /me must not empty the panel. */
  const [allowed, setAllowed] = useState<string[] | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  // Why the connect screen is showing again — a session disconnected
  // from the profile (or expired) looks different from a first run.
  const [disconnected, setDisconnected] = useState(false);
  // True while the person's own Disconnect runs, so the storage
  // listener below does not read it as a revocation.
  const ownDisconnect = useRef(false);

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
      if (changes.jwt.newValue === undefined) {
        setPhase('login'); setMe(null);
        if (!ownDisconnect.current) setDisconnected(true);
        // Consumed HERE, after it was read: storage change events are
        // delivered as their own dispatch, after disconnect() has moved on.
        ownDisconnect.current = false;
      }
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
  // The remembered choice, read once.  Applied only if that feature is
  // still one this person may open — grants change, and a stored id must
  // never reopen a door the server has since closed.
  useEffect(() => {
    void chrome.storage.local.get(FEATURE_KEY).then((got) => {
      const id = got[FEATURE_KEY];
      if (typeof id === 'string' && FEATURES.some((f) => f.id === id)) setFeatureId(id);
    });
  }, []);

  useEffect(() => {
    if (phase !== 'ready') return;
    let cancelled = false;
    apiJSON<MeWire>('/extension/me')
      .then((w) => {
        if (cancelled) return;
        setMe({ name: w.display_name ?? null, role: w.role ?? null, account_name: w.account_name ?? null });
        setAllowed(Array.isArray(w.features) ? w.features : null);
      })
      .catch(() => { if (!cancelled) setMe(null); });
    return () => { cancelled = true; };
  }, [phase]);

  if (phase === 'loading') return <p className="muted" style={{ padding: 16 }}>Loading…</p>;
  if (phase === 'login') {
    return <Connect disconnected={disconnected} onDone={() => { setDisconnected(false); setPhase('ready'); }} />;
  }

  const available = allowed === null
    ? FEATURES.slice(0, 1)
    : FEATURES.filter((f) => allowed.includes(f.id));
  // An account whose grants match nothing still gets a surface rather
  // than a blank panel; Live Map says its own "this connection cannot
  // read the live map" when that is the truth.
  const offered = available.length > 0 ? available : FEATURES.slice(0, 1);
  const feature = offered.find((f) => f.id === featureId) ?? offered[0];
  const pickFeature = (id: string) => {
    setFeatureId(id);
    // One key, two readers: the panel remembers the choice and the
    // overlay's card on google.com/maps reads it to label its button —
    // "for levels & more" is a lie while the panel is on Inventory.
    void chrome.storage.local.set({ [FEATURE_KEY]: id });
  };
  // Own choice, not a revocation: the session is ended on the server
  // too (the Active Sessions row goes), then the token is dropped
  // whatever the server said, and the connect screen reads as a first run.
  const disconnect = async () => {
    ownDisconnect.current = true;
    setDisconnected(false);
    try { await apiFetch('/auth/logout', { method: 'POST' }); } catch { /* the token is dropped either way; the row expires on its own */ }
    finally { await clearToken(); }
    // The next person to connect may be a different one with different
    // grants: what one truck's inventory was, and whether Inventory was
    // permitted at all, are answers to THAT session's key, not this one's.
    forgetInventory();
    setMe(null); setView('feature'); setPhase('login');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header className="row" style={{ padding: '6px 12px', borderBottom: '1px solid var(--border)', justifyContent: 'space-between' }}>
        {view === 'settings'
          ? <strong>4truck · Settings</strong>
          : <FeatureMenu features={offered} current={feature} onPick={pickFeature} />}
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

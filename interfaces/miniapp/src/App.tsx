import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { AppRoot, Placeholder, Spinner } from '@telegram-apps/telegram-ui';
import { Icon24LockOutline } from '@vkontakte/icons';
import { useTelegram, useBackButton } from './hooks/useTelegram';
import { setToken, apiJSON } from './api/client';import { BottomNav } from './components/BottomNav';
import { OfflineBanner } from './components/OfflineBanner';
import { MapPage } from './pages/MapPage';
// Non-map pages are lazy-loaded so their JS (and any deps unique to them)
// is fetched only when the user first navigates to that tab.  After the
// first visit each page stays mounted (display:none when inactive) so
// component state survives subsequent tab switches.
const VehiclesPage = lazy(() => import('./pages/VehiclesPage').then(m => ({ default: m.VehiclesPage })));
const PTIPage = lazy(() => import('./pages/PTIPage').then(m => ({ default: m.PTIPage })));
const AlertsPage = lazy(() => import('./pages/AlertsPage').then(m => ({ default: m.AlertsPage })));
const ScorecardPage = lazy(() => import('./pages/ScorecardPage').then(m => ({ default: m.ScorecardPage })));
const AIChatPage = lazy(() => import('./pages/AIChatPage').then(m => ({ default: m.AIChatPage })));
const ProfilePage = lazy(() => import('./pages/ProfilePage').then(m => ({ default: m.ProfilePage })));
import type { Page } from './types';

type AuthState = 'loading' | 'ok' | 'error';

/** Minimal slice of /api/user/me we need in App for routing decisions. */
interface UserMeBasic {
  role: string;
  permissions: Record<string, boolean>;
}

function getHashPage(): Page {
  const hash = window.location.hash.replace('#', '');
  return (['map', 'vehicles', 'pti', 'alerts', 'scorecard', 'ai', 'profile'] as Page[]).includes(hash as Page)
    ? (hash as Page)
    : 'map';
}

export default function App() {
  const tg = useTelegram();
  const [authState, setAuthState] = useState<AuthState>('loading');
  const [page, setPage] = useState<Page>(getHashPage());
  const [alertCount, setAlertCount] = useState(0);
  const [alertBadgeVersion, setAlertBadgeVersion] = useState(0);
  const [userPerms, setUserPerms] = useState<Record<string, boolean>>({});
  const [userRole, setUserRole] = useState<string>('driver');
  // Lazy-loaded pages stay mounted after first visit so component state
  // survives subsequent tab switches.  Map is always considered visited
  // because it is the default landing tab.
  const [visited, setVisited] = useState<Set<Page>>(() => new Set([getHashPage(), 'map']));
  const pollRef = useRef<number | null>(null);
  // Timestamp of the last user-driven badge update (an ack/bulk-ack from
  // the Alerts page).  The poller respects this for 90s so a still-cached
  // server count can't snap the badge back up after the user just zeroed
  // it.  New alerts (count > local) still flow through immediately.
  const lastLocalCountAtRef = useRef<number>(0);
  // Remember each page's scrollTop so a tab switch restores the user's
  // place instead of snapping back to the top.  Pages are kept mounted
  // (display:none) but their scroll containers are reset by the browser
  // on display flip-flop, hence this manual save/restore.
  const scrollMemoryRef = useRef<Record<string, number>>({});
  const pageRefs = {
    map: useRef<HTMLDivElement | null>(null),
    vehicles: useRef<HTMLDivElement | null>(null),
    pti: useRef<HTMLDivElement | null>(null),
    alerts: useRef<HTMLDivElement | null>(null),
    scorecard: useRef<HTMLDivElement | null>(null),
    ai: useRef<HTMLDivElement | null>(null),
    profile: useRef<HTMLDivElement | null>(null),
  } as const;
  const prevPageRef = useRef<Page>(page);

  useEffect(() => {
    // Signal to Telegram: expand to full height
    tg.expand();

    // Sync Telegram's header and background color to match the app theme.
    // This makes the status bar and header bar seamlessly blend with the app,
    // like BotFather and CryptoBot do.
    const webApp = window.Telegram?.WebApp;
    if (webApp) {
      const bg = webApp.themeParams.bg_color;
      const headerBg = webApp.themeParams.secondary_bg_color ?? webApp.themeParams.bg_color;
      if (bg) webApp.setBackgroundColor(bg);
      if (headerBg) webApp.setHeaderColor(headerBg);
    }

    // Signal to Telegram that the app is ready (hides loading screen)
    tg.ready();

    // Authenticate
    authenticate();

    // Hash-based routing
    const onHash = () => {
      const next = getHashPage();
      setPage(next);
      setVisited(prev => (prev.has(next) ? prev : new Set(prev).add(next)));
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function authenticate() {
    try {
      if (tg.isMiniApp) {
        // Running inside Telegram — exchange initData for a JWT
        const resp = await fetch('/api/auth/telegram', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ init_data: tg.initData }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error((err as { detail?: string }).detail ?? 'Auth failed');
        }
        const data = (await resp.json()) as { access_token: string };
        setToken(data.access_token);
        await loadUserProfile();
        setAuthState('ok');
      } else {
        // Browser fallback: use a previously stored session token
        const stored = sessionStorage.getItem('st_token');
        if (stored) {
          setToken(stored);
          await loadUserProfile();
          setAuthState('ok');
        } else {
          setAuthState('error');
        }
      }
    } catch (err) {
      console.error('Auth error:', err);
      setAuthState('error');
    }
  }

  async function loadUserProfile() {
    try {
      const me = await apiJSON<UserMeBasic>('/api/user/me');
      setUserPerms(me.permissions);
      setUserRole(me.role);
      // If the initial page is one the user cannot access, redirect to map.
      const initial = getHashPage();
      if (!canAccessPage(initial, me.permissions)) {
        window.location.hash = 'map';
        setPage('map');
      }
    } catch {
      // Non-fatal — continue with empty perms (pages handle their own 403s)
    }
  }

  function canAccessPage(p: Page, perms: Record<string, boolean>): boolean {
    if (p === 'alerts') return !!(perms.can_alerts_all || perms.can_alerts_vehicle);
    if (p === 'scorecard') return !!(perms.can_scorecard_all || perms.can_scorecard_vehicle);
    if (p === 'ai') return !!(perms.can_vehicle_all || perms.can_vehicle_vehicle);
    return true; // map, vehicles, profile always accessible
  }

  // History stack so the Telegram BackButton walks back through the
  // user's actual path (Map → Vehicles → Profile pops to Vehicles, not
  // Map).  Map is always the bottom of the stack — pressing back from
  // map closes the mini app.  Stored in a ref because no UI reads it
  // directly; only navigation logic mutates it.
  const historyRef = useRef<Page[]>(['map']);

  function navigate(p: Page) {
    // Silently redirect to map if the user taps a tab they can't access.
    if (!canAccessPage(p, userPerms)) {
      p = 'map';
    }
    setPage(p);
    setVisited(prev => (prev.has(p) ? prev : new Set(prev).add(p)));
    window.location.hash = p;
    const stack = historyRef.current;
    // Don't push the same page twice in a row.  If the page is already
    // on the stack, slice back to it instead of growing forever.
    if (stack[stack.length - 1] === p) return;
    const idx = stack.lastIndexOf(p);
    historyRef.current = idx >= 0 ? stack.slice(0, idx + 1) : [...stack, p];
  }

  // Telegram BackButton: visible on every page except map; tap pops the
  // history stack so the user steps backward through their own path.
  useBackButton(authState === 'ok' && page !== 'map', () => {
    const stack = historyRef.current;
    if (stack.length <= 1) {
      setPage('map');
      window.location.hash = 'map';
      historyRef.current = ['map'];
      return;
    }
    const next = stack.slice(0, -1);
    const dest = next[next.length - 1];
    setPage(dest);
    window.location.hash = dest;
    historyRef.current = next;
  });

  // Save / restore scrollTop on tab switches.  Runs *synchronously* with
  // the layout flip via useEffect so the user never sees the page snap
  // to top before being scrolled back.
  useEffect(() => {
    const prev = prevPageRef.current;
    if (prev !== page) {
      const prevEl = pageRefs[prev]?.current;
      if (prevEl) scrollMemoryRef.current[prev] = prevEl.scrollTop;
      const nextEl = pageRefs[page]?.current;
      if (nextEl) nextEl.scrollTop = scrollMemoryRef.current[page] ?? 0;
      prevPageRef.current = page;
    }
    // pageRefs is a stable object literal; we only depend on `page`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  // Wrap setAlertCount so the Alerts page's local updates (ack, bulk-ack)
  // also stamp the freshness ref — the poller needs that to know the
  // user just hit zero and the cached server count is stale.
  const handleAlertCountChange = useCallback((c: number) => {
    lastLocalCountAtRef.current = Date.now();
    setAlertCount(c);
  }, []);

  // Poll alert count once authed, every 60 s, so the tab badge stays current
  // even if the user never opens the alerts tab.
  useEffect(() => {
    if (authState !== 'ok') return;
    let cancelled = false;
    const ACK_GRACE_MS = 90_000; // how long the local count is authoritative after an ack
    async function tick() {
      try {
        const data = await apiJSON<{ count?: number }>('/api/alerts/pending/count');
        if (cancelled) return;
        const c = typeof data.count === 'number' ? data.count : 0;
        setAlertCount(prev => {
          // Within the ack-grace window, only let the server *raise* the
          // count (new alerts).  Block stale-cache reverts that would
          // snap the badge back up after the user zeroed it.
          const sinceLocal = Date.now() - lastLocalCountAtRef.current;
          if (sinceLocal < ACK_GRACE_MS && c <= prev) {
            return prev;
          }
          if (c !== prev) setAlertBadgeVersion(v => v + 1);
          return c;
        });
      } catch {
        /* ignore — keep last value */
      }
    }
    tick();
    pollRef.current = window.setInterval(tick, 60_000);
    return () => {
      cancelled = true;
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [authState]);

  // AppRoot's `platform` prop only accepts 'ios' | 'base'; collapse Android
  // (and anything else) onto the 'base' (Material) appearance.
  const rootPlatform: 'ios' | 'base' = tg.platform === 'ios' ? 'ios' : 'base';

  // ── Loading screen ──────────────────────────────────────────

  if (authState === 'loading') {
    return (
      <AppRoot appearance={tg.colorScheme} platform={rootPlatform}>
        <div className="centered" style={{ height: '100vh' }}>
          <Spinner size="l" />
        </div>
      </AppRoot>
    );
  }

  // ── Auth error screen ───────────────────────────────────────

  if (authState === 'error') {
    return (
      <AppRoot appearance={tg.colorScheme} platform={rootPlatform}>
        <div className="centered" style={{ height: '100vh' }}>
          <Placeholder
            header="Authentication Required"
            description="Please open this app from the Telegram bot to continue."
          >
            <Icon24LockOutline width={48} height={48} aria-label="Authentication required" style={{ opacity: 0.4 }} />
          </Placeholder>
        </div>
      </AppRoot>
    );
  }

  // ── Main app ────────────────────────────────────────────────

  return (
    <AppRoot appearance={tg.colorScheme} platform={rootPlatform}>
      <OfflineBanner />
      <div className="app-layout">
        {/* Content area — all three pages are always mounted so the Leaflet
            map survives tab switches. Hidden pages use display:none. */}
        <main className="app-content">
          <div ref={pageRefs.map} className={`page${page !== 'map' ? ' page--hidden' : ''}`}>
            <MapPage
              active={page === 'map'}
              userPerms={userPerms}
              onNavigate={navigate}
            />
          </div>
          <Suspense fallback={null}>
            {visited.has('vehicles') && (
              <div ref={pageRefs.vehicles} className={`page${page !== 'vehicles' ? ' page--hidden' : ''}`}>
                <VehiclesPage active={page === 'vehicles'} onGoToMap={() => navigate('map')} />
              </div>
            )}
            {visited.has('pti') && (
              <div ref={pageRefs.pti} className={`page${page !== 'pti' ? ' page--hidden' : ''}`}>
                <PTIPage active={page === 'pti'} />
              </div>
            )}
            {visited.has('alerts') && (
              <div ref={pageRefs.alerts} className={`page${page !== 'alerts' ? ' page--hidden' : ''}`}>
                <AlertsPage active={page === 'alerts'} onCountChange={handleAlertCountChange} refreshKey={alertBadgeVersion} />
              </div>
            )}
            {visited.has('scorecard') && (
              <div ref={pageRefs.scorecard} className={`page${page !== 'scorecard' ? ' page--hidden' : ''}`}>
                <ScorecardPage userRole={userRole} />
              </div>
            )}
            {visited.has('ai') && (
              <div ref={pageRefs.ai} className={`page${page !== 'ai' ? ' page--hidden' : ''}`}>
                <AIChatPage active={page === 'ai'} userRole={userRole} />
              </div>
            )}
            {visited.has('profile') && (
              <div ref={pageRefs.profile} className={`page${page !== 'profile' ? ' page--hidden' : ''}`}>
                <ProfilePage />
              </div>
            )}
          </Suspense>
        </main>

        <BottomNav page={page} onNavigate={navigate} alertCount={alertCount} userPerms={userPerms} />
      </div>
    </AppRoot>
  );
}

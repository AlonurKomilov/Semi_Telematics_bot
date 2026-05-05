import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { AppRoot, Placeholder, Spinner } from '@telegram-apps/telegram-ui';
import { useTelegram, useBackButton } from './hooks/useTelegram';
import { setToken, apiJSON } from './api/client';import { BottomNav } from './components/BottomNav';
import { OfflineBanner } from './components/OfflineBanner';
import { MapPage } from './pages/MapPage';
// Non-map pages are lazy-loaded so their JS (and any deps unique to them)
// is fetched only when the user first navigates to that tab.  After the
// first visit each page stays mounted (display:none when inactive) so
// component state survives subsequent tab switches.
const VehiclesPage = lazy(() => import('./pages/VehiclesPage').then(m => ({ default: m.VehiclesPage })));
const AlertsPage = lazy(() => import('./pages/AlertsPage').then(m => ({ default: m.AlertsPage })));
const ScorecardPage = lazy(() => import('./pages/ScorecardPage').then(m => ({ default: m.ScorecardPage })));
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
  return (['map', 'vehicles', 'alerts', 'scorecard', 'profile'] as Page[]).includes(hash as Page)
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
    if (p === 'alerts') return !!(perms.can_alerts_all || perms.can_alerts_own);
    if (p === 'scorecard') return !!(perms.can_scorecard_all || perms.can_scorecard_own);
    return true; // map, vehicles, profile always accessible
  }

  function navigate(p: Page) {
    // Silently redirect to map if the user taps a tab they can't access.
    if (!canAccessPage(p, userPerms)) {
      p = 'map';
    }
    setPage(p);
    setVisited(prev => (prev.has(p) ? prev : new Set(prev).add(p)));
    window.location.hash = p;
  }

  // Telegram BackButton: visible on every page except map; tap returns to map.
  useBackButton(authState === 'ok' && page !== 'map', () => navigate('map'));

  // Poll alert count once authed, every 60 s, so the tab badge stays current
  // even if the user never opens the alerts tab.
  useEffect(() => {
    if (authState !== 'ok') return;
    let cancelled = false;
    async function tick() {
      try {
        const data = await apiJSON<{ count?: number }>('/api/alerts/pending/count');
        if (cancelled) return;
        const c = typeof data.count === 'number' ? data.count : 0;
        setAlertCount(prev => {
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
            🔒
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
        <div className="app-content">
          <div className={`page${page !== 'map' ? ' page--hidden' : ''}`}>
            <MapPage active={page === 'map'} />
          </div>
          <Suspense fallback={null}>
            {visited.has('vehicles') && (
              <div className={`page${page !== 'vehicles' ? ' page--hidden' : ''}`}>
                <VehiclesPage active={page === 'vehicles'} onGoToMap={() => navigate('map')} />
              </div>
            )}
            {visited.has('alerts') && (
              <div className={`page${page !== 'alerts' ? ' page--hidden' : ''}`}>
                <AlertsPage active={page === 'alerts'} onCountChange={setAlertCount} refreshKey={alertBadgeVersion} />
              </div>
            )}
            {visited.has('scorecard') && (
              <div className={`page${page !== 'scorecard' ? ' page--hidden' : ''}`}>
                <ScorecardPage userRole={userRole} />
              </div>
            )}
            {visited.has('profile') && (
              <div className={`page${page !== 'profile' ? ' page--hidden' : ''}`}>
                <ProfilePage />
              </div>
            )}
          </Suspense>
        </div>

        <BottomNav page={page} onNavigate={navigate} alertCount={alertCount} userPerms={userPerms} />
      </div>
    </AppRoot>
  );
}

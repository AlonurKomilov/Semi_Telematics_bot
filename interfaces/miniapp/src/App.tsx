import { useEffect, useState } from 'react';
import { AppRoot, Placeholder, Spinner } from '@telegram-apps/telegram-ui';
import { useTelegram } from './hooks/useTelegram';
import { setToken } from './api/client';
import { BottomNav } from './components/BottomNav';
import { MapPage } from './pages/MapPage';
import { TrucksPage } from './pages/TrucksPage';
import { AlertsPage } from './pages/AlertsPage';
import type { Page } from './types';

type AuthState = 'loading' | 'ok' | 'error';

function getHashPage(): Page {
  const hash = window.location.hash.replace('#', '');
  return (['map', 'trucks', 'alerts'] as Page[]).includes(hash as Page)
    ? (hash as Page)
    : 'map';
}

export default function App() {
  const tg = useTelegram();
  const [authState, setAuthState] = useState<AuthState>('loading');
  const [page, setPage] = useState<Page>(getHashPage());

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
    const onHash = () => setPage(getHashPage());
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
        setAuthState('ok');
      } else {
        // Browser fallback: use a previously stored session token
        const stored = sessionStorage.getItem('st_token');
        if (stored) {
          setToken(stored);
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

  function navigate(p: Page) {
    setPage(p);
    window.location.hash = p;
  }

  // ── Loading screen ──────────────────────────────────────────

  if (authState === 'loading') {
    return (
      <AppRoot appearance={tg.colorScheme} platform={tg.platform}>
        <div className="centered" style={{ height: '100vh' }}>
          <Spinner size="l" />
        </div>
      </AppRoot>
    );
  }

  // ── Auth error screen ───────────────────────────────────────

  if (authState === 'error') {
    return (
      <AppRoot appearance={tg.colorScheme} platform={tg.platform}>
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
    <AppRoot appearance={tg.colorScheme} platform={tg.platform}>
      <div className="app-layout">
        {/* Content area — all three pages are always mounted so the Leaflet
            map survives tab switches. Hidden pages use display:none. */}
        <div className="app-content">
          <div className={`page${page !== 'map' ? ' page--hidden' : ''}`}>
            <MapPage active={page === 'map'} />
          </div>
          <div className={`page${page !== 'trucks' ? ' page--hidden' : ''}`}>
            <TrucksPage onGoToMap={() => navigate('map')} />
          </div>
          <div className={`page${page !== 'alerts' ? ' page--hidden' : ''}`}>
            <AlertsPage />
          </div>
        </div>

        <BottomNav page={page} onNavigate={navigate} />
      </div>
    </AppRoot>
  );
}

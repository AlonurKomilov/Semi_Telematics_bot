import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { Toaster, toast } from 'sonner';
import { useNotifPosition } from './components/banners';
import App from './App';
import { AuthProvider } from './context/AuthContext';
import { RoleViewProvider } from './context/RoleViewContext';
import PreferencesSync from './preferences/PreferencesSync';
import { ThemeProvider, useTheme } from './context/ThemeContext';
import { TooltipProvider } from './components/ui/tooltip';
import { applyPublicFormTheme } from './features/applications/public/theme';
import MaintenanceOverlay from './components/MaintenanceOverlay';
import './i18n';  // initialise i18next before any component renders
import './index.css';

/** The MAIN app's toast/banner lane — position follows the user's
 * notification-position preference (the public-apply mount keeps a fixed
 * top-right: one-time visitors have no preferences). */
function AppToaster() {
  const position = useNotifPosition();
  // Sonner paints its own surface and defaults to LIGHT. On the three
  // dark themes that put a white toast over a near-black page — the
  // one element on screen that ignored the theme entirely. `light`
  // covers every non-dark colour, which today is just `light`.
  const { theme } = useTheme();
  const mode = theme.mode;
  // Sonner starts a top-positioned toast 32px down the viewport, which
  // lands it squarely on the 48px topbar — the banner covered the
  // navigation it was often reporting about. Clear the header instead,
  // and clear it at whatever height the Size setting has given it: the
  // header is h-12 on the `controls` region, so the offset reads the
  // same two variables the header does.
  const offset = position.startsWith('top')
    ? 'calc(3rem * var(--size-control, 1) * var(--size-region-controls, 1) + 0.5rem)'
    : undefined;
  return (
    <Toaster richColors theme={mode} position={position} closeButton offset={offset} />
  );
}

// Single shared QueryClient.  ``staleTime: 60s`` matches the server-side
// 120-second Samsara cache: by the time the user comes back to a tab the
// upstream data has likely refreshed once, but rapid navigation between
// pages reuses the cached payload.
//
// ``retry: 3`` with exponential backoff (1s → 2s → 4s, capped at 10s)
// hides API restarts from active users.  A ``make restart-api`` takes
// ~3-5s; nginx already retries upstream 3× within 30s, and this client-
// side retry covers the case where nginx itself gives up (very rare).
// Mutations (POST/PATCH/DELETE) intentionally have retry disabled — we
// can't tell whether a failed write was actually applied, so retrying
// risks duplicate orders/users/etc.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: 3,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 10_000),
    },
    mutations: {
      retry: false,
      onError: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Request failed';
        toast.error(msg);
      },
    },
  },
});

// apply.<apex> serves this same dist but is a PUBLIC, auth-free driver
// application — mount it standalone with no ThemeProvider (so the page
// stays on the light `:root` tokens), no auth, no router, no shell.
// ``?apply`` is honoured too so the form can be previewed in local dev.
const _host = window.location.hostname.toLowerCase();
const _isApply = _host.startsWith('apply.') || new URLSearchParams(window.location.search).has('apply');
const _root = ReactDOM.createRoot(document.getElementById('root')!);

// Lazy so the public form is its OWN chunk — dashboard users never pay
// for it, and apply.* visitors never load the auth/router/shell graph.
// /status[/<ref>] renders the self-service status checker instead;
// /carrier/<token> is the carrier self-fill sheet (Carrier Directory
// invite links — recruiting managers send these to external carriers).
const PublicApply = React.lazy(() => import('./features/applications/public/PublicApply'));
const ApplyStatus = React.lazy(() => import('./features/applications/public/ApplyStatus'));
const PublicCarrierIntake = React.lazy(() => import('./features/carrier-directory/PublicCarrierIntake'));
const _seg0 = window.location.pathname.split('/').filter(Boolean)[0]?.toLowerCase();
const _isStatus = _seg0 === 'status';
const _isCarrierIntake = _seg0 === 'carrier';

if (_isApply) {
  // Public form theme — the SAME rule the recruiter preview reproduces
  // (one source of truth in public/theme.ts), so the two never diverge.
  applyPublicFormTheme();
  document.body.className = 'bg-background text-foreground';
  _root.render(
    <React.StrictMode>
      <React.Suspense fallback={null}>
        {_isCarrierIntake ? <PublicCarrierIntake /> : _isStatus ? <ApplyStatus /> : <PublicApply />}
      </React.Suspense>
      {/* Outside ThemeProvider by design (see above), and the public
          apply pages are always light — so this one is pinned, not read. */}
      <Toaster richColors theme="light" position="top-right" closeButton />
    </React.StrictMode>,
  );
} else {
  _root.render(
    <React.StrictMode>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          {/* Tooltips open after a short hover intent (no bubble spam while
              the mouse is just passing).  timeout={0} disables the
              warm-group so EVERY tooltip waits the same delay — no
              instant pop when moving between neighbouring targets. */}
          <TooltipProvider delay={600} timeout={0}>
            <BrowserRouter basename={import.meta.env.VITE_ROUTER_BASE ?? ''}>
              <AuthProvider>
                {/* Syncs personal preferences to the account once we know
                    WHO is signed in — inside AuthProvider by necessity.
                    Renders nothing. */}
                <PreferencesSync />
                <RoleViewProvider>
                  <App />
                </RoleViewProvider>
              </AuthProvider>
            </BrowserRouter>
            {/* Above the auth decision on purpose: a restart 502s /user/me,
                which lands users on the Login branch — the "updating…"
                overlay must cover that state too, not just signed-in. */}
            <MaintenanceOverlay />
          </TooltipProvider>
          <AppToaster />
          {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
        </QueryClientProvider>
      </ThemeProvider>
    </React.StrictMode>,
  );
}

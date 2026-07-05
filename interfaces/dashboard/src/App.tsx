import { useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import { isSafeReturnTo, APEX_DOMAIN } from './lib/safeReturnTo';
import AppRouter from './router';
import PendingInviteBanner from './components/PendingInviteBanner';
import MaintenanceOverlay from './components/MaintenanceOverlay';
import Login from './pages/Login';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import VerifyEmail from './pages/VerifyEmail';

/**
 * Apex (``4truck.us``) is the single canonical login host — every
 * persona subdomain bounces here when no session, and Telegram's
 * Login Widget is configured for this one domain.  Two host-aware
 * routing rules sit on top of that:
 *
 *   1. Unauthenticated user on a persona subdomain → bounce to
 *      apex login, preserving the original URL via ``?return_to=``
 *      so a deep link survives.
 *
 *   2. Authenticated user on apex → forward to their role's host.
 *      Without this rule the user gets stuck on apex/login (or any
 *      apex path), which has no SPA route for ``/login`` because
 *      apex isn't meant to render the dashboard itself — visiting
 *      a SPA route there falls through to the catch-all 404.
 *
 * Role mapping mirrors AuthContext.ROLE_TO_HOST.  Driver lands on
 * dash. (gets the slimmed DriverOverview component on the shared
 * dashboard); owner/admin also go to dash.; the three operational
 * personas get their own branded entry point.
 */
const ROLE_TO_HOST: Record<string, string> = {
  owner: `dash.${APEX_DOMAIN}`,
  admin: `dash.${APEX_DOMAIN}`,
  fleet: `fleet.${APEX_DOMAIN}`,
  dispatcher: `dispatch.${APEX_DOMAIN}`,
  safety: `safety.${APEX_DOMAIN}`,
  hr: `hr.${APEX_DOMAIN}`,
  accounting: `accounting.${APEX_DOMAIN}`,
  recruiter: `recruiter.${APEX_DOMAIN}`,
  driver: `dash.${APEX_DOMAIN}`,
};

function isOnApex(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.location.hostname.toLowerCase() === APEX_DOMAIN
  );
}

function shouldBounceToApex(): boolean {
  if (typeof window === 'undefined') return false;
  const host = window.location.hostname.toLowerCase();
  if (host === APEX_DOMAIN) return false;
  return host.endsWith(`.${APEX_DOMAIN}`);
}

// ── Loop guard ────────────────────────────────────────────────────
//
// Each bounce (subdomain→apex or apex→subdomain) stamps a counter in
// sessionStorage.  If we see more than LOOP_THRESHOLD bounces inside
// LOOP_WINDOW_MS, something is genuinely desynced — both sides
// disagree about whether the user is logged in — and silently
// retrying forever wedges the browser.  We break out by hard-clearing
// localStorage, calling /auth/logout to invalidate the server cookie,
// and showing the apex login form.
const LOOP_KEY = 'auth_bounce_counter';
const LOOP_TS_KEY = 'auth_bounce_first_ts';
const LOOP_WINDOW_MS = 5_000;
const LOOP_THRESHOLD = 3;

function recordBounceAndCheckLoop(): boolean {
  try {
    const now = Date.now();
    const first = Number(sessionStorage.getItem(LOOP_TS_KEY) || '0');
    const count = Number(sessionStorage.getItem(LOOP_KEY) || '0');
    if (!first || now - first > LOOP_WINDOW_MS) {
      sessionStorage.setItem(LOOP_TS_KEY, String(now));
      sessionStorage.setItem(LOOP_KEY, '1');
      return false;
    }
    const next = count + 1;
    sessionStorage.setItem(LOOP_KEY, String(next));
    return next >= LOOP_THRESHOLD;
  } catch {
    return false;
  }
}

function clearBounceCounter(): void {
  try {
    sessionStorage.removeItem(LOOP_KEY);
    sessionStorage.removeItem(LOOP_TS_KEY);
  } catch { /* ignore */ }
}

async function hardLogoutOnLoop(): Promise<void> {
  // Loop detected — break it.  Drop any localStorage token, force the
  // server to clear the cookie one more time, then land on apex/login
  // WITHOUT a return_to so the apex side can't re-forward us right
  // back to where we came from.
  try {
    localStorage.removeItem('access_token');
    sessionStorage.removeItem('access_token');
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
  } catch { /* best-effort */ }
  clearBounceCounter();
  window.location.replace(`https://${APEX_DOMAIN}/login`);
}

function bounceToApexLogin(): void {
  if (recordBounceAndCheckLoop()) {
    void hardLogoutOnLoop();
    return;
  }
  const returnTo = encodeURIComponent(window.location.href);
  window.location.replace(`https://${APEX_DOMAIN}/login?return_to=${returnTo}`);
}

function forwardAuthenticatedFromApex(role: string | undefined): void {
  if (recordBounceAndCheckLoop()) {
    void hardLogoutOnLoop();
    return;
  }
  // Honor return_to first — set by the unauth bounce, this is where
  // the user actually wanted to go.  ``isSafeReturnTo`` is the shared
  // origin guard in ``lib/safeReturnTo`` so this site stays in lock-
  // step with the AuthContext post-login redirect (an earlier drift
  // between the two checks was the cause of the open-redirect
  // vulnerability flagged by the security review).
  const params = new URLSearchParams(window.location.search);
  const returnTo = params.get('return_to');
  if (isSafeReturnTo(returnTo)) {
    window.location.replace(returnTo!);
    return;
  }
  const target = role ? ROLE_TO_HOST[role] : undefined;
  window.location.replace(`https://${target ?? `dash.${APEX_DOMAIN}`}/`);
}

// Routes reachable WITHOUT an authenticated session.  Rendered before
// the auth guard so a user clicking a "reset your password" link from
// their inbox can land here even though they're not signed in.
const PUBLIC_AUTH_ROUTES: Record<string, React.ComponentType> = {
  '/forgot-password': ForgotPassword,
  '/reset-password': ResetPassword,
  '/verify-email': VerifyEmail,
};

export default function App() {
  const { user, loading } = useAuth();
  const location = useLocation();

  useEffect(() => {
    if (loading) return;
    if (!user && shouldBounceToApex()) {
      bounceToApexLogin();
      return;
    }
    if (user && isOnApex()) {
      forwardAuthenticatedFromApex(user.role);
      return;
    }
    // Clean landing — no bounce required.  Clear the loop counter so
    // subsequent legitimate navigations start fresh.
    clearBounceCounter();
  }, [loading, user]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary" />
      </div>
    );
  }

  // Render a spinner while the useEffect navigates away in two
  // cases: unauth on a persona subdomain (bouncing to apex login) and
  // auth on apex (forwarding to role's host).  Both avoid a flash of
  // the wrong UI during the synchronous redirect.
  if (!user && shouldBounceToApex()) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary" />
      </div>
    );
  }
  if (user && isOnApex()) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary" />
      </div>
    );
  }

  // Public auth routes (reset-password, verify-email, forgot-password)
  // are reachable without a session.  They live OUTSIDE the AppRouter
  // so the shell + permission-guarded routes don't load on what's
  // essentially a public page.  Match the leading path component
  // exactly so ``/reset-password?token=...`` query strings still
  // route correctly.
  const PublicPage = PUBLIC_AUTH_ROUTES[location.pathname];
  if (PublicPage) return <PublicPage />;

  // ``/login`` has no entry in AppRouter — without these two arms a
  // signed-in user hitting /login fell through to the catch-all 404,
  // and a signed-out user hit AppRouter's auth guard and saw nothing.
  // Authenticated → home; unauthenticated → the Login screen (same
  // component the apex bounce uses).
  if (location.pathname === '/login') {
    return user ? <Navigate to="/" replace /> : <Login />;
  }

  // ``/signup/<code>`` is the path-segment URL embedded in invite
  // emails — the segment form keeps the code out of Referer headers
  // and CDN query-string access logs, which a ``?invite=`` query
  // param wouldn't.  Login.tsx itself reads the code from the path
  // in its useEffect (alongside the ?invite= legacy form) and
  // pre-fills the registration form.
  //
  // Authenticated case: a 4truck user with an existing session
  // clicks an invite emailed to them for a DIFFERENT account
  // (legitimate scenario: fleet owner invited to a customer's
  // account; accountant invited across multiple client accounts).
  // We forward to / with the code as ?pending_invite= so the
  // dashboard's PendingInviteBanner can surface "You're signed in
  // to X — sign out to accept this invite for Y" rather than
  // silently dropping the link.  PendingInviteBanner reads the
  // query param, fetches /auth/invite-preview, and renders the
  // affordance.
  if (location.pathname.startsWith('/signup/')) {
    if (user) {
      const m = location.pathname.match(/^\/signup\/([A-Za-z0-9-]+)$/);
      const code = m ? m[1] : null;
      return (
        <Navigate
          to={code ? `/?pending_invite=${encodeURIComponent(code)}` : '/'}
          replace
        />
      );
    }
    return <Login />;
  }

  if (!user) return <Login />;

  return (
    <>
      <PendingInviteBanner />
      <MaintenanceOverlay />
      <AppRouter />
    </>
  );
}

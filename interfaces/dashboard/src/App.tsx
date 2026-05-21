import { useEffect } from 'react';
import { useAuth } from './context/AuthContext';
import { isSafeReturnTo, APEX_DOMAIN } from './lib/safeReturnTo';
import AppRouter from './router';
import Login from './pages/Login';

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

function bounceToApexLogin(): void {
  const returnTo = encodeURIComponent(window.location.href);
  window.location.replace(`https://${APEX_DOMAIN}/login?return_to=${returnTo}`);
}

function forwardAuthenticatedFromApex(role: string | undefined): void {
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

export default function App() {
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    if (!user && shouldBounceToApex()) {
      bounceToApexLogin();
      return;
    }
    if (user && isOnApex()) {
      forwardAuthenticatedFromApex(user.role);
    }
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

  if (!user) return <Login />;

  return <AppRouter />;
}

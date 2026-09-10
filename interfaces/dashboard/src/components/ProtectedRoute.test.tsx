import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// Redundant with the central cleanup in src/test/setup.ts (cleanup is
// idempotent), kept as a local reminder that these tests render.
afterEach(cleanup);

/**
 * The guard has three outcomes, and the bug this pins was collapsing two
 * of them: "this view is not allowed here" and "we don't know yet" both
 * produced a redirect.  Because the redirect uses ``replace``, the
 * original URL was destroyed, so every deep link / bookmark / hard
 * refresh of a gated route on a PREVIEW view (where permissions arrive
 * from a second request) landed on the overview and stayed there.
 *
 * Navigate is stubbed to a marker so no Router is needed — what matters
 * is WHETHER a redirect is rendered, not where it goes.
 */
const state = {
  ready: true,
  perms: ['can_alerts_all'] as string[],
};

vi.mock('react-router-dom', () => ({
  Navigate: ({ to }: { to: string }) => <div data-testid="redirect">{to}</div>,
  Link: ({ to, children }: { to: string; children?: React.ReactNode }) => <a data-testid="upgrade" href={to}>{children}</a>,
}));

const auth = { plan: { excluded_flags: [] as string[], label: 'Starter' }, billing: false };
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { plan: auth.plan, permissions: { can_manage_billing: auth.billing } } }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, string>) => (o ? `${k}:${Object.values(o).join(',')}` : k) }),
}));

vi.mock('../context/RoleViewContext', () => ({
  useRoleView: () => ({
    viewPermsReady: state.ready,
    viewHasAny: (...flags: string[]) => flags.some((f) => state.perms.includes(f)),
  }),
}));

import ProtectedRoute from './ProtectedRoute';

function renderGuard() {
  return render(
    <ProtectedRoute permission={['can_alerts_all', 'can_alerts_vehicle']}>
      <div data-testid="page">the alerts board</div>
    </ProtectedRoute>,
  );
}

describe('ProtectedRoute', () => {
  it('renders the page when the active view holds one of the flags', () => {
    state.ready = true;
    state.perms = ['can_alerts_vehicle'];
    renderGuard();
    expect(screen.getByTestId('page')).toBeTruthy();
    expect(screen.queryByTestId('redirect')).toBeNull();
  });

  it('redirects when the answer is authoritative and negative', () => {
    state.ready = true;
    state.perms = [];
    renderGuard();
    expect(screen.getByTestId('redirect').textContent).toBe('/');
    expect(screen.queryByTestId('page')).toBeNull();
  });

  it('WAITS instead of redirecting while permissions are still loading', () => {
    // The regression: perms are empty here only because the fetch is in
    // flight.  Redirecting now would discard the URL the user asked for.
    state.ready = false;
    state.perms = [];
    renderGuard();
    expect(screen.queryByTestId('redirect')).toBeNull();
    expect(screen.queryByTestId('page')).toBeNull();
  });

  it('does not leak the page early while permissions are loading', () => {
    // The opposite failure: waiting must not mean rendering the gated
    // page before the answer is known.
    state.ready = false;
    state.perms = ['can_alerts_all'];
    renderGuard();
    expect(screen.queryByTestId('page')).toBeNull();
  });
});

describe('ProtectedRoute — a door the PLAN closed', () => {
  it('tells whoever can change the plan, and points at Billing', () => {
    state.ready = true;
    state.perms = [];
    auth.billing = true;
    auth.plan = { excluded_flags: ['can_alerts_all', 'can_alerts_vehicle'], label: 'Starter' };
    renderGuard();
    expect(screen.queryByTestId('page')).toBeNull();
    expect(screen.queryByTestId('redirect')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('plan.route_title');
    expect(screen.getByTestId('upgrade').getAttribute('href')).toContain('/billing');
  });

  it('redirects everyone else, exactly as for a door they do not hold', () => {
    state.ready = true;
    state.perms = [];
    auth.billing = false;
    auth.plan = { excluded_flags: ['can_alerts_all', 'can_alerts_vehicle'], label: 'Starter' };
    renderGuard();
    expect(screen.getByTestId('redirect')).toBeTruthy();
  });

  it('a door the plan did NOT close still redirects a billing holder who lacks it', () => {
    state.ready = true;
    state.perms = [];
    auth.billing = true;
    auth.plan = { excluded_flags: [], label: 'Pro' };
    renderGuard();
    expect(screen.getByTestId('redirect')).toBeTruthy();
  });
});

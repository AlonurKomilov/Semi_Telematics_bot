import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// This project doesn't enable RTL's automatic cleanup, so without this
// each render leaves its nodes in the document and the next test's
// queries find the PREVIOUS test's output.
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

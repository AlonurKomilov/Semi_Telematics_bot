import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';

/**
 * Pins ``viewPermsReady`` itself — the flag route guards use to tell
 * "this view may not be here" apart from "we don't know yet".
 *
 * ProtectedRoute's own test mocks this whole context, so it proves the
 * GUARD reacts correctly but can't catch the derivation being wrong.
 * The bug it guards against is a preview view answering "no" to every
 * permission while /admin/permissions/roles is still in flight, which a
 * redirecting consumer turned into a lost URL.
 */
let resolveRoles: (v: unknown) => void;
let rejectRoles: (e: unknown) => void;
const apiJSON = vi.fn(() => new Promise((res, rej) => {
  resolveRoles = res;
  rejectRoles = rej;
}));

const authUser = { role: 'owner' as string };

vi.mock('../api/client', () => ({
  apiJSON: (...a: unknown[]) => apiJSON(...(a as [])),
  setActiveViewForApi: () => {},
}));
vi.mock('./AuthContext', () => ({
  useAuth: () => ({ user: authUser }),
}));
// The provider reads TWO preferences: previewAsManager (boolean) and
// activeView (the previewed role, '' = none).  The mock must be
// key-aware — returning one value for every key made activeView resolve
// to `true`, which silently fell through to the user's real role.
let mockActiveView = '';
vi.mock('../preferences', () => ({
  usePreference: (key: string) => ({
    value: key === 'roleView.activeView' ? mockActiveView : true,
    setValue: () => {},
  }),
}));

import { RoleViewProvider, useRoleView } from './RoleViewContext';

function Probe() {
  const { viewPermsReady, activeView } = useRoleView();
  return <div data-testid="probe">{activeView}:{viewPermsReady ? 'ready' : 'waiting'}</div>;
}

function mount() {
  return render(<RoleViewProvider><Probe /></RoleViewProvider>);
}

const readout = () => screen.getByTestId('probe').textContent;

beforeEach(() => {
  apiJSON.mockClear();
  authUser.role = 'owner';
  localStorage.clear();
  mockActiveView = '';   // no preview unless a test opts in
});
afterEach(cleanup);

describe('viewPermsReady', () => {
  it('waits while a PREVIEW view’s permission sets are in flight', async () => {
    mockActiveView = 'fleet';   // Owner previewing Fleet
    mount();
    expect(readout()).toBe('fleet:waiting');

    await act(async () => { resolveRoles({ current: { fleet: { can_alerts_all: true } } }); });
    expect(readout()).toBe('fleet:ready');
  });

  it('becomes ready even when the roles fetch FAILS', async () => {
    // A stock admin can't read /admin/permissions/roles (403).  If this
    // stayed false the guard would wait forever and the page would never
    // render — the failure mode opposite to the redirect bug.
    mockActiveView = 'safety';
    mount();
    expect(readout()).toBe('safety:waiting');

    await act(async () => { rejectRoles(new Error('403')); });
    expect(readout()).toBe('safety:ready');
  });

  it('is ready immediately on the SELF view — perms come from /user/me', async () => {
    mount();   // owner, no saved preview choice → activeView === realRole
    expect(readout()).toBe('owner:ready');
  });

  it('is ready immediately for a user who cannot switch views at all', async () => {
    authUser.role = 'driver';
    mount();
    expect(readout()).toBe('driver:ready');
    expect(apiJSON).not.toHaveBeenCalled();   // no roles fetch to wait on
  });
});

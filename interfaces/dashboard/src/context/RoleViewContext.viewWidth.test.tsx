import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';

/**
 * Pins ``viewVehicleScope`` — the ACTIVE VIEW's unit width.
 *
 * Verbs already follow the view (an Owner previewing Fleet sees Fleet's
 * permission set); the width used to stay the Owner's own, so a preview
 * of a role the account narrowed still showed the heatmap, the
 * cross-department nav items and the /vehicles tile links the real
 * member never gets.  Width is Team Management's answer, fetched from
 * its own endpoint — Permissions never answers a width question.
 */
const pending: Record<string, { resolve: (v: unknown) => void; reject: (e: unknown) => void }> = {};
const apiJSON = vi.fn((url: string) => new Promise((resolve, reject) => {
  pending[url] = { resolve, reject };
}));

const authUser: { role: string; vehicle_scope?: 'all' | 'assigned' } = { role: 'owner' };

vi.mock('../api/client', () => ({
  apiJSON: (...a: unknown[]) => apiJSON(...(a as [string])),
  setActiveViewForApi: () => {},
}));
vi.mock('./AuthContext', () => ({
  useAuth: () => ({ user: authUser }),
}));
let mockActiveView = '';
vi.mock('../preferences', () => ({
  usePreference: (key: string) => ({
    value: key === 'roleView.activeView' ? mockActiveView : true,
    setValue: () => {},
  }),
}));

import { RoleViewProvider, useRoleView } from './RoleViewContext';

function Probe() {
  const { activeView, viewVehicleScope } = useRoleView();
  return <div data-testid="probe">{activeView}:{viewVehicleScope ?? 'unknown'}</div>;
}
const mount = () => render(<RoleViewProvider><Probe /></RoleViewProvider>);
const readout = () => screen.getByTestId('probe').textContent;
const WIDTHS = '/admin/roles/vehicle-scope';

beforeEach(() => {
  apiJSON.mockClear();
  for (const k of Object.keys(pending)) delete pending[k];
  authUser.role = 'owner';
  authUser.vehicle_scope = 'all';
  localStorage.clear();
  mockActiveView = '';
});
afterEach(cleanup);

describe('viewVehicleScope', () => {
  it('is the previewed ROLE\'s width from Team Management, not the viewer\'s own', async () => {
    mockActiveView = 'dispatcher';   // Owner (wide) previewing a narrowed role
    mount();
    expect(readout()).toBe('dispatcher:unknown');   // loading = unknown (wide)
    await act(async () => {
      pending[WIDTHS].resolve({ role_vehicle_scopes: { dispatcher: 'assigned', fleet: 'all' } });
    });
    expect(readout()).toBe('dispatcher:assigned');
  });

  it('asks Team Management\'s endpoint, never the permissions one, for width', () => {
    mockActiveView = 'fleet';
    mount();
    expect(apiJSON.mock.calls.map((c) => c[0])).toContain(WIDTHS);
  });

  it('is the member\'s OWN resolved width on the self view', () => {
    authUser.vehicle_scope = 'assigned';
    mount();   // owner on their own view — /me already resolved the width
    expect(readout()).toBe('owner:assigned');
  });

  it('stays unknown (= wide) when the width fetch fails', async () => {
    mockActiveView = 'safety';
    mount();
    await act(async () => { pending[WIDTHS].reject(new Error('403')); });
    expect(readout()).toBe('safety:unknown');
  });

  it('never fetches for a member who cannot preview', () => {
    authUser.role = 'driver';
    authUser.vehicle_scope = 'assigned';
    mount();
    expect(readout()).toBe('driver:assigned');
    expect(apiJSON.mock.calls.map((c) => c[0])).not.toContain(WIDTHS);
  });
});

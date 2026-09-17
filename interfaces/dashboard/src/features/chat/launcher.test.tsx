import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { RoleViewProvider, useRoleView } from '@/context/RoleViewContext';
import { generateNav } from '@/shells/nav/generateNav';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ChatLauncher } from './ChatLauncher';

const state = vi.hoisted(() => ({
  user: { role: 'owner', permissions: { can_view_chat: false } },
  preferences: {} as Record<string, unknown>,
  current: {} as Record<string, { can_view_chat: boolean }>, api: vi.fn(),
}));
vi.mock('@/context/AuthContext', () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock('@/api/client', () => ({ apiJSON: (...args: unknown[]) => state.api(...args), setActiveViewForApi: vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: () => 'Chat' }) }));
vi.mock('@/preferences', async () => {
  const { useState, useCallback } = await import('react');
  return { usePreference: (key: string) => {
    const [value, setValue] = useState(() => state.preferences[key]);
    const update = useCallback((v: unknown) => { state.preferences[key] = v; setValue(v); }, [key]);
    return { value, setValue: update };
  } };
});
function Probe() {
  const view = useRoleView();
  const paths = generateNav(view.activeView, view.viewHasAny, [], 'assigned')
    .flatMap((group) => group.items.map((item) => item.path));
  return <>
    <div data-testid="sidebar">{paths.includes('/chat') ? 'Chat' : ''}</div>
    <div data-testid="view">{view.activeView}:{view.activePermissionKey}</div>
    <button onClick={() => view.switchView('fleet', 'fleet')}>Preview employee</button>
    <button onClick={() => view.switchView('fleet', 'fleet__manager')}>Preview manager</button>
    <button onClick={() => view.refreshPermissions()}>Refresh grants</button>
    <ChatLauncher />
  </>;
}
function mount(path = '/') {
  return render(<MemoryRouter initialEntries={[path]}><TooltipProvider><RoleViewProvider>
    <Probe />
  </RoleViewProvider></TooltipProvider></MemoryRouter>);
}
function expectVisibility(visible: boolean) {
  expect(!!screen.queryByRole('link', { name: 'Chat' })).toBe(visible);
  expect(screen.getByTestId('sidebar').textContent).toBe(visible ? 'Chat' : '');
}
beforeEach(() => {
  state.user = { role: 'owner', permissions: { can_view_chat: false } };
  state.preferences = { 'roleView.activeView': '', 'roleView.previewAsManager': true };
  state.current = { owner: { can_view_chat: false }, fleet: { can_view_chat: true }, fleet__manager: { can_view_chat: false } };
  state.api.mockReset().mockImplementation((url) => Promise.resolve(
    url === '/admin/permissions/roles' ? { current: state.current } : { role_vehicle_scopes: {} }));
  window.history.replaceState({}, '', '/');
});
afterEach(cleanup);
describe('Chat entrypoints share the effective permission resolver', () => {
  it('does not give the Owner a Fleet grant; Employee and Manager use their own stored tiers', async () => {
    mount();
    await act(async () => {});
    expectVisibility(false);
    fireEvent.click(screen.getByRole('button', { name: 'Preview employee' }));
    expectVisibility(true);
    fireEvent.click(screen.getByRole('button', { name: 'Preview manager' }));
    expectVisibility(false);
    expect(state.user).toEqual({ role: 'owner', permissions: { can_view_chat: false } });
  });
  it('refreshes both entrypoints after a saved role grant is revoked', async () => {
    state.preferences['roleView.activeView'] = 'fleet';
    state.preferences['roleView.previewAsManager'] = false;
    mount();
    await waitFor(() => expectVisibility(true));
    state.current = { ...state.current, fleet: { can_view_chat: false } };
    fireEvent.click(screen.getByRole('button', { name: 'Refresh grants' }));
    await waitFor(() => expectVisibility(false));
  });
  it('hides both entrypoints while preview grants load even if the real Owner has access', async () => {
    state.user.permissions.can_view_chat = true;
    state.preferences['roleView.activeView'] = 'fleet';
    state.preferences['roleView.previewAsManager'] = false;
    let resolve!: (value: unknown) => void;
    state.api.mockImplementation((url) => url === '/admin/permissions/roles'
      ? new Promise((res) => { resolve = res; }) : Promise.resolve({ role_vehicle_scopes: {} }));
    mount(); expectVisibility(false);
    await act(async () => { resolve({ current: state.current }); });
    expectVisibility(true);
  });
  it.each(['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'hr', 'accounting', 'recruiter', 'driver'])(
    'uses /user/me for the real %s role regardless of preview tier preference', async (role) => {
      state.user = { role, permissions: { can_view_chat: true } };
      mount('/chat'); await act(async () => {}); expectVisibility(true);
      expect(screen.getByRole('link', { name: 'Chat' }).getAttribute('href')).toBe('/chat');
      expect(screen.getByRole('link', { name: 'Chat' }).getAttribute('aria-current')).toBe('page');
    });
  it('opens the Chat route from the topbar and marks the current page', async () => {
    state.user = { role: 'fleet', permissions: { can_view_chat: true } };
    mount();
    fireEvent.click(screen.getByRole('link', { name: 'Chat' }));
    expect(screen.getByRole('link', { name: 'Chat' }).getAttribute('aria-current')).toBe('page');
  });
  it('consumes incoming role/tier selection over stale origin preferences', async () => {
    state.preferences['roleView.activeView'] = 'safety';
    window.history.replaceState({ key: 'keep' }, '', '/permissions?tab=roles&view_role=fleet&view_tier=employee#services');
    mount(); await waitFor(() => expectVisibility(true));
    expect(screen.getByTestId('view').textContent).toBe('fleet:fleet');
    expect(state.preferences['roleView.activeView']).toBe('fleet');
    expect(state.preferences['roleView.permissionKey']).toBe('fleet');
    expect(window.location.search).toBe('?tab=roles');
    expect(window.location.hash).toBe('#services');
    expect(window.history.state).toEqual({ key: 'keep' });
  });
  it('ignores preview URL hints for a member who cannot switch roles', async () => {
    state.user = { role: 'fleet', permissions: { can_view_chat: false } };
    window.history.replaceState({}, '', '/?view_role=owner&view_tier=manager');
    mount(); await act(async () => {}); expectVisibility(false);
    expect(screen.getByTestId('view').textContent).toBe('fleet:fleet');
    expect(state.preferences['roleView.activeView']).toBe('');
    expect(state.api).not.toHaveBeenCalled();
  });
});

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import type { User } from '@/types';
import type { ChatApi } from './api';
import type { ChatSession } from './session';
import { RoleViewProvider, useRoleView } from '@/context/RoleViewContext';
import ProtectedRoute from '@/components/ProtectedRoute';
import { TooltipProvider } from '@/components/ui/tooltip';
import { PersonaSelector } from '@/components/PersonaSelector';
import { generateNav } from '@/shells/nav/generateNav';
import Chat from './Chat';
import { ChatLauncher } from './ChatLauncher';
import { ApiError } from './api';
import { fakeApi } from './fixtures';
import en from '@/locales/en.json';
const state = vi.hoisted(() => ({
  user: {} as User, preferences: {} as Record<string, unknown>,
  rows: {} as Record<string, { can_view_chat: boolean }>,
  transport: {} as ChatApi, sessions: [] as ChatSession[], api: vi.fn(), refresh: vi.fn(),
}));
vi.mock('@/context/AuthContext', () => ({ useAuth: () => ({ user: state.user, loading: false, refreshUser: state.refresh }) }));
vi.mock('@/api/client', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/client')>(),
  apiJSON: (...args: unknown[]) => state.api(...args), setActiveViewForApi: vi.fn(),
}));
vi.mock('@/preferences', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/preferences')>();
  const { useState, useCallback } = await import('react');
  return { ...actual, usePreference: (key: string) => {
    const [value, setValue] = useState(() => state.preferences[key]);
    const update = useCallback((v: unknown) => { state.preferences[key] = v; setValue(v); }, [key]);
    return { value, setValue: update };
  } };
});
vi.mock('./session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./session')>();
  return { ...actual, ChatSession: class extends actual.ChatSession {
    constructor() { super(state.transport, null); state.sessions.push(this); }
  } };
});
// Capture the identity supplied by the real page after its session loads a room.
vi.mock('./Thread', () => ({ Thread: ({ ownId, ownRole }: { ownId: number; ownRole: string }) =>
  <div data-testid="actor">{ownId}:{ownRole}</div> }));
const i18n = createInstance();
await i18n.init({ lng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
function Harness() {
  const view = useRoleView(); const location = useLocation();
  const nav = generateNav(view.activeView, view.viewHasAny, []);
  return <>
    <PersonaSelector />
    <span data-testid="location">{location.pathname}</span>
    <span data-testid="sidebar">{nav.some(g => g.items.some(i => i.path === '/chat')) ? 'Chat' : ''}</span>
    <span data-testid="preview">{view.isPreviewing ? view.activePermissionKey : 'self'}</span>
    <button onClick={() => view.switchView('owner', 'owner__co')}>Preview co-owner</button>
    <button onClick={() => view.switchView('owner', 'owner')}>Preview primary</button>
    <button onClick={() => view.switchView('admin', 'admin')}>Preview standard admin</button>
    <button onClick={() => view.switchView(state.user.role)}>Return to self</button>
    <ChatLauncher />
    <Routes>
      <Route path="/" element={<p>Overview</p>} />
      <Route path="/chat" element={<ProtectedRoute permission="can_view_chat"><Chat /></ProtectedRoute>} />
    </Routes>
  </>;
}
function mount(path = '/chat') {
  return render(<MemoryRouter initialEntries={[path]}><I18nextProvider i18n={i18n}>
    <TooltipProvider><RoleViewProvider><Harness /></RoleViewProvider></TooltipProvider>
  </I18nextProvider></MemoryRouter>);
}
beforeEach(() => {
  state.user = { id: 12, account_id: 7, role: 'owner', is_primary_owner: true, display_name: 'Allen',
    permissions: { can_view_chat: false } } as User;
  state.preferences = { 'roleView.activeView': 'fleet', 'roleView.permissionKey': 'fleet__manager', 'roleView.previewAsManager': true };
  state.rows = { owner: { can_view_chat: false }, owner__co: { can_view_chat: true },
    fleet: { can_view_chat: false }, fleet__manager: { can_view_chat: true },
    admin: { can_view_chat: false }, admin__manager: { can_view_chat: true } };
  state.transport = fakeApi(); state.refresh.mockReset().mockResolvedValue(undefined);
  state.api.mockReset().mockImplementation((url) => Promise.resolve(url === '/admin/permissions/roles'
    ? { current: state.rows } : { role_vehicle_scopes: {} }));
  window.history.replaceState({}, '', '/');
});
afterEach(() => { cleanup(); state.sessions.splice(0).forEach(s => s.stop()); });
describe('real Chat route, preview and actor permission boundaries', () => {
  it('keeps an allowed Fleet Manager preview on /chat when the real actor is denied', async () => {
    vi.mocked(state.transport.bootstrap).mockRejectedValue(new ApiError(403, 'Chat disabled'));
    mount(); await screen.findByText('Chat access unavailable');
    expect(screen.getByTestId('location').textContent).toBe('/chat');
    expect(screen.getByTestId('sidebar').textContent).toBe('Chat');
    expect(screen.getByRole('link', { name: 'Chat' })).toBeDefined();
    expect(screen.getByText(/Chat uses your signed-in account: Allen · Owner · Primary/)).toBeDefined();
    expect(screen.queryByRole('button', { name: 'New chat' })).toBeNull();
    expect(screen.queryByTestId('actor')).toBeNull();
    expect(state.sessions[0].getSnapshot().summaries).toEqual([]);
    vi.mocked(state.transport.bootstrap).mockResolvedValue({ items: [], next_after: null });
    fireEvent.click(screen.getByRole('button', { name: 'Check access again' }));
    await screen.findByRole('button', { name: 'New chat' });
    expect(state.refresh).toHaveBeenCalledOnce();
  });
  it('opens messages with the signed-in actor even when the preview role differs', async () => {
    state.user.permissions.can_view_chat = true;
    mount('/chat?conversation=room-a');
    await waitFor(() => expect(screen.getByTestId('actor').textContent).toBe('12:owner'));
    expect(state.user.account_id).toBe(7);
    expect(screen.getByTestId('preview').textContent).toBe('fleet__manager');
  });
  it('does not let the actor grant bypass a denied preview', async () => {
    state.user.permissions.can_view_chat = true; state.preferences['roleView.permissionKey'] = 'fleet';
    mount(); await screen.findByText('Overview');
    expect(screen.queryByRole('link', { name: 'Chat' })).toBeNull();
    expect(state.transport.bootstrap).not.toHaveBeenCalled();
  });
  it.each([true, false])('distinguishes owner tiers and return to actual identity (primary=%s)', async primary => {
    state.user.is_primary_owner = primary; state.user.permissions.can_view_chat = !primary;
    state.preferences = { 'roleView.activeView': 'owner', 'roleView.permissionKey': '', 'roleView.previewAsManager': true };
    mount('/');
    expect(screen.getByTestId('sidebar').textContent).toBe(primary ? '' : 'Chat');
    expect(screen.getByText(primary ? 'Owner · Primary' : 'Owner · Co-owner')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: primary ? 'Preview co-owner' : 'Preview primary' }));
    await waitFor(() => expect(screen.getByTestId('sidebar').textContent).toBe(primary ? 'Chat' : ''));
    expect(screen.getByTestId('preview').textContent).toBe(primary ? 'owner__co' : 'owner');
    expect(screen.getByText(primary ? 'Owner · Co-owner' : 'Owner · Primary')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: 'Return to self' }));
    expect(screen.getByTestId('preview').textContent).toBe('self');
    expect(screen.getByTestId('sidebar').textContent).toBe(primary ? '' : 'Chat');
  });
  it('previews a same-role admin tier instead of substituting /me', async () => {
    state.user = { ...state.user, role: 'admin', is_manager: true, permissions: { can_view_chat: true } as User['permissions'] };
    state.preferences['roleView.activeView'] = 'admin'; state.preferences['roleView.permissionKey'] = '';
    mount('/'); expect(screen.getByTestId('sidebar').textContent).toBe('Chat');
    fireEvent.click(screen.getByRole('button', { name: 'Preview standard admin' }));
    await waitFor(() => expect(screen.getByTestId('preview').textContent).toBe('admin'));
    expect(screen.getByTestId('sidebar').textContent).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Return to self' }));
    expect(screen.getByTestId('sidebar').textContent).toBe('Chat');
  });
  it.each(['missing', 'failed'])('never fabricates preview grants when its row is %s', async reason => {
    state.user.permissions.can_view_chat = true;
    if (reason === 'missing') delete state.rows.fleet__manager;
    else state.api.mockRejectedValue(new Error('offline'));
    mount(); await screen.findByText(/Could not load permissions for this preview/);
    expect(screen.getByTestId('location').textContent).toBe('/chat');
    expect(screen.getByTestId('sidebar').textContent).toBe('');
    expect(state.transport.bootstrap).not.toHaveBeenCalled();
  });
  it('refreshes preview grants on focus after another tab edits them', async () => {
    mount('/'); await screen.findByRole('link', { name: 'Chat' });
    state.rows = { ...state.rows, fleet__manager: { can_view_chat: false } };
    fireEvent.focus(window);
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Chat' })).toBeNull());
  });
});

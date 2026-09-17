import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import { RoleLens, type RoleLensApi } from './RoleLens';
const switchView = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ user: null as null | { role: string; is_primary_owner: boolean } }));
vi.mock('@/context/RoleViewContext', () => ({ useRoleView: () => ({ canSwitch: true, switchView }) }));
vi.mock('@/preferences', () => ({ usePreference: () => ({ value: 'fleet', setValue: vi.fn() }) }));
vi.mock('@/context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...await importOriginal<typeof import('react-i18next')>(),
  useTranslation: () => ({ t: (key: string) => key }),
}));
const LABELS: Record<string, string> = { owner: 'Owner', fleet: 'Fleet' };
const TIERS: Record<string, { key: string; label: string }[]> = {
  owner: [{ key: 'owner__co', label: 'Co-owner' }, { key: 'owner', label: 'Primary' }],
  fleet: [{ key: 'fleet', label: 'Employee' }, { key: 'fleet__manager', label: 'Manager' }],
};
function mount(role = 'fleet') {
  const api: RoleLensApi = {
    roles: [role], roleLabel: () => LABELS[role], tierCols: () => TIERS[role],
    granted: (col, row) => 'key' in row && ((row.key === 'can_view_chat' && col === 'fleet')
      || (row.key === 'can_invite' && col === 'fleet__manager')),
    changed: () => false, locked: () => false, onToggle: vi.fn(), ownerPowers: [], heldBy: () => [],
    search: { query: '', setQuery: vi.fn() },
  };
  return render(<MemoryRouter><TooltipProvider><RoleLens api={api} /></TooltipProvider></MemoryRouter>);
}
afterEach(() => { cleanup(); auth.user = null; vi.clearAllMocks(); });
describe('Permissions role/tier preview', () => {
  it('previews the selected Employee or Manager tier explicitly', () => {
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'Preview the dashboard as Fleet — Employee' }));
    expect(switchView).toHaveBeenLastCalledWith('fleet', 'fleet');
    fireEvent.click(screen.getByRole('button', { name: 'Manager' }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview the dashboard as Fleet — Manager' }));
    expect(switchView).toHaveBeenLastCalledWith('fleet', 'fleet__manager');
  });
  it.each([true, false])('opens the actual owner tier and previews the selected exact row (primary=%s)', primary => {
    auth.user = { role: 'owner', is_primary_owner: primary };
    mount('owner');
    const ownLabel = primary ? 'Primary' : 'Co-owner';
    fireEvent.click(screen.getByRole('button', { name: `Preview the dashboard as Owner — ${ownLabel}` }));
    expect(switchView).toHaveBeenLastCalledWith('owner', primary ? 'owner' : 'owner__co');
    expect(screen.getByRole('button', { name: `${ownLabel} · You` })).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: primary ? 'Co-owner' : 'Primary' }));
    fireEvent.click(screen.getByRole('button', { name: `Preview the dashboard as Owner — ${primary ? 'Co-owner' : 'Primary'}` }));
    expect(switchView).toHaveBeenLastCalledWith('owner', primary ? 'owner__co' : 'owner');
  });
  it('does not describe a base-only Chat grant as something the Manager adds', () => {
    mount();
    const added = screen.getByText('Manager adds 1:').parentElement!;
    expect(added.textContent).toContain('Send Invites');
    expect(added.textContent).not.toContain('Chat');
    expect(screen.getByText('Employee only 1:').parentElement!.textContent).toContain('Chat');
    expect(screen.getByRole('button', { name: 'Chat — view: granted' })).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: 'Manager' }));
    expect(screen.getByRole('button', { name: 'Chat — view: no access' })).toBeDefined();
  });
});

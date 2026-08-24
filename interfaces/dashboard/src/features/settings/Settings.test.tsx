/**
 * Regression test for the Settings page render SPLIT.
 *
 * A role MANAGER (can_manage_role_bot, NOT can_manage_account) reaches
 * Settings for the Telegram Bot card ONLY — no /admin/settings fetch, so
 * they must never see the loading skeleton, the account-wide error state,
 * or any owner-only section (Account Timezone, Configuration, Working
 * Hours, Danger Zone).  An OWNER (can_manage_account) gets the full page.
 *
 * This guards the manager-scoping fixes (View-As faithfulness + the
 * dead-end where a manager landed on Settings and hit its 403): the split
 * is driven entirely by useViewPermissions, so we mock that and assert
 * which landmarks render for each persona.  Heavy children and data hooks
 * are stubbed — this test is about the branch, not their internals.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';

// --- the split driver: a mutable permission set the tests swap per case
const mockPerms = new Set<string>();
vi.mock('../../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: (p: string) => mockPerms.has(p),
    hasAny: (...ps: string[]) => ps.some((p) => mockPerms.has(p)),
  }),
}));

// --- i18n: echo the key so assertions match stable strings, not copy
// `initReactI18next` is exported because the page now reaches src/i18n.ts
// through the config gear's import chain, and that module calls
// `.use(initReactI18next)` at load. Without it the whole suite fails to
// collect — an incomplete mock, not a broken page.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

// --- data hooks: no real network / react-query provider needed
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: undefined, isLoading: false, error: undefined }),
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));
// apiJSON is only reached from useEffects here; reject so the optional
// sections (vendor directory / market data / timezone) settle to hidden.
vi.mock('../../api/client', () => ({
  apiJSON: vi.fn(() => Promise.reject(new Error('mock'))),
}));

// --- shell primitives → testid stubs so "no error / no skeleton" is exact
vi.mock('../../components/shell', () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
  // Renders a real heading so the queries below still find sections by
  // role — the point of the primitive is that a section title IS a
  // heading, and a stub that dropped that would hide the regression it
  // exists to catch.
  SectionHeader: ({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) => (
    <div><h2>{children}</h2>{action}</div>
  ),
  ErrorState: ({ message }: { message?: string }) => (
    <div data-testid="error-state">{message}</div>
  ),
  CardSkeleton: () => <div data-testid="card-skeleton" />,
}));

// --- heavy children stubbed to identifiable markers (owner-only ones let
//     us assert a manager never renders them)
vi.mock('./DangerZoneSection', () => ({
  default: () => <div data-testid="danger-zone" />,
}));
vi.mock('./delivery/DeliveryModeSelector', () => ({
  default: () => <div data-testid="delivery-mode" />,
}));
vi.mock('./delivery/SubBotRoster', () => ({
  default: () => <div data-testid="sub-bot-roster" />,
}));
vi.mock('../../components/datagrid', () => ({ default: () => <div data-testid="datagrid" /> }));
vi.mock('../../features/ai/helpers', () => ({ rollupByDisplayLabel: () => [] }));
vi.mock('../../hooks/useNow', () => ({ useNow: () => new Date('2026-07-23T12:00:00Z') }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// Radix Select drags DOM/portal machinery irrelevant to the split.
vi.mock('../../components/ui/select', () => ({
  Select: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectValue: () => null,
}));

import Settings from './Settings';

function renderAs(perms: string[]) {
  mockPerms.clear();
  perms.forEach((p) => mockPerms.add(p));
  return render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  mockPerms.clear();
});

describe('Settings render split — role manager (can_manage_role_bot only)', () => {
  it('renders the Telegram Bot card and its manager subtitle', () => {
    renderAs(['can_manage_role_bot']);
    expect(screen.getByText('bot_card.title')).toBeTruthy();
    expect(screen.getByText('bot_card.manager_subtitle')).toBeTruthy();
  });

  it('never renders the loading skeleton or the account error state', () => {
    renderAs(['can_manage_role_bot']);
    // /admin/settings is disabled for a manager → no skeleton, no error.
    expect(screen.queryByTestId('card-skeleton')).toBeNull();
    expect(screen.queryByTestId('error-state')).toBeNull();
  });

  it('never renders owner-only sections (timezone, working hours, danger zone)', () => {
    renderAs(['can_manage_role_bot']);
    expect(screen.queryByText('Account Timezone')).toBeNull();
    expect(screen.queryByText('Working Hours')).toBeNull();
    expect(screen.queryByTestId('danger-zone')).toBeNull();
  });
});

describe('Settings render split — owner (can_manage_account)', () => {
  it('renders the full page: bot card + owner-only sections', () => {
    renderAs(['can_manage_account']);
    expect(screen.getByText('bot_card.title')).toBeTruthy();
    expect(screen.getByText('Account Timezone')).toBeTruthy();
    expect(screen.getByText('Working Hours')).toBeTruthy();
    expect(screen.getByTestId('danger-zone')).toBeTruthy();
  });

  it('does NOT show the manager subtitle (that copy is manager-only)', () => {
    renderAs(['can_manage_account']);
    expect(screen.queryByText('bot_card.manager_subtitle')).toBeNull();
  });
});

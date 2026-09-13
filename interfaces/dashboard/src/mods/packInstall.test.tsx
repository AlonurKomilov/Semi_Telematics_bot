/**
 * Removing a pack takes its items off every picker.
 *
 * The rule the whole store rests on, and the one nothing else can see:
 * `offer.test.tsx` mocks the door to prove every picker ASKS it, and the
 * store page proves the removal is recorded. Neither renders a picker
 * against a real removal, so both stayed green when the door was made to
 * answer "yes" to everything. This is that test.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

// `vi.mock` is hoisted above the file, so the spy has to be hoisted too
// or the factory closes over a variable that does not exist yet.
const { setTheme, removed } = vi.hoisted(() => ({
  setTheme: vi.fn(),
  /** What this device has taken off — PACK ids. */
  removed: { current: [] as string[] },
}));

// Spread the real modules: the preferences barrel pulls in AuthContext,
// which pulls in src/i18n.ts, which needs `initReactI18next` to exist.
// A bare factory here replaced the whole module and the suite failed to
// collect rather than failing an assertion.
vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));
vi.mock('./context', () => ({
  useMods: () => ({
    theme: {
      mode: 'dark', accent: 'blue', radius: 'md', material: 'solid',
      motion: 'default', icons: 'regular', mod: '',
    },
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: (k: string) => ({
    value: k === 'mods.sound.volume' ? 1
      : k === 'dispatch.soundOn' ? true
        : k === 'mods.packs.removed' ? removed.current
          : 'chime',
    setValue: () => {},
  }),
}));

// The picker's scope row asks who this is: it offers only the places
// this person may open. These tests are not about that gate, so they
// wear an owner's view — every place reachable, permissions settled.
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: () => true, hasAny: () => true, hasWide: () => true,
    vehicleScope: 'all', role: 'owner', ready: true,
  }),
}));



import { ModControls } from './panel/ModControls';

describe('an item whose pack is gone is not offered', () => {
  it('the preset is on the row while its pack is installed', () => {
    removed.current = [];
    render(<ModControls />);
    expect(screen.getByText('Cab')).toBeTruthy();
    expect(screen.getByText('Wall')).toBeTruthy();
  });

  it('and gone from it when the pack is removed', () => {
    removed.current = ['cab'];
    render(<ModControls />);
    expect(screen.queryByText('Cab'), 'a removed pack still offers its item').toBeNull();
    expect(screen.getByText('Wall'), 'removing one pack took another one with it').toBeTruthy();
  });

  it('the row itself goes when nothing is left on it', () => {
    removed.current = ['cab', 'wall'];
    render(<ModControls />);
    expect(screen.queryByText('Presets'), 'an empty heading is left behind').toBeNull();
  });
});

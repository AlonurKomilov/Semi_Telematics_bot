/**
 * The mount point hands the stored pack to the door.
 *
 * `iconLane.test.ts` proves each pack carries every glyph, and
 * `modControls.test.tsx` proves the chip writes the axis. Between them
 * sits the thing neither can see: whether what a person chose ever
 * reaches the provider that installs it. A mount point that always
 * asked for the base pack would leave the chip highlighted, the value
 * stored, and the screen unchanged.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';

const { installed } = vi.hoisted(() => ({ installed: vi.fn() }));
vi.mock('../../lib/icons', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  IconPackProvider: (p: { pack: string; weight: string; children: React.ReactNode }) => {
    installed(p.pack, p.weight);
    return p.children;
  },
}));

let theme: Record<string, unknown> = {};
vi.mock('../context', () => ({ useMods: () => ({ theme }) }));

import { IconWeight } from './IconWeight';

const mount = (over: Record<string, unknown> = {}) => {
  theme = { icons: 'regular', ...over };
  render(<IconWeight><span /></IconWeight>);
};

beforeEach(() => installed.mockClear());

describe('what the mount point installs', () => {
  it('is the pack that is stored', () => {
    mount({ iconPack: 'phosphor' });
    expect(installed).toHaveBeenCalledWith('phosphor', 'regular');
  });

  it('and the weight that is stored', () => {
    mount({ iconPack: 'lucide', icons: 'bold' });
    expect(installed).toHaveBeenCalledWith('lucide', 'bold');
  });

  /** An older stored value, or a pack that was removed. Neither may
   *  leave the app with no glyphs at all — the base pack is what an
   *  unreadable value falls back to. */
  it('falls back to the base pack when nothing is stored', () => {
    mount({});
    expect(installed).toHaveBeenCalledWith('lucide', 'regular');
  });

  it('and to the base weight when the stored one is not one of ours', () => {
    mount({ iconPack: 'lucide', icons: 'ultra-heavy' });
    expect(installed).toHaveBeenCalledWith('lucide', 'regular');
  });
});

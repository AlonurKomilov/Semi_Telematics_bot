/**
 * The corners this app ships — one `.css` file each, listed here.
 *
 * Resource, not engine: `mods/corners.ts` says what a corner item is and
 * knows nothing about which exist. `rounded` has no file on purpose —
 * it is the `:root` ramp itself.
 */
import type { CornerPack } from '../../../corners';

export const CORNERS: readonly CornerPack[] = [
  { id: 'rounded', label: 'Rounded',
    description: 'The corner this app was drawn with — soft, not round' },
  { id: 'sharp', label: 'Sharp',
    description: 'Square edges, nothing softened — drawings and dense tables read flatter' },
  { id: 'soft-panels', label: 'Soft panels',
    description: 'Cards, dialogs and sheets round; buttons and fields exactly as they were' },
  { id: 'pill', label: 'Pill',
    description: 'Fully rounded — controls read as capsules, at arm\'s length' },
];

export const CORNER_IDS = CORNERS.map((c) => c.id);
export const cornersById = (id: string): CornerPack | undefined =>
  CORNERS.find((c) => c.id === id);

/**
 * The pointer sets this app ships — one `.css` file each, listed here.
 *
 * Resource, not engine: `mods/cursor.ts` defines the nine kinds a pack
 * must answer for and the size a browser will draw. `system` has no
 * file on purpose — the operating system's pointer is what no rule
 * means.
 */
import type { CursorPack } from '../../cursor';

export const CURSOR_PACKS: readonly CursorPack[] = [
  { id: 'system', label: 'System', description: 'Your operating system’s own pointer' },
  { id: 'sharp',  label: 'Sharp',  description: 'Squared geometry, drawn to stay visible on any ground' },
];

export const CURSOR_IDS = CURSOR_PACKS.map((c) => c.id);

export const cursorPackById = (id: string): CursorPack | undefined =>
  CURSOR_PACKS.find((c) => c.id === id);

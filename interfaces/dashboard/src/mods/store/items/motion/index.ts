/**
 * The speeds this app ships — one `.css` file each, listed here.
 *
 * `default` has no file: `--motion-scale: 1` is `:root`'s own, and a
 * file for it would restate the engine's default under a stamp nothing
 * needs.
 */
import type { MotionPack } from '../../../motion';

export const MOTION_PACKS: readonly MotionPack[] = [
  { id: 'calm', label: 'Calm', scale: 1.6,
    description: 'Everything takes longer — nothing snaps, nothing demands an answer' },
  // "Normal", not "Default": it is the word the panel has always shown,
  // and a shelf that renamed it would change copy nobody asked to change.
  { id: 'default', label: 'Normal', scale: 1,
    description: 'The speed this app was drawn at' },
  { id: 'night-haul', label: 'Night Haul', scale: 1.8, eases: true,
    description: 'Slower than Calm, and eased so nothing looks in a hurry to be answered' },
  { id: 'snappy', label: 'Snappy', scale: 0.6,
    description: 'Short and immediate — the app keeps up with a fast hand' },
];

export const MOTION_IDS = MOTION_PACKS.map((m) => m.id);
export const motionPackById = (id: string): MotionPack | undefined =>
  MOTION_PACKS.find((m) => m.id === id);

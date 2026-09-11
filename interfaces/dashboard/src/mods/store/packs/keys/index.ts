/**
 * The keyboard cue sets this app ships — one file each, listed here.
 *
 * Resource, not engine: `sound/keys.ts` defines what a key pack must be
 * and how a press becomes a cue, and knows nothing about which packs
 * exist. Two, and deliberately not more, for now — see `packs.test.ts`
 * for the shape a third has to keep.
 */
import type { KeyPack } from '../../../sound/keys';
import { click } from './click';
import { soft } from './soft';

export const KEY_PACKS: readonly KeyPack[] = [click, soft];

export const keyPackById = (id: string): KeyPack | undefined =>
  KEY_PACKS.find((p) => p.id === id);

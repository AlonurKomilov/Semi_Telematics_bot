/**
 * The beds this app ships — one file each, listed here.
 *
 * Resource, not engine: `mods/sound/bed.ts` says what a bed is and knows
 * nothing about which exist. Unlike the CSS axes there is no default
 * file, because there is no default bed — background sound is OFF until
 * somebody turns it on, and OFF is a switch rather than an item called
 * "none".
 */
import type { AmbiencePack } from '../../../sound/bed';
import { road } from './road';
import { rain } from './rain';
import { wind } from './wind';
import { room } from './room';

export const AMBIENCE_PACKS: readonly AmbiencePack[] = [road, rain, wind, room];

export const AMBIENCE_IDS = AMBIENCE_PACKS.map((a) => a.id);
export const ambienceById = (id: string): AmbiencePack | undefined =>
  AMBIENCE_PACKS.find((a) => a.id === id);

/**
 * The act cue sets this app ships — one file each, listed here.
 *
 * RESOURCE, NOT ENGINE, the same split the sound and keyboard folders
 * make: `sound/acts.ts` defines what an act pack must be and knows
 * nothing about which ones exist; this folder is what exists.
 *
 * Every file imports only TYPES. A runtime import here would put a
 * resource on the path from `preferences/registry.ts` back to itself.
 */
import type { ActPack } from '../../../sound/acts';
import { chime } from './chime';
import { blip } from './blip';
import { nightHaul } from './night-haul';

export const ACT_PACKS: readonly ActPack[] = [chime, blip, nightHaul];

export const actPackById = (id: string): ActPack | undefined =>
  ACT_PACKS.find((p) => p.id === id);

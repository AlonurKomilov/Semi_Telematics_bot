/**
 * The entrances this app ships — one file each, listed here.
 *
 * No default file and no "none" item: whether a page moves at all is a
 * SWITCH (`entranceOn`), not an entrance called nothing. `lift` is what
 * the app has always used, so it is the id a stored `true` migrates to.
 */
import type { EntrancePack } from '../../../entrance';
import { lift } from './lift';
import { fade } from './fade';
import { slide } from './slide';

export const ENTRANCE_PACKS: readonly EntrancePack[] = [lift, fade, slide];

export const ENTRANCE_IDS = ENTRANCE_PACKS.map((e) => e.id);
export const entranceById = (id: string): EntrancePack | undefined =>
  ENTRANCE_PACKS.find((e) => e.id === id);

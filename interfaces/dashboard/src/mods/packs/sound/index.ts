/**
 * The cue sets this app ships — one file each, listed here.
 *
 * RESOURCE, NOT ENGINE. `sound/engine.ts` defines what a pack must be
 * (`SoundPack`, the bounds, `playCue`) and knows nothing about which
 * packs exist; this folder is what exists. That is GX's shape — the
 * engine is theirs, a mod is a package the engine consumes by contract
 * — and it is the reason a person adding a third pack never opens the
 * file that unlocks audio.
 *
 * A pack is a FILE. `packs.test.ts` reads this directory and holds the
 * list below equal to it in both directions: a file not listed is a
 * pack nobody can choose, an entry with no file is a name that resolves
 * to nothing. And every pack file imports only TYPES — a runtime import
 * here would put a resource on the path from `preferences/registry.ts`
 * back to itself.
 */
import type { SoundPack } from '../../sound/engine';
import { chime } from './chime';
import { blip } from './blip';

export const SOUND_PACKS: readonly SoundPack[] = [chime, blip];

export const soundPackById = (id: string): SoundPack | undefined =>
  SOUND_PACKS.find((p) => p.id === id);

/**
 * The looks this app ships — one file each, listed here.
 *
 * RESOURCE, NOT ENGINE. `catalogue.ts` defines what a mod IS (`Mod`,
 * where each field lands, how each is installed) and knows nothing about
 * which mods exist; this folder is what exists. GX's Mods are exactly
 * this: a package the engine consumes by contract. A person adding a
 * third look never opens the file that installs one.
 *
 * A pack is a FILE. `packs.test.ts` holds this list equal to the folder
 * in both directions, and every file here imports only TYPES.
 */
/**
 * No look carries `mode`. Dark or light is the one axis that
 * is about the room a person is sitting in — a bright yard office at
 * noon, a cab at 2am — and a look that seizes it makes the screen
 * unreadable for exactly the reason they chose the other one. A mod
 * dresses the app; it does not decide where you are.
 */
import type { Mod } from '../../../catalogue';
import { cab } from './cab';
import { wall } from './wall';

export const MODS: readonly Mod[] = [cab, wall];

export const modById = (id: string): Mod | undefined =>
  MODS.find((m) => m.id === id);

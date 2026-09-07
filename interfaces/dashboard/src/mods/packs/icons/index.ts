/**
 * The icon packs this app ships — three files each (`<id>.tsx` the
 * provider, `<id>.icons.ts` the glyphs under our names, `<id>.weights.ts`
 * how it takes a weight), listed here.
 *
 * RESOURCE, NOT ENGINE. `lib/icons/index.tsx` is the door: it resolves a
 * name to a glyph at render through whichever pack is installed, and
 * knows nothing about which packs exist — it asks this file for the
 * base and for a loader. `lib/icons/pack.ts` is the contract.
 *
 * Lucide is the BASE and is imported statically: it is what an
 * unconfigured screen paints. Phosphor is reached only by `import()`
 * — every glyph ships with all six weights inlined, +93 kB gzip, and in
 * its own chunk that is paid by the people who asked for it. Flat files
 * rather than a folder per pack so the chunk keeps the pack's name.
 */
import type { IconPackDef, IconPackModule } from '../../../lib/icons/pack';
import * as lucide from './lucide';

export const BASE_PACK = { id: 'lucide', module: lucide as unknown as IconPackModule } as const;

export const ICON_PACKS: readonly IconPackDef[] = [
  { id: 'lucide',   label: 'Lucide',   load: () => Promise.resolve(BASE_PACK.module) },
  { id: 'phosphor', label: 'Phosphor', load: () => import('./phosphor').then((m) => m as unknown as IconPackModule) },
];
export const ICON_PACK_IDS = ICON_PACKS.map((p) => p.id);

export const iconPackById = (id: string): IconPackDef | undefined =>
  ICON_PACKS.find((p) => p.id === id);

/** The module for a pack id, or `undefined` for a name that is not a
 *  pack — the door keeps painting what it has rather than blanking. */
export const loadIconPack = (id: string): Promise<IconPackModule | undefined> => {
  const p = iconPackById(id);
  return p ? p.load() : Promise.resolve(undefined);
};

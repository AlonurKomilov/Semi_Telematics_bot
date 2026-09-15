/**
 * The materials this app ships — one `.css` file each, listed here.
 * Solid has no file: it IS the base `.surface`, and it must cost
 * exactly nothing — `material.test.ts` holds the base class to no
 * backdrop filter.
 */
import type { MaterialPack } from '../../../material';

export const MATERIAL_PACKS: readonly MaterialPack[] = [
  { id: 'solid', label: 'Solid', description: 'Opaque surfaces — the way it has always been' },
  {
    id: 'glass', label: 'Glass',
    description: 'Surfaces you can see the ground through',
    /**
     * 30px of bevel, falling away as the square-and-a-bit.
     *
     * The band is in SURFACE pixels, so the same declaration gives a
     * chip and a dialog the same thickness of glass rather than a
     * thickness proportional to their size — which is what a real pane
     * does and what the first attempt at this got wrong.
     * 2.2 rather than a straight ramp: a rounded edge bends hardest in
     * the last millimetre and barely at all a third of the way in, so
     * a linear falloff reads as a wide soft smear instead of an edge.
     */
    bevel: { band: 30, falloff: 2.2 },
  },
];
export const MATERIAL_IDS = MATERIAL_PACKS.map((p) => p.id);
export const materialPackById = (id: string): MaterialPack | undefined =>
  MATERIAL_PACKS.find((p) => p.id === id);

/**
 * The materials this app ships — one `.css` file each, listed here.
 * Solid has no file: it IS the base `.surface`, and it must cost
 * exactly nothing — `material.test.ts` holds the base class to no
 * backdrop filter.
 */
import type { MaterialPack } from '../../material';

export const MATERIAL_PACKS: readonly MaterialPack[] = [
  { id: 'solid', label: 'Solid', description: 'Opaque surfaces — the way it has always been, at no cost' },
  { id: 'glass', label: 'Glass', description: 'Surfaces you can see the ground through' },
];
export const MATERIAL_IDS = MATERIAL_PACKS.map((p) => p.id);
export const materialPackById = (id: string): MaterialPack | undefined =>
  MATERIAL_PACKS.find((p) => p.id === id);

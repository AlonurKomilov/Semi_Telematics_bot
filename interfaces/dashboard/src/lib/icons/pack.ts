/**
 * What an icon pack MODULE is — the shape the door consumes. The pack's
 * meta and loader (`IconPackDef`) sit beside the packs in
 * `mods/store/packs/icons/index.ts`, on top of `packs/meta.ts` like every axis. A type-only leaf: the packs are
 * fetched on demand and must not drag the door into their chunk to
 * learn what shape to be.
 */
import type { ReactNode, JSX } from 'react';
import type { IconWeightName } from './weight';

/** A pack module: every name in `names.ts` as a component, and a
 *  `Provider` that installs a weight its own way — lucide strokes,
 *  Phosphor names its six. */
export type IconPackModule = Record<string, unknown> & {
  Provider: (p: { weight: IconWeightName; children: ReactNode }) => JSX.Element;
};

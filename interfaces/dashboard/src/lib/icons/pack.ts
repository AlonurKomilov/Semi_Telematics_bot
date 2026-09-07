/**
 * What an icon pack IS — the contract the door consumes and every pack
 * in `mods/packs/icons/` answers to. A type-only leaf: the packs are
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

export interface IconPackDef {
  readonly id: string;
  readonly label: string;
  /** Fetches the module. The base pack resolves at once; the rest are
   *  their own chunk, paid for by the people who asked for them. */
  readonly load: () => Promise<IconPackModule>;
}

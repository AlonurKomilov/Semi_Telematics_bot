/**
 * The lucide pack: its glyphs and the way it takes a weight.
 *
 * Weight is not one mechanism across packs. Lucide draws with a stroke
 * and takes `strokeWidth`; Phosphor ships six named weights and takes
 * `weight`. So each pack owns its provider, which is also what keeps
 * the other pack's context out of the bundle a person is not wearing.
 */
import type { ReactNode } from 'react';
import { LucideProvider } from 'lucide-react';
import type { IconWeightName } from './weight';

export * from './lucide.icons';

/** Lucide's own 2 is `regular`; the other two bracket it.
 *  Exported so `iconLane.test.ts` can hold it TOTAL over the axis: a
 *  missing entry resolves to `undefined`, lucide falls back to its
 *  default, and the weight silently stops working — which reads as the
 *  feature being broken rather than as a typo. */
export const WEIGHT_MAP: Record<IconWeightName, number> = {
  hairline: 1.25, regular: 2, bold: 2.5,
};

export function Provider({ weight, children }: { weight: IconWeightName; children: ReactNode }) {
  return <LucideProvider strokeWidth={WEIGHT_MAP[weight]}>{children}</LucideProvider>;
}

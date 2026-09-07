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
import { WEIGHT_MAP } from './lucide.weights';
import type { IconWeightName } from './weight';

export * from './lucide.icons';
export { WEIGHT_MAP } from './lucide.weights';


export function Provider({ weight, children }: { weight: IconWeightName; children: ReactNode }) {
  return <LucideProvider strokeWidth={WEIGHT_MAP[weight]}>{children}</LucideProvider>;
}

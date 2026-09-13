/**
 * The Phosphor pack: its glyphs, under our names, and its own weight.
 *
 * REACHED ONLY BY `import()` — from `index.ts` beside this file. Phosphor ships every icon with all six
 * weights inlined and offers no per-weight entry point, so these 157
 * glyphs measure +417 kB raw / +93 kB gzip — a 38% tax on the main
 * bundle, paid on first paint by everyone including the people who
 * never change the pack. In its own chunk it is paid by the people who
 * asked for it.
 */
import type { ReactNode } from 'react';
import { IconContext } from '@phosphor-icons/react';
import { WEIGHT_MAP } from './phosphor.weights';
import type { IconWeightName } from '../../../../lib/icons/weight';

export * from './phosphor.icons';
export { WEIGHT_MAP } from './phosphor.weights';


export function Provider({ weight, children }: { weight: IconWeightName; children: ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: WEIGHT_MAP[weight] }}>
      {children}
    </IconContext.Provider>
  );
}

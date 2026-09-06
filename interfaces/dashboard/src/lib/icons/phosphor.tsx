/**
 * The Phosphor pack: its glyphs, under our names, and its own weight.
 *
 * REACHED ONLY BY `import()`. Phosphor ships every icon with all six
 * weights inlined and offers no per-weight entry point, so these 157
 * glyphs measure +417 kB raw / +93 kB gzip — a 38% tax on the main
 * bundle, paid on first paint by everyone including the people who
 * never change the pack. In its own chunk it is paid by the people who
 * asked for it.
 */
import type { ReactNode } from 'react';
import { IconContext } from '@phosphor-icons/react';
import type { IconWeightName } from './weight';

export * from './phosphor.icons';

/** Ours are three; Phosphor's are six. `thin` is a hair too fine at
 *  small sizes, so `hairline` takes `light`. */
const WEIGHT = { hairline: 'light', regular: 'regular', bold: 'bold' } as const;

export function Provider({ weight, children }: { weight: IconWeightName; children: ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: WEIGHT[weight] }}>
      {children}
    </IconContext.Provider>
  );
}

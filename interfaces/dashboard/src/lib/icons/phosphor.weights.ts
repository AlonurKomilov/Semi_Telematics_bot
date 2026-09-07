import type { IconWeightName } from './weight';

/**
 * How this pack takes a weight.
 *
 * Its OWN module, and the reason is a suite that went flaky: the
 * completeness guards used to import the pack itself to read this, and
 * importing Phosphor pulls 3045 exports through the transform — 15 to
 * 23 seconds, over the 20s timeout under load. The map is plain data
 * and needs none of that, so it lives where it can be read on its own.
 */
/** Ours are three; Phosphor's are six. `thin` is a hair too fine at
 *  small sizes, so `hairline` takes `light`. Exported, and held TOTAL
 *  over the axis by `iconLane.test.ts` for the same reason lucide's is:
 *  a missing entry leaves the weight silently unchanged. */
export const WEIGHT_MAP: Record<IconWeightName, 'light' | 'regular' | 'bold'> = {
  hairline: 'light', regular: 'regular', bold: 'bold',
};

import type { IconWeightName } from '../../../lib/icons/weight';

/**
 * How this pack takes a weight.
 *
 * Its OWN module, and the reason is a suite that went flaky: the
 * completeness guards used to import the pack itself to read this, and
 * importing Phosphor pulls 3045 exports through the transform — 15 to
 * 23 seconds, over the 20s timeout under load. The map is plain data
 * and needs none of that, so it lives where it can be read on its own.
 */
/** Lucide's own 2 is `regular`; the other two bracket it.
 *  Exported so `iconLane.test.ts` can hold it TOTAL over the axis: a
 *  missing entry resolves to `undefined`, lucide falls back to its
 *  default, and the weight silently stops working — which reads as the
 *  feature being broken rather than as a typo. */
export const WEIGHT_MAP: Record<IconWeightName, number> = {
  hairline: 1.25, regular: 2, bold: 2.5,
};

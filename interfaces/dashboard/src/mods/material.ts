import type { ItemMeta } from './store/items/meta';
import type { Bevel } from './lens';
/**
 * What a surface is MADE OF, as opposed to what colour it is.
 *
 * An axis, not a pack field — it belongs beside corners in the panel,
 * because it is a property of the whole app rather than of one look. A
 * mod may set it, the same way a mod sets corners.
 *
 * The materials themselves — and the CSS that makes one — live in
 * `mods/store/items/material/`. This file is the contract.
 */
export interface MaterialPack extends ItemMeta {
  /**
   * The edge this material bends light at, or nothing if it bends none.
   *
   * OPTIONAL BECAUSE IT IS A PROPERTY OF ONE MATERIAL, not of the axis:
   * `solid` has no edge to speak of and must cost exactly nothing, so
   * the absence here is the answer rather than a zero somewhere that
   * still has to be read.
   *
   * The numbers are the PACK's, the same way a depth pack owns its
   * ladder: how thick a pane reads and how sharply its bevel falls away
   * are what a material IS. `mods/lens.ts` knows how to draw a bevel
   * and at what resolution — a contract and a measured performance
   * decision — and knows nothing about which materials exist or what
   * they ask for. Keeping that line is why this field is here and not
   * there.
   */
  readonly bevel?: Bevel;
}

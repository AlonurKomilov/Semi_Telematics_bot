import type { PackMeta } from './store/packs/meta';
/**
 * What a surface is MADE OF, as opposed to what colour it is.
 *
 * An axis, not a pack field — it belongs beside corners in the panel,
 * because it is a property of the whole app rather than of one look. A
 * mod may set it, the same way a mod sets corners.
 *
 * The materials themselves — and the CSS that makes one — live in
 * `mods/store/packs/material/`. This file is the contract.
 */
export interface MaterialPack extends PackMeta {
}

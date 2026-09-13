import type { ItemMeta } from './store/items/meta';

/**
 * The corner — how hard the edge of everything is.
 *
 * ONE CHOICE, EVERY SURFACE. A card, a button, a chip and a dialog all
 * take their corner from the same ramp, which is why this was a value
 * with three settings for so long: moving `--radius` moved all of them
 * together, and there was nothing else to say.
 *
 * What makes it an item now is that the ramp came apart. `--radius-sm`
 * through `--radius-3xl` are each their own token, so a pack can reach
 * ONE step — round cards over square controls, a capsule button on a
 * plain panel — instead of sliding the whole scale. That is a thing to
 * design, and a thing to design is a thing to ship.
 *
 * It PRINTS, unlike a wallpaper or a pointer: paper keeps the shape of
 * a card, so these blocks are not wrapped in `@media screen`.
 *
 * `rounded` has no file. It IS `:root` — the ramp the app was drawn
 * with — and a file for it would restate the engine's own defaults
 * under a stamp nothing needs.
 */
export interface CornerPack extends ItemMeta {
}

// The items — the list and the CSS — live in `mods/store/items/corners/`.
// This file is the contract one has to keep.

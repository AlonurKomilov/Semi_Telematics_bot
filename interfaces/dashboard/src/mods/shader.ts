import type { PackMeta } from './store/packs/meta';
/**
 * The light the interface sits in.
 *
 * CALLED "SHADERS" ON PURPOSE, and it is not a borrowed word. A
 * Minecraft shader pack replaces the game's LIGHTING — shadows, sun
 * angle, bloom, atmosphere — and deliberately does not touch what
 * anything IS: a diamond block keeps its texture, a redstone torch
 * keeps its red. Changing the objects themselves is what a texture pack
 * does, and it is a different thing.
 *
 * That distinction is exactly why this axis is safe and why GX's was
 * not. GX's shaders are `hue-rotate` over page content, which turns a
 * danger red into something that reads as success — it changes what
 * things ARE. In Minecraft's own terms GX ships a texture pack and
 * calls it a shader.
 *
 * So this changes light and nothing else. No token that carries meaning
 * is touched; `--danger` is `--danger` under every preset. There is no
 * readability gate here because there is nothing to break.
 *
 * WHERE IT LANDS: the shadow scale. `shadow-sm` through `shadow-2xl`
 * were Tailwind's hardcoded values, so 79 call sites drew a light
 * nobody could move. They compose from these three multipliers now —
 * the same thing `--radius` did for `rounded-*` and `--size-*` did for
 * every length — and not one of those call sites changed.
 */
export interface ShaderPack extends PackMeta {
  /** How far a surface lifts off the ground — the shadow's offset. */
  readonly lift: number;
  /** How far the light spreads before it stops — the blur. */
  readonly spread: number;
  /** How much of the light the surface blocks — the shadow's alpha. */
  readonly strength: number;
  /**
   * Whether a CARD leaves the ground.
   *
   * The design system's elevation ladder — background, sidebar, card,
   * popover — is real, and it is said entirely in COLOUR: a card is a
   * lighter plane, not a raised one. So the light had nothing to act on
   * where a person actually looks. It reached popovers and menus, which
   * is 54 of the 78 shadows in the app and none of the surface every
   * page is made of.
   *
   * 0 at Flat, which is what "flat" means and what keeps today
   * pixel-identical; 1 where a preset is meant to lift things. It
   * multiplies the other three rather than replacing them, so a raised
   * card is lit by the same sun as everything else.
   */
  readonly elevate: 0 | 1;
}

/**
 * `flat` is not "no shadows". It is TODAY, exactly — every multiplier 1,
 * so an unstamped document and a `flat` one are the same document.
 *
 * That matters more here than it did for the wallpaper. `shadow-lg` is
 * on 37 popovers and menus, where the shadow is what separates the
 * overlay from the content under it; a preset that removed it would not
 * be a flatter look, it would be a menu you cannot find the edge of.
 * The two departures move the light, they do not put it out.
 */
// The presets — the numbers and the CSS — live in `mods/store/packs/shader/`.
// This file is the contract: what a light is, and the band it may move in.

/**
 * How far a multiplier may go.
 *
 * A shadow is a depth cue, and past a point it stops being one: at high
 * strength a card reads as a hole rather than a raised surface, and at
 * high lift the shadow detaches from the thing casting it. The band is
 * a design limit rather than a safety one — nothing breaks outside it,
 * it just stops looking like light.
 */
export const SHADER_BAND = { min: 0.3, max: 2.5 };

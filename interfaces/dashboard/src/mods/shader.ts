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
export interface ShaderPack {
  readonly id: string;
  readonly label: string;
  readonly why: string;
  /** How far a surface lifts off the ground — the shadow's offset. */
  readonly lift: number;
  /** How far the light spreads before it stops — the blur. */
  readonly spread: number;
  /** How much of the light the surface blocks — the shadow's alpha. */
  readonly strength: number;
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
export const SHADER_PACKS: readonly ShaderPack[] = [
  { id: 'flat', label: 'Flat', why: 'The light this app was drawn in',
    lift: 1, spread: 1, strength: 1 },
  { id: 'soft', label: 'Soft', why: 'A lower sun — longer shadows, softer edges',
    lift: 1.7, spread: 1.9, strength: 0.75 },
  { id: 'studio', label: 'Studio', why: 'Overhead and close — short shadows, crisp edges',
    lift: 0.6, spread: 0.5, strength: 1.7 },
];

export const SHADER_IDS = SHADER_PACKS.map((s) => s.id);

export const shaderPackById = (id: string): ShaderPack | undefined =>
  SHADER_PACKS.find((s) => s.id === id);

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

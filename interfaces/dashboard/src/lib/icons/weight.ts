/**
 * The weight axis, named where both packs can see it.
 *
 * It lives in its own leaf rather than in the catalogue because
 * `phosphor.tsx` is dynamically imported and must not drag the mods
 * engine into its chunk to learn three words.
 */
export const ICON_WEIGHTS = ['hairline', 'regular', 'bold'] as const;
export type IconWeightName = (typeof ICON_WEIGHTS)[number];

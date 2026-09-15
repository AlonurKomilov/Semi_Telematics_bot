import type { DepthPack } from './index';

/**
 * `flat` is not a new look. It is the ladder this app has always used,
 * moved out of the engine unchanged — every number below is the one
 * `theme/palette.ts` held, so installing it changes nothing anybody
 * would notice. That is the point: a move and a change in one commit is
 * a move nobody can check.
 *
 * Its name is a description rather than a compliment. In DARK the
 * planes climb — a card is 0.175 above the page, a popover 0.220, so
 * the three read as three. In LIGHT card and popover are both ZERO:
 * they sit exactly on the page, and every surface in the product is the
 * same white. That is why a card there is visible only by its border,
 * and why glass has nothing behind it to show.
 *
 * It is flat for a reason that is not an oversight. The page IS the
 * seed, and the light seed is `oklch(1 0 0)` — a card cannot climb
 * above white. A ladder that lifts in light needs a page that is not at
 * the ceiling, which is a decision about the SEED and belongs with
 * whoever picks one.
 */
export const flat: DepthPack = {
  id: 'flat',
  label: 'Flat',
  description: 'The steps this app was drawn with — planes close together',
  ladder: {
    light: {
      card:          { dL:  0.000, C: 0.000 },
      popover:       { dL:  0.000, C: 0.000 },
      secondary:     { dL: -0.030, C: 0.000 },
      muted:         { dL: -0.030, C: 0.000 },
      accent:        { dL: -0.030, C: 0.000 },
      sidebar:       { dL: -0.035, C: 0.004 },
      sidebarAccent: { dL: -0.065, C: 0.006 },
    },
    dark: {
      card:          { dL:  0.175, C: 0.000 },
      popover:       { dL:  0.220, C: 0.000 },
      secondary:     { dL:  0.200, C: 0.000 },
      muted:         { dL:  0.140, C: 0.000 },
      accent:        { dL:  0.240, C: 0.015 },
      sidebar:       { dL:  0.115, C: 0.022 },
      sidebarAccent: { dL:  0.200, C: 0.025 },
    },
  },
};

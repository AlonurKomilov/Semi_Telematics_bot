import type { DepthPack } from './index';

/**
 * `flat` is not a new look. It is the ladder this app has always used,
 * moved out of the engine unchanged — every number below is the one
 * `theme/palette.ts` held, so installing it changes nothing anybody
 * would notice. That is the point: a move and a change in one commit is
 * a move nobody can check.
 *
 * Its name is a description rather than a compliment: the steps are
 * close together. In DARK the planes climb — a card is 0.175 above the
 * page, a popover 0.220, so the three read as three. In LIGHT the
 * climb is a tenth of that — a card is 0.051 above the page — which is
 * all a near-white palette has room for, but it is no longer nothing.
 *
 * Its LIGHT half used to be flat for a reason that was not an
 * oversight and is no longer true. The page IS the seed, and the light
 * seed was `oklch(1 0 0)` — a card cannot climb above white, so card
 * and popover both read 0.000 and every light surface in the product
 * was the same colour. A ladder that lifts in light needs a page that
 * is not at the ceiling, and the page came down: the light canvas is
 * 0.95 now, so the steps below are the same destinations measured from
 * a seed that leaves room above it. Nothing a reader sees moved except
 * the page itself.
 */
export const flat: DepthPack = {
  id: 'flat',
  label: 'Flat',
  description: 'The steps this app was drawn with — planes close together',
  ladder: {
    light: {
      card:          { dL:  0.051, C: 0.000 },
      popover:       { dL:  0.051, C: 0.000 },
      secondary:     { dL:  0.021, C: 0.000 },
      muted:         { dL:  0.021, C: 0.000 },
      accent:        { dL:  0.021, C: 0.000 },
      sidebar:       { dL:  0.016, C: 0.004 },
      sidebarAccent: { dL: -0.014, C: 0.006 },
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

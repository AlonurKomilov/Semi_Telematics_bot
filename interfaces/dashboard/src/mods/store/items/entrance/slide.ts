import type { EntrancePack } from '../../../entrance';

/** Sideways, the direction a page is usually navigated from. Longer
 *  than Lift because a horizontal distance reads as further. */
export const slide: EntrancePack = {
  id: 'slide',
  label: 'Slide',
  description: 'The page comes in from the right, the way a next page arrives',
  classes: 'animate-in fade-in-0 slide-in-from-right-4',
};

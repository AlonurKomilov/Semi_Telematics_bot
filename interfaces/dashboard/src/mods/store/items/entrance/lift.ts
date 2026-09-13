import type { EntrancePack } from '../../../entrance';

/** The one this app has always used when a look asked for an entrance:
 *  fade, and a short rise from below. */
export const lift: EntrancePack = {
  id: 'lift',
  label: 'Lift',
  description: 'The page fades in and rises a little — the movement this app has always used',
  classes: 'animate-in fade-in-0 slide-in-from-bottom-2',
};

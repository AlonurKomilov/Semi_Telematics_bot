import type { Mod } from '../../../catalogue';

/**
 * Desk — the dispatcher's, not the driver's.
 *
 * Everything the cab looks need, this one does not: no pattern to read
 * through, no movement to notice from across a room, and the quietest
 * keyboard we ship, because the person at this desk is on the phone and
 * somebody else is sitting two metres away.
 *
 * The entrance is Fade — opacity only. A desk navigates more than a cab
 * does, and sideways movement thirty times an hour is the tax the switch
 * exists to refuse.
 */
export const desk: Mod = {
  id: 'desk', label: 'Desk', accent: 'blue', motion: 'default',
  wallpaper: 'none', wallpaperPage: 'none',
  radius: 'soft-panels', shader: 'soft',
  keys: 'night-haul', ambience: 'room',
  entrance: 'fade', entranceOn: true,
  description: 'For a desk with other people at it — nothing patterned, nothing loud, nothing moving sideways',
};

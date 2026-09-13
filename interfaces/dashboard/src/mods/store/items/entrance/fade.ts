import type { EntrancePack } from '../../../entrance';

/** No movement at all — only the opacity. For somebody who wants the
 *  page to announce itself without anything sliding. */
export const fade: EntrancePack = {
  id: 'fade',
  label: 'Fade',
  description: 'Opacity only, nothing moves — the quietest arrival there is',
  classes: 'animate-in fade-in-0',
};

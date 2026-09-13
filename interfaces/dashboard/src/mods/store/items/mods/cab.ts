import type { Mod } from '../../../catalogue';

/**
 * Cab — a tablet in a moving truck. Bigger targets for gloved hands,
 * pill corners that read at arm's length, bold glyphs, and Blip for a
 * cue that cuts through road noise without being louder.
 */
export const cab: Mod = {
  id: 'cab',  label: 'Cab',  accent: 'azure', radius: 'pill',    size: 1.25,
  icons: 'bold', sound: 'blip',
  description: 'Tablet in a moving truck — bigger targets, gloved hands'
};

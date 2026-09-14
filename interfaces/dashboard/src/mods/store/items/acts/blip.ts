import type { ActPack } from '../../../sound/acts';

/**
 * The sharp voice. Square waves, short, closer to a machine reporting
 * than to a bell — the act half of `sound/blip`.
 *
 * `page_open` and `page_close` are triangles rather than squares on
 * purpose: they are the two longest sounds in the pack, and a square
 * held for 50ms is a buzz rather than a note.
 */
export const blip: ActPack = {
  id: 'blip',
  label: 'Blip',
  description: 'Clipped and digital, like the rest of Blip',
  cues: {
    press:         { wave: 'square',   from: 2000, to: 2000, dur: 0.010, gain: 0.030 },
    chip:          { wave: 'square',   from: 2500, to: 2500, dur: 0.010, gain: 0.028 },
    toggle_on:     { wave: 'square',   from: 900,  to: 1200, dur: 0.035, gain: 0.036 },
    toggle_off:    { wave: 'square',   from: 1200, to: 900,  dur: 0.035, gain: 0.036 },
    menu_pick:     { wave: 'square',   from: 1600, to: 1900, dur: 0.016, gain: 0.030 },
    page_open:     { wave: 'triangle', from: 700,  to: 1000, dur: 0.050, gain: 0.048 },
    page_close:    { wave: 'triangle', from: 1000, to: 700,  dur: 0.050, gain: 0.044 },
    surface_open:  { wave: 'square',   from: 1100, to: 1400, dur: 0.040, gain: 0.044 },
    surface_close: { wave: 'square',   from: 1400, to: 1100, dur: 0.040, gain: 0.040 },
    select_add:    { wave: 'square',   from: 1300, to: 1550, dur: 0.024, gain: 0.034 },
    select_clear:  { wave: 'square',   from: 1550, to: 1200, dur: 0.028, gain: 0.032 },
  },
};

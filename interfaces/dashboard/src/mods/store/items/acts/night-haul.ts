import type { ActPack } from '../../../sound/acts';

/**
 * The low voice, for a cab at night and for a room with other people in
 * it.
 *
 * Every cue sits an octave or more below the other two packs and lands
 * quieter — 0.040 at its loudest against Blip's 0.048 — because low and
 * soft is what carries least across a desk. It is the pack to reach for
 * when the honest answer to "can I hear this all day" is almost no.
 */
export const nightHaul: ActPack = {
  id: 'night-haul',
  label: 'Night Haul',
  description: 'Low and unhurried, like the rest of Night Haul',
  cues: {
    press:         { wave: 'sine', from: 520, to: 520, dur: 0.016, gain: 0.022 },
    chip:          { wave: 'sine', from: 680, to: 680, dur: 0.014, gain: 0.020 },
    toggle_on:     { wave: 'sine', from: 310, to: 390, dur: 0.050, gain: 0.028 },
    toggle_off:    { wave: 'sine', from: 390, to: 310, dur: 0.050, gain: 0.028 },
    menu_pick:     { wave: 'sine', from: 440, to: 520, dur: 0.022, gain: 0.024 },
    page_open:     { wave: 'sine', from: 220, to: 300, dur: 0.065, gain: 0.040 },
    page_close:    { wave: 'sine', from: 300, to: 220, dur: 0.065, gain: 0.036 },
    surface_open:  { wave: 'sine', from: 260, to: 340, dur: 0.055, gain: 0.038 },
    surface_close: { wave: 'sine', from: 340, to: 260, dur: 0.055, gain: 0.034 },
    select_add:    { wave: 'sine', from: 380, to: 460, dur: 0.030, gain: 0.026 },
    select_clear:  { wave: 'sine', from: 460, to: 360, dur: 0.034, gain: 0.024 },
  },
};

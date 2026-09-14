import type { ActPack } from '../../../sound/acts';

/**
 * The warm voice, answering your hand instead of the server.
 *
 * Its cues are the same family as `sound/chime` — sine, no edges — but
 * an octave of the way down in weight: the loudest thing here is 0.046,
 * against 0.20 for the alert it must never compete with.
 *
 * The two directions are exact mirrors. `toggle_off` is `toggle_on`
 * with `from` and `to` swapped, and the same everywhere else, so a
 * person learns one shape and reads its reverse for free — and nobody
 * can ship a pack where ON and OFF disagree about which way is up.
 */
export const chime: ActPack = {
  id: 'chime',
  label: 'Chime',
  description: 'Soft and round, like the rest of Chime',
  cues: {
    // Held, not glided. A press is the most frequent sound in the
    // product by an order of magnitude, and a pitch that MOVES is a
    // sound that asks to be listened to.
    press:         { wave: 'sine', from: 1400, to: 1400, dur: 0.014, gain: 0.026 },
    chip:          { wave: 'sine', from: 1850, to: 1850, dur: 0.012, gain: 0.024 },
    toggle_on:     { wave: 'sine', from: 620,  to: 780,  dur: 0.050, gain: 0.034 },
    toggle_off:    { wave: 'sine', from: 780,  to: 620,  dur: 0.050, gain: 0.034 },
    menu_pick:     { wave: 'sine', from: 1100, to: 1300, dur: 0.020, gain: 0.028 },
    // Places are the longest and the loudest here: they happen a
    // hundredth as often as a press and they mean the ground moved.
    page_open:     { wave: 'sine', from: 520,  to: 700,  dur: 0.060, gain: 0.046 },
    page_close:    { wave: 'sine', from: 700,  to: 520,  dur: 0.060, gain: 0.042 },
    surface_open:  { wave: 'sine', from: 640,  to: 820,  dur: 0.050, gain: 0.044 },
    surface_close: { wave: 'sine', from: 820,  to: 640,  dur: 0.050, gain: 0.040 },
    select_add:    { wave: 'sine', from: 900,  to: 1050, dur: 0.030, gain: 0.032 },
    select_clear:  { wave: 'sine', from: 1050, to: 820,  dur: 0.035, gain: 0.030 },
  },
};

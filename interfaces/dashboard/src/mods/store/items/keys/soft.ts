import type { KeyPack } from '../../../sound/keys';

/**
 * The same shape as `click` with the edge taken off — for somebody who
 * wants to know the key registered without announcing it to the room.
 */
export const soft: KeyPack = {
  id: 'soft',
  label: 'Soft',
  description: 'A quiet thock, for a shared room',
  cues: {
    letter:    { wave: 'sine',     from: 1400, to: 1150, dur: 0.014, gain: 0.035 },
    space:     { wave: 'sine',     from: 1000, to: 820,  dur: 0.018, gain: 0.038 },
    enter:     { wave: 'triangle', from: 1200, to: 1600, dur: 0.020, gain: 0.038 },
    backspace: { wave: 'sine',     from: 1100, to: 780,  dur: 0.016, gain: 0.032 },
  },
};

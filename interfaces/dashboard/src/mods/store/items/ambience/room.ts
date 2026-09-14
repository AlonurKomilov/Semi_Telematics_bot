import type { AmbiencePack } from '../../../sound/bed';

/**
 * Room — the sound a room makes when nothing in it is happening.
 *
 * The quietest bed here by some way, and it has to be: an office already
 * has air moving through it, and this is meant to sit under that rather
 * than beside it. One tone at mains-adjacent frequency, because that is
 * what a room full of equipment actually hums at.
 */
export const room: AmbiencePack = {
  id: 'room',
  label: 'Room',
  description: 'The hum of a room with nothing happening in it — barely there, and meant to be',
  bed: {
    filter: 'lowpass', freq: 220, q: 0.5, gain: 0.028,
    tones: [{ wave: 'sine', freq: 118, gain: 0.005 }],
  },
};

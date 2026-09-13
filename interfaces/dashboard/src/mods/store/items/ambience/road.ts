import type { AmbiencePack } from '../../../sound/bed';

/**
 * Road — the hum from inside a cab at speed.
 *
 * A low-passed rumble with two slow tones under it, near the engine
 * orders a truck actually sits at. Not a recording of a road: a road IS
 * broadband noise with everything above a few hundred hertz absorbed by
 * the cab, so the description and the thing are the same shape.
 */
export const road: AmbiencePack = {
  id: 'road',
  label: 'Road',
  description: 'The hum from inside a cab at speed — low, steady, nothing in it to listen to',
  bed: {
    filter: 'lowpass', freq: 320, q: 0.7, gain: 0.075,
    tones: [
      { wave: 'sine', freq: 62, gain: 0.012 },
      { wave: 'sine', freq: 93, gain: 0.007 },
    ],
  },
};

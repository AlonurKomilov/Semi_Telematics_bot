import type { AmbiencePack } from '../../../sound/bed';

/**
 * Rain — on the windscreen, from the dry side of it.
 *
 * Band-passed rather than low-passed: rain lives in the middle, and
 * taking the bottom out is what separates it from the road. No tones —
 * a tone under rain is a sound with a source, and rain has none.
 */
export const rain: AmbiencePack = {
  id: 'rain',
  label: 'Rain',
  description: 'On the windscreen, from the dry side — a wash with nothing underneath it',
  bed: { filter: 'bandpass', freq: 1400, q: 0.35, gain: 0.06 },
};

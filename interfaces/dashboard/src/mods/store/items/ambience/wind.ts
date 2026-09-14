import type { AmbiencePack } from '../../../sound/bed';

/**
 * Wind — air moving past a cab at speed, heard through glass.
 *
 * High-passed, which is what separates it from Road: the road is what
 * comes UP through the floor and the wind is what goes OVER the roof.
 * No tones — wind has no note in it, and adding one would make it a
 * draught somewhere with a source.
 */
export const wind: AmbiencePack = {
  id: 'wind',
  label: 'Wind',
  description: 'Air over the roof at speed, heard through glass — thinner than the road beneath it',
  bed: { filter: 'highpass', freq: 900, q: 0.4, gain: 0.05 },
};

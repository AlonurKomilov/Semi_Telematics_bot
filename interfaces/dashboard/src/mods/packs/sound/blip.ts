import type { SoundPack } from '../../sound/engine';

/**
 * Square waves, short. Reads as instrumentation rather than
 * notification — for a yard terminal where a chime sounds like a phone
 * somebody left on a desk.
 */
export const blip: SoundPack = {
  id: 'blip',
  label: 'Blip',
  cues: {
    alert:    { wave: 'square', from: 1000, to: 1000, dur: 0.06, gain: 0.10 },
    critical: { wave: 'square', from: 1400, to: 700,  dur: 0.14, gain: 0.13 },
    success:  { wave: 'square', from: 1200, to: 1600, dur: 0.05, gain: 0.08 },
    error:    { wave: 'sawtooth', from: 240, to: 160, dur: 0.16, gain: 0.12 },
    undo:     { wave: 'square', from: 800,  to: 800,  dur: 0.04, gain: 0.08 },
  },
};

import type { KeyPack } from '../../../sound/keys';

/**
 * Softer than Soft, and lower with it — a keyboard nobody in the next
 * bunk hears. The band is tight (a key cue is 6-50ms, gain under 0.08),
 * so this sits at the quiet end of it rather than inventing a new one.
 */
export const nightHaul: KeyPack = {
  id: 'night-haul',
  label: 'Night Haul',
  description: 'Barely there — a keyboard for a sleeping house',
  cues: {
    letter:    { wave: 'sine', from: 900, to: 760, dur: 0.012, gain: 0.022 },
    space:     { wave: 'sine', from: 680, to: 560, dur: 0.016, gain: 0.024 },
    enter:     { wave: 'sine', from: 820, to: 1020, dur: 0.018, gain: 0.026 },
    backspace: { wave: 'sine', from: 740, to: 520, dur: 0.014, gain: 0.020 },
  },
};

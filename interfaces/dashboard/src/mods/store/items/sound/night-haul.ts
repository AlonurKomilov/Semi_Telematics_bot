import type { SoundPack } from '../../../sound/engine';

/**
 * Chime an octave down with the gain pulled back — the same five
 * answers, told to a cab at 3am instead of an office at noon.
 *
 * Lower AND quieter, and the two do different work. Quieter is obvious.
 * Lower matters because a cab is loud in the bass and quiet up top: a
 * high cue cuts through road noise, which is exactly what Blip is for
 * and exactly wrong at night, when the person is already alone with the
 * screen and everything else is asleep.
 */
export const nightHaul: SoundPack = {
  id: 'night-haul',
  label: 'Night Haul',
  description: 'Low and quiet — told to a cab at 3am, not an office at noon',
  cues: {
    alert:    { wave: 'sine', from: 440, to: 220, dur: 0.35, gain: 0.10 },
    // Still starts higher and falls further than alert. Urgency is the
    // shape of the cue, never the loudness — that is the listener's.
    critical: { wave: 'sine', from: 660, to: 165, dur: 0.45, gain: 0.12 },
    success:  { wave: 'sine', from: 330, to: 495, dur: 0.16, gain: 0.08 },
    error:    { wave: 'triangle', from: 210, to: 130, dur: 0.28, gain: 0.09 },
    undo:     { wave: 'sine', from: 260, to: 260, dur: 0.10, gain: 0.07 },
  },
};

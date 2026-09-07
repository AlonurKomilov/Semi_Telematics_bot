import type { SoundPack } from '../../sound/engine';

/**
 * `chime` is not a new sound. Its `alert` cue is the one this app has
 * always played — 880 Hz gliding to 440 over 0.35s at gain 0.18, a sine
 * — reproduced exactly, so turning the engine on changes nothing anybody
 * would notice. The other four are the same voice answering different
 * questions.
 */
export const chime: SoundPack = {
  id: 'chime',
  label: 'Chime',
  description: 'The bell this app has always rung',
  cues: {
    alert:    { wave: 'sine', from: 880,  to: 440,  dur: 0.35, gain: 0.18 },
    // Two things separate critical from alert without being louder: it
    // starts higher and falls further. Loudness is the listener's
    // setting, not ours to spend on urgency.
    critical: { wave: 'sine', from: 1320, to: 330,  dur: 0.45, gain: 0.20 },
    // Rising, because everything that went right rises.
    success:  { wave: 'sine', from: 660,  to: 990,  dur: 0.16, gain: 0.14 },
    error:    { wave: 'triangle', from: 320, to: 190, dur: 0.28, gain: 0.16 },
    // Short and neutral: it marks that a window opened, and the window
    // is the message.
    undo:     { wave: 'sine', from: 520,  to: 520,  dur: 0.10, gain: 0.12 },
  },
};

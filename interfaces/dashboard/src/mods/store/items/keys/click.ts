import type { KeyPack } from '../../../sound/keys';

/**
 * A dry board — a short square blip with the pitch dropping on the
 * wider keys, which is what a real keyboard does: a spacebar is bigger,
 * so it sounds lower. Backspace moves DOWN, as it does in every pack —
 * a correction should not sound like progress.
 */
export const click: KeyPack = {
  id: 'click',
  label: 'Click',
  description: 'A crisp click on every key',
  cues: {
    letter:    { wave: 'square',   from: 2200, to: 1700, dur: 0.012, gain: 0.045 },
    space:     { wave: 'square',   from: 1500, to: 1100, dur: 0.016, gain: 0.05 },
    enter:     { wave: 'triangle', from: 1800, to: 2400, dur: 0.018, gain: 0.05 },
    backspace: { wave: 'square',   from: 1600, to: 1000, dur: 0.014, gain: 0.042 },
  },
};

/**
 * The keyboard, and the fields it must never speak over.
 *
 * Two properties carry this feature. Four classes rather than one, so
 * typing feels like a keyboard instead of a metronome — and four classes
 * rather than per-key, because per-key would make typed text audible to
 * anyone in the room.
 *
 * That ceiling is why the silence rule is the load-bearing half. Four
 * classes still leak length, word boundaries and corrections, which on a
 * shared dispatch floor is a real channel over a credential.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  KEY_CLASSES, KEY_PACKS, KEY_LIMITS, keyPackById,
  classify, isSensitiveTarget, pickKeyCue, resetKeySoundForTests,
} from './keys';
import { isCueWithin, CUE_LIMITS } from './engine';

const press = (init: Partial<KeyboardEvent> & { key: string }, target?: Element) => {
  const e = new KeyboardEvent('keydown', { bubbles: true, ...init } as KeyboardEventInit);
  if (target) Object.defineProperty(e, 'target', { value: target, configurable: true });
  return e;
};

beforeEach(() => { resetKeySoundForTests(); document.body.innerHTML = ''; });

describe('every pack can actually be played', () => {
  it('defines all four classes, inside the keyboard band', () => {
    expect(KEY_PACKS.length).toBeGreaterThan(1);
    for (const pack of KEY_PACKS)
      for (const cls of KEY_CLASSES) {
        const cue = pack.cues[cls];
        expect(cue, `${pack.id} has no "${cls}" cue`).toBeDefined();
        expect(isCueWithin(cue, KEY_LIMITS), `${pack.id}/${cls} is outside KEY_LIMITS`).toBe(true);
      }
  });

  it('needs its own band — a key click does not fit the notification one', () => {
    // The whole reason KEY_LIMITS exists. If a key cue also satisfied
    // CUE_LIMITS the parameterisation would be dead weight, and someone
    // would delete it.
    const shorter = KEY_PACKS.flatMap((p) => KEY_CLASSES.map((c) => p.cues[c]))
      .filter((c) => !isCueWithin(c, CUE_LIMITS));
    expect(shorter.length, 'every key cue fits CUE_LIMITS — the second band is pointless')
      .toBeGreaterThan(0);
    expect(KEY_LIMITS.gain.max, 'the key ceiling is not below the cue ceiling')
      .toBeLessThan(CUE_LIMITS.gain.max);
  });
});

describe('what counts as typing', () => {
  it('maps the four classes', () => {
    expect(classify(press({ key: 'a' }))).toBe('letter');
    expect(classify(press({ key: '7' }))).toBe('letter');
    expect(classify(press({ key: ' ' }))).toBe('space');
    expect(classify(press({ key: 'Enter' }))).toBe('enter');
    expect(classify(press({ key: 'Backspace' }))).toBe('backspace');
    expect(classify(press({ key: 'Delete' }))).toBe('backspace');
  });

  it('is silent for keys that are not typing', () => {
    for (const key of ['Shift', 'Control', 'ArrowLeft', 'F5', 'Tab', 'Escape', 'CapsLock'])
      expect(classify(press({ key })), `${key} made a sound`).toBeNull();
  });

  it('drops a held key — the browser repeats it about thirty times a second', () => {
    expect(classify(press({ key: 'a', repeat: true }))).toBeNull();
  });

  it('is silent for shortcuts — a Ctrl+C that clicks means nothing', () => {
    expect(classify(press({ key: 'c', ctrlKey: true }))).toBeNull();
    expect(classify(press({ key: 'c', metaKey: true }))).toBeNull();
    expect(classify(press({ key: 'c', altKey: true }))).toBeNull();
  });
});

describe('the fields that stay silent', () => {
  const el = (html: string): Element => {
    document.body.innerHTML = html;
    return document.body.firstElementChild!;
  };

  it('a password field', () => {
    expect(isSensitiveTarget(el('<input type="password" />'))).toBe(true);
  });

  it('anything the platform labels a credential, a card or a code', () => {
    for (const ac of [
      'current-password', 'new-password', 'cc-number', 'cc-csc',
      'credit-card-number', 'one-time-code',
    ])
      expect(isSensitiveTarget(el(`<input autocomplete="${ac}" />`)), ac).toBe(true);
  });

  it('anything inside a marked region, however deep', () => {
    document.body.innerHTML =
      '<fieldset data-no-key-sound><div><label><input id="ssn" /></label></div></fieldset>';
    expect(isSensitiveTarget(document.getElementById('ssn'))).toBe(true);
  });

  it('and an ordinary field is NOT silent — otherwise this is a mute button', () => {
    expect(isSensitiveTarget(el('<input type="text" />'))).toBe(false);
    expect(isSensitiveTarget(el('<textarea></textarea>'))).toBe(false);
    expect(isSensitiveTarget(el('<input autocomplete="email" />'))).toBe(false);
  });

  it('no cue is produced for a silenced field, whatever was typed', () => {
    const input = el('<input type="password" />');
    for (const key of ['a', ' ', 'Enter', 'Backspace'])
      expect(pickKeyCue(press({ key }, input), 'click'), key).toBeNull();
  });
});

describe('eight keystrokes a second do not sum into a buzz', () => {
  const target = () => {
    document.body.innerHTML = '<input type="text" />';
    return document.body.firstElementChild!;
  };

  it('drops a press that lands inside the floor rather than queueing it', () => {
    const t = target();
    expect(pickKeyCue(press({ key: 'a' }, t), 'click'), 'the first press was dropped').not.toBeNull();
    expect(pickKeyCue(press({ key: 'b' }, t), 'click'), 'two clicks inside 30ms would overlap').toBeNull();
  });

  it('the floor is longer than the longest cue, so two can never overlap', () => {
    const longest = Math.max(
      ...KEY_PACKS.flatMap((p) => KEY_CLASSES.map((c) => p.cues[c].dur)),
    );
    // 30ms, stated as a fact about the cues rather than a magic number.
    expect(longest).toBeLessThan(0.03);
  });

  it('an unknown pack yields nothing and does not throw', () => {
    expect(() => pickKeyCue(press({ key: 'a' }, target()), 'a-pack-that-left')).not.toThrow();
    resetKeySoundForTests();
    expect(pickKeyCue(press({ key: 'a' }, target()), 'a-pack-that-left')).toBeNull();
  });

  it('resolves the pack a person chose', () => {
    const t = target();
    expect(pickKeyCue(press({ key: 'a' }, t), 'click')).toEqual(keyPackById('click')!.cues.letter);
    resetKeySoundForTests();
    expect(pickKeyCue(press({ key: ' ' }, t), 'soft')).toEqual(keyPackById('soft')!.cues.space);
  });
});

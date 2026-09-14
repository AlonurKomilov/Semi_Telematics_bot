/**
 * The lane between three preferences and one continuous sound.
 *
 * `bed.test.ts` proves the engine can start, stop and refuse. Nothing
 * proved the SWITCH reaches it — which is the half a person actually
 * touches, and the half that would fail silently: a bed that never
 * starts and a bed that never stops both look like "the setting does
 * nothing" from the outside.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

const { startBed, stopBed, prefs } = vi.hoisted(() => ({
  startBed: vi.fn(), stopBed: vi.fn(),
  prefs: { current: { on: false, which: 'road', volume: 1 } },
}));

vi.mock('./bed', () => ({ startBed, stopBed }));
vi.mock('../../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: (k: string) => ({
    value: k === 'mods.sound.background' ? prefs.current.on
      : k === 'mods.sound.background.pack' ? prefs.current.which
        : prefs.current.volume,
    setValue: () => {},
  }),
}));

import { useBed } from './useBed';
import { armAudio, resetAudioForTests } from './engine';
import { ambienceById } from '../store/items/ambience';

function Host() { useBed(); return null; }
const mount = () => render(<Host />);

describe('the switch reaches the engine', () => {
  /** The gesture a browser requires before any audio may sound. */
  const unlock = () => {
    armAudio();
    window.dispatchEvent(new Event('pointerdown'));
  };

  beforeEach(() => {
    cleanup();
    resetAudioForTests();
    startBed.mockClear(); stopBed.mockClear();
    prefs.current = { on: false, which: 'road', volume: 1 };
  });

  it('plays nothing while the switch is off', () => {
    mount();
    expect(startBed, 'a bed started with the switch off').not.toHaveBeenCalled();
    expect(stopBed).toHaveBeenCalled();
  });

  it('starts the chosen bed when it is on and audio is unlocked', () => {
    unlock();
    prefs.current.on = true;
    mount();
    expect(startBed).toHaveBeenCalledWith(ambienceById('road')!.bed, 1);
  });

  it('waits for the gesture rather than giving up — the reload case', () => {
    // THE bug this file exists to keep closed. On a reload the effect
    // runs before anything has been clicked; a bare `startBed` returns
    // early, nothing ever runs it again, and the feature makes no sound
    // for the rest of the session. It was shipped that way.
    prefs.current.on = true;
    mount();
    expect(startBed, 'a bed started before any gesture — the browser would refuse it')
      .not.toHaveBeenCalled();
    unlock();
    expect(startBed, 'the bed never started after the gesture that allowed it')
      .toHaveBeenCalledWith(ambienceById('road')!.bed, 1);
  });

  it('and a page that leaves before the gesture leaves nothing waiting', () => {
    prefs.current.on = true;
    const { unmount } = mount();
    unmount();
    startBed.mockClear();
    unlock();
    expect(startBed, 'a bed started into a page that had already gone')
      .not.toHaveBeenCalled();
  });

  it('plays nothing at zero volume, however on', () => {
    prefs.current = { on: true, which: 'road', volume: 0 };
    mount();
    expect(startBed, 'a silenced app played a bed').not.toHaveBeenCalled();
  });

  it('plays nothing for a bed that no longer ships', () => {
    // A stored id outlives the item it names — the sanitiser drops it on
    // read, and this is the frame before that lands.
    prefs.current = { on: true, which: 'gone', volume: 1 };
    mount();
    expect(startBed).not.toHaveBeenCalled();
  });

  it('stops when the page leaves, so nothing plays over a closed tab', () => {
    unlock();
    prefs.current.on = true;
    const { unmount } = mount();
    stopBed.mockClear();
    unmount();
    expect(stopBed, 'the bed outlived the provider').toHaveBeenCalled();
  });
});

import { useEffect } from 'react';
import { usePreference } from '../../preferences';
import { ambienceById } from '../store/items/ambience';
import { startBed, stopBed } from './bed';
import { onUnlocked } from './engine';

/**
 * The bed's one lane: three preferences in, one continuous sound out.
 *
 * Mounted ONCE, by the provider. A hook that started a bed per caller
 * would layer them — every extra copy adding its own gain to the same
 * output, which is how a quiet rumble becomes a roar nobody asked for.
 *
 * The volume is the same one the cues ride, deliberately: a person who
 * has turned the sound down has turned the sound down, and a bed that
 * kept its own level would be the one thing on the page that did not
 * listen.
 */
export function useBed(): void {
  const { value: on } = usePreference('mods.sound.background');
  const { value: which } = usePreference('mods.sound.background.pack');
  const { value: volume } = usePreference('mods.sound.volume');

  useEffect(() => {
    const pack = ambienceById(which);
    if (!on || !pack || volume <= 0) { stopBed(); return; }
    // NOT a bare `startBed`. On a reload this effect runs before any
    // gesture has unlocked audio, `startBed` returns early, and nothing
    // ever runs it again — which is a bed that plays exactly once, the
    // first time the switch is turned on, and never afterwards.
    const cancel = onUnlocked(() => startBed(pack.bed, volume));
    return () => { cancel(); stopBed(); };
  }, [on, which, volume]);
}

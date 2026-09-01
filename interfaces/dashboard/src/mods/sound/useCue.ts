import { useCallback, useEffect } from 'react';
import { usePreference } from '../../preferences';
import { armAudio, playCue, soundPackById, type CueName } from './engine';

/**
 * Play a named cue at this screen's volume, from this person's pack.
 *
 * A hook rather than a bare function because both halves are
 * preferences, and reading them at the call site is what keeps a
 * changed pack or a moved volume slider live without a reload.
 *
 * Arming happens here too: browsers grant audio on a gesture, and the
 * first component that might make a sound is as good a place as any to
 * start listening for one. Calling it repeatedly is free — after the
 * first gesture it returns immediately.
 */
export function useCue(): (name: CueName) => void {
  const { value: packId } = usePreference('sound.pack');
  const { value: volume } = usePreference('sound.volume');

  useEffect(() => { armAudio(); }, []);

  return useCallback((name: CueName) => {
    if (volume <= 0) return;
    const cue = soundPackById(packId)?.cues[name];
    if (cue) playCue(cue, volume);
  }, [packId, volume]);
}

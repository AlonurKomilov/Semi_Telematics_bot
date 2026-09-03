/**
 * Whether this screen has settled.
 *
 * Presence is measured, not guessed: any of the five pointer/key events
 * resets the clock, and a hidden tab is treated as absent immediately —
 * a backgrounded tablet is the clearest case of nobody looking, and
 * waiting three more minutes to admit it wastes the whole window.
 *
 * Returns false and installs nothing while the gate is off, so a screen
 * that never wants this never pays a listener for it.
 */
import { useEffect, useRef, useState } from 'react';
import { usePreference } from '../../preferences';
import { AMBIENT_AFTER_MS, PRESENCE_EVENTS } from './ambient';

export function useAmbient(afterMs: number = AMBIENT_AFTER_MS): boolean {
  const { value: enabled } = usePreference('mods.ambient');
  const [idle, setIdle] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!enabled) { setIdle(false); return; }

    const arm = () => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => setIdle(true), afterMs);
    };
    const present = () => { setIdle(false); arm(); };
    const visibility = () => {
      // Hidden is not "idle in three minutes", it is idle now.
      if (document.hidden) { if (timer.current !== null) clearTimeout(timer.current); setIdle(true); }
      else present();
    };

    for (const ev of PRESENCE_EVENTS) window.addEventListener(ev, present, { passive: true });
    document.addEventListener('visibilitychange', visibility);
    arm();

    return () => {
      for (const ev of PRESENCE_EVENTS) window.removeEventListener(ev, present);
      document.removeEventListener('visibilitychange', visibility);
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [enabled, afterMs]);

  return enabled && idle;
}

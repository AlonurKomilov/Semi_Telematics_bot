/**
 * Per-user tour verdicts — preferences service, synced scope, so a
 * skip on the desktop holds on the laptop (precedent:
 * `config.moved_notice_dismissed`).  The backend never reads this;
 * eligibility is computed client-side at page open, which is what
 * "schedule for the next visit" means in practice.
 */
import { useCallback } from 'react';
import { usePreference } from '../../preferences';
import type { TourState, TourStatus } from './types';

export function useTourState(): {
  state: TourState;
  record: (tourKey: string, s: TourStatus) => void;
} {
  const { value, setValue } = usePreference('tour.state');
  const record = useCallback(
    (tourKey: string, s: TourStatus) => {
      setValue((prev) => ({
        ...(prev ?? {}),
        [tourKey]: { s, t: new Date().toISOString() },
      }));
    },
    [setValue],
  );
  return { state: value ?? {}, record };
}

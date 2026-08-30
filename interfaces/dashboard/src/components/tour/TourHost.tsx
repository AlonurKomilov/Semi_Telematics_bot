/**
 * The one line a page adds to get tours.
 *
 *     <TourHost feature="maintenance"
 *                    ctx={{ count: allTasks.length, canCreate }} />
 *
 * Picks at most ONE eligible tour for this page (never a queue — a
 * second idea on the same visit is noise), offers the intro, and runs
 * the engine on Show me.  Permission gating happens twice without this
 * component knowing: the page itself is behind the feature's
 * permission, and the tour's `relevant()` sees the ctx.
 */
import { useEffect, useRef, useState } from 'react';
import { apiJSON } from '../../api/client';
import { useSyncLoaded } from '../../preferences';
import { TOUR_CATALOG, eligibleTour } from './tourCatalog';
import TourIntro from './TourIntro';
import TourOverlay from './TourOverlay';
import { useTourState } from './useTourState';
import type { TourCtx, TourSpec } from './types';

export default function TourHost({
  feature,
  ctx,
}: {
  feature: string;
  ctx: TourCtx;
}) {
  const { state, record } = useTourState();
  const syncLoaded = useSyncLoaded();
  // Decide ONCE, and only after the synced verdicts have arrived.
  // Before hydration the local state can be empty, and deciding on it
  // re-offers a tour the user skipped on another device — the exact
  // promise `tour.state`'s synced scope makes.  After deciding,
  // never revisit: eligibility flipping mid-visit (a task created, the
  // count crossing the line) must not pop a dialog over someone's
  // half-filled form.  Next visit is soon enough.
  const decided = useRef(false);
  const [offered, setOffered] = useState<TourSpec | null>(null);
  useEffect(() => {
    if (!syncLoaded || decided.current) return;
    decided.current = true;
    let cancelled = false;
    (async () => {
      // Behavioural signals, when any of this feature's tours declare
      // them — one request for the union of pairs.  The endpoint being
      // unreachable degrades to page-local evidence inside each tour's
      // relevant(); it must never degrade to a thrown offer.
      const pairs = [...new Set(
        TOUR_CATALOG
          .filter((t) => t.feature === feature)
          .flatMap((t) => t.signals ?? []),
      )];
      let signals: TourCtx['signals'];
      if (pairs.length) {
        try {
          const res = await apiJSON<{ signals: NonNullable<TourCtx['signals']> }>(
            `/me/tour-signals?pairs=${encodeURIComponent(pairs.join(','))}`);
          signals = res.signals;
        } catch { /* degrade, don't silence */ }
      }
      if (!cancelled) setOffered(eligibleTour(feature, { ...ctx, signals }, state));
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- decide-once: later ctx/state changes are deliberately ignored
  }, [syncLoaded]);
  const [phase, setPhase] = useState<'intro' | 'touring' | 'off'>('intro');

  if (!offered || phase === 'off') return null;
  if (phase === 'touring') {
    return (
      <TourOverlay
        tour={offered}
        onDone={() => { record(offered.key, 'done'); setPhase('off'); }}
        onExit={() => { record(offered.key, 'snoozed'); setPhase('off'); }}
      />
    );
  }
  return (
    <TourIntro
      tour={offered}
      onShowMe={() => setPhase('touring')}
      onSkip={() => { record(offered.key, 'skipped'); setPhase('off'); }}
      onSnooze={() => { record(offered.key, 'snoozed'); setPhase('off'); }}
    />
  );
}

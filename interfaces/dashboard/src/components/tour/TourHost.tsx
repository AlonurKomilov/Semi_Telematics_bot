/**
 * The one line a page adds to get tours.
 *
 *     <TourHost feature="maintenance"
 *               ctx={{ count: allTasks.length, canCreate }} />
 *
 * Two ways in, three phases.  The AUTOMATIC path is an invitation:
 * eligibility picks at most ONE tour and shows the beacon — a small
 * spark beside the control the tour teaches — and only a press opens
 * the intro (beacon → intro → touring).  The MANUAL path is a command:
 * the Tours page navigates here with ?tour=<key>, and an explicit
 * "Start tour" overrides every verdict — done, skipped, adopted,
 * not-relevant — because a person asking to re-learn outranks every
 * heuristic about whether they need to.  Repetition is how tours
 * teach; the override is what makes the Tours page a real library
 * rather than a list of things you may no longer open.
 */
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import { useSyncLoaded } from '../../preferences';
import { TOUR_CATALOG, eligibleTour } from './tourCatalog';
import TourBeacon from './TourBeacon';
import TourIntro from './TourIntro';
import TourOverlay from './TourOverlay';
import { useTourState } from './useTourState';
import type { TourCtx, TourSpec } from './types';

/** The manual-launch lookup, pure for testing: the ?tour= key must
 *  name a catalog tour belonging to THIS page's feature. */
export function resolveManualTour(
  feature: string, key: string | null,
): TourSpec | null {
  if (!key) return null;
  return TOUR_CATALOG.find(
    (t) => t.key === key && t.feature === feature) ?? null;
}

export default function TourHost({
  feature,
  ctx,
}: {
  feature: string;
  ctx: TourCtx;
}) {
  const { state, record } = useTourState();
  const syncLoaded = useSyncLoaded();
  const [searchParams, setSearchParams] = useSearchParams();
  const decided = useRef(false);
  const [offered, setOffered] = useState<TourSpec | null>(null);
  const [observed, setObserved] = useState<number | null>(null);
  const [phase, setPhase] = useState<'beacon' | 'intro' | 'touring' | 'off'>('beacon');

  useEffect(() => {
    // Manual launch is checked BEFORE the decide-once guard, not
    // behind it: the override must hold by construction, not by the
    // accident that navigating here happens to remount this host.  A
    // ?tour= appearing on an already-decided instance (a same-page
    // relaunch, a future overlay-style Tours panel) still launches.
    const manual = resolveManualTour(feature, searchParams.get('tour'));
    if (manual) {
      decided.current = true;
      const next = new URLSearchParams(searchParams);
      next.delete('tour');
      setSearchParams(next, { replace: true });
      setOffered(manual);
      setObserved(null);
      setPhase('touring');
      return;
    }
    if (decided.current) return;

    if (!syncLoaded) return;
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
      if (cancelled) return;
      const full = { ...ctx, signals };
      const spec = eligibleTour(feature, full, state);
      setOffered(spec);
      setObserved(spec?.observedCount?.(full) ?? null);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- decide-once: later ctx/state changes are deliberately ignored
  }, [syncLoaded, searchParams]);

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
  if (phase === 'intro') {
    return (
      <TourIntro
        tour={offered}
        observed={observed}
        onShowMe={() => setPhase('touring')}
        onSkip={() => { record(offered.key, 'skipped'); setPhase('off'); }}
        onSnooze={() => { record(offered.key, 'snoozed'); setPhase('off'); }}
      />
    );
  }
  return <TourBeacon tour={offered} onOpen={() => setPhase('intro')} />;
}

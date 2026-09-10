/**
 * One reading of the tour library for every surface that shows it —
 * the Tours page and the avatar menu's line.  Reachable tours for this
 * role and its modules, the behavioural signals in one request, and
 * the checklist model over both.
 */
import { useEffect, useMemo, useState } from 'react';
import { apiJSON } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { TOUR_CATALOG } from '../../components/tour';
import type { TourCtx } from '../../components/tour';
import { useTourState } from '../../components/tour/useTourState';
import { useSyncLoaded } from '../../preferences';
import { reachableFeature } from './reachable';
import { libraryModel, signalPairs } from './library';
import type { LibraryModel } from './library';

type Reachable = { tour: (typeof TOUR_CATALOG)[number]; feature: NonNullable<ReturnType<typeof reachableFeature>> };

export function useTourLibrary(): { model: LibraryModel<Reachable['tour'], Reachable['feature']>; ready: boolean } {
  const { user } = useAuth();
  const { hasAny } = useViewPermissions();
  const { state } = useTourState();
  // Verdicts wait for the synced preferences to hydrate — the
  // pre-hydration value is empty, and stamping every card "New" for a
  // beat before flipping to Done is the provisional-value flash the
  // preferences contract names (TourHost gates the same read).
  const ready = useSyncLoaded();

  const reachable = useMemo<Reachable[]>(() => {
    const access = { hasAny, enabledModules: user?.enabled_modules };
    return TOUR_CATALOG.flatMap((tour) => {
      const feature = reachableFeature(tour.feature, access);
      if (!feature) return [];
      // The tour's OWN grant, not just its page's — a page frequently
      // opens on a wider permission than the controls a tour walks
      // through.
      if (tour.requires?.length && !hasAny(...tour.requires)) return [];
      return [{ tour, feature }];
    });
  }, [hasAny, user?.enabled_modules]);

  // The behavioural signals every reachable tour declares, in one
  // request — the same read TourHost makes per page, so "you already
  // do this" here is the same fact the beacon retires on.
  const [signals, setSignals] = useState<TourCtx['signals']>(undefined);
  const pairs = useMemo(() => signalPairs(reachable), [reachable]);
  useEffect(() => {
    let live = true;
    if (!pairs.length) { setSignals(undefined); return; }
    apiJSON<{ signals: NonNullable<TourCtx['signals']> }>(
      `/me/tour-signals?pairs=${encodeURIComponent(pairs.join(','))}`)
      .then((res) => { if (live) setSignals(res.signals); })
      .catch(() => { if (live) setSignals(undefined); });   // unknown ≠ adopted
    return () => { live = false; };
  }, [pairs]);

  const model = useMemo(
    () => libraryModel(reachable, ready ? state : {}, signals),
    [reachable, state, signals, ready],
  );
  return { model, ready };
}

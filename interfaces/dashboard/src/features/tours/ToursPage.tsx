/**
 * The tour library — every walkthrough, browsable and re-runnable.
 *
 * One-shot tours don't teach; repetition does.  The automatic path
 * (beacon → intro) is deliberately conservative — skip is final,
 * adoption retires — so this page is the unconditional way back:
 * "Start tour" overrides every recorded verdict, because a person
 * asking to re-learn outranks every heuristic about whether they
 * need to.  Launching navigates to the feature's own page with
 * ?tour=<key>; the walk always happens on the real surface it
 * teaches, never on screenshots of it.
 */
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, GraduationCap, Play, RotateCcw } from 'lucide-react';
import { PageHeader } from '@/components/shell';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useAuth } from '../../context/AuthContext';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { TOUR_CATALOG } from '../../components/tour';
import { useTourState } from '../../components/tour/useTourState';
import { useSyncLoaded } from '../../preferences';
import { reachableFeature } from './reachable';

export default function ToursPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { hasAny } = useViewPermissions();
  const { state } = useTourState();
  // Verdict chips wait for the synced preferences to hydrate — the
  // pre-hydration value is empty, and stamping every card "New" for a
  // beat before flipping to Done is the provisional-value flash the
  // preferences contract names (TourHost gates the same read).
  const verdictsReady = useSyncLoaded();

  const rows = useMemo(() => {
    const access = { hasAny, enabledModules: user?.enabled_modules };
    return TOUR_CATALOG.flatMap((tour) => {
      const feature = reachableFeature(tour.feature, access);
      if (!feature) return [];
      // The tour's OWN grant, not just its page's — a page frequently
      // opens on a wider permission than the controls a tour walks
      // through.  Without this the library offers a card whose first
      // step points at a button the viewer cannot see.
      if (tour.requires?.length && !hasAny(...tour.requires)) return [];
      return [{ tour, feature }];
    });
  }, [hasAny, user?.enabled_modules]);

  return (
    <div className="p-6">
      <PageHeader
        icon={GraduationCap}
        title={t('nav.tours')}
        description={t('tour.page.description')}
      />
      {rows.length === 0 ? (
        <Card>
          <p className="text-sm text-muted-foreground">{t('tour.page.empty')}</p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {rows.map(({ tour, feature }) => {
            const verdict = verdictsReady ? state[tour.key]?.s : undefined;
            const done = verdict === 'done';
            return (
              <Card key={tour.key} className="flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {t(feature.labelKey)}
                    </p>
                    <h3 className="text-base font-semibold text-foreground">
                      {t(`tour.${tour.key}.title`)}
                    </h3>
                  </div>
                  {!verdictsReady ? null : done ? (
                    <Badge tone="ok">
                      <CheckCircle2 />
                      {t('tour.page.status_done')}
                    </Badge>
                  ) : verdict === 'skipped' ? (
                    <Badge tone="neutral">{t('tour.page.status_skipped')}</Badge>
                  ) : (
                    <Badge tone="info">{t('tour.page.status_new')}</Badge>
                  )}
                </div>
                <p className="flex-1 text-sm text-muted-foreground">
                  {t(`tour.${tour.key}.body`)}
                </p>
                <div>
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`${feature.path}?tour=${encodeURIComponent(tour.key)}`)}
                    className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary-hover transition min-h-tap"
                  >
                    {done ? <RotateCcw className="size-3.5" /> : <Play className="size-3.5" />}
                    {done ? t('tour.page.run_again') : t('tour.page.start')}
                  </button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

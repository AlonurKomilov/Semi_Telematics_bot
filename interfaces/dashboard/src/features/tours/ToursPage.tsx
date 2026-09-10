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
 *
 * The page reads as a checklist, not a catalogue: how far along the
 * person is (counting what they already do on their own, by signal),
 * ONE next tour chosen for them with a single button, the settled ones
 * dimmed at the end — and the person's own switch to stop the beacons
 * on pages, kept here where it can be undone.
 */
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { BookOpen, CheckCircle2, GraduationCap, MessageSquare, Play, RotateCcw } from '../../lib/icons';
import { PageHeader } from '@/components/shell';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { usePreference } from '../../preferences';
import { useTourLibrary } from './useTourLibrary';
import type { LibraryStatus } from './library';

export default function ToursPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { hasAny } = useViewPermissions();
  const { value: hidden, setValue: setHidden } = usePreference('tour.hidden');
  const { model, ready: verdictsReady } = useTourLibrary();

  const launch = (path: string, key: string) =>
    navigate(`${path}?tour=${encodeURIComponent(key)}`);

  const statusChip = (status: LibraryStatus) => {
    if (!verdictsReady) return null;
    if (status === 'done') return <Badge tone="ok"><CheckCircle2 />{t('tour.page.status_done')}</Badge>;
    if (status === 'adopted') return <Badge tone="ok"><CheckCircle2 />{t('tour.page.status_adopted')}</Badge>;
    if (status === 'skipped') return <Badge tone="neutral">{t('tour.page.status_skipped')}</Badge>;
    return <Badge tone="info">{t('tour.page.status_new')}</Badge>;
  };

  return (
    <div className="p-6">
      <PageHeader
        icon={GraduationCap}
        title={t('nav.tours')}
        description={t('tour.page.description')}
      />
      {model.total === 0 ? (
        <Card>
          <p className="text-sm text-muted-foreground">{t('tour.page.empty')}</p>
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          {/* Progress — counted over what the person already does, not
              only what they ran; the number that makes "3 of 8" a
              gradient rather than a to-do list. */}
          {verdictsReady && (
            <p className="text-sm text-muted-foreground tabular-nums" aria-live="polite">
              {t('tour.page.progress', { done: model.done, total: model.total })}
              {model.next === null && model.done === model.total && (
                <span className="ml-2 text-foreground">{t('tour.page.all_done')}</span>
              )}
            </p>
          )}

          {/* ONE next tour, chosen for the person, with one button —
              the decision made so they do not have to. */}
          {verdictsReady && model.next && (
            <Card className="border-primary/40 bg-primary/5 flex flex-col gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-primary">
                {t('tour.page.next_title')}
              </p>
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {t(model.next.feature.labelKey)}
                  </p>
                  <h3 className="text-base font-semibold text-foreground">
                    {t(`tour.${model.next.tour.key}.title`)}
                  </h3>
                  <p className="text-sm text-muted-foreground">{t(`tour.${model.next.tour.key}.body`)}</p>
                </div>
                <Button type="button" size="sm" onClick={() => launch(model.next!.feature.path, model.next!.tour.key)}>
                  <Play className="size-3.5" aria-hidden />
                  {t('tour.page.show_me')}
                </Button>
              </div>
            </Card>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {model.rows.map(({ tour, feature, status }) => {
              const settled = status === 'done' || status === 'adopted';
              return (
                // A settled card recedes — still here to run again, no
                // longer asking for attention.
                <Card key={tour.key} className={`flex flex-col gap-2 ${settled ? 'opacity-70' : ''}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        {t(feature.labelKey)}
                      </p>
                      <h3 className="text-base font-semibold text-foreground">
                        {t(`tour.${tour.key}.title`)}
                      </h3>
                    </div>
                    {statusChip(status)}
                  </div>
                  <p className="flex-1 text-sm text-muted-foreground">
                    {t(`tour.${tour.key}.body`)}
                  </p>
                  <div>
                    <Button
                      type="button"
                      size="sm"
                      variant={settled ? 'outline' : 'default'}
                      onClick={() => launch(feature.path, tour.key)}
                    >
                      {settled ? <RotateCcw className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
                      {settled ? t('tour.page.run_again') : t('tour.page.start')}
                    </Button>
                  </div>
                </Card>
              );
            })}
          </div>

          {/* The person's own switch — kept here, where it can be undone. */}
          <Card className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{t('tour.page.hide_label')}</p>
              <p className="text-xs text-muted-foreground">{t('tour.page.hide_hint')}</p>
            </div>
            <Switch
              checked={!!hidden}
              onCheckedChange={(next) => setHidden(next)}
              size="md"
              aria-label={t('tour.page.hide_label')}
            />
          </Card>

          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {hasAny('can_view_knowledge_base') && (
              <Link to="/knowledge" className="inline-flex items-center gap-1 text-primary hover:underline">
                <BookOpen className="size-3.5" aria-hidden /> {t('tour.page.footer_kb')}
              </Link>
            )}
            {hasAny('can_view_ai_assistant') && (
              <Link to="/ai" className="inline-flex items-center gap-1 text-primary hover:underline">
                <MessageSquare className="size-3.5" aria-hidden /> {t('tour.page.footer_ai')}
              </Link>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

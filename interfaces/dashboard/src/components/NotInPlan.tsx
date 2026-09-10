import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Lock } from '../lib/icons';
import { FEATURE_CATALOG } from '../config/featureCatalog';
import { cn } from '@/lib/utils';
import { cardVariants } from '@/components/ui/card';
import { buttonVariants } from '@/components/ui/button';

/** What a door says to the one person who can open it: this feature
 *  is not in the account's plan.  Rendered in place of the page, only
 *  for a view that holds can_manage_billing (ProtectedRoute decides);
 *  a plain member never sees it — for them the feature is absent. */
export function NotInPlan({ flags, planLabel }: { flags: string[]; planLabel: string }) {
  const { t } = useTranslation();
  const feature = FEATURE_CATALOG.find((f) => {
    const p = f.permission;
    const ps = p == null ? [] : Array.isArray(p) ? p : [p];
    return ps.some((x) => flags.includes(x));
  });
  const name = feature ? t(feature.labelKey) : t('plan.this_feature');
  return (
    <div className="p-6 max-w-lg mx-auto">
      <div className={cn(cardVariants({ padding: 'none' }), 'p-5 flex flex-col gap-3')} role="status">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Lock className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span>{t('plan.route_title', { feature: name })}</span>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('plan.route_body', { plan: planLabel || t('plan.current_plan') })}
        </p>
        <div>
          <Link
            to={feature ? `/billing?upgrade=${feature.id}` : '/billing'}
            className={buttonVariants({ size: 'sm' })}
          >
            {t('plan.upgrade')}
          </Link>
        </div>
      </div>
    </div>
  );
}

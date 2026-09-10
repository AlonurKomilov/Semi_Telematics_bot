/**
 * The avatar menu's line for Tours — per person, so it lives in the
 * personal menu: "Tours · 3 of 8 done · Next: …".  What Claude Code's
 * onboarding popover taught us, on the one door that is always in
 * reach without spending a topbar icon.  Renders nothing until the
 * grant and the verdicts are known, and nothing at all for a role
 * without the service or with no tour to show.
 */
import { useTranslation } from 'react-i18next';
import { GraduationCap } from '../../lib/icons';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { MenuButton } from '../../components/AvatarMenu';
import { useTourLibrary } from './useTourLibrary';

export function ToursMenuItem({ onGo }: { onGo: (path: string) => void }) {
  const { t } = useTranslation();
  const { has, ready: permsReady } = useViewPermissions();
  const { model, ready } = useTourLibrary();
  if (!permsReady || !has('can_view_tours') || model.total === 0) return null;
  const sub = !ready ? undefined
    : model.next
      ? `${t('tour.page.progress', { done: model.done, total: model.total })} · ${t('tour.menu.next', { title: t(`tour.${model.next.tour.key}.title`) })}`
      : `${t('tour.page.progress', { done: model.done, total: model.total })} · ${t('tour.menu.all_done')}`;
  return (
    <MenuButton
      icon={<GraduationCap className="size-3.5" />}
      label={t('nav.tours')}
      sub={sub}
      onClick={() => onGo('/tours')}
    />
  );
}

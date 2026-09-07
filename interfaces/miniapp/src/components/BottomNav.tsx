import { Tabbar } from '@telegram-apps/telegram-ui';
import { useTranslation } from 'react-i18next';
import {
  Icon24LocationMapOutline,
  Icon24TruckOutline,
  Icon24NotificationOutline,
  Icon24StatisticsOutline,
  Icon24UserOutline,
  Icon24MessageOutline,
  Icon24CheckCircleOutline,
} from '@vkontakte/icons';
import type { Page } from '../types';
import { haptics } from '../hooks/useTelegram';

interface Props {
  page: Page;
  onNavigate: (page: Page) => void;
  /** Number of pending alerts — displays a red bubble on the alerts tab. */
  alertCount?: number;
  /** User permissions — used to hide tabs the user cannot access. */
  userPerms?: Record<string, boolean>;
}

// ``labelKey`` resolves to the localised tab label at render time.
const ALL_TABS: { id: Page; labelKey: string; icon: React.ReactNode; permKeys?: string[] }[] = [
  { id: 'map',       labelKey: 'tabs.map',       icon: <Icon24LocationMapOutline /> },
  { id: 'vehicles',  labelKey: 'tabs.vehicles',  icon: <Icon24TruckOutline /> },
  // PTI tab visible to drivers (own scope) and fleet reviewers (all scope).
  { id: 'pti',       labelKey: 'tabs.pti',       icon: <Icon24CheckCircleOutline />,  permKeys: ['can_view_inspections'] },
  { id: 'alerts',    labelKey: 'tabs.alerts',    icon: <Icon24NotificationOutline />, permKeys: ['can_view_alerts'] },
  { id: 'scorecard', labelKey: 'tabs.scorecard', icon: <Icon24StatisticsOutline />,   permKeys: ['can_view_scorecards'] },
  // The AI assistant is a service granted per role (can_view_ai_assistant);
  // what it answers about is still the driver's own truck.
  { id: 'ai',        labelKey: 'tabs.ai',        icon: <Icon24MessageOutline />,      permKeys: ['can_view_ai_assistant'] },
  { id: 'profile',   labelKey: 'tabs.profile',   icon: <Icon24UserOutline /> },
];

export function BottomNav({ page, onNavigate, alertCount = 0, userPerms = {} }: Props) {
  const { t } = useTranslation();
  const hasPerms = Object.keys(userPerms).length > 0;

  const tabs = hasPerms
    ? ALL_TABS.filter(tab => {
        if (!tab.permKeys) return true;
        return tab.permKeys.some(k => userPerms[k]);
      })
    : ALL_TABS; // show all tabs while perms are still loading

  return (
    <nav className="tabbar-wrap" aria-label="Primary">
      <Tabbar>
        {tabs.map(tab => {
          const label = t(tab.labelKey);
          return (
            <Tabbar.Item
              key={tab.id}
              text={label}
              aria-label={label}
              selected={page === tab.id}
              onClick={() => {
                haptics.selection();
                onNavigate(tab.id);
              }}
            >
              <span style={{ position: 'relative', display: 'inline-block' }}>
                {tab.icon}
                {tab.id === 'alerts' && alertCount > 0 && (
                  <span className="tab-badge" aria-label={`${alertCount} ${t('alerts.title')}`}>
                    {alertCount > 99 ? '99+' : alertCount}
                  </span>
                )}
              </span>
            </Tabbar.Item>
          );
        })}
      </Tabbar>
    </nav>
  );
}

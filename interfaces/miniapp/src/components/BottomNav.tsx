import { Tabbar } from '@telegram-apps/telegram-ui';
import {
  Icon24LocationMapOutline,
  Icon24TruckOutline,
  Icon24NotificationOutline,
  Icon24StatisticsOutline,
  Icon24UserOutline,
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

const ALL_TABS: { id: Page; label: string; icon: React.ReactNode; permKeys?: string[] }[] = [
  { id: 'map',       label: 'Map',     icon: <Icon24LocationMapOutline /> },
  { id: 'vehicles',  label: 'Vehicles', icon: <Icon24TruckOutline /> },
  { id: 'alerts',    label: 'Alerts',  icon: <Icon24NotificationOutline />, permKeys: ['can_alerts_all', 'can_alerts_own'] },
  { id: 'scorecard', label: 'Score',   icon: <Icon24StatisticsOutline />,  permKeys: ['can_scorecard_all', 'can_scorecard_own'] },
  { id: 'profile',   label: 'Profile', icon: <Icon24UserOutline /> },
];

export function BottomNav({ page, onNavigate, alertCount = 0, userPerms = {} }: Props) {
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
        {tabs.map(tab => (
          <Tabbar.Item
            key={tab.id}
            text={tab.label}
            aria-label={tab.label}
            selected={page === tab.id}
            onClick={() => {
              haptics.selection();
              onNavigate(tab.id);
            }}
          >
            <span style={{ position: 'relative', display: 'inline-block' }}>
              {tab.icon}
              {tab.id === 'alerts' && alertCount > 0 && (
                <span className="tab-badge" aria-label={`${alertCount} pending alerts`}>
                  {alertCount > 99 ? '99+' : alertCount}
                </span>
              )}
            </span>
          </Tabbar.Item>
        ))}
      </Tabbar>
    </nav>
  );
}

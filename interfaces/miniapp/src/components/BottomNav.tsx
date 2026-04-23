import { Tabbar } from '@telegram-apps/telegram-ui';
import { Icon28LocationMapOutline, Icon28TruckOutline, Icon28Notifications } from '@vkontakte/icons';
import type { Page } from '../types';

interface Props {
  page: Page;
  onNavigate: (page: Page) => void;
}

const TABS: { id: Page; label: string; icon: React.ReactNode }[] = [
  { id: 'map',    label: 'Map',    icon: <Icon28LocationMapOutline /> },
  { id: 'trucks', label: 'Trucks', icon: <Icon28TruckOutline /> },
  { id: 'alerts', label: 'Alerts', icon: <Icon28Notifications /> },
];

export function BottomNav({ page, onNavigate }: Props) {
  return (
    <Tabbar>
      {TABS.map(tab => (
        <Tabbar.Item
          key={tab.id}
          text={tab.label}
          selected={page === tab.id}
          onClick={() => onNavigate(tab.id)}
        >
          {tab.icon}
        </Tabbar.Item>
      ))}
    </Tabbar>
  );
}

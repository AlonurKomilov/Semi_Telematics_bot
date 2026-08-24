/**
 * Live-banner level — how many alerts pop up on screen while you're on
 * the dashboard.  A radio group (mutually-exclusive, so radios not
 * toggles): the choice applies instantly and is per-device.
 */
import { BellRing } from 'lucide-react';
import {
  setBannerLevel, useBannerLevel, type BannerLevel,
} from './bannerLevel';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

const OPTIONS: { value: BannerLevel; label: string; hint: string }[] = [
  {
    value: 'all',
    label: 'All new alerts',
    hint: 'Every new alert pops up while you’re on the dashboard.',
  },
  {
    value: 'critical',
    label: 'Critical only',
    hint: 'Only critical alerts pop up. Everything still appears in the bell.',
  },
  {
    value: 'off',
    label: 'Off',
    hint: 'No pop-ups. Alerts still appear in the bell and on the board.',
  },
];

export default function BannerLevelCard() {
  const level = useBannerLevel();

  return (
    <Card render={<section />}>
      <SectionHeader size="card" icon={<BellRing className="size-4" />} className="mb-1">
            On-screen alerts
          </SectionHeader>
      <p className="text-xs text-muted-foreground mb-3">
        Which alerts pop up while you have the dashboard open.
      </p>
      <div className="space-y-1">
        {OPTIONS.map(({ value, label, hint }) => {
          const active = value === level;
          return (
            <label
              key={value}
              className="flex items-start gap-3 py-1.5 cursor-pointer"
            >
              <input
                type="radio"
                name="banner-level"
                checked={active}
                onChange={() => setBannerLevel(value)}
                className="accent-primary cursor-pointer mt-0.5"
              />
              <span className="flex-1 min-w-0">
                <span className="text-sm">{label}</span>
                <span className="block text-xs text-muted-foreground mt-0.5">{hint}</span>
              </span>
            </label>
          );
        })}
      </div>
    </Card>
  );
}

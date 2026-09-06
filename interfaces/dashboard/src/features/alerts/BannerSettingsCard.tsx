/**
 * In-app banner settings — where notification banners/toasts appear on
 * screen, chosen VISUALLY (three mini window mockups) instead of by
 * reading "top-right" labels: the thumbnail IS the preview, so the
 * choice needs no terminology.
 *
 * The preference moves the whole notification lane (banners, saves,
 * errors — one consistent home), applies instantly, and is per-device
 * (localStorage) — the position that suits a wall-mounted dispatch
 * screen isn't the one for a cab tablet.
 */
import { toast } from '../../lib/toast';
import { PanelTopClose } from 'lucide-react';
import {
  setNotifPosition, useNotifPosition, type NotifPosition,
} from '@/components/banners';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

const OPTIONS: { value: NotifPosition; label: string }[] = [
  { value: 'top-right', label: 'Top right' },
  { value: 'bottom-right', label: 'Bottom right' },
  { value: 'bottom-center', label: 'Bottom center' },
];

// Pill placement inside the mini window mockup, per option.
const PILL_POS: Record<NotifPosition, string> = {
  'top-right': 'top-1.5 right-1.5',
  'bottom-right': 'bottom-1.5 right-1.5',
  'bottom-center': 'bottom-1.5 left-1/2 -translate-x-1/2',
};

export default function BannerSettingsCard() {
  const position = useNotifPosition();

  const pick = (pos: NotifPosition) => {
    if (pos === position) return;
    setNotifPosition(pos);
    // Instant proof — the confirmation toast arrives AT the new position.
    toast.success('Notification position updated');
  };

  return (
    <Card render={<section />}>
      <SectionHeader size="card" icon={<PanelTopClose className="size-4" />} className="mb-1">
            Notification position
          </SectionHeader>
      <p className="text-xs text-muted-foreground mb-3">
        Where in-app notifications appear on this device.
      </p>
      <div className="grid grid-cols-3 gap-3">
        {OPTIONS.map(({ value, label }) => {
          const active = value === position;
          return (
            <button
              key={value}
              onClick={() => pick(value)}
              aria-pressed={active}
              className={`group rounded-lg border p-2 text-center transition-colors ${
                active
                  ? 'border-primary ring-1 ring-primary bg-primary/5'
                  : 'border-border hover:border-muted-foreground/40'
              }`}
            >
              {/* Mini window mockup — the thumbnail IS the preview */}
              <span className="relative block aspect-[4/3] rounded-md bg-muted/60 border border-border/60 overflow-hidden">
                <span className="absolute top-0 inset-x-0 h-2 bg-muted border-b border-border/60" />
                <span className="absolute left-1.5 top-3.5 right-6 h-1 rounded-sm bg-border" />
                <span className="absolute left-1.5 top-6 right-9 h-1 rounded-sm bg-border" />
                <span className="absolute left-1.5 top-8 right-7 h-1 rounded-sm bg-border/70" />
                <span
                  className={`absolute ${PILL_POS[value]} w-9 h-2.5 rounded-sm bg-foreground/80 shadow-sm`}
                  aria-hidden
                />
              </span>
              <span className={`mt-1.5 inline-flex items-center gap-1.5 text-xs font-medium ${
                active ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground'
              }`}>
                <span className={`size-2 rounded-full border ${
                  active ? 'bg-primary border-primary' : 'border-muted-foreground/50'
                }`} aria-hidden />
                {label}
              </span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

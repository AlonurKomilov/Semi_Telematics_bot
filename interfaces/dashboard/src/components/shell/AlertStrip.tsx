import type { LucideIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { toneClasses, type Tone } from '../../lib/status';

export interface AlertItem {
  label: string;
  count: number;
  href: string;
  icon: LucideIcon;
  /** "critical" lights up red, "warning" amber, "info" primary. */
  tone?: 'critical' | 'warning' | 'info';
}

interface AlertStripProps {
  items: AlertItem[];
}

const TONE_CLASSES: Record<NonNullable<AlertItem['tone']>, Tone> = {
  critical: 'danger',
  warning: 'warn',
  info: 'info',
};

export default function AlertStrip({ items }: AlertStripProps) {
  const visible = items.filter((i) => i.count > 0);
  if (visible.length === 0) return null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
      {visible.map((item) => {
        const Icon = item.icon;
        const cls = toneClasses(TONE_CLASSES[item.tone ?? 'info']);
        return (
          <Link
            key={item.label}
            to={item.href}
            className={`flex items-center justify-between gap-3 px-4 py-3 border rounded-xl transition ${cls}`}
          >
            <div className="flex items-center gap-3 min-w-0 min-h-tap">
              <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-card/40 shrink-0">
                <Icon className="size-4.5" />
              </span>
              <div className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-wide opacity-80">
                  Needs attention
                </p>
                <p className="text-sm font-medium truncate">
                  <span className="text-2xl font-bold mr-1.5 align-baseline">
                    {item.count}
                  </span>
                  {item.label}
                </p>
              </div>
            </div>
            <ChevronRight className="shrink-0 opacity-60 size-4" />
          </Link>
        );
      })}
    </div>
  );
}

import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

type Tone = 'default' | 'positive' | 'warning' | 'critical' | 'info';

const TONE_CLASSES: Record<Tone, { value: string; iconBg: string; iconFg: string }> = {
  default: {
    value: 'text-foreground',
    iconBg: 'bg-muted',
    iconFg: 'text-muted-foreground',
  },
  positive: {
    value: 'text-ok',
    iconBg: 'bg-ok-bg',
    iconFg: 'text-ok',
  },
  warning: {
    value: 'text-warn',
    iconBg: 'bg-warn-bg',
    iconFg: 'text-warn',
  },
  critical: {
    value: 'text-danger',
    iconBg: 'bg-danger-bg',
    iconFg: 'text-danger',
  },
  info: {
    value: 'text-info',
    iconBg: 'bg-info-bg',
    iconFg: 'text-info',
  },
};

interface KpiCardProps {
  label: string;
  value?: number | string;
  hint?: string;
  tone?: Tone;
  icon?: LucideIcon;
  onClick?: () => void;
  trailing?: ReactNode;
  /** Card acts as an active toggle (e.g. "this filter is applied").
   * Drawn as a ring so the VALUE never has to change to show state — a
   * card that rewrites its own number to indicate selection is
   * indistinguishable from the number really being that. */
  selected?: boolean;
}

export default function KpiCard({
  label,
  value,
  hint,
  tone = 'default',
  icon: Icon,
  onClick,
  trailing,
  selected = false,
}: KpiCardProps) {
  const t = TONE_CLASSES[tone];
  const interactive = !!onClick;

  const Wrapper: React.ElementType = interactive ? 'button' : 'div';

  return (
    <Wrapper
      onClick={onClick}
      {...(interactive ? { 'aria-pressed': selected } : {})}
      className={`bg-card border rounded-xl p-5 text-left w-full transition ${
        selected ? 'border-primary ring-1 ring-primary' : 'border-border'
      } ${
        // The hover border is omitted while selected — it's a weaker
        // primary than the selected one, so it would visually *undo* the
        // selection under the cursor.
        interactive
          ? `cursor-pointer hover:bg-card/80 ${selected ? '' : 'hover:border-primary/40'}`
          : ''
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide truncate">
            {label}
          </p>
          <p className={`text-3xl font-bold mt-1.5 ${t.value}`}>
            {value ?? '—'}
          </p>
          {hint && (
            <p className="text-xs text-muted-foreground mt-1.5 truncate">{hint}</p>
          )}
        </div>
        {Icon && (
          <span
            className={`inline-flex items-center justify-center w-9 h-9 rounded-lg shrink-0 ${t.iconBg} ${t.iconFg}`}
          >
            <Icon className="size-4.5" />
          </span>
        )}
        {trailing}
      </div>
    </Wrapper>
  );
}

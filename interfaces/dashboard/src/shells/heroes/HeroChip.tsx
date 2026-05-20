/**
 * Compact chip used in shell hero strips.
 *
 * Variants follow KpiCard's tone palette so the dashboard reads
 * consistently — green for positive/active, yellow for warnings, red
 * for critical, muted-gray for neutral counts.
 */
import type { ReactNode } from 'react';

type ChipTone = 'neutral' | 'positive' | 'warning' | 'critical' | 'info';

const TONE_CLASSES: Record<ChipTone, string> = {
  neutral:  'bg-muted/40 text-muted-foreground border-border/60',
  positive: 'bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/20',
  warning:  'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border-yellow-500/20',
  critical: 'bg-destructive/10 text-destructive border-destructive/30',
  info:     'bg-primary/10 text-primary border-primary/30',
};

interface HeroChipProps {
  label: string;
  value: ReactNode;
  tone?: ChipTone;
  title?: string;
}

export default function HeroChip({ label, value, tone = 'neutral', title }: HeroChipProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] border rounded ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="opacity-70">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </span>
  );
}

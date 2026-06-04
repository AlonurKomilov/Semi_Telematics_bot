/**
 * Compact chip used in shell hero strips.
 *
 * Variants follow KpiCard's tone palette so the dashboard reads
 * consistently — green for positive/active, yellow for warnings, red
 * for critical, muted-gray for neutral counts.
 */
import type { ReactNode } from 'react';
import { toneClasses, type Tone } from '../../lib/status';

type ChipTone = 'neutral' | 'positive' | 'warning' | 'critical' | 'info';

const CHIP_TONE: Record<ChipTone, Tone> = {
  neutral:  'neutral',
  positive: 'ok',
  warning:  'warn',
  critical: 'danger',
  info:     'info',
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
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-2xs border rounded ${toneClasses(CHIP_TONE[tone])}`}
      title={title}
    >
      <span className="opacity-70">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </span>
  );
}

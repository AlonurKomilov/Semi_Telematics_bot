/**
 * Compact chip used in shell hero strips.
 *
 * Variants follow KpiCard's tone palette so the dashboard reads
 * consistently — green for positive/active, yellow for warnings, red
 * for critical, muted-gray for neutral counts.
 */
import type { ReactNode } from 'react';
import { Tip } from '../../components/tooltip';
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
  const chip = (
    <span
      // ``shrink-0 whitespace-nowrap`` is load-bearing, not cosmetic.
      // Without it a chip in a full strip COMPRESSES below its natural
      // width and wraps to two or three lines — "On the road" measured
      // 58px against a 94px natural width and grew to 56px tall.  The
      // strip's ``overflow-x-auto`` then computes overflow-y to auto as
      // well, so it became a 66px scroll box inside a 48px header whose
      // parent clips: ~9px sliced off the top and bottom of every chip.
      // Refusing to shrink lets the intended horizontal scroll do its job.
      className={`inline-flex shrink-0 whitespace-nowrap items-center gap-1.5 px-2 py-0.5 text-2xs border rounded-md ${toneClasses(CHIP_TONE[tone])}`}
    >
      <span className="opacity-70">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </span>
  );
  return title ? <Tip label={title}>{chip}</Tip> : chip;
}


/** "9d" / "14h" / "40m" — the age of the oldest open critical.
 *
 *  Coarse on purpose: the decision it feeds is "is anything rotting?",
 *  and a precise duration invites reading it as a countdown. */
export function oldestCriticalAge(firstSeenIso: string): string {
  const ms = Date.now() - new Date(firstSeenIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

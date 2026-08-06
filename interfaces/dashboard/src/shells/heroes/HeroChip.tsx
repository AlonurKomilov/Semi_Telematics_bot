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

/** The strip a hero's chips sit in.  Six heroes rendered this byte for
 *  byte, and a seventh written tomorrow would have had to rediscover the
 *  ``overflow-y-hidden`` — which is NOT cosmetic: see the chip's own note
 *  below.  ``overflow-x-auto`` alone computes overflow-y to auto as well,
 *  turning a 48px header into a 66px scroll box whose parent clips ~9px
 *  off every chip.
 *
 *  A class constant rather than a component, deliberately.  Wrapping it
 *  would invite ``useWheelToHorizontal``, and this strip is already
 *  ``overflow-x-auto`` — the browser handles the gesture, so adding the
 *  bridge would DOUBLE every trackpad swipe.  That is the exact bug its
 *  own docblock warns about.
 *
 *  It carried a dead ``scrollbar-thin`` in all six copies: tailwind.config
 *  has ``plugins: []`` and no rule defines it, so it emitted nothing. */
export const HERO_STRIP =
  // ``hidden lg:flex`` — the strip is a WIDE-SCREEN overview and does not
  // survive a phone.  The topbar hands it whatever is left after the ☰ and
  // the six controls on the right, which at 375px is about 57px of a
  // 685px strip: 8%, showing a sliver of one chip with no hint that six
  // more exist.  It scrolls, so nothing was unreachable — it just read as
  // a rendering fault.
  //
  // Hiding loses nothing, because the same numbers live on the pages: the
  // Vehicles grid carries Moving / Idle / Stopped as its own tabs.
  //
  // ONE standalone display utility, not ``hidden`` beside ``flex`` — they
  // are the same Tailwind group, so both in one string is a coin-flip on
  // source order.
  // The container STAYS on every width — it is the ``flex-1`` spacer that
  // pushes the topbar's right cluster (search / language / bell / theme /
  // avatar) to the right edge.  Hiding the whole strip below ``lg`` took
  // the spacer with it and let the whole topbar bunch up on the left.
  //
  // So only the CHIPS hide.  ``[&>*]:hidden`` below lg, restored to their
  // own ``inline-flex`` at lg — matching what HeroChip renders, since a
  // plain ``flex`` here would change their box and their spacing.
  'flex-1 min-w-0 flex items-center px-2 gap-1.5 overflow-x-auto overflow-y-hidden '
  + '[&>*]:hidden lg:[&>*]:inline-flex';

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

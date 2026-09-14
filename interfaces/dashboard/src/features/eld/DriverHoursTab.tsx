/**
 * One driver's hours, on their own card.
 *
 * The same endpoint the fleet-wide page reads, asked for one person —
 * not a second store and not a second shape.  A driver's remaining
 * hours must read identically here and on /eld or one of the two is
 * lying, and there is no way for a reader to tell which.
 *
 * The three states this has to keep apart are the same three the page
 * keeps apart, for the same reason: no ELD connected, an ELD connected
 * but nothing for this person, and a reading that has gone stale.
 */
import { Link } from 'react-router-dom';
import { Clock, Plug } from '../../lib/icons';
import { CardSkeleton, EmptyState, ErrorState, KpiCard } from '../../components/shell';
import { Freshness } from '../../components/tooltip';
import { Badge } from '../../components/ui/badge';
import { statusTone, toneText } from '../../lib/status';
import { DUTY_LABELS, clockText, useHours } from './useHours';
import type { Clock as ClockValue, DriverHours } from './useHours';

/**
 * One clock, on the shell's own figure primitive.
 *
 * The `hint` slot carries the "not reported" explanation VISIBLY
 * rather than behind a hover: on a compliance surface, the difference
 * between "we do not know" and "no hours left" is the answer, and an
 * answer that requires hovering is one a dispatcher can miss.
 */
function ClockTile({ label, clock }: { label: string; clock: ClockValue | null }) {
  const out = clock !== null && clock.seconds <= 0;
  return (
    <KpiCard
      label={label}
      value={clock === null ? '—' : clockText(clock)}
      hint={clock === null ? 'not reported by the ELD' : undefined}
      tone={out ? 'critical' : 'default'}
    />
  );
}

export default function DriverHoursTab({ userId }: { userId: number }) {
  const { data, isLoading, isError, refetch } = useHours(userId);

  if (isLoading) return <CardSkeleton />;
  if (isError) {
    return (
      <ErrorState
        message="Couldn't load hours of service"
        onRetry={() => void refetch()}
      />
    );
  }

  // Never an empty panel on a compliance question: an absent feed and a
  // driver with hours left must not look the same.
  if (!data?.connected) {
    return (
      <EmptyState
        icon={Plug}
        title="No electronic logging device is connected"
        description="Nothing has been recorded for this account, so this is not a statement that this driver has hours remaining."
        action={
          <Link
            to="/integrations"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 min-h-tap bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary-hover transition"
          >
            Go to Integrations
          </Link>
        }
      />
    );
  }

  const row: DriverHours | undefined = data.drivers[0];
  if (!row) {
    return (
      <EmptyState
        icon={Clock}
        title="No reading for this driver"
        description="Hours of service IS connected for this account. This driver may not be linked to their record on the connected ELD yet — the Integrations tab is where that link is made."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <Badge tone={statusTone(row.duty_status)}>
          {DUTY_LABELS[row.duty_status] ?? row.duty_status}
        </Badge>
        <Freshness ts={row.as_of} cue={false}>
          <span
            className={`text-xs ${row.stale ? toneText('warn') : 'text-muted-foreground'}`}
          >
            {row.age_minutes === null
              ? 'reading age unknown'
              : `as reported ${Math.round(row.age_minutes)} min ago`}
            {row.source ? ` · ${row.source}` : ''}
          </span>
        </Freshness>
      </div>

      {/* Every clock counts DOWN.  There is no "used today" here
          because an ELD does not report one, and deriving it needs the
          ruleset's limit — which the certified device knows and we
          do not. */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <ClockTile label="Drive left" clock={row.drive_remaining} />
        <ClockTile label="Shift left" clock={row.shift_remaining} />
        <ClockTile label="Cycle left" clock={row.cycle_remaining} />
        <ClockTile label="Break due in" clock={row.break_in} />
      </div>

      <p className="text-xs text-muted-foreground max-w-prose">
        {data.record_of}{' '}
        <Link to="/eld" className="text-primary hover:underline">
          See every driver
        </Link>
      </p>
    </div>
  );
}

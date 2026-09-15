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
import { CLOCK_ORDER, DUTY_LABELS, clockCoverage, clockText, useHours }
  from './useHours';
import type { Clock as ClockValue, DriverHours, HosClockId } from './useHours';

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

/**
 * A floor under every branch.
 *
 * Skeleton, empty state and loaded body have very different heights,
 * and this panel sits in a drawer with content below it.  Without a
 * floor the region collapses and re-expands as the query resolves —
 * and again on every 60-second refetch, whenever `connected` or the
 * row's presence flips.  One box, contents swapped.
 */
function TabBody({ children }: { children: React.ReactNode }) {
  return <div className="min-h-48">{children}</div>;
}

export default function DriverHoursTab({ userId }: { userId: number }) {
  const { data, isLoading, isError, refetch } = useHours(userId);

  if (isLoading) return <TabBody><CardSkeleton /></TabBody>;
  if (isError) {
    return (
      <TabBody>
        <ErrorState
          message="Couldn't load hours of service"
          onRetry={() => void refetch()}
        />
      </TabBody>
    );
  }

  // Never an empty panel on a compliance question: an absent feed and a
  // driver with hours left must not look the same.
  if (!data?.connected) {
    return (
      <TabBody>
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
      </TabBody>
    );
  }

  const row: DriverHours | undefined = data.drivers[0];
  const coverage = clockCoverage(data);
  if (!row) {
    return (
      <TabBody>
        <EmptyState
          icon={Clock}
          title="No reading for this driver"
          description="Hours of service IS connected for this account. This driver may not be linked to their record on the connected ELD yet — the Integrations tab is where that link is made."
        />
      </TabBody>
    );
  }

  const CLOCK_LABELS: Record<HosClockId, string> = {
    drive: 'Drive left',
    shift: 'Shift left',
    cycle: 'Cycle left',
    break: 'Break due in',
  };
  const CLOCK_VALUES: Record<HosClockId, ClockValue | null> = {
    drive: row.drive_remaining,
    shift: row.shift_remaining,
    cycle: row.cycle_remaining,
    break: row.break_in,
  };
  const tiles = CLOCK_ORDER
    .filter((id) => coverage.reported.has(id))
    .map((id) => ({ id, label: CLOCK_LABELS[id], clock: CLOCK_VALUES[id] }));

  return (
    <TabBody>
    <div className="space-y-4">
      {/* gap-2 inside, space-y-4 between: the same 2:1 ratio the tile
          grid uses, so this row and the tiles read as two groups
          rather than one continuous run. */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
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
          do not.

          Only the clocks this account's ELD actually publishes get a
          tile.  Four tiles reading "—" would be four figures on a
          compliance card, and a figure is read before its hint is. */}
      {coverage.none ? (
        <p className="text-xs text-muted-foreground max-w-prose">
          {coverage.deviceLabel} reports duty status only — remaining
          drive, shift, cycle and break time are not available from it.
        </p>
      ) : (
        <div className={`grid grid-cols-2 gap-2 ${
          tiles.length > 2 ? 'sm:grid-cols-4' : 'sm:grid-cols-2'
        }`}>
          {tiles.map(({ id, label, clock }) => (
            <ClockTile key={id} label={label} clock={clock} />
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground max-w-prose">
        {data.record_of}{' '}
        <Link to="/eld" className="text-primary hover:underline">
          See every driver
        </Link>
      </p>
    </div>
    </TabBody>
  );
}

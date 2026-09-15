/**
 * Hours of Service — every driver's remaining clocks, fleet-wide.
 *
 * A read surface over a mirror.  The certified ELD is the system of
 * record; this page shows what it last reported and how long ago, and
 * it can never be edited from here.
 *
 * Two screen states do the real work:
 *
 *   no ELD connected — must NOT look like an empty fleet.  An empty
 *     table on a compliance page reads as "nobody is near their
 *     limit", which is the worst direction to be wrong in, so that
 *     state says the feed is absent and points at Integrations.
 *
 *   a stale reading — a duty clock changes by the second and the feed
 *     polls every five minutes, so a reading is always somewhat
 *     behind.  Past the server's own window it stops being shown as
 *     current, because a number an operator dispatches on has to
 *     carry its age.
 */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Clock, Plug } from '../../lib/icons';
import DataGrid from '../../components/datagrid';
import { PageHeader, CardSkeleton, EmptyState, ErrorState } from '../../components/shell';
import { Freshness, Tip } from '../../components/tooltip';
import { Badge } from '../../components/ui/badge';
import { statusTone, toneText } from '../../lib/status';
import type { AnyColumn } from '../../types';
import { CLOCK_ORDER, DUTY_LABELS, clockCoverage, clockText, countLowOnDrive, useHours }
  from './useHours';
import type { DriverHours, HosClockId, HoursResponse } from './useHours';

/**
 * One clock cell.
 *
 * `—` for a clock the ELD did not report and `0m` for a driver who has
 * run out are the two answers that must never look alike: the first is
 * a gap in the feed, the second is a driver who has to stop.  So the
 * dash is muted and carries its own explanation, and zero is painted
 * with the danger tone.
 */
function ClockCell({ clock }: { clock: DriverHours['drive_remaining'] }) {
  if (!clock) {
    return (
      <Tip label="The ELD did not report this clock — this is not zero hours remaining">
        <span className="text-muted-foreground/50 cursor-help tabular-nums">—</span>
      </Tip>
    );
  }
  const out = clock.seconds <= 0;
  return (
    <span className={`tabular-nums ${out ? `${toneText('danger')} font-medium` : ''}`}>
      {clockText(clock)}
    </span>
  );
}

const IDENTITY_COLUMNS: AnyColumn[] = [
  {
    key: 'driver',
    label: 'Driver',
    sortable: true,
    render: (v, row) => {
      const r = row as unknown as DriverHours;
      return (
        <div className="flex items-center gap-2">
          <span className={r.linked ? '' : 'text-muted-foreground'}>
            {String(v || '—')}
          </span>
          {/* A status about OUR RECORD of the driver, not about the
              driver — so it keeps the badge family and takes the
              outline weight, where duty status takes the fill. */}
          {!r.linked && (
            <Tip label="The ELD reports this driver, but nobody has linked them to a member of your team yet. Link them on the driver's Integrations tab.">
              <Badge tone="neutral" subtle className="cursor-help">
                unlinked
              </Badge>
            </Tip>
          )}
        </div>
      );
    },
  },
  { key: 'vehicle', label: 'Truck', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v || '—')}</span> },
  {
    key: 'duty_status',
    label: 'Duty status',
    sortable: true,
    render: (v) => {
      const status = String(v || 'unknown');
      return (
        <Badge tone={statusTone(status)}>{DUTY_LABELS[status] ?? status}</Badge>
      );
    },
  },
  {
    key: 'as_of',
    label: 'Reading',
    sortable: true,
    render: (v, row) => {
      const r = row as unknown as DriverHours;
      // The server owns the staleness rule — a duty clock's window is
      // minutes, far tighter than the generic freshness cue — so the
      // visible warning follows its flag and the hover reuses the
      // shared primitive for the exact age.
      return (
        <Freshness ts={String(v || '')} cue={false}>
          <span className={r.stale ? toneText('warn') : 'text-muted-foreground'}>
            {r.age_minutes === null
              ? 'age unknown'
              : `${Math.round(r.age_minutes)} min ago`}
          </span>
        </Freshness>
      );
    },
  },
];

/**
 * One clock column per countdown the ELD actually publishes.
 *
 * A column for a clock nobody reports is not an empty column — it is
 * four dashes down a compliance page, and a dash in a hours-remaining
 * column is read as a zero long before anybody hovers it.  So the set
 * is built from what the server says the device can report, and the
 * header says the rest in words.
 *
 * Per-cell dashes stay correct INSIDE a visible column: on an account
 * running two ELDs the column exists because one of them fills it, and
 * the other one's rows show `—` with its explanation.
 */
const CLOCK_COLUMNS: Record<HosClockId, AnyColumn> = {
  drive: { key: 'drive_remaining', label: 'Drive left',
    render: (_v, row) => <ClockCell clock={(row as unknown as DriverHours).drive_remaining} /> },
  shift: { key: 'shift_remaining', label: 'Shift left',
    render: (_v, row) => <ClockCell clock={(row as unknown as DriverHours).shift_remaining} /> },
  cycle: { key: 'cycle_remaining', label: 'Cycle left',
    render: (_v, row) => <ClockCell clock={(row as unknown as DriverHours).cycle_remaining} /> },
  break: { key: 'break_in', label: 'Break due in',
    render: (_v, row) => <ClockCell clock={(row as unknown as DriverHours).break_in} /> },
};

/** Identity, then the clocks that exist, then the reading's age. */
function buildColumns(reported: Set<HosClockId>): AnyColumn[] {
  const clocks = CLOCK_ORDER.filter((c) => reported.has(c))
    .map((c) => CLOCK_COLUMNS[c]);
  const readingAt = IDENTITY_COLUMNS.length - 1;
  return [
    ...IDENTITY_COLUMNS.slice(0, readingAt),
    ...clocks,
    IDENTITY_COLUMNS[readingAt],
  ];
}

/**
 * What the header says beyond the title.
 *
 * Two facts, both read from the rows rather than asserted: how many
 * drivers are close to stopping, and how many the caller's own vehicle
 * access removed.  The second is the one a surface usually leaves out —
 * showing three of ten and saying nothing lets the reader conclude
 * they are seeing everything.
 */
function HeaderMeta({ data }: { data: HoursResponse }) {
  const coverage = clockCoverage(data);
  // Only ask the question the device can answer.  On an ELD with no
  // drive clock this count is 0 for every fleet on earth, and printing
  // that 0 would state "nobody is close to their limit" on no evidence
  // at all — the exact misreading this page exists to prevent.
  const low = coverage.reported.has('drive')
    ? countLowOnDrive(data.drivers)
    : null;
  return (
    <div className="flex flex-col gap-1 text-xs">
      {/* Act on this.  Kept adjacent and at warn weight so the eye
          finds them without reading the whole strip. */}
      {((low ?? 0) > 0 || data.stale_count > 0) && (
        <div className="flex items-center gap-3 flex-wrap">
          {low !== null && low > 0 && (
            <span className={toneText('warn')}>
              {low} {low === 1 ? 'driver has' : 'drivers have'} under 1h
              drive time left
            </span>
          )}
          {data.stale_count > 0 && (
            <span className={toneText('warn')}>
              {data.stale_count} of {data.count} readings older than{' '}
              {data.stale_after_minutes} min
            </span>
          )}
        </div>
      )}
      {/* Why there is no warning above.
          A page that simply omits the "under 1h" line on a device that
          cannot measure it leaves the reader to conclude the fleet is
          clear.  This states the limit of the instrument instead, and
          names the device so the reader knows where to look for the
          number we do not have.  Context rather than alarm — muted, own
          line — because the drivers are not in trouble; we are just not
          the ones who can say. */}
      {coverage.missing.length > 0 && (
        <span className="text-muted-foreground">
          {coverage.none ? (
            <>
              {coverage.deviceLabel} reports duty status only — remaining
              drive, shift, cycle and break time are not available from
              it, so this page cannot say who is close to a limit.
            </>
          ) : (
            <>
              {coverage.deviceLabel} does not report{' '}
              {coverage.missing.join(', ')} time.
            </>
          )}
        </span>
      )}

      {/* What you are looking at.  Context, not a warning — its own
          line and muted, so it never competes with the two above. */}
      {data.hidden_by_scope > 0 && (
        <span className="text-muted-foreground">
          Showing {data.count} of {data.count + data.hidden_by_scope} drivers
          — limited to your vehicle access
        </span>
      )}
    </div>
  );
}

export default function HoursPage() {
  const { data, isLoading, isError, refetch } = useHours();

  const rows = useMemo(
    () => (data?.drivers ?? []) as unknown as Record<string, unknown>[],
    [data],
  );

  const columns = useMemo(
    () => buildColumns(clockCoverage(data).reported),
    [data],
  );
  // Sorting by a column that is not rendered leaves the grid's chip row
  // advertising a sort the reader cannot see or clear.
  const hasDrive = clockCoverage(data).reported.has('drive');

  return (
    <div className="space-y-4">
      <PageHeader
        title="Hours of Service"
        icon={Clock}
        // The description must not promise clocks this account's device
        // does not publish — an over-claim behind a ⓘ is still an
        // over-claim, and this is the surface where it would matter.
        description={
          clockCoverage(data).none && data?.connected
            ? `Duty status for every driver, mirrored from ${clockCoverage(data).deviceLabel.toLowerCase()}. It does not report remaining drive, shift, cycle or break time. The ELD is the system of record — these readings are read-only, and each one shows how old it is.`
            : 'Duty status and remaining drive, shift and cycle time for every driver, mirrored from the connected electronic logging device. The ELD is the system of record — these readings are read-only, and each one shows how old it is.'
        }
        meta={data?.connected ? <HeaderMeta data={data} /> : undefined}
      />

      {isLoading && <CardSkeleton />}

      {isError && (
        <ErrorState
          message="Couldn't load hours of service"
          onRetry={() => void refetch()}
        />
      )}

      {/* The state this page exists to get right.  An empty table here
          would read as "nobody is near their limit"; this says the feed
          is absent and where to go. */}
      {!isLoading && !isError && data && !data.connected && (
        <EmptyState
          icon={Plug}
          title="No electronic logging device is connected"
          description="Nothing has been recorded, so this is not a statement that every driver has hours remaining. Connect an ELD to see duty status and remaining time here."
          action={
            <Link
              to="/integrations"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 min-h-tap bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary-hover transition"
            >
              Go to Integrations
            </Link>
          }
        />
      )}

      {!isLoading && !isError && data?.connected && rows.length === 0 && (
        <EmptyState
          icon={Clock}
          title="No drivers in your vehicle access"
          description="Hours of service IS connected for this account — you are seeing none because no driver is assigned to a truck you have access to."
        />
      )}

      {!isLoading && !isError && data?.connected && rows.length > 0 && (
        <>
          <DataGrid
            tableId="eld-hours"
            columns={columns}
            data={rows}
            searchKey={['driver', 'vehicle']}
            // Most drive time first.  The rows arrive newest-reading
            // first, which answers a question nobody asked; this page
            // exists for "who can take this load and for how long".
            // On a device with no drive clock that column does not
            // exist, so the rows keep their newest-first order.
            defaultSorting={hasDrive
              ? [{ id: 'drive_remaining', desc: true }]
              : []}
            // A dispatcher's own views — "my night shift", "running
            // low" — saved per user, right-click to manage.
            savedTabs
          />
          <p className="text-xs text-muted-foreground max-w-prose">
            {data.record_of}
          </p>
        </>
      )}
    </div>
  );
}

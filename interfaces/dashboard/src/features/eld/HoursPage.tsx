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
import EldConfigPanel from './config/EldConfigPanel';
import FeatureConfigGear from '../_lib/FeatureConfigGear';
import { PageHeader, CardSkeleton, EmptyState, ErrorState } from '../../components/shell';
import { Freshness, Tip } from '../../components/tooltip';
import { Badge } from '../../components/ui/badge';
import { statusTone, toneText } from '../../lib/status';
import type { AnyColumn } from '../../types';
import { CLOCK_ORDER, DUTY_LABELS, clockCoverage, clockText, countLowOnDrive,
  statusFor, useHours } from './useHours';
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

const identityColumns = (showUnlinked: boolean): AnyColumn[] => [
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
              outline weight, where duty status takes the fill.

              Marked per row only while the state is PARTIAL.  When
              nobody is linked — which is every account whose ELD has no
              roster link yet — the badge is on all of them, and a mark
              that never varies stops being a mark: it is twenty-two
              copies of one fact, in the column that carries the
              driver's name.  The header states it once instead. */}
          {!r.linked && showUnlinked && (
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
  {
    key: 'vehicle',
    label: 'Truck',
    sortable: true,
    render: (v, row) => {
      const r = row as unknown as DriverHours;
      if (!v) return <span className="text-muted-foreground">—</span>;
      // An assignment somebody made and a device's report of where the
      // tractor is are not the same fact. The roster's answer stands
      // plain; the device's is marked, because it can be right about
      // the truck and wrong about who should be driving it.
      return r.vehicle_from === 'eld' ? (
        <Tip label="Reported by the ELD, not from a truck assignment on your roster">
          <span className="tabular-nums text-muted-foreground cursor-help">
            {String(v)}
          </span>
        </Tip>
      ) : (
        <span className="tabular-nums">{String(v)}</span>
      );
    },
  },
  {
    key: 'duty_status',
    label: 'Duty status',
    sortable: true,
    render: (v, row) => {
      const r = row as unknown as DriverHours;
      const status = String(v || 'unknown');
      const held = statusFor(r.since);
      // On a device that reports no countdowns, HOW LONG is the whole
      // answer. "Sleeper berth" says almost nothing; "Sleeper berth ·
      // 6h 20m" says when they can drive again. It was stored from the
      // first commit and never rendered.
      return (
        <div className="flex items-center gap-2">
          <Badge tone={statusTone(status)}>
            {DUTY_LABELS[status] ?? status}
          </Badge>
          {held && (
            <span className="text-2xs text-muted-foreground tabular-nums">
              {held}
            </span>
          )}
        </div>
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

/**
 * Which carrier the driver works for.
 *
 * An account is several legal companies — this one runs five — and
 * hours of service is regulated per DRIVER of a named carrier. Every
 * other multi-record grid here carries this column (Loads, Scorecards,
 * Mileage, Work Orders, Inventory, Reports), and it takes the same
 * shape: the company CODE, sortable and filterable.
 *
 * Rendered only when something fills it, so a single-company account
 * does not get a column of blanks.
 */
const COMPANY_COLUMN: AnyColumn = {
  key: 'company',
  label: 'Company',
  sortable: true,
  filterable: true,
  filterMode: 'select',
  minWidth: 104,
  render: (v) => (v
    ? <span className="font-medium">{String(v)}</span>
    : <span className="text-muted-foreground">—</span>),
};

/** Identity, then the clocks that exist, then the reading's age. */
function buildColumns(
  reported: Set<HosClockId>,
  showUnlinked: boolean,
  showCompany: boolean,
): AnyColumn[] {
  const identity = identityColumns(showUnlinked);
  const clocks = CLOCK_ORDER.filter((c) => reported.has(c))
    .map((c) => CLOCK_COLUMNS[c]);
  const readingAt = identity.length - 1;
  return [
    // Company sits FIRST, before the driver's name: on a multi-carrier
    // account it is the thing that says which of five businesses this
    // row belongs to, and reading a name without knowing whose employee
    // it is answers the wrong question.
    ...(showCompany ? [COMPANY_COLUMN] : []),
    ...identity.slice(0, readingAt),
    ...clocks,
    identity[readingAt],
  ];
}

/** Nobody on this page is attached to a person on our roster.
 *
 *  Its visible consequence is a Truck column of dashes, which on its
 *  own reads as "these drivers have no truck" rather than "we have not
 *  been told which truck".  Same shape as the clock columns: the gap is
 *  in what we know, and a surface that shows the gap without naming it
 *  lets the reader fill it in wrongly. */
function allUnlinked(data: HoursResponse | undefined): boolean {
  return !!data && data.count > 0 && data.drivers.every((d) => !d.linked);
}

/**
 * Only one integration is polled for a capability.
 *
 * Two connected ELDs both offering hours of service is not an error and
 * not rare — an account can run one vendor for telematics and another
 * as its actual logging device. The resolver takes the first in catalog
 * order and the other is simply never asked, which is correct and
 * completely invisible: the fact lives in a server log line while the
 * operator looks at an empty page having just entered five keys.
 *
 * Returns the sentence that names the integration to switch off, or
 * null when there is no contention to explain.
 */
/** The connected ELDs, by name — so the waiting state can say "ORIENT
 *  ELD is connected" rather than the vaguer "an ELD is". */
function connectedNames(data: HoursResponse | undefined): string {
  const names = (data?.feed?.connected ?? []).map((p) => p.name);
  return names.length ? names.join(' and ') : 'Your electronic logging device';
}


function shadowedNote(data: HoursResponse | undefined): string | null {
  const feed = data?.feed;
  if (!feed || !feed.shadowed.length || !feed.serving_name) return null;
  const waiting = feed.shadowed.map((p) => p.name).join(' and ');
  return `${feed.serving_name} is the integration currently polled for `
    + `hours of service, so ${waiting} is connected but not in use. `
    + `Only one can serve it. To switch, turn OFF "Hours of service" on `
    + `${feed.serving_name}'s Integration card.`;
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
      {/* A table that is filling from the other integration is the
          quietest version of this problem: everything looks fine and
          the device the operator just wired is doing nothing. */}
      {shadowedNote(data) && (
        <span className={toneText('warn')}>{shadowedNote(data)}</span>
      )}

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

      {/* Said once here instead of on every row — see the Driver
          column for why.  Names the empty Truck column before the
          reader concludes these drivers have no truck. */}
      {allUnlinked(data) && (
        <span className="text-muted-foreground">
          {data.drivers.some((d) => d.vehicle_from === 'eld')
            // The device fills the Truck column now, so the sentence
            // must stop claiming it is empty — and must say what is
            // actually still missing: these drivers are not people on
            // your roster yet, which is what their HOS tab, their
            // assignments and your vehicle access all hang off.
            ? <>No driver here is linked to a member of your team yet — trucks
               are as the ELD reports them, not from your assignments. Link
               them on each driver&apos;s Integrations tab.</>
            : <>No driver here is linked to a member of your team yet, so the
               Truck column is empty. Link them on each driver&apos;s
               Integrations tab.</>}
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
    () => buildColumns(
      clockCoverage(data).reported,
      !allUnlinked(data),
      (data?.companies?.length ?? 0) > 1,
    ),
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
        // The config family's one door, in the slot it occupies on
        // every other feature. It self-gates on can_manage_config_all
        // and renders nothing for everyone else — this page is read by
        // three departments, and most of them may not change an
        // account-wide value.
        actions={
          <FeatureConfigGear feature="Hours of Service">
            <EldConfigPanel deviceCount={data?.feed?.connected?.length ?? 0} />
          </FeatureConfigGear>
        }
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
          is absent and where to go.

          It must ALSO not be shown to an account that has just
          connected one. "No electronic logging device is connected" was
          being rendered to an operator looking at five green keys on
          the Integrations page, because `connected` meant "a reading
          has arrived" rather than "a device is wired". Telling somebody
          their device is absent while they are looking at it is the
          same failure this page exists to prevent, pointed the other
          way. */}
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

      {/* Connected, and nothing has arrived yet.
          Two different reasons land here and they need different
          sentences: the feed is simply young, or another integration is
          the one being polled and this one will never fill on its own.
          The second is the one an operator cannot work out alone. */}
      {!isLoading && !isError && data?.awaiting_first_reading && (
        <EmptyState
          icon={Clock}
          title={shadowedNote(data)
            ? 'Connected, but another integration is serving hours of service'
            : `${connectedNames(data)} is connected — waiting for the first reading`}
          description={shadowedNote(data) ?? (
            'Duty status is polled every 5 minutes. If nothing appears '
            + 'after that, check on Integrations that each company has '
            + 'its own key and that its last test passed.'
          )}
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

      {!isLoading && !isError && data?.connected
        && !data.awaiting_first_reading && rows.length === 0 && (
        <EmptyState
          icon={Clock}
          title="No drivers in your vehicle access"
          description="Hours of service IS connected for this account — you are seeing none because no driver is assigned to a truck you have access to."
        />
      )}

      {!isLoading && !isError && data?.connected
        && !data.awaiting_first_reading && rows.length > 0 && (
        <>
          <DataGrid
            tableId="eld-hours"
            columns={columns}
            data={rows}
            searchKey={['driver', 'vehicle', 'company']}
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

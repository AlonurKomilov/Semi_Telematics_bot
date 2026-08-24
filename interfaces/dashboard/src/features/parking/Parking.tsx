import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { ParkingSquare, CheckCircle2 } from 'lucide-react';

import DataGrid from '../../components/datagrid';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton, LastUpdated,
} from '../../components/shell';
import { useTimezone } from '../../hooks/useTimezone';
import { usePermissions } from '../../hooks/usePermissions';
import { formatDate } from '../../utils/datetime';

import { listActiveParking, resolveParkingEvent, type ParkingEvent } from './api';
import { makeParkingColumns } from './columns';
import { parseAiAnalysis } from './aiAnalysis';
import { parkingRowMenu } from './contextMenu';
import ParkingDetailSheet from './ParkingDetailSheet';

/**
 * Parking — one row per VEHICLE, current state.
 *
 * This page answers "where is every truck parked right now, and is that
 * OK?".  It does not list past stops.  An earlier revision put active and
 * resolved events in one grid with a Status column, which looked tidy and
 * read badly: ``upsert_parking_event`` keeps one unresolved row per
 * vehicle, so active rows are already a vehicle list, and folding history
 * in meant one identity column addressed two different kinds of thing —
 * sorting by Vehicle interleaved a truck's current state with its own
 * past.  It was also a weaker copy of a view that already exists.
 *
 * The three questions now have three homes:
 *   this page   — which vehicles are parked, and badly?      (per vehicle)
 *   Alerts      — what parking events fired, when, how many? (per event)
 *   the drawer  — does THIS truck park badly repeatedly?     (per vehicle,
 *                 over time)
 *
 * Segment order is All → Needs attention: it matches the Loads grid, where
 * "All rows" leads.  Landing unfiltered and narrowing is the convention.
 */
export default function Parking() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const qc = useQueryClient();
  const { has } = usePermissions();

  const [detail, setDetail] = useState<ParkingEvent | null>(null);
  const [confirming, setConfirming] = useState<ParkingEvent | null>(null);
  const [error, setError] = useState('');

  const canResolve = has('can_parking_all') || has('can_parking_vehicle');

  const { data, isLoading, isFetching, error: queryError, refetch, dataUpdatedAt } =
    useQuery({
      queryKey: ['parking', 'active'],
      // Unresolved only, safe locations included.  This IS the vehicle
      // list: one unresolved row per vehicle is the upsert's invariant.
      queryFn: () => listActiveParking({ attentionOnly: false }),
      // Keeps the previous page mounted while refetching.  Without it the
      // DataGrid unmounts on every poll and the operator loses scroll
      // position, selection, and whichever saved tab they were on.
      placeholderData: keepPreviousData,
    });

  // ``ai_confidence`` is derived at the page boundary rather than
  // server-side: the value already rides inside ai_analysis, and parsing
  // it once here keeps the column, its filter, and the detail panel all
  // reading the SAME string instead of three parses that can disagree.
  const rows = useMemo(
    () => (data?.events ?? []).map((e) => ({
      ...e,
      ai_confidence: parseAiAnalysis(e.ai_analysis ?? '').confidence,
    })),
    [data],
  );

  const columns = useMemo(() => makeParkingColumns(tz), [tz]);

  async function doResolve(events: ParkingEvent[]) {
    setError('');
    try {
      // Sequential on purpose: the API answers 400 for an already-resolved
      // event, and firing a whole selection in parallel would turn one
      // stale row into an unattributable failure.
      for (const ev of events) await resolveParkingEvent(ev.id);
      qc.invalidateQueries({ queryKey: ['parking'] });
      setDetail(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Resolve failed');
    }
  }

  const displayError = error || (queryError instanceof Error ? queryError.message : '');

  return (
    <div>
      {/* The description deliberately does NOT say "every vehicle currently
          parked" — that was false.  A stop is only recorded when it is
          unsafe or unverified; trucks at a truck stop, in a yard, or inside
          a geofence are checked, resolved, and never stored.  Claiming full
          coverage made an empty page read as "no truck is parked" when the
          truth is "no truck is parked BADLY". */}
      <PageHeader
        icon={ParkingSquare}
        title={t('pages.parking_title')}
        description="Vehicles parked somewhere unsafe or unverified. Safe stops — truck stops, yards, your geofences — are checked and not listed. Open a row for the AI's reasoning and that vehicle's parking history; past events across the account live on the Alerts page."
        actions={
          <LastUpdated fetchedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={refetch} />
        }
      />

      {displayError && rows.length === 0 ? (
        <ErrorState
          title="Couldn't load parking events"
          message={displayError}
          onRetry={() => refetch()}
        />
      ) : isLoading && rows.length === 0 ? (
        // cols matches makeParkingColumns' length: a narrower skeleton
        // reflows the table sideways the moment real data lands.
        <TableSkeleton rows={8} cols={10} />
      ) : rows.length === 0 ? (
        // Empty here is GOOD NEWS, and the old copy said the opposite:
        // "No vehicles currently parked" reads as a data gap, when the
        // commonest cause is every parked truck being somewhere safe —
        // those stops are resolved and never stored.
        <EmptyState
          icon={ParkingSquare}
          title="No vehicles parked unsafely"
          description="Every parked truck is at a truck stop, yard, or inside one of your geofences — or they're all moving. Unsafe and unverified stops appear here within minutes of the next parking check."
        />
      ) : (
        <DataGrid
          // ``displayError`` reaches the DOM through exactly one branch
          // above — gated on ``rows.length === 0``.  That fixed nothing
          // for the two failures that actually happen here:
          //   * a failed REFETCH, which leaves the previous rows up, so
          //     the operator reads a stale board as current;
          //   * a failed BULK RESOLVE, which happens on a populated page
          //     by definition.  Select eight events, hit resolve, the
          //     third returns 400 (the case the loop's own comment
          //     anticipates), the loop aborts — and absolutely nothing
          //     appeared on screen.  Five stayed unresolved with no way
          //     to know which.  There is no toast on this page either;
          //     the error state existed, was assigned, and was never
          //     rendered.
          error={displayError || undefined}
          onRetry={() => refetch()}
          tableId="parking"
          columns={columns}
          data={rows as unknown as Record<string, unknown>[]}
          savedTabs
          searchKey={['vehicle_name', 'address']}
          searchPlaceholder="Search vehicle or address…"
          segments={[
            { key: 'all', label: 'All', showCount: false },
            {
              key: 'attention',
              label: 'Needs attention',
              tone: 'danger',
              // Urgency, NOT location_class.  ``parking_events`` is an
              // exceptions table: features/parking/check.py returns early
              // for geofence stops, safe-keyword stops, and AI-confirmed
              // safe stops, so a row only EXISTS when it is unsafe or
              // unverified.  A location_class predicate therefore matched
              // 100% of rows and this tab was a byte-identical copy of
              // "All" — two tabs implying a distinction the data cannot
              // express.  ``alert_level`` is the axis that actually
              // partitions (28 of 38 live rows), and it is the same
              // verdict that decides whether the bot pages anyone, so the
              // page and the alert now agree on what "attention" means.
              match: (row) => String(row.alert_level ?? 'none') !== 'none',
            },
          ]}
          onRowClick={(row) => setDetail(row as unknown as ParkingEvent)}
          rowActions={(row) => parkingRowMenu(row as unknown as ParkingEvent, {
            canResolve,
            openDetail: (e) => setDetail(e),
            confirmResolve: (e) => setConfirming(e),
          })}
          bulkSelection={canResolve}
          bulkActions={canResolve ? [{
            label: 'Resolve',
            icon: CheckCircle2,
            // DataGrid's own confirm rather than a second hand-rolled
            // dialog — same reason the grid owns selection and filters.
            confirm: (n) => `Resolve ${n} event${n !== 1 ? 's' : ''}? `
              + 'This closes their parking alerts and clears any pending '
              + 'acknowledgements for those vehicles. It cannot be undone.',
            onRun: (selected) => {
              // Unresolved only: resolving a resolved row is a 400, and a
              // count that includes them would be a lie in the prompt.
              const evs = (selected as unknown as ParkingEvent[]).filter((e) => !e.resolved);
              if (evs.length) return doResolve(evs);
            },
          }] : undefined}
        />
      )}

      {/* No standalone count line here.  DataGrid already prints one in its
          pagination footer, and this one sat 12px below it computed on a
          DIFFERENT denominator — `rows.length` is pre-segment, so on "Needs
          attention" the grid read 28 and this read 38: two adjacent numbers
          about one list, disagreeing.  The grid owns counts; the segment tab
          carries its own badge; "flagged" is stated in the page description. */}

      <ParkingDetailSheet
        event={detail}
        onClose={() => setDetail(null)}
        canResolve={canResolve}
        onResolve={(e) => setConfirming(e)}
      />

      {/* Single-row resolve.  Cannot be undone (there is no reopen
          endpoint) and it clears the vehicle's pending acknowledgements,
          so the consequence is named before it runs.  The bulk path uses
          DataGrid's own confirm above. */}
      <Dialog open={!!confirming} onOpenChange={(o) => { if (!o) setConfirming(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Resolve parking event?</DialogTitle>
            {/* Names the EVENT, not just the vehicle.  The detail sheet
                repoints to a past stop when a history row is clicked and
                Resolve acts on whatever is shown — so a vehicle-only prompt
                was identical whether you were closing the current stop or
                one from three months ago. */}
            <DialogDescription>
              {confirming && (
                <>
                  Closes parking event{' '}
                  <span className="font-mono text-foreground">#{confirming.id}</span>
                  {confirming.first_stopped
                    ? ` (${formatDate(confirming.first_stopped, { timeZone: tz })})`
                    : ''}
                  {' '}for{' '}
                  <span className="font-medium text-foreground">{confirming.vehicle_name}</span>
                  {confirming.company_code ? ` (${confirming.company_code})` : ''} and clears any
                  pending acknowledgements for it. This cannot be undone.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              onClick={() => setConfirming(null)}
              className="px-3 py-1.5 border border-border bg-background hover:bg-muted rounded-lg text-sm font-medium text-foreground transition"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                const ev = confirming;
                setConfirming(null);
                if (ev) doResolve([ev]);
              }}
              className="px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-lg text-sm font-medium text-primary-foreground transition"
            >
              Resolve
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

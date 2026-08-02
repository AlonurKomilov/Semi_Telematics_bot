/**
 * Parking grid columns — ONE ROW PER VEHICLE, current state only.
 *
 * The grid answers "where is every truck parked right now, and is that
 * OK?".  It deliberately does NOT list past stops: ``upsert_parking_event``
 * keeps one unresolved row per vehicle, so active events already ARE a
 * vehicle list — mixing resolved rows in gave the table two different
 * kinds of thing under one identity column, and sorting by Vehicle
 * interleaved a truck's current state with its own history.
 *
 * The per-event, cross-vehicle, historical view is the ALERTS page:
 * ``parking_check`` is a registered alert source, and Alerts already
 * carries parking in its type filter, family labels, severity filter and
 * time window.  A vehicle's own history lives one click away in the
 * detail drawer.  Three questions, three homes, no duplication.
 */
import type { AnyColumn } from '../../types';
import { formatDate } from '../../utils/datetime';
import { ClassBadge, AlertBadge, ConfidenceBadge } from './badges';

/** Hours → "45m" / "3.2h" / "2d 17h".
 *
 *  Rounds to whole hours BEFORE splitting into days.  Rounding the
 *  remainder instead let it reach 24: 71.9h rendered as "2d 24h" rather
 *  than "3d 0h", which showed up on nearly every row of a vehicle's
 *  history because long stops cluster just under a day boundary. */
export function formatDuration(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  const total = Math.round(hours);
  return `${Math.floor(total / 24)}d ${total % 24}h`;
}

export function mapsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps?q=${lat},${lng}`;
}

/** Amber past 2h, red past 8h — the same thresholds the InfoTip explains.
 *  Exported so the detail panel can colour its duration identically. */
export function durationToneClass(hours: number): string {
  return hours >= 8 ? 'text-danger font-medium' : hours >= 2 ? 'text-warn' : '';
}

const str = (row: Record<string, unknown>, k: string) => String(row[k] ?? '');

export function makeParkingColumns(tz: string): AnyColumn[] {
  return [
    // Hidden by default via the column controls if it's noise, but present
    // so a row can be quoted ("parking event #4450") when comparing against
    // Alerts or a support thread.
    // Rendered `#1234` in font-mono to match how the detail sheet and its
    // history rows print the same identifier.  design.md reserves font-mono
    // for machine identifiers and requires one column look the same
    // everywhere it appears — an operator comparing a grid row against the
    // sheet is matching the two by eye.
    {
      key: 'id',
      label: 'Event ID',
      sortable: true,
      render: (v) => (
        <span className="font-mono text-muted-foreground">#{String(v ?? '')}</span>
      ),
    },
    { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
    { key: 'company_code', label: 'Company', sortable: true, filterable: true },
    {
      key: 'location_class',
      label: 'Location',
      sortable: true,
      filterable: true,
      filterValue: (row) => str(row, 'location_class'),
      filterLabel: (row) => str(row, 'location_class').toUpperCase() || '(none)',
      render: (v) => <ClassBadge cls={v as string} />,
    },
    {
      key: 'alert_level',
      label: 'Severity',
      sortable: true,
      filterable: true,
      filterValue: (row) => str(row, 'alert_level'),
      filterLabel: (row) => str(row, 'alert_level').toUpperCase() || '(none)',
      render: (v) => <AlertBadge level={v as string} />,
    },
    {
      // Derived on the client from ai_analysis (see Parking.tsx, where
      // rows are decorated before they reach the grid).  A column, not a
      // detail-only field: the verdict's confidence belongs beside the
      // verdict, otherwise the badge asserts a classification while how
      // sure we are stays hidden.
      key: 'ai_confidence',
      label: 'AI confidence',
      sortable: true,
      filterable: true,
      filterValue: (row) => str(row, 'ai_confidence'),
      filterLabel: (row) => str(row, 'ai_confidence').toUpperCase() || '(none)',
      render: (v) => (v ? <ConfidenceBadge value={v as string} /> : <span className="text-muted-foreground">—</span>),
    },
    {
      // FLAGGED stops only — unsafe plus unverified, the same predicate
      // as the "Needs attention" segment.  Not a total: a truck that
      // stopped 120 times in truck stops is doing its job, and counting
      // those the same as 120 shoulder stops sorts the wrong vehicles to
      // the top.  The point of the column is spotting repeat offenders
      // ACROSS the fleet — the drawer already answers "does this truck
      // park badly repeatedly?" one vehicle at a time, but until now you
      // had to suspect a truck before you could check it.
      key: 'flagged_count',
      label: 'Flagged stops',
      sortable: true,
      aggregable: true,
      filterable: true, filterMode: 'range', filterRange: { min: 0, step: 1 },
      render: (v) => <span className="tabular-nums">{Number(v ?? 0)}</span>,
    },
    { key: 'address', label: 'Address' },
    {
      key: 'duration_hours',
      label: 'Duration',
      sortable: true,
      aggregable: true,
      filterable: true, filterMode: 'range', filterRange: { min: 0, step: 1, unit: 'h' },
      render: (v) => {
        const h = Number(v ?? 0);
        return <span className={durationToneClass(h)}>{formatDuration(h)}</span>;
      },
    },
    {
      key: 'first_stopped',
      label: 'Stopped at',
      sortable: true,
      filterable: true, filterMode: 'date-range',
      render: (v) => (v ? formatDate(v as string, { timeZone: tz }) : '—'),
    },
  ];
}

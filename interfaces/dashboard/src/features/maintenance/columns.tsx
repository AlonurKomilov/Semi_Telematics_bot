// ── The maintenance grid's columns ──────────────────────────────────
//
// Lifted out of Tasks.tsx (2,310 lines) — the precedent is
// ``features/parking/columns.tsx``, and datagrid/CLAUDE.md names column
// config as the split candidate when a page file stops being readable.
//
// Two pieces, because the grid needs two:
//
//   ``baseColumns``  the plain shape — keys, labels, what is sortable
//                    and filterable.  Data, no behaviour.
//   ``makeColumns``  the same list with the STATUS and PRIORITY renders
//                    overridden, so a row whose due date / odometer /
//                    engine hours has passed shows that urgency instead
//                    of its stale stored label.
//
// The override is why this is a FACTORY and not a constant: it closes
// over three things only the page knows — the urgency classifier, the
// custom-type label map, and the account timezone.  Passing them in
// keeps the file pure and keeps the memo's dependency list honest at the
// call site.
//
// ⚠️ SEGMENTS deliberately did NOT come with it.  datagrid/CLAUDE.md:
// column config is the usual candidate, never the segments — six lines
// read better beside the grid they scope.

// Base column set, excluding the bulk-select checkbox.  The component
// composes the final columns array by prepending a selection column
// whose render closes over the page-level ``selectedIds`` state.

import type { AnyColumn, MaintenanceTask } from '../../types';
import StatusBadge from '../../components/StatusBadge';
import { formatDate } from '../../utils/datetime';
import {
  PriorityBadge, EngineHoursProgress, TaskTypeCell, DueDateChip,
  MileageProgress, type Priority,
} from './badges';

const baseColumns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
  // Company column lets operators tell trucks apart when two
  // companies under the same account each have a "103" or "101".
  // Mirrors the Vehicles list column for visual parity.  Renders the
  // raw company_code (e.g. G1 / OSY / PTG) — short, scannable.
  { key: 'company_code', label: 'Company', sortable: true, filterable: true,
    render: (v) => {
      const s = String(v || '').trim();
      return s
        ? <span className="text-xs">{s}</span>
        : <span className="text-muted-foreground text-xs">—</span>;
    } },
  // Priority badge — first column after vehicle so it carries the most
  // visual weight.  ``sortKey`` ranks critical→high→medium→low so the
  // sort matches operator expectations (alphabetical would put
  // critical AFTER low, which is wrong).  Filter matches against
  // the raw priority string so "high" narrows to High rows.
  { key: 'priority', label: 'Priority', sortable: true,
    filterable: true,
    filterValue: (row) => String((row as MaintenanceTask).priority ?? ''),
    // Title-case the priority code for display (low → Low,
    // critical → Critical) so the dropdown reads cleanly.
    filterLabel: (row) => {
      const p = String((row as MaintenanceTask).priority ?? '').toLowerCase();
      return p ? p.charAt(0).toUpperCase() + p.slice(1) : '(none)';
    },
    sortKey: (row) => {
      const rank: Record<string, number> = {
        critical: 0, high: 1, medium: 2, low: 3,
      };
      const r = row as MaintenanceTask;
      return rank[(r.priority || 'medium').toLowerCase()] ?? 99;
    },
    render: (v) => <PriorityBadge value={v} /> },
  { key: 'task_type', label: 'Service task', sortable: true, filterable: true,
    filterValue: (row) => String((row as MaintenanceTask).task_type ?? ''),
    // De-slug fallback only — the component overrides this with the
    // SSOT lookup (module scope can't reach the service-tasks query).
    filterLabel: (row) => {
      const code = String((row as MaintenanceTask).task_type ?? '');
      return code
        ? code.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
        : '(none)';
    },
    render: (v) => <TaskTypeCell type={String(v || 'custom')} /> },
  { key: 'description', label: 'Description', render: (v) => {
    const s = String(v || '');
    return s.length > 60 ? <span title={s}>{s.slice(0, 60)}…</span> : s;
  }},
  { key: 'due_date', label: 'Due Date', sortable: true,
    // Date-range filter — From / To date pickers.  Bounds auto-
    // compute from the loaded task set; "To" is inclusive to end-of-
    // day so a single-day filter keeps the whole day.
    filterable: true,
    filterMode: 'date-range',
    render: (v, row) => {
      const r = row as MaintenanceTask;
      return <DueDateChip value={v} status={r.status} recurDays={r.recur_interval_days} />;
    } },
  // Combined "Mileage Progress" replaces the bare "Due Miles" cell so the
  // operator can see how close each truck is to the next service without
  // doing the arithmetic in their head.
  //
  // Sortable by URGENCY, not by raw due_miles.  ``due_miles -
  // last_odometer`` is the canonical "miles to go": negative means
  // overdue (most-negative = most-overdue), small positive means due
  // soon, large positive means far from due.  ASC click puts the most
  // urgent truck at the top — what the operator wants when scanning
  // "what do I need to service this week?".  Rows with no due_miles
  // threshold sink to the bottom via +Infinity.
  { key: 'due_miles', label: 'Mileage', sortable: true,
    sortKey: (row) => {
      const r = row as MaintenanceTask;
      if (r.due_miles == null) return Number.POSITIVE_INFINITY;
      const last = r.last_odometer ?? 0;
      return Number(r.due_miles) - Number(last);
    },
    render: (_v, row) => <MileageProgress row={row as MaintenanceTask} /> },
  // Engine-hours parallel to mileage.  Same urgency-based sort:
  // ``due_engine_hours - last_engine_hours`` ascending, with rows
  // missing the threshold sinking to the bottom.
  { key: 'due_engine_hours', label: 'Engine Hours', sortable: true,
    sortKey: (row) => {
      const r = row as MaintenanceTask;
      if (r.due_engine_hours == null) return Number.POSITIVE_INFINITY;
      const last = r.last_engine_hours ?? 0;
      return Number(r.due_engine_hours) - Number(last);
    },
    render: (_v, row) => <EngineHoursProgress row={row as MaintenanceTask} /> },
  { key: 'status', label: 'Status', sortable: true,
    filterable: true,
    filterValue: (row) => String((row as MaintenanceTask).status ?? ''),
    // Status codes are short snake_case strings; title-case for
    // display so "due_soon" reads as "Due Soon" in the dropdown.
    filterLabel: (row) => {
      const s = String((row as MaintenanceTask).status ?? '');
      return s
        ? s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
        : '(none)';
    },
    render: (v) => <StatusBadge status={String(v)} /> },
  // Updated column shows date + time so multiple same-day edits are
  // distinguishable.  Short locale format keeps it readable without
  // dominating the row width.
  { key: 'updated_at', label: 'Updated', sortable: true,
    filterable: true,
    filterMode: 'date-range',
    render: (v) => v
      ? new Date(String(v)).toLocaleString(undefined, {
          month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        })
      : '—' },
];

export interface ColumnDeps {
  /** Buckets a task as overdue / dueSoon / neither — the SAME classifier
   *  the chip counts use, so a badge can never disagree with a count. */
  dueSoonClassify: (t: MaintenanceTask) => 'due_soon' | 'overdue' | null;
  /** Account-defined task types, code → label. */
  customTypeLabelByValue: Record<string, string>;
  /** Account timezone; dates are rendered in it, never in UTC. */
  tz: string;
}

export function makeColumns({
  dueSoonClassify, customTypeLabelByValue, tz,
}: ColumnDeps): AnyColumn[] {
  // Override the Status column's render so an open row whose
  // due_date / due_miles / due_engine_hours puts it in the dueSoon
  // or overdue bucket displays that urgency in the badge instead of
  // the stale stored label.  Keeps the per-row badge consistent with
  // the chip counts — previously a task 25 mi past its due odometer
  // still showed "pending" while the Overdue chip counted it.
  // Backend ``status`` column is untouched; only the display flips
  // based on the same urgency check the buckets use.
  //
  // Priority escalates the same way, and to the SAME target the
  // backend uses: the scheduled overdue-marker jobs
  // (mark_overdue_tasks_by_mileage / …_engine_hours / date) persist
  // ``priority='critical'`` whenever they flip a task to overdue
  // (see adapters/storage/maintenance.py update_maintenance_status_bulk).
  // We derive the SAME Critical here so the instant display and the
  // DB (which catches up on the next 6h tick) never disagree — an
  // earlier version raised to High, which made one unchanged task
  // read Medium → High → Critical as the scheduler caught up.  The
  // tooltip preserves the stored value so nothing is hidden.
  const effectivePriority = (t: MaintenanceTask): string => {
    const stored = String(t.priority || 'medium').toLowerCase();
    const status = String(t.status || '').toLowerCase();
    if (status === 'completed' || status === 'cancelled') return stored;
    if (dueSoonClassify(t) === 'overdue' && stored !== 'critical') {
      return 'critical';
    }
    return stored;
  };
  const PRIORITY_RANK: Record<string, number> = {
    critical: 0, high: 1, medium: 2, low: 3,
  };
  // Same derivation the badge render uses — exported to the Status
  // column's filterValue/filterLabel so the 3-dot filter offers
  // "Overdue" / "Due Soon" options that match what the rows SHOW.
  // Without this the filter matched the raw stored status, and with
  // the urgency chips gone (hero strip took over the counts) there
  // would be no way left to slice by urgency.
  const effectiveStatus = (t: MaintenanceTask): string => {
    const stored = String(t.status ?? '').toLowerCase();
    const derived = (stored === 'pending' || stored === 'due_soon')
      ? dueSoonClassify(t)
      : null;
    if (derived === 'overdue') return 'overdue';
    if (derived === 'due_soon' && stored === 'pending') return 'due_soon';
    return stored;
  };
  const enrichedBase = baseColumns.map(col => {
    if (col.key === 'status') {
      return {
        ...col,
        filterValue: (row: Record<string, unknown>) =>
          effectiveStatus(row as unknown as MaintenanceTask),
        filterLabel: (row: Record<string, unknown>) => {
          const s = effectiveStatus(row as unknown as MaintenanceTask);
          return s
            ? s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
            : '(none)';
        },
        render: (v: unknown, row: Record<string, unknown>) => {
          const t = row as unknown as MaintenanceTask;
          const stored = String(v);
          const derived = (stored === 'pending' || stored === 'due_soon')
            ? dueSoonClassify(t)
            : null;
          if (derived === 'overdue') {
            return (
              <span title={`Stored as "${stored}" — overdue by its due date / mileage / engine hours`}>
                <StatusBadge status="overdue" />
              </span>
            );
          }
          if (derived === 'due_soon' && stored === 'pending') {
            return <StatusBadge status="due_soon" />;
          }
          return <StatusBadge status={stored} />;
        },
      };
    }
    if (col.key === 'priority') {
      return {
        ...col,
        filterValue: (row: Record<string, unknown>) =>
          effectivePriority(row as unknown as MaintenanceTask),
        filterLabel: (row: Record<string, unknown>) => {
          const p = effectivePriority(row as unknown as MaintenanceTask);
          return p ? p.charAt(0).toUpperCase() + p.slice(1) : '(none)';
        },
        sortKey: (row: Record<string, unknown>) =>
          PRIORITY_RANK[effectivePriority(row as unknown as MaintenanceTask)] ?? 99,
        render: (v: unknown, row: Record<string, unknown>) => {
          const t = row as unknown as MaintenanceTask;
          const eff = effectivePriority(t);
          const stored = String(t.priority || 'medium').toLowerCase();
          if (eff !== stored) {
            return (
              <span title={`Priority "${stored}" auto-raised to Critical — task is overdue`}>
                <PriorityBadge value={eff} />
              </span>
            );
          }
          return <PriorityBadge value={v} />;
        },
      };
    }
    if (col.key === 'task_type') {
      // Override the static render AND filter label so every value —
      // standard or custom — resolves through the account's own task
      // list instead of the kebab-cased value.
      return {
        ...col,
        filterLabel: (row: Record<string, unknown>) => {
          const code = String((row as unknown as MaintenanceTask).task_type ?? '');
          if (!code) return '(none)';
          return customTypeLabelByValue[code]
            ?? code.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
        },
        render: (v: unknown) => {
          const type = String(v || 'inspection');
          return (
            <TaskTypeCell
              type={type}
              customLabel={customTypeLabelByValue[type]}
            />
          );
        },
      };
    }
    if (col.key === 'updated_at') {
      // Override the static render so the Updated timestamp formats
      // in the account-effective timezone (the module-level
      // ``baseColumns`` definition can't reach the ``useTimezone``
      // hook).  Same short date+time shape as before.
      return {
        ...col,
        render: (v: unknown) => v
          ? formatDate(String(v), {
              timeZone: tz,
              intl: { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' },
            })
          : '—',
      };
    }
    return col;
  });
  return enrichedBase;
}

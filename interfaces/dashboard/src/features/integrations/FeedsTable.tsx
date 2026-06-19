/**
 * Shared "Synced data" renderer for every integration.
 *
 * Both the Samsara (telematics) and Datatruck (TMS) cards build a
 * normalized ``FeedRow[]`` and hand it here, so the two read as one
 * design family.  Provider differences ride inside the row data:
 *   - ``cadence``  — scheduled feeds (Samsara) show "every 5 min".
 *   - ``badge``    — live run progress / failed state (Datatruck).
 *   - ``action``   — a per-row button ("Sync now" / "Refresh").
 *   - ``note``     — a secondary warning line under the row.
 *
 * This component is purely presentational; all fetching, polling,
 * sync/preview logic lives in the per-provider adapter that builds the
 * rows.
 */

import { Fragment, type ReactNode } from 'react';
import { Database, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';

export type FeedTone = 'info' | 'danger' | 'warn';

export interface FeedRow {
  key: string;
  label: string;
  /** Primary freshness text ("347 records · 2 min ago" / "nothing synced yet"). */
  freshness: string;
  enabled: boolean;
  /** Right-aligned cadence for scheduled feeds ("every 5 min"). */
  cadence?: string;
  /** Product feature this row serves — rows are grouped under a small
   *  feature sub-header so the card reads "by feature" (Vehicles, Safety,
   *  Drivers, Work Orders…).  Empty → ungrouped (rendered flat). */
  feature?: string;
  /** Enable/disable checkbox at the row start — folds the old separate
   *  capability checklist into the feed row (one place per data type). */
  toggle?: { enabled: boolean; onChange: (enabled: boolean) => void } | null;
  /** Status chip between freshness and action (live progress / failed). */
  badge?: { text: string; tone: FeedTone; title?: string } | null;
  /** Secondary warning line under the row. */
  note?: { text: string; title?: string } | null;
  /** Right-side action button. */
  action?: {
    label: string;
    onClick: () => void;
    busy?: boolean;
    disabled?: boolean;
    title?: string;
  } | null;
}

export default function FeedsTable({
  rows,
  loading,
  error,
  emptyText = 'No data feeds.',
  footer,
}: {
  rows: FeedRow[];
  loading?: boolean;
  error?: boolean;
  emptyText?: string;
  /** Rendered below the list, inside the container (e.g. a feedback line). */
  footer?: ReactNode;
}) {
  // Group rows under their feature, preserving first-appearance order.
  // When no row carries a feature, render flat (no sub-headers).
  const groupOrder: string[] = [];
  const byFeature = new Map<string, FeedRow[]>();
  for (const r of rows) {
    const k = r.feature || '';
    if (!byFeature.has(k)) {
      byFeature.set(k, []);
      groupOrder.push(k);
    }
    byFeature.get(k)!.push(r);
  }
  const showHeaders = groupOrder.some((g) => g !== '');

  const renderRow = (r: FeedRow) => (
    <li
      key={r.key}
      className={`px-3 py-2 text-sm ${r.enabled ? '' : 'opacity-60'}`}
    >
      <div className="flex items-center gap-3">
        {r.toggle && (
          <input
            type="checkbox"
            checked={r.toggle.enabled}
            onChange={(e) => r.toggle!.onChange(e.target.checked)}
            className="size-4 shrink-0 accent-primary"
            title="Enable or disable syncing this data"
          />
        )}
        <span
          className={`flex-1 min-w-0 truncate ${
            r.enabled ? 'text-foreground' : 'text-muted-foreground line-through'
          }`}
        >
          {r.label}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground">{r.freshness}</span>
        {r.badge && (
          <span
            className={`${toneClasses(r.badge.tone)} shrink-0 max-w-44 truncate rounded px-1.5 py-0.5 text-2xs`}
            title={r.badge.title}
          >
            {r.badge.text}
          </span>
        )}
        {r.cadence && (
          <span className="shrink-0 w-20 text-right text-2xs text-muted-foreground">
            {r.cadence}
          </span>
        )}
        {r.action && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={r.action.onClick}
            disabled={r.action.disabled}
            title={r.action.title}
          >
            {r.action.busy ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RefreshCw size={12} />
            )}
            {r.action.label}
          </Button>
        )}
      </div>
      {r.note && (
        <p
          className={`${toneClasses('warn')} mt-1.5 rounded px-2 py-1 text-2xs`}
          title={r.note.title}
        >
          {r.note.text}
        </p>
      )}
    </li>
  );

  return (
    <div className="mt-4 border border-border rounded-lg overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 bg-muted/40 border-b border-border">
        <Database size={14} className="text-muted-foreground" />
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Synced data
        </span>
      </div>
      {loading ? (
        <p className="flex items-center gap-1.5 px-3 py-3 text-sm text-muted-foreground">
          <Loader2 size={12} className="animate-spin" />
          Loading…
        </p>
      ) : error ? (
        <p className="px-3 py-3 text-sm text-danger">Couldn't load synced data.</p>
      ) : rows.length === 0 ? (
        <p className="px-3 py-3 text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ul className="divide-y divide-border">
          {groupOrder.map((g) => (
            <Fragment key={g || '_ungrouped'}>
              {showHeaders && g && (
                <li className="bg-muted/20 px-3 py-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                  {g}
                </li>
              )}
              {byFeature.get(g)!.map(renderRow)}
            </Fragment>
          ))}
        </ul>
      )}
      {footer}
    </div>
  );
}

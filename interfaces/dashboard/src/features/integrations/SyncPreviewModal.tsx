/**
 * Sync preview (plan → apply) modal.
 *
 * Shown when the operator clicks "Sync now" on a resource that MERGES
 * into a user-owned SSOT (trucks/trailers → vehicle registry, work
 * orders → work-orders module).  It renders the dry-run diff returned by
 * the preview endpoint — what would be added, enriched, or needs review
 * — and gates the write behind an explicit Accept.  Nothing is written
 * until the operator approves; Decline discards the cached preview.
 */

import { Loader2, Plus, ArrowUpCircle, AlertTriangle, RefreshCw, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import type { DatatruckPreviewStatus } from './types';

interface Props {
  resource: string;
  label: string;
  preview: DatatruckPreviewStatus;
  applying: boolean;
  onAccept: () => void;
  onClose: () => void;
}

function Stat({ tone, icon, n, text }: {
  tone: Parameters<typeof toneClasses>[0];
  icon: React.ReactNode; n: number; text: string;
}) {
  return (
    <div className={`${toneClasses(tone)} flex items-center gap-2 rounded-md border px-3 py-2`}>
      {icon}
      <span className="text-lg font-semibold tabular-nums">{n}</span>
      <span className="text-xs">{text}</span>
    </div>
  );
}

export default function SyncPreviewModal({
  resource: _resource, label, preview, applying, onAccept, onClose,
}: Props) {
  const diff = preview.diff;
  if (!diff) return null;  // only rendered once the preview is ready
  // "Actionable" = rows that will actually change something.  A re-sync
  // re-matches every existing row (counts.update), but most carry no field
  // changes — those aren't actionable and shouldn't gate Accept or clutter
  // the list.
  const actionable = diff.kind === 'work_orders'
    ? diff.counts.new + (diff.counts.changed ?? 0)
    : diff.counts.new + diff.counts.enrich + diff.counts.review;
  const nothingToDo = actionable === 0;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center pt-[10vh] px-4 bg-black/40 backdrop-blur-sm"
      onClick={applying ? undefined : onClose}
    >
      <div
        className="w-full max-w-2xl bg-card border border-border rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[80vh]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div>
            <h2 className="text-base font-semibold">Review {label} sync</h2>
            <p className="text-2xs text-muted-foreground">
              {(preview.fetched ?? 0).toLocaleString()} fetched from Datatruck ·
              nothing is written until you accept
            </p>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose}
                  disabled={applying} aria-label="Close">
            <X size={16} />
          </Button>
        </div>

        <div className="overflow-y-auto p-4 space-y-4">
          {diff.kind === 'vehicles' && (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Stat tone="ok" icon={<Plus size={16} />} n={diff.counts.new} text="new" />
                <Stat tone="info" icon={<ArrowUpCircle size={16} />} n={diff.counts.enrich} text="enriched" />
                <Stat tone="warn" icon={<AlertTriangle size={16} />} n={diff.counts.review} text="to review" />
                <Stat tone="neutral" icon={<RefreshCw size={16} />} n={diff.counts.unchanged} text="unchanged" />
              </div>

              {diff.review.length > 0 && (
                <Section title="Possible duplicates — review">
                  {diff.review.map((r, i) => (
                    <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium">{r.unit}</span>
                      <span className="text-2xs text-muted-foreground">{r.reason}</span>
                    </li>
                  ))}
                </Section>
              )}
              {diff.new.length > 0 && (
                <Section title="New vehicles (will be added)">
                  {diff.new.map((r, i) => (
                    <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium">{r.unit}</span>
                      {r.plate && <span className="text-2xs text-muted-foreground font-mono">{r.plate}</span>}
                      {r.vin && <span className="text-2xs text-muted-foreground font-mono">{r.vin}</span>}
                    </li>
                  ))}
                </Section>
              )}
              {diff.enrich.length > 0 && (
                <Section title="Existing vehicles (empty fields filled, nothing overwritten)">
                  {diff.enrich.map((r, i) => (
                    <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium">{r.matched_unit}</span>
                      <span className="text-2xs text-muted-foreground">
                        +{r.fills.join(', ')} <span className="opacity-60">(matched by {r.by})</span>
                      </span>
                    </li>
                  ))}
                </Section>
              )}
            </>
          )}

          {diff.kind === 'work_orders' && (() => {
            // Only rows whose Datatruck fields actually differ are worth
            // showing/applying; the rest are "already up to date".
            const changedRows = diff.update.filter((r) => (r.changes?.length ?? 0) > 0);
            const unchanged = diff.update.length - changedRows.length;
            return (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <Stat tone="ok" icon={<Plus size={16} />} n={diff.counts.new} text="new work orders" />
                  <Stat tone="info" icon={<RefreshCw size={16} />} n={changedRows.length} text="with changes" />
                </div>
                {unchanged > 0 && (
                  <p className="text-2xs text-muted-foreground">
                    {unchanged.toLocaleString()} existing work order{unchanged === 1 ? '' : 's'} already
                    up to date — no field changes.
                  </p>
                )}
                {diff.new.length > 0 && (
                  <Section title="New work orders (will be added)">
                    {diff.new.slice(0, 100).map((r, i) => (
                      <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-medium">{r.number || r.external_id}</span>
                        <span className="text-2xs text-muted-foreground">{r.vendor}</span>
                        {r.vehicle && <span className="text-2xs text-muted-foreground">· {r.vehicle}</span>}
                        {r.total != null && <span className="text-2xs text-muted-foreground tabular-nums">· ${r.total}</span>}
                      </li>
                    ))}
                  </Section>
                )}
                {changedRows.length > 0 && (
                  <Section title="Changed — Datatruck fields refreshed, your edits kept">
                    {changedRows.slice(0, 100).map((r, i) => (
                      <li key={i}>
                        <span className="font-medium">{r.number || r.external_id}</span>
                        <ul className="ml-3 mt-0.5 space-y-0.5">
                          {r.changes!.map((c, j) => (
                            <li key={j} className="text-2xs text-muted-foreground">
                              {c.field}:{' '}
                              <span className="line-through opacity-70">{c.from || '—'}</span>
                              {' → '}
                              <span className="text-foreground font-medium">{c.to || '—'}</span>
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </Section>
                )}
              </>
            );
          })()}

          {nothingToDo && (
            <p className="text-sm text-muted-foreground text-center py-6">
              Everything is already up to date — nothing to apply.
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border bg-muted/20">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={applying}>
            Decline
          </Button>
          <Button type="button" size="sm" onClick={onAccept} disabled={applying || nothingToDo}>
            {applying ? <Loader2 size={14} className="animate-spin" /> : null}
            {applying ? 'Applying…' : `Accept & apply${nothingToDo ? '' : ` (${actionable})`}`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide mb-1.5 text-muted-foreground">
        {title}
      </p>
      <ul className="space-y-1 text-sm border border-border rounded-md px-3 py-2 max-h-44 overflow-y-auto">
        {children}
      </ul>
    </div>
  );
}

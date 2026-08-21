/**
 * One-time "Import drivers from Datatruck" onboarding action.
 *
 * The ongoing sync only LINKS Datatruck drivers onto existing driver-users
 * (a background job must never mass-create accounts).  This panel is the
 * deliberate human act that creates the missing ones: preview first (who
 * would be created / linked / needs review / is skipped), then an
 * idempotent apply.  Created drivers are roster entries — no login until
 * they're invited.
 *
 * Rendered inside the Datatruck card (below the synced-data table); gated
 * by the card's own permission (can_manage_integrations).
 */

import { useState } from 'react';
import { Loader2, UserPlus } from 'lucide-react';
import { applyDriverImport, getDriverImportPlan } from './api';
import type { DriverImportPlan, DriverImportResult } from './api';

export default function DriverImportPanel() {
  const [plan, setPlan] = useState<DriverImportPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<DriverImportResult | null>(null);
  const [error, setError] = useState('');

  const loadPlan = async () => {
    if (loading) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      setPlan(await getDriverImportPlan());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not build the plan');
    } finally {
      setLoading(false);
    }
  };

  const apply = async () => {
    if (applying) return;
    setApplying(true);
    setError('');
    try {
      const res = await applyDriverImport();
      setResult(res);
      setPlan(await getDriverImportPlan());   // refresh — should show 0 to create
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setApplying(false);
    }
  };

  const actionable = plan ? plan.counts.create + plan.counts.link : 0;

  return (
    <div className="mt-3 rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            <UserPlus size={14} className="text-muted-foreground" />
            Import drivers from Datatruck
          </div>
          <p className="text-2xs text-muted-foreground">
            One-time onboarding: creates 4truck driver records for synced
            Datatruck drivers that don't exist yet (matched by CDL, then
            email). Preview first — nothing changes until you apply.
          </p>
        </div>
        <button
          type="button"
          onClick={loadPlan}
          disabled={loading}
          className="shrink-0 px-2.5 py-1.5 rounded border border-border bg-muted text-xs text-foreground hover:border-ring focus:outline-none disabled:opacity-50 min-h-tap"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : 'Preview import'}
        </button>
      </div>

      {plan && (
        <div className="mt-2 space-y-1.5">
          <p className="text-xs text-foreground">
            <span className="font-semibold">{plan.counts.create}</span> to create
            {' · '}<span className="font-semibold">{plan.counts.link}</span> to link
            {' · '}{plan.already_linked} already linked
            {plan.counts.review > 0 && <> {' · '}<span className="font-semibold">{plan.counts.review}</span> need review</>}
            {plan.counts.skipped > 0 && <> {' · '}{plan.counts.skipped} skipped (inactive)</>}
          </p>
          {plan.create.length > 0 && (
            <div className="max-h-28 overflow-y-auto rounded border border-border bg-muted/40 px-2 py-1">
              {plan.create.map((d) => (
                <div key={d.external_id} className="text-2xs text-muted-foreground">
                  + {d.name}{d.email ? ` · ${d.email}` : ''}
                </div>
              ))}
            </div>
          )}
          {plan.review.length > 0 && (
            <div className="rounded border border-border bg-muted/40 px-2 py-1">
              {plan.review.map((d) => (
                <div key={d.external_id} className="text-2xs text-muted-foreground">
                  ⚠ {d.name} — {d.reason}
                </div>
              ))}
            </div>
          )}
          {actionable > 0 && (
            <button
              type="button"
              onClick={apply}
              disabled={applying}
              className="px-2.5 py-1.5 rounded bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 focus:outline-none disabled:opacity-50 min-h-tap"
            >
              {applying
                ? <Loader2 size={14} className="animate-spin" />
                : `Import ${plan.counts.create} + link ${plan.counts.link}`}
            </button>
          )}
        </div>
      )}

      {result && (
        <p className="mt-2 text-xs text-foreground">
          Done — created {result.created}, linked {result.linked}
          {result.review > 0 ? `, ${result.review} still need review` : ''}.
        </p>
      )}
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </div>
  );
}

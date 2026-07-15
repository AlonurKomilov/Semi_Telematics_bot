/**
 * Generic write-action approve card — the copilot "hands" UI.
 *
 * The AI proposes a write (`{type:'action_proposal', proposal_id, summary,
 * risk}`); this card shows the plain-language effect + Approve / Reject.
 * Approve → the server re-authorizes and executes; Reject → recorded, no
 * write.  Registered as the `action_proposal` renderer, so it's THE card
 * for every write action in 4.0 — a feature can later ship a richer card
 * in its own `ai_actions/` folder that registers a more specific type.
 *
 * On mount it fetches the live status (a proposal approved on another
 * device / earlier session shows "Done" instead of a stale button).
 */
import { useState, useEffect } from 'react';
import { Check, X, Loader2, ShieldAlert } from 'lucide-react';
import { aiApproveAction, aiRejectAction, aiGetActionStatus } from '../../../api/client';
import { toneClasses } from '../../../lib/status';
import { registerArtifact } from './registry';
import type { Artifact } from './types';

type Phase = 'pending' | 'working' | 'done' | 'declined' | 'failed' | 'expired';

function ActionProposalView({ artifact }: { artifact: Artifact }) {
  const a = artifact as Artifact & {
    proposal_id?: string; summary?: string; risk?: string; error?: string;
    consequence?: string;
  };
  const [phase, setPhase] = useState<Phase>('pending');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  // Reconcile with the server on mount — a proposal may already be
  // resolved (approved elsewhere, expired) so the card never lies.
  useEffect(() => {
    if (!a.proposal_id) return;
    let alive = true;
    aiGetActionStatus(a.proposal_id)
      .then((s) => {
        if (!alive) return;
        if (s.status === 'consumed') { setPhase('done'); setResult(s.result); }
        else if (s.status === 'declined') setPhase('declined');
        else if (s.status === 'failed') setPhase('failed');
      })
      .catch(() => { if (alive) setPhase('expired'); }); // 404 = pruned/expired
    return () => { alive = false; };
  }, [a.proposal_id]);

  if (!a.proposal_id) {
    return (
      <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-2xs text-destructive">
        {a.error || 'This action could not be created.'}
      </div>
    );
  }

  async function approve() {
    if (!a.proposal_id) return;
    setPhase('working'); setError('');
    try {
      const res = await aiApproveAction(a.proposal_id);
      setResult(res.result || null);
      setPhase('done');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed');
      setPhase('pending');
    }
  }
  async function reject() {
    if (!a.proposal_id) return;
    try { await aiRejectAction(a.proposal_id); } catch { /* best effort */ }
    setPhase('declined');
  }

  const msg = (result?.message as string) || '';

  return (
    <div className="mt-2 rounded-lg border border-border bg-muted/40 p-3">
      <div className="flex items-start gap-2">
        <span className="text-2xs font-medium text-foreground">{a.summary}</span>
        {a.risk && a.risk !== 'low' && (
          <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-3xs font-medium ${toneClasses('warn')}`}>
            <ShieldAlert size={12} aria-hidden /> {a.risk}
          </span>
        )}
      </div>

      {/* Reversibility hint at the approve moment — the copilot tells the
          user what committing does before they commit (P5, trust). */}
      {phase === 'pending' && a.consequence && (
        <p className="mt-1 text-3xs text-muted-foreground">{a.consequence}</p>
      )}

      {phase === 'pending' && (
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={approve}
            className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-2xs font-medium text-primary-foreground hover:brightness-110 transition"
          >
            <Check size={12} aria-hidden /> Approve
          </button>
          <button
            onClick={reject}
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-2xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <X size={12} aria-hidden /> Reject
          </button>
          {error && <span className="text-3xs text-destructive">{error}</span>}
        </div>
      )}
      {phase === 'working' && (
        <div className="mt-2 inline-flex items-center gap-1.5 text-2xs text-muted-foreground">
          <Loader2 size={12} className="animate-spin" aria-hidden /> Working…
        </div>
      )}
      {phase === 'done' && (
        <div className="mt-2 inline-flex items-center gap-1.5 text-2xs text-ok">
          <Check size={12} aria-hidden /> {msg || 'Done'}
        </div>
      )}
      {phase === 'declined' && (
        <div className="mt-2 text-2xs text-muted-foreground">Declined — nothing changed.</div>
      )}
      {phase === 'failed' && (
        <div className="mt-2 text-2xs text-destructive">This action failed. Ask the assistant to try again.</div>
      )}
      {phase === 'expired' && (
        <div className="mt-2 text-2xs text-muted-foreground">This proposal has expired. Ask again to get a fresh one.</div>
      )}
    </div>
  );
}

registerArtifact('action_proposal', (a) => <ActionProposalView artifact={a} />);

export default ActionProposalView;

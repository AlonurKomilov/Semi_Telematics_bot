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
import { useState, useEffect, useRef } from 'react';
import { Check, X, Loader2, ShieldAlert, Undo2 } from 'lucide-react';
import { aiApproveAction, aiRejectAction, aiUndoAction, aiGetActionStatus } from '../../../api/client';
import { toneClasses } from '../../../lib/status';
import { registerArtifact } from './registry';
import type { Artifact } from './types';

type Phase = 'pending' | 'working' | 'done' | 'declined' | 'failed' | 'expired' | 'undoing' | 'undone';

function ActionProposalView({ artifact }: { artifact: Artifact }) {
  const a = artifact as Artifact & {
    proposal_id?: string; summary?: string; risk?: string; error?: string;
    consequence?: string;
  };
  const [phase, setPhase] = useState<Phase>('pending');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  // Copilot-style undo: availability from the server, two-click confirm
  // (163 items shouldn't vanish on a stray click), outcome message.
  const [undoAvailable, setUndoAvailable] = useState(false);
  const [undoConfirm, setUndoConfirm] = useState(false);
  const [undoResult, setUndoResult] = useState<Record<string, unknown> | null>(null);
  const undoConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const undoArmedAt = useRef(0);
  useEffect(() => () => { if (undoConfirmTimer.current) clearTimeout(undoConfirmTimer.current); }, []);

  // Reconcile with the server on mount — a proposal may already be
  // resolved (approved elsewhere, expired) so the card never lies.
  useEffect(() => {
    if (!a.proposal_id) return;
    let alive = true;
    aiGetActionStatus(a.proposal_id)
      .then((s) => {
        if (!alive) return;
        if (s.status === 'consumed') {
          setPhase('done'); setResult(s.result); setUndoAvailable(!!s.undoable);
        }
        else if (s.status === 'undone') { setPhase('undone'); setUndoResult(s.undo_result ?? null); }
        else if (s.status === 'undoing') setPhase('undoing');
        else if (s.status === 'declined') setPhase('declined');
        else if (s.status === 'failed') setPhase('failed');
      })
      .catch(() => { if (alive) setPhase('expired'); }); // 404 = pruned/expired
    return () => { alive = false; };
  }, [a.proposal_id]);

  // A card that mounts (or lands) in 'undoing' would otherwise show a
  // spinner forever — the mount reconcile is one-shot.  Poll until the
  // state turns terminal; the interval dies with the phase.
  useEffect(() => {
    if (phase !== 'undoing' || !a.proposal_id) return;
    const pid = a.proposal_id;
    const iv = setInterval(() => {
      aiGetActionStatus(pid)
        .then((s) => {
          if (s.status === 'undone') { setPhase('undone'); setUndoResult(s.undo_result ?? null); }
          else if (s.status === 'consumed') {
            // Undo failed server-side and reverted — back to done.
            setPhase('done'); setResult(s.result); setUndoAvailable(!!s.undoable);
          }
        })
        .catch(() => { /* keep polling; terminal states end it */ });
    }, 2000);
    return () => clearInterval(iv);
  }, [phase, a.proposal_id]);

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
      // Freshly executed — re-read undo availability from the server
      // (registry recipe + window), never assume it client-side.
      aiGetActionStatus(a.proposal_id)
        .then((s) => setUndoAvailable(!!s.undoable))
        .catch(() => { /* card just shows no Undo */ });
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
  async function undo() {
    if (!a.proposal_id) return;
    if (!undoConfirm) {
      // First click arms the confirm; it disarms itself after 5s.
      setUndoConfirm(true);
      undoArmedAt.current = Date.now();
      if (undoConfirmTimer.current) clearTimeout(undoConfirmTimer.current);
      undoConfirmTimer.current = setTimeout(() => setUndoConfirm(false), 5000);
      return;
    }
    // An accidental double-click would arm-then-fire in one gesture —
    // a confirm only counts once it's been visible for a beat.
    if (Date.now() - undoArmedAt.current < 300) return;
    setUndoConfirm(false);
    setPhase('undoing'); setError('');
    try {
      const res = await aiUndoAction(a.proposal_id);
      setUndoResult(res.result || null);
      setPhase('undone');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Undo failed');
      setPhase('done');
    }
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
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-2xs text-ok">
            <Check size={12} aria-hidden /> {msg || 'Done'}
          </span>
          {undoAvailable && (
            <button
              onClick={undo}
              className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-2xs transition-colors ${
                undoConfirm
                  ? `font-medium ${toneClasses('warn')}`
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              <Undo2 size={12} aria-hidden />
              {undoConfirm ? 'Undo this action?' : 'Undo'}
            </button>
          )}
          {error && <span className="text-3xs text-destructive">{error}</span>}
        </div>
      )}
      {phase === 'undoing' && (
        <div className="mt-2 inline-flex items-center gap-1.5 text-2xs text-muted-foreground">
          <Loader2 size={12} className="animate-spin" aria-hidden /> Undoing…
        </div>
      )}
      {phase === 'undone' && (
        <div className="mt-2 inline-flex items-center gap-1.5 text-2xs text-muted-foreground">
          <Undo2 size={12} aria-hidden />
          {(undoResult?.message as string) || 'Undone — the changes were reversed.'}
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

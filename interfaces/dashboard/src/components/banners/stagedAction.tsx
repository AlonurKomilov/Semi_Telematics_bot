/**
 * stagedAction — the cancellable pending-action primitive.
 *
 * The Gmail-undo-send pattern, as a reusable contract ANY feature can
 * wrap around its existing POST — no backend change needed:
 *
 *   stagedAction({
 *     label: 'Acknowledging 12 alerts',
 *     commit: () => apiJSON('/alerts/bulk-ack', ...),
 *   })
 *
 * Contract:
 *   1. Banner: "<label> — Applies in Ns" + Cancel.
 *   2. Countdown ends → commit() runs (the REAL request happens only
 *      now) → success toast.  Commit failure → error banner with Retry.
 *   3. Cancel → nothing was ever sent → "Cancelled" feedback.
 *   4. Tab closed / navigated away during a WAITING window (the
 *      countdown, or a failure banner awaiting Retry) → commit fires
 *      immediately with `{keepalive: true}` so the request outlives the
 *      page: the user's intent is never silently dropped.  Residual gap
 *      (documented): a close in the exact moment a commit is already in
 *      flight rides that normal fetch, which an unloading page MAY
 *      abort.  Limitations (v1): the pending window is per-tab — another
 *      device doesn't see it; server-side staging (the
 *      ai_action_proposals machinery) is the upgrade path when a feature
 *      needs cross-device pending state.
 *
 * The sibling `undoableAction` is the execute-then-undo variant: commits
 * IMMEDIATELY and offers Undo for N seconds — for actions where instant
 * effect matters and a reverse call exists.
 */
import { toast } from 'sonner';
import { playUiCue } from '../../mods/sound/cue';
import { showBanner } from './AppBanner';

const DEFAULT_SECONDS = 10;

export interface CommitHint {
  /** True when the commit fires from a CLOSING tab — the caller should
   * pass it into its fetch (``keepalive: true``) so the browser lets the
   * request outlive the page.  Without it, unload-time fetches can be
   * aborted and the "intent never lost" guarantee doesn't hold. */
  keepalive?: boolean;
}

export interface StagedActionOptions {
  /** What is about to happen — present tense ("Acknowledging 12 alerts"). */
  label: string;
  detail?: string;
  seconds?: number;
  /** The real request. Runs on expiry (or immediately on tab close, with
   * ``{keepalive: true}`` — forward the hint into the fetch). */
  commit: (hint?: CommitHint) => Promise<void>;
  onCancel?: () => void;
  /** Success feedback after commit (default: "<label> — done"). */
  successMessage?: string;
}

// Every un-settled staged action registers a flush so a closing tab
// commits them all — losing the user's intent silently is never OK.
const _pendingFlushes = new Set<() => void>();
let _flushHooked = false;

function _hookPagehide() {
  if (_flushHooked || typeof window === 'undefined') return;
  _flushHooked = true;
  window.addEventListener('pagehide', () => {
    _pendingFlushes.forEach((flush) => flush());
    _pendingFlushes.clear();
  });
}

/** Number of not-yet-settled staged actions (exported for tests). */
export function pendingStagedCount(): number {
  return _pendingFlushes.size;
}

export function stagedAction(opts: StagedActionOptions): void {
  _hookPagehide();
  const seconds = opts.seconds ?? DEFAULT_SECONDS;
  let settled = false;

  const runCommit = async () => {
    if (settled) return;
    settled = true;
    _pendingFlushes.delete(flush);
    try {
      await opts.commit();
      toast.success(opts.successMessage ?? `${opts.label} — done`);
    } catch (e) {
      // The commit FAILED, so the intent is still live: re-arm the
      // tab-close safety net for the whole time the failure banner
      // waits — closing the tab then fires one last keepalive attempt
      // instead of silently accepting the failure.  Dismissing the
      // banner (✕) is the explicit "give up" that disarms it.
      settled = false;
      _pendingFlushes.add(flush);
      showBanner({
        tone: 'danger',
    // This file plays its own cues (see `playUiCue` below); the lane
    // must not add a second one on top.
    cue: false,
        title: `${opts.label} failed`,
        detail: e instanceof Error ? e.message : undefined,
        onClose: () => {
          settled = true;
          _pendingFlushes.delete(flush);
        },
        actions: [{
          label: 'Retry',
          primary: true,
          onClick: () => void runCommit(),
        }],
      });
    }
  };

  // pagehide flush: fire-and-forget with keepalive — the page is going
  // away, so no toasts and no retry UI; the request outliving the page
  // is all that matters.
  const flush = () => {
    if (settled) return;
    settled = true;
    opts.commit({ keepalive: true })
      .catch(() => { /* page is gone; nothing to show */ });
  };
  _pendingFlushes.add(flush);

  const cancel = () => {
    settled = true;
    _pendingFlushes.delete(flush);
    opts.onCancel?.();
    toast(`Cancelled — ${opts.label.toLowerCase()} was not applied`);
  };

  showBanner({
    tone: 'info',
    // This file plays its own cues (see `playUiCue` below); the lane
    // must not add a second one on top.
    cue: false,
    title: opts.label,
    detail: opts.detail,
    seconds,
    countdown: 'commit',
    onExpire: runCommit,
    onClose: cancel,                 // ✕ on a pending action = Cancel
    actions: [{ label: 'Cancel', onClick: cancel }],
  });
}

export interface UndoableActionOptions {
  /** What just happened — past tense ("Acknowledged 12 alerts"). */
  label: string;
  seconds?: number;
  /** The reverse call, offered for `seconds`. */
  undo: () => Promise<void>;
}

/** Execute-then-undo: call AFTER the action succeeded to offer a
 * time-boxed Undo.  (The action itself already ran — this only shows
 * the window.) */
export function undoableAction(opts: UndoableActionOptions): void {
  // The three cues this function was named for. `undo` marks the window
  // OPENING — the sound means "that is reversible for a few seconds" —
  // and the other two answer what happened when it was taken. All three
  // were declared, packed and validated from the day the engine shipped
  // and had no call site; this is the one.
  //
  // Imported by path, never through `../../mods`: that barrel exports
  // ModPanel, which imports this file.
  playUiCue('undo');
  showBanner({
    tone: 'ok',
    // This file plays its own cues (see `playUiCue` below); the lane
    // must not add a second one on top.
    cue: false,
    title: opts.label,
    seconds: opts.seconds ?? DEFAULT_SECONDS,
    countdown: 'dismiss',
    actions: [{
      label: 'Undo',
      onClick: async () => {
        try {
          await opts.undo();
          playUiCue('success');
          toast.success('Undone');
        } catch (e) {
          playUiCue('error');
          toast.error(e instanceof Error ? e.message : 'Undo failed');
        }
      },
    }],
  });
}

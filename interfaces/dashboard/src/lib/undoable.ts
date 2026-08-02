/**
 * Destructive actions with an Undo affordance.
 *
 * The delete is REAL and immediate — no staged writes, no ambiguity
 * about server state.  Undo simply restores the action's trail group
 * (capabilities/activity_trail), which is why the button can be honest
 * about what it does and why missing the toast costs nothing: the same
 * restore lives in the record's History forever.
 *
 * Usage:
 *   const res = await apiJSON('/maintenance/tasks/bulk/delete', {...});
 *   undoableToast({
 *     message: `Deleted ${res.deleted} tasks`,
 *     groupId: res.group_id,
 *     onRestored: load,
 *   });
 */
import { toast } from 'sonner';
import { apiJSON } from '../api/client';

/** Matches the acknowledge-undo window — one undo rhythm platform-wide. */
export const UNDO_WINDOW_MS = 15_000;

interface RestoreGroupResult {
  restored: number;
  conflicts: { entity_id: string; reason: string }[];
  skipped: number;
}

export function undoableToast({
  message, groupId, onRestored, durationMs = UNDO_WINDOW_MS,
}: {
  message: string;
  /** The trail group the action wrote; omit to show a plain toast. */
  groupId?: string | null;
  /** Refresh the caller's list once records are back. */
  onRestored?: () => void;
  durationMs?: number;
}): void {
  if (!groupId) {
    toast.success(message);
    return;
  }
  toast.success(message, {
    duration: durationMs,
    action: {
      label: 'Undo',
      onClick: () => { void runUndo(groupId, onRestored); },
    },
  });
}

async function runUndo(groupId: string, onRestored?: () => void) {
  const pending = toast.loading('Restoring…');
  try {
    const res = await apiJSON<RestoreGroupResult>(
      `/activity/restore-group/${groupId}`, { method: 'POST' },
    );
    // Never round a partial result up to "done" — the count the user
    // reads is the count that actually came back.
    const conflicts = res.conflicts?.length ?? 0;
    const noun = `record${res.restored === 1 ? '' : 's'}`;
    toast.success(
      conflicts > 0
        ? `${res.restored} ${noun} restored · ${conflicts} already exist`
        : `${res.restored} ${noun} restored`,
      { id: pending },
    );
    onRestored?.();
  } catch (e) {
    toast.error(
      e instanceof Error ? e.message : 'Could not restore',
      { id: pending },
    );
  }
}

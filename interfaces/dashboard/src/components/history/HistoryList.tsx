/**
 * The shared who-did-what renderer — ONE way to show activity-trail
 * events everywhere (the /audit page, per-record History dialogs, and
 * every feature that adopts the trail next).  Generalized from the
 * Inventory drawer's HISTORY card, the shape the owner asked to make
 * the standard.
 *
 * Events arrive in the facade wire shape: field-level changes as
 * `{field: {from, to}}`, an `actor_name` resolved server-side, and
 * bulk actions pre-collapsed into `is_group` rows.
 */
import { ArchiveRestore } from 'lucide-react';
import { formatDate } from '../../utils/datetime';

export interface ActivityChange { from: unknown; to: unknown }

export interface ActivityEvent {
  id: string;
  source?: string;
  entity_type: string;
  entity_id: string;
  action: string;
  changes: Record<string, ActivityChange>;
  actor_user_id: number | null;
  actor_name?: string;
  group_id?: string | null;
  is_group?: boolean;
  count?: number;
  sample_entity_ids?: string[];
  context?: Record<string, unknown>;
  note?: string;
  created_at: string;
  /** Server-decided: this delete event can be brought back by THIS viewer. */
  restorable?: boolean;
  /** Raw activity_events id — what the restore endpoint keys on (the
   *  facade's `id` is prefixed by source). */
  event_id?: number;
}

/** Action → sentence fragment. Unknown actions fall through readable. */
export const ACTION_LABEL: Record<string, string> = {
  create: 'Created',
  update: 'Edited',
  delete: 'Deleted',
  complete: 'Completed',
  status_change: 'Status changed',
  reopen: 'Reopened',
  spawn: 'Auto-renewed',
  snooze: 'Snoozed',
  attachment_add: 'Attachment added',
  attachment_remove: 'Attachment removed',
  restore: 'Restored',
  deactivate: 'Deactivated',
  merge_away: 'Merged into another record',
  merge_in: 'Absorbed a merged record',
  link_public: 'Linked to shared entry',
  unlink_public: 'Unlinked from shared entry',
  review_submit: 'Review submitted',
  // Team management (user entity)
  role_change: 'Role changed',
  manager_tier_change: 'Manager tier changed',
  co_owner_added: 'Co-owner added',
  co_owner_removed: 'Co-owner removed',
  telegram_invite_issued: 'Telegram sign-in link issued',
  user_activate: 'Activated',
  user_deactivate: 'Deactivated',
  user_quiet_hours_set: 'Personal working hours set',
  user_assigned_work_hours_set: 'Working-hours schedule assigned',
  company_assignment: 'Company access changed',
  // Driver applications (hiring decisions — FMCSA audit surface)
  application_status_changed: 'Stage changed',
  application_check_completed: 'Pre-hire check completed',
  application_check_cleared: 'Pre-hire check un-ticked',
  application_notes_updated: 'Recruiter notes edited',
};

export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action.replace(/[_.]/g, ' ');
}

/** Entity type → feature word for the account-wide feed. */
export const ENTITY_LABEL: Record<string, string> = {
  maintenance_task: 'Maintenance task',
  maintenance_template: 'Maintenance template',
  maintenance: 'Maintenance task',       // frozen-log rows
  load: 'Load',
  inventory_item: 'Inventory item',
  vehicle: 'Vehicle',
  part: 'Part',
  vendor: 'Vendor',
  vendor_directory_entry: 'Shared vendor entry',
  sharing_settings: 'Sharing settings',
  user: 'Team member',
  work_order: 'Work order',
  service_task: 'Service task',
  invite: 'Invite',
  company: 'Company',
  schedule: 'Working hours',
  account: 'Account',
  setting: 'Account setting',
  role: 'Role permissions',
  alert_type: 'Alert routing',
  alert_topic: 'Alert topic',
  coaching: 'Coaching',
  driver_pay_run: 'Driver pay run',
  driver: 'Driver',
  driver_application: 'Driver application',
  integration: 'Integration',
};

export function entityLabel(entityType: string): string {
  return ENTITY_LABEL[entityType] ?? entityType.replace(/[_.]/g, ' ');
}

export function fieldLabel(field: string): string {
  const s = field.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return 'empty';
  if (typeof v === 'number') return Number(v).toLocaleString();
  return String(v);
}

/** One `Field: old → new` line. */
function ChangeLine({ field, change }: { field: string; change: ActivityChange }) {
  return (
    <div className="flex items-baseline gap-1.5 min-w-0 text-xs">
      <span className="shrink-0 text-muted-foreground">{fieldLabel(field)}:</span>
      <span className="truncate text-muted-foreground line-through decoration-border">
        {fmtValue(change.from)}
      </span>
      <span aria-hidden className="shrink-0 text-muted-foreground/60">→</span>
      <span className="truncate text-foreground font-medium">{fmtValue(change.to)}</span>
    </div>
  );
}

/**
 * Compact change list — the grid-cell / dialog body form.  Caps the
 * visible lines; the remainder is summarized honestly (never silently
 * dropped — the incident rule).
 */
export function ChangeLines({
  changes, max = 4,
}: {
  changes: Record<string, ActivityChange>;
  max?: number;
}) {
  const entries = Object.entries(changes ?? {});
  if (entries.length === 0) return null;
  const shown = entries.slice(0, max);
  const rest = entries.length - shown.length;
  return (
    <div className="space-y-0.5 min-w-0">
      {shown.map(([f, c]) => <ChangeLine key={f} field={f} change={c} />)}
      {rest > 0 && (
        <div className="text-2xs text-muted-foreground">+{rest} more field{rest === 1 ? '' : 's'}</div>
      )}
    </div>
  );
}

/**
 * The vertical event list — the History dialog body.  Newest first,
 * one block per event: what happened, by whom, when, and every field
 * that changed.
 */
export function HistoryList({
  events, tz, emptyText = 'No recorded activity yet.', onRestore,
}: {
  events: ActivityEvent[];
  tz: string;
  emptyText?: string;
  /** When set, server-flagged restorable events show a Restore action. */
  onRestore?: (event: ActivityEvent) => void;
}) {
  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground py-4 text-center">{emptyText}</p>;
  }
  return (
    <ol className="space-y-3">
      {events.map((e) => (
        <li key={e.id} className="border-l-2 border-border pl-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-sm font-medium text-foreground">
              {actionLabel(e.action)}
              {e.action === 'spawn' && e.context?.from_parent != null && (
                <span className="font-normal text-muted-foreground"> from #{String(e.context.from_parent)}</span>
              )}
            </span>
            <span className="shrink-0 text-2xs text-muted-foreground tabular-nums">
              {formatDate(e.created_at, { timeZone: tz })}
            </span>
          </div>
          <p className="text-2xs text-muted-foreground mb-1">
            {e.actor_name || (e.actor_user_id == null ? 'System' : `#${e.actor_user_id}`)}
          </p>
          <ChangeLines changes={e.changes} max={12} />
          {e.note ? <p className="mt-0.5 text-2xs text-muted-foreground">{e.note}</p> : null}
          {e.restorable && onRestore && (
            <button
              type="button"
              onClick={() => onRestore(e)}
              className="mt-1.5 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
            >
              <ArchiveRestore size={14} /> Restore this record
            </button>
          )}
          {e.action === 'restore' && e.context?.restored_from_event != null && (
            <p className="mt-0.5 text-2xs text-muted-foreground">
              brought back from the deletion above — nothing was erased
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

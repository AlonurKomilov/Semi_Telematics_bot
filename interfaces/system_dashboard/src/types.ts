// Operator-side type definitions.  Mirrors what /api/system/* returns.
// Kept narrow — the operator UI shows everything, but we still type
// the fields we read so refactors at the backend surface a TS error
// rather than a runtime ``undefined``.

export interface SubscriptionSummary {
  tier: string;
  status: string;
  vehicle_count: number;
  is_comped: boolean;
  comp_expires_at: string | null;
  past_due_since: string | null;
  billing_email: string;
  provider: string;
}

export interface AccountListItem {
  id: number;
  name: string;
  slug: string;
  tier: string;
  is_active: boolean;
  created_at: string;
  subscription: SubscriptionSummary;
}

export interface SubscriptionFull {
  account_id: number;
  tier: string;
  status: string;
  vehicle_count: number;
  base_vehicles: number;
  monthly_base_usd: number;
  extra_vehicle_cents: number;
  billing_email: string;
  provider: string;
  provider_customer_id: string;
  provider_subscription_id: string;
  provider_base_item_id: string;
  provider_extra_item_id: string;
  trial_ends_at: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  canceled_at: string | null;
  past_due_since: string | null;
  is_comped: number;  // SQLite-bool — 0 or 1
  comp_expires_at: string | null;
  comp_reason: string;
  comp_granted_by: number | null;
  comp_granted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BillingComputation {
  tier: string;
  base_cents: number;
  included: number;
  extra_unit_cents: number;
  active_vehicles: number;
  inactive_vehicles: number;
  extras: number;
  extras_cents: number;
  subtotal_cents: number;
}

export interface Invoice {
  id: number;
  provider_invoice_id: string;
  status: string;
  amount_due_cents: number;
  amount_paid_cents: number;
  currency: string;
  period_start: string | null;
  period_end: string | null;
  hosted_invoice_url: string;
  invoice_pdf_url: string;
  paid_at: string | null;
  created_at: string;
}

export interface CompHistoryRow {
  id: number;
  account_id: number;
  action: string;  // granted | renewed | revoked | expired | expiring_reminder
  expires_at: string | null;
  reason: string;
  actor_user_id: number | null;
  created_at: string;
}

export interface AccountDetail {
  account: {
    id: number;
    name: string;
    slug: string;
    tier: string;
    is_active: boolean;
    created_at: string;
    timezone: string;
    bot_username: string;
  };
  subscription: SubscriptionFull | null;
  billing: BillingComputation | null;
  user_count: number;
  invoices: Invoice[];
  comp_history: CompHistoryRow[];
}

export interface SystemStats {
  accounts_total: number;
  accounts_active: number;
  by_status: Record<string, number>;
  by_tier: Record<string, number>;
  comped: number;
  past_due: number;
}

// Operator action response shapes

export interface SyncQuantityResult {
  skipped: string | null;
  before?: number;
  after?: number;
  account_id: number;
  reason?: string;
}

export interface RefreshVehiclesResult {
  account_id: number;
  persisted: number;
  active_vehicles: number;
  inactive_vehicles: number;
}

export interface BillingEmailResult {
  account_id: number;
  email: string;
  synced_to_provider: boolean;
  reason?: string;
}

// Metrics snapshot returned by /system/metrics-snapshot.  Each value
// is the cumulative counter total since the API process started, with
// the per-label combination joined by ``|`` (matches the backend
// ``_counter_map`` projection).  Empty object when the Prometheus
// client isn't installed; UI must handle that.
export interface MetricsSnapshot {
  webhook_events: Record<string, number>;   // "event_type|result" → count
  sync_quantity:  Record<string, number>;   // "result" → count
  notifications:  Record<string, number>;   // "kind|channel" → count
  comp_sweep:     Record<string, number>;   // "action" → count
}

// Audit feed row shapes (joined with account name server-side).
export interface CompActionRow extends CompHistoryRow {
  account_name: string;
}

export interface PastDueRow {
  account_id: number;
  account_name: string;
  tier: string;
  status: string;
  past_due_since: string;
  provider: string;
  billing_email: string;
}

export interface WebhookEventRow {
  event_id: string;
  event_type: string;
  processed_at: string;
  account_id: number | null;
  account_name: string | null;
}

// Cross-account invoice row (Invoice + account name)
export interface InvoiceWithAccount extends Invoice {
  account_id: number;
  account_name: string;
}

// System health
export interface HealthComponent {
  status: 'ok' | 'warn' | 'down';
  detail: string;
}
export interface SystemHealth {
  overall: 'ok' | 'warn' | 'down';
  components: Record<string, HealthComponent>;
}

// Cross-account user row
export interface SystemUser {
  id: number;
  telegram_id: number;
  account_id: number;
  account_name: string;
  role: string;
  display_name: string;
  email: string | null;
  is_active: number;  // SQLite bool 0/1
  created_at: string;
  last_seen: string | null;  // ISO timestamp; stamped (throttled) on each authed API hit
}

// One row of the per-user sessions expander on the /users page.
// Pass 1 surfaces these read-only; Pass 2 adds a revoke ✕.
export interface UserSession {
  id: number;
  user_id: number;
  jti: string;
  device_label: string;
  user_agent: string;
  ip: string;
  created_at: string;
  last_seen: string;
  expires_at: string;
  revoked_at: string | null;
}

// Error log row
export interface ErrorRow {
  id: number;
  source: string;
  job_name: string | null;
  account_id: number | null;
  error_type: string;
  error_msg: string;
  traceback: string | null;
  created_at: string;
}

// ── Alert routing config ────────────────────────────────────────

export type AlertRoutingMode = 'single_group' | 'per_persona_groups';

export type PersonaKey = 'owner_admin' | 'dispatcher' | 'safety' | 'fleet' | 'hr';

export interface PersonaGroupRow {
  persona:    PersonaKey;
  chat_id:    number;
  chat_title: string;
  is_active:  boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertRoutingConfig {
  account_id:         number;
  alert_routing_mode: AlertRoutingMode;
  // Always populated for every valid persona; null for unregistered.
  personas:           Record<PersonaKey, PersonaGroupRow | null>;
  valid_personas:     PersonaKey[];
}

// AI feedback — what users thumbed-down on the dashboard chat.  One
// row per ai_usage row that has ``had_reask=TRUE``; the reason +
// note are optional (users skip the follow-up form sometimes).
export type AIFeedbackReason =
  | 'inaccurate'
  | 'off_topic'
  | 'incomplete'
  | 'hallucinated'
  | 'vague'
  | 'unjust_refusal'
  | 'other';

export interface AIFeedbackRow {
  id: number;
  account_id: number;
  account_name: string;
  user_id: number;
  model: string;
  role: string | null;
  prompt_category: string | null;
  feedback_reason: AIFeedbackReason | null;
  feedback_note: string | null;
  latency_ms: number | null;
  created_at: string;
  user_question: string | null;
  ai_answer: string | null;
}

export interface AIFeedbackResponse {
  items: AIFeedbackRow[];
  /** Aggregate count of rows per reason (over the same date window
   *  as ``items``, regardless of pagination).  The key ``"__none__"``
   *  counts bare thumbs-downs where the user skipped the form. */
  counts_by_reason: Record<string, number>;
  days: number;
  limit: number;
  offset: number;
}

// ── Data retention contract (read-only operator view) ──────────────

export interface RetentionNeed {
  feature: string;
  keep_days: number;
  reason: string;
}

export interface RetentionRun {
  ran_at: string | null;
  rows_deleted: number | null;
  accounts: number | null;
}

export interface RetentionTarget {
  key: string;
  label: string;
  scope: 'tenant' | 'platform';
  keep_days: number;
  needs: RetentionNeed[];
  last_run: RetentionRun | null;
}

export interface RetentionContract {
  job: {
    id: string;
    schedule: string;
    scope: string;
  };
  targets: RetentionTarget[];
}

export interface OrphanReport {
  account_id: number;
  scanned_files: number;
  referenced: number;
  grace_days: number;
  candidate_count: number;
  candidate_bytes: number;
  sample: string[];
}

export interface OrphanPurgeResult {
  account_id: number;
  grace_days: number;
  dry_run: boolean;
  candidate_count: number;
  candidate_bytes: number;
  deleted: number;
  deleted_bytes: number;
  sample: string[];
}

export interface SchedulerJob {
  job_id: string;
  trigger: string;
  next_run_at: string | null;
  category: string;
  description: string;
  updated_at: string;
}

export interface SchedulerJobsResponse {
  jobs: SchedulerJob[];
}

// ── File scans (per-upload AV scan observability) ─────────────────

export interface ScanHealth {
  enabled: boolean;
  host: string | null;
  port: number | null;
  max_concurrent: number;
  rate_per_sec: number;
  timeout_sec: number;
  scan_types: string[] | 'all';
}

export interface ScanTopSignature {
  signature: string;
  count: number;
}

export interface ScanStats {
  total: number;
  clean: number;
  infected: number;
  unavailable: number;
  latency_p50: number;
  latency_p95: number;
  latency_max: number;
  top_signatures: ScanTopSignature[];
  window_days: number;
}

export interface ScanEvent {
  id: number;
  account_id: number | null;
  user_id: number | null;
  surface: string;
  file_size: number | null;
  content_type: string | null;
  verdict: 'clean' | 'infected' | 'unavailable';
  signature: string | null;
  latency_ms: number | null;
  created_at: string;
}

export interface RescanJob {
  id: number;
  started_at: string;
  completed_at: string | null;
  status: 'running' | 'completed' | 'cancelled' | 'failed';
  total_files: number;
  scanned_files: number;
  clean_count: number;
  infected_count: number;
  error_count: number;
  started_by: number | null;
  cancelled_by: number | null;
  last_error: string | null;
}

export interface QuarantinedArticle {
  id: number;
  account_id: number;
  title: string;
  category: string;
  visibility: string;
  quarantined_at: string;
  quarantined_reason: string | null;
  created_by: number | null;
  creator_name: string | null;
}

export interface ScansOverview {
  health: ScanHealth;
  stats: ScanStats;
  recent: ScanEvent[];
  active_job: RescanJob | null;
  recent_jobs: RescanJob[];
  quarantine: QuarantinedArticle[];
}

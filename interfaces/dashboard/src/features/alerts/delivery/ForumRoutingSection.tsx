import { Fragment, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../../api/client';
import { FEATURE_GROUPS, SUBTYPE_LABELS } from './alertRoutingConstants';
import { Switch } from '../../../components/ui/switch';
import { ErrorState } from '../../../components/shell';
import { InfoTip } from '../../../components/tooltip';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../../components/ui/select';
import { useTimezone } from '../../../hooks/useTimezone';
import { formatDate } from '../../../utils/datetime';
import {
  ChevronDown, ChevronRight, Check,
  // Per-alert-type row icons.  The backend still ships `icon_emoji`
  // (used inside Telegram message bodies, where emoji is data not
  // UI), but the dashboard renders these lucide equivalents so the
  // table reads as a professional admin tool.
  AlertTriangle, HeartPulse, Fuel, ShieldAlert, Camera, ParkingSquare,
  MapPin, BarChart3, Wrench, FileText, RefreshCw,
  type LucideIcon,
} from 'lucide-react';
import { useRoleView } from '../../../context/RoleViewContext';

// alert_type → lucide icon.  Keep in sync with FORUM_TOPIC_SPEC in
// capabilities/alerting/forum_topics.py.  An unknown key falls back
// to a neutral marker so a newly-added catalog entry can't crash the
// table while the dashboard catches up.
const TYPE_ICON: Record<string, LucideIcon> = {
  faults:      AlertTriangle,
  health:      HeartPulse,
  fuel:        Fuel,
  events:      ShieldAlert,
  camera:      Camera,
  parking:     ParkingSquare,
  geofence:    MapPin,
  scorecard:   BarChart3,
  maintenance: Wrench,
  documents:   FileText,
  system:      RefreshCw,
};

interface CatalogRow {
  alert_type: string;
  name: string;
  icon_emoji: string;
  description: string;
  pinned: boolean;
}

interface RouteRow extends CatalogRow {
  is_mapped: boolean;
  is_active: boolean;
  message_thread_id: number | null;
  topic_name_snapshot: string;
  /** Per-topic "🟢 RESOLVED" auto-resolve receipt toggle.  Defaults
   *  to true on legacy rows (migration 079).  When false, the chat
   *  receipt is suppressed but the underlying alert_history row
   *  still flips to resolved — dashboard monitoring stays accurate. */
  send_resolve_receipt: boolean;
  /** Sub-category vocabulary + selection, only on types that have one
   *  (events).  ``selected === null`` ⇒ ALL kinds (the default). */
  subtypes?: { all: string[]; selected: string[] | null };
}

interface CustomTopic {
  id: number;
  name: string;
  alert_type: string;
  subtypes: string[];
  has_thread: boolean;
}

interface RoutingState {
  connected: boolean;
  chat_id?: number;
  chat_title?: string;
  setup_status?: string;
  last_setup_at?: string | null;
  last_repair_at?: string | null;
  catalog: CatalogRow[];
  routes: RouteRow[];
  custom_topics?: CustomTopic[];
  settings?: {
    ai_per_type: Record<string, boolean>;
  };
}

// Alert types that currently generate AI content — only these
// render an AI toggle in the dashboard.  Keep in sync with
// ``_AI_CAPABLE`` in interfaces/api/routes/admin.py.
const AI_CAPABLE_TYPES = ['faults', 'health', 'parking', 'camera'] as const;

/**
 * Inline section rendered inside the Telegram Bot admin card.
 *
 * Two modes: (a) not-yet-connected — shows the 3-step wizard, (b)
 * connected — shows status + per-topic toggle table.  Disconnecting
 * here only removes the binding; the actual Telegram topics live
 * on until /resetforum is run inside the group.
 */
export default function ForumRoutingSection() {
  const { viewHas } = useRoleView();
  const canConfigureRouting = viewHas('can_manage_config_all');
  const { t } = useTranslation();
  const tz = useTimezone();
  const [state, setState] = useState<RoutingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [showMapping, setShowMapping] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // Custom-topics add form + inline delete confirm (native confirm() is
  // banned in this feature — matches the card's other two-step confirms).
  const [showAddTopic, setShowAddTopic] = useState(false);
  const [ctName, setCtName] = useState('');
  const [ctType, setCtType] = useState('');
  const [ctSubs, setCtSubs] = useState<string[]>([]);
  const [confirmTopic, setConfirmTopic] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const data = await apiJSON<RoutingState>('/admin/forum-routing');
      setState(data);
    } catch {
      setState(null);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const handleToggle = async (alert_type: string, is_active: boolean) => {
    setBusyKey(alert_type);
    try {
      await apiJSON(`/admin/forum-routing/${alert_type}`, {
        method: 'PUT',
        body: { is_active },
      });
      const row = state?.routes.find(r => r.alert_type === alert_type);
      toast.success(t('forum_routing.toast_toggled', { name: row?.name || alert_type }));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  // Per-topic resolve-receipt toggle.  Uses a distinct busy key
  // (`__rr_…`) so it doesn't collide with the row's main is_active
  // spinner if both are mid-flight.  When the admin turns this off,
  // we suppress only the chat "🟢 RESOLVED" receipt — the underlying
  // alert_history row is still stamped resolved so dashboard counters
  // stay accurate.
  const handleToggleReceipt = async (alert_type: string, enabled: boolean) => {
    const key = `__rr_${alert_type}__`;
    setBusyKey(key);
    try {
      await apiJSON(`/admin/forum-routing/routes/${alert_type}/receipt`, {
        method: 'PUT',
        body: { send_resolve_receipt: enabled },
      });
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  const handleDisconnect = async () => {
    setBusyKey('__disconnect__');
    try {
      await apiJSON('/admin/forum-routing/disconnect', { method: 'POST' });
      toast.success(t('forum_routing.toast_disconnected'));
      setConfirmDisconnect(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  // The AI toggle and the sub-category chips write account_settings rows
  // (forum_ai.<type>, forum_subtypes.<type>) that the config family owns,
  // so both moved to can_manage_config_all.  Linking the group, creating
  // topics and testing delivery stay on can_manage_account — that is
  // operating the integration, not setting a value every future alert is
  // rendered through.  This section is reached on the latter, so the two
  // differ and the controls must be inert rather than error on click.
  const handleToggleAIForType = async (alertType: string, next: boolean) => {
    setBusyKey(`__ai_${alertType}__`);
    try {
      await apiJSON('/alerts/config', {
        method: 'PUT',
        body: { ai_per_type: { [alertType]: next } },
      });
      toast.success(t('forum_routing.toast_settings_saved'));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  // Sub-category selection for a type's group topic (e.g. Safety Events
  // → only crash + braking).  Empty selection is guarded; a full set
  // normalizes to ALL server-side.
  const toggleSubtype = async (r: RouteRow, sub: string) => {
    if (!r.subtypes || busyKey) return;
    const current = r.subtypes.selected ?? r.subtypes.all;
    const active = current.includes(sub);
    const next = active ? current.filter((s) => s !== sub) : [...current, sub];
    if (!next.length) {
      toast.error(t('forum_routing.subtype_keep_one'));
      return;
    }
    setBusyKey(`__st_${r.alert_type}__`);
    try {
      await apiJSON(`/alerts/config/${r.alert_type}/subtypes`, {
        method: 'PUT', body: { selected: next },
      });
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  const createTopic = async () => {
    const name = ctName.trim();
    if (!name || !ctType || busyKey) return;
    setBusyKey('__ctopic__');
    try {
      const res = await apiJSON<CustomTopic>('/admin/forum-routing/custom-topics', {
        method: 'POST', body: { name, alert_type: ctType, subtypes: ctSubs },
      });
      setCtName(''); setCtType(''); setCtSubs([]); setShowAddTopic(false);
      toast.success(t('forum_routing.toast_topic_created', { name: res.name }));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  const deleteTopic = async (topic: CustomTopic) => {
    setBusyKey('__ctopic__');
    try {
      await apiJSON(`/admin/forum-routing/custom-topics/${topic.id}`, { method: 'DELETE' });
      setConfirmTopic(null);
      toast.success(t('forum_routing.toast_topic_deleted'));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('forum_routing.toast_error'));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div className="border-t border-border mt-5 pt-5">
      {/* No section heading — the "Group delivery" tab title + the
          delivery-mode indicator above already frame this.  (Dropped
          "Alert group & topics" to end the group-noun pile-up.) */}
      <p className="text-xs text-muted-foreground mb-4">
        {t('forum_routing.section_desc')}
      </p>

      {loading && (
        <p className="text-sm text-muted-foreground">{t('forum_routing.loading')}</p>
      )}

      {/* Failed fetch must SAY so — the old code rendered nothing,
          leaving just the heading + description over blank space. */}
      {!loading && loadError && (
        <ErrorState message={t('forum_routing.load_error')} onRetry={() => { void load(); }} />
      )}

      {!loading && state && (
        <div className="space-y-4">
          {/* Single mode now: Forum group with topics.  Personal DMs
              are a per-user opt-in (Alerts bell → Notification preferences) and
              always fire on top of group routing, so there's no
              admin-level "DM mode" toggle — connecting a group only
              adds the topic-channel surface, it never replaces DMs. */}
          <div className="p-3 rounded-lg border border-border bg-card">
            <p className="text-sm font-medium">{t('forum_routing.mode_group_title')}</p>

            {!state.connected && (
              <div className="mt-3 space-y-2">
                <p className="text-xs font-semibold text-foreground">
                  {t('forum_routing.setup_wizard_title')}
                </p>
                <ol className="text-xs text-muted-foreground space-y-1.5 list-decimal list-inside">
                  <li>{t('forum_routing.setup_step_1')}</li>
                  <li>{t('forum_routing.setup_step_2')}</li>
                  <li>
                    {t('forum_routing.setup_step_3')
                      .split('/setupforum')
                      .map((part, i, arr) => (
                        <span key={i}>
                          {part}
                          {i < arr.length - 1 && (
                            <code className="px-1 py-0.5 bg-muted rounded text-foreground font-mono">/setupforum</code>
                          )}
                        </span>
                      ))}
                  </li>
                </ol>
              </div>
            )}

            {state.connected && (
              <div className="mt-1 space-y-0.5">
                <p className="text-xs text-muted-foreground inline-flex items-center gap-1">
                  <Check size={12} className="text-ok" />
                  {t('forum_routing.mode_group_connected', { title: state.chat_title })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t('forum_routing.mode_group_setup_status', {
                    // Task language, not system language: "provisioned"
                    // is the backend's word, the user reads "Topics created".
                    status: state.setup_status === 'provisioned'
                      ? t('forum_routing.status_ready')
                      : state.setup_status,
                  })}
                  {state.last_setup_at && (
                    ' · ' + t('forum_routing.mode_group_last_setup', {
                      when: formatDate(state.last_setup_at, { timeZone: tz }),
                    })
                  )}
                </p>
                <p className="text-xs text-muted-foreground font-mono">
                  {t('forum_routing.mode_group_chat_id', { id: state.chat_id })}
                </p>
                {/* A greyed control with no reason reads as broken, not
                    as withheld — say which permission it needs, the same
                    way the storage backend chooser does. */}
                {!canConfigureRouting && (
                  <p className="text-xs text-muted-foreground mt-2">
                    {t(
                      'forum_routing.needs_config_permission',
                      'The AI column and sub-category chips need Config · account-wide. You can still link the group, create topics and test delivery.',
                    )}
                  </p>
                )}
                <p className="text-xs text-muted-foreground mt-2">
                  {/* Count PROVISIONED threads only — a deliberately
                      disabled type must not read as missing/broken. */}
                  {t('forum_routing.topics_mapped', {
                    mapped: state.routes.filter(r => r.is_mapped).length,
                    total: state.routes.length,
                  })}
                </p>
              </div>
            )}
          </div>

          {/* Per-topic mapping + disconnect (connected only) */}
          {state.connected && (
            <>
              <button
                onClick={() => setShowMapping(s => !s)}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                {showMapping
                  ? <ChevronDown size={14} />
                  : <ChevronRight size={14} />}
                {showMapping
                  ? t('forum_routing.btn_hide_mapping')
                  : t('forum_routing.btn_show_mapping')}
              </button>

              {showMapping && (
                <div className="border border-border rounded-lg overflow-hidden">
                  {/* Legend ABOVE the controls it explains — the "click
                      to switch" affordance must be read BEFORE the
                      toggles, not discovered as trailing fine print. */}
                  <p className="text-xs text-muted-foreground px-3 py-2 border-b border-border/40 bg-muted/20">
                    {t('forum_routing.toggle_legend_lead')}
                    <span className="mx-1">·</span>
                    <strong className="font-medium text-foreground">AI</strong> = include the AI analysis in this topic's posts.
                    <span className="mx-1">·</span>
                    <strong className="font-medium text-foreground">Resolved</strong> = post a confirmation when an alert auto-resolves.
                    <span className="mx-1">·</span>
                    <strong className="font-medium text-foreground">Routing</strong> = post this alert type to the group at all (personal DMs are unaffected).
                  </p>
                  <table className="w-full text-sm">
                    <tbody>
                      {/* Column headers — switches aren't self-labeling,
                          so the setting name lives once at the top
                          (same column-matrix grammar as role mode). */}
                      <tr className="border-b border-border/40">
                        <td />
                        <td />
                        <td className="px-3 py-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground text-right">{t('forum_routing.col_ai')}</td>
                        <td className="px-3 py-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground text-right">{t('forum_routing.col_resolved')}</td>
                        <td className="px-3 py-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground text-right">{t('forum_routing.col_routing')}</td>
                      </tr>
                      {/* Grouped under the owning FEATURE — same
                          hierarchy as the role-mode expanders. */}
                      {FEATURE_GROUPS.map((g) => {
                        const groupRows = state.routes.filter((r) => g.types.includes(r.alert_type));
                        if (!groupRows.length) return null;
                        return (
                        <Fragment key={g.label}>
                          {/* Skip the caps heading for single-row groups
                              (Geofences, Scorecards…) — same rule as the
                              role-mode expander, so the two modes match. */}
                          {groupRows.length > 1 && (
                            <tr className="bg-muted/40">
                              <td colSpan={5} className="px-3 py-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                                {g.label}
                              </td>
                            </tr>
                          )}
                          {groupRows.map(r => {
                        const isAICapable = (AI_CAPABLE_TYPES as readonly string[]).includes(r.alert_type);
                        const aiEnabled = state.settings?.ai_per_type?.[r.alert_type] ?? true;
                        const aiBusy = busyKey === `__ai_${r.alert_type}__`;
                        const TypeIcon = TYPE_ICON[r.alert_type] ?? AlertTriangle;
                        return (
                        <Fragment key={r.alert_type}>
                        <tr className="border-b border-border/40 last:border-0">
                          <td className="px-3 py-2 align-top w-10">
                            <TypeIcon size={16} className="text-muted-foreground" />
                          </td>
                          <td className="px-3 py-2 align-top">
                            <p className="font-medium">{r.name}</p>
                            <p className="text-xs text-muted-foreground">{r.description}</p>
                            {!r.is_mapped && (
                              <p className="text-xs text-warn mt-1">
                                {t('forum_routing.topic_status_missing')}
                              </p>
                            )}
                            {r.is_mapped && !r.is_active && (
                              <p className="text-xs text-warn mt-1">
                                {t('forum_routing.topic_status_inactive')}
                              </p>
                            )}
                          </td>
                          {/* AI / Resolved / Routing are on/off SWITCHES —
                              one control grammar shared with role mode.
                              The column header names each; a switch can't
                              be mistaken for a status badge (UX audit). */}
                          <td className="px-3 py-2 align-top w-20 text-right whitespace-nowrap">
                            {/* Non-AI-capable rows show a muted dash:
                                "not applicable", not "missing". */}
                            {!isAICapable && r.is_mapped && (
                              <span className="text-xs text-muted-foreground/50">—</span>
                            )}
                            {isAICapable && r.is_mapped && (
                              <Switch
                                size="sm"
                                aria-label={`${r.name} — ${t('forum_routing.col_ai')}`}
                                checked={aiEnabled}
                                disabled={aiBusy || !canConfigureRouting}
                                onCheckedChange={(v) => handleToggleAIForType(r.alert_type, v)}
                              />
                            )}
                          </td>
                          {/* Group-side resolved-notification toggle.
                              Independent from each user's personal
                              alert_resolve_receipts DM preference (set
                              under Alerts bell → Notification preferences). */}
                          <td className="px-3 py-2 align-top w-20 text-right whitespace-nowrap">
                            {r.is_mapped && (
                              <Switch
                                size="sm"
                                aria-label={`${r.name} — ${t('forum_routing.col_resolved')}`}
                                checked={r.send_resolve_receipt}
                                disabled={busyKey === `__rr_${r.alert_type}__`}
                                onCheckedChange={(v) => handleToggleReceipt(r.alert_type, v)}
                              />
                            )}
                          </td>
                          <td className="px-3 py-2 align-top w-20 text-right whitespace-nowrap">
                            {r.is_mapped && (
                              <Switch
                                size="sm"
                                aria-label={`${r.name} — ${t('forum_routing.col_routing')}`}
                                checked={r.is_active}
                                disabled={busyKey === r.alert_type}
                                onCheckedChange={(v) => handleToggle(r.alert_type, v)}
                              />
                            )}
                          </td>
                        </tr>
                        {/* Sub-category chips — pick which kinds of this
                            type reach the group (all on by default). */}
                        {r.subtypes && r.is_mapped && r.is_active && (
                          <tr>
                            <td />
                            <td colSpan={4} className="px-3 pb-2">
                              <div className="ml-1 flex flex-wrap items-center gap-1">
                                <span className="text-2xs text-muted-foreground">
                                  {!r.subtypes.selected
                                    ? t('forum_routing.subtype_prefix_all')
                                    : t('forum_routing.subtype_prefix_some', {
                                        n: r.subtypes.selected.length,
                                        total: r.subtypes.all.length,
                                      })}
                                </span>
                                {r.subtypes.all.map((s) => {
                                  const active = !r.subtypes!.selected || r.subtypes!.selected.includes(s);
                                  return (
                                    <button
                                      key={s}
                                      type="button"
                                      disabled={busyKey === `__st_${r.alert_type}__` || !canConfigureRouting}
                                      onClick={() => { void toggleSubtype(r, s); }}
                                      className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md border text-2xs transition disabled:opacity-60 ${
                                        active
                                          ? 'border-primary/40 bg-primary/10 text-primary'
                                          : 'border-border text-muted-foreground hover:border-ring'
                                      }`}
                                    >
                                      {active && <Check size={12} />}
                                      {SUBTYPE_LABELS[s] || s}
                                    </button>
                                  );
                                })}
                              </div>
                            </td>
                          </tr>
                        )}
                        </Fragment>
                        );
                          })}
                        </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* ── Custom topics — user-defined threads in the forum ── */}
              <div>
                <div className="mb-1 inline-flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t('forum_routing.custom_topics_title')}
                  <InfoTip label={t('forum_routing.custom_topics_hint')} size={12} />
                </div>
                {!(state.custom_topics ?? []).length && (
                  <p className="mb-1 text-2xs text-muted-foreground">
                    {t('forum_routing.custom_topics_empty')}
                  </p>
                )}
                <div className="space-y-1">
                  {(state.custom_topics ?? []).map((tp) => {
                    const label = state.routes.find((r) => r.alert_type === tp.alert_type)?.name || tp.alert_type;
                    return (
                    <div key={tp.id} className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="w-40 shrink-0 text-foreground">{tp.name}</span>
                      <span className="text-muted-foreground">
                        {label}
                        {tp.subtypes.length > 0
                          ? `: ${tp.subtypes.map((s) => SUBTYPE_LABELS[s] || s).join(', ')}`
                          : ` · ${t('forum_routing.topic_all_kinds')}`}
                      </span>
                      {confirmTopic === tp.id ? (
                        <span className="inline-flex flex-wrap items-center gap-1.5">
                          <span className="text-2xs text-muted-foreground">
                            {t('forum_routing.confirm_topic_delete', { name: tp.name })}
                          </span>
                          <button type="button" onClick={() => { void deleteTopic(tp); }}
                            disabled={busyKey === '__ctopic__'}
                            className="text-xs font-medium text-destructive hover:underline disabled:opacity-50">
                            {busyKey === '__ctopic__' ? '…' : t('forum_routing.topic_remove')}
                          </button>
                          <button type="button" onClick={() => setConfirmTopic(null)}
                            className="text-xs text-muted-foreground hover:text-foreground">
                            {t('forum_routing.btn_cancel')}
                          </button>
                        </span>
                      ) : (
                        <button type="button" onClick={() => setConfirmTopic(tp.id)}
                          className="text-xs text-destructive hover:underline">
                          {t('forum_routing.topic_remove')}
                        </button>
                      )}
                    </div>
                    );
                  })}

                  {!showAddTopic ? (
                    <button type="button"
                      onClick={() => { setShowAddTopic(true); setCtName(''); setCtType(''); setCtSubs([]); }}
                      className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition">
                      {t('forum_routing.add_topic_btn')}
                    </button>
                  ) : (
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        value={ctName}
                        onChange={(e) => setCtName(e.target.value)}
                        placeholder={t('forum_routing.topic_name_ph')}
                        autoComplete="off" spellCheck={false}
                        className="w-40 bg-muted border border-border rounded px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring"
                      />
                      <Select value={ctType || undefined} onValueChange={(v) => { setCtType(v); setCtSubs([]); }}>
                        <SelectTrigger size="sm" aria-label={t('forum_routing.custom_topics_title')} className="text-xs">
                          <SelectValue placeholder={t('forum_routing.topic_type_ph')} />
                        </SelectTrigger>
                        <SelectContent>
                          {state.routes.filter((r) => r.is_mapped).map((r) => (
                            <SelectItem key={r.alert_type} value={r.alert_type} className="text-xs">{r.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {(() => {
                        const vocab = state.routes.find((r) => r.alert_type === ctType)?.subtypes?.all;
                        if (!vocab) return null;
                        return (
                          <>
                            {vocab.map((s) => {
                              const on = ctSubs.includes(s);
                              return (
                                <button key={s} type="button"
                                  onClick={() => setCtSubs(on ? ctSubs.filter((x) => x !== s) : [...ctSubs, s])}
                                  className={`px-2 py-0.5 rounded-md border text-2xs transition ${
                                    on ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:border-ring'
                                  }`}>
                                  {SUBTYPE_LABELS[s] || s}
                                </button>
                              );
                            })}
                            <span className="text-2xs text-muted-foreground">{t('forum_routing.topic_subs_hint')}</span>
                          </>
                        );
                      })()}
                      <button type="button" onClick={() => { void createTopic(); }}
                        disabled={busyKey === '__ctopic__' || !ctName.trim() || !ctType}
                        className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50">
                        {busyKey === '__ctopic__' ? '…' : t('forum_routing.topic_create')}
                      </button>
                      <button type="button" onClick={() => setShowAddTopic(false)}
                        className="text-xs text-muted-foreground hover:text-foreground">
                        {t('forum_routing.btn_cancel')}
                      </button>
                    </div>
                  )}
                </div>
              </div>

              <div>
                {!confirmDisconnect ? (
                  <button
                    onClick={() => setConfirmDisconnect(true)}
                    className="px-3 py-1.5 bg-destructive/10 hover:bg-destructive/20 text-destructive rounded text-xs font-medium transition"
                  >
                    {t('forum_routing.btn_disconnect')}
                  </button>
                ) : (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm text-muted-foreground">
                      {t('forum_routing.btn_disconnect_confirm')}
                    </span>
                    <button
                      onClick={handleDisconnect}
                      disabled={busyKey === '__disconnect__'}
                      className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 rounded text-xs font-medium text-destructive-foreground transition min-h-tap"
                    >
                      {t('forum_routing.btn_disconnect_yes')}
                    </button>
                    <button
                      onClick={() => setConfirmDisconnect(false)}
                      className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded text-xs font-medium transition"
                    >
                      {t('forum_routing.btn_cancel')}
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

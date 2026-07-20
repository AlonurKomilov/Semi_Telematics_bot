import { Fragment, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { FEATURE_GROUPS } from './AlertRoutingSection';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import {
  ChevronDown, ChevronRight, Check, X,
  // Per-alert-type row icons.  The backend still ships `icon_emoji`
  // (used inside Telegram message bodies, where emoji is data not
  // UI), but the dashboard renders these lucide equivalents so the
  // table reads as a professional admin tool.
  AlertTriangle, HeartPulse, Fuel, ShieldAlert, Camera, ParkingSquare,
  MapPin, BarChart3, Wrench, FileText, RefreshCw,
  // Button glyphs (replaced 🤖 and 🟢 emoji per design.md "no emoji
  // as UI icons" — section.7 / hard-rules).
  Sparkles, CheckCircle2, BellOff,
  type LucideIcon,
} from 'lucide-react';

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
  const { t } = useTranslation();
  const tz = useTimezone();
  const [state, setState] = useState<RoutingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [showMapping, setShowMapping] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await apiJSON<RoutingState>('/admin/forum-routing');
      setState(data);
    } catch {
      setState(null);
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

  const handleToggleAIForType = async (alertType: string, next: boolean) => {
    setBusyKey(`__ai_${alertType}__`);
    try {
      await apiJSON('/admin/forum-routing/settings', {
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

  return (
    <div className="border-t border-border mt-5 pt-5">
      <h3 className="text-sm font-semibold mb-2">{t('forum_routing.section_title')}</h3>
      <p className="text-xs text-muted-foreground mb-4">
        {t('forum_routing.section_desc')}
      </p>

      {loading && (
        <p className="text-sm text-muted-foreground">{t('forum_routing.loading')}</p>
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
                  {t('forum_routing.mode_group_setup_status', { status: state.setup_status })}
                  {state.last_setup_at && (
                    ' · ' + t('forum_routing.mode_group_last_setup', {
                      when: formatDate(state.last_setup_at, { timeZone: tz }),
                    })
                  )}
                </p>
                <p className="text-xs text-muted-foreground font-mono">
                  {t('forum_routing.mode_group_chat_id', { id: state.chat_id })}
                </p>
                <p className="text-xs text-muted-foreground mt-2">
                  {t('forum_routing.topics_mapped', {
                    mapped: state.routes.filter(r => r.is_mapped && r.is_active).length,
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
                  <table className="w-full text-sm">
                    <tbody>
                      {/* Grouped under the owning FEATURE — same
                          hierarchy as the role-mode expanders. */}
                      {FEATURE_GROUPS.map((g) => {
                        const groupRows = state.routes.filter((r) => g.types.includes(r.alert_type));
                        if (!groupRows.length) return null;
                        return (
                        <Fragment key={g.label}>
                          <tr className="bg-muted/40">
                            <td colSpan={5} className="px-3 py-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                              {g.label}
                            </td>
                          </tr>
                          {groupRows.map(r => {
                        const isAICapable = (AI_CAPABLE_TYPES as readonly string[]).includes(r.alert_type);
                        const aiEnabled = state.settings?.ai_per_type?.[r.alert_type] ?? true;
                        const aiBusy = busyKey === `__ai_${r.alert_type}__`;
                        const TypeIcon = TYPE_ICON[r.alert_type] ?? AlertTriangle;
                        return (
                        <tr key={r.alert_type} className="border-b border-border/40 last:border-0">
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
                          {/* AI Analysis toggle column — only for AI-capable types.
                              Non-AI types render an empty cell so the
                              Enable/Disable column stays aligned across rows. */}
                          <td className="px-3 py-2 align-top w-28 text-right whitespace-nowrap">
                            {isAICapable && r.is_mapped && (
                              <button
                                disabled={aiBusy}
                                onClick={() => handleToggleAIForType(r.alert_type, !aiEnabled)}
                                title={aiEnabled
                                  ? t('forum_routing.ai_status_on')
                                  : t('forum_routing.ai_status_off')}
                                className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition ${
                                  aiEnabled
                                    ? toneClasses('info')
                                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                                }`}
                              >
                                <Sparkles size={12} />
                                {aiEnabled ? t('forum_routing.ai_on') : t('forum_routing.ai_off')}
                              </button>
                            )}
                          </td>
                          {/* Group-side resolved-notification toggle.
                              Independent from each user's personal
                              alert_resolve_receipts DM preference (set
                              under Alerts bell → Notification preferences). */}
                          <td className="px-3 py-2 align-top w-40 text-right whitespace-nowrap">
                            {r.is_mapped && (
                              <button
                                disabled={busyKey === `__rr_${r.alert_type}__`}
                                onClick={() => handleToggleReceipt(r.alert_type, !r.send_resolve_receipt)}
                                title={r.send_resolve_receipt
                                  ? 'Group receives a RESOLVED message when this alert auto-clears'
                                  : 'Group does NOT receive a resolved-notification for this alert type'}
                                className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition ${
                                  r.send_resolve_receipt
                                    ? toneClasses('ok')
                                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                                }`}
                              >
                                {r.send_resolve_receipt
                                  ? <CheckCircle2 size={12} />
                                  : <BellOff size={12} />}
                                {r.send_resolve_receipt ? 'Resolved notif.' : 'No notif.'}
                              </button>
                            )}
                          </td>
                          <td className="px-3 py-2 align-top w-28 text-right">
                            {r.is_mapped && (
                              <button
                                disabled={busyKey === r.alert_type}
                                onClick={() => handleToggle(r.alert_type, !r.is_active)}
                                className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition ${
                                  r.is_active
                                    ? toneClasses('ok')
                                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                                }`}
                              >
                                {r.is_active ? <Check size={12} /> : <X size={12} />}
                                {r.is_active
                                  ? t('forum_routing.btn_toggle_disable')
                                  : t('forum_routing.btn_toggle_enable')}
                              </button>
                            )}
                          </td>
                        </tr>
                        );
                          })}
                        </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                  <p className="text-xs text-muted-foreground px-3 py-2 border-t border-border/40 bg-muted/20">
                    <strong className="font-medium text-foreground">AI</strong> = toggle AI Analysis inclusion for that topic.
                    <span className="mx-1">·</span>
                    <strong className="font-medium text-foreground">Resolved notif.</strong> = post a confirmation message to the group when an alert auto-resolves.
                    <span className="mx-1">·</span>
                    <strong className="font-medium text-foreground">Disable</strong> = stop routing this alert type to the group (per-user DMs still fire based on each user’s preferences).
                  </p>
                </div>
              )}

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
                      className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 rounded text-xs font-medium text-destructive-foreground transition"
                    >
                      {t('forum_routing.btn_disconnect_yes')}
                    </button>
                    <button
                      onClick={() => setConfirmDisconnect(false)}
                      className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded text-xs font-medium transition"
                    >
                      {t('forum_routing.btn_disconnect_cancel')}
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

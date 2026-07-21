import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { Check, ChevronDown, ChevronRight, Info } from 'lucide-react';
import { InfoTip } from '../../components/tooltip';

// The Telegram Bot card's body controller.  The Routing selector sits
// at the TOP of the card (it's the bot-topology decision, not an
// alert sub-setting — the owner's hierarchy call):
//
//   Routing: [Single bot] [Sub bot per role]
//     Single bot       → the classic header + forum-with-topics panel
//                        (passed in as ``singleBody`` from Settings)
//     Sub bot per role → the bot ROSTER: Main row first (identity +
//                        Owner & Admins group + fallback sender), then
//                        one row per role: name · group (setup step 1)
//                        · sender bot (right) · ▸ alerts for this group
//
// A role MANAGER sees this same card (route opened via
// can_manage_role_bot) — the server's ``manageable`` list drives which
// rows/toggles are editable; everything else renders read-only.

interface PersonaBinding {
  chat_id: number;
  chat_title: string;
  is_active: boolean;
}

interface AlertRoutingResponse {
  mode: 'single_group' | 'per_persona_groups';
  personas: Record<string, PersonaBinding | null>;
  vehicle_count: number;
  nudge_threshold: number;
  bot_configured: boolean;
}

interface SubBotRow {
  persona: string;
  bot_username: string;
  is_running: boolean;
}

interface SubBotsResponse {
  personas: Record<string, SubBotRow | null>;
  manageable: string[];
}

interface TopicRow {
  alert_type: string;
  enabled: boolean;
  ai: boolean;
  // Present only for types with a sub-category vocabulary (events).
  // selected === null ⇒ ALL sub-categories (the default).
  subtypes?: { all: string[]; selected: string[] | null };
}

interface PersonaTopicsResponse {
  personas: Record<string, TopicRow[]>;
  manageable: string[];
}

interface CustomTopic {
  id: number;
  name: string;
  alert_type: string;
  subtypes: string[];
  has_thread: boolean;
}

interface CustomTopicsResponse {
  personas: Record<string, CustomTopic[]>;
}

export interface BotConfigLite {
  has_bot: boolean;
  bot_username: string;
  first_name?: string;
  is_running?: boolean;
}

// Operational roles; the owner_admin aggregate renders as the Main row.
const ROLE_ORDER = ['dispatcher', 'safety', 'fleet', 'hr', 'accounting', 'recruiter'] as const;

// Display names for the canonical alert types — same English catalog
// the single-forum panel shows (its names come from the backend spec).
const TYPE_LABELS: Record<string, string> = {
  faults: 'Faults', health: 'Health', fuel: 'Fuel', events: 'Safety Events',
  camera: 'Cameras', parking: 'Parking', geofence: 'Geofences',
  scorecard: 'Scorecards', maintenance: 'Maintenance',
  documents: 'Driver Documents', system: 'Sync & System',
};

// Feature hierarchy — topics render grouped under their owning FEATURE
// (mirrors the product taxonomy: Vehicle telemetry, Safety, …), not as
// one flat list.  Exported so the single-mode panel groups identically.
export const FEATURE_GROUPS: { label: string; types: string[] }[] = [
  { label: 'Vehicle', types: ['faults', 'health', 'fuel'] },
  { label: 'Safety', types: ['events', 'camera', 'parking'] },
  { label: 'Geofences', types: ['geofence'] },
  { label: 'Scorecards', types: ['scorecard'] },
  { label: 'Maintenance', types: ['maintenance'] },
  { label: 'Drivers', types: ['documents'] },
  { label: 'System', types: ['system'] },
];

// Sub-category display names — mirrors the events formatting SSOT
// (capabilities/formatting/events.py keys).
const SUBTYPE_LABELS: Record<string, string> = {
  crash: 'Crash', braking: 'Harsh Braking', acceleration: 'Harsh Acceleration',
  harshTurn: 'Harsh Turn', rollingStop: 'Rolling Stop',
  followingDistance: 'Following Distance', laneDeparture: 'Lane Departure',
};

export default function AlertRoutingSection({
  botConfig,
  canManageAccount,
  singleBody,
}: {
  botConfig: BotConfigLite;
  canManageAccount: boolean;
  singleBody: ReactNode;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<AlertRoutingResponse | null>(null);
  const [subBots, setSubBots] = useState<SubBotsResponse | null>(null);
  const [topics, setTopics] = useState<PersonaTopicsResponse | null>(null);
  const [chatInputs, setChatInputs] = useState<Record<string, string>>({});
  const [tokenInputs, setTokenInputs] = useState<Record<string, string>>({});
  const [customTopics, setCustomTopics] = useState<CustomTopicsResponse | null>(null);
  // Add-topic mini-form (one open at a time, keyed by persona)
  const [ctName, setCtName] = useState('');
  const [ctType, setCtType] = useState('');
  const [ctSubs, setCtSubs] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string>('');
  // Which row's group-bind / sub-bot-attach input is open (on-demand
  // inputs keep the roster scannable AND dodge browser autofill — a
  // permanently-rendered bare input attracted saved emails/passwords,
  // seen live on the owner's account).
  const [openInput, setOpenInput] = useState<string>('');

  const load = useCallback(() => {
    apiJSON<AlertRoutingResponse>('/admin/alert-routing').then(setData).catch(() => setData(null));
    apiJSON<SubBotsResponse>('/admin/bot-instances').then(setSubBots).catch(() => setSubBots(null));
    apiJSON<PersonaTopicsResponse>('/admin/alert-routing/persona-topics')
      .then(setTopics).catch(() => setTopics(null));
    apiJSON<CustomTopicsResponse>('/admin/alert-routing/custom-topics')
      .then(setCustomTopics).catch(() => setCustomTopics(null));
  }, []);
  useEffect(load, [load]);

  if (!data) return <>{singleBody}</>;

  const manageable = subBots?.manageable ?? [];
  const canManage = (persona: string) =>
    canManageAccount || manageable.includes(persona);

  const setMode = async (mode: AlertRoutingResponse['mode']) => {
    if (mode === data.mode || busy || !canManageAccount) return;
    setBusy('mode');
    try {
      await apiJSON('/admin/alert-routing', { method: 'PUT', body: { mode } });
      setData({ ...data, mode });
      toast.success(t('alert_routing.toast_mode_saved'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const bind = async (persona: string) => {
    const raw = (chatInputs[persona] || '').trim();
    if (!raw || busy) return;
    setBusy(persona);
    try {
      const res = await apiJSON<{ chat_id: number; chat_title: string }>(
        '/admin/alert-routing/persona-groups',
        { method: 'POST', body: { persona, chat_id: Number(raw) } },
      );
      setData({
        ...data,
        personas: {
          ...data.personas,
          [persona]: { chat_id: res.chat_id, chat_title: res.chat_title, is_active: true },
        },
      });
      setChatInputs({ ...chatInputs, [persona]: '' });
      toast.success(t('alert_routing.toast_bound', { title: res.chat_title || res.chat_id }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const unbind = async (persona: string) => {
    if (busy) return;
    // Named stakes: unbinding silently stops this role's group posts
    // (no legacy-forum fallback by design), so the confirm says what
    // actually changes before the click lands.
    const msg = persona === 'owner_admin'
      ? t('alert_routing.confirm_unbind_main')
      : t('alert_routing.confirm_unbind', { role: t(`alert_routing.persona_${persona}`) });
    if (!confirm(msg)) return;
    setBusy(persona);
    try {
      await apiJSON(`/admin/alert-routing/persona-groups/${persona}`, { method: 'DELETE' });
      setData({ ...data, personas: { ...data.personas, [persona]: null } });
      toast.success(t('alert_routing.toast_unbound'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const attachSubBot = async (persona: string) => {
    const token = (tokenInputs[persona] || '').trim();
    if (!token || busy) return;
    setBusy(`sub-${persona}`);
    try {
      const res = await apiJSON<{ bot_username: string }>('/admin/bot-instances', {
        method: 'POST', body: { persona, bot_token: token },
      });
      setTokenInputs({ ...tokenInputs, [persona]: '' });
      setSubBots(subBots && {
        ...subBots,
        personas: {
          ...subBots.personas,
          [persona]: { persona, bot_username: res.bot_username, is_running: false },
        },
      });
      toast.success(t('alert_routing.toast_subbot_attached', { username: res.bot_username }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const detachSubBot = async (persona: string) => {
    if (busy) return;
    const sub = subBots?.personas?.[persona];
    if (!confirm(t('alert_routing.confirm_detach', { username: sub?.bot_username ?? '' }))) return;
    setBusy(`sub-${persona}`);
    try {
      await apiJSON(`/admin/bot-instances/${persona}`, { method: 'DELETE' });
      setSubBots(subBots && {
        ...subBots,
        personas: { ...subBots.personas, [persona]: null },
      });
      toast.success(t('alert_routing.toast_subbot_detached'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const toggleTopic = async (persona: string, alert_type: string, field: 'enabled' | 'ai', value: boolean) => {
    if (busy) return;
    setBusy(`topic-${alert_type}-${field}`);
    try {
      await apiJSON(`/admin/alert-routing/persona-topics/${persona}/${alert_type}`, {
        method: 'PUT', body: { field, value },
      });
      setTopics(topics && {
        ...topics,
        personas: {
          ...topics.personas,
          [persona]: (topics.personas[persona] || []).map((r) =>
            r.alert_type === alert_type ? { ...r, [field]: value } : r,
          ),
        },
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const toggleSubtype = async (persona: string, row: TopicRow, sub: string) => {
    if (busy || !row.subtypes) return;
    const current = row.subtypes.selected ?? row.subtypes.all;
    const active = current.includes(sub);
    const next = active ? current.filter((s) => s !== sub) : [...current, sub];
    if (!next.length) {
      toast.error(t('alert_routing.subtype_keep_one'));
      return;
    }
    setBusy(`subtype-${persona}-${row.alert_type}`);
    try {
      const res = await apiJSON<{ selected: string[] | null }>(
        `/admin/alert-routing/persona-topics/${persona}/${row.alert_type}/subtypes`,
        { method: 'PUT', body: { selected: next } },
      );
      setTopics(topics && {
        ...topics,
        personas: {
          ...topics.personas,
          [persona]: (topics.personas[persona] || []).map((r) =>
            r.alert_type === row.alert_type && r.subtypes
              ? { ...r, subtypes: { ...r.subtypes, selected: res.selected } }
              : r,
          ),
        },
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const createCustomTopic = async (persona: string) => {
    const name = ctName.trim();
    if (!name || !ctType || busy) return;
    setBusy(`ctopic-${persona}`);
    try {
      const res = await apiJSON<CustomTopic>('/admin/alert-routing/custom-topics', {
        method: 'POST',
        body: { persona, name, alert_type: ctType, subtypes: ctSubs },
      });
      setCustomTopics((prev) => ({
        personas: {
          ...(prev?.personas ?? {}),
          [persona]: [...(prev?.personas?.[persona] ?? []), res],
        },
      }));
      setCtName(''); setCtType(''); setCtSubs([]); setOpenInput('');
      toast.success(res.has_thread
        ? t('alert_routing.toast_topic_created_thread', { name: res.name })
        : t('alert_routing.toast_topic_created_flat', { name: res.name }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const deleteCustomTopic = async (persona: string, topic: CustomTopic) => {
    if (busy) return;
    if (!confirm(t('alert_routing.confirm_topic_delete', { name: topic.name }))) return;
    setBusy(`ctopic-${persona}`);
    try {
      await apiJSON(`/admin/alert-routing/custom-topics/${topic.id}`, { method: 'DELETE' });
      setCustomTopics((prev) => prev && ({
        personas: {
          ...prev.personas,
          [persona]: (prev.personas[persona] ?? []).filter((x) => x.id !== topic.id),
        },
      }));
      toast.success(t('alert_routing.toast_topic_deleted'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const showNudge =
    canManageAccount && data.mode === 'single_group'
    && data.vehicle_count > data.nudge_threshold;

  const modeOption = (
    mode: AlertRoutingResponse['mode'],
    title: string,
    desc: string,
  ) => {
    const selected = data.mode === mode;
    return (
      <button
        type="button"
        onClick={() => { void setMode(mode); }}
        disabled={busy === 'mode' || !canManageAccount}
        className={`flex-1 text-left border rounded-lg px-3 py-2 transition ${
          selected ? 'border-primary bg-primary/5' : 'border-border hover:border-ring'
        } ${!canManageAccount ? 'opacity-70 cursor-default' : ''}`}
      >
        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
          {selected && <Check size={14} className="text-primary shrink-0" />}
          {title}
        </span>
        <span className="block text-xs text-muted-foreground mt-0.5">{desc}</span>
      </button>
    );
  };

  const groupCell = (persona: string) => {
    const bound = data.personas[persona];
    const editable = canManage(persona);
    const isMain = persona === 'owner_admin';
    if (bound) {
      return (
        <>
          {/* Status chips render FLAT (no border) — border + hover is
              reserved for actions so state vs click-me reads at a
              glance (component-grammar rule from the UX audit). */}
          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs ${toneClasses('ok')}`}>
            <Check size={12} />
            {bound.chat_title || bound.chat_id}
          </span>
          {editable && (
            <button
              type="button"
              onClick={() => { void unbind(persona); }}
              disabled={busy === persona}
              className="text-xs text-destructive hover:underline disabled:opacity-50"
            >
              {t('alert_routing.unbind')}
            </button>
          )}
        </>
      );
    }
    // Unbound: the Main row's meaning differs — ITS group is the
    // critical cross-post destination, not a fallback consumer.
    const fallbackText = isMain
      ? t('alert_routing.main_group_unbound')
      : t('alert_routing.bound_fallback');
    if (!editable) {
      return (
        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          {t('alert_routing.no_group_chip')} <InfoTip label={fallbackText} size={12} />
        </span>
      );
    }
    if (openInput !== `group-${persona}`) {
      return (
        <>
          <button
            type="button"
            onClick={() => setOpenInput(`group-${persona}`)}
            className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
          >
            {t('alert_routing.bind_group_btn')}
          </button>
          <InfoTip label={`${fallbackText} ${t('alert_routing.hint_chatid')}`} size={12} />
        </>
      );
    }
    return (
      <>
        {/* Visible setup hint — binding is a one-time task; the design
            rule keeps one-time-form help visible, not behind a tip. */}
        <span className="text-2xs text-muted-foreground whitespace-nowrap">
          {t('alert_routing.hint_chatid_short')}
        </span>
        <input
          value={chatInputs[persona] || ''}
          onChange={(e) => setChatInputs({ ...chatInputs, [persona]: e.target.value })}
          placeholder={t('alert_routing.chat_id_ph')}
          autoComplete="off"
          inputMode="numeric"
          spellCheck={false}
          className="w-40 bg-muted border border-border rounded px-2 py-1 text-xs text-foreground font-mono focus:outline-none focus:border-ring"
        />
        <button
          type="button"
          onClick={() => { void bind(persona).then(() => setOpenInput('')); }}
          disabled={busy === persona || !(chatInputs[persona] || '').trim()}
          className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50"
        >
          {busy === persona ? '…' : t('alert_routing.bind')}
        </button>
        <button
          type="button"
          onClick={() => setOpenInput('')}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          {t('alert_routing.cancel')}
        </button>
      </>
    );
  };

  const topicsExpander = (persona: string) => {
    const rows = topics?.personas?.[persona] ?? [];
    if (!rows.length) return null;
    const open = !!expanded[persona];
    const editable = canManage(persona);
    return (
      <div className="w-full">
        <button
          type="button"
          onClick={() => setExpanded({ ...expanded, [persona]: !open })}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {t('alert_routing.topics_expander')}
        </button>
        {open && (
          <div className="mt-1.5 ml-4 space-y-2">
            {/* Column headers once — the per-row repeated checkbox
                labels were a scanning wall; a matrix reads by column. */}
            <div className="flex items-center gap-3 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
              <span className="w-32 shrink-0" />
              <span className="w-16 inline-flex items-center justify-center gap-0.5">
                {t('alert_routing.col_route')}
                <InfoTip label={t('alert_routing.topic_route_note')} size={12} />
              </span>
              <span className="w-16 text-center">{t('alert_routing.col_ai')}</span>
            </div>
            {FEATURE_GROUPS
              .map((g) => ({ ...g, rows: rows.filter((r) => g.types.includes(r.alert_type)) }))
              .filter((g) => g.rows.length > 0)
              .map((g) => (
                <div key={g.label}>
                  {/* Feature heading — the topic hierarchy mirrors the
                      product taxonomy instead of one flat list. */}
                  <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                    {g.label}
                  </div>
                  <div className="space-y-1">
                    {g.rows.map((r) => (
                      <div key={r.alert_type}>
                        <div className="flex items-center gap-3 text-xs">
                          <span className="w-32 shrink-0 text-foreground">
                            {TYPE_LABELS[r.alert_type] || r.alert_type}
                          </span>
                          {/* label-wrapped so the whole cell stays a
                              click target, not just the checkbox box */}
                          <label className={`w-16 text-center ${editable ? 'cursor-pointer' : 'opacity-70'}`}>
                            <input
                              type="checkbox"
                              aria-label={`${TYPE_LABELS[r.alert_type] || r.alert_type} — ${t('alert_routing.topic_route')}`}
                              checked={r.enabled}
                              disabled={!editable || busy === `topic-${r.alert_type}-enabled`}
                              onChange={(e) => { void toggleTopic(persona, r.alert_type, 'enabled', e.target.checked); }}
                            />
                          </label>
                          <label className={`w-16 text-center ${editable ? 'cursor-pointer' : 'opacity-70'}`}>
                            <input
                              type="checkbox"
                              aria-label={`${TYPE_LABELS[r.alert_type] || r.alert_type} — ${t('alert_routing.topic_ai')}`}
                              checked={r.ai}
                              disabled={!editable || busy === `topic-${r.alert_type}-ai`}
                              onChange={(e) => { void toggleTopic(persona, r.alert_type, 'ai', e.target.checked); }}
                            />
                          </label>
                        </div>
                        {/* Sub-category chips — pick exactly which kinds
                            this role's group receives (all on by default). */}
                        {r.subtypes && r.enabled && (
                          <div className="mt-1 ml-4 flex flex-wrap items-center gap-1">
                            {r.subtypes.all.map((s) => {
                              const active = !r.subtypes!.selected || r.subtypes!.selected.includes(s);
                              return (
                                <button
                                  key={s}
                                  type="button"
                                  disabled={!editable || busy === `subtype-${persona}-${r.alert_type}`}
                                  onClick={() => { void toggleSubtype(persona, r, s); }}
                                  className={`px-2 py-0.5 rounded-md border text-2xs transition disabled:opacity-60 ${
                                    active
                                      ? 'border-primary/40 bg-primary/10 text-primary'
                                      : 'border-border text-muted-foreground hover:border-ring'
                                  }`}
                                >
                                  {SUBTYPE_LABELS[s] || s}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}

            {/* ── Custom topics — user-defined rules for THIS role ── */}
            <div>
              <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('alert_routing.custom_topics_title')}
                <InfoTip label={t('alert_routing.custom_topics_hint')} size={12} />
              </div>
              <div className="space-y-1">
                {(customTopics?.personas?.[persona] ?? []).map((tp) => (
                  <div key={tp.id} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="w-32 shrink-0 text-foreground">{tp.name}</span>
                    <span className="text-muted-foreground">
                      {TYPE_LABELS[tp.alert_type] || tp.alert_type}
                      {tp.subtypes.length > 0
                        ? `: ${tp.subtypes.map((s) => SUBTYPE_LABELS[s] || s).join(', ')}`
                        : ` · ${t('alert_routing.topic_all_subtypes')}`}
                    </span>
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded-md text-2xs ${
                      tp.has_thread ? toneClasses('info') : toneClasses('neutral')
                    }`}>
                      {tp.has_thread ? t('alert_routing.topic_thread') : t('alert_routing.topic_flat')}
                    </span>
                    {editable && (
                      <button
                        type="button"
                        onClick={() => { void deleteCustomTopic(persona, tp); }}
                        disabled={busy === `ctopic-${persona}`}
                        className="text-xs text-destructive hover:underline disabled:opacity-50"
                      >
                        {t('alert_routing.remove')}
                      </button>
                    )}
                  </div>
                ))}
                {editable && openInput !== `ctopic-${persona}` && (
                  <button
                    type="button"
                    onClick={() => { setOpenInput(`ctopic-${persona}`); setCtName(''); setCtType(''); setCtSubs([]); }}
                    className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
                  >
                    {t('alert_routing.add_topic_btn')}
                  </button>
                )}
                {editable && openInput === `ctopic-${persona}` && (
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      value={ctName}
                      onChange={(e) => setCtName(e.target.value)}
                      placeholder={t('alert_routing.topic_name_ph')}
                      autoComplete="off"
                      spellCheck={false}
                      className="w-40 bg-muted border border-border rounded px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring"
                    />
                    <select
                      value={ctType}
                      onChange={(e) => { setCtType(e.target.value); setCtSubs([]); }}
                      aria-label={t('alert_routing.custom_topics_title')}
                      className="bg-muted border border-border rounded px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring"
                    >
                      <option value="">{t('alert_routing.topic_type_ph')}</option>
                      {rows.map((r) => (
                        <option key={r.alert_type} value={r.alert_type}>
                          {TYPE_LABELS[r.alert_type] || r.alert_type}
                        </option>
                      ))}
                    </select>
                    {(() => {
                      const vocab = rows.find((r) => r.alert_type === ctType)?.subtypes?.all;
                      if (!vocab) return null;
                      const chips = vocab.map((s) => {
                        const on = ctSubs.includes(s);
                        return (
                          <button
                            key={s}
                            type="button"
                            onClick={() => setCtSubs(on ? ctSubs.filter((x) => x !== s) : [...ctSubs, s])}
                            className={`px-2 py-0.5 rounded-md border text-2xs transition ${
                              on
                                ? 'border-primary/40 bg-primary/10 text-primary'
                                : 'border-border text-muted-foreground hover:border-ring'
                            }`}
                          >
                            {SUBTYPE_LABELS[s] || s}
                          </button>
                        );
                      });
                      return (
                        <>
                          {chips}
                          <span className="text-2xs text-muted-foreground">
                            {t('alert_routing.topic_subs_hint')}
                          </span>
                        </>
                      );
                    })()}
                    <button
                      type="button"
                      onClick={() => { void createCustomTopic(persona); }}
                      disabled={busy === `ctopic-${persona}` || !ctName.trim() || !ctType}
                      className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50"
                    >
                      {busy === `ctopic-${persona}` ? '…' : t('alert_routing.topic_create')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setOpenInput('')}
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      {t('alert_routing.cancel')}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };

  const roleMode = data.mode === 'per_persona_groups';

  // Destinations = the Main (owner_admin) group + every permission-
  // visible role.  Counting Main keeps the progress honest AND above
  // a demotivating flat zero once anything at all is bound.
  const visibleRoles = ROLE_ORDER.filter((p) => (topics?.personas?.[p]?.length ?? 0) > 0);
  const destinations = ['owner_admin', ...(visibleRoles.length ? visibleRoles : [...ROLE_ORDER])];
  const boundCount = destinations.filter((p) => data.personas[p]).length;

  return (
    <div>
      {/* Routing — the topology decision, top of the card. */}
      <div className="mb-3">
        <div className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
          {t('alert_routing.routing_label')}
        </div>
        {showNudge && (
          <div className={`mb-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${toneClasses('info')}`}>
            <Info size={14} className="mt-0.5 shrink-0" />
            <span>{t('alert_routing.nudge', { count: data.vehicle_count })}</span>
          </div>
        )}
        <div className="flex flex-col sm:flex-row gap-2">
          {modeOption('single_group',
            t('alert_routing.mode_single_title'), t('alert_routing.mode_single_desc'))}
          {modeOption('per_persona_groups',
            t('alert_routing.mode_multi_title'), t('alert_routing.mode_multi_desc'))}
        </div>
        {/* The switch rebuilds the card body — say up front that it's
            reversible so exploring the other mode feels safe. */}
        {canManageAccount && (
          <p className="mt-1.5 text-2xs text-muted-foreground">
            {t('alert_routing.mode_switch_safe')}
          </p>
        )}
      </div>

      {!roleMode ? (
        singleBody
      ) : (
        <div className="space-y-3">
          {/* Completion cue — how much of the roster is set up. */}
          <p className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            {t('alert_routing.roster_progress', { n: boundCount, total: destinations.length })}
            <InfoTip label={`${t('alert_routing.subbot_hint')} ${t('alert_routing.fallback_note')}`} size={12} />
          </p>

          {/* First-run path — VISIBLE until the first group is bound
              (one-time setup help stays out from behind tips).  Step 1
              is pre-checked by the bot connection itself. */}
          {boundCount === 0 && (
            <div className="rounded-lg border border-border px-3 py-2 space-y-1 text-xs">
              <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('alert_routing.setup_title')}
              </p>
              <p className="flex items-center gap-1.5 text-muted-foreground">
                <Check size={12} className="text-ok shrink-0" />
                {t('alert_routing.setup_step_bot', { username: botConfig.bot_username })}
              </p>
              <p className="text-foreground">{t('alert_routing.setup_step_group')}</p>
              <p className="text-foreground">{t('alert_routing.setup_step_chatid')}</p>
              <p className="text-foreground">{t('alert_routing.setup_step_bind')}</p>
            </div>
          )}

          {/* Main row — the identity bot.  Same spot the single-mode
              header occupies; label states its three jobs. */}
          <div className="border border-border rounded-lg px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-foreground">{t('alert_routing.main_row_label')}</span>
              {/* GROUP cell right after the name — the destination is
                  setup step 1 on every row; the sender bot is metadata
                  on the right.  One grammar for Main and role rows. */}
              <span className="inline-flex items-center gap-2">
                {groupCell('owner_admin')}
              </span>
              <span className="inline-flex items-center gap-2 ml-auto">
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs ${
                  botConfig.is_running !== false ? toneClasses('ok') : toneClasses('warn')
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${botConfig.is_running !== false ? 'bg-ok animate-pulse' : 'bg-warn'}`} />
                  {botConfig.is_running !== false ? t('alert_routing.running') : t('alert_routing.configured')}
                </span>
                <a
                  href={`https://t.me/${botConfig.bot_username}`}
                  target="_blank" rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  @{botConfig.bot_username}
                </a>
              </span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('alert_routing.main_row_caption')}
            </p>
            {topicsExpander('owner_admin')}
          </div>

          {/* Role rows — reading order mirrors the setup order: the
              role, its GROUP (step 1), then its optional Sub bot
              (step 2), then topics. */}
          {ROLE_ORDER.map((persona) => {
            const sub = subBots?.personas?.[persona] ?? null;
            const editable = canManage(persona);
            // The Permissions matrix is the SSOT: a role with no
            // alert-type features granted has nothing to route, so its
            // row doesn't render (unless something is already bound).
            const roleTopics = topics?.personas?.[persona] ?? [];
            if (!roleTopics.length && !data.personas[persona] && !sub) {
              return null;
            }
            return (
              <div key={persona} className="border border-border rounded-lg px-3 py-2">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium text-foreground w-20 shrink-0">
                    {t(`alert_routing.persona_${persona}`)}
                  </span>

                  {/* GROUP cell first — binding the group IS setup
                      step 1; the visual order now matches it. */}
                  <span className="inline-flex items-center gap-2">
                    {groupCell(persona)}
                  </span>

                  {/* BOT cell — who SENDS to this group; metadata on
                      the right, same slot as the Main row's identity. */}
                  <span className="inline-flex items-center gap-2 ml-auto">
                    {sub ? (
                      <>
                        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs ${
                          sub.is_running ? toneClasses('ok') : toneClasses('neutral')
                        }`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${sub.is_running ? 'bg-ok' : 'bg-muted-foreground'}`} />
                          @{sub.bot_username}
                        </span>
                        {editable && (
                          <button
                            type="button"
                            onClick={() => { void detachSubBot(persona); }}
                            disabled={busy === `sub-${persona}`}
                            className="text-xs text-destructive hover:underline disabled:opacity-50"
                          >
                            {t('alert_routing.subbot_detach')}
                          </button>
                        )}
                      </>
                    ) : !editable ? (
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs ${toneClasses('neutral')}`}>
                        {t('alert_routing.main_sends')}
                      </span>
                    ) : openInput !== `token-${persona}` ? (
                      <>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs ${toneClasses('neutral')}`}>
                          {t('alert_routing.main_sends')}
                        </span>
                        <button
                          type="button"
                          onClick={() => setOpenInput(`token-${persona}`)}
                          className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
                        >
                          {t('alert_routing.subbot_attach')}
                        </button>
                      </>
                    ) : (
                      <>
                        <input
                          type="text"
                          value={tokenInputs[persona] || ''}
                          onChange={(e) => setTokenInputs({ ...tokenInputs, [persona]: e.target.value })}
                          placeholder={t('alert_routing.subbot_token_ph')}
                          autoComplete="off"
                          spellCheck={false}
                          className="w-48 bg-muted border border-border rounded px-2 py-1 text-xs text-foreground font-mono focus:outline-none focus:border-ring"
                        />
                        <button
                          type="button"
                          onClick={() => { void attachSubBot(persona).then(() => setOpenInput('')); }}
                          disabled={busy === `sub-${persona}` || (tokenInputs[persona] || '').trim().length < 30}
                          className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50"
                        >
                          {busy === `sub-${persona}` ? '…' : t('alert_routing.subbot_attach')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setOpenInput('')}
                          className="text-xs text-muted-foreground hover:text-foreground"
                        >
                          {t('alert_routing.cancel')}
                        </button>
                      </>
                    )}
                  </span>
                </div>
                {topicsExpander(persona)}
              </div>
            );
          })}


        </div>
      )}
    </div>
  );
}

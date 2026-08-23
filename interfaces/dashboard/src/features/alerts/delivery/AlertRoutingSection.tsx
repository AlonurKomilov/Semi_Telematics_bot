import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../../api/client';
import { toneClasses } from '../../../lib/status';
import { Link } from 'react-router-dom';
import { AlertTriangle, Check, ChevronDown, ChevronRight, Info } from 'lucide-react';
import { InfoTip, Tip } from '../../../components/tooltip';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../../components/ui/select';
import { Switch } from '../../../components/ui/switch';
import { ErrorState } from '../../../components/shell';
import { useRoleView } from '../../../context/RoleViewContext';
import { ROLE_ORDER, TYPE_LABELS, FEATURE_GROUPS, SUBTYPE_LABELS } from './alertRoutingConstants';

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
  // Delivery health — stamped by the backend when a group post fails
  // (per-persona failures never auto-disable; a human must fix the
  // group), cleared by the next successful post.
  last_error?: string;
  last_error_at?: string;
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
  const { isPreviewing, activeView } = useRoleView();
  const [data, setData] = useState<AlertRoutingResponse | null>(null);
  const [subBots, setSubBots] = useState<SubBotsResponse | null>(null);
  const [topics, setTopics] = useState<PersonaTopicsResponse | null>(null);
  const [chatInputs, setChatInputs] = useState<Record<string, string>>({});
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
  // The mode-driving fetch has its own loading/error state: the card
  // CANNOT know which mode to render until /alert-routing resolves, so
  // failing to it must be an honest error — not a silent fall-through
  // to the single-mode body (which mislabels a Sub-bots account).
  const [loading, setLoading] = useState(true);
  const [dataError, setDataError] = useState(false);
  // Topics drive per-role row visibility; a failed fetch would hide
  // unbound roles and read as "empty by permission" — track it so the
  // roster can say so instead of lying.
  const [topicsError, setTopicsError] = useState(false);
  // Inline confirm target (replaces window.confirm — the settings
  // feature's established rule; matches the card's other two-step
  // confirms).  One at a time: "unbind-<p>" | "detach-<p>" | "rm-<id>".
  const [confirming, setConfirming] = useState<string>('');

  const load = useCallback(() => {
    setLoading(true); setDataError(false); setTopicsError(false);
    apiJSON<AlertRoutingResponse>('/admin/alert-routing')
      .then(setData).catch(() => setDataError(true)).finally(() => setLoading(false));
    // topicsError doubles as the "secondary routing details" flag: ANY
    // of the three supporting fetches failing shows the one retry banner
    // (a silent Sub-bots failure would even blank a manager's roster,
    // since `manageable` drives row visibility).
    apiJSON<SubBotsResponse>('/admin/bot-instances')
      .then(setSubBots).catch(() => { setSubBots(null); setTopicsError(true); });
    apiJSON<PersonaTopicsResponse>('/admin/alert-routing/persona-topics')
      .then(setTopics).catch(() => setTopicsError(true));
    apiJSON<CustomTopicsResponse>('/admin/alert-routing/custom-topics')
      .then(setCustomTopics).catch(() => { setCustomTopics(null); setTopicsError(true); });
  }, []);
  useEffect(load, [load]);

  if (!data) {
    if (dataError) return <ErrorState message={t('alert_routing.load_error')} onRetry={load} />;
    if (loading) return <p className="py-3 text-sm text-muted-foreground">{t('common.loading')}</p>;
    return <>{singleBody}</>;
  }

  const manageable = subBots?.manageable ?? [];
  // View-As faithfulness: the server's `manageable` reflects the REAL
  // caller (owner → every persona), so while previewing a persona
  // without account rights only that persona's row is editable — what
  // the real manager gets.  (canManageAccount is already view-aware.)
  const canManage = (persona: string) =>
    (canManageAccount || manageable.includes(persona))
    && (!isPreviewing || canManageAccount || persona === activeView);

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
    // Keep the confirm cluster mounted through the request (its Yes
    // button shows the in-place spinner); clear it in finally.
    setBusy(persona);
    try {
      await apiJSON(`/admin/alert-routing/persona-groups/${persona}`, { method: 'DELETE' });
      setData({ ...data, personas: { ...data.personas, [persona]: null } });
      toast.success(t('alert_routing.toast_unbound'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy(''); setConfirming('');
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
      setBusy(''); setConfirming('');
    }
  };

  // Inline two-step confirm — replaces window.confirm() so every
  // destructive action on the card shares ONE themed grammar (the
  // settings feature bans the native dialog; the card's Disconnect Bot
  // / Unlink group already confirm inline).  Shows the stakes → Yes/Cancel.
  const confirmCluster = (
    prompt: string,
    yesLabel: string,
    onYes: () => void,
    busyKey: string,
  ) => (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="text-2xs text-muted-foreground">{prompt}</span>
      <button
        type="button"
        onClick={onYes}
        disabled={busy === busyKey}
        className="text-xs font-medium text-destructive hover:underline disabled:opacity-50 py-1 -my-1 min-h-tap"
      >
        {busy === busyKey ? '…' : yesLabel}
      </button>
      <button
        type="button"
        onClick={() => setConfirming('')}
        className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
      >
        {t('alert_routing.cancel')}
      </button>
    </span>
  );

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
          {bound.last_error && (
            /* Delivery-health warning: posts to this group are failing
               (bot kicked / group deleted / lost admin) and per-persona
               routing never self-disables — surface it instead of the
               silent-forever failure the audit flagged. */
            <Tip label={bound.last_error}>
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs ${toneClasses('warn')}`}>
                <AlertTriangle size={12} />
                {t('alert_routing.delivery_failing')}
              </span>
            </Tip>
          )}
          {editable && (
            confirming === `unbind-${persona}` ? (
              confirmCluster(
                isMain
                  ? t('alert_routing.confirm_unbind_main')
                  : t('alert_routing.confirm_unbind', { role: t(`alert_routing.persona_${persona}`) }),
                t('alert_routing.unbind'),
                () => { void unbind(persona); },
                persona,
              )
            ) : (
              <button
                type="button"
                onClick={() => setConfirming(`unbind-${persona}`)}
                disabled={busy === persona}
                className="text-xs text-destructive hover:underline disabled:opacity-50 py-1 -my-1 min-h-tap"
              >
                {t('alert_routing.unbind')}
              </button>
            )
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
            className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition min-h-tap"
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
          className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50 min-h-tap"
        >
          {busy === persona ? '…' : t('alert_routing.bind')}
        </button>
        <button
          type="button"
          onClick={() => setOpenInput('')}
          className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
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
        <span className="inline-flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setExpanded({ ...expanded, [persona]: !open })}
            className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {t('alert_routing.topics_expander')}
          </button>
          {/* Settings apply to a group that isn't bound yet — say so,
              so the toggles below don't read as already-live. */}
          {!data.personas[persona] && (
            <span className="text-2xs text-muted-foreground">
              {t('alert_routing.topics_needs_group')}
            </span>
          )}
        </span>
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
                      product taxonomy.  Skip it for single-row groups
                      (Geofences, Scorecards…): a caps heading over one
                      row whose label repeats it is pure duplication. */}
                  {g.rows.length > 1 && (
                    <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      {g.label}
                    </div>
                  )}
                  <div className="space-y-1">
                    {g.rows.map((r) => (
                      <div key={r.alert_type}>
                        <div className="flex items-center gap-3 text-xs">
                          <span className="w-32 shrink-0 text-foreground">
                            {TYPE_LABELS[r.alert_type] || r.alert_type}
                          </span>
                          {/* Switch controls — same on/off grammar the
                              single-bot topic table uses, so both modes
                              read identically (audit: matched controls). */}
                          <span className="w-16 flex justify-center">
                            <Switch
                              size="sm"
                              aria-label={`${TYPE_LABELS[r.alert_type] || r.alert_type} — ${t('alert_routing.topic_route')}`}
                              checked={r.enabled}
                              disabled={!editable || busy === `topic-${r.alert_type}-enabled`}
                              onCheckedChange={(v) => { void toggleTopic(persona, r.alert_type, 'enabled', v); }}
                            />
                          </span>
                          <span className="w-16 flex justify-center">
                            <Switch
                              size="sm"
                              aria-label={`${TYPE_LABELS[r.alert_type] || r.alert_type} — ${t('alert_routing.topic_ai')}`}
                              checked={r.ai}
                              disabled={!editable || busy === `topic-${r.alert_type}-ai`}
                              onCheckedChange={(v) => { void toggleTopic(persona, r.alert_type, 'ai', v); }}
                            />
                          </span>
                        </div>
                        {/* Sub-category chips — pick exactly which kinds
                            this role's group receives (all on by default). */}
                        {r.subtypes && r.enabled && (
                          <div className="mt-1 ml-4 flex flex-wrap items-center gap-1">
                            {/* The chip row needs a name — unlabeled
                                pills read as decoration, not filters. */}
                            <span className="text-2xs text-muted-foreground">
                              {!r.subtypes.selected
                                ? t('alert_routing.subtype_prefix_all')
                                : t('alert_routing.subtype_prefix_some', {
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
                                  disabled={!editable || busy === `subtype-${persona}-${r.alert_type}`}
                                  onClick={() => { void toggleSubtype(persona, r, s); }}
                                  className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md border text-2xs transition disabled:opacity-60 ${
                                    active
                                      ? 'border-primary/40 bg-primary/10 text-primary'
                                      : 'border-border text-muted-foreground hover:border-ring'
                                  } min-h-tap`}
                                >
                                  {/* ✓ marks a SELECTED filter so it can't
                                      be mistaken for a pressable action. */}
                                  {active && <Check size={12} />}
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
              <div className="mb-1 inline-flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('alert_routing.custom_topics_title')}
                <InfoTip label={t('alert_routing.custom_topics_hint')} size={12} />
              </div>
              {/* Empty state sells the feature — a bare heading + button
                  hides the "why" inside the ⓘ nobody clicks. */}
              {!(customTopics?.personas?.[persona] ?? []).length && (
                <p className="mb-1 text-2xs text-muted-foreground">
                  {t('alert_routing.custom_topics_empty')}
                </p>
              )}
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
                      confirming === `rm-${tp.id}` ? (
                        confirmCluster(
                          t('alert_routing.confirm_topic_delete', { name: tp.name }),
                          t('alert_routing.remove'),
                          () => { void deleteCustomTopic(persona, tp); },
                          `ctopic-${persona}`,
                        )
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirming(`rm-${tp.id}`)}
                          disabled={busy === `ctopic-${persona}`}
                          className="text-xs text-destructive hover:underline disabled:opacity-50 py-1 -my-1 min-h-tap"
                        >
                          {t('alert_routing.remove')}
                        </button>
                      )
                    )}
                  </div>
                ))}
                {editable && openInput !== `ctopic-${persona}` && (
                  <button
                    type="button"
                    onClick={() => { setOpenInput(`ctopic-${persona}`); setCtName(''); setCtType(''); setCtSubs([]); }}
                    className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition min-h-tap"
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
                    <Select
                      value={ctType || undefined}
                      onValueChange={(v) => { setCtType(v); setCtSubs([]); }}
                    >
                      <SelectTrigger
                        size="sm"
                        aria-label={t('alert_routing.custom_topics_title')}
                        className="text-xs"
                      >
                        <SelectValue placeholder={t('alert_routing.topic_type_ph')} />
                      </SelectTrigger>
                      <SelectContent>
                        {rows.map((r) => (
                          <SelectItem key={r.alert_type} value={r.alert_type} className="text-xs">
                            {TYPE_LABELS[r.alert_type] || r.alert_type}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
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
                            } min-h-tap`}
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
                      className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50 min-h-tap"
                    >
                      {busy === `ctopic-${persona}` ? '…' : t('alert_routing.topic_create')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setOpenInput('')}
                      className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
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
  // The single role a manager manages — used to scope their own
  // first-run setup help (the account-wide checklist is owner-only).
  const managedRoles = ROLE_ORDER.filter((p) => canManage(p));
  const managerRole = !canManageAccount && managedRoles.length === 1 ? managedRoles[0] : null;

  return (
    <div>
      {/* The delivery MODE is the owner's topology choice, set on
          Settings → Telegram Bot; this surface reflects it and routes
          accordingly (no selector here). */}
      <p className="mb-3 text-xs text-muted-foreground">
        {t('alert_routing.mode_indicator', {
          mode: t(roleMode ? 'alert_routing.mode_multi_title' : 'alert_routing.mode_single_title'),
        })}
        {canManageAccount && (
          <>
            {' · '}
            <Link to="/settings" className="text-primary hover:underline">
              {t('alert_routing.mode_change_link')}
            </Link>
          </>
        )}
      </p>

      {!roleMode ? (
        // Single mode has ONE owner-managed forum group; a role manager
        // (no can_manage_account) can't configure it — and the forum GET
        // is account-gated, so rendering singleBody would 403 into a
        // misleading "couldn't load, retry" error.  Show an honest note.
        canManageAccount ? singleBody : (
          <p className="text-sm text-muted-foreground">
            {t('alert_routing.single_owner_only')}
          </p>
        )
      ) : (
        <div className="space-y-3">
          {/* Topics drive row visibility — if that fetch failed, say so
              instead of rendering a roster that looks empty-by-permission. */}
          {topicsError && (
            <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${toneClasses('warn')}`}>
              <Info size={14} className="mt-0.5 shrink-0" />
              <span>{t('alert_routing.topics_error')}</span>
              <button
                type="button"
                onClick={load}
                className="ml-auto shrink-0 font-medium underline hover:opacity-70 py-0.5 -my-0.5 min-h-tap"
              >
                {t('common.retry')}
              </button>
            </div>
          )}
          {/* Completion cue + first-run path are ACCOUNT-WIDE state —
              owner/admin only.  A role manager sees just their own row
              (its bind form carries the /chatid hint), never other
              roles' setup progress. */}
          {canManageAccount && (
          <p className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            {t('alert_routing.roster_progress', { n: boundCount, total: destinations.length })}
            <InfoTip label={`${t('alert_routing.subbot_hint')} ${t('alert_routing.fallback_note')}`} size={12} />
          </p>
          )}

          {/* First-run path — VISIBLE until the first group is bound
              (one-time setup help stays out from behind tips).  Step 1
              is pre-checked by the bot connection itself. */}
          {canManageAccount && boundCount === 0 && (
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

          {/* Manager: scoped setup help for their OWN unbound row.  The
              account-wide checklist above is owner-only, but a first-time
              manager still needs create-group → /chatid → bind — the
              audit fix that closed the leak had over-clipped this. */}
          {managerRole && !data.personas[managerRole] && (
            <div className="rounded-lg border border-border px-3 py-2 space-y-1 text-xs">
              <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('alert_routing.setup_title')}
              </p>
              <p className="text-foreground">{t('alert_routing.setup_step_group_bot', { username: botConfig.bot_username })}</p>
              <p className="text-foreground">{t('alert_routing.setup_step_chatid')}</p>
              <p className="text-foreground">{t('alert_routing.setup_step_bind')}</p>
            </div>
          )}

          {/* Main row — the identity bot + Owner & Admins group.
              Owner/admin only: a role manager has no business with the
              owners' group, and the account-bot identity stays off
              their routing view. */}
          {canManageAccount && (
          <div className="border border-border rounded-lg px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {/* One w-40 name column on EVERY row (Main included) so
                  the group cells align vertically down the roster. */}
              <span className="font-medium text-foreground w-40 shrink-0">{t('alert_routing.main_row_label')}</span>
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
          )}

          {/* Role rows — the role, its GROUP (bind here), a READ-ONLY
              sender indicator (attach the Sub bot on Settings), then
              topics.  Rows are viewer-scoped: owner/admin sees every
              role; a manager (and a manager-tier PREVIEW) sees ONLY
              their own role — other roles' groups are not their
              business (info-scope, same rule as the Settings roster). */}
          {ROLE_ORDER.filter((p) => canManage(p)).map((persona) => {
            const sub = subBots?.personas?.[persona] ?? null;
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
                  <span className="font-medium text-foreground w-40 shrink-0">
                    {t(`alert_routing.persona_${persona}`)}
                  </span>

                  {/* GROUP cell first — binding the group IS setup
                      step 1; the visual order now matches it. */}
                  <span className="inline-flex items-center gap-2">
                    {groupCell(persona)}
                  </span>

                  {/* SENDER — who delivers to this group.  READ-ONLY here;
                      attaching/detaching the Sub bot lives on Settings →
                      Telegram Bot (the "bot" home), so it isn't duplicated. */}
                  <span className="inline-flex items-center gap-2 ml-auto">
                    {sub ? (
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs ${
                        sub.is_running ? toneClasses('ok') : toneClasses('neutral')
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${sub.is_running ? 'bg-ok' : 'bg-muted-foreground'}`} />
                        @{sub.bot_username}
                      </span>
                    ) : (
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs ${toneClasses('neutral')}`}>
                        {t('alert_routing.main_sends')}
                      </span>
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

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { Check, ChevronDown, ChevronRight, Info } from 'lucide-react';

// The Telegram Bot card's body controller.  The Routing selector sits
// at the TOP of the card (it's the bot-topology decision, not an
// alert sub-setting — the owner's hierarchy call):
//
//   Routing: [Single bot] [Sub bot per role]
//     Single bot       → the classic header + forum-with-topics panel
//                        (passed in as ``singleBody`` from Settings)
//     Sub bot per role → the bot ROSTER: Main row first (identity +
//                        Owner & Admins group + fallback sender), then
//                        one row per role: status · Sub bot · group ·
//                        ▸ topics & settings
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
}

interface PersonaTopicsResponse {
  personas: Record<string, TopicRow[]>;
  manageable: string[];
}

export interface BotConfigLite {
  has_bot: boolean;
  bot_username: string;
  first_name?: string;
  is_running?: boolean;
}

// Operational roles; the owner_admin aggregate renders as the Main row.
const ROLE_ORDER = ['dispatcher', 'safety', 'fleet', 'hr'] as const;

// Display names for the canonical alert types — same English catalog
// the single-forum panel shows (its names come from the backend spec).
const TYPE_LABELS: Record<string, string> = {
  faults: 'Faults', health: 'Health', fuel: 'Fuel', events: 'Safety Events',
  camera: 'Cameras', parking: 'Parking', geofence: 'Geofences',
  scorecard: 'Scorecards', maintenance: 'Maintenance',
  documents: 'Driver Documents', system: 'Sync & System',
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
      await apiJSON(`/admin/alert-routing/persona-topics/${alert_type}`, {
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
          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs border ${toneClasses('ok')}`}>
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
      return <span className="text-xs text-muted-foreground">{fallbackText}</span>;
    }
    if (openInput !== `group-${persona}`) {
      return (
        <>
          <button
            type="button"
            onClick={() => setOpenInput(`group-${persona}`)}
            className="px-2.5 py-1 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
          >
            {t('alert_routing.bind_group_btn')}
          </button>
          <span className="text-xs text-muted-foreground">{fallbackText}</span>
        </>
      );
    }
    return (
      <>
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
          className="px-2.5 py-1 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50"
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
          <div className="mt-1.5 ml-4 space-y-1">
            {rows.map((r) => (
              <div key={r.alert_type} className="flex items-center gap-3 text-xs">
                <span className="w-32 shrink-0 text-foreground">
                  {TYPE_LABELS[r.alert_type] || r.alert_type}
                </span>
                <label className={`inline-flex items-center gap-1.5 ${editable ? '' : 'opacity-70'}`}>
                  <input
                    type="checkbox"
                    checked={r.enabled}
                    disabled={!editable || busy === `topic-${r.alert_type}-enabled`}
                    onChange={(e) => { void toggleTopic(persona, r.alert_type, 'enabled', e.target.checked); }}
                  />
                  <span className="text-muted-foreground">{t('alert_routing.topic_route')}</span>
                </label>
                <label className={`inline-flex items-center gap-1.5 ${editable ? '' : 'opacity-70'}`}>
                  <input
                    type="checkbox"
                    checked={r.ai}
                    disabled={!editable || busy === `topic-${r.alert_type}-ai`}
                    onChange={(e) => { void toggleTopic(persona, r.alert_type, 'ai', e.target.checked); }}
                  />
                  <span className="text-muted-foreground">{t('alert_routing.topic_ai')}</span>
                </label>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  const roleMode = data.mode === 'per_persona_groups';

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
      </div>

      {!roleMode ? (
        singleBody
      ) : (
        <div className="space-y-3">
          {/* Completion cue — how much of the roster is set up. */}
          <p className="text-xs text-muted-foreground">
            {t('alert_routing.roster_progress', {
              n: ROLE_ORDER.filter((p) => data.personas[p]).length,
              total: ROLE_ORDER.length,
            })}
          </p>

          {/* Main row — the identity bot.  Same spot the single-mode
              header occupies; label states its three jobs. */}
          <div className="border border-border rounded-lg px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-foreground">{t('alert_routing.main_row_label')}</span>
              <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs border ${
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
              <span className="inline-flex items-center gap-2 ml-auto">
                {groupCell('owner_admin')}
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
            return (
              <div key={persona} className="border border-border rounded-lg px-3 py-2">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium text-foreground w-20 shrink-0">
                    {t(`alert_routing.persona_${persona}`)}
                  </span>

                  {/* Group cell — step 1 */}
                  <span className="inline-flex items-center gap-2">
                    {groupCell(persona)}
                  </span>

                  {/* Sub bot cell — step 2, right-aligned */}
                  <span className="inline-flex items-center gap-2 ml-auto">
                    {sub ? (
                      <>
                        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs border ${
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
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs border ${toneClasses('neutral')}`}>
                        {t('alert_routing.main_sends')}
                      </span>
                    ) : openInput !== `token-${persona}` ? (
                      <>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs border ${toneClasses('neutral')}`}>
                          {t('alert_routing.main_sends')}
                        </span>
                        <button
                          type="button"
                          onClick={() => setOpenInput(`token-${persona}`)}
                          className="px-2.5 py-1 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
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
                          className="px-2.5 py-1 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50"
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

          <p className="text-xs text-muted-foreground">
            {t('alert_routing.hint_chatid')} {t('alert_routing.subbot_hint')}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('alert_routing.fallback_note')}
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * Sub bots roster — attach a per-role SENDER bot.  Lives on
 * Settings → Telegram Bot (the "bot" home) per the owner's architecture:
 * the bot / credential side is Settings; the group + topics side is
 * Alerts → Group delivery.
 *
 * Scope: an owner (can_manage_account) manages every role's Sub bot; a
 * role MANAGER (can_manage_role_bot) sees ONLY their own role's row —
 * the API `manageable` list enforces it server-side.  Only meaningful in
 * Sub-bots mode; single mode has no per-role senders, so it renders
 * nothing.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../../api/client';
import { toneClasses } from '../../../lib/status';
import { InfoTip } from '../../../components/tooltip';
import { useRoleView } from '../../../context/RoleViewContext';
import { ROLE_ORDER } // Alert-routing VOCABULARY (personas, family labels), not a component.
// It stays in features/alerts/delivery because it describes alerts; this
// roster only renders those names. A shared constant crossing features is
// a far milder coupling than the shared COMPONENTS that were just undone.
from '../../alerts/delivery/alertRoutingConstants';
import { Badge } from '@/components/ui/badge';

interface SubBotRow { persona: string; bot_username: string; is_running: boolean; }
interface SubBotsResponse { personas: Record<string, SubBotRow | null>; manageable: string[]; }
interface ModeResp { mode: 'single_group' | 'per_persona_groups'; }

export default function SubBotRoster({ canManageAccount }: { canManageAccount: boolean }) {
  const { t } = useTranslation();
  // View-As faithfulness: the server's `manageable` reflects the REAL
  // caller (an owner → every persona), so an owner previewing
  // "Fleet · Manager" would still see all rows.  While previewing a
  // persona without account rights, restrict to that persona's row —
  // what the real manager gets.
  const { isPreviewing, activeView } = useRoleView();
  const [subBots, setSubBots] = useState<SubBotsResponse | null>(null);
  const [mode, setMode] = useState<ModeResp['mode'] | null>(null);
  const [tokenInputs, setTokenInputs] = useState<Record<string, string>>({});
  const [openInput, setOpenInput] = useState('');
  const [confirming, setConfirming] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(() => {
    apiJSON<SubBotsResponse>('/admin/bot-instances').then(setSubBots).catch(() => setSubBots(null));
    apiJSON<ModeResp>('/admin/alert-routing').then((r) => setMode(r.mode)).catch(() => setMode(null));
  }, []);
  useEffect(load, [load]);

  // Single mode has one shared bot — no per-role senders to attach.
  if (mode !== 'per_persona_groups' || !subBots) return null;

  const manageable = subBots.manageable ?? [];
  const canManage = (p: string) => canManageAccount || manageable.includes(p);
  // Owner sees every role; a manager sees only the ones they manage;
  // a manager-tier PREVIEW sees only the previewed role's row.
  const rows = ROLE_ORDER.filter((p) =>
    canManage(p) && (!isPreviewing || canManageAccount || p === activeView));
  if (!rows.length) return null;

  const attach = async (persona: string) => {
    const token = (tokenInputs[persona] || '').trim();
    if (!token || busy) return;
    setBusy(`sub-${persona}`);
    try {
      const res = await apiJSON<{ bot_username: string }>('/admin/bot-instances', {
        method: 'POST', body: { persona, bot_token: token },
      });
      setTokenInputs({ ...tokenInputs, [persona]: '' });
      setOpenInput('');
      setSubBots({
        ...subBots,
        personas: { ...subBots.personas, [persona]: { persona, bot_username: res.bot_username, is_running: false } },
      });
      toast.success(t('alert_routing.toast_subbot_attached', { username: res.bot_username }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy('');
    }
  };

  const detach = async (persona: string) => {
    if (busy) return;
    setBusy(`sub-${persona}`);
    try {
      await apiJSON(`/admin/bot-instances/${persona}`, { method: 'DELETE' });
      setSubBots({ ...subBots, personas: { ...subBots.personas, [persona]: null } });
      toast.success(t('alert_routing.toast_subbot_detached'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy(''); setConfirming('');
    }
  };

  return (
    <div className="mb-4">
      <div className="mb-1.5 inline-flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        {t('alert_routing.subbots_label')}
        <InfoTip label={t('alert_routing.subbot_hint')} size={12} />
      </div>
      <div className="space-y-1.5">
        {rows.map((persona) => {
          const sub = subBots.personas?.[persona] ?? null;
          const editable = canManage(persona);
          return (
            <div key={persona} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-foreground w-24 shrink-0">
                {t(`alert_routing.persona_${persona}`)}
              </span>
              {sub ? (
                <>
                  <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs ${
                    sub.is_running ? toneClasses('ok') : toneClasses('neutral')
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${sub.is_running ? 'bg-ok' : 'bg-muted-foreground'}`} />
                    @{sub.bot_username}
                  </span>
                  {editable && (confirming === `detach-${persona}` ? (
                    <span className="inline-flex flex-wrap items-center gap-1.5">
                      <span className="text-2xs text-muted-foreground">
                        {t('alert_routing.confirm_detach', { username: sub.bot_username })}
                      </span>
                      <button type="button" onClick={() => { void detach(persona); }}
                        disabled={busy === `sub-${persona}`}
                        className="text-xs font-medium text-destructive hover:underline disabled:opacity-50 py-1 -my-1 min-h-tap">
                        {busy === `sub-${persona}` ? '…' : t('alert_routing.subbot_detach')}
                      </button>
                      <button type="button" onClick={() => setConfirming('')}
                        className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap">
                        {t('alert_routing.cancel')}
                      </button>
                    </span>
                  ) : (
                    <button type="button" onClick={() => setConfirming(`detach-${persona}`)}
                      disabled={busy === `sub-${persona}`}
                      className="text-xs text-destructive hover:underline disabled:opacity-50 py-1 -my-1 min-h-tap">
                      {t('alert_routing.subbot_detach')}
                    </button>
                  ))}
                </>
              ) : !editable ? (
                <Badge tone="neutral">
                  {t('alert_routing.main_sends')}
                </Badge>
              ) : openInput !== `token-${persona}` ? (
                <>
                  <Badge tone="neutral">
                    {t('alert_routing.main_sends')}
                  </Badge>
                  <button type="button" onClick={() => setOpenInput(`token-${persona}`)}
                    className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition min-h-tap">
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
                    autoComplete="off" spellCheck={false}
                    className="w-64 bg-muted border border-border rounded px-2 py-1 text-xs text-foreground font-mono focus:outline-none focus:border-ring"
                  />
                  <button type="button" onClick={() => { void attach(persona); }}
                    disabled={busy === `sub-${persona}` || (tokenInputs[persona] || '').trim().length < 30}
                    className="px-2.5 py-1 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50 min-h-tap">
                    {busy === `sub-${persona}` ? '…' : t('alert_routing.subbot_attach')}
                  </button>
                  <button type="button" onClick={() => setOpenInput('')}
                    className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap">
                    {t('alert_routing.cancel')}
                  </button>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

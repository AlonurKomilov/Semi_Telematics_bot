import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { Check, Info } from 'lucide-react';

// Where the bot posts alerts: everything into one forum group
// (single_group, the default) or a flat group per department
// (per_persona_groups).  Sits inside the Telegram Bot card, above the
// per-topic ForumRoutingSection — that table keeps working in both
// modes because the resolver falls back to the single-group route for
// any department without a binding.

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

// Display order: operational departments first, the owner/admin
// critical-aggregate last.  These are persona artifacts by definition
// (each row IS one department's group), so persona words are correct here.
const PERSONA_ORDER = ['dispatcher', 'safety', 'fleet', 'hr', 'owner_admin'] as const;

export default function AlertRoutingSection() {
  const { t } = useTranslation();
  const [data, setData] = useState<AlertRoutingResponse | null>(null);
  const [chatInputs, setChatInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string>('');

  const load = useCallback(() => {
    apiJSON<AlertRoutingResponse>('/admin/alert-routing')
      .then(setData)
      .catch(() => setData(null));
  }, []);
  useEffect(load, [load]);

  if (!data) return null;

  const setMode = async (mode: AlertRoutingResponse['mode']) => {
    if (mode === data.mode || busy) return;
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

  const showNudge =
    data.mode === 'single_group' && data.vehicle_count > data.nudge_threshold;

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
        disabled={busy === 'mode'}
        className={`flex-1 text-left border rounded-lg px-3 py-2 transition ${
          selected ? 'border-primary bg-primary/5' : 'border-border hover:border-ring'
        }`}
      >
        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
          {selected && <Check size={14} className="text-primary shrink-0" />}
          {title}
        </span>
        <span className="block text-xs text-muted-foreground mt-0.5">{desc}</span>
      </button>
    );
  };

  return (
    <div className="mt-6 border-t border-border pt-4">
      <h3 className="text-sm font-semibold mb-2">{t('alert_routing.section_title')}</h3>

      {showNudge && (
        <div className={`mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${toneClasses('info')}`}>
          <Info size={14} className="mt-0.5 shrink-0" />
          <span>{t('alert_routing.nudge', { count: data.vehicle_count })}</span>
        </div>
      )}

      <div className="flex flex-col sm:flex-row gap-2 mb-3">
        {modeOption('single_group',
          t('alert_routing.mode_single_title'), t('alert_routing.mode_single_desc'))}
        {modeOption('per_persona_groups',
          t('alert_routing.mode_multi_title'), t('alert_routing.mode_multi_desc'))}
      </div>

      {data.mode === 'per_persona_groups' && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">{t('alert_routing.hint_chatid')}</p>
          {PERSONA_ORDER.map((persona) => {
            const bound = data.personas[persona];
            return (
              <div key={persona} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="w-32 shrink-0 text-foreground">
                  {t(`alert_routing.persona_${persona}`)}
                </span>
                {bound ? (
                  <>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs border ${toneClasses('ok')}`}>
                      <Check size={12} />
                      {bound.chat_title || bound.chat_id}
                    </span>
                    <button
                      type="button"
                      onClick={() => { void unbind(persona); }}
                      disabled={busy === persona}
                      className="text-xs text-destructive hover:underline disabled:opacity-50"
                    >
                      {t('alert_routing.unbind')}
                    </button>
                  </>
                ) : (
                  <>
                    <input
                      value={chatInputs[persona] || ''}
                      onChange={(e) => setChatInputs({ ...chatInputs, [persona]: e.target.value })}
                      placeholder={t('alert_routing.chat_id_ph')}
                      className="w-44 bg-muted border border-border rounded px-2 py-1 text-xs text-foreground font-mono focus:outline-none focus:border-ring"
                    />
                    <button
                      type="button"
                      onClick={() => { void bind(persona); }}
                      disabled={busy === persona || !(chatInputs[persona] || '').trim()}
                      className="px-2.5 py-1 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition disabled:opacity-50"
                    >
                      {busy === persona ? '…' : t('alert_routing.bind')}
                    </button>
                    <span className="text-xs text-muted-foreground">
                      {t('alert_routing.bound_fallback')}
                    </span>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

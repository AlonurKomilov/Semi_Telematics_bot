/**
 * Delivery mode selector — Single bot ↔ Sub bots.  This is the OWNER's
 * topology decision, so it lives with the bot credential on
 * Settings → Telegram Bot (not on the operational Group delivery tab
 * where managers work).  The Group delivery body reads whatever mode
 * this sets and renders the single-forum config or the per-role roster.
 *
 * Owner-only in practice: rendered inside the can_manage_account-gated
 * bot card, and the PUT is can_manage_account-gated server-side too.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJSON } from '../../../api/client';
import { toneClasses } from '../../../lib/status';
import { usePreference } from '../../../preferences';
import { Check, Info, X } from 'lucide-react';

interface ModeResponse {
  mode: 'single_group' | 'per_persona_groups';
  vehicle_count: number;
  nudge_threshold: number;
}

export default function DeliveryModeSelector({
  canManageAccount,
}: {
  canManageAccount: boolean;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<ModeResponse | null>(null);
  const [busy, setBusy] = useState(false);
  // Per-browser dismissal — advice, not account config.  Registered
  // device-scoped in the preferences registry, which also owns the legacy
  // 'tg_routing_nudge_dismissed' key.
  const { value: nudgeDismissed, setValue: setNudgeDismissed } =
    usePreference('alerts.routingNudgeDismissed');

  const load = useCallback(() => {
    apiJSON<ModeResponse>('/admin/alert-routing').then(setData).catch(() => setData(null));
  }, []);
  useEffect(load, [load]);

  if (!data) return null;

  const setMode = async (mode: ModeResponse['mode']) => {
    if (mode === data.mode || busy || !canManageAccount) return;
    setBusy(true);
    try {
      await apiJSON('/admin/alert-routing', { method: 'PUT', body: { mode } });
      setData({ ...data, mode });
      toast.success(t('alert_routing.toast_mode_saved'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('alert_routing.toast_error'));
    } finally {
      setBusy(false);
    }
  };

  const showNudge =
    canManageAccount && data.mode === 'single_group'
    && data.vehicle_count > data.nudge_threshold
    && !nudgeDismissed;

  const modeOption = (mode: ModeResponse['mode'], title: string, desc: string) => {
    const selected = data.mode === mode;
    return (
      <button
        type="button"
        onClick={() => { void setMode(mode); }}
        disabled={busy || !canManageAccount}
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

  return (
    <div className="mb-4">
      <div className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        {t('alert_routing.routing_label')}
      </div>
      {showNudge && (
        <div className={`mb-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${toneClasses('info')}`}>
          <Info size={14} className="mt-0.5 shrink-0" />
          <span>{t('alert_routing.nudge', { count: data.vehicle_count })}</span>
          <button
            type="button"
            aria-label={t('alert_routing.dismiss')}
            onClick={() => setNudgeDismissed(true)}
            className="ml-auto shrink-0 hover:opacity-70"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <div className="flex flex-col sm:flex-row gap-2">
        {modeOption('single_group',
          t('alert_routing.mode_single_title'), t('alert_routing.mode_single_desc'))}
        {modeOption('per_persona_groups',
          t('alert_routing.mode_multi_title'), t('alert_routing.mode_multi_desc'))}
      </div>
      {/* The switch rebuilds the Group delivery body — say up front that
          it's reversible so exploring the other mode feels safe. */}
      {canManageAccount && (
        <p className="mt-1.5 text-2xs text-muted-foreground">
          {t('alert_routing.mode_switch_safe')}
        </p>
      )}
    </div>
  );
}

/**
 * "Notify me when" matrix — rows = the caller's role-tailored alert
 * types, columns = the personal channels (Telegram DM / Email / Push).
 * One checkbox per (type × channel): exactly the preference matrix the
 * backend stores, rendered as the grid it is.
 *
 * Column rules:
 *   Telegram — the legacy per-user toggles (/user/me/alerts); disabled
 *              when the Telegram master switch is off.
 *   Email    — /notifications/prefs/email; disabled until the address is
 *              VERIFIED (nothing sends before that, so a live-looking
 *              toggle would lie).
 *   Push     — /notifications/prefs/web_push; disabled until at least one
 *              device is enabled.
 *
 * A raw table is correct here per the dashboard rules: this is a CONFIG
 * MATRIX (form UI), not a data list — the documented DataGrid exception.
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Send, Mail, MonitorSmartphone } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { Tip } from '@/components/tooltip';
import { Card } from '@/components/ui/card';

// Every alert type the registry can hand us needs a row label here —
// the fallback renders the RAW key ("maintenance"), which reads as a bug
// beside "Engine Faults".  tests/test_alert_type_labels_ui.py pins this map
// against the backend catalog (both directions) so a newly registered type
// can't leak again — the drift is cross-language, so the guard lives there.
const TYPE_LABEL: Record<string, string> = {
  faults: 'Engine Faults',
  health: 'Vehicle Health',
  fuel: 'Fuel & DEF',
  geofence: 'Geofence Entry/Exit',
  events: 'Safety Events',
  camera: 'Camera Issues',
  parking: 'Unsafe Parking',
  maintenance: 'Maintenance Due',
};

interface ChannelPrefs {
  connected: boolean;
  verified: boolean;
  enabled_master: boolean;
  types: Record<string, boolean>;
  devices?: { id: number }[];
}

interface MatrixProps {
  /** Role-tailored types + Telegram state come from the parent's
   * /user/me/alerts load (the legacy columns stay SSOT for telegram
   * until the matrix reader flip). */
  relevantTypes: string[];
  telegramMasterOn: boolean;
  telegramToggles: Record<string, boolean>;
  onTelegramToggle: (field: string, value: boolean) => Promise<void>;
  /** Bumped by the channel cards after connect/verify/device changes so
   * the column enable-states stay fresh. */
  refreshKey: number;
  /** Fired after a successful write so the page can confirm the autosave. */
  onSaved?: () => void;
}

export default function NotifyMatrix({
  relevantTypes, telegramMasterOn, telegramToggles, onTelegramToggle,
  refreshKey, onSaved,
}: MatrixProps) {
  const [email, setEmail] = useState<ChannelPrefs | null>(null);
  const [push, setPush] = useState<ChannelPrefs | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [e, p] = await Promise.all([
        apiJSON<{ email: ChannelPrefs }>('/notifications/prefs/email'),
        apiJSON<{ web_push: ChannelPrefs }>('/notifications/prefs/web_push'),
      ]);
      setEmail(e.email);
      setPush(p.web_push);
    } catch {
      // Column headers render a neutral state; toggles stay disabled.
    }
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);

  const setMatrixType = async (
    channel: 'email' | 'web_push', type: string, enabled: boolean,
  ) => {
    const setState = channel === 'email' ? setEmail : setPush;
    setState(p => p && ({ ...p, types: { ...p.types, [type]: enabled } }));
    setSaving(`${channel}:${type}`);
    try {
      await apiJSON(`/notifications/prefs/${channel}/type`, {
        method: 'PUT', body: { alert_type: type, enabled },
      });
      onSaved?.();
    } catch (e) {
      setState(p => p && ({ ...p, types: { ...p.types, [type]: !enabled } }));
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(null);
    }
  };

  const emailOn = !!email?.verified && !!email?.enabled_master;
  const pushOn = (push?.devices?.length ?? 0) > 0 && !!push?.enabled_master;

  const colHint = {
    telegram: telegramMasterOn ? '' : 'Personal alerts are switched off above',
    email: emailOn ? '' : 'Connect and verify your email above first',
    push: pushOn ? '' : 'Enable push on at least one device above first',
  };

  return (
    <Card render={<section />}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-3">
        Notify me when
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th className="text-left font-medium pb-2">Alert type</th>
              <Th icon={Send} label="Telegram" hint={colHint.telegram} />
              <Th icon={Mail} label="Email" hint={colHint.email} />
              <Th icon={MonitorSmartphone} label="Push" hint={colHint.push} />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {relevantTypes.map((type) => (
              <tr key={type}>
                <td className="py-2.5 pr-3">{TYPE_LABEL[type] || type}</td>
                <Cell
                  checked={!!telegramToggles[`alert_${type}`]}
                  disabled={!telegramMasterOn || saving === `tg:${type}`}
                  hint={colHint.telegram}
                  onChange={async (v) => {
                    setSaving(`tg:${type}`);
                    // setField already toasts + rolls back on failure —
                    // swallow the re-throw so nothing rejects unhandled.
                    try { await onTelegramToggle(`alert_${type}`, v); }
                    catch { /* handled upstream */ }
                    finally { setSaving(null); }
                  }}
                />
                <Cell
                  checked={!!email?.types[type]}
                  disabled={!emailOn || saving === `email:${type}`}
                  hint={colHint.email}
                  onChange={(v) => setMatrixType('email', type, v)}
                />
                <Cell
                  checked={!!push?.types[type]}
                  disabled={!pushOn || saving === `web_push:${type}`}
                  hint={colHint.push}
                  onChange={(v) => setMatrixType('web_push', type, v)}
                />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {relevantTypes.length === 0 && (
        <p className="text-xs text-muted-foreground">
          Your role doesn&apos;t have any alert categories configured.
        </p>
      )}
      {/* Visible (not hover-only) explanation of greyed columns — this
          app runs on cab tablets where tooltips are unreachable. */}
      {(colHint.telegram || colHint.email || colHint.push) && (
        <p className="text-2xs text-muted-foreground mt-2">
          {[
            colHint.telegram && `Telegram: ${colHint.telegram.toLowerCase()}`,
            colHint.email && `Email: ${colHint.email.toLowerCase()}`,
            colHint.push && `Push: ${colHint.push.toLowerCase()}`,
          ].filter(Boolean).join(' · ')}
        </p>
      )}
      {/* Why this grid has three channels while Account activity below has
          four: alerts have their own feed (the bell reads them from the
          alert board), so there's no In-app column to opt into here. */}
      <p className="text-2xs text-muted-foreground mt-2">
        Alerts always appear in the bell — these channels are the extra
        places they can reach you.
      </p>
    </Card>
  );
}

function Th({ icon: Icon, label, hint }: {
  icon: typeof Send; label: string; hint: string;
}) {
  const head = (
    <span className={`inline-flex items-center gap-1.5 font-medium ${
      hint ? 'opacity-50' : ''
    }`}>
      <Icon className="size-3.5" aria-hidden /> {label}
    </span>
  );
  return (
    <th className="pb-2 px-2 text-center font-medium w-24">
      {hint ? <Tip label={hint}>{head}</Tip> : head}
    </th>
  );
}

function Cell({ checked, disabled, hint, onChange }: {
  checked: boolean; disabled: boolean; hint: string;
  onChange: (v: boolean) => void | Promise<void>;
}) {
  const box = (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={(e) => void onChange(e.target.checked)}
      className="accent-primary cursor-pointer disabled:cursor-not-allowed"
      aria-label={hint || undefined}
    />
  );
  return (
    <td className="py-2.5 px-2 text-center">
      {/* Disabled inputs swallow pointer events, so the Tip needs a
          wrapper element to hover — without it the reason never shows. */}
      {disabled && hint ? <Tip label={hint}><span>{box}</span></Tip> : box}
    </td>
  );
}

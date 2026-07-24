/**
 * "Account activity" — the TARGETED (opt-out) notification sources.
 *
 * The other half of the notification model from the alert matrix: alerts
 * are BROADCAST + opt-IN (pick which ones reach you); account-activity
 * notices (e.g. "someone accepted your invite") are TARGETED at the one
 * person who cares and delivered opt-OUT — ON for every connected channel
 * until you mute it here.  Same config-matrix shape as NotifyMatrix (rows
 * = categories, columns = the three personal channels), so the two read
 * as one family; the difference is the default and the write endpoint.
 *
 * A column is greyed when its channel can't deliver yet (server-computed
 * ``ready``), matching the alert matrix so a live-looking toggle never
 * lies.  A raw table is correct here per the dashboard rules: a CONFIG
 * MATRIX, not a data list (the documented DataGrid exception).
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Send, Mail, MonitorSmartphone, Check, Bell } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { Tip } from '@/components/tooltip';

type ChannelKey = 'telegram_dm' | 'email' | 'web_push' | 'in_app';

interface Category {
  key: string;
  label: string;
  source: string;
  mandatory: boolean;
  channels: Record<ChannelKey, boolean>;
}
interface ChannelState { connected: boolean; verified: boolean; enabled_master: boolean; ready: boolean; }
interface Payload { categories: Category[]; channels: Record<ChannelKey, ChannelState>; }

const COLUMNS: { key: ChannelKey; icon: typeof Send; label: string; notReady: string }[] = [
  // In-app first: always deliverable (the bell inbox needs no connection),
  // so its column is never greyed — the one toggle every user can use.
  { key: 'in_app', icon: Bell, label: 'In-app', notReady: '' },
  { key: 'telegram_dm', icon: Send, label: 'Telegram', notReady: 'Turn on personal alerts in Channels above first' },
  { key: 'email', icon: Mail, label: 'Email', notReady: 'Connect and verify your email in Channels above first' },
  { key: 'web_push', icon: MonitorSmartphone, label: 'Push', notReady: 'Enable push on at least one device in Channels above first' },
];

export default function AccountActivitySection({ refreshKey, section = 'personal' }: {
  refreshKey: number;
  /** 'personal' = team/ai targeted notices; 'system' = the system.*
   *  categories (security/billing — typically mandatory, locked-on).
   *  Same endpoint + grid; the page mounts one instance per section. */
  section?: 'personal' | 'system';
}) {
  const [data, setData] = useState<Payload | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      setData(await apiJSON<Payload>('/notifications/preferences/account-activity'));
    } catch {
      // A load failure must NOT masquerade as "nothing registered yet" —
      // that would tell the user all is well when it isn't.
      setFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);

  const toggle = async (cat: Category, channel: ChannelKey, enabled: boolean) => {
    setData(d => d && ({
      ...d,
      categories: d.categories.map(c =>
        c.key === cat.key ? { ...c, channels: { ...c.channels, [channel]: enabled } } : c),
    }));
    setSaving(`${cat.key}:${channel}`);
    try {
      await apiJSON('/notifications/preferences/account-activity', {
        method: 'PUT', body: { category: cat.key, channel, enabled },
      });
    } catch (e) {
      setData(d => d && ({
        ...d,
        categories: d.categories.map(c =>
          c.key === cat.key ? { ...c, channels: { ...c.channels, [channel]: !enabled } } : c),
      }));
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(null);
    }
  };

  const visible = (data?.categories ?? []).filter((c) =>
    section === 'system' ? c.source === 'system' : c.source !== 'system');

  // Load failed — offer a retry, never a false "all good" empty state.
  if (loaded && failed) {
    return (
      <section className="bg-card border border-border rounded-xl p-4">
        <p className="text-sm text-muted-foreground">
          Couldn’t load account-activity settings.{' '}
          <button onClick={() => void load()} className="text-primary hover:underline font-medium">
            Try again
          </button>
        </p>
      </section>
    );
  }
  // Nothing to configure yet — say so rather than render an empty grid.
  if (loaded && (!data || visible.length === 0)) {
    return (
      <section className="bg-card border border-border rounded-xl p-4">
        <p className="text-sm text-muted-foreground">
          {section === 'system'
            ? 'No system notices apply to your role yet.'
            : 'No account-activity notifications yet. As new ones arrive (like when someone accepts your invite), you\u2019ll be able to choose where they reach you here.'}
        </p>
      </section>
    );
  }
  if (!data) return null;

  const notReady = (k: ChannelKey) => (data.channels[k]?.ready ? '' : COLUMNS.find(c => c.key === k)!.notReady);

  return (
    <section className="bg-card border border-border rounded-xl p-4">
      {/* Opt-OUT, unlike the opt-IN Alerts grid above — the two look
          identical, so name the polarity explicitly: here ON is the
          resting state and a tick you REMOVE is a mute.  System rows are
          mostly MANDATORY (locked on), so that badge says so instead. */}
      <span className="inline-flex items-center gap-1.5 rounded-md bg-muted px-2 py-0.5 text-2xs font-medium text-muted-foreground mb-3">
        <Check size={12} aria-hidden />
        {section === 'system'
          ? 'Security and billing notices can\u2019t be switched off'
          : 'On by default — turn off what you don\u2019t want'}
      </span>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th className="text-left font-medium pb-2">Notify me about</th>
              {COLUMNS.map(col => (
                <Th key={col.key} icon={col.icon} label={col.label} hint={notReady(col.key)} />
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {visible.map(cat => (
              <tr key={cat.key}>
                <td className="py-2.5 pr-3">{cat.label}</td>
                {COLUMNS.map(col => {
                  const hint = notReady(col.key);
                  return (
                    <Cell
                      key={col.key}
                      checked={!!cat.channels[col.key]}
                      disabled={cat.mandatory || !!hint || saving === `${cat.key}:${col.key}`}
                      hint={cat.mandatory ? 'Required — can’t be turned off' : hint}
                      onChange={(v) => toggle(cat, col.key, v)}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Visible (not hover-only) explanation of greyed columns — cab
          tablets can't reach tooltips. */}
      {COLUMNS.some(c => notReady(c.key)) && (
        <p className="text-2xs text-muted-foreground mt-2">
          {COLUMNS
            .map(c => notReady(c.key) && `${c.label}: ${notReady(c.key).toLowerCase()}`)
            .filter(Boolean).join(' · ')}
        </p>
      )}
    </section>
  );
}

function Th({ icon: Icon, label, hint }: { icon: typeof Send; label: string; hint: string }) {
  const head = (
    <span className={`inline-flex items-center gap-1.5 font-medium ${hint ? 'opacity-50' : ''}`}>
      <Icon size={14} aria-hidden /> {label}
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
      {disabled && hint ? <Tip label={hint}>{box}</Tip> : box}
    </td>
  );
}

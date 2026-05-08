import { useEffect, useState } from 'react';
import { Mail } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { PageHeader, CardSkeleton, ErrorState } from '../../components/shell';
import type { Subscription } from '../../types';

const REPORT_TYPES: Record<string, string> = {
  faults: '🔧 Faults',
  fuel: '⛽ Fuel & DEF',
  health: '🏥 Vehicle Health',
  efficiency: '📊 Efficiency',
  camera: '📷 Cameras',
};

const FREQUENCIES = ['daily', 'weekly', 'monthly'] as const;

const HOURS = Array.from({ length: 24 }, (_, i) => i);

function fmtHour(h: number) {
  if (h === 0) return '12:00 AM';
  if (h === 12) return '12:00 PM';
  return h < 12 ? `${h}:00 AM` : `${h - 12}:00 PM`;
}

const COMMON_TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Phoenix',
  'America/Anchorage',
  'Pacific/Honolulu',
  'UTC',
];

export default function Subscriptions() {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Form
  const [frequency, setFrequency] = useState('daily');
  const [reportType, setReportType] = useState('faults');
  const [sendHour, setSendHour] = useState(7);
  const [timezone, setTimezone] = useState('America/New_York');

  async function load() {
    setLoading(true);
    try {
      const d = await apiJSON<{ subscription: Subscription | null }>('/user/subscriptions');
      setSub(d.subscription);
      if (d.subscription) {
        setFrequency(d.subscription.frequency);
        setReportType(d.subscription.report_type);
        setSendHour(d.subscription.send_hour);
        setTimezone(d.subscription.timezone);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function save() {
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await apiJSON('/user/subscriptions', {
        method: 'PUT',
        body: { frequency, report_type: reportType, send_hour: sendHour, timezone },
      });
      setSuccess('Subscription saved!');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function unsubscribe() {
    if (!confirm('Unsubscribe from report subscriptions?')) return;
    try {
      await apiJSON('/user/subscriptions', { method: 'DELETE' });
      setSub(null);
      setSuccess('Unsubscribed');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    }
  }

  if (loading) {
    return (
      <div className="max-w-xl">
        <PageHeader
          icon={Mail}
          title="Report Subscriptions"
          description="Schedule a recurring email with the report of your choice. Pick the frequency, hour, and time zone — you can unsubscribe any time."
        />
        <CardSkeleton height="h-48" />
      </div>
    );
  }

  return (
    <div className="max-w-xl">
      <PageHeader
        icon={Mail}
        title="Report Subscriptions"
        description="Schedule a recurring email with the report of your choice. Pick the frequency, hour, and time zone — you can unsubscribe any time."
      />

      {error && <div className="mb-3"><ErrorState message={error} /></div>}
      {success && <p className="text-green-600 dark:text-green-400 text-sm mb-3">{success}</p>}

      {sub && (
        <div className="bg-muted rounded-lg p-4 mb-6 flex items-center justify-between">
          <div>
            <p className="text-sm text-foreground/80">
              Currently subscribed: <span className="font-medium text-foreground">{REPORT_TYPES[sub.report_type] || sub.report_type}</span>{' '}
              — {sub.frequency} at {fmtHour(sub.send_hour)} ({sub.timezone})
            </p>
          </div>
          <button onClick={unsubscribe} className="text-destructive hover:text-destructive/80 text-sm">Unsubscribe</button>
        </div>
      )}

      <div className="space-y-4">
        <div>
          <label className="block text-sm text-muted-foreground mb-1">Report Type</label>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(REPORT_TYPES).map(([val, label]) => (
              <button
                key={val}
                onClick={() => setReportType(val)}
                className={`px-3 py-2 rounded-lg text-sm text-left transition-colors border ${
                  reportType === val
                    ? 'bg-primary/15 border-primary text-primary'
                    : 'bg-muted border-border text-muted-foreground hover:text-foreground'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm text-muted-foreground mb-1">Frequency</label>
          <div className="flex gap-2">
            {FREQUENCIES.map((f) => (
              <button
                key={f}
                onClick={() => setFrequency(f)}
                className={`px-4 py-2 rounded-lg text-sm capitalize transition-colors border ${
                  frequency === f
                    ? 'bg-primary/15 border-primary text-primary'
                    : 'bg-muted border-border text-muted-foreground hover:text-foreground'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-sm text-muted-foreground mb-1">Delivery Hour</label>
            <select value={sendHour} onChange={(e) => setSendHour(+e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border">
              {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-sm text-muted-foreground mb-1">Timezone</label>
            <select value={timezone} onChange={(e) => setTimezone(e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border">
              {COMMON_TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
        </div>

        <button
          onClick={save}
          disabled={saving}
          className="w-full py-3 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium text-sm transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving...' : sub ? 'Update Subscription' : 'Subscribe'}
        </button>
      </div>
    </div>
  );
}

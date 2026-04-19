import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';
import type { Subscription } from '../../types';

const REPORT_TYPES: Record<string, string> = {
  faults: '🔧 Faults',
  fuel: '⛽ Fuel & DEF',
  health: '🏥 Vehicle Health',
  efficiency: '📊 Efficiency',
  camera: '📷 Camera Check',
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
    if (!confirm('Unsubscribe from auto-reports?')) return;
    try {
      await apiJSON('/user/subscriptions', { method: 'DELETE' });
      setSub(null);
      setSuccess('Unsubscribed');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    }
  }

  if (loading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div className="max-w-xl">
      <h1 className="text-2xl font-bold mb-6">📬 Auto-Report Subscription</h1>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
      {success && <p className="text-green-400 text-sm mb-3">{success}</p>}

      {sub && (
        <div className="bg-gray-800 rounded-lg p-4 mb-6 flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-300">
              Currently subscribed: <span className="font-medium text-white">{REPORT_TYPES[sub.report_type] || sub.report_type}</span>{' '}
              — {sub.frequency} at {fmtHour(sub.send_hour)} ({sub.timezone})
            </p>
          </div>
          <button onClick={unsubscribe} className="text-red-400 hover:text-red-300 text-sm">Unsubscribe</button>
        </div>
      )}

      <div className="space-y-4">
        <div>
          <label className="block text-sm text-gray-400 mb-1">Report Type</label>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(REPORT_TYPES).map(([val, label]) => (
              <button
                key={val}
                onClick={() => setReportType(val)}
                className={`px-3 py-2 rounded-lg text-sm text-left transition-colors border ${
                  reportType === val
                    ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                    : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm text-gray-400 mb-1">Frequency</label>
          <div className="flex gap-2">
            {FREQUENCIES.map((f) => (
              <button
                key={f}
                onClick={() => setFrequency(f)}
                className={`px-4 py-2 rounded-lg text-sm capitalize transition-colors border ${
                  frequency === f
                    ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                    : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-sm text-gray-400 mb-1">Delivery Hour</label>
            <select value={sendHour} onChange={(e) => setSendHour(+e.target.value)} className="w-full bg-gray-800 rounded px-3 py-2 text-sm text-gray-100 border border-gray-700">
              {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-sm text-gray-400 mb-1">Timezone</label>
            <select value={timezone} onChange={(e) => setTimezone(e.target.value)} className="w-full bg-gray-800 rounded px-3 py-2 text-sm text-gray-100 border border-gray-700">
              {COMMON_TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
        </div>

        <button
          onClick={save}
          disabled={saving}
          className="w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving...' : sub ? 'Update Subscription' : 'Subscribe'}
        </button>
      </div>
    </div>
  );
}

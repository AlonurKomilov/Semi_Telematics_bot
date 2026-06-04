import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { GraduationCap } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';

// ── Types ─────────────────────────────────────────────────────────

interface CoachingRule {
  id: number;
  name: string;
  kind: 'score_threshold' | 'incident_count';
  topic_key: string;
  period_days: number;
  score_max: number | null;
  event_type: string | null;
  min_count: number | null;
  severity: 'low' | 'medium' | 'high';
  message: string;
  active: number | boolean;
}

interface CoachingTopic {
  account_id: number;
  key: string;
  label: string;
  default_message: string;
  active: number | boolean;
}

interface CoachingAssignment {
  id: number;
  account_id: number;
  driver_id: string;
  rule_id: number | null;
  topic_key: string;
  severity: 'low' | 'medium' | 'high';
  reason: string;
  status: 'pending' | 'acknowledged' | 'cancelled';
  assigned_by: number;
  assigned_at: string;
  due_at: string | null;
  acknowledged_at: string | null;
}

type Tab = 'assignments' | 'rules' | 'topics';

const SEVERITY_ICON: Record<string, string> = {
  low: '🟢',
  medium: '🟡',
  high: '🔴',
};

// ── Page ─────────────────────────────────────────────────────────

export default function Coaching() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('assignments');
  const { user, loading: authLoading } = useAuth();
  const disabled = user?.coaching_enabled === false;

  if (authLoading) {
    return (
      <div className="space-y-4">
        <PageHeader
          icon={GraduationCap}
          title={t('pages.coaching_title')}
          description={t('pages.coaching_desc_default')}
        />
        <CardSkeleton height="h-40" />
      </div>
    );
  }

  if (disabled) {
    return (
      <div className="space-y-4">
        <PageHeader
          icon={GraduationCap}
          title={t('pages.coaching_title')}
          description={t('pages.coaching_desc_short')}
        />
        <div className="rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-700 dark:text-yellow-300">
          Auto Coaching is not enabled for this account. Contact your administrator to activate this feature.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        icon={GraduationCap}
        title={t('pages.coaching_title')}
        description={t('pages.coaching_desc_long')}
      />

      <div className="flex gap-2 border-b border-border/50">
        {(['assignments', 'rules', 'topics'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm font-medium ${
              tab === t ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground'
            }`}
          >
            {t === 'assignments' ? 'Assignments' : t === 'rules' ? 'Rules' : 'Topics'}
          </button>
        ))}
      </div>

      {tab === 'assignments' && <AssignmentsTab />}
      {tab === 'rules' && <RulesTab />}
      {tab === 'topics' && <TopicsTab />}
    </div>
  );
}

// ── Assignments Tab ──────────────────────────────────────────────

function AssignmentsTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [driverFilter, setDriverFilter] = useState<string>('');

  // Manual assignment form
  const [drvId, setDrvId] = useState('');
  const [topicKey, setTopicKey] = useState('');
  const [severity, setSeverity] = useState<'low' | 'medium' | 'high'>('medium');
  const [reason, setReason] = useState('');

  const { data: itemsData, isLoading: loading } = useQuery({
    queryKey: ['coaching-assignments', statusFilter, driverFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (statusFilter) params.set('status', statusFilter);
      if (driverFilter) params.set('driver_id', driverFilter);
      return apiJSON<CoachingAssignment[]>(
        `/coaching/assignments${params.toString() ? `?${params}` : ''}`,
      );
    },
    placeholderData: (prev) => prev,
  });
  const items = Array.isArray(itemsData) ? itemsData : [];

  const { data: topicsData } = useQuery({
    queryKey: ['coaching-topics'],
    queryFn: () => apiJSON<CoachingTopic[]>('/coaching/topics'),
  });
  const topics = Array.isArray(topicsData) ? topicsData : [];
  // Default the topic picker to the first topic once topics load.
  useEffect(() => {
    if (!topicKey && topics[0]) setTopicKey(topics[0].key);
  }, [topicKey, topics]);

  const load = () => qc.invalidateQueries({ queryKey: ['coaching-assignments'] });

  const onAssign = async () => {
    if (!drvId || !topicKey) return;
    const r = await apiFetch('/coaching/assign', {
      method: 'POST',
      body: { driver_id: drvId, topic_key: topicKey, severity, reason },
    });
    if (r.ok) {
      setDrvId('');
      setReason('');
      await load();
    }
  };

  const onCancel = async (id: number) => {
    if (!confirm('Cancel this coaching assignment?')) return;
    const r = await apiFetch(`/coaching/assignments/${id}/cancel`, { method: 'POST' });
    if (r.ok) await load();
  };

  const onRunNow = async () => {
    if (!confirm('Trigger coaching evaluation for the last 7 days?')) return;
    await apiFetch('/coaching/run-now?days=7', { method: 'POST' });
    await load();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border/50 p-3">
        <div>
          <label className="block text-xs text-muted-foreground">Driver</label>
          <input
            value={drvId}
            onChange={(e) => setDrvId(e.target.value)}
            className="rounded border border-border/50 bg-background px-2 py-1 text-sm"
            placeholder={t('forms.driver_id_placeholder')}
          />
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Topic</label>
          <select
            value={topicKey}
            onChange={(e) => setTopicKey(e.target.value)}
            className="rounded border border-border/50 bg-background px-2 py-1 text-sm"
          >
            {topics.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label || t.key}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Severity</label>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as 'low' | 'medium' | 'high')}
            className="rounded border border-border/50 bg-background px-2 py-1 text-sm"
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
        <div className="flex-1">
          <label className="block text-xs text-muted-foreground">Reason</label>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
            placeholder={t('forms.optional_placeholder')}
          />
        </div>
        <button
          onClick={onAssign}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
        >
          Assign
        </button>
        <button
          onClick={onRunNow}
          className="rounded border border-border/50 px-3 py-1 text-sm"
        >
          Run engine now
        </button>
      </div>

      <div className="flex flex-wrap gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded border border-border/50 bg-background px-2 py-1 text-sm"
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <input
          value={driverFilter}
          onChange={(e) => setDriverFilter(e.target.value)}
          placeholder={t('forms.filter_by_driver_id')}
          className="rounded border border-border/50 bg-background px-2 py-1 text-sm"
        />
        <button onClick={load} className="rounded border border-border/50 px-3 py-1 text-sm">
          Apply
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No assignments.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-2">Severity</th>
              <th className="py-1 pr-2">Driver</th>
              <th className="py-1 pr-2">Topic</th>
              <th className="py-1 pr-2">Reason</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1 pr-2">Assigned</th>
              <th className="py-1 pr-2"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id} className="border-t border-border/30">
                <td className="py-1 pr-2">{SEVERITY_ICON[a.severity] || '🟡'}</td>
                <td className="py-1 pr-2 font-mono">{a.driver_id}</td>
                <td className="py-1 pr-2">{a.topic_key}</td>
                <td className="py-1 pr-2">{a.reason}</td>
                <td className="py-1 pr-2">{a.status}</td>
                <td className="py-1 pr-2 text-xs text-muted-foreground">{a.assigned_at}</td>
                <td className="py-1 pr-2">
                  {a.status !== 'cancelled' && (
                    <button
                      onClick={() => onCancel(a.id)}
                      className="text-xs text-destructive underline"
                    >
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Rules Tab ────────────────────────────────────────────────────

function RulesTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  // Form state
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'score_threshold' | 'incident_count'>('score_threshold');
  const [topicKey, setTopicKey] = useState('');
  const [periodDays, setPeriodDays] = useState(7);
  const [scoreMax, setScoreMax] = useState<number | ''>('');
  const [eventType, setEventType] = useState('');
  const [minCount, setMinCount] = useState<number | ''>('');
  const [severity, setSeverity] = useState<'low' | 'medium' | 'high'>('medium');
  const [message, setMessage] = useState('');

  const { data: rulesData, isLoading: rulesLoading } = useQuery({
    queryKey: ['coaching-rules'],
    queryFn: () => apiJSON<CoachingRule[]>('/coaching/rules'),
  });
  const { data: topicsData } = useQuery({
    queryKey: ['coaching-topics'],
    queryFn: () => apiJSON<CoachingTopic[]>('/coaching/topics'),
  });
  const rules = Array.isArray(rulesData) ? rulesData : [];
  const topics = Array.isArray(topicsData) ? topicsData : [];
  const loading = rulesLoading;

  useEffect(() => {
    if (!topicKey && topics[0]) setTopicKey(topics[0].key);
  }, [topicKey, topics]);

  const load = () => qc.invalidateQueries({ queryKey: ['coaching-rules'] });

  const onCreate = async () => {
    if (!name || !topicKey) return;
    const body: Record<string, unknown> = {
      name,
      kind,
      topic_key: topicKey,
      period_days: periodDays,
      severity,
      message,
    };
    if (kind === 'score_threshold') body.score_max = scoreMax || 0;
    else {
      body.event_type = eventType || null;
      body.min_count = minCount || 0;
    }
    const r = await apiFetch('/coaching/rules', {
      method: 'POST',
      body,
    });
    if (r.ok) {
      setName('');
      setMessage('');
      setScoreMax('');
      setEventType('');
      setMinCount('');
      await load();
    }
  };

  const onToggle = async (rule: CoachingRule) => {
    await apiFetch(`/coaching/rules/${rule.id}`, {
      method: 'PUT',
      body: { active: !rule.active },
    });
    await load();
  };

  const onDelete = async (id: number) => {
    if (!confirm('Delete this rule?')) return;
    await apiFetch(`/coaching/rules/${id}`, { method: 'DELETE' });
    await load();
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 rounded-lg border border-border/50 p-3 md:grid-cols-2">
        <div>
          <label className="block text-xs text-muted-foreground">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Kind</label>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as 'score_threshold' | 'incident_count')}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
          >
            <option value="score_threshold">Score ≤ threshold</option>
            <option value="incident_count">Incident count ≥ N</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Topic</label>
          <select
            value={topicKey}
            onChange={(e) => setTopicKey(e.target.value)}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
          >
            {topics.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label || t.key}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Period (days)</label>
          <input
            type="number"
            value={periodDays}
            onChange={(e) => setPeriodDays(parseInt(e.target.value) || 7)}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
          />
        </div>
        {kind === 'score_threshold' ? (
          <div>
            <label className="block text-xs text-muted-foreground">Score max</label>
            <input
              type="number"
              value={scoreMax}
              onChange={(e) => setScoreMax(e.target.value === '' ? '' : parseFloat(e.target.value))}
              className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
            />
          </div>
        ) : (
          <>
            <div>
              <label className="block text-xs text-muted-foreground">Event type</label>
              <input
                value={eventType}
                onChange={(e) => setEventType(e.target.value)}
                placeholder={t('forms.event_type_example')}
                className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground">Min count</label>
              <input
                type="number"
                value={minCount}
                onChange={(e) => setMinCount(e.target.value === '' ? '' : parseInt(e.target.value))}
                className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
              />
            </div>
          </>
        )}
        <div>
          <label className="block text-xs text-muted-foreground">Severity</label>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as 'low' | 'medium' | 'high')}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
        <div className="md:col-span-2">
          <label className="block text-xs text-muted-foreground">Message (shown to driver)</label>
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="w-full rounded border border-border/50 bg-background px-2 py-1 text-sm"
          />
        </div>
        <div className="md:col-span-2">
          <button
            onClick={onCreate}
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          >
            Create rule
          </button>
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : rules.length === 0 ? (
        <p className="text-sm text-muted-foreground">No rules configured.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Kind</th>
              <th className="py-1 pr-2">Topic</th>
              <th className="py-1 pr-2">Trigger</th>
              <th className="py-1 pr-2">Severity</th>
              <th className="py-1 pr-2">Active</th>
              <th className="py-1 pr-2"></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id} className="border-t border-border/30">
                <td className="py-1 pr-2">{r.name}</td>
                <td className="py-1 pr-2">{r.kind}</td>
                <td className="py-1 pr-2">{r.topic_key}</td>
                <td className="py-1 pr-2 text-xs text-muted-foreground">
                  {r.kind === 'score_threshold'
                    ? `score ≤ ${r.score_max}`
                    : `${r.event_type || 'any'} ≥ ${r.min_count}/${r.period_days}d`}
                </td>
                <td className="py-1 pr-2">{SEVERITY_ICON[r.severity] || '🟡'}</td>
                <td className="py-1 pr-2">
                  <button onClick={() => onToggle(r)} className="text-xs underline">
                    {r.active ? 'Disable' : 'Enable'}
                  </button>
                </td>
                <td className="py-1 pr-2">
                  <button
                    onClick={() => onDelete(r.id)}
                    className="text-xs text-destructive underline"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Topics Tab ───────────────────────────────────────────────────

function TopicsTab() {
  const { data, isLoading: loading } = useQuery({
    queryKey: ['coaching-topics'],
    queryFn: () => apiJSON<CoachingTopic[]>('/coaching/topics'),
  });
  const topics = Array.isArray(data) ? data : [];

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-muted-foreground">
        <tr>
          <th className="py-1 pr-2">Key</th>
          <th className="py-1 pr-2">Label</th>
          <th className="py-1 pr-2">Default message</th>
          <th className="py-1 pr-2">Active</th>
        </tr>
      </thead>
      <tbody>
        {topics.map((t) => (
          <tr key={t.key} className="border-t border-border/30">
            <td className="py-1 pr-2 font-mono">{t.key}</td>
            <td className="py-1 pr-2">{t.label}</td>
            <td className="py-1 pr-2 text-xs text-muted-foreground">{t.default_message}</td>
            <td className="py-1 pr-2">{t.active ? '✓' : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

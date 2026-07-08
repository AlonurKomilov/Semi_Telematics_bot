import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { GraduationCap } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import DataGrid from '../../components/DataGrid';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import type { AnyColumn } from '../../types';

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

const SEVERITY_ITEMS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

const KIND_ITEMS = [
  { value: 'score_threshold', label: 'Score ≤ threshold' },
  { value: 'incident_count', label: 'Incident count ≥ N' },
];

const STATUS_ITEMS = [
  { value: '', label: 'All statuses' },
  { value: 'pending', label: 'Pending' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'cancelled', label: 'Cancelled' },
];

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
        <div className={`rounded-lg border px-4 py-3 text-sm ${toneClasses('warn')}`}>
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
  const topicItems = useMemo(
    () => topics.map((tp) => ({ value: tp.key, label: tp.label || tp.key })),
    [topics],
  );
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
          <Select value={topicKey} onValueChange={(v) => setTopicKey(v)} items={topicItems}>
            <SelectTrigger aria-label="Topic"><SelectValue placeholder="Select topic" /></SelectTrigger>
            <SelectContent>
              {topicItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Severity</label>
          <Select value={severity} onValueChange={(v) => setSeverity(v as 'low' | 'medium' | 'high')} items={SEVERITY_ITEMS}>
            <SelectTrigger aria-label="Severity"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEVERITY_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
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
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v)} items={STATUS_ITEMS}>
          <SelectTrigger aria-label="Status filter"><SelectValue /></SelectTrigger>
          <SelectContent>
            {STATUS_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
          </SelectContent>
        </Select>
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
        <DataGrid
          columns={[
            {
              key: 'severity', label: 'Severity', sortable: true,
              render: (v) => <span>{SEVERITY_ICON[String(v)] || '🟡'}</span>,
            },
            {
              key: 'driver_id', label: 'Driver', sortable: true,
              render: (v) => <span className="font-mono">{String(v)}</span>,
            },
            { key: 'topic_key', label: 'Topic', sortable: true },
            { key: 'reason', label: 'Reason', sortable: false },
            { key: 'status', label: 'Status', sortable: true },
            {
              key: 'assigned_at', label: 'Assigned', sortable: true,
              render: (v) => <span className="text-xs text-muted-foreground">{String(v ?? '')}</span>,
            },
            {
              key: '_actions', label: '', sortable: false,
              render: (_v, row) => {
                const a = row as unknown as CoachingAssignment;
                return a.status !== 'cancelled' ? (
                  <span className="inline-flex justify-end w-full">
                    <button
                      onClick={() => onCancel(a.id)}
                      className="text-xs text-destructive underline"
                    >
                      Cancel
                    </button>
                  </span>
                ) : null;
              },
            },
          ]}
          data={items as unknown as Record<string, unknown>[]}
          enableToolbar={false}
          enablePagination={false}
        />
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
  const topicItems = useMemo(
    () => topics.map((tp) => ({ value: tp.key, label: tp.label || tp.key })),
    [topics],
  );
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
          <Select value={kind} onValueChange={(v) => setKind(v as 'score_threshold' | 'incident_count')} items={KIND_ITEMS}>
            <SelectTrigger className="w-full" aria-label="Kind"><SelectValue /></SelectTrigger>
            <SelectContent>
              {KIND_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground">Topic</label>
          <Select value={topicKey} onValueChange={(v) => setTopicKey(v)} items={topicItems}>
            <SelectTrigger className="w-full" aria-label="Topic"><SelectValue placeholder="Select topic" /></SelectTrigger>
            <SelectContent>
              {topicItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
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
          <Select value={severity} onValueChange={(v) => setSeverity(v as 'low' | 'medium' | 'high')} items={SEVERITY_ITEMS}>
            <SelectTrigger className="w-full" aria-label="Severity"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEVERITY_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
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
        <DataGrid
          columns={[
            { key: 'name', label: 'Name', sortable: true },
            { key: 'kind', label: 'Kind', sortable: true },
            { key: 'topic_key', label: 'Topic', sortable: true },
            {
              key: '_trigger', label: 'Trigger', sortable: false,
              render: (_v, row) => {
                const r = row as unknown as CoachingRule;
                return (
                  <span className="text-xs text-muted-foreground">
                    {r.kind === 'score_threshold'
                      ? `score ≤ ${r.score_max}`
                      : `${r.event_type || 'any'} ≥ ${r.min_count}/${r.period_days}d`}
                  </span>
                );
              },
            },
            {
              key: 'severity', label: 'Severity', sortable: true,
              render: (v) => <span>{SEVERITY_ICON[String(v)] || '🟡'}</span>,
            },
            {
              key: 'active', label: 'Active', sortable: true,
              render: (_v, row) => {
                const r = row as unknown as CoachingRule;
                return (
                  <button onClick={() => onToggle(r)} className="text-xs underline">
                    {r.active ? 'Disable' : 'Enable'}
                  </button>
                );
              },
            },
            {
              key: '_actions', label: '', sortable: false,
              render: (_v, row) => {
                const r = row as unknown as CoachingRule;
                return (
                  <span className="inline-flex justify-end w-full">
                    <button
                      onClick={() => onDelete(r.id)}
                      className="text-xs text-destructive underline"
                    >
                      Delete
                    </button>
                  </span>
                );
              },
            },
          ]}
          data={rules as unknown as Record<string, unknown>[]}
          enableToolbar={false}
          enablePagination={false}
        />
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
    <DataGrid
      columns={[
        {
          key: 'key', label: 'Key', sortable: true,
          render: (v) => <span className="font-mono">{String(v)}</span>,
        },
        { key: 'label', label: 'Label', sortable: true },
        {
          key: 'default_message', label: 'Default message', sortable: false,
          render: (v) => <span className="text-xs text-muted-foreground">{String(v ?? '')}</span>,
        },
        {
          key: 'active', label: 'Active', sortable: true,
          render: (v) => <span>{v ? '✓' : '—'}</span>,
        },
      ]}
      data={topics as unknown as Record<string, unknown>[]}
      enableToolbar={false}
      enablePagination={false}
    />
  );
}

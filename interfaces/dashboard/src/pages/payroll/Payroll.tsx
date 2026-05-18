import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DollarSign } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';

// ── Types ─────────────────────────────────────────────────────────

interface BonusRule {
  id: number;
  name: string;
  kind: 'score_threshold' | 'incident_count';
  amount_cents: number;
  period_days: number;
  score_min: number | null;
  event_type: string | null;
  max_count: number | null;
  active: number | boolean;
}

interface DriverPaySettings {
  account_id: number;
  driver_id: string;
  base_pay_cents: number;
  opt_in: number | boolean;
  updated_at: string;
}

interface PayrollRun {
  id: number;
  account_id: number;
  period_start: string;
  period_end: string;
  status: 'draft' | 'finalized' | 'cancelled';
  total_cents: number | null;
  created_at: string;
  finalized_at: string | null;
}

interface RunItem {
  id: number;
  driver_id: string;
  driver_name: string;
  base_pay_cents: number;
  bonus_total_cents: number;
  total_cents: number;
  breakdown: { rule_id: number; name: string; kind: string; amount_cents: number; detail?: string }[];
}

interface RunDetail extends PayrollRun {
  items: RunItem[];
}

const fmtCents = (c: number | null | undefined) =>
  c == null ? '$0.00' : `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

type Tab = 'rules' | 'runs' | 'settings';

// ── Page ─────────────────────────────────────────────────────────

export default function Payroll() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('runs');
  const { user, loading: authLoading } = useAuth();
  const disabled = user?.payroll_enabled === false;

  if (authLoading) {
    return (
      <div className="space-y-4">
        <PageHeader
          icon={DollarSign}
          title={t('pages.payroll_title')}
          description={t('pages.payroll_desc_long')}
        />
        <CardSkeleton height="h-40" />
      </div>
    );
  }

  if (disabled) {
    return (
      <div className="space-y-4">
        <PageHeader
          icon={DollarSign}
          title={t('pages.payroll_title')}
          description={t('pages.payroll_desc_short')}
        />
        <div className="rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-700 dark:text-yellow-300">
          Payroll is not enabled for this account. Contact your administrator to activate Pay-for-Performance.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        icon={DollarSign}
        title={t('pages.payroll_title')}
        description={t('pages.payroll_desc_long')}
      />

      <div className="flex gap-2 border-b border-border/50">
        {(['runs', 'rules', 'settings'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm font-medium ${
              tab === t ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground'
            }`}
          >
            {t === 'runs' ? 'Runs' : t === 'rules' ? 'Bonus Rules' : 'Driver Settings'}
          </button>
        ))}
      </div>

      {tab === 'runs' && <RunsTab />}
      {tab === 'rules' && <RulesTab />}
      {tab === 'settings' && <SettingsTab />}
    </div>
  );
}

// ── Runs Tab ─────────────────────────────────────────────────────

function RunsTab() {
  const [runs, setRuns] = useState<PayrollRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');

  const load = () => {
    setLoading(true);
    apiJSON<PayrollRun[]>('/payroll/runs')
      .then(setRuns)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const create = async () => {
    if (!periodStart || !periodEnd) {
      setError('Pick start + end dates');
      return;
    }
    setError('');
    try {
      await apiJSON('/payroll/runs', {
        method: 'POST',
        body: { period_start: periodStart, period_end: periodEnd },
      });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'create failed');
    }
  };

  const openRun = async (id: number) => {
    try {
      const d = await apiJSON<RunDetail>(`/payroll/runs/${id}`);
      setSelected(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'open failed');
    }
  };

  const finalize = async (id: number) => {
    if (!confirm('Finalize this run? It cannot be edited afterward.')) return;
    await apiFetch(`/payroll/runs/${id}/finalize`, { method: 'POST' });
    load();
    if (selected?.id === id) openRun(id);
  };

  const cancel = async (id: number) => {
    if (!confirm('Cancel this run?')) return;
    await apiFetch(`/payroll/runs/${id}/cancel`, { method: 'POST' });
    load();
    if (selected?.id === id) openRun(id);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">
          Period start
          <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
            className="ml-2 border rounded px-2 py-1 bg-background" />
        </label>
        <label className="text-sm">
          Period end
          <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
            className="ml-2 border rounded px-2 py-1 bg-background" />
        </label>
        <button onClick={create} className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm">
          Create draft run
        </button>
      </div>

      {error && <div className="text-red-500 text-sm">{error}</div>}
      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}

      <table className="w-full text-sm">
        <thead className="text-muted-foreground border-b">
          <tr>
            <th className="text-left p-2">Period</th>
            <th className="text-left p-2">Status</th>
            <th className="text-right p-2">Total</th>
            <th className="text-left p-2">Created</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-border/30 hover:bg-muted/30">
              <td className="p-2">{r.period_start} → {r.period_end}</td>
              <td className="p-2">
                <span className={`px-2 py-0.5 rounded text-xs ${
                  r.status === 'finalized' ? 'bg-green-500/20 text-green-700'
                  : r.status === 'cancelled' ? 'bg-gray-500/20 text-gray-600'
                  : 'bg-yellow-500/20 text-yellow-700'
                }`}>{r.status}</span>
              </td>
              <td className="p-2 text-right">{fmtCents(r.total_cents)}</td>
              <td className="p-2 text-xs text-muted-foreground">{r.created_at?.slice(0, 16)}</td>
              <td className="p-2 text-right">
                <button onClick={() => openRun(r.id)} className="text-primary text-xs hover:underline mr-2">
                  Detail
                </button>
                {r.status === 'draft' && (
                  <>
                    <button onClick={() => finalize(r.id)} className="text-green-600 text-xs hover:underline mr-2">
                      Finalize
                    </button>
                    <button onClick={() => cancel(r.id)} className="text-red-500 text-xs hover:underline">
                      Cancel
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selected && (
        <div className="border rounded p-3 space-y-2">
          <div className="flex justify-between">
            <h3 className="font-semibold">
              Run #{selected.id}: {selected.period_start} → {selected.period_end}
            </h3>
            <button onClick={() => setSelected(null)} className="text-sm text-muted-foreground hover:underline">
              Close
            </button>
          </div>
          <table className="w-full text-sm">
            <thead className="text-muted-foreground border-b">
              <tr>
                <th className="text-left p-1">Driver</th>
                <th className="text-right p-1">Base</th>
                <th className="text-right p-1">Bonus</th>
                <th className="text-right p-1">Total</th>
                <th className="text-left p-1">Breakdown</th>
              </tr>
            </thead>
            <tbody>
              {selected.items.map((it) => (
                <tr key={it.id} className="border-b border-border/30">
                  <td className="p-1">{it.driver_name || it.driver_id}</td>
                  <td className="p-1 text-right">{fmtCents(it.base_pay_cents)}</td>
                  <td className="p-1 text-right">{fmtCents(it.bonus_total_cents)}</td>
                  <td className="p-1 text-right font-semibold">{fmtCents(it.total_cents)}</td>
                  <td className="p-1 text-xs text-muted-foreground">
                    {(it.breakdown || []).map((b) => `${b.name} (${fmtCents(b.amount_cents)})`).join(', ') || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Rules Tab ────────────────────────────────────────────────────

function RulesTab() {
  const [rules, setRules] = useState<BonusRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<Partial<BonusRule>>({
    name: '', kind: 'score_threshold', amount_cents: 5000,
    period_days: 30, score_min: 80, max_count: null, event_type: '', active: true,
  });

  const load = () => {
    setLoading(true);
    apiJSON<BonusRule[]>('/payroll/rules')
      .then(setRules)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const save = async () => {
    setError('');
    try {
      const body: Record<string, unknown> = {
        name: draft.name,
        kind: draft.kind,
        amount_cents: Number(draft.amount_cents),
        period_days: Number(draft.period_days || 30),
        active: !!draft.active,
      };
      if (draft.kind === 'score_threshold') body.score_min = Number(draft.score_min);
      if (draft.kind === 'incident_count') {
        body.max_count = Number(draft.max_count);
        if (draft.event_type) body.event_type = draft.event_type;
      }
      await apiJSON('/payroll/rules', { method: 'POST', body });
      setDraft({
        name: '', kind: 'score_threshold', amount_cents: 5000,
        period_days: 30, score_min: 80, max_count: null, event_type: '', active: true,
      });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'save failed');
    }
  };

  const remove = async (id: number) => {
    if (!confirm('Delete rule?')) return;
    await apiFetch(`/payroll/rules/${id}`, { method: 'DELETE' });
    load();
  };

  const toggle = async (r: BonusRule) => {
    await apiJSON(`/payroll/rules/${r.id}`, { method: 'PUT', body: { active: !r.active } });
    load();
  };

  return (
    <div className="space-y-4">
      <div className="border rounded p-3 space-y-2">
        <h3 className="font-semibold text-sm">Add Bonus Rule</h3>
        {error && <div className="text-red-500 text-sm">{error}</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <input placeholder="Name"
            value={draft.name ?? ''} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className="border rounded px-2 py-1 bg-background" />
          <select value={draft.kind ?? 'score_threshold'}
            onChange={(e) => setDraft({ ...draft, kind: e.target.value as BonusRule['kind'] })}
            className="border rounded px-2 py-1 bg-background">
            <option value="score_threshold">Score ≥ threshold</option>
            <option value="incident_count">Incidents ≤ max</option>
          </select>
          <input type="number" placeholder="Amount (cents)"
            value={draft.amount_cents ?? 0}
            onChange={(e) => setDraft({ ...draft, amount_cents: Number(e.target.value) })}
            className="border rounded px-2 py-1 bg-background" />
          <input type="number" placeholder="Period (days)"
            value={draft.period_days ?? 30}
            onChange={(e) => setDraft({ ...draft, period_days: Number(e.target.value) })}
            className="border rounded px-2 py-1 bg-background" />
          {draft.kind === 'score_threshold' ? (
            <input type="number" placeholder="Min score"
              value={draft.score_min ?? 0}
              onChange={(e) => setDraft({ ...draft, score_min: Number(e.target.value) })}
              className="border rounded px-2 py-1 bg-background" />
          ) : (
            <>
              <input type="number" placeholder="Max count"
                value={draft.max_count ?? 0}
                onChange={(e) => setDraft({ ...draft, max_count: Number(e.target.value) })}
                className="border rounded px-2 py-1 bg-background" />
              <input placeholder="Event type (e.g. harshBraking)"
                value={draft.event_type ?? ''}
                onChange={(e) => setDraft({ ...draft, event_type: e.target.value })}
                className="border rounded px-2 py-1 bg-background" />
            </>
          )}
        </div>
        <button onClick={save} className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm">
          Add Rule
        </button>
      </div>

      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}
      <table className="w-full text-sm">
        <thead className="text-muted-foreground border-b">
          <tr>
            <th className="text-left p-2">Name</th>
            <th className="text-left p-2">Kind</th>
            <th className="text-left p-2">Threshold</th>
            <th className="text-right p-2">Amount</th>
            <th className="text-center p-2">Active</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id} className="border-b border-border/30">
              <td className="p-2">{r.name}</td>
              <td className="p-2 text-xs">{r.kind}</td>
              <td className="p-2 text-xs">
                {r.kind === 'score_threshold'
                  ? `score ≥ ${r.score_min}`
                  : `${r.event_type || 'any'} ≤ ${r.max_count}`}
              </td>
              <td className="p-2 text-right">{fmtCents(r.amount_cents)}</td>
              <td className="p-2 text-center">
                <input type="checkbox" checked={!!r.active} onChange={() => toggle(r)} />
              </td>
              <td className="p-2 text-right">
                <button onClick={() => remove(r.id)} className="text-red-500 text-xs hover:underline">
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Settings Tab ─────────────────────────────────────────────────

function SettingsTab() {
  const [rows, setRows] = useState<DriverPaySettings[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [driverId, setDriverId] = useState('');
  const [basePay, setBasePay] = useState(0);
  const [optIn, setOptIn] = useState(true);

  const load = () => {
    setLoading(true);
    apiJSON<DriverPaySettings[]>('/payroll/settings')
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const save = async () => {
    if (!driverId.trim()) {
      setError('driver_id required');
      return;
    }
    setError('');
    try {
      await apiJSON(`/payroll/settings/${encodeURIComponent(driverId)}`, {
        method: 'PUT',
        body: { base_pay_cents: Number(basePay), opt_in: optIn },
      });
      setDriverId('');
      setBasePay(0);
      setOptIn(true);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'save failed');
    }
  };

  return (
    <div className="space-y-4">
      <div className="border rounded p-3 space-y-2">
        <h3 className="font-semibold text-sm">Set Driver Pay</h3>
        {error && <div className="text-red-500 text-sm">{error}</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <input placeholder="Driver ID (Samsara)" value={driverId}
            onChange={(e) => setDriverId(e.target.value)}
            className="border rounded px-2 py-1 bg-background" />
          <input type="number" placeholder="Base pay (cents)" value={basePay}
            onChange={(e) => setBasePay(Number(e.target.value))}
            className="border rounded px-2 py-1 bg-background" />
          <label className="text-sm flex items-center gap-2">
            <input type="checkbox" checked={optIn} onChange={(e) => setOptIn(e.target.checked)} />
            Opted in
          </label>
          <button onClick={save} className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm">
            Save
          </button>
        </div>
      </div>

      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}

      <table className="w-full text-sm">
        <thead className="text-muted-foreground border-b">
          <tr>
            <th className="text-left p-2">Driver ID</th>
            <th className="text-right p-2">Base Pay</th>
            <th className="text-center p-2">Opted In</th>
            <th className="text-left p-2">Updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.driver_id} className="border-b border-border/30">
              <td className="p-2">{r.driver_id}</td>
              <td className="p-2 text-right">{fmtCents(r.base_pay_cents)}</td>
              <td className="p-2 text-center">{r.opt_in ? '✓' : '—'}</td>
              <td className="p-2 text-xs text-muted-foreground">{r.updated_at?.slice(0, 16)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

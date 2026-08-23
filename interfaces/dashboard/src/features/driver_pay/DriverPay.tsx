import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DollarSign, FileText } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { useAuth } from '../../context/AuthContext';
import { useTimezone } from '../../hooks/useTimezone';
import { todayInTimeZone } from '../../utils/datetime';
import { PageHeader, CardSkeleton } from '../../components/shell';
import DataGrid from '../../components/datagrid';
import { Button } from '../../components/ui/button';
import StatementDrawer from './StatementDrawer';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';

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
  pay_model?: string;
  pay_rate?: number | null;
  updated_at: string;
}

interface DriverPayRun {
  id: number;
  account_id: number;
  period_start: string;
  period_end: string;
  status: 'draft' | 'finalized' | 'cancelled';
  total_cents: number | null;
  created_at: string;
  finalized_at: string | null;
}

export interface StatementLoadLine {
  date: string; load_number: string; route: string;
  rate_cents: number; pay_cents: number;
}
export interface StatementAddition {
  date: string; kind: string; amount_cents: number;
  notes: string; load_number: string;
}
export interface StatementDeduction {
  date: string; kind: string; amount_cents: number;
  notes: string; recurring?: boolean;
}
export interface Statement {
  driver_user_id?: number | null;
  zero_pay_loads?: number;
  loads?: StatementLoadLine[];
  additions?: StatementAddition[];
  deductions?: StatementDeduction[];
  base_pay_cents?: number;
  load_earnings_cents?: number;
  extras_cents?: number;
  bonus_total_cents?: number;
  total_cents?: number;
  deductions_cents?: number;
  net_cents?: number;
}

interface RunItem {
  id: number;
  driver_id: string;
  driver_name: string;
  base_pay_cents: number;
  bonus_total_cents: number;
  total_cents: number;
  breakdown: { rule_id: number | null; name: string; kind: string; amount_cents: number; detail?: string }[];
  statement?: Statement;
  deductions_cents?: number;
  net_cents?: number;
}

interface RunDetail extends DriverPayRun {
  items: RunItem[];
}

const fmtCents = (c: number | null | undefined) =>
  c == null ? '$0.00' : `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// A new run defaults to the PREVIOUS calendar month — the dominant driver-pay
// period — so the operator confirms one dropdown instead of typing two dates.
// Computed from the account-tz "today" (never UTC) to avoid month-boundary drift.
function lastCalendarMonth(tz: string): { start: string; end: string } {
  const [y, m] = todayInTimeZone(tz).split('-').map(Number);   // m = 1..12
  const py = m === 1 ? y - 1 : y;
  const pm = m === 1 ? 12 : m - 1;                             // previous month, 1-indexed
  const mm = String(pm).padStart(2, '0');
  const lastDay = new Date(py, pm, 0).getDate();              // last day of month pm
  return { start: `${py}-${mm}-01`, end: `${py}-${mm}-${String(lastDay).padStart(2, '0')}` };
}

// Whole-run export — one CSV row per driver so accounting can hand the
// batch to a bookkeeper / import it, instead of opening each statement.
// Amounts in dollars (bookkeeping convention), not the wire's cents.
function exportRunCsv(run: RunDetail) {
  const esc = (s: unknown) => `"${String(s ?? '').replace(/"/g, '""')}"`;
  const dollars = (c: number | null | undefined) => (Number(c || 0) / 100).toFixed(2);
  const header = ['Driver', 'Driver ID', 'Base', 'Load earnings', 'Extras', 'Bonuses', 'Gross', 'Deductions', 'Net'];
  const rows = (run.items || []).map((it) => {
    const st = it.statement || {};
    return [
      it.driver_name || it.driver_id,
      it.driver_id,
      dollars(st.base_pay_cents ?? it.base_pay_cents),
      dollars(st.load_earnings_cents),
      dollars(st.extras_cents),
      dollars(it.bonus_total_cents),
      dollars(st.total_cents ?? it.total_cents),
      dollars(it.deductions_cents ?? st.deductions_cents),
      dollars(it.net_cents ?? st.net_cents ?? it.total_cents),
    ];
  });
  const csv = [header, ...rows].map((r) => r.map(esc).join(',')).join('\r\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `driver-pay-run-${run.id}_${run.period_start}_to_${run.period_end}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const RULE_KIND_ITEMS: { value: BonusRule['kind']; label: string }[] = [
  { value: 'score_threshold', label: 'Score ≥ threshold' },
  { value: 'incident_count', label: 'Incidents ≤ max' },
];

type Tab = 'rules' | 'runs' | 'settings';

// ── Page ─────────────────────────────────────────────────────────

export default function DriverPay() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('runs');
  const { user, loading: authLoading } = useAuth();
  const disabled = user?.payroll_enabled === false;

  if (authLoading) {
    return (
      <div className="space-y-4">
        <PageHeader
          icon={DollarSign}
          title={t('pages.driver_pay_title')}
          description={t('pages.driver_pay_desc_long')}
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
          title={t('pages.driver_pay_title')}
          description={t('pages.driver_pay_desc_short')}
        />
        <div className={`rounded-lg border px-4 py-3 text-sm ${toneClasses('warn')}`}>
          Driver Pay is not enabled for this account. Contact your administrator to activate the Accounting module.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        icon={DollarSign}
        title={t('pages.driver_pay_title')}
        description={t('pages.driver_pay_desc_long')}
      />

      <div className="flex gap-2 border-b border-border">
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
  const [runs, setRuns] = useState<DriverPayRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [statementItem, setStatementItem] = useState<RunItem | null>(null);
  const tz = useTimezone();
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  // Seed last-month defaults once the account tz resolves; never clobber a
  // date the operator has already picked (the `|| prev` guard).
  useEffect(() => {
    const d = lastCalendarMonth(tz);
    setPeriodStart((p) => p || d.start);
    setPeriodEnd((p) => p || d.end);
  }, [tz]);

  const load = () => {
    setLoading(true);
    apiJSON<DriverPayRun[]>('/driver-pay/runs')
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
      await apiJSON('/driver-pay/runs', {
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
      const d = await apiJSON<RunDetail>(`/driver-pay/runs/${id}`);
      setSelected(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'open failed');
    }
  };

  const finalize = async (id: number) => {
    if (!confirm('Finalize this run? It cannot be edited afterward.')) return;
    await apiFetch(`/driver-pay/runs/${id}/finalize`, { method: 'POST' });
    load();
    if (selected?.id === id) openRun(id);
  };

  const cancel = async (id: number) => {
    if (!confirm('Cancel this run?')) return;
    await apiFetch(`/driver-pay/runs/${id}/cancel`, { method: 'POST' });
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
        <button onClick={create} className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm min-h-tap">
          Create draft run
        </button>
      </div>

      {error && <div className="text-danger text-sm">{error}</div>}
      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}

      <DataGrid
        columns={[
          {
            key: 'period_start', label: 'Period', sortable: true,
            render: (_v, row) => {
              const r = row as unknown as DriverPayRun;
              return <span>{r.period_start} → {r.period_end}</span>;
            },
          },
          {
            key: 'status', label: 'Status', sortable: true,
            render: (v) => {
              const s = String(v);
              return (
                <span className={`px-2 py-0.5 rounded text-xs border ${
                  toneClasses(s === 'finalized' ? 'ok' : s === 'cancelled' ? 'neutral' : 'warn')
                }`}>{s}</span>
              );
            },
          },
          {
            key: 'total_cents', label: 'Total', sortable: true,
            render: (v) => <span className="tabular-nums">{fmtCents(Number(v))}</span>,
          },
          {
            key: 'created_at', label: 'Created', sortable: true,
            render: (v) => (
              <span className="text-xs text-muted-foreground tabular-nums">
                {String(v ?? '').slice(0, 16)}
              </span>
            ),
          },
          {
            key: '_actions', label: '', sortable: false,
            render: (_v, row) => {
              const r = row as unknown as DriverPayRun;
              return (
                <span className="inline-flex justify-end gap-2 w-full">
                  <button onClick={() => openRun(r.id)} className="text-primary text-xs hover:underline py-1 -my-1 min-h-tap">
                    Detail
                  </button>
                  {r.status === 'draft' && (
                    <>
                      <button onClick={() => finalize(r.id)} className="text-ok text-xs hover:underline py-1 -my-1 min-h-tap">
                        Finalize
                      </button>
                      <button onClick={() => cancel(r.id)} className="text-danger text-xs hover:underline py-1 -my-1 min-h-tap">
                        Cancel
                      </button>
                    </>
                  )}
                </span>
              );
            },
          },
        ]}
        data={runs as unknown as Record<string, unknown>[]}
        enableToolbar={false}
        enablePagination={false}
      />

      {selected && (
        <div className="border rounded p-3 space-y-2">
          <div className="flex justify-between">
            <h3 className="font-semibold">
              Run #{selected.id}: {selected.period_start} → {selected.period_end}
            </h3>
            <div className="flex items-center gap-3">
              <button onClick={() => exportRunCsv(selected)}
                className="text-primary text-sm hover:underline py-1 -my-1 min-h-tap">
                Export CSV
              </button>
              <button onClick={() => setSelected(null)} className="text-sm text-muted-foreground hover:underline py-1 -my-1 min-h-tap">
                Close
              </button>
            </div>
          </div>
          <DataGrid
            columns={[
              {
                key: 'driver_name', label: 'Driver', sortable: true,
                render: (_v, row) => {
                  const it = row as unknown as RunItem;
                  return <span>{it.driver_name || it.driver_id}</span>;
                },
              },
              { key: 'base_pay_cents', label: 'Base', sortable: true,
                render: (v) => <span className="tabular-nums">{fmtCents(Number(v))}</span> },
              {
                key: '_loads', label: 'Loads', sortable: true,
                sortKey: (row) => {
                  const b = (row as unknown as { breakdown?: { kind: string; amount_cents: number }[] }).breakdown || [];
                  return b.find((x) => x.kind === 'load_earnings')?.amount_cents ?? 0;
                },
                render: (_v, row) => {
                  const b = (row as unknown as { breakdown?: { kind: string; amount_cents: number }[] }).breakdown || [];
                  const e = b.find((x) => x.kind === 'load_earnings');
                  return e ? <span className="tabular-nums">{fmtCents(e.amount_cents)}</span>
                           : <span className="text-muted-foreground">—</span>;
                },
              },
              {
                key: '_extras', label: 'Extras', sortable: true,
                sortKey: (row) => {
                  const b = (row as unknown as { breakdown?: { kind: string; amount_cents: number }[] }).breakdown || [];
                  return b.find((x) => x.kind === 'extra_items')?.amount_cents ?? 0;
                },
                render: (_v, row) => {
                  const b = (row as unknown as { breakdown?: { kind: string; amount_cents: number }[] }).breakdown || [];
                  const e = b.find((x) => x.kind === 'extra_items');
                  return e ? <span className="tabular-nums">{fmtCents(e.amount_cents)}</span>
                           : <span className="text-muted-foreground">—</span>;
                },
              },
              { key: 'bonus_total_cents', label: 'Bonus', sortable: true,
                render: (v) => <span className="tabular-nums">{fmtCents(Number(v))}</span> },
              { key: 'total_cents', label: 'Gross', sortable: true,
                render: (v) => <span className="tabular-nums">{fmtCents(Number(v))}</span> },
              { key: 'deductions_cents', label: 'Deductions', sortable: true,
                render: (v) => (Number(v) ? <span className="tabular-nums text-danger">−{fmtCents(Number(v))}</span> : <span className="text-muted-foreground">—</span>) },
              { key: 'net_cents', label: 'Net', sortable: true,
                render: (v, row) => {
                  const it = row as unknown as RunItem;
                  const net = it.net_cents ?? it.total_cents;
                  return <span className="font-semibold tabular-nums">{fmtCents(Number(net))}</span>;
                } },
              {
                key: 'breakdown', label: 'Breakdown', sortable: false,
                render: (_v, row) => {
                  const it = row as unknown as RunItem;
                  const s = (it.breakdown || []).map(b => `${b.name} (${fmtCents(b.amount_cents)})`).join(', ');
                  return <span className="text-xs text-muted-foreground">{s || '—'}</span>;
                },
              },
              {
                key: '_statement', label: '', sortable: false,
                render: (_v, row) => {
                  const it = row as unknown as RunItem;
                  return (
                    <Button
                      type="button" variant="outline" size="xs"
                      onClick={() => setStatementItem(it)}
                    >
                      <FileText size={12} className="mr-1" /> Statement
                    </Button>
                  );
                },
              },
            ]}
            data={selected.items as unknown as Record<string, unknown>[]}
            enableToolbar={false}
            enablePagination={false}
          />
        </div>
      )}

      {statementItem && selected && (
        <StatementDrawer
          driver={statementItem.driver_name || statementItem.driver_id}
          period={`${selected.period_start} → ${selected.period_end}`}
          statement={statementItem.statement ?? {}}
          runId={selected.id}
          runStatus={selected.status}
          onChanged={async () => {
            // Recompute pulled fresh items → re-open the run and re-point
            // the drawer at this driver's refreshed statement.
            const d = await apiJSON<RunDetail>(`/driver-pay/runs/${selected.id}`);
            setSelected(d);
            const again = (d.items || []).find(
              (it) => it.driver_id === statementItem.driver_id,
            );
            setStatementItem(again ?? null);
            load();
          }}
          onClose={() => setStatementItem(null)}
        />
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
    apiJSON<BonusRule[]>('/driver-pay/config/rules')
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
      await apiJSON('/driver-pay/config/rules', { method: 'POST', body });
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
    await apiFetch(`/driver-pay/config/rules/${id}`, { method: 'DELETE' });
    load();
  };

  const toggle = async (r: BonusRule) => {
    await apiJSON(`/driver-pay/config/rules/${r.id}`, { method: 'PUT', body: { active: !r.active } });
    load();
  };

  return (
    <div className="space-y-4">
      <div className="border rounded p-3 space-y-2">
        <h3 className="font-semibold text-sm">Add Bonus Rule</h3>
        {error && <div className="text-danger text-sm">{error}</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <input placeholder="Name"
            value={draft.name ?? ''} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className="border rounded px-2 py-1 bg-background" />
          <Select
            value={draft.kind ?? 'score_threshold'}
            onValueChange={(v) => setDraft({ ...draft, kind: v as BonusRule['kind'] })}
            items={RULE_KIND_ITEMS}
          >
            <SelectTrigger className="w-full" aria-label="Rule kind"><SelectValue /></SelectTrigger>
            <SelectContent>
              {RULE_KIND_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
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
        <button onClick={save} className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm min-h-tap">
          Add Rule
        </button>
      </div>

      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}
      <DataGrid
        columns={[
          { key: 'name', label: 'Name', sortable: true },
          {
            key: 'kind', label: 'Kind', sortable: true,
            render: (v) => <span className="text-xs">{String(v)}</span>,
          },
          {
            key: '_threshold', label: 'Threshold', sortable: false,
            render: (_v, row) => {
              const r = row as unknown as BonusRule;
              return (
                <span className="text-xs">
                  {r.kind === 'score_threshold'
                    ? `score ≥ ${r.score_min}`
                    : `${r.event_type || 'any'} ≤ ${r.max_count}`}
                </span>
              );
            },
          },
          {
            key: 'amount_cents', label: 'Amount', sortable: true,
            render: (v) => <span className="tabular-nums">{fmtCents(Number(v))}</span>,
          },
          {
            key: 'active', label: 'Active', sortable: true,
            render: (_v, row) => {
              const r = row as unknown as BonusRule;
              return (
                <span className="inline-flex justify-center w-full">
                  <input type="checkbox" checked={!!r.active} onChange={() => toggle(r)} />
                </span>
              );
            },
          },
          {
            key: '_actions', label: '', sortable: false,
            render: (_v, row) => {
              const r = row as unknown as BonusRule;
              return (
                <span className="inline-flex justify-end w-full">
                  <button onClick={() => remove(r.id)} className="text-destructive text-xs hover:underline py-1 -my-1 min-h-tap">
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
    </div>
  );
}

// ── Settings Tab ─────────────────────────────────────────────────

interface SamsaraDriver { samsara_driver_id: string; name: string }

function SettingsTab() {
  const [rows, setRows] = useState<DriverPaySettings[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [driverId, setDriverId] = useState('');
  const [basePay, setBasePay] = useState('');   // dollars (string; '' = 0)
  const [optIn, setOptIn] = useState(true);
  // Per-load earnings model for loads with no stored pay:
  // percentage-of-rate or per-mile; '' = no per-load math.
  const [payModel, setPayModel] = useState('');
  const [payRate, setPayRate] = useState('');
  // Roster for the driver picker — beats hand-typing an opaque Samsara id.
  // Soft-fails (empty) if Samsara isn't connected; the picker then just
  // shows nothing and the operator can still type via "Other".
  const [drivers, setDrivers] = useState<SamsaraDriver[]>([]);
  useEffect(() => {
    apiJSON<{ drivers: SamsaraDriver[] }>('/drivers/samsara')
      .then((r) => setDrivers(r.drivers ?? []))
      .catch(() => setDrivers([]));
  }, []);

  const load = () => {
    setLoading(true);
    apiJSON<DriverPaySettings[]>('/driver-pay/settings')
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
      await apiJSON(`/driver-pay/settings/${encodeURIComponent(driverId)}`, {
        method: 'PUT',
        body: {
          base_pay_cents: Math.round((Number(basePay) || 0) * 100), opt_in: optIn,
          pay_model: payModel,
          pay_rate: payRate.trim() === '' ? null : Number(payRate),
        },
      });
      setDriverId('');
      setBasePay('');
      setOptIn(true);
      setPayModel('');
      setPayRate('');
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'save failed');
    }
  };

  return (
    <div className="space-y-4">
      <div className="border rounded p-3 space-y-2">
        <h3 className="font-semibold text-sm">Set Driver Pay</h3>
        {error && <div className="text-danger text-sm">{error}</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <div className="flex flex-col gap-0.5">
            <input placeholder="Driver (name or Samsara ID)" value={driverId}
              list="dp-driver-roster"
              onChange={(e) => setDriverId(e.target.value)}
              className="border rounded px-2 py-1 bg-background text-foreground" />
            <datalist id="dp-driver-roster">
              {drivers.map((d) => (
                <option key={d.samsara_driver_id} value={d.samsara_driver_id}>
                  {d.name}
                </option>
              ))}
            </datalist>
            {(() => {
              const match = drivers.find((d) => d.samsara_driver_id === driverId.trim());
              return match ? (
                <span className="text-2xs text-muted-foreground">→ {match.name}</span>
              ) : null;
            })()}
          </div>
          <input type="number" min="0" step="0.01" placeholder="Base pay ($)" value={basePay}
            onChange={(e) => setBasePay(e.target.value)}
            className="border rounded px-2 py-1 bg-background" />
          <select
            value={payModel}
            onChange={(e) => setPayModel(e.target.value)}
            aria-label="Pay model"
            className="border rounded px-2 py-1 bg-background text-foreground"
          >
            <option value="">No per-load pay</option>
            <option value="percentage">% of load rate</option>
            <option value="per_mile">Per mile</option>
          </select>
          <input type="number" placeholder="Rate (28 = 28% / $ per mile)" value={payRate}
            onChange={(e) => setPayRate(e.target.value)}
            disabled={!payModel}
            className="border rounded px-2 py-1 bg-background disabled:opacity-50" />
          <label className="text-sm flex items-center gap-2">
            <input type="checkbox" checked={optIn} onChange={(e) => setOptIn(e.target.checked)} />
            Opted in
          </label>
          <button onClick={save} className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm min-h-tap">
            Save
          </button>
        </div>
      </div>

      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}

      <DataGrid
        columns={[
          {
            key: 'driver_id', label: 'Driver', sortable: true,
            render: (v) => {
              const id = String(v ?? '');
              const match = drivers.find((d) => d.samsara_driver_id === id);
              return match
                ? <span>{match.name} <span className="text-2xs text-muted-foreground font-mono">{id}</span></span>
                : <span className="font-mono text-xs">{id}</span>;
            },
          },
          {
            key: 'base_pay_cents', label: 'Base Pay', sortable: true,
            render: (v) => <span className="tabular-nums">{fmtCents(Number(v))}</span>,
          },
          {
            key: 'pay_model', label: 'Pay Model', sortable: true,
            render: (v, row) => {
              const m = String(v || '');
              if (!m) return <span className="text-muted-foreground">—</span>;
              const r = (row as unknown as DriverPaySettings).pay_rate;
              return <span>{m === 'percentage' ? `${r ?? '?'}% of rate` : `$${r ?? '?'}/mi`}</span>;
            },
          },
          {
            key: 'opt_in', label: 'Opted In', sortable: true,
            render: (v) => (
              <span className="inline-flex justify-center w-full">{v ? '✓' : '—'}</span>
            ),
          },
          {
            key: 'updated_at', label: 'Updated', sortable: true,
            render: (v) => (
              <span className="text-xs text-muted-foreground tabular-nums">
                {String(v ?? '').slice(0, 16)}
              </span>
            ),
          },
        ]}
        data={rows as unknown as Record<string, unknown>[]}
        enableToolbar={false}
        enablePagination={false}
      />
    </div>
  );
}


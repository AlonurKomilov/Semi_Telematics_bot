import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Trophy } from 'lucide-react';
import { apiJSON } from '../../api/client';
import {
  PageHeader,
  ErrorState,
  CardSkeleton,
  EmptyState,
} from '../../components/shell';

// ── Types ─────────────────────────────────────────────────────────

interface ScoreRule {
  id: string;
  label: string;
  category: 'safety' | 'fleet' | 'efficiency' | string;
  pillar?: 'safety' | 'efficiency' | 'compliance' | string;
  kind: 'bonus' | 'penalty';
  default_points: number;
  default_cap: number | null;
  points: number;
  cap: number | null;
  enabled: boolean;
  overridden: boolean;
 // curve metadata (read-only kind, editable anchors)
  curve_kind?: string | null;
  curve_x_zero?: number | null;
  curve_x_max?:  number | null;
  curve_y_max?:  number | null;
  default_curve_x_zero?: number | null;
  default_curve_x_max?:  number | null;
  default_curve_y_max?:  number | null;
}

interface RulesResponse { rules: ScoreRule[]; count: number; }

// Per-row local edit buffer. Curve fields default to current server
// values; null is a valid value (= "clear override / use code default").
interface RuleDraft {
  points: number;
  cap: number | null;
  enabled: boolean;
  curve_x_zero: number | null;
  curve_x_max:  number | null;
  curve_y_max:  number | null;
}

function draftFromRule(r: ScoreRule): RuleDraft {
  return {
    points: r.points,
    cap: r.cap,
    enabled: r.enabled,
    curve_x_zero: r.curve_x_zero ?? null,
    curve_x_max:  r.curve_x_max  ?? null,
    curve_y_max:  r.curve_y_max  ?? null,
  };
}

// ── Category meta ────────────────────────────────────────────────

const CAT_META: Record<string, { color: string; bg: string; icon: string; label: string }> = {
  safety:     { color: '#ef4444', bg: '#ef444422', icon: '🛡', label: 'Safety' },
  fleet:      { color: '#3b82f6', bg: '#3b82f622', icon: '🛠', label: 'Fleet' },
  efficiency: { color: '#22c55e', bg: '#22c55e22', icon: '⚡', label: 'Efficiency' },
};

const CATEGORIES = ['all', 'safety', 'fleet', 'efficiency'] as const;
type CategoryFilter = (typeof CATEGORIES)[number];

// ── Page ─────────────────────────────────────────────────────────

export default function ScorecardRules() {
  const { t } = useTranslation();
  const [rules, setRules]     = useState<ScoreRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');
  const [savingId, setSavingId] = useState<string | null>(null);
  const [filter, setFilter]   = useState<CategoryFilter>('all');
  const [search, setSearch]   = useState('');
  // Local edit buffer keyed by rule_id; absent ⇒ row matches server state
  const [drafts, setDrafts]   = useState<Record<string, RuleDraft>>({});

  const load = () => {
    setLoading(true);
    setError('');
    apiJSON<RulesResponse>('/admin/scorecard-rules')
      .then((d) => setRules(d.rules || []))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load rules'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const filtered = useMemo(() => {
    let list = rules;
    if (filter !== 'all') list = list.filter((r) => r.category === filter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((r) => r.label.toLowerCase().includes(q) || r.id.toLowerCase().includes(q));
    }
    return list;
  }, [rules, filter, search]);

  const grouped = useMemo(() => {
    const out: Record<string, ScoreRule[]> = {};
    for (const r of filtered) (out[r.category] ||= []).push(r);
    return out;
  }, [filtered]);

  const stats = useMemo(() => ({
    total:      rules.length,
    enabled:    rules.filter((r) => r.enabled).length,
    overridden: rules.filter((r) => r.overridden).length,
  }), [rules]);

  // ── Mutations ───────────────────────────────────────────────────

  const draftFor = (r: ScoreRule) => drafts[r.id] ?? draftFromRule(r);

  const isDirty = (r: ScoreRule) => {
    const d = drafts[r.id];
    if (!d) return false;
    return (
      d.points  !== r.points ||
      d.cap     !== r.cap    ||
      d.enabled !== r.enabled ||
      d.curve_x_zero !== (r.curve_x_zero ?? null) ||
      d.curve_x_max  !== (r.curve_x_max  ?? null) ||
      d.curve_y_max  !== (r.curve_y_max  ?? null)
    );
  };

  const setDraft = (id: string, patch: Partial<RuleDraft>) => {
    setDrafts((prev) => {
      const r = rules.find((x) => x.id === id);
      if (!r) return prev;
      const cur = prev[id] ?? draftFromRule(r);
      return { ...prev, [id]: { ...cur, ...patch } };
    });
  };

  const saveRule = async (r: ScoreRule) => {
    const d = draftFor(r);
    setSavingId(r.id);
    try {
      await apiJSON(`/admin/scorecard-rules/${encodeURIComponent(r.id)}`, {
        method: 'PUT',
        body: {
          points: d.points,
          cap: d.cap,
          enabled: d.enabled,
          curve_x_zero: d.curve_x_zero,
          curve_x_max:  d.curve_x_max,
          curve_y_max:  d.curve_y_max,
        },
      });
      setDrafts((prev) => { const { [r.id]: _, ...rest } = prev; return rest; });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSavingId(null);
    }
  };

  const resetRule = async (r: ScoreRule) => {
    if (!r.overridden) return;
    if (!window.confirm(`Reset "${r.label}" to defaults?`)) return;
    setSavingId(r.id);
    try {
      await apiJSON(`/admin/scorecard-rules/${encodeURIComponent(r.id)}`, { method: 'DELETE' });
      setDrafts((prev) => { const { [r.id]: _, ...rest } = prev; return rest; });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reset failed');
    } finally {
      setSavingId(null);
    }
  };

  // ── Render ──────────────────────────────────────────────────────

  return (
    <div>
      <PageHeader
        icon={Trophy}
        title={t('pages.scorecard_rules_title')}
        description={t('pages.scorecard_rules_desc')}
      />

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <Kpi label="Rules" value={stats.total.toString()} />
        <Kpi label="Enabled" value={stats.enabled.toString()} color="#22c55e" />
        <Kpi
          label="Customised"
          value={stats.overridden.toString()}
          color={stats.overridden > 0 ? '#3b82f6' : '#6b7280'}
        />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex gap-1 bg-muted/50 p-1 rounded-lg">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => setFilter(c)}
              className={`px-3 py-1 rounded text-xs font-medium transition ${
                filter === c
                  ? 'bg-card shadow-sm text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {c === 'all' ? 'All' : CAT_META[c]?.label ?? c}
            </button>
          ))}
        </div>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('forms.search_rules_placeholder')}
          className="flex-1 min-w-[200px] bg-muted border border-border rounded px-3 py-1.5 text-sm"
        />
      </div>

      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      {loading ? (
        <div className="space-y-3">
          <CardSkeleton height="h-32" />
          <CardSkeleton height="h-32" />
        </div>
      ) : Object.keys(grouped).length === 0 ? (
        <EmptyState
          icon={Trophy}
          title="No rules match"
          description="Try a different category or clear the search."
        />
      ) : (
        <div className="space-y-6">
          {Object.entries(grouped).map(([cat, rs]) => (
            <section key={cat}>
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="text-xs font-bold uppercase tracking-wide px-2 py-0.5 rounded"
                  style={{ background: CAT_META[cat]?.bg, color: CAT_META[cat]?.color }}
                >
                  {CAT_META[cat]?.icon} {CAT_META[cat]?.label ?? cat}
                </span>
                <span className="text-xs text-muted-foreground">{rs.length} rule{rs.length === 1 ? '' : 's'}</span>
              </div>
              <div className="bg-card border border-border rounded-xl divide-y divide-border overflow-hidden">
                {rs.map((r) => (
                  <RuleRow
                    key={r.id}
                    rule={r}
                    draft={draftFor(r)}
                    dirty={isDirty(r)}
                    saving={savingId === r.id}
                    onChange={(patch) => setDraft(r.id, patch)}
                    onSave={() => saveRule(r)}
                    onReset={() => resetRule(r)}
                  />
                ))}
              </div>
            </section>
          ))}
          {filtered.length === 0 && (
            <p className="text-sm text-muted-foreground italic">No rules match the current filter.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-2xl font-bold" style={color ? { color } : undefined}>{value}</p>
    </div>
  );
}

function RuleRow({
  rule, draft, dirty, saving, onChange, onSave, onReset,
}: {
  rule: ScoreRule;
  draft: RuleDraft;
  dirty: boolean;
  saving: boolean;
  onChange: (patch: Partial<RuleDraft>) => void;
  onSave: () => void;
  onReset: () => void;
}) {
  const isBonus = rule.kind === 'bonus';
  const ptsColor = isBonus ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400';
  const [showCurve, setShowCurve] = useState(false);
  const hasCurve = !!rule.curve_kind;
  return (
    <>
    <div className={`flex items-center gap-3 px-4 py-3 ${draft.enabled ? '' : 'opacity-50'}`}>
      {/* enabled toggle */}
      <button
        onClick={() => onChange({ enabled: !draft.enabled })}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${
          draft.enabled ? 'bg-green-600' : 'bg-muted'
        }`}
        title={draft.enabled ? 'Enabled' : 'Disabled'}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
            draft.enabled ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>

      {/* label + id */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">
          {rule.label}
          {rule.pillar && (
            <span
              className="ml-2 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded font-mono"
              style={{
                background:
                  rule.pillar === 'safety'     ? '#ef444422'
                : rule.pillar === 'efficiency' ? '#22c55e22'
                : '#3b82f622',
                color:
                  rule.pillar === 'safety'     ? '#ef4444'
                : rule.pillar === 'efficiency' ? '#22c55e'
                : '#3b82f6',
              }}
              title={`Pillar: ${rule.pillar}`}
            >
              {rule.pillar}
            </span>
          )}
          {rule.curve_kind && (
            <span
              className="ml-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-500"
              title={`Curve: ${rule.curve_kind}`}
            >
              curve
            </span>
          )}
          {rule.overridden && (
            <span className="ml-2 text-[10px] text-blue-500 uppercase tracking-wide">customised</span>
          )}
        </p>
        <p className="text-[10px] text-muted-foreground font-mono truncate">{rule.id}</p>
      </div>

      {/* points input */}
      <label className="flex items-center gap-1.5 text-xs">
        <span className="text-muted-foreground">pts</span>
        <input
          type="number"
          value={draft.points}
          onChange={(e) => onChange({ points: Number(e.target.value) })}
          min={isBonus ? 0 : -100}
          max={isBonus ? 100 : 0}
          className={`w-16 bg-muted border border-border rounded px-2 py-1 text-sm text-right tabular-nums font-bold ${ptsColor}`}
        />
      </label>

      {/* cap input */}
      <label className="flex items-center gap-1.5 text-xs">
        <span className="text-muted-foreground">cap</span>
        <input
          type="number"
          value={draft.cap ?? ''}
          placeholder="—"
          onChange={(e) => onChange({ cap: e.target.value === '' ? null : Number(e.target.value) })}
          className="w-16 bg-muted border border-border rounded px-2 py-1 text-sm text-right tabular-nums"
        />
      </label>

      {/* save/reset */}
      <div className="flex gap-1 shrink-0">
        <button
          disabled={!dirty || saving}
          onClick={onSave}
          className="px-2.5 py-1 rounded text-xs font-medium bg-primary text-primary-foreground disabled:opacity-30 disabled:cursor-not-allowed hover:opacity-90"
        >
          {saving ? '…' : 'Save'}
        </button>
        <button
          disabled={!rule.overridden || saving}
          onClick={onReset}
          className="px-2.5 py-1 rounded text-xs font-medium border border-border disabled:opacity-30 disabled:cursor-not-allowed hover:bg-muted"
          title="Reset to default"
        >
          ⟲
        </button>
        {hasCurve && (
          <button
            onClick={() => setShowCurve((v) => !v)}
            className="px-2 py-1 rounded text-xs font-medium border border-border hover:bg-muted"
            title="Edit curve anchors"
          >
            {showCurve ? '▴' : '▾'}
          </button>
        )}
      </div>
    </div>
    {hasCurve && showCurve && (
      <div className="bg-muted/30 px-4 py-3 border-t border-border text-xs space-y-2">
        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Curve: <span className="font-mono">{rule.curve_kind}</span>
          <span className="ml-3 opacity-60">
            defaults — x_zero {rule.default_curve_x_zero ?? '—'} ·
            x_max {rule.default_curve_x_max ?? '—'} ·
            y_max {rule.default_curve_y_max ?? '—'}
          </span>
        </p>
        <div className="grid grid-cols-3 gap-3">
          <CurveInput
            label="x_zero (no penalty below)"
            value={draft.curve_x_zero}
            onChange={(v) => onChange({ curve_x_zero: v })}
          />
          <CurveInput
            label="x_max (cap point)"
            value={draft.curve_x_max}
            onChange={(v) => onChange({ curve_x_max: v })}
          />
          <CurveInput
            label="y_max (points at cap)"
            value={draft.curve_y_max}
            onChange={(v) => onChange({ curve_y_max: v === null ? null : Math.trunc(v) })}
          />
        </div>
      </div>
    )}
    </>
  );
}

function CurveInput({
  label, value, onChange,
}: { label: string; value: number | null; onChange: (v: number | null) => void }) {
  const { t } = useTranslation();
  return (
    <label className="flex flex-col gap-1">
      <span className="text-muted-foreground text-[10px]">{label}</span>
      <input
        type="number"
        value={value ?? ''}
        placeholder={t('forms.default_placeholder')}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        className="bg-muted border border-border rounded px-2 py-1 text-sm tabular-nums"
      />
    </label>
  );
}

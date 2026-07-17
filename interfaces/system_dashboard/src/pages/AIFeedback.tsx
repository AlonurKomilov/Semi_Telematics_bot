import { useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';
import RangeTabs from '../components/RangeTabs';
import type { AIFeedbackResponse, AIFeedbackReason, AIFeedbackRow } from '../types';

// Operator review of customer thumbs-down feedback.  Lists every
// ``had_reask=TRUE`` row across all accounts in the chosen window,
// optionally filtered to a single reason category, with the actual
// user question + AI answer joined in so the operator can see WHAT
// the user complained about — not just that they complained.
//
// Each reason maps to a different remediation pipeline:
//   inaccurate     → tool result needs review
//   off_topic      → prompt classifier needs a new pattern
//   incomplete     → scope filter (vehicle_nums) was too narrow
//   hallucinated   → strongest signal; model gets downweighted hard
//   vague          → model used too few tools; tighten the prompt
//   unjust_refusal → candidate for the heuristic detector's keyword list
//   other          → operator reads the free-text note, decides

const REASON_LABELS: Record<string, string> = {
  inaccurate: 'Wrong data',
  off_topic: "Didn't answer",
  incomplete: 'Missed data',
  hallucinated: 'Hallucination',
  vague: 'Too vague',
  unjust_refusal: 'Unjust refusal',
  other: 'Other',
  __none__: 'No reason given',
};

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function reasonColor(r: string | null): string {
  switch (r) {
    case 'hallucinated':    return 'text-rose-400 border-rose-500/40 bg-rose-500/10';
    case 'unjust_refusal':  return 'text-amber-400 border-amber-500/40 bg-amber-500/10';
    case 'inaccurate':      return 'text-orange-400 border-orange-500/40 bg-orange-500/10';
    case 'incomplete':      return 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10';
    case 'off_topic':       return 'text-indigo-400 border-indigo-500/40 bg-indigo-500/10';
    case 'vague':           return 'text-slate-400 border-slate-500/40 bg-slate-500/10';
    case 'other':           return 'text-slate-300 border-slate-600 bg-slate-700/30';
    default:                return 'text-slate-500 border-slate-700 bg-slate-800/30';
  }
}

const REASON_FILTERS: (AIFeedbackReason | '')[] = [
  '', 'inaccurate', 'off_topic', 'incomplete',
  'hallucinated', 'vague', 'unjust_refusal', 'other',
];

export default function AIFeedbackPage() {
  const [data, setData] = useState<AIFeedbackResponse | null>(null);
  const [reason, setReason] = useState<AIFeedbackReason | ''>('');
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    setErr('');
    const qs = new URLSearchParams({ limit: '200', days: String(days) });
    if (reason) qs.set('reason', reason);
    apiJSON<AIFeedbackResponse>(`/system/ai-feedback?${qs}`)
      .then(setData)
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [reason, days]); // eslint-disable-line react-hooks/exhaustive-deps

  const counts = data?.counts_by_reason || {};
  const totalAll = Object.values(counts).reduce((acc, n) => acc + n, 0);

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">AI feedback</h1>
        <p className="text-xs text-slate-400 mt-0.5">
          Customer thumbs-down across the fleet — reason categories show what's failing
          and where to look. Bare thumbs-down without a reason still flips had_reask but
          isn't categorised; users skip the form sometimes.
        </p>
      </header>

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-xs text-slate-400">Reason:</span>
        {REASON_FILTERS.map((r) => (
          <button
            key={r || 'all'}
            onClick={() => setReason(r)}
            className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
              reason === r
                ? 'bg-indigo-500 text-white border-indigo-500'
                : 'bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-600'
            }`}
          >
            {r === '' ? 'All' : REASON_LABELS[r] || r}
            {r !== '' && counts[r] !== undefined && (
              <span className="ml-1.5 text-slate-400">{counts[r]}</span>
            )}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2 text-xs">
          <span className="text-slate-400">Days:</span>
          <RangeTabs tabs={[7, 30, 90] as const} value={days} onChange={setDays} />
        </div>
      </div>

      {/* Top-line counts */}
      {!loading && data && (
        <div className="mb-4 text-xs text-slate-400">
          <strong className="text-slate-200">{totalAll}</strong> thumbs-down in the last {data.days} days
          {Object.keys(counts).length > 1 && (
            <>
              {' · '}
              {Object.entries(counts)
                .sort((a, b) => b[1] - a[1])
                .map(([r, n], i) => (
                  <span key={r}>
                    {i > 0 && ' · '}
                    <span className="text-slate-300">{REASON_LABELS[r] || r}</span>
                    {' '}
                    <span className="text-slate-500">{n}</span>
                  </span>
                ))}
            </>
          )}
        </div>
      )}

      {err && (
        <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/40 rounded p-3 mb-4">
          {err}
        </div>
      )}

      {loading && <div className="text-xs text-slate-400">Loading…</div>}

      {!loading && data && data.items.length === 0 && (
        <div className="text-xs text-slate-500 bg-slate-800/50 border border-slate-700 rounded p-4">
          No thumbs-down in this window. Either users are happy or no chat traffic — check
          /accounts to confirm.
        </div>
      )}

      <ul className="space-y-2">
        {data?.items.map((row) => (
          <FeedbackRowCard
            key={row.id}
            row={row}
            expanded={expanded === row.id}
            onToggle={() => setExpanded((cur) => (cur === row.id ? null : row.id))}
          />
        ))}
      </ul>
    </div>
  );
}

interface RowProps {
  row: AIFeedbackRow;
  expanded: boolean;
  onToggle: () => void;
}

function FeedbackRowCard({ row, expanded, onToggle }: RowProps) {
  const reasonKey = row.feedback_reason || '__none__';
  return (
    <li className="rounded border border-slate-700 bg-slate-800/40">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-slate-800/80 transition-colors"
      >
        <span
          className={`px-2 py-0.5 text-2xs rounded-md border font-medium whitespace-nowrap ${reasonColor(row.feedback_reason)}`}
        >
          {REASON_LABELS[reasonKey]}
        </span>
        <span className="text-xs text-slate-300 truncate flex-1">
          {row.user_question || <em className="text-slate-500">(no question recorded)</em>}
        </span>
        <span className="text-2xs text-slate-500 font-mono whitespace-nowrap">
          {row.model}
        </span>
        <span className="text-2xs text-slate-400 whitespace-nowrap">
          {row.account_name}
        </span>
        <span className="text-2xs text-slate-500 whitespace-nowrap">
          {fmtTime(row.created_at)}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-slate-700 px-3 py-2 space-y-2 text-xs">
          {row.feedback_note && (
            <div>
              <div className="text-2xs uppercase tracking-wide text-slate-500 mb-0.5">
                User's note
              </div>
              <div className="text-slate-200 whitespace-pre-wrap">
                {row.feedback_note}
              </div>
            </div>
          )}
          <div>
            <div className="text-2xs uppercase tracking-wide text-slate-500 mb-0.5">
              Question
            </div>
            <div className="text-slate-300 whitespace-pre-wrap">
              {row.user_question || <em className="text-slate-500">(not found in chat_history)</em>}
            </div>
          </div>
          <div>
            <div className="text-2xs uppercase tracking-wide text-slate-500 mb-0.5">
              AI answer
            </div>
            <div className="text-slate-300 whitespace-pre-wrap max-h-64 overflow-y-auto">
              {row.ai_answer || <em className="text-slate-500">(not found in chat_history)</em>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-2xs text-slate-500 pt-2 border-t border-slate-700">
            <span>account: <span className="text-slate-300">{row.account_name} (#{row.account_id})</span></span>
            <span>user: <span className="text-slate-300">#{row.user_id}</span></span>
            <span>role: <span className="text-slate-300">{row.role || '—'}</span></span>
            <span>category: <span className="text-slate-300">{row.prompt_category || '—'}</span></span>
            <span>latency: <span className="text-slate-300">{row.latency_ms ?? '—'} ms</span></span>
            <span>id: <span className="text-slate-300 font-mono">#{row.id}</span></span>
          </div>
        </div>
      )}
    </li>
  );
}

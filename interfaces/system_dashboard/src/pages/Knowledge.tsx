import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import type { ReviewArticle, ReviewList } from '../types';

/** Compact timestamp — the format Scans and Retention use, so date
 *  columns line up across the console. */
function fmtTs(iso: string): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : '—';
}

/** How long something has been waiting, in the units an operator thinks
 *  in. A queue row that says "4d" reads as a backlog; the same row
 *  stamped 2026-09-07 11:04 does not. Empty when the timestamp will not
 *  parse, so the caller can drop the chip rather than render a blank. */
function waitedFor(iso: string): string {
  if (!iso) return '';
  const then = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return '';
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 60) return `${Math.max(mins, 0)}m`;
  if (mins < 60 * 48) return `${Math.floor(mins / 60)}h`;
  return `${Math.floor(mins / 1440)}d`;
}

/** Status only. One shape per meaning class: a STATE is a flat tinted
 *  chip with no border, because a bordered pill reads as pressable.
 *  Classification (the category) is plain text beside it. */
function State({ children, tone }: { children: React.ReactNode; tone: 'amber' | 'emerald' | 'slate' }) {
  const cls =
    tone === 'amber' ? 'bg-amber-900/40 text-amber-300'
      : tone === 'emerald' ? 'bg-emerald-900/40 text-emerald-300'
      : 'bg-slate-800 text-slate-400';
  return <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium ${cls}`}>{children}</span>;
}

type Lane = 'pending' | 'published' | 'refused';

/** One article, expanded to everything the decision needs.
 *
 *  The operator is deciding whether this text enters every other
 *  customer's knowledge base AND every other customer's AI assistant,
 *  so the body is shown in full rather than as a preview — a judgement
 *  made on the first 200 characters is not a judgement. It is capped in
 *  HEIGHT, not in length: an unbounded body in flow pushes every row
 *  below it, which lands a moved Publish button under a cursor that has
 *  not moved.
 *
 *  The card header is the expand toggle (the console's own idiom on the
 *  AI-feedback rows), so the control an operator uses on every row
 *  holds one position no matter how many actions that row carries.
 */
function ArticleCard({
  a, lane, busy, err, onApprove, onRefuse,
}: {
  a: ReviewArticle;
  lane: Lane;
  busy: string | null;
  err: string;
  onApprove: (a: ReviewArticle) => void;
  onRefuse: (a: ReviewArticle, lane: Lane) => void;
}) {
  const [open, setOpen] = useState(lane === 'pending');
  const waited = lane === 'pending' ? waitedFor(a.created_at) : '';
  const anyBusy = busy !== null;
  // Pending rows carry their lane on the ROW, not only in the section
  // label above them: an article moves between these lists when the
  // operator acts, so the label alone cannot survive scrolling.
  const frame = lane === 'pending'
    ? 'border-amber-900/50 border-l-2 border-l-amber-700'
    : 'border-slate-800';

  return (
    <div className={`bg-slate-900 border rounded-lg overflow-hidden ${frame}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full text-left px-4 py-3 hover:bg-slate-800/60 focus-visible:outline focus-visible:outline-1 focus-visible:outline-slate-500"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-slate-200">{a.title}</span>
          {lane === 'pending' && <State tone="amber">{waited ? `waiting ${waited}` : 'waiting'}</State>}
          {lane === 'published' && <State tone="emerald">published</State>}
          {lane === 'refused' && <State tone="slate">refused</State>}
          <span className="text-xs text-slate-500">{a.category || 'general'}</span>
        </div>
        <div className="text-xs text-slate-500 mt-1">
          <Link
            to={`/accounts/${a.account_id}`}
            onClick={(e) => e.stopPropagation()}
            className="text-slate-400 hover:text-slate-200 underline decoration-slate-700"
          >
            account #{a.account_id}
          </Link>
          {a.creator_name && <span> · by {a.creator_name}</span>}
          <span> · submitted {fmtTs(a.created_at)}</span>
          <span> · audience {a.target_role === 'all' ? 'every role' : a.target_role}</span>
          {a.platform_reviewed_at && <span> · reviewed {fmtTs(a.platform_reviewed_at)}</span>}
        </div>
        {lane === 'pending' && (
          <div className="text-xs text-slate-500 mt-1">
            approved by this account&rsquo;s owner · awaiting the platform
          </div>
        )}
        {a.platform_review_note && (
          <div className="text-xs text-slate-400 mt-1 italic">reason: {a.platform_review_note}</div>
        )}
      </button>

      {open && (
        <div className="px-4 pb-3 border-t border-slate-800/60 pt-3">
          <p className="text-sm text-slate-300 whitespace-pre-wrap break-words max-w-3xl max-h-64 overflow-y-auto">
            {a.description || <span className="text-slate-600 italic">No body text.</span>}
          </p>
          {a.tags && <div className="mt-2 text-xs text-slate-500 font-mono">{a.tags}</div>}
          {a.media_url && (
            <a
              href={a.media_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-xs text-slate-400 hover:text-slate-200 underline decoration-slate-700 break-all"
            >
              attachment: {a.media_url}
            </a>
          )}
        </div>
      )}

      {/* The failure belongs beside the button that caused it — a banner
          at the top of the page paints where the operator is not looking
          once they are six rows down. */}
      {err && (
        <div className="px-4 py-2 border-t border-rose-900/60 bg-rose-950/40 text-rose-300 text-xs">
          {err}
        </div>
      )}

      {lane !== 'refused' && (
        <div className="px-4 py-2.5 border-t border-slate-800/60 flex items-center justify-end gap-2">
          {lane === 'pending' ? (
            <>
              <button
                onClick={() => onRefuse(a, lane)}
                disabled={anyBusy}
                className="px-2 py-1 rounded text-xs text-slate-300 hover:text-rose-200 border border-slate-700 hover:border-rose-800/60 disabled:opacity-50"
              >
                {busy === `un-${a.id}` ? 'Refusing…' : 'Refuse'}
              </button>
              {/* Weight tracks blast radius, not sentiment: this is the
                  one control on the console that reaches every customer. */}
              <button
                onClick={() => onApprove(a)}
                disabled={anyBusy}
                className="px-4 py-2 rounded text-xs font-medium bg-emerald-900/40 text-emerald-200 border border-emerald-800/60 hover:bg-emerald-900/60 disabled:opacity-50"
              >
                {busy === `ok-${a.id}` ? 'Publishing…' : 'Publish to every account'}
              </button>
            </>
          ) : (
            <button
              onClick={() => onRefuse(a, lane)}
              disabled={anyBusy}
              className="px-2 py-1 rounded text-xs text-rose-300 hover:text-rose-200 border border-rose-900/60 hover:bg-rose-900/40 disabled:opacity-50"
            >
              {busy === `un-${a.id}` ? 'Withdrawing…' : 'Unpublish'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Section({
  title, count, loading, empty, children,
}: {
  title: string; count: number; loading: boolean; empty: string; children: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
        {/* A count of 0 while the request is still in flight is a
            statement the page cannot yet make — omit it, never render 0. */}
        {title}{loading && count === 0 ? '' : ` (${count})`}
      </p>
      {count === 0 ? (
        <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-6 text-sm text-slate-500">
          {loading ? 'Loading…' : empty}
        </div>
      ) : (
        <div className="grid gap-3">{children}</div>
      )}
    </section>
  );
}

export default function KnowledgePage() {
  const [pending, setPending] = useState<ReviewArticle[]>([]);
  const [live, setLive] = useState<ReviewArticle[]>([]);
  const [refused, setRefused] = useState<ReviewArticle[]>([]);
  const [err, setErr] = useState('');
  const [cardErr, setCardErr] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    Promise.all([
      apiJSON<ReviewList>('/system/knowledge/pending'),
      apiJSON<ReviewList>('/system/knowledge/published'),
      apiJSON<ReviewList>('/system/knowledge/refused'),
    ])
      .then(([p, l, r]) => {
        setPending(p.articles ?? []);
        setLive(l.articles ?? []);
        setRefused(r.articles ?? []);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  function noteOn(id: number, message: string) {
    setCardErr((m) => ({ ...m, [id]: message }));
  }

  async function approve(a: ReviewArticle) {
    // The blast radius is stated at the top of the page, which has
    // scrolled away by the time an operator is working a queue. Name it
    // here, where the decision is actually taken.
    const ok = window.confirm(
      `Publish “${a.title}” to every account on the platform.\n\n` +
      'It becomes readable in every account’s knowledge base and is fed to ' +
      'every account’s AI assistant. Customers can read it immediately. ' +
      'Unpublishing later removes it, but does not un-read it.',
    );
    if (!ok) return;
    setBusy(`ok-${a.id}`);
    noteOn(a.id, '');
    try {
      const res = await apiJSON<{ ok: boolean }>(
        `/system/knowledge/${a.id}/approve`, { method: 'POST', body: { note: '' } });
      // A write that reports ok:false is a failure, not a success with a
      // quiet result — the guard misses when the account withdraws its
      // own approval between the page loading and this click.
      if (!res?.ok) {
        noteOn(a.id, 'Nothing was published — the account may have withdrawn its approval. Refresh and re-check.');
      }
      load();
    } catch (e: unknown) {
      noteOn(a.id, e instanceof Error ? e.message : 'Publish failed');
    } finally {
      setBusy(null);
    }
  }

  async function refuse(a: ReviewArticle, lane: Lane) {
    const live = lane === 'published';
    const note = window.prompt(
      live
        ? `Withdraw “${a.title}” from every other account.\n\n` +
          'It stays as this account’s own private article. Reason (optional):'
        : `Refuse “${a.title}”. It has not been published, so nothing is withdrawn.\n\n` +
          'It returns to this account as their own private article, and they can ' +
          'submit it again. Reason (optional):',
      a.platform_review_note || '',
    );
    if (note === null) return;
    setBusy(`un-${a.id}`);
    noteOn(a.id, '');
    try {
      const res = await apiJSON<{ ok: boolean }>(
        `/system/knowledge/${a.id}/unpublish`, { method: 'POST', body: { note } });
      if (!res?.ok) noteOn(a.id, 'Nothing changed — refresh and re-check.');
      load();
    } catch (e: unknown) {
      noteOn(a.id, e instanceof Error ? e.message : 'Withdraw failed');
    } finally {
      setBusy(null);
    }
  }

  const card = (a: ReviewArticle, lane: Lane) => (
    <ArticleCard
      key={a.id} a={a} lane={lane} busy={busy} err={cardErr[a.id] || ''}
      onApprove={approve} onRefuse={refuse}
    />
  );

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-100">Knowledge review</h1>
      <p className="text-xs text-slate-500 mt-1 mb-3 max-w-2xl">
        An article marked public is readable by every account on the platform and is fed to
        every account&rsquo;s AI assistant. The publishing account&rsquo;s owner decides whether to
        submit it; you decide whether it goes out.
      </p>

      <div className="flex items-center mb-5">
        <button
          onClick={load}
          disabled={loading}
          className="ml-auto px-2 py-1 rounded text-xs text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-50"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {err && (
        <div className="mb-4 px-3 py-2 rounded border border-rose-900/60 bg-rose-950/40 text-rose-300 text-sm">
          {err}
        </div>
      )}

      <Section
        title="Waiting for review" count={pending.length} loading={loading}
        empty="Nothing waiting. Submissions land here the moment an account approves one."
      >
        {pending.map((a) => card(a, 'pending'))}
      </Section>

      <Section
        title="Published platform-wide" count={live.length} loading={loading}
        empty="Nothing is published across accounts."
      >
        {live.map((a) => card(a, 'published'))}
      </Section>

      <Section
        title="Reviewed and declined" count={refused.length} loading={loading}
        empty="Nothing has been declined."
      >
        {refused.map((a) => card(a, 'refused'))}
      </Section>

      <p className="text-xs text-slate-600 mt-6 max-w-2xl">
        Declining keeps the row as the authoring account&rsquo;s own private article — refusing
        publication is not deleting somebody&rsquo;s work, and they can submit it again. An
        article the file scanner quarantines is withdrawn automatically and appears under{' '}
        <Link to="/scans" className="text-slate-400 hover:text-slate-200 underline decoration-slate-700">
          File scans
        </Link>.
      </p>
    </div>
  );
}

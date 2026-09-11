import { useCallback, useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type { ReviewArticle, ReviewList } from '../types';

/** Compact timestamp — same shape the Scans and Retention pages use, so
 *  date columns line up across the console. */
function fmtTs(iso: string): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : '—';
}

/** How long something has been waiting, in the units an operator thinks
 *  in.  A queue that says "4 days" reads as a backlog; the same row
 *  stamped "2026-09-07 11:04" does not. */
function waitedFor(iso: string): string {
  if (!iso) return '';
  const then = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return '';
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 60) return `${Math.max(mins, 0)}m`;
  if (mins < 60 * 48) return `${Math.floor(mins / 60)}h`;
  return `${Math.floor(mins / 1440)}d`;
}

function Tag({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'slate' | 'amber' | 'emerald' }) {
  const cls =
    tone === 'amber'
      ? 'bg-amber-900/40 text-amber-300 border-amber-800/60'
      : tone === 'emerald'
      ? 'bg-emerald-900/40 text-emerald-300 border-emerald-800/60'
      : 'bg-slate-800/60 text-slate-400 border-slate-700';
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium border ${cls}`}>
      {children}
    </span>
  );
}

/** One article, expanded to everything the decision needs.
 *
 *  The operator is deciding whether this text goes into every other
 *  customer's knowledge base AND into every other customer's AI
 *  assistant, so the body is shown in full rather than truncated to a
 *  preview — a judgement made on the first 200 characters is not a
 *  judgement.
 */
function ArticleCard({
  a, busy, onApprove, onUnpublish, published,
}: {
  a: ReviewArticle;
  busy: string | null;
  published: boolean;
  onApprove: (a: ReviewArticle) => void;
  onUnpublish: (a: ReviewArticle) => void;
}) {
  const [open, setOpen] = useState(!published);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
      <div className="px-4 py-3 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-200">{a.title}</span>
            <Tag>{a.category || 'general'}</Tag>
            {published ? <Tag tone="emerald">published</Tag> : <Tag tone="amber">waiting {waitedFor(a.created_at)}</Tag>}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            account <span className="font-mono text-slate-400">{a.account_id}</span>
            {a.creator_name && <span> · by {a.creator_name}</span>}
            <span> · submitted {fmtTs(a.created_at)}</span>
            {a.platform_reviewed_at && <span> · reviewed {fmtTs(a.platform_reviewed_at)}</span>}
          </div>
          {a.platform_review_note && (
            <div className="text-xs text-slate-400 mt-1 italic">note: {a.platform_review_note}</div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setOpen((v) => !v)}
            className="px-2 py-1 rounded text-xs text-slate-400 hover:text-slate-200 border border-slate-700"
          >
            {open ? 'Hide text' : 'Read text'}
          </button>
          {published ? (
            <button
              onClick={() => onUnpublish(a)}
              disabled={busy === `un-${a.id}`}
              className="px-2 py-1 rounded text-xs text-rose-300 hover:text-rose-200 border border-rose-900/60 hover:bg-rose-900/40 disabled:opacity-50"
            >
              {busy === `un-${a.id}` ? 'Withdrawing…' : 'Unpublish'}
            </button>
          ) : (
            <>
              <button
                onClick={() => onApprove(a)}
                disabled={busy === `ok-${a.id}`}
                className="px-2 py-1 rounded text-xs text-emerald-300 hover:text-emerald-200 border border-emerald-800/60 hover:bg-emerald-900/40 disabled:opacity-50"
              >
                {busy === `ok-${a.id}` ? 'Publishing…' : 'Publish'}
              </button>
              <button
                onClick={() => onUnpublish(a)}
                disabled={busy === `un-${a.id}`}
                className="px-2 py-1 rounded text-xs text-slate-300 hover:text-rose-200 border border-slate-700 hover:border-rose-800/60 disabled:opacity-50"
              >
                {busy === `un-${a.id}` ? 'Refusing…' : 'Refuse'}
              </button>
            </>
          )}
        </div>
      </div>
      {open && (
        <div className="px-4 pb-4 border-t border-slate-800/60 pt-3">
          <p className="text-sm text-slate-300 whitespace-pre-wrap break-words">
            {a.description || <span className="text-slate-600 italic">No body text.</span>}
          </p>
          {a.tags && <div className="mt-2 text-xs text-slate-500 font-mono">{a.tags}</div>}
          {a.media_url && (
            <div className="mt-2 text-xs text-slate-500 font-mono break-all">
              attachment: {a.media_url}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function KnowledgePage() {
  const [pending, setPending] = useState<ReviewArticle[]>([]);
  const [live, setLive] = useState<ReviewArticle[]>([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    Promise.all([
      apiJSON<ReviewList>('/system/knowledge/pending'),
      apiJSON<ReviewList>('/system/knowledge/published'),
    ])
      .then(([p, l]) => {
        setPending(p.articles ?? []);
        setLive(l.articles ?? []);
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

  async function approve(a: ReviewArticle) {
    setBusy(`ok-${a.id}`);
    setErr('');
    try {
      await apiJSON(`/system/knowledge/${a.id}/approve`, {
        method: 'POST', body: { note: '' },
      });
      load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Publish failed');
    } finally {
      setBusy(null);
    }
  }

  async function unpublish(a: ReviewArticle) {
    // The reason is the whole point of refusing — it is what the
    // account's owner will be asked about, and the row keeps it.
    const note = window.prompt(
      `Withdraw "${a.title}" from every other account.\n\n` +
      'It stays as this account’s own private article. Reason (optional):',
      a.platform_review_note || '',
    );
    if (note === null) return;
    setBusy(`un-${a.id}`);
    setErr('');
    try {
      await apiJSON(`/system/knowledge/${a.id}/unpublish`, {
        method: 'POST', body: { note },
      });
      load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Withdraw failed');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="flex items-baseline justify-between gap-4 mb-1">
        <h1 className="text-lg text-slate-200">Knowledge review</h1>
        <button
          onClick={load}
          disabled={loading}
          className="px-2 py-1 rounded text-xs text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-50"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>
      <p className="text-xs text-slate-500 mb-5 max-w-2xl">
        An article marked public is readable by every account on the platform and is fed to
        every account&rsquo;s AI assistant. The publishing account&rsquo;s owner decides whether to
        submit it; you decide whether it goes out.
      </p>

      {err && (
        <div className="mb-4 px-3 py-2 rounded border border-rose-900/60 bg-rose-950/40 text-rose-300 text-sm">
          {err}
        </div>
      )}

      <section className="mb-8">
        <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
          Waiting for review ({pending.length})
        </p>
        {pending.length === 0 ? (
          <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-6 text-sm text-slate-500">
            {loading ? 'Loading…' : 'Nothing waiting. Submissions land here the moment an account approves one.'}
          </div>
        ) : (
          <div className="grid gap-3">
            {pending.map((a) => (
              <ArticleCard
                key={a.id} a={a} busy={busy} published={false}
                onApprove={approve} onUnpublish={unpublish}
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
          Published platform-wide ({live.length})
        </p>
        {live.length === 0 ? (
          <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-6 text-sm text-slate-500">
            {loading ? 'Loading…' : 'Nothing is published across accounts.'}
          </div>
        ) : (
          <div className="grid gap-3">
            {live.map((a) => (
              <ArticleCard
                key={a.id} a={a} busy={busy} published
                onApprove={approve} onUnpublish={unpublish}
              />
            ))}
          </div>
        )}
      </section>

      <p className="text-xs text-slate-600 mt-6 max-w-2xl">
        Withdrawing keeps the row as the authoring account&rsquo;s own private article — refusing
        publication is not deleting somebody&rsquo;s work. An article that the file scanner
        quarantines is withdrawn automatically and appears under{' '}
        <span className="text-slate-400">File scans</span>.
      </p>
    </div>
  );
}

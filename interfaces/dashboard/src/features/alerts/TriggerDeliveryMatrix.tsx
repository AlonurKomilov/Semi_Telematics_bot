/**
 * "My triggers — where they reach you": rows = the triggers this person
 * set, columns = the personal channels (Telegram DM / Email / Push).
 *
 * The split with the Alerts page is the point.  A trigger has two halves
 * and they are different questions asked by different moods: *what do I
 * want to know* (the metric and the number) and *where should it reach
 * me*.  The first is an ALERT question and lives on the Alerts page beside
 * what the trigger has caught.  The second is a NOTIFICATION question, and
 * every other answer to it — which alert types, which activity notices,
 * which digests — is on this page.  Delivery controls stranded on a
 * feature page are how a person ends up hunting three screens to turn
 * Telegram off.
 *
 * Rows are TRIGGERS, not the trigger CATEGORY, and that is the one thing
 * the matrix above cannot do.  Its rows are alert types shared by everyone
 * who receives them, so a single "my triggers" row there could only mean
 * "all of them, one way".  Per-trigger is what lets DEF reach a phone
 * while battery waits for email — and it is why ``alert.trigger`` is
 * excluded from that matrix (capabilities/notifications/router.py): two
 * switches over one delivery is how "I turned it off and it kept coming"
 * happens.
 *
 * Column gating is the same as the matrix above, for the same reason: a
 * live-looking checkbox on a channel that cannot deliver is a lie the
 * person only discovers by not being told something.
 *
 * A raw table is correct here per the dashboard rules — this is a CONFIG
 * MATRIX (form UI), not a data list.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { ApiError, apiJSON } from '@/api/client';
import { Card } from '@/components/ui/card';
import { CardSkeleton } from '@/components/shell';
import { MatrixCell, MatrixTh } from './_shared/matrixCells';
import { CHANNEL_META } from './_shared/channels';

interface Trigger {
  id: number;
  metric: string;
  describes: string;
  enabled: boolean;
  channels: string[];
}

interface ChannelPrefs {
  connected: boolean;
  verified: boolean;
  enabled_master: boolean;
  devices?: { id: number }[];
}


const API = '/alerts/triggers';

export default function TriggerDeliveryMatrix({
  telegramMasterOn, refreshKey, onSaved,
}: {
  telegramMasterOn: boolean;
  /** Bumped by the channel cards after connect/verify/device changes so
   *  the column enable-states stay fresh. */
  refreshKey: number;
  onSaved?: () => void;
}) {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [email, setEmail] = useState<ChannelPrefs | null>(null);
  const [push, setPush] = useState<ChannelPrefs | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Distinct from "no triggers".  One 429 on a prefs endpoint must never
  // render as a statement that this person has nothing — least of all on
  // the page whose job is to show the move didn't lose anything.
  const [failed, setFailed] = useState(false);
  // Which channels the server says a trigger may use.
  const [channelKeys, setChannelKeys] = useState<string[]>([]);
  // A SET, not a scalar: two cells in flight would otherwise have the
  // first to land clear the flag for both.
  const [saving, setSaving] = useState<ReadonlySet<string>>(new Set());
  // Latest rows, readable from inside a queued write without re-creating
  // the handler on every render.
  const latest = useRef<Trigger[]>([]);
  useEffect(() => { latest.current = triggers; }, [triggers]);
  // One promise chain per trigger id — the ordering guarantee.
  const chains = useRef(new Map<number, Promise<void>>());

  const mark = (k: string, on: boolean) =>
    setSaving((cur) => {
      const next = new Set(cur);
      if (on) next.add(k); else next.delete(k);
      return next;
    });

  const load = useCallback(async () => {
    setFailed(false);
    // TWO groups, settled independently.  The rows and the column states
    // fail for different reasons and one must not take the other down:
    // a prefs blip should grey the columns, not empty the table.
    const rows = (async () => {
      const [mine, cat] = await Promise.all([
        apiJSON<{ triggers: Trigger[] }>(API),
        apiJSON<{ channels: string[] }>(`${API}/metrics`),
      ]);
      return { triggers: mine.triggers || [], channels: cat.channels || [] };
    })();
    const prefs = (async () => {
      const [e, p] = await Promise.all([
        apiJSON<{ email: ChannelPrefs }>('/notifications/prefs/email'),
        apiJSON<{ web_push: ChannelPrefs }>('/notifications/prefs/web_push'),
      ]);
      return { email: e.email, push: p.web_push };
    })();

    const [rowsRes, prefsRes] = await Promise.allSettled([rows, prefs]);
    if (rowsRes.status === 'fulfilled') {
      setTriggers(rowsRes.value.triggers);
      setChannelKeys(rowsRes.value.channels);
    } else {
      setFailed(true);
    }
    if (prefsRes.status === 'fulfilled') {
      setEmail(prefsRes.value.email);
      setPush(prefsRes.value.push);
    }
    // Prefs failing alone is NOT a failure of this card: the rows are the
    // content, and columns already have a designed can't-deliver state.
    setLoaded(true);
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);

  const emailOn = !!email?.verified && !!email?.enabled_master;
  const pushOn = (push?.devices?.length ?? 0) > 0 && !!push?.enabled_master;

  const colHint: Record<string, string> = {
    telegram_dm: telegramMasterOn ? '' : 'Personal alerts are switched off above',
    email: emailOn ? '' : 'Connect and verify your email above first',
    web_push: pushOn ? '' : 'Enable push on at least one device above first',
  };
  const colOn: Record<string, boolean> = {
    telegram_dm: telegramMasterOn, email: emailOn, web_push: pushOn,
  };

  const toggle = (t: Trigger, channel: string, on: boolean) => {
    const cell = `${t.id}:${channel}`;
    if (saving.has(cell)) return;      // this exact box is already moving
    mark(cell, true);
    // Queued behind whatever else is writing THIS row.  The payload is the
    // whole channel array and the server overwrites the column wholesale,
    // so two concurrent PATCHes on one trigger are a lost update: tick
    // Telegram then Email quickly and whichever request the server commits
    // last decides, while the UI shows both ticked over a row that has one.
    // Per-CELL guarding does not prevent that — only ordering does.
    const prev = chains.current.get(t.id) ?? Promise.resolve();
    const run = prev.then(async () => {
      // Read the CURRENT row, not the one captured when this cell
      // rendered: by the time the queue reaches us a sibling toggle may
      // already have changed the list we are about to send.
      const row = latest.current.find((x) => x.id === t.id);
      if (!row) return;                         // deleted while queued
      const next = on
        ? [...row.channels.filter((c) => c !== channel), channel]
        : row.channels.filter((c) => c !== channel);
      setTriggers((cur) => cur.map((x) => (
        x.id === t.id ? { ...x, channels: next } : x)));
      try {
        const fresh = await apiJSON<Trigger>(`${API}/${t.id}`, {
          method: 'PATCH', body: { channels: next },
        });
        setTriggers((cur) => cur.map((x) => (x.id === t.id ? fresh : x)));
        onSaved?.();
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // Deleted on the Alerts page, maybe in another tab.  Drop the
          // row rather than restoring a ghost that 404s on every touch.
          setTriggers((cur) => cur.filter((x) => x.id !== t.id));
          return;
        }
        // Roll back only the ONE channel this call changed — restoring the
        // captured row would also wipe a sibling toggle that succeeded.
        setTriggers((cur) => cur.map((x) => (x.id === t.id ? { ...x, channels: (
          on ? x.channels.filter((c) => c !== channel)
             : [...x.channels.filter((c) => c !== channel), channel]
        ) } : x)));
        toast.error(err instanceof Error ? err.message : 'Save failed');
      }
    }).finally(() => mark(cell, false));
    // The CHAIN must never reject, or every later write on this row is
    // skipped; the per-call catch above already reported the failure.
    chains.current.set(t.id, run.catch(() => {}));
  };

  const columns = CHANNEL_META.filter((c) => channelKeys.includes(c.key));
  const anyHint = columns.some((c) => colHint[c.key]);

  return (
    <Card render={<section />}>
      {/* No caps label of its own: the SourceLabel directly above already
          says "My triggers — where they reach you", and the sibling card's
          inner label ("Notify me when") earns its place by saying
          something different.  A second heading here would name one object
          twice in two stacked lines. */}
      {!loaded ? (
        <CardSkeleton height="h-32" message="Loading your triggers…" />
      ) : failed ? (
        <p className="text-xs text-muted-foreground">
          Couldn’t load your triggers — this is a loading problem, not an
          empty list; nothing has been changed.{' '}
          <button type="button" onClick={() => void load()}
                  className="text-primary hover:underline min-h-tap">
            Try again
          </button>
        </p>
      ) : triggers.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          You haven’t set any triggers yet. A trigger is your own sentence —
          “tell me when DEF drops below 10%” — and you write one on the{' '}
          <Link to="/alerts/triggers" className="text-primary hover:underline">
            Alerts page
          </Link>. It’ll appear here once it exists, so you can choose where
          it reaches you.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted-foreground">
                <th className="text-left font-medium pb-2">Trigger</th>
                {columns.map((c) => (
                  <MatrixTh key={c.key} icon={c.icon} label={c.label}
                            hint={colHint[c.key]} />
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {triggers.map((t) => (
                <tr key={t.id}>
                  {/* A switched-off trigger keeps its row and its
                      checkboxes: "where would this reach me" stays a
                      real question for something you mean to switch back
                      on, and greying it here would look like the delivery
                      choice was lost. The muted text says which it is. */}
                  <td className={`py-2.5 pr-3 ${
                    t.enabled ? '' : 'text-muted-foreground'}`}>
                    {t.describes}
                    {!t.enabled && (
                      <span className="text-2xs text-muted-foreground"> · off</span>
                    )}
                  </td>
                  {columns.map((c) => (
                    <MatrixCell
                      key={c.key}
                      checked={t.channels.includes(c.key)}
                      disabled={!colOn[c.key]}
                      busy={saving.has(`${t.id}:${c.key}`)}
                      hint={colHint[c.key]}
                      label={`${c.label} — ${t.describes}`}
                      onChange={(v) => toggle(t, c.key, v)}
                    />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Visible (not hover-only) explanation of greyed columns — this app
          runs on cab tablets where tooltips are unreachable. */}
      {anyHint && triggers.length > 0 && (
        <p className="text-2xs text-muted-foreground mt-2">
          {columns
            .filter((c) => colHint[c.key])
            .map((c) => `${c.label}: ${colHint[c.key].toLowerCase()}`)
            .join(' · ')}
        </p>
      )}

      <p className="text-2xs text-muted-foreground mt-2">
        A trigger always appears in the bell — these channels are the extra
        places it can reach you. What each one watches is set on the{' '}
        <Link to="/alerts/triggers" className="text-primary hover:underline">
          Alerts page
        </Link>.
      </p>
    </Card>
  );
}

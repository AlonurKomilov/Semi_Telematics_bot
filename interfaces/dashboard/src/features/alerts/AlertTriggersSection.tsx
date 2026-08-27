/**
 * "Triggers I set" — a person's own watches on vehicle metrics.
 *
 * ONE vocabulary, deliberately.  A TRIGGER is the thing you create; a
 * THRESHOLD is the number on it.  The table, the class, the package, the
 * endpoints, the notification category and the DM all say trigger, and an
 * earlier draft of this file said "thresholds" for the object — a third
 * word for one concept, which is the same-name-two-things confusion the
 * clarity rules exist to stop, just wearing the other face.
 *
 * It sits on the ALERTS page and owns exactly one half of a trigger:
 * WHAT it watches.  The other half — where it reaches you — is a
 * notification question, and it lives with every other answer to that
 * question on notification preferences, as a matrix whose rows are
 * individual triggers (TriggerDeliveryMatrix).  Splitting them this way
 * costs a link and buys the rule that delivery is configured in exactly
 * one place; the alternative was a person hunting three screens to turn
 * Telegram off.  This page STATES where each trigger goes, so the row
 * still answers "did my change take", and never edits it.
 *
 * The form is rendered ENTIRELY from ``/alerts/triggers/metrics``.  No
 * metric, unit, range or direction is hardcoded here — adding "watch
 * coolant" server-side makes it appear in this list with its own bounds
 * and its own explanation, which is the point of the catalog.
 *
 * Three things the UI has to say out loud, because each is a question
 * someone would otherwise ask support:
 *
 *   • the direction is not a choice — the metric owns it ("below"), so
 *     the row states it rather than offering a comparator that only ever
 *     has one useful value;
 *   • an engine-gated metric only means something while the engine runs,
 *     so a battery trigger silent overnight is working, not broken;
 *   • these arrive by direct message and never reach the shared Alerts
 *     board — one person's trigger is not the account's news.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { BellRing, Pencil, Plus, Trash2 } from 'lucide-react';
import { ApiError, apiJSON } from '@/api/client';
import { Tip } from '@/components/tooltip';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { CardSkeleton, SectionHeader } from '@/components/shell';
import { Switch } from '@/components/ui/switch';
import TriggerEditorSheet from './TriggerEditorSheet';
import type { EditingTrigger } from './TriggerEditorSheet';

interface MetricSpec {
  key: string;
  label: string;
  unit: string;
  direction: 'below' | 'above';
  min: number;
  max: number;
  hysteresis: number;
  requires_engine: string | null;
  checked_every_minutes: number;
  hint: string;
}

interface Trigger {
  id: number;
  metric: string;
  threshold: number;
  enabled: boolean;
  describes: string;
  /** Registry ids this trigger watches; empty = all in my scope. */
  vehicles: number[];
  watches_all: boolean;
  unit: string | null;
  direction: 'below' | 'above' | null;
  /** The EXTRA channels this trigger asked for. */
  channels: string[];
  /** Everything it actually reaches, bell included — the server's list,
   *  so the UI never has to know that the bell is implicit. */
  delivers_to: string[];
}

/** Channel keys are wire values; these are the words a person reads.  A
 *  key with no entry here still renders (as its key) rather than
 *  vanishing, so a channel added server-side is visible before this file
 *  catches up. */
const CHANNEL_LABEL: Record<string, string> = {
  // ``in_app`` is in ``delivers_to`` and never in ``channels`` — the bell
  // is not a choice, so it has no checkbox anywhere, but the row still
  // names it: "Telegram · Email" alone would read as if the bell were
  // one more thing that could be switched off.
  in_app: 'Bell',
  telegram_dm: 'Telegram',
  email: 'Email',
  web_push: 'Push',
};

const API = '/alerts/triggers';

export default function AlertTriggersSection(
  { onChanged }: { onChanged?: () => void },
) {
  const [metrics, setMetrics] = useState<MetricSpec[]>([]);
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [maxPerUser, setMaxPerUser] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  // A SET, not a scalar: two requests in flight would otherwise have the
  // first to finish clear the flag for both.  And it drives aria-busy,
  // never `disabled` — disabling the control a keyboard user is standing
  // on blurs it, and focus falls to <body>.
  const [busy, setBusy] = useState<ReadonlySet<number | 'new'>>(new Set());
  const mark = (k: number | 'new', on: boolean) =>
    setBusy((cur) => {
      const next = new Set(cur);
      if (on) next.add(k); else next.delete(k);
      return next;
    });
  // Trash buttons by trigger id, so removing a row can hand focus to the
  // next one instead of dropping the keyboard user at the document top.
  const trashRefs = useRef(new Map<number, HTMLButtonElement | null>());
  // The same map for the edit button.  `remove()` already hands focus to
  // a neighbouring row rather than dropping the keyboard user at the top
  // of the document; closing the edit sheet has to return focus the same
  // way, to the control that OPENED it — not to Add at the bottom of a
  // list of twenty.
  const editRefs = useRef(new Map<number, HTMLButtonElement | null>());
  const addBtnRef = useRef<HTMLButtonElement | null>(null);

  // The add form stays closed until asked for: this section is a LIST of
  // what you already watch, and an always-open empty form would make an
  // unconfigured page read as a chore rather than a summary.
  const [adding, setAdding] = useState(false);
  // The row being edited.  Separate from `adding` so the sheet can tell
  // "create" from "edit" without inferring it from a half-filled form.
  const [editing, setEditing] = useState<EditingTrigger | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const [cat, mine] = await Promise.all([
        apiJSON<{ metrics: MetricSpec[]; max_per_user: number }>(
          `${API}/metrics`),
        apiJSON<{ triggers: Trigger[] }>(API),
      ]);
      setMetrics(cat.metrics);
      setMaxPerUser(cat.max_per_user);
      setTriggers(mine.triggers);
    } catch {
      setFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const spec = (key: string) => metrics.find((m) => m.key === key);
  const atCap = maxPerUser > 0 && triggers.length >= maxPerUser;

  const toggle = async (t: Trigger, enabled: boolean) => {
    if (busy.has(t.id)) return;
    // Surgical, never a whole-array snapshot: restoring `prev` would
    // resurrect a row a concurrent delete had already removed, leaving a
    // trigger on screen that 404s on every touch.
    setTriggers((cur) => cur.map((x) => (x.id === t.id ? { ...x, enabled } : x)));
    mark(t.id, true);
    try {
      const fresh = await apiJSON<Trigger>(`${API}/${t.id}`, {
        method: 'PATCH', body: { enabled },
      });
      setTriggers((cur) => cur.map((x) => (x.id === t.id ? fresh : x)));
      onChanged?.();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Deleted elsewhere. Drop it rather than restoring a ghost.
        setTriggers((cur) => cur.filter((x) => x.id !== t.id));
        return;
      }
      setTriggers((cur) => cur.map((x) => (x.id === t.id ? { ...x, enabled: !enabled } : x)));
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      mark(t.id, false);
    }
  };

  const remove = async (t: Trigger) => {
    if (busy.has(t.id)) return;
    const idx = triggers.findIndex((x) => x.id === t.id);
    // Hand focus to the next row's remove button BEFORE this one
    // unmounts; deleting the third of five otherwise drops a keyboard
    // user back at the top of the document with no announcement.
    const next = triggers[idx + 1] ?? triggers[idx - 1];
    setTriggers((cur) => cur.filter((x) => x.id !== t.id));
    requestAnimationFrame(() => {
      const el = next ? trashRefs.current.get(next.id) : addBtnRef.current;
      el?.focus();
    });
    mark(t.id, true);
    try {
      await apiJSON(`${API}/${t.id}`, { method: 'DELETE' });
      onChanged?.();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        onChanged?.();          // already gone — the goal, not a failure
        return;
      }
      // Put back exactly the one row, at the place it came from.
      setTriggers((cur) => {
        const n = cur.slice();
        n.splice(Math.min(idx, n.length), 0, t);
        return n;
      });
      toast.error(e instanceof Error ? e.message : 'Could not remove that trigger');
    } finally {
      mark(t.id, false);
    }
  };

  // Both non-loaded states keep the heading and reserve height: a card
  // that grows from one line to a full section when the fetch lands
  // shoves every section below it down the page.
  const heading = (
    <SectionHeader size="card" icon={<BellRing className="size-4" />} className="mb-1">
      Triggers I set
    </SectionHeader>
  );

  if (!loaded) {
    return (
      <Card render={<section />}>
        {heading}
        <CardSkeleton height="h-24" message="Loading your triggers…" />
      </Card>
    );
  }

  if (failed) {
    return (
      <Card render={<section />}>
        {heading}
        <p className="text-xs text-muted-foreground h-24">
          Couldn’t load your triggers.{' '}
          <button type="button" onClick={() => void load()}
            className="text-primary hover:underline min-h-tap">
            Try again
          </button>
        </p>
      </Card>
    );
  }

  return (
    <Card render={<section />}>
      {heading}
      <p className="text-xs text-muted-foreground mb-3">
        Your own triggers, on the vehicles you can see. None of them post to
        the shared Alerts board — setting one adds nothing to anyone else’s
        queue, and what yours have caught is listed below. Every one reaches
        your bell; each row also shows where it is <em>set</em> to reach
        you — Telegram, email and push have to be connected before they
        can, which is on{' '}
        <Link to="/notifications/preferences" className="text-primary hover:underline">
          notification preferences
        </Link>{' '}along with the switches themselves.
      </p>
      <p className="text-xs text-muted-foreground mb-3">
        You hear on the <span className="text-foreground">crossing</span>:
        a truck already past your number when you save it stays quiet, and
        the next one to cross is the one you hear about.
      </p>

      {triggers.length === 0 && !adding && (
        <p className="text-xs text-muted-foreground mb-3">
          Nothing yet. For example: <span className="text-foreground">DEF level
          below 10%</span> — you’d hear the moment a truck crosses it.
        </p>
      )}

      {triggers.length > 0 && (
        <ul className="flex flex-col divide-y divide-border/60 mb-3">
          {triggers.map((t) => {
            const m = spec(t.metric);
            return (
              <li key={t.id} className="flex flex-wrap items-center gap-2 py-2"
                  aria-busy={busy.has(t.id)}>
                {/* A Switch, not a checkbox: this answers "is this
                    behaviour on?", not "is this item in a set" — and
                    every checkbox elsewhere on this page means the
                    latter.  Not nested in a <label>: Switch renders a
                    <button role="switch">, which a label may not wrap.
                    State is left OUT of the accessible name because
                    aria-checked already announces it. */}
                {m ? (
                  <Switch
                    size="sm"
                    checked={t.enabled}
                    onCheckedChange={(next) => { void toggle(t, next); }}
                    aria-label={t.describes}
                  />
                ) : (
                  // Nothing to switch on: this metric is gone from the
                  // catalog, so the only useful verb left is remove.
                  <span className="inline-flex size-4 items-center justify-center
                                   text-muted-foreground/40" aria-hidden>–</span>
                )}
                {/* The sentence and its footnote are ONE flex child.  As
                    two, the optional "while running" sat between the text
                    and the channel checkboxes, so an engine-gated row
                    pushed its checkboxes left by the width of two words
                    and no two rows agreed on where "Telegram" was. */}
                <span className="flex items-baseline gap-2 flex-1 min-w-0">
                  <span className={`text-sm truncate ${
                    t.enabled ? 'text-foreground' : 'text-muted-foreground'}`}>
                    {t.describes}
                  </span>
                  {/* Why a battery trigger can be quiet all night — said
                      where the question occurs, not in a help page. */}
                  {m?.requires_engine === 'on' && (
                    <span className="text-2xs text-muted-foreground shrink-0">
                      while running
                    </span>
                  )}
                </span>

                {/* Where it goes is STATED here, never edited here.  The
                    controls live with every other delivery choice on
                    notification preferences; a checkbox on this page would
                    be the third screen a person has to visit to turn
                    Telegram off.  Read-only, so the row still answers
                    "did my change take" without owning the answer. */}
                {m && (
                  // Two facts, two phrases.  Both are muted 2xs and sit
                  // together at the end of the row, so each leads with a
                  // preposition — without one they read as a single
                  // string at narrow widths.
                  <span className="text-2xs text-muted-foreground shrink-0">
                    {t.watches_all
                      ? 'on every vehicle'
                      : `on ${t.vehicles.length} vehicle${t.vehicles.length === 1 ? '' : 's'}`}
                    {' · to '}
                    {t.delivers_to.map((c) => CHANNEL_LABEL[c] ?? c).join(' · ')}
                  </span>
                )}
                {/* A row naming a metric the catalog no longer carries
                    stays visible and deletable rather than vanishing. */}
                {!m && (
                  <span className="text-2xs text-muted-foreground">
                    no longer available — remove it
                  </span>
                )}

                <Tip label={`Edit — ${t.describes}`}>
                  <Button
                    ref={(el) => { editRefs.current.set(t.id, el); }}
                    variant="ghost"
                    size="icon"
                    onClick={() => {
                      setEditing({
                        id: t.id, metric: t.metric, threshold: t.threshold,
                        vehicles: t.vehicles, watches_all: t.watches_all,
                      });
                      setAdding(true);
                    }}
                    aria-label={`Edit ${t.describes}`}
                  >
                    <Pencil />
                  </Button>
                </Tip>
                <Tip label={`Stop watching — ${t.describes}`}>
                  <Button
                    ref={(el) => { trashRefs.current.set(t.id, el); }}
                    variant="ghost"
                    size="icon"
                    onClick={() => { void remove(t); }}
                    aria-label={`Stop watching ${t.describes}`}
                  >
                    <Trash2 />
                  </Button>
                </Tip>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex items-center gap-2">
        <Button
          ref={addBtnRef}
          variant="outline"
          size="sm"
          aria-disabled={atCap}
          onClick={() => { if (!atCap) { setEditing(null); setAdding(true); } }}
        >
          <Plus /> Add
        </Button>
        {atCap && (
          <span className="text-2xs text-muted-foreground">
            {maxPerUser} is the limit — remove one to add another.
          </span>
        )}
      </div>

      {/* A Sheet, not the inline row this replaced: a trigger now also
          carries a vehicle selection, and a fleet of 189 does not fit on
          the line that held "Watch [metric] below [n] %". */}
      <TriggerEditorSheet
        open={adding}
        editing={editing}
        onClose={() => {
          const wasEditing = editing?.id;
          setAdding(false);
          setEditing(null);
          // Focus returns to whatever opened the sheet: the row's own
          // pencil for an edit, Add for a create.  Either way something
          // deliberate, never <body>.
          requestAnimationFrame(() => {
            const back = wasEditing !== undefined
              ? editRefs.current.get(wasEditing) : null;
            (back ?? addBtnRef.current)?.focus();
          });
        }}
        onSaved={() => { void load(); onChanged?.(); }}
      />
    </Card>
  );
}

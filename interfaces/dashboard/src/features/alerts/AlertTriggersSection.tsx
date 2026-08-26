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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { BellRing, Plus, Trash2, X } from 'lucide-react';
import { ApiError, apiJSON } from '@/api/client';
import { Tip } from '@/components/tooltip';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CardSkeleton, SectionHeader } from '@/components/shell';
import { Switch } from '@/components/ui/switch';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

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
  // What a trigger created here will reach, per the server.
  const [defaultChannels, setDefaultChannels] = useState<string[]>([]);
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
  const addBtnRef = useRef<HTMLButtonElement | null>(null);
  const metricRef = useRef<HTMLButtonElement | null>(null);

  // The add form stays closed until asked for: this section is a LIST of
  // what you already watch, and an always-open empty form would make an
  // unconfigured page read as a chore rather than a summary.
  const [adding, setAdding] = useState(false);
  const [draftMetric, setDraftMetric] = useState('');
  const [draftValue, setDraftValue] = useState('');

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const [cat, mine] = await Promise.all([
        apiJSON<{
          metrics: MetricSpec[]; max_per_user: number;
          default_channels: string[];
        }>(`${API}/metrics`),
        apiJSON<{ triggers: Trigger[] }>(API),
      ]);
      setMetrics(cat.metrics);
      setMaxPerUser(cat.max_per_user);
      setDefaultChannels(cat.default_channels || []);
      setTriggers(mine.triggers);
    } catch {
      setFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // "your bell, Telegram and email" — built from the server's answer.
  const defaultLabels = ['in_app', ...defaultChannels]
    .map((c) => CHANNEL_LABEL[c] ?? c)
    .join(', ')
    .replace(/, ([^,]*)$/, ' and $1');

  const spec = (key: string) => metrics.find((m) => m.key === key);
  const metricItems = useMemo(
    () => metrics.map((m) => ({ value: m.key, label: m.label })), [metrics]);
  const draftSpec = spec(draftMetric);
  const atCap = maxPerUser > 0 && triggers.length >= maxPerUser;

  const rangeError = (): string => {
    if (!draftSpec || draftValue === '') return '';
    const n = Number(draftValue);
    if (Number.isNaN(n)) return 'That has to be a number.';
    // `min`/`max` on a number input constrain the spinner, not typing —
    // without this check the only feedback is a round trip that fails.
    if (n < draftSpec.min || n > draftSpec.max) {
      return `${draftSpec.label} accepts ${draftSpec.min}–${draftSpec.max}${draftSpec.unit}.`;
    }
    return '';
  };

  const add = async () => {
    if (!draftSpec || draftValue === '' || rangeError()) return;
    if (busy.has('new')) return;
    mark('new', true);
    try {
      const made = await apiJSON<Trigger>(API, {
        method: 'POST',
        // No channels in the body: the server's default applies, and
        // where it goes is retuned on notification preferences — the same
        // way a newly relevant alert type arrives with defaults there.
        body: { metric: draftMetric, threshold: Number(draftValue) },
      });
      setTriggers((cur) => [made, ...cur]);
      setAdding(false);
      setDraftMetric('');
      setDraftValue('');
      onChanged?.();
      // The form unmounts — put focus somewhere deliberate rather than
      // letting it fall to <body>.
      requestAnimationFrame(() => addBtnRef.current?.focus());
    } catch (e) {
      // The server's refusals are written to be read — "would fire on
      // almost every vehicle" says more than "invalid input".
      toast.error(e instanceof Error ? e.message : 'Could not add that trigger');
    } finally {
      mark('new', false);
    }
  };

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
                  // The leading "to" is doing real work: with the
                  // checkboxes gone, this run and the "while running" note
                  // beside it are both muted 2xs and read as one string at
                  // narrow widths.  A preposition restores the boundary
                  // and makes the run a phrase rather than a list.
                  <span className="text-2xs text-muted-foreground shrink-0">
                    to {t.delivers_to
                      .map((c) => CHANNEL_LABEL[c] ?? c)
                      .join(' · ')}
                  </span>
                )}
                {/* A row naming a metric the catalog no longer carries
                    stays visible and deletable rather than vanishing. */}
                {!m && (
                  <span className="text-2xs text-muted-foreground">
                    no longer available — remove it
                  </span>
                )}

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

      {/* A real form when adding: Enter submits and Escape cancels, which
          is what anyone typing a number expects.  A plain div of inputs
          answers neither key. */}
      {adding ? (
        <form
          aria-label="Add an alert trigger"
          onSubmit={(e) => { e.preventDefault(); void add(); }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') { e.stopPropagation(); setAdding(false); }
          }}
          className="flex flex-col gap-2 rounded-md border border-border p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span id="trg-metric-lbl" className="text-xs text-muted-foreground">
              Watch
            </span>
            {/* ``items`` is what lets the closed trigger render the LABEL;
                without it the control shows the raw catalog key. */}
            <Select
              value={draftMetric}
              onValueChange={(v) => setDraftMetric(v ?? '')}
              items={metricItems}
            >
              <SelectTrigger ref={metricRef} id="trg-metric"
                             aria-labelledby="trg-metric-lbl trg-metric"
                             className="h-8 w-56 text-xs">
                <SelectValue placeholder="Pick a metric" />
              </SelectTrigger>
              <SelectContent>
                {metrics.map((m) => (
                  <SelectItem key={m.key} value={m.key}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* The direction is stated, never offered: the metric owns it,
                and a comparator with one useful value is a control that
                only invites a wrong answer.  Blank until a metric is
                picked — "below" would be the wrong word for coolant. */}
            <span className="text-xs text-muted-foreground">
              {draftSpec?.direction ?? ''}
            </span>

            <Input
              type="number"
              inputMode="decimal"
              value={draftValue}
              onChange={(e) => setDraftValue(e.target.value)}
              disabled={!draftSpec}
              min={draftSpec?.min}
              max={draftSpec?.max}
              // Half a volt is a real battery threshold; an integer step
              // would put it out of the spinner's reach.
              step={draftSpec && draftSpec.hysteresis < 1 ? 0.1 : 1}
              aria-describedby="trg-help"
              aria-invalid={!!rangeError()}
              placeholder={draftSpec ? `${draftSpec.min}–${draftSpec.max}` : '—'}
              aria-label={draftSpec
                ? `Threshold in ${draftSpec.unit}, between ${draftSpec.min} and ${draftSpec.max}`
                : 'Threshold'}
              className="h-8 w-24 text-xs"
            />
            <span className="text-xs text-muted-foreground w-8">
              {draftSpec?.unit ?? ''}
            </span>

            <Button
              type="submit"
              size="sm"
              aria-disabled={!draftSpec || draftValue === '' || !!rangeError()
                             || busy.has('new')}
            >
              Add
            </Button>
            <Tip label="Cancel">
              <Button type="button" variant="ghost" size="icon"
                      onClick={() => setAdding(false)}
                      aria-label="Cancel adding a trigger">
                <X />
              </Button>
            </Tip>
          </div>

          {/* The range lives HERE, not only in the placeholder — a
              placeholder vanishes on the first keystroke, which is exactly
              when someone needs to know the bounds.  Followed by the
              metric's own sentence, which is what stops a number that is
              physically meaningless for it. */}
          <p id="trg-help" className="text-2xs text-muted-foreground">
            {draftSpec ? (
              <>
                {rangeError() && (
                  <span className="text-danger">{rangeError()} </span>
                )}
                Between {draftSpec.min} and {draftSpec.max}{draftSpec.unit}.{' '}
                {draftSpec.hint}
                {draftSpec.requires_engine === 'on'
                  && ' Checked only while the engine is running.'}
                {' '}Re-checked every {draftSpec.checked_every_minutes} minutes.
              </>
            ) : 'Pick what to watch, then the number.'}
          </p>
          {/* The channel names come from the SERVER's default, never a
              sentence written here — a hardcoded promise about where an
              alert will go is one nobody notices has gone stale. */}
          <p className="text-2xs text-muted-foreground">
            It’ll reach {defaultLabels || 'your bell'} to start with — change
            where on{' '}
            <Link to="/notifications/preferences"
                  className="text-primary hover:underline">
              notification preferences
            </Link>.
          </p>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <Button
            ref={addBtnRef}
            variant="outline"
            size="sm"
            aria-disabled={atCap}
            onClick={() => {
              if (atCap) return;
              setAdding(true);
              // Enter the form rather than leaving focus on a button that
              // just vanished from the reading order.
              requestAnimationFrame(() => metricRef.current?.focus());
            }}
          >
            <Plus /> Add
          </Button>
          {atCap && (
            <span className="text-2xs text-muted-foreground">
              {maxPerUser} is the limit — remove one to add another.
            </span>
          )}
        </div>
      )}
    </Card>
  );
}

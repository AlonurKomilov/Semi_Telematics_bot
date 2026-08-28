/**
 * Write one trigger: what to watch, at what number, on which vehicles.
 *
 * An inline panel inside the card that lists the triggers. The list is
 * the reason.  "Watch [metric] below [n] %" fitted on one line; a fleet
 * of 189 vehicles does not, and squeezing it into a popover on that line
 * would have made the most consequential choice in the form the smallest
 * control on it.
 *
 * The form reads FEATURE, then WATCH, then ON WHICH VEHICLES — which
 * part of the product, which of its numbers, on which trucks.
 *
 * FEATURE is a field, not a caption.  It was a SelectGroup heading inside
 * the metric dropdown first, on the reasoning that a select with one
 * option is a decision nobody makes.  That reasoning was about the
 * CONTROL and ignored the form: a heading inside a list is invisible
 * until the list is opened, so a closed form showed no Feature anywhere
 * and the structure existed only for people who had already gone
 * looking.  As a field it also does real work — it FILTERS what can be
 * watched, so the two selects read as one sentence.  With a single
 * feature it fills itself in rather than demanding a choice; the day a
 * second registers, it becomes a real one with no change here.
 *
 * The word is not new: "Feature" is what the Alerts board's column and
 * the delivery settings already call this dimension, and the value comes
 * from featureCatalog.ts, which is the SSOT for what a feature is named.
 *
 * DELIVERY is absent entirely.  Where a trigger reaches you is answered
 * on notification preferences, per trigger, next to every other delivery
 * choice — see TriggerDeliveryMatrix.
 *
 * The vehicle list separates CAN'T from HAVEN'T.  86 of 189 active
 * vehicles on the live account report no telemetry — all 79 trailers
 * among them — and every metric in the catalog is engine or tank
 * telemetry.  Those vehicles stay selectable, because a trailer that
 * gains a gateway later should start working rather than need re-picking,
 * but they are marked, because picking one today buys silence and the
 * person deserves to know that before they save rather than after a week
 * of hearing nothing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Search } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { FEATURE_CATALOG } from '@/config/featureCatalog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ScrollRegion } from '@/components/scrolling';
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
  /** The featureCatalog id that owns this metric. The LABEL is resolved
   *  here from that catalog + i18n rather than sent by the API — the
   *  frontend owns feature names, and a second copy on the wire would be
   *  a second place for "Vehicles" to be spelled differently. */
  feature: string;
}

interface Targetable {
  id: number;
  name: string;
  type: string;
  company: string;
  watchable: boolean;
}

const API = '/alerts/triggers';

/** The row being edited.  Absent = creating a new trigger. */
export interface EditingTrigger {
  id: number;
  metric: string;
  threshold: number;
  vehicles: number[];
  watches_all: boolean;
}

export default function TriggerEditorForm({ editing, onClose, onSaved }: {
  editing?: EditingTrigger | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [metrics, setMetrics] = useState<MetricSpec[]>([]);
  const [fleet, setFleet] = useState<Targetable[]>([]);
  const [loaded, setLoaded] = useState(false);
  // A toast fades; the empty dropdown it explained does not.  This keeps
  // the reason on screen for as long as the problem lasts.
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const [feature, setFeature] = useState('');
  const [metric, setMetric] = useState('');
  const [value, setValue] = useState('');
  // Empty = every vehicle in my scope.  Kept as the same "" meaning the
  // column carries, so the UI never invents a third state.
  const [picked, setPicked] = useState<number[]>([]);
  const [allMine, setAllMine] = useState(true);
  const [query, setQuery] = useState('');
  const metricRef = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const [cat, veh] = await Promise.all([
        apiJSON<{ metrics: MetricSpec[] }>(`${API}/metrics`),
        apiJSON<{ vehicles: Targetable[] }>(`${API}/vehicles`),
      ]);
      setMetrics(cat.metrics || []);
      setFleet(veh.vehicles || []);
    } catch {
      setFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  // Mounted = open.  The parent renders this only while adding or
  // editing, so there is no `open` prop to guard on any more.
  useEffect(() => {
    setQuery('');
    if (editing) {
      // Feature is derived from the metric rather than stored, so an edit
      // resolves it the same way the create path would — one fact, one
      // owner, no second copy to fall out of date.
      setMetric(editing.metric);
      setValue(String(editing.threshold));
      setAllMine(editing.watches_all);
      setPicked(editing.vehicles);
      setFeature('');                 // resolved once metrics land, below
    } else {
      setFeature(''); setMetric(''); setValue('');
      setPicked([]); setAllMine(true);
    }
    void load();
    requestAnimationFrame(() => metricRef.current?.focus());
  }, [editing, load]);

  const spec = metrics.find((m) => m.key === metric);

  // Only the metrics belonging to the chosen feature.  This is what makes
  // Feature a real control rather than a caption: it NARROWS what can be
  // watched, so the two selects read as one sentence — watch, in this
  // feature, this thing.
  const metricsInFeature = useMemo(
    () => metrics.filter((m) => m.feature === feature), [metrics, feature]);

  // Group the metric list by the feature that owns it — the grouping IS
  // the Feature dimension, so it needs no control of its own.
  const groups = useMemo(() => {
    const by = new Map<string, { label: string; items: MetricSpec[] }>();
    for (const m of metrics) {
      let g = by.get(m.feature);
      if (!g) {
        // featureCatalog is the SSOT for what a feature is CALLED; the
        // API only ever sends its id.  An id the catalog does not know
        // falls back to the id rather than rendering blank.
        const entry = FEATURE_CATALOG.find((f) => f.id === m.feature);
        g = { label: entry ? t(entry.labelKey) : m.feature, items: [] };
        by.set(m.feature, g);
      }
      g.items.push(m);
    }
    return [...by.entries()];
  }, [metrics, t]);

  // An edit knows its metric before it knows the catalog, so the feature
  // it belongs to can only be resolved once the metrics arrive.  Runs
  // before the single-feature default below, so an edit never briefly
  // shows the wrong group.
  useEffect(() => {
    if (feature || !metric) return;
    const m = metrics.find((x) => x.key === metric);
    if (m) setFeature(m.feature);
  }, [feature, metric, metrics]);

  // With a single feature there is no decision to make, so the control
  // still SHOWS the answer but does not demand it.  The moment a second
  // feature registers this stops firing and the field becomes a real
  // choice, with no change here.
  useEffect(() => {
    if (feature || groups.length !== 1) return;
    setFeature(groups[0][0]);
  }, [feature, groups]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return fleet.filter((v) => !q
      || v.name.toLowerCase().includes(q)
      || v.company.toLowerCase().includes(q));
  }, [fleet, query]);

  const rangeError = (): string => {
    if (!spec || value === '') return '';
    const n = Number(value);
    if (Number.isNaN(n)) return 'That has to be a number.';
    if (n < spec.min || n > spec.max) {
      return `${spec.label} accepts ${spec.min}–${spec.max}${spec.unit}.`;
    }
    return '';
  };

  // Why the button is inert, in the same words the person would ask it.
  // A greyed control with no reason is the failure the section beside
  // this one already avoids ("20 is the limit — remove one to add
  // another"), and an aria-disabled button that simply does nothing on
  // click is worse than a disabled one: it accepts the press.
  const blockedBecause = (): string => {
    if (busy) return 'Saving…';
    if (failed) return 'Couldn’t load what can be watched.';
    // Editing a trigger whose metric left the catalog: the generic
    // "pick a feature" is true but unhelpful, because the person did not
    // choose to be here and nothing on screen says what changed.
    if (editing && metric && !metrics.some((m) => m.key === metric)) {
      return 'That metric is no longer available — pick what to watch instead.';
    }
    if (!feature) return 'Pick a feature.';
    if (!spec) return 'Pick what to watch.';
    if (value === '') return 'Enter a number.';
    if (rangeError()) return rangeError();
    if (!allMine && picked.length === 0) return 'Pick at least one vehicle.';
    return '';
  };
  const blocked = !!blockedBecause();

  const save = async () => {
    if (blocked || busy) return;
    if (editing && !hasWork) {
      // Nothing moved.  Sending the fields anyway would clear the
      // crossing flags server-side and silently re-seed a trigger the
      // person only looked at — a save that costs them the next real
      // crossing is worse than one that does nothing.
      onClose();
      return;
    }
    setBusy(true);
    try {
      // Empty array and "all mine" are the same wire value on purpose —
      // the server's '' already means all.
      const body = {
        metric, threshold: Number(value),
        vehicles: allMine ? [] : picked,
      };
      await (editing
        ? apiJSON(`${API}/${editing.id}`, { method: 'PATCH', body })
        : apiJSON(API, { method: 'POST', body }));
      // Every failure toasted and success said nothing, so the one
      // outcome a person most wants confirmed was the silent one.
      toast.success(`${editing ? 'Updated' : 'Watching'} `
        + `${spec?.label.toLowerCase()} ${spec?.direction} `
        + `${value}${spec?.unit} on `
        + (allMine ? 'every vehicle you can see'
                   : `${picked.length} vehicle${picked.length === 1 ? '' : 's'}`));
      onSaved();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message
        : `Could not ${editing ? 'save' : 'add'} that trigger`);
    } finally {
      setBusy(false);
    }
  };

  // Anything typed or picked that would be lost.  Not a generic "dirty"
  // flag: opening the form and closing it again must not nag, so this
  // asks whether there is WORK here, and picking 20 of 189 vehicles is
  // several minutes of it.
  const hasWork = editing
    // An edit opens already full, so "is there work" has to mean CHANGED
    // — otherwise closing a sheet you only looked at would nag every
    // time. Compared as sets, because picking the same trucks in a
    // different order is not an edit.
    ? (metric !== editing.metric
       || value !== String(editing.threshold)
       || allMine !== editing.watches_all
       // Only when the selection is what gets SENT.  Ticking some
       // vehicles and switching back to "all" leaves `picked` populated
       // but unsent, and counting it would nag about a change the save
       // would not make.
       || (!allMine && (picked.length !== editing.vehicles.length
                        || picked.some((id) => !editing.vehicles.includes(id)))))
    : (metric !== '' || value !== '' || (!allMine && picked.length > 0));

  const requestClose = () => {
    if (busy) return;
    if (hasWork && !window.confirm(editing
      ? 'Discard these changes? They aren’t saved yet.'
      : 'Discard this trigger? What you picked here isn’t saved yet.')) {
      return;
    }
    onClose();
  };

  const toggleVehicle = (id: number) =>
    setPicked((cur) => (cur.includes(id)
      ? cur.filter((x) => x !== id) : [...cur, id]));

  const unwatchable = fleet.length - fleet.filter((v) => v.watchable).length;

  if (!open) return null;

  return (
    // An inline panel in the card that lists the triggers, not a drawer.
    // It was a Sheet first, on the reasoning that 189 vehicles cannot sit
    // on a line — true of the LINE, and the wrong conclusion: the list
    // scrolls inside its own well either way, and here it opens where the
    // triggers already are instead of covering them. The card's own copy
    // already states the crossing rule, so this panel repeats no heading
    // and no caveat; it is the form, nothing else.
        <form
          onSubmit={(e) => { e.preventDefault(); void save(); }}
          onKeyDown={(e) => {
            // Escape cancels, the way it did before the drawer — and the
            // drawer's own Escape is gone with it, so this is now the
            // only thing answering that key.
            if (e.key === 'Escape') { e.stopPropagation(); requestClose(); }
          }}
          aria-label={editing ? 'Edit trigger' : 'Add an alert trigger'}
          className="flex flex-col gap-5 rounded-md border border-border p-3"
        >

          {/* FEATURE, then WATCH — the order the sentence is thought in:
              which part of the product, then which of its numbers.  It
              was a group heading inside the metric dropdown before, which
              meant it existed but was invisible until you opened the
              list; on a closed form there was no Feature anywhere.  As a
              field it also does real work — it FILTERS what can be
              watched, so the two selects read as one sentence. */}
          <div className="flex flex-col gap-2">
            <span id="trg-feature-lbl" className="text-xs font-medium
                                                  uppercase tracking-wide
                                                  text-muted-foreground">
              Feature
            </span>
            <Select
              value={feature}
              onValueChange={(v) => {
                setFeature(v ?? '');
                // A metric from the old feature would be a sentence about
                // something the form no longer says.
                setMetric('');
              }}
              items={groups.map(([key, g]) => ({ value: key, label: g.label }))}
            >
              <SelectTrigger id="trg-feature"
                             aria-labelledby="trg-feature-lbl trg-feature"
                             className="w-56 text-xs">
                <SelectValue placeholder="Pick a feature" />
              </SelectTrigger>
              <SelectContent>
                {groups.map(([key, g]) => (
                  <SelectItem key={key} value={key}>{g.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-2">
            <span id="trg-watch-lbl" className="text-xs font-medium
                                                uppercase tracking-wide
                                                text-muted-foreground">
              Watch
            </span>
            <div className="flex flex-wrap items-center gap-2">
              {/* No SelectGroup heading any more: the list is already
                  filtered to one feature, so repeating its name above
                  every option would label a group of one kind. */}
              <Select value={metric} onValueChange={(v) => setMetric(v ?? '')}
                      items={metricsInFeature.map(
                        (m) => ({ value: m.key, label: m.label }))}>
                <SelectTrigger ref={metricRef} id="trg-metric"
                               aria-labelledby="trg-watch-lbl trg-metric"
                               className="w-56 text-xs">
                  <SelectValue placeholder={feature ? 'Pick a metric'
                                                    : 'Pick a feature first'} />
                </SelectTrigger>
                <SelectContent>
                  {metricsInFeature.map((m) => (
                    <SelectItem key={m.key} value={m.key}>{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* Stated, never offered: the metric owns its direction and
                  a comparator with one useful value only invites a wrong
                  answer. */}
              <span className="text-xs text-muted-foreground">
                {spec?.direction ?? ''}
              </span>

              <Input
                type="number"
                inputMode="decimal"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                disabled={!spec}
                min={spec?.min}
                max={spec?.max}
                step={spec && spec.hysteresis < 1 ? 0.1 : 1}
                aria-describedby="trg-help"
                aria-invalid={!!rangeError()}
                placeholder={spec ? `${spec.min}–${spec.max}` : '—'}
                aria-label={spec
                  ? `Threshold in ${spec.unit}, between ${spec.min} and ${spec.max}`
                  : 'Threshold'}
                className="h-8 w-24 text-xs"
              />
              <span className="text-xs text-muted-foreground">{spec?.unit ?? ''}</span>
            </div>
            {failed && (
              <p className="text-2xs text-danger">
                Couldn’t load what can be watched.{' '}
                <button type="button" onClick={() => void load()}
                        className="text-primary hover:underline min-h-tap">
                  Try again
                </button>
              </p>
            )}
            <p id="trg-help" className="text-2xs text-muted-foreground">
              {spec ? (
                <>
                  {rangeError() && <span className="text-danger">{rangeError()} </span>}
                  Between {spec.min} and {spec.max}{spec.unit}. {spec.hint}
                  {spec.requires_engine === 'on'
                    && ' Checked only while the engine is running.'}
                  {' '}Re-checked every {spec.checked_every_minutes} minutes.
                </>
              ) : 'Pick what to watch, then the number.'}
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium uppercase tracking-wide
                             text-muted-foreground">
              On which vehicles
            </span>
            {/* A Switch, NOT a checkbox, and the rule names this exact
                mistake: "Never mix shapes in one vertical run: the pivot
                panel stacked a zone setting directly above its field rows
                as the same checkbox, so five identical boxes in one
                column meant two unrelated things."  Below this sits a run
                of N identical checkboxes meaning MEMBERSHIP (is this
                vehicle in the set); a checkbox here would have been the
                sixth identical box meaning something else entirely, and
                would read as select-all.
                Switch is also the honest primitive: this is a BEHAVIOUR
                ("limit this trigger"), not membership.  Off is the
                default because watching everything is the safe, commonest
                answer — and it keeps meaning "everything" as the fleet
                grows, which a snapshot of ticked boxes would not. */}
            <div className="flex items-center gap-2">
              <Switch
                size="sm"
                checked={!allMine}
                onCheckedChange={(on) => setAllMine(!on)}
                aria-label="Limit this trigger to specific vehicles"
              />
              <span className="text-xs">
                Limit to specific vehicles
                <span className="text-muted-foreground">
                  {allMine
                    ? ` — watching all ${fleet.length}, including any added later`
                    : ''}
                </span>
              </span>
            </div>

            {!allMine && (
              <div className="flex flex-col gap-2">
                <div className="relative">
                  <Search className="size-3.5 absolute left-2 top-1/2 -translate-y-1/2
                                     text-muted-foreground" aria-hidden />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search by unit or company…"
                    aria-label="Search vehicles"
                    className="h-8 pl-7 text-xs"
                  />
                </div>
                <p className="text-2xs text-muted-foreground" aria-live="polite">
                  {picked.length} selected
                  {unwatchable > 0 && ` · ${unwatchable} of your vehicles report no telemetry yet`}
                </p>
                {/* ScrollRegion, not a bare overflow div: this list runs
                    to the whole roster (189 active on the live account),
                    which is past the "short picker list" carve-out. It
                    brings the focusability a plain overflow div lacks
                    (WCAG 2.1.1 — otherwise a keyboard user cannot scroll
                    it at all), plus overscroll-contain so a wheel at the
                    list's end does not scroll the page behind it. */}
                <ScrollRegion label="Vehicles"
                              className="max-h-64 rounded-md border
                                         border-border divide-y divide-border/60">
                  {/* Three different empties, three different answers.
                      One branch for all of them rendered a failed fetch
                      as `No vehicle matches ""` — an error dressed as a
                      fact about the search someone had not typed. */}
                  {!loaded ? (
                    <p className="text-xs text-muted-foreground p-3">Loading…</p>
                  ) : failed ? (
                    <p className="text-xs text-muted-foreground p-3">
                      Couldn’t load your vehicles.{' '}
                      <button type="button" onClick={() => void load()}
                              className="text-primary hover:underline min-h-tap">
                        Try again
                      </button>
                    </p>
                  ) : fleet.length === 0 ? (
                    <p className="text-xs text-muted-foreground p-3">
                      You don’t have any vehicles to pick from yet.
                    </p>
                  ) : shown.length === 0 ? (
                    <p className="text-xs text-muted-foreground p-3">
                      No vehicle matches “{query}”.
                    </p>
                  ) : shown.map((v) => (
                    <label key={v.id}
                           className="flex items-center gap-2 px-3 py-2 text-xs
                                      cursor-pointer min-h-tap">
                      <input type="checkbox"
                             className="accent-primary min-h-tap min-w-tap"
                             checked={picked.includes(v.id)}
                             onChange={() => toggleVehicle(v.id)} />
                      <span className="flex-1 min-w-0 truncate">
                        {v.name || `#${v.id}`}
                        {v.company && (
                          <span className="text-muted-foreground"> · {v.company}</span>
                        )}
                      </span>
                      {/* Says what happens to the TRIGGER, not a fact
                          about the vehicle.  "no telemetry yet" reads as
                          a spec line someone can shrug at; the person
                          needs to know that picking this buys silence
                          until the vehicle reports. */}
                      {!v.watchable && (
                        <span className="text-2xs text-muted-foreground shrink-0">
                          won’t fire until it reports
                        </span>
                      )}
                    </label>
                  ))}
                </ScrollRegion>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2
                          border-t border-border pt-3">
          {/* No icon: this was the only Cancel in the app with one. */}
          <Button type="button" variant="ghost" onClick={requestClose}>
            Cancel
          </Button>
          <div className="flex items-center gap-2">
            {blocked && (
              <span className="text-2xs text-muted-foreground" aria-live="polite">
                {blockedBecause()}
              </span>
            )}
            <Button type="submit" aria-disabled={blocked}>
              {editing ? 'Save changes' : 'Add trigger'}
            </Button>
          </div>
          </div>
        </form>
  );
}

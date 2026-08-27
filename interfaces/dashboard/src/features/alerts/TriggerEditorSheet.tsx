/**
 * Write one trigger: what to watch, at what number, on which vehicles.
 *
 * A Sheet rather than the inline row it replaced, and the vehicle list is
 * the reason.  "Watch [metric] below [n] %" fitted on one line; a fleet
 * of 189 vehicles does not, and squeezing it into a popover on that line
 * would have made the most consequential choice in the form the smallest
 * control on it.
 *
 * Two things here are stated rather than asked:
 *
 * FEATURE is a heading, not a select.  Every metric the catalog carries
 * today belongs to Vehicles, and a dropdown with one option is a decision
 * nobody makes.  It is rendered as a SelectGroup label so the grouping is
 * already visible and already correct — the day a Cameras metric is
 * registered, a second group appears with no change here.  The word
 * matches the "Feature" column on the Alerts board and the delivery
 * settings, which is the same dimension under the same name.
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
import {
  Sheet, SheetContent, SheetBody, SheetDescription, SheetFooter,
  SheetHeader, SheetTitle,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ScrollRegion } from '@/components/scrolling';
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel,
  SelectTrigger, SelectValue,
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

export default function TriggerEditorSheet({ open, onClose, onSaved }: {
  open: boolean;
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

  useEffect(() => {
    if (!open) return;
    setMetric(''); setValue(''); setPicked([]); setAllMine(true); setQuery('');
    void load();
    requestAnimationFrame(() => metricRef.current?.focus());
  }, [open, load]);

  const spec = metrics.find((m) => m.key === metric);

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
    if (!spec) return 'Pick what to watch.';
    if (value === '') return 'Enter a number.';
    if (rangeError()) return rangeError();
    if (!allMine && picked.length === 0) return 'Pick at least one vehicle.';
    return '';
  };
  const blocked = !!blockedBecause();

  const save = async () => {
    if (blocked || busy) return;
    setBusy(true);
    try {
      await apiJSON(API, {
        method: 'POST',
        body: {
          metric, threshold: Number(value),
          // Empty array and "all mine" are the same wire value on
          // purpose — the server's '' already means all.
          vehicles: allMine ? [] : picked,
        },
      });
      // Every failure toasted and success said nothing, so the one
      // outcome a person most wants confirmed was the silent one.
      toast.success(`Watching ${spec?.label.toLowerCase()} ${spec?.direction} `
        + `${value}${spec?.unit} on `
        + (allMine ? 'every vehicle you can see'
                   : `${picked.length} vehicle${picked.length === 1 ? '' : 's'}`));
      onSaved();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add that trigger');
    } finally {
      setBusy(false);
    }
  };

  // Anything typed or picked that would be lost.  Not a generic "dirty"
  // flag: opening the Sheet and closing it again must not nag, so this
  // asks whether there is WORK here, and picking 20 of 189 vehicles is
  // several minutes of it.
  const hasWork = metric !== '' || value !== '' || picked.length > 0;

  const requestClose = () => {
    if (busy) return;
    if (hasWork && !window.confirm(
      'Discard this trigger? What you picked here isn’t saved yet.')) {
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
    // Escape and a backdrop click are the same intent as Cancel, so they
    // get the same guard — a selection that took minutes to build must
    // not vanish to a mis-aimed click.
    <Sheet open onOpenChange={(o) => { if (!o) requestClose(); }}>
      <SheetContent side="right" size="lg" aria-label="Add an alert trigger">
        <SheetHeader className="px-5 py-4 border-b border-border shrink-0">
          <SheetTitle>Add a trigger</SheetTitle>
          {/* SheetDescription, not a styled <p>: the primitive is what
              wires aria-describedby onto the dialog, so this caveat —
              the single most load-bearing sentence in the form — is
              announced WITH the sheet rather than only seen. */}
          <SheetDescription>
            You hear on the <span className="text-foreground">crossing</span> —
            a vehicle already past your number when you save stays quiet.
          </SheetDescription>
        </SheetHeader>

        {/* A real <form>, restored deliberately.  The inline row this
            Sheet replaced was one, and its comment said why: "Enter
            submits and Escape cancels, which is what anyone typing a
            number expects.  A plain div of inputs answers neither key."
            Moving to a Sheet dropped the form and with it Enter — a
            regression against intent this feature had already written
            down.  Escape is the Sheet's own. */}
        <form
          onSubmit={(e) => { e.preventDefault(); void save(); }}
          className="contents"
        >
        <SheetBody label="Add a trigger"
                   className="px-5 py-4 flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <span id="trg-watch-lbl" className="text-xs font-medium
                                                uppercase tracking-wide
                                                text-muted-foreground">
              Watch
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={metric} onValueChange={(v) => setMetric(v ?? '')}
                      items={metrics.map((m) => ({ value: m.key, label: m.label }))}>
                <SelectTrigger ref={metricRef} id="trg-metric"
                               aria-labelledby="trg-watch-lbl trg-metric"
                               className="h-8 w-56 text-xs">
                  <SelectValue placeholder="Pick a metric" />
                </SelectTrigger>
                <SelectContent>
                  {groups.map(([key, g]) => (
                    <SelectGroup key={key}>
                      <SelectLabel>{g.label}</SelectLabel>
                      {g.items.map((m) => (
                        <SelectItem key={m.key} value={m.key}>{m.label}</SelectItem>
                      ))}
                    </SelectGroup>
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
                    it at all), plus overscroll-contain inside a Sheet. */}
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
        </SheetBody>

        <SheetFooter className="px-5 py-4 border-t border-border shrink-0
                                flex-row justify-end gap-2">
          {/* No icon: this is the only Cancel in the app that had one,
              and the Sheet already draws a ✕ in the opposite corner —
              two glyphs for one verb. */}
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
              Add trigger
            </Button>
          </div>
        </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

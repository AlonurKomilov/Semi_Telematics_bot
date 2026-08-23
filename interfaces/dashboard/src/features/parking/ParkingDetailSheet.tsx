/**
 * Per-vehicle parking detail — the panel a grid row opens.
 *
 * A side sheet rather than a dialog: the list stays visible behind it, so
 * moving row-to-row during triage doesn't lose your place. That motion is
 * the whole reason the card list became a grid.
 *
 * It also carries what the card list could never show — the vehicle's
 * OTHER parking events. "Is this truck always parking badly?" is the
 * question a dispatcher actually has, and answering it previously meant
 * switching tabs and filtering by hand.
 */
import { useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';

import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription,
} from '../../components/ui/sheet';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { parseAiAnalysis } from './aiAnalysis';
import {
  ClassBadge, AlertBadge, ConfidenceBadge, DimBadge, StatusBadge,
} from './badges';
import { formatDuration, mapsUrl, durationToneClass } from './columns';
import {
  fetchParkingMapImage, listVehicleParkingHistory, type ParkingEvent,
} from './api';

interface Props {
  event: ParkingEvent | null;
  onClose: () => void;
  canResolve: boolean;
  onResolve: (e: ParkingEvent) => void;
}

export default function ParkingDetailSheet({
  event, onClose, canResolve, onResolve,
}: Props) {
  const tz = useTimezone();
  // Which event the panel is SHOWING.  Seeded from the row that opened
  // the drawer, then repointed when a history row is clicked — so a past
  // stop gets the same reason + snapshot treatment as the live one,
  // without a second drawer or a navigation stack.
  const [viewing, setViewing] = useState<ParkingEvent | null>(event);
  const [mapUrl, setMapUrl] = useState('');
  const [mapError, setMapError] = useState('');
  const [history, setHistory] = useState<ParkingEvent[] | null>(null);

  // Re-seed whenever the drawer is opened on a different row.
  useEffect(() => { setViewing(event); }, [event]);

  const shown = viewing ?? event;
  const id = shown?.id ?? null;
  // History follows the VEHICLE, not the shown event, so clicking through
  // past stops never re-fetches the same list.
  const vehicleId = event?.vehicle_id ?? '';

  // Snapshot: fetched per event, and the object URL is REVOKED on
  // change/close.  The card list created one per expand and never
  // released any, so a long triage session leaked a blob per event.
  useEffect(() => {
    if (!shown || !shown.map_image_path) {
      setMapUrl(''); setMapError('');
      return;
    }
    let url = '';
    let cancelled = false;
    setMapUrl(''); setMapError('');
    fetchParkingMapImage(shown.id)
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          // 410 is distinct from 404 on purpose.  Before snapshots were
          // keyed per event, every stop by a truck overwrote the previous
          // stop's file, so an older event's stored path now resolves to a
          // NEWER stop's map.  Saying "no snapshot" there would be false —
          // one was taken — and showing it would caption another location
          // with this event's address and AI reasoning.
          setMapError(
            res.status === 410
              ? 'Snapshot was overwritten by a later stop for this vehicle, so it is no longer this stop’s map.'
              : res.status === 404
                ? 'No snapshot stored for this event.'
                : `Could not load snapshot (HTTP ${res.status}).`,
          );
          return;
        }
        url = URL.createObjectURL(await res.blob());
        if (cancelled) { URL.revokeObjectURL(url); return; }
        setMapUrl(url);
      })
      .catch(() => { if (!cancelled) setMapError('Could not load snapshot — network error.'); });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, shown]);

  useEffect(() => {
    if (!vehicleId) { setHistory(null); return; }
    let cancelled = false;
    setHistory(null);
    listVehicleParkingHistory(vehicleId)
      .then((r) => { if (!cancelled) setHistory(r.events); })
      .catch(() => { if (!cancelled) setHistory([]); });
    return () => { cancelled = true; };
  }, [vehicleId]);

  if (!event || !shown) return null;
  const ai = parseAiAnalysis(shown.ai_analysis ?? '');
  const others = (history ?? []).filter((h) => h.id !== shown.id);
  const isLive = shown.id === event.id;

  return (
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      {/* xl, not the sm default: this panel pairs prose with an image, and
          24rem forces both to fight for the same width. */}
      <SheetContent side="right" size="xl" className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            {event.vehicle_name}
            <span className="text-xs font-normal text-muted-foreground">{event.company_code}</span>
            {/* Stable handle for cross-referencing with Alerts or support. */}
            <span className="text-xs font-normal font-mono text-muted-foreground">#{shown.id}</span>
            {!isLive && (
              <button
                onClick={() => setViewing(event)}
                className="text-xs font-normal text-primary hover:underline py-1 -my-1 min-h-tap"
              >
                ← back to current stop
              </button>
            )}
          </SheetTitle>
          <SheetDescription>
            {shown.address || `${shown.latitude.toFixed(5)}, ${shown.longitude.toFixed(5)}`}
          </SheetDescription>
        </SheetHeader>

        <div className="px-4 pb-4 space-y-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <DimBadge label="Status"><StatusBadge resolved={shown.resolved} /></DimBadge>
            <DimBadge label="Location"><ClassBadge cls={shown.location_class} /></DimBadge>
            <DimBadge label="Severity"><AlertBadge level={shown.alert_level} /></DimBadge>
            {ai.confidence && (
              <DimBadge label="AI confidence"><ConfidenceBadge value={ai.confidence} /></DimBadge>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Duration</dt>
              <dd className={durationToneClass(shown.duration_hours)}>
                {formatDuration(shown.duration_hours)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Stopped at</dt>
              <dd>{shown.first_stopped ? formatDate(shown.first_stopped, { timeZone: tz }) : '—'}</dd>
            </div>
          </dl>

          {/* The reason is the primary content of this panel — it gets the
              room, and the snapshot supporting it is capped below it. */}
          {(ai.reason || ai.rest) && (
            <section>
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
                Why this was flagged
              </h3>
              <p className="text-sm text-foreground bg-muted rounded p-3 whitespace-pre-wrap">
                {ai.reason || ai.rest}
              </p>
            </section>
          )}

          {/* Always mounted — never `{shown.map_image_path && …}`.  Clicking a
              history row repoints this panel, and a past stop with no stored
              snapshot used to unmount this whole section, jumping the "Other
              stops" list ~280px up THE INSTANT the pointer was over it. The
              list is what the user is clicking through, so the region that
              sits above it has to hold its place even when it has nothing to
              show. */}
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
              AI location snapshot
            </h3>
            {!shown.map_image_path ? (
              <p className="text-xs text-muted-foreground">No snapshot stored for this event.</p>
            ) : mapUrl ? (
              <img
                src={mapUrl}
                alt={`AI location snapshot for ${event.vehicle_name}`}
                className="rounded-lg border border-border max-w-full max-h-64 object-contain"
              />
            ) : mapError ? (
              <p className="text-xs text-destructive">{mapError}</p>
            ) : (
              <p className="text-xs text-muted-foreground">Loading snapshot…</p>
            )}
          </section>

          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
              Other stops for this vehicle
            </h3>
            {history === null ? (
              <p className="text-xs text-muted-foreground">Loading…</p>
            ) : others.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No other parking events recorded for {event.vehicle_name}.
              </p>
            ) : (
              <ul className="divide-y divide-border rounded-lg border border-border">
                {others.map((h) => (
                  <li key={h.id}>
                    {/* A real button, not a clickable <li>: keyboard and
                        screen-reader users get the same affordance, and the
                        row is the whole hit area rather than its text. */}
                    <button
                      type="button"
                      onClick={() => setViewing(h)}
                      className="w-full flex items-center gap-3 px-3 py-2 text-xs text-left hover:bg-muted transition"
                    >
                      <span className="font-mono text-muted-foreground shrink-0">#{h.id}</span>
                      <span className="text-muted-foreground shrink-0">
                        {h.first_stopped ? formatDate(h.first_stopped, { timeZone: tz }) : '—'}
                      </span>
                      <span className="truncate flex-1">{h.address || '—'}</span>
                      <ClassBadge cls={h.location_class} />
                      <span className={`shrink-0 ${durationToneClass(h.duration_hours)}`}>
                        {formatDuration(h.duration_hours)}
                      </span>
                      {/* Drill-in affordance at REST, not on hover: without it
                          this bordered list is indistinguishable from a
                          read-only table until the pointer happens to land on
                          a row, and the whole point of the panel is that these
                          rows open. */}
                      <ChevronRight
                        size={14}
                        className="shrink-0 text-muted-foreground"
                        aria-hidden
                      />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Weight follows CONSEQUENCE, not frequency.  These two were
              rendered identically: one opens a map tab, the other closes an
              alert permanently and clears pending acknowledgements.  Resolve
              leads as the filled primary; the maps link demotes to a plain
              link so the irreversible action is never the quieter of the
              two. */}
          <div className="flex items-center gap-3 pt-2">
            {canResolve && !shown.resolved && (
              <button
                onClick={() => onResolve(shown)}
                className="px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-lg text-xs font-medium text-primary-foreground transition min-h-tap"
              >
                Resolve
              </button>
            )}
            {/* py-1.5 is not decoration: without it this is a ~16px hit box,
                under the 24px WCAG 2.5.8 floor, sitting beside a 28px button.
                It isn't an inline link in a sentence, so no exception applies.
                The padding buys the target height while the absent fill and
                border keep it visually subordinate to Resolve. */}
            <a
              href={mapsUrl(shown.latitude, shown.longitude)}
              target="_blank"
              rel="noopener noreferrer"
              className="-mx-1 inline-flex items-center px-1 py-1.5 min-h-tap rounded text-xs font-medium text-primary hover:underline"
            >
              Open in Google Maps
            </a>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

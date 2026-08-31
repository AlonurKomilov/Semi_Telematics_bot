import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X, Download, Truck, User, MapPin, Gauge, Clock, Loader2 } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { formatDate, formatDay, formatTime } from '@/utils/datetime';
import { useTimezone } from '@/hooks/useTimezone';
import type { SafetyEvent } from '@/types';
import { Badge } from '@/components/ui/badge';

interface VideoUrlResponse {
  event_id: string;
  angle: 'forward' | 'inward';
  url: string;
  forward_url: string;
  inward_url: string;
}

/**
 * Inline video viewer for a safety event.
 *
 * Replaces the previous "View → open S3 link in a new tab" behavior so
 * the dispatcher sees the clip + event context in one place (matching
 * Samsara's own player layout) and can still grab the raw file when
 * needed.
 *
 * Auth flow: the dashboard fetches the fresh S3 URL via the bearer-token
 * authenticated `/api/safety/events/{id}/video` endpoint, then assigns
 * the returned URL directly to ``<video src>``.  We can't point
 * ``<video>`` at the API endpoint because plain media GETs don't carry
 * the ``Authorization`` header, so the JWT-required dependency would
 * 422 the request.  S3 doesn't need auth, so once we have the signed
 * URL the browser plays it directly.
 */
export default function EventVideoModal({
  event,
  onClose,
}: {
  event: SafetyEvent;
  onClose: () => void;
}) {
  const tz = useTimezone();
  const [angle, setAngle] = useState<'forward' | 'inward'>('forward');
  const [videoUrl, setVideoUrl] = useState<string>('');
  const [loadError, setLoadError] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  // Escape is <Dialog>'s job now.  The hand-rolled listener that used to
  // live here worked, but it was the ONLY part of modal behaviour this
  // had: no focus trap (Tab walked out into the page behind the video)
  // and no background scroll lock.  ``role="dialog" aria-modal="true"``
  // were written on a plain div, which is a claim, not an implementation.

  const hasInward = !!event.inward_video_url;

  // Fetch the fresh signed URL on mount + every angle switch.  Cached
  // by the browser's network layer if the user toggles back, but the
  // backend always re-pulls from Samsara so the URL is current.
  const fetchUrl = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    setVideoUrl('');
    try {
      const resp = await apiJSON<VideoUrlResponse>(
        `/safety/events/${encodeURIComponent(event.event_id)}/video?angle=${angle}`,
      );
      setVideoUrl(resp.url);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load video URL');
    } finally {
      setLoading(false);
    }
  }, [event.event_id, angle]);

  useEffect(() => { fetchUrl(); }, [fetchUrl]);

  // Trigger a fresh load on the <video> element when the src changes so
  // the previous angle's frame isn't lingering when the user flips tabs.
  useEffect(() => {
    const el = videoRef.current;
    if (el && videoUrl) { el.load(); }
  }, [videoUrl]);

  // Strip "Violation" / "Detected" / "Detection" / "Event" suffix
  // words from the title so the chip doesn't read as accusatory and
  // matches the cleaned label in the Events table.
  const NOISE_SUFFIX_RE = /\s+(?:Violation|Detected|Detection|Event)$/i;
  const eventTitle = (() => {
    if (!event.event_type) return 'Safety Event';
    let label = event.event_type.replace(/([A-Z])/g, ' $1').replace(/^./, (c) => c.toUpperCase()).trim();
    for (let i = 0; i < 4 && NOISE_SUFFIX_RE.test(label); i++) {
      label = label.replace(NOISE_SUFFIX_RE, '').trim();
    }
    return label;
  })();
  const eventTime = event.time ? new Date(event.time) : null;
  const dateStr = formatDay(eventTime, {
    timeZone: tz,
    intl: { year: 'numeric', month: 'long', day: 'numeric' },
  });
  const timeStr = formatTime(eventTime, {
    timeZone: tz,
    intl: { hour: '2-digit', minute: '2-digit', second: '2-digit' },
  });

  const latLng = (event.latitude != null && event.longitude != null)
    ? `${event.latitude.toFixed(6)}, ${event.longitude.toFixed(6)}`
    : null;
  const mapsHref = latLng
    ? `https://www.google.com/maps?q=${event.latitude},${event.longitude}`
    : null;

  const downloadName = useMemo(
    () => `event-${event.event_id}-${angle}.mp4`,
    [event.event_id, angle],
  );

  return (
    // Unlike the inspection lightbox, the backdrop here is NOT the
    // surface — the player sits in a real card, so this is an ordinary
    // centred dialog and takes the primitive's own chrome.
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent
        size="5xl" className="w-full max-h-[92vh] overflow-hidden flex flex-col p-0 gap-0"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">
          Event video — {dateStr}
        </DialogTitle>
        {/* Header — date · vehicle · driver · time (matches Samsara layout) */}
        <div className="flex items-center justify-between gap-4 px-4 py-3 bg-black text-white">
          <div className="flex items-center gap-4 text-sm">
            <span className="font-medium">{dateStr}</span>
            <span className="opacity-30">•</span>
            <span className="inline-flex items-center gap-1.5">
              <Truck className="opacity-70 size-3.5" />
              {event.vehicle_name || '—'}
            </span>
            <span className="opacity-30">•</span>
            <span className="inline-flex items-center gap-1.5">
              <User className="opacity-70 size-3.5" />
              {event.driver_name || 'Unassigned'}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-sm tabular-nums">{timeStr}</span>
            <button
              onClick={onClose}
              aria-label="Close"
              className="inline-flex size-8 items-center justify-center rounded-md hover:bg-white/10 transition-colors"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>

        {/* Camera-angle tabs (only when inward is available) */}
        {/* Opaque, and white-based text — this strip is player chrome,
            like the header above it, not app chrome. It used to be
            `bg-black/40`, which is TRANSPARENT: over the light theme's
            white popover it resolved to a mid-grey (L 0.32), and
            `text-muted-foreground` is a mid-grey too — the inactive tab
            measured 1.66:1 and could not be read. (The active tab was
            fine at 6.95; the defect was only ever the inactive one.)
            Opaque black renders the same in both themes, which is what
            media surround should do. The active underline stays
            `border-primary` so the accent still reads here. */}
        {hasInward && (
          <div className="flex border-b border-white/10 bg-black">
            {(['forward', 'inward'] as const).map((a) => (
              <button
                key={a}
                onClick={() => setAngle(a)}
                className={`px-4 py-2 text-xs font-medium capitalize transition ${
                  angle === a
                    ? 'text-white border-b-2 border-primary'
                    : 'text-white/60 hover:text-white'
                }`}
              >
                {a} camera
              </button>
            ))}
          </div>
        )}

        {/* Video — native HTML5 player; supports fullscreen + PiP + speed.
            Three render states: loading (spinner), error (message +
            retry), ready (the actual video element with the fresh URL). */}
        <div className="bg-black flex items-center justify-center min-h-[40vh] max-h-[60vh]">
          {loading ? (
            <div className="flex flex-col items-center gap-2 text-white/70 py-12">
              <Loader2 className="animate-spin size-6" />
              <span className="text-xs">Loading video…</span>
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center gap-3 text-white/70 py-12 px-6 text-center">
              <span className="text-sm">{loadError}</span>
              <button
                onClick={fetchUrl}
                className="px-3 py-1.5 rounded-md bg-white/10 hover:bg-white/20 text-xs font-medium min-h-tap"
              >
                Try again
              </button>
            </div>
          ) : (
            <video
              ref={videoRef}
              controls
              preload="metadata"
              controlsList="nodownload"
              className="w-full max-h-[60vh]"
            >
              <source src={videoUrl} type="video/mp4" />
              Your browser does not support video playback.
            </video>
          )}
        </div>

        {/* Footer strip — event type pill + meta chips + Download */}
        <div className="px-4 py-3 bg-card/80 border-t border-border flex flex-wrap items-center gap-3">
          <span className="px-2 py-0.5 rounded-md text-xs font-semibold bg-primary/15 text-foreground ring-1 ring-primary">
            {eventTitle}
          </span>
          {event.severity && (
            <Badge tone="warn" className="capitalize">
              {event.severity}
            </Badge>
          )}
          {event.g_force > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Gauge className="size-3" /> {event.g_force.toFixed(2)} g
            </span>
          )}
          {eventTime && (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Clock className="size-3" /> {formatDate(eventTime, { timeZone: tz })}
            </span>
          )}
          {latLng && (
            <a
              href={mapsHref!}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline min-h-tap"
              title="Open in Google Maps"
            >
              <MapPin className="size-3" /> {latLng}
            </a>
          )}
          {/* Download — points at the fresh S3 URL directly so the
              browser can stream the file without going through our
              proxy.  Disabled while we're still fetching the URL. */}
          <a
            href={videoUrl || '#'}
            download={downloadName}
            target="_blank"
            rel="noopener noreferrer"
            aria-disabled={!videoUrl}
            onClick={(e) => { if (!videoUrl) e.preventDefault(); }}
            className={`ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              videoUrl
                ? 'bg-primary/15 hover:bg-primary/25 text-foreground ring-1 ring-primary'
                : 'bg-muted text-muted-foreground cursor-not-allowed'
            } min-h-tap`}
          >
            <Download className="size-3.5" />
            Download
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}

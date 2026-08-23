/**
 * Address + speed + coordinates row triplet, used by the Location
 * section.  Address text becomes a Google Maps deep-link anchored at
 * the exact lat/lng, and coordinates are shown with a one-click copy
 * button.  Extracted verbatim from the original VehicleDetail.tsx.
 */
import { useState } from 'react';
import { Copy, Check, ExternalLink } from 'lucide-react';
import { Freshness, Tip } from '../../../../components/tooltip';
import { Row } from './Row';

interface LocationRowsProps {
  address?: string | null;
  latitude: number | null;
  longitude: number | null;
  speedMph: number | null;
  /** GPS fix time — ONE clock for all three rows.  Address carries the
   *  visible staleness cue; Speed/Coordinates get tooltip-only so a
   *  stale fix doesn't paint three dots. */
  ts?: string | null;
}

export function LocationRows({
  address,
  latitude,
  longitude,
  speedMph,
  ts,
}: LocationRowsProps) {
  const [copied, setCopied] = useState(false);
  const hasCoords = latitude != null && longitude != null;
  const coordsText = hasCoords ? `${latitude}, ${longitude}` : '';
  const mapsHref = hasCoords
    ? `https://www.google.com/maps/search/?api=1&query=${latitude},${longitude}`
    : null;

  async function copyCoords() {
    if (!coordsText) return;
    try {
      await navigator.clipboard.writeText(coordsText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be blocked — silently no-op */
    }
  }

  return (
    <>
      <div className="flex justify-between text-sm gap-3">
        <span className="text-muted-foreground flex-shrink-0">Address</span>
        <Freshness ts={ts}>
          {address && mapsHref ? (
            <Tip label="Open in Google Maps">
            <a
              href={mapsHref}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-1 text-right min-h-tap"
            >
              <span>{address}</span>
              <ExternalLink size={12} className="flex-shrink-0 opacity-70" />
            </a>
            </Tip>
          ) : (
            <span>{address || '—'}</span>
          )}
        </Freshness>
      </div>
      <Row label="Speed" ts={ts} cue={false} value={speedMph != null ? `${speedMph} mph` : '—'} />
      <div className="flex justify-between items-center text-sm gap-3 py-1 -my-1 min-h-tap">
        <span className="text-muted-foreground flex-shrink-0">Coordinates</span>
        {hasCoords ? (
          <span className="inline-flex items-center gap-2">
            <Freshness ts={ts} cue={false}>
              <span className="font-mono text-xs">{coordsText}</span>
            </Freshness>
            <Tip label={copied ? 'Copied' : 'Copy to clipboard'}>
            <button
              type="button"
              onClick={copyCoords}
              className="text-muted-foreground hover:text-foreground transition-colors py-0.5 -my-0.5 min-h-tap"
              aria-label={copied ? 'Coordinates copied' : 'Copy coordinates'}
            >
              {copied ? (
                <Check size={14} className="text-ok" />
              ) : (
                <Copy size={14} />
              )}
            </button>
            </Tip>
          </span>
        ) : (
          <span>—</span>
        )}
      </div>
    </>
  );
}

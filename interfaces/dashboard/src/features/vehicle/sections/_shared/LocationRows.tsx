/**
 * Address + speed + coordinates row triplet, used by the Location
 * section.  Address text becomes a Google Maps deep-link anchored at
 * the exact lat/lng, and coordinates are shown with a one-click copy
 * button.  Extracted verbatim from the original VehicleDetail.tsx.
 */
import { useState } from 'react';
import { Copy, Check, ExternalLink } from 'lucide-react';
import { Row } from './Row';

interface LocationRowsProps {
  address?: string | null;
  latitude: number | null;
  longitude: number | null;
  speedMph: number | null;
}

export function LocationRows({
  address,
  latitude,
  longitude,
  speedMph,
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
        {address && mapsHref ? (
          <a
            href={mapsHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline inline-flex items-center gap-1 text-right"
            title="Open in Google Maps"
          >
            <span>{address}</span>
            <ExternalLink size={12} className="flex-shrink-0 opacity-70" />
          </a>
        ) : (
          <span>{address || '—'}</span>
        )}
      </div>
      <Row label="Speed" value={speedMph != null ? `${speedMph} mph` : '—'} />
      <div className="flex justify-between items-center text-sm gap-3">
        <span className="text-muted-foreground flex-shrink-0">Coordinates</span>
        {hasCoords ? (
          <span className="inline-flex items-center gap-2">
            <span className="font-mono text-xs">{coordsText}</span>
            <button
              type="button"
              onClick={copyCoords}
              className="text-muted-foreground hover:text-foreground transition-colors"
              title={copied ? 'Copied' : 'Copy to clipboard'}
              aria-label={copied ? 'Coordinates copied' : 'Copy coordinates'}
            >
              {copied ? (
                <Check size={14} className="text-green-500" />
              ) : (
                <Copy size={14} />
              )}
            </button>
          </span>
        ) : (
          <span>—</span>
        )}
      </div>
    </>
  );
}

/**
 * Map colour constants — single source of truth for every colour drawn
 * on a Leaflet map: vehicle markers, fault/maintenance rings, geofence
 * polygons, utilisation heatmaps, route lines and popup links.
 *
 * WHY THIS IS SEPARATE FROM THE CSS DESIGN TOKENS (index.css):
 * Leaflet draws markers to <canvas> or takes colours as plain STRINGS
 * passed to its APIs (L.circleMarker({color}), divIcon HTML, heatmap
 * gradients).  In those contexts `var(--ok)` cannot resolve, so map
 * colours must be literal values.  This file is the ONE place they live
 * — never inline a hex inside a *Layer / map component again (design.md
 * §8).  Theme-aware UI chrome still uses the CSS tokens; only the map
 * canvas uses these.
 *
 * MAP_STATUS carries the same MEANING as the --ok/--warn/--danger/--info
 * design tokens but uses the vivid 500-weight variants — saturated dots
 * read better on map tiles than the contrast-tuned text tokens.  If you
 * retune the palette hues, keep these aligned.
 */

export const MAP_STATUS = {
  ok:      '#22c55e', // moving / safe / on-track
  warn:    '#f59e0b', // idle / due-soon / caution
  danger:  '#ef4444', // stopped / overdue / unsafe
  info:    '#3b82f6', // informational / in-progress
  neutral: '#6b7280', // stopped-crawl / no-signal
} as const;

/**
 * Categorical palette for distinguishing companies on the fleet map.
 * Index = assignment order; callers wrap with `% length` when there are
 * more companies than colours.  Deliberately avoids the MAP_STATUS hues
 * so a company colour is never mistaken for a moving/idle/stopped signal.
 */
export const COMPANY_PALETTE: string[] = [
  '#7c3aed', // violet
  '#0891b2', // cyan
  '#16a34a', // green
  '#ca8a04', // amber
  '#db2777', // pink
  '#0284c7', // sky
  '#9333ea', // purple
  '#65a30d', // lime
];

/** Geofence overlay colours — categorical identity by source/state. */
export const GEOFENCE = {
  platform: '#10b981', // geofence drawn inside 4truck
  samsara:  '#3b82f6', // geofence imported from Samsara
  preview:  '#f59e0b', // unsaved draft currently being drawn
  fill:     '#3b82f6', // default boundary fill
  stroke:   '#2563eb', // default boundary stroke
} as const;

/** Utilisation heatmap gradient stops (low → high), sequential blues. */
export const HEATMAP_GRADIENT: Record<number, string> = {
  0.2: '#0891b2',
  0.5: '#0284c7',
  0.8: '#1d4ed8',
  1.0: '#1e40af',
};

/** Swatch choices offered in the custom-layer colour picker. */
export const CUSTOM_LAYER_SWATCHES: string[] = [
  '#7c3aed', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#6366f1', '#14b8a6', '#84cc16', '#64748b',
];

/** Brand link colour for <a> tags inside Leaflet popup HTML strings. */
export const POPUP_LINK = '#2563eb';

/** Neutral chrome inside Leaflet popup HTML strings (badges + secondary
 *  text).  Popup markup is a plain string, so these can't be CSS tokens
 *  — same rule as everything else in this file (design.md §8). */
export const POPUP = {
  badgeBg:   '#374151', // amenity-badge background
  badgeText: '#e5e7eb', // amenity-badge text
  muted:     '#9ca3af', // secondary text (subtitle, meta row)
} as const;

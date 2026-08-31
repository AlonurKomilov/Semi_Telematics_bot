/**
 * POI Layer Registry — single source of truth for all map overlay layers.
 *
 * STRUCTURE:
 *   POI_GROUPS  → display-only headers in the toggle panel (NOT toggleable).
 *   POI_LAYERS  → individual data layers (each toggleable, with optional brand chips).
 *
 * Each layer's `group` field links it to a POI_GROUPS entry.  Layers without a
 * `group` are rendered ungrouped at the top of the panel.
 *
 * To ADD a new layer:
 *   1. Add one entry to POI_LAYERS below.
 *   2. Add a matching key to POI_OVERPASS_QUERIES in features/location/pois.py
 *      (or, for a DB-backed layer, a source branch in map_pois there).
 *   The hook and the panel pick it up automatically.
 *   If the layer's feature properties are NOT OSM tags, also give it a
 *   `popup` builder — the default popup renders OSM amenity badges.
 *
 * To REMOVE a layer:
 *   1. Delete the entry from POI_LAYERS (and the backend query if no longer used).
 */

import {
  BedDouble,
  FlaskConical,
  FolderOpen,
  Fuel,
  Scale,
  ShowerHead,
  SquareParking,
  Store,
  TrafficCone,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { POPUP, POPUP_LINK } from './mapColors';

/**
 * Layer icon: a lucide component for BUILT-IN layers (rendered in the
 * panel as a normal token-coloured icon, and inlined as SVG inside map
 * markers/cluster bubbles) — or the user-picked emoji STRING for
 * custom layers (stored in the DB; user content is exempt from the
 * lucide-only rule).  Never author a new built-in with an emoji.
 */
export type PoiIconSpec = LucideIcon | string;

/** One GeoJSON point as served by GET /map/pois — the unit every layer
 *  renders.  Lives here (not in the hook) so layer defs can type their
 *  popup builders without importing the engine. */
export interface PoiFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: Record<string, unknown>;
}

export interface PoiBrandFilter {
  /**
   * Unique key for this chip — stored in activeFilters, used as React key.
   * NOT used directly for text matching; see matchTerms.
   */
  value: string;
  /** Short display label in the UI chip. */
  label: string;
  /** Legacy field — chips render text-only now.  Kept because custom
   *  layers' brand_filters DTO (DB rows) may still carry it. */
  icon?: string;
  /**
   * One or more OSM brand/name strings to match against (case-insensitive,
   * word-boundary aware).  If omitted, falls back to matching against `value`.
   *
   * Use this when a single chip covers multiple OSM brand values, e.g.
   * Pilot Flying J locations appear under brand=Pilot, brand=Flying J,
   * OR brand=Pilot Flying J depending on how/when they were tagged in OSM.
   */
  matchTerms?: string[];
}

export interface PoiLayerDef {
  /** Unique key — used as the API `type` param and as cache key. */
  id: string;
  /** Human-readable label shown in the toggle panel. */
  label: string;
  /** Hex colour for markers on the map. */
  color: string;
  /** Panel + marker icon — see PoiIconSpec (lucide for built-ins,
   *  DB-stored emoji string for custom layers). */
  icon: PoiIconSpec;
  /** Whether this layer is switched ON when the map first loads. */
  defaultOn: boolean;
  /**
   * Optional brand/chain sub-filters shown below this layer's toggle when enabled.
   * When any filter is active only markers matching that brand are shown
   * (client-side, no new fetch).  When none are selected all markers are shown.
   */
  brandFilters?: PoiBrandFilter[];
  /**
   * Optional group key — links this layer to a POI_GROUPS entry.  Layers in the
   * same group are rendered together under a header.  Omit for ungrouped layers.
   */
  group?: string;
  /**
   * Optional popup-HTML builder for this layer's markers.  When omitted the
   * engine renders the default OSM-tag popup (name/brand + amenity badges).
   * Layers whose feature properties are NOT OSM tags (DB-backed layers like
   * the vendor directory) supply their own builder here — never add
   * layer-specific branches inside usePoiLayers.
   */
  popup?: (feature: PoiFeature, def: PoiLayerDef) => string;
}

export interface PoiGroupDef {
  /** Unique key referenced by PoiLayerDef.group. */
  id: string;
  /** Header label shown in the panel. */
  label: string;
  /** Optional header icon (lucide for our groups). */
  icon?: PoiIconSpec;
}

/**
 * Display-only group headers.  Order here = render order in the panel.
 * Groups are NOT toggleable — they're just visual sorting/sectioning.
 */
export const POI_GROUPS: PoiGroupDef[] = [
  { id: 'fuel_plaza',     label: 'Fuel Stops & Plazas', icon: Fuel },
  { id: 'highway_safety', label: 'Highway & Safety',    icon: TrafficCone },
  { id: 'services',       label: 'Services',            icon: Wrench },
  { id: 'custom',         label: 'My Layers',           icon: FolderOpen },
];

// ── Vendor-directory popup ───────────────────────────────────────────────────

/** Escape untrusted text for the popup HTML string. */
function esc(v: unknown): string {
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Identity-only popup for the platform vendor directory (Phase C2 of
 * capabilities/platform/docs/vendor-parts-master-data.md): name, services,
 * address, phone, website.  Deliberately NO ratings or usage counts —
 * the map is visible to every role with can_location_map, while review
 * data stays behind the manager-gated vendor endpoints.  Widening that
 * is a product decision, not a popup tweak.
 */
export function vendorDirectoryPopup(f: PoiFeature): string {
  const p = f.properties;
  const services = String(p.services ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const badges = services.length
    ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:3px">${
        services.map((s) => `<span style="background:${POPUP.badgeBg};color:${POPUP.badgeText};font-size:10px;padding:2px 6px;border-radius:10px;white-space:nowrap">${esc(s)}</span>`).join('')
      }</div>`
    : '';
  const meta: string[] = [];
  if (p.address) meta.push(esc(p.address));
  if (p.phone)   meta.push(esc(p.phone));
  const metaHtml = meta.length
    ? `<div style="color:${POPUP.muted};font-size:10px;margin-top:4px">${meta.join(' &nbsp;·&nbsp; ')}</div>`
    : '';
  const site = String(p.website ?? '');
  const siteHtml = /^https?:\/\//i.test(site)
    ? `<div style="margin-top:4px"><a href="${esc(site)}" target="_blank" rel="noreferrer" style="color:${POPUP_LINK};font-size:10px">${esc(site.replace(/^https?:\/\//i, ''))}</a></div>`
    : '';
  const chain = String(p.chain ?? '').trim();
  // Present only on the my_vendors layer: the caller's own vendor name.
  const mine = String(p.my_vendor_name ?? '').trim();
  const mineHtml = mine
    ? `<div style="margin-top:4px;font-size:10px;color:${POPUP.badgeText}"><span style="background:${POPUP.badgeBg};padding:2px 6px;border-radius:10px">Your vendor · ${esc(mine)}</span></div>`
    : '';
  return `<div style="min-width:160px;max-width:240px">`
    + `<div style="font-weight:600;font-size:13px">${esc(p.name)}</div>`
    + `<div style="color:${POPUP.muted};font-size:11px">${chain ? `${esc(chain)} · ` : ''}Repair shop · 4truck directory</div>`
    + mineHtml
    + badges
    + metaHtml
    + siteHtml
    + `</div>`;
}

/**
 * Master list of available POI overlay layers.
 * Order within a group = render order under that group's header.
 */
export const POI_LAYERS: PoiLayerDef[] = [
  // ═══════════════════════════════════════════════════════════════════════════
  // GROUP: Fuel Stops & Plazas
  // ═══════════════════════════════════════════════════════════════════════════
  {
    // ── Fuel Stations ──────────────────────────────────────────────────────
    // Comprehensive layer covering every diesel-capable fuel point — from a
    // rural Chevron to a full-service Pilot/Flying J Plaza.  Brand chips let
    // drivers narrow to a specific chain.
    id: 'fuel_station',
    label: 'Fuel Stations',
    color: '#f59e0b',
    icon: Fuel,
    defaultOn: false,
    group: 'fuel_plaza',
    brandFilters: [
      // ── Full-service truck plazas ──────────────────────────────────────
      //
      // OSM brand audit (April 2026):
      //   Pilot Flying J locations use brand=Pilot Flying J (combined),
      //   brand=Pilot (older Pilot-only), OR brand=Flying J (older FJ-only).
      //   One chip — ONE button — matches all three.
      {
        value:      'pilot_flyingj',
        label:      'Pilot / Flying J',
        matchTerms: ['Pilot Flying J', 'Pilot Travel Center', 'Pilot Travel Centre', 'Pilot', 'Flying J'],
      },
      //
      //   Love's Travel Stops: brand=Love's  (apostrophe varies — cover both)
      {
        value:      'loves',
        label:      "Love's",
        matchTerms: ["Love's", 'Loves', "Love's Travel Stop", 'Loves Travel Stop'],
      },
      //
      //   TravelCenters of America: brand=TA (short form used on most OSM nodes)
      //   Petro Stopping Centers: brand=Petro — same parent company (TA Operating LLC).
      //   One chip — ONE button — matches both brands.
      //   NOTE: 'TA' uses word-boundary matching so it doesn't hit "station", etc.
      {
        value:      'ta_petro',
        label:      'TA / Petro',
        matchTerms: ['TA', 'Petro', 'TravelCenters of America', 'Petro Stopping Centers', 'TA Travel Center'],
      },
      //
      //   Sapp Bros Truck Stops: brand=Sapp Bros or brand=Sapp Bros.
      {
        value:      'sapp_bros',
        label:      'Sapp Bros',
        matchTerms: ['Sapp Bros', 'Sapp Bros.'],
      },
      { value: 'Bosselman',    label: 'Bosselman' },
      { value: 'Ambest',       label: 'Ambest' },
      {
        value:      'road_ranger',
        label:      'Road Ranger',
        matchTerms: ['Road Ranger'],
      },
      //   Kwik Trip (WI/MN/IA) also operates as Kwik Star in Iowa — same company.
      {
        value:      'kwik_trip',
        label:      'Kwik Trip',
        matchTerms: ['Kwik Trip', 'Kwik Star'],
      },

      // ── Independent fuel stations ──────────────────────────────────────
      { value: 'Shell',   label: 'Shell' },
      // 'BP' is word-boundary matched to avoid hitting e.g. "Sapp Bros" (no 'bp')
      { value: 'BP',      label: 'BP' },
      //   Exxon and Mobil are distinct OSM brands (both ExxonMobil Corp).
      //   Keep as separate chips — OSM data rarely uses "ExxonMobil" on-screen.
      {
        value:      'Exxon',
        label:      'Exxon',
        matchTerms: ['ExxonMobil', 'Exxon', 'Esso'],
      },
      { value: 'Mobil',    label: 'Mobil' },
      { value: 'Chevron',  label: 'Chevron' },
      { value: 'Valero',   label: 'Valero' },
      { value: 'Speedway', label: 'Speedway' },
      { value: 'Maverick', label: 'Maverick' },
    ],
  },
  {
    // DEF = Diesel Exhaust Fluid (AdBlue) — SCR trucks derate without it.
    // Subset of fuel stations where fuel:adblue=yes is explicitly tagged
    // PLUS major-chain truck plazas whose every location sells DEF
    // (Pilot/FJ, Love's, TA/Petro, etc.).  Brand chips let drivers narrow
    // to a specific chain — same client-side filter as Fuel Stations.
    id: 'def_station',
    label: 'DEF / AdBlue Stations',
    color: '#0d9488',
    icon: FlaskConical,
    defaultOn: false,
    group: 'fuel_plaza',
    brandFilters: [
      {
        value:      'pilot_flyingj',
        label:      'Pilot / Flying J',
        matchTerms: ['Pilot Flying J', 'Pilot Travel Center', 'Pilot Travel Centre', 'Pilot', 'Flying J'],
      },
      {
        value:      'loves',
        label:      "Love's",
        matchTerms: ["Love's", 'Loves', "Love's Travel Stop", 'Loves Travel Stop'],
      },
      {
        value:      'ta_petro',
        label:      'TA / Petro',
        matchTerms: ['TA', 'Petro', 'TravelCenters of America', 'Petro Stopping Centers', 'TA Travel Center'],
      },
      {
        value:      'sapp_bros',
        label:      'Sapp Bros',
        matchTerms: ['Sapp Bros', 'Sapp Bros.'],
      },
      {
        value:      'road_ranger',
        label:      'Road Ranger',
        matchTerms: ['Road Ranger'],
      },
    ],
  },
  {
    // HOS Hours-of-Service compliance — drivers need parking before clock runs out.
    id: 'truck_parking',
    label: 'Truck Parking',
    color: '#3b82f6',
    icon: SquareParking,
    defaultOn: false,
    group: 'fuel_plaza',
  },
  {
    id: 'shower',
    label: 'Showers',
    color: '#ec4899',
    icon: ShowerHead,
    defaultOn: false,
    group: 'fuel_plaza',
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // GROUP: Highway & Safety
  // ═══════════════════════════════════════════════════════════════════════════
  {
    id: 'weigh_station',
    label: 'Weigh Stations',
    color: '#8b5cf6',
    icon: Scale,
    defaultOn: false,
    group: 'highway_safety',
  },
  {
    id: 'rest_area',
    label: 'Rest Areas',
    color: '#06b6d4',
    icon: BedDouble,
    defaultOn: false,
    group: 'highway_safety',
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // GROUP: Services
  // ═══════════════════════════════════════════════════════════════════════════
  {
    // Platform-curated repair-shop directory (vendor-parts master data,
    // Phase C2).  DB-backed: served by the vendor_directory branch in
    // features/location/pois.py, NOT Overpass.  Only operator-geocoded
    // ACTIVE entries appear; properties are identity-only.
    id: 'vendor_directory',
    label: 'Repair Shops',
    color: '#16a34a',
    icon: Wrench,
    defaultOn: false,
    group: 'services',
    popup: vendorDirectoryPopup,
  },
  {
    // THIS account's shops: directory entries auto-linked from the
    // account's own vendors (service recorded → identity approved →
    // linked, no user ceremony).  Identity + own-vendor name only —
    // never spend.  Blue to stand apart from the green public layer.
    id: 'my_vendors',
    label: 'My Vendors',
    color: '#2563eb',
    icon: Store,
    defaultOn: false,
    group: 'services',
    popup: vendorDirectoryPopup,
  },
  // ─── Add new layers above this line ───────────────────────────────────────
];

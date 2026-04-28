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
 *   2. Add a matching key to POI_OVERPASS_QUERIES in interfaces/api/routes/maps.py.
 *   The hook and the panel pick it up automatically.
 *
 * To REMOVE a layer:
 *   1. Delete the entry from POI_LAYERS (and the backend query if no longer used).
 */

export interface PoiBrandFilter {
  /**
   * Unique key for this chip — stored in activeFilters, used as React key.
   * NOT used directly for text matching; see matchTerms.
   */
  value: string;
  /** Short display label in the UI chip. */
  label: string;
  /** Optional emoji/icon for the chip. */
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
  /** Small icon character rendered inside the marker dot. */
  icon: string;
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
}

export interface PoiGroupDef {
  /** Unique key referenced by PoiLayerDef.group. */
  id: string;
  /** Header label shown in the panel. */
  label: string;
  /** Optional emoji for the header. */
  icon?: string;
}

/**
 * Display-only group headers.  Order here = render order in the panel.
 * Groups are NOT toggleable — they're just visual sorting/sectioning.
 */
export const POI_GROUPS: PoiGroupDef[] = [
  { id: 'fuel_plaza',     label: 'Fuel Stops & Plazas', icon: '⛽' },
  { id: 'highway_safety', label: 'Highway & Safety',    icon: '🛣️' },
];

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
    icon: '⛽',
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
        icon:       '✈️',
        matchTerms: ['Pilot Flying J', 'Pilot Travel Center', 'Pilot Travel Centre', 'Pilot', 'Flying J'],
      },
      //
      //   Love's Travel Stops: brand=Love's  (apostrophe varies — cover both)
      {
        value:      'loves',
        label:      "Love's",
        icon:       '❤️',
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
        icon:       '🔵',
        matchTerms: ['TA', 'Petro', 'TravelCenters of America', 'Petro Stopping Centers', 'TA Travel Center'],
      },
      //
      //   Sapp Bros Truck Stops: brand=Sapp Bros or brand=Sapp Bros.
      {
        value:      'sapp_bros',
        label:      'Sapp Bros',
        icon:       '🟢',
        matchTerms: ['Sapp Bros', 'Sapp Bros.'],
      },
      { value: 'Bosselman',    label: 'Bosselman',        icon: '🟡' },
      { value: 'Ambest',       label: 'Ambest',           icon: '🟡' },
      {
        value:      'road_ranger',
        label:      'Road Ranger',
        icon:       '🛣️',
        matchTerms: ['Road Ranger'],
      },
      //   Kwik Trip (WI/MN/IA) also operates as Kwik Star in Iowa — same company.
      {
        value:      'kwik_trip',
        label:      'Kwik Trip',
        icon:       '🟠',
        matchTerms: ['Kwik Trip', 'Kwik Star'],
      },

      // ── Independent fuel stations ──────────────────────────────────────
      { value: 'Shell',   label: 'Shell',   icon: '🐚' },
      // 'BP' is word-boundary matched to avoid hitting e.g. "Sapp Bros" (no 'bp')
      { value: 'BP',      label: 'BP',      icon: '🟢' },
      //   Exxon and Mobil are distinct OSM brands (both ExxonMobil Corp).
      //   Keep as separate chips — OSM data rarely uses "ExxonMobil" on-screen.
      {
        value:      'Exxon',
        label:      'Exxon',
        icon:       '🔴',
        matchTerms: ['ExxonMobil', 'Exxon', 'Esso'],
      },
      { value: 'Mobil',    label: 'Mobil',    icon: '🔴' },
      { value: 'Chevron',  label: 'Chevron',  icon: '🔵' },
      { value: 'Valero',   label: 'Valero',   icon: '⭐' },
      { value: 'Speedway', label: 'Speedway', icon: '🏎️' },
      { value: 'Maverick', label: 'Maverick', icon: '🤠' },
    ],
  },
  {
    // DEF = Diesel Exhaust Fluid (AdBlue) — SCR trucks derate without it.
    // Subset of fuel stations where fuel:adblue=yes is explicitly tagged.
    id: 'def_station',
    label: 'DEF / AdBlue Stations',
    color: '#0d9488',
    icon: '🧪',
    defaultOn: false,
    group: 'fuel_plaza',
  },
  {
    // HOS Hours-of-Service compliance — drivers need parking before clock runs out.
    id: 'truck_parking',
    label: 'Truck Parking',
    color: '#3b82f6',
    icon: '🅿',
    defaultOn: false,
    group: 'fuel_plaza',
  },
  {
    id: 'shower',
    label: 'Showers',
    color: '#ec4899',
    icon: '🚿',
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
    icon: '⚖️',
    defaultOn: false,
    group: 'highway_safety',
  },
  {
    id: 'rest_area',
    label: 'Rest Areas',
    color: '#06b6d4',
    icon: '🛏️',
    defaultOn: false,
    group: 'highway_safety',
  },
  // ─── Add new layers above this line ───────────────────────────────────────
];

/**
 * What the panel can draw on top of the map, and what each one looks
 * like.  The dashboard's `config/poiLayers.ts` is the original and the
 * IDs here must match it exactly: an id is the `type` parameter of
 * GET /map/pois, so a typo is not a styling bug, it is a 422.
 *
 * The colours match too, deliberately.  A blue dot means truck parking
 * on the dashboard; it has to mean truck parking here, or the two
 * screens describe the same road in two languages.
 *
 * What is NOT here: the brand list is trimmed to the chains a driver
 * actually asks for by name.  The dashboard shows seventeen fuel
 * brands in a panel that can afford them; this column is 320px, and a
 * chip row that wraps to five lines hides the layer list underneath it.
 * Every layer still returns every brand — the chips narrow, they never
 * decide what was fetched.
 */

/** Lucide paths, inlined: the panel bundles no icon library, and a
 *  marker's glyph has to be a string anyway — Leaflet takes HTML, not
 *  components.  Each is the `d`/elements of the 24×24 lucide original. */
const GLYPH: Record<string, string> = {
  fuel:
    '<line x1="3" x2="15" y1="22" y2="22"/><line x1="4" x2="14" y1="9" y2="9"/>'
    + '<path d="M14 22V4a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v18"/>'
    + '<path d="M14 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2 2 2 0 0 0 2-2V9.83a2 2 0 0 0-.59-1.42L18 5"/>',
  flask:
    '<path d="M10 2v7.31"/><path d="M14 9.3V1.99"/><path d="M8.5 2h7"/>'
    + '<path d="M14 9.3a6.5 6.5 0 1 1-4 0"/><path d="M5.52 16h12.96"/>',
  parking:
    '<rect width="18" height="18" x="3" y="3" rx="2"/>'
    + '<path d="M9 17V7h4a3 3 0 0 1 0 6H9"/>',
  shower:
    '<path d="m4 4 2.5 2.5"/><path d="M13.5 6.5a4.95 4.95 0 0 0-7 7"/><path d="M15 5 5 15"/>'
    + '<path d="M14 17v.01"/><path d="M10 16v.01"/><path d="M13 13v.01"/><path d="M16 10v.01"/>'
    + '<path d="M11 20v.01"/><path d="M17 14v.01"/><path d="M20 11v.01"/>',
  scale:
    '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/>'
    + '<path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/>'
    + '<path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
  bed:
    '<path d="M2 20v-8a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v8"/>'
    + '<path d="M4 10V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v4"/><path d="M12 4v6"/><path d="M2 18h20"/>',
  wrench:
    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94'
    + 'l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  store:
    '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/>'
    + '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M2 7h20"/>'
    + '<path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/>',
  folder:
    '<path d="m6 14 1.45-2.9A2 2 0 0 1 9.24 10H22l-2.55 5.1A2 2 0 0 1 17.66 16H4"/>'
    + '<path d="M2 6a2 2 0 0 1 2-2h3.93a2 2 0 0 1 1.66.9l.82 1.2a2 2 0 0 0 1.66.9H18a2 2 0 0 1 2 2v1"/>',
};

/** One glyph as an SVG string, at the size the caller has room for. */
export function glyphSvg(key: string, sizePx: number, colour = '#fff'): string {
  const body = GLYPH[key];
  if (!body) return '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${sizePx}" height="${sizePx}" `
    + `viewBox="0 0 24 24" fill="none" stroke="${colour}" stroke-width="2.5" `
    + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

export interface PoiBrand {
  /** Stored in the selection and used as the React key — never matched on. */
  value: string;
  label: string;
  /** The OSM spellings this one chip stands for.  Absent means "match
   *  the value itself", which is right for single-spelling brands. */
  matchTerms?: string[];
}

export interface PoiLayerDef {
  /** The API's `type`, and the cache key.  Must equal the dashboard's. */
  id: string;
  label: string;
  /** Marker colour — the same hex the dashboard draws this layer in. */
  color: string;
  glyph: keyof typeof GLYPH | string;
  group: string;
  brands?: PoiBrand[];
  /** Layers whose properties are NOT OSM tags bring their own popup. */
  popup?: (f: PoiFeatureLike, def: PoiLayerDef) => string;
}

/** Only what the popups read — keeps this file free of the fetch layer. */
export interface PoiFeatureLike { properties: Record<string, unknown> | null }

export const POI_GROUPS: { id: string; label: string; glyph: string }[] = [
  { id: 'fuel_plaza',     label: 'Fuel & plazas', glyph: 'fuel' },
  { id: 'highway_safety', label: 'Highway',       glyph: 'scale' },
  { id: 'services',       label: 'Services',      glyph: 'wrench' },
  { id: 'custom',         label: 'My layers',     glyph: 'folder' },
];

// ── popups ───────────────────────────────────────────────────────────────

/**
 * Escape before anything reaches innerHTML.
 *
 * These strings are OSM tag values.  Anybody in the world can edit
 * OpenStreetMap, so a fuel station's `name` is untrusted input that
 * arrives through our own API and lands in a popup — inside the
 * EXTENSION's origin, which holds the panel's token.  There is no
 * version of that worth risking for four fewer lines.
 */
export function esc(v: unknown): string {
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const POPUP_MUTED = '#8b92a5';
const POPUP_BADGE_BG = 'rgba(255,255,255,.08)';

function badges(items: string[]): string {
  if (!items.length) return '';
  return `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:3px">${
    items.map((a) => `<span style="background:${POPUP_BADGE_BG};font-size:10px;`
      + `padding:2px 6px;border-radius:10px;white-space:nowrap">${a}</span>`).join('')
  }</div>`;
}

/** The default popup: OSM tags read as a place. */
export function osmPopup(f: PoiFeatureLike, def: PoiLayerDef): string {
  const p = (f.properties ?? {}) as Record<string, string>;
  const name = p.name || def.label;
  const subtitle = (p.brand && p.brand !== name) ? p.brand
    : (p.operator && p.operator !== name) ? p.operator
    : def.label;

  const amenities: string[] = [];
  if (p['fuel:diesel'] === 'yes') amenities.push('⛽ Diesel');
  if (p['fuel:adblue'] === 'yes') amenities.push('🧪 DEF');
  if (p.shower === 'yes')         amenities.push('🚿 Showers');
  if (p.toilets === 'yes')        amenities.push('🚻 Restrooms');
  if (p.capacity)                 amenities.push(`🅿 ${esc(p.capacity)} spots`);
  if (p.fee === 'no')             amenities.push('🆓 Free');
  if (p.fee === 'yes')            amenities.push('💰 Fee');

  const meta: string[] = [];
  if (p.opening_hours) meta.push(`🕐 ${esc(p.opening_hours)}`);
  if (p.phone)         meta.push(`📞 ${esc(p.phone)}`);

  return `<div style="min-width:150px;max-width:220px">`
    + `<div style="font-weight:600;font-size:13px">${esc(name)}</div>`
    + `<div style="color:${POPUP_MUTED};font-size:11px">${esc(subtitle)}</div>`
    + badges(amenities)
    + (meta.length
        ? `<div style="color:${POPUP_MUTED};font-size:10px;margin-top:4px">${meta.join(' &nbsp;·&nbsp; ')}</div>`
        : '')
    + `</div>`;
}

/**
 * The repair-shop directory's popup: identity only.
 *
 * Deliberately NO ratings and no spend — the map rides
 * `can_view_location`, which many more people hold than the vendor
 * endpoints behind which that data lives.  Widening this is a product
 * decision, not a popup tweak.
 */
export function vendorPopup(f: PoiFeatureLike): string {
  const p = (f.properties ?? {}) as Record<string, string>;
  const services = String(p.services ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const meta: string[] = [];
  if (p.address) meta.push(esc(p.address));
  if (p.phone)   meta.push(esc(p.phone));
  const site = String(p.website ?? '');
  const mine = String(p.my_vendor_name ?? '').trim();
  const chain = String(p.chain ?? '').trim();
  return `<div style="min-width:150px;max-width:220px">`
    + `<div style="font-weight:600;font-size:13px">${esc(p.name)}</div>`
    + `<div style="color:${POPUP_MUTED};font-size:11px">`
    + `${chain ? `${esc(chain)} · ` : ''}Repair shop · 4truck directory</div>`
    + (mine ? badges([`Your vendor · ${esc(mine)}`]) : '')
    + badges(services.map(esc))
    + (meta.length
        ? `<div style="color:${POPUP_MUTED};font-size:10px;margin-top:4px">${meta.join(' &nbsp;·&nbsp; ')}</div>`
        : '')
    // rel="noreferrer" AND a fresh context: a popup link is the one
    // place a stranger's OSM edit gets to choose a URL.
    + (/^https?:\/\//i.test(site)
        ? `<div style="margin-top:4px"><a href="${esc(site)}" target="_blank" rel="noreferrer noopener" `
          + `style="color:#3b82f6;font-size:10px">${esc(site.replace(/^https?:\/\//i, ''))}</a></div>`
        : '')
    + `</div>`;
}

// ── the layers ───────────────────────────────────────────────────────────

/** The chains whose names a driver says out loud.  Shared by the two
 *  fuel layers, because a Pilot is a Pilot on both. */
const TRUCK_STOP_BRANDS: PoiBrand[] = [
  { value: 'pilot_flyingj', label: 'Pilot / FJ',
    matchTerms: ['Pilot Flying J', 'Pilot Travel Center', 'Pilot Travel Centre', 'Pilot', 'Flying J'] },
  { value: 'loves', label: "Love's",
    matchTerms: ["Love's", 'Loves', "Love's Travel Stop", 'Loves Travel Stop'] },
  { value: 'ta_petro', label: 'TA / Petro',
    matchTerms: ['TA', 'Petro', 'TravelCenters of America', 'Petro Stopping Centers', 'TA Travel Center'] },
  { value: 'sapp_bros', label: 'Sapp Bros', matchTerms: ['Sapp Bros', 'Sapp Bros.'] },
  { value: 'road_ranger', label: 'Road Ranger', matchTerms: ['Road Ranger'] },
];

export const POI_LAYERS: PoiLayerDef[] = [
  {
    id: 'fuel_station', label: 'Fuel stations', color: '#f59e0b',
    glyph: 'fuel', group: 'fuel_plaza',
    brands: [
      ...TRUCK_STOP_BRANDS,
      { value: 'kwik_trip', label: 'Kwik Trip', matchTerms: ['Kwik Trip', 'Kwik Star'] },
      { value: 'Shell', label: 'Shell' },
      { value: 'BP', label: 'BP' },
      { value: 'Exxon', label: 'Exxon', matchTerms: ['ExxonMobil', 'Exxon', 'Esso'] },
      { value: 'Mobil', label: 'Mobil' },
      { value: 'Chevron', label: 'Chevron' },
      { value: 'Valero', label: 'Valero' },
    ],
  },
  {
    // DEF = Diesel Exhaust Fluid.  An SCR truck derates without it, so
    // this is a layer somebody opens in a hurry.
    id: 'def_station', label: 'DEF / AdBlue', color: '#0d9488',
    glyph: 'flask', group: 'fuel_plaza', brands: TRUCK_STOP_BRANDS,
  },
  { id: 'truck_parking', label: 'Truck parking', color: '#3b82f6',
    glyph: 'parking', group: 'fuel_plaza' },
  { id: 'shower', label: 'Showers', color: '#ec4899',
    glyph: 'shower', group: 'fuel_plaza' },

  { id: 'weigh_station', label: 'Weigh stations', color: '#8b5cf6',
    glyph: 'scale', group: 'highway_safety' },
  { id: 'rest_area', label: 'Rest areas', color: '#06b6d4',
    glyph: 'bed', group: 'highway_safety' },

  { id: 'vendor_directory', label: 'Repair shops', color: '#16a34a',
    glyph: 'wrench', group: 'services', popup: vendorPopup },
  { id: 'my_vendors', label: 'My vendors', color: '#2563eb',
    glyph: 'store', group: 'services', popup: vendorPopup },
];

/** A server-side custom layer, as GET /map/custom-layers describes it. */
export interface CustomLayerDto {
  id: number;
  name: string;
  color?: string | null;
  icon?: string | null;
}

/** A custom layer, wearing the same shape as a built-in.  The `custom_`
 *  prefix is what the server's /pois branch dispatches on — it is part
 *  of the id, not decoration. */
export function customLayerDef(dto: CustomLayerDto): PoiLayerDef {
  return {
    id: `custom_${dto.id}`,
    label: dto.name,
    color: dto.color || '#64748b',
    // User-picked emoji, stored in the DB: the one place a layer's mark
    // is not one of ours.  `glyphSvg` returns '' for it and the marker
    // falls back to drawing the character itself.
    glyph: dto.icon || '',
    group: 'custom',
  };
}

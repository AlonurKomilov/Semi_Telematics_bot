/**
 * CustomLayerEditor — admin modal for creating / editing per-tenant POI layers.
 *
 * Three creation modes (tabs):
 *   📍 Pin-drop  — paste lat/lng of a known branded location; the server reads
 *                  the OSM brand tag at that point and expands to ALL matching
 *                  brand locations across the dataset (e.g. click on a Pilot →
 *                  layer shows every Pilot/Flying J).
 *   🔎 Overpass  — power-user mode: write a single Overpass selector clause
 *                  (e.g. `node["amenity"="car_wash"]["hgv"="yes"]`).  Validated
 *                  server-side against an allowlist.
 *   📄 CSV       — upload a list of fixed points (lat,lng[,name,...]) up to
 *                  50 000 rows.  Read locally with FileReader and posted as
 *                  JSON (no multipart dependency).
 *
 * Edit mode reuses the same modal but only allows label / color / icon /
 * default_on changes — source_type and underlying data are immutable.
 */

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, ArrowRight, FileText, MapPin, Search, type LucideIcon } from 'lucide-react';
import type L from 'leaflet';
import { apiFetch, apiJSON } from '@/api/client';
import type { PoiLayerDef } from '@/config/poiLayers';
import { CUSTOM_LAYER_SWATCHES } from '@/config/mapColors';

// The marker-icon picker below is USER content — the operator picks an
// emoji that is stored on the layer row and shown on their map.  This is
// the one sanctioned non-lucide icon surface (see PoiIconSpec); the
// dialog's own chrome (tabs, buttons) uses lucide like everything else.
/** Curated emoji list shown in the icon picker — kept short on purpose. */
const ICON_CHOICES = ['📍', '⛽', '🅿️', '🛒', '🍔', '🔧', '🛏️', '🚿', '🏪', '🚛', '⚖️', '🛣️', '🗂️'];
/** Curated swatch palette covering the primary POI brand families. */
const COLOR_CHOICES = CUSTOM_LAYER_SWATCHES;

type Tab = 'pin' | 'overpass' | 'csv';

interface CustomLayerEditorProps {
  mode: 'create' | 'edit';
  /** DB id of the layer being edited — required when mode === 'edit'. */
  layerId?: number;
  /** Current set of custom layers (used in edit mode to prefill the form). */
  existingLayers: PoiLayerDef[];
  /** Optional Leaflet map ref.  When supplied, the pin-drop tab shows a
   *  “Pick on map” button that hides the modal, swaps the cursor to a
   *  crosshair and captures the next map click into the lat/lng fields.
   *  Falls back to manual entry when undefined. */
  leafletMap?: React.RefObject<L.Map | null>;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}

export default function CustomLayerEditor(props: CustomLayerEditorProps) {
  const { mode, layerId, existingLayers, leafletMap, onClose, onSaved } = props;

  // ── Common form fields shared by every tab ─────────────────────────────────
  const [label, setLabel]         = useState('');
  const [color, setColor]         = useState(COLOR_CHOICES[0]);
  const [icon, setIcon]           = useState(ICON_CHOICES[0]);
  const [defaultOn, setDefaultOn] = useState(false);

  // ── Tab-specific fields ───────────────────────────────────────────────────
  const [tab, setTab]                 = useState<Tab>('pin');
  const [pinLat, setPinLat]           = useState('');
  const [pinLng, setPinLng]           = useState('');
  const [overpassQuery, setOverpassQuery] = useState('');
  const [csvText, setCsvText]         = useState('');
  const [csvFileName, setCsvFileName] = useState('');

  // ── Discovered-brand preview shown after Pin-drop / Brand-search ─────────
  // The new "preview-then-confirm" flow runs in two steps:
  //   1. preview-pin  → returns { brand, amenity, count, sample[] }
  //   2. from-brand   → persists the layer covering EVERY US match
  // The preview lives in state so the same render path is reused by both
  // the Pin-drop and Brand-search tabs.  Each tab owns its own preview ref
  // because the user might switch tabs and we shouldn't carry state across.
  interface BrandPreview {
    brand: string;
    amenity: string;
    count: number;
    sample: { lat: number; lng: number; name: string; address?: string | null }[];
  }
  const [pinPreview, setPinPreview]       = useState<BrandPreview | null>(null);
  const [pinPreviewing, setPinPreviewing] = useState(false);

  // ── Brand-search (Overpass tab) state ─────────────────────────────────────
  const [brandQuery, setBrandQuery]   = useState('');
  const [brandResults, setBrandResults] =
    useState<{ brand: string; amenity: string; count: number }[]>([]);
  const [brandLoading, setBrandLoading] = useState(false);
  const [brandSelected, setBrandSelected] =
    useState<{ brand: string; amenity: string; count: number } | null>(null);
  /** Power-user escape hatch: write a raw Overpass clause instead of picking
   *  a brand.  Hidden behind a collapsible because the typical admin should
   *  never need it. */
  const [showAdvancedOverpass, setShowAdvancedOverpass] = useState(false);

  // ── Submission state ──────────────────────────────────────────────────────
  const [busy, setBusy]   = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo]   = useState<string | null>(null);

  // ── Pin-drop “pick on map” state ───────────────────────────────────────
  // When the admin clicks “Pick on map” we hide this modal (without
  // unmounting it — we want to preserve all in-progress form state), put
  // the map in a special listening mode, and capture the first click as
  // lat/lng.  Esc cancels.  We hide rather than unmount so reopening keeps
  // every other field the user already filled in.
  const [picking, setPicking] = useState(false);
  useEffect(() => {
    if (!picking) return;
    const map = leafletMap?.current;
    if (!map) { setPicking(false); return; }

    // Visual hint: crosshair cursor on the map container.
    const container = map.getContainer();
    const prevCursor = container.style.cursor;
    container.style.cursor = 'crosshair';

    const onClick = (e: L.LeafletMouseEvent) => {
      setPinLat(e.latlng.lat.toFixed(6));
      setPinLng(e.latlng.lng.toFixed(6));
      setPicking(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPicking(false);
    };
    map.on('click', onClick);
    window.addEventListener('keydown', onKey);
    return () => {
      map.off('click', onClick);
      window.removeEventListener('keydown', onKey);
      container.style.cursor = prevCursor;
    };
  }, [picking, leafletMap]);

  // Prefill the form when editing an existing layer.
  useEffect(() => {
    if (mode !== 'edit' || layerId == null) return;
    const existing = existingLayers.find((l) => l.id === `custom_${layerId}`);
    if (!existing) return;
    setLabel(existing.label);
    setColor(existing.color);
    // Custom layers always store an emoji string; the union type on
    // PoiLayerDef.icon exists for built-ins (lucide components).
    setIcon(typeof existing.icon === 'string' ? existing.icon : ICON_CHOICES[0]);
    setDefaultOn(existing.defaultOn);
  }, [mode, layerId, existingLayers]);

  /** Step 1 of the new pin-drop flow: detect the brand at lat/lng and load
   *  a US-wide preview.  Doesn't write anything to the DB. */
  async function runPinPreview() {
    const lat = Number(pinLat), lng = Number(pinLng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      setError('Latitude and longitude must be numeric.');
      return;
    }
    setPinPreviewing(true); setError(null); setInfo(null); setPinPreview(null);
    try {
      const data = await apiJSON<BrandPreview>(
        '/map/custom-layers/preview-pin',
        { method: 'POST', body: { lat, lng } },
      );
      setPinPreview(data);
      // Auto-fill the label with the discovered brand if the admin hasn't
      // typed one yet.  They can still edit it before pressing Save.
      if (!label.trim()) setLabel(`${data.brand} (all locations)`);
    } catch (e) {
      const msg = (e as Error).message || '';
      if (msg.includes('404') || msg.toLowerCase().includes('no branded')) {
        setError('No branded POI found within 50 m. For a single-point save, use Geofences instead.');
      } else {
        setError(`Preview failed: ${msg}`);
      }
    } finally {
      setPinPreviewing(false);
    }
  }

  /** Step 2 of the pin-drop flow / single step of the brand-search flow:
   *  persist a layer for an already-discovered brand. */
  async function submitFromBrand(brand: string, amenity: string) {
    if (!brand) { setError('Pick a brand first.'); return; }
    setBusy(true); setError(null); setInfo(null);
    try {
      await apiJSON('/map/custom-layers/from-brand', {
        method: 'POST',
        body: {
          brand,
          amenity: amenity || undefined,
          label: label.trim() || undefined,
          color, icon,
          default_on: defaultOn,
        },
      });
      await onSaved();
    } catch (e) {
      setError(`Save failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  // Debounced brand-search.  Re-fires whenever the query text settles for
  // 300 ms; the network call is intentionally slow (Overpass aggregation
  // takes ~1-2 s) so a pure on-keystroke fetch would hammer Overpass.
  useEffect(() => {
    if (mode !== 'create' || tab !== 'overpass') return;
    if (showAdvancedOverpass) return;
    const q = brandQuery.trim();
    if (q.length < 2) { setBrandResults([]); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      setBrandLoading(true);
      try {
        const data = await apiJSON<{ results: typeof brandResults }>(
          `/map/custom-layers/brand-search?q=${encodeURIComponent(q)}`,
        );
        if (!cancelled) setBrandResults(data.results || []);
      } catch {
        if (!cancelled) setBrandResults([]);
      } finally {
        if (!cancelled) setBrandLoading(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [brandQuery, mode, tab, showAdvancedOverpass]);

  /** Read a File as UTF-8 text via FileReader (avoids fetch() complications). */
  function handleCsvFile(file: File) {
    setCsvFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => setCsvText(String(reader.result ?? ''));
    reader.onerror = () => setError('Failed to read CSV file.');
    reader.readAsText(file);
  }

  /** PATCH the existing layer — only metadata changes are supported here. */
  async function submitEdit() {
    if (layerId == null) return;
    if (!label.trim()) { setError('Label is required.'); return; }
    setBusy(true); setError(null);
    try {
      const r = await apiFetch(`/map/custom-layers/${layerId}`, {
        method: 'PATCH',
        body: { label: label.trim(), color, icon, default_on: defaultOn },
      });
      if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
      await onSaved();
    } catch (e) {
      setError(`Update failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  /** POST /map/custom-layers with a raw Overpass selector clause. */
  async function submitOverpass() {
    if (!label.trim())          { setError('Label is required.'); return; }
    if (!overpassQuery.trim())  { setError('Overpass query is required.'); return; }
    setBusy(true); setError(null);
    try {
      await apiJSON('/map/custom-layers', {
        method: 'POST',
        body: {
          label: label.trim(),
          color, icon,
          source_type: 'overpass',
          overpass_query: overpassQuery.trim(),
          default_on: defaultOn,
        },
      });
      await onSaved();
    } catch (e) {
      setError(`Create failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  /** Two-step CSV creation: first POST creates the layer, then POST /csv
   *  ingests the points.  If the second step fails the empty layer is left
   *  behind — the admin can delete it from the panel. */
  async function submitCsv() {
    if (!label.trim()) { setError('Label is required.'); return; }
    if (!csvText)      { setError('Choose a CSV file first.'); return; }
    setBusy(true); setError(null);
    try {
      const created = await apiJSON<{ id: number }>('/map/custom-layers', {
        method: 'POST',
        body: {
          label: label.trim(),
          color, icon,
          source_type: 'csv',
          default_on: defaultOn,
        },
      });
      const result = await apiJSON<{ inserted: number; skipped: number }>(
        `/map/custom-layers/${created.id}/csv`,
        { method: 'POST', body: { csv: csvText } },
      );
      setInfo(`Inserted ${result.inserted} points (skipped ${result.skipped}).`);
      await onSaved();
    } catch (e) {
      setError(`CSV upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  function handleSubmit() {
    if (mode === 'edit')        return submitEdit();
    if (tab === 'pin') {
      // Pin tab is now two-step: require a confirmed preview before save.
      if (!pinPreview) { setError('Click "Detect" first to preview the brand.'); return; }
      return submitFromBrand(pinPreview.brand, pinPreview.amenity);
    }
    if (tab === 'overpass') {
      if (showAdvancedOverpass) return submitOverpass();
      if (brandSelected) return submitFromBrand(brandSelected.brand, brandSelected.amenity);
      setError('Search for a brand and pick a result, or switch to Advanced.');
      return;
    }
    if (tab === 'csv')          return submitCsv();
  }

  // The modal must escape any ancestor that creates a containing block for
  // `position: fixed` (the parent `PoiLayerPanel` uses `backdrop-blur` which
  // does exactly that in WebKit).  We render through a Portal to <body> so
  // the overlay covers the whole viewport instead of being clipped to the
  // panel's bounding box.
  return createPortal(
    <>
      {/* Floating hint shown only while picking on the map. The main modal
          and its overlay are hidden in that state so the admin can interact
          with the map underneath. Esc or the cancel button aborts. */}
      {picking && (
        <div
          className="fixed left-1/2 top-4 -translate-x-1/2 z-[2100] bg-card border border-border rounded-lg shadow-lg px-4 py-2 flex items-center gap-3 text-sm"
          role="status"
        >
          <span className="inline-flex items-center gap-1.5"><MapPin size={14} /> Click anywhere on the map to set the pin location</span>
          <button
            type="button"
            onClick={() => setPicking(false)}
            className="text-xs text-muted-foreground hover:text-foreground underline py-1 -my-1 min-h-tap"
          >Cancel (Esc)</button>
        </div>
      )}
      <div
        className={`fixed inset-0 z-[2000] flex items-center justify-center bg-black/50 p-4 ${
          picking ? 'hidden' : ''
        }`}
        onClick={onClose}
        role="dialog"
        aria-modal="true"
      >
      <div
        className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="font-semibold text-foreground">
            {mode === 'edit' ? 'Edit POI Layer' : 'New POI Layer'}
          </h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground text-lg leading-none min-h-tap"
            aria-label="Close"
          >×</button>
        </div>

        {/* Tabs (create mode only) */}
        {mode === 'create' && (
          <div className="flex border-b border-border">
            {([
              ['pin',      'Pin-drop',     MapPin],
              ['overpass', 'Brand search', Search],
              ['csv',      'CSV',          FileText],
            ] as [Tab, string, LucideIcon][]).map(([key, lbl, Icon]) => (
              <button
                key={key}
                onClick={() => { setTab(key); setError(null); setInfo(null); }}
                className={`flex-1 px-3 py-2 text-xs font-medium transition border-b-2 inline-flex items-center justify-center gap-1.5 ${
                  tab === key
                    ? 'border-primary text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              ><Icon size={14} />{lbl}</button>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="p-4 space-y-3">
          {/* Tab-specific instructions / inputs */}
          {mode === 'create' && tab === 'pin' && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground leading-snug">
                Pin one location of the chain you want to track (e.g. one Pilot
                station). The system reads the OSM <code>brand</code> tag at that
                point, counts every matching location across the USA and shows
                you a preview &mdash; you only commit if the result looks right.
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="Latitude (e.g. 41.5868)"
                  value={pinLat}
                  onChange={(e) => { setPinLat(e.target.value); setPinPreview(null); }}
                  className="flex-1 px-2 py-1.5 text-sm bg-background border border-border rounded"
                />
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="Longitude (e.g. -93.625)"
                  value={pinLng}
                  onChange={(e) => { setPinLng(e.target.value); setPinPreview(null); }}
                  className="flex-1 px-2 py-1.5 text-sm bg-background border border-border rounded"
                />
              </div>
              <div className="flex gap-2">
                {leafletMap && (
                  <button
                    type="button"
                    onClick={() => { setError(null); setInfo(null); setPicking(true); }}
                    className="flex-1 px-3 py-1.5 text-xs font-medium rounded border border-border bg-background hover:bg-muted transition flex items-center justify-center gap-1"
                  >
                    <MapPin size={14} /><span>Pick on map</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={runPinPreview}
                  disabled={pinPreviewing || !pinLat || !pinLng}
                  className="flex-1 px-3 py-1.5 text-xs font-medium rounded bg-primary/10 text-primary hover:bg-primary/20 transition disabled:opacity-50 inline-flex items-center justify-center gap-1.5 min-h-tap"
                >
                  {pinPreviewing ? 'Detecting…' : <><Search size={14} /> Detect brand</>}
                </button>
              </div>
              {pinPreview && <BrandPreviewCard preview={pinPreview} />}
            </div>
          )}

          {mode === 'create' && tab === 'overpass' && (
            <div className="space-y-2">
              {!showAdvancedOverpass && (
                <>
                  <p className="text-xs text-muted-foreground leading-snug">
                    Type a brand name (e.g. <code>Pilot</code>, <code>TA</code>,
                    <code> Loves</code>). The system searches OSM and shows you
                    every matching chain with the total US-wide location count.
                    Pick one to preview &amp; save.
                  </p>
                  <input
                    type="text"
                    value={brandQuery}
                    onChange={(e) => { setBrandQuery(e.target.value); setBrandSelected(null); }}
                    placeholder="Search brand…"
                    className="w-full px-2 py-1.5 text-sm bg-background border border-border rounded"
                  />
                  {brandLoading && (
                    <p className="text-2xs text-muted-foreground">Searching…</p>
                  )}
                  {!brandLoading && brandQuery.trim().length >= 2 && brandResults.length === 0 && (
                    <p className="text-2xs text-muted-foreground">No matching brands found.</p>
                  )}
                  {brandResults.length > 0 && (
                    <ul className="max-h-44 overflow-y-auto border border-border rounded divide-y divide-border">
                      {brandResults.map((r) => {
                        const isSel = brandSelected?.brand === r.brand
                          && brandSelected?.amenity === r.amenity;
                        return (
                          <li key={`${r.brand}|${r.amenity}`}>
                            <button
                              type="button"
                              onClick={() => {
                                setBrandSelected(r);
                                if (!label.trim()) setLabel(`${r.brand} (all locations)`);
                              }}
                              className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between gap-2 hover:bg-muted transition ${
                                isSel ? 'bg-primary/10' : ''
                              }`}
                            >
                              <span className="truncate">
                                <span className="font-semibold text-foreground">{r.brand}</span>
                                {r.amenity && (
                                  <span className="ml-1 text-muted-foreground">· {r.amenity}</span>
                                )}
                              </span>
                              <span className="shrink-0 text-2xs font-mono px-1.5 py-0.5 rounded bg-background border border-border text-muted-foreground">
                                {r.count.toLocaleString()} locations
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </>
              )}
              {showAdvancedOverpass && (
                <>
                  <p className="text-xs text-muted-foreground leading-snug">
                    Power-user mode. A single Overpass selector clause. Must
                    start with <code>node[</code>, <code>way[</code> or
                    <code> nwr[</code>. Statement separators (<code>;</code>),
                    <code> out</code>, recursion and area queries are blocked.
                  </p>
                  <textarea
                    value={overpassQuery}
                    onChange={(e) => setOverpassQuery(e.target.value)}
                    placeholder='node["amenity"="car_wash"]["hgv"="yes"]'
                    rows={3}
                    className="w-full px-2 py-1.5 text-xs font-mono bg-background border border-border rounded"
                  />
                </>
              )}
              <button
                type="button"
                onClick={() => setShowAdvancedOverpass((v) => !v)}
                className="text-2xs text-muted-foreground hover:text-foreground underline py-1 -my-1 min-h-tap"
              >
                {showAdvancedOverpass
                  ? <span className="inline-flex items-center gap-1"><ArrowLeft size={12} /> Back to brand search</span>
                  : <span className="inline-flex items-center gap-1">Advanced: write raw Overpass query <ArrowRight size={12} /></span>}
              </button>
            </div>
          )}

          {mode === 'create' && tab === 'csv' && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground leading-snug">
                CSV with a header row. <code>lat</code> and <code>lng</code> columns are required;
                any other columns are stored as point properties. Up to 50 000 rows / 5 MB.
              </p>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleCsvFile(f);
                }}
                className="block w-full text-xs text-muted-foreground"
              />
              {csvFileName && (
                <p className="text-2xs text-muted-foreground">
                  Loaded: <span className="font-mono">{csvFileName}</span> ({csvText.length.toLocaleString()} chars)
                </p>
              )}
            </div>
          )}

          {/* Common: label */}
          <div>
            <label className="block text-2xs font-medium text-muted-foreground mb-1">
              Label {mode === 'create' && tab === 'pin' && '(auto-filled if blank)'}
            </label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="My Custom Layer"
              maxLength={64}
              className="w-full px-2 py-1.5 text-sm bg-background border border-border rounded"
            />
          </div>

          {/* Common: color picker */}
          <div>
            <label className="block text-2xs font-medium text-muted-foreground mb-1">Color</label>
            <div className="flex flex-wrap gap-1.5">
              {COLOR_CHOICES.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setColor(c)}
                  className={`w-6 h-6 rounded border-2 transition ${
                    color === c ? 'border-foreground' : 'border-transparent'
                  } min-h-tap min-w-tap`}
                  style={{ background: c }}
                  aria-label={`Color ${c}`}
                />
              ))}
            </div>
          </div>

          {/* Common: icon picker */}
          <div>
            <label className="block text-2xs font-medium text-muted-foreground mb-1">Icon</label>
            <div className="flex flex-wrap gap-1">
              {ICON_CHOICES.map((i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setIcon(i)}
                  className={`w-7 h-7 text-base rounded border transition ${
                    icon === i
                      ? 'border-primary bg-primary/10'
                      : 'border-border hover:border-ring'
                  } min-h-tap min-w-tap`}
                >{i}</button>
              ))}
            </div>
          </div>

          {/* Common: default-on toggle */}
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={defaultOn}
              onChange={(e) => setDefaultOn(e.target.checked)}
            />
            <span className="text-foreground">Show on by default for everyone</span>
          </label>

          {/* Status messages */}
          {error && <p className="text-xs text-destructive">{error}</p>}
          {info  && <p className="text-xs text-ok">{info}</p>}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border">
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap"
          >Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={busy}
            className="px-3 py-1.5 text-xs font-semibold rounded bg-primary text-primary-foreground hover:bg-primary/90 transition disabled:opacity-50 min-h-tap"
          >
            {busy ? 'Saving…' : mode === 'edit' ? 'Save' : 'Create'}
          </button>
        </div>
      </div>
      </div>
    </>,
    document.body,
  );
}

/** Inline preview card showing the discovered brand, the total US-wide
 *  location count and a few representative addresses.  Pure presentational. */
function BrandPreviewCard({
  preview,
}: {
  preview: {
    brand: string; amenity: string; count: number;
    sample: { lat: number; lng: number; name: string; address?: string | null }[];
  };
}) {
  return (
    <div className="rounded-lg border border-border bg-background/60 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground truncate">
            {preview.brand}
          </p>
          {preview.amenity && (
            <p className="text-2xs text-muted-foreground">
              Category: <span className="font-mono">{preview.amenity}</span>
            </p>
          )}
        </div>
        <span className="shrink-0 text-xs font-bold px-2 py-1 rounded bg-primary/15 text-primary">
          {preview.count.toLocaleString()} in USA
        </span>
      </div>
      {preview.sample.length > 0 && (
        <div>
          <p className="text-3xs uppercase tracking-wide text-muted-foreground mb-1">
            Sample locations
          </p>
          <ul className="text-2xs text-muted-foreground space-y-0.5 max-h-24 overflow-y-auto">
            {preview.sample.map((s, i) => (
              <li key={i} className="truncate">
                • {s.name || preview.brand}
                {s.address ? <span className="text-foreground/70"> — {s.address}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-2xs text-muted-foreground">
        Saving will create a layer covering <strong>all {preview.count.toLocaleString()}</strong>{' '}
        matching locations across the United States.
      </p>
    </div>
  );
}

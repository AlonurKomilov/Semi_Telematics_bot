import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Eye, Map, MapPin, Plus, Trash2, X } from 'lucide-react';
import { ContextMenu, type MenuAction } from '../../components/ui/context-menu';
import { apiJSON, apiFetch } from '../../api/client';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useLeafletMap } from '../../hooks/useLeafletMap';
import { usePoiLayers } from '../../hooks/usePoiLayers';
import PoiLayerPanel from '@/features/live-map/PoiLayerPanel';
import { PageHeader, EmptyState } from '../../components/shell';
import { Button } from '../../components/ui/button';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import type { GeofenceFeature, GeofencesResponse } from '../../types';
import type L from 'leaflet';
import { GEOFENCE } from '../../config/mapColors';
import { toneClasses } from '../../lib/status';
import { Card } from '@/components/ui/card';
import { Tip } from '../../components/tooltip';

/** @deprecated — CSS injection is now handled by useLeafletMap */
// function ensureLeafletCSS() { ... }  — removed

const PLATFORM_COLOR = GEOFENCE.platform;
const SAMSARA_COLOR  = GEOFENCE.samsara;
const PREVIEW_COLOR  = GEOFENCE.preview;

const ZONE_TYPE_PRESETS = [
  'maintenance_shop', 'dispatch_yard', 'fuel_station', 'customer_site',
  'driver_rest_stop', 'warehouse', 'weigh_station', 'border_crossing', 'custom',
];

const ZONE_TYPE_ITEMS = ZONE_TYPE_PRESETS.map((z) => ({
  value: z, label: z.replace(/_/g, ' '),
}));

const ZONE_ROLE_OPTIONS = [
  { value: 'all',        label: 'All teams' },
  { value: 'fleet',      label: 'Fleet' },
  { value: 'dispatcher', label: 'Dispatch' },
  { value: 'safety',     label: 'Safety' },
  { value: 'admin',      label: 'Admin' },
  { value: 'driver',     label: 'Drivers' },
];

interface NominatimResult {
  place_id: number;
  display_name: string;
  lat: string;
  lon: string;
}

interface AddZoneForm {
  name: string;
  geofence_type: string;
  geofence_type_custom: string;
  address: string;
  latitude: string;
  longitude: string;
  radius_miles: string;
  zone_role: string;
}

const DEFAULT_FORM: AddZoneForm = {
  name: '',
  geofence_type: 'custom',
  geofence_type_custom: '',
  address: '',
  latitude: '',
  longitude: '',
  radius_miles: '0.25',
  zone_role: 'all',
};

export default function Geofences() {
  const { t } = useTranslation();
  const { has, role } = useViewPermissions();
  const canManage = has('can_geofence_all');
  const { isOwner } = useShellConfig();

  const { mapRef, leafletMap, isReady, invalidateSize } = useLeafletMap();
  const poiHook = usePoiLayers(leafletMap, isReady);
  const layerGroupRef = useRef<L.LayerGroup | null>(null);
  const previewMarkerRef = useRef<L.Marker | null>(null);
  const previewCircleRef = useRef<L.Circle | null>(null);
  const mapClickHandlerRef = useRef<((e: L.LeafletMouseEvent) => void) | null>(null);
  const geocodeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [geofences, setGeofences] = useState<GeofenceFeature[]>([]);
  const [selected, setSelected] = useState<GeofenceFeature | null>(null);
  // 'list' | 'detail' | 'add'
  const [panelMode, setPanelMode] = useState<'list' | 'detail' | 'add'>('list');
  const [form, setForm] = useState<AddZoneForm>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [geocodeResults, setGeocodeResults] = useState<NominatimResult[]>([]);
  const [geocoding, setGeocoding] = useState(false);
  const [pickingFromMap, setPickingFromMap] = useState(false);

  // ── Map features ─────────────────────────────────────────────────────────

  const loadFeatures = (Leaf: typeof L, features: GeofenceFeature[]) => {
    if (!leafletMap.current) return;
    layerGroupRef.current?.clearLayers();
    const group = Leaf.layerGroup().addTo(leafletMap.current);
    layerGroupRef.current = group;
    features.forEach((f) => {
      const isPlatform = f.properties?.source === 'platform';
      const color = isPlatform ? PLATFORM_COLOR : SAMSARA_COLOR;
      let layer: L.Layer | null = null;
      if (f.geometry.type === 'Polygon') {
        const coords = (f.geometry.coordinates as [number, number][][])[0].map(
          ([lng, lat]: [number, number]) => [lat, lng] as L.LatLngTuple,
        );
        layer = Leaf.polygon(coords, { color, weight: 2, fillOpacity: 0.15 });
      } else if (f.geometry.type === 'Point') {
        const radiusM = f.properties?.radius_meters ?? f.properties?.radius;
        if (radiusM) {
          const [lng, lat] = f.geometry.coordinates as [number, number];
          layer = Leaf.circle([lat, lng], { radius: radiusM, color, weight: 2, fillOpacity: 0.15 });
        }
      }
      if (layer) {
        layer.on('click', () => { setSelected(f); setPanelMode('detail'); });
        layer.bindPopup(f.properties?.name || 'Zone');
        layer.addTo(group);
      }
    });
    if (features.length > 0) {
      try {
        const bounds = (group as L.LayerGroup & { getBounds: () => L.LatLngBoundsExpression }).getBounds();
        leafletMap.current.fitBounds(bounds, { padding: [40, 40] });
      } catch { /* ignore */ }
    }
  };

  const fetchGeofences = async (Leaf?: typeof L) => {
    const data = await apiJSON<GeofencesResponse>('/geofences');
    const features = data.features || [];
    setGeofences(features);
    const leaf = Leaf ?? window.L;
    if (leaf && leafletMap.current) loadFeatures(leaf, features);
    return features;
  };

  // ── Preview circle for draft zone ────────────────────────────────────────

  const updatePreview = useCallback((latStr: string, lngStr: string, radiusMi: string) => {
    const Leaf = window.L;
    if (!Leaf || !leafletMap.current) return;
    const lat = parseFloat(latStr);
    const lng = parseFloat(lngStr);
    const radius = parseFloat(radiusMi) * 1609.344;
    if (isNaN(lat) || isNaN(lng) || isNaN(radius) || radius <= 0) {
      previewMarkerRef.current?.remove(); previewMarkerRef.current = null;
      previewCircleRef.current?.remove(); previewCircleRef.current = null;
      return;
    }
    if (previewCircleRef.current) {
      previewCircleRef.current.setLatLng([lat, lng]).setRadius(radius);
    } else {
      previewCircleRef.current = Leaf.circle([lat, lng], {
        radius, color: PREVIEW_COLOR, weight: 2, dashArray: '6 4', fillOpacity: 0.12,
      }).addTo(leafletMap.current);
    }
    if (previewMarkerRef.current) {
      previewMarkerRef.current.setLatLng([lat, lng]);
    } else {
      const icon = Leaf.divIcon({
        className: '',
        html: `<div style="width:12px;height:12px;background:${PREVIEW_COLOR};border:2px solid #fff;border-radius:50%;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>`,
        iconSize: [12, 12], iconAnchor: [6, 6],
      });
      previewMarkerRef.current = Leaf.marker([lat, lng], { icon, interactive: false })
        .addTo(leafletMap.current);
    }
    leafletMap.current.panTo([lat, lng], { animate: true, duration: 0.4 });
  }, []);

  useEffect(() => {
    if (panelMode === 'add') updatePreview(form.latitude, form.longitude, form.radius_miles);
  }, [form.latitude, form.longitude, form.radius_miles, panelMode, updatePreview]);

  useEffect(() => {
    if (panelMode !== 'add') {
      previewMarkerRef.current?.remove(); previewMarkerRef.current = null;
      previewCircleRef.current?.remove(); previewCircleRef.current = null;
    }
  }, [panelMode]);

  // ── Map click picking ────────────────────────────────────────────────────

  const startMapPick = () => {
    if (!leafletMap.current) return;
    setPickingFromMap(true);
    leafletMap.current.getContainer().style.cursor = 'crosshair';
    const handler = async (e: L.LeafletMouseEvent) => {
      const lat = e.latlng.lat.toFixed(6);
      const lng = e.latlng.lng.toFixed(6);
      setForm(f => ({ ...f, latitude: lat, longitude: lng, address: 'Locating…' }));
      setPickingFromMap(false);
      if (leafletMap.current) leafletMap.current.getContainer().style.cursor = '';
      if (mapClickHandlerRef.current && leafletMap.current) {
        leafletMap.current.off('click', mapClickHandlerRef.current);
        mapClickHandlerRef.current = null;
      }
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}`,
          { headers: { 'Accept-Language': 'en' } },
        );
        const data = await res.json();
        setForm(f => ({ ...f, address: data.display_name || `${lat}, ${lng}` }));
      } catch {
        setForm(f => ({ ...f, address: `${lat}, ${lng}` }));
      }
    };
    mapClickHandlerRef.current = handler;
    leafletMap.current.on('click', handler);
  };

  const cancelMapPick = () => {
    setPickingFromMap(false);
    if (leafletMap.current) leafletMap.current.getContainer().style.cursor = '';
    if (mapClickHandlerRef.current && leafletMap.current) {
      leafletMap.current.off('click', mapClickHandlerRef.current);
      mapClickHandlerRef.current = null;
    }
  };

  // ── Address geocoding ────────────────────────────────────────────────────

  const handleAddressChange = (value: string) => {
    setForm(f => ({ ...f, address: value }));
    setGeocodeResults([]);
    if (geocodeTimer.current) clearTimeout(geocodeTimer.current);
    if (value.trim().length < 4) return;
    geocodeTimer.current = setTimeout(async () => {
      setGeocoding(true);
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(value)}&limit=5&countrycodes=us`,
          { headers: { 'Accept-Language': 'en' } },
        );
        const results: NominatimResult[] = await res.json();
        setGeocodeResults(results);
      } catch { /* ignore */ }
      setGeocoding(false);
    }, 500);
  };

  const pickGeocodeResult = (r: NominatimResult) => {
    setForm(f => ({
      ...f, address: r.display_name,
      latitude: parseFloat(r.lat).toFixed(6),
      longitude: parseFloat(r.lon).toFixed(6),
    }));
    setGeocodeResults([]);
  };

  // ── Map init — replaced by useLeafletMap; load geofences once map is ready ──

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    fetchGeofences(window.L as typeof L).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isReady]);

  // Invalidate map size when panel switches so tiles recalculate
  useEffect(() => {
    setTimeout(() => invalidateSize(), 200);
  }, [panelMode, invalidateSize]);

  // ── Save ─────────────────────────────────────────────────────────────────

  const handleSave = async () => {
    setSaveError('');
    if (!form.name.trim()) { setSaveError('Name is required'); return; }
    const lat = parseFloat(form.latitude);
    const lng = parseFloat(form.longitude);
    const radius = parseFloat(form.radius_miles);
    if (isNaN(lat) || lat < -90 || lat > 90) { setSaveError('Set a location — search an address or click the map'); return; }
    if (isNaN(lng) || lng < -180 || lng > 180) { setSaveError('Set a location — search an address or click the map'); return; }
    if (isNaN(radius) || radius <= 0) { setSaveError('Radius must be greater than 0 mi'); return; }

    const effectiveType = form.geofence_type === 'custom' && form.geofence_type_custom.trim()
      ? form.geofence_type_custom.trim().toLowerCase().replace(/\s+/g, '_')
      : form.geofence_type;

    const zoneRole = isOwner ? form.zone_role : undefined;
    setSaving(true);
    try {
      await apiJSON('/geofences', {
        method: 'POST',
        body: {
          name: form.name.trim(),
          geofence_type: effectiveType,
          shape_type: 'circle',
          latitude: lat,
          longitude: lng,
          radius_miles: radius,
          ...(zoneRole !== undefined ? { zone_role: zoneRole } : {}),
        },
      });
      setPanelMode('list');
      setForm(DEFAULT_FORM);
      setGeocodeResults([]);
      await fetchGeofences();
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Failed to save zone');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (zoneId: number) => {
    setDeletingId(zoneId);
    try {
      const res = await apiFetch(`/geofences/${zoneId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      if (selected?.properties?.id === zoneId) { setSelected(null); setPanelMode('list'); }
      await fetchGeofences();
    } catch { /* ignore */ }
    setDeletingId(null);
  };

  const openAddPanel = () => {
    setSelected(null);
    setForm(DEFAULT_FORM);
    setSaveError('');
    setGeocodeResults([]);
    setPanelMode('add');
  };

  const closeAddPanel = () => {
    cancelMapPick();
    setPanelMode(selected ? 'detail' : 'list');
  };

  const zoneRoleLabel = (r?: string) =>
    ZONE_ROLE_OPTIONS.find(o => o.value === r)?.label ?? r ?? 'All teams';

  const platformCount = geofences.filter(f => f.properties?.source === 'platform').length;
  const samsaraCount  = geofences.filter(f => f.properties?.source === 'samsara').length;
  const coordsSet = form.latitude !== '' && form.longitude !== '';

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0">
        <PageHeader
          icon={MapPin}
          title={`${t('pages.geofences_title')} (${geofences.length})`}
          description={t('pages.geofences_desc')}
          meta={
            (samsaraCount > 0 || platformCount > 0) && (
              <div className="flex gap-3 text-xs text-muted-foreground">
                {samsaraCount > 0 && (
                  <span className="flex items-center gap-1.5">
                    {/* Legend swatches reference the same GEOFENCE source
                        colours the map markers use (config/mapColors.ts) —
                        one source of truth, so the dot can't drift from the
                        polygon it describes. */}
                    <span className="w-2 h-2 rounded-full inline-block" style={{ background: GEOFENCE.samsara }} />
                    {samsaraCount} Samsara
                  </span>
                )}
                {platformCount > 0 && (
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full inline-block" style={{ background: GEOFENCE.platform }} />
                    {platformCount} Platform
                  </span>
                )}
              </div>
            )
          }
          actions={
            canManage && panelMode !== 'add' ? (
              <Button onClick={openAddPanel}>
                <Plus className="mr-1.5" />
                Add zone
              </Button>
            ) : null
          }
        />
      </div>


      {/* Main content — map + side panel side by side */}
      <div className="flex gap-4 flex-1 min-h-0">

        {/* Map — always visible */}
        <div className="flex-1 relative rounded-lg border border-border overflow-hidden z-0 min-h-100">
          <div ref={mapRef} className="absolute inset-0" />
          <PoiLayerPanel poiHook={poiHook} leafletMap={leafletMap} />
      {/* Absolute against the MAP, not the page. This lived at the page
                  root with top-20, which put it across the <h1> — the banner
                  covered the page title it was meant to sit below. */}
              {pickingFromMap && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-[1000] bg-warn text-warn-foreground text-sm font-medium px-4 py-2 rounded-lg shadow-lg flex items-center gap-3 pointer-events-auto">
              <MapPin className="size-4" aria-hidden /> Click anywhere on the map to set zone center
              <button onClick={cancelMapPick} className="underline text-warn-foreground/80 hover:text-warn-foreground text-xs py-1 -my-1 min-h-tap">Cancel</button>
            </div>
          )}
        </div>

        {/* Right panel */}
        <div className="w-80 shrink-0 flex flex-col gap-3 overflow-y-auto">

          {/* ── ADD ZONE PANEL ── */}
          {panelMode === 'add' && (
            <Card className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold">New Zone</h2>
                <button onClick={closeAddPanel} aria-label="Close" className="text-muted-foreground hover:text-foreground py-0.5 -my-0.5 min-h-tap inline-flex items-center justify-center min-w-tap"><X className="size-3.5" /></button>
              </div>

              {/* Name */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Zone Name <span className="text-destructive">*</span></label>
                <input
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. Denver Yard"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>

              {/* Zone Type */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Zone Type</label>
                <Select
                  value={form.geofence_type}
                  onValueChange={(v) => setForm(f => ({ ...f, geofence_type: v }))}
                  items={ZONE_TYPE_ITEMS}
                >
                  <SelectTrigger className="w-full" aria-label="Zone Type"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ZONE_TYPE_ITEMS.map((it) => (
                      <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {form.geofence_type === 'custom' && (
                  <input
                    value={form.geofence_type_custom}
                    onChange={e => setForm(f => ({ ...f, geofence_type_custom: e.target.value }))}
                    placeholder="Custom type name"
                    className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                )}
              </div>

              {/* Location */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Location <span className="text-destructive">*</span></label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <input
                      value={form.address}
                      onChange={e => handleAddressChange(e.target.value)}
                      placeholder="Search address…"
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 pr-7 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                    />
                    {geocoding && <span className="absolute right-2 top-2.5 text-muted-foreground text-xs animate-pulse">⟳</span>}
                    {geocodeResults.length > 0 && (
                      <Card padding="none" className="absolute z-[500] mt-1 w-full shadow-xl overflow-hidden text-sm" render={<ul />}>
                        {geocodeResults.map(r => (
                          <li key={r.place_id}>
                            <button type="button" onClick={() => pickGeocodeResult(r)}
                              className="w-full text-left px-3 py-2 hover:bg-muted line-clamp-2 text-xs">
                              {r.display_name}
                            </button>
                          </li>
                        ))}
                      </Card>
                    )}
                  </div>
                  {/* The sentence below points at this button by name, so the
                      button has to carry one. It used to print the same map
                      glyph the sentence did, which made the mapping literal;
                      once that became an icon the hint pointed at nothing. */}
                  <Tip label={pickingFromMap ? 'Cancel map pick' : 'Pick on the map'}>
                  <button
                    type="button"
                    onClick={pickingFromMap ? cancelMapPick : startMapPick}
                    aria-label={pickingFromMap ? 'Cancel map pick' : 'Pick on the map'}
                    className={`shrink-0 px-2.5 py-2 min-h-tap rounded-lg border text-sm transition ${
                      pickingFromMap
                        ? `${toneClasses('warn')} animate-pulse`
                        : 'border-border bg-background hover:bg-muted'
                    }`}
                  >
                    <Map className="size-3.5" aria-hidden />
                  </button>
                  </Tip>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {pickingFromMap ? 'Click on the map to place the zone center' : 'Type an address, or pick it on the map'}
                </p>

                {coordsSet ? (
                  <div className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs mt-2 ${toneClasses('ok')}`}>
                    <Check className="size-3.5 shrink-0" aria-hidden />
                    <span className="font-mono flex-1 truncate">
                      {parseFloat(form.latitude).toFixed(4)}, {parseFloat(form.longitude).toFixed(4)}
                    </span>
                    <button type="button"
                      onClick={() => setForm(f => ({ ...f, latitude: '', longitude: '', address: '' }))}
                      className="text-danger ml-1 shrink-0 inline-flex items-center justify-center min-h-tap min-w-tap" aria-label="Clear location">
                      <X className="size-3.5" />
                    </button>
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-2 mt-2">
                    <input value={form.latitude}
                      onChange={e => setForm(f => ({ ...f, latitude: e.target.value }))}
                      placeholder="Lat" type="number" step="any"
                      className="w-full rounded-lg border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary" />
                    <input value={form.longitude}
                      onChange={e => setForm(f => ({ ...f, longitude: e.target.value }))}
                      placeholder="Lng" type="number" step="any"
                      className="w-full rounded-lg border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary" />
                  </div>
                )}
              </div>

              {/* Radius */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Radius (miles) <span className="text-destructive">*</span></label>
                <input
                  value={form.radius_miles}
                  onChange={e => setForm(f => ({ ...f, radius_miles: e.target.value }))}
                  placeholder="0.25"
                  type="number" step="0.01" min="0.01"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                />
                {form.radius_miles && !isNaN(parseFloat(form.radius_miles)) && parseFloat(form.radius_miles) > 0 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    ≈ {Math.round(parseFloat(form.radius_miles) * 1609.344).toLocaleString()} m
                  </p>
                )}
              </div>

              {/* Zone Attribution */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Team</label>
                {isOwner ? (
                  <Select
                    value={form.zone_role}
                    onValueChange={(v) => setForm(f => ({ ...f, zone_role: v }))}
                    items={ZONE_ROLE_OPTIONS}
                  >
                    <SelectTrigger className="w-full" aria-label="Team"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {ZONE_ROLE_OPTIONS.map((o) => (
                        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <p className="text-xs text-muted-foreground bg-muted rounded-lg px-3 py-2">
                    Auto-saved as <strong>{zoneRoleLabel(role)}</strong>
                  </p>
                )}
              </div>

              {saveError && (
                <p className={`text-xs rounded-lg px-3 py-2 ${toneClasses('danger')}`}>{saveError}</p>
              )}

              <div className="flex gap-2 pt-1">
                <button onClick={closeAddPanel}
                  className="flex-1 py-2 rounded-lg border border-border text-sm hover:bg-muted transition min-h-tap">
                  Cancel
                </button>
                <button onClick={handleSave} disabled={saving}
                  className="flex-1 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition disabled:opacity-60 min-h-tap">
                  {saving ? 'Saving…' : 'Create Zone'}
                </button>
              </div>
            </Card>
          )}

          {/* ── DETAIL PANEL ── */}
          {panelMode === 'detail' && selected && (
            <Card>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-base font-semibold break-words pr-2">{selected.properties?.name || 'Zone'}</h2>
                <button onClick={() => setPanelMode('list')} aria-label="Close" className="text-muted-foreground hover:text-foreground shrink-0 py-1 -my-1 min-h-tap inline-flex items-center justify-center min-w-tap"><X className="size-3.5" /></button>
              </div>
              <dl className="space-y-2 text-sm">
                <div><dt className="text-xs text-muted-foreground uppercase tracking-wide">Shape</dt><dd className="capitalize">{selected.geometry.type === 'Point' ? 'Circle' : 'Polygon'}</dd></div>
                {selected.properties?.geofence_type && (
                  <div><dt className="text-xs text-muted-foreground uppercase tracking-wide">Type</dt><dd className="capitalize">{selected.properties.geofence_type.replace(/_/g, ' ')}</dd></div>
                )}
                {selected.properties?.zone_role && (
                  <div><dt className="text-xs text-muted-foreground uppercase tracking-wide">Team</dt><dd>{zoneRoleLabel(selected.properties.zone_role)}</dd></div>
                )}
                {(selected.properties?.radius_meters ?? selected.properties?.radius) && (() => {
                  const m = (selected.properties?.radius_meters ?? selected.properties?.radius) as number;
                  return (
                    <div>
                      <dt className="text-xs text-muted-foreground uppercase tracking-wide">Radius</dt>
                      <dd>{(m / 1609.344).toFixed(2)} mi ({m.toLocaleString()} m)</dd>
                    </div>
                  );
                })()}
                {selected.geometry.type === 'Polygon' && (
                  <div><dt className="text-xs text-muted-foreground uppercase tracking-wide py-1 -my-1 min-h-tap">Vertices</dt><dd>{((selected.geometry.coordinates as [number, number][][])[0] || []).length}</dd></div>
                )}
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wide py-1 -my-1 min-h-tap">Source</dt>
                  <dd>
                    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-md border font-medium ${
                      selected.properties?.source === 'platform'
                        ? toneClasses('ok')
                        : toneClasses('info')
                    }`}>
                      {selected.properties?.source === 'platform' ? 'Platform' : 'Samsara'}
                    </span>
                  </dd>
                </div>
                {selected.properties?.company && (
                  <div><dt className="text-xs text-muted-foreground uppercase tracking-wide">Company</dt><dd>{selected.properties.company}</dd></div>
                )}
              </dl>
              {canManage && selected.properties?.source === 'platform' && typeof selected.properties?.id === 'number' && (
                <button
                  onClick={() => handleDelete(selected.properties!.id as number)}
                  disabled={deletingId === selected.properties?.id}
                  className="mt-4 w-full text-sm text-destructive hover:bg-destructive/10 rounded-lg py-1.5 transition border border-destructive/30"
                >
                  {deletingId === selected.properties?.id ? 'Deleting…' : <span className="inline-flex items-center justify-center gap-1.5"><Trash2 className="size-3.5" aria-hidden /> Delete Zone</span>}
                </button>
              )}
            </Card>
          )}

          {/* ── ZONE LIST ── */}
          {geofences.length > 0 && panelMode !== 'add' && (
            <Card padding="compact" className="flex-1 overflow-y-auto min-h-0">
              <h3 className="text-xs text-muted-foreground uppercase tracking-wide mb-2">All Zones</h3>
              <ul className="space-y-0.5">
                {geofences.map((f, i) => {
                  const canDelete = canManage && f.properties?.source === 'platform' && typeof f.properties?.id === 'number';
                  // Right-click the zone → Open, plus Delete for managers
                  // on platform-owned zones (Samsara-sourced zones are
                  // read-only here, same as the inline ✕ gate).
                  const zoneMenu: MenuAction[] = [
                    { key: 'open', label: 'Open zone', icon: <Eye className="text-muted-foreground size-3.5" />, onSelect: () => { setSelected(f); setPanelMode('detail'); } },
                    ...(canDelete ? [{ key: 'delete', label: 'Delete zone', icon: <Trash2 className="size-3.5" />, danger: true, separatorBefore: true, onSelect: () => handleDelete(f.properties!.id as number) }] : []),
                  ];
                  return (
                  <ContextMenu key={i} items={zoneMenu} render={<li className="flex items-center gap-1 min-w-0" />}>
                    <button
                      onClick={() => { setSelected(f); setPanelMode('detail'); }}
                      className={`flex-1 min-w-0 text-left px-2 py-1.5 rounded text-sm transition flex items-center gap-2 ${
                        selected === f && panelMode === 'detail'
                          ? 'bg-primary/15 text-primary'
                          : 'text-foreground/80 hover:bg-muted'
                      }`}
                    >
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: f.properties?.source === 'platform' ? GEOFENCE.platform : GEOFENCE.samsara }} />
                      <span className="truncate">{f.properties?.name || `Zone ${i + 1}`}</span>
                    </button>
                    {canDelete && (
                      <button
                        onClick={() => handleDelete(f.properties!.id as number)}
                        disabled={deletingId === f.properties?.id}
                        className="text-destructive/70 hover:text-destructive text-xs px-1 shrink-0 py-1 -my-1 min-h-tap"
                        aria-label="Delete zone"
                      >
                        {deletingId === f.properties?.id ? '…' : <X className="size-3.5" />}
                      </button>
                    )}
                  </ContextMenu>
                  );
                })}
              </ul>
            </Card>
          )}

          {/* Empty state when no zones and not in add mode */}
          {geofences.length === 0 && panelMode !== 'add' && (
            <EmptyState
              icon={MapPin}
              title="No geofences yet"
              description={
                canManage
                  ? "Click \"Add zone\" to create your first geofence — pick a location on the map or paste an address."
                  : 'Ask an admin to create geofences for your team.'
              }
              action={
                canManage ? (
                  <Button onClick={openAddPanel}>
                    <Plus className="mr-1.5" />
                    Add zone
                  </Button>
                ) : undefined
              }
            />
          )}

          {/* Hint when list has items and none selected */}
          {geofences.length > 0 && panelMode === 'list' && (
            <div className="text-center text-muted-foreground text-xs py-2">
              Click a zone on the map or in the list to view details
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * MapTypeControl — floating map-type switcher panel.
 *
 * Inspired by the Google Maps "Map details" panel. Lets the user switch
 * between three base tile layers and toggle a road-labels overlay.
 *
 * Usage:
 *   const { mapType, showLabels, setMapType, setShowLabels, isReady } = useLeafletMap();
 *   // …inside the map container div:
 *   <MapTypeControl {...{ mapType, showLabels, setMapType, setShowLabels, isReady }} />
 *
 * Tile sources (all free, no API key required):
 *   Standard  → OpenStreetMap
 *   Satellite → ESRI World Imagery
 *   Terrain   → OpenTopoMap (shows elevation contours; max zoom 17)
 *   Labels    → ESRI World Boundaries & Places overlay (satellite/terrain only)
 */

import { useState } from 'react';
import { MAP_TYPE_PREVIEW } from '../../config/mapColors';
import { Check, ChevronDown, ChevronUp, Map as MapIcon, Mountain, Satellite, type LucideIcon } from '../../lib/icons';
import type { MapProvider, MapType } from '@/hooks/useLeafletMap';
import { cn } from '@/lib/utils';
import { toast } from '../../lib/toast';

import { apiJSON } from '../../api/client';
import { Tip } from '../../components/tooltip';
import { usePermissions } from '../../hooks/usePermissions';
import type { MapEngineState } from './engine/useMapEngine';

interface MapTypeControlProps {
  mapType: MapType;
  showLabels: boolean;
  setMapType: (type: MapType) => void;
  setShowLabels: (show: boolean) => void;
  isReady: boolean;
  /** Whose tiles are under the overlays, and whether Google is even on
   *  offer.  The row is drawn only when choosing would do something. */
  provider?: MapProvider;
  engine?: Pick<MapEngineState, 'googleAvailable' | 'fellBackFrom' | 'reason' | 'refresh'>;
}

/** The two basemaps, in the order a picker should offer them: the one
 *  that costs nothing first.  The label says whose map it is — a person
 *  choosing "Google" is choosing a familiar map, and the copy says so
 *  rather than naming an API. */
const PROVIDERS: { id: MapProvider; label: string }[] = [
  { id: 'osm',    label: 'OpenStreetMap' },
  { id: 'google', label: 'Google' },
];

/** Visual thumbnail configs for each tile type. */
const MAP_TYPES: {
  id: MapType;
  label: string;
  /** CSS background used for the thumbnail preview card. */
  preview: string;
  /** lucide icon shown inside the card. */
  icon: LucideIcon;
}[] = [
  {
    id:      'standard',
    label:   'Default',
    preview: MAP_TYPE_PREVIEW.standard,
    icon:    MapIcon,
  },
  {
    id:      'satellite',
    label:   'Satellite',
    preview: MAP_TYPE_PREVIEW.satellite,
    icon:    Satellite,
  },
  {
    id:      'terrain',
    label:   'Terrain',
    preview: MAP_TYPE_PREVIEW.terrain,
    icon:    Mountain,
  },
];

export default function MapTypeControl({
  mapType,
  showLabels,
  setMapType,
  setShowLabels,
  isReady,
  provider = 'osm',
  engine,
}: MapTypeControlProps) {
  // Collapsed by default so the live-map opens with a clean overlay —
  // the user expands the picker only when they actually want to switch
  // map type / labels.
  const [collapsed, setCollapsed] = useState(true);
  const [saving, setSaving] = useState(false);
  const { has } = usePermissions();
  // The basemap is an ACCOUNT setting — one truth for everyone who looks
  // at the map — so changing it is config, not a view preference, and
  // rides the account-wide config flag.  Everyone else sees which is on.
  const canChoose = has('can_manage_config_all');

  const chooseProvider = async (id: MapProvider) => {
    if (id === provider || saving) return;
    setSaving(true);
    try {
      await apiJSON('/map/config', { method: 'PUT', body: JSON.stringify({ engine: id }) });
      engine?.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not change the map');
    } finally {
      setSaving(false);
    }
  };

  if (!isReady) return null;

  return (
    <div
      className="absolute bottom-6 left-3 z-[1000] bg-card/95 backdrop-blur border border-border rounded-xl shadow-lg text-sm select-none"
      style={{ minWidth: '196px' }}
    >
      {/* Header */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        // Four rounded corners only while COLLAPSED, where the header IS the
        // panel. Expanded, it is docked on three edges: its hover fill then
        // curves away from the straight divider under it and ~15px of panel
        // background shows through each bottom corner at Pill.
        className={`w-full flex items-center justify-between px-3 py-2.5 font-semibold text-foreground hover:bg-muted/60 transition ${
          collapsed ? 'rounded-xl' : 'rounded-t-xl'
        }`}
      >
        <span className="flex items-center gap-1.5">
          <MapIcon className="text-muted-foreground size-4" />
          <span>Map Type</span>
        </span>
        {collapsed
          ? <ChevronDown className="text-muted-foreground size-3.5" />
          : <ChevronUp className="text-muted-foreground size-3.5" />}
      </button>

      {!collapsed && (
        <div className="border-t border-border px-3 pt-2.5 pb-3 space-y-3">
          {/* Whose map — only when Google is on offer on this server */}
          {engine?.googleAvailable && (
            <div className="space-y-1">
              <div className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">Map</div>
              <div className="flex gap-1" role="radiogroup" aria-label="Map provider">
                {PROVIDERS.map(({ id, label }) => {
                  const active = provider === id;
                  const btn = (
                    <button
                      key={id}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      disabled={!canChoose || saving}
                      onClick={() => void chooseProvider(id)}
                      className={cn(
                        'flex-1 min-h-tap rounded-md border px-2 py-1 text-xs transition',
                        active
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border text-muted-foreground hover:border-foreground/40',
                        !canChoose && 'cursor-default',
                      )}
                    >
                      {label}
                    </button>
                  );
                  return canChoose ? btn : (
                    <Tip key={id} label="The map is an account-wide setting. Ask whoever manages configuration to change it.">
                      {btn}
                    </Tip>
                  );
                })}
              </div>
              {engine.fellBackFrom === 'google' && engine.reason && (
                <p className="text-2xs text-muted-foreground">{engine.reason}</p>
              )}
            </div>
          )}
          {/* Tile type card grid */}
          <div className="flex gap-2 justify-between">
            {MAP_TYPES.map(({ id, label, preview, icon: Icon }) => {
              const active = mapType === id;
              return (
                <button
                  key={id}
                  onClick={() => setMapType(id)}
                  className="flex flex-col items-center gap-1 flex-1"
                  aria-label={label}
                >
                  {/* Preview card */}
                  <div
                    className={`w-full h-11 rounded-lg border-2 flex items-start justify-end p-1 transition ${
                      active
                        ? 'border-primary ring-1 ring-primary/40'
                        : 'border-transparent hover:border-border'
                    }`}
                    style={{ background: preview }}
                  >
                    {/* Check badge */}
                    {active && (
                      <span className="bg-primary text-primary-foreground rounded-full w-4 h-4 flex items-center justify-center leading-none">
                        <Check className="size-2.5" />
                      </span>
                    )}
                  </div>
                  {/* Icon + label */}
                  <Icon className={cn(active ? 'text-primary' : 'text-muted-foreground', 'size-4.5')} />
                  <span
                    className={`text-2xs leading-tight ${
                      active ? 'text-primary font-medium' : 'text-muted-foreground'
                    }`}
                  >
                    {label}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Labels toggle — only meaningful for satellite/terrain.
              Standard OSM tiles already include road labels in the tile artwork. */}
          {mapType !== 'standard' && (
            <label className="flex items-center gap-2.5 cursor-pointer py-0.5 hover:text-foreground group">
              {/* Hit box split from paint: the box reads as 16px but the
                  target has to clear 24px, and -m-1 keeps the row's gap. */}
              <span
                onClick={() => setShowLabels(!showLabels)}
                className="min-h-tap min-w-tap -m-1 flex-shrink-0 inline-flex items-center justify-center"
                role="checkbox"
                aria-checked={showLabels}
                tabIndex={0}
                onKeyDown={(e) => e.key === ' ' && setShowLabels(!showLabels)}
              >
                <span
                  aria-hidden
                  className={`w-4 h-4 rounded border flex items-center justify-center transition ${
                    showLabels
                      ? 'border-transparent bg-primary'
                      : 'border-border bg-muted group-hover:border-ring'
                  }`}
                >
                  {showLabels && (
                    <Check className="size-3 text-primary-foreground" />
                  )}
                </span>
              </span>
              <span className={`text-xs ${showLabels ? 'text-foreground' : 'text-muted-foreground'}`}>
                Labels
              </span>
            </label>
          )}
        </div>
      )}
    </div>
  );
}

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
import { Check, ChevronDown, ChevronUp, Map as MapIcon, Mountain, Satellite, type LucideIcon } from 'lucide-react';
import type { MapType } from '@/hooks/useLeafletMap';

interface MapTypeControlProps {
  mapType: MapType;
  showLabels: boolean;
  setMapType: (type: MapType) => void;
  setShowLabels: (show: boolean) => void;
  isReady: boolean;
}

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
    // Green represents road/greenspace colors of OSM
    preview: 'linear-gradient(135deg, #4a8c5e 0%, #6aab7e 40%, #c8d8a0 100%)',
    icon:    MapIcon,
  },
  {
    id:      'satellite',
    label:   'Satellite',
    // Dark blue-grey represents aerial/space photography
    preview: 'linear-gradient(135deg, #1a2332 0%, #243447 50%, #2e4060 100%)',
    icon:    Satellite,
  },
  {
    id:      'terrain',
    label:   'Terrain',
    // Brown/green represents elevation contours
    preview: 'linear-gradient(135deg, #6b4c1e 0%, #8b6a2e 40%, #7a9c4a 100%)',
    icon:    Mountain,
  },
];

export default function MapTypeControl({
  mapType,
  showLabels,
  setMapType,
  setShowLabels,
  isReady,
}: MapTypeControlProps) {
  // Collapsed by default so the live-map opens with a clean overlay —
  // the user expands the picker only when they actually want to switch
  // map type / labels.
  const [collapsed, setCollapsed] = useState(true);

  if (!isReady) return null;

  return (
    <div
      className="absolute bottom-6 left-3 z-[1000] bg-card/95 backdrop-blur border border-border rounded-xl shadow-lg text-sm select-none"
      style={{ minWidth: '196px' }}
    >
      {/* Header */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between px-3 py-2.5 font-semibold text-foreground hover:bg-muted/60 rounded-xl transition"
      >
        <span className="flex items-center gap-1.5">
          <MapIcon size={16} className="text-muted-foreground" />
          <span>Map Type</span>
        </span>
        {collapsed
          ? <ChevronDown size={14} className="text-muted-foreground" />
          : <ChevronUp size={14} className="text-muted-foreground" />}
      </button>

      {!collapsed && (
        <div className="border-t border-border px-3 pt-2.5 pb-3 space-y-3">
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
                        <Check size={10} />
                      </span>
                    )}
                  </div>
                  {/* Icon + label */}
                  <Icon size={18} className={active ? 'text-primary' : 'text-muted-foreground'} />
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
                    <Check size={11} className="text-primary-foreground" />
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

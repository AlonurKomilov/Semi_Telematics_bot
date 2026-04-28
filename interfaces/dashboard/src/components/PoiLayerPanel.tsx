/**
 * PoiLayerPanel — floating map overlay toggle panel.
 *
 * Usage in a page that already calls usePoiLayers:
 *
 *   const poiHook = usePoiLayers(leafletMap, isReady);
 *   <PoiLayerPanel poiHook={poiHook} />
 */

import { useState } from 'react';
import { POI_LAYERS, POI_GROUPS } from '../config/poiLayers';
import type { PoiLayerDef } from '../config/poiLayers';
import type { UsePoiLayersResult, PoiFeature } from '../hooks/usePoiLayers';

interface PoiLayerPanelProps {
  poiHook: UsePoiLayersResult;
}

function exportCsv(allFeatures: Record<string, PoiFeature[]>, enabled: Record<string, boolean>) {
  const rows = ['Layer,Name,Brand,Lat,Lng,DEF,Diesel,Showers,Phone,Hours'];
  POI_LAYERS.forEach((def) => {
    if (!enabled[def.id]) return;
    (allFeatures[def.id] ?? []).forEach((f) => {
      const p = f.properties as Record<string, string> | null | undefined;
      const [lng, lat] = f.geometry.coordinates;
      const cell = (v: string) => `"${(v ?? '').replace(/"/g, '""')}"`;
      rows.push([
        cell(def.label),
        cell(p?.name || ''),
        cell(p?.brand || p?.operator || ''),
        lat.toFixed(6),
        lng.toFixed(6),
        cell(p?.['fuel:adblue'] === 'yes' ? 'Yes' : ''),
        cell(p?.['fuel:diesel'] === 'yes' ? 'Yes' : ''),
        cell(p?.shower === 'yes' ? 'Yes' : ''),
        cell(p?.phone || ''),
        cell(p?.opening_hours || ''),
      ].join(','));
    });
  });
  const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const a    = Object.assign(document.createElement('a'), {
    href: url,
    download: `poi_${new Date().toISOString().slice(0, 10)}.csv`,
  });
  a.click();
  URL.revokeObjectURL(url);
}

export default function PoiLayerPanel({ poiHook }: PoiLayerPanelProps) {
  const { enabled, toggle, loading, errors, counts, brandFilters, toggleBrand, presentBrands, allFeatures } =
    poiHook;

  const [collapsed, setCollapsed] = useState(false);
  // Per-layer chips panel open/closed (default: closed so panel stays compact)
  const [chipsOpen, setChipsOpen] = useState<Record<string, boolean>>({});

  return (
    <div
      className="absolute top-3 right-3 z-[1000] bg-card/95 backdrop-blur border border-border rounded-xl shadow-lg text-sm select-none"
      style={{ minWidth: '210px', maxWidth: '260px' }}
    >
      {/* Header */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between px-3 py-2.5 font-semibold text-foreground hover:bg-muted/60 rounded-xl transition"
      >
        <span className="flex items-center gap-1.5">
          <span>🗺</span>
          <span>Map Layers</span>
        </span>
        <span className="text-muted-foreground text-xs">{collapsed ? '▼' : '▲'}</span>
      </button>

      {/* CSV export — only shown when at least one layer is on with loaded data */}
      {!collapsed && Object.values(enabled).some(Boolean) && (
        <div className="border-t border-border px-3 py-1.5">
          <button
            onClick={() => exportCsv(allFeatures, enabled)}
            className="w-full text-[10px] text-muted-foreground hover:text-foreground transition text-left flex items-center gap-1"
          >
            <span>⬇</span><span>Export visible POIs as CSV</span>
          </button>
        </div>
      )}

      {/* Layer toggles, organised by group */}
      {!collapsed && (
        <div className="border-t border-border px-3 py-2 space-y-2.5">
          {/* Ungrouped layers (rendered first, no header) */}
          {POI_LAYERS.filter((d) => !d.group).map((def) => renderLayerRow(def))}

          {/* Grouped layers — one section per POI_GROUPS entry */}
          {POI_GROUPS.map((grp) => {
            const groupLayers = POI_LAYERS.filter((d) => d.group === grp.id);
            if (groupLayers.length === 0) return null;
            return (
              <div key={grp.id} className="space-y-1.5">
                {/* Group header — display only, NOT toggleable */}
                <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground pt-0.5">
                  {grp.icon && <span className="text-xs leading-none">{grp.icon}</span>}
                  <span>{grp.label}</span>
                  <span className="flex-1 border-t border-border ml-1" />
                </div>
                {/* Layers in this group */}
                <div className="space-y-1.5">
                  {groupLayers.map((def) => renderLayerRow(def))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  function renderLayerRow(def: PoiLayerDef) {
            const isOn          = enabled[def.id] ?? false;
            const isBusy        = loading[def.id] ?? false;
            const errMsg        = errors[def.id];
            const count         = counts[def.id] ?? 0;
            const activeBrands  = brandFilters[def.id]  ?? new Set<string>();
            const present       = presentBrands[def.id] ?? new Set<string>();
            // Only show chips that actually appear in loaded data
            const availableChips = (def.brandFilters ?? []).filter((bf) => present.has(bf.value));
            const hasChips       = isOn && !isBusy && availableChips.length > 0;
            const isChipsOpen    = chipsOpen[def.id] ?? false;

            return (
              <div key={def.id}>
                {/* ── Main toggle row ── */}
                <label className="flex items-center gap-2.5 cursor-pointer py-0.5 hover:text-foreground group">
                  {/* Checkbox */}
                  <span
                    onClick={() => toggle(def.id)}
                    className={`w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center transition
                      ${isOn ? 'border-transparent' : 'border-border bg-muted group-hover:border-ring'}`}
                    style={isOn ? { background: def.color } : {}}
                    role="checkbox"
                    aria-checked={isOn}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === ' ' && toggle(def.id)}
                  >
                    {isOn && <span className="text-white text-[9px] leading-none">✓</span>}
                  </span>

                  {/* Icon */}
                  <span className="text-base leading-none" onClick={() => toggle(def.id)}>
                    {def.icon}
                  </span>

                  {/* Label */}
                  <span
                    className={`flex-1 leading-tight text-xs ${isOn ? 'text-foreground' : 'text-muted-foreground'}`}
                    onClick={() => toggle(def.id)}
                  >
                    {def.label}
                  </span>

                  {/* Count badge */}
                  {isOn && !isBusy && count > 0 && (
                    <span
                      className="text-[9px] font-bold px-1.5 py-0.5 rounded-full text-white leading-none"
                      style={{ background: def.color }}
                    >
                      {count > 999 ? '999+' : count}
                    </span>
                  )}

                  {/* Spinner */}
                  {isBusy && (
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-muted border-t-primary animate-spin flex-shrink-0" />
                  )}
                </label>

                {/* Error */}
                {errMsg && (
                  <p className="text-[10px] text-destructive leading-tight pl-7 pb-0.5">⚠ {errMsg}</p>
                )}

                {/* ── Brand filter section ── */}
                {hasChips && (
                  <>
                    {/* Collapse toggle row */}
                    <button
                      onClick={() =>
                        setChipsOpen((prev) => ({ ...prev, [def.id]: !prev[def.id] }))
                      }
                      className="flex items-center gap-1 pl-7 text-[10px] text-muted-foreground hover:text-foreground transition py-0.5"
                    >
                      <span style={{ fontSize: '8px' }}>{isChipsOpen ? '▲' : '▼'}</span>
                      <span>Filter brands</span>
                      {/* Active-filter count badge (visible even when chips are collapsed) */}
                      {activeBrands.size > 0 && (
                        <span
                          className="px-1.5 py-0.5 rounded-full text-white text-[9px] font-bold leading-none"
                          style={{ background: def.color }}
                        >
                          {activeBrands.size} active
                        </span>
                      )}
                    </button>

                    {/* Chips — shown only when expanded */}
                    {isChipsOpen && (
                      <div className="flex flex-wrap gap-1 pl-7 pt-1 pb-1">
                        {availableChips.map((bf) => {
                          const active = activeBrands.has(bf.value);
                          return (
                            <button
                              key={bf.value}
                              onClick={() => toggleBrand(def.id, bf.value)}
                              title={bf.label}
                              className={`text-[10px] px-1.5 py-0.5 rounded-full border transition leading-none
                                ${active
                                  ? 'text-white border-transparent'
                                  : 'text-muted-foreground border-border hover:border-ring hover:text-foreground'
                                }`}
                              style={active ? { background: def.color, borderColor: def.color } : {}}
                            >
                              {bf.icon ? `${bf.icon} ` : ''}{bf.label}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}
              </div>
            );
  }
}


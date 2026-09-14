/**
 * PoiLayerPanel — floating map overlay toggle panel.
 *
 * Usage in a page that already calls usePoiLayers:
 *
 *   const poiHook = usePoiLayers(leafletMap, isReady);
 *   <PoiLayerPanel poiHook={poiHook} />
 */

import { useState } from 'react';
import type L from 'leaflet';
import { Check, ChevronDown, ChevronUp, Download, Map as MapIcon, Pencil, Trash2, TriangleAlert } from '@/lib/icons';
import { ContextMenu, type MenuAction } from '@/components/ui/context-menu';
import { POI_GROUPS, staleSourceAge } from './layers';
import type { PoiLayerDef } from './layers';
import type { UsePoiLayersResult, PoiFeature } from './usePoiLayers';
import { useViewPermissions } from '@/hooks/useViewPermissions';
import { apiFetch } from '@/api/client';
import { readableTextOn } from '@/mods';
import { Freshness, Tip } from '@/components/tooltip';
import CustomLayerEditor from './CustomLayerEditor';
import PoiIcon from './PoiIcon';

interface PoiLayerPanelProps {
  poiHook: UsePoiLayersResult;
  /** Optional Leaflet map ref — when provided, the custom-layer editor's
   *  pin-drop tab gains a “Pick on map” button that temporarily hides the
   *  modal so the admin can click on the map.  Pages that don't pass this
   *  fall back to manual lat/lng entry. */
  leafletMap?: React.RefObject<L.Map | null>;
}

/** Custom layer ids carry the `custom_<dbId>` prefix — use this to detect them. */
const CUSTOM_ID_RE = /^custom_(\d+)$/;

function exportCsv(
  layers: PoiLayerDef[],
  allFeatures: Record<string, PoiFeature[]>,
  enabled: Record<string, boolean>,
) {
  const rows = ['Layer,Name,Brand,Lat,Lng,DEF,Diesel,Showers,Phone,Hours'];
  layers.forEach((def) => {
    if (!enabled[def.id]) return;
    (allFeatures[def.id] ?? []).forEach((f) => {
      const p = f.properties as Record<string, string> | null | undefined;
      const [lng, lat] = f.geometry.coordinates;
      const cell = (v: string) => `"${(v ?? '').replace(/"/g, '""')}"`;
      // The def_station layer is BY DEFINITION an AdBlue-capable subset, so
      // every row from it should report DEF=Yes even when the upstream
      // fuel:adblue tag is missing on the OSM node.
      const hasDef = def.id === 'def_station' || p?.['fuel:adblue'] === 'yes';
      rows.push([
        cell(def.label),
        cell(p?.name || ''),
        cell(p?.brand || p?.operator || ''),
        lat.toFixed(6),
        lng.toFixed(6),
        cell(hasDef ? 'Yes' : ''),
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

export default function PoiLayerPanel({ poiHook, leafletMap }: PoiLayerPanelProps) {
  const {
    enabled, toggle, loading, errors, counts,
    brandFilters, toggleBrand, presentBrands, allFeatures,
    effectiveLayers, refreshCustomLayers, sourceAsOf, notes,
  } = poiHook;
  const { has } = useViewPermissions();
  // POI is a sub-feature with its own view verb, so a role that sees the
  // map may still be refused the overlays.  The hook stops fetching for
  // the same reason; this stops the card being offered.  Offered and
  // then refused is the shape the extension's abilities mechanism was
  // built to prevent, and the dashboard owes the same.
  const canViewPoi = has('can_view_poi');
  // Said once per render, not once per row: the extract's age is a fact
  // about the SOURCE, and thirteen rows repeating it would read as
  // thirteen problems.
  const staleAge = staleSourceAge(sourceAsOf);
  const canManage = has('can_manage_poi_layers');

  // Collapsed by default — the live-map opens cleaner; the user
  // expands the Map Layers panel only when they want to toggle POIs
  // or custom layers.
  const [collapsed, setCollapsed] = useState(true);
  // Per-layer chips panel open/closed (default: closed so panel stays compact)
  const [chipsOpen, setChipsOpen] = useState<Record<string, boolean>>({});
  // Custom layer editor modal state — null = closed; { mode: 'create' } or
  // { mode: 'edit', layerId } depending on the launch action.
  const [editorState, setEditorState] = useState<
    | null
    | { mode: 'create' }
    | { mode: 'edit'; layerId: number }
  >(null);

  const customLayers = effectiveLayers.filter((d) => d.group === 'custom');

  /** DELETE a custom layer with confirmation — then refresh the hook so the
   *  panel re-renders without the row.  Failures are surfaced via alert(). */
  async function handleDeleteCustom(layerDbId: number, label: string) {
    if (!window.confirm(`Delete layer “${label}”?  This cannot be undone.`)) return;
    try {
      const r = await apiFetch(`/map/custom-layers/${layerDbId}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refreshCustomLayers();
    } catch (e) {
      alert(`Failed to delete layer: ${(e as Error).message}`);
    }
  }

  // After the hooks, never before them: an early return above a hook is
  // a different render on the second pass, and React counts them.
  if (!canViewPoi) return null;

  return (
    <div
      className="absolute top-3 right-3 z-[1000] bg-card/95 backdrop-blur border border-border rounded-xl shadow-lg text-sm select-none"
      style={{ minWidth: '210px', maxWidth: '260px' }}
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
          <span>Map Layers</span>
        </span>
        {collapsed
          ? <ChevronDown className="text-muted-foreground size-3.5" />
          : <ChevronUp className="text-muted-foreground size-3.5" />}
      </button>

      {/* CSV export — only shown when at least one layer is on with loaded data */}
      {!collapsed && Object.values(enabled).some(Boolean) && (
        <div className="border-t border-border px-3 py-1.5">
          <button
            onClick={() => exportCsv(effectiveLayers, allFeatures, enabled)}
            className="w-full text-2xs text-muted-foreground hover:text-foreground transition text-left flex items-center gap-1 py-1 -my-1 min-h-tap"
          >
            <Download className="size-3" /><span>Export all POIs in current area (CSV)</span>
          </button>
        </div>
      )}

      {/* Layer toggles, organised by group */}
      {!collapsed && (
        <div className="border-t border-border px-3 py-2 space-y-2.5">
          {/* Ungrouped layers (rendered first, no header) */}
          {effectiveLayers.filter((d) => !d.group).map((def) => renderLayerRow(def))}

          {/* Grouped layers — one section per POI_GROUPS entry */}
          {POI_GROUPS.map((grp) => {
            const groupLayers = effectiveLayers.filter((d) => d.group === grp.id);
            // Hide "My Layers" header entirely when there are no custom layers
            // AND the user lacks management rights (so it stays clean for drivers).
            if (groupLayers.length === 0 && !(grp.id === 'custom' && canManage)) return null;
            return (
              <div key={grp.id} className="space-y-1.5">
                {/* Group header — display only, NOT toggleable */}
                <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-muted-foreground pt-0.5">
                  {grp.icon && <PoiIcon icon={grp.icon} size={12} className="shrink-0" />}
                  <span>{grp.label}</span>
                  <span className="flex-1 border-t border-border ml-1" />
                  {/* "+ New" trigger only on the custom group, only for admins */}
                  {grp.id === 'custom' && canManage && (
                    <Tip label="Create new POI layer">
                      <button
                        type="button"
                        onClick={() => setEditorState({ mode: 'create' })}
                        className="text-2xs font-bold text-primary hover:underline normal-case tracking-normal py-1 -my-1 min-h-tap"
                      >
                        + New
                      </button>
                    </Tip>
                  )}
                </div>
                {/* Layers in this group */}
                <div className="space-y-1.5">
                  {groupLayers.map((def) => renderLayerRow(def))}
                </div>
                {/* Empty-state hint for admins so they know how to start */}
                {grp.id === 'custom' && groupLayers.length === 0 && canManage && (
                  <p className="text-2xs text-muted-foreground italic pl-1">
                    No custom layers yet. Click “+ New” to add one.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* WHOSE data this is, and — when we know it — how old.

          Two jobs, gated separately on purpose.  The credit is ODbL's,
          and it is the only place this surface credits OpenStreetMap for
          the overlay: the basemap's attribution is Esri's or Google's
          and covers nothing here.  It used to ride on the DATE being
          known, so a layer whose mirror reported no extract stamp
          dropped the attribution along with the age.

          The age is the answer to "why is the truck stop that opened
          this summer not here" — the public mirrors run months behind,
          and a missing stop is the source being old, not the map being
          wrong.  `Freshness` renders its children alone when `ts` is
          null, so unknown simply says nothing; a wrong date would be
          worse than none. */}
      {!collapsed && (
        <div className="border-t border-border px-3 py-1.5">
          <Freshness ts={sourceAsOf}>
            <span className="text-2xs text-muted-foreground">OpenStreetMap data</span>
          </Freshness>
        </div>
      )}

      {/* Custom-layer editor modal */}
      {editorState && canManage && (
        <CustomLayerEditor
          mode={editorState.mode}
          layerId={editorState.mode === 'edit' ? editorState.layerId : undefined}
          existingLayers={customLayers}
          leafletMap={leafletMap}
          onClose={() => setEditorState(null)}
          onSaved={async () => { await refreshCustomLayers(); setEditorState(null); }}
        />
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
            // Custom-layer admin controls (✏ / 🗑) appear only on layers whose id
            // matches `custom_<dbId>` and only when the current user has the
            // manage permission — the same gate the backend enforces.
            const customMatch    = CUSTOM_ID_RE.exec(def.id);
            const customDbId     = customMatch ? Number(customMatch[1]) : null;
            const showCustomCtrls = canManage && customDbId !== null;
            // Right-click a custom layer → Edit / Delete (mirrors the ✏/🗑
            // controls, which stay visible).  Built-in layers get no menu.
            const layerMenu: MenuAction[] = showCustomCtrls ? [
              { key: 'edit', label: 'Edit layer', icon: <Pencil className="text-muted-foreground size-3.5" />, onSelect: () => setEditorState({ mode: 'edit', layerId: customDbId! }) },
              { key: 'delete', label: 'Delete layer', icon: <Trash2 className="size-3.5" />, danger: true, separatorBefore: true, onSelect: () => handleDeleteCustom(customDbId!, def.label) },
            ] : [];

            return (
              <ContextMenu key={def.id} items={layerMenu} render={<div />}>
                {/* ── Main toggle row ── */}
                <label className="flex items-center gap-2.5 cursor-pointer py-0.5 hover:text-foreground group">
                  {/* Checkbox — hit box split from paint: the box reads as
                      16px, the target has to clear 24px, and -m-1 gives the
                      row back the gap the paint had. */}
                  <span
                    onClick={() => toggle(def.id)}
                    className="min-h-tap min-w-tap -m-1 flex-shrink-0 inline-flex items-center justify-center"
                    role="checkbox"
                    aria-checked={isOn}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === ' ' && toggle(def.id)}
                  >
                    <span
                      aria-hidden
                      className={`w-4 h-4 rounded border flex items-center justify-center transition
                        ${isOn ? 'border-transparent' : 'border-border bg-muted group-hover:border-ring'}`}
                      style={isOn ? { background: def.color } : {}}
                    >
                      {/* Not `text-white`.  The box is filled with the
                          LAYER's colour, and white on the amber fuel
                          colour is 2.15:1 against the 3:1 a mark that
                          identifies a control needs.  Measured per
                          colour, both ways, and the better one wins. */}
                      {isOn && <Check className="size-3" style={{ color: readableTextOn(def.color) }} />}
                    </span>
                  </span>

                  {/* Icon — token-coloured like the label so on/off state reads the same */}
                  <PoiIcon
                    icon={def.icon}
                    size={16}
                    className={`shrink-0 ${isOn ? 'text-foreground' : 'text-muted-foreground'}`}
                    onClick={() => toggle(def.id)}
                  />

                  {/* Label */}
                  <span
                    className={`flex-1 leading-tight text-xs ${isOn ? 'text-foreground' : 'text-muted-foreground'} py-1 -my-1 min-h-tap`}
                    onClick={() => toggle(def.id)}
                  >
                    {def.label}
                  </span>

                  {/* Count badge */}
                  {/* The pill keeps the layer's colour and takes the ink
                      that can be READ on it.  It was `text-white` on
                      every layer, which measures 2.15:1 on amber fuel
                      and 2.43:1 on cyan rest areas against 4.5:1 for
                      text — seven of the eight failed, and the eighth
                      is blue, which is why it shipped.  Chosen per
                      colour by comparing both extremes, every layer now
                      clears 4.5:1 (the tightest is the violet weigh
                      station at 4.68). */}
                  {isOn && !isBusy && count > 0 && (
                    <span
                      className="text-2xs font-bold px-1.5 py-0.5 rounded-full leading-none tabular-nums"
                      style={{ background: def.color, color: readableTextOn(def.color) }}
                    >
                      {count > 999 ? '999+' : count}
                    </span>
                  )}

                  {/* Spinner */}
                  {isBusy && (
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-muted border-t-primary animate-spin flex-shrink-0" />
                  )}

                  {/* Custom-layer admin controls */}
                  {showCustomCtrls && (
                    <>
                      <Tip label="Edit layer">
                        <button
                          type="button"
                          aria-label="Edit layer"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setEditorState({ mode: 'edit', layerId: customDbId! });
                          }}
                          className="text-muted-foreground hover:text-foreground leading-none px-0.5 py-1 -my-1 min-h-tap"
                        ><Pencil className="size-3" /></button>
                      </Tip>
                      <Tip label="Delete layer">
                        <button
                          type="button"
                          aria-label="Delete layer"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            handleDeleteCustom(customDbId!, def.label);
                          }}
                          className="text-muted-foreground hover:text-destructive leading-none px-0.5 py-1 -my-1 min-h-tap"
                        ><Trash2 className="size-3" /></button>
                      </Tip>
                    </>
                  )}
                </label>

                {/* Error */}
                {errMsg && (
                  <p className="text-2xs text-destructive leading-tight pl-7 pb-0.5 flex items-center gap-1"><TriangleAlert className="shrink-0 size-3" /> {errMsg}</p>
                )}

                {/* A layer that is ON and drew nothing said NOTHING here —
                    no badge, no note, just a ticked row over an empty map.
                    That is the shape the owner met in Chicago: "none" and
                    "we could not tell you" looked identical, and so did
                    "none" and "your own brand filter is hiding them".
                    Both halves are named now, and when the extract is
                    months behind that is named beside them — a truck stop
                    that opened since simply is not in the data, and only
                    a date can say so. */}
                {/* SOMETHING TRUE THAT IS NOT A FAULT.
                    "None in this view" is true of the VIEWPORT and
                    silent about what has no viewport to be in: 439 of
                    this account's 443 vendors have no address on file,
                    so the layer draws 4 and reads as "you have four".
                    Shown whether or not the view is empty, because the
                    number it corrects is the one beside the row. */}
                {isOn && !isBusy && !errMsg && notes[def.id] && (
                  <p className="text-2xs text-muted-foreground leading-tight pl-7 pb-0.5">
                    {notes[def.id]}
                  </p>
                )}
                {isOn && !isBusy && !errMsg && !notes[def.id] && count === 0 && (
                  <p className="text-2xs text-muted-foreground leading-tight pl-7 pb-0.5">
                    {(allFeatures[def.id]?.length ?? 0) > 0
                      ? 'None of the chosen brands in this view'
                      : 'None in this view'}
                    {staleAge && ` · OSM data ${staleAge} old`}
                  </p>
                )}

                {/* ── Brand filter section ── */}
                {hasChips && (
                  <>
                    {/* Collapse toggle row */}
                    <button
                      onClick={() =>
                        setChipsOpen((prev) => ({ ...prev, [def.id]: !prev[def.id] }))
                      }
                      className="flex items-center gap-1 pl-7 text-2xs text-muted-foreground hover:text-foreground transition py-0.5 min-h-tap"
                    >
                      {isChipsOpen ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
                      <span>Filter brands</span>
                      {/* Active-filter count badge (visible even when chips are collapsed) */}
                      {activeBrands.size > 0 && (
                        <span
                          className="px-1.5 py-0.5 rounded-full text-2xs font-bold leading-none"
                          style={{ background: def.color, color: readableTextOn(def.color) }}
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
                              className={`text-2xs px-1.5 py-0.5 rounded-full border transition leading-none
                                ${active
                                  ? 'border-transparent'
                                  : 'text-muted-foreground border-border hover:border-ring hover:text-foreground'
                                } min-h-tap`}
                              // The chip KEEPS the layer's colour — it is
                              // the one place the colour still carries
                              // meaning, tying the chip to its layer — and
                              // takes the ink that can be read on it.  Only
                              // fuel (#f59e0b) and DEF (#0d9488) have brand
                              // chips, and both clear 4.5:1 this way (8.8
                              // and 5.05); the violet that cannot is a
                              // layer with no chips at all.
                              style={active
                                ? { background: def.color, borderColor: def.color,
                                    color: readableTextOn(def.color) }
                                : {}}
                            >
                              {bf.label}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}
              </ContextMenu>
            );
  }
}


/**
 * The map's own controls, in the shape the dashboard uses: a header
 * that IS the control while collapsed, opening onto a body docked
 * under it.  Two of them — what the map is drawn on, and what is drawn
 * on top of it.
 *
 * Collapsed by default and only one open at a time, both for the same
 * reason: the column is 320px and the map is why the panel is open.  A
 * control parked open over it is rent charged on every glance.
 */
import { useState } from 'react';

import { MAP_ENGINES, MAP_TYPES, MAP_TYPE_LABEL, type MapType } from './tiles';
import type { MapEngine } from './engine';
import { POI_GROUPS, glyphSvg, type PoiLayerDef } from './poiLayers';
import type { PoiLayersState } from './usePoiLayers';

/** A picture OF the tiles rather than a colour from the palette — which
 *  is why these are literals.  The dashboard's `MAP_TYPE_PREVIEW`,
 *  kept identical so the same swatch means the same map on both. */
const PREVIEW: Record<MapType, string> = {
  standard:  'linear-gradient(135deg, #4a8c5e 0%, #6aab7e 40%, #c8d8a0 100%)',
  satellite: 'linear-gradient(135deg, #1a2332 0%, #243447 50%, #2e4060 100%)',
  terrain:   'linear-gradient(135deg, #6b4c1e 0%, #8b6a2e 40%, #7a9c4a 100%)',
};

/** Whose map it is.  "Esri", not "OpenStreetMap": the free tiles came
 *  from OSM's volunteer servers until they blocked app-distributed use,
 *  and a label naming a vendor the map no longer comes from is worse
 *  than a vague one.  The stored id stays `osm` — it is a saved
 *  preference, and renaming it would reset everyone's choice. */
const ENGINE_LABEL: Record<MapEngine, string> = { osm: 'Esri', google: 'Google' };

const Chevron = ({ open }: { open: boolean }) => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
       style={{ marginLeft: 'auto', color: 'var(--muted)' }}>
    {open ? <path d="m18 15-6-6-6 6" /> : <path d="m6 9 6 6 6-6" />}
  </svg>
);

interface Props {
  mapType: MapType;
  provider: MapEngine;
  /** Whether this ACCOUNT has Google at all — the server's answer, not
   *  a guess.  The row is drawn only when choosing would do something. */
  googleAvailable: boolean;
  /** What was actually drawn.  A lit chip over Esri tiles would be the
   *  picker lying about the map underneath it. */
  drewGoogle: boolean;
  onChoose: (type: MapType, provider: MapEngine) => void;
  poi: PoiLayersState;
}

type Open = 'base' | 'layers' | null;

export default function MapControls({
  mapType, provider, googleAvailable, drewGoogle, onChoose, poi,
}: Props) {
  const [open, setOpen] = useState<Open>(null);
  const press = (which: Exclude<Open, null>) =>
    setOpen((cur) => (cur === which ? null : which));

  return (
    <div style={{
      position: 'absolute', top: 8, left: 8, bottom: 8,
      // Zoom owns the top-right; leaving its column free is what keeps
      // one control from sitting half behind another.
      right: 46, zIndex: 500,
      display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
      gap: 6, minHeight: 0,
      // The container spans the map so its children can be bounded by
      // it; it must not swallow a drag on the map underneath.
      pointerEvents: 'none',
    }}>
      {/* ── what the map is drawn on ─────────────────────────────── */}
      <div className="mapctl">
        <button type="button" className="mapctl-head" aria-expanded={open === 'base'}
                aria-controls="mapctl-base" onClick={() => press('base')}>
          <span>{MAP_TYPE_LABEL[mapType]}</span>
          <Chevron open={open === 'base'} />
        </button>
        {open === 'base' && (
          <div className="mapctl-body" id="mapctl-base">
            {googleAvailable && (
              <div style={{ display: 'grid', gap: 4 }}>
                <div className="eyebrow muted">Map</div>
                <div className="row" role="radiogroup" aria-label="Which map" style={{ gap: 4 }}>
                  {MAP_ENGINES.map((e) => (
                    <button key={e} type="button" role="radio" aria-checked={provider === e}
                            className={`chip ${provider === e ? 'on' : ''}`}
                            style={{ flex: '1 1 0', justifyContent: 'center' }}
                            onClick={() => onChoose(mapType, e)}>
                      {/* Says what HAPPENED, not what was asked for: a
                          spent quota or a refused session falls back to
                          the free map, and the chip has to admit it. */}
                      {ENGINE_LABEL[e]}
                      {e === 'google' && provider === 'google' && !drewGoogle ? ' · off' : ''}
                    </button>
                  ))}
                </div>
                {provider === 'google' && !drewGoogle && (
                  <p className="small muted" style={{ margin: 0 }}>
                    Google could not be drawn — showing the free map.
                  </p>
                )}
              </div>
            )}
            <div className="row" role="radiogroup" aria-label="Map type" style={{ gap: 6 }}>
              {MAP_TYPES.map((t) => (
                <button key={t} type="button" className="maptype" role="radio"
                        aria-checked={mapType === t} onClick={() => onChoose(t, provider)}>
                  <i style={{ background: PREVIEW[t] }} aria-hidden />
                  <span>{MAP_TYPE_LABEL[t]}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── what is drawn on top of it ───────────────────────────── */}
      <div className="mapctl">
        <button type="button" className="mapctl-head" aria-expanded={open === 'layers'}
                aria-controls="mapctl-layers" onClick={() => press('layers')}>
          <span>Layers</span>
          {poi.activeCount > 0 && (
            <span className="mapctl-count" aria-label={`${poi.activeCount} on`}>
              {poi.activeCount}
            </span>
          )}
          <Chevron open={open === 'layers'} />
        </button>
        {open === 'layers' && (
          <div className="mapctl-body" id="mapctl-layers">
            {POI_GROUPS.map((g) => {
              const inGroup = poi.layers.filter((l) => l.group === g.id);
              // A group with nothing in it is not an empty section, it is
              // no section — "My layers" exists only once the account has
              // made one.
              if (!inGroup.length) return null;
              return (
                <div key={g.id} style={{ display: 'grid', gap: 2 }}>
                  <div className="eyebrow muted">{g.label}</div>
                  {inGroup.map((def) => (
                    <LayerRow key={def.id} def={def} poi={poi} />
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function LayerRow({ def, poi }: { def: PoiLayerDef; poi: PoiLayersState }) {
  const on = !!poi.enabled[def.id];
  const busy = !!poi.loading[def.id];
  const err = poi.errors[def.id];
  const note = poi.notes[def.id];
  const count = poi.counts[def.id];
  const here = poi.present[def.id];
  const picked = poi.brands[def.id];
  const chips = on && here?.size
    ? (def.brands ?? []).filter((b) => here.has(b.value))
    : [];

  return (
    <div>
      <button type="button" className="layer-row" aria-pressed={on}
              onClick={() => poi.toggle(def.id)}>
        <span className="layer-dot" style={{ background: on ? def.color : 'transparent',
                                             borderColor: on ? 'rgba(255,255,255,.55)' : 'var(--edge)' }}
              aria-hidden
              dangerouslySetInnerHTML={{ __html: on ? glyphSvg(def.glyph, 9) : '' }} />
        <span style={{ flex: '1 1 auto', minWidth: 0, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={def.label}>
          {def.label}
        </span>
        {busy && <span className="muted small" aria-live="polite">…</span>}
        {/* Both numbers, always, when the cap bit.  A count that showed
            only what is drawn would turn "900 stops near you" into
            "250" with nothing saying so. */}
        {!busy && on && count && (
          <span className="muted small" style={{ flex: 'none' }}>
            {count.shown < count.total ? `${count.shown}/${count.total}` : count.total}
          </span>
        )}
      </button>
      {err && (
        <p className="small" style={{ margin: '0 0 2px 24px', color: 'var(--warn)' }}>{err}</p>
      )}
      {/* Quiet, not coloured: the layer is on and working, the view is
          simply wider than the server answers for.  Painting this the
          same as a failure would make a reopened panel look broken. */}
      {!err && note && (
        <p className="small muted" style={{ margin: '0 0 2px 24px' }}>{note}</p>
      )}
      {count && on && count.shown < count.total && (
        <p className="small muted" style={{ margin: '0 0 2px 24px' }}>
          Nearest {count.shown} shown — zoom in for the rest
        </p>
      )}
      {chips.length > 0 && (
        <div className="row" style={{ gap: 3, flexWrap: 'wrap', margin: '2px 0 4px 24px' }}>
          {chips.map((b) => (
            <button key={b.value} type="button"
                    className={`chip ${picked?.has(b.value) ? 'on' : ''}`}
                    style={{ fontSize: 11, padding: '1px 7px' }}
                    aria-pressed={!!picked?.has(b.value)}
                    onClick={() => poi.toggleBrand(def.id, b.value)}>
              {b.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

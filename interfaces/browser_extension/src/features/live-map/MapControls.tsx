/**
 * The map's controls, in the dashboard's places and in the dashboard's
 * grammar: zoom in the top-left corner (Leaflet's own, themed), Map
 * Layers top-right, Map Type bottom-left.
 *
 * The positions are not a preference.  Somebody who works in both
 * screens builds one muscle memory, and a control that moves corners
 * between them spends that memory on every switch — which is the whole
 * of the owner's "hudda dashboardnikidag".  So: same corners, same
 * header shape, same row grammar, same colours.
 *
 * What is NOT copied is the dashboard's width.  This column is 320px,
 * so the cards are narrower and the brand chips fold; the arrangement
 * is the dashboard's, the measurements are this panel's.
 */
import { useState } from 'react';

import { MAP_TYPES, MAP_TYPE_LABEL, type MapType } from './tiles';
import type { MapEngine } from './engine';
import { POI_GROUPS, glyphSvg, readableOn, type PoiLayerDef } from './poi/layers';
import type { PoiLayersState } from './poi/usePoiLayers';

/** A picture OF the tiles rather than a colour from the palette — which
 *  is why these are literals.  The dashboard's `MAP_TYPE_PREVIEW`, kept
 *  identical so the same swatch means the same map on both screens. */
const PREVIEW: Record<MapType, string> = {
  standard:  'linear-gradient(135deg, #4a8c5e 0%, #6aab7e 40%, #c8d8a0 100%)',
  satellite: 'linear-gradient(135deg, #1a2332 0%, #243447 50%, #2e4060 100%)',
  terrain:   'linear-gradient(135deg, #6b4c1e 0%, #8b6a2e 40%, #7a9c4a 100%)',
};

/** Whose map it is.  "Esri", not "OpenStreetMap": the free tiles came
 *  from OSM's volunteer servers until they blocked app-distributed use,
 *  and a label naming a vendor the map no longer comes from is worse
 *  than a vague one.  The stored value stays `osm` — it is the
 *  account's setting, and renaming it would mean nothing draws. */
const ENGINE_LABEL: Record<MapEngine, string> = { osm: 'Esri', google: 'Google' };

/**
 * ONE, and it is deliberately the smallest number that works.
 *
 * These cards are siblings of `.leaflet-container`, not children, so
 * they do not compete with Leaflet's 200–1000 pane ladder at all — the
 * container carries `isolation: isolate`, which keeps that ladder
 * inside it.  Beating a stacking context whose own level is `auto`
 * takes exactly 1.
 *
 * It was 500, copied from the dashboard where the map is NOT isolated.
 * That number outranked the header's dropdown (`.menu`, z-index 10),
 * so the feature switcher opened UNDERNEATH the map controls — the
 * same symptom the isolation rule in index.css was added to fix, walked
 * back in through a control that sits outside the thing being isolated.
 */
const CTL_Z = 1;

const ICON = {
  layers:
    '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0'
    + 'l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0'
    + 'l8.58-3.9A1 1 0 0 0 22 12"/><path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0'
    + 'l8.58-3.9A1 1 0 0 0 22 17"/>',
  map:
    '<path d="M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894'
    + 'l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381'
    + 'V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z"/>'
    + '<path d="M15 5.764v15"/><path d="M9 3.236v15"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  down:  '<path d="m6 9 6 6 6-6"/>',
  up:    '<path d="m18 15-6-6-6 6"/>',
  warn:  '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
       + '<path d="M12 9v4"/><path d="M12 17h.01"/>',
};

function Icon({ name, size = 14, className }: { name: keyof typeof ICON; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
         className={className} style={{ flex: 'none' }}
         dangerouslySetInnerHTML={{ __html: ICON[name] }} />
  );
}

/** One card header, identical on both controls: icon, name, chevron. */
function Head({ icon, label, open, onPress, id, extra }: {
  icon: keyof typeof ICON; label: string; open: boolean;
  onPress: () => void; id: string; extra?: React.ReactNode;
}) {
  return (
    <button type="button" className="mapctl-head" aria-expanded={open}
            aria-controls={id} onClick={onPress}>
      <Icon name={icon} className="muted" />
      <span>{label}</span>
      {extra}
      <Icon name={open ? 'up' : 'down'} size={12} className="muted"
            // The chevron is the only thing pushed to the far edge, so
            // the badge sits beside the name it counts rather than
            // floating alone at the other end of the header.
            />
    </button>
  );
}

interface Props {
  mapType: MapType;
  /** The ACCOUNT's engine, read from the server — not a device choice. */
  provider: MapEngine;
  /** Whether Google is on offer for this account at all. */
  googleAvailable: boolean;
  /** What was actually drawn.  Google can be the account's engine and
   *  still fail to draw — a spent quota, a refused session. */
  drewGoogle: boolean;
  /** May this person change the account's engine? (`config.all`) */
  canManageEngine: boolean;
  /** A write is in flight — the row is not pressable twice. */
  savingEngine: boolean;
  /** What went wrong on the last engine write, if anything. */
  engineError: string;
  onChooseType: (type: MapType) => void;
  onChooseEngine: (engine: MapEngine) => void;
  showLabels: boolean;
  onToggleLabels: (on: boolean) => void;
  poi: PoiLayersState;
}

export default function MapControls(p: Props) {
  return (
    <>
      <LayersCard poi={p.poi} />
      <TypeCard {...p} />
    </>
  );
}

/* ── Map Layers — top-right, where the dashboard keeps it ─────────── */

function LayersCard({ poi }: { poi: PoiLayersState }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mapctl" style={{ position: 'absolute', top: 8, right: 8, zIndex: CTL_Z,
                                     maxWidth: 232, maxHeight: 'calc(100% - 16px)' }}>
      <Head icon="layers" label="Map Layers" open={open} id="mapctl-layers"
            onPress={() => setOpen((o) => !o)}
            extra={poi.activeCount > 0 && (
              <span className="mapctl-count" aria-label={`${poi.activeCount} on`}>
                {poi.activeCount}
              </span>
            )} />
      {open && (
        <div className="mapctl-body" id="mapctl-layers">
          {POI_GROUPS.map((g) => {
            const inGroup = poi.layers.filter((l) => l.group === g.id);
            // A group with nothing in it is not an empty section, it is
            // no section — "My layers" exists once the account makes one.
            if (!inGroup.length) return null;
            return (
              <div key={g.id} style={{ display: 'grid', gap: 1 }}>
                <div className="grouphead">
                  <span>{g.label}</span>
                  <i aria-hidden />
                </div>
                {inGroup.map((def) => <LayerRow key={def.id} def={def} poi={poi} />)}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function LayerRow({ def, poi }: { def: PoiLayerDef; poi: PoiLayersState }) {
  const [chipsOpen, setChipsOpen] = useState(false);
  const on = !!poi.enabled[def.id];
  const busy = !!poi.loading[def.id];
  const err = poi.errors[def.id];
  const note = poi.notes[def.id];
  const count = poi.counts[def.id];
  const here = poi.present[def.id];
  const picked = poi.brands[def.id];
  const chips = on && !busy && here?.size
    ? (def.brands ?? []).filter((b) => here.has(b.value))
    : [];

  return (
    <div>
      <button type="button" className="layer-row" aria-pressed={on}
              onClick={() => poi.toggle(def.id)}>
        {/* The dashboard's grammar: a checkbox that fills with the
            layer's own colour, so the swatch and the switch are one
            thing rather than two things to read. */}
        <span className="layer-check" aria-hidden
              style={on
                ? { background: def.color, borderColor: 'transparent', color: readableOn(def.color) }
                : undefined}>
          {on && <Icon name="check" size={11} />}
        </span>
        <span aria-hidden className={on ? 'layer-glyph on' : 'layer-glyph'}
              dangerouslySetInnerHTML={{ __html: glyphSvg(def.glyph, 14, 'currentColor') }} />
        <span className={on ? 'layer-name on' : 'layer-name'} title={def.label}>{def.label}</span>
        {busy && <span className="spinner" aria-label="Loading" />}
        {!busy && on && count && count.total > 0 && (
          <span className="layer-count"
                style={{ background: def.color, color: readableOn(def.color) }}>
            {count.total > 999 ? '999+' : count.total}
          </span>
        )}
      </button>
      {err && (
        <p className="row rowmsg" style={{ color: 'var(--warn)', gap: 4 }}>
          <Icon name="warn" size={11} /><span>{err}</span>
        </p>
      )}
      {/* Quiet, not coloured: the layer is on and working, the view is
          simply wider than the server answers for.  Painting this like a
          failure would make a reopened panel look broken. */}
      {!err && note && <p className="rowmsg muted">{note}</p>}
      {on && !busy && !err && !note && count && count.shown < count.total && (
        <p className="rowmsg muted">Nearest {count.shown} shown — zoom in for the rest</p>
      )}
      {/* An empty layer names the reason it is empty.  "0" alone sends
          somebody hunting a truck stop away believing there is none,
          when a chip they pressed two minutes ago is hiding it. */}
      {on && !busy && !err && !note && count?.total === 0 && (
        <p className="rowmsg muted">
          {count.fetched > 0 ? 'None of the chosen brands in this view' : 'None in this view'}
        </p>
      )}
      {chips.length > 0 && (
        <>
          <button type="button" className="chipstoggle" aria-expanded={chipsOpen}
                  onClick={() => setChipsOpen((c) => !c)}>
            <Icon name={chipsOpen ? 'up' : 'down'} size={11} />
            <span>Filter brands</span>
            {/* Visible while the chips are FOLDED, which is the whole
                point of it: a filter you cannot see is a filter you
                blame the data for. */}
            {!!picked?.size && (
              <span className="layer-count"
                    style={{ background: def.color, color: readableOn(def.color) }}>
                {picked.size} on
              </span>
            )}
          </button>
          {chipsOpen && (
            <div className="row chiprow">
              {chips.map((b) => (
                <button key={b.value} type="button"
                        className={`chip tiny ${picked?.has(b.value) ? 'on' : ''}`}
                        aria-pressed={!!picked?.has(b.value)}
                        onClick={() => poi.toggleBrand(def.id, b.value)}>
                  {b.label}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── Map Type — bottom-left, where the dashboard keeps it ─────────── */

function TypeCard({
  mapType, provider, googleAvailable, drewGoogle, canManageEngine, savingEngine,
  engineError, onChooseType, onChooseEngine, showLabels, onToggleLabels,
}: Props) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mapctl" style={{ position: 'absolute', bottom: 8, left: 8, zIndex: CTL_Z,
                                     maxHeight: 'calc(100% - 16px)' }}>
      <Head icon="map" label="Map Type" open={open} id="mapctl-base"
            onPress={() => setOpen((o) => !o)} />
      {open && (
        <div className="mapctl-body" id="mapctl-base">
          {googleAvailable && (
            <div style={{ display: 'grid', gap: 4 }}>
              <div className="grouphead"><span>Map</span><i aria-hidden /></div>
              <div className="row" role="radiogroup" aria-label="Which map" style={{ gap: 4 }}>
                {(['osm', 'google'] as const).map((e) => (
                  <button key={e} type="button" role="radio" aria-checked={provider === e}
                          className={`chip ${provider === e ? 'on' : ''}`}
                          style={{ flex: '1 1 0', justifyContent: 'center' }}
                          disabled={!canManageEngine || savingEngine}
                          onClick={() => onChooseEngine(e)}>
                    {ENGINE_LABEL[e]}
                  </button>
                ))}
              </div>
              {/* Why the buttons are dead, said where they are dead.  A
                  disabled control with no reason reads as broken. */}
              {!canManageEngine && (
                <p className="rowmsg muted" style={{ marginLeft: 0 }}>
                  The map is an account-wide setting — ask whoever manages configuration.
                </p>
              )}
              {canManageEngine && engineError && (
                <p className="rowmsg" style={{ marginLeft: 0, color: 'var(--warn)' }}>{engineError}</p>
              )}
              {/* Says what HAPPENED, not what was asked for.  This is the
                  honest form of the old "Google · unavailable" chip: the
                  account IS on Google, and Google did not answer. */}
              {provider === 'google' && !drewGoogle && (
                <p className="rowmsg muted" style={{ marginLeft: 0 }}>
                  Google could not be drawn — showing the free map.
                </p>
              )}
            </div>
          )}
          <div className="row" role="radiogroup" aria-label="Map type" style={{ gap: 6 }}>
            {MAP_TYPES.map((t) => (
              <button key={t} type="button" className="maptype" role="radio"
                      aria-checked={mapType === t} onClick={() => onChooseType(t)}>
                <i style={{ background: PREVIEW[t] }} aria-hidden>
                  {mapType === t && <b><Icon name="check" size={9} /></b>}
                </i>
                <span>{MAP_TYPE_LABEL[t]}</span>
              </button>
            ))}
          </div>
          {/* Only where it means something: Standard tiles carry their own
              names, and so does Google's roadmap — switching it on there
              would print every road twice. */}
          {mapType !== 'standard' && (
            <button type="button" className="layer-row" aria-pressed={showLabels}
                    onClick={() => onToggleLabels(!showLabels)}>
              <span className="layer-check" aria-hidden
                    style={showLabels ? { background: 'var(--primary-fill)', borderColor: 'transparent' } : undefined}>
                {showLabels && <Icon name="check" size={11} />}
              </span>
              <span className={showLabels ? 'layer-name on' : 'layer-name'}>Road names</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}

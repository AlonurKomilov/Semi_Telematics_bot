/**
 * Which map this account is drawn on, resolved once per surface.
 *
 * The server decides (features/location/map_engine.py): an account
 * setting names an engine, and the answer falls back to the free one
 * whenever the platform cannot serve the paid one. This hook does not
 * re-decide any of that — it asks, loads what the answer needs, and
 * reports one of three states so a surface never has to guess:
 *
 *   'loading'  — nothing drawn yet, and it is not an error
 *   'osm'      — Leaflet, the free engine, and what every failure ends as
 *   'google'   — the script is loaded and `window.google.maps` exists
 *
 * FAILING TO OSM IS THE WHOLE POINT. A refused key, a referrer the key
 * does not allow, an exhausted daily quota and an unreachable network
 * all land in the same place: the account keeps a working map. Google
 * refusing is a billing or configuration fact, never a reason for a
 * carrier to stare at a blank rectangle — so the hook reports the
 * reason for a log and hands back 'osm'.
 */
import { useEffect, useRef, useState } from 'react';

import { apiJSON } from '../../../api/client';
import { isGoogleLoaded, loadGoogleMaps } from './googleLoader';

export type MapEngine = 'osm' | 'google';

/** The shape `/map/engine` answers with. `key` rides only with the
 *  engine that needs one — an account on OSM is never handed a billable
 *  credential it might load anyway. */
interface EngineWire {
  engine: MapEngine;
  requested: MapEngine;
  engines: MapEngine[];
  google_available: boolean;
  key?: string;
}

export interface MapEngineState {
  /** What to draw. Never 'google' until the script is really loaded. */
  engine: MapEngine | null;
  /** True until the first answer; a surface shows its skeleton. */
  loading: boolean;
  /** Set when the account ASKED for an engine it did not get, so a
   *  settings page can explain rather than appear not to have saved. */
  fellBackFrom: MapEngine | null;
  /** Why, in one sentence, for a log or a tooltip. */
  reason: string;
}

const OSM: MapEngine = 'osm';
const GOOGLE: MapEngine = 'google';

export function useMapEngine(): MapEngineState {
  const [state, setState] = useState<MapEngineState>({
    engine: null, loading: true, fellBackFrom: null, reason: '',
  });
  // A surface can unmount while the script is still arriving; setting
  // state then is a React warning and, worse, a map built into a
  // detached node.
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const settle = (s: MapEngineState) => { if (alive.current) setState(s); };

    (async () => {
      let wire: EngineWire;
      try {
        wire = await apiJSON<EngineWire>('/map/engine');
      } catch {
        // The endpoint is part of drawing a map, not of deciding
        // whether to draw one.  Unreachable means the free engine.
        settle({ engine: OSM, loading: false, fellBackFrom: null,
                 reason: 'The map engine could not be read; using OpenStreetMap.' });
        return;
      }

      if (wire.engine !== GOOGLE) {
        settle({
          engine: OSM, loading: false,
          fellBackFrom: wire.requested === GOOGLE ? GOOGLE : null,
          reason: wire.requested === GOOGLE
            ? 'This account asks for Google, but the platform has no Google Maps key configured.'
            : '',
        });
        return;
      }

      if (!wire.key) {
        settle({ engine: OSM, loading: false, fellBackFrom: GOOGLE,
                 reason: 'The server chose Google but sent no key.' });
        return;
      }

      try {
        await loadGoogleMaps(wire.key);
        if (!isGoogleLoaded()) throw new Error('google.maps missing after load');
        settle({ engine: GOOGLE, loading: false, fellBackFrom: null, reason: '' });
      } catch (e) {
        settle({
          engine: OSM, loading: false, fellBackFrom: GOOGLE,
          reason: `Google Maps did not load (${e instanceof Error ? e.message : 'unknown'}); using OpenStreetMap.`,
        });
      }
    })();

    return () => { alive.current = false; };
  }, []);

  return state;
}

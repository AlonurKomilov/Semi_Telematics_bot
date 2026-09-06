/**
 * Which basemap this account is drawn on, resolved once per surface.
 *
 * The server decides (features/location/map_engine.py): an account
 * setting names an engine, and the answer falls back to the free one
 * whenever the platform cannot serve the paid one. This hook does not
 * re-decide any of that — it asks, and reports one of three states so
 * a surface never has to guess:
 *
 *   loading  — nothing known yet, and it is not an error
 *   'osm'    — OpenStreetMap under Leaflet, the free engine, and what
 *              every failure ends as
 *   'google' — Google's tiles under the SAME Leaflet, through the Map
 *              Tiles API; every overlay stays where it is
 *
 * Google is tiles here, not a script. The Maps JavaScript API forbids
 * its content inside a non-Google map; the Map Tiles API is sold for
 * exactly this. So there is nothing to load — a tile layer asks the
 * server for a session token (`tileSession`) and points Leaflet at
 * Google's tile URL.
 *
 * FAILING TO OSM IS THE WHOLE POINT. A refused key, a spent daily quota,
 * an unreachable network, a session Google would not open — all land
 * in the same place: the account keeps a working map. Google refusing
 * is a billing or configuration fact, never a reason for a carrier to
 * stare at a blank rectangle.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { apiJSON } from '../../../api/client';

export type MapEngine = 'osm' | 'google';
/** Google's names for our Map Type picker's three choices. */
export type TileType = 'roadmap' | 'satellite' | 'terrain';

/** The shape `/map/engine` answers with. The key rides only with the
 *  engine that needs one; this hook never reads it — the session
 *  endpoint bakes it into the tile URL. */
export interface EngineWire {
  engine: MapEngine;
  requested: MapEngine;
  engines: MapEngine[];
  google_available: boolean;
}

/** What `/map/tiles/session` answers with: the template Leaflet
 *  expands, and what the attribution control must show. */
export interface TileSession {
  type: TileType;
  tile_url: string;
  viewport_url: string;
  tile_size: number;
  image_format: string;
  expiry: number;
  max_zoom: number;
}

export interface MapEngineState {
  /** What to draw. Null until the first answer. */
  engine: MapEngine | null;
  loading: boolean;
  /** Whether the platform CAN draw Google at all — a picker offers the
   *  choice only when picking it would do something. */
  googleAvailable: boolean;
  /** Set when the account ASKED for an engine it did not get, so a
   *  control can explain rather than appear not to have saved. */
  fellBackFrom: MapEngine | null;
  /** Why, in one sentence, for a log or a tooltip. */
  reason: string;
  /** A Google session for one tile type. Rejects when the platform or
   *  Google refuses; the caller falls back to OSM tiles. */
  tileSession: (type: TileType) => Promise<TileSession>;
  /** Re-ask the server — after the account's setting changed. */
  refresh: () => void;
  /** Report that Google tiles failed after resolution (a session the
   *  server refused, tiles that 4xx): the surface drops to OSM and the
   *  reason is kept. */
  fallBack: (reason: string) => void;
}

const OSM: MapEngine = 'osm';
const GOOGLE: MapEngine = 'google';

export function providerFromWire(wire: EngineWire): Pick<MapEngineState, 'engine' | 'fellBackFrom' | 'reason' | 'googleAvailable'> {
  const fellBack = wire.engine !== GOOGLE && wire.requested === GOOGLE;
  return {
    engine: wire.engine === GOOGLE ? GOOGLE : OSM,
    googleAvailable: !!wire.google_available,
    fellBackFrom: fellBack ? GOOGLE : null,
    reason: fellBack ? 'This account asks for Google, but the platform has no Google Maps key configured.' : '',
  };
}

export function useMapEngine(): MapEngineState {
  const [state, setState] = useState<Omit<MapEngineState, 'tileSession' | 'refresh' | 'fallBack'>>({
    engine: null, loading: true, googleAvailable: false, fellBackFrom: null, reason: '',
  });
  const [tick, setTick] = useState(0);
  // A surface can unmount while the answer is in flight; setting state
  // then is a React warning, and a map built into a detached node.
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const settle = (s: typeof state) => { if (alive.current) setState(s); };
    (async () => {
      try {
        const wire = await apiJSON<EngineWire>('/map/engine');
        settle({ loading: false, ...providerFromWire(wire) });
      } catch {
        // The endpoint is part of drawing a map, not of deciding
        // whether to draw one. Unreachable means the free engine.
        settle({ engine: OSM, loading: false, googleAvailable: false, fellBackFrom: null,
                 reason: 'The map engine could not be read; using OpenStreetMap.' });
      }
    })();
    return () => { alive.current = false; };
  }, [tick]);

  const tileSession = useCallback(
    (type: TileType) => apiJSON<TileSession>(`/map/tiles/session?type=${encodeURIComponent(type)}`),
    [],
  );
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  const fallBack = useCallback((reason: string) => {
    setState((s) => (s.engine === GOOGLE
      ? { ...s, engine: OSM, fellBackFrom: GOOGLE, reason }
      : s));
  }, []);

  return { ...state, tileSession, refresh, fallBack };
}

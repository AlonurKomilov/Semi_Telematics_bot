/**
 * Whose basemap the panel draws, and the session Google needs to draw it.
 *
 * The dashboard has had this since the Google engine landed; the panel
 * had one hardcoded layer.  The owner asked for the two to match, and
 * they now call the same two endpoints — `/map/engine` for what this
 * account is allowed to use, `/map/tiles/session` for the per-session
 * tile template Google hands out.  Both ride `can_view_location`, which
 * a panel token has carried since v1, so parity cost no new permission.
 *
 * Sessions EXPIRE.  Google's template is only good until `expiry`, and a
 * map left open all afternoon outlives it — so the session is re-asked
 * for when it is close to running out rather than cached for the life of
 * the panel, which is what every other answer here does.
 */
import { apiJSON } from '../../api/client';

export type MapEngine = 'osm' | 'google';
export type TileType = 'roadmap' | 'satellite' | 'terrain';

/** Our three words for a basemap, in Google's. */
export const GOOGLE_TYPE: Record<string, TileType> = {
  standard: 'roadmap',
  satellite: 'satellite',
  terrain: 'terrain',
};

export interface EngineWire {
  engine: MapEngine;
  requested: MapEngine;
  engines: MapEngine[];
  google_available: boolean;
}

export interface TileSession {
  type: TileType;
  tile_url: string;
  viewport_url: string;
  tile_size: number;
  image_format: string;
  /** Unix seconds. */
  expiry: number;
  max_zoom: number;
}

/**
 * What this account may draw with.
 *
 * A refusal is not an error here: an account without the Google engine
 * simply gets the free one, and a panel that could not ask at all should
 * behave like an account that has only the free one rather than showing
 * a picker that cannot deliver.
 */
export async function readEngine(): Promise<EngineWire> {
  try {
    return await apiJSON<EngineWire>('/map/engine');
  } catch {
    return { engine: 'osm', requested: 'osm', engines: ['osm'], google_available: false };
  }
}

/** Re-ask this many seconds before the template stops working, so a tile
 *  is never requested with a session that expired between the check and
 *  the fetch. */
const RENEW_MARGIN_S = 60;

const live = new Map<TileType, TileSession>();

export function sessionIsUsable(s: TileSession | undefined, nowMs = Date.now()): boolean {
  return !!s && s.expiry * 1000 - nowMs > RENEW_MARGIN_S * 1000;
}

/**
 * A usable session for one tile type, opening one only when the held one
 * is gone or nearly so.  Returns null when Google cannot be reached — the
 * caller draws the free layer, which is the same answer the dashboard
 * gives when a quota is spent.
 */
export async function tileSession(type: TileType): Promise<TileSession | null> {
  const held = live.get(type);
  if (sessionIsUsable(held)) return held!;
  try {
    const s = await apiJSON<TileSession>(
      `/map/tiles/session?type=${encodeURIComponent(type)}`);
    live.set(type, s);
    return s;
  } catch {
    return null;
  }
}

/** Dropped on disconnect: a session belongs to the token that opened it. */
export function forgetSessions(): void {
  live.clear();
}

/**
 * Whose basemap the panel draws, and the session Google needs to draw it.
 *
 * The dashboard has had this since the Google engine landed; the panel
 * had one hardcoded layer.  The owner asked for the two to match, and
 * they now call the same two endpoints — `/map/engine` for what this
 * account is allowed to use, `/map/tiles/session` for the per-session
 * tile template Google hands out.  Both ride `can_view_live_map`, which
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
  /** Straight to Google, key in the query.  The PANEL MUST NOT USE
   *  THIS.  The key is HTTP-referrer restricted and a
   *  `chrome-extension://` page sends no Referer at all, so Google
   *  answers every one of these `403 Requests from referer <empty> are
   *  blocked` — a grey rectangle with a working session behind it.
   *  Kept in the type because the dashboard, whose pages ARE on an
   *  allowed origin, uses it and pays no proxy bandwidth for the
   *  privilege. */
  tile_url: string;
  viewport_url: string;
  /** Through our API, which holds the key and sends the referer the
   *  restriction wants.  This is the panel's path. */
  proxy_tile_url: string;
  proxy_copyright_url: string;
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

/**
 * Change which engine THIS ACCOUNT is drawn on.
 *
 * An account-wide write, and deliberately so: the dashboard has no
 * per-device basemap choice at all — the account's engine IS its map —
 * and Google's tiles are billable, so "which map we buy" is one truth
 * for everyone who looks, not a per-browser taste.  The server gates it
 * on `can_manage_config_all`; the panel hides the control without that
 * ability rather than offering it and answering 403 on the press.
 *
 * Held sessions are dropped on success: they were opened against the
 * old answer, and one opened while the account was on Google is worth
 * nothing the moment it is not.
 */
export async function setAccountEngine(engine: MapEngine): Promise<EngineWire> {
  const out = await apiJSON<EngineWire>('/map/config', { method: 'PUT', body: { engine } });
  forgetSessions();
  return out;
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

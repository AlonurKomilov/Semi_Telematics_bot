/**
 * The two rules a basemap swap has to keep.
 *
 * jsdom draws no map, so these hold the parts that are pure: which
 * session a type asks for, when a held one is still good, and what the
 * panel falls back to when Google cannot answer.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { GOOGLE_TYPE, sessionIsUsable, tileSession, forgetSessions } from './engine';
import { applyLabels, type BaseState } from './basemap';
import { MAP_TYPES, MAP_ENGINES, MAP_TYPE_LABEL, TILES } from './tiles';

vi.mock('../../api/client', () => ({ apiJSON: vi.fn() }));
const { apiJSON } = await import('../../api/client');
const mocked = apiJSON as unknown as ReturnType<typeof vi.fn>;

// `as const` and not a bare string: `TileSession.type` is the union,
// so an inferred `string` here is the one thing in this file tsc
// rejects — and it did, while the suite stayed green.
const session = (expiry: number) => ({
  type: 'roadmap' as const, tile_url: 'https://t/{z}/{x}/{y}', viewport_url: 'https://v',
  tile_size: 256, image_format: 'png', expiry, max_zoom: 22,
});

beforeEach(() => { forgetSessions(); mocked.mockReset(); });

describe('the panel asks Google in Google’s words', () => {
  it('maps our three names onto theirs', () => {
    expect(GOOGLE_TYPE.standard).toBe('roadmap');
    expect(GOOGLE_TYPE.satellite).toBe('satellite');
    expect(GOOGLE_TYPE.terrain).toBe('terrain');
  });

  it('every type the picker offers has a word and a tile source', () => {
    // The picker renders MAP_TYPES; a member with no label or no layer
    // would be a button that draws nothing.
    for (const t of MAP_TYPES) {
      expect(MAP_TYPE_LABEL[t], t).toBeTruthy();
      expect(TILES[t]?.url, t).toContain('http');
      expect(GOOGLE_TYPE[t], t).toBeTruthy();
    }
    expect([...MAP_ENGINES]).toEqual(['osm', 'google']);
  });
});

describe('a session is re-asked for before it expires, not after', () => {
  it('holds one that has time left', async () => {
    mocked.mockResolvedValue(session(Math.floor(Date.now() / 1000) + 3600));
    await tileSession('roadmap');
    await tileSession('roadmap');
    expect(mocked).toHaveBeenCalledTimes(1);
    expect(mocked).toHaveBeenCalledWith('/map/tiles/session?type=roadmap');
  });

  it('re-opens one inside the renewal margin', async () => {
    // The margin exists so a tile is never requested with a template that
    // expired between the check and the fetch.
    expect(sessionIsUsable(session(Math.floor(Date.now() / 1000) + 10))).toBe(false);
    expect(sessionIsUsable(session(Math.floor(Date.now() / 1000) + 3600))).toBe(true);
    expect(sessionIsUsable(undefined)).toBe(false);
  });

  it('asks once per type, because a session is per type', async () => {
    mocked.mockResolvedValue(session(Math.floor(Date.now() / 1000) + 3600));
    await tileSession('roadmap');
    await tileSession('satellite');
    expect(mocked).toHaveBeenCalledTimes(2);
  });
});

describe('Google refusing is an answer, not a blank map', () => {
  it('returns null so the caller draws the free layer', async () => {
    // A spent quota, an account without the engine, no network — all
    // "cannot draw Google right now", all answered the same way.
    mocked.mockRejectedValue(new Error('503'));
    expect(await tileSession('roadmap')).toBeNull();
  });

  it('does not cache the refusal', async () => {
    mocked.mockRejectedValueOnce(new Error('503'));
    await tileSession('roadmap');
    mocked.mockResolvedValueOnce(session(Math.floor(Date.now() / 1000) + 3600));
    expect(await tileSession('roadmap')).not.toBeNull();
  });
});


describe('road names go over the maps that lack them, and no others', () => {
  /** Enough of Leaflet for the one thing this decides: whether a layer
   *  was built and added at all. */
  function rig() {
    const added: unknown[] = [];
    const removed: unknown[] = [];
    const Leaf = {
      tileLayer: (url: string, opts: Record<string, unknown>) => ({
        url, opts,
        addTo: (_m: unknown) => { added.push(url); return { url, opts, remove: () => removed.push(url) }; },
        remove: () => removed.push(url),
      }),
    } as unknown as typeof import('leaflet');
    const state: BaseState = { layer: null, labels: null, seq: 0 };
    return { Leaf, state, added, removed, map: {} as never };
  }

  it('draws nothing over Standard — those tiles carry their own names', () => {
    const { Leaf, state, added, map } = rig();
    applyLabels(map, Leaf, state, 'standard', true);
    expect(added).toHaveLength(0);
    expect(state.labels).toBeNull();
  });

  it('draws them over Satellite and Terrain', () => {
    for (const type of ['satellite', 'terrain'] as const) {
      const { Leaf, state, added, map } = rig();
      applyLabels(map, Leaf, state, type, true);
      expect(added, type).toHaveLength(1);
      expect(state.labels, type).not.toBeNull();
    }
  });

  it('takes the old one off before putting a new one on', () => {
    // Two presses in a row would otherwise stack two identical label
    // layers, and only the top one would ever come off again.
    const { Leaf, state, map, removed } = rig();
    applyLabels(map, Leaf, state, 'satellite', true);
    applyLabels(map, Leaf, state, 'satellite', true);
    expect(removed).toHaveLength(1);
  });

  it('switching them off leaves nothing behind', () => {
    const { Leaf, state, map, removed } = rig();
    applyLabels(map, Leaf, state, 'satellite', true);
    applyLabels(map, Leaf, state, 'satellite', false);
    expect(removed).toHaveLength(1);
    expect(state.labels).toBeNull();
  });

  it('sits in the pane between the tiles and the trucks', () => {
    // `shadowPane` is the one built-in pane above the base tiles and
    // below every marker.  Without it a city name can cover a truck.
    const { Leaf, state, map } = rig();
    applyLabels(map, Leaf, state, 'satellite', true);
    expect((state.labels as unknown as { opts: Record<string, unknown> }).opts.pane)
      .toBe('shadowPane');
  });
});

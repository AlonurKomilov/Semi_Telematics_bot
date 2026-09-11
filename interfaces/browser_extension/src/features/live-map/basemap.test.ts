/**
 * The two rules a basemap swap has to keep.
 *
 * jsdom draws no map, so these hold the parts that are pure: which
 * session a type asks for, when a held one is still good, and what the
 * panel falls back to when Google cannot answer.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { GOOGLE_TYPE, sessionIsUsable, tileSession, forgetSessions } from './engine';
import { MAP_TYPES, MAP_ENGINES, MAP_TYPE_LABEL, TILES } from './tiles';

vi.mock('../../api/client', () => ({ apiJSON: vi.fn() }));
const { apiJSON } = await import('../../api/client');
const mocked = apiJSON as unknown as ReturnType<typeof vi.fn>;

const session = (expiry: number) => ({
  type: 'roadmap', tile_url: 'https://t/{z}/{x}/{y}', viewport_url: 'https://v',
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

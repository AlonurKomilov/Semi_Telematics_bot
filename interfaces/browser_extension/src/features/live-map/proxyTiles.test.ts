/**
 * The panel must never ask Google for a tile directly.
 *
 * It did, for two versions, and the symptom was a grey rectangle with
 * a working session, a drawn attribution and every truck in place —
 * because the failure is invisible from the code: the key is
 * HTTP-referrer restricted, a `chrome-extension://` page sends no
 * Referer at all, and Google answers `403 Requests from referer
 * <empty> are blocked`.  Nothing in the panel is wrong; the request
 * simply cannot succeed from here.
 */
import { describe, expect, it } from 'vitest';

import { tilePath } from './proxyTiles';
import basemapSrc from './basemap.ts?raw';

const basemap = basemapSrc as unknown as string;

/** Comments explain the rule; they are not the rule. */
function code(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
}

describe('the template the server writes is filled here', () => {
  it('substitutes every coordinate', () => {
    expect(tilePath('/map/tile?type=roadmap&z={z}&x={x}&y={y}', 4, 3, 5))
      .toBe('/map/tile?type=roadmap&z=4&x=3&y=5');
  });

  it('fills a placeholder that appears twice', () => {
    expect(tilePath('{z}/{x}/{y}/{z}', 1, 2, 3)).toBe('1/2/3/1');
  });

  it('leaves a placeholder it has no value for alone rather than throwing', () => {
    // The template arrives over the wire.  A server that grows a `{r}`
    // for retina must not take the map down.
    expect(() => tilePath('{z}/{x}/{y}/{r}', 1, 2, 3)).not.toThrow();
    expect(tilePath('{z}/{x}/{y}/{r}', 1, 2, 3)).toBe('1/2/3/{r}');
  });

  it('handles zero, which is a real tile coordinate', () => {
    expect(tilePath('{z}/{x}/{y}', 0, 0, 0)).toBe('0/0/0');
  });
});

describe('the basemap asks us, not Google', () => {
  it('draws Google through the proxy template', () => {
    const src = code(basemap);
    expect(src).toContain('proxy_tile_url');
    expect(src).toContain('proxyTileLayer(');
  });

  it('never reaches for the direct template', () => {
    // `sess.tile_url` carries the key straight to Google and is the
    // one thing that cannot work from an extension page.
    expect(code(basemap)).not.toContain('sess.tile_url');
    expect(code(basemap)).not.toContain('sess.viewport_url');
  });
});

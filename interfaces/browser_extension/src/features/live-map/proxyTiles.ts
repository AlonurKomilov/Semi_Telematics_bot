/**
 * Google's tiles, fetched through our own API instead of directly.
 *
 * WHY THIS FILE EXISTS, in one paragraph, because it looks like
 * unnecessary indirection until you know: the platform's Google key is
 * protected by an HTTP-referrer restriction rather than by secrecy —
 * every tile request carries the key, so the restriction is the only
 * thing standing between it and anyone who opens a network tab.  A
 * page that is not on an allowed origin sends NO `Referer` header at
 * all, and Google then answers every tile `403 Requests from referer
 * <empty> are blocked`.  A browser-extension page is exactly that:
 * Chrome never sends a `chrome-extension://` origin to an https host,
 * whatever referrer policy the request asks for.  So the panel opened
 * a valid session, drew Google's attribution, and showed a grey
 * rectangle.
 *
 * Teaching the extension to FORGE the header was the other way out.
 * It would have meant shipping a header-rewriting rule to every
 * install next to the key, which together are an unrestricted key for
 * anybody who reads both.  The key stays on the server.
 *
 * The cost is our bandwidth, and it is paid only by clients that
 * cannot carry a referer.  Google's tile count is identical either
 * way.
 */
import type L from 'leaflet';

import { apiFetch } from '../../api/client';

/** Where the blob URL is parked so `tileunload` can revoke it.  On the
 *  element, not in a map: Leaflet owns the element's lifetime and will
 *  tell us when it is done with it. */
const BLOB_ATTR = 'data-blob-url';

/**
 * Fill `{z}`, `{x}` and `{y}` in a template the SERVER wrote.
 *
 * Leaflet's own `Util.template` would do this, but it throws on a
 * placeholder it has no value for — and this template arrives over the
 * wire, so a server that grows a `{r}` for retina would take the map
 * down rather than draw it slightly wrong.
 */
export function tilePath(template: string, z: number, x: number, y: number): string {
  return template
    .replace(/\{z\}/g, String(z))
    .replace(/\{x\}/g, String(x))
    .replace(/\{y\}/g, String(y));
}

/**
 * A tile layer that asks our API for each tile and paints the bytes.
 *
 * `<img src>` cannot carry an Authorization header, which is why the
 * bytes are fetched and handed over as a blob rather than pointed at:
 * the alternative is a signed URL scheme, and a token in an image URL
 * is a token in every log and referrer along the way.
 */
export function proxyTileLayer(
  Leaf: typeof L, template: string, opts: L.TileLayerOptions,
): L.TileLayer {
  const Proxy = Leaf.TileLayer.extend({
    createTile(this: L.TileLayer, coords: { x: number; y: number; z: number },
               done: (err: Error | undefined, tile: HTMLElement) => void) {
      const img = document.createElement('img');
      // Decorative: the map beneath the markers is not information a
      // screen reader can use, and an empty alt keeps it out of the way.
      img.alt = '';
      void apiFetch(tilePath(template, coords.z, coords.x, coords.y))
        .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((b) => {
          const url = URL.createObjectURL(b);
          img.setAttribute(BLOB_ATTR, url);
          img.src = url;
          done(undefined, img);
        })
        .catch((e: unknown) => {
          // Leaflet keeps the element and marks it errored, which is
          // what draws the blank square rather than a broken image.
          done(e instanceof Error ? e : new Error('tile failed'), img);
        });
      return img;
    },
  });
  const layer = new (Proxy as unknown as new (u: string, o: L.TileLayerOptions) => L.TileLayer)(
    template, opts,
  );
  // Every blob is revoked when Leaflet drops the tile.  Without this a
  // long pan leaks one object URL per tile, and the leak is invisible
  // until the tab is minutes old — which is most of a panel's life.
  layer.on('tileunload', (e: L.LeafletEvent) => {
    const el = (e as unknown as { tile?: HTMLElement }).tile;
    const url = el?.getAttribute(BLOB_ATTR);
    if (url) { URL.revokeObjectURL(url); el?.removeAttribute(BLOB_ATTR); }
  });
  return layer;
}

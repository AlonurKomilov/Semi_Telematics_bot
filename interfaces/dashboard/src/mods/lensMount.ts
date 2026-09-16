/**
 * Putting a bevel on the screen: the filter, and who gets it.
 *
 * `lens.ts` draws the map and knows nothing about the DOM. This mounts
 * one `<filter>` per distinct pane SHAPE and hands each surface the name
 * of its own, as a custom property.
 *
 * THE STYLESHEET STILL DECIDES, which is the point of doing it this way
 * rather than the obvious way. The obvious way is for this file to
 * select the translucent surfaces and filter those — and that means
 * copying `glass.css`'s selector into TypeScript, where it silently
 * stops matching the day either side changes. Instead every `.surface`
 * is handed `--surface-lens` and the stylesheet spends it or ignores it:
 * the glass rule reads it, and the escape hatch that makes floating
 * surfaces opaque sets `backdrop-filter: none`, which throws the whole
 * filter away including this. So a popover can never be lensed even if
 * this file offers it one, and the rule about what glass IS stays in
 * one place.
 *
 * WHY A FILTER PER SHAPE. The bevel's band is measured in surface
 * pixels — a chip and a dialog get the same thickness of glass, not a
 * thickness proportional to their size — so the map depends on the pane
 * it is for. Sizes are bucketed before that becomes a filter per
 * element: a 3px difference in width moves the band by a fraction of a
 * map column, and nobody can see it.
 */
import { bevelMap, type Bevel, type LensMap } from './lens';

/**
 * How coarsely sizes are rounded before they become a filter.
 *
 * A pane's exact width changes with every list that grows a scrollbar,
 * so minting a filter per pixel would mint one per interaction. 24px is
 * the 4px spacing scale six steps up, the same step `grid` and `gauge`
 * draw on; at a 30px band it moves the bevel by under a map column,
 * which is the resolution the map has anyway.
 */
export const BUCKET = 24;

/** The custom property a surface reads its own filter out of. Unset is
 *  a valid state and means "no bevel" — `glass.css` falls back to an
 *  empty value, so the rest of the filter chain still applies. */
export const LENS_VAR = '--surface-lens';

/** Where the filters live. One node, created on demand, never emptied
 *  while the page is up: a filter that has been used once is likely to
 *  be used again the moment anything re-renders at the same size. */
const HOST_ID = 'mods-lens-filters';

/** Round UP, never nearest: rounding down makes the last bucket
 *  narrower than the pane it serves, and the bevel then starts inside
 *  the edge rather than on it. */
export const bucketed = (px: number): number =>
  Math.max(BUCKET, Math.ceil(px / BUCKET) * BUCKET);

/** The name of the filter for one pane shape. Shape, not element —
 *  every pane of these dimensions shares it. */
export const lensFilterId = (w: number, h: number, r: number): string =>
  `lens-${w}x${h}r${Math.round(r)}`;

/** Turns a map into something `feImage` can load. Injected rather than
 *  called directly so the mounting can be tested without a canvas —
 *  jsdom has no 2D context, and a module that cannot be tested off a
 *  browser gets tested in nobody's browser. */
export type Encoder = (map: LensMap) => string;

/** The real one: a canvas, which is also the only thing here that
 *  produces a COMPRESSED png. The map is a few hundred bytes that way
 *  and several kilobytes any other. */
export function canvasEncoder(doc: Document): Encoder {
  return (map) => {
    const c = doc.createElement('canvas');
    c.width = map.width; c.height = map.height;
    const ctx = c.getContext('2d');
    if (!ctx) return '';
    const img = ctx.createImageData(map.width, map.height);
    img.data.set(map.data);
    ctx.putImageData(img, 0, 0);
    return c.toDataURL('image/png');
  };
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function host(doc: Document): SVGSVGElement {
  const found = doc.getElementById(HOST_ID);
  if (found) return found as unknown as SVGSVGElement;
  const svg = doc.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('id', HOST_ID);
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.setAttribute('aria-hidden', 'true');
  // Out of flow and out of the way: an `<svg>` in normal flow is an
  // inline box, and a zero-sized inline box still takes a line's worth
  // of leading from whatever it lands in.
  svg.setAttribute('style', 'position:absolute;width:0;height:0;overflow:hidden');
  doc.body.appendChild(svg);
  return svg as SVGSVGElement;
}

/**
 * Make sure the filter for this shape exists, and answer its id.
 *
 * `preserveAspectRatio="none"` is what lets one small map cover a pane
 * of any proportion — the map is 32×40 whatever the surface is, and
 * stretching it is the whole reason the feature is affordable. The
 * filter REGION is in user-space units so it lands exactly on the
 * border box; a percentage region would grow with the element and take
 * the bevel with it.
 */
export function ensureFilter(
  doc: Document, w: number, h: number, r: number, bevel: Bevel, encode: Encoder,
): string {
  const id = lensFilterId(w, h, r);
  if (doc.getElementById(id)) return id;

  const href = encode(bevelMap(w, h, r, bevel));
  if (!href) return '';

  const f = doc.createElementNS(SVG_NS, 'filter');
  f.setAttribute('id', id);
  f.setAttribute('x', '0'); f.setAttribute('y', '0');
  f.setAttribute('width', String(w)); f.setAttribute('height', String(h));
  f.setAttribute('filterUnits', 'userSpaceOnUse');

  const img = doc.createElementNS(SVG_NS, 'feImage');
  img.setAttribute('href', href);
  img.setAttribute('preserveAspectRatio', 'none');
  img.setAttribute('x', '0'); img.setAttribute('y', '0');
  img.setAttribute('width', String(w)); img.setAttribute('height', String(h));
  img.setAttribute('result', 'bevel');

  const disp = doc.createElementNS(SVG_NS, 'feDisplacementMap');
  disp.setAttribute('in', 'SourceGraphic');
  disp.setAttribute('in2', 'bevel');
  // A channel runs 0..1 and displaces by `scale * (c - 0.5)`, so the
  // peak is half the scale. The pack says how far the rim bends in
  // pixels; this is that, in the units the filter wants.
  disp.setAttribute('scale', String(bevel.strength * 2));
  disp.setAttribute('xChannelSelector', 'R');
  disp.setAttribute('yChannelSelector', 'G');

  f.appendChild(img); f.appendChild(disp);
  host(doc).appendChild(f);
  return id;
}

/** The radius a pane is actually drawn with, in px. Read off the
 *  element rather than off `--radius`, because a call site may have
 *  overridden it and the bevel has to follow the corner that paints. */
export function radiusOf(el: Element, view: Window): number {
  const raw = view.getComputedStyle(el).borderTopLeftRadius;
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Give one surface the filter for its current shape.
 *
 * A pane with no area gets nothing — an element inside a collapsed
 * panel measures 0×0, and a filter region of zero is one the browser
 * has to reject rather than skip.
 */
export function applyLens(
  el: HTMLElement, w: number, h: number, r: number, bevel: Bevel,
  doc: Document, encode: Encoder,
): void {
  if (w < 1 || h < 1) { el.style.removeProperty(LENS_VAR); return; }
  const id = ensureFilter(doc, bucketed(w), bucketed(h), r, bevel, encode);
  if (!id) { el.style.removeProperty(LENS_VAR); return; }
  el.style.setProperty(LENS_VAR, `url(#${id})`);
}

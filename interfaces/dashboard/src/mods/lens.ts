/**
 * The bevel a pane of glass has at its edge, as a displacement map.
 *
 * WHAT THIS IS FOR. `backdrop-filter` can bend what is behind a surface
 * — `feDisplacementMap` moves each pixel by the value of a second image
 * — but the filter cannot see the ELEMENT. For a backdrop filter the
 * source alpha is opaque across the whole region, so nothing inside the
 * filter knows where the edge is. Two ways of deriving the shape were
 * tried and both measured flat: a difference of gaussians on
 * `SourceAlpha`, and `feMorphology` erosion. The shape has to be handed
 * in, and this is the thing that draws it.
 *
 * WHY IT IS TINY, which is the whole reason the feature is affordable.
 * The obvious map is one pixel per surface pixel, and that is what the
 * first version drew: 236×300 for a 236×300 pane. Measured over sixteen
 * surfaces on a moving backdrop, that costs +53.4 ms per frame against
 * a +15.0 ms baseline for the same filter chain without it — three and
 * a half times the price of the whole rest of the material. An 8×8 map
 * stretched to the same pane costs +16.4 ms, which is the baseline
 * again: the price is the map's RESOLUTION, not the displacement.
 *
 * And the effect does not care. Measured at four resolutions on the same
 * pane, rim displacement came out 10.5 / 11.2 / 10.2 / 12.1 px and the
 * centre came out 0.00 px in every one — because a bevel is a smooth
 * ramp, and a smooth ramp is exactly the thing that survives being
 * sampled coarsely and stretched back. So the map is drawn small on
 * purpose, and `RESOLUTION` below is a performance decision with a
 * measurement behind it rather than a default nobody questioned.
 */

/** The shape of the edge, in CSS pixels of the surface it describes. */
export interface Bevel {
  /**
   * How far in from the edge the bend reaches. Real glass bends light
   * in the few millimetres where it curves; past that it is a flat
   * pane and light goes straight through.
   */
  readonly band: number;
  /**
   * How sharply the bend dies away across `band`. 1 is linear; higher
   * pulls the effect tighter to the very edge, which is what a rounded
   * bevel actually does.
   */
  readonly falloff: number;
  /**
   * How far the rim bends what is behind it, in pixels, at the very
   * edge. A pane of glass is not a prism: measured against the shipped
   * value the displacement is about a dozen pixels at the rim and
   * exactly none a third of the way in.
   *
   * Carried here rather than derived from `band` because they are two
   * different statements — how WIDE the bevel is and how STRONGLY it
   * refracts — and a material can want a broad gentle edge or a narrow
   * hard one.
   */
  readonly strength: number;
}

/**
 * The map's own pixel grid, independent of the surface it is stretched
 * over.
 *
 * Small for the reason in the file docstring. Not smaller: a map has to
 * resolve the CORNER, and a corner is where both axes ramp at once —
 * below about 24 a rounded corner starts to read as a chamfer. 32×40
 * keeps the 4:5 bias of a typical panel so the two axes get similar
 * sampling, and the PNG of it is a few hundred bytes.
 */
export const RESOLUTION = { w: 32, h: 40 } as const;

/** The value that means "do not move this pixel". `feDisplacementMap`
 *  reads a channel as `(value/255 - 0.5) * scale`, so the neutral is the
 *  middle of the range and NOT zero — a map of zeroes would shove the
 *  whole backdrop half a scale to one side. */
export const NEUTRAL = 128;

/**
 * WHERE THE GLASS ACTUALLY ENDS, in surface pixels along one side.
 *
 * A lens bends light where the pane stops, and a pane of the frame
 * stops in fewer places than it has sides. Two things are not edges:
 *
 *   · a SEAM. Where two panes of the same sheet meet, the glass does
 *     not end. Bending there draws a line across something with no
 *     boundary — which is what the owner saw at the corner of the rail
 *     and the header.
 *   · the WINDOW'S OWN BOUNDARY. The top of the top bar has nothing
 *     beyond it: there is no backdrop out there to bend, and a
 *     displacement at that edge samples outside the region and smears
 *     whatever the browser clamps in. As he put it, the top bar's edge
 *     is the one facing DOWN, toward the page.
 *
 * And a side is rarely all one thing, which is the part a boolean per
 * side could not say. The rail meets the header for the header's 48px
 * and faces the page for the six hundred below it — read as a boolean,
 * one flush neighbour sealed the whole side, so the rail bent along its
 * OUTER edge and not along the one facing the page. Exactly backwards,
 * and the reason the effect read as weak and misplaced.
 *
 * So a side carries the RANGES of it that are still edges, and the
 * engine is told nothing else: `lensMount` measures them.
 */
export interface OpenSpan {
  readonly side: 'top' | 'right' | 'bottom' | 'left';
  /** Along that side, in surface pixels, from its start. */
  readonly from: number;
  readonly to: number;
}

export interface LensMap {
  readonly width: number;
  readonly height: number;
  /** RGBA, row-major. R carries the x displacement, G the y, and B is
   *  left neutral so a reader looking at the map sees grey where
   *  nothing moves. */
  readonly data: Uint8ClampedArray;
}

/**
 * Draw the bevel for a surface of `w`×`h` with corner radius `r`.
 *
 * The maths is the signed distance to a rounded rectangle: negative
 * outside, positive inside, and its GRADIENT points straight in from
 * the nearest edge — which is the direction a lens has to push. The
 * displacement is that direction, faded by how deep into the pane the
 * pixel is, so the middle of the map comes out exactly neutral and
 * stays there.
 *
 * `w`, `h` and `r` are the SURFACE's dimensions, not the map's. They
 * decide where the band sits relative to the edge; the map is then
 * sampled at `RESOLUTION` and stretched back over the surface by the
 * filter.
 */
export function bevelMap(
  w: number, h: number, r: number, bevel: Bevel, open?: readonly OpenSpan[],
): LensMap {
  // No spans given means the whole outline is an edge, which is what a
  // card is: it sits on the page with nothing against it.
  const whole = open === undefined;
  const { w: MW, h: MH } = RESOLUTION;
  const data = new Uint8ClampedArray(MW * MH * 4);
  // Clamp the radius the way CSS does: a corner cannot be larger than
  // half the box, and a pane narrower than its own radius is a capsule.
  const rad = Math.max(0, Math.min(r, Math.min(w, h) / 2));
  const band = Math.max(1e-6, bevel.band);

  for (let my = 0; my < MH; my++) {
    // Sample at pixel CENTRES: a map stretched from corner to corner
    // would put its outermost row half a surface-pixel outside the
    // pane, and the bevel would start one sample late on every edge.
    const y = ((my + 0.5) / MH) * h;
    for (let mx = 0; mx < MW; mx++) {
      const x = ((mx + 0.5) / MW) * w;

      let depth: number;
      let nx = 0, ny = 0;

      if (whole) {
        // Signed distance to the rounded rectangle, positive inside.
        const qx = Math.abs(x - w / 2) - (w / 2 - rad);
        const qy = Math.abs(y - h / 2) - (h / 2 - rad);
        const outside = Math.hypot(Math.max(qx, 0), Math.max(qy, 0));
        const inside = Math.min(Math.max(qx, qy), 0);
        depth = -(outside + inside);

        // The inward normal, from the gradient of that field. Away from
        // the corners this is a unit vector along one axis; in a corner
        // it is the diagonal, which is what makes the corner bend round
        // rather than fold.
        if (qx > 0 || qy > 0) {
          const ox = Math.max(qx, 0), oy = Math.max(qy, 0);
          const len = Math.hypot(ox, oy) || 1;
          nx = (Math.sign(x - w / 2) * ox) / len;
          ny = (Math.sign(y - h / 2) * oy) / len;
        } else if (qx > qy) {
          nx = Math.sign(x - w / 2);
        } else {
          ny = Math.sign(y - h / 2);
        }
      } else {
        // A PANE THAT ENDS IN PLACES measures to those places only.
        // Distance to a SEGMENT, not to a side: the nearest point on
        // the open stretch, so a pixel past the end of one falls away
        // from it the way it would from a corner.
        //
        // No corner rounding here, and none is missed: a pane with a
        // seam is a strip of the frame, and the frame's sides are
        // square — the radius belongs to the page card they surround.
        let best = Infinity;
        let bx = 0, by = 0;
        for (const span of open!) {
          const lo = Math.min(span.from, span.to), hi = Math.max(span.from, span.to);
          let px: number, py: number;
          if (span.side === 'left') { px = 0; py = Math.min(Math.max(y, lo), hi); }
          else if (span.side === 'right') { px = w; py = Math.min(Math.max(y, lo), hi); }
          else if (span.side === 'top') { px = Math.min(Math.max(x, lo), hi); py = 0; }
          else { px = Math.min(Math.max(x, lo), hi); py = h; }
          const vx = x - px, vy = y - py;
          const d = Math.hypot(vx, vy);
          // Within half a pixel of two stretches at once is a corner of
          // the OPEN outline, and it takes both — the same thing the
          // rounded-rect field does at its own corners.
          if (d < best - 0.5) { best = d; bx = vx; by = vy; }
          else if (d <= best + 0.5) { best = Math.min(best, d); bx += vx; by += vy; }
        }
        depth = best;
        const len = Math.hypot(bx, by);
        // The inward normal is the way back to the glass's end.
        if (len > 0) { nx = -bx / len; ny = -by / len; }
      }

      // Deep inside the pane this is 0, so the centre is exactly
      // neutral and a reader sees straight through it.
      const fade = Math.pow(Math.max(0, 1 - depth / band), bevel.falloff);
      const i = (my * MW + mx) * 4;
      data[i] = NEUTRAL - 127 * nx * fade;
      data[i + 1] = NEUTRAL - 127 * ny * fade;
      data[i + 2] = NEUTRAL;
      data[i + 3] = 255;
    }
  }
  return { width: MW, height: MH, data };
}

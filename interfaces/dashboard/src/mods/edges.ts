/**
 * WHERE A SURFACE ACTUALLY ENDS — the one place that answers it.
 *
 * Every axis that draws anything at a boundary needs the same fact, and
 * until now only the lens had it, buried inside `lensMount` where
 * nothing else could ask. That is the shape of problem the WASH had:
 * a thing with no name, which neither the owner nor I could point at
 * while we spent hours describing its effects.
 *
 * So it has a name and a file. A shadow deciding which way to fall, a
 * border asking which side is real, a rim placing a highlight — each of
 * them is asking this question, and each of them should get the same
 * answer as the lens rather than working one out privately.
 *
 * MEASURED, NOT DECLARED, and that is the one decision worth defending
 * because the obvious alternative is to have each component state its
 * own sides. It cannot. Two facts settled it:
 *
 *   · A side is rarely all one thing. The rail meets the header along
 *     the top 48px of its right side and faces the page for the six
 *     hundred below. Nobody declares that correctly — the first attempt
 *     here used one boolean per side and came out exactly backwards,
 *     bending the rail against the window and leaving it flat where it
 *     meets the page.
 *   · It changes while the app runs. Open the assistant and the gutter
 *     that ended the window starts facing a second page instead. A
 *     declaration is stale from that moment, and silently.
 *
 * The room left for declaring is the one the layout cannot see, and the
 * app already has that shape in `.surface-opaque`: measure by default,
 * and let a call site that knows better say so, with its reason.
 *
 * ONE READING PER BATCH. `readEdges` walks the surfaces once and hands
 * back something every caller shares, so a second axis costs nothing.
 * The cost that matters is not measuring — it is measuring twice.
 */

/** Every surface a person's material can reach, and so every surface
 *  that can have an edge. One spelling, because two would drift. */
export const SURFACES = '.surface';

/**
 * How close two panes have to be before the gap between them stops
 * being a gap.
 *
 * A fraction of a pixel of rounding, not a design tolerance: flush
 * sides are laid out flush, and what varies is subpixel layout and the
 * Size multiplier's rounding. Anything wider is a real gap, and a real
 * gap means the glass really does end there.
 */
export const SEAM = 1.5;

/**
 * A stretch of one side that is still an edge, as a FRACTION of that
 * side — 0 at its start, 1 at its end. A side can have none, one, or
 * several.
 *
 * Fractions rather than pixels, and the reason is the bucketing. A
 * filter is drawn for a size ROUNDED UP to the nearest 24px so panes of
 * a similar size can share one, and spans measured in pixels then land
 * in a different coordinate system from the map that uses them: a
 * full-width top side read as `0-968` on a map drawn 984 wide left the
 * last sixteen pixels of it treated as sealed. A fraction means the
 * same thing in both, and it survives any rounding either of them does.
 */
export interface OpenSpan {
  readonly side: 'top' | 'right' | 'bottom' | 'left';
  readonly from: number;
  readonly to: number;
}

/** What `readEdges` needs of the window. A plain shape rather than
 *  `Window`, so a caller can measure against something else — a test,
 *  or one day a pane inside its own scroller. */
export interface Viewport {
  readonly innerWidth: number;
  readonly innerHeight: number;
}

/** What is left of `[0, len]` once the closed stretches are taken out.
 *  Anything shorter than a seam is not a stretch of edge. */
function remaining(len: number, closed: [number, number][]): [number, number][] {
  const out: [number, number][] = [];
  let at = 0;
  for (const [a, b] of [...closed].sort((p, q) => p[0] - q[0])) {
    if (a > at + SEAM) out.push([at, Math.min(a, len)]);
    at = Math.max(at, b);
    if (at >= len) break;
  }
  if (at < len - SEAM) out.push([at, len]);
  return out.filter(([a, b]) => b - a > SEAM);
}

/**
 * Where this box's glass ends, given what is around it.
 *
 * Two things are not edges. A side with another pane flush against it
 * is a SEAM for exactly the stretch they share — where two panes of one
 * sheet meet, the glass does not end. And a side lying on the window's
 * own boundary has nothing beyond it: nothing out there to bend, and a
 * displacement at that edge samples outside the region and smears back
 * whatever the browser clamps in.
 */
export function spansFor(
  rect: DOMRect, others: readonly DOMRect[], view: Viewport,
): OpenSpan[] {
  const out: OpenSpan[] = [];
  const closed: Record<OpenSpan['side'], [number, number][]> = {
    top: [], right: [], bottom: [], left: [],
  };

  for (const o of others) {
    const overY: [number, number] = [
      Math.max(o.top, rect.top) - rect.top, Math.min(o.bottom, rect.bottom) - rect.top];
    const overX: [number, number] = [
      Math.max(o.left, rect.left) - rect.left, Math.min(o.right, rect.right) - rect.left];
    if (Math.abs(o.right - rect.left) <= SEAM && overY[1] - overY[0] > SEAM) closed.left.push(overY);
    if (Math.abs(o.left - rect.right) <= SEAM && overY[1] - overY[0] > SEAM) closed.right.push(overY);
    if (Math.abs(o.bottom - rect.top) <= SEAM && overX[1] - overX[0] > SEAM) closed.top.push(overX);
    if (Math.abs(o.top - rect.bottom) <= SEAM && overX[1] - overX[0] > SEAM) closed.bottom.push(overX);
  }

  const add = (side: OpenSpan['side'], len: number, onWindow: boolean) => {
    if (onWindow || len <= 0) return;
    for (const [from, to] of remaining(len, closed[side]))
      out.push({ side, from: from / len, to: to / len });
  };
  add('left', rect.height, rect.left <= SEAM);
  add('right', rect.height, rect.right >= view.innerWidth - SEAM);
  add('top', rect.width, rect.top <= SEAM);
  add('bottom', rect.width, rect.bottom >= view.innerHeight - SEAM);
  return out;
}

/** One reading of the whole layout, shared by every axis that asks. */
export interface EdgeReading {
  /**
   * This element's open stretches, or `undefined` when it has no
   * measurable box.
   *
   * The distinction matters and is not the same as an empty list. An
   * empty list means MEASURED, and nothing about it is an edge — a
   * strip boxed in on all four sides, which should get no boundary
   * treatment at all. `undefined` means the question could not be
   * asked: not laid out yet, or an environment that does no layout.
   * Inventing seams out of zeroes would silently strip the treatment
   * from everything, so an unmeasured surface is read as a lone pane,
   * which is what it was before any of this existed.
   */
  of(el: Element): OpenSpan[] | undefined;
}

/**
 * Measure every surface once.
 *
 * Reads only — no write touches the DOM here — so a caller can fold
 * this into the read half of its own batch and pay for a single layout.
 */
export function readEdges(doc: Document, view: Viewport): EdgeReading {
  const nodes = [...doc.querySelectorAll(SURFACES)];
  const rects = nodes.map((n) => n.getBoundingClientRect());
  const spans = new Map<Element, OpenSpan[] | undefined>();

  nodes.forEach((el, i) => {
    const rect = rects[i];
    if (rect.width <= 0 || rect.height <= 0) { spans.set(el, undefined); return; }
    // Its own box is in the list; a pane is not its own neighbour.
    spans.set(el, spansFor(rect, rects.filter((_, j) => j !== i), view));
  });

  return { of: (el) => (spans.has(el) ? spans.get(el) : undefined) };
}

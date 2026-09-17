/**
 * The bevel map, held to the three things that make it a bevel.
 *
 * These are not style assertions. Each one is a property the rendered
 * effect was MEASURED to have, and each has a way of being wrong that
 * looks fine in a screenshot:
 *
 *   · the centre is neutral — otherwise the whole backdrop shifts, and
 *     a uniform shift of a flat ground is invisible until something
 *     with an edge sits behind it
 *   · the edge pushes OUTWARD along the nearest normal — the sign is a
 *     coin flip that turns a lens into a pinch, and both bend
 *   · the map is small — the reason the feature is affordable at all,
 *     and the single easiest thing for a later edit to undo
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { bevelMap, RESOLUTION, NEUTRAL, type Bevel } from './lens';
import type { OpenSpan } from './edges';
import { MATERIAL_PACKS, materialPackById } from './store/items/material';

const BEVEL: Bevel = { band: 30, falloff: 2.2, strength: 13 };
const W = 236, H = 300, R = 28;

/** One pixel's displacement, in map units away from neutral. */
function at(m: ReturnType<typeof bevelMap>, mx: number, my: number) {
  const i = (my * m.width + mx) * 4;
  return { x: m.data[i] - NEUTRAL, y: m.data[i + 1] - NEUTRAL, b: m.data[i + 2] };
}

describe('the bevel map', () => {
  const map = bevelMap(W, H, R, BEVEL);

  it('is small, and that is the performance decision', () => {
    // Measured over 16 surfaces on a moving backdrop: a full-resolution
    // map cost +53.4ms per frame against +15.0ms for the same filter
    // chain without it, and a tiny one cost +16.4ms — the baseline
    // again. Raising this silently is how the feature stops being
    // affordable, so the number is pinned rather than merely small.
    expect(RESOLUTION.w).toBe(32);
    expect(RESOLUTION.h).toBe(40);
    expect(map.width).toBe(32);
    expect(map.height).toBe(40);
    expect(map.data.length).toBe(32 * 40 * 4);
  });

  it('leaves the middle exactly alone', () => {
    // Not "close to neutral" — exactly. The band is 30px into a 300px
    // pane, so everything in the middle fifth is past it.
    for (const my of [18, 20, 22])
      for (const mx of [13, 16, 19]) {
        const p = at(map, mx, my);
        expect(p.x, `x moved at ${mx},${my}`).toBe(0);
        expect(p.y, `y moved at ${mx},${my}`).toBe(0);
      }
  });

  it('pushes outward at every edge, along that edge normal', () => {
    const mid = { x: RESOLUTION.w >> 1, y: RESOLUTION.h >> 1 };
    // Left edge moves in -x and not in y; right in +x. Top and bottom
    // are the same statement rotated. `NEUTRAL - 127*n*fade` means a
    // normal pointing LEFT (-1) raises the channel, so the left column
    // must read above neutral.
    const left = at(map, 0, mid.y), right = at(map, RESOLUTION.w - 1, mid.y);
    expect(left.x).toBeGreaterThan(40);
    expect(right.x).toBeLessThan(-40);
    expect(Math.abs(left.y)).toBeLessThan(6);
    expect(Math.abs(right.y)).toBeLessThan(6);

    const top = at(map, mid.x, 0), bottom = at(map, mid.x, RESOLUTION.h - 1);
    expect(top.y).toBeGreaterThan(40);
    expect(bottom.y).toBeLessThan(-40);
    expect(Math.abs(top.x)).toBeLessThan(6);
    expect(Math.abs(bottom.x)).toBeLessThan(6);
  });

  it('bends a corner on the diagonal, not on one axis', () => {
    // The thing that separates a bevel from four ramps: in a corner
    // both axes move at once, which is why the corner reads as round.
    const c = at(map, 0, 0);
    expect(c.x).toBeGreaterThan(20);
    expect(c.y).toBeGreaterThan(20);
  });

  it('dies away with depth rather than stopping at a line', () => {
    const mid = RESOLUTION.h >> 1;
    const run = [0, 1, 2, 3, 4, 5].map((mx) => Math.abs(at(map, mx, mid).x));
    for (let i = 1; i < run.length; i++)
      expect(run[i], `sample ${i} is not below ${i - 1}`).toBeLessThanOrEqual(run[i - 1]);
    expect(run[0]).toBeGreaterThan(run[run.length - 1]);
  });

  it('keeps blue neutral so the map can be read by eye', () => {
    for (const i of [0, 400, 2000, map.data.length - 2])
      expect(map.data[(i >> 2) * 4 + 2]).toBe(NEUTRAL);
  });

  it('clamps a radius larger than the box, the way CSS does', () => {
    // A pane narrower than twice its radius is a capsule, and the
    // distance field goes imaginary if the radius is taken literally.
    const capsule = bevelMap(40, 200, 999, BEVEL);
    expect([...capsule.data].every((v) => Number.isFinite(v))).toBe(true);
    const mid = at(capsule, RESOLUTION.w >> 1, RESOLUTION.h >> 1);
    expect(Number.isNaN(mid.x)).toBe(false);
  });

  it('puts the band in SURFACE pixels, not map pixels', () => {
    // The map is one fixed grid whatever the surface is, so the band
    // has to be measured against the surface — otherwise a wide panel
    // gets a wide bevel, which is the bug the first attempt shipped
    // (one map stretched across three widths, the band stretching with
    // it). A 30px band is a fifth of a 150px pane and a twentieth of a
    // 600px one, so the same band reaches FEWER map columns on the
    // wider surface.
    const narrow = bevelMap(150, 300, R, BEVEL);
    const wide = bevelMap(600, 300, R, BEVEL);
    const mid = RESOLUTION.h >> 1;
    const reach = (m: ReturnType<typeof bevelMap>) =>
      [...Array(RESOLUTION.w).keys()].filter((mx) => Math.abs(at(m, mx, mid).x) > 2).length;
    expect(reach(wide), 'the wide pane got as much bevel as the narrow one')
      .toBeLessThan(reach(narrow));
  });
});

/**
 * THE SHAPE IS THE ENGINE'S, THE NUMBERS ARE THE PACK'S — the same line
 * the depth ladder is held to, and for the same reason: a material's
 * thickness is what that material IS, so a band typed into the drawing
 * code would be one material's taste compiled into the machine that
 * serves all of them.
 *
 * Source-level, because an output test cannot see this. A bevel moved
 * back into `lens.ts` would draw exactly the same map, so nothing about
 * the rendered result would change — which is what makes the line worth
 * a guard rather than a comment.
 */
describe('the bevel is a pack value, not an engine one', () => {
  /** Comments blanked in place, or this file's own prose about `band`
   *  counts as a violation of the rule it documents. */
  const codeOnly = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
       .replace(/\/\/[^\n]*/g, (m) => m.replace(/[^\n]/g, ' '));

  it('the engine states no bevel of its own', () => {
    const src = codeOnly(readFileSync(join(__dirname, 'lens.ts'), 'utf8'));
    expect(src.match(/\bband\s*:\s*[\d.]/g) ?? [], 'lens.ts holds a band').toEqual([]);
    expect(src.match(/\bfalloff\s*:\s*[\d.]/g) ?? [], 'lens.ts holds a falloff').toEqual([]);
    expect(src.match(/\bstrength\s*:\s*[\d.]/g) ?? [], 'lens.ts holds a strength').toEqual([]);
  });

  it('and the pack states one', () => {
    // The control: without it the sweep above passes just as happily
    // when the field has been renamed and it is measuring nothing.
    const glass = materialPackById('glass');
    expect(glass?.bevel, 'glass declares no bevel').toBeTruthy();
    expect(glass!.bevel!.band).toBeGreaterThan(0);
    expect(glass!.bevel!.falloff).toBeGreaterThan(1);
    expect(glass!.bevel!.strength).toBeGreaterThan(0);
  });

  it('solid declares none, because it must cost nothing', () => {
    // Not a zero band — an ABSENT one. A zero is a number the engine
    // still has to read and a filter it still has to mount.
    expect(materialPackById('solid')?.bevel).toBeUndefined();
    expect(MATERIAL_PACKS.filter((p) => p.bevel).map((p) => p.id)).toEqual(['glass']);
  });
});

/**
 * GLASS DOES NOT BEND WHERE IT DOES NOT END.
 *
 * The owner, at the corner where the rail meets the header: "a lens
 * only acts at an EDGE, so where two parts join it should be
 * continuous." And on the top bar: "its edge is the one facing DOWN —
 * above it is already the outside, it does not need an edge there."
 *
 * Both are the same law, and neither can be said by a bevel drawn from
 * a rounded-rect field: that field is ONE distance to the whole
 * outline, so every side bends whether or not the glass ends there.
 *
 * Which stretches are real is measured at runtime and handed in —
 * nothing here or in the pack declares it, so one rule written for the
 * frame still reaches all five of its sides.
 */
describe('a pane that ends in only some places', () => {
  const at = (m: ReturnType<typeof bevelMap>, mx: number, my: number) => {
    const i = (my * m.width + mx) * 4;
    return { x: m.data[i] - NEUTRAL, y: m.data[i + 1] - NEUTRAL };
  };
  const mid = { x: RESOLUTION.w >> 1, y: RESOLUTION.h >> 1 };

  it('bends toward the stretch that is an edge, and not the other way', () => {
    const right: OpenSpan[] = [{ side: 'right', from: 0, to: 1 }];
    const m = bevelMap(W, H, 0, BEVEL, right);
    expect(at(m, RESOLUTION.w - 1, mid.y).x, 'the open side stopped bending').toBeLessThan(-40);
    expect(at(m, 0, mid.y).x, 'the sealed side bends anyway').toBe(0);
  });

  it('and along a side, only within the stretch that is open', () => {
    // The rail: the header covers the top of its right side, the page
    // faces the rest. The top must be still and the bottom must bend.
    const partial: OpenSpan[] = [{ side: 'right', from: 0.5, to: 1 }];
    const m = bevelMap(W, H, 0, BEVEL, partial);
    const outer = RESOLUTION.w - 1;
    expect(Math.abs(at(m, outer, 1).x), 'it bends where the neighbour covers it')
      .toBeLessThan(6);
    expect(at(m, outer, RESOLUTION.h - 2).x, 'it stopped bending where it faces the page')
      .toBeLessThan(-40);
  });

  it('and a full side is full however the map was rounded', () => {
    // THE BUG THE ID GAVE AWAY. A filter is drawn for a size rounded UP
    // to the next 24px so similar panes share one, and spans measured
    // in PIXELS then lived in a different coordinate system from the
    // map that used them: a full-width top read as `0-968` on a map
    // drawn 984 wide left the last sixteen pixels sealed, at the very
    // corner where it shows most.
    //
    // A fraction means the same thing in both. Drawn for a width the
    // span never saw, the far end still has to bend.
    const full: OpenSpan[] = [{ side: 'top', from: 0, to: 1 }];
    const m = bevelMap(984, 720, 0, BEVEL, full);
    for (const mx of [0, RESOLUTION.w - 1])
      expect(at(m, mx, 0).y, `the top stops bending at column ${mx}`).toBeGreaterThan(40);
  });

  it('and not at all when nothing about it is an edge', () => {
    const m = bevelMap(8, H, 0, BEVEL, []);
    for (const mx of [0, RESOLUTION.w >> 1, RESOLUTION.w - 1])
      expect(at(m, mx, mid.y).x, `column ${mx} bends with no edge anywhere`).toBe(0);
  });

  it('and a pane given no spans at all is byte-for-byte what it was', () => {
    // The default has to be the old path exactly, or every card in the
    // app quietly changes the day this lands.
    const before = bevelMap(W, H, R, BEVEL);
    const after = bevelMap(W, H, R, BEVEL, undefined);
    expect([...after.data]).toEqual([...before.data]);
  });
});

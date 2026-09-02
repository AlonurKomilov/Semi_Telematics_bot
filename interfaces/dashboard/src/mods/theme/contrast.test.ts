/**
 * Guards for the maths that ships.
 *
 * `colour.test.ts` proves the VALUES we authored are legible. This proves
 * the FUNCTIONS stay correct on values nobody authored — which is the
 * whole point of moving them out of the test file: a runtime theme is
 * arithmetic on customer input, and the build-time token guards cannot
 * see it.
 *
 * These are properties swept over the sRGB cube, not examples. An example
 * suite passes by describing the bug it was written after; a sweep fails
 * the moment the function is wrong ANYWHERE. Each one below has been
 * watched to fail against a deliberately broken implementation — a guard
 * nobody has seen go red is a guard nobody has tested.
 */
import { describe, it, expect } from 'vitest';
import {
  oklchToSrgb, srgbToOklch, relLum, contrastRatio, over,
  parseHex, toHex, readableOn, clampLightness, clampSurface,
  AA_TEXT, AA_LARGE, AAA_TEXT, srgbInGamut, srgbToLab, deltaE2000, distance,
  type RGB,
} from './contrast';

const cube = (step: number): RGB[] => {
  const out: RGB[] = [];
  for (let r = 0; r < 256; r += step)
    for (let g = 0; g < 256; g += step)
      for (let b = 0; b < 256; b += step) out.push([r / 255, g / 255, b / 255]);
  return out;
};

/** Every 8th value per channel — 32,768 colours, for the cheap checks. */
const CUBE = cube(8);
/** Every 16th — 4,096. The clamps bisect, and each step gamut-maps, so
 *  the dense cube costs ~50s per sweep; at 16 the whole file runs in
 *  seconds. Density was not free to give up, so it was checked rather
 *  than assumed: every colour this suite has actually caught a bug on
 *  (#d08000, #d88028, #00ff1b) is a multiple of 16 and still swept. A
 *  slow guard is a guard someone eventually passes `--exclude` to. */
const CLAMP_CUBE = cube(16);

const WHITE: RGB = [1, 1, 1];
const NEAR_BLACK: RGB = [10 / 255, 10 / 255, 10 / 255];
const hex = (c: RGB) => toHex(c);

describe('colour space', () => {
  it('round-trips sRGB → OKLCH → sRGB', () => {
    let worst = 0, at = '';
    for (const c of CUBE) {
      const { L, C, H } = srgbToOklch(c);
      const back = oklchToSrgb(L, C, H).rgb;
      const d = Math.max(...back.map((x, i) => Math.abs(x - c[i])));
      if (d > worst) { worst = d; at = hex(c); }
    }
    // 1/255 is one 8-bit step: anything under it is invisible on screen.
    expect(worst, `worst round-trip error at ${at}`).toBeLessThan(1 / 255);
  });

  it('reports gamut mapping honestly', () => {
    // In-gamut colours must not claim they were mapped, or the ratchet in
    // colour.test.ts records noise; out-of-gamut ones must not claim they
    // were painted as written, which is the failure that makes every
    // downstream ratio a sum about a colour nobody sees.
    expect(oklchToSrgb(0.6, 0.05, 250).mapped).toBe(false);
    const far = oklchToSrgb(0.6, 0.4, 250);
    expect(far.mapped).toBe(true);
    expect(far.rgb.every((c) => c >= -1e-6 && c <= 1 + 1e-6)).toBe(true);
  });

  it('relLum matches the WCAG anchors', () => {
    expect(relLum([1, 1, 1])).toBeCloseTo(1, 6);
    expect(relLum([0, 0, 0])).toBeCloseTo(0, 6);
    expect(contrastRatio([1, 1, 1], [0, 0, 0])).toBeCloseTo(21, 6);
    // The endpoints alone prove nothing: decode(0) and decode(1) are the
    // identity, so dropping the sRGB decode entirely still passes them —
    // it did, when this test had only the three lines above. Every other
    // guard in this file is blind to it too, because they compare two
    // contrastRatios and a wrong relLum is wrong on both sides. Only a
    // MID-TONE against a published value catches it.
    expect(relLum(parseHex('#808080')!), 'mid grey').toBeCloseTo(0.2159, 4);
    // Greys alone are still not enough, and this cost a second round of
    // mutation testing to notice: on a grey r === g === b, so ANY three
    // weights that sum to 1 agree. Swapping in YIQ's 0.299/0.587/0.114 —
    // the exact formula this module exists to replace — passed every
    // grey anchor above. Only the primaries pin the coefficients.
    expect(relLum([1, 0, 0]), 'red weight').toBeCloseTo(0.2126, 6);
    expect(relLum([0, 1, 0]), 'green weight').toBeCloseTo(0.7152, 6);
    expect(relLum([0, 0, 1]), 'blue weight').toBeCloseTo(0.0722, 6);
    // #767676 is the darkest grey that clears 4.5:1 on white — the value
    // every contrast tool agrees on, so it pins us to the standard and
    // not merely to ourselves.
    expect(contrastRatio(parseHex('#767676')!, [1, 1, 1])).toBeCloseTo(4.54, 2);
    expect(contrastRatio(parseHex('#808080')!, [1, 1, 1])).toBeCloseTo(3.95, 2);
  });

  it('composites alpha the way the browser does', () => {
    expect(over([1, 0, 0], 0.5, [0, 0, 1])).toEqual([0.5, 0, 0.5]);
    expect(over([1, 0, 0], 1, [0, 0, 1])).toEqual([1, 0, 0]);
    expect(over([1, 0, 0], 0, [0, 0, 1])).toEqual([0, 0, 1]);
  });

  it('parses and prints hex, and rejects what is not hex', () => {
    for (const c of CUBE) expect(parseHex(toHex(c))).toEqual(c);
    expect(parseHex('#abc')).toEqual(parseHex('#aabbcc'));
    expect(parseHex('fff')).toEqual([1, 1, 1]);
    // Customer input: a bad value must be detectable, never silently black.
    for (const bad of ['', '#', 'red', '#12345', '#gggggg', '#1234567'])
      expect(parseHex(bad), `${JSON.stringify(bad)} should not parse`).toBeNull();
  });
});

describe('readableOn', () => {
  it('never picks the worse of the two, anywhere in the cube', () => {
    let bad = 0, worst = { gap: 0, at: '' };
    for (const c of CUBE) {
      const got = contrastRatio(readableOn(c), c);
      const best = Math.max(contrastRatio(NEAR_BLACK, c), contrastRatio(WHITE, c));
      if (got < best - 1e-9) {
        bad++;
        if (best - got > worst.gap) worst = { gap: best - got, at: hex(c) };
      }
    }
    expect(bad, `${bad} surfaces got the worse colour; worst ${worst.at} loses ${worst.gap.toFixed(2)}`).toBe(0);
  });

  it('is the exact maximum, not merely a good pick', () => {
    // The predecessor (YIQ, 0.6 threshold) passed nothing like this: it
    // inverted on 29.9% of the cube, worst case #00ff1b at 1.37:1 where
    // black gave 14.44:1. That case is pinned here by name.
    const green = parseHex('#00ff1b')!;
    expect(readableOn(green)).toEqual(NEAR_BLACK);
    expect(contrastRatio(readableOn(green), green)).toBeGreaterThan(14);
  });

  it('leaves only mid-tones under AA, and never by its own choosing', () => {
    // Some surfaces admit no readable text at all. That is a fact about
    // the surface, not a defect here — but it must stay RARE and be
    // confined to mid lightness, or the claim "then move the surface"
    // is hiding a bug in the pick.
    const stuck = CUBE.filter((c) => contrastRatio(readableOn(c), c) < AA_TEXT);
    expect(stuck.length / CUBE.length).toBeLessThan(0.02);
    for (const c of stuck) {
      const best = Math.max(contrastRatio(NEAR_BLACK, c), contrastRatio(WHITE, c));
      expect(best, `${hex(c)} had a readable option and it was not taken`).toBeLessThan(AA_TEXT);
    }
  });
});

describe('clampLightness', () => {
  // Our two real canvases, the two grounds that broke earlier drafts (a
  // saturated mid-tone brand, and the green that admits no white text),
  // and — load-bearing — #787878.
  //
  // Without that last one the `met: false` branch never executes: on any
  // ground light or dark enough, one endpoint always reaches 4.5, so
  // every clamp succeeds. Two of the tests below then loop over nothing
  // and pass. Three separate mutations survived because of it (a false
  // `met`, an early give-up, and a reversed direction).
  //
  // #787878 is not a round number, it is the branch's only address. The
  // surfaces that admit NO readable text are a hairline band: near-black
  // reaches 4.48:1 there and white 4.42:1, and one step either way
  // (#767676 → 4.54) puts the ground back out of it. A mid grey chosen
  // by eye — #808080, where black still gives 5.32 — leaves the branch
  // just as dead as before.
  const GROUNDS: RGB[] = [
    [1, 1, 1], parseHex('#1e1e1e')!, parseHex('#0a4d8c')!,
    parseHex('#00ff1b')!, parseHex('#787878')!, parseHex('#0048f8')!,
  ];
  // #0048f8 is the ground that caught a direction bug the other four
  // could not see. Its OKLab lightness is 0.505 — nominally light — but
  // blue carries a WCAG luminance weight of 0.0722, so the two measures
  // disagree and a clamp that picked its direction from lightness went
  // the wrong way. Every ground above happens to have the two agree,
  // which is why the suite was green while `met:false` was being
  // returned at AA for saturated blues.  A ground list is only as good
  // as its disagreements.

  it('meets the floor whenever it says it did', () => {
    for (const floor of [AA_TEXT, AAA_TEXT]) {
      let met = 0, unmet = 0;
      for (const bg of GROUNDS) {
        for (const fg of CLAMP_CUBE) {
          const r = clampLightness(fg, bg, floor);
          if (!r.met) { unmet++; continue; }
          met++;
          expect(contrastRatio(r.rgb, bg),
            `${hex(fg)} on ${hex(bg)} claimed met at ${floor}`).toBeGreaterThanOrEqual(floor - 1e-6);
        }
      }
      expect(met, `no successful clamps checked at ${floor}`).toBeGreaterThan(1000);
      // The census, and it differs by floor on purpose. At AA the
      // failure branch CANNOT run — that is the guarantee proved in
      // clampLightness's own comment, and asserting 0 here is what would
      // notice if it stopped holding. At AAA it must run, or the
      // assertion above is being skipped past in silence.
      if (floor === AA_TEXT) {
        expect(unmet, 'a clamp failed at AA — the 4.5826 guarantee broke').toBe(0);
      } else {
        expect(unmet, 'the met:false branch never ran — see GROUNDS').toBeGreaterThan(100);
      }
    }
  });

  it('tells the truth when it cannot get there', () => {
    // met:false must mean the ENDPOINT fails — i.e. no colour of this hue
    // works on this ground. If a smaller step would have succeeded, the
    // caller is being sent to clampSurface for nothing.
    // AAA, because at AA nothing can fail and this would sweep an empty
    // set — which it silently did until the census below was added.
    let seen = 0;
    for (const bg of GROUNDS) {
      for (const fg of CLAMP_CUBE) {
        const { met } = clampLightness(fg, bg, AAA_TEXT);
        if (met) continue;
        seen++;
        const { C, H } = srgbToOklch(fg);
        // Both ends, deliberately: this is also what catches a clamp
        // that walked toward the ground instead of away from it.
        const reachable = Math.max(
          contrastRatio(srgbInGamut(0, C, H), bg),
          contrastRatio(srgbInGamut(1, C, H), bg),
        );
        expect(reachable, `${hex(fg)} on ${hex(bg)} gave up early`).toBeLessThan(AAA_TEXT);
      }
    }
    expect(seen, 'nothing failed to clamp — this test proved nothing').toBeGreaterThan(100);
  });

  it('holds the hue it was given', () => {
    // Asserted as a DISTRIBUTION, not one bound. A clamp that quietly
    // rotated every hue by 4 degrees would sail through `max < 8` while
    // being obviously broken; what proves the hue is held is that the
    // median shift is exactly zero.
    //
    // The clamps generate through `srgbInGamut`, which reduces chroma and
    // never clips across hue, so nothing here rotates a colour on
    // purpose. What residue there is comes from 8-bit quantization: the
    // shipped value is a hex triple, and at low chroma one channel step
    // is a large fraction of the chroma, so the hue it implies moves.
    // That is also why the residue is harmless — a hue shift you can
    // measure on a near-grey is not one you can see.
    //
    // Measured over 22,440 clamps across the six grounds: 23% shift by
    // exactly zero, median 0.154, p90 0.898, p99 3.128, max 5.496
    // (worst #007070 on #787878, 194.8 → 200.3).
    //
    // The bounds sit between that and the failure they exist to catch.
    // Generating through the CSS Color 4 local clip instead measured
    // p99 17.81 and max 20.67 — three to four times the ceiling below,
    // with no overlap.
    const shifts: number[] = [];
    for (const bg of GROUNDS) {
      for (const fg of CLAMP_CUBE) {
        const src = srgbToOklch(fg);
        if (src.C < 0.06) continue; // hue is meaningless on a near-grey
        const { rgb } = clampLightness(fg, bg, AA_TEXT);
        const out = srgbToOklch(rgb);
        if (out.C < 0.02) continue; // gamut mapping flattened it to grey
        shifts.push(Math.min(Math.abs(out.H - src.H), 360 - Math.abs(out.H - src.H)));
      }
    }
    shifts.sort((a, b) => a - b);
    const q = (p: number) => shifts[Math.floor(p * (shifts.length - 1))];
    expect(shifts.length).toBeGreaterThan(1000);
    expect(q(0.5), 'median hue shift').toBeLessThan(0.3);
    expect(q(0.99), 'p99 hue shift').toBeLessThan(5);
    expect(shifts[shifts.length - 1], 'worst hue shift').toBeLessThan(8);
  });

  it('moves as little as it can', () => {
    // The point of a clamp is the CLOSEST legible colour. Stepping back
    // toward the original by a hair must break the floor — otherwise the
    // bisection is overshooting and the customer's colour is being
    // thrown away further than necessary.
    for (const bg of GROUNDS.slice(0, 4)) {
      for (const fg of CLAMP_CUBE.filter((_, i) => i % 7 === 0)) {
        const { rgb, met } = clampLightness(fg, bg, AA_TEXT);
        if (!met) continue;
        const src = srgbToOklch(fg), out = srgbToOklch(rgb);
        if (Math.abs(out.L - src.L) < 1e-4) continue; // was already fine
        const backL = out.L + (src.L - out.L) * 0.05;
        const back = oklchToSrgb(backL, out.C, out.H).rgb;
        expect(contrastRatio(back, bg),
          `${hex(fg)} on ${hex(bg)} overshot`).toBeLessThan(AA_TEXT + 0.15);
      }
    }
  });

  it('leaves a colour that already passes completely alone', () => {
    const bg: RGB = [1, 1, 1];
    for (const fg of CUBE) {
      if (contrastRatio(fg, bg) < AA_TEXT) continue;
      expect(clampLightness(fg, bg, AA_TEXT).rgb).toEqual(fg);
    }
  });

  it('honours a lower floor for non-text UI', () => {
    const bg = parseHex('#ffffff')!;
    const fg = parseHex('#cccccc')!;
    expect(contrastRatio(clampLightness(fg, bg, AA_LARGE).rgb, bg)).toBeGreaterThanOrEqual(AA_LARGE - 1e-6);
    // and a large-text clamp must not be doing the text-floor's work
    expect(contrastRatio(clampLightness(fg, bg, AA_LARGE).rgb, bg)).toBeLessThan(AA_TEXT);
  });
});

describe('clampSurface', () => {
  it('always yields a surface that admits readable text', () => {
    for (const c of CUBE) {
      const { rgb } = clampSurface(c, AA_TEXT);
      expect(contrastRatio(readableOn(rgb), rgb),
        `${hex(c)} → ${hex(rgb)} still admits no text`).toBeGreaterThanOrEqual(AA_TEXT - 1e-6);
    }
  });

  it('moves only what it must', () => {
    for (const c of CUBE) {
      const { rgb, moved } = clampSurface(c, AA_TEXT);
      const wasFine = contrastRatio(readableOn(c), c) >= AA_TEXT;
      expect(moved, `${hex(c)}: moved=${moved} but wasFine=${wasFine}`).toBe(!wasFine);
      if (!moved) expect(rgb).toEqual(c);
    }
  });

  it('keeps the hue the customer chose', () => {
    for (const c of CLAMP_CUBE) {
      const src = srgbToOklch(c);
      if (src.C < 0.06) continue;
      const { rgb, moved } = clampSurface(c, AA_TEXT);
      if (!moved) continue;
      const out = srgbToOklch(rgb);
      if (out.C < 0.02) continue;
      const d = Math.min(Math.abs(out.H - src.H), 360 - Math.abs(out.H - src.H));
      expect(d, `${hex(c)} → ${hex(rgb)}`).toBeLessThan(6);
    }
  });
});

/**
 * CIEDE2000, against the data the standard is checked with.
 *
 * Sharma, Wu & Dalal published 34 pairs chosen to exercise every branch
 * of the formula — the hue-wraparound cases, the RT rotation term near
 * hue 275, the near-neutral chroma cases where the G factor bites. That
 * is why they are here rather than a handful of our own colours: our
 * colours cannot reach those branches, so a copy of this function with a
 * broken wraparound would agree with us on everything we ship and be
 * wrong about the first accent a customer picks.
 *
 * These are REFERENCE values, not values read off this implementation.
 */
describe('deltaE2000 matches the published test data', () => {
  const CASES: Array<[[number, number, number], [number, number, number], number]> = [
    // Hue wraparound, blue end — the pairs the 1976 formula gets worst.
    [[50, 2.6772, -79.7751], [50, 0, -82.7485], 2.0425],
    [[50, 3.1571, -77.2803], [50, 0, -82.7485], 2.8615],
    [[50, 2.8361, -74.02], [50, 0, -82.7485], 3.4412],
    [[50, -1.3802, -84.2814], [50, 0, -82.7485], 1.0000],
    [[50, -1.1848, -84.8006], [50, 0, -82.7485], 1.0000],
    [[50, -0.9009, -85.5211], [50, 0, -82.7485], 1.0000],
    // Near-neutral, where the G factor rescales a*.
    [[50, 0, 0], [50, -1, 2], 2.3669],
    [[50, -1, 2], [50, 0, 0], 2.3669],
    [[50, 2.49, -0.001], [50, -2.49, 0.0009], 7.1792],
    // Deliberately just-noticeable: every one of these is exactly 1.
    [[50, 2.5, 0], [50, 3.1736, 0.5854], 1.0000],
    [[50, 2.5, 0], [50, 3.2972, 0], 1.0000],
    [[50, 2.5, 0], [50, 1.8634, 0.5757], 1.0000],
    [[50, 2.5, 0], [50, 3.2592, 0.335], 1.0000],
    // Large differences, where SL/SC/SH carry the answer.
    [[50, 2.5, 0], [73, 25, -18], 27.1492],
    [[50, 2.5, 0], [61, -5, 29], 22.8977],
    [[50, 2.5, 0], [56, -27, -3], 31.9030],
    // Real measured pairs from the paper's tail.
    [[60.2574, -34.0099, 36.2677], [60.4626, -34.1751, 39.4387], 1.2644],
    [[63.0109, -31.0961, -5.8663], [62.8187, -29.7946, -4.0864], 1.2630],
    [[22.7233, 20.0904, -46.694], [23.0331, 14.973, -42.5619], 2.0373],
    [[90.9257, -0.5406, -0.9208], [88.6381, -0.8985, -0.7239], 1.5381],
    [[2.0776, 0.0795, -1.135], [0.9033, -0.0636, -0.5514], 0.9082],
  ];

  it.each(CASES)('%j vs %j is %f', (a, b, expected) => {
    expect(deltaE2000(a, b)).toBeCloseTo(expected, 3);
  });

  it('is symmetric and zero on itself, everywhere in the cube', () => {
    for (let i = 0; i < 400; i++) {
      const rnd = (): RGB => [
        ((i * 37) % 256) / 255, ((i * 91) % 256) / 255, ((i * 143) % 256) / 255,
      ];
      const a = rnd();
      const b: RGB = [a[2], a[0], a[1]];
      expect(distance(a, a)).toBeCloseTo(0, 9);
      expect(distance(a, b)).toBeCloseTo(distance(b, a), 9);
    }
  });

  it('srgbToLab puts the two ends of the cube where CIELAB says', () => {
    const [wL, wa, wb] = srgbToLab([1, 1, 1]);
    expect(wL).toBeCloseTo(100, 2);
    expect(Math.hypot(wa, wb)).toBeLessThan(0.02);
    expect(srgbToLab([0, 0, 0])[0]).toBeCloseTo(0, 6);
  });
});

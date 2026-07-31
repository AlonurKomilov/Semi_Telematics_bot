/**
 * The layout resolver — pure invariants the scene relies on.
 *
 * The bounds suite runs the REAL vocabulary shape (per-system counts
 * copied from adapters/storage/service_taxonomy.py), not a synthetic
 * guess: an earlier synthetic fixture degenerated to a uniform count
 * and waved through a layout the real data pushed off the world.
 */
import { describe, expect, it } from 'vitest';
import {
  BANDS, GROUND, POSITIONED, TRAILER_SHIFT, UNIT_CAPTIONS, bandOf,
  resolveLayout, type AssemblyLike, type Unit,
} from './layout';

const A = (key: string, system_key: string): AssemblyLike => ({ key, system_key });

const SAMPLE: AssemblyLike[] = [
  A('air_compressor', 'air_system'), A('air_dryer', 'air_system'),
  A('pads_shoes', 'brakes'), A('abs', 'brakes'),
  A('radiator', 'cooling'), A('water_pump', 'cooling'), A('thermostat', 'cooling'),
  A('headlights', 'lighting'),
];

/**
 * The real seeded taxonomy, by shape: 112 assemblies over 15 systems
 * (counts mirror SERVICE_ASSEMBLIES — update together).  All 15
 * POSITIONED keys are real air_system/brakes assemblies, so those two
 * systems contribute NOTHING to the shelf.
 */
const REAL_COUNTS: Record<string, number> = {
  engine: 9, cooling: 7, fuel: 6, exhaust: 8, drivetrain: 8,
  brakes: 9, air_system: 6, suspension: 7, steering: 5,
  tires_wheels: 6, electrical: 8, lighting: 4, hvac: 8,
  body_cab: 12, trailer: 9,
};
const REAL_ORDER = Object.keys(REAL_COUNTS);

const realFixture = (growth = 0): AssemblyLike[] => {
  const fixture: AssemblyLike[] = [];
  const positioned = Object.keys(POSITIONED);
  positioned.forEach((k, i) => fixture.push(A(k, i < 6 ? 'air_system' : 'brakes')));
  for (const [sys, count] of Object.entries(REAL_COUNTS)) {
    const already = sys === 'air_system' ? 6 : sys === 'brakes' ? 9 : 0;
    for (let i = 0; i < count - already + growth; i += 1) {
      fixture.push(A(`${sys}_item_${i}`, sys));
    }
  }
  return fixture;
};

describe('resolveLayout', () => {
  it('gives every assembly exactly one slot', () => {
    const { slots } = resolveLayout(SAMPLE, ['air_system', 'brakes', 'cooling', 'lighting']);
    expect(slots.size).toBe(SAMPLE.length);
  });

  it('authored positions are honored verbatim and flagged', () => {
    const { slots } = resolveLayout(SAMPLE, []);
    const s = slots.get('air_compressor')!;
    expect(s.positioned).toBe(true);
    expect(s.pos).toEqual(POSITIONED.air_compressor.pos);
  });

  it('unpositioned assemblies land on the shelf, never at origin', () => {
    const { slots } = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    for (const key of ['radiator', 'water_pump', 'thermostat', 'headlights']) {
      const s = slots.get(key)!;
      expect(s.positioned).toBe(false);
      expect(s.pos[2]).toBeGreaterThan(3);      // beside the rig, not inside it
    }
  });

  it('no two shelf slots collide', () => {
    const { slots } = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    const seen = new Set<string>();
    for (const [, s] of slots) {
      const k = s.pos.join(',');
      expect(seen.has(k)).toBe(false);
      seen.add(k);
    }
  });

  it('every shelf system gets a cluster caption anchor', () => {
    const { clusters } = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    expect(clusters.has('cooling')).toBe(true);
    expect(clusters.has('lighting')).toBe(true);
    // positioned-only systems don't get shelf captions
    expect(clusters.has('air_system')).toBe(false);
  });

  it('is a pure function: identical input, identical output', () => {
    const a = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    const b = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    expect([...a.slots.entries()]).toEqual([...b.slots.entries()]);
  });

  it('holds every invariant under a permuted systemsOrder', () => {
    // Order changes where clusters march, never which band holds them
    // and never whether slots collide.
    const reversed = [...REAL_ORDER].reverse();
    const { slots, clusters } = resolveLayout(realFixture(), reversed);
    const seen = new Set<string>();
    for (const [, s] of slots) {
      const k = s.pos.join(',');
      expect(seen.has(k)).toBe(false);
      seen.add(k);
    }
    for (const [sys, [x]] of clusters) {
      const band = BANDS[bandOf(sys)];
      expect(x).toBeLessThanOrEqual(band.xStart);
      expect(x).toBeGreaterThan(band.xFloor);
    }
  });
});

describe('unit bands (tractor / shared seam / trailer)', () => {
  it('maps tractor-only systems to the tractor, the trailer to the trailer, everything else to the seam', () => {
    expect(bandOf('engine')).toBe('tractor');
    expect(bandOf('hvac')).toBe('tractor');
    expect(bandOf('trailer')).toBe('trailer');
    // both-unit systems and any FUTURE system key default to shared
    expect(bandOf('brakes')).toBe('shared');
    expect(bandOf('lighting')).toBe('shared');
    expect(bandOf('some_new_system')).toBe('shared');
  });

  it('band x-windows are disjoint (bands share z lanes — overlap would collide)', () => {
    const order: Unit[] = ['tractor', 'shared', 'trailer'];
    for (let i = 1; i < order.length; i += 1) {
      expect(BANDS[order[i]].xStart).toBeLessThan(BANDS[order[i - 1]].xFloor);
    }
  });

  it('keeps every SLOT of a system inside its band x-window (real scale)', () => {
    // Anchors satisfy the window by construction; the slots marching
    // aft of the anchor are what can actually leak into the next band.
    const { slots } = resolveLayout(realFixture(), REAL_ORDER);
    for (const [key, s] of slots) {
      if (s.positioned) continue;
      const sys = key.replace(/_item_\d+$/, '');
      const band = BANDS[bandOf(sys)];
      expect(s.pos[0], key).toBeLessThanOrEqual(band.xStart);
      expect(s.pos[0], key).toBeGreaterThanOrEqual(band.xFloor - 1e-9);
    }
  });

  it('reads front-to-back: tractor clusters, then seam, then trailer', () => {
    const { clusters } = resolveLayout(realFixture(), REAL_ORDER);
    const [engX] = clusters.get('engine')!;
    const [elecX] = clusters.get('electrical')!;
    const [trlX] = clusters.get('trailer')!;
    expect(engX).toBeGreaterThan(elecX);
    expect(elecX).toBeGreaterThan(trlX);
  });
});

describe('the two-vehicle stage', () => {
  it('the trailer parks aft with a readable gap to the tractor', () => {
    // Mirrored ghost-rig constants (GhostChassis.tsx): tractor frame
    // rails end at x=-1.2; the trailer box nose sits at local x=+1.1.
    const TRACTOR_TAIL = -1.2;
    const TRAILER_NOSE = 1.1 + TRAILER_SHIFT;
    expect(TRAILER_NOSE).toBeLessThan(TRACTOR_TAIL - 2);   // ≥2m visible gap
  });

  it('vehicle captions sit on the open driver side, opposite the shelf', () => {
    const { slots } = resolveLayout(realFixture(), REAL_ORDER);
    for (const u of UNIT_CAPTIONS) {
      expect(u.pos[2]).toBeLessThan(-1.4);                 // clear of the rigs
      for (const [, s] of slots) {
        if (!s.positioned) expect(Math.sign(s.pos[2])).not.toBe(Math.sign(u.pos[2]));
      }
    }
  });
});

describe('shelf bounds (the real-world scale)', () => {
  const onPlane = (pos: readonly [number, number, number], what: string) => {
    const [cx, cz] = GROUND.center;
    expect(pos[0], `${what} x`).toBeGreaterThan(cx - GROUND.size[0] / 2 + 0.5);
    expect(pos[0], `${what} x`).toBeLessThan(cx + GROUND.size[0] / 2 - 0.5);
    expect(pos[2], `${what} z`).toBeGreaterThan(cz - GROUND.size[1] / 2 + 0.5);
    expect(pos[2], `${what} z`).toBeLessThan(cz + GROUND.size[1] / 2 - 0.5);
  };

  it('all slots and captions stay on the ground plane', () => {
    const { slots, clusters } = resolveLayout(realFixture(), REAL_ORDER);
    for (const [key, s] of slots) onPlane(s.pos, `slot ${key}`);
    for (const [key, pos] of clusters) onPlane(pos, `cluster ${key}`);
    for (const u of UNIT_CAPTIONS) onPlane(u.pos, `caption ${u.key}`);
  });

  it('still fits with +3 assemblies in EVERY system (growth headroom)', () => {
    const { slots, clusters } = resolveLayout(realFixture(3), REAL_ORDER);
    for (const [key, s] of slots) onPlane(s.pos, `slot ${key}`);
    for (const [key, pos] of clusters) onPlane(pos, `cluster ${key}`);
  });

  it('shelf slots stay clear of both vehicles (z beside the rig)', () => {
    const { slots } = resolveLayout(realFixture(), REAL_ORDER);
    for (const [, s] of slots) {
      if (s.positioned) continue;
      expect(s.pos[2]).toBeGreaterThan(3.5);    // rigs live in z ∈ [-1.4, 1.4]
    }
  });
});

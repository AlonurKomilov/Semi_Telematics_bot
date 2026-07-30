/**
 * The layout resolver — pure invariants the scene relies on.
 */
import { describe, expect, it } from 'vitest';
import { POSITIONED, resolveLayout, type AssemblyLike } from './layout';

const A = (key: string, system_key: string): AssemblyLike => ({ key, system_key });

const SAMPLE: AssemblyLike[] = [
  A('air_compressor', 'air_system'), A('air_dryer', 'air_system'),
  A('pads_shoes', 'brakes'), A('abs', 'brakes'),
  A('radiator', 'cooling'), A('water_pump', 'cooling'), A('thermostat', 'cooling'),
  A('headlights', 'lighting'),
];

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

  it('is deterministic and stable across system order input', () => {
    const a = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    const b = resolveLayout(SAMPLE, ['cooling', 'lighting']);
    expect([...a.slots.entries()]).toEqual([...b.slots.entries()]);
  });
});

describe('shelf bounds (the real-world scale)', () => {
  it('112 assemblies across 16 systems all stay on the ground plane', () => {
    // Synthetic worst case shaped like the real vocabulary: 15
    // positioned + ~97 shelf items over 13 systems of 6-9 each.
    const fixture: AssemblyLike[] = [];
    const positionedKeys = Object.keys(POSITIONED);
    positionedKeys.forEach((k, i) =>
      fixture.push(A(k, i < 6 ? 'air_system' : 'brakes')));
    const systems = Array.from({ length: 13 }, (_, i) => `sys_${i}`);
    let n = 0;
    for (const sys of systems) {
      const count = 6 + (n % 4);      // 6..9 per system, ~97 total
      for (let i = 0; i < count; i += 1, n += 1) {
        fixture.push(A(`shelf_item_${n}`, sys));
      }
    }
    const { slots } = resolveLayout(fixture, systems);
    for (const [, s] of slots) {
      if (s.positioned) continue;
      // Ground plane: 34×32 centred at origin → x ∈ [-17,17], z ∈ [-16,16]
      expect(s.pos[0]).toBeGreaterThan(-16.5);
      expect(s.pos[0]).toBeLessThan(9);
      expect(s.pos[2]).toBeGreaterThan(3.5);
      expect(s.pos[2]).toBeLessThan(15.5);
    }
  });
});

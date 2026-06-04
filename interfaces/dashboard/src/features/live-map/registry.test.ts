import { describe, it, expect } from 'vitest';
import { LIVE_MAP_SECTIONS } from './registry';
import { LIVE_MAP_LAYOUTS } from './layouts';

/**
 * Every persona referenced by a Live Map layout must resolve to a real
 * registry entry — catches typos like ``'safety_event'`` vs
 * ``'safety_heatmap'`` that PageLayoutHost would silently skip at
 * runtime.  Coverage of the canonical Persona set is enforced by the
 * project-wide ``npm run check:layout-coverage`` script.
 */
describe('Live Map registry × layouts', () => {
  it('every section id used in any layout exists in LIVE_MAP_SECTIONS', () => {
    for (const [persona, layout] of Object.entries(LIVE_MAP_LAYOUTS)) {
      if (!layout) continue;
      for (const sectionId of layout) {
        expect(
          LIVE_MAP_SECTIONS[sectionId],
          `persona "${persona}" references unknown section "${sectionId}"`,
        ).toBeDefined();
      }
    }
  });

  it('every registry section has a non-empty label', () => {
    for (const [id, def] of Object.entries(LIVE_MAP_SECTIONS)) {
      expect(def.label, `section "${id}" missing label`).toBeTruthy();
    }
  });
});


/**
 * Strict role-binding lock for Live Map overlays.  Each persona's
 * layout MUST only include overlays that match THAT persona's
 * workspace.  A regression here would put Dispatch's geofence
 * boundaries on a Safety user's map (or worse, leak Fleet's fault
 * markers onto Owner's executive view) without anyone noticing.
 *
 * Maintain in lockstep with [docs/architecture/PERSONA.md].  When a
 * new overlay lands, decide its target persona(s) and add it to
 * both the corresponding ``allowed`` set here AND the persona's
 * array in layouts.ts.
 */
describe('Live Map strict role binding', () => {
  // What each persona is ALLOWED to mount.  Anything in the
  // persona's layout outside this set is a strict-binding violation.
  const ALLOWED: Record<string, Set<string>> = {
    owner: new Set([
      'fleet_status',
      'company_color_partition',
      'utilisation_heatmap',
      'safety_heatmap',
    ]),
    admin: new Set([
      'fleet_status',
      'company_color_partition',
      'utilisation_heatmap',
      'safety_heatmap',
    ]),
    fleet: new Set([
      'fleet_status',
      'fault_markers',
      'maintenance_markers',
      'safety_heatmap',
    ]),
    dispatcher: new Set([
      'dispatch_route',
      'geofence_boundaries',
      'unsafe_parking_markers',
      'safety_heatmap',
    ]),
    safety: new Set(['fleet_status', 'safety_heatmap']),
    hr: new Set(['fleet_status', 'safety_heatmap']),
    accounting: new Set(['fleet_status', 'company_color_partition']),
    driver: new Set(['fleet_status']),
  };

  for (const [persona, allowed] of Object.entries(ALLOWED)) {
    it(`${persona} layout only contains ${persona}-relevant overlays`, () => {
      const layout = LIVE_MAP_LAYOUTS[persona as keyof typeof LIVE_MAP_LAYOUTS] ?? [];
      const leaked = layout.filter((id) => !allowed.has(id));
      expect(
        leaked,
        `${persona} layout includes overlay(s) outside its workspace: ${leaked.join(', ')}`,
      ).toEqual([]);
    });
  }

  it('Dispatch is the ONLY persona that mounts geofence_boundaries', () => {
    for (const [persona, layout] of Object.entries(LIVE_MAP_LAYOUTS)) {
      if (persona === 'dispatcher') continue;
      expect(
        layout?.includes('geofence_boundaries'),
        `${persona} should not mount geofence_boundaries (Dispatch-only)`,
      ).toBeFalsy();
    }
  });

  it('Dispatch is the ONLY persona that mounts unsafe_parking_markers', () => {
    for (const [persona, layout] of Object.entries(LIVE_MAP_LAYOUTS)) {
      if (persona === 'dispatcher') continue;
      expect(
        layout?.includes('unsafe_parking_markers'),
        `${persona} should not mount unsafe_parking_markers (Dispatch-only)`,
      ).toBeFalsy();
    }
  });

  it('Fleet is the ONLY persona that mounts fault_markers + maintenance_markers', () => {
    for (const [persona, layout] of Object.entries(LIVE_MAP_LAYOUTS)) {
      if (persona === 'fleet') continue;
      expect(
        layout?.includes('fault_markers'),
        `${persona} should not mount fault_markers (Fleet-only)`,
      ).toBeFalsy();
      expect(
        layout?.includes('maintenance_markers'),
        `${persona} should not mount maintenance_markers (Fleet-only)`,
      ).toBeFalsy();
    }
  });

  it('utilisation_heatmap is mounted ONLY for Owner / Admin', () => {
    const owners = new Set(['owner', 'admin']);
    for (const [persona, layout] of Object.entries(LIVE_MAP_LAYOUTS)) {
      const has = layout?.includes('utilisation_heatmap') ?? false;
      if (owners.has(persona)) {
        expect(has, `${persona} should mount utilisation_heatmap`).toBe(true);
      } else {
        expect(
          has,
          `${persona} should not mount utilisation_heatmap (Owner/Admin only)`,
        ).toBe(false);
      }
    }
  });

  it('company_color_partition is mounted ONLY for cross-cutting personas', () => {
    const expected = new Set(['owner', 'admin', 'accounting']);
    for (const [persona, layout] of Object.entries(LIVE_MAP_LAYOUTS)) {
      const has = layout?.includes('company_color_partition') ?? false;
      if (expected.has(persona)) {
        expect(has, `${persona} should mount company_color_partition`).toBe(true);
      } else {
        expect(
          has,
          `${persona} should not mount company_color_partition`,
        ).toBe(false);
      }
    }
  });
});

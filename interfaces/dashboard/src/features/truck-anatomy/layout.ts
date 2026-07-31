/**
 * Where each assembly sits on the ghost rig.
 *
 * Coordinate frame: +x = toward the tractor's nose, +y = up,
 * +z = passenger (curb) side — the shelf lives there; the driver's
 * side (-z) stays open floor for the vehicle captions.  Units are
 * roughly meters.
 *
 * The rig is TWO vehicles, uncoupled — the same worldview as the
 * Vehicle registry (truck and trailer are both first-class):
 *   • TRACTOR — nose near x=8, frame rails end near x=-1.
 *   • TRAILER — shifted aft by TRAILER_SHIFT so an open gap separates
 *     the units and the coupling hardware (fifth wheel, kingpin,
 *     gladhands) is visible instead of buried in an overlap.
 *
 * Two slot tiers, by design:
 *   • POSITIONED — hand-authored places for the systems we've done
 *     properly (air system + brakes first: the most truck-specific,
 *     least-covered teaching content there is).
 *   • the SHELF — every assembly we haven't positioned yet renders in
 *     a labeled cluster beside the rig, grouped by system, so all 112
 *     exist on screen from day one and each system graduates from the
 *     shelf to the chassis as its positions get authored.
 *
 * The shelf is split into three BANDS that mirror the physical truth
 * (presentation only — the taxonomy has no truck/trailer dimension,
 * so this mapping must never leave this file):
 *   • tractor-only systems park beside the tractor,
 *   • the trailer's own system parks beside the trailer,
 *   • systems that exist on BOTH units (brakes, air, electrical,
 *     lighting, tires, suspension …) park at the SEAM between them —
 *     literally the systems that cross the gladhands.
 *
 * Pure module — no React, no three.js — so the resolver is unit-
 * testable and the layout is data a 3D freelancer can edit without
 * touching a component.
 */

export interface Slot {
  pos: [number, number, number];
  size: [number, number, number];
  /** Hand-authored chassis position vs auto shelf. */
  positioned: boolean;
}

export interface AssemblyLike { key: string; system_key: string }

/** World-x shift of the whole trailer group — the uncoupled gap. */
export const TRAILER_SHIFT = -5.5;

/**
 * The ground plane, shared by GhostChassis (draws it) and the bounds
 * test (asserts every slot and caption stays on it).  One rectangle,
 * two consumers — they cannot drift apart.
 */
export const GROUND = {
  center: [-3.5, 6] as [number, number],   // x, z
  size: [34, 30] as [number, number],      // width (x), depth (z)
};

export type Unit = 'tractor' | 'shared' | 'trailer';

/**
 * Which unit's shelf band a system parks beside.  PRESENTATION ONLY:
 * VMRS-style systems are equipment-agnostic, so anything not listed
 * (brakes, air_system, suspension, tires_wheels, electrical, lighting,
 * pm, inspection, other, future keys) defaults to the shared seam.
 */
const BAND_OF: Record<string, Unit> = {
  engine: 'tractor', cooling: 'tractor', fuel: 'tractor',
  exhaust: 'tractor', drivetrain: 'tractor', steering: 'tractor',
  hvac: 'tractor', body_cab: 'tractor',
  trailer: 'trailer',
};
export const bandOf = (systemKey: string): Unit =>
  BAND_OF[systemKey] ?? 'shared';

/**
 * Shelf x-windows per band; lanes wrap in +z inside their own band.
 * Windows are DISJOINT in x — bands share the same z lanes, so an
 * overlap in x would let two bands collide.  The seam band is the
 * widest: it holds the most systems (everything that lives on both
 * vehicles), and depth costs more than width here — every extra lane
 * pushes the shelf further from the rig.
 */
export const BANDS: Record<Unit, { xStart: number; xFloor: number }> = {
  // 2m seams between windows — wider than the 1.2m intra-band cluster
  // gap, so the three bands read as three groups, not one long shelf.
  tractor: { xStart: 10, xFloor: -1 },
  shared:  { xStart: -3, xFloor: -12 },
  trailer: { xStart: -14, xFloor: -19.5 },
};

/** Ground captions naming the two vehicles (driver's side, open floor). */
export const UNIT_CAPTIONS: ReadonlyArray<{
  key: Unit; label: string; pos: [number, number, number];
}> = [
  { key: 'tractor', label: 'Tractor', pos: [4.5, 0.05, -2.8] },
  { key: 'trailer', label: 'Trailer', pos: [-11.1, 0.05, -2.8] },
];

/** Hand-authored chassis positions. Keys = service_assembly_library. */
export const POSITIONED: Record<string, Omit<Slot, 'positioned'>> = {
  // ── Air system: compressor on the engine, dryer behind the cab,
  //    tanks on the frame rails, lines running aft, gladhands hung on
  //    the sleeper's rear wall (the coupling side — uncoupled, that is
  //    where they visibly live), valve cluster near the dash/frame. ──
  air_compressor:    { pos: [6.6, 1.15, 0.45],  size: [0.5, 0.5, 0.5] },
  air_dryer:         { pos: [3.4, 0.95, 0.95],  size: [0.45, 0.7, 0.45] },
  air_tanks:         { pos: [3.0, 0.55, -0.85], size: [1.6, 0.45, 0.45] },
  air_lines:         { pos: [0.6, 0.75, 0.0],   size: [3.2, 0.12, 0.12] },
  gladhands:         { pos: [2.5, 1.7, 0.0],    size: [0.4, 0.3, 0.9] },
  air_supply_valves: { pos: [4.6, 0.85, 0.6],   size: [0.6, 0.35, 0.35] },
  // ── Brakes: per-wheel hardware sketched at the drive axles, ABS
  //    module on the frame, control valves near the dash. ──
  pads_shoes:          { pos: [2.2, 0.5, 1.05],  size: [0.55, 0.55, 0.25] },
  drums_rotors:        { pos: [2.2, 0.5, -1.05], size: [0.65, 0.65, 0.3] },
  brake_chambers:      { pos: [1.6, 0.62, 1.0],  size: [0.4, 0.4, 0.45] },
  slack_adjusters:     { pos: [1.6, 0.45, -1.0], size: [0.35, 0.2, 0.4] },
  scam_hardware:       { pos: [2.8, 0.45, 1.0],  size: [0.45, 0.3, 0.3] },
  abs:                 { pos: [3.9, 0.7, -0.6],  size: [0.35, 0.3, 0.3] },
  brake_control_valves:{ pos: [5.2, 0.95, 0.3],  size: [0.5, 0.3, 0.4] },
  // frame rails end at x=-1.2 — the line must stay ON the frame now
  // that no coupled trailer hides an overhang
  brake_lines:         { pos: [0.4, 0.62, 0.55], size: [3.2, 0.1, 0.1] },
  parking_brake:       { pos: [5.6, 1.3, -0.3],  size: [0.3, 0.3, 0.3] },
};

/** Shelf geometry: clusters beside the rig on the passenger side. */
const SHELF_Z = 4.2;          // first lane beside the chassis
const SHELF_COLS = 3;         // items per cluster row
const ITEM = 0.9;             // slot pitch inside a cluster
const CLUSTER_GAP = 1.2;      // air between system clusters
const LANE_PITCH = SHELF_COLS * ITEM + 1.4;
const SHELF_SIZE: [number, number, number] = [0.6, 0.6, 0.6];

export interface ResolvedLayout {
  slots: Map<string, Slot>;
  /** Shelf cluster origins by system — where its floating label goes. */
  clusters: Map<string, [number, number, number]>;
}

/**
 * Every assembly gets exactly one slot: its authored position when we
 * have one, otherwise a place on its system's shelf cluster inside the
 * system's band.  `systemsOrder` fixes cluster order (the reporting
 * axis's own order), so the shelf is stable run to run.
 */
export function resolveLayout(
  assemblies: AssemblyLike[], systemsOrder: string[],
): ResolvedLayout {
  const slots = new Map<string, Slot>();
  const clusters = new Map<string, [number, number, number]>();

  const bySystem = new Map<string, AssemblyLike[]>();
  for (const a of assemblies) {
    if (POSITIONED[a.key]) {
      slots.set(a.key, { ...POSITIONED[a.key], positioned: true });
      continue;
    }
    const list = bySystem.get(a.system_key) ?? [];
    list.push(a);
    bySystem.set(a.system_key, list);
  }

  const order = [
    ...systemsOrder.filter((k) => bySystem.has(k)),
    ...[...bySystem.keys()].filter((k) => !systemsOrder.includes(k)),
  ];

  // Each band marches aft through its own x-window and WRAPS into a
  // further lane (+z) when it would walk past the window — all three
  // bands must stay on the ground plane at real vocabulary scale
  // (bounds-tested against GROUND).
  const cursors: Record<Unit, { x: number; lane: number }> = {
    tractor: { x: BANDS.tractor.xStart, lane: 0 },
    shared:  { x: BANDS.shared.xStart,  lane: 0 },
    trailer: { x: BANDS.trailer.xStart, lane: 0 },
  };
  for (const systemKey of order) {
    const items = bySystem.get(systemKey)!;
    const band = bandOf(systemKey);
    const { xStart, xFloor } = BANDS[band];
    const cur = cursors[band];
    const rows = Math.ceil(items.length / SHELF_COLS);
    if (cur.x !== xStart && cur.x - rows * ITEM < xFloor) {
      cur.x = xStart;
      cur.lane += 1;
    }
    const laneZ = SHELF_Z + cur.lane * LANE_PITCH;
    clusters.set(systemKey, [cur.x, 0.3, laneZ]);
    items.forEach((a, i) => {
      const col = i % SHELF_COLS;
      const row = Math.floor(i / SHELF_COLS);
      slots.set(a.key, {
        pos: [cur.x - row * ITEM, 0.3, laneZ + col * ITEM],
        size: SHELF_SIZE,
        positioned: false,
      });
    });
    cur.x -= rows * ITEM + CLUSTER_GAP;
  }
  return { slots, clusters };
}

/**
 * Where each assembly sits on the ghost chassis.
 *
 * Coordinate frame: +x = toward the tractor's nose, +y = up,
 * +z = driver's side.  Units are roughly meters; the whole rig is
 * ~20 long.  The tractor nose is near x=8, the fifth wheel ~x=1.5,
 * the trailer runs x=1 … x=-12.
 *
 * Two tiers, by design:
 *   • POSITIONED — hand-authored places for the systems we've done
 *     properly (air system + brakes first: the most truck-specific,
 *     least-covered teaching content there is).
 *   • the SHELF — every assembly we haven't positioned yet renders in
 *     a labeled cluster beside the rig, grouped by system, so all 112
 *     exist on screen from day one and each system graduates from the
 *     shelf to the chassis as its positions get authored.  Same
 *     growth pattern as the taxonomy itself: node by node.
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

/** Hand-authored chassis positions. Keys = service_assembly_library. */
export const POSITIONED: Record<string, Omit<Slot, 'positioned'>> = {
  // ── Air system: compressor on the engine, dryer behind the cab,
  //    tanks on the frame rails, lines running aft, gladhands at the
  //    trailer nose, valve cluster near the dash/frame. ──
  air_compressor:    { pos: [6.6, 1.15, 0.45],  size: [0.5, 0.5, 0.5] },
  air_dryer:         { pos: [3.4, 0.95, 0.95],  size: [0.45, 0.7, 0.45] },
  air_tanks:         { pos: [3.0, 0.55, -0.85], size: [1.6, 0.45, 0.45] },
  air_lines:         { pos: [0.6, 0.75, 0.0],   size: [3.2, 0.12, 0.12] },
  gladhands:         { pos: [1.3, 1.6, 0.0],    size: [0.5, 0.3, 0.9] },
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
  brake_lines:         { pos: [0.0, 0.62, 0.55], size: [4.0, 0.1, 0.1] },
  parking_brake:       { pos: [5.6, 1.3, -0.3],  size: [0.3, 0.3, 0.3] },
};

/** Shelf geometry: clusters beside the rig on the passenger side. */
const SHELF_Z = 4.2;          // one lane beside the chassis
const SHELF_COLS = 3;         // items per cluster row
const ITEM = 0.9;             // slot pitch inside a cluster
const CLUSTER_GAP = 1.2;      // air between system clusters
const SHELF_SIZE: [number, number, number] = [0.6, 0.6, 0.6];

export interface ResolvedLayout {
  slots: Map<string, Slot>;
  /** Shelf cluster origins by system — where its floating label goes. */
  clusters: Map<string, [number, number, number]>;
}

/**
 * Every assembly gets exactly one slot: its authored position when we
 * have one, otherwise a place on its system's shelf cluster.
 * `systemsOrder` fixes cluster order (the reporting axis's own order),
 * so the shelf is stable run to run.
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

  // Lanes: the shelf marches aft and WRAPS into a further lane when
  // it would walk off the ground plane — 97 unpositioned assemblies
  // must stay inside the world, not float in the void.
  const X_START = 8;
  const X_FLOOR = -15.5;
  const LANE_PITCH = SHELF_COLS * ITEM + 1.4;
  let cursorX = X_START;
  let lane = 0;
  for (const systemKey of order) {
    const items = bySystem.get(systemKey)!;
    const rows = Math.ceil(items.length / SHELF_COLS);
    if (cursorX - rows * ITEM < X_FLOOR) { cursorX = X_START; lane += 1; }
    const laneZ = SHELF_Z + lane * LANE_PITCH;
    clusters.set(systemKey, [cursorX, 0.3, laneZ]);
    items.forEach((a, i) => {
      const col = i % SHELF_COLS;
      const row = Math.floor(i / SHELF_COLS);
      slots.set(a.key, {
        pos: [cursorX - row * ITEM, 0.3, laneZ + col * ITEM],
        size: SHELF_SIZE,
        positioned: false,
      });
    });
    cursorX -= rows * ITEM + CLUSTER_GAP;
  }
  return { slots, clusters };
}

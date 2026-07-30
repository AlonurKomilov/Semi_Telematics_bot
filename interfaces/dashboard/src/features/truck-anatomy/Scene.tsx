/**
 * The 3D stage: ghost rig + one AssemblyNode per taxonomy entry.
 *
 * Selection logic lives in the PAGE; the scene receives plain props
 * and reports clicks — so the panel and the canvas can never disagree
 * about what's selected, and the scene stays dumb enough to test the
 * interesting parts (layout, explode math) as pure functions.
 */
import { useEffect, useMemo, useRef } from 'react';
import { Canvas } from '@react-three/fiber';
import { CameraControls, Html } from '@react-three/drei';
import * as THREE from 'three';
import type { Assembly } from '../parts/useAssemblies';
import AssemblyNode from './AssemblyNode';
import GhostChassis from './GhostChassis';
import { resolveLayout, type ResolvedLayout } from './layout';
import { useSceneTokens } from './colors';

export interface SceneSelection {
  systemKey: string | null;
  assemblyKey: string | null;
}

export default function Scene({
  assemblies, systemsOrder, systemLabels, selection, explode, matches, onPick,
}: {
  assemblies: Assembly[];
  systemsOrder: string[];
  systemLabels: Map<string, string>;
  selection: SceneSelection;
  explode: boolean;
  /** Search narrowing — null = no query; otherwise the matching keys. */
  matches: Set<string> | null;
  onPick: (assemblyKey: string | null) => void;
}) {
  const tokens = useSceneTokens();
  const controls = useRef<CameraControls | null>(null);
  const cameraInitialised = useRef(false);

  // The hover handlers set the body cursor; if a hovered node unmounts
  // (navigation, filtering) the pointer would stick app-wide.
  useEffect(() => () => { document.body.style.cursor = 'auto'; }, []);

  const layout: ResolvedLayout = useMemo(
    () => resolveLayout(assemblies, systemsOrder),
    [assemblies, systemsOrder],
  );

  // Explode: push each selected-system assembly away from the system's
  // centroid.  Others never move — a layout that walks teaches nothing.
  const offsets = useMemo(() => {
    const out = new Map<string, [number, number, number]>();
    if (!explode || !selection.systemKey) return out;
    const members = assemblies.filter((a) => a.system_key === selection.systemKey);
    const c = new THREE.Vector3();
    for (const m of members) {
      const s = layout.slots.get(m.key);
      if (s) c.add(new THREE.Vector3(...s.pos));
    }
    c.divideScalar(Math.max(1, members.length));
    for (const m of members) {
      const s = layout.slots.get(m.key);
      if (!s) continue;
      const dir = new THREE.Vector3(...s.pos).sub(c);
      if (dir.lengthSq() < 1e-6) dir.set(0, 1, 0);
      dir.normalize().multiplyScalar(1.1);
      out.set(m.key, [dir.x, dir.y + 0.4, dir.z]);
    }
    return out;
  }, [explode, selection.systemKey, assemblies, layout]);

  // Fly to the selected assembly (or back out to the whole rig).
  // The callback ref (below) handles the FIRST frame — this effect can
  // run before CameraControls mounts inside the Canvas.
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    if (selection.assemblyKey) {
      const s = layout.slots.get(selection.assemblyKey);
      if (s) {
        const [x, y, z] = s.pos;
        void c.setLookAt(x + 4, y + 2.5, z + 4, x, y, z, true);
      }
    } else {
      void c.setLookAt(14, 8, 14, -1, 1, 0, true);
    }
  }, [selection.assemblyKey, layout]);

  const dimFor = (a: Assembly): number => {
    if (matches) return matches.has(a.key) ? 1 : 0.1;
    if (!selection.systemKey) return 1;
    return a.system_key === selection.systemKey ? 1 : 0.1;
  };

  // A 1-character query matches nearly everything — 112 simultaneous
  // DOM label portals is churn, not information.  Labels in search
  // mode only when the result set is readable.
  const searchLabels = matches !== null && matches.size <= 24;

  return (
    <Canvas
      shadows={false}
      camera={{ position: [14, 8, 14], fov: 40 }}
      onPointerMissed={() => onPick(null)}
    >
      <ambientLight intensity={0.9} />
      <directionalLight position={[10, 14, 8]} intensity={0.9} />
      <GhostChassis tokens={tokens} />
      {assemblies.map((a) => {
        const slot = layout.slots.get(a.key);
        if (!slot) return null;
        const dim = dimFor(a);
        return (
          <AssemblyNode
            key={a.key}
            assemblyKey={a.key}
            label={a.label}
            slot={slot}
            tokens={tokens}
            state={{
              dim,
              selected: selection.assemblyKey === a.key,
              labeled: dim === 1 && (
                matches !== null ? searchLabels : selection.systemKey !== null
              ),
              offset: offsets.get(a.key) ?? [0, 0, 0],
            }}
            onClick={() => onPick(a.key)}
          />
        );
      })}
      {/* Floating shelf-cluster captions: which system a parked group
          belongs to, until it graduates onto the chassis. */}
      {[...layout.clusters.entries()].map(([systemKey, pos]) => (
        <Html
          key={systemKey}
          center
          zIndexRange={[10, 0]}
          position={[pos[0], pos[1] + 1.3, pos[2] + 0.9]}
          className="pointer-events-none select-none"
          distanceFactor={16}
        >
          <span className="text-3xs uppercase tracking-wide text-muted-foreground whitespace-nowrap">
            {systemLabels.get(systemKey) ?? systemKey}
          </span>
        </Html>
      ))}
      <CameraControls
        ref={(c) => {
          controls.current = c;
          // First mount: jump (not fly) to the resting view so a warm
          // react-query cache can't leave the camera staring at 0,0,0.
          if (c && !cameraInitialised.current) {
            cameraInitialised.current = true;
            void c.setLookAt(14, 8, 14, -1, 1, 0, false);
          }
        }}
        makeDefault
        maxPolarAngle={Math.PI / 2.05}
      />
    </Canvas>
  );
}

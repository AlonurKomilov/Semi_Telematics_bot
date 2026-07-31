/**
 * The ghost rig — a stylized TRACTOR and TRAILER from primitives, just
 * enough silhouette to give every assembly a believable home.  It is
 * scenery, not subject: translucent, token-coloured, never clickable.
 *
 * The two vehicles render UNCOUPLED (trailer group shifted aft by
 * TRAILER_SHIFT) — the same worldview as the Vehicle registry, and the
 * gap is what makes the coupling hardware (fifth wheel, kingpin,
 * landing gear) teachable instead of buried in an overlap.
 */
import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { SceneTokens } from './colors';
import { GROUND, TRAILER_SHIFT } from './layout';

export default function GhostChassis({ tokens }: { tokens: SceneTokens }) {
  const body = useMemo(() => new THREE.MeshStandardMaterial({
    color: tokens.mutedForeground, transparent: true, opacity: 0.14,
    roughness: 0.9, depthWrite: false,
  }), [tokens.mutedForeground]);
  const wheel = useMemo(() => new THREE.MeshStandardMaterial({
    color: tokens.foreground, transparent: true, opacity: 0.18,
    roughness: 1, depthWrite: false,
  }), [tokens.foreground]);
  // r3f auto-disposes JSX-created objects, not ones we `new` — every
  // theme change would otherwise leak the previous shader programs.
  useEffect(() => () => body.dispose(), [body]);
  useEffect(() => () => wheel.dispose(), [wheel]);

  const tractorAxles = [7.4, 2.6, 1.4];       // steer + drives
  const trailerAxles = [-9.6, -10.8];         // tandems (trailer-local)

  const axlePair = (x: number, i: number) => (
    <group key={i}>
      <mesh material={wheel} position={[x, 0.5, 1.05]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.5, 0.5, 0.42, 20]} />
      </mesh>
      <mesh material={wheel} position={[x, 0.5, -1.05]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.5, 0.5, 0.42, 20]} />
      </mesh>
    </group>
  );

  return (
    <group>
      {/* ══ TRACTOR — a complete vehicle on its own ══ */}
      <group>
        {/* frame rails */}
        <mesh material={body} position={[3.5, 0.55, 0.45]}>
          <boxGeometry args={[9.4, 0.16, 0.12]} />
        </mesh>
        <mesh material={body} position={[3.5, 0.55, -0.45]}>
          <boxGeometry args={[9.4, 0.16, 0.12]} />
        </mesh>
        {/* hood + cab + sleeper */}
        <mesh material={body} position={[7.0, 1.35, 0]}>
          <boxGeometry args={[2.2, 1.1, 1.9]} />
        </mesh>
        <mesh material={body} position={[5.0, 1.95, 0]}>
          <boxGeometry args={[1.8, 2.3, 2.2]} />
        </mesh>
        <mesh material={body} position={[3.4, 1.8, 0]}>
          <boxGeometry args={[1.4, 2.0, 2.2]} />
        </mesh>
        {/* fuel tanks */}
        <mesh material={body} position={[4.6, 0.55, 1.15]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.33, 0.33, 1.5, 16]} />
        </mesh>
        <mesh material={body} position={[4.6, 0.55, -1.15]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.33, 0.33, 1.5, 16]} />
        </mesh>
        {/* fifth wheel — the coupling plate, now in the open */}
        <mesh material={body} position={[0.4, 0.72, 0]} rotation={[0, 0, -0.06]}>
          <boxGeometry args={[1.1, 0.1, 1.0]} />
        </mesh>
        {tractorAxles.map(axlePair)}
      </group>

      {/* ══ TRAILER — a complete vehicle on its own, parked aft ══ */}
      <group position={[TRAILER_SHIFT, 0, 0]}>
        {/* box + rails */}
        <mesh material={body} position={[-5.6, 2.05, 0]}>
          <boxGeometry args={[13.4, 2.7, 2.5]} />
        </mesh>
        <mesh material={body} position={[-5.6, 0.6, 0]}>
          <boxGeometry args={[13.4, 0.14, 0.9]} />
        </mesh>
        {/* kingpin — under the nose, mate of the fifth wheel */}
        <mesh material={body} position={[0.2, 0.5, 0]}>
          <cylinderGeometry args={[0.07, 0.07, 0.24, 12]} />
        </mesh>
        {/* landing gear — legs DOWN: the trailer is standing on them */}
        <mesh material={body} position={[-0.9, 0.33, 0.55]}>
          <boxGeometry args={[0.16, 0.66, 0.16]} />
        </mesh>
        <mesh material={body} position={[-0.9, 0.33, -0.55]}>
          <boxGeometry args={[0.16, 0.66, 0.16]} />
        </mesh>
        {trailerAxles.map(axlePair)}
      </group>

      {/* ground plane, faint — geometry shared with the bounds test */}
      <mesh position={[GROUND.center[0], -0.01, GROUND.center[1]]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={GROUND.size} />
        <meshBasicMaterial color={tokens.border} transparent opacity={0.25} />
      </mesh>
    </group>
  );
}

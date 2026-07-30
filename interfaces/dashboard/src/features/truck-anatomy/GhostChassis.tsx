/**
 * The ghost rig — a stylized tractor + trailer from primitives, just
 * enough silhouette to give every assembly a believable home.  It is
 * scenery, not subject: translucent, token-coloured, never clickable.
 */
import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { SceneTokens } from './colors';

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

  const axles: Array<[number, number]> = [
    [7.4, 0],            // steer
    [2.6, 0], [1.4, 0],  // drives
    [-9.6, 0], [-10.8, 0], // trailer tandems
  ];

  return (
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
      {/* trailer box + rails */}
      <mesh material={body} position={[-5.6, 2.05, 0]}>
        <boxGeometry args={[13.4, 2.7, 2.5] } />
      </mesh>
      <mesh material={body} position={[-5.6, 0.6, 0]}>
        <boxGeometry args={[13.4, 0.14, 0.9]} />
      </mesh>
      {/* landing gear */}
      <mesh material={body} position={[-0.6, 0.32, 0]}>
        <boxGeometry args={[0.18, 0.6, 0.9]} />
      </mesh>
      {/* wheels */}
      {axles.map(([x], i) => (
        <group key={i}>
          <mesh material={wheel} position={[x, 0.5, 1.05]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.5, 0.5, 0.42, 20]} />
          </mesh>
          <mesh material={wheel} position={[x, 0.5, -1.05]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.5, 0.5, 0.42, 20]} />
          </mesh>
        </group>
      ))}
      {/* ground shadow plane, faint */}
      <mesh position={[0, -0.01, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[34, 32]} />
        <meshBasicMaterial color={tokens.border} transparent opacity={0.25} />
      </mesh>
    </group>
  );
}

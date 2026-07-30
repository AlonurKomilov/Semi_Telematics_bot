/**
 * One taxonomy node in the scene.
 *
 * Tries the real model at /models/truck-anatomy/<key>.glb; a missing
 * or broken file falls back to the labeled placeholder box — so the
 * page works with zero models on day one, and every GLB that later
 * lands under the right key replaces its box with NO code change.
 * That contract (see README.md here) is the whole asset pipeline.
 */
import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html, useGLTF } from '@react-three/drei';
import * as THREE from 'three';
import type { SceneTokens } from './colors';
import type { Slot } from './layout';

export interface NodeState {
  /** 1 = normal, <1 = ghosted by a selection elsewhere. */
  dim: number;
  selected: boolean;
  labeled: boolean;
  /** World-space offset applied when the system is exploded. */
  offset: [number, number, number];
}

/** Loader errors are EXPECTED (most keys have no GLB yet). */
class GlbBoundary extends Component<
  { fallback: ReactNode; children: ReactNode }, { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function Glb({ url, onMaterials }: {
  url: string;
  onMaterials: (mats: THREE.MeshStandardMaterial[]) => void;
}) {
  const gltf = useGLTF(url);
  // The cached gltf.scene is SHARED — clone the graph and its
  // materials so dim/selection can drive this instance without
  // repainting every other user of the cache.
  const scene = useMemo(() => {
    const cloned = gltf.scene.clone(true);
    const mats: THREE.MeshStandardMaterial[] = [];
    cloned.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.isMesh && mesh.material) {
        const m = (mesh.material as THREE.MeshStandardMaterial).clone();
        m.transparent = true;
        mesh.material = m;
        mats.push(m);
      }
    });
    onMaterials(mats);
    return cloned;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gltf.scene]);
  useEffect(() => () => {
    scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.isMesh) (mesh.material as THREE.Material).dispose();
    });
  }, [scene]);
  return <primitive object={scene} />;
}

export default function AssemblyNode({
  assemblyKey, label, slot, state, tokens, onClick,
}: {
  assemblyKey: string;
  label: string;
  slot: Slot;
  state: NodeState;
  tokens: SceneTokens;
  onClick: () => void;
}) {
  const group = useRef<THREE.Group>(null);
  const glbMats = useRef<THREE.MeshStandardMaterial[]>([]);
  const mat = useMemo(() => new THREE.MeshStandardMaterial({
    color: tokens.card, roughness: 0.6, metalness: 0.05,
    transparent: true,
  }), [tokens.card]);
  useEffect(() => () => mat.dispose(), [mat]);
  // Hoisted lerp targets — constructing (and CSS-parsing) a Color
  // inside useFrame is ~6,700 allocations/second across 112 nodes.
  const targets = useMemo(() => ({
    primary: new THREE.Color(tokens.primary),
    positioned: new THREE.Color(tokens.mutedForeground),
    shelf: new THREE.Color(tokens.border),
    black: new THREE.Color('#000000'),
  }), [tokens]);

  // Damp toward the exploded/rest position and the target opacity —
  // springs without a spring library.
  useFrame((_, delta) => {
    const g = group.current;
    if (!g) return;
    const t = 1 - Math.exp(-delta * 8);
    g.position.x += (slot.pos[0] + state.offset[0] - g.position.x) * t;
    g.position.y += (slot.pos[1] + state.offset[1] - g.position.y) * t;
    g.position.z += (slot.pos[2] + state.offset[2] - g.position.z) * t;
    const targetOpacity = state.dim < 1 ? 0.15 : 0.95;
    mat.opacity += (targetOpacity - mat.opacity) * t;
    const target = state.selected ? targets.primary
      : slot.positioned ? targets.positioned : targets.shelf;
    mat.color.lerp(target, t);
    // A REAL model obeys the same rules as its placeholder: it ghosts
    // with the rest and glows via emissive when selected (tinting
    // base colour would wreck textures).
    for (const m of glbMats.current) {
      m.opacity += (targetOpacity - m.opacity) * t;
      m.emissive.lerp(state.selected ? targets.primary : targets.black, t);
      m.emissiveIntensity = state.selected ? 0.35 : 0;
    }
  });

  const placeholder = (
    <mesh material={mat} castShadow>
      <boxGeometry args={slot.size} />
    </mesh>
  );

  return (
    <group
      ref={group}
      position={slot.pos}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; }}
      onPointerOut={() => { document.body.style.cursor = 'auto'; }}
    >
      <GlbBoundary fallback={placeholder}>
        <Suspense fallback={placeholder}>
          <Glb
            url={`/models/truck-anatomy/${assemblyKey}.glb`}
            onMaterials={(mats) => { glbMats.current = mats; }}
          />
        </Suspense>
      </GlbBoundary>
      {state.labeled && state.dim === 1 && (
        <Html
          center
          distanceFactor={12}
          zIndexRange={[10, 0]}
          position={[0, slot.size[1] / 2 + 0.35, 0]}
          className="pointer-events-none select-none"
        >
          <span className={`px-1.5 py-0.5 rounded border text-2xs whitespace-nowrap ${
            state.selected
              ? 'bg-primary text-primary-foreground border-primary'
              : 'bg-card/90 text-foreground border-border'
          }`}>
            {label}
          </span>
        </Html>
      )}
    </group>
  );
}

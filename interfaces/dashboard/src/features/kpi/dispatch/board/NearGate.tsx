/**
 * Mounts its children only when near the viewport; until then it holds
 * their EXACT final height, so page length, scrollbar and layout never
 * move (the CLS contract).  One gate per dispatcher section — see
 * [useNearViewport.ts](useNearViewport.ts) for the trigger's rules.
 *
 * Collapsing a section unmounts its gate, so expanding re-arms the
 * watcher: on-screen sections fire immediately, off-screen ones wait —
 * which is what makes "Expand all" cost the visible sections only.
 */
import type { ReactNode } from 'react';
import { useNearViewport } from './useNearViewport';

export function NearGate({ index, height, children }: {
  /** Section position — paces the no-observer fallback stagger. */
  index: number;
  /** The body's exact final height in px (header 32 + rows × 144). */
  height: number;
  children: ReactNode;
}) {
  const { near, ref } = useNearViewport(index);
  if (near) return <>{children}</>;
  return <div ref={ref} style={{ height }} aria-hidden />;
}

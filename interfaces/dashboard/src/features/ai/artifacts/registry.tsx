/**
 * Artifact registry — `type` → React renderer.
 *
 * THE universal mechanism: a feature adds a new artifact type by calling
 * `registerArtifact('my_type', render)` from its own
 * `features/<x>/ai_artifacts/` folder (imported once in the artifacts
 * barrel).  No shared file changes, no central switch — same
 * registration philosophy as `@register_tool` and the retention hub.
 *
 * An unknown type degrades to a readable JSON fallback rather than
 * crashing, so a client that predates a new artifact type still renders
 * the message.
 */
import { type ReactNode } from 'react';
import type { Artifact } from './types';

export type ArtifactRenderer = (artifact: Artifact) => ReactNode;

const RENDERERS: Record<string, ArtifactRenderer> = {};

/** Register a renderer for an artifact `type`.  Idempotent — the last
 *  registration for a type wins (so a feature can override a built-in). */
export function registerArtifact(type: string, render: ArtifactRenderer): void {
  RENDERERS[type] = render;
}

/** True when some renderer can handle this artifact type. */
export function hasArtifactRenderer(type: string): boolean {
  return type in RENDERERS;
}

/** Render an artifact, or a safe fallback when its type is unknown. */
export function renderArtifact(artifact: Artifact): ReactNode {
  const render = RENDERERS[artifact.type];
  if (render) return render(artifact);
  return fallback(artifact);
}

function fallback(artifact: Artifact): ReactNode {
  return (
    <div className="rounded-md border border-border bg-muted/40 p-2 text-3xs text-muted-foreground">
      <div className="mb-1 font-medium">
        Unsupported result: {artifact.type}
      </div>
      <pre className="whitespace-pre-wrap break-all">
        {JSON.stringify(artifact, null, 2).slice(0, 600)}
      </pre>
    </div>
  );
}

/**
 * Artifacts barrel — the single import surface for the assistant.
 *
 * Importing this module runs the built-in renderers' `registerArtifact`
 * side effects (table, chart).  A FEATURE that ships its own artifact
 * type adds ONE import line here (or the renderer self-registers when
 * its feature bundle loads) — no other change:
 *
 *     import '../../<feature>/ai_artifacts/MyArtifact';
 *
 * Consumers import `renderArtifact` / types from here.
 */
import './TableArtifact';        // registers 'table'
import './ChartArtifact';        // registers 'chart'
import './ActionProposalCard';   // registers 'action_proposal' (write-action approve card)

export { renderArtifact, registerArtifact, hasArtifactRenderer } from './registry';
export type { Artifact, TableArtifact, ChartArtifact } from './types';

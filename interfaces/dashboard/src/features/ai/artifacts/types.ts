/**
 * Artifact contract — the structured outputs the assistant can render
 * natively (a real table, a chart) instead of flattening to prose.
 *
 * An artifact is a plain `{ type, ... }` object a tool attaches to its
 * result (backend) or the model emits.  The frontend `artifactRegistry`
 * maps `type` → a React renderer.  Adding a NEW artifact type is a
 * registration in the owning feature's `ai_artifacts/` folder — never a
 * change to shared code (see registry.ts).
 *
 * Unknown types degrade to a readable fallback, so an old client meeting
 * a new artifact type never crashes.
 */

/** The shared shape every artifact has. */
export interface BaseArtifact {
  type: string;
  /** Optional heading shown above the rendered artifact. */
  title?: string;
}

/** A tabular artifact — rendered through the app's DataGrid. */
export interface TableArtifact extends BaseArtifact {
  type: 'table';
  columns: { key: string; label: string }[];
  rows: Record<string, unknown>[];
}

/** A bar/line chart — rendered through recharts with the chart tokens. */
export interface ChartArtifact extends BaseArtifact {
  type: 'chart';
  chart: 'bar' | 'line';
  data: Record<string, unknown>[];
  /** Category axis key (x). */
  xKey: string;
  /** One entry per plotted series. */
  series: { key: string; label?: string }[];
}

/**
 * A staged-import preview — the shared shape EVERY import target's
 * propose step emits (built server-side by `build_import_preview`).
 * Rows here are a bounded display sample; the full staged rows live on
 * the proposal server-side and are what the executor writes.
 */
export interface ImportPreviewArtifact extends BaseArtifact {
  type: 'import_preview';
  /** ImportTarget name (e.g. 'inventory'). */
  target?: string;
  columns: { key: string; label: string }[];
  rows: Record<string, unknown>[];
  totals: { total: number; shown: number; skipped: number };
  /** Human-readable per-row skip reasons (bounded server-side). */
  skipped: string[];
  skipped_truncated?: boolean;
  /** Non-fatal adjustments (values coerced to vocabulary defaults) —
   *  the rows staged, but not exactly as mapped. */
  notices?: string[];
  /** Linked pending proposal — powers the editable preview (stamped by
   *  the router alongside the sibling action_proposal). */
  proposal_id?: string;
}

export type Artifact =
  | TableArtifact
  | ChartArtifact
  | ImportPreviewArtifact
  | BaseArtifact;

/** Narrowing helpers for renderers. */
export const isTable = (a: Artifact): a is TableArtifact => a.type === 'table';
export const isChart = (a: Artifact): a is ChartArtifact => a.type === 'chart';
export const isImportPreview = (a: Artifact): a is ImportPreviewArtifact =>
  a.type === 'import_preview';

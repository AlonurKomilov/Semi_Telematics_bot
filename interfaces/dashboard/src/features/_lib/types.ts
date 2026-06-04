/**
 * Per-persona page composition — shared types.
 *
 * The Pattern B model: each shared feature page declares a registry
 * of named sections (lazy-loaded components) and a layout map keyed
 * by persona.  The PageLayoutHost picks the active persona's layout
 * at render time and mounts only those sections — so a Fleet user
 * never downloads the JS chunks for Safety-only sections.
 *
 * Adding a new persona (e.g. ``hr``, ``accounting``) is a one-line
 * change in the ``Persona`` union.  Existing feature layout files
 * keep working — missing persona entries fall through to the
 * owner-superset layout at runtime, so no feature crashes when a
 * persona arrives before its tuned layout is written.
 */
import type { ComponentType, LazyExoticComponent } from 'react';

// Canonical persona keys used by every layout config.  Mirrors the
// ``activeView`` values written by RoleViewContext.  ``hr`` and
// ``accounting`` are reserved here ahead of the backend Role enum
// change so layout configs can be authored before the roles ship —
// see docs/architecture/per-persona-pattern (if/when added).
export type Persona =
  | 'owner'
  | 'admin'
  | 'fleet'
  | 'dispatcher'
  | 'safety'
  | 'driver'
  | 'hr'
  | 'accounting';

/**
 * A single section in a page layout.
 *
 *  - ``Component`` is the React.lazy wrapper so Vite emits a separate
 *    JS chunk per section; personas only download their own sections.
 *  - ``label`` is an optional human-friendly name surfaced by the
 *    layout-coverage audit script and the error boundary fallback.
 */
export interface SectionDef<P = Record<string, never>> {
  Component: LazyExoticComponent<ComponentType<P>>;
  label?: string;
}

/**
 * A feature's section registry — keys are stable section ids used
 * by every persona's layout array.
 */
export type SectionRegistry<P = Record<string, never>> = Record<string, SectionDef<P>>;

/**
 * A single layout — ordered list of section ids.  Order = visual top-
 * to-bottom order in the rendered page.
 */
export type Layout = string[];

/**
 * Layout-per-persona map.  Marked Partial so feature authors only
 * write the personas that need tuned views; missing personas fall
 * through to the owner-superset at runtime (see PageLayoutHost).
 */
export type LayoutMap = Partial<Record<Persona, Layout>>;

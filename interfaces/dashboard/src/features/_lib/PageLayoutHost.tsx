/**
 * Pattern B page renderer.
 *
 * Takes a feature's section registry + layouts and renders the
 * active persona's section list inside per-section Suspense + error
 * boundaries.  Sections are lazy-loaded so Vite emits a separate
 * chunk per section — personas only download their own.
 *
 * Resolution order for the active layout:
 *   1. ``layouts[persona]`` for the active persona
 *   2. ``layouts.owner`` (owner is the canonical superset)
 *   3. ``layouts.admin`` (next-best superset)
 *   4. every section in the registry (last-resort fallback so a brand-
 *      new persona never crashes the page mid-rollout)
 *
 * The runtime fallback is the safety net; the audit script
 * (``scripts/check-layout-coverage.mjs``) is the CI-time enforcement.
 */
import {
  Component,
  Suspense,
  type ComponentType,
  type ReactNode,
} from 'react';
import { useShellConfig } from '../../hooks/useShellConfig';
import type { LayoutMap, SectionRegistry } from './types';

interface PageLayoutHostProps<P extends object> {
  /** Section registry: id → lazy component. */
  registry: SectionRegistry<P>;
  /** Per-persona layouts. */
  layouts: LayoutMap;
  /** Props passed to every section component as a single object. */
  sectionProps: P;
  /** Placeholder rendered inside each section's Suspense while its chunk loads. */
  sectionFallback?: ReactNode;
}

export function PageLayoutHost<P extends object>({
  registry,
  layouts,
  sectionProps,
  sectionFallback,
}: PageLayoutHostProps<P>) {
  const { persona } = useShellConfig();
  const layout =
    layouts[persona] ??
    layouts.owner ??
    layouts.admin ??
    (Object.keys(registry) as string[]);

  return (
    <>
      {layout.map((sectionId) => {
        const def = registry[sectionId];
        if (!def) {
          // Layout references a section that isn't in the registry —
          // bad data, but we'd rather skip than crash the page.  Dev
          // build surfaces it; CI catches it via the layout-coverage
          // audit script.
          if (import.meta.env.DEV) {
            console.warn(
              `[PageLayoutHost] Layout for persona="${persona}" references unknown section: "${sectionId}"`,
            );
          }
          return null;
        }
        // ``LazyExoticComponent<ComponentType<P>>`` is renderable JSX
        // but its declared props type carries Lazy's ref-forwarding
        // bookkeeping that the page-level sectionProps don't satisfy.
        // Cast through ``unknown`` to a plain ComponentType<P> — at
        // runtime they're identical; at the type level this discards
        // the ref-forward overload we never use.
        const SectionComponent = def.Component as unknown as ComponentType<P>;
        return (
          <SectionErrorBoundary
            key={sectionId}
            sectionId={sectionId}
            label={def.label}
          >
            <Suspense fallback={sectionFallback ?? null}>
              <SectionComponent {...sectionProps} />
            </Suspense>
          </SectionErrorBoundary>
        );
      })}
    </>
  );
}

// ── Per-section error boundary ────────────────────────────────────
//
// Isolates a thrown error to the single section that produced it.
// A broken Fault Codes panel must not blank the whole Vehicle page;
// the surrounding sections render fine and the user can still act.

interface SectionErrorBoundaryProps {
  sectionId: string;
  label?: string;
  children: ReactNode;
}

interface SectionErrorBoundaryState {
  error: Error | null;
}

class SectionErrorBoundary extends Component<
  SectionErrorBoundaryProps,
  SectionErrorBoundaryState
> {
  state: SectionErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): SectionErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`[Section "${this.props.sectionId}"] render error:`, error);
  }

  render() {
    if (this.state.error) {
      const name = this.props.label || this.props.sectionId;
      return (
        <div className="bg-card border border-destructive/40 rounded-xl p-4 text-sm">
          <p className="font-medium text-destructive">
            "{name}" failed to render.
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {this.state.error.message}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

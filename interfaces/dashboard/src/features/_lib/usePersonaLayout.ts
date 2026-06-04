/**
 * Resolve the active persona's section list from a LayoutMap.
 *
 * Most callers should use <PageLayoutHost> directly — this hook is
 * for pages that need to know which sections are about to render
 * (e.g. to conditionally enable a page-level "Print all sections"
 * button only when the print-friendly section is in the layout).
 *
 * Resolution order:
 *
 *   persona → owner → admin → empty array.
 *
 * NOTE: PageLayoutHost has a 4th "render every registry section"
 * last-resort fallback that this hook does NOT mirror, because the
 * hook has no registry handle.  Callers that need the same
 * last-resort semantics should provide their own fallback list.
 * This divergence is intentional but is the kind of subtle gotcha
 * worth knowing about.
 */
import { useShellConfig } from '../../hooks/useShellConfig';
import type { LayoutMap } from './types';

export function usePersonaLayout(layouts: LayoutMap): string[] {
  const { persona } = useShellConfig();
  return layouts[persona] ?? layouts.owner ?? layouts.admin ?? [];
}

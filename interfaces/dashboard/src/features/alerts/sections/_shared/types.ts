/**
 * Uniform props every Alerts section accepts.
 *
 * Empty by design — sections are self-contained:
 *   • Filter state via ``useAlertsFilters()`` (URL params)
 *   • Selection + bulk UI state via ``useAlertsSelection()`` (Context)
 *   • Server data via ``useQuery(['alerts', urlParams])`` — TanStack
 *     Query dedupes the shared key so the same /alerts request fires
 *     once regardless of how many sections subscribe
 *
 * The page wrapper doesn't thread any data through sectionProps; it
 * just provides the AlertsSelectionProvider and renders
 * PageLayoutHost.  That keeps section composition decoupled — adding
 * a new section means writing the file, registering it, and adding
 * it to a layout.  No prop-shape changes.
 */
export type AlertsSectionProps = Record<string, never>;

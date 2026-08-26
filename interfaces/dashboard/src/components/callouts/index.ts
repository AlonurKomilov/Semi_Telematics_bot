/**
 * Callouts — the app's PERSISTENT message lane (shared chrome, any
 * feature may use it).  Its sibling `components/banners` owns the
 * transient lane; both read one tone vocabulary from `lib/status`, so
 * a warning wears the same colour and icon whichever lane it lands in.
 *
 *   Callout        pinned strip — page or card
 *   CalloutGroup   several occurrences of ONE callout, said once
 *   CalloutInline  stands in for an empty value
 *   CalloutChip    compact, for a table cell
 *   byEntity       group a response's callouts for row lookup
 */
export { default as Callout } from './Callout';
export { default as CalloutGroup } from './CalloutGroup';
export { default as CalloutInline } from './CalloutInline';
export { default as CalloutChip } from './CalloutChip';
export { useCallout, resolveCallout, CALLOUT_LINES } from './useCallout';
export type { ResolvedCallout, CalloutLine, CalloutLineName } from './useCallout';
export {
  CALLOUT_CATALOG, byEntity, calloutSpec, defaultDismiss, dismissBehaviour,
} from './calloutCatalog';
export type {
  CalloutData, CalloutDismiss, CalloutKind, CalloutSpec,
} from './calloutCatalog';

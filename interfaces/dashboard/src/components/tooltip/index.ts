/**
 * The tooltip family — single source of truth for "hover reveals info".
 *
 * Raw primitive: components/ui/tooltip.tsx (bubble, positioning, theme).
 * Compositions live HERE:
 *   Tip       — one-liner label tooltip; THE replacement for title=
 *   InfoTip   — ⓘ icon for learn-once field explanations (helper text)
 *   Freshness — data-freshness indicator (timeago + staleness cue)
 * Root TooltipProvider is mounted once in main.tsx.
 */
export { Tip } from './Tip';
export { Freshness } from './Freshness';
export { InfoTip } from './InfoTip';

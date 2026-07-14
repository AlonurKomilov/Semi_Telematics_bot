/**
 * Tip — the one-liner tooltip for the 90% case, and THE replacement for
 * native ``title=`` attributes (browser tooltips are unthemed, delayed
 * ~1s, inconsistent per OS, and invisible on touch):
 *
 *     <Tip label="Export to CSV"><Button …><Download /></Button></Tip>
 *
 * The trigger props are merged ONTO the child element itself (Base UI
 * ``render`` composition) — no wrapper element, so layout, flex gaps,
 * and existing trigger behaviour (menus, popovers) are untouched.
 * Keep ``aria-label`` on icon-only children: the tooltip is a sighted-
 * hover affordance, not the accessible name.
 *
 * The app root mounts a single TooltipProvider (main.tsx) — do not add
 * per-instance providers.
 */
import type { ReactElement, ReactNode } from 'react';
import { Tooltip, TooltipTrigger, TooltipContent } from '../ui/tooltip';

interface TipProps {
  /** Tooltip text (or small formatted content). Empty/undefined → the
   *  child renders untouched, no tooltip wiring at all. */
  label?: ReactNode;
  /** Bubble placement relative to the child (default "top"). */
  side?: 'top' | 'bottom' | 'left' | 'right';
  /** A single element the tooltip attaches to. */
  children: ReactElement;
}

export function Tip({ label, side = 'top', children }: TipProps) {
  if (label == null || label === '') return children;
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  );
}

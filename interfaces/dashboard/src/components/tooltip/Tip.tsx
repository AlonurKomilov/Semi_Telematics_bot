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
  /** Anchor the bubble to the MOUSE CURSOR (just below it) — the
   *  editor/VS Code feel and the family DEFAULT: the bubble appears
   *  where the user is actually pointing, never across the component.
   *  Pass false for element-anchored placement (e.g. when a design
   *  needs the bubble pinned to the control). */
  followCursor?: boolean;
  /** A single element the tooltip attaches to. */
  children: ReactElement;
}

export function Tip({ label, side, followCursor = true, children }: TipProps) {
  if (label == null || label === '') return children;
  return (
    <Tooltip trackCursorAxis={followCursor ? 'both' : 'none'}>
      <TooltipTrigger render={children} />
      <TooltipContent
        // Native-tooltip geometry: the bubble's TOP-LEFT corner sits just
        // right of the cursor tip and slightly below it — the WHOLE bubble
        // is right of the pointer (side="right" = to the right; align
        // "start" = top edge at cursor level; alignOffset pushes it under
        // the cursor tip).  Never centered under the pointer.
        side={side ?? (followCursor ? 'right' : 'top')}
        sideOffset={followCursor ? 12 : 4}
        align={followCursor ? 'start' : 'center'}
        alignOffset={followCursor ? 14 : 0}
        showArrow={!followCursor}
      >
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

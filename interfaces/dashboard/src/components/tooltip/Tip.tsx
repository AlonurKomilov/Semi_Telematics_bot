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
  /** Anchor the bubble to the MOUSE CURSOR instead of the element —
   *  the VS Code file-tree feel.  Use on WIDE targets (table cells,
   *  full-width text rows) where an element-centered bubble can open
   *  far from where the user is actually pointing.  Icon buttons and
   *  small values read better element-anchored (default). */
  followCursor?: boolean;
  /** A single element the tooltip attaches to. */
  children: ReactElement;
}

export function Tip({ label, side, followCursor = false, children }: TipProps) {
  if (label == null || label === '') return children;
  return (
    <Tooltip trackCursorAxis={followCursor ? 'both' : 'none'}>
      <TooltipTrigger render={children} />
      <TooltipContent
        side={side ?? (followCursor ? 'bottom' : 'top')}
        sideOffset={followCursor ? 14 : 4}
        showArrow={!followCursor}
      >
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

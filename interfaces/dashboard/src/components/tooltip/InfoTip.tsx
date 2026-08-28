/**
 * InfoTip — the ⓘ affordance for learn-once explanations.
 *
 * A TOGGLETIP, not a hover tooltip: the bubble opens on CLICK, stays
 * open while the user reads, closes on outside-click or Esc — and it is
 * anchored to the ⓘ icon itself, never to the cursor.  Deliberate
 * contrast with <Tip>/<Freshness> (hover, cursor-anchored): a hover
 * bubble vanishes the moment the mouse drifts, which is wrong for
 * multi-sentence explanations, and hover doesn't exist on touch.
 *
 *     <span className={labelCls}>Note <InfoTip label="saved to the history…" /></span>
 *
 * Icon semantics (keep them honest): ⓘ = neutral explanation (this
 * component) · (?) = how-to help · (!) = warnings ONLY — an (!) next to
 * a form label reads as an error.
 */
import type { ReactNode } from 'react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Info } from 'lucide-react';
import { iconSizeClass } from '@/lib/iconSize';

interface InfoTipProps {
  /** The explanation shown in the bubble. */
  label: ReactNode;
  /** Icon size — 12 for inline-with-small-label, 14 default, 16 titles. */
  size?: 12 | 14 | 16;
}

export function InfoTip({ label, size = 14 }: InfoTipProps) {
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger
        // A real button: keyboard-focusable, Enter/Space toggles, and
        // click-to-open works on touch where hover tooltips can't.
        aria-label="More information"
        // `p-1 -m-1` alone gives a 22x22 box at the default size=14 —
        // two pixels under the WCAG 2.5.8 floor — and it rides
        // --size-layout, so it falls further as the user shrinks the
        // UI. `min-*-tap` is the part that does not move. The clamp
        // costs ~2px of horizontal flow at size=14; the alternative was
        // a hand-tuned negative margin per icon size, which is a magic
        // constant that goes stale the first time a size is added.
        className="inline-flex items-center justify-center align-middle cursor-pointer rounded p-1 -m-1 min-h-tap min-w-tap text-muted-foreground/60 hover:text-muted-foreground data-[open]:text-foreground transition-colors"
      >
        <Info className={iconSizeClass(size)} />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        {/* Anchored to the ⓘ itself (element, not cursor) — the bubble
            visually belongs to the icon that was pressed. */}
        <PopoverPrimitive.Positioner side="bottom" align="start" sideOffset={6} className="isolate z-50">
          <PopoverPrimitive.Popup
            // Same bubble recipe as TooltipContent so the two read as one
            // family; slightly roomier padding since this holds sentences.
            className="z-50 w-fit max-w-xs rounded-md bg-foreground px-3 py-2 text-xs text-background shadow-lg outline-none data-[open]:animate-in data-[open]:fade-in-0 data-[open]:zoom-in-95 data-[closed]:animate-out data-[closed]:fade-out-0 data-[closed]:zoom-out-95"
          >
            {label}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

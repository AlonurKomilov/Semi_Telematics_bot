// Slider — the SSOT for "pick a value on a continuous range".
//
// Wraps @base-ui/react's Slider the same way context-menu wraps its Menu:
// the primitive owns keyboard operation, ARIA and pointer capture, this
// file owns the tokens.  Hand-rolling one would mean re-implementing
// arrow/Home/End/PageUp handling and `aria-valuetext` — the two things a
// slider is most often shipped without.
//
// THE TWO CALLBACKS ARE NOT INTERCHANGEABLE, and the difference is the
// reason this component exists rather than a raw <input type="range">:
//
//   onValueChange     fires on every pointer frame during a drag
//   onValueCommitted  fires once, on release (and on every keypress)
//
// A continuous control that writes a stored preference on `onValueChange`
// writes it ~60 times a second — sixty synchronous localStorage writes and
// sixty subscriber notifications for one gesture.  Preview live, persist
// on commit.  Call sites get both, deliberately named, so the split is
// hard to get wrong by accident.
import { Slider as Base } from '@base-ui/react/slider';
import { cn } from '@/lib/utils';

interface SliderProps {
  value: number;
  /** Live, per-frame. Use for preview — never to write a preference. */
  onValueChange?: (v: number) => void;
  /** Once, on release or keypress. Use for anything that persists. */
  onValueCommitted?: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  /** Accessible name — required; the track carries no text of its own. */
  'aria-label': string;
  /** Spoken instead of the raw number ("125 per cent" beats "1.25"). */
  formatValue?: (v: number) => string;
  className?: string;
}

const first = (v: number | readonly number[]): number =>
  (Array.isArray(v) ? v[0] : v as number);

export function Slider({
  value,
  onValueChange,
  onValueCommitted,
  min = 0,
  max = 1,
  step = 0.01,
  disabled = false,
  className = '',
  formatValue,
  'aria-label': ariaLabel,
}: SliderProps) {
  return (
    <Base.Root
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onValueChange={(v) => onValueChange?.(first(v))}
      onValueCommitted={(v) => onValueCommitted?.(first(v))}
      // Clicking the thumb left document.activeElement on <body>, so the
      // arrow keys — the only way to land on an exact value — did nothing
      // until you tabbed in from somewhere else. The keyboard support was
      // all there; the hand-off from mouse to keyboard was the broken
      // part, at precisely the moment someone wants to fine-tune.
      onPointerDown={(e) => {
        const input = (e.currentTarget as HTMLElement)
          .querySelector<HTMLElement>('input,[role="slider"]');
        // after the primitive's own pointer handling, not before it
        if (input) requestAnimationFrame(() => input.focus({ preventScroll: true }));
      }}
      className={cn('relative flex w-full touch-none select-none items-center', className)}
    >
      {/* The Control must be tall enough for the thumb, which the primitive
          centres on the TRACK with `top:50%; translate:-50% -50%` — so it
          overflows a track thinner than itself by half its height in each
          direction. h-5 clears an 18px thumb (size-3.5 + border-2); a
          shorter control lets it collide with whatever sits below.

          The track is `muted-foreground/35`, not `bg-muted`: muted is
          oklch(0.97) against a white popover, which measures 1.06:1 — a
          rail nobody can see. At /35 it is 1.57:1 on light and 1.73:1 on
          dark, comfortably above the app's own hairline `--border`
          (1.26 / 1.46), so the range reads without shouting. The
          component's own affordance is the thumb, which carries a
          `border-2 border-primary`. */}
      <Base.Control className="flex h-5 w-full items-center">
        <Base.Track className="h-1 w-full rounded-full bg-muted-foreground/35">
          <Base.Indicator className="h-full rounded-full bg-primary" />
          <Base.Thumb
            aria-label={ariaLabel}
            // The primitive hands the formatter (formattedValue, value,
            // index); we only ever want the raw number, so the adapter
            // stays here rather than at every call site.
            getAriaValueText={formatValue ? (_formatted, v) => formatValue(v) : undefined}
            className={cn(
              'size-3.5 rounded-full bg-background border-2 border-primary shadow-sm',
              // The painted thumb is 14px; the TARGET is the 24px square
              // this pseudo-element centres on it. Growing the thumb
              // itself would make the control look clumsy, and padding
              // is inert on a `size-*` box (border-box eats it), so the
              // hit area is drawn separately and invisibly. `size-tap`
              // rather than a scale step: a floor must not shrink with
              // the very control it belongs to — and rather than a bare
              // `[24px]`, because a named step is greppable and an
              // arbitrary one emits nothing if the scanner misses it.
              'relative after:absolute after:left-1/2 after:top-1/2 after:size-tap',
              "after:-translate-x-1/2 after:-translate-y-1/2 after:content-['']",
              // focus-WITHIN, not focus-visible: the primitive renders the
              // real focusable input inside this element, so a
              // `focus-visible:` rule here can never match and the
              // keyboard focus state would never paint.
              // ring-3 + /50 is the house focus treatment (five other controls in
              // this folder use it). The slider had its own ring-2 at full
              // alpha, which on a thumb this small read as the faintest ring
              // on the page rather than the boldest.
              'focus-within:outline-none focus-within:ring-3 focus-within:ring-ring/50 focus-within:ring-offset-1',
              // Disabled is a data attribute here, not the :disabled
              // pseudo-class — this is a div, which cannot be :disabled.
              'data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed',
            )}
          />
        </Base.Track>
      </Base.Control>
    </Base.Root>
  );
}

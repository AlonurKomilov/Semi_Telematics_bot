import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * The one-of-many member of the control family.
 *
 * CLAUDE.md names the split — "Checkbox = membership · Switch =
 * behaviour · pressed button = a behaviour in a bar" — and until now the
 * family had a primitive for every branch but this one. Radio answers a
 * fourth question none of them do: *which ONE of a visible set?* A
 * checkbox row for exclusive options invites two ticks; a select hides
 * the alternatives behind a click, which is wrong when the whole point
 * is comparing them side by side.
 *
 * Built at instance ONE, deliberately. The checkbox primitive's own
 * docstring records what waiting costs: thirty-five raw
 * `<input type="checkbox">` across ten different class strings, three
 * sizing idioms, two cursor idioms, and exactly two of them carrying the
 * tap floor. That backlog is still being paid down. Radio had two
 * call-sites when this was written, so the cheap moment was now.
 *
 * `min-h-tap min-w-tap` is not optional and not a call-site decision. A
 * native radio renders ~13px and, unlike `h-8` on a text input, cannot
 * be grown by a size class — the circle is drawn by the UA. It is also
 * the one step that does NOT scale with the Size control, which is the
 * whole reason design.md §5.1 requires it: a floor that shrank with
 * everything else would not be a floor.
 *
 * Deliberately a plain `<input>` rather than a styled div: it keeps the
 * native control's keyboard behaviour — arrow keys moving within the
 * group, and the roving tab stop a re-implementation almost never gets
 * right — plus form participation and the platform's own a11y tree.
 * `accent-primary` is what themes it.
 *
 * Group the inputs with a shared `name`; the visible label belongs in a
 * wrapping `<label>` so the whole row is the hit target, not just the
 * circle.
 */
function Radio({ className, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      // eslint-disable-next-line no-restricted-syntax -- this IS the primitive the rule points at
      type="radio"
      data-slot="radio"
      className={cn(
        'accent-primary cursor-pointer min-h-tap min-w-tap',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}

export { Radio };

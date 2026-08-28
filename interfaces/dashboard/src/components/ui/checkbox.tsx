import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * The membership half of the checkbox/switch split.
 *
 * CLAUDE.md states the rule — "Checkbox = membership · Switch =
 * behaviour" — and named a primitive for only one side of it. So the
 * switch half had one definition and the checkbox half had thirty-five
 * raw `<input type="checkbox">` across ten different class strings:
 * three sizing idioms, two cursor idioms, and exactly two of them
 * carrying the tap floor. A rule that points at a primitive on one side
 * and at nothing on the other is a rule that only gets followed half
 * the time.
 *
 * `min-h-tap min-w-tap` is not optional and not a call-site decision. A
 * native checkbox renders ~13px, and unlike `h-8` on a text input it
 * cannot be grown by a size class — the box is drawn by the UA. It is
 * also the one step that does not scale with the Size control, which is
 * the whole reason design.md §5.1 requires it: a floor that shrank with
 * everything else would not be a floor.
 *
 * Deliberately a plain `<input>` rather than a styled div: it keeps the
 * native control's keyboard behaviour, its indeterminate state, and its
 * participation in a form, none of which a re-implementation gets right
 * for free. `accent-primary` is what themes it.
 *
 * Use `<Switch>` instead when the answer is "is this BEHAVIOUR on?", and
 * never mix the two shapes in one vertical run — five identical boxes in
 * a column meaning two unrelated things is the pivot-panel bug the rule
 * was written about.
 */
function Checkbox({ className, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      type="checkbox"
      data-slot="checkbox"
      className={cn(
        'accent-primary cursor-pointer min-h-tap min-w-tap',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}

export { Checkbox };

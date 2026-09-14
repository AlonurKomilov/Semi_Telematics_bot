// Toggle switch — the SSOT for an on/off control.
//
// New code composes this instead of re-writing the track and knob, and
// `chrome.test.ts` holds that as a RULE rather than as a hope — because
// this comment used to claim it had already replaced the copies in
// Profile, Permissions, TeamManagement and DatatruckSyncPanel while all
// four were still drawing their own. Three were byte-identical to
// `size="md"` and one to `size="sm"`, so they looked settled while
// drifting independently: one of them painted a DISABLED switch grey
// whether it was on or off, which this does not, so a disabled manager
// read as not a manager — and nothing could catch it, because there was
// nothing to compare against.
//
// One file draws `role="switch"` and is right to. The alert matrix's
// ChannelPill answers the same question in a WORD — On / Off / N/A —
// because a channel that cannot deliver should say so rather than dim
// an empty box the reader has to interpret. It is named in
// `SWITCH_BY_DESIGN` with that reason, not carried as debt.
//
// On = `bg-primary` (the app's established switch-on colour, NOT a
// semantic hue) so one convention reads as "on" everywhere.
//
//   size="sm"  → w-8 h-4  (dense matrices, per-row toggles)
//   size="md"  → h-6 w-11 (standalone settings rows)

interface SwitchProps {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  size?: 'sm' | 'md';
  /** Accessible name — required (the switch has no visible text). */
  'aria-label': string;
  className?: string;
}

export function Switch({
  checked,
  onCheckedChange,
  disabled = false,
  size = 'md',
  className = '',
  'aria-label': ariaLabel,
}: SwitchProps) {
  const sm = size === 'sm';
  // THE HIT BOX AND THE PAINTED TRACK ARE TWO ELEMENTS, on purpose.
  //
  // `size="sm"` paints a 32x16 track. That is a pointer target 8px under
  // the WCAG 2.5.8 minimum, and it fails the spacing exception exactly
  // where it is used most: the alert-routing topic matrix stacks these
  // `space-y-1`, which measures a 20px centre-to-centre pitch, well
  // inside the 24px circle the exception requires to be clear.
  //
  // Growing the track would change what a switch LOOKS like everywhere.
  // So the <button> keeps the role and the aria, drops all paint, and
  // becomes the 24px target; an inner span carries the visuals at their
  // original size. `min-*-tap` rather than a scale step because a floor
  // that shrinks with the Size control is not a floor — see design.md §5.1.
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => { if (!disabled) onCheckedChange(!checked); }}
      className={`shrink-0 inline-flex items-center justify-center rounded-full
        min-h-tap min-w-tap
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1
        disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
    >
      <span
        aria-hidden
        className={`relative inline-flex items-center rounded-full transition
          ${sm ? 'w-8 h-4' : 'h-6 w-11'}
          ${checked ? 'bg-primary' : 'bg-muted-foreground/30'}`}
      >
        <span
          className={`inline-block rounded-full bg-background shadow transition-transform
            ${sm ? 'w-3 h-3' : 'h-5 w-5'}
            ${checked
              ? (sm ? 'translate-x-4' : 'translate-x-5')
              : 'translate-x-0.5'}`}
        />
      </span>
    </button>
  );
}

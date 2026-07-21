// Toggle switch — the SSOT for an on/off control.
//
// The app hand-rolled this markup in ~4 places (Profile, Permissions,
// TeamManagement, DatatruckSyncPanel) at two sizes.  This is the shared
// primitive; new code composes it instead of re-writing the track/knob.
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
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => { if (!disabled) onCheckedChange(!checked); }}
      className={`shrink-0 relative inline-flex items-center rounded-full transition
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1
        disabled:opacity-50 disabled:cursor-not-allowed
        ${sm ? 'w-8 h-4' : 'h-6 w-11'}
        ${checked ? 'bg-primary' : 'bg-muted-foreground/30'} ${className}`}
    >
      <span
        className={`inline-block rounded-full bg-background shadow transition-transform
          ${sm ? 'w-3 h-3' : 'h-5 w-5'}
          ${checked
            ? (sm ? 'translate-x-4' : 'translate-x-5')
            : 'translate-x-0.5'}`}
      />
    </button>
  );
}

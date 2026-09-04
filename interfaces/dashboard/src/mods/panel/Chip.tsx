/**
 * The chip — one option in a row of them.
 *
 * Extracted from `ModPanel.tsx` unchanged when that file passed a
 * thousand lines. It is the panel's only shared primitive: every
 * category renders rows of these, so it lives beside them rather than
 * inside any one of them.
 */
import { cn } from '../../lib/utils';

export function Chip<T extends string>({
  value,
  current,
  label,
  dot,
  onClick,
}: {
  value: T;
  current: T;
  label: string;
  /** A CSS colour VALUE, not a class — the colour is data here, so it
   *  cannot be a Tailwind class name (those must be statically
   *  scannable). A `--swatch-*` token where the colour is ours to
   *  choose; a raw hex only where the person chose it, which is the
   *  one thing no token can name. */
  dot?: string;
  onClick: (v: T) => void;
}) {
  const active = value === current;
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      aria-pressed={active}
      className={cn(
        'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors min-h-tap',
        active
          ? 'bg-primary/15 text-foreground ring-1 ring-primary/40'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
      )}
    >
      {dot && (
        <span
          aria-hidden
          className="w-2.5 h-2.5 rounded-full shrink-0 border border-border"
          style={{ background: dot }}
        />
      )}
      {label}
    </button>
  );
}

import { useState, useRef, useEffect } from 'react';
import { Palette } from 'lucide-react';
import { Button } from './ui/button';
import { useTheme, type ColorTheme, type Density, type RadiusVariant } from '../context/ThemeContext';
import { cn } from '../lib/utils';

// ── Option rows ──────────────────────────────────────────────

const COLOR_OPTIONS: { value: ColorTheme; label: string; dot: string }[] = [
  { value: 'dark-blue',   label: 'Dark Blue',   dot: 'bg-blue-500' },
  { value: 'dark-purple', label: 'Dark Purple',  dot: 'bg-purple-500' },
  { value: 'dark-green',  label: 'Dark Green',   dot: 'bg-green-500' },
  { value: 'light',       label: 'Light',        dot: 'bg-yellow-100 border border-border' },
];

const DENSITY_OPTIONS: { value: Density; label: string }[] = [
  { value: 'compact',     label: 'Compact' },
  { value: 'default',     label: 'Default' },
  { value: 'comfortable', label: 'Roomy' },
];

const RADIUS_OPTIONS: { value: RadiusVariant; label: string }[] = [
  { value: 'sharp',   label: 'Sharp' },
  { value: 'rounded', label: 'Rounded' },
  { value: 'pill',    label: 'Pill' },
];

function Chip<T extends string>({
  value,
  current,
  label,
  dot,
  onClick,
}: {
  value: T;
  current: T;
  label: string;
  dot?: string;
  onClick: (v: T) => void;
}) {
  const active = value === current;
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className={cn(
        'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors',
        active
          ? 'bg-primary/15 text-primary ring-1 ring-primary/40'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
      )}
    >
      {dot && <span className={cn('w-2.5 h-2.5 rounded-full shrink-0', dot)} />}
      {label}
    </button>
  );
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 shrink-0"
        title="Theme"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Palette size={16} />
      </Button>

      {open && (
        <div className="absolute right-0 top-10 z-50 w-56 bg-popover border border-border rounded-xl shadow-xl p-3 space-y-3">

          {/* Color theme */}
          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Color</p>
            <div className="flex flex-wrap gap-1">
              {COLOR_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.color} label={o.label} dot={o.dot}
                  onClick={(v) => setTheme({ color: v })} />
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Density */}
          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Density</p>
            <div className="flex gap-1">
              {DENSITY_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.density} label={o.label}
                  onClick={(v) => setTheme({ density: v })} />
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Radius */}
          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Corners</p>
            <div className="flex gap-1">
              {RADIUS_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.radius} label={o.label}
                  onClick={(v) => setTheme({ radius: v })} />
              ))}
            </div>
          </div>

        </div>
      )}
    </div>
  );
}



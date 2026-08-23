import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Palette, RotateCcw, SlidersHorizontal } from 'lucide-react';
import { Button } from './ui/button';
import { Slider } from './ui/slider';
import { Tip } from './tooltip';
import { useTheme, applySize, type ColorTheme, type RadiusVariant } from '../context/ThemeContext';
import { SIZE_MIN, SIZE_MAX } from '../preferences';
import { cn } from '../lib/utils';

// ── Option rows ──────────────────────────────────────────────
//
// Labels are i18n KEYS with an English default (the house `t(key,
// fallback)` shape), not literal strings: this popover sits beside the
// language selector and was the only control up there that never
// translated.  Swatches are `--swatch-*` tokens — see index.css for why
// a raw `bg-blue-500` was both a rule violation and the wrong colour.

const COLOR_OPTIONS: { value: ColorTheme; key: string; label: string; dot: string }[] = [
  { value: 'dark-blue',   key: 'theme.dark_blue',   label: 'Dark Blue',   dot: 'var(--swatch-dark-blue)' },
  { value: 'dark-purple', key: 'theme.dark_purple', label: 'Dark Purple', dot: 'var(--swatch-dark-purple)' },
  { value: 'dark-green',  key: 'theme.dark_green',  label: 'Dark Green',  dot: 'var(--swatch-dark-green)' },
  // `theme.color_light`, not the older `theme.light`: that key is already
  // translated in all nine locales while these three siblings are not, so
  // reusing it rendered ONE translated chip beside three English ones in
  // every partially-translated locale.  A key of its own makes the whole
  // row fall back together.
  { value: 'light',       key: 'theme.color_light', label: 'Light',       dot: 'var(--swatch-light)' },
];

const RADIUS_OPTIONS: { value: RadiusVariant; key: string; label: string }[] = [
  { value: 'sharp',   key: 'theme.corners_sharp',   label: 'Sharp' },
  { value: 'rounded', key: 'theme.corners_rounded', label: 'Rounded' },
  { value: 'pill',    key: 'theme.corners_pill',    label: 'Pill' },
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
  /** A `--swatch-*` CSS value, not a class — the colour is data here, so
   *  it cannot be a Tailwind class name (those must be statically
   *  scannable) and must not be a literal (that is what the tokens fix). */
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
          ? 'bg-primary/15 text-primary ring-1 ring-primary/40'
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

export function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, setTheme, size, setSize } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // What the slider shows while a drag is in flight.  The stored value is
  // only written on release — see the note in ui/slider.tsx — so during a
  // drag the preference and the screen disagree by design, and this holds
  // the screen's value.  `null` means "not dragging, read the preference".
  const [dragging, setDragging] = useState<number | null>(null);
  const shown = dragging ?? size.global;

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
      {/* `theme.picker`, not the pre-existing `theme.toggle` ("Toggle
          theme") — this opens a menu of three settings, it does not
          flip one. */}
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 shrink-0"
        aria-label={t('theme.picker', 'Theme')}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Palette size={16} />
      </Button>

      {open && (
        <div className="absolute right-0 top-10 z-50 w-56 bg-popover border border-border rounded-xl shadow-xl p-3 space-y-3">

          {/* Color theme */}
          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              {t('theme.group_color', 'Color')}
            </p>
            <div className="flex flex-wrap gap-1">
              {COLOR_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.color} label={t(o.key, o.label)} dot={o.dot}
                  onClick={(v) => setTheme({ color: v })} />
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Size — replaces the Density chips, which changed nothing.
              The scale runs 100% → 150%, not around a midpoint: the lower
              half stays unavailable until the 24px hit-target floor is
              repaired (design.md §5.1). So the handle rests at its own
              minimum by default, and the live percentage beside the label
              is what tells the user the control is working — it is the
              only readout, which is why it sits in the label row rather
              than under the track. */}
          <div>
            {/* Label, current value and reset share one row. The range ends
                were spelled out under the track at first, which put a
                second "100%" directly below the current value whenever the
                slider sat at its minimum — which is the default, so most
                users would have met the confusing state first. */}
            <div className="flex items-center justify-between gap-2 mb-1.5">
              <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t('theme.group_size', 'Size')}
              </p>
              <div className="flex items-center gap-1.5">
                <span className="text-2xs tabular-nums text-muted-foreground">
                  {Math.round(shown * 100)}%
                </span>
                <Tip label={t('theme.size_reset', 'Reset')}>
                  <button
                    type="button"
                    onClick={() => { setDragging(null); setSize({ global: 1 }); }}
                    disabled={size.global === 1 && dragging === null}
                    aria-label={t('theme.size_reset', 'Reset')}
                    className="inline-flex size-5 min-h-tap min-w-tap items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
                  >
                    <RotateCcw size={12} />
                  </button>
                </Tip>
              </div>
            </div>
            <Slider
              value={shown}
              min={SIZE_MIN}
              max={SIZE_MAX}
              step={0.05}
              aria-label={t('theme.size_label', 'Interface size')}
              formatValue={(v) => `${Math.round(v * 100)}%`}
              // Live: paint straight to the DOM so the drag is smooth and
              // React is not re-rendered 60 times for one gesture.
              onValueChange={(v) => { setDragging(v); applySize({ ...size, global: v }); }}
              // Committed: now it becomes the stored preference.
              onValueCommitted={(v) => { setDragging(null); setSize({ global: v }); }}
            />
          </div>

          <div className="border-t border-border" />

          {/* Radius */}
          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              {t('theme.group_corners', 'Corners')}
            </p>
            <div className="flex gap-1">
              {RADIUS_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.radius} label={t(o.key, o.label)}
                  onClick={(v) => setTheme({ radius: v })} />
              ))}
            </div>
          </div>

          {/* Per-region sizing and the cross-device switch do not fit a
              w-56 popover, and they are settings rather than a quick
              toggle — design.md §7 forbids inventing an in-between
              width, so they live on the profile page instead. */}
          <div className="border-t border-border" />
          <Link
            to="/profile#appearance"
            onClick={() => setOpen(false)}
            className="flex items-center gap-1.5 text-2xs text-muted-foreground hover:text-foreground"
          >
            <SlidersHorizontal size={12} />
            {t('theme.size_by_region', 'Size by region…')}
          </Link>

        </div>
      )}
    </div>
  );
}



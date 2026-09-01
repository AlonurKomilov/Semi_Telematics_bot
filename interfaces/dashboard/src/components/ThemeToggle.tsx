import { useState, useRef, useEffect, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Palette, RotateCcw, SlidersHorizontal } from 'lucide-react';
import { Button } from './ui/button';
import { Slider } from './ui/slider';
import { Tip } from './tooltip';
import {
  useTheme, applySize, type Mode, type Accent, type RadiusVariant, type Material,
  type Motion,
} from '../context/ThemeContext';
import { SIZE_MIN, SIZE_MAX } from '../preferences';
import { cn } from '../lib/utils';
import {
  THEME_PACKS, THEME_MODS, THEME_MATERIALS, THEME_MOTIONS,
  activeModId, modById, type ThemeMod,
} from '../lib/themePacks';

// ── Option rows ──────────────────────────────────────────────
//
// Labels are i18n KEYS with an English default (the house `t(key,
// fallback)` shape), not literal strings: this popover sits beside the
// language selector and was the only control up there that never
// translated.  Swatches are `--swatch-*` tokens — see index.css for why
// a raw `bg-blue-500` was both a rule violation and the wrong colour.

// Two rows, because they answer two questions. One row of four —
// Dark Blue / Dark Purple / Dark Green / Light — read as a list of
// themes, but three of its chips set a mode AND an accent while the
// fourth set only a mode: Light looked like a kind of dark. It also made
// "Light with a green accent" impossible to express, though Light has
// always had an accent (its --primary is chromatic blue).
//
// `theme.dark` / `theme.light` are translated in all nine locales and
// had no call sites; the accent keys are the old `theme.dark_*` renamed,
// with the "dark" qualifier dropped from every translation. Net zero new
// English keys, which is what locales/parity.test.ts requires.
const MODE_OPTIONS: { value: Mode; key: string; label: string; dot: string }[] = [
  { value: 'dark',  key: 'theme.dark',  label: 'Dark',  dot: 'var(--swatch-mode-dark)' },
  { value: 'light', key: 'theme.light', label: 'Light', dot: 'var(--swatch-mode-light)' },
];

/**
 * Generated from the pack catalogue, not restated. This list was the
 * seventh place a new accent had to be added by hand, and the one whose
 * omission is invisible — everything else keeps working and the pack
 * simply never appears in the picker.
 *
 * A pack needs no translation to be added. `t(key, label)` takes the
 * label as its fallback, so a pack with no `theme.accent_<id>` key shows
 * its English name in every locale — which is the rule for feature and
 * theme names here anyway.
 */
const ACCENT_OPTIONS: { value: Accent; key: string; label: string; dot: string }[] =
  THEME_PACKS.map((p) => ({
    value: p.id as Accent,
    key: `theme.accent_${p.id}`,
    label: p.label,
    dot: `var(--swatch-accent-${p.id})`,
  }));

/**
 * A mod is a whole look, so it sits ABOVE the three axes it sets: pick a
 * look, then tweak. Touch any of those axes afterwards and the mod chip
 * un-highlights on its own — no "modified" state to keep, because the
 * axes ARE the state and a mod is only ever a way of writing them.
 */
const MOD_OPTIONS = THEME_MODS.map((m) => ({
  value: m.id,
  label: m.label,
  why: m.why,
  dot: `var(--swatch-accent-${m.accent})`,
  mod: m,
}));

/** What surfaces are made of. An axis, so it sits beside Corners rather
 *  than inside a look — a mod may set it, and so may the person. */
const MATERIAL_OPTIONS: { value: Material; key: string; label: string }[] =
  THEME_MATERIALS.map((m) => ({
    value: m,
    key: `theme.material_${m}`,
    label: m === 'solid' ? 'Solid' : 'Glass',
  }));

const MOTION_OPTIONS: { value: Motion; key: string; label: string }[] =
  THEME_MOTIONS.map((m) => ({
    value: m,
    key: `theme.motion_${m}`,
    label: m === 'default' ? 'Normal' : m === 'calm' ? 'Calm' : 'Snappy',
  }));

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

export function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, setTheme, size, setSize } = useTheme();

  // A mod is "on" only while every axis it declares still matches. Tweak
  // the corners and it goes off — what is applied is the axes, and the
  // mod was only the thing that wrote them.
  const activeMod = activeModId({
    accent: theme.accent, radius: theme.radius, size: size.global,
    material: theme.material, motion: theme.motion, icons: theme.icons,
  });
  const activeWhy = modById(activeMod)?.why ?? '';

  const applyMod = (m: ThemeMod) => {
    setTheme({
      accent: m.accent as Accent,
      ...(m.radius === undefined ? {} : { radius: m.radius }),
      ...(m.material === undefined ? {} : { material: m.material }),
      ...(m.motion === undefined ? {} : { motion: m.motion }),
      // Mod-only axes. The panel has no chip for either, so a mod is the
      // only way to reach them — and the only way back is another mod or
      // a reset, which is why neither may be irreversible or unreadable.
      ...(m.icons === undefined ? {} : { icons: m.icons }),
      ...(m.entrance === undefined ? {} : { entrance: m.entrance }),
    });
    if (m.size !== undefined) setSize({ global: m.size });
  };
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
        className="shrink-0"
        aria-label={t('theme.picker', 'Theme')}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Palette />
      </Button>

      {open && (
        <div
          // The picker holds its OWN size, like the /profile panel: it
          // lives in the `controls` region AND drives the global, so
          // without this the slider grows and slides under the pointer
          // mid-drag. A browser audit measured the same runaway here as
          // on /profile. The page behind the popover still previews.
          style={{
            '--size-region': 1,
            '--size-text': size.text * size.global,
            '--size-control': size.control * size.global,
            '--size-layout': size.layout * size.global,
            '--size-panel': size.panel * size.global,
          } as CSSProperties}
          className="absolute right-0 top-10 z-50 w-56 bg-popover border border-border rounded-xl shadow-xl p-3 space-y-3"
        >

          {/* Color theme */}
          {MOD_OPTIONS.length > 0 && (
            <>
              <div>
                <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
                  {t('theme.group_look', 'Look')}
                </p>
                <div className="flex flex-wrap gap-1">
                  {MOD_OPTIONS.map((o) => (
                    <Chip key={o.value} value={o.value} current={activeMod} label={o.label} dot={o.dot}
                      onClick={() => applyMod(o.mod)} />
                  ))}
                </div>
                {/* Only for the mod actually applied. A line under every
                    chip would crowd a panel that already carries three
                    controls; a line under the one in force says what you
                    just chose, which is when it is worth reading. */}
                {activeWhy && (
                  <p className="text-2xs text-muted-foreground mt-1.5">{activeWhy}</p>
                )}
              </div>

              <div className="border-t border-border" />
            </>
          )}

          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              {t('theme.group_color', 'Color')}
            </p>
            {/* Mode first, then accent. The rows are separate elements
                rather than one wrapped list so the two questions cannot
                re-flow into each other at any Size setting. */}
            <div className="flex flex-wrap gap-1">
              {MODE_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.mode} label={t(o.key, o.label)} dot={o.dot}
                  onClick={(v) => setTheme({ mode: v })} />
              ))}
            </div>
            <div className="flex flex-wrap gap-1 mt-1">
              {ACCENT_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.accent} label={t(o.key, o.label)} dot={o.dot}
                  onClick={(v) => setTheme({ accent: v })} />
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
                {t('theme.group_size', 'Interface size')}
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
                    <RotateCcw className="size-3" />
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

          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              {t('theme.group_material', 'Material')}
            </p>
            {/* Beside Corners, not inside Look: it is a property of the
                whole app, and a person may want glass without taking a
                mod's size and colour with it. */}
            <div className="flex flex-wrap gap-1">
              {MATERIAL_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.material} label={t(o.key, o.label)}
                  onClick={(v) => setTheme({ material: v })} />
              ))}
            </div>
          </div>

          <div>
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              {t('theme.group_motion', 'Motion')}
            </p>
            {/* A multiplier on every transition. Spinners and pulses are
                deliberately not on it — see index.css. */}
            <div className="flex flex-wrap gap-1">
              {MOTION_OPTIONS.map((o) => (
                <Chip key={o.value} value={o.value} current={theme.motion} label={t(o.key, o.label)}
                  onClick={(v) => setTheme({ motion: v })} />
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
            <SlidersHorizontal className="size-3" />
            {t('theme.size_by_region', 'Size by region…')}
          </Link>

        </div>
      )}
    </div>
  );
}



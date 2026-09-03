import { useState, useRef, useEffect, useMemo, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ChevronRight, Palette, RotateCcw, SlidersHorizontal, Volume2, VolumeX, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Slider } from '../components/ui/slider';
import { Switch } from '../components/ui/switch';
import { Tip } from '../components/tooltip';
import {
  useMods, applySize, type Mode, type Accent, type RadiusVariant, type Material,
  type Motion,
} from './context';
import { SIZE_MIN, SIZE_MAX } from '../preferences';
import { cn } from '../lib/utils';
import {
  THEME_PACKS, MODS, MOD_MATERIALS, MOD_MOTIONS, MOD_ICONS, FONT_PACKS,
  modMatchesAxes, modById, packById, modFootprint, motionPercent,
  type Mod, type ModIcons,
} from './catalogue';
import { SOUND_PACKS, armAudio, playCue, type SoundPack } from './sound/engine';
import { KEY_PACKS, KEY_LIMITS, keyPackById } from './sound/keys';
import { MODS_HREF } from './href';
import { accentTokens } from './theme/accent';
import { usePreference } from '../preferences';
import { undoableAction } from '../components/banners/stagedAction';

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
 * What a tone is called to somebody who is not reading our CSS.
 *
 * The token names are `ok` / `warn` / `danger` / `info` because that is
 * what they do in a stylesheet. "Your colour is too close to --ok" is
 * not a sentence anybody can act on.
 */
const TONE_NAMES: Record<string, string> = {
  ok: 'the success colour',
  warn: 'the warning colour',
  danger: 'the danger colour',
  info: 'the info colour',
};

/**
 * The colour nobody curated.
 *
 * The four packs beside it were each tuned by hand; this one is checked
 * by `accentTokens` at the moment it is picked, and it can come back
 * refused. That refusal is the feature — a primary button the colour of
 * `--danger` is a lie about what the button does — so it is SAID, not
 * swallowed, and the tone it collided with is named in words a person
 * can act on.
 *
 * A nudge is said too. The engine moves a colour's lightness a little to
 * get it clear of a tone, and a colour that came back slightly different
 * with no explanation is the kind of thing that reads as a bug.
 *
 * A native colour input rather than a hand-built wheel: it is the
 * platform's own picker, it is keyboard-accessible and localised for
 * free, and it costs nothing to ship. It sits invisibly ON the chip, so
 * the chip is the hit target and the input never has to be styled.
 */
function BrandChip({ brand, mode, wearing, onPick, onClear }: {
  brand?: string;
  mode: Mode;
  /** The pack seed currently painting, for the mode being worn. The
   *  picker opens HERE when nothing custom is set: a colour picker that
   *  opens on grey is a blank decision wearing the shape of a value, and
   *  the honest starting point is the colour already on the screen. */
  wearing: string;
  onPick: (hex: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const [refused, setRefused] = useState<string | null>(null);

  // What the stored colour actually does in the mode being worn. A hex
  // can clear the tones on near-black and collide on white, so "is a
  // custom colour painting right now" is a question about this mode, not
  // about whether the field is set.
  const worn = useMemo(
    () => (brand ? accentTokens(brand, mode) : null),
    [brand, mode],
  );
  const active = !!worn?.tokens;

  const pick = (hex: string) => {
    const r = accentTokens(hex, mode);
    if (!r.tokens) {
      setRefused(r.collidesWith ?? null);
      return;
    }
    setRefused(null);
    onPick(hex);
  };

  const note = refused
    ? t('theme.brand_refused', 'That colour reads as {{tone}}. Pick another.')
        .replace('{{tone}}', TONE_NAMES[refused] ?? refused)
    : worn?.movedFrom
      ? t('theme.brand_moved', 'Lightened away from {{tone}} so the two do not read alike.')
          .replace('{{tone}}', TONE_NAMES[worn.movedFrom] ?? worn.movedFrom)
      : brand && !active
        ? t('theme.brand_unworn', 'This colour cannot be worn in {{mode}} mode — the pack below is painting.')
            .replace('{{mode}}', mode)
        : null;

  return (
    <>
      <span className="relative inline-flex">
        <button
          type="button"
          aria-pressed={active}
          className={cn(
            'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors min-h-tap',
            active
              ? 'bg-primary/15 text-foreground ring-1 ring-primary/40'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
          )}
        >
          <span
            aria-hidden
            className="w-2.5 h-2.5 rounded-full shrink-0 border border-border"
            style={{ background: brand ?? 'var(--muted-foreground)' }}
          />
          {t('theme.accent_custom', 'Custom')}
        </button>
        {/* Over the whole chip, invisible, so the chip IS the control.
            `sr-only` would take it out of the pointer's way entirely. */}
        <input
          type="color"
          value={brand ?? wearing}
          onChange={(e) => pick(e.target.value)}
          aria-label={t('theme.accent_custom', 'Custom')}
          className="absolute inset-0 w-full min-h-tap opacity-0 cursor-pointer"
        />
      </span>
      {/* Offered whenever a colour is STORED, not only while it paints.
          Gating this on `active` stranded the exact person who most
          needs it: 80 hexes clear the tones on near-black and collide on
          white, so a colour picked in dark mode and then carried into
          light shows the advisory below with no way to act on it. */}
      {brand && (
        // An ACTION sitting in a row of SELECTIONS, so it does not wear
        // a selection's box. Same rule the rest of the app follows: one
        // shape per meaning class, or the eye has to read every element
        // to find out which are choices and which do something.
        <button
          type="button"
          onClick={() => {
            setRefused(null);
            const was = brand;
            onClear();
            // The colour is a hex somebody chose by eye. Losing it costs
            // them the search, not a click — and every other destructive
            // control on this surface (all five resets, and installing a
            // mod over hand-set axes) already offers the way back.
            undoableAction({
              label: 'Custom colour cleared',
              undo: async () => { if (was) onPick(was); },
            });
          }}
          className="inline-flex items-center gap-1 px-1 text-xs text-muted-foreground hover:text-foreground min-h-tap"
        >
          <X className="size-3" aria-hidden />
          {t('theme.brand_clear', 'Clear')}
        </button>
      )}
      {note && (
        <p className="basis-full text-2xs leading-snug text-muted-foreground mt-0.5">
          {note}
        </p>
      )}
    </>
  );
}

/**
 * A mod is a whole look, so it sits ABOVE the three axes it sets: pick a
 * look, then tweak. Touch any of those axes afterwards and the mod chip
 * un-highlights on its own — no "modified" state to keep, because the
 * axes ARE the state and a mod is only ever a way of writing them.
 */
const MOD_OPTIONS = MODS.map((m) => ({
  value: m.id,
  label: m.label,
  why: m.why,
  dot: `var(--swatch-accent-${m.accent})`,
  mod: m,
}));

/** What surfaces are made of. An axis, so it sits beside Corners rather
 *  than inside a look — a mod may set it, and so may the person. */
const MATERIAL_OPTIONS: { value: Material; key: string; label: string }[] =
  MOD_MATERIALS.map((m) => ({
    value: m,
    key: `mods.material_${m}`,
    label: m === 'solid' ? 'Solid' : 'Glass',
  }));

const MOTION_OPTIONS: { value: Motion; key: string; label: string }[] =
  MOD_MOTIONS.map((m) => ({
    value: m,
    key: `mods.motion_${m}`,
    label: m === 'default' ? 'Normal' : m === 'calm' ? 'Calm' : 'Snappy',
  }));

/**
 * Icon stroke weight — the axis that had no control.
 *
 * It was mod-only for a stated reason: "a mod whose every setting is
 * also a chip is a shortcut, not a look". The owner's call reverses
 * that, and the reason it is safe to reverse is that icons are not
 * really one axis. A pack decides WHICH glyphs; the weight decides how
 * heavy they are drawn. We ship one pack (lucide), so the weight is the
 * only part there is anything to choose about — and a second pack, when
 * it comes, becomes a row above this one rather than a redesign.
 */
/**
 * What each chip is drawn in, so the choice can be SEEN rather than read.
 *
 * A second copy of the stacks in `index.css`, and `font.test.ts` parses
 * the stylesheet and fails when the two disagree — the same bargain the
 * boot script and `MOTION_SCALE` already make. It cannot be read out of
 * the stylesheet at render time: the value lives on `:root[data-font=x]`,
 * and only one of those is applied at a time.
 */
const FONT_PREVIEW: Record<string, string> = {
  geist: "'Geist Variable', sans-serif",
  system: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  serif: "ui-serif, Georgia, Cambria, 'Times New Roman', serif",
  mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  rounded: "ui-rounded, 'SF Pro Rounded', 'Hiragino Maru Gothic ProN', 'Varela Round', system-ui, sans-serif",
};

const ICON_OPTIONS: { value: ModIcons; key: string; label: string }[] =
  MOD_ICONS.map((i) => ({
    value: i,
    key: `mods.icons_${i}`,
    label: i === 'hairline' ? 'Hairline' : i === 'regular' ? 'Regular' : 'Bold',
  }));

const RADIUS_OPTIONS: { value: RadiusVariant; key: string; label: string }[] = [
  { value: 'sharp',   key: 'mods.corners_sharp',   label: 'Sharp' },
  { value: 'rounded', key: 'mods.corners_rounded', label: 'Rounded' },
  { value: 'pill',    key: 'mods.corners_pill',    label: 'Pill' },
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

/**
 * Every mod control there is, in one place, rendered in two contexts.
 *
 * `compact` is the top-bar popover: the two axes a person changes often
 * — which mod is installed, and the colour it wears — plus a door to the
 * rest. Everything else is the /mods page, which has the width for it.
 *
 * One component rather than two, because a duplicated chip row is a
 * chip row that drifts: the panel and the page would answer the same
 * question differently within a release, and nothing would fail.
 */
/**
 * The categories, at runtime as well as in the type, so a guard can walk
 * them. A union alone is erased, and "adding a section forces a
 * decision" cannot be enforced against something that does not exist
 * when the test runs.
 *
 * `mods` is the container's own row, not a category — it is here because
 * `section="mods"` is how the card asks for that row.
 */
export const MOD_SECTIONS = ['mods', 'interface', 'effects', 'sounds'] as const;
export type ModSection = (typeof MOD_SECTIONS)[number];

export function ModControls({ compact = false, onNavigate, section }: {
  compact?: boolean;
  /** Compact only: let the popover close itself when the link is taken. */
  onNavigate?: () => void;
  /**
   * Render ONE category instead of all of them.
   *
   * Omitted means the whole set, which is what the popover's compact
   * branch wants and what the guards walk. A named section is the
   * /profile card asking for one block — and there the Card's own
   * headings carry the boundary, so the internal rules would be two
   * objects describing one line (design.md §6).
   */
  section?: ModSection;
}) {
  const { t } = useTranslation();
  const { theme, setTheme, size, setSize } = useMods();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');
  const { value: volume, setValue: setVolume } = usePreference('mods.sound.volume');
  // Read only, and only to REPORT it — the switch itself stays where it
  // is used. Two controls for one boolean is the object-map problem
  // this line exists to solve, not to repeat.
  const { value: alertSoundOn } = usePreference('dispatch.soundOn');
  const { value: uiSound, setValue: setUiSound } = usePreference('mods.sound.ui');
  const { value: keySound, setValue: setKeySound } = usePreference('mods.sound.keyboard');
  const { value: keyPack, setValue: setKeyPack } = usePreference('mods.sound.keyboard.pack');
  const { value: ambient, setValue: setAmbient } = usePreference('mods.ambient');

  /**
   * Hearing it is the only way to choose it, so every gesture in the
   * sound section plays something: picking a pack plays that pack, and
   * committing the slider plays at that level.
   *
   * The clicked pack's cue is played DIRECTLY rather than through the
   * stored one — `setValue` is async, so previewing after setting would
   * play the pack you just left.
   */
  const preview = (pack: SoundPack, at: number) => {
    armAudio();
    playCue(pack.cues.alert, at);
  };

  /** The level to come back to. Silencing and restoring must not cost
   *  somebody the level they set — a mute that resets to 100% is a mute
   *  people stop using. */
  const beforeMute = useRef(1);

  // The mod that is INSTALLED — read, not recomputed. A mod stays on
  // after you tweak an axis, because that is what installed means and
  // because the next thing a mod carries will be a sound pack, which
  // cannot be read back off the DOM to re-derive identity from.
  const activeMod = theme.mod ?? '';
  const installed = modById(activeMod);
  const activeWhy = installed?.why ?? '';
  // What it CARRIES, by category — GX shows an installed mod's footprint
  // as a checklist, and "what will this change?" had no answer here at
  // all. Only for the installed one: a footprint under every chip would
  // be four lines of prose in a w-56 popover.
  const activeCarries = installed ? modFootprint(installed) : [];
  // Whether a picked colour is what is actually painting, in the mode
  // being worn — not merely whether one is stored. The pack chips read
  // their highlight off this, because a chip highlighted while its block
  // stands down is pointing at a colour nobody can see.
  const brandWorn = useMemo(
    () => (theme.brand ? accentTokens(theme.brand, theme.mode).tokens !== null : false),
    [theme.brand, theme.mode],
  );
  // Whether what you see is still exactly what it asked for. A separate
  // question from identity, and the reason the two were split.
  const modified = installed !== undefined && !modMatchesAxes(installed, {
    accent: theme.accent, radius: theme.radius, size: size.global,
    material: theme.material, motion: theme.motion, icons: theme.icons,
    sound: soundPack,
  });

  const applyMod = (m: Mod) => {
    // Snapshot BEFORE the write. Installing a mod overwrites accent,
    // corners, material, motion, icon weight, size and sound in one
    // click — up to seven values somebody may have spent real time on,
    // and "let me just see what Wall looks like" is the most likely
    // reason anyone clicks here. The same helper guards SizeCard's
    // reset, for the same reason.
    const previous = {
      mod: theme.mod, accent: theme.accent, radius: theme.radius,
      material: theme.material, motion: theme.motion,
      icons: theme.icons, entrance: theme.entrance,
    };
    const previousSize = size.global;
    const previousSound = soundPack;
    setTheme({
      // Stored, so it survives an axis being edited afterwards. Clicking
      // an already-installed mod therefore RESTORES it — the useful
      // second meaning of the same gesture.
      mod: m.id,
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
    // Not part of the theme preference — sound is its own key, and a mod
    // sets the pack without touching the volume.
    if (m.sound !== undefined) setSoundPack(m.sound);
    if (m.size !== undefined) setSize({ global: m.size });

    undoableAction({
      label: `${m.label} installed`,
      undo: async () => {
        setTheme(previous);
        setSize({ global: previousSize });
        setSoundPack(previousSound);
      },
    });
  };


  // What the slider shows while a drag is in flight.  The stored value is
  // only written on release — see the note in ui/slider.tsx — so during a
  // drag the preference and the screen disagree by design, and this holds
  // the screen's value.  `null` means "not dragging, read the preference".
  const [dragging, setDragging] = useState<number | null>(null);
  const shown = dragging ?? size.global;

  // design.md §4's caps label is `text-xs font-medium`. The popover runs
  // one step tighter because seven of these stack inside w-56; the page
  // has the room, so there it is the canonical combo — the same words,
  // read at the size every other section label in the app is read at.
  const groupLabel = compact
    ? 'text-2xs font-semibold uppercase tracking-wide text-muted-foreground'
    : 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

  const has = (s: ModSection) => section === undefined || section === s;
  const rule = section === undefined
    ? <div className="border-t border-border" />
    : null;

  return (
    <>

      {/* The container's own row: installing a mod writes into every
          category below, which is why it is not one of them. */}
      {has('mods') && MOD_OPTIONS.length > 0 && (
        <>
          <div>
            <p className={`${groupLabel} mb-1.5`}>
              {t('mods.group_mods', 'Mods')}
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
              <p className="text-2xs text-muted-foreground mt-1.5">
                {activeWhy}
                {activeCarries.length > 0 && (
                  <>
                    <br />
                    <span className="capitalize">{activeCarries.join(' · ')}</span>
                  </>
                )}
                {/* Says what happened rather than scolding: the mod
                    is still installed, some of it has been changed,
                    and the way back is the chip you already see. */}
                {modified && (
                  <span className="text-muted-foreground/70">
                    {' · '}{t('mods.mod_edited', 'edited — tap again to restore')}
                  </span>
                )}
              </p>
            )}
          </div>

          {rule}
        </>
      )}

      {has('interface') && (
      <div>
        <p className={`${groupLabel} mb-1.5`}>
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
        {/* While a picked colour is what paints, NO pack chip is
            highlighted — the stylesheet has stood that pack's block
            down, so showing it selected would be showing a colour that
            is not on the screen. `theme.accent` is still stored and
            still what a Clear returns to. */}
        <div className="flex flex-wrap gap-1 mt-1">
          {ACCENT_OPTIONS.map((o) => (
            <Chip key={o.value} value={o.value} current={brandWorn ? ('' as Accent) : theme.accent}
              label={t(o.key, o.label)} dot={o.dot}
              onClick={(v) => setTheme({ accent: v })} />
          ))}
          <BrandChip
            brand={theme.brand}
            mode={theme.mode}
            wearing={(packById(theme.accent) ?? THEME_PACKS[0]).seed[theme.mode]}
            onPick={(hex) => setTheme({ brand: hex })}
            onClear={() => setTheme({ brand: undefined })}
          />
        </div>
      </div>
      )}

      {compact ? (
        <>
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
              <p className={groupLabel}>
                {t('mods.group_size', 'Interface size')}
              </p>
              <div className="flex items-center gap-1.5">
                <span className="text-2xs tabular-nums text-muted-foreground">
                  {Math.round(shown * 100)}%
                </span>
                <Tip label={t('mods.size_reset', 'Reset')}>
                  <button
                    type="button"
                    onClick={() => { setDragging(null); setSize({ global: 1 }); }}
                    disabled={size.global === 1 && dragging === null}
                    aria-label={t('mods.size_reset', 'Reset')}
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
              aria-label={t('mods.size_label', 'Interface size')}
              formatValue={(v) => `${Math.round(v * 100)}%`}
              // Live: paint straight to the DOM so the drag is smooth and
              // React is not re-rendered 60 times for one gesture.
              onValueChange={(v) => { setDragging(v); applySize({ ...size, global: v }); }}
              // Committed: now it becomes the stored preference.
              onValueCommitted={(v) => { setDragging(null); setSize({ global: v }); }}
            />
          </div>

          {/* Corners, material, motion, sound and sizing BY REGION do
              not fit a w-56 popover, and they are settings rather than a
              quick toggle — design.md §7 forbids inventing an in-between
              width, so they live on the Mods page instead.

              The global size slider stays here and is the one control
              deliberately in both places: "a bit bigger" is the case
              almost everyone wants, and making them open a page for it
              would be the wrong trade. The page does not repeat it —
              there, SizeCard owns size whole (global, per region, and
              the cross-device switch). */}
          <div className="border-t border-border" />
          {/* `min-h-tap` and the padding that earns it: this is the
              panel's only door to four of the seven axes, and as a bare
              `text-2xs` row its hit box was the line box — under the 24px
              WCAG 2.5.8 floor. `-my-1` gives the target back its height
              without spending any of the popover's vertical budget.
              The trailing chevron is what makes it read as a ROUTE
              rather than a caption under the controls. */}
          <Link
            to={MODS_HREF}
            onClick={onNavigate}
            className="flex items-center gap-1.5 text-2xs text-muted-foreground hover:text-foreground min-h-tap py-1 -my-1"
          >
            <SlidersHorizontal className="size-3 shrink-0" />
            {t('mods.all_customization', 'All customization…')}
            <ChevronRight className="size-3 shrink-0 ml-auto" />
          </Link>
        </>
      ) : (
        <>
          {rule}

      {/* Radius */}
      {has('interface') && (
      <>
      <div>
        <p className={`${groupLabel} mb-1.5`}>
          {t('mods.group_corners', 'Corners')}
        </p>
        <div className="flex gap-1">
          {RADIUS_OPTIONS.map((o) => (
            <Chip key={o.value} value={o.value} current={theme.radius} label={t(o.key, o.label)}
              onClick={(v) => setTheme({ radius: v })} />
          ))}
        </div>
      </div>

      <div>
        <p className={`${groupLabel} mb-1.5`}>
          {t('mods.group_material', 'Material')}
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
        <p className={`${groupLabel} mb-1.5`}>
          {t('mods.group_font', 'Typeface')}
        </p>
        {/* Each chip is SET IN ITS OWN FACE. A list of font names in one
            font tells you nothing — the whole question a person is
            asking here is "what does it look like", and the chip can
            simply answer it. */}
        <div className="flex flex-wrap gap-1">
          {FONT_PACKS.map((f) => (
            <span key={f.id} style={{ fontFamily: FONT_PREVIEW[f.id] }}>
              <Chip value={f.id} current={theme.font} label={f.label}
                onClick={(v) => setTheme({ font: v })} />
            </span>
          ))}
        </div>
        <p className="text-2xs text-muted-foreground mt-1.5">
          {FONT_PACKS.find((f) => f.id === theme.font)?.note ?? ''}
        </p>
      </div>

      <div>
        <p className={`${groupLabel} mb-1.5`}>
          {t('mods.group_icons', 'Icons')}
        </p>
        <div className="flex flex-wrap gap-1">
          {ICON_OPTIONS.map((o) => (
            <Chip key={o.value} value={o.value} current={theme.icons} label={t(o.key, o.label)}
              onClick={(v) => setTheme({ icons: v })} />
          ))}
        </div>
        {/* The pack is named rather than offered. One chip in a row is
            not a choice, and this codebase already says so about the
            catalogue — "four accents and two sound packs is not a
            catalogue". When a second pack ships, it becomes a chip row
            here and this line goes away. */}
        <p className="text-2xs text-muted-foreground mt-1.5">
          {t('mods.icons_pack', 'Lucide')}
        </p>
      </div>
      </>
      )}

      {has('effects') && (
      <div>
        {/* The header carries the intensity, exactly as Sound's does.
            GX gives every mods category a percentage; ours had one for
            Sound and one for Size and nothing here, even though motion
            has been a multiplier all along.

            It is INVERTED on the way out — see `motionPercent`. The
            stored scale multiplies duration, so calm is 1.6; every other
            percentage on this card means more of the thing named, and a
            "Motion 160%" that moves least would be the only one lying. */}
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <p className={groupLabel}>
            {t('mods.group_motion', 'Motion')}
          </p>
          <span className="text-2xs tabular-nums text-muted-foreground">
            {motionPercent(theme.motion)}%
          </span>
        </div>
        {/* A multiplier on every transition. Spinners and pulses are
            deliberately not on it — see index.css. */}
        <div className="flex flex-wrap gap-1">
          {MOTION_OPTIONS.map((o) => (
            <Chip key={o.value} value={o.value} current={theme.motion} label={t(o.key, o.label)}
              onClick={(v) => setTheme({ motion: v })} />
          ))}
        </div>

        {/* Ambient is an effect in the literal sense — it is a thing the
            app does on its own, over time, without being asked. It sits
            beside Motion rather than in Interface for that reason: a
            person looking for "what does this screen do while I am not
            here" is not looking under colours and corners. */}
        <div className="flex items-center justify-between gap-2 mt-2.5">
          <span className="text-xs text-foreground">
            {t('mods.ambient_label', 'Ambient mode')}
          </span>
          <Switch
            size="sm"
            checked={ambient}
            onCheckedChange={setAmbient}
            aria-label={t('mods.ambient_label', 'Ambient mode')}
          />
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t(
            'mods.ambient_hint',
            'After a few untouched minutes the page grows and the menus fade, so it reads from across the room. Alerts stay their own size.',
          )}
        </p>
      </div>
      )}

      {rule}

      {has('sounds') && (
      <div>
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <p className={groupLabel}>
            {t('mods.group_sound', 'Sound')}
          </p>
          <div className="flex items-center gap-1.5">
            <span className="text-2xs tabular-nums text-muted-foreground">
              {Math.round(volume * 100)}%
            </span>
            {/* Silence and restore, in one control. Zero is a real
                setting here rather than a disabled state — it is how
                a person quiets one screen without turning off each
                feature's own toggle. */}
            {/* Mute first, reset LAST — because the trailing control
                of a slider section's header means "return this
                section to its default" in every section, and SIZE
                already established that. A person who learns one
                header should not have to relearn the next. */}
            <Tip label={volume > 0 ? t('mods.sound_mute', 'Silence') : t('mods.sound_unmute', 'Unmute')}>
              <button
                type="button"
                onClick={() => {
                  if (volume > 0) { beforeMute.current = volume; setVolume(0); }
                  else setVolume(beforeMute.current || 1);
                }}
                aria-label={volume > 0 ? t('mods.sound_mute', 'Silence') : t('mods.sound_unmute', 'Unmute')}
                className="inline-flex size-5 min-h-tap min-w-tap items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60"
              >
                {volume > 0 ? <VolumeX className="size-3" /> : <Volume2 className="size-3" />}
              </button>
            </Tip>
            <Tip label={t('mods.sound_reset', 'Reset')}>
              <button
                type="button"
                onClick={() => setVolume(1)}
                disabled={volume === 1}
                aria-label={t('mods.sound_reset', 'Reset')}
                className="inline-flex size-5 min-h-tap min-w-tap items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
              >
                <RotateCcw className="size-3" />
              </button>
            </Tip>
          </div>
        </div>
        <Slider
          value={volume}
          min={0}
          max={1}
          step={0.05}
          aria-label={t('mods.sound_label', 'Sound volume')}
          formatValue={(v) => `${Math.round(v * 100)}%`}
          onValueCommitted={(v) => {
            setVolume(v);
            const pack = SOUND_PACKS.find((p) => p.id === soundPack);
            if (pack && v > 0) preview(pack, v);
          }}
        />
        <div className="flex flex-wrap gap-1 mt-1.5">
          {SOUND_PACKS.map((p) => (
            <Chip key={p.id} value={p.id} current={soundPack} label={p.label}
              onClick={(v) => {
                setSoundPack(v);
                if (volume > 0) preview(p, volume);
              }} />
          ))}
        </div>
        {/* Sounds is a GROUP of named things, not one switch.
            That is GX's shape — its Sounds category holds Background
            Music, Browser sound and Keyboard sound as separate items —
            and the owner's call. Ours holds three: what the interface
            does, what the keyboard does, and what alerts do. One volume
            above them all, because `engine.test.ts` holds the line that
            there is exactly ONE intensity: two numbers multiplying into
            one gain is how a person reaches 40% of 40%, hears almost
            nothing, and decides the feature is broken. */}
        <div className="border-t border-border mt-2.5 pt-2.5" />

        {/* The gate this section can actually operate.
            Two gates and one dial, which is the honest shape: the
            volume is a LEVEL, and each thing that makes a sound has
            its own switch. Alert sound's switch lives in the alerts
            panel and only three of the nine roles ever see it — this
            one is here, so every role has something the dial applies
            to. Off by default, and it has to stay that way: the level
            defaults to 1, so this switch is the whole distance between
            a fresh account and noise on a shared floor. */}
        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-xs text-foreground">
            {t('mods.sound_ui_label', 'Interface sounds')}
          </span>
          <Switch
            size="sm"
            checked={uiSound}
            onCheckedChange={(next) => {
              setUiSound(next);
              // Armed from inside the click that turned it on. A
              // listener added mid-dispatch still receives the event on
              // nodes it has not reached yet, and window is above the
              // React root — so this same click unlocks audio and the
              // first cue after it can be heard. The provider's effect
              // covers the reload case; this covers the first try,
              // which is the one that decides whether a person believes
              // the feature works.
              if (next) armAudio();
            }}
            aria-label={t('mods.sound_ui_label', 'Interface sounds')}
          />
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t('mods.sound_ui_hint', 'A short cue when something can be undone, and when it lands.')}
        </p>

        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-xs text-foreground">
            {t('mods.sound_keys_label', 'Keyboard')}
          </span>
          <Switch
            size="sm"
            checked={keySound}
            onCheckedChange={(next) => {
              setKeySound(next);
              // Same click, same reason as the switch above: a listener
              // added mid-dispatch still reaches window, so the gesture
              // that turns this on is the gesture that unlocks audio.
              if (next) armAudio();
            }}
            aria-label={t('mods.sound_keys_label', 'Keyboard')}
          />
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t('mods.sound_keys_hint', 'Typing clicks. Never in a password or payment field.')}
        </p>
        {keySound && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {KEY_PACKS.map((p) => (
              <Chip key={p.id} value={p.id} current={keyPack} label={p.label}
                onClick={(v) => {
                  setKeyPack(v);
                  // Preview the letter, the one a person hears most.
                  if (volume > 0) playCue(keyPackById(v)!.cues.letter, volume, KEY_LIMITS);
                }} />
            ))}
          </div>
        )}

        {/* The gate's STATE, not merely its existence.
            The audit's sharpest finding: this section can read 100%
            while the product is silent, because `dispatch.soundOn`
            defaults to false and lives in the alerts panel. Naming
            that a switch exists somewhere does not help — a person
            who reads it still has to go hunting. So it says which
            switch, what state it is in, and where. */}
        <p className="text-2xs text-muted-foreground mt-1.5">
          {t('mods.sound_gate_label', 'Live alerts')}
          {' · '}
          <span className={alertSoundOn ? 'text-foreground' : undefined}>
            {alertSoundOn ? t('mods.sound_gate_on', 'on') : t('mods.sound_gate_off', 'off')}
          </span>
          {!alertSoundOn && (
            <> {t('mods.sound_gate_where', '— turn on in the alerts panel')}</>
          )}
        </p>
      </div>
      )}
        </>
      )}

    </>
  );
}

/**
 * The top-bar entry point: a button, a popover, and the compact controls.
 * It owns only the open/closed question — every control inside it belongs
 * to `ModControls`, which the /mods page renders in full.
 */
export function ModPanel() {
  const { t } = useTranslation();
  const { size } = useMods();
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
      {/* `mods.picker`, not the pre-existing `theme.toggle` ("Toggle
          theme") — this opens a menu of three settings, it does not
          flip one. */}
      <Button
        variant="ghost"
        size="icon"
        className="shrink-0"
        aria-label={t('mods.picker', 'Mods')}
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
          <ModControls compact onNavigate={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}

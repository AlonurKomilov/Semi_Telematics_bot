/**
 * The Interface category — colour, corners, material, typeface, icons.
 *
 * Five groups, exported one by one rather than as a single block,
 * because they do not all render on the same surfaces: Color is in the
 * popover AND on the page, the other four are page-only. That split
 * used to be expressed by having two `has('interface')` blocks on
 * either side of the compact ternary in one 1,000-line file; here it is
 * `ModControls` composing named groups, which is what it was always
 * doing.
 *
 * Each group reads the theme itself. `usePreference` and `useMods` are
 * subscriptions, not expensive lookups, and the profile card's own
 * `Section` already does exactly this — a component takes what it uses
 * rather than being handed eleven props it mostly ignores.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMods, type Mode, type Accent, type RadiusVariant, type Material } from '../context';
import {
  THEME_PACKS, MOD_MATERIALS, MOD_ICONS, FONT_PACKS, packById, type ModIcons,
} from '../catalogue';
import { accentTokens } from '../theme/accent';
import { fitCanvas } from '../theme/canvas';
import { Chip } from './Chip';
import { BrandChip } from './BrandChip';
import { CanvasChip } from './CanvasChip';
import { SURFACES, surfaceById } from '../surfaces';

/** The caps label above a group. The popover runs smaller — seven of
 *  them stack inside `w-56`. */
export type LabelClass = string;

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

/** What surfaces are made of. An axis, so it sits beside Corners rather
 *  than inside a look — a mod may set it, and so may the person. */
const MATERIAL_OPTIONS: { value: Material; key: string; label: string }[] =
  MOD_MATERIALS.map((m) => ({
    value: m,
    key: `mods.material_${m}`,
    label: m === 'solid' ? 'Solid' : 'Glass',
  }));

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

// ── The groups ───────────────────────────────────────────────────────

/** Colour: the mode, the four packs, and the one nobody curated.
 *  The only Interface group the popover carries. */
/**
 * Write a canvas to the global seed or to one place.
 *
 * A pure function over the stored shape rather than two call sites,
 * because the empty case matters: a `surfaces` object with no entries
 * left is not the same state as one that was never written, and the
 * sanitiser drops an empty object anyway. Clearing the last surface
 * removes the field.
 */
function writeCanvas(
  theme: { canvas?: string; surfaces?: Record<string, string> },
  target: string,
  hex: string | undefined,
): { canvas?: string } | { surfaces?: Record<string, string> } {
  if (!target) return { canvas: hex };
  const next = { ...(theme.surfaces ?? {}) };
  if (hex) next[target] = hex;
  else delete next[target];
  return { surfaces: Object.keys(next).length ? next : undefined };
}

/**
 * The dot a scope chip wears: the background that place is actually
 * painting, or nothing.
 *
 * Stored is not the same as worn. A canvas the current mode cannot
 * wear is refused by the gate and the place falls back to the global
 * look — so showing its dot would point at a colour nobody can see,
 * the same mistake `brandWorn` exists to avoid on the pack chips.
 */
function wornCanvas(hex: string | undefined, mode: Mode): string | undefined {
  if (!hex) return undefined;
  return fitCanvas(hex, mode).rgb ? hex : undefined;
}

export function ColorGroup({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  /** Which place the background picker is aiming at. Deliberately NOT
   *  stored: it is a question about this moment, not a preference, and
   *  a remembered target is one a person returns to having forgotten. */
  const [target, setTarget] = useState('');
  // Whether a picked colour is what is actually painting, in the mode
  // being worn — not merely whether one is stored. The pack chips read
  // their highlight off this, because a chip highlighted while its block
  // stands down is pointing at a colour nobody can see.
  /** Places holding a background this mode refuses. A bare chip would
   *  otherwise say two things at once — no colour set, or one set and
   *  standing down — and the difference is what somebody needs to know
   *  before picking again over the top of it. */
  const unworn = useMemo(
    () => SURFACES.filter((s) => {
      const hex = theme.surfaces?.[s.id];
      return Boolean(hex) && !wornCanvas(hex, theme.mode);
    }),
    [theme.surfaces, theme.mode],
  );
  const brandWorn = useMemo(
    () => (theme.brand ? accentTokens(theme.brand, theme.mode).tokens !== null : false),
    [theme.brand, theme.mode],
  );
  return (
    <div>
      <p className={`${label} mb-1.5`}>
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
      {/* The other half of a palette. Its own row, because it claims
          far more than the accent does — a background repaints every
          surface in the app, and putting it in the accent row would
          make the two look like the same size of decision. */}
      <div className="flex flex-wrap items-center gap-1 mt-1.5">
        <CanvasChip
          canvas={target ? theme.surfaces?.[target] : theme.canvas}
          mode={theme.mode}
          onPick={(hex) => setTheme(writeCanvas(theme, target, hex))}
          onClear={() => setTheme(writeCanvas(theme, target, undefined))}
        />
      </div>

      {/* WHERE the background applies. Each chip wears the background
          that place is painting, so which places carry one is legible
          without clicking through all four — state that can only be
          discovered by probing is state a person forgets they set. The accent has no such row: it
          is the brand and stays global, so a page can change the
          conditions it is read in without the product looking like
          several products.

          A named list rather than a route pattern — we own all
          forty-one routes, so a pattern would buy nothing and cost
          the two things a list gives: a typo fails loudly, and the
          control is a button rather than a text field. */}
      {/* A plain sub-label, NOT the caps group class. This is a
          question WITHIN Color — where does the background you just
          picked apply — and giving it the weight of a heading made it
          read as a peer of Corners and Material. The section guard
          caught that: it saw a group the taxonomy had never heard of. */}
      <p className="text-xs text-foreground mt-2.5 mb-1.5">
        {t('theme.canvas_scope', 'Background applies to')}
      </p>
      <div className="flex flex-wrap gap-1">
        <Chip value="" current={target} label={t('theme.scope_all', 'Everywhere')}
          dot={wornCanvas(theme.canvas, theme.mode)}
          onClick={() => setTarget('')} />
        {SURFACES.map((s) => (
          <Chip key={s.id} value={s.id} current={target} label={s.title}
            dot={wornCanvas(theme.surfaces?.[s.id], theme.mode)}
            onClick={(v) => setTarget(v)} />
        ))}
      </div>
      <p className="text-2xs text-muted-foreground mt-1.5">
        {target
          ? `${surfaceById(target)?.title} — ${surfaceById(target)?.why}.`
          : unworn.length
            ? `${t('theme.scope_unworn', 'Not worn in {{mode}} mode')
                .replace('{{mode}}', theme.mode)}: ${unworn.map((s) => s.title).join(', ')}.`
            : t('theme.scope_all_hint', 'One background for the whole app.')}
      </p>
    </div>
  );
}

export function CornersGroup({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  return (
    <div>
      <p className={`${label} mb-1.5`}>
        {t('mods.group_corners', 'Corners')}
      </p>
      <div className="flex gap-1">
        {RADIUS_OPTIONS.map((o) => (
          <Chip key={o.value} value={o.value} current={theme.radius} label={t(o.key, o.label)}
            onClick={(v) => setTheme({ radius: v })} />
        ))}
      </div>
    </div>
  );
}

export function MaterialGroup({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  return (
    <div>
      <p className={`${label} mb-1.5`}>
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
  );
}

export function TypefaceGroup({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  return (
    <div>
      <p className={`${label} mb-1.5`}>
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
  );
}

export function IconsGroup({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  return (
    <div>
      <p className={`${label} mb-1.5`}>
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
  );
}

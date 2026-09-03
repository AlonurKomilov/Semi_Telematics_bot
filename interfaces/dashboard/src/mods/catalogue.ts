/**
 * The theme packs — ONE list, and the reason this file exists.
 *
 * The accent used to be a closed union with six independent pins: a
 * TypeScript type, a runtime array, a membership test in the sanitiser,
 * a hand-written copy inside index.html's pre-paint script, a CSS block
 * per accent per mode, and a test asserting the array literally. Adding
 * a fourth meant finding all six, and missing one failed silently in a
 * different place each time.
 *
 * A pack is a NAME and a SEED. Everything else about it — the fill, the
 * hover, the accent as text — is derived by `derivePalette`, and the
 * guard in themePacks.test.ts asserts the CSS we ship equals what the
 * derivation produces. So a pack cannot drift from its seed, and adding
 * one is: append here, paste the generated block into index.css, and
 * add the id to the boot script's own array in index.html.
 *
 * That last one cannot be generated away — the pre-paint script runs
 * before any module loads and so cannot import this file (design.md §2
 * rule 4). It is the one copy that stays, and `themeBoot.test.ts`
 * exists to compare the two: it runs the shipped script against
 * `applyTheme` over every valid theme and fails on disagreement. It
 * caught azure's omission on the first run.
 *
 * These are OURS. Nothing in the product edits them — not an account,
 * not an account owner. A user picks from what we prepared, the same way
 * they pick dark or light today. When per-user authoring arrives, the
 * seed is still the artifact and this list is still the catalogue; only
 * the delivery changes, from a committed CSS block to properties applied
 * at runtime.
 */
import type { ThemeMode } from './theme/palette';
// Type-only, so no runtime cycle: registry.ts imports THEME_PACKS as a
// value, and this import is erased.
import type { ModRadius } from '../preferences/registry';

export interface ThemePack {
  /** The stored value, and the `data-accent` attribute. */
  readonly id: string;
  /** Shown in the picker. Not translated — see the theme panel's rule. */
  readonly label: string;
  /** `--primary` per mode. The canvas comes from the mode, not the pack;
   *  a pack that carried its own canvas would be taking the light/dark
   *  choice away from the person using it. */
  readonly seed: Readonly<Record<ThemeMode, string>>;
}

export const THEME_PACKS: readonly ThemePack[] = [
  { id: 'blue',   label: 'Blue',   seed: { light: '#2a5cda', dark: '#427bff' } },
  { id: 'purple', label: 'Purple', seed: { light: '#7d40c8', dark: '#9b61ea' } },
  { id: 'green',  label: 'Green',  seed: { light: '#197112', dark: '#38aa2f' } },
  { id: 'azure',  label: 'Azure',  seed: { light: '#027689', dark: '#0796ae' } },
] as const;

/** The three tokens an accent block re-points, and therefore the three a
 *  pack's seed is responsible for. `--chart-1` moves with the accent too
 *  but is NOT derived from the seed: the ramp's job is series
 *  separation, which is a property of all five slots together. */
export const PACK_TOKENS = ['--primary', '--primary-hover', '--primary-text'] as const;

/**
 * What a surface is MADE OF, as opposed to what colour it is.
 *
 * An axis, not a pack field — it belongs beside corners in the panel,
 * because it is a property of the whole app rather than of one look. A
 * mod may set it, the same way a mod sets corners.
 *
 * Kept here rather than in the preferences registry for the same reason
 * the accent set is: one list, and the registry derives from it.
 */
export const MOD_MATERIALS = ['solid', 'glass'] as const;
/** How fast the app moves. A multiplier on every transition — see the
 *  motion tokens in index.css for why the infinite loops are excluded. */
export const MOD_MOTIONS = ['calm', 'default', 'snappy'] as const;
/**
 * What each setting does to a duration, so the panel can say it.
 *
 * A SECOND copy of numbers that live in `index.css` — the axis is keyed
 * on an attribute, so the values have to be in a stylesheet, and a
 * component cannot read a stylesheet without `getComputedStyle` in
 * render. `motion.test.ts` parses the CSS and fails when the two
 * disagree, which is the same bargain the boot script already makes with
 * `applyTheme`.
 *
 * These multiply DURATION, so the number and the feeling run opposite
 * ways: calm is 1.6 because it takes longer. Anything showing this to a
 * person has to invert it — see `motionPercent`.
 */
export const MOTION_SCALE: Readonly<Record<(typeof MOD_MOTIONS)[number], number>> = {
  calm: 1.6,
  default: 1,
  snappy: 0.6,
};

/**
 * The intensity a person reads, as a percentage.
 *
 * Inverted, and that is the whole point of it being a function. Every
 * other percentage on this surface means MORE of the thing named: 160%
 * sound is louder, 150% size is bigger. A raw motion scale would break
 * that — calm would read 160% while being the setting that moves least.
 * So the number shown is how fast the app moves, not how long it takes:
 * calm 63%, default 100%, snappy 167%.
 */
export const motionPercent = (m: (typeof MOD_MOTIONS)[number]): number =>
  // Falls back to the neutral scale rather than dividing by undefined.
  // The registry sanitises this axis, so a bad value cannot arrive at
  // runtime — but `100 / undefined` renders the string "NaN%" on a
  // person's screen, and a panel that prints NaN because a test fixture
  // held a stale enum is a worse outcome than one that prints 100.
  Math.round(100 / (MOTION_SCALE[m] ?? 1));
/**
 * Icon stroke weight, and the FIRST property a mod carries that the
 * panel does not expose.
 *
 * That asymmetry is deliberate and worth stating: up to now a mod was a
 * bundle of controls a person could reach anyway, which makes it a
 * shortcut rather than a look. Not every property has to be a chip.
 * This one rides `<LucideProvider>`, which the installed lucide already
 * ships and nothing here used — one mount point, zero call sites among
 * the 1,663 icon usages.
 */
/**
 * The typeface the whole app is set in.
 *
 * An AXIS, keyed on an attribute, exactly like the accent and the
 * corners — not something a mod injects. `--font-sans` is deliberately
 * unreachable by the token injector (pinned by `inject.test.ts`), and it
 * should stay that way: a font-family is a name that has to resolve to
 * something installed, and the injector's grammar cannot express or
 * verify that.
 *
 * Every stack here costs ZERO bytes. Four of the five are faces the
 * operating system already ships, and they are genuinely different from
 * each other — a serif next to a monospace is not a subtle change. That
 * is the foundation rather than the ceiling: adding a real bundled
 * webfont later is one `@import` in index.css, one block beside the
 * others, and one entry here. Nothing else in this file has to move.
 */
export interface FontPack {
  readonly id: string;
  readonly label: string;
  /** What a person is actually looking at, for the panel to say. */
  readonly note: string;
}
export const FONT_PACKS: readonly FontPack[] = [
  { id: 'geist',   label: 'Geist',   note: 'The one this app was drawn with' },
  { id: 'system',  label: 'System',  note: 'Whatever your computer uses' },
  { id: 'serif',   label: 'Serif',   note: 'Book-like, with strokes on the letters' },
  { id: 'mono',    label: 'Mono',    note: 'Every letter the same width' },
  { id: 'rounded', label: 'Rounded', note: 'Softer corners on the letters' },
];
export const MOD_FONTS = FONT_PACKS.map((f) => f.id);
export type ModFont = string;

export const MOD_ICONS = ['hairline', 'regular', 'bold'] as const;
/** The stroke widths those names mean. `regular` is lucide's own 2. */
export const ICON_STROKE: Record<string, number> = {
  hairline: 1.25, regular: 2, bold: 2.5,
};
export type ModMaterial = (typeof MOD_MATERIALS)[number];
export type ModMotion = (typeof MOD_MOTIONS)[number];
export type ModIcons = (typeof MOD_ICONS)[number];

export const packById = (id: string): ThemePack | undefined =>
  THEME_PACKS.find((p) => p.id === id);

/**
 * A MOD is a named combination of axes we already have — not a new
 * colour.
 *
 * The first draft put `radius` and `size` onto ThemePack, and that was
 * wrong twice over. It conflated "a hue with its derived tokens" with "a
 * look", and it meant every look needed a hue of its own — which the
 * palette cannot supply. Measured while choosing azure: every remaining
 * hue either collides with `--danger` under simulated colour blindness,
 * collides with `--warn`, or duplicates a pack we ship. A model that
 * demands a new hue per look runs out after one.
 *
 * Referencing a pack instead costs nothing: no seed, no CSS block, no
 * swatch, no ramp rotation. A mod is a row in this list and nothing else.
 *
 * And this is the axis Opera GX cannot distribute. A GX mod carries
 * colours, sounds and wallpapers; corner radius and UI density are
 * user settings there, unreachable from a mod. Ours are already values
 * on `<html>` — `data-radius` and the `--size-*` multipliers — so a mod
 * can simply carry them.
 */
export interface Mod {
  readonly id: string;
  readonly label: string;
  /** The colour pack this look wears. Must be a `THEME_PACKS` id. */
  readonly accent: string;
  readonly radius?: ModRadius;
  /** The global size multiplier. At or above 1 only — the panel's own
   *  slider starts at 100% because everything below it waits on the
   *  24px hit-target floor (design.md §5.1), and a mod must not reach
   *  somewhere the control cannot follow it back from. */
  readonly size?: number;
  /** What surfaces are made of. Omit and the person's own choice stands. */
  readonly material?: ModMaterial;
  /** How fast it moves. Omit and the person's own choice stands. */
  readonly motion?: ModMotion;
  /** Icon stroke weight. */
  readonly icons?: ModIcons;
  /** The typeface. A `FONT_PACKS` id; omit and the person's own stands.
   *  A look that changes the colour and not the lettering is half a
   *  look — this is the field that lets a mod be one. */
  readonly font?: ModFont;
  /** Animate the routed page in. Off unless a mod asks: an operations
   *  dashboard is navigated dozens of times an hour, and a slide-in on
   *  every one of them is a tax rather than a delight. */
  readonly entrance?: boolean;
  /**
   * Which cue set the look comes with — a `SOUND_PACKS` id.
   *
   * The first thing a mod carries that is not a value on `<html>`, and
   * the reason identity had to stop being a sum of the axes. It sets
   * the pack, never the volume: how loud is the room's business, and a
   * look that reaches for it would be a look that shouts in a quiet
   * office.
   */
  readonly sound?: string;
  /** One line, shown under the label. Says who the look is FOR. */
  readonly why: string;
}

/**
 * Deliberately NOT carrying `mode`. Dark or light is the one axis that
 * is about the room a person is sitting in — a bright yard office at
 * noon, a cab at 2am — and a look that seizes it makes the screen
 * unreadable for exactly the reason they chose the other one. A mod
 * dresses the app; it does not decide where you are.
 */
export const MODS: readonly Mod[] = [
  { id: 'cab',  label: 'Cab',  accent: 'azure', radius: 'pill',    size: 1.25,
    icons: 'bold', sound: 'blip',
    why: 'Tablet in a moving truck — bigger targets, gloved hands' },
  { id: 'wall', label: 'Wall', accent: 'blue',  radius: 'rounded', size: 1.45,
    icons: 'bold', entrance: true,
    why: 'A display read from across the room' },
] as const;

/**
 * Which category each thing a mod carries lands in — GX shows an
 * installed mod's footprint as a checklist, and this is the map that
 * makes ours say the same thing in the same words the sections use.
 *
 * Typed as a total Record over the Mod fields, so ADDING A FIELD IS A
 * COMPILE ERROR until someone says where it belongs. That is stronger
 * than a test: it cannot be skipped, and it fails at the moment the
 * field is written rather than the moment the suite runs.
 *
 * `id`, `label` and `why` are metadata about the mod, not things it
 * changes about the app, so they are the excluded three.
 */
export const MOD_FIELD_SECTION: Record<
  keyof Omit<Mod, 'id' | 'label' | 'why'>,
  'interface' | 'effects' | 'sounds' | 'size'
> = {
  accent: 'interface',
  radius: 'interface',
  material: 'interface',
  icons: 'interface',
  font: 'interface',
  motion: 'effects',
  entrance: 'effects',
  sound: 'sounds',
  size: 'size',
};

/** In the order the card renders them, so the footprint reads down the
 *  page rather than in whatever order the fields happen to be declared. */
const SECTION_ORDER = ['interface', 'sounds', 'effects', 'size'] as const;

/**
 * The categories this mod actually writes into.
 *
 * Every field is optional but `accent`, so two mods can carry very
 * different amounts — Cab brings a sound pack and Wall does not, and
 * before this there was no way to know that without reading the source.
 */
export const modFootprint = (m: Mod): readonly string[] => {
  const touched = new Set(
    (Object.keys(m) as (keyof Mod)[])
      .filter((k) => k in MOD_FIELD_SECTION && m[k] !== undefined)
      .map((k) => MOD_FIELD_SECTION[k as keyof typeof MOD_FIELD_SECTION]),
  );
  return SECTION_ORDER.filter((s) => touched.has(s));
};

export const modById = (id: string): Mod | undefined =>
  MODS.find((m) => m.id === id);

/**
 * Everything a mod can set. One object rather than a parameter list:
 * `activeModId` had reached five positional arguments and every new axis
 * was changing its signature and every call site with it.
 */
export interface ModAxes {
  accent: string;
  radius: string;
  size: number;
  material: string;
  motion: string;
  /** Icon stroke weight. NOT a panel control — see Mod.icons. */
  icons: string;
  /** The installed cue set. Not on `<html>`, but readable, so a mod
   *  that carries one can still be reported as edited. */
  sound: string;
}

/**
 * Does the current state still equal this mod, axis for axis?
 *
 * NOT the same question as "which mod is on" — that is `theme.mod`, and
 * the split is the point of this change.
 *
 * The old model recomputed identity from the axes: a mod was "on" while
 * the sum matched, and tweaking a corner made it quietly not-on. That is
 * elegant while everything a mod carries is a value on `<html>`, and it
 * hits a wall the moment one is not. A sound pack cannot be recomputed
 * from the DOM. Neither can a wallpaper. If identity is a sum of axes,
 * then installing a mod and nudging the corners would stop its sounds —
 * which is not what "installed" means anywhere.
 *
 * So identity is STORED and this function answers the smaller question
 * it used to answer by accident: whether what you see is still exactly
 * what the mod asked for, or whether you have since changed something.
 */
export const modMatchesAxes = (m: Mod, a: ModAxes): boolean =>
  m.accent === a.accent
  && (m.radius === undefined || m.radius === a.radius)
  && (m.material === undefined || m.material === a.material)
  && (m.motion === undefined || m.motion === a.motion)
  && (m.icons === undefined || m.icons === a.icons)
  && (m.sound === undefined || m.sound === a.sound)
  // Float compare: the size arrives from a slider and a stored JSON
  // round-trip, so `===` against 1.25 is a coin toss.
  && (m.size === undefined || Math.abs(m.size - a.size) < 1e-6);

/**
 * @deprecated Identity now lives in `theme.mod`. Kept for one release so
 *   a caller that still asks "which mod do these axes add up to" gets a
 *   sensible answer instead of a type error — but it cannot see a mod
 *   whose assets are installed and whose axes have been edited, which is
 *   the whole reason identity moved.
 */
export const activeModId = (a: ModAxes): string =>
  MODS.find((m) => modMatchesAxes(m, a))?.id ?? '';

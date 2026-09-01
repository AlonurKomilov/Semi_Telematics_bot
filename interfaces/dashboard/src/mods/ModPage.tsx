/**
 * The Mods page — every customization the app offers, in one place.
 *
 * The top-bar popover is deliberately NOT this page in miniature. It
 * carries the three things a person changes often (which mod is
 * installed, the colour it wears, and the global size) and a door to
 * here; everything that is a *setting* rather than a *toggle* lives on
 * this page, which has the width design.md §7 refuses to invent for a
 * popover.
 *
 * Both surfaces render the SAME `ModControls`, so a chip row cannot
 * drift between them — the only difference is the `compact` flag and
 * which branch of it renders.
 *
 * Size is the one axis split across the two: the popover holds the
 * global slider, and this page hands size WHOLE to `SizeCard` (global,
 * per region, cross-device). The page therefore does not repeat the
 * global slider — one object, one face.
 */
import { Palette, RotateCcw } from 'lucide-react';
import PageHeader from '../components/shell/PageHeader';
import { Card } from '../components/ui/card';
import { SectionHeader } from '../components/shell';
import { undoableAction } from '../components/banners/stagedAction';
import { usePreference } from '../preferences/usePreference';
import { DEFS, MOD_DEFAULT } from '../preferences/registry';
import { useMods } from './context';
import { ModControls } from './ModPanel';
import SizeCard from './SizeCard';

/** Read from the registry, never re-typed here: a second copy of a
 *  default is a copy that drifts the first time someone edits one. */
const SOUND_PACK_DEFAULT = DEFS['mods.sound.pack'].default;
const SOUND_VOLUME_DEFAULT = DEFS['mods.sound.volume'].default;

/**
 * The axes this reset owns. Two deliberate absences:
 *
 *   `mode` — dark/light is about the room the person is sitting in, not
 *   about the look. It is the one axis a mod may never carry (guarded in
 *   catalogue.test.ts), and a reset that threw a light-mode user into
 *   dark would be the same mistake from the other side.
 *
 *   `size` — SizeCard owns size whole and has its own reset. Each card
 *   resets what it owns, so neither reaches into the other.
 */
export const RESET_AXES = {
  accent: MOD_DEFAULT.accent,
  radius: MOD_DEFAULT.radius,
  material: MOD_DEFAULT.material,
  motion: MOD_DEFAULT.motion,
  icons: MOD_DEFAULT.icons,
  entrance: MOD_DEFAULT.entrance,
  mod: undefined,
  tokens: undefined,
} as const;

export function ResetAppearance() {
  const { theme, setTheme } = useMods();
  const { value: pack, setValue: setPack } = usePreference('mods.sound.pack');
  const { value: volume, setValue: setVolume } = usePreference('mods.sound.volume');

  const atDefault =
    theme.accent === RESET_AXES.accent &&
    theme.radius === RESET_AXES.radius &&
    theme.material === RESET_AXES.material &&
    theme.motion === RESET_AXES.motion &&
    theme.icons === RESET_AXES.icons &&
    theme.entrance === RESET_AXES.entrance &&
    (theme.mod ?? '') === '' &&
    theme.tokens === undefined &&
    pack === SOUND_PACK_DEFAULT &&
    volume === SOUND_VOLUME_DEFAULT;

  const reset = () => {
    // Snapshot BEFORE the write, for the same reason installing a mod
    // snapshots: this is the user's own configuration, and one click
    // discards up to eight tuned values.
    const previous = {
      accent: theme.accent, radius: theme.radius, material: theme.material,
      motion: theme.motion, icons: theme.icons, entrance: theme.entrance,
      mod: theme.mod, tokens: theme.tokens,
    };
    const previousPack = pack;
    const previousVolume = volume;

    setTheme(RESET_AXES);
    setPack(SOUND_PACK_DEFAULT);
    setVolume(SOUND_VOLUME_DEFAULT);

    undoableAction({
      label: 'Appearance reset',
      undo: async () => {
        setTheme(previous);
        setPack(previousPack);
        setVolume(previousVolume);
      },
    });
  };

  return (
    <button
      type="button"
      onClick={reset}
      disabled={atDefault}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground shrink-0 py-1 -my-1 min-h-tap"
    >
      <RotateCcw className="size-3.5" />
      Reset appearance
    </button>
  );
}

export default function ModPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Palette}
        title="Mods"
        description="How the app looks, moves and sounds. A mod installs a whole look at once; every axis below is also yours to set on its own."
      />

      {/* max-w-2xl, not the full page: these are chip rows and short
          labels. Stretched across a wide viewport the eye loses which
          label belongs to which row, and the section rules turn into
          full-bleed lines that read as page furniture rather than as
          the boundary between two questions. */}
      <Card render={<section />} className="max-w-2xl">
        {/* Both cards on this page wear the same chrome — title, muted
            line, reset opposite — through the SectionHeader primitive
            rather than a hand-rolled flex row. Their BODIES differ
            because their content genuinely does (six parallel groups
            here, one control plus a disclosure there); the header is
            where the page says they are siblings. */}
        <SectionHeader
          description="Colour, corners, material, motion and sound."
          action={<ResetAppearance />}
        >
          Appearance
        </SectionHeader>
        {/* The popover supplies this rhythm from its own `space-y-3`;
            on a page the wrapper has to, or the sections collide. */}
        <div className="space-y-3 mt-4">
          <ModControls />
        </div>
      </Card>

      <div className="max-w-2xl">
        <SizeCard />
      </div>
    </div>
  );
}

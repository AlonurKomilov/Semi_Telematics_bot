/**
 * Modifications — every customization the app offers, as one card on
 * /profile.
 *
 * MODS IS THE ENGINE, NOT A PAGE. The data lives in the user's
 * preferences, the engine reads it, and nothing stored means
 * MOD_DEFAULT. So the surface belongs where the data belongs: on the
 * page whose own subtitle already says "these settings apply to your own
 * dashboard… they don't affect anyone else on the account". `/settings`
 * could not host it — that one is gated on `can_manage_account`.
 *
 * FOUR SECTIONS, and they are the taxonomy a future catalogue will use,
 * so there is one vocabulary in both places rather than two. Interface,
 * Sounds, Effects, Size.
 *
 * THE MOD ROW IS NOT A SECTION. Installing a mod writes into every
 * section below it, so it sits at the card level with the card-level
 * reset. Putting it inside Interface would make it the only control that
 * writes outside its own block.
 *
 * The catalogue page is deferred until there is content to browse — four
 * accents and two sound packs is not a catalogue.
 */
import { RotateCcw } from 'lucide-react';
import { Card } from '../components/ui/card';
import { SectionHeader } from '../components/shell';
import { undoableAction } from '../components/banners/stagedAction';
import { usePreference, DEFS, MOD_DEFAULT } from '../preferences';
import { useMods } from './context';
import { ModControls, type ModSection } from './ModPanel';
import SizeCard from './SizeCard';

export { MODS_HREF } from './href';

/** Read from the registry, never re-typed here: a second copy of a
 *  default is a copy that drifts the first time someone edits one. */
const SOUND_PACK_DEFAULT = DEFS['mods.sound.pack'].default;
const SOUND_VOLUME_DEFAULT = DEFS['mods.sound.volume'].default;

/**
 * The axes a reset owns, partitioned by section.
 *
 * `mod` and `tokens` are the CONTAINER's, not Interface's — resetting
 * one category must not uninstall the look that supplied all four.
 *
 * Two axes are in no partition at all, and both absences are deliberate:
 *
 *   `mode` — dark/light is about the room the person is sitting in, not
 *   about the look. It is the one axis a mod may never carry, and a
 *   reset that threw a light-mode user into dark would be that same
 *   mistake arriving from the other direction.
 *
 *   `color` — a derived alias, re-written by `setTheme` on every write.
 *   It has no independent value to restore.
 */
export const SECTION_AXES = {
  interface: {
    accent: MOD_DEFAULT.accent,
    // A picked colour is an interface choice, so "Reset interface"
    // clears it — not only "Reset mods". It has no entry in
    // `MOD_DEFAULT` because its default is ABSENCE: the packs are the
    // floor, and nothing custom is what a new account wears.
    brand: undefined,
    radius: MOD_DEFAULT.radius,
    material: MOD_DEFAULT.material,
    icons: MOD_DEFAULT.icons,
  },
  effects: {
    motion: MOD_DEFAULT.motion,
    entrance: MOD_DEFAULT.entrance,
  },
} as const;

/** The container's own axes — what "Reset mods" adds on top of the four. */
export const CONTAINER_AXES = { mod: undefined, tokens: undefined } as const;

/** Every axis a reset touches, at any level. Kept as ONE object because
 *  `resetAppearance.test.tsx` walks it against `MOD_DEFAULT` to force a
 *  decision when an axis is added. */
export const RESET_AXES = {
  ...SECTION_AXES.interface,
  ...SECTION_AXES.effects,
  ...CONTAINER_AXES,
} as const;

/** Read one axis off the stored shape by name. The partitions above are
 *  keyed by string, so the cast lives here once instead of at each use. */
const axisOf = (t: unknown, k: string): unknown => (t as Record<string, unknown>)[k];

/**
 * A reset for one scope.
 *
 * Rendered only when that scope has something to undo. That is NOT the
 * hidden-control failure SizeCard warns about — there the control
 * vanishes because of an external condition the person cannot see, so
 * they hunt for it. Here its absence means "nothing to reset", and the
 * reason is on screen: every chip in the section is visibly on its
 * default.
 */
function SectionReset({ label, atDefault, onReset }: {
  label: string;
  atDefault: boolean;
  onReset: () => void;
}) {
  if (atDefault) return null;
  return (
    <button
      type="button"
      onClick={onReset}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground shrink-0 py-1 -my-1 min-h-tap"
    >
      <RotateCcw className="size-3.5" />
      {label}
    </button>
  );
}

/** One category: its heading, its own reset, its controls. */
function Section({ id, title, label, axes }: {
  id: Exclude<ModSection, 'mods'>;
  title: string;
  label: string;
  axes: Record<string, unknown>;
}) {
  const { theme, setTheme } = useMods();
  const { value: pack, setValue: setPack } = usePreference('mods.sound.pack');
  const { value: volume, setValue: setVolume } = usePreference('mods.sound.volume');

  const soundAtDefault = pack === SOUND_PACK_DEFAULT && volume === SOUND_VOLUME_DEFAULT;
  const axesAtDefault = Object.entries(axes)
    .every(([k, v]) => axisOf(theme, k) === v);
  const atDefault = id === 'sounds' ? soundAtDefault : axesAtDefault;

  const reset = () => {
    // Snapshot BEFORE the write, for the same reason installing a mod
    // snapshots: this is the person's own configuration.
    if (id === 'sounds') {
      const wasPack = pack;
      const wasVolume = volume;
      setPack(SOUND_PACK_DEFAULT);
      setVolume(SOUND_VOLUME_DEFAULT);
      undoableAction({
        label: `${title} reset`,
        undo: async () => { setPack(wasPack); setVolume(wasVolume); },
      });
      return;
    }
    const previous = Object.fromEntries(
      Object.keys(axes).map((k) => [k, axisOf(theme, k)]),
    );
    setTheme(axes);
    undoableAction({ label: `${title} reset`, undo: async () => setTheme(previous) });
  };

  return (
    <div className="border-t border-border pt-4 max-w-2xl">
      <SectionHeader
        size="card"
        action={<SectionReset label={label} atDefault={atDefault} onReset={reset} />}
      >
        {title}
      </SectionHeader>
      <div className="space-y-3 mt-3">
        <ModControls section={id} />
      </div>
    </div>
  );
}

/** "Reset mods" — the whole card, one level above the sections. */
export function ResetMods() {
  const { theme, setTheme } = useMods();
  const { value: pack, setValue: setPack } = usePreference('mods.sound.pack');
  const { value: volume, setValue: setVolume } = usePreference('mods.sound.volume');

  const atDefault =
    Object.entries(RESET_AXES).every(
      ([k, v]) => axisOf(theme, k) === v,
    )
    && pack === SOUND_PACK_DEFAULT
    && volume === SOUND_VOLUME_DEFAULT;

  const reset = () => {
    const previous = Object.fromEntries(
      Object.keys(RESET_AXES).map((k) => [k, axisOf(theme, k)]),
    );
    const wasPack = pack;
    const wasVolume = volume;
    setTheme(RESET_AXES);
    setPack(SOUND_PACK_DEFAULT);
    setVolume(SOUND_VOLUME_DEFAULT);
    undoableAction({
      label: 'Mods reset',
      undo: async () => {
        setTheme(previous);
        setPack(wasPack);
        setVolume(wasVolume);
      },
    });
  };

  return <SectionReset label="Reset mods" atDefault={atDefault} onReset={reset} />;
}

export default function Modifications() {
  return (
    <Card render={<section />} id="modifications" className="scroll-mt-20">
      <SectionHeader
        description="How the app looks, moves and sounds. Only affects what you see."
        action={<ResetMods />}
      >
        Modifications
      </SectionHeader>

      {/* The container's own row — no caps label, because the card is
          already called Modifications and a second name would only
          repeat it. */}
      <div className="mt-4 max-w-2xl">
        <ModControls section="mods" />
      </div>

      <div className="mt-4 space-y-4">
        <Section id="interface" title="Interface" label="Reset interface"
          axes={SECTION_AXES.interface} />
        <Section id="sounds" title="Sounds" label="Reset sounds" axes={{}} />
        <Section id="effects" title="Effects" label="Reset effects"
          axes={SECTION_AXES.effects} />
        <div className="border-t border-border pt-4">
          <SizeCard />
        </div>
      </div>
    </Card>
  );
}

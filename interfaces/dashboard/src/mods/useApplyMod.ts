/**
 * Installing a whole look — the one write in this service that touches
 * seven axes at once, and therefore the one that owes an undo.
 *
 * A hook rather than a function on the row that used to own it, because
 * two surfaces install a mod now: the panel's Mods row, and the store,
 * where a look is a tile like any other pack. A second copy of this
 * would be a second chance to forget a field — which is the bug the
 * walked `MOD_THEME_FIELDS` below already exists to prevent.
 */
import { usePreference } from '../preferences';
import { undoableAction } from '../components/banners/stagedAction';
import { useMods } from './context';
import { MOD_THEME_FIELDS, type Mod } from './catalogue';
import type { ModSetting } from '../preferences/registry';

export function useApplyMod(): (m: Mod) => void {
  const { theme, setTheme, size, setSize } = useMods();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');

  return (m: Mod) => {
    // Snapshot BEFORE the write. Installing a mod overwrites accent,
    // corners, material, motion, icon weight, size and sound in one
    // click — up to seven values somebody may have spent real time on,
    // and "let me just see what Wall looks like" is the most likely
    // reason anyone clicks here. The same helper guards SizeCard's
    // reset, for the same reason.
    // What to put back, read from the SAME list the install walks. It
    // used to be typed out here, and it had already fallen behind: a
    // look carrying a typeface would have been undone into the wrong
    // one, if a look carrying a typeface had done anything at all.
    const previous = {
      mod: theme.mod,
      ...Object.fromEntries(MOD_THEME_FIELDS.map((f) => [f, theme[f as keyof typeof theme]])),
    };
    const previousSize = size.global;
    const previousSound = soundPack;
    setTheme({
      // Stored, so it survives an axis being edited afterwards. Clicking
      // an already-installed mod therefore RESTORES it — the useful
      // second meaning of the same gesture.
      mod: m.id,
      // WALKED, not listed. Every field this hand-written list forgot
      // was a promise the catalogue made and nothing kept: `font` was
      // declared on `Mod`, filed under Typeface, shown in the footprint
      // — and never applied, so a look that changed the lettering
      // changed nothing. `MOD_FIELD_APPLIER` is total over the type, so
      // a new field either comes through here or names another home.
      ...Object.fromEntries(
        MOD_THEME_FIELDS
          .filter((f) => m[f] !== undefined)
          .map((f) => [f, m[f]]),
      ),
    } as Partial<ModSetting>);
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
}

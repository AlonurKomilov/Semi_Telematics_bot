/**
 * The container's own row — which whole look is installed.
 *
 * Not a category: installing a mod writes into every category below it,
 * which is exactly why it sits above them. It owns `applyMod`, the one
 * write in this service that touches seven axes at once, and therefore
 * the undo that write needs.
 */
import { useTranslation } from 'react-i18next';
import { usePreference } from '../../preferences';
import { undoableAction } from '../../components/banners/stagedAction';
import { useMods, type Accent } from '../context';
import { MODS, modById, modMatchesAxes, modFootprint, type Mod } from '../catalogue';
import { Chip } from './Chip';
import type { LabelClass } from './Interface';

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

export function ModsRow({ label: groupLabel }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme, size, setSize } = useMods();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');

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

  return (
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
  );
}

/** Whether there is anything to show — the container row renders only
 *  when the catalogue has entries, which is how a shrunken catalogue
 *  leaves no empty heading behind. */
export const HAS_MODS = MOD_OPTIONS.length > 0;

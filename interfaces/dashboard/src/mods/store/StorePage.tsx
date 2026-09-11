/**
 * /mods/store — everything there is, by the shelf it sits on.
 *
 * /mods is what you HAVE: the settings drawn as depth, one control per
 * item. This is what there IS: one tile per pack, with the sentence the
 * pack carries about itself, which until now had nowhere to be read —
 * `PackMeta.description` was a single line under a chip row, replaced
 * the moment you moved the mouse.
 *
 * It lists the STORE, not the engine: the rows come from the catalogue,
 * every tile says who published it, and applying goes through the one
 * home `axes.ts` declares for that shelf. A pack this install does not
 * carry is not drawn — the same door every picker asks.
 *
 * Previewing is APPLYING here, on purpose. Six of the ten axes paint
 * from `:root` — a wallpaper, a typeface, a cursor cannot be shown
 * inside a 200px tile without a second copy of the pack's CSS, and a
 * preview that is a copy is a preview that can lie. So the app itself is
 * the preview, one click away, and every apply is undoable from the
 * places that already own that undo.
 */
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Check, Store } from '../../lib/icons';
import { PageHeader, SectionHeader } from '../../components/shell';
import { Card } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { usePreference } from '../../preferences';
import { useMods } from '../context';
import { useApplyMod } from '../useApplyMod';
import { MODS_PAGE_HREF } from '../href';
import { armAudio, playCue } from '../sound/engine';
import { KEY_LIMITS } from '../sound/keys';
import { modById } from './packs/mods';
import { accentSeed } from './packs/theme';
import { soundPackById } from './packs/sound';
import { keyPackById } from './packs/keys';
import { PACK_AXES } from './packs';
import { rowsOf, type StoreRow } from './index';
import { offered } from './local';
import { AXIS_UI } from './axes';
import type { ModSetting } from '../../preferences/registry';

/** The dot a tile can honestly draw: a colour the pack IS. Everything
 *  else paints from the root and is previewed by being applied. */
function dotOf(axis: string, id: string, mode: 'light' | 'dark'): string | undefined {
  if (axis === 'theme') return accentSeed(id, mode);
  const mod = axis === 'mods' ? modById(id) : undefined;
  return mod ? accentSeed(mod.accent, mode) : undefined;
}

export function ModsStorePage() {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  const applyMod = useApplyMod();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');
  const { value: keyPack, setValue: setKeyPack } = usePreference('mods.sound.keyboard.pack');
  const { value: volume } = usePreference('mods.sound.volume');

  const prefOf = (key: string) => (key === 'mods.sound.pack' ? soundPack : keyPack);
  const setPref = (key: string, id: string) =>
    (key === 'mods.sound.pack' ? setSoundPack : setKeyPack)(id);

  /** Whether this pack is the one that shelf is wearing. */
  const isApplied = (axis: string, id: string): boolean => {
    const home = AXIS_UI[axis]?.home;
    if (!home) return false;
    if (home.mod) return (theme.mod ?? '') === id;
    if (home.pref) return prefOf(home.pref) === id;
    return home.theme!.every((f) => theme[f] === id);
  };

  const apply = (axis: string, id: string) => {
    const home = AXIS_UI[axis]?.home;
    if (!home) return;
    if (home.mod) {
      const mod = modById(id);
      if (mod) applyMod(mod);
      return;
    }
    if (home.pref) {
      setPref(home.pref, id);
      // A sound pack you cannot hear is a blank tile. Same cue the
      // panel plays when a chip is picked, at the level already set.
      if (volume > 0) {
        armAudio();
        if (home.pref === 'mods.sound.pack') {
          const pack = soundPackById(id);
          if (pack) playCue(pack.cues.alert, volume);
        } else {
          const pack = keyPackById(id);
          if (pack) playCue(pack.cues.letter, volume, KEY_LIMITS);
        }
      }
      return;
    }
    setTheme(Object.fromEntries(home.theme!.map((f) => [f, id])) as Partial<ModSetting>);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('mods.store_title', 'Mods store')}
        description={t('mods.store_desc',
          'Every pack this app carries. Applying one changes what you see straight away.')}
        icon={Store}
        actions={(
          <Button variant="outline" size="sm" render={<Link to={MODS_PAGE_HREF} />}>
            <ArrowLeft className="size-4" />
            {t('mods.store_back', 'Your mods')}
          </Button>
        )}
      />
      {PACK_AXES.map(({ axis }) => {
        const ui = AXIS_UI[axis];
        const rows = offered(axis, rowsOf(axis), (r) => r.id);
        if (!ui || rows.length === 0) return null;
        return (
          <section key={axis} data-testid={`store-axis-${axis}`}>
            <SectionHeader>{ui.label}</SectionHeader>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {rows.map((row) => (
                <PackTile
                  key={row.id}
                  row={row}
                  dot={dotOf(axis, row.id, theme.mode)}
                  applied={isApplied(axis, row.id)}
                  onApply={() => apply(axis, row.id)}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function PackTile({ row, dot, applied, onApply }: {
  row: StoreRow; dot?: string; applied: boolean; onApply: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Card className="p-3 flex flex-col gap-3">
      <div className="flex items-start gap-2 min-w-0">
        {dot && (
          <span aria-hidden className="size-4 rounded-full shrink-0 mt-0.5 border border-border"
            style={{ background: dot }} />
        )}
        <div className="min-w-0">
          <p className="font-medium truncate">{row.label}</p>
          <p className="text-xs text-muted-foreground">{row.description}</p>
        </div>
      </div>
      <div className="mt-auto flex items-center justify-between gap-2">
        {/* Who made it. One value today, and the reason the column
            exists: the store says who owns a pack, the pack does not. */}
        <span className="text-2xs text-muted-foreground truncate">
          {t('mods.store_by', 'by {{who}}', { who: row.publisher })}
        </span>
        <Button size="sm" variant={applied ? 'secondary' : 'default'}
          disabled={applied} onClick={onApply}>
          {applied
            ? (<><Check className="size-4" />{t('mods.store_applied', 'Applied')}</>)
            : t('mods.store_apply', 'Apply')}
        </Button>
      </div>
    </Card>
  );
}

export default ModsStorePage;

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
import { ArrowLeft, Check, Plus, Store, X } from '../../lib/icons';
import { PageHeader, SectionHeader } from '../../components/shell';
import { Card } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { undoableAction } from '../../components/banners/stagedAction';
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
import { rowsOf, rowById, type StoreRow } from './index';
import { offered } from './local';
import { AXIS_UI, defaultOf } from './axes';
import { useShelves } from './useOffered';
import { cn } from '../../lib/utils';
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
  const { isKept, keep, drop, canDrop } = useShelves();
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

  /**
   * Applying is the preview, so it has to be as cheap to undo as it was
   * to try — and on four of these shelves (cursor, material, shader, and
   * a still wallpaper) the change is quiet enough that a person can
   * click and not be sure anything happened. The banner is both answers
   * at once: what changed, and the way back. The looks shelf is absent
   * from this — `useApplyMod` owns the undo for the write that touches
   * seven axes, and two banners for one click is worse than none.
   */
  const apply = (axis: string, id: string, label: string) => {
    const home = AXIS_UI[axis]?.home;
    if (!home) return;
    const said = t('mods.store_toast', '{{shelf}} set to {{pack}}',
      { shelf: AXIS_UI[axis].label, pack: label });
    if (home.mod) {
      const mod = modById(id);
      if (mod) applyMod(mod);
      return;
    }
    if (home.pref) {
      const was = prefOf(home.pref);
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
      undoableAction({ label: said, undo: async () => setPref(home.pref!, was) });
      return;
    }
    const was = Object.fromEntries(
      home.theme!.map((f) => [f, theme[f]])) as Partial<ModSetting>;
    setTheme(Object.fromEntries(home.theme!.map((f) => [f, id])) as Partial<ModSetting>);
    undoableAction({ label: said, undo: async () => setTheme(was) });
  };

  /**
   * Taking a pack off the shelf you are WEARING it from would leave the
   * app painting something no picker offers and nothing can put back —
   * the same trap `ModsLock` closes when a permission goes away, closed
   * the same way: the removal writes the reset.
   */
  const remove = (axis: string, id: string, label: string) => {
    drop(axis, id);
    if (!isApplied(axis, id)) return;
    const back = defaultOf(axis);
    if (back) { apply(axis, back, rowById(axis, back)?.label ?? back); return; }
    // The looks shelf has no default: stop wearing it, and leave the
    // axes it wrote alone — a look is a way of writing them, not a
    // layer over them.
    setTheme({ mod: '' } as Partial<ModSetting>);
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
                  kept={isKept(axis, row.id)}
                  removable={canDrop(axis, row.id)}
                  onApply={() => apply(axis, row.id, row.label)}
                  onKeep={() => keep(axis, row.id)}
                  onRemove={() => remove(axis, row.id, row.label)}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function PackTile({ row, dot, applied, kept, removable, onApply, onKeep, onRemove }: {
  row: StoreRow; dot?: string; applied: boolean; kept: boolean; removable: boolean;
  onApply: () => void; onKeep: () => void; onRemove: () => void;
}) {
  const { t } = useTranslation();
  return (
    // A pack taken off the shelf stays on the page, dimmed: it is the
    // only way back. Hiding it would make removal indistinguishable
    // from the pack never having existed.
    <Card className={cn('p-3 flex flex-col gap-3', !kept && 'opacity-60')}>
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
        {/* What IS, and what you can DO, are not the same shape. A
            disabled button saying "Applied" is a status wearing an
            action's clothes — the eye has to read it to find out it is
            not a control. */}
        <div className="flex items-center gap-1.5 shrink-0">
          {!kept
            ? (
              <Button size="sm" variant="outline" onClick={onKeep}>
                <Plus className="size-4" aria-hidden />
                {t('mods.store_add', 'Add')}
              </Button>
            )
            : applied
              ? (
                <Badge tone="ok">
                  <Check className="size-3.5" aria-hidden />
                  {t('mods.store_applied', 'Applied')}
                </Badge>
              )
              : (
                <Button size="sm" onClick={onApply}>{t('mods.store_apply', 'Apply')}</Button>
              )}
          {/* Removable only where a shelf would still have something on
              it afterwards. A control that silently does nothing is
              worse than one that is not there. */}
          {kept && removable && (
            <Button size="sm" variant="ghost" onClick={onRemove}
              aria-label={t('mods.store_remove', 'Remove {{pack}}', { pack: row.label })}>
              <X className="size-4" aria-hidden />
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

export default ModsStorePage;

/**
 * /mods/store — the packs, and what each one brings.
 *
 * /mods is what you HAVE, drawn as depth. This is what there IS, and the
 * unit here is the PACK: one name, installed or not, arriving with every
 * item it ships and leaving with them. Items are not installed one at a
 * time — they have no existence apart from the pack that shipped them —
 * so this page has exactly two verbs, Install and Remove, and the
 * choosing of which installed item to WEAR stays where it always was, in
 * the pickers on /mods and in the panel.
 *
 * Two levels: the shelf of packs, and one pack's own page listing what
 * it brings, by axis. A pack you removed stays on the shelf, marked —
 * otherwise removal would be indistinguishable from the pack never
 * having existed.
 */
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Check, Plus, Store, X } from '../../lib/icons';
import { PageHeader, SectionHeader } from '../../components/shell';
import { Card } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { cn } from '../../lib/utils';
import { undoableAction } from '../../components/banners/stagedAction';
import { usePreference } from '../../preferences';
import { useMods } from '../context';
import { MODS_PAGE_HREF, MODS_STORE_HREF } from '../href';
import { accentSeed } from './items/theme';
import { modById } from './items/mods';
import { itemById } from './index';
import { PACKS, packById, removable, type Pack } from './packs';
import { useShelves } from './useOffered';
import { AXIS_UI, defaultOf } from './axes';
import type { ModSetting } from '../../preferences/registry';

/** How many shelves a pack reaches, and how many items it brings. */
const size = (p: Pack) => {
  const axes = Object.entries(p.items).filter(([, ids]) => ids.length > 0);
  return { axes: axes.length, items: axes.reduce((n, [, ids]) => n + ids.length, 0) };
};

/** The dot a tile can honestly draw: a colour the item IS. Everything
 *  else paints from the root and is seen by being worn. */
function dotOf(axis: string, id: string, mode: 'light' | 'dark'): string | undefined {
  if (axis === 'theme') return accentSeed(id, mode);
  const mod = axis === 'mods' ? modById(id) : undefined;
  return mod ? accentSeed(mod.accent, mode) : undefined;
}

export function ModsStorePage() {
  const { pack: packParam } = useParams();
  const pack = packParam ? packById(packParam) : undefined;
  if (packParam && !pack) return <NoSuchPack what={packParam} />;
  return pack ? <PackPage pack={pack} /> : <PackShelf />;
}

/** Install and remove, with the way back — removing a pack can take a
 *  wallpaper, a cue set and an accent off the screen in one click. */
function useInstaller() {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  const { hasPack, install, uninstall } = useShelves();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');
  const { value: keyPack, setValue: setKeyPack } = usePreference('mods.sound.keyboard.pack');

  const prefOf = (key: string) => (key === 'mods.sound.pack' ? soundPack : keyPack);
  const setPref = (key: string, id: string) =>
    (key === 'mods.sound.pack' ? setSoundPack : setKeyPack)(id);

  /** What the person is wearing on one axis, whatever kind of home it has. */
  const worn = (axis: string): string => {
    const home = AXIS_UI[axis]?.home;
    if (!home) return '';
    if (home.mod) return theme.mod ?? '';
    if (home.pref) return prefOf(home.pref);
    return String(theme[home.theme![0]] ?? '');
  };

  const wear = (axis: string, id: string) => {
    const home = AXIS_UI[axis]?.home;
    if (!home) return;
    if (home.mod) { setTheme({ mod: id } as Partial<ModSetting>); return; }
    if (home.pref) { setPref(home.pref, id); return; }
    setTheme(Object.fromEntries(home.theme!.map((f) => [f, id])) as Partial<ModSetting>);
  };

  /**
   * Taking a pack off while wearing one of its items would leave the app
   * painting something no picker offers and nothing can put back — the
   * trap `ModsLock` closes when a permission goes away, closed the same
   * way: the removal writes the reset, on every axis it touched.
   */
  const remove = (p: Pack) => {
    const wasWearing: [string, string][] = [];
    for (const [axis, ids] of Object.entries(p.items)) {
      const on = worn(axis);
      if (!on || !ids.includes(on)) continue;
      wasWearing.push([axis, on]);
      wear(axis, defaultOf(axis) ?? '');
    }
    uninstall(p.id);
    undoableAction({
      label: t('mods.store_removed', '{{pack}} removed', { pack: p.label }),
      undo: async () => {
        install(p.id);
        for (const [axis, id] of wasWearing) wear(axis, id);
      },
    });
  };

  const add = (p: Pack) => {
    install(p.id);
    undoableAction({
      label: t('mods.store_added', '{{pack}} installed', { pack: p.label }),
      undo: async () => uninstall(p.id),
    });
  };

  return { hasPack, add, remove };
}

function InstallButton({ pack, installed, add, remove, size: btn = 'sm' }: {
  pack: Pack; installed: boolean;
  add: (p: Pack) => void; remove: (p: Pack) => void; size?: 'sm' | 'default';
}) {
  const { t } = useTranslation();
  if (!installed) {
    return (
      <Button size={btn} onClick={() => add(pack)}>
        <Plus className="size-4" aria-hidden />
        {t('mods.store_install', 'Install')}
      </Button>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <Badge tone="ok">
        <Check className="size-3.5" aria-hidden />
        {t('mods.store_installed', 'Installed')}
      </Badge>
      {/* The pack that carries every axis's fallback has no Remove: a
          control that silently does nothing is worse than none. */}
      {removable(pack.id) && (
        <Button size="sm" variant="ghost" onClick={() => remove(pack)}
          aria-label={t('mods.store_remove', 'Remove {{pack}}', { pack: pack.label })}>
          <X className="size-4" aria-hidden />
        </Button>
      )}
    </div>
  );
}

function PackShelf() {
  const { t } = useTranslation();
  const { hasPack, add, remove } = useInstaller();
  return (
    <div className="space-y-6">
      <PageHeader
        title={t('mods.store_title', 'Mods store')}
        description={t('mods.store_desc',
          'A pack brings a wallpaper, its sounds and its colours together. Install one, then wear what you like from it.')}
        icon={Store}
        actions={(
          <Button variant="outline" size="sm" render={<Link to={MODS_PAGE_HREF} />}>
            <ArrowLeft className="size-4" />
            {t('mods.store_back', 'Your mods')}
          </Button>
        )}
      />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="store-packs">
        {PACKS.map((p) => {
          const installed = hasPack(p.id);
          const n = size(p);
          return (
            <Card key={p.id} className={cn('p-3 flex flex-col gap-3', !installed && 'opacity-60')}>
              <div className="min-w-0">
                <Link to={`${MODS_STORE_HREF}/${p.id}`} className="font-medium hover:underline">
                  {p.label}
                </Link>
                <p className="text-xs text-muted-foreground">{p.description}</p>
              </div>
              <p className="text-2xs text-muted-foreground">
                {t('mods.store_size', '{{items}} items across {{axes}} shelves',
                  { items: n.items, axes: n.axes })}
                {' · '}
                {t('mods.store_by', 'by {{who}}', { who: p.publisher })}
              </p>
              <div className="mt-auto flex items-center justify-between gap-2">
                <Link to={`${MODS_STORE_HREF}/${p.id}`}
                  className="text-xs text-muted-foreground hover:text-foreground min-h-tap inline-flex items-center">
                  {t('mods.store_whats_inside', "What's inside")}
                </Link>
                <InstallButton pack={p} installed={installed} add={add} remove={remove} />
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function PackPage({ pack }: { pack: Pack }) {
  const { t } = useTranslation();
  const { theme } = useMods();
  const { hasPack, add, remove } = useInstaller();
  const shelves = Object.entries(pack.items).filter(([, ids]) => ids.length > 0);
  return (
    <div className="space-y-6">
      <PageHeader
        title={pack.label}
        description={pack.description}
        icon={Store}
        meta={t('mods.store_by', 'by {{who}}', { who: pack.publisher })}
        actions={(
          <div className="flex items-center gap-3">
            <Button variant="outline" size="sm" render={<Link to={MODS_STORE_HREF} />}>
              <ArrowLeft className="size-4" />
              {t('mods.store_all', 'All packs')}
            </Button>
            <InstallButton pack={pack} installed={hasPack(pack.id)} add={add} remove={remove} />
          </div>
        )}
      />
      {shelves.map(([axis, ids]) => (
        <section key={axis} data-testid={`store-axis-${axis}`}>
          <SectionHeader description={AXIS_UI[axis]?.note}>
            {AXIS_UI[axis]?.label ?? axis}
          </SectionHeader>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {ids.map((id) => {
              const item = itemById(axis, id);
              const dot = dotOf(axis, id, theme.mode);
              return (
                <Card key={id} className="p-3 flex items-start gap-2">
                  {dot && (
                    <span aria-hidden className="size-4 rounded-full shrink-0 mt-0.5 border border-border"
                      style={{ background: dot }} />
                  )}
                  <div className="min-w-0">
                    <p className="font-medium truncate">{item?.label ?? id}</p>
                    <p className="text-xs text-muted-foreground">{item?.description ?? ''}</p>
                  </div>
                </Card>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function NoSuchPack({ what }: { what: string }) {
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Store}
        title="Not a pack"
        description={`There is no “${what}” in the store.`}
        actions={(
          <Button variant="outline" size="sm" render={<Link to={MODS_STORE_HREF} />}>
            <ArrowLeft className="size-4" />
            All packs
          </Button>
        )}
      />
    </div>
  );
}

export default ModsStorePage;

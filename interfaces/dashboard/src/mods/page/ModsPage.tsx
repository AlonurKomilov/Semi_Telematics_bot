/**
 * /mods — the same settings drawn as depth.
 *
 * Three levels, one component, the URL as the state:
 *
 *   /mods                     the hub — what is installed, and the four
 *                             categories with how far each is dialled
 *   /mods/:category           a grid of the category's items, each tile
 *                             saying whether it has been touched
 *   /mods/:category/:item     ONE item's control — the same component
 *                             the panel and the card compose into their
 *                             category block, rendered on its own
 *
 * It renders FROM THE TAXONOMY. Every tile, heading and route segment
 * comes from `mods/taxonomy.ts`; adding an item there adds it here with
 * no edit to this file. That is the whole reason the taxonomy exists as
 * one declaration — this page would otherwise have been its seventh
 * hand-written copy.
 *
 * Depth over density, deliberately. The card on /profile is the flat
 * list for somebody who knows what they want; this is the map for
 * somebody finding out what there is. Same data, two shapes, and the
 * panel in the top bar is the third — GX's own split.
 */
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft, LayoutGrid, Palette, Square, Layers, PenLine, Sparkles,
  Volume2, Bell, Zap, Monitor, Maximize2, Puzzle, type LucideIcon,
} from '../../lib/icons';
import { PageHeader, SectionHeader } from '../../components/shell';
import { Card } from '../../components/ui/card';
import { cn } from '../../lib/utils';
import { usePreference, preferences } from '../../preferences';
import { useMods } from '../context';
import { ModControls } from '../panel/ModControls';
import { ITEM_GROUPS, CATEGORY_CONTROLS } from '../panel/items';
import SizeCard from '../SizeCard';
import { modById } from '../packs/mods';
import { RotateCcw } from '../../lib/icons';
import { undoableAction } from '../../components/banners/stagedAction';
import { MOD_DEFAULT, DEFS } from '../../preferences/registry';
import {
  TAXONOMY, categoryById, browsableItemsOf, type CategoryId, type TaxonomyItem,
} from '../taxonomy';
import { MODS_PAGE_HREF, MODS_HREF } from '../href';
import {
  itemState, itemSummary, categoryTouched, categoryIntensity, type TileState,
} from './state';

/**
 * A glyph per tile. Presentation only — the taxonomy stays data, and a
 * tile with no entry here still renders, with the category's glyph.
 */
const ICONS: Record<string, LucideIcon> = {
  interface: Palette, sounds: Volume2, effects: Zap, size: Maximize2,
  theme: Palette, corners: Square, material: Layers, typeface: PenLine, icons: Sparkles,
  'sounds/interface': Volume2, keyboard: Volume2, alerts: Bell,
  motion: Zap, entrance: Zap, ambient: Monitor,
  global: Maximize2, regions: LayoutGrid,
};
const iconFor = (cat: CategoryId, item?: TaxonomyItem): LucideIcon =>
  (item && (ICONS[`${cat}/${item.id}`] ?? ICONS[item.id])) ?? ICONS[cat] ?? Puzzle;

const STATE_LABEL: Record<TileState, string> = {
  default: 'Default', changed: 'Changed', off: 'Off',
};

/** One tile. A link, because every level of this page is an address. */
function Tile({ to, icon: Icon, title, meta, state }: {
  to: string; icon: LucideIcon; title: string; meta?: string; state?: TileState;
}) {
  return (
    <Link
      to={to}
      className={cn(
        'group flex flex-col gap-3 p-4 rounded-lg border border-border bg-card',
        'hover:bg-muted/60 hover:border-primary/40 transition-colors min-h-tap',
        state === 'changed' && 'border-primary/40',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className={cn(
          'inline-flex size-9 items-center justify-center rounded-md',
          state === 'changed' ? 'bg-primary/15 text-foreground' : 'bg-muted text-muted-foreground',
        )}>
          <Icon className="size-4.5" />
        </span>
        {state && (
          <span className={cn(
            'text-2xs uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded',
            state === 'changed' && 'bg-primary/15 text-foreground',
            state === 'off' && 'bg-muted text-muted-foreground',
            state === 'default' && 'text-muted-foreground',
          )}>
            {STATE_LABEL[state]}
          </span>
        )}
      </div>
      <div className="min-w-0">
        <div className="text-sm font-medium text-foreground">{title}</div>
        {meta && <div className="text-xs text-muted-foreground tabular-nums mt-0.5">{meta}</div>}
      </div>
    </Link>
  );
}

function Crumb({ to, children }: { to: string; children: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground min-h-tap">
      <ArrowLeft className="size-3.5" />
      {children}
    </Link>
  );
}

const read = (k: string) => preferences.get(k as never) as unknown;

/** Level 0 — the hub. */
function Hub() {
  const { theme, size } = useMods();
  const installed = theme.mod ? modById(theme.mod) : undefined;
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Puzzle}
        title="Mods"
        description="How the app looks, moves and sounds — drawn as a map. Only affects what you see."
        actions={
          <Link to={MODS_HREF} className="text-xs text-muted-foreground hover:text-foreground min-h-tap inline-flex items-center">
            Flat list on your profile
          </Link>
        }
      />

      {/* The centre: what is installed. The same chip row the card and
          the popover render — a mod is a way of writing the axes, and
          this is where a person picks one before tuning it below. */}
      <Card render={<section />}>
        {/* The description is for the NO-MOD case only. When a mod is
            installed, `ModControls` already prints its `why` under the
            chip in force (ModPanel.tsx) — repeating it here put the same
            sentence on the screen twice. */}
        <SectionHeader
          size="card"
          description={installed ? undefined : 'No mod installed — every category is set by hand.'}
        >
          {installed ? installed.label : 'Your own'}
        </SectionHeader>
        <div className="mt-3 max-w-2xl">
          <ModControls section="mods" />
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="mods-hub">
        {TAXONOMY.map((cat) => {
          const pct = categoryIntensity(cat, theme, size, read);
          const { changed, total } = categoryTouched(cat, theme, read);
          return (
            <Tile
              key={cat.id}
              to={`${MODS_PAGE_HREF}/${cat.id}`}
              icon={iconFor(cat.id)}
              title={cat.title}
              meta={pct !== null ? `${pct}%` : `${changed} of ${total} changed`}
              state={changed > 0 ? 'changed' : 'default'}
            />
          );
        })}
      </div>
    </div>
  );
}

/** The caps label an item wears on its own page — §4's canonical step. */
const PAGE_LABEL = 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

/** Level 1 — a category's items, under the category's own control. */
function CategoryGrid({ id }: { id: CategoryId }) {
  const cat = categoryById(id)!;
  const { theme } = useMods();
  const Own = CATEGORY_CONTROLS[cat.id];
  return (
    <div className="space-y-6">
      <Crumb to={MODS_PAGE_HREF}>Mods</Crumb>
      <PageHeader icon={iconFor(cat.id)} title={cat.title} />
      {/* What the category owns and every item under it shares — the
          sound level and cue set. Above the tiles rather than repeated
          on each item page: a person setting the volume is not setting
          the keyboard. */}
      {Own && (
        <Card render={<section />} data-testid="mods-category-own">
          <Own label={PAGE_LABEL} />
        </Card>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="mods-category">
        {browsableItemsOf(cat.id).map((item) => (
          <Tile
            key={item.id}
            to={`${MODS_PAGE_HREF}/${cat.id}/${item.id}`}
            icon={iconFor(cat.id, item)}
            title={item.title}
            // The VALUE, not merely that it moved. A tile that says
            // "Changed" makes a person click to find out what; one that
            // says "Pill" has already answered.
            meta={itemSummary(item, theme, read) ?? undefined}
            state={itemState(item, theme, read)}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * "Reset <item>", for one item.
 *
 * The category reset on the profile card restores every axis of the
 * category at once; a page about one thing offers to put back that one
 * thing. Its targets are the item's own declaration — `axes` minus
 * `keep` to the registry default, `prefs` to theirs — so an item that
 * gains an axis gains a reset for it without anyone editing this.
 */
function ItemReset({ item }: { item: TaxonomyItem }) {
  const { theme, setTheme } = useMods();
  const axes = item.axes.filter((a) => !(item.keep ?? []).includes(a));
  const prefs = item.prefs ?? [];
  const axisDefault = (a: string) => (MOD_DEFAULT as unknown as Record<string, unknown>)[a];
  const prefDefault = (k: string) => (DEFS as unknown as Record<string, { default: unknown }>)[k].default;
  const readAxis = (a: string) => (theme as unknown as Record<string, unknown>)[a];

  const atDefault = axes.every((a) => readAxis(a) === axisDefault(a))
    && prefs.every((k) => read(k) === prefDefault(k));
  if (atDefault) return null;

  const reset = () => {
    // Snapshot before the write — this is the person's own configuration.
    const wasAxes = Object.fromEntries(axes.map((a) => [a, readAxis(a)]));
    const wasPrefs = Object.fromEntries(prefs.map((k) => [k, read(k)]));
    setTheme(Object.fromEntries(axes.map((a) => [a, axisDefault(a)])));
    for (const k of prefs) preferences.set(k as never, prefDefault(k) as never);
    undoableAction({
      label: `${item.title} reset`,
      undo: async () => {
        setTheme(wasAxes);
        for (const k of prefs) preferences.set(k as never, wasPrefs[k] as never);
      },
    });
  };

  return (
    <button
      type="button"
      onClick={reset}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground shrink-0 min-h-tap"
    >
      <RotateCcw className="size-3.5" />
      Reset {item.title.toLowerCase()}
    </button>
  );
}

/** Level 2 — ONE item's control, or SizeCard for either half of Size. */
function ItemControl({ id, itemId }: { id: CategoryId; itemId: string }) {
  const cat = categoryById(id)!;
  const item = browsableItemsOf(cat.id).find((i) => i.id === itemId);
  if (!item) return <NotHere what={`${cat.id}/${itemId}`} />;
  const Item = ITEM_GROUPS[`${cat.id}/${item.id}`];
  return (
    <div className="space-y-6">
      <Crumb to={`${MODS_PAGE_HREF}/${cat.id}`}>{cat.title}</Crumb>
      <PageHeader
        icon={iconFor(cat.id, item)}
        title={item.title}
        actions={cat.id === 'size' ? undefined : <ItemReset item={item} />}
      />
      {/* SizeCard is already a Card of its own (it owns #interface-size
          and its pinned styles); wrapping it again would enclose a box
          in a box and mount that anchor id twice. Its two items are the
          two halves of that one card, so either address renders it
          whole — the one place on this page a tile does not open on a
          single thing, and `panel: false` in the taxonomy says why. */}
      {cat.id === 'size'
        ? <div data-testid="mods-item"><SizeCard /></div>
        : Item
          ? (
            <Card render={<section />} data-testid="mods-item">
              <Item label={PAGE_LABEL} />
            </Card>
          )
          : <NotHere what={`${cat.id}/${itemId}`} />}
    </div>
  );
}

function NotHere({ what }: { what: string }) {
  return (
    <div className="space-y-6">
      <Crumb to={MODS_PAGE_HREF}>Mods</Crumb>
      <PageHeader icon={Puzzle} title="Not a category" description={`There is no “${what}” here.`} />
    </div>
  );
}

export default function ModsPage() {
  const { category, item } = useParams<{ category?: string; item?: string }>();
  // Subscribes this page to the sound gates so a tile's state re-renders
  // when a switch on the control level flips — `preferences.get` alone
  // would read fresh but never re-render.
  usePreference('mods.sound.ui'); usePreference('mods.sound.keyboard');
  usePreference('dispatch.soundOn'); usePreference('mods.ambient');
  usePreference('mods.sound.volume');

  if (!category) return <Hub />;
  const cat = categoryById(category);
  if (!cat) return <NotHere what={category} />;
  if (!item) return <CategoryGrid id={cat.id} />;
  return <ItemControl id={cat.id} itemId={item} />;
}

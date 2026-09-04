/**
 * /mods — the same settings drawn as depth.
 *
 * Three levels, one component, the URL as the state:
 *
 *   /mods                     the hub — what is installed, and the four
 *                             categories with how far each is dialled
 *   /mods/:category           a grid of the category's items, each tile
 *                             saying whether it has been touched
 *   /mods/:category/:item     the item's control — the SAME `Section`
 *                             the profile card renders, so the page and
 *                             the card cannot answer differently
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
} from 'lucide-react';
import { PageHeader, SectionHeader } from '../../components/shell';
import { Card } from '../../components/ui/card';
import { cn } from '../../lib/utils';
import { usePreference, preferences } from '../../preferences';
import { useMods } from '../context';
import { ModControls } from '../panel/ModControls';
import { Section, SECTION_AXES } from '../Modifications';
import SizeCard from '../SizeCard';
import { modById } from '../catalogue';
import { TAXONOMY, categoryById, type CategoryId, type TaxonomyItem } from '../taxonomy';
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

/** Level 1 — a category's items. */
function CategoryGrid({ id }: { id: CategoryId }) {
  const cat = categoryById(id)!;
  const { theme } = useMods();
  return (
    <div className="space-y-6">
      <Crumb to={MODS_PAGE_HREF}>Mods</Crumb>
      <PageHeader icon={iconFor(cat.id)} title={cat.title} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="mods-category">
        {cat.items.map((item) => (
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

/** Level 2 — one item's control: the card's own Section, or SizeCard. */
function ItemControl({ id, itemId }: { id: CategoryId; itemId: string }) {
  const cat = categoryById(id)!;
  const item = cat.items.find((i) => i.id === itemId);
  return (
    <div className="space-y-6">
      <Crumb to={`${MODS_PAGE_HREF}/${cat.id}`}>{cat.title}</Crumb>
      <PageHeader icon={iconFor(cat.id, item)} title={item?.title ?? cat.title} />
      {/* SizeCard is already a Card of its own (it owns #interface-size
          and its pinned styles); wrapping it again would enclose a box
          in a box and mount that anchor id twice. The other categories
          render the profile card's Section, standalone, inside one Card. */}
      {cat.id === 'size'
        ? <div data-testid="mods-item"><SizeCard /></div>
        : (
          <Card render={<section />} data-testid="mods-item">
            {cat.id === 'sounds'
              ? <Section id="sounds" title="Sounds" label="Reset sounds" axes={{}} standalone />
              : <Section id={cat.id} title={cat.title} label={`Reset ${cat.title.toLowerCase()}`}
                  axes={SECTION_AXES[cat.id]} standalone />}
          </Card>
        )}
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

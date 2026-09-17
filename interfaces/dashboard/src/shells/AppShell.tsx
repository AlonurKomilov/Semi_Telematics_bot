/**
 * AppShell — the chrome every persona renders inside.
 *
 * Sidebar, mobile drawer, topbar, and the rounded content card that
 * holds the routed page. It was six files long: DefaultShell and the
 * five persona shells were 118–151 lines each and byte-identical apart
 * from ONE prop — whether the topbar's middle zone gets a persona hero.
 * Nothing else varied. No shell passed anything to `Sidebar`, which
 * derives its nav from the active persona on its own, so the doc
 * comments claiming "the difference is the sidebar's nav config" were
 * describing something the code never did.
 *
 * Two topbar variants also existed — one with `justify-between` and a
 * `min-w-0` left cluster, one without and `shrink-0`. Measured in
 * Chrome at 1280 and 420px: identical geometry in all four
 * combinations, because every occupant of the middle zone (persona
 * hero, feature hero, and the plain spacer ShellHero falls back to) is
 * `flex-1`, so `justify-content` never has free space to distribute.
 * One header now.
 *
 * The per-persona files stay: each is a few lines that names its hero,
 * and that is deliberately the seam where a Fleet-only or Safety-only
 * change goes without touching the other five.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Search, Menu, X } from '../lib/icons';

import Sidebar from '../components/Sidebar';
import PendingInviteBanner from '../components/PendingInviteBanner';
import { DocumentLock } from './DocumentLock';
import MobileNavDrawer from '../components/shell/MobileNavDrawer';
import CommandPalette from '../components/shell/CommandPalette';
import KeyboardShortcuts from '../components/shell/KeyboardShortcuts';
import {
  entranceById, ModPanel, useMods, surfaceFor, ModsLock, useCanMods,
  pageWallpaperFor, resolveWallpaper, usePageCue,
} from '../mods';
import { LanguageSelector } from '../components/LanguageSelector';
import { AvatarMenu } from '../components/AvatarMenu';
import { ChatLauncher } from '../features/chat/ChatLauncher';
import { AssistantLauncher } from '../features/ai/AssistantLauncher';
import { AlertsLauncher } from '../features/alerts/AlertsLauncher';
import { useAssistantDock, useDockedContentClass } from '../features/ai/AssistantContext';
import AssistantPanel from '../features/ai/AssistantPanel';
import { shortcut } from '../utils/platform';
import { sizeRegion } from '@/lib/sizeRegion';
import ShellHero from './heroes/ShellHero';
import { SHELL_SCROLLPORT_ATTR } from '../lib/scrollport';

export default function AppShell({ hero }: { hero?: ReactNode }) {
  const { theme } = useMods();
  const entranceClasses = entranceById(theme.entrance)?.classes;
  // Mods is a service: a role without it gets no palette in the bar,
  // and `ModsLock` below puts its stored look back to the defaults.
  const canMods = useCanMods();
  const { pathname } = useLocation();
  // HERE because this is the one component that stays mounted across
  // every route change — it renders the <Outlet/> the pages appear in,
  // so a hook inside a page would hear its own arrival and nothing
  // else. The gates are read at play time, in `cue.ts`.
  usePageCue();
  const dockedContentClass = useDockedContentClass();
  const dock = useAssistantDock();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      } else if (
        e.key === '/' &&
        (e.target as HTMLElement)?.tagName !== 'INPUT' &&
        (e.target as HTMLElement)?.tagName !== 'TEXTAREA'
      ) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Which named place this route is, stamped on <html> so the injector's
  // `:root[data-surface="…"]` block can take effect. On <html> rather
  // than on a wrapper because three MutationObservers read theme tokens
  // off the document element — a token scoped to a subtree would be
  // invisible to the 3D truck, the DataGrid canvas and the radius
  // reader, and they would paint the previous page's colours.
  useEffect(() => {
    const s = surfaceFor(pathname);
    const root = document.documentElement;
    if (s) root.dataset.surface = s.id;
    else delete root.dataset.surface;
    // The page's pattern follows the place: a named place may hold its
    // own. The engine stamps the same answer on every theme change, both
    // through one resolver, so the two writers cannot disagree.
    // Resolved, like the other writer — the two must agree, and `desk`
    // is not something the stylesheet answers.
    root.dataset.wallpaperPage = resolveWallpaper(
      pageWallpaperFor(theme, s?.id ?? null), theme.wallpaperDesk,
    );
  }, [pathname, theme]);

  // `relative` on the two GROUNDS below: a wallpaper's wash is a
  // `::before` at `inset: 0`, and it anchors to the nearest positioned
  // ancestor. The packs used to declare that themselves, and it cost two
  // fixed panels their position — an unlayered rule beats a utility — so
  // the element says it now. `mods/wallpaper.test.ts` holds both halves.
  return (
    <div className="relative flex flex-col h-screen overflow-hidden bg-background text-foreground desk-ground">
      <ModsLock />
      {/* Nothing here scrolls the document — see DocumentLock. */}
      <DocumentLock />
      {/* A row of its own INSIDE the viewport box. It used to render in
          App.tsx, above a shell that is exactly 100vh, which made the
          document taller than the window every time an invite link was
          open — the one in-flow thing above the box, and the shape of
          bug the lock exists to make impossible. Here it takes its
          height from the shell and the work below simply gets less. */}
      {/* THE FRAME'S GROUND, and it is an element of its own now.
          The root above used to carry `chrome-ground` itself, which
          made one element the BOTTOM OF THE WINDOW and the FRAME'S
          GROUND at the same time. That double duty is the same shape as
          the two bugs fixed just before this one — the gutters that
          were padding, the split that was a margin — and it has the
          same consequence: a thing with two jobs can only ever be given
          one of them.
          Three planes now, bottom to top. The ROOT is the desk: the
          plane everything sits on, and where a wallpaper that spans the
          whole window will paint. THIS is the frame's, covering the
          window and painting only in the gaps the frame leaves. The
          PAGE cards carry the third. Each one can be given its own
          pattern without borrowing another's origin or scale, which is
          the thing two planes could not express: one continuous pattern
          across the whole window, at one size.
          A WRAPPER rather than a plane at `inset-0`, deliberately. A
          parent's background paints below its children's with no
          z-index at all; an absolutely positioned sibling paints ABOVE
          in-flow content and would need a negative z-index and a
          stacking context on the root to be pushed back down — two more
          things to keep true. It also keeps `--ground` inheriting into
          every frame pane, which is where glass reads the tint that
          tells a pane over the rail from a pane over the page.
          `relative` because a live pattern's wash is a `::before` at
          `inset: 0` and anchors to the nearest positioned ancestor. */}
      <div className="relative flex flex-col flex-1 min-h-0 chrome-ground">
      <PendingInviteBanner />
      {/* Recedes in ambient mode — see the [data-ambient] block in
          index.css. Marked rather than selected by shape, so a shell
          refactor cannot silently take the mode's meaning with it. */}
      {/* `chrome-ground` is where the frame's wallpaper paints, and
          every chrome surface — the sidebar, the header, the gutters —
          is a `chrome-pane` that steps aside for it. Both are CLASSES
          rather than shape selectors: the pattern must not start
          following whatever div happens to be first in this file, the
          same reason `data-ambient-recede` is a marker.
          `shellMarkers.test.ts` holds both. */}
      {/* Sidebar and content, side by side, in what is left of the
          viewport after the rows above it. `min-h-0` so the envelope's
          own scroller shrinks instead of pushing the box open. */}
      <div className="flex flex-1 min-h-0">
        <div className="hidden lg:block" data-ambient-recede>
          <Sidebar />
        </div>

        <MobileNavDrawer
          open={mobileSidebarOpen}
          onOpenChange={setMobileSidebarOpen}
        />

        {/* PURE LAYOUT. It paints nothing and marks nothing, and that is
            the point: every pixel of chrome now belongs to a side that
            is its own element, so a rule written for the frame reaches
            all four of them.
            It used to paint the chrome itself and leave the right and
            bottom edges as `pr-2 pb-2` — 8px of PADDING. Padding has no
            boundary, so two of the four sides could not take a border,
            a rim, a radius or a lens no matter what any material asked
            for, and a mods change landed on the rail and the header
            only. That asymmetry is what the owner kept hitting.
            It also could not be fixed by marking this element, which is
            why it was left out for so long: it wraps the header AND
            every page, so `.surface` here bought a frosted 8px frame
            and cost a VIEWPORT-SIZED backdrop root over every card in
            the app — under Glass a `backdrop-filter` on an ancestor
            makes a descendant's own filter a no-op, which is why the
            persona menu showed the sidebar through itself CRISP rather
            than smeared. A gutter rendered as a SIBLING of <main> is
            nobody's ancestor, so it carries the full frame class list
            with none of that cost. */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Three zones: mobile-menu (left), hero (middle, flex-1), tools
              (right). The hero lives INSIDE the h-12 strip rather than in
              a row of its own, so content sits at the same Y whether or
              not a persona contributes one.
              `controls` is this strip's Size region — see lib/sizeRegion. */}
          <header
            data-ambient-recede
            style={sizeRegion('controls')}
            className="h-12 bg-sidebar surface surface-sidebar chrome-pane text-sidebar-foreground flex items-center px-2 sm:px-3 lg:px-4 shrink-0 gap-2 sm:gap-3"
          >
            <div className="flex items-center gap-3 shrink-0">
              <button
                onClick={() => setMobileSidebarOpen((o) => !o)}
                className="lg:hidden inline-flex size-8 min-h-tap min-w-tap items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
                aria-label="Toggle navigation"
              >
                {mobileSidebarOpen ? <X className="size-4.5" /> : <Menu className="size-4.5" />}
              </button>
            </div>

            {/* Route-aware: a feature hero (Maintenance counts on
                /maintenance) takes the slot; the persona hero is the
                cross-cutting fallback, and personas without one fall back
                to a plain spacer. */}
            <ShellHero fallback={hero} />

            <div className="flex items-center gap-1 sm:gap-2 shrink-0">
              <button
                onClick={() => setPaletteOpen(true)}
                className="hidden md:inline-flex items-center gap-2 px-3 py-1.5 min-h-tap text-xs text-muted-foreground bg-muted/40 border border-border rounded-md hover:bg-muted hover:text-foreground transition w-55 lg:w-70"
                aria-label="Open command palette"
              >
                <Search className="size-3.5" />
                <span>Search…</span>
                <kbd className="ml-auto px-1.5 py-0.5 text-2xs border border-border rounded bg-card">
                  {shortcut('K')}
                </kbd>
              </button>
              <button
                onClick={() => setPaletteOpen(true)}
                className="md:hidden inline-flex size-8 min-h-tap min-w-tap items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
                aria-label="Open search"
              >
                <Search className="size-4.5" />
              </button>
              <LanguageSelector />
              <AlertsLauncher />
              <ChatLauncher />
              <AssistantLauncher />
              {canMods && <ModPanel />}
              <AvatarMenu />
            </div>
          </header>

          {/* <main> is the rounded content card. Its OWN overflow is hidden
              so the corners clip cleanly, and an inner div scrolls —
              without that split the scrollbar renders at <main>'s right
              edge, visually leaking into the chrome frame because the
              rounded corner curves away from its straight track.
              `text` is this card's Size region.

              THE BORDER LIVES HERE, not on the <header>. The header used to
              carry `border-b`, which is a straight 1px line across the full
              frame — and this card's top corners curve away from it. That
              left a quarter-disc of frame showing under each end of the
              line: 4px at Sharp, 14px at Rounded, 20px at Pill. The line and
              the corner were two objects describing one boundary, so the
              bigger the radius the further apart they read.

              One object now. The card's own edge IS the boundary, so it
              follows the radius exactly and cannot disagree with it at any
              preset. Sidebar, header and gutters are all `bg-sidebar` — one
              continuous chrome surface — and the card is the thing sitting
              in it, which is what the outline says. */}
          {/* The content row: the card, and the right side of the frame
              beside it. */}
          <div className="flex flex-1 min-h-0">
          <main
            style={sizeRegion('text')}
            // Always a page ground: the class only says which colour is the
            // ground here. A pattern reaches it through `data-wallpaper-page`
            // on <html>, and with `none` there is no rule — `bg-background`
            // paints the card as it always has.
            className={`relative flex-1 min-w-0 bg-background border border-border rounded-xl overflow-hidden page-ground ${dockedContentClass}`}
          >
          {/* THE page scrollport, named so a caller with no element in
              hand can reach it — see lib/scrollport. */}
          <div
            {...{ [SHELL_SCROLLPORT_ATTR]: "" }}
            className="h-full overflow-y-auto [scrollbar-gutter:stable] scroll-pb-16 p-4 lg:p-6"
          >
              {/* An entrance for the routed page, when one is asked for.
                  Off by default and on purpose: this app is navigated
                  dozens of times an hour, and a slide-in on every one of
                  them is a tax rather than a delight.

                  One wrapper reaches every route because there is exactly
                  one <Outlet/> and all six shells go through it. Keyed on
                  the pathname so React remounts it and the animation
                  actually replays; the duration rides --motion-scale like
                  everything else, and the reduced-motion floor turns it
                  off entirely.

                  WHICH movement is an item now, so the classes come from
                  the shelf rather than being spelled here — and they are
                  literal in the item's own file, because Tailwind reads
                  source text and a class assembled at runtime is never
                  built at all. */}
              {theme.entranceOn && entranceClasses ? (
                // `h-full` IS LOAD-BEARING, and it is the reason this
                // wrapper may exist at all.
                //
                // A page that fills the screen asks for it with `h-full`,
                // and `height: 100%` needs a parent with a definite
                // height.  The scrollport above has one; this wrapper did
                // not, so switching the entrance animation ON quietly put
                // a `height: auto` box in the middle of the chain and
                // every full-height page fell back to content height.
                //
                // The Live Map is where it showed: its two columns are a
                // map and a 98-row vehicle list, so the row took the
                // list's height and the map grew to match — a map several
                // screens tall with tiles only at the top, and a page
                // that scrolled when this shell's whole contract is that
                // it does not.  Invisible to anyone with entrances off,
                // which is why it lived.
                //
                // A decoration may not change the box it decorates.
                <div key={pathname} className={`h-full ${entranceClasses}`}>
                  <Outlet />
                </div>
              ) : (
                <Outlet />
              )}
            </div>
          </main>
            {/* THE RIGHT SIDE OF THE FRAME — and the SPLIT between the
                two pages when the assistant is open. It is one element
                doing both jobs, because they are the same job: the
                chrome that ends a page. It takes the page's own class
                so it is there exactly when the page beside it is.
                Same class list as the rail and the header, because they
                are the same object seen from four directions — the
                guard in `frame.test.ts` is what keeps that true rather
                than this comment. `aria-hidden`: they carry no content
                and a screen reader announcing empty groups around every
                page is noise.
                32px, and the number is the LENS's, not a taste. A bevel
                bends within a band measured from the edge, and the
                pack's band is 30px — so a side thinner than that is
                displaced end to end, which is distortion rather than a
                rim. At 8px these three were 3.75× inside the band and
                no per-edge trick changed it. At 32px a band of half the
                side fits, which is the width a refracting frame needs
                before it can have one. The page pays 24px for it. */}
            <div
              aria-hidden
              className={`w-8 shrink-0 bg-sidebar surface surface-sidebar chrome-pane ${dockedContentClass}`}
            />
            {/* THE SUB-PAGE, in the row rather than over it. It renders
                nothing unless it is open, allowed and off the /ai
                routes — `useAssistantDock` is that one answer, and the
                gutter below reads the same one, so the frame can never
                close around a page that is not there. */}
            <AssistantPanel />
            {dock.docked && (
              <div
                aria-hidden
                className="w-8 shrink-0 bg-sidebar surface surface-sidebar chrome-pane"
              />
            )}
          </div>
          <div
            aria-hidden
            className="h-8 shrink-0 bg-sidebar surface surface-sidebar chrome-pane"
          />
        </div>
      </div>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <KeyboardShortcuts onOpenSearch={() => setPaletteOpen(true)} />
    </div>
  );
}

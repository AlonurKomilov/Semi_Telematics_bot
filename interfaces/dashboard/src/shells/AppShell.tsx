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
import { Outlet } from 'react-router-dom';
import { Search, Menu, X } from 'lucide-react';

import Sidebar from '../components/Sidebar';
import MobileNavDrawer from '../components/shell/MobileNavDrawer';
import CommandPalette from '../components/shell/CommandPalette';
import KeyboardShortcuts from '../components/shell/KeyboardShortcuts';
import { ThemeToggle } from '../components/ThemeToggle';
import { LanguageSelector } from '../components/LanguageSelector';
import { AvatarMenu } from '../components/AvatarMenu';
import { AssistantLauncher } from '../features/ai/AssistantLauncher';
import { AlertsLauncher } from '../features/alerts/AlertsLauncher';
import { useDockedContentClass } from '../features/ai/AssistantContext';
import { shortcut } from '../utils/platform';
import { sizeRegion } from '@/lib/sizeRegion';
import ShellHero from './heroes/ShellHero';

export default function AppShell({ hero }: { hero?: ReactNode }) {
  const dockedContentClass = useDockedContentClass();
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

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      <MobileNavDrawer
        open={mobileSidebarOpen}
        onOpenChange={setMobileSidebarOpen}
      />

      {/* The chrome envelope around the content card. `bg-sidebar` paints
          the chrome colour; `pr-2 pb-2` leaves an 8px frame to the right
          and below, so the chrome wraps the content on every side — top
          from the header, left from the sidebar. */}
      <div className="flex-1 flex flex-col overflow-hidden bg-sidebar pr-2 pb-2">
        {/* Three zones: mobile-menu (left), hero (middle, flex-1), tools
            (right). The hero lives INSIDE the h-12 strip rather than in
            a row of its own, so content sits at the same Y whether or
            not a persona contributes one.
            `controls` is this strip's Size region — see lib/sizeRegion. */}
        <header
          style={sizeRegion('controls')}
          className="h-12 border-b border-border bg-sidebar text-sidebar-foreground flex items-center px-3 lg:px-4 shrink-0 gap-3"
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

          <div className="flex items-center gap-2 shrink-0">
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
            <AssistantLauncher />
            <ThemeToggle />
            <AvatarMenu />
          </div>
        </header>

        {/* <main> is the rounded content card. Its OWN overflow is hidden
            so the corners clip cleanly, and an inner div scrolls —
            without that split the scrollbar renders at <main>'s right
            edge, visually leaking into the chrome frame because the
            rounded corner curves away from its straight track.
            `text` is this card's Size region. */}
        <main
          style={sizeRegion('text')}
          className={`flex-1 bg-background rounded-xl overflow-hidden ${dockedContentClass}`}
        >
          <div className="h-full overflow-y-auto [scrollbar-gutter:stable] scroll-pb-16 p-4 lg:p-6">
            <Outlet />
          </div>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <KeyboardShortcuts onOpenSearch={() => setPaletteOpen(true)} />
    </div>
  );
}

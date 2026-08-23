/**
 * DefaultShell — the original Layout, owned by Owner / Admin.
 *
 * This is the all-encompassing dashboard wrapper: full sidebar (all
 * groups via ``defaultNav``), generic header, role-preview banner when
 * Owner/Admin is viewing as another persona.  Fleet / Dispatch /
 * Safety personas will get their own shells (FleetShell, etc.) that
 * subclass-via-composition: same building blocks, different nav
 * config + landing emphasis + hero widget.
 *
 * Phase 0 of the role-shell migration moved this file from
 * components/Layout.tsx → shells/DefaultShell.tsx unchanged.  The
 * router now selects which shell to render based on the active
 * persona; for Owner/Admin (the only switchable roles) and for any
 * persona whose dedicated shell isn't built yet, this default shell
 * renders.
 */
import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import ShellHero from './heroes/ShellHero';
import { Search, Menu, X } from 'lucide-react';
import Sidebar from '../components/Sidebar';
import MobileNavDrawer from '../components/shell/MobileNavDrawer';
import { ThemeToggle } from '../components/ThemeToggle';
import { LanguageSelector } from '../components/LanguageSelector';
import { AvatarMenu } from '../components/AvatarMenu';
import { AssistantLauncher } from '../features/ai/AssistantLauncher';
import { AlertsLauncher } from '../features/alerts/AlertsLauncher';
import { useDockedContentClass } from '../features/ai/AssistantContext';
import { shortcut } from '../utils/platform';
import CommandPalette from '../components/shell/CommandPalette';
import KeyboardShortcuts from '../components/shell/KeyboardShortcuts';

export default function DefaultShell() {
  const dockedContentClass = useDockedContentClass();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  // Preview banner + breadcrumb were both removed — the persona pill in
  // the sidebar already tells you which view you're in (clicking it
  // exits to Owner), and the role-based navigation makes a breadcrumb
  // trail redundant.  Keeps the topbar uncluttered, matching the
  // Samsara reference.

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      } else if (e.key === '/' && (e.target as HTMLElement)?.tagName !== 'INPUT' && (e.target as HTMLElement)?.tagName !== 'TEXTAREA') {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      {/* Desktop sidebar */}
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      {/* Mobile sidebar drawer */}
      <MobileNavDrawer
        open={mobileSidebarOpen}
        onOpenChange={setMobileSidebarOpen}
      />

      {/* Right column is the chrome envelope around the content card.
          ``bg-sidebar`` paints the chrome colour, ``pr-2 pb-2`` leaves
          an 8px frame to the right and below the content so the
          chrome wraps the whole content panel — top from the header,
          left from the sidebar, right and bottom from this padding.
          Result: a single rounded "card" sits inside a chrome window,
          matching the Samsara reference where the chrome surrounds
          the content on every side. */}
      <div className="flex-1 flex flex-col overflow-hidden bg-sidebar pr-2 pb-2">
        {/* Two-zone header: mobile-menu / spacer (left), tools cluster
            (right) with search grouped alongside language / theme /
            avatar.  Centering the search competed visually with the
            persona Hero strip below it on Fleet / Dispatch / Safety
            shells — pushing it back to the right cluster lets the
            Hero stand on its own and harmonises the topbar across
            every persona view. */}
        <header className="h-12 border-b border-border bg-sidebar text-sidebar-foreground flex items-center justify-between px-3 lg:px-4 shrink-0 gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => setMobileSidebarOpen((o) => !o)}
              className="lg:hidden inline-flex size-8 items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
              aria-label="Toggle navigation"
            >
              {mobileSidebarOpen ? <X className="size-4.5" /> : <Menu className="size-4.5" />}
            </button>
          </div>

          {/* Route-aware feature hero (Maintenance counts on
              /maintenance, more features over time).  These shells
              have no persona hero, so off-feature routes render the
              plain spacer — topbar layout unchanged. */}
          <ShellHero />

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setPaletteOpen(true)}
              className="hidden md:inline-flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground bg-muted/40 border border-border rounded-md hover:bg-muted hover:text-foreground transition w-[220px] lg:w-[280px]"
              aria-label="Open command palette"
            >
              <Search className="size-3.5" />
              <span>Search…</span>
              <kbd className="ml-auto px-1.5 py-0.5 text-3xs border border-border rounded bg-card">
                {shortcut('K')}
              </kbd>
            </button>
            <button
              onClick={() => setPaletteOpen(true)}
              className="md:hidden inline-flex size-8 items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
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

        {/* <main> is the rounded content card.  Its OWN overflow is
            hidden so the rounded corners clip cleanly; an inner div
            handles scrolling.  Without this split, the scrollbar
            renders at <main>'s right edge — visually leaking into
            the chrome frame because the rounded corner curves away
            from the scrollbar's straight vertical track.  Putting
            the scrollbar on the inner div (which sits inside the
            rounded shell) keeps it visually inside the card. */}
        <main className={`flex-1 bg-background rounded-xl overflow-hidden ${dockedContentClass}`}>
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

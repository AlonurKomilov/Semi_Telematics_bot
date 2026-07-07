/**
 * FleetShell — wrapper for the Fleet persona.
 *
 * Phase 2 of the role-shell migration: this file is currently an exact
 * clone of DefaultShell so behavior is unchanged.  Phase 3 will swap
 * in ``fleetNav`` (Fleet-emphasized sidebar order) and Phase 4 will
 * add a Fleet hero (vehicle status counts, fuel summary) above the
 * main column.  Until those phases land the only difference between
 * this shell and DefaultShell is the file you're editing — which is
 * the point: future Fleet-only changes go HERE without touching the
 * shared shell.
 */
import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Search, Menu, X } from 'lucide-react';
import Sidebar from '../components/Sidebar';
import { ThemeToggle } from '../components/ThemeToggle';
import { LanguageSelector } from '../components/LanguageSelector';
import { AvatarMenu } from '../components/AvatarMenu';
import CommandPalette from '../components/shell/CommandPalette';
import KeyboardShortcuts from '../components/shell/KeyboardShortcuts';
import FleetHero from './heroes/FleetHero';
import ShellHero from './heroes/ShellHero';

export default function FleetShell() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

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
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      {mobileSidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 z-50 bg-black/50"
          onClick={() => setMobileSidebarOpen(false)}
        >
          <div className="h-full" onClick={(e) => e.stopPropagation()}>
            <Sidebar />
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-col overflow-hidden bg-sidebar pr-2 pb-2">
        {/* Three-zone header: mobile-menu (left), persona Hero chips
            (middle, flex-1), tools cluster (right).  The Hero lives
            INSIDE the topbar — no separate row below — so the chrome
            stays a single h-12 strip and content sits at the same Y
            position regardless of whether a persona shell adds a
            status strip or not.  Default shell (Owner/Admin) just
            omits the middle Hero, leaving the topbar's flex layout
            balanced between mobile-menu and tools. */}
        <header className="h-12 bg-sidebar text-sidebar-foreground flex items-center px-3 lg:px-4 shrink-0 gap-3">
          <div className="flex items-center gap-3 shrink-0">
            <button
              onClick={() => setMobileSidebarOpen((o) => !o)}
              className="lg:hidden p-1.5 rounded hover:bg-muted text-muted-foreground"
              aria-label="Toggle navigation"
            >
              {mobileSidebarOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>

          {/* Route-aware: a feature hero (e.g. Maintenance counts on
              /maintenance) takes the slot; the persona hero is the
              cross-cutting fallback everywhere else. */}
          <ShellHero fallback={<FleetHero />} />

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setPaletteOpen(true)}
              className="hidden md:inline-flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground bg-muted/40 border border-border rounded-md hover:bg-muted hover:text-foreground transition w-[220px] lg:w-[280px]"
              aria-label="Open command palette"
            >
              <Search size={14} />
              <span>Search…</span>
              <kbd className="ml-auto px-1.5 py-0.5 text-3xs border border-border rounded bg-card">
                ⌘K
              </kbd>
            </button>
            <button
              onClick={() => setPaletteOpen(true)}
              className="md:hidden p-1.5 rounded hover:bg-muted text-muted-foreground"
              aria-label="Open search"
            >
              <Search size={18} />
            </button>
            <LanguageSelector />
            <ThemeToggle />
            <AvatarMenu />
          </div>
        </header>

        <main className="flex-1 bg-background rounded-xl overflow-hidden">
          <div className="h-full overflow-y-auto p-4 lg:p-6">
            <Outlet />
          </div>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <KeyboardShortcuts onOpenSearch={() => setPaletteOpen(true)} />
    </div>
  );
}

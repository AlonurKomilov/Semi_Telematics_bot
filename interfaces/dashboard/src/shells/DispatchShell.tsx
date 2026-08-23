/**
 * DispatchShell — wrapper for the Dispatch persona.
 *
 * Phase 2 of the role-shell migration: this file is currently an exact
 * clone of DefaultShell so behavior is unchanged.  Phase 3 will swap
 * in ``dispatchNav`` (Dispatch-emphasized sidebar: Routes/Live Map/
 * Geofences/Alerts up top) and Phase 4 will add a Dispatch hero
 * (active routes count, on-time / delayed chips, ETA summary) above
 * the main column.  Until those phases land the only difference
 * between this shell and DefaultShell is the file you're editing —
 * which is the point: future Dispatch-only changes go HERE without
 * touching the shared shell.
 */
import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
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
import DispatchHero from './heroes/DispatchHero';
import ShellHero from './heroes/ShellHero';

export default function DispatchShell() {
  const dockedContentClass = useDockedContentClass();
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

      <MobileNavDrawer
        open={mobileSidebarOpen}
        onOpenChange={setMobileSidebarOpen}
      />

      <div className="flex-1 flex flex-col overflow-hidden bg-sidebar pr-2 pb-2">
        {/* Three-zone header — see DefaultShell for the design note. */}
        <header className="h-12 border-b border-border bg-sidebar text-sidebar-foreground flex items-center px-3 lg:px-4 shrink-0 gap-3">
          <div className="flex items-center gap-3 shrink-0">
            <button
              onClick={() => setMobileSidebarOpen((o) => !o)}
              className="lg:hidden inline-flex size-8 items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
              aria-label="Toggle navigation"
            >
              {mobileSidebarOpen ? <X className="size-4.5" /> : <Menu className="size-4.5" />}
            </button>
          </div>

          {/* Route-aware: a feature hero (e.g. Maintenance counts on
              /maintenance) takes the slot; the persona hero is the
              cross-cutting fallback everywhere else. */}
          <ShellHero fallback={<DispatchHero />} />

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

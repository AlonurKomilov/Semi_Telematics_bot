/**
 * AccountingShell — wrapper for the Accounting persona.
 *
 * Same chrome as DefaultShell; the difference is the sidebar's nav
 * config, which uses ``accountingNav`` (cost analytics + billing
 * emphasised).  No persona hero strip — that's product work for
 * later (Accounting's natural hero chips would be cost-per-mile
 * trend + unpaid invoices + fuel cost MTD).  Until the hero is
 * designed, the shell renders DefaultShell's two-zone topbar layout.
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

export default function AccountingShell() {
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

      <div className="flex-1 flex flex-col overflow-hidden bg-sidebar pr-2 pb-2">
        <header className="h-12 border-b border-border bg-sidebar text-sidebar-foreground flex items-center justify-between px-3 lg:px-4 shrink-0 gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => setMobileSidebarOpen((o) => !o)}
              className="lg:hidden inline-flex size-8 items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
              aria-label="Toggle navigation"
            >
              {mobileSidebarOpen ? <X size={18} /> : <Menu size={18} />}
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
              <Search size={14} />
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
              <Search size={18} />
            </button>
            <LanguageSelector />
            <AlertsLauncher />
            <AssistantLauncher />
            <ThemeToggle />
            <AvatarMenu />
          </div>
        </header>

        <main className={`flex-1 bg-background rounded-xl overflow-hidden ${dockedContentClass}`}>
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

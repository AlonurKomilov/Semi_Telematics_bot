import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Search, Menu, X, Eye } from 'lucide-react';
import Sidebar from './Sidebar';
import { ThemeToggle } from './ThemeToggle';
import { LanguageSelector } from './LanguageSelector';
import { AvatarMenu } from './AvatarMenu';
import Breadcrumb from './shell/Breadcrumb';
import CommandPalette from './shell/CommandPalette';
import KeyboardShortcuts from './shell/KeyboardShortcuts';
import { useRoleView } from '../context/RoleViewContext';

export default function Layout() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  // Role-view preview banner — shown only when Owner/Admin has switched
  // to view as another role (e.g. "Previewing as Fleet").  Subtle bar
  // above the header so the operator can't forget they're not in their
  // own role.
  const { isPreviewing, viewLabel, switchView } = useRoleView();

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
      {mobileSidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 z-50 bg-black/50"
          onClick={() => setMobileSidebarOpen(false)}
        >
          <div
            className="h-full"
            onClick={(e) => e.stopPropagation()}
          >
            <Sidebar />
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-col overflow-hidden bg-background">
        {isPreviewing && (
          <div
            className="bg-primary/10 border-b border-primary/30 text-primary text-[12px] px-4 py-1.5 flex items-center justify-between gap-3 shrink-0"
            role="status"
            aria-live="polite"
          >
            <span className="flex items-center gap-2">
              <Eye size={13} />
              <span>
                Previewing dashboard as <span className="font-medium">{viewLabel}</span> —
                data and permissions are unchanged
              </span>
            </span>
            <button
              type="button"
              onClick={() => switchView('owner')}
              className="text-[11px] underline opacity-80 hover:opacity-100"
            >
              Exit preview
            </button>
          </div>
        )}
        <header className="h-14 bg-card border-b border-border flex items-center justify-between px-3 lg:px-4 shrink-0 gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => setMobileSidebarOpen((o) => !o)}
              className="lg:hidden p-1.5 rounded hover:bg-muted text-muted-foreground"
              aria-label="Toggle navigation"
            >
              {mobileSidebarOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            <Breadcrumb />
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setPaletteOpen(true)}
              className="hidden md:inline-flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground bg-muted/40 border border-border rounded-md hover:bg-muted hover:text-foreground transition"
              aria-label="Open command palette"
            >
              <Search size={13} />
              <span>Search…</span>
              <kbd className="ml-3 px-1.5 py-0.5 text-[10px] border border-border rounded bg-card">
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

        <main className="flex-1 overflow-y-auto p-4 lg:p-6 bg-background">
          <Outlet />
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <KeyboardShortcuts onOpenSearch={() => setPaletteOpen(true)} />
    </div>
  );
}

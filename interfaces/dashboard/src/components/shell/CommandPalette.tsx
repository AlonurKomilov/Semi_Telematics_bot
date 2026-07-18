import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, ArrowRight } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useRoleView } from '../../context/RoleViewContext';
import { ROUTE_ENTRIES, type RouteEntry } from './routeRegistry';
import { shortcut } from '../../utils/platform';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

function score(entry: RouteEntry, q: string): number {
  if (!q) return 0;
  const term = q.toLowerCase();
  const label = entry.label.toLowerCase();
  if (label === term) return 100;
  if (label.startsWith(term)) return 80;
  if (label.includes(term)) return 60;
  if (entry.group.toLowerCase().includes(term)) return 40;
  if (entry.description?.toLowerCase().includes(term)) return 30;
  if (entry.keywords?.some((k) => k.includes(term))) return 25;
  return 0;
}

export default function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { user } = useAuth();
  // Persona-aware: a command is offered only if the ACTIVE VIEW can run
  // it (viewHasAny), so an Owner/Admin previewing another persona doesn't
  // get ⌘K shortcuts that persona lacks.  Falls back to the real user's
  // permissions when not previewing.
  const { viewHasAny } = useRoleView();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);

  const visibleEntries = useMemo(() => {
    return ROUTE_ENTRIES.filter((e) => {
      if (e.path === '/driver-pay' && user?.payroll_enabled === false) return false;
      if (e.path === '/coaching' && user?.coaching_enabled === false) return false;
      if (!e.permission) return true;
      const flags = Array.isArray(e.permission) ? e.permission : [e.permission];
      return viewHasAny(...flags);
    });
  }, [viewHasAny, user]);

  const matches = useMemo(() => {
    if (!query.trim()) return visibleEntries.slice(0, 12);
    return visibleEntries
      .map((e) => ({ entry: e, s: score(e, query.trim()) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 12)
      .map((x) => x.entry);
  }, [visibleEntries, query]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    setActiveIdx(0);
  }, [query]);

  const go = useCallback(
    (entry: RouteEntry) => {
      onClose();
      navigate(entry.path);
    },
    [navigate, onClose]
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const m = matches[activeIdx];
      if (m) go(m);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center pt-[15vh] px-4 bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <Search size={16} className="text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Jump to a page or feature…"
            className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:text-muted-foreground"
          />
          <kbd className="text-3xs text-muted-foreground border border-border rounded px-1.5 py-0.5">
            Esc
          </kbd>
        </div>
        <div className="max-h-80 overflow-y-auto p-1">
          {matches.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              No results for "{query}"
            </div>
          ) : (
            matches.map((m, i) => {
              const Icon = m.icon;
              const active = i === activeIdx;
              return (
                <button
                  key={m.path}
                  onClick={() => go(m)}
                  onMouseEnter={() => setActiveIdx(i)}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-left transition ${
                    active ? 'bg-primary/10 text-foreground' : 'text-muted-foreground'
                  }`}
                >
                  <span
                    className={`inline-flex items-center justify-center w-7 h-7 rounded-md shrink-0 ${
                      active ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    <Icon size={14} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground truncate">
                      {m.label}
                    </p>
                    {m.description && (
                      <p className="text-xs text-muted-foreground truncate">
                        {m.description}
                      </p>
                    )}
                  </div>
                  <span className="text-3xs uppercase tracking-wide text-muted-foreground/70 shrink-0">
                    {m.group}
                  </span>
                  {active && (
                    <ArrowRight size={14} className="text-primary shrink-0" />
                  )}
                </button>
              );
            })
          )}
        </div>
        <div className="px-3 py-2 border-t border-border bg-muted/20 flex items-center justify-between text-2xs text-muted-foreground">
          <span>↑↓ to navigate · ↵ to open</span>
          <span>
            Press
            <kbd className="mx-1 px-1.5 py-0.5 rounded border border-border">{shortcut('K')}</kbd>
            anywhere
          </span>
        </div>
      </div>
    </div>
  );
}

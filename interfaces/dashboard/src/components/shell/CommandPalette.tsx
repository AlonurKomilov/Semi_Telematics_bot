import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, ArrowRight } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useRoleView } from '../../context/RoleViewContext';
import { ROUTE_ENTRIES, type RouteEntry } from './routeRegistry';
import { shortcut } from '../../utils/platform';
import { Dialog, DialogContent, DialogTitle } from '../ui/dialog';

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
  // The highlighted row, so arrowing past the fold can scroll it back
  // into view.  The list is ``max-h-80`` (320px) and a row is ~44px, so
  // from about the seventh result the highlight simply left the box and
  // a keyboard-only user was moving a selection they could not see.
  const activeRef = useRef<HTMLButtonElement>(null);
  // Which device last moved the selection.  Without this the fix above
  // creates a NEW bug: scrolling a row under a stationary cursor fires
  // ``mouseenter``, which would yank the selection back to whatever the
  // pointer happens to be resting on.  Only a real pointer MOVE hands
  // control back to the mouse.
  const keyboardNav = useRef(false);

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

  useEffect(() => {
    if (!keyboardNav.current) return;
    // ``nearest`` scrolls the minimum needed — ``center`` would jump the
    // list on every keystroke even when the row was already visible.
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      keyboardNav.current = true;
      setActiveIdx((i) => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      keyboardNav.current = true;
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const m = matches[activeIdx];
      if (m) go(m);
    }
  };

  if (!open) return null;

  return (
    // <Dialog> for the focus trap, aria-modal and background scroll lock
    // the hand-rolled overlay never had.  ``z-[60]`` stays on the content:
    // the palette is reachable from ON TOP of a dialog, and the z-ladder
    // reserves 60 for exactly that (design.md §7).
    //
    // ⚠️ Two focus owners, deliberately reconciled rather than fought:
    // the palette focuses its input on open, and the Dialog's own initial
    // focus lands on the first focusable descendant — which IS that
    // input, since it is the first control in the tree.  They agree, so
    // neither is removed.  Escape is likewise handled twice (the input's
    // onKeyDown and the Dialog) and both call the same onClose, so a
    // double-fire is a no-op.
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent
        size="xl" className="z-[60] w-full overflow-hidden p-0 gap-0 top-[15vh] translate-y-0"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <Search className="text-muted-foreground shrink-0 size-4" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Jump to a page or feature…"
            className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:text-muted-foreground"
          />
          <kbd className="text-2xs text-muted-foreground border border-border rounded px-1.5 py-0.5">
            Esc
          </kbd>
        </div>
        <div
          className="max-h-80 overflow-y-auto overscroll-contain p-1"
          onMouseMove={() => { keyboardNav.current = false; }}
        >
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
                  ref={active ? activeRef : undefined}
                  onClick={() => go(m)}
                  onMouseEnter={() => { if (!keyboardNav.current) setActiveIdx(i); }}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-left transition ${
                    active ? 'bg-primary/10 text-foreground' : 'text-muted-foreground'
                  }`}
                >
                  <span
                    className={`inline-flex items-center justify-center w-7 h-7 rounded-md shrink-0 ${
                      active ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    <Icon className="size-3.5" />
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
                  <span className="text-2xs uppercase tracking-wide text-muted-foreground/70 shrink-0">
                    {m.group}
                  </span>
                  {active && (
                    <ArrowRight className="text-primary shrink-0 size-3.5" />
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
      </DialogContent>
    </Dialog>
  );
}

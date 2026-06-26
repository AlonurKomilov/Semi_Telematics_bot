import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';

interface ShortcutDef {
  keys: string;
  label: string;
  href?: string;
  action?: () => void;
}

interface KeyboardShortcutsProps {
  onOpenSearch: () => void;
}

export default function KeyboardShortcuts({ onOpenSearch }: KeyboardShortcutsProps) {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);

  const shortcuts: ShortcutDef[] = [
    { keys: '⌘K', label: 'Open command palette', action: onOpenSearch },
    { keys: '/', label: 'Open command palette' },
    { keys: '?', label: 'Show this help' },
    { keys: 'g h', label: 'Go to Overview', href: '/' },
    { keys: 'g v', label: 'Go to Vehicles', href: '/vehicles' },
    { keys: 'g m', label: 'Go to Live map', href: '/live-map' },
    { keys: 'g a', label: 'Go to Alerts', href: '/alerts' },
    { keys: 'g s', label: 'Go to Scorecards', href: '/scorecards' },
    { keys: 'g r', label: 'Go to Reports', href: '/reports' },
    { keys: 'g t', label: 'Go to Maintenance', href: '/maintenance' },
    { keys: 'g k', label: 'Go to Knowledge base', href: '/knowledge' },
    { keys: 'Esc', label: 'Close panels & dialogs' },
  ];

  useEffect(() => {
    let pendingG = false;
    let pendingTimer: ReturnType<typeof setTimeout> | null = null;

    function isTypingTarget(t: EventTarget | null): boolean {
      if (!(t instanceof HTMLElement)) return false;
      const tag = t.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable;
    }

    function clearG() {
      pendingG = false;
      if (pendingTimer) {
        clearTimeout(pendingTimer);
        pendingTimer = null;
      }
    }

    function onKey(e: KeyboardEvent) {
      // ignore modifier-augmented keys (⌘K is handled by Layout)
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;

      // ?  → open help
      if (e.key === '?') {
        e.preventDefault();
        setHelpOpen((o) => !o);
        clearG();
        return;
      }

      if (e.key === 'Escape') {
        if (helpOpen) setHelpOpen(false);
        clearG();
        return;
      }

      if (pendingG) {
        const map: Record<string, string> = {
          h: '/',
          v: '/vehicles',
          m: '/live-map',
          a: '/alerts',
          s: '/scorecards',
          r: '/reports',
          t: '/maintenance',
          k: '/knowledge',
        };
        const dest = map[e.key.toLowerCase()];
        if (dest) {
          e.preventDefault();
          navigate(dest);
        }
        clearG();
        return;
      }

      if (e.key.toLowerCase() === 'g') {
        pendingG = true;
        pendingTimer = setTimeout(clearG, 1200);
      }
    }

    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      clearG();
    };
  }, [navigate, helpOpen]);

  if (!helpOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={() => setHelpOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        className="w-full max-w-md bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <p className="text-sm font-semibold text-foreground">Keyboard shortcuts</p>
          <button
            onClick={() => setHelpOpen(false)}
            aria-label="Close"
            className="text-muted-foreground hover:text-foreground p-1"
          >
            <X size={16} />
          </button>
        </div>
        <ul className="p-3 space-y-1.5">
          {shortcuts.map((s) => (
            <li
              key={s.keys}
              className="flex items-center justify-between gap-3 px-2.5 py-1.5 rounded-md hover:bg-muted/50 cursor-default"
            >
              <span className="text-sm text-foreground">{s.label}</span>
              <span className="flex items-center gap-1">
                {s.keys.split(' ').map((k, i) => (
                  <kbd
                    key={i}
                    className="text-2xs px-1.5 py-0.5 border border-border rounded bg-muted/50 font-mono"
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

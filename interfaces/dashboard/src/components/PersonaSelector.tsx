import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, Eye } from 'lucide-react';
import { useRoleView } from '../context/RoleViewContext';

/**
 * Top-bar persona view selector.
 *
 * Sits on the LEFT side of the header (next to the breadcrumb) — kept
 * deliberately separate from the right-side cluster of personal
 * settings (language, theme, avatar) because picking a view changes
 * what the dashboard RENDERS, not who the user is or what they own.
 *
 * No role icons here: the icons read as "this is the user's role" /
 * "this is data about a person", which is the wrong mental model — the
 * selector is a UI scope filter, not a profile field.  Just clean text.
 *
 * Visual treatment:
 *   * Default: muted background, "View:" prefix label + role name.
 *   * Previewing (Owner/Admin viewing as Fleet/Safety/etc): primary
 *     ring + an Eye icon so the operator knows the dashboard is in
 *     preview mode rather than their own role.
 *   * Non-switchable roles (Fleet / Safety / Dispatcher / Driver) get a
 *     non-interactive pill — they can see their role but can't change
 *     the view, because there's nothing else they would see.
 *
 * Picking a new view:
 *   1. Persists the choice via RoleViewContext (localStorage).
 *   2. Navigates to the persona's home route so the preview lands where
 *      that role's day starts (Fleet → /fleet/map, Safety →
 *      /safety/scorecards, etc).
 */
export function PersonaSelector() {
  const {
    activeView, viewLabel, canSwitch, availableViews, switchView,
    homeRoute, isPreviewing,
  } = useRoleView();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Click-outside / Esc to close.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Non-switchable user (Fleet / Safety / Dispatcher / Driver): static
  // pill with no interactivity.  Shows the user what role they're in
  // without implying they can change it.
  if (!canSwitch) {
    return (
      <div
        className="inline-flex items-center px-2 py-0.5 text-2xs text-muted-foreground/80 bg-muted/30 border border-border/60 rounded-md"
        title={`Your role: ${viewLabel}`}
      >
        {viewLabel}
      </div>
    );
  }

  const handlePick = (role: string) => {
    if (role !== activeView) {
      switchView(role);
      // Navigate to the new persona's home route so the preview lands
      // somewhere the chosen role would normally start.  We do this
      // even when picking the operator's own role back from a preview —
      // returning to Overview is a natural "exit preview" signal.
      navigate(homeRoute === '/' ? '/' : homeRoute);
    }
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1 px-2 py-0.5 text-2xs rounded-md border transition ${
          isPreviewing
            ? 'bg-primary/10 text-primary border-primary/40 hover:bg-primary/15'
            : 'bg-muted/30 text-muted-foreground/90 border-border/60 hover:bg-muted hover:text-foreground'
        }`}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={
          isPreviewing
            ? `Previewing dashboard as ${viewLabel} — click to switch back. Changes what you see, not what's stored.`
            : `Dashboard view: ${viewLabel}. Picking a different role re-skins the UI; data and permissions remain yours.`
        }
      >
        {isPreviewing && <Eye size={12} className="opacity-80" />}
        <span>{viewLabel}</span>
        <ChevronDown size={12} className="opacity-60" />
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute left-0 mt-1 w-60 bg-card border border-border rounded-lg shadow-xl text-sm z-50 overflow-hidden"
        >
          <li className="px-3 py-1.5 text-3xs uppercase tracking-wider text-muted-foreground/60 border-b border-border">
            View dashboard as…
          </li>
          {availableViews.map(v => {
            const isActive = v.key === activeView;
            return (
              <li key={v.key}>
                <button
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  onClick={() => handlePick(v.key)}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors ${
                    isActive
                      ? 'bg-primary/10 text-primary'
                      : 'hover:bg-muted text-foreground'
                  }`}
                >
                  <span className="flex-1">{v.label}</span>
                  {isActive && <Check size={14} className="opacity-80" />}
                </button>
              </li>
            );
          })}
          <li className="px-3 py-1.5 text-3xs text-muted-foreground/60 border-t border-border bg-muted/20 leading-snug">
            Changes the dashboard UI only — data and permissions stay yours.
          </li>
        </ul>
      )}
    </div>
  );
}

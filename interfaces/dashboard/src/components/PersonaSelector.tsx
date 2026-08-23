import { Tip } from './tooltip';
import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, ChevronRight, Eye } from 'lucide-react';
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
export function PersonaSelector({ compact = false }: { compact?: boolean }) {
  const {
    activeView, viewLabel, canSwitch, availableViews, switchView,
    homeRoute, isPreviewing,
    activeViewSupportsManager, activeViewTier, previewAsManager, setPreviewAsManager,
  } = useRoleView();
  const tierSuffix = activeViewSupportsManager && activeViewTier
    ? ` · ${previewAsManager ? activeViewTier.senior : activeViewTier.base}` : '';
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
  // without implying they can change it.  In compact (collapsed-rail)
  // mode the full label doesn't fit, but dropping it entirely made the
  // collapsed rail's top row go from "role pill + brand" to nothing —
  // the two sidebar states stopped agreeing on what they show.  A
  // single-letter identity badge (same idiom as AvatarMenu's initials
  // circle) keeps the persona visible at a glance in both states.
  if (!canSwitch) {
    if (compact) {
      const initial = viewLabel.trim().charAt(0).toUpperCase() || '?';
      return (
        <Tip label={`Your role: ${viewLabel}`}>
          <div
            className="flex size-7 items-center justify-center rounded-full border border-border/60 bg-muted/30 text-2xs font-semibold text-muted-foreground/80 select-none"
            aria-label={`Your role: ${viewLabel}`}
          >
            {initial}
          </div>
        </Tip>
      );
    }
    return (
      <Tip label={`Your role: ${viewLabel}`}>
        <div className="inline-flex h-7 items-center px-2 text-2xs text-muted-foreground/80 bg-muted/30 border border-border/60 rounded-md">
          {viewLabel}
        </div>
      </Tip>
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

  // Pick a manager-capable role AT a specific tier: remember the tier (it
  // persists), then enter that role — navigating only when actually switching
  // roles (a same-role tier flip just re-skins the current pages in place).
  const pickTier = (role: string, wantManager: boolean) => {
    setPreviewAsManager(wantManager);
    if (role !== activeView) handlePick(role);
    else setOpen(false);
  };

  const triggerTitle = isPreviewing
    ? `Previewing dashboard as ${viewLabel} — click to switch back. Changes what you see, not what's stored.`
    : `Dashboard view: ${viewLabel}. Picking a different role re-skins the UI; data and permissions remain yours.`;

  return (
    <div ref={ref} className="relative">
      {compact ? (
        // Collapsed-rail trigger.  Same dropdown; the panel overhangs the
        // rail into the content area (no overflow clip on the sidebar).
        // Circular geometry matches the non-switchable badge above (and
        // AvatarMenu's identity dot) so collapsed/expanded read as the
        // same control at two densities, not two different UIs.  Idle,
        // this is an Eye ("click to preview a role") — but mid-preview it
        // swaps to the previewed role's initial, since an unchanging Eye
        // glyph can't tell you WHICH role you're currently seeing, the
        // one thing the expanded label always shows.
        <Tip label={triggerTitle}>
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            className={`flex size-7 items-center justify-center rounded-full border text-2xs font-semibold transition ${
              isPreviewing
                ? 'bg-primary/10 text-primary border-primary/40 hover:bg-primary/15'
                : 'text-muted-foreground border-transparent hover:bg-muted/50 hover:text-foreground'
            } min-h-tap min-w-tap`}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-label="View dashboard as…"
          >
            {isPreviewing ? (viewLabel.trim().charAt(0).toUpperCase() || '?') : <Eye className="size-4" />}
          </button>
        </Tip>
      ) : (
        <Tip label={triggerTitle}>
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className={`inline-flex h-7 items-center gap-1 px-2 text-2xs rounded-md border transition ${
            isPreviewing
              ? 'bg-primary/10 text-primary border-primary/40 hover:bg-primary/15'
              : 'bg-muted/30 text-muted-foreground/90 border-border/60 hover:bg-muted hover:text-foreground'
          } min-h-tap`}
          aria-haspopup="listbox"
          aria-expanded={open}
        >
          {isPreviewing && <Eye className="opacity-80 size-3" />}
          <span>{viewLabel}{tierSuffix}</span>
          <ChevronDown className="opacity-60 size-3" />
        </button>
        </Tip>
      )}

      {open && (
        <ul
          role="listbox"
          className="absolute left-0 mt-1 w-64 bg-card border border-border rounded-lg shadow-xl text-sm z-50"
        >
          <li className="px-3 py-1.5 text-3xs uppercase tracking-wider text-muted-foreground/60 border-b border-border">
            View dashboard as…
          </li>
          {availableViews.map(v => {
            const isActive = v.key === activeView;
            // Manager-capable role (e.g. Recruiter): the role row enters at the
            // SAVED tier; two nested rows let the operator pick Manager /
            // Employee explicitly (and that choice persists).
            if (v.supportsManager) {
              // Manager-capable role: a "Recruiter ›" submenu row that opens a
              // Manager / Employee flyout on hover (mirrors the column menu's
              // "Sort › Ascending/Descending").  Clicking the role itself enters
              // at the SAVED tier; the flyout picks + persists a tier.
              return (
                <li key={v.key} className="group/sub relative">
                  <button
                    type="button" role="option" aria-selected={isActive}
                    aria-haspopup="menu"
                    onClick={() => handlePick(v.key)}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors ${
                      isActive ? 'bg-primary/10 text-primary' : 'hover:bg-muted text-foreground'
                    }`}
                  >
                    <span className="flex-1">{v.label}</span>
                    {isActive && <Check className="opacity-80 size-3.5" />}
                    <ChevronRight className="opacity-50 size-3.5" />
                  </button>
                  {/* Flyout — flush to the right so hover bridges without a gap. */}
                  <ul
                    role="menu"
                    className="invisible group-hover/sub:visible absolute left-full top-0 z-50 w-44 overflow-hidden rounded-lg border border-border bg-card shadow-xl"
                  >
                    {([[v.tier?.senior ?? 'Manager', true], [v.tier?.base ?? 'Employee', false]] as const).map(([label, wantManager]) => {
                      const tierActive = isActive && previewAsManager === wantManager;
                      return (
                        <li key={label}>
                          <button
                            type="button" role="menuitemradio" aria-checked={tierActive}
                            onClick={() => pickTier(v.key, wantManager)}
                            className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                              tierActive ? 'bg-primary/10 text-primary' : 'hover:bg-muted text-foreground'
                            }`}
                          >
                            <span className="flex-1">{label}</span>
                            {tierActive && <Check className="opacity-80 size-3.5" />}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              );
            }
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
                  {isActive && <Check className="opacity-80 size-3.5" />}
                </button>
              </li>
            );
          })}
          <li className="rounded-b-lg px-3 py-1.5 text-3xs text-muted-foreground/60 border-t border-border bg-muted/20 leading-snug">
            Changes the dashboard UI only — data and permissions stay yours.
          </li>
        </ul>
      )}
    </div>
  );
}

import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, PanelLeftClose, PanelLeftOpen, Settings as SettingsIcon } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useRoleView } from '../context/RoleViewContext';
import { PersonaSelector } from './PersonaSelector';
import { generateNav, type NavItem } from '../shells/nav/generateNav';

// Sidebar generates its own nav from the feature catalog, keyed off the
// active persona (RoleViewContext.activeView).  No per-shell nav config:
// every shell just renders <Sidebar /> and the catalog decides what each
// persona sees (catalog ∩ enabled modules ∩ permissions).

// Persist the collapsed preference so the user's choice survives across
// page loads.  Stored as ``'1'`` / ``'0'`` so a simple string check
// works without JSON parsing; reading is cheap enough to do in the
// initializer of useState (called once per mount).
const COLLAPSE_KEY = 'sidebar.collapsed';
// The Settings group is ONE collapsible parent entry (the feature's
// components nest under it).  Closed by default; the preference persists.
const SETTINGS_OPEN_KEY = 'sidebar.settingsOpen';

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

export default function Sidebar() {
  const { user } = useAuth();
  const { t } = useTranslation();
  // Local state seeded from localStorage; persisted on every toggle.
  // Same-mount reads see the latest value because we update both
  // simultaneously below.
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(() => {
    try { return localStorage.getItem(SETTINGS_OPEN_KEY) === '1'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(SETTINGS_OPEN_KEY, settingsOpen ? '1' : '0'); } catch { /* session-only */ }
  }, [settingsOpen]);
  const location = useLocation();
  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    } catch {
      /* localStorage disabled — toggle still works for the session */
    }
  }, [collapsed]);
  // ``viewHasAny`` is the active-persona-aware permission check from
  // RoleViewContext.  For non-switchable roles (Fleet/Safety/Dispatcher/
  // Driver) it falls back to the user's own permissions.  For Owner/
  // Admin previewing as another role, it uses that role's perm set so
  // the sidebar reflects what the previewed persona would see — no
  // bouncing back to "but they're Owner so show everything".
  const { viewHasAny, activeView } = useRoleView();

  // The generated nav already applies module + permission filtering; the
  // only thing left is the two account kill-switches (payroll / coaching)
  // which are separate from the department modules.
  const navConfig = generateNav(activeView, viewHasAny, user?.enabled_modules);

  const filterItems = (items: NavItem[]) =>
    items.filter((item) => {
      if (item.path === '/payroll') return user?.payroll_enabled !== false;
      if (item.path === '/coaching') return user?.coaching_enabled !== false;
      return true;
    });

  return (
    <aside
      // No outer rounding, no right border — the sidebar merges
      // seamlessly with the topbar into a single L-shaped chrome
      // panel.  The rounded "indent" where chrome meets content is
      // applied to the content area's top-left corner in each shell
      // (see ``rounded-tl-xl`` on <main>), revealing the chrome
      // colour behind it.  That single inset curve is what gives
      // the Samsara-style continuous-chrome look.
      className={`${collapsed ? 'w-14' : 'w-56'} bg-sidebar text-sidebar-foreground flex flex-col shrink-0 h-screen transition-[width] duration-150 ease-out`}
    >
      {/* Logo row + collapse toggle.  When expanded the row carries
          the brand mark, brand text, persona selector, and a collapse
          button.  When collapsed it shrinks to just the brand glyph
          and the expand button so the chrome stays usable at minimum
          width.  PersonaSelector is hidden in collapsed mode — it
          isn't valuable without its label, and the avatar menu in the
          top bar still surfaces persona controls via the dropdown. */}
      <div className={`h-12 flex items-center ${collapsed ? 'px-2 justify-center' : 'px-3 gap-2'} shrink-0`}>
        {!collapsed && <span className="text-lg font-bold text-foreground">4truck</span>}
        {!collapsed && (
          <div className="ml-auto">
            <PersonaSelector />
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="p-1.5 rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground transition"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>

      {/* Nav — scrolls independently.  Group title labels (FLEET /
          SAFETY / REPORTS / WORKFORCE / ADMIN) are deliberately
          omitted: the active persona is already conveyed by the
          subdomain (fleet.4truck.us etc.) and the persona pill in
          the brand row, so repeating "FLEET" as a heading inside a
          Fleet user's sidebar is just visual noise.  A thin
          separator between groups keeps the implicit structure
          readable in both collapsed and expanded modes. */}
      <nav className="flex-1 overflow-y-auto py-2 scrollbar-thin">
        {navConfig.map((group, gi) => {
          const items = filterItems(group.items);
          if (items.length === 0) return null;
          // The Settings feature renders as one parent row that expands to
          // its permitted components.  While a child route is active the
          // group stays open so the active pill is never hidden.  In the
          // collapsed icon rail there's no room for nesting — fall through
          // to the flat divider+icons rendering below.
          if (group.collapsible && !collapsed) {
            const childActive = items.some((i) => location.pathname.startsWith(i.path));
            const open = settingsOpen || childActive;
            return (
              <div key={group.titleKey ?? `_set-${gi}`}>
                <button
                  type="button"
                  onClick={() => setSettingsOpen(!open)}
                  disabled={childActive && open}
                  aria-expanded={open}
                  className={`w-full flex items-center gap-3 px-3 mx-2 my-0.5 rounded-md py-2 text-sm transition-colors ${
                    childActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                  style={{ width: 'calc(100% - 1rem)' }}
                >
                  <SettingsIcon size={16} className="shrink-0" />
                  <span className="flex-1 text-left">{group.titleKey ? t(group.titleKey) : ''}</span>
                  {open ? <ChevronDown size={14} className="shrink-0" /> : <ChevronRight size={14} className="shrink-0" />}
                </button>
                {open && items.map((item) => {
                  const Icon = item.icon;
                  const label = t(item.labelKey);
                  return (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      title={undefined}
                      className={({ isActive }) =>
                        `flex items-center gap-3 pl-9 pr-3 mx-2 my-0.5 rounded-md py-1.5 text-sm transition-colors ${
                          isActive
                            ? 'bg-primary/15 text-primary'
                            : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                        }`
                      }
                    >
                      <Icon size={14} className="shrink-0" />
                      {label}
                    </NavLink>
                  );
                })}
              </div>
            );
          }
          return (
            <div key={group.titleKey ?? `_top-${gi}`}>
              {/* Group header.  Expanded: a quiet small-caps label so the
                  sidebar's structure (Fleet / Workforce / Admin …) is
                  legible instead of an unlabelled run of links.  Collapsed:
                  fall back to a thin divider since text doesn't fit the
                  icon rail. */}
              {group.titleKey && (collapsed
                ? gi > 0 && <div className="my-2 mx-3 border-t border-sidebar-border" />
                : (
                  <div className={`px-3 ${gi > 0 ? 'mt-4' : 'mt-1'} mb-1 text-2xs font-semibold uppercase tracking-wider text-muted-foreground/70 select-none`}>
                    {t(group.titleKey)}
                  </div>
                )
              )}
              {items.map((item) => {
                const Icon = item.icon;
                const label = t(item.labelKey);
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === '/'}
                    title={collapsed ? label : undefined}
                    // Single rounded-pill geometry for both collapsed and
                    // expanded modes — the background hugs the item and
                    // its radius tracks the active Corners preset (Sharp /
                    // Default / Rounded / Pill), so the chrome looks
                    // consistent with cards and buttons elsewhere on the
                    // page.  The old right-edge accent bar was a fixed
                    // 2px-solid line that ignored the theme; the rounded
                    // background makes the active state itself the visual
                    // cue, no edge accent needed.
                    className={({ isActive }) =>
                      `flex items-center ${collapsed ? 'justify-center' : 'gap-3'} px-3 mx-2 my-0.5 rounded-md py-2 text-sm transition-colors ${
                        isActive
                          ? 'bg-primary/15 text-primary'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                      }`
                    }
                  >
                    <Icon size={16} className="shrink-0" />
                    {!collapsed && label}
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}

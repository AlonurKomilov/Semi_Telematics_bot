import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Tip } from './tooltip';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, PanelLeftClose, PanelLeftOpen, Settings as SettingsIcon } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { usePreference } from '../preferences';
import { useRoleView } from '../context/RoleViewContext';
import { PersonaSelector } from './PersonaSelector';
import { generateNav, type NavItem } from '../shells/nav/generateNav';

// Sidebar generates its own nav from the feature catalog, keyed off the
// active persona (RoleViewContext.activeView).  No per-shell nav config:
// every shell just renders <Sidebar /> and the catalog decides what each
// persona sees (catalog ∩ enabled modules ∩ permissions).

export default function Sidebar({ forceExpanded = false }: {
  /** Ignore the collapsed preference and always render labels.
   *
   *  For the MOBILE DRAWER: the rail is a desktop space-saver, and
   *  ``sidebar.collapsed`` is device-scoped — but a phone-width window on
   *  a desktop machine IS the same device, so a collapsed desktop rail
   *  followed the user into the drawer.  An icon-only nav inside an
   *  overlay that is already covering the screen saves nothing and costs
   *  every label. */
  forceExpanded?: boolean;
} = {}) {
  const { user } = useAuth();
  const { t } = useTranslation();
  // Collapsed state is a per-user preference (device-scoped: it depends on
  // THIS screen's width).  Persistence + the legacy '1'/'0' migration live
  // in the preferences registry.
  const { value: storedCollapsed, setValue: setCollapsed } = usePreference('sidebar.collapsed');
  const collapsed = forceExpanded ? false : storedCollapsed;
  // The Settings group follows the route: it expands while you're on
  // the Settings page or any of its components, and folds by itself the
  // moment you navigate to another feature — no chevron press needed.
  // (The chevron remains for peeking at the list without navigating.)
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const location = useLocation();
  useEffect(() => {
    // Expose the live width so the assistant's expanded overlay can start
    // at the content edge (right of the sidebar) instead of covering the
    // rail.  Mirrors the --assistant-w pattern; matches w-14 / w-56.
    document.documentElement.style.setProperty(
      '--sidebar-w', collapsed ? '3.5rem' : '14rem');
  }, [collapsed]);
  // ``viewHasAny`` is the active-persona-aware permission check from
  // RoleViewContext.  For non-switchable roles (Fleet/Safety/Dispatcher/
  // Driver) it falls back to the user's own permissions.  For Owner/
  // Admin previewing as another role, it uses that role's perm set so
  // the sidebar reflects what the previewed persona would see — no
  // bouncing back to "but they're Owner so show everything".
  const { viewHasAny, activeView } = useRoleView();

  // The generated nav already applies module + permission filtering; the
  // only thing left is the two account kill-switches (driver-pay / coaching)
  // which are separate from the department modules.
  const navConfig = generateNav(activeView, viewHasAny, user?.enabled_modules);

  const settingsGroup = navConfig.find((g) => g.collapsible);
  const inSettingsArea = !!settingsGroup && (
    settingsGroup.items.some((i) => location.pathname.startsWith(i.path)) ||
    (!!settingsGroup.parentItem && location.pathname.startsWith(settingsGroup.parentItem.path))
  );
  useEffect(() => {
    if (!inSettingsArea) setSettingsOpen(false);
  }, [location.pathname, inSettingsArea]);

  // Item-level children (Settings-style nesting for regular entries,
  // e.g. Vehicles ▸ Inventory).  Manual toggles live here; an item
  // auto-opens while the route is inside its own or a child's area so
  // the active pill is never hidden.
  const [openItems, setOpenItems] = useState<Record<string, boolean>>({});

  const filterItems = (items: NavItem[]) =>
    items.filter((item) => {
      if (item.path === '/driver-pay') return user?.payroll_enabled !== false;
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
      //
      // NO width transition, deliberately: animating width re-lays-out
      // the ENTIRE content area every frame for the whole duration —
      // measured at ~39 dropped frames (~400ms of stutter) per toggle
      // with a large board open.  An instant snap costs one relayout.
      className={`${collapsed ? 'w-14' : 'w-56'} bg-sidebar text-sidebar-foreground flex flex-col shrink-0 h-screen`}
    >
      {/* Logo row + collapse toggle.  Expanded: one h-12 row carries the
          brand text, persona selector, and collapse button — plenty of
          width.  Collapsed: the rail is only 56px wide, and a size-7
          (28px) persona badge sitting beside a 28px toggle button in a
          single row doesn't fit (56px content − padding − gap leaves
          ~48px for the two, ~10px short) — the pair would silently
          overflow the rail's right edge into the content area.  Stacking
          them in a column sidesteps the arithmetic entirely: each
          control only has to fit the rail's width on its own, which it
          does with room to spare. The persona indicator persists in
          localStorage even when collapsed, so it's never a case of
          hiding it — always render it, just at whatever density fits. */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-1 px-1 py-2 shrink-0">
          <PersonaSelector compact />
          <Tip label="Expand sidebar">
            <button
              type="button"
              onClick={() => setCollapsed((c) => !c)}
              className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground transition"
              aria-label="Expand sidebar"
            >
              <PanelLeftOpen className="size-4" />
            </button>
          </Tip>
        </div>
      ) : (
        <div className="h-12 flex items-center px-3 gap-2 shrink-0">
          <span className="text-lg font-bold text-foreground">4truck</span>
          <div className="ml-auto">
            <PersonaSelector />
          </div>
          {/* Hidden in the mobile drawer.  ``forceExpanded`` pins
              ``collapsed`` to false, so pressing this did NOTHING visible
              — while still flipping the stored preference, so a tap on a
              phone silently collapsed the operator's DESKTOP sidebar next
              time they opened a laptop.  A dead control with an invisible
              side effect.  The drawer is dismissed with ☰ / backdrop /
              Escape, not by collapsing. */}
          {!forceExpanded && (
            <Tip label="Collapse sidebar">
              <button
                type="button"
                onClick={() => setCollapsed((c) => !c)}
                className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground transition"
                aria-label="Collapse sidebar"
              >
                <PanelLeftClose className="size-4" />
              </button>
            </Tip>
          )}
        </div>
      )}

      {/* Nav — scrolls independently.  Group title labels (FLEET /
          SAFETY / REPORTS / WORKFORCE / ADMIN) are deliberately
          omitted: the active persona is already conveyed by the
          subdomain (fleet.4truck.us etc.) and the persona pill in
          the brand row, so repeating "FLEET" as a heading inside a
          Fleet user's sidebar is just visual noise.  A thin
          separator between groups keeps the implicit structure
          readable in both collapsed and expanded modes. */}
      <nav className="flex-1 overflow-y-auto overscroll-contain py-2">
        {navConfig.map((group, gi) => {
          // Collapsed icon rail has no nesting — flatten children so
          // every destination stays one click away.
          const baseItems = collapsed
            // Include the collapsible parent in the icon rail — it's a
            // real destination (/settings), not just a folder.
            ? [...(group.parentItem ? [group.parentItem] : []),
               ...group.items.flatMap((i) => [i, ...(i.children ?? [])])]
            : group.items;
          const items = filterItems(baseItems);
          // A collapsible group whose PARENT is visible must render even
          // with zero children — a role manager holds only the parent
          // (/settings); dropping the group hid Settings from them.
          const hasVisibleParent = !collapsed && !!group.parentItem;
          if (items.length === 0 && !hasVisibleParent) return null;
          // The Settings feature renders as one parent row that expands to
          // its permitted components.  While a child route is active the
          // group stays open so the active pill is never hidden.  In the
          // collapsed icon rail there's no room for nesting — fall through
          // to the flat divider+icons rendering below.
          if (group.collapsible && !collapsed) {
            const groupActive =
              items.some((i) => location.pathname.startsWith(i.path)) ||
              (!!group.parentItem && location.pathname.startsWith(group.parentItem.path));
            const open = settingsOpen || groupActive;
            const label = group.titleKey ? t(group.titleKey) : '';
            const chevron = (
              <button
                type="button"
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); setSettingsOpen(!open); }}
                disabled={groupActive && open}
                aria-expanded={open}
                aria-label={open ? `Collapse ${label}` : `Expand ${label}`}
                className="inline-flex size-6 min-h-tap min-w-tap items-center justify-center rounded-md text-muted-foreground hover:text-foreground shrink-0 disabled:opacity-60"
              >
                {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              </button>
            );
            return (
              <div key={group.titleKey ?? `_set-${gi}`}>
                {group.parentItem ? (
                  // Pressing "Settings" opens the Settings page itself —
                  // the page IS the feature's general-config component
                  // ("Account Settings" was the same thing under a second
                  // name).  The chevron alone expands/collapses.
                  <NavLink
                    to={group.parentItem.path}
                    className={({ isActive }) =>
                      `flex items-center gap-3 pl-3 pr-1 mx-2 my-0.5 rounded-md py-1.5 text-sm transition-colors ${
                        isActive
                          ? 'bg-primary/15 text-primary'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                      }`
                    }
                  >
                    <SettingsIcon className="shrink-0 size-4" />
                    <span className="flex-1">{label}</span>
                    {/* No children (a role manager holds only the parent) →
                        no expand arrow over nothing. */}
                    {items.length > 0 && chevron}
                  </NavLink>
                ) : (
                  <div className="flex items-center gap-3 pl-3 pr-1 mx-2 my-0.5 rounded-md py-1.5 text-sm text-muted-foreground">
                    <SettingsIcon className="shrink-0 size-4" />
                    <span className="flex-1">{label}</span>
                    {chevron}
                  </div>
                )}
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
                      <Icon className="shrink-0 size-3.5" />
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
                const kids = collapsed ? [] : filterItems(item.children ?? []);
                if (kids.length) {
                  // Parent with indented children — same look and rules as
                  // the Settings expander: the row navigates, the chevron
                  // toggles, the area auto-opens while a child is active.
                  const inArea = location.pathname === item.path
                    || location.pathname.startsWith(item.path + '/');
                  const childActive = kids.some((k) =>
                    location.pathname === k.path || location.pathname.startsWith(k.path + '/'));
                  const open = openItems[item.path] ?? (inArea || childActive);
                  const parentActive = inArea && !childActive;
                  return (
                    <div key={item.path}>
                      <NavLink
                        to={item.path}
                        className={`flex items-center gap-3 pl-3 pr-1 mx-2 my-0.5 rounded-md py-1.5 text-sm transition-colors ${
                          parentActive
                            ? 'bg-primary/15 text-primary'
                            : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                        }`}
                      >
                        <Icon className="shrink-0 size-4" />
                        <span className="flex-1">{label}</span>
                        <button
                          type="button"
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpenItems((m) => ({ ...m, [item.path]: !open })); }}
                          disabled={childActive && open}
                          aria-expanded={open}
                          aria-label={open ? `Collapse ${label}` : `Expand ${label}`}
                          className="inline-flex size-6 min-h-tap min-w-tap items-center justify-center rounded-md text-muted-foreground hover:text-foreground shrink-0 disabled:opacity-60"
                        >
                          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                        </button>
                      </NavLink>
                      {open && kids.map((k) => {
                        const KIcon = k.icon;
                        return (
                          <NavLink
                            key={k.path}
                            to={k.path}
                            className={({ isActive }) =>
                              `flex items-center gap-3 pl-9 pr-3 mx-2 my-0.5 rounded-md py-1.5 text-sm transition-colors ${
                                isActive
                                  ? 'bg-primary/15 text-primary'
                                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                              }`
                            }
                          >
                            <KIcon className="shrink-0 size-3.5" />
                            {t(k.labelKey)}
                          </NavLink>
                        );
                      })}
                    </div>
                  );
                }
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
                    <Icon className="shrink-0 size-4" />
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
